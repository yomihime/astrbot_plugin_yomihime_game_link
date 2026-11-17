from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.core.contracts.services import CacheAccessRequest
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.ports import AuthorizationWindowExpired, RevisionConflict
from ygl_test_subject.infrastructure.files import LocalSafeFileStore
from ygl_test_subject.infrastructure.sqlite import repositories_cache_resources
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.migrations import (
    MigrationRunner,
    discover_migrations,
)
from ygl_test_subject.infrastructure.sqlite.repositories_cache_resources import (
    SQLiteCacheRepository,
    SQLiteResourceRepository,
    _scope_from_row,
)

from yomihime_game_link_sdk.storage import (
    CacheEntry,
    CacheLookupStatus,
    CacheVisibility,
    GrantReference,
    OwnerScope,
    ResourceMetadata,
)


class _SteppedDateTime(datetime):
    moments: list[datetime] = []

    @classmethod
    def now(cls, tz=None):
        if cls.moments:
            return cls.moments.pop(0)
        return datetime.now(tz)


class CacheResourceRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = SQLiteDatabase(self.root / "state.sqlite3")

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _request(
        key: str, visibility: CacheVisibility, scope: OwnerScope
    ) -> CacheAccessRequest:
        return CacheAccessRequest(key, visibility, scope)

    async def test_cache_scope_expiry_revision_and_reopen(self) -> None:
        repository = SQLiteCacheRepository(self.database)
        public = self._request("same", CacheVisibility.PUBLIC, OwnerScope.public())
        user_a = self._request("same", CacheVisibility.USER, OwnerScope.user("a"))
        user_b = self._request("same", CacheVisibility.USER, OwnerScope.user("b"))
        expiry = datetime.now(UTC) + timedelta(minutes=5)
        await repository.put(
            public,
            validate_contract(CacheEntry("same", {"value": 1}, expiry)),
            source_version=4,
        )
        await repository.put(
            user_a,
            validate_contract(CacheEntry("same", {"value": 2}, expiry)),
            source_version=5,
        )
        self.assertEqual(
            (await repository.get(public, minimum_source_version=4)).status,
            CacheLookupStatus.HIT,
        )
        self.assertEqual((await repository.get(user_b)).status, CacheLookupStatus.MISS)
        self.assertEqual(
            (await repository.get(user_a, minimum_source_version=6)).status,
            CacheLookupStatus.REJECTED,
        )
        updated = await repository.put(
            public,
            validate_contract(CacheEntry("same", {"value": 3}, expiry)),
            expected_revision=1,
            source_version=6,
        )
        self.assertEqual(updated.revision, 2)
        with self.assertRaises(RevisionConflict):
            await repository.put(
                public,
                validate_contract(CacheEntry("same", {"value": 4}, expiry)),
                expected_revision=1,
            )
        await repository.invalidate(public)
        self.assertEqual((await repository.get(public)).status, CacheLookupStatus.MISS)
        reopened = SQLiteCacheRepository(self.database)
        self.assertEqual((await reopened.get(user_a)).entry.payload["value"], 2)

    async def test_prune_legacy_expiry_is_bounded_and_recreation_rejects_stale_cas(
        self,
    ):
        migrations = discover_migrations(self.database.migrations_dir)
        connection = self.database.connect()
        try:
            MigrationRunner(
                connection, migrations=[m for m in migrations if m.version <= 240]
            ).apply()
            connection.executemany(
                "INSERT INTO cache_entries(cache_key,visibility,payload_json,expires_at,revision) "
                "VALUES (?,'public','{}','2000-01-01T00:00:00+00:00',?)",
                [(f"legacy-{i}", 37) for i in range(129)],
            )
            connection.execute(
                "INSERT INTO module_collections VALUES ('ff14/ff14','server_directory',1,'public','[]')"
            )
            connection.execute(
                "INSERT INTO module_records VALUES ('ff14/ff14','server_directory','public',"
                "'','',0,'universalis',1,'{}',41)"
            )
            connection.execute(
                "INSERT INTO b04_subscriptions(subscription_id,revision,owner_id,scope_kind,status,record_json) "
                "VALUES ('retained',9,'owner','public','active','{}')"
            )
            connection.commit()
        finally:
            connection.close()

        repository = SQLiteCacheRepository(self.database)
        request = self._request("legacy-0", CacheVisibility.PUBLIC, OwnerScope.public())
        self.assertEqual(
            (await repository.get(request)).status, CacheLookupStatus.EXPIRED
        )
        self.assertEqual(await repository.current_revision(request), 37)
        live = self._request("legacy-0", CacheVisibility.USER, OwnerScope.user("alice"))
        entry = CacheEntry(
            "legacy-0", {"live": True}, datetime.now(UTC) + timedelta(days=1)
        )
        live_entry = await repository.put(live, entry)
        self.assertEqual(await repository.prune_expired(), 128)
        self.assertEqual(await repository.prune_expired(), 1)
        with patch.object(
            self.database.executor,
            "run_transaction",
            side_effect=AssertionError("idle write"),
        ):
            self.assertEqual(await repository.prune_expired(), 0)
        self.assertEqual((await repository.get(live)).entry, live_entry)
        self.assertEqual((await repository.get(request)).status, CacheLookupStatus.MISS)
        with self.assertRaises(RevisionConflict):
            await repository.put(request, entry, expected_revision=37)

        reopened = SQLiteCacheRepository(self.database.path)
        try:
            recreated = await reopened.put(request, entry)
            self.assertGreater(recreated.revision, 37)
            with self.assertRaises(RevisionConflict):
                await reopened.put(request, entry, expected_revision=37)
            self.assertEqual((await reopened.get(request)).entry, recreated)
        finally:
            await reopened.database.executor.close()
        connection = self.database.connect()
        try:
            self.assertEqual(
                tuple(
                    connection.execute(
                        "SELECT record_key,revision FROM module_records WHERE collection='server_directory'"
                    ).fetchone()
                ),
                ("universalis", 41),
            )
            self.assertEqual(
                tuple(
                    connection.execute(
                        "SELECT subscription_id,revision,status FROM b04_subscriptions"
                    ).fetchone()
                ),
                ("retained", 9, "active"),
            )
        finally:
            connection.close()

    async def test_exhausted_revision_never_wraps_or_promotes_to_float(self):
        repository = SQLiteCacheRepository(self.database)
        request = self._request("last", CacheVisibility.PUBLIC, OwnerScope.public())
        maximum = (1 << 63) - 1
        expired = CacheEntry("last", {}, datetime.now(UTC) - timedelta(seconds=1))
        await repository.put(request, expired)
        await self.database.executor.run_transaction(
            lambda unit: unit.execute(
                "UPDATE cache_entries SET revision=?", (maximum,)
            ).rowcount
        )
        with self.assertRaisesRegex(OverflowError, "revision exhausted"):
            await repository.put(request, expired, expected_revision=maximum)
        await repository.invalidate(request)
        self.assertEqual(await repository.current_revision(request), maximum)
        self.assertEqual(await repository.prune_expired(), 1)
        # Exhausting the shared creation floor disables inserts, including for
        # new keys. Starting over at one would silently re-admit an old writer.
        with self.assertRaisesRegex(OverflowError, "revision exhausted"):
            await repository.put(request, expired)
        self.assertIsNone(await repository.current_revision(request))

    async def test_prune_rechecks_expiry_after_concurrent_renewal(self):
        repository = SQLiteCacheRepository(self.database)
        request = self._request("renew", CacheVisibility.PUBLIC, OwnerScope.public())
        now = datetime.now(UTC)
        stale = await repository.put(
            request, CacheEntry("renew", {}, now - timedelta(days=1))
        )
        other = SQLiteCacheRepository(self.database.path)
        write = self.database.executor.run_transaction

        async def after_renewal(prune, **kwargs):
            await other.put(
                request,
                CacheEntry("renew", {"renewed": True}, now + timedelta(days=1)),
                expected_revision=stale.revision,
            )
            return await write(prune, **kwargs)

        try:
            with patch.object(
                self.database.executor, "run_transaction", side_effect=after_renewal
            ):
                self.assertEqual(await repository.prune_expired(), 0)
            self.assertTrue((await repository.get(request)).entry.payload["renewed"])
        finally:
            await other.database.executor.close()

    async def test_cache_authorized_scope_is_grant_revision_bound(self) -> None:
        repository = SQLiteCacheRepository(self.database)
        first = self._request(
            "account",
            CacheVisibility.AUTHORIZED,
            OwnerScope.authorized("u1", validate_contract(GrantReference("g1", 1))),
        )
        second = self._request(
            "account",
            CacheVisibility.AUTHORIZED,
            OwnerScope.authorized("u1", validate_contract(GrantReference("g1", 2))),
        )
        expiry = datetime.now(UTC) + timedelta(minutes=5)
        await repository.put(
            first,
            validate_contract(CacheEntry("account", {"token": "metadata"}, expiry)),
        )
        self.assertEqual((await repository.get(first)).status, CacheLookupStatus.HIT)
        self.assertEqual(
            (await repository.get(second)).status, CacheLookupStatus.REJECTED
        )

    async def test_file_scope_isolation_and_orphan_recovery(self) -> None:
        files = LocalSafeFileStore(self.root / "assets")
        content = b"same bytes"
        stage_a = await files.stage("op-a", content, OwnerScope.user("a"))
        stage_b = await files.stage("op-b", content, OwnerScope.user("b"))
        self.assertNotEqual(stage_a.asset_id, stage_b.asset_id)
        metadata_a = validate_contract(
            ResourceMetadata(stage_a.asset_id, "image/png", stage_a.scope, len(content))
        )
        metadata_b = validate_contract(
            ResourceMetadata(stage_b.asset_id, "image/png", stage_b.scope, len(content))
        )
        await files.commit(stage_a, metadata_a)
        await files.commit(stage_b, metadata_b)
        self.assertEqual(await files.read(stage_a.asset_id, stage_a.scope), content)
        with self.assertRaises(FileNotFoundError):
            await files.read(stage_a.asset_id, OwnerScope.user("b"))
        abandoned = await files.stage("abandoned", b"orphan", OwnerScope.public())
        await files.mark_orphan(abandoned, "index failure")
        self.assertTrue(await files.recover_orphans())

    async def test_resource_index_requires_media_type_and_cas(self) -> None:
        files = LocalSafeFileStore(self.root / "assets")
        repository = SQLiteResourceRepository(self.database, files)
        stage = await files.stage("op", b"png", OwnerScope.public())
        metadata = validate_contract(
            ResourceMetadata(stage.asset_id, "image/png", stage.scope, 3)
        )
        await files.commit(stage, metadata)
        registered = await repository.register(metadata)
        self.assertEqual(registered.revision, 1)
        self.assertEqual(await files.recover_orphans(), ())
        self.assertEqual(await repository.read(stage.asset_id, stage.scope), b"png")
        with self.assertRaises(RevisionConflict):
            await repository.register(metadata, expected_revision=0)
        with self.assertRaises(ValueError):
            await repository.register(
                validate_contract(
                    ResourceMetadata(stage.asset_id, "unknown", OwnerScope.public(), 3)
                )
            )

        failed_stage = await files.stage(
            "index-failure", b"failed", OwnerScope.public()
        )
        failed_metadata = validate_contract(
            ResourceMetadata(
                failed_stage.asset_id, "image/png", failed_stage.scope, len(b"failed")
            )
        )
        await files.commit(failed_stage, failed_metadata)
        with self.assertRaises(ValueError):
            await repository.register(
                validate_contract(
                    ResourceMetadata(
                        failed_stage.asset_id,
                        "unknown",
                        failed_stage.scope,
                        len(b"failed"),
                    )
                )
            )
        self.assertTrue(await files.recover_orphans())
        await files.mark_orphan(failed_stage, "index failure")
        reopened_files = LocalSafeFileStore(self.root / "assets")
        self.assertTrue(await reopened_files.recover_orphans())
        self.assertEqual(
            await reopened_files.read(failed_stage.asset_id, failed_stage.scope),
            b"failed",
        )
        self.assertIsNone(
            await repository.metadata(failed_stage.asset_id, failed_stage.scope)
        )

        duplicate_stage = await files.stage("duplicate", b"png", OwnerScope.public())
        await files.commit(duplicate_stage, metadata)
        with self.assertRaises(RevisionConflict):
            await repository.register(metadata)
        await files.mark_orphan(duplicate_stage, "duplicate index")
        self.assertEqual(
            await files.recover_orphans(), await reopened_files.recover_orphans()
        )
        self.assertEqual(await repository.read(stage.asset_id, stage.scope), b"png")

    async def test_resource_user_and_authorized_scope_round_trip_and_restart(
        self,
    ) -> None:
        files = LocalSafeFileStore(self.root / "assets")
        repository = SQLiteResourceRepository(self.database, files)
        user_scope = OwnerScope.user("alice")
        user_stage = await files.stage("user-resource", b"alice", user_scope)
        user_metadata = validate_contract(
            ResourceMetadata(
                user_stage.asset_id, "image/png", user_scope, len(b"alice")
            )
        )
        await files.commit(user_stage, user_metadata)
        await repository.register(user_metadata)
        self.assertEqual(
            await repository.read(user_stage.asset_id, user_scope), b"alice"
        )
        with self.assertRaises(FileNotFoundError):
            await repository.read(user_stage.asset_id, OwnerScope.user("bob"))

        grant_one = OwnerScope.authorized(
            "alice", validate_contract(GrantReference("grant", 1))
        )
        authorized_stage = await files.stage("authorized-resource", b"grant", grant_one)
        authorized_metadata = validate_contract(
            ResourceMetadata(
                authorized_stage.asset_id, "image/png", grant_one, len(b"grant")
            )
        )
        await files.commit(authorized_stage, authorized_metadata)
        await repository.register(authorized_metadata)
        self.assertEqual(
            await repository.read(authorized_stage.asset_id, grant_one), b"grant"
        )
        with self.assertRaises(FileNotFoundError):
            await repository.read(
                authorized_stage.asset_id,
                OwnerScope.authorized(
                    "alice", validate_contract(GrantReference("grant", 2))
                ),
            )
        with self.assertRaises(FileNotFoundError):
            await repository.read(
                authorized_stage.asset_id,
                OwnerScope.authorized(
                    "bob", validate_contract(GrantReference("grant", 1))
                ),
            )

        reopened = SQLiteResourceRepository(
            self.database, LocalSafeFileStore(self.root / "assets")
        )
        self.assertEqual(await reopened.read(user_stage.asset_id, user_scope), b"alice")
        self.assertEqual(
            await reopened.read(authorized_stage.asset_id, grant_one), b"grant"
        )

    async def test_resource_malformed_scope_row_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _scope_from_row(
                {
                    "scope_kind": "user",
                    "user_id": "alice",
                    "grant_id": "unexpected",
                    "grant_revision": None,
                }
            )
        with self.assertRaises(ValueError):
            _scope_from_row(
                {
                    "scope_kind": "authorized",
                    "user_id": "alice",
                    "grant_id": None,
                    "grant_revision": 1,
                }
            )

    async def test_registration_state_keeps_expired_resource_index_visible(
        self,
    ) -> None:
        files = LocalSafeFileStore(self.root / "expired-index-assets")
        repository = SQLiteResourceRepository(self.database, files)
        scope = OwnerScope.authorized(
            "alice", validate_contract(GrantReference("expired-index", 1))
        )
        payload = b"expired-but-indexed"
        stage = await files.stage("expired-index", payload, scope)
        metadata = validate_contract(
            ResourceMetadata(
                stage.asset_id,
                "application/octet-stream",
                scope,
                len(payload),
                datetime.now(UTC) - timedelta(seconds=1),
            )
        )
        await files.commit(stage, metadata)
        registered = await repository.register(metadata)
        self.assertIsNone(await repository.metadata(stage.asset_id, scope))
        self.assertEqual(
            await repository.registration_state(stage.asset_id, scope), registered
        )

    async def test_concurrent_cache_replace_has_one_winner(self) -> None:
        repository = SQLiteCacheRepository(self.database)
        request = self._request("race", CacheVisibility.PUBLIC, OwnerScope.public())
        expiry = datetime.now(UTC) + timedelta(minutes=5)

        async def write(value: int):
            return await repository.put(
                request, validate_contract(CacheEntry("race", {"value": value}, expiry))
            )

        results = await asyncio.gather(write(1), write(2), return_exceptions=True)
        self.assertEqual(sum(isinstance(item, RevisionConflict) for item in results), 1)
        self.assertEqual(sum(not isinstance(item, Exception) for item in results), 1)

    async def test_repository_construction_is_lazy_and_sql_uses_worker(self) -> None:
        database = SQLiteDatabase(self.root / "lazy.sqlite3")
        file_store = LocalSafeFileStore(self.root / "lazy-assets")
        default_assets = self.root / "default-assets"
        caller_thread = threading.get_ident()
        connection_threads: list[int] = []
        original_connect = SQLiteDatabase._connect

        def track_connect(database: SQLiteDatabase):
            connection_threads.append(threading.get_ident())
            return original_connect(database)

        with patch.object(SQLiteDatabase, "_connect", track_connect):
            cache = SQLiteCacheRepository(database)
            resources = SQLiteResourceRepository(database, file_store)
            default_resources = SQLiteResourceRepository(database, root=default_assets)
            self.assertEqual(connection_threads, [])
            self.assertIsNone(database._executor)
            self.assertFalse(default_assets.exists())
            self.assertIs(resources.file_store, file_store)
            self.assertIsInstance(default_resources.file_store, LocalSafeFileStore)
            self.assertTrue(default_assets.is_dir())

            request = self._request(
                "worker", CacheVisibility.PUBLIC, OwnerScope.public()
            )
            entry = validate_contract(
                CacheEntry(
                    "worker", {"value": 1}, datetime.now(UTC) + timedelta(minutes=1)
                )
            )
            await cache.put(request, entry)
        self.assertTrue(connection_threads)
        self.assertNotIn(caller_thread, connection_threads)
        self.assertEqual(len(set(connection_threads)), 1)
        connection_threads.clear()
        with patch.object(SQLiteDatabase, "_connect", track_connect):
            self.assertIsNone(
                await resources.current_revision("missing", OwnerScope.public())
            )
        self.assertTrue(connection_threads)
        self.assertNotIn(caller_thread, connection_threads)
        self.assertEqual(len(set(connection_threads)), 1)

    async def test_authorization_deadline_expires_while_waiting_in_real_worker_queue(
        self,
    ) -> None:
        repository = SQLiteCacheRepository(self.database)
        request = self._request(
            "queued-private",
            CacheVisibility.AUTHORIZED,
            OwnerScope.authorized(
                "alice", validate_contract(GrantReference("grant-queue", 1))
            ),
        )
        await repository.current_revision(request)
        executor = self.database.executor
        worker_started = threading.Event()
        resume_worker = threading.Event()

        def block_worker(unit):
            worker_started.set()
            resume_worker.wait(timeout=3)

        blocker = asyncio.create_task(
            executor.run_transaction(block_worker, begin_mode="IMMEDIATE")
        )
        self.assertTrue(await asyncio.to_thread(worker_started.wait, 1))
        deadline = datetime.now(UTC) + timedelta(seconds=0.25)
        write = asyncio.create_task(
            repository.put(
                request,
                validate_contract(
                    CacheEntry("queued-private", {"secret": True}, deadline)
                ),
                authorization_deadline=deadline,
            )
        )
        for _ in range(100):
            if executor._queue.qsize() >= 1:
                break
            await asyncio.sleep(0.005)
        self.assertEqual(executor._queue.qsize(), 1)
        await asyncio.sleep(
            max(0, (deadline - datetime.now(UTC)).total_seconds()) + 0.02
        )
        resume_worker.set()
        await blocker
        with self.assertRaises(AuthorizationWindowExpired):
            await write
        self.assertIsNone(await repository.current_revision(request))

    async def test_authorization_deadline_expires_during_sqlite_immediate_lock_wait(
        self,
    ) -> None:
        repository = SQLiteCacheRepository(self.database)
        request = self._request(
            "locked-private",
            CacheVisibility.AUTHORIZED,
            OwnerScope.authorized(
                "alice", validate_contract(GrantReference("grant-lock", 1))
            ),
        )
        await repository.current_revision(request)
        locker = self.database.connect()
        locker.execute("BEGIN IMMEDIATE")
        try:
            deadline = datetime.now(UTC) + timedelta(seconds=0.25)
            write = asyncio.create_task(
                repository.put(
                    request,
                    validate_contract(
                        CacheEntry("locked-private", {"secret": True}, deadline)
                    ),
                    authorization_deadline=deadline,
                )
            )
            await asyncio.sleep(0.05)
            self.assertEqual(self.database.executor._outstanding, 1)
            await asyncio.sleep(
                max(0, (deadline - datetime.now(UTC)).total_seconds()) + 0.02
            )
            locker.rollback()
            with self.assertRaises(AuthorizationWindowExpired):
                await write
        finally:
            locker.close()
        self.assertIsNone(await repository.current_revision(request))

    async def test_final_deadline_check_rolls_back_cache_and_resource_mutations(
        self,
    ) -> None:
        deadline = _SteppedDateTime(2050, 1, 1, tzinfo=UTC)
        before = _SteppedDateTime(2049, 12, 31, tzinfo=UTC)
        after = _SteppedDateTime(2050, 1, 1, 0, 0, 1, tzinfo=UTC)
        grant_scope = OwnerScope.authorized(
            "alice", validate_contract(GrantReference("grant-step", 1))
        )
        cache_repository = SQLiteCacheRepository(self.database)
        cache_request = self._request(
            "rollback-cache", CacheVisibility.AUTHORIZED, grant_scope
        )
        old_entry = validate_contract(
            CacheEntry(
                "rollback-cache",
                {"value": "old"},
                datetime.now(UTC) + timedelta(minutes=5),
            )
        )
        await cache_repository.put(cache_request, old_entry)
        updated_entry = validate_contract(
            CacheEntry(
                "rollback-cache",
                {"value": "new"},
                datetime.now(UTC) + timedelta(minutes=5),
            )
        )
        _SteppedDateTime.moments = [before, after]
        with patch.object(repositories_cache_resources, "datetime", _SteppedDateTime):
            with self.assertRaises(AuthorizationWindowExpired):
                await cache_repository.put(
                    cache_request,
                    updated_entry,
                    expected_revision=1,
                    authorization_deadline=deadline,
                )
        self.assertEqual(
            (await cache_repository.get(cache_request)).entry.payload["value"], "old"
        )

        _SteppedDateTime.moments = [before, after]
        with patch.object(repositories_cache_resources, "datetime", _SteppedDateTime):
            with self.assertRaises(AuthorizationWindowExpired):
                await cache_repository.invalidate(
                    cache_request, authorization_deadline=deadline
                )
        self.assertEqual(
            (await cache_repository.get(cache_request)).status, CacheLookupStatus.HIT
        )

        files = LocalSafeFileStore(self.root / "assets-deadline")
        resource_repository = SQLiteResourceRepository(self.database, files)
        stage = await files.stage("deadline-base", b"base", grant_scope)
        original = validate_contract(
            ResourceMetadata(stage.asset_id, "image/png", grant_scope, 4)
        )
        await files.commit(stage, original)
        registered = await resource_repository.register(original)
        replacement = validate_contract(
            ResourceMetadata(
                registered.asset_id,
                "image/png",
                grant_scope,
                4,
                datetime.now(UTC) + timedelta(minutes=10),
                registered.temporary,
            )
        )
        _SteppedDateTime.moments = [before, after]
        with patch.object(repositories_cache_resources, "datetime", _SteppedDateTime):
            with self.assertRaises(AuthorizationWindowExpired):
                await resource_repository.register(
                    replacement,
                    expected_revision=registered.revision,
                    authorization_deadline=deadline,
                )
        self.assertEqual(
            await resource_repository.registration_state(stage.asset_id, grant_scope),
            registered,
        )

        _SteppedDateTime.moments = [before, after]
        with patch.object(repositories_cache_resources, "datetime", _SteppedDateTime):
            with self.assertRaises(AuthorizationWindowExpired):
                await resource_repository.delete(
                    stage.asset_id,
                    grant_scope,
                    authorization_deadline=deadline,
                )
        self.assertEqual(
            await resource_repository.registration_state(stage.asset_id, grant_scope),
            registered,
        )


if __name__ == "__main__":
    unittest.main()
