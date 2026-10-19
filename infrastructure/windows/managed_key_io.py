"""Native NTFS key-file I/O with pinned identities and private creation ACLs."""

from __future__ import annotations

import ctypes
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

from ..managed_key_file import ManagedKeyFileError
from . import fs
from .maintenance import (
    _PIN_ACCESS,
    _TRUSTED_INSTALLER,
    INHERIT_ONLY_ACE,
    READ_CONTROL,
    _Ace,
    _mapped_mask,
    _SecurityNative,
    _validate_descriptor,
)

_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_WRITE_THROUGH = 0x80000000
_ERROR_ALREADY_EXISTS = 183
_ERROR_FILE_EXISTS = 80
_SDDL_REVISION_1 = 1
_PARENT_REPLACE = 0x00000040 | 0x00040000 | 0x00080000
_CHILD_REPLACE = 0x00010000 | 0x00040000 | 0x00080000
_OWNER_RIGHTS = "S-1-3-4"


def _resolve_owner_rights(native, owner, aces, allowed_owners):
    """OWNER_RIGHTS applies to this verified owner, never an outsider SID."""
    if owner not in allowed_owners:
        raise ManagedKeyFileError()
    owner_rights = native._fixed_sid(_OWNER_RIGHTS)
    return tuple(
        _Ace(
            ace.kind, ace.flags, ace.mask, owner if ace.sid == owner_rights else ace.sid
        )
        for ace in aces
    )


def _edge_acl(native, handle, operator, trusted, *, parent: bool, volume: bool = False):
    owner, aces = native.descriptor(handle)
    allowed_owners = {operator, *trusted}
    if volume:
        allowed_owners.add(native._fixed_sid(_TRUSTED_INSTALLER))
    aces = _resolve_owner_rights(native, owner, aces, allowed_owners)
    allowed = allowed_owners
    forbidden = _PARENT_REPLACE if parent else _CHILD_REPLACE
    for ace in aces:
        if ace.kind not in {0, 1} or ace.flags & ~0x1F:
            raise ManagedKeyFileError()
        mask = _mapped_mask(ace.mask)
        if ace.kind == 0 and not ace.flags & INHERIT_ONLY_ACE:
            if ace.sid not in allowed and mask & forbidden:
                raise ManagedKeyFileError()


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


def _bindings(native: _SecurityNative) -> None:
    k32, adv = native.dll, native.security
    for dll, name, args, result in (
        (k32, "CreateDirectoryW", [wintypes.LPCWSTR, ctypes.c_void_p], wintypes.BOOL),
        (
            k32,
            "WriteFile",
            [
                wintypes.HANDLE,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
                ctypes.c_void_p,
            ],
            wintypes.BOOL,
        ),
        (k32, "FlushFileBuffers", [wintypes.HANDLE], wintypes.BOOL),
        (
            adv,
            "ConvertSidToStringSidW",
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
            wintypes.BOOL,
        ),
        (
            adv,
            "ConvertStringSecurityDescriptorToSecurityDescriptorW",
            [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.c_void_p,
            ],
            wintypes.BOOL,
        ),
    ):
        function = getattr(dll, name)
        function.argtypes = args
        function.restype = result


@contextmanager
def _private_descriptor(native: _SecurityNative):
    sid = ctypes.create_string_buffer(native.operator())
    sid_text = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    try:
        if not native.security.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise ManagedKeyFileError()
        operator = ctypes.wstring_at(sid_text.value)
        sddl = (
            f"O:{operator}D:P(A;OICI;FA;;;{operator})(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
        )
        if not native.security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, _SDDL_REVISION_1, ctypes.byref(descriptor), None
        ):
            raise ManagedKeyFileError()
        attributes = _SecurityAttributes(
            ctypes.sizeof(_SecurityAttributes), descriptor, False
        )
        yield attributes
    finally:
        if descriptor.value:
            native.LocalFree(descriptor)
        if sid_text.value:
            native.LocalFree(sid_text)


@contextmanager
def _pin(path: Path, data_root: Path):
    """Hold no-delete handles for each ancestor, validating ACL and FileID."""
    handles: list[fs._Handle] = []
    proofs: list[tuple[fs._Handle, int, str, str]] = []
    try:
        fs._check_runtime()
        native = _SecurityNative()
        _bindings(native)
        spec, data = fs._parse_root(path), fs._parse_root(data_root)
        if spec.drive_root.casefold() != data.drive_root.casefold() or tuple(
            item.casefold() for item in spec.components[: len(data.components)]
        ) != tuple(item.casefold() for item in data.components):
            raise ManagedKeyFileError()
        operator = native.operator()
        trusted = tuple(
            native._fixed_sid(sid)
            for sid in ("S-1-5-18", "S-1-5-32-544", _TRUSTED_INSTALLER)
        )
        guid = fs._volume_name(native, spec.drive_root)
        volume = fs._open_volume_root(
            native, guid, access=_PIN_ACCESS, share_mode=fs.FILE_SHARE_READ | 2
        )
        handles.append(volume)
        serial, maximum = fs._volume_serial(native, volume)
        _edge_acl(native, volume, operator, trusted, parent=True, volume=True)
        expected = guid.rstrip("\\")
        for index, component in enumerate(spec.components):
            fs._validate_component(component, maximum)
            expected += "\\" + component
            role = "data" if index >= len(data.components) - 1 else "ancestor"
            value = fs._handle_value(
                native.CreateFileW(
                    expected,
                    _PIN_ACCESS | fs.FILE_LIST_DIRECTORY,
                    fs.FILE_SHARE_READ | 2,
                    None,
                    fs.OPEN_EXISTING,
                    fs.FILE_FLAG_OPEN_REPARSE_POINT | fs.FILE_FLAG_BACKUP_SEMANTICS,
                    None,
                )
            )
            if value is None:
                raise ManagedKeyFileError()
            handle = fs._Handle(native, value)
            handles.append(handle)
            info = fs._get_info(native, handle)
            identity = fs._file_id(info)
            fs._verify_opened(native, handle, identity, serial, directory=True)
            if native.final_path(handle).rstrip("\\").casefold() != expected.casefold():
                raise ManagedKeyFileError()
            _edge_acl(native, handle, operator, trusted, parent=True)
            _edge_acl(native, handle, operator, trusted, parent=False)
            if role == "data":
                owner, aces = native.descriptor(handle)
                aces = _resolve_owner_rights(
                    native, owner, aces, {operator, trusted[0], trusted[1]}
                )
                _validate_descriptor(
                    owner, aces, operator, *trusted, role=role, directory=True
                )
            proofs.append((handle, identity, expected, role))
        yield native, expected, serial, operator, trusted, handles[-1], maximum
        _edge_acl(native, volume, operator, trusted, parent=True, volume=True)
        for handle, identity, expected_path, role in proofs:
            fs._verify_opened(native, handle, identity, serial, directory=True)
            if (
                native.final_path(handle).rstrip("\\").casefold()
                != expected_path.casefold()
            ):
                raise ManagedKeyFileError()
            _edge_acl(native, handle, operator, trusted, parent=True)
            _edge_acl(native, handle, operator, trusted, parent=False)
            if role == "data":
                owner, aces = native.descriptor(handle)
                aces = _resolve_owner_rights(
                    native, owner, aces, {operator, trusted[0], trusted[1]}
                )
                _validate_descriptor(
                    owner, aces, operator, *trusted, role=role, directory=True
                )
    except ManagedKeyFileError:
        raise
    except (OSError, ValueError, fs.WindowsScanError):
        raise ManagedKeyFileError() from None
    finally:
        for handle in reversed(handles):
            handle.close()


@contextmanager
def _ensure_key_dir(data_root: Path, root: Path):
    # Retain the verified parent and ancestor handles through key-file flush.
    with _pin(data_root, data_root) as (
        native,
        _path,
        _serial,
        _operator,
        _trusted,
        _handle,
        _maximum,
    ):
        with _private_descriptor(native) as attributes:
            if not native.dll.CreateDirectoryW(str(root), ctypes.byref(attributes)):
                if native.last_error() != _ERROR_ALREADY_EXISTS:
                    raise ManagedKeyFileError()
        yield


def _file_check(native, handle, expected: str, serial: int, operator, trusted):
    info = fs._get_info(native, handle)
    identity = fs._file_id(info)
    fs._verify_opened(native, handle, identity, serial, directory=False)
    if (
        info.nNumberOfLinks != 1
        or native.final_path(handle).casefold() != expected.casefold()
    ):
        raise ManagedKeyFileError()
    owner, aces = native.descriptor(handle)
    aces = _resolve_owner_rights(
        native, owner, aces, {operator, trusted[0], trusted[1]}
    )
    _validate_descriptor(owner, aces, operator, *trusted, role="data", directory=False)
    return identity


def create_key(data_root: Path, root: Path, name: str, payload: bytes) -> None:
    try:
        with (
            _ensure_key_dir(data_root, root),
            _pin(root, data_root) as (
                native,
                root_path,
                serial,
                operator,
                trusted,
                _handle,
                _maximum,
            ),
        ):
            expected = root_path + "\\" + name
            with _private_descriptor(native) as attributes:
                value = fs._handle_value(
                    native.CreateFileW(
                        expected,
                        _GENERIC_WRITE | READ_CONTROL | fs.FILE_READ_ATTRIBUTES,
                        fs.FILE_SHARE_READ,
                        ctypes.byref(attributes),
                        1,  # CREATE_NEW; existing key content is never replaced.
                        fs.FILE_FLAG_OPEN_REPARSE_POINT | _WRITE_THROUGH,
                        None,
                    )
                )
            if value is None:
                if native.last_error() in {_ERROR_ALREADY_EXISTS, _ERROR_FILE_EXISTS}:
                    raise ManagedKeyFileError("key_file_exists")
                raise ManagedKeyFileError()
            with fs._Handle(native, value) as handle:
                _file_check(native, handle, expected, serial, operator, trusted)
                buffer = ctypes.create_string_buffer(payload)
                written = wintypes.DWORD()
                if not native.dll.WriteFile(
                    wintypes.HANDLE(handle.value),
                    buffer,
                    len(payload),
                    ctypes.byref(written),
                    None,
                ) or written.value != len(payload):
                    raise ManagedKeyFileError()
                if not native.dll.FlushFileBuffers(wintypes.HANDLE(handle.value)):
                    raise ManagedKeyFileError()
                _file_check(native, handle, expected, serial, operator, trusted)
    except ManagedKeyFileError:
        raise
    except (OSError, ValueError, fs.WindowsScanError):
        raise ManagedKeyFileError() from None


def read_key(data_root: Path, root: Path, name: str, limit: int) -> bytes:
    try:
        with _pin(root, data_root) as (
            native,
            root_path,
            serial,
            operator,
            trusted,
            _handle,
            _maximum,
        ):
            expected = root_path + "\\" + name
            value = fs._handle_value(
                native.CreateFileW(
                    expected,
                    _GENERIC_READ | READ_CONTROL | fs.FILE_READ_ATTRIBUTES,
                    fs.FILE_SHARE_READ,
                    None,
                    fs.OPEN_EXISTING,
                    fs.FILE_FLAG_OPEN_REPARSE_POINT,
                    None,
                )
            )
            if value is None:
                raise ManagedKeyFileError()
            with fs._Handle(native, value) as handle:
                _file_check(native, handle, expected, serial, operator, trusted)
                buffer = ctypes.create_string_buffer(limit)
                read = wintypes.DWORD()
                if not native.ReadFile(
                    wintypes.HANDLE(handle.value),
                    buffer,
                    limit,
                    ctypes.byref(read),
                    None,
                ):
                    raise ManagedKeyFileError()
                _file_check(native, handle, expected, serial, operator, trusted)
                return bytes(buffer.raw[: read.value])
    except ManagedKeyFileError:
        raise
    except (OSError, ValueError, fs.WindowsScanError):
        raise ManagedKeyFileError() from None


def list_keys(data_root: Path, root: Path) -> tuple[str, ...]:
    try:
        with _pin(data_root, data_root) as (
            native,
            _parent_path,
            _serial,
            _operator,
            _trusted,
            parent,
            maximum,
        ):
            entries = fs._enumerate(
                native, parent, max_entries=4096, max_component=maximum
            )
            child = [item for item in entries if item.name.casefold() == "keys"]
            if not child:
                return ()
            if len(child) != 1:
                raise ManagedKeyFileError()
            with _pin(root, data_root) as (
                child_native,
                _path,
                _serial,
                _operator,
                _trusted,
                handle,
                maximum,
            ):
                if fs._file_id(fs._get_info(child_native, handle)) != child[0].file_id:
                    raise ManagedKeyFileError()
                entries = fs._enumerate(
                    child_native, handle, max_entries=4096, max_component=maximum
                )
                return tuple(sorted(item.name for item in entries))
    except ManagedKeyFileError:
        raise
    except (OSError, ValueError, fs.WindowsScanError):
        raise ManagedKeyFileError() from None
