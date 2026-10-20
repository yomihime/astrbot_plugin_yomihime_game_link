"""Real second encrypt-then-wait process, exact handle exit and public migration."""

import asyncio
import base64
import os
import pickle
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.infrastructure.managed_key_manager import ManagedKeyManager
from ygl_test_subject.infrastructure.private_storage import (
    PrivateStorageError,
    resolve_private_storage,
)
from ygl_test_subject.infrastructure.private_storage_migration import (
    execute_storage_migration,
    plan_storage_migration,
)
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.windows.private_storage import (
    WindowsPrivateStorageGuard,
)
from ygl_test_subject.services.storage_migration_supervisor import (
    OfflineSourceLease,
    SyntheticSourceSupervisor,
    deployment_supervisor_for_existing_source,
)

from tests.storage import test_managed_key_manager as key_fixture
from tests.storage import test_private_storage as native_fixture


@unittest.skipUnless(os.name == "nt", "native Windows private migration fixture")
class StorageSupervisorTests(unittest.TestCase):
    def setUp(self):
        native_fixture.WindowsPrivateStorageTests.setUp(self)
        self.supervisor = SyntheticSourceSupervisor.create_synthetic(self.owned)
        self.original = self.supervisor.source_root
        self.addCleanup(lambda: asyncio.run(self.supervisor.close()))

    def seed(self):
        database = SQLiteDatabase(self.original / "runtime.sqlite3")
        database.initialize()
        key_fixture.ManagedKeyTests._insert_secret(
            SimpleNamespace(root=self.original, database=database), bytes(range(32))
        )
        manager = ManagedKeyManager(database, self.original, self.original / "secrets")

        async def initialize():
            try:
                status = await manager.startup()
                await manager.adopt_environment(
                    expected_revision=status["revision"],
                    expected_generation=status["generation"],
                    inventory_digest=status["inventory_digest"],
                )
            finally:
                await database.executor.close()

        with patch.dict(
            os.environ, {"YGL_SECRET_KEY": base64.b64encode(bytes(range(32))).decode()}
        ):
            asyncio.run(initialize())
        return resolve_private_storage(self.original, private_base=self.owned)

    def test_live_old_encrypt_worker_denies_then_exact_exit_allows_public_execute(self):
        layout = self.seed()
        plan = plan_storage_migration(layout)
        original_blob = (layout.secret_root / "secret_synthetic_01.blob").read_bytes()

        async def scenario():
            writer = await self.supervisor.start_delayed_writer()
            with self.assertRaises(PrivateStorageError):
                async with self.supervisor.acquire_offline_source(plan):
                    self.fail("live old writer must deny")
            # A runtime quiesce-shaped object is never an offline proof.
            with self.assertRaises(PrivateStorageError):
                execute_storage_migration(
                    plan,
                    source_proof=SimpleNamespace(assert_current=lambda *_: None),
                    private_guard=None,
                )
            self.assertFalse((layout.secret_root / "synthetic_delayed.blob").exists())
            await writer.stop_normally()
            self.assertTrue(writer.exited())
            async with self.supervisor.acquire_offline_source(plan) as proof:
                self.assertIs(type(proof), OfflineSourceLease)
                proof.assert_current(plan)
                with self.assertRaises(TypeError):
                    pickle.dumps(proof)
                with self.assertRaises(PrivateStorageError):
                    await self.supervisor.start_delayed_writer()
                with WindowsPrivateStorageGuard(layout.instance_root) as guard:
                    receipt = execute_storage_migration(
                        plan, source_proof=proof, private_guard=guard
                    )
                self.assertEqual(receipt.state, "committed")
            with self.assertRaises(PrivateStorageError):
                proof.assert_current(plan)
            with self.assertRaises(PrivateStorageError):
                await self.supervisor.start_delayed_writer()

        asyncio.run(scenario())
        selected = resolve_private_storage(self.original, private_base=self.owned)
        self.assertEqual(
            (selected.secret_root / "secret_synthetic_01.blob").read_bytes(),
            original_blob,
        )
        db = SQLiteDatabase(selected.database_path)
        manager = ManagedKeyManager(db, selected.key_data_root, selected.secret_root)

        async def cold():
            try:
                self.assertTrue((await manager.startup())["ready"])
            finally:
                await db.executor.close()

        asyncio.run(cold())

    def test_unknown_consumer_generation_change_and_missing_real_adapter_deny(self):
        layout = self.seed()
        plan = plan_storage_migration(layout)
        with self.assertRaisesRegex(
            PrivateStorageError, "offline_supervisor_unavailable"
        ):
            deployment_supervisor_for_existing_source(layout)
        with self.assertRaises(PrivateStorageError):
            OfflineSourceLease(None, plan)

        async def scenario():
            async with self.supervisor.acquire_offline_source(plan) as proof:
                self.supervisor.mark_coverage_unknown()
                with self.assertRaises(PrivateStorageError):
                    proof.assert_current(plan)

        asyncio.run(scenario())

    def test_real_abnormal_exit_caught_stop_error_never_mints_lease(self):
        plan = plan_storage_migration(self.seed())

        async def scenario():
            writer = await self.supervisor.start_delayed_writer()
            writer.process.stdin.write(b"x")
            writer.process.stdin.flush()
            self.assertEqual(await asyncio.to_thread(writer.process.wait), 2)
            with self.assertRaisesRegex(PrivateStorageError, "source_consumer_failed"):
                await writer.stop_normally()
            with self.assertRaises(PrivateStorageError):
                async with self.supervisor.acquire_offline_source(plan):
                    self.fail("caught failed stop must not restore authority")
            self.assertFalse(self.supervisor._coverage_known)

        asyncio.run(scenario())

    def test_exit_zero_without_supervisor_stop_receipt_is_insufficient(self):
        plan = plan_storage_migration(self.seed())

        async def scenario():
            writer = await self.supervisor.start_delayed_writer()
            # Bypassing the supervisor-owned stop method cannot create proof,
            # even with the same q byte and real successful child returncode.
            writer.process.stdin.write(b"q")
            writer.process.stdin.flush()
            self.assertEqual(await asyncio.to_thread(writer.process.wait), 0)
            with self.assertRaises(PrivateStorageError):
                async with self.supervisor.acquire_offline_source(plan):
                    self.fail("poll exit0 must not stand in for stop evidence")
            with self.assertRaises(PrivateStorageError):
                await writer.stop_normally()

        asyncio.run(scenario())

    def test_cancelled_stop_cannot_issue_after_child_eventually_exits_zero(self):
        plan = plan_storage_migration(self.seed())

        async def scenario():
            writer = await self.supervisor.start_delayed_writer()
            stop = asyncio.create_task(writer.stop_normally())
            await asyncio.sleep(0)
            self.assertTrue(writer._stopping)
            stop.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await stop
            self.assertEqual(await asyncio.to_thread(writer.process.wait), 0)
            with self.assertRaises(PrivateStorageError):
                async with self.supervisor.acquire_offline_source(plan):
                    self.fail("cancelled confirmation must remain poisoned")

        asyncio.run(scenario())

    def test_stop_io_failure_permanently_denies_even_after_later_cleanup(self):
        plan = plan_storage_migration(self.seed())

        async def scenario():
            writer = await self.supervisor.start_delayed_writer()
            with patch.object(
                writer.process.stdin,
                "flush",
                side_effect=OSError("synthetic unknown stop"),
            ):
                with self.assertRaises(OSError):
                    await writer.stop_normally()
            # Drain our buffered q only for resource cleanup. Even the real
            # protocol/exit0 cannot retroactively repair the failed attempt.
            writer.process.stdin.flush()
            await asyncio.to_thread(writer.process.wait)
            self.assertEqual(writer.process.returncode, 0)
            with self.assertRaises(PrivateStorageError):
                async with self.supervisor.acquire_offline_source(plan):
                    self.fail("later exit cannot repair a failed stop attempt")

        asyncio.run(scenario())

    def test_fresh_handle_identity_failure_revokes_a_current_lease(self):
        plan = plan_storage_migration(self.seed())

        async def scenario():
            writer = await self.supervisor.start_delayed_writer()
            await writer.stop_normally()
            async with self.supervisor.acquire_offline_source(plan) as proof:
                with patch.object(
                    writer,
                    "assert_identity",
                    side_effect=PrivateStorageError("source_consumer_identity_unknown"),
                ):
                    with self.assertRaises(PrivateStorageError):
                        proof.assert_current(plan)
                with self.assertRaises(PrivateStorageError):
                    proof.assert_current(plan)

        asyncio.run(scenario())
