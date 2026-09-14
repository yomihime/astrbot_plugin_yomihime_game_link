"""Collection, observation, and subscription matching contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from math import isfinite
from typing import Mapping, Protocol

from .display import DisplayDocument, Privacy
from .storage import (
    GrantReference,
    JsonObject,
    JsonValue,
    OwnerScope,
    OwnershipKind,
    freeze_json,
)


def _identifier(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")


def _global_module_identifier(value: str) -> None:
    """Keep scheduling IDs globally qualified without importing context internals."""
    if not isinstance(value, str) or value.count("/") != 1:
        raise ValueError("module_id must be a global package/module identifier")
    package_id, module_id = value.split("/")
    _identifier(package_id, "package_id")
    _identifier(module_id, "module_id")


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class NormalizedInput:
    """Canonical, schema-validated collector parameters supplied by a module."""

    values: JsonObject

    def __post_init__(self) -> None:
        snapshot = freeze_json(self.values)
        if not isinstance(snapshot, Mapping):
            raise TypeError("normalized input must be a JSON object")
        object.__setattr__(self, "values", snapshot)


@dataclass(frozen=True, slots=True)
class CollectionKey:
    module_id: str
    collector_id: str
    key_version: int
    source_id: str
    parameters: NormalizedInput
    scope: OwnerScope

    def __post_init__(self) -> None:
        _global_module_identifier(self.module_id)
        for field in ("collector_id", "source_id"):
            _identifier(getattr(self, field), field)
        if (
            not isinstance(self.parameters, NormalizedInput)
            or not isinstance(self.scope, OwnerScope)
            or isinstance(self.key_version, bool)
            or not isinstance(self.key_version, int)
            or self.key_version < 1
        ):
            raise ValueError("key_version must be at least 1")


class ObservationCompleteness(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Observation:
    """An immutable collector snapshot; its payload is opaque to the runtime."""

    observation_id: str
    key: CollectionKey
    data_version: int
    source_observed_at: datetime | None
    collected_at: datetime
    completeness: ObservationCompleteness
    covered_ids: tuple[str, ...]
    payload: JsonObject

    def __post_init__(self) -> None:
        _identifier(self.observation_id, "observation_id")
        if (
            not isinstance(self.key, CollectionKey)
            or isinstance(self.data_version, bool)
            or not isinstance(self.data_version, int)
            or self.data_version < 1
        ):
            raise ValueError("data_version must be at least 1")
        _aware(self.collected_at, "collected_at")
        if self.source_observed_at is not None:
            _aware(self.source_observed_at, "source_observed_at")
        covered = tuple(self.covered_ids)
        if len(set(covered)) != len(covered):
            raise ValueError("covered_ids must not contain duplicates")
        for item in covered:
            _identifier(item, "covered_id")
        if not isinstance(self.completeness, ObservationCompleteness):
            raise TypeError(
                "observation completeness must be an ObservationCompleteness"
            )
        snapshot = freeze_json(self.payload)
        if not isinstance(snapshot, Mapping):
            raise TypeError("observation payload must be a JSON object")
        object.__setattr__(self, "covered_ids", covered)
        object.__setattr__(self, "payload", snapshot)


@dataclass(frozen=True, slots=True)
class SubscriptionView:
    """Read-only revision of one subscription visible to its matcher."""

    subscription_id: str
    revision: int
    owner_id: str
    grant: GrantReference | None
    notification_conversation_id: str
    filters: JsonObject

    def __post_init__(self) -> None:
        for field in ("subscription_id", "owner_id", "notification_conversation_id"):
            _identifier(getattr(self, field), field)
        if (
            not isinstance(self.grant, (GrantReference, type(None)))
            or isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 1
        ):
            raise ValueError("subscription revision must be at least 1")
        snapshot = freeze_json(self.filters)
        if not isinstance(snapshot, Mapping):
            raise TypeError("subscription filters must be a JSON object")
        object.__setattr__(self, "filters", snapshot)


@dataclass(frozen=True, slots=True)
class EvaluationState:
    revision: int
    value: JsonValue

    def __post_init__(self) -> None:
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 1
        ):
            raise ValueError("evaluation state revision must be at least 1")
        object.__setattr__(self, "value", freeze_json(self.value))


@dataclass(frozen=True, slots=True)
class EvaluationDecision:
    """Pure matcher output, committed atomically by the owning service."""

    state: JsonValue
    triggered: bool
    event_key: str | None = None
    event_version: int | None = None
    display_data: DisplayDocument | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", freeze_json(self.state))
        if not isinstance(self.triggered, bool):
            raise TypeError("triggered must be a bool")
        if self.triggered:
            _identifier(self.event_key or "", "event_key")
            if (
                isinstance(self.event_version, bool)
                or not isinstance(self.event_version, int)
                or self.event_version < 1
            ):
                raise ValueError("triggered decisions require event_version")
            if not isinstance(self.display_data, DisplayDocument):
                raise TypeError("triggered decisions require a display document")
        elif self.event_key is not None or self.event_version is not None:
            raise ValueError("non-triggered decisions cannot define an event")
        elif self.display_data is not None:
            raise TypeError("display_data must be a DisplayDocument")


@dataclass(frozen=True, slots=True)
class CollectionView:
    key: CollectionKey
    deadline_monotonic: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.key, CollectionKey):
            raise TypeError("collection view requires a CollectionKey")
        if self.deadline_monotonic is not None and (
            isinstance(self.deadline_monotonic, bool)
            or not isinstance(self.deadline_monotonic, (int, float))
            or not isfinite(self.deadline_monotonic)
            or self.deadline_monotonic <= 0
        ):
            raise ValueError("collection deadline must be a monotonic float")


class Collector(Protocol):
    def normalize(self, parameters: JsonObject) -> NormalizedInput: ...

    async def collect(
        self,
        context: CollectionView,
        parameters: NormalizedInput,
        previous: Observation | None,
    ) -> Observation: ...


class SubscriptionEvaluator(Protocol):
    """A synchronous pure matcher: it must not issue network or storage IO."""

    def evaluate(
        self,
        subscription: SubscriptionView,
        observation: Observation,
        previous_state: EvaluationState | None,
    ) -> EvaluationDecision: ...


@dataclass(frozen=True, slots=True)
class IntervalLimits:
    requested_seconds: float
    module_minimum_seconds: float
    runtime_minimum_seconds: float

    def __post_init__(self) -> None:
        values = (
            self.requested_seconds,
            self.module_minimum_seconds,
            self.runtime_minimum_seconds,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
            or value <= 0
            for value in values
        ):
            raise ValueError("intervals must be positive seconds")

    @property
    def target_seconds(self) -> float:
        return max(
            self.requested_seconds,
            self.module_minimum_seconds,
            self.runtime_minimum_seconds,
        )


@dataclass(frozen=True, slots=True)
class ScheduleDescriptor:
    collector_id: str
    key_version: int
    source_id: str
    data_version: int
    input_schema: JsonObject
    shared_scope: OwnershipKind
    trigger: "ScheduleTrigger"
    minimum_interval_seconds: float

    def __post_init__(self) -> None:
        for field in ("collector_id", "source_id"):
            _identifier(getattr(self, field), field)
        if (
            isinstance(self.key_version, bool)
            or not isinstance(self.key_version, int)
            or isinstance(self.data_version, bool)
            or not isinstance(self.data_version, int)
            or self.key_version < 1
            or self.data_version < 1
        ):
            raise ValueError("schedule versions must be positive")
        if not isinstance(self.shared_scope, OwnershipKind):
            raise TypeError("schedule shared scope must be an OwnershipKind")
        if not isinstance(self.trigger, ScheduleTrigger):
            raise TypeError("schedule trigger must be a ScheduleTrigger")
        if (
            isinstance(self.minimum_interval_seconds, bool)
            or not isinstance(self.minimum_interval_seconds, (int, float))
            or not isfinite(self.minimum_interval_seconds)
            or self.minimum_interval_seconds <= 0
        ):
            raise ValueError("minimum interval must be positive")
        snapshot = freeze_json(self.input_schema)
        if not isinstance(snapshot, Mapping):
            raise TypeError("schedule input_schema must be a JSON object")
        object.__setattr__(self, "input_schema", snapshot)


class ScheduleTrigger(str, Enum):
    PERIODIC = "periodic"
    ON_DEMAND = "on_demand"


@dataclass(frozen=True, slots=True)
class SubscriptionDescriptor:
    type_id: str
    collector_id: str
    matcher_id: str
    filter_schema: JsonObject
    notification_modes: tuple[str, ...]

    def __post_init__(self) -> None:
        _identifier(self.type_id, "type_id")
        _identifier(self.collector_id, "collector_id")
        _identifier(self.matcher_id, "matcher_id")
        modes = tuple(self.notification_modes)
        if not modes or len(modes) != len(set(modes)):
            raise ValueError("notification modes must be non-empty and unique")
        for mode in modes:
            _identifier(mode, "notification mode")
        object.__setattr__(self, "notification_modes", modes)
        snapshot = freeze_json(self.filter_schema)
        if not isinstance(snapshot, Mapping):
            raise TypeError("subscription filter_schema must be a JSON object")
        object.__setattr__(self, "filter_schema", snapshot)


def validate_evaluation_decision(
    subscription: SubscriptionView,
    observation: Observation,
    decision: EvaluationDecision,
) -> EvaluationDecision:
    """Reject delivery documents that widen the collected data's scope."""
    if not isinstance(subscription, SubscriptionView):
        raise TypeError("subscription must be a SubscriptionView")
    if not isinstance(observation, Observation):
        raise TypeError("observation must be an Observation")
    if not isinstance(decision, EvaluationDecision):
        raise TypeError("decision must be an EvaluationDecision")
    scope = observation.key.scope
    if scope.user_id is not None and subscription.owner_id != scope.user_id:
        raise ValueError("subscription owner cannot receive another user's observation")
    if scope.grant is not None and subscription.grant != scope.grant:
        raise ValueError("subscription grant must match an authorized observation")
    if not decision.triggered:
        return decision
    document = decision.display_data
    assert document is not None
    if (
        scope.kind is not OwnershipKind.PUBLIC
        and document.privacy is not Privacy.PRIVATE
    ):
        raise ValueError("private observations require private delivery documents")
    return decision
