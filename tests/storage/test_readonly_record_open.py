"""Read-only OWNER dependency records use real SQLite without catalog writes."""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories_config import (
    SQLiteRecordRepository,
)
from ygl_test_subject.services.module_services import RegistryRegistrationLookup

from tests.storage import test_config_records_repository as repository_fixture
from yomihime_game_link_sdk.errors import AccessDenied, RevisionConflict
from yomihime_game_link_sdk.storage import (
    CollectionDescriptor,
    CollectionIndex,
    DeclaredIndexQuery,
    OwnerScope,
    OwnershipKind,
    QueryOperator,
)


class ReadOnlyRecordOpenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = SQLiteDatabase(Path(self.temporary.name) / "records.sqlite3")
        self.addAsyncCleanup(self.database.executor.close)
        await self.database.executor.initialize()
        self.descriptor = CollectionDescriptor(
            "binding",
            1,
            OwnershipKind.USER,
            (CollectionIndex("by-name", "name"),),
        )
        _, self.registry, _, self.registered = (
            repository_fixture.ConfigRecordsRepositoryTests._records(
                self.database, self.descriptor
            )
        )
        self.lookup = RegistryRegistrationLookup(self.registry)
        self.writer = SQLiteRecordRepository(
            self.database, self.registered, self.lookup
        )
        self.owner = OwnerScope.user("principal-a")

    def _reader(self, **kwargs):
        return SQLiteRecordRepository(
            self.database, self.registered, self.lookup, read_only=True, **kwargs
        )

    async def _open(self, repository, owner=None):
        return await repository.collection(
            self.registered.module_id, self.descriptor, owner or self.owner
        )

    async def _snapshot(self):
        def read(unit):
            return tuple(
                tuple(tuple(row) for row in unit.execute(sql).fetchall())
                for sql in (
                    "SELECT * FROM module_collections ORDER BY module_id,collection",
                    "SELECT * FROM module_records ORDER BY module_id,collection,owner_user_id,record_key",
                )
            )

        return await self.database.executor.run_read(read)

    @contextmanager
    def _sqlite_readonly(self):
        """An actual SQLite authorizer rejects writes, rather than mocking SQL."""
        connect = self.database.connect
        trace = []
        denied = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}

        def open_connection():
            connection = connect()
            connection.set_authorizer(
                lambda action, *_: sqlite3.SQLITE_DENY
                if action in denied
                else sqlite3.SQLITE_OK
            )
            connection.set_trace_callback(trace.append)
            return connection

        with patch.object(self.database, "connect", open_connection):
            yield trace

    async def test_absent_catalog_open_get_query_are_empty_without_any_sql_writes(self):
        calls = []

        def deny_write():
            calls.append(threading.get_ident())
            raise AccessDenied("source_revoked")

        reader = self.writer.with_write_check(deny_write, read_only=True)
        before = await self._snapshot()
        with self._sqlite_readonly() as trace:
            collection = await self._open(reader)
            self.assertIsNone(await collection.get("default"))
            page = await collection.query(
                DeclaredIndexQuery("by-name", QueryOperator.EQUALS, "alice")
            )
            self.assertEqual(page.records, ())
        self.assertFalse(calls)
        self.assertFalse(
            any(sql.upper().startswith("BEGIN IMMEDIATE") for sql in trace)
        )
        self.assertEqual(await self._snapshot(), before)

    async def test_existing_records_are_readable_and_owner_isolated(self):
        writer = await self._open(self.writer)
        await writer.create("default", {"name": "alice"})
        other = await self._open(self.writer, OwnerScope.user("principal-b"))
        await other.create("default", {"name": "bob"})
        before = await self._snapshot()
        with self._sqlite_readonly():
            reader = await self._open(self._reader())
            self.assertEqual((await reader.get("default")).value["name"], "alice")
            page = await reader.query(
                DeclaredIndexQuery("by-name", QueryOperator.PREFIX, "a")
            )
            self.assertEqual(
                [record.value["name"] for record in page.records], ["alice"]
            )
        self.assertEqual(await self._snapshot(), before)

    async def test_mutations_use_worker_write_check_and_readonly_fallback(self):
        writer = await self._open(self.writer)
        await writer.create("default", {"name": "alice"})
        calls = []

        def deny_write():
            calls.append(threading.get_ident())
            raise AccessDenied("source_revoked")

        before = await self._snapshot()
        for repository in (self._reader(), self._reader(write_check=deny_write)):
            collection = await self._open(repository)
            for method in (
                lambda: collection.create("new", {"name": "new"}),
                lambda: collection.replace(
                    "default", {"name": "changed"}, expected_revision=1
                ),
                lambda: collection.delete("default", expected_revision=1),
            ):
                with self.assertRaises(AccessDenied):
                    await method()
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(identity != threading.get_ident() for identity in calls))
        self.assertEqual(await self._snapshot(), before)

    async def test_rebinding_cannot_drop_readonly_and_writer_cas_stays_intact(self):
        writer = await self._open(self.writer)
        await writer.create("default", {"name": "one"})
        reader = await self._open(self._reader().with_write_check(lambda: None))
        self.assertEqual(
            (
                await writer.replace("default", {"name": "two"}, expected_revision=1)
            ).revision,
            2,
        )
        with self.assertRaises(RevisionConflict):
            await writer.replace("default", {"name": "stale"}, expected_revision=1)
        self.assertEqual((await reader.get("default")).revision, 2)
        with self.assertRaises(AccessDenied):
            await reader.replace("default", {"name": "late"}, expected_revision=2)
        self.assertEqual((await writer.get("default")).value["name"], "two")

    async def test_stored_schema_owner_indexes_and_invalid_metadata_are_rejected(self):
        await self._open(self.writer)
        original = await self._snapshot()
        for field, value in (
            ("schema_version", 2),
            ("owner_kind", "public"),
            ("indexes_json", '{"by-name":"other"}'),
            ("indexes_json", "invalid json"),
        ):
            with self.subTest(field=field, value=value):
                await self.database.executor.run_transaction(
                    lambda unit: unit.execute(
                        f"UPDATE module_collections SET {field}=?", (value,)
                    ).rowcount
                )
                before = await self._snapshot()
                with (
                    self._sqlite_readonly(),
                    self.assertRaisesRegex(ValueError, "declaration"),
                ):
                    await self._open(self._reader())
                self.assertEqual(await self._snapshot(), before)
                await self.database.executor.run_transaction(
                    lambda unit: unit.execute(
                        "UPDATE module_collections SET schema_version=1,owner_kind='user',indexes_json=?",
                        (original[0][0][4],),
                    ).rowcount
                )

    async def test_missing_catalog_with_orphan_records_fails_closed(self):
        writer = await self._open(self.writer)
        await writer.create("default", {"name": "orphan"})
        connection = self.database.connect()
        try:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("DELETE FROM module_collections")
            connection.commit()
        finally:
            connection.close()
        before = await self._snapshot()
        with (
            self._sqlite_readonly(),
            self.assertRaisesRegex(ValueError, "catalog is missing"),
        ):
            await self._open(self._reader())
        self.assertEqual(await self._snapshot(), before)

    async def test_queued_readonly_open_and_read_reject_changed_epoch_in_worker(self):
        writer = await self._open(self.writer)
        await writer.create("default", {"name": "alice"})
        reader = await self._open(self._reader())
        for method in (
            lambda: self._open(self._reader()),
            lambda: reader.get("default"),
        ):
            entered, release = threading.Event(), threading.Event()

            def block(unit):
                entered.set()
                if not release.wait(3):
                    raise AssertionError("SQLite worker was not released")

            blocker = asyncio.create_task(self.database.executor.run_read(block))
            async with asyncio.timeout(3):
                while not entered.is_set():
                    await asyncio.sleep(0)
            operation = asyncio.create_task(method())
            try:
                async with asyncio.timeout(3):
                    while self.database.executor._queue.qsize() != 1:
                        await asyncio.sleep(0)
                if self.registry._lifecycle_owner is None:
                    self.registry._bind_lifecycle_owner(object(), lambda _: False)
                current = self.registry.snapshot().module(self.registered.module_id)
                self.registry._publish_lifecycle_projection(
                    self.registered.module_id, enabled=True, epoch=current.epoch + 1
                )
            finally:
                release.set()
            await blocker
            with self.assertRaisesRegex(ValueError, "registration changed"):
                await operation
            self.registered = self.registry.snapshot().module(self.registered.module_id)
            reader = await self._open(self._reader())


if __name__ == "__main__":
    unittest.main()
