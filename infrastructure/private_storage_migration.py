"""Explicit, supervised migration of an isolated complete credential store.

The source supervisor proves writer exclusion. SQLite locks and filesystem
pins are additional checks; neither substitutes for complete consumer lineage.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path

from .managed_key_manager import ManagedKeyManager
from .private_storage import (
    PrivateStorageError,
    PrivateStorageLayout,
    instance_identity,
)
from .sqlite.database import SQLiteDatabase, SQLiteUnitOfWork


@dataclass(frozen=True)
class MigrationPlan:
    layout: PrivateStorageLayout
    operation_id: str
    source_identity: str
    generation: int
    revision: int
    inventory_digest: str
    snapshot_digest: str
    files_digest: str
    files: tuple[tuple[str, str, int], ...]

    def safe_projection(self):
        return {
            "operation_id": self.operation_id,
            "source_identity": self.source_identity,
            "generation": self.generation,
            "revision": self.revision,
            "inventory_digest": self.inventory_digest,
            "snapshot_digest": self.snapshot_digest,
            "files_digest": self.files_digest,
            "file_count": len(self.files),
            "source_retained": True,
            "requires_stopped_host": True,
        }


@dataclass(frozen=True)
class MigrationReceipt:
    operation_id: str
    source_identity: str
    manifest_digest: str
    state: str = "committed"
    source_retained: bool = True
    restart_required: bool = True

    def safe_projection(self):
        return dict(vars(self))


def _source_identity(layout):
    info = layout.database_path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise PrivateStorageError("inventory_unknown")
    return hashlib.sha256(
        f"{instance_identity(layout.data_root)}:{info.st_dev}:{info.st_ino}".encode()
    ).hexdigest()


def _snapshot_digest(connection):
    digest = hashlib.sha256()
    for statement in connection.iterdump():
        digest.update(statement.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _files(layout, *, ignore=()):
    result = []
    total = 0
    for directory in ("keys", "secrets", "temp", "recovery"):
        root = layout.storage_root / directory
        try:
            info = root.lstat()
        except FileNotFoundError:
            continue
        if (
            not stat.S_ISDIR(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400
        ):
            raise PrivateStorageError("inventory_unknown")
        for parent, directories, files in os.walk(root, followlinks=False):
            if len(Path(parent).relative_to(root).parts) > 32:
                raise PrivateStorageError("inventory_unknown")
            for name in directories + files:
                path = Path(parent) / name
                if path.relative_to(layout.storage_root).as_posix() in ignore:
                    continue
                before = path.lstat()
                if (
                    stat.S_ISLNK(before.st_mode)
                    or getattr(before, "st_file_attributes", 0) & 0x400
                ):
                    raise PrivateStorageError("inventory_unknown")
                if name in directories:
                    continue
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                    raise PrivateStorageError("inventory_unknown")
                total += before.st_size
                if len(result) >= 8192 or total > 512 * 1024 * 1024:
                    raise PrivateStorageError("inventory_unknown")
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                after = path.lstat()
                if (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                    raise PrivateStorageError("stale_inventory")
                result.append(
                    (
                        path.relative_to(layout.storage_root).as_posix(),
                        digest.hexdigest(),
                        before.st_size,
                    )
                )
    return tuple(sorted(result))


def _files_digest(files):
    return hashlib.sha256(json.dumps(files, separators=(",", ":")).encode()).hexdigest()


def _manager(layout):
    return ManagedKeyManager(
        SQLiteDatabase(layout.database_path), layout.key_data_root, layout.secret_root
    )


def plan_storage_migration(layout: PrivateStorageLayout) -> MigrationPlan:
    """Read a safe plan; never initialize a DB, copy files or change source ACLs."""
    if (
        layout.security_status != "legacy_review_required"
        or layout.instance_root is None
    ):
        raise PrivateStorageError("migration_not_required")
    layout.assert_credential_writable()
    manager = _manager(layout)
    connection = sqlite3.connect(f"{layout.database_path.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        epoch = connection.execute(
            "SELECT * FROM private_storage_epoch WHERE singleton=1"
        ).fetchone()
        unit = SQLiteUnitOfWork(connection)
        status, pending = manager._status_in(unit), manager._pending(unit)
        if pending is not None and pending["purpose"] == "storage_migration":
            operation_id = str(pending["operation_id"])
        elif status["ready"] and epoch["state"] in {"active", "activated"}:
            operation_id = secrets.token_hex(16)
        else:
            raise PrivateStorageError("credential_storage_not_ready")
        files = _files(layout)
        return MigrationPlan(
            layout,
            operation_id,
            _source_identity(layout),
            status["generation"],
            status["revision"],
            status["inventory_digest"],
            _snapshot_digest(connection),
            _files_digest(files),
            files,
        )
    except sqlite3.Error:
        raise PrivateStorageError("storage_schema_unavailable") from None
    finally:
        connection.close()


def _fault(stage):
    """Named durability boundaries used by fault-injection tests."""


def _assert_proof(proof, plan):
    try:
        from ..services.storage_migration_supervisor import OfflineSourceLease
    except ImportError:
        raise PrivateStorageError("offline_supervisor_unavailable") from None

    if type(proof) is not OfflineSourceLease:
        raise PrivateStorageError("source_exclusivity_unproven")
    proof.assert_current(plan)


def _fence_source(connection, plan):
    source = connection.execute(
        "SELECT * FROM private_storage_epoch WHERE singleton=1"
    ).fetchone()
    if source["state"] == "fenced":
        if source["operation_id"] != plan.operation_id:
            raise PrivateStorageError("operation_changed")
        return
    connection.execute(
        "UPDATE private_storage_epoch SET epoch=epoch+1,state='fenced',operation_id=?,source_identity=?,target_identity=?,inventory_digest=? WHERE singleton=1",
        (
            plan.operation_id,
            plan.source_identity,
            instance_identity(plan.layout.data_root),
            plan.inventory_digest,
        ),
    )
    connection.execute(
        "INSERT INTO managed_master_key_operations(operation_id,kind,state,candidate_key_id,expected_generation,expected_revision,inventory_digest,purpose) VALUES(?,'restore','preparing',NULL,?,?,?,'storage_migration')",
        (plan.operation_id, plan.generation, plan.revision, plan.inventory_digest),
    )
    # Old SQLite consumers do not know the epoch table. Persistent triggers
    # reject their ordinary/cache/subscription/grant/receipt writes too.
    names = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    for index, name in enumerate(names):
        quoted = '"' + name.replace('"', '""') + '"'
        for action in ("INSERT", "UPDATE", "DELETE"):
            connection.execute(
                f"CREATE TRIGGER ygl_storage_fence_{index}_{action.lower()} BEFORE {action} ON {quoted} BEGIN SELECT RAISE(ABORT, 'private_storage_source_fenced'); END"
            )


def execute_storage_migration(
    plan: MigrationPlan, *, source_proof, private_guard
) -> MigrationReceipt:
    """Public entry: only exact current supervisor authority reaches the pipeline."""
    _assert_proof(source_proof, plan)
    from .private_storage_transfer import execute_pipeline

    return execute_pipeline(
        plan,
        assert_current=lambda: _assert_proof(source_proof, plan),
        private_guard=private_guard,
    )


def verify_committed_storage(layout, selector=None):
    """Shared read-only reconciliation for cold load and commit-unknown recovery."""
    from .private_storage_transfer import verify_committed_storage as verify

    return verify(layout, selector)


__all__ = [
    "MigrationPlan",
    "MigrationReceipt",
    "plan_storage_migration",
    "execute_storage_migration",
    "verify_committed_storage",
]
