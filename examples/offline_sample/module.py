"""Deterministic SDK example used by the installed-artifact integration path."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from yomihime_game_link_sdk.contexts import InvocationView, MessageContext
from yomihime_game_link_sdk.declarations import CapabilityReference
from yomihime_game_link_sdk.display import DisplayDocument, Privacy, TextBlock
from yomihime_game_link_sdk.errors import (
    AccessDenied,
    OperationTimeout,
    RevisionConflict,
    ServiceUnavailable,
    UniqueConstraintViolation,
)
from yomihime_game_link_sdk.results import (
    CapabilityResult,
    ErrorCode,
    ErrorDetail,
    FactDocument,
    ResultStatus,
)
from yomihime_game_link_sdk.services import (
    CapabilityHealth,
    HealthReport,
    HealthStatus,
    ModuleHandlers,
    ModuleServices,
    ResolvedIdentity,
)
from yomihime_game_link_sdk.storage import (
    CacheLookupStatus,
    CacheQuery,
    JsonObject,
    OwnershipKind,
)
from yomihime_game_link_sdk.subscriptions import (
    CollectionView,
    DigestScheduleProfile,
    DstFoldPolicy,
    DstGapPolicy,
    EvaluationDecision,
    EvaluationState,
    NormalizedInput,
    Observation,
    ObservationCompleteness,
    SubscriptionRequest,
    SubscriptionView,
)

_OBSERVED_AT = datetime(2026, 1, 1, tzinfo=UTC)
_SOURCE_MODULE = "offline_sample/source"
_SOURCE_CAPABILITY = "read"
_SAMPLE_VALUE = 7


async def read_tool_message(
    services: ModuleServices, invocation: InvocationView
) -> MessageContext:
    """Read a current root Tool message through the public bound SDK port.

    The returned value is module input, not authority or public output. Call
    again after other awaits and before using it for effects or results.
    Commands, Web and nested invocations do not acquire a message here.
    """
    scope = await services.scopes.bind(invocation)
    return await scope.message.read()


def public_error_example() -> CapabilityResult:
    """Fixed public JSON error example, not a registered handler or authority."""
    error = ErrorDetail(
        ErrorCode.NO_RECORDS,
        "No matching public records; choose another source.",
    )
    return CapabilityResult(
        "offline-public-error",
        ResultStatus.ERROR,
        error=error,
        model_facts=FactDocument(
            {
                "status": "error",
                "error": {"code": error.code.value, "message": error.message},
                "supplement": {
                    "archive": {
                        "attempted_source": "offline",
                        "available": None,
                        "confidence": 0.25,
                    },
                    "recovery_hint": "Choose another public source.",
                },
            }
        ),
    )


class Factory:
    """Factory for the command, Tool, account, subscription, and collector module."""

    async def create(self, services: ModuleServices) -> "OfflineSampleModule":
        configuration = await services.config.current()
        instance = OfflineSampleModule(services)
        instance.factory_config_revision = configuration.revision
        return instance


class SourceFactory:
    """Factory for the independent public source capability module."""

    async def create(self, services: ModuleServices) -> "OfflineSourceModule":
        del services
        return OfflineSourceModule()


class OfflineSampleModule:
    def __init__(self, services: ModuleServices) -> None:
        self._services = services
        self.observations = {}
        self.last_scope = None
        self.hold_entered = asyncio.Event()
        self.hold_release = asyncio.Event()
        self.task_started = asyncio.Event()
        self.task_finished = asyncio.Event()
        self.start_count = 0
        self.stop_count = 0
        self.late_returns = 0

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
                "state": _StateCapability(self),
                "message": _MessageCapability(self),
                "public_error": _PublicErrorCapability(),
                "subscription_revise": _SubscriptionReviseCapability(self._services),
            },
            collectors={
                "public_catalog": _OfflineCollector(),
                "private_catalog": _OfflineCollector(),
            },
            evaluators={"sample_matcher": _SampleEvaluator()},
        )

    async def start(self) -> None:
        configuration = await self._services.config.current()
        self.start_config_revision = configuration.revision
        self.start_count += 1

    async def stop(self) -> None:
        configuration = await self._services.config.current()
        self.stop_config_revision = configuration.revision
        self.stop_count += 1

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
        self.last_view = None

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
        self.last_view = await self._subscriptions.create_request(context, request)
        return _subscription_management_receipt()


class _SubscriptionListCapability:
    def __init__(self, services: ModuleServices) -> None:
        self._subscriptions = services.subscriptions
        self.last_views = ()

    async def invoke(
        self, context: InvocationView, parameters: JsonObject
    ) -> CapabilityResult:
        del parameters
        self.last_views = await self._subscriptions.list_current(context)
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


class _SubscriptionReviseCapability:
    def __init__(self, services: ModuleServices) -> None:
        self._subscriptions = services.subscriptions
        self.last_view = None

    async def invoke(
        self, context: InvocationView, parameters: JsonObject
    ) -> CapabilityResult:
        self.last_view = await self._subscriptions.revise_request(
            context,
            SubscriptionRequest(
                type_id="public_watch",
                collector_parameters={"region": str(parameters["region"])},
                filters={"minimum": int(parameters["minimum"])},
                notification_mode="instant",
                subscription_id=str(parameters["subscription_id"]),
                expected_revision=int(parameters["expected_revision"]),
            ),
        )
        return _subscription_management_receipt()


class _StateCapability:
    """Small USER-owned notebook; receipts never expose notebook contents.

    Observations and finite barriers are local demonstration state. They are
    never authority and are not exported as public facts or SDK services.
    """

    def __init__(self, owner: OfflineSampleModule) -> None:
        self.owner = owner

    async def invoke(
        self, context: InvocationView, parameters: JsonObject
    ) -> CapabilityResult:
        owner = self.owner
        scope = await owner._services.scopes.bind(context)
        owner.last_scope = scope
        action = str(parameters["action"])
        try:
            config = await owner._services.config.current()
            notebook = await scope.records.collection("notebook")
            record = await notebook.get("note")
            if action == "write":
                value = {
                    "value": int(parameters.get("value", 7)),
                    "marker": "sample-private-note",
                }
                if record is None:
                    record = await notebook.create("note", value)
                else:
                    record = await notebook.replace(
                        "note", value, expected_revision=record.revision
                    )
                assert await notebook.get("note") == record
            owner.observations = {
                "region": config.values.get("region"),
                "record": record,
            }
            if action == "cache":
                entry = await scope.cache.put("note", {"value": 7}, ttl_seconds=600)
                lookup = await scope.cache.lookup(CacheQuery("note"))
                projection = await scope.cache.get("note")
                missing = await scope.cache.lookup(CacheQuery("absent"))
                assert (
                    lookup.status is CacheLookupStatus.HIT
                    and lookup.entry == entry == projection
                )
                assert (
                    missing.status is CacheLookupStatus.MISS
                    and await scope.cache.get("absent") is None
                )
                owner.observations.update(cache=entry, lookup=lookup, missing=missing)
                await scope.cache.put("expired", {"value": 0}, ttl_seconds=0.001)
                await asyncio.sleep(0.003)
                expired = await scope.cache.lookup(CacheQuery("expired"))
                assert (
                    expired.status is CacheLookupStatus.EXPIRED
                    and await scope.cache.get("expired") is None
                )
                owner.observations["expired"] = expired
            elif action == "read":
                owner.observations["cache"] = await scope.cache.lookup(
                    CacheQuery("note")
                )
            elif action == "command_message":
                await scope.message.read()
            elif action in {"hold", "late"}:
                owner.task_started.clear()
                owner.task_finished.clear()
                work_end = asyncio.Event()

                async def local_work():
                    owner.task_started.set()
                    try:
                        await work_end.wait()
                    finally:
                        owner.task_finished.set()

                scope.tasks.create_task(local_work(), name="offline-local-work")
                await owner.task_started.wait()
                owner.hold_entered.set()
                release_deadline = asyncio.get_running_loop().time() + 2.0
                cancellations = 0
                try:
                    while not owner.hold_release.is_set():
                        try:
                            remaining = (
                                release_deadline - asyncio.get_running_loop().time()
                            )
                            if remaining <= 0:
                                raise TimeoutError("sample barrier ended")
                            await asyncio.wait_for(owner.hold_release.wait(), remaining)
                        except asyncio.CancelledError:
                            cancellations += 1
                            if action != "late":
                                raise
                            if cancellations > 3:
                                raise
                finally:
                    # SDK work belongs to the module lifecycle. This finite
                    # demonstration also owns its per-call completion signal.
                    work_end.set()
                    await owner.task_finished.wait()
                if action == "late":
                    owner.late_returns += 1
            return _result(
                "offline-state",
                "Sample notebook",
                "The notebook request was processed.",
                "This fixed private receipt contains no notebook data.",
                privacy=Privacy.PRIVATE,
            )
        except (
            AccessDenied,
            RevisionConflict,
            ServiceUnavailable,
            OperationTimeout,
            UniqueConstraintViolation,
        ) as error:
            owner.observations["public_error_code"] = error.code
            return CapabilityResult(
                "offline-state-denied",
                ResultStatus.ERROR,
                error=ErrorDetail(
                    ErrorCode.MODULE_UNAVAILABLE, "The sample operation is unavailable."
                ),
                privacy=Privacy.PRIVATE,
            )


class _MessageCapability:
    def __init__(self, owner: OfflineSampleModule) -> None:
        self.owner = owner

    async def invoke(
        self, context: InvocationView, parameters: JsonObject
    ) -> CapabilityResult:
        scope = await self.owner._services.scopes.bind(context)
        self.owner.last_scope = scope
        message = await scope.message.read()
        self.owner.observations = {
            "message_read": bool(message.text),
            "message_type": type(message),
        }
        if parameters.get("hold", False):
            self.owner.hold_entered.set()
            await self.owner.hold_release.wait()
            await scope.message.read()
        return _result(
            "offline-message",
            "Sample message",
            "The current message was read.",
            "This fixed receipt contains no message text or correlation.",
        )


class _PublicErrorCapability:
    async def invoke(
        self, context: InvocationView, parameters: JsonObject
    ) -> CapabilityResult:
        del context, parameters
        return public_error_example()


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


__all__ = ["Factory", "SourceFactory", "read_tool_message", "public_error_example"]
