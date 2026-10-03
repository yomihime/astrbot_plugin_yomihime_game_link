"""Local identity/bootstrap checks for the pinned Dashboard SDK bundle."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import scripts.build_dashboard_zip as zip_builder
from scripts.build_dashboard_zip import (
    EXPECTED_WHEEL_SHA256,
    MAINTENANCE_HELPER_FILES,
    OPERATOR_SCRIPT_FILES,
    PAGE_FILES,
    ROOT_FILES,
    RUNTIME_DIRS,
    _reject_link,
    build,
    included_files,
    validate_source_path,
    validate_wheel,
)

ROOT = Path(__file__).resolve().parents[2]


def _write_operator_script_fixtures(root: Path) -> None:
    for filename in (*OPERATOR_SCRIPT_FILES, *PAGE_FILES, *MAINTENANCE_HELPER_FILES):
        path = root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"operator script fixture")


BOOTSTRAP_CASE = r"""
import importlib
import importlib.util
import sys
import types
import asyncio
from pathlib import Path
from types import SimpleNamespace

plugin_root = Path(sys.argv[1]).resolve()
mode = sys.argv[2]
astrbot = types.ModuleType("astrbot")
api = types.ModuleType("astrbot.api")
event = types.ModuleType("astrbot.api.event")
star = types.ModuleType("astrbot.api.star")
class Event:
    def __init__(self):
        self.llm_flags = []
    def should_call_llm(self, value):
        self.llm_flags.append(value)
    def plain_result(self, text):
        return text
lifecycle_events = []
class Star:
    async def initialize(self): lifecycle_events.append("super_initialize")
    async def terminate(self): lifecycle_events.append("super_terminate")
class Filter:
    @staticmethod
    def command(*args, **kwargs):
        return lambda fn: fn
def register(*args, **kwargs):
    return lambda cls: cls
event.AstrMessageEvent = Event
event.filter = Filter()
star.Star = Star
star.register = register
for name, module in {
    "astrbot": astrbot,
    "astrbot.api": api,
    "astrbot.api.event": event,
    "astrbot.api.star": star,
}.items():
    sys.modules[name] = module

if mode in {"same", "partial", "unsupported", "mixed"}:
    sys.path.insert(0, str(plugin_root))
    import yomihime_sdk
    sys.path.pop(0)
    if mode == "partial":
        del sys.modules["yomihime_sdk.api.results"]
    elif mode == "unsupported":
        yomihime_sdk.__version__ = "9.9.9"
    elif mode == "mixed":
        other = Path(sys.argv[3]).resolve()
        name = "yomihime_sdk.api.results"
        spec = importlib.util.spec_from_file_location(
            name, other / "yomihime_sdk/api/results.py"
        )
        mixed_module = importlib.util.module_from_spec(spec)
        sys.modules[name] = mixed_module
        spec.loader.exec_module(mixed_module)
elif mode == "different":
    other = Path(sys.argv[3]).resolve()
    sys.path.insert(0, str(other))
    import yomihime_sdk
    sys.path.pop(0)

before = list(sys.path)
spec = importlib.util.spec_from_file_location("ygl_bootstrap_subject", plugin_root / "main.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
try:
    spec.loader.exec_module(module)
except module.SDKBootstrapError:
    assert mode in {"different", "partial", "unsupported", "mixed", "root-escape"}, mode
else:
    assert mode in {"cold", "same", "root-check", "entry-text", "entry-none", "lifecycle"}, mode
    if mode == "root-check":
        try:
            module._require_contained(Path(sys.argv[3]).resolve(), plugin_root, "test SDK root")
        except module.SDKBootstrapError:
            pass
        else:
            raise AssertionError("out-of-root SDK package was accepted")
    assert module.bootstrap_sdk(plugin_root) is sys.modules["yomihime_sdk"]
    sdk_root = Path(sys.modules["yomihime_sdk"].__file__).resolve().parent
    assert sdk_root == plugin_root / "yomihime_sdk"
    if mode == "lifecycle":
        class Pages:
            def register(self): lifecycle_events.append("register")
            def close(self): lifecycle_events.append("page_close")
        class LifecycleRuntime:
            def __init__(self, fail_start=False, fail_close=False):
                self.fail_start, self.fail_close = fail_start, fail_close
            async def initialize(self):
                lifecycle_events.append("runtime_initialize")
                if self.fail_start: raise RuntimeError("start failure")
            async def terminate(self):
                lifecycle_events.append("runtime_terminate")
                if self.fail_close: raise RuntimeError("close failure")
        async def exercise():
            for fail_start, fail_close in ((False, False), (True, False), (False, True)):
                lifecycle_events.clear()
                subject = object.__new__(module.YomihimeGameLink)
                subject._pages = Pages()
                subject._runtime = LifecycleRuntime(fail_start, fail_close)
                try: await subject.initialize()
                except RuntimeError:
                    assert fail_start
                if fail_start:
                    assert lifecycle_events == ["super_initialize", "register", "runtime_initialize", "page_close"]
                    continue
                assert lifecycle_events == ["super_initialize", "register", "runtime_initialize"]
                try: await subject.terminate()
                except RuntimeError:
                    assert fail_close
                assert lifecycle_events[-3:] == ["page_close", "runtime_terminate", "super_terminate"]
        asyncio.run(exercise())
    if mode in {"entry-text", "entry-none"}:
        result = "help text" if mode == "entry-text" else None
        class Runtime:
            async def handle_event(self, event):
                return result
        event_instance = Event()
        subject = SimpleNamespace(_runtime=Runtime())
        async def collect():
            return [
                item async for item in module.YomihimeGameLink.game_link(
                    subject, event_instance
                )
            ]
        assert asyncio.run(collect()) == ([] if result is None else [result])
        assert event_instance.llm_flags == [True]
assert sys.path == before, (before, sys.path)
"""


class SDKBootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(prefix="ygl-h-sdk-")
        cls.temp_root = Path(cls.temp.name).resolve()
        cls.wheel_dir = cls.temp_root / "wheel"
        cls.wheel_dir.mkdir()
        env = os.environ.copy()
        env["SOURCE_DATE_EPOCH"] = "315532800"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--no-index",
                "--no-deps",
                "--no-build-isolation",
                "--wheel-dir",
                str(cls.wheel_dir),
                str(ROOT),
            ],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        cls.wheel = next(cls.wheel_dir.glob("*.whl"))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def _plugin(self, name: str) -> Path:
        root = self.temp_root / name
        root.mkdir()
        shutil.copy2(ROOT / "main.py", root / "main.py")
        for name, data in validate_wheel(self.wheel).items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return root

    def _run_bootstrap(self, root: Path, mode: str, other: Path | None = None) -> None:
        command = [sys.executable, "-c", BOOTSTRAP_CASE, str(root), mode]
        if other is not None:
            command.append(str(other))
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr)

    def test_page_registration_and_cleanup_surround_runtime_lifecycle(self) -> None:
        self._run_bootstrap(self._plugin("page-lifecycle"), "lifecycle")

    def test_cold_and_identical_preload_work_under_arbitrary_plugin_names(self) -> None:
        self._run_bootstrap(self._plugin("plugin-one"), "cold")
        self._run_bootstrap(self._plugin("another_arbitrary_plugin"), "cold")
        self._run_bootstrap(self._plugin("same-preload"), "same")

    def test_matched_command_disables_default_llm_and_preserves_text_yield(
        self,
    ) -> None:
        self._run_bootstrap(self._plugin("entry-text"), "entry-text")
        self._run_bootstrap(self._plugin("entry-none"), "entry-none")

    def test_different_partial_mixed_and_unsupported_preloads_fail_closed(self) -> None:
        target = self._plugin("target-plugin")
        foreign = self._plugin("foreign-plugin")
        changed = foreign / "yomihime_sdk/api/results.py"
        changed.write_text(
            changed.read_text(encoding="utf-8") + "\n# same version, different build\n",
            encoding="utf-8",
        )
        self._run_bootstrap(target, "different", foreign)
        self._run_bootstrap(self._plugin("partial-plugin"), "partial")
        self._run_bootstrap(self._plugin("unsupported-plugin"), "unsupported")
        self._run_bootstrap(self._plugin("mixed-plugin"), "mixed", foreign)

    def test_sdk_package_root_cannot_escape_the_plugin_root(self) -> None:
        target = self._plugin("sdk-root-target")
        foreign = self._plugin("sdk-root-foreign")
        sdk_tree = target / "yomihime_sdk"
        shutil.rmtree(sdk_tree)
        try:
            os.symlink(foreign / "yomihime_sdk", sdk_tree, target_is_directory=True)
        except OSError:
            shutil.copytree(foreign / "yomihime_sdk", sdk_tree)
            self._run_bootstrap(target, "root-check", foreign / "yomihime_sdk")
        else:
            self._run_bootstrap(target, "root-escape")

    def test_zip_builder_rejects_runtime_escape_and_file_links(self) -> None:
        repository = self.temp_root / "synthetic-plugin-root"
        repository.mkdir()
        for name in ROOT_FILES:
            (repository / name).write_text("fixture", encoding="utf-8")
        for name in RUNTIME_DIRS:
            (repository / name).mkdir()
        _write_operator_script_fixtures(repository)

        outside = self.temp_root / "outside.py"
        outside.write_text("private sentinel", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "escapes its allowed root"):
            validate_source_path(outside, repository / "core", "fixture source")

        runtime_file = repository / "core" / "private.py"
        try:
            os.link(outside, runtime_file)
        except OSError:
            try:
                os.symlink(outside, runtime_file)
            except OSError:
                pass
        if runtime_file.exists() or runtime_file.is_symlink():
            with self.assertRaisesRegex(ValueError, "hard link|symlink"):
                included_files(repository)
        else:
            # Link creation can be disabled on Windows; the executable path
            # containment check above still exercises the escape rejection.
            self.assertRaisesRegex(
                ValueError,
                "escapes its allowed root",
                validate_source_path,
                outside,
                repository / "core",
                "fixture source",
            )

    def test_link_and_reparse_metadata_are_rejected_without_os_link_support(
        self,
    ) -> None:
        probe = self.temp_root / "metadata-probe"
        probe.write_text("ordinary file", encoding="utf-8")
        cases = (
            (stat.S_IFLNK, 1, 0, "symlink"),
            (stat.S_IFREG, 2, 0, "hard link"),
            (stat.S_IFREG, 1, 0x400, "reparse point"),
        )
        for mode, links, attributes, message in cases:
            metadata = SimpleNamespace(
                st_mode=mode,
                st_nlink=links,
                st_file_attributes=attributes,
            )
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(ValueError, message),
            ):
                _reject_link(probe, metadata, "fixture input")

    def test_wheel_parser_uses_the_same_bytes_that_passed_the_digest(self) -> None:
        original_path = self.temp_root / "pinned-snapshot.whl"
        original_path.write_bytes(self.wheel.read_bytes())
        replacement = self.temp_root / "replacement.whl"
        with (
            zipfile.ZipFile(self.wheel) as source,
            zipfile.ZipFile(
                replacement, "w", compression=zipfile.ZIP_DEFLATED
            ) as output,
        ):
            authenticated = source.read("yomihime_sdk/py.typed")
            for name in source.namelist():
                data = source.read(name)
                if name == "yomihime_sdk/py.typed":
                    data = b"replaced after digest snapshot\n"
                output.writestr(name, data)

        read_snapshot = zip_builder._read_snapshot

        def read_then_replace(path, allowed_root=None, label="Package input"):
            snapshot = read_snapshot(path, allowed_root, label)
            replacement.replace(original_path)
            return snapshot

        with mock.patch.object(
            zip_builder, "_read_snapshot", side_effect=read_then_replace
        ):
            package = zip_builder.validate_wheel(original_path)
        self.assertEqual(package["yomihime_sdk/py.typed"], authenticated)
        self.assertNotEqual(
            package["yomihime_sdk/py.typed"], b"replaced after digest snapshot\n"
        )

    def test_zip_uses_the_runtime_bytes_captured_by_its_validated_snapshot(
        self,
    ) -> None:
        repository = self.temp_root / "snapshot-plugin-root"
        repository.mkdir()
        for name in ROOT_FILES:
            (repository / name).write_text("fixture", encoding="utf-8")
        for name in RUNTIME_DIRS:
            (repository / name).mkdir()
        _write_operator_script_fixtures(repository)
        shutil.copytree(
            ROOT / "modules" / "ff14",
            repository / "modules" / "ff14",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        runtime_file = repository / "core" / "snapshot.py"
        original = b"trusted snapshot bytes\n"
        runtime_file.write_bytes(original)
        archive_path = self.temp_root / "snapshot.zip"
        read_snapshot = zip_builder._read_snapshot
        replaced = False

        def read_then_modify(path, allowed_root=None, label="Package input"):
            nonlocal replaced
            snapshot = read_snapshot(path, allowed_root, label)
            if Path(path) == runtime_file and not replaced:
                runtime_file.write_bytes(b"changed after snapshot\n")
                replaced = True
            return snapshot

        with mock.patch.object(
            zip_builder, "_read_snapshot", side_effect=read_then_modify
        ):
            zip_builder.build(self.wheel, archive_path, repository_root=repository)
        with zipfile.ZipFile(archive_path) as archive:
            self.assertEqual(archive.read("core/snapshot.py"), original)
        self.assertEqual(runtime_file.read_bytes(), b"changed after snapshot\n")

    def test_zip_contains_pinned_sdk_and_root_documents(self) -> None:
        package = validate_wheel(self.wheel)
        archive_path = self.temp_root / "assembled.zip"
        build(self.wheel, archive_path)
        with zipfile.ZipFile(archive_path) as schema_archive:
            for config_asset in (
                "_conf_schema.json",
                "modules/ff14/config.py",
                *PAGE_FILES,
                *MAINTENANCE_HELPER_FILES,
            ):
                self.assertEqual(
                    schema_archive.read(config_asset),
                    (ROOT / config_asset).read_bytes(),
                )
        document_bytes = {
            name: (ROOT / name).read_bytes() for name in ("README.md", "CHANGELOG.md")
        }
        document_text = {
            name: data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
            for name, data in document_bytes.items()
        }
        with zipfile.ZipFile(archive_path) as archive:
            for name, data in package.items():
                self.assertEqual(archive.read(name), data, name)
            for name, data in document_bytes.items():
                self.assertEqual(archive.read(name), data, name)
            extracted = self.temp_root / "dash-upload-arbitrary-name"
            archive.extractall(extracted)
        for name, data in document_bytes.items():
            document = extracted / name
            self.assertEqual(document.read_bytes(), data, name)
            self.assertEqual(document.read_text(encoding="utf-8"), document_text[name])
        self._run_bootstrap(extracted, "cold")

        incomplete_repository = self.temp_root / "missing-readme-plugin-root"
        incomplete_repository.mkdir()
        for name in ROOT_FILES:
            (incomplete_repository / name).write_bytes(b"fixture")
        for name in RUNTIME_DIRS:
            (incomplete_repository / name).mkdir()
        (incomplete_repository / "README.md").unlink()
        incomplete_archive = self.temp_root / "missing-readme.zip"
        with self.assertRaises(FileNotFoundError):
            build(
                self.wheel,
                incomplete_archive,
                repository_root=incomplete_repository,
            )
        self.assertFalse(incomplete_archive.exists())

    def test_builder_rejects_modified_wheel_even_with_a_self_reported_digest(
        self,
    ) -> None:
        tampered = self.temp_root / "self-reported-wheel.whl"
        tampered.write_bytes(self.wheel.read_bytes() + b"tampered")
        with self.assertRaisesRegex(ValueError, "independently pinned"):
            validate_wheel(tampered)

    def test_rebuilt_wheel_digest_matches_k_review(self) -> None:
        self.assertEqual(
            hashlib.sha256(self.wheel.read_bytes()).hexdigest(), EXPECTED_WHEEL_SHA256
        )


if __name__ == "__main__":
    unittest.main()
