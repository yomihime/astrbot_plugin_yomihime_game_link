from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ygl_test_subject.core.contracts.services import (
    Grant,
    GrantStatus,
    SubscriptionOutput,
)
from ygl_test_subject.core.contracts.subscriptions import (
    DeliveryAttempt,
    DeliveryEvent,
    DeliveryState,
    delivery_idempotency_key,
    digest_envelope_idempotency_key,
)
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.ports import MessageStatus, RevisionConflict, SecretOwner
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.presentation.rendering import (
    GenericDisplayRenderer,
    RenderingBounds,
)
from ygl_test_subject.services.b04_runtime import SQLiteResourceVisibilityProbe
from ygl_test_subject.services.module_services import (
    ModuleServicesFactory,
)
from ygl_test_subject.services.scheduler import digest_window_for_date

from tests.fixtures.b04_runtime import (
    DeterministicClock,
    OfflineHttpTransport,
    RenderBatchBarrier,
    build_runtime,
    create_subscription_event_fixture,
    replace_subscription_gate_fixture,
    synthetic_subscription_gate_bindings,
)
from yomihime_game_link_sdk.contexts import (
    InvocationConversationKind,
    InvocationOrigin,
    InvocationSubscriptionScope,
)
from yomihime_game_link_sdk.display import (
    DisplayDocument,
    ImageBlock,
    Privacy,
    TextBlock,
)
from yomihime_game_link_sdk.errors import (
    AccessDenied,
    InvalidInvocation,
    ServiceUnavailable,
)
from yomihime_game_link_sdk.results import CapabilityResult, FactDocument, ResultStatus
from yomihime_game_link_sdk.storage import OwnerScope, OwnershipKind, ResourceMetadata
from yomihime_game_link_sdk.subscriptions import (
    ConversationKind,
    ConversationRef,
    DigestScheduleProfile,
    DstFoldPolicy,
    DstGapPolicy,
    ObservationCompleteness,
    SubscriptionRequest,
)


class B04RuntimeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bindings = synthetic_subscription_gate_bindings(("sample/feed",))
        self.clock = DeterministicClock()
        self.runtime = build_runtime(self.root, clock=self.clock)
        self.module_services = self.runtime.module_factory.for_module("sample/feed")
        self.subscriptions = self.module_services.subscriptions

    def tearDown(self) -> None:
        self.temp.cleanup()

    async def _conversation(
        self, user: str, *, kind: ConversationKind = ConversationKind.DIRECT
    ) -> ConversationRef:
        ref = validate_contract(
            ConversationRef("test-adapter", kind, user, f"private-route-{user}")
        )
        await self.runtime.host_repositories.conversations.save(ref)
        return ref

    async def _create(
        self,
        user: str,
        *,
        conversation_id: str | None = None,
        type_id: str = "feed-alert",
        minimum: int = 0,
        mode: str = "instant",
        digest_schedule: DigestScheduleProfile | None = None,
        kind: ConversationKind = ConversationKind.DIRECT,
        grant: Grant | None = None,
    ):
        route = conversation_id or user
        await self._conversation(route, kind=kind)
        invocation = self.runtime.invocation(user, route, grant=grant)
        request = validate_contract(
            SubscriptionRequest(
                type_id,
                {"region": "global"},
                {"minimum": minimum},
                mode,
                digest_schedule,
            )
        )
        return await self.subscriptions.create_request(invocation, request)

    @staticmethod
    def _principal(actor: str) -> str:
        return {"alice": "principal-alice", "bob": "principal-bob"}[actor]

    async def _grant(
        self,
        actor: str,
        *,
        grant_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> Grant:
        principal_id = self._principal(actor)
        identifier = grant_id or f"grant-{actor}"
        secret_owner = SecretOwner(
            principal_id,
            "sample/feed",
            "credential",
            f"seed-{identifier}",
        )
        receipt = await self.runtime.host_repositories.secret_store.put(
            b"b04-test-secret", owner=secret_owner
        )
        grant = Grant(
            identifier,
            1,
            principal_id,
            "sample/feed",
            f"account-{actor}",
            ("read",),
            receipt.secret_ref,
            GrantStatus.ACTIVE,
            expires_at,
        )
        await self.runtime.host_repositories.authorization.create_grant(
            grant, expected_revision=0
        )
        return grant

    def _count(self, table: str) -> int:
        connection = self.runtime.database.connect()
        try:
            return int(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            )
        finally:
            connection.close()

    async def test_i01_i02_b03_compatibility_bundle_and_b04_module_bundle(self):
        base_factory = ModuleServicesFactory(
            self.runtime.registry,
            self.runtime.issuer,
            self.runtime.lifecycle,
            self.runtime.host_repositories,
            OfflineHttpTransport(),
            config_principal_id="host-config",
            identity_namespace="b04-tests",
            utc_clock=self.clock,
        )
        base = base_factory.for_module("sample/feed")
        original = base.subscriptions
        self.assertIsNot(original, self.runtime.operations)
        before = tuple(
            self._count(table)
            for table in (
                "b04_subscriptions",
                "b04_collection_jobs",
                "b04_delivery_events",
            )
        )
        command = self.runtime.invocation("alice", "alice")
        request = validate_contract(
            SubscriptionRequest(
                "feed-alert", {"region": "global"}, {"minimum": 1}, "instant"
            )
        )
        with self.assertRaises(ServiceUnavailable) as caught:
            await original.create_request(command, request)
        self.assertEqual(caught.exception.code, "service_unavailable")
        with self.assertRaises(ServiceUnavailable):
            await original.revise_request(command, request)
        with self.assertRaises(ServiceUnavailable):
            await original.list_current(command)
        with self.assertRaises(ServiceUnavailable):
            await original.cancel(command, "sub-missing", expected_revision=1)
        non_command = self.runtime.invocation(
            "alice", "alice", origin=InvocationOrigin.LLM_TOOL
        )
        for call in (
            original.create_request(non_command, request),
            original.revise_request(non_command, request),
            original.list_current(non_command),
            original.cancel(non_command, "sub-missing", expected_revision=1),
        ):
            with self.assertRaises(AccessDenied):
                await call
        self.assertEqual(
            before,
            tuple(
                self._count(table)
                for table in (
                    "b04_subscriptions",
                    "b04_collection_jobs",
                    "b04_delivery_events",
                )
            ),
        )

        b04_bundle = self.module_services
        self.assertIsNot(b04_bundle, base)
        self.assertIsNot(b04_bundle.subscriptions, self.runtime.operations)
        foreign_module = self.runtime.issuer.issue(
            origin=InvocationOrigin.COMMAND,
            module_id="sample/other",
            module_epoch=1,
            registry_revision=self.runtime.registry.snapshot().revision,
            actor_id="alice",
            conversation_id="alice",
            adapter_id="test-adapter",
            capability_id="manage",
        )
        with self.assertRaises(InvalidInvocation):
            await b04_bundle.subscriptions.list_current(foreign_module)
        await self._conversation("alice")
        tool = self.runtime.invocation(
            "alice", "alice", origin=InvocationOrigin.LLM_TOOL
        )
        before_tool = self._count("b04_subscriptions")
        with self.assertRaises(AccessDenied):
            await b04_bundle.subscriptions.create_request(
                tool,
                validate_contract(
                    SubscriptionRequest(
                        "feed-alert", {"region": "global"}, {"minimum": 1}, "instant"
                    )
                ),
            )
        self.assertEqual(self._count("b04_subscriptions"), before_tool)
        created = await b04_bundle.subscriptions.create_request(
            self.runtime.invocation("alice", "alice"),
            validate_contract(
                SubscriptionRequest(
                    "feed-alert", {"region": "global"}, {"minimum": 1}, "instant"
                )
            ),
        )
        listed = await b04_bundle.subscriptions.list_current(
            self.runtime.invocation("alice", "alice")
        )
        self.assertEqual(listed, (created,))
        revised = await b04_bundle.subscriptions.revise_request(
            self.runtime.invocation("alice", "alice"),
            validate_contract(
                SubscriptionRequest(
                    "feed-alert",
                    {"region": "global"},
                    {"minimum": 2},
                    "instant",
                    subscription_id=created.subscription_id,
                    expected_revision=1,
                )
            ),
        )
        self.assertEqual(revised.revision, 2)
        with self.assertRaises(RevisionConflict):
            await b04_bundle.subscriptions.revise_request(
                self.runtime.invocation("alice", "alice"),
                validate_contract(
                    SubscriptionRequest(
                        "feed-alert",
                        {"region": "global"},
                        {"minimum": 3},
                        "instant",
                        subscription_id=created.subscription_id,
                        expected_revision=1,
                    )
                ),
            )
        await b04_bundle.subscriptions.cancel(
            self.runtime.invocation("alice", "alice"),
            created.subscription_id,
            expected_revision=2,
        )
        record = await self.runtime.repositories.subscriptions.current(
            created.subscription_id
        )
        self.assertEqual(record.status.value, "cancelled")

    async def test_i03_i05_i08_shared_public_collection_matches_independently(self):
        alice = await self._create("alice", minimum=5)
        bob = await self._create("bob", minimum=50)
        alice_record = await self.runtime.repositories.subscriptions.current(
            alice.subscription_id
        )
        bob_record = await self.runtime.repositories.subscriptions.current(
            bob.subscription_id
        )
        self.assertEqual(alice_record.collection_key, bob_record.collection_key)
        self.assertEqual(self._count("b04_collection_jobs"), 1)
        self.assertEqual(self._count("b04_subscription_jobs"), 2)
        await self.runtime.scheduler.run_due_page()
        self.assertEqual(self.runtime.collector.calls, 1)
        self.assertEqual(self._count("b04_delivery_events"), 1)
        deliveries = await self.runtime.repositories.deliveries.list_due_events(
            now=self.clock(), limit=10, after_cursor=None
        )
        self.assertEqual(len(deliveries), 1)
        result = await self.runtime.delivery.dispatch_event(
            deliveries[0].event_key,
            deliveries[0].event_version,
            deliveries[0].subscription_id,
            deliveries[0].subscription_revision,
        )
        self.assertEqual(result.state, DeliveryState.SENT)
        self.assertEqual(len(self.runtime.message_port.calls), 1)
        self.assertEqual(self.runtime.message_port.calls[0][0].conversation_id, "alice")
        # The same persisted event cannot be claimed and delivered a second time.
        replay = await self.runtime.delivery.dispatch_event(
            deliveries[0].event_key,
            deliveries[0].event_version,
            deliveries[0].subscription_id,
            deliveries[0].subscription_revision,
        )
        self.assertEqual(replay.state, DeliveryState.SENT)
        self.assertEqual(len(self.runtime.message_port.calls), 1)

    async def test_i04_i21_i22_private_and_group_route_boundaries(self):
        await self._conversation("group", kind=ConversationKind.GROUP)
        grant = await self._grant("alice", expires_at=self.clock() + timedelta(hours=1))
        module_manifest = (
            self.runtime.registry.snapshot().module("sample/feed").manifest
        )
        # The declared test schedule is public; a forged group route still must
        # be rejected if a private AUTHORIZED subscription is proposed.
        invocation = self.runtime.invocation("alice", "group", grant=grant)
        before = self._count("b04_subscriptions")
        with self.assertRaises(AccessDenied):
            await self.subscriptions.create_request(
                invocation,
                validate_contract(
                    SubscriptionRequest(
                        "private-alert", {"region": "global"}, {"minimum": 1}, "instant"
                    )
                ),
            )
        with self.assertRaises(AccessDenied):
            await self.subscriptions.create_request(
                invocation,
                validate_contract(
                    SubscriptionRequest(
                        "private-alert",
                        {"region": "global"},
                        {"minimum": 1},
                        "digest",
                        validate_contract(
                            DigestScheduleProfile(
                                "UTC",
                                "12:00",
                                3600,
                                DstFoldPolicy.FIRST_OCCURRENCE,
                                DstGapPolicy.SKIP,
                            )
                        ),
                    )
                ),
            )
        self.assertEqual(self._count("b04_subscriptions"), before)
        self.assertEqual(self._count("b04_digest_members"), 0)
        self.assertEqual(self.runtime.message_port.calls, [])
        self.assertEqual(
            module_manifest.schedules[0].shared_scope, OwnershipKind.PUBLIC
        )
        public_group = await self._create(
            "alice", conversation_id="group", kind=ConversationKind.GROUP
        )
        public_record = await self.runtime.repositories.subscriptions.current(
            public_group.subscription_id
        )
        self.assertEqual(public_record.collection_key.scope, OwnerScope.public())
        self.assertIsNone(public_record.grant)
        self.assertEqual(self.runtime.message_port.calls, [])
        # Even an invalid persisted private event aimed at a GROUP route is
        # rejected at the final delivery boundary and its bytes never escape.
        group_event_key = "invalid-private-group"
        group_recipient = await self.runtime.host_repositories.conversations.current(
            "test-adapter", "group"
        )
        forged = await create_subscription_event_fixture(
            self.runtime.repositories.deliveries,
            self.bindings,
            DeliveryEvent(
                group_event_key,
                1,
                public_record.subscription_id,
                public_record.revision,
                public_record.owner_id,
                None,
                group_recipient,
                validate_contract(
                    DisplayDocument(
                        "Secret",
                        "Must not send",
                        (validate_contract(TextBlock("PRIVATE-ONLY")),),
                        privacy=Privacy.PRIVATE,
                    )
                ),
                delivery_idempotency_key(
                    group_event_key,
                    1,
                    public_record.subscription_id,
                    public_record.revision,
                    group_recipient,
                ),
            ),
        )
        rejected = await self.runtime.delivery.dispatch_event(
            forged.event_key,
            forged.event_version,
            forged.subscription_id,
            forged.subscription_revision,
        )
        self.assertEqual(rejected.state, DeliveryState.CANCELLED)
        self.assertEqual(self.runtime.message_port.calls, [])

        # Simulate a corrupt persisted direct route whose ConversationRef lost
        # its kind during decoding. The delivery read adapter returns it once;
        # the current subscription route comparison must fail closed.
        alice = await self._create("alice", minimum=0)
        alice_record = await self.runtime.repositories.subscriptions.current(
            alice.subscription_id
        )
        corrupt_key = "invalid-missing-route-kind"
        valid = await create_subscription_event_fixture(
            self.runtime.repositories.deliveries,
            self.bindings,
            DeliveryEvent(
                corrupt_key,
                1,
                alice_record.subscription_id,
                alice_record.revision,
                alice_record.owner_id,
                None,
                alice_record.recipient,
                validate_contract(
                    DisplayDocument(
                        "Secret",
                        "Must not send",
                        (validate_contract(TextBlock("MISSING-KIND-PRIVATE")),),
                        privacy=Privacy.PRIVATE,
                    )
                ),
                delivery_idempotency_key(
                    corrupt_key,
                    1,
                    alice_record.subscription_id,
                    alice_record.revision,
                    alice_record.recipient,
                ),
            ),
        )
        malformed_recipient = object.__new__(ConversationRef)
        object.__setattr__(malformed_recipient, "adapter_id", "test-adapter")
        object.__setattr__(malformed_recipient, "kind", None)
        object.__setattr__(malformed_recipient, "conversation_id", "alice")
        object.__setattr__(malformed_recipient, "delivery_route", "private-route-alice")
        object.__setattr__(valid, "recipient", malformed_recipient)

        class CorruptStoredRead:
            def __init__(self, delegate):
                self.delegate = delegate

            async def current_event(self, event_key, event_version, **kwargs):
                if event_key == corrupt_key:
                    return valid
                return await self.delegate.current_event(
                    event_key, event_version, **kwargs
                )

            def __getattr__(self, name):
                return getattr(self.delegate, name)

        original_repository = self.runtime.delivery._deliveries
        self.runtime.delivery._deliveries = CorruptStoredRead(original_repository)
        try:
            corrupt_result = await self.runtime.delivery.dispatch_event(
                corrupt_key,
                1,
                alice_record.subscription_id,
                alice_record.revision,
            )
        finally:
            self.runtime.delivery._deliveries = original_repository
        self.assertEqual(corrupt_result.state, DeliveryState.CANCELLED)
        self.assertEqual(self.runtime.message_port.calls, [])

    async def test_i04_user_scoped_collection_and_private_events_do_not_cross_users(
        self,
    ):
        alice = await self._create("alice", type_id="user-alert", minimum=0)
        bob = await self._create("bob", type_id="user-alert", minimum=0)
        alice_record = await self.runtime.repositories.subscriptions.current(
            alice.subscription_id
        )
        bob_record = await self.runtime.repositories.subscriptions.current(
            bob.subscription_id
        )
        self.assertEqual(
            alice_record.collection_key.scope, OwnerScope.user("principal-alice")
        )
        self.assertEqual(
            bob_record.collection_key.scope, OwnerScope.user("principal-bob")
        )
        self.assertNotEqual(alice_record.collection_key, bob_record.collection_key)

        await self.runtime.scheduler.run_due_page()
        self.assertEqual(self.runtime.collector.calls, 2)
        due = await self.runtime.repositories.deliveries.list_due_events(
            now=self.clock(), limit=10, after_cursor=None
        )
        events = {event.subscription_id: event for event in due}
        self.assertEqual(set(events), {alice.subscription_id, bob.subscription_id})
        for record in (alice_record, bob_record):
            event = events[record.subscription_id]
            self.assertEqual(event.owner_id, record.owner_id)
            expected_actor = "alice" if record.owner_id == "principal-alice" else "bob"
            self.assertEqual(event.recipient.conversation_id, expected_actor)
            self.assertEqual(event.display_data.privacy, Privacy.PRIVATE)
            self.assertIn(
                record.subscription_id, event.display_data.ordered_blocks[0].text
            )

        for event in events.values():
            await self.runtime.delivery.dispatch_event(
                event.event_key,
                event.event_version,
                event.subscription_id,
                event.subscription_revision,
            )
        self.assertEqual(
            {
                target.conversation_id
                for target, _payload in self.runtime.message_port.calls
            },
            {"alice", "bob"},
        )

    async def test_i04_authorized_collection_isolated_by_grant_and_owner(self):
        grants = []
        views = []
        for owner in ("alice", "bob"):
            grant = await self._grant(
                owner,
                grant_id=f"grant-{owner}",
                expires_at=self.clock() + timedelta(hours=1),
            )
            grants.append(grant)
            views.append(
                await self._create(
                    owner, type_id="private-alert", minimum=0, grant=grant
                )
            )
        records = [
            await self.runtime.repositories.subscriptions.current(view.subscription_id)
            for view in views
        ]
        self.assertEqual(
            [record.collection_key.scope.kind for record in records],
            [OwnershipKind.AUTHORIZED, OwnershipKind.AUTHORIZED],
        )
        self.assertNotEqual(
            records[0].collection_key.scope.grant,
            records[1].collection_key.scope.grant,
        )
        await self.runtime.scheduler.run_due_page()
        self.assertEqual(self.runtime.collector.calls, 2)
        due = await self.runtime.repositories.deliveries.list_due_events(
            now=self.clock(), limit=10, after_cursor=None
        )
        self.assertEqual(
            {event.owner_id for event in due},
            {"principal-alice", "principal-bob"},
        )
        self.assertTrue(
            all(event.display_data.privacy is Privacy.PRIVATE for event in due)
        )
        for event in due:
            await self.runtime.delivery.dispatch_event(
                event.event_key,
                event.event_version,
                event.subscription_id,
                event.subscription_revision,
            )
        self.assertEqual(
            {
                target.conversation_id
                for target, _payload in self.runtime.message_port.calls
            },
            {"alice", "bob"},
        )

    async def test_i06_authorized_grant_revocation_during_collection_blocks_commit(
        self,
    ):
        grant = await self._grant("alice", expires_at=self.clock() + timedelta(hours=1))
        await self._create("alice", type_id="private-alert", grant=grant)
        self.runtime.collector.block = True
        run = asyncio.create_task(self.runtime.scheduler.run_due_page())
        await asyncio.wait_for(self.runtime.collector.started.wait(), timeout=2)
        await self.runtime.host_repositories.authorization.revoke_grant(
            grant, expected_revision=grant.revision
        )
        self.runtime.collector.release.set()
        await asyncio.wait_for(run, timeout=2)
        self.assertEqual(self._count("b04_observations"), 0)
        self.assertEqual(self._count("b04_delivery_events"), 0)
        self.assertEqual(self.runtime.message_port.calls, [])

    async def test_i09_authorized_revoke_before_send_blocks_private_event(self):
        grant = await self._grant("alice", expires_at=self.clock() + timedelta(hours=1))
        view = await self._create("alice", type_id="private-alert", grant=grant)
        record = await self.runtime.repositories.subscriptions.current(
            view.subscription_id
        )
        key = "authorized-revoke-before-send"
        event = await create_subscription_event_fixture(
            self.runtime.repositories.deliveries,
            self.bindings,
            DeliveryEvent(
                key,
                1,
                record.subscription_id,
                record.revision,
                record.owner_id,
                record.grant,
                record.recipient,
                validate_contract(
                    DisplayDocument(
                        "Private",
                        "Payload",
                        (validate_contract(TextBlock("do not send")),),
                        privacy=Privacy.PRIVATE,
                    )
                ),
                delivery_idempotency_key(
                    key,
                    1,
                    record.subscription_id,
                    record.revision,
                    record.recipient,
                ),
            ),
        )
        await self.runtime.host_repositories.authorization.revoke_grant(
            grant, expected_revision=grant.revision
        )
        result = await self.runtime.delivery.dispatch_event(
            event.event_key,
            event.event_version,
            event.subscription_id,
            event.subscription_revision,
        )
        self.assertEqual(result.state, DeliveryState.CANCELLED)
        self.assertEqual(self.runtime.message_port.calls, [])

    async def test_i21_authorized_revise_rejects_group_and_changed_direct_route(self):
        grant = await self._grant("alice", expires_at=self.clock() + timedelta(hours=1))
        due_at = self.clock() + timedelta(minutes=5)
        profile = validate_contract(
            DigestScheduleProfile(
                "UTC",
                due_at.strftime("%H:%M"),
                3600,
                DstFoldPolicy.FIRST_OCCURRENCE,
                DstGapPolicy.SKIP,
            )
        )
        view = await self._create(
            "alice",
            type_id="private-alert",
            minimum=0,
            mode="digest",
            digest_schedule=profile,
            grant=grant,
        )
        record = await self.runtime.repositories.subscriptions.current(
            view.subscription_id
        )
        await self.runtime.scheduler.create_digest_windows_page(
            local_dates=(self.clock().date(),)
        )
        await self.runtime.scheduler.run_due_page()
        original_members = self._count("b04_digest_members")
        self.assertEqual(original_members, 1)
        original_windows = self._count("b04_digest_windows")
        request = validate_contract(
            SubscriptionRequest(
                "private-alert",
                {"region": "global"},
                {"minimum": 1},
                "digest",
                profile,
                subscription_id=view.subscription_id,
                expected_revision=record.revision,
            )
        )

        def set_persisted_conversation(*, kind: ConversationKind, route: str) -> None:
            connection = self.runtime.database.connect()
            try:
                cursor = connection.execute(
                    "UPDATE conversations SET kind=?,delivery_route=? "
                    "WHERE adapter_id='test-adapter' AND conversation_id='alice'",
                    (kind.value, route),
                )
                self.assertEqual(cursor.rowcount, 1)
                connection.commit()
            finally:
                connection.close()

        original_link = await self.runtime.repositories.jobs.current_for_subscription(
            view.subscription_id
        )
        original_members = self._count("b04_digest_members")
        original_windows = self._count("b04_digest_windows")
        original_job_links = self._count("b04_subscription_jobs")

        set_persisted_conversation(
            kind=ConversationKind.GROUP, route="group-route-alice"
        )
        group_invocation = self.runtime.invocation("alice", "alice", grant=grant)
        self.assertEqual(
            await self.runtime.resolver.resolve(group_invocation),
            validate_contract(
                ConversationRef(
                    "test-adapter",
                    ConversationKind.GROUP,
                    "alice",
                    "group-route-alice",
                )
            ),
        )
        with self.assertRaises(AccessDenied) as group_rejected:
            await self.subscriptions.revise_request(group_invocation, request)
        self.assertEqual(group_rejected.exception.code, "access_denied")

        set_persisted_conversation(
            kind=ConversationKind.DIRECT, route="private-route-alice-reassigned"
        )
        changed_route_invocation = self.runtime.invocation(
            "alice", "alice", grant=grant
        )
        self.assertEqual(
            await self.runtime.resolver.resolve(changed_route_invocation),
            validate_contract(
                ConversationRef(
                    "test-adapter",
                    ConversationKind.DIRECT,
                    "alice",
                    "private-route-alice-reassigned",
                )
            ),
        )
        with self.assertRaises(AccessDenied) as route_rejected:
            await self.subscriptions.revise_request(changed_route_invocation, request)
        self.assertEqual(route_rejected.exception.code, "access_denied")
        current = await self.runtime.repositories.subscriptions.current(
            view.subscription_id
        )
        current_link = await self.runtime.repositories.jobs.current_for_subscription(
            view.subscription_id
        )
        self.assertEqual(current.revision, record.revision)
        self.assertEqual(current.recipient, record.recipient)
        self.assertEqual(self._count("b04_digest_members"), original_members)
        self.assertEqual(self._count("b04_digest_windows"), original_windows)
        self.assertEqual(self._count("b04_subscription_jobs"), original_job_links)
        self.assertEqual(current_link, original_link)
        self.assertEqual(self.runtime.message_port.calls, [])

    async def test_i21_i22_authorized_forged_group_and_missing_kind_never_send(self):
        await self._conversation("group", kind=ConversationKind.GROUP)
        grant = await self._grant("alice", expires_at=self.clock() + timedelta(hours=1))
        view = await self._create(
            "alice", type_id="private-alert", minimum=0, grant=grant
        )
        record = await self.runtime.repositories.subscriptions.current(
            view.subscription_id
        )
        self.assertEqual(
            record.collection_key.scope,
            OwnerScope.authorized("principal-alice", record.grant),
        )
        self.assertEqual(record.grant.grant_id, grant.grant_id)
        current_grant = (
            await self.runtime.host_repositories.authorization.current_grant(
                grant.grant_id
            )
        )
        self.assertEqual(current_grant.status, GrantStatus.ACTIVE)

        group = await self.runtime.host_repositories.conversations.current(
            "test-adapter", "group"
        )
        group_key = "authorized-forged-group-route"
        group_event = await create_subscription_event_fixture(
            self.runtime.repositories.deliveries,
            self.bindings,
            DeliveryEvent(
                group_key,
                1,
                record.subscription_id,
                record.revision,
                record.owner_id,
                record.grant,
                record.recipient,
                validate_contract(
                    DisplayDocument(
                        "Private alert",
                        "Forbidden group destination",
                        (validate_contract(TextBlock("AUTHORIZED-GROUP-SECRET")),),
                        privacy=Privacy.PRIVATE,
                    )
                ),
                delivery_idempotency_key(
                    group_key,
                    1,
                    record.subscription_id,
                    record.revision,
                    record.recipient,
                ),
            ),
        )
        forged_group_read = object.__new__(DeliveryEvent)
        for field in DeliveryEvent.__dataclass_fields__:
            object.__setattr__(
                forged_group_read,
                field,
                group if field == "recipient" else getattr(group_event, field),
            )

        missing_kind_key = "authorized-missing-route-kind"
        direct_event = await create_subscription_event_fixture(
            self.runtime.repositories.deliveries,
            self.bindings,
            DeliveryEvent(
                missing_kind_key,
                1,
                record.subscription_id,
                record.revision,
                record.owner_id,
                record.grant,
                record.recipient,
                validate_contract(
                    DisplayDocument(
                        "Private alert",
                        "Malformed route kind",
                        (
                            validate_contract(
                                TextBlock("AUTHORIZED-MISSING-KIND-SECRET")
                            ),
                        ),
                        privacy=Privacy.PRIVATE,
                    )
                ),
                delivery_idempotency_key(
                    missing_kind_key,
                    1,
                    record.subscription_id,
                    record.revision,
                    record.recipient,
                ),
            ),
        )
        malformed_recipient = object.__new__(ConversationRef)
        object.__setattr__(malformed_recipient, "adapter_id", "test-adapter")
        object.__setattr__(malformed_recipient, "kind", None)
        object.__setattr__(malformed_recipient, "conversation_id", "alice")
        object.__setattr__(malformed_recipient, "delivery_route", "private-route-alice")
        object.__setattr__(direct_event, "recipient", malformed_recipient)

        class CorruptAuthorizedRead:
            def __init__(self, delegate):
                self.delegate = delegate

            async def current_event(self, event_key, event_version, **kwargs):
                if event_key == group_key:
                    return forged_group_read
                if event_key == missing_kind_key:
                    return direct_event
                return await self.delegate.current_event(
                    event_key, event_version, **kwargs
                )

            def __getattr__(self, name):
                return getattr(self.delegate, name)

        original_repository = self.runtime.delivery._deliveries
        self.runtime.delivery._deliveries = CorruptAuthorizedRead(original_repository)
        try:
            group_result = await self.runtime.delivery.dispatch_event(
                group_key,
                1,
                record.subscription_id,
                record.revision,
            )
            missing_kind_result = await self.runtime.delivery.dispatch_event(
                missing_kind_key,
                1,
                record.subscription_id,
                record.revision,
            )
        finally:
            self.runtime.delivery._deliveries = original_repository
        self.assertEqual(group_result.state, DeliveryState.CANCELLED)
        self.assertEqual(missing_kind_result.state, DeliveryState.CANCELLED)
        self.assertEqual(self.runtime.message_port.calls, [])
        self.assertFalse(
            any(
                b"AUTHORIZED-GROUP-SECRET" in call[1].text.encode()
                or b"AUTHORIZED-MISSING-KIND-SECRET" in call[1].text.encode()
                for call in self.runtime.message_port.calls
            )
        )

    async def test_i09_send_timeout_becomes_unknown_and_is_never_auto_retried(self):
        view = await self._create("alice", minimum=0)
        record = await self.runtime.repositories.subscriptions.current(
            view.subscription_id
        )
        timeout_runtime = build_runtime(self.root, clock=self.clock, send_timeout=0.1)
        key = "send-timeout-unknown"
        event = await create_subscription_event_fixture(
            timeout_runtime.repositories.deliveries,
            self.bindings,
            DeliveryEvent(
                key,
                1,
                record.subscription_id,
                record.revision,
                record.owner_id,
                record.grant,
                record.recipient,
                validate_contract(
                    DisplayDocument(
                        "Test", "Timeout", (validate_contract(TextBlock("payload")),)
                    )
                ),
                delivery_idempotency_key(
                    key,
                    1,
                    record.subscription_id,
                    record.revision,
                    record.recipient,
                ),
            ),
        )
        timeout_runtime.message_port.block = True
        dispatch = asyncio.create_task(
            timeout_runtime.delivery.dispatch_event(
                event.event_key,
                event.event_version,
                event.subscription_id,
                event.subscription_revision,
            )
        )
        try:
            await asyncio.wait_for(timeout_runtime.message_port.started.wait(), 2)
            result = await dispatch
        finally:
            timeout_runtime.message_port.release.set()
            await asyncio.gather(dispatch, return_exceptions=True)
        self.assertEqual(result.state, DeliveryState.UNKNOWN)
        saved = await timeout_runtime.repositories.deliveries.current_event(
            event.event_key,
            event.event_version,
            subscription_id=event.subscription_id,
            subscription_revision=event.subscription_revision,
        )
        self.assertEqual(saved.state, DeliveryState.UNKNOWN)
        self.assertEqual(
            await timeout_runtime.repositories.deliveries.list_due_events(
                now=self.clock(), limit=10, after_cursor=None
            ),
            (),
        )
        self.assertEqual(await timeout_runtime.delivery.dispatch_due_events(), 0)
        self.assertEqual(len(timeout_runtime.message_port.calls), 1)

    async def test_i05_complete_partial_and_failed_cursor_semantics(self):
        view = await self._create("alice", minimum=0)
        record = await self.runtime.repositories.subscriptions.current(
            view.subscription_id
        )
        await self.runtime.scheduler.run_due_page()
        complete = await self.runtime.repositories.scheduler.current_evaluation(
            view.subscription_id, record.collection_key
        )
        self.assertEqual(complete.cursor.completeness, ObservationCompleteness.COMPLETE)
        self.assertEqual(complete.cursor.covered_ids, ())

        self.clock.advance(timedelta(seconds=61))
        self.runtime.collector.next_completeness = ObservationCompleteness.PARTIAL
        await self.runtime.scheduler.run_due_page()
        partial = await self.runtime.repositories.scheduler.current_evaluation(
            view.subscription_id, record.collection_key
        )
        self.assertEqual(partial.cursor.completeness, ObservationCompleteness.PARTIAL)
        self.assertEqual(partial.cursor.covered_ids, ("item-a",))
        prior_state, prior_cursor, prior_observation = (
            partial.state,
            partial.cursor,
            partial.observation,
        )

        self.clock.advance(timedelta(seconds=61))
        self.runtime.collector.next_completeness = ObservationCompleteness.FAILED
        await self.runtime.scheduler.run_due_page()
        failed = await self.runtime.repositories.scheduler.current_evaluation(
            view.subscription_id, record.collection_key
        )
        self.assertEqual(
            (failed.state, failed.cursor, failed.observation),
            (prior_state, prior_cursor, prior_observation),
        )

    async def test_pause_resume_during_collector_await_discards_old_work_and_restarts_previous(
        self,
    ):
        await self._create("alice", minimum=0)
        await self.runtime.scheduler.run_due_page()
        before = self._count("b04_observations")
        self.assertEqual(before, 1)
        self.clock.advance(timedelta(seconds=61))
        candidate = (
            await self.runtime.repositories.scheduler.list_due_jobs(
                now=self.clock(), limit=10
            )
        )[0]
        self.runtime.collector.block = True
        run = asyncio.create_task(self.runtime.scheduler.run_due_job(candidate))
        await asyncio.wait_for(self.runtime.collector.started.wait(), timeout=2)
        self.assertIsNotNone(self.runtime.collector.previous_inputs[-1])
        bindings = self.runtime.repositories.lifecycle._subscription_gate_bindings
        await replace_subscription_gate_fixture(
            self.runtime.database, bindings, "sample/feed", False, self.clock()
        )
        await replace_subscription_gate_fixture(
            self.runtime.database, bindings, "sample/feed", True, self.clock()
        )
        self.runtime.collector.release.set()
        self.assertIsNone(await asyncio.wait_for(run, timeout=2))
        self.assertEqual(self._count("b04_observations"), before)
        fresh = await self.runtime.scheduler.run_due_job(candidate)
        self.assertTrue(fresh.committed)
        self.assertIsNone(self.runtime.collector.previous_inputs[-1])
        self.assertEqual(self._count("b04_observations"), before + 1)
        self.assertEqual(self.runtime.message_port.calls, [])

    async def test_i06_disable_cancels_collection_without_commit(self):
        await self._create("alice", minimum=0)
        self.runtime.collector.block = True
        run = asyncio.create_task(self.runtime.scheduler.run_due_page())
        await self.runtime.collector.started.wait()
        await self.runtime.lifecycle.stop("sample/feed")
        self.runtime.collector.release.set()
        with self.assertRaises(asyncio.CancelledError):
            await run
        self.assertEqual(self._count("b04_observations"), 0)
        self.assertEqual(self._count("b04_delivery_events"), 0)

    async def test_i15_local_collection_cancellation_releases_lease_without_commit(
        self,
    ):
        view = await self._create("alice", minimum=0)
        record = await self.runtime.repositories.subscriptions.current(
            view.subscription_id
        )
        candidate = (
            await self.runtime.repositories.scheduler.list_due_jobs(
                now=self.clock(), limit=10
            )
        )[0]
        self.runtime.collector.block = True
        running = asyncio.create_task(self.runtime.scheduler.run_due_job(candidate))
        await self.runtime.collector.started.wait()
        self.assertTrue(
            await self.runtime.scheduler.cancel_local(record.collection_key)
        )
        with self.assertRaises(asyncio.CancelledError):
            await running
        self.assertEqual(self._count("b04_observations"), 0)
        self.assertEqual(self._count("b04_delivery_events"), 0)
        self.assertEqual(
            len(
                await self.runtime.repositories.scheduler.list_due_jobs(
                    now=self.clock(), limit=10
                )
            ),
            1,
        )

    async def test_i07_concurrent_revision_cas_has_one_winner(self):
        view = await self._create("alice", minimum=1)

        def request(threshold):
            return validate_contract(
                SubscriptionRequest(
                    "feed-alert",
                    {"region": "global"},
                    {"minimum": threshold},
                    "instant",
                    subscription_id=view.subscription_id,
                    expected_revision=1,
                )
            )

        results = await asyncio.gather(
            self.subscriptions.revise_request(
                self.runtime.invocation("alice", "alice"), request(2)
            ),
            self.subscriptions.revise_request(
                self.runtime.invocation("alice", "alice"), request(3)
            ),
            return_exceptions=True,
        )
        self.assertEqual(
            sum(not isinstance(result, Exception) for result in results), 1
        )
        self.assertEqual(
            sum(isinstance(result, RevisionConflict) for result in results), 1
        )
        saved = await self.runtime.repositories.subscriptions.current(
            view.subscription_id
        )
        self.assertEqual(saved.revision, 2)

    async def test_i07_concurrent_creates_share_only_the_collection_job(self):
        await self._conversation("alice")

        def request():
            return validate_contract(
                SubscriptionRequest(
                    "feed-alert",
                    {"region": "global"},
                    {"minimum": 1},
                    "instant",
                )
            )

        first, second = await asyncio.gather(
            self.subscriptions.create_request(
                self.runtime.invocation("alice", "alice"), request()
            ),
            self.subscriptions.create_request(
                self.runtime.invocation("alice", "alice"), request()
            ),
        )
        self.assertNotEqual(first.subscription_id, second.subscription_id)
        first_record = await self.runtime.repositories.subscriptions.current(
            first.subscription_id
        )
        second_record = await self.runtime.repositories.subscriptions.current(
            second.subscription_id
        )
        self.assertEqual(first_record.collection_key, second_record.collection_key)
        self.assertEqual(self._count("b04_subscriptions"), 2)
        self.assertEqual(self._count("b04_collection_jobs"), 1)

    async def test_i07_cancel_racing_collection_match_prevents_new_event(self):
        view = await self._create("alice", minimum=0)
        self.runtime.collector.block = True
        run = asyncio.create_task(self.runtime.scheduler.run_due_page())
        await asyncio.wait_for(self.runtime.collector.started.wait(), timeout=2)
        cancelled = await self.subscriptions.cancel(
            self.runtime.invocation("alice", "alice"),
            view.subscription_id,
            expected_revision=1,
        )
        self.assertIsNone(cancelled)
        cancelled_record = await self.runtime.repositories.subscriptions.current(
            view.subscription_id
        )
        self.assertEqual(cancelled_record.status.value, "cancelled")
        self.runtime.collector.release.set()
        await asyncio.wait_for(run, timeout=2)
        self.assertEqual(self._count("b04_delivery_events"), 0)
        snapshot = await self.runtime.repositories.scheduler.current_evaluation(
            view.subscription_id, cancelled_record.collection_key
        )
        self.assertIsNotNone(snapshot)
        self.assertIsNone(snapshot.state)
        self.assertIsNone(snapshot.cursor)

    async def test_i09_i10_message_unknown_restart_and_private_resource_probe(self):
        await self._create("alice", minimum=0)
        await self.runtime.scheduler.run_due_page()
        due = await self.runtime.repositories.deliveries.list_due_events(
            now=self.clock(), limit=10, after_cursor=None
        )
        self.assertEqual(len(due), 1)
        self.runtime.message_port.outcomes.append(MessageStatus.UNKNOWN)
        await self.runtime.delivery.dispatch_event(
            due[0].event_key,
            due[0].event_version,
            due[0].subscription_id,
            due[0].subscription_revision,
        )
        self.assertEqual(len(self.runtime.message_port.calls), 1)
        current = await self.runtime.repositories.deliveries.current_event(
            due[0].event_key,
            due[0].event_version,
            subscription_id=due[0].subscription_id,
            subscription_revision=due[0].subscription_revision,
        )
        self.assertEqual(current.state, DeliveryState.UNKNOWN)
        self.assertFalse(
            await self.runtime.output._resource_visibility.contains_non_public_resource_reference(
                "ordinary text"
            )
        )

        asset_id = "asset_privateembedded"
        await self.runtime.host_repositories.resources.register(
            validate_contract(
                ResourceMetadata(
                    asset_id,
                    "text/plain",
                    OwnerScope.user("principal-alice"),
                    12,
                    None,
                    False,
                    1,
                )
            )
        )
        self.assertTrue(
            await self.runtime.resource_visibility.contains_non_public_resource_reference(
                asset_id
            )
        )
        self.assertTrue(
            await self.runtime.resource_visibility.contains_non_public_resource_reference(
                f"prefix::{asset_id}::suffix"
            )
        )
        self.assertFalse(
            await self.runtime.resource_visibility.contains_non_public_resource_reference(
                "ordinary public text"
            )
        )
        await self.runtime.host_repositories.resources.register(
            validate_contract(
                ResourceMetadata(
                    "asset_privateoverbound",
                    "text/plain",
                    OwnerScope.user("principal-bob"),
                    12,
                    None,
                    False,
                    1,
                )
            )
        )
        bounded_probe = SQLiteResourceVisibilityProbe(
            self.runtime.database, max_candidates=1
        )
        with self.assertRaisesRegex(RuntimeError, "work limit"):
            await bounded_probe.contains_non_public_resource_reference("no reference")

        class FailingDatabase(SQLiteDatabase):
            def connect(self):
                raise sqlite3.OperationalError("injected query failure")

        failing_probe = SQLiteResourceVisibilityProbe(
            FailingDatabase(self.root / "failed-probe.sqlite3")
        )
        with self.assertRaisesRegex(RuntimeError, "probe failed"):
            await failing_probe.contains_non_public_resource_reference("anything")
        candidates = (
            validate_contract(FactDocument({f"key {asset_id}": "public"})),
            validate_contract(FactDocument({"nested": {"text": f"source {asset_id}"}})),
            validate_contract(
                FactDocument({"answer": "public"}, sources=(f"provider {asset_id}",))
            ),
        )
        for index, facts in enumerate(candidates):
            blocked_tool = await self.runtime.output.route(
                self.runtime.invocation(
                    "alice", "alice", origin=InvocationOrigin.LLM_TOOL
                ),
                validate_contract(
                    CapabilityResult(
                        f"tool-result-{index}",
                        ResultStatus.SUCCESS,
                        document=validate_contract(
                            DisplayDocument(
                                "Tool",
                                "Facts",
                                (validate_contract(TextBlock("Public")),),
                            )
                        ),
                        model_facts=facts,
                    )
                ),
            )
            self.assertEqual(blocked_tool.error_code, "tool_result_not_public")
        failing_runtime = build_runtime(
            self.root,
            clock=self.clock,
            resource_visibility_probe=failing_probe,
        )
        failed_probe = await failing_runtime.output.route(
            failing_runtime.invocation(
                "alice", "alice", origin=InvocationOrigin.LLM_TOOL
            ),
            validate_contract(
                CapabilityResult(
                    "tool-result-error",
                    ResultStatus.SUCCESS,
                    document=validate_contract(
                        DisplayDocument(
                            "Tool", "Facts", (validate_contract(TextBlock("Public")),)
                        )
                    ),
                    model_facts=validate_contract(FactDocument({"answer": "public"})),
                )
            ),
        )
        self.assertEqual(
            failed_probe.error_code, "tool_resource_visibility_unavailable"
        )
        self.assertEqual(failing_runtime.message_port.calls, [])
        self.assertEqual(len(self.runtime.message_port.calls), 1)
        reopened = type(self.runtime.database)(self.root / "runtime.sqlite3")
        self.assertEqual(reopened.schema_version(), 100)
        recovered = build_runtime(self.root, clock=self.clock)
        self.assertTrue(
            await recovered.resource_visibility.contains_non_public_resource_reference(
                asset_id
            )
        )
        await recovered.delivery.recover_startup()
        self.assertEqual(
            await recovered.repositories.deliveries.current_event(
                due[0].event_key,
                due[0].event_version,
                subscription_id=due[0].subscription_id,
                subscription_revision=due[0].subscription_revision,
            ),
            await self.runtime.repositories.deliveries.current_event(
                due[0].event_key,
                due[0].event_version,
                subscription_id=due[0].subscription_id,
                subscription_revision=due[0].subscription_revision,
            ),
        )
        oversized_asset_id = "oversized-" + ("x" * 600)
        connection = recovered.database.connect()
        try:
            connection.execute(
                "INSERT INTO assets(asset_id,media_type,scope_kind,user_id,grant_id,"
                "grant_revision,size_bytes,expires_at,temporary,revision) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    oversized_asset_id,
                    "text/plain",
                    "authorized",
                    "principal-alice",
                    "grant-alice",
                    1,
                    12,
                    None,
                    0,
                    1,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(RuntimeError, "probe failed"):
            await recovered.resource_visibility.contains_non_public_resource_reference(
                "ordinary text"
            )
        oversized_blocked = await recovered.output.route(
            recovered.invocation("alice", "alice", origin=InvocationOrigin.LLM_TOOL),
            validate_contract(
                CapabilityResult(
                    "oversized-asset-probe",
                    ResultStatus.SUCCESS,
                    document=validate_contract(
                        DisplayDocument(
                            "Tool", "Facts", (validate_contract(TextBlock("Public")),)
                        )
                    ),
                    model_facts=validate_contract(FactDocument({"answer": "public"})),
                )
            ),
        )
        self.assertEqual(
            oversized_blocked.error_code, "tool_resource_visibility_unavailable"
        )
        self.assertEqual(recovered.message_port.calls, [])

    async def test_i09_i11_accepted_failed_unknown_are_distinct_and_unknown_is_not_due(
        self,
    ):
        view = await self._create("alice", minimum=0)
        record = await self.runtime.repositories.subscriptions.current(
            view.subscription_id
        )
        document = validate_contract(
            DisplayDocument(
                "Test", "Delivery", (validate_contract(TextBlock("payload")),)
            )
        )
        events = []
        for version, key in enumerate(
            ("status-accepted", "status-failed", "status-unknown"), 1
        ):
            event = DeliveryEvent(
                key,
                version,
                record.subscription_id,
                record.revision,
                record.owner_id,
                record.grant,
                record.recipient,
                document,
                delivery_idempotency_key(
                    key,
                    version,
                    record.subscription_id,
                    record.revision,
                    record.recipient,
                ),
            )
            events.append(
                await create_subscription_event_fixture(
                    self.runtime.repositories.deliveries, self.bindings, event
                )
            )
        self.runtime.message_port.outcomes.extend(
            (
                MessageStatus.ACCEPTED,
                MessageStatus.FAILED,
                MessageStatus.UNKNOWN,
            )
        )
        outcomes = []
        for event in events:
            result = await self.runtime.delivery.dispatch_event(
                event.event_key,
                event.event_version,
                event.subscription_id,
                event.subscription_revision,
            )
            outcomes.append(result.state)
        self.assertEqual(
            outcomes, [DeliveryState.SENT, DeliveryState.FAILED, DeliveryState.UNKNOWN]
        )
        due = await self.runtime.repositories.deliveries.list_due_events(
            now=self.clock(), limit=10, after_cursor=None
        )
        # FAILED has no retry policy in this fixture, so it is persisted but
        # is not immediately due; UNKNOWN is likewise held for reconciliation.
        self.assertEqual(due, ())

    async def test_i11_failed_retry_times_are_isolated_by_direct_recipient(self):
        first = await self._create("alice", minimum=0)
        second = await self._create("bob", minimum=0)
        records = [
            await self.runtime.repositories.subscriptions.current(
                first.subscription_id
            ),
            await self.runtime.repositories.subscriptions.current(
                second.subscription_id
            ),
        ]
        events = []
        for key, record in zip(("retry-alice", "retry-bob"), records, strict=True):
            events.append(
                await create_subscription_event_fixture(
                    self.runtime.repositories.deliveries,
                    self.bindings,
                    DeliveryEvent(
                        key,
                        1,
                        record.subscription_id,
                        record.revision,
                        record.owner_id,
                        record.grant,
                        record.recipient,
                        validate_contract(
                            DisplayDocument(
                                "Test",
                                "Retry",
                                (validate_contract(TextBlock("payload")),),
                            )
                        ),
                        delivery_idempotency_key(
                            key,
                            1,
                            record.subscription_id,
                            record.revision,
                            record.recipient,
                        ),
                    ),
                )
            )

        def retry_policy(event, completed_at):
            delay = 5 if event.owner_id == "principal-alice" else 10
            return completed_at + timedelta(minutes=delay)

        retry_runtime = build_runtime(
            self.root, clock=self.clock, retry_at=retry_policy
        )
        retry_runtime.message_port.outcomes.extend(
            (MessageStatus.FAILED, MessageStatus.FAILED)
        )
        for event in events:
            result = await retry_runtime.delivery.dispatch_event(
                event.event_key,
                event.event_version,
                event.subscription_id,
                event.subscription_revision,
            )
            self.assertEqual(result.state, DeliveryState.FAILED)
        saved = [
            await retry_runtime.repositories.deliveries.current_event(
                event.event_key,
                event.event_version,
                subscription_id=event.subscription_id,
                subscription_revision=event.subscription_revision,
            )
            for event in events
        ]
        self.assertEqual(
            [item.retry_at for item in saved],
            [
                self.clock() + timedelta(minutes=5),
                self.clock() + timedelta(minutes=10),
            ],
        )
        self.clock.advance(timedelta(minutes=6))
        processed = await retry_runtime.delivery.dispatch_due_events(now=self.clock())
        self.assertEqual(processed, 1)
        self.assertEqual(len(retry_runtime.message_port.calls), 3)
        remaining = await retry_runtime.repositories.deliveries.current_event(
            events[1].event_key,
            events[1].event_version,
            subscription_id=events[1].subscription_id,
            subscription_revision=events[1].subscription_revision,
        )
        self.assertEqual(remaining.state, DeliveryState.FAILED)
        self.assertEqual(remaining.retry_at, self.clock() + timedelta(minutes=4))

    async def test_i12_optional_and_required_resource_failures_degrade_to_text(self):
        class MissingAssetReader:
            async def read(self, asset_id, *, audience, max_bytes):
                del asset_id, audience, max_bytes
                raise FileNotFoundError("test asset unavailable")

        class UnusedImageBackend:
            async def validate(self, image_bytes, *, max_bytes, max_dimension):
                del image_bytes, max_bytes, max_dimension

        renderer = GenericDisplayRenderer(
            RenderingBounds(2000, 100, 100, 100, 1024 * 1024, 2048, 8, 64, 100),
            asset_reader=MissingAssetReader(),
            image_backend=UnusedImageBackend(),
        )
        owner_view = await self._create("alice", minimum=0)
        owner = await self.runtime.repositories.subscriptions.current(
            owner_view.subscription_id
        )
        failing_runtime = build_runtime(self.root, clock=self.clock, renderer=renderer)
        event_key = "renderer-fallback"
        event = await create_subscription_event_fixture(
            failing_runtime.repositories.deliveries,
            self.bindings,
            DeliveryEvent(
                event_key,
                1,
                owner.subscription_id,
                owner.revision,
                owner.owner_id,
                owner.grant,
                owner.recipient,
                validate_contract(
                    DisplayDocument(
                        "Images",
                        "Fallbacks",
                        (
                            validate_contract(
                                ImageBlock(
                                    "optionalasset", "optional image", required=False
                                )
                            ),
                            validate_contract(
                                ImageBlock(
                                    "requiredasset",
                                    "required image",
                                    fallback_text="required image unavailable",
                                    required=True,
                                )
                            ),
                        ),
                    )
                ),
                delivery_idempotency_key(
                    event_key,
                    1,
                    owner.subscription_id,
                    owner.revision,
                    owner.recipient,
                ),
            ),
        )
        result = await failing_runtime.delivery.dispatch_event(
            event.event_key,
            event.event_version,
            event.subscription_id,
            event.subscription_revision,
        )
        self.assertEqual(result.state, DeliveryState.SENT)
        self.assertEqual(len(failing_runtime.message_port.calls), 1)
        payload = failing_runtime.message_port.calls[0][1]
        self.assertIn("optional image", payload.text)
        self.assertIn("required image unavailable", payload.text)
        self.assertEqual(payload.resource_ids, ())

    async def test_i10_database_reopen_recovers_sending_as_unknown_without_resend(self):
        view = await self._create("alice", minimum=0)
        record = await self.runtime.repositories.subscriptions.current(
            view.subscription_id
        )
        event_key, version = "restart-sending", 1
        event = await create_subscription_event_fixture(
            self.runtime.repositories.deliveries,
            self.bindings,
            DeliveryEvent(
                event_key,
                version,
                record.subscription_id,
                record.revision,
                record.owner_id,
                record.grant,
                record.recipient,
                validate_contract(
                    DisplayDocument(
                        "Test",
                        "Restart",
                        (validate_contract(TextBlock("private payload")),),
                        privacy=Privacy.PRIVATE,
                    )
                ),
                delivery_idempotency_key(
                    event_key,
                    version,
                    record.subscription_id,
                    record.revision,
                    record.recipient,
                ),
            ),
        )
        claimed = await self.runtime.repositories.deliveries.claim_sending(
            event_key,
            version,
            subscription_id=record.subscription_id,
            subscription_revision=record.revision,
            expected_state=DeliveryState.PENDING,
            attempt_number=1,
            started_at=self.clock(),
            now=self.clock(),
        )
        self.assertIsNotNone(claimed)
        recovered_runtime = build_runtime(self.root, clock=self.clock)
        stale, _envelopes = await recovered_runtime.delivery.recover_startup()
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0].event_key, event.event_key)
        self.assertEqual(stale[0].state, DeliveryState.UNKNOWN)
        self.assertEqual(recovered_runtime.message_port.calls, [])

    async def test_i14_event_commit_fault_does_not_leave_partial_cursor_or_event(self):
        view = await self._create("alice", minimum=0)
        record = await self.runtime.repositories.subscriptions.current(
            view.subscription_id
        )
        await self.runtime.scheduler.run_due_page()
        before = await self.runtime.repositories.scheduler.current_evaluation(
            view.subscription_id, record.collection_key
        )
        event_count = self._count("b04_delivery_events")
        self.clock.advance(timedelta(seconds=61))
        connection = self.runtime.database.connect()
        try:
            connection.execute(
                "CREATE TRIGGER b04_test_abort_event BEFORE INSERT ON b04_delivery_events "
                "WHEN NEW.event_key LIKE 'event-observation-2-%' "
                "BEGIN SELECT RAISE(ABORT, 'injected event write failure'); END"
            )
            connection.commit()
        finally:
            connection.close()
        await self.runtime.scheduler.run_due_page()
        connection = self.runtime.database.connect()
        try:
            connection.execute("DROP TRIGGER b04_test_abort_event")
            connection.commit()
        finally:
            connection.close()
        after = await self.runtime.repositories.scheduler.current_evaluation(
            view.subscription_id, record.collection_key
        )
        self.assertEqual(after.state, before.state)
        self.assertEqual(after.cursor, before.cursor)
        self.assertEqual(self._count("b04_delivery_events"), event_count)

    async def test_i18_dst_window_identity_and_bounds_survive_persistence(self):
        await self._create(
            "alice",
            mode="digest",
            digest_schedule=validate_contract(
                DigestScheduleProfile(
                    "America/New_York",
                    "01:30",
                    3600,
                    DstFoldPolicy.FIRST_OCCURRENCE,
                    DstGapPolicy.NEXT_VALID_INSTANT,
                )
            ),
        )
        record = await self.runtime.repositories.subscriptions.list_for_owner(
            "principal-alice", limit=10
        )
        folded = digest_window_for_date(record[0], date(2026, 11, 1))
        self.assertIsNotNone(folded)
        self.assertEqual(folded.utc_end, datetime(2026, 11, 1, 5, 30, tzinfo=UTC))
        self.assertEqual(
            folded.window_id,
            digest_window_for_date(record[0], date(2026, 11, 1)).window_id,
        )
        self.assertEqual(folded.due_at, folded.utc_end)
        self.assertEqual(
            digest_envelope_idempotency_key(folded.window_id, record[0].recipient),
            digest_envelope_idempotency_key(folded.window_id, record[0].recipient),
        )
        persisted = await self.runtime.repositories.windows.create(folded)
        reopened = type(self.runtime.database)(self.root / "runtime.sqlite3")
        from ygl_test_subject.infrastructure.sqlite.repositories_subscriptions import (
            SQLiteDigestWindowRepository,
        )

        recovered = await SQLiteDigestWindowRepository(reopened).get(folded.window_id)
        self.assertEqual(recovered, persisted)

        gap_record = record[0]
        gap_profile = validate_contract(
            DigestScheduleProfile(
                "America/New_York",
                "02:30",
                3600,
                DstFoldPolicy.SECOND_OCCURRENCE,
                DstGapPolicy.NEXT_VALID_INSTANT,
            )
        )
        gap_record = type(gap_record)(
            gap_record.subscription_id,
            gap_record.revision,
            gap_record.module_id,
            gap_record.collection_key,
            gap_record.owner_id,
            gap_record.grant,
            gap_record.recipient,
            "digest",
            gap_record.filters,
            gap_record.status,
            gap_record.type_id,
            gap_profile,
        )
        second_fold_profile = validate_contract(
            DigestScheduleProfile(
                "America/New_York",
                "01:30",
                3600,
                DstFoldPolicy.SECOND_OCCURRENCE,
                DstGapPolicy.NEXT_VALID_INSTANT,
            )
        )
        second_fold_record = type(gap_record)(
            gap_record.subscription_id,
            gap_record.revision,
            gap_record.module_id,
            gap_record.collection_key,
            gap_record.owner_id,
            gap_record.grant,
            gap_record.recipient,
            gap_record.notification_mode,
            gap_record.filters,
            gap_record.status,
            gap_record.type_id,
            second_fold_profile,
        )
        second_fold = digest_window_for_date(second_fold_record, date(2026, 11, 1))
        self.assertEqual(second_fold.utc_end, datetime(2026, 11, 1, 6, 30, tzinfo=UTC))
        self.assertNotEqual(folded.window_id, second_fold.window_id)
        spring = digest_window_for_date(gap_record, date(2026, 3, 8))
        self.assertEqual(spring.utc_end, datetime(2026, 3, 8, 7, 0, tzinfo=UTC))
        first_gap_record = type(gap_record)(
            gap_record.subscription_id,
            gap_record.revision,
            gap_record.module_id,
            gap_record.collection_key,
            gap_record.owner_id,
            gap_record.grant,
            gap_record.recipient,
            gap_record.notification_mode,
            gap_record.filters,
            gap_record.status,
            gap_record.type_id,
            validate_contract(
                DigestScheduleProfile(
                    "America/New_York",
                    "02:30",
                    3600,
                    DstFoldPolicy.FIRST_OCCURRENCE,
                    DstGapPolicy.NEXT_VALID_INSTANT,
                )
            ),
        )
        first_gap = digest_window_for_date(first_gap_record, date(2026, 3, 8))
        self.assertEqual(first_gap.utc_end, spring.utc_end)
        skip_gap_record = type(gap_record)(
            gap_record.subscription_id,
            gap_record.revision,
            gap_record.module_id,
            gap_record.collection_key,
            gap_record.owner_id,
            gap_record.grant,
            gap_record.recipient,
            gap_record.notification_mode,
            gap_record.filters,
            gap_record.status,
            gap_record.type_id,
            validate_contract(
                DigestScheduleProfile(
                    "America/New_York",
                    "02:30",
                    3600,
                    DstFoldPolicy.FIRST_OCCURRENCE,
                    DstGapPolicy.SKIP,
                )
            ),
        )
        self.assertIsNone(digest_window_for_date(skip_gap_record, date(2026, 3, 8)))
        self.assertEqual(
            second_fold.window_id,
            digest_window_for_date(second_fold_record, date(2026, 11, 1)).window_id,
        )
        self.assertEqual(
            spring.window_id,
            digest_window_for_date(gap_record, date(2026, 3, 8)).window_id,
        )

        second_persisted = await self.runtime.repositories.windows.create(second_fold)
        spring_persisted = await self.runtime.repositories.windows.create(spring)
        first_gap_persisted = await self.runtime.repositories.windows.create(first_gap)
        ZoneInfo.clear_cache()
        fresh_clock = DeterministicClock(datetime(2026, 11, 1, 4, 0, tzinfo=UTC))
        fresh_runtime = build_runtime(self.root, clock=fresh_clock)
        recovered_second = await fresh_runtime.repositories.windows.get(
            second_persisted.window_id
        )
        recovered_spring = await fresh_runtime.repositories.windows.get(
            spring_persisted.window_id
        )
        recovered_first_gap = await fresh_runtime.repositories.windows.get(
            first_gap_persisted.window_id
        )
        self.assertEqual(recovered_second, second_persisted)
        self.assertEqual(recovered_spring, spring_persisted)
        self.assertEqual(recovered_first_gap, first_gap_persisted)
        for expected in (second_fold, spring, first_gap):
            actual = await fresh_runtime.repositories.windows.get(expected.window_id)
            self.assertEqual(actual.utc_start, expected.utc_start)
            self.assertEqual(actual.utc_end, expected.utc_end)
            self.assertEqual(actual.due_at, expected.due_at)

    async def test_i16_i17_i20_two_digest_members_one_persisted_window_after_restart(
        self,
    ):
        now = self.clock()
        due_at = now + timedelta(minutes=5)
        profile = validate_contract(
            DigestScheduleProfile(
                "UTC",
                due_at.strftime("%H:%M"),
                3600,
                DstFoldPolicy.FIRST_OCCURRENCE,
                DstGapPolicy.SKIP,
            )
        )
        first = await self._create(
            "alice", minimum=1, mode="digest", digest_schedule=profile
        )
        second = await self._create(
            "alice", minimum=2, mode="digest", digest_schedule=profile
        )
        await self.runtime.scheduler.create_digest_windows_page(
            local_dates=(now.date(),)
        )
        first_record = await self.runtime.repositories.subscriptions.current(
            first.subscription_id
        )
        window = digest_window_for_date(first_record, now.date())
        self.assertIsNotNone(
            await self.runtime.repositories.windows.get(window.window_id)
        )
        await self.runtime.scheduler.run_due_page()
        self.assertEqual(self.runtime.collector.calls, 1)
        self.assertEqual(self._count("b04_delivery_events"), 2)
        self.assertEqual(self._count("b04_digest_members"), 2)
        # No matched member causes a recollection during rendering/restart.
        before = self.runtime.collector.calls
        saved_window = await self.runtime.repositories.windows.get(window.window_id)
        self.assertEqual(saved_window.utc_start, window.utc_start)
        self.assertEqual(saved_window.utc_end, window.utc_end)
        self.assertEqual(saved_window.due_at, window.due_at)
        ZoneInfo.clear_cache()
        reopened_clock = DeterministicClock(datetime(2026, 11, 1, 4, 0, tzinfo=UTC))
        reopened = build_runtime(self.root, clock=reopened_clock)
        recovered_window = await reopened.repositories.windows.get(window.window_id)
        self.assertEqual(recovered_window, saved_window)
        reopened_clock.current = recovered_window.due_at
        processed = await reopened.delivery.dispatch_due_digests(now=reopened_clock())
        self.assertEqual(processed, 1)
        self.assertEqual(len(reopened.message_port.calls), 1)
        digest_text = reopened.message_port.calls[0][1].text
        self.assertIn(first.subscription_id, digest_text)
        self.assertIn(second.subscription_id, digest_text)
        self.assertEqual(before, 1)

    async def test_core_delivery_order_routes_digest_only_when_its_window_is_due(
        self,
    ):
        now = self.clock()
        due_at = now + timedelta(minutes=5)
        window_date = due_at.date()
        profile = validate_contract(
            DigestScheduleProfile(
                "UTC",
                due_at.strftime("%H:%M"),
                3600,
                DstFoldPolicy.FIRST_OCCURRENCE,
                DstGapPolicy.SKIP,
            )
        )
        digest_view = await self._create(
            "bob", minimum=0, mode="digest", digest_schedule=profile
        )
        instant_view = await self._create("bob", minimum=0, mode="instant")
        cancelled_view = await self._create("bob", minimum=0, mode="instant")
        await self.runtime.scheduler.create_digest_windows_page(
            local_dates=(window_date,)
        )
        digest_record = await self.runtime.repositories.subscriptions.current(
            digest_view.subscription_id
        )
        window = digest_window_for_date(digest_record, window_date)
        self.assertGreater(window.due_at, now)

        # Match the Core pump order: collection, immediate events, then digests.
        await self.runtime.scheduler.run_due_page()
        await self.runtime.repositories.subscriptions.cancel(
            cancelled_view.subscription_id,
            expected_revision=cancelled_view.revision,
        )
        digest_event_key = f"event-observation-1-{digest_view.subscription_id}"
        cancelled_event_key = f"event-observation-1-{cancelled_view.subscription_id}"
        digest_event = await self.runtime.repositories.deliveries.current_event(
            digest_event_key,
            1,
            subscription_id=digest_view.subscription_id,
            subscription_revision=digest_view.revision,
        )
        self.assertEqual(digest_event.state, DeliveryState.PENDING)
        saved_window = await self.runtime.repositories.windows.get(window.window_id)
        self.assertEqual(
            tuple(member.event_key for member in saved_window.members),
            (digest_event_key,),
        )
        cancelled_event = await self.runtime.repositories.deliveries.current_event(
            cancelled_event_key,
            1,
            subscription_id=cancelled_view.subscription_id,
            subscription_revision=cancelled_view.revision,
        )
        self.assertEqual(cancelled_event.state, DeliveryState.PENDING)

        class _CountingRenderer:
            def __init__(self, delegate) -> None:
                self.delegate = delegate
                self.single_calls = 0
                self.batch_calls = 0

            async def render(self, document, *, limits, audience):
                self.single_calls += 1
                return await self.delegate.render(
                    document, limits=limits, audience=audience
                )

            async def render_batch(self, batch, limits):
                self.batch_calls += 1
                return await self.delegate.render_batch(batch, limits)

        renderer = _CountingRenderer(self.runtime.delivery._renderer)
        self.runtime.delivery._renderer = renderer
        self.runtime.delivery._page_size = 1
        future_page = []
        cursor = None
        while True:
            page = await self.runtime.repositories.deliveries.list_due_events(
                now=now, limit=1, after_cursor=cursor
            )
            if not page:
                break
            future_page.extend(page)
            cursor = page[-1].cursor
        self.assertEqual(
            {event.subscription_id for event in future_page},
            {instant_view.subscription_id, cancelled_view.subscription_id},
        )
        self.assertNotIn(
            digest_view.subscription_id,
            {event.subscription_id for event in future_page},
        )
        self.assertEqual(await self.runtime.delivery.dispatch_due_events(now=now), 2)
        self.assertEqual(len(self.runtime.message_port.calls), 1)
        immediate_text = self.runtime.message_port.calls[0][1].text
        self.assertIn(instant_view.subscription_id, immediate_text)
        self.assertNotIn(digest_view.subscription_id, immediate_text)
        self.assertNotIn(cancelled_view.subscription_id, immediate_text)
        self.assertEqual(
            (
                await self.runtime.repositories.deliveries.current_event(
                    cancelled_event.event_key,
                    cancelled_event.event_version,
                    subscription_id=cancelled_event.subscription_id,
                    subscription_revision=cancelled_event.subscription_revision,
                )
            ).state,
            DeliveryState.CANCELLED,
        )

        # A direct event route must leave a legal digest member untouched.
        render_calls = (renderer.single_calls, renderer.batch_calls)
        deferred = await self.runtime.delivery.dispatch_event(
            digest_event.event_key,
            digest_event.event_version,
            digest_event.subscription_id,
            digest_event.subscription_revision,
        )
        self.assertEqual(deferred.state, DeliveryState.PENDING)
        self.assertEqual((renderer.single_calls, renderer.batch_calls), render_calls)
        direct_claim = await self.runtime.repositories.deliveries.claim_sending(
            digest_event.event_key,
            digest_event.event_version,
            subscription_id=digest_event.subscription_id,
            subscription_revision=digest_event.subscription_revision,
            expected_state=DeliveryState.PENDING,
            attempt_number=1,
            started_at=now,
            now=now,
        )
        self.assertIsNone(direct_claim)
        self.assertEqual(
            (
                await self.runtime.repositories.deliveries.current_event(
                    digest_event.event_key,
                    digest_event.event_version,
                    subscription_id=digest_event.subscription_id,
                    subscription_revision=digest_event.subscription_revision,
                )
            ).state,
            DeliveryState.PENDING,
        )
        self.assertEqual(
            await self.runtime.repositories.deliveries.list_due_events(
                now=now, limit=10
            ),
            (),
        )

        self.clock.current = window.due_at
        self.assertEqual(
            await self.runtime.delivery.dispatch_due_events(now=self.clock()), 0
        )
        self.assertEqual(
            await self.runtime.delivery.dispatch_due_digests(now=self.clock()), 1
        )
        self.assertEqual(len(self.runtime.message_port.calls), 2)
        self.assertEqual(renderer.single_calls, 1)
        self.assertEqual(renderer.batch_calls, 1)

        sent_envelope = await self.runtime.repositories.windows.current_envelope(
            window.window_id, digest_record.recipient
        )
        self.assertEqual(sent_envelope.state.value, "sent")
        await self.runtime.repositories.subscriptions.cancel(
            digest_record.subscription_id,
            expected_revision=digest_record.revision,
        )

        # Immediate cleanup and a direct caller must preserve the historical
        # PENDING event associated with the immutable SENT envelope.
        direct_after_send = await self.runtime.delivery.dispatch_event(
            digest_event.event_key,
            digest_event.event_version,
            digest_event.subscription_id,
            digest_event.subscription_revision,
        )
        self.assertEqual(direct_after_send.state, DeliveryState.PENDING)
        self.assertEqual(
            await self.runtime.delivery.dispatch_due_events(now=self.clock()), 0
        )
        self.assertIsNone(
            await self.runtime.repositories.deliveries.claim_sending(
                digest_event.event_key,
                digest_event.event_version,
                subscription_id=digest_event.subscription_id,
                subscription_revision=digest_event.subscription_revision,
                expected_state=DeliveryState.PENDING,
                attempt_number=1,
                started_at=self.clock(),
                now=self.clock(),
            )
        )
        with self.assertRaises(RevisionConflict):
            await self.runtime.repositories.deliveries.record_attempt(
                digest_event.event_key,
                digest_event.event_version,
                DeliveryAttempt(
                    1,
                    DeliveryState.CANCELLED,
                    digest_event.idempotency_key,
                    self.clock(),
                    self.clock(),
                    error_code="subscription_cancelled",
                ),
                subscription_id=digest_event.subscription_id,
                subscription_revision=digest_event.subscription_revision,
                expected_state=DeliveryState.PENDING,
            )
        preserved_event = await self.runtime.repositories.deliveries.current_event(
            digest_event.event_key,
            digest_event.event_version,
            subscription_id=digest_event.subscription_id,
            subscription_revision=digest_event.subscription_revision,
        )
        preserved_envelope = await self.runtime.repositories.windows.current_envelope(
            window.window_id, digest_record.recipient
        )
        self.assertEqual(preserved_event, digest_event)
        self.assertEqual(preserved_envelope, sent_envelope)
        self.assertEqual(len(self.runtime.message_port.calls), 2)

    async def test_i20_digest_excludes_other_timezone_window_and_recipient(self):
        now = self.clock().replace(second=0, microsecond=0) + timedelta(minutes=1)
        self.clock.current = now
        due_at = now + timedelta(minutes=5)
        tokyo_due_at = due_at.astimezone(ZoneInfo("Asia/Tokyo"))
        utc_profile = validate_contract(
            DigestScheduleProfile(
                "UTC",
                due_at.strftime("%H:%M"),
                3600,
                DstFoldPolicy.FIRST_OCCURRENCE,
                DstGapPolicy.SKIP,
            )
        )
        tokyo_profile = validate_contract(
            DigestScheduleProfile(
                "Asia/Tokyo",
                tokyo_due_at.strftime("%H:%M"),
                3600,
                DstFoldPolicy.FIRST_OCCURRENCE,
                DstGapPolicy.SKIP,
            )
        )
        first = await self._create(
            "alice", minimum=0, mode="digest", digest_schedule=utc_profile
        )
        second = await self._create(
            "alice", minimum=1, mode="digest", digest_schedule=utc_profile
        )
        other_recipient = await self._create(
            "bob", minimum=0, mode="digest", digest_schedule=utc_profile
        )
        other_timezone = await self._create(
            "alice", minimum=0, mode="digest", digest_schedule=tokyo_profile
        )
        await self.runtime.scheduler.create_digest_windows_page(
            local_dates=tuple({due_at.date(), tokyo_due_at.date()})
        )
        records = {
            view.subscription_id: await self.runtime.repositories.subscriptions.current(
                view.subscription_id
            )
            for view in (first, second, other_recipient, other_timezone)
        }
        window = digest_window_for_date(records[first.subscription_id], due_at.date())
        recipient_window = digest_window_for_date(
            records[other_recipient.subscription_id], due_at.date()
        )
        timezone_window = digest_window_for_date(
            records[other_timezone.subscription_id], tokyo_due_at.date()
        )
        self.assertEqual(window.due_at, timezone_window.due_at)
        self.assertNotEqual(window.window_id, timezone_window.window_id)
        self.assertNotEqual(window.window_id, recipient_window.window_id)
        await self.runtime.scheduler.run_due_page()
        self.assertEqual(self.runtime.collector.calls, 1)
        self.assertEqual(self._count("b04_digest_members"), 4)
        self.assertIsNotNone(
            await self.runtime.repositories.windows.get(window.window_id)
        )
        result = await self.runtime.delivery.dispatch_digest(
            window.window_id,
            records[first.subscription_id].recipient,
            now=window.due_at,
        )
        self.assertEqual(result.state, DeliveryState.SENT)
        self.assertEqual(len(self.runtime.message_port.calls), 1)
        text = self.runtime.message_port.calls[0][1].text
        self.assertIn(first.subscription_id, text)
        self.assertIn(second.subscription_id, text)
        self.assertNotIn(other_recipient.subscription_id, text)
        self.assertNotIn(other_timezone.subscription_id, text)

    async def test_i19_digest_rechecks_only_cancelled_member_after_render_barrier(self):
        now = self.clock()
        due_at = now + timedelta(minutes=5)
        profile = validate_contract(
            DigestScheduleProfile(
                "UTC",
                due_at.strftime("%H:%M"),
                3600,
                DstFoldPolicy.FIRST_OCCURRENCE,
                DstGapPolicy.SKIP,
            )
        )
        first = await self._create(
            "alice", minimum=1, mode="digest", digest_schedule=profile
        )
        second = await self._create(
            "alice", minimum=2, mode="digest", digest_schedule=profile
        )
        await self.runtime.scheduler.create_digest_windows_page(
            local_dates=(now.date(),)
        )
        first_record = await self.runtime.repositories.subscriptions.current(
            first.subscription_id
        )
        window = digest_window_for_date(first_record, now.date())
        await self.runtime.scheduler.run_due_page()

        barrier = RenderBatchBarrier()
        reopened = build_runtime(self.root, clock=self.clock, renderer=barrier)
        self.clock.current = window.due_at
        dispatch = asyncio.create_task(
            reopened.delivery.dispatch_digest(
                window.window_id, first_record.recipient, now=self.clock()
            )
        )
        await barrier.started.wait()
        await self.subscriptions.cancel(
            self.runtime.invocation("alice", "alice"),
            first.subscription_id,
            expected_revision=1,
        )
        barrier.release.set()
        result = await dispatch
        self.assertEqual(result.state, DeliveryState.SENT)
        self.assertEqual(len(reopened.message_port.calls), 1)
        delivered_text = reopened.message_port.calls[0][1].text
        self.assertNotIn(first.subscription_id, delivered_text)
        self.assertIn(second.subscription_id, delivered_text)

    async def test_i19_digest_with_all_members_cancelled_never_calls_message_port(self):
        now = self.clock()
        due_at = now + timedelta(minutes=5)
        profile = validate_contract(
            DigestScheduleProfile(
                "UTC",
                due_at.strftime("%H:%M"),
                3600,
                DstFoldPolicy.FIRST_OCCURRENCE,
                DstGapPolicy.SKIP,
            )
        )
        first = await self._create(
            "alice", minimum=1, mode="digest", digest_schedule=profile
        )
        second = await self._create(
            "alice", minimum=2, mode="digest", digest_schedule=profile
        )
        await self.runtime.scheduler.create_digest_windows_page(
            local_dates=(now.date(),)
        )
        record = await self.runtime.repositories.subscriptions.current(
            first.subscription_id
        )
        window = digest_window_for_date(record, now.date())
        await self.runtime.scheduler.run_due_page()
        await self.subscriptions.cancel(
            self.runtime.invocation("alice", "alice"),
            first.subscription_id,
            expected_revision=1,
        )
        await self.subscriptions.cancel(
            self.runtime.invocation("alice", "alice"),
            second.subscription_id,
            expected_revision=1,
        )
        self.clock.current = window.due_at
        await self.runtime.delivery.dispatch_due_digests(now=self.clock())
        self.assertEqual(self.runtime.message_port.calls, [])

    async def test_i13_command_tool_nested_routes_and_four_way_output(self):
        # The production OutputService routes through a recording MessagePort;
        # this fixture has no live host and no send authority outside that port.
        invocation = self.runtime.invocation("alice", "alice")
        await self._conversation("alice")
        result = validate_contract(
            CapabilityResult(
                "result",
                ResultStatus.SUCCESS,
                document=validate_contract(
                    DisplayDocument(
                        "title", "summary", (validate_contract(TextBlock("body")),)
                    )
                ),
            )
        )
        output = await self.runtime.output.route(invocation, result)
        self.assertEqual(output.status.value, "sent")
        self.assertEqual(len(self.runtime.message_port.calls), 1)

        tool = self.runtime.invocation(
            "alice", "alice", origin=InvocationOrigin.LLM_TOOL
        )
        tool_result = await self.runtime.output.route(
            tool,
            validate_contract(
                CapabilityResult(
                    "tool",
                    ResultStatus.SUCCESS,
                    document=validate_contract(
                        DisplayDocument(
                            "Tool", "Facts", (validate_contract(TextBlock("public")),)
                        )
                    ),
                    model_facts=validate_contract(FactDocument({"answer": "public"})),
                )
            ),
        )
        self.assertEqual(tool_result.status.value, "tool_result")
        self.assertEqual(len(self.runtime.message_port.calls), 1)

        nested = self.runtime.issuer.derive(
            invocation,
            module_id="sample/feed",
            module_epoch=invocation.module_epoch,
            capability_id="read",
        )
        nested_result = await self.runtime.output.route(nested, result)
        self.assertEqual(nested_result.status.value, "nested_result")
        self.assertEqual(len(self.runtime.message_port.calls), 1)

        await self._create("bob", minimum=0)
        await self.runtime.scheduler.run_due_page()
        due = await self.runtime.repositories.deliveries.list_due_events(
            now=self.clock(), limit=10, after_cursor=None
        )
        subscription_event = next(
            item for item in due if item.owner_id == "principal-bob"
        )
        subscription_record = await self.runtime.repositories.subscriptions.current(
            subscription_event.subscription_id
        )
        self.assertIsNotNone(subscription_record)
        self.assertEqual(
            subscription_record.revision, subscription_event.subscription_revision
        )
        module = self.runtime.registry.snapshot().module("sample/feed")
        subscription_invocation = self.runtime.issuer.issue(
            origin=InvocationOrigin.SUBSCRIPTION,
            module_id="sample/feed",
            module_epoch=module.epoch,
            registry_revision=self.runtime.registry.snapshot().revision,
            actor_id=subscription_record.owner_id,
            adapter_id=subscription_record.recipient.adapter_id,
            conversation_id=subscription_record.recipient.conversation_id,
            delivery_route=subscription_record.recipient.delivery_route,
            capability_id="read",
            grant_id=(
                None
                if subscription_record.grant is None
                else subscription_record.grant.grant_id
            ),
            grant_revision=(
                None
                if subscription_record.grant is None
                else subscription_record.grant.revision
            ),
            subscription_id=subscription_record.subscription_id,
            subscription_revision=subscription_record.revision,
            conversation_kind=validate_contract(
                InvocationConversationKind(subscription_record.recipient.kind.value)
            ),
            subscription_scope=validate_contract(
                InvocationSubscriptionScope(
                    subscription_record.collection_key.scope.kind.value
                )
            ),
        )
        self.runtime.lifecycle.admission.admit(subscription_invocation, "read")
        queued = await self.runtime.output.route(
            subscription_invocation, SubscriptionOutput(subscription_event)
        )
        self.assertEqual(queued.status.value, "subscription_enqueued")
        self.assertEqual(len(self.runtime.message_port.calls), 1)


__all__ = ["B04RuntimeIntegrationTests"]
