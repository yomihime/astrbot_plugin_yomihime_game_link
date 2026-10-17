"""Finite fixed-schema and explicit-filter regressions; synthetic only."""

from __future__ import annotations

import asyncio
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.adapters.astrbot.web_public import project_result
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.public_result import (
    preflight_error_facts,
    public_fact_strings,
)

from modules.ff14.features.fflogs import (
    FFLogsCharacterLookup,
    _character_inputs,
    _error,
)
from modules.ff14.features.fflogs_chat import message_parameters
from modules.ff14.features.fflogs_evidence import RequestMetrics
from modules.ff14.features.fflogs_scope_contract import (
    SHAPE_QUERY,
    InvocationObservation,
)
from tests.modules.ff14 import test_fflogs as f
from yomihime_game_link_sdk.contexts import InvocationOrigin
from yomihime_game_link_sdk.errors import SourceHttpError
from yomihime_game_link_sdk.results import CapabilityResult, ErrorCode, ResultStatus

TARGET = {
    "realm": "cn",
    "server": "Cerberus",
    "character": "Synthetic Hero",
    "zone_id": 65,
    "difficulty": 100,
    "partition": -1,
    "timeframe": "SyntheticHistorical",
    "spec_name": "Astrologian",
}
WEB = SimpleNamespace(origin=InvocationOrigin.WEB_PUBLIC)


def schema():
    args = []
    for name, kind, leaf in (
        ("zoneID", "SCALAR", "Int"),
        ("difficulty", "SCALAR", "Int"),
        ("partition", "SCALAR", "Int"),
        ("metric", "ENUM", "CharacterPageRankingMetricType"),
        ("specName", "SCALAR", "String"),
        ("timeframe", "ENUM", "RankingTimeframeType"),
    ):
        args.append(
            {
                "name": name,
                "defaultValue": None,
                "type": {"kind": kind, "name": leaf, "ofType": None},
            }
        )
    return {
        "data": {
            "character": {
                "name": "Character",
                "fields": [{"name": "zoneRankings", "args": args}],
            },
            "timeframe": {
                "name": "RankingTimeframeType",
                "kind": "ENUM",
                "enumValues": [
                    {"name": "SyntheticHistorical"},
                    {"name": "SyntheticCurrent"},
                ],
            },
        }
    }


def character(partition=-1):
    result = f._character(zone_id=65, difficulty=100, partition=partition)
    row = result["data"]["characterData"]["character"]["rankings"]["rankings"][0]
    row.update(bestSpec="Astrologian", spec="Astrologian")
    return result


def responses(*, partition=-1):
    return [
        schema(),
        *f._character_responses(realm="cn", character=character(partition)),
    ]


def operations(http):
    return [json.loads(request.body) for request in http.requests]


class ScopeContractTests(unittest.IsolatedAsyncioTestCase):
    def handler(self, responses):
        http = f._FakeHttp(responses)
        handler = FFLogsCharacterLookup(f._services(http))
        self.addCleanup(handler.close)
        return handler, http

    async def test_exact_scoped_wire_and_facts(self):
        handler, http = self.handler(responses())
        result = await handler.invoke(None, TARGET)
        self.assertIs(result.status, ResultStatus.SUCCESS)
        validate_contract(result)
        tuple(public_fact_strings(result.model_facts))
        projected = project_result(result)
        facts = projected["model_facts"]["fflogs"]
        self.assertEqual(
            facts["requested"],
            {
                key: TARGET[key]
                for key in ("zone_id", "difficulty", "partition", "metric")
                if key in TARGET
            }
            | {
                "metric": "rdps",
                "timeframe": "SyntheticHistorical",
                "spec_name": "Astrologian",
            },
        )
        self.assertEqual(facts["selection"]["partition"], -1)
        self.assertEqual(facts["rankings"][0]["bestSpec"], "Astrologian")
        self.assertEqual(facts["rankings"][0]["spec"], "Astrologian")
        self.assertEqual(facts["rankings"][0]["bestAmount"], "12345.6")
        ops = operations(http)
        self.assertEqual(ops[0], {"query": SHAPE_QUERY, "variables": {}})
        self.assertEqual(len(ops), 4)
        wire = ops[-1]
        self.assertEqual(wire["variables"]["timeframe"], "SyntheticHistorical")
        self.assertEqual(wire["variables"]["specName"], "Astrologian")
        self.assertIn("$timeframe: RankingTimeframeType!", wire["query"])
        self.assertIn("specName: $specName", wire["query"])
        self.assertNotIn("Astrologian", wire["query"])
        self.assertNotIn("FF14CatalogCharacter", wire["query"])

    async def test_sentinel_positive_selection_retains_requested_and_actual(self):
        target = {key: value for key, value in TARGET.items() if key != "spec_name"} | {
            "zone_id": 30
        }
        for empty in (False, True):
            with self.subTest(empty=empty):
                body = character(partition=3)
                raw = body["data"]["characterData"]["character"]["rankings"]
                raw["zone"] = 30
                if empty:
                    raw["rankings"] = []
                handler, http = self.handler(
                    [schema(), *f._character_responses(realm="cn", character=body)]
                )
                result = await handler.invoke(WEB, target)
                validate_contract(result)
                tuple(public_fact_strings(result.model_facts))
                projected = project_result(result)
                if empty:
                    self.assertIs(result.error.code, ErrorCode.NO_RECORDS)
                    preflight_error_facts(result)
                    facts = projected["model_facts"]["supplement"]["fflogs"]
                else:
                    self.assertIs(result.status, ResultStatus.SUCCESS)
                    facts = projected["model_facts"]["fflogs"]
                self.assertEqual(facts["requested"]["partition"], -1)
                self.assertEqual(facts["selection"]["partition"], 3)
                self.assertEqual(facts["selection"]["zone"], 30)
                self.assertEqual(facts["selection"]["difficulty"], 100)
                self.assertEqual(facts["selection"]["metric"], "rdps")
                self.assertEqual(facts["total_rows"], 0 if empty else 1)
                self.assertFalse(facts["truncated"])
                self.assertNotIn("fflogs_observation", json.dumps(projected))
                wire = operations(http)[-1]
                self.assertEqual(wire["variables"]["partition"], -1)
                self.assertEqual(wire["variables"]["zoneId"], 30)
                self.assertEqual(wire["variables"]["timeframe"], target["timeframe"])
                self.assertNotIn("specName", wire["variables"])
                self.assertIn("partition: $partition", wire["query"])
                self.assertEqual(len(http.requests), 4)

    async def test_unknown_enum_is_safe_failure_only_schema(self):
        handler, http = self.handler([schema()])
        result = await handler.invoke(None, {**TARGET, "timeframe": "Unrecognized"})
        self.assertIs(result.error.code, ErrorCode.PARAMETER_ERROR)
        self.assertIsNone(result.document)
        validate_contract(result)
        preflight_error_facts(result)
        tuple(public_fact_strings(result.model_facts))
        projected = project_result(result)
        self.assertEqual(
            projected["model_facts"]["supplement"]["timeframe_enum"],
            ["SyntheticHistorical", "SyntheticCurrent"],
        )
        self.assertEqual(len(http.requests), 1)

    async def test_exact_missing_zero_and_bounded_source_facts(self):
        value = character()
        row = value["data"]["characterData"]["character"]["rankings"]["rankings"][0]
        row.update(rankPercent=None, bestAmount=0, totalKills=0)
        row.pop("spec")
        value["data"]["characterData"]["character"]["rankings"]["rankings"] = [
            copy.deepcopy(row) for _ in range(13)
        ]
        body = (
            json.dumps(value)
            .replace('"bestAmount": 0', '"bestAmount": 0.00000000000000000001')
            .encode()
        )
        handler, _ = self.handler(
            [schema(), *f._character_responses(realm="cn", character=body)]
        )
        result = await handler.invoke(None, TARGET)
        facts = project_result(result)["model_facts"]["fflogs"]
        self.assertEqual(len(facts["rankings"]), 12)
        self.assertTrue(facts["truncated"])
        self.assertEqual(facts["rankings"][0]["bestAmount"], "1E-20")
        self.assertIsNone(facts["rankings"][0]["rankPercent"])
        self.assertEqual(facts["rankings"][0]["totalKills"], "0")
        self.assertIsNone(facts["rankings"][0]["spec"])

    async def test_every_source_row_job_is_validated_before_truncation(self):
        for last in (13, 41, 500):
            for field, bad_job in (
                ("bestSpec", "Bard"),
                ("spec", "Bard"),
                ("spec", "bad\njob"),
            ):
                with self.subTest(last=last, field=field, bad_job=bad_job):
                    value = character()
                    selection = value["data"]["characterData"]["character"]["rankings"]
                    prototype = selection["rankings"][0]
                    selection["rankings"] = [
                        copy.deepcopy(prototype) for _ in range(last)
                    ]
                    for index, row in enumerate(selection["rankings"]):
                        row["encounter"] = {
                            "id": 8000 + index,
                            "name": f"Synthetic encounter {index}",
                        }
                    selection["rankings"][-1][field] = bad_job
                    handler, http = self.handler(
                        [schema(), *f._character_responses(realm="cn", character=value)]
                    )
                    result = await handler.invoke(None, TARGET)
                    self.assertIs(result.error.code, ErrorCode.UNPARSED)
                    self.assertIsNone(result.document)
                    self.assertIsNone(project_result(result)["model_facts"])
                    self.assertEqual(len(http.requests), 4)

    async def test_actual_enum_descriptions_and_unknowns_survive_safe_error(self):
        described = schema()
        values = described["data"]["timeframe"]["enumValues"]
        values[0]["description"] = "Synthetic historical meaning\nfrom the source."
        values[1]["description"] = "Synthetic current meaning"
        handler, http = self.handler([described])
        result = await handler.invoke(None, {**TARGET, "timeframe": "Unrecognized"})
        self.assertIs(result.error.code, ErrorCode.PARAMETER_ERROR)
        validate_contract(result)
        preflight_error_facts(result)
        tuple(public_fact_strings(result.model_facts))
        supplement = project_result(result)["model_facts"]["supplement"]
        self.assertEqual(supplement["timeframe_descriptions"], values)
        self.assertIn("enumValues { name description }", operations(http)[0]["query"])
        self.assertEqual(len(http.requests), 1)
        for description in (None, "", " \n\t"):
            with self.subTest(description=description):
                value = schema()
                value["data"]["timeframe"]["enumValues"][0]["description"] = description
                handler, http = self.handler([value])
                result = await handler.invoke(
                    None, {**TARGET, "timeframe": "Unrecognized"}
                )
                projected = project_result(result)["model_facts"]["supplement"][
                    "timeframe_descriptions"
                ]
                self.assertTrue(all(item["description"] is None for item in projected))
                self.assertEqual(len(http.requests), 1)

    async def test_enum_description_item_and_total_budgets_fail_closed(self):
        for description in (
            512 * "x" + "TOO_LONG_PRIVATE",
            10,
            "bad\x00description",
            "bearer PRIVATE_VALUE",
        ):
            with self.subTest(description=description):
                value = schema()
                value["data"]["timeframe"]["enumValues"][0]["description"] = description
                handler, http = self.handler([value])
                result = await handler.invoke(
                    None, {**TARGET, "timeframe": "Unrecognized"}
                )
                self.assertIs(result.error.code, ErrorCode.UNPARSED)
                self.assertIsNone(result.model_facts)
                self.assertNotIn("PRIVATE", str(project_result(result)))
                self.assertEqual(len(http.requests), 1)
        for length, success in ((80, True), (512, False)):
            value = schema()
            value["data"]["timeframe"]["enumValues"] = [
                {"name": f"T{index}", "description": "d" * length}
                for index in range(32)
            ]
            handler, http = self.handler([value])
            result = await handler.invoke(None, {**TARGET, "timeframe": "Unrecognized"})
            self.assertEqual(len(http.requests), 1)
            if success:
                self.assertIs(result.error.code, ErrorCode.PARAMETER_ERROR)
                preflight_error_facts(result)
                tuple(public_fact_strings(result.model_facts))
                self.assertEqual(
                    len(
                        project_result(result)["model_facts"]["supplement"][
                            "timeframe_descriptions"
                        ]
                    ),
                    32,
                )
            else:
                self.assertIs(result.error.code, ErrorCode.UNPARSED)
                self.assertIsNone(project_result(result)["model_facts"])

    async def test_successful_empty_selection_retains_safe_scope_evidence(self):
        value = character()
        value["data"]["characterData"]["character"]["rankings"]["rankings"] = []
        handler, http = self.handler(
            [schema(), *f._character_responses(realm="cn", character=value)]
        )
        result = await handler.invoke(None, TARGET)
        self.assertIs(result.status, ResultStatus.ERROR)
        self.assertIs(result.error.code, ErrorCode.NO_RECORDS)
        self.assertIsNone(result.document)
        validate_contract(result)
        preflight_error_facts(result)
        tuple(public_fact_strings(result.model_facts))
        facts = project_result(result)["model_facts"]
        self.assertEqual(facts["error"]["message"], result.error.message)
        scope = facts["supplement"]["fflogs"]
        self.assertEqual(scope["rankings"], [])
        self.assertEqual(scope["total_rows"], 0)
        self.assertFalse(scope["truncated"])
        self.assertEqual(scope["requested"]["timeframe"], TARGET["timeframe"])
        self.assertEqual(scope["requested"]["spec_name"], TARGET["spec_name"])
        self.assertEqual(
            scope["selection"],
            {
                "zone": 65,
                "difficulty": 100,
                "partition": -1,
                "metric": "rdps",
                "size": 8,
            },
        )
        self.assertEqual(len(http.requests), 4)

    async def test_failed_or_hidden_empty_response_has_no_successful_empty_facts(self):
        for reason in (
            "hidden",
            "missing_character",
            "wrong_identity",
            "wrong_selection",
        ):
            value = character()
            raw = value["data"]["characterData"]["character"]
            raw["rankings"]["rankings"] = []
            if reason == "hidden":
                raw["hidden"] = True
            elif reason == "missing_character":
                value["data"]["characterData"]["character"] = None
            elif reason == "wrong_identity":
                raw["name"] = "Foreign Hero"
            else:
                raw["rankings"]["partition"] = 3
            handler, _ = self.handler(
                [schema(), *f._character_responses(realm="cn", character=value)]
            )
            target = (
                {**TARGET, "partition": 1} if reason == "wrong_selection" else TARGET
            )
            result = await handler.invoke(None, target)
            self.assertIs(result.status, ResultStatus.ERROR)
            self.assertIsNone(result.model_facts)
        handler, _ = self.handler(
            [
                schema(),
                *f._character_responses(realm="cn")[:-1],
                SourceHttpError("http_403"),
            ]
        )
        result = await handler.invoke(None, TARGET)
        self.assertIs(result.status, ResultStatus.ERROR)
        self.assertIsNone(result.model_facts)

    async def test_local_invalid_inputs_do_no_http(self):
        for change in (
            {"partition": 0},
            {"partition": -2},
            {"partition": True},
            {"partition": None},
            {"timeframe": "bad) { token"},
            {"spec_name": "x\n"},
            {"zone_id": None},
        ):
            with self.subTest(change=change):
                handler, http = self.handler([])
                result = await handler.invoke(None, {**TARGET, **change})
                self.assertIs(result.error.code, ErrorCode.PARAMETER_ERROR)
                self.assertEqual(http.requests, [])
        nozone = {
            key: value
            for key, value in TARGET.items()
            if key not in {"zone_id", "difficulty", "partition"}
        }
        self.assertIsInstance(_character_inputs(nozone), CapabilityResult)

    async def test_old_explicit_and_sentinel_no_extra_schema(self):
        for partition in (-1, 2):
            handler, http = self.handler(
                f._character_responses(realm="cn", character=character(partition))
            )
            target = {
                key: value
                for key, value in TARGET.items()
                if key not in {"timeframe", "spec_name"}
            }
            result = await handler.invoke(None, {**target, "partition": partition})
            self.assertIs(result.status, ResultStatus.SUCCESS)
            self.assertEqual(len(http.requests), 3)
            self.assertIsNone(result.model_facts)
            self.assertEqual(operations(http)[-1]["variables"]["partition"], partition)

    async def test_malformed_schemas_fail_closed(self):
        variants = []
        for value in ("String", "WrongTimeframeType"):
            item = schema()
            item["data"]["character"]["fields"][0]["args"][-1]["type"]["name"] = value
            variants.append(item)
        item = schema()
        item["data"]["character"]["fields"][0]["args"][-2]["type"]["kind"] = "ENUM"
        variants.append(item)
        item = schema()
        item["data"]["character"]["fields"][0]["args"].append(
            copy.deepcopy(item["data"]["character"]["fields"][0]["args"][0])
        )
        variants.append(item)
        item = schema()
        item["data"]["timeframe"]["enumValues"] *= 2
        variants.append(item)
        item = schema()
        item["data"]["timeframe"]["enumValues"] = [
            {"name": "X" + str(i)} for i in range(33)
        ]
        variants.append(item)
        item = schema()
        item["data"]["character"]["fields"] *= 129
        variants.append(item)
        item = schema()
        item["errors"] = [{"message": "SECRET_UNSAFE_PAYLOAD"}]
        variants.append(item)
        item = schema()
        item["data"]["character"]["fields"][0]["args"][0]["defaultValue"] = (
            "SECRET_UNSAFE_PAYLOAD\n"
        )
        variants.append(item)
        item = schema()
        item["data"]["character"]["fields"][0]["args"][0]["type"] = {
            "kind": "LIST",
            "name": None,
            "ofType": {"kind": "SCALAR", "name": "Int"},
        }
        variants.append(item)
        for value in variants:
            with self.subTest(value=value):
                handler, http = self.handler([value])
                result = await handler.invoke(None, TARGET)
                self.assertIs(result.status, ResultStatus.ERROR)
                self.assertNotIn("SECRET_UNSAFE_PAYLOAD", str(result))
                self.assertEqual(len(http.requests), 1)

    async def test_source_errors_fail_closed_without_retry(self):
        for code in ("http_401", "http_403", "http_429", "timeout"):
            handler, http = self.handler([SourceHttpError(code)])
            result = await handler.invoke(None, TARGET)
            self.assertIs(result.status, ResultStatus.ERROR)
            self.assertEqual(len(http.requests), 1)

    async def test_new_filters_rejected_for_chat_and_command(self):
        for origin in (InvocationOrigin.COMMAND, InvocationOrigin.LLM_TOOL):
            handler, http = self.handler([])
            result = await handler._execute(SimpleNamespace(origin=origin), TARGET)
            self.assertIs(result.error.code, ErrorCode.PARAMETER_ERROR)
            self.assertEqual(http.requests, [])

    async def test_mismatch_scope_identity_and_job_rejected(self):
        for field in (
            "partition",
            "zone",
            "difficulty",
            "metric",
            "name",
            "server",
            "bestSpec",
        ):
            value = character(partition=3)
            raw = value["data"]["characterData"]["character"]
            if field in {"partition", "zone", "difficulty"}:
                raw["rankings"][field] = 3 if field == "partition" else 22
            elif field == "metric":
                raw["rankings"][field] = "ndps"
            elif field == "name":
                raw[field] = "Foreign Hero"
            elif field == "server":
                raw[field]["name"] = "Foreign Server"
            else:
                raw["rankings"]["rankings"][0][field] = "Bard"
            handler, http = self.handler(
                [schema(), *f._character_responses(realm="cn", character=value)]
            )
            target = {**TARGET, "partition": 1} if field == "partition" else TARGET
            result = await handler.invoke(None, target)
            self.assertIs(result.error.code, ErrorCode.UNPARSED)
            self.assertEqual(len(http.requests), 4)

    async def test_contract_cache_scoped_and_close_cancels_before_lookup(self):
        handler, http = self.handler(
            [
                schema(),
                *f._character_responses(realm="cn", character=character()),
                *f._character_responses(realm="cn", character=character()),
            ]
        )
        await handler.invoke(None, TARGET)
        await handler.invoke(None, TARGET)
        self.assertEqual(sum("__type" in item["query"] for item in operations(http)), 1)
        self.assertEqual(len(http.requests), 7)
        handler.close()
        self.assertEqual(handler._ranking_contract_cache, {})
        entered = asyncio.Event()

        class Closing:
            async def fetch(self, request):
                entered.set()
                handler.close()
                return await f._FakeHttp([schema()]).fetch(request)

        handler = FFLogsCharacterLookup(f._services(Closing()))
        self.addCleanup(handler.close)
        result = await handler.invoke(None, TARGET)
        self.assertTrue(entered.is_set())
        self.assertIs(result.status, ResultStatus.ERROR)
        self.assertEqual(handler._ranking_contract_cache, {})

    async def test_cancel_preflight_stops_no_cache(self):
        entered, cancelled = asyncio.Event(), asyncio.Event()

        class Hanging:
            def __init__(self):
                self.requests = []

            async def fetch(self, request):
                self.requests.append(request)
                entered.set()
                try:
                    await asyncio.Future()
                finally:
                    cancelled.set()

        http = Hanging()
        handler = FFLogsCharacterLookup(f._services(http))
        self.addCleanup(handler.close)
        task = asyncio.create_task(handler.invoke(None, TARGET))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cancelled.is_set())
        self.assertEqual(len(http.requests), 1)
        self.assertEqual(handler._ranking_contract_cache, {})

    def test_chat_partition_sentinel_grammar(self):
        self.assertEqual(
            message_parameters(
                "cn Cerberus Hero zone_id=65 difficulty=100 partition=-1"
            )["partition"],
            -1,
        )
        for value in ("0", "-2", "True"):
            with self.assertRaises(ValueError):
                message_parameters(
                    "cn Cerberus Hero zone_id=65 difficulty=100 partition=" + value
                )


class ObservationTests(unittest.IsolatedAsyncioTestCase):
    def handler(self, values):
        http = f._FakeHttp(values)
        handler = FFLogsCharacterLookup(f._services(http))
        self.addCleanup(handler.close)
        return handler, http

    def observed(self, result):
        self.assertIs(result.status, ResultStatus.ERROR)
        self.assertIsNone(result.document)
        validate_contract(result)
        preflight_error_facts(result)
        tuple(public_fact_strings(result.model_facts))
        facts = project_result(result)["model_facts"]
        self.assertEqual(
            facts["error"],
            {"code": result.error.code.value, "message": result.error.message},
        )
        value = facts["supplement"]["fflogs_observation"]
        encoded = json.dumps(value)
        for secret in (
            "ATTACKER",
            "Synthetic Hero",
            "Cerberus",
            "12345.6",
            "client_secret",
            "Bearer",
            "traceback",
            "query",
            "headers",
        ):
            self.assertNotIn(secret, encoded)
        self.assertNotIn("rankings", value.get("selection") or {})
        self.assertEqual(value["underlying_cache"], "unknown")
        return value

    async def test_schema_directory_fail_before_any_role_attempt(self):
        cases = (
            ([{"data": {"character": None}}], "schema", 0),
            (
                [schema(), {"data": {"worldData": {"regions": "ATTACKER"}}}],
                "server-directory",
                1,
            ),
            (
                [
                    schema(),
                    f._regions(),
                    {"data": {"worldData": {"region": "ATTACKER"}}},
                ],
                "server-directory",
                2,
            ),
        )
        for values, stage, directory_attempts in cases:
            handler, _ = self.handler(values)
            observed = self.observed(await handler.invoke(WEB, TARGET))
            self.assertEqual(observed["stage"], stage)
            self.assertEqual(observed["fetches"]["character"]["attempts"], 0)
            self.assertEqual(observed["fetches"]["schema"]["attempts"], 1)
            self.assertEqual(
                observed["fetches"]["directory"]["attempts"], directory_attempts
            )
            self.assertEqual(observed["last_http_status"], 200)
            self.assertIsNone(observed["selection"])

    async def test_source_failure_status_and_code_are_closed(self):
        for code, status, safe in (
            ("invalid_response", None, "invalid_response"),
            ("upstream_error", 401, "upstream_error"),
            ("upstream_error", 403, "upstream_error"),
            ("rate_limited", 429, "rate_limited"),
            ("timeout", None, "timeout"),
            ("ATTACKER Bearer client_secret", None, "other_source_error"),
        ):
            handler, _ = self.handler(
                [
                    schema(),
                    *f._character_responses(realm="cn")[:-1],
                    SourceHttpError(code, status_code=status),
                ]
            )
            observed = self.observed(await handler.invoke(WEB, TARGET))
            self.assertEqual(observed["stage"], "character-fetch")
            self.assertEqual(observed["fetches"]["character"]["attempts"], 1)
            self.assertEqual(observed["fetches"]["character"]["returned"], 0)
            self.assertEqual(observed["fetches"]["character"]["failed"], 1)
            self.assertEqual(observed["last_http_status"], status)
            self.assertEqual(observed["source_error"], safe)

    async def test_json_and_character_shape_boundaries(self):
        for body, stage in (
            (b"ATTACKER invalid", "graphql-envelope"),
            ({"data": {"characterData": {"character": "ATTACKER"}}}, "character-shape"),
            ({"data": {"characterData": {"character": None}}}, "character-shape"),
        ):
            handler, _ = self.handler(
                [schema(), *f._character_responses(realm="cn")[:-1], body]
            )
            observed = self.observed(await handler.invoke(WEB, TARGET))
            self.assertEqual(observed["stage"], stage)
            self.assertEqual(observed["fetches"]["character"]["returned"], 1)
            self.assertEqual(observed["last_http_status"], 200)
            self.assertIsNone(observed["selection"])

    async def test_hidden_and_identity_errors_never_attach_selection_or_row_count(self):
        for field, value in (
            ("name", "ATTACKER"),
            ("server.name", "ATTACKER"),
            ("hidden", True),
            ("hidden", None),
            ("id", 0),
        ):
            body = character()
            raw = body["data"]["characterData"]["character"]
            if field == "server.name":
                raw["server"]["name"] = value
            else:
                raw[field] = value
            handler, _ = self.handler(
                [schema(), *f._character_responses(realm="cn", character=body)]
            )
            observed = self.observed(await handler.invoke(WEB, TARGET))
            self.assertIsNone(observed["selection"])
            self.assertIsNone(observed["selection_types"])
            self.assertIsNone(observed["row_count"])
            self.assertNotIn("rows", observed)
        body = character()
        raw = body["data"]["characterData"]["character"]
        raw["name"] = "ATTACKER"
        raw["rankings"]["rankings"][0]["rankPercent"] = "ATTACKER"
        handler, _ = self.handler(
            [schema(), *f._character_responses(realm="cn", character=body)]
        )
        observed = self.observed(await handler.invoke(WEB, TARGET))
        self.assertEqual(observed["failure"]["field"], "character")
        self.assertIsNone(observed["failure"]["index"])

    async def test_actual_partition_minus_one_zero_and_positive_remain_distinct(self):
        for value in (-1, 0, 3):
            body = character(partition=value)
            if value == -1:
                body["data"]["characterData"]["character"]["rankings"]["zone"] = 99
            handler, _ = self.handler(
                [schema(), *f._character_responses(realm="cn", character=body)]
            )
            requested = 1 if value == 3 else -1
            observed = self.observed(
                await handler.invoke(WEB, {**TARGET, "partition": requested})
            )
            self.assertEqual(observed["stage"], "selection")
            self.assertEqual(observed["selection"]["partition"], value)
            self.assertEqual(observed["selection_types"]["partition"], "integer")
            self.assertEqual(observed["requested"]["partition"], requested)
            self.assertEqual(
                observed["failure"]["field"], "zone" if value == -1 else "partition"
            )

    async def test_partition_zero_none_and_positive_mismatch_fail_closed(self):
        for requested, actual in (
            (-1, 0),
            (-1, None),
            (1, 3),
            (1, -1),
            (1, 0),
            (1, None),
        ):
            with self.subTest(requested=requested, actual=actual):
                body = character(partition=3)
                body["data"]["characterData"]["character"]["rankings"]["partition"] = (
                    actual
                )
                handler, http = self.handler(
                    [schema(), *f._character_responses(realm="cn", character=body)]
                )
                result = await handler.invoke(WEB, {**TARGET, "partition": requested})
                self.assertIs(result.error.code, ErrorCode.UNPARSED)
                observed = self.observed(result)
                self.assertEqual(observed["requested"]["partition"], requested)
                self.assertEqual(observed["failure"]["field"], "partition")
                self.assertEqual(
                    observed["stage"],
                    "character-shape" if actual is None else "selection",
                )
                self.assertEqual(observed["selection"]["partition"], actual)
                self.assertEqual(
                    operations(http)[-1]["variables"]["partition"], requested
                )
                self.assertEqual(len(http.requests), 4)

    async def test_invalid_selection_keeps_types_without_arbitrary_values(self):
        target = {key: value for key, value in TARGET.items() if key != "spec_name"}
        for field, value, kind in (
            ("partition", "ATTACKER", "string"),
            ("metric", {"ATTACKER": 1}, "object"),
            ("zone", True, "boolean"),
            ("difficulty", None, "null"),
        ):
            body = character()
            body["data"]["characterData"]["character"]["rankings"][field] = value
            handler, _ = self.handler(
                [schema(), *f._character_responses(realm="cn", character=body)]
            )
            observed = self.observed(await handler.invoke(WEB, target))
            self.assertEqual(observed["stage"], "character-shape")
            self.assertEqual(observed["failure"]["field"], field)
            self.assertEqual(observed["selection_types"][field], kind)
            self.assertIsNone(observed["selection"][field])
            self.assertIsNone(observed["requested"]["spec_name"])

    async def test_parser_first_failed_row_field_has_no_numeric_score(self):
        for key, value in (
            ("rankPercent", "ATTACKER"),
            ("bestAmount", []),
            ("totalKills", True),
        ):
            body = character()
            body["data"]["characterData"]["character"]["rankings"]["rankings"][0][
                key
            ] = value
            handler, _ = self.handler(
                [schema(), *f._character_responses(realm="cn", character=body)]
            )
            observed = self.observed(await handler.invoke(WEB, TARGET))
            self.assertEqual(observed["stage"], "character-shape")
            self.assertEqual(observed["failure"]["field"], key)
            self.assertEqual(observed["failure"]["index"], 0)
            self.assertTrue(observed["failure"]["present"])
            self.assertEqual(observed["row_count"], 1)

    async def test_final_job_failure_has_index_and_booleans_no_job_value(self):
        body = character()
        rows = body["data"]["characterData"]["character"]["rankings"]["rankings"]
        rows *= 500
        rows[-1] = copy.deepcopy(rows[-1])
        rows[-1]["spec"] = "Bard"
        handler, _ = self.handler(
            [schema(), *f._character_responses(realm="cn", character=body)]
        )
        observed = self.observed(await handler.invoke(WEB, TARGET))
        self.assertEqual(observed["stage"], "row-fields")
        self.assertEqual(observed["failure"]["field"], "spec")
        self.assertEqual(observed["failure"]["index"], 499)
        self.assertEqual(observed["row_count"], 500)
        self.assertTrue(observed["failure"]["token_valid"])
        self.assertFalse(observed["failure"]["matches"])
        self.assertNotIn("Bard", json.dumps(observed))

    async def test_schema_help_success_empty_and_old_web_call_unchanged(self):
        handler, _ = self.handler([schema()])
        result = await handler.invoke(WEB, {**TARGET, "timeframe": "Unrecognized"})
        projected = project_result(result)
        self.assertIn("timeframe_enum", projected["model_facts"]["supplement"])
        self.assertNotIn("fflogs_observation", json.dumps(projected))
        for empty in (False, True):
            body = character()
            if empty:
                body["data"]["characterData"]["character"]["rankings"]["rankings"] = []
            handler, _ = self.handler(
                [schema(), *f._character_responses(realm="cn", character=body)]
            )
            self.assertNotIn(
                "fflogs_observation",
                json.dumps(project_result(await handler.invoke(WEB, TARGET))),
            )
        handler, http = self.handler(
            f._character_responses(realm="cn", character=character(2))
        )
        old = {
            key: value
            for key, value in TARGET.items()
            if key not in {"timeframe", "spec_name"}
        } | {"partition": 2}
        self.assertIsNone((await handler.invoke(WEB, old)).model_facts)
        self.assertEqual(len(http.requests), 3)

    async def test_interleaved_calls_keep_independent_stage_and_counters(self):
        entered, release = asyncio.Event(), asyncio.Event()

        class Interleaved:
            async def fetch(self, request):
                query = json.loads(request.body)["query"]
                if "FF14CharacterRankingShape" in query:
                    response = schema()
                elif "FF14WorldRegions" in query:
                    response = (
                        {"data": {"worldData": {"regions": "ATTACKER"}}}
                        if asyncio.current_task().get_name() == "second-observation"
                        else f._regions()
                    )
                elif "FF14WorldServers" in query:
                    response = f._server_page(region="CN")
                else:
                    entered.set()
                    await release.wait()
                    response = b"ATTACKER invalid json"
                return await f._FakeHttp([response]).fetch(request)

        handler = FFLogsCharacterLookup(f._services(Interleaved()))
        self.addCleanup(handler.close)
        first = asyncio.create_task(
            handler.invoke(WEB, TARGET), name="first-observation"
        )
        await asyncio.wait_for(entered.wait(), 1)
        second = await asyncio.create_task(
            handler.invoke(WEB, {**TARGET, "zone_id": 30}), name="second-observation"
        )
        release.set()
        a, b = self.observed(await first), self.observed(second)
        self.assertEqual(
            (a["stage"], b["stage"]), ("graphql-envelope", "server-directory")
        )
        self.assertEqual(
            (
                a["fetches"]["character"]["attempts"],
                b["fetches"]["character"]["attempts"],
            ),
            (1, 0),
        )
        self.assertEqual(
            (a["fetches"]["schema"]["attempts"], b["fetches"]["schema"]["attempts"]),
            (1, 0),
        )
        self.assertEqual(
            (a["requested"]["zone_id"], b["requested"]["zone_id"]), (65, 30)
        )
        self.assertEqual(b["schema_cache"], "module_hit")

    async def test_cancel_and_close_do_not_cache_or_turn_into_success(self):
        entered, cancelled = asyncio.Event(), asyncio.Event()

        class Hanging:
            async def fetch(self, request):
                entered.set()
                try:
                    await asyncio.Future()
                finally:
                    cancelled.set()

        handler = FFLogsCharacterLookup(f._services(Hanging()))
        self.addCleanup(handler.close)
        task = asyncio.create_task(handler.invoke(WEB, TARGET))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cancelled.is_set())
        self.assertEqual(handler._ranking_contract_cache, {})

        class Closing:
            async def fetch(self, request):
                handler.close()
                return await f._FakeHttp([schema()]).fetch(request)

        handler = FFLogsCharacterLookup(f._services(Closing()))
        self.addCleanup(handler.close)
        observed = self.observed(await handler.invoke(WEB, TARGET))
        self.assertEqual(observed["stage"], "schema")
        self.assertEqual(observed["fetches"]["character"]["attempts"], 0)
        self.assertEqual(handler._ranking_contract_cache, {})

    async def test_outer_deadline_retains_error_and_inflight_stage(self):
        entered, cancelled = asyncio.Event(), asyncio.Event()

        class Hanging:
            async def fetch(self, request):
                entered.set()
                try:
                    await asyncio.Future()
                finally:
                    cancelled.set()

        handler = FFLogsCharacterLookup(f._services(Hanging()))
        self.addCleanup(handler.close)
        timeout = asyncio.timeout
        with patch(
            "modules.ff14.features.fflogs.asyncio.timeout",
            side_effect=lambda _: timeout(0.05),
        ):
            result = await handler.invoke(WEB, TARGET)
        observed = self.observed(result)
        self.assertTrue(entered.is_set())
        self.assertTrue(cancelled.is_set())
        self.assertIs(result.error.code, ErrorCode.UPSTREAM_ERROR)
        self.assertEqual(observed["stage"], "schema")
        self.assertEqual(observed["failure"]["guard"], "deadline")
        self.assertEqual(observed["fetches"]["schema"]["cancelled"], 1)

    def test_maximum_and_explicit_fallback_fit_existing_public_error_budget(self):
        metrics = RequestMetrics()
        for name in (
            "OtherBusinessRead",
            "FF14WorldRegions",
            "FF14WorldServers",
            "FF14PublicCharacterZone",
        ):
            metrics.operations[name] = {
                key: 2_147_483_647
                for key in ("attempts", "returned", "failed", "cancelled", "elapsed_ms")
            }
        observation = InvocationObservation(
            {**TARGET, "timeframe": "T" * 80, "spec_name": "S" * 80}, metrics
        )
        raw = character()["data"]["characterData"]["character"]
        raw["rankings"]["partition"] = 0
        observation.character_shape(raw, "Synthetic Hero", "Cerberus")
        observation.stage = "selection"
        observation.reject("selection_mismatch", "partition", value=0)
        observed = self.observed(
            observation.attach(_error(ErrorCode.UNPARSED, "固定安全错误。"))
        )
        self.assertFalse(observed["observation_truncated"])
        self.assertEqual(observed["selection"]["partition"], 0)
        observation.shape = {str(index): "object" for index in range(600)}
        observed = self.observed(
            observation.attach(_error(ErrorCode.UNPARSED, "固定安全错误。"))
        )
        self.assertTrue(observed["observation_truncated"])
        self.assertEqual(observed["requested"]["timeframe"], "T" * 80)
