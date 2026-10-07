"""Fixed Host tool calls, real Core and synthetic upstream/model; no live LLM."""

from __future__ import annotations

import ast
import asyncio
import json
import re
import unittest
from types import SimpleNamespace

from ygl_test_subject.adapters.astrbot.tool_publisher import ToolPublicationError
from ygl_test_subject.infrastructure.sqlite.executor import SQLiteExecutorClosedError
from ygl_test_subject.modules.ff14.query_resolution import TrustedOwner
from ygl_test_subject.services.core_runtime import CoreRuntimeCleanupPending

from yomihime_sdk.api.contexts import InvocationOrigin
from yomihime_sdk.api.display import Privacy
from yomihime_sdk.api.results import (
    CapabilityResult,
    ErrorCode,
    ErrorDetail,
    ResultStatus,
)

from . import test_astrbot_runtime as fixture
from .test_market_integration import FixtureTransport
from .tool_contract import tool_contracts


class ControlledProvider:
    """Deterministic model stub consuming user events and actual Host schemas."""

    def __init__(self):
        self.trace, self.pending = [], None

    async def chat(self, wrapper, toolset):
        import jsonschema

        text = wrapper.context.event.get_message_str()
        schemas = {tool.name: tool.parameters for tool in toolset.tools}
        selected = re.fullmatch(r"选择物品 ([0-9]+)", text)
        named = (
            [
                item
                for item in self.pending["selection"]["candidates"]
                if text in (item["name"], item["name"] + "那个。")
            ]
            if self.pending
            else []
        )
        if self.pending and (selected or len(named) == 1):
            name = "ff14_market_select"
            args = {
                key: self.pending["selection"][key]
                for key in ("batch_id", "generation")
            }
            args["item_id"] = int(selected[1]) if selected else named[0]["item_id"]
        else:
            name = "ff14_market_query"
            args = {
                "query": "Synthetic",
                "server": "90001",
                "quality": "hq",
                "intent": "min",
            }
            if text == "帮我看看犎牛牛排国服哪里最便宜":
                args = {"query": "犎牛牛排", "region": "cn", "intent": "min"}
            elif text == "帮我看看犎牛牛排哪里最便宜":
                args = {"query": "犎牛牛排", "intent": "min"}
            elif text == "牛排国服哪里最便宜？":
                args = {"query": "牛排", "region": "cn", "intent": "min"}
            elif "Synthetic" not in text:
                raise AssertionError(
                    "stub has no tool-selection fixture for this message"
                )
        jsonschema.validate(args, schemas[name])
        facts = json.loads(await toolset.get_tool(name).call(wrapper, **args))
        if facts["status"] == "needs_selection":
            self.pending = facts
            reply = (
                "请回复“"
                + facts["name_confirmation"]
                + "”："
                + ", ".join(item["name"] for item in facts["selection"]["candidates"])
            )
        else:
            limits = facts["market"]["limitations"]
            reply = str(facts["market"]["minimums"])
            if limits["minimum_scope"] == "available_returned_scopes":
                reply += "；仅已返回范围，不能称完整全服最低"
            if not limits["realtime_availability"]:
                reply += "；不保证实时可买"
        self.trace.append(
            dict(
                user=text,
                schemas=schemas,
                tool=name,
                arguments=args,
                status=facts["status"],
                facts=facts,
                reply=reply,
            )
        )
        return facts, reply


class LLMToolTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.AstrBotRuntimeTests.setUp
    _runtime = fixture.AstrBotRuntimeTests._runtime

    def tearDown(self):
        if hasattr(self, "contracts"):
            self.contracts.__exit__(None, None, None)
        fixture.AstrBotRuntimeTests.tearDown(self)

    async def start(self):
        self.contracts = tool_contracts()
        self.host = host = self.contracts.__enter__()

        class Context(host.Context, fixture._Context):
            def __init__(self):
                fixture._Context.__init__(self)
                manager = host.Manager.__new__(host.Manager)
                manager.func_list = []
                self.provider_manager = SimpleNamespace(llm_tools=manager)

        self.context = Context()
        self.transport = FixtureTransport()
        self.runtime = self._runtime(
            self.context, http_transport_factory=lambda: self.transport
        )
        self.addAsyncCleanup(self.runtime.terminate)
        await self.runtime.initialize()
        self.manager = self.context.provider_manager.llm_tools
        self.provider = ControlledProvider()

    def wrapper(
        self,
        text="查物品ID 44091的价格",
        *,
        sender="user-17",
        session="group-42",
        adapter="test-platform-1",
        context=None,
        role="member",
    ):
        host = self.host
        event = host.Event()
        event.message_str, event.role = text, role
        event.platform_meta = SimpleNamespace(id=adapter)
        event.message_obj = SimpleNamespace(
            sender=SimpleNamespace(user_id=sender), type=host.MessageType.GROUP_MESSAGE
        )
        event.session = SimpleNamespace(
            session_id=session, message_type=host.MessageType.GROUP_MESSAGE
        )
        return host.ContextWrapper(
            context=host.AstrAgentContext(context=context or self.context, event=event)
        )

    async def call(
        self, name="ff14_market_query", parameters=None, wrapper=None, toolset=None
    ):
        tools = toolset or self.manager.get_full_tool_set()
        value = await tools.get_tool(name).call(
            wrapper or self.wrapper(),
            **(parameters or {"query": "44091", "region": "cn"}),
        )
        return json.loads(value) if value.startswith("{") else value

    async def assert_core_closed(self, core, module):
        self.assertTrue(core.closed)
        self.assertFalse(core.registry.is_active(module))
        self.assertIsNone(core._pump_task)
        with self.assertRaises(SQLiteExecutorClosedError):
            await core.database.executor.run_read(
                lambda unit: unit.execute("SELECT 1").fetchone()[0]
            )

    async def test_global_all_intents_cross_real_core_fact_gate(self):
        from collections.abc import Mapping

        def nodes(value):
            if isinstance(value, Mapping):
                return 1 + sum(nodes(k) + nodes(v) for k, v in value.items())
            if isinstance(value, (tuple, list)):
                return 1 + sum(nodes(v) for v in value)
            return 1

        await self.start()
        handler = (
            self.runtime.core_runtime.registry.snapshot()
            .module("ff14/ff14")
            .handlers.capabilities["ff14.market.query"]
        )
        original = handler.invoke
        counts = []

        async def measured(*args):
            result = await original(*args)
            counts.append(
                nodes(result.model_facts.facts) + len(result.model_facts.sources)
            )
            print(
                "FF14 public fact nodes:", args[1].get("intent"), counts[-1], flush=True
            )
            return result

        handler.invoke = measured
        for intent in ("min", "overview"):
            with self.subTest(intent=intent):
                before = len(
                    [r for r in self.transport.requests if "/aggregated/" in r.path]
                )
                facts = await self.call(
                    parameters={
                        "query": "44091",
                        "region": "global",
                        "quality": "all",
                        "intent": intent,
                    }
                )
                self.assertEqual(facts["status"], "success")
                self.assertLessEqual(counts[-1], 512)
                market = facts["market"]
                self.assertEqual(len(market["coverage"]), 4)
                self.assertTrue(
                    all(row["state"] == "available" for row in market["coverage"])
                )
                self.assertEqual(
                    {row["quality"] for row in market["minimums"]}, {"nq", "hq"}
                )
                self.assertTrue(
                    all(
                        row["world_name_state"] == "verified"
                        for row in market["minimums"]
                    )
                )
                self.assertFalse(market["limitations"]["realtime_availability"])
                self.assertEqual(
                    len(
                        [r for r in self.transport.requests if "/aggregated/" in r.path]
                    )
                    - before,
                    4,
                )
                before = len(self.transport.requests)
                cached = await self.call(
                    parameters={
                        "query": "44091",
                        "region": "global",
                        "quality": "all",
                        "intent": intent,
                    }
                )
                self.assertEqual(cached["status"], "success")
                self.assertTrue(
                    all(row["cached"] for row in cached["market"]["coverage"])
                )
                self.assertEqual(len(self.transport.requests), before)
        self.assertEqual(
            len([r for r in self.transport.requests if "/aggregated/" in r.path]), 8
        )

    async def test_same_query_new_event_reparses_but_same_event_keeps_batch(self):
        await self.start()
        parameters = {
            "query": "Synthetic",
            "region": "cn",
            "quality": "all",
            "intent": "overview",
        }
        user = self.wrapper("Synthetic国服行情")
        pending = await self.call(parameters=parameters, wrapper=user)
        retry = await self.call(
            parameters={
                **parameters,
                "region": "global",
                "quality": "hq",
                "intent": "min",
            },
            wrapper=user,
        )
        self.assertEqual(retry["selection"], pending["selection"])
        self.assertEqual(retry["market"], pending["market"])
        for field, value, text in (
            ("region", "global", "Synthetic国际服行情？"),
            ("quality", "hq", "Synthetic国服HQ行情？"),
            ("intent", "min", "Synthetic国服最低价？"),
            ("intent", "overview", "Synthetic什么价？"),
        ):
            with self.subTest(field=field):
                fresh = await self.call(
                    parameters={**parameters, field: value}, wrapper=self.wrapper(text)
                )
                self.assertNotEqual(
                    fresh["selection"]["generation"], pending["selection"]["generation"]
                )
                self.assertEqual(
                    fresh["market"][field]
                    if field != "region"
                    else fresh["market"]["scope"]["regions"],
                    value
                    if field != "region"
                    else ["North-America", "Europe", "Japan", "Oceania"],
                )
                stale = {
                    key: pending["selection"][key] for key in ("batch_id", "generation")
                }
                stale["item_id"] = 44091
                self.assertEqual(
                    (
                        await self.call(
                            "ff14_market_select", stale, self.wrapper("选择物品 44091")
                        )
                    )["status"],
                    "error",
                )
                pending = fresh

    async def test_global_missing_metrics_and_warning_bound_cross_core_gate(self):
        from collections.abc import Mapping
        from dataclasses import replace
        from unittest.mock import patch

        from .test_market_integration import aggregated

        def nodes(value):
            if isinstance(value, Mapping):
                return 1 + sum(nodes(k) + nodes(v) for k, v in value.items())
            if isinstance(value, (tuple, list)):
                return 1 + sum(nodes(v) for v in value)
            return 1

        await self.start()

        async def missing(request):
            if "/aggregated/" in request.path:
                payload = aggregated(request.path.split("/")[-2])
                for quality in ("nq", "hq"):
                    for metric in (
                        "recentPurchase",
                        "averageSalePrice",
                        "dailySaleVelocity",
                    ):
                        payload["results"][0][quality].pop(metric)
                return payload
            return None

        self.transport.callback = missing
        handler = (
            self.runtime.core_runtime.registry.snapshot()
            .module("ff14/ff14")
            .handlers.capabilities["ff14.market.query"]
        )
        client_type = type(handler).invoke.__globals__["MarketClient"]
        original = client_type.execute

        async def warnings(client, query):
            execution = await original(client, query)
            return replace(
                execution,
                result=replace(
                    execution.result,
                    warnings=tuple("Synthetic warning " + str(i) for i in range(8)),
                ),
            )

        with patch.object(client_type, "execute", warnings):
            for intent in ("min", "overview"):
                with self.subTest(intent=intent):
                    facts = await self.call(
                        parameters={
                            "query": "44091",
                            "region": "global",
                            "quality": "all",
                            "intent": intent,
                        }
                    )
                    self.assertEqual(facts["status"], "partial_success")
                    print(
                        "FF14 missing metric public fact nodes:",
                        intent,
                        nodes(facts),
                        flush=True,
                    )
                    self.assertLessEqual(nodes(facts), 512)
                    market = facts["market"]
                    self.assertEqual(len(market["warnings"]), 8)
                    self.assertFalse(market["coverage_complete"])
                    self.assertEqual(len(market["coverage"]), 4)
                    self.assertTrue(
                        all(row["state"] == "available" for row in market["coverage"])
                    )
                    for row in market["minimums"]:
                        self.assertEqual(row["price_per_unit"], 100)
                        self.assertEqual(row["world_name_state"], "verified")
                        self.assertIsNotNone(row["time"]["source_time"])
                        self.assertIsNotNone(row["source"]["fetched_age_seconds"])
                    for scope in market["coverage"]:
                        for quote in scope["quotes"]:
                            if intent == "overview":
                                self.assertIsNone(quote["recent_purchase"])
                                self.assertIsNone(quote["average_sale_price"])
                                self.assertIsNone(quote["daily_sale_velocity"])
                                self.assertEqual(
                                    quote["missing"],
                                    [
                                        "recent_purchase",
                                        "average_sale_price",
                                        "daily_sale_velocity",
                                    ],
                                )
                            else:
                                self.assertEqual(quote["missing"], [])
                                self.assertNotIn("recent_purchase", quote)

    async def test_missing_query_rejected_in_module_without_losing_candidate(self):
        await self.start()
        pending = await self.call(
            parameters={"query": "Synthetic"}, wrapper=self.wrapper("Synthetic什么价")
        )
        query_tool = self.manager.get_full_tool_set().get_tool("ff14_market_query")
        self.assertEqual(query_tool.parameters["required"], [])
        before = len(self.transport.requests)
        missing = json.loads(
            await query_tool.call(self.wrapper("Synthetic什么价"), region="global")
        )
        self.assertEqual(missing["status"], "error")
        self.assertEqual(missing["error"]["code"], "parameter_error")
        self.assertEqual(len(self.transport.requests), before)
        selected = await self.call(
            "ff14_market_select",
            {
                **{
                    key: pending["selection"][key] for key in ("batch_id", "generation")
                },
                "item_id": 44091,
            },
            self.wrapper("选择物品 44091"),
        )
        self.assertEqual(selected["status"], "success")

    async def test_generic_publisher_query_is_not_an_item_contract(self):
        from uuid import uuid4

        from yomihime_sdk.api.display import DisplayDocument, TextBlock
        from yomihime_sdk.api.manifests import (
            CapabilityDescriptor,
            CapabilityEffect,
            InvocationPolicy,
            ModuleCategory,
            ModuleManifest,
            PackageManifest,
            ToolDescriptor,
        )
        from yomihime_sdk.api.results import FactDocument
        from yomihime_sdk.api.services import (
            CapabilityHealth,
            HealthReport,
            HealthStatus,
            ModuleHandlers,
        )
        from yomihime_sdk.api.version import CONTRACT_VERSION

        await self.start()
        descriptor = ToolDescriptor(
            name="generic_query",
            description="generic",
            capability_id="read",
            parameter_mapping={"query": "text"},
        )
        capability = CapabilityDescriptor(
            capability_id="read",
            invocation_policy=InvocationPolicy.NATURAL_LANGUAGE_ALLOWED,
            effect=CapabilityEffect.READ_ONLY,
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
        )
        manifest = ModuleManifest(
            module_id="module",
            route="generic",
            category=ModuleCategory.GAME,
            factory_entry="synthetic:Factory",
            module_version="1.0.0",
            capabilities=(capability,),
            tools=(descriptor,),
            commands=(),
        )
        seen = []

        class Handler:
            async def invoke(self, context, parameters):
                seen.append(dict(parameters))
                return CapabilityResult(
                    "generic",
                    ResultStatus.SUCCESS,
                    DisplayDocument("Generic", "generic", (TextBlock("ok"),)),
                    model_facts=FactDocument({"answer": parameters["text"]}),
                )

        handlers = ModuleHandlers({"read": Handler()}, {}, {})

        class Instance:
            def handlers(self):
                return handlers

            async def start(self):
                pass

            async def stop(self):
                pass

            async def check_health(self):
                return HealthReport({"read": CapabilityHealth(HealthStatus.AVAILABLE)})

        core = self.runtime.core_runtime
        core.registry.register_package(
            PackageManifest(
                package_id="generic",
                package_version="1.0.0",
                contract_version=CONTRACT_VERSION,
                modules=(manifest,),
                author="Tests",
                license="AGPL-3.0",
                source="synthetic fixture",
            ),
            {"module": handlers},
        )
        instance, install_id, run_id = Instance(), uuid4().hex, uuid4().hex
        adopted = core.lifecycle.adopt_candidate(
            "generic", manifest, install_id, instance
        )
        core.lifecycle.install_dormant(
            "generic", "generic/module", install_id, instance, adopted
        )
        identity, _ = await core.lifecycle.start_candidate("generic/module", run_id)
        core.lifecycle.publish_committed_intent(
            "generic/module", run_id, identity, True, core.registry.snapshot().revision
        )
        self.addAsyncCleanup(core.lifecycle.stop, "generic/module")
        toolset = self.manager.get_full_tool_set()
        tool = toolset.get_tool("generic_query")
        self.assertEqual(tool.parameters["required"], ["query"])
        for query in ("44091", "no item evidence"):
            self.assertEqual(
                await self.call(
                    "generic_query", {"query": query}, self.wrapper("hello")
                ),
                {"answer": query},
            )
        self.assertEqual(len(seen), 2)
        self.assertEqual(self.transport.requests, [])
        self.host.preferences.permissions = {"_default": {"generic_query": "admin"}}
        self.assertIn(
            "Permission denied",
            await self.call("generic_query", {"query": "other"}, self.wrapper("hello")),
        )
        self.assertEqual(len(seen), 2)
        self.assertEqual(
            await self.call(
                "generic_query", {"query": "other"}, self.wrapper("hello", role="admin")
            ),
            {"answer": "other"},
        )
        await core.lifecycle.stop("generic/module")
        with self.assertRaises(PermissionError):
            await self.call(
                "generic_query",
                {"query": "other"},
                self.wrapper("hello", role="admin"),
                toolset=toolset,
            )

    async def test_fixed_host_query_prices_cache_and_explicit_command_provider_zero(
        self,
    ):
        await self.start()
        self.assertEqual(len(self.manager.func_list), 2)
        raw = self.manager.func_list[0]
        self.assertIsNone(raw.handler)
        self.assertNotIn("command", raw.parameters["properties"])
        self.assertNotIn("input", raw.parameters["properties"])
        first = await self.call(
            parameters={
                "query": "44091",
                "server": "90001",
                "quality": "hq",
                "intent": "listings",
            }
        )
        second = await self.call(
            parameters={
                "query": "44091",
                "server": "90001",
                "quality": "hq",
                "intent": "listings",
            }
        )
        self.assertEqual(first["status"], "success")
        market = first["market"]
        self.assertEqual(market["currency"], "Gil")
        self.assertEqual(market["price_unit"], "per_item")
        self.assertEqual(len(market["listings"]), 5)
        self.assertTrue(market["truncated"])
        self.assertEqual(market["listings"][0]["price_per_unit"], 100)
        self.assertEqual(market["listings"][0]["world_name"], "SyntheticChina")
        self.assertEqual(market["listings"][0]["source"]["provider"], "Universalis")
        self.assertFalse(market["listings"][0]["source"]["cached"])
        self.assertTrue(second["market"]["listings"][0]["source"]["cached"])
        self.assertFalse(market["coverage"][0]["cached"])
        self.assertTrue(second["market"]["coverage"][0]["cached"])
        self.assertEqual(len(self.transport.requests), 3)
        self.assertFalse(market["limitations"]["complete_world_coverage"])
        self.assertFalse(market["limitations"]["realtime_availability"])
        self.assertEqual(
            market["limitations"]["listings_scope"], "bounded_returned_sample"
        )
        self.assertNotIn("retainer", json.dumps(first))
        self.assertNotIn("actor", json.dumps(first))
        # Execute the unchanged production command handler body, with its Host
        # should_call_llm guard, then the test dispatcher attempts provider fallback.
        tree = ast.parse((fixture.ROOT / "main.py").read_text(encoding="utf-8"))
        owner = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "YomihimeGameLink"
        )
        method = next(
            node for node in owner.body if getattr(node, "name", None) == "game_link"
        )
        method.decorator_list = []
        namespace = {"AstrMessageEvent": self.host.Event}
        exec(
            compile(
                ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                "production_command_handler",
                "exec",
            ),
            namespace,
        )
        wrapper = self.wrapper("/ygl ff14 status")
        event = wrapper.context.event
        event.call_llm = False
        event._has_send_oper, event.is_at_or_wake_command = False, True
        event._extras = {"activated_handlers": ["production game_link"]}

        async def star_process(event):
            async for value in namespace["game_link"](
                SimpleNamespace(_runtime=self.runtime), event
            ):
                yield value

        async def agent_process(event):
            await self.provider.chat(wrapper, self.manager.get_full_tool_set())
            yield None

        stage = self.host.ProcessStage()
        stage.ctx = SimpleNamespace(
            astrbot_config={"provider_settings": {"enable": True}}
        )
        stage.star_request_sub_stage = SimpleNamespace(process=star_process)
        stage.agent_sub_stage = SimpleNamespace(process=agent_process)
        async for _ in stage.process(event):
            pass
        self.assertEqual(self.provider.trace, [])
        self.assertEqual(len(self.context.calls), 1)

    async def test_requested_chinese_name_and_omitted_scope_use_core_cn(self):
        await self.start()
        from .test_market_integration import aggregated

        async def one_item(request):
            if request.source_id == "xivapi_items":
                return {"results": [{"row_id": 44091, "fields": {"Name": "犎牛牛排"}}]}
            if "/aggregated/" in request.path:
                return aggregated("China")
            return None

        self.transport.callback = one_item
        tools = self.manager.get_full_tool_set()
        for text in ("帮我看看犎牛牛排国服哪里最便宜", "帮我看看犎牛牛排哪里最便宜"):
            facts, reply = await self.provider.chat(self.wrapper(text), tools)
            self.assertEqual(facts["status"], "success")
            self.assertEqual(facts["market"]["query"], "犎牛牛排")
            self.assertEqual(facts["market"]["intent"], "min")
            self.assertEqual(facts["market"]["scope"]["kind"], "region")
            self.assertEqual(facts["market"]["scope"]["regions"], ["China"])
            self.assertIn("仅已返回范围", reply)
        self.assertEqual(self.provider.trace[0]["arguments"]["region"], "cn")
        self.assertNotIn("region", self.provider.trace[1]["arguments"])

    async def test_controlled_model_chat_asks_and_continues_explicit_user_selection(
        self,
    ):
        await self.start()
        pending, question = await self.provider.chat(
            self.wrapper("Synthetic什么价"), self.manager.get_full_tool_set()
        )
        self.assertEqual(pending["status"], "needs_selection")
        self.assertEqual(pending["user_confirmation"], pending["name_confirmation"])
        self.assertIn("<ID>", pending["id_confirmation"])
        self.assertIn("唯一完整名称", question)
        selection = pending["selection"]
        args = {key: selection[key] for key in ("batch_id", "generation")}
        args["item_id"] = 44092
        for text in ("Synthetic什么价", "选择物品 44091", "选择物品 440920"):
            with self.subTest(text=text):
                denied = await self.call("ff14_market_select", args, self.wrapper(text))
                self.assertEqual(denied["status"], "error")
        for text in (
            "Synthetic 44092个多少钱",
            "服务器44092的Synthetic价格",
            "物品ID 440920",
        ):
            with self.subTest(text=text):
                denied = await self.call(
                    parameters={"query": "44092"}, wrapper=self.wrapper(text)
                )
                self.assertEqual(denied["status"], "error")
                self.assertEqual(denied["error"]["code"], "unsupported")
        continued, reply = await self.provider.chat(
            self.wrapper("选择物品 44092"), self.manager.get_full_tool_set()
        )
        self.assertEqual(continued["market"]["item_id"], 44092)
        self.assertEqual(continued["market"]["item_name"], "Synthetic Item 44092")
        self.assertEqual(continued["market"]["original_query"], "Synthetic")
        self.assertEqual(continued["market"]["quality"], "hq")
        self.assertEqual(continued["market"]["intent"], "min")
        self.assertEqual(continued["market"]["scope"], pending["market"]["scope"])
        self.assertEqual(
            continued["market"]["module_revision"], pending["market"]["module_revision"]
        )
        replay = await self.call(
            "ff14_market_select", args, self.wrapper("选择物品 44092")
        )
        self.assertEqual(replay["status"], "error")
        self.assertEqual(
            [row["tool"] for row in self.provider.trace],
            ["ff14_market_query", "ff14_market_select"],
        )
        self.assertIn("仅已返回范围", reply)
        self.assertIn("不保证实时可买", reply)

    async def test_overview_selection_keeps_original_context_and_versioned_quotes(self):
        await self.start()
        pending = await self.call(
            parameters={
                "query": "Synthetic",
                "region": "cn",
                "quality": "all",
                "intent": "overview",
            },
            wrapper=self.wrapper("Synthetic国服行情"),
        )
        selection = pending["selection"]
        continued = await self.call(
            "ff14_market_select",
            {
                "batch_id": selection["batch_id"],
                "generation": selection["generation"],
                "item_id": 44091,
            },
            self.wrapper("Synthetic Item 44091那个"),
        )
        self.assertEqual(continued["status"], "success")
        market = continued["market"]
        self.assertEqual(market["intent"], "overview")
        self.assertEqual(market["quality"], "all")
        for key in ("scope", "module_revision", "core_revision"):
            self.assertEqual(market[key], pending["market"][key])
        self.assertEqual(market["fact_projection_version"], 3)
        self.assertEqual(market["minimums"][0]["world_name"], "SyntheticChina")
        self.assertEqual(
            market["coverage"][0]["quotes"][0]["minimum_ref"], "market.minimums[0]"
        )
        self.assertIn("recent_purchase", market["coverage"][0]["quotes"][0])

    async def test_parameters_identity_origin_and_host_permission_guard(self):
        await self.start()
        for field in (
            "actor_id",
            "session",
            "role",
            "epoch",
            "event",
            "user_confirmed",
            "confirmation",
            "user_text",
            "command",
            "input",
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                await self.call(parameters={"query": "44091", field: "forged"})
        forged = self.wrapper(context=self.host.Context())
        with self.assertRaises(PermissionError):
            await self.call(wrapper=forged)
        ingress = self.runtime._make_ingress(fixture._Event())
        self.assertFalse(
            self.runtime._validate_ingress(InvocationOrigin.LLM_TOOL, ingress)
        )
        self.host.preferences.permissions = {"_default": {"ff14_market_query": "admin"}}
        denied = await self.call()
        self.assertIn("Permission denied", denied)
        self.assertEqual(self.transport.requests, [])
        allowed = await self.call(wrapper=self.wrapper(role="admin"))
        self.assertEqual(allowed["status"], "success")

    async def test_numeric_id_requires_label_or_documented_whole_message(self):
        await self.start()
        for text in (
            "这个物品44091个多少钱",
            "item44091 units price",
            "物品44091多少钱",
            "服务器44091多少钱",
            "物品 ID 440910多少钱",
            "item ID 440910 price",
            "Synthetic数量44091",
            "someitem ID 44091 price",
        ):
            before = len(self.transport.requests)
            with self.subTest(text=text):
                denied = await self.call(wrapper=self.wrapper(text))
                self.assertEqual(denied["status"], "error")
                self.assertEqual(denied["error"]["code"], "unsupported")
            self.assertEqual(len(self.transport.requests), before)
        for text in (
            "物品ID 44091多少钱",
            "item ID: 44091 price",
            "44091",
            "查价 44091多少钱",
            "查询44091什么价",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    (await self.call(wrapper=self.wrapper(text)))["status"], "success"
                )

    async def test_name_evidence_rejects_guesses_preserves_selection_and_new_query(
        self,
    ):
        await self.start()
        user = self.wrapper("Synthetic什么价")
        parameters = {
            "query": "Synthetic",
            "server": "90001",
            "quality": "hq",
            "intent": "min",
        }
        first = await self.call(parameters=parameters, wrapper=user)
        self.assertEqual(first["status"], "needs_selection")
        retry = await self.call(parameters=parameters, wrapper=user)
        self.assertEqual(retry["status"], "needs_selection")
        args = {key: retry["selection"][key] for key in ("batch_id", "generation")}
        args["item_id"] = 44092
        for wrapper in (user, self.wrapper("刚才那个"), self.wrapper("随便选一个")):
            before = len(self.transport.requests)
            denied = await self.call(
                parameters={**parameters, "query": "Synthetic Item 44092"},
                wrapper=wrapper,
            )
            self.assertEqual(denied["status"], "error")
            self.assertEqual(denied["error"]["code"], "unsupported")
            self.assertEqual(len(self.transport.requests), before)

        from .test_market_integration import aggregated

        async def exact_name(request):
            if request.source_id == "xivapi_items":
                return {
                    "results": [
                        {"row_id": 44092, "fields": {"Name": "Synthetic Item 44092"}}
                    ]
                }
            if "/aggregated/" in request.path:
                payload = aggregated("China", level="world")
                payload["results"][0]["itemId"] = 44092
                return payload
            return None

        self.transport.callback = exact_name
        self.assertEqual(
            (await self.call("ff14_market_select", args, self.wrapper("刚才那个")))[
                "status"
            ],
            "error",
        )
        selected = await self.call(
            "ff14_market_select", args, self.wrapper("选择物品 44092")
        )
        self.assertEqual(selected["status"], "success")
        self.assertEqual(selected["market"]["item_id"], 44092)
        for field in ("scope", "quality", "intent", "module_revision", "core_revision"):
            self.assertEqual(selected["market"][field], retry["market"][field])
        self.assertEqual(selected["market"]["original_query"], "Synthetic")
        replay = await self.call(
            "ff14_market_select", args, self.wrapper("选择物品 44092")
        )
        self.assertEqual(replay["status"], "error")

        # A candidate name must use select even when explicitly present. A
        # genuinely unrelated new item still invalidates the previous batch.
        self.transport.callback = None
        pending = await self.call(
            parameters=parameters, wrapper=self.wrapper("Synthetic什么价")
        )
        stale = {key: pending["selection"][key] for key in ("batch_id", "generation")}
        stale["item_id"] = 44092
        self.transport.callback = exact_name
        explicit = await self.call(
            parameters={**parameters, "query": "Synthetic Item 44092"},
            wrapper=self.wrapper("查询 SYNTHETIC   ITEM 44092 价格"),
        )
        self.assertEqual(explicit["status"], "error")
        explicit = await self.call(
            parameters={**parameters, "query": "Unrelated Item"},
            wrapper=self.wrapper("查询 UNRELATED   ITEM 价格"),
        )
        self.assertEqual(explicit["status"], "success")
        self.assertEqual(explicit["market"]["item_id"], 44092)
        invalidated = await self.call(
            "ff14_market_select", stale, self.wrapper("选择物品 44092")
        )
        self.assertEqual(invalidated["status"], "error")

    async def test_natural_name_chat_selection_preserves_context_prices_and_cache(self):
        from .test_market_integration import aggregated

        await self.start()

        async def prices(request):
            if request.source_id == "xivapi_items":
                return {
                    "results": [
                        {"row_id": 44091, "fields": {"Name": "犎牛牛排"}},
                        {"row_id": 44092, "fields": {"Name": "另一种牛排"}},
                    ]
                }
            if "/aggregated/" in request.path:
                return aggregated("China", level="region")
            return None

        self.transport.callback = prices
        original = self.wrapper("牛排国服哪里最便宜？")
        pending, question = await self.provider.chat(
            original, self.manager.get_full_tool_set()
        )
        self.assertEqual(pending["status"], "needs_selection")
        self.assertIn("犎牛牛排", question)
        self.assertIn("唯一完整名称", question)
        self.assertNotIn("/ygl", question)
        args = {key: pending["selection"][key] for key in ("batch_id", "generation")}
        args["item_id"] = 44091
        before = len(self.transport.requests)
        self.assertEqual(
            (await self.call("ff14_market_select", args, original))["status"], "error"
        )
        for text in (
            "犎牛牛排？",
            "不是犎牛牛排",
            "不要犎牛牛排",
            "犎牛牛排或另一种牛排",
            "牛排那个。",
            "第二个",
            "犎牛牛排2个",
            "刚才那个",
        ):
            with self.subTest(text=text):
                denied = await self.call("ff14_market_select", args, self.wrapper(text))
                self.assertEqual(denied["status"], "error")
        for text, query in (
            ("犎牛牛排那个。", "犎牛牛排"),
            ("犎牛牛排那个。", "牛排"),
            ("犎牛牛排那个。", "犎"),
            ("犎牛牛排？", "犎牛牛排"),
            ("不是犎牛牛排", "犎牛牛排"),
            ("不是犎牛", "犎牛"),
            ("就犎牛", "犎牛"),
            ("犎牛？", "犎牛"),
            ("犎牛牛排或另一种牛排", "牛排"),
            ("物品ID 44091", "44091"),
            ("第二个", "第二个"),
        ):
            with self.subTest(text=text, query=query):
                denied = await self.call(
                    parameters={"query": query, "region": "global", "quality": "nq"},
                    wrapper=self.wrapper(text),
                )
                self.assertEqual(denied["status"], "error")
        self.assertEqual(len(self.transport.requests), before)
        wrong_id = await self.call(
            "ff14_market_select",
            {**args, "item_id": 44092},
            self.wrapper("犎牛牛排那个。"),
        )
        self.assertEqual(wrong_id["status"], "error")
        for identity in (
            {"sender": "other"},
            {"session": "other"},
            {"adapter": "other"},
        ):
            denied = await self.call(
                "ff14_market_select", args, self.wrapper("犎牛牛排那个。", **identity)
            )
            self.assertEqual(denied["status"], "error")
        continued, reply = await self.provider.chat(
            self.wrapper("犎牛牛排那个。"), self.manager.get_full_tool_set()
        )
        self.assertEqual(continued["status"], "success")
        self.assertEqual(continued["market"]["item_id"], 44091)
        self.assertEqual(continued["market"]["item_name"], "犎牛牛排")
        self.assertEqual(continued["market"]["original_query"], "牛排")
        for field in ("scope", "quality", "intent", "core_revision", "module_revision"):
            self.assertEqual(continued["market"][field], pending["market"][field])
        self.assertIn("100", reply)
        self.assertIn("仅已返回范围", reply)
        self.assertIn("不保证实时可买", reply)
        self.assertEqual(
            [row["tool"] for row in self.provider.trace],
            ["ff14_market_query", "ff14_market_select"],
        )
        self.assertEqual(
            (
                await self.call(
                    "ff14_market_select", args, self.wrapper("犎牛牛排那个。")
                )
            )["status"],
            "error",
        )
        cached = await self.call(
            parameters={"query": "44091", "region": "cn", "intent": "min"}
        )
        self.assertTrue(cached["market"]["coverage"][0]["cached"])
        self.assertEqual(len(self.transport.requests), before + 1)
        handler = (
            self.runtime.core_runtime.registry.snapshot()
            .module("ff14/ff14")
            .handlers.capabilities["ff14.market.query"]
        )
        self.assertIsNone(handler._tool_event.get())

    async def test_bound_event_reset_after_exception_and_concurrent_owners(self):
        await self.start()
        handler = (
            self.runtime.core_runtime.registry.snapshot()
            .module("ff14/ff14")
            .handlers.capabilities["ff14.market.query"]
        )
        invoke = handler.invoke

        async def fail(*args):
            self.assertIsNotNone(handler._tool_event.get())
            raise RuntimeError("synthetic handler failure")

        handler.invoke = fail
        try:
            with self.assertRaisesRegex(
                PermissionError, "Core did not authorize public ToolOutput"
            ):
                await self.call()
        finally:
            handler.invoke = invoke
        self.assertIsNone(handler._tool_event.get())

        entered = asyncio.Event()
        release = asyncio.Event()
        seen = []

        async def observe(context, parameters):
            proof = handler._tool_event.get()
            seen.append((context.actor_id, proof[0].user, proof[1], proof[2]))
            if context.actor_id.endswith("user-17"):
                entered.set()
                await release.wait()
                self.assertIs(handler._tool_event.get(), proof)
            return await invoke(context, parameters)

        handler.invoke = observe
        try:
            first = asyncio.create_task(self.call())
            await asyncio.wait_for(entered.wait(), 3)
            second = await self.call(
                wrapper=self.wrapper(sender="other", session="other")
            )
            release.set()
            self.assertEqual((await first)["status"], "success")
            self.assertEqual(second["status"], "success")
        finally:
            release.set()
            handler.invoke = invoke
        self.assertEqual(
            {row[0] for row in seen},
            {"test-platform-1:user-17", "test-platform-1:other"},
        )
        self.assertTrue(all(actor == owner for actor, owner, _, _ in seen))
        self.assertIsNot(seen[0][2], seen[1][2])
        self.assertIsNone(handler._tool_event.get())

    async def test_consumed_selection_flight_rejects_query_bypass_and_fences_new_item(
        self,
    ):
        from .test_market_integration import aggregated

        await self.start()
        for item_id, replace_query in ((44091, False), (44092, True)):
            with self.subTest(item_id=item_id):
                self.transport.callback = None
                pending = await self.call(
                    parameters={
                        "query": "Synthetic",
                        "server": "90001",
                        "quality": "hq",
                        "intent": "min",
                    },
                    wrapper=self.wrapper("Synthetic什么价"),
                )
                args = {
                    key: pending["selection"][key] for key in ("batch_id", "generation")
                }
                args["item_id"] = item_id
                entered, release = asyncio.Event(), asyncio.Event()

                async def blocked(request):
                    if request.source_id == "xivapi_items":
                        return {
                            "results": [
                                {"row_id": 44093, "fields": {"Name": "Unrelated Item"}}
                            ]
                        }
                    if "/aggregated/" in request.path:
                        current_id = int(request.path.rsplit("/", 1)[-1])
                        if current_id == item_id:
                            entered.set()
                            await release.wait()
                        payload = aggregated(
                            "China",
                            level="world" if current_id == item_id else "region",
                        )
                        payload["results"][0]["itemId"] = current_id
                        return payload
                    return None

                self.transport.callback = blocked
                confirmation = self.wrapper(f"Synthetic Item {item_id}")
                selected = asyncio.create_task(
                    self.call("ff14_market_select", args, confirmation)
                )
                try:
                    await asyncio.wait_for(entered.wait(), 3)
                    before = len(self.transport.requests)
                    for query in (f"Synthetic Item {item_id}", "Synthetic"):
                        denied = await self.call(
                            parameters={
                                "query": query,
                                "region": "global",
                                "quality": "nq",
                            },
                            wrapper=confirmation,
                        )
                        self.assertEqual(denied["status"], "error")
                    for text in (
                        "第7个",
                        "第七个",
                        "第十个",
                        "最后一个",
                        "不是",
                        "不要",
                        "不选",
                        "后者",
                    ):
                        denied = await self.call(
                            parameters={
                                "query": text,
                                "region": "global",
                                "quality": "nq",
                            },
                            wrapper=self.wrapper(text),
                        )
                        self.assertEqual(denied["status"], "error")
                    replay = await self.call("ff14_market_select", args, confirmation)
                    self.assertEqual(replay["status"], "error")
                    self.assertEqual(len(self.transport.requests), before)
                    if replace_query:
                        fresh = await self.call(
                            parameters={
                                "query": "Unrelated Item",
                                "region": "cn",
                                "intent": "min",
                            },
                            wrapper=self.wrapper("Unrelated Item多少钱"),
                        )
                        self.assertEqual(fresh["status"], "success")
                        self.assertEqual(fresh["market"]["item_id"], 44093)
                finally:
                    release.set()
                result = await selected
                if replace_query:
                    self.assertEqual(result["status"], "error")
                    self.assertNotIn("market", result)
                else:
                    self.assertEqual(result["status"], "success")
                    for field in (
                        "scope",
                        "quality",
                        "intent",
                        "core_revision",
                        "module_revision",
                    ):
                        self.assertEqual(
                            result["market"][field], pending["market"][field]
                        )

    async def test_ordinal_denial_query_guard_keeps_batch_and_rejects_absent_expired(
        self,
    ):
        await self.start()
        continuations = (
            "第7个",
            "第七个",
            "第十个",
            "最后一个",
            "不是",
            "不要",
            "不选",
            "后者",
        )
        for index, text in enumerate(continuations):
            sender = f"ordinal-user-{index}"
            original = self.wrapper("Synthetic什么价", sender=sender)
            parameters = {
                "query": "Synthetic",
                "server": "90001",
                "quality": "hq",
                "intent": "min",
            }
            pending = await self.call(parameters=parameters, wrapper=original)
            before = len(self.transport.requests)
            rejected = await self.call(
                parameters={"query": text, "region": "global", "quality": "nq"},
                wrapper=self.wrapper(text, sender=sender),
            )
            self.assertEqual(rejected["status"], "error")
            for message, query in (
                ("第7个吗？", "第7个"),
                ("帮我看后者", "后者"),
                ("不是吧", "不是"),
                ("我说不要", "不要"),
                ("我说不选", "不选"),
                ("我说不要Synthetic", "不要Synthetic"),
            ):
                rejected = await self.call(
                    parameters={"query": query, "region": "global", "quality": "nq"},
                    wrapper=self.wrapper(message, sender=sender),
                )
                self.assertEqual(rejected["status"], "error")
            retry = await self.call(parameters=parameters, wrapper=original)
            self.assertEqual(retry["selection"], pending["selection"])
            self.assertEqual(retry["market"], pending["market"])
            self.assertEqual(len(self.transport.requests), before)
            args = {
                key: pending["selection"][key] for key in ("batch_id", "generation")
            }
            args["item_id"] = 44091
            selected = await self.call(
                "ff14_market_select",
                args,
                self.wrapper("Synthetic Item 44091", sender=sender),
            )
            self.assertEqual(selected["status"], "success")
            for field in (
                "scope",
                "quality",
                "intent",
                "core_revision",
                "module_revision",
            ):
                self.assertEqual(selected["market"][field], pending["market"][field])

        handler = (
            self.runtime.core_runtime.registry.snapshot()
            .module("ff14/ff14")
            .handlers.capabilities["ff14.market.query"]
        )
        for state in ("absent", "expired"):
            sender = f"guard-{state}"
            if state == "expired":
                old = await self.call(
                    parameters={"query": "Synthetic"},
                    wrapper=self.wrapper("Synthetic什么价", sender=sender),
                )
                handler.registry._clock = lambda: 10**20
            before = len(self.transport.requests)
            for text in continuations:
                denied = await self.call(
                    parameters={"query": text, "region": "global", "quality": "nq"},
                    wrapper=self.wrapper(text, sender=sender),
                )
                self.assertEqual(denied["status"], "error")
            self.assertEqual(len(self.transport.requests), before)
            if state == "expired":
                args = {
                    key: old["selection"][key] for key in ("batch_id", "generation")
                }
                args["item_id"] = 44091
                self.assertEqual(
                    (
                        await self.call(
                            "ff14_market_select",
                            args,
                            self.wrapper("Synthetic Item 44091", sender=sender),
                        )
                    )["status"],
                    "error",
                )
                handler.registry._clock = handler.clock

        # Ordinal words inside a genuine full item name are not positional replies.
        for index, name in enumerate(
            ("第七天堂牛排", "第七个勇士徽章", "最后一个传说牛排", "前者的矿石")
        ):
            sender = f"fresh-name-{index}"
            old = await self.call(
                parameters={"query": "Synthetic"},
                wrapper=self.wrapper("Synthetic什么价", sender=sender),
            )
            fresh = await self.call(
                parameters={"query": name},
                wrapper=self.wrapper(f"查询{name}价格", sender=sender),
            )
            self.assertEqual(fresh["status"], "needs_selection")
            self.assertEqual(fresh["market"]["query"], name)
            self.assertNotEqual(
                fresh["selection"]["generation"], old["selection"]["generation"]
            )

    async def test_cancelled_consumed_flight_clears_context_and_recovers_after_guard(
        self,
    ):
        await self.start()
        pending = await self.call(
            parameters={"query": "Synthetic"}, wrapper=self.wrapper("Synthetic什么价")
        )
        handler = (
            self.runtime.core_runtime.registry.snapshot()
            .module("ff14/ff14")
            .handlers.capabilities["ff14.market.query"]
        )
        args = {key: pending["selection"][key] for key in ("batch_id", "generation")}
        args["item_id"] = 44091
        entered, release = asyncio.Event(), asyncio.Event()
        reset = []

        async def block(request):
            if "/aggregated/" in request.path:
                entered.set()
                await release.wait()
            return None

        async def call_selection():
            try:
                return await self.call(
                    "ff14_market_select", args, self.wrapper("Synthetic Item 44091")
                )
            finally:
                reset.append(handler._tool_event.get())

        self.transport.callback = block
        flight = asyncio.create_task(call_selection())
        try:
            await asyncio.wait_for(entered.wait(), 3)
            before = len(self.transport.requests)
            denied = await self.call(
                parameters={"query": "第十个", "region": "global"},
                wrapper=self.wrapper("第十个"),
            )
            self.assertEqual(denied["status"], "error")
            self.assertEqual(len(self.transport.requests), before)
            flight.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await flight
        finally:
            release.set()
        self.assertEqual(reset, [None])
        self.assertEqual(handler.registry.size, 0)
        self.transport.callback = None
        fresh = await self.call(
            parameters={"query": "Synthetic"}, wrapper=self.wrapper("Synthetic什么价")
        )
        self.assertEqual(fresh["status"], "needs_selection")
        self.assertNotEqual(
            fresh["selection"]["generation"], pending["selection"]["generation"]
        )

    async def test_consumed_confirmation_proof_clears_on_exception_and_query_recovers(
        self,
    ):
        await self.start()
        pending = await self.call(
            parameters={"query": "Synthetic"}, wrapper=self.wrapper("Synthetic什么价")
        )
        args = {key: pending["selection"][key] for key in ("batch_id", "generation")}
        args["item_id"] = 44091
        handler = (
            self.runtime.core_runtime.registry.snapshot()
            .module("ff14/ff14")
            .handlers.capabilities["ff14.market.query"]
        )
        start_bound = handler.source.start_bound

        async def failing_catalog():
            self.assertEqual(handler.registry.size, 1)
            self.assertIsNotNone(handler._tool_event.get())
            raise RuntimeError("synthetic internal catalog failure")

        handler.source.start_bound = lambda *args, **kwargs: SimpleNamespace(
            catalog=failing_catalog
        )
        try:
            with self.assertRaisesRegex(
                PermissionError, "Core did not authorize public ToolOutput"
            ):
                await self.call(
                    "ff14_market_select", args, self.wrapper("Synthetic Item 44091")
                )
        finally:
            handler.source.start_bound = start_bound
        self.assertIsNone(handler._tool_event.get())
        self.assertEqual(handler.registry.size, 0)
        retry = await self.call(
            parameters={"query": "Synthetic"}, wrapper=self.wrapper("Synthetic什么价")
        )
        self.assertEqual(retry["status"], "needs_selection")
        self.assertNotEqual(
            retry["selection"]["generation"], pending["selection"]["generation"]
        )
        self.assertEqual(
            (
                await self.call(
                    "ff14_market_select", args, self.wrapper("Synthetic Item 44091")
                )
            )["status"],
            "error",
        )
        args.update(
            {key: retry["selection"][key] for key in ("batch_id", "generation")}
        )
        self.assertEqual(
            (
                await self.call(
                    "ff14_market_select", args, self.wrapper("Synthetic Item 44091")
                )
            )["status"],
            "success",
        )

    async def test_unbound_wrong_owner_and_expired_confirmation_cannot_query(self):
        await self.start()
        core = self.runtime.core_runtime
        handler = (
            core.registry.snapshot()
            .module("ff14/ff14")
            .handlers.capabilities["ff14.market.query"]
        )
        event = self.wrapper("物品ID 44091").context.event
        ingress = self.runtime._make_ingress(event, origin=InvocationOrigin.LLM_TOOL)
        unbound = await core.invoke_tool(
            "ff14/ff14", "ff14_market_query", {"query": "44091"}, ingress=ingress
        )
        self.assertEqual(unbound.output.result.facts.facts["status"], "error")
        with handler._bind_tool_event(
            event,
            "物品ID 44091",
            "test-platform-1:other",
            "group-42",
            "test-platform-1",
        ):
            wrong = await core.invoke_tool(
                "ff14/ff14", "ff14_market_query", {"query": "44091"}, ingress=ingress
            )
        self.assertEqual(wrong.output.result.facts.facts["status"], "error")
        self.assertIsNone(handler._tool_event.get())
        self.assertEqual(self.transport.requests, [])
        for text in (
            "Synthetic Item 44091那个。",
            "就Synthetic Item 44091",
            "选择Synthetic Item 44091",
            "选Synthetic Item 44091",
        ):
            with self.subTest(text=text):
                denied = await self.call(
                    parameters={"query": "Synthetic Item 44091"},
                    wrapper=self.wrapper(text),
                )
                self.assertEqual(denied["status"], "error")
                self.assertEqual(self.transport.requests, [])
        pending = await self.call(
            parameters={"query": "Synthetic"}, wrapper=self.wrapper("Synthetic什么价")
        )
        args = {key: pending["selection"][key] for key in ("batch_id", "generation")}
        args["item_id"] = 44091
        handler.registry._clock = lambda: 10**20
        before = len(self.transport.requests)
        for text in ("Synthetic Item 44091那个。", "就Synthetic Item 44091"):
            denied = await self.call(
                parameters={"query": "Synthetic Item 44091"}, wrapper=self.wrapper(text)
            )
            self.assertEqual(denied["status"], "error")
        denied = await self.call(
            "ff14_market_select", args, self.wrapper("Synthetic Item 44091那个。")
        )
        self.assertEqual(denied["status"], "error")
        self.assertEqual(len(self.transport.requests), before)
        self.assertEqual(handler.registry.size, 0)

    async def test_candidate_namespace_isolation_expiry_and_new_query_invalidation(
        self,
    ):
        await self.start()
        pending = await self.call(
            parameters={"query": "Synthetic"}, wrapper=self.wrapper("Synthetic什么价")
        )
        args = {key: pending["selection"][key] for key in ("batch_id", "generation")}
        args["item_id"] = 44091
        for kwargs in ({"sender": "other"}, {"session": "other"}, {"adapter": "other"}):
            denied = await self.call(
                "ff14_market_select", args, self.wrapper("选择物品 44091", **kwargs)
            )
            self.assertEqual(denied["status"], "error")
        module = self.runtime.core_runtime.registry.snapshot().module("ff14/ff14")
        handler = module.handlers.capabilities["ff14.market.query"]
        owner = TrustedOwner("test-platform-1:user-17", "group-42", "test-platform-1")
        with self.assertRaises(ValueError):
            handler.registry.choose(
                owner, args["batch_id"], args["generation"], args["item_id"]
            )
        handler.registry._clock = lambda: 10**20
        expired = await self.call(
            "ff14_market_select", args, self.wrapper("选择物品 44091")
        )
        self.assertEqual(expired["status"], "error")
        handler.registry._clock = handler.clock
        pending = await self.call(
            parameters={"query": "Synthetic"}, wrapper=self.wrapper("Synthetic什么价")
        )
        args.update(
            {key: pending["selection"][key] for key in ("batch_id", "generation")}
        )
        await self.call(
            parameters={"query": "44093"}, wrapper=self.wrapper("物品ID 44093")
        )
        invalid = await self.call(
            "ff14_market_select", args, self.wrapper("选择物品 44091")
        )
        self.assertEqual(invalid["status"], "error")

    async def test_terminate_old_toolset_replacement_and_retry_cleanup(self):
        await self.start()
        core = self.runtime.core_runtime
        module = core.registry.snapshot().module("ff14/ff14")
        old = self.manager.get_full_tool_set()
        replacement = self.host.FunctionTool(
            name="ff14_market_query",
            description="other owner",
            parameters={"type": "object"},
        )
        self.manager.func_list[0] = replacement
        with self.assertRaises(PermissionError):
            await self.call(toolset=old)
        self.assertEqual(self.transport.requests, [])
        publisher = self.runtime._tool_publisher
        real_cleanup = publisher.cleanup
        publisher.cleanup = lambda: (_ for _ in ()).throw(
            ToolPublicationError("synthetic pending")
        )
        with self.assertRaises(CoreRuntimeCleanupPending) as pending:
            await self.runtime.terminate()
        self.assertEqual(pending.exception.component, "tool_publication")
        with self.assertRaises(PermissionError):
            await self.call(toolset=old)
        self.assertIs(self.runtime._tool_publisher, publisher)
        self.assertFalse(core._accepting)
        self.assertIsNone(self.runtime.core_runtime)
        await self.assert_core_closed(core, module)
        self.assertTrue(self.transport.closed)
        self.assertEqual(self.transport.close_calls, 1)
        publisher.cleanup = real_cleanup
        await self.runtime.terminate()
        self.assertEqual(self.manager.func_list, [replacement])
        self.assertIsNone(self.runtime.core_runtime)
        self.assertEqual(self.transport.close_calls, 1)

    async def test_tool_errors_and_private_result_cannot_bypass_output(self):
        await self.start()
        invalid = await self.call(parameters={"query": "44091", "server": "missing"})
        self.assertEqual(invalid["status"], "error")
        self.assertEqual(invalid["error"]["code"], "parameter_error")
        module = self.runtime.core_runtime.registry.snapshot().module("ff14/ff14")
        handler = module.handlers.capabilities["ff14.market.query"]
        original = handler.invoke

        async def private(*_):
            return CapabilityResult(
                "private",
                ResultStatus.ERROR,
                privacy=Privacy.PRIVATE,
                error=ErrorDetail(ErrorCode.NOT_FOUND, "private internal"),
            )

        handler.invoke = private
        with self.assertRaises(PermissionError):
            await self.call()
        handler.invoke = original

    async def test_awaited_shutdown_suppresses_late_tool_facts(self):
        await self.start()
        started, release = asyncio.Event(), asyncio.Event()

        async def callback(request):
            if "/aggregated/" in request.path:
                started.set()
                await release.wait()
            return None

        self.transport.callback = callback
        pending = asyncio.create_task(self.call())
        await asyncio.wait_for(started.wait(), 5)
        stopping = asyncio.create_task(self.runtime.terminate())
        await asyncio.sleep(0)
        release.set()
        with self.assertRaises((PermissionError, asyncio.CancelledError)):
            await pending
        await stopping

    async def test_collision_and_partial_publication_failure_close_real_runtime(self):
        await self.start()
        await self.runtime.terminate()
        other = self.host.FunctionTool(
            name="ff14_market_query",
            description="other plugin",
            parameters={"type": "object"},
        )
        self.manager.func_list.append(other)
        with self.assertRaises(ToolPublicationError):
            await self.runtime.initialize()
        self.assertIsNone(self.runtime.core_runtime)
        self.assertEqual(self.manager.func_list, [other])
        self.manager.func_list.clear()
        original = self.context.add_llm_tools
        count = 0

        def partial(*tools):
            nonlocal count
            count += 1
            original(*tools)
            if count == 2:
                raise RuntimeError("synthetic registration failure")

        self.context.add_llm_tools = partial
        with self.assertRaises(RuntimeError):
            await self.runtime.initialize()
        self.assertEqual(count, 2)
        self.assertEqual(self.manager.func_list, [])
        self.assertIsNone(self.runtime.core_runtime)
        self.context.add_llm_tools = original
        await self.runtime.initialize()
        self.assertEqual(len(self.manager.func_list), 2)

    async def test_module_stop_revoke_and_pending_observer_cleanup(self):
        await self.start()
        old = self.manager.get_full_tool_set()
        publisher = self.runtime._tool_publisher
        original = publisher.cleanup
        publisher.cleanup = lambda: (_ for _ in ()).throw(
            ToolPublicationError("synthetic pending")
        )
        await self.runtime.core_runtime.lifecycle.stop("ff14/ff14")
        self.assertTrue(all(handle.revoked for handle in publisher.handles))
        with self.assertRaises(PermissionError):
            await self.call(toolset=old)
        self.assertEqual(publisher.failure, "ToolPublicationError")
        publisher.cleanup = original
        publisher.cleanup()
        self.assertEqual(self.manager.func_list, [])

    async def test_initialization_pending_cleanup_retains_owner_and_retries(self):
        await self.start()
        await self.runtime.terminate()
        original = self.context.add_llm_tools
        retained = {}

        def failed_registration(*tools):
            original(*tools)
            publisher = self.runtime._tool_publisher
            retained["publisher"] = publisher
            retained["cleanup"] = publisher.cleanup
            retained["core"] = self.runtime.core_runtime
            retained["module"] = (
                retained["core"].registry.snapshot().module("ff14/ff14")
            )
            publisher.cleanup = lambda: (_ for _ in ()).throw(
                ToolPublicationError("synthetic pending initialization cleanup")
            )
            raise RuntimeError("synthetic registration failure")

        self.context.add_llm_tools = failed_registration
        self.transport.closed = False
        close_calls = self.transport.close_calls
        with self.assertRaises(CoreRuntimeCleanupPending) as pending:
            await self.runtime.initialize()
        self.assertEqual(pending.exception.component, "tool_publication")
        publisher = retained["publisher"]
        self.assertIs(self.runtime._tool_publisher, publisher)
        self.assertIsNone(self.runtime.core_runtime)
        self.assertFalse(retained["core"]._accepting)
        await self.assert_core_closed(retained["core"], retained["module"])
        self.assertTrue(self.transport.closed)
        self.assertEqual(self.transport.close_calls, close_calls + 1)
        self.assertIsNone(self.runtime._http_transport)
        self.assertFalse(self.runtime._ready)
        old = self.manager.get_full_tool_set()
        with self.assertRaises(PermissionError):
            await self.call(toolset=old)
        self.assertEqual(self.transport.requests, [])
        publisher.cleanup = retained["cleanup"]
        self.context.add_llm_tools = original
        other = self.host.FunctionTool(
            name="ff14_market_query", description="other", parameters={"type": "object"}
        )
        self.manager.func_list.insert(0, other)
        await self.runtime.terminate()
        self.assertEqual(self.manager.func_list, [other])
        self.assertIsNone(self.runtime.core_runtime)
        self.assertEqual(self.transport.close_calls, close_calls + 1)

    async def test_failed_tool_cleanup_preserves_pending_core_until_retry(self):
        await self.start()
        core = self.runtime.core_runtime
        module = core.registry.snapshot().module("ff14/ff14")
        publisher = self.runtime._tool_publisher
        cleanup, close = publisher.cleanup, core.close
        publisher.cleanup = lambda: (_ for _ in ()).throw(
            ToolPublicationError("pending tools")
        )

        async def pending_core():
            return False

        core.close = pending_core
        with self.assertRaises(CoreRuntimeCleanupPending) as failure:
            await self.runtime.terminate()
        self.assertEqual(
            failure.exception.component, "tool_publication,core_runtime_close"
        )
        self.assertIs(self.runtime.core_runtime, core)
        self.assertIs(self.runtime._tool_publisher, publisher)
        self.assertFalse(core.closed)
        self.assertFalse(core._accepting)
        self.assertIs(self.runtime._http_transport, self.transport)
        self.assertFalse(self.transport.closed)
        self.assertEqual(self.transport.close_calls, 0)
        publisher.cleanup, core.close = cleanup, close
        await self.runtime.terminate()
        await self.assert_core_closed(core, module)
        self.assertEqual(self.manager.func_list, [])
        self.assertTrue(self.transport.closed)
        self.assertEqual(self.transport.close_calls, 1)

    async def test_tool_and_transport_failures_close_core_and_retain_exact_retry_owners(
        self,
    ):
        await self.start()
        core = self.runtime.core_runtime
        module = core.registry.snapshot().module("ff14/ff14")
        publisher = self.runtime._tool_publisher
        cleanup, close = publisher.cleanup, self.transport.close
        publisher.cleanup = lambda: (_ for _ in ()).throw(
            ToolPublicationError("pending tools")
        )

        async def pending_transport():
            raise RuntimeError("pending transport")

        self.transport.close = pending_transport
        with self.assertRaises(CoreRuntimeCleanupPending) as failure:
            await self.runtime.terminate()
        self.assertEqual(
            failure.exception.component, "tool_publication,http_transport_close"
        )
        self.assertIsNone(self.runtime.core_runtime)
        await self.assert_core_closed(core, module)
        self.assertIs(self.runtime._tool_publisher, publisher)
        self.assertIs(self.runtime._http_transport, self.transport)
        self.assertFalse(self.transport.closed)
        self.assertEqual(self.transport.close_calls, 0)
        publisher.cleanup, self.transport.close = cleanup, close
        await self.runtime.terminate()
        self.assertIsNone(self.runtime._tool_publisher)
        self.assertIsNone(self.runtime._http_transport)
        self.assertEqual(self.manager.func_list, [])
        self.assertEqual(self.transport.close_calls, 1)

    async def test_partial_no_data_and_decimal_missing_facts_remain_truthful(self):
        await self.start()
        from yomihime_sdk.api.services import HttpResponse

        from .test_market_integration import aggregated

        async def partial(request):
            if "/aggregated/" not in request.path:
                return None
            region = request.path.split("/")[-2]
            if region == "Europe":
                return HttpResponse(503, {}, b"{}")
            return HttpResponse(200, {}, json.dumps(aggregated(region)).encode())

        self.transport.callback = partial
        result = await self.call(parameters={"query": "44091", "region": "global"})
        self.assertEqual(result["status"], "partial_success")
        self.assertTrue(result["market"]["partial"])
        self.assertFalse(result["market"]["coverage_complete"])
        self.assertEqual(result["market"]["limitations"]["failed_regions"], ["Europe"])
        self.assertEqual(
            result["market"]["limitations"]["minimum_scope"],
            "available_returned_scopes",
        )
        self.assertEqual(len(result["market"]["coverage"]), 4)
        self.assertEqual(result["market"]["coverage"][1]["fetched_at"], None)
        minimum = await self.call(
            parameters={"query": "44091", "region": "global", "intent": "min"}
        )
        self.assertEqual(minimum["status"], "partial_success")
        self.assertEqual(minimum["market"]["limitations"]["failed_regions"], ["Europe"])
        self.assertEqual(len(minimum["market"]["coverage"]), 4)

        async def missing_future(request):
            if "/aggregated/" in request.path:
                payload = aggregated("China")
                for quality in ("nq", "hq"):
                    payload["results"][0][quality].pop("averageSalePrice")
                    payload["results"][0][quality]["recentPurchase"]["region"][
                        "timestamp"
                    ] = 4102444800000
                payload["results"][0].pop("worldUploadTimes")
                return payload
            return None

        self.transport.callback = missing_future
        incomplete = await self.call(parameters={"query": "44091", "region": "cn"})
        quote = incomplete["market"]["coverage"][0]["quotes"][0]
        self.assertIsNone(quote["average_sale_price"])
        self.assertIn("average_sale_price", quote["missing"])
        self.assertEqual(quote["daily_sale_velocity"], 1.25)
        self.assertIsNone(incomplete["market"]["minimums"][0]["time"]["source_time"])
        self.assertTrue(quote["recent_time"]["future_time"])
        self.assertLess(quote["recent_time"]["age_seconds"], 0)
        self.assertTrue(incomplete["market"]["warnings"])

        # Query a new noncached item and return the official explicit empty marker.
        async def empty(request):
            if "/aggregated/" in request.path:
                return HttpResponse(200, {}, b'{"results":[],"failedItems":[]}')
            return None

        self.transport.callback = empty
        no_data = await self.call(
            parameters={"query": "44092", "region": "cn"},
            wrapper=self.wrapper("物品ID 44092多少钱"),
        )
        self.assertEqual(no_data["status"], "error")
        self.assertEqual(no_data["error"]["code"], "no_records")
        self.assertEqual(no_data["market"]["coverage"][0]["state"], "empty")
        self.assertEqual(no_data["market"]["minimums"], [])


if __name__ == "__main__":
    unittest.main()
