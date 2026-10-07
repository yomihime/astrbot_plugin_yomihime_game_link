"""Non-game declaration, exact deployment policy and persistent-data retention."""

import json
import tempfile
import time
import unittest
from pathlib import Path

from ygl_test_subject.api.administration import AdminAuthorizationDenied, AdminOperation
from ygl_test_subject.api.display import DisplayLimits
from ygl_test_subject.api.services import (
    ConfigFieldUpdate,
    ConfigPatch,
    ConfigPatchMode,
    ConfigTarget,
)
from ygl_test_subject.services.core_configuration import core_config_target
from ygl_test_subject.services.core_runtime import CoreRuntime

from tests.fixtures.settings_module import write_settings_module
from tests.services.test_admin_operations import _Codec, _MessagePort, _Renderer


class NeutralModuleSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self.temp.name)
        extensions = self.root / "extensions"
        self.owner = write_settings_module(extensions)
        write_settings_module(extensions, "other", "otherdemo")
        self.target = ConfigTarget("fixture", self.owner)
        self.core = CoreRuntime(
            database=self.root / "runtime.sqlite3",
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
            pump_interval=3600,
            cleanup_timeout=1,
            ordinary_config_resources={
                core_config_target("fixture"): {"default_region"},
                self.target: {"label"},
            },
            managed_module_owners={self.owner},
        )
        await self.core.start()
        self.ordinary = self.core.admin_authorization.register_source(
            "ordinary",
            resources=self.core.admin_operations.ordinary_resources(),
            operations={AdminOperation.READ_CONFIG, AdminOperation.UPDATE_CONFIG},
        )
        self.modules = self.core.admin_authorization.register_source(
            "modules",
            resources=self.core.admin_operations.module_resources(),
            operations={
                AdminOperation.LIST_MODULES,
                AdminOperation.SET_ENABLED,
                AdminOperation.UNLOAD_MODULE,
            },
        )

    async def asyncTearDown(self):
        await self.core.close(timeout=1)
        self.temp.cleanup()

    def proof(self, source):
        request = object()
        return source.issue(
            subject="synthetic",
            request=request,
            expiry=time.time() + 30,
            operations=source.operations,
            resources=source.resources,
            live=lambda actual: actual is request,
        )

    async def test_neutral_module_and_core_settings_survive_owner_unload_restore(self):
        ops = self.core.admin_operations
        context = self.proof(self.ordinary)
        catalog = await ops.ordinary_catalog(authorization=context)
        rows = await ops.ordinary_snapshot(authorization=context)
        self.assertEqual(set(rows), {"game_link/core", self.owner})
        other = next(f for f in catalog["fields"] if f["module_id"] == "other/demo")
        self.assertFalse(other["readable"])
        with self.assertRaises(AdminAuthorizationDenied):
            await ops.update_config(
                None,
                "other/demo",
                ConfigPatch(
                    1,
                    (
                        ConfigFieldUpdate(
                            "label", ConfigPatchMode.REPLACE, value="unauthorized"
                        ),
                    ),
                    (),
                ),
                authorization=context,
            )
        await ops.update_config(
            None,
            self.owner,
            ConfigPatch(
                rows[self.owner]["revision"],
                (
                    ConfigFieldUpdate(
                        "label", ConfigPatchMode.REPLACE, value="persisted"
                    ),
                ),
                ops.ordinary_declarations(self.target),
            ),
            authorization=context,
        )
        self.ordinary.end(context)
        context = self.proof(self.modules)
        with self.assertRaises(AdminAuthorizationDenied):
            await ops.ordinary_snapshot(authorization=context)
        await ops.set_enabled(
            None,
            self.owner,
            True,
            expected_registry_revision=self.core.registry.snapshot().revision,
            authorization=context,
        )
        saved = await self.core.config_repository.current(self.target)
        marker = self.root / "persistent-business-data"
        marker.write_bytes(b"retain")
        result = await ops.unload_module(
            None,
            self.owner,
            expected_registry_revision=self.core.registry.snapshot().revision,
            authorization=context,
        )
        self.assertEqual(result["state"], "unloaded")
        self.assertNotIn(self.owner, self.core.registry.snapshot().modules)
        self.assertEqual(
            (await self.core.config_repository.current(self.target)).values,
            saved.values,
        )
        self.assertEqual(marker.read_bytes(), b"retain")
        await ops.set_enabled(
            None,
            self.owner,
            True,
            expected_registry_revision=self.core.registry.snapshot().revision,
            authorization=context,
        )
        self.assertTrue(self.core.registry.is_active(self.owner))
        self.assertEqual(
            self.core.registry.snapshot().module(self.owner).manifest.pages[0].route_id,
            "items",
        )
        self.modules.end(context)


class SharedPackageRestoreTests(unittest.IsolatedAsyncioTestCase):
    proof = NeutralModuleSettingsTests.proof
    asyncTearDown = NeutralModuleSettingsTests.asyncTearDown

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self.temp.name)
        extensions = self.root / "extensions"
        self.owner = write_settings_module(extensions)
        manifest_path = extensions / "example/yomihime.manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        sibling = json.loads(json.dumps(manifest["modules"][0]))
        sibling["module_id"] = "second"
        sibling["route"] = "second"
        sibling["pages"][0]["entry"] = "second.js"
        sibling["resources"][0]["path"] = "second.js"
        (extensions / "example/second.js").write_bytes(
            (extensions / "example/page.js").read_bytes()
        )
        manifest["modules"].append(sibling)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.target = ConfigTarget("fixture", self.owner)
        self.core = CoreRuntime(
            database=self.root / "runtime.sqlite3",
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
            pump_interval=3600,
            cleanup_timeout=1,
            ordinary_config_resources={
                core_config_target("fixture"): {"default_region"},
                self.target: {"label"},
                ConfigTarget("fixture", "example/second"): {"label"},
            },
            managed_module_owners={self.owner, "example/second"},
        )
        await self.core.start()
        self.modules = self.core.admin_authorization.register_source(
            "modules",
            resources=self.core.admin_operations.module_resources(),
            operations={
                AdminOperation.LIST_MODULES,
                AdminOperation.SET_ENABLED,
                AdminOperation.UNLOAD_MODULE,
            },
        )

    async def test_restore_one_owner_does_not_reregister_unloaded_sibling(self):
        ops = self.core.admin_operations
        context = self.proof(self.modules)

        async def enabled(owner):
            return await ops.set_enabled(
                None,
                owner,
                True,
                expected_registry_revision=self.core.registry.snapshot().revision,
                authorization=context,
            )

        async def unload(owner):
            return await ops.unload_module(
                None,
                owner,
                expected_registry_revision=self.core.registry.snapshot().revision,
                authorization=context,
            )

        await enabled(self.owner)
        await enabled("example/second")
        second_identity = self.core.lifecycle.current_identity("example/second")
        await unload(self.owner)
        self.assertTrue(self.core.registry.is_active("example/second"))
        self.assertIs(
            self.core.lifecycle.current_identity("example/second"), second_identity
        )
        self.assertIn("example", self.core.extension_runtime._built)
        await unload("example/second")
        self.assertEqual(dict(self.core.registry.snapshot().modules), {})
        self.assertNotIn("example", self.core.extension_runtime._built)
        await enabled(self.owner)
        self.assertNotIn("example/second", self.core.registry.snapshot().modules)
        self.modules.end(context)
