"""Verify the narrow, non-overwriting FF14 bundle installation path."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from ygl_test_subject.adapters.astrbot.bundled import (
    BundledExtensionError,
    install_bundled_ff14,
)
from ygl_test_subject.extensions.discovery import discover_packages
from ygl_test_subject.extensions.factory_resolver import FilesystemFactorySource
from ygl_test_subject.extensions.loader import CandidateState, ExtensionCandidate

from scripts.build_release import build_release, build_sdk_wheel
from yomihime_sdk.api.services import HealthStatus, ModuleFactory, ModuleServices

ROOT = Path(__file__).resolve().parents[2]
BUNDLE_SOURCE = ROOT / "modules"


class BundledExtensionTests(unittest.IsolatedAsyncioTestCase):
    EXPECTED_CAPABILITIES = {
        "status",
        "item.lookup",
        "ff14.logs.character",
        "ff14.logs.output_percentile",
        "ff14.calendar.query",
        "ff14.calendar.subscription.create",
        "ff14.calendar.subscription.list",
        "ff14.calendar.subscription.update",
        "ff14.calendar.subscription.cancel",
    }

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="ygl-bundled-ff14-", dir=ROOT
        )
        self.root = Path(self.temporary.name)
        self.plugin_root = self.root / "plugin"
        self.data_root = self.root / "data"
        self.plugin_root.mkdir()
        self.data_root.mkdir()
        (self.plugin_root / "modules").mkdir()
        shutil.copytree(BUNDLE_SOURCE / "ff14", self.plugin_root / "modules" / "ff14")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def assert_ff14_factory_contract(self, instance, health) -> None:
        handlers = instance.handlers()
        self.assertEqual(set(handlers.capabilities), self.EXPECTED_CAPABILITIES)
        self.assertEqual(set(handlers.collectors), {"ff14.calendar.collect"})
        self.assertEqual(set(handlers.evaluators), {"ff14.calendar.daily_summary"})
        self.assertEqual(set(health.capabilities), self.EXPECTED_CAPABILITIES)
        output_health = health.capabilities["ff14.logs.output_percentile"]
        self.assertIs(output_health.status, HealthStatus.UNAVAILABLE)
        self.assertIn("HTTP 403", output_health.reason)
        self.assertIn("尚未验证", output_health.reason)

    async def test_install_discovers_manifest_and_resolves_factory(self) -> None:
        result = install_bundled_ff14(self.plugin_root, self.data_root)

        self.assertTrue(result.installed)
        self.assertTrue(result.trusted)
        self.assertEqual(result.package_dir, result.extension_root / "ff14")
        self.assertEqual(
            result.extension_root.parent, self.data_root / "bundled_extensions"
        )
        self.assertRegex(result.extension_root.name, r"^[0-9a-f]{64}$")
        discovered = discover_packages(result.extension_root)
        self.assertEqual(len(discovered), 1)
        package = discovered[0]
        self.assertTrue(package.valid)
        self.assertEqual(package.package_id, "ff14")
        self.assertEqual(package.manifest.modules[0].factory_entry, "module:Factory")

        owner = FilesystemFactorySource()
        candidate = ExtensionCandidate(package, CandidateState.DISABLED, "test")
        lease = await owner.capture(candidate)
        try:
            factory = lease.resolve(package.manifest.modules[0].factory_entry)
            self.assertIsInstance(factory, ModuleFactory)
            services = object.__new__(ModuleServices)
            instance = await factory.create(services)
            await instance.start()
            health = await instance.check_health()
            self.assertIs(health.capabilities["status"].status, HealthStatus.AVAILABLE)
            self.assert_ff14_factory_contract(instance, health)
            await instance.stop()
        finally:
            lease.release()

    async def test_real_release_zip_installs_and_resolves_ff14_factory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ygl-release-ff14-", dir=ROOT) as work:
            work_root = Path(work)
            artifact_dir = work_root / "artifacts"
            plugin_root = work_root / "installed-plugin"
            data_root = work_root / "astrbot-data"
            plugin_root.mkdir()
            data_root.mkdir()
            sdk_build_root = work_root / "sdk-build"
            sdk_build_root.mkdir()
            sdk_wheel = build_sdk_wheel(sdk_build_root, ROOT)
            archive_path, _, _ = build_release(
                sdk_wheel=sdk_wheel,
                output_dir=artifact_dir,
                repository_root=ROOT,
            )
            with ZipFile(archive_path) as archive:
                archive.extractall(plugin_root)

            result = install_bundled_ff14(plugin_root, data_root)

            self.assertTrue(result.installed)
            self.assertTrue(result.trusted)
            package = discover_packages(result.extension_root)[0]
            owner = FilesystemFactorySource()
            lease = await owner.capture(
                ExtensionCandidate(package, CandidateState.DISABLED, "test")
            )
            try:
                factory = lease.resolve(package.manifest.modules[0].factory_entry)
                instance = await factory.create(object.__new__(ModuleServices))
                await instance.start()
                health = await instance.check_health()
                self.assert_ff14_factory_contract(instance, health)
                await instance.stop()
            finally:
                lease.release()

    async def test_exact_existing_package_is_trusted_without_rewrite(self) -> None:
        first = install_bundled_ff14(self.plugin_root, self.data_root)
        before = {
            path.relative_to(first.package_dir).as_posix(): path.read_bytes()
            for path in first.package_dir.rglob("*")
            if path.is_file()
        }

        second = install_bundled_ff14(self.plugin_root, self.data_root)

        self.assertFalse(second.installed)
        self.assertTrue(second.trusted)
        after = {
            path.relative_to(second.package_dir).as_posix(): path.read_bytes()
            for path in second.package_dir.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)

    async def test_existing_user_data_is_never_overwritten_or_trusted_on_mismatch(
        self,
    ) -> None:
        first = install_bundled_ff14(self.plugin_root, self.data_root)
        readme = first.package_dir / "README.md"
        readme.write_bytes(readme.read_bytes() + b"\nuser data\n")
        before = readme.read_bytes()

        second = install_bundled_ff14(self.plugin_root, self.data_root)

        self.assertFalse(second.installed)
        self.assertFalse(second.trusted)
        self.assertEqual(second.reason, "existing_readme_mismatch")
        self.assertEqual(readme.read_bytes(), before)

    async def test_source_upgrade_uses_a_new_slot_and_keeps_old_data(self) -> None:
        first = install_bundled_ff14(self.plugin_root, self.data_root)
        old_files = {
            path.relative_to(first.extension_root).as_posix(): path.read_bytes()
            for path in first.extension_root.rglob("*")
            if path.is_file()
        }
        old_readme = first.package_dir / "README.md"
        original_source_readme = self.plugin_root / "modules" / "ff14" / "README.md"
        original_source_readme.write_bytes(
            original_source_readme.read_bytes() + b"\nBundled source revision 2.\n"
        )

        upgraded = install_bundled_ff14(self.plugin_root, self.data_root)

        self.assertTrue(upgraded.installed)
        self.assertTrue(upgraded.trusted)
        self.assertNotEqual(upgraded.extension_root, first.extension_root)
        self.assertTrue(first.extension_root.is_dir())
        self.assertEqual(old_readme.read_bytes(), old_files["ff14/README.md"])
        self.assertEqual(
            {
                path.relative_to(first.extension_root).as_posix(): path.read_bytes()
                for path in first.extension_root.rglob("*")
                if path.is_file()
            },
            old_files,
        )
        self.assertEqual(
            discover_packages(upgraded.extension_root)[0].package_id, "ff14"
        )

    async def test_legacy_user_extension_and_runtime_database_are_untouched(
        self,
    ) -> None:
        legacy_dir = self.data_root / "extensions" / "ff14"
        legacy_dir.mkdir(parents=True)
        legacy_file = legacy_dir / "user-data.txt"
        legacy_file.write_text("keep this", encoding="utf-8")
        database = self.data_root / "runtime.sqlite3"
        database.write_bytes(b"existing runtime state")

        result = install_bundled_ff14(self.plugin_root, self.data_root)

        self.assertTrue(result.trusted)
        self.assertEqual(legacy_file.read_text(encoding="utf-8"), "keep this")
        self.assertEqual(database.read_bytes(), b"existing runtime state")

    async def test_fingerprint_slot_with_extra_data_is_fail_closed(self) -> None:
        first = install_bundled_ff14(self.plugin_root, self.data_root)
        extra = first.extension_root / "unexpected.txt"
        extra.write_text("unknown slot content", encoding="utf-8")

        second = install_bundled_ff14(self.plugin_root, self.data_root)

        self.assertFalse(second.installed)
        self.assertFalse(second.trusted)
        self.assertEqual(second.reason, "existing_bundle_slot_mismatch")
        self.assertEqual(extra.read_text(encoding="utf-8"), "unknown slot content")

    async def test_bundle_source_link_is_rejected_without_installing(self) -> None:
        external = self.root / "outside.py"
        external.write_text("EXTERNAL = True\n", encoding="utf-8")
        linked = self.plugin_root / "modules" / "ff14" / "external.py"
        try:
            linked.symlink_to(external)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")

        with self.assertRaisesRegex(BundledExtensionError, "bundled_source_invalid"):
            install_bundled_ff14(self.plugin_root, self.data_root)
        self.assertFalse((self.data_root / "bundled_extensions").exists())
        self.assertEqual(external.read_text(encoding="utf-8"), "EXTERNAL = True\n")

    async def test_existing_extension_root_link_is_rejected(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        try:
            (self.data_root / "bundled_extensions").symlink_to(
                outside, target_is_directory=True
            )
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")

        with self.assertRaises(BundledExtensionError):
            install_bundled_ff14(self.plugin_root, self.data_root)
        self.assertEqual(tuple(outside.iterdir()), ())


if __name__ == "__main__":
    unittest.main()
