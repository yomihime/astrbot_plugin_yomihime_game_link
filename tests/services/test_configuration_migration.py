from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.api.services import ConfigTarget
from ygl_test_subject.core.ports import RevisionConflict
from ygl_test_subject.infrastructure.sqlite.database import (
    SQLiteDatabase,
    SQLiteUnitOfWork,
)
from ygl_test_subject.infrastructure.sqlite.repositories_config import (
    SQLiteConfigRepository,
)
from ygl_test_subject.infrastructure.sqlite.repositories_config_migration import (
    SQLiteOrdinaryConfigurationMigrationRepository,
)
from ygl_test_subject.modules.ff14.config import DAYS, DELIVERY_TIME, REGION, TIMEZONE
from ygl_test_subject.services.configuration import ConfigurationValueError
from ygl_test_subject.services.configuration_migration import (
    OrdinaryConfigurationMigration,
)
from ygl_test_subject.services.core_configuration import (
    CoreDefaultsView,
    core_config_target,
)

from tests.host.assembly_contract import ordinary_migration_fields


class MigrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.db = SQLiteDatabase(Path(self.temp.name) / "config.sqlite3")
        await self.db.executor.initialize()
        self.config = SQLiteConfigRepository(self.db)
        self.repo = SQLiteOrdinaryConfigurationMigrationRepository(self.config)
        self.migration = OrdinaryConfigurationMigration(
            self.repo,
            ordinary_migration_fields("host"),
            migration_id="ff14-core-defaults-v1",
        )
        self.core = core_config_target("host")
        self.ff14 = ConfigTarget("host", "ff14/ff14")

    async def asyncTearDown(self):
        await self.db.executor.close()
        self.temp.cleanup()

    async def seed(self, target, values):
        old = await self.config.current(target)

        def write(unit):
            revision = old.revision + 1
            unit.execute(
                "UPDATE config_state SET revision=? WHERE principal_id=? AND module_id=?",
                (revision, target.principal_id, target.module_id),
            )
            for field, value in values.items():
                self.repo._write(unit, target, field, value, revision)

        await self.db.executor.run_transaction(write)

    async def snapshots(self):
        return {
            target: await self.config.current(target)
            for target in (self.core, self.ff14)
        }

    async def test_combinations_raw_presence_new_valid_priority_safe_diagnostics(self):
        await self.seed(self.core, {"default_region": "global"})
        await self.seed(self.ff14, {DAYS: 3, "unrelated": "kept"})
        raw = {REGION: "cn", DAYS: None, TIMEZONE: "UTC"}
        diagnostics = await self.migration.migrate(raw)
        self.assertEqual(
            [d["origin"] for d in diagnostics], ["new", "new", "legacy", "default"]
        )
        self.assertTrue(diagnostics[0]["conflict"])
        self.assertNotIn("value", diagnostics[0])
        snapshots = await self.snapshots()
        self.assertEqual(snapshots[self.core].values["default_region"], "global")
        self.assertEqual(snapshots[self.ff14].values[DAYS], 3)
        self.assertEqual(snapshots[self.ff14].values[TIMEZONE], "UTC")
        self.assertEqual(snapshots[self.ff14].values[DELIVERY_TIME], "08:00")
        self.assertEqual(snapshots[self.ff14].values["unrelated"], "kept")
        self.assertEqual(raw, {REGION: "cn", DAYS: None, TIMEZONE: "UTC"})

    async def test_invalid_new_or_legacy_rejects_entire_batch(self):
        for raw in (
            {REGION: None},
            {TIMEZONE: "private-zone"},
            {DELIVERY_TIME: "24:00"},
            {DAYS: True},
        ):
            with self.subTest(field=next(iter(raw))):
                before = await self.snapshots()
                with self.assertRaises(ConfigurationValueError):
                    await self.migration.migrate(raw)
                self.assertEqual(await self.snapshots(), before)
                self.assertFalse(await self.migration.complete())
        await self.seed(self.core, {"default_region": None})
        with self.assertRaises(ConfigurationValueError):
            await self.migration.migrate({REGION: "global"})
        self.assertFalse(await self.migration.complete())

    async def test_missing_raw_source_is_not_hydrated_default(self):
        with self.assertRaisesRegex(ValueError, "source is unavailable"):
            await self.migration.migrate(None)
        self.assertFalse(await self.migration.complete())

    async def test_marker_failure_rolls_back_all_fields_and_revisions(self):
        before = await self.snapshots()
        await self.db.executor.run_transaction(
            lambda unit: unit.execute(
                "CREATE TRIGGER fail_marker BEFORE INSERT ON ordinary_config_migrations "
                "BEGIN SELECT RAISE(ABORT, 'marker failure'); END"
            ).rowcount
        )
        with self.assertRaises(Exception):
            await self.migration.migrate({REGION: "global"})
        self.assertEqual(await self.snapshots(), before)
        self.assertFalse(await self.migration.complete())
        await self.db.executor.run_transaction(
            lambda unit: unit.execute("DROP TRIGGER fail_marker").rowcount
        )
        await self.migration.migrate({REGION: "global"})
        self.assertTrue(await self.migration.complete())

    async def test_commit_interruption_cannot_publish_completion(self):
        before = await self.snapshots()
        original = SQLiteUnitOfWork.commit_sync

        def interrupted(unit):
            if (
                unit.execute("SELECT 1 FROM ordinary_config_migrations").fetchone()
                is not None
            ):
                raise KeyboardInterrupt("before migration commit")
            original(unit)

        with patch.object(SQLiteUnitOfWork, "commit_sync", interrupted):
            with self.assertRaises(KeyboardInterrupt):
                await self.migration.migrate({})
        self.assertEqual(await self.snapshots(), before)
        self.assertFalse(await self.migration.complete())

    async def test_caller_cancel_drains_atomic_commit_and_restart_resolves_marker(self):
        entered, release = threading.Event(), threading.Event()
        original = self.repo._write

        def blocked(unit, target, field, value, revision):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test transaction was not released")
            original(unit, target, field, value, revision)

        with patch.object(self.repo, "_write", blocked):
            task = asyncio.create_task(self.migration.migrate({REGION: "global"}))
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            task.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        # The caller's outcome is unknown until inspecting the durable marker.
        self.assertTrue(await self.migration.complete())
        self.assertEqual(
            (await self.config.current(self.core)).values["default_region"], "global"
        )
        self.assertEqual(
            set((await self.config.current(self.ff14)).values),
            {DAYS, TIMEZONE, DELIVERY_TIME},
        )
        self.assertEqual(await self.migration.migrate(None), ())

    async def test_each_target_cas_conflict_no_partial_write(self):
        original = self.repo.apply
        for target in (self.core, self.ff14):

            async def concurrent(migration, snapshots, selected, legacy):
                await self.seed(target, {"unrelated": "concurrent"})
                return await original(migration, snapshots, selected, legacy)

            with patch.object(self.repo, "apply", concurrent):
                with self.assertRaises(RevisionConflict):
                    await self.migration.migrate({})
            self.assertFalse(await self.migration.complete())
            self.assertNotIn(DAYS, (await self.config.current(self.ff14)).values)

    async def test_restart_idempotent_and_clear_never_revives_legacy(self):
        await self.migration.migrate({REGION: "global", DAYS: 3})
        before = await self.snapshots()
        self.assertEqual(await self.migration.migrate(None), ())
        self.assertEqual(await self.snapshots(), before)
        await self.db.executor.run_transaction(
            lambda unit: unit.execute(
                "DELETE FROM config_entries WHERE module_id=? AND field=?",
                (self.core.module_id, "default_region"),
            ).rowcount
        )
        migration = OrdinaryConfigurationMigration(
            self.repo,
            ordinary_migration_fields("host"),
            migration_id=self.migration.migration_id,
        )
        self.assertEqual(await migration.migrate({REGION: "global"}), ())
        config = type(
            "Config", (), {"current": lambda _: self.config.current(self.core)}
        )()
        view = CoreDefaultsView(
            config,
            legacy_defaults={"default_region": "global"},
            migration_complete=migration.complete,
        )
        self.assertEqual((await view.current()).values["default_region"], "cn")

    async def test_corrupt_marker_fails_closed(self):
        await self.migration.migrate({})
        await self.db.executor.run_transaction(
            lambda unit: unit.execute(
                "UPDATE ordinary_config_migrations SET snapshot_json=?",
                (json.dumps({}),),
            ).rowcount
        )
        with self.assertRaisesRegex(ValueError, "marker"):
            await self.migration.migrate({REGION: "global"})

    async def test_marker_version_target_and_snapshot_binding_reject_tampering(self):
        await self.migration.migrate({})
        original = await self.db.executor.run_read(
            lambda unit: unit.execute(
                "SELECT snapshot_json FROM ordinary_config_migrations"
            ).fetchone()[0]
        )
        variants = []
        wrong_target = json.loads(original)
        wrong_target["fields"][0]["module_id"] = "other/core"
        variants.append(wrong_target)
        wrong_snapshot = json.loads(original)
        wrong_snapshot["fields"][0]["after"]["value"] = None
        variants.append(wrong_snapshot)
        for payload in variants:
            await self.db.executor.run_transaction(
                lambda unit: unit.execute(
                    "UPDATE ordinary_config_migrations SET snapshot_json=?",
                    (json.dumps(payload),),
                ).rowcount
            )
            with self.assertRaises(ValueError):
                await self.migration.complete()
        await self.db.executor.run_transaction(
            lambda unit: unit.execute(
                "UPDATE ordinary_config_migrations SET version=2,snapshot_json=?",
                (original,),
            ).rowcount
        )
        with self.assertRaisesRegex(ValueError, "marker"):
            await self.migration.migrate(None)

    async def test_rollback_preserves_later_same_value_admin_write(self):
        await self.migration.migrate({})
        await self.seed(self.ff14, {DAYS: 7})
        current = await self.snapshots()
        with self.assertRaisesRegex(ValueError, "changed"):
            await self.migration.rollback({t: s.revision for t, s in current.items()})
        self.assertEqual(await self.snapshots(), current)

    async def test_rollback_only_four_fields_marker_preserves_later_unrelated(self):
        await self.seed(self.ff14, {DAYS: 5})
        await self.migration.migrate({REGION: "global", TIMEZONE: "UTC"})
        await self.seed(
            self.ff14, {"unrelated": "later", "subscriptions_enabled": False}
        )
        current = await self.snapshots()
        legacy = await self.migration.rollback(
            {t: s.revision for t, s in current.items()}
        )
        self.assertEqual(legacy, {REGION: "global", TIMEZONE: "UTC"})
        restored = await self.snapshots()
        self.assertEqual(restored[self.core].values, {})
        self.assertEqual(
            restored[self.ff14].values,
            {DAYS: 5, "unrelated": "later", "subscriptions_enabled": False},
        )
        self.assertFalse(await self.migration.complete())

    async def test_rollback_rejects_changed_field_or_stale_revision_atomically(self):
        await self.migration.migrate({})
        current = await self.snapshots()
        await self.seed(self.ff14, {DAYS: 9})
        with self.assertRaises(RevisionConflict):
            await self.migration.rollback({t: s.revision for t, s in current.items()})
        current = await self.snapshots()
        with self.assertRaisesRegex(ValueError, "changed"):
            await self.migration.rollback({t: s.revision for t, s in current.items()})
        self.assertTrue(await self.migration.complete())
        self.assertEqual(await self.snapshots(), current)
