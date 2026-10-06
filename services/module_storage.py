"""Stable, module-owned directories without changing shared SQLite storage."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from yomihime_sdk.api.storage import ModuleStoragePaths

from ..core.ports import validate_module_id


class ModuleStorageRouter:
    def __init__(self, root: Path):
        self._root = Path(root).absolute()

    @staticmethod
    def _directory(path: Path) -> None:
        if os.path.lexists(path):
            metadata = path.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or getattr(path, "is_junction", lambda: False)()
                or getattr(metadata, "st_file_attributes", 0) & 0x400
            ):
                raise ValueError("module storage directory must not be linked")
        else:
            path.mkdir()

    def paths(self, module_id: str) -> ModuleStoragePaths:
        validate_module_id(module_id)
        # Only Core's module key chooses the namespace. No user path is accepted.
        for parent in (*reversed(self._root.parents), self._root):
            self._directory(parent)
        root = self._root / hashlib.sha256(module_id.encode("utf-8")).hexdigest()
        self._directory(root)
        paths = {}
        for kind in ("config", "cache", "data", "secrets"):
            path = root / kind
            self._directory(path)
            paths[kind] = str(path)
        return ModuleStoragePaths(module_id=module_id, root=str(root), **paths)
