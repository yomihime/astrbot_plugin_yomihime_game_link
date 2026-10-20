"""A trusted Host can select another SDK-only bundle or an empty Core."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.adapters.astrbot.bundled import (
    BundledExtensionError,
    ReviewedBundleSelection,
    install_bundled_package,
)
from ygl_test_subject.adapters.astrbot.runtime import AstrBotRuntime
from ygl_test_subject.extensions.discovery import discover_packages
from ygl_test_subject.infrastructure.private_storage import resolve_private_storage

from tests.fixtures.managed_font import OfflineManagedFont
from tests.storage import test_private_storage as native_fixture

ROOT = Path(__file__).resolve().parents[2]


class ReviewedBundleSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ygl-selected-", dir=ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.plugin = self.root / "plugin"
        self.data = self.root / "data"
        self.plugin.mkdir()
        self.data.mkdir()

    def test_minimal_selected_by_host_uses_same_capture_and_immutable_install(self):
        source = self.plugin / "examples/modules/minimal"
        shutil.copytree(ROOT / "examples/modules/minimal", source)
        selection = ReviewedBundleSelection("minimal", "examples/modules")
        installed = install_bundled_package(self.plugin, self.data, selection=selection)
        self.assertTrue(installed.trusted)
        self.assertTrue(installed.installed)
        self.assertEqual(installed.inventory.package_id, "minimal")
        discovered = discover_packages(installed.extension_root)
        self.assertEqual([package.package_id for package in discovered], ["minimal"])
        self.assertEqual(discovered[0].manifest.modules[0].route, "minimal")
        self.assertEqual(discovered[0].manifest.modules[0].pages[0].role, "home")
        manifest = json.loads(
            (installed.package_dir / "yomihime.manifest.json").read_bytes()
        )
        self.assertEqual(manifest["modules"][0]["pages"][0]["entry"], "pages/entry.js")
        reused = install_bundled_package(self.plugin, self.data, selection=selection)
        self.assertTrue(reused.trusted)
        self.assertFalse(reused.installed)
        self.assertEqual(reused.extension_root, installed.extension_root)
        (installed.package_dir / "pages/entry.js").write_bytes(b"untrusted")
        refused = install_bundled_package(self.plugin, self.data, selection=selection)
        self.assertFalse(refused.trusted)

    def test_empty_selection_needs_no_module_directory_and_writes_nothing(self):
        result = install_bundled_package(self.plugin, self.data, selection=None)
        self.assertFalse(result.selected)
        self.assertFalse(result.trusted)
        self.assertIsNone(result.inventory)
        self.assertEqual(list(self.data.iterdir()), [])

    def test_selection_is_closed_typed_host_input(self):
        for package, directory in (
            ("../minimal", "modules"),
            ("minimal", "../modules"),
            ("minimal", "C:/modules"),
        ):
            with self.assertRaises(ValueError):
                ReviewedBundleSelection(package, directory)
        with self.assertRaises(BundledExtensionError):
            install_bundled_package(
                self.plugin, self.data, selection={"package_id": "minimal"}
            )


@unittest.skipUnless(os.name == "nt", "native private isolated runtime")
class ReviewedRuntimeSelectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        native_fixture.WindowsPrivateStorageTests.setUp(self)

    async def check_runtime(self, selection):
        plugin = self.owned / "plugin"
        plugin.mkdir()
        if selection is not None:
            shutil.copytree(
                ROOT / "examples/modules/minimal", plugin / "examples/modules/minimal"
            )

        class NoBusinessTransport:
            async def request(self, *_):
                raise AssertionError("isolated source test must not make HTTP requests")

            request_credential_exchange = request

            async def close(self):
                pass

        runtime = AstrBotRuntime(
            object(),
            plugin_root=plugin,
            data_dir=self.original,
            bundled_selection=selection,
            font_manager_factory=OfflineManagedFont,
            http_transport_factory=NoBusinessTransport,
        )
        with (
            patch.dict(os.environ, {"YGL_SECRET_KEY": ""}),
            patch(
                "ygl_test_subject.adapters.astrbot.runtime.resolve_private_storage",
                lambda root: resolve_private_storage(root, private_base=self.owned),
            ),
        ):
            try:
                await runtime.initialize()
                self.assertTrue(runtime.ready, runtime.bundle_failure_reason)
                catalog = runtime.public_module_catalog()
                if selection is None:
                    self.assertEqual(catalog["modules"], [])
                    self.assertIsNone(runtime.bundle_failure_reason)
                else:
                    module = catalog["modules"][0]
                    self.assertEqual(module["module_id"], "minimal/demo")
                    self.assertEqual(module["state"], "loaded")
                    self.assertEqual(module["pages"][0]["role"], "home")
            finally:
                await runtime.terminate()
        self.assertIsNone(runtime.core_runtime)

    async def test_trusted_minimal_uses_actual_host_assembly_and_core_loader(self):
        await self.check_runtime(ReviewedBundleSelection("minimal", "examples/modules"))

    async def test_no_module_retains_ready_global_core(self):
        await self.check_runtime(None)
