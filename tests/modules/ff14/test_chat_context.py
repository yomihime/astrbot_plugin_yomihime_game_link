"""Offline semantic DTO/state checks, never actual model understanding proofs."""

import asyncio
import json
import unittest
from dataclasses import replace

from ygl_test_subject.modules.ff14.features.chat_context import (
    PendingTargets,
    chat_owner,
    language_region,
)
from ygl_test_subject.modules.ff14.features.fflogs_chat import (
    validate_message_parameters,
)
from ygl_test_subject.modules.ff14.query_resolution import (
    MarketQueryResolver,
    QueryDefaults,
    QueryResolutionError,
)

from yomihime_game_link_sdk.errors import SourceHttpError
from yomihime_game_link_sdk.results import ResultStatus
from yomihime_game_link_sdk.services import HttpResponse

from . import test_fflogs as fixture
from . import test_fflogs_chat as chat_fixture
from .test_query_resolution import catalog

TARGET = chat_fixture.TARGET
invocation = chat_fixture.invocation


class ChatContextTests(unittest.TestCase):
    def test_request_expression_language_excludes_business_names_and_numbers(self):
        resolver = MarketQueryResolver(catalog())
        defaults = QueryDefaults("global", 2, 3)
        for semantic, expected, basis in (
            ({"request_language": "zh"}, ("China",), "request_language"),
            (
                {"request_language": "non_zh"},
                ("North-America", "Europe", "Japan", "Oceania"),
                "request_language",
            ),
            (
                {"request_language": "unknown", "context_language": "zh"},
                ("China",),
                "context_language",
            ),
            (
                {"request_language": "unknown"},
                ("North-America", "Europe", "Japan", "Oceania"),
                "core_default",
            ),
        ):
            for name in ("犎牛牛排", "Synthetic 日本 Ore", "44091"):
                result = resolver.parse(
                    {"query": name, **semantic}, defaults, chat=True
                )
                self.assertEqual(result.scope.regions, expected)
                self.assertEqual(result.scope.default_basis, basis)
        # Command/Web omission keeps Core even when a query contains Chinese.
        self.assertEqual(
            resolver.parse({"query": "犎牛牛排"}, defaults).scope.regions,
            ("North-America", "Europe", "Japan", "Oceania"),
        )

    def test_explicit_region_server_priority_and_conflict(self):
        resolver = MarketQueryResolver(catalog())
        defaults = QueryDefaults("global", 2, 3)
        self.assertEqual(
            resolver.parse(
                {"query": "1", "server": "紫水", "request_language": "non_zh"},
                defaults,
                chat=True,
            ).scope.default_basis,
            "server_region",
        )
        scoped = resolver.parse(
            {"query": "1", "region": "global", "request_language": "zh"},
            defaults,
            chat=True,
        )
        self.assertEqual(scoped.scope.default_basis, "explicit_region")
        self.assertNotIn("China", scoped.scope.regions)
        with self.assertRaises(QueryResolutionError):
            resolver.parse(
                {"query": "1", "server": "紫水", "region": "global"},
                defaults,
                chat=True,
            )

    def test_languages_and_business_actions_never_accept_identity_fields(self):
        for value in (True, "Chinese", None):
            with self.assertRaises(QueryResolutionError):
                language_region({"request_language": value}, "cn")
        for key in ("actor_id", "session", "event_ref", "confirmed", "token"):
            with self.assertRaises(ValueError):
                validate_message_parameters(
                    {**TARGET, key: "fake"}, "related user request"
                )

    def test_pending_limits_ttl_supersession_and_close(self):
        now = [0]
        pending = PendingTargets(clock=lambda: now[0], ttl=300, capacity=128)
        for i in range(130):
            pending.begin(("actor", i), str(i), {"character": "Synthetic Hero"})
        self.assertEqual(len(pending.states), 128)
        self.assertIsNone(pending.get(("actor", 0)))
        old = pending.get(("actor", 129))
        new = pending.begin(
            ("actor", 129), "correction", {"character": "Other Synthetic"}
        )
        self.assertNotEqual(old.generation, new.generation)
        with self.assertRaises(QueryResolutionError):
            pending.current(("actor", 129), old)
        now[0] = 301
        self.assertIsNone(pending.get(("actor", 129)))
        pending.begin("key", "new", {})
        pending.close()
        self.assertEqual(len(pending.states), 0)

    def test_trusted_owner_epoch_adapter_module_and_capability_isolation(self):
        view = invocation()
        owner = chat_owner(view)
        for changed in (
            replace(view, actor_id="other"),
            replace(view, conversation_id="other"),
            replace(view, adapter_id="other"),
            replace(view, module_epoch=2),
            replace(view, module_id="other/module"),
        ):
            self.assertNotEqual(owner, chat_owner(changed))
        pending = PendingTargets()
        key = (owner, view.capability_id)
        pending.begin(key, "event", {})
        self.assertIsNone(pending.get((owner, "item.lookup")))
        for kwargs in ({"ttl": 301}, {"capacity": 129}, {"ttl": 0}):
            with self.assertRaises(ValueError):
                PendingTargets(**kwargs)


class LogsAdaptiveTests(unittest.IsolatedAsyncioTestCase):
    setup_handler = chat_fixture.FFLogsChatTests.setup_handler
    call = chat_fixture.FFLogsChatTests.call

    async def server_pending(self):
        rows = [
            fixture._server_row(901),
            fixture._server_row(902, slug="cerberus-2", subregion="Chaos"),
        ]
        self.setup_handler(fixture._character_responses(first_rows=rows)[:-1])
        pending = await self.call()
        self.assertIs(pending.status, ResultStatus.NEEDS_SELECTION)
        facts = pending.model_facts.facts["fflogs"]
        return {key: facts[key] for key in ("batch_id", "generation")}

    async def test_logs_confirmation_event_fence_blocks_alias_query_and_correction_during_fetch(
        self,
    ):
        choice = await self.server_pending()
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.http.fetch
        character_requests = []

        async def blocked(request):
            if (
                request.source_id.startswith("fflogs_public")
                and "FF14PublicCharacter" in json.loads(request.body)["query"]
            ):
                character_requests.append(request)
                entered.set()
                await release.wait()
                return HttpResponse(200, {}, json.dumps(fixture._character()).encode())
            return await original(request)

        self.http.fetch = blocked
        flight = asyncio.create_task(
            self.call(
                "第二个",
                {**choice, "action": "continue", "server_id": 902},
                event="confirm",
            )
        )
        await entered.wait()
        key = (chat_owner(invocation()), "ff14.logs.character")
        state = self.handler._chat_pending.get(key)
        generation = state.generation
        try:
            for action in ("query", "correction", "continue"):
                for server in ("Cerberus", "cerberus"):
                    denied = await self.call(
                        "第二个",
                        {**TARGET, "server": server, "action": action},
                        event="confirm",
                    )
                    self.assertIs(denied.status, ResultStatus.ERROR)
            self.assertEqual(len(character_requests), 1)
            self.assertEqual(self.handler._chat_pending.get(key).generation, generation)
        finally:
            release.set()
        self.assertIs((await flight).status, ResultStatus.PARTIAL_SUCCESS)
        self.assertIs(
            (
                await self.call(
                    "第二个", {**TARGET, "action": "correction"}, event="confirm"
                )
            ).status,
            ResultStatus.ERROR,
        )
        self.assertEqual(len(character_requests), 1)

    async def test_logs_new_event_supersedes_confirmation_flight_and_blocks_late_facts(
        self,
    ):
        choice = await self.server_pending()
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.http.fetch
        character_requests = []

        async def blocked_once(request):
            if (
                request.source_id.startswith("fflogs_public")
                and "FF14PublicCharacter" in json.loads(request.body)["query"]
            ):
                character_requests.append(request)
                if len(character_requests) == 1:
                    entered.set()
                    await release.wait()
                    return HttpResponse(
                        200, {}, json.dumps(fixture._character()).encode()
                    )
            return await original(request)

        self.http.fetch = blocked_once
        flight = asyncio.create_task(
            self.call(
                "第二个",
                {**choice, "action": "continue", "server_id": 902},
                event="confirm",
            )
        )
        await entered.wait()
        try:
            self.http.responses.extend(fixture._character_responses())
            fresh = await self.call(
                "重新查角色", {**TARGET, "action": "correction"}, event="fresh"
            )
            self.assertIs(fresh.status, ResultStatus.PARTIAL_SUCCESS)
        finally:
            release.set()
        late = await flight
        self.assertIs(late.status, ResultStatus.ERROR)
        self.assertNotIn("fflogs", late.model_facts.facts)
        self.assertEqual(len(character_requests), 2)

    async def test_logs_server_choices_owner_epoch_ttl_membership_consume_and_close(
        self,
    ):
        choice = await self.server_pending()
        before = len(self.http.requests)
        args = {**choice, "action": "continue", "server_id": 902}
        for changes in (
            {"actor_id": "other"},
            {"conversation_id": "other"},
            {"adapter_id": "other"},
            {"module_epoch": 2},
        ):
            self.assertIs(
                (await self.call("第二个", args, event="foreign", **changes)).status,
                ResultStatus.ERROR,
            )
        for server_id in (71001, 999, True):
            self.assertIs(
                (
                    await self.call(
                        "第二个", {**args, "server_id": server_id}, event="member"
                    )
                ).status,
                ResultStatus.ERROR,
            )
        self.assertEqual(len(self.http.requests), before)
        self.handler._chat_pending.get(
            (chat_owner(invocation()), "ff14.logs.character")
        ).expires = -1
        self.assertIs(
            (await self.call("第二个", args, event="expired")).status,
            ResultStatus.ERROR,
        )
        self.assertEqual(len(self.http.requests), before)
        self.handler.close()
        self.assertEqual(len(self.handler._chat_pending.states), 0)

    async def test_correction_supersedes_pending_and_same_event_cannot_change(self):
        self.setup_handler()
        old = await self.call("查旧角色", {"character": "Old Synthetic"})
        self.assertIs(
            (
                await self.call(
                    "同消息改名",
                    {"character": "Synthetic Hero", "action": "correction"},
                )
            ).status,
            ResultStatus.ERROR,
        )
        new = await self.call(
            "更正角色",
            {"character": "Synthetic Hero", "action": "correction"},
            event="correct",
        )
        self.assertNotEqual(
            old.model_facts.facts["fflogs"]["generation"],
            new.model_facts.facts["fflogs"]["generation"],
        )
        result = await self.call(
            "补服务器国际服",
            {"server": "Cerberus", "realm": "global", "action": "continue"},
            event="supply",
        )
        self.assertIs(result.status, ResultStatus.PARTIAL_SUCCESS)
        self.assertEqual(
            result.model_facts.facts["fflogs"]["character"]["name"], "Synthetic Hero"
        )

    async def test_public_server_region_wins_language_ids_never_become_logs_id(self):
        self.setup_handler(fixture._character_responses(realm="cn"))
        original = self.http.fetch

        async def fetch(request):
            if request.source_id == "universalis_market":
                payload = (
                    [{"id": 71001, "name": "Cerberus"}]
                    if request.path.endswith("worlds")
                    else [{"name": "SyntheticDC", "region": "China", "worlds": [71001]}]
                )
                return HttpResponse(200, {}, json.dumps(payload).encode())
            return await original(request)

        self.http.fetch = fetch
        result = await self.call(
            "English request with server Cerberus",
            {
                "server": "Cerberus",
                "character": "Synthetic Hero",
                "request_language": "non_zh",
            },
        )
        self.assertIs(result.status, ResultStatus.SUCCESS)
        self.assertEqual(result.model_facts.facts["fflogs"]["realm"], "cn")
        self.assertEqual(
            result.model_facts.facts["fflogs"]["default_basis"], "server_region"
        )
        self.assertEqual(
            json.loads(self.http.requests[-1].body)["variables"]["serverSlug"],
            "cerberus",
        )
        self.setup_handler([])
        self.http.fetch = fetch
        conflict = await self.call("explicit global", {**TARGET, "realm": "global"})
        self.assertIs(conflict.status, ResultStatus.ERROR)
        self.assertIn("冲突", conflict.error.message)

    async def test_unavailable_public_directory_does_not_claim_logs_server_absent(self):
        self.setup_handler()
        original = self.http.fetch

        async def fetch(request):
            if request.source_id == "universalis_market":
                raise SourceHttpError("upstream_error", status_code=503)
            return await original(request)

        self.http.fetch = fetch
        result = await self.call()
        self.assertIs(result.status, ResultStatus.PARTIAL_SUCCESS)
        self.assertEqual(
            result.model_facts.facts["fflogs"]["default_basis"], "explicit_region"
        )

    async def test_new_query_does_not_borrow_an_unrelated_pending_target(self):
        self.setup_handler()
        await self.call("查旧角色", {"character": "Old Synthetic"})
        result = await self.call(
            "新服务器查询", {"server": "Cerberus"}, event="new-query"
        )
        self.assertEqual(result.model_facts.facts["fflogs"]["missing"], ("character",))
        self.assertEqual(self.http.requests, [])
