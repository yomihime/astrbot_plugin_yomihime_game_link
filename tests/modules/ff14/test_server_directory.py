"""Persistence, target classification, cooldown, and generation fences."""

import asyncio
import copy
import unittest
from datetime import timedelta
from types import SimpleNamespace

from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.modules.ff14.config import SERVER_DIRECTORY_GENERATION
from ygl_test_subject.modules.ff14.features.fflogs_server import resolve_realm
from ygl_test_subject.modules.ff14.features.market_sources import (
    DCS_PATH,
    WORLDS_PATH,
    MarketDeadlineError,
    MarketPayloadError,
    MarketSourceClient,
)
from ygl_test_subject.modules.ff14.features.server_directory import (
    COLLECTION,
    KEY,
    UNKNOWN_COOLDOWN,
    ServerDirectory,
)

from tests.modules.ff14.test_market import DCS, NOW, REGION_IDS, WORLDS, FakeHttp
from yomihime_game_link_sdk.errors import (
    AccessDenied,
    RevisionConflict,
    ServiceUnavailable,
    SourceHttpError,
    UniqueConstraintViolation,
)
from yomihime_game_link_sdk.storage import VersionedRecord


class MemoryRecords:
    def __init__(self):
        self.record = None
        self.opened = 0

    async def collection(self, name):
        if name != COLLECTION:
            raise AssertionError(name)
        self.opened += 1
        return self

    async def get(self, key):
        if key != KEY:
            raise AssertionError(key)
        return self.record

    async def create(self, key, value):
        if self.record is not None:
            raise UniqueConstraintViolation("module_record")
        self.record = validate_contract(VersionedRecord(key, 1, value))
        return self.record

    async def replace(self, key, value, *, expected_revision):
        if expected_revision != self.record.revision:
            raise RevisionConflict(
                "module_record", expected_revision, self.record.revision
            )
        self.record = validate_contract(
            VersionedRecord(key, expected_revision + 1, value)
        )
        return self.record


class ServerDirectoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = NOW
        self.values = {}
        self.revision = 1
        self.records = MemoryRecords()
        self.directory = ServerDirectory(self)
        self.worlds = copy.deepcopy(WORLDS)
        self.dcs = copy.deepcopy(DCS)
        self.http = FakeHttp(
            lambda request: self.worlds if request.path == WORLDS_PATH else self.dcs
        )
        self.clock = 0.0
        self.source = MarketSourceClient(
            None, clock=lambda: self.clock, wall_clock=lambda: self.now
        )

    async def current(self):
        return SimpleNamespace(revision=self.revision, values=dict(self.values))

    def session(self, *, directory=None, http=None, records=None, deadline=None):
        return self.source.start_bound(
            http if http is not None else self.http,
            None,
            directory=directory if directory is not None else self.directory,
            records=records if records is not None else self.records,
            deadline=deadline,
        )

    def add_world(self, world_id, name, *, dc=0):
        self.worlds.append({"id": world_id, "name": name})
        if dc is not None:
            self.dcs[dc]["worlds"].append(world_id)

    async def test_persists_known_and_default_scope_after_restart_and_unrelated_config(
        self,
    ):
        self.add_world(3000, "Cloudtest01", dc=None)
        first = await self.session().catalog()
        self.assertFalse(any(row.cached for row in first.provenance))
        self.now += timedelta(days=3650)
        self.values["ff14_default_region"] = "global"
        self.revision += 1
        restarted = ServerDirectory(self)
        hit = await self.session(directory=restarted).catalog(
            (("world", "SyntheticChina"),)
        )
        default = await self.session(directory=restarted).catalog()
        self.assertEqual(hit, default)
        self.assertEqual(hit.catalog, first.catalog)
        self.assertEqual(hit.catalog.unassigned_worlds, ((3000, "Cloudtest01"),))
        self.assertTrue(all(row.cached for row in hit.provenance))
        self.assertEqual(
            [row.fetched_at for row in first.provenance],
            [row.fetched_at for row in hit.provenance],
        )
        self.assertEqual(
            [row.path for row in self.http.requests], [WORLDS_PATH, DCS_PATH]
        )
        self.assertEqual(self.records.opened, 3)

    async def test_unknown_world_or_dc_refreshes_whole_directory_and_throttles_distinct_misses(
        self,
    ):
        await self.session().catalog()
        self.now += UNKNOWN_COOLDOWN
        self.add_world(91001, "NewWorld")
        self.dcs.append({"name": "NewDC", "region": "Japan", "worlds": []})
        self.add_world(91002, "NewDCWorld", dc=-1)
        refreshed = await self.session().catalog((("world", 91001),))
        self.assertEqual(refreshed.catalog.find("world", "NewWorld").id, 91001)
        self.assertEqual(refreshed.catalog.find("dc", "NewDC").region, "Japan")
        other = ServerDirectory(self)
        for targets in (
            (("world", "MistakeOne"),),
            (("world", 11111),),
            (("dc", "MistakeTwo"),),
        ):
            hit = await self.session(directory=other).catalog(targets)
            self.assertEqual(hit.catalog, refreshed.catalog)
            self.assertTrue(all(row.cached for row in hit.provenance))
        self.assertEqual(len(self.http.requests), 4)
        self.now += UNKNOWN_COOLDOWN
        self.dcs.append({"name": "LatestDC", "region": "Japan", "worlds": []})
        self.add_world(91003, "LatestDCWorld", dc=-1)
        result = await self.session(directory=other).catalog((("dc", "LatestDC"),))
        self.assertEqual(result.catalog.find("dc", "LatestDC").region, "Japan")
        self.assertEqual(len(self.http.requests), 6)

    async def test_ambiguous_unassigned_and_unsupported_are_not_misses(self):
        self.add_world(3000, "Cloudtest01", dc=None)
        self.add_world(91001, "SyntheticChina", dc=1)
        self.dcs.append({"name": "UnsupportedDC", "region": "Alt", "worlds": []})
        self.add_world(91002, "UnsupportedWorld", dc=-1)
        await self.session().catalog()
        self.now += UNKNOWN_COOLDOWN
        for kind, value in (
            ("world", "SYNTHETICCHINA"),
            ("world", "Cloudtest01"),
            ("world", 3000),
            ("world", "UnsupportedWorld"),
            ("dc", "UnsupportedDC"),
        ):
            hit = await self.session().catalog(((kind, value),))
            self.assertTrue(all(row.cached for row in hit.provenance))
        self.assertEqual(len(self.http.requests), 2)

    async def test_clock_rollback_does_not_keep_unknown_targets_cooled_down(self):
        await self.session().catalog()
        self.now -= timedelta(days=1)
        self.add_world(91001, "AfterClockRollback")
        fresh = await self.session().catalog((("world", 91001),))
        self.assertEqual(fresh.catalog.find("world", "AfterClockRollback").id, 91001)
        self.assertEqual(len(self.http.requests), 4)
        await self.session().catalog((("world", "Other unknown"),))
        self.assertEqual(len(self.http.requests), 4)

    async def test_endpoint_failures_preserve_last_complete_snapshot_and_cooldown(self):
        first = await self.session().catalog()
        for bad_path in (WORLDS_PATH, DCS_PATH):
            for failure in (SourceHttpError("transport_failed"), {"bad": "array"}):
                with self.subTest(path=bad_path, error=type(failure).__name__):
                    self.now += UNKNOWN_COOLDOWN

                    def response(request):
                        if request.path == bad_path:
                            if isinstance(failure, Exception):
                                raise failure
                            return failure
                        return self.worlds if request.path == WORLDS_PATH else self.dcs

                    http = FakeHttp(response)
                    with self.assertRaises((MarketPayloadError, SourceHttpError)):
                        await self.session(http=http).catalog((("world", "Unknown"),))
                    self.assertEqual(len(http.requests), 2)
                    known = await self.session(http=http).catalog(
                        (("world", REGION_IDS["China"]),)
                    )
                    unknown = await self.session(http=http).catalog(
                        (("world", "Other unknown"),)
                    )
                    self.assertEqual(known.catalog, first.catalog)
                    self.assertEqual(known, unknown)
                    self.assertEqual(len(http.requests), 2)

    async def test_concurrent_module_instances_use_one_cas_claim_and_known_still_reads(
        self,
    ):
        first = await self.session().catalog()
        self.now += UNKNOWN_COOLDOWN
        entered, release = asyncio.Event(), asyncio.Event()
        before = self.http.callback

        async def delayed(request):
            entered.set()
            await release.wait()
            return before(request)

        self.http.callback = delayed
        flight = asyncio.create_task(self.session().catalog((("world", "Unknown"),)))
        try:
            await entered.wait()
            concurrent = self.session(directory=ServerDirectory(self))
            self.assertEqual(
                (await concurrent.catalog((("world", "Other"),))).catalog, first.catalog
            )
            self.assertEqual((await self.session().catalog()).catalog, first.catalog)
            self.assertEqual(len(self.http.requests), 4)
        finally:
            release.set()
            await flight

    async def test_empty_data_center_candidates_do_not_replace_the_current_directory(
        self,
    ):
        first = await self.session().catalog()
        original = copy.deepcopy(self.dcs)
        for empty_centers in (1, len(self.dcs)):
            with self.subTest(empty_centers=empty_centers):
                self.now += UNKNOWN_COOLDOWN
                self.dcs = copy.deepcopy(original)
                for dc in self.dcs[:empty_centers]:
                    dc["worlds"] = []
                with self.assertRaises(MarketPayloadError):
                    await self.session().catalog((("world", "Unknown"),))
                self.assertEqual(
                    (await self.session().catalog()).catalog, first.catalog
                )

    async def test_structurally_valid_regression_is_preserved_until_explicit_clear(
        self,
    ):
        first = await self.session().catalog()
        original = copy.deepcopy(self.dcs)
        for shape in ("dc_missing", "world_unassigned", "unsupported"):
            with self.subTest(shape=shape):
                self.now += UNKNOWN_COOLDOWN
                self.dcs = copy.deepcopy(original)
                if shape == "dc_missing":
                    region = self.dcs.pop(0)
                    self.dcs[0]["worlds"].extend(region["worlds"])
                elif shape == "world_unassigned":
                    if not any(row["id"] == 91001 for row in self.worlds):
                        self.worlds.append({"id": 91001, "name": "Replacement"})
                    self.dcs[0]["worlds"] = [91001]
                else:
                    self.dcs[0]["region"] = "Alt"
                with self.assertRaises(MarketPayloadError) as error:
                    await self.session().catalog((("world", "Unknown"),))
                self.assertEqual(error.exception.code, "directory_coverage_regressed")
                self.assertEqual(
                    (await self.session().catalog()).catalog, first.catalog
                )

        self.values[SERVER_DIRECTORY_GENERATION] = "operator-clear"
        self.dcs = original[1:]
        rebuilt = await self.session().catalog()
        self.assertIn(
            (REGION_IDS["China"], "SyntheticChina"), rebuilt.catalog.unassigned_worlds
        )
        self.assertEqual(rebuilt.generation, "operator-clear")

    async def test_clear_fences_late_old_generation_after_a_new_snapshot(self):
        old = await self.session().catalog()
        self.now += UNKNOWN_COOLDOWN
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(request):
            entered.set()
            await release.wait()
            return WORLDS if request.path == WORLDS_PATH else DCS

        # A separately created service models a fresh module instance. It can
        # finish a new generation concurrently with the old claim.
        stale = asyncio.create_task(
            self.session(http=FakeHttp(delayed)).catalog((("world", "Unknown"),))
        )
        try:
            await entered.wait()
            self.values[SERVER_DIRECTORY_GENERATION] = "new-generation"
            self.revision += 1
            self.add_world(91001, "AfterClear")
            restarted = ServerDirectory(self)
            new_http = FakeHttp(self.http.callback)
            restarted_source = MarketSourceClient(
                None, clock=lambda: self.clock, wall_clock=lambda: self.now
            )
            rebuilt = await restarted_source.start_bound(
                new_http, None, directory=restarted, records=self.records
            ).catalog((("world", "AfterClear"),))
            self.assertEqual(len(new_http.requests), 2)
            self.assertEqual(rebuilt.generation, "new-generation")
        finally:
            release.set()
            with self.assertRaises(ServiceUnavailable):
                await stale
        with self.assertRaises(ServiceUnavailable):
            await self.directory.require_current(old)
        hit = await self.session().catalog((("world", 91001),))
        self.assertEqual(hit.catalog, rebuilt.catalog)
        self.assertEqual(len(self.http.requests), 2)

    async def test_cancellation_permission_and_total_deadline_propagate(self):
        first = await self.session().catalog()
        self.now += UNKNOWN_COOLDOWN
        denied = AccessDenied("source_revoked")

        def deny(request):
            raise denied

        with self.assertRaises(AccessDenied) as caught:
            await self.session(http=FakeHttp(deny)).catalog((("world", "Unknown"),))
        self.assertIs(caught.exception, denied)
        self.now += UNKNOWN_COOLDOWN

        def cancelled(request):
            raise asyncio.CancelledError

        with self.assertRaises(asyncio.CancelledError):
            await self.session(http=FakeHttp(cancelled)).catalog(
                (("world", "Unknown"),)
            )
        self.now += UNKNOWN_COOLDOWN

        def late(request):
            self.clock = 31
            return self.worlds if request.path == WORLDS_PATH else self.dcs

        with self.assertRaises(MarketDeadlineError):
            await self.session(http=FakeHttp(late)).catalog((("world", "Unknown"),))
        self.assertEqual((await self.session().catalog()).catalog, first.catalog)

    async def test_logs_reuses_market_snapshot_and_reports_persisted_hits(self):
        await self.session().catalog()
        metrics = []
        realm = await resolve_realm(
            self.source,
            self.http,
            self.records,
            "SyntheticChina",
            deadline=30,
            directory=ServerDirectory(self),
            metrics=SimpleNamespace(cache=lambda *args: metrics.append(args)),
        )
        self.assertEqual(realm, "cn")
        self.assertEqual(
            metrics, [("UniversalisWorlds", True), ("UniversalisDataCenters", True)]
        )
        self.assertEqual(len(self.http.requests), 2)
