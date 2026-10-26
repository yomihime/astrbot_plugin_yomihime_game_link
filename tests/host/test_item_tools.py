"""Official Host publisher/Core composition; synthetic upstream and tool arguments."""

import json
import unittest
from types import SimpleNamespace

from . import test_astrbot_runtime as runtime_fixture
from . import test_llm_tools as tools_fixture


class ItemToolHostTests(unittest.IsolatedAsyncioTestCase):
    setUp = tools_fixture.LLMToolTests.setUp
    tearDown = tools_fixture.LLMToolTests.tearDown
    _runtime = tools_fixture.LLMToolTests._runtime
    start = tools_fixture.LLMToolTests.start
    wrapper = tools_fixture.LLMToolTests.wrapper
    call = tools_fixture.LLMToolTests.call

    async def start_items(self, *, missing=False):
        await self.start()

        async def source(request):
            if request.source_id == "xivapi_items" and request.path == "/api/search":
                name_query = dict(request.query).get("query", "")
                if "Absent" in name_query:
                    return {"results": []}
                ids = (44091,) if "Exact" in name_query else (44091, 44092)
                return {
                    "results": [
                        {
                            "row_id": i,
                            "fields": {
                                "Name": "Exact"
                                if len(ids) == 1
                                else "Same"
                                if "Same" in name_query
                                else f"Synthetic Item {i}"
                            },
                        }
                        for i in ids
                    ]
                }
            if request.source_id == "xivapi_items" and "/sheet/Item/" in request.path:
                item_id = int(request.path.rsplit("/", 1)[-1])
                fields = {"Name": f"Synthetic Item {item_id}"}
                if not missing:
                    fields.update(
                        Description="Synthetic item only.",
                        LevelItem={"value": 7},
                        LevelEquip=2,
                    )
                return {"row_id": item_id, "fields": fields}
            if request.source_id == "garland_items":
                item_id = int(request.path.rsplit("/", 1)[-1].split(".")[0])
                return {
                    "item": {"id": item_id, "nodes": [1]},
                    "partials": [
                        {"type": "node", "id": 1, "obj": {"n": "Synthetic Node"}}
                    ],
                }
            return None

        self.transport.callback = source

    async def item(self, text, query="Synthetic", **identity):
        return await self.call(
            "ff14_item_lookup", {"query": query}, self.wrapper(text, **identity)
        )

    @staticmethod
    def selection(pending, item_id=44091):
        return {
            "batch_id": pending["selection"]["batch_id"],
            "generation": pending["selection"]["generation"],
            "item_id": item_id,
        }

    async def test_formal_tools_schema_exact_id_and_public_fact_gate(self):
        await self.start_items()
        tools = self.manager.get_full_tool_set()
        schema = tools.get_tool("ff14").parameters
        self.assertEqual(set(schema["required"]), {"action", "arguments"})
        self.assertEqual(
            set(schema["properties"]["action"]["enum"]),
            {
                "market_query",
                "market_select",
                "item_lookup",
                "item_select",
                "logs_character",
            },
        )
        self.assertTrue(
            {"query", "batch_id", "generation", "item_id"}
            <= set(schema["properties"]["arguments"]["properties"])
        )
        exact = await self.item("查Exact怎么获得", "Exact")
        self.assertEqual(exact["status"], "success")
        self.assertEqual(exact["item"]["item_id"], 44091)
        numeric = await self.item("查询物品ID44091的信息", "44091")
        self.assertEqual(numeric["status"], "success")
        self.assertEqual(numeric["item"]["routes"][0]["kind"], "node")
        self.assertIsNone(numeric["error"])
        self.assertNotIn("market", numeric)
        self.assertNotIn("scope", numeric["item"])

    async def test_two_messages_with_synthetic_arguments_require_real_confirmation(
        self,
    ):
        await self.start_items()
        original = self.wrapper("查Synthetic")
        pending = await self.call("ff14_item_lookup", {"query": "Synthetic"}, original)
        args = self.selection(pending)
        before = len(self.transport.requests)
        self.assertEqual(
            (await self.call("ff14_item_select", args, original))["status"], "error"
        )
        choice = {key: value for key, value in args.items() if key != "item_id"}
        self.assertEqual(
            (
                await self.call(
                    "ff14_item_select",
                    {**choice, "action": "clarify"},
                    self.wrapper("哪个更好?"),
                )
            )["status"],
            "needs_selection",
        )
        self.assertEqual(len(self.transport.requests), before)
        facts = await self.call("ff14_item_select", args, self.wrapper("第一个"))
        self.assertEqual(facts["status"], "success")
        self.assertEqual(facts["item"]["query"], "Synthetic")
        self.assertEqual(
            (await self.call("ff14_item_select", args, self.wrapper("第一个")))[
                "status"
            ],
            "error",
        )

    async def test_formal_identity_and_cross_capability_batches_are_isolated(self):
        await self.start_items()
        pending = await self.item("查Synthetic信息")
        args = self.selection(pending)
        for identity in (
            {"sender": "other"},
            {"session": "other"},
            {"adapter": "other"},
        ):
            denied = await self.call(
                "ff14_item_select", args, self.wrapper("选择物品 44091", **identity)
            )
            self.assertEqual(denied["status"], "error")
        market = await self.call(
            "ff14_market_query",
            {"query": "Synthetic", "region": "cn"},
            self.wrapper("Synthetic国服行情"),
        )
        self.assertEqual(market["status"], "needs_selection")
        for tool, wrong_args in (
            ("ff14_item_select", self.selection(market)),
            ("ff14_market_select", args),
        ):
            denied = await self.call(tool, wrong_args, self.wrapper("选择物品 44091"))
            self.assertEqual(denied["status"], "error")
        result = await self.call(
            "ff14_item_select", args, self.wrapper("选择物品 44091")
        )
        self.assertEqual(result["status"], "success")

    async def test_error_and_unknown_fields_cross_core_without_inventing_values(self):
        await self.start_items(missing=True)
        absent = await self.item("查Absent信息", "Absent")
        self.assertEqual(absent["status"], "error")
        self.assertEqual(absent["error"]["code"], "not_found")
        self.assertEqual(set(absent), {"status", "error"})
        result = await self.item("物品ID44091", "44091")
        for field in ("description", "item_level", "equip_level"):
            self.assertIsNone(result["item"][field])
            self.assertIn(field, result["item"]["missing"])
        invented = await self.item("只查Synthetic", "0")
        self.assertEqual(invented["status"], "error")

    async def test_direct_command_keeps_display_and_does_not_call_model(self):
        await self.start_items()
        event = runtime_fixture._Event(context=self.context)
        event.message = "/ygl ff14 item 44091"
        self.assertIsNone(await self.runtime.handle_event(event))
        reply = self.context.calls[-1][1].chain[0].text
        self.assertIn("Synthetic Item 44091", reply)
        self.assertIn("物品 ID：44091", reply)
        self.assertEqual(self.provider.trace, [])
        self.assertTrue(
            all(
                request.source_id in ("xivapi_items", "garland_items")
                for request in self.transport.requests
            )
        )

    async def test_module_stop_clears_pending_and_revokes_item_tools(self):
        await self.start_items()
        module = self.runtime.core_runtime.registry.snapshot().module("ff14/ff14")
        handler = module.handlers.capabilities["item.lookup"]
        old_wrapper = self.wrapper("查Synthetic")
        request = SimpleNamespace(
            func_tool=self.manager.get_full_tool_set(), conversation=None
        )
        await self.runtime._tool_publisher.scope_request(
            old_wrapper.context.event, request
        )
        old = request.func_tool
        old_aggregate = old.get_tool(tools_fixture.FF14_MODEL_TOOL)
        positive = json.loads(
            await old_aggregate.call(
                old_wrapper,
                action=tools_fixture.FF14_ACTIONS["ff14_item_lookup"],
                arguments={"query": "Synthetic"},
            )
        )
        self.assertEqual(positive["status"], "needs_selection")
        pending = positive
        self.assertEqual(handler._chat.registry.size, 1)
        await self.runtime.terminate()
        self.assertEqual(handler._chat.registry.size, 0)
        with self.assertRaises(PermissionError):
            await old_aggregate.call(
                old_wrapper,
                action=tools_fixture.FF14_ACTIONS["ff14_item_select"],
                arguments=self.selection(pending),
            )

    async def test_duplicate_exact_original_name_new_event_replaces_batch(self):
        await self.start_items()
        source = self.wrapper("查Same")
        old = await self.call("ff14_item_lookup", {"query": "Same"}, source)
        retry = await self.call("ff14_item_lookup", {"query": "Same"}, source)
        self.assertEqual(old["selection"], retry["selection"])
        before = len(self.transport.requests)
        choice = {key: old["selection"][key] for key in ("batch_id", "generation")}
        self.assertEqual(
            (
                await self.call(
                    "ff14_item_select",
                    {**choice, "action": "clarify"},
                    self.wrapper("叫Same的那个"),
                )
            )["status"],
            "needs_selection",
        )
        self.assertEqual(len(self.transport.requests), before)
        new = await self.item("重新查相同物品", "Same")
        for field in ("batch_id", "generation"):
            self.assertNotEqual(old["selection"][field], new["selection"][field])
        self.assertEqual(
            (
                await self.call(
                    "ff14_item_select", self.selection(old), self.wrapper("第一个")
                )
            )["status"],
            "error",
        )
        self.assertEqual(
            (
                await self.call(
                    "ff14_item_select", self.selection(new), self.wrapper("第一个")
                )
            )["status"],
            "success",
        )


if __name__ == "__main__":
    unittest.main()
