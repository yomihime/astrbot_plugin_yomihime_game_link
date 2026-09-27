"""Deterministic SDK example used by the installed-artifact integration path."""

from __future__ import annotations

from datetime import UTC, datetime

from yomihime_sdk import (
    CapabilityHealth,
    CapabilityReference,
    CapabilityResult,
    CollectionView,
    DigestScheduleProfile,
    DisplayDocument,
    DstFoldPolicy,
    DstGapPolicy,
    ErrorCode,
    ErrorDetail,
    EvaluationDecision,
    EvaluationState,
    FactDocument,
    HealthReport,
    HealthStatus,
    InvocationView,
    JsonObject,
    ModuleHandlers,
    ModuleServices,
    NormalizedInput,
    Observation,
    ObservationCompleteness,
    OwnershipKind,
    Privacy,
    ResolvedIdentity,
    ResultStatus,
    SubscriptionRequest,
    SubscriptionView,
    TextBlock,
)

_OBSERVED_AT = datetime(2026, 1, 1, tzinfo=UTC)
_SOURCE_MODULE = "offline_sample/source"
_SOURCE_CAPABILITY = "read"
_SAMPLE_VALUE = 7


class Factory:
    """Factory for the command, Tool, account, subscription, and collector module."""

    async def create(self, services: ModuleServices) -> "OfflineSampleModule":
        return OfflineSampleModule(services)


class SourceFactory:
    """Factory for the independent public source capability module."""

    async def create(self, services: ModuleServices) -> "OfflineSourceModule":
        del services
        return OfflineSourceModule()


class OfflineSampleModule:
    def __init__(self, services: ModuleServices) -> None:
        self._services = services

    def handlers(self) -> ModuleHandlers:
        return ModuleHandlers(
            capabilities={
                "status": _StatusCapability(),
                "from_source": _DependencyCapability(self._services),
                "configured_status": _ConfiguredStatusCapability(),
                "account_bind": _AccountBindCapability(self._services),
                "account_list": _AccountListCapability(self._services),
                "account_unbind": _AccountUnbindCapability(self._services),
                "subscription_create": _SubscriptionCreateCapability(self._services),
                "subscription_list": _SubscriptionListCapability(self._services),
                "subscription_cancel": _SubscriptionCancelCapability(self._services),
                "private_status": _PrivateStatusCapability(self._services),
            },
            collectors={
                "public_catalog": _OfflineCollector(),
                "private_catalog": _OfflineCollector(),
            },
            evaluators={"sample_matcher": _SampleEvaluator()},
        )

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def check_health(self) -> HealthReport:
        # HealthResolver applies required_config/source/dependency availability
        # per capability after this module-level instance report.
        return HealthReport(
            capabilities={
                capability_id: CapabilityHealth(status=HealthStatus.AVAILABLE)
                for capability_id in self.handlers().capabilities
            }
        )


class OfflineSourceModule:
    def handlers(self) -> ModuleHandlers:
        return ModuleHandlers(
            capabilities={"read": _SourceReadCapability()},
            collectors={},
            evaluators={},
        )

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def check_health(self) -> HealthReport:
        return HealthReport(
            capabilities={"read": CapabilityHealth(status=HealthStatus.AVAILABLE)}
        )


def _result(
    result_id: str,
    title: str,
    subject: str,
    text: str,
    *,
    privacy: Privacy = Privacy.PUBLIC,
    facts: JsonObject | None = None,
) -> CapabilityResult:
    return CapabilityResult(
        result_id=result_id,
        status=ResultStatus.SUCCESS,
        document=DisplayDocument(
            title=title,
            subject=subject,
            ordered_blocks=(TextBlock(text),),
            privacy=privacy,
        ),
        model_facts=FactDocument(facts=facts) if facts is not None else None,
        privacy=privacy,
    )


def _account_management_receipt() -> CapabilityResult:
    return _result(
        "offline-account-management",
        "Sample account management",
        "The sample account management request was processed.",
        "This offline sample returns a fixed public receipt.",
    )


def _subscription_management_receipt() -> CapabilityResult:
    return _result(
        "offline-subscription-management",
        "Sample subscription management",
        "The sample subscription management request was processed.",
        "This offline sample returns a fixed public receipt.",
    )


class _StatusCapability:
    async def invoke(
        self, context: InvocationView, parameters: JsonObject
    ) -> CapabilityResult:
        del context, parameters
        return _result(
            "offline-sample-status",
            "Offline SDK sample",
            "The sample is ready.",
            "This result was created offline.",
            facts={"sample": "offline", "ready": True},
        )


class _SourceReadCapability:
    async def invoke(
        self, context: InvocationView, parameters: JsonObject
    ) -> CapabilityResult:
        del context, parameters
        return _result(
            "offline-source-read",
            "Offline sample source",
            "A fixed public sample value is available.",
            f"Sample value: {_SAMPLE_VALUE}.",
            facts={"source": "offline", "value": _SAMPLE_VALUE},
        )


class _DependencyCapability:
    def __init__(self, services: ModuleServices) -> None:
        self._services = services

    async def invoke(
        self, context: InvocationView, parameters: JsonObject
    ) -> CapabilityResult:
        del parameters
        scoped = await self._services.scopes.bind(context)
        return await scoped.dependencies.invoke(
            context,
            CapabilityReference(_SOURCE_MODULE, _SOURCE_CAPABILITY),
            {},
        )


class _ConfiguredStatusCapability:
    async def invoke(
        self, context: InvocationView, parameters: JsonObject
    ) -> CapabilityResult:
        del context, parameters
        return _result(
            "offline-configured-status",
            "Configured sample",
            "The required region is configured.",
            "Configuration is consumed by Core health resolution.",
            facts={"configured": True},
        )


class _AccountBindCapability:
    def __init__(self, services: ModuleServices) -> None:
        self._accounts = services.accounts

    async def invoke(
        self, context: InvocationView, parameters: JsonObject
    ) -> CapabilityResult:
        provider = str(parameters["provider"])
        subject = str(parameters["subject"])
        await self._accounts.bind(
            context,
            ResolvedIdentity(
                identity_id=f"{provider}:{subject}",
                provider=provider,
                subject=subject,
            ),
        )
        return _account_management_receipt()


class _AccountListCapability:
    def __init__(self, services: ModuleServices) -> None:
        self._accounts = services.accounts

    async def invoke(
        self, context: InvocationView, parameters: JsonObject
    ) -> CapabilityResult:
        del parameters
        await self._accounts.bindings(context)
        return _account_management_receipt()


class _AccountUnbindCapability:
    def __init__(self, services: ModuleServices) -> None:
        self._accounts = services.accounts

    async def invoke(
        self, context: InvocationView, parameters: JsonObject
    ) -> CapabilityResult:
        binding_id = str(parameters["binding_id"])
        revision = int(parameters["expected_revision"])
        await self._accounts.unbind(context, binding_id, expected_revision=revision)
        return _account_management_receipt()


class _SubscriptionCreateCapability:
    def __init__(self, services: ModuleServices) -> None:
        self._subscriptions = services.subscriptions

    async def invoke(
        self, context: InvocationView, parameters: JsonObject
    ) -> CapabilityResult:
        mode = str(parameters["mode"])
        digest_schedule = None
        if mode == "digest":
            digest_schedule = DigestScheduleProfile(
                timezone_name="UTC",
                local_time=str(parameters.get("digest_time", "23:59")),
                window_duration_seconds=3600,
                fold_policy=DstFoldPolicy.FIRST_OCCURRENCE,
                gap_policy=DstGapPolicy.SKIP,
            )
        request = SubscriptionRequest(
            type_id=str(parameters["type_id"]),
            collector_parameters={"region": str(parameters["region"])},
            filters={"minimum": int(parameters["minimum"])},
            notification_mode=mode,
            digest_schedule=digest_schedule,
        )
        await self._subscriptions.create_request(context, request)
        return _subscription_management_receipt()


class _SubscriptionListCapability:
    def __init__(self, services: ModuleServices) -> None:
        self._subscriptions = services.subscriptions

    async def invoke(
        self, context: InvocationView, parameters: JsonObject
    ) -> CapabilityResult:
        del parameters
        await self._subscriptions.list_current(context)
        return _subscription_management_receipt()


class _SubscriptionCancelCapability:
    def __init__(self, services: ModuleServices) -> None:
        self._subscriptions = services.subscriptions

    async def invoke(
        self, context: InvocationView, parameters: JsonObject
    ) -> CapabilityResult:
        subscription_id = str(parameters["subscription_id"])
        revision = int(parameters["expected_revision"])
        await self._subscriptions.cancel(
            context, subscription_id, expected_revision=revision
        )
        return _subscription_management_receipt()


class _PrivateStatusCapability:
    def __init__(self, services: ModuleServices) -> None:
        self._accounts = services.accounts

    async def invoke(
        self, context: InvocationView, parameters: JsonObject
    ) -> CapabilityResult:
        del parameters
        grant = await self._accounts.status(context)
        if grant is None:
            return CapabilityResult(
                result_id="offline-private-status-denied",
                status=ResultStatus.ERROR,
                error=ErrorDetail(
                    ErrorCode.AUTH_REQUIRED, "active authorization required"
                ),
                privacy=Privacy.PRIVATE,
            )
        return _result(
            "offline-private-status",
            "Private offline sample",
            "Authorized private data is available.",
            "This fixed private result contains no credential material.",
            privacy=Privacy.PRIVATE,
        )


class _OfflineCollector:
    def normalize(self, parameters: JsonObject) -> NormalizedInput:
        return NormalizedInput({"region": str(parameters["region"])})

    async def collect(
        self,
        context: CollectionView,
        parameters: NormalizedInput,
        previous: Observation | None,
    ) -> Observation:
        del previous
        region = str(parameters.values["region"])
        return Observation(
            observation_id=f"offline-{context.key.collector_id}-{region}",
            key=context.key,
            data_version=1,
            source_observed_at=_OBSERVED_AT,
            collected_at=_OBSERVED_AT,
            completeness=ObservationCompleteness.COMPLETE,
            covered_ids=(region,),
            payload={"region": region, "value": _SAMPLE_VALUE},
        )


class _SampleEvaluator:
    def evaluate(
        self,
        subscription: SubscriptionView,
        observation: Observation,
        previous_state: EvaluationState | None,
    ) -> EvaluationDecision:
        del previous_state
        value = int(observation.payload["value"])
        minimum = int(subscription.filters.get("minimum", 0))
        if value < minimum:
            return EvaluationDecision(state={"value": value}, triggered=False)

        private = observation.key.scope.kind is not OwnershipKind.PUBLIC
        privacy = Privacy.PRIVATE if private else Privacy.PUBLIC
        document = DisplayDocument(
            title="Offline sample update",
            subject="A deterministic observation matched.",
            ordered_blocks=(TextBlock(f"Sample value: {value}."),),
            sources=("offline sample",),
            privacy=privacy,
        )
        return EvaluationDecision(
            state={"value": value},
            triggered=True,
            event_key=f"sample-{subscription.subscription_id}-{observation.observation_id}",
            event_version=1,
            display_data=document,
        )


__all__ = ["Factory", "SourceFactory"]
