"""B04-D delivery state-machine probes using real SQLite repositories."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.core.context_issuer import ContextIssuer
from ygl_test_subject.core.contracts.services import Grant, GrantStatus
from ygl_test_subject.core.contracts.subscriptions import (
    DeliveryEvent,
    DeliveryState,
    DigestEnvelope,
    DigestEnvelopeState,
    DigestMemberAssociation,
    DigestMemberDisposition,
    DigestWindow,
    SubscriptionRecord,
    SubscriptionStatus,
    delivery_idempotency_key,
)
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.lifecycle import LifecycleController
from ygl_test_subject.core.ports import MessageReceipt, MessageStatus
from ygl_test_subject.core.registry import Registry
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories_subscriptions import (
    SQLiteDeliveryRepository,
    SQLiteDigestWindowRepository,
    SQLiteSubscriptionStore,
)
from ygl_test_subject.services.delivery import DeliveryService
from ygl_test_subject.services.output import LifecycleApprovedSendScheduler

from tests.fixtures.b04_runtime import (
    _run_async_from_sync,
    create_digest_envelope_fixture,
    create_subscription_event_fixture,
    initialize_subscription_gate_fixture,
    replace_subscription_gate_fixture,
    synthetic_subscription_gate_bindings,
)
from yomihime_game_link_sdk.declarations import (
    CapabilityDescriptor,
    CapabilityEffect,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
    SourceDeclaration,
)
from yomihime_game_link_sdk.display import (
    DigestMember,
    DisplayDocument,
    DisplayLimits,
    DisplayOutput,
    Privacy,
    TextBlock,
)
from yomihime_game_link_sdk.services import (
    CapabilityHealth,
    HealthReport,
    HealthStatus,
    ModuleHandlers,
)
from yomihime_game_link_sdk.storage import GrantReference, OwnerScope, OwnershipKind
from yomihime_game_link_sdk.subscriptions import (
    CollectionKey,
    ConversationKind,
    ConversationRef,
    DigestScheduleProfile,
    DstFoldPolicy,
    DstGapPolicy,
    NormalizedInput,
    ScheduleDescriptor,
    ScheduleTrigger,
)
from yomihime_game_link_sdk.version import MODULE_ABI_VERSION


async def _resolved(value):
    return value


async def _nothing(*_args, **_kwargs):
    return None


class _Routes:
    def __init__(self) -> None:
        self.private_calls = 0
        self.current_calls = 0
        self.reject_at: int | None = None

    async def resolve_current(self, _owner, recipient):
        self.current_calls += 1
        return None if self.reject_at == self.current_calls else recipient

    async def resolve_private(self, _owner, recipient):
        self.private_calls += 1
        return None if self.reject_at == self.private_calls else recipient


class _Renderer:
    def __init__(self, text: str | None = None) -> None:
        self.text = text
        self.calls = []

    async def render(self, document, *, limits, audience):
        self.calls.append((document, audience))
        return validate_contract(DisplayOutput(self.text or document.title))

    async def render_batch(self, batch, limits):
        self.calls.append(batch)
        return validate_contract(
            DisplayOutput("digest:" + ",".join(x.document.title for x in batch.members))
        )


class _MessagePort:
    def __init__(self, receipt=None, error=None) -> None:
        self.receipt = receipt or MessageReceipt(MessageStatus.ACCEPTED, "platform-1")
        self.error = error
        self.calls = []

    async def send(self, target, payload):
        self.calls.append((target, payload))
        if self.error:
            raise self.error
        return self.receipt


class _Grants:
    def __init__(self, grant=None) -> None:
        self.grant = grant

    async def current_grant(self, _grant_id):
        return self.grant


class _GatedDeliveryRepository:
    """Pause after the real event SENDING transaction commits."""

    def __init__(self, repository) -> None:
        self.repository = repository
        self.committed = asyncio.Event()
        self.release = asyncio.Event()

    async def claim_sending(self, *args, **kwargs):
        value = await self.repository.claim_sending(*args, **kwargs)
        self.committed.set()
        await self.release.wait()
        return value

    def __getattr__(self, name):
        return getattr(self.repository, name)


class _GatedDigestWindows:
    """Pause after the real digest SENDING transaction commits."""

    def __init__(self, repository) -> None:
        self.repository = repository
        self.committed = asyncio.Event()
        self.release = asyncio.Event()

    async def begin_envelope_send(self, *args, **kwargs):
        value = await self.repository.begin_envelope_send(*args, **kwargs)
        self.committed.set()
        await self.release.wait()
        return value

    def __getattr__(self, name):
        return getattr(self.repository, name)


class _DeliveryHandler:
    async def invoke(self, *_args):
        return None


class _DeliveryCollector:
    def normalize(self, parameters):
        return validate_contract(NormalizedInput(dict(parameters)))

    async def collect(self, *_args):
        return None


class _DeliveryModuleInstance:
    def __init__(self, handlers):
        self._handlers = handlers

    def handlers(self):
        return self._handlers

    async def start(self):
        return None

    async def stop(self):
        return None

    async def check_health(self):
        return validate_contract(
            HealthReport(
                {"read": validate_contract(CapabilityHealth(HealthStatus.AVAILABLE))}
            )
        )


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "delivery.sqlite3"
        self.db = SQLiteDatabase(self.path)
        self.bindings = synthetic_subscription_gate_bindings(("sample/game",))
        self.deliveries = SQLiteDeliveryRepository(
            self.db, subscription_gate_bindings=self.bindings
        )
        self.windows = SQLiteDigestWindowRepository(
            self.db, subscription_gate_bindings=self.bindings
        )
        self.subscriptions = SQLiteSubscriptionStore(self.db)
        self.now = datetime(2026, 9, 25, 12, tzinfo=UTC)
        self.registry = Registry()
        self.issuer = ContextIssuer()
        capability = validate_contract(
            CapabilityDescriptor(
                "read",
                {"type": "object", "properties": {}, "required": []},
                InvocationPolicy.COMMAND_ONLY,
                CapabilityEffect.READ_ONLY,
            )
        )
        schedule = validate_contract(
            ScheduleDescriptor(
                "collector",
                1,
                "source",
                1,
                {"type": "object", "properties": {}, "required": []},
                OwnershipKind.USER,
                ScheduleTrigger.PERIODIC,
                60,
                60,
                "cadence",
            )
        )
        manifest = validate_contract(
            ModuleManifest(
                "game",
                "game",
                ModuleCategory.GAME,
                "tests:DeliveryFixture",
                "1.0.0",
                (capability,),
                schedules=(schedule,),
                sources=(
                    validate_contract(SourceDeclaration("source", "example.test")),
                ),
            )
        )
        handlers = validate_contract(
            ModuleHandlers(
                {"read": _DeliveryHandler()}, {"collector": _DeliveryCollector()}, {}
            )
        )
        self.registry.register_package(
            validate_contract(
                PackageManifest(
                    "sample",
                    "1.0.0",
                    MODULE_ABI_VERSION,
                    (manifest,),
                    "Tests",
                    "MIT",
                    "offline",
                )
            ),
            {"game": handlers},
        )
        self.lifecycle = LifecycleController(self.registry, issuer=self.issuer)
        instance = _DeliveryModuleInstance(handlers)
        install_operation = "delivery-test-install"
        captured = self.lifecycle.adopt_candidate(
            "sample", manifest, install_operation, instance
        )
        self.lifecycle.install_dormant(
            "sample", "sample/game", install_operation, instance, captured
        )
        start_operation = "delivery-test-start"
        identity, _ = _run_async_from_sync(
            self.lifecycle.start_candidate("sample/game", start_operation)
        )
        self.lifecycle.publish_committed_intent(
            "sample/game",
            start_operation,
            identity,
            True,
            self.registry.snapshot().revision,
        )
        self.send_scheduler = LifecycleApprovedSendScheduler(self.lifecycle)
        self.routes = _Routes()
        self.renderer = _Renderer()
        self.port = _MessagePort()
        self.recipient = validate_contract(
            ConversationRef("adapter", ConversationKind.DIRECT, "u1", "private-u1")
        )
        self.scope = OwnerScope.user("u1")
        self.key = validate_contract(
            CollectionKey(
                "sample/game",
                "collector",
                1,
                "source",
                validate_contract(NormalizedInput({})),
                self.scope,
            )
        )
        self.record = SubscriptionRecord(
            subscription_id="sub-1",
            revision=1,
            module_id="sample/game",
            collection_key=self.key,
            owner_id="u1",
            grant=None,
            recipient=self.recipient,
            notification_mode="instant",
            filters={},
            status=SubscriptionStatus.ACTIVE,
        )
        self.event = self._event()
        self.service = self._service()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _event(self, *, key="event-1", title="saved-title", privacy=Privacy.PUBLIC):
        document = validate_contract(
            DisplayDocument(
                title,
                "subject",
                (validate_contract(TextBlock("saved body")),),
                privacy=privacy,
            )
        )
        return DeliveryEvent(
            key,
            1,
            self.record.subscription_id,
            self.record.revision,
            self.record.owner_id,
            self.record.grant,
            self.record.recipient,
            document,
            delivery_idempotency_key(
                key, 1, self.record.subscription_id, 1, self.recipient
            ),
        )

    def _service(self, *, port=None, renderer=None):
        return DeliveryService(
            deliveries=self.deliveries,
            subscriptions=self.subscriptions,
            grants=_Grants(),
            windows=self.windows,
            registry=self.registry,
            admission=self.lifecycle.admission,
            send_scheduler=self.send_scheduler,
            routes=self.routes,
            renderer=renderer or self.renderer,
            message_port=port or self.port,
            limits=validate_contract(DisplayLimits(2, 1024)),
            now=lambda: self.now,
        )

    async def _disable(self) -> None:
        operation = "delivery-test-disable"
        identity = self.lifecycle.quiesce("sample/game", operation, "test_disable")
        self.lifecycle.publish_committed_intent(
            "sample/game",
            operation,
            identity,
            False,
            self.registry.snapshot().revision,
        )
        await self.lifecycle.stop_candidate("sample/game", operation, 1.0)

    async def asyncSetUp(self) -> None:
        await initialize_subscription_gate_fixture(
            self.db, self.bindings, self.now - timedelta(days=2)
        )
        await self.subscriptions.create(self.record)
        await create_subscription_event_fixture(
            self.deliveries, self.bindings, self.event
        )

    async def test_accepted_receipt_is_persisted_for_exact_subscriber(self) -> None:
        result = await self.service.dispatch_event("event-1", 1, "sub-1", 1)
        saved = await self.deliveries.current_event(
            "event-1", 1, subscription_id="sub-1", subscription_revision=1
        )
        self.assertEqual(result.state, DeliveryState.SENT)
        self.assertEqual(saved.state, DeliveryState.SENT)
        self.assertEqual(saved.attempt.platform_message_id, "platform-1")
        self.assertEqual(len(self.port.calls), 1)
        self.assertEqual(self.port.calls[0][1].text, "saved-title")

    async def test_eligibility_structure_rejection_preserves_authority_and_cancel(self):
        from yomihime_game_link_sdk.errors import AccessDenied

        async def check(recipient, privacy):
            return await self.service._eligibility(
                "sub-1", 1, "u1", None, recipient, privacy, self.now
            )

        self.assertTrue((await check(self.recipient, Privacy.PUBLIC)).allowed)
        before = await self.subscriptions.current("sub-1")
        for recipient, privacy in (
            (replace(self.recipient, kind=None), Privacy.PUBLIC),
            (self.recipient, replace(self.event.display_data, privacy=None)),
        ):
            with self.subTest(recipient_kind=recipient.kind, privacy=privacy):
                result = await check(recipient, privacy)
                self.assertFalse(result.allowed)
                self.assertEqual(result.code, "privacy_rejected")
        self.assertEqual(await self.subscriptions.current("sub-1"), before)
        self.assertEqual(self.port.calls, [])
        for failure in (
            AccessDenied(),
            RuntimeError("internal"),
            asyncio.CancelledError(),
        ):
            with self.subTest(failure=type(failure).__name__):
                with (
                    patch(
                        "ygl_test_subject.services.delivery.validate_contract",
                        side_effect=failure,
                    ),
                    self.assertRaises(type(failure)),
                ):
                    await check(self.recipient, Privacy.PUBLIC)
        self.assertEqual(await self.subscriptions.current("sub-1"), before)
        self.assertEqual(self.port.calls, [])

    async def test_scheduled_pause_resume_aborts_before_actual_io(self):
        scheduled = self.send_scheduler.schedule
        pauses = []

        async def pause_resume():
            async with self.lifecycle.admission.mutation("test-pause-resume"):
                await replace_subscription_gate_fixture(
                    self.db, self.bindings, "sample/game", False, self.now
                )
                await replace_subscription_gate_fixture(
                    self.db, self.bindings, "sample/game", True, self.now
                )

        def schedule(permit, work, *, name):
            pauses.append(asyncio.create_task(pause_resume()))
            scheduled(permit, work, name=name)

        with patch.object(self.send_scheduler, "schedule", schedule):
            result = await self.service.dispatch_event("event-1", 1, "sub-1", 1)
        await asyncio.gather(*pauses)
        saved = await self.deliveries.current_event(
            "event-1", 1, subscription_id="sub-1", subscription_revision=1
        )
        self.assertEqual(result.state, DeliveryState.CANCELLED)
        self.assertEqual(saved.attempt.error_code, "cancelled_before_dispatch")
        self.assertEqual(self.port.calls, [])

    async def test_io_entry_precedes_waiting_mutation_and_receipt_survives_pause(self):
        proof = asyncio.Event()
        release_proof = asyncio.Event()
        entered = asyncio.Event()
        release_io = asyncio.Event()
        order = []
        current = self.deliveries.is_current_for_send

        async def gated_current(event):
            result = await current(event)
            proof.set()
            await release_proof.wait()
            return result

        class Port(_MessagePort):
            async def send(port, target, payload):
                order.append("io")
                entered.set()
                await release_io.wait()
                return await super().send(target, payload)

        async def pause():
            async with self.lifecycle.admission.mutation("test-pause"):
                await replace_subscription_gate_fixture(
                    self.db, self.bindings, "sample/game", False, self.now
                )
                order.append("pause")

        port = Port()
        with patch.object(self.deliveries, "is_current_for_send", gated_current):
            dispatch = asyncio.create_task(
                self._service(port=port).dispatch_event("event-1", 1, "sub-1", 1)
            )
            waiter = None
            try:
                await asyncio.wait_for(proof.wait(), 2)
                waiter = asyncio.create_task(pause())
                release_proof.set()
                await asyncio.wait_for(entered.wait(), 2)
                await asyncio.wait_for(waiter, 2)
                self.assertFalse(dispatch.done())
                self.assertEqual(order, ["io", "pause"])
                release_io.set()
                result = await dispatch
            finally:
                release_proof.set()
                release_io.set()
                await asyncio.gather(
                    dispatch,
                    *(() if waiter is None else (waiter,)),
                    return_exceptions=True,
                )
        saved = await self.deliveries.current_event(
            "event-1", 1, subscription_id="sub-1", subscription_revision=1
        )
        self.assertEqual(result.state, DeliveryState.SENT)
        self.assertEqual(saved.state, DeliveryState.SENT)
        self.assertEqual(len(port.calls), 1)

    async def test_timeout_before_io_entry_is_known_unsent(self):
        proof = asyncio.Event()
        release = asyncio.Event()
        current = self.deliveries.is_current_for_send

        async def gated_current(event):
            value = await current(event)
            proof.set()
            await release.wait()
            return value

        service = self._service()
        service._send_timeout = 0.05
        with patch.object(self.deliveries, "is_current_for_send", gated_current):
            dispatch = asyncio.create_task(
                service.dispatch_event("event-1", 1, "sub-1", 1)
            )
            try:
                await asyncio.wait_for(proof.wait(), 2)
                await asyncio.sleep(0.1)
                self.assertFalse(dispatch.done())
                release.set()
                result = await dispatch
            finally:
                release.set()
                await asyncio.gather(dispatch, return_exceptions=True)
        self.assertEqual(result.state, DeliveryState.CANCELLED)
        self.assertEqual(self.port.calls, [])
        saved = await self.deliveries.current_event(
            "event-1", 1, subscription_id="sub-1", subscription_revision=1
        )
        self.assertEqual(saved.attempt.error_code, "cancelled_before_dispatch")

    async def test_repeated_caller_cancel_drains_io_before_unknown_archive(self):
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        release = asyncio.Event()

        class Port(_MessagePort):
            async def send(port, target, payload):
                port.calls.append((target, payload))
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    await release.wait()
                    raise

        port = Port()
        dispatch = asyncio.create_task(
            self._service(port=port).dispatch_event("event-1", 1, "sub-1", 1)
        )
        try:
            await asyncio.wait_for(entered.wait(), 2)
            dispatch.cancel()
            await asyncio.wait_for(cancelled.wait(), 2)
            dispatch.cancel()
            await asyncio.sleep(0)
            self.assertFalse(dispatch.done())
            saved = await self.deliveries.current_event(
                "event-1", 1, subscription_id="sub-1", subscription_revision=1
            )
            self.assertEqual(saved.state, DeliveryState.SENDING)
            async with self.lifecycle.admission.mutation("test-during-drain"):
                await replace_subscription_gate_fixture(
                    self.db, self.bindings, "sample/game", False, self.now
                )
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await dispatch
        finally:
            release.set()
            await asyncio.gather(dispatch, return_exceptions=True)
        saved = await self.deliveries.current_event(
            "event-1", 1, subscription_id="sub-1", subscription_revision=1
        )
        self.assertEqual(saved.state, DeliveryState.UNKNOWN)
        self.assertEqual(len(port.calls), 1)

    async def test_unstarted_owned_sender_or_io_child_is_drained_and_aborted(self):
        loop = asyncio.get_running_loop()
        original_factory = loop.get_task_factory()
        for name in ("runner", "io"):
            with self.subTest(name=name):
                key = "unstarted-" + name
                await create_subscription_event_fixture(
                    self.deliveries, self.bindings, self._event(key=key)
                )
                tasks = []

                def factory(event_loop, work, context=None):
                    task = asyncio.Task(work, loop=event_loop, context=context)
                    if work.cr_code.co_name == name:
                        tasks.append(task)
                        task.cancel()
                    return task

                loop.set_task_factory(factory)
                try:
                    result = await asyncio.wait_for(
                        self.service.dispatch_event(key, 1, "sub-1", 1), 2
                    )
                finally:
                    loop.set_task_factory(original_factory)
                saved = await self.deliveries.current_event(
                    key, 1, subscription_id="sub-1", subscription_revision=1
                )
                self.assertEqual(result.state, DeliveryState.CANCELLED)
                self.assertEqual(saved.attempt.error_code, "cancelled_before_dispatch")
                self.assertTrue(tasks and all(task.done() for task in tasks))
                async with self.lifecycle.admission.mutation("test-after-abort"):
                    pass
        self.assertEqual(self.port.calls, [])

    async def _digest_receipt_fixture(self, suffix):
        event = self._event(key="receipt-" + suffix)
        member = validate_contract(
            DigestMember(
                event.subscription_id,
                event.subscription_revision,
                event.event_key,
                event.event_version,
            )
        )
        due = self.now - timedelta(minutes=1)
        window = DigestWindow(
            "receipt-window-" + suffix,
            "UTC",
            "daily",
            self.now - timedelta(hours=2),
            due,
            due,
            DstFoldPolicy.FIRST_OCCURRENCE,
            DstGapPolicy.SKIP,
            (member,),
        )
        await self.windows.create(window)
        await create_digest_envelope_fixture(
            self.deliveries,
            self.bindings,
            DigestEnvelope(
                "receipt-envelope-" + suffix,
                window.window_id,
                self.recipient,
                (member,),
                member_associations=(
                    DigestMemberAssociation(
                        window.window_id, self.recipient, member, event
                    ),
                ),
            ),
        )
        return window, event

    async def _assert_cancelled_receipt_archived(self, *, digest, scope_cancel, status):
        entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        children, send_work = [], []
        service = None

        class Port(_MessagePort):
            async def send(port, target, payload):
                port.calls.append((target, payload))
                if not scope_cancel:
                    # The outer wait already captured 30s. Only the IO-owner's
                    # timeout, installed after this entry handshake, is shortened.
                    service._send_timeout = 0.03
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    await release.wait()
                    return MessageReceipt(status, "receipt-after-cancel")

        loop = asyncio.get_running_loop()
        previous_factory = loop.get_task_factory()

        def factory(event_loop, work, context=None):
            task = asyncio.Task(work, loop=event_loop, context=context)
            if work.cr_code.co_name == "io":
                children.append(task)
            elif work.cr_code.co_name == "sender":
                send_work.append(task)
            return task

        port = Port()
        service = self._service(port=port)
        window, event = (
            await self._digest_receipt_fixture("cancel")
            if digest
            else (None, self.event)
        )
        loop.set_task_factory(factory)
        dispatch = asyncio.create_task(
            service.dispatch_digest(window.window_id, self.recipient, now=self.now)
            if digest
            else service.dispatch_event(event.event_key, 1, event.subscription_id, 1)
        )
        scope = self.lifecycle.scope("sample/game")
        try:
            await asyncio.wait_for(entered.wait(), 2)
            if scope_cancel:
                scope.cancel()
            await asyncio.wait_for(cancelled.wait(), 2)
            if scope_cancel:
                scope.cancel()
            await asyncio.sleep(0)
            self.assertFalse(dispatch.done())
            self.assertTrue(children and not children[0].done())
            self.assertTrue(send_work and not send_work[0].done())
            if digest:
                sending = await self.windows.current_envelope(
                    window.window_id, self.recipient
                )
                self.assertEqual(sending.state, DigestEnvelopeState.SENDING)
            else:
                sending = await self.deliveries.current_event(
                    event.event_key,
                    1,
                    subscription_id=event.subscription_id,
                    subscription_revision=1,
                )
                self.assertEqual(sending.state, DeliveryState.SENDING)
            release.set()
            result = await asyncio.wait_for(dispatch, 2)
            await scope.wait(timeout=2)
        finally:
            release.set()
            loop.set_task_factory(previous_factory)
            await asyncio.gather(
                dispatch, *children, *send_work, return_exceptions=True
            )
        expected = (
            DeliveryState.SENT
            if status is MessageStatus.ACCEPTED
            else DeliveryState.FAILED
        )
        self.assertEqual(result.state, expected)
        self.assertEqual(len(port.calls), 1)
        self.assertEqual(
            children[0].result(), MessageReceipt(status, "receipt-after-cancel")
        )
        if scope_cancel:
            self.assertTrue(
                send_work[0].cancelled(),
                "original owned cancellation must propagate after archival",
            )
        else:
            self.assertFalse(send_work[0].cancelled())
        if digest:
            saved = await self.windows.current_envelope(
                window.window_id, self.recipient
            )
            self.assertEqual(
                saved.state,
                DigestEnvelopeState.SENT
                if expected is DeliveryState.SENT
                else DigestEnvelopeState.FAILED,
            )
            attempt = saved.delivery_attempts[-1]
            self.assertEqual(
                attempt.started_at, sending.delivery_attempts[-1].started_at
            )
        else:
            saved = await self.deliveries.current_event(
                event.event_key,
                1,
                subscription_id=event.subscription_id,
                subscription_revision=1,
            )
            self.assertEqual(saved.state, expected)
            attempt = saved.attempt
            self.assertEqual(attempt.started_at, sending.attempt.started_at)
        self.assertEqual(attempt.state, expected)
        self.assertEqual(attempt.platform_message_id, "receipt-after-cancel")
        self.assertEqual(attempt.attempt_number, 1)
        self.assertEqual(
            attempt.idempotency_key,
            event.idempotency_key
            if not digest
            else sending.delivery_attempts[-1].idempotency_key,
        )

    async def test_owned_instant_cancel_preserves_accepted_receipt(self):
        await self._assert_cancelled_receipt_archived(
            digest=False, scope_cancel=True, status=MessageStatus.ACCEPTED
        )

    async def test_owned_instant_cancel_preserves_failed_receipt(self):
        await self._assert_cancelled_receipt_archived(
            digest=False, scope_cancel=True, status=MessageStatus.FAILED
        )

    async def test_instant_internal_timeout_preserves_accepted_receipt(self):
        await self._assert_cancelled_receipt_archived(
            digest=False, scope_cancel=False, status=MessageStatus.ACCEPTED
        )

    async def test_instant_internal_timeout_preserves_failed_receipt(self):
        await self._assert_cancelled_receipt_archived(
            digest=False, scope_cancel=False, status=MessageStatus.FAILED
        )

    async def test_owned_digest_cancel_preserves_accepted_receipt(self):
        await self._assert_cancelled_receipt_archived(
            digest=True, scope_cancel=True, status=MessageStatus.ACCEPTED
        )

    async def test_owned_digest_cancel_preserves_failed_receipt(self):
        await self._assert_cancelled_receipt_archived(
            digest=True, scope_cancel=True, status=MessageStatus.FAILED
        )

    async def test_digest_internal_timeout_preserves_accepted_receipt(self):
        await self._assert_cancelled_receipt_archived(
            digest=True, scope_cancel=False, status=MessageStatus.ACCEPTED
        )

    async def test_digest_internal_timeout_preserves_failed_receipt(self):
        await self._assert_cancelled_receipt_archived(
            digest=True, scope_cancel=False, status=MessageStatus.FAILED
        )

    async def test_digest_claim_expiry_while_io_child_queues_is_known_unsent(self):
        base_now = self.now
        loop = asyncio.get_running_loop()
        previous_factory = loop.get_task_factory()
        current = self.windows.is_current_for_send
        for offset in (timedelta(0), timedelta(microseconds=1)):
            with self.subTest(offset=offset):
                self.now = base_now
                window, event = await self._digest_receipt_fixture(
                    "queued-" + str(offset)
                )
                expiries, callbacks = [], []

                async def proof(claim, envelope, *, now):
                    value = await current(claim, envelope, now=now)
                    self.assertTrue(value)
                    expiries.append(claim.expires_at)
                    return value

                def expire():
                    callbacks.append(True)
                    self.now = expiries[0] + offset

                def factory(event_loop, work, context=None):
                    if work.cr_code.co_name == "io":
                        event_loop.call_soon(expire)
                    return asyncio.Task(work, loop=event_loop, context=context)

                with patch.object(self.windows, "is_current_for_send", proof):
                    loop.set_task_factory(factory)
                    try:
                        result = await self.service.dispatch_digest(
                            window.window_id, self.recipient, now=self.now
                        )
                    finally:
                        loop.set_task_factory(previous_factory)
                self.assertEqual(callbacks, [True])
                self.assertEqual(
                    len(expiries), 1, "child must not add another SQL proof"
                )
                self.assertEqual(result.state, DeliveryState.CANCELLED)
                saved = await self.windows.current_envelope(
                    window.window_id, self.recipient
                )
                self.assertEqual(saved.state, DigestEnvelopeState.READY)
                self.assertEqual(
                    saved.delivery_attempts[-1].error_code, "cancelled_before_dispatch"
                )
        self.assertEqual(self.port.calls, [])

    async def test_private_grant_expiry_while_io_child_queues_is_known_unsent(self):
        base_now = self.now
        loop = asyncio.get_running_loop()
        previous_factory = loop.get_task_factory()
        for offset in (timedelta(0), timedelta(microseconds=1)):
            with self.subTest(offset=offset):
                self.now = base_now
                suffix = str(offset)
                grant_ref = validate_contract(
                    GrantReference("queued-grant-" + suffix, 1)
                )
                record = SubscriptionRecord(
                    "queued-private-" + suffix,
                    1,
                    "sample/game",
                    replace(self.key, scope=OwnerScope.authorized("u1", grant_ref)),
                    "u1",
                    grant_ref,
                    self.recipient,
                    "instant",
                    {},
                    SubscriptionStatus.ACTIVE,
                )
                await self.subscriptions.create(record)
                key = "queued-private-event-" + suffix
                event = DeliveryEvent(
                    key,
                    1,
                    record.subscription_id,
                    1,
                    "u1",
                    grant_ref,
                    self.recipient,
                    validate_contract(
                        DisplayDocument(
                            "private",
                            "subject",
                            (validate_contract(TextBlock("private body")),),
                            privacy=Privacy.PRIVATE,
                        )
                    ),
                    delivery_idempotency_key(
                        key, 1, record.subscription_id, 1, self.recipient
                    ),
                )
                await create_subscription_event_fixture(
                    self.deliveries, self.bindings, event
                )
                expires = base_now + timedelta(seconds=2)
                grant = Grant(
                    grant_ref.grant_id,
                    1,
                    "u1",
                    "sample/game",
                    "queued-account",
                    ("read",),
                    None,
                    GrantStatus.ACTIVE,
                    expires,
                )
                service = self._service()
                service._grants = _Grants(grant)
                callbacks = []

                def expire():
                    callbacks.append(True)
                    self.now = expires + offset

                def factory(event_loop, work, context=None):
                    if work.cr_code.co_name == "io":
                        event_loop.call_soon(expire)
                    return asyncio.Task(work, loop=event_loop, context=context)

                loop.set_task_factory(factory)
                try:
                    result = await service.dispatch_event(
                        key, 1, record.subscription_id, 1
                    )
                finally:
                    loop.set_task_factory(previous_factory)
                self.assertEqual(callbacks, [True])
                self.assertEqual(result.state, DeliveryState.CANCELLED)
                saved = await self.deliveries.current_event(
                    key,
                    1,
                    subscription_id=record.subscription_id,
                    subscription_revision=1,
                )
                self.assertEqual(saved.state, DeliveryState.CANCELLED)
                self.assertEqual(saved.attempt.error_code, "cancelled_before_dispatch")
        self.assertEqual(self.port.calls, [])

    async def test_host_exception_is_unknown_and_not_retried(self) -> None:
        port = _MessagePort(error=OSError("private transport detail"))
        service = self._service(port=port)
        result = await service.dispatch_event("event-1", 1, "sub-1", 1)
        saved = await self.deliveries.current_event(
            "event-1", 1, subscription_id="sub-1", subscription_revision=1
        )
        again = await service.dispatch_event("event-1", 1, "sub-1", 1)
        self.assertEqual(result.state, DeliveryState.UNKNOWN)
        self.assertEqual(saved.state, DeliveryState.UNKNOWN)
        self.assertEqual(again.state, DeliveryState.UNKNOWN)
        self.assertEqual(len(port.calls), 1)

    async def test_direct_event_dispatch_defers_exact_active_digest_without_rendering(
        self,
    ):
        digest_record = replace(
            self.record,
            subscription_id="sub-digest-direct",
            notification_mode="digest",
            type_id="sample-type",
            digest_schedule=validate_contract(
                DigestScheduleProfile(
                    "UTC",
                    "12:00",
                    86400,
                    DstFoldPolicy.FIRST_OCCURRENCE,
                    DstGapPolicy.SKIP,
                )
            ),
        )
        await self.subscriptions.create(digest_record)
        event_key = "event-digest-direct"
        event = DeliveryEvent(
            event_key,
            1,
            digest_record.subscription_id,
            digest_record.revision,
            digest_record.owner_id,
            digest_record.grant,
            digest_record.recipient,
            validate_contract(
                DisplayDocument(
                    "digest-only-title",
                    "subject",
                    (validate_contract(TextBlock("digest-only body")),),
                    privacy=Privacy.PUBLIC,
                )
            ),
            delivery_idempotency_key(
                event_key,
                1,
                digest_record.subscription_id,
                digest_record.revision,
                digest_record.recipient,
            ),
        )
        await create_subscription_event_fixture(self.deliveries, self.bindings, event)

        result = await self.service.dispatch_event(
            event.event_key,
            event.event_version,
            event.subscription_id,
            event.subscription_revision,
        )
        current = await self.deliveries.current_event(
            event.event_key,
            event.event_version,
            subscription_id=event.subscription_id,
            subscription_revision=event.subscription_revision,
        )
        self.assertEqual(result.state, DeliveryState.PENDING)
        self.assertEqual(current.state, DeliveryState.PENDING)
        self.assertEqual(self.renderer.calls, [])
        self.assertEqual(self.port.calls, [])

    async def test_subscription_mode_revision_before_claim_cancels_stale_event(self):
        revised = replace(
            self.record,
            revision=self.record.revision + 1,
            notification_mode="digest",
        )
        subscription_store = self.subscriptions
        expected_revision = self.record.revision

        class _ModeChangingRepository:
            def __init__(self, repository) -> None:
                self.repository = repository
                self.changed = False

            async def claim_sending(self, *args, **kwargs):
                if not self.changed:
                    self.changed = True
                    await subscription_store.revise(
                        revised, expected_revision=expected_revision
                    )
                return await self.repository.claim_sending(*args, **kwargs)

            def __getattr__(self, name):
                return getattr(self.repository, name)

        service = self._service()
        service._deliveries = _ModeChangingRepository(self.deliveries)
        result = await service.dispatch_event("event-1", 1, "sub-1", 1)
        saved = await self.deliveries.current_event(
            "event-1", 1, subscription_id="sub-1", subscription_revision=1
        )
        current_subscription = await self.subscriptions.current("sub-1")
        self.assertEqual(current_subscription.revision, 2)
        self.assertEqual(current_subscription.notification_mode, "digest")
        self.assertEqual(result.state, DeliveryState.CANCELLED)
        self.assertEqual(result.error_code, "subscription_revision_changed")
        self.assertEqual(saved.state, DeliveryState.CANCELLED)
        self.assertEqual(self.port.calls, [])

    async def test_concurrent_workers_claim_one_immediate_send(self) -> None:
        results = await asyncio.gather(
            self.service.dispatch_event("event-1", 1, "sub-1", 1),
            self.service.dispatch_event("event-1", 1, "sub-1", 1),
        )
        self.assertTrue(
            all(
                result.state in (DeliveryState.SENT, DeliveryState.SENDING)
                for result in results
            )
        )
        self.assertIn(DeliveryState.SENT, [result.state for result in results])
        self.assertEqual(len(self.port.calls), 1)

    async def test_send_timeout_is_unknown_and_not_retried(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        class HangingPort(_MessagePort):
            async def send(self, target, payload):
                self.calls.append((target, payload))
                entered.set()
                await release.wait()

        port = HangingPort()
        service = DeliveryService(
            deliveries=self.deliveries,
            subscriptions=self.subscriptions,
            grants=_Grants(),
            windows=self.windows,
            registry=self.registry,
            admission=self.lifecycle.admission,
            send_scheduler=self.send_scheduler,
            routes=self.routes,
            renderer=self.renderer,
            message_port=port,
            limits=validate_contract(DisplayLimits(2, 1024)),
            now=lambda: self.now,
            send_timeout=0.1,
        )
        dispatch = asyncio.create_task(service.dispatch_event("event-1", 1, "sub-1", 1))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            result = await dispatch
        finally:
            release.set()
            await asyncio.gather(dispatch, return_exceptions=True)
        again = await service.dispatch_event("event-1", 1, "sub-1", 1)
        self.assertEqual(result.state, DeliveryState.UNKNOWN)
        self.assertEqual(again.state, DeliveryState.UNKNOWN)
        self.assertEqual(len(port.calls), 1)

    async def test_digest_stamp_change_after_render_removes_member_and_rerenders(self):
        second = replace(self.record, subscription_id="sub-2")
        await self.subscriptions.create(second)
        obsolete = self._event(key="obsolete", title="obsolete-body")
        survivor = replace(
            self._event(key="survivor", title="visible-body"),
            subscription_id=second.subscription_id,
            idempotency_key=delivery_idempotency_key(
                "survivor", 1, second.subscription_id, 1, self.recipient
            ),
        )
        members = tuple(
            validate_contract(
                DigestMember(event.subscription_id, 1, event.event_key, 1)
            )
            for event in (obsolete, survivor)
        )
        due = self.now - timedelta(minutes=1)
        window = DigestWindow(
            "stamp-window",
            "UTC",
            "daily",
            self.now - timedelta(hours=2),
            due,
            due,
            DstFoldPolicy.FIRST_OCCURRENCE,
            DstGapPolicy.SKIP,
            members,
        )
        await self.windows.create(window)
        envelope = DigestEnvelope(
            "stamp-envelope",
            window.window_id,
            self.recipient,
            members,
            member_associations=tuple(
                DigestMemberAssociation(window.window_id, self.recipient, member, event)
                for member, event in zip(members, (obsolete, survivor))
            ),
        )
        await create_digest_envelope_fixture(self.deliveries, self.bindings, envelope)

        class Renderer(_Renderer):
            async def render_batch(inner, batch, limits):
                output = await super().render_batch(batch, limits)
                if len(inner.calls) == 1:
                    await self.db.executor.run_transaction(
                        lambda unit: unit.execute(
                            "UPDATE b04_delivery_events SET gate_revision=NULL,intent_revision=NULL WHERE event_key='obsolete'"
                        ).rowcount,
                        begin_mode="IMMEDIATE",
                    )
                return output

        renderer = Renderer()
        result = await self._service(renderer=renderer).dispatch_digest(
            window.window_id, self.recipient, now=self.now
        )
        self.assertEqual(result.state, DeliveryState.SENT)
        self.assertEqual(len(renderer.calls), 2)
        self.assertEqual(self.port.calls[0][1].text, "digest:visible-body")
        saved = await self.windows.current_envelope(window.window_id, self.recipient)
        self.assertEqual(saved.members, (members[1],))
        self.assertTrue(
            any(
                item.member == members[0]
                and item.disposition is DigestMemberDisposition.UNAUTHORIZED
                for item in saved.member_receipts
            )
        )

    async def test_disabled_module_blocks_before_render_or_send(self) -> None:
        await self._disable()
        result = await self.service.dispatch_event("event-1", 1, "sub-1", 1)
        self.assertEqual(result.state, DeliveryState.CANCELLED)
        self.assertEqual(self.renderer.calls, [])
        self.assertEqual(self.port.calls, [])

    async def test_epoch_change_during_render_blocks_send(self) -> None:
        class ChangingRenderer(_Renderer):
            async def render(inner, document, *, limits, audience):
                output = await super().render(
                    document, limits=limits, audience=audience
                )
                await self._disable()
                return output

        service = self._service(renderer=ChangingRenderer())
        result = await service.dispatch_event("event-1", 1, "sub-1", 1)
        saved = await self.deliveries.current_event(
            "event-1", 1, subscription_id="sub-1", subscription_revision=1
        )
        self.assertEqual(result.state, DeliveryState.CANCELLED)
        self.assertEqual(saved.state, DeliveryState.CANCELLED)
        self.assertEqual(self.port.calls, [])

    async def test_route_revocation_after_claim_is_cancelled_before_port(self) -> None:
        self.routes.reject_at = 3  # initial, post-render, then final post-claim guard
        result = await self.service.dispatch_event("event-1", 1, "sub-1", 1)
        saved = await self.deliveries.current_event(
            "event-1", 1, subscription_id="sub-1", subscription_revision=1
        )
        self.assertEqual(result.state, DeliveryState.CANCELLED)
        self.assertEqual(saved.state, DeliveryState.CANCELLED)
        self.assertEqual(self.port.calls, [])

    async def test_grant_expiry_during_sqlite_event_sending_cas_aborts_before_port(
        self,
    ):
        grant_ref = validate_contract(GrantReference("grant-event-expiry", 1))
        scope = OwnerScope.authorized("u1", grant_ref)
        key = validate_contract(
            CollectionKey(
                "sample/game",
                "collector",
                1,
                "source",
                validate_contract(NormalizedInput({})),
                scope,
            )
        )
        record = SubscriptionRecord(
            subscription_id="sub-expiry",
            revision=1,
            module_id="sample/game",
            collection_key=key,
            owner_id="u1",
            grant=grant_ref,
            recipient=self.recipient,
            notification_mode="instant",
            filters={},
            status=SubscriptionStatus.ACTIVE,
        )
        await self.subscriptions.create(record)
        event = DeliveryEvent(
            "event-expiry",
            1,
            record.subscription_id,
            1,
            record.owner_id,
            grant_ref,
            record.recipient,
            validate_contract(
                DisplayDocument(
                    "private title",
                    "subject",
                    (validate_contract(TextBlock("private body")),),
                    privacy=Privacy.PRIVATE,
                )
            ),
            delivery_idempotency_key(
                "event-expiry", 1, record.subscription_id, 1, record.recipient
            ),
        )
        await create_subscription_event_fixture(self.deliveries, self.bindings, event)
        expires_at = self.now + timedelta(seconds=2)
        grant = Grant(
            grant_ref.grant_id,
            grant_ref.revision,
            "u1",
            "sample/game",
            "account-expiry",
            ("read",),
            None,
            GrantStatus.ACTIVE,
            expires_at,
        )
        gated = _GatedDeliveryRepository(self.deliveries)
        service = DeliveryService(
            deliveries=gated,
            subscriptions=self.subscriptions,
            grants=_Grants(grant),
            windows=self.windows,
            registry=self.registry,
            admission=self.lifecycle.admission,
            send_scheduler=self.send_scheduler,
            routes=self.routes,
            renderer=self.renderer,
            message_port=self.port,
            limits=validate_contract(DisplayLimits(2, 1024)),
            now=lambda: self.now,
        )

        dispatch = asyncio.create_task(
            service.dispatch_event("event-expiry", 1, "sub-expiry", 1)
        )
        await gated.committed.wait()
        self.now = expires_at
        gated.release.set()
        result = await dispatch

        saved = await self.deliveries.current_event(
            "event-expiry", 1, subscription_id="sub-expiry", subscription_revision=1
        )
        self.assertEqual(result.state, DeliveryState.CANCELLED)
        self.assertEqual(result.error_code, "grant_unavailable")
        self.assertEqual(saved.state, DeliveryState.CANCELLED)
        self.assertEqual(saved.attempt.error_code, "cancelled_before_dispatch")
        self.assertEqual(self.port.calls, [])

    async def test_cancel_during_event_sending_commit_drains_exact_abort(self):
        gated = _GatedDeliveryRepository(self.deliveries)
        service = DeliveryService(
            deliveries=gated,
            subscriptions=self.subscriptions,
            grants=_Grants(),
            windows=self.windows,
            registry=self.registry,
            admission=self.lifecycle.admission,
            send_scheduler=self.send_scheduler,
            routes=self.routes,
            renderer=self.renderer,
            message_port=self.port,
            limits=validate_contract(DisplayLimits(2, 1024)),
            now=lambda: self.now,
        )
        dispatch = asyncio.create_task(service.dispatch_event("event-1", 1, "sub-1", 1))

        await gated.committed.wait()
        dispatch.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await dispatch

        saved = await self.deliveries.current_event(
            "event-1", 1, subscription_id="sub-1", subscription_revision=1
        )
        self.assertEqual(saved.state, DeliveryState.CANCELLED)
        self.assertEqual(saved.attempt.error_code, "cancelled_before_dispatch")
        self.assertEqual(self.port.calls, [])

    async def test_digest_claim_expiry_after_sqlite_sending_cas_aborts_then_reclaims(
        self,
    ):
        start = self.now - timedelta(hours=2)
        due = self.now - timedelta(minutes=1)
        member = validate_contract(DigestMember("sub-1", 1, "event-claim-expiry", 1))
        event = self._event(key="event-claim-expiry")
        window = DigestWindow(
            "window-claim-expiry",
            "UTC",
            "daily",
            start,
            due,
            due,
            DstFoldPolicy.FIRST_OCCURRENCE,
            DstGapPolicy.SKIP,
            (member,),
        )
        await self.windows.create(window)
        association = DigestMemberAssociation(
            window.window_id, self.recipient, member, event
        )
        await create_digest_envelope_fixture(
            self.deliveries,
            self.bindings,
            DigestEnvelope(
                "envelope-claim-expiry",
                window.window_id,
                self.recipient,
                (member,),
                member_associations=(association,),
            ),
        )
        gated = _GatedDigestWindows(self.windows)
        service = DeliveryService(
            deliveries=self.deliveries,
            subscriptions=self.subscriptions,
            grants=_Grants(),
            windows=gated,
            registry=self.registry,
            admission=self.lifecycle.admission,
            send_scheduler=self.send_scheduler,
            routes=self.routes,
            renderer=self.renderer,
            message_port=self.port,
            limits=validate_contract(DisplayLimits(2, 1024)),
            now=lambda: self.now,
            claim_lease=timedelta(seconds=1),
        )

        dispatch = asyncio.create_task(
            service.dispatch_digest(window.window_id, self.recipient, now=self.now)
        )
        await gated.committed.wait()
        self.now += timedelta(seconds=2)
        gated.release.set()
        result = await dispatch

        saved = await self.windows.current_envelope(window.window_id, self.recipient)
        self.assertEqual(result.state, DeliveryState.CANCELLED)
        self.assertEqual(result.error_code, "delivery_claim_expired")
        self.assertEqual(saved.state.value, "ready")
        self.assertEqual(saved.delivery_attempts[-1].state, DeliveryState.FAILED)
        self.assertEqual(
            saved.delivery_attempts[-1].error_code, "cancelled_before_dispatch"
        )
        self.assertEqual(self.port.calls, [])

        retried = await self._service().dispatch_digest(
            window.window_id, self.recipient, now=self.now
        )
        self.assertEqual(retried.state, DeliveryState.SENT)
        self.assertEqual(len(self.port.calls), 1)

    async def test_digest_grant_expiring_after_sqlite_sending_cas_rerenders_survivors(
        self,
    ):
        start = self.now - timedelta(hours=2)
        due = self.now - timedelta(minutes=1)
        grant_ref = validate_contract(GrantReference("grant-digest-expiry", 1))
        private_key = validate_contract(
            CollectionKey(
                "sample/game",
                "collector",
                1,
                "source",
                validate_contract(NormalizedInput({})),
                OwnerScope.authorized("u1", grant_ref),
            )
        )
        private_record = SubscriptionRecord(
            subscription_id="sub-digest-private",
            revision=1,
            module_id="sample/game",
            collection_key=private_key,
            owner_id="u1",
            grant=grant_ref,
            recipient=self.recipient,
            notification_mode="digest",
            filters={},
            status=SubscriptionStatus.ACTIVE,
        )
        await self.subscriptions.create(private_record)
        private_event = DeliveryEvent(
            "event-digest-private",
            1,
            private_record.subscription_id,
            private_record.revision,
            private_record.owner_id,
            grant_ref,
            private_record.recipient,
            validate_contract(
                DisplayDocument(
                    "expired-private-title",
                    "subject",
                    (validate_contract(TextBlock("private body")),),
                    privacy=Privacy.PRIVATE,
                )
            ),
            delivery_idempotency_key(
                "event-digest-private",
                1,
                private_record.subscription_id,
                1,
                self.recipient,
            ),
        )
        public_event = self._event(key="event-digest-public", title="public-title")
        await create_subscription_event_fixture(
            self.deliveries, self.bindings, private_event
        )
        await create_subscription_event_fixture(
            self.deliveries, self.bindings, public_event
        )
        private_member = validate_contract(
            DigestMember(private_record.subscription_id, 1, private_event.event_key, 1)
        )
        public_member = validate_contract(
            DigestMember("sub-1", 1, public_event.event_key, 1)
        )
        window = DigestWindow(
            "window-digest-grant-expiry",
            "UTC",
            "daily",
            start,
            due,
            due,
            DstFoldPolicy.FIRST_OCCURRENCE,
            DstGapPolicy.SKIP,
            (private_member, public_member),
        )
        await self.windows.create(window)
        await create_digest_envelope_fixture(
            self.deliveries,
            self.bindings,
            DigestEnvelope(
                "envelope-digest-grant-expiry",
                window.window_id,
                self.recipient,
                (private_member, public_member),
                member_associations=(
                    DigestMemberAssociation(
                        window.window_id, self.recipient, private_member, private_event
                    ),
                    DigestMemberAssociation(
                        window.window_id, self.recipient, public_member, public_event
                    ),
                ),
            ),
        )

        expires_at = self.now + timedelta(seconds=2)
        grant = Grant(
            grant_ref.grant_id,
            grant_ref.revision,
            "u1",
            "sample/game",
            "account-digest-expiry",
            ("read",),
            None,
            GrantStatus.ACTIVE,
            expires_at,
        )
        gated = _GatedDigestWindows(self.windows)
        service = DeliveryService(
            deliveries=self.deliveries,
            subscriptions=self.subscriptions,
            grants=_Grants(grant),
            windows=gated,
            registry=self.registry,
            admission=self.lifecycle.admission,
            send_scheduler=self.send_scheduler,
            routes=self.routes,
            renderer=self.renderer,
            message_port=self.port,
            limits=validate_contract(DisplayLimits(2, 1024)),
            now=lambda: self.now,
        )

        dispatch = asyncio.create_task(
            service.dispatch_digest(window.window_id, self.recipient)
        )
        await gated.committed.wait()
        self.now = expires_at
        gated.release.set()
        result = await dispatch

        saved = await self.windows.current_envelope(window.window_id, self.recipient)
        self.assertEqual(result.state, DeliveryState.SENT)
        self.assertEqual(saved.state, DigestEnvelopeState.SENT)
        self.assertEqual(saved.members, (public_member,))
        self.assertEqual(
            [item.state for item in saved.delivery_attempts],
            [DeliveryState.FAILED, DeliveryState.SENT],
        )
        self.assertEqual(
            saved.delivery_attempts[0].error_code, "cancelled_before_dispatch"
        )
        self.assertEqual(len(self.renderer.calls), 2)
        self.assertEqual(
            tuple(item.document.title for item in self.renderer.calls[-1].members),
            ("public-title",),
        )
        self.assertEqual(len(self.port.calls), 1)
        self.assertEqual(self.port.calls[0][1].text, "digest:public-title")

    async def test_cancel_during_pruned_digest_sending_commit_aborts_exact_attempt(
        self,
    ):
        start = self.now - timedelta(hours=2)
        due = self.now - timedelta(minutes=1)
        other = SubscriptionRecord(
            subscription_id="sub-pruned-valid",
            revision=1,
            module_id="sample/game",
            collection_key=self.key,
            owner_id="u1",
            grant=None,
            recipient=self.recipient,
            notification_mode="digest",
            filters={},
            status=SubscriptionStatus.ACTIVE,
        )
        await self.subscriptions.create(other)
        removed_event = self._event(key="event-pruned-removed", title="removed-title")
        valid_event = DeliveryEvent(
            "event-pruned-valid",
            1,
            other.subscription_id,
            1,
            other.owner_id,
            None,
            other.recipient,
            validate_contract(
                DisplayDocument(
                    "remaining-title",
                    "subject",
                    (validate_contract(TextBlock("body")),),
                )
            ),
            delivery_idempotency_key(
                "event-pruned-valid", 1, other.subscription_id, 1, self.recipient
            ),
        )
        await create_subscription_event_fixture(
            self.deliveries, self.bindings, removed_event
        )
        await create_subscription_event_fixture(
            self.deliveries, self.bindings, valid_event
        )
        removed_member = validate_contract(
            DigestMember("sub-1", 1, removed_event.event_key, 1)
        )
        valid_member = validate_contract(
            DigestMember(other.subscription_id, 1, valid_event.event_key, 1)
        )
        window = DigestWindow(
            "window-pruned-cancel",
            "UTC",
            "daily",
            start,
            due,
            due,
            DstFoldPolicy.FIRST_OCCURRENCE,
            DstGapPolicy.SKIP,
            (removed_member, valid_member),
        )
        await self.windows.create(window)
        await create_digest_envelope_fixture(
            self.deliveries,
            self.bindings,
            DigestEnvelope(
                "envelope-pruned-cancel",
                window.window_id,
                self.recipient,
                (removed_member, valid_member),
                member_associations=(
                    DigestMemberAssociation(
                        window.window_id, self.recipient, removed_member, removed_event
                    ),
                    DigestMemberAssociation(
                        window.window_id, self.recipient, valid_member, valid_event
                    ),
                ),
            ),
        )

        class CancellingRoutes(_Routes):
            async def resolve_current(inner, owner, recipient):
                inner.current_calls += 1
                # Initial (2), post-render (2), then final validation (1).
                if inner.current_calls == 5:
                    await self.subscriptions.cancel("sub-1", expected_revision=1)
                return recipient

        gated = _GatedDigestWindows(self.windows)
        service = self._service()
        service._windows = gated
        service._routes = CancellingRoutes()
        dispatch = asyncio.create_task(
            service.dispatch_digest(window.window_id, self.recipient)
        )
        await gated.committed.wait()
        dispatch.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await dispatch

        saved = await self.windows.current_envelope(window.window_id, self.recipient)
        self.assertEqual(saved.state, DigestEnvelopeState.READY)
        self.assertEqual(saved.members, (valid_member,))
        self.assertEqual(saved.delivery_attempts[-1].state, DeliveryState.FAILED)
        self.assertEqual(
            saved.delivery_attempts[-1].error_code, "cancelled_before_dispatch"
        )
        self.assertEqual(self.port.calls, [])

        gated.release.set()
        retried = await service.dispatch_digest(window.window_id, self.recipient)
        saved = await self.windows.current_envelope(window.window_id, self.recipient)
        self.assertEqual(retried.state, DeliveryState.SENT)
        self.assertEqual(saved.state, DigestEnvelopeState.SENT)
        self.assertEqual(len(saved.delivery_attempts), 2)
        self.assertEqual(self.port.calls[0][1].text, "digest:remaining-title")

    async def test_subscription_cancelled_during_final_route_check_blocks_immediate_send(
        self,
    ) -> None:
        class CancellingRoutes(_Routes):
            async def resolve_current(inner, owner, recipient):
                inner.current_calls += 1
                if inner.current_calls == 3:
                    await self.subscriptions.cancel("sub-1", expected_revision=1)
                return recipient

        service = self._service()
        service._routes = CancellingRoutes()
        result = await service.dispatch_event("event-1", 1, "sub-1", 1)
        saved = await self.deliveries.current_event(
            "event-1", 1, subscription_id="sub-1", subscription_revision=1
        )
        self.assertEqual(result.state, DeliveryState.CANCELLED)
        self.assertEqual(saved.state, DeliveryState.CANCELLED)
        self.assertEqual(self.port.calls, [])

    async def test_known_failed_receipt_keeps_failed_state_without_retry_policy(
        self,
    ) -> None:
        port = _MessagePort(MessageReceipt(MessageStatus.FAILED))
        service = self._service(port=port)
        result = await service.dispatch_event("event-1", 1, "sub-1", 1)
        saved = await self.deliveries.current_event(
            "event-1", 1, subscription_id="sub-1", subscription_revision=1
        )
        self.assertEqual(result.state, DeliveryState.FAILED)
        self.assertEqual(saved.state, DeliveryState.FAILED)
        self.assertIsNone(saved.retry_at)

    async def test_known_failed_digest_reopens_only_after_saved_retry_time(
        self,
    ) -> None:
        start = self.now - timedelta(hours=2)
        due = self.now - timedelta(minutes=1)
        member = validate_contract(DigestMember("sub-1", 1, "event-retry", 1))
        event = self._event(key="event-retry")
        window = DigestWindow(
            "window-retry",
            "UTC",
            "daily",
            start,
            due,
            due,
            DstFoldPolicy.FIRST_OCCURRENCE,
            DstGapPolicy.SKIP,
            (member,),
        )
        await self.windows.create(window)
        association = DigestMemberAssociation(
            "window-retry", self.recipient, member, event
        )
        await create_digest_envelope_fixture(
            self.deliveries,
            self.bindings,
            DigestEnvelope(
                "envelope-retry",
                "window-retry",
                self.recipient,
                (member,),
                member_associations=(association,),
            ),
        )
        retry_at = self.now + timedelta(minutes=5)
        failed_port = _MessagePort(MessageReceipt(MessageStatus.FAILED))
        service = DeliveryService(
            deliveries=self.deliveries,
            subscriptions=self.subscriptions,
            grants=_Grants(),
            windows=self.windows,
            registry=self.registry,
            admission=self.lifecycle.admission,
            send_scheduler=self.send_scheduler,
            routes=self.routes,
            renderer=self.renderer,
            message_port=failed_port,
            limits=validate_contract(DisplayLimits(2, 1024)),
            now=lambda: self.now,
            retry_at=lambda _event, _completed: retry_at,
        )
        first = await service.dispatch_digest(
            "window-retry", self.recipient, now=self.now
        )
        saved = await self.windows.current_envelope("window-retry", self.recipient)
        self.assertEqual(first.state, DeliveryState.FAILED)
        self.assertEqual(saved.retry_at, retry_at)
        self.assertIsNone(
            await service.dispatch_digest("window-retry", self.recipient, now=self.now)
        )
        self.assertEqual(len(failed_port.calls), 1)

        self.now = retry_at
        accepted_port = _MessagePort()
        result = await self._service(port=accepted_port).dispatch_digest(
            "window-retry", self.recipient, now=self.now
        )
        saved = await self.windows.current_envelope("window-retry", self.recipient)
        self.assertEqual(result.state, DeliveryState.SENT)
        self.assertEqual(saved.state.value, "sent")
        self.assertEqual(len(saved.delivery_attempts), 2)
        self.assertEqual(len(accepted_port.calls), 1)

    async def test_authorized_group_recipient_is_rejected_before_render_or_send(self):
        grant_ref = validate_contract(GrantReference("grant-1", 1))
        scope = OwnerScope.authorized("u1", grant_ref)
        key = validate_contract(
            CollectionKey(
                "sample/game",
                "collector",
                1,
                "source",
                validate_contract(NormalizedInput({})),
                scope,
            )
        )
        group = validate_contract(
            ConversationRef("adapter", ConversationKind.GROUP, "g1", "group-g1")
        )
        # Storage rejects this malformed authority/route combination. Feed it
        # directly to D to prove its independent persisted-data guard as well.
        record = SimpleNamespace(
            subscription_id="authorized-group",
            revision=1,
            module_id="sample/game",
            collection_key=key,
            owner_id="u1",
            grant=grant_ref,
            recipient=group,
            status=SubscriptionStatus.ACTIVE,
        )
        self.service._subscriptions = SimpleNamespace(
            current=lambda _subscription_id: _resolved(record)
        )
        document = validate_contract(
            DisplayDocument(
                "private title",
                "subject",
                (validate_contract(TextBlock("private body")),),
                privacy=Privacy.PRIVATE,
            )
        )
        event = object.__new__(DeliveryEvent)
        for field, value in {
            "event_key": "authorized-group-event",
            "event_version": 1,
            "subscription_id": record.subscription_id,
            "subscription_revision": record.revision,
            "owner_id": record.owner_id,
            "grant": record.grant,
            "recipient": record.recipient,
            "display_data": document,
            "idempotency_key": delivery_idempotency_key(
                "authorized-group-event", 1, record.subscription_id, 1, group
            ),
            "state": DeliveryState.PENDING,
            "attempt": None,
            "retry_at": None,
        }.items():
            object.__setattr__(event, field, value)
        self.service._deliveries = SimpleNamespace(
            current_event=lambda *_args, **_kwargs: _resolved(event),
            record_attempt=_nothing,
        )
        result = await self.service.dispatch_event(
            "authorized-group-event", 1, record.subscription_id, 1
        )
        self.assertEqual(result.state, DeliveryState.CANCELLED)
        self.assertEqual(result.error_code, "privacy_rejected")
        self.assertEqual(self.renderer.calls, [])
        self.assertEqual(self.port.calls, [])

    async def test_startup_recovery_marks_stale_event_unknown_without_resend(self):
        claimed = await self.deliveries.claim_sending(
            "event-1",
            1,
            subscription_id="sub-1",
            subscription_revision=1,
            expected_state=DeliveryState.PENDING,
            attempt_number=1,
            started_at=self.now - timedelta(hours=1),
            now=self.now - timedelta(hours=1),
        )
        self.assertIsNotNone(claimed)
        stale, _ = await self.service.recover_startup()
        self.assertEqual(len(stale), 1)
        saved = await self.deliveries.current_event(
            "event-1", 1, subscription_id="sub-1", subscription_revision=1
        )
        result = await self.service.dispatch_event("event-1", 1, "sub-1", 1)
        self.assertEqual(saved.state, DeliveryState.UNKNOWN)
        self.assertEqual(result.state, DeliveryState.UNKNOWN)
        self.assertEqual(self.port.calls, [])

    async def test_digest_uses_saved_association_and_claims_once(self) -> None:
        start = self.now - timedelta(hours=2)
        due = self.now - timedelta(minutes=1)
        member = validate_contract(DigestMember("sub-1", 1, "event-digest", 1))
        event = self._event(key="event-digest")
        from ygl_test_subject.core.contracts.subscriptions import (
            DigestMemberAssociation,
            DigestWindow,
        )

        window = DigestWindow(
            "window-1",
            "UTC",
            "daily",
            start,
            due,
            due,
            DstFoldPolicy.FIRST_OCCURRENCE,
            DstGapPolicy.SKIP,
            (member,),
        )
        await self.windows.create(window)
        association = DigestMemberAssociation("window-1", self.recipient, member, event)
        envelope = DigestEnvelope(
            "envelope-1",
            "window-1",
            self.recipient,
            (member,),
            member_associations=(association,),
        )
        await create_digest_envelope_fixture(self.deliveries, self.bindings, envelope)
        result = await self.service.dispatch_digest(
            "window-1", self.recipient, now=self.now
        )
        again = await self.service.dispatch_digest(
            "window-1", self.recipient, now=self.now
        )
        self.assertEqual(result.state, DeliveryState.SENT)
        self.assertIsNone(again)
        self.assertEqual(len(self.port.calls), 1)
        self.assertEqual(self.port.calls[0][1].text, "digest:saved-title")

    async def test_unknown_digest_receipt_is_never_reclaimed(self) -> None:
        start = self.now - timedelta(hours=2)
        due = self.now - timedelta(minutes=1)
        member = validate_contract(DigestMember("sub-1", 1, "event-digest-unknown", 1))
        event = self._event(key="event-digest-unknown")
        window = DigestWindow(
            "window-digest-unknown",
            "UTC",
            "daily",
            start,
            due,
            due,
            DstFoldPolicy.FIRST_OCCURRENCE,
            DstGapPolicy.SKIP,
            (member,),
        )
        await self.windows.create(window)
        association = DigestMemberAssociation(
            window.window_id, self.recipient, member, event
        )
        await create_digest_envelope_fixture(
            self.deliveries,
            self.bindings,
            DigestEnvelope(
                "envelope-digest-unknown",
                window.window_id,
                self.recipient,
                (member,),
                member_associations=(association,),
            ),
        )
        port = _MessagePort(MessageReceipt(MessageStatus.UNKNOWN))
        service = self._service(port=port)

        result = await service.dispatch_digest(
            window.window_id, self.recipient, now=self.now
        )
        again = await service.dispatch_digest(
            window.window_id, self.recipient, now=self.now
        )
        saved = await self.windows.current_envelope(window.window_id, self.recipient)

        self.assertEqual(result.state, DeliveryState.UNKNOWN)
        self.assertIsNone(again)
        self.assertEqual(saved.state.value, "unknown")
        self.assertEqual(saved.delivery_attempts[-1].state, DeliveryState.UNKNOWN)
        self.assertEqual(len(port.calls), 1)

    async def test_digest_cancellation_after_first_render_removes_only_that_member(
        self,
    ) -> None:
        start = self.now - timedelta(hours=2)
        due = self.now - timedelta(minutes=1)
        other = SubscriptionRecord(
            subscription_id="sub-2",
            revision=1,
            module_id="sample/game",
            collection_key=self.key,
            owner_id="u1",
            grant=None,
            recipient=self.recipient,
            notification_mode="digest",
            filters={},
            status=SubscriptionStatus.ACTIVE,
        )
        await self.subscriptions.create(other)
        event_one = self._event(
            key="event-one",
            title="revoked-private-title",
            privacy=Privacy.PRIVATE,
        )
        event_two = DeliveryEvent(
            "event-two",
            1,
            other.subscription_id,
            1,
            other.owner_id,
            None,
            other.recipient,
            validate_contract(
                DisplayDocument(
                    "remaining-title",
                    "subject",
                    (validate_contract(TextBlock("body")),),
                    privacy=Privacy.PUBLIC,
                )
            ),
            delivery_idempotency_key("event-two", 1, "sub-2", 1, self.recipient),
        )
        await create_subscription_event_fixture(
            self.deliveries, self.bindings, event_one
        )
        await create_subscription_event_fixture(
            self.deliveries, self.bindings, event_two
        )
        member_one = validate_contract(DigestMember("sub-1", 1, "event-one", 1))
        member_two = validate_contract(DigestMember("sub-2", 1, "event-two", 1))
        window = DigestWindow(
            "window-members",
            "UTC",
            "daily",
            start,
            due,
            due,
            DstFoldPolicy.FIRST_OCCURRENCE,
            DstGapPolicy.SKIP,
            (member_one, member_two),
        )
        await self.windows.create(window)
        associations = (
            DigestMemberAssociation(
                "window-members", self.recipient, member_one, event_one
            ),
            DigestMemberAssociation(
                "window-members", self.recipient, member_two, event_two
            ),
        )
        await create_digest_envelope_fixture(
            self.deliveries,
            self.bindings,
            DigestEnvelope(
                "envelope-members",
                "window-members",
                self.recipient,
                (member_one, member_two),
                member_associations=associations,
            ),
        )

        class CancellingRenderer(_Renderer):
            async def render_batch(inner, batch, limits):
                if not inner.calls:
                    await self.subscriptions.cancel("sub-1", expected_revision=1)
                return await super().render_batch(batch, limits)

        renderer = CancellingRenderer()
        service = self._service(renderer=renderer)
        result = await service.dispatch_digest(
            "window-members", self.recipient, now=self.now
        )
        self.assertEqual(result.state, DeliveryState.SENT)
        self.assertEqual(len(self.port.calls), 1)
        self.assertEqual(self.port.calls[0][1].text, "digest:remaining-title")
        self.assertEqual(
            tuple(item.member for item in renderer.calls[-1].members),
            (member_two,),
        )

    async def test_cancel_during_final_digest_route_check_removes_private_member(self):
        start = self.now - timedelta(hours=2)
        due = self.now - timedelta(minutes=1)
        profile = validate_contract(
            DigestScheduleProfile(
                "UTC",
                "12:00",
                86400,
                DstFoldPolicy.FIRST_OCCURRENCE,
                DstGapPolicy.SKIP,
            )
        )
        private_record = SubscriptionRecord(
            subscription_id="sub-1",
            revision=2,
            module_id="sample/game",
            collection_key=self.key,
            owner_id="u1",
            grant=None,
            recipient=self.recipient,
            notification_mode="digest",
            filters={},
            status=SubscriptionStatus.ACTIVE,
            type_id="sample/type",
            digest_schedule=profile,
        )
        await self.subscriptions.revise(private_record, expected_revision=1)
        other = SubscriptionRecord(
            subscription_id="sub-final-valid",
            revision=1,
            module_id="sample/game",
            collection_key=self.key,
            owner_id="u1",
            grant=None,
            recipient=self.recipient,
            notification_mode="digest",
            filters={},
            status=SubscriptionStatus.ACTIVE,
            type_id="sample/type",
            digest_schedule=profile,
        )
        await self.subscriptions.create(other)
        revoked_event = DeliveryEvent(
            "event-final-private",
            1,
            private_record.subscription_id,
            private_record.revision,
            private_record.owner_id,
            private_record.grant,
            private_record.recipient,
            validate_contract(
                DisplayDocument(
                    "PRIVATE-REVOKED",
                    "subject",
                    (validate_contract(TextBlock("private body")),),
                    privacy=Privacy.PRIVATE,
                )
            ),
            delivery_idempotency_key(
                "event-final-private",
                1,
                private_record.subscription_id,
                private_record.revision,
                self.recipient,
            ),
        )
        valid_event = DeliveryEvent(
            "event-final-valid",
            1,
            other.subscription_id,
            1,
            other.owner_id,
            None,
            other.recipient,
            validate_contract(
                DisplayDocument(
                    "PUBLIC-VALID",
                    "subject",
                    (validate_contract(TextBlock("body")),),
                    privacy=Privacy.PUBLIC,
                )
            ),
            delivery_idempotency_key(
                "event-final-valid", 1, other.subscription_id, 1, self.recipient
            ),
        )
        await create_subscription_event_fixture(
            self.deliveries, self.bindings, revoked_event
        )
        await create_subscription_event_fixture(
            self.deliveries, self.bindings, valid_event
        )
        revoked_member = validate_contract(
            DigestMember("sub-1", 2, revoked_event.event_key, 1)
        )
        valid_member = validate_contract(
            DigestMember(other.subscription_id, 1, valid_event.event_key, 1)
        )
        window = DigestWindow(
            "window-final-route",
            "UTC",
            "daily",
            start,
            due,
            due,
            DstFoldPolicy.FIRST_OCCURRENCE,
            DstGapPolicy.SKIP,
            (revoked_member, valid_member),
        )
        await self.windows.create(window)
        associations = (
            DigestMemberAssociation(
                window.window_id, self.recipient, revoked_member, revoked_event
            ),
            DigestMemberAssociation(
                window.window_id, self.recipient, valid_member, valid_event
            ),
        )
        await create_digest_envelope_fixture(
            self.deliveries,
            self.bindings,
            DigestEnvelope(
                "envelope-final-route",
                window.window_id,
                self.recipient,
                (revoked_member, valid_member),
                member_associations=associations,
            ),
        )

        class CancellingRoutes(_Routes):
            async def resolve_current(inner, owner, recipient):
                inner.current_calls += 1
                # Initial checks (2), post-render checks (2), then the first
                # final route attestation before SENDING.
                if inner.current_calls == 5:
                    await self.subscriptions.cancel("sub-1", expected_revision=2)
                return recipient

        service = self._service()
        service._routes = CancellingRoutes()
        result = await service.dispatch_digest(
            window.window_id, self.recipient, now=self.now
        )
        saved = await self.windows.current_envelope(window.window_id, self.recipient)
        self.assertEqual(result.state, DeliveryState.SENT)
        self.assertEqual(saved.state.value, "sent")
        self.assertEqual(saved.members, (valid_member,))
        self.assertEqual(len(saved.delivery_attempts), 2)
        self.assertEqual(saved.delivery_attempts[0].state, DeliveryState.FAILED)
        self.assertEqual(
            saved.delivery_attempts[0].error_code, "cancelled_before_dispatch"
        )
        self.assertEqual(saved.delivery_attempts[1].state, DeliveryState.SENT)
        self.assertEqual(len(self.port.calls), 1)
        self.assertEqual(self.port.calls[0][1].text, "digest:PUBLIC-VALID")
        self.assertNotIn("PRIVATE-REVOKED", self.port.calls[0][1].text)

    async def test_digest_with_no_valid_members_is_cancelled_without_send(self) -> None:
        start = self.now - timedelta(hours=2)
        due = self.now - timedelta(minutes=1)
        member = validate_contract(DigestMember("sub-1", 1, "event-all-revoked", 1))
        event = DeliveryEvent(
            "event-all-revoked",
            1,
            "sub-1",
            1,
            "u1",
            None,
            self.recipient,
            validate_contract(
                DisplayDocument(
                    "private revoked title",
                    "subject",
                    (validate_contract(TextBlock("private body")),),
                    privacy=Privacy.PRIVATE,
                )
            ),
            delivery_idempotency_key(
                "event-all-revoked", 1, "sub-1", 1, self.recipient
            ),
        )
        window = DigestWindow(
            "window-all-revoked",
            "UTC",
            "daily",
            start,
            due,
            due,
            DstFoldPolicy.FIRST_OCCURRENCE,
            DstGapPolicy.SKIP,
            (member,),
        )
        await self.windows.create(window)
        association = DigestMemberAssociation(
            window.window_id, self.recipient, member, event
        )
        await create_digest_envelope_fixture(
            self.deliveries,
            self.bindings,
            DigestEnvelope(
                "envelope-all-revoked",
                window.window_id,
                self.recipient,
                (member,),
                member_associations=(association,),
            ),
        )

        class CancellingRenderer(_Renderer):
            async def render_batch(inner, batch, limits):
                await self.subscriptions.cancel("sub-1", expected_revision=1)
                return await super().render_batch(batch, limits)

        renderer = CancellingRenderer()
        result = await self._service(renderer=renderer).dispatch_digest(
            window.window_id, self.recipient, now=self.now
        )
        envelope = await self.windows.current_envelope(window.window_id, self.recipient)
        self.assertEqual(result.state, DeliveryState.CANCELLED)
        self.assertEqual(envelope.state.value, "cancelled")
        self.assertEqual(self.port.calls, [])
