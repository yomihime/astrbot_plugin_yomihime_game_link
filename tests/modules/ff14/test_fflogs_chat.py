"""Offline role tool proofs, truthful facts, bounded publication and safe errors."""

from __future__ import annotations

import asyncio
import json
import logging
import unittest
from dataclasses import replace
from types import SimpleNamespace

from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.public_result import public_fact_strings
from ygl_test_subject.modules.ff14.features.fflogs import FFLogsCharacterLookup
from ygl_test_subject.modules.ff14.features.fflogs_chat import message_parameters

from yomihime_game_link_sdk.contexts import (
    InvocationOrigin,
    InvocationView,
    MessageContext,
)
from yomihime_game_link_sdk.errors import SourceHttpError
from yomihime_game_link_sdk.results import ErrorCode, ResultStatus

from . import test_fflogs as fixture

TARGET = {"realm": "global", "server": "Cerberus", "character": "Synthetic Hero"}
MESSAGE = '查 FFLogs 国际服 Cerberus "Synthetic Hero"'
SENTINEL = "synthetic-private-secret-S2-never-publish"


def invocation(event="one", **changes):
    return replace(
        InvocationView(
            event,
            InvocationOrigin.LLM_TOOL,
            "alice",
            "chat",
            "ff14/ff14",
            1,
            1,
            adapter_id="test",
            capability_id="ff14.logs.character",
        ),
        **changes,
    )


class FFLogsChatTests(unittest.IsolatedAsyncioTestCase):
    def setup_handler(self, responses=None, *, configured=True, config=None):
        self.http = fixture._FakeHttp(
            fixture._character_responses() if responses is None else responses
        )
        self.services = fixture._services(self.http, configured=configured)
        self.messages = {}
        outer = self

        class Scopes:
            async def bind(self, context):
                async def read():
                    return outer.messages[context.invocation_id]

                return SimpleNamespace(
                    http=outer.http, message=SimpleNamespace(read=read)
                )

        self.services.scopes = Scopes()
        if config:
            self.services.config = config
        self.handler = FFLogsCharacterLookup(self.services)
        self.addCleanup(self.handler.close)

    async def call(self, text=MESSAGE, parameters=None, event="one", **identity):
        self.messages[event] = MessageContext(text, event)
        result = await self.handler.invoke(
            invocation(event, **identity), TARGET if parameters is None else parameters
        )
        validate_contract(result)
        list(public_fact_strings(result.model_facts))
        return result

    async def test_whole_current_message_public_role_facts_and_real_selection(self):
        self.setup_handler()
        result = await self.call()
        self.assertIs(result.status, ResultStatus.PARTIAL_SUCCESS)
        facts = result.model_facts.facts["fflogs"]
        self.assertEqual(facts["character"]["name"], "Synthetic Hero")
        self.assertEqual(facts["resolved_server"]["region"], "NA")
        self.assertEqual(facts["selection"]["metric"], "rdps")
        self.assertEqual(facts["selection"]["partition"], -1)
        self.assertEqual(facts["selection"]["selection_source"], "fflogs_default")
        self.assertIsNone(facts["selection"]["requested_filter_group"])
        self.assertEqual(facts["rankings"][0]["rank_percent"], 93.5)
        self.assertEqual(facts["rankings"][0]["best_amount"], 12345.6)
        self.assertIsNone(facts["source"]["source_updated_at"])
        self.assertTrue(facts["source"]["retrieved_at"])
        self.assertIn("Synthetic%20Hero", facts["source"]["url"])
        self.assertFalse(facts["limitations"]["battle_analysis"])
        self.assertFalse(facts["limitations"]["global_output_percentiles"])
        self.assertEqual(len(self.http.requests), 6)
        self.assertNotIn("Authorization", self.http.requests[-1].headers)

    async def test_required_fields_complete_new_message_after_clarification(self):
        self.setup_handler()
        for text in (
            "查Logs",
            "国际服",
            "国际服 Cerberus",
            '国际服 "Synthetic Hero"',
            "刚才那个",
            "第一个",
        ):
            with self.subTest(text=text):
                error = await self.call(text, event=text)
                self.assertIs(error.error.code, ErrorCode.PARAMETER_ERROR)
                self.assertIn("一条新消息", error.error.message)
        self.assertEqual(self.http.requests, [])
        self.assertIs(
            (await self.call(event="complete-new")).status, ResultStatus.PARTIAL_SUCCESS
        )

    async def test_missing_parameters_and_model_shortening_invention_are_rejected(self):
        self.setup_handler()
        for parameters in (
            {"realm": "global"},
            {**TARGET, "character": "Hero"},
            {**TARGET, "server": "Cer"},
            {**TARGET, "character": "Invented"},
            {**TARGET, "realm": "cn"},
            {**TARGET, "metric": "ndps"},
            {**TARGET, "server_hint": "Aether"},
            {**TARGET, "token": SENTINEL},
        ):
            with self.subTest(parameters=parameters):
                self.assertIs(
                    (await self.call(parameters=parameters)).status, ResultStatus.ERROR
                )
        self.assertEqual(self.http.requests, [])

    async def test_negations_multiple_targets_and_history_only_messages_are_rejected(
        self,
    ):
        self.setup_handler()
        for text in (
            '不要查 FFLogs 国际服 Cerberus "Synthetic Hero"',
            '不是国际服 Cerberus "Synthetic Hero"',
            MESSAGE + ' 或 "Another Hero"',
            MESSAGE + " 国服",
            "同一个角色",
            "还是之前那个",
            "国际服？",
        ):
            with self.subTest(text=text):
                self.assertIs(
                    (await self.call(text, event=text)).status, ResultStatus.ERROR
                )
        self.assertEqual(self.http.requests, [])

    async def test_aliases_command_grammar_and_labelled_fields_accept_full_names(self):
        for text in (
            '查一下 FFLogs GLOBAL Cerberus "Synthetic Hero"',
            '帮我查 logs 国际服 Cerberus "Synthetic Hero"',
            '/ygl ff14 logs global Cerberus "Synthetic Hero"',
            'realm=国际服 server=Cerberus character="Synthetic Hero"',
            "帮我查一下国际服Cerberus上的Synthetic Hero战绩",
            "查 FFLogs global Cerberus的Synthetic Hero战绩",
        ):
            with self.subTest(text=text):
                self.setup_handler()
                self.assertIs(
                    (await self.call(text)).status, ResultStatus.PARTIAL_SUCCESS
                )
        values = message_parameters(
            '查 FFLogs 国服 "Synthetic Server" "Synthetic Hero"'
        )
        self.assertEqual(values["server"], "Synthetic Server")
        self.assertEqual(values["character"], "Synthetic Hero")
        self.setup_handler([])
        for parameters in (
            {**TARGET, "server": "Cer"},
            {**TARGET, "character": "Hero"},
        ):
            self.assertIs(
                (
                    await self.call("查国际服Cerberus的Synthetic Hero战绩", parameters)
                ).status,
                ResultStatus.ERROR,
            )
        self.assertEqual(self.http.requests, [])

    async def test_explicit_metrics_and_complete_filters_are_source_verified(self):
        for metric in ("rdps", "ndps", "cdps"):
            with self.subTest(metric=metric):
                self.setup_handler(
                    fixture._character_responses(
                        realm="cn",
                        character=fixture._character(
                            metric=metric, zone_id=2, difficulty=3, partition=4
                        ),
                    )
                )
                parameters = {
                    **TARGET,
                    "realm": "cn",
                    "metric": metric,
                    "zone_id": 2,
                    "difficulty": 3,
                    "partition": 4,
                }
                text = f'查 FFLogs 国服 Cerberus "Synthetic Hero" metric={metric.upper()} zone_id=2 difficulty=3 partition=4'
                result = await self.call(text, parameters)
                self.assertIs(result.status, ResultStatus.SUCCESS)
                self.assertEqual(
                    result.model_facts.facts["fflogs"]["selection"][
                        "requested_filter_group"
                    ],
                    {"zone_id": 2, "difficulty": 3, "partition": 4},
                )
                self.assertEqual(
                    json.loads(self.http.requests[-1].body)["variables"]["metric"],
                    metric,
                )
        self.setup_handler([])
        for tail in (
            "zone_id=2",
            "zone_id=2 difficulty=3",
            "zone_id=2 difficulty=3 partition=0",
            "metric=pdps",
            "metric=rdps metric=ndps",
        ):
            self.assertIs(
                (await self.call(MESSAGE + " " + tail)).status, ResultStatus.ERROR
            )
        self.assertEqual(self.http.requests, [])

    async def test_server_ambiguity_has_bounded_typed_choices_and_complete_retry(self):
        rows = [
            fixture._server_row(901, subregion="Aether"),
            fixture._server_row(902, slug="cerberus-2", subregion="Chaos"),
        ]
        # Ambiguity returns after the directory, before a character fetch. Each
        # completed synthetic invocation gets only the responses it can consume.
        self.setup_handler(fixture._character_responses(first_rows=rows)[:-1])
        pending = await self.call()
        self.assertIs(pending.status, ResultStatus.NEEDS_SELECTION)
        choices = pending.model_facts.facts["fflogs"]["candidates"]
        self.assertEqual([row["subregion"] for row in choices], ["Aether", "Chaos"])
        self.assertEqual(len(self.http.requests), 5)
        self.assertIs(
            (
                await self.call(
                    "Chaos", {**TARGET, "server_hint": "Chaos"}, event="partial-retry"
                )
            ).status,
            ResultStatus.ERROR,
        )
        self.assertEqual(len(self.http.requests), 5)
        self.assertEqual(len(self.http.responses), 0)
        self.http.responses.extend(fixture._character_responses(first_rows=rows))
        result = await self.call(
            MESSAGE + " server_hint=Chaos",
            {**TARGET, "server_hint": "Chaos"},
            event="complete-retry",
        )
        self.assertIs(result.status, ResultStatus.PARTIAL_SUCCESS)
        self.assertEqual(
            json.loads(self.http.requests[-1].body)["variables"]["serverSlug"],
            "cerberus-2",
        )

    async def test_missing_values_are_null_while_source_zero_is_preserved(self):
        body = fixture._character()
        rankings = body["data"]["characterData"]["character"]["rankings"]["rankings"]
        rankings[0].update(bestAmount=None, rankPercent=None, bestSpec=None)
        rankings.append(
            {
                "encounter": {"id": 1234, "name": "Synthetic Zero"},
                "bestAmount": 0,
                "rankPercent": 0,
            }
        )
        self.setup_handler(fixture._character_responses(character=body))
        result = await self.call()
        rows = result.model_facts.facts["fflogs"]["rankings"]
        self.assertIsNone(rows[0]["best_amount"])
        self.assertIsNone(rows[0]["rank_percent"])
        self.assertIn("spec", rows[0]["missing"])
        self.assertEqual(rows[1]["best_amount"], 0)
        self.assertEqual(rows[1]["rank_percent"], 0)

    async def test_max_500_rankings_facts_fit_real_core_scan_and_disclose_truncation(
        self,
    ):
        body = fixture._character()
        ranking = body["data"]["characterData"]["character"]["rankings"]
        ranking["rankings"] = [
            {
                "encounter": {"id": i + 1, "name": "首领" * 80},
                "rankPercent": None,
                "bestAmount": None,
                "bestSpec": None,
            }
            for i in range(500)
        ]
        ranking["size"] = 500
        self.setup_handler(fixture._character_responses(realm="cn", character=body))
        result = await self.call(
            MESSAGE.replace("国际服", "国服"), {**TARGET, "realm": "cn"}
        )
        self.assertIs(result.status, ResultStatus.PARTIAL_SUCCESS)
        facts = result.model_facts.facts["fflogs"]
        self.assertEqual(
            (facts["returned_rows"], facts["total_rows"], facts["source_size"]),
            (20, 500, 500),
        )
        self.assertTrue(facts["truncated"])
        self.assertLessEqual(len(result.document.ordered_blocks), 32)

    async def test_unset_unknown_and_configured_unavailable_are_truthful(self):
        self.setup_handler([], configured=False)
        unset = await self.call()
        self.assertEqual(
            unset.model_facts.facts["supplement"]["fflogs"]["credential_state"], "unset"
        )
        self.assertEqual(self.http.requests, [])

        class Unknown:
            async def current(self):
                raise RuntimeError(SENTINEL)

        self.setup_handler([], config=Unknown())
        unknown = await self.call()
        self.assertEqual(
            unknown.model_facts.facts["supplement"]["fflogs"]["credential_state"],
            "unknown",
        )
        self.setup_handler([SourceHttpError("credentials_unavailable")])
        unavailable = await self.call()
        self.assertIs(unavailable.error.code, ErrorCode.AUTH_REQUIRED)
        state = unavailable.model_facts.facts["supplement"]["fflogs"]
        self.assertEqual(state["credential_state"], "configured_but_unavailable")
        self.assertIsNone(state["credential_failure_reason"])
        self.assertIn("原因未公开", unavailable.error.message)

    async def test_status_timeout_and_parse_errors_remain_distinct_and_safe(self):
        for source, expected in (
            (SourceHttpError(SENTINEL, status_code=401), ErrorCode.AUTH_EXPIRED),
            (SourceHttpError(SENTINEL, status_code=403), ErrorCode.AUTH_EXPIRED),
            (SourceHttpError(SENTINEL, status_code=429), ErrorCode.RATE_LIMITED),
            (SourceHttpError("timeout"), ErrorCode.UPSTREAM_ERROR),
            (b"bad body", ErrorCode.UNPARSED),
        ):
            with self.subTest(expected=expected):
                self.setup_handler([source])
                result = await self.call()
                self.assertIs(result.error.code, expected)
                self.assertNotIn(SENTINEL, str(result.model_facts.facts))
                if getattr(source, "code", None) == "timeout":
                    self.assertIn("超时", result.error.message)

    async def test_not_found_hidden_empty_and_graphql_errors(self):
        for variant, expected in (
            ("missing", ErrorCode.NOT_FOUND),
            ("hidden", ErrorCode.NOT_PUBLIC),
            ("empty", ErrorCode.NO_RECORDS),
            ("graphql", ErrorCode.UPSTREAM_ERROR),
        ):
            body = fixture._character()
            if variant == "missing":
                body["data"]["characterData"]["character"] = None
            elif variant == "hidden":
                body["data"]["characterData"]["character"]["hidden"] = True
            elif variant == "empty":
                body["data"]["characterData"]["character"]["rankings"]["rankings"] = []
            else:
                body = {"errors": [{"message": SENTINEL}], "data": None}
            self.setup_handler(fixture._character_responses(character=body))
            self.assertIs((await self.call()).error.code, expected)

    async def test_source_target_metric_and_filter_mismatches_are_rejected(self):
        for variant in ("name", "server", "metric", "filters"):
            body = fixture._character()
            raw = body["data"]["characterData"]["character"]
            parameters = TARGET
            text = MESSAGE
            if variant == "name":
                raw["name"] = "Other Hero"
            elif variant == "server":
                raw["server"]["name"] = "Other Server"
            elif variant == "metric":
                raw["rankings"]["metric"] = "ndps"
            else:
                parameters = {**TARGET, "zone_id": 2, "difficulty": 3, "partition": 4}
                text += " zone_id=2 difficulty=3 partition=4"
            self.setup_handler(fixture._character_responses(character=body))
            self.assertIs(
                (await self.call(text, parameters)).error.code, ErrorCode.UNPARSED
            )

    async def test_synthetic_secret_headers_and_raw_exceptions_never_publish_or_log(
        self,
    ):
        body = fixture._character()
        raw = body["data"]["characterData"]["character"]
        raw["access_token"] = SENTINEL
        raw["client_secret"] = SENTINEL
        raw["rankings"]["rankings"][0]["private_report"] = SENTINEL
        records = []

        class Collector(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        collector = Collector()
        logging.getLogger().addHandler(collector)
        self.addCleanup(logging.getLogger().removeHandler, collector)
        self.setup_handler(fixture._character_responses(character=body))
        result = await self.call()
        self.assertNotIn(SENTINEL, str(result.model_facts.facts))
        self.assertNotIn(SENTINEL, str(result.document))
        self.assertNotIn(SENTINEL, str(records))

    async def test_identity_missing_current_proof_change_and_closed_lifecycle(self):
        self.setup_handler()
        for field in ("actor_id", "conversation_id", "adapter_id"):
            self.assertIs((await self.call(**{field: None})).status, ResultStatus.ERROR)
        self.assertEqual(self.http.requests, [])
        fetch = self.http.fetch

        async def changed(request):
            value = await fetch(request)
            self.messages["one"] = MessageContext(MESSAGE, "other-event")
            return value

        self.http.fetch = changed
        self.assertIs((await self.call()).error.code, ErrorCode.UNSUPPORTED)
        self.assertEqual(len(self.http.requests), 1)
        self.setup_handler()
        fetch = self.http.fetch

        async def closed(request):
            value = await fetch(request)
            self.handler.close()
            return value

        self.http.fetch = closed
        self.assertIs((await self.call()).error.code, ErrorCode.UNSUPPORTED)
        self.assertEqual(len(self.http.requests), 1)

    async def test_deadline_and_cancel_close_own_inflight_query(self):
        self.setup_handler([])
        self.messages["expired"] = MessageContext(MESSAGE, "expired")
        expired = await self.handler.invoke(invocation("expired", deadline=0), TARGET)
        self.assertIs(expired.error.code, ErrorCode.UPSTREAM_ERROR)
        started = asyncio.Event()

        async def wait(request):
            started.set()
            await asyncio.Event().wait()

        self.http.fetch = wait
        task = asyncio.create_task(self.call())
        try:
            await asyncio.wait_for(started.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
