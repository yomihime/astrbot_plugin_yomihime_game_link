"""Real Windows storage boundary; only newly created synthetic trees are used."""

import asyncio
import os
import shutil
import sqlite3
import unittest
import uuid
from unittest.mock import patch

from ygl_test_subject.infrastructure.managed_key_file import (
    ManagedKeyFileError,
    ManagedKeyFiles,
)
from ygl_test_subject.infrastructure.private_storage import (
    PrivateStorageError,
    resolve_private_storage,
)
from ygl_test_subject.infrastructure.windows.private_storage import (
    create_private_directory,
    identity_private_base,
)


@unittest.skipUnless(os.name == "nt", "Windows native storage boundary")
class WindowsPrivateStorageTests(unittest.TestCase):
    def setUp(self):
        self.base = identity_private_base()
        self.owned = create_private_directory(
            self.base, "ygl-cm-ui-synthetic-" + uuid.uuid4().hex
        )
        self.addCleanup(shutil.rmtree, self.owned)
        self.original = self.owned / "astrbot-data"
        self.original.mkdir()

    def layout(self):
        return resolve_private_storage(self.original, private_base=self.owned)

    def descriptor_directory(self, name, extra):
        import ctypes

        from ygl_test_subject.infrastructure.windows.maintenance import _SecurityNative
        from ygl_test_subject.infrastructure.windows.managed_key_io import (
            _bindings,
            _SecurityAttributes,
        )

        native = _SecurityNative()
        _bindings(native)
        descriptor = ctypes.c_void_p()
        path = self.owned / name
        self.assertTrue(
            native.security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                "D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;OW)" + extra,
                1,
                ctypes.byref(descriptor),
                None,
            )
        )
        try:
            attributes = _SecurityAttributes(
                ctypes.sizeof(_SecurityAttributes), descriptor, False
            )
            self.assertTrue(
                native.dll.CreateDirectoryW(str(path), ctypes.byref(attributes))
            )
        finally:
            native.LocalFree(descriptor)
        return path

    def test_native_relaxed_parent_rejects_outsider_replacement_and_control(self):
        from ygl_test_subject.infrastructure.windows.managed_key_io import _pin

        for mask in (0x40, 0x40000, 0x80000):
            with self.subTest(mask=mask):
                parent = self.descriptor_directory(
                    "unsafe-parent-" + str(mask), f"(A;OICI;0x{mask:08x};;;WD)"
                )
                with self.assertRaises(ManagedKeyFileError):
                    with _pin(parent, parent, private_data=False):
                        pass
                with self.assertRaises(ManagedKeyFileError):
                    create_private_directory(parent, "private-child")
                self.assertFalse((parent / "private-child").exists())

    def test_native_existing_child_with_effective_inherited_read_is_not_repaired(self):
        from ygl_test_subject.infrastructure.windows.maintenance import _SecurityNative
        from ygl_test_subject.infrastructure.windows.managed_key_io import (
            _bindings,
            _pin,
        )

        parent = self.descriptor_directory(
            "inherited-readable-parent", "(A;OICIIO;GRGX;;;AU)"
        )
        native = _SecurityNative()
        _bindings(native)
        child = parent / "existing-child"
        self.assertTrue(native.dll.CreateDirectoryW(str(child), None))
        with _pin(parent, parent, private_data=False):
            with self.assertRaises(ManagedKeyFileError):
                with _pin(child, child):
                    pass
            with self.assertRaises(ManagedKeyFileError):
                create_private_directory(parent, "existing-child")
        self.assertTrue(child.exists())

    def test_known_folder_ignores_environment_and_new_root_covers_sensitive_files(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.original / "untrusted")}):
            self.assertEqual(identity_private_base(), self.base)
        layout = self.layout()
        self.assertEqual(layout.security_status, "private")
        self.assertNotEqual(layout.storage_root, self.original)
        with layout.open_guard() as guard:
            connection = sqlite3.connect(layout.database_path)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE synthetic(value TEXT)")
            connection.execute("INSERT INTO synthetic VALUES('SYNTHETIC ONLY')")
            connection.commit()
            (layout.temp_root / "synthetic.tmp").write_bytes(b"synthetic")
            (layout.recovery_root / "synthetic.receipt").write_bytes(b"synthetic")
            ManagedKeyFiles(layout.key_data_root).create("a" * 32, b"s" * 32)
            guard.validate()
            self.assertTrue(
                layout.database_path.with_name("runtime.sqlite3-wal").exists()
            )
            self.assertTrue(
                layout.database_path.with_name("runtime.sqlite3-shm").exists()
            )
            connection.close()
            with self.assertRaises(PermissionError):
                layout.storage_root.rename(layout.storage_root.with_name("replaced"))
        self.assertEqual(self.layout().database_path, layout.database_path)

    def test_legacy_database_is_retained_and_ambiguous_inventory_is_rejected(self):
        legacy = self.original / "runtime.sqlite3"
        legacy.write_bytes(b"SYNTHETIC LEGACY BYTES")
        layout = self.layout()
        self.assertEqual(layout.database_path, legacy)
        self.assertEqual(layout.security_status, "legacy_review_required")
        self.assertEqual(legacy.read_bytes(), b"SYNTHETIC LEGACY BYTES")
        target = create_private_directory(layout.instance_root, "storage")
        (target / "runtime.sqlite3").write_bytes(b"SYNTHETIC AMBIGUITY")
        with self.assertRaises(PrivateStorageError) as error:
            self.layout()
        self.assertEqual(error.exception.code, "ambiguous_storage")
        self.assertEqual(legacy.read_bytes(), b"SYNTHETIC LEGACY BYTES")

    def test_hardlinks_in_sensitive_tree_are_rejected(self):
        layout = self.layout()
        with layout.open_guard() as guard:
            original = layout.temp_root / "original.tmp"
            original.write_bytes(b"SYNTHETIC ONLY")
            os.link(original, layout.recovery_root / "linked.receipt")
            with self.assertRaises(ManagedKeyFileError):
                guard.validate()

    def test_reparse_point_in_recovery_is_rejected(self):
        import _winapi

        layout = self.layout()
        with layout.open_guard() as guard:
            _winapi.CreateJunction(
                str(layout.temp_root), str(layout.recovery_root / "junction")
            )
            with self.assertRaises(ManagedKeyFileError):
                guard.validate()

    def test_migration_plan_is_read_only_and_unknown_source_proof_is_rejected(self):
        from ygl_test_subject.infrastructure.managed_key_manager import (
            ManagedKeyManager,
        )
        from ygl_test_subject.infrastructure.private_storage_migration import (
            execute_storage_migration,
            plan_storage_migration,
        )
        from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
        from ygl_test_subject.infrastructure.windows.private_storage import (
            WindowsPrivateStorageGuard,
        )

        database = SQLiteDatabase(self.original / "runtime.sqlite3")
        database.initialize()
        manager = ManagedKeyManager(database, self.original, self.original / "secrets")

        async def start_and_close():
            try:
                self.assertTrue((await manager.startup())["ready"])
            finally:
                await database.executor.close()

        with patch.dict(os.environ, {}, clear=True):
            asyncio.run(start_and_close())
            before = database.path.read_bytes()
            layout = self.layout()
            plan = plan_storage_migration(layout)
            self.assertEqual(database.path.read_bytes(), before)
            self.assertNotIn(str(self.original), str(plan.safe_projection()))
            with WindowsPrivateStorageGuard(layout.instance_root) as guard:
                with self.assertRaises(PrivateStorageError) as error:
                    execute_storage_migration(
                        plan, source_proof={"host_stopped": True}, private_guard=guard
                    )
                self.assertIn(
                    error.exception.code,
                    {"offline_supervisor_unavailable", "source_exclusivity_unproven"},
                )
            self.assertEqual(database.path.read_bytes(), before)

    def test_existing_parent_is_not_made_private_or_repaired(self):
        # Create a deliberately readable synthetic parent. Do not assume
        # Python's temporary-directory creation inherits a broad DACL.
        import ctypes

        from ygl_test_subject.infrastructure.windows.maintenance import _SecurityNative
        from ygl_test_subject.infrastructure.windows.managed_key_io import (
            _bindings,
            _pin,
            _SecurityAttributes,
        )

        native = _SecurityNative()
        _bindings(native)
        descriptor = ctypes.c_void_p()
        broad = self.owned / "readable-parent"
        self.assertTrue(
            native.security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                "D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;OW)(A;OICI;GRGX;;;AU)",
                1,
                ctypes.byref(descriptor),
                None,
            )
        )
        try:
            attributes = _SecurityAttributes(
                ctypes.sizeof(_SecurityAttributes), descriptor, False
            )
            self.assertTrue(
                native.dll.CreateDirectoryW(str(broad), ctypes.byref(attributes))
            )
        finally:
            native.LocalFree(descriptor)
        with _pin(broad, broad, private_data=False) as (
            native,
            _path,
            _serial,
            _operator,
            _trusted,
            handle,
            _maximum,
        ):
            before = native.descriptor(handle)
            child = create_private_directory(broad, "private-child")
            self.assertEqual(native.descriptor(handle), before)
            with _pin(child, child):
                pass
        with self.assertRaises(ManagedKeyFileError):
            with _pin(broad, broad):
                pass
