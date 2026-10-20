"""Private creation and lifetime ancestor pins for Windows credential storage.

No existing ACL is modified. Same-user compromise and administrators are
outside this boundary; handles prevent untrusted path replacement, not them.
"""

from __future__ import annotations

import ctypes
import secrets
import uuid
from contextlib import ExitStack
from ctypes import wintypes
from pathlib import Path

from ..managed_key_file import ManagedKeyFileError
from . import fs
from .maintenance import READ_CONTROL
from .managed_key_io import _bindings, _file_check, _pin, _private_descriptor


def identity_private_base() -> Path:
    """Resolve LocalAppData for the actual process token, ignoring environment."""
    fs._check_runtime()
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    shell.SHGetKnownFolderPath.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole.CoTaskMemFree.restype = None
    # FOLDERID_LocalAppData. A null token means the current process identity.
    folder = ctypes.create_string_buffer(
        uuid.UUID("f1b32785-6fba-4fcf-9d55-7b8e7f157091").bytes_le
    )
    result = ctypes.c_void_p()
    try:
        if (
            shell.SHGetKnownFolderPath(folder, 0, None, ctypes.byref(result)) != 0
            or not result.value
        ):
            raise ManagedKeyFileError()
        path = Path(ctypes.wstring_at(result))
        # Pinning verifies both the actual runtime SID and the whole parent chain.
        with _pin(path, path, private_data=False):
            return path
    finally:
        if result.value:
            ole.CoTaskMemFree(result)


def create_private_directory(parent: Path, name: str) -> Path:
    """CREATE_NEW with a protected inheritable DACL; verify any existing name."""
    path = parent / name
    with _pin(parent, parent, private_data=False) as (
        native,
        _path,
        _serial,
        _operator,
        _trusted,
        _handle,
        maximum,
    ):
        fs._validate_component(name, maximum)
        _bindings(native)
        with _private_descriptor(native) as attributes:
            if not native.dll.CreateDirectoryW(str(path), ctypes.byref(attributes)):
                if native.last_error() != 183:
                    raise ManagedKeyFileError()
        with _pin(path, path):
            pass
    return path


def publish_private_selector(instance: Path, payload: bytes, operation_id: str) -> None:
    """Flush a same-directory candidate, then atomically publish write-through."""
    publish_private_document(instance, "active.json", payload)


def publish_private_document(instance: Path, name: str, payload: bytes) -> None:
    """Publish bounded internal metadata; failed candidates are preserved."""
    from .managed_key_io import create_key, read_key

    if len(payload) > 2 * 1024 * 1024:
        raise ManagedKeyFileError()
    fs._validate_component(name, 255)
    candidate = "." + name + "-" + secrets.token_hex(16) + ".tmp"
    try:
        create_key(instance, instance, candidate, payload)
    except ManagedKeyFileError as error:
        if (
            error.code != "key_file_exists"
            or read_key(instance, instance, candidate, len(payload) + 1) != payload
        ):
            raise
    with _pin(instance, instance) as (
        native,
        _path,
        _serial,
        _operator,
        _trusted,
        _handle,
        _maximum,
    ):
        native.dll.MoveFileExW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
        ]
        native.dll.MoveFileExW.restype = wintypes.BOOL
        if not native.dll.MoveFileExW(
            str(instance / candidate), str(instance / name), 1 | 8
        ):
            raise ManagedKeyFileError()


class WindowsPrivateStorageGuard:
    """Hold no-delete directory handles and verify all existing private entries."""

    def __init__(self, root: Path, *, create_children: bool = True):
        self.root = root
        self.create_children = create_children
        self._stack: ExitStack | None = None

    def __enter__(self):
        if self._stack is not None:
            raise ManagedKeyFileError()
        stack = ExitStack()
        try:
            stack.enter_context(_pin(self.root, self.root))
            for name in ("keys", "secrets", "temp", "recovery"):
                child = self.root / name
                if self.create_children:
                    child = create_private_directory(self.root, name)
                elif not child.exists():
                    continue
                stack.enter_context(_pin(child, self.root))
            self._stack = stack
            self.validate()
            return self
        except BaseException:
            self._stack = None
            stack.close()
            raise

    def validate(self):
        if self._stack is None:
            raise ManagedKeyFileError()
        pending, count = [(self.root, 0)], 0
        while pending:
            directory, depth = pending.pop()
            if depth > 32:
                raise ManagedKeyFileError()
            with _pin(directory, self.root) as (
                native,
                expected,
                serial,
                operator,
                trusted,
                handle,
                maximum,
            ):
                for entry in fs._enumerate(
                    native, handle, max_entries=8192, max_component=maximum
                ):
                    if (
                        not self.create_children
                        and directory == self.root
                        and entry.name
                        not in {
                            "runtime.sqlite3",
                            "runtime.sqlite3-wal",
                            "runtime.sqlite3-shm",
                            "keys",
                            "secrets",
                            "temp",
                            "recovery",
                        }
                    ):
                        continue
                    count += 1
                    if count > 8192 or entry.attributes & (
                        fs.FILE_ATTRIBUTE_REPARSE_POINT | fs.FILE_ATTRIBUTE_DEVICE
                    ):
                        raise ManagedKeyFileError()
                    if entry.attributes & fs.FILE_ATTRIBUTE_DIRECTORY:
                        pending.append((directory / entry.name, depth + 1))
                        continue
                    named = expected + "\\" + entry.name
                    value = fs._handle_value(
                        native.CreateFileW(
                            named,
                            READ_CONTROL | fs.FILE_READ_ATTRIBUTES | fs.FILE_READ_DATA,
                            7,
                            None,
                            fs.OPEN_EXISTING,
                            fs.FILE_FLAG_OPEN_REPARSE_POINT,
                            None,
                        )
                    )
                    if value is None:
                        raise ManagedKeyFileError()
                    with fs._Handle(native, value) as child:
                        if (
                            _file_check(native, child, named, serial, operator, trusted)
                            != entry.file_id
                        ):
                            raise ManagedKeyFileError()

    def close(self):
        stack, self._stack = self._stack, None
        if stack is not None:
            stack.close()

    def __exit__(self, *exc):
        self.close()


__all__ = [
    "WindowsPrivateStorageGuard",
    "create_private_directory",
    "identity_private_base",
]
