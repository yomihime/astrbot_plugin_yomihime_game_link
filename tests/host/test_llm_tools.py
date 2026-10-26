"""Fixed Host tool calls, real Core and synthetic upstream/model; no live LLM."""

from __future__ import annotations

import ast
import asyncio
import json
import re
import unittest
from copy import copy
from types import SimpleNamespace

from ygl_test_subject.adapters.astrbot.tool_publisher import ToolPublicationError
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.infrastructure.sqlite.executor import SQLiteExecutorClosedError
from ygl_test_subject.modules.ff14.query_resolution import TrustedOwner
from ygl_test_subject.services.core_runtime import CoreRuntimeCleanupPending

from yomihime_game_link_sdk.contexts import InvocationOrigin
from yomihime_game_link_sdk.display import Privacy
from yomihime_game_link_sdk.results import (
    CapabilityResult,
    ErrorCode,
    ErrorDetail,
    ResultStatus,
)

from . import test_astrbot_runtime as fixture
from .test_market_integration import FixtureTransport
from .tool_contract import tool_contracts

FF14_MODEL_TOOL = "ff14"
FF14_ACTIONS = {
    "ff14_market_query": "market_query",
    "ff14_market_select": "market_select",
    "ff14_item_lookup": "item_lookup",
    "ff14_item_select": "item_select",
    "ff14_logs_character": "logs_character",
}


class ControlledProvider:
    """Deterministic model stub consuming user events and actual Host schemas."""

    def __init__(self, publisher):
        self.trace, self.pending = [], None
        self.publisher = publisher

    async def chat(self, wrapper, toolset):
        import jsonschema

        request = SimpleNamespace(func_tool=toolset, conversation=None)
        await self.publisher.scope_request(wrapper.context.event, request)
        toolset = request.func_tool
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
                args = {"query": "犎牛牛排", "intent": "min", "request_language": "zh"}
            elif text == "牛排国服哪里最便宜？":
                args = {"query": "牛排", "region": "cn", "intent": "min"}
            elif "Synthetic" not in text:
                raise AssertionError(
                    "stub has no tool-selection fixture for this message"
                )
        call = {"action": FF14_ACTIONS[name], "arguments": args}
        jsonschema.validate(call, schemas[FF14_MODEL_TOOL])
        facts = json.loads(
            await toolset.get_tool(FF14_MODEL_TOOL).call(wrapper, **call)
        )
        if facts["status"] == "needs_selection":
            self.pending = facts
            reply = "请选择候选：" + ", ".join(
                item["name"] for item in facts["selection"]["candidates"]
            )
        else:
            if facts["status"] != "success":
                raise AssertionError(
                    f"controlled provider expected market success: {facts}"
                )
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
                self.config_umos = []
                self.config = {"subagent_orchestrator": {}}
                self.global_config = {"subagent_orchestrator": {}}

            def get_config(self, *, umo=None):
                if umo is None:
                    return self.global_config
                if type(umo) is not str or not umo:
                    raise AssertionError("configuration must be event-bound")
                self.config_umos.append(umo)
                return self.config

            def get_llm_tool_manager(self):
                return self.provider_manager.llm_tools

        self.context = Context()
        self.transport = FixtureTransport()
        self.runtime = self._runtime(
            self.context, http_transport_factory=lambda: self.transport
        )
        self.addAsyncCleanup(self.runtime.terminate)
        await self.runtime.initialize()
        await fixture.AstrBotRuntimeTests._assert_initial_public_calendar_sync(
            self, self.runtime, self.transport
        )
        # Retain the exact two-region startup audit, then leave the original
        # no-request, source, cache and owner checks on the command/tool delta.
        self.initial_background_requests = tuple(self.transport.requests)
        self.transport.requests.clear()
        self.manager = self.context.provider_manager.llm_tools
        self.host.tool_exec.llm_tools = self.manager
        self.host.provider_register.llm_tools = self.manager
        self.provider = ControlledProvider(self.runtime._tool_publisher)

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
        event.platform_meta = SimpleNamespace(id=adapter, name="test-platform")
        event.message_obj = SimpleNamespace(
            sender=SimpleNamespace(user_id=sender), type=host.MessageType.GROUP_MESSAGE
        )
        event.session = SimpleNamespace(
            session_id=session, message_type=host.MessageType.GROUP_MESSAGE
        )
        event.unified_msg_origin = f"{adapter}:{session}"
        return host.ContextWrapper(
            context=host.AstrAgentContext(context=context or self.context, event=event)
        )

    async def call(
        self, name="ff14_market_query", parameters=None, wrapper=None, toolset=None
    ):
        tools = toolset or self.manager.get_full_tool_set()
        wrapper = wrapper or self.wrapper()
        parameters = parameters or {"query": "44091", "region": "cn"}
        if name in FF14_ACTIONS:
            request = SimpleNamespace(func_tool=tools, conversation=None)
            await self.runtime._tool_publisher.scope_request(
                wrapper.context.event, request
            )
            value = await request.func_tool.get_tool(FF14_MODEL_TOOL).call(
                wrapper,
                action=FF14_ACTIONS[name],
                arguments=parameters,
            )
        else:
            value = await tools.get_tool(name).call(wrapper, **parameters)
        return json.loads(value) if value.startswith("{") else value

    async def call_captured_parent(
        self,
        parent,
        wrapper,
        name="ff14_market_query",
        parameters=None,
    ):
        parameters = parameters or {"query": "44091", "region": "cn"}
        value = await parent.call(
            wrapper,
            action=FF14_ACTIONS[name],
            arguments=parameters,
        )
        return json.loads(value) if value.startswith("{") else value

    async def test_final_tool_json_keeps_raw_facts_and_does_not_apply_command_projection(
        self,
    ):
        from yomihime_game_link_sdk.display import DisplayDocument, TextBlock
        from yomihime_game_link_sdk.results import FactDocument

        await self.start()
        core = self.runtime.core_runtime

        def forbidden(*args):
            raise AssertionError("tool result must not project command headings")

        core.output._command_heading_projector = forbidden
        handler = (
            core.registry.snapshot()
            .module("ff14/ff14")
            .handlers.capabilities["ff14.market.query"]
        )
        facts = {
            "status": "success",
            "value": 1234.5,
            "unknown": None,
            "unit": "Gil",
            "coverage": {"complete": False, "region": "synthetic"},
            "source": {"url": "https://example.invalid/public-source"},
            # A legitimate business name is data, never a presentation field
            # to strip merely because it contains the same brand characters.
            "name": "如月怜的游戏连结",
        }

        async def success(*args):
            return CapabilityResult(
                "synthetic-serialization",
                ResultStatus.SUCCESS,
                document=DisplayDocument(
                    "原始业务标题", "原始业务主体", (TextBlock("正文"),)
                ),
                model_facts=FactDocument(
                    facts, sources=("sources-only-not-serialized",)
                ),
            )

        handler.invoke = success
        first = await self.call(parameters={"query": "44091", "region": "cn"})
        second = await self.call(parameters={"query": "44091", "region": "cn"})
        self.assertEqual(first, facts)
        self.assertEqual(second, facts)
        self.assertNotIn("sources-only-not-serialized", json.dumps(first))
        self.assertEqual(self.context.calls, [])

        for supplement in (
            None,
            {"source": {"url": "https://example.invalid/error-source"}, "value": None},
        ):
            expected = {
                "status": "error",
                "error": {
                    "code": "no_records",
                    "message": "No public records in this scope.",
                },
            }
            if supplement is not None:
                expected["supplement"] = supplement

            async def error(*args):
                return CapabilityResult(
                    "synthetic-serialization-error",
                    ResultStatus.ERROR,
                    error=ErrorDetail(
                        ErrorCode.NO_RECORDS, expected["error"]["message"]
                    ),
                    model_facts=FactDocument(expected),
                )

            handler.invoke = error
            returned = await self.call(parameters={"query": "44091", "region": "cn"})
            self.assertEqual(returned, expected)
            self.assertEqual(self.context.calls, [])

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
        original_invoke = handler.invoke
        measured_counts = []

        async def measured(*args):
            result = await original_invoke(*args)
            count = nodes(result.model_facts.facts) + len(result.model_facts.sources)
            measured_counts.append(count)
            print(
                "ACTUAL missing metrics module fact nodes:",
                args[1].get("intent"),
                count,
                flush=True,
            )
            self.assertLessEqual(count, 512)
            return result

        handler.invoke = measured
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
        wrapper = self.wrapper()
        request = SimpleNamespace(
            func_tool=self.manager.get_full_tool_set(), conversation=None
        )
        await self.runtime._tool_publisher.scope_request(wrapper.context.event, request)
        aggregate = request.func_tool.get_tool(FF14_MODEL_TOOL)
        self.assertEqual(
            aggregate.parameters["properties"]["arguments"]["required"], []
        )

        async def call_query(call_wrapper, **parameters):
            return json.dumps(
                await self.call(parameters=parameters, wrapper=call_wrapper)
            )

        query_tool = SimpleNamespace(call=call_query)
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

        from yomihime_game_link_sdk.declarations import (
            CapabilityDescriptor,
            CapabilityEffect,
            InvocationPolicy,
            ModuleCategory,
            ModuleManifest,
            PackageManifest,
            ToolDescriptor,
        )
        from yomihime_game_link_sdk.display import DisplayDocument, TextBlock
        from yomihime_game_link_sdk.results import FactDocument
        from yomihime_game_link_sdk.services import (
            CapabilityHealth,
            HealthReport,
            HealthStatus,
            ModuleHandlers,
        )
        from yomihime_game_link_sdk.version import MODULE_ABI_VERSION

        await self.start()
        descriptor = validate_contract(
            ToolDescriptor(
                name="generic_query",
                description="generic",
                capability_id="read",
                parameter_mapping={"query": "text"},
            )
        )
        capability = validate_contract(
            CapabilityDescriptor(
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
        )
        manifest = validate_contract(
            ModuleManifest(
                module_id="module",
                route="generic",
                category=ModuleCategory.GAME,
                factory_entry="synthetic:Factory",
                module_version="1.0.0",
                capabilities=(capability,),
                tools=(descriptor,),
                commands=(),
            )
        )
        seen = []

        class Handler:
            async def invoke(self, context, parameters):
                seen.append(dict(parameters))
                return validate_contract(
                    CapabilityResult(
                        "generic",
                        ResultStatus.SUCCESS,
                        validate_contract(
                            DisplayDocument(
                                "Generic",
                                "generic",
                                (validate_contract(TextBlock("ok")),),
                            )
                        ),
                        model_facts=validate_contract(
                            FactDocument({"answer": parameters["text"]})
                        ),
                    )
                )

        handlers = validate_contract(ModuleHandlers({"read": Handler()}, {}, {}))

        class Instance:
            def handlers(self):
                return handlers

            async def start(self):
                pass

            async def stop(self):
                pass

            async def check_health(self):
                return validate_contract(
                    HealthReport(
                        {
                            "read": validate_contract(
                                CapabilityHealth(HealthStatus.AVAILABLE)
                            )
                        }
                    )
                )

        core = self.runtime.core_runtime
        core.registry.register_package(
            validate_contract(
                PackageManifest(
                    package_id="generic",
                    package_version="1.0.0",
                    contract_version=MODULE_ABI_VERSION,
                    modules=(manifest,),
                    author="Tests",
                    license="AGPL-3.0",
                    source="synthetic fixture",
                )
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
        self.assertEqual(len(self.manager.func_list), 1)
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
        self.assertNotIn("cached", market["listings"][0]["source"])
        self.assertNotIn("cached", second["market"]["listings"][0]["source"])
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

        # The tool-only Event fixture intentionally omits delivery methods.
        # Execute the pinned Host methods here, observing its exact-event send
        # separately from Context fan-out and retaining the Host sent flag.
        import hashlib
        import uuid
        from io import BytesIO

        from PIL import Image

        from .astrbot_contract import validate_host_source

        _, sources = validate_host_source()
        host_tree = ast.parse(sources["astrbot/core/platform/astr_message_event.py"])
        host_event = next(
            node
            for node in host_tree.body
            if isinstance(node, ast.ClassDef) and node.name == "AstrMessageEvent"
        )
        delivery_methods = [
            node
            for node in host_event.body
            if getattr(node, "name", None) in {"chain_result", "send"}
        ]

        async def upload_metric(**kwargs):
            pass

        delivery_namespace = {
            "asyncio": asyncio,
            "hashlib": hashlib,
            "uuid": uuid,
            "Metric": SimpleNamespace(upload=upload_metric),
            "MessageEventResult": SimpleNamespace,
        }
        future = ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")], level=0
        )
        exec(
            compile(
                ast.fix_missing_locations(
                    ast.Module(body=[future, *delivery_methods], type_ignores=[])
                ),
                "pinned_host_command_delivery",
                "exec",
            ),
            delivery_namespace,
        )
        event.chain_result = delivery_namespace["chain_result"].__get__(event)
        host_send = delivery_namespace["send"].__get__(event)
        event.platform_meta.name = "test-platform"
        exact_sends = []

        async def record_send(chain):
            exact_sends.append(chain)
            return await host_send(chain)

        event.send = record_send
        self.runtime._message_port._image_factory = lambda content: SimpleNamespace(
            png=content
        )

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
        self.assertTrue(event._has_send_oper)
        self.assertEqual(len(exact_sends), 1)
        self.assertEqual(self.context.calls, [])
        self.assertEqual(len(exact_sends[0].chain), 1)
        with Image.open(BytesIO(exact_sends[0].chain[0].png)) as png:
            self.assertEqual(png.format, "PNG")
            self.assertGreater(png.width, 100)
            self.assertGreater(png.height, 100)
            png.verify()

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
        for text in ("帮我看看犎牛牛排国服哪里最便宜", "帮我看看犎牛牛排哪里最便宜"):
            facts, reply = await self.provider.chat(
                self.wrapper(text), self.manager.get_full_tool_set()
            )
            self.assertEqual(facts["status"], "success")
            self.assertEqual(facts["market"]["query"], "犎牛牛排")
            self.assertEqual(facts["market"]["intent"], "min")
            self.assertEqual(facts["market"]["scope"]["kind"], "region")
            self.assertEqual(facts["market"]["scope"]["regions"], ["China"])
            self.assertEqual(
                facts["market"]["scope"]["default_basis"],
                "explicit_region" if "国服" in text else "request_language",
            )
            self.assertIn("仅已返回范围", reply)
        self.assertEqual(self.provider.trace[0]["arguments"]["region"], "cn")
        self.assertNotIn("region", self.provider.trace[1]["arguments"])

    async def test_malformed_enabled_subagent_scope_rejects_aggregate_reconstruction(
        self,
    ):
        await self.start()
        event = self.wrapper().context.event
        enabled = {
            "main_enable": True,
            "remove_main_duplicate_tools": True,
        }
        cases = (
            ("missing runtime", {**enabled, "agents": []}, None),
            ("agents not list", {**enabled, "agents": "invalid"}, ()),
            ("agent not mapping", {**enabled, "agents": [object()]}, ()),
            ("tools not list", {**enabled, "agents": [{"tools": "invalid"}]}, ()),
        )
        for label, orchestrator, handoffs in cases:
            with self.subTest(label=label):
                self.context.global_config = {"subagent_orchestrator": orchestrator}
                if handoffs is None:
                    if hasattr(self.context, "subagent_orchestrator"):
                        del self.context.subagent_orchestrator
                else:
                    self.context.subagent_orchestrator = SimpleNamespace(
                        handoffs=handoffs
                    )
                request = SimpleNamespace(
                    func_tool=self.manager.get_full_tool_set(), conversation=None
                )
                with self.assertRaisesRegex(
                    PermissionError, "cannot prove Host subagent tool scope"
                ):
                    await self.runtime._tool_publisher.scope_request(event, request)

        del self.context.subagent_orchestrator
        for label, orchestrator in (
            ("disabled", {"main_enable": False}),
            (
                "duplicate removal disabled",
                {"main_enable": True, "remove_main_duplicate_tools": False},
            ),
        ):
            with self.subTest(label=label):
                self.context.global_config = {"subagent_orchestrator": orchestrator}
                if orchestrator["main_enable"]:
                    self.context.subagent_orchestrator = SimpleNamespace(handoffs=[])
                request = SimpleNamespace(
                    func_tool=self.manager.get_full_tool_set(), conversation=None
                )
                await self.runtime._tool_publisher.scope_request(event, request)
                self.assertEqual(
                    request.func_tool.get_tool(FF14_MODEL_TOOL).parameters[
                        "properties"
                    ]["action"]["enum"],
                    list(FF14_ACTIONS.values()),
                )

    async def test_subagent_replay_uses_default_config_not_event_profile(self):
        await self.start()
        self.context.subagent_orchestrator = SimpleNamespace(handoffs=[])
        enabled = {
            "main_enable": True,
            "remove_main_duplicate_tools": True,
            "agents": [{"tools": ["ff14_market_query"]}],
        }
        disabled = {
            "main_enable": False,
            "remove_main_duplicate_tools": False,
        }
        cases = (
            (
                "default removes while event disables",
                enabled,
                disabled,
                [
                    "market_select",
                    "item_lookup",
                    "item_select",
                    "logs_character",
                ],
            ),
            (
                "event cannot remove while default disables",
                disabled,
                enabled,
                list(FF14_ACTIONS.values()),
            ),
        )
        for label, default, event_profile, expected in cases:
            with self.subTest(label=label):
                self.context.global_config = {"subagent_orchestrator": default}
                self.context.config = {"subagent_orchestrator": event_profile}
                event = self.wrapper().context.event
                handoff = self.host.HandoffTool(
                    self.host.Agent(name="scope", tools=["ff14_market_query"])
                )
                self.context.subagent_orchestrator = SimpleNamespace(handoffs=[handoff])
                self.assertEqual(
                    self.runtime._tool_publisher._subagent_assigned_tools(event),
                    frozenset({"ff14_market_query"})
                    if default is enabled
                    else frozenset(),
                )
                request = SimpleNamespace(
                    func_tool=self.manager.get_full_tool_set(), conversation=None
                )
                request.func_tool.add_tool(handoff)
                await self.runtime._tool_publisher.scope_request(event, request)
                self.assertEqual(
                    request.func_tool.get_tool(FF14_MODEL_TOOL).parameters[
                        "properties"
                    ]["action"]["enum"],
                    expected,
                )

    async def test_handoff_clones_exact_event_scope_with_native_global_dispatch(self):
        await self.start()
        passthrough = self.host.FunctionTool(
            name="synthetic_passthrough",
            description="synthetic",
            parameters={"type": "object", "properties": {}},
            handler=lambda: None,
        )
        original_agent = self.host.Agent(
            name="scope",
            instructions="synthetic instructions",
            tools=["ff14_market_query", passthrough],
            begin_dialogs=[{"role": "user", "content": "synthetic"}],
        )
        original = self.host.HandoffTool(original_agent)
        original.provider_id = "synthetic-provider"
        self.context.subagent_orchestrator = SimpleNamespace(handoffs=[original])
        self.context.global_config = {
            "subagent_orchestrator": {
                "main_enable": True,
                "remove_main_duplicate_tools": True,
                "agents": [{"tools": ["ff14_market_query"]}],
            }
        }
        self.context.config = {
            "provider_settings": {
                "computer_use_runtime": "none",
                "sandbox": {"booter": None},
            }
        }

        wrappers = (self.wrapper(), self.wrapper(session="group-43"))
        clones = []
        for wrapper in wrappers:
            request = SimpleNamespace(
                func_tool=self.manager.get_full_tool_set(), conversation=None
            )
            request.func_tool.add_tool(original)
            runtime_calls = len(self.host.runtime_tool_calls)
            await self.runtime._tool_publisher.scope_request(
                wrapper.context.event, request
            )
            self.assertEqual(len(self.host.runtime_tool_calls), runtime_calls)
            clone = request.func_tool.get_tool(original.name)
            clones.append(clone)
            self.assertIsInstance(clone, self.host.HandoffTool)
            self.assertIsNot(clone, original)
            self.assertIsNot(clone.agent, original.agent)
            self.assertEqual(clone.provider_id, original.provider_id)
            self.assertEqual(clone.parameters, original.parameters)
            self.assertEqual(clone.description, original.description)
            self.assertEqual(clone.active, original.active)
            self.assertEqual(clone.agent.instructions, original.agent.instructions)
            self.assertEqual(clone.agent.begin_dialogs, original.agent.begin_dialogs)
            self.assertEqual(original.agent.tools, ["ff14_market_query", passthrough])
            self.assertEqual(
                request.func_tool.get_tool(FF14_MODEL_TOOL).parameters["properties"][
                    "action"
                ]["enum"],
                [
                    "market_select",
                    "item_lookup",
                    "item_select",
                    "logs_character",
                ],
            )
            handoff_tools = self.host.FunctionToolExecutor._build_handoff_toolset(
                wrapper, clone.agent.tools
            )
            self.assertEqual(len(self.host.runtime_tool_calls), runtime_calls + 1)
            self.assertIsNotNone(handoff_tools)
            self.assertIs(handoff_tools.get_tool(passthrough.name), passthrough)
            scoped = handoff_tools.get_tool(FF14_MODEL_TOOL)
            self.assertEqual(
                scoped.parameters["properties"]["action"]["enum"],
                ["market_query"],
            )
            native_result = await (
                self.manager.get_full_tool_set()
                .get_tool(FF14_MODEL_TOOL)
                .call(
                    wrapper,
                    action="market_query",
                    arguments={"query": "44091"},
                )
            )
            self.assertIsInstance(json.loads(native_result), dict)

        self.assertIsNot(clones[0], clones[1])
        self.assertIsNot(clones[0].agent.tools, clones[1].agent.tools)
        first_tools = self.host.FunctionToolExecutor._build_handoff_toolset(
            wrappers[0], clones[0].agent.tools
        )
        with self.assertRaisesRegex(PermissionError, "exact Host request event"):
            await first_tools.get_tool(FF14_MODEL_TOOL).call(
                wrappers[1],
                action="market_query",
                arguments={"query": "44091"},
            )

        self.host.preferences.permissions = {"_default": {"ff14_market_query": "admin"}}
        denied = await first_tools.get_tool(FF14_MODEL_TOOL).call(
            wrappers[0],
            action="market_query",
            arguments={"query": "44091"},
        )
        self.assertIn("requires admin privileges", denied)
        self.host.preferences.permissions = {"_default": {FF14_MODEL_TOOL: "admin"}}
        denied = await first_tools.get_tool(FF14_MODEL_TOOL).call(
            wrappers[0],
            action="market_query",
            arguments={"query": "44091"},
        )
        self.assertIn("requires admin privileges", denied)
        wrappers[0].context.event.role = "admin"
        allowed = await first_tools.get_tool(FF14_MODEL_TOOL).call(
            wrappers[0],
            action="market_query",
            arguments={"query": "44091", "region": "cn"},
        )
        self.assertIsInstance(json.loads(allowed), dict)
        wrappers[0].context.event.role = "member"
        self.host.preferences.permissions = {}
        handle = next(
            item
            for item in self.runtime._tool_publisher.handles
            if item.published_name == FF14_MODEL_TOOL
        )
        handle.revoked = True
        with self.assertRaisesRegex(PermissionError, "no longer current"):
            await first_tools.get_tool(FF14_MODEL_TOOL).call(
                wrappers[0],
                action="market_query",
                arguments={"query": "44091"},
            )

    async def test_handoff_preserves_none_empty_parent_and_nested_semantics(self):
        await self.start()
        publisher = self.runtime._tool_publisher
        all_actions = list(FF14_ACTIONS.values())

        async def scoped(configured, assigned):
            agent = self.host.Agent(name="scope", tools=configured)
            handoff = self.host.HandoffTool(agent)
            self.context.subagent_orchestrator = SimpleNamespace(handoffs=[handoff])
            self.context.global_config = {
                "subagent_orchestrator": {
                    "main_enable": True,
                    "remove_main_duplicate_tools": True,
                    "agents": [{"tools": assigned}],
                }
            }
            self.context.config = {
                "provider_settings": {
                    "computer_use_runtime": "none",
                    "sandbox": {"booter": None},
                }
            }
            wrapper = self.wrapper()
            request = SimpleNamespace(
                func_tool=self.manager.get_full_tool_set(), conversation=None
            )
            request.func_tool.add_tool(handoff)
            await publisher.scope_request(wrapper.context.event, request)
            clone = request.func_tool.get_tool(handoff.name)
            return (
                wrapper,
                request,
                clone,
                self.host.FunctionToolExecutor._build_handoff_toolset(
                    wrapper, clone.agent.tools
                ),
            )

        runtime_calls = len(self.host.runtime_tool_calls)
        _, request, clone, built = await scoped(None, None)
        self.assertEqual(len(self.host.runtime_tool_calls), runtime_calls + 1)
        self.assertIsNone(clone.agent.tools)
        self.assertFalse(hasattr(clone, "_ygl_handoff_parent"))
        self.assertIsNone(request.func_tool.get_tool(FF14_MODEL_TOOL))
        self.assertEqual(
            built.get_tool(FF14_MODEL_TOOL).parameters["properties"]["action"]["enum"],
            all_actions,
        )

        runtime_calls = len(self.host.runtime_tool_calls)
        _, request, clone, built = await scoped([], [])
        self.assertEqual(len(self.host.runtime_tool_calls), runtime_calls + 1)
        self.assertEqual(clone.agent.tools, [])
        self.assertFalse(hasattr(clone, "_ygl_handoff_parent"))
        self.assertIsNotNone(request.func_tool.get_tool(FF14_MODEL_TOOL))
        self.assertIsNone(built)

        runtime_calls = len(self.host.runtime_tool_calls)
        _, request, _, built = await scoped([FF14_MODEL_TOOL], [FF14_MODEL_TOOL])
        self.assertEqual(len(self.host.runtime_tool_calls), runtime_calls + 1)
        self.assertIsNone(request.func_tool.get_tool(FF14_MODEL_TOOL))
        self.assertEqual(
            built.get_tool(FF14_MODEL_TOOL).parameters["properties"]["action"]["enum"],
            all_actions,
        )

        original_runtime_builder = (
            self.host.FunctionToolExecutor._get_runtime_computer_tools
        )
        runtime_objects = []

        def synthetic_runtime(runtime, manager, booter):
            self.assertEqual(runtime, "local")
            self.assertIs(manager, self.manager)
            self.assertIsNone(booter)
            tool = self.host.FunctionTool(
                name="astrbot_execute_shell",
                description="synthetic",
                parameters={"type": "object", "properties": {}},
                handler=lambda: None,
            )
            runtime_objects.append(tool)
            return {tool.name: tool}

        self.host.FunctionToolExecutor._get_runtime_computer_tools = staticmethod(
            synthetic_runtime
        )
        try:
            runtime_a = self.host.HandoffTool(
                self.host.Agent(name="runtime_a", tools=None)
            )
            runtime_b = self.host.HandoffTool(
                self.host.Agent(name="runtime_b", tools=None)
            )
            self.context.subagent_orchestrator = SimpleNamespace(
                handoffs=[runtime_a, runtime_b]
            )
            self.context.global_config = {
                "subagent_orchestrator": {
                    "main_enable": True,
                    "remove_main_duplicate_tools": False,
                    "agents": [],
                }
            }
            self.context.config = {
                "provider_settings": {
                    "computer_use_runtime": "local",
                    "sandbox": {"booter": None},
                }
            }
            request = SimpleNamespace(
                func_tool=self.manager.get_full_tool_set(), conversation=None
            )
            request.func_tool.add_tool(runtime_a)
            request.func_tool.add_tool(runtime_b)
            await publisher.scope_request(self.wrapper().context.event, request)
            clone_a = request.func_tool.get_tool(runtime_a.name)
            clone_b = request.func_tool.get_tool(runtime_b.name)
            self.assertEqual(runtime_objects, [])
            built_a = self.host.FunctionToolExecutor._build_handoff_toolset(
                self.wrapper(), clone_a.agent.tools
            )
            built_b = self.host.FunctionToolExecutor._build_handoff_toolset(
                self.wrapper(), clone_b.agent.tools
            )
            tool_a = built_a.get_tool("astrbot_execute_shell")
            tool_b = built_b.get_tool("astrbot_execute_shell")
            self.assertIsNot(tool_a, tool_b)
            self.assertEqual(runtime_objects, [tool_a, tool_b])
        finally:
            self.host.FunctionToolExecutor._get_runtime_computer_tools = (
                original_runtime_builder
            )

        self.host.preferences.inactivated_llm_tools = ["ff14_market_query"]
        _, _, _, built = await scoped(
            ["ff14_market_query", "ff14_item_lookup"],
            ["ff14_market_query", "ff14_item_lookup"],
        )
        self.assertEqual(
            built.get_tool(FF14_MODEL_TOOL).parameters["properties"]["action"]["enum"],
            ["item_lookup"],
        )
        self.host.preferences.inactivated_llm_tools = [FF14_MODEL_TOOL]
        parent_handle = next(
            item for item in publisher.handles if item.published_name == FF14_MODEL_TOOL
        )
        parent_handle.tool.active = False
        _, _, _, built = await scoped([FF14_MODEL_TOOL], [FF14_MODEL_TOOL])
        self.assertIsNone(built)
        parent_handle.tool.active = True
        self.host.preferences.inactivated_llm_tools = []

        handle = next(
            item for item in publisher.handles if item.published_name == FF14_MODEL_TOOL
        )
        foreign_parent = self.host.FunctionTool(
            name=FF14_MODEL_TOOL,
            description="foreign parent object",
            parameters={"type": "object", "properties": {}},
            handler=lambda: None,
        )
        other_manager = self.host.Manager.__new__(self.host.Manager)
        other_manager.func_list = [handle.tool]
        other_wrapper = other_manager.get_full_tool_set().get_tool(FF14_MODEL_TOOL)
        same_manager_wrapper = self.manager.get_full_tool_set().get_tool(
            FF14_MODEL_TOOL
        )

        class CustomSameFields(self.host.FunctionTool):
            async def call(self, _context, **_kwargs):
                return "custom child semantics"

        custom_same_fields = CustomSameFields(
            name=FF14_MODEL_TOOL,
            description="custom child same-fields wrapper",
            parameters={"type": "object", "properties": {}},
        )
        custom_same_fields._wrapped = handle.tool
        custom_same_fields._mgr = self.manager
        for collision in (
            foreign_parent,
            other_wrapper,
            same_manager_wrapper,
            custom_same_fields,
        ):
            with self.subTest(collision=collision.description):
                _, _, _, built = await scoped([collision, "ff14_market_query"], [])
                self.assertIs(built.get_tool(FF14_MODEL_TOOL), collision)
        self.assertEqual(
            await built.get_tool(FF14_MODEL_TOOL).call(self.wrapper()),
            "custom child semantics",
        )

        nested = self.host.HandoffTool(
            self.host.Agent(name="nested", tools=["ff14_item_lookup"])
        )
        _, _, outer, built = await scoped([nested], [])
        nested_clone = built.get_tool(nested.name)
        self.assertIsInstance(nested_clone, self.host.HandoffTool)
        self.assertIsNot(nested_clone, nested)
        nested_tools = self.host.FunctionToolExecutor._build_handoff_toolset(
            self.wrapper(), nested_clone.agent.tools
        )
        # The nested scope is still bound to the outer request event.
        self.assertEqual(
            nested_tools.get_tool(FF14_MODEL_TOOL).parameters["properties"]["action"][
                "enum"
            ],
            ["item_lookup"],
        )
        self.assertIn(nested_clone, list(outer.agent.tools))

        cyclic_a = self.host.HandoffTool(self.host.Agent(name="cyclic_a", tools=[]))
        cyclic_b = self.host.HandoffTool(self.host.Agent(name="cyclic_b", tools=[]))
        cyclic_a.agent.tools.extend([cyclic_b, "ff14_market_query"])
        cyclic_b.agent.tools.extend([cyclic_a, "ff14_item_lookup"])
        self.context.subagent_orchestrator = SimpleNamespace(
            handoffs=[cyclic_a, cyclic_b]
        )
        self.context.global_config = {
            "subagent_orchestrator": {
                "main_enable": True,
                "remove_main_duplicate_tools": False,
                "agents": [],
            }
        }
        request = SimpleNamespace(
            func_tool=self.manager.get_full_tool_set(), conversation=None
        )
        request.func_tool.add_tool(cyclic_a)
        request.func_tool.add_tool(cyclic_b)
        await publisher.scope_request(self.wrapper().context.event, request)
        clone_a = request.func_tool.get_tool(cyclic_a.name)
        clone_b = request.func_tool.get_tool(cyclic_b.name)
        self.assertIsInstance(clone_a, self.host.HandoffTool)
        self.assertIsInstance(clone_b, self.host.HandoffTool)
        clone_a_tools = list(clone_a.agent.tools)
        clone_b_tools = list(clone_b.agent.tools)
        self.assertIs(clone_a_tools[0], clone_b)
        self.assertIs(clone_b_tools[0], clone_a)
        self.assertEqual(
            clone_a_tools[1].parameters["properties"]["action"]["enum"],
            ["market_query"],
        )
        self.assertEqual(
            clone_b_tools[1].parameters["properties"]["action"]["enum"],
            ["item_lookup"],
        )
        self.assertIs(cyclic_a.agent.tools[0], cyclic_b)
        self.assertIs(cyclic_b.agent.tools[0], cyclic_a)

        valid = self.host.HandoffTool(
            self.host.Agent(name="valid", tools=["ff14_market_query"])
        )
        malformed = self.host.HandoffTool(self.host.Agent(name="malformed", tools=[]))
        malformed.agent.tools = object()
        self.context.subagent_orchestrator = SimpleNamespace(
            handoffs=[valid, malformed]
        )
        request = SimpleNamespace(
            func_tool=self.manager.get_full_tool_set(), conversation=None
        )
        request.func_tool.add_tool(valid)
        request.func_tool.add_tool(malformed)
        with self.assertRaisesRegex(
            PermissionError, "cannot scope Host subagent handoff"
        ):
            await publisher.scope_request(self.wrapper().context.event, request)
        self.assertIs(request.func_tool.get_tool(valid.name), valid)
        self.assertIs(request.func_tool.get_tool(malformed.name), malformed)

    async def test_handoff_clones_module_global_nested_string_resolution(self):
        await self.start()
        child = self.host.HandoffTool(
            self.host.Agent(name="child", tools=["ff14_item_lookup"])
        )
        parent = self.host.HandoffTool(
            self.host.Agent(name="parent", tools=[child.name, "synthetic_foreign_name"])
        )
        self.manager.func_list.append(child)
        try:
            self.context.subagent_orchestrator = SimpleNamespace(handoffs=[parent])
            self.context.global_config = {
                "subagent_orchestrator": {
                    "main_enable": True,
                    "remove_main_duplicate_tools": False,
                    "agents": [],
                }
            }
            self.context.config = {
                "provider_settings": {
                    "computer_use_runtime": "none",
                    "sandbox": {"booter": None},
                }
            }

            async def prepared(session):
                wrapper = self.wrapper(session=session)
                request = SimpleNamespace(
                    func_tool=self.manager.get_full_tool_set(), conversation=None
                )
                request.func_tool.add_tool(parent)
                await self.runtime._tool_publisher.scope_request(
                    wrapper.context.event, request
                )
                clone = request.func_tool.get_tool(parent.name)
                self.assertIsInstance(clone, self.host.HandoffTool)
                return wrapper, clone

            wrapper, parent_clone = await prepared("group-42")
            parent_tools = self.host.FunctionToolExecutor._build_handoff_toolset(
                wrapper, parent_clone.agent.tools
            )
            child_clone = parent_tools.get_tool(child.name)
            self.assertIsInstance(child_clone, self.host.HandoffTool)
            self.assertIsNot(child_clone, child)
            child_tools = self.host.FunctionToolExecutor._build_handoff_toolset(
                wrapper, child_clone.agent.tools
            )
            self.assertEqual(
                child_tools.get_tool(FF14_MODEL_TOOL).parameters["properties"][
                    "action"
                ]["enum"],
                ["item_lookup"],
            )
            self.assertIn("synthetic_foreign_name", list(parent_clone.agent.tools))
            self.assertEqual(parent.agent.tools, [child.name, "synthetic_foreign_name"])
            self.assertEqual(child.agent.tools, ["ff14_item_lookup"])

            # String registration stays late-bound: prepare while active, then
            # disable before Host actually builds the child toolset.
            wrapper, parent_clone = await prepared("group-43")
            child.active = False
            inactive_tools = self.host.FunctionToolExecutor._build_handoff_toolset(
                wrapper, parent_clone.agent.tools
            )
            self.assertTrue(
                inactive_tools is None or inactive_tools.get_tool(child.name) is None
            )

            # The reverse transition also matches Host: an inactive target at
            # prepare time may become the current active registration later.
            wrapper, parent_clone = await prepared("group-44")
            child.active = True
            reenabled = self.host.FunctionToolExecutor._build_handoff_toolset(
                wrapper, parent_clone.agent.tools
            ).get_tool(child.name)
            self.assertIsInstance(reenabled, self.host.HandoffTool)
            self.assertEqual(
                self.host.FunctionToolExecutor._build_handoff_toolset(
                    wrapper, reenabled.agent.tools
                )
                .get_tool(FF14_MODEL_TOOL)
                .parameters["properties"]["action"]["enum"],
                ["item_lookup"],
            )

            # Replacing the module-global registration between prepare and
            # execute must clone the new target rather than the old object.
            wrapper, parent_clone = await prepared("group-45")
            replacement = self.host.HandoffTool(
                self.host.Agent(name="child", tools=["ff14_market_query"])
            )
            self.manager.func_list.append(replacement)
            try:
                replaced = self.host.FunctionToolExecutor._build_handoff_toolset(
                    wrapper, parent_clone.agent.tools
                ).get_tool(child.name)
                self.assertIsInstance(replaced, self.host.HandoffTool)
                self.assertIsNot(replaced._ygl_handoff_parent, child)
                self.assertIs(replaced._ygl_handoff_parent, replacement)
                self.assertEqual(
                    self.host.FunctionToolExecutor._build_handoff_toolset(
                        wrapper, replaced.agent.tools
                    )
                    .get_tool(FF14_MODEL_TOOL)
                    .parameters["properties"]["action"]["enum"],
                    ["market_query"],
                )
            finally:
                self.manager.func_list.remove(replacement)
        finally:
            child.active = True
            self.manager.func_list.remove(child)

    async def test_handoff_actual_agent_scope_may_differ_from_main_fallback(self):
        await self.start()

        class Personas:
            def get_persona_v3_by_id(self, persona_id):
                self.persona_id = persona_id
                return {"tools": None}

        self.context.persona_manager = Personas()
        handoff = self.host.HandoffTool(self.host.Agent(name="scope", tools=None))
        self.context.subagent_orchestrator = SimpleNamespace(handoffs=[handoff])
        self.context.global_config = {
            "subagent_orchestrator": {
                "main_enable": True,
                "remove_main_duplicate_tools": True,
                "agents": [
                    {
                        "persona_id": "all-tools",
                        "tools": ["ff14_item_lookup"],
                    }
                ],
            }
        }
        self.context.config = {
            "provider_settings": {
                "computer_use_runtime": "none",
                "sandbox": {"booter": None},
            }
        }
        wrapper = self.wrapper()
        request = SimpleNamespace(
            func_tool=self.manager.get_full_tool_set(), conversation=None
        )
        request.func_tool.add_tool(handoff)
        await self.runtime._tool_publisher.scope_request(wrapper.context.event, request)
        main = request.func_tool.get_tool(FF14_MODEL_TOOL)
        self.assertNotIn(
            "item_lookup",
            main.parameters["properties"]["action"]["enum"],
        )
        clone = request.func_tool.get_tool(handoff.name)
        built = self.host.FunctionToolExecutor._build_handoff_toolset(
            wrapper, clone.agent.tools
        )
        self.assertEqual(
            built.get_tool(FF14_MODEL_TOOL).parameters["properties"]["action"]["enum"],
            list(FF14_ACTIONS.values()),
        )

    async def test_native_parent_direct_call_preserves_owned_policy_boundary(self):
        await self.start()
        publisher = self.runtime._tool_publisher
        handle = next(
            item for item in publisher.handles if item.published_name == FF14_MODEL_TOOL
        )
        wrapper = self.wrapper()
        value = await handle.tool.call(
            wrapper,
            action="market_query",
            arguments={"query": "44091", "region": "cn"},
        )
        self.assertIsInstance(json.loads(value), dict)
        for kwargs in (
            {
                "action": "market_query",
                "arguments": {"query": "44091"},
                "forged": True,
            },
            {"action": "forged", "arguments": {"query": "44091"}},
            {
                "action": "market_query",
                "arguments": {"query": "44091", "character": "forged"},
            },
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                await handle.tool.call(wrapper, **kwargs)

        foreign_parent = self.host.FunctionTool(
            name=FF14_MODEL_TOOL,
            description="foreign",
            parameters={"type": "object", "properties": {}},
            handler=lambda: None,
        )
        self.manager.func_list.append(foreign_parent)
        try:
            with self.assertRaisesRegex(PermissionError, "no longer current"):
                await handle.tool.call(
                    wrapper,
                    action="market_query",
                    arguments={"query": "44091"},
                )
        finally:
            self.manager.func_list.remove(foreign_parent)

        wrapper.context.event.plugins_name = []
        with self.assertRaisesRegex(PermissionError, "plugin scope"):
            await handle.tool.call(
                wrapper,
                action="market_query",
                arguments={"query": "44091"},
            )
        wrapper.context.event.plugins_name = None

        self.host.preferences.permissions = {"_default": {"ff14_market_query": "admin"}}
        denied = await handle.tool.call(
            wrapper,
            action="market_query",
            arguments={"query": "44091"},
        )
        self.assertIn("requires admin privileges", denied)
        self.host.preferences.permissions = {}

        wrong_context = self.wrapper(context=self.host.Context())
        with self.assertRaisesRegex(PermissionError, "official Host event"):
            await handle.tool.call(
                wrong_context,
                action="market_query",
                arguments={"query": "44091"},
            )

        descriptor = handle.action_descriptors["market_query"]
        for label, forged_handle, forged_descriptor, forged_action in (
            ("handle", copy(handle), descriptor, "market_query"),
            ("descriptor", handle, copy(descriptor), "market_query"),
            ("action", handle, descriptor, "item_lookup"),
        ):
            with self.subTest(label=label), self.assertRaises(PermissionError):
                await self.runtime.invoke_tool(
                    handle.module_id,
                    descriptor.name,
                    {"query": "44091"},
                    wrapper.context.event,
                    tool_handle=forged_handle,
                    tool_descriptor=forged_descriptor,
                    tool_action=forged_action,
                )

        handle.revoked = True
        with self.assertRaisesRegex(PermissionError, "no longer current"):
            await handle.tool.call(
                wrapper,
                action="market_query",
                arguments={"query": "44091"},
            )

    async def test_aggregate_schema_factors_exact_description_sentences_only(self):
        await self.start()
        publisher = self.runtime._tool_publisher
        handle = next(
            item for item in publisher.handles if item.published_name == FF14_MODEL_TOOL
        )
        tool = self.manager.get_full_tool_set().get_tool(FF14_MODEL_TOOL)
        parameters = tool.parameters
        action_help = parameters["properties"]["action"]["description"]
        sentences = {
            sentence
            for descriptor in handle.action_descriptors.values()
            for sentence in re.split(r"(?<=[.!?])\s+", descriptor.description.strip())
            if sentence
        }
        for sentence in sentences:
            with self.subTest(sentence=sentence):
                self.assertEqual(action_help.count(sentence), 1)

        naive = "；".join(
            f"{descriptor.action}: {descriptor.description}"
            for descriptor in handle.action_descriptors.values()
        )
        naive_parameters = json.loads(json.dumps(parameters, ensure_ascii=False))
        naive_parameters["properties"]["action"]["description"] = naive
        optimized_bytes = len(
            json.dumps(
                parameters,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )
        naive_bytes = len(
            json.dumps(
                naive_parameters,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )
        self.assertLess(optimized_bytes, naive_bytes)
        naive_parameters["properties"]["action"]["description"] = action_help
        self.assertEqual(naive_parameters, parameters)
        self.assertEqual(len(parameters["properties"]["arguments"]["properties"]), 23)

    async def _assert_native_final_policy_transition(self, transition):
        await self.start()
        publisher = self.runtime._tool_publisher
        handle = next(
            item for item in publisher.handles if item.published_name == FF14_MODEL_TOOL
        )
        wrapper = self.wrapper()
        original_inactive = publisher._inactive_tool_names
        calls = 0

        async def suspended_final_read():
            nonlocal calls
            calls += 1
            inactive = await original_inactive()
            if calls == 2:
                await asyncio.sleep(0)
                transition(publisher, handle)
            return inactive

        publisher._inactive_tool_names = suspended_final_read
        try:
            with self.assertRaisesRegex(
                PermissionError, "changed during final policy check"
            ):
                await handle.tool.call(
                    wrapper,
                    action="market_query",
                    arguments={"query": "44091", "region": "cn"},
                )
            self.assertEqual(calls, 2)
        finally:
            publisher._inactive_tool_names = original_inactive

    async def test_native_final_policy_await_rechecks_handle_revoke(self):
        def revoke(_publisher, handle):
            handle.revoked = True

        await self._assert_native_final_policy_transition(revoke)

    async def test_native_final_policy_await_rechecks_manager_replacement(self):
        state = {}

        def replace(_publisher, handle):
            tools = handle.manager.func_list
            state["index"] = tools.index(handle.tool)
            state["handle"] = handle
            tools[state["index"]] = self.host.FunctionTool(
                name=handle.published_name,
                description="foreign replacement",
                parameters={"type": "object", "properties": {}},
                handler=lambda: None,
            )

        try:
            await self._assert_native_final_policy_transition(replace)
        finally:
            handle = state.get("handle")
            if handle is not None:
                handle.manager.func_list[state["index"]] = handle.tool

    async def test_native_final_policy_await_rechecks_epoch_change(self):
        state = {}

        def advance(_publisher, handle):
            registry = self.runtime.core_runtime.registry
            state["registry"] = registry
            state["module_id"] = handle.module_id
            state["epoch"] = handle.epoch
            registry._publish_lifecycle_projection(
                handle.module_id,
                enabled=True,
                epoch=handle.epoch + 1,
            )

        try:
            await self._assert_native_final_policy_transition(advance)
        finally:
            registry = state.get("registry")
            if registry is not None:
                registry._publish_lifecycle_projection(
                    state["module_id"],
                    enabled=True,
                    epoch=state["epoch"],
                )

    async def test_scope_request_sanitizes_before_fault_and_commits_atomically(self):
        await self.start()
        publisher = self.runtime._tool_publisher
        handle = next(
            item for item in publisher.handles if item.published_name == FF14_MODEL_TOOL
        )
        unrelated = self.host.FunctionTool(
            name="synthetic_unrelated",
            description="synthetic",
            parameters={"type": "object", "properties": {}},
            handler=lambda: None,
        )

        def request_with_raw():
            tools = self.manager.get_full_tool_set()
            tools.add_tool(unrelated)
            return SimpleNamespace(func_tool=tools, conversation=None)

        request = request_with_raw()
        self.context.global_config = None
        with self.assertRaisesRegex(PermissionError, "subagent tool scope"):
            await publisher.scope_request(self.wrapper().context.event, request)
        self.assertIsNone(request.func_tool.get_tool(FF14_MODEL_TOOL))
        self.assertIs(request.func_tool.get_tool(unrelated.name), unrelated)

        self.context.global_config = {"subagent_orchestrator": {}}
        original_scope_handoffs = publisher._scope_handoffs
        partial = self.host.FunctionTool(
            name="synthetic_partial",
            description="synthetic",
            parameters={"type": "object", "properties": {}},
            handler=lambda: None,
        )

        async def fail_after_partial(_event, proposed, _inactive):
            proposed.add_tool(partial)
            raise PermissionError("synthetic projection failure")

        publisher._scope_handoffs = fail_after_partial
        try:
            request = request_with_raw()
            with self.assertRaisesRegex(PermissionError, "projection failure"):
                await publisher.scope_request(self.wrapper().context.event, request)
            self.assertIsNone(request.func_tool.get_tool(FF14_MODEL_TOOL))
            self.assertIsNone(request.func_tool.get_tool(partial.name))
            self.assertIs(request.func_tool.get_tool(unrelated.name), unrelated)
        finally:
            publisher._scope_handoffs = original_scope_handoffs

        original_inactive = publisher._inactive_tool_names

        async def cancel_policy_read():
            raise asyncio.CancelledError()

        publisher._inactive_tool_names = cancel_policy_read
        try:
            request = request_with_raw()
            with self.assertRaises(asyncio.CancelledError):
                await publisher.scope_request(self.wrapper().context.event, request)
            self.assertIsNone(request.func_tool.get_tool(FF14_MODEL_TOOL))
            self.assertIs(request.func_tool.get_tool(unrelated.name), unrelated)
        finally:
            publisher._inactive_tool_names = original_inactive

        foreign_during_await = self.host.FunctionTool(
            name=FF14_MODEL_TOOL,
            description="foreign during projection",
            parameters={"type": "object", "properties": {}},
            handler=lambda: None,
        )
        request = request_with_raw()

        async def mutate_during_policy_read():
            values = await original_inactive()
            request.func_tool.add_tool(foreign_during_await)
            return values

        publisher._inactive_tool_names = mutate_during_policy_read
        try:
            with self.assertRaisesRegex(PermissionError, "changed during projection"):
                await publisher.scope_request(self.wrapper().context.event, request)
            self.assertIs(
                request.func_tool.get_tool(FF14_MODEL_TOOL), foreign_during_await
            )
            self.assertIs(request.func_tool.get_tool(unrelated.name), unrelated)
        finally:
            publisher._inactive_tool_names = original_inactive

        invalid = SimpleNamespace(func_tool=object(), conversation=None)
        with self.assertRaisesRegex(PermissionError, "valid Host request ToolSet"):
            await publisher.scope_request(self.wrapper().context.event, invalid)
        self.assertIsInstance(invalid.func_tool, self.host.ToolSet)
        self.assertTrue(invalid.func_tool.empty())

        foreign = self.host.FunctionTool(
            name=FF14_MODEL_TOOL,
            description="foreign request tool",
            parameters={"type": "object", "properties": {}},
            handler=lambda: None,
        )
        wrapped_foreign = self.host.FunctionTool(
            name=FF14_MODEL_TOOL,
            description="wrapped foreign request tool",
            parameters={"type": "object", "properties": {}},
            handler=lambda: None,
        )
        wrapped_foreign._wrapped = foreign

        class CustomSameFields(self.host.FunctionTool):
            async def call(self, _context, **_kwargs):
                return "custom guard semantics"

        custom_same_fields = CustomSameFields(
            name=FF14_MODEL_TOOL,
            description="custom same-fields wrapper",
            parameters={"type": "object", "properties": {}},
        )
        custom_same_fields._wrapped = handle.tool
        custom_same_fields._mgr = self.manager
        for selected in (foreign, wrapped_foreign, custom_same_fields):
            with self.subTest(selected=selected.description):
                request = request_with_raw()
                request.func_tool.add_tool(selected)
                await publisher.scope_request(self.wrapper().context.event, request)
                self.assertIs(request.func_tool.get_tool(FF14_MODEL_TOOL), selected)
                self.assertIs(request.func_tool.get_tool(unrelated.name), unrelated)
        self.assertEqual(
            await custom_same_fields.call(self.wrapper()), "custom guard semantics"
        )

        owned_wrapper = self.manager.get_full_tool_set().get_tool(FF14_MODEL_TOOL)
        self.assertIs(owned_wrapper._wrapped, handle.tool)
        self.assertIs(owned_wrapper._mgr, self.manager)
        request = SimpleNamespace(
            func_tool=self.host.ToolSet([owned_wrapper, unrelated]), conversation=None
        )
        await publisher.scope_request(self.wrapper().context.event, request)
        scoped = request.func_tool.get_tool(FF14_MODEL_TOOL)
        self.assertIsNot(scoped, owned_wrapper)
        self.assertIs(scoped._ygl_parent, handle)

        other_manager = self.host.Manager.__new__(self.host.Manager)
        other_manager.func_list = [handle.tool]

        async def other_manager_denies(_name, _context):
            return "other manager denied"

        other_manager._check_tool_permission = other_manager_denies
        other_wrapper = other_manager.get_full_tool_set().get_tool(FF14_MODEL_TOOL)
        self.assertIs(other_wrapper._wrapped, handle.tool)
        self.assertIs(other_wrapper._mgr, other_manager)
        request = request_with_raw()
        request.func_tool.add_tool(other_wrapper)
        await publisher.scope_request(self.wrapper().context.event, request)
        selected = request.func_tool.get_tool(FF14_MODEL_TOOL)
        self.assertIs(selected, other_wrapper)
        before = len(self.transport.requests)
        self.assertEqual(
            await selected.call(
                self.wrapper(),
                action="market_query",
                arguments={"query": "44091"},
            ),
            "other manager denied",
        )
        self.assertEqual(len(self.transport.requests), before)

        request = request_with_raw()
        await publisher.scope_request(self.wrapper().context.event, request)
        committed = request.func_tool.get_tool(FF14_MODEL_TOOL)
        self.assertIsNotNone(committed)
        self.assertIsNot(committed, handle.tool)
        self.assertIs(request.func_tool.get_tool(unrelated.name), unrelated)

    async def test_inactive_parent_is_not_reconstructed_and_revokes_scoped_actions(
        self,
    ):
        await self.start()
        publisher = self.runtime._tool_publisher
        handle = next(
            item for item in publisher.handles if item.published_name == FF14_MODEL_TOOL
        )
        handle.tool.active = False
        publisher.publish()
        self.assertIs(
            next(
                item
                for item in publisher.handles
                if item.published_name == FF14_MODEL_TOOL
            ),
            handle,
        )
        self.assertFalse(handle.tool.active)
        event = self.wrapper().context.event
        before_umos = tuple(self.context.config_umos)
        filtered = self.manager.get_full_tool_set()
        wrapped = filtered.get_tool(FF14_MODEL_TOOL)
        self.assertIsNotNone(wrapped)
        self.assertFalse(wrapped.active)
        request = SimpleNamespace(func_tool=filtered, conversation=None)
        await publisher.scope_request(event, request)
        self.assertIsNone(request.func_tool.get_tool(FF14_MODEL_TOOL))
        self.assertEqual(tuple(self.context.config_umos), before_umos)

        filtered = self.manager.get_full_tool_set()
        filtered.remove_tool(FF14_MODEL_TOOL)
        request = SimpleNamespace(func_tool=filtered, conversation=None)
        await publisher.scope_request(event, request)
        self.assertIsNone(request.func_tool.get_tool(FF14_MODEL_TOOL))

        class Personas:
            async def resolve_selected_persona(self, **_kwargs):
                return None, {"tools": ["ff14_market_query"]}, None, None

        handle.tool.active = True
        self.context.persona_manager = Personas()
        self.context.config = {
            "subagent_orchestrator": {},
            "provider_settings": {},
            "agent_runner": {"config": {"persona": {}}},
        }
        request = SimpleNamespace(
            func_tool=self.host.ToolSet(),
            conversation=SimpleNamespace(persona_id=None),
        )
        await publisher.scope_request(event, request)
        scoped = request.func_tool.get_tool(FF14_MODEL_TOOL)
        self.assertEqual(
            tuple(self.context.config_umos),
            (*before_umos, event.unified_msg_origin),
        )
        self.assertEqual(
            scoped.parameters["properties"]["action"]["enum"], ["market_query"]
        )
        descriptor = handle.action_descriptors["market_query"]
        self.assertTrue(publisher.scoped_current(handle, "market_query", descriptor))
        self.assertTrue(self.runtime._tool_owner_current(handle))
        handle.tool.active = False
        self.assertFalse(publisher.scoped_current(handle, "market_query", descriptor))
        self.assertFalse(self.runtime._tool_owner_current(handle))

    async def test_legacy_inactive_action_survives_aggregate_upgrade_and_reactivation(
        self,
    ):
        await self.start()
        publisher = self.runtime._tool_publisher
        handle = next(
            item for item in publisher.handles if item.published_name == FF14_MODEL_TOOL
        )
        self.assertTrue(handle.tool.active)
        self.host.preferences.inactivated_llm_tools = ["ff14_market_query"]
        event = self.wrapper().context.event
        request = SimpleNamespace(
            func_tool=self.manager.get_full_tool_set(), conversation=None
        )
        await publisher.scope_request(event, request)
        self.assertEqual(
            request.func_tool.get_tool(FF14_MODEL_TOOL).parameters["properties"][
                "action"
            ]["enum"],
            ["market_select", "item_lookup", "item_select", "logs_character"],
        )

        class Personas:
            tools = None

            async def resolve_selected_persona(self, **_kwargs):
                return None, {"tools": self.tools}, None, None

        personas = Personas()
        self.context.persona_manager = personas
        self.context.config = {
            "subagent_orchestrator": {},
            "provider_settings": {},
            "agent_runner": {"config": {"persona": {}}},
        }
        for persona_tools, expected in (
            (
                None,
                ["market_select", "item_lookup", "item_select", "logs_character"],
            ),
            (["ff14_market_query", "ff14_item_lookup"], ["item_lookup"]),
        ):
            with self.subTest(persona_tools=persona_tools):
                personas.tools = persona_tools
                request = SimpleNamespace(
                    func_tool=self.host.ToolSet(),
                    conversation=SimpleNamespace(persona_id=None),
                )
                await publisher.scope_request(event, request)
                self.assertEqual(
                    request.func_tool.get_tool(FF14_MODEL_TOOL).parameters[
                        "properties"
                    ]["action"]["enum"],
                    expected,
                )

        self.host.preferences.inactivated_llm_tools = []
        live_wrapper = self.wrapper()
        live_event = live_wrapper.context.event
        request = SimpleNamespace(
            func_tool=self.manager.get_full_tool_set(), conversation=None
        )
        await publisher.scope_request(live_event, request)
        self.assertEqual(self.host.preferences.calls[-1], ("inactivated_llm_tools", ()))
        stale = request.func_tool.get_tool(FF14_MODEL_TOOL)
        self.assertIn("market_query", stale.parameters["properties"]["action"]["enum"])
        self.host.preferences.inactivated_llm_tools = ["ff14_market_query"]
        with self.assertRaisesRegex(PermissionError, "Host tool is inactive"):
            await stale.call(
                live_wrapper,
                action="market_query",
                arguments={"query": "44091"},
            )

        self.host.preferences.inactivated_llm_tools = []
        deactivated = []

        async def deactivate_during_request(_request):
            if not deactivated:
                deactivated.append(True)
                self.host.preferences.inactivated_llm_tools = ["ff14_market_query"]
            return None

        self.transport.callback = deactivate_during_request
        with self.assertRaisesRegex(PermissionError, "Host tool became inactive"):
            await stale.call(
                live_wrapper,
                action="market_query",
                arguments={"query": "44091", "region": "cn"},
            )
        self.assertEqual(deactivated, [True])
        self.transport.callback = None

        self.host.preferences.inactivated_llm_tools = [FF14_MODEL_TOOL]
        request = SimpleNamespace(
            func_tool=self.manager.get_full_tool_set(), conversation=None
        )
        await publisher.scope_request(self.wrapper().context.event, request)
        self.assertIsNone(request.func_tool.get_tool(FF14_MODEL_TOOL))

        self.host.preferences.inactivated_llm_tools = []
        request = SimpleNamespace(
            func_tool=self.manager.get_full_tool_set(), conversation=None
        )
        await publisher.scope_request(self.wrapper().context.event, request)
        self.assertIn(
            "market_query",
            request.func_tool.get_tool(FF14_MODEL_TOOL).parameters["properties"][
                "action"
            ]["enum"],
        )
        self.host.preferences.inactivated_llm_tools = "invalid"
        with self.assertRaisesRegex(
            PermissionError, "cannot prove Host inactive tool scope"
        ):
            await publisher.scope_request(
                self.wrapper().context.event,
                SimpleNamespace(
                    func_tool=self.manager.get_full_tool_set(), conversation=None
                ),
            )

        original_global_get = self.host.preferences.global_get

        async def unavailable(key, default):
            if key == "inactivated_llm_tools":
                raise RuntimeError("private preference failure")
            return await original_global_get(key, default)

        self.host.preferences.global_get = unavailable
        try:
            with self.assertRaisesRegex(
                PermissionError, "cannot prove Host inactive tool scope"
            ):
                await publisher.scope_request(
                    self.wrapper().context.event,
                    SimpleNamespace(
                        func_tool=self.manager.get_full_tool_set(), conversation=None
                    ),
                )
        finally:
            self.host.preferences.global_get = original_global_get
            self.host.preferences.inactivated_llm_tools = []

    async def test_controlled_model_chat_asks_and_continues_explicit_user_selection(
        self,
    ):
        await self.start()
        original = self.wrapper("Synthetic什么价")
        pending, question = await self.provider.chat(
            original, self.manager.get_full_tool_set()
        )
        self.assertEqual(pending["status"], "needs_selection")
        self.assertNotIn("user_confirmation", pending)
        self.assertIn("请选择候选", question)
        args = {key: pending["selection"][key] for key in ("batch_id", "generation")}
        self.assertEqual(
            (
                await self.call(
                    "ff14_market_select", {**args, "item_id": 44092}, original
                )
            )["status"],
            "error",
        )
        before = len(self.transport.requests)
        clarified = await self.call(
            "ff14_market_select", {**args, "action": "clarify"}, self.wrapper("哪个好?")
        )
        self.assertEqual(clarified["status"], "needs_selection")
        self.assertEqual(len(self.transport.requests), before)
        continued, reply = await self.provider.chat(
            self.wrapper("选择物品 44092"), self.manager.get_full_tool_set()
        )
        self.assertEqual(continued["market"]["item_id"], 44092)
        for field in ("scope", "quality", "intent", "core_revision", "module_revision"):
            self.assertEqual(continued["market"][field], pending["market"][field])
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
        for text in ("继续那个物品", "look it up", "物品44091多少钱", "44091"):
            result = await self.call(
                parameters={"query": "44091", "request_language": "unknown"},
                wrapper=self.wrapper(text),
            )
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["market"]["scope"]["default_basis"], "core_default")
        before = len(self.transport.requests)
        from yomihime_game_link_sdk.errors import ParameterError

        for query in (True, None, "x" * 121):
            with self.assertRaises(ParameterError):
                await self.call(
                    parameters={"query": query}, wrapper=self.wrapper("查相关物品")
                )
        for query in ("0", "2147483648", 'a"b', "Ore\x85"):
            denied = await self.call(
                parameters={"query": query}, wrapper=self.wrapper("查相关物品")
            )
            self.assertEqual(denied["status"], "error")
        self.assertEqual(len(self.transport.requests), before)

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
        pending = await self.call(parameters=parameters, wrapper=user)
        retry = await self.call(parameters=parameters, wrapper=user)
        self.assertEqual(pending["selection"], retry["selection"])
        before = len(self.transport.requests)
        denied = await self.call(
            parameters={
                **parameters,
                "query": "Synthetic Item 44092",
                "action": "correction",
            },
            wrapper=user,
        )
        self.assertEqual(denied["status"], "error")
        self.assertEqual(len(self.transport.requests), before)
        args = {key: pending["selection"][key] for key in ("batch_id", "generation")}
        clarified = await self.call(
            "ff14_market_select",
            {**args, "action": "clarify"},
            self.wrapper("刚才那个?"),
        )
        self.assertEqual(clarified["selection"], pending["selection"])
        selected = await self.call(
            "ff14_market_select", {**args, "item_id": 44092}, self.wrapper("第二个")
        )
        self.assertEqual(selected["status"], "success")
        for field in ("scope", "quality", "intent", "module_revision", "core_revision"):
            self.assertEqual(selected["market"][field], pending["market"][field])
        self.assertEqual(
            (
                await self.call(
                    "ff14_market_select",
                    {**args, "item_id": 44092},
                    self.wrapper("第二个"),
                )
            )["status"],
            "error",
        )
        fresh = await self.call(
            parameters=parameters, wrapper=self.wrapper("新查相同物品")
        )
        self.assertNotEqual(
            fresh["selection"]["generation"], pending["selection"]["generation"]
        )

    async def test_natural_name_chat_selection_preserves_context_prices_and_cache(self):
        await self.start()
        original = self.wrapper("Synthetic什么价")
        pending, _ = await self.provider.chat(
            original, self.manager.get_full_tool_set()
        )
        args = {key: pending["selection"][key] for key in ("batch_id", "generation")}
        args["item_id"] = 44091
        before = len(self.transport.requests)
        self.assertEqual(
            (await self.call("ff14_market_select", args, original))["status"], "error"
        )
        for text in ("不是", "两个都行", "哪个好", "刚才那个?"):
            result = await self.call(
                "ff14_market_select",
                {
                    **{key: args[key] for key in ("batch_id", "generation")},
                    "action": "clarify",
                },
                self.wrapper(text),
            )
            self.assertEqual(result["status"], "needs_selection")
        for identity in (
            {"sender": "other"},
            {"session": "other"},
            {"adapter": "other"},
        ):
            self.assertEqual(
                (
                    await self.call(
                        "ff14_market_select", args, self.wrapper("第一个", **identity)
                    )
                )["status"],
                "error",
            )
        self.assertEqual(len(self.transport.requests), before)
        selected = await self.call("ff14_market_select", args, self.wrapper("第一个"))
        self.assertEqual(selected["status"], "success")
        for field in ("scope", "quality", "intent", "core_revision", "module_revision"):
            self.assertEqual(selected["market"][field], pending["market"][field])
        self.assertFalse(selected["market"]["limitations"]["complete_world_coverage"])
        self.assertEqual(
            selected["market"]["time_semantics"]["minimum"], "world_upload"
        )
        self.assertEqual(
            (await self.call("ff14_market_select", args, self.wrapper("第一个")))[
                "status"
            ],
            "error",
        )

    async def test_bound_event_reset_after_exception_and_concurrent_owners(self):
        await self.start()
        handler = (
            self.runtime.core_runtime.registry.snapshot()
            .module("ff14/ff14")
            .handlers.capabilities["ff14.market.query"]
        )
        invoke = handler.invoke

        async def fail(*args):
            scope = await handler.services.scopes.bind(args[0])
            self.assertTrue((await scope.message.read()).event_ref)
            raise RuntimeError("synthetic handler failure")

        handler.invoke = fail
        try:
            with self.assertRaisesRegex(
                PermissionError, "Core did not authorize public ToolOutput"
            ):
                await self.call()
        finally:
            handler.invoke = invoke
        self.assertFalse(hasattr(handler, "_tool_event"))

        entered = asyncio.Event()
        release = asyncio.Event()
        seen = []

        async def observe(context, parameters):
            scope = await handler.services.scopes.bind(context)
            message = await scope.message.read()
            seen.append((context.actor_id, message.event_ref, message.text))
            if context.actor_id.endswith("user-17"):
                entered.set()
                await release.wait()
                self.assertEqual(await scope.message.read(), message)
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
        self.assertNotEqual(seen[0][1], seen[1][1])
        self.assertEqual(seen[0][2], seen[1][2])
        self.assertFalse(hasattr(handler, "_tool_event"))

    async def test_consumed_selection_flight_rejects_query_bypass_and_fences_new_item(
        self,
    ):
        from unittest.mock import patch

        from .test_market_integration import aggregated

        await self.start()
        handler = (
            self.runtime.core_runtime.registry.snapshot()
            .module("ff14/ff14")
            .handlers.capabilities["ff14.market.query"]
        )
        # Native Runtime uses a captured extension snapshot, while the controlled
        # receiver composition uses the checkout. Patch only this actual source
        # class, and restore it before leaving each selected flight.
        session_type = handler.source.start_bound.__func__.__globals__["MarketSession"]
        self.assertEqual(session_type.__module__, type(handler.source).__module__)
        original_fetch = session_type._fetch
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

                async def response(request):
                    if request.source_id == "xivapi_items":
                        return {
                            "results": [
                                {"row_id": 44093, "fields": {"Name": "Unrelated Item"}}
                            ]
                        }
                    if "/aggregated/" in request.path:
                        current_id = int(request.path.rsplit("/", 1)[-1])
                        payload = aggregated(
                            "China",
                            level="world" if current_id == item_id else "region",
                        )
                        payload["results"][0]["itemId"] = current_id
                        return payload
                    return None

                async def hold_fetched_result(session, path, parameters):
                    body = await original_fetch(session, path, parameters)
                    if "/aggregated/" in path and path.endswith(f"/{item_id}"):
                        # HTTP's real 10s timer and JSON decoding have finished.
                        # Hold an acquired result before the capability consumes
                        # it; this is a live capability flight, not in-flight HTTP.
                        entered.set()
                        await release.wait()
                    return body

                self.transport.callback = response
                confirmation = self.wrapper(f"Synthetic Item {item_id}")
                with patch.object(session_type, "_fetch", hold_fetched_result):
                    selected = asyncio.create_task(
                        self.call("ff14_market_select", args, confirmation)
                    )
                    try:
                        await asyncio.wait_for(entered.wait(), 3)
                        self.assertEqual(
                            [
                                r.path
                                for r in self.transport.requests
                                if r.path.endswith(f"/aggregated/90001/{item_id}")
                            ],
                            [f"/api/v2/aggregated/90001/{item_id}"],
                        )
                        before = len(self.transport.requests)
                        denials = []
                        for query in (f"Synthetic Item {item_id}", "Synthetic"):
                            denials.append(
                                self.call(
                                    parameters={
                                        "query": query,
                                        "region": "global",
                                        "quality": "nq",
                                    },
                                    wrapper=confirmation,
                                )
                            )
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
                            denials.append(
                                self.call(
                                    parameters={
                                        "action": "clarify",
                                    },
                                    wrapper=self.wrapper(text),
                                )
                            )
                        # All negatives share the same acquired-but-uncommitted
                        # result. Production HTTP and total deadlines stay intact.
                        values = await asyncio.gather(
                            *denials,
                            self.call("ff14_market_select", args, confirmation),
                        )
                        for denied in values[:-1]:
                            self.assertEqual(denied["status"], "error")
                            self.assertEqual(denied["error"]["code"], "parameter_error")
                        replay = values[-1]
                        self.assertEqual(replay["status"], "error")
                        self.assertEqual(replay["error"]["code"], "parameter_error")
                        self.assertEqual(len(self.transport.requests), before)
                        self.assertFalse(
                            selected.done(), "consumed flight must remain live"
                        )
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
                        await asyncio.gather(selected, return_exceptions=True)
                    result = selected.result()
                self.assertIs(session_type._fetch, original_fetch)
                if replace_query:
                    self.assertEqual(result["status"], "error")
                    self.assertEqual(result["error"]["code"], "parameter_error")
                    self.assertTrue(result["error"]["message"].startswith("候选已失效"))
                    self.assertNotIn("market", result)
                else:
                    self.assertEqual(result["status"], "success", result)
                    self.assertEqual(result["market"]["item_id"], item_id)
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
        pending = await self.call(
            parameters={"query": "Synthetic"}, wrapper=self.wrapper("查相关物品")
        )
        before = len(self.transport.requests)
        for text in ("不要", "哪个区别", "不确定"):
            result = await self.call(
                parameters={"action": "clarify"}, wrapper=self.wrapper(text)
            )
            self.assertEqual(result["selection"], pending["selection"])
        self.assertEqual(len(self.transport.requests), before)
        for identity in ({"sender": "absent"}, {"session": "absent"}):
            self.assertEqual(
                (
                    await self.call(
                        parameters={"action": "continue"},
                        wrapper=self.wrapper("第一个", **identity),
                    )
                )["status"],
                "error",
            )
        handler = (
            self.runtime.core_runtime.registry.snapshot()
            .module("ff14/ff14")
            .handlers.capabilities["ff14.market.query"]
        )
        handler.registry._clock = lambda: 10**20
        self.assertEqual(
            (
                await self.call(
                    parameters={"action": "clarify"}, wrapper=self.wrapper("哪个好")
                )
            )["status"],
            "error",
        )
        self.assertEqual(len(self.transport.requests), before)

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
                reset.append(hasattr(handler, "_tool_event"))

        self.transport.callback = block
        flight = asyncio.create_task(call_selection())
        try:
            await asyncio.wait_for(entered.wait(), 3)
            before = len(self.transport.requests)
            denied = await self.call(
                parameters={"action": "clarify"},
                wrapper=self.wrapper("第十个"),
            )
            self.assertEqual(denied["status"], "error")
            self.assertEqual(len(self.transport.requests), before)
            flight.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await flight
        finally:
            release.set()
        self.assertEqual(reset, [False])
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
            self.assertFalse(hasattr(handler, "_tool_event"))
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
        self.assertFalse(hasattr(handler, "_tool_event"))
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
        from yomihime_game_link_sdk.errors import AccessDenied

        await self.start()
        core = self.runtime.core_runtime
        handler = (
            core.registry.snapshot()
            .module("ff14/ff14")
            .handlers.capabilities["ff14.market.query"]
        )
        event = self.wrapper("物品ID 44091").context.event
        ingress = self.runtime._make_ingress(event, origin=InvocationOrigin.LLM_TOOL)
        self.assertFalse(hasattr(handler, "_bind_tool_event"))
        with self.assertRaises(AccessDenied):
            await core.invoke_tool(
                "ff14/ff14", "ff14_market_query", {"query": "44091"}, ingress=ingress
            )
        handle = next(
            h
            for h in self.runtime._tool_publisher.handles
            if h.published_name == FF14_MODEL_TOOL
        )
        # The missing owner closes its own receipt; a fresh official event is
        # the positive control for the independent actor-only forgery.
        with self.assertRaises(AccessDenied):
            self.runtime._make_ingress(
                event, origin=InvocationOrigin.LLM_TOOL, tool_handle=handle
            )
        event = self.wrapper("物品ID 44091").context.event
        valid = self.runtime._make_ingress(
            event, origin=InvocationOrigin.LLM_TOOL, tool_handle=handle
        )
        self.assertTrue(
            self.runtime._message_ingress.current(InvocationOrigin.LLM_TOOL, valid)
        )
        with self.assertRaises(AccessDenied):
            await core.invoke_tool(
                "ff14/ff14",
                "ff14_market_query",
                {"query": "44091"},
                ingress=__import__("dataclasses").replace(
                    valid, actor_id="test-platform-1:other"
                ),
            )
        self.assertEqual(self.transport.requests, [])
        for text in (
            "Synthetic Item 44091那个。",
            "就Synthetic Item 44091",
            "选择Synthetic Item 44091",
            "选Synthetic Item 44091",
        ):
            denied = await self.call(
                parameters={"action": "clarify"}, wrapper=self.wrapper(text)
            )
            self.assertEqual(denied["status"], "error")
            self.assertEqual(self.transport.requests, [])
        original = self.wrapper("Synthetic什么价")
        pending = await self.call(parameters={"query": "Synthetic"}, wrapper=original)
        args = {key: pending["selection"][key] for key in ("batch_id", "generation")}
        args["item_id"] = 44091
        before = len(self.transport.requests)
        for identity in (
            {"sender": "other"},
            {"session": "other"},
            {"adapter": "other"},
        ):
            denied = await self.call(
                "ff14_market_select", args, self.wrapper("第一个", **identity)
            )
            self.assertEqual(denied["status"], "error")
        self.assertEqual(
            (await self.call("ff14_market_select", args, original))["status"], "error"
        )
        handler.registry._clock = lambda: 10**20
        for parameters in ({"action": "clarify"}, {"action": "continue"}):
            self.assertEqual(
                (await self.call(parameters=parameters, wrapper=self.wrapper("那个")))[
                    "status"
                ],
                "error",
            )
        denied = await self.call("ff14_market_select", args, self.wrapper("第一个"))
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
        publisher = self.runtime._tool_publisher
        wrapper = self.wrapper()
        request = SimpleNamespace(
            func_tool=self.manager.get_full_tool_set(), conversation=None
        )
        await publisher.scope_request(wrapper.context.event, request)
        captured = request.func_tool.get_tool(FF14_MODEL_TOOL)
        self.assertIsNotNone(captured)
        self.assertEqual(
            (await self.call_captured_parent(captured, wrapper))["status"], "success"
        )
        self.transport.requests.clear()
        replacement = self.host.FunctionTool(
            name="ff14_market_query",
            description="other owner",
            parameters={"type": "object"},
        )
        self.manager.func_list[0] = replacement
        with self.assertRaises(PermissionError):
            await self.call_captured_parent(captured, wrapper)
        self.assertEqual(self.transport.requests, [])
        real_cleanup = publisher.cleanup
        publisher.cleanup = lambda: (_ for _ in ()).throw(
            ToolPublicationError("synthetic pending")
        )
        try:
            with self.assertRaises(CoreRuntimeCleanupPending) as pending:
                await self.runtime.terminate()
            self.assertEqual(pending.exception.component, "tool_publication")
            with self.assertRaises(PermissionError):
                await self.call_captured_parent(captured, wrapper)
            self.assertIs(self.runtime._tool_publisher, publisher)
            self.assertFalse(core._accepting)
            self.assertIsNone(self.runtime.core_runtime)
            await self.assert_core_closed(core, module)
            self.assertTrue(self.transport.closed)
            self.assertEqual(self.transport.close_calls, 1)
        finally:
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
            return validate_contract(
                CapabilityResult(
                    "private",
                    ResultStatus.ERROR,
                    privacy=Privacy.PRIVATE,
                    error=validate_contract(
                        ErrorDetail(ErrorCode.NOT_FOUND, "private internal")
                    ),
                )
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
            if count == 1:
                raise RuntimeError("synthetic registration failure")

        self.context.add_llm_tools = partial
        with self.assertRaises(RuntimeError):
            await self.runtime.initialize()
        self.assertEqual(count, 1)
        self.assertEqual(self.manager.func_list, [])
        self.assertIsNone(self.runtime.core_runtime)
        self.context.add_llm_tools = original
        await self.runtime.initialize()
        self.assertEqual(len(self.manager.func_list), 1)

    async def test_module_stop_revoke_and_pending_observer_cleanup(self):
        await self.start()
        publisher = self.runtime._tool_publisher
        wrapper = self.wrapper()
        request = SimpleNamespace(
            func_tool=self.manager.get_full_tool_set(), conversation=None
        )
        await publisher.scope_request(wrapper.context.event, request)
        captured = request.func_tool.get_tool(FF14_MODEL_TOOL)
        self.assertIsNotNone(captured)
        self.assertEqual(
            (await self.call_captured_parent(captured, wrapper))["status"], "success"
        )
        self.transport.requests.clear()
        original = publisher.cleanup
        publisher.cleanup = lambda: (_ for _ in ()).throw(
            ToolPublicationError("synthetic pending")
        )
        try:
            await self.runtime.core_runtime.lifecycle.stop("ff14/ff14")
            self.assertTrue(all(handle.revoked for handle in publisher.handles))
            with self.assertRaises(PermissionError):
                await self.call_captured_parent(captured, wrapper)
            self.assertEqual(self.transport.requests, [])
            self.assertEqual(publisher.failure, "ToolPublicationError")
        finally:
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
        try:
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
            captured = self.manager.get_full_tool_set().get_tool(FF14_MODEL_TOOL)
            self.assertIsNotNone(captured)
            self.assertIs(captured._wrapped, publisher.handles[0].tool)
            with self.assertRaises(PermissionError):
                await self.call_captured_parent(captured, self.wrapper())
            self.assertEqual(self.transport.requests, [])
        finally:
            if "publisher" in retained:
                retained["publisher"].cleanup = retained["cleanup"]
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
        from yomihime_game_link_sdk.services import HttpResponse

        from .test_market_integration import aggregated

        async def partial(request):
            if "/aggregated/" not in request.path:
                return None
            region = request.path.split("/")[-2]
            if region == "Europe":
                return validate_contract(HttpResponse(503, {}, b"{}"))
            return validate_contract(
                HttpResponse(200, {}, json.dumps(aggregated(region)).encode())
            )

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
                return validate_contract(
                    HttpResponse(200, {}, b'{"results":[],"failedItems":[]}')
                )
            return None

        self.transport.callback = empty
        no_data = await self.call(
            parameters={"query": "44092", "region": "cn"},
            wrapper=self.wrapper("物品ID 44092多少钱"),
        )
        self.assertEqual(no_data["status"], "error")
        self.assertEqual(no_data["error"]["code"], "no_records")
        self.assertNotIn("market", no_data)
        self.assertEqual(
            no_data["supplement"]["market"]["coverage"][0]["state"], "empty"
        )
        self.assertEqual(no_data["supplement"]["market"]["minimums"], [])


if __name__ == "__main__":
    unittest.main()
