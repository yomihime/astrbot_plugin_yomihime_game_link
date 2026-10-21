"""Native pipeline tests; synthetic callback is NOT supervisor authority.

These tests exercise storage crash/reconciliation only. The public entry still
requires the exact P5 lease; the true supervised second-writer matrix is separate.
"""

import asyncio
import base64
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.infrastructure import private_storage_transfer as transfer
from ygl_test_subject.infrastructure.managed_key_manager import ManagedKeyManager
from ygl_test_subject.infrastructure.private_storage import (
    PrivateStorageError,
    resolve_private_storage,
)
from ygl_test_subject.infrastructure.private_storage_migration import (
    plan_storage_migration,
)
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.windows.private_storage import (
    WindowsPrivateStorageGuard,
    publish_private_document,
)

from tests.storage import test_managed_key_manager as key_fixture
from tests.storage import test_private_storage as native_fixture


class InjectedCrash(RuntimeError):
    pass


@unittest.skipUnless(os.name == "nt", "Windows native private-storage pipeline")
class MigrationPipelineTests(unittest.TestCase):
    layout = native_fixture.WindowsPrivateStorageTests.layout

    def setUp(self):
        native_fixture.WindowsPrivateStorageTests.setUp(self)
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def create_source(self, name="source"):
        self.original = self.owned / name
        self.original.mkdir()
        database = SQLiteDatabase(self.original / "runtime.sqlite3")
        database.initialize()
        key = bytes(range(32))
        key_fixture.ManagedKeyTests._insert_secret(
            SimpleNamespace(root=self.original, database=database), key
        )
        manager = ManagedKeyManager(database, self.original, self.original / "secrets")

        async def initialize():
            try:
                status = await manager.startup()
                self.assertTrue(status["ready"], status)
                result = await manager.adopt_environment(
                    expected_revision=status["revision"],
                    expected_generation=status["generation"],
                    inventory_digest=status["inventory_digest"],
                )
                self.assertTrue(result["ready"], result)
            finally:
                await database.executor.close()

        with patch.dict(os.environ, {"YGL_SECRET_KEY": base64.b64encode(key).decode()}):
            asyncio.run(initialize())
        layout = self.layout()
        for directory in (layout.temp_root, layout.recovery_root):
            directory.mkdir()
        (layout.temp_root / "synthetic.tmp").write_bytes(b"SYNTHETIC TEMP" * 100)
        (layout.recovery_root / "synthetic.receipt").write_bytes(
            b"SYNTHETIC RECOVERY" * 100
        )
        return layout

    def run_pipeline(self, plan):
        # This private callback proves only that this isolated test has not
        # revoked its own local operation. It is never passed to public execute.
        with WindowsPrivateStorageGuard(plan.layout.instance_root) as guard:
            return transfer.execute_pipeline(
                plan, assert_current=lambda: None, private_guard=guard
            )

    def test_wal_backup_cold_load_reentry_and_legitimate_target_writes(self):
        layout = self.create_source()
        writer = sqlite3.connect(layout.database_path)
        self.addCleanup(writer.close)
        self.assertEqual(writer.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
        writer.execute("CREATE TABLE migration_wal_fixture(value TEXT)")
        writer.execute("INSERT INTO migration_wal_fixture VALUES('SYNTHETIC WAL ROW')")
        writer.commit()
        self.assertGreater(
            layout.database_path.with_name("runtime.sqlite3-wal").stat().st_size, 0
        )
        original_files = transfer._files(layout)
        plan = plan_storage_migration(layout)
        first = self.run_pipeline(plan)
        selected = resolve_private_storage(self.original, private_base=self.owned)
        self.assertEqual(selected.security_status, "private")
        self.assertEqual(transfer._files(layout), original_files)
        target = sqlite3.connect(selected.database_path)
        try:
            self.assertEqual(
                target.execute("SELECT value FROM migration_wal_fixture").fetchone(),
                ("SYNTHETIC WAL ROW",),
            )
            target.execute(
                "INSERT INTO migration_wal_fixture VALUES('LEGITIMATE TARGET UPDATE')"
            )
            target.commit()
        finally:
            target.close()
        self.assertEqual(self.run_pipeline(plan), first)
        self.assertEqual(
            resolve_private_storage(
                self.original, private_base=self.owned
            ).storage_root,
            selected.storage_root,
        )
        with self.assertRaises(sqlite3.IntegrityError):
            writer.execute(
                "INSERT INTO migration_wal_fixture VALUES('OLD SOURCE WRITE')"
            )
        writer.rollback()

    def test_completed_stage_and_midwrite_faults_resume_with_source_fenced(self):
        stages = [
            "source_fenced",
            "database_copy_progress",
            "database_copied",
            "files_flushed",
            "target_activated",
            "before_selector",
            "selector_published",
        ]
        prototype = self.create_source("prototype")
        copied_names = [name for name, _, _ in transfer._files(prototype)]
        stages += ["file_copy_progress:" + name for name in copied_names]
        for index, stage in enumerate(stages):
            with self.subTest(stage=stage):
                layout = self.create_source("case" + str(index))
                plan = plan_storage_migration(layout)
                # Generated key IDs differ between isolated sources.
                actual_stage = stage
                if stage.startswith("file_copy_progress:keys/"):
                    actual_stage = "file_copy_progress:" + next(
                        name for name, _, _ in plan.files if name.startswith("keys/")
                    )
                fired = []

                def interrupt(current):
                    if current == actual_stage and not fired:
                        fired.append(current)
                        raise InjectedCrash(current)

                with patch.object(transfer, "_fault", side_effect=interrupt):
                    with self.assertRaises(InjectedCrash):
                        self.run_pipeline(plan)
                self.assertTrue(fired)
                source = sqlite3.connect(layout.database_path)
                try:
                    self.assertEqual(
                        source.execute(
                            "SELECT state FROM private_storage_epoch"
                        ).fetchone(),
                        ("fenced",),
                    )
                    with self.assertRaises(sqlite3.IntegrityError):
                        source.execute(
                            "INSERT INTO config_state(principal_id,module_id,revision) VALUES('old','old/module',0)"
                        )
                    source.rollback()
                finally:
                    source.close()
                original = transfer._files(layout)
                resumed = plan_storage_migration(layout)
                self.assertEqual(resumed.operation_id, plan.operation_id)
                self.assertEqual(self.run_pipeline(resumed).state, "committed")
                self.assertEqual(transfer._files(layout), original)
                self.assertEqual(
                    resolve_private_storage(
                        self.original, private_base=self.owned
                    ).security_status,
                    "private",
                )
                journal = transfer._load_journal(resumed)
                if len(journal["attempts"]) > 1:
                    self.assertEqual(journal["attempts"][0]["state"], "abandoned")
                    self.assertTrue(
                        (
                            layout.instance_root / journal["attempts"][0]["storage"]
                        ).exists()
                    )

    def test_unknown_or_changed_partial_inventory_is_preserved_and_rejected(self):
        for index, change in enumerate(("unknown", "changed_identity")):
            layout = self.create_source("unknown-case" + str(index))
            plan = plan_storage_migration(layout)
            with patch.object(
                transfer,
                "_fault",
                side_effect=lambda stage: (_ for _ in ()).throw(InjectedCrash())
                if stage == "database_copied"
                else None,
            ):
                with self.assertRaises(InjectedCrash):
                    self.run_pipeline(plan)
            journal = transfer._load_journal(plan)
            root = layout.instance_root / journal["attempts"][0]["storage"]
            if change == "unknown":
                path = root / "unknown-user-bytes"
                path.write_bytes(b"PRESERVE UNKNOWN")
            else:
                path = root / "runtime.sqlite3"
                old = self.owned / ("preserved-original-" + str(index) + ".sqlite3")
                path.rename(old)
                path.write_bytes(b"PRESERVE CHANGED IDENTITY")
            before = path.read_bytes()
            with self.assertRaises(PrivateStorageError):
                self.run_pipeline(plan_storage_migration(layout))
            self.assertEqual(path.read_bytes(), before)

    def test_committed_pair_members_are_checked_on_receipt_and_cold_load(self):
        for index, change in enumerate(
            (
                "selector_target",
                "manifest_bytes",
                "manifest_pair",
                "target_journal",
                "target_payload",
                "target_epoch",
                "target_database",
                "target_database_identity",
                "source_epoch",
                "source_journal",
            )
        ):
            with self.subTest(change=change):
                layout = self.create_source("paired" + str(index))
                plan = plan_storage_migration(layout)
                self.run_pipeline(plan)
                selector = transfer._read_selector(
                    layout.instance_root, layout.data_root
                )
                root = layout.instance_root / selector["storage"]
                if change == "selector_target":
                    selector["storage"] = "storage-" + "0" * 32
                    publish_private_document(
                        layout.instance_root, "active.json", transfer._encode(selector)
                    )
                elif change.startswith("manifest"):
                    path = root / "recovery" / "migration.json"
                    value = json.loads(path.read_bytes())
                    value["source_identity"] = "0" * 64
                    encoded = transfer._encode(value)
                    path.write_bytes(encoded)
                    if change == "manifest_pair":
                        selector["manifest_digest"] = hashlib.sha256(
                            encoded
                        ).hexdigest()
                        publish_private_document(
                            layout.instance_root,
                            "active.json",
                            transfer._encode(selector),
                        )
                elif change == "target_payload":
                    next((root / "secrets").iterdir()).unlink()
                elif change in {"target_database", "target_database_identity"}:
                    (root / "runtime.sqlite3").rename(root / "preserved.sqlite3")
                    if change == "target_database_identity":
                        (root / "runtime.sqlite3").write_bytes(
                            (root / "preserved.sqlite3").read_bytes()
                        )
                elif change.startswith("source_"):
                    source = sqlite3.connect(layout.database_path)
                    try:
                        for (trigger,) in source.execute(
                            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'ygl_storage_fence_%'"
                        ).fetchall():
                            source.execute('DROP TRIGGER "' + trigger + '"')
                        if change == "source_epoch":
                            source.execute(
                                "UPDATE private_storage_epoch SET epoch=epoch+1"
                            )
                        else:
                            source.execute(
                                "UPDATE managed_master_key_operations SET state='abandoned' WHERE operation_id=?",
                                (plan.operation_id,),
                            )
                        source.commit()
                    finally:
                        source.close()
                else:
                    target = sqlite3.connect(root / "runtime.sqlite3")
                    try:
                        if change == "target_epoch":
                            target.execute(
                                "UPDATE private_storage_epoch SET epoch=epoch+1"
                            )
                        else:
                            target.execute(
                                "UPDATE managed_master_key_operations SET state='abandoned' WHERE operation_id=?",
                                (plan.operation_id,),
                            )
                        target.commit()
                    finally:
                        target.close()
                for check in (
                    lambda: self.run_pipeline(plan),
                    lambda: resolve_private_storage(
                        self.original, private_base=self.owned
                    ),
                ):
                    with self.assertRaises(PrivateStorageError) as error:
                        check()
                    self.assertIn(
                        error.exception.code,
                        {"ambiguous_storage", "private_storage_unavailable"},
                    )

    def test_public_authority_is_not_a_callback_and_second_process_sql_source_write_is_rejected(
        self,
    ):
        from ygl_test_subject.infrastructure.private_storage_migration import (
            execute_storage_migration,
        )

        layout = self.create_source()
        plan = plan_storage_migration(layout)
        with WindowsPrivateStorageGuard(layout.instance_root) as guard:
            with self.assertRaises(PrivateStorageError):
                execute_storage_migration(
                    plan, source_proof=lambda: True, private_guard=guard
                )
        self.run_pipeline(plan)
        script = "import sqlite3,sys\nc=sqlite3.connect(sys.argv[1])\ntry:\n c.execute(\"INSERT INTO config_state(principal_id,module_id,revision) VALUES('old','old/module',1)\")\nexcept sqlite3.IntegrityError as e:\n print(str(e));sys.exit(0)\nelse:\n sys.exit(1)\n"
        child = subprocess.run(
            [sys.executable, "-c", script, str(layout.database_path)],
            capture_output=True,
        )
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertIn(b"private_storage_source_fenced", child.stdout)
