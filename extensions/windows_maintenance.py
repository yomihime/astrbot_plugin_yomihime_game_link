"""Windows OS proof for stopped-host maintenance, held until resource close.

This guard owns no configuration authority and never changes an ACL. The
loaded CLI still requires Core's independent admin credential. Private trees
are checked by file ID; ancestors need no enumeration of unrelated siblings.
"""

from __future__ import annotations

import ctypes
import struct
import sys
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from . import windows_fs as fs

READ_CONTROL = 0x00020000
FILE_SHARE_WRITE = 0x00000002
TOKEN_QUERY = 0x0008
ERROR_NO_TOKEN = 1008
ERROR_INSUFFICIENT_BUFFER = 122
OWNER_SECURITY_INFORMATION = 0x00000001
DACL_SECURITY_INFORMATION = 0x00000004
SE_FILE_OBJECT = 1
INHERIT_ONLY_ACE = 0x08
MAX_TREE_ENTRIES = 4096
MAX_TREE_DEPTH = 64
MAX_ACES = 512
_PIN_ACCESS = READ_CONTROL | fs.FILE_READ_ATTRIBUTES | fs.FILE_READ_DATA
_ALL_FILE_RIGHTS = 0x001F01FF
_MUTATION = 0x000D0156
_ANCESTOR_MUTATION = 0x000D0152
_TRUSTED_INSTALLER = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"


class WindowsMaintenanceError(fs.WindowsScanError):
    """A bounded OS-proof failure; never includes a path, SID, or payload."""


@dataclass(frozen=True, slots=True)
class _Ace:
    kind: int
    flags: int
    mask: int
    sid: bytes


def _mapped_mask(mask: int) -> int:
    mapped = mask & 0x0FFFFFFF
    for generic, specific in (
        (0x80000000, 0x00120089),
        (0x40000000, 0x00120116),
        (0x20000000, 0x001200A0),
        (0x10000000, _ALL_FILE_RIGHTS),
    ):
        if mask & generic:
            mapped |= specific
    if mapped & ~_ALL_FILE_RIGHTS:
        raise WindowsMaintenanceError("acl_uninterpretable")
    return mapped


def _validate_descriptor(
    owner: bytes,
    aces: tuple[_Ace, ...],
    operator: bytes,
    system: bytes,
    administrators: bytes,
    trusted_installer: bytes,
    *,
    role: str,
    directory: bool,
) -> None:
    trusted = {operator, system, administrators}
    owners = trusted | ({trusted_installer} if role == "ancestor" else set())
    if owner not in owners:
        raise WindowsMaintenanceError("untrusted_owner")
    for ace in aces:
        if ace.kind not in {0, 1} or ace.flags & ~0x1F:
            raise WindowsMaintenanceError("acl_uninterpretable")
        mask = _mapped_mask(ace.mask)
        # Deny ACEs never grant authority. Do not rely on a deny to compensate
        # for a dangerous outsider allow ACE or noncanonical ACL ordering.
        if ace.kind == 1:
            continue
        if ace.flags & INHERIT_ONLY_ACE and (role == "ancestor" or not directory):
            continue
        forbidden = _ANCESTOR_MUTATION if role == "ancestor" else _MUTATION
        if role == "data":
            forbidden |= 0x0001 | 0x0008  # contents/listing and extended attributes
        if ace.sid not in trusted and mask & forbidden:
            raise WindowsMaintenanceError("untrusted_access")


class _SecurityNative(fs._Native):
    def __init__(self) -> None:
        super().__init__()
        try:
            self.security = ctypes.WinDLL("advapi32", use_last_error=True)
            prototypes = (
                (self.dll, "GetCurrentProcess", [], wintypes.HANDLE),
                (self.dll, "GetCurrentThread", [], wintypes.HANDLE),
                (self.dll, "LocalFree", [ctypes.c_void_p], ctypes.c_void_p),
                (
                    self.dll,
                    "GetFinalPathNameByHandleW",
                    [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD],
                    wintypes.DWORD,
                ),
                (
                    self.security,
                    "OpenProcessToken",
                    [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)],
                    wintypes.BOOL,
                ),
                (
                    self.security,
                    "OpenThreadToken",
                    [
                        wintypes.HANDLE,
                        wintypes.DWORD,
                        wintypes.BOOL,
                        ctypes.POINTER(wintypes.HANDLE),
                    ],
                    wintypes.BOOL,
                ),
                (
                    self.security,
                    "GetTokenInformation",
                    [
                        wintypes.HANDLE,
                        ctypes.c_int,
                        ctypes.c_void_p,
                        wintypes.DWORD,
                        ctypes.POINTER(wintypes.DWORD),
                    ],
                    wintypes.BOOL,
                ),
                (
                    self.security,
                    "GetSecurityInfo",
                    [
                        wintypes.HANDLE,
                        ctypes.c_int,
                        wintypes.DWORD,
                        ctypes.POINTER(ctypes.c_void_p),
                        ctypes.c_void_p,
                        ctypes.POINTER(ctypes.c_void_p),
                        ctypes.c_void_p,
                        ctypes.POINTER(ctypes.c_void_p),
                    ],
                    wintypes.DWORD,
                ),
                (
                    self.security,
                    "IsValidSecurityDescriptor",
                    [ctypes.c_void_p],
                    wintypes.BOOL,
                ),
                (self.security, "IsValidAcl", [ctypes.c_void_p], wintypes.BOOL),
                (self.security, "IsValidSid", [ctypes.c_void_p], wintypes.BOOL),
                (self.security, "GetLengthSid", [ctypes.c_void_p], wintypes.DWORD),
                (
                    self.security,
                    "ConvertStringSidToSidW",
                    [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)],
                    wintypes.BOOL,
                ),
            )
            for dll, name, arguments, result in prototypes:
                function = getattr(dll, name)
                function.argtypes = arguments
                function.restype = result
                setattr(self, name, function)
        except (AttributeError, OSError):
            raise WindowsMaintenanceError("unsupported_environment") from None

    def _free(self, allocation: ctypes.c_void_p) -> None:
        if allocation.value and self.LocalFree(allocation):
            raise WindowsMaintenanceError("security_cleanup_failed")

    def _sid(self, pointer: int) -> bytes:
        if not pointer or not self.IsValidSid(ctypes.c_void_p(pointer)):
            raise WindowsMaintenanceError("identity_unavailable")
        size = int(self.GetLengthSid(ctypes.c_void_p(pointer)))
        if not 8 <= size <= 68:
            raise WindowsMaintenanceError("identity_unavailable")
        return ctypes.string_at(pointer, size)

    def _fixed_sid(self, text: str) -> bytes:
        allocation = ctypes.c_void_p()
        try:
            if not self.ConvertStringSidToSidW(text, ctypes.byref(allocation)):
                raise WindowsMaintenanceError("identity_unavailable")
            return self._sid(allocation.value)
        finally:
            self._free(allocation)

    def operator(self) -> bytes:
        thread = wintypes.HANDLE()
        if self.OpenThreadToken(
            self.GetCurrentThread(), TOKEN_QUERY, True, ctypes.byref(thread)
        ):
            with fs._Handle(self, int(thread.value)):
                raise WindowsMaintenanceError("impersonation_rejected")
        if self.last_error() != ERROR_NO_TOKEN:
            raise WindowsMaintenanceError("identity_unavailable")
        process = wintypes.HANDLE()
        if not self.OpenProcessToken(
            self.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(process)
        ):
            raise WindowsMaintenanceError("identity_unavailable")
        with fs._Handle(self, int(process.value)) as token:
            token_type, returned = wintypes.DWORD(), wintypes.DWORD()
            if (
                not self.GetTokenInformation(
                    wintypes.HANDLE(token.value),
                    8,
                    ctypes.byref(token_type),
                    ctypes.sizeof(token_type),
                    ctypes.byref(returned),
                )
                or returned.value != 4
                or token_type.value != 1
            ):
                raise WindowsMaintenanceError("identity_unavailable")
            required = wintypes.DWORD()
            if (
                self.GetTokenInformation(
                    wintypes.HANDLE(token.value), 1, None, 0, ctypes.byref(required)
                )
                or self.last_error() != ERROR_INSUFFICIENT_BUFFER
                or not 16 <= required.value <= 256
            ):
                raise WindowsMaintenanceError("identity_unavailable")
            buffer = ctypes.create_string_buffer(required.value)
            if not self.GetTokenInformation(
                wintypes.HANDLE(token.value),
                1,
                buffer,
                len(buffer),
                ctypes.byref(returned),
            ) or returned.value != len(buffer):
                raise WindowsMaintenanceError("identity_unavailable")
            sid = ctypes.c_void_p.from_buffer(buffer).value
            base = ctypes.addressof(buffer)
            if sid is None or not base <= sid <= base + len(buffer) - 8:
                raise WindowsMaintenanceError("identity_unavailable")
            sid_header = ctypes.string_at(sid, 8)
            if (
                sid_header[0] != 1
                or sid_header[1] > 15
                or sid + 8 + 4 * sid_header[1] > base + len(buffer)
            ):
                raise WindowsMaintenanceError("identity_unavailable")
            value = self._sid(sid)
            if sid + len(value) > base + len(buffer):
                raise WindowsMaintenanceError("identity_unavailable")
            return value

    def descriptor(self, handle: fs._Handle) -> tuple[bytes, tuple[_Ace, ...]]:
        owner, acl, descriptor = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
        try:
            result = self.GetSecurityInfo(
                wintypes.HANDLE(handle.value),
                SE_FILE_OBJECT,
                OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
                ctypes.byref(owner),
                None,
                ctypes.byref(acl),
                None,
                ctypes.byref(descriptor),
            )
            if (
                result
                or not descriptor.value
                or not self.IsValidSecurityDescriptor(descriptor)
                or not acl.value
                or not self.IsValidAcl(acl)
            ):
                raise WindowsMaintenanceError("acl_unavailable")
            owner_bytes = self._sid(owner.value)
            header = ctypes.string_at(acl.value, 8)
            revision, _, size, count, reserved = struct.unpack("<BBHHH", header)
            if (
                revision not in {2, 4}
                or reserved
                or not 8 <= size <= 65535
                or count > MAX_ACES
            ):
                raise WindowsMaintenanceError("acl_uninterpretable")
            raw = ctypes.string_at(acl.value, size)
            offset, aces = 8, []
            for _ in range(count):
                if offset + 8 > size:
                    raise WindowsMaintenanceError("acl_uninterpretable")
                kind, flags, length, mask = struct.unpack_from("<BBHI", raw, offset)
                if (
                    kind not in {0, 1}
                    or length < 16
                    or length % 4
                    or offset + length > size
                ):
                    raise WindowsMaintenanceError("acl_uninterpretable")
                sid_pointer = acl.value + offset + 8
                sid_header = raw[offset + 8 : offset + 16]
                if (
                    sid_header[0] != 1
                    or sid_header[1] > 15
                    or 8 + 4 * sid_header[1] != length - 8
                ):
                    raise WindowsMaintenanceError("acl_uninterpretable")
                sid = self._sid(sid_pointer)
                if len(sid) != length - 8:
                    raise WindowsMaintenanceError("acl_uninterpretable")
                aces.append(_Ace(kind, flags, mask, sid))
                offset += length
            return owner_bytes, tuple(aces)
        finally:
            self._free(descriptor)

    def final_path(self, handle: fs._Handle) -> str:
        capacity = 256
        while capacity <= fs.MAX_WINDOWS_PATH_UNITS + 1:
            buffer = ctypes.create_unicode_buffer(capacity)
            length = int(
                self.GetFinalPathNameByHandleW(
                    wintypes.HANDLE(handle.value), buffer, capacity, 1
                )
            )
            if not length:
                raise WindowsMaintenanceError("identity_unavailable")
            if length < capacity:
                return buffer.value
            capacity = length + 1
        raise WindowsMaintenanceError("unsupported_environment")


class WindowsMaintenanceGuard:
    """Own pinned code, DB and parent directory handles for one operation."""

    def __init__(self, plugin_root: Path, database: Path) -> None:
        self._plugin_root = plugin_root
        self._database = database
        self._handles: list[fs._Handle] = []
        self._proofs: list[tuple[fs._Handle, str, bool, int, int]] = []
        self._volumes: list[tuple[str, str]] = []
        self._active = False
        self._entered = False
        self._entries_seen = 0

    def _hold(self, handle: fs._Handle) -> fs._Handle:
        self._handles.append(handle)
        return handle

    def _check(
        self, handle: fs._Handle, role: str, directory: bool, identity: int, serial: int
    ) -> None:
        info = fs._verify_opened(
            self._native, handle, identity, serial, directory=directory
        )
        if info.dwFileAttributes & fs.FILE_ATTRIBUTE_DEVICE or (
            not directory and info.nNumberOfLinks != 1
        ):
            raise WindowsMaintenanceError("linked_or_device_rejected")
        owner, aces = self._native.descriptor(handle)
        _validate_descriptor(
            owner, aces, self._operator, *self._trusted, role=role, directory=directory
        )

    def _record(
        self, handle: fs._Handle, role: str, directory: bool, identity: int, serial: int
    ) -> None:
        self._check(handle, role, directory, identity, serial)
        self._proofs.append((handle, role, directory, identity, serial))

    def _chain(self, path: Path, role: str, *, directory: bool):
        spec = fs._parse_root(path)
        if not spec.components:
            raise WindowsMaintenanceError("private_root_required")
        if len(spec.components) > MAX_TREE_DEPTH:
            raise WindowsMaintenanceError("budget_exceeded")
        guid = fs._volume_name(self._native, spec.drive_root)
        self._volumes.append((spec.drive_root, guid))
        volume = self._hold(
            fs._open_volume_root(self._native, guid, access=_PIN_ACCESS)
        )
        serial, maximum = fs._volume_serial(self._native, volume)
        identity = fs._file_id(fs._get_info(self._native, volume))
        self._record(volume, "ancestor", True, identity, serial)
        expected = guid.rstrip("\\")
        for index, component in enumerate(spec.components):
            fs._validate_component(component, maximum)
            expected += "\\" + component
            is_directory = index < len(spec.components) - 1 or directory
            current_role = "ancestor" if index < len(spec.components) - 1 else role
            selected_parts = (
                spec.drive_root.casefold(),
                *[item.casefold() for item in spec.components[: index + 1]],
            )
            if selected_parts[: len(self._data_parts)] == self._data_parts:
                current_role = "data"
            shares = fs.FILE_SHARE_READ | (
                FILE_SHARE_WRITE if current_role == "data" else 0
            )
            flags = fs.FILE_FLAG_OPEN_REPARSE_POINT | (
                fs.FILE_FLAG_BACKUP_SEMANTICS if is_directory else 0
            )
            value = fs._handle_value(
                self._native.CreateFileW(
                    expected, _PIN_ACCESS, shares, None, fs.OPEN_EXISTING, flags, None
                )
            )
            if value is None:
                raise WindowsMaintenanceError("root_unavailable")
            path_handle = self._hold(fs._Handle(self._native, value))
            info = fs._get_info(self._native, path_handle)
            identity = fs._file_id(info)
            self._record(path_handle, current_role, is_directory, identity, serial)
            if (
                self._native.final_path(path_handle).rstrip("\\").casefold()
                != expected.casefold()
            ):
                raise WindowsMaintenanceError("alias_rejected")
            access = _PIN_ACCESS | (
                fs.FILE_LIST_DIRECTORY
                if is_directory and current_role != "ancestor"
                else fs.FILE_READ_DATA
                if current_role == "code" and not is_directory
                else 0
            )
            by_id = self._hold(
                fs._open_file_by_id(
                    self._native,
                    volume,
                    identity,
                    directory=is_directory,
                    access=access,
                    share_mode=shares,
                )
            )
            self._record(by_id, current_role, is_directory, identity, serial)
            if (
                self._native.final_path(by_id).rstrip("\\").casefold()
                != expected.casefold()
            ):
                raise WindowsMaintenanceError("identity_changed")
        return volume, by_id, identity, serial, maximum

    def _entries(self, volume, identity, serial, maximum):
        with fs._open_file_by_id(
            self._native,
            volume,
            identity,
            directory=True,
            access=_PIN_ACCESS | fs.FILE_LIST_DIRECTORY,
        ) as cursor:
            fs._verify_opened(self._native, cursor, identity, serial, directory=True)
            return fs._enumerate(
                self._native,
                cursor,
                max_entries=MAX_TREE_ENTRIES,
                max_component=maximum,
            )

    def _tree(self, volume, root_id, serial, maximum, role, database_id=None, depth=0):
        if depth > MAX_TREE_DEPTH:
            raise WindowsMaintenanceError("budget_exceeded")
        entries = self._entries(volume, root_id, serial, maximum)
        for entry in entries:
            self._entries_seen += 1
            if self._entries_seen > MAX_TREE_ENTRIES:
                raise WindowsMaintenanceError("budget_exceeded")
            if entry.attributes & fs.FILE_ATTRIBUTE_REPARSE_POINT:
                raise WindowsMaintenanceError("reparse_rejected")
            directory = bool(entry.attributes & fs.FILE_ATTRIBUTE_DIRECTORY)
            key = (serial, entry.file_id)
            if key in self._seen:
                raise WindowsMaintenanceError("alias_rejected")
            self._seen.add(key)
            access = _PIN_ACCESS | (
                fs.FILE_LIST_DIRECTORY
                if directory
                else 0
                if entry.file_id == database_id
                else fs.FILE_READ_DATA
            )
            shares = fs.FILE_SHARE_READ | (
                FILE_SHARE_WRITE
                if role == "data" and (directory or entry.file_id == database_id)
                else 0
            )
            handle = self._hold(
                fs._open_file_by_id(
                    self._native,
                    volume,
                    entry.file_id,
                    directory=directory,
                    access=access,
                    share_mode=shares,
                )
            )
            self._record(handle, role, directory, entry.file_id, serial)
            current = [
                item
                for item in self._entries(volume, root_id, serial, maximum)
                if item.name == entry.name
            ]
            if len(current) != 1 or current[0].file_id != entry.file_id:
                raise WindowsMaintenanceError("identity_changed")
            if directory:
                self._tree(
                    volume, entry.file_id, serial, maximum, role, database_id, depth + 1
                )
            elif role == "data" and entry.file_id != database_id:
                # Check mutable files with a short read-only pin, excluding a
                # preexisting writer/mapping, then allow Core's own replace and
                # old-secret cleanup. The fixed private parent DACL stays held.
                self._proofs.pop()
                self._handles.remove(handle)
                handle.close()

    def __enter__(self):
        if self._entered or self._handles:
            raise WindowsMaintenanceError("guard_reuse_rejected")
        self._entered = True
        try:
            fs._check_runtime()
            self._native = _SecurityNative()
            self._operator = self._native.operator()
            self._trusted = tuple(
                self._native._fixed_sid(sid)
                for sid in ("S-1-5-18", "S-1-5-32-544", _TRUSTED_INSTALLER)
            )
            code_spec, data_spec = (
                fs._parse_root(self._plugin_root),
                fs._parse_root(self._database.parent),
            )
            code_parts = (
                code_spec.drive_root.casefold(),
                *[item.casefold() for item in code_spec.components],
            )
            data_parts = (
                data_spec.drive_root.casefold(),
                *[item.casefold() for item in data_spec.components],
            )
            self._data_parts = data_parts
            if (
                code_parts[: len(data_parts)] == data_parts
                or data_parts[: len(code_parts)] == code_parts
            ):
                raise WindowsMaintenanceError("private_roots_overlap")
            code = self._chain(self._plugin_root, "code", directory=True)
            data = self._chain(self._database.parent, "data", directory=True)
            database = self._chain(self._database, "data", directory=False)
            if database[3] != data[3] or database[0].native is not data[0].native:
                raise WindowsMaintenanceError("identity_changed")
            self._seen = {(code[3], code[2]), (data[3], data[2])}
            self._tree(code[0], code[2], code[3], code[4], "code")
            self._tree(data[0], data[2], data[3], data[4], "data", database[2])
            self._active = True
            self._revalidate()
            return self
        except BaseException:
            self._close()
            raise

    def _revalidate(self):
        if self._native.operator() != self._operator:
            raise WindowsMaintenanceError("identity_changed")
        for drive, guid in self._volumes:
            if fs._volume_name(self._native, drive).casefold() != guid.casefold():
                raise WindowsMaintenanceError("identity_changed")
        for proof in self._proofs:
            self._check(*proof)

    def _close(self):
        failed = False
        for handle in reversed(self._handles):
            try:
                handle.close()
            except fs.WindowsScanError:
                failed = True
        self._handles.clear()
        self._proofs.clear()
        self._active = False
        if failed and sys.exc_info()[0] is None:
            raise WindowsMaintenanceError("handle_cleanup_failed")

    def __exit__(self, exc_type, exc, traceback):
        try:
            if exc_type is None:
                self._revalidate()
        finally:
            self._close()


__all__ = ["WindowsMaintenanceError", "WindowsMaintenanceGuard"]
