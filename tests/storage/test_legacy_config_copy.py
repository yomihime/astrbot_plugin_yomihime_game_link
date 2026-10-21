"""Atomic ordinary-field migration, source retention, CAS and authority fences."""

import tempfile
import unittest
from pathlib import Path

from ygl_test_subject.core.ports import RevisionConflict
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories_config import (
    SQLiteConfigRepository,
)
from ygl_test_subject.services.configuration.legacy_copy import LegacyConfigCopyPolicy

from yomihime_game_link_sdk import ConfigField, ConfigTarget


class LegacyConfigCopyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ygl-legacy-copy-")
        self.db = SQLiteDatabase(Path(self.temp.name) / "config.sqlite3")
        self.repo = SQLiteConfigRepository(self.db)
        self.source = ConfigTarget("test", "game_link/core")
        self.target = ConfigTarget("test", "sample/module")
        self.field = ConfigField("destination", value_schema={"type": "string"})
        self.policy = LegacyConfigCopyPolicy(
            "sample.copy",
            self.source,
            "original",
            self.target.module_id,
            self.field.name,
            self.field.value_schema,
        )
        await self.db.executor.initialize()

    async def asyncTearDown(self):
        await self.db.executor.close()
        self.temp.cleanup()

    async def seed(self, target, field, raw):
        def write(unit):
            self.repo._ensure_state(unit, target)
            unit.execute(
                "INSERT INTO config_entries "
                "(principal_id,module_id,field,value_json,revision) VALUES(?,?,?,?,1)",
                (target.principal_id, target.module_id, field, raw),
            )

        await self.db.executor.run_transaction(write, begin_mode="IMMEDIATE")

    async def copy(self, fence=lambda unit: None, **revisions):
        return await self.repo.copy_legacy_if_absent(
            self.policy,
            source_revision=revisions.get("source", 1),
            target_revision=revisions.get("target", 1),
            declaration=self.field,
            fence=fence,
        )

    async def test_absent_pair_is_read_only_and_copy_keeps_source(self):
        pair = await self.repo.current_pair(self.source, self.target)
        self.assertEqual([snapshot.revision for snapshot in pair], [1, 1])
        self.assertEqual(
            await self.db.executor.run_read(
                lambda unit: unit.execute(
                    "SELECT COUNT(*) FROM config_state"
                ).fetchone()[0]
            ),
            0,
        )
        await self.seed(self.source, "original", '"preserved"')
        before = (await self.repo.current_pair(self.source, self.target))[0]
        self.assertEqual(await self.copy(), ("copied", 2, True))
        source, target = await self.repo.current_pair(self.source, self.target)
        self.assertEqual(source, before)
        self.assertEqual(target.values["destination"], "preserved")

    async def test_existing_destination_is_never_overwritten(self):
        await self.seed(self.source, "original", '"old"')
        await self.seed(self.target, "destination", '"user"')
        self.assertEqual(await self.copy(), ("already_configured", 1, False))
        self.assertEqual(
            (await self.repo.current_pair(self.source, self.target))[1].values[
                "destination"
            ],
            "user",
        )

    async def test_dual_revision_conflict_and_invalid_value_do_not_write(self):
        await self.seed(self.source, "original", "7")
        with self.assertRaises(RevisionConflict):
            await self.copy(source=2)
        with self.assertRaisesRegex(ValueError, "invalid_legacy_value"):
            await self.copy()
        self.assertEqual(
            (await self.repo.current_pair(self.source, self.target))[1].values, {}
        )

    async def test_authority_change_at_commit_rolls_back_target(self):
        await self.seed(self.source, "original", '"value"')
        calls = 0

        def fence(unit):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise RuntimeError("authority expired")

        with self.assertRaisesRegex(RuntimeError, "authority expired"):
            await self.copy(fence=fence)
        self.assertEqual(
            (await self.repo.current_pair(self.source, self.target))[1].values, {}
        )

    async def test_source_save_after_plan_rejects_stale_revision(self):
        await self.seed(self.source, "original", '"planned"')
        planned_source, planned_target = await self.repo.current_pair(
            self.source, self.target
        )

        def concurrent_save(unit):
            unit.execute(
                "UPDATE config_state SET revision=2 WHERE principal_id=? AND module_id=?",
                (self.source.principal_id, self.source.module_id),
            )
            unit.execute(
                "UPDATE config_entries SET value_json='\"changed\"',revision=2 WHERE principal_id=? AND module_id=?",
                (self.source.principal_id, self.source.module_id),
            )

        await self.db.executor.run_transaction(concurrent_save, begin_mode="IMMEDIATE")
        with self.assertRaises(RevisionConflict):
            await self.copy(
                source=planned_source.revision, target=planned_target.revision
            )
        source, target = await self.repo.current_pair(self.source, self.target)
        self.assertEqual(source.values["original"], "changed")
        self.assertEqual(target.values, {})
