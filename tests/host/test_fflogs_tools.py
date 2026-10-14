"""Official role tools and Core with isolated synthetic credentials/upstream."""

import base64
import json
import os
import time
import unittest
from unittest.mock import patch

from ygl_test_subject.core.contracts.administration import AdminOperation
from ygl_test_subject.core.contracts.validation_boundary import validate_contract

from tests.modules.ff14 import test_fflogs as fflogs_fixture
from yomihime_game_link_sdk.services import HttpResponse

from . import test_astrbot_runtime as runtime_fixture
from . import test_llm_tools as tools_fixture

TARGET = {"realm": "cn", "server": "Cerberus", "character": "Synthetic Hero"}
MESSAGE = '查 FFLogs 国服 Cerberus "Synthetic Hero"'
SENTINEL = "synthetic-private-S2-credential-value"


class _CredentialTransport(tools_fixture.FixtureTransport):
    """Supply the existing authenticated channel contract before Core starts."""

    def __init__(self):
        super().__init__()
        self._credential_channel = object()
        self.exchanges = 0

    async def request_credential_exchange(self, request):
        # The real credential service/HTTP proof path sends this to the
        # synthetic transport. Neither credential body nor headers are logged.
        self.exchanges += 1
        return validate_contract(
            HttpResponse(
                200,
                {},
                json.dumps(
                    {
                        "access_token": "synthetic-S2-access-token",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                    }
                ).encode(),
            )
        )


class FFLogsToolHostTests(unittest.IsolatedAsyncioTestCase):
    setUp = tools_fixture.LLMToolTests.setUp
    tearDown = tools_fixture.LLMToolTests.tearDown
    _runtime = tools_fixture.LLMToolTests._runtime
    start = tools_fixture.LLMToolTests.start
    wrapper = tools_fixture.LLMToolTests.wrapper
    call = tools_fixture.LLMToolTests.call

    async def start_logs(self, *, configured=True, count=1):
        environment = patch.dict(
            os.environ, {"YGL_SECRET_KEY": base64.b64encode(bytes(range(32))).decode()}
        )
        environment.start()
        self.addCleanup(environment.stop)
        # Borrow the unchanged publisher fixture with a credential-capable
        # transport constructor. SourceHttp captures the same channel at init;
        # adding one afterward would not establish that proof.
        with patch.object(tools_fixture, "FixtureTransport", _CredentialTransport):
            await self.start()

        async def upstream(request):
            if request.source_id not in ("fflogs_public_cn", "fflogs_public_global"):
                return None
            body = json.loads(request.body)
            variables = body["variables"]
            if "FF14WorldRegions" in body["query"]:
                return fflogs_fixture._regions()
            if "regionId" in variables:
                regions = {1: "NA", 2: "EU", 3: "JP", 4: "CN", 5: "OC"}
                region = regions[variables["regionId"]]
                rows = (
                    [fflogs_fixture._server_row(region=region)]
                    if region in ("CN", "NA")
                    else []
                )
                return fflogs_fixture._server_page(rows, region=region)
            result = fflogs_fixture._character(
                metric=variables["metric"],
                zone_id=variables.get("zoneId", 73),
                difficulty=variables.get("difficulty", 101),
                partition=variables.get("partition", -1),
            )
            ranking = result["data"]["characterData"]["character"]["rankings"]
            ranking["rankings"] = [
                {
                    "encounter": {"id": i + 1, "name": f"Synthetic Trial {i}"},
                    "rankPercent": None,
                    "bestAmount": 0,
                    "bestSpec": None,
                }
                for i in range(count)
            ]
            result["data"]["characterData"]["character"]["private_token"] = SENTINEL
            return result

        self.transport.callback = upstream
        if configured:
            ops = self.runtime.core_runtime.admin_operations
            source = self.runtime._credential_admin_source
            proof = source.issue(
                subject="synthetic-test-admin",
                request=object(),
                expiry=time.time() + 60,
                operations={AdminOperation.READ_CONFIG, AdminOperation.UPDATE_CONFIG},
                resources=ops.credential_resources(),
                live=lambda _: True,
            )
            try:
                status = await ops.credential_status(authorization=proof)
                result = await ops.credential_update(
                    {
                        "module_id": "ff14/ff14",
                        "expected_revision": status["ff14/ff14"]["revision"],
                        "updates": [
                            {
                                "field": field,
                                "mode": "replace",
                                "value": {
                                    "client_id": "synthetic-client",
                                    "client_secret": SENTINEL,
                                },
                            }
                            for field in (
                                "credential_fflogs_cn",
                                "credential_fflogs_global",
                            )
                        ],
                    },
                    authorization=proof,
                )
                self.assertTrue(result)
            finally:
                source.end(proof)

    async def role(self, text=MESSAGE, parameters=None, **identity):
        return await self.call(
            "ff14_logs_character",
            TARGET if parameters is None else parameters,
            self.wrapper(text, **identity),
        )

    async def test_schema_public_role_actual_metric_filter_source_and_unknown_facts(
        self,
    ):
        await self.start_logs()
        tools = self.manager.get_full_tool_set()
        schema = tools.get_tool("ff14_logs_character").parameters
        self.assertEqual(set(schema.get("required", [])), set())
        self.assertEqual(
            set(schema["properties"]),
            {
                "realm",
                "server",
                "character",
                "server_hint",
                "metric",
                "zone_id",
                "difficulty",
                "partition",
                "action",
                "request_language",
                "context_language",
                "batch_id",
                "generation",
                "server_id",
            },
        )
        self.assertEqual(
            {tool.name for tool in tools.tools},
            {
                "ff14_market_query",
                "ff14_market_select",
                "ff14_item_lookup",
                "ff14_item_select",
                "ff14_logs_character",
            },
        )
        result = await self.role()
        self.assertEqual(result["status"], "success")
        self.assertGreater(self.transport.exchanges, 0)
        facts = result["fflogs"]
        self.assertEqual(facts["selection"]["metric"], "rdps")
        self.assertEqual(facts["selection"]["partition"], -1)
        self.assertEqual(facts["resolved_server"]["region"], "CN")
        self.assertIsNone(facts["rankings"][0]["rank_percent"])
        self.assertEqual(facts["rankings"][0]["best_amount"], 0)
        self.assertIsNone(facts["source"]["source_updated_at"])
        self.assertIn("cn.fflogs.com/character/CN", facts["source"]["url"])
        self.assertNotIn(SENTINEL, json.dumps(result))
        filtered = await self.role(
            MESSAGE + " metric=ndps zone_id=2 difficulty=3 partition=4",
            {**TARGET, "metric": "ndps", "zone_id": 2, "difficulty": 3, "partition": 4},
        )
        self.assertEqual(filtered["status"], "success")
        self.assertEqual(filtered["fflogs"]["selection"]["zone_id"], 2)
        self.assertEqual(filtered["fflogs"]["selection"]["metric"], "ndps")

    async def test_current_complete_message_only_no_model_history_or_shortening(self):
        await self.start_logs()
        pending = await self.role("查角色", {"character": "Synthetic Hero"})
        self.assertEqual(pending["status"], "needs_selection")
        self.assertEqual(pending["fflogs"]["missing"], ["server"])
        self.assertEqual(self.transport.requests, [])
        valid = await self.role(
            "补充Cerberus国服",
            {"server": "Cerberus", "realm": "cn", "action": "continue"},
        )
        self.assertEqual(valid["status"], "success")
        self.assertGreater(self.transport.exchanges, 0)
        foreign_context = self.host.Context()
        foreign = self.wrapper(MESSAGE, context=foreign_context)
        before = len(self.transport.requests)
        exchanges = self.transport.exchanges
        with self.assertRaises(PermissionError):
            await self.call("ff14_logs_character", TARGET, foreign)
        self.assertEqual(len(self.transport.requests), before)
        self.assertEqual(self.transport.exchanges, exchanges)

    async def test_unset_credentials_are_explicit_without_http(self):
        await self.start_logs(configured=False)
        unset = await self.role()
        self.assertEqual(unset["status"], "error")
        self.assertEqual(unset["supplement"]["fflogs"]["credential_state"], "unset")
        self.assertEqual(self.transport.requests, [])
        self.assertEqual(self.transport.exchanges, 0)
        self.assertNotIn(SENTINEL, json.dumps(unset))

    async def test_configured_unavailable_does_not_invent_a_crypto_cause(self):
        await self.start_logs()
        store = self.runtime.core_runtime.secret_store
        codec = store.codec
        store.codec = None
        try:
            result = await self.role()
        finally:
            store.codec = codec
        self.assertEqual(result["error"]["code"], "auth_required")
        self.assertEqual(
            result["supplement"]["fflogs"]["credential_state"],
            "configured_but_unavailable",
        )
        self.assertIsNone(result["supplement"]["fflogs"]["credential_failure_reason"])
        self.assertIn("原因未公开", result["error"]["message"])
        self.assertNotIn(SENTINEL, json.dumps(result))

    async def test_500_rows_cross_actual_core_public_budget_as_finite_partial(self):
        await self.start_logs(count=500)
        result = await self.role()
        self.assertEqual(result["status"], "partial_success")
        self.assertGreater(self.transport.exchanges, 0)
        self.assertEqual(result["fflogs"]["returned_rows"], 20)
        self.assertEqual(result["fflogs"]["total_rows"], 500)
        self.assertTrue(result["fflogs"]["truncated"])
        self.assertNotIn(SENTINEL, json.dumps(result))

    async def test_direct_command_keeps_no_model_and_stop_revokes_role_tool(self):
        await self.start_logs()
        event = runtime_fixture._Event()
        event.message = '/ygl ff14 logs 国服 Cerberus "Synthetic Hero"'
        self.assertIsNone(await self.runtime.handle_event(event))
        reply = self.context.calls[-1][1].chain[0].text
        self.assertIn("Synthetic Hero", reply)
        self.assertIn("FFLogs", reply)
        self.assertEqual(self.provider.trace, [])
        self.assertGreater(self.transport.exchanges, 0)
        old = self.manager.get_full_tool_set()
        await self.runtime.terminate()
        with self.assertRaises(PermissionError):
            await self.call(
                "ff14_logs_character", TARGET, self.wrapper(MESSAGE), toolset=old
            )


if __name__ == "__main__":
    unittest.main()
