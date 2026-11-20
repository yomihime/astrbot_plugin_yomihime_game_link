"""Complete invocation coverage; synthetic upstream, real cache and Core contracts."""

from __future__ import annotations

import asyncio
import io
import json
import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from time import monotonic, time
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.invocation import Gateway
from ygl_test_subject.modules.ff14.features.fflogs import (
    FFLogsCharacterLookup,
    _bounded_catalog_targets,
    _catalog_query_targets,
    _character_result,
    _target_key,
)
from ygl_test_subject.modules.ff14.features.fflogs_catalog import select_character_zones
from ygl_test_subject.modules.ff14.features.fflogs_evidence import project
from ygl_test_subject.modules.ff14.features.fflogs_history_cache import (
    HISTORY_CACHE_TTL,
    _plain,
    complete_pack_key,
    complete_packs,
    history_identity,
    restore_complete_pack,
)
from ygl_test_subject.modules.ff14.features.fflogs_models import (
    EncounterRecord,
    FFLogsPayloadError,
    PartitionRecord,
)
from ygl_test_subject.presentation.command_card import CommandCardRenderer

from tests.integration import test_bound_public_contracts as bound_fixture
from tests.modules.ff14 import test_fflogs as public_fixture
from tests.modules.ff14 import test_fflogs_explicit_history as explicit_fixture
from yomihime_game_link_sdk.contexts import InvocationOrigin
from yomihime_game_link_sdk.display import DisplayAudience, Privacy
from yomihime_game_link_sdk.errors import (
    InvalidInvocation,
    SourceHttpError,
)
from yomihime_game_link_sdk.storage import CacheLookup, CacheLookupStatus, CacheQuery


class CompleteHistoryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = explicit_fixture.ExplicitHistoryTests.asyncSetUp
    fetch = explicit_fixture.ExplicitHistoryTests.fetch
    run_history = explicit_fixture.ExplicitHistoryTests.run_history
    requested = explicit_fixture.ExplicitHistoryTests.requested
    planned_packs = explicit_fixture.ExplicitHistoryTests.planned_packs

    def large_catalog(self):
        self.metadata = replace(
            self.metadata,
            zones=(
                *self.metadata.zones[:3],
                *(
                    replace(
                        zone,
                        partitions=tuple(
                            PartitionRecord(p, f"标准阵容构成 (Synthetic {p})", p == 1)
                            for p in range(1, 17)
                        ),
                    )
                    for zone in self.metadata.zones[3:]
                ),
            ),
        )
        keys = tuple(key for group in self.planned_packs() for key in group)
        self.assertEqual(len(keys), 163)
        identity = history_identity(
            self.source,
            self.role,
            explicit_fixture.VARIABLES,
            self.metadata,
            self.owner,
        )
        groups = complete_packs(self.metadata, keys)
        return [(complete_pack_key(identity, group), group) for group in groups]

    def omit_rankings(self, scopes):
        targets = {(key[0], key[2]) for key in scopes}
        original = self.fetch

        async def fetch(request):
            response = await original(request)
            variables = json.loads(request.body)["variables"]
            value = json.loads(response.body)
            character = value["data"]["characterData"]["character"]
            for alias in tuple(character):
                if (
                    alias.startswith("z")
                    and (variables[alias], variables.get("p" + alias[1:])) in targets
                ):
                    del character[alias]
            return SimpleNamespace(status_code=200, body=json.dumps(value).encode())

        self.scope.http.fetch = fetch

    async def test_two_default_zones_share_refresh_when_their_history_keys_overlap(
        self,
    ):
        compact = self.large_catalog()
        first, members = compact[0]
        self.omit_rankings(members[:1])
        incomplete = self.scope.http.fetch
        entered, release, waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()
        second_requests = []
        lock_keys = []
        refresh = self.handler._refresh

        @asynccontextmanager
        async def observe_refresh(key, deadline):
            if key[0] == "history":
                lock_keys.append(key)
                if len(lock_keys) == 2:
                    waiting.set()
            async with refresh(key, deadline) as acquired:
                yield acquired

        self.handler._refresh = observe_refresh

        async def first_fetch(request):
            if request.source_id == self.source:
                entered.set()
                await release.wait()
            return await incomplete(request)

        async def second_fetch(request):
            if request.source_id == self.source:
                second_requests.append(json.loads(request.body))
            return await self.fetch(request)

        def start(zone, fetch):
            scope = SimpleNamespace(
                cache=self.scope.cache, http=SimpleNamespace(fetch=fetch)
            )
            return asyncio.create_task(
                self.handler._catalog_rankings(
                    scope,
                    self.source,
                    replace(self.role, zone_id=zone),
                    self.metadata,
                    explicit_fixture.VARIABLES,
                    self.handler._lifecycle,
                    cache_owner=self.owner,
                )
            )

        tasks = [start(73, first_fetch)]
        try:
            async with asyncio.timeout(5):
                await entered.wait()
                tasks.append(start(74, second_fetch))
                await waiting.wait()
            self.assertEqual(lock_keys[0], lock_keys[1])
            self.assertFalse(tasks[1].done())
            self.assertFalse(second_requests)
            release.set()
            partial, full = await asyncio.gather(*tasks)
        finally:
            release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        self.assertFalse(partial.catalog_complete)
        self.assertTrue(full.catalog_complete)
        self.assertEqual(full.history_cache_hits, 162)
        self.assertEqual(
            self.requested(second_requests), [(members[0][0], members[0][2])]
        )
        stored = (await self.scope.cache.lookup(CacheQuery(first))).entry
        restored, _ = restore_complete_pack(stored.payload, first, members)
        self.assertEqual(set(restored), set(members))

    async def test_distinct_sources_and_owners_can_refresh_while_first_is_in_flight(
        self,
    ):
        tasks = []

        async def start_and_wait(source, owner):
            entered = asyncio.Event()

            async def fetch(request):
                if request.source_id != source:
                    raise OSError("synthetic menu unavailable")
                entered.set()
                await asyncio.Future()

            scope = SimpleNamespace(
                cache=self.contract._cache(owner), http=SimpleNamespace(fetch=fetch)
            )
            tasks.append(
                asyncio.create_task(
                    self.handler._catalog_rankings(
                        scope,
                        source,
                        self.role,
                        self.metadata,
                        explicit_fixture.VARIABLES,
                        self.handler._lifecycle,
                        cache_owner=owner,
                    )
                )
            )
            await entered.wait()

        try:
            async with asyncio.timeout(5):
                await start_and_wait(self.source, self.owner)
                await start_and_wait(self.source, self.contract._invocation("bob"))
                await start_and_wait("fflogs_public_global", self.owner)
            self.assertTrue(all(not task.done() for task in tasks))
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_sparse_anchor_reuses_every_valid_scope_without_rewriting(self):
        compact = self.large_catalog()
        _, members = compact[0]
        failed = members[:1]
        self.omit_rankings(failed)
        first = await self.collect()
        self.assertFalse(first.catalog_complete)
        self.calls.clear()
        writes = self.operations["put"]
        lookup = self.scope.cache.lookup
        reads = []

        async def observe(query):
            reads.append(query.key)
            return await lookup(query)

        self.scope.cache.lookup = observe
        second = await self.collect()
        self.assertEqual(second.groups, first.groups)
        self.assertEqual(second.history_cache_hits, 162)
        self.assertEqual(self.requested(self.calls), [(failed[0][0], failed[0][2])])
        self.assertEqual(self.operations["put"], writes)
        self.assertCountEqual(
            [key for key in reads if key.startswith("fflogs-complete-")],
            [key for key, _ in compact],
        )
        self.assertEqual(
            sum(key.startswith("fflogs-history-pack-") for key in reads), 1
        )

    async def test_all_missing_anchor_discovers_siblings_without_proving_a_single_anchor_scope(
        self,
    ):
        compact = self.large_catalog()
        _, members = compact[0]
        self.omit_rankings(members)
        cold = await self.collect()
        self.calls.clear()
        hot = await self.collect()
        self.assertFalse(cold.catalog_complete)
        self.assertEqual(hot.groups, cold.groups)
        self.assertEqual(hot.history_cache_hits, 163 - len(members))
        self.assertCountEqual(
            self.requested(self.calls), [(k[0], k[2]) for k in members]
        )

    async def test_sparse_update_preserves_unread_valid_sibling_and_original_date(self):
        compact = self.large_catalog()
        first = compact[0][0]
        key, members = compact[1]
        await self.collect()
        lookup = self.scope.cache.lookup

        def dates(entry):
            return restore_complete_pack(entry.payload, key, members)[1]

        before = dates((await lookup(CacheQuery(key))).entry)

        async def undiscovered(query):
            if query.key == first:
                return CacheLookup(CacheLookupStatus.MISS)
            return await lookup(query)

        self.scope.cache.lookup = undiscovered
        self.omit_rankings(members[:1])
        partial = await self.collect()
        self.assertFalse(partial.catalog_complete)
        self.assertEqual(partial.history_cache_hits, 0)
        after = dates((await lookup(CacheQuery(key))).entry)
        self.assertEqual(after[members[0]], before[members[0]])
        self.assertGreater(after[members[1]], before[members[1]])
        self.scope.cache.lookup = lookup
        self.calls.clear()
        self.assertEqual((await self.collect()).history_cache_hits, 163)
        self.assertFalse(self.calls)

    async def test_sparse_anchor_never_replaces_corrupt_unverified_siblings(self):
        compact = self.large_catalog()
        first, members = compact[0]
        await self.collect()
        lookup, put = self.scope.cache.lookup, self.scope.cache.put
        original = (await lookup(CacheQuery(first))).entry
        writes = []

        async def corrupt(query):
            result = await lookup(query)
            if query.key == first:
                return SimpleNamespace(
                    status=result.status,
                    entry=SimpleNamespace(
                        expires_at=original.expires_at, payload={"version": "bad"}
                    ),
                )
            return result

        async def observe(key, payload, **kwargs):
            writes.append(key)
            return await put(key, payload, **kwargs)

        self.omit_rankings(members[:1])
        self.scope.cache = SimpleNamespace(lookup=corrupt, put=observe)
        self.assertFalse((await self.collect()).catalog_complete)
        self.assertNotIn(first, writes)
        self.assertEqual((await lookup(CacheQuery(first))).entry, original)

    async def test_revocation_during_partial_sibling_merge_stops_publication(self):
        compact = self.large_catalog()
        first = compact[0][0]
        missing, members = compact[1]
        lookup, put = self.scope.cache.lookup, self.scope.cache.put
        writes = []

        async def revoke(query):
            if query.key == missing:
                self.contract.issuer.release(self.owner)
            return await lookup(query)

        async def observe(key, payload, **kwargs):
            writes.append(key)
            return await put(key, payload, **kwargs)

        self.omit_rankings(members[:1])
        self.scope.cache = SimpleNamespace(lookup=revoke, put=observe)
        with self.assertRaises(PermissionError):
            await self.collect()
        self.assertNotIn(first, writes)
        self.assertFalse(writes)

    async def test_complete_slices_migrate_existing_legacy_without_fetch_or_renewal(
        self,
    ):
        compact = self.large_catalog()
        lookup, put = self.scope.cache.lookup, self.scope.cache.put
        reads, writes, payloads = [], [], {}

        async def observe_read(query):
            reads.append(query.key)
            return await lookup(query)

        async def observe_write(key, payload, **kwargs):
            writes.append(key)
            payloads[key] = payload
            return await put(key, payload, **kwargs)

        self.scope.cache = SimpleNamespace(lookup=observe_read, put=observe_write)
        with patch(
            "ygl_test_subject.modules.ff14.features.fflogs_history_cache.complete_packs",
            return_value=(),
        ):
            original = await self.collect()
        self.assertTrue(original.catalog_complete)
        legacy_dates = sorted(
            entry["snapshot"]["fetched_at"]
            for payload in payloads.values()
            for entry in payload["entries"]
        )
        self.calls.clear()
        writes.clear()
        migrated = await self.collect()
        self.assertEqual(migrated.groups, original.groups)
        self.assertEqual(migrated.history_cache_hits, 163)
        self.assertFalse(self.calls)
        self.assertEqual(set(writes), {key for key, _ in compact})
        self.assertEqual(writes[-1], compact[0][0])
        self.assertEqual(
            sorted(
                entry[1] for key, _ in compact for entry in payloads[key]["entries"]
            ),
            legacy_dates,
        )
        reads.clear()
        writes.clear()
        hot = await self.collect()
        self.assertEqual(hot.groups, original.groups)
        self.assertCountEqual(reads, [key for key, _ in compact])
        self.assertFalse(self.calls)
        self.assertFalse(writes)
        first, planned = compact[0]
        raw = _plain(payloads[first])
        raw["entries"][0][1] = time() - HISTORY_CACHE_TTL
        values, dates = restore_complete_pack(raw, first, planned)
        self.assertNotIn(planned[0], values)
        self.assertNotIn(planned[0], dates)
        self.assertEqual(len(values), len(planned) - 1)
        raw["entries"].reverse()
        with self.assertRaises(FFLogsPayloadError):
            restore_complete_pack(raw, first, planned)

    async def test_batch_reads_survive_missing_anchor_and_refetch_only_expired_members(
        self,
    ):
        compact = self.large_catalog()
        batches = []

        async def lookup_many(queries):
            batches.append(tuple(q.key for q in queries))
            return await self.cache.lookup_many(queries)

        self.scope.cache.lookup_many = lookup_many
        original = await self.collect()
        self.assertTrue(original.catalog_complete)
        self.calls.clear()
        batches.clear()
        hot = await self.collect()
        self.assertEqual(hot.groups, original.groups)
        self.assertEqual(hot.history_cache_hits, 163)
        self.assertFalse(self.calls)
        self.assertEqual(batches, [tuple(key for key, _ in compact)])

        anchor, missing = compact[0]
        sibling, members = compact[1]
        await self.cache.invalidate(anchor)
        entry = await self.cache.get(sibling)
        payload = _plain(entry.payload)
        payload["entries"][0][1] = time() - HISTORY_CACHE_TTL - 1
        await self.cache.put(sibling, payload, ttl_seconds=HISTORY_CACHE_TTL)
        repaired = await self.collect()
        expected = (*missing, members[0])
        self.assertCountEqual(
            self.requested(self.calls), [(k[0], k[2]) for k in expected]
        )
        self.assertEqual(repaired.history_cache_hits, 163 - len(expected))
        self.assertEqual(repaired.groups, original.groups)

    async def test_compact_miss_and_bad_slice_use_legacy_or_only_fetch_missing_members(
        self,
    ):
        compact = self.large_catalog()
        original = await self.collect()
        self.assertTrue(original.catalog_complete)
        self.calls.clear()
        key, members = compact[1]
        lookup = self.scope.cache.lookup

        async def corrupt(query):
            result = await lookup(query)
            if query.key != key:
                return result
            self.assertIs(result.status, CacheLookupStatus.HIT)
            return SimpleNamespace(
                status=result.status,
                entry=SimpleNamespace(
                    expires_at=result.entry.expires_at, payload={"version": "invalid"}
                ),
            )

        self.scope.cache.lookup = corrupt
        repaired = await self.collect()
        self.assertEqual(repaired.groups, original.groups)
        self.assertEqual(repaired.history_cache_hits, 163 - len(members))
        self.assertEqual(
            set(self.requested(self.calls)), {(k[0], k[2]) for k in members}
        )
        self.calls.clear()
        self.scope.cache.lookup = lookup
        self.assertEqual((await self.collect()).history_cache_hits, 163)
        self.assertFalse(self.calls)
        self.contract.issuer.release(self.owner)
        with self.assertRaises((PermissionError, RuntimeError)):
            await self.collect()
        self.assertFalse(self.calls)

    async def test_interrupted_slice_publication_remains_readable_from_legacy(self):
        compact = self.large_catalog()
        put = self.scope.cache.put
        seen = []

        async def fail_second_slice(key, payload, **kwargs):
            seen.append(key)
            if key == compact[2][0]:
                raise OSError("synthetic write failure")
            return await put(key, payload, **kwargs)

        self.scope.cache.put = fail_second_slice
        original = await self.collect()
        self.assertTrue(original.catalog_complete)
        self.assertNotIn(compact[0][0], seen)
        self.assertIn(compact[1][0], seen)
        self.calls.clear()
        self.scope.cache.put = put
        migrated = await self.collect()
        self.assertEqual(migrated.groups, original.groups)
        self.assertEqual(migrated.history_cache_hits, 163)
        self.assertFalse(self.calls)

    async def test_revoked_during_compact_write_does_not_publish_or_fall_back(self):
        compact = self.large_catalog()
        put = self.scope.cache.put
        writes = []

        async def revoke_before_second_write(key, payload, **kwargs):
            writes.append(key)
            if key == compact[2][0]:
                self.contract.issuer.release(self.owner)
            return await put(key, payload, **kwargs)

        self.scope.cache.put = revoke_before_second_write
        with self.assertRaises(PermissionError):
            await self.collect()
        self.assertEqual(writes, [compact[1][0], compact[2][0]])

    async def test_optional_menu_overlaps_history_and_reports_cache_hit(self):
        from tests.modules.ff14.test_duty_order import payload

        events = []
        evidence = SimpleNamespace(
            observe=lambda operation, body, **kw: events.append((operation, body, kw))
        )
        original = self.scope.http.fetch
        menu_calls = []
        history_started = asyncio.Event()

        async def fetch(request):
            if request.source_id == "xivapi_items":
                async with asyncio.timeout(1):
                    await history_started.wait()
                menu_calls.append(request)
                return SimpleNamespace(status_code=200, body=payload())
            history_started.set()
            return await original(request)

        self.scope.http.fetch = fetch
        first = await self.collect(evidence)
        second = await self.collect(evidence)
        states = [event for event in events if event[0] == "FF14DutyOrderState"]
        self.assertEqual(events[-1][0], "FF14HistorySummary")
        self.assertEqual([event[2]["cached"] for event in states], [False, True])
        self.assertTrue(all(event[1]["complete"] for event in states))
        self.assertEqual(len(menu_calls), 1)
        self.assertEqual(first.rankings, second.rankings)
        for event in events:
            if event[0] == "FF14HistorySummary":
                self.assertEqual(event[1]["planned"], 111)

    def summary(self):
        events = []
        evidence = SimpleNamespace(
            observe=lambda operation, payload, **kwargs: events.append(
                (operation, payload)
            )
        )
        return events, evidence

    async def collect(self, evidence=None, deadline=None):
        return await self.handler._catalog_rankings(
            self.scope,
            self.source,
            self.role,
            self.metadata,
            explicit_fixture.VARIABLES,
            self.handler._lifecycle,
            evidence,
            deadline=deadline,
            cache_owner=self.owner,
        )

    def shared_history(self):
        self.metadata = replace(
            self.metadata,
            zones=(
                *self.metadata.zones[:3],
                *(
                    replace(
                        zone,
                        expansion_id=10,
                        expansion_name="Synthetic previous expansion",
                        encounters=(
                            EncounterRecord(
                                zone.encounters[0].encounter_id,
                                "Synthetic Shared Ultimate",
                                zone.zone_id,
                            ),
                        ),
                    )
                    for zone in self.metadata.zones[3:]
                ),
            ),
            expansion_order=(20, 10),
        )
        ordered = _bounded_catalog_targets(
            _catalog_query_targets(select_character_zones(self.metadata, 73))
        )
        last = _target_key(ordered[-1])
        original = self.fetch

        async def winning_fetch(request):
            response = await original(request)
            value = json.loads(response.body)
            variables = json.loads(request.body)["variables"]
            for name, zone_id in variables.items():
                if name.startswith("z") and (
                    zone_id,
                    variables.get("p" + name[1:]),
                ) == (last[0], last[2]):
                    rows = value["data"]["characterData"]["character"][name]["rankings"]
                    for row in rows:
                        row.update(
                            rankPercent=100,
                            bestSpec="Astrologian",
                            bestAmount=8765,
                            totalKills=9,
                        )
            return SimpleNamespace(status_code=200, body=json.dumps(value).encode())

        self.scope.http.fetch = winning_fetch
        return last

    async def test_cold_111_last_scope_wins_entire_row_then_one_production_png(self):
        last = self.shared_history()
        self.scope.cache = None
        events, evidence = self.summary()
        result = await self.collect(evidence)
        self.assertEqual(len(self.requested(self.calls)), 111)
        self.assertEqual(self.requested(self.calls)[-1], (last[0], last[2]))
        row = result.groups[0].rankings[0]
        self.assertEqual(
            (
                row.rank_percent_exact,
                row.origin.zone_id,
                row.origin.requested_partition,
                row.spec,
                row.best_amount,
                row.total_kills,
            ),
            (100, last[0], last[2], "Astrologian", 8765, 9),
        )
        self.assertTrue(result.catalog_complete)
        document = _character_result(
            result, "https://cn.fflogs.com", "CN", "rdps", None, "cn", limit=1
        )
        validate_contract(document)
        Gateway._valid_result(document, expected_privacy=Privacy.PUBLIC)
        presentation = CommandCardRenderer()(
            document.document, audience=DisplayAudience.PUBLIC
        )
        self.assertIsNotNone(presentation.bitmap)
        with Image.open(io.BytesIO(presentation.bitmap.content)) as image:
            image.load()
            self.assertEqual(image.n_frames, 1)
            self.assertEqual(image.format, "PNG")
        self.assertIn("100.0%", presentation.text)
        self.assertNotIn("未查询", presentation.text)
        summary = project(*events[-1])
        self.assertEqual(
            (summary["planned"], summary["attempted"], summary["not_queried"]),
            (111, 111, 0),
        )

    async def test_cache_absent_bad_oversize_putfail_and_partial_hit_are_equivalent(
        self,
    ):
        self.shared_history()
        self.scope.cache = None
        reference = await self.collect()
        expected = reference.groups
        actual_cache = self.cache

        async def miss(query):
            return CacheLookup(CacheLookupStatus.MISS)

        async def bad(query):
            return SimpleNamespace(
                status=CacheLookupStatus.HIT,
                entry=SimpleNamespace(
                    expires_at=SimpleNamespace(timestamp=lambda: 10**12),
                    payload={"corrupt": [0] * 600},
                ),
            )

        async def fail(*args, **kwargs):
            raise OSError("Synthetic private backend error")

        for lookup, put in (
            (miss, actual_cache.put),
            (bad, actual_cache.put),
            (miss, fail),
        ):
            with self.subTest(lookup=lookup.__name__, put=put.__name__):
                before = len(self.calls)
                self.scope.cache = SimpleNamespace(lookup=lookup, put=put)
                result = await self.collect()
                self.assertEqual(result.groups, expected)
                self.assertTrue(result.catalog_complete)
                self.assertEqual(len(self.requested(self.calls[before:])), 111)
        calls = 0

        async def partial(query):
            nonlocal calls
            calls += 1
            return await actual_cache.lookup(query) if calls == 1 else await miss(query)

        self.scope.cache = SimpleNamespace(lookup=partial, put=actual_cache.put)
        events, evidence = self.summary()
        before = len(self.calls)
        result = await self.collect(evidence)
        summary = events[-1][1]
        self.assertGreater(result.history_cache_hits, 0)
        self.assertEqual(result.groups, expected)
        self.assertEqual(summary["attempted"] + summary["cache_hit"], 111)
        self.assertEqual(summary["attempted"], len(self.requested(self.calls[before:])))

    async def test_actual_shared_deadline_allows_work_after_old_fifteen_seconds(self):
        self.scope.cache = None
        clock = [0.0]
        original = self.scope.http.fetch

        async def slow(request):
            response = await original(request)
            clock[0] += 4
            await asyncio.sleep(0)
            return response

        self.scope.http.fetch = slow
        with patch(
            "ygl_test_subject.modules.ff14.features.fflogs.monotonic",
            side_effect=lambda: clock[0],
        ):
            result = await self.collect(deadline=80)
        self.assertGreater(clock[0], 15)
        self.assertLess(clock[0], 80)
        self.assertEqual(len(self.requested(self.calls)), 111)
        self.assertTrue(result.catalog_complete)

    async def test_last_batch_failure_missing_alias_invalid_and_empty_remain_distinct(
        self,
    ):
        self.scope.cache = None
        original = self.fetch

        async def altered(request):
            response = await original(request)
            if len(self.calls) == 5:
                raise OSError("Synthetic last batch failure")
            if len(self.calls) == 1:
                value = json.loads(response.body)
                character = value["data"]["characterData"]["character"]
                character.pop("z0")
                value["errors"] = [{"path": ["characterData", "character", "z0"]}]
                character["z1"] = {"malformed": True}
                return SimpleNamespace(status_code=200, body=json.dumps(value).encode())
            return response

        self.scope.http.fetch = altered
        events, evidence = self.summary()
        result = await self.collect(evidence)
        summary = events[-1][1]
        self.assertEqual(
            (
                summary["planned"],
                summary["attempted"],
                summary["missing_alias"],
                summary["invalid"],
                summary["request_failed"],
                summary["not_queried"],
            ),
            (111, 111, 1, 1, 15, 0),
        )
        self.assertGreater(summary["fresh_positive"], 0)
        self.assertGreater(summary["fresh_empty"], 0)
        self.assertEqual(
            sum(
                summary[key]
                for key in (
                    "cache_hit",
                    "fresh_positive",
                    "fresh_empty",
                    "missing_alias",
                    "invalid",
                    "request_failed",
                    "not_queried",
                )
            ),
            111,
        )
        self.assertFalse(result.catalog_complete)
        self.assertTrue(any(row.history_complete is False for row in result.rankings))

    async def test_rate_limit_stops_queued_batches_without_inventing_coverage(self):
        self.scope.cache = None
        for code, status in (("upstream_error", 429), ("rate_limited", None)):
            with self.subTest(code=code):
                calls = []

                async def limited(request):
                    calls.append(request.source_id)
                    raise SourceHttpError(code, status_code=status)

                self.scope.http.fetch = limited
                events, evidence = self.summary()
                result = await self.collect(evidence)
                summary = events[-1][1]
                self.assertEqual(calls.count(self.source), 1)
                self.assertEqual(
                    (
                        summary["attempted"],
                        summary["request_failed"],
                        summary["not_queried"],
                    ),
                    (24, 24, 87),
                )
                self.assertFalse(result.catalog_complete)

    async def test_size_fallback_completes_all_scopes_with_smaller_batches(self):
        self.scope.cache = None
        original = self.fetch
        reference = await self.collect()
        for mode in ("parser", "transport"):
            with self.subTest(mode=mode):
                sizes = []

                async def fetch(request):
                    if request.source_id != self.source:
                        raise OSError("Synthetic menu unavailable")
                    variables = json.loads(request.body)["variables"]
                    sizes.append(sum(key.startswith("z") for key in variables))
                    if sizes[-1] > 12:
                        if mode == "parser":
                            return SimpleNamespace(
                                status_code=200, body=b"x" * 1_000_001
                            )
                        raise SourceHttpError("response_too_large")
                    return await original(request)

                self.scope.http.fetch = fetch
                events, evidence = self.summary()
                result = await self.collect(evidence)
                self.assertEqual(sizes[0], 24)
                self.assertTrue(all(size <= 12 for size in sizes[1:]))
                self.assertEqual(sum(sizes[1:]), 111)
                self.assertTrue(result.catalog_complete)
                self.assertEqual(result.groups, reference.groups)
                self.assertEqual(events[-1][1]["attempted"], 111)

    async def test_production_invoke_consumes_context_deadline_and_validates_result(
        self,
    ):
        self.shared_history()
        base = public_fixture._FakeHttp(public_fixture._character_responses(realm="cn"))
        catalog_fetch = self.scope.http.fetch
        clock = [monotonic()]
        started = clock[0]

        async def fetch(request):
            if "FF14CatalogCharacter" in json.loads(request.body)["query"]:
                response = await catalog_fetch(request)
                clock[0] += 8
                await asyncio.sleep(0)
                return response
            return await base.fetch(request)

        services = public_fixture._services(SimpleNamespace(fetch=fetch))
        handler = FFLogsCharacterLookup(services)
        self.addCleanup(handler.close)

        async def metadata(*args, **kwargs):
            return self.metadata

        handler._character_metadata = metadata
        context = SimpleNamespace(
            origin=InvocationOrigin.COMMAND, deadline=started + 90
        )
        with patch(
            "ygl_test_subject.modules.ff14.features.fflogs.monotonic",
            side_effect=lambda: clock[0],
        ):
            result = await handler.invoke(
                context,
                {"realm": "cn", "server": "Cerberus", "character": "Synthetic Hero"},
            )
        self.assertIsNone(result.error)
        self.assertEqual(len(self.requested(self.calls)), 111)
        self.assertGreater(clock[0] - started, 30)
        Gateway._valid_result(result, expected_privacy=Privacy.PUBLIC)
        validate_contract(result)
        presentation = CommandCardRenderer()(
            result.document, audience=DisplayAudience.PUBLIC
        )
        self.assertIsNotNone(presentation.bitmap)
        self.assertIn("100.0%", presentation.text)

    async def test_cache_backend_unavailable_preserves_scope_and_revocation(self):
        contract = bound_fixture.BoundPublicContractTests(methodName="runTest")
        await contract.asyncSetUp()
        self.addAsyncCleanup(contract.asyncTearDown)
        owner, bound = await contract.bound()
        self.owner = owner
        services = contract.runtime.services.for_module(owner.module_id)
        rebindings = []

        async def rebind(view):
            renewed = await services.scopes.bind(view)
            rebindings.append(renewed)
            return SimpleNamespace(cache=renewed.cache, http=self.scope.http)

        self.handler._services = SimpleNamespace(scopes=SimpleNamespace(bind=rebind))
        self.scope.cache = bound.cache

        async def backend_failure(*args, **kwargs):
            raise OSError("Synthetic private backend path")

        events, evidence = self.summary()
        with (
            patch.object(
                type(contract.runtime.repositories.cache), "get", backend_failure
            ),
            patch.object(
                type(contract.runtime.repositories.cache), "get_many", backend_failure
            ),
            patch.object(
                type(contract.runtime.repositories.cache), "put", backend_failure
            ),
        ):
            result = await self.collect(evidence)
        self.assertTrue(result.catalog_complete)
        self.assertEqual(len(self.requested(self.calls)), 111)
        self.assertEqual(events[-1][1]["cache_unsaved"], 111)
        self.assertEqual(rebindings, [])
        before = len(self.calls)

        async def revoked_lookup(*args, **kwargs):
            contract.runtime.issuer.release(owner)
            raise OSError("synthetic backend failure after revocation")

        with patch.object(
            type(contract.runtime.repositories.cache), "get_many", revoked_lookup
        ):
            with self.assertRaises(InvalidInvocation):
                await self.collect()
        self.assertEqual(len(self.calls), before)

    async def test_production_invoke_preserves_exact_scope_after_cache_backend_failure(
        self,
    ):
        self.shared_history()
        contract = bound_fixture.BoundPublicContractTests(methodName="runTest")
        await contract.asyncSetUp()
        self.addAsyncCleanup(contract.asyncTearDown)
        owner = contract.view()
        actual_services = contract.runtime.services.for_module(owner.module_id)
        source = public_fixture._FakeHttp(
            public_fixture._character_responses(realm="cn")
        )
        synthetic_catalog = self.scope.http.fetch
        versions, used = [], []

        async def bind(view):
            bound = await actual_services.scopes.bind(view)
            versions.append(bound)

            class GuardedHttp:
                async def fetch(self, request):
                    # Every HTTP consumer retains the exact initial Core scope.
                    bound.tasks.deadline_monotonic
                    used.append(bound)
                    if "FF14CatalogCharacter" in json.loads(request.body)["query"]:
                        return await synthetic_catalog(request)
                    return await source.fetch(request)

            return SimpleNamespace(http=GuardedHttp(), cache=bound.cache)

        services = public_fixture._services(source)
        services.scopes = SimpleNamespace(bind=bind)
        handler = FFLogsCharacterLookup(services)
        self.addCleanup(handler.close)

        async def metadata(*args, **kwargs):
            return self.metadata

        handler._character_metadata = metadata

        async def fail(*args, **kwargs):
            raise OSError("Synthetic backend failure")

        with (
            patch.object(type(contract.runtime.repositories.cache), "get", fail),
            patch.object(type(contract.runtime.repositories.cache), "get_many", fail),
            patch.object(type(contract.runtime.repositories.cache), "put", fail),
        ):
            result = await handler.invoke(
                owner,
                {"realm": "cn", "server": "Cerberus", "character": "Synthetic Hero"},
            )
        self.assertIsNone(result.error)
        Gateway._valid_result(result, expected_privacy=Privacy.PUBLIC)
        self.assertEqual(len(self.requested(self.calls)), 111)
        self.assertEqual(len(versions), 1)
        self.assertTrue(used)
        self.assertTrue(all(item is versions[0] for item in used))
        self.assertIsNone(versions[0].tasks.deadline_monotonic)
        contract.runtime.issuer.release(owner)
        with self.assertRaises(InvalidInvocation):
            versions[0].tasks.deadline_monotonic

    def test_history_summary_is_closed_and_identity_free(self):
        value = project(
            "FF14HistorySummary",
            {
                "planned": 111,
                "max_inflight": 2,
                "actor": "PRIVATE",
                "token": "PRIVATE",
                "invalid": True,
                "read_ms": -1,
            },
        )
        self.assertEqual(value["planned"], 111)
        self.assertEqual(value["max_inflight"], 2)
        self.assertEqual(value["invalid"], 0)
        self.assertEqual(value["read_ms"], 0)
        self.assertNotIn("PRIVATE", json.dumps(value))
