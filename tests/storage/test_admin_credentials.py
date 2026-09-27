from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from secrets import token_urlsafe

from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories_admin_credentials import (
    AdminCredentialStateError,
    AdminCredentialStatus,
    SQLiteAdminCredentialRepository,
)
from ygl_test_subject.services.admin_authorization import _digest


class AdminCredentialRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "core.sqlite3"
        self.database = SQLiteDatabase(self.path)
        self.database.initialize()
        self.repository = SQLiteAdminCredentialRepository(self.database)

    def tearDown(self) -> None:
        self.temp.cleanup()

    async def test_seed_and_lifecycle_keep_only_digest(self) -> None:
        initial = await self.repository.current()
        self.assertEqual(initial.status, AdminCredentialStatus.UNINITIALIZED)
        self.assertEqual((initial.generation, initial.verifier_digest), (0, None))

        secret = token_urlsafe(32)
        active = await self.repository.bootstrap(_digest(secret))
        self.assertEqual(
            (active.status, active.generation), (AdminCredentialStatus.ACTIVE, 1)
        )
        self.assertEqual(len(active.verifier_digest or b""), 32)
        self.assertNotEqual(active.verifier_digest, secret.encode())
        self.assertNotIn(secret.encode(), self.path.read_bytes())

        rotated = await self.repository.rotate(1, _digest(token_urlsafe(32)))
        self.assertEqual(
            (rotated.status, rotated.generation), (AdminCredentialStatus.ACTIVE, 2)
        )
        revoked = await self.repository.revoke(2)
        self.assertEqual(
            (revoked.status, revoked.generation, revoked.verifier_digest),
            (AdminCredentialStatus.REVOKED, 3, None),
        )
        recovered = await self.repository.recover(3, _digest(token_urlsafe(32)))
        self.assertEqual(
            (recovered.status, recovered.generation), (AdminCredentialStatus.ACTIVE, 4)
        )
        connection = self.database.connect()
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE admin_credentials SET verifier_digest=NULL "
                    "WHERE singleton=1"
                )
        finally:
            connection.close()

    async def test_cross_state_and_stale_transitions_fail(self) -> None:
        with self.assertRaises(AdminCredentialStateError):
            await self.repository.revoke(0)
        await self.repository.bootstrap(_digest(token_urlsafe(32)))
        with self.assertRaises(AdminCredentialStateError):
            await self.repository.bootstrap(_digest(token_urlsafe(32)))
        with self.assertRaises(AdminCredentialStateError):
            await self.repository.rotate(0, _digest(token_urlsafe(32)))
        with self.assertRaises(AdminCredentialStateError):
            await self.repository.recover(1, _digest(token_urlsafe(32)))

    async def test_concurrent_bootstrap_has_one_winner(self) -> None:
        left = SQLiteAdminCredentialRepository(self.path)
        right = SQLiteAdminCredentialRepository(self.path)

        async def attempt(repo: SQLiteAdminCredentialRepository):
            try:
                return await repo.bootstrap(_digest(token_urlsafe(32)))
            except AdminCredentialStateError:
                return None

        outcomes = await asyncio.gather(attempt(left), attempt(right))
        self.assertEqual(sum(item is not None for item in outcomes), 1)
        self.assertEqual((await self.repository.current()).generation, 1)

    async def test_failed_transition_rolls_back_digest_and_generation(self) -> None:
        old = await self.repository.bootstrap(_digest(token_urlsafe(32)))
        connection = self.database.connect()
        try:
            connection.execute(
                "CREATE TRIGGER reject_admin_update BEFORE UPDATE ON admin_credentials "
                "BEGIN SELECT RAISE(ABORT, 'blocked'); END"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(AdminCredentialStateError):
            await self.repository.rotate(1, _digest(token_urlsafe(32)))
        current = await self.repository.current()
        self.assertEqual(current, old)

    async def test_missing_row_corrupt_row_and_missing_database_fail_closed(
        self,
    ) -> None:
        connection = self.database.connect()
        try:
            connection.execute("DELETE FROM admin_credentials")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(AdminCredentialStateError):
            await self.repository.current()

        corrupt_path = Path(self.temp.name) / "corrupt.sqlite3"
        corrupt_db = SQLiteDatabase(corrupt_path)
        corrupt_db.initialize()
        connection = corrupt_db.connect()
        try:
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute(
                "UPDATE admin_credentials SET state='ACTIVE', generation=0, "
                "verifier_digest=?",
                (b"short",),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(AdminCredentialStateError):
            await SQLiteAdminCredentialRepository(corrupt_db).current()

        missing = SQLiteAdminCredentialRepository(
            Path(self.temp.name) / "deleted.sqlite3"
        )
        with self.assertRaises(AdminCredentialStateError):
            await missing.current()
        with self.assertRaises(AdminCredentialStateError):
            await missing.bootstrap(_digest(token_urlsafe(32)))

        broken_path = Path(self.temp.name) / "broken.sqlite3"
        broken_path.write_bytes(b"not a sqlite database")
        with self.assertRaises(AdminCredentialStateError):
            await SQLiteAdminCredentialRepository(broken_path).current()


if __name__ == "__main__":
    unittest.main()
