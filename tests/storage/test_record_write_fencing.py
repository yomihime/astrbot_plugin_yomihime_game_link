"""Real-worker regression coverage for record registration commit fences."""

from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories_config import (
    SQLiteRecordRepository,
)
from ygl_test_subject.services.module_services import RegistryRegistrationLookup

import tests.storage.test_config_records_repository as repository_fixture
from yomihime_game_link_sdk.storage import (
    CollectionDescriptor,
    OwnerScope,
    OwnershipKind,
)


async def _wait_for(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.001)


class RecordWriteFencingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = SQLiteDatabase(Path(self.temp.name) / "records.sqlite3")
        self.descriptor = CollectionDescriptor("records", 1, OwnershipKind.PUBLIC)

    async def asyncTearDown(self):
        await self.database.executor.close()
        self.temp.cleanup()

    def _repository(self):
        _, registry, _, registered = (
            repository_fixture.ConfigRecordsRepositoryTests._records(
                self.database, self.descriptor
            )
        )
        return (
            SQLiteRecordRepository(
                self.database, registered, RegistryRegistrationLookup(registry)
            ),
            registry,
        )

    async def _collection(self):
        repository, registry = self._repository()
        collection = await repository.collection(
            repository.module_id, self.descriptor, OwnerScope.public()
        )
        return collection, registry

    @staticmethod
    def _invalidate(registry, change):
        if change == "disable":
            registry.set_enabled("package/module-a", False)
        else:
            registry._bind_lifecycle_owner(object(), lambda _: False)
            registry._publish_lifecycle_projection(
                "package/module-a", enabled=True, epoch=1
            )

    async def _stored(self, key):
        return await self.database.executor.run_read(
            lambda unit: tuple(
                tuple(row)
                for row in unit.execute(
                    "SELECT payload_json,revision FROM module_records WHERE record_key=?",
                    (key,),
                ).fetchall()
            )
        )

    async def _blocked_worker(self):
        entered, release = threading.Event(), threading.Event()

        def block(unit):
            entered.set()
            if not release.wait(5):
                raise AssertionError("test did not release SQLite worker")

        task = asyncio.create_task(self.database.executor.run_read(block))
        await _wait_for(entered.is_set)
        return task, release

    async def _prepare_write(self, collection, method, key):
        if method != "create":
            await collection.create(key, {"value": "original"})
        if method == "delete":
            return lambda: collection.delete(key, expected_revision=1)
        if method == "replace":
            return lambda: collection.replace(
                key, {"value": "late"}, expected_revision=1
            )
        return lambda: collection.create(key, {"value": "late"})

    async def test_queued_create_replace_delete_reject_disable_and_epoch_change(self):
        for change in ("disable", "epoch"):
            for method in ("create", "replace", "delete"):
                with self.subTest(change=change, method=method):
                    collection, registry = await self._collection()
                    key = f"queued-{change}-{method}"
                    write = await self._prepare_write(collection, method, key)
                    before = await self._stored(key)
                    blocker, release = await self._blocked_worker()
                    task = asyncio.create_task(write())
                    try:
                        await _wait_for(
                            lambda: self.database.executor._queue.qsize() == 1
                        )
                        self._invalidate(registry, change)
                    finally:
                        release.set()
                    await blocker
                    with self.assertRaisesRegex(ValueError, "registration changed"):
                        await task
                    self.assertEqual(await self._stored(key), before)

    async def test_invalidation_after_sql_rolls_back_create_replace_delete(self):
        executor = self.database.executor
        original = executor.run_transaction
        for change in ("disable", "epoch"):
            for method in ("create", "replace", "delete"):
                with self.subTest(change=change, method=method):
                    collection, registry = await self._collection()
                    key = f"precommit-{change}-{method}"
                    write = await self._prepare_write(collection, method, key)
                    before = await self._stored(key)
                    entered, release = threading.Event(), threading.Event()

                    async def pause_after_sql(callback, **kwargs):
                        def paused(unit):
                            result = callback(unit)
                            entered.set()
                            if not release.wait(5):
                                raise AssertionError("test did not release commit")
                            return result

                        return await original(paused, **kwargs)

                    with patch.object(executor, "run_transaction", pause_after_sql):
                        task = asyncio.create_task(write())
                        try:
                            await _wait_for(entered.is_set)
                            self._invalidate(registry, change)
                        finally:
                            release.set()
                        with self.assertRaisesRegex(ValueError, "registration changed"):
                            await task
                    self.assertEqual(await self._stored(key), before)

    async def test_queued_collection_registration_does_not_write_after_disable(self):
        repository, registry = self._repository()
        blocker, release = await self._blocked_worker()
        task = asyncio.create_task(
            repository.collection(
                repository.module_id, self.descriptor, OwnerScope.public()
            )
        )
        try:
            await _wait_for(lambda: self.database.executor._queue.qsize() == 1)
            registry.set_enabled(repository.module_id, False)
        finally:
            release.set()
        await blocker
        with self.assertRaisesRegex(ValueError, "registration changed"):
            await task
        self.assertEqual(
            await self.database.executor.run_read(
                lambda unit: unit.execute(
                    "SELECT COUNT(*) FROM module_collections"
                ).fetchone()[0]
            ),
            0,
        )

    async def test_cancelled_queued_write_drains_then_rejects_new_epoch(self):
        collection, registry = await self._collection()
        blocker, release = await self._blocked_worker()
        task = asyncio.create_task(collection.create("cancelled", {"value": "late"}))
        try:
            await _wait_for(lambda: self.database.executor._queue.qsize() == 1)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            self._invalidate(registry, "epoch")
        finally:
            release.set()
        await blocker
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(await self._stored("cancelled"), ())
        self.assertEqual(self.database.executor._outstanding, 0)

    async def test_registry_publication_waits_for_actual_guarded_commit(self):
        collection, registry = await self._collection()
        entered, release, published, attempted = (threading.Event() for _ in range(4))
        original = self.database.executor.run_transaction

        @contextmanager
        def pause_commit():
            # This is entered after the registration guard, inside commit_sync.
            entered.set()
            if not release.wait(5):
                raise AssertionError("test did not release guarded commit")
            yield

        async def guarded(callback, **kwargs):
            def wrapped(unit):
                result = callback(unit)
                unit.add_commit_guard(pause_commit)
                return result

            return await original(wrapped, **kwargs)

        def disable():
            attempted.set()
            registry.set_enabled(collection.module_id, False)
            published.set()

        with patch.object(self.database.executor, "run_transaction", guarded):
            task = asyncio.create_task(collection.create("linearized", {"value": 1}))
            await _wait_for(entered.is_set)
            publication = asyncio.create_task(asyncio.to_thread(disable))
            try:
                await _wait_for(attempted.is_set)
                self.assertFalse(published.is_set())
                self.assertTrue(
                    registry.snapshot().module(collection.module_id).enabled
                )
            finally:
                release.set()
            await task
            await publication
        self.assertTrue(published.is_set())
        self.assertEqual(await self._stored("linearized"), (('{"value":1}', 1),))

    async def test_unrelated_registry_publication_keeps_issued_collection_valid(self):
        collection, registry = await self._collection()
        with registry._snapshot_guard() as snapshot:
            registry._publish_snapshot(
                replace(snapshot, revision=snapshot.revision + 1)
            )
        self.assertEqual((await collection.create("current", {"value": 1})).revision, 1)


if __name__ == "__main__":
    unittest.main()
