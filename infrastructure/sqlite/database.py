"""Standard-library SQLite connection and transaction boundaries."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Sequence

from ...core.ports import RevisionConflict, UniqueConstraintViolation
from .migrations import MigrationRunner

SQLITE_HEADER = b"SQLite format 3\x00"


class SQLiteDatabaseError(RuntimeError):
    """Base error for controlled SQLite infrastructure failures."""


class DatabaseCorruptionError(SQLiteDatabaseError):
    """An existing file is not a valid SQLite database."""


class DatabaseClosedError(SQLiteDatabaseError):
    """An operation was attempted after a connection was closed."""


class SQLiteBusyError(SQLiteDatabaseError):
    """SQLite could not obtain a lock before the configured timeout."""


def _translate_sqlite_error(exc: sqlite3.Error) -> SQLiteDatabaseError | None:
    if isinstance(exc, sqlite3.OperationalError) and any(
        marker in str(exc).lower()
        for marker in ("database is locked", "database is busy")
    ):
        return SQLiteBusyError("database lock timeout")
    return None


class SQLiteDatabase:
    """A caller-owned SQLite file with explicit migration and connection setup."""

    def __init__(
        self,
        path: str | Path,
        *,
        migrations_dir: str | Path | None = None,
        timeout: float = 1.0,
        busy_timeout_ms: int | None = None,
    ) -> None:
        if not isinstance(path, (str, Path)) or not str(path).strip():
            raise ValueError("database path must be non-empty")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or timeout < 0
        ):
            raise ValueError("timeout must be non-negative")
        if busy_timeout_ms is None:
            busy_timeout_ms = max(0, int(float(timeout) * 1000))
        if (
            isinstance(busy_timeout_ms, bool)
            or not isinstance(busy_timeout_ms, int)
            or busy_timeout_ms < 0
        ):
            raise ValueError("busy_timeout_ms must be non-negative")
        self.path = Path(path).expanduser()
        self.migrations_dir = (
            Path(migrations_dir).expanduser()
            if migrations_dir is not None
            else Path(__file__).with_name("migrations")
        )
        self.timeout = float(timeout)
        self.busy_timeout_ms = busy_timeout_ms
        self._init_lock = threading.Lock()
        self._sync_initialized = False
        self._executor_lock = threading.Lock()
        self._executor: Any = None

    def _validate_existing_file(self) -> None:
        if not self.path.exists():
            return
        if not self.path.is_file():
            raise DatabaseCorruptionError("database path is not a file")
        try:
            with self.path.open("rb") as handle:
                header = handle.read(len(SQLITE_HEADER))
        except OSError as exc:
            raise DatabaseCorruptionError("database file cannot be read") from exc
        if len(header) != len(SQLITE_HEADER) or header != SQLITE_HEADER:
            raise DatabaseCorruptionError("existing database file is not SQLite")

    def _connect(self) -> sqlite3.Connection:
        """Open a connection without doing database-wide maintenance checks."""

        self._validate_existing_file()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                str(self.path),
                timeout=self.timeout,
                isolation_level=None,
                check_same_thread=True,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
            connection.execute("PRAGMA schema_version")
            return connection
        except sqlite3.DatabaseError as exc:
            if connection is not None:
                connection.close()
            translated = _translate_sqlite_error(exc)
            if translated is not None:
                raise translated from None
            raise DatabaseCorruptionError("database file cannot be opened") from exc

    def connect(self) -> sqlite3.Connection:
        """Open one non-shareable synchronous connection.

        This primitive intentionally does not run migrations or integrity
        checks. Production async callers use :class:`SQLiteExecutor`; direct
        synchronous callers must explicitly call ``initialize`` first.
        """

        return self._connect()

    @staticmethod
    def _check_integrity(connection: sqlite3.Connection) -> None:
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
        except sqlite3.DatabaseError as exc:
            raise DatabaseCorruptionError("database integrity check failed") from exc
        if integrity is None or integrity[0] != "ok":
            raise DatabaseCorruptionError("database integrity check failed")

    def _initialize_sync(self) -> int:
        """Run startup integrity validation and migrations on the caller thread."""

        with self._init_lock:
            connection = self._connect()
            try:
                self._check_integrity(connection)
                version = MigrationRunner(
                    connection, directory=self.migrations_dir
                ).apply()
            except sqlite3.DatabaseError as exc:
                if (
                    "malformed" in str(exc).lower()
                    or "not a database" in str(exc).lower()
                ):
                    raise DatabaseCorruptionError(
                        "database file is malformed"
                    ) from None
                raise
            finally:
                connection.close()
            self._sync_initialized = True
            return version

    def initialize(self) -> int:
        """Synchronously validate and migrate for explicit CLI/test use only."""

        return self._initialize_sync()

    def migrate(self) -> int:
        return self.initialize()

    def schema_version(self) -> int:
        self.initialize()
        connection = self._connect()
        try:
            return MigrationRunner(
                connection, directory=self.migrations_dir
            ).current_version()
        finally:
            connection.close()

    def unit_of_work(self, *, begin_mode: str = "DEFERRED") -> "SQLiteUnitOfWork":
        # Kept as a synchronous compatibility primitive for direct tests. All
        # production async repositories go through the executor below.
        if not self._sync_initialized:
            self.initialize()
        return SQLiteUnitOfWork(self._connect(), begin_mode=begin_mode)

    @property
    def executor(self) -> Any:
        """Return this database's lazily created, single-thread async executor."""

        with self._executor_lock:
            if self._executor is None:
                from .executor import SQLiteExecutor

                self._executor = SQLiteExecutor(self)
            return self._executor

    async def begin(self) -> "SQLiteUnitOfWork":
        """Legacy direct-UoW adapter retained for explicit tests.

        Production async code must use ``database.executor.run_transaction``
        so the unit of work never leaves its worker thread.
        """

        unit = self.unit_of_work()
        await unit.__aenter__()
        return unit

    def factory(self) -> "SQLiteUnitOfWorkFactory":
        return SQLiteUnitOfWorkFactory(self)


class SQLiteUnitOfWork:
    """One SQLite connection and one explicit transaction scope."""

    def __init__(
        self, connection: sqlite3.Connection, *, begin_mode: str = "DEFERRED"
    ) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be sqlite3.Connection")
        begin_mode = begin_mode.upper()
        if begin_mode not in {"DEFERRED", "IMMEDIATE", "EXCLUSIVE"}:
            raise ValueError("begin_mode must be DEFERRED, IMMEDIATE, or EXCLUSIVE")
        self._connection = connection
        self.begin_mode = begin_mode
        self._closed = False
        self._entered = False

    @property
    def connection(self) -> sqlite3.Connection:
        self._ensure_open()
        return self._connection

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def in_transaction(self) -> bool:
        return not self._closed and self._connection.in_transaction

    def _ensure_open(self) -> None:
        if self._closed:
            raise DatabaseClosedError("unit of work is closed")

    async def __aenter__(self) -> "SQLiteUnitOfWork":
        self._begin_sync()
        return self

    def _begin_sync(self) -> None:
        self._ensure_open()
        if not self._entered:
            try:
                self._connection.execute(f"BEGIN {self.begin_mode}")
            except sqlite3.Error as exc:
                translated = _translate_sqlite_error(exc)
                if translated is not None:
                    raise translated from None
                raise
            self._entered = True

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        try:
            if exc_type is None:
                await self.commit()
            else:
                await self.rollback()
        finally:
            await self.close()

    def execute(self, sql: str, parameters: Iterable[Any] = ()) -> sqlite3.Cursor:
        self._ensure_open()
        try:
            return self._connection.execute(sql, tuple(parameters))
        except sqlite3.IntegrityError as exc:
            if "unique" in str(exc).lower():
                raise UniqueConstraintViolation("sqlite") from None
            raise
        except sqlite3.Error as exc:
            translated = _translate_sqlite_error(exc)
            if translated is not None:
                raise translated from None
            raise

    def executemany(
        self, sql: str, parameters: Iterable[Sequence[Any]]
    ) -> sqlite3.Cursor:
        self._ensure_open()
        try:
            return self._connection.executemany(sql, parameters)
        except sqlite3.IntegrityError as exc:
            if "unique" in str(exc).lower():
                raise UniqueConstraintViolation("sqlite") from None
            raise
        except sqlite3.Error as exc:
            translated = _translate_sqlite_error(exc)
            if translated is not None:
                raise translated from None
            raise

    def compare_and_swap(
        self,
        sql: str,
        parameters: Iterable[Any],
        *,
        resource: str,
        expected_revision: int,
        actual_revision: int | None = None,
    ) -> sqlite3.Cursor:
        cursor = self.execute(sql, parameters)
        if cursor.rowcount != 1:
            actual = expected_revision if actual_revision is None else actual_revision
            raise RevisionConflict(resource, expected_revision, actual)
        return cursor

    cas_update = compare_and_swap

    async def commit(self) -> None:
        self.commit_sync()

    def commit_sync(self) -> None:
        self._ensure_open()
        try:
            self._connection.commit()
        except sqlite3.Error as exc:
            translated = _translate_sqlite_error(exc)
            if translated is not None:
                raise translated from None
            raise

    async def rollback(self) -> None:
        self.rollback_sync()

    def rollback_sync(self) -> None:
        self._ensure_open()
        try:
            self._connection.rollback()
        except sqlite3.Error as exc:
            translated = _translate_sqlite_error(exc)
            if translated is not None:
                raise translated from None
            raise

    async def close(self) -> None:
        self.close_sync()

    def close_sync(self) -> None:
        if self._closed:
            return
        try:
            if self._connection.in_transaction:
                self._connection.rollback()
            self._connection.close()
        finally:
            self._closed = True


class SQLiteUnitOfWorkFactory:
    """Async adapter implementing ``core.ports.UnitOfWorkFactory``."""

    def __init__(
        self,
        database: SQLiteDatabase | str | Path,
        *,
        begin_mode: str = "DEFERRED",
        **kwargs: Any,
    ) -> None:
        self.database = (
            database
            if isinstance(database, SQLiteDatabase)
            else SQLiteDatabase(database, **kwargs)
        )
        if kwargs and isinstance(database, SQLiteDatabase):
            raise TypeError("connection options belong on SQLiteDatabase")
        self.begin_mode = begin_mode.upper()
        if self.begin_mode not in {"DEFERRED", "IMMEDIATE", "EXCLUSIVE"}:
            raise ValueError("begin_mode must be DEFERRED, IMMEDIATE, or EXCLUSIVE")

    async def begin(self) -> SQLiteUnitOfWork:
        """Legacy test adapter; repositories must use SQLiteExecutor."""

        unit = self.database.unit_of_work(begin_mode=self.begin_mode)
        await unit.__aenter__()
        return unit


Database = SQLiteDatabase
UnitOfWork = SQLiteUnitOfWork
UnitOfWorkFactory = SQLiteUnitOfWorkFactory


__all__ = [
    "Database",
    "DatabaseClosedError",
    "DatabaseCorruptionError",
    "SQLiteBusyError",
    "SQLiteDatabase",
    "SQLiteDatabaseError",
    "SQLiteUnitOfWork",
    "SQLiteUnitOfWorkFactory",
    "UnitOfWork",
    "UnitOfWorkFactory",
]
