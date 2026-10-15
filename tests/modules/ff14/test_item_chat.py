"""Offline item tool identity, continuation, lifecycle and fact-budget tests."""

from __future__ import annotations

import asyncio
import json
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.public_result import public_fact_strings
from ygl_test_subject.modules.ff14.features.item_chat import (
    ITEM_SELECT,
    _record_facts,
)
from ygl_test_subject.modules.ff14.features.item_sources import (
    ItemLookupSnapshot,
    ItemSourceClient,
)
from ygl_test_subject.modules.ff14.features.items import ItemLookup
from ygl_test_subject.modules.ff14.models import (
    AcquisitionKind,
    AcquisitionRoute,
    ItemAmount,
    ItemCandidate,
    ItemRecord,
)
from ygl_test_subject.modules.ff14.query_resolution import (
    CandidateRegistry,
    ItemQuery,
    TrustedOwner,
)

from yomihime_game_link_sdk.contexts import (
    InvocationOrigin,
    InvocationView,
    MessageContext,
)
from yomihime_game_link_sdk.errors import SourceHttpError
from yomihime_game_link_sdk.results import ErrorCode, FactDocument, ResultStatus

from .test_items import _FakeHttp

FIXTURE = json.loads(
    (Path(__file__).parents[2] / "fixtures/ff14/items.json").read_text(encoding="utf-8")
)
NAMES = ("Sample Ore Alpha", "Sample Ore Beta")


def context(
    event="request", *, user="alice", session="chat", adapter="test", select=False
):
    return InvocationView(
        event,
        InvocationOrigin.LLM_TOOL,
        user,
        session,
        "ff14/ff14",
        1,
        1,
        adapter_id=adapter,
        capability_id=ITEM_SELECT if select else "item.lookup",
    )


class _Services:
    def __init__(self, http):
        self.http = http
        self.messages = {}
        self.scopes = self

    async def bind(self, invocation):
        async def read():
            return self.messages[invocation.invocation_id]

        return SimpleNamespace(http=self.http, message=SimpleNamespace(read=read))


class ItemChatTests(unittest.IsolatedAsyncioTestCase):
    async def test_consumed_confirmation_and_source_cannot_restart_after_success(self):
        pending = await self.pending()
        self.detail_responses()
        chosen = await self.call("第一个", event="confirm", select=self.choice(pending))
        self.assertIs(chosen.status, ResultStatus.SUCCESS)
        before = len(self.http.requests)
        for event in ("confirm", "request"):
            for action in ("query", "correction"):
                self.detail_responses()
                self.services.messages[event] = MessageContext(
                    "same trusted event", event
                )
                result = await self.handler.invoke(
                    context(event), {"query": "90001", "action": action}
                )
                self.assertIs(result.status, ResultStatus.ERROR)
                self.assertEqual(len(self.http.requests), before)
                self.http.responses.clear()
        self.detail_responses()
        fresh = await self.call("重新查询90001", "90001", event="new-request")
        self.assertIs(fresh.status, ResultStatus.SUCCESS)

    def setup_handler(self, responses=()):
        self.http = _FakeHttp(list(responses))
        self.services = _Services(self.http)
        self.handler = ItemLookup(self.services)
        self.addCleanup(self.handler.close)

    async def call(
        self, text, query="Ore", *, event="request", select=None, **identity
    ):
        ctx = context(event, select=select is not None, **identity)
        self.services.messages[event] = MessageContext(text, event)
        result = await self.handler.invoke(ctx, select or {"query": query})
        validate_contract(result)
        list(public_fact_strings(result.model_facts))
        return result

    async def pending(self):
        exact = {
            "results": [
                {"row_id": item_id, "fields": {"Name": name}}
                for item_id, name in zip((90001, 90002), NAMES)
            ]
        }
        self.setup_handler([exact])
        return await self.call("查一下Ore的信息")

    @staticmethod
    def choice(result, item_id=90001):
        batch = result.model_facts.facts["selection"]
        return {
            "batch_id": batch["batch_id"],
            "generation": batch["generation"],
            "item_id": item_id,
        }

    def detail_responses(self):
        self.http.responses.extend(
            [
                FIXTURE["xivcdn"]["item_detail"],
                {
                    "item": {"id": 90001, "nodes": ["11"]},
                    "partials": [
                        {"type": "node", "id": "11", "obj": {"n": "Synthetic Node"}}
                    ],
                },
            ]
        )

    async def test_exact_first_and_structured_success_routes(self):
        self.setup_handler(
            [{"results": [{"row_id": 90001, "fields": {"Name": NAMES[0]}}]}]
        )
        self.detail_responses()
        result = await self.call("查一下Sample Ore Alpha怎么获得", NAMES[0])
        self.assertIs(result.status, ResultStatus.SUCCESS)
        facts = result.model_facts.facts["item"]
        self.assertEqual(
            (facts["item_id"], facts["item_level"], facts["equip_level"]), (90001, 7, 2)
        )
        self.assertEqual(facts["routes"][0]["kind"], "node")
        self.assertIn("Synthetic Node", facts["routes"][0]["details"])
        self.assertFalse(facts["routes_complete"])
        self.assertEqual(len(self.http.requests), 3)
        self.assertEqual(
            dict(self.http.requests[0].query)["query"], 'Name="Sample Ore Alpha"'
        )
        self.assertNotIn("scope", facts)
        self.assertNotIn("intent", facts)

    async def test_numeric_id_and_missing_fields_remain_unknown_partial(self):
        self.setup_handler(
            [
                {"row_id": 90001, "fields": {"Name": NAMES[0]}},
                SourceHttpError("upstream_error", status_code=503),
            ]
        )
        result = await self.call("查询物品90001的信息", "90001")
        self.assertIs(result.status, ResultStatus.PARTIAL_SUCCESS)
        item = result.model_facts.facts["item"]
        for field in ("description", "item_level", "equip_level"):
            self.assertIsNone(item[field])
            self.assertIn(field, item["missing"])
        self.assertEqual(item["routes_state"], "unknown")
        self.assertEqual(len(self.http.requests), 2)
        self.assertTrue(item["warnings"])

    async def test_fuzzy_fallback_candidates_do_not_lookup_detail(self):
        self.setup_handler(
            [
                {"results": []},
                FIXTURE["xivcdn"]["search_first_page"],
                FIXTURE["xivcdn"]["search_second_page"],
            ]
        )
        result = await self.call("查一下Ore")
        self.assertIs(result.status, ResultStatus.NEEDS_SELECTION)
        self.assertEqual(len(self.http.requests), 3)
        self.assertEqual(dict(self.http.requests[1].query)["query"], 'Name~"Ore"')
        self.assertEqual(result.model_facts.facts["selection"]["kind"], "item_lookup")

    async def test_no_result_and_upstream_errors_have_generic_facts(self):
        cases = [
            ([{"results": []}, {"results": []}], "Ore", "查Ore", ErrorCode.NOT_FOUND),
            (
                [SourceHttpError("upstream_error", status_code=429)],
                "Ore",
                "查Ore",
                ErrorCode.RATE_LIMITED,
            ),
            (
                [SourceHttpError("upstream_error", status_code=404)],
                "Ore",
                "查Ore",
                ErrorCode.UPSTREAM_ERROR,
            ),
            (
                [SourceHttpError("upstream_error", status_code=404)],
                "90001",
                "物品ID90001",
                ErrorCode.NOT_FOUND,
            ),
            ([b"invalid"], "Ore", "查Ore", ErrorCode.UNPARSED),
        ]
        for responses, query, message, code in cases:
            with self.subTest(code=code, query=query):
                self.setup_handler(responses)
                result = await self.call(message, query)
                self.assertIs(result.error.code, code)
                self.assertEqual(set(result.model_facts.facts), {"status", "error"})
                self.assertNotIn("item", result.model_facts.facts)

    async def test_illegal_and_model_invented_query_do_not_fetch(self):
        self.setup_handler()
        for query in ("0", "2147483648", 'Ore" OR 1', True, None, "x" * 121, "Ore\n"):
            self.assertIs(
                (await self.call("查相关物品", query)).status, ResultStatus.ERROR
            )
        self.assertEqual(self.http.requests, [])
        self.setup_handler(
            [{"results": [{"row_id": 90001, "fields": {"Name": NAMES[0]}}]}]
        )
        self.detail_responses()
        self.assertIs(
            (await self.call("相关前文里的矿石", NAMES[0])).status, ResultStatus.SUCCESS
        )

    async def test_name_confirmation_and_consumed_replay(self):
        pending = await self.pending()
        self.detail_responses()
        choice = self.choice(pending)
        result = await self.call(
            "Sample Ore Alpha那个。", event="confirm", select=choice
        )
        self.assertIs(result.status, ResultStatus.SUCCESS)
        self.assertEqual(result.model_facts.facts["item"]["query"], "Ore")
        before = len(self.http.requests)
        self.assertIs(
            (
                await self.call(
                    "Sample Ore Alpha那个。", event="confirm", select=choice
                )
            ).status,
            ResultStatus.ERROR,
        )
        self.assertEqual(len(self.http.requests), before)

    async def test_id_confirmation_requires_real_matching_new_event(self):
        pending = await self.pending()
        choice = self.choice(pending)
        for event, item_id in (("request", 90001), ("confirm", 99999), ("bool", True)):
            self.assertIs(
                (
                    await self.call(
                        "第一个", event=event, select={**choice, "item_id": item_id}
                    )
                ).status,
                ResultStatus.ERROR,
            )
        self.detail_responses()
        self.assertIs(
            (await self.call("第一个", event="new", select=choice)).status,
            ResultStatus.SUCCESS,
        )

    async def test_questions_negations_ordinals_and_multiple_names_need_clarification(
        self,
    ):
        pending = await self.pending()
        choice = {
            key: value
            for key, value in self.choice(pending).items()
            if key != "item_id"
        }
        for text in ("不要", "哪个好", "两个都行", "刚才那个?"):
            result = await self.call(
                text, event=text, select={**choice, "action": "clarify"}
            )
            self.assertIs(result.status, ResultStatus.NEEDS_SELECTION)
        self.assertEqual(len(self.http.requests), 1)
        self.detail_responses()
        self.assertIs(
            (
                await self.call("第一个", event="ordinal", select=self.choice(pending))
            ).status,
            ResultStatus.SUCCESS,
        )

    async def test_duplicate_names_need_explicit_id(self):
        self.setup_handler(
            [
                {
                    "results": [
                        {"row_id": i, "fields": {"Name": "Same"}}
                        for i in (90001, 90002)
                    ]
                }
            ]
        )
        pending = await self.call("查同名", "Same")
        choice = {
            key: value
            for key, value in self.choice(pending).items()
            if key != "item_id"
        }
        self.assertIs(
            (
                await self.call(
                    "叫Same的", event="unclear", select={**choice, "action": "clarify"}
                )
            ).status,
            ResultStatus.NEEDS_SELECTION,
        )
        self.detail_responses()
        self.assertIs(
            (
                await self.call(
                    "第一个", event="distinct", select={**choice, "item_id": 90001}
                )
            ).status,
            ResultStatus.SUCCESS,
        )

    async def test_cross_user_session_adapter_batch_and_generation_are_denied(self):
        pending = await self.pending()
        choice = self.choice(pending)
        for identity in ({"user": "bob"}, {"session": "other"}, {"adapter": "other"}):
            self.assertIs(
                (
                    await self.call(
                        NAMES[0], event=str(identity), select=choice, **identity
                    )
                ).status,
                ResultStatus.ERROR,
            )
        for field in ("batch_id", "generation"):
            self.assertIs(
                (
                    await self.call(
                        NAMES[0], event=field, select={**choice, field: "wrong"}
                    )
                ).status,
                ResultStatus.ERROR,
            )
        self.assertEqual(len(self.http.requests), 1)

    async def test_duplicate_original_name_explicit_new_query_replaces_batch(self):
        self.setup_handler(
            [
                {
                    "results": [
                        {"row_id": i, "fields": {"Name": "Same"}}
                        for i in (90001, 90002)
                    ]
                }
            ]
        )
        pending = await self.call("查询Same", "Same")
        self.assertEqual(
            self.choice(pending), self.choice(await self.call("查询Same", "Same"))
        )
        self.http.responses.append(
            {
                "results": [
                    {"row_id": i, "fields": {"Name": "Same"}} for i in (90001, 90002)
                ]
            }
        )
        new = await self.call("再查询同一个", "Same", event="new")
        self.assertNotEqual(
            self.choice(pending)["generation"], self.choice(new)["generation"]
        )
        self.assertIs(
            (
                await self.call("第一个", event="old", select=self.choice(pending))
            ).status,
            ResultStatus.ERROR,
        )

    async def test_command_origin_select_is_denied_without_consuming_llm_batch(self):
        pending = await self.pending()
        ctx = replace(context("command", select=True), origin=InvocationOrigin.COMMAND)
        result = await self.handler.invoke(ctx, self.choice(pending))
        self.assertIs(result.error.code, ErrorCode.UNSUPPORTED)
        self.assertEqual(len(self.http.requests), 1)
        self.assertEqual(self.handler._chat.registry.size, 1)
        self.detail_responses()
        self.assertIs(
            (
                await self.call(
                    "选择物品 90001", event="confirmed", select=self.choice(pending)
                )
            ).status,
            ResultStatus.SUCCESS,
        )

    async def test_pending_query_cannot_be_bypassed_by_model_candidate_query(self):
        pending = await self.pending()
        for query in (NAMES[0], "90001"):
            self.assertIs(
                (await self.call("查Ore", query, event="request")).status,
                ResultStatus.ERROR,
            )
        self.assertEqual(len(self.http.requests), 1)
        self.assertEqual(self.handler._chat.registry.size, 1)
        self.assertEqual(pending.model_facts.facts["item"]["query"], "Ore")

    async def test_same_event_retry_keeps_batch_new_same_query_invalidates(self):
        old = await self.pending()
        retry = await self.call("查一下Ore的信息")
        self.assertEqual(self.choice(old), self.choice(retry))
        self.http.responses.append(
            {
                "results": [
                    {"row_id": 90001, "fields": {"Name": "Other"}},
                    {"row_id": 90002, "fields": {"Name": "Another"}},
                ]
            }
        )
        new = await self.call("再查Ore", event="new")
        self.assertNotEqual(self.choice(old)["batch_id"], self.choice(new)["batch_id"])
        self.assertIs(
            (await self.call(NAMES[0], event="stale", select=self.choice(old))).status,
            ResultStatus.ERROR,
        )

    async def test_expiry_and_lifecycle_close_do_not_restore_candidates(self):
        pending = await self.pending()
        now = [0]
        self.handler._chat.registry = CandidateRegistry(clock=lambda: now[0], ttl=1)
        self.http.responses.append(
            {
                "results": [
                    {"row_id": 90001, "fields": {"Name": "A"}},
                    {"row_id": 90002, "fields": {"Name": "B"}},
                ]
            }
        )
        pending = await self.call("查Ore", event="new")
        now[0] = 1
        self.assertIs(
            (
                await self.call(
                    "选择物品 90001", event="expired", select=self.choice(pending)
                )
            ).status,
            ResultStatus.ERROR,
        )
        self.handler.close()
        self.assertIs(
            (
                await self.call(
                    "选择物品 90001", event="closed", select=self.choice(pending)
                )
            ).status,
            ResultStatus.ERROR,
        )
        self.assertEqual(self.handler._chat.registry.size, 0)

    async def test_market_batch_is_not_an_item_batch(self):
        pending = await self.pending()
        other = CandidateRegistry()
        owner = TrustedOwner("alice", "chat", "llm_tool:test")
        batch = other.publish(
            other.begin(owner),
            ItemQuery("Ore"),
            (ItemCandidate(90001, NAMES[0]),),
            False,
        )
        args = {
            "batch_id": batch.batch_id,
            "generation": batch.generation,
            "item_id": 90001,
        }
        self.assertIs(
            (await self.call(NAMES[0], event="foreign", select=args)).status,
            ResultStatus.ERROR,
        )
        self.assertEqual(self.handler._chat.registry.size, 1)
        self.assertEqual(pending.status, ResultStatus.NEEDS_SELECTION)

    async def test_message_proof_change_drops_late_detail(self):
        self.setup_handler()
        record = ItemRecord(
            90001,
            "Synthetic",
            source_urls=(
                "https://xivapi-v2.xivcdn.com/item",
                "https://garlandtools.cn/item",
            ),
        )

        async def lookup(source, item_id):
            self.services.messages["request"] = MessageContext("changed", "other")
            return ItemLookupSnapshot(record, True)

        with patch.object(ItemSourceClient, "lookup", lookup):
            result = await self.call("物品ID90001", "90001")
        self.assertIs(result.error.code, ErrorCode.UNSUPPORTED)
        self.assertEqual(self.handler._chat.registry.size, 0)

    async def test_late_search_after_new_query_or_close_cannot_publish(self):
        for close in (False, True):
            with self.subTest(close=close):
                self.setup_handler()
                entered, release = asyncio.Event(), asyncio.Event()

                async def search(source, query, *, exact=False):
                    if query == "Old":
                        entered.set()
                        await release.wait()
                    return (ItemCandidate(90001, "A"), ItemCandidate(90002, "B")), False

                with patch.object(ItemSourceClient, "search", search):
                    task = asyncio.create_task(self.call("查Old", "Old", event="old"))
                    await entered.wait()
                    if close:
                        self.handler.close()
                    else:
                        newer = await self.call("查New", "New", event="new")
                    release.set()
                    late = await task
                self.assertIs(late.status, ResultStatus.ERROR)
                if close:
                    self.assertEqual(self.handler._chat.registry.size, 0)
                else:
                    self.assertEqual(self.handler._chat.registry.size, 1)
                    self.assertEqual(newer.model_facts.facts["item"]["query"], "New")

    async def test_deadline_expires_before_source_access(self):
        self.setup_handler()
        ctx = replace(context(), deadline=0.000001)
        self.services.messages["request"] = MessageContext("物品ID90001", "request")
        result = await self.handler.invoke(ctx, {"query": "90001"})
        self.assertIs(result.status, ResultStatus.ERROR)
        self.assertEqual(self.http.requests, [])

    def test_max_source_projection_fits_core_fact_budget_and_discloses_omissions(self):
        costs = tuple(ItemAmount(i, i, "材料" * 60) for i in range(1, 9))
        route = AcquisitionRoute(
            AcquisitionKind.CRAFT, "制作", tuple("细节" * 120 for _ in range(6)), costs
        )
        record = ItemRecord(
            90001,
            "物品" * 80,
            "说明" * 500,
            routes=(route,) * 24,
            source_urls=(
                "https://xivapi-v2.xivcdn.com/item",
                "https://garlandtools.cn/item",
            ),
            warnings=tuple("警告" + str(i) for i in range(30)),
        )
        facts = _record_facts(record, ItemQuery("测试"), partial=False)
        document = FactDocument(
            {"status": "partial_success", "error": None, "item": facts},
            record.source_urls,
        )
        validate_contract(document)
        list(public_fact_strings(document))
        self.assertTrue(facts["partial"])
        self.assertTrue(facts["routes_truncated"])
        self.assertTrue(facts["warnings_truncated"])
        self.assertEqual(len(facts["routes"]), 6)
        self.assertEqual(len(facts["routes"][0]["costs"]), 4)


if __name__ == "__main__":
    unittest.main()
