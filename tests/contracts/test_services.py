"""Boundary checks for storage, scheduling, and module service contracts."""

import unittest
from datetime import UTC, datetime
from typing import get_type_hints

from ygl_test_subject.core.contracts.subscriptions import validate_evaluation_decision
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.ports import (
    MessageReceipt,
    MessageStatus,
    MessageTarget,
    RenderedMessage,
)

from yomihime_game_link_sdk.contexts import InvocationOrigin, InvocationView
from yomihime_game_link_sdk.display import DisplayDocument, TextBlock
from yomihime_game_link_sdk.services import (
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
from yomihime_game_link_sdk.storage import (
    CacheEntry,
    DeclaredIndexQuery,
    GrantReference,
    OwnerScope,
    OwnershipKind,
    QueryOperator,
    RecordPage,
    VersionedRecord,
)
from yomihime_game_link_sdk.subscriptions import (
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
)


class ServicesContractTests(unittest.TestCase):
    def test_pages_and_collection_views_reject_mutable_untyped_members(self):
        with self.assertRaises(TypeError):
            validate_contract(RecordPage([{"mutable": []}]))
        with self.assertRaises(TypeError):
            validate_contract(CollectionView({"mutable": []}))
        for status in (200.5, "200", True):
            with self.subTest(status=status), self.assertRaises(ValueError):
                validate_contract(HttpResponse(status, {}, b""))
        records = []
        page = validate_contract(RecordPage(records))
        records.append({"changed": True})
        self.assertEqual(page.records, ())

    def test_private_scope_requires_user_and_versioned_grant(self):
        grant = validate_contract(GrantReference("grant-1", 2))
        scope = OwnerScope.authorized("user-1", grant)
        self.assertEqual(scope.kind, OwnershipKind.AUTHORIZED)
        self.assertEqual(OwnerScope.user("user-1").kind, OwnershipKind.USER)
        with self.assertRaises(ValueError):
            validate_contract(OwnerScope(OwnershipKind.AUTHORIZED, "user-1"))
        with self.assertRaises(ValueError):
            validate_contract(GrantReference("grant-1", 0))
        with self.assertRaises(ValueError):
            validate_contract(OwnerScope(OwnershipKind.PUBLIC, "user-1", grant))

    def test_json_snapshots_cannot_be_mutated_through_source_values(self):
        source = {"items": [{"name": "before"}]}
        record = validate_contract(VersionedRecord("record-1", 1, source))
        source["items"][0]["name"] = "after"
        self.assertEqual(record.value["items"][0]["name"], "before")
        with self.assertRaises(TypeError):
            record.value["new"] = "no"  # type: ignore[index]
        entry = validate_contract(CacheEntry("cache-1", source, datetime.now(UTC)))
        with self.assertRaises(ValueError):
            validate_contract(CacheEntry("cache-2", {}, datetime.now()))
        self.assertEqual(entry.payload["items"][0]["name"], "after")

    def test_observation_and_decision_validate_scope_and_event_version(self):
        key = validate_contract(
            CollectionKey(
                "example/steam",
                "prices",
                1,
                "steam",
                validate_contract(NormalizedInput({"app": 1})),
                OwnerScope.public(),
            )
        )
        now = datetime.now(UTC)
        observation = validate_contract(
            Observation(
                "obs-1",
                key,
                1,
                now,
                now,
                ObservationCompleteness.COMPLETE,
                ("app-1",),
                {"price": 10},
            )
        )
        self.assertEqual(observation.payload["price"], 10)
        document = validate_contract(
            DisplayDocument(
                "Sale", "App", (validate_contract(TextBlock("Now on sale")),)
            )
        )
        decision = validate_contract(EvaluationDecision({}, True, "sale", 1, document))
        subscription = validate_contract(
            SubscriptionView("sub-1", 1, "user-1", None, "chat-1", {})
        )
        self.assertIs(
            validate_evaluation_decision(subscription, observation, decision), decision
        )
        with self.assertRaises((TypeError, ValueError)):
            validate_contract(EvaluationDecision({}, True, "sale", None, document))
        with self.assertRaises(TypeError):
            validate_contract(EvaluationDecision({}, True, "sale", 1, {}))
        private_key = validate_contract(
            CollectionKey(
                "example/steam",
                "prices",
                1,
                "steam",
                validate_contract(NormalizedInput({"app": 1})),
                OwnerScope.user("owner-1"),
            )
        )
        private_observation = validate_contract(
            Observation(
                "obs-2",
                private_key,
                1,
                now,
                now,
                ObservationCompleteness.COMPLETE,
                (),
                {},
            )
        )
        with self.assertRaises(ValueError):
            validate_evaluation_decision(
                subscription,
                private_observation,
                validate_contract(EvaluationDecision({}, False)),
            )
        self.assertEqual(
            validate_contract(IntervalLimits(5, 30, 60)).target_seconds, 60
        )

    def test_scheduling_and_http_contracts_reject_scope_or_credential_escape(self):
        schedule = validate_contract(
            ScheduleDescriptor(
                "prices",
                1,
                "steam",
                1,
                {"type": "object"},
                OwnershipKind.PUBLIC,
                ScheduleTrigger.PERIODIC,
                60,
                default_interval_seconds=60,
                interval_config_key="prices_interval",
            )
        )
        self.assertEqual(schedule.trigger, ScheduleTrigger.PERIODIC)
        with self.assertRaises(ValueError):
            validate_contract(HttpRequest("steam", "//other.example/path"))
        with self.assertRaises(ValueError):
            validate_contract(HttpRequest(object(), "/path"))  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            validate_contract(HttpRequest("steam\nsource", "/path"))
        with self.assertRaises(ValueError):
            validate_contract(HttpRequest("steam", "/safe/../secret"))
        for path in ("/search?host=evil.example", "/search#fragment"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_contract(HttpRequest("steam", path))
        with self.assertRaises(ValueError):
            validate_contract(
                HttpRequest("steam", "/path", headers={"Authorization": "secret"})
            )
        for header in (
            "Host",
            "Accept-Encoding",
            "Content-Length",
            "Transfer-Encoding",
            "Connection",
            "Upgrade",
        ):
            with self.subTest(header=header), self.assertRaises(ValueError):
                validate_contract(
                    HttpRequest("steam", "/path", headers={header: "controlled"})
                )
        query = validate_contract(
            HttpRequest(
                "steam",
                "/search",
                query=(("q", 'Iron ore 50% +#&"铁矿"'),),
            )
        )
        self.assertEqual(query.query[0][1], 'Iron ore 50% +#&"铁矿"')
        for value in ("line\nbreak", "tab\there", "del\x7f"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_contract(
                    HttpRequest("steam", "/search", query=(("q", value),))
                )
        request = validate_contract(
            HttpRequest("steam", "/graphql", "POST", body=b"{}")
        )
        self.assertEqual(request.method, "POST")
        with self.assertRaises(TypeError):
            validate_contract(HttpRequest("steam", "/path", query=(["key", "value"],)))
        with self.assertRaises(TypeError):
            validate_contract(
                ScheduleDescriptor(
                    "prices",
                    1,
                    "steam",
                    1,
                    {"type": "object"},
                    "public",
                    ScheduleTrigger.PERIODIC,
                    60,  # type: ignore[arg-type]
                    default_interval_seconds=60,
                    interval_config_key="prices_interval",
                )
            )

    def test_health_and_message_status_are_typed(self):
        report = validate_contract(
            HealthReport(
                {"lookup": validate_contract(CapabilityHealth(HealthStatus.AVAILABLE))}
            )
        )
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
            validate_contract(DeclaredIndexQuery("by_name", QueryOperator.EQUALS, []))  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            validate_contract(
                CollectionKey("example/steam", "prices", 1, "steam", {}, "public")
            )  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            validate_contract(EvaluationDecision({}, "false"))  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            validate_contract(ResourceReference("../secret", "", object()))  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            validate_contract(ResourceReference(" ", "image/png", OwnerScope.public()))
        with self.assertRaises(TypeError):
            validate_contract(HttpResponse(200, {}, "body"))  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            validate_contract(ResolvedIdentity("identity", "", "subject"))
        identity = validate_contract(ResolvedIdentity("identity", "steam", "subject"))
        with self.assertRaises(TypeError):
            validate_contract(BindingView("binding", 1, identity, 1))  # type: ignore[arg-type]

    def test_factory_wires_only_public_protocols(self):
        context = validate_contract(
            InvocationView(
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
        )

        class Handler:
            async def invoke(self, context, parameters):
                return None

        class Instance:
            def handlers(self):
                return validate_contract(ModuleHandlers({"lookup": Handler()}, {}, {}))

            async def start(self):
                return None

            async def stop(self):
                return None

            async def check_health(self):
                return validate_contract(
                    HealthReport(
                        {
                            "lookup": validate_contract(
                                CapabilityHealth(HealthStatus.AVAILABLE)
                            )
                        }
                    )
                )

        class Factory:
            async def create(self, services):
                return Instance()

        self.assertTrue(isinstance(Handler(), CapabilityHandler))
        self.assertTrue(isinstance(Instance(), ModuleInstance))
        self.assertTrue(isinstance(Factory(), ModuleFactory))
        self.assertEqual(context.module_id, "example/steam")
        self.assertIn("config", get_type_hints(ModuleServices))
