"""SQLite-backed grants, login sessions, and secret receipt ledger.

The module deliberately stores only opaque secret reference metadata.  Secret
bytes are handled by :mod:`infrastructure.secret_store` and never enter a
SQLite parameter, DTO, exception, or log message.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yomihime_game_link_sdk.storage import SecretRef

from ...core.contracts.services import (
    Grant,
    GrantStatus,
    LoginSession,
    LoginSessionStatus,
)
from ...core.contracts.storage import validate_module_id
from ...core.contracts.validation_boundary import validate_contract
from ...core.ports import RevisionConflict, UniqueConstraintViolation
from .database import SQLiteDatabase


def _database(value: SQLiteDatabase | str | Path) -> SQLiteDatabase:
    return value if isinstance(value, SQLiteDatabase) else SQLiteDatabase(value)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


def _datetime(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value).astimezone(UTC)


def _bounded(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(character in value for character in ("/", "\\", "\n", "\r"))
    ):
        raise ValueError(f"{label} must be a bounded identifier")
    return value


def _module_id(value: object, label: str = "module_id") -> str:
    """Validate a registry module ID without treating it as a filesystem path.

    Module IDs may be local (``module``) or globally qualified
    (``package/module``).  ``validate_module_id`` enforces the grammar and
    rejects traversal, empty segments, and additional path components; the
    repository still uses bound SQLite parameters for isolation.
    """

    try:
        validate_module_id(value, label)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a bounded module identifier") from None
    return value


def _grant(row: sqlite3.Row) -> Grant:
    secret = None
    if row["secret_token"] is not None:
        secret = validate_contract(
            SecretRef(
                row["secret_token"],
                row["secret_principal_id"],
                row["secret_module_id"],
                row["secret_field"],
                row["secret_operation_id"],
            )
        )
    return Grant(
        row["grant_id"],
        int(row["revision"]),
        row["principal_id"],
        row["module_id"],
        row["account_id"],
        tuple(json.loads(row["scopes_json"])),
        secret,
        GrantStatus(row["status"]),
        _datetime(row["expires_at"]),
    )


def _session(row: sqlite3.Row) -> LoginSession:
    return LoginSession(
        row["session_id"],
        row["principal_id"],
        row["module_id"],
        int(row["generation"]),
        LoginSessionStatus(row["status"]),
        _datetime(row["expires_at"]),
    )


def _ref(row: sqlite3.Row) -> SecretRef:
    return validate_contract(
        SecretRef(
            row["secret_token"],
            row["principal_id"],
            row["module_id"],
            row["field"],
            row["operation_id"],
        )
    )


class SQLiteGrantStore:
    """A real SQLite implementation of ``GrantStore``."""

    def __init__(self, database: SQLiteDatabase | str | Path) -> None:
        self.database = _database(database)

    @staticmethod
    def _validate_expected(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("expected revision must be non-negative")
        return value

    async def current_grant(self, grant_id: str) -> Grant | None:
        _bounded(grant_id, "grant_id")

        def _worker(unit):
            row = unit.execute(
                "SELECT * FROM grants WHERE grant_id = ?", (grant_id,)
            ).fetchone()
            if row is None:
                return None
            grant = _grant(row)
            if (
                grant.status is GrantStatus.ACTIVE
                and grant.expires_at is not None
                and grant.expires_at <= datetime.now(UTC)
            ):
                unit.execute(
                    "UPDATE grants SET status='expired', revision=revision+1 "
                    "WHERE grant_id=? AND revision=? AND status='active'",
                    (grant_id, grant.revision),
                )
                return Grant(
                    grant.grant_id,
                    grant.revision + 1,
                    grant.principal_id,
                    grant.module_id,
                    grant.account_id,
                    grant.scopes,
                    grant.secret_ref,
                    GrantStatus.EXPIRED,
                    grant.expires_at,
                )
            return grant

        return await self.database.executor.run_transaction(
            _worker, begin_mode="IMMEDIATE"
        )

    async def list_for(self, principal_id: str, module_id: str) -> tuple[Grant, ...]:
        _bounded(principal_id, "principal_id")
        _module_id(module_id)

        def _worker(unit):
            rows = unit.execute(
                "SELECT * FROM grants WHERE principal_id = ? AND module_id = ? "
                "ORDER BY grant_id",
                (principal_id, module_id),
            ).fetchall()
            return tuple(_grant(row) for row in rows)

        return await self.database.executor.run_transaction(
            _worker, begin_mode="DEFERRED"
        )

    @staticmethod
    def _grant_params(grant: Grant) -> tuple[Any, ...]:
        grant = Grant(
            grant.grant_id,
            grant.revision,
            grant.principal_id,
            grant.module_id,
            grant.account_id,
            grant.scopes,
            grant.secret_ref,
            grant.status,
            grant.expires_at,
        )
        ref = grant.secret_ref
        return (
            grant.grant_id,
            grant.revision,
            grant.principal_id,
            grant.module_id,
            grant.account_id,
            json.dumps(grant.scopes, separators=(",", ":")),
            None if ref is None else ref.token,
            None if ref is None else ref.principal_id,
            None if ref is None else ref.module_id,
            None if ref is None else ref.field,
            None if ref is None else ref.operation_id,
            grant.status.value,
            _iso(grant.expires_at),
        )

    @staticmethod
    def _revoke_grant_in_unit(
        unit,
        grant: Grant,
        *,
        expected_revision: int,
        require_exact_grant: bool = False,
    ) -> Grant:
        """CAS-revoke one exact Grant and cancel pending sessions in this UoW."""

        expected_revision = SQLiteGrantStore._validate_expected(expected_revision)
        _bounded(grant.grant_id, "grant_id")
        row = unit.execute(
            "SELECT * FROM grants WHERE grant_id=?", (grant.grant_id,)
        ).fetchone()
        if row is None:
            raise RevisionConflict("grant", expected_revision, 0)
        current = _grant(row)
        if current.revision != expected_revision:
            raise RevisionConflict("grant", expected_revision, current.revision)
        if (grant.principal_id, grant.module_id) != (
            current.principal_id,
            current.module_id,
        ):
            raise ValueError("grant owner cannot change")
        if require_exact_grant and current != grant:
            raise ValueError("grant does not match the stored revision")
        if current.status is not GrantStatus.ACTIVE:
            raise ValueError("only an active grant can be revoked")
        revoked = Grant(
            current.grant_id,
            expected_revision + 1,
            current.principal_id,
            current.module_id,
            current.account_id,
            current.scopes,
            current.secret_ref,
            GrantStatus.REVOKED,
            current.expires_at,
        )
        params = SQLiteGrantStore._grant_params(revoked)
        cursor = unit.execute(
            "UPDATE grants SET revision=?, principal_id=?, module_id=?, account_id=?, "
            "scopes_json=?, secret_token=?, secret_principal_id=?, secret_module_id=?, "
            "secret_field=?, secret_operation_id=?, status=?, expires_at=? "
            "WHERE grant_id=? AND revision=?",
            (*params[1:], params[0], expected_revision),
        )
        if cursor.rowcount != 1:
            raise RevisionConflict("grant", expected_revision, current.revision)
        unit.execute(
            "UPDATE login_sessions SET status='cancelled' WHERE principal_id=? "
            "AND module_id=? AND status='pending'",
            (current.principal_id, current.module_id),
        )
        return revoked

    async def create_grant(self, grant: Grant, *, expected_revision: int) -> Grant:
        expected_revision = self._validate_expected(expected_revision)
        if expected_revision != 0 or grant.revision != 1:
            raise RevisionConflict("grant", expected_revision, 1)
        params = self._grant_params(grant)

        def _worker(unit):
            try:
                unit.execute(
                    "INSERT INTO grants (grant_id, revision, principal_id, module_id, account_id, "
                    "scopes_json, secret_token, secret_principal_id, secret_module_id, "
                    "secret_field, secret_operation_id, status, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    params,
                )
            except sqlite3.IntegrityError as exc:
                if "unique" in str(exc).lower() or "primary key" in str(exc).lower():
                    raise UniqueConstraintViolation("grant") from None
                raise
            return grant

        return await self.database.executor.run_transaction(
            _worker, begin_mode="IMMEDIATE"
        )

    async def _cas_grant(
        self, grant: Grant, *, expected_revision: int, status: GrantStatus | None = None
    ) -> Grant:
        expected_revision = self._validate_expected(expected_revision)
        _bounded(grant.grant_id, "grant_id")

        def _worker(unit):
            row = unit.execute(
                "SELECT * FROM grants WHERE grant_id = ?", (grant.grant_id,)
            ).fetchone()
            if row is None:
                raise RevisionConflict("grant", expected_revision, 0)
            current = _grant(row)
            if current.revision != expected_revision:
                raise RevisionConflict("grant", expected_revision, current.revision)
            if (grant.principal_id, grant.module_id) != (
                current.principal_id,
                current.module_id,
            ):
                raise ValueError("grant owner cannot change")
            desired = Grant(
                current.grant_id,
                expected_revision + 1,
                current.principal_id,
                current.module_id,
                grant.account_id,
                grant.scopes,
                grant.secret_ref,
                status or grant.status,
                grant.expires_at,
            )
            params = self._grant_params(desired)
            cursor = unit.execute(
                "UPDATE grants SET revision=?, principal_id=?, module_id=?, account_id=?, scopes_json=?, "
                "secret_token=?, secret_principal_id=?, secret_module_id=?, secret_field=?, "
                "secret_operation_id=?, status=?, expires_at=? WHERE grant_id=? AND revision=?",
                (*params[1:12], params[12], params[0], expected_revision),
            )
            if cursor.rowcount != 1:
                raise RevisionConflict("grant", expected_revision, current.revision)
            return desired

        return await self.database.executor.run_transaction(
            _worker, begin_mode="IMMEDIATE"
        )

    async def rotate_grant(self, grant: Grant, *, expected_revision: int) -> Grant:
        return await self._cas_grant(grant, expected_revision=expected_revision)

    async def revoke_grant(self, grant: Grant, *, expected_revision: int) -> Grant:
        return await self._cas_grant(
            grant, expected_revision=expected_revision, status=GrantStatus.REVOKED
        )


class SQLiteLoginSessionRepository:
    """SQLite-backed login generation state."""

    def __init__(
        self, database: SQLiteDatabase | str | Path, *, expire_on_open: bool = True
    ) -> None:
        self.database = _database(database)
        # Kept for constructor compatibility; startup cleanup is explicit via
        # expire_pending_on_restart() and is owned by CoreRuntime.
        self.expire_on_open = bool(expire_on_open)

    async def current(self, session_id: str) -> LoginSession | None:
        _bounded(session_id, "session_id")

        def _worker(unit):
            row = unit.execute(
                "SELECT * FROM login_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            session = _session(row)
            if (
                session.status is LoginSessionStatus.PENDING
                and session.expires_at <= datetime.now(UTC)
            ):
                unit.execute(
                    "UPDATE login_sessions SET status='expired' WHERE session_id=? AND status='pending'",
                    (session_id,),
                )
                return LoginSession(
                    session.session_id,
                    session.principal_id,
                    session.module_id,
                    session.generation,
                    LoginSessionStatus.EXPIRED,
                    session.expires_at,
                )
            return session

        return await self.database.executor.run_transaction(
            _worker, begin_mode="DEFERRED"
        )

    async def next_generation(
        self, principal_id: str, module_id: str, *, expected_generation: int
    ) -> LoginSession:
        _bounded(principal_id, "principal_id")
        _module_id(module_id)
        if (
            isinstance(expected_generation, bool)
            or not isinstance(expected_generation, int)
            or expected_generation < 0
        ):
            raise ValueError("expected generation must be non-negative")

        def _worker(unit):
            row = unit.execute(
                "SELECT COALESCE(MAX(generation), 0) AS generation FROM login_sessions WHERE principal_id=? AND module_id=?",
                (principal_id, module_id),
            ).fetchone()
            actual = int(row["generation"])
            if actual != expected_generation:
                raise RevisionConflict(
                    "login_session_generation", expected_generation, actual
                )
            generation = actual + 1
            session_id = f"login_{__import__('secrets').token_urlsafe(18)}"
            expiry = datetime.now(UTC).replace(microsecond=0)
            from datetime import timedelta

            expiry += timedelta(minutes=10)
            unit.execute(
                "UPDATE login_sessions SET status='cancelled' WHERE principal_id=? AND module_id=? AND status='pending'",
                (principal_id, module_id),
            )
            session = LoginSession(
                session_id,
                principal_id,
                module_id,
                generation,
                LoginSessionStatus.PENDING,
                expiry,
            )
            unit.execute(
                "INSERT INTO login_sessions(session_id,principal_id,module_id,generation,status,expires_at) VALUES(?,?,?,?,?,?)",
                (
                    session.session_id,
                    session.principal_id,
                    session.module_id,
                    session.generation,
                    session.status.value,
                    _iso(session.expires_at),
                ),
            )
            return session

        return await self.database.executor.run_transaction(
            _worker, begin_mode="IMMEDIATE"
        )

    async def save(
        self, session: LoginSession, *, expected_generation: int
    ) -> LoginSession:
        session = LoginSession(
            session.session_id,
            session.principal_id,
            session.module_id,
            session.generation,
            session.status,
            session.expires_at,
        )
        if (
            isinstance(expected_generation, bool)
            or not isinstance(expected_generation, int)
            or expected_generation < 0
        ):
            raise ValueError("expected generation must be non-negative")

        def _worker(unit) -> tuple[LoginSession, bool]:
            row = unit.execute(
                "SELECT * FROM login_sessions WHERE session_id=?",
                (session.session_id,),
            ).fetchone()
            if row is None:
                if expected_generation != 0 or session.generation != 1:
                    raise RevisionConflict("login_session", expected_generation, 0)
                unit.execute(
                    "INSERT INTO login_sessions(session_id,principal_id,module_id,generation,status,expires_at) VALUES(?,?,?,?,?,?)",
                    (
                        session.session_id,
                        session.principal_id,
                        session.module_id,
                        session.generation,
                        session.status.value,
                        _iso(session.expires_at),
                    ),
                )
                return session, False
            actual = int(row["generation"])
            if (
                actual != expected_generation
                or session.generation != expected_generation
            ):
                raise RevisionConflict("login_session", expected_generation, actual)
            if (
                row["principal_id"] != session.principal_id
                or row["module_id"] != session.module_id
            ):
                raise ValueError("login session owner cannot change")
            latest = unit.execute(
                "SELECT COALESCE(MAX(generation), 0) AS generation FROM login_sessions "
                "WHERE principal_id=? AND module_id=?",
                (row["principal_id"], row["module_id"]),
            ).fetchone()
            if int(latest["generation"]) != actual:
                raise RevisionConflict(
                    "login_session_generation",
                    expected_generation,
                    int(latest["generation"]),
                )
            current_status = LoginSessionStatus(row["status"])
            persisted_expiry = _datetime(row["expires_at"])
            if (
                current_status is LoginSessionStatus.PENDING
                and persisted_expiry is not None
                and persisted_expiry <= datetime.now(UTC)
            ):
                cursor = unit.execute(
                    "UPDATE login_sessions SET status='expired' WHERE session_id=? "
                    "AND principal_id=? AND module_id=? AND generation=? "
                    "AND status='pending' AND expires_at=?",
                    (
                        session.session_id,
                        row["principal_id"],
                        row["module_id"],
                        actual,
                        row["expires_at"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise RevisionConflict("login_session", expected_generation, actual)
                expired = LoginSession(
                    session.session_id,
                    session.principal_id,
                    session.module_id,
                    session.generation,
                    LoginSessionStatus.EXPIRED,
                    persisted_expiry,
                )
                return expired, True
            if (
                current_status is not LoginSessionStatus.PENDING
                and session.status is not current_status
            ):
                raise RevisionConflict(
                    "login_session_state", expected_generation, actual
                )
            cursor = unit.execute(
                "UPDATE login_sessions SET status=? WHERE session_id=? "
                "AND principal_id=? AND module_id=? AND generation=?",
                (
                    session.status.value,
                    session.session_id,
                    row["principal_id"],
                    row["module_id"],
                    expected_generation,
                ),
            )
            if cursor.rowcount != 1:
                raise RevisionConflict("login_session", expected_generation, actual)
            return session, False

        saved, expired = await self.database.executor.run_transaction(
            _worker, begin_mode="IMMEDIATE"
        )
        if expired:
            raise RevisionConflict(
                "login_session_expired", expected_generation, saved.generation
            )
        return saved

    async def supersede_pending(
        self, principal_id: str, module_id: str, *, generation: int
    ) -> None:
        _bounded(principal_id, "principal_id")
        _module_id(module_id)
        if generation < 1:
            raise ValueError("generation must be positive")

        def _worker(unit):
            unit.execute(
                "UPDATE login_sessions SET status='cancelled' WHERE principal_id=? AND module_id=? AND status='pending' AND generation<>?",
                (principal_id, module_id, generation),
            )

        return await self.database.executor.run_transaction(
            _worker, begin_mode="IMMEDIATE"
        )

    async def expire_pending_on_restart(self) -> int:
        def _worker(unit):
            return unit.execute(
                "UPDATE login_sessions SET status='expired' WHERE status='pending'"
            ).rowcount

        return await self.database.executor.run_transaction(
            _worker, begin_mode="IMMEDIATE"
        )


class SQLiteAuthRepository(SQLiteGrantStore, SQLiteLoginSessionRepository):
    """SQLite implementation of the composite authorization repository."""

    def __init__(
        self, database: SQLiteDatabase | str | Path, *, expire_on_open: bool = True
    ) -> None:
        SQLiteGrantStore.__init__(self, database)
        # Opening a repository never performs SQL. CoreRuntime calls the
        # bounded asynchronous maintenance method during startup.
        self.expire_on_open = bool(expire_on_open)

    async def revoke_grant(self, grant: Grant, *, expected_revision: int) -> Grant:
        """Revoke a grant and fence pending callbacks in the same transaction."""

        def _worker(unit):
            return self._revoke_grant_in_unit(
                unit, grant, expected_revision=expected_revision
            )

        return await self.database.executor.run_transaction(
            _worker, begin_mode="IMMEDIATE"
        )

    async def revoke_grant_with_invalidation(
        self, grant: Grant, *, expected_revision: int
    ) -> Grant:
        """Atomically revoke a Grant and its private runtime state."""

        from .repositories_grant_revocation import SQLiteGrantRevocationRepository

        return await SQLiteGrantRevocationRepository(
            self.database
        ).revoke_grant_with_invalidation(grant, expected_revision=expected_revision)

    async def complete_login(
        self,
        session: LoginSession,
        grant: Grant,
        *,
        expected_generation: int,
        expected_grant_revision: int | None,
    ) -> tuple[LoginSession, Grant]:
        """Complete a current login and create/rotate its grant atomically.

        ``BEGIN IMMEDIATE`` takes SQLite's write reservation before either row
        is read.  The same reservation is used by ``revoke_grant``, so a
        revoke and a completion against one grant observe a strict order even
        when they come through different repository methods or connections.
        """

        session = LoginSession(
            session.session_id,
            session.principal_id,
            session.module_id,
            session.generation,
            session.status,
            session.expires_at,
        )
        grant = Grant(
            grant.grant_id,
            grant.revision,
            grant.principal_id,
            grant.module_id,
            grant.account_id,
            grant.scopes,
            grant.secret_ref,
            grant.status,
            grant.expires_at,
        )
        if (
            isinstance(expected_generation, bool)
            or not isinstance(expected_generation, int)
            or expected_generation < 1
        ):
            raise ValueError("expected generation must be positive")
        if session.generation != expected_generation:
            raise RevisionConflict(
                "login_session_generation", expected_generation, session.generation
            )
        if session.status is not LoginSessionStatus.COMPLETED:
            raise ValueError("completed login session is required")
        if expected_grant_revision is not None:
            self._validate_expected(expected_grant_revision)
            if grant.revision != expected_grant_revision + 1:
                raise RevisionConflict(
                    "grant", expected_grant_revision + 1, grant.revision
                )
        elif grant.revision != 1:
            raise RevisionConflict("grant", 1, grant.revision)
        if grant.status is not GrantStatus.ACTIVE:
            raise ValueError("active grant is required")
        if (grant.principal_id, grant.module_id) != (
            session.principal_id,
            session.module_id,
        ):
            raise ValueError("grant owner does not match login session")

        def _worker(unit):
            # BEGIN IMMEDIATE may wait behind another writer. Validate expiry
            # only after acquiring that reservation so a callback cannot
            # become authorized while blocked on SQLite's write lock.
            now = datetime.now(UTC)
            persisted = unit.execute(
                "SELECT * FROM login_sessions WHERE session_id=?",
                (session.session_id,),
            ).fetchone()
            if persisted is None:
                raise RevisionConflict("login_session", expected_generation, 0)
            actual_generation = int(persisted["generation"])
            if actual_generation != expected_generation:
                raise RevisionConflict(
                    "login_session_generation", expected_generation, actual_generation
                )
            if (
                persisted["principal_id"] != session.principal_id
                or persisted["module_id"] != session.module_id
            ):
                raise ValueError("login session owner cannot change")
            latest = unit.execute(
                "SELECT COALESCE(MAX(generation), 0) AS generation "
                "FROM login_sessions WHERE principal_id=? AND module_id=?",
                (persisted["principal_id"], persisted["module_id"]),
            ).fetchone()
            latest_generation = int(latest["generation"])
            if latest_generation != expected_generation:
                raise RevisionConflict(
                    "login_session_generation", expected_generation, latest_generation
                )
            if (
                LoginSessionStatus(persisted["status"])
                is not LoginSessionStatus.PENDING
            ):
                raise RevisionConflict(
                    "login_session_state", expected_generation, actual_generation
                )
            persisted_expiry = _datetime(persisted["expires_at"])
            if persisted_expiry is None or persisted_expiry <= now:
                raise RevisionConflict(
                    "login_session_expired", expected_generation, actual_generation
                )

            grant_params = self._grant_params(grant)
            if expected_grant_revision is None:
                active = unit.execute(
                    "SELECT grant_id, revision FROM grants WHERE principal_id=? "
                    "AND module_id=? AND status='active' "
                    "AND (expires_at IS NULL OR expires_at>?)",
                    (grant.principal_id, grant.module_id, _iso(now)),
                ).fetchone()
                if active is not None:
                    raise RevisionConflict("grant", 0, int(active["revision"]))
                unit.execute(
                    "INSERT INTO grants (grant_id, revision, principal_id, module_id, account_id, "
                    "scopes_json, secret_token, secret_principal_id, secret_module_id, "
                    "secret_field, secret_operation_id, status, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    grant_params,
                )
            else:
                current_row = unit.execute(
                    "SELECT * FROM grants WHERE grant_id=?", (grant.grant_id,)
                ).fetchone()
                if current_row is None:
                    raise RevisionConflict("grant", expected_grant_revision, 0)
                current = _grant(current_row)
                if current.revision != expected_grant_revision:
                    raise RevisionConflict(
                        "grant", expected_grant_revision, current.revision
                    )
                if (current.principal_id, current.module_id) != (
                    grant.principal_id,
                    grant.module_id,
                ):
                    raise ValueError("grant owner cannot change")
                reauthorizing_revoked = current.status is GrantStatus.REVOKED
                if reauthorizing_revoked and current.account_id != grant.account_id:
                    raise RevisionConflict(
                        "grant_account", expected_grant_revision, current.revision
                    )
                if (
                    current.status is not GrantStatus.ACTIVE
                    and not reauthorizing_revoked
                    or current.status is GrantStatus.ACTIVE
                    and current.expires_at is not None
                    and current.expires_at <= now
                ):
                    raise RevisionConflict(
                        "grant_state", expected_grant_revision, current.revision
                    )
                cursor = unit.execute(
                    "UPDATE grants SET revision=?, principal_id=?, module_id=?, account_id=?, "
                    "scopes_json=?, secret_token=?, secret_principal_id=?, secret_module_id=?, "
                    "secret_field=?, secret_operation_id=?, status=?, expires_at=? "
                    "WHERE grant_id=? AND revision=? AND status=?",
                    (
                        *grant_params[1:],
                        grant_params[0],
                        expected_grant_revision,
                        current.status.value,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RevisionConflict(
                        "grant", expected_grant_revision, current.revision
                    )

            cursor = unit.execute(
                "UPDATE login_sessions SET status='completed' WHERE session_id=? "
                "AND principal_id=? AND module_id=? AND generation=? "
                "AND status='pending' AND expires_at=?",
                (
                    session.session_id,
                    session.principal_id,
                    session.module_id,
                    expected_generation,
                    persisted["expires_at"],
                ),
            )
            if cursor.rowcount != 1:
                raise RevisionConflict(
                    "login_session", expected_generation, actual_generation
                )
            completed_session = LoginSession(
                session.session_id,
                session.principal_id,
                session.module_id,
                expected_generation,
                LoginSessionStatus.COMPLETED,
                persisted_expiry,
            )
            return completed_session, grant

        return await self.database.executor.run_transaction(
            _worker, begin_mode="IMMEDIATE"
        )


GrantRepository = SQLiteGrantStore
LoginRepository = SQLiteLoginSessionRepository
AuthRepository = SQLiteAuthRepository
SQLiteGrantRepository = SQLiteGrantStore
SQLiteLoginRepository = SQLiteLoginSessionRepository
GrantStore = SQLiteGrantStore
LoginSessionRepository = SQLiteLoginSessionRepository

__all__ = [
    "AuthRepository",
    "GrantRepository",
    "GrantStore",
    "LoginRepository",
    "LoginSessionRepository",
    "SQLiteAuthRepository",
    "SQLiteGrantStore",
    "SQLiteGrantRepository",
    "SQLiteLoginRepository",
    "SQLiteLoginSessionRepository",
]
