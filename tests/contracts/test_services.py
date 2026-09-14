"""Boundary checks for storage, scheduling, and module service contracts."""

import unittest
from datetime import UTC, datetime
from typing import get_type_hints

from ygl_test_subject.api.contexts import InvocationOrigin, InvocationView
from ygl_test_subject.api.display import DisplayDocument, TextBlock
from ygl_test_subject.api.services import (
    BindingView,
    CapabilityHandler,
    CapabilityHealth,
    HealthReport,
    HealthStatus,
    HttpRequest,
    HttpResponse,
    ModuleFactory,
    ModuleHandlers,
    ModuleInstance,
    ModuleServices,
    ResolvedIdentity,
    ResourceReference,
)
from ygl_test_subject.api.storage import (
    CacheEntry,
    DeclaredIndexQuery,
    GrantReference,
    OwnerScope,
    OwnershipKind,
    QueryOperator,
    RecordPage,
    VersionedRecord,
)
from ygl_test_subject.api.subscriptions import (
    CollectionKey,
    CollectionView,
    EvaluationDecision,
    IntervalLimits,
    NormalizedInput,
    Observation,
    ObservationCompleteness,
    ScheduleDescriptor,
    ScheduleTrigger,
    SubscriptionView,
    validate_evaluation_decision,
)
from ygl_test_subject.core.ports import (
    MessageReceipt,
    MessageStatus,
    MessageTarget,
    RenderedMessage,
)


class ServicesContractTests(unittest.TestCase):
    def test_pages_and_collection_views_reject_mutable_untyped_members(self):
        with self.assertRaises(TypeError):
            RecordPage([{"mutable": []}])
        with self.assertRaises(TypeError):
            CollectionView({"mutable": []})
        for status in (200.5, "200", True):
            with self.subTest(status=status), self.assertRaises(ValueError):
                HttpResponse(status, {}, b"")
        records = []
        page = RecordPage(records)
        records.append({"changed": True})
        self.assertEqual(page.records, ())

    def test_private_scope_requires_user_and_versioned_grant(self):
        grant = GrantReference("grant-1", 2)
        scope = OwnerScope.private("user-1", grant)
        self.assertEqual(scope.kind, OwnershipKind.AUTHORIZED)
        self.assertEqual(OwnerScope.user("user-1").kind, OwnershipKind.USER)
        with self.assertRaises(ValueError):
            OwnerScope(OwnershipKind.AUTHORIZED, "user-1")
        with self.assertRaises(ValueError):
            GrantReference("grant-1", 0)
        with self.assertRaises(ValueError):
            OwnerScope(OwnershipKind.PUBLIC, "user-1", grant)

    def test_json_snapshots_cannot_be_mutated_through_source_values(self):
        source = {"items": [{"name": "before"}]}
        record = VersionedRecord("record-1", 1, source)
        source["items"][0]["name"] = "after"
        self.assertEqual(record.value["items"][0]["name"], "before")
        with self.assertRaises(TypeError):
            record.value["new"] = "no"  # type: ignore[index]
        entry = CacheEntry("cache-1", source, datetime.now(UTC))
        with self.assertRaises(ValueError):
            CacheEntry("cache-2", {}, datetime.now())
        self.assertEqual(entry.payload["items"][0]["name"], "after")

    def test_observation_and_decision_validate_scope_and_event_version(self):
        key = CollectionKey(
            "example/steam",
            "prices",
            1,
            "steam",
            NormalizedInput({"app": 1}),
            OwnerScope.public(),
        )
        now = datetime.now(UTC)
        observation = Observation(
            "obs-1",
            key,
            1,
            now,
            now,
            ObservationCompleteness.COMPLETE,
            ("app-1",),
            {"price": 10},
        )
        self.assertEqual(observation.payload["price"], 10)
        document = DisplayDocument("Sale", "App", (TextBlock("Now on sale"),))
        decision = EvaluationDecision({}, True, "sale", 1, document)
        subscription = SubscriptionView("sub-1", 1, "user-1", None, "chat-1", {})
        self.assertIs(
            validate_evaluation_decision(subscription, observation, decision), decision
        )
        with self.assertRaises((TypeError, ValueError)):
            EvaluationDecision({}, True, "sale", None, document)
        with self.assertRaises(TypeError):
            EvaluationDecision({}, True, "sale", 1, {})
        private_key = CollectionKey(
            "example/steam",
            "prices",
            1,
            "steam",
            NormalizedInput({"app": 1}),
            OwnerScope.user("owner-1"),
        )
        private_observation = Observation(
            "obs-2",
            private_key,
            1,
            now,
            now,
            ObservationCompleteness.COMPLETE,
            (),
            {},
        )
        with self.assertRaises(ValueError):
            validate_evaluation_decision(
                subscription, private_observation, EvaluationDecision({}, False)
            )
        self.assertEqual(IntervalLimits(5, 30, 60).target_seconds, 60)

    def test_scheduling_and_http_contracts_reject_scope_or_credential_escape(self):
        schedule = ScheduleDescriptor(
            "prices",
            1,
            "steam",
            1,
            {"type": "object"},
            OwnershipKind.PUBLIC,
            ScheduleTrigger.PERIODIC,
            60,
        )
        self.assertEqual(schedule.trigger, ScheduleTrigger.PERIODIC)
        with self.assertRaises(ValueError):
            HttpRequest("steam", "//other.example/path")
        with self.assertRaises(ValueError):
            HttpRequest(object(), "/path")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            HttpRequest("steam\nsource", "/path")
        with self.assertRaises(ValueError):
            HttpRequest("steam", "/safe/../secret")
        with self.assertRaises(ValueError):
            HttpRequest("steam", "/path", headers={"Authorization": "secret"})
        request = HttpRequest("steam", "/graphql", "POST", body=b"{}")
        self.assertEqual(request.method, "POST")
        with self.assertRaises(TypeError):
            HttpRequest("steam", "/path", query=(["key", "value"],))
        with self.assertRaises(TypeError):
            ScheduleDescriptor(
                "prices",
                1,
                "steam",
                1,
                {"type": "object"},
                "public",
                ScheduleTrigger.PERIODIC,
                60,  # type: ignore[arg-type]
            )

    def test_health_and_message_status_are_typed(self):
        report = HealthReport({"lookup": CapabilityHealth(HealthStatus.AVAILABLE)})
        self.assertEqual(report.capabilities["lookup"].status, HealthStatus.AVAILABLE)
        self.assertEqual(
            MessageReceipt(MessageStatus.ACCEPTED).status, MessageStatus.ACCEPTED
        )
        with self.assertRaises(TypeError):
            MessageReceipt("accepted")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            MessageTarget("")
        with self.assertRaises(ValueError):
            RenderedMessage("text", ["../secret"])  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            RenderedMessage("text", (" ",))

    def test_dtos_reject_wrong_nested_types_and_scalar_escape(self):
        with self.assertRaises(TypeError):
            DeclaredIndexQuery("by_name", QueryOperator.EQUALS, [])  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            CollectionKey("example/steam", "prices", 1, "steam", {}, "public")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            EvaluationDecision({}, "false")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            ResourceReference("../secret", "", object())  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            ResourceReference(" ", "image/png", OwnerScope.public())
        with self.assertRaises(TypeError):
            HttpResponse(200, {}, "body")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            ResolvedIdentity("identity", "", "subject")
        identity = ResolvedIdentity("identity", "steam", "subject")
        with self.assertRaises(TypeError):
            BindingView("binding", 1, identity, 1)  # type: ignore[arg-type]

    def test_factory_wires_only_public_protocols(self):
        context = InvocationView(
            invocation_id="inv-1",
            origin=InvocationOrigin.COMMAND,
            actor_id="u",
            conversation_id="c",
            module_id="example/steam",
            module_epoch=1,
            registry_revision=1,
            deadline=None,
            parent_id=None,
            grant_id=None,
            grant_revision=None,
            subscription_id=None,
            subscription_revision=None,
        )

        class Handler:
            async def invoke(self, context, parameters):
                return None

        class Instance:
            def handlers(self):
                return ModuleHandlers({"lookup": Handler()}, {}, {})

            async def start(self):
                return None

            async def stop(self):
                return None

            async def check_health(self):
                return HealthReport(
                    {"lookup": CapabilityHealth(HealthStatus.AVAILABLE)}
                )

        class Factory:
            async def create(self, services):
                return Instance()

        self.assertTrue(isinstance(Handler(), CapabilityHandler))
        self.assertTrue(isinstance(Instance(), ModuleInstance))
        self.assertTrue(isinstance(Factory(), ModuleFactory))
        self.assertEqual(context.module_id, "example/steam")
        self.assertIn("config", get_type_hints(ModuleServices))
