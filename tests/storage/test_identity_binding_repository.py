"""Real SQLite coverage for the B03-S2 identity and binding repositories."""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.api.services import (
    Binding,
    BindingDefaultSnapshot,
    ConversationKey,
    ConversationKind,
    ConversationRef,
    Principal,
    ResolvedIdentity,
)
from ygl_test_subject.core.ports import (
    ModuleNotRegistered,
    ModuleRegistrationSnapshot,
    RevisionConflict,
    UniqueConstraintViolation,
)
from ygl_test_subject.infrastructure.sqlite.database import (
    SQLiteBusyError,
    SQLiteDatabase,
)
from ygl_test_subject.infrastructure.sqlite.repositories_identity import (
    SQLiteBindingRepository,
    SQLiteConversationRepository,
    SQLiteIdentityRepository,
)


class _ModuleLookup:
    def __init__(self) -> None:
        self.modules = {
            "steam",
            "other-module",
            "module-a",
            "module-b",
        }

    async def require_registered(self, module_id: str) -> ModuleRegistrationSnapshot:
        if module_id not in self.modules:
            raise ModuleNotRegistered(module_id)
        return ModuleRegistrationSnapshot(module_id, True, 1, 1)


class IdentityBindingRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "identity.sqlite3"
        self.database = SQLiteDatabase(self.path)
        self.identities = SQLiteIdentityRepository(self.database)
        self.conversations = SQLiteConversationRepository(self.database)
        self.bindings = SQLiteBindingRepository(self.database, _ModuleLookup())
        await self.identities.save_principal(Principal("p-1", "qq", "1001"))
        await self.identities.save_principal(Principal("p-2", "qq", "1002"))
        await self.conversations.save(
            ConversationRef("adapter-a", ConversationKind.GROUP, "chat-a", "route-a")
        )
        await self.conversations.save(
            ConversationRef("adapter-a", ConversationKind.DIRECT, "chat-b", "route-b")
        )

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    async def _binding(
        self,
        binding_id: str,
        principal_id: str = "p-1",
        conversation_id: str = "chat-a",
        module_id: str = "steam",
        adapter_id: str = "adapter-a",
        revision: int = 1,
    ) -> Binding:
        return Binding(
            binding_id,
            revision,
            principal_id,
            module_id,
            "account",
            binding_id,
            False,
            "user",
            conversation_id,
            adapter_id,
        )

    @staticmethod
    def _key(conversation_id: str, adapter_id: str = "adapter-a") -> ConversationKey:
        return ConversationKey(adapter_id, conversation_id)

    async def test_s2_01_identity_dedup_and_default_replacement(self) -> None:
        identity = ResolvedIdentity("i-1", "steam", "steam-user")
        saved = await self.identities.save_identity("p-1", identity)
        self.assertEqual(saved.principal_id, "p-1")
        self.assertEqual(await self.identities.save_identity("p-1", saved), saved)
        self.assertEqual(
            await self.identities.find_principal("qq", "1001"),
            Principal("p-1", "qq", "1001"),
        )

        first = await self.bindings.save(
            await self._binding("b-1"), expected_revision=0
        )
        second = await self.bindings.save(
            await self._binding("b-2"), expected_revision=0
        )
        selected = await self.bindings.replace_default(
            "p-1", "steam", self._key("chat-a"), first.binding_id, expected_revision=0
        )
        self.assertTrue(selected.is_default)
        selected = await self.bindings.replace_default(
            "p-1", "steam", self._key("chat-a"), second.binding_id, expected_revision=1
        )
        self.assertEqual(selected.binding_id, "b-2")
        self.assertEqual(
            (
                await self.bindings.current_default("p-1", "steam", self._key("chat-a"))
            ).binding_id,
            "b-2",
        )
        self.assertEqual(
            sum(
                item.is_default
                for item in await self.bindings.list_for(
                    "p-1", "steam", self._key("chat-a")
                )
            ),
            1,
        )

    async def test_identity_sql_runs_on_the_database_worker(self) -> None:
        caller_thread = threading.get_ident()
        connection_threads: list[int] = []
        original_connect = SQLiteDatabase._connect

        def track_connect(database: SQLiteDatabase):
            connection_threads.append(threading.get_ident())
            return original_connect(database)

        identity = ResolvedIdentity("i-worker", "steam", "worker-user")
        with patch.object(SQLiteDatabase, "_connect", track_connect):
            self.assertEqual(
                await self.identities.save_identity("p-1", identity),
                ResolvedIdentity("i-worker", "steam", "worker-user", "p-1"),
            )
        self.assertTrue(connection_threads)
        self.assertNotIn(caller_thread, connection_threads)
        self.assertEqual(len(set(connection_threads)), 1)

        connection_threads.clear()
        with patch.object(SQLiteDatabase, "_connect", track_connect):
            self.assertEqual(
                await self.identities.current_identity("i-worker"),
                ResolvedIdentity("i-worker", "steam", "worker-user", "p-1"),
            )
        self.assertTrue(connection_threads)
        self.assertNotIn(caller_thread, connection_threads)
        self.assertEqual(len(set(connection_threads)), 1)

    async def test_s2_02_scope_and_namespace_isolation(self) -> None:
        await self.identities.save_identity(
            "p-1", ResolvedIdentity("i-1", "qq", "same")
        )
        with self.assertRaises(UniqueConstraintViolation):
            await self.identities.save_identity(
                "p-2", ResolvedIdentity("i-2", "qq", "same")
            )
        await self.identities.save_identity(
            "p-2", ResolvedIdentity("i-2", "discord", "same")
        )

        await self.bindings.save(await self._binding("b-1"), expected_revision=0)
        await self.bindings.save(
            await self._binding("b-2", conversation_id="chat-b"), expected_revision=0
        )
        self.assertEqual(
            await self.bindings.list_for("p-2", "steam", self._key("chat-a")), ()
        )
        self.assertEqual(
            len(await self.bindings.list_for("p-1", "steam", self._key("chat-b"))), 1
        )
        with self.assertRaises(ValueError):
            await self.bindings.replace_default(
                "p-2", "steam", self._key("chat-a"), "b-1", expected_revision=0
            )

    async def test_s2_sol_001_binding_owner_is_immutable(self) -> None:
        original = await self.bindings.save(
            await self._binding("b-1"), expected_revision=0
        )
        moved = Binding(
            "b-1",
            2,
            "p-2",
            "other-module",
            "account",
            "new",
            False,
            "user",
            "chat-b",
            "adapter-a",
        )
        with self.assertRaises(ValueError):
            await self.bindings.save(moved, expected_revision=original.revision)
        current = await self.bindings.current("b-1")
        self.assertEqual(current.principal_id, "p-1")
        self.assertEqual(current.module_id, "steam")
        self.assertEqual(current.conversation_id, "chat-a")
        self.assertEqual(current.revision, 1)

    async def test_s2_sol_002_unregistered_module_is_rejected(self) -> None:
        with self.assertRaises(ModuleNotRegistered):
            await self.bindings.save(
                await self._binding("unknown", module_id="not-registered"),
                expected_revision=0,
            )
        self.assertIsNone(await self.bindings.current("unknown"))

    async def test_s2_sol_003_same_conversation_id_is_adapter_scoped(self) -> None:
        same_a = ConversationRef("adapter-a", ConversationKind.GROUP, "same", "route-a")
        same_b = ConversationRef("adapter-b", ConversationKind.GROUP, "same", "route-b")
        await self.conversations.save(same_a)
        await self.conversations.save(same_b)
        self.assertEqual(await self.conversations.current("adapter-a", "same"), same_a)
        self.assertEqual(await self.conversations.current("adapter-b", "same"), same_b)
        await self.bindings.save(
            await self._binding(
                "same-a", conversation_id="same", adapter_id="adapter-a"
            ),
            expected_revision=0,
        )
        await self.bindings.save(
            await self._binding(
                "same-b", conversation_id="same", adapter_id="adapter-b"
            ),
            expected_revision=0,
        )
        self.assertEqual(
            (
                await self.bindings.list_for(
                    "p-1", "steam", self._key("same", "adapter-a")
                )
            )[0].binding_id,
            "same-a",
        )
        self.assertEqual(
            (
                await self.bindings.list_for(
                    "p-1", "steam", self._key("same", "adapter-b")
                )
            )[0].binding_id,
            "same-b",
        )
        await self.bindings.replace_default(
            "p-1",
            "steam",
            self._key("same", "adapter-a"),
            "same-a",
            expected_revision=0,
        )
        await self.bindings.replace_default(
            "p-1",
            "steam",
            self._key("same", "adapter-b"),
            "same-b",
            expected_revision=0,
        )
        self.assertEqual(
            (
                await self.bindings.current_default(
                    "p-1", "steam", self._key("same", "adapter-a")
                )
            ).binding_id,
            "same-a",
        )
        self.assertEqual(
            (
                await self.bindings.current_default(
                    "p-1", "steam", self._key("same", "adapter-b")
                )
            ).binding_id,
            "same-b",
        )
        snapshot_a = await self.bindings.current_default_snapshot(
            "p-1", "steam", self._key("same", "adapter-a")
        )
        snapshot_b = await self.bindings.current_default_snapshot(
            "p-1", "steam", self._key("same", "adapter-b")
        )
        self.assertEqual(
            (snapshot_a.binding.binding_id, snapshot_a.default_revision),
            ("same-a", 1),
        )
        self.assertEqual(
            (snapshot_b.binding.binding_id, snapshot_b.default_revision),
            ("same-b", 1),
        )

    async def test_s2_03_default_revision_has_one_winner(self) -> None:
        await self.bindings.save(await self._binding("b-1"), expected_revision=0)
        await self.bindings.save(await self._binding("b-2"), expected_revision=0)
        first = await self.bindings.replace_default(
            "p-1", "steam", self._key("chat-a"), "b-1", expected_revision=0
        )
        self.assertEqual(first.revision, 1)

        async def stale_replace() -> None:
            await self.bindings.replace_default(
                "p-1", "steam", self._key("chat-a"), "b-2", expected_revision=0
            )

        with self.assertRaises(RevisionConflict):
            await stale_replace()
        self.assertEqual(
            (
                await self.bindings.current_default("p-1", "steam", self._key("chat-a"))
            ).binding_id,
            "b-1",
        )

    async def test_s2_snapshot_reads_independent_default_revision(self) -> None:
        empty = await self.bindings.current_default_snapshot(
            "p-1", "steam", self._key("chat-a")
        )
        self.assertEqual(empty, BindingDefaultSnapshot(None, 0))

        await self.bindings.save(await self._binding("b-1"), expected_revision=0)
        await self.bindings.replace_default(
            "p-1", "steam", self._key("chat-a"), "b-1", expected_revision=0
        )
        snapshot = await self.bindings.current_default_snapshot(
            "p-1", "steam", self._key("chat-a")
        )
        self.assertEqual(snapshot.default_revision, 1)
        self.assertEqual(snapshot.binding.binding_id, "b-1")
        self.assertEqual(snapshot.binding.revision, 1)

        await self.bindings.save(
            await self._binding("b-1", revision=2), expected_revision=1
        )
        snapshot = await self.bindings.current_default_snapshot(
            "p-1", "steam", self._key("chat-a")
        )
        self.assertEqual((snapshot.binding.revision, snapshot.default_revision), (2, 1))

        await self.bindings.save(await self._binding("b-2"), expected_revision=0)
        await self.bindings.replace_default(
            "p-1", "steam", self._key("chat-a"), "b-2", expected_revision=1
        )
        snapshot = await self.bindings.current_default_snapshot(
            "p-1", "steam", self._key("chat-a")
        )
        self.assertEqual(
            (snapshot.binding.binding_id, snapshot.default_revision), ("b-2", 2)
        )

        await self.bindings.clear_default(
            "p-1", "steam", self._key("chat-a"), expected_revision=2
        )
        self.assertEqual(
            await self.bindings.current_default_snapshot(
                "p-1", "steam", self._key("chat-a")
            ),
            BindingDefaultSnapshot(None, 0),
        )

        reopened = SQLiteDatabase(self.path)
        self.assertEqual(
            await SQLiteBindingRepository(
                reopened, _ModuleLookup()
            ).current_default_snapshot("p-1", "steam", self._key("chat-a")),
            BindingDefaultSnapshot(None, 0),
        )

    async def test_s2_04_invalid_external_identity_revision_and_rollback(self) -> None:
        with self.assertRaises(ValueError):
            await self.bindings.save(
                Binding(
                    "legacy",
                    1,
                    "p-1",
                    "steam",
                    "account",
                    "legacy",
                    False,
                    "user",
                    "chat-a",
                ),
                expected_revision=0,
            )
        with self.assertRaises(ValueError):
            await self.identities.save_identity(
                "p-1", ResolvedIdentity("i-x", "", "subject")
            )
        binding = await self.bindings.save(
            await self._binding("b-1"), expected_revision=0
        )
        changed = Binding(
            "b-1",
            2,
            "p-1",
            "steam",
            "account",
            "new",
            False,
            "user",
            "chat-a",
            "adapter-a",
        )
        with self.assertRaises(RevisionConflict):
            await self.bindings.save(changed, expected_revision=0)
        self.assertEqual(
            (await self.bindings.current("b-1")).object_id, binding.object_id
        )
        with self.assertRaises(ValueError):
            await self.bindings.replace_default(
                "p-1", "steam", self._key("chat-a"), "missing", expected_revision=0
            )

    async def test_s2_05_delete_and_reopen_preserve_consistency(self) -> None:
        await self.identities.save_identity(
            "p-1", ResolvedIdentity("i-1", "steam", "one")
        )
        await self.bindings.save(await self._binding("b-1"), expected_revision=0)
        await self.bindings.replace_default(
            "p-1", "steam", self._key("chat-a"), "b-1", expected_revision=0
        )
        await self.bindings.delete("b-1", expected_revision=1)
        self.assertIsNone(
            await self.bindings.current_default("p-1", "steam", self._key("chat-a"))
        )
        self.assertGreaterEqual(self.database.schema_version(), 20)

        reopened = SQLiteDatabase(self.path)
        self.assertEqual(
            await SQLiteIdentityRepository(reopened).current_identity("i-1"),
            ResolvedIdentity("i-1", "steam", "one", "p-1"),
        )
        self.assertIsNone(
            await SQLiteBindingRepository(reopened, _ModuleLookup()).current("b-1")
        )

    async def test_s2_06_bind_default_dual_cas_uniqueness_and_rollback(self) -> None:
        first = await self.bindings.bind_default(
            await self._binding("atomic-1"),
            expected_binding_revision=0,
            expected_default_revision=0,
        )
        self.assertEqual(first.revision, 1)
        self.assertTrue(first.is_default)
        second = await self.bindings.bind_default(
            await self._binding("atomic-2"),
            expected_binding_revision=0,
            expected_default_revision=1,
        )
        self.assertEqual(second.revision, 1)
        self.assertEqual(
            (
                await self.bindings.current_default("p-1", "steam", self._key("chat-a"))
            ).binding_id,
            "atomic-2",
        )

        stale_default = Binding(
            "atomic-2",
            2,
            "p-1",
            "steam",
            "account",
            "changed-default",
            False,
            "user",
            "chat-a",
            "adapter-a",
        )
        with self.assertRaises(RevisionConflict):
            await self.bindings.bind_default(
                stale_default,
                expected_binding_revision=1,
                expected_default_revision=1,
            )
        unchanged = await self.bindings.current("atomic-2")
        self.assertEqual((unchanged.revision, unchanged.object_id), (1, "atomic-2"))

        stale_binding = Binding(
            "atomic-2",
            2,
            "p-1",
            "steam",
            "account",
            "changed-binding",
            False,
            "user",
            "chat-a",
            "adapter-a",
        )
        with self.assertRaises(RevisionConflict):
            await self.bindings.bind_default(
                stale_binding,
                expected_binding_revision=0,
                expected_default_revision=2,
            )
        self.assertEqual(
            (
                await self.bindings.current_default("p-1", "steam", self._key("chat-a"))
            ).binding_id,
            "atomic-2",
        )

        duplicate = Binding(
            "duplicate",
            1,
            "p-1",
            "steam",
            "account",
            "atomic-2",
            False,
            "user",
            "chat-a",
            "adapter-a",
        )
        with self.assertRaises(UniqueConstraintViolation):
            await self.bindings.bind_default(
                duplicate,
                expected_binding_revision=0,
                expected_default_revision=2,
            )
        self.assertIsNone(await self.bindings.current("duplicate"))

        connection = self.database.connect()
        try:
            connection.execute(
                "CREATE TRIGGER fail_binding_default BEFORE UPDATE ON binding_defaults "
                "BEGIN SELECT RAISE(ABORT, 'test default failure'); END"
            )
        finally:
            connection.close()
        failing = Binding(
            "atomic-2",
            2,
            "p-1",
            "steam",
            "account",
            "triggered-rollback",
            False,
            "user",
            "chat-a",
            "adapter-a",
        )
        with self.assertRaises(sqlite3.IntegrityError):
            await self.bindings.bind_default(
                failing,
                expected_binding_revision=1,
                expected_default_revision=2,
            )
        unchanged = await self.bindings.current("atomic-2")
        self.assertEqual((unchanged.revision, unchanged.object_id), (1, "atomic-2"))
        self.assertEqual(
            (
                await self.bindings.current_default("p-1", "steam", self._key("chat-a"))
            ).binding_id,
            "atomic-2",
        )

        reopened = SQLiteDatabase(self.path)
        self.assertEqual(
            (
                await SQLiteBindingRepository(
                    reopened, _ModuleLookup()
                ).current_default("p-1", "steam", self._key("chat-a"))
            ).binding_id,
            "atomic-2",
        )

    async def test_s2_07_bind_default_concurrent_single_winner(self) -> None:
        await self.bindings.save(await self._binding("race-a"), expected_revision=0)
        await self.bindings.save(await self._binding("race-b"), expected_revision=0)
        database_a = SQLiteDatabase(self.path, timeout=0.3)
        database_b = SQLiteDatabase(self.path, timeout=0.3)
        database_a.schema_version()
        database_b.schema_version()
        repo_a = SQLiteBindingRepository(database_a, _ModuleLookup())
        repo_b = SQLiteBindingRepository(database_b, _ModuleLookup())

        async def attempt(
            repository: SQLiteBindingRepository, binding_id: str, delay: float = 0
        ):
            if delay:
                await asyncio.sleep(delay)
            try:
                return await repository.bind_default(
                    await self._binding(binding_id, revision=2),
                    expected_binding_revision=1,
                    expected_default_revision=0,
                )
            except Exception as exc:  # noqa: BLE001 - assert the losing CAS outcome below
                return exc

        results = await asyncio.gather(
            attempt(repo_a, "race-a"), attempt(repo_b, "race-b", 0.05)
        )
        successes = [result for result in results if isinstance(result, Binding)]
        failures = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], (RevisionConflict, SQLiteBusyError))
        current_default = await self.bindings.current_default(
            "p-1", "steam", self._key("chat-a")
        )
        self.assertIn(current_default.binding_id, {"race-a", "race-b"})
        self.assertEqual(current_default.revision, 2)
        winner_id = current_default.binding_id
        for binding_id in ("race-a", "race-b"):
            current = await self.bindings.current(binding_id)
            self.assertEqual(current.revision, 2 if binding_id == winner_id else 1)


if __name__ == "__main__":
    unittest.main()
