"""Fail-closed, handle-based Windows extension manifest reads.

This module intentionally supports only the reviewed x64 CPython/Windows/NTFS
matrix. It uses documented Win32 APIs and never opens a child by path.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import platform
import re
import struct
import sys
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Documented Win32 constants.
FILE_LIST_DIRECTORY = 0x0001
FILE_READ_DATA = 0x0001
FILE_READ_ATTRIBUTES = 0x0080
SYNCHRONIZE = 0x00100000
FILE_SHARE_READ = 0x00000001
OPEN_EXISTING = 3
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
FILE_ATTRIBUTE_DEVICE = 0x00000040
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
FILE_ID_BOTH_DIR_INFO_CLASS = 0x0000000A
FILE_ID_TYPE = 0
FILE_BASIC_INFO_CLASS = 0
ERROR_NO_MORE_FILES = 18
ERROR_MORE_DATA = 234
ERROR_ACCESS_DENIED = 5
ERROR_SHARING_VIOLATION = 32
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
FILE_ID_NAME_OFFSET = 104
FILE_ID_VALUE_OFFSET = 96
FILE_ID_FIXED_SIZE = 104
ENUM_BUFFER_SIZE = 64 * 1024
COMPONENT_LOOKUP_LIMIT = 4096
MAX_WINDOWS_PATH_UNITS = 32767

# The exact local qualification matrix, not a claim that other targets are
# unsafe in general. Any expansion needs its own matrix and review.
SUPPORTED_WINDOWS_BUILD = "10.0.26200"
SUPPORTED_WINDOWS_BUILDS = (SUPPORTED_WINDOWS_BUILD, "10.0.26300")
SUPPORTED_PYTHON = (3, 13, 9)
SUPPORTED_CLASSIC_GIL_PYTHON = (3, 12, 10)
SUPPORTED_CLASSIC_GIL_PYTHONS = (SUPPORTED_CLASSIC_GIL_PYTHON, (3, 12, 14))
SUPPORTED_PYTHON_VERSIONS = (SUPPORTED_PYTHON, *SUPPORTED_CLASSIC_GIL_PYTHONS)


class WindowsScanError(ValueError):
    """Stable, non-sensitive root-level Windows scan failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _RootValidationError(WindowsScanError):
    """A pinned-root validation failure that must abort the whole scan."""


@dataclass(frozen=True, slots=True)
class WindowsPackageBytes:
    """Raw inert manifest bytes, or a stable package-local diagnostic."""

    name: str
    data: bytes | None
    diagnostic: str | None = None
    identity: object | None = None


@dataclass(frozen=True, slots=True)
class _DirectoryEntry:
    name: str
    file_id: int
    attributes: int


@dataclass(frozen=True, slots=True)
class _PathSpec:
    drive_root: str
    components: tuple[str, ...]
    display_root: Path


class _FileId128(ctypes.Structure):
    _fields_ = [("Identifier", ctypes.c_ubyte * 16)]


class _Guid(ctypes.Structure):
    _fields_ = [("Data", ctypes.c_ubyte * 16)]


class _FileIdUnion(ctypes.Union):
    _fields_ = [
        ("FileId", ctypes.c_longlong),
        ("ObjectId", _Guid),
        ("ExtendedFileId", _FileId128),
    ]


class _FileIdDescriptor(ctypes.Structure):
    _anonymous_ = ("Id",)
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("Id", _FileIdUnion),
    ]


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", wintypes.FILETIME),
        ("ftLastAccessTime", wintypes.FILETIME),
        ("ftLastWriteTime", wintypes.FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]


class _FileBasicInfo(ctypes.Structure):
    _fields_ = [
        ("CreationTime", ctypes.c_longlong),
        ("LastAccessTime", ctypes.c_longlong),
        ("LastWriteTime", ctypes.c_longlong),
        ("ChangeTime", ctypes.c_longlong),
        ("FileAttributes", wintypes.DWORD),
    ]


class _FileIdBothDirectoryInfo(ctypes.Structure):
    _fields_ = [
        ("NextEntryOffset", wintypes.DWORD),
        ("FileIndex", wintypes.DWORD),
        ("CreationTime", ctypes.c_longlong),
        ("LastAccessTime", ctypes.c_longlong),
        ("LastWriteTime", ctypes.c_longlong),
        ("ChangeTime", ctypes.c_longlong),
        ("EndOfFile", ctypes.c_longlong),
        ("AllocationSize", ctypes.c_longlong),
        ("FileAttributes", wintypes.DWORD),
        ("FileNameLength", wintypes.DWORD),
        ("EaSize", wintypes.DWORD),
        ("ShortNameLength", ctypes.c_byte),
        ("ShortName", wintypes.WCHAR * 12),
        ("FileId", ctypes.c_longlong),
        ("FileName", wintypes.WCHAR * 1),
    ]


def _assert_x64_layout() -> None:
    """Reject an ABI/layout different from the reviewed x64 SDK layout."""
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        raise WindowsScanError("unsupported_environment")
    if ctypes.sizeof(_FileIdDescriptor) != 24:
        raise WindowsScanError("unsupported_environment")
    if _FileIdDescriptor.Id.offset != 8:
        raise WindowsScanError("unsupported_environment")
    if ctypes.sizeof(_ByHandleFileInformation) != 52:
        raise WindowsScanError("unsupported_environment")
    if ctypes.sizeof(_FileBasicInfo) != 40:
        raise WindowsScanError("unsupported_environment")
    if ctypes.sizeof(_FileIdBothDirectoryInfo) != 112:
        raise WindowsScanError("unsupported_environment")
    if _FileIdBothDirectoryInfo.FileId.offset != FILE_ID_VALUE_OFFSET:
        raise WindowsScanError("unsupported_environment")
    if _FileIdBothDirectoryInfo.FileName.offset != FILE_ID_NAME_OFFSET:
        raise WindowsScanError("unsupported_environment")


class _Native:
    """Explicit WinAPI prototypes; use_last_error preserves GetLastError."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise WindowsScanError("unsupported_environment")
        _assert_x64_layout()
        try:
            self.dll = ctypes.WinDLL("kernel32", use_last_error=True)
            self.GetVolumeNameForVolumeMountPointW = (
                self.dll.GetVolumeNameForVolumeMountPointW
            )
            self.CreateFileW = self.dll.CreateFileW
            self.GetVolumeInformationByHandleW = self.dll.GetVolumeInformationByHandleW
            self.GetFileInformationByHandleEx = self.dll.GetFileInformationByHandleEx
            self.OpenFileById = self.dll.OpenFileById
            self.GetFileInformationByHandle = self.dll.GetFileInformationByHandle
            self.ReadFile = self.dll.ReadFile
            self.CloseHandle = self.dll.CloseHandle
        except (AttributeError, OSError):
            raise WindowsScanError("unsupported_environment") from None

        self.GetVolumeNameForVolumeMountPointW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            wintypes.DWORD,
        ]
        self.GetVolumeNameForVolumeMountPointW.restype = wintypes.BOOL
        self.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self.CreateFileW.restype = wintypes.HANDLE
        self.GetVolumeInformationByHandleW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPWSTR,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPWSTR,
            wintypes.DWORD,
        ]
        self.GetVolumeInformationByHandleW.restype = wintypes.BOOL
        self.GetFileInformationByHandleEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.GetFileInformationByHandleEx.restype = wintypes.BOOL
        self.OpenFileById.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_FileIdDescriptor),
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.OpenFileById.restype = wintypes.HANDLE
        self.GetFileInformationByHandle.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_ByHandleFileInformation),
        ]
        self.GetFileInformationByHandle.restype = wintypes.BOOL
        self.ReadFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        self.ReadFile.restype = wintypes.BOOL
        self.CloseHandle.argtypes = [wintypes.HANDLE]
        self.CloseHandle.restype = wintypes.BOOL

    @staticmethod
    def last_error() -> int:
        return ctypes.get_last_error()


class _Handle:
    """Single-owner Win32 handle, closed exactly once."""

    __slots__ = ("native", "value", "closed")

    def __init__(self, native: _Native, value: int) -> None:
        self.native = native
        self.value = value
        self.closed = False

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if not self.native.CloseHandle(wintypes.HANDLE(self.value)):
            raise WindowsScanError("io_error")

    def __enter__(self) -> _Handle:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        try:
            self.close()
        except WindowsScanError:
            if exc_type is None:
                raise


def _handle_value(value: object) -> int | None:
    if value is None:
        return None
    raw = value.value if isinstance(value, ctypes.c_void_p) else value
    if raw is None or raw == INVALID_HANDLE_VALUE:
        return None
    return int(raw)


def _is_device_name(name: str) -> bool:
    stem = name.split(".", 1)[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL"}:
        return True
    return bool(re.fullmatch(r"(?:COM|LPT)(?:[1-9]|[¹²³])", stem))


def _validate_component(name: str, max_component: int) -> None:
    if not name or name in {".", ".."} or len(name) > max_component:
        raise WindowsScanError("unsupported_environment")
    if name[-1] in {".", " "} or _is_device_name(name):
        raise WindowsScanError("unsupported_environment")
    for char in name:
        point = ord(char)
        if point == 0 or point < 32 or 0xD800 <= point <= 0xDFFF:
            raise WindowsScanError("unsupported_environment")
        if char in '<>:"/\\|?*':
            raise WindowsScanError("unsupported_environment")


def _parse_root(root: str | os.PathLike[str]) -> _PathSpec:
    try:
        raw = os.fspath(root)
    except (TypeError, ValueError, OSError):
        raise WindowsScanError("root_unavailable") from None
    if (
        type(raw) is not str
        or len(raw.encode("utf-16-le", "surrogatepass")) // 2 > MAX_WINDOWS_PATH_UNITS
    ):
        raise WindowsScanError("unsupported_environment")
    match = re.fullmatch(r"([A-Za-z]):\\(.*)", raw, flags=re.DOTALL)
    if match is None or raw.startswith("\\\\") or "/" in raw:
        raise WindowsScanError("unsupported_environment")
    drive = match.group(1).upper() + ":\\"
    tail = match.group(2)
    if tail.endswith("\\"):
        tail = tail[:-1]
    components = tuple(tail.split("\\")) if tail else ()
    if any(not component for component in components):
        raise WindowsScanError("unsupported_environment")
    for component in components:
        _validate_component(component, 255)
    try:
        display_root = Path(raw)
    except (TypeError, ValueError, OSError):
        raise WindowsScanError("unsupported_environment") from None
    return _PathSpec(drive, components, display_root)


class _OsVersionInfoW(ctypes.Structure):
    _fields_ = [
        ("dwOSVersionInfoSize", wintypes.DWORD),
        ("dwMajorVersion", wintypes.DWORD),
        ("dwMinorVersion", wintypes.DWORD),
        ("dwBuildNumber", wintypes.DWORD),
        ("dwPlatformId", wintypes.DWORD),
        ("szCSDVersion", wintypes.WCHAR * 128),
    ]


def _native_windows_version() -> tuple[int, int, int]:
    """Read the running NT version without shell or DLL-version fallbacks."""
    if (
        os.name != "nt"
        or ctypes.sizeof(wintypes.DWORD) != 4
        or ctypes.sizeof(wintypes.LONG) != 4
        or ctypes.sizeof(wintypes.WCHAR) != 2
        or ctypes.sizeof(_OsVersionInfoW) != 276
        or _OsVersionInfoW.szCSDVersion.offset != 20
    ):
        raise WindowsScanError("unsupported_environment")
    try:
        get_version = ctypes.WinDLL("ntdll").RtlGetVersion
        get_version.argtypes = [ctypes.POINTER(_OsVersionInfoW)]
        get_version.restype = wintypes.LONG
        info = _OsVersionInfoW()
        info.dwOSVersionInfoSize = ctypes.sizeof(info)
        status = get_version(ctypes.byref(info))
    except Exception:
        raise WindowsScanError("unsupported_environment") from None
    if (
        type(status) is not int
        or status != 0
        or info.dwOSVersionInfoSize != ctypes.sizeof(info)
        or info.dwPlatformId != 2
    ):
        raise WindowsScanError("unsupported_environment")
    return int(info.dwMajorVersion), int(info.dwMinorVersion), int(info.dwBuildNumber)


def _check_runtime() -> None:
    if os.name != "nt":
        raise WindowsScanError("unsupported_environment")
    native_version = ".".join(str(part) for part in _native_windows_version())
    if native_version not in SUPPORTED_WINDOWS_BUILDS:
        raise WindowsScanError("unsupported_environment")
    python_version = sys.version_info[:3]
    if (
        platform.python_implementation() != "CPython"
        or python_version not in SUPPORTED_PYTHON_VERSIONS
    ):
        raise WindowsScanError("unsupported_environment")
    if platform.machine().upper() not in {"AMD64", "X86_64"}:
        raise WindowsScanError("unsupported_environment")
    # CPython 3.12 has no free-threaded build. For 3.13, require the runtime
    # probe because free-threaded builds are available in that release line.
    if python_version == SUPPORTED_PYTHON:
        gil_probe = getattr(sys, "_is_gil_enabled", None)
        if gil_probe is None or gil_probe() is not True:
            raise WindowsScanError("unsupported_environment")
    elif python_version not in SUPPORTED_CLASSIC_GIL_PYTHONS:
        raise WindowsScanError("unsupported_environment")
    _assert_x64_layout()


def _get_info(native: _Native, handle: _Handle) -> _ByHandleFileInformation:
    info = _ByHandleFileInformation()
    if not native.GetFileInformationByHandle(
        wintypes.HANDLE(handle.value), ctypes.byref(info)
    ):
        raise WindowsScanError("io_error")
    if not info.dwVolumeSerialNumber:
        raise WindowsScanError("unsupported_environment")
    return info


def _file_id(info: _ByHandleFileInformation) -> int:
    return (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow)


def _open_file_by_id(
    native: _Native,
    volume_hint: _Handle,
    file_id: int,
    *,
    directory: bool,
    access: int | None = None,
    share_mode: int = FILE_SHARE_READ,
) -> _Handle:
    if not file_id or not 0 <= file_id <= 0xFFFFFFFFFFFFFFFF:
        raise WindowsScanError("identity_changed")
    descriptor = _FileIdDescriptor()
    descriptor.dwSize = ctypes.sizeof(_FileIdDescriptor)
    descriptor.Type = FILE_ID_TYPE
    # Preserve the exact 64-bit bit pattern when LARGE_INTEGER is signed.
    signed_file_id = file_id if file_id < (1 << 63) else file_id - (1 << 64)
    descriptor.FileId = signed_file_id
    if access is None:
        access = (
            (FILE_LIST_DIRECTORY if directory else FILE_READ_DATA)
            | FILE_READ_ATTRIBUTES
            | SYNCHRONIZE
        )
    flags = FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= FILE_FLAG_BACKUP_SEMANTICS
    value = _handle_value(
        native.OpenFileById(
            wintypes.HANDLE(volume_hint.value),
            ctypes.byref(descriptor),
            access,
            share_mode,
            None,
            flags,
        )
    )
    if value is None:
        error = native.last_error()
        if error in {ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION}:
            raise WindowsScanError("root_unavailable")
        raise WindowsScanError("identity_changed")
    return _Handle(native, value)


def _verify_opened(
    native: _Native,
    handle: _Handle,
    expected_id: int,
    volume_serial: int,
    *,
    directory: bool,
) -> _ByHandleFileInformation:
    info = _get_info(native, handle)
    attrs = int(info.dwFileAttributes)
    if info.dwVolumeSerialNumber != volume_serial or _file_id(info) != expected_id:
        raise WindowsScanError("identity_changed")
    if attrs & FILE_ATTRIBUTE_REPARSE_POINT:
        raise WindowsScanError("reparse_rejected")
    if bool(attrs & FILE_ATTRIBUTE_DIRECTORY) != directory:
        raise WindowsScanError("identity_changed")
    return info


def _parse_directory_buffer(
    buffer: ctypes.Array[ctypes.c_char],
) -> list[_DirectoryEntry]:
    raw = memoryview(buffer).cast("B")
    entries: list[_DirectoryEntry] = []
    offset = 0
    while True:
        if offset < 0 or offset + FILE_ID_FIXED_SIZE > len(raw):
            raise WindowsScanError("io_error")
        next_offset, _file_index = struct.unpack_from("<II", raw, offset)
        attrs = struct.unpack_from("<I", raw, offset + 56)[0]
        name_length = struct.unpack_from("<I", raw, offset + 60)[0]
        if name_length % 2:
            raise WindowsScanError("io_error")
        id_value = struct.unpack_from("<Q", raw, offset + FILE_ID_VALUE_OFFSET)[0]
        name_start = offset + FILE_ID_NAME_OFFSET
        name_end = name_start + name_length
        record_end = offset + next_offset if next_offset else len(raw)
        if (
            name_end > len(raw)
            or name_end > record_end
            or (next_offset and (next_offset % 8 or record_end <= name_start))
        ):
            raise WindowsScanError("io_error")
        try:
            name = bytes(raw[name_start:name_end]).decode("utf-16-le", "strict")
        except UnicodeDecodeError:
            raise WindowsScanError("unsupported_environment") from None
        # Preserve the conventional cursor entries here. The enumerator filters
        # them after recognizing a valid (possibly all-dot) batch.
        if not id_value:
            raise WindowsScanError("identity_changed")
        entries.append(_DirectoryEntry(name, id_value, int(attrs)))
        if not next_offset:
            break
        offset = record_end
    return entries


def _enumerate(
    native: _Native,
    directory: _Handle,
    *,
    max_entries: int,
    max_component: int,
    target_name: str | None = None,
) -> list[_DirectoryEntry]:
    entries: list[_DirectoryEntry] = []
    while True:
        buffer = ctypes.create_string_buffer(ENUM_BUFFER_SIZE)
        ok = native.GetFileInformationByHandleEx(
            wintypes.HANDLE(directory.value),
            FILE_ID_BOTH_DIR_INFO_CLASS,
            ctypes.byref(buffer),
            ENUM_BUFFER_SIZE,
        )
        if not ok:
            error = native.last_error()
            if error == ERROR_NO_MORE_FILES:
                break
            # ERROR_MORE_DATA is never accepted: a partial record is not proof
            # that the directory cursor advanced safely.
            if error == ERROR_MORE_DATA:
                raise WindowsScanError("io_error")
            raise WindowsScanError("io_error")
        batch = _parse_directory_buffer(buffer)
        if not batch:
            raise WindowsScanError("io_error")
        for entry in batch:
            if entry.name in {".", ".."}:
                continue
            _validate_component(entry.name, max_component)
            entries.append(entry)
            if len(entries) > max_entries:
                raise WindowsScanError("budget_exceeded")
        if len(entries) > max_entries:
            raise WindowsScanError("budget_exceeded")
    return entries


def _volume_name(native: _Native, drive_root: str) -> str:
    buffer = ctypes.create_unicode_buffer(64)
    if not native.GetVolumeNameForVolumeMountPointW(drive_root, buffer, len(buffer)):
        raise WindowsScanError("root_unavailable")
    value = buffer.value
    if not re.fullmatch(r"\\\\\?\\Volume\{[0-9A-Fa-f-]{36}\}\\", value):
        raise WindowsScanError("unsupported_environment")
    return value


def _open_volume_root(
    native: _Native,
    volume_path: str,
    *,
    access: int = FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES | SYNCHRONIZE,
    share_mode: int = FILE_SHARE_READ,
) -> _Handle:
    flags = FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT
    value = _handle_value(
        native.CreateFileW(
            volume_path,
            access,
            share_mode,
            None,
            OPEN_EXISTING,
            flags,
            None,
        )
    )
    if value is None:
        raise WindowsScanError("root_unavailable")
    return _Handle(native, value)


def _volume_serial(native: _Native, handle: _Handle) -> tuple[int, int]:
    fs_name = ctypes.create_unicode_buffer(32)
    serial = wintypes.DWORD()
    maximum_component = wintypes.DWORD()
    flags = wintypes.DWORD()
    ok = native.GetVolumeInformationByHandleW(
        wintypes.HANDLE(handle.value),
        None,
        0,
        ctypes.byref(serial),
        ctypes.byref(maximum_component),
        ctypes.byref(flags),
        fs_name,
        len(fs_name),
    )
    if not ok or fs_name.value.upper() != "NTFS" or not serial.value:
        raise WindowsScanError("unsupported_environment")
    info = _get_info(native, handle)
    if info.dwVolumeSerialNumber != serial.value:
        raise WindowsScanError("identity_changed")
    if info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT:
        raise WindowsScanError("reparse_rejected")
    if not info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY:
        raise WindowsScanError("identity_changed")
    return int(serial.value), int(maximum_component.value)


def _reopen_directory(
    native: _Native,
    volume_hint: _Handle,
    identity: int,
    volume_serial: int,
) -> _Handle:
    handle = _open_file_by_id(native, volume_hint, identity, directory=True)
    try:
        _verify_opened(native, handle, identity, volume_serial, directory=True)
    except BaseException:
        handle.close()
        raise
    return handle


def _lookup_component(
    native: _Native,
    volume_hint: _Handle,
    parent_id: int,
    component: str,
    volume_serial: int,
    max_component: int,
) -> tuple[_Handle, int]:
    with _reopen_directory(
        native, volume_hint, parent_id, volume_serial
    ) as enum_parent:
        matches = [
            entry
            for entry in _enumerate(
                native,
                enum_parent,
                max_entries=COMPONENT_LOOKUP_LIMIT,
                max_component=max_component,
                target_name=component,
            )
            if entry.name == component
        ]
    if len(matches) != 1:
        raise WindowsScanError("root_unavailable")
    entry = matches[0]
    if entry.attributes & FILE_ATTRIBUTE_REPARSE_POINT:
        raise WindowsScanError("reparse_rejected")
    if not entry.attributes & FILE_ATTRIBUTE_DIRECTORY:
        raise WindowsScanError("root_unavailable")
    child = _open_file_by_id(native, volume_hint, entry.file_id, directory=True)
    try:
        _verify_opened(native, child, entry.file_id, volume_serial, directory=True)
        with _reopen_directory(
            native, volume_hint, parent_id, volume_serial
        ) as confirm_parent:
            confirmation = [
                item
                for item in _enumerate(
                    native,
                    confirm_parent,
                    max_entries=COMPONENT_LOOKUP_LIMIT,
                    max_component=max_component,
                    target_name=component,
                )
                if item.name == component
            ]
        if len(confirmation) != 1 or confirmation[0].file_id != entry.file_id:
            raise WindowsScanError("identity_changed")
    except BaseException:
        child.close()
        raise
    return child, entry.file_id


def _read_manifest(
    native: _Native,
    volume_hint: _Handle,
    package: _Handle,
    package_id: int,
    package_name: str,
    volume_serial: int,
    max_component: int,
    max_bytes: int,
    event_hook: Callable[..., None] | None,
    *,
    return_identity: bool = False,
) -> bytes | tuple[bytes, int, int, int]:
    manifest_name = "yomihime.manifest.json"
    with _reopen_directory(
        native, volume_hint, package_id, volume_serial
    ) as enum_package:
        matches = [
            entry
            for entry in _enumerate(
                native,
                enum_package,
                max_entries=COMPONENT_LOOKUP_LIMIT,
                max_component=max_component,
                target_name=manifest_name,
            )
            if entry.name == manifest_name
        ]
    if len(matches) != 1:
        raise WindowsScanError("manifest_unavailable")
    entry = matches[0]
    _emit(event_hook, "manifest_enumerated", package_name, package)
    if entry.attributes & FILE_ATTRIBUTE_REPARSE_POINT:
        raise WindowsScanError("manifest_unavailable")
    if entry.attributes & FILE_ATTRIBUTE_DIRECTORY:
        raise WindowsScanError("manifest_invalid")
    file_handle = _open_file_by_id(native, volume_hint, entry.file_id, directory=False)
    with file_handle:
        info = _verify_opened(
            native, file_handle, entry.file_id, volume_serial, directory=False
        )
        if info.nNumberOfLinks != 1:
            raise WindowsScanError("manifest_linked")
        size = (int(info.nFileSizeHigh) << 32) | int(info.nFileSizeLow)
        if size > max_bytes:
            raise WindowsScanError("budget_exceeded")
        with _reopen_directory(
            native, volume_hint, package_id, volume_serial
        ) as confirm_package:
            confirmation = [
                item
                for item in _enumerate(
                    native,
                    confirm_package,
                    max_entries=COMPONENT_LOOKUP_LIMIT,
                    max_component=max_component,
                    target_name=manifest_name,
                )
                if item.name == manifest_name
            ]
        if len(confirmation) != 1 or confirmation[0].file_id != entry.file_id:
            raise WindowsScanError("identity_changed")
        if event_hook is not None:
            event_hook("manifest_pinned", package_name, package, file_handle)
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        total = 0
        while remaining:
            request = min(65_536, remaining)
            storage = ctypes.create_string_buffer(request)
            count = wintypes.DWORD()
            if not native.ReadFile(
                wintypes.HANDLE(file_handle.value),
                ctypes.byref(storage),
                request,
                ctypes.byref(count),
                None,
            ):
                raise WindowsScanError("io_error")
            if count.value == 0:
                break
            chunk = storage.raw[: count.value]
            chunks.append(chunk)
            total += count.value
            remaining -= count.value
        if total > max_bytes:
            raise WindowsScanError("budget_exceeded")
        if total != size:
            raise WindowsScanError("identity_changed")
        if total < max_bytes + 1:
            # The loop ended on EOF or after exhausting the exact byte limit.
            # A separate one-byte read distinguishes EOF from an extra byte.
            storage = ctypes.create_string_buffer(1)
            count = wintypes.DWORD()
            if not native.ReadFile(
                wintypes.HANDLE(file_handle.value),
                ctypes.byref(storage),
                1,
                ctypes.byref(count),
                None,
            ):
                raise WindowsScanError("io_error")
            if count.value:
                raise WindowsScanError("budget_exceeded")
        data = b"".join(chunks)
        if return_identity:
            last_write = (int(info.ftLastWriteTime.dwHighDateTime) << 32) | int(
                info.ftLastWriteTime.dwLowDateTime
            )
            return data, entry.file_id, size, last_write
        return data


def _emit(
    hook: Callable[..., None] | None,
    event: str,
    object_name: str,
    parent: _Handle | None = None,
    opened: _Handle | None = None,
) -> None:
    if hook is not None:
        hook(event, object_name, parent, opened)


def scan_windows_root(
    root: str | os.PathLike[str],
    *,
    max_bytes: int,
    max_entries: int,
    max_packages: int,
    event_hook: Callable[..., None] | None = None,
) -> tuple[WindowsPackageBytes, ...]:
    """Scan one NTFS root without reopening any child by path.

    `event_hook` is a private test seam. Production callers always omit it.
    """
    _check_runtime()
    path_spec = _parse_root(root)
    native = _Native()
    first_guid = _volume_name(native, path_spec.drive_root)
    root_handles: list[_Handle] = []
    try:
        volume = _open_volume_root(native, first_guid)
        root_handles.append(volume)
        volume_serial, max_component = _volume_serial(native, volume)
        root_info = _get_info(native, volume)
        parent = volume
        parent_id = _file_id(root_info)
        if not parent_id:
            raise WindowsScanError("identity_changed")
        for component in path_spec.components:
            child, child_id = _lookup_component(
                native,
                volume,
                parent_id,
                component,
                volume_serial,
                max_component,
            )
            root_handles.append(child)
            parent = child
            parent_id = child_id
        extension_root = parent
        _emit(event_hook, "root_pinned", str(path_spec.display_root), extension_root)
        entries = _enumerate(
            native,
            extension_root,
            max_entries=max_entries,
            max_component=max_component,
        )
        package_entries = [
            entry for entry in entries if entry.attributes & FILE_ATTRIBUTE_DIRECTORY
        ]
        if len(package_entries) > max_packages:
            raise WindowsScanError("budget_exceeded")
        entries.sort(key=lambda item: item.name)
        results: list[WindowsPackageBytes] = []
        for entry in entries:
            if not entry.attributes & FILE_ATTRIBUTE_DIRECTORY:
                continue
            if entry.attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                results.append(
                    WindowsPackageBytes(entry.name, None, "package_link_rejected")
                )
                continue
            _emit(event_hook, "package_enumerated", entry.name, extension_root)
            package: _Handle | None = None
            try:
                package = _open_file_by_id(
                    native, volume, entry.file_id, directory=True
                )
                _verify_opened(
                    native, package, entry.file_id, volume_serial, directory=True
                )
                try:
                    with _reopen_directory(
                        native, volume, parent_id, volume_serial
                    ) as confirm_root:
                        confirmation_entries = _enumerate(
                            native,
                            confirm_root,
                            max_entries=max_entries,
                            max_component=max_component,
                        )
                except WindowsScanError as exc:
                    raise _RootValidationError(exc.code) from None
                names = [
                    item for item in confirmation_entries if item.name == entry.name
                ]
                if len(names) != 1 or names[0].file_id != entry.file_id:
                    raise _RootValidationError("identity_changed")
            except WindowsScanError as exc:
                if isinstance(exc, _RootValidationError):
                    if package is not None:
                        package.close()
                    raise
                code = (
                    "package_link_rejected"
                    if exc.code == "reparse_rejected"
                    else "package_unavailable"
                )
                results.append(WindowsPackageBytes(entry.name, None, code))
                if package is not None:
                    package.close()
                continue
            try:
                _emit(event_hook, "package_pinned", entry.name, extension_root, package)
                manifest_read = _read_manifest(
                    native,
                    volume,
                    package,
                    entry.file_id,
                    entry.name,
                    volume_serial,
                    max_component,
                    max_bytes,
                    event_hook,
                    return_identity=True,
                )
                if not isinstance(manifest_read, tuple):
                    raise WindowsScanError("io_error")
                data, manifest_file_id, manifest_size, manifest_last_write = (
                    manifest_read
                )
                from .source_snapshot import WindowsPackageIdentity

                identity = WindowsPackageIdentity(
                    volume_guid=first_guid,
                    volume_serial=volume_serial,
                    root_file_id=parent_id,
                    package_file_id=entry.file_id,
                    manifest_file_id=manifest_file_id,
                    manifest_size=manifest_size,
                    manifest_last_write=manifest_last_write,
                )
                results.append(WindowsPackageBytes(entry.name, data, identity=identity))
            except WindowsScanError as exc:
                results.append(WindowsPackageBytes(entry.name, None, exc.code))
            finally:
                package.close()
        if (
            _volume_name(native, path_spec.drive_root).casefold()
            != first_guid.casefold()
        ):
            raise WindowsScanError("identity_changed")
        return tuple(results)
    except WindowsScanError:
        raise
    except (OSError, TypeError, ValueError, RuntimeError, OverflowError):
        raise WindowsScanError("io_error") from None
    finally:
        close_failed = False
        for handle in reversed(root_handles):
            try:
                handle.close()
            except WindowsScanError:
                close_failed = True
        if close_failed and sys.exc_info()[0] is None:
            raise WindowsScanError("io_error")


def capture_python_sources(
    root: str | os.PathLike[str],
    package_name: str,
    package_id: str,
    expected_identity: object,
    expected_manifest_digest: bytes,
    *,
    limits: object,
    event_hook: Callable[..., None] | None = None,
) -> dict[str, bytes]:
    """Capture Python source after reopening the exact scanned NTFS package.

    Every object is opened by its enumerated FileID.  Names are used only as
    parent-child edge proofs, never as import paths or file-open paths.
    """
    from yomihime_sdk.api.manifests import EXTENSION_MANIFEST_ABI

    from .source_snapshot import (
        SourceSnapshotError,
        SourceSnapshotLimits,
        WindowsPackageIdentity,
    )

    _check_runtime()
    if type(expected_identity) is not WindowsPackageIdentity or not isinstance(
        limits, SourceSnapshotLimits
    ):
        raise SourceSnapshotError("candidate_stale")
    try:
        path_spec = _parse_root(root)
        native = _Native()
        volume_guid = _volume_name(native, path_spec.drive_root)
        if volume_guid.casefold() != str(expected_identity.volume_guid).casefold():
            raise SourceSnapshotError("candidate_stale")
        handles: list[_Handle] = []
        try:
            volume = _open_volume_root(native, volume_guid)
            handles.append(volume)
            volume_serial, max_component = _volume_serial(native, volume)
            if volume_serial != expected_identity.volume_serial:
                raise SourceSnapshotError("candidate_stale")

            parent_id = _file_id(_get_info(native, volume))
            if not parent_id:
                raise SourceSnapshotError("candidate_stale")
            for component in path_spec.components:
                child, child_id = _lookup_component(
                    native, volume, parent_id, component, volume_serial, max_component
                )
                handles.append(child)
                parent_id = child_id
            if parent_id != expected_identity.root_file_id:
                raise SourceSnapshotError("candidate_stale")
            extension_root = handles[-1]
            _emit(
                event_hook,
                "capture_root_pinned",
                str(path_spec.display_root),
                extension_root,
            )

            _confirm_child(
                native,
                volume,
                parent_id,
                package_name,
                int(expected_identity.package_file_id),
                volume_serial,
                max_component,
            )
            package = _open_file_by_id(
                native, volume, int(expected_identity.package_file_id), directory=True
            )
            handles.append(package)
            _verify_opened(
                native,
                package,
                int(expected_identity.package_file_id),
                volume_serial,
                directory=True,
            )
            _confirm_child(
                native,
                volume,
                parent_id,
                package_name,
                int(expected_identity.package_file_id),
                volume_serial,
                max_component,
            )

            manifest = _read_manifest(
                native,
                volume,
                package,
                int(expected_identity.package_file_id),
                package_name,
                volume_serial,
                max_component,
                EXTENSION_MANIFEST_ABI.max_bytes,
                event_hook,
                return_identity=True,
            )
            if not isinstance(manifest, tuple):
                raise SourceSnapshotError("candidate_stale")
            manifest_bytes, manifest_id, manifest_size, manifest_write = manifest
            if (
                manifest_id != expected_identity.manifest_file_id
                or manifest_size != expected_identity.manifest_size
                or manifest_write != expected_identity.manifest_last_write
                or hashlib.sha256(manifest_bytes).digest() != expected_manifest_digest
            ):
                raise SourceSnapshotError("candidate_stale")
            from .disk_manifest import ManifestError, parse_manifest

            try:
                parsed = parse_manifest(manifest_bytes)
            except ManifestError:
                raise SourceSnapshotError("candidate_stale") from None
            if parsed.package_id != package_id:
                raise SourceSnapshotError("candidate_stale")

            files: dict[str, bytes] = {}
            tree_count = [0]
            total_bytes = [0]
            visited: list[tuple[int, tuple[tuple[str, int, int], ...]]] = []
            _capture_windows_directory(
                native,
                volume,
                package,
                prefix="",
                depth=0,
                package_name=package_name,
                package_id=int(expected_identity.package_file_id),
                volume_serial=volume_serial,
                max_component=max_component,
                limits=limits,
                files=files,
                tree_count=tree_count,
                total_bytes=total_bytes,
                visited=visited,
                event_hook=event_hook,
            )

            for directory_id, before in visited:
                with _reopen_directory(
                    native, volume, directory_id, volume_serial
                ) as directory:
                    after = _windows_inventory(
                        native,
                        directory,
                        max_component=max_component,
                        max_entries=int(limits.max_tree_entries),
                    )
                if before != after:
                    raise SourceSnapshotError("candidate_stale")
            final_manifest = _read_manifest(
                native,
                volume,
                package,
                int(expected_identity.package_file_id),
                package_name,
                volume_serial,
                max_component,
                EXTENSION_MANIFEST_ABI.max_bytes,
                event_hook,
                return_identity=True,
            )
            if (
                not isinstance(final_manifest, tuple)
                or final_manifest[1:] != manifest[1:]
                or hashlib.sha256(final_manifest[0]).digest()
                != expected_manifest_digest
            ):
                raise SourceSnapshotError("candidate_stale")
            _confirm_child(
                native,
                volume,
                parent_id,
                package_name,
                int(expected_identity.package_file_id),
                volume_serial,
                max_component,
            )
            if (
                _volume_name(native, path_spec.drive_root).casefold()
                != volume_guid.casefold()
            ):
                raise SourceSnapshotError("candidate_stale")
            return files
        finally:
            close_failed = False
            for handle in reversed(handles):
                try:
                    handle.close()
                except WindowsScanError:
                    close_failed = True
            if close_failed:
                raise SourceSnapshotError("source_unavailable")
    except SourceSnapshotError:
        raise
    except WindowsScanError as exc:
        mapping = {
            "budget_exceeded": "source_budget_exceeded",
            "reparse_rejected": "source_link_rejected",
            "manifest_linked": "source_hardlink_rejected",
            "unsupported_environment": "unsupported_environment",
        }
        raise SourceSnapshotError(mapping.get(exc.code, "candidate_stale")) from None
    except (OSError, TypeError, ValueError, RuntimeError, OverflowError):
        raise SourceSnapshotError("source_unavailable") from None


def _confirm_child(
    native: _Native,
    volume: _Handle,
    parent_id: int,
    name: str,
    expected_id: int,
    volume_serial: int,
    max_component: int,
) -> None:
    with _reopen_directory(native, volume, parent_id, volume_serial) as parent:
        matches = [
            entry
            for entry in _enumerate(
                native,
                parent,
                max_entries=COMPONENT_LOOKUP_LIMIT,
                max_component=max_component,
                target_name=name,
            )
            if entry.name == name
        ]
    if (
        len(matches) != 1
        or matches[0].file_id != expected_id
        or matches[0].attributes & FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise WindowsScanError("identity_changed")


def _windows_inventory(
    native: _Native,
    directory: _Handle,
    *,
    max_component: int,
    max_entries: int,
) -> tuple[tuple[str, int, int], ...]:
    entries = _enumerate(
        native, directory, max_entries=max_entries, max_component=max_component
    )
    result = []
    for entry in entries:
        if entry.attributes & FILE_ATTRIBUTE_REPARSE_POINT:
            raise WindowsScanError("reparse_rejected")
        result.append((entry.name, entry.file_id, entry.attributes))
    return tuple(sorted(result))


def _capture_windows_directory(
    native: _Native,
    volume: _Handle,
    directory: _Handle,
    *,
    prefix: str,
    depth: int,
    package_name: str,
    package_id: int,
    volume_serial: int,
    max_component: int,
    limits: object,
    files: dict[str, bytes],
    tree_count: list[int],
    total_bytes: list[int],
    visited: list[tuple[int, tuple[tuple[str, int, int], ...]]],
    event_hook: Callable[..., None] | None,
) -> None:
    from .source_snapshot import _check_source_path, _importable_source_path

    if depth > int(limits.max_depth):
        raise WindowsScanError("budget_exceeded")
    current = _get_info(native, directory)
    directory_id = _file_id(current)
    before = _windows_inventory(
        native,
        directory,
        max_component=max_component,
        max_entries=int(limits.max_tree_entries),
    )
    visited.append((directory_id, before))
    for name, file_id, attributes in before:
        tree_count[0] += 1
        if tree_count[0] > int(limits.max_tree_entries):
            raise WindowsScanError("budget_exceeded")
        relative = f"{prefix}/{name}" if prefix else name
        _check_source_path(relative)
        is_directory = bool(attributes & FILE_ATTRIBUTE_DIRECTORY)
        if is_directory:
            if depth >= int(limits.max_depth):
                raise WindowsScanError("budget_exceeded")
            child = _open_file_by_id(native, volume, file_id, directory=True)
            try:
                _verify_opened(native, child, file_id, volume_serial, directory=True)
                _confirm_child(
                    native,
                    volume,
                    directory_id,
                    name,
                    file_id,
                    volume_serial,
                    max_component,
                )
                _capture_windows_directory(
                    native,
                    volume,
                    child,
                    prefix=relative,
                    depth=depth + 1,
                    package_name=package_name,
                    package_id=package_id,
                    volume_serial=volume_serial,
                    max_component=max_component,
                    limits=limits,
                    files=files,
                    tree_count=tree_count,
                    total_bytes=total_bytes,
                    visited=visited,
                    event_hook=event_hook,
                )
            finally:
                child.close()
            continue
        if not name.endswith(".py"):
            continue
        if attributes & FILE_ATTRIBUTE_DEVICE:
            raise WindowsScanError("manifest_invalid")
        if len(files) >= int(limits.max_files):
            raise WindowsScanError("budget_exceeded")
        if not _importable_source_path(relative):
            raise WindowsScanError("unsupported_environment")
        source = _open_file_by_id(native, volume, file_id, directory=False)
        with source:
            info = _verify_opened(
                native, source, file_id, volume_serial, directory=False
            )
            if info.dwFileAttributes & FILE_ATTRIBUTE_DEVICE:
                raise WindowsScanError("manifest_invalid")
            if info.nNumberOfLinks != 1:
                raise WindowsScanError("manifest_linked")
            size = (int(info.nFileSizeHigh) << 32) | int(info.nFileSizeLow)
            if size > int(limits.max_file_bytes):
                raise WindowsScanError("budget_exceeded")
            _confirm_child(
                native,
                volume,
                directory_id,
                name,
                file_id,
                volume_serial,
                max_component,
            )
            _emit(event_hook, "source_pinned", relative, directory, source)
            before_change = _change_time(native, source)
            content = _read_source_handle(
                native, source, size, int(limits.max_file_bytes)
            )
            after_info = _verify_opened(
                native, source, file_id, volume_serial, directory=False
            )
            after_change = _change_time(native, source)
            if (
                after_info.nNumberOfLinks != 1
                or after_info.nFileSizeHigh != info.nFileSizeHigh
                or after_info.nFileSizeLow != info.nFileSizeLow
                or after_info.ftLastWriteTime.dwHighDateTime
                != info.ftLastWriteTime.dwHighDateTime
                or after_info.ftLastWriteTime.dwLowDateTime
                != info.ftLastWriteTime.dwLowDateTime
                or after_change != before_change
            ):
                raise WindowsScanError("identity_changed")
        total_bytes[0] += len(content)
        if total_bytes[0] > int(limits.max_package_bytes):
            raise WindowsScanError("budget_exceeded")
        files[relative] = content


def _read_source_handle(
    native: _Native, handle: _Handle, expected_size: int, max_bytes: int
) -> bytes:
    if expected_size > max_bytes:
        raise WindowsScanError("budget_exceeded")
    chunks: list[bytes] = []
    total = 0
    while total < expected_size:
        request = min(65_536, expected_size - total)
        storage = ctypes.create_string_buffer(request)
        count = wintypes.DWORD()
        if not native.ReadFile(
            wintypes.HANDLE(handle.value),
            ctypes.byref(storage),
            request,
            ctypes.byref(count),
            None,
        ):
            raise WindowsScanError("io_error")
        if count.value == 0:
            raise WindowsScanError("identity_changed")
        chunks.append(storage.raw[: count.value])
        total += count.value
    storage = ctypes.create_string_buffer(1)
    count = wintypes.DWORD()
    if not native.ReadFile(
        wintypes.HANDLE(handle.value),
        ctypes.byref(storage),
        1,
        ctypes.byref(count),
        None,
    ):
        raise WindowsScanError("io_error")
    if count.value:
        raise WindowsScanError("budget_exceeded")
    return b"".join(chunks)


def _change_time(native: _Native, handle: _Handle) -> int:
    info = _FileBasicInfo()
    if not native.GetFileInformationByHandleEx(
        wintypes.HANDLE(handle.value),
        FILE_BASIC_INFO_CLASS,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        raise WindowsScanError("io_error")
    return int(info.ChangeTime)
