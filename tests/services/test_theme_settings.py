"""Core-owned theme catalog and coherent persisted presentation preferences."""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.core.contracts.administration import (
    AdminAuthorizationDenied,
    AdminOperation,
)
from ygl_test_subject.core.contracts.services import ConfigFieldUpdate, ConfigPatch
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.ports import RevisionConflict
from ygl_test_subject.presentation.layout_renderer import list_themes
from ygl_test_subject.services.configuration.defaults import (
    CORE_CONFIG_FIELDS,
    CORE_MODULE_ID,
    IMAGE_THEME,
    MODULE_REPLY_THEME,
    ThemeSettingsView,
    core_config_target,
    module_configuration_fields,
)
from ygl_test_subject.services.configuration.operations import ConfigurationValueError
from ygl_test_subject.services.core_runtime import CoreRuntime
from ygl_test_subject.services.trusted_assembly import ReviewedAssembly

from tests.fixtures.settings_module import write_settings_module
from tests.services.test_admin_operations import _Codec, _MessagePort, _Renderer
from yomihime_game_link_sdk.declarations import ConfigField, ConfigUpdateMode
from yomihime_game_link_sdk.display import DisplayLimits
from yomihime_game_link_sdk.services import ConfigTarget


class ThemeSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        extensions = self.root / "extensions"
        self.owners = (
            write_settings_module(extensions),
            write_settings_module(extensions, "other", "otherdemo"),
        )
        self.core_target = core_config_target("fixture")
        self.targets = tuple(
            validate_contract(ConfigTarget("fixture", owner)) for owner in self.owners
        )
        self.resources = {
            self.core_target: {field.name for field in CORE_CONFIG_FIELDS},
            **{target: {"reply_theme", "label"} for target in self.targets},
        }
        self.arguments = dict(
            database=self.root / "core.sqlite3",
            extension_root=extensions,
            file_root=self.root / "files",
            secret_root=self.root / "secrets",
            secret_codec=_Codec(),
            http_transport=lambda request: request,
            renderer=_Renderer(),
            display_limits=validate_contract(DisplayLimits(2, 4096)),
            message_port=_MessagePort(),
            admin_context_validator=lambda *_: False,
            host_ingress_validator=lambda *_: False,
            config_principal_id="fixture",
            identity_namespace="fixture",
            ordinary_config_resources=self.resources,
            managed_module_owners=self.owners,
            pump_interval=3600,
            cleanup_timeout=1,
        )
        await self.start_core()

    async def start_core(self):
        self.core = CoreRuntime(**self.arguments)
        await self.core.start()
        self.source = self.core.admin_authorization.register_source(
            "theme-test",
            resources=self.resources,
            operations={AdminOperation.READ_CONFIG, AdminOperation.UPDATE_CONFIG},
        )
        self.view = ThemeSettingsView(
            self.core.config_repository,
            self.core.lifecycle.admission,
            principal_id="fixture",
        )

    async def asyncTearDown(self):
        await self.core.close(timeout=1)
        self.temp.cleanup()

    def proof(self, operation):
        request = object()
        return self.source.issue(
            subject="theme-test",
            request=request,
            expiry=time.time() + 30,
            operations={operation},
            resources=self.resources,
            live=lambda actual: actual is request,
        )

    async def read(self, name):
        proof = self.proof(AdminOperation.READ_CONFIG)
        try:
            return await getattr(self.core.admin_operations, name)(authorization=proof)
        finally:
            self.source.end(proof)

    async def save(self, target, value, *, clear=False, revision=None):
        current = await self.core.config_repository.current(target)
        field = IMAGE_THEME if target == self.core_target else MODULE_REPLY_THEME
        proof = self.proof(AdminOperation.UPDATE_CONFIG)
        try:
            return await self.core.admin_operations.update_config(
                None,
                target.module_id,
                ConfigPatch(
                    revision if revision is not None else current.revision,
                    (
                        ConfigFieldUpdate(
                            field.name,
                            ConfigUpdateMode.CLEAR
                            if clear
                            else ConfigUpdateMode.REPLACE,
                            **({} if clear else {"value": value}),
                        ),
                    ),
                    self.core.admin_operations.ordinary_declarations(target),
                ),
                authorization=proof,
            )
        finally:
            self.source.end(proof)

    async def test_catalog_uses_actual_core_choices_and_absence_is_follow_core(self):
        catalog = await self.read("ordinary_catalog")
        actual = [
            {"value": option.id, "label": option.label} for option in list_themes()
        ]
        selected = [
            field for field in catalog["fields"] if field["group"] == "reply_style"
        ]
        self.assertEqual(len(selected), 3)
        for field in selected:
            self.assertTrue(field["readable"] and field["editable"])
            self.assertEqual(field["description"], "图片主题")
            self.assertEqual(
                field["value_schema"]["enum"], [option.id for option in list_themes()]
            )
            self.assertEqual(
                field["choices"],
                actual
                if field["module_id"] == CORE_MODULE_ID
                else [{"value": None, "label": "跟随核心"}, *actual],
            )
        snapshot = await self.read("ordinary_snapshot")
        for owner in self.owners:
            self.assertEqual(
                snapshot[owner]["fields"]["reply_theme"],
                {
                    "value": None,
                    "state": "valid",
                    "present": False,
                    "source": "default",
                },
            )
        initial = await self.view.current(self.owners[0])
        self.assertEqual(
            (initial.core_theme, initial.module_theme), ("warm_paper", None)
        )
        with self.assertRaises(FrozenInstanceError):
            initial.core_theme = "cool_white"
        self.assertEqual(
            (await self.core.config_repository.current(self.core_target)).values, {}
        )
        self.assertEqual(
            (await self.core.config_repository.current(self.targets[0])).values, {}
        )

    async def test_cas_persistence_isolation_core_help_and_clear(self):
        await self.save(self.core_target, "cool_white")
        original = await self.view.current(self.owners[0])
        await self.save(self.targets[0], "deep_indigo")
        module = await self.view.current(self.owners[0])
        other = await self.view.current(self.owners[1])
        core_help = await self.view.current()
        self.assertEqual(
            (module.core_theme, module.module_theme), ("cool_white", "deep_indigo")
        )
        self.assertEqual((other.core_theme, other.module_theme), ("cool_white", None))
        self.assertEqual(
            (core_help.core_theme, core_help.module_theme, core_help.module_revision),
            ("cool_white", None, None),
        )
        self.assertEqual(await self.view.current(CORE_MODULE_ID), core_help)
        with self.assertRaises(RevisionConflict):
            await self.save(
                self.targets[0], "warm_paper", revision=original.module_revision
            )
        await self.core.close(timeout=1)
        await self.start_core()
        self.assertEqual(await self.view.current(self.owners[0]), module)
        await self.save(self.targets[0], None, clear=True)
        self.assertEqual((await self.view.current(self.owners[0])).module_theme, None)
        self.assertNotIn(
            "reply_theme",
            (await self.core.config_repository.current(self.targets[0])).values,
        )
        self.assertEqual(
            (await self.view.current(self.owners[1])).core_theme, "cool_white"
        )

    async def test_invalid_explicit_values_reject_and_require_authorization(self):
        for target in (self.core_target, self.targets[0]):
            for value in (None, "unknown", "", False, 1, {"palette": "custom"}):
                with (
                    self.subTest(target=target, value=value),
                    self.assertRaises(ValueError),
                ):
                    await self.save(target, value)
        with self.assertRaises(AdminAuthorizationDenied):
            await self.core.admin_operations.ordinary_catalog(authorization=None)
        proof = self.proof(AdminOperation.READ_CONFIG)
        self.source.end(proof)
        with self.assertRaises(AdminAuthorizationDenied):
            await self.core.admin_operations.ordinary_snapshot(authorization=proof)
        target = self.targets[0]

        def seed_invalid(unit):
            unit.execute(
                "INSERT INTO config_entries(principal_id,module_id,field,value_json,revision) VALUES (?,?,?,?,1)",
                (
                    target.principal_id,
                    target.module_id,
                    "reply_theme",
                    json.dumps(None),
                ),
            )

        await self.core.database.executor.run_transaction(seed_invalid)
        with self.assertRaises(ConfigurationValueError):
            await self.view.current(target.module_id)
        snapshot = await self.read("ordinary_snapshot")
        self.assertEqual(
            snapshot[target.module_id]["fields"]["reply_theme"]["state"], "invalid"
        )
        await self.save(target, None, clear=True)
        self.assertIsNone((await self.view.current(target.module_id)).module_theme)

    async def test_theme_pair_read_holds_one_mutation_gate(self):
        entered, release, writing = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original = self.core.config_repository.current

        async def delayed(target, *args, **kwargs):
            snapshot = await original(target, *args, **kwargs)
            if target == self.core_target and not entered.is_set():
                entered.set()
                await release.wait()
            return snapshot

        async def write():
            writing.set()
            return await self.save(self.targets[0], "deep_indigo")

        with patch.object(self.core.config_repository, "current", delayed):
            reading = asyncio.create_task(self.view.current(self.owners[0]))
            await entered.wait()
            saving = asyncio.create_task(write())
            await writing.wait()
            await asyncio.sleep(0)
            self.assertFalse(saving.done())
            release.set()
            before = await reading
            await saving
        self.assertEqual((before.core_theme, before.module_theme), ("warm_paper", None))
        self.assertEqual(
            (await self.view.current(self.owners[0])).module_theme, "deep_indigo"
        )

    async def test_invalid_core_cannot_be_hidden_by_valid_module_override(self):
        await self.save(self.targets[0], "deep_indigo")
        await self.core.config_repository.current(self.core_target)
        for invalid in (None, "unknown"):

            def seed_invalid(unit):
                unit.execute(
                    "INSERT OR REPLACE INTO config_entries(principal_id,module_id,field,value_json,revision) VALUES (?,?,?,?,1)",
                    ("fixture", CORE_MODULE_ID, "image_theme", json.dumps(invalid)),
                )

            await self.core.database.executor.run_transaction(seed_invalid)
            with self.assertRaises(ConfigurationValueError):
                await self.view.current(self.owners[0])
            with self.assertRaises(ConfigurationValueError):
                await self.view.current()
        await self.save(self.core_target, None, clear=True)
        repaired = await self.view.current(self.owners[0])
        self.assertEqual(
            (repaired.core_theme, repaired.module_theme), ("warm_paper", "deep_indigo")
        )

    async def test_loaded_module_theme_write_preserves_capability_health(self):
        resources = self.core.admin_operations.module_resources(self.owners[0])
        source = self.core.admin_authorization.register_source(
            "theme-lifecycle-test",
            resources=resources,
            operations={AdminOperation.SET_ENABLED},
        )
        request = object()
        proof = source.issue(
            subject="theme-test",
            request=request,
            expiry=time.time() + 30,
            operations={AdminOperation.SET_ENABLED},
            resources=resources,
            live=lambda actual: actual is request,
        )
        try:
            await self.core.admin_operations.set_enabled(
                None,
                self.owners[0],
                True,
                expected_registry_revision=self.core.registry.snapshot().revision,
                authorization=proof,
            )
        finally:
            source.end(proof)
        before = self.core.health_resolver.current(self.owners[0], "inspect")
        for value in ("cool_white", "deep_indigo", None):
            with patch.object(
                self.core.health_resolver,
                "publish_config",
                wraps=self.core.health_resolver.publish_config,
            ) as published:
                saved = await self.save(self.targets[0], value, clear=value is None)
            published.assert_called_once()
            args, kwargs = published.call_args
            self.assertEqual(args[1].revision, saved.revision)
            self.assertNotIn("reply_theme", args[1].values)
            self.assertEqual(kwargs["changed_fields"], frozenset())
            self.assertEqual(
                self.core.health_resolver.current(self.owners[0], "inspect"), before
            )
            self.assertEqual(
                (await self.view.current(self.owners[0])).module_theme, value
            )

    async def test_generic_core_field_is_injected_without_mutating_manifest(self):
        manifests = {
            owner: self.core.admin_operations._selection(
                owner, self.core.registry.snapshot()
            ).manifest
            for owner in self.owners
        }
        for manifest in manifests.values():
            self.assertNotIn(
                "reply_theme", [field.name for field in manifest.config_fields]
            )
            fields = module_configuration_fields(manifest.config_fields)
            self.assertIs(fields[-1], MODULE_REPLY_THEME)
        await self.save(self.targets[0], "cool_white")
        services = self.core.module_services.for_candidate(
            self.owners[0], manifests[self.owners[0]]
        )
        self.assertEqual(
            (await services.config.current()).values["reply_theme"], "cool_white"
        )
        owner_field = validate_contract(ConfigField("default_region"))
        self.assertEqual(
            module_configuration_fields((owner_field,)),
            (owner_field, MODULE_REPLY_THEME),
        )
        for reserved in (
            "reply_theme",
            "image_theme",
            "core_defaults",
        ):
            with self.assertRaises(ValueError):
                module_configuration_fields((validate_contract(ConfigField(reserved)),))
        assembly = ReviewedAssembly(
            module_id=self.owners[0],
            manifests=manifests,
            gates={},
            source_hosts={},
            credential_policies=(),
            managed_credentials=(),
            credential_realms={},
            validators={},
            public_bindings={},
            support=SimpleNamespace(ordinary_config_fields=("label",)),
        )
        self.assertEqual(
            assembly.ordinary_resources("fixture"),
            {
                self.targets[0]: {"label", "reply_theme"},
                self.targets[1]: {"reply_theme"},
            },
        )
