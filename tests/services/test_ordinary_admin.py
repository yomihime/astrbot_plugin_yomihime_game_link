"""Core-only bounded management and explicit startup recovery, using temporary data."""

import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from ygl_test_subject.api.administration import AdminAuthorizationDenied, AdminOperation
from ygl_test_subject.api.display import DisplayLimits
from ygl_test_subject.api.manifests import ConfigField
from ygl_test_subject.api.services import (
    ConfigFieldUpdate,
    ConfigPatch,
    ConfigPatchMode,
    ConfigTarget,
)
from ygl_test_subject.core.ports import RevisionConflict
from ygl_test_subject.modules.ff14.config import (
    CALENDAR_CONFIG_FIELDS,
    CALENDAR_VALUE_VALIDATORS,
    DAYS,
    DELIVERY_TIME,
    TIMEZONE,
)
from ygl_test_subject.services.configuration_migration import OrdinaryMigrationField
from ygl_test_subject.services.core_configuration import (
    DEFAULT_REGION,
    core_config_target,
)
from ygl_test_subject.services.core_runtime import CoreRuntime

from tests.services.test_admin_operations import _Codec, _MessagePort, _Renderer


class OrdinaryAdminTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self.temp.name)
        extensions = self.root / "extensions"
        shutil.copytree(
            Path(__file__).resolve().parents[2] / "modules/ff14",
            extensions / "ff14",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        self.coretarget = core_config_target("fixture")
        self.ff14target = ConfigTarget("fixture", "ff14/ff14")
        self.fields = (
            OrdinaryMigrationField(self.coretarget, DEFAULT_REGION, "legacy_region"),
            *(
                OrdinaryMigrationField(
                    self.ff14target,
                    field,
                    field.name,
                    CALENDAR_VALUE_VALIDATORS.get(field.name),
                )
                for field in CALENDAR_CONFIG_FIELDS
            ),
        )
        self.raw = {"legacy_region": "INVALID-PRIVATE-RAW"}
        self.core = CoreRuntime(
            database=self.root / "core.db",
            extension_root=extensions,
            file_root=self.root / "files",
            secret_root=self.root / "secrets",
            secret_codec=_Codec(),
            http_transport=lambda request: request,
            renderer=_Renderer(),
            display_limits=DisplayLimits(2, 4096),
            message_port=_MessagePort(),
            admin_context_validator=lambda *_: False,
            host_ingress_validator=lambda *_: False,
            config_principal_id="fixture",
            identity_namespace="fixture",
            ordinary_config_resources={self.coretarget: {DEFAULT_REGION.name}, self.ff14target: {field.name for field in CALENDAR_CONFIG_FIELDS}},
            ordinary_migration_fields=self.fields,
            ordinary_migration_source=lambda: self.raw,
            module_config_validators={"ff14/ff14": CALENDAR_VALUE_VALIDATORS},
            pump_interval=3600,
            cleanup_timeout=1,
        )
        with self.assertRaises(ValueError):
            await self.core.start()
        self.resources = self.core.admin_operations.ordinary_resources()
        self.source = self.core.admin_authorization.register_source(
            "test-adapter",
            resources=self.resources,
            operations={
                AdminOperation.READ_CONFIG,
                AdminOperation.UPDATE_CONFIG,
                AdminOperation.ROLLBACK_CONFIG,
                AdminOperation.RECOVER_CONFIG,
            },
        )

    async def asyncTearDown(self):
        await self.core.close(timeout=1)
        self.temp.cleanup()

    def context(self, operation):
        request = object()
        return self.source.issue(
            subject="fixture-subject",
            request=request,
            expiry=time.time() + 30,
            operations={operation},
            resources=self.resources,
            live=lambda actual: actual is request,
        )

    async def read(self):
        context = self.context(AdminOperation.READ_CONFIG)
        try:
            return await self.core.admin_operations.ordinary_snapshot(
                authorization=context
            )
        finally:
            self.source.end(context)

    async def update(self, target, values, revision=None):
        snapshot = await self.core.config_repository.current(target)
        declarations = tuple(
            field.declaration for field in self.fields if field.target == target
        )
        context = self.context(AdminOperation.UPDATE_CONFIG)
        try:
            return await self.core.admin_operations.update_config(
                None,
                target.module_id,
                ConfigPatch(
                    revision or snapshot.revision,
                    tuple(
                        ConfigFieldUpdate(key, ConfigPatchMode.REPLACE, value=value)
                        for key, value in values.items()
                    ),
                    declarations,
                ),
                authorization=context,
            )
        finally:
            self.source.end(context)

    async def test_start_failure_preserves_healthy_foundation_no_business_and_safe_read(
        self,
    ):
        self.assertTrue(self.core.management_available)
        self.assertTrue(self.core.configuration_blocked)
        self.assertFalse(self.core.started)
        self.assertFalse(self.core.accepting)
        self.assertIsNone(self.core._pump_task)
        self.assertEqual(dict(self.core.registry.snapshot().modules), {})
        read = await self.read()
        self.assertEqual(set(read), {"game_link/core", "ff14/ff14"})
        self.assertNotIn("INVALID-PRIVATE-RAW", json.dumps(read))
        self.assertFalse(read["game_link/core"]["fields"]["default_region"]["present"])
        self.assertEqual(
            (await self.core.admin_credential_repository.current()).generation, 0
        )
        self.assertEqual(self.raw, {"legacy_region": "INVALID-PRIVATE-RAW"})

    async def test_invalid_sqlite_value_safe_read_repair_and_cas(self):
        migration = self.core.ordinary_config_migration.repository
        snapshot = await self.core.config_repository.current(self.coretarget)
        await self.core.database.executor.run_transaction(
            lambda unit: migration._write(
                unit,
                self.coretarget,
                "default_region",
                "PRIVATE-INVALID",
                snapshot.revision,
            )
        )
        result = await self.read()
        field = result["game_link/core"]["fields"]["default_region"]
        self.assertEqual(
            field,
            {"value": None, "state": "invalid", "present": True, "source": "sqlite"},
        )
        revision = result["game_link/core"]["revision"]
        repaired = await self.update(
            self.coretarget, {"default_region": "global"}, revision
        )
        self.assertEqual(repaired.revision, revision + 1)
        self.assertFalse(self.core.started)
        with self.assertRaises(RevisionConflict):
            await self.update(self.coretarget, {"default_region": "cn"}, revision)
        self.assertEqual(
            (await self.read())["game_link/core"]["fields"]["default_region"]["value"],
            "global",
        )

    async def test_repair_unrelated_invalid_field_is_not_silently_repaired(self):
        migration = self.core.ordinary_config_migration.repository
        snapshot = await self.core.config_repository.current(self.ff14target)
        await self.core.database.executor.run_transaction(
            lambda unit: migration._write(
                unit, self.ff14target, DAYS, 99, snapshot.revision
            )
        )
        with self.assertRaises(ValueError):
            await self.update(self.ff14target, {TIMEZONE: "UTC"})
        result = await self.update(
            self.ff14target, {DAYS: 3, TIMEZONE: "UTC", DELIVERY_TIME: "10:30"}
        )
        self.assertEqual(result.revision, 2)

    async def test_recover_requires_all_explicit_new_values_and_both_revisions(self):
        context = self.context(AdminOperation.RECOVER_CONFIG)
        revisions = {
            target: (await self.core.config_repository.current(target)).revision
            for target in self.resources
        }
        with self.assertRaises(ValueError):
            await self.core.recover_management(
                revisions, authorization=context, complete_from_current=True
            )
        self.assertFalse(await self.core.ordinary_config_migration.complete())
        await self.update(self.coretarget, {"default_region": "global"})
        await self.update(
            self.ff14target, {DAYS: 3, TIMEZONE: "UTC", DELIVERY_TIME: "10:30"}
        )
        with self.assertRaises(RevisionConflict):
            await self.core.recover_management(
                revisions, authorization=context, complete_from_current=True
            )
        revisions = {
            target: (await self.core.config_repository.current(target)).revision
            for target in self.resources
        }
        await self.core.recover_management(
            revisions, authorization=context, complete_from_current=True
        )
        self.assertTrue(self.core.started)
        self.assertTrue(self.core.accepting)
        self.assertIsNotNone(self.core._pump_task)
        marker = await self.core.database.executor.run_read(
            lambda u: u.execute(
                "SELECT snapshot_json FROM ordinary_config_migrations"
            ).fetchone()[0]
        )
        self.assertEqual(json.loads(marker)["source"], "admin_replacement")
        self.assertIsNone(json.loads(marker)["legacy"])
        self.assertEqual(self.raw, {"legacy_region": "INVALID-PRIVATE-RAW"})
        self.assertEqual(
            (await self.core.admin_credential_repository.current()).generation, 0
        )

    async def test_rollback_preserves_unrelated_and_same_value_later_write_blocks(self):
        self.raw = {
            "legacy_region": "global",
            DAYS: 3,
            TIMEZONE: "UTC",
            DELIVERY_TIME: "10:30",
        }
        revisions = {
            target: (await self.core.config_repository.current(target)).revision
            for target in self.resources
        }
        context = self.context(AdminOperation.RECOVER_CONFIG)
        await self.core.recover_management(revisions, authorization=context)
        migration = self.core.ordinary_config_migration.repository
        await self.core.config_repository.current(
            ConfigTarget("unrelated", "other/mod")
        )
        await self.core.database.executor.run_transaction(
            lambda u: migration._write(
                u, ConfigTarget("unrelated", "other/mod"), "note", "keep", 1
            )
        )
        revisions = {
            target: (await self.core.config_repository.current(target)).revision
            for target in self.resources
        }
        rollback = self.context(AdminOperation.ROLLBACK_CONFIG)
        await self.core.admin_operations.ordinary_rollback(
            revisions, authorization=rollback
        )
        self.assertFalse(await self.core.ordinary_config_migration.complete())
        self.assertEqual(
            (
                await self.core.config_repository.current(
                    ConfigTarget("unrelated", "other/mod")
                )
            ).values["note"],
            "keep",
        )
        self.assertNotIn(
            "default_region",
            (await self.core.config_repository.current(self.coretarget)).values,
        )
        await self.core.ordinary_config_migration.migrate(self.raw)
        await self.update(self.coretarget, {"default_region": "global"})
        revisions = {
            target: (await self.core.config_repository.current(target)).revision
            for target in self.resources
        }
        with self.assertRaises(ValueError):
            await self.core.admin_operations.ordinary_rollback(
                revisions, authorization=rollback
            )
        self.assertTrue(await self.core.ordinary_config_migration.complete())

    async def test_no_extra_module_lifecycle_or_untrusted_recovery_after_close(self):
        context = self.context(AdminOperation.UPDATE_CONFIG)
        with self.assertRaises(AdminAuthorizationDenied):
            await self.core.admin_operations.list_modules(None, authorization=context)
        with self.assertRaises(AdminAuthorizationDenied):
            await self.core.admin_operations.update_config(
                None,
                "ff14/ff14",
                ConfigPatch(
                    1,
                    (
                        ConfigFieldUpdate(
                            "unrelated", ConfigPatchMode.REPLACE, value=True
                        ),
                    ),
                    (ConfigField("unrelated"),),
                ),
                authorization=context,
            )
        await self.core.close(timeout=1)
        self.assertFalse(self.core.management_available)
        with self.assertRaises(AdminAuthorizationDenied):
            await self.core.recover_management({}, authorization=context)

    async def test_database_integrity_failure_does_not_preserve_foundation(self):
        self.raw = {}
        with patch.object(
            self.core.database.executor,
            "verify_integrity",
            new=AsyncMock(side_effect=RuntimeError("unavailable")),
        ):
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                await self.core.start()
        self.assertFalse(self.core.management_available)
        self.assertFalse(self.core.configuration_blocked)
        self.assertFalse(self.core.started)
        self.assertIsNone(self.core._pump_task)

    async def test_ended_recovery_proof_cannot_start_business(self):
        self.raw = {}
        rows = await self.read()
        context = self.context(AdminOperation.RECOVER_CONFIG)
        self.source.end(context)
        with self.assertRaises(AdminAuthorizationDenied):
            await self.core.recover_management(
                {
                    target: rows[target.module_id]["revision"]
                    for target in self.resources
                },
                authorization=context,
            )
        self.assertFalse(self.core.started)
        self.assertIsNone(self.core._pump_task)
