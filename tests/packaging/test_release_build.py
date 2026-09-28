"""Check release tag, runtime manifest, and checksum boundaries."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from setuptools import build_meta

from scripts.build_dashboard_zip import ROOT_FILES, RUNTIME_DIRS, included_files
from scripts.build_release import (
    build_release,
    plugin_metadata,
    validate_tag,
    write_checksums,
)

ROOT = Path(__file__).resolve().parents[2]


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
    record_name = "yomihime_module_sdk-1.1.0.dist-info/RECORD"
    payloads = {
        "yomihime_sdk/__init__.py": b"SDK_VALUE = 1\n",
        "yomihime_module_sdk-1.1.0.dist-info/METADATA": (
            b"Metadata-Version: 2.4\nName: yomihime-module-sdk\nVersion: 1.1.0\n"
        ),
        "yomihime_module_sdk-1.1.0.dist-info/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: fixture\n"
            b"Root-Is-Purelib: true\nTag: py3-none-any\n"
        ),
        "yomihime_module_sdk-1.1.0.dist-info/top_level.txt": b"yomihime_sdk\n",
        "yomihime_module_sdk-1.1.0.dist-info/licenses/LICENSE": b"MIT\n",
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
                "__init__.py",
                "metadata.yaml",
                "logo.png",
                "LICENSE",
                "README.md",
                "CHANGELOG.md",
                "extensions/disk_manifest.py",
                "infrastructure/sqlite/migrations/0000_schema_migrations.sql",
            }
            <= names
        )
        excluded_parts = {
            ".architecture-refactor",
            ".coordination",
            "docs",
            "examples",
            "tests",
            "dist",
            "__pycache__",
        }
        self.assertFalse(
            any(excluded_parts.intersection(Path(name).parts) for name in names)
        )
        self.assertFalse(any(Path(name).suffix in {".pyc", ".pyo"} for name in names))

    def test_runtime_manifest_skips_cache_and_local_data_directories(self) -> None:
        with tempfile.TemporaryDirectory(prefix="yomihime-release-manifest-") as work:
            root = Path(work).resolve()
            for filename in ROOT_FILES:
                (root / filename).write_bytes(b"runtime root asset")
            for directory in RUNTIME_DIRS:
                (root / directory).mkdir()
            (root / "core" / "module.py").write_bytes(b"runtime source")
            for directory in (".cache", "cache", "data", "local_data"):
                local = root / "extensions" / directory
                local.mkdir()
                (local / "private.py").write_bytes(b"must not ship")

            names = {path.relative_to(root).as_posix() for path in included_files(root)}

            self.assertIn("core/module.py", names)
            self.assertFalse(any("private.py" in name for name in names))

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
            wheel = root / "yomihime_module_sdk-1.1.0-py3-none-any.whl"
            wheel.write_bytes(b"not the reviewed wheel")
            output = root / "artifacts"

            with self.assertRaisesRegex(ValueError, "independently pinned"):
                build_release(sdk_wheel=wheel, output_dir=output)
            self.assertFalse(output.exists())

    def test_posix_wheel_normalizes_to_stable_windows_bytes_idempotently(self) -> None:
        with tempfile.TemporaryDirectory(prefix="yomihime-wheel-normalize-") as work:
            wheel_path = Path(work) / "sdk.whl"
            original_payloads, record_name = _write_posix_wheel(wheel_path)
            normalize_wheel_archive = _load_pep517_normalizer()
            self.assertTrue(normalize_wheel_archive(wheel_path))

            normalized_payloads = {
                **original_payloads,
                "yomihime_module_sdk-1.1.0.dist-info/METADATA": (
                    original_payloads["yomihime_module_sdk-1.1.0.dist-info/METADATA"]
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
                    archive.read("yomihime_module_sdk-1.1.0.dist-info/METADATA"),
                    normalized_payloads["yomihime_module_sdk-1.1.0.dist-info/METADATA"],
                )
                self.assertEqual(
                    archive.read("yomihime_module_sdk-1.1.0.dist-info/WHEEL"),
                    original_payloads["yomihime_module_sdk-1.1.0.dist-info/WHEEL"],
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
