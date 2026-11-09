"""Real private imports and immutable whole-artifact capture."""

from __future__ import annotations

import hashlib
import importlib
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.extensions import runtime_bundle
from ygl_test_subject.extensions.hot_update_protocol import payload_digest
from ygl_test_subject.extensions.runtime_bundle import (
    RuntimeBundleError,
    RuntimeBundleStore,
)


def _release(files: dict[str, bytes], *, package_id: str = "sample") -> dict:
    inventory = [
        {"path": path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for path, data in files.items()
    ]
    return {
        "schema": "ygl.release/1",
        "plugin_id": {"author": "tests", "name": "sample"},
        "package_id": package_id,
        "release": "1.0.0",
        "channel": "stable",
        "release_sequence": 1,
        "payload_digest": payload_digest(inventory),
        "files": inventory,
    }


def _write(root: Path, files: dict[str, bytes]) -> None:
    for path, data in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


SDK = {
    f"yomihime_game_link_sdk/{path.name}": path.read_bytes()
    for path in (Path(__file__).parents[2] / "yomihime_game_link_sdk").glob("*.py")
}
SDK.update({"core_version.py": b"VALUE = 'old'\n", "templates/page.txt": b"old page"})
DLC = {
    "packages/sample/__init__.py": b"",
    "packages/sample/factory.py": (
        b"from .._api_v2 import ConfigSnapshot\n"
        b"class Factory:\n    async def create(self, services):\n        return None\n"
        b"    def config_type(self):\n        return ConfigSnapshot\n"
        b"    def lazy(self):\n        from . import late\n        return late.VALUE\n"
    ),
    "packages/sample/late.py": b"VALUE = 'old plugin'\n",
    "packages/sample/assets/logo.txt": b"old logo",
}


class PrivateRuntimeBundleTest(unittest.TestCase):
    def test_private_types_resources_lazy_and_release(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            core_dir, dlc_dir = base / "core", base / "dlc"
            _write(core_dir, SDK)
            _write(dlc_dir, DLC)
            store = RuntimeBundleStore()
            core = store.capture(core_dir, _release(SDK), kind="core")
            dlc = store.capture(dlc_dir, _release(DLC), kind="dlc")
            core.mount_core()
            dlc.mount_package(core, "sample", "packages/sample")
            old_type = importlib.import_module(
                f"{core.namespace}.yomihime_game_link_sdk.services"
            ).ModuleFactory
            factory = dlc.resolve("factory:Factory")
            self.assertIsInstance(factory, old_type)
            self.assertIs(
                factory.config_type(),
                importlib.import_module(
                    f"{core.namespace}.yomihime_game_link_sdk.services"
                ).ConfigSnapshot,
            )
            self.assertEqual(core.read_resource("templates/page.txt"), b"old page")
            self.assertEqual(
                (core.resource_root / "templates/page.txt").read_bytes(), b"old page"
            )
            self.assertEqual(
                (dlc.resource_root / "packages/sample/assets/logo.txt").read_bytes(),
                b"old logo",
            )

            # Replacing mutable installation paths cannot affect either old lease.
            new_sdk = {path: data.replace(b"old", b"new") for path, data in SDK.items()}
            new_dlc = {path: data.replace(b"old", b"new") for path, data in DLC.items()}
            _write(core_dir, new_sdk)
            _write(dlc_dir, new_dlc)
            (dlc_dir / "packages/sample/absent.py").write_bytes(
                b"VALUE = 'disk only'\n"
            )
            self.assertEqual(dlc.resolve("factory:Factory").lazy(), "old plugin")
            with self.assertRaises(ModuleNotFoundError):
                importlib.import_module(f"{dlc.namespace}.absent")
            self.assertEqual(core.read_resource("templates/page.txt"), b"old page")
            (dlc_dir / "packages/sample/absent.py").unlink()
            new_core = store.capture(core_dir, _release(new_sdk), kind="core")
            new_child = store.capture(dlc_dir, _release(new_dlc), kind="dlc")
            new_core.mount_core()
            new_child.mount_package(new_core, "sample", "packages/sample")
            self.assertEqual(new_core.read_resource("templates/page.txt"), b"new page")
            self.assertEqual(new_child.resolve("factory:Factory").lazy(), "new plugin")
            self.assertEqual(
                dlc.read_resource("packages/sample/assets/logo.txt"), b"old logo"
            )
            self.assertEqual(
                new_child.read_resource("packages/sample/assets/logo.txt"), b"new logo"
            )
            self.assertEqual(core.release(), "retained_busy")
            dlc.retain()
            self.assertEqual(dlc.release(), "retained_busy")
            dlc.drop()
            self.assertEqual(dlc.release(), "released")
            self.assertFalse(dlc.files)
            self.assertIsNone(dlc._parent)
            self.assertEqual(core.release(), "released")
            self.assertFalse(core.files)
            self.assertEqual(new_child.release(), "released")
            self.assertEqual(new_core.release(), "released")
            self.assertFalse(
                any(name.startswith(core.namespace) for name in sys.modules)
            )

    def test_explicit_parent_preserves_core_and_child_lease_lifetimes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            core_root, dlc_root = root / "core", root / "dlc"
            _write(core_root, SDK)
            _write(dlc_root, DLC)
            parent = root / "leases"
            store = RuntimeBundleStore(materialization_parent=parent)
            core = store.capture(core_root, _release(SDK), kind="core")
            child = store.capture(dlc_root, _release(DLC), kind="dlc")
            self.assertEqual(core.resource_root.parent, parent)
            self.assertEqual(child.resource_root.parent, parent)
            core.mount_core()
            child.mount_package(core, "sample", "packages/sample")
            self.assertEqual(core.release(), "retained_busy")
            child.retain()
            self.assertEqual(child.release(), "retained_busy")
            child.drop()
            self.assertEqual(child.release(), "released")
            self.assertEqual(core.release(), "released")
            self.assertTrue(parent.is_dir())
            self.assertEqual(list(parent.iterdir()), [])
            self.assertEqual(store.reserved_bytes("core"), 0)
            self.assertEqual(store.reserved_bytes("dlc"), 0)

    def test_materialization_parent_link_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "target"
            target.mkdir()
            alias = root / "alias"
            alias.symlink_to(target, target_is_directory=True)
            for parent in (alias, alias / "leases"):
                with (
                    self.subTest(parent=parent.name),
                    self.assertRaisesRegex(
                        RuntimeBundleError, "materialization_parent_rejected"
                    ),
                ):
                    RuntimeBundleStore(materialization_parent=parent)
            self.assertEqual(list(target.iterdir()), [])

    def test_materialization_parent_reparse_metadata_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            parent = root / "leases"
            parent.mkdir()
            original_lstat = Path.lstat

            def reparse_metadata(path, *args, **kwargs):
                info = original_lstat(path, *args, **kwargs)
                if path == parent:
                    return SimpleNamespace(
                        st_mode=info.st_mode,
                        st_file_attributes=getattr(info, "st_file_attributes", 0)
                        | 0x400,
                    )
                return info

            with (
                patch.object(Path, "lstat", reparse_metadata),
                self.assertRaisesRegex(
                    RuntimeBundleError, "materialization_parent_rejected"
                ),
            ):
                RuntimeBundleStore(materialization_parent=parent)
            self.assertEqual(list(parent.iterdir()), [])

    def test_replaced_parent_is_rejected_before_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write(root / "core", SDK)
            parent = root / "leases"
            store = RuntimeBundleStore(materialization_parent=parent)
            parent.rename(root / "original")
            parent.mkdir()
            with self.assertRaisesRegex(
                RuntimeBundleError, "materialization_parent_changed"
            ):
                store.capture(root / "core", _release(SDK), kind="core")
            self.assertEqual(list(parent.iterdir()), [])
            self.assertEqual(store.reserved_bytes("core"), 0)

    def test_parent_replaced_during_creation_keeps_replacement_untouched(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write(root / "core", SDK)
            parent = root / "leases"
            store = RuntimeBundleStore(materialization_parent=parent)
            original_mkdtemp = tempfile.mkdtemp
            marker = []

            def replace_parent(*args, **kwargs):
                created = Path(original_mkdtemp(*args, **kwargs))
                parent.rename(root / "original")
                parent.mkdir()
                replacement = parent / created.name
                replacement.mkdir()
                marker.append(replacement / "untouched.txt")
                marker[0].write_bytes(b"replacement belongs to another owner")
                return str(created)

            with (
                patch.object(runtime_bundle.tempfile, "mkdtemp", replace_parent),
                self.assertRaisesRegex(
                    RuntimeBundleError, "materialization_parent_changed"
                ),
            ):
                store.capture(root / "core", _release(SDK), kind="core")
            self.assertEqual(
                marker[0].read_bytes(), b"replacement belongs to another owner"
            )
            self.assertEqual(list(marker[0].parent.iterdir()), marker)
            self.assertEqual(store.reserved_bytes("core"), 0)

    def test_release_refuses_a_replaced_lease_root_and_remains_retryable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write(root / "core", SDK)
            store = RuntimeBundleStore(materialization_parent=root / "leases")
            lease = store.capture(root / "core", _release(SDK), kind="core")
            original = lease.resource_root
            preserved = original.with_name(original.name + "-original")
            original.rename(preserved)
            original.mkdir()
            marker = original / "untouched.txt"
            marker.write_bytes(b"replacement")
            try:
                with self.assertRaisesRegex(
                    RuntimeBundleError, "materialization_parent_changed"
                ):
                    lease.release()
                self.assertEqual(marker.read_bytes(), b"replacement")
                self.assertGreater(store.reserved_bytes("core"), 0)
            finally:
                marker.unlink()
                original.rmdir()
                preserved.rename(original)
                self.assertEqual(lease.release(), "released")
            self.assertEqual(store.reserved_bytes("core"), 0)

    def test_final_materialization_identity_failure_cleans_owned_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write(root / "core", SDK)
            parent = root / "leases"
            store = RuntimeBundleStore(materialization_parent=parent)
            original_identity = runtime_bundle._materialization_directory_identity
            root_reads = 0

            def fail_final_identity(path):
                nonlocal root_reads
                if path.parent == parent:
                    root_reads += 1
                    if root_reads == 2:
                        raise RuntimeBundleError("injected_final_identity_failure")
                return original_identity(path)

            with (
                patch.object(
                    runtime_bundle,
                    "_materialization_directory_identity",
                    fail_final_identity,
                ),
                self.assertRaisesRegex(
                    RuntimeBundleError, "injected_final_identity_failure"
                ),
            ):
                store.capture(root / "core", _release(SDK), kind="core")
            self.assertEqual(list(parent.iterdir()), [])
            self.assertEqual(store.reserved_bytes("core"), 0)

    def test_materialization_handoff_performs_no_additional_identity_read(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write(root / "core", SDK)
            parent = root / "leases"
            store = RuntimeBundleStore(materialization_parent=parent)
            original_identity = runtime_bundle._materialization_directory_identity
            original_materialize = runtime_bundle._materialize
            handed_off = False
            late_reads = 0

            def materialize(*args, **kwargs):
                nonlocal handed_off
                value = original_materialize(*args, **kwargs)
                handed_off = True
                return value

            def fail_after_handoff(path):
                nonlocal late_reads
                if handed_off:
                    late_reads += 1
                    raise RuntimeBundleError("injected_post_materialization_failure")
                return original_identity(path)

            with (
                patch.object(runtime_bundle, "_materialize", materialize),
                patch.object(
                    runtime_bundle,
                    "_materialization_directory_identity",
                    fail_after_handoff,
                ),
            ):
                lease = store.capture(root / "core", _release(SDK), kind="core")
            self.assertTrue(handed_off)
            self.assertEqual(late_reads, 0)
            self.assertEqual(lease.resource_root.parent, parent)
            self.assertEqual(lease.release(), "released")
            self.assertEqual(list(parent.iterdir()), [])
            self.assertEqual(store.reserved_bytes("core"), 0)

    def test_generation_types_and_missing_import(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            _write(base / "core", SDK)
            _write(base / "dlc", DLC)
            store = RuntimeBundleStore()
            first = store.capture(base / "core", _release(SDK), kind="core")
            second = store.capture(base / "core", _release(SDK), kind="core")
            first.mount_core()
            second.mount_core()
            child = store.capture(base / "dlc", _release(DLC), kind="dlc")
            child.mount_package(first, "sample", "packages/sample")
            sibling = store.capture(base / "dlc", _release(DLC), kind="dlc")
            sibling.mount_package(first, "sample", "packages/sample")
            first_type = importlib.import_module(
                f"{first.namespace}.yomihime_game_link_sdk.services"
            ).ModuleFactory
            second_type = importlib.import_module(
                f"{second.namespace}.yomihime_game_link_sdk.services"
            ).ModuleFactory
            first_value = importlib.import_module(
                f"{first.namespace}.yomihime_game_link_sdk.services"
            ).ConfigSnapshot
            sibling_value = importlib.import_module(
                f"{sibling.namespace.rsplit('.packages.', 1)[0]}.yomihime_game_link_sdk.services"
            ).ConfigSnapshot
            second_value = importlib.import_module(
                f"{second.namespace}.yomihime_game_link_sdk.services"
            ).ConfigSnapshot
            self.assertIsNot(first_type, second_type)
            self.assertIs(first_value, sibling_value)
            self.assertIsNot(first_value, second_value)
            self.assertIs(child.resolve("factory:Factory").config_type(), first_value)
            self.assertIsNot(
                child.resolve("factory:Factory").config_type(), second_value
            )
            with self.assertRaises(ModuleNotFoundError):
                importlib.import_module(f"{first.namespace}.not_in_inventory")
            self.assertEqual(child.release(), "released")
            self.assertEqual(sibling.release(), "released")
            self.assertEqual(first.release(), "released")
            self.assertEqual(second.release(), "released")

    def test_invalid_inventory_and_budget_preserve_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write(root, SDK)
            store = RuntimeBundleStore(core_capacity=128 * 1024 * 1024)
            active = store.capture(root, _release(SDK), kind="core")
            with self.assertRaisesRegex(
                RuntimeBundleError, "runtime_bundle_budget_exceeded"
            ):
                store.capture(root, _release(SDK), kind="core")
            active.mount_core()
            self.assertIsNotNone(active.namespace)
            broken = dict(SDK)
            broken["extra.txt"] = b"not installed"
            with self.assertRaises(RuntimeBundleError):
                store.capture(root, _release(broken), kind="core")
            self.assertEqual(active.release(), "released")
            self.assertEqual(store.reserved_bytes("core"), 0)

    def test_repeated_mount_release_restores_importer_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            core_root = root / "core"
            _write(core_root, SDK)
            dlc_root = root / "dlc"
            _write(dlc_root, DLC)
            store = RuntimeBundleStore()
            before = len(sys.meta_path)
            for _ in range(10):
                lease = store.capture(core_root, _release(SDK), kind="core")
                lease.mount_core()
                importlib.import_module(
                    f"{lease.namespace}.yomihime_game_link_sdk.services"
                )
                child = store.capture(dlc_root, _release(DLC), kind="dlc")
                child.mount_package(lease, "sample", "packages/sample")
                child.resolve("factory:Factory")
                self.assertEqual(child.release(), "released")
                self.assertEqual(lease.release(), "released")
            self.assertEqual(len(sys.meta_path), before)
            self.assertEqual(store.reserved_bytes("core"), 0)
            self.assertEqual(store.reserved_bytes("dlc"), 0)

    def test_cleanup_failure_keeps_lease_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write(root, SDK)
            store = RuntimeBundleStore()
            lease = store.capture(root, _release(SDK), kind="core")
            lease.mount_core()
            with patch.object(
                runtime_bundle, "_remove_materialized", side_effect=OSError("busy")
            ):
                with self.assertRaisesRegex(
                    RuntimeBundleError, "resource_cleanup_failed"
                ):
                    lease.release()
            self.assertGreater(store.reserved_bytes("core"), 0)
            self.assertEqual(lease.read_resource("templates/page.txt"), b"old page")
            self.assertIn(lease._finder, sys.meta_path)
            self.assertEqual(lease.release(), "released")
            self.assertEqual(store.reserved_bytes("core"), 0)

    def test_unlisted_file_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write(root, SDK)
            store = RuntimeBundleStore()
            (root / "extra.txt").write_bytes(b"x")
            with self.assertRaisesRegex(RuntimeBundleError, "unlisted_file"):
                store.capture(root, _release(SDK), kind="core")

    def test_native_file_symlink_rejected(self) -> None:
        # This is an actual OS link probe. Creation errors remain errors and do
        # not count as executing the capture guard or as synthetic coverage.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write(root, SDK)
            store = RuntimeBundleStore()
            (root / "linked.txt").symlink_to(root / "core_version.py")
            with self.assertRaisesRegex(RuntimeBundleError, "bundle_link_rejected"):
                store.capture(root, _release(SDK), kind="core")

    def test_reserved_package_alias_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = RuntimeBundleStore()
            dlc_root = root / "dlc"
            alias = {"packages/sample/_api_v2.py": b"bad"}
            _write(dlc_root, alias)
            with self.assertRaisesRegex(RuntimeBundleError, "reserved_package_path"):
                store.capture(dlc_root, _release(alias), kind="dlc")

    def test_changed_hash_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write(root, SDK)
            store = RuntimeBundleStore()
            (root / "core_version.py").write_bytes(b"changed")
            with self.assertRaisesRegex(
                RuntimeBundleError, "bundle_hash_mismatch|bundle_file_invalid"
            ):
                store.capture(root, _release(SDK), kind="core")

    def test_case_collision_synthetic_enumeration_rejected(self) -> None:
        # Supply a closed directory enumeration with two distinct casefold
        # aliases. This tests the real guard, not native filesystem support for
        # creating both names in one directory.
        class Entries(list):
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write(root, SDK)
            entries = Entries(SimpleNamespace(name=p.name) for p in root.iterdir())
            entries.append(SimpleNamespace(name="Core_Version.py"))
            store = RuntimeBundleStore()
            with (
                patch.object(runtime_bundle.os, "scandir", return_value=entries),
                self.assertRaisesRegex(RuntimeBundleError, "case_collision"),
            ):
                store.capture(root, _release(SDK), kind="core")

    def test_case_alias_on_actual_filesystem_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write(root, SDK)
            original = root / "core_version.py"
            alias = root / "Core_Version.py"
            alias.write_bytes(b"collision")
            names = {p.name for p in root.iterdir()}
            store = RuntimeBundleStore()
            if {original.name, alias.name}.issubset(names):
                self.assertFalse(original.samefile(alias))
                self.assertEqual(original.read_bytes(), SDK["core_version.py"])
                expected = "case_collision"
            else:
                self.assertTrue(original.samefile(alias))
                self.assertEqual(original.read_bytes(), b"collision")
                expected = "bundle_file_invalid|bundle_hash_mismatch"
            with self.assertRaisesRegex(RuntimeBundleError, expected):
                store.capture(root, _release(SDK), kind="core")

    def test_generated_cache_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write(root, SDK)
            store = RuntimeBundleStore()
            cache = root / "__pycache__"
            cache.mkdir()
            (cache / "module.pyc").write_bytes(b"generated")
            lease = store.capture(root, _release(SDK), kind="core")
            self.assertEqual(lease.release(), "released")

    def test_native_generated_cache_symlink_rejected(self) -> None:
        # Keep this independent so a missing OS link privilege cannot hide the
        # successful ordinary generated-cache capture above.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write(root, SDK)
            store = RuntimeBundleStore()
            cache = root / "__pycache__"
            cache.mkdir()
            (cache / "linked.pyc").symlink_to(root / "core_version.py")
            with self.assertRaisesRegex(RuntimeBundleError, "bundle_link_rejected"):
                store.capture(root, _release(SDK), kind="core")

    def test_windows_capture_synthetic_link_and_reparse_metadata_rejected(self) -> None:
        # Exercise the Windows guard implementation with controlled metadata on
        # ordinary files. This is not an actual OS symlink/reparse qualification.
        for cached in (False, True):
            for kind in ("symlink", "reparse"):
                with self.subTest(cached=cached, kind=kind):
                    with tempfile.TemporaryDirectory() as temp:
                        root = Path(temp)
                        _write(root, SDK)
                        parent = root / "__pycache__" if cached else root
                        parent.mkdir(exist_ok=True)
                        probe = parent / "000-linked.pyc"
                        probe.write_bytes(b"ordinary file")
                        original_lstat = Path.lstat
                        info = original_lstat(probe)
                        fields = {
                            name: getattr(info, name)
                            for name in dir(info)
                            if name.startswith("st_")
                        }
                        if kind == "symlink":
                            fields["st_mode"] = stat.S_IFLNK
                        else:
                            fields["st_file_attributes"] = getattr(
                                info, "st_file_attributes", 0
                            ) | getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

                        def controlled_lstat(path):
                            if path == probe:
                                return SimpleNamespace(**fields)
                            return original_lstat(path)

                        listed = {row["path"]: row for row in _release(SDK)["files"]}
                        with (
                            patch.object(Path, "lstat", controlled_lstat),
                            self.assertRaisesRegex(
                                RuntimeBundleError, "bundle_link_rejected"
                            ),
                        ):
                            runtime_bundle._capture_windows_tree(
                                root, listed, runtime_bundle.CORE_MAX_FILE_BYTES
                            )
