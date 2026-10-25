"""Own-runtime drain excludes the exact admin requester, never external writers."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.executor import SQLiteExecutorClosedError
from ygl_test_subject.services.admin_operations import _tracked_admin_request
from ygl_test_subject.services.core_runtime import (
    CoreRuntime,
    CoreRuntimeCleanupPending,
)

from yomihime_game_link_sdk import DisplayLimits


class StorageQuiesceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="quiesce-", dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "extensions").mkdir()
        self.core = CoreRuntime(
            database=SQLiteDatabase(self.root / "runtime.sqlite3"),
            extension_root=self.root / "extensions",
            file_root=self.root / "files",
            secret_root=self.root / "secrets",
            secret_codec=None,
            http_transport=lambda request: request,
            renderer=SimpleNamespace(render=lambda *args: None),
            display_limits=DisplayLimits(2, 4096),
            message_port=SimpleNamespace(send=lambda *args: None),
            admin_context_validator=lambda *_: False,
            host_ingress_validator=lambda *_: False,
            config_principal_id="synthetic",
            identity_namespace="synthetic",
            cleanup_timeout=1,
        )
        await self.core.start()

    async def asyncTearDown(self):
        await self.core.close(timeout=2)
        self.temp.cleanup()

    async def test_exact_admin_requester_is_not_cancelled_and_lease_expires(self):
        core = self.core

        @_tracked_admin_request
        async def migration(ops):
            identity = core.storage_migration_identity
            async with core.quiesce_storage_migration(*identity) as lease:
                lease.assert_current()
                self.assertEqual(asyncio.current_task().cancelling(), 0)
                self.assertFalse(core.accepting)
                with self.assertRaises(SQLiteExecutorClosedError):
                    await core.database.executor.run_read(lambda unit: 1)
            with self.assertRaises(RuntimeError):
                lease.assert_current()

        await migration(core.admin_operations)

    async def test_successful_quiesce_then_close_submits_no_additional_SQL(self):
        core = self.core
        async with core.quiesce_storage_migration(*core.storage_migration_identity):
            self.assertTrue(core.jobs._closed)
            self.assertTrue(core.jobs._close_recovered)
            self.assertTrue(core._jobs_cleanup_task.result())
            self.assertEqual(core.database.executor.state, "CLOSED")
        attempts = []
        original = type(core.database.executor)._submit

        async def observe(executor, *args, **kwargs):
            attempts.append(args[0])
            # Observe attempts while retaining the real CLOSED-executor guard.
            return await original(executor, *args, **kwargs)

        with patch.object(type(core.database.executor), "_submit", observe):
            self.assertTrue(await core.close(timeout=1))
        self.assertEqual(attempts, [])

    async def test_accepted_jobs_SQL_blocks_lease_without_closing_executor(self):
        def awaiting(task):
            cursor, names = task.get_coro(), []
            while cursor is not None:
                code = getattr(cursor, "cr_code", None)
                if code is not None:
                    names.append(code.co_name)
                cursor = getattr(cursor, "cr_await", None)
            return names

        async def until(predicate):
            async with asyncio.timeout(1):
                while not predicate():
                    await asyncio.sleep(0.001)

        core, locker = self.core, None
        try:
            await until(
                lambda: "sleep" in awaiting(core._pump_task)
                and "wait" in awaiting(core.jobs._driver)
                and core.database.executor._outstanding == 0
            )
            locker = core.database.connect()
            locker.execute("BEGIN IMMEDIATE")
            core.jobs.notify_changed()
            await until(
                lambda: "expire" in awaiting(core.jobs._driver)
                and core.database.executor._outstanding > 0
            )
            # The existing outer quiesce deadline and the Jobs observer share
            # this bound; either timeout form must retain the exact cleanup.
            with self.assertRaises((CoreRuntimeCleanupPending, TimeoutError)):
                async with core.quiesce_storage_migration(
                    *core.storage_migration_identity, timeout=0.02
                ):
                    self.fail("Accepted SQL must drain before any lease")
            self.assertIsNone(core._storage_lease)
            self.assertTrue(core.cleanup_pending)
            self.assertTrue(core.jobs._closed)
            cleanup = core._jobs_cleanup_task
            self.assertIsNotNone(cleanup)
            self.assertFalse(cleanup.done())
            self.assertEqual(core.database.executor.state, "OPEN")
            self.assertGreater(core.database.executor._outstanding, 0)
            with self.assertRaises(CoreRuntimeCleanupPending) as caught:
                await core.close(timeout=0.02)
            self.assertEqual(caught.exception.component, "durable_jobs")
            self.assertIs(core._jobs_cleanup_task, cleanup)
            self.assertEqual(cleanup.cancelling(), 0)
            self.assertEqual(core.database.executor.state, "OPEN")
        finally:
            if locker is not None:
                locker.rollback()
                locker.close()
            self.assertTrue(await core.close(timeout=1))

    async def test_jobs_recovery_error_refuses_lease_and_preserves_executor(self):
        core = self.core
        original = core.jobs.repository.recover

        async def fail_after_real_SQL(now):
            await original(now)
            raise RuntimeError("synthetic post-SQL recovery failure")

        core.jobs.repository.recover = fail_after_real_SQL
        try:
            with self.assertRaises(CoreRuntimeCleanupPending) as caught:
                async with core.quiesce_storage_migration(
                    *core.storage_migration_identity
                ):
                    self.fail("Recovery error must not issue a lease")
            self.assertEqual(caught.exception.component, "durable_jobs")
            self.assertIsInstance(caught.exception.__cause__, RuntimeError)
            self.assertIsNone(core._storage_lease)
            self.assertTrue(core.cleanup_pending)
            self.assertEqual(core.database.executor.state, "OPEN")
            self.assertFalse(core.jobs._close_recovered)
        finally:
            core.jobs.repository.recover = original
            self.assertTrue(await core.close(timeout=1))

    async def test_identity_mismatch_does_not_quiesce_and_timeout_never_issues_lease(
        self,
    ):
        runtime, generation = self.core.storage_migration_identity
        with self.assertRaises(RuntimeError):
            async with self.core.quiesce_storage_migration(runtime, generation + 1):
                pass
        self.assertTrue(self.core.accepting)
        self.assertFalse(self.core.jobs._closed)

        async def blocked(*_):
            await asyncio.Event().wait()

        with patch.object(
            type(self.core.module_services), "close_credentials", blocked
        ):
            with self.assertRaises(TimeoutError):
                async with self.core.quiesce_storage_migration(
                    runtime, generation, timeout=0.02
                ):
                    self.fail("lease must not issue after timeout")
        self.assertIsNone(self.core._storage_lease)
        self.assertFalse(self.core.accepting)
        self.assertTrue(self.core.cleanup_pending)
