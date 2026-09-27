"""Dedicated SQLite storage for the independent Core admin verifier."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ...api.administration import (
    AdminAuthorizationDenied,
    AdminAuthorizationGrant,
    AdminOperation,
)
from .database import SQLiteDatabase, SQLiteUnitOfWork


class AdminCredentialStateError(RuntimeError):
    """Credential state is missing, corrupt, or unavailable."""


class AdminCredentialStatus(StrEnum):
    UNINITIALIZED = "UNINITIALIZED"
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


@dataclass(frozen=True, slots=True)
class AdminCredentialState:
    status: AdminCredentialStatus
    generation: int
    verifier_digest: bytes | None


def _state(row: sqlite3.Row | None) -> AdminCredentialState:
    if row is None:
        raise AdminCredentialStateError("admin credential state is unavailable")
    try:
        status = AdminCredentialStatus(row["state"])
        generation = row["generation"]
        digest = row["verifier_digest"]
        if type(generation) is not int or generation < 0:
            raise ValueError
        if digest is not None and not isinstance(digest, bytes):
            raise ValueError
        if status is AdminCredentialStatus.UNINITIALIZED:
            valid = generation == 0 and digest is None
        elif status is AdminCredentialStatus.ACTIVE:
            valid = generation >= 1 and isinstance(digest, bytes) and len(digest) == 32
        else:
            valid = generation >= 1 and digest is None
        if not valid:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise AdminCredentialStateError("admin credential state is corrupt") from None
    return AdminCredentialState(status, generation, digest)


class SQLiteAdminCredentialRepository:
    """Atomically transitions the single verifier row using IMMEDIATE locks."""

    def __init__(self, database: SQLiteDatabase | str | Path) -> None:
        self.database = (
            database
            if isinstance(database, SQLiteDatabase)
            else SQLiteDatabase(database)
        )

    def _require_existing_database(self) -> None:
        # Do not let an authorization/maintenance call recreate a deleted DB;
        # the normal host startup migration owns first-time schema creation.
        if not self.database.path.is_file():
            raise AdminCredentialStateError("admin credential storage is unavailable")

    async def current(self) -> AdminCredentialState:
        try:
            self._require_existing_database()

            def read(unit: SQLiteUnitOfWork) -> AdminCredentialState:
                rows = unit.execute(
                    "SELECT singleton, state, generation, verifier_digest "
                    "FROM admin_credentials"
                ).fetchall()
                if len(rows) != 1 or rows[0]["singleton"] != 1:
                    raise AdminCredentialStateError(
                        "admin credential state is unavailable"
                    )
                return _state(rows[0])

            return await self.database.executor.run_read(read)
        except AdminCredentialStateError:
            raise
        except Exception:
            raise AdminCredentialStateError(
                "admin credential storage is unavailable"
            ) from None

    @staticmethod
    def _digest(value: bytes) -> None:
        if type(value) is not bytes or len(value) != 32:
            raise ValueError("verifier digest must be 32 bytes")

    async def _transition(
        self,
        *,
        expected: AdminCredentialStatus,
        generation: int,
        status: AdminCredentialStatus,
        digest: bytes | None,
    ) -> AdminCredentialState:
        if type(generation) is not int or generation < 0:
            raise ValueError("generation must be non-negative")
        if digest is not None:
            self._digest(digest)
        try:
            self._require_existing_database()

            def transition(unit: SQLiteUnitOfWork) -> AdminCredentialState:
                rows = unit.execute(
                    "SELECT singleton, state, generation, verifier_digest "
                    "FROM admin_credentials"
                ).fetchall()
                if len(rows) != 1 or rows[0]["singleton"] != 1:
                    raise AdminCredentialStateError(
                        "admin credential state is unavailable"
                    )
                current = _state(rows[0])
                if current.status is not expected or current.generation != generation:
                    raise AdminCredentialStateError(
                        "admin credential lifecycle conflict"
                    )
                next_generation = generation + 1
                cursor = unit.execute(
                    "UPDATE admin_credentials SET state=?, generation=?, "
                    "verifier_digest=? WHERE singleton=1 AND state=? AND generation=?",
                    (status.value, next_generation, digest, expected.value, generation),
                )
                if cursor.rowcount != 1:
                    raise AdminCredentialStateError(
                        "admin credential lifecycle conflict"
                    )
                updated = unit.execute(
                    "SELECT singleton, state, generation, verifier_digest "
                    "FROM admin_credentials"
                ).fetchone()
                return _state(updated)

            return await self.database.executor.run_transaction(
                transition, begin_mode="IMMEDIATE"
            )
        except AdminCredentialStateError:
            raise
        except Exception:
            raise AdminCredentialStateError(
                "admin credential storage is unavailable"
            ) from None

    async def bootstrap(self, digest: bytes) -> AdminCredentialState:
        return await self._transition(
            expected=AdminCredentialStatus.UNINITIALIZED,
            generation=0,
            status=AdminCredentialStatus.ACTIVE,
            digest=digest,
        )

    async def rotate(self, generation: int, digest: bytes) -> AdminCredentialState:
        return await self._transition(
            expected=AdminCredentialStatus.ACTIVE,
            generation=generation,
            status=AdminCredentialStatus.ACTIVE,
            digest=digest,
        )

    async def revoke(self, generation: int) -> AdminCredentialState:
        return await self._transition(
            expected=AdminCredentialStatus.ACTIVE,
            generation=generation,
            status=AdminCredentialStatus.REVOKED,
            digest=None,
        )

    async def recover(self, generation: int, digest: bytes) -> AdminCredentialState:
        return await self._transition(
            expected=AdminCredentialStatus.REVOKED,
            generation=generation,
            status=AdminCredentialStatus.ACTIVE,
            digest=digest,
        )


__all__ = [
    "AdminCredentialState",
    "AdminCredentialStateError",
    "AdminCredentialStatus",
    "SQLiteAdminCredentialRepository",
]


def assert_generation_current(
    unit: SQLiteUnitOfWork,
    grant: AdminAuthorizationGrant,
    operation: AdminOperation,
) -> None:
    """Revalidate an admin grant inside the protected SQLite transaction."""

    if (
        not isinstance(grant, AdminAuthorizationGrant)
        or grant.operation is not operation
    ):
        raise AdminAuthorizationDenied from None
    try:
        row = unit.execute(
            "SELECT state, generation, verifier_digest FROM admin_credentials "
            "WHERE singleton=1"
        ).fetchone()
        if (
            row is None
            or row["state"] != AdminCredentialStatus.ACTIVE.value
            or type(row["generation"]) is not int
            or row["generation"] != grant.generation
            or type(row["verifier_digest"]) is not bytes
            or len(row["verifier_digest"]) != 32
        ):
            raise AdminAuthorizationDenied
    except AdminAuthorizationDenied:
        raise
    except Exception:
        raise AdminAuthorizationDenied from None
