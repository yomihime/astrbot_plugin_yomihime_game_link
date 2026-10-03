"""Behavioral probes for the additive B04-C contract revision."""

import asyncio
import inspect
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import get_args, get_type_hints

from ygl_test_subject.api.contexts import (
    InvocationConversationKind,
    InvocationOrigin,
    InvocationSubscriptionScope,
    InvocationView,
)
from ygl_test_subject.api.display import DisplayDocument, Privacy, TextBlock
from ygl_test_subject.api.results import CapabilityResult, FactDocument
from ygl_test_subject.api.services import (
    AuthorizedRecipient,
    CommandOutput,
    ConversationKind,
    ConversationRef,
    SubscriptionOperations,
    SubscriptionOutput,
    ToolOutput,
    TrustedConversationResolver,
    TrustedPersistedRouteResolver,
    resolve_authorized_recipient,
)
from ygl_test_subject.api.storage import GrantReference, OwnerScope, OwnershipKind
from ygl_test_subject.api.subscriptions import (
    ActiveDigestSchedule,
    CadenceConfiguration,
    CollectionKey,
    DeliveryAttempt,
    DeliveryEvent,
    DeliveryEventCursor,
    DeliveryState,
    DigestEnvelope,
    DigestEnvelopeClaim,
    DigestEnvelopeState,
    DigestMember,
    DigestMemberAssociation,
    DigestRouteCandidate,
    DigestRouteCursor,
    DigestScheduleProfile,
    DigestWindow,
    DigestWindowSelector,
    DstFoldPolicy,
    DstGapPolicy,
    DueCollectionJob,
    DueJobCursor,
    EvaluationDecision,
    EvaluationState,
    NormalizedInput,
    Observation,
    ObservationCompleteness,
    ObservationCursor,
    ObservationEvaluationCommit,
    ScheduleDescriptor,
    ScheduleTrigger,
    SubscriptionEvaluationCommit,
    SubscriptionEvaluationSnapshot,
    SubscriptionJobAssociation,
    SubscriptionJobChange,
    SubscriptionJobChangeKind,
    SubscriptionRecord,
    SubscriptionRequest,
    SubscriptionStatus,
    SubscriptionView,
    delivery_idempotency_key,
    digest_envelope_idempotency_key,
    validate_evaluation_decision,
)
from ygl_test_subject.api.version import (
    B02_CONTRACT_VERSION,
    COMPATIBLE_CONTRACT_VERSIONS,
    CONTRACT_REVISION,
    CONTRACT_VERSION,
)
from ygl_test_subject.core.context_issuer import ContextIssuer, InvalidInvocation
from ygl_test_subject.core.ports import (
    CollectionRunRequest,
    DeliveryRepository,
    DigestWindowRepository,
    ExecutionLease,
    MessageReceipt,
    MessageStatus,
    MessageTarget,
    RootOutputClaim,
    RootOutputOutcome,
    RootOutputRepository,
    RootOutputState,
    SchedulerRepository,
    SubscriptionJobRepository,
    SubscriptionLifecycleRepository,
    SubscriptionRepository,
    SubscriptionStore,
)

NOW = datetime(2026, 9, 25, 0, 0, tzinfo=UTC)


def sample_key(scope: OwnerScope | None = None) -> CollectionKey:
    return CollectionKey(
        "sample/alpha",
        "prices",
        1,
        "public-api",
        NormalizedInput({"item": "x"}),
        scope or OwnerScope.public(),
    )


def sample_invocation(
    origin: InvocationOrigin = InvocationOrigin.COMMAND,
    *,
    adapter_id: str | None = "adapter",
    conversation_id: str | None = "direct-1",
) -> InvocationView:
    return InvocationView(
        "invocation-1",
        origin,
        "user-1",
        conversation_id,
        "sample/alpha",
        1,
        1,
        adapter_id=adapter_id,
    )


class B04ContractTests(unittest.TestCase):
    def test_c01_revision_imports_and_b03_operation_signatures_remain_compatible(self):
        self.assertEqual(CONTRACT_VERSION, "1.4.0")
        self.assertEqual(CONTRACT_REVISION, "UI-B0-PUBLIC-WEB")
        self.assertEqual(
            COMPATIBLE_CONTRACT_VERSIONS,
            (B02_CONTRACT_VERSION, "1.1.0", "1.2.0", "1.3.0", "1.4.0"),
        )
        self.assertIn("1.1.0", COMPATIBLE_CONTRACT_VERSIONS)
        expected = {
            "create": ("self", "invocation", "subscription"),
            "revise": ("self", "invocation", "subscription"),
            "list_current": ("self", "invocation"),
            "cancel": ("self", "invocation", "subscription_id", "expected_revision"),
        }
        for name, parameters in expected.items():
            self.assertEqual(
                tuple(
                    inspect.signature(getattr(SubscriptionOperations, name)).parameters
                ),
                parameters,
            )
        self.assertTrue(get_type_hints(SubscriptionRecord))
        self.assertTrue(get_type_hints(CollectionRunRequest))

    def test_c12_root_output_repository_has_durable_cas_surface(self):
        expected = {
            "claim": (
                "self",
                "root_invocation_id",
                "output_identity",
                "payload_fingerprint",
                "owner_token",
                "now",
                "lease_expires_at",
            ),
            "begin_sending": ("self", "claim", "now", "lease_expires_at"),
            "complete": (
                "self",
                "claim",
                "receipt",
                "outcome",
                "completed_at",
                "error_code",
            ),
            "recover_expired": ("self", "before", "recovered_at"),
        }
        for name, parameters in expected.items():
            self.assertEqual(
                tuple(
                    inspect.signature(getattr(RootOutputRepository, name)).parameters
                ),
                parameters,
            )
        self.assertTrue(get_type_hints(RootOutputClaim))
        self.assertIs(get_type_hints(RootOutputClaim)["claim_generation"], int)
        self.assertEqual(
            {state.value for state in RootOutputState},
            {"claimed", "sending", "completed", "unknown"},
        )
        self.assertEqual(
            {outcome.value for outcome in RootOutputOutcome},
            {"message", "tool_returned", "subscription_enqueued", "controlled_result"},
        )

    def test_c13_root_output_generation_is_required_and_positive(self):
        values = (
            "root-id",
            "result-id",
            "a" * 64,
            RootOutputState.CLAIMED,
            "owner-token",
            1,
            NOW,
            NOW + timedelta(minutes=1),
        )
        valid = RootOutputClaim(*values)
        self.assertEqual(valid.claim_generation, 1)
        for generation in (0, -1, True):
            with self.subTest(generation=generation), self.assertRaises(ValueError):
                RootOutputClaim(*values[:5], generation, *values[6:])

    def test_c12_subscription_context_carries_full_issuer_owned_route(self):
        self.assertEqual(InvocationOrigin.SUBSCRIPTION.value, "subscription")
        self.assertEqual(
            {kind.value for kind in InvocationConversationKind}, {"direct", "group"}
        )
        self.assertEqual(
            {scope.value for scope in InvocationSubscriptionScope},
            {"public", "user", "authorized"},
        )
        self.assertIn("delivery_route", InvocationView.__dataclass_fields__)
        self.assertIn("conversation_kind", InvocationView.__dataclass_fields__)

    def test_c11_scheduler_commit_accepts_explicit_utc_reschedule(self):
        methods = (
            SchedulerRepository.commit_observation,
            SchedulerRepository.commit_observation_with_evaluations,
        )
        for method in methods:
            parameters = inspect.signature(method).parameters
            self.assertEqual(parameters["next_due_at"].default, None)
            self.assertIn("datetime | None", str(get_type_hints(method)["next_due_at"]))
        self.assertEqual(
            tuple(inspect.signature(methods[0]).parameters),
            ("self", "lease", "observation", "next_due_at"),
        )
        self.assertEqual(
            tuple(inspect.signature(methods[1]).parameters),
            ("self", "lease", "commit", "next_due_at"),
        )

    def test_c02_subscription_scope_and_revision_cas_values(self):
        public = SubscriptionRecord(
            "sub-1",
            1,
            "sample/alpha",
            sample_key(),
            "user-1",
            None,
            ConversationRef("adapter", ConversationKind.DIRECT, "direct-1", "route-1"),
            "instant",
            {"threshold": 3},
        )
        self.assertEqual(public.status, SubscriptionStatus.ACTIVE)
        private_key = sample_key(OwnerScope.user("user-1"))
        SubscriptionRecord(
            "sub-2",
            2,
            "sample/alpha",
            private_key,
            "user-1",
            None,
            public.recipient,
            "digest",
            {},
        )
        grant = GrantReference("grant-1", 4)
        authorized_key = sample_key(OwnerScope.authorized("user-1", grant))
        SubscriptionRecord(
            "sub-3",
            1,
            "sample/alpha",
            authorized_key,
            "user-1",
            grant,
            public.recipient,
            "instant",
            {},
        )
        with self.assertRaises(ValueError):
            SubscriptionRecord(
                "sub-4",
                1,
                "sample/alpha",
                private_key,
                "someone-else",
                None,
                public.recipient,
                "instant",
                {},
            )
        with self.assertRaises(ValueError):
            SubscriptionRecord(
                "sub-5",
                1,
                "sample/alpha",
                authorized_key,
                "user-1",
                GrantReference("grant-1", 3),
                public.recipient,
                "instant",
                {},
            )
        self.assertIn(
            "expected_revision", inspect.signature(SubscriptionStore.revise).parameters
        )
        self.assertIn(
            "expected_revision", inspect.signature(SubscriptionStore.cancel).parameters
        )

    def test_c02_persisted_integer_identity_fields_reject_int_subclasses(self):
        class EvilInt(int):
            def __lt__(self, other):
                return False

            def __gt__(self, other):
                return True

        evil = EvilInt(-4)
        recipient = ConversationRef(
            "adapter", ConversationKind.DIRECT, "direct-1", "route-1"
        )
        valid_key = sample_key()
        event_key = delivery_idempotency_key("event", 1, "sub", 1, recipient)
        checks = (
            lambda: CollectionKey(
                "sample/alpha",
                "prices",
                evil,
                "public-api",
                NormalizedInput({}),
                OwnerScope.public(),
            ),
            lambda: SubscriptionRecord(
                "sub",
                evil,
                "sample/alpha",
                valid_key,
                "user-1",
                None,
                recipient,
                "instant",
                {},
            ),
            lambda: DigestMember("sub", evil, "event", 1),
            lambda: DigestMember("sub", 1, "event", evil),
            lambda: DeliveryAttempt(evil, DeliveryState.SENDING, "key", NOW),
            lambda: DeliveryEvent(
                "event",
                evil,
                "sub",
                1,
                "user-1",
                None,
                recipient,
                DisplayDocument("Title", "Subject", (TextBlock("x"),)),
                event_key,
            ),
            lambda: DeliveryEvent(
                "event",
                1,
                "sub",
                evil,
                "user-1",
                None,
                recipient,
                DisplayDocument("Title", "Subject", (TextBlock("x"),)),
                event_key,
            ),
            lambda: Observation(
                "obs",
                valid_key,
                evil,
                NOW,
                NOW,
                ObservationCompleteness.COMPLETE,
                (),
                {},
            ),
            lambda: SubscriptionView("sub", evil, "user-1", None, "direct-1", {}),
            lambda: ScheduleDescriptor(
                "prices",
                evil,
                "source",
                1,
                {"type": "object", "properties": {}, "required": []},
                OwnershipKind.PUBLIC,
                ScheduleTrigger.ON_DEMAND,
                1.0,
            ),
            lambda: ScheduleDescriptor(
                "prices",
                1,
                "source",
                evil,
                {"type": "object", "properties": {}, "required": []},
                OwnershipKind.PUBLIC,
                ScheduleTrigger.ON_DEMAND,
                1.0,
            ),
            lambda: CollectionRunRequest(valid_key, NOW, 10, evil, 1, 1),
            lambda: CollectionRunRequest(valid_key, NOW, 10, 1, evil, 1),
            lambda: CollectionRunRequest(valid_key, NOW, 10, 1, 1, evil),
            lambda: ExecutionLease(valid_key, "lease", evil, 1, 1, NOW),
            lambda: ExecutionLease(valid_key, "lease", 1, evil, 1, NOW),
            lambda: ExecutionLease(valid_key, "lease", 1, 1, evil, NOW),
        )
        for construct in checks:
            with self.subTest(construct=construct):
                with self.assertRaises(ValueError):
                    construct()
        from ygl_test_subject.api.subscriptions import EvaluationState

        with self.assertRaises(ValueError):
            EvaluationState(evil, {})
        with self.assertRaises(ValueError):
            EvaluationDecision(
                {},
                True,
                "event",
                evil,
                DisplayDocument("Title", "Subject", (TextBlock("x"),)),
            )

    def test_c03_resolver_contract_cannot_elevate_tool_origin(self):
        self.assertEqual(
            tuple(inspect.signature(TrustedConversationResolver.resolve).parameters),
            ("self", "invocation"),
        )
        self.assertTrue(get_type_hints(TrustedConversationResolver.resolve))

        class Resolver:
            calls = 0

            async def resolve(self, invocation):
                self.calls += 1
                return ConversationRef(
                    "adapter", ConversationKind.DIRECT, "direct-1", "route-1"
                )

        resolver = Resolver()
        denied = asyncio.run(
            resolve_authorized_recipient(
                sample_invocation(InvocationOrigin.LLM_TOOL), resolver
            )
        )
        self.assertEqual(denied, AuthorizedRecipient.refused())
        self.assertEqual(resolver.calls, 0)

    def test_c04_observation_completeness_and_coverage_are_explicit(self):
        for state in ObservationCompleteness:
            observation = Observation(
                f"obs-{state.value}",
                sample_key(),
                1,
                NOW,
                NOW,
                state,
                ("item-1",) if state is ObservationCompleteness.PARTIAL else (),
                {"items": []},
            )
            self.assertEqual(observation.completeness, state)
        with self.assertRaises(ValueError):
            Observation(
                "dup",
                sample_key(),
                1,
                NOW,
                NOW,
                ObservationCompleteness.PARTIAL,
                ("x", "x"),
                {},
            )
        with self.assertRaises(ValueError):
            Observation(
                "failed-coverage",
                sample_key(),
                1,
                NOW,
                NOW,
                ObservationCompleteness.FAILED,
                ("item-1",),
                {},
            )

    def test_c05_matcher_remains_sync_pure_and_event_versioned(self):
        from ygl_test_subject.api.subscriptions import SubscriptionEvaluator

        self.assertFalse(inspect.iscoroutinefunction(SubscriptionEvaluator.evaluate))
        self.assertEqual(
            tuple(inspect.signature(SubscriptionEvaluator.evaluate).parameters),
            ("self", "subscription", "observation", "previous_state"),
        )
        view = SubscriptionView("sub", 1, "user-1", None, "direct-1", {})
        observation = Observation(
            "obs", sample_key(), 1, NOW, NOW, ObservationCompleteness.COMPLETE, (), {}
        )
        decision = EvaluationDecision(
            {},
            True,
            "event-1",
            1,
            DisplayDocument("Title", "Subject", (TextBlock("x"),)),
        )
        self.assertEqual(
            validate_evaluation_decision(view, observation, decision), decision
        )
        private_observation = Observation(
            "private",
            sample_key(OwnerScope.user("user-1")),
            1,
            NOW,
            NOW,
            ObservationCompleteness.COMPLETE,
            (),
            {},
        )
        with self.assertRaises(ValueError):
            validate_evaluation_decision(view, private_observation, decision)

    def test_c06_c07_message_outcomes_and_unknown_recovery_state(self):
        for status in MessageStatus:
            receipt = MessageReceipt(status)
            self.assertEqual(receipt.status, status)
        recipient = ConversationRef(
            "adapter", ConversationKind.DIRECT, "direct-1", "route-1"
        )
        key = delivery_idempotency_key("event-1", 1, "sub-1", 1, recipient)
        attempt = DeliveryAttempt(1, DeliveryState.UNKNOWN, key, NOW, completed_at=NOW)
        with self.assertRaises(ValueError):
            DeliveryAttempt(1, DeliveryState.SENT, key, NOW)
        sent = DeliveryAttempt(1, DeliveryState.SENT, key, NOW, completed_at=NOW)
        self.assertEqual(sent.state, DeliveryState.SENT)
        event = DeliveryEvent(
            "event-1",
            1,
            "sub-1",
            1,
            "user-1",
            None,
            recipient,
            DisplayDocument(
                "Title", "Subject", (TextBlock("private"),), privacy=Privacy.PRIVATE
            ),
            key,
            DeliveryState.UNKNOWN,
            attempt,
        )
        self.assertEqual(event.state, DeliveryState.UNKNOWN)
        with self.assertRaises(ValueError):
            DeliveryEvent(
                "event-2",
                1,
                "sub-1",
                1,
                "user-1",
                None,
                event.recipient,
                event.display_data,
                "key-2",
                DeliveryState.UNKNOWN,
            )
        self.assertEqual(
            MessageTarget(
                "direct-1", conversation=event.recipient, authorized=True
            ).conversation,
            event.recipient,
        )
        with self.assertRaises(ValueError):
            MessageTarget(
                "group-1",
                conversation=ConversationRef(
                    "adapter", ConversationKind.GROUP, "group-1", "route-g"
                ),
                authorized=True,
            )
        self.assertNotEqual(
            key,
            delivery_idempotency_key(
                "event-1",
                1,
                "sub-1",
                2,
                ConversationRef(
                    "adapter", ConversationKind.DIRECT, "direct-1", "route-1"
                ),
            ),
        )

    def test_c06_shared_event_has_independent_subscription_delivery_identity(self):
        recipient_a = ConversationRef(
            "adapter", ConversationKind.DIRECT, "direct-a", "route-a"
        )
        recipient_b = recipient_a
        document = DisplayDocument("Title", "Subject", (TextBlock("shared"),))
        event_a = DeliveryEvent(
            "shared-event",
            1,
            "sub-a",
            3,
            "user-a",
            None,
            recipient_a,
            document,
            delivery_idempotency_key("shared-event", 1, "sub-a", 3, recipient_a),
        )
        event_b = DeliveryEvent(
            "shared-event",
            1,
            "sub-b",
            7,
            "user-a",
            None,
            recipient_b,
            document,
            delivery_idempotency_key("shared-event", 1, "sub-b", 7, recipient_b),
        )
        self.assertEqual(
            (event_a.event_key, event_a.event_version),
            (event_b.event_key, event_b.event_version),
        )
        self.assertNotEqual(event_a.idempotency_key, event_b.idempotency_key)

        selectors = (
            "subscription_id",
            "subscription_revision",
        )
        for method in (
            DeliveryRepository.current_event,
            DeliveryRepository.record_attempt,
            DeliveryRepository.claim_sending,
            DeliveryRepository.mark_sending_unknown,
        ):
            parameters = inspect.signature(method).parameters
            for name in selectors:
                self.assertIn(name, parameters)
                self.assertIs(parameters[name].kind, inspect.Parameter.KEYWORD_ONLY)
                self.assertIs(parameters[name].default, inspect.Parameter.empty)
            self.assertTrue(get_type_hints(method), method.__qualname__)

    def test_c08_private_documents_do_not_enter_tool_output(self):
        private_doc = DisplayDocument(
            "Private", "Subject", (TextBlock("secret"),), privacy=Privacy.PRIVATE
        )
        with self.assertRaises(ValueError):
            CapabilityResult(
                "result",
                "success",
                private_doc,
                FactDocument({}),
                privacy=Privacy.PRIVATE,
            )
        public_facts = CapabilityResult(
            "tool-result",
            "success",
            DisplayDocument("Public", "Subject", (TextBlock("summary"),)),
            FactDocument({}),
        )
        self.assertEqual(
            ToolOutput(public_facts.model_facts).facts, public_facts.model_facts
        )
        self.assertTrue(get_type_hints(ToolOutput))

    def test_c09_cadence_is_configured_separately_from_notification_mode(self):
        config = CadenceConfiguration((10.0, 30.0, 90.0))
        self.assertEqual(config.validate_target(30), 30.0)
        with self.assertRaises(ValueError):
            config.validate_target(20)
        with self.assertRaises(ValueError):
            config.validate_target(True)
        with self.assertRaises(ValueError):
            CadenceConfiguration((10.0, float("inf")))
        record = SubscriptionRecord(
            "sub",
            1,
            "sample/alpha",
            sample_key(),
            "user-1",
            None,
            ConversationRef("adapter", ConversationKind.DIRECT, "direct-1", "route-1"),
            "digest",
            {},
        )
        self.assertEqual(record.notification_mode, "digest")
        self.assertEqual(config.allowed_seconds, (10.0, 30.0, 90.0))

    def test_c10_c11_digest_window_persists_utc_bounds_policy_and_members(self):
        member = DigestMember("sub-1", 3, "event-1", 2)
        window = DigestWindow(
            "window-2026-09-25",
            "Europe/Paris",
            "2026-09-25T08:00",
            NOW,
            NOW + timedelta(hours=1),
            NOW + timedelta(hours=1),
            DstFoldPolicy.SECOND_OCCURRENCE,
            DstGapPolicy.NEXT_VALID_INSTANT,
            (member,),
        )
        self.assertEqual(window.members, (member,))
        self.assertEqual(window.utc_start.tzinfo, UTC)
        self.assertEqual(window.fold_policy.value, "second_occurrence")
        self.assertEqual(window.gap_policy.value, "next_valid_instant")
        self.assertEqual(window.policy_revision, 1)
        revised_policy = DigestWindow(
            "window-2026-09-25-revised",
            "Europe/Paris",
            "2026-09-25T08:00",
            NOW,
            NOW + timedelta(hours=1),
            NOW + timedelta(hours=1),
            DstFoldPolicy.FIRST_OCCURRENCE,
            DstGapPolicy.SKIP,
            (member,),
            policy_revision=4,
        )
        self.assertEqual(revised_policy.policy_revision, 4)
        with self.assertRaises(ValueError):
            DigestWindow(
                "window",
                "Europe/Paris",
                "local",
                NOW.replace(tzinfo=None),
                NOW + timedelta(hours=1),
                NOW + timedelta(hours=1),
                DstFoldPolicy.FIRST_OCCURRENCE,
                DstGapPolicy.SKIP,
                (member,),
            )
        with self.assertRaises(ValueError):
            DigestWindow(
                "window",
                "Europe/Paris",
                "local",
                NOW,
                NOW + timedelta(hours=1),
                NOW + timedelta(hours=1),
                DstFoldPolicy.FIRST_OCCURRENCE,
                DstGapPolicy.SKIP,
                (member,),
                policy_revision=0,
            )

        class EvilInt(int):
            def __lt__(self, other):
                return False

        with self.assertRaises(ValueError):
            DigestWindow(
                "window",
                "Europe/Paris",
                "local",
                NOW,
                NOW + timedelta(hours=1),
                NOW + timedelta(hours=1),
                DstFoldPolicy.FIRST_OCCURRENCE,
                DstGapPolicy.SKIP,
                (member,),
                policy_revision=EvilInt(-1),
            )
        with self.assertRaises(ValueError):
            DigestWindow(
                "window",
                "not/a-real-timezone",
                "local",
                NOW,
                NOW + timedelta(hours=1),
                NOW + timedelta(hours=1),
                DstFoldPolicy.FIRST_OCCURRENCE,
                DstGapPolicy.SKIP,
                (member,),
            )
        with self.assertRaises(ValueError):
            DigestWindow(
                "window",
                "Europe/Paris",
                "local",
                NOW,
                NOW + timedelta(hours=1),
                NOW,
                DstFoldPolicy.FIRST_OCCURRENCE,
                DstGapPolicy.SKIP,
                (member,),
            )

    def test_c12_c13_authorized_recipient_fails_closed_and_group_authority_is_unset(
        self,
    ):
        class Resolver:
            def __init__(self, issuer, result):
                self.issuer = issuer
                self.result = result
                self.lookups = 0

            async def resolve(self, invocation):
                self.issuer.require(invocation)
                self.lookups += 1
                if (
                    invocation.adapter_id != self.result.adapter_id
                    or invocation.conversation_id != self.result.conversation_id
                ):
                    return None
                return self.result

        issuer = ContextIssuer()
        issued = issuer.issue(
            origin=InvocationOrigin.COMMAND,
            module_id="sample/alpha",
            module_epoch=1,
            registry_revision=1,
            actor_id="user-1",
            adapter_id="adapter",
            conversation_id="direct-1",
        )
        direct = ConversationRef(
            "adapter", ConversationKind.DIRECT, "direct-1", "route-1"
        )
        resolver = Resolver(issuer, direct)
        self.assertEqual(
            asyncio.run(resolve_authorized_recipient(issued, resolver)).conversation,
            direct,
        )
        calls_after_issued = resolver.lookups
        copied = replace(issued)
        with self.assertRaises(InvalidInvocation):
            issuer.require(copied)
        self.assertEqual(
            asyncio.run(resolve_authorized_recipient(copied, resolver)),
            AuthorizedRecipient.refused(),
        )
        unissued = sample_invocation()
        with self.assertRaises(InvalidInvocation):
            issuer.require(unissued)
        self.assertEqual(
            asyncio.run(resolve_authorized_recipient(unissued, resolver)),
            AuthorizedRecipient.refused(),
        )
        self.assertEqual(resolver.lookups, calls_after_issued)

        group = ConversationRef(
            "adapter", ConversationKind.GROUP, "direct-1", "route-1"
        )
        self.assertEqual(
            asyncio.run(resolve_authorized_recipient(issued, Resolver(issuer, group))),
            AuthorizedRecipient.refused(),
        )
        mismatched = ConversationRef(
            "adapter", ConversationKind.DIRECT, "other", "route-1"
        )
        self.assertEqual(
            asyncio.run(
                resolve_authorized_recipient(issued, Resolver(issuer, mismatched))
            ),
            AuthorizedRecipient.refused(),
        )
        self.assertEqual(
            asyncio.run(
                resolve_authorized_recipient(
                    sample_invocation(conversation_id=None), Resolver(issuer, direct)
                )
            ),
            AuthorizedRecipient.refused(),
        )
        with self.assertRaises(ValueError):
            AuthorizedRecipient(group)
        # No DTO or port here asserts authority to create group subscriptions.
        self.assertFalse(any("group_authority" in name for name in globals()))

    def test_c14_port_and_output_annotations_are_resolvable(self):
        for method in (
            SchedulerRepository.claim_due,
            SchedulerRepository.current_evaluation,
            SchedulerRepository.commit_observation,
            SchedulerRepository.release,
            SubscriptionRepository.current_revision,
            CommandOutput.__post_init__,
            SubscriptionOutput.__post_init__,
        ):
            self.assertTrue(get_type_hints(method), method.__qualname__)
        lease = ExecutionLease(
            sample_key(), "lease-token", 1, 1, 1, NOW + timedelta(minutes=1)
        )
        self.assertEqual(lease.expires_at, NOW + timedelta(minutes=1))
        request = CollectionRunRequest(sample_key(), NOW, 60, 1, 1, 1)
        self.assertEqual(request.key, lease.key)
        with self.assertRaises(ValueError):
            CollectionRunRequest(sample_key(), NOW, 0, 1, 1, 1)

    def test_c03_atomic_commit_digest_claim_and_delivery_cas_contracts(self):
        member = DigestMember("sub-1", 3, "event-1", 2)
        recipient = ConversationRef(
            "adapter", ConversationKind.DIRECT, "direct-1", "route-1"
        )
        digest_document = DisplayDocument(
            "Digest event", "Saved subject", (TextBlock("Saved body"),)
        )
        digest_event = DeliveryEvent(
            "event-1",
            2,
            "sub-1",
            3,
            "user-1",
            None,
            recipient,
            digest_document,
            delivery_idempotency_key("event-1", 2, "sub-1", 3, recipient),
        )
        association = DigestMemberAssociation(
            "window-1", recipient, member, digest_event
        )
        self.assertIs(association.event.display_data, digest_document)
        with self.assertRaises(TypeError):
            DigestMemberAssociation("window-1", recipient, member, object())
        evaluation = SubscriptionEvaluationCommit(
            "sub-1",
            3,
            None,
            EvaluationState(1, {"counter": 1}),
            ObservationCursor("observation-1", 1, ObservationCompleteness.COMPLETE, ()),
            delivery_events=(digest_event,),
            digest_members=(association,),
        )
        self.assertEqual(evaluation.digest_members, (association,))
        observation = Observation(
            "observation-1",
            sample_key(),
            1,
            None,
            NOW,
            ObservationCompleteness.COMPLETE,
            (),
            {},
        )
        commit = ObservationEvaluationCommit(observation, (evaluation,))
        self.assertEqual(commit.subscriptions, (evaluation,))
        with self.assertRaises(ValueError):
            SubscriptionEvaluationCommit(
                "sub-1",
                3,
                None,
                EvaluationState(1, {}),
                ObservationCursor(
                    "observation-1", 1, ObservationCompleteness.COMPLETE, ()
                ),
                delivery_events=(digest_event,),
                digest_members=(
                    association,
                    DigestMemberAssociation(
                        "window-2", recipient, member, digest_event
                    ),
                ),
            )
        with self.assertRaises(ValueError):
            SubscriptionEvaluationCommit(
                "sub-1",
                3,
                None,
                EvaluationState(1, {}),
                ObservationCursor(
                    "observation-1", 1, ObservationCompleteness.COMPLETE, ()
                ),
                digest_members=(association,),
            )
        forged_document_event = replace(
            digest_event,
            display_data=DisplayDocument(
                "Different document", "Other subject", (TextBlock("other"),)
            ),
        )
        with self.assertRaises(ValueError):
            SubscriptionEvaluationCommit(
                "sub-1",
                3,
                None,
                EvaluationState(1, {}),
                ObservationCursor(
                    "observation-1", 1, ObservationCompleteness.COMPLETE, ()
                ),
                delivery_events=(digest_event,),
                digest_members=(
                    DigestMemberAssociation(
                        "window-1", recipient, member, forged_document_event
                    ),
                ),
            )
        with self.assertRaises(ValueError):
            DigestMemberAssociation(
                "window-1",
                recipient,
                DigestMember("sub-1", 3, "event-1", 1),
                digest_event,
            )
        with self.assertRaises(ValueError):
            ObservationEvaluationCommit(
                replace(
                    observation,
                    completeness=ObservationCompleteness.FAILED,
                ),
                (evaluation,),
            )

        association_record = SubscriptionJobAssociation("sub-1", 3, sample_key(), 60, 1)
        self.assertEqual(association_record.association_revision, 1)
        for method in (
            SubscriptionJobRepository.associate,
            SubscriptionJobRepository.remove,
            SubscriptionJobRepository.for_collection,
            SubscriptionJobRepository.current_for_subscription,
            SchedulerRepository.commit_observation_with_evaluations,
            DigestWindowRepository.claim_due_envelope,
            DigestWindowRepository.current_envelope,
            DigestWindowRepository.reconcile_envelope_members,
            DigestWindowRepository.begin_envelope_send,
            DigestWindowRepository.complete_envelope_send,
            DigestWindowRepository.recover_expired_envelope_claims,
            DigestWindowRepository.retry_failed_envelope,
            SubscriptionLifecycleRepository.apply,
            DeliveryRepository.current_event,
            DeliveryRepository.record_attempt,
            DeliveryRepository.claim_sending,
            DeliveryRepository.mark_sending_unknown,
        ):
            self.assertTrue(get_type_hints(method), method.__qualname__)
        self.assertEqual(
            tuple(
                inspect.signature(DeliveryRepository.mark_sending_unknown).parameters
            ),
            (
                "self",
                "event_key",
                "event_version",
                "subscription_id",
                "subscription_revision",
                "expected_attempt_number",
                "expected_started_at",
                "completed_at",
            ),
        )
        self.assertEqual(
            tuple(
                inspect.signature(DigestWindowRepository.claim_due_envelope).parameters
            ),
            ("self", "window_id", "recipient", "now", "lease_expires_at"),
        )
        self.assertEqual(
            tuple(
                inspect.signature(DigestWindowRepository.current_envelope).parameters
            ),
            ("self", "window_id", "recipient"),
        )
        apply_parameters = inspect.signature(
            SubscriptionLifecycleRepository.apply
        ).parameters
        self.assertEqual(tuple(apply_parameters), ("self", "change", "initial_run"))
        self.assertIs(
            apply_parameters["initial_run"].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        self.assertIsNone(apply_parameters["initial_run"].default)
        apply_types = get_type_hints(SubscriptionLifecycleRepository.apply)
        self.assertIs(apply_types["change"], SubscriptionJobChange)
        self.assertEqual(
            set(get_args(apply_types["initial_run"])),
            {CollectionRunRequest, type(None)},
        )

        claim_time = NOW + timedelta(hours=1)
        expiry = claim_time + timedelta(minutes=5)
        claimed_envelope = DigestEnvelope(
            "envelope-1",
            "window-1",
            recipient,
            (member,),
            state=DigestEnvelopeState.CLAIMED,
            member_associations=(association,),
            claim_token="claim-1",
            claimed_at=claim_time,
            claim_expires_at=expiry,
        )
        claim = DigestEnvelopeClaim("claim-1", claimed_envelope, claim_time, expiry)
        self.assertEqual(claim.envelope, claimed_envelope)
        self.assertIs(
            claim.envelope.member_associations[0].event.display_data,
            digest_document,
        )
        restarted_envelope = DigestEnvelope(
            "envelope-1",
            "window-1",
            recipient,
            (member,),
            state=DigestEnvelopeState.CLAIMED,
            member_associations=(association,),
            claim_token="claim-1",
            claimed_at=claim_time,
            claim_expires_at=expiry,
        )
        self.assertEqual(
            restarted_envelope.member_associations[0].event.display_data,
            digest_document,
        )
        self.assertEqual(claimed_envelope.delivery_attempts, ())
        sending_attempt = DeliveryAttempt(
            1,
            DeliveryState.SENDING,
            digest_envelope_idempotency_key("window-1", recipient),
            claim_time,
        )
        sending_envelope = replace(
            claimed_envelope,
            state=DigestEnvelopeState.SENDING,
            delivery_attempts=(sending_attempt,),
        )
        self.assertEqual(sending_envelope.state, DigestEnvelopeState.SENDING)
        unknown_attempt = replace(
            sending_attempt,
            state=DeliveryState.UNKNOWN,
            completed_at=expiry,
        )
        unknown_envelope = DigestEnvelope(
            "envelope-1",
            "window-1",
            recipient,
            (member,),
            state=DigestEnvelopeState.UNKNOWN,
            member_associations=(association,),
            delivery_attempts=(unknown_attempt,),
        )
        with self.assertRaises(ValueError):
            replace(unknown_envelope, state=DigestEnvelopeState.CLAIMED)
        sent_attempt = replace(
            sending_attempt,
            state=DeliveryState.SENT,
            completed_at=expiry,
        )
        sent_envelope = DigestEnvelope(
            "envelope-1",
            "window-1",
            recipient,
            (member,),
            state=DigestEnvelopeState.SENT,
            member_associations=(association,),
            delivery_attempts=(sent_attempt,),
        )
        with self.assertRaises(ValueError):
            DigestEnvelope("orphan", "window-1", recipient, (member,))
        with self.assertRaises(ValueError):
            DigestEnvelopeClaim(
                "claim-2", sent_envelope, expiry, expiry + timedelta(minutes=1)
            )

    def test_c04_subscription_job_change_bundles_lifecycle_cas(self):
        key = sample_key()
        recipient = ConversationRef(
            "adapter", ConversationKind.DIRECT, "direct-1", "route-1"
        )
        active = SubscriptionRecord(
            "sub-1",
            1,
            "sample/alpha",
            key,
            "user-1",
            None,
            recipient,
            "instant",
            {},
        )
        link_v1 = SubscriptionJobAssociation("sub-1", 1, key, 60, 1, 1)
        created = SubscriptionJobChange(
            SubscriptionJobChangeKind.CREATE, active, link_v1, None, None
        )
        self.assertEqual(created.record, active)
        revised_record = replace(active, revision=2, notification_mode="digest")
        link_v2 = SubscriptionJobAssociation("sub-1", 2, key, 60, 1, 2)
        revised = SubscriptionJobChange(
            SubscriptionJobChangeKind.REVISE, revised_record, link_v2, 1, 1
        )
        self.assertEqual(revised.association.association_revision, 2)
        cancelled_record = replace(
            revised_record, revision=3, status=SubscriptionStatus.CANCELLED
        )
        cancelled = SubscriptionJobChange(
            SubscriptionJobChangeKind.CANCEL, cancelled_record, None, 2, 2
        )
        self.assertIsNone(cancelled.association)
        with self.assertRaises(ValueError):
            SubscriptionJobChange(
                SubscriptionJobChangeKind.REVISE,
                revised_record,
                link_v2,
                1,
                2,
            )

    def test_c05_current_evaluation_snapshot_is_subscription_and_scope_bound(self):
        key = sample_key(OwnerScope.user("user-1"))
        recipient = ConversationRef(
            "adapter", ConversationKind.DIRECT, "direct-1", "route-1"
        )
        subscription = SubscriptionRecord(
            "sub-private",
            7,
            "sample/alpha",
            key,
            "user-1",
            None,
            recipient,
            "instant",
            {"min": 5},
        )
        observation = Observation(
            "private-observation",
            key,
            3,
            None,
            NOW,
            ObservationCompleteness.PARTIAL,
            ("item-1",),
            {"value": 8},
        )
        cursor = ObservationCursor(
            observation.observation_id,
            observation.data_version,
            observation.completeness,
            observation.covered_ids,
        )
        snapshot = SubscriptionEvaluationSnapshot(
            subscription,
            EvaluationState(4, {"count": 2}),
            cursor,
            observation,
        )
        self.assertEqual(snapshot.state.revision, 4)
        self.assertEqual(snapshot.subscription.revision, 7)
        self.assertEqual(snapshot.observation.key.scope, key.scope)
        with self.assertRaises(ValueError):
            SubscriptionEvaluationSnapshot(
                subscription,
                snapshot.state,
                cursor,
                replace(observation, key=sample_key()),
            )
        with self.assertRaises(ValueError):
            SubscriptionEvaluationSnapshot(
                subscription,
                snapshot.state,
                replace(cursor, observation_id="different-observation"),
                observation,
            )

        class ReadProbe:
            async def current_evaluation(self, subscription_id, collection_key):
                if (
                    subscription_id != subscription.subscription_id
                    or collection_key != subscription.collection_key
                ):
                    return None
                return snapshot

        repo = ReadProbe()
        self.assertEqual(
            asyncio.run(repo.current_evaluation("sub-private", key)), snapshot
        )
        self.assertIsNone(
            asyncio.run(repo.current_evaluation("sub-private", sample_key()))
        )
        self.assertIsNone(
            asyncio.run(
                repo.current_evaluation(
                    "sub-private", sample_key(OwnerScope.user("other-user"))
                )
            )
        )
        grant = GrantReference("grant-1", 3)
        authorized_key = sample_key(OwnerScope.authorized("user-1", grant))
        authorized_subscription = SubscriptionRecord(
            "sub-authorized",
            2,
            "sample/alpha",
            authorized_key,
            "user-1",
            grant,
            recipient,
            "instant",
            {},
        )
        authorized_observation = Observation(
            "authorized-observation",
            authorized_key,
            1,
            None,
            NOW,
            ObservationCompleteness.COMPLETE,
            (),
            {},
        )
        authorized_snapshot = SubscriptionEvaluationSnapshot(
            authorized_subscription,
            EvaluationState(1, {}),
            ObservationCursor(
                authorized_observation.observation_id,
                authorized_observation.data_version,
                authorized_observation.completeness,
                authorized_observation.covered_ids,
            ),
            authorized_observation,
        )

        class GrantReadProbe:
            async def current_evaluation(self, subscription_id, collection_key):
                if (
                    subscription_id != authorized_subscription.subscription_id
                    or collection_key != authorized_subscription.collection_key
                ):
                    return None
                return authorized_snapshot

        self.assertIsNone(
            asyncio.run(
                GrantReadProbe().current_evaluation(
                    "sub-authorized",
                    sample_key(
                        OwnerScope.authorized("user-1", GrantReference("grant-1", 4))
                    ),
                )
            )
        )

    def test_c07_subscription_request_and_digest_schedule_contract(self):
        profile = DigestScheduleProfile(
            "Asia/Shanghai",
            "08:00",
            3600,
            DstFoldPolicy.FIRST_OCCURRENCE,
            DstGapPolicy.SKIP,
            2,
        )
        create_request = SubscriptionRequest(
            "dota2.match",
            {"account": "42"},
            {"threshold": 3},
            "digest",
            profile,
        )
        self.assertIsNone(create_request.subscription_id)
        self.assertIsNone(create_request.expected_revision)
        self.assertNotIn("owner_id", create_request.__dataclass_fields__)
        self.assertNotIn("module_id", create_request.__dataclass_fields__)
        self.assertNotIn("recipient", create_request.__dataclass_fields__)
        with self.assertRaises(TypeError):
            create_request.filters["threshold"] = 4

        revise_request = SubscriptionRequest(
            "dota2.match",
            {"account": "42"},
            {"threshold": 4},
            "instant",
            None,
            "sub-1",
            3,
        )
        self.assertEqual(revise_request.subscription_id, "sub-1")
        self.assertEqual(revise_request.expected_revision, 3)
        with self.assertRaises(ValueError):
            SubscriptionRequest("type", {}, {}, "instant", None, "sub-1", None)
        with self.assertRaises(ValueError):
            DigestScheduleProfile(
                "Asia/Shanghai",
                "24:00",
                3600,
                DstFoldPolicy.FIRST_OCCURRENCE,
                DstGapPolicy.SKIP,
            )
        with self.assertRaises(ValueError):
            DigestScheduleProfile(
                "No/SuchZone",
                "08:00",
                3600,
                DstFoldPolicy.FIRST_OCCURRENCE,
                DstGapPolicy.SKIP,
            )

        recipient = ConversationRef(
            "adapter", ConversationKind.DIRECT, "direct-1", "route-1"
        )
        window = DigestWindow(
            "window-1",
            "Asia/Shanghai",
            "2026-09-25",
            datetime(2026, 9, 24, 23, 0, tzinfo=UTC),
            datetime(2026, 9, 25, 0, 0, tzinfo=UTC),
            datetime(2026, 9, 25, 0, 0, tzinfo=UTC),
            profile.fold_policy,
            profile.gap_policy,
            (),
            profile.policy_revision,
            profile,
            recipient,
        )
        self.assertEqual(window.due_at, window.utc_end)
        selector = DigestWindowSelector(
            profile, recipient, datetime(2026, 9, 24, 23, 30, tzinfo=UTC)
        )
        self.assertEqual(selector.recipient, window.schedule_recipient)
        with self.assertRaises(ValueError):
            replace(window, due_at=window.utc_end + timedelta(seconds=1))

        due_job = DueCollectionJob(sample_key(), NOW, 60.0, 1, "job-key-1")
        self.assertEqual(due_job.key, sample_key())
        self.assertEqual(due_job.cursor, DueJobCursor(NOW, "job-key-1"))
        self.assertNotIn("module_epoch", DueCollectionJob.__dataclass_fields__)
        self.assertNotIn("registry_revision", DueCollectionJob.__dataclass_fields__)
        candidate = DigestRouteCandidate("window-1", recipient, NOW, "recipient-key")
        self.assertEqual(candidate.recipient, recipient)
        self.assertEqual(
            candidate.cursor,
            DigestRouteCursor(NOW, "window-1", "recipient-key"),
        )

        scheduled_record = SubscriptionRecord(
            "sub-scheduled",
            4,
            "sample/alpha",
            sample_key(),
            "user-1",
            None,
            recipient,
            "digest",
            {},
            type_id="dota2.match",
            digest_schedule=profile,
        )
        active_schedule = ActiveDigestSchedule(scheduled_record)
        self.assertEqual(active_schedule.cursor, "sub-scheduled")
        self.assertEqual(active_schedule.record.digest_schedule, profile)
        with self.assertRaises(ValueError):
            ActiveDigestSchedule(replace(scheduled_record, notification_mode="instant"))
        with self.assertRaises(ValueError):
            ActiveDigestSchedule(replace(scheduled_record, digest_schedule=None))

    def test_c07_known_failure_retry_contract_and_routes(self):
        recipient = ConversationRef(
            "adapter", ConversationKind.DIRECT, "direct-1", "route-1"
        )
        document = DisplayDocument("Notice", "Event", (TextBlock("notice"),))
        key = delivery_idempotency_key("event", 1, "sub", 1, recipient)
        failed_attempt = DeliveryAttempt(
            1, DeliveryState.FAILED, key, NOW, NOW, error_code="host_rejected"
        )
        failed = DeliveryEvent(
            "event",
            1,
            "sub",
            1,
            "owner",
            None,
            recipient,
            document,
            key,
            DeliveryState.FAILED,
            failed_attempt,
            NOW + timedelta(minutes=5),
        )
        self.assertEqual(failed.retry_at, NOW + timedelta(minutes=5))
        self.assertEqual(
            failed.cursor,
            DeliveryEventCursor("event", 1, "sub", 1),
        )
        with self.assertRaisesRegex(ValueError, "reconciliation attempt"):
            DeliveryEvent(
                "event",
                1,
                "sub",
                1,
                "owner",
                None,
                recipient,
                document,
                key,
                DeliveryState.UNKNOWN,
            )
        with self.assertRaises(ValueError):
            replace(failed, state=DeliveryState.UNKNOWN)
        with self.assertRaises(ValueError):
            replace(
                failed, retry_at=NOW - timedelta(seconds=1), state=DeliveryState.PENDING
            )

        for method in (
            SubscriptionStore.list_for_owner,
            SubscriptionStore.list_active_digest_schedules,
            SubscriptionJobRepository.current_for_subscription,
            SchedulerRepository.list_due_jobs,
            DigestWindowRepository.list_due_routes,
            DeliveryRepository.list_due_events,
            TrustedPersistedRouteResolver.resolve_current,
            TrustedPersistedRouteResolver.resolve_private,
        ):
            self.assertTrue(get_type_hints(method))
        self.assertIn(
            "SubscriptionRequest",
            str(get_type_hints(SubscriptionOperations.create_request)["request"]),
        )

    def test_c08_global_schedule_scan_and_starvation_free_due_cursors(self):
        scan_methods = (
            (SchedulerRepository.list_due_jobs, DueJobCursor),
            (DigestWindowRepository.list_due_routes, DigestRouteCursor),
            (DeliveryRepository.list_due_events, DeliveryEventCursor),
        )
        for method, cursor_type in scan_methods:
            parameters = inspect.signature(method).parameters
            self.assertIn("after_cursor", parameters)
            self.assertIsNone(parameters["after_cursor"].default)
            hints = get_type_hints(method)
            self.assertIn(cursor_type, get_args(hints["after_cursor"]))

        schedule_scan = SubscriptionStore.list_active_digest_schedules
        schedule_params = inspect.signature(schedule_scan).parameters
        self.assertEqual(
            tuple(schedule_params),
            ("self", "limit", "after_subscription_id"),
        )
        self.assertIsNone(schedule_params["after_subscription_id"].default)

        for method in (schedule_scan, *(method for method, _ in scan_methods)):
            self.assertTrue(get_type_hints(method))


if __name__ == "__main__":
    unittest.main()
