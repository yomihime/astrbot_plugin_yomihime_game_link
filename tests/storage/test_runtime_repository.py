from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ygl_test_subject.api.administration import (
    AdminAuthorizationDenied,
    AdminAuthorizationGrant,
    AdminOperation,
)
from ygl_test_subject.api.manifests import ConfigField
from ygl_test_subject.api.services import (
    ConfigFieldUpdate,
    ConfigPatchMode,
    ConfigTarget,
    PersistedConfigPatch,
)
from ygl_test_subject.api.storage import (
    SecretReceipt,
    SecretRef,
    SecretTarget,
)
from ygl_test_subject.core.ports import RevisionConflict
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories_admin_credentials import (
    SQLiteAdminCredentialRepository,
)
from ygl_test_subject.infrastructure.sqlite.repositories_config import (
    SQLiteConfigRepository,
)
from ygl_test_subject.infrastructure.sqlite.repositories_runtime import (
    RuntimeJournalPhase,
    SQLiteModuleRuntimeRepository,
)


class RuntimeRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "runtime.sqlite3"
        self.database = SQLiteDatabase(self.path)
        self.repository = SQLiteModuleRuntimeRepository(self.database)
        self.credentials = SQLiteAdminCredentialRepository(self.database)

    def tearDown(self) -> None:
        self.temp.cleanup()

    async def _grant(self) -> AdminAuthorizationGrant:
        await self.database.executor.initialize()
        await self.credentials.bootstrap(bytes(range(32)))
        return AdminAuthorizationGrant(AdminOperation.SET_ENABLED, 1)

    async def test_intent_and_journal_commit_then_reopen(self) -> None:
        self.assertFalse(self.path.exists())
        self.assertIsNone(
            await self.repository.current_intent("sample", "sample/module")
        )
        self.assertTrue(self.path.exists())
        grant = await self._grant()
        prepared = await self.repository.prepare(
            "operation-enable",
            "sample",
            "sample/module",
            True,
            expected_intent_revision=0,
            expected_registry_revision=7,
            grant=grant,
        )
        self.assertEqual(prepared.phase, RuntimeJournalPhase.PREPARED)
        self.assertEqual(prepared.expected_registry_revision, 7)
        self.assertIsNone(
            await self.repository.current_intent("sample", "sample/module")
        )

        enabled = await self.repository.commit_intent("operation-enable", grant)
        self.assertTrue(enabled.desired_enabled)
        self.assertEqual(enabled.intent_revision, 1)
        self.assertEqual(enabled.operation_id, "operation-enable")
        self.assertEqual(
            (await self.repository.current_journal("operation-enable")).phase,
            RuntimeJournalPhase.COMMITTED,
        )
        self.assertEqual(
            await self.repository.mark_phase(
                "operation-enable", RuntimeJournalPhase.APPLIED
            ),
            await self.repository.current_journal("operation-enable"),
        )

        reopened = SQLiteModuleRuntimeRepository(SQLiteDatabase(self.path))
        restored = await reopened.current_intent("sample", "sample/module")
        self.assertIsNotNone(restored)
        self.assertTrue(restored.desired_enabled)
        self.assertEqual(restored.intent_revision, 1)
        disabled = await reopened.prepare(
            "operation-disable",
            "sample",
            "sample/module",
            False,
            expected_intent_revision=1,
            expected_registry_revision=8,
            grant=grant,
        )
        self.assertEqual(disabled.old_intent_revision, 1)
        result = await reopened.commit_intent("operation-disable", grant)
        self.assertFalse(result.desired_enabled)
        self.assertEqual(result.intent_revision, 2)

    async def test_commit_checks_live_generation_and_intent_cas_atomically(
        self,
    ) -> None:
        grant = await self._grant()
        first = await self.repository.prepare(
            "operation-one",
            "sample",
            "sample/module",
            True,
            expected_intent_revision=0,
            expected_registry_revision=2,
            grant=grant,
        )
        self.assertEqual(first.phase, RuntimeJournalPhase.PREPARED)
        with self.assertRaises(ValueError):
            await self.repository.mark_phase(
                "operation-one", RuntimeJournalPhase.COMMITTED
            )

        await self.credentials.rotate(1, b"r" * 32)
        with self.assertRaises(AdminAuthorizationDenied):
            await self.repository.commit_intent("operation-one", grant)
        self.assertIsNone(
            await self.repository.current_intent("sample", "sample/module")
        )
        self.assertEqual(
            (await self.repository.current_journal("operation-one")).phase,
            RuntimeJournalPhase.PREPARED,
        )

        with self.assertRaises(RevisionConflict):
            await self.repository.prepare(
                "stale-operation",
                "sample",
                "sample/module",
                True,
                expected_intent_revision=1,
                expected_registry_revision=3,
                grant=AdminAuthorizationGrant(AdminOperation.SET_ENABLED, 2),
            )
        self.assertIsNone(await self.repository.current_journal("stale-operation"))

    async def test_authorized_config_requires_current_generation_and_claimed_receipt(
        self,
    ) -> None:
        await self.database.executor.initialize()
        await self.credentials.bootstrap(bytes(range(32)))
        config = SQLiteConfigRepository(self.database)
        target = ConfigTarget("principal", "sample/module")
        patch = PersistedConfigPatch(
            1,
            (ConfigFieldUpdate("region", ConfigPatchMode.REPLACE, value="cn"),),
            (ConfigField("region"),),
            "config-operation",
            target,
        )
        grant = AdminAuthorizationGrant(AdminOperation.UPDATE_CONFIG, 1)
        committed = await config.update_authorized(target, patch, grant)
        self.assertEqual(committed.revision, 2)

        stale = PersistedConfigPatch(
            2,
            (ConfigFieldUpdate("region", ConfigPatchMode.REPLACE, value="jp"),),
            (ConfigField("region"),),
            "stale-config-operation",
            target,
        )
        await self.credentials.rotate(1, b"x" * 32)
        with self.assertRaises(AdminAuthorizationDenied):
            await config.update_authorized(target, stale, grant)
        self.assertEqual((await config.current(target)).values["region"], "cn")

    async def test_authorized_config_rejects_unclaimed_receipt_without_mutation(
        self,
    ) -> None:
        await self.database.executor.initialize()
        await self.credentials.bootstrap(bytes(range(32)))
        config = SQLiteConfigRepository(self.database)
        target = ConfigTarget("principal", "sample/module")
        receipt = SecretReceipt(
            SecretRef(
                "secret_unclaimed", "principal", "sample/module", "token", "config-op"
            ),
            SecretTarget("principal", "sample/module", "token"),
            "config-op",
            1,
            1,
        )
        patch = PersistedConfigPatch(
            1,
            (ConfigFieldUpdate("token", ConfigPatchMode.REPLACE, receipt=receipt),),
            (ConfigField("token", sensitive=True),),
            "config-op",
            target,
        )
        grant = AdminAuthorizationGrant(AdminOperation.UPDATE_CONFIG, 1)
        with self.assertRaises(AdminAuthorizationDenied):
            await config.update_authorized(target, patch, grant)
        current = await config.current(target)
        self.assertEqual(current.revision, 1)
        self.assertEqual(current.secret_metadata, ())


if __name__ == "__main__":
    unittest.main()
