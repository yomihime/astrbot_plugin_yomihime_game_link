from __future__ import annotations

import json
import unittest
from dataclasses import replace

from ygl_test_subject.core.contracts.administration import (
    AdminAuthorizationDenied,
    AdminOperation,
)
from ygl_test_subject.core.contracts.services import ConfigFieldUpdate, ConfigPatch
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.ports import RevisionConflict
from ygl_test_subject.modules.ff14.config import (
    CALENDAR_CONFIG_FIELDS,
    CALENDAR_VALUE_VALIDATORS,
    DAYS,
    DELIVERY_TIME,
    SYNC_INTERVAL,
    TIMEZONE,
    FF14ConfigSnapshot,
)
from ygl_test_subject.services.configuration.defaults import (
    CORE_CONFIG_FIELDS,
    CORE_MODULE_ID,
    core_config_target,
)
from ygl_test_subject.services.configuration.operations import (
    ConfigurationCoordinator,
    ConfigurationValueError,
)

import tests.services.test_admin_operations as runtime_fixture
from yomihime_game_link_sdk.declarations import ConfigField, ConfigUpdateMode
from yomihime_game_link_sdk.services import ConfigTarget


class CoreDefaultsRuntimeTests(unittest.IsolatedAsyncioTestCase):
    # The retired game-default DTO has no compatibility shim. These tests keep
    # current Core auth/CAS/repair and Module field isolation behavior.
    setUp = runtime_fixture.AdminOperationsRuntimeTests.setUp
    asyncTearDown = runtime_fixture.AdminOperationsRuntimeTests.asyncTearDown
    _runtime = runtime_fixture.AdminOperationsRuntimeTests._runtime
    _bootstrap = runtime_fixture.AdminOperationsRuntimeTests._bootstrap
    _start_with_candidate = (
        runtime_fixture.AdminOperationsRuntimeTests._start_with_candidate
    )

    async def _seed_invalid_defaults(self, target, values):
        runtime = self._runtime()
        if "default_region" in values:
            from ygl_test_subject.services.configuration.legacy_copy import (
                LegacyConfigCopyPolicy,
            )

            runtime.admin_operations._legacy_config_policies = {
                "sample.region": LegacyConfigCopyPolicy(
                    "sample.region",
                    target,
                    "default_region",
                    "admin/mod",
                    "mode",
                    {"type": "string"},
                )
            }
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
        runtime, context = await self._seed_invalid_defaults(
            target,
            {
                "image_theme": "private-invalid-value",
                "default_region": "legacy-preserved",
            },
        )
        with self.assertRaises(AdminAuthorizationDenied):
            await runtime.admin_facade.config_snapshot(None, CORE_MODULE_ID)
        summary = await runtime.admin_facade.config_snapshot(
            None, CORE_MODULE_ID, authorization=context
        )
        self.assertEqual(summary.revision, 2)
        self.assertEqual(set(summary.fields), {"image_theme"})
        self.assertNotIn("private-invalid-value", repr(summary))
        patch = ConfigPatch(
            2,
            (
                ConfigFieldUpdate(
                    "image_theme", ConfigUpdateMode.REPLACE, value="warm_paper"
                ),
            ),
            CORE_CONFIG_FIELDS,
        )
        await runtime.admin_facade.update_config(
            None, CORE_MODULE_ID, patch, authorization=context
        )
        raw = await runtime.config_repository.current(target)
        self.assertEqual(raw.revision, 3)
        self.assertEqual(
            raw.values,
            {"image_theme": "warm_paper", "default_region": "legacy-preserved"},
        )

    async def test_admin_can_replace_invalid_core_without_bypassing_patch_guards(self):
        target = core_config_target("host-config")
        runtime, context = await self._seed_invalid_defaults(
            target, {"image_theme": None}
        )

        def patch(mode, value=None, revision=2):
            return ConfigPatch(
                revision,
                (ConfigFieldUpdate("image_theme", mode, value=value),),
                CORE_CONFIG_FIELDS,
            )

        for action, error in (
            (patch(ConfigUpdateMode.REPLACE, True), ConfigurationValueError),
            (patch(ConfigUpdateMode.KEEP), ConfigurationValueError),
            (patch(ConfigUpdateMode.REPLACE, "warm_paper", 1), RevisionConflict),
        ):
            with self.assertRaises(error):
                await runtime.admin_facade.update_config(
                    None, CORE_MODULE_ID, action, authorization=context
                )
            self.assertEqual(
                (await runtime.config_repository.current(target)).values,
                {"image_theme": None},
            )
        valid = patch(ConfigUpdateMode.REPLACE, "warm_paper")
        with self.assertRaises(AdminAuthorizationDenied):
            await runtime.admin_facade.update_config(None, CORE_MODULE_ID, valid)
        updated = await runtime.admin_facade.update_config(
            None, CORE_MODULE_ID, valid, authorization=context
        )
        self.assertEqual(updated.revision, 3)
        self.assertEqual(
            (await runtime.config_repository.current(target)).values,
            {"image_theme": "warm_paper"},
        )

    async def test_admin_can_clear_invalid_core_and_restore_missing_key_rules(self):
        target = core_config_target("host-config")
        runtime, context = await self._seed_invalid_defaults(
            target, {"image_theme": None}
        )
        patch = ConfigPatch(
            2,
            (ConfigFieldUpdate("image_theme", ConfigUpdateMode.CLEAR),),
            CORE_CONFIG_FIELDS,
        )
        updated = await runtime.admin_facade.update_config(
            None, CORE_MODULE_ID, patch, authorization=context
        )
        self.assertEqual(updated.revision, 3)
        self.assertEqual((await runtime.config_repository.current(target)).values, {})
        self.assertEqual(
            (await runtime.theme_settings.current()).core_theme, "warm_paper"
        )

    async def test_module_view_is_readonly_and_has_no_core_defaults_object(self):
        runtime = self._runtime()
        await self._start_with_candidate(runtime)
        manifest = runtime.extension_runtime.candidate(
            "admin"
        ).package.manifest.modules[0]
        services = runtime.module_services.for_candidate("admin/mod", manifest)
        first = await services.config.current()
        self.assertEqual(first.revision, 1)
        self.assertNotIn("core_defaults", first.values)
        for attr in (
            "update",
            "update_admin",
            "clear",
            "_core",
            "_target",
            "__current",
        ):
            self.assertFalse(hasattr(services.config, attr))
        self.assertFalse(hasattr(runtime, "core_defaults"))
        with self.assertRaises(TypeError):
            first.values["fake"] = "new"
        for name in ("core_defaults", "image_theme", "reply_theme"):
            with self.assertRaises(ValueError):
                runtime.module_services.for_candidate(
                    "admin/mod", replace(manifest, config_fields=(ConfigField(name),))
                )
        with self.assertRaises(ValueError):
            runtime.module_services.for_candidate(
                CORE_MODULE_ID, replace(manifest, module_id="core")
            )
        self.assertEqual(
            (
                await runtime.config_repository.current(
                    ConfigTarget("host-config", "admin/mod")
                )
            ).values,
            {},
        )

    async def test_missing_raw_core_uses_theme_default_without_persistence(self):
        runtime = self._runtime()
        await self._start_with_candidate(runtime)
        self.assertEqual(
            (await runtime.theme_settings.current()).core_theme, "warm_paper"
        )
        self.assertEqual(
            (
                await runtime.config_repository.current(
                    core_config_target("host-config")
                )
            ).values,
            {},
        )

    async def test_invalid_legacy_region_is_preserved_without_entering_core_contract(
        self,
    ):
        target = core_config_target("host-config")
        runtime, context = await self._seed_invalid_defaults(
            target, {"default_region": None}
        )
        summary = await runtime.admin_facade.config_snapshot(
            None, CORE_MODULE_ID, authorization=context
        )
        self.assertEqual(summary.fields, {"image_theme": "unset"})
        self.assertFalse(hasattr(summary, "default_region"))
        self.assertEqual(
            (await runtime.config_repository.current(target)).values,
            {"default_region": None},
        )

    async def test_core_admin_read_fences_generation_after_repository_read(self):
        from unittest.mock import patch

        runtime = self._runtime()
        await self._start_with_candidate(runtime)
        _, context = await self._bootstrap(runtime)
        original = runtime.config_repository.current

        async def rotated(target):
            snapshot = await original(target)
            await runtime.admin_credential_repository.rotate(
                1, runtime_fixture._digest(runtime_fixture.token_urlsafe(32))
            )
            return snapshot

        with patch.object(runtime.config_repository, "current", rotated):
            with self.assertRaises(AdminAuthorizationDenied):
                await runtime.admin_facade.config_snapshot(
                    None, CORE_MODULE_ID, authorization=context
                )

    async def test_unrelated_or_keep_patch_cannot_hide_invalid_module_predecessor(self):
        target = validate_contract(ConfigTarget("host-config", "ff14/ff14"))
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
            ConfigFieldUpdate(DAYS, ConfigUpdateMode.REPLACE, value=8),
            ConfigFieldUpdate(TIMEZONE, ConfigUpdateMode.KEEP),
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
                (ConfigFieldUpdate(TIMEZONE, ConfigUpdateMode.REPLACE, value="UTC"),),
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
                        "image_theme", ConfigUpdateMode.REPLACE, value=value
                    ),
                ),
                CORE_CONFIG_FIELDS,
                target=target,
            )

        with self.assertRaises(AdminAuthorizationDenied):
            await runtime.admin_facade.update_config(
                None, CORE_MODULE_ID, patch("warm_paper")
            )
        with self.assertRaises((AdminAuthorizationDenied, ValueError)):
            await runtime.admin_facade.update_config(
                None,
                CORE_MODULE_ID,
                patch("warm_paper", target=core_config_target("other-principal")),
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
            None, CORE_MODULE_ID, patch("warm_paper"), authorization=context
        )
        self.assertEqual(updated.revision, 2)
        raw = await runtime.config_repository.current(core_config_target("host-config"))
        self.assertEqual(raw.values, {"image_theme": "warm_paper"})
        with self.assertRaises(RevisionConflict):
            await runtime.admin_facade.update_config(
                None, CORE_MODULE_ID, patch("cool_white"), authorization=context
            )
        with self.assertRaises(ValueError):
            await runtime.admin_facade.update_config(
                None, "admin/mod", patch("cool_white"), authorization=context
            )
        with self.assertRaises(AdminAuthorizationDenied):
            await runtime.admin_facade.update_config(
                None,
                "unknown/mod",
                ConfigPatch(
                    1,
                    (ConfigFieldUpdate("mode", ConfigUpdateMode.REPLACE, value="ok"),),
                    (validate_contract(ConfigField("mode")),),
                ),
                authorization=context,
            )
        self.assertEqual(
            (
                await runtime.config_repository.current(
                    validate_contract(ConfigTarget("host-config", "admin/mod"))
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

    async def test_module_calendar_schema_isolation_and_invalid_writes(self):
        runtime = self._runtime()
        await self._start_with_candidate(runtime)
        _, context = await self._bootstrap(runtime)
        target = validate_contract(ConfigTarget("host-config", "ff14/ff14"))
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
        for name in (DAYS, TIMEZONE, DELIVERY_TIME, SYNC_INTERVAL):
            with self.assertRaisesRegex(ValueError, "require a value"):
                ConfigFieldUpdate(name, ConfigUpdateMode.REPLACE, value=None)
        invalid = {
            DAYS: [True, False, 0, 31, "7", 7.0],
            TIMEZONE: ["", "Unknown/Bad", "../UTC", "UTC\n"],
            DELIVERY_TIME: ["8:00", "24:00", "00:60", "08:00\n"],
            SYNC_INTERVAL: [True, False, 0, 900, 21601, 86401, "21600", 21600.0],
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
                                        name, ConfigUpdateMode.REPLACE, value=value
                                    ),
                                ),
                                CALENDAR_CONFIG_FIELDS,
                            ),
                            grant,
                        )
                    self.assertEqual((await coordinator.current()).revision, 1)
        valid = {
            DAYS: 30,
            TIMEZONE: "UTC",
            DELIVERY_TIME: "23:59",
            SYNC_INTERVAL: 43200,
        }
        result = await coordinator.update_admin(
            target,
            ConfigPatch(
                1,
                tuple(
                    ConfigFieldUpdate(name, ConfigUpdateMode.REPLACE, value=value)
                    for name, value in valid.items()
                ),
                CALENDAR_CONFIG_FIELDS,
            ),
            grant,
        )
        self.assertEqual(result.values, valid)
        self.assertEqual(
            (
                await runtime.config_repository.current(
                    core_config_target("host-config")
                )
            ).revision,
            1,
        )
        self.assertEqual(
            (
                await runtime.config_repository.current(
                    core_config_target("host-config")
                )
            ).values,
            {},
        )
        combined = coordinator.view()
        self.assertEqual((await combined.current()).revision, 2)
        self.assertEqual(
            FF14ConfigSnapshot.from_values(
                (await combined.current()).values
            ).calendar_sync_interval_seconds,
            43200,
        )
        update = ConfigFieldUpdate(SYNC_INTERVAL, ConfigUpdateMode.REPLACE, value=86400)
        with self.assertRaises(RevisionConflict):
            await coordinator.update_admin(
                target, ConfigPatch(1, (update,), CALENDAR_CONFIG_FIELDS), grant
            )
        self.assertEqual((await coordinator.current()).revision, 2)
        self.assertEqual((await coordinator.current()).values[SYNC_INTERVAL], 43200)
        await coordinator.update_admin(
            target, ConfigPatch(2, (update,), CALENDAR_CONFIG_FIELDS), grant
        )
        self.assertEqual((await combined.current()).revision, 3)
        self.assertEqual(
            FF14ConfigSnapshot.from_values(
                (await combined.current()).values
            ).calendar_sync_interval_seconds,
            86400,
        )
        await coordinator.update_admin(
            target,
            ConfigPatch(
                3,
                (ConfigFieldUpdate(SYNC_INTERVAL, ConfigUpdateMode.CLEAR),),
                CALENDAR_CONFIG_FIELDS,
            ),
            grant,
        )
        self.assertEqual((await combined.current()).revision, 4)
        self.assertEqual(
            FF14ConfigSnapshot.from_values(
                (await combined.current()).values
            ).calendar_sync_interval_seconds,
            21600,
        )

    async def test_retained_source_cannot_be_patched_or_cleared_or_leaked(self):
        target = core_config_target("host-config")
        runtime, context = await self._seed_invalid_defaults(
            target, {"default_region": "legacy-value"}
        )
        coordinator = runtime.admin_operations._core_configuration()
        view = await coordinator.current()
        self.assertNotIn("default_region", view.values)
        grant = await runtime.admin_authorization.authorize(
            AdminOperation.UPDATE_CONFIG, invocation=None, context=context
        )
        for mode in (ConfigUpdateMode.CLEAR, ConfigUpdateMode.REPLACE):
            update = ConfigFieldUpdate(
                "default_region",
                mode,
                value="new" if mode is ConfigUpdateMode.REPLACE else None,
            )
            with self.assertRaises(ValueError):
                ConfigPatch(2, (update,), CORE_CONFIG_FIELDS)
            forged = ConfigPatch(
                2, (update,), (ConfigField("default_region", False, "retained source"),)
            )
            with self.assertRaises((ValueError, AdminAuthorizationDenied)):
                await coordinator.update_admin(target, forged, grant)
        self.assertEqual(
            (await runtime.config_repository.current(target)).values,
            {"default_region": "legacy-value"},
        )
        from yomihime_game_link_sdk import (
            ConfigSnapshot,
            SecretMetadata,
            SecretMetadataState,
        )

        with self.assertRaises(ValueError):
            coordinator._validate_snapshot_fields(
                ConfigSnapshot(
                    2,
                    {},
                    (
                        SecretMetadata(
                            "default_region", None, 2, SecretMetadataState.TOMBSTONED
                        ),
                    ),
                    target,
                )
            )
        with self.assertRaises(ValueError):
            coordinator._validate_snapshot_fields(
                ConfigSnapshot(2, {"unknown_row": "private"}, target=target)
            )
