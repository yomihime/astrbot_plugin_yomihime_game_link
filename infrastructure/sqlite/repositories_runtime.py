"""Durable module enable intent and operation journal storage."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from ...api.administration import (
    AdminAuthorizationDenied,
    AdminAuthorizationGrant,
    AdminOperation,
)
from ...api.storage import validate_module_id
from ...core.ports import RevisionConflict
from .database import SQLiteDatabase, SQLiteUnitOfWork
from .repositories_admin_credentials import assert_generation_current


class RuntimeJournalPhase(StrEnum):
    PREPARED = "prepared"
    COMMITTED = "committed"
    APPLIED = "applied"
    COMPENSATED = "compensated"
    RECOVERY_REQUIRED = "recovery_required"


@dataclass(frozen=True, slots=True)
class ModuleRuntimeIntent:
    package_id: str
    module_id: str
    desired_enabled: bool
    intent_revision: int
    operation_id: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RuntimeJournalEntry:
    operation_id: str
    package_id: str
    module_id: str
    kind: str
    phase: RuntimeJournalPhase
    old_enabled: bool
    new_enabled: bool
    old_intent_revision: int
    expected_intent_revision: int
    expected_registry_revision: int
    admin_generation: int | None
    failure_code: str | None
    created_at: datetime
    updated_at: datetime


_FAILURE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_PHASE_TRANSITIONS = {
    RuntimeJournalPhase.PREPARED: {
        RuntimeJournalPhase.COMPENSATED,
        RuntimeJournalPhase.RECOVERY_REQUIRED,
    },
    RuntimeJournalPhase.COMMITTED: {
        RuntimeJournalPhase.APPLIED,
        RuntimeJournalPhase.RECOVERY_REQUIRED,
    },
    RuntimeJournalPhase.RECOVERY_REQUIRED: {
        RuntimeJournalPhase.APPLIED,
        RuntimeJournalPhase.COMPENSATED,
    },
    RuntimeJournalPhase.APPLIED: set(),
    RuntimeJournalPhase.COMPENSATED: set(),
}


def _identifier(value: object, field: str) -> str:
    if (
        type(value) is not str
        or not value.strip()
        or len(value) > 512
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError(f"{field} must be bounded text")
    return value


def _package_id(value: object) -> str:
    return _identifier(value, "package_id")


def _module(value: object) -> str:
    try:
        validate_module_id(value, "module_id")
    except (TypeError, ValueError):
        raise ValueError("module_id is invalid") from None
    return value


def _operation_id(value: object) -> str:
    return _identifier(value, "operation_id")


def _failure_code(value: str | None) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not _FAILURE_CODE.fullmatch(value):
        raise ValueError("failure_code must be a stable code")
    return value


def _revision(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _read_time(value: object) -> datetime:
    try:
        result = datetime.fromisoformat(value)  # type: ignore[arg-type]
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError
        return result.astimezone(UTC)
    except (TypeError, ValueError):
        raise ValueError("stored runtime timestamp is invalid") from None


def _intent(row: sqlite3.Row | None) -> ModuleRuntimeIntent | None:
    if row is None:
        return None
    try:
        enabled = row["desired_enabled"]
        revision = row["intent_revision"]
        if enabled not in (0, 1) or type(revision) is not int or revision < 1:
            raise ValueError
        return ModuleRuntimeIntent(
            _package_id(row["package_id"]),
            _module(row["module_id"]),
            bool(enabled),
            revision,
            _operation_id(row["operation_id"]),
            _read_time(row["updated_at"]),
        )
    except (IndexError, KeyError, TypeError, ValueError):
        raise ValueError("stored runtime intent is invalid") from None


def _journal(row: sqlite3.Row | None) -> RuntimeJournalEntry:
    if row is None:
        raise ValueError("runtime journal entry is unavailable")
    try:
        old_enabled = row["old_enabled"]
        new_enabled = row["new_enabled"]
        if old_enabled not in (0, 1) or new_enabled not in (0, 1):
            raise ValueError
        admin_generation = row["admin_generation"]
        if admin_generation is not None and (
            type(admin_generation) is not int or admin_generation < 1
        ):
            raise ValueError
        return RuntimeJournalEntry(
            _operation_id(row["operation_id"]),
            _package_id(row["package_id"]),
            _module(row["module_id"]),
            row["kind"],
            RuntimeJournalPhase(row["phase"]),
            bool(old_enabled),
            bool(new_enabled),
            _revision(row["old_intent_revision"], "old_intent_revision"),
            _revision(row["expected_intent_revision"], "expected_intent_revision"),
            _revision(row["expected_registry_revision"], "expected_registry_revision"),
            admin_generation,
            _failure_code(row["failure_code"]),
            _read_time(row["created_at"]),
            _read_time(row["updated_at"]),
        )
    except (IndexError, KeyError, TypeError, ValueError):
        raise ValueError("stored runtime journal entry is invalid") from None


class SQLiteModuleRuntimeRepository:
    """Persist enable intent and its crash-recovery journal on the worker."""

    def __init__(self, database: SQLiteDatabase | str | Path) -> None:
        self.database = (
            database
            if isinstance(database, SQLiteDatabase)
            else SQLiteDatabase(database)
        )

    async def current_intent(
        self, package_id: str, module_id: str
    ) -> ModuleRuntimeIntent | None:
        package_id = _package_id(package_id)
        module_id = _module(module_id)

        def read(unit: SQLiteUnitOfWork) -> ModuleRuntimeIntent | None:
            return _intent(
                unit.execute(
                    "SELECT * FROM module_runtime_intents "
                    "WHERE package_id=? AND module_id=?",
                    (package_id, module_id),
                ).fetchone()
            )

        return await self.database.executor.run_read(read)

    async def list_intents(self) -> tuple[ModuleRuntimeIntent, ...]:
        def read(unit: SQLiteUnitOfWork) -> tuple[ModuleRuntimeIntent, ...]:
            rows = unit.execute(
                "SELECT * FROM module_runtime_intents ORDER BY package_id, module_id"
            ).fetchall()
            values = tuple(_intent(row) for row in rows)
            if any(item is None for item in values):
                raise ValueError("stored runtime intent is invalid")
            return values  # type: ignore[return-value]

        return await self.database.executor.run_read(read)

    async def prepare(
        self,
        operation_id: str,
        package_id: str,
        module_id: str,
        desired_enabled: bool,
        *,
        expected_intent_revision: int,
        expected_registry_revision: int,
        grant: AdminAuthorizationGrant | None = None,
    ) -> RuntimeJournalEntry:
        operation_id = _operation_id(operation_id)
        package_id = _package_id(package_id)
        module_id = _module(module_id)
        if type(desired_enabled) is not bool:
            raise TypeError("desired_enabled must be bool")
        expected_intent_revision = _revision(
            expected_intent_revision, "expected_intent_revision"
        )
        expected_registry_revision = _revision(
            expected_registry_revision, "expected_registry_revision"
        )
        if grant is not None and (
            not isinstance(grant, AdminAuthorizationGrant)
            or grant.operation is not AdminOperation.SET_ENABLED
        ):
            raise AdminAuthorizationDenied from None
        generation = None if grant is None else grant.generation
        kind = "enable" if desired_enabled else "disable"
        now = _utc_now()

        def write(unit: SQLiteUnitOfWork) -> RuntimeJournalEntry:
            existing = unit.execute(
                "SELECT * FROM module_runtime_journal WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if existing is not None:
                entry = _journal(existing)
                if (
                    entry.package_id,
                    entry.module_id,
                    entry.kind,
                    entry.expected_intent_revision,
                    entry.expected_registry_revision,
                    entry.admin_generation,
                ) != (
                    package_id,
                    module_id,
                    kind,
                    expected_intent_revision,
                    expected_registry_revision,
                    generation,
                ):
                    raise ValueError("runtime operation id is already in use")
                return entry

            current = _intent(
                unit.execute(
                    "SELECT * FROM module_runtime_intents "
                    "WHERE package_id=? AND module_id=?",
                    (package_id, module_id),
                ).fetchone()
            )
            actual_revision = 0 if current is None else current.intent_revision
            if expected_intent_revision != actual_revision:
                raise RevisionConflict(
                    "runtime_intent", expected_intent_revision, actual_revision
                )
            old_enabled = False if current is None else current.desired_enabled
            unit.execute(
                "INSERT INTO module_runtime_journal "
                "(operation_id,package_id,module_id,kind,phase,old_enabled,new_enabled,"
                "old_intent_revision,expected_intent_revision,expected_registry_revision,"
                "admin_generation,failure_code,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL,?,?)",
                (
                    operation_id,
                    package_id,
                    module_id,
                    kind,
                    RuntimeJournalPhase.PREPARED.value,
                    int(old_enabled),
                    int(desired_enabled),
                    actual_revision,
                    expected_intent_revision,
                    expected_registry_revision,
                    generation,
                    now,
                    now,
                ),
            )
            return _journal(
                unit.execute(
                    "SELECT * FROM module_runtime_journal WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
            )

        return await self.database.executor.run_transaction(
            write, begin_mode="IMMEDIATE"
        )

    async def commit_intent(
        self, operation_id: str, grant: AdminAuthorizationGrant
    ) -> ModuleRuntimeIntent:
        operation_id = _operation_id(operation_id)
        if not isinstance(grant, AdminAuthorizationGrant):
            raise AdminAuthorizationDenied from None

        def commit(unit: SQLiteUnitOfWork) -> ModuleRuntimeIntent:
            journal_row = unit.execute(
                "SELECT * FROM module_runtime_journal WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            journal = _journal(journal_row)
            if (
                journal.phase is not RuntimeJournalPhase.PREPARED
                or journal.admin_generation != grant.generation
            ):
                raise AdminAuthorizationDenied from None
            assert_generation_current(unit, grant, AdminOperation.SET_ENABLED)
            current = _intent(
                unit.execute(
                    "SELECT * FROM module_runtime_intents "
                    "WHERE package_id=? AND module_id=?",
                    (journal.package_id, journal.module_id),
                ).fetchone()
            )
            actual_revision = 0 if current is None else current.intent_revision
            actual_enabled = False if current is None else current.desired_enabled
            if (
                actual_revision != journal.expected_intent_revision
                or actual_revision != journal.old_intent_revision
                or actual_enabled != journal.old_enabled
            ):
                raise RevisionConflict(
                    "runtime_intent", journal.expected_intent_revision, actual_revision
                )
            next_revision = actual_revision + 1
            now = _utc_now()
            if current is None:
                unit.execute(
                    "INSERT INTO module_runtime_intents "
                    "(package_id,module_id,desired_enabled,intent_revision,operation_id,updated_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        journal.package_id,
                        journal.module_id,
                        int(journal.new_enabled),
                        next_revision,
                        operation_id,
                        now,
                    ),
                )
            else:
                cursor = unit.execute(
                    "UPDATE module_runtime_intents SET desired_enabled=?,intent_revision=?, "
                    "operation_id=?,updated_at=? WHERE package_id=? AND module_id=? "
                    "AND intent_revision=?",
                    (
                        int(journal.new_enabled),
                        next_revision,
                        operation_id,
                        now,
                        journal.package_id,
                        journal.module_id,
                        actual_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RevisionConflict(
                        "runtime_intent",
                        journal.expected_intent_revision,
                        actual_revision,
                    )
            cursor = unit.execute(
                "UPDATE module_runtime_journal SET phase='committed',updated_at=? "
                "WHERE operation_id=? AND phase='prepared'",
                (now, operation_id),
            )
            if cursor.rowcount != 1:
                raise RevisionConflict("runtime_journal", 0, 1)
            return _intent(
                unit.execute(
                    "SELECT * FROM module_runtime_intents "
                    "WHERE package_id=? AND module_id=?",
                    (journal.package_id, journal.module_id),
                ).fetchone()
            )  # type: ignore[return-value]

        return await self.database.executor.run_transaction(
            commit, begin_mode="IMMEDIATE"
        )

    async def current_journal(self, operation_id: str) -> RuntimeJournalEntry | None:
        operation_id = _operation_id(operation_id)

        def read(unit: SQLiteUnitOfWork) -> RuntimeJournalEntry | None:
            row = unit.execute(
                "SELECT * FROM module_runtime_journal WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            return None if row is None else _journal(row)

        return await self.database.executor.run_read(read)

    async def list_pending_journal(self) -> tuple[RuntimeJournalEntry, ...]:
        def read(unit: SQLiteUnitOfWork) -> tuple[RuntimeJournalEntry, ...]:
            rows = unit.execute(
                "SELECT * FROM module_runtime_journal "
                "WHERE phase IN ('prepared','committed','recovery_required') "
                "ORDER BY created_at,operation_id"
            ).fetchall()
            return tuple(_journal(row) for row in rows)

        return await self.database.executor.run_read(read)

    async def mark_phase(
        self,
        operation_id: str,
        phase: RuntimeJournalPhase,
        *,
        failure_code: str | None = None,
    ) -> RuntimeJournalEntry:
        operation_id = _operation_id(operation_id)
        if not isinstance(phase, RuntimeJournalPhase):
            raise TypeError("phase must be RuntimeJournalPhase")
        failure_code = _failure_code(failure_code)
        now = _utc_now()

        def update(unit: SQLiteUnitOfWork) -> RuntimeJournalEntry:
            row = unit.execute(
                "SELECT * FROM module_runtime_journal WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            current = _journal(row)
            if current.phase is phase:
                return current
            if phase not in _PHASE_TRANSITIONS[current.phase]:
                raise ValueError("runtime journal phase transition is invalid")
            cursor = unit.execute(
                "UPDATE module_runtime_journal SET phase=?,failure_code=?,updated_at=? "
                "WHERE operation_id=? AND phase=?",
                (phase.value, failure_code, now, operation_id, current.phase.value),
            )
            if cursor.rowcount != 1:
                raise RevisionConflict("runtime_journal", 0, 1)
            return _journal(
                unit.execute(
                    "SELECT * FROM module_runtime_journal WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
            )

        return await self.database.executor.run_transaction(
            update, begin_mode="IMMEDIATE"
        )

    async def compensate(
        self,
        operation_id: str,
        *,
        expected_intent_revision: int,
        grant: AdminAuthorizationGrant,
        failure_code: str | None = None,
    ) -> ModuleRuntimeIntent:
        operation_id = _operation_id(operation_id)
        expected_intent_revision = _revision(
            expected_intent_revision, "expected_intent_revision"
        )
        failure_code = _failure_code(failure_code)
        if not isinstance(grant, AdminAuthorizationGrant):
            raise AdminAuthorizationDenied from None

        def restore(unit: SQLiteUnitOfWork) -> ModuleRuntimeIntent:
            journal = _journal(
                unit.execute(
                    "SELECT * FROM module_runtime_journal WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
            )
            if journal.phase not in {
                RuntimeJournalPhase.COMMITTED,
                RuntimeJournalPhase.RECOVERY_REQUIRED,
            }:
                raise ValueError("runtime operation cannot be compensated")
            assert_generation_current(unit, grant, AdminOperation.SET_ENABLED)
            current = _intent(
                unit.execute(
                    "SELECT * FROM module_runtime_intents "
                    "WHERE package_id=? AND module_id=?",
                    (journal.package_id, journal.module_id),
                ).fetchone()
            )
            actual_revision = 0 if current is None else current.intent_revision
            if (
                actual_revision != expected_intent_revision
                or current is None
                or current.operation_id != operation_id
                or current.desired_enabled != journal.new_enabled
            ):
                raise RevisionConflict(
                    "runtime_intent", expected_intent_revision, actual_revision
                )
            now = _utc_now()
            next_revision = actual_revision + 1
            cursor = unit.execute(
                "UPDATE module_runtime_intents SET desired_enabled=?,intent_revision=?, "
                "updated_at=? WHERE package_id=? AND module_id=? AND intent_revision=? "
                "AND operation_id=?",
                (
                    int(journal.old_enabled),
                    next_revision,
                    now,
                    journal.package_id,
                    journal.module_id,
                    actual_revision,
                    operation_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RevisionConflict(
                    "runtime_intent", expected_intent_revision, actual_revision
                )
            unit.execute(
                "UPDATE module_runtime_journal SET phase='compensated',failure_code=?, "
                "updated_at=? WHERE operation_id=? AND phase=?",
                (failure_code, now, operation_id, journal.phase.value),
            )
            return _intent(
                unit.execute(
                    "SELECT * FROM module_runtime_intents "
                    "WHERE package_id=? AND module_id=?",
                    (journal.package_id, journal.module_id),
                ).fetchone()
            )  # type: ignore[return-value]

        return await self.database.executor.run_transaction(
            restore, begin_mode="IMMEDIATE"
        )


__all__ = [
    "ModuleRuntimeIntent",
    "RuntimeJournalEntry",
    "RuntimeJournalPhase",
    "SQLiteModuleRuntimeRepository",
]
