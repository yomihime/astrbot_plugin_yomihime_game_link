"""Windows NTFS handle and race acceptance for inert static discovery."""

from __future__ import annotations

import asyncio
import ctypes
import gc
import json
import os
import subprocess
import tempfile
import threading
import unittest
from ctypes import wintypes
from pathlib import Path
from unittest.mock import patch

from api.manifests import EXTENSION_MANIFEST_ABI
from extensions import discovery, windows_fs
from extensions.discovery import (
    EXTENSION_ROOT_MAX_ENTRIES,
    EXTENSION_ROOT_MAX_PACKAGES,
    DiscoveryRootError,
    discover_packages,
)
from extensions.factory_resolver import FilesystemFactorySource
from extensions.loader import CandidateState, ExtensionCandidate
from extensions.source_snapshot import (
    SourceSnapshotError,
    SourceSnapshotLimits,
    capture_source_bundle,
)
from extensions.windows_fs import (
    WindowsScanError,
    _assert_x64_layout,
    _ByHandleFileInformation,
    _Handle,
    _parse_root,
    capture_python_sources,
    scan_windows_root,
)


def _document(package_id: str = "sample_pkg") -> dict[str, object]:
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


def _write_package(
    root: Path, name: str = "pkg", package_id: str = "sample_pkg"
) -> Path:
    package = root / name
    package.mkdir()
    (package / EXTENSION_MANIFEST_ABI.filename).write_text(
        json.dumps(_document(package_id)), encoding="utf-8"
    )
    return package


def _temporary_directory() -> tempfile.TemporaryDirectory[str]:
    # Keep native fixtures on the repository's already-probed local NTFS volume.
    return tempfile.TemporaryDirectory(dir=Path.cwd())


def _is_qualified_temp_volume() -> bool:
    try:
        with _temporary_directory() as directory:
            scan_windows_root(
                directory,
                max_bytes=EXTENSION_MANIFEST_ABI.max_bytes,
                max_entries=EXTENSION_ROOT_MAX_ENTRIES,
                max_packages=EXTENSION_ROOT_MAX_PACKAGES,
            )
        return True
    except WindowsScanError:
        return False


WINDOWS_NTFS_QUALIFIED = _is_qualified_temp_volume()


def _make_junction(link: Path, target: Path) -> str | None:
    """Create a real directory junction; return an explicit unavailable reason."""
    script = (
        "$ErrorActionPreference='Stop'; "
        "New-Item -ItemType Junction -Path $env:E_JUNCTION_PATH "
        "-Target $env:E_JUNCTION_TARGET | Out-Null"
    )
    env = dict(os.environ)
    env["E_JUNCTION_PATH"] = str(link)
    env["E_JUNCTION_TARGET"] = str(target)
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"junction creator unavailable: {type(exc).__name__}"
    if result.returncode != 0 or not link.exists():
        return "Windows did not create a directory junction in the fixture location"
    return None


class WindowsAbiTests(unittest.TestCase):
    def test_documented_x64_struct_layout(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows ABI test requires Windows")
        _assert_x64_layout()
        self.assertEqual(windows_fs.ctypes.sizeof(windows_fs._FileIdDescriptor), 24)
        self.assertEqual(windows_fs._FileIdDescriptor.Id.offset, 8)
        self.assertEqual(
            windows_fs.ctypes.sizeof(windows_fs._ByHandleFileInformation), 52
        )
        self.assertEqual(
            windows_fs.ctypes.sizeof(windows_fs._FileIdBothDirectoryInfo), 112
        )
        self.assertEqual(windows_fs._FileIdBothDirectoryInfo.FileId.offset, 96)
        self.assertEqual(windows_fs._FileIdBothDirectoryInfo.FileName.offset, 104)

    def test_single_owner_closes_exactly_once(self) -> None:
        class FakeNative:
            calls = 0

            def CloseHandle(self, handle: object) -> bool:
                self.calls += 1
                return True

        native = FakeNative()
        handle = _Handle(native, 5)  # type: ignore[arg-type]
        handle.close()
        handle.close()
        self.assertEqual(native.calls, 1)

    def test_unsupported_matrix_fails_before_root_access(self) -> None:
        with patch(
            "extensions.windows_fs.platform.version", return_value="unsupported"
        ):
            with self.assertRaises(WindowsScanError) as raised:
                scan_windows_root(
                    "Z:\\missing\\root",
                    max_bytes=EXTENSION_MANIFEST_ABI.max_bytes,
                    max_entries=EXTENSION_ROOT_MAX_ENTRIES,
                    max_packages=EXTENSION_ROOT_MAX_PACKAGES,
                )
        self.assertEqual(raised.exception.code, "unsupported_environment")

    def test_root_grammar_rejects_path_aliases(self) -> None:
        for value in (
            "relative\\root",
            "C:relative",
            "\\\\server\\share\\root",
            "\\\\?\\C:\\root",
            "C:/root",
            "C:\\root\\..\\other",
            "C:\\root\\name:stream",
            "C:\\root\\bad.",
            "C:\\root\\CON.txt",
        ):
            with self.subTest(value=value), self.assertRaises(WindowsScanError):
                _parse_root(value)
        self.assertEqual(_parse_root("C:\\root\\").components, ("root",))

    def test_handle_file_id_mismatch_is_rejected(self) -> None:
        info = _ByHandleFileInformation()
        info.dwVolumeSerialNumber = 123
        info.nFileIndexLow = 10
        info.dwFileAttributes = windows_fs.FILE_ATTRIBUTE_DIRECTORY
        native = object()
        handle = _Handle(native, 1)  # type: ignore[arg-type]
        with patch("extensions.windows_fs._get_info", return_value=info):
            with self.assertRaises(WindowsScanError) as raised:
                windows_fs._verify_opened(
                    native,
                    handle,
                    11,
                    123,
                    directory=True,  # type: ignore[arg-type]
                )
        self.assertEqual(raised.exception.code, "identity_changed")

    def test_capture_inventory_rejects_reparse_entries(self) -> None:
        native = object()
        directory = _Handle(native, 1)  # type: ignore[arg-type]
        entry = windows_fs._DirectoryEntry(
            "linked.py", 77, windows_fs.FILE_ATTRIBUTE_REPARSE_POINT
        )
        with patch("extensions.windows_fs._enumerate", return_value=[entry]):
            with self.assertRaises(WindowsScanError) as raised:
                windows_fs._windows_inventory(
                    native,
                    directory,
                    max_component=255,
                    max_entries=8,  # type: ignore[arg-type]
                )
        self.assertEqual(raised.exception.code, "reparse_rejected")


@unittest.skipUnless(
    WINDOWS_NTFS_QUALIFIED, "local runtime/root is outside the qualified NTFS matrix"
)
class WindowsNtfScannerTests(unittest.TestCase):
    def test_valid_and_empty_roots_are_inert(self) -> None:
        with _temporary_directory() as directory:
            root = Path(directory)
            self.assertEqual(discover_packages(root), ())
            package = _write_package(root)
            factory = package / "fixture_module.py"
            marker = package / "factory-ran.marker"
            factory.write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
                encoding="utf-8",
            )
            before = set(__import__("sys").modules)
            found = discover_packages(root)
            self.assertEqual(len(found), 1)
            self.assertTrue(found[0].valid)
            self.assertEqual(found[0].package_id, "sample_pkg")
            self.assertFalse(found[0].enabled)
            self.assertFalse(marker.exists())
            self.assertEqual(set(__import__("sys").modules) - before, set())

    def test_authorized_capture_reuses_file_ids_and_freezes_source_bytes(self) -> None:
        with _temporary_directory() as directory:
            root = Path(directory)
            package = _write_package(root)
            marker = package / "factory-ran.marker"
            source = package / "module.py"
            source.write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
                encoding="utf-8",
            )
            package_candidate = discover_packages(root)[0]
            self.assertTrue(package_candidate.valid)
            before_modules = set(__import__("sys").modules)

            bundle = capture_source_bundle(
                package_candidate._provenance, SourceSnapshotLimits()
            )
            captured = bundle.files["module.py"]
            self.assertFalse(marker.exists())
            self.assertEqual(set(__import__("sys").modules) - before_modules, set())
            source.write_text("VALUE = 'new disk source'\n", encoding="utf-8")
            self.assertEqual(bundle.files["module.py"], captured)

    def test_capture_enforces_file_budget_before_module_execution(self) -> None:
        with _temporary_directory() as directory:
            root = Path(directory)
            package = _write_package(root)
            marker = package / "budget-ran.marker"
            (package / "module.py").write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
                encoding="utf-8",
            )
            candidate = discover_packages(root)[0]
            limits = SourceSnapshotLimits(max_file_bytes=8, max_package_bytes=16)
            with self.assertRaisesRegex(SourceSnapshotError, "source_budget_exceeded"):
                capture_source_bundle(candidate._provenance, limits)
            self.assertFalse(marker.exists())

    def test_capture_enforces_tree_budget(self) -> None:
        with _temporary_directory() as directory:
            root = Path(directory)
            package = _write_package(root)
            (package / "module.py").write_text("VALUE = 1", encoding="utf-8")
            candidate = discover_packages(root)[0]
            limits = SourceSnapshotLimits(max_tree_entries=1)
            with self.assertRaisesRegex(SourceSnapshotError, "source_budget_exceeded"):
                capture_source_bundle(candidate._provenance, limits)

    def test_capture_rejects_directory_mutation_during_read(self) -> None:
        with _temporary_directory() as directory:
            root = Path(directory)
            package = _write_package(root)
            (package / "module.py").write_text("VALUE = 1", encoding="utf-8")
            candidate = discover_packages(root)[0]
            provenance = candidate._provenance
            assert provenance is not None
            injected = False

            def hook(event: str, name: str, parent: object, opened: object) -> None:
                nonlocal injected
                if event == "source_pinned" and not injected:
                    injected = True
                    (package / "late.py").write_text("VALUE = 2", encoding="utf-8")

            with self.assertRaisesRegex(SourceSnapshotError, "candidate_stale"):
                capture_python_sources(
                    provenance.root_locator,
                    provenance.package_name,
                    provenance.package_id,
                    provenance.windows_identity,
                    provenance.manifest_sha256,
                    limits=SourceSnapshotLimits(),
                    event_hook=hook,
                )
            self.assertTrue(injected)

    def test_cancellation_drains_a_live_windows_source_reader(self) -> None:
        with _temporary_directory() as directory:
            root = Path(directory)
            package = _write_package(root)
            (package / "module.py").write_text("VALUE = 1", encoding="utf-8")
            candidate = ExtensionCandidate(
                discover_packages(root)[0], CandidateState.DISABLED
            )
            owner = FilesystemFactorySource(
                max_package_bytes=1024, max_runtime_bytes=1024
            )
            entered_reader = threading.Event()
            release_reader = threading.Event()
            real_capture = windows_fs.capture_python_sources

            def pause_with_file_open(
                *args: object, **kwargs: object
            ) -> dict[str, bytes]:
                def hook(event: str, name: str, parent: object, opened: object) -> None:
                    if event == "source_pinned":
                        entered_reader.set()
                        release_reader.wait(5)

                kwargs["event_hook"] = hook
                return real_capture(*args, **kwargs)  # type: ignore[arg-type]

            async def exercise() -> None:
                with patch.object(
                    windows_fs,
                    "capture_python_sources",
                    side_effect=pause_with_file_open,
                ):
                    capture = asyncio.create_task(owner.capture(candidate))
                    ready = await asyncio.to_thread(entered_reader.wait, 2)
                    if not ready and capture.done():
                        capture.result()
                    self.assertTrue(ready)
                    capture.cancel()
                    await asyncio.sleep(0.02)
                    self.assertFalse(capture.done())
                    self.assertEqual(owner.reserved_bytes, 1024)
                    release_reader.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await capture
                self.assertEqual(owner.reserved_bytes, 0)

            asyncio.run(exercise())

    def test_scan_to_capture_rejects_package_manifest_and_source_links(self) -> None:
        with _temporary_directory() as directory:
            root = Path(directory)
            package = _write_package(root)
            candidate = discover_packages(root)[0]
            package.rename(root / "moved")
            _write_package(root, "pkg", "replacement_pkg")
            with self.assertRaisesRegex(SourceSnapshotError, "candidate_stale"):
                capture_source_bundle(candidate._provenance, SourceSnapshotLimits())

        with _temporary_directory() as directory:
            root = Path(directory)
            package = _write_package(root)
            candidate = discover_packages(root)[0]
            (package / EXTENSION_MANIFEST_ABI.filename).write_text(
                json.dumps(_document("changed_pkg")), encoding="utf-8"
            )
            with self.assertRaisesRegex(SourceSnapshotError, "candidate_stale"):
                capture_source_bundle(candidate._provenance, SourceSnapshotLimits())

        with _temporary_directory() as directory:
            root = Path(directory)
            package = _write_package(root)
            outside = root / "outside.py"
            outside.write_text("raise AssertionError('outside')", encoding="utf-8")
            source = package / "module.py"
            source.write_text("VALUE = 1", encoding="utf-8")
            hardlink = package / "hardlink.py"
            try:
                os.link(source, hardlink)
            except OSError as exc:
                self.skipTest(
                    f"NTFS source hard link unavailable: {type(exc).__name__}"
                )
            candidate = discover_packages(root)[0]
            with self.assertRaisesRegex(
                SourceSnapshotError, "source_hardlink_rejected"
            ):
                capture_source_bundle(candidate._provenance, SourceSnapshotLimits())

    def test_capture_rejects_source_junction_without_reading_outside(self) -> None:
        with _temporary_directory() as directory:
            base = Path(directory)
            root = base / "root"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            package = _write_package(root)
            target = outside / "modules"
            target.mkdir()
            (target / "outside.py").write_text(
                "raise AssertionError('outside')", encoding="utf-8"
            )
            unavailable = _make_junction(package / "linked", target)
            if unavailable:
                self.skipTest(f"NOT RUN: {unavailable}")
            candidate = discover_packages(root)[0]
            with self.assertRaisesRegex(SourceSnapshotError, "source_link_rejected"):
                capture_source_bundle(candidate._provenance, SourceSnapshotLimits())

    def test_manifest_budget_and_link_count_are_fail_closed(self) -> None:
        with _temporary_directory() as directory:
            root = Path(directory)
            package = _write_package(root)
            manifest = package / EXTENSION_MANIFEST_ABI.filename
            manifest.write_bytes(b" " * (EXTENSION_MANIFEST_ABI.max_bytes + 1))
            result = discover_packages(root)
            self.assertEqual(result[0].diagnostic, "manifest byte budget exceeded")

        with _temporary_directory() as directory:
            root = Path(directory)
            package = _write_package(root)
            manifest = package / EXTENSION_MANIFEST_ABI.filename
            hardlink = package / "manifest-hardlink"
            try:
                os.link(manifest, hardlink)
            except OSError as exc:
                self.skipTest(
                    f"NTFS hard link fixture unavailable: {type(exc).__name__}"
                )
            result = discover_packages(root)
            self.assertEqual(result[0].diagnostic, "manifest is unavailable or linked")

    def test_root_and_package_budgets(self) -> None:
        with _temporary_directory() as directory:
            root = Path(directory)
            for index in range(EXTENSION_ROOT_MAX_ENTRIES + 1):
                (root / f"entry-{index:03d}").touch()
            with self.assertRaisesRegex(DiscoveryRootError, "budget"):
                discover_packages(root)

        with _temporary_directory() as directory:
            root = Path(directory)
            for index in range(EXTENSION_ROOT_MAX_PACKAGES + 1):
                (root / f"pkg-{index:03d}").mkdir()
            with self.assertRaisesRegex(DiscoveryRootError, "budget"):
                discover_packages(root)

    def test_repeated_scans_release_process_handles(self) -> None:
        kernel32 = windows_fs.ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = windows_fs.wintypes.HANDLE
        kernel32.GetProcessHandleCount.argtypes = [
            windows_fs.wintypes.HANDLE,
            windows_fs.ctypes.POINTER(windows_fs.wintypes.DWORD),
        ]
        kernel32.GetProcessHandleCount.restype = windows_fs.wintypes.BOOL
        process = kernel32.GetCurrentProcess()

        def count_handles() -> int:
            count = windows_fs.wintypes.DWORD()
            self.assertTrue(
                kernel32.GetProcessHandleCount(process, windows_fs.ctypes.byref(count))
            )
            return int(count.value)

        with _temporary_directory() as directory:
            before = count_handles()
            for _ in range(5):
                self.assertEqual(discover_packages(directory), ())
            gc.collect()
            after = count_handles()
        self.assertLessEqual(after, before + 1)

    def test_package_junction_is_rejected_without_reading_outside(self) -> None:
        with _temporary_directory() as directory:
            base = Path(directory)
            root = base / "root"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            _write_package(outside, "real", "outside_pkg")
            link = root / "junction_pkg"
            unavailable = _make_junction(link, outside / "real")
            if unavailable:
                self.skipTest(f"NOT RUN: {unavailable}")
            with patch.object(
                discovery,
                "parse_manifest",
                side_effect=AssertionError("outside bytes read"),
            ):
                result = discover_packages(root)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].diagnostic, "package_link_rejected")

    def test_root_ancestor_junction_is_rejected(self) -> None:
        with _temporary_directory() as directory:
            base = Path(directory)
            actual = base / "actual"
            actual.mkdir()
            _write_package(actual, "real", "outside_pkg")
            link = base / "junction"
            unavailable = _make_junction(link, actual)
            if unavailable:
                self.skipTest(f"NOT RUN: {unavailable}")
            with self.assertRaises(DiscoveryRootError):
                discover_packages(link)

    def test_enumerated_id_mismatch_never_reaches_manifest_read(self) -> None:
        with _temporary_directory() as directory:
            root = Path(directory)
            _write_package(root)
            original_open = windows_fs._open_file_by_id
            pending = {"package": False}

            def event_hook(
                event: str, name: str, parent: object, opened: object
            ) -> None:
                if event == "package_enumerated":
                    pending["package"] = True

            def wrong_id(
                native: object, volume_hint: object, file_id: int, *, directory: bool
            ):
                if pending["package"] and directory:
                    pending["package"] = False
                    file_id += 1
                return original_open(native, volume_hint, file_id, directory=directory)

            with patch.object(windows_fs, "_open_file_by_id", side_effect=wrong_id):
                with patch.object(
                    windows_fs,
                    "_read_manifest",
                    side_effect=AssertionError("manifest read after ID mismatch"),
                ):
                    records = scan_windows_root(
                        root,
                        max_bytes=EXTENSION_MANIFEST_ABI.max_bytes,
                        max_entries=EXTENSION_ROOT_MAX_ENTRIES,
                        max_packages=EXTENSION_ROOT_MAX_PACKAGES,
                        event_hook=event_hook,
                    )
            self.assertEqual(len(records), 1)
            self.assertIsNone(records[0].data)
            self.assertEqual(records[0].diagnostic, "package_unavailable")

    def test_pinned_root_revalidation_failure_aborts_entire_scan(self) -> None:
        with _temporary_directory() as directory:
            root = Path(directory)
            _write_package(root, "a", "first_pkg")
            _write_package(root, "b", "second_pkg")
            original_enumerate = windows_fs._enumerate
            original_emit = windows_fs._emit
            state = {"fail_next_enumeration": False}
            events: list[tuple[str, str]] = []

            def fail_root_revalidation(*args: object, **kwargs: object):
                if state["fail_next_enumeration"]:
                    state["fail_next_enumeration"] = False
                    raise WindowsScanError("io_error")
                return original_enumerate(*args, **kwargs)

            def record_event(event: str, name: str) -> None:
                events.append((event, name))
                if event == "package_enumerated":
                    state["fail_next_enumeration"] = True

            def mark_package_enumerated(
                event: str, name: str, parent: object, opened: object
            ) -> None:
                record_event(event, name)

            def patched_emit(
                hook: object,
                event: str,
                name: str,
                parent: object = None,
                opened: object = None,
            ) -> None:
                original_emit(hook, event, name, parent, opened)
                record_event(event, name)

            with patch.object(
                windows_fs, "_enumerate", side_effect=fail_root_revalidation
            ):
                with self.assertRaises(WindowsScanError) as raised:
                    scan_windows_root(
                        root,
                        max_bytes=EXTENSION_MANIFEST_ABI.max_bytes,
                        max_entries=EXTENSION_ROOT_MAX_ENTRIES,
                        max_packages=EXTENSION_ROOT_MAX_PACKAGES,
                        event_hook=lambda event,
                        name,
                        parent,
                        opened: mark_package_enumerated(event, name, parent, opened),
                    )
            self.assertEqual(raised.exception.code, "io_error")
            self.assertEqual(
                [name for event, name in events if event == "package_enumerated"],
                ["a"],
            )

            state["fail_next_enumeration"] = False
            events.clear()
            with (
                patch.object(
                    windows_fs, "_enumerate", side_effect=fail_root_revalidation
                ),
                patch.object(windows_fs, "_emit", side_effect=patched_emit),
            ):
                with self.assertRaises(DiscoveryRootError):
                    discover_packages(root)
            self.assertEqual(
                [name for event, name in events if event == "package_enumerated"],
                ["a"],
            )

    def test_preexisting_writer_blocks_manifest_pin(self) -> None:
        with _temporary_directory() as directory:
            root = Path(directory)
            package = _write_package(root)
            manifest = package / EXTENSION_MANIFEST_ABI.filename
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateFileW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.LPVOID,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.HANDLE,
            ]
            kernel32.CreateFileW.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            writer = kernel32.CreateFileW(
                str(manifest),
                0x40000000,  # GENERIC_WRITE
                0x00000007,  # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
                None,
                3,  # OPEN_EXISTING
                0x00000080,  # FILE_ATTRIBUTE_NORMAL
                None,
            )
            writer_value = (
                writer.value if isinstance(writer, ctypes.c_void_p) else writer
            )
            if not writer_value or writer_value == windows_fs.INVALID_HANDLE_VALUE:
                raise OSError(ctypes.get_last_error(), "CreateFileW writer")
            events: list[str] = []
            try:
                records = scan_windows_root(
                    root,
                    max_bytes=EXTENSION_MANIFEST_ABI.max_bytes,
                    max_entries=EXTENSION_ROOT_MAX_ENTRIES,
                    max_packages=EXTENSION_ROOT_MAX_PACKAGES,
                    event_hook=lambda event, *_: events.append(event),
                )
            finally:
                kernel32.CloseHandle(writer)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].diagnostic, "root_unavailable")
            self.assertIsNone(records[0].data)
            self.assertIn("manifest_enumerated", events)
            self.assertNotIn("manifest_pinned", events)

    def test_preexisting_writable_mapping_blocks_manifest_pin(self) -> None:
        with _temporary_directory() as directory:
            root = Path(directory)
            package = _write_package(root)
            manifest = package / EXTENSION_MANIFEST_ABI.filename
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateFileW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.LPVOID,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.HANDLE,
            ]
            kernel32.CreateFileW.restype = wintypes.HANDLE
            kernel32.CreateFileMappingW.argtypes = [
                wintypes.HANDLE,
                wintypes.LPVOID,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.LPCWSTR,
            ]
            kernel32.CreateFileMappingW.restype = wintypes.HANDLE
            kernel32.MapViewOfFile.argtypes = [
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.c_size_t,
            ]
            kernel32.MapViewOfFile.restype = ctypes.c_void_p
            kernel32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
            kernel32.UnmapViewOfFile.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL

            file_handle = kernel32.CreateFileW(
                str(manifest),
                0xC0000000,  # GENERIC_READ | GENERIC_WRITE
                0x00000007,  # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
                None,
                3,  # OPEN_EXISTING
                0x00000080,  # FILE_ATTRIBUTE_NORMAL
                None,
            )
            file_value = (
                file_handle.value
                if isinstance(file_handle, ctypes.c_void_p)
                else file_handle
            )
            if not file_value or file_value == windows_fs.INVALID_HANDLE_VALUE:
                raise OSError(ctypes.get_last_error(), "CreateFileW mapping source")
            mapping = None
            view = None
            try:
                mapping = kernel32.CreateFileMappingW(
                    file_handle,
                    None,
                    0x00000004,  # PAGE_READWRITE
                    0,
                    0,
                    None,
                )
                if not mapping:
                    raise OSError(ctypes.get_last_error(), "CreateFileMappingW")
                view = kernel32.MapViewOfFile(
                    mapping,
                    0x00000002,  # FILE_MAP_WRITE
                    0,
                    0,
                    0,
                )
                if not view:
                    raise OSError(ctypes.get_last_error(), "MapViewOfFile")
                self.assertTrue(ctypes.string_at(view, 1))
                kernel32.CloseHandle(file_handle)
                file_handle = None

                events: list[str] = []
                records = scan_windows_root(
                    root,
                    max_bytes=EXTENSION_MANIFEST_ABI.max_bytes,
                    max_entries=EXTENSION_ROOT_MAX_ENTRIES,
                    max_packages=EXTENSION_ROOT_MAX_PACKAGES,
                    event_hook=lambda event, *_: events.append(event),
                )
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0].diagnostic, "root_unavailable")
                self.assertIsNone(records[0].data)
                self.assertIn("manifest_enumerated", events)
                self.assertNotIn("manifest_pinned", events)
            finally:
                if view:
                    kernel32.UnmapViewOfFile(view)
                if mapping:
                    kernel32.CloseHandle(mapping)
                if file_handle:
                    kernel32.CloseHandle(file_handle)

    def test_package_rename_and_manifest_write_barriers(self) -> None:
        with _temporary_directory() as directory:
            root = Path(directory)
            package = _write_package(root)
            manifest = package / EXTENSION_MANIFEST_ABI.filename
            blocked: list[str] = []

            def hook(event: str, name: str, parent: object, opened: object) -> None:
                if event == "manifest_enumerated":
                    try:
                        manifest.rename(package / "replaced.json")
                    except OSError:
                        blocked.append("manifest_replace")
                    else:
                        self.fail(
                            "pinned package unexpectedly allowed manifest replacement"
                        )
                elif event == "package_pinned":
                    try:
                        package.rename(root / "renamed")
                    except OSError:
                        blocked.append("package_rename")
                    else:
                        self.fail("pinned package unexpectedly allowed rename")
                elif event == "manifest_pinned":
                    try:
                        with manifest.open("r+b") as writer:
                            writer.write(b"X")
                    except OSError:
                        blocked.append("manifest_write")
                    else:
                        self.fail("pinned manifest unexpectedly allowed write")

            records = scan_windows_root(
                root,
                max_bytes=EXTENSION_MANIFEST_ABI.max_bytes,
                max_entries=EXTENSION_ROOT_MAX_ENTRIES,
                max_packages=EXTENSION_ROOT_MAX_PACKAGES,
                event_hook=hook,
            )
            self.assertEqual(
                blocked, ["package_rename", "manifest_replace", "manifest_write"]
            )
            self.assertEqual(len(records), 1)
            self.assertIsNotNone(records[0].data)

    def test_enumeration_to_open_package_replacement_is_blocked(self) -> None:
        with _temporary_directory() as directory:
            base = Path(directory)
            root = base / "root"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            package = _write_package(root)
            _write_package(outside, "replacement", "outside_pkg")
            sentinel = outside / "replacement" / EXTENSION_MANIFEST_ABI.filename
            blocked: list[bool] = []

            def hook(event: str, name: str, parent: object, opened: object) -> None:
                if event == "package_enumerated":
                    try:
                        package.rename(root / "moved")
                        (root / name).mkdir()
                    except OSError:
                        blocked.append(True)
                    else:
                        self.fail(
                            "root enumeration lock unexpectedly allowed replacement"
                        )

            records = scan_windows_root(
                root,
                max_bytes=EXTENSION_MANIFEST_ABI.max_bytes,
                max_entries=EXTENSION_ROOT_MAX_ENTRIES,
                max_packages=EXTENSION_ROOT_MAX_PACKAGES,
                event_hook=hook,
            )
            self.assertEqual(blocked, [True])
            self.assertEqual(len(records), 1)
            self.assertIsNotNone(records[0].data)
            self.assertTrue(sentinel.exists())

    def test_manifest_symlink_is_rejected(self) -> None:
        with _temporary_directory() as directory:
            root = Path(directory)
            package = _write_package(root)
            outside = root.parent / (root.name + "-outside-manifest")
            try:
                outside.write_bytes(json.dumps(_document("outside_pkg")).encode())
                manifest = package / EXTENSION_MANIFEST_ABI.filename
                manifest.unlink()
                try:
                    manifest.symlink_to(outside)
                except OSError as exc:
                    self.skipTest(f"NOT RUN: symlink unavailable: {type(exc).__name__}")
                result = discover_packages(root)
                self.assertEqual(
                    result[0].diagnostic, "manifest is unavailable or linked"
                )
            finally:
                outside.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
