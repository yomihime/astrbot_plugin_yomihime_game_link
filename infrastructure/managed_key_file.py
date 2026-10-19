"""Private, immutable master-key files below a persistent plugin data root.

The caller serializes creation through the SQLite key-operation journal.  A
file is never replaced: a committed selector refers to its immutable name.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

_MAGIC = b"YGLK\x01"
_SIZE = len(_MAGIC) + 32
_ID = re.compile(r"[0-9a-f]{32}\Z")


class ManagedKeyFileError(RuntimeError):
    """A safe, bounded failure; never includes a path or key material."""

    def __init__(self, code: str = "key_file_unavailable") -> None:
        if code not in {"key_file_unavailable", "key_file_exists", "key_file_invalid"}:
            code = "key_file_unavailable"
        self.code = code
        super().__init__(code)


class ManagedKeyFiles:
    """Read or exclusively create one 32-byte key in an owner-private directory."""

    def __init__(self, data_root: str | Path) -> None:
        self.data_root = Path(data_root)
        if not self.data_root.is_absolute() or ".." in self.data_root.parts:
            raise ManagedKeyFileError("key_file_invalid")
        self.root = self.data_root / "keys"

    @staticmethod
    def _name(key_id: str) -> str:
        if type(key_id) is not str or not _ID.fullmatch(key_id):
            raise ManagedKeyFileError("key_file_invalid")
        return f"master-{key_id}.key"

    def create(self, key_id: str, key: bytes) -> None:
        name = self._name(key_id)
        if type(key) is not bytes or len(key) != 32:
            raise ManagedKeyFileError("key_file_invalid")
        if os.name == "nt":
            self._windows_create(name, key)
        else:
            self._posix_create(name, key)

    def read(self, key_id: str) -> bytes:
        name = self._name(key_id)
        raw = self._windows_read(name) if os.name == "nt" else self._posix_read(name)
        if len(raw) != _SIZE or not raw.startswith(_MAGIC):
            raise ManagedKeyFileError("key_file_invalid")
        return raw[len(_MAGIC) :]

    def entries(self) -> tuple[str, ...]:
        """Return bounded names, including unexpected files, for full inventory."""
        if os.name == "nt":
            return self._windows_entries()
        try:
            os.stat(self.root, follow_symlinks=False)
        except FileNotFoundError:
            return ()
        except OSError:
            raise ManagedKeyFileError() from None
        with self._posix_directory(create=False) as directory:
            try:
                return tuple(sorted(os.listdir(directory)))
            except OSError:
                raise ManagedKeyFileError() from None

    def _posix_directory(self, *, create: bool):
        from contextlib import contextmanager

        @contextmanager
        def pinned():
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            parent = directory = None
            try:
                absolute = self.data_root.absolute()
                parent = os.open(absolute.anchor, flags)
                for component in absolute.parts[1:]:
                    ancestor = os.fstat(parent)
                    if (
                        not stat.S_ISDIR(ancestor.st_mode)
                        or ancestor.st_uid not in (0, os.geteuid())
                        or ancestor.st_mode & 0o022
                        and not ancestor.st_mode & stat.S_ISVTX
                    ):
                        raise ManagedKeyFileError()
                    following = os.open(component, flags, dir_fd=parent)
                    os.close(parent)
                    parent = following
                parent_stat = os.fstat(parent)
                if (
                    not stat.S_ISDIR(parent_stat.st_mode)
                    or parent_stat.st_uid != os.geteuid()
                    or parent_stat.st_mode & 0o022
                ):
                    raise ManagedKeyFileError()
                if create:
                    try:
                        os.mkdir("keys", mode=0o700, dir_fd=parent)
                        os.fsync(parent)
                    except FileExistsError:
                        pass
                directory = os.open("keys", flags, dir_fd=parent)
                info = os.fstat(directory)
                if (
                    not stat.S_ISDIR(info.st_mode)
                    or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077
                    or info.st_nlink < 2
                ):
                    raise ManagedKeyFileError()
                yield directory
            except ManagedKeyFileError:
                raise
            except OSError:
                raise ManagedKeyFileError() from None
            finally:
                if directory is not None:
                    os.close(directory)
                if parent is not None:
                    os.close(parent)

        return pinned()

    @staticmethod
    def _check_posix_file(directory: int, name: str, handle: int) -> None:
        opened = os.fstat(handle)
        named = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or opened.st_mode & 0o077
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise ManagedKeyFileError()

    def _posix_create(self, name: str, key: bytes) -> None:
        with self._posix_directory(create=True) as directory:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
            try:
                handle = os.open(name, flags, 0o600, dir_fd=directory)
            except FileExistsError:
                raise ManagedKeyFileError("key_file_exists") from None
            except OSError:
                raise ManagedKeyFileError() from None
            try:
                self._check_posix_file(directory, name, handle)
                payload = _MAGIC + key
                while payload:
                    count = os.write(handle, payload)
                    if count <= 0:
                        raise ManagedKeyFileError()
                    payload = payload[count:]
                os.fsync(handle)
                self._check_posix_file(directory, name, handle)
                os.fsync(directory)
            except (OSError, ManagedKeyFileError):
                # Preserve an incomplete candidate for explicit journal recovery.
                raise ManagedKeyFileError() from None
            finally:
                os.close(handle)

    def _posix_read(self, name: str) -> bytes:
        with self._posix_directory(create=False) as directory:
            try:
                handle = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory
                )
                try:
                    self._check_posix_file(directory, name, handle)
                    raw = os.read(handle, _SIZE + 1)
                    self._check_posix_file(directory, name, handle)
                    return raw
                finally:
                    os.close(handle)
            except ManagedKeyFileError:
                raise
            except OSError:
                raise ManagedKeyFileError() from None

    def _windows_create(self, name: str, key: bytes) -> None:
        from .windows.managed_key_io import create_key

        create_key(self.data_root, self.root, name, _MAGIC + key)

    def _windows_read(self, name: str) -> bytes:
        from .windows.managed_key_io import read_key

        return read_key(self.data_root, self.root, name, _SIZE + 1)

    def _windows_entries(self) -> tuple[str, ...]:
        from .windows.managed_key_io import list_keys

        return list_keys(self.data_root, self.root)


__all__ = ["ManagedKeyFileError", "ManagedKeyFiles"]
