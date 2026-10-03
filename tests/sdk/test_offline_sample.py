"""SDK-only contract tests for the inert offline sample package."""

from __future__ import annotations

import unittest
from pathlib import Path

from examples.offline_sample.module import Factory, SourceFactory
from extensions.disk_manifest import parse_manifest
from yomihime_sdk import (
    BindingView,
    CapabilityReference,
    CapabilityResult,
    CollectionKey,
    CollectionView,
    DisplayDocument,
    FactDocument,
    GrantReference,
    HealthStatus,
    InvocationOrigin,
    InvocationView,
    ModuleServices,
    NormalizedInput,
    ObservationCompleteness,
    OwnerScope,
    OwnershipKind,
    Privacy,
    ResolvedIdentity,
    ResultStatus,
    SubscriptionRequest,
    SubscriptionView,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "examples" / "offline_sample" / "manifest.json"


class _Accounts:
    def __init__(self) -> None:
        self.bind_calls: list[tuple[InvocationView, ResolvedIdentity]] = []
        self.list_calls: list[InvocationView] = []
        self.unbind_calls: list[tuple[InvocationView, str, int]] = []
        self.status_calls: list[InvocationView] = []
        self.binding = BindingView(
            "binding-secret-734",
            918273,
            ResolvedIdentity(
                "provider-secret-381:subject-secret-982",
                "provider-secret-381",
                "subject-secret-982",
            ),
            True,
        )
        self.status_value = GrantReference("grant-alice", 1)

    async def bind(
        self, invocation: InvocationView, identity: ResolvedIdentity
    ) -> BindingView:
        self.bind_calls.append((invocation, identity))
        self.binding = BindingView("binding-secret-734", 918273, identity, True)
        return self.binding

    async def bindings(self, invocation: InvocationView) -> tuple[BindingView, ...]:
        self.list_calls.append(invocation)
        return (self.binding,)

    async def unbind(
        self, invocation: InvocationView, binding_id: str, *, expected_revision: int
    ) -> None:
        self.unbind_calls.append((invocation, binding_id, expected_revision))

    async def status(self, invocation: InvocationView) -> GrantReference | None:
        self.status_calls.append(invocation)
        return self.status_value


class _Subscriptions:
    def __init__(self) -> None:
        self.create_calls: list[tuple[InvocationView, SubscriptionRequest]] = []
        self.list_calls: list[InvocationView] = []
        self.cancel_calls: list[tuple[InvocationView, str, int]] = []
        self.current = SubscriptionView(
            "subscription-secret-281",
            654321,
            "owner-secret-681",
            None,
            "actor-secret-957",
            {"minimum": 87531, "region": "region-secret-531"},
        )

    async def create_request(
        self, invocation: InvocationView, request: SubscriptionRequest
    ) -> SubscriptionView:
        self.create_calls.append((invocation, request))
        self.current = SubscriptionView(
            "subscription-secret-281",
            654321,
            "owner-secret-681",
            None,
            "actor-secret-957",
            request.filters,
        )
        return self.current

    async def list_current(
        self, invocation: InvocationView
    ) -> tuple[SubscriptionView, ...]:
        self.list_calls.append(invocation)
        return (self.current,)

    async def cancel(
        self,
        invocation: InvocationView,
        subscription_id: str,
        *,
        expected_revision: int,
    ) -> None:
        self.cancel_calls.append((invocation, subscription_id, expected_revision))


class _Dependencies:
    def __init__(self) -> None:
        self.calls: list[tuple[InvocationView, object, object]] = []

    async def invoke(self, invocation, capability, parameters):
        self.calls.append((invocation, capability, parameters))
        return CapabilityResult(
            "stub-source-result",
            ResultStatus.SUCCESS,
            document=DisplayDocument("Stub source", "A dependency was called.", ()),
            model_facts=FactDocument({"source": "stub"}),
        )


class _Scopes:
    def __init__(self) -> None:
        self.dependencies = _Dependencies()
        self.bind_calls: list[InvocationView] = []

    async def bind(self, invocation: InvocationView):
        self.bind_calls.append(invocation)
        return self


def _services() -> tuple[ModuleServices, _Accounts, _Subscriptions, _Scopes]:
    accounts = _Accounts()
    subscriptions = _Subscriptions()
    scopes = _Scopes()
    return (
        ModuleServices(
            config=object(),
            identities=object(),
            accounts=accounts,
            subscriptions=subscriptions,
            scopes=scopes,
        ),
        accounts,
        subscriptions,
        scopes,
    )


def _command_view(
    capability_id: str, *, module_id: str = "offline_sample/status"
) -> InvocationView:
    return InvocationView(
        invocation_id=f"sample-{capability_id}",
        origin=InvocationOrigin.COMMAND,
        actor_id="alice",
        conversation_id="alice",
        module_id=module_id,
        module_epoch=1,
        registry_revision=1,
        adapter_id="sample-adapter",
        capability_id=capability_id,
    )


class OfflineSampleTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.package = parse_manifest(MANIFEST_PATH.read_bytes())
        cls.modules = {module.module_id: module for module in cls.package.modules}

    def test_manifest_has_real_dependencies_command_only_management_and_matching_jobs(
        self,
    ) -> None:
        self.assertEqual(set(self.modules), {"status", "source"})
        status = self.modules["status"]
        source = self.modules["source"]
        self.assertEqual(self.package.contract_version, "1.1.0")
        gate = next(
            field
            for field in status.config_fields
            if field.name == "sample_subscriptions_enabled"
        )
        self.assertIs(gate.default, True)
        self.assertFalse(gate.sensitive)
        self.assertFalse(gate.required)
        capabilities = {item.capability_id: item for item in status.capabilities}

        self.assertEqual(
            status.commands[0].operation_path,
            "status",
        )
        self.assertIn("sample_status", {item.name for item in status.tools})
        self.assertEqual(
            capabilities["from_source"].required_capabilities,
            (CapabilityReference("offline_sample/source", "read"),),
        )
        self.assertEqual(
            capabilities["status"].required_capabilities,
            (),
        )
        self.assertEqual(
            capabilities["configured_status"].required_config,
            ("region",),
        )
        command_capabilities = {item.capability_id for item in status.commands}
        tool_capabilities = {item.capability_id for item in status.tools}
        for command_only in (
            "account_bind",
            "account_list",
            "account_unbind",
            "subscription_create",
            "subscription_list",
            "subscription_cancel",
            "private_status",
        ):
            self.assertIn(command_only, command_capabilities)
            self.assertNotIn(command_only, tool_capabilities)
            self.assertEqual(
                capabilities[command_only].invocation_policy.value, "command_only"
            )
        for public_management in (
            "account_bind",
            "account_list",
            "account_unbind",
            "subscription_create",
            "subscription_list",
            "subscription_cancel",
        ):
            self.assertEqual(
                capabilities[public_management].privacy_floor.value, "public"
            )
        self.assertEqual(capabilities["private_status"].privacy_floor.value, "private")

        self.assertEqual(
            {schedule.collector_id for schedule in status.schedules},
            {"public_catalog", "private_catalog"},
        )
        scopes = {
            schedule.collector_id: schedule.shared_scope
            for schedule in status.schedules
        }
        self.assertIs(scopes["public_catalog"], OwnershipKind.PUBLIC)
        self.assertIs(scopes["private_catalog"], OwnershipKind.AUTHORIZED)
        subscriptions = {item.type_id: item for item in status.subscriptions}
        self.assertEqual(subscriptions["public_watch"].collector_id, "public_catalog")
        self.assertEqual(subscriptions["private_watch"].collector_id, "private_catalog")
        self.assertEqual(
            {subscription.matcher_id for subscription in status.subscriptions},
            {"sample_matcher"},
        )
        self.assertEqual({item.capability_id for item in source.capabilities}, {"read"})

    async def test_factories_handlers_and_health_match_the_static_contract(
        self,
    ) -> None:
        services, _accounts, _subscriptions, _scopes = _services()
        created = {
            "status": await Factory().create(services),
            "source": await SourceFactory().create(services),
        }
        for module_id, instance in created.items():
            handlers = instance.handlers()
            manifest = self.modules[module_id]
            self.assertEqual(
                set(handlers.capabilities),
                {item.capability_id for item in manifest.capabilities},
            )
            self.assertEqual(
                set(handlers.collectors),
                {item.collector_id for item in manifest.schedules},
            )
            self.assertEqual(
                set(handlers.evaluators),
                {item.matcher_id for item in manifest.subscriptions},
            )
            health = await instance.check_health()
            self.assertEqual(set(health.capabilities), set(handlers.capabilities))
            self.assertTrue(
                all(
                    item.status is HealthStatus.AVAILABLE
                    for item in health.capabilities.values()
                )
            )

    async def test_original_status_command_tool_and_declared_dependency_return_sdk_dtos(
        self,
    ) -> None:
        services, _accounts, _subscriptions, scopes = _services()
        instance = await Factory().create(services)
        handlers = instance.handlers()

        status = await handlers.capabilities["status"].invoke(
            _command_view("status"), {}
        )
        self.assertEqual(status.result_id, "offline-sample-status")
        self.assertEqual(status.status, ResultStatus.SUCCESS)
        self.assertEqual(status.model_facts.facts, {"sample": "offline", "ready": True})

        dependent = await handlers.capabilities["from_source"].invoke(
            _command_view("from_source"), {}
        )
        self.assertEqual(dependent.result_id, "stub-source-result")
        self.assertEqual(len(scopes.bind_calls), 1)
        self.assertEqual(
            scopes.dependencies.calls[0][1],
            CapabilityReference("offline_sample/source", "read"),
        )

    async def test_account_handlers_delegate_exactly_through_injected_operations(
        self,
    ) -> None:
        services, accounts, _subscriptions, _scopes = _services()
        instance = await Factory().create(services)
        handlers = instance.handlers().capabilities
        view = _command_view("account_bind")
        private_markers = (
            "alice",
            "provider-secret-381",
            "subject-secret-982",
            "binding-secret-734",
            "918273",
            "grant-alice",
        )

        bound = await handlers["account_bind"].invoke(
            view,
            {
                "provider": "provider-secret-381",
                "subject": "subject-secret-982",
            },
        )
        self.assertEqual(accounts.bind_calls[0][0], view)
        self.assertEqual(
            accounts.bind_calls[0][1],
            ResolvedIdentity(
                "provider-secret-381:subject-secret-982",
                "provider-secret-381",
                "subject-secret-982",
            ),
        )

        listed = await handlers["account_list"].invoke(
            _command_view("account_list"), {}
        )
        self.assertEqual(accounts.list_calls, [_command_view("account_list")])

        unbind_view = _command_view("account_unbind")
        removed = await handlers["account_unbind"].invoke(
            unbind_view,
            {"binding_id": "binding-secret-734", "expected_revision": 918273},
        )
        self.assertEqual(
            accounts.unbind_calls,
            [(unbind_view, "binding-secret-734", 918273)],
        )

        self.assertEqual(bound, listed)
        self.assertEqual(listed, removed)
        self._assert_public_management_receipt(
            bound, "offline-account-management", private_markers
        )

    async def test_subscription_handlers_build_sdk_request_and_delegate_crud(
        self,
    ) -> None:
        services, _accounts, subscriptions, _scopes = _services()
        instance = await Factory().create(services)
        handlers = instance.handlers().capabilities
        create_view = _command_view("subscription_create")
        created = await handlers["subscription_create"].invoke(
            create_view,
            {
                "type_id": "public_watch",
                "region": "global",
                "minimum": 5,
                "mode": "digest",
                "digest_time": "23:59",
            },
        )
        self.assertEqual(created.status, ResultStatus.SUCCESS)
        delegated_view, request = subscriptions.create_calls[0]
        self.assertIs(delegated_view, create_view)
        self.assertIsInstance(request, SubscriptionRequest)
        self.assertEqual(request.type_id, "public_watch")
        self.assertEqual(request.collector_parameters, {"region": "global"})
        self.assertEqual(request.filters, {"minimum": 5})
        self.assertEqual(request.notification_mode, "digest")
        self.assertEqual(request.digest_schedule.timezone_name, "UTC")
        self.assertEqual(request.digest_schedule.local_time, "23:59")

        instant_view = _command_view("subscription_create")
        instant = await handlers["subscription_create"].invoke(
            instant_view,
            {
                "type_id": "public_watch",
                "region": "region-secret-531",
                "minimum": 87531,
                "mode": "instant",
            },
        )
        self.assertIsNone(subscriptions.create_calls[-1][1].digest_schedule)

        list_view = _command_view("subscription_list")
        listed = await handlers["subscription_list"].invoke(list_view, {})
        self.assertEqual(subscriptions.list_calls, [list_view])

        cancel_view = _command_view("subscription_cancel")
        cancelled = await handlers["subscription_cancel"].invoke(
            cancel_view,
            {
                "subscription_id": "subscription-secret-281",
                "expected_revision": 654321,
            },
        )
        self.assertEqual(
            subscriptions.cancel_calls,
            [(cancel_view, "subscription-secret-281", 654321)],
        )
        self.assertEqual(created, instant)
        self.assertEqual(instant, listed)
        self.assertEqual(listed, cancelled)
        self._assert_public_management_receipt(
            created,
            "offline-subscription-management",
            (
                "alice",
                "subscription-secret-281",
                "654321",
                "owner-secret-681",
                "actor-secret-957",
                "region-secret-531",
                "87531",
                "digest",
                "23:59",
                "private_watch",
                "grant-alice",
            ),
        )

    async def test_management_service_errors_propagate_without_receipts(self) -> None:
        cases = (
            (
                "accounts",
                "bind",
                "account_bind",
                {"provider": "steam", "subject": "alice"},
            ),
            ("accounts", "bindings", "account_list", {}),
            (
                "accounts",
                "unbind",
                "account_unbind",
                {"binding_id": "binding-secret", "expected_revision": 2},
            ),
            (
                "subscriptions",
                "create_request",
                "subscription_create",
                {
                    "type_id": "public_watch",
                    "region": "global",
                    "minimum": 1,
                    "mode": "instant",
                },
            ),
            ("subscriptions", "list_current", "subscription_list", {}),
            (
                "subscriptions",
                "cancel",
                "subscription_cancel",
                {"subscription_id": "subscription-secret", "expected_revision": 2},
            ),
        )
        for service_name, method_name, capability_id, parameters in cases:
            with self.subTest(capability_id=capability_id):
                services, accounts, subscriptions, _scopes = _services()
                service = accounts if service_name == "accounts" else subscriptions
                failure = RuntimeError("management service failed")

                async def fail(*_args, **_kwargs):
                    raise failure

                setattr(service, method_name, fail)
                instance = await Factory().create(services)
                handler = instance.handlers().capabilities[capability_id]
                with self.assertRaises(RuntimeError) as raised:
                    await handler.invoke(_command_view(capability_id), parameters)
                self.assertIs(raised.exception, failure)

    def _assert_public_management_receipt(
        self,
        result: CapabilityResult,
        result_id: str,
        private_markers: tuple[str, ...],
    ) -> None:
        self.assertEqual(result.result_id, result_id)
        self.assertIs(result.status, ResultStatus.SUCCESS)
        self.assertIs(result.privacy, Privacy.PUBLIC)
        self.assertIsNotNone(result.document)
        self.assertIs(result.document.privacy, Privacy.PUBLIC)
        self.assertIsNone(result.model_facts)
        self.assertEqual(result.provenance, ())
        self.assertEqual(result.warnings, ())
        self.assertEqual(result.timestamps, ())
        self.assertEqual(result.document.sources, ())
        self.assertEqual(result.document.timestamps, ())
        self.assertEqual(
            result.document.title,
            "Sample account management"
            if result_id == "offline-account-management"
            else "Sample subscription management",
        )
        self.assertEqual(
            result.document.subject,
            "The sample account management request was processed."
            if result_id == "offline-account-management"
            else "The sample subscription management request was processed.",
        )
        self.assertEqual(
            result.document.ordered_blocks[0].text,
            "This offline sample returns a fixed public receipt.",
        )
        rendered = repr(result)
        for marker in private_markers:
            self.assertNotIn(marker, rendered)

    async def test_private_status_uses_account_service_and_returns_no_credential(
        self,
    ) -> None:
        services, accounts, _subscriptions, _scopes = _services()
        instance = await Factory().create(services)
        view = _command_view("private_status")
        result = (
            await instance.handlers().capabilities["private_status"].invoke(view, {})
        )
        self.assertEqual(accounts.status_calls, [view])
        self.assertEqual(result.status, ResultStatus.SUCCESS)
        self.assertIs(result.privacy, Privacy.PRIVATE)
        self.assertNotIn("grant-alice", result.document.ordered_blocks[0].text)
        self.assertIsNone(result.model_facts)

    async def test_collectors_and_matcher_keep_public_and_authorized_scope(
        self,
    ) -> None:
        services, _accounts, _subscriptions, _scopes = _services()
        instance = await Factory().create(services)
        handlers = instance.handlers()
        region = "global"
        public_parameters = NormalizedInput({"region": region})
        public_key = CollectionKey(
            "offline_sample/status",
            "public_catalog",
            1,
            "offline",
            public_parameters,
            OwnerScope(OwnershipKind.PUBLIC),
        )
        public_observation = await handlers.collectors["public_catalog"].collect(
            CollectionView(public_key), public_parameters, None
        )
        public_subscription = SubscriptionView(
            "sub-public", 1, "alice", None, "alice", {"minimum": 5}
        )
        public_decision = handlers.evaluators["sample_matcher"].evaluate(
            public_subscription, public_observation, None
        )
        self.assertEqual(
            public_observation.completeness, ObservationCompleteness.COMPLETE
        )
        self.assertEqual(public_observation.payload["value"], 7)
        self.assertTrue(public_decision.triggered)
        self.assertIs(public_decision.display_data.privacy, Privacy.PUBLIC)

        grant = GrantReference("grant-alice", 2)
        private_parameters = NormalizedInput({"region": region})
        private_key = CollectionKey(
            "offline_sample/status",
            "private_catalog",
            1,
            "offline",
            private_parameters,
            OwnerScope(OwnershipKind.AUTHORIZED, "alice", grant),
        )
        private_observation = await handlers.collectors["private_catalog"].collect(
            CollectionView(private_key), private_parameters, None
        )
        private_subscription = SubscriptionView(
            "sub-private", 1, "alice", grant, "alice", {"minimum": 5}
        )
        private_decision = handlers.evaluators["sample_matcher"].evaluate(
            private_subscription, private_observation, None
        )
        self.assertTrue(private_decision.triggered)
        self.assertIs(private_decision.display_data.privacy, Privacy.PRIVATE)
        self.assertEqual(private_observation.key.scope.grant, grant)


if __name__ == "__main__":
    unittest.main()
