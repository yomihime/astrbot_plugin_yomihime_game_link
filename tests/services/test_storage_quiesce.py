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
from ygl_test_subject.services.core_runtime import CoreRuntime

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

    async def test_identity_mismatch_does_not_quiesce_and_timeout_never_issues_lease(
        self,
    ):
        runtime, generation = self.core.storage_migration_identity
        with self.assertRaises(RuntimeError):
            async with self.core.quiesce_storage_migration(runtime, generation + 1):
                pass
        self.assertTrue(self.core.accepting)

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
