from __future__ import annotations

import json
import unittest
from dataclasses import replace

from ygl_test_subject.adapters.astrbot.config_adapter import legacy_core_defaults
from ygl_test_subject.api.administration import AdminAuthorizationDenied, AdminOperation
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
    FF14ConfigSnapshot,
)
from ygl_test_subject.services.configuration import (
    ConfigurationCoordinator,
    ConfigurationValueError,
)
from ygl_test_subject.services.core_configuration import (
    CORE_CONFIG_FIELDS,
    CORE_MODULE_ID,
    DEFAULT_REGION,
    CoreDefaultsConfigView,
    CoreDefaultsView,
    core_config_target,
    plan_configuration_rollback,
    select_configuration_value,
)

import tests.services.test_admin_operations as runtime_fixture


class CoreSelectionTests(unittest.TestCase):
    def test_rollback_plan_preserves_missing_invalid_values_and_export_gate(self):
        fields = CORE_CONFIG_FIELDS + CALENDAR_CONFIG_FIELDS
        new = {"default_region": None, DAYS: 9}
        legacy = {"default_region": "global", DELIVERY_TIME: "bad-time"}
        plan = plan_configuration_rollback(
            fields, new, legacy, new_configuration_written=False
        )
        self.assertFalse(plan.requires_current_export)
        self.assertEqual(plan.original_new_values, new)
        self.assertEqual(plan.original_legacy_values, legacy)
        self.assertNotIn(TIMEZONE, plan.original_new_values)
        new[DAYS] = 15
        legacy["default_region"] = "cn"
        self.assertEqual(plan.original_new_values[DAYS], 9)
        self.assertEqual(plan.original_legacy_values["default_region"], "global")
        after_write = plan_configuration_rollback(
            fields, new, legacy, new_configuration_written=True
        )
        self.assertTrue(after_write.requires_current_export)
        with self.assertRaises(ValueError):
            plan_configuration_rollback(
                (ConfigField("token", sensitive=True),),
                {},
                {},
                new_configuration_written=False,
            )
        with self.assertRaises(ValueError):
            plan_configuration_rollback(
                fields, {"unrelated": 1}, {}, new_configuration_written=False
            )

    def test_raw_presence_and_selection_matrix(self):
        missing = object()
        candidates = [missing, "cn", "global", None, "", "bad", True]
        for new in candidates:
            for old in candidates:
                with self.subTest(new=new, old=old):
                    fresh = {} if new is missing else {"default_region": new}
                    legacy = {} if old is missing else {"legacy_region": old}
                    before = (dict(fresh), dict(legacy))
                    valid_new = new in ("cn", "global")
                    valid_old = old in ("cn", "global")
                    rejects = (new is not missing and not valid_new) or (
                        new is missing and old is not missing and not valid_old
                    )
                    if rejects:
                        with self.assertRaises(ConfigurationValueError) as caught:
                            select_configuration_value(
                                DEFAULT_REGION,
                                fresh,
                                legacy,
                                legacy_field="legacy_region",
                            )
                        self.assertEqual(caught.exception.field, "default_region")
                        self.assertEqual(
                            caught.exception.code, "invalid_configuration_value"
                        )
                        self.assertNotIn("bad", str(caught.exception))
                    else:
                        selected = select_configuration_value(
                            DEFAULT_REGION, fresh, legacy, legacy_field="legacy_region"
                        )
                        self.assertEqual(
                            selected.value,
                            new if valid_new else old if valid_old else "cn",
                        )
                        self.assertEqual(
                            selected.origin,
                            "new"
                            if valid_new
                            else "legacy"
                            if valid_old
                            else "default",
                        )
                        self.assertEqual(
                            selected.conflict, valid_new and valid_old and new != old
                        )
                        self.assertEqual(selected.new_present, new is not missing)
                        self.assertEqual(selected.legacy_present, old is not missing)
                        self.assertNotIn("value", selected.diagnostic())
                        self.assertNotIn("global", str(dict(selected.diagnostic())))
                    self.assertEqual(
                        (fresh, legacy), before, "rollback inputs must remain untouched"
                    )

    def test_host_mapping_does_not_inject_default_before_legacy_selection(self):
        self.assertEqual(dict(legacy_core_defaults({})), {})
        mapped = legacy_core_defaults({"ff14_default_region": "global"})
        selected = select_configuration_value(DEFAULT_REGION, {}, mapped)
        self.assertEqual(
            (selected.value, selected.origin, selected.new_present),
            ("global", "legacy", False),
        )
        self.assertEqual(
            FF14ConfigSnapshot.from_values(
                {"ff14_default_region": "global"}
            ).default_region,
            "global",
        )
        explicit_none = legacy_core_defaults({"ff14_default_region": None})
        with self.assertRaises(ConfigurationValueError):
            select_configuration_value(DEFAULT_REGION, {}, explicit_none)

    def test_calendar_selection_each_field_and_legacy_rollback(self):
        for field in CALENDAR_CONFIG_FIELDS:
            check = CALENDAR_VALUE_VALIDATORS.get(field.name)
            legacy = {field.name: field.default}
            result = select_configuration_value(field, {}, legacy, validator=check)
            self.assertEqual(result.origin, "legacy")
            self.assertFalse(result.new_present)
            for invalid in (None, True, "", "rejected-input"):
                with self.subTest(field=field.name, invalid=invalid):
                    with self.assertRaises(ConfigurationValueError):
                        select_configuration_value(
                            field, {field.name: invalid}, legacy, validator=check
                        )
                    self.assertEqual(legacy, {field.name: field.default})
        self.assertEqual(
            {field.name for field in CORE_CONFIG_FIELDS}, {"default_region"}
        )
        self.assertEqual(
            {field.name for field in CALENDAR_CONFIG_FIELDS},
            {DAYS, TIMEZONE, DELIVERY_TIME},
        )

    def test_falsy_legacy_mapping_inputs_reject_and_selected_json_is_detached(self):
        for invalid in (False, [], "", 0):
            with self.assertRaises(TypeError):
                CoreDefaultsView(object(), legacy_defaults=invalid)
        value = {"items": ["kept"]}
        field = ConfigField(
            "settings",
            value_schema={
                "type": "object",
                "properties": {"items": {"type": "array", "items": {"type": "string"}}},
            },
        )
        selected = select_configuration_value(field, {"settings": value}, {})
        value["items"].append("later")
        self.assertEqual(selected.value["items"], ("kept",))


class CoreDefaultsRuntimeTests(unittest.IsolatedAsyncioTestCase):
    setUp = runtime_fixture.AdminOperationsRuntimeTests.setUp
    asyncTearDown = runtime_fixture.AdminOperationsRuntimeTests.asyncTearDown
    _runtime = runtime_fixture.AdminOperationsRuntimeTests._runtime
    _bootstrap = runtime_fixture.AdminOperationsRuntimeTests._bootstrap
    _start_with_candidate = (
        runtime_fixture.AdminOperationsRuntimeTests._start_with_candidate
    )

    async def _seed_invalid_defaults(self, target, values):
        runtime = self._runtime()
        await self._start_with_candidate(runtime)
        _, context = await self._bootstrap(runtime)

        def seed(unit):
            unit.execute(
                "INSERT INTO config_state(principal_id,module_id,revision) VALUES (?,?,2)",
                (target.principal_id, target.module_id),
            )
            for name, value in values.items():
                unit.execute(
                    "INSERT INTO config_entries(principal_id,module_id,field,value_json,revision) VALUES (?,?,?,?,2)",
                    (target.principal_id, target.module_id, name, json.dumps(value)),
                )

        await runtime.database.executor.run_transaction(seed)
        return runtime, context

    async def test_core_admin_read_revision_invalid_safe_repair_and_no_auth(self):
        target = core_config_target("host-config")
        runtime, context = await self._seed_invalid_defaults(target, {"default_region": "private-invalid-value"})
        with self.assertRaises(AdminAuthorizationDenied):
            await runtime.admin_facade.config_snapshot(None, CORE_MODULE_ID)
        summary = await runtime.admin_facade.config_snapshot(None, CORE_MODULE_ID, authorization=context)
        self.assertEqual((summary.revision, summary.state, summary.default_region), (2, "invalid", None))
        self.assertTrue(summary.raw_present)
        self.assertNotIn("private-invalid-value", repr(summary))
        await runtime.admin_facade.update_config(None, CORE_MODULE_ID,
            ConfigPatch(summary.revision, (ConfigFieldUpdate("default_region", ConfigPatchMode.REPLACE, value="global"),), CORE_CONFIG_FIELDS),
            authorization=context)
        current = await runtime.admin_facade.config_snapshot(None, CORE_MODULE_ID, authorization=context)
        self.assertEqual((current.revision, current.default_region, current.state), (3, "global", "valid"))

    async def test_core_admin_read_fences_generation_after_repository_read(self):
        from unittest.mock import patch
        runtime = self._runtime()
        await self._start_with_candidate(runtime)
        _, context = await self._bootstrap(runtime)
        original = runtime.config_repository.current
        async def rotated(target):
            snapshot = await original(target)
            await runtime.admin_credential_repository.rotate(1, runtime_fixture._digest(runtime_fixture.token_urlsafe(32)))
            return snapshot
        with patch.object(runtime.config_repository, "current", rotated):
            with self.assertRaises(AdminAuthorizationDenied):
                await runtime.admin_facade.config_snapshot(None, CORE_MODULE_ID, authorization=context)

    async def test_actual_admin_write_conflicts_with_migration_without_partial_completion(self):
        from unittest.mock import patch

        from ygl_test_subject.adapters.astrbot.config_adapter import (
            ordinary_migration_fields,
        )
        from ygl_test_subject.infrastructure.sqlite.repositories_config_migration import (
            SQLiteOrdinaryConfigurationMigrationRepository,
        )
        from ygl_test_subject.services.configuration_migration import (
            OrdinaryConfigurationMigration,
        )
        runtime = self._runtime()
        await self._start_with_candidate(runtime)
        _, context = await self._bootstrap(runtime)
        repository = SQLiteOrdinaryConfigurationMigrationRepository(runtime.config_repository)
        migration = OrdinaryConfigurationMigration(repository, ordinary_migration_fields("host-config"), migration_id="ff14-core-defaults-v1")
        original = repository.apply
        async def admin_concurrent(definition, snapshots, selected, legacy):
            await runtime.admin_facade.update_config(None, CORE_MODULE_ID,
                ConfigPatch(snapshots[core_config_target("host-config")].revision,
                            (ConfigFieldUpdate("default_region", ConfigPatchMode.REPLACE, value="global"),), CORE_CONFIG_FIELDS),
                authorization=context)
            return await original(definition, snapshots, selected, legacy)
        with patch.object(repository, "apply", admin_concurrent):
            with self.assertRaises(RevisionConflict):
                await migration.migrate({})
        self.assertFalse(await migration.complete())
        self.assertEqual((await runtime.config_repository.current(ConfigTarget("host-config", "ff14/ff14"))).values, {})

    async def test_admin_can_replace_invalid_core_without_bypassing_patch_guards(self):
        target = core_config_target("host-config")
        runtime, context = await self._seed_invalid_defaults(
            target, {"default_region": None}
        )

        def patch(mode, value=None, revision=2):
            return ConfigPatch(
                revision,
                (ConfigFieldUpdate("default_region", mode, value=value),),
                CORE_CONFIG_FIELDS,
            )

        for action, error in (
            (patch(ConfigPatchMode.REPLACE, True), ConfigurationValueError),
            (patch(ConfigPatchMode.KEEP), ConfigurationValueError),
            (patch(ConfigPatchMode.REPLACE, "global", revision=1), RevisionConflict),
        ):
            with self.assertRaises(error):
                await runtime.admin_facade.update_config(
                    None, CORE_MODULE_ID, action, authorization=context
                )
            raw = await runtime.config_repository.current(target)
            self.assertEqual(
                (raw.revision, dict(raw.values)), (2, {"default_region": None})
            )
        valid = patch(ConfigPatchMode.REPLACE, "global")
        with self.assertRaises(AdminAuthorizationDenied):
            await runtime.admin_facade.update_config(None, CORE_MODULE_ID, valid)
        with self.assertRaises(ConfigurationValueError):
            await runtime.core_defaults.current()
        updated = await runtime.admin_facade.update_config(
            None, CORE_MODULE_ID, valid, authorization=context
        )
        self.assertEqual(updated.revision, 3)
        raw = await runtime.config_repository.current(target)
        self.assertEqual(
            (raw.revision, dict(raw.values)), (3, {"default_region": "global"})
        )
        self.assertEqual(
            (await runtime.core_defaults.current()).values["origin"], "new"
        )

    async def test_admin_can_clear_invalid_core_and_restore_missing_key_rules(self):
        target = core_config_target("host-config")
        runtime, context = await self._seed_invalid_defaults(
            target, {"default_region": None}
        )
        legacy_view = CoreDefaultsView(
            runtime.admin_operations._core_configuration().view(),
            legacy_defaults={"default_region": "global"},
        )
        with self.assertRaises(ConfigurationValueError):
            await legacy_view.current()
        patch = ConfigPatch(
            2,
            (ConfigFieldUpdate("default_region", ConfigPatchMode.CLEAR),),
            CORE_CONFIG_FIELDS,
        )
        updated = await runtime.admin_facade.update_config(
            None, CORE_MODULE_ID, patch, authorization=context
        )
        self.assertEqual(updated.revision, 3)
        self.assertEqual((await runtime.config_repository.current(target)).values, {})
        self.assertEqual(
            (await runtime.core_defaults.current()).values["default_region"], "cn"
        )
        legacy = await legacy_view.current()
        self.assertEqual(
            (
                legacy.values["default_region"],
                legacy.values["origin"],
                legacy.values["raw_present"],
            ),
            ("global", "legacy", False),
        )

    async def test_unrelated_or_keep_patch_cannot_hide_invalid_module_predecessor(self):
        target = ConfigTarget("host-config", "ff14/ff14")
        runtime, context = await self._seed_invalid_defaults(
            target, {DAYS: 7, TIMEZONE: None}
        )
        coordinator = ConfigurationCoordinator(
            target,
            CALENDAR_CONFIG_FIELDS,
            runtime.config_repository,
            runtime.secret_store,
            admission=runtime.lifecycle.admission,
            validate_admin_grant=lambda grant: runtime.admin_authorization.validate_generation(
                grant, operation=AdminOperation.UPDATE_CONFIG
            ),
            publish_config=lambda *_args, **_kwargs: (),
            value_validators=CALENDAR_VALUE_VALIDATORS,
        )
        grant = await runtime.admin_authorization.authorize(
            AdminOperation.UPDATE_CONFIG, invocation=None, context=context
        )
        for update in (
            ConfigFieldUpdate(DAYS, ConfigPatchMode.REPLACE, value=8),
            ConfigFieldUpdate(TIMEZONE, ConfigPatchMode.KEEP),
        ):
            with self.assertRaises(ConfigurationValueError):
                await coordinator.update_admin(
                    target, ConfigPatch(2, (update,), CALENDAR_CONFIG_FIELDS), grant
                )
            raw = await runtime.config_repository.current(target)
            self.assertEqual(
                (raw.revision, dict(raw.values)), (2, {DAYS: 7, TIMEZONE: None})
            )
        with self.assertRaises(ConfigurationValueError):
            await coordinator.current()
        repaired = await coordinator.update_admin(
            target,
            ConfigPatch(
                2,
                (ConfigFieldUpdate(TIMEZONE, ConfigPatchMode.REPLACE, value="UTC"),),
                CALENDAR_CONFIG_FIELDS,
            ),
            grant,
        )
        self.assertEqual(repaired.values, {DAYS: 7, TIMEZONE: "UTC"})

    async def test_real_core_admin_grant_cas_and_reserved_patch_boundaries(self):
        runtime = self._runtime()
        await self._start_with_candidate(runtime)
        _, context = await self._bootstrap(runtime)

        def patch(value, revision=1, target=None):
            return ConfigPatch(
                revision,
                (
                    ConfigFieldUpdate(
                        "default_region", ConfigPatchMode.REPLACE, value=value
                    ),
                ),
                CORE_CONFIG_FIELDS,
                target=target,
            )

        with self.assertRaises(AdminAuthorizationDenied):
            await runtime.admin_facade.update_config(
                None, CORE_MODULE_ID, patch("global")
            )
        with self.assertRaises((AdminAuthorizationDenied, ValueError)):
            await runtime.admin_facade.update_config(
                None,
                CORE_MODULE_ID,
                patch("global", target=core_config_target("other-principal")),
                authorization=context,
            )
        with self.assertRaisesRegex(ValueError, "require a value"):
            patch(None)
        for value in (True, "", "unsafe-input"):
            with self.assertRaises(ConfigurationValueError) as caught:
                await runtime.admin_facade.update_config(
                    None, CORE_MODULE_ID, patch(value), authorization=context
                )
            self.assertNotIn("unsafe-input", str(caught.exception))
        updated = await runtime.admin_facade.update_config(
            None, CORE_MODULE_ID, patch("global"), authorization=context
        )
        self.assertEqual(updated.revision, 2)
        raw = await runtime.config_repository.current(core_config_target("host-config"))
        self.assertEqual(raw.values, {"default_region": "global"})
        with self.assertRaises(RevisionConflict):
            await runtime.admin_facade.update_config(
                None, CORE_MODULE_ID, patch("cn"), authorization=context
            )
        with self.assertRaises(ValueError):
            await runtime.admin_facade.update_config(
                None, "admin/mod", patch("cn"), authorization=context
            )
        with self.assertRaises(AdminAuthorizationDenied):
            await runtime.admin_facade.update_config(
                None,
                "unknown/mod",
                ConfigPatch(
                    1,
                    (ConfigFieldUpdate("mode", ConfigPatchMode.REPLACE, value="ok"),),
                    (ConfigField("mode"),),
                ),
                authorization=context,
            )
        self.assertEqual(
            (
                await runtime.config_repository.current(
                    ConfigTarget("host-config", "admin/mod")
                )
            ).values,
            {},
        )
        self.assertEqual(
            (
                await runtime.config_repository.current(
                    core_config_target("other-principal")
                )
            ).values,
            {},
        )

    async def test_combined_view_is_readonly_and_preserves_separate_revisions(self):
        runtime = self._runtime()
        await self._start_with_candidate(runtime)
        _, context = await self._bootstrap(runtime)
        services = runtime.module_services.for_candidate(
            "admin/mod",
            runtime.extension_runtime.candidate("admin").package.manifest.modules[0],
        )
        first = await services.config.current()
        self.assertEqual(
            (first.revision, first.values["core_defaults"]["revision"]), (1, 1)
        )
        for attr in (
            "update",
            "update_admin",
            "clear",
            "_core",
            "_target",
            "__current",
        ):
            self.assertFalse(hasattr(services.config, attr))
            self.assertFalse(hasattr(runtime.core_defaults, attr))
        await runtime.admin_facade.update_config(
            None,
            CORE_MODULE_ID,
            ConfigPatch(
                1,
                (
                    ConfigFieldUpdate(
                        "default_region", ConfigPatchMode.REPLACE, value="global"
                    ),
                ),
                CORE_CONFIG_FIELDS,
            ),
            authorization=context,
        )
        after = await services.config.current()
        self.assertEqual(after.revision, 1)
        self.assertEqual(after.values["core_defaults"]["revision"], 2)
        self.assertEqual(after.values["core_defaults"]["default_region"], "global")
        with self.assertRaises(TypeError):
            after.values["core_defaults"]["default_region"] = "cn"
        self.assertEqual(first.values["core_defaults"]["default_region"], "cn")
        manifest = runtime.extension_runtime.candidate(
            "admin"
        ).package.manifest.modules[0]
        for name in ("core_defaults", "default_region"):
            with self.assertRaises(ValueError):
                runtime.module_services.for_candidate(
                    "admin/mod", replace(manifest, config_fields=(ConfigField(name),))
                )
        with self.assertRaises(ValueError):
            runtime.module_services.for_candidate(
                CORE_MODULE_ID, replace(manifest, module_id="core")
            )
        for name in ("core_defaults", "default_region"):
            runtime.module_services._module_host_config_snapshots = {
                "admin/mod": {name: "global"}
            }
            with self.assertRaises(ValueError):
                runtime.module_services.for_candidate("admin/mod", manifest)
        self.assertEqual(
            (
                await runtime.config_repository.current(
                    ConfigTarget("host-config", "admin/mod")
                )
            ).values,
            {},
        )

    async def test_missing_raw_core_preserves_legacy_global_without_persistence(self):
        runtime = self._runtime()
        await self._start_with_candidate(runtime)
        view = CoreDefaultsView(
            runtime.admin_operations._core_configuration().view(),
            legacy_defaults={"default_region": "global"},
        )
        result = await view.current()
        self.assertEqual(
            result.values,
            {
                "default_region": "global",
                "origin": "legacy",
                "conflict": False,
                "raw_present": False,
            },
        )
        self.assertEqual(
            (
                await runtime.config_repository.current(
                    core_config_target("host-config")
                )
            ).values,
            {},
        )

    async def test_explicit_null_raw_core_rejects_with_safe_error_without_fallback(
        self,
    ):
        runtime = self._runtime()
        await self._start_with_candidate(runtime)
        target = core_config_target("host-config")

        def seed(unit):
            unit.execute(
                "INSERT INTO config_state(principal_id,module_id,revision) VALUES (?,?,2)",
                (target.principal_id, target.module_id),
            )
            unit.execute(
                "INSERT INTO config_entries(principal_id,module_id,field,value_json,revision) VALUES (?,?,?,'null',2)",
                (target.principal_id, target.module_id, "default_region"),
            )

        await runtime.database.executor.run_transaction(seed)
        view = CoreDefaultsView(
            runtime.admin_operations._core_configuration().view(),
            legacy_defaults={"default_region": "global"},
        )
        with self.assertRaises(ConfigurationValueError) as caught:
            await view.current()
        self.assertEqual(
            (caught.exception.field, caught.exception.code),
            ("default_region", "invalid_configuration_value"),
        )
        self.assertNotIn("null", str(caught.exception))
        self.assertEqual(
            (await runtime.config_repository.current(target)).values,
            {"default_region": None},
        )

    async def test_module_calendar_schema_isolation_and_invalid_writes(self):
        runtime = self._runtime()
        await self._start_with_candidate(runtime)
        _, context = await self._bootstrap(runtime)
        target = ConfigTarget("host-config", "ff14/ff14")
        coordinator = ConfigurationCoordinator(
            target,
            CALENDAR_CONFIG_FIELDS,
            runtime.config_repository,
            runtime.secret_store,
            admission=runtime.lifecycle.admission,
            validate_admin_grant=lambda grant: runtime.admin_authorization.validate_generation(
                grant, operation=AdminOperation.UPDATE_CONFIG
            ),
            publish_config=lambda *_args, **_kwargs: (),
            value_validators=CALENDAR_VALUE_VALIDATORS,
        )
        grant = await runtime.admin_authorization.authorize(
            AdminOperation.UPDATE_CONFIG, invocation=None, context=context
        )
        for name in (DAYS, TIMEZONE, DELIVERY_TIME):
            with self.assertRaisesRegex(ValueError, "require a value"):
                ConfigFieldUpdate(name, ConfigPatchMode.REPLACE, value=None)
        invalid = {
            DAYS: [True, False, 0, 31, "7", 7.0],
            TIMEZONE: ["", "Unknown/Bad", "../UTC", "UTC\n"],
            DELIVERY_TIME: ["8:00", "24:00", "00:60", "08:00\n"],
        }
        for name, values in invalid.items():
            for value in values:
                with self.subTest(field=name, value=value):
                    with self.assertRaises(ConfigurationValueError):
                        await coordinator.update_admin(
                            target,
                            ConfigPatch(
                                1,
                                (
                                    ConfigFieldUpdate(
                                        name, ConfigPatchMode.REPLACE, value=value
                                    ),
                                ),
                                CALENDAR_CONFIG_FIELDS,
                            ),
                            grant,
                        )
                    self.assertEqual((await coordinator.current()).revision, 1)
        valid = {DAYS: 30, TIMEZONE: "UTC", DELIVERY_TIME: "23:59"}
        result = await coordinator.update_admin(
            target,
            ConfigPatch(
                1,
                tuple(
                    ConfigFieldUpdate(name, ConfigPatchMode.REPLACE, value=value)
                    for name, value in valid.items()
                ),
                CALENDAR_CONFIG_FIELDS,
            ),
            grant,
        )
        self.assertEqual(result.values, valid)
        self.assertEqual((await runtime.core_defaults.current()).revision, 1)
        self.assertEqual(
            (
                await runtime.config_repository.current(
                    core_config_target("host-config")
                )
            ).values,
            {},
        )
        combined = CoreDefaultsConfigView(
            coordinator.view(), runtime.core_defaults, runtime.lifecycle.admission
        )
        self.assertEqual((await combined.current()).revision, 2)
