"""B04-D delivery state-machine probes using real SQLite repositories."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from ygl_test_subject.api.display import (
    DisplayDocument,
    DisplayLimits,
    DisplayOutput,
    Privacy,
    TextBlock,
)
from ygl_test_subject.api.manifests import (
    CapabilityDescriptor,
    CapabilityEffect,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
    SourceDeclaration,
)
from ygl_test_subject.api.services import (
    CapabilityHealth,
    Grant,
    GrantStatus,
    HealthReport,
    HealthStatus,
    ModuleHandlers,
)
from ygl_test_subject.api.storage import GrantReference, OwnerScope, OwnershipKind
from ygl_test_subject.api.subscriptions import (
    CollectionKey,
    ConversationKind,
    ConversationRef,
    DeliveryEvent,
    DeliveryState,
    DigestEnvelope,
    DigestEnvelopeState,
    DigestMember,
    DigestMemberAssociation,
    DigestScheduleProfile,
    DigestWindow,
    DstFoldPolicy,
    DstGapPolicy,
    NormalizedInput,
    ScheduleDescriptor,
    ScheduleTrigger,
    SubscriptionRecord,
    SubscriptionStatus,
    delivery_idempotency_key,
)
from ygl_test_subject.api.version import CONTRACT_VERSION
from ygl_test_subject.core.context_issuer import ContextIssuer
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

from tests.fixtures.b04_runtime import _run_async_from_sync


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
        return DisplayOutput(self.text or document.title)

    async def render_batch(self, batch, limits):
        self.calls.append(batch)
        return DisplayOutput(
            "digest:" + ",".join(x.document.title for x in batch.members)
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
        return NormalizedInput(dict(parameters))

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
        return HealthReport({"read": CapabilityHealth(HealthStatus.AVAILABLE)})


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "delivery.sqlite3"
        self.db = SQLiteDatabase(self.path)
        self.deliveries = SQLiteDeliveryRepository(self.db)
        self.windows = SQLiteDigestWindowRepository(self.db)
        self.subscriptions = SQLiteSubscriptionStore(self.db)
        self.now = datetime(2026, 9, 25, 12, tzinfo=UTC)
        self.registry = Registry()
        self.issuer = ContextIssuer()
        capability = CapabilityDescriptor(
            "read",
            {"type": "object", "properties": {}, "required": []},
            InvocationPolicy.COMMAND_ONLY,
            CapabilityEffect.READ_ONLY,
        )
        schedule = ScheduleDescriptor(
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
        manifest = ModuleManifest(
            "game",
            "game",
            ModuleCategory.GAME,
            "tests:DeliveryFixture",
            "1.0.0",
            (capability,),
            schedules=(schedule,),
            sources=(SourceDeclaration("source", "example.test"),),
        )
        handlers = ModuleHandlers(
            {"read": _DeliveryHandler()}, {"collector": _DeliveryCollector()}, {}
        )
        self.registry.register_package(
            PackageManifest(
                "sample",
                "1.0.0",
                CONTRACT_VERSION,
                (manifest,),
                "Tests",
                "MIT",
                "offline",
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
        self.recipient = ConversationRef(
            "adapter", ConversationKind.DIRECT, "u1", "private-u1"
        )
        self.scope = OwnerScope.user("u1")
        self.key = CollectionKey(
            "sample/game", "collector", 1, "source", NormalizedInput({}), self.scope
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
        document = DisplayDocument(
            title, "subject", (TextBlock("saved body"),), privacy=privacy
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
            limits=DisplayLimits(2, 1024),
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
        await self.subscriptions.create(self.record)
        await self.deliveries.create_event(self.event)

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
            digest_schedule=DigestScheduleProfile(
                "UTC",
                "12:00",
                86400,
                DstFoldPolicy.FIRST_OCCURRENCE,
                DstGapPolicy.SKIP,
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
            DisplayDocument(
                "digest-only-title",
                "subject",
                (TextBlock("digest-only body"),),
                privacy=Privacy.PUBLIC,
            ),
            delivery_idempotency_key(
                event_key,
                1,
                digest_record.subscription_id,
                digest_record.revision,
                digest_record.recipient,
            ),
        )
        await self.deliveries.create_event(event)

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
        self.assertTrue(all(result.state is DeliveryState.SENT for result in results))
        self.assertEqual(len(self.port.calls), 1)

    async def test_send_timeout_is_unknown_and_not_retried(self) -> None:
        class HangingPort(_MessagePort):
            async def send(self, target, payload):
                self.calls.append((target, payload))
                await asyncio.sleep(0.05)

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
            limits=DisplayLimits(2, 1024),
            now=lambda: self.now,
            send_timeout=0.001,
        )
        result = await service.dispatch_event("event-1", 1, "sub-1", 1)
        again = await service.dispatch_event("event-1", 1, "sub-1", 1)
        self.assertEqual(result.state, DeliveryState.UNKNOWN)
        self.assertEqual(again.state, DeliveryState.UNKNOWN)
        self.assertEqual(len(port.calls), 1)

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
        grant_ref = GrantReference("grant-event-expiry", 1)
        scope = OwnerScope.authorized("u1", grant_ref)
        key = CollectionKey(
            "sample/game", "collector", 1, "source", NormalizedInput({}), scope
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
            DisplayDocument(
                "private title",
                "subject",
                (TextBlock("private body"),),
                privacy=Privacy.PRIVATE,
            ),
            delivery_idempotency_key(
                "event-expiry", 1, record.subscription_id, 1, record.recipient
            ),
        )
        await self.deliveries.create_event(event)
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
            limits=DisplayLimits(2, 1024),
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
            limits=DisplayLimits(2, 1024),
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
        member = DigestMember("sub-1", 1, "event-claim-expiry", 1)
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
        await self.deliveries.create_envelope(
            DigestEnvelope(
                "envelope-claim-expiry",
                window.window_id,
                self.recipient,
                (member,),
                member_associations=(association,),
            )
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
            limits=DisplayLimits(2, 1024),
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
        grant_ref = GrantReference("grant-digest-expiry", 1)
        private_key = CollectionKey(
            "sample/game",
            "collector",
            1,
            "source",
            NormalizedInput({}),
            OwnerScope.authorized("u1", grant_ref),
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
            DisplayDocument(
                "expired-private-title",
                "subject",
                (TextBlock("private body"),),
                privacy=Privacy.PRIVATE,
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
        await self.deliveries.create_event(private_event)
        await self.deliveries.create_event(public_event)
        private_member = DigestMember(
            private_record.subscription_id, 1, private_event.event_key, 1
        )
        public_member = DigestMember("sub-1", 1, public_event.event_key, 1)
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
        await self.deliveries.create_envelope(
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
            )
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
            limits=DisplayLimits(2, 1024),
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
            DisplayDocument("remaining-title", "subject", (TextBlock("body"),)),
            delivery_idempotency_key(
                "event-pruned-valid", 1, other.subscription_id, 1, self.recipient
            ),
        )
        await self.deliveries.create_event(removed_event)
        await self.deliveries.create_event(valid_event)
        removed_member = DigestMember("sub-1", 1, removed_event.event_key, 1)
        valid_member = DigestMember(other.subscription_id, 1, valid_event.event_key, 1)
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
        await self.deliveries.create_envelope(
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
            )
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
        member = DigestMember("sub-1", 1, "event-retry", 1)
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
        await self.deliveries.create_envelope(
            DigestEnvelope(
                "envelope-retry",
                "window-retry",
                self.recipient,
                (member,),
                member_associations=(association,),
            )
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
            limits=DisplayLimits(2, 1024),
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
        grant_ref = GrantReference("grant-1", 1)
        scope = OwnerScope.authorized("u1", grant_ref)
        key = CollectionKey(
            "sample/game", "collector", 1, "source", NormalizedInput({}), scope
        )
        group = ConversationRef("adapter", ConversationKind.GROUP, "g1", "group-g1")
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
        document = DisplayDocument(
            "private title",
            "subject",
            (TextBlock("private body"),),
            privacy=Privacy.PRIVATE,
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
        member = DigestMember("sub-1", 1, "event-digest", 1)
        event = self._event(key="event-digest")
        from ygl_test_subject.api.subscriptions import (
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
        await self.deliveries.create_envelope(envelope)
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
        member = DigestMember("sub-1", 1, "event-digest-unknown", 1)
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
        await self.deliveries.create_envelope(
            DigestEnvelope(
                "envelope-digest-unknown",
                window.window_id,
                self.recipient,
                (member,),
                member_associations=(association,),
            )
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
            DisplayDocument(
                "remaining-title",
                "subject",
                (TextBlock("body"),),
                privacy=Privacy.PUBLIC,
            ),
            delivery_idempotency_key("event-two", 1, "sub-2", 1, self.recipient),
        )
        await self.deliveries.create_event(event_one)
        await self.deliveries.create_event(event_two)
        member_one = DigestMember("sub-1", 1, "event-one", 1)
        member_two = DigestMember("sub-2", 1, "event-two", 1)
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
        await self.deliveries.create_envelope(
            DigestEnvelope(
                "envelope-members",
                "window-members",
                self.recipient,
                (member_one, member_two),
                member_associations=associations,
            )
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
        profile = DigestScheduleProfile(
            "UTC",
            "12:00",
            86400,
            DstFoldPolicy.FIRST_OCCURRENCE,
            DstGapPolicy.SKIP,
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
            DisplayDocument(
                "PRIVATE-REVOKED",
                "subject",
                (TextBlock("private body"),),
                privacy=Privacy.PRIVATE,
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
            DisplayDocument(
                "PUBLIC-VALID", "subject", (TextBlock("body"),), privacy=Privacy.PUBLIC
            ),
            delivery_idempotency_key(
                "event-final-valid", 1, other.subscription_id, 1, self.recipient
            ),
        )
        await self.deliveries.create_event(revoked_event)
        await self.deliveries.create_event(valid_event)
        revoked_member = DigestMember("sub-1", 2, revoked_event.event_key, 1)
        valid_member = DigestMember(other.subscription_id, 1, valid_event.event_key, 1)
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
        await self.deliveries.create_envelope(
            DigestEnvelope(
                "envelope-final-route",
                window.window_id,
                self.recipient,
                (revoked_member, valid_member),
                member_associations=associations,
            )
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
        member = DigestMember("sub-1", 1, "event-all-revoked", 1)
        event = DeliveryEvent(
            "event-all-revoked",
            1,
            "sub-1",
            1,
            "u1",
            None,
            self.recipient,
            DisplayDocument(
                "private revoked title",
                "subject",
                (TextBlock("private body"),),
                privacy=Privacy.PRIVATE,
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
        await self.deliveries.create_envelope(
            DigestEnvelope(
                "envelope-all-revoked",
                window.window_id,
                self.recipient,
                (member,),
                member_associations=(association,),
            )
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
