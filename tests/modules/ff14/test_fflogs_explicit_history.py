"""Explicit history with the real managed cache/SQLite authorization boundary."""

from __future__ import annotations

import asyncio
import json
import unittest
from contextvars import ContextVar
from dataclasses import replace
from decimal import Decimal
from time import monotonic, time
from types import SimpleNamespace
from unittest.mock import patch

from modules.ff14.features.fflogs import (
    FFLogsCharacterLookup,
    _bounded_catalog_targets,
    _catalog_query_targets,
    _target_key,
)
from modules.ff14.features.fflogs_catalog import select_character_zones
from modules.ff14.features.fflogs_history_cache import (
    HISTORY_CACHE_TTL,
    HistoryRotation,
    cache_pack_key,
    cache_packs,
    history_identity,
    pack_payload,
    restore_pack,
    restore_snapshot,
    snapshot_key,
    snapshot_payload,
)
from modules.ff14.features.fflogs_models import (
    EncounterRecord,
    FFLogsPayloadError,
    OutputMetadata,
    PartitionRecord,
)
from tests.modules.ff14 import test_fflogs_aggregation as fixture
from tests.services import test_cache_resources as managed_cache_fixture
from yomihime_game_link_sdk.errors import ServiceUnavailable
from yomihime_game_link_sdk.storage import CacheLookup, CacheLookupStatus, CacheQuery


def catalog():
    counts = (3, 15, 21, 21, 18, 9, 3, 12, 3, 3)
    return OutputMetadata(
        (
            *(
                fixture.zone(73 + index, f"Synthetic tier {index}")
                for index in range(3)
            ),
            *(
                fixture.zone(
                    900 + index,
                    f"Synthetic Ultimate source {index}",
                    difficulty="普通",
                    encounters=(
                        EncounterRecord(
                            9000 + index,
                            f"Synthetic Ultimate boss {index}",
                            900 + index,
                        ),
                    ),
                    partitions=tuple(
                        PartitionRecord(p, f"标准阵容构成 (Synthetic {p})", p == 1)
                        for p in range(1, count + 1)
                    ),
                )
                for index, count in enumerate(counts)
            ),
        ),
        (),
        True,
        (20,),
    )


VARIABLES = dict(
    name="Synthetic Hero", serverSlug="cerberus", serverRegion="CN", metric="rdps"
)


class ExplicitHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.contract = managed_cache_fixture.CacheResourcesServiceTests(
            methodName="runTest"
        )
        await self.contract.asyncSetUp()
        self.addAsyncCleanup(self.contract.asyncTearDown)
        self.owner = self.contract._invocation("alice")
        self.cache = self.contract._cache(self.owner)
        self.operations = {"lookup": 0, "put": 0}
        self.current_io = ContextVar("history_test_io", default=None)
        self.cache_delay = 0
        self.lookup_starts = []
        self.http_starts = []

        async def lookup(query):
            self.lookup_starts.append(monotonic())
            self.operations["lookup"] += 1
            if self.current_io.get() is not None:
                self.current_io.get()["lookup"] += 1
            if self.cache_delay:
                try:
                    await asyncio.sleep(self.cache_delay)
                except asyncio.CancelledError:
                    await asyncio.sleep(0.02)
                    raise
            return await self.cache.lookup(query)

        async def put(*args, **kwargs):
            self.operations["put"] += 1
            if self.current_io.get() is not None:
                self.current_io.get()["put"] += 1
            return await self.cache.put(*args, **kwargs)

        self.handler = FFLogsCharacterLookup(None)
        self.addCleanup(self.handler.close)
        self.metadata = catalog()
        self.calls = []
        self.source_fails = False
        self.pause = None
        self.role = fixture.record()
        self.source = "fflogs_public_cn"
        self.raw_by_zone = {}
        self.visibility = False
        self.return_id = None
        self.scope = SimpleNamespace(
            cache=SimpleNamespace(lookup=lookup, put=put),
            http=SimpleNamespace(fetch=self.fetch),
        )

    async def fetch(self, request):
        self.http_starts.append(monotonic())
        wire = json.loads(request.body)
        variables = wire["variables"]
        self.calls.append(wire)
        if self.current_io.get() is not None:
            self.current_io.get()["ranking_posts"] += 1
        if self.pause is not None:
            await self.pause.wait()
        if self.source_fails:
            raise ValueError("Synthetic source error must not enter a snapshot")
        value = dict(
            id=self.role.character_id if self.return_id is None else self.return_id,
            name=self.role.name,
            hidden=self.visibility,
            server={"name": self.role.server_name},
        )
        for name, zone_id in variables.items():
            if not name.startswith("z"):
                continue
            index = name[1:]
            zone = next(z for z in self.metadata.zones if z.zone_id == zone_id)
            period = variables.get("p" + index, -1)
            value[name] = dict(
                metric="rdps",
                zone=zone_id,
                difficulty=variables["d" + index],
                partition=period,
                size=8,
                rankings=[]
                if period == 2
                else [
                    dict(
                        encounter={
                            "id": encounter.encounter_id,
                            "name": encounter.name,
                        },
                        rankPercent=0 if period == 1 else 99.9,
                        bestAmount=0,
                        totalKills=0,
                        bestSpec="Scholar",
                    )
                    for encounter in zone.encounters
                ],
            )
        for name, selection in value.items():
            if name.startswith("z") and selection["zone"] in self.raw_by_zone:
                for row in selection["rankings"]:
                    row.update(
                        rankPercent=self.raw_by_zone[selection["zone"]],
                        bestSpec="Astrologian",
                        bestAmount=7654,
                        totalKills=7,
                    )
        body = json.dumps({"data": {"characterData": {"character": value}}})
        for raw in self.raw_by_zone.values():
            body = body.replace('"' + raw + '"', raw)
        return SimpleNamespace(status_code=200, body=body.encode())

    async def run_history(self):
        started = monotonic()
        io = {"lookup": 0, "put": 0, "ranking_posts": 0}
        token = self.current_io.set(io)
        try:
            return await self.handler._catalog_rankings(
                self.scope,
                self.source,
                self.role,
                self.metadata,
                VARIABLES,
                self.handler._lifecycle,
                cache_owner=self.owner,
            )
        finally:
            from modules.ff14.features.fflogs import CATALOG_RANKING_DEADLINE_SECONDS

            print(
                "CACHE_IO "
                + json.dumps(
                    {
                        "test": self.id(),
                        "elapsed_ms": round((monotonic() - started) * 1000),
                        **io,
                        "ranking_deadline_seconds": CATALOG_RANKING_DEADLINE_SECONDS,
                    }
                ),
                flush=True,
            )
            self.current_io.reset(token)

    def requested(self, calls):
        return [
            (wire["variables"][name], wire["variables"].get("p" + name[1:]))
            for wire in calls
            for name in wire["variables"]
            if name.startswith("z")
        ]

    def planned_packs(self):
        targets = _catalog_query_targets(select_character_zones(self.metadata, 73))
        ordered = _bounded_catalog_targets(targets, limit=None)
        keys = tuple(
            dict.fromkeys(
                _target_key(target)
                for target in ordered
                if target[2] != "ultimate" or target[3] is not None
            )
        )
        return cache_packs(self.metadata, keys)

    async def test_real_cache_three_calls_cover_111_then_all_hit_and_preserve_savage(
        self,
    ):
        targets = _catalog_query_targets(select_character_zones(self.metadata, 73))
        self.assertEqual(len(targets), 111)
        previous = set()
        for expected, complete in ((48, False), (48, False), (15, True)):
            start = len(self.calls)
            result = await self.run_history()
            fresh = self.requested(self.calls[start:])
            self.assertEqual(len(fresh), expected)
            self.assertFalse(previous.intersection(fresh))
            previous.update(fresh)
            self.assertEqual(result.catalog_complete, complete)
            self.assertEqual(
                {g.zone_id for g in result.groups if g.category == "savage"},
                {73, 74, 75},
            )
            self.assertTrue(
                all(
                    g.rankings[0].query_state == "record"
                    for g in result.groups
                    if g.category == "savage"
                )
            )
        self.assertEqual(len(self.calls), 10)
        count = len(self.calls)
        cached = await self.run_history()
        self.assertEqual(len(self.calls), count)
        self.assertEqual(cached.history_cache_hits, 111)
        self.assertTrue(cached.catalog_complete)
        self.assertIsNotNone(cached.history_fetched_at)
        self.assertEqual(cached.history_cache_ttl, 900)

    async def test_failure_rotates_without_becoming_coverage_and_recovers(self):
        self.source_fails = True
        failed = await self.run_history()
        first = set(self.requested(self.calls))
        self.assertFalse(failed.catalog_complete)
        self.source_fails = False
        before = len(self.calls)
        partial = await self.run_history()
        self.assertFalse(first.intersection(self.requested(self.calls[before:])))
        self.assertFalse(partial.catalog_complete)
        for _ in range(2):
            complete = await self.run_history()
        self.assertTrue(complete.catalog_complete)

    async def test_real_cache_owner_isolation_and_expiry(self):
        await self.run_history()
        identity = history_identity(
            self.source, self.role, VARIABLES, self.metadata, self.owner
        )
        target = _catalog_query_targets(select_character_zones(self.metadata, 73))[0]
        group = next(
            group for group in self.planned_packs() if _target_key(target) in group
        )
        key = cache_pack_key(identity, group)
        self.assertIs(
            (await self.cache.lookup(CacheQuery(key))).status, CacheLookupStatus.HIT
        )
        bob = self.contract._invocation("bob")
        self.assertIs(
            (await self.contract._cache(bob).lookup(CacheQuery(key))).status,
            CacheLookupStatus.MISS,
        )
        with patch(
            "modules.ff14.features.fflogs_history_cache.time", return_value=time() + 901
        ):
            before = len(self.calls)
            result = await self.run_history()
            self.assertEqual(len(self.requested(self.calls[before:])), 48)
            self.assertEqual(result.history_cache_hits, 0)

    async def test_cancel_releases_lock_and_close_rejects_inflight(self):
        self.pause = asyncio.Event()
        task = asyncio.create_task(self.run_history())
        await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(
            all(
                not state.lock.locked() and state.users == 0
                for state in self.handler._history_rotation.entries.values()
            )
        )
        self.pause = None
        self.assertFalse((await self.run_history()).catalog_complete)
        self.pause = asyncio.Event()
        self.metadata = replace(self.metadata, expansion_order=(10, 20))
        before = len(self.calls)
        task = asyncio.create_task(self.run_history())
        while len(self.calls) == before:
            await asyncio.sleep(0.01)
        self.handler.close()
        self.pause.set()
        with self.assertRaises(ServiceUnavailable):
            await task
        self.assertFalse(self.handler._history_rotation.entries)

    async def test_cache_128_digit_full_row_winner_survives_real_sqlite(self):
        self.metadata = replace(
            self.metadata,
            zones=(
                self.metadata.zones[0],
                *(
                    replace(
                        zone,
                        name="Synthetic precision Ultimate",
                        encounters=(
                            EncounterRecord(
                                zone.zone_id * 10,
                                "Synthetic shared precision Ultimate",
                                zone.zone_id,
                            ),
                        ),
                        partitions=(
                            PartitionRecord(3, "标准阵容构成 (Synthetic)", True),
                        ),
                    )
                    for zone in self.metadata.zones[3:5]
                ),
            ),
        )
        self.raw_by_zone = {
            900: "99." + "0" * 124 + "01",
            901: "99." + "0" * 124 + "02",
        }
        first = await self.run_history()
        count = len(self.calls)
        cached = await self.run_history()
        self.assertEqual(len(self.calls), count)
        for result in (first, cached):
            ultimate_groups = [
                group for group in result.groups if group.category == "ultimate"
            ]
            self.assertEqual(len(ultimate_groups), 1)
            savage = next(
                group for group in result.groups if group.category == "savage"
            )
            self.assertEqual(savage.rankings[0].rank_percent_exact, Decimal("99.9"))
            winner = max(
                (
                    row
                    for group in result.groups
                    if group.category == "ultimate"
                    for row in group.rankings
                    if row.rank_percent_exact is not None
                ),
                key=lambda row: row.rank_percent_exact,
            )
            self.assertEqual(
                (
                    winner.origin.zone_id,
                    winner.origin.requested_partition,
                    winner.spec,
                    winner.best_amount,
                    winner.total_kills,
                ),
                (901, 3, "Astrologian", 7654, 7),
            )
            self.assertEqual(winner.rank_percent_exact, Decimal(self.raw_by_zone[901]))

    async def test_hidden_mismatch_and_cache_write_failure_never_create_completion(
        self,
    ):
        self.metadata = replace(
            self.metadata, zones=(self.metadata.zones[0], self.metadata.zones[3])
        )
        self.visibility = True
        self.assertTrue((await self.run_history()).hidden)
        self.assertEqual(self.operations["put"], 0)
        self.visibility = False
        self.return_id = 999
        self.assertFalse((await self.run_history()).catalog_complete)
        self.assertEqual(self.operations["put"], 0)
        self.return_id = None

        async def failing_put(*args, **kwargs):
            raise RuntimeError("Synthetic arbitrary error text")

        self.scope.cache.put = failing_put
        result = await self.run_history()
        self.assertTrue(result.catalog_complete)
        from modules.ff14.features.fflogs import HISTORY_INCOMPLETE

        self.assertNotIn(HISTORY_INCOMPLETE, result.catalog_warnings)
        self.assertTrue(any(row.query_state == "record" for row in result.rankings))
        self.assertTrue(
            all(
                row.history_complete
                for group in result.groups
                if group.category == "ultimate"
                for row in group.rankings
            )
        )
        self.assertNotIn("arbitrary", str(result.catalog_warnings))
        self.assertTrue(any("保存" in item for item in result.catalog_warnings))

    async def test_mixed_zone_scope_snapshots_are_full_and_do_not_merge_none_with_positive(
        self,
    ):
        mixed = fixture.zone(
            950,
            "Synthetic mixed duty",
            encounters=(
                EncounterRecord(9501, "Synthetic Mixed Ultimate", 950),
                EncounterRecord(9502, "Synthetic ordinary boss", 950),
            ),
        )
        self.metadata = replace(self.metadata, zones=(self.metadata.zones[0], mixed))
        first = await self.run_history()
        calls = len(self.calls)
        cached = await self.run_history()
        self.assertEqual(len(self.calls), calls)
        self.assertIn((950, 3), self.requested(self.calls))
        self.assertIn((950, None), self.requested(self.calls))
        for result in (first, cached):
            ultimate = next(
                group for group in result.groups if group.category == "ultimate"
            )
            savage = next(
                group
                for group in result.groups
                if group.category == "savage" and group.zone_id == 950
            )
            self.assertEqual(len(ultimate.rankings), 1)
            self.assertEqual(len(savage.rankings), 1)
            self.assertEqual(savage.rankings[0].encounter_id, 9502)
            self.assertEqual(ultimate.rankings[0].origin.requested_partition, 3)
            self.assertTrue(
                all(row.origin.requested_partition is None for row in savage.rankings)
            )
        identity = history_identity(
            self.source, self.role, VARIABLES, self.metadata, self.owner
        )
        for group in self.planned_packs():
            entry = await self.cache.lookup(CacheQuery(cache_pack_key(identity, group)))
            _, selections, _ = restore_pack(entry.entry.payload, identity, group)
            for key, selection in selections.items():
                if key[0] == 950:
                    self.assertEqual(
                        {row.encounter_id for row in selection.rows}, {9501, 9502}
                    )

    async def test_small_catalog_identity_dimensions_and_owner_do_not_reuse_snapshots(
        self,
    ):
        self.metadata = replace(
            self.metadata, zones=(self.metadata.zones[0], self.metadata.zones[3])
        )
        await self.run_history()
        mutations = (
            lambda: setattr(self, "role", replace(self.role, character_id=43)),
            lambda: setattr(self, "source", "fflogs_public_global"),
            lambda: setattr(
                self, "metadata", replace(self.metadata, expansion_order=(10, 20))
            ),
        )
        for mutate in mutations:
            mutate()
            result = await self.run_history()
            self.assertEqual(result.history_cache_hits, 0)
        self.owner = self.contract._invocation("bob")
        self.cache = self.contract._cache(self.owner)
        self.assertEqual((await self.run_history()).history_cache_hits, 0)

    async def test_same_role_concurrent_calls_serialize_and_reuse_fresh_results(self):
        await asyncio.gather(self.run_history(), self.run_history())
        requested = self.requested(self.calls)
        self.assertEqual(len(requested), 96)
        self.assertEqual(len(set(requested)), 96)

    async def test_revoked_invocation_cache_is_not_a_miss(self):
        self.contract.issuer.release(self.owner)
        with self.assertRaises((PermissionError, RuntimeError)):
            await self.run_history()
        self.assertFalse(self.calls)

    async def test_slow_cache_cannot_starve_http_or_advance_cursor_without_attempt(
        self,
    ):
        self.cache_delay = 0.1
        with patch(
            "modules.ff14.features.fflogs.CATALOG_RANKING_DEADLINE_SECONDS", 0.04
        ):
            for attempt in (1, 2):
                before = len(self.calls)
                result = await self.run_history()
                self.assertEqual(len(self.requested(self.calls[before:])), 48)
                self.assertTrue(
                    any(row.query_state == "record" for row in result.rankings)
                )
                state = next(iter(self.handler._history_rotation.entries.values()))
                self.assertEqual(state.cursor, 48 * attempt)
                self.assertFalse(result.catalog_complete)
                self.assertLess(
                    self.http_starts[before] - self.lookup_starts[attempt - 1], 0.1
                )
        identity = history_identity(
            self.source, self.role, VARIABLES, self.metadata, self.owner
        )
        state = self.handler._history_rotation.acquire(identity)
        await state.lock.acquire()
        cursor = state.cursor
        before = len(self.calls)
        try:
            with patch(
                "modules.ff14.features.fflogs.CATALOG_RANKING_DEADLINE_SECONDS", 0.04
            ):
                result = await self.run_history()
            self.assertEqual(state.cursor, cursor)
            self.assertEqual(len(self.calls), before)
        finally:
            state.lock.release()
            self.handler._history_rotation.release(state)

    async def test_writing_stall_is_after_all_http_and_keeps_returned_rows(self):
        async def immediate_miss(query):
            return CacheLookup(CacheLookupStatus.MISS)

        attempts = []

        async def stalled_put(*args, **kwargs):
            self.operations["put"] += 1
            self.current_io.get()["put"] += 1
            attempts.append(len(self.calls))
            await asyncio.Event().wait()

        self.scope.cache.lookup = immediate_miss
        self.scope.cache.put = stalled_put
        with patch(
            "modules.ff14.features.fflogs.CATALOG_RANKING_DEADLINE_SECONDS", 0.15
        ):
            result = await self.run_history()
        self.assertEqual(attempts, [4])
        self.assertEqual(len(self.requested(self.calls)), 48)
        self.assertTrue(any(row.query_state == "record" for row in result.rankings))
        self.assertTrue(any("保存" in warning for warning in result.catalog_warnings))
        self.assertFalse(result.catalog_complete)

    async def test_complete_fresh_history_stays_complete_when_put_times_out(self):
        self.metadata = replace(self.metadata, zones=self.metadata.zones[:4])

        async def immediate_miss(query):
            return CacheLookup(CacheLookupStatus.MISS)

        attempts = []

        async def stalled_put(*args, **kwargs):
            attempts.append(len(self.calls))
            await asyncio.Event().wait()

        self.scope.cache.lookup = immediate_miss
        self.scope.cache.put = stalled_put
        with patch(
            "modules.ff14.features.fflogs.CATALOG_RANKING_DEADLINE_SECONDS", 0.15
        ):
            result = await self.run_history()
        self.assertEqual(attempts, [1])
        self.assertEqual(len(self.requested(self.calls)), 6)
        self.assertTrue(result.catalog_complete)
        self.assertTrue(
            all(
                row.history_complete
                for group in result.groups
                if group.category == "ultimate"
                for row in group.rankings
            )
        )
        from modules.ff14.features.fflogs import HISTORY_INCOMPLETE

        self.assertNotIn(HISTORY_INCOMPLETE, result.catalog_warnings)
        self.assertTrue(any("保存" in warning for warning in result.catalog_warnings))


class SnapshotTests(unittest.TestCase):
    def test_stable_pack_layout_full_scope_ttl_and_node_limit(self):
        metadata = catalog()
        targets = _catalog_query_targets(select_character_zones(metadata, 73))
        ordered = _bounded_catalog_targets(targets, limit=None)
        keys = tuple(_target_key(target) for target in ordered)
        packs = cache_packs(metadata, keys)
        self.assertEqual(packs, cache_packs(metadata, keys))
        self.assertLess(len(packs), 20)
        group = packs[0]
        by_key = {_target_key(target): target for target in targets}
        values = {
            key: snapshot_payload(fixture.selection(by_key[key])) for key in group
        }
        original = cache_pack_key("scope", group)
        payload = pack_payload("scope", values, group)
        _, selections, _ = restore_pack(payload, "scope", group)
        self.assertEqual(set(selections), set(group))
        self.assertTrue(
            all(isinstance(entry["snapshot"], dict) for entry in payload["entries"])
        )
        values[group[0]]["fetched_at"] = time() - 901
        changed = pack_payload("scope", values, group)
        self.assertNotIn(group[0], restore_pack(changed, "scope", group)[1])
        self.assertEqual(original, cache_pack_key("scope", group))
        self.assertNotEqual(original, cache_pack_key("other", group))
        self.assertEqual(
            len(
                {
                    cache_pack_key("scope", ((1, 2, p, "rdps", "Historical", None),))
                    for p in (None, -1, 3)
                }
            ),
            3,
        )
        with self.assertRaises(FFLogsPayloadError):
            pack_payload(
                "scope",
                {key: snapshot_payload(fixture.selection(by_key[key])) for key in keys},
                keys,
            )

    def test_precise_codec_empty_zero_and_128_digits(self):
        metadata = catalog()
        target = _catalog_query_targets(select_character_zones(metadata, 73))[0]
        raw = "99." + "9" * 126
        selection = fixture.selection(target, raw=raw, amount=0, kills=0, job="Scholar")
        restored, at = restore_snapshot(snapshot_payload(selection))
        self.assertEqual(restored.rows[0].rank_percent_exact, Decimal(raw))
        self.assertEqual(
            (
                restored.rows[0].best_amount,
                restored.rows[0].total_kills,
                restored.rows[0].spec,
            ),
            (0, 0, "Scholar"),
        )
        self.assertGreater(at, time() - 5)
        self.assertEqual(
            restore_snapshot(snapshot_payload(replace(selection, rows=())))[0].rows, ()
        )
        with self.assertRaises(FFLogsPayloadError):
            snapshot_payload(replace(selection, rows=selection.rows * 100))
        with self.assertRaises(FFLogsPayloadError):
            restore_snapshot(
                snapshot_payload(selection, fetched_at=time() - HISTORY_CACHE_TTL)
            )

    def test_keys_and_lifetime_rotation_are_bounded(self):
        metadata = catalog()
        role = fixture.record()
        base = history_identity("fflogs_public_cn", role, VARIABLES, metadata)
        changed = [
            history_identity("fflogs_public_global", role, VARIABLES, metadata),
            history_identity(
                "fflogs_public_cn", replace(role, character_id=43), VARIABLES, metadata
            ),
            history_identity(
                "fflogs_public_cn", role, {**VARIABLES, "serverRegion": "JP"}, metadata
            ),
            history_identity(
                "fflogs_public_cn",
                role,
                VARIABLES,
                replace(metadata, expansion_order=(10, 20)),
            ),
        ]
        self.assertTrue(all(value != base for value in changed))
        self.assertEqual(
            len(
                {
                    snapshot_key(base, (1, 2, p, "rdps", "Historical", None))
                    for p in (None, -1, 3)
                }
            ),
            3,
        )
        rotation = HistoryRotation()
        for index in range(32):
            state = rotation.acquire(str(index))
            rotation.release(state)
        self.assertEqual(len(rotation.entries), 16)
        rotation.clear()
        self.assertFalse(rotation.entries)

    def test_unknown_and_nonstandard_labels_do_not_disappear_or_get_queried(self):
        metadata = catalog()
        first = metadata.zones[3]
        changed = replace(
            metadata,
            zones=(
                replace(
                    first,
                    partitions=(
                        PartitionRecord(1, "标准阵容构成 (Synthetic)", True),
                        PartitionRecord(2, "非标准阵容构成 (Synthetic)", False),
                        PartitionRecord(3, "unknown provider label", False),
                    ),
                ),
                *metadata.zones[:3],
            ),
        )
        targets = _catalog_query_targets(select_character_zones(changed, 73))
        self.assertEqual(
            {target[3] for target in targets if target[2] == "ultimate"}, {1, None}
        )
