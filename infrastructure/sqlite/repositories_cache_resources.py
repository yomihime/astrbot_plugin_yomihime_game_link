"""SQLite repositories for scope-bound cache and resource metadata."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yomihime_game_link_sdk.storage import (
    CacheEntry,
    CacheLookup,
    CacheLookupStatus,
    OwnerScope,
    OwnershipKind,
    ResourceMetadata,
)

from ...core.contracts.services import CacheAccessRequest, validate_cache_access_request
from ...core.contracts.storage import OwnerScope_validate, freeze_json
from ...core.contracts.validation_boundary import validate_contract
from ...core.ports import (
    AuthorizationWindowExpired,
    RevisionConflict,
    UniqueConstraintViolation,
)
from .database import SQLiteDatabase, SQLiteUnitOfWork, SQLiteUnitOfWorkFactory


def _database(
    value: SQLiteDatabase | SQLiteUnitOfWorkFactory | str | Path,
) -> SQLiteDatabase:
    if isinstance(value, SQLiteDatabase):
        return value
    if isinstance(value, SQLiteUnitOfWorkFactory):
        return value.database
    if isinstance(value, (str, Path)):
        return SQLiteDatabase(value)
    raise TypeError("database must be a SQLiteDatabase, factory, or path")


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


def _datetime(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value).astimezone(UTC)


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _json(value: object) -> str:
    try:
        return json.dumps(
            _plain(freeze_json(value)),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        raise ValueError("value is not valid JSON") from None


def _load_json(value: object) -> Mapping[str, Any]:
    if not isinstance(value, str):
        raise ValueError("stored cache payload is invalid")
    try:
        result = freeze_json(json.loads(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("stored cache payload is invalid") from None
    if not isinstance(result, Mapping):
        raise ValueError("stored cache payload must be an object")
    return result


def _scope_parts(scope: OwnerScope) -> tuple[str, str | None, str | None, int | None]:
    validate_contract(scope)
    scope = OwnerScope_validate(scope)
    if scope.kind is OwnershipKind.PUBLIC:
        return "public", None, None, None
    if scope.kind is OwnershipKind.USER:
        return "user", scope.user_id, None, None
    assert scope.grant is not None
    return "authorized", scope.user_id, scope.grant.grant_id, scope.grant.revision


def _scope_from_row(row: sqlite3.Row) -> OwnerScope:
    """Rebuild the scope from the actual 0040 ``assets`` columns."""
    try:
        kind = row["scope_kind"]
        user_id = row["user_id"]
        grant_id = row["grant_id"]
        grant_revision = row["grant_revision"]
        if kind == "public":
            if (
                user_id is not None
                or grant_id is not None
                or grant_revision is not None
            ):
                raise ValueError("public resource row carries owner data")
            return OwnerScope.public()
        if kind == "user":
            if user_id is None or grant_id is not None or grant_revision is not None:
                raise ValueError("user resource row carries invalid grant data")
            return OwnerScope.user(user_id)
        if kind == "authorized":
            from yomihime_game_link_sdk.storage import GrantReference

            if user_id is None or grant_id is None or grant_revision is None:
                raise ValueError("authorized resource row is incomplete")
            return OwnerScope.authorized(
                user_id,
                validate_contract(GrantReference(grant_id, int(grant_revision))),
            )
    except (IndexError, KeyError, TypeError, ValueError):
        raise ValueError("stored owner scope is invalid") from None
    raise ValueError("stored owner scope is invalid")


def _validate_source_version(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("source version must be a non-negative integer")
    return value


def _authorization_deadline(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("authorization deadline must be timezone-aware")
    return value.astimezone(UTC)


def _check_authorization_deadline(value: datetime | None) -> None:
    if value is not None and datetime.now(UTC) >= value:
        raise AuthorizationWindowExpired("grant authorization window has expired")


class SQLiteCacheRepository:
    """A real SQLite implementation of the frozen ``CacheRepository`` port."""

    def __init__(self, database: SQLiteDatabase | SQLiteUnitOfWorkFactory | str | Path):
        self.database = _database(database)

    @staticmethod
    def _request(request: CacheAccessRequest) -> CacheAccessRequest:
        return validate_cache_access_request(request)

    @staticmethod
    def _entry(row: sqlite3.Row) -> CacheEntry:
        return validate_contract(
            CacheEntry(
                row["cache_key"],
                _load_json(row["payload_json"]),
                _datetime(row["expires_at"]),
                int(row["revision"]),
            )
        )

    @staticmethod
    def _lookup_params(request: CacheAccessRequest) -> tuple[Any, ...]:
        kind, user_id, grant_id, grant_revision = _scope_parts(request.scope)
        return request.key, request.visibility.value, user_id, grant_id, grant_revision

    async def get(
        self,
        request: CacheAccessRequest,
        *,
        minimum_source_version: int | None = None,
    ) -> CacheLookup:
        request = self._request(request)
        if minimum_source_version is not None:
            minimum_source_version = _validate_source_version(minimum_source_version)

        def _read(unit: SQLiteUnitOfWork) -> tuple[bool, tuple[Any, ...] | None]:
            row = unit.connection.execute(
                "SELECT * FROM cache_entries WHERE cache_key=? AND visibility=? "
                "AND user_id IS ? AND grant_id IS ? AND grant_revision IS ?",
                self._lookup_params(request),
            ).fetchone()
            grant_mismatch = False
            if row is None and request.visibility.value == "authorized":
                grant = request.scope.grant
                assert grant is not None
                prior = unit.connection.execute(
                    "SELECT 1 FROM cache_entries WHERE cache_key=? AND visibility='authorized' "
                    "AND user_id=? AND grant_id=? LIMIT 1",
                    (request.key, request.scope.user_id, grant.grant_id),
                ).fetchone()
                grant_mismatch = prior is not None
            detached = None
            if row is not None:
                detached = (
                    bool(row["invalidated"]),
                    row["expires_at"],
                    int(row["source_version"]),
                    row["cache_key"],
                    row["payload_json"],
                    int(row["revision"]),
                )
            return grant_mismatch, detached

        grant_mismatch, row = await self.database.executor.run_read(_read)
        if grant_mismatch:
            return validate_contract(CacheLookup(CacheLookupStatus.REJECTED))
        if row is None or row[0]:
            return validate_contract(CacheLookup(CacheLookupStatus.MISS))
        expires_at = _datetime(row[1])
        if expires_at is None or expires_at <= datetime.now(UTC):
            return validate_contract(CacheLookup(CacheLookupStatus.EXPIRED))
        if minimum_source_version is not None and row[2] < minimum_source_version:
            return validate_contract(CacheLookup(CacheLookupStatus.REJECTED))
        entry = validate_contract(
            CacheEntry(row[3], _load_json(row[4]), expires_at, row[5])
        )
        return validate_contract(CacheLookup(CacheLookupStatus.HIT, entry))

    async def put(
        self,
        request: CacheAccessRequest,
        entry: CacheEntry,
        *,
        expected_revision: int | None = None,
        source_version: int = 0,
        authorization_deadline: datetime | None = None,
    ) -> CacheEntry:
        validate_contract(entry)
        request = self._request(request)
        if not isinstance(entry, CacheEntry):
            raise TypeError("entry must be a CacheEntry")
        if entry.key != request.key:
            raise ValueError("cache entry key does not match request")
        if expected_revision is not None and (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise ValueError("expected revision must be non-negative")
        source_version = _validate_source_version(source_version)
        authorization_deadline = _authorization_deadline(authorization_deadline)
        params = self._lookup_params(request)
        payload_json = _json(entry.payload)
        expires_at = _iso(entry.expires_at)

        def _write(unit: SQLiteUnitOfWork) -> int:
            _check_authorization_deadline(authorization_deadline)
            row = unit.connection.execute(
                "SELECT revision, invalidated FROM cache_entries WHERE cache_key=? "
                "AND visibility=? AND user_id IS ? AND grant_id IS ? AND grant_revision IS ?",
                params,
            ).fetchone()
            current = None if row is None else int(row["revision"])
            if expected_revision is None:
                if current is not None:
                    raise RevisionConflict("cache", 0, current)
                revision = 1
                try:
                    unit.execute(
                        "INSERT INTO cache_entries(cache_key,visibility,user_id,grant_id,grant_revision,"
                        "payload_json,expires_at,source_version,revision,invalidated) VALUES (?,?,?,?,?,?,?,?,?,0)",
                        (
                            *params,
                            payload_json,
                            expires_at,
                            source_version,
                            revision,
                        ),
                    )
                except sqlite3.IntegrityError:
                    raise UniqueConstraintViolation("cache") from None
            else:
                if current != expected_revision:
                    raise RevisionConflict("cache", expected_revision, current or 0)
                revision = expected_revision + 1
                unit.execute(
                    "UPDATE cache_entries SET payload_json=?, expires_at=?, source_version=?, "
                    "revision=?, invalidated=0 WHERE cache_key=? AND visibility=? AND user_id IS ? "
                    "AND grant_id IS ? AND grant_revision IS ? AND revision=?",
                    (
                        payload_json,
                        expires_at,
                        source_version,
                        revision,
                        *params,
                        expected_revision,
                    ),
                )
            _check_authorization_deadline(authorization_deadline)
            return revision

        revision = await self.database.executor.run_transaction(
            _write, begin_mode="IMMEDIATE"
        )
        return validate_contract(
            CacheEntry(entry.key, entry.payload, entry.expires_at, revision)
        )

    async def invalidate(
        self,
        request: CacheAccessRequest,
        *,
        authorization_deadline: datetime | None = None,
    ) -> None:
        request = self._request(request)
        authorization_deadline = _authorization_deadline(authorization_deadline)
        params = self._lookup_params(request)

        def _write(unit: SQLiteUnitOfWork) -> None:
            _check_authorization_deadline(authorization_deadline)
            unit.execute(
                "UPDATE cache_entries SET invalidated=1, revision=revision+1 "
                "WHERE cache_key=? AND visibility=? AND user_id IS ? AND grant_id IS ? AND grant_revision IS ?",
                params,
            )
            _check_authorization_deadline(authorization_deadline)

        await self.database.executor.run_transaction(_write, begin_mode="IMMEDIATE")

    async def current_revision(self, request: CacheAccessRequest) -> int | None:
        request = self._request(request)

        def _read(unit: SQLiteUnitOfWork) -> int | None:
            row = unit.connection.execute(
                "SELECT revision FROM cache_entries WHERE cache_key=? AND visibility=? "
                "AND user_id IS ? AND grant_id IS ? AND grant_revision IS ?",
                self._lookup_params(request),
            ).fetchone()
            return None if row is None else int(row["revision"])

        return await self.database.executor.run_read(_read)


_MEDIA_TYPE = re.compile(r"^[a-z][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$")


def _validate_metadata(metadata: ResourceMetadata) -> ResourceMetadata:
    validate_contract(metadata)
    if not isinstance(metadata, ResourceMetadata):
        raise TypeError("metadata must be ResourceMetadata")
    metadata = validate_contract(
        ResourceMetadata(
            metadata.asset_id,
            metadata.media_type,
            metadata.scope,
            metadata.size_bytes,
            metadata.expires_at,
            metadata.temporary,
            metadata.revision,
        )
    )
    if not _MEDIA_TYPE.fullmatch(metadata.media_type.lower()):
        raise ValueError("unsupported media type")
    if metadata.asset_id in {".", ".."} or ".." in metadata.asset_id:
        raise ValueError("asset id cannot contain traversal")
    return metadata


class SQLiteResourceRepository:
    """SQLite index for resources; bytes are supplied by a SafeFileStore."""

    def __init__(
        self,
        database: SQLiteDatabase | SQLiteUnitOfWorkFactory | str | Path,
        file_store: Any = None,
        *,
        root: str | Path | None = None,
    ) -> None:
        self.database = _database(database)
        self._file_store = (
            None
            if file_store is None or isinstance(file_store, (str, Path))
            else file_store
        )
        self._file_store_root = (
            file_store
            if isinstance(file_store, (str, Path))
            else root or (self.database.path.parent / "assets")
        )

    @property
    def file_store(self) -> Any:
        if self._file_store is None:
            from ..files import LocalSafeFileStore

            self._file_store = LocalSafeFileStore(self._file_store_root)
        return self._file_store

    @staticmethod
    def _params(asset_id: str, scope: OwnerScope) -> tuple[Any, ...]:
        validate_contract(scope)
        if (
            not isinstance(asset_id, str)
            or not asset_id.strip()
            or "/" in asset_id
            or "\\" in asset_id
        ):
            raise ValueError("asset id is invalid")
        kind, user_id, grant_id, grant_revision = _scope_parts(scope)
        return asset_id, kind, user_id, grant_id, grant_revision

    @staticmethod
    def _metadata(row: sqlite3.Row) -> ResourceMetadata:
        return validate_contract(
            ResourceMetadata(
                row["asset_id"],
                row["media_type"],
                _scope_from_row(row),
                int(row["size_bytes"]),
                _datetime(row["expires_at"]),
                bool(row["temporary"]),
                int(row["revision"]),
            )
        )

    @property
    def managed_cache(self):
        """One lifecycle coordinator shared by all invocation facades."""
        manager = getattr(self, "_managed_cache", None)
        if manager is None:
            from ...services.managed_resources import ManagedResourceCache

            manager = ManagedResourceCache(self)
            self._managed_cache = manager
        return manager

    @staticmethod
    def managed_scope_key(scope: OwnerScope) -> str:
        # Database-only normalized key, never an SDK ownership capability.
        return json.dumps(_scope_parts(scope), separators=(",", ":"))

    async def managed_entry(self, asset_id: str, scope: OwnerScope):
        key = self.managed_scope_key(scope)

        def read(unit):
            row = unit.execute(
                "SELECT content_sha256,size_bytes,expires_at,last_access,evicting "
                "FROM managed_resource_entries WHERE scope_key=? AND asset_id=?",
                (key, asset_id),
            ).fetchone()
            return None if row is None else tuple(row)

        return await self.database.executor.run_read(read)

    async def managed_usage(self, scope: OwnerScope) -> tuple[int, int, int]:
        key = self.managed_scope_key(scope)

        def read(unit):
            row = unit.execute(
                "SELECT COALESCE(SUM(size_bytes),0),COUNT(*) "
                "FROM managed_resource_entries WHERE scope_key=?",
                (key,),
            ).fetchone()
            counter = unit.execute(
                "SELECT evictions FROM managed_resource_scopes WHERE scope_key=?",
                (key,),
            ).fetchone()
            return int(row[0]), int(row[1]), 0 if counter is None else int(counter[0])

        return await self.database.executor.run_read(read)

    async def managed_candidates(
        self, scope: OwnerScope, *, now: datetime, limit: int, expired_only: bool
    ) -> tuple[tuple[str, int], ...]:
        key = self.managed_scope_key(scope)

        def read(unit):
            if expired_only:
                rows = unit.execute(
                    "SELECT asset_id,size_bytes FROM managed_resource_entries "
                    "WHERE scope_key=? AND (evicting=1 OR expires_at<=?) "
                    "ORDER BY evicting DESC,expires_at,asset_id LIMIT ?",
                    (key, _iso(now), limit),
                ).fetchall()
            else:
                rows = unit.execute(
                    "SELECT asset_id,size_bytes FROM managed_resource_entries "
                    "WHERE scope_key=? ORDER BY last_access,asset_id LIMIT ?",
                    (key, limit),
                ).fetchall()
            return tuple((str(row[0]), int(row[1])) for row in rows)

        return await self.database.executor.run_read(read)

    async def managed_mark_evicting(
        self, asset_id: str, scope: OwnerScope, *, authorization_deadline=None
    ) -> None:
        key = self.managed_scope_key(scope)

        def write(unit):
            _check_authorization_deadline(authorization_deadline)
            unit.execute(
                "UPDATE managed_resource_entries SET evicting=1 "
                "WHERE scope_key=? AND asset_id=?",
                (key, asset_id),
            )
            _check_authorization_deadline(authorization_deadline)

        await self.database.executor.run_transaction(write, begin_mode="IMMEDIATE")

    async def managed_finish_eviction(
        self, asset_id: str, scope: OwnerScope, *, authorization_deadline=None
    ) -> None:
        key = self.managed_scope_key(scope)
        params = self._params(asset_id, scope)

        def write(unit):
            _check_authorization_deadline(authorization_deadline)
            row = unit.execute(
                "SELECT evicting FROM managed_resource_entries "
                "WHERE scope_key=? AND asset_id=?",
                (key, asset_id),
            ).fetchone()
            if row is None or not row[0]:
                return
            unit.execute(
                "DELETE FROM assets WHERE asset_id=? AND scope_kind=? AND user_id IS ? "
                "AND grant_id IS ? AND grant_revision IS ?",
                params,
            )
            unit.execute(
                "DELETE FROM managed_resource_entries WHERE scope_key=? AND asset_id=?",
                (key, asset_id),
            )
            unit.execute(
                "INSERT INTO managed_resource_scopes(scope_key,evictions) VALUES (?,1) "
                "ON CONFLICT(scope_key) DO UPDATE SET evictions=evictions+1",
                (key,),
            )
            _check_authorization_deadline(authorization_deadline)

        await self.database.executor.run_transaction(write, begin_mode="IMMEDIATE")

    async def managed_touch(
        self, asset_id: str, scope: OwnerScope, now: datetime
    ) -> None:
        key = self.managed_scope_key(scope)

        def write(unit):
            unit.execute(
                "UPDATE managed_resource_entries SET last_access=? "
                "WHERE scope_key=? AND asset_id=? AND evicting=0",
                (_iso(now), key, asset_id),
            )

        await self.database.executor.run_transaction(write, begin_mode="IMMEDIATE")

    async def register_managed(
        self,
        metadata: ResourceMetadata,
        *,
        module_id: str,
        content_sha256: str,
        now: datetime,
        authorization_deadline=None,
    ) -> ResourceMetadata:
        """Commit resource/index retention facts together; never adopt legacy rows."""
        metadata = _validate_metadata(metadata)
        params = self._params(metadata.asset_id, metadata.scope)
        key = self.managed_scope_key(metadata.scope)

        def write(unit):
            _check_authorization_deadline(authorization_deadline)
            row = unit.execute(
                "SELECT revision FROM assets WHERE asset_id=? AND scope_kind=? "
                "AND user_id IS ? AND grant_id IS ? AND grant_revision IS ?",
                params,
            ).fetchone()
            managed = unit.execute(
                "SELECT 1 FROM managed_resource_entries WHERE scope_key=? AND asset_id=?",
                (key, metadata.asset_id),
            ).fetchone()
            revision = 1 if row is None else int(row[0]) + 1
            if row is None:
                unit.execute(
                    "INSERT INTO assets(asset_id,media_type,scope_kind,user_id,grant_id,"
                    "grant_revision,size_bytes,expires_at,temporary,revision) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        params[0],
                        metadata.media_type,
                        *params[1:],
                        metadata.size_bytes,
                        _iso(metadata.expires_at),
                        1,
                        revision,
                    ),
                )
            else:
                unit.execute(
                    "UPDATE assets SET media_type=?,size_bytes=?,expires_at=?,revision=? "
                    "WHERE asset_id=? AND scope_kind=? AND user_id IS ? AND grant_id IS ? "
                    "AND grant_revision IS ?",
                    (
                        metadata.media_type,
                        metadata.size_bytes,
                        _iso(metadata.expires_at),
                        revision,
                        *params,
                    ),
                )
            if row is None or managed is not None:
                unit.execute(
                    "INSERT INTO managed_resource_entries(scope_key,asset_id,module_id,"
                    "content_sha256,size_bytes,expires_at,last_access,evicting) "
                    "VALUES (?,?,?,?,?,?,?,0) ON CONFLICT(scope_key,asset_id) DO UPDATE SET "
                    "content_sha256=excluded.content_sha256,size_bytes=excluded.size_bytes,"
                    "expires_at=excluded.expires_at,last_access=excluded.last_access,evicting=0",
                    (
                        key,
                        metadata.asset_id,
                        module_id,
                        content_sha256,
                        metadata.size_bytes,
                        _iso(metadata.expires_at),
                        _iso(now),
                    ),
                )
            _check_authorization_deadline(authorization_deadline)
            return revision

        revision = await self.database.executor.run_transaction(
            write, begin_mode="IMMEDIATE"
        )
        await self.file_store.confirm(metadata)
        return ResourceMetadata(
            metadata.asset_id,
            metadata.media_type,
            metadata.scope,
            metadata.size_bytes,
            metadata.expires_at,
            True,
            revision,
        )

    async def current_revision(self, asset_id: str, scope: OwnerScope) -> int | None:
        validate_contract(scope)
        params = self._params(asset_id, scope)

        def _read(unit: SQLiteUnitOfWork) -> int | None:
            row = unit.connection.execute(
                "SELECT revision FROM assets WHERE asset_id=? AND scope_kind=? AND user_id IS ? "
                "AND grant_id IS ? AND grant_revision IS ?",
                params,
            ).fetchone()
            return None if row is None else int(row["revision"])

        return await self.database.executor.run_read(_read)

    async def register(
        self,
        metadata: ResourceMetadata,
        *,
        expected_revision: int | None = None,
        authorization_deadline: datetime | None = None,
    ) -> ResourceMetadata:
        validate_contract(metadata)
        metadata = _validate_metadata(metadata)
        authorization_deadline = _authorization_deadline(authorization_deadline)
        if expected_revision is not None and (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise ValueError("expected revision must be non-negative")
        params = self._params(metadata.asset_id, metadata.scope)

        def _register(unit: SQLiteUnitOfWork) -> int:
            _check_authorization_deadline(authorization_deadline)
            try:
                row = unit.connection.execute(
                    "SELECT * FROM assets WHERE asset_id=? AND scope_kind=? AND user_id IS ? "
                    "AND grant_id IS ? AND grant_revision IS ?",
                    params,
                ).fetchone()
                current = None if row is None else int(row["revision"])
                if expected_revision is None:
                    if current is not None:
                        raise RevisionConflict("asset", 0, current)
                    revision = 1
                    unit.execute(
                        "INSERT INTO assets(asset_id,media_type,scope_kind,user_id,grant_id,grant_revision,"
                        "size_bytes,expires_at,temporary,revision) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (
                            params[0],
                            metadata.media_type,
                            *params[1:],
                            metadata.size_bytes,
                            _iso(metadata.expires_at),
                            int(metadata.temporary),
                            revision,
                        ),
                    )
                else:
                    if current != expected_revision:
                        raise RevisionConflict("asset", expected_revision, current or 0)
                    revision = expected_revision + 1
                    unit.execute(
                        "UPDATE assets SET media_type=?,size_bytes=?,expires_at=?,temporary=?,revision=? "
                        "WHERE asset_id=? AND scope_kind=? AND user_id IS ? AND grant_id IS ? AND grant_revision IS ? "
                        "AND revision=?",
                        (
                            metadata.media_type,
                            metadata.size_bytes,
                            _iso(metadata.expires_at),
                            int(metadata.temporary),
                            revision,
                            *params,
                            expected_revision,
                        ),
                    )
                _check_authorization_deadline(authorization_deadline)
                return revision
            except sqlite3.IntegrityError:
                raise UniqueConstraintViolation("asset") from None

        revision = await self.database.executor.run_transaction(
            _register, begin_mode="IMMEDIATE"
        )
        confirm = getattr(self.file_store, "confirm", None)
        if confirm is not None:
            await confirm(metadata)
        return validate_contract(
            ResourceMetadata(
                metadata.asset_id,
                metadata.media_type,
                metadata.scope,
                metadata.size_bytes,
                metadata.expires_at,
                metadata.temporary,
                revision,
            )
        )

    async def registration_state(
        self, asset_id: str, scope: OwnerScope
    ) -> ResourceMetadata | None:
        """Return the exact index row without filtering resource expiry."""
        validate_contract(scope)
        params = self._params(asset_id, scope)

        def _read(unit: SQLiteUnitOfWork) -> ResourceMetadata | None:
            row = unit.connection.execute(
                "SELECT * FROM assets WHERE asset_id=? AND scope_kind=? AND user_id IS ? "
                "AND grant_id IS ? AND grant_revision IS ?",
                params,
            ).fetchone()
            return None if row is None else self._metadata(row)

        metadata = await self.database.executor.run_read(_read)
        if metadata is not None and metadata.scope != OwnerScope_validate(scope):
            raise ValueError("stored owner scope does not match lookup")
        return metadata

    async def metadata(
        self, asset_id: str, scope: OwnerScope
    ) -> ResourceMetadata | None:
        validate_contract(scope)
        params = self._params(asset_id, scope)

        def _read(unit: SQLiteUnitOfWork) -> ResourceMetadata | None:
            row = unit.connection.execute(
                "SELECT * FROM assets WHERE asset_id=? AND scope_kind=? AND user_id IS ? "
                "AND grant_id IS ? AND grant_revision IS ?",
                params,
            ).fetchone()
            return None if row is None else self._metadata(row)

        metadata = await self.database.executor.run_read(_read)
        if metadata is None:
            return None
        if metadata.scope != OwnerScope_validate(scope):
            raise ValueError("stored owner scope does not match lookup")
        if metadata.expires_at is not None and metadata.expires_at <= datetime.now(UTC):
            return None
        return metadata

    async def read(self, asset_id: str, scope: OwnerScope) -> bytes:
        validate_contract(scope)
        metadata = await self.metadata(asset_id, scope)
        if metadata is None:
            raise FileNotFoundError("resource is unavailable")
        content = await self.file_store.read(asset_id, metadata.scope)
        if len(content) != metadata.size_bytes:
            raise ValueError("resource size does not match metadata")
        return content

    async def delete(
        self,
        asset_id: str,
        scope: OwnerScope,
        *,
        authorization_deadline: datetime | None = None,
    ) -> None:
        validate_contract(scope)
        params = self._params(asset_id, scope)
        authorization_deadline = _authorization_deadline(authorization_deadline)

        def _delete(unit: SQLiteUnitOfWork) -> None:
            _check_authorization_deadline(authorization_deadline)
            unit.execute(
                "DELETE FROM assets WHERE asset_id=? AND scope_kind=? AND user_id IS ? "
                "AND grant_id IS ? AND grant_revision IS ?",
                params,
            )
            _check_authorization_deadline(authorization_deadline)

        await self.database.executor.run_transaction(_delete, begin_mode="IMMEDIATE")


CacheRepository = SQLiteCacheRepository
CacheStore = SQLiteCacheRepository
ResourceRepository = SQLiteResourceRepository
AssetRepository = SQLiteResourceRepository

__all__ = [
    "SQLiteCacheRepository",
    "SQLiteResourceRepository",
    "CacheRepository",
    "CacheStore",
    "ResourceRepository",
    "AssetRepository",
]
