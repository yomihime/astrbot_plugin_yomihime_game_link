"""Check release tag, runtime manifest, and checksum boundaries."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.build_dashboard_zip import ROOT_FILES, RUNTIME_DIRS, included_files
from scripts.build_release import (
    build_release,
    plugin_metadata,
    validate_tag,
    write_checksums,
)

ROOT = Path(__file__).resolve().parents[2]


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
            root = Path(work)
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


if __name__ == "__main__":
    unittest.main()
