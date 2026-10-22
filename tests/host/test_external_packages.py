"""Real Core, SQLite, safe disk capture and SDK factories; no Host or network."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import unittest
import uuid
from pathlib import Path
from time import time
from types import SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.adapters.astrbot.bundled import DEFAULT_BUNDLE_SELECTION
from ygl_test_subject.adapters.astrbot.runtime import PLUGIN_NAME, AstrBotRuntime
from ygl_test_subject.core.contracts.administration import AdminOperation
from ygl_test_subject.infrastructure.private_storage import resolve_private_storage
from ygl_test_subject.infrastructure.windows.private_storage import (
    create_private_directory,
    identity_private_base,
)
from ygl_test_subject.services.extension_runtime import ExtensionCleanupPending

from yomihime_game_link_sdk.host import ExtensionHostError
from yomihime_game_link_sdk.services import HealthStatus

ROOT = Path(__file__).resolve().parents[2]


class _Font:
    def __init__(self, _root):
        pass

    def provider(self):
        return None

    def start(self):
        pass

    async def close(self):
        pass


class _Transport:
    closed = False

    async def request(self, _request):
        raise AssertionError("offline test must not request a network resource")

    request_credential_exchange = request

    async def close(self):
        self.closed = True


class _Context:
    def __init__(self):
        self.stars = {}

    def get_registered_star(self, name):
        return self.stars.get(name)

    async def send_message(self, *_args):
        return True


SOURCE = """from yomihime_game_link_sdk.services import ModuleHandlers, HealthReport
from .shared import VALUE
class Instance:
    def handlers(self): return ModuleHandlers(capabilities={}, collectors={}, evaluators={})
    async def start(self): self.started = VALUE
    async def stop(self): self.started = None
    async def check_health(self): return HealthReport(capabilities={})
class Factory:
    async def create(self, services):
        self.services = services
        return Instance()
"""


@unittest.skipUnless(os.name == "nt", "native Windows capture and private storage")
class ExternalPackageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = create_private_directory(
            identity_private_base(), "ygl-external-test-" + uuid.uuid4().hex
        )
        self.addCleanup(shutil.rmtree, self.root)
        self.installation = self.root / "dlc"
        self.package = self.installation / "packages" / "steam_dlc"
        self.package.mkdir(parents=True)
        (self.installation / "main.py").write_text("# outer plugin\n", encoding="utf-8")
        self.owner = object()
        self.context = _Context()
        self.metadata = SimpleNamespace(
            name="external_dlc",
            activated=True,
            star_cls=self.owner,
            module=SimpleNamespace(__file__=str(self.installation / "main.py")),
        )
        self.context.stars[self.metadata.name] = self.metadata
        self.manifest = {
            "schema_version": 1,
            "package_id": "steam_dlc",
            "package_version": "1.0.0",
            "contract_version": "2.0",
            "author": "test",
            "license": "MIT",
            "source": "https://example.invalid/test",
            "modules": [
                {
                    "module_id": name,
                    "route": name,
                    "category": "platform",
                    "factory_entry": "module:Factory",
                    "module_version": "1.0.0",
                    "capabilities": [],
                    "commands": [],
                    "sources": [],
                }
                for name in ("steam", "dota2", "cs2", "deadlock")
            ],
        }
        self._write_manifest()
        (self.package / "module.py").write_text(SOURCE, encoding="utf-8")
        (self.package / "shared.py").write_text("VALUE = 17\n", encoding="utf-8")
        (self.root / "core-data").mkdir()
        self.runtime = AstrBotRuntime(
            self.context,
            plugin_root=ROOT,
            data_dir=self.root / "core-data",
            font_manager_factory=_Font,
            http_transport_factory=_Transport,
            bundled_selection=None,
        )
        self.addAsyncCleanup(self.runtime.terminate)
        self.key_patch = patch(
            "ygl_test_subject.adapters.astrbot.runtime.EnvironmentKeyProvider.get_key",
            return_value=bytes(range(32)),
        )
        self.key_patch.start()
        self.addCleanup(self.key_patch.stop)
        private = create_private_directory(self.root, "private-storage")
        self.storage_patch = patch(
            "ygl_test_subject.adapters.astrbot.runtime.resolve_private_storage",
            lambda path: resolve_private_storage(path, private_base=private),
        )
        self.storage_patch.start()
        self.addCleanup(self.storage_patch.stop)
        await self.runtime.initialize()
        self.host = self.runtime.extension_host
        self.assertIsNotNone(self.host)
        self.core = self.runtime.core_runtime

    def _write_manifest(self):
        (self.package / "yomihime.manifest.json").write_text(
            json.dumps(self.manifest), encoding="utf-8"
        )

    async def _register(self):
        return await self.host.register_package(
            plugin_name=self.metadata.name,
            plugin_instance=self.metadata.star_cls,
            package_root=str(self.package),
        )

    async def test_four_modules_real_capture_idempotency_and_exact_old_lease(self):
        first = await self._register()
        self.assertTrue(first.active)
        self.assertIs(await self._register(), first)
        first_epochs = {}
        for name in ("steam", "dota2", "cs2", "deadlock"):
            owner = "steam_dlc/" + name
            module = self.core.registry.snapshot().module(owner)
            self.assertTrue(module.enabled)
            self.assertEqual(self.core.lifecycle.instance(owner).started, 17)
            first_epochs[owner] = module.epoch
        self.assertEqual(len(self.core.admin_operations.module_resources()), 4)
        source_count = len(self.core.admin_authorization._sources)
        self.core.extension_runtime.scan()
        self.assertIsNotNone(self.core.extension_runtime.candidate("steam_dlc"))
        self.metadata.star_cls = object()
        replacement = await self._register()
        self.assertEqual(len(self.core.admin_authorization._sources), source_count)
        self.assertFalse(first.active)
        self.assertTrue(replacement.active)
        for owner, epoch in first_epochs.items():
            self.assertGreater(self.core.registry.snapshot().module(owner).epoch, epoch)
        await self.host.unregister_package(first)
        self.assertTrue(replacement.active)
        await self.host.unregister_package(replacement)
        self.assertFalse(replacement.active)
        self.assertFalse(self.core.registry.snapshot().modules)
        self.assertIsNone(self.core.extension_runtime.candidate("steam_dlc"))
        self.assertTrue(
            (
                await self.core.runtime_repository.current_intent("steam_dlc", "steam")
            ).desired_enabled
        )

    async def test_wrong_instance_and_path_rejected_before_registration(self):
        with self.assertRaisesRegex(ExtensionHostError, "plugin_instance_unavailable"):
            await self.host.register_package(
                plugin_name="external_dlc",
                plugin_instance=object(),
                package_root=str(self.package),
            )
        with self.assertRaisesRegex(ExtensionHostError, "package_root_rejected"):
            await self.host.register_package(
                plugin_name="external_dlc",
                plugin_instance=self.owner,
                package_root=str(ROOT / "modules" / "ff14"),
            )
        self.assertFalse(self.core.registry.snapshot().modules)

    async def test_same_runtime_source_change_requires_restart(self):
        lease = await self._register()
        (self.package / "shared.py").write_text("VALUE = 23\n", encoding="utf-8")
        with self.assertRaisesRegex(ExtensionHostError, "restart_required"):
            await self._register()
        self.assertTrue(lease.active)
        self.assertEqual(self.core.lifecycle.instance("steam_dlc/steam").started, 17)

    async def test_core_close_revokes_bridge_and_stale_lease(self):
        lease = await self._register()
        await self.runtime.terminate()
        self.assertFalse(lease.active)
        with self.assertRaisesRegex(ExtensionHostError, "core_unavailable"):
            await self._register()
        await self.host.unregister_package(lease)

    async def test_no_credential_source_health_and_module_policy_are_isolated(self):
        self.manifest["modules"][1]["sources"] = [
            {"source_id": "public_api", "host": "api.opendota.com"}
        ]
        self._write_manifest()
        await self._register()
        health = await self.runtime._source_health("steam_dlc/dota2", "public_api")
        self.assertIs(health.status, HealthStatus.AVAILABLE)
        other = await self.runtime._source_health("steam_dlc/steam", "public_api")
        self.assertIs(other.status, HealthStatus.UNAVAILABLE)
        resources = self.core.admin_operations.module_resources("steam_dlc/dota2")
        target = next(iter(resources))
        self.assertEqual(target.principal_id, PLUGIN_NAME)
        source = self.runtime._module_admin_source
        self.assertIn(target, source.resources)
        self.assertIn(AdminOperation.UNLOAD_MODULE, source.operations)

    async def test_disabled_user_intent_survives_dlc_and_core_reconnect(self):
        first = await self._register()
        source = self.runtime._module_admin_source
        resources = self.core.admin_operations.module_resources("steam_dlc/steam")
        context = source.issue(
            subject="synthetic-admin",
            request=self,
            expiry=time() + 30,
            operations={AdminOperation.SET_ENABLED},
            resources=resources,
            live=lambda request: request is self,
        )
        try:
            await self.core.admin_operations.set_enabled(
                None,
                "steam_dlc/steam",
                False,
                expected_registry_revision=self.core.registry.snapshot().revision,
                authorization=context,
            )
        finally:
            source.end(context)
        await self.host.unregister_package(first)
        second = await self._register()
        self.assertTrue(second.active)
        self.assertFalse(
            self.core.registry.snapshot().module("steam_dlc/steam").enabled
        )
        old_host = self.host
        await self.runtime.terminate()
        await self.runtime.initialize()
        self.host = self.runtime.extension_host
        self.core = self.runtime.core_runtime
        self.assertIsNot(self.host, old_host)
        self.assertFalse(second.active)
        third = await self._register()
        self.assertTrue(third.active)
        self.assertFalse(
            self.core.registry.snapshot().module("steam_dlc/steam").enabled
        )
        self.assertTrue(self.core.registry.snapshot().module("steam_dlc/dota2").enabled)
        await old_host.unregister_package(second)
        self.assertTrue(third.active)

    async def test_failed_second_factory_leaves_no_partial_route_or_policy(self):
        (self.package / "module.py").write_text(
            SOURCE.replace(
                "self.services = services",
                "self.calls = getattr(self, 'calls', 0) + 1\n"
                "        if self.calls == 2: raise RuntimeError('synthetic failure')",
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
            await self._register()
        self.assertFalse(self.core.registry.snapshot().modules)
        self.assertIsNone(self.core.extension_runtime.candidate("steam_dlc"))
        self.assertIsNone(self.runtime._module_admin_source)
        self.assertFalse(self.host._packages)

    async def test_ff14_and_external_packages_keep_distinct_catalogs_and_factories(
        self,
    ):
        await self.runtime.terminate()
        (self.root / "bundled-data").mkdir()
        runtime = AstrBotRuntime(
            self.context,
            plugin_root=self._ff14_fixture(),
            data_dir=self.root / "bundled-data",
            font_manager_factory=_Font,
            http_transport_factory=_Transport,
            bundled_selection=DEFAULT_BUNDLE_SELECTION,
        )
        self.addAsyncCleanup(runtime.terminate)
        await runtime.initialize()
        self.runtime, self.core = runtime, runtime.core_runtime
        self.host = runtime.extension_host
        await self._register()
        self.assertTrue(self.core.registry.snapshot().module("ff14/ff14").enabled)
        self.assertTrue(self.core.registry.snapshot().module("steam_dlc/steam").enabled)
        self.core.extension_runtime.scan()
        self.assertEqual(
            {c.package.package_id for c in self.core.extension_runtime.candidates()},
            {"ff14", "steam_dlc"},
        )
        self.assertEqual(len(self.core.admin_operations.module_resources()), 5)

    async def test_stop_timeout_revokes_lease_and_retains_exact_retry_ownership(self):
        (self.package / "module.py").write_text(
            "import asyncio\nSLOW = True\n"
            + SOURCE.replace(
                "async def stop(self): self.started = None",
                "async def stop(self):\n"
                "        global SLOW\n"
                "        if SLOW:\n"
                "            SLOW = False\n"
                "            await asyncio.sleep(60)\n"
                "        self.started = None",
            ),
            encoding="utf-8",
        )
        lease = await self._register()
        self.host._timeout = 0.05
        with self.assertRaises((ExtensionHostError, ExtensionCleanupPending)):
            await self.host.unregister_package(lease)
        self.assertFalse(lease.active)
        self.assertIs(self.host._packages["steam_dlc"], lease)
        self.host._timeout = 1
        await self.host.unregister_package(lease)
        self.assertFalse(self.core.registry.snapshot().modules)
        self.assertFalse(self.host._packages)

    async def test_registration_timeout_owns_late_factory_and_cleans_after_retry(self):
        (self.package / "module.py").write_text(
            "import asyncio\n"
            + SOURCE.replace(
                "self.services = services",
                "await asyncio.sleep(0.25)\n        self.services = services",
            ),
            encoding="utf-8",
        )
        self.host._timeout = 0.05
        self.host._registration_timeout = 0.05
        with self.assertRaisesRegex(ExtensionHostError, "cleanup_pending"):
            await self._register()
        retained = self.host._packages.get("steam_dlc")
        if retained is not None:
            self.assertFalse(retained.active)
        await asyncio.sleep(0.3)
        self.host._timeout = 1
        if retained is not None:
            await self.host.unregister_package(retained)
        self.assertFalse(self.core.registry.snapshot().modules)
        self.assertIsNone(self.core.extension_runtime.candidate("steam_dlc"))
        self.assertEqual(self.host._sources._filesystem.reserved_bytes, 0)

    def _ff14_fixture(self):
        source = Path(
            os.environ.get("YGL_TEST_FF14_BUNDLE_SOURCE", ROOT / "modules" / "ff14")
        )
        plugin = self.root / "bundled-plugin"
        shutil.copytree(
            source,
            plugin / "modules" / "ff14",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        return plugin


if __name__ == "__main__":
    unittest.main()
