"""Core declaration metadata and unchanged, bounded ordinary-write authority."""

import json
import unittest
from unittest.mock import AsyncMock, patch

from ygl_test_subject.core.contracts.administration import (
    AdminAuthorizationDenied,
    AdminOperation,
    OrdinaryRollbackReceipt,
)
from ygl_test_subject.core.contracts.services import ConfigFieldUpdate, ConfigPatch
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.modules.ff14.config import DELIVERY_TIME, TIMEZONE
from ygl_test_subject.services.configuration_catalog import (
    project_configuration_catalog,
)
from ygl_test_subject.services.configuration_migration import (
    OrdinaryConfigurationMigration,
    OrdinaryMigrationField,
)
from ygl_test_subject.services.core_configuration import DEFAULT_REGION

from tests.services import test_ordinary_admin as ordinary_fixture
from tests.services.test_admin_operations import _config_manifest
from yomihime_game_link_sdk.declarations import ConfigField, ConfigUpdateMode
from yomihime_game_link_sdk.services import ConfigTarget


class CatalogRuntimeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = ordinary_fixture.OrdinaryAdminTests.asyncSetUp
    asyncTearDown = ordinary_fixture.OrdinaryAdminTests.asyncTearDown
    context = ordinary_fixture.OrdinaryAdminTests.context
    update = ordinary_fixture.OrdinaryAdminTests.update
    read = ordinary_fixture.OrdinaryAdminTests.read

    async def catalog(self):
        context = self.context(AdminOperation.READ_CONFIG)
        try:
            return await self.core.admin_operations.ordinary_catalog(
                authorization=context
            )
        finally:
            self.source.end(context)

    async def test_catalog_metadata_does_not_read_values_or_import_candidates(self):
        package = self.root / "extensions/admin"
        package.mkdir()
        manifest = _config_manifest()
        manifest["modules"][0]["config_fields"].append(
            {"name": "private_token", "sensitive": True}
        )
        (package / "yomihime.manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        (package / "module.py").write_text(
            "raise AssertionError('metadata imported source')", encoding="utf-8"
        )
        self.core.extension_runtime.scan(self.root / "extensions")
        target = validate_contract(ConfigTarget("fixture", "admin/mod"))
        await self.core.config_repository.current(target)
        await self.core.database.executor.run_transaction(
            lambda unit: self.core.ordinary_config_migration.repository._write(
                unit, target, "mode", "PRIVATE-UNGRANTED-VALUE", 1
            )
        )
        with patch.object(
            self.core.config_repository,
            "current",
            new=AsyncMock(side_effect=AssertionError("catalog read stored values")),
        ):
            result = await self.catalog()
        self.assertEqual(set(result), {"schema_version", "fields"})
        self.assertEqual(result["schema_version"], 1)
        keys = {
            "module_id",
            "name",
            "description",
            "group",
            "value_schema",
            "default",
            "required",
            "readable",
            "editable",
            "blocked_reason",
        }
        self.assertTrue(all(set(field) == keys for field in result["fields"]))
        second = next(
            field for field in result["fields"] if field["module_id"] == "admin/mod"
        )
        self.assertEqual(
            (second["readable"], second["editable"], second["blocked_reason"]),
            (False, False, "not_granted"),
        )
        self.assertNotIn("PRIVATE-UNGRANTED-VALUE", json.dumps(result))
        self.assertNotIn("private_token", json.dumps(result))
        self.assertNotIn("admin/mod", await self.read())
        context = self.context(AdminOperation.UPDATE_CONFIG)
        with self.assertRaises(AdminAuthorizationDenied):
            await self.core.admin_operations.update_config(
                None,
                "admin/mod",
                ConfigPatch(
                    1,
                    (ConfigFieldUpdate("mode", ConfigUpdateMode.REPLACE, value="new"),),
                    (validate_contract(ConfigField("mode")),),
                ),
                authorization=context,
            )
        self.source.end(context)
        allowed = [field for field in result["fields"] if field["readable"]]
        self.assertEqual(len(allowed), 4)
        self.assertTrue(all(field["editable"] for field in allowed))

    async def test_catalog_requires_current_read_proof(self):
        for context in (None, self.context(AdminOperation.UPDATE_CONFIG)):
            with self.assertRaises(AdminAuthorizationDenied):
                await self.core.admin_operations.ordinary_catalog(authorization=context)
            if context is not None:
                self.source.end(context)
        ended = self.context(AdminOperation.READ_CONFIG)
        self.source.end(ended)
        with self.assertRaises(AdminAuthorizationDenied):
            await self.core.admin_operations.ordinary_catalog(authorization=ended)

    async def test_missing_or_replaced_semantic_binding_blocks_write_clear_and_recover(
        self,
    ):
        ops = self.core.admin_operations
        original = ops._module_config_validators
        for replacement in (
            {},
            {
                "ff14/ff14": {
                    TIMEZONE: lambda value: None,
                    DELIVERY_TIME: original["ff14/ff14"][DELIVERY_TIME],
                }
            },
        ):
            with (
                self.subTest(replacement=bool(replacement)),
                patch.object(ops, "_module_config_validators", replacement),
            ):
                catalog = await self.catalog()
                zone = next(
                    field for field in catalog["fields"] if field["name"] == TIMEZONE
                )
                self.assertTrue(zone["readable"])
                self.assertFalse(zone["editable"])
                self.assertEqual(
                    zone["blocked_reason"], "semantic_validator_unavailable"
                )
                before = await self.core.config_repository.current(self.ff14target)
                for mode in (ConfigUpdateMode.REPLACE, ConfigUpdateMode.CLEAR):
                    context = self.context(AdminOperation.UPDATE_CONFIG)
                    update = ConfigFieldUpdate(
                        TIMEZONE,
                        mode,
                        value="UTC" if mode is ConfigUpdateMode.REPLACE else None,
                    )
                    with self.assertRaisesRegex(ValueError, "semantic validator"):
                        await ops.update_config(
                            None,
                            self.ff14target.module_id,
                            ConfigPatch(
                                before.revision,
                                (update,),
                                tuple(
                                    field.declaration
                                    for field in self.fields
                                    if field.target == self.ff14target
                                ),
                            ),
                            authorization=context,
                        )
                    self.source.end(context)
                for complete in (False, True):
                    context = self.context(AdminOperation.RECOVER_CONFIG)
                    revisions = {
                        target: (
                            await self.core.config_repository.current(target)
                        ).revision
                        for target in self.resources
                    }
                    with self.assertRaisesRegex(ValueError, "semantic validator"):
                        await self.core.recover_management(
                            revisions,
                            authorization=context,
                            complete_from_current=complete,
                        )
                    self.source.end(context)
                self.assertEqual(
                    before, await self.core.config_repository.current(self.ff14target)
                )
                self.assertFalse(await self.core.ordinary_config_migration.complete())
                self.assertTrue(self.core.management_available)
                self.assertTrue(self.core.configuration_blocked)
        # Only the affected fields are gated: the Core default remains repairable.
        with patch.object(ops, "_module_config_validators", {}):
            result = await self.update(self.coretarget, {"default_region": "global"})
            self.assertEqual(result.revision, 2)

    async def test_semantic_values_and_cas_keep_atomic_write_contract(self):
        before = await self.core.config_repository.current(self.ff14target)
        for values in (
            {TIMEZONE: "invalid-fixture-zone"},
            {DELIVERY_TIME: "24:00"},
            {TIMEZONE: True},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                await self.update(self.ff14target, values)
            self.assertEqual(
                before, await self.core.config_repository.current(self.ff14target)
            )
        changed = await self.update(self.ff14target, {TIMEZONE: "UTC"}, before.revision)
        self.assertEqual(changed.revision, before.revision + 1)
        from ygl_test_subject.core.ports import RevisionConflict

        with self.assertRaises(RevisionConflict):
            await self.update(
                self.ff14target, {TIMEZONE: "Asia/Shanghai"}, before.revision
            )

    async def test_limited_rollback_survives_missing_validator_and_preserves_unrelated(
        self,
    ):
        self.raw = {}
        context = self.context(AdminOperation.RECOVER_CONFIG)
        revisions = {
            target: (await self.core.config_repository.current(target)).revision
            for target in self.resources
        }
        await self.core.recover_management(revisions, authorization=context)
        self.source.end(context)
        unrelated = validate_contract(ConfigTarget("unrelated", "other/mod"))
        await self.core.config_repository.current(unrelated)
        await self.core.database.executor.run_transaction(
            lambda unit: self.core.ordinary_config_migration.repository._write(
                unit, unrelated, "note", "kept", 1
            )
        )
        revisions = {
            target: (await self.core.config_repository.current(target)).revision
            for target in self.resources
        }
        context = self.context(AdminOperation.ROLLBACK_CONFIG)
        with patch.object(self.core.admin_operations, "_module_config_validators", {}):
            result = await self.core.admin_operations.ordinary_rollback(
                revisions, authorization=context
            )
        self.source.end(context)
        self.assertIs(type(result), OrdinaryRollbackReceipt)
        self.assertIs(result.rolled_back, True)
        self.assertEqual(
            (await self.core.config_repository.current(unrelated)).values["note"],
            "kept",
        )
        self.assertFalse(await self.core.ordinary_config_migration.complete())

    async def test_runtime_start_missing_binding_preserves_recovery_foundation(self):
        self.raw = {}
        with patch.object(self.core.admin_operations, "_module_config_validators", {}):
            with self.assertRaisesRegex(ValueError, "semantic validator"):
                await self.core.start()
            self.assertTrue(self.core.management_available)
            self.assertTrue(self.core.configuration_blocked)
            self.assertFalse(self.core.started)
        await self.core.start()
        self.assertTrue(self.core.started)
        self.assertTrue(
            all(
                field["editable"]
                for field in (await self.catalog())["fields"]
                if field["readable"]
            )
        )


class CatalogBudgetTests(unittest.TestCase):
    def project(self, fields):
        return project_configuration_catalog(
            {"test/mod": fields},
            {},
            principal_id="fixture",
            validator_check=lambda resources: None,
        )

    def test_closed_budget_rejects_without_truncation(self):
        with self.assertRaisesRegex(ValueError, "field budget"):
            self.project(
                tuple(validate_contract(ConfigField(f"field{i}")) for i in range(129))
            )
        with self.assertRaisesRegex(ValueError, "string budget"):
            self.project((validate_contract(ConfigField("field", default="x" * 4097)),))
        with self.assertRaisesRegex(ValueError, "byte budget"):
            self.project(
                tuple(
                    validate_contract(
                        ConfigField(f"field{i}", default=chr(0x4E2D) * 1000)
                    )
                    for i in range(128)
                )
            )

    def test_standalone_migration_required_binding_is_frozen_and_guards_writes(self):
        target = validate_contract(ConfigTarget("fixture", "game_link/core"))

        def validator(value):
            pass

        migration = OrdinaryConfigurationMigration(
            object(),
            (OrdinaryMigrationField(target, DEFAULT_REGION, "legacy", validator),),
            migration_id="fixture",
            value_validators={},
        )
        with self.assertRaisesRegex(ValueError, "semantic validator"):
            migration.require_semantic_validators()
        migration.require_semantic_validators(
            {
                validate_contract(ConfigTarget("fixture", "other/mod")): {
                    "default_region"
                }
            }
        )
        self.assertIs(
            migration._required_validators[(target, "default_region")], validator
        )
        with self.assertRaises(TypeError):
            migration._required_validators[(target, "default_region")] = None

    def test_sensitive_declarations_are_omitted(self):
        result = self.project(
            (
                validate_contract(ConfigField("token", sensitive=True)),
                validate_contract(ConfigField("ordinary", default="safe")),
            )
        )
        self.assertEqual([field["name"] for field in result["fields"]], ["ordinary"])
        self.assertFalse(result["fields"][0]["readable"])
