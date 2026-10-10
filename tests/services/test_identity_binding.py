"""A1 identity and command binding coverage over real temporary SQLite."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from ygl_test_subject.core.context_issuer import ContextIssuer
from ygl_test_subject.core.contracts.services import ConversationKey, Principal
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.ports import (
    ModuleNotRegistered,
    ModuleRegistrationSnapshot,
    RevisionConflict,
)
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories_identity import (
    SQLiteBindingRepository,
    SQLiteConversationRepository,
    SQLiteIdentityRepository,
)
from ygl_test_subject.services.bindings import (
    AccountOperationsService,
    IdentityBindingError,
    IdentityBindingNotFound,
    IdentityBindingPermissionError,
    ModuleBindingUnavailable,
)
from ygl_test_subject.services.identity import IdentityResolverService

from yomihime_game_link_sdk.contexts import InvocationOrigin
from yomihime_game_link_sdk.services import BindingView, ResolvedIdentity
from yomihime_game_link_sdk.subscriptions import ConversationKind, ConversationRef

MODULE = "package/module"


class _Lookup:
    def __init__(self) -> None:
        self.modules = {MODULE}

    async def require_registered(self, module_id: str) -> ModuleRegistrationSnapshot:
        if module_id not in self.modules:
            raise ModuleNotRegistered(module_id)
        return ModuleRegistrationSnapshot(MODULE, True, 1, 1)


class _SnapshotBarrier:
    def __init__(self) -> None:
        self.count = 0
        self.ready = asyncio.Event()

    async def wait_for_two(self) -> None:
        self.count += 1
        if self.count == 2:
            self.ready.set()
        await self.ready.wait()


class _BarrierBindingRepository:
    def __init__(self, delegate, barrier: _SnapshotBarrier) -> None:
        self.delegate = delegate
        self.barrier = barrier

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)

    async def current_default_snapshot(self, *args, **kwargs):
        value = await self.delegate.current_default_snapshot(*args, **kwargs)
        await self.barrier.wait_for_two()
        return value


class IdentityBindingServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "identity.sqlite3"
        self.database = SQLiteDatabase(self.path)
        self.identities = SQLiteIdentityRepository(self.database)
        self.conversations = SQLiteConversationRepository(self.database)
        self.bindings = SQLiteBindingRepository(self.database, _Lookup())
        await self.identities.save_principal(Principal("p-1", "qq", "u-1"))
        await self.identities.save_principal(Principal("p-2", "qq", "u-2"))
        for adapter in ("adapter-a", "adapter-b"):
            await self.conversations.save(
                validate_contract(
                    ConversationRef(
                        adapter, ConversationKind.GROUP, "same-chat", adapter
                    )
                )
            )
        for identity in (
            validate_contract(ResolvedIdentity("i-1", "steam", "same-name")),
            validate_contract(ResolvedIdentity("i-2", "steam", "second-name")),
        ):
            await self.identities.save_identity("p-1", identity)
        await self.identities.save_identity(
            "p-2", validate_contract(ResolvedIdentity("i-3", "steam-alt", "same-name"))
        )
        self.issuer = ContextIssuer()
        self.resolver = IdentityResolverService(
            self.identities,
            self.conversations,
            self.bindings,
            _Lookup(),
            self.issuer,
            identity_namespace="qq",
        )
        self.accounts = AccountOperationsService(
            self.identities,
            self.conversations,
            self.bindings,
            _Lookup(),
            self.issuer,
            identity_namespace="qq",
        )

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    def _invocation(
        self,
        actor: str = "u-1",
        adapter: str = "adapter-a",
        origin: InvocationOrigin = InvocationOrigin.COMMAND,
        module: str = MODULE,
    ):
        return self.issuer.issue(
            origin=origin,
            module_id=module,
            module_epoch=1,
            registry_revision=1,
            actor_id=actor,
            conversation_id="same-chat",
            adapter_id=adapter,
        )

    async def test_a1_01_bind_default_replace_list_unbind_and_reopen(self) -> None:
        invocation = self._invocation()
        first = await self.accounts.bind(
            invocation, validate_contract(ResolvedIdentity("i-1", "steam", "same-name"))
        )
        self.assertTrue(first.is_default)
        self.assertEqual(
            (await self.resolver.default_identity(invocation)).identity_id, "i-1"
        )

        second = await self.accounts.bind(
            invocation,
            validate_contract(ResolvedIdentity("i-2", "steam", "second-name")),
        )
        self.assertTrue(second.is_default)
        values = await self.accounts.bindings(invocation)
        self.assertEqual({item.identity.identity_id for item in values}, {"i-1", "i-2"})
        await self.accounts.replace_default(invocation, first.binding_id)
        self.assertEqual(
            (await self.resolver.default_identity(invocation)).identity_id, "i-1"
        )
        await self.accounts.unbind(invocation, first.binding_id, expected_revision=1)
        self.assertIsNone(await self.resolver.default_identity(invocation))

        reopened_database = SQLiteDatabase(self.path)
        reopened_resolver = IdentityResolverService(
            SQLiteIdentityRepository(reopened_database),
            SQLiteConversationRepository(reopened_database),
            SQLiteBindingRepository(reopened_database, _Lookup()),
            _Lookup(),
            self.issuer,
            identity_namespace="qq",
        )
        self.assertIsNone(await reopened_resolver.default_identity(invocation))

    async def test_a1_02_adapter_actor_and_origin_are_isolated(self) -> None:
        invocation = self._invocation()
        first = await self.accounts.bind(
            invocation, validate_contract(ResolvedIdentity("i-1", "steam", "same-name"))
        )
        other_adapter = self._invocation(adapter="adapter-b")
        self.assertIsNone(await self.resolver.default_identity(other_adapter))
        self.assertEqual(
            await self.accounts.bindings(self._invocation(actor="u-2")), ()
        )
        with self.assertRaises(IdentityBindingPermissionError):
            await self.accounts.unbind(
                self._invocation(actor="u-2"), first.binding_id, expected_revision=1
            )
        with self.assertRaises(IdentityBindingPermissionError):
            await self.accounts.bindings(
                self._invocation(origin=InvocationOrigin.LLM_TOOL)
            )
        forged = replace(invocation, adapter_id="adapter-b")
        with self.assertRaises(IdentityBindingPermissionError):
            await self.resolver.default_identity(forged)
        await self.accounts.bind(
            other_adapter,
            validate_contract(ResolvedIdentity("i-1", "steam", "same-name")),
        )
        self.assertEqual(
            (await self.resolver.default_identity(other_adapter)).identity_id, "i-1"
        )

    async def test_a1_02_same_display_name_keeps_principal_isolation(self) -> None:
        first_invocation = self._invocation(actor="u-1")
        second_invocation = self._invocation(actor="u-2")
        first = await self.accounts.bind(
            first_invocation,
            validate_contract(ResolvedIdentity("i-1", "steam", "same-name")),
        )
        second = await self.accounts.bind(
            second_invocation,
            validate_contract(ResolvedIdentity("i-3", "steam-alt", "same-name")),
        )
        self.assertEqual(
            (await self.resolver.default_identity(first_invocation)).identity_id,
            "i-1",
        )
        self.assertEqual(
            (await self.resolver.default_identity(second_invocation)).identity_id,
            "i-3",
        )
        self.assertEqual(
            {
                item.identity.identity_id
                for item in await self.accounts.bindings(first_invocation)
            },
            {"i-1"},
        )
        with self.assertRaises(IdentityBindingPermissionError):
            await self.accounts.unbind(
                first_invocation, second.binding_id, expected_revision=1
            )
        self.assertEqual(
            (await self.resolver.default_identity(second_invocation)).identity_id,
            "i-3",
        )
        self.assertNotEqual(first.binding_id, second.binding_id)

    async def test_a1_03_default_cas_conflict_has_no_partial_binding(self) -> None:
        invocation = self._invocation()
        await self.accounts.bind(
            invocation, validate_contract(ResolvedIdentity("i-1", "steam", "same-name"))
        )
        await self.identities.save_identity(
            "p-1", validate_contract(ResolvedIdentity("i-4", "steam", "fourth-name"))
        )
        key = ConversationKey("adapter-a", "same-chat")
        current = await self.bindings.current_default_snapshot("p-1", MODULE, key)
        self.assertEqual(current.default_revision, 1)
        current_binding = await self.bindings.current_default("p-1", MODULE, key)
        with self.assertRaises(RevisionConflict):
            await self.bindings.bind_default(
                replace(
                    current_binding,
                    revision=2,
                    object_id="i-4",
                ),
                expected_binding_revision=1,
                expected_default_revision=0,
            )
        self.assertEqual(
            (await self.resolver.default_identity(invocation)).identity_id, "i-1"
        )

    async def test_a1_04_invalid_identity_module_and_rollback_are_controlled(
        self,
    ) -> None:
        invocation = self._invocation()
        with self.assertRaises(IdentityBindingNotFound):
            await self.accounts.bind(
                invocation, validate_contract(ResolvedIdentity("missing", "steam", "x"))
            )
        with self.assertRaises(ModuleBindingUnavailable):
            await self.accounts.bind(
                self._invocation(module="unknown/module"),
                validate_contract(ResolvedIdentity("i-1", "steam", "same-name")),
            )
        await self.accounts.bind(
            invocation, validate_contract(ResolvedIdentity("i-1", "steam", "same-name"))
        )
        connection = self.database.connect()
        try:
            connection.execute(
                "CREATE TRIGGER fail_a1_default BEFORE UPDATE ON binding_defaults "
                "BEGIN SELECT RAISE(ABORT, 'a1 rollback'); END"
            )
        finally:
            connection.close()
        with self.assertRaises(IdentityBindingError):
            await self.accounts.bind(
                invocation,
                validate_contract(ResolvedIdentity("i-2", "steam", "second-name")),
            )
        self.assertEqual(len(await self.accounts.bindings(invocation)), 1)

    async def test_a1_05_concurrent_default_change_has_one_winner(self) -> None:
        invocation = self._invocation()
        await self.accounts.bind(
            invocation, validate_contract(ResolvedIdentity("i-1", "steam", "same-name"))
        )
        await self.identities.save_identity(
            "p-1", validate_contract(ResolvedIdentity("i-4", "steam", "fourth-name"))
        )
        await self.identities.save_identity(
            "p-1", validate_contract(ResolvedIdentity("i-5", "steam", "fifth-name"))
        )
        barrier = _SnapshotBarrier()
        service_a = AccountOperationsService(
            SQLiteIdentityRepository(SQLiteDatabase(self.path, timeout=0.3)),
            SQLiteConversationRepository(SQLiteDatabase(self.path, timeout=0.3)),
            _BarrierBindingRepository(
                SQLiteBindingRepository(
                    SQLiteDatabase(self.path, timeout=0.3), _Lookup()
                ),
                barrier,
            ),
            _Lookup(),
            self.issuer,
            identity_namespace="qq",
        )
        service_b = AccountOperationsService(
            SQLiteIdentityRepository(SQLiteDatabase(self.path, timeout=0.3)),
            SQLiteConversationRepository(SQLiteDatabase(self.path, timeout=0.3)),
            _BarrierBindingRepository(
                SQLiteBindingRepository(
                    SQLiteDatabase(self.path, timeout=0.3), _Lookup()
                ),
                barrier,
            ),
            _Lookup(),
            self.issuer,
            identity_namespace="qq",
        )
        results = await asyncio.gather(
            service_a.bind(
                invocation,
                validate_contract(ResolvedIdentity("i-4", "steam", "fourth-name")),
            ),
            service_b.bind(
                invocation,
                validate_contract(ResolvedIdentity("i-5", "steam", "fifth-name")),
            ),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(item, BindingView) for item in results), 1)
        self.assertEqual(sum(isinstance(item, Exception) for item in results), 1)


if __name__ == "__main__":
    unittest.main()
