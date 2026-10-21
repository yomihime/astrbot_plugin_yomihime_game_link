"""A stale runtime cannot write any table after source migration or epoch change."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from ygl_test_subject.infrastructure.sqlite.database import (
    SQLiteDatabase,
    StorageEpochChangedError,
)


class StorageEpochFenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ygl-epoch-")
        self.db = SQLiteDatabase(Path(self.temp.name) / "db.sqlite3")
        await self.db.executor.initialize()
        await self.db.executor.run_transaction(
            lambda unit: unit.execute("CREATE TABLE synthetic(value TEXT)").rowcount
        )

    async def asyncTearDown(self):
        await self.db.executor.close()
        self.temp.cleanup()

    def fence_external(self, *, epoch=2, state="fenced"):
        with closing(sqlite3.connect(self.db.path)) as connection:
            connection.execute(
                "UPDATE private_storage_epoch SET epoch=?,state=?,operation_id='synthetic' WHERE singleton=1",
                (epoch, state),
            )
            connection.commit()

    async def test_all_write_and_read_worker_paths_and_restart_reject_fenced_source(
        self,
    ):
        self.fence_external()
        for submit in (self.db.executor.run_transaction, self.db.executor.run_read):
            with self.assertRaises(StorageEpochChangedError):
                await submit(
                    lambda unit: unit.execute(
                        "INSERT INTO synthetic VALUES('must not commit')"
                    ).rowcount
                )
        cold = SQLiteDatabase(self.db.path)
        with self.assertRaises(StorageEpochChangedError):
            cold.initialize()
        with closing(sqlite3.connect(self.db.path)) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM synthetic").fetchone()[0], 0
            )

    async def test_stale_epoch_denies_even_when_new_state_is_active(self):
        self.fence_external(state="activated")
        with self.assertRaises(StorageEpochChangedError):
            await self.db.executor.run_transaction(
                lambda unit: unit.execute(
                    "INSERT INTO synthetic VALUES('stale')"
                ).rowcount
            )

    async def test_commit_fence_rolls_back_callback_that_changes_own_epoch(self):
        def attempt(unit):
            unit.execute("INSERT INTO synthetic VALUES('rollback')")
            unit.connection.execute(
                "UPDATE private_storage_epoch SET epoch=epoch+1 WHERE singleton=1"
            )

        with self.assertRaises(StorageEpochChangedError):
            await self.db.executor.run_transaction(attempt)
        with closing(sqlite3.connect(self.db.path)) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM synthetic").fetchone()[0], 0
            )
            self.assertEqual(
                connection.execute(
                    "SELECT epoch FROM private_storage_epoch"
                ).fetchone()[0],
                1,
            )

    async def test_synchronous_migration_connection_also_fences_actual_commit(self):
        with closing(self.db.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT INTO synthetic VALUES('startup rollback')")
            connection.execute(
                "UPDATE private_storage_epoch SET epoch=epoch+1 WHERE singleton=1"
            )
            with self.assertRaises(StorageEpochChangedError):
                connection.commit()
            connection.rollback()
        with closing(sqlite3.connect(self.db.path)) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM synthetic").fetchone()[0], 0
            )
            self.assertEqual(
                connection.execute(
                    "SELECT epoch FROM private_storage_epoch"
                ).fetchone()[0],
                1,
            )
