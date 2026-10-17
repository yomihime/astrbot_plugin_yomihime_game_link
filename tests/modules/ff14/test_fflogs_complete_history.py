"""Complete invocation coverage; synthetic upstream, real cache and Core contracts."""

from __future__ import annotations

import asyncio
import io
import json
import unittest
from dataclasses import replace
from time import monotonic
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.invocation import Gateway
from ygl_test_subject.presentation.command_card import CommandCardRenderer

from modules.ff14.features.fflogs import (
    FFLogsCharacterLookup,
    _bounded_catalog_targets,
    _catalog_query_targets,
    _character_result,
    _target_key,
)
from modules.ff14.features.fflogs_catalog import select_character_zones
from modules.ff14.features.fflogs_evidence import project
from modules.ff14.features.fflogs_models import EncounterRecord
from tests.integration import test_bound_public_contracts as bound_fixture
from tests.modules.ff14 import test_fflogs as public_fixture
from tests.modules.ff14 import test_fflogs_explicit_history as explicit_fixture
from yomihime_game_link_sdk.contexts import InvocationOrigin
from yomihime_game_link_sdk.display import DisplayAudience, Privacy
from yomihime_game_link_sdk.errors import (
    InvalidInvocation,
    ServiceUnavailable,
    SourceHttpError,
)
from yomihime_game_link_sdk.storage import CacheLookup, CacheLookupStatus


class CompleteHistoryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = explicit_fixture.ExplicitHistoryTests.asyncSetUp
    fetch = explicit_fixture.ExplicitHistoryTests.fetch
    run_history = explicit_fixture.ExplicitHistoryTests.run_history
    requested = explicit_fixture.ExplicitHistoryTests.requested
    planned_packs = explicit_fixture.ExplicitHistoryTests.planned_packs

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
            "modules.ff14.features.fflogs.monotonic", side_effect=lambda: clock[0]
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
            if len(self.calls) == 10:
                raise OSError("Synthetic last batch failure")
            if len(self.calls) == 1:
                value = json.loads(response.body)
                character = value["data"]["characterData"]["character"]
                character.pop("z0")
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
            (111, 111, 1, 1, 3, 0),
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
        calls = 0

        async def limited(request):
            nonlocal calls
            calls += 1
            raise SourceHttpError("rate_limited", status_code=429)

        self.scope.http.fetch = limited
        events, evidence = self.summary()
        result = await self.collect(evidence)
        summary = events[-1][1]
        self.assertEqual(calls, 1)
        self.assertEqual(
            (summary["attempted"], summary["request_failed"], summary["not_queried"]),
            (12, 12, 99),
        )
        self.assertFalse(result.catalog_complete)

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
                clock[0] += 4
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
            "modules.ff14.features.fflogs.monotonic", side_effect=lambda: clock[0]
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

    async def test_cache_backend_unavailable_rechecks_real_binder_and_revocation(self):
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
                type(contract.runtime.repositories.cache), "put", backend_failure
            ),
        ):
            result = await self.collect(evidence)
        self.assertTrue(result.catalog_complete)
        self.assertEqual(len(self.requested(self.calls)), 111)
        self.assertEqual(events[-1][1]["cache_unsaved"], 111)
        self.assertGreater(len(rebindings), 1)
        before = len(self.calls)

        async def revoked_lookup(query):
            contract.runtime.issuer.release(owner)
            raise ServiceUnavailable()

        self.scope.cache = SimpleNamespace(lookup=revoked_lookup)
        with self.assertRaises(InvalidInvocation):
            await self.collect()
        self.assertEqual(len(self.calls), before)

    async def test_production_invoke_refreshes_every_consumer_after_cache_rebind(self):
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
                    # Real exact-caller proof rejects any stale pre-rebind consumer.
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
            patch.object(type(contract.runtime.repositories.cache), "put", fail),
        ):
            result = await handler.invoke(
                owner,
                {"realm": "cn", "server": "Cerberus", "character": "Synthetic Hero"},
            )
        self.assertIsNone(result.error)
        Gateway._valid_result(result, expected_privacy=Privacy.PUBLIC)
        self.assertEqual(len(self.requested(self.calls)), 111)
        self.assertGreater(len(versions), 2)
        self.assertIn(
            versions[11], used
        )  # Full cache read phase was rebound before HTTP.
        with self.assertRaises(InvalidInvocation):
            versions[0].tasks.deadline_monotonic
        self.assertIsNone(versions[-1].tasks.deadline_monotonic)

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
