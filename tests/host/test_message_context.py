"""Controlled AstrBot receiver replay + real offline Core/FF14, not Runtime.start.

The fixed Host AST classes and synthetic HTTP/provider fixtures are explicit.
No AstrBotRuntime readiness flags, native discovery gates, or SDK proof are faked.
"""

from __future__ import annotations

import asyncio
import dataclasses
import gc
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from ygl_test_subject.adapters.astrbot.runtime import (
    _AstrBotMessageIngress,
    _MessageReceipts,
)
from ygl_test_subject.adapters.astrbot.tool_publisher import AstrBotToolPublisher
from ygl_test_subject.core.invocation import Gateway
from ygl_test_subject.core.lifecycle import _ServiceLifetime
from ygl_test_subject.extensions.disk_manifest import parse_manifest
from ygl_test_subject.modules.ff14.module import Factory
from ygl_test_subject.services.configuration import ConfigurationService
from ygl_test_subject.services.core_configuration import (
    CORE_CONFIG_FIELDS,
    CoreDefaultsView,
    core_config_target,
)
from ygl_test_subject.services.core_runtime import _HostIngressAuthority
from ygl_test_subject.services.identity import InvocationPrincipalResolver
from ygl_test_subject.services.output import (
    LifecycleApprovedSendScheduler,
    OutputService,
)

import yomihime_game_link_sdk as ygl
from tests.fixtures.b03_runtime import _Collector, _Handler, build_runtime
from tests.services import test_output as outlet

from . import test_llm_tools as legacy
from .test_market_integration import FixtureTransport
from .tool_contract import tool_contracts

ROOT = Path(__file__).resolve().parents[2]


class _OfflineHost:
    """Trusted controlled composition delegates the production receiver and ports."""

    def __init__(self, runtime, context, receiver, authority, gateway, output):
        self.core_runtime = self
        self.runtime, self.registry, self.lifecycle = (
            runtime,
            runtime.registry,
            runtime.lifecycle,
        )
        self.receiver, self.authority, self.gateway, self.output = (
            receiver,
            authority,
            gateway,
            output,
        )
        self.accepting = False
        self.publisher = AstrBotToolPublisher(context, self, self.invoke_tool)
        self._tool_publisher = self.publisher

    def current_owner(self, handle):
        return (
            self.accepting
            and any(h is handle for h in self.publisher.handles)
            and self.publisher._current(handle)
        )

    def require_accepting(self):
        if not self.accepting:
            raise ygl.InvalidInvocation()

    def _make_ingress(
        self, event, *, origin=ygl.InvocationOrigin.COMMAND, tool_handle=None
    ):
        return self.receiver.make(event, origin=origin, tool_handle=tool_handle)

    def _validate_ingress(self, origin, ingress):
        return self.receiver.validate(origin, ingress)

    async def invoke_tool(
        self,
        module_id,
        tool_name,
        parameters,
        event=None,
        *,
        ingress=None,
        tool_handle=None,
    ):
        self.require_accepting()
        if ingress is None:
            if (
                tool_handle is None
                or tool_handle.module_id != module_id
                or tool_handle.descriptor.name != tool_name
            ):
                raise PermissionError("Host tool ingress unavailable")
            ingress = self.receiver.make(
                event, origin=ygl.InvocationOrigin.LLM_TOOL, tool_handle=tool_handle
            )
        accepted = await self.authority.validate(ygl.InvocationOrigin.LLM_TOOL, ingress)
        snapshot = self.registry.snapshot()
        module = snapshot.module(module_id)
        capability = next(
            t.capability_id for t in module.manifest.tools if t.name == tool_name
        )
        view = self.runtime.issuer.issue(
            origin=ygl.InvocationOrigin.LLM_TOOL,
            module_id=module_id,
            module_epoch=module.epoch,
            registry_revision=snapshot.revision,
            actor_id=ingress.actor_id,
            conversation_id=ingress.conversation_id,
            adapter_id=ingress.adapter_id,
            capability_id=capability,
            deadline=self.runtime.issuer._clock() + 30,
        )
        try:
            self.lifecycle.admission.admit(view, capability)
            self.authority.attach_message(self.runtime.issuer, view, accepted)
            result = await self.gateway.invoke_tool(view, tool_name, parameters)
            self.runtime.issuer._require_message_current(view)
            routed = await self.output.route(view, result)
            self.runtime.issuer._require_message_current(view)
            if not self.receiver.current(ygl.InvocationOrigin.LLM_TOOL, ingress):
                raise PermissionError("Host message unavailable")
            return SimpleNamespace(result=result, output=routed)
        finally:
            self.runtime.issuer.release(view)


class ControlledMessageTests(unittest.IsolatedAsyncioTestCase):
    wrapper = legacy.LLMToolTests.wrapper
    call = legacy.LLMToolTests.call

    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="s2a-c-",
        )
        self.root = Path(self.temporary.name)

    async def start(self):
        if hasattr(self, "runtime"):
            return
        self.contracts = tool_contracts()
        self.host = host = self.contracts.__enter__()

        class Context(host.Context, legacy.fixture._Context):
            def __init__(self):
                legacy.fixture._Context.__init__(self)
                manager = host.Manager.__new__(host.Manager)
                manager.func_list = []
                self.provider_manager = SimpleNamespace(llm_tools=manager)

        self.context = Context()
        self.transport = FixtureTransport()
        runtime = await build_runtime(self.root / "core")
        self.b03 = runtime
        package = parse_manifest(
            (ROOT / "modules/ff14/yomihime.manifest.json").read_bytes()
        )
        manifest = package.modules[0]
        # Dormant declarations only. Actual Factory/Lifecycle replace these
        # placeholders before opening the composition readiness gate.
        runtime.registry.register_package(
            package,
            {
                "ff14": ygl.ModuleHandlers(
                    {
                        c.capability_id: _Handler(c.capability_id)
                        for c in manifest.capabilities
                    },
                    {c.collector_id: _Collector() for c in manifest.schedules},
                    {
                        s.matcher_id: SimpleNamespace(evaluate=lambda *a: None)
                        for s in manifest.subscriptions
                    },
                )
            },
        )
        runtime.services._http_transport = self.transport
        defaults = ConfigurationService(
            core_config_target("host-config"),
            CORE_CONFIG_FIELDS,
            runtime.repositories.config,
            runtime.repositories.secret_store,
        )
        runtime.services._core_defaults = CoreDefaultsView(defaults)
        lifetime = _ServiceLifetime(("ff14", "ff14/ff14", "c-ff14-install"))
        candidate_services = runtime.services.for_candidate(
            "ff14/ff14", manifest, service_lifetime=lifetime
        )
        instance = await Factory().create(candidate_services)
        handlers = runtime.lifecycle.adopt_candidate(
            "ff14", manifest, "c-ff14-install", instance, service_lifetime=lifetime
        )
        runtime.lifecycle.install_dormant(
            "ff14", "ff14/ff14", "c-ff14-install", instance, handlers
        )
        identity, _ = await runtime.lifecycle.start_candidate(
            "ff14/ff14", "c-ff14-start"
        )
        await runtime.health.prepare("ff14/ff14", manifest)
        runtime.lifecycle.publish_committed_intent(
            "ff14/ff14",
            "c-ff14-start",
            identity,
            True,
            runtime.registry.snapshot().revision,
        )
        gateway = Gateway(
            runtime.registry,
            runtime.issuer,
            admission=runtime.lifecycle.admission,
            lifecycle=runtime.lifecycle,
        )
        from ygl_test_subject.infrastructure.sqlite.repositories_output import (
            SQLiteRootOutputRepository,
        )
        from ygl_test_subject.infrastructure.sqlite.repositories_subscriptions import (
            SQLiteDeliveryRepository,
            SQLiteSubscriptionStore,
        )

        output = OutputService(
            issuer=runtime.issuer,
            admission=runtime.lifecycle.admission,
            send_scheduler=LifecycleApprovedSendScheduler(runtime.lifecycle),
            registry=runtime.registry,
            renderer=outlet._Renderer(),
            limits=ygl.DisplayLimits(2, 1024),
            conversations=outlet._Conversations(),
            message_port=outlet._MessagePort(),
            deliveries=SQLiteDeliveryRepository(runtime.database),
            grants=runtime.repositories.grant_revocation,
            subscriptions=SQLiteSubscriptionStore(runtime.database),
            root_outputs=SQLiteRootOutputRepository(runtime.database),
            resource_visibility=outlet._ResourceVisibility(runtime.database),
            principal_resolver=InvocationPrincipalResolver(
                runtime.issuer,
                runtime.repositories.identities,
                identity_namespace="test-users",
                admission=runtime.lifecycle.admission,
            ),
            claim_lease=timedelta(minutes=1),
            send_timeout=5,
        )
        receipts = _MessageReceipts()
        self.receipts = receipts
        owner = {}
        receiver = _AstrBotMessageIngress(
            object(), receipts, lambda handle: owner["host"].current_owner(handle)
        )
        authority = _HostIngressAuthority(
            receiver.validate,
            receiver.current,
            lambda: owner["host"].require_accepting(),
        )
        self.runtime = _OfflineHost(
            runtime, self.context, receiver, authority, gateway, output
        )
        owner["host"] = self.runtime
        self.runtime.accepting = True
        self.runtime.publisher.publish()
        self.manager = self.context.provider_manager.llm_tools
        self.provider = legacy.ControlledProvider()

    async def asyncTearDown(self):
        async def stop_owned(modules):
            if modules:
                try:
                    await self.b03.lifecycle.stop(modules[0])
                finally:
                    await stop_owned(modules[1:])

        try:
            try:
                if hasattr(self, "runtime"):
                    self.runtime.accepting = False
                    self.receipts.close()
                    self.runtime.publisher.revoke()
            finally:
                if hasattr(self, "b03"):
                    owned = ["sample/alpha", "sample/beta"]
                    if "ff14/ff14" in self.b03.lifecycle._states:
                        owned.insert(0, "ff14/ff14")
                    try:
                        await stop_owned(owned)
                    finally:
                        try:
                            await self.b03.services.close_credentials()
                        finally:
                            await self.b03.database.executor.close(timeout=2)
        finally:
            try:
                if hasattr(self, "contracts"):
                    self.contracts.__exit__(None, None, None)
            finally:
                self.temporary.cleanup()

    async def test_s2a_controlled_host_to_real_core_and_ff14(self):
        await self.start()
        facts = await self.call()
        self.assertEqual(facts["status"], "success")
        self.assertEqual(facts["market"]["item_id"], 44091)
        handler = (
            self.runtime.registry.snapshot()
            .module("ff14/ff14")
            .handlers.capabilities["ff14.market.query"]
        )
        self.assertFalse(hasattr(handler, "_bind_tool_event"))
        self.assertFalse(hasattr(handler, "_tool_event"))
        self.assertFalse(self.b03.issuer._issued)

    async def test_s2a_receipt_same_object_distinct_equal_messages_and_nonpersistent_ids(
        self,
    ):
        await self.start()
        h = self.runtime.publisher.handles[0]
        first, equal = (
            self.wrapper("同文").context.event,
            self.wrapper("同文").context.event,
        )
        first.message_obj.message_id = equal.message_obj.message_id = "same-platform-id"

        def ingress(event):
            return self.runtime.receiver.make(
                event, origin=ygl.InvocationOrigin.LLM_TOOL, tool_handle=h
            )

        a, b, c = ingress(first), ingress(first), ingress(equal)
        self.assertEqual(a.message_correlation, b.message_correlation)
        self.assertNotEqual(a.message_correlation, c.message_correlation)
        self.assertIs(a.evidence.receipt.event(), first)
        self.assertNotIn("同文", repr(a.evidence.receipt))
        self.assertTrue(self.runtime.receiver.current(ygl.InvocationOrigin.LLM_TOOL, a))

    async def test_s2a_receipt_expiry_tombstone_capacity_gc_and_no_refresh(self):
        await self.start()
        now = [0.0]
        self.receipts._clock = lambda: now[0]
        h = self.runtime.publisher.handles[0]
        events = [self.wrapper(str(i)).context.event for i in range(128)]

        def capture(event):
            return self.runtime.receiver.make(
                event, origin=ygl.InvocationOrigin.LLM_TOOL, tool_handle=h
            )

        first = capture(events[0])
        for event in events[1:]:
            capture(event)
        with self.assertRaises(ygl.ServiceUnavailable):
            capture(self.wrapper("overflow").context.event)
        now[0] = 299
        self.assertEqual(
            capture(events[0]).message_correlation, first.message_correlation
        )
        now[0] = 300
        with self.assertRaises(ygl.OperationTimeout):
            self.runtime.receiver.current(ygl.InvocationOrigin.LLM_TOOL, first)
        self.assertIsNone(first.evidence.receipt.text)
        self.assertEqual(len(self.receipts._records), 128)
        with self.assertRaises(ygl.OperationTimeout):
            capture(events[0])
        with self.assertRaises(ygl.ServiceUnavailable):
            capture(self.wrapper("still-full").context.event)
        removed = events.pop(1)
        key = id(removed)
        del removed
        gc.collect()
        self.assertNotIn(key, self.receipts._records)
        added = self.wrapper("gc-slot").context.event
        capture(added)
        self.assertEqual(len(self.receipts._records), 128)

    async def test_s2a_receipt_mutation_owner_replacement_and_close_fail_closed(self):
        await self.start()
        h = self.runtime.publisher.handles[0]
        for mutation in ("text", "actor", "session", "adapter", "kind"):
            with self.subTest(mutation=mutation):
                event = self.wrapper("canary").context.event
                ingress = self.runtime.receiver.make(
                    event, origin=ygl.InvocationOrigin.LLM_TOOL, tool_handle=h
                )
                if mutation == "text":
                    event.message_str = "changed"
                elif mutation == "actor":
                    event.message_obj.sender.user_id = "other"
                elif mutation == "session":
                    event.session.session_id = "other"
                elif mutation == "adapter":
                    event.platform_meta.id = "other"
                else:
                    event.message_obj.type = self.host.MessageType.FRIEND_MESSAGE
                self.assertFalse(
                    self.runtime.receiver.current(
                        ygl.InvocationOrigin.LLM_TOOL, ingress
                    )
                )
                self.assertIsNone(ingress.evidence.receipt.text)
        event = self.wrapper().context.event
        ingress = self.runtime.receiver.make(
            event, origin=ygl.InvocationOrigin.LLM_TOOL, tool_handle=h
        )
        replacement = self.host.FunctionTool(
            name=h.tool.name, description="other owner", parameters={"type": "object"}
        )
        index = self.manager.func_list.index(h.tool)
        self.manager.func_list[index] = replacement
        self.assertFalse(
            self.runtime.receiver.current(ygl.InvocationOrigin.LLM_TOOL, ingress)
        )
        self.manager.func_list[index] = h.tool
        self.receipts.close()
        self.assertFalse(
            self.runtime.receiver.current(ygl.InvocationOrigin.LLM_TOOL, ingress)
        )
        self.assertEqual(len(self.receipts._records), 0)
        self.assertIsNone(ingress.evidence.receipt.text)

    async def test_s2a_receipt_bounded_text_unweakrefable_and_threaded_retry(self):
        await self.start()
        h = self.runtime.publisher.handles[0]
        for text in ("x" * 16385, "\ud800"):
            with self.subTest(size=len(text)), self.assertRaises(ygl.ParameterError):
                self.runtime.receiver.make(
                    self.wrapper(text).context.event,
                    origin=ygl.InvocationOrigin.LLM_TOOL,
                    tool_handle=h,
                )

        class NoWeak:
            __slots__ = ()

            def get_message_str(self):
                return "bounded"

        with self.assertRaises(ygl.ServiceUnavailable):
            self.receipts.capture(NoWeak(), ("a", "b", "c", ygl.ConversationKind.GROUP))
        event = self.wrapper("shared").context.event
        from concurrent.futures import ThreadPoolExecutor

        def capture(_):
            return self.runtime.receiver.make(
                event, origin=ygl.InvocationOrigin.LLM_TOOL, tool_handle=h
            ).message_correlation

        with ThreadPoolExecutor(max_workers=4) as workers:
            values = list(workers.map(capture, range(16)))
        self.assertEqual(len(set(values)), 1)

    async def test_s2a_ff14_source_change_after_catalog_and_market_await_has_no_publish(
        self,
    ):
        await self.start()
        handler = (
            self.runtime.registry.snapshot()
            .module("ff14/ff14")
            .handlers.capabilities["ff14.market.query"]
        )
        for stage in ("catalog", "item", "market"):
            with self.subTest(stage=stage):
                wrapper = self.wrapper(
                    "Synthetic什么价" if stage != "market" else "物品ID 44091",
                    sender=stage,
                )
                event = wrapper.context.event
                effects = []
                publish, finish = handler.registry.publish, handler.registry.finish

                def record_publish(*args, **kwargs):
                    effects.append("publish")
                    return publish(*args, **kwargs)

                def record_finish(*args, **kwargs):
                    effects.append("finish")
                    return finish(*args, **kwargs)

                handler.registry.publish, handler.registry.finish = (
                    record_publish,
                    record_finish,
                )

                async def invalidate(request):
                    matches = (
                        request.path == "/api/v2/worlds"
                        if stage == "catalog"
                        else request.source_id == "xivapi_items"
                        if stage == "item"
                        else "/aggregated/" in request.path
                    )
                    if matches:
                        effects.clear()
                        event.message_str = "changed-canary"
                    return None

                self.transport.callback = invalidate
                try:
                    with self.assertRaises(PermissionError):
                        await self.call(
                            parameters={
                                "query": "44091" if stage == "market" else "Synthetic"
                            },
                            wrapper=wrapper,
                        )
                    self.assertEqual(effects, [])
                finally:
                    self.transport.callback = None
                    handler.registry.publish, handler.registry.finish = publish, finish

    def _r2_safe_failure(self, error, expected):
        self.assertIs(type(error), expected)
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        for marker in ("R2-CANARY", "private-getter-canary"):
            self.assertNotIn(marker, str(error))

    def _r2_cleared(self, receipt):
        self.assertTrue(receipt.closed)
        self.assertIsNone(receipt.text)
        self.assertFalse(receipt.facts)
        self.assertFalse(receipt.correlation)

    async def _r2_issued_read(self, event, *, tool_name="ff14_market_query"):
        handle = next(
            h for h in self.runtime.publisher.handles if h.descriptor.name == tool_name
        )
        ingress = self.runtime.receiver.make(
            event, origin=ygl.InvocationOrigin.LLM_TOOL, tool_handle=handle
        )
        accepted = await self.runtime.authority.validate(
            ygl.InvocationOrigin.LLM_TOOL, ingress
        )
        snapshot = self.b03.registry.snapshot()
        module = snapshot.module(handle.module_id)
        view = self.b03.issuer.issue(
            origin=ygl.InvocationOrigin.LLM_TOOL,
            module_id=module.module_id,
            module_epoch=module.epoch,
            registry_revision=snapshot.revision,
            actor_id=ingress.actor_id,
            conversation_id=ingress.conversation_id,
            adapter_id=ingress.adapter_id,
            capability_id=handle.descriptor.capability_id,
            deadline=self.b03.issuer._clock() + 30,
        )
        self.b03.lifecycle.admission.admit(view, handle.descriptor.capability_id)
        self.runtime.authority.attach_message(self.b03.issuer, view, accepted)
        bound = await self.b03.services.for_module(module.module_id).scopes.bind(view)
        return view, bound, ingress, handle

    async def test_r2_receipt_ttl_actual_read_gateway_and_retry_timeout(self):
        await self.start()
        now = [0.0]
        self.receipts._clock = lambda: now[0]
        event = self.wrapper("R2-CANARY").context.event
        view, bound, ingress, handle = await self._r2_issued_read(event)
        try:
            self.assertEqual((await bound.message.read()).text, "R2-CANARY")
            before = len(self.transport.requests)
            now[0] = 300.0
            with self.assertRaises(ygl.OperationTimeout) as raised:
                await bound.message.read()
            self._r2_safe_failure(raised.exception, ygl.OperationTimeout)
            with self.assertRaises(ygl.OperationTimeout) as raised:
                await self.runtime.authority.validate(
                    ygl.InvocationOrigin.LLM_TOOL, ingress
                )
            self._r2_safe_failure(raised.exception, ygl.OperationTimeout)
            with self.assertRaises(ygl.OperationTimeout) as raised:
                await self.b03.issuer._check_message_source(view)
            self._r2_safe_failure(raised.exception, ygl.OperationTimeout)
            denied = await self.runtime.gateway.invoke_tool(
                view, handle.descriptor.name, {"query": "44091"}
            )
            self.assertIs(denied.status, ygl.ResultStatus.ERROR)
            self.assertIs(denied.error.code, ygl.ErrorCode.MODULE_UNAVAILABLE)
            self.assertNotIn("R2-CANARY", str(denied.error))
            self.assertIsNone(denied.model_facts)
            self.assertIsNone(denied.document)
            with self.assertRaises(ygl.OperationTimeout) as raised:
                self.runtime.receiver.make(
                    event, origin=ygl.InvocationOrigin.LLM_TOOL, tool_handle=handle
                )
            self._r2_safe_failure(raised.exception, ygl.OperationTimeout)

            def fail():
                raise RuntimeError("private-getter-canary")

            event.get_sender_id = fail
            with self.assertRaises(ygl.OperationTimeout) as raised:
                self.runtime.receiver.make(
                    event, origin=ygl.InvocationOrigin.LLM_TOOL, tool_handle=handle
                )
            self._r2_safe_failure(raised.exception, ygl.OperationTimeout)
            self._r2_cleared(ingress.evidence.receipt)
            self.assertEqual(ingress.evidence.receipt.expires, 300.0)
            self.assertIs(self.receipts._records[id(event)], ingress.evidence.receipt)
            self.assertEqual(len(self.transport.requests), before)
        finally:
            self.b03.issuer.release(view)

    async def test_r2_create_unavailability_and_structural_types_no_payload(self):
        await self.start()
        handle = self.runtime.publisher.handles[0]
        events = [self.wrapper(str(i)).context.event for i in range(128)]
        for event in events:
            self.runtime.receiver.make(
                event, origin=ygl.InvocationOrigin.LLM_TOOL, tool_handle=handle
            )
        with self.assertRaises(ygl.ServiceUnavailable) as raised:
            self.runtime.receiver.make(
                self.wrapper("overflow").context.event,
                origin=ygl.InvocationOrigin.LLM_TOOL,
                tool_handle=handle,
            )
        self._r2_safe_failure(raised.exception, ygl.ServiceUnavailable)
        fresh = _MessageReceipts()

        class NoWeak:
            __slots__ = ()

            def get_message_str(self):
                return "R2-CANARY"

        with self.assertRaises(ygl.ServiceUnavailable) as raised:
            fresh.capture(NoWeak(), ("a", "b", "c", ygl.ConversationKind.GROUP))
        self._r2_safe_failure(raised.exception, ygl.ServiceUnavailable)
        receiver = _AstrBotMessageIngress(object(), fresh, self.runtime.current_owner)
        for getter in ("get_message_str", "get_sender_id"):
            with self.subTest(getter=getter):
                event = self.wrapper("R2-CANARY").context.event

                def fail():
                    raise RuntimeError("private-getter-canary")

                setattr(event, getter, fail)
                with self.assertRaises(ygl.ServiceUnavailable) as raised:
                    receiver.make(
                        event, origin=ygl.InvocationOrigin.LLM_TOOL, tool_handle=handle
                    )
                self._r2_safe_failure(raised.exception, ygl.ServiceUnavailable)
        self.assertEqual(len(fresh._records), 0)
        for text in ("x" * 16385, "\ud800"):
            with self.subTest(structure=len(text)):
                with self.assertRaises(ygl.ParameterError) as raised:
                    receiver.make(
                        self.wrapper(text).context.event,
                        origin=ygl.InvocationOrigin.LLM_TOOL,
                        tool_handle=handle,
                    )
                self._r2_safe_failure(raised.exception, ygl.ParameterError)

    async def test_r2_all_closed_receipt_payloads_clear_with_gc_and_capacity(self):
        await self.start()
        handle = self.runtime.publisher.handles[0]
        now = [0.0]
        self.receipts._clock = lambda: now[0]
        for cause in (
            "expiry",
            "text",
            "identity",
            "getter",
            "make_getter",
            "owner",
            "owner_getter",
            "close",
            "gc",
        ):
            with self.subTest(cause=cause):
                table = _MessageReceipts()
                table._clock = lambda: now[0]
                receiver = _AstrBotMessageIngress(
                    object(), table, self.runtime.current_owner
                )
                event = self.wrapper("R2-CANARY").context.event
                ingress = receiver.make(
                    event, origin=ygl.InvocationOrigin.LLM_TOOL, tool_handle=handle
                )
                receipt = ingress.evidence.receipt
                if cause == "expiry":
                    now[0] += 300
                    with self.assertRaises(ygl.OperationTimeout):
                        receiver.current(ygl.InvocationOrigin.LLM_TOOL, ingress)
                elif cause == "text":
                    event.message_str = "changed"
                    self.assertFalse(
                        receiver.current(ygl.InvocationOrigin.LLM_TOOL, ingress)
                    )
                elif cause == "identity":
                    event.session.session_id = "changed"
                    self.assertFalse(
                        receiver.current(ygl.InvocationOrigin.LLM_TOOL, ingress)
                    )
                elif cause == "getter":

                    def fail():
                        raise RuntimeError("private-getter-canary")

                    event.get_sender_id = fail
                    with self.assertRaises(ygl.ServiceUnavailable):
                        receiver.current(ygl.InvocationOrigin.LLM_TOOL, ingress)
                elif cause == "make_getter":

                    def fail():
                        raise RuntimeError("private-getter-canary")

                    event.get_sender_id = fail
                    with self.assertRaises(ygl.ServiceUnavailable) as raised:
                        receiver.make(
                            event,
                            origin=ygl.InvocationOrigin.LLM_TOOL,
                            tool_handle=handle,
                        )
                    self._r2_safe_failure(raised.exception, ygl.ServiceUnavailable)
                elif cause == "owner_getter":

                    def fail(_):
                        raise RuntimeError("private-getter-canary")

                    receiver._owner_current = fail
                    with self.assertRaises(ygl.ServiceUnavailable) as raised:
                        receiver.current(ygl.InvocationOrigin.LLM_TOOL, ingress)
                    self._r2_safe_failure(raised.exception, ygl.ServiceUnavailable)
                elif cause == "owner":
                    i = self.manager.func_list.index(handle.tool)
                    self.manager.func_list[i] = self.host.FunctionTool(
                        name=handle.tool.name,
                        description="other",
                        parameters={"type": "object"},
                    )
                    try:
                        self.assertFalse(
                            receiver.current(ygl.InvocationOrigin.LLM_TOOL, ingress)
                        )
                    finally:
                        self.manager.func_list[i] = handle.tool
                elif cause == "close":
                    table.close()
                else:
                    key = id(event)
                    del event
                    gc.collect()
                    self.assertNotIn(key, table._records)
                self._r2_cleared(receipt)
                if cause not in ("gc", "close"):
                    self.assertIs(table._records[id(event)], receipt)
                table.close()

    async def test_r2_actual_read_getter_revoke_and_cancel_are_safe(self):
        await self.start()
        for cause in ("getter", "identity", "owner", "cancel"):
            with self.subTest(cause=cause):
                event = self.wrapper("R2-CANARY").context.event
                view, bound, ingress, handle = await self._r2_issued_read(event)
                original = event.get_message_str
                index = self.manager.func_list.index(handle.tool)
                try:
                    await bound.message.read()
                    before = len(self.transport.requests)
                    expected = (
                        ygl.ServiceUnavailable
                        if cause == "getter"
                        else ygl.AccessDenied
                    )
                    if cause == "getter":

                        def fail():
                            raise RuntimeError("private-getter-canary")

                        event.get_message_str = fail
                    elif cause == "identity":
                        event.session.session_id = "changed"
                    elif cause == "owner":
                        self.manager.func_list[index] = self.host.FunctionTool(
                            name=handle.tool.name,
                            description="other",
                            parameters={"type": "object"},
                        )
                    else:

                        def cancel():
                            raise asyncio.CancelledError()

                        event.get_message_str = cancel
                        with self.assertRaises(asyncio.CancelledError):
                            await bound.message.read()
                        event.get_message_str = original
                        self.assertEqual((await bound.message.read()).text, "R2-CANARY")
                        continue
                    with self.assertRaises(expected) as raised:
                        await bound.message.read()
                    self._r2_safe_failure(raised.exception, expected)
                    if cause == "getter":
                        with self.assertRaises(ygl.ServiceUnavailable) as raised:
                            await self.runtime.authority.validate(
                                ygl.InvocationOrigin.LLM_TOOL, ingress
                            )
                        self._r2_safe_failure(raised.exception, ygl.ServiceUnavailable)
                        denied = await self.runtime.gateway.invoke_tool(
                            view, handle.descriptor.name, {"query": "44091"}
                        )
                        self.assertIs(denied.status, ygl.ResultStatus.ERROR)
                        self.assertIs(
                            denied.error.code, ygl.ErrorCode.MODULE_UNAVAILABLE
                        )
                        self.assertNotIn("canary", denied.error.message)
                        self.assertIsNone(denied.model_facts)
                        self.assertIsNone(denied.document)
                    self._r2_cleared(ingress.evidence.receipt)
                    self.assertEqual(len(self.transport.requests), before)
                finally:
                    event.get_message_str = original
                    self.manager.func_list[index] = handle.tool
                    self.b03.issuer.release(view)

    async def test_r2_gateway_task_entry_and_return_source_classification(self):
        await self.start()
        now = [0.0]
        self.receipts._clock = lambda: now[0]
        handler = (
            self.runtime.registry.snapshot()
            .module("ff14/ff14")
            .handlers.capabilities["ff14.market.query"]
        )
        original_invoke = handler.invoke
        for stage in ("task_entry", "return"):
            for failure in ("ttl", "getter", "proof", "cancel"):
                with self.subTest(stage=stage, failure=failure):
                    event = self.wrapper(
                        "物品ID 44091", sender=stage + failure
                    ).context.event
                    view, bound, ingress, handle = await self._r2_issued_read(event)
                    original_text = event.get_message_str
                    calls = []
                    invalidated = []

                    def invalidate():
                        if invalidated:
                            return
                        invalidated.append(len(self.transport.requests))
                        if failure == "ttl":
                            now[0] += 300
                        elif failure == "proof":
                            event.session.session_id = "changed"
                        elif failure == "getter":

                            def fail():
                                raise RuntimeError("private-getter-canary")

                            event.get_message_str = fail
                        else:

                            def cancel():
                                raise asyncio.CancelledError()

                            event.get_message_str = cancel

                    def task_text():
                        if (
                            asyncio.current_task().get_name()
                            == "gateway:ff14.market.query"
                        ):
                            invalidate()
                            return (
                                event.get_message_str()
                                if event.get_message_str is not task_text
                                else original_text()
                            )
                        return original_text()

                    async def observe(view, parameters):
                        calls.append("handler")
                        result = await original_invoke(view, parameters)
                        if stage == "return":
                            invalidate()
                        return result

                    handler.invoke = observe
                    if stage == "task_entry":
                        event.get_message_str = task_text
                    before = len(self.transport.requests)
                    try:
                        if failure == "cancel":
                            with self.assertRaises(asyncio.CancelledError):
                                await self.runtime.gateway.invoke_tool(
                                    view, handle.descriptor.name, {"query": "44091"}
                                )
                        else:
                            denied = await self.runtime.gateway.invoke_tool(
                                view, handle.descriptor.name, {"query": "44091"}
                            )
                            self.assertIs(denied.status, ygl.ResultStatus.ERROR)
                            self.assertIs(
                                denied.error.code, ygl.ErrorCode.MODULE_UNAVAILABLE
                            )
                            self.assertEqual(
                                denied.error.message, "module is unavailable"
                            )
                            self.assertIsNone(denied.model_facts)
                            self.assertIsNone(denied.document)
                            self._r2_cleared(ingress.evidence.receipt)
                        self.assertTrue(invalidated)
                        self.assertEqual(
                            calls, [] if stage == "task_entry" else ["handler"]
                        )
                        if stage == "task_entry":
                            self.assertEqual(len(self.transport.requests), before)
                        # Return guard cannot retract requests accepted before
                        # invalidation. No subsequent request/result is admitted.
                        self.assertEqual(len(self.transport.requests), invalidated[0])
                    finally:
                        event.get_message_str = original_text
                        handler.invoke = original_invoke
                        self.b03.issuer.release(view)

    async def _recovery_read(self, wrapper, *, tool_name="ff14_market_query"):
        """Actual receiver/authority/issuer/admission/binder, without business IO."""
        view, bound, ingress, handle = await self._r2_issued_read(
            wrapper.context.event, tool_name=tool_name
        )
        try:
            message = await bound.message.read()
            self.assertIs(type(message), ygl.MessageContext)
            self.assertEqual(message.text, wrapper.context.event.get_message_str())
            self.assertTrue(
                self.runtime.receiver.current(ygl.InvocationOrigin.LLM_TOOL, ingress)
            )
            self.assertFalse(ingress.evidence.receipt.closed)
            self.assertTrue(self.runtime.current_owner(handle))
            return message, ingress, handle
        finally:
            self.b03.issuer.release(view)

    async def test_unbound_wrong_owner_and_expired_confirmation_cannot_query(self):
        """Restore original business assertions after explicit receipt controls."""
        await self.start()
        self.recovery_trace = []
        core = self.runtime.core_runtime
        handler = (
            core.registry.snapshot()
            .module("ff14/ff14")
            .handlers.capabilities["ff14.market.query"]
        )
        self.assertFalse(hasattr(handler, "_bind_tool_event"))
        # This private missing-owner fixture bypasses the normal Host prefix.
        # It deliberately reaches the receiver and closes its own receipt.
        await self._recovery_read(self.wrapper("物品ID 44091"))
        self.assertEqual(self.transport.requests, [])
        event = self.wrapper("物品ID 44091").context.event
        ingress = self.runtime._make_ingress(
            event, origin=ygl.InvocationOrigin.LLM_TOOL
        )
        with self.assertRaises(ygl.AccessDenied) as error:
            await core.invoke_tool(
                "ff14/ff14", "ff14_market_query", {"query": "44091"}, ingress=ingress
            )
        self._r2_safe_failure(error.exception, ygl.AccessDenied)
        self._r2_cleared(ingress.evidence.receipt)
        handle = next(
            h
            for h in self.runtime.publisher.handles
            if h.descriptor.name == "ff14_market_query"
        )
        with self.assertRaises(ygl.AccessDenied) as error:
            self.runtime._make_ingress(
                event, origin=ygl.InvocationOrigin.LLM_TOOL, tool_handle=handle
            )
        self._r2_safe_failure(error.exception, ygl.AccessDenied)
        self.assertIs(self.receipts._records[id(event)], ingress.evidence.receipt)
        self.assertEqual(self.transport.requests, [])
        self.recovery_trace.append(
            "1455-1465_private_missing_owner_closed_replay_no_HTTP"
        )

        # A fresh official event is genuinely live before changing only actor_id.
        wrapper = self.wrapper("物品ID 44091")
        view, bound, valid, handle = await self._r2_issued_read(wrapper.context.event)
        try:
            self.assertEqual((await bound.message.read()).text, "物品ID 44091")
            self.assertTrue(
                self.runtime.receiver.current(ygl.InvocationOrigin.LLM_TOOL, valid)
            )
            self.assertFalse(valid.evidence.receipt.closed)
            wrong = dataclasses.replace(valid, actor_id="test-platform-1:other")
            self.assertEqual(
                [
                    f.name
                    for f in dataclasses.fields(valid)
                    if getattr(valid, f.name) != getattr(wrong, f.name)
                ],
                ["actor_id"],
            )
            with self.assertRaises(ygl.AccessDenied) as error:
                await core.invoke_tool(
                    "ff14/ff14", "ff14_market_query", {"query": "44091"}, ingress=wrong
                )
            self._r2_safe_failure(error.exception, ygl.AccessDenied)
        finally:
            self.b03.issuer.release(view)
        self.assertEqual(self.transport.requests, [])
        self.recovery_trace.append("1467-1472_live_actor_only_denial_no_HTTP")

        for index, text in enumerate(
            (
                "Synthetic Item 44091那个。",
                "就Synthetic Item 44091",
                "选择Synthetic Item 44091",
                "选Synthetic Item 44091",
            )
        ):
            with self.subTest(unbound_name=index):
                wrapper = self.wrapper(text)
                await self._recovery_read(wrapper)
                self.assertEqual(self.transport.requests, [])
                denied = await self.call(
                    parameters={"query": "Synthetic Item 44091"}, wrapper=wrapper
                )
                self.assertEqual(denied["status"], "error")
                self.assertEqual(denied["error"]["code"], "parameter_error")
                self.assertTrue(
                    denied["error"]["message"].startswith("没有有效候选可供确认")
                )
                self.assertEqual(self.transport.requests, [])
                self.recovery_trace.append(
                    "1473-1485_absent_candidate_name_" + str(index) + "_no_HTTP"
                )

        pending = await self.call(
            parameters={"query": "Synthetic"}, wrapper=self.wrapper("Synthetic什么价")
        )
        self.assertEqual(pending["status"], "needs_selection")
        args = {key: pending["selection"][key] for key in ("batch_id", "generation")}
        self.assertTrue(args["batch_id"])
        self.assertTrue(args["generation"])
        self.assertIn(
            44091, [item["item_id"] for item in pending["selection"]["candidates"]]
        )
        args["item_id"] = 44091
        self.assertEqual(handler.registry.size, 1)
        self.recovery_trace.append("1486-1490_current_batch_generation_item_positive")

        # All three messages and exact owned handles are live before changing
        # only the FF14 candidate clock. Source receipt TTL/owner stay intact.
        expiry_queries = [
            self.wrapper(text)
            for text in ("Synthetic Item 44091那个。", "就Synthetic Item 44091")
        ]
        expiry_select = self.wrapper("Synthetic Item 44091那个。")
        expiry_sources = []
        before = len(self.transport.requests)
        for wrapper in expiry_queries:
            _, source, owned = await self._recovery_read(wrapper)
            expiry_sources.append((source, owned, source.evidence.receipt.expires))
        _, source, owned = await self._recovery_read(
            expiry_select, tool_name="ff14_market_select"
        )
        expiry_sources.append((source, owned, source.evidence.receipt.expires))
        self.assertEqual(len(self.transport.requests), before)
        self.assertEqual(handler.registry.size, 1)
        handler.registry._clock = lambda: 10**20
        for index, wrapper in enumerate(expiry_queries):
            with self.subTest(expired_name=index):
                source, owned, expires = expiry_sources[index]
                self.assertTrue(
                    self.runtime.receiver.current(ygl.InvocationOrigin.LLM_TOOL, source)
                )
                self.assertTrue(self.runtime.current_owner(owned))
                self.assertEqual(source.evidence.receipt.expires, expires)
                denied = await self.call(
                    parameters={"query": "Synthetic Item 44091"}, wrapper=wrapper
                )
                self.assertEqual(denied["status"], "error")
                self.assertEqual(denied["error"]["code"], "parameter_error")
                self.assertTrue(
                    denied["error"]["message"].startswith("没有有效候选可供确认")
                )
                self.assertEqual(len(self.transport.requests), before)
                self.recovery_trace.append(
                    "1491-1497_candidate_clock_only_name_" + str(index) + "_no_HTTP"
                )
        source, owned, expires = expiry_sources[2]
        self.assertTrue(
            self.runtime.receiver.current(ygl.InvocationOrigin.LLM_TOOL, source)
        )
        self.assertTrue(self.runtime.current_owner(owned))
        self.assertEqual(source.evidence.receipt.expires, expires)
        denied = await self.call("ff14_market_select", args, expiry_select)
        self.assertEqual(denied["status"], "error")
        self.assertEqual(denied["error"]["code"], "parameter_error")
        self.assertTrue(denied["error"]["message"].startswith("候选确认无效或已过期"))
        self.assertEqual(len(self.transport.requests), before)
        self.assertEqual(handler.registry.size, 0)
        self.recovery_trace.append("1498-1503_expired_select_no_HTTP_registry_zero")

    async def test_recovery_controlled_host_prefix_rejects_before_capture(self):
        """Controlled Host contract; production Runtime prefix is static evidence."""
        await self.start()
        self.recovery_trace = []
        handle = next(
            h
            for h in self.runtime.publisher.handles
            if h.descriptor.name == "ff14_market_query"
        )
        invalids = (
            None,
            SimpleNamespace(module_id="foreign/module", descriptor=handle.descriptor),
            SimpleNamespace(
                module_id=handle.module_id,
                descriptor=SimpleNamespace(name="wrong_tool"),
            ),
        )
        for index, invalid in enumerate(invalids):
            with self.subTest(prefix=index):
                wrapper = self.wrapper("物品ID 44091")
                message, live, _ = await self._recovery_read(wrapper)
                size = len(self.receipts._records)
                with self.assertRaises(PermissionError) as error:
                    await self.runtime.invoke_tool(
                        handle.module_id,
                        handle.descriptor.name,
                        {"query": "44091"},
                        wrapper.context.event,
                        tool_handle=invalid,
                    )
                self._r2_safe_failure(error.exception, PermissionError)
                self.assertEqual(str(error.exception), "Host tool ingress unavailable")
                self.assertEqual(len(self.receipts._records), size)
                self.assertFalse(live.evidence.receipt.closed)
                retry, _, _ = await self._recovery_read(wrapper)
                self.assertEqual(retry.event_ref, message.event_ref)
                # An uncaptured official event also fails before any allocation.
                untouched = self.wrapper("物品ID 44091")
                self.assertNotIn(id(untouched.context.event), self.receipts._records)
                with self.assertRaises(PermissionError) as error:
                    await self.runtime.invoke_tool(
                        handle.module_id,
                        handle.descriptor.name,
                        {"query": "44091"},
                        untouched.context.event,
                        tool_handle=invalid,
                    )
                self._r2_safe_failure(error.exception, PermissionError)
                self.assertNotIn(id(untouched.context.event), self.receipts._records)
                fresh, _, _ = await self._recovery_read(untouched)
                self.assertNotEqual(fresh.event_ref, message.event_ref)
                self.assertEqual(self.transport.requests, [])
                self.recovery_trace.append(
                    "prefix_" + str(index) + "_reject_no_capture_live_retry"
                )

    async def test_recovery_live_retry_new_message_and_closed_replay_separate(self):
        await self.start()
        self.recovery_trace = []
        wrapper = self.wrapper("同文消息")
        first, source, handle = await self._recovery_read(wrapper)
        retry, repeated, _ = await self._recovery_read(wrapper)
        self.assertEqual(first.event_ref, retry.event_ref)
        self.assertIs(source.evidence.receipt, repeated.evidence.receipt)
        distinct = self.wrapper("同文消息")
        new, new_source, _ = await self._recovery_read(distinct)
        self.assertIsNot(wrapper.context.event, distinct.context.event)
        self.assertEqual(first.text, new.text)
        self.assertNotEqual(first.event_ref, new.event_ref)
        self.recovery_trace.append(
            "same_live_object_same_ref_distinct_equal_text_new_ref"
        )
        # Close one real source by identity mutation, without closing its table.
        event = wrapper.context.event
        original = event.session.session_id
        event.session.session_id = "changed"
        self.assertFalse(
            self.runtime.receiver.current(ygl.InvocationOrigin.LLM_TOOL, source)
        )
        event.session.session_id = original
        self._r2_cleared(source.evidence.receipt)
        for attempt in range(2):
            with self.assertRaises(ygl.AccessDenied) as error:
                self.runtime._make_ingress(
                    event, origin=ygl.InvocationOrigin.LLM_TOOL, tool_handle=handle
                )
            self._r2_safe_failure(error.exception, ygl.AccessDenied)
            self.assertIs(self.receipts._records[id(event)], source.evidence.receipt)
        still_new, still_source, _ = await self._recovery_read(distinct)
        self.assertEqual(still_new.event_ref, new.event_ref)
        self.assertIs(still_source.evidence.receipt, new_source.evidence.receipt)
        self.assertEqual(self.transport.requests, [])
        self.recovery_trace.append(
            "closed_old_object_no_resign_new_object_remains_live_no_HTTP"
        )


# Reuse unchanged business assertions through this separately labelled receiver
# composition. The original native Runtime.start methods remain independent.
for _method in (
    "test_same_query_new_event_reparses_but_same_event_keeps_batch",
    "test_global_all_intents_cross_real_core_fact_gate",
    "test_global_missing_metrics_and_warning_bound_cross_core_gate",
    "test_missing_query_rejected_in_module_without_losing_candidate",
    "test_requested_chinese_name_and_omitted_scope_use_core_cn",
    "test_controlled_model_chat_asks_and_continues_explicit_user_selection",
    "test_overview_selection_keeps_original_context_and_versioned_quotes",
    "test_parameters_identity_origin_and_host_permission_guard",
    "test_numeric_id_requires_label_or_documented_whole_message",
    "test_name_evidence_rejects_guesses_preserves_selection_and_new_query",
    "test_natural_name_chat_selection_preserves_context_prices_and_cache",
    "test_bound_event_reset_after_exception_and_concurrent_owners",
    "test_consumed_selection_flight_rejects_query_bypass_and_fences_new_item",
    "test_ordinal_denial_query_guard_keeps_batch_and_rejects_absent_expired",
    "test_cancelled_consumed_flight_clears_context_and_recovers_after_guard",
    "test_consumed_confirmation_proof_clears_on_exception_and_query_recovers",
    "test_candidate_namespace_isolation_expiry_and_new_query_invalidation",
    "test_tool_errors_and_private_result_cannot_bypass_output",
    "test_partial_no_data_and_decimal_missing_facts_remain_truthful",
):
    setattr(ControlledMessageTests, _method, getattr(legacy.LLMToolTests, _method))
