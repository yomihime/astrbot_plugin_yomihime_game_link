"""Deterministic, transactional SQLite migration discovery and execution."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Sequence


class MigrationError(RuntimeError):
    """A migration cannot be safely discovered, validated, or applied."""


class MigrationDiscoveryError(MigrationError):
    """A migration directory contains an invalid or ambiguous entry."""


class MigrationChecksumError(MigrationError):
    """An already-applied migration was modified."""


_MIGRATION_NAME = re.compile(
    r"^(?P<version>\d{4,})_(?P<name>[A-Za-z0-9][A-Za-z0-9_-]*)\.sql$"
)


@dataclass(frozen=True, slots=True)
class Migration:
    """A migration script with a stable numeric version and content digest."""

    version: int
    name: str
    path: Path
    sql: str
    checksum: str

    @classmethod
    def from_path(cls, path: Path, *, root: Path | None = None) -> "Migration":
        path = Path(path)
        if root is not None:
            root = Path(root).resolve()
            resolved = path.resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                raise MigrationDiscoveryError(
                    "migration path escapes directory"
                ) from None
            path = resolved
        if path.suffix.lower() != ".sql":
            raise MigrationDiscoveryError("migration files must use .sql suffix")
        match = _MIGRATION_NAME.fullmatch(path.name)
        if match is None:
            raise MigrationDiscoveryError(
                "migration filename must start with a numeric version"
            )
        version = int(match.group("version"))
        if version < 0:
            raise MigrationDiscoveryError("migration version must be non-negative")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise MigrationDiscoveryError("migration file cannot be read") from exc
        try:
            sql = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MigrationDiscoveryError("migration file must be UTF-8") from exc
        return cls(
            version=version,
            name=match.group("name"),
            path=path,
            sql=sql,
            checksum=hashlib.sha256(raw).hexdigest(),
        )


def discover_migrations(directory: str | Path) -> tuple[Migration, ...]:
    """Discover ``NNNN_name.sql`` files in one directory in numeric order."""

    root = Path(directory).resolve()
    if not root.is_dir():
        raise MigrationDiscoveryError("migration directory does not exist")
    migrations: list[Migration] = []
    seen_versions: set[int] = set()
    seen_names: set[str] = set()
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if not path.is_file() or path.suffix.lower() != ".sql":
            continue
        migration = Migration.from_path(path, root=root)
        if migration.version in seen_versions:
            raise MigrationDiscoveryError("duplicate migration version")
        if migration.name in seen_names:
            raise MigrationDiscoveryError("duplicate migration name")
        seen_versions.add(migration.version)
        seen_names.add(migration.name)
        migrations.append(migration)
    if not migrations:
        raise MigrationDiscoveryError("no migration files found")
    return tuple(sorted(migrations, key=lambda item: (item.version, item.name)))


class MigrationRegistry:
    """Explicit registration helper used by hosts that assemble migrations."""

    def __init__(self) -> None:
        self._migrations: dict[int, Migration] = {}

    def register(self, migration: Migration) -> None:
        if not isinstance(migration, Migration):
            raise TypeError("migration must be a Migration")
        if migration.version in self._migrations:
            raise MigrationDiscoveryError("duplicate migration version")
        if any(item.name == migration.name for item in self._migrations.values()):
            raise MigrationDiscoveryError("duplicate migration name")
        self._migrations[migration.version] = migration

    def migrations(self) -> tuple[Migration, ...]:
        return tuple(self._migrations[version] for version in sorted(self._migrations))


def _statements(sql: str) -> Iterable[str]:
    """Yield complete statements without using executescript's implicit commit."""

    buffer: list[str] = []
    for character in sql:
        buffer.append(character)
        candidate = "".join(buffer)
        if sqlite3.complete_statement(candidate):
            if candidate.strip():
                yield candidate
            buffer.clear()
    remainder = "".join(buffer).strip()
    if remainder:
        raise MigrationError("migration contains incomplete SQL")


class MigrationRunner:
    """Apply migrations atomically and record their checksums."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        directory: str | Path | None = None,
        migrations: Sequence[Migration] | None = None,
    ) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be sqlite3.Connection")
        if directory is not None and migrations is not None:
            raise ValueError("provide directory or migrations, not both")
        self.connection = connection
        self.directory = Path(directory).resolve() if directory is not None else None
        self._migrations = (
            tuple(sorted(migrations, key=lambda item: (item.version, item.name)))
            if migrations is not None
            else discover_migrations(self.directory)
            if self.directory is not None
            else ()
        )
        self._validate_migrations(self._migrations)

    @staticmethod
    def _validate_migrations(migrations: Sequence[Migration]) -> None:
        versions: set[int] = set()
        names: set[str] = set()
        for migration in migrations:
            if not isinstance(migration, Migration):
                raise TypeError("migrations must contain Migration values")
            if migration.version in versions or migration.name in names:
                raise MigrationDiscoveryError("duplicate migration registration")
            versions.add(migration.version)
            names.add(migration.name)

    @property
    def registered(self) -> tuple[Migration, ...]:
        return tuple(self._migrations)

    def apply(self) -> int:
        if not self._migrations:
            raise MigrationDiscoveryError("no migrations registered")
        if self._migrations[0].version != 0:
            raise MigrationDiscoveryError("base migration 0000 is required")
        connection = self.connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            discovered = {
                migration.version: (migration.name, migration.checksum)
                for migration in self._migrations
            }
            try:
                applied_rows = connection.execute(
                    "SELECT version, name, checksum FROM schema_migrations"
                ).fetchall()
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc).lower():
                    applied_rows = ()
                else:
                    raise MigrationError(
                        "schema migration metadata is unavailable"
                    ) from exc
            for applied in applied_rows:
                expected = discovered.get(int(applied[0]))
                if expected is None:
                    raise MigrationChecksumError("applied migration is missing")
                if (applied[1], applied[2]) != expected:
                    raise MigrationChecksumError("applied migration checksum mismatch")
            for migration in self._migrations:
                try:
                    applied = connection.execute(
                        "SELECT checksum, name FROM schema_migrations WHERE version = ?",
                        (migration.version,),
                    ).fetchone()
                except sqlite3.OperationalError as exc:
                    if migration.version == 0 and "no such table" in str(exc).lower():
                        applied = None
                    else:
                        raise MigrationError(
                            "schema migration metadata is unavailable"
                        ) from exc
                if applied is not None:
                    if applied[0] != migration.checksum or applied[1] != migration.name:
                        raise MigrationChecksumError(
                            "applied migration checksum mismatch"
                        )
                    continue
                for statement in _statements(migration.sql):
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, name, checksum, applied_at) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        migration.version,
                        migration.name,
                        migration.checksum,
                        datetime.now(UTC).isoformat(),
                    ),
                )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()
        return int(row[0])

    def run(self) -> int:
        """Compatibility spelling for ``apply``."""

        return self.apply()

    def current_version(self) -> int:
        try:
            row = self.connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return 0
            raise MigrationError("schema migration metadata is unavailable") from exc
        except sqlite3.DatabaseError as exc:
            raise MigrationError("schema migration metadata is unavailable") from exc
        return int(row[0])


discover = discover_migrations
