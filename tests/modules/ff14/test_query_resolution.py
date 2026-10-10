"""Offline Q1 fixtures: every catalog ID below is synthetic, not a live directory."""

from __future__ import annotations

import asyncio
import unittest
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

from ygl_test_subject.modules.ff14.features.item_sources import ItemSourceClient
from ygl_test_subject.modules.ff14.models import ItemCandidate
from ygl_test_subject.modules.ff14.query_resolution import (
    GLOBAL_REGIONS,
    CandidateBatch,
    CandidateRegistry,
    CatalogEntry,
    MarketQueryResolver,
    QueryCoordinator,
    QueryDefaults,
    QueryResolutionError,
    QueryTicket,
    ScopeCatalog,
    TrustedOwner,
    command_parameters,
    natural_parameters,
    request_plan,
    structured_parameters,
)

from yomihime_game_link_sdk.results import ErrorCode

from .test_items import _FakeHttp


def catalog():
    # Historical real World 1043 evidence is deliberately not substituted for
    # this injectable synthetic fixture or claimed to be current acceptance.
    return ScopeCatalog(
        (
            CatalogEntry("dc", 70001, "猫小胖", "China", aliases=("猫区",)),
            CatalogEntry("dc", 70002, "陆行鸟", "China", aliases=("鸟区",)),
            CatalogEntry("dc", 70003, "Synthetic Europe DC", "Europe"),
            CatalogEntry("world", 71001, "紫水栈桥", "China", 70001, ("紫水",)),
            CatalogEntry("world", 71002, "Synthetic World", "Europe", 70003),
            CatalogEntry("world", 71003, "Shared", "China", 70001, ("duplicate",)),
            CatalogEntry("world", 71004, "Shared", "China", 70002, ("duplicate",)),
            CatalogEntry("dc", 70004, "Cloud", "NA-Cloud-DC Beta"),
            CatalogEntry("world", 71005, "Cloud World", "NA-Cloud-DC Beta", 70004),
        )
    )


class _Config:
    def __init__(self):
        self.region = "cn"
        self.core_revision = 2
        self.module_revision = 3
        self.calls = 0

    async def current(self):
        self.calls += 1
        return SimpleNamespace(
            revision=self.module_revision,
            values={
                "core_defaults": {
                    "default_region": self.region,
                    "revision": self.core_revision,
                }
            },
        )


class _Search:
    def __init__(self, values=None):
        self.values = (
            values
            if values is not None
            else ((ItemCandidate(90001, "A"), ItemCandidate(90002, "B")), False)
        )
        self.calls = []

    async def search(self, query, *, exact=False):
        self.calls.append((query, exact))
        return self.values


class NaturalConfirmationTests(unittest.TestCase):
    continuations = (
        "第7个",
        "第七个",
        "第十个",
        "最后一个",
        "不是",
        "不要",
        "不选",
        "后者",
        "第0个",
        "第10个",
        "第十一",
        "第十一個",
        "第十一项",
        "第两百个",
        "第-1个",
        "第1.5个",
        "第９９９个",
        "前者",
        "前一个",
        "下一个",
        "那个？",
    )

    def setUp(self):
        self.now = 0
        self.registry = CandidateRegistry(clock=lambda: self.now)
        self.owner = TrustedOwner("user", "session", "llm_tool:adapter")
        self.event = "source-ref"
        self.query = MarketQueryResolver(catalog()).parse(
            {"query": "牛排", "server": "71001", "quality": "hq", "intent": "min"},
            QueryDefaults("cn", 2, 3),
        )
        self.publish()

    def publish(self, names=("犎牛牛排", "另一种牛排")):
        ticket = self.registry.begin(self.owner, source_ref=self.event)
        self.batch = self.registry.publish(
            ticket,
            self.query,
            tuple(ItemCandidate(90001 + i, name) for i, name in enumerate(names)),
            False,
        )

    def choose(self, text, *, event_ref=None, owner=None, item_id=90001):
        return self.registry.choose_confirmed(
            owner or self.owner,
            self.batch.batch_id,
            self.batch.generation,
            item_id,
            event_ref=event_ref or "new-ref",
            text=text,
        )

    def test_closed_positive_templates_restore_frozen_context_and_consume(self):
        for text in (
            "犎牛牛排",
            "犎牛牛排那个。",
            "选犎牛牛排",
            "选择犎牛牛排",
            "就犎牛牛排",
            "就选犎牛牛排",
            "选犎牛牛排",
            "就犎牛牛排！",
            "就选犎牛牛排",
            "选择物品 90001",
        ):
            with self.subTest(text=text):
                self.publish()
                selected = self.choose(text)
                self.assertEqual(selected.item_id, 90001)
                self.assertEqual(selected.item_name, "犎牛牛排")
                self.assertEqual(selected.original_query, "牛排")
                for field in (
                    "scope",
                    "quality",
                    "intent",
                    "core_revision",
                    "module_revision",
                ):
                    self.assertEqual(
                        getattr(selected, field), getattr(self.query, field)
                    )
                with self.assertRaises(QueryResolutionError):
                    self.choose(text)

    def test_rejections_preserve_ticket_and_do_not_let_model_choose(self):
        for text in (
            "犎牛牛排？",
            "不是犎牛牛排",
            "不要犎牛牛排",
            "犎牛牛排或另一种牛排",
            "牛排那个",
            "第一/第二个",
            "第二个",
            "犎牛牛排2个",
            "刚才那个",
        ):
            with self.subTest(text=text), self.assertRaises(QueryResolutionError):
                self.choose(text)
        with self.assertRaises(QueryResolutionError):
            self.choose("犎牛牛排", event_ref=self.event)
        with self.assertRaises(QueryResolutionError):
            self.choose("犎牛牛排", item_id=90002)
        self.assertEqual(self.choose("犎牛牛排那个。").item_id, 90001)
        self.publish(("同名牛排", "同名牛排"))
        with self.assertRaises(QueryResolutionError):
            self.choose("同名牛排")
        self.assertEqual(self.choose("选择物品 90001").item_id, 90001)

    def test_owner_ttl_absence_and_new_query_invalidation(self):
        for owner in (
            TrustedOwner("other", "session", "llm_tool:adapter"),
            TrustedOwner("user", "other", "llm_tool:adapter"),
            TrustedOwner("user", "session", "adapter"),
        ):
            with self.assertRaises(QueryResolutionError):
                self.choose("犎牛牛排", owner=owner)
        self.now = 301
        with self.assertRaises(QueryResolutionError):
            self.choose("犎牛牛排")

        for text in (
            "犎牛牛排那个。",
            "选择犎牛牛排",
            "选择物品 90001",
            "第二个",
            "刚才那个",
        ):
            with self.subTest(text=text), self.assertRaises(QueryResolutionError):
                self.registry.tool_query(self.owner, "new-ref", text, "犎牛牛排")
        self.publish()
        self.assertIsNone(
            self.registry.tool_query(self.owner, "new-ref", "新矿石多少钱", "新矿石")
        )
        self.registry.begin(self.owner, source_ref="new-ref")
        with self.assertRaises(QueryResolutionError):
            self.choose("犎牛牛排")

    def test_names_with_spaces_normalize_and_duplicate_names_remain_ambiguous(self):
        self.publish(("Synthetic Steak", "Other Steak"))
        self.assertEqual(self.choose("就选 SYNTHETIC   STEAK。").item_id, 90001)
        self.publish(("Synthetic Steak", "synthetic   steak"))
        with self.assertRaises(QueryResolutionError):
            self.choose("Synthetic Steak那个")
        with self.assertRaises(QueryResolutionError):
            self.choose("不是 Synthetic Steak")
        self.assertEqual(self.choose("选择物品 90001").item_id, 90001)

    def test_pending_query_cannot_recalculate_context_from_confirmation(self):
        self.assertIs(
            self.registry.tool_query(
                self.owner, self.event, "牛排国服哪里最便宜？", "牛排"
            ),
            self.batch,
        )
        for text, query in (
            ("犎牛牛排那个。", "犎牛牛排"),
            ("犎牛牛排那个。", "牛排"),
            ("犎牛牛排那个。", "犎"),
            ("犎牛牛排？", "犎牛牛排"),
            ("不是犎牛牛排", "犎牛牛排"),
            ("不是犎牛", "犎牛"),
            ("就犎牛", "犎牛"),
            ("犎牛？", "犎牛"),
            ("犎牛牛排或另一种牛排", "另一种牛排"),
            ("物品ID 90001", "90001"),
            ("第二个", "第二个"),
            ("牛排国服哪里最便宜？", "犎牛牛排"),
        ):
            with (
                self.subTest(text=text, query=query),
                self.assertRaises(QueryResolutionError),
            ):
                self.registry.tool_query(self.owner, "new-ref", text, query)
        self.assertEqual(self.choose("犎牛牛排那个。").scope, self.query.scope)

    def test_original_word_only_same_event_is_retry_new_questions_are_fresh(self):
        self.assertIs(
            self.registry.tool_query(self.owner, self.event, "牛排什么价？", "牛排"),
            self.batch,
        )
        self.assertIsNone(
            self.registry.tool_query(self.owner, "new-ref", "牛排国际服什么价？", "牛排")
        )
        self.assertIsNone(
            self.registry.tool_query(
                self.owner, "new-ref", "牛排国服哪里最便宜？", "牛排"
            )
        )
        with self.assertRaises(QueryResolutionError):
            self.registry.tool_query(self.owner, "new-ref", "牛排？", "牛排")
        self.assertEqual(self.choose("犎牛牛排那个。").scope, self.query.scope)

    def test_all_positional_denial_messages_refuse_before_begin_in_every_state(self):
        # Candidate capacity is not a boundary on rejected ordinal syntax.
        for state in ("active", "absent", "expired", "consumed"):
            for text in self.continuations:
                with self.subTest(state=state, text=text):
                    self.publish()
                    ticket = QueryTicket(self.owner, self.batch.generation)
                    if state == "absent":
                        self.registry.finish(ticket, self.query)
                    elif state == "expired":
                        self.now += 301
                    elif state == "consumed":
                        self.registry.choose_confirmed(
                            self.owner,
                            self.batch.batch_id,
                            self.batch.generation,
                            90001,
                            event_ref="new-ref",
                            text="犎牛牛排",
                            retain=True,
                        )
                    with self.assertRaises(QueryResolutionError):
                        self.registry.tool_query(self.owner, "new-ref", text, text)
                    if state == "active":
                        self.assertIs(self.registry._current(ticket).batch, self.batch)
                        self.assertEqual(
                            self.choose("犎牛牛排").scope, self.query.scope
                        )
                    elif state == "consumed":
                        pending = self.registry._current(ticket)
                        self.assertIsNone(pending.batch)
                        self.assertIs(pending.consumed_batch, self.batch)
                        self.registry._release_confirmed(ticket)
                        self.assertEqual(self.registry.size, 0)
                    else:
                        self.assertEqual(self.registry.size, 0)

    def test_positional_query_token_refuses_but_unrelated_full_names_remain_queries(
        self,
    ):
        for text, query in (
            ("第七个多少钱", "第七个"),
            ("看看最后一个价格", "最后一个"),
            ("第7个吗？", "第7个"),
            ("帮我看后者", "后者"),
            ("不是吧", "不是"),
            ("我说不要", "不要"),
            ("我说不选", "不选"),
            ("我说不要犎牛", "不要犎牛"),
        ):
            with self.subTest(text=text), self.assertRaises(QueryResolutionError):
                self.registry.tool_query(self.owner, "new-ref", text, query)
        for name in (
            "第七天堂牛排",
            "第七个勇士徽章",
            "最后一个传说牛排",
            "前者的矿石",
        ):
            self.assertIsNone(
                self.registry.tool_query(self.owner, "new-ref", f"查询{name}价格", name)
            )
        self.publish(("第7个", "另一种牛排"))
        with self.assertRaises(QueryResolutionError):
            self.choose("选第7个")
        self.assertEqual(self.choose("选择物品 90001").item_id, 90001)


class ParsingTests(unittest.TestCase):
    def setUp(self):
        self.resolver = MarketQueryResolver(catalog())
        self.defaults = QueryDefaults("cn", 2, 3)

    def parse(self, **values):
        return self.resolver.parse({"query": "犎牛牛排", **values}, self.defaults)

    def test_name_id_defaults_and_immutable_contract(self):
        query = self.parse()
        self.assertEqual(
            (query.query, query.item_id, query.quality, query.intent),
            ("犎牛牛排", None, "all", "overview"),
        )
        self.assertEqual(
            (query.scope.regions, query.scope.source), (("China",), "default")
        )
        self.assertEqual((query.core_revision, query.module_revision), (2, 3))
        with self.assertRaises(FrozenInstanceError):
            query.quality = "hq"
        self.assertEqual(self.parse(query="44091").item_id, 44091)

    def test_explicit_scope_overrides_default_and_preserves_narrowest(self):
        for token in ("紫水栈桥", "紫水", "71001", 71001):
            query = self.parse(server=token, dc="猫区", region="China")
            self.assertEqual((query.scope.kind, query.scope.target), ("world", 71001))
        self.assertEqual(
            self.parse(server="Synthetic World").scope.regions, ("Europe",)
        )
        global_defaults = QueryDefaults("global", 4, 5)
        self.assertEqual(
            self.resolver.parse(
                {"query": "1", "dc": "鸟区"}, global_defaults
            ).scope.regions,
            ("China",),
        )
        self.assertEqual(self.parse(dc=70002, region="cn").scope.kind, "dc")

    def test_contradictory_scope_never_expands_world(self):
        for params in (
            {"server": "紫水", "dc": "陆行鸟"},
            {"server": "紫水", "region": "global"},
            {"dc": "猫小胖", "region": "Europe"},
        ):
            with self.subTest(params=params), self.assertRaises(QueryResolutionError):
                self.parse(**params)

    def test_ambiguous_alias_and_names_have_choices(self):
        for token in ("Shared", "duplicate"):
            with (
                self.subTest(token=token),
                self.assertRaises(QueryResolutionError) as raised,
            ):
                self.parse(server=token)
            self.assertEqual(
                tuple(entry.id for entry in raised.exception.choices), (71003, 71004)
            )
        self.assertEqual(self.parse(server=71004).scope.target, 71004)

    def test_unsupported_and_unavailable_catalog(self):
        with self.assertRaises(QueryResolutionError) as raised:
            self.parse(server="Cloud World")
        self.assertIs(raised.exception.code, ErrorCode.UNSUPPORTED)
        resolver = MarketQueryResolver(ScopeCatalog((), available=False))
        with self.assertRaises(QueryResolutionError) as raised:
            resolver.parse({"query": "1", "server": "紫水"}, self.defaults)
        self.assertIs(raised.exception.code, ErrorCode.UPSTREAM_ERROR)
        self.assertEqual(
            resolver.parse({"query": "1"}, self.defaults).scope.regions, ("China",)
        )

    def test_strict_parameters_types_null_keys_controls_lengths(self):
        cases = [
            {"query": None},
            {"query": 44091},
            {"query": True},
            {"query": ""},
            {"query": "0"},
            {"query": "2147483648"},
            {"query": "x" * 121},
            {"query": "\tname"},
            {"query": "name\x85"},
            {"query": 'a"b'},
            {"quality": None},
            {"quality": "HQ"},
            {"quality": True},
            {"quality": "maybe"},
            {"intent": None},
            {"intent": "cheapest"},
            {"intent": 1},
            {"server": None},
            {"server": True},
            {"server": 0},
            {"server": []},
            {"dc": None},
            {"region": None},
            {"region": "한국"},
            {"region": "Cloud"},
            {"server": "unknown"},
            {"dc": "unknown"},
            {"server": "紫水\n"},
            {"region": "cn\x00"},
            {"owner": "fake"},
            {"qualty": "hq"},
        ]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(QueryResolutionError):
                self.parse(**values)

    def test_structured_item_is_literal_with_spaces_and_scope_words(self):
        parameters = structured_parameters(
            {"query": "Synthetic 国服 HQ Ore", "quality": "nq"}
        )
        query = self.resolver.parse(parameters, self.defaults)
        self.assertEqual(query.query, "Synthetic 国服 HQ Ore")
        self.assertEqual(query.quality, "nq")

    def test_command_quotes_and_key_boundaries(self):
        parameters = command_parameters(
            '"Synthetic HQ Ore" dc="Synthetic Europe DC" quality=hq intent=min'
        )
        query = self.resolver.parse(parameters, self.defaults)
        self.assertEqual(
            (query.query, query.scope.target, query.quality, query.intent),
            ("Synthetic HQ Ore", 70003, "hq", "min"),
        )
        for literal in ("HQ", "region=cn Ore", "国服"):
            query = self.resolver.parse(
                command_parameters(f'"{literal}"'), self.defaults
            )
            self.assertEqual(query.query, literal)
        for text in (
            "name qualty=hq",
            "name server=unknown",
            "name quality=wrong",
            "name region=wrong",
            "name HQ",
            "name region=",
            "name quality=hq quality=nq",
            "name query=other",
            '"broken',
        ):
            with self.subTest(text=text), self.assertRaises(QueryResolutionError):
                self.resolver.parse(command_parameters(text), self.defaults)

    def test_explicit_natural_grammars(self):
        expected = (
            ("查一下紫水栈桥的犎牛牛排多少钱", "world", 71001, "all", "overview"),
            ("陆行鸟大区犎牛牛排 HQ 什么价", "dc", 70002, "hq", "overview"),
            ("犎牛牛排国服哪里最便宜", "region", None, "all", "min"),
            (
                '"Synthetic Europe DC"大区 "HQ Ore" NQ 什么价',
                "dc",
                70003,
                "nq",
                "overview",
            ),
        )
        for text, kind, target, quality, intent in expected:
            with self.subTest(text=text):
                query = self.resolver.parse(natural_parameters(text), self.defaults)
                self.assertEqual(
                    (query.scope.kind, query.scope.target, query.quality, query.intent),
                    (kind, target, quality, intent),
                )
        for text in (
            "犎牛牛排多少钱",
            "陆行鸟大区犎牛牛排 maybe 什么价",
            "查一下未知的犎牛牛排多少钱",
            "查一下紫水的HQ Ore多少钱",
            "犎牛牛排国服哪里最便宜 quality=hq",
        ):
            with self.subTest(text=text), self.assertRaises(QueryResolutionError):
                self.resolver.parse(natural_parameters(text), self.defaults)

    def test_bounded_request_plans_never_send_global(self):
        query = self.parse(query="44091", region="global", intent="min")
        plan = request_plan(query)
        self.assertEqual(plan.scopes, GLOBAL_REGIONS)
        self.assertNotIn("global", plan.scopes)
        self.assertEqual(plan.endpoint, "aggregated")
        self.assertEqual(request_plan(self.parse(query="1")).scopes, ("China",))
        for quality in ("all", "hq", "nq"):
            plan = request_plan(
                self.parse(query="1", server="紫水", quality=quality, intent="listings")
            )
            self.assertEqual(plan.scopes, (71001,))
            self.assertEqual(
                dict(plan.parameters).get("hq"),
                {"all": None, "hq": "true", "nq": "false"}[quality],
            )
        with self.assertRaises(QueryResolutionError):
            request_plan(self.parse())

    def test_natural_delimiters_inside_quoted_scope_names_are_literal(self):
        resolver = MarketQueryResolver(
            ScopeCatalog(
                (
                    CatalogEntry("dc", 72001, "测试大区", "China"),
                    CatalogEntry("world", 72002, "测试的世界", "China", 72001),
                )
            )
        )
        for text, kind, target, quality in (
            ('查一下"测试的世界"的"犎牛牛排"多少钱', "world", 72002, "all"),
            ('"测试大区"大区 "犎牛牛排" HQ 什么价', "dc", 72001, "hq"),
        ):
            with self.subTest(text=text):
                query = resolver.parse(natural_parameters(text), self.defaults)
                self.assertEqual(
                    (query.scope.kind, query.scope.target, query.quality),
                    (kind, target, quality),
                )
                self.assertEqual(query.query, "犎牛牛排")

    def test_natural_unquoted_adjacent_quality_never_becomes_item_default(self):
        for text in (
            "犎牛牛排HQ国服哪里最便宜",
            "查一下紫水的犎牛牛排NQ多少钱",
        ):
            with self.subTest(text=text), self.assertRaises(QueryResolutionError):
                self.resolver.parse(natural_parameters(text), self.defaults)

    def test_natural_quoted_item_keeps_quality_and_scope_words_literal(self):
        for text, name, quality, intent in (
            ('"犎牛牛排HQ国服"国服哪里最便宜', "犎牛牛排HQ国服", "all", "min"),
            (
                '查一下紫水的"犎牛牛排NQ的国服"多少钱',
                "犎牛牛排NQ的国服",
                "all",
                "overview",
            ),
            (
                '陆行鸟大区 "大区 HQ NQ 国服" HQ 什么价',
                "大区 HQ NQ 国服",
                "hq",
                "overview",
            ),
        ):
            with self.subTest(text=text):
                query = self.resolver.parse(natural_parameters(text), self.defaults)
                self.assertEqual(
                    (query.query, query.quality, query.intent), (name, quality, intent)
                )


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 100.0
        self.registry = CandidateRegistry(clock=lambda: self.now)
        self.coordinator = QueryCoordinator(
            MarketQueryResolver(catalog()), self.registry
        )
        self.config = _Config()
        self.services = SimpleNamespace(config=self.config)
        self.owner = TrustedOwner("user-a", "session-a", "adapter-a")

    async def resolve(self, params=None, source=None, owner=None, **kwargs):
        return await self.coordinator.resolve(
            owner or self.owner,
            params or {"query": "犎牛牛排"},
            self.services,
            source or _Search(),
            **kwargs,
        )

    async def test_capture_once_and_selection_keeps_context_after_default_change(self):
        batch = await self.resolve(
            {"query": "犎牛牛排", "quality": "hq", "intent": "min"}
        )
        self.assertIsInstance(batch, CandidateBatch)
        self.assertEqual(self.config.calls, 1)
        self.config.region, self.config.core_revision, self.config.module_revision = (
            "global",
            4,
            5,
        )
        selected = self.coordinator.choose(
            self.owner, batch.batch_id, batch.generation, 90002
        )
        self.assertEqual((selected.item_id, selected.query), (90002, "90002"))
        self.assertEqual(
            (
                selected.scope.regions,
                selected.quality,
                selected.intent,
                selected.core_revision,
                selected.module_revision,
            ),
            (("China",), "hq", "min", 2, 3),
        )
        self.assertEqual(self.config.calls, 1)
        fresh = await self.resolve({"query": "44091"})
        self.assertEqual(
            (fresh.scope.regions, fresh.core_revision, fresh.module_revision),
            (GLOBAL_REGIONS, 4, 5),
        )
        self.assertEqual(self.config.calls, 2)

    async def test_all_entry_functions_resolve_the_same_contract(self):
        cases = (
            ("structured", {"query": "犎牛牛排", "server": "紫水栈桥"}),
            ("page", {"query": "犎牛牛排", "server": "紫水栈桥"}),
            ("tool", {"query": "犎牛牛排", "server": "紫水栈桥"}),
            ("command", "犎牛牛排 server=紫水栈桥"),
            ("natural", "查一下紫水栈桥的犎牛牛排多少钱"),
        )
        queries = []
        for entry, payload in cases:
            batch = await self.resolve(payload, entry=entry)
            queries.append(batch.query)
        self.assertTrue(all(query == queries[0] for query in queries))

    async def test_owner_user_session_origin_isolation_and_replay(self):
        batch = await self.resolve()
        for owner in (
            TrustedOwner("user-b", "session-a", "adapter-a"),
            TrustedOwner("user-a", "session-b", "adapter-a"),
            TrustedOwner("user-a", "session-a", "adapter-b"),
        ):
            with self.subTest(owner=owner), self.assertRaises(QueryResolutionError):
                self.coordinator.choose(owner, batch.batch_id, batch.generation, 90001)
        for item_id in (True, "90001", 90003):
            with self.subTest(item_id=item_id), self.assertRaises(QueryResolutionError):
                self.coordinator.choose(
                    self.owner, batch.batch_id, batch.generation, item_id
                )
        for batch_id, generation in (
            ("bad", batch.generation),
            (batch.batch_id, "bad"),
        ):
            with self.assertRaises(QueryResolutionError):
                self.coordinator.choose(self.owner, batch_id, generation, 90001)
        self.coordinator.choose(self.owner, batch.batch_id, batch.generation, 90001)
        with self.assertRaises(QueryResolutionError):
            self.coordinator.choose(self.owner, batch.batch_id, batch.generation, 90001)

    async def test_anonymous_and_json_owner_cannot_claim_isolation(self):
        for owner in (
            None,
            {"user": "user-a", "session": "session-a", "origin": "adapter-a"},
        ):
            with self.assertRaises(QueryResolutionError) as raised:
                await self.coordinator.resolve(
                    owner, {"query": "1"}, self.services, _Search()
                )
            self.assertIs(raised.exception.code, ErrorCode.UNSUPPORTED)
        with self.assertRaises(QueryResolutionError):
            await self.resolve({"query": "1", "owner": {"user": "fake"}})
        self.assertEqual(
            self.config.calls, 1
        )  # invalid query still reads only one snapshot

    async def test_new_invalid_queries_and_transcription_failures_invalidate_previous(
        self,
    ):
        for payload, entry in (
            ({"query": "1", "quality": None}, "structured"),
            ("name quality=wrong", "command"),
            ("多少钱", "natural"),
        ):
            batch = await self.resolve()
            with self.assertRaises(QueryResolutionError):
                await self.resolve(payload, entry=entry)
            with self.assertRaises(QueryResolutionError):
                self.coordinator.choose(
                    self.owner, batch.batch_id, batch.generation, 90001
                )

    async def test_late_search_never_restores_old_generation(self):
        started, released = asyncio.Event(), asyncio.Event()

        class Delayed(_Search):
            async def search(inner, query, *, exact=False):
                started.set()
                await released.wait()
                return inner.values

        late = asyncio.create_task(self.resolve(source=Delayed()))
        await started.wait()
        fresh = await self.resolve({"query": "1"})
        released.set()
        with self.assertRaises(QueryResolutionError):
            await late
        self.assertEqual(fresh.item_id, 1)
        self.assertEqual(self.registry.size, 0)

    async def test_generation_precedes_slow_config_capture_and_fences_direct_id(self):
        started, released = asyncio.Event(), asyncio.Event()
        original = self.config.current
        calls = 0

        async def delayed_current():
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await released.wait()
            return await original()

        self.config.current = delayed_current
        late = asyncio.create_task(self.resolve({"query": "1"}))
        await started.wait()
        current = await self.resolve()
        released.set()
        with self.assertRaises(QueryResolutionError):
            await late
        selected = self.coordinator.choose(
            self.owner, current.batch_id, current.generation, 90001
        )
        self.assertEqual(selected.item_id, 90001)

    async def test_exact_empty_fuzzy_truncated_and_not_found(self):
        class Sequence(_Search):
            async def search(inner, query, *, exact=False):
                inner.calls.append((query, exact))
                return ((), False) if exact else ((ItemCandidate(90001, "A"),), False)

        source = Sequence()
        query = await self.resolve(source=source)
        self.assertEqual(query.item_id, 90001)
        self.assertEqual(source.calls, [("犎牛牛排", True), ("犎牛牛排", False)])
        for values in (((ItemCandidate(90001, "A"),), True), ((), True)):
            source = _Search(values)
            batch = await self.resolve(source=source)
            self.assertIsInstance(batch, CandidateBatch)
            self.assertTrue(batch.truncated)
            self.assertEqual(len(source.calls), 1)
        with self.assertRaises(QueryResolutionError) as raised:
            await self.resolve(source=_Search(((), False)))
        self.assertIs(raised.exception.code, ErrorCode.NOT_FOUND)

    async def test_real_item_source_request_contract_without_garland_lookup(self):
        for exact, responses, expected in (
            (
                True,
                [{"results": [{"row_id": 90001, "fields": {"Name": "Synthetic"}}]}],
                ['Name="Synthetic"'],
            ),
            (
                False,
                [
                    {"results": []},
                    {"results": [{"row_id": 90001, "fields": {"Name": "Synthetic"}}]},
                ],
                ['Name="Synthetic"', 'Name~"Synthetic"'],
            ),
        ):
            with self.subTest(exact=exact):
                http = _FakeHttp(responses)
                result = await self.resolve(
                    {"query": "Synthetic"}, source=ItemSourceClient(http)
                )
                self.assertEqual(result.item_id, 90001)
                self.assertEqual(
                    [dict(request.query)["query"] for request in http.requests],
                    expected,
                )
                self.assertTrue(
                    all(request.path == "/api/search" for request in http.requests)
                )

    async def test_s2a_message_guard_refuses_after_config_and_search_without_publish(self):
        from yomihime_game_link_sdk.errors import AccessDenied
        for boundary in ("config", "search"):
            with self.subTest(boundary=boundary):
                registry = CandidateRegistry(clock=lambda: self.now)
                coordinator = QueryCoordinator(self.coordinator.resolver, registry)
                effects = []
                original_publish, original_finish = registry.publish, registry.finish
                def publish(*args, **kwargs):
                    effects.append("publish")
                    return original_publish(*args, **kwargs)
                def finish(*args, **kwargs):
                    effects.append("finish")
                    return original_finish(*args, **kwargs)
                registry.publish, registry.finish = publish, finish
                calls = 0
                async def guard():
                    nonlocal calls
                    calls += 1
                    if calls == (1 if boundary == "config" else 2):
                        raise AccessDenied("source_revoked")
                with self.assertRaises(AccessDenied):
                    await coordinator.resolve(
                        self.owner, {"query": "Synthetic"}, self.services,
                        _Search(), guard=guard
                    )
                self.assertEqual(effects, [])
                self.assertIsNone(next(iter(registry._pending.values())).batch)

    async def test_resource_bounds_expiry_and_restart(self):
        registry = CandidateRegistry(
            clock=lambda: self.now, capacity=2, max_candidates=2
        )
        coordinator = QueryCoordinator(self.coordinator.resolver, registry)
        owners = [TrustedOwner(str(i), "s", "o") for i in range(3)]
        batches = []
        source = _Search(
            (tuple(ItemCandidate(90000 + i, str(i)) for i in range(8)), False)
        )
        for owner in owners:
            batches.append(
                await coordinator.resolve(
                    owner, {"query": "Synthetic"}, self.services, source
                )
            )
        self.assertEqual(registry.size, 2)
        self.assertEqual(len(batches[-1].candidates), 2)
        self.assertTrue(batches[-1].truncated)
        with self.assertRaises(QueryResolutionError):
            coordinator.choose(
                owners[0], batches[0].batch_id, batches[0].generation, 90000
            )
        self.now += 300
        self.assertEqual(registry.size, 0)
        with self.assertRaises(QueryResolutionError):
            coordinator.choose(
                owners[2], batches[2].batch_id, batches[2].generation, 90000
            )
        restart = QueryCoordinator(self.coordinator.resolver, CandidateRegistry())
        with self.assertRaises(QueryResolutionError):
            restart.choose(owners[2], batches[2].batch_id, batches[2].generation, 90000)
        for kwargs in (
            {"capacity": 0},
            {"capacity": 129},
            {"max_candidates": 7},
            {"ttl": 301},
            {"ttl": True},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                CandidateRegistry(**kwargs)
