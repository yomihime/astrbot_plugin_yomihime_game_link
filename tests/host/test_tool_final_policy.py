"""Real Host tools and message receipts around the final asynchronous policy read.

Only the registry and business result are synthetic; no SQLite, provider, network,
or Host startup is needed to verify receipt expiry and request isolation.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from dataclasses import replace
from types import SimpleNamespace

from ygl_test_subject.adapters.astrbot.runtime import (
    AstrBotRuntime,
    _AstrBotMessageIngress,
    _MessageReceipts,
)
from ygl_test_subject.adapters.astrbot.tool_publisher import AstrBotToolPublisher
from ygl_test_subject.core.contracts.services import ToolOutput
from ygl_test_subject.services.core_runtime import HostIngressUnavailable
from ygl_test_subject.services.output import OutputStatus

from tests.core.test_invocation import _capability, _package
from yomihime_game_link_sdk.errors import OperationTimeout
from yomihime_game_link_sdk.results import FactDocument

from .tool_contract import tool_contracts


class _Harness:
    def __init__(self, host):
        self.host = host
        self.clock = [0.0]
        module_manifest = _package(_capability(), aggregate=True).modules[0]
        module_manifest = replace(
            module_manifest,
            tools=(
                module_manifest.tools[0],
                replace(module_manifest.tools[0], name="record_other", action="other"),
            ),
        )
        module = SimpleNamespace(
            module_id="records/records", epoch=1, enabled=True, manifest=module_manifest
        )
        snapshot = SimpleNamespace(
            modules={module.module_id: module}, revision=1, module=lambda _: module
        )
        registry = SimpleNamespace(
            snapshot=lambda: snapshot,
            is_active=lambda value: value is module,
            observe=lambda _: lambda: None,
        )
        manager = host.Manager.__new__(host.Manager)
        manager.func_list = []
        self.manager = manager

        class Context(host.Context):
            def __init__(self):
                self.provider_manager = SimpleNamespace(llm_tools=manager)
                self.global_config = {"subagent_orchestrator": {}}
                self.subagent_orchestrator = SimpleNamespace(handoffs=[])

            def get_config(self, *, umo=None):
                return self.global_config if umo is None else {"provider_settings": {}}

            def get_llm_tool_manager(self):
                return manager

        self.context = Context()
        self.calls = []
        self.unavailable = False

        async def core_invoke(module_id, target, arguments, *, ingress, **_kwargs):
            self.calls.append((module_id, target, arguments, ingress))
            if self.unavailable:
                raise HostIngressUnavailable()
            return SimpleNamespace(
                output=SimpleNamespace(
                    status=OutputStatus.TOOL_RESULT,
                    result=ToolOutput(FactDocument({"status": "synthetic_success"})),
                )
            )

        core = SimpleNamespace(registry=registry, invoke_tool=core_invoke)
        runtime = AstrBotRuntime.__new__(AstrBotRuntime)
        runtime._ready = True
        runtime._closing = False
        runtime._core = core
        runtime._message_receipts = _MessageReceipts()
        runtime._message_receipts._clock = lambda: self.clock[0]
        runtime._message_ingress = _AstrBotMessageIngress(
            object(), runtime._message_receipts, runtime._tool_owner_current
        )
        publisher = AstrBotToolPublisher(self.context, core, runtime.invoke_tool)
        runtime._tool_publisher = publisher
        publisher.publish()
        self.publisher = publisher
        self.runtime = runtime
        self.handle = publisher.handles[0]
        host.provider_register.llm_tools = manager
        host.tool_exec.llm_tools = manager

    def wrapper(self, session="conversation-a"):
        event = self.host.Event()
        event.message_str = "original synthetic request"
        event.role = "member"
        event.platform_meta = SimpleNamespace(id="adapter", name="platform")
        event.message_obj = SimpleNamespace(
            sender=SimpleNamespace(user_id="member"),
            type=self.host.MessageType.GROUP_MESSAGE,
        )
        event.session = SimpleNamespace(
            session_id=session, message_type=self.host.MessageType.GROUP_MESSAGE
        )
        event.unified_msg_origin = f"adapter:{session}"
        return self.host.ContextWrapper(
            context=self.host.AstrAgentContext(context=self.context, event=event)
        )

    async def scoped(self, wrapper):
        request = SimpleNamespace(
            func_tool=self.manager.get_full_tool_set(), conversation=None
        )
        await self.publisher.scope_request(wrapper.context.event, request)
        return request.func_tool.get_tool("records")


class FinalToolPolicyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.host = self.enterContext(tool_contracts())
        self.harness = _Harness(self.host)

    async def _call_with_final_transition(self, transition):
        harness = self.harness
        wrapper = harness.wrapper()
        scoped = await harness.scoped(wrapper)
        original = harness.publisher._inactive_tool_names
        reads = 0

        async def final_read():
            nonlocal reads
            reads += 1
            inactive = await original()
            if reads == 2:
                await asyncio.sleep(0)
                transition(wrapper)
            return inactive

        harness.publisher._inactive_tool_names = final_read
        try:
            return await scoped.call(wrapper, action="lookup", arguments={"id": "one"})
        finally:
            self.assertEqual(reads, 2)
            self.assertEqual(len(harness.calls), 1)
            harness.publisher._inactive_tool_names = original

    async def test_valid_message_returns_authorized_facts(self):
        result = await self._call_with_final_transition(lambda _wrapper: None)
        self.assertEqual(json.loads(result), {"status": "synthetic_success"})

    async def test_final_policy_wait_cannot_outlive_original_receipt(self):
        for unavailable in (False, True):
            with self.subTest(unavailable=unavailable):
                self.harness = _Harness(self.host)
                self.harness.unavailable = unavailable

                def expire(_wrapper):
                    self.harness.clock[0] = 301

                with self.assertRaises(OperationTimeout):
                    await self._call_with_final_transition(expire)

    async def test_message_change_during_final_policy_wait_is_rejected(self):
        def change(wrapper):
            wrapper.context.event.message_str = "a different request"

        with self.assertRaisesRegex(PermissionError, "Host message unavailable"):
            await self._call_with_final_transition(change)

    async def test_request_context_changes_during_final_policy_wait_are_rejected(self):
        for target in ("event", "agent_context", "host_context"):
            with self.subTest(target=target):
                self.harness = _Harness(self.host)

                def change(wrapper):
                    replacement = self.harness.wrapper("conversation-b")
                    if target == "event":
                        wrapper.context.event = replacement.context.event
                    elif target == "agent_context":
                        wrapper.context = replacement.context
                    else:
                        wrapper.context.context = object()

                with self.assertRaisesRegex(PermissionError, "request context changed"):
                    await self._call_with_final_transition(change)

    async def test_plugin_removal_during_final_policy_wait_is_rejected(self):
        def change(wrapper):
            wrapper.context.event.plugins_name = []

        with self.assertRaisesRegex(PermissionError, "plugin scope"):
            await self._call_with_final_transition(change)

    async def test_valid_unavailable_result_remains_controlled(self):
        self.harness.unavailable = True
        result = await self._call_with_final_transition(lambda _wrapper: None)
        self.assertEqual(json.loads(result)["error"]["code"], "module_unavailable")

    def _handoff_request(self):
        harness = self.harness
        handoff = self.host.HandoffTool(
            self.host.Agent(name="limited", tools=["record_query"])
        )
        harness.context.subagent_orchestrator.handoffs = [handoff]
        harness.context.global_config = {
            "subagent_orchestrator": {
                "main_enable": True,
                "remove_main_duplicate_tools": True,
                "agents": [{"tools": ["record_query"]}],
            }
        }
        request = SimpleNamespace(
            func_tool=self.host.ToolSet([handoff]), conversation=None
        )
        return handoff, request

    async def test_same_event_handoff_reprojection_preserves_legacy_subset(self):
        handoff, request = self._handoff_request()
        wrapper = self.harness.wrapper()
        for _ in range(2):
            await self.harness.publisher.scope_request(wrapper.context.event, request)
            clone = request.func_tool.get_tool(handoff.name)
            self.assertIs(clone._ygl_event, wrapper.context.event)
            toolset = self.host.FunctionToolExecutor._build_handoff_toolset(
                wrapper, clone.agent.tools
            )
            tool = toolset.get_tool("records")
            self.assertEqual(
                tool.parameters["properties"]["action"]["enum"], ["lookup"]
            )
        result = await tool.call(wrapper, action="lookup", arguments={"id": "one"})
        self.assertEqual(json.loads(result), {"status": "synthetic_success"})

    async def test_handoff_from_previous_event_is_not_rebound(self):
        for policy_failure in (False, True):
            with self.subTest(policy_failure=policy_failure):
                self.harness = _Harness(self.host)
                handoff, request = self._handoff_request()
                wrapper_a = self.harness.wrapper()
                wrapper_b = self.harness.wrapper("conversation-b")
                await self.harness.publisher.scope_request(
                    wrapper_a.context.event, request
                )
                old_clone = request.func_tool.get_tool(handoff.name)
                if policy_failure:

                    async def fail():
                        raise PermissionError("policy unavailable")

                    self.harness.publisher._inactive_tool_names = fail
                    with self.assertRaisesRegex(PermissionError, "policy unavailable"):
                        await self.harness.publisher.scope_request(
                            wrapper_b.context.event, request
                        )
                else:
                    await self.harness.publisher.scope_request(
                        wrapper_b.context.event, request
                    )
                self.assertIs(old_clone._ygl_event, wrapper_a.context.event)
                self.assertIsNone(request.func_tool.get_tool(handoff.name))
                self.assertEqual(self.harness.calls, [])
