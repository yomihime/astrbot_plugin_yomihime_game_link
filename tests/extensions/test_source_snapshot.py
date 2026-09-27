"""Authorization-time bounded source capture tests."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from api.manifests import EXTENSION_MANIFEST_ABI
from extensions.discovery import (
    DiscoveryRootError,
    _require_handle_relative_support,
    discover_packages,
)
from extensions.source_snapshot import (
    MAX_PACKAGE_SOURCE_BYTES,
    SourceBundle,
    SourceSnapshotError,
    SourceSnapshotLimits,
    _bundle_digest,
    capture_source_bundle,
)


def _manifest(package_id: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "package_id": package_id,
        "package_version": "1.0.0",
        "contract_version": "1.1.0",
        "modules": [],
        "author": "Example",
        "license": "MIT",
        "source": "local",
    }


def _write_package(root: Path, package_id: str = "snapshot_pkg") -> Path:
    package = root / "pkg"
    package.mkdir()
    (package / EXTENSION_MANIFEST_ABI.filename).write_text(
        json.dumps(_manifest(package_id)), encoding="utf-8"
    )
    return package


def _safe_posix_scan_available() -> bool:
    if os.name == "nt":
        return False
    try:
        _require_handle_relative_support()
    except DiscoveryRootError:
        return False
    return True


class SourceSnapshotLimitTests(unittest.TestCase):
    def test_limits_only_lower_frozen_ceiling(self) -> None:
        lowered = SourceSnapshotLimits(max_package_bytes=1024, max_runtime_bytes=2048)
        self.assertEqual(lowered.max_package_bytes, 1024)
        with self.assertRaises(ValueError):
            SourceSnapshotLimits(max_package_bytes=MAX_PACKAGE_SOURCE_BYTES + 1)
        with self.assertRaises(ValueError):
            SourceSnapshotLimits(max_package_bytes=1024, max_runtime_bytes=512)

    def test_source_bundle_is_a_read_only_copy(self) -> None:
        mutable = {"module.py": b"value = 1"}
        content = mutable["module.py"]
        bundle = SourceBundle(mutable, _bundle_digest(mutable), len(content))
        mutable["module.py"] = b"value = 2"
        self.assertEqual(bundle.files["module.py"], b"value = 1")
        with self.assertRaises(TypeError):
            bundle.files["other.py"] = b"x"  # type: ignore[index]

    def test_untrusted_or_forged_provenance_is_rejected(self) -> None:
        from extensions.source_snapshot import PackageProvenance

        forged = PackageProvenance(
            platform="posix",
            root_locator="/tmp/root",
            package_name="pkg",
            package_id="pkg",
            manifest_sha256=hashlib.sha256(b"{}").digest(),
            manifest_abi=(1, EXTENSION_MANIFEST_ABI.filename, "local", 1),
        )
        with self.assertRaises(SourceSnapshotError):
            capture_source_bundle(forged, SourceSnapshotLimits())


@unittest.skipUnless(
    _safe_posix_scan_available(), "requires POSIX dirfd/no-follow support"
)
class PosixSourceSnapshotTests(unittest.TestCase):
    def test_scan_then_capture_and_later_disk_change_preserves_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = _write_package(root)
            source = package / "module.py"
            source.write_bytes(b"VALUE = 'captured'\n")
            candidate = discover_packages(root)[0]
            self.assertTrue(candidate.valid)

            bundle = capture_source_bundle(
                candidate._provenance, SourceSnapshotLimits()
            )
            source.write_bytes(b"VALUE = 'replaced'\n")
            self.assertEqual(bundle.files["module.py"], b"VALUE = 'captured'\n")

    def test_package_or_manifest_replacement_after_scan_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = _write_package(root)
            candidate = discover_packages(root)[0]
            package.rename(root / "moved")
            replacement = _write_package(root, "replacement_pkg")
            (replacement / "module.py").write_text(
                "raise AssertionError", encoding="utf-8"
            )
            with self.assertRaisesRegex(SourceSnapshotError, "candidate_stale"):
                capture_source_bundle(candidate._provenance, SourceSnapshotLimits())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = _write_package(root)
            candidate = discover_packages(root)[0]
            (package / EXTENSION_MANIFEST_ABI.filename).write_text(
                json.dumps(_manifest("changed_pkg")), encoding="utf-8"
            )
            with self.assertRaisesRegex(SourceSnapshotError, "candidate_stale"):
                capture_source_bundle(candidate._provenance, SourceSnapshotLimits())

    def test_source_symlink_and_hardlink_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = _write_package(root)
            outside = root / "outside.py"
            outside.write_text("raise AssertionError('outside')", encoding="utf-8")
            (package / "module.py").symlink_to(outside)
            candidate = discover_packages(root)[0]
            with self.assertRaisesRegex(SourceSnapshotError, "source_link_rejected"):
                capture_source_bundle(candidate._provenance, SourceSnapshotLimits())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = _write_package(root)
            source = package / "module.py"
            source.write_text("VALUE = 1", encoding="utf-8")
            os.link(source, package / "alias.py")
            candidate = discover_packages(root)[0]
            with self.assertRaisesRegex(
                SourceSnapshotError, "source_hardlink_rejected"
            ):
                capture_source_bundle(candidate._provenance, SourceSnapshotLimits())

    def test_source_byte_budget_is_enforced_at_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = _write_package(root)
            (package / "module.py").write_bytes(b"VALUE = 1")
            candidate = discover_packages(root)[0]
            limits = SourceSnapshotLimits(max_file_bytes=4, max_package_bytes=16)
            with self.assertRaisesRegex(SourceSnapshotError, "source_budget_exceeded"):
                capture_source_bundle(candidate._provenance, limits)


if __name__ == "__main__":
    unittest.main()
