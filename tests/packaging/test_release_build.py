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
    FF14_COMPAT_FILES,
    MAINTENANCE_HELPER_FILES,
    OPERATOR_SCRIPT_FILES,
    PAGE_FILES,
    ROOT_FILES,
    RUNTIME_DIRS,
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
    for filename in (*OPERATOR_SCRIPT_FILES, *PAGE_FILES, *MAINTENANCE_HELPER_FILES):
        path = root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"operator script fixture")
    for relative in FF14_COMPAT_FILES:
        canonical = root / FF14_BUNDLE_ROOT / relative
        canonical.parent.mkdir(parents=True, exist_ok=True)
        canonical.write_bytes((root / "pages/ff14" / Path(relative).name).read_bytes())


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
    return json.dumps(manifest).encode()


class ReleaseBuildTests(unittest.TestCase):
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
            (root / "pages/ff14/private.json").write_bytes(b"must not ship")
            (root / "pages/unreviewed").mkdir()
            (root / "pages/unreviewed/index.html").write_bytes(b"must not ship")
            names = {path.relative_to(root).as_posix() for path in included_files(root)}
            self.assertEqual(
                {name for name in names if name.startswith("pages/")}, set(PAGE_FILES)
            )
            projected = root / "pages/ff14/app.js"
            original_page = projected.read_bytes()
            projected.write_bytes(b"changed generated page")
            with self.assertRaisesRegex(ValueError, "projection differs"):
                included_files(root)
            projected.write_bytes(original_page)
            extra = ff14_root / "pages/compat/unreviewed.js"
            extra.write_bytes(b"unexpected canonical source")
            with self.assertRaisesRegex(ValueError, "Unreviewed compatibility locator"):
                included_files(root)
            extra.unlink()
            sentinel = root / "private-sentinel"
            sentinel.write_bytes(b"private")
            for filename, label in (
                (PAGE_FILES[0], "Page asset"),
                (MAINTENANCE_HELPER_FILES[0], "Maintenance helper"),
            ):
                with self.subTest(filename=filename):
                    asset = root / filename
                    original = asset.read_bytes()
                    asset.unlink()
                    with self.assertRaises(FileNotFoundError):
                        included_files(root)
                    os.link(sentinel, asset)
                    with self.assertRaisesRegex(
                        ValueError, label + " must not be a hard link"
                    ):
                        included_files(root)
                    asset.unlink()
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

            with self.assertRaisesRegex(ValueError, "Unsupported FF14 bundle file"):
                included_files(root)

    def test_ff14_bundle_rejects_unreviewed_source_directories(self) -> None:
        for unreviewed_path in (
            "fixtures/sample.py",
            "tests/private.py",
            "sample/helper.py",
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
                unexpected.parent.mkdir(parents=True)
                unexpected.write_bytes(b"must not ship")

                with self.assertRaisesRegex(
                    ValueError, "Unsupported FF14 bundle directory"
                ):
                    included_files(root)

    def test_ff14_bundle_rejects_external_links(self) -> None:
        with tempfile.TemporaryDirectory(prefix="yomihime-release-link-") as work:
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
            outside = root.parent / f"{root.name}-outside.py"
            outside.write_bytes(b"external source")
            try:
                (ff14_root / "external.py").symlink_to(outside)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"symlink creation is unavailable: {exc}")

            try:
                with self.assertRaisesRegex(ValueError, "symlink|reparse point"):
                    included_files(root)
            finally:
                outside.unlink(missing_ok=True)

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
            wheel = root / "yomihime_game_link_sdk-0.1.0a6-py3-none-any.whl"
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
                self.assertEqual(
                    archive.read("requirements.txt"),
                    (ROOT / "requirements.txt").read_bytes(),
                )
                for filename in (
                    "README.md",
                    "modules/ff14/README.md",
                    *OPERATOR_SCRIPT_FILES,
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
