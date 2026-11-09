"""Verify the narrow, non-overwriting FF14 bundle installation path."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from zipfile import ZipFile

from ygl_test_subject.adapters.astrbot.bundled import (
    BundledExtensionError,
    install_bundled_package,
)
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.extensions.discovery import discover_packages
from ygl_test_subject.extensions.factory_resolver import FilesystemFactorySource
from ygl_test_subject.extensions.loader import CandidateState, ExtensionCandidate

from scripts.build_release import build_release
from yomihime_game_link_sdk.services import (
    ConfigSnapshot,
    HealthStatus,
    ModuleFactory,
    ModuleServices,
)

ROOT = Path(__file__).resolve().parents[2]
BUNDLE_SOURCE = ROOT / "modules"


class BundledExtensionTests(unittest.IsolatedAsyncioTestCase):
    EXPECTED_CAPABILITIES = {
        "status",
        "ff14.news.addhere",
        "item.lookup",
        "ff14.item.select",
        "ff14.market.query",
        "ff14.market.select",
        "ff14.logs.character",
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

    async def test_public_web_manifest_change_installs_new_slot_without_old_slot_mutation(
        self,
    ):
        manifest_path = self.plugin_root / "modules" / "ff14" / "yomihime.manifest.json"
        current_bytes = manifest_path.read_bytes()
        current = json.loads(current_bytes)
        old = json.loads(current_bytes)
        # A previous-shaped declaration admitted under the current ABI.
        # Historical module ABI versions are intentionally not compatible.
        old_module = old["modules"][0]
        old_module.pop("display")
        # Legacy command-only modules predate aggregate model-tool metadata.
        old_module.pop("model_tool_name")
        old_module.pop("model_tool_description")
        old_module["tools"] = []
        old_module["capabilities"] = [
            capability
            for capability in old_module["capabilities"]
            if capability["capability_id"] != "ff14.market.select"
        ]
        for capability in old_module["capabilities"]:
            capability.pop("invocation_origins", None)
            if capability["capability_id"] == "ff14.market.query":
                # Preserve this fixture's input/command-only historical shape,
                # including removal of the later semantic dialogue fields.
                for field in (
                    "query",
                    "server",
                    "dc",
                    "region",
                    "quality",
                    "intent",
                    "action",
                    "request_language",
                    "context_language",
                ):
                    capability["input_schema"]["properties"].pop(field)
        old["contract_version"] = "2.0"
        old["modules"][0]["pages"] = []
        public_ids = {
            "item.lookup",
            "ff14.logs.character",
            "ff14.calendar.query",
            "ff14.market.query",
        }
        for capability in old["modules"][0]["capabilities"]:
            if capability["capability_id"] in public_ids:
                capability["invocation_policy"] = "command_only"
        manifest_path.write_text(
            json.dumps(old, ensure_ascii=False, indent=2), encoding="utf8"
        )
        previous = install_bundled_package(self.plugin_root, self.data_root)
        before = {
            str(path.relative_to(previous.extension_root)): path.read_bytes()
            for path in previous.extension_root.rglob("*")
            if path.is_file()
        }
        manifest_path.write_bytes(current_bytes)
        replacement = install_bundled_package(self.plugin_root, self.data_root)
        self.assertNotEqual(previous.extension_root, replacement.extension_root)
        self.assertEqual(
            before,
            {
                str(path.relative_to(previous.extension_root)): path.read_bytes()
                for path in previous.extension_root.rglob("*")
                if path.is_file()
            },
        )
        self.assertEqual(current["contract_version"], "2.0")
        self.assertEqual(
            (replacement.package_dir / "yomihime.manifest.json").read_bytes(),
            current_bytes,
        )
        legacy = discover_packages(previous.extension_root)[0].manifest
        self.assertEqual(legacy.contract_version, "2.0")
        self.assertEqual(legacy.modules[0].tools, ())
        self.assertIsNone(legacy.modules[0].model_tool_name)
        self.assertIsNone(legacy.modules[0].model_tool_description)
        self.assertEqual(
            {capability.capability_id for capability in legacy.modules[0].capabilities},
            self.EXPECTED_CAPABILITIES - {"ff14.market.select"},
        )
        self.assertEqual(
            {
                capability["capability_id"]
                for capability in current["modules"][0]["capabilities"]
                if capability["invocation_policy"] == "command_and_public_web"
            },
            public_ids,
        )
        for capability in old["modules"][0]["capabilities"]:
            if capability["capability_id"] in public_ids:
                capability["invocation_policy"] = "command_and_public_web"
        old["contract_version"] = "2.0"
        old["modules"][0]["pages"] = current["modules"][0]["pages"]
        # A second current-ABI declaration remains a separate immutable slot.
        manifest_path.write_text(
            json.dumps(old, ensure_ascii=False, indent=2), encoding="utf8"
        )
        legacy15 = install_bundled_package(self.plugin_root, self.data_root)
        self.assertNotIn(
            legacy15.extension_root,
            (previous.extension_root, replacement.extension_root),
        )
        package15 = discover_packages(legacy15.extension_root)[0].manifest
        self.assertEqual(package15.contract_version, "2.0")
        self.assertEqual(package15.modules[0].tools, ())
        self.assertIsNone(package15.modules[0].model_tool_name)
        self.assertIsNone(package15.modules[0].model_tool_description)
        market15 = next(
            capability
            for capability in package15.modules[0].capabilities
            if capability.capability_id == "ff14.market.query"
        )
        self.assertEqual(set(market15.input_schema["properties"]), {"input", "command"})
        self.assertEqual(market15.invocation_policy.value, "command_and_public_web")
        self.assertTrue(
            all(
                capability.invocation_origins is None
                for capability in package15.modules[0].capabilities
            )
        )
        self.assertEqual(
            before,
            {
                str(path.relative_to(previous.extension_root)): path.read_bytes()
                for path in previous.extension_root.rglob("*")
                if path.is_file()
            },
        )
        self.assertEqual(
            (replacement.package_dir / "yomihime.manifest.json").read_bytes(),
            current_bytes,
        )

    def assert_ff14_factory_contract(self, instance, health) -> None:
        handlers = instance.handlers()
        self.assertEqual(set(handlers.capabilities), self.EXPECTED_CAPABILITIES)
        self.assertEqual(
            set(handlers.collectors),
            {"ff14.calendar.collect", "ff14.calendar.sync", "ff14.news.collect"},
        )
        self.assertEqual(
            set(handlers.evaluators),
            {"ff14.calendar.daily_summary", "ff14.news.official"},
        )
        self.assertEqual(set(handlers.event_composers), {"ff14.news.card"})
        self.assertEqual(set(health.capabilities), self.EXPECTED_CAPABILITIES)
        self.assertNotIn("ff14.logs.output_percentile", health.capabilities)

    @staticmethod
    def _services(values=None):
        services = object.__new__(ModuleServices)
        object.__setattr__(
            services,
            "config",
            AsyncMock(
                current=AsyncMock(
                    return_value=validate_contract(
                        ConfigSnapshot(
                            1,
                            values
                            if values is not None
                            else {
                                "ff14_default_region": "cn",
                                "ff14_calendar_default_days": 7,
                                "ff14_calendar_default_timezone": "Asia/Shanghai",
                                "ff14_calendar_default_delivery_time": "08:00",
                            },
                        )
                    )
                )
            ),
        )
        return services

    async def test_factory_rejects_missing_and_invalid_config(self) -> None:
        result = install_bundled_package(self.plugin_root, self.data_root)
        package = discover_packages(result.extension_root)[0]
        lease = await FilesystemFactorySource().capture(
            ExtensionCandidate(package, CandidateState.DISABLED, "test")
        )
        try:
            factory = lease.resolve(package.manifest.modules[0].factory_entry)
            with self.assertRaises(AttributeError):
                await factory.create(object.__new__(ModuleServices))
            with self.assertRaisesRegex(ValueError, "ff14_calendar_default_days"):
                await factory.create(self._services({"ff14_calendar_default_days": 0}))
        finally:
            lease.release()

    async def test_install_discovers_manifest_and_resolves_factory(self) -> None:
        result = install_bundled_package(self.plugin_root, self.data_root)

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
            services = self._services()
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
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PrivateKey,
            )

            # A complete Core release is signed and captures its own private SDK;
            # the optional developer wheel is not an installation prerequisite.
            archive_path, _ = build_release(
                output_dir=artifact_dir,
                repository_root=ROOT,
                signing_key=Ed25519PrivateKey.generate(),
            )
            with ZipFile(archive_path) as archive:
                archive.extractall(plugin_root)

            result = install_bundled_package(plugin_root, data_root)

            self.assertTrue(result.installed)
            self.assertTrue(result.trusted)
            package = discover_packages(result.extension_root)[0]
            owner = FilesystemFactorySource()
            lease = await owner.capture(
                ExtensionCandidate(package, CandidateState.DISABLED, "test")
            )
            try:
                factory = lease.resolve(package.manifest.modules[0].factory_entry)
                instance = await factory.create(self._services())
                await instance.start()
                health = await instance.check_health()
                self.assert_ff14_factory_contract(instance, health)
                await instance.stop()
            finally:
                lease.release()

    async def test_exact_existing_package_is_trusted_without_rewrite(self) -> None:
        first = install_bundled_package(self.plugin_root, self.data_root)
        before = {
            path.relative_to(first.package_dir).as_posix(): path.read_bytes()
            for path in first.package_dir.rglob("*")
            if path.is_file()
        }

        second = install_bundled_package(self.plugin_root, self.data_root)

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
        first = install_bundled_package(self.plugin_root, self.data_root)
        readme = first.package_dir / "README.md"
        readme.write_bytes(readme.read_bytes() + b"\nuser data\n")
        before = readme.read_bytes()

        second = install_bundled_package(self.plugin_root, self.data_root)

        self.assertFalse(second.installed)
        self.assertFalse(second.trusted)
        self.assertEqual(second.reason, "existing_readme_mismatch")
        self.assertEqual(readme.read_bytes(), before)

    async def test_source_upgrade_uses_a_new_slot_and_keeps_old_data(self) -> None:
        first = install_bundled_package(self.plugin_root, self.data_root)
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

        upgraded = install_bundled_package(self.plugin_root, self.data_root)

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

        result = install_bundled_package(self.plugin_root, self.data_root)

        self.assertTrue(result.trusted)
        self.assertEqual(legacy_file.read_text(encoding="utf-8"), "keep this")
        self.assertEqual(database.read_bytes(), b"existing runtime state")

    async def test_historical_long_staging_and_source_paths_are_trusted(self) -> None:
        parent = self.root / "long-data"
        parent.mkdir()
        suffix = "/bundled_extensions"
        component_length = 179 - len(str(parent)) - len(suffix) - 1
        self.assertGreater(component_length, 0)
        data_dir = parent / ("d" * component_length)
        data_dir.mkdir()
        first = install_bundled_package(self.plugin_root, data_dir)
        self.assertTrue(first.trusted, first.reason)
        self.assertEqual(len(str(data_dir / "bundled_extensions")), 179)
        self.assertEqual(
            len(str(first.package_dir / "features" / "calendar_subscriptions.py")),
            284,
        )
        self.assertEqual(len(discover_packages(first.extension_root)), 1)
        second = install_bundled_package(self.plugin_root, data_dir)
        self.assertTrue(second.trusted, second.reason)
        self.assertFalse(second.installed)
        self.assertEqual(second.extension_root, first.extension_root)

    async def test_fingerprint_slot_with_extra_data_is_fail_closed(self) -> None:
        first = install_bundled_package(self.plugin_root, self.data_root)
        extra = first.extension_root / "unexpected.txt"
        extra.write_text("unknown slot content", encoding="utf-8")

        second = install_bundled_package(self.plugin_root, self.data_root)

        self.assertFalse(second.installed)
        self.assertFalse(second.trusted)
        self.assertEqual(second.reason, "existing_bundle_slot_mismatch")
        self.assertEqual(extra.read_text(encoding="utf-8"), "unknown slot content")

    async def test_staging_creation_permission_and_io_failures_are_distinct(
        self,
    ) -> None:
        original_mkdir = Path.mkdir
        for failure, reason in (
            (PermissionError("private details"), "package_permission_denied"),
            (FileNotFoundError("private details"), "package_root_unavailable"),
        ):

            def fail_staging(path, *args, **kwargs):
                if path.name.startswith(".bundle-") and path.name.endswith(".stage"):
                    raise failure
                return original_mkdir(path, *args, **kwargs)

            with self.subTest(reason=reason), patch.object(Path, "mkdir", fail_staging):
                outcome = install_bundled_package(self.plugin_root, self.data_root)
            self.assertFalse(outcome.trusted)
            self.assertEqual(outcome.reason, reason)

    async def test_unsupported_native_environment_keeps_the_safe_reason(self) -> None:
        from ygl_test_subject.infrastructure.windows.fs import WindowsScanError

        with (
            patch("ygl_test_subject.adapters.astrbot.bundled.os.name", "nt"),
            patch(
                "ygl_test_subject.infrastructure.windows.fs._check_runtime",
                side_effect=WindowsScanError("unsupported_environment"),
            ),
        ):
            with self.assertRaises(BundledExtensionError) as raised:
                install_bundled_package(self.plugin_root, self.data_root)
        self.assertEqual(raised.exception.code, "unsupported_environment")
        self.assertFalse((self.data_root / "bundled_extensions").exists())

    async def test_bundle_source_link_is_rejected_without_installing(self) -> None:
        external = self.root / "outside.py"
        external.write_text("EXTERNAL = True\n", encoding="utf-8")
        linked = self.plugin_root / "modules" / "ff14" / "external.py"
        try:
            linked.symlink_to(external)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")

        with self.assertRaisesRegex(BundledExtensionError, "bundled_source_invalid"):
            install_bundled_package(self.plugin_root, self.data_root)
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
            install_bundled_package(self.plugin_root, self.data_root)
        self.assertEqual(tuple(outside.iterdir()), ())


class BundledPublishRecoveryTests(unittest.TestCase):
    """Deterministic control-flow checks; synthetic IDs are not NTFS proof."""

    def setUp(self):
        from types import SimpleNamespace

        from ygl_test_subject.adapters.astrbot import bundled

        self.subject = bundled
        platform = patch.object(
            bundled, "os", SimpleNamespace(name="nt", rename=unittest.mock.Mock())
        )
        platform.start()
        self.addCleanup(platform.stop)
        self.root = Path("C:/controlled/bundled_extensions")
        self.stage = self.root / ".original.stage"
        self.target = self.root / "fingerprint"
        identity = bundled._BundleDirectoryIdentity
        self.parent = identity("volume", 71, 99, None)
        self.staged = identity("volume", 71, 99, 123)
        self.payload = {"original": object()}

    @staticmethod
    def denied(winerror=5):
        error = PermissionError("injected final rename refusal")
        if winerror is not None:
            error.winerror = winerror
        return error

    def publish(self):
        return self.subject._publish_staged_bundle(
            self.root,
            self.stage,
            self.target,
            self.payload,
            self.parent,
            self.staged,
        )

    def test_win5_absent_revalidated_stage_then_single_extra_rename(self):
        with (
            patch.object(
                self.subject,
                "_check_windows_slot",
                side_effect=[
                    (True, None, False),
                    (False, None, True),
                    (True, None, False),
                    (True, None, False),
                ],
            ) as check,
            patch.object(
                self.subject.os, "rename", side_effect=[self.denied(), None]
            ) as rename,
        ):
            self.assertEqual(self.publish(), (True, True, None))
        self.assertEqual(rename.call_count, 2)
        for call in rename.call_args_list:
            self.assertEqual(call.args, (self.stage, self.target))
        self.assertEqual(check.call_count, 4)
        self.assertIs(check.call_args_list[2].args[2], self.payload)
        self.assertEqual(check.call_args_list[3].kwargs, {"expected_id": 123})

    def test_win5_or_fileexists_reuses_valid_competitor_without_retry(self):
        for error in (self.denied(), FileExistsError()):
            with (
                self.subTest(error=type(error).__name__),
                patch.object(
                    self.subject,
                    "_check_windows_slot",
                    side_effect=[
                        (True, None, False),
                        (True, None, False),
                    ],
                ),
                patch.object(self.subject.os, "rename", side_effect=error) as rename,
            ):
                self.assertEqual(self.publish(), (False, True, None))
                self.assertEqual(rename.call_count, 1)

    def test_competitor_mismatch_is_untrusted_without_retry(self):
        with (
            patch.object(
                self.subject,
                "_check_windows_slot",
                side_effect=[
                    (True, None, False),
                    (False, "existing_readme_mismatch", False),
                ],
            ),
            patch.object(
                self.subject.os, "rename", side_effect=self.denied()
            ) as rename,
        ):
            self.assertEqual(self.publish(), (False, False, "existing_readme_mismatch"))
            self.assertEqual(rename.call_count, 1)

    def test_fileexists_absent_does_not_publish_again(self):
        with (
            patch.object(
                self.subject,
                "_check_windows_slot",
                side_effect=[
                    (True, None, False),
                    (False, None, True),
                ],
            ),
            patch.object(
                self.subject.os, "rename", side_effect=FileExistsError()
            ) as rename,
        ):
            with self.assertRaises(FileExistsError):
                self.publish()
            self.assertEqual(rename.call_count, 1)

    def test_other_final_permission_errors_do_not_observe_or_retry(self):
        for winerror in (None, 32, 1314):
            with (
                self.subTest(winerror=winerror),
                patch.object(
                    self.subject,
                    "_check_windows_slot",
                    return_value=(True, None, False),
                ) as check,
                patch.object(
                    self.subject.os, "rename", side_effect=self.denied(winerror)
                ) as rename,
            ):
                with self.assertRaises(PermissionError):
                    self.publish()
                self.assertEqual(rename.call_count, 1)
                self.assertEqual(check.call_count, 1)

    def test_permanent_denial_never_attempts_third_publish(self):
        with (
            patch.object(
                self.subject,
                "_check_windows_slot",
                side_effect=[
                    (True, None, False),
                    (False, None, True),
                    (True, None, False),
                    (False, None, True),
                ],
            ),
            patch.object(
                self.subject.os, "rename", side_effect=[self.denied(), self.denied()]
            ) as rename,
        ):
            with self.assertRaises(PermissionError):
                self.publish()
            self.assertEqual(rename.call_count, 2)

    def test_excluded_second_permission_error_does_not_observe_or_reuse_target(self):
        for winerror in (None, 32, 1314):
            second_error = self.denied(winerror)
            with (
                self.subTest(winerror=winerror),
                patch.object(
                    self.subject,
                    "_check_windows_slot",
                    side_effect=[
                        (True, None, False),
                        (False, None, True),
                        (True, None, False),
                        (True, None, False),
                    ],
                ) as check,
                patch.object(
                    self.subject.os, "rename", side_effect=[self.denied(), second_error]
                ) as rename,
            ):
                with self.assertRaises(PermissionError) as raised:
                    self.publish()
                self.assertIs(raised.exception, second_error)
                self.assertEqual(rename.call_count, 2)
                # The fourth check would validate a competing target. An
                # excluded second error must propagate before that observation.
                self.assertEqual(check.call_count, 3)

    def test_second_competitor_is_reused_with_no_third_publish(self):
        for error in (FileExistsError(), self.denied()):
            with (
                self.subTest(error=type(error).__name__),
                patch.object(
                    self.subject,
                    "_check_windows_slot",
                    side_effect=[
                        (True, None, False),
                        (False, None, True),
                        (True, None, False),
                        (True, None, False),
                    ],
                ),
                patch.object(
                    self.subject.os, "rename", side_effect=[self.denied(), error]
                ) as rename,
            ):
                self.assertEqual(self.publish(), (False, True, None))
                self.assertEqual(rename.call_count, 2)

    def test_stage_tamper_before_retry_prevents_second_rename(self):
        with (
            patch.object(
                self.subject,
                "_check_windows_slot",
                side_effect=[
                    (True, None, False),
                    (False, None, True),
                    (False, "existing_bundle_slot_mismatch", False),
                ],
            ),
            patch.object(
                self.subject.os, "rename", side_effect=self.denied()
            ) as rename,
        ):
            self.assertEqual(
                self.publish(), (False, False, "existing_bundle_slot_mismatch")
            )
            self.assertEqual(rename.call_count, 1)

    def test_identity_or_close_unknown_prevents_second_rename(self):
        for reason in ("bundle_identity_changed", "bundle_handle_close_failed"):
            with (
                self.subTest(reason=reason),
                patch.object(
                    self.subject,
                    "_check_windows_slot",
                    side_effect=[
                        (True, None, False),
                        (False, None, True),
                        self.subject.BundledExtensionError(reason),
                    ],
                ),
                patch.object(
                    self.subject.os, "rename", side_effect=self.denied()
                ) as rename,
            ):
                with self.assertRaises(BundledExtensionError):
                    self.publish()
                self.assertEqual(rename.call_count, 1)

    def test_success_target_must_retain_staging_native_id(self):
        different = self.subject._BundleDirectoryIdentity("volume", 71, 99, 456)
        with (
            patch.object(
                self.subject, "_windows_bundle_identity", return_value=different
            ),
            patch.object(self.subject, "_matches_existing_slot") as matches,
        ):
            with self.assertRaises(BundledExtensionError):
                self.subject._check_windows_slot(
                    self.root, self.target, self.payload, self.parent, expected_id=123
                )
            matches.assert_not_called()

    def test_competitor_may_have_different_stable_native_id(self):
        competitor = self.subject._BundleDirectoryIdentity("volume", 71, 99, 456)
        with (
            patch.object(
                self.subject, "_windows_bundle_identity", return_value=competitor
            ),
            patch.object(
                self.subject, "_matches_existing_slot", return_value=(True, None)
            ) as matches,
        ):
            self.assertEqual(
                self.subject._check_windows_slot(
                    self.root, self.target, self.payload, self.parent
                ),
                (True, None, False),
            )
            matches.assert_called_once_with(self.root, self.target, self.payload)

    def test_parent_and_slot_substitution_are_not_trusted(self):
        identity = self.subject._BundleDirectoryIdentity
        before = identity("volume", 71, 99, 456)
        for changed in (
            identity("other", 71, 99, 456),
            identity("volume", 72, 99, 456),
            identity("volume", 71, 98, 456),
            identity("volume", 71, 99, 457),
            self.parent,
        ):
            with (
                self.subTest(changed=changed),
                patch.object(
                    self.subject,
                    "_windows_bundle_identity",
                    side_effect=[before, changed],
                ),
                patch.object(
                    self.subject, "_matches_existing_slot", return_value=(True, None)
                ),
            ):
                with self.assertRaises(BundledExtensionError):
                    self.subject._check_windows_slot(
                        self.root, self.target, self.payload, self.parent
                    )

    def test_parent_substitution_is_not_absence(self):
        changed = self.subject._BundleDirectoryIdentity("volume", 71, 98, None)
        with patch.object(
            self.subject, "_windows_bundle_identity", return_value=changed
        ):
            with self.assertRaises(BundledExtensionError):
                self.subject._check_windows_slot(
                    self.root, self.target, self.payload, self.parent
                )

    def test_non_windows_permission_denied_is_not_retried(self):
        # Patch only the subject's os binding; pathlib and other modules retain
        # the real OS, and no synthetic ID masquerades as native qualification.
        from types import SimpleNamespace

        rename = unittest.mock.Mock(side_effect=self.denied())
        with (
            patch.object(
                self.subject, "os", SimpleNamespace(name="posix", rename=rename)
            ),
            patch.object(self.subject, "_windows_bundle_identity") as native,
        ):
            with self.assertRaises(PermissionError):
                self.publish()
            rename.assert_called_once_with(self.stage, self.target)
            native.assert_not_called()

    def test_native_close_failure_rejects_receipt_and_closes_other_handles(self):
        from types import SimpleNamespace

        from ygl_test_subject.infrastructure.windows import fs

        native = SimpleNamespace(
            CloseHandle=unittest.mock.Mock(side_effect=lambda handle: handle.value != 4)
        )
        handles = [fs._Handle(native, value) for value in range(1, 6)]
        with (
            patch.object(fs, "_Native", return_value=native),
            patch.object(
                fs,
                "_parse_root",
                return_value=SimpleNamespace(
                    drive_root="C:/", components=("controlled", "bundled_extensions")
                ),
            ),
            patch.object(fs, "_volume_name", return_value="volume"),
            patch.object(fs, "_open_volume_root", return_value=handles[0]),
            patch.object(fs, "_volume_serial", return_value=(71, 255)),
            patch.object(fs, "_get_info"),
            patch.object(fs, "_file_id", return_value=1),
            patch.object(
                fs, "_lookup_component", side_effect=[(handles[1], 2), (handles[2], 99)]
            ),
            patch.object(fs, "_reopen_directory", side_effect=handles[3:]),
            patch.object(fs, "_enumerate", return_value=[]),
            patch.object(fs, "_verify_opened"),
        ):
            with self.assertRaisesRegex(
                BundledExtensionError, "bundle_handle_close_failed"
            ):
                self.subject._windows_bundle_identity(self.root, self.target)
        self.assertEqual(native.CloseHandle.call_count, 5)
        self.assertEqual(
            [call.args[0].value for call in native.CloseHandle.call_args_list],
            [5, 4, 3, 2, 1],
        )


if __name__ == "__main__":
    unittest.main()
