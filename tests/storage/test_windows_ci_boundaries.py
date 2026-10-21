"""Hosted Windows primitives, not workstation storage qualification.

These tests never bypass the production OS/Python matrix. They exercise actual
Win32 descriptor decoding on newly created inert directories, early selector
refusals and the public migration authority gate without starting a Host.
"""

import ctypes
import os
import tempfile
import unittest
from pathlib import Path

from ygl_test_subject.infrastructure import private_storage as storage
from ygl_test_subject.infrastructure.private_storage_migration import (
    execute_storage_migration,
)
from ygl_test_subject.infrastructure.windows import fs, maintenance
from ygl_test_subject.infrastructure.windows.managed_key_io import (
    _bindings,
    _SecurityAttributes,
)


@unittest.skipUnless(os.name == "nt", "actual Win32 primitives require Windows")
class WindowsCiBoundariesTests(unittest.TestCase):
    def test_actual_runtime_qualifies_or_refuses_without_opening_storage(self):
        actual = ".".join(map(str, fs._native_windows_version()))
        if actual not in fs.SUPPORTED_WINDOWS_BUILDS:
            with self.assertRaises(fs.WindowsScanError) as raised:
                fs._check_runtime()
            self.assertEqual(raised.exception.code, "unsupported_environment")
        else:
            fs._check_runtime()

    def test_native_private_and_outsider_acl_descriptors(self):
        native = maintenance._SecurityNative()
        _bindings(native)
        operator = native.operator()
        sid_buffer = ctypes.create_string_buffer(operator)
        sid_text = ctypes.c_void_p()
        self.assertTrue(
            native.security.ConvertSidToStringSidW(sid_buffer, ctypes.byref(sid_text))
        )
        try:
            sid = ctypes.wstring_at(sid_text.value)
        finally:
            native.LocalFree(sid_text)
        system = native._fixed_sid("S-1-5-18")
        admins = native._fixed_sid("S-1-5-32-544")
        installer = native._fixed_sid(
            "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
        )
        with tempfile.TemporaryDirectory(prefix="ygl-ci-native-acl-") as temporary:
            for name, extra in (
                ("private", ""),
                ("outside-read", "(A;OICI;GR;;;WD)"),
                ("outside-control", "(A;OICI;FA;;;WD)"),
            ):
                with self.subTest(name=name):
                    descriptor = ctypes.c_void_p()
                    path = Path(temporary) / name
                    sddl = f"O:{sid}D:P(A;OICI;FA;;;{sid})(A;OICI;FA;;;SY)(A;OICI;FA;;;BA){extra}"
                    self.assertTrue(
                        native.security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                            sddl, 1, ctypes.byref(descriptor), None
                        )
                    )
                    try:
                        attributes = _SecurityAttributes(
                            ctypes.sizeof(_SecurityAttributes), descriptor, False
                        )
                        self.assertTrue(
                            native.dll.CreateDirectoryW(
                                str(path), ctypes.byref(attributes)
                            )
                        )
                    finally:
                        native.LocalFree(descriptor)
                    value = native.CreateFileW(
                        str(path),
                        maintenance.READ_CONTROL,
                        fs.FILE_SHARE_READ,
                        None,
                        fs.OPEN_EXISTING,
                        fs.FILE_FLAG_BACKUP_SEMANTICS | fs.FILE_FLAG_OPEN_REPARSE_POINT,
                        None,
                    )
                    self.assertNotIn(value, (None, fs.INVALID_HANDLE_VALUE))
                    with fs._Handle(native, value) as handle:
                        owner, aces = native.descriptor(handle)
                    if extra:
                        with self.assertRaises(maintenance.WindowsMaintenanceError):
                            maintenance._validate_descriptor(
                                owner,
                                aces,
                                operator,
                                system,
                                admins,
                                installer,
                                role="data",
                                directory=True,
                            )
                    else:
                        maintenance._validate_descriptor(
                            owner,
                            aces,
                            operator,
                            system,
                            admins,
                            installer,
                            role="data",
                            directory=True,
                        )

    def test_selector_hardlink_and_oversize_are_retained_and_rejected(self):
        with tempfile.TemporaryDirectory(prefix="ygl-ci-selector-") as temporary:
            root = Path(temporary)
            selector = root / "active.json"
            sentinel = root / "retained"
            sentinel.write_bytes(b"synthetic retained source")
            os.link(sentinel, selector)
            with self.assertRaises(storage.PrivateStorageError) as raised:
                storage._read_selector(root, root)
            self.assertEqual(raised.exception.code, "ambiguous_storage")
            self.assertEqual(sentinel.read_bytes(), b"synthetic retained source")
            selector.unlink()
            selector.write_bytes(b"x" * 4097)
            with self.assertRaises(storage.PrivateStorageError) as raised:
                storage._read_selector(root, root)
            self.assertEqual(raised.exception.code, "ambiguous_storage")
            self.assertEqual(selector.stat().st_size, 4097)

    def test_public_migration_refuses_callback_without_touching_source(self):
        with tempfile.TemporaryDirectory(prefix="ygl-ci-migration-") as temporary:
            source = Path(temporary) / "runtime.sqlite3"
            source.write_bytes(b"retained synthetic database")
            with self.assertRaises(storage.PrivateStorageError) as raised:
                execute_storage_migration(
                    object(), source_proof=lambda: None, private_guard=object()
                )
            self.assertEqual(raised.exception.code, "source_exclusivity_unproven")
            self.assertEqual(source.read_bytes(), b"retained synthetic database")
