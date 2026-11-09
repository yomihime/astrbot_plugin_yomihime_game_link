"""Real Windows files cover the path-stat versus handle-stat timestamp split."""

import ctypes
import hashlib
import os
import tempfile
import unittest
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.bootstrap import artifact_cache
from ygl_test_subject.extensions import file_identity, runtime_bundle
from ygl_test_subject.extensions.runtime_bundle import RuntimeBundleError


@unittest.skipUnless(os.name == "nt", "Windows path/handle timestamp contract")
class WindowsCaptureIdentityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ygl-win-identity-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "payload.bin"
        self.payload = b"same-size-closed-payload"
        self.path.write_bytes(self.payload)
        # Set creation time explicitly: rapid file creation/utime may share a
        # Windows clock tick, so a mere immediate update is not a reliable split.
        import msvcrt

        native = ctypes.WinDLL("kernel32", use_last_error=True)
        native.SetFileTime.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        native.SetFileTime.restype = wintypes.BOOL
        ticks = (1_500_000_000 + 11_644_473_600) * 10_000_000
        creation = wintypes.FILETIME(ticks & 0xFFFFFFFF, ticks >> 32)
        with self.path.open("r+b") as writer:
            if not native.SetFileTime(
                wintypes.HANDLE(msvcrt.get_osfhandle(writer.fileno())),
                ctypes.byref(creation),
                None,
                None,
            ):
                raise ctypes.WinError(ctypes.get_last_error())
        self.fixed_time = 1_600_000_000_000_000_000
        os.utime(self.path, ns=(self.fixed_time, self.fixed_time))

        class Basic(ctypes.Structure):
            _fields_ = [
                (n, ctypes.c_longlong)
                for n in (
                    "CreationTime",
                    "LastAccessTime",
                    "LastWriteTime",
                    "ChangeTime",
                )
            ] + [("Attributes", wintypes.DWORD)]

        native.GetFileInformationByHandleEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        native.GetFileInformationByHandleEx.restype = wintypes.BOOL
        native.SetFileInformationByHandle.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        native.SetFileInformationByHandle.restype = wintypes.BOOL
        with self.path.open("r+b") as writer:
            handle = wintypes.HANDLE(msvcrt.get_osfhandle(writer.fileno()))
            basic = Basic()
            if not native.GetFileInformationByHandleEx(
                handle, 0, ctypes.byref(basic), ctypes.sizeof(basic)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            # A later write now produces a different real ChangeTime without
            # requiring arbitrary sleeps or relying on Windows clock resolution.
            basic.ChangeTime = (1_400_000_000 + 11_644_473_600) * 10_000_000
            if not native.SetFileInformationByHandle(
                handle, 0, ctypes.byref(basic), ctypes.sizeof(basic)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
        self.listed = {
            "payload.bin": {
                "size": len(self.payload),
                "sha256": hashlib.sha256(self.payload).hexdigest(),
            }
        }

    def test_real_stable_metadata_read_accepts_path_handle_ctime_split(self):
        before = self.path.lstat()
        with self.path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
        self.assertEqual(before.st_birthtime_ns, opened.st_birthtime_ns)
        self.assertNotEqual(before.st_ctime_ns, opened.st_ctime_ns)
        self.assertEqual(artifact_cache._read(self.path, 1024), self.payload)

    def test_real_stable_tree_capture_accepts_path_handle_ctime_split(self):
        self.assertEqual(
            runtime_bundle._capture_windows_tree(self.root, self.listed, 1024),
            {"payload.bin": self.payload},
        )

    def replacement(self):
        replacement = self.root / "replacement.bin"
        replacement.write_bytes(self.payload)
        os.utime(replacement, ns=(self.fixed_time, self.fixed_time))
        os.replace(replacement, self.path)

    def test_metadata_rejects_path_replacement_before_open(self):
        original_open = os.open
        changed = False

        def open_replaced(path, flags, *args, **kwargs):
            nonlocal changed
            if Path(path) == self.path and not changed:
                changed = True
                self.replacement()
            return original_open(path, flags, *args, **kwargs)

        with patch.object(artifact_cache.os, "open", side_effect=open_replaced):
            with self.assertRaisesRegex(RuntimeBundleError, "cache_changed"):
                artifact_cache._read(self.path, 1024)

    def test_metadata_rejects_same_handle_change_with_restored_mtime(self):
        original_fstat = os.fstat
        calls = 0

        def fstat_changed(fd):
            nonlocal calls
            calls += 1
            if calls == 2:
                with self.path.open("r+b") as writer:
                    writer.write(b"X" + self.payload[1:])
                    writer.flush()
                os.utime(self.path, ns=(self.fixed_time, self.fixed_time))
            return original_fstat(fd)

        with patch.object(artifact_cache.os, "fstat", side_effect=fstat_changed):
            with self.assertRaisesRegex(RuntimeBundleError, "cache_changed"):
                artifact_cache._read(self.path, 1024)

    def test_metadata_rejects_path_replacement_after_handle_read(self):
        original_plain = artifact_cache._plain
        checks = 0

        def plain_replaced(path, **kwargs):
            nonlocal checks
            checks += 1
            if checks == 2:
                self.replacement()
            return original_plain(path, **kwargs)

        with patch.object(artifact_cache, "_plain", side_effect=plain_replaced):
            with self.assertRaisesRegex(RuntimeBundleError, "cache_changed"):
                artifact_cache._read(self.path, 1024)

    def test_tree_rejects_same_handle_change_with_restored_mtime(self):
        original_read = os.read
        changed = False

        def read_changed(fd, count):
            nonlocal changed
            content = original_read(fd, count)
            if not changed:
                changed = True
                with self.path.open("r+b") as writer:
                    writer.write(b"X" + self.payload[1:])
                    writer.flush()
                os.utime(self.path, ns=(self.fixed_time, self.fixed_time))
            return content

        with patch.object(runtime_bundle.os, "read", side_effect=read_changed):
            with self.assertRaisesRegex(RuntimeBundleError, "bundle_changed"):
                runtime_bundle._capture_windows_tree(self.root, self.listed, 1024)

    def test_metadata_and_tree_reject_hardlinked_payload(self):
        os.link(self.path, self.root / "linked.bin")
        with self.assertRaisesRegex(RuntimeBundleError, "cache_path_rejected"):
            artifact_cache._read(self.path, 1024)
        # Keep the alias outside the declared root scan; the link count itself
        # must still reject the declared file rather than only an extra name.
        other = self.root / "subtree"
        other.mkdir()
        os.link(self.path, other / "payload.bin")
        with self.assertRaisesRegex(RuntimeBundleError, "bundle_file_invalid"):
            runtime_bundle._capture_windows_tree(other, self.listed, 1024)


class FileIdentityDomainTest(unittest.TestCase):
    @staticmethod
    def info(**changes):
        values = dict(
            st_dev=1,
            st_ino=2,
            st_mode=0o100600,
            st_nlink=1,
            st_size=5,
            st_mtime_ns=6,
            st_ctime_ns=7,
            st_birthtime_ns=8,
        )
        values.update(changes)
        return SimpleNamespace(**values)

    def test_windows_cross_api_identity_and_same_handle_change_time(self):
        with patch.object(file_identity.os, "name", "nt"):
            path = self.info(st_ctime_ns=8)
            opened = self.info(st_ctime_ns=9)
            self.assertEqual(
                file_identity.stable_identity(path),
                file_identity.stable_identity(opened),
            )
            self.assertNotEqual(
                file_identity.handle_identity(opened),
                file_identity.handle_identity(self.info(st_ctime_ns=10)),
            )

    def test_missing_windows_birthtime_fails_closed(self):
        info = self.info()
        del info.st_birthtime_ns
        with patch.object(file_identity.os, "name", "nt"):
            with self.assertRaises(file_identity.FileIdentityUnavailable):
                file_identity.stable_identity(info)

    def test_posix_cross_api_identity_keeps_change_time(self):
        with patch.object(file_identity.os, "name", "posix"):
            self.assertNotEqual(
                file_identity.stable_identity(self.info()),
                file_identity.stable_identity(self.info(st_ctime_ns=9)),
            )
