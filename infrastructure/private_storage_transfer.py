"""Private migration pipeline, candidate journal and committed-state verifier.

Only the public migration entry verifies supervisor authority. Tests may call
this private pipeline with an isolated synthetic source; that does not prove
the source-supervision boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
from pathlib import Path

from .managed_key_file import ManagedKeyFileError
from .managed_key_manager import ManagedKeyConflict, _check
from .private_storage import (
    PrivateStorageError,
    PrivateStorageLayout,
    _read_selector,
    instance_identity,
)
from .private_storage_migration import (
    MigrationReceipt,
    _fault,
    _fence_source,
    _files,
    _files_digest,
    _manager,
    _snapshot_digest,
    _source_identity,
)
from .sqlite.database import SQLiteUnitOfWork
from .windows.managed_key_io import read_key
from .windows.private_storage import (
    WindowsPrivateStorageGuard,
    create_private_directory,
    publish_private_document,
    publish_private_selector,
)

_AUXILIARY = {"runtime.sqlite3-journal", "runtime.sqlite3-wal", "runtime.sqlite3-shm"}


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _identity(path):
    info = path.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & 0x400
        or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
        or stat.S_ISREG(info.st_mode)
        and info.st_nlink != 1
    ):
        raise PrivateStorageError("target_inventory_conflict")
    return [info.st_dev, info.st_ino]


def _tree(root):
    entries = {".": {"identity": _identity(root), "directory": True}}
    for parent, dirs, files in os.walk(root, followlinks=False):
        if len(Path(parent).relative_to(root).parts) > 32:
            raise PrivateStorageError("target_inventory_conflict")
        for name in dirs + files:
            path = Path(parent) / name
            entries[path.relative_to(root).as_posix()] = {
                "identity": _identity(path),
                "directory": name in dirs,
            }
            if len(entries) > 16384:
                raise PrivateStorageError("target_inventory_conflict")
    return entries


def _load_journal(plan):
    name = "migration-" + plan.operation_id + ".json"
    path = plan.layout.instance_root / name
    if not path.exists():
        return {
            "version": 2,
            "operation_id": plan.operation_id,
            "source_identity": plan.source_identity,
            "inventory_digest": plan.inventory_digest,
            "files_digest": plan.files_digest,
            "attempts": [],
        }
    try:
        value = json.loads(
            read_key(
                plan.layout.instance_root,
                plan.layout.instance_root,
                name,
                2 * 1024 * 1024 + 1,
            )
        )
        if (
            set(value)
            != {
                "version",
                "operation_id",
                "source_identity",
                "inventory_digest",
                "files_digest",
                "attempts",
            }
            or any(
                value[key] != expected
                for key, expected in (
                    ("version", 2),
                    ("operation_id", plan.operation_id),
                    ("source_identity", plan.source_identity),
                    ("inventory_digest", plan.inventory_digest),
                    ("files_digest", plan.files_digest),
                )
            )
            or type(value["attempts"]) is not list
            or len(value["attempts"]) > 16
        ):
            raise ValueError
        storage_names = set()
        for attempt in value["attempts"]:
            if (
                type(attempt) is not dict
                or set(attempt) != {"storage", "state", "entries"}
                or type(attempt["storage"]) is not str
                or re.fullmatch(r"storage-[0-9a-f]{32}", attempt["storage"]) is None
                or attempt["storage"] in storage_names
                or attempt["state"]
                not in {"allocated", "preparing", "abandoned", "prepared"}
                or type(attempt["entries"]) is not dict
                or len(attempt["entries"]) > 16384
            ):
                raise ValueError
            storage_names.add(attempt["storage"])
            for name, entry in attempt["entries"].items():
                if (
                    type(name) is not str
                    or name != "."
                    and (
                        not name
                        or name.startswith("/")
                        or "\\" in name
                        or any(part in {"", ".", ".."} for part in name.split("/"))
                    )
                    or type(entry) is not dict
                    or set(entry) != {"identity", "directory"}
                    or type(entry["directory"]) is not bool
                    or entry["identity"] is not None
                    and (
                        type(entry["identity"]) is not list
                        or len(entry["identity"]) != 2
                        or any(
                            type(item) is not int or item < 0
                            for item in entry["identity"]
                        )
                    )
                ):
                    raise ValueError
        return value
    except (OSError, ValueError, TypeError, KeyError):
        raise PrivateStorageError("target_inventory_conflict") from None


def _save_journal(plan, journal):
    publish_private_document(
        plan.layout.instance_root,
        "migration-" + plan.operation_id + ".json",
        _encode(journal),
    )


def _check_attempt(plan, attempt):
    """Only recorded identities or explicitly intended empty entries are ours.

    SQLite auxiliary names are journalled as allowed before opening SQLite;
    they may survive a process crash and are retained with the abandoned DB.
    No unexpected name or changed recorded identity is adopted or deleted.
    """
    if (
        set(attempt) != {"storage", "state", "entries"}
        or attempt["state"] not in {"allocated", "preparing", "abandoned", "prepared"}
        or re.fullmatch(r"storage-[0-9a-f]{32}", attempt["storage"]) is None
    ):
        raise PrivateStorageError("target_inventory_conflict")
    root = plan.layout.instance_root / attempt["storage"]
    if not root.exists():
        if attempt["entries"]:
            raise PrivateStorageError("target_inventory_conflict")
        return
    with WindowsPrivateStorageGuard(root, create_children=False):
        actual = _tree(root)
        recorded = attempt["entries"]
        if not recorded:
            # Prior durable creation intent permits only the empty root. A
            # nonempty directory without recorded identity is unknown.
            if set(actual) != {"."}:
                raise PrivateStorageError("target_inventory_conflict")
            return
        for name, entry in actual.items():
            if (
                name in _AUXILIARY
                and "runtime.sqlite3" in recorded
                and not entry["directory"]
            ):
                continue
            expected = recorded.get(name)
            if expected is None or expected["directory"] != entry["directory"]:
                raise PrivateStorageError("target_inventory_conflict")
            if (
                expected["identity"] is not None
                and expected["identity"] != entry["identity"]
            ):
                raise PrivateStorageError("target_inventory_conflict")
            if expected["identity"] is None:
                path = root / name
                if (
                    entry["directory"]
                    and any(path.iterdir())
                    or not entry["directory"]
                    and path.stat().st_size
                ):
                    raise PrivateStorageError("target_inventory_conflict")
        for name, expected in recorded.items():
            if expected["identity"] is not None and name not in actual:
                raise PrivateStorageError("target_inventory_conflict")


def _new_attempt(plan, journal):
    import secrets

    for attempt in journal["attempts"]:
        _check_attempt(plan, attempt)
        attempt["state"] = "abandoned"
    if len(journal["attempts"]) >= 16:
        raise PrivateStorageError("migration_attempt_limit")
    attempt = {
        "storage": "storage-" + secrets.token_hex(16),
        "state": "allocated",
        "entries": {},
    }
    journal["attempts"].append(attempt)
    _save_journal(plan, journal)
    root = plan.layout.instance_root / attempt["storage"]
    if root.exists():
        raise PrivateStorageError("target_inventory_conflict")
    create_private_directory(plan.layout.instance_root, attempt["storage"])
    attempt["entries"] = _tree(root)
    attempt["state"] = "preparing"
    _save_journal(plan, journal)
    return root, attempt


def _record_directory(plan, journal, attempt, root, path):
    if path == root:
        return
    _record_directory(plan, journal, attempt, root, path.parent)
    name = path.relative_to(root).as_posix()
    if name in attempt["entries"]:
        return
    attempt["entries"][name] = {"identity": None, "directory": True}
    _save_journal(plan, journal)
    if path.exists():
        raise PrivateStorageError("target_inventory_conflict")
    create_private_directory(path.parent, path.name)
    attempt["entries"][name]["identity"] = _identity(path)
    _save_journal(plan, journal)


def _reserve_file(plan, journal, attempt, root, path):
    _record_directory(plan, journal, attempt, root, path.parent)
    name = path.relative_to(root).as_posix()
    attempt["entries"][name] = {"identity": None, "directory": False}
    _save_journal(plan, journal)
    with path.open("xb") as output:
        output.flush()
        os.fsync(output.fileno())
    attempt["entries"][name]["identity"] = _identity(path)
    _save_journal(plan, journal)


def _copy(plan, journal, attempt, root, name, digest, size):
    destination = root / name
    _reserve_file(plan, journal, attempt, root, destination)
    result = hashlib.sha256()
    with (
        (plan.layout.storage_root / name).open("rb") as incoming,
        destination.open("r+b") as outgoing,
    ):
        chunk_size = min(64 * 1024, max(1, size // 2))
        for chunk in iter(lambda: incoming.read(chunk_size), b""):
            outgoing.write(chunk)
            outgoing.flush()
            result.update(chunk)
            _fault("file_copy_progress:" + name)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    if destination.stat().st_size != size or result.hexdigest() != digest:
        raise PrivateStorageError("stale_inventory")


def verify_committed_storage(layout, selector=None):
    """Reconcile immutable commit provenance and current target readiness.

    The recorded DB hash is a migration-time audit checkpoint. Legitimate
    target writes do not have to preserve that historical content hash.
    """
    try:
        layout.assert_credential_writable()
        selector = selector or _read_selector(layout.instance_root, layout.data_root)
        if selector is None:
            raise ValueError
        target_root = layout.instance_root / selector["storage"]
        target_layout = PrivateStorageLayout(
            layout.data_root, target_root, layout.instance_root, "private"
        )
        with WindowsPrivateStorageGuard(target_root, create_children=False):
            encoded = read_key(
                target_root,
                target_root / "recovery",
                "migration.json",
                2 * 1024 * 1024 + 1,
            )
            if hashlib.sha256(encoded).hexdigest() != selector["manifest_digest"]:
                raise ValueError
            manifest = json.loads(encoded)
            expected_keys = {
                "version",
                "operation_id",
                "source_identity",
                "instance_identity",
                "source_epoch",
                "target_epoch",
                "generation",
                "revision",
                "inventory_digest",
                "files_digest",
                "source_snapshot_digest",
                "target_root_identity",
                "target_database_identity",
                "database_digest",
            }
            if (
                set(manifest) != expected_keys
                or manifest["version"] != 2
                or manifest["operation_id"] != selector["operation_id"]
                or manifest["instance_identity"] != instance_identity(layout.data_root)
                or manifest["source_identity"] != _source_identity(layout)
                or manifest["target_root_identity"] != _identity(target_root)
                or manifest["target_database_identity"]
                != _identity(target_layout.database_path)
            ):
                raise ValueError
            if any(
                type(manifest[key]) is not int or manifest[key] < 1
                for key in ("source_epoch", "target_epoch", "generation", "revision")
            ) or any(
                type(manifest[key]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", manifest[key]) is None
                for key in (
                    "source_identity",
                    "instance_identity",
                    "inventory_digest",
                    "files_digest",
                    "source_snapshot_digest",
                    "database_digest",
                )
            ):
                raise ValueError
            source = sqlite3.connect(
                f"{layout.database_path.as_uri()}?mode=ro", uri=True
            )
            source.row_factory = sqlite3.Row
            target = sqlite3.connect(
                f"{target_layout.database_path.as_uri()}?mode=ro", uri=True
            )
            target.row_factory = sqlite3.Row
            try:
                source.execute("BEGIN")
                target.execute("BEGIN")
                source_epoch = source.execute(
                    "SELECT * FROM private_storage_epoch WHERE singleton=1"
                ).fetchone()
                target_epoch = target.execute(
                    "SELECT * FROM private_storage_epoch WHERE singleton=1"
                ).fetchone()
                for record, state, epoch in (
                    (source_epoch, "fenced", manifest["source_epoch"]),
                    (target_epoch, "activated", manifest["target_epoch"]),
                ):
                    if record is None or (
                        record["state"],
                        record["epoch"],
                        record["operation_id"],
                        record["source_identity"],
                        record["target_identity"],
                        record["inventory_digest"],
                    ) != (
                        state,
                        epoch,
                        manifest["operation_id"],
                        manifest["source_identity"],
                        manifest["instance_identity"],
                        manifest["inventory_digest"],
                    ):
                        raise ValueError
                if (
                    manifest["target_epoch"] != manifest["source_epoch"] + 1
                    or _snapshot_digest(source) != manifest["source_snapshot_digest"]
                    or _files_digest(_files(layout)) != manifest["files_digest"]
                ):
                    raise ValueError
                for connection, state in ((source, "preparing"), (target, "committed")):
                    operation = connection.execute(
                        "SELECT kind,state,purpose,expected_generation,expected_revision,inventory_digest FROM managed_master_key_operations WHERE operation_id=?",
                        (manifest["operation_id"],),
                    ).fetchone()
                    if operation is None or tuple(operation) != (
                        "restore",
                        state,
                        "storage_migration",
                        manifest["generation"],
                        manifest["revision"],
                        manifest["inventory_digest"],
                    ):
                        raise ValueError
                if (
                    target.execute("PRAGMA quick_check").fetchone()[0] != "ok"
                    or target.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name LIKE 'ygl_storage_fence_%'"
                    ).fetchone()
                    is not None
                ):
                    raise ValueError
                if not _manager(target_layout)._status_in(SQLiteUnitOfWork(target))[
                    "ready"
                ]:
                    raise ValueError
            finally:
                target.close()
                source.close()
            return target_layout, manifest
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        sqlite3.Error,
        ManagedKeyFileError,
        ManagedKeyConflict,
    ):
        raise PrivateStorageError("ambiguous_storage") from None


def execute_pipeline(plan, *, assert_current, private_guard):
    """Internal storage algorithm; caller must establish exact source authority."""
    assert_current()
    if (
        type(private_guard) is not WindowsPrivateStorageGuard
        or private_guard.root != plan.layout.instance_root
    ):
        raise PrivateStorageError("private_guard_required")
    private_guard.validate()
    previous = _read_selector(plan.layout.instance_root, plan.layout.data_root)
    if previous is not None:
        if previous["operation_id"] != plan.operation_id:
            raise PrivateStorageError("operation_changed")
        _, manifest = verify_committed_storage(plan.layout, previous)
        if (
            manifest["source_identity"] != plan.source_identity
            or manifest["inventory_digest"] != plan.inventory_digest
            or manifest["files_digest"] != plan.files_digest
            or manifest["generation"] != plan.generation
            or manifest["revision"] != plan.revision
        ):
            raise PrivateStorageError("ambiguous_storage")
        assert_current()
        return MigrationReceipt(
            plan.operation_id, plan.source_identity, previous["manifest_digest"]
        )
    if (
        _source_identity(plan.layout) != plan.source_identity
        or _files_digest(_files(plan.layout)) != plan.files_digest
    ):
        raise PrivateStorageError("stale_inventory")
    manager = _manager(plan.layout)
    connection = sqlite3.connect(plan.layout.database_path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN IMMEDIATE")
        unit = SQLiteUnitOfWork(connection)
        row, inventory = manager._selection(unit), manager._inventory(unit)
        if (
            (row["generation"], row["revision"], inventory["digest"])
            != (plan.generation, plan.revision, plan.inventory_digest)
            or inventory["unknown"]
            or _snapshot_digest(connection) != plan.snapshot_digest
        ):
            raise PrivateStorageError("stale_inventory")
        present, key, error = manager._environment_key()
        if row["source"] == "managed":
            environment_key = key
            key = manager.files.read(row["key_id"])
            if error is not None or present and environment_key != key:
                raise PrivateStorageError("environment_key_conflict")
        elif not present or error is not None or key is None:
            raise PrivateStorageError("credential_storage_not_ready")
        if _check(key) != row["key_check"]:
            raise PrivateStorageError("credential_storage_not_ready")
        manager._decrypt_all(key, inventory)
        assert_current()
        _fence_source(connection, plan)
        connection.commit()
        _fault("source_fenced")
        assert_current()
        connection.execute("BEGIN IMMEDIATE")
        source_epoch = connection.execute(
            "SELECT epoch FROM private_storage_epoch WHERE singleton=1"
        ).fetchone()[0]
        frozen_digest = _snapshot_digest(connection)
        journal = _load_journal(plan)
        target_root, attempt = _new_attempt(plan, journal)
        target_layout = PrivateStorageLayout(
            plan.layout.data_root, target_root, plan.layout.instance_root, "private"
        )
        # Register subdirectory identities before the lifetime guard uses them.
        for name in ("keys", "secrets", "temp", "recovery"):
            _record_directory(plan, journal, attempt, target_root, target_root / name)
        with target_layout.open_guard() as target_guard:
            target_database = target_layout.database_path
            _reserve_file(plan, journal, attempt, target_root, target_database)
            reader = sqlite3.connect(
                f"{plan.layout.database_path.as_uri()}?mode=ro", uri=True
            )
            target = sqlite3.connect(target_database)
            try:
                reader.backup(
                    target,
                    pages=1,
                    progress=lambda *_: _fault("database_copy_progress"),
                )
            finally:
                target.close()
                reader.close()
            _fault("database_copied")
            for name, digest, size in plan.files:
                assert_current()
                _copy(plan, journal, attempt, target_root, name, digest, size)
            _fault("files_flushed")
            if _files(target_layout) != plan.files:
                raise PrivateStorageError("target_inventory_conflict")
            target = sqlite3.connect(target_database, isolation_level=None)
            target.row_factory = sqlite3.Row
            try:
                target.execute("BEGIN IMMEDIATE")
                for (trigger,) in target.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'ygl_storage_fence_%'"
                ).fetchall():
                    target.execute('DROP TRIGGER "' + trigger.replace('"', '""') + '"')
                target.execute(
                    "UPDATE private_storage_epoch SET state='activated',epoch=epoch+1 WHERE singleton=1 AND operation_id=? AND state='fenced'",
                    (plan.operation_id,),
                )
                target.execute(
                    "UPDATE managed_master_key_operations SET state='committed' WHERE operation_id=? AND purpose='storage_migration' AND state='preparing'",
                    (plan.operation_id,),
                )
                target.commit()
                if not _manager(target_layout)._status_in(SQLiteUnitOfWork(target))[
                    "ready"
                ]:
                    raise PrivateStorageError("credential_storage_not_ready")
                target.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                target.close()
            with target_database.open("r+b") as durable:
                os.fsync(durable.fileno())
            _fault("target_activated")
            target_guard.validate()
            manifest = {
                "version": 2,
                "operation_id": plan.operation_id,
                "source_identity": plan.source_identity,
                "instance_identity": instance_identity(plan.layout.data_root),
                "source_epoch": source_epoch,
                "target_epoch": source_epoch + 1,
                "generation": plan.generation,
                "revision": plan.revision,
                "inventory_digest": plan.inventory_digest,
                "files_digest": plan.files_digest,
                "source_snapshot_digest": frozen_digest,
                "target_root_identity": _identity(target_root),
                "target_database_identity": _identity(target_database),
                "database_digest": hashlib.sha256(
                    target_database.read_bytes()
                ).hexdigest(),
            }
            encoded_manifest = _encode(manifest)
            manifest_digest = hashlib.sha256(encoded_manifest).hexdigest()
            manifest_path = target_layout.recovery_root / "migration.json"
            if any(name == "recovery/migration.json" for name, _, _ in plan.files):
                raise PrivateStorageError("target_inventory_conflict")
            _reserve_file(plan, journal, attempt, target_root, manifest_path)
            with manifest_path.open("r+b") as output:
                output.write(encoded_manifest)
                output.flush()
                os.fsync(output.fileno())
            attempt["state"] = "prepared"
            _save_journal(plan, journal)
            assert_current()
            _fault("before_selector")
            selector = _encode(
                {
                    "version": 1,
                    "source_identity": instance_identity(plan.layout.data_root),
                    "operation_id": plan.operation_id,
                    "storage": target_root.name,
                    "manifest_digest": manifest_digest,
                }
            )
            publish_private_selector(
                plan.layout.instance_root, selector, plan.operation_id
            )
            _fault("selector_published")
            verify_committed_storage(plan.layout)
            return MigrationReceipt(
                plan.operation_id, plan.source_identity, manifest_digest
            )
    finally:
        connection.rollback()
        connection.close()
