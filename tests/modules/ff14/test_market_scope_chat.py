"""Synthetic trustworthy scope choices and real-event/generation fences."""

import asyncio
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.modules.ff14.features.chat_context import chat_owner
from ygl_test_subject.modules.ff14.features.item_chat import ItemChat
from ygl_test_subject.modules.ff14.features.item_sources import ItemSourceClient
from ygl_test_subject.modules.ff14.features.market_handler import MarketQueryHandler
from ygl_test_subject.modules.ff14.features.market_scope_chat import MarketScopeChoices
from ygl_test_subject.modules.ff14.features.market_sources import (
    CatalogSnapshot,
    MarketSourceClient,
    Provenance,
    parse_catalog,
)
from ygl_test_subject.modules.ff14.models import ItemCandidate
from ygl_test_subject.modules.ff14.query_resolution import (
    CatalogEntry,
    QueryDefaults,
    QueryResolutionError,
    ScopeCatalog,
)

from yomihime_game_link_sdk.contexts import (
    InvocationOrigin,
    InvocationView,
    MessageContext,
)
from yomihime_game_link_sdk.results import CapabilityResult, ResultStatus

from . import test_market as market_fixture


def context(event, *, select=False, **changes):
    return replace(
        InvocationView(
            event,
            InvocationOrigin.LLM_TOOL,
            "actor",
            "session",
            "ff14/ff14",
            1,
            1,
            adapter_id="adapter",
            capability_id="ff14.market.select" if select else "ff14.market.query",
        ),
        **changes,
    )


def snapshot():
    return SimpleNamespace(
        catalog=ScopeCatalog(
            (
                CatalogEntry("dc", "SyntheticCN", "SyntheticCN", "China"),
                CatalogEntry("dc", "SyntheticEU", "SyntheticEU", "Europe"),
                CatalogEntry("world", 71001, "Shared", "China", "SyntheticCN"),
                CatalogEntry("world", 71002, "Shared", "Europe", "SyntheticEU"),
            )
        ),
        provenance=(),
    )


class MarketScopeTests(unittest.IsolatedAsyncioTestCase):
    def parsed_scopes(self, *, shared_worlds=False):
        return CatalogSnapshot(
            parse_catalog(
                [
                    {
                        "id": 90001,
                        "name": "SharedWorld" if shared_worlds else "SyntheticCNWorld",
                    },
                    {
                        "id": 90003,
                        "name": "SharedWorld" if shared_worlds else "SyntheticEUWorld",
                    },
                ],
                [
                    {"name": "SharedDC", "region": "China", "worlds": [90001]},
                    {"name": "shareddc", "region": "Europe", "worlds": [90003]},
                ],
            ),
            (
                Provenance("https://universalis.app/api/v2/worlds", market_fixture.NOW),
                Provenance(
                    "https://universalis.app/api/v2/data-centers",
                    market_fixture.NOW,
                    True,
                ),
            ),
        )

    @staticmethod
    def scope_args(result):
        selection = result.model_facts.facts["selection"]
        return {key: selection[key] for key in ("batch_id", "generation", "scope_kind")}

    def setUp(self):
        self.now = [0]
        self.catalog_calls = 0
        self.config_calls = 0
        self.fetches = []
        self.messages = {}
        self.catalog_snapshot = snapshot()
        outer = self

        class Services:
            def __init__(self):
                self.scopes = self
                self.config = self

            async def bind(self, ctx):
                async def read():
                    return outer.messages[ctx.invocation_id]

                return SimpleNamespace(
                    message=SimpleNamespace(read=read), http=None, cache=None
                )

            async def current(self):
                outer.config_calls += 1
                return SimpleNamespace(
                    revision=3,
                    values={
                        "core_defaults": {"default_region": "global", "revision": 2}
                    },
                )

        self.handler = MarketQueryHandler(Services(), clock=lambda: self.now[0])
        self.addCleanup(self.handler.close)

        class Session:
            async def catalog(self):
                outer.catalog_calls += 1
                return outer.catalog_snapshot

            async def run(self, awaitable):
                return await awaitable

        self.handler.source.start_bound = lambda *args, **kwargs: Session()
        globals_ = self.handler.invoke.__func__.__globals__

        class Client:
            def __init__(self, session, catalog):
                pass

        async def execute(client, query):
            outer.fetches.append(query)
            if hasattr(outer, "blocked"):
                outer.entered.set()
                await outer.blocked.wait()
            return SimpleNamespace(
                query=query,
                observed_at=datetime.now(timezone.utc),
                outcomes=(),
                minimums=(),
                listings=(),
                truncated=False,
                catalog=None,
                result=CapabilityResult("scope-test", ResultStatus.SUCCESS),
            )

        Client.execute = execute
        self.client_patch = patch.dict(globals_, {"MarketClient": Client})
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)

    async def call(self, event, parameters, *, select=False, **changes):
        self.messages[event] = MessageContext("semantic user input", event)
        return await self.handler.invoke(
            context(event, select=select, **changes), parameters
        )

    async def pending(self, **parameters):
        result = await self.call(
            "issuing",
            {
                "query": "44091",
                "server": "Shared",
                "quality": "hq",
                "intent": "min",
                **parameters,
            },
        )
        self.assertIs(result.status, ResultStatus.NEEDS_SELECTION)
        self.assertEqual(self.fetches, [])
        return {
            key: result.model_facts.facts["selection"][key]
            for key in ("batch_id", "generation", "scope_kind")
        }

    async def test_ambiguous_world_namespace_frozen_conditions_and_no_first_choice(
        self,
    ):
        choice = await self.pending(request_language="non_zh")
        self.assertEqual(choice["scope_kind"], "world")
        before = (self.catalog_calls, self.config_calls)
        for event in ("denial", "distinction", "multiple"):
            result = await self.call(
                event, {**choice, "action": "clarify"}, select=True
            )
            self.assertIs(result.status, ResultStatus.NEEDS_SELECTION)
        self.assertEqual(self.fetches, [])
        self.assertEqual((self.catalog_calls, self.config_calls), before)
        result = await self.call("select", {**choice, "world_id": 71001}, select=True)
        self.assertIs(result.status, ResultStatus.SUCCESS)
        query = self.fetches[0]
        self.assertEqual(
            (query.scope.target, query.scope.regions, query.scope.default_basis),
            (71001, ("China",), "server_region"),
        )
        self.assertEqual(
            (query.quality, query.intent, query.core_revision, query.module_revision),
            ("hq", "min", 2, 3),
        )
        self.assertEqual((self.catalog_calls, self.config_calls), before)
        self.assertIs(
            (
                await self.call("replay", {**choice, "world_id": 71001}, select=True)
            ).status,
            ResultStatus.ERROR,
        )

    async def double_ambiguity(self):
        triple = getattr(self, "use_triple", False)
        self.chain_events = (
            ("issuing", "scope-confirm", "dc-confirm")
            if triple
            else ("issuing", "scope-confirm")
        )
        if triple:
            self.catalog_snapshot = self.parsed_scopes(shared_worlds=True)

        async def search(source, query, *, exact=False):
            self.assertEqual(query, "Synthetic Ore")
            return (
                ItemCandidate(90001, "Synthetic Ore Alpha"),
                ItemCandidate(90002, "Synthetic Ore Beta"),
            ), False

        with patch.object(ItemSourceClient, "search", search):
            scope = await self.pending(
                query="Synthetic Ore",
                request_language="non_zh",
                **({"server": "SharedWorld", "dc": "SHAREDDC"} if triple else {}),
            )
            before = (self.catalog_calls, self.config_calls)
            result = await self.call(
                "scope-confirm",
                {**scope, "world_id": 90001 if triple else 71001},
                select=True,
            )
            if triple:
                self.assertIs(result.status, ResultStatus.NEEDS_SELECTION)
                selection = result.model_facts.facts["selection"]
                self.assertEqual(selection["scope_kind"], "dc")
                self.assertEqual(
                    [row["dc_id"] for row in selection["candidates"]],
                    ["SharedDC", "shareddc"],
                )
                dc_args = self.scope_args(result)
                for event in self.chain_events[:2]:
                    denied = await self.call(
                        event, {**dc_args, "dc_id": "SharedDC"}, select=True
                    )
                    self.assertIs(denied.status, ResultStatus.ERROR)
                    bypass = await self.call(
                        event, {"query": "90002", "action": "correction"}
                    )
                    self.assertIn(
                        bypass.status,
                        (ResultStatus.ERROR, ResultStatus.NEEDS_SELECTION),
                    )
                    self.assertEqual(self.fetches, [])
                result = await self.call(
                    "dc-confirm", {**dc_args, "dc_id": "SharedDC"}, select=True
                )
        self.assertIs(result.status, ResultStatus.NEEDS_SELECTION)
        self.assertEqual(self.fetches, [])
        self.assertEqual((self.catalog_calls, self.config_calls), before)
        selection = result.model_facts.facts["selection"]
        self.assertEqual(
            [row["item_id"] for row in selection["candidates"]], [90001, 90002]
        )
        owner = chat_owner(context("issuing"))
        state = self.handler.registry._pending[owner]
        self.assertEqual(state.source_ref, "issuing")
        self.assertEqual(
            state.generation,
            self.handler.scope_choices.get(owner).values["ticket"].generation,
        )
        query = state.batch.query
        self.assertEqual(
            (query.scope.target, query.scope.regions, query.scope.default_basis),
            (90001 if triple else 71001, ("China",), "server_region"),
        )
        self.assertEqual(
            (
                query.query,
                query.quality,
                query.intent,
                query.core_revision,
                query.module_revision,
            ),
            ("Synthetic Ore", "hq", "min", 2, 3),
        )
        return {key: selection[key] for key in ("batch_id", "generation")}, state

    async def test_scope_then_item_requires_third_event_and_fetches_once(self):
        choice, state = await self.double_ambiguity()
        batch = state.batch
        for event in self.chain_events:
            denied = await self.call(event, {**choice, "item_id": 90002}, select=True)
            self.assertIs(denied.status, ResultStatus.ERROR)
            self.assertIs(state.batch, batch)
            self.assertIsNone(state.consumed_batch)
            self.assertEqual(self.fetches, [])
            for action in ("query", "correction", "continue"):
                denied = await self.call(
                    event, {"query": "90002", "region": "global", "action": action}
                )
                self.assertIs(denied.status, ResultStatus.ERROR)
                self.assertIs(state.batch, batch)
                self.assertEqual(self.fetches, [])
        self.assertEqual(state.confirmation_ref, self.chain_events[-1])
        result = await self.call(
            "item-confirm", {**choice, "item_id": 90002}, select=True
        )
        self.assertIs(result.status, ResultStatus.SUCCESS)
        self.assertEqual(len(self.fetches), 1)
        query = self.fetches[0]
        self.assertEqual(
            (query.item_id, query.original_query), (90002, "Synthetic Ore")
        )
        for field in ("scope", "quality", "intent", "core_revision", "module_revision"):
            self.assertEqual(getattr(query, field), getattr(batch.query, field))
        replay = await self.call("replay", {**choice, "item_id": 90002}, select=True)
        self.assertIs(replay.status, ResultStatus.ERROR)
        self.assertEqual(len(self.fetches), 1)

    async def test_scope_then_item_owner_membership_cross_capability_ttl_and_close(
        self,
    ):
        choice, state = await self.double_ambiguity()
        batch = state.batch
        for changes in (
            {"actor_id": "other"},
            {"conversation_id": "other"},
            {"adapter_id": "other"},
            {"module_epoch": 2},
            {"module_id": "other/module"},
        ):
            with self.subTest(identity=changes):
                denied = await self.call(
                    "foreign", {**choice, "item_id": 90002}, select=True, **changes
                )
                self.assertIs(denied.status, ResultStatus.ERROR)
                self.assertIs(state.batch, batch)
        for override in (
            {"item_id": True},
            {"item_id": 99999},
            {"batch_id": "wrong"},
            {"generation": "wrong"},
        ):
            with self.subTest(choice=override):
                denied = await self.call(
                    "bad", {**choice, "item_id": 90002, **override}, select=True
                )
                self.assertIs(denied.status, ResultStatus.ERROR)
                self.assertIs(state.batch, batch)
        item = ItemChat(self.handler.services, clock=lambda: self.now[0])
        self.addCleanup(item.close)
        self.messages["cross-item"] = MessageContext(
            "select synthetic ore", "cross-item"
        )
        denied = await item.invoke(
            context("cross-item", capability_id="ff14.item.select"),
            {**choice, "item_id": 90002},
        )
        self.assertIs(denied.status, ResultStatus.ERROR)
        self.assertIs(state.batch, batch)
        self.assertEqual(item.registry.size, 0)

        async def search(source, query, *, exact=False):
            return (
                ItemCandidate(90001, "Synthetic Ore Alpha"),
                ItemCandidate(90002, "Synthetic Ore Beta"),
            ), False

        self.messages["item-issuing"] = MessageContext(
            "find synthetic ore", "item-issuing"
        )
        with patch.object(ItemSourceClient, "search", search):
            other = await item.invoke(
                context("item-issuing", capability_id="ff14.item.lookup"),
                {"query": "Synthetic Ore"},
            )
        self.assertIs(other.status, ResultStatus.NEEDS_SELECTION)
        other_choice = other.model_facts.facts["selection"]
        denied = await self.call(
            "cross-market",
            {
                "batch_id": other_choice["batch_id"],
                "generation": other_choice["generation"],
                "item_id": 90002,
            },
            select=True,
        )
        self.assertIs(denied.status, ResultStatus.ERROR)
        self.assertIs(state.batch, batch)
        self.assertEqual(self.fetches, [])
        self.assertIsNone(state.consumed_batch)
        self.now[0] = 301
        expired = await self.call("expired", {**choice, "item_id": 90002}, select=True)
        self.assertIs(expired.status, ResultStatus.ERROR)
        self.assertEqual(self.fetches, [])
        closed_choice, _ = await self.double_ambiguity()
        self.handler.close()
        self.assertEqual(self.handler.registry.size, 0)
        self.assertEqual(len(self.handler.scope_choices.pending.states), 0)
        closed = await self.call(
            "closed", {**closed_choice, "item_id": 90002}, select=True
        )
        self.assertIs(closed.status, ResultStatus.ERROR)
        self.assertEqual(self.fetches, [])

    async def test_scope_then_item_flight_fences_queries_and_late_results(self):
        for boundary in ("new_event", "close"):
            with self.subTest(boundary=boundary):
                self.fetches.clear()
                choice, state = await self.double_ambiguity()
                self.entered, self.blocked = asyncio.Event(), asyncio.Event()
                flight = asyncio.create_task(
                    self.call("item-confirm", {**choice, "item_id": 90002}, select=True)
                )
                release = self.blocked
                try:
                    await self.entered.wait()
                    self.assertIsNone(state.batch)
                    self.assertEqual(state.confirmation_ref, "item-confirm")
                    generation = state.generation
                    for event in (*self.chain_events, "item-confirm"):
                        for action in ("query", "correction", "continue"):
                            denied = await self.call(
                                event,
                                {
                                    "query": "90002",
                                    "region": "global",
                                    "action": action,
                                },
                            )
                            self.assertIs(denied.status, ResultStatus.ERROR)
                            self.assertEqual(state.generation, generation)
                            self.assertEqual(len(self.fetches), 1)
                    concurrent = await self.call(
                        "item-confirm", {**choice, "item_id": 90002}, select=True
                    )
                    self.assertIs(concurrent.status, ResultStatus.ERROR)
                    self.assertEqual(len(self.fetches), 1)
                    del self.blocked
                    if boundary == "new_event":
                        fresh = await self.call(
                            "fresh",
                            {
                                "query": "44092",
                                "region": "global",
                                "action": "correction",
                            },
                        )
                        self.assertIs(fresh.status, ResultStatus.SUCCESS)
                        self.assertEqual(len(self.fetches), 2)
                    else:
                        self.handler.close()
                        self.assertEqual(self.handler.registry.size, 0)
                        self.assertEqual(
                            len(self.handler.scope_choices.pending.states), 0
                        )
                finally:
                    release.set()
                    late = await flight
                    if hasattr(self, "blocked"):
                        del self.blocked
                self.assertIs(late.status, ResultStatus.ERROR)
                self.assertNotIn("market", late.model_facts.facts)
                replay = await self.call(
                    "late-replay", {**choice, "item_id": 90002}, select=True
                )
                self.assertIs(replay.status, ResultStatus.ERROR)
                self.assertEqual(len(self.fetches), 2 if boundary == "new_event" else 1)

    async def test_world_dc_item_requires_fourth_event_and_single_consumption(self):
        self.use_triple = True
        await self.test_scope_then_item_requires_third_event_and_fetches_once()

    async def test_world_dc_item_identity_membership_cross_capability_ttl_close(self):
        self.use_triple = True
        await (
            self.test_scope_then_item_owner_membership_cross_capability_ttl_and_close()
        )

    async def test_world_dc_item_inflight_supersession_and_close_late_fences(self):
        self.use_triple = True
        await self.test_scope_then_item_flight_fences_queries_and_late_results()

    async def test_typed_dc_exact_members_cross_actual_market_http_and_full_catalog(
        self,
    ):
        self.client_patch.stop()  # Exercise the real MarketClient and MarketSession.
        frozen = self.parsed_scopes()
        dc_rows = [
            {"name": "SharedDC", "region": "China", "worlds": [90001]},
            {"name": "shareddc", "region": "Europe", "worlds": [90003]},
        ]
        worlds = [
            {"id": 90001, "name": "SyntheticCNWorld"},
            {"id": 90003, "name": "SyntheticEUWorld"},
        ]
        for canonical, region in (("SharedDC", "China"), ("shareddc", "Europe")):
            with self.subTest(dc=canonical):

                def payload(request):
                    if request.path.endswith("/worlds"):
                        return worlds
                    if request.path.endswith("/data-centers"):
                        return dc_rows
                    self.assertEqual(
                        request.path, f"/api/v2/aggregated/{canonical}/44091"
                    )
                    return market_fixture.aggregated(region, quality="hq", level="dc")

                http = market_fixture.FakeHttp(payload)
                original_bind = self.handler.services.bind

                async def bind(ctx):
                    scope = await original_bind(ctx)
                    return SimpleNamespace(message=scope.message, http=http, cache=None)

                self.handler.services.bind = bind
                self.handler.source = MarketSourceClient(
                    None,
                    clock=lambda: self.now[0],
                    wall_clock=lambda: market_fixture.NOW,
                )
                first = await self.call(
                    "dc-A-" + canonical,
                    {
                        "query": "44091",
                        "dc": "SHAREDDC",
                        "quality": "hq",
                        "intent": "min",
                        "request_language": "non_zh",
                    },
                )
                self.assertIs(first.status, ResultStatus.NEEDS_SELECTION)
                args = self.scope_args(first)
                self.assertEqual(
                    [
                        r["dc_id"]
                        for r in first.model_facts.facts["selection"]["candidates"]
                    ],
                    ["SharedDC", "shareddc"],
                )
                second = await self.call(
                    "dc-B-" + canonical, {**args, "dc_id": canonical}, select=True
                )
                self.assertIs(second.status, ResultStatus.SUCCESS)
                facts = second.model_facts.facts["market"]
                self.assertEqual(
                    (
                        facts["scope"]["kind"],
                        facts["scope"]["target"],
                        facts["scope"]["regions"],
                        facts["scope"]["default_basis"],
                    ),
                    ("dc", canonical, (region,), "server_region"),
                )
                self.assertEqual(
                    (
                        facts["quality"],
                        facts["intent"],
                        facts["core_revision"],
                        facts["module_revision"],
                    ),
                    ("hq", "min", 2, 3),
                )
                self.assertEqual(
                    len([r for r in http.requests if "/aggregated/" in r.path]), 1
                )
                self.assertEqual(
                    facts["minimums"][0]["world_name"],
                    "SyntheticCNWorld" if region == "China" else "SyntheticEUWorld",
                )
                self.assertIn(
                    f"/aggregated/{canonical}/44091",
                    facts["minimums"][0]["source"]["url"],
                )
                replay = await self.call(
                    "dc-replay", {**args, "dc_id": canonical}, select=True
                )
                self.assertIs(replay.status, ResultStatus.ERROR)
                self.assertEqual(
                    len([r for r in http.requests if "/aggregated/" in r.path]), 1
                )
                self.handler.services.bind = original_bind
        with self.assertRaises(QueryResolutionError):
            frozen.catalog.find(
                "dc", "SharedDC"
            )  # Ordinary casefold remains ambiguous.

    async def test_typed_dc_invalid_namespace_membership_conflicts_and_catalog_binding(
        self,
    ):
        self.catalog_snapshot = self.parsed_scopes()
        first = await self.call(
            "dc-A",
            {"query": "44091", "dc": "SHAREDDC", "quality": "hq", "intent": "min"},
        )
        args = self.scope_args(first)
        for bad in (
            {"dc_id": "SHAREDDC"},
            {"dc_id": "unknown"},
            {"dc_id": True},
            {"world_id": 90001},
            {"item_id": 90001},
        ):
            with self.subTest(bad=bad):
                denied = await self.call("bad-dc", {**args, **bad}, select=True)
                self.assertIs(denied.status, ResultStatus.ERROR)
                self.assertFalse(
                    self.handler.scope_choices.get(chat_owner(context("dc-A"))).consumed
                )
                self.assertEqual(self.fetches, [])
        chosen = await self.call("dc-B", {**args, "dc_id": "SharedDC"}, select=True)
        self.assertIs(chosen.status, ResultStatus.SUCCESS)
        self.assertEqual(
            (self.fetches[0].scope.target, self.fetches[0].scope.regions),
            ("SharedDC", ("China",)),
        )
        self.fetches.clear()
        first = await self.call(
            "region-A", {"query": "44091", "dc": "SHAREDDC", "region": "global"}
        )
        args = self.scope_args(first)
        self.assertIs(
            (
                await self.call(
                    "region-bad", {**args, "dc_id": "SharedDC"}, select=True
                )
            ).status,
            ResultStatus.ERROR,
        )
        self.assertEqual(self.fetches, [])
        self.catalog_snapshot = self.parsed_scopes(shared_worlds=True)
        first = await self.call(
            "world-A", {"query": "44091", "server": "SharedWorld", "dc": "SHAREDDC"}
        )
        second = await self.call(
            "world-B", {**self.scope_args(first), "world_id": 90001}, select=True
        )
        self.assertEqual(second.model_facts.facts["selection"]["scope_kind"], "dc")
        conflict = await self.call(
            "world-C", {**self.scope_args(second), "dc_id": "shareddc"}, select=True
        )
        self.assertIs(conflict.status, ResultStatus.ERROR)
        self.assertIn("冲突", conflict.error.message)
        self.assertEqual(self.fetches, [])
        state = self.handler.scope_choices.get(chat_owner(context("world-A")))
        proxy = self.handler.scope_choices.selection_catalog(state)
        self.assertIs(proxy.catalog.entries, self.catalog_snapshot.catalog.entries)
        self.assertIs(proxy.provenance, self.catalog_snapshot.provenance)
        with self.assertRaises(QueryResolutionError):
            proxy.catalog.find("dc", "SHAREDDC")
        unrelated = snapshot()
        with self.assertRaises(QueryResolutionError):
            self.handler.scope_choices.selection_catalog(state, unrelated)

    async def test_owner_epoch_member_namespace_and_original_event_denials_no_fetch(
        self,
    ):
        choice = await self.pending()
        for changes in (
            {"actor_id": "other"},
            {"conversation_id": "other"},
            {"adapter_id": "other"},
            {"module_epoch": 2},
        ):
            self.assertIs(
                (
                    await self.call(
                        "foreign", {**choice, "world_id": 71001}, select=True, **changes
                    )
                ).status,
                ResultStatus.ERROR,
            )
        for bad in (
            {"world_id": 999},
            {"world_id": True},
            {"dc_id": "SyntheticCN"},
            {"item_id": 71001},
        ):
            self.assertIs(
                (await self.call("bad", {**choice, **bad}, select=True)).status,
                ResultStatus.ERROR,
            )
        self.assertIs(
            (
                await self.call("issuing", {**choice, "world_id": 71001}, select=True)
            ).status,
            ResultStatus.ERROR,
        )
        self.assertEqual(self.fetches, [])

    async def test_explicit_region_conflict_rejected_and_region_filter_kept(self):
        result = await self.call(
            "conflict", {"query": "44091", "server": "71001", "region": "global"}
        )
        self.assertIs(result.status, ResultStatus.ERROR)
        self.assertEqual(self.fetches, [])
        choice = await self.pending(region="global", request_language="zh")
        self.assertIs(
            (
                await self.call(
                    "wrong-region", {**choice, "world_id": 71001}, select=True
                )
            ).status,
            ResultStatus.ERROR,
        )
        result = await self.call(
            "global-member", {**choice, "world_id": 71002}, select=True
        )
        self.assertIs(result.status, ResultStatus.SUCCESS)
        self.assertEqual(self.fetches[0].scope.default_basis, "explicit_region")

    async def test_consumed_confirmation_and_issuing_event_cannot_create_second_query(
        self,
    ):
        choice = await self.pending()
        self.entered, self.blocked = asyncio.Event(), asyncio.Event()
        flight = asyncio.create_task(
            self.call("confirmation", {**choice, "world_id": 71001}, select=True)
        )
        await self.entered.wait()
        try:
            for event in ("confirmation", "issuing"):
                for action in ("query", "correction", "continue"):
                    result = await self.call(
                        event, {"query": "44092", "region": "global", "action": action}
                    )
                    self.assertIs(result.status, ResultStatus.ERROR)
            self.assertEqual(len(self.fetches), 1)
        finally:
            self.blocked.set()
        self.assertIs((await flight).status, ResultStatus.SUCCESS)

    async def test_real_new_event_supersedes_late_scope_flight_and_close_clears(self):
        choice = await self.pending()
        self.entered, self.blocked = asyncio.Event(), asyncio.Event()
        flight = asyncio.create_task(
            self.call("confirmation", {**choice, "world_id": 71001}, select=True)
        )
        await self.entered.wait()
        release = self.blocked
        del self.blocked  # Subsequent new user query has no artificial barrier.
        # Keep the actual Event object awaited by the first flight.
        state = self.handler.scope_choices.get(chat_owner(context("issuing")))
        old_generation = state.generation
        result = await self.call(
            "fresh", {"query": "44092", "region": "global", "action": "correction"}
        )
        self.assertIs(result.status, ResultStatus.SUCCESS)
        self.assertIsNone(
            self.handler.scope_choices.get(chat_owner(context("issuing")))
        )
        release.set()
        late = await flight
        self.assertIs(late.status, ResultStatus.ERROR)
        self.assertNotIn("market", late.model_facts.facts)
        self.assertEqual(len(self.fetches), 2)
        self.assertIs(
            (await self.call("old", {**choice, "world_id": 71001}, select=True)).status,
            ResultStatus.ERROR,
        )
        self.handler.close()
        self.assertEqual(len(self.handler.scope_choices.pending.states), 0)
        self.assertTrue(old_generation)

    async def test_ttl_and_bounded_dc_namespace(self):
        choice = await self.pending()
        self.now[0] = 301
        self.assertIs(
            (
                await self.call("expired", {**choice, "world_id": 71001}, select=True)
            ).status,
            ResultStatus.ERROR,
        )
        self.assertEqual(self.fetches, [])
        registry = MarketScopeChoices(clock=lambda: 0)
        owner = chat_owner(context("dc"))
        error = QueryResolutionError(
            "ambiguous",
            choices=(
                CatalogEntry("dc", 1, "SharedDC", "China"),
                CatalogEntry("dc", 2, "SharedDC", "Europe"),
            ),
        )
        error.parameters = {"query": "44091", "dc": "SharedDC"}
        error.defaults = QueryDefaults("cn", 2, 3)
        state = registry.publish(
            owner,
            "issuing",
            None,
            error,
            CatalogSnapshot(ScopeCatalog(error.choices), ()),
        )
        args = {
            "batch_id": state.batch_id,
            "generation": state.generation,
            "scope_kind": "dc",
            "dc_id": "2",
        }
        selected, values = registry.choose(owner, "fresh", args)
        self.assertEqual(values["dc"], 2)
        with self.assertRaises(QueryResolutionError):
            registry.choose(owner, "replay", args)
        registry.close()
        self.assertEqual(len(registry.pending.states), 0)

    async def test_handler_dc_choice_preserves_real_namespace_and_configuration(self):
        self.catalog_snapshot = SimpleNamespace(
            catalog=ScopeCatalog(
                (
                    CatalogEntry("dc", 1, "SharedDC", "China"),
                    CatalogEntry("dc", 2, "SharedDC", "Europe"),
                )
            ),
            provenance=(),
        )
        pending = await self.call(
            "dc-issuing",
            {"query": "44091", "dc": "SharedDC", "quality": "nq", "intent": "overview"},
        )
        self.assertIs(pending.status, ResultStatus.NEEDS_SELECTION)
        selection = pending.model_facts.facts["selection"]
        self.assertEqual(selection["scope_kind"], "dc")
        args = {key: selection[key] for key in ("batch_id", "generation", "scope_kind")}
        before = (self.config_calls, self.catalog_calls)
        result = await self.call("dc-confirm", {**args, "dc_id": "2"}, select=True)
        self.assertIs(result.status, ResultStatus.SUCCESS)
        self.assertEqual(
            (
                self.fetches[0].scope.kind,
                self.fetches[0].scope.target,
                self.fetches[0].scope.regions,
            ),
            ("dc", 2, ("Europe",)),
        )
        self.assertEqual(
            (self.fetches[0].quality, self.fetches[0].intent), ("nq", "overview")
        )
        self.assertEqual((self.config_calls, self.catalog_calls), before)
