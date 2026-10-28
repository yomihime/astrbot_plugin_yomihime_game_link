"""Three native capabilities share one isolated Host, not candidate authority."""

import json
import unittest
from types import SimpleNamespace

from . import test_fflogs_tools as logs_fixture
from . import test_item_tools as item_fixture
from . import test_llm_tools as tools_fixture

TOOL_NAMES = {"ff14"}
MARKET_PARAMETERS = {
    "query": "Synthetic",
    "server": "90001",
    "quality": "hq",
    "intent": "listings",
}
MARKET_MESSAGE = "查Synthetic在90001的HQ挂牌价格"


class FF14ChatCapabilitiesHostTests(unittest.IsolatedAsyncioTestCase):
    setUp = tools_fixture.LLMToolTests.setUp
    tearDown = tools_fixture.LLMToolTests.tearDown
    _runtime = tools_fixture.LLMToolTests._runtime
    start = tools_fixture.LLMToolTests.start
    wrapper = tools_fixture.LLMToolTests.wrapper
    call = tools_fixture.LLMToolTests.call
    start_logs = logs_fixture.FFLogsToolHostTests.start_logs
    role = logs_fixture.FFLogsToolHostTests.role
    item = item_fixture.ItemToolHostTests.item
    selection = staticmethod(item_fixture.ItemToolHostTests.selection)

    async def start_capabilities(self):
        # Exactly one initial Host/Core composition. Keep the already accepted
        # FFLogs callback and credential channel; ordinary market/search replies
        # continue through FixtureTransport's established synthetic fallback.
        await self.start_logs()
        self.logs_upstream = self.transport.callback
        self.transport.callback = self.capability_upstream

    async def capability_upstream(self, request):
        if request.source_id in ("fflogs_public_cn", "fflogs_public_global"):
            return await self.logs_upstream(request)
        if request.source_id == "xivapi_items" and "/sheet/Item/" in request.path:
            item_id = int(request.path.rsplit("/", 1)[-1])
            return {
                "row_id": item_id,
                "fields": {
                    "Name": f"Synthetic Item {item_id}",
                    "Description": "Synthetic item only.",
                    "LevelItem": {"value": 7},
                    "LevelEquip": 2,
                },
            }
        if request.source_id == "garland_items":
            item_id = int(request.path.rsplit("/", 1)[-1].split(".")[0])
            return {
                "item": {"id": item_id, "nodes": [1]},
                "partials": [{"type": "node", "id": 1, "obj": {"n": "Synthetic Node"}}],
            }
        return None

    async def market(self):
        return await self.call(
            "ff14_market_query", MARKET_PARAMETERS, self.wrapper(MARKET_MESSAGE)
        )

    async def test_same_session_switches_keep_each_real_confirmation_and_facts(self):
        await self.start_capabilities()
        tools = self.manager.get_full_tool_set()
        self.assertEqual({tool.name for tool in tools.tools}, TOOL_NAMES)
        pending_item = await self.item("查Synthetic的信息和获取途径")
        pending_market = await self.market()
        self.assertEqual(pending_item["status"], "needs_selection")
        self.assertEqual(pending_market["status"], "needs_selection")
        item_args = self.selection(pending_item)
        market_args = self.selection(pending_market)
        self.assertNotEqual(item_args["batch_id"], market_args["batch_id"])

        before = len(self.transport.requests)
        incomplete = await self.role("国服", {"realm": "cn"})
        self.assertEqual(incomplete["status"], "needs_selection")
        self.assertEqual(incomplete["fflogs"]["missing"], ["character", "server"])
        self.assertEqual(len(self.transport.requests), before)
        role = await self.role()
        self.assertEqual(role["status"], "success")
        self.assertEqual(role["fflogs"]["character"]["name"], "Synthetic Hero")
        self.assertEqual(role["fflogs"]["resolved_server"]["name"], "Cerberus")
        self.assertEqual(role["fflogs"]["selection"]["metric"], "rdps")
        self.assertIsNone(role["fflogs"]["source"]["source_updated_at"])
        self.assertTrue(role["fflogs"]["source"]["retrieved_at"])
        self.assertFalse(role["fflogs"]["limitations"]["global_output_percentiles"])

        before = len(self.transport.requests)
        for name, wrong_args in (
            ("ff14_item_select", market_args),
            ("ff14_market_select", item_args),
        ):
            with self.subTest(tool=name):
                denied = await self.call(
                    name, wrong_args, self.wrapper("选择物品 44091")
                )
                self.assertEqual(denied["status"], "error")
                self.assertEqual(len(self.transport.requests), before)

        item = await self.call(
            "ff14_item_select", item_args, self.wrapper("Synthetic Item 44091那个。")
        )
        market = await self.call(
            "ff14_market_select",
            market_args,
            self.wrapper("Synthetic Item 44091那个。"),
        )
        self.assertEqual(item["status"], "success")
        self.assertEqual(market["status"], "success")
        self.assertEqual(item["item"]["routes"][0]["kind"], "node")
        self.assertNotIn("scope", item["item"])
        for field in ("scope", "quality", "intent", "core_revision", "module_revision"):
            self.assertEqual(market["market"][field], pending_market["market"][field])
        prices = market["market"]
        self.assertEqual(prices["currency"], "Gil")
        self.assertEqual(len(prices["listings"]), 5)
        self.assertTrue(prices["truncated"])
        self.assertFalse(prices["limitations"]["complete_world_coverage"])
        self.assertFalse(prices["limitations"]["realtime_availability"])
        self.assertEqual(
            prices["limitations"]["listings_scope"], "bounded_returned_sample"
        )
        row = prices["listings"][0]
        self.assertEqual(row["world_name"], "SyntheticChina")
        self.assertEqual(row["source"]["provider"], "Universalis")
        self.assertTrue(row["source"]["fetched_at"])
        self.assertTrue(row["reviewed"]["source_time"])
        self.assertTrue(row["uploaded"]["source_time"])
        self.assertNotEqual(row["reviewed"], row["uploaded"])
        self.assertEqual(prices["time_semantics"]["uploaded"], "world_upload")
        self.assertIsNone(prices["time_semantics"]["listing_created_at"])
        for facts, own_key in ((item, "item"), (market, "market"), (role, "fflogs")):
            self.assertEqual(
                set(facts).intersection({"item", "market", "fflogs"}), {own_key}
            )
            self.assertNotIn(logs_fixture.SENTINEL, json.dumps(facts))

        before = len(self.transport.requests)
        for name, args in (
            ("ff14_item_select", item_args),
            ("ff14_market_select", market_args),
        ):
            replay = await self.call(name, args, self.wrapper("选择物品 44091"))
            self.assertEqual(replay["status"], "error")
            self.assertEqual(len(self.transport.requests), before)
        self.assertEqual(self.provider.trace, [])

    async def test_all_old_handles_revoked_and_same_runtime_restores_fresh_tools(self):
        await self.start_capabilities()
        pending_item = await self.item("查Synthetic信息")
        pending_market = await self.market()
        self.assertEqual(pending_item["status"], "needs_selection")
        self.assertEqual(pending_market["status"], "needs_selection")
        item_args, market_args = (
            self.selection(pending_item),
            self.selection(pending_market),
        )
        module = self.runtime.core_runtime.registry.snapshot().module("ff14/ff14")
        item_handler = module.handlers.capabilities["item.lookup"]
        market_handler = module.handlers.capabilities["ff14.market.query"]
        self.assertEqual(
            (item_handler._chat.registry.size, market_handler.registry.size), (1, 1)
        )
        old = self.manager.get_full_tool_set()
        old_wrapper = self.wrapper("old scoped aggregate")
        request = SimpleNamespace(func_tool=old, conversation=None)
        await self.runtime._tool_publisher.scope_request(
            old_wrapper.context.event, request
        )
        old = request.func_tool
        old_scoped = old.get_tool("ff14")
        old_transport = self.transport
        calls = (
            ("ff14_item_lookup", {"query": "44091"}, "物品ID 44091的信息"),
            ("ff14_item_select", item_args, "选择物品 44091"),
            (
                "ff14_market_query",
                {"query": "44091", "region": "cn"},
                "物品ID 44091价格",
            ),
            ("ff14_market_select", market_args, "选择物品 44091"),
            ("ff14_logs_character", logs_fixture.TARGET, logs_fixture.MESSAGE),
        )
        await self.runtime.terminate()
        self.assertEqual(self.manager.func_list, [])
        self.assertEqual(
            (item_handler._chat.registry.size, market_handler.registry.size), (0, 0)
        )
        self.assertTrue(old_transport.closed)
        self.assertEqual(old_transport.close_calls, 1)
        before = len(old_transport.requests)
        for name, args, text in calls:
            with (
                self.subTest(tool=name, phase="stopped"),
                self.assertRaises(PermissionError),
            ):
                await old_scoped.call(
                    old_wrapper,
                    action=tools_fixture.FF14_ACTIONS[name],
                    arguments=args,
                )
        self.assertEqual(len(old_transport.requests), before)

        # The runtime's existing factory closes over self.transport. Supply a
        # fresh legal transport before initialize; never revive a closed channel
        # or an old handle. The same isolated DB retains synthetic credentials.
        self.transport = logs_fixture._CredentialTransport()
        self.transport.callback = self.capability_upstream
        await self.runtime.initialize()
        fresh_module = self.runtime.core_runtime.registry.snapshot().module("ff14/ff14")
        self.assertEqual(
            (
                fresh_module.handlers.capabilities["item.lookup"]._chat.registry.size,
                fresh_module.handlers.capabilities["ff14.market.query"].registry.size,
            ),
            (0, 0),
        )
        fresh = self.manager.get_full_tool_set()
        self.assertEqual({tool.name for tool in fresh.tools}, TOOL_NAMES)
        self.assertEqual(len(self.manager.func_list), 1)
        self.assertIsNot(fresh.get_tool("ff14"), old.get_tool("ff14"))
        for name, args, text in calls:
            with (
                self.subTest(tool=name, phase="restarted"),
                self.assertRaises(PermissionError),
            ):
                await old_scoped.call(
                    old_wrapper,
                    action=tools_fixture.FF14_ACTIONS[name],
                    arguments=args,
                )
        self.assertEqual(self.transport.requests, [])
        for name, args in (
            ("ff14_item_select", item_args),
            ("ff14_market_select", market_args),
        ):
            denied = await self.call(name, args, self.wrapper("选择物品 44091"))
            self.assertEqual(denied["status"], "error")
        self.assertEqual(self.transport.requests, [])
        item = await self.item("物品ID 44091的信息", "44091")
        market = await self.call(
            "ff14_market_query",
            {"query": "44091", "region": "cn"},
            self.wrapper("物品ID 44091价格"),
        )
        role = await self.role()
        for facts in (item, market, role):
            self.assertEqual(facts["status"], "success")
        self.assertGreater(self.transport.exchanges, 0)
        self.assertEqual(self.provider.trace, [])
        await self.runtime.terminate()
        self.assertTrue(self.transport.closed)
        self.assertEqual(self.transport.close_calls, 1)
        self.assertEqual(self.manager.func_list, [])


if __name__ == "__main__":
    unittest.main()
