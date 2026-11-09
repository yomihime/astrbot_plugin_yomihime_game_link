"""Local-process exclusivity for a Core data directory.

Keep the descriptor open for the entire coordinator lifetime. The file is never
removed on release: unlinking a locked inode would permit a second owner.
Network filesystem lock semantics are outside this contract.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path


class RuntimeOwnerLockError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class RuntimeOwnerLock:
    def __init__(self, data_root: str | Path) -> None:
        root = Path(data_root).resolve(strict=True)
        if not root.is_dir():
            raise RuntimeOwnerLockError("invalid_data_dir")
        path = root / ".ygl-runtime-owner.lock"
        try:
            if path.is_symlink():
                raise RuntimeOwnerLockError("unsafe_lock_file")
            flags = os.O_RDWR | os.O_CREAT
            flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(path, flags, 0o600)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise RuntimeOwnerLockError("unsafe_lock_file")
                if os.stat(path, follow_symlinks=False).st_ino != info.st_ino:
                    raise RuntimeOwnerLockError("unsafe_lock_file")
                if os.name == "nt":
                    import msvcrt

                    if info.st_size == 0:
                        os.write(fd, b"\0")
                    os.lseek(fd, 0, os.SEEK_SET)
                    try:
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    except OSError as exc:
                        raise RuntimeOwnerLockError("data_dir_busy") from exc
                else:
                    import fcntl

                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except OSError as exc:
                        raise RuntimeOwnerLockError("data_dir_busy") from exc
                latest = os.stat(path, follow_symlinks=False)
                if (latest.st_dev, latest.st_ino, latest.st_nlink) != (
                    info.st_dev,
                    info.st_ino,
                    1,
                ):
                    raise RuntimeOwnerLockError("unsafe_lock_file")
                self._fd = fd
                self.path = path
            except BaseException:
                os.close(fd)
                raise
        except RuntimeOwnerLockError:
            raise
        except OSError as exc:
            raise RuntimeOwnerLockError("lock_unavailable") from exc

    def close(self) -> None:
        fd = getattr(self, "_fd", None)
        if fd is None:
            return
        self._fd = None
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
