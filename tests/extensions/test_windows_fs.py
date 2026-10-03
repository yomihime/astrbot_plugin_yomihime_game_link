"""Windows NTFS handle and race acceptance for inert static discovery."""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import gc
import json
import os
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from api.manifests import EXTENSION_MANIFEST_ABI
from extensions import discovery, windows_fs
from extensions import windows_maintenance as maintenance
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
    _check_runtime,
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
        self.assertEqual(ctypes.sizeof(windows_fs._OsVersionInfoW), 276)
        self.assertEqual(windows_fs._OsVersionInfoW.szCSDVersion.offset, 20)

    def test_exact_python_matrix_and_gil_guards(self) -> None:
        platform_stub = SimpleNamespace(
            version=lambda: windows_fs.SUPPORTED_WINDOWS_BUILD,
            python_implementation=lambda: "CPython",
            machine=lambda: "AMD64",
        )
        cases = (
            ((3, 12, 10), None, True),
            ((3, 12, 14), None, True),
            ((3, 12, 13), None, False),
            ((3, 12, 15), None, False),
            ((3, 13, 9), True, True),
            ((3, 13, 9), False, False),
            ((3, 13, 9), None, False),
            ((3, 12, 9), True, False),
        )
        for version, gil_enabled, supported in cases:
            with self.subTest(version=version, gil_enabled=gil_enabled):
                system_stub = SimpleNamespace(version_info=version)
                if gil_enabled is not None:
                    system_stub._is_gil_enabled = lambda value=gil_enabled: value
                with (
                    patch.object(windows_fs, "os", SimpleNamespace(name="nt")),
                    patch.object(windows_fs, "platform", platform_stub),
                    patch.object(
                        windows_fs,
                        "_native_windows_version",
                        return_value=(10, 0, 26200),
                    ),
                    patch.object(windows_fs, "sys", system_stub),
                    patch.object(windows_fs, "_assert_x64_layout") as layout_check,
                ):
                    if supported:
                        _check_runtime()
                        layout_check.assert_called_once_with()
                    else:
                        with self.assertRaises(WindowsScanError) as raised:
                            _check_runtime()
                        self.assertEqual(
                            raised.exception.code, "unsupported_environment"
                        )
                        layout_check.assert_not_called()

    def test_exact_windows_build_matrix(self) -> None:
        for build, supported in (
            ("10.0.26200", True),
            ("10.0.26300", True),
            ("10.0.26100", False),
            ("10.0.26301", False),
        ):
            with (
                self.subTest(build=build),
                patch.object(windows_fs, "os", SimpleNamespace(name="nt")),
                patch.object(
                    windows_fs,
                    "platform",
                    SimpleNamespace(
                        version=lambda: "10.0.26100",
                        python_implementation=lambda: "CPython",
                        machine=lambda: "AMD64",
                    ),
                ),
                patch.object(
                    windows_fs, "sys", SimpleNamespace(version_info=(3, 12, 10))
                ),
                patch.object(
                    windows_fs,
                    "_native_windows_version",
                    return_value=tuple(int(part) for part in build.split(".")),
                ),
                patch.object(windows_fs, "_assert_x64_layout") as layout_check,
            ):
                if supported:
                    _check_runtime()
                    layout_check.assert_called_once_with()
                else:
                    with self.assertRaises(WindowsScanError) as raised:
                        _check_runtime()
                    self.assertEqual(raised.exception.code, "unsupported_environment")
                    layout_check.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows native version ABI")
    def test_native_version_api_rejects_status_layout_platform_and_call_failures(self):
        class FakeVersionApi:
            def __init__(self, status, size=276, platform_id=2):
                self.status, self.size, self.platform_id = status, size, platform_id

            def __call__(self, pointer):
                info = pointer._obj
                self_input_size = info.dwOSVersionInfoSize
                if self_input_size != 276:
                    raise AssertionError("incorrect caller size")
                info.dwOSVersionInfoSize = self.size
                info.dwMajorVersion, info.dwMinorVersion, info.dwBuildNumber = (
                    10,
                    0,
                    26300,
                )
                info.dwPlatformId = self.platform_id
                return self.status

        for status, size, platform_id, supported in (
            (0, 276, 2, True),
            (-1, 276, 2, False),
            (1, 276, 2, False),
            (False, 276, 2, False),
            (True, 276, 2, False),
            (0, 284, 2, False),
            (0, 276, 0, False),
        ):
            with self.subTest(status=status, size=size, platform_id=platform_id):
                api = FakeVersionApi(status, size, platform_id)
                with patch.object(
                    windows_fs.ctypes,
                    "WinDLL",
                    return_value=SimpleNamespace(RtlGetVersion=api),
                ):
                    if supported:
                        self.assertEqual(
                            windows_fs._native_windows_version(), (10, 0, 26300)
                        )
                        self.assertEqual(
                            api.argtypes, [ctypes.POINTER(windows_fs._OsVersionInfoW)]
                        )
                        self.assertIs(api.restype, wintypes.LONG)
                    else:
                        with self.assertRaises(WindowsScanError) as raised:
                            windows_fs._native_windows_version()
                        self.assertEqual(
                            raised.exception.code, "unsupported_environment"
                        )

        def failing_api(pointer):
            raise OSError("synthetic native failure")

        for dll in (SimpleNamespace(), SimpleNamespace(RtlGetVersion=failing_api)):
            with (
                patch.object(windows_fs.ctypes, "WinDLL", return_value=dll),
                self.assertRaises(WindowsScanError),
            ):
                windows_fs._native_windows_version()
        with (
            patch.object(
                windows_fs.ctypes,
                "WinDLL",
                side_effect=OSError("synthetic missing DLL"),
            ),
            self.assertRaises(WindowsScanError),
        ):
            windows_fs._native_windows_version()
        with (
            patch.object(windows_fs.ctypes, "sizeof", return_value=4),
            self.assertRaises(WindowsScanError),
        ):
            windows_fs._native_windows_version()

    @unittest.skipUnless(os.name == "nt", "requires actual running NT version")
    def test_actual_native_version_qualifies_despite_platform_report_mismatch(self):
        native_version = windows_fs._native_windows_version()
        if (
            ".".join(map(str, native_version))
            not in windows_fs.SUPPORTED_WINDOWS_BUILDS
            or sys.version_info[:3] not in windows_fs.SUPPORTED_PYTHON_VERSIONS
        ):
            self.skipTest("actual runtime is outside the existing exact matrix")
        with patch.object(
            windows_fs.platform, "version", return_value="10.0.26100"
        ) as compatibility_report:
            windows_fs._check_runtime()
        compatibility_report.assert_not_called()

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
            "extensions.windows_fs._native_windows_version", return_value=(10, 0, 0)
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


class WindowsMaintenanceAclTests(unittest.TestCase):
    def test_acl_authority_masks_owner_and_inheritance_are_conservative(self):
        op, system, admin, installer, outsider = (
            b"operator",
            b"system",
            b"admin",
            b"installer",
            b"outsider",
        )
        cases = (
            (op, "code", True, 0, 0x40000000, False),
            (op, "code", True, 0, 0x80000000, True),
            (op, "data", True, 0, 0x80000000, False),
            (op, "ancestor", True, 0, 0x4, True),
            (op, "ancestor", True, 0, 0x2, False),
            (op, "ancestor", True, 0, 0x40, False),
            (op, "ancestor", True, 0, 0x40000, False),
            (op, "ancestor", True, 0, 0x80000, False),
            (op, "ancestor", True, 0x08, 0x10000000, True),
            (op, "code", True, 0x0B, 0x10000000, False),
            (op, "code", False, 0x08, 0x10000000, True),
            (outsider, "code", True, 0, 0, False),
            (installer, "ancestor", True, 0, 0, True),
            (installer, "code", True, 0, 0, False),
        )
        for owner, role, directory, flags, mask, allowed in cases:
            with self.subTest(role=role, flags=flags, mask=mask, allowed=allowed):

                def validate():
                    return maintenance._validate_descriptor(
                        owner,
                        (maintenance._Ace(0, flags, mask, outsider),),
                        op,
                        system,
                        admin,
                        installer,
                        role=role,
                        directory=directory,
                    )

                if allowed:
                    validate()
                else:
                    with self.assertRaises(maintenance.WindowsMaintenanceError):
                        validate()
        for kind, flags, mask in ((9, 0, 0), (0, 0x80, 0), (0, 0, 0x02000000)):
            with self.assertRaises(maintenance.WindowsMaintenanceError):
                maintenance._validate_descriptor(
                    op,
                    (maintenance._Ace(kind, flags, mask, op),),
                    op,
                    system,
                    admin,
                    installer,
                    role="code",
                    directory=True,
                )


@unittest.skipUnless(os.name == "nt", "native Windows OS proof requires Windows")
class WindowsMaintenanceNativeTests(unittest.TestCase):
    """Real OS fixtures; no shared ACL, drive mapping, credential, or host edits."""

    def setUp(self):
        windows_fs._check_runtime()
        self.native = maintenance._SecurityNative()
        operator = self.native.operator()
        self.operator_sid = (
            "S-"
            + str(operator[0])
            + "-"
            + str(int.from_bytes(operator[2:8], "big"))
            + "".join(
                "-" + str(value)
                for value in struct.unpack("<" + "I" * operator[1], operator[8:])
            )
        )
        # TEMP can refer to a different user under the managed tool token.
        # Derive this fixture root from the real primary token, never its name.
        userenv = ctypes.WinDLL("userenv", use_last_error=True)
        profile = userenv.GetUserProfileDirectoryW
        profile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        profile.restype = wintypes.BOOL
        token = wintypes.HANDLE()
        self.assertTrue(
            self.native.OpenProcessToken(
                self.native.GetCurrentProcess(),
                maintenance.TOKEN_QUERY,
                ctypes.byref(token),
            )
        )
        with windows_fs._Handle(self.native, int(token.value)) as handle:
            length = wintypes.DWORD()
            profile(wintypes.HANDLE(handle.value), None, ctypes.byref(length))
            self.assertTrue(1 < length.value < 32768)
            buffer = ctypes.create_unicode_buffer(length.value)
            self.assertTrue(
                profile(wintypes.HANDLE(handle.value), buffer, ctypes.byref(length))
            )
        private_temp = Path(buffer.value) / "AppData" / "Local" / "Temp"
        self.assertTrue(private_temp.is_dir())
        self.temporary = tempfile.TemporaryDirectory(
            prefix="ygl-maintenance-native-", dir=private_temp
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self._acl(self.root)
        self.code = self.root / "plugin"
        self.data = self.root / "data"
        self.code.mkdir()
        self.data.mkdir()
        self.code_file = self.code / "inert.py"
        self.code_file.write_bytes(b"# inert fixture, never imported\n")
        self.mutable = self.data / "old-ciphertext"
        self.mutable.write_bytes(b"synthetic ciphertext fixture")
        self.database = self.data / "runtime.sqlite3"
        with contextlib.closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TABLE guard_fixture(value TEXT)")
            connection.commit()

    def _acl(self, target, extra="", *, null=False):
        self.assertTrue(Path(target).resolve().is_relative_to(self.root))
        convert = (
            self.native.security.ConvertStringSecurityDescriptorToSecurityDescriptorW
        )
        convert.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.DWORD),
        ]
        convert.restype = wintypes.BOOL
        apply = self.native.security.SetFileSecurityW
        apply.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
        apply.restype = wintypes.BOOL
        descriptor = ctypes.c_void_p()
        sddl = (
            "D:NO_ACCESS_CONTROL"
            if null
            else "D:P(A;OICI;FA;;;"
            + self.operator_sid
            + ")(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
            + extra
        )
        try:
            self.assertTrue(convert(sddl, 1, ctypes.byref(descriptor), None))
            self.assertTrue(apply(str(target), 0x80000004, descriptor))
        finally:
            self.native._free(descriptor)

    def _guard(self):
        return maintenance.WindowsMaintenanceGuard(self.code, self.database)

    def _reject(self, guard=None):
        guard = guard or self._guard()
        with self.assertRaises(windows_fs.WindowsScanError):
            with guard:
                self.fail("unsafe fixture acquired OS authority")
        self.assertEqual(guard._handles, [])

    def test_pins_code_and_database_but_permits_sqlite_and_secret_cleanup(self):
        guard = self._guard()
        with guard:
            self.assertTrue(guard._handles)
            with self.assertRaises(OSError):
                self.code_file.write_bytes(b"must not overwrite pinned code")
            for pinned in (self.root, self.code, self.data, self.database):
                with self.subTest(pinned=pinned.name), self.assertRaises(OSError):
                    pinned.rename(pinned.with_name(pinned.name + "-replaced"))
            with contextlib.closing(sqlite3.connect(self.database)) as connection:
                self.assertEqual(
                    connection.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal"
                )
                connection.execute(
                    "INSERT INTO guard_fixture VALUES(?)", ("synthetic",)
                )
                connection.commit()
                self.assertTrue(Path(str(self.database) + "-wal").is_file())
                self.assertTrue(Path(str(self.database) + "-shm").is_file())
            replacement = self.data / "new-ciphertext"
            replacement.write_bytes(b"synthetic replacement")
            os.replace(replacement, self.mutable)
            self.mutable.unlink()
        self.assertEqual(guard._handles, [])
        self.code_file.write_bytes(b"released")
        self.database.rename(self.data / "released.sqlite3")

    def test_acl_rejects_outsider_mutation_private_reads_and_null_dacl(self):
        for target, extra, null in (
            (self.code, "(A;;GW;;;WD)", False),
            (self.data, "(A;;GR;;;WD)", False),
            (self.root, "(A;;0x40;;;WD)", False),
            (self.root, "(A;;0x100;;;WD)", False),
            (self.code, "", True),
        ):
            with self.subTest(extra=extra, null=null):
                self._acl(target, extra, null=null)
                try:
                    self._reject()
                finally:
                    self._acl(target)

    def test_ancestor_sibling_directory_creation_and_code_reads_are_distinct(self):
        self._acl(self.root, "(A;;0x4;;;WD)")
        self._acl(self.code, "(A;;GR;;;WD)")
        with self._guard():
            pass

    def test_hard_links_in_code_and_mutable_data_are_rejected(self):
        alias = self.root / "alias"
        for source in (self.code_file, self.mutable):
            with self.subTest(source=source.name):
                os.link(source, alias)
                try:
                    self._reject()
                finally:
                    alias.unlink()

    def test_preexisting_writers_block_code_and_short_mutable_file_pins(self):
        for target in (self.code_file, self.mutable):
            with self.subTest(target=target.name):
                value = windows_fs._handle_value(
                    self.native.CreateFileW(
                        str(target), 0x40000000, 7, None, 3, 0x80, None
                    )
                )
                self.assertIsNotNone(value)
                with windows_fs._Handle(self.native, value):
                    self._reject()

    def test_real_thread_impersonation_is_rejected_then_reverted(self):
        impersonate = self.native.security.ImpersonateSelf
        impersonate.argtypes = [ctypes.c_int]
        impersonate.restype = wintypes.BOOL
        revert = self.native.security.RevertToSelf
        revert.argtypes = []
        revert.restype = wintypes.BOOL
        self.assertTrue(impersonate(2))
        try:
            self._reject()
        finally:
            self.assertTrue(revert())
        with self._guard():
            pass

    def test_junction_is_rejected_without_following_its_target(self):
        target = self.root / "inert-target"
        target.mkdir()
        link = self.code / "junction"
        unavailable = _make_junction(link, target)
        if unavailable:
            self.skipTest(unavailable)
        try:
            self._reject()
        finally:
            link.rmdir()
        self.assertTrue(target.is_dir())

    def test_path_to_id_handoff_keeps_the_original_directory_pinned(self):
        value = windows_fs._handle_value(
            self.native.CreateFileW(
                str(self.code), maintenance._PIN_ACCESS, 1, None, 3, 0x02200000, None
            )
        )
        self.assertIsNotNone(value)
        with windows_fs._Handle(self.native, value) as handle:
            code_id = windows_fs._file_id(windows_fs._get_info(self.native, handle))
        original = windows_fs._open_file_by_id
        attempts = []

        def open_id(native, volume, identity, **kwargs):
            if identity == code_id and not attempts:
                attempts.append(True)
                with self.assertRaises(OSError):
                    self.code.rename(self.root / "must-not-replace")
            return original(native, volume, identity, **kwargs)

        with patch.object(windows_fs, "_open_file_by_id", open_id), self._guard():
            pass
        self.assertEqual(attempts, [True])
        self.assertTrue(self.code.is_dir())

    def test_unreliable_volume_proof_and_budget_faults_fail_closed(self):
        original = windows_fs._volume_name
        calls = []

        def mapped(native, drive):
            calls.append(True)
            value = original(native, drive)
            return value if len(calls) <= 3 else "untrusted-mapping-fault"

        # Explicit fault injection, never an edit to a real drive or mount.
        with patch.object(windows_fs, "_volume_name", mapped):
            self._reject()
        with patch.object(maintenance, "MAX_TREE_ENTRIES", 0):
            self._reject()

    def test_cancel_and_repeated_operations_release_all_native_handles(self):
        count = self.native.dll.GetProcessHandleCount
        count.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        count.restype = wintypes.BOOL

        def handles():
            value = wintypes.DWORD()
            self.assertTrue(count(self.native.GetCurrentProcess(), ctypes.byref(value)))
            return value.value

        baseline = handles()
        for _ in range(5):
            guard = self._guard()
            with self.assertRaises(asyncio.CancelledError):
                with guard:
                    raise asyncio.CancelledError()
            self.assertEqual(guard._handles, [])
        self.assertLessEqual(handles(), baseline + 2)


if __name__ == "__main__":
    unittest.main()
