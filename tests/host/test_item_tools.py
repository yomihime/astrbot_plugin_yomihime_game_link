"""Official Host publisher/Core composition; synthetic upstream and tool arguments."""

import unittest

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
        schema = tools.get_tool("ff14_item_lookup").parameters
        self.assertEqual(set(schema["properties"]), {"query"})
        self.assertEqual(
            set(tools.get_tool("ff14_item_select").parameters["properties"]),
            {"batch_id", "generation", "item_id"},
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
        pending = await self.item("帮我查Synthetic的信息")
        self.assertEqual(pending["status"], "needs_selection")
        args = self.selection(pending)
        # Synthetic tool arguments submit the candidate ID; the real event must
        # independently establish that same choice before any detail fetch.
        before = len(self.transport.requests)
        denied = await self.call("ff14_item_select", args, self.wrapper("第一个"))
        self.assertEqual(denied["status"], "error")
        self.assertEqual(len(self.transport.requests), before)
        facts = await self.call(
            "ff14_item_select", args, self.wrapper("Synthetic Item 44091那个。")
        )
        self.assertEqual(facts["status"], "success")
        self.assertEqual(facts["item"]["query"], "Synthetic")
        self.assertEqual(facts["item"]["name"], "Synthetic Item 44091")
        replay = await self.call(
            "ff14_item_select", args, self.wrapper("选择物品 44091")
        )
        self.assertEqual(replay["status"], "error")

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
        invented = await self.item("只查Synthetic", "Unprovided")
        self.assertEqual(invented["status"], "error")

    async def test_direct_command_keeps_display_and_does_not_call_model(self):
        await self.start_items()
        event = runtime_fixture._Event()
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
        pending = await self.item("查Synthetic")
        module = self.runtime.core_runtime.registry.snapshot().module("ff14/ff14")
        handler = module.handlers.capabilities["item.lookup"]
        old = self.manager.get_full_tool_set()
        self.assertEqual(handler._chat.registry.size, 1)
        await self.runtime.terminate()
        self.assertEqual(handler._chat.registry.size, 0)
        with self.assertRaises(PermissionError):
            await self.call(
                "ff14_item_select",
                self.selection(pending),
                self.wrapper("选择物品 44091"),
                toolset=old,
            )

    async def test_duplicate_exact_original_name_new_event_replaces_batch(self):
        await self.start_items()
        source_event = self.wrapper("查Same")
        old = await self.call("ff14_item_lookup", {"query": "Same"}, source_event)
        self.assertEqual(old["status"], "needs_selection")
        self.assertEqual(
            [item["name"] for item in old["selection"]["candidates"]],
            ["Same", "Same"],
        )
        retry = await self.call("ff14_item_lookup", {"query": "Same"}, source_event)
        self.assertEqual(old["selection"], retry["selection"])
        before = len(self.transport.requests)
        for text in (
            "Same",
            "Same那个",
            "Same？",
            "再查Same？",
            "不是Same",
            "第一个",
            "选择Same",
        ):
            with self.subTest(text=text):
                denied = await self.item(text, "Same")
                self.assertEqual(denied["status"], "error")
                self.assertEqual(len(self.transport.requests), before)
        new = await self.item("再查Same", "Same")
        self.assertEqual(new["status"], "needs_selection")
        for field in ("batch_id", "generation"):
            self.assertNotEqual(old["selection"][field], new["selection"][field])
        before_selection = len(self.transport.requests)
        denied = await self.call(
            "ff14_item_select", self.selection(old), self.wrapper("选择物品 44091")
        )
        self.assertEqual(denied["status"], "error")
        self.assertEqual(len(self.transport.requests), before_selection)
        ambiguous = await self.call(
            "ff14_item_select", self.selection(new), self.wrapper("Same")
        )
        self.assertEqual(ambiguous["status"], "error")
        result = await self.call(
            "ff14_item_select", self.selection(new), self.wrapper("选择物品 44091")
        )
        self.assertEqual(result["status"], "success")


if __name__ == "__main__":
    unittest.main()
