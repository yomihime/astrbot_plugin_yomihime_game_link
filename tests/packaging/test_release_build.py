"""Check release tag, runtime manifest, and checksum boundaries."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from setuptools import build_meta

from scripts.build_dashboard_zip import (
    FF14_BUNDLE_REQUIRED_FILES,
    FF14_BUNDLE_ROOT,
    FONT_ASSETS,
    HOST_METADATA_FILES,
    MAINTENANCE_HELPER_FILES,
    OPERATOR_SCRIPT_FILES,
    PAGE_FILES,
    REVIEWED_BUNDLE_SOURCES,
    ROOT_FILES,
    RUNTIME_DIRS,
    _reviewed_bundle_payloads,
    included_files,
)
from scripts.build_release import (
    build_release,
    build_sdk_wheel,
    plugin_metadata,
    validate_tag,
    write_checksums,
)

ROOT = Path(__file__).resolve().parents[2]


def _write_operator_script_fixtures(root: Path) -> None:
    for filename in FONT_ASSETS:
        path = root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / filename).read_bytes())
    for filename in (
        *OPERATOR_SCRIPT_FILES,
        *HOST_METADATA_FILES,
        *PAGE_FILES,
        *MAINTENANCE_HELPER_FILES,
    ):
        path = root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"operator script fixture")


def _render_record(payloads: dict[str, bytes], record_name: str) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    for name, data in payloads.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest())
        writer.writerow(
            (name, f"sha256={digest.decode('ascii').rstrip('=')}", str(len(data)))
        )
    writer.writerow((record_name, "", ""))
    return output.getvalue().encode("utf-8")


def _write_posix_wheel(path: Path) -> tuple[dict[str, bytes], str]:
    record_name = "yomihime_module_sdk-1.5.0.dist-info/RECORD"
    payloads = {
        "yomihime_game_link_sdk/__init__.py": b"SDK_VALUE = 1\n",
        "yomihime_module_sdk-1.5.0.dist-info/METADATA": (
            b"Metadata-Version: 2.4\nName: yomihime-module-sdk\nVersion: 1.5.0\n"
        ),
        "yomihime_module_sdk-1.5.0.dist-info/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: fixture\n"
            b"Root-Is-Purelib: true\nTag: py3-none-any\n"
        ),
        "yomihime_module_sdk-1.5.0.dist-info/top_level.txt": b"yomihime_game_link_sdk\n",
        "yomihime_module_sdk-1.5.0.dist-info/licenses/LICENSE": b"MIT\n",
    }
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, data in (
            *payloads.items(),
            (record_name, _render_record(payloads, record_name)),
        ):
            info = ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return payloads, record_name


def _load_pep517_normalizer():
    captured: dict[str, object] = {}

    def capture_setup(**kwargs: object) -> None:
        captured.update(kwargs)

    with patch("setuptools.setup", side_effect=capture_setup):
        build_meta._BuildMetaBackend().run_setup(str(ROOT / "setup.py"))

    command = captured["cmdclass"]["bdist_wheel"]
    return command.run.__globals__["normalize_wheel_archive"]


def _fixture_manifest():
    import json

    manifest = json.loads((ROOT / "modules/ff14/yomihime.manifest.json").read_bytes())
    for module in manifest["modules"]:
        module.pop("pages", None)
        module.pop("resources", None)
        module.get("display", {}).pop("logo_resource", None)
    return json.dumps(manifest).encode()


class ReleaseBuildTests(unittest.TestCase):
    def test_reviewed_bundle_capture_loads_checkout_sdk_in_isolated_process(
        self,
    ) -> None:
        import json
        import subprocess
        import sys
        import textwrap
        from importlib.util import find_spec

        dependency_paths = {}
        for name in ("cryptography", "_cffi_backend"):
            spec = find_spec(name)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.origin)
            location = Path(spec.origin).resolve().parent
            dependency_paths[name] = str(
                location.parent if name == "cryptography" else location
            )
        script = textwrap.dedent(
            """            import json
            import runpy
            import sys
            from importlib.machinery import PathFinder
            from importlib.util import find_spec
            from pathlib import Path

            root = Path(sys.argv[1]).resolve()
            assert str(root) not in sys.path
            assert "yomihime_game_link_sdk" not in sys.modules

            dependency_paths = json.loads(sys.argv[2])

            class BuildDependencyFinder:
                def find_spec(self, fullname, path=None, target=None):
                    if fullname in dependency_paths:
                        return PathFinder.find_spec(
                            fullname, [dependency_paths[fullname]]
                        )
                    return None

            sys.meta_path.insert(0, BuildDependencyFinder())
            assert find_spec("yomihime_game_link_sdk") is None
            tool = runpy.run_path(str(root / "scripts" / "build_dashboard_zip.py"))
            initial_path = list(sys.path)
            with tool["_checkout_imports"]():
                sdk = sys.modules["yomihime_game_link_sdk"]
                assert Path(sdk.__file__).resolve() == (
                    root / "yomihime_game_link_sdk" / "__init__.py"
                )
            try:
                payloads = tool["_reviewed_bundle_payloads"](root)
            finally:
                assert sys.path == initial_path
                assert "yomihime_game_link_sdk" not in sys.modules
                assert not any(name.startswith("_ygl_release_build") for name in sys.modules)
            assert len(payloads) == 1
            package_root, payload = payloads[0]
            assert package_root == root / "modules" / "ff14"
            assert {
                name for name in payload["sources"] if name.endswith(".py")
            } == tool["REVIEWED_BUNDLE_SOURCES"][("modules", "ff14")]
            print("checkout SDK capture verified")
            """
        )
        with tempfile.TemporaryDirectory(prefix="yomihime-build-sdk-import-") as work:
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-B",
                    "-c",
                    script,
                    str(ROOT),
                    json.dumps(dependency_paths),
                ],
                cwd=work,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "checkout SDK capture verified")

    def test_font_assets_are_exact_and_required_with_link_protection(self) -> None:
        from scripts.build_dashboard_zip import _font_asset_snapshot

        with tempfile.TemporaryDirectory(prefix="yomihime-font-assets-") as work:
            root = Path(work).resolve()
            _write_operator_script_fixtures(root)
            for name, (size, digest) in FONT_ASSETS.items():
                with self.subTest(asset=name):
                    path = root / name
                    original = path.read_bytes()
                    self.assertEqual(len(original), size)
                    self.assertEqual(hashlib.sha256(original).hexdigest(), digest)
                    self.assertEqual(_font_asset_snapshot(path, root), original)
                    path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
                    with self.assertRaisesRegex(ValueError, "reviewed size/hash"):
                        _font_asset_snapshot(path, root)
                    path.unlink()
                    with self.assertRaises(FileNotFoundError):
                        _font_asset_snapshot(path, root)
                    sentinel = root / "sentinel"
                    sentinel.write_bytes(original)
                    os.link(sentinel, path)
                    with self.assertRaisesRegex(ValueError, "must not be a hard link"):
                        _font_asset_snapshot(path, root)
                    path.unlink()
                    sentinel.unlink()
                    path.write_bytes(original)

    def test_tag_must_be_valid_and_match_metadata_version(self) -> None:
        _, metadata_version = plugin_metadata(ROOT)
        self.assertEqual(
            validate_tag(metadata_version, metadata_version), metadata_version
        )

        for tag in ("v1.2", "0.1.2", "v01.2.3", "v1.2.3-rc1", "branch-name"):
            with self.subTest(tag=tag), self.assertRaisesRegex(ValueError, "Invalid"):
                validate_tag(tag, metadata_version)
        next_parts = metadata_version[1:].split(".")
        next_parts[-1] = str(int(next_parts[-1]) + 1)
        mismatched_tag = f"v{'.'.join(next_parts)}"
        with tempfile.TemporaryDirectory(prefix="yomihime-release-tag-") as work:
            with self.assertRaisesRegex(ValueError, "does not match"):
                build_release(mismatched_tag, output_dir=Path(work) / "artifacts")

    def test_runtime_manifest_contains_plugin_assets_and_extensions_only(self) -> None:
        paths = included_files(ROOT)
        names = {path.relative_to(ROOT).as_posix() for path in paths}
        self.assertTrue(
            {
                "main.py",
                "_conf_schema.json",
                "__init__.py",
                "metadata.yaml",
                "logo.png",
                "LICENSE",
                "README.md",
                "CHANGELOG.md",
                "requirements.txt",
                *OPERATOR_SCRIPT_FILES,
                *HOST_METADATA_FILES,
                *MAINTENANCE_HELPER_FILES,
                *PAGE_FILES,
                "extensions/disk_manifest.py",
                "infrastructure/sqlite/migrations/0000_schema_migrations.sql",
                "modules/ff14/__init__.py",
                "modules/ff14/module.py",
                "modules/ff14/config.py",
                "modules/ff14/yomihime.manifest.json",
                "modules/ff14/README.md",
            }
            <= names
        )
        ff14_names = {name for name in names if name.startswith("modules/ff14/")}
        self.assertTrue(
            {f"modules/ff14/{relative}" for relative in FF14_BUNDLE_REQUIRED_FILES}
            <= ff14_names
        )
        excluded_parts = {
            ".architecture-refactor",
            ".coordination",
            "docs",
            "examples",
            "tests",
            "__pycache__",
        }
        self.assertFalse(
            any(excluded_parts.intersection(Path(name).parts) for name in names)
        )
        self.assertNotIn("scripts/build_dashboard_zip.py", names)
        self.assertNotIn("scripts/build_release.py", names)
        self.assertFalse(any(Path(name).suffix in {".pyc", ".pyo"} for name in names))

    def test_runtime_manifest_skips_cache_and_local_data_directories(self) -> None:
        with tempfile.TemporaryDirectory(prefix="yomihime-release-manifest-") as work:
            root = Path(work).resolve()
            for filename in ROOT_FILES:
                (root / filename).write_bytes(b"runtime root asset")
            for directory in RUNTIME_DIRS:
                (root / directory).mkdir()
            _write_operator_script_fixtures(root)
            ff14_root = root / FF14_BUNDLE_ROOT
            ff14_root.mkdir(parents=True, exist_ok=True)
            for filename in FF14_BUNDLE_REQUIRED_FILES:
                package_file = ff14_root / filename
                package_file.parent.mkdir(parents=True, exist_ok=True)
                package_file.write_bytes(
                    _fixture_manifest()
                    if filename == "yomihime.manifest.json"
                    else b"FF14 package input"
                )
            (root / "core" / "module.py").write_bytes(b"runtime source")
            for directory in (".cache", "cache", "data", "local_data"):
                local = root / "extensions" / directory
                local.mkdir()
                (local / "private.py").write_bytes(b"must not ship")

            names = {path.relative_to(root).as_posix() for path in included_files(root)}

            self.assertIn("core/module.py", names)
            self.assertFalse(any("private.py" in name for name in names))

            for relative in (
                "unreviewed.otf",
                "unreviewed.txt",
                "unreviewed.md",
                "helper.py",
            ):
                (root / "presentation/assets/fonts" / relative).write_bytes(b"private")
            names = {path.relative_to(root).as_posix() for path in included_files(root)}
            self.assertEqual(
                {name for name in names if name.startswith("presentation/assets/")},
                set(FONT_ASSETS),
            )
            for name in FONT_ASSETS:
                path = root / name
                original = path.read_bytes()
                path.unlink()
                with self.assertRaises(FileNotFoundError):
                    included_files(root)
                path.write_bytes(original)

    def test_pages_are_exact_required_assets_and_reject_hard_links(self) -> None:
        with tempfile.TemporaryDirectory(prefix="yomihime-release-pages-") as work:
            root = Path(work).resolve()
            for filename in ROOT_FILES:
                (root / filename).write_bytes(b"runtime root asset")
            for directory in RUNTIME_DIRS:
                (root / directory).mkdir()
            _write_operator_script_fixtures(root)
            ff14_root = root / FF14_BUNDLE_ROOT
            for filename in FF14_BUNDLE_REQUIRED_FILES:
                path = ff14_root / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(
                    _fixture_manifest()
                    if filename == "yomihime.manifest.json"
                    else b"FF14 package input"
                )
            (root / "pages/00-game-link/private.json").write_bytes(b"must not ship")
            (root / ".astrbot-plugin/i18n/private.json").write_bytes(b"must not ship")
            (root / "pages/unreviewed").mkdir()
            (root / "pages/unreviewed/index.html").write_bytes(b"must not ship")
            names = {path.relative_to(root).as_posix() for path in included_files(root)}
            self.assertEqual(
                {name for name in names if name.startswith("pages/")}, set(PAGE_FILES)
            )
            self.assertEqual(
                {name for name in names if name.startswith(".astrbot-plugin/")},
                set(HOST_METADATA_FILES),
            )
            sentinel = root / "private-sentinel"
            sentinel.write_bytes(b"private")
            for filename, label in (
                (PAGE_FILES[0], "Page asset"),
                (HOST_METADATA_FILES[0], "Host metadata"),
                (MAINTENANCE_HELPER_FILES[0], "Maintenance helper"),
            ):
                with self.subTest(filename=filename):
                    asset = root / filename
                    original = asset.read_bytes()
                    asset.unlink()
                    try:
                        if filename in PAGE_FILES:
                            with self.assertRaises(FileNotFoundError) as raised:
                                included_files(root)
                            self.assertIn(filename, str(raised.exception))
                            self.assertIn(
                                "npm run build --prefix pages/frontend",
                                str(raised.exception),
                            )
                        else:
                            with self.assertRaises(FileNotFoundError):
                                included_files(root)
                        os.link(sentinel, asset)
                        with self.assertRaisesRegex(
                            ValueError, label + " must not be a hard link"
                        ):
                            included_files(root)
                        self.assertEqual(sentinel.read_bytes(), b"private")
                    finally:
                        asset.unlink(missing_ok=True)
                        asset.write_bytes(original)

    def test_ff14_bundle_rejects_unreviewed_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="yomihime-release-bundle-") as work:
            root = Path(work).resolve()
            for filename in ROOT_FILES:
                (root / filename).write_bytes(b"runtime root asset")
            for directory in RUNTIME_DIRS:
                (root / directory).mkdir()
            _write_operator_script_fixtures(root)
            ff14_root = root / FF14_BUNDLE_ROOT
            ff14_root.mkdir(parents=True, exist_ok=True)
            for filename in FF14_BUNDLE_REQUIRED_FILES:
                package_file = ff14_root / filename
                package_file.parent.mkdir(parents=True, exist_ok=True)
                package_file.write_bytes(
                    _fixture_manifest()
                    if filename == "yomihime.manifest.json"
                    else b"FF14 package input"
                )
            (ff14_root / "unexpected.json").write_bytes(b"must not ship")

            with self.assertRaisesRegex(
                ValueError, "unsupported page resource extension"
            ):
                included_files(root)

    def test_ff14_bundle_rejects_unreviewed_source_directories(self) -> None:
        for unreviewed_path in (
            "fixtures/sample.py",
            "tests/private.py",
            "sample/helper.py",
            "unregistered.py",
        ):
            with (
                self.subTest(path=unreviewed_path),
                tempfile.TemporaryDirectory(
                    prefix="yomihime-release-bundle-source-"
                ) as work,
            ):
                root = Path(work).resolve()
                for filename in ROOT_FILES:
                    (root / filename).write_bytes(b"runtime root asset")
                for directory in RUNTIME_DIRS:
                    (root / directory).mkdir()
                _write_operator_script_fixtures(root)
                ff14_root = root / FF14_BUNDLE_ROOT
                ff14_root.mkdir(parents=True, exist_ok=True)
                for filename in FF14_BUNDLE_REQUIRED_FILES:
                    package_file = ff14_root / filename
                    package_file.parent.mkdir(parents=True, exist_ok=True)
                    package_file.write_bytes(
                        _fixture_manifest()
                        if filename == "yomihime.manifest.json"
                        else b"FF14 package input"
                    )
                unexpected = ff14_root / unreviewed_path
                unexpected.parent.mkdir(parents=True, exist_ok=True)
                unexpected.write_bytes(b"must not ship")

                with self.assertRaisesRegex(
                    ValueError, "Unsupported reviewed bundle source inventory"
                ):
                    included_files(root)

    def test_reviewed_release_compositions_have_exact_source_inventory(self) -> None:
        for bundle_roots, identity in (
            (("modules",), ("modules", "ff14")),
            (("examples/modules",), ("examples/modules", "minimal")),
        ):
            with self.subTest(identity=identity):
                payloads = _reviewed_bundle_payloads(ROOT, bundle_roots=bundle_roots)
                self.assertEqual(len(payloads), 1)
                _, payload = payloads[0]
                self.assertEqual(
                    {name for name in payload["sources"] if name.endswith(".py")},
                    REVIEWED_BUNDLE_SOURCES[identity],
                )

    def test_reviewed_nested_package_is_allowed_but_missing_source_is_rejected(
        self,
    ) -> None:
        import json

        identity = ("examples/modules", "minimal")
        reviewed = REVIEWED_BUNDLE_SOURCES[identity] | {
            "helpers/__init__.py",
            "helpers/facts.py",
        }
        with tempfile.TemporaryDirectory(prefix="yomihime-reviewed-layout-") as work:
            root = Path(work).resolve()
            package = root / "examples/modules/minimal"
            for name in reviewed:
                path = package / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"# reviewed fixture source\n")
            original = ROOT / "examples/modules/minimal"
            manifest = json.loads((original / "yomihime.manifest.json").read_bytes())
            for module in manifest["modules"]:
                module.pop("pages", None)
                module.pop("resources", None)
            (package / "yomihime.manifest.json").write_bytes(
                json.dumps(manifest).encode()
            )
            (package / "assembly.json").write_bytes(
                (original / "assembly.json").read_bytes()
            )
            (package / "README.md").write_bytes(b"reviewed fixture")
            with patch.dict(REVIEWED_BUNDLE_SOURCES, {identity: frozenset(reviewed)}):
                payloads = _reviewed_bundle_payloads(root, bundle_roots=(identity[0],))
                self.assertEqual(len(payloads), 1)
                self.assertEqual(
                    {
                        name
                        for name in payloads[0][1]["sources"]
                        if name.endswith(".py")
                    },
                    reviewed,
                )
                (package / "helpers/facts.py").unlink()
                with self.assertRaisesRegex(
                    ValueError, "Unsupported reviewed bundle source inventory"
                ):
                    _reviewed_bundle_payloads(root, bundle_roots=(identity[0],))

    def test_unknown_release_composition_is_rejected_before_capture(self) -> None:
        with tempfile.TemporaryDirectory(prefix="yomihime-unselected-package-") as work:
            root = Path(work).resolve()
            package = root / "modules/unselected"
            package.mkdir(parents=True)
            (package / "yomihime.manifest.json").write_bytes(b"{}")
            with self.assertRaisesRegex(
                ValueError, "Unsupported reviewed release composition"
            ):
                _reviewed_bundle_payloads(root)

    def test_ff14_bundle_rejects_external_links(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix="yomihime-release-link-") as work,
            tempfile.TemporaryDirectory(
                prefix="yomihime-release-outside-"
            ) as outside_work,
        ):
            root = Path(work).resolve()
            for filename in ROOT_FILES:
                (root / filename).write_bytes(b"runtime root asset")
            for directory in RUNTIME_DIRS:
                (root / directory).mkdir()
            _write_operator_script_fixtures(root)
            ff14_root = root / FF14_BUNDLE_ROOT
            ff14_root.mkdir(parents=True, exist_ok=True)
            for filename in FF14_BUNDLE_REQUIRED_FILES:
                package_file = ff14_root / filename
                package_file.parent.mkdir(parents=True, exist_ok=True)
                package_file.write_bytes(
                    _fixture_manifest()
                    if filename == "yomihime.manifest.json"
                    else b"FF14 package input"
                )
            baseline = included_files(root)
            self.assertTrue(
                {ff14_root / filename for filename in FF14_BUNDLE_REQUIRED_FILES}
                <= set(baseline)
            )
            outside = Path(outside_work).resolve() / "outside.py"
            outside.write_bytes(b"external source")
            link = ff14_root / "external.py"
            try:
                link.symlink_to(outside)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"symlink creation is unavailable: {exc}")

            try:
                self.assertTrue(link.is_symlink())
                self.assertTrue(link.exists())
                self.assertEqual(link.resolve(), outside)
                with self.assertRaisesRegex(ValueError, "^bundled_source_invalid$"):
                    included_files(root)
            finally:
                link.unlink(missing_ok=True)
            self.assertEqual(included_files(root), baseline)

    def test_sha256sums_lists_artifact_names_and_digests_in_order(self) -> None:
        with tempfile.TemporaryDirectory(prefix="yomihime-release-sums-") as work:
            root = Path(work)
            zip_path = root / "plugin-artifact.zip"
            wheel_path = root / "sdk.whl"
            zip_path.write_bytes(b"plugin archive")
            wheel_path.write_bytes(b"independent SDK wheel")

            sums = write_checksums((zip_path, wheel_path), root / "SHA256SUMS")

            expected = "".join(
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
                for path in sorted((zip_path, wheel_path), key=lambda item: item.name)
            )
            self.assertEqual(sums.read_text(encoding="ascii"), expected)

    def test_reused_sdk_wheel_still_requires_the_independent_pin(self) -> None:
        with tempfile.TemporaryDirectory(prefix="yomihime-release-wheel-") as work:
            root = Path(work)
            wheel = root / "yomihime_game_link_sdk-0.1.0a19-py3-none-any.whl"
            wheel.write_bytes(b"not the reviewed wheel")
            output = root / "artifacts"

            with self.assertRaisesRegex(ValueError, "independently pinned"):
                build_release(sdk_wheel=wheel, output_dir=output)
            self.assertFalse(output.exists())

    def test_built_release_zip_contains_the_closed_ff14_bundle(self) -> None:
        with tempfile.TemporaryDirectory(prefix="yomihime-release-ff14-") as work:
            work_root = Path(work)
            sdk_build_root = work_root / "sdk-build"
            sdk_build_root.mkdir()
            sdk_wheel = build_sdk_wheel(sdk_build_root, ROOT)
            archive_path, _, _ = build_release(
                sdk_wheel=sdk_wheel,
                output_dir=work_root / "artifacts",
                repository_root=ROOT,
            )

            with ZipFile(archive_path) as archive:
                self.assertIsNone(archive.testzip())
                names = set(archive.namelist())
                self.assertIn("requirements.txt", names)
                for name in FONT_ASSETS:
                    self.assertEqual(archive.read(name), (ROOT / name).read_bytes())
                self.assertEqual(
                    archive.read("requirements.txt"),
                    (ROOT / "requirements.txt").read_bytes(),
                )
                for filename in (
                    "README.md",
                    "modules/ff14/README.md",
                    *OPERATOR_SCRIPT_FILES,
                    *HOST_METADATA_FILES,
                ):
                    self.assertEqual(
                        archive.read(filename), (ROOT / filename).read_bytes()
                    )
                self.assertEqual(
                    {name for name in names if name.startswith("scripts/")},
                    set(OPERATOR_SCRIPT_FILES),
                )
                actual_ff14 = {
                    name for name in names if name.startswith("modules/ff14/")
                }
                expected_ff14 = {
                    path.relative_to(ROOT).as_posix()
                    for path in included_files(ROOT)
                    if path.relative_to(ROOT).parts[:2] == ("modules", "ff14")
                }
                self.assertEqual(actual_ff14, expected_ff14)
                actual_operator_scripts = {
                    name for name in names if name.startswith("scripts/")
                }
                self.assertEqual(actual_operator_scripts, set(OPERATOR_SCRIPT_FILES))
                self.assertTrue(
                    {
                        "modules/ff14/__init__.py",
                        "modules/ff14/module.py",
                        "modules/ff14/yomihime.manifest.json",
                        "modules/ff14/README.md",
                    }
                    <= actual_ff14
                )
                self.assertFalse(
                    any(
                        any(
                            part in {"tests", "fixtures", "sample", "samples"}
                            for part in Path(name).parts
                        )
                        for name in actual_ff14
                    )
                )
                self.assertFalse(
                    any(
                        stat.S_ISLNK(info.external_attr >> 16)
                        for info in archive.infolist()
                    )
                )

    def test_posix_wheel_normalizes_to_stable_windows_bytes_idempotently(self) -> None:
        with tempfile.TemporaryDirectory(prefix="yomihime-wheel-normalize-") as work:
            wheel_path = Path(work) / "sdk.whl"
            original_payloads, record_name = _write_posix_wheel(wheel_path)
            normalize_wheel_archive = _load_pep517_normalizer()
            self.assertTrue(normalize_wheel_archive(wheel_path))

            normalized_payloads = {
                **original_payloads,
                "yomihime_module_sdk-1.5.0.dist-info/METADATA": (
                    original_payloads["yomihime_module_sdk-1.5.0.dist-info/METADATA"]
                    .replace(b"\r\n", b"\n")
                    .replace(b"\n", b"\r\n")
                ),
            }
            with ZipFile(wheel_path) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(
                    tuple(archive.namelist()), (*normalized_payloads, record_name)
                )
                for info in archive.infolist():
                    self.assertEqual(info.create_system, 0)
                    mode = 0o100664 if info.filename == record_name else 0o100666
                    self.assertEqual(info.external_attr, mode << 16)
                self.assertEqual(
                    archive.read("yomihime_module_sdk-1.5.0.dist-info/METADATA"),
                    normalized_payloads["yomihime_module_sdk-1.5.0.dist-info/METADATA"],
                )
                self.assertEqual(
                    archive.read("yomihime_module_sdk-1.5.0.dist-info/WHEEL"),
                    original_payloads["yomihime_module_sdk-1.5.0.dist-info/WHEEL"],
                )
                self.assertEqual(
                    archive.read(record_name),
                    _render_record(normalized_payloads, record_name),
                )

            canonical_bytes = wheel_path.read_bytes()
            self.assertFalse(normalize_wheel_archive(wheel_path))
            self.assertEqual(wheel_path.read_bytes(), canonical_bytes)


if __name__ == "__main__":
    unittest.main()
