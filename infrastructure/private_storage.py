"""Credential storage layout selection; never migrate or change an existing ACL."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

from .managed_key_file import ManagedKeyFileError

_STORAGE_NAME = re.compile(r"storage(?:-[0-9a-f]{32})?\Z")


class PrivateStorageError(RuntimeError):
    """Bounded public failure, containing no path, identity or secret."""

    def __init__(self, code="private_storage_unavailable"):
        self.code = code
        super().__init__(code)


def instance_identity(data_root: Path) -> str:
    path = os.path.normcase(str(data_root.absolute()))
    return hashlib.sha256(path.encode("utf-8")).hexdigest()


def _has_legacy_inventory(root: Path) -> bool:
    for name in (
        "runtime.sqlite3",
        "runtime.sqlite3-wal",
        "runtime.sqlite3-shm",
        "keys",
        "secrets",
    ):
        try:
            (root / name).lstat()
            return True
        except FileNotFoundError:
            pass
        except OSError:
            raise PrivateStorageError("inventory_unknown") from None
    return False


@dataclass(frozen=True)
class PrivateStorageLayout:
    data_root: Path
    storage_root: Path
    instance_root: Path | None
    security_status: str

    @property
    def database_path(self):
        return self.storage_root / "runtime.sqlite3"

    @property
    def secret_root(self):
        return self.storage_root / "secrets"

    @property
    def key_data_root(self):
        return self.storage_root

    @property
    def temp_root(self):
        return self.storage_root / "temp"

    @property
    def recovery_root(self):
        return self.storage_root / "recovery"

    def open_guard(self):
        if os.name == "nt" and self.security_status == "private":
            from .windows.private_storage import WindowsPrivateStorageGuard

            return WindowsPrivateStorageGuard(self.storage_root)
        # Legacy safety checks remain in the original key I/O; failure must
        # not prevent unrelated Core/global settings from starting.
        return nullcontext(self)

    def assert_credential_writable(self):
        if os.name == "nt":
            from .windows.private_storage import WindowsPrivateStorageGuard

            try:
                with WindowsPrivateStorageGuard(
                    self.storage_root, create_children=False
                ):
                    pass
            except Exception:
                raise PrivateStorageError("unsafe_storage_layout") from None


def _read_selector(instance: Path, data_root: Path):
    selector = instance / "active.json"
    try:
        info = selector.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 4096:
        raise PrivateStorageError("ambiguous_storage")
    try:
        from .windows.managed_key_io import read_key

        value = json.loads(read_key(instance, instance, "active.json", 4097))
        if (
            set(value)
            != {
                "version",
                "source_identity",
                "operation_id",
                "storage",
                "manifest_digest",
            }
            or value["version"] != 1
            or value["source_identity"] != instance_identity(data_root)
            or not _STORAGE_NAME.fullmatch(value["storage"])
        ):
            raise ValueError
        if not re.fullmatch(r"[0-9a-f]{32}", value["operation_id"]) or not re.fullmatch(
            r"[0-9a-f]{64}", value["manifest_digest"]
        ):
            raise ValueError
        return value
    except (OSError, ValueError, TypeError, KeyError):
        raise PrivateStorageError("ambiguous_storage") from None


def resolve_private_storage(
    data_root: Path, *, private_base: Path | None = None
) -> PrivateStorageLayout:
    """Choose existing legacy data, or a new identity-owned Windows layout.

    ``private_base`` is a trusted Host/test injection, never an environment
    override. Merely selecting a layout never copies or rewrites user data.
    """
    data_root = Path(data_root)
    if not data_root.is_absolute() or ".." in data_root.parts:
        raise PrivateStorageError()
    if os.name != "nt":
        return PrivateStorageLayout(
            data_root, data_root, None, "legacy_review_required"
        )
    from .windows.managed_key_io import _pin
    from .windows.private_storage import create_private_directory, identity_private_base

    try:
        base = (
            Path(private_base) if private_base is not None else identity_private_base()
        )
        if not base.is_absolute() or ".." in base.parts:
            raise PrivateStorageError()
        instance = base
        for part in ("yomihime-game-link", "instances", instance_identity(data_root)):
            instance = create_private_directory(instance, part)
        with _pin(instance, instance):
            selector = _read_selector(instance, data_root)
            legacy = _has_legacy_inventory(data_root)
            if selector is not None:
                from .private_storage_migration import verify_committed_storage

                selected, _manifest = verify_committed_storage(
                    PrivateStorageLayout(
                        data_root, data_root, instance, "legacy_review_required"
                    ),
                    selector,
                )
                return selected
            target = instance / "storage"
            private_exists = _has_legacy_inventory(target)
            if legacy:
                if private_exists:
                    raise PrivateStorageError("ambiguous_storage")
                return PrivateStorageLayout(
                    data_root, data_root, instance, "legacy_review_required"
                )
            target = create_private_directory(instance, "storage")
            return PrivateStorageLayout(data_root, target, instance, "private")
    except PrivateStorageError:
        raise
    except (OSError, ValueError, sqlite3.Error, ManagedKeyFileError):
        raise PrivateStorageError() from None


__all__ = [
    "PrivateStorageError",
    "PrivateStorageLayout",
    "instance_identity",
    "resolve_private_storage",
]
