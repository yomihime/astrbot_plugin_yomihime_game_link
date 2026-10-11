"""SQLite repositories for principals, conversations, identities, and bindings."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from yomihime_game_link_sdk.services import ResolvedIdentity
from yomihime_game_link_sdk.subscriptions import ConversationKind, ConversationRef

from ...core.contracts.services import (
    Binding,
    BindingDefaultSnapshot,
    ConversationKey,
    Principal,
)
from ...core.contracts.validation_boundary import validate_contract
from ...core.ports import (
    ModuleRegistrationLookup,
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
    return SQLiteDatabase(value)


def _row_value(row: sqlite3.Row, key: str) -> Any:
    return row[key]


class _SQLiteRepository:
    def __init__(self, database: SQLiteDatabase | SQLiteUnitOfWorkFactory | str | Path):
        self.database = _database(database)

    @staticmethod
    def _integrity_error(exc: sqlite3.IntegrityError) -> Exception:
        text = str(exc).lower()
        if "unique" in text:
            return UniqueConstraintViolation("identity_binding")
        if "foreign key" in text:
            return ValueError("referenced identity or conversation does not exist")
        return exc


class SQLiteIdentityRepository(_SQLiteRepository):
    """Persist principals and provider identities in the shared SQLite database."""

    async def current_principal(self, principal_id: str) -> Principal | None:
        def _run(unit: SQLiteUnitOfWork) -> Any:
            row = unit.connection.execute(
                "SELECT principal_id, identity_namespace, external_user_id "
                "FROM principals WHERE principal_id = ?",
                (principal_id,),
            ).fetchone()
            if row is None:
                return None
            return Principal(
                _row_value(row, "principal_id"),
                _row_value(row, "identity_namespace"),
                _row_value(row, "external_user_id"),
            )

        return await self.database.executor.run_read(_run)

    async def find_principal(
        self, identity_namespace: str, external_user_id: str
    ) -> Principal | None:
        def _run(unit: SQLiteUnitOfWork) -> Any:
            row = unit.connection.execute(
                "SELECT principal_id, identity_namespace, external_user_id "
                "FROM principals WHERE identity_namespace = ? AND external_user_id = ?",
                (identity_namespace, external_user_id),
            ).fetchone()
            if row is None:
                return None
            return Principal(row[0], row[1], row[2])

        return await self.database.executor.run_read(_run)

    async def save_principal(self, principal: Principal) -> Principal:
        if not isinstance(principal, Principal):
            raise TypeError("principal must be a Principal")

        def _run(unit: SQLiteUnitOfWork) -> Any:
            existing = unit.connection.execute(
                "SELECT principal_id, identity_namespace, external_user_id "
                "FROM principals WHERE principal_id = ?",
                (principal.principal_id,),
            ).fetchone()
            if existing is not None:
                if tuple(existing) != (
                    principal.principal_id,
                    principal.identity_namespace,
                    principal.external_user_id,
                ):
                    raise UniqueConstraintViolation("principal_id")
                return principal
            try:
                unit.execute(
                    "INSERT INTO principals(principal_id, identity_namespace, external_user_id) "
                    "VALUES (?, ?, ?)",
                    (
                        principal.principal_id,
                        principal.identity_namespace,
                        principal.external_user_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise self._integrity_error(exc) from None
            return principal

        return await self.database.executor.run_transaction(
            _run, begin_mode="IMMEDIATE"
        )

    async def current_identity(self, identity_id: str) -> ResolvedIdentity | None:
        def _run(unit: SQLiteUnitOfWork) -> Any:
            row = unit.connection.execute(
                "SELECT identity_id, provider, subject, principal_id "
                "FROM identities WHERE identity_id = ?",
                (identity_id,),
            ).fetchone()
            if row is None:
                return None
            return validate_contract(ResolvedIdentity(row[0], row[1], row[2], row[3]))

        return await self.database.executor.run_read(_run)

    async def save_identity(
        self, principal_id: str, identity: ResolvedIdentity
    ) -> ResolvedIdentity:
        validate_contract(identity)
        if not isinstance(identity, ResolvedIdentity):
            raise TypeError("identity must be a ResolvedIdentity")
        if identity.principal_id not in (None, principal_id):
            raise ValueError("identity principal does not match owner")

        def _run(unit: SQLiteUnitOfWork) -> Any:
            principal = unit.connection.execute(
                "SELECT 1 FROM principals WHERE principal_id = ?", (principal_id,)
            ).fetchone()
            if principal is None:
                raise ValueError("principal does not exist")
            existing = unit.connection.execute(
                "SELECT identity_id, provider, subject, principal_id "
                "FROM identities WHERE identity_id = ?",
                (identity.identity_id,),
            ).fetchone()
            persisted = validate_contract(
                ResolvedIdentity(
                    identity.identity_id,
                    identity.provider,
                    identity.subject,
                    principal_id,
                )
            )
            if existing is not None:
                if tuple(existing) != tuple(
                    (
                        persisted.identity_id,
                        persisted.provider,
                        persisted.subject,
                        principal_id,
                    )
                ):
                    raise UniqueConstraintViolation("identity_id")
                return persisted
            try:
                unit.execute(
                    "INSERT INTO identities(identity_id, principal_id, provider, subject) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        persisted.identity_id,
                        principal_id,
                        persisted.provider,
                        persisted.subject,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise self._integrity_error(exc) from None
            return persisted

        return await self.database.executor.run_transaction(
            _run, begin_mode="IMMEDIATE"
        )


class SQLiteConversationRepository(_SQLiteRepository):
    """Persist adapter conversations and preserve their adapter/session scope."""

    async def current(
        self, adapter_id: str, conversation_id: str
    ) -> ConversationRef | None:
        def _run(unit: SQLiteUnitOfWork) -> Any:
            row = unit.connection.execute(
                "SELECT adapter_id, kind, conversation_id, delivery_route "
                "FROM conversations WHERE adapter_id = ? AND conversation_id = ?",
                (adapter_id, conversation_id),
            ).fetchone()
            if row is None:
                return None
            return validate_contract(
                ConversationRef(
                    row[0], validate_contract(ConversationKind(row[1])), row[2], row[3]
                )
            )

        return await self.database.executor.run_read(_run)

    async def save(self, conversation: ConversationRef) -> ConversationRef:
        validate_contract(conversation)
        if not isinstance(conversation, ConversationRef):
            raise TypeError("conversation must be a ConversationRef")

        def _run(unit: SQLiteUnitOfWork) -> Any:
            existing = unit.connection.execute(
                "SELECT adapter_id, kind, conversation_id, delivery_route "
                "FROM conversations WHERE adapter_id = ? AND conversation_id = ?",
                (conversation.adapter_id, conversation.conversation_id),
            ).fetchone()
            if existing is not None:
                if tuple(existing) != (
                    conversation.adapter_id,
                    conversation.kind.value,
                    conversation.conversation_id,
                    conversation.delivery_route,
                ):
                    raise UniqueConstraintViolation("conversation")
                return conversation
            try:
                unit.execute(
                    "INSERT INTO conversations(adapter_id, kind, conversation_id, delivery_route) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        conversation.adapter_id,
                        conversation.kind.value,
                        conversation.conversation_id,
                        conversation.delivery_route,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise self._integrity_error(exc) from None
            return conversation

        return await self.database.executor.run_transaction(
            _run, begin_mode="IMMEDIATE"
        )


class SQLiteBindingRepository(_SQLiteRepository):
    """Persist conversation-scoped module bindings and an independent default relation."""

    def __init__(
        self,
        database: SQLiteDatabase | SQLiteUnitOfWorkFactory | str | Path,
        module_lookup: ModuleRegistrationLookup,
    ) -> None:
        super().__init__(database)
        require_registered = getattr(module_lookup, "require_registered", None)
        if not callable(require_registered):
            raise TypeError("module_lookup must implement require_registered")
        self.module_lookup = module_lookup

    async def _require_module(self, module_id: str) -> None:
        await self.module_lookup.require_registered(module_id)

    @staticmethod
    def _binding(row: sqlite3.Row, is_default: bool | None = None) -> Binding:
        if is_default is None:
            is_default = bool(row[9])
        return Binding(
            row[0],
            int(row[1]),
            row[2],
            row[3],
            row[5],
            row[6],
            bool(is_default),
            row[7],
            row[8],
            row[4],
        )

    @staticmethod
    def _select_sql(*, include_default_revision: bool = False) -> str:
        default_revision = ", d.revision" if include_default_revision else ""
        return (
            "SELECT b.binding_id, b.revision, b.principal_id, b.module_id, "
            "b.adapter_id, b.object_type, b.object_id, b.origin, b.conversation_id, "
            "CASE WHEN d.binding_id IS NULL THEN 0 ELSE 1 END"
            f"{default_revision} "
            "FROM bindings b LEFT JOIN binding_defaults d ON "
            "d.binding_id = b.binding_id AND d.principal_id = b.principal_id "
            "AND d.module_id = b.module_id AND d.adapter_id = b.adapter_id "
            "AND d.conversation_id = b.conversation_id "
        )

    @staticmethod
    def _ensure_conversation_scope(unit: Any, conversation: ConversationKey) -> None:
        if not isinstance(conversation, ConversationKey):
            raise TypeError("conversation must be a ConversationKey")
        count = unit.connection.execute(
            "SELECT COUNT(*) FROM conversations WHERE adapter_id = ? "
            "AND conversation_id = ?",
            (conversation.adapter_id, conversation.conversation_id),
        ).fetchone()[0]
        if count == 0:
            raise ValueError("conversation does not exist")

    async def current(self, binding_id: str) -> Binding | None:
        def _run(unit: SQLiteUnitOfWork) -> Any:
            row = unit.connection.execute(
                self._select_sql() + "WHERE b.binding_id = ?", (binding_id,)
            ).fetchone()
            return None if row is None else self._binding(row)

        return await self.database.executor.run_read(_run)

    async def list_for(
        self, principal_id: str, module_id: str, conversation: ConversationKey
    ) -> tuple[Binding, ...]:
        def _run(unit: SQLiteUnitOfWork) -> Any:
            self._ensure_conversation_scope(unit, conversation)
            rows = unit.connection.execute(
                self._select_sql() + "WHERE b.principal_id = ? AND b.module_id = ? "
                "AND b.adapter_id = ? AND b.conversation_id = ? ORDER BY b.binding_id",
                (
                    principal_id,
                    module_id,
                    conversation.adapter_id,
                    conversation.conversation_id,
                ),
            ).fetchall()
            return tuple(self._binding(row) for row in rows)

        return await self.database.executor.run_read(_run)

    async def current_default(
        self, principal_id: str, module_id: str, conversation: ConversationKey
    ) -> Binding | None:
        def _run(unit: SQLiteUnitOfWork) -> Any:
            self._ensure_conversation_scope(unit, conversation)
            row = unit.connection.execute(
                self._select_sql() + "WHERE d.principal_id = ? AND d.module_id = ? "
                "AND d.adapter_id = ? AND d.conversation_id = ?",
                (
                    principal_id,
                    module_id,
                    conversation.adapter_id,
                    conversation.conversation_id,
                ),
            ).fetchone()
            return None if row is None else self._binding(row, True)

        return await self.database.executor.run_read(_run)

    async def current_default_snapshot(
        self, principal_id: str, module_id: str, conversation: ConversationKey
    ) -> BindingDefaultSnapshot:
        """Read the default binding and its independent relation revision."""

        def _run(unit: SQLiteUnitOfWork) -> Any:
            self._ensure_conversation_scope(unit, conversation)
            row = unit.connection.execute(
                self._select_sql(include_default_revision=True)
                + "WHERE d.principal_id = ? AND d.module_id = ? "
                "AND d.adapter_id = ? AND d.conversation_id = ?",
                (
                    principal_id,
                    module_id,
                    conversation.adapter_id,
                    conversation.conversation_id,
                ),
            ).fetchone()
            if row is None:
                return BindingDefaultSnapshot(None, 0)
            return BindingDefaultSnapshot(self._binding(row, True), int(row[10]))

        return await self.database.executor.run_read(_run)

    async def save(self, binding: Binding, *, expected_revision: int) -> Binding:
        binding = Binding.validate_for_repository(binding)
        if isinstance(expected_revision, bool) or expected_revision < 0:
            raise ValueError("expected_revision must be non-negative")
        await self._require_module(binding.module_id)

        def _run(unit: SQLiteUnitOfWork) -> Any:
            try:
                existing = unit.connection.execute(
                    "SELECT revision, principal_id, module_id, adapter_id, conversation_id "
                    "FROM bindings WHERE binding_id = ?",
                    (binding.binding_id,),
                ).fetchone()
                if existing is None:
                    if expected_revision != 0 or binding.revision != 1:
                        raise RevisionConflict("binding", expected_revision, 0)
                    self._ensure_conversation_scope(unit, binding.conversation_key)
                    try:
                        unit.execute(
                            "INSERT INTO bindings(binding_id, revision, principal_id, module_id, "
                            "adapter_id, object_type, object_id, origin, conversation_id) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                binding.binding_id,
                                binding.revision,
                                binding.principal_id,
                                binding.module_id,
                                binding.adapter_id,
                                binding.object_type,
                                binding.object_id,
                                binding.origin,
                                binding.conversation_id,
                            ),
                        )
                    except sqlite3.IntegrityError as exc:
                        raise self._integrity_error(exc) from None
                else:
                    actual = int(existing[0])
                    if expected_revision != actual or binding.revision != actual + 1:
                        raise RevisionConflict("binding", expected_revision, actual)
                    if tuple(existing[1:]) != (
                        binding.principal_id,
                        binding.module_id,
                        binding.adapter_id,
                        binding.conversation_id,
                    ):
                        raise ValueError("binding ownership is immutable")
                    unit.compare_and_swap(
                        "UPDATE bindings SET revision = ?, object_type = ?, object_id = ?, "
                        "origin = ? "
                        "WHERE binding_id = ? AND revision = ?",
                        (
                            binding.revision,
                            binding.object_type,
                            binding.object_id,
                            binding.origin,
                            binding.binding_id,
                            expected_revision,
                        ),
                        resource="binding",
                        expected_revision=expected_revision,
                        actual_revision=actual,
                    )
                row = unit.connection.execute(
                    self._select_sql() + "WHERE b.binding_id = ?", (binding.binding_id,)
                ).fetchone()
                if row is None:
                    raise RuntimeError("binding disappeared during save")
                return self._binding(row)
            except sqlite3.IntegrityError as exc:
                raise self._integrity_error(exc) from None

        return await self.database.executor.run_transaction(
            _run, begin_mode="IMMEDIATE"
        )

    async def bind_default(
        self,
        binding: Binding,
        *,
        expected_binding_revision: int,
        expected_default_revision: int,
    ) -> Binding:
        """Atomically persist a binding and make it the scoped default."""

        binding = Binding.validate_for_repository(binding)
        for name, value in (
            ("expected_binding_revision", expected_binding_revision),
            ("expected_default_revision", expected_default_revision),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        await self._require_module(binding.module_id)

        def _run(unit: SQLiteUnitOfWork) -> Any:
            try:
                self._ensure_conversation_scope(unit, binding.conversation_key)
                existing = unit.connection.execute(
                    "SELECT revision, principal_id, module_id, adapter_id, conversation_id "
                    "FROM bindings WHERE binding_id = ?",
                    (binding.binding_id,),
                ).fetchone()
                if existing is None:
                    if expected_binding_revision != 0 or binding.revision != 1:
                        raise RevisionConflict("binding", expected_binding_revision, 0)
                else:
                    actual_binding_revision = int(existing[0])
                    if (
                        expected_binding_revision != actual_binding_revision
                        or binding.revision != actual_binding_revision + 1
                    ):
                        raise RevisionConflict(
                            "binding",
                            expected_binding_revision,
                            actual_binding_revision,
                        )
                    if tuple(existing[1:]) != (
                        binding.principal_id,
                        binding.module_id,
                        binding.adapter_id,
                        binding.conversation_id,
                    ):
                        raise ValueError("binding ownership is immutable")

                current_default = unit.connection.execute(
                    "SELECT revision FROM binding_defaults WHERE principal_id = ? "
                    "AND module_id = ? AND adapter_id = ? AND conversation_id = ?",
                    (
                        binding.principal_id,
                        binding.module_id,
                        binding.adapter_id,
                        binding.conversation_id,
                    ),
                ).fetchone()
                actual_default_revision = (
                    0 if current_default is None else int(current_default[0])
                )
                if expected_default_revision != actual_default_revision:
                    raise RevisionConflict(
                        "binding_default",
                        expected_default_revision,
                        actual_default_revision,
                    )

                if existing is None:
                    try:
                        unit.execute(
                            "INSERT INTO bindings(binding_id, revision, principal_id, module_id, "
                            "adapter_id, object_type, object_id, origin, conversation_id) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                binding.binding_id,
                                binding.revision,
                                binding.principal_id,
                                binding.module_id,
                                binding.adapter_id,
                                binding.object_type,
                                binding.object_id,
                                binding.origin,
                                binding.conversation_id,
                            ),
                        )
                    except sqlite3.IntegrityError as exc:
                        raise self._integrity_error(exc) from None
                else:
                    unit.compare_and_swap(
                        "UPDATE bindings SET revision = ?, object_type = ?, object_id = ?, "
                        "origin = ? WHERE binding_id = ? AND revision = ?",
                        (
                            binding.revision,
                            binding.object_type,
                            binding.object_id,
                            binding.origin,
                            binding.binding_id,
                            expected_binding_revision,
                        ),
                        resource="binding",
                        expected_revision=expected_binding_revision,
                        actual_revision=int(existing[0]),
                    )

                next_default_revision = actual_default_revision + 1
                if current_default is None:
                    unit.execute(
                        "INSERT INTO binding_defaults(principal_id, module_id, adapter_id, "
                        "conversation_id, binding_id, revision) VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            binding.principal_id,
                            binding.module_id,
                            binding.adapter_id,
                            binding.conversation_id,
                            binding.binding_id,
                            next_default_revision,
                        ),
                    )
                else:
                    unit.compare_and_swap(
                        "UPDATE binding_defaults SET binding_id = ?, revision = ? "
                        "WHERE principal_id = ? AND module_id = ? AND adapter_id = ? "
                        "AND conversation_id = ? AND revision = ?",
                        (
                            binding.binding_id,
                            next_default_revision,
                            binding.principal_id,
                            binding.module_id,
                            binding.adapter_id,
                            binding.conversation_id,
                            expected_default_revision,
                        ),
                        resource="binding_default",
                        expected_revision=expected_default_revision,
                        actual_revision=actual_default_revision,
                    )
                row = unit.connection.execute(
                    self._select_sql() + "WHERE b.binding_id = ?", (binding.binding_id,)
                ).fetchone()
                if row is None:
                    raise RuntimeError("binding disappeared during bind_default")
                return self._binding(row, True)
            except sqlite3.IntegrityError as exc:
                raise self._integrity_error(exc) from None

        return await self.database.executor.run_transaction(
            _run, begin_mode="IMMEDIATE"
        )

    async def replace_default(
        self,
        principal_id: str,
        module_id: str,
        conversation: ConversationKey,
        binding_id: str,
        *,
        expected_revision: int,
    ) -> Binding:
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise ValueError("expected_revision must be a non-negative integer")
        await self._require_module(module_id)

        def _run(unit: SQLiteUnitOfWork) -> Any:
            self._ensure_conversation_scope(unit, conversation)
            binding = unit.connection.execute(
                "SELECT binding_id FROM bindings WHERE binding_id = ? AND principal_id = ? "
                "AND module_id = ? AND adapter_id = ? AND conversation_id = ?",
                (
                    binding_id,
                    principal_id,
                    module_id,
                    conversation.adapter_id,
                    conversation.conversation_id,
                ),
            ).fetchone()
            if binding is None:
                raise ValueError("binding is outside the requested scope")
            current = unit.connection.execute(
                "SELECT binding_id, revision FROM binding_defaults WHERE principal_id = ? "
                "AND module_id = ? AND adapter_id = ? AND conversation_id = ?",
                (
                    principal_id,
                    module_id,
                    conversation.adapter_id,
                    conversation.conversation_id,
                ),
            ).fetchone()
            actual = 0 if current is None else int(current[1])
            if expected_revision != actual:
                raise RevisionConflict("binding_default", expected_revision, actual)
            next_revision = 1 if current is None else actual + 1
            if current is None:
                unit.execute(
                    "INSERT INTO binding_defaults(principal_id, module_id, adapter_id, "
                    "conversation_id, binding_id, revision) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        principal_id,
                        module_id,
                        conversation.adapter_id,
                        conversation.conversation_id,
                        binding_id,
                        next_revision,
                    ),
                )
            else:
                unit.compare_and_swap(
                    "UPDATE binding_defaults SET binding_id = ?, revision = ? "
                    "WHERE principal_id = ? AND module_id = ? AND adapter_id = ? "
                    "AND conversation_id = ? "
                    "AND revision = ?",
                    (
                        binding_id,
                        next_revision,
                        principal_id,
                        module_id,
                        conversation.adapter_id,
                        conversation.conversation_id,
                        expected_revision,
                    ),
                    resource="binding_default",
                    expected_revision=expected_revision,
                    actual_revision=actual,
                )
            row = unit.connection.execute(
                self._select_sql() + "WHERE b.binding_id = ?", (binding_id,)
            ).fetchone()
            return self._binding(row, True)

        return await self.database.executor.run_transaction(
            _run, begin_mode="IMMEDIATE"
        )

    async def clear_default(
        self,
        principal_id: str,
        module_id: str,
        conversation: ConversationKey,
        *,
        expected_revision: int,
    ) -> None:
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise ValueError("expected_revision must be a non-negative integer")
        await self._require_module(module_id)

        def _run(unit: SQLiteUnitOfWork) -> Any:
            self._ensure_conversation_scope(unit, conversation)
            current = unit.connection.execute(
                "SELECT revision FROM binding_defaults WHERE principal_id = ? "
                "AND module_id = ? AND adapter_id = ? AND conversation_id = ?",
                (
                    principal_id,
                    module_id,
                    conversation.adapter_id,
                    conversation.conversation_id,
                ),
            ).fetchone()
            actual = 0 if current is None else int(current[0])
            if expected_revision != actual:
                raise RevisionConflict("binding_default", expected_revision, actual)
            if current is not None:
                unit.compare_and_swap(
                    "DELETE FROM binding_defaults WHERE principal_id = ? AND module_id = ? "
                    "AND adapter_id = ? AND conversation_id = ? AND revision = ?",
                    (
                        principal_id,
                        module_id,
                        conversation.adapter_id,
                        conversation.conversation_id,
                        expected_revision,
                    ),
                    resource="binding_default",
                    expected_revision=expected_revision,
                    actual_revision=actual,
                )

        return await self.database.executor.run_transaction(
            _run, begin_mode="IMMEDIATE"
        )

    async def delete(self, binding_id: str, *, expected_revision: int) -> None:
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise ValueError("expected_revision must be a non-negative integer")

        def _run(unit: SQLiteUnitOfWork) -> Any:
            row = unit.connection.execute(
                "SELECT revision FROM bindings WHERE binding_id = ?", (binding_id,)
            ).fetchone()
            actual = 0 if row is None else int(row[0])
            if row is None or expected_revision != actual:
                raise RevisionConflict("binding", expected_revision, actual)
            unit.compare_and_swap(
                "DELETE FROM bindings WHERE binding_id = ? AND revision = ?",
                (binding_id, expected_revision),
                resource="binding",
                expected_revision=expected_revision,
                actual_revision=actual,
            )

        return await self.database.executor.run_transaction(
            _run, begin_mode="IMMEDIATE"
        )


class SQLiteIdentityBindingRepository(
    SQLiteIdentityRepository, SQLiteConversationRepository, SQLiteBindingRepository
):
    """Convenience facade exposing all B03-S2 repositories over one database."""

    def __init__(
        self,
        database: SQLiteDatabase | SQLiteUnitOfWorkFactory | str | Path,
        module_lookup: ModuleRegistrationLookup,
    ) -> None:
        SQLiteBindingRepository.__init__(self, database, module_lookup)


IdentityRepositorySQLite = SQLiteIdentityRepository
ConversationRepositorySQLite = SQLiteConversationRepository
BindingRepositorySQLite = SQLiteBindingRepository
IdentityRepository = SQLiteIdentityRepository
ConversationRepository = SQLiteConversationRepository
BindingRepository = SQLiteBindingRepository


__all__ = [
    "BindingRepositorySQLite",
    "BindingRepository",
    "ConversationRepository",
    "ConversationRepositorySQLite",
    "IdentityRepository",
    "IdentityRepositorySQLite",
    "SQLiteBindingRepository",
    "SQLiteConversationRepository",
    "SQLiteIdentityBindingRepository",
    "SQLiteIdentityRepository",
]
