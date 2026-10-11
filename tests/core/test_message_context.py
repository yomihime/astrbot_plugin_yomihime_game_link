"""S2-A offline Core receivers; no AstrBot, model, or real host instance."""

from __future__ import annotations

import asyncio
import dataclasses
import tempfile
import unittest
import weakref
from pathlib import Path
from secrets import token_hex
from types import SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.core.invocation import Gateway
from ygl_test_subject.core.lifecycle import _ServiceLifetime
from ygl_test_subject.core.registry import Registry
from ygl_test_subject.core.task_scope import TaskScope
from ygl_test_subject.services.core_runtime import (
    HostIngress,
    _HostIngressAuthority,
)
from ygl_test_subject.services.module_services import InvocationServiceBinder

import yomihime_game_link_sdk as ygl
from tests.core import test_invocation as invocation_fixture
from tests.fixtures.b03_runtime import _Collector, _manifest, build_runtime
from tests.fixtures.b03_runtime import _Handler as _B03Handler

# The consuming module imports only the public SDK. Core/Host objects are never
# available in its globals; the trusted test composition wires ModuleServices.
_MODULE = """import yomihime_game_link_sdk as ygl
class Handler:
    def __init__(self):
        self.services = None
        self.calls = []
        self.messages = []
        self.bound = None
        self.probe = None
        self.errors = []
    async def invoke(self, view: ygl.InvocationView, parameters: ygl.JsonObject) -> ygl.CapabilityResult:
        self.calls.append(view)
        self.bound = await self.services.scopes.bind(view)
        if self.probe is not None:
            await self.probe(self.bound)
        else:
            self.messages.append(await self.bound.message.read())
        return ygl.CapabilityResult("safe-result", ygl.ResultStatus.SUCCESS,
            document=ygl.DisplayDocument("Safe", "Result", (ygl.TextBlock("ok"),)))
"""


def handler():
    namespace = {}
    exec(_MODULE, namespace)
    return namespace["Handler"]()


class _Source:
    def __init__(
        self, text="source canary", actor="alice", session="room", adapter="offline"
    ):
        self.text, self.actor, self.session, self.adapter = (
            text,
            actor,
            session,
            adapter,
        )
        self.kind = ygl.ConversationKind.DIRECT
        self.active = True


class _Host:
    """Private exact receipt test Host, implementing the normal internal port."""

    def __init__(self, clock):
        self.clock = clock
        self.receipts = {}
        self.before = None
        self.current_override = None
        self.checks = []

    def ingress(self, source):
        receipt = self.receipts.get(id(source))
        if receipt is None:
            key = id(source)

            def gone(ref):
                old = self.receipts.get(key)
                if old is not None and old.ref is ref:
                    self.receipts.pop(key, None)

            receipt = SimpleNamespace(
                ref=weakref.ref(source, gone),
                text=source.text,
                actor=source.actor,
                session=source.session,
                adapter=source.adapter,
                kind=source.kind,
                deadline=self.clock() + 300,
                correlation=token_hex(32),
            )
            self.receipts[id(source)] = receipt
        kwargs = dict(
            adapter_id=receipt.adapter,
            actor_id=receipt.actor,
            conversation_id=receipt.session,
            delivery_route="offline-route",
            conversation_kind=receipt.kind,
            evidence=receipt,
        )
        # A-phase can exercise the old receiver before the new internal fields exist.
        fields = {field.name for field in dataclasses.fields(HostIngress)}
        if "message_text" in fields:
            kwargs.update(
                message_text=receipt.text, message_correlation=receipt.correlation
            )
        return HostIngress(**kwargs)

    async def validate(self, origin, ingress):
        accepted = self._receipt_current(origin, ingress)
        if self.before:
            await self.before()
        return accepted

    def current(self, origin, ingress):
        self.checks.append("current")
        if self.current_override is not None:
            return self.current_override(origin, ingress)
        return self._receipt_current(origin, ingress)

    def _receipt_current(self, origin, ingress):
        receipt = ingress.evidence
        source = receipt.ref() if hasattr(receipt, "ref") else None
        if source is None or self.receipts.get(id(source)) is not receipt:
            return False
        if self.clock() >= receipt.deadline:
            raise ygl.OperationTimeout()
        return (
            source.active
            and source.text == receipt.text
            and source.actor == receipt.actor
            and source.session == receipt.session
            and source.adapter == receipt.adapter
            and source.kind is receipt.kind
            and ingress.actor_id == receipt.actor
            and ingress.adapter_id == receipt.adapter
            and ingress.conversation_id == receipt.session
            and ingress.conversation_kind is receipt.kind
            and getattr(ingress, "message_text", receipt.text) == receipt.text
            and getattr(ingress, "message_correlation", receipt.correlation)
            == receipt.correlation
        )


class MessageContextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.clock = [1000.0]
        self.host = _Host(lambda: self.clock[0])
        self.subject = handler()
        self.closed = False
        registry = Registry()
        dependencies = (
            ygl.CapabilityReference("sample/beta", "read"),
            ygl.CapabilityReference("sample/alpha", "private.read"),
        )
        alpha = dataclasses.replace(
            _manifest("alpha", dependencies),
            tools=(ygl.ToolDescriptor("read_tool", "read", {}, "read"),),
            commands=_manifest("alpha").commands
            + (ygl.CommandDescriptor("read", "read", {}, "read"),),
        )
        beta = dataclasses.replace(
            _manifest("beta"),
            tools=(ygl.ToolDescriptor("beta_read_tool", "read", {}, "read"),),
        )
        registry.register_package(
            ygl.PackageManifest(
                "sample",
                "1.0.0",
                ygl.MODULE_ABI_VERSION,
                (alpha, beta),
                "tests",
                "MIT",
                "offline",
            ),
            {
                "alpha": ygl.ModuleHandlers(
                    {"read": self.subject, "private.read": _B03Handler("private.read")},
                    {"account-collector": _Collector()},
                    {},
                ),
                "beta": ygl.ModuleHandlers({"read": handler()}, {}, {}),
            },
        )
        self.runtime = await build_runtime(self.root, registry=registry)
        self.runtime.issuer._clock = lambda: self.clock[0]
        self.runtime.services._clock = lambda: self.clock[0]
        self.subject.services = self.runtime.services.for_module("sample/alpha")
        self.beta = (
            registry.snapshot().module("sample/beta").handlers.capabilities["read"]
        )
        self.beta.services = self.runtime.services.for_module("sample/beta")
        self.authority = _HostIngressAuthority(
            self.host.validate, self.host.current, self.require_accepting
        )
        self.gateway = Gateway(
            registry,
            self.runtime.issuer,
            admission=self.runtime.lifecycle.admission,
            lifecycle=self.runtime.lifecycle,
            clock=lambda: self.clock[0],
        )

    def require_accepting(self):
        if self.closed:
            raise ygl.InvalidInvocation()

    async def asyncTearDown(self):
        self.closed = True
        if hasattr(self, "runtime"):
            for issued in tuple(self.runtime.issuer._issued.values()):
                if issued.invocation_id in self.runtime.issuer._issued:
                    self.runtime.issuer.release(issued)
            await self.runtime.services.close_credentials()
            await self.runtime.database.executor.close(timeout=2)
        self.temp.cleanup()

    async def enter(self, source, module_id="sample/alpha"):
        ingress = self.host.ingress(source)
        accepted = await self.authority.validate(ygl.InvocationOrigin.LLM_TOOL, ingress)
        snapshot = self.runtime.registry.snapshot()
        module = snapshot.module(module_id)
        view = self.runtime.issuer.issue(
            origin=ygl.InvocationOrigin.LLM_TOOL,
            module_id=module.module_id,
            module_epoch=module.epoch,
            registry_revision=snapshot.revision,
            actor_id=ingress.actor_id,
            conversation_id=ingress.conversation_id,
            adapter_id=ingress.adapter_id,
            capability_id="read",
            deadline=self.clock[0] + 30,
        )
        try:
            self.runtime.lifecycle.admission.admit(view, "read")
            self.authority.attach_message(self.runtime.issuer, view, accepted)
        except BaseException:
            self.runtime.issuer.release(view)
            raise
        return view

    async def invoke(self, source=None, **kwargs):
        source = source or _Source()
        view = await self.enter(source)
        try:
            return await self.gateway.invoke_tool(view, "read_tool", kwargs)
        finally:
            self.runtime.issuer.release(view)

    def safe(self, error):
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        self.assertNotIn("canary", str(error))

    async def test_s2a_sdk_only_module_reads_actual_core(self):
        result = await self.invoke()
        self.assertIs(result.status, ygl.ResultStatus.SUCCESS)
        self.assertEqual(self.subject.messages[0].text, "source canary")
        self.assertTrue(self.subject.messages[0].event_ref)
        self.assertFalse(self.runtime.issuer._issued)

    async def test_s2a_plain_issuer_root_cannot_enter_handler(self):
        module = self.runtime.registry.snapshot().module("sample/alpha")
        view = self.runtime.issuer.issue(
            origin=ygl.InvocationOrigin.LLM_TOOL,
            module_id=module.module_id,
            module_epoch=module.epoch,
            registry_revision=self.runtime.registry.snapshot().revision,
            actor_id="alice",
            conversation_id="room",
            adapter_id="offline",
            capability_id="read",
        )
        try:
            result = await self.gateway.invoke_tool(view, "read_tool", {})
            self.assertIs(result.status, ygl.ResultStatus.ERROR)
            self.assertEqual(self.subject.calls, [])
        finally:
            self.runtime.issuer.release(view)

    async def test_s2a_same_source_retry_and_identity_scoped_references(self):
        source = _Source()
        await self.invoke(source)
        first = self.subject.messages[-1]
        old_bound = self.subject.bound
        await self.invoke(source)
        self.assertEqual(first.event_ref, self.subject.messages[-1].event_ref)
        with self.assertRaises(ygl.InvalidInvocation) as raised:
            await old_bound.message.read()
        self.safe(raised.exception)
        for fresh in (
            _Source(),
            _Source(actor="bob"),
            _Source(session="other"),
            _Source(adapter="other"),
        ):
            with self.subTest(
                actor=fresh.actor, session=fresh.session, adapter=fresh.adapter
            ):
                await self.invoke(fresh)
                self.assertNotEqual(
                    first.event_ref, self.subject.messages[-1].event_ref
                )
        self.assertEqual(first.text, self.subject.messages[-1].text)

    async def test_s2a_forged_ingress_description_and_receipt_rejected(self):
        source = _Source()
        ingress = self.host.ingress(source)
        for change in (
            {"evidence": object()},
            {"actor_id": "mallory"},
            {"conversation_id": "other"},
            {"adapter_id": "other"},
            {"message_text": "fake"},
            {"message_correlation": "fake"},
        ):
            with self.subTest(change=tuple(change)):
                with self.assertRaises(
                    (ygl.InvalidInvocation, ygl.AccessDenied)
                ) as raised:
                    await self.authority.validate(
                        ygl.InvocationOrigin.LLM_TOOL,
                        dataclasses.replace(ingress, **change),
                    )
                self.safe(raised.exception)
        self.assertEqual(self.subject.calls, [])

    async def test_s2a_model_fields_and_copied_views_never_deliver(self):
        for name in (
            "text",
            "event_ref",
            "actor",
            "session",
            "adapter",
            "trusted",
            "message",
        ):
            with self.subTest(field=name):
                result = await self.invoke(**{name: "fake"})
                self.assertIs(result.status, ygl.ResultStatus.ERROR)
        self.assertEqual(self.subject.calls, [])
        source = _Source()
        view = await self.enter(source)
        try:
            with self.assertRaises(ygl.InvalidInvocation) as raised:
                await self.subject.services.scopes.bind(dataclasses.replace(view))
            self.safe(raised.exception)
            result = await self.gateway.invoke_tool(
                dataclasses.replace(view), "read_tool", {}
            )
            self.assertIs(result.status, ygl.ResultStatus.ERROR)
            self.assertEqual(self.subject.calls, [])
            fake = dataclasses.replace(ygl.MessageContext("fake", "replayed-reference"))
            self.assertFalse(hasattr(self.subject.services.scopes, "attach_message"))
            self.assertFalse(hasattr(fake, "trusted"))
        finally:
            self.runtime.issuer.release(view)

    async def test_s2a_current_source_changes_and_expiry_reject_saved_read(self):
        for change in (
            "revoke",
            "expiry",
            "text",
            "actor",
            "session",
            "adapter",
            "kind",
        ):
            with self.subTest(change=change):
                source = _Source()
                view = await self.enter(source)
                bound = await self.subject.services.scopes.bind(view)
                self.change_source(source, change)
                expected = (
                    ygl.OperationTimeout if change == "expiry" else ygl.AccessDenied
                )
                try:
                    with self.assertRaises(expected) as raised:
                        await bound.message.read()
                    self.safe(raised.exception)
                finally:
                    self.runtime.issuer.release(view)

    def change_source(self, source, change):
        if change == "revoke":
            source.active = False
        elif change == "expiry":
            self.clock[0] += 300
        elif change == "kind":
            source.kind = ygl.ConversationKind.GROUP
        else:
            setattr(source, change, "changed-canary")

    async def test_s2a_async_source_current_after_all_awaits(self):
        # Both windows are real product awaits. The Host checker deliberately
        # returns its pre-await True; the post-check binder delegates its actual
        # admission before suspending. Only the final sync predicate can reject.
        for window in ("source_checker", "binder_after_true"):
            for change in (
                "revoke",
                "expiry",
                "text",
                "actor",
                "session",
                "adapter",
                "kind",
            ):
                with self.subTest(window=window, change=change):
                    source = _Source()
                    view = await self.enter(source)
                    bound = await self.subject.services.scopes.bind(view)
                    entered, release = asyncio.Event(), asyncio.Event()

                    async def barrier():
                        entered.set()
                        await release.wait()

                    calls = 0
                    original = InvocationServiceBinder._admit

                    async def delayed(binder, invocation):
                        nonlocal calls
                        result = await original(binder, invocation)
                        calls += 1
                        if calls == 2:
                            await barrier()
                        return result

                    if window == "source_checker":
                        self.host.before = barrier
                    with patch.object(
                        InvocationServiceBinder,
                        "_admit",
                        delayed if window == "binder_after_true" else original,
                    ):
                        task = asyncio.create_task(bound.message.read())
                        await asyncio.wait_for(entered.wait(), 1)
                        self.change_source(source, change)
                        release.set()
                        expected = (
                            ygl.OperationTimeout
                            if change == "expiry"
                            else ygl.AccessDenied
                        )
                        with self.assertRaises(expected) as raised:
                            await task
                        self.safe(raised.exception)
                    self.host.before = None
                    self.runtime.issuer.release(view)
                    self.assertEqual(self.subject.messages, [])
                    self.assertEqual(self.runtime.transport.requests, [])
        self.assertEqual(self.subject.calls, [])

    async def test_s2a_root_async_old_true_and_actual_task_entry_are_fenced(self):
        for window in ("host_ingress_checker", "gateway_task_entry"):
            for change in (
                "revoke",
                "expiry",
                "text",
                "actor",
                "session",
                "adapter",
                "kind",
            ):
                with self.subTest(window=window, change=change):
                    source = _Source()
                    entered, release = asyncio.Event(), asyncio.Event()

                    async def barrier():
                        entered.set()
                        await release.wait()

                    original = TaskScope.run

                    async def delayed(scope, work, **kwargs):
                        try:
                            await barrier()
                        except BaseException:
                            work.close()
                            raise
                        return await original(scope, work, **kwargs)

                    if window == "host_ingress_checker":
                        self.host.before = barrier
                    with patch.object(
                        TaskScope,
                        "run",
                        delayed if window == "gateway_task_entry" else original,
                    ):
                        task = asyncio.create_task(self.invoke(source))
                        await asyncio.wait_for(entered.wait(), 1)
                        self.change_source(source, change)
                        release.set()
                        if window == "host_ingress_checker":
                            expected = (
                                ygl.OperationTimeout
                                if change == "expiry"
                                else ygl.AccessDenied
                            )
                            with self.assertRaises(expected) as raised:
                                await task
                            self.safe(raised.exception)
                        else:
                            result = await task
                            self.assertIs(result.status, ygl.ResultStatus.ERROR)
                    self.host.before = None
                    self.assertEqual(self.subject.calls, [])
                    self.assertEqual(self.subject.messages, [])
                    self.assertEqual(self.runtime.transport.requests, [])
                    self.assertFalse(self.runtime.issuer._messages)

    async def test_s2a_async_positive_and_cancellation_both_windows(self):
        async def yield_once():
            await asyncio.sleep(0)

        self.host.before = yield_once
        self.assertIs((await self.invoke()).status, ygl.ResultStatus.SUCCESS)
        self.host.before = None
        for window in ("source_checker", "binder_after_true"):
            source = _Source()
            view = await self.enter(source)
            bound = await self.subject.services.scopes.bind(view)
            entered = asyncio.Event()

            async def wait_forever():
                entered.set()
                await asyncio.Event().wait()

            original = InvocationServiceBinder._admit
            count = 0

            async def delay(binder, invocation):
                nonlocal count
                result = await original(binder, invocation)
                count += 1
                if count == 2:
                    await wait_forever()
                return result

            if window == "source_checker":
                self.host.before = wait_forever
            with patch.object(
                InvocationServiceBinder,
                "_admit",
                delay if window == "binder_after_true" else original,
            ):
                task = asyncio.create_task(bound.message.read())
                await asyncio.wait_for(entered.wait(), 1)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.host.before = None
            self.runtime.issuer.release(view)
        self.assertEqual(len(self.subject.messages), 1)

    async def test_s2a_sync_predicate_required(self):
        source = _Source()
        ingress = self.host.ingress(source)

        async def asynchronous(*_):
            return True

        def awaitable(*_):
            return asynchronous()

        def nonexact(*_):
            return 1

        def canary(*_):
            raise RuntimeError("getter internal canary")

        foreign = _Host(lambda: self.clock[0])
        for predicate in (
            None,
            asynchronous,
            awaitable,
            nonexact,
            canary,
            foreign.current,
        ):
            with self.subTest(kind=getattr(predicate, "__name__", None)):
                authority = _HostIngressAuthority(
                    self.host.validate, predicate, self.require_accepting
                )
                with self.assertRaises(ygl.AccessDenied) as raised:
                    await authority.validate(ygl.InvocationOrigin.LLM_TOOL, ingress)
                self.safe(raised.exception)
        view = await self.enter(source)
        bound = await self.subject.services.scopes.bind(view)
        try:
            for override in (asynchronous, awaitable, nonexact, canary):
                self.host.current_override = override
                with self.subTest(read=getattr(override, "__name__", None)):
                    with self.assertRaises(ygl.AccessDenied) as raised:
                        await bound.message.read()
                    self.safe(raised.exception)
        finally:
            self.host.current_override = None
            self.runtime.issuer.release(view)
        self.assertEqual(self.subject.calls, [])

    async def test_s2a_nested_dependencies_do_not_inherit_message(self):
        async def child_read(bound):
            with self.assertRaises(ygl.ServiceUnavailable) as raised:
                await bound.message.read()
            self.safe(raised.exception)

        self.beta.probe = child_read
        source = _Source()
        view = await self.enter(source)
        bound = await self.subject.services.scopes.bind(view)
        try:
            result = await bound.dependencies.invoke(
                view, ygl.CapabilityReference("sample/beta", "read"), {}
            )
            self.assertIs(result.status, ygl.ResultStatus.SUCCESS)
            self.assertEqual(self.beta.calls[0].parent_id, view.invocation_id)
            self.assertEqual(self.beta.messages, [])
            private = await bound.dependencies.invoke(
                view, ygl.CapabilityReference("sample/alpha", "private.read"), {}
            )
            self.assertIs(private.status, ygl.ResultStatus.ERROR)
            child = self.runtime.issuer.derive(
                view,
                module_id=view.module_id,
                module_epoch=view.module_epoch,
                capability_id="read",
            )
            self.runtime.lifecycle.admission.admit(child, "read")
            child_bound = await self.subject.services.scopes.bind(child)
            await child_read(child_bound)
        finally:
            self.runtime.issuer.release(view)
        with self.assertRaises(ygl.InvalidInvocation):
            await child_bound.message.read()

    async def test_s2a_nonchat_message_unavailable_without_new_permissions(self):
        module = self.runtime.registry.snapshot().module("sample/alpha")
        for origin in (
            ygl.InvocationOrigin.COMMAND,
            ygl.InvocationOrigin.ADMIN,
            ygl.InvocationOrigin.SUBSCRIPTION,
            ygl.InvocationOrigin.SCHEDULER,
        ):
            if origin is ygl.InvocationOrigin.SUBSCRIPTION:
                kwargs = dict(
                    subscription_id="subscription",
                    subscription_revision=1,
                    delivery_route="offline-route",
                    conversation_kind=ygl.InvocationConversationKind.DIRECT,
                    subscription_scope=ygl.InvocationSubscriptionScope.PUBLIC,
                )
            else:
                kwargs = {}
            view = self.runtime.issuer.issue(
                origin=origin,
                module_id=module.module_id,
                module_epoch=module.epoch,
                registry_revision=self.runtime.registry.snapshot().revision,
                actor_id="alice",
                conversation_id="room",
                adapter_id="offline",
                capability_id="read",
                **kwargs,
            )
            try:
                if origin is ygl.InvocationOrigin.COMMAND:
                    self.runtime.lifecycle.admission.admit(view, "read")
                    bound = await self.subject.services.scopes.bind(view)
                    with self.assertRaises(ygl.ServiceUnavailable) as raised:
                        await bound.message.read()
                else:
                    # These origins cannot gain a public binder/admission simply
                    # to obtain a message handle. The issuer source receiver also
                    # rejects message reads without inventing chat provenance.
                    with self.assertRaises(ygl.ServiceUnavailable) as raised:
                        self.runtime.issuer._message_for(view)
                self.safe(raised.exception)
            finally:
                self.runtime.issuer.release(view)

    async def test_s2a_release_timeout_revoke_disable_and_scope_stop(self):
        for transition in ("release", "timeout", "revoke"):
            with self.subTest(transition=transition):
                source = _Source()
                view = await self.enter(source)
                bound = await self.subject.services.scopes.bind(view)
                try:
                    if transition == "release":
                        self.runtime.issuer.release(view)
                    elif transition == "timeout":
                        self.clock[0] += 31
                    else:
                        source.active = False
                    expected = (
                        ygl.OperationTimeout
                        if transition == "timeout"
                        else ygl.AccessDenied
                    )
                    with self.assertRaises(expected) as raised:
                        await bound.message.read()
                    self.safe(raised.exception)
                finally:
                    if view.invocation_id in self.runtime.issuer._issued:
                        self.runtime.issuer.release(view)
        source = _Source()
        view = await self.enter(source)
        bound = await self.subject.services.scopes.bind(view)
        try:
            # Use the actual Lifecycle-owned synchronous disable fence, then
            # drain and detach its instance; Registry rejects bypass attempts.
            identity = self.runtime.lifecycle.quiesce(
                "sample/alpha", "stop", "disabled"
            )
            with self.assertRaises(ygl.AccessDenied) as raised:
                await bound.message.read()
            self.safe(raised.exception)
            self.runtime.lifecycle.publish_committed_intent(
                "sample/alpha",
                "stop",
                identity,
                False,
                self.runtime.registry.snapshot().revision,
            )
            await self.runtime.lifecycle.stop_candidate("sample/alpha", "stop", 1)
            with self.assertRaises(ygl.AccessDenied) as raised:
                await bound.message.read()
            self.safe(raised.exception)
            self.runtime.lifecycle.detach_stopped("sample/alpha")
            with self.assertRaises(ygl.AccessDenied) as raised:
                await bound.message.read()
            self.safe(raised.exception)
        finally:
            self.runtime.issuer.release(view)
        self.assertFalse(self.runtime.issuer._messages)

    async def test_s2a_module_runtime_epoch_reference_isolation_and_foreign_acceptance(
        self,
    ):
        source = _Source()
        view = await self.enter(source)
        try:
            message = await (
                await self.subject.services.scopes.bind(view)
            ).message.read()
            other = _HostIngressAuthority(
                self.host.validate, self.host.current, self.require_accepting
            )
            accepted = await other.validate(
                ygl.InvocationOrigin.LLM_TOOL, self.host.ingress(source)
            )
            with self.assertRaises(ygl.InvalidInvocation) as raised:
                self.authority.attach_message(self.runtime.issuer, view, accepted)
            self.safe(raised.exception)
            from ygl_test_subject.core.context_issuer import ContextIssuer

            second = ContextIssuer(clock=lambda: self.clock[0])
            foreign = second.issue(
                origin=view.origin,
                module_id=view.module_id,
                module_epoch=view.module_epoch,
                registry_revision=view.registry_revision,
                actor_id=view.actor_id,
                conversation_id=view.conversation_id,
                adapter_id=view.adapter_id,
                capability_id=view.capability_id,
            )
            self.assertNotEqual(
                second._message_scope_key, self.runtime.issuer._message_scope_key
            )
            with self.assertRaises(ygl.InvalidInvocation):
                self.runtime.issuer._message_for(foreign)
            second.release(foreign)
            dto = dataclasses.replace(message)
            self.assertEqual(dto.event_ref, message.event_ref)
        finally:
            self.runtime.issuer.release(view)

    async def test_s2a_module_epoch_reload_and_module_reference_isolation(self):
        from tests.fixtures.b03_runtime import _RuntimeInstance

        source = _Source()
        old = await self.enter(source)
        bound = await self.subject.services.scopes.bind(old)
        first = await bound.message.read()
        beta = await self.enter(source, "sample/beta")
        try:
            result = await self.gateway.invoke_tool(beta, "beta_read_tool", {})
            self.assertIs(result.status, ygl.ResultStatus.SUCCESS)
            self.assertNotEqual(first.event_ref, self.beta.messages[-1].event_ref)
        finally:
            self.runtime.issuer.release(beta)
        module = self.runtime.registry.snapshot().module("sample/alpha")
        identity = self.runtime.lifecycle.quiesce(
            module.module_id, "reload-stop", "replaced"
        )
        self.runtime.lifecycle.publish_committed_intent(
            module.module_id,
            "reload-stop",
            identity,
            False,
            self.runtime.registry.snapshot().revision,
        )
        await self.runtime.lifecycle.stop_candidate(module.module_id, "reload-stop", 1)
        self.runtime.lifecycle.detach_stopped(module.module_id)
        self.runtime.lifecycle.restore_registration(
            "sample", module.manifest, module.handlers
        )
        replacement = handler()
        lifetime = _ServiceLifetime(("sample", module.module_id, "reload-install"))
        replacement.services = self.runtime.services.for_candidate(
            module.module_id, module.manifest, service_lifetime=lifetime
        )
        replacement_handlers = dataclasses.replace(
            module.handlers,
            capabilities={**module.handlers.capabilities, "read": replacement},
        )
        instance = _RuntimeInstance(replacement_handlers, module.manifest)
        handlers = self.runtime.lifecycle.adopt_candidate(
            "sample",
            module.manifest,
            "reload-install",
            instance,
            service_lifetime=lifetime,
        )
        self.runtime.lifecycle.install_dormant(
            "sample", module.module_id, "reload-install", instance, handlers
        )
        new_identity, _ = await self.runtime.lifecycle.start_candidate(
            module.module_id, "reload-start"
        )
        await self.runtime.health.prepare(module.module_id, module.manifest)
        self.runtime.lifecycle.publish_committed_intent(
            module.module_id,
            "reload-start",
            new_identity,
            True,
            self.runtime.registry.snapshot().revision,
        )
        self.assertNotEqual(old.module_epoch, new_identity.module_epoch)
        with self.assertRaises(ygl.InvalidInvocation) as raised:
            await bound.message.read()
        self.safe(raised.exception)
        self.assertIsNot(replacement, self.subject)
        self.assertIsNot(replacement.services, self.subject.services)
        fresh = await self.enter(source)
        try:
            message = await (
                await replacement.services.scopes.bind(fresh)
            ).message.read()
            self.assertNotEqual(first.event_ref, message.event_ref)
            self.assertEqual(first.text, message.text)
        finally:
            self.runtime.issuer.release(fresh)
            self.runtime.issuer.release(old)

    async def test_s2a_runtime_isolation_uses_actual_second_core_composition(self):
        second_handler = handler()
        alpha = self.runtime.registry.snapshot().module("sample/alpha").manifest
        beta = self.runtime.registry.snapshot().module("sample/beta").manifest
        registry = Registry()
        registry.register_package(
            ygl.PackageManifest(
                "sample",
                "1.0.0",
                ygl.MODULE_ABI_VERSION,
                (alpha, beta),
                "tests",
                "MIT",
                "offline",
            ),
            {
                "alpha": ygl.ModuleHandlers(
                    {
                        "read": second_handler,
                        "private.read": _B03Handler("private.read"),
                    },
                    {"account-collector": _Collector()},
                    {},
                ),
                "beta": ygl.ModuleHandlers({"read": _B03Handler()}, {}, {}),
            },
        )
        second = await build_runtime(self.root / "second", registry=registry)
        second.issuer._clock = lambda: self.clock[0]
        second_handler.services = second.services.for_module("sample/alpha")
        source = _Source()
        first = await self.enter(source)
        second_view = None
        try:
            original = await (
                await self.subject.services.scopes.bind(first)
            ).message.read()
            ingress = self.host.ingress(source)
            accepted = await self.authority.validate(
                ygl.InvocationOrigin.LLM_TOOL, ingress
            )
            module = second.registry.snapshot().module("sample/alpha")
            second_view = second.issuer.issue(
                origin=ygl.InvocationOrigin.LLM_TOOL,
                module_id=module.module_id,
                module_epoch=module.epoch,
                registry_revision=second.registry.snapshot().revision,
                actor_id=ingress.actor_id,
                conversation_id=ingress.conversation_id,
                adapter_id=ingress.adapter_id,
                capability_id="read",
            )
            second.lifecycle.admission.admit(second_view, "read")
            self.authority.attach_message(second.issuer, second_view, accepted)
            gateway = Gateway(
                registry,
                second.issuer,
                admission=second.lifecycle.admission,
                lifecycle=second.lifecycle,
            )
            self.assertIs(
                (await gateway.invoke_tool(second_view, "read_tool", {})).status,
                ygl.ResultStatus.SUCCESS,
            )
            self.assertNotEqual(
                original.event_ref, second_handler.messages[-1].event_ref
            )
            with self.assertRaises(ygl.InvalidInvocation):
                await second_handler.services.scopes.bind(first)
        finally:
            self.runtime.issuer.release(first)
            if second_view is not None:
                second.issuer.release(second_view)
            await second.services.close_credentials()
            await second.database.executor.close(timeout=2)

    async def test_s2a_gateway_cancellation_releases_old_access(self):
        entered = asyncio.Event()

        async def blocked(bound):
            entered.set()
            await asyncio.Event().wait()

        self.subject.probe = blocked
        task = asyncio.create_task(self.invoke())
        await asyncio.wait_for(entered.wait(), 1)
        bound = self.subject.bound
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.runtime.issuer._messages)
        with self.assertRaises(ygl.InvalidInvocation) as raised:
            await bound.message.read()
        self.safe(raised.exception)
        self.assertEqual(self.subject.messages, [])

    async def test_s2a_gateway_timeout_then_read_rejects(self):
        self.gateway._handler_timeout = 0.01

        async def blocked(_):
            await asyncio.Event().wait()

        self.subject.probe = blocked
        result = await self.invoke()
        self.assertIs(result.status, ygl.ResultStatus.ERROR)
        self.assertFalse(self.runtime.issuer._messages)
        with self.assertRaises(ygl.InvalidInvocation):
            await self.subject.bound.message.read()

    async def test_s2a_receiving_limits_and_public_error_chain(self):
        for text in ("x" * 16385, "\ud800"):
            source = _Source(text=text)
            with self.subTest(size=len(text)):
                with self.assertRaises(ygl.InvalidInvocation) as raised:
                    await self.enter(source)
                self.safe(raised.exception)
        source = _Source()
        view = await self.enter(source)
        bound = await self.subject.services.scopes.bind(view)
        try:

            def getter(*_):
                raise RuntimeError("internal getter canary")

            self.host.current_override = getter
            with self.assertRaises(ygl.InvalidInvocation) as raised:
                await bound.message.read()
            self.safe(raised.exception)
        finally:
            self.host.current_override = None
            self.runtime.issuer.release(view)
        self.assertEqual(self.subject.calls, [])

    async def test_s2a_public_web_bind_read_unavailable_and_command_still_runs(self):
        from ygl_test_subject.core.context_issuer import ContextIssuer

        from tests.contracts.test_context_issuer import _PublicWebProofs

        async def no_message(bound):
            with self.assertRaises(ygl.ServiceUnavailable) as raised:
                await bound.message.read()
            self.safe(raised.exception)

        self.subject.probe = no_message
        module = self.runtime.registry.snapshot().module("sample/alpha")
        command = self.runtime.issuer.issue(
            origin=ygl.InvocationOrigin.COMMAND,
            module_id=module.module_id,
            module_epoch=module.epoch,
            registry_revision=self.runtime.registry.snapshot().revision,
            actor_id="alice",
            conversation_id="room",
            adapter_id="offline",
            capability_id="read",
        )
        try:
            result = await self.gateway.invoke_command(command, "read", {})
            self.assertIs(result.status, ygl.ResultStatus.SUCCESS)
        finally:
            self.runtime.issuer.release(command)
        proofs = _PublicWebProofs()
        issuer = ContextIssuer(
            public_web_validator=proofs,
            public_web_capabilities=frozenset({("sample/alpha", "read")}),
        )
        web_handler = handler()
        web_handler.probe = no_message
        alpha = dataclasses.replace(
            module.manifest,
            tools=(),
            capabilities=(
                dataclasses.replace(
                    module.manifest.capabilities[0],
                    invocation_policy=ygl.InvocationPolicy.COMMAND_AND_PUBLIC_WEB,
                ),
                module.manifest.capabilities[1],
            ),
        )
        beta = self.runtime.registry.snapshot().module("sample/beta").manifest
        registry = Registry()
        registry.register_package(
            ygl.PackageManifest(
                "sample",
                "1.0.0",
                ygl.MODULE_ABI_VERSION,
                (alpha, beta),
                "tests",
                "MIT",
                "offline",
            ),
            {
                "alpha": ygl.ModuleHandlers(
                    {"read": web_handler, "private.read": _B03Handler("private.read")},
                    {"account-collector": _Collector()},
                    {},
                ),
                "beta": ygl.ModuleHandlers({"read": _B03Handler()}, {}, {}),
            },
        )
        web_runtime = await build_runtime(
            self.root / "web", registry=registry, issuer=issuer
        )
        web_handler.services = web_runtime.services.for_module("sample/alpha")
        gateway = Gateway(
            registry,
            issuer,
            admission=web_runtime.lifecycle.admission,
            lifecycle=web_runtime.lifecycle,
        )
        try:
            result = await gateway.invoke_public_web(
                proofs.new("sample/alpha", "read"), "sample/alpha", "read", {}
            )
            self.assertIs(result.status, ygl.ResultStatus.SUCCESS)
            self.assertIsNone(web_handler.calls[0].actor_id)
            self.assertIsNone(web_handler.calls[0].conversation_id)
            self.assertEqual(web_handler.messages, [])
        finally:
            gateway.fence_public_web()
            await gateway.drain_public_web(1)
            await web_runtime.services.close_credentials()
            await web_runtime.database.executor.close(timeout=2)


class MessageDeclarationTests(unittest.IsolatedAsyncioTestCase):
    async def test_s2a_public_surface_and_descriptive_dto(self):
        self.assertTrue(hasattr(ygl.InvocationServices, "message"))
        self.assertTrue(hasattr(ygl, "MessageAccess"))
        dto = ygl.MessageContext("source canary", "correlation canary")
        self.assertEqual(dataclasses.replace(dto), dto)
        self.assertNotIn("canary", repr(dto))
        self.assertEqual(
            {f.name for f in dataclasses.fields(dto)}, {"text", "event_ref"}
        )


class MessageGatewayAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_s2a_actual_gateway_plain_issuer_no_message_never_enters(self):
        fixture = invocation_fixture.GatewayTests()
        fixture.setUp()
        subject, command = await fixture._ready()
        fixture.issuer.release(command)
        module = fixture.registry.snapshot().module("testpkg/records")
        view = fixture.issuer.issue(
            origin=ygl.InvocationOrigin.LLM_TOOL,
            module_id=module.module_id,
            module_epoch=module.epoch,
            registry_revision=fixture.registry.snapshot().revision,
            actor_id="alice",
            conversation_id="room",
            capability_id="record.query",
        )
        try:
            result = await fixture._gateway().invoke_tool(
                view, "record_query", {"id": "one"}
            )
            self.assertIs(result.status, ygl.ResultStatus.ERROR)
            self.assertEqual(subject.calls, [])
        finally:
            fixture.issuer.release(view)
