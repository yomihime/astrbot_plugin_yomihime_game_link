"""B09 real public scheduled lease; controlled Host bootstrap, no native start."""

import unittest
from datetime import UTC, datetime, timedelta

from ygl_test_subject.core.lifecycle import _ServiceLifetime
from ygl_test_subject.core.ports import CollectionRunRequest
from ygl_test_subject.infrastructure.sqlite.repositories_subscriptions import (
    SQLiteSchedulerRepository,
)

import yomihime_game_link_sdk as ygl
from tests.fixtures.b04_runtime import (
    initialize_subscription_gate_fixture,
    synthetic_subscription_gate_bindings,
)
from tests.host import test_message_context as host_fixture


class ScheduledMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_s2a_public_scheduled_scope_has_unavailable_message(self):
        fixture = host_fixture.ControlledMessageTests(
            "test_s2a_controlled_host_to_real_core_and_ff14"
        )
        await fixture.asyncSetUp()
        try:
            await fixture.start()
            runtime = fixture.b03
            snapshot = runtime.registry.snapshot()
            module = snapshot.module("ff14/ff14")
            descriptor = next(
                d
                for d in module.manifest.schedules
                if d.collector_id == "ff14.calendar.collect"
            )
            self.assertIs(descriptor.shared_scope, ygl.OwnershipKind.PUBLIC)
            now = datetime.now(UTC)
            bindings = synthetic_subscription_gate_bindings(("ff14/ff14",))
            await initialize_subscription_gate_fixture(runtime.database, bindings, now)
            scheduler = SQLiteSchedulerRepository(
                runtime.database, subscription_gate_bindings=bindings
            )
            key = ygl.CollectionKey(
                module.module_id,
                descriptor.collector_id,
                descriptor.key_version,
                descriptor.source_id,
                ygl.NormalizedInput({"region": "cn"}),
                ygl.OwnerScope.public(),
            )
            request = CollectionRunRequest(
                key,
                now - timedelta(seconds=1),
                descriptor.default_interval_seconds,
                descriptor.data_version,
                module.epoch,
                snapshot.revision,
            )
            execution = await scheduler.claim_due(request, now=now)
            self.assertIsNotNone(execution)
            runtime.execution_claim_proofs.prove(execution)
            lease = runtime.lifecycle.admission.admit_schedule(
                execution, descriptor.collector_id
            )
            view = runtime.issuer.issue(
                origin=ygl.InvocationOrigin.SCHEDULER,
                module_id=module.module_id,
                module_epoch=module.epoch,
                registry_revision=snapshot.revision,
                capability_id=None,
            )
            runtime.issuer.attach_lease(view, lease)
            try:
                bound = await runtime.services.for_module(module.module_id).scopes.bind(
                    view
                )
                with self.assertRaises(ygl.ServiceUnavailable) as error:
                    await bound.message.read()
                self.assertIsNone(error.exception.__cause__)
                self.assertIsNone(error.exception.__context__)
                self.assertIsNone(view.actor_id)
                self.assertIsNone(view.grant_id)
            finally:
                runtime.issuer.release(view)
        finally:
            await fixture.asyncTearDown()

    async def test_s2a_controlled_owned_tool_to_sdk_only_receiver_no_raw_event(self):
        from dataclasses import replace

        from tests.core import test_message_context as core_fixture
        from tests.fixtures.b03_runtime import _manifest, _RuntimeInstance

        fixture = host_fixture.ControlledMessageTests(
            "test_s2a_controlled_host_to_real_core_and_ff14"
        )
        await fixture.asyncSetUp()
        try:
            await fixture.start()
            runtime = fixture.b03
            namespace = {}
            source = core_fixture._MODULE.replace(
                'document=ygl.DisplayDocument("Safe", "Result", (ygl.TextBlock("ok"),)))',
                'document=ygl.DisplayDocument("Safe", "Result", (ygl.TextBlock("ok"),)), model_facts=ygl.FactDocument({"ok":True}))',
            )
            exec(source, namespace)
            consumer = namespace["Handler"]()
            manifest = replace(
                _manifest("message"),
                tools=(ygl.ToolDescriptor("sdk_message_read", "read", {}, "read"),),
            )
            handlers = ygl.ModuleHandlers({"read": consumer}, {}, {})
            runtime.registry.register_package(
                ygl.PackageManifest(
                    "sdk_message",
                    "1.0.0",
                    ygl.MODULE_ABI_VERSION,
                    (manifest,),
                    "tests",
                    "MIT",
                    "SDK-only offline consumer",
                ),
                {"message": handlers},
            )
            lifetime = _ServiceLifetime(
                ("sdk_message", "sdk_message/message", "sdk-install")
            )
            consumer.services = runtime.services.for_candidate(
                "sdk_message/message", manifest, service_lifetime=lifetime
            )
            instance = _RuntimeInstance(handlers, manifest)
            adopted = runtime.lifecycle.adopt_candidate(
                "sdk_message",
                manifest,
                "sdk-install",
                instance,
                service_lifetime=lifetime,
            )
            runtime.lifecycle.install_dormant(
                "sdk_message", "sdk_message/message", "sdk-install", instance, adopted
            )
            identity, _ = await runtime.lifecycle.start_candidate(
                "sdk_message/message", "sdk-start"
            )
            await runtime.health.prepare("sdk_message/message", manifest)
            runtime.lifecycle.publish_committed_intent(
                "sdk_message/message",
                "sdk-start",
                identity,
                True,
                runtime.registry.snapshot().revision,
            )
            wrapper = fixture.wrapper("message-canary-token")
            try:
                tool = fixture.manager.get_full_tool_set().get_tool("sdk_message_read")
                output = await tool.call(wrapper)
                self.assertEqual(consumer.messages[-1].text, "message-canary-token")
                self.assertIs(type(consumer.messages[-1]), ygl.MessageContext)
                self.assertNotIn("message-canary-token", output)
                self.assertNotIn(consumer.messages[-1].event_ref, output)
                self.assertNotIn("message-canary-token", repr(consumer.messages[-1]))
                with self.assertRaises(ygl.InvalidInvocation) as error:
                    await consumer.bound.message.read()
                self.assertIsNone(error.exception.__cause__)
                self.assertIsNone(error.exception.__context__)
                first = consumer.messages[-1].event_ref
                await tool.call(wrapper)
                self.assertEqual(consumer.messages[-1].event_ref, first)
            finally:
                await runtime.lifecycle.stop("sdk_message/message")
        finally:
            await fixture.asyncTearDown()

    async def test_s2a_host_getter_error_is_safe_and_creates_no_receipt(self):
        fixture = host_fixture.ControlledMessageTests(
            "test_s2a_controlled_host_to_real_core_and_ff14"
        )
        await fixture.asyncSetUp()
        try:
            await fixture.start()
            handle = fixture.runtime.publisher.handles[0]

            class Failing(fixture.host.Event):
                def get_sender_id(self):
                    raise RuntimeError("private-getter-canary")

            event = Failing()
            event.platform_meta = fixture.wrapper().context.event.platform_meta
            before = len(fixture.receipts._records)
            with self.assertRaises(ygl.ServiceUnavailable) as error:
                fixture.runtime.receiver.make(
                    event, origin=ygl.InvocationOrigin.LLM_TOOL, tool_handle=handle
                )
            self.assertIs(type(error.exception), ygl.ServiceUnavailable)
            self.assertNotIn("canary", str(error.exception))
            self.assertIsNone(error.exception.__cause__)
            self.assertIsNone(error.exception.__context__)
            self.assertEqual(len(fixture.receipts._records), before)
        finally:
            await fixture.asyncTearDown()
