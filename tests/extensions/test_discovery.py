"""Filesystem containment and static-only extension discovery tests."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from ygl_test_subject.core.contracts.manifests import EXTENSION_MANIFEST_ABI
from ygl_test_subject.extensions.discovery import (
    EXTENSION_ROOT_MAX_ENTRIES,
    EXTENSION_ROOT_MAX_PACKAGES,
    DiscoveryRootError,
    _bounded_entry_names,
    _check_package_budget,
    _open_flags,
    _read_manifest_at,
    _require_handle_relative_support,
    discover_packages,
)


def empty_package(package_id: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "package_id": package_id,
        "package_version": "1.0.0",
        "contract_version": "2.0",
        "modules": [],
        "author": "Example",
        "license": "MIT",
        "source": "local",
    }


def write_package(root: Path, folder: str, package_id: str) -> Path:
    package_dir = root / folder
    package_dir.mkdir()
    (package_dir / EXTENSION_MANIFEST_ABI.filename).write_text(
        json.dumps(empty_package(package_id)), encoding="utf-8"
    )
    return package_dir


def supports_handle_relative_scan() -> bool:
    try:
        _require_handle_relative_support()
    except DiscoveryRootError:
        return False
    return True


HANDLE_RELATIVE_SCAN = supports_handle_relative_scan()


class DiscoveryTests(unittest.TestCase):
    def test_empty_root_and_missing_root_behavior(self) -> None:
        with tempfile.TemporaryDirectory(
            dir=Path.cwd() if os.name == "nt" else None
        ) as temporary:
            if HANDLE_RELATIVE_SCAN or os.name == "nt":
                try:
                    self.assertEqual(discover_packages(temporary), ())
                except DiscoveryRootError:
                    if os.name != "nt":
                        raise
                    self.skipTest(
                        "Windows native scanner is unavailable on this matrix"
                    )
            else:
                with self.assertRaisesRegex(DiscoveryRootError, "handle-relative"):
                    discover_packages(temporary)
        with self.assertRaises(DiscoveryRootError):
            discover_packages(None)

    def test_root_entry_and_package_budgets_are_checked_before_growth(self) -> None:
        visited = 0

        def entries():
            nonlocal visited
            for index in range(EXTENSION_ROOT_MAX_ENTRIES + 10):
                visited += 1
                yield f"entry_{index}"

        with self.assertRaisesRegex(DiscoveryRootError, "entry budget"):
            _bounded_entry_names(entries())
        self.assertEqual(visited, EXTENSION_ROOT_MAX_ENTRIES + 1)
        with self.assertRaisesRegex(DiscoveryRootError, "package budget"):
            _check_package_budget(EXTENSION_ROOT_MAX_PACKAGES + 1)

    @unittest.skipUnless(
        HANDLE_RELATIVE_SCAN, "platform lacks safe dirfd/no-follow scan"
    )
    def test_bad_package_is_isolated_and_duplicate_ids_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_package(root, "one", "duplicate_pkg")
            write_package(root, "two", "duplicate_pkg")
            (root / "bad").mkdir()
            (root / "bad" / EXTENSION_MANIFEST_ABI.filename).write_text(
                "{", encoding="utf-8"
            )
            items = discover_packages(root)
            self.assertEqual(len(items), 3)
            self.assertTrue(all(item.diagnostic for item in items))
            self.assertEqual(
                sum(item.diagnostic == "duplicate_package_id" for item in items), 2
            )

    @unittest.skipUnless(
        HANDLE_RELATIVE_SCAN, "platform lacks safe dirfd/no-follow scan"
    )
    def test_manifest_factory_entry_is_never_imported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package_dir = write_package(root, "pkg", "side_effect_pkg")
            document = empty_package("side_effect_pkg")
            document["modules"] = [
                {
                    "module_id": "side_effect",
                    "route": "side_effect",
                    "category": "platform",
                    "factory_entry": "fixture_module:Factory",
                    "module_version": "1.0.0",
                    "capabilities": [],
                }
            ]
            (package_dir / EXTENSION_MANIFEST_ABI.filename).write_text(
                json.dumps(document), encoding="utf-8"
            )
            marker = package_dir / "imported.marker"
            (package_dir / "fixture_module.py").write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
                encoding="utf-8",
            )
            before = set(__import__("sys").modules)
            found = discover_packages(root)
            self.assertTrue(found[0].valid)
            self.assertFalse(marker.exists())
            self.assertEqual(set(__import__("sys").modules) - before, set())

    @unittest.skipUnless(
        HANDLE_RELATIVE_SCAN, "platform lacks safe dirfd/no-follow scan"
    )
    def test_rejects_package_symlink_and_outside_manifest_link(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            outside = Path(temporary) / "outside"
            root.mkdir()
            outside.mkdir()
            write_package(outside, "escape", "outside_pkg")
            try:
                (root / "package_link").symlink_to(
                    outside / "escape", target_is_directory=True
                )
            except (OSError, NotImplementedError):
                self.skipTest("directory symlinks are unavailable on this host")
            items = discover_packages(root)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].diagnostic, "package_link_rejected")

    @unittest.skipUnless(
        HANDLE_RELATIVE_SCAN, "platform lacks safe dirfd/no-follow scan"
    )
    def test_rejects_symlink_as_injected_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            actual = base / "actual"
            actual.mkdir()
            root_link = base / "linked"
            try:
                root_link.symlink_to(actual, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory symlinks are unavailable on this host")
            with self.assertRaises(DiscoveryRootError):
                discover_packages(root_link)

    @unittest.skipUnless(
        HANDLE_RELATIVE_SCAN, "platform lacks safe dirfd/no-follow scan"
    )
    def test_open_package_handle_survives_path_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = write_package(root, "package", "original_pkg")
            original_bytes = (original / EXTENSION_MANIFEST_ABI.filename).read_bytes()
            root_fd = os.open(root, _open_flags(directory=True))
            try:
                package_fd = os.open(
                    "package", _open_flags(directory=True), dir_fd=root_fd
                )
                try:
                    original.rename(root / "renamed_original")
                    replacement = root / "package"
                    replacement.mkdir()
                    (replacement / EXTENSION_MANIFEST_ABI.filename).write_text(
                        json.dumps(empty_package("replacement_pkg")), encoding="utf-8"
                    )
                    self.assertEqual(_read_manifest_at(package_fd), original_bytes)
                finally:
                    os.close(package_fd)
            finally:
                os.close(root_fd)

    def test_unsupported_platform_fails_closed_before_reading_root(self) -> None:
        if HANDLE_RELATIVE_SCAN or os.name == "nt":
            self.skipTest("platform provides the safe native or dirfd scanner path")
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(DiscoveryRootError, "handle-relative"):
                discover_packages(temporary)


if __name__ == "__main__":
    unittest.main()
