"""Collection, observation, and subscription matching contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import Enum
from math import isfinite
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from yomihime_game_link_sdk.contexts import InvocationView as InvocationView
from yomihime_game_link_sdk.display import DigestMember as DigestMember
from yomihime_game_link_sdk.display import DisplayDocument as DisplayDocument
from yomihime_game_link_sdk.display import Privacy as Privacy
from yomihime_game_link_sdk.storage import GrantReference as GrantReference
from yomihime_game_link_sdk.storage import JsonObject as JsonObject
from yomihime_game_link_sdk.storage import JsonValue as JsonValue
from yomihime_game_link_sdk.storage import OwnerScope as OwnerScope
from yomihime_game_link_sdk.storage import OwnershipKind as OwnershipKind
from yomihime_game_link_sdk.subscriptions import CollectionKey as CollectionKey
from yomihime_game_link_sdk.subscriptions import CollectionView as CollectionView
from yomihime_game_link_sdk.subscriptions import Collector as Collector
from yomihime_game_link_sdk.subscriptions import ConversationKind as ConversationKind
from yomihime_game_link_sdk.subscriptions import ConversationRef as ConversationRef
from yomihime_game_link_sdk.subscriptions import (
    DigestScheduleProfile as DigestScheduleProfile,
)
from yomihime_game_link_sdk.subscriptions import DstFoldPolicy as DstFoldPolicy
from yomihime_game_link_sdk.subscriptions import DstGapPolicy as DstGapPolicy
from yomihime_game_link_sdk.subscriptions import (
    EvaluationDecision as EvaluationDecision,
)
from yomihime_game_link_sdk.subscriptions import EvaluationState as EvaluationState
from yomihime_game_link_sdk.subscriptions import IntervalLimits as IntervalLimits
from yomihime_game_link_sdk.subscriptions import NormalizedInput as NormalizedInput
from yomihime_game_link_sdk.subscriptions import Observation as Observation
from yomihime_game_link_sdk.subscriptions import (
    ObservationCompleteness as ObservationCompleteness,
)
from yomihime_game_link_sdk.subscriptions import (
    ScheduleDescriptor as ScheduleDescriptor,
)
from yomihime_game_link_sdk.subscriptions import ScheduleTrigger as ScheduleTrigger
from yomihime_game_link_sdk.subscriptions import (
    SubscriptionDescriptor as SubscriptionDescriptor,
)
from yomihime_game_link_sdk.subscriptions import (
    SubscriptionEvaluator as SubscriptionEvaluator,
)
from yomihime_game_link_sdk.subscriptions import (
    SubscriptionRequest as SubscriptionRequest,
)
from yomihime_game_link_sdk.subscriptions import SubscriptionView as SubscriptionView

from .schema import freeze_input_schema as freeze_input_schema
from .storage import freeze_json as freeze_json


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


def _positive_int(value: int, field: str) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field} must be a built-in positive integer")


def check_DigestScheduleProfile(self) -> None:
    _identifier(self.timezone_name, "timezone_name")
    try:
        ZoneInfo(self.timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("timezone_name must identify a valid timezone") from None
    if (
        not isinstance(self.local_time, str)
        or len(self.local_time) != 5
        or self.local_time[2] != ":"
        or (not self.local_time[:2].isdigit())
        or (not self.local_time[3:].isdigit())
        or (int(self.local_time[:2]) > 23)
        or (int(self.local_time[3:]) > 59)
    ):
        raise ValueError("local_time must use 24-hour HH:MM format")
    if (
        type(self.window_duration_seconds) is not int
        or not 1 <= self.window_duration_seconds <= 86400
    ):
        raise ValueError("window duration must be 1 to 86400 whole seconds")
    if not isinstance(self.fold_policy, DstFoldPolicy):
        raise TypeError("fold_policy must be a DstFoldPolicy")
    if not isinstance(self.gap_policy, DstGapPolicy):
        raise TypeError("gap_policy must be a DstGapPolicy")
    _positive_int(self.policy_revision, "policy_revision")


def check_SubscriptionRequest(self) -> None:
    _identifier(self.type_id, "type_id")
    _identifier(self.notification_mode, "notification_mode")
    if (self.subscription_id is None) != (self.expected_revision is None):
        raise ValueError(
            "subscription ID and expected revision must be supplied together"
        )
    if self.subscription_id is not None:
        _identifier(self.subscription_id, "subscription_id")
        _positive_int(self.expected_revision, "expected_revision")
    if self.digest_schedule is not None and (
        not isinstance(self.digest_schedule, DigestScheduleProfile)
    ):
        raise TypeError("digest_schedule must be a DigestScheduleProfile or None")
    parameters = freeze_json(self.collector_parameters)
    filters = freeze_json(self.filters)
    if not isinstance(parameters, Mapping) or not isinstance(filters, Mapping):
        raise TypeError("collector parameters and filters must be JSON objects")
    object.__setattr__(self, "collector_parameters", parameters)
    object.__setattr__(self, "filters", filters)


def check_NormalizedInput(self) -> None:
    snapshot = freeze_json(self.values)
    if not isinstance(snapshot, Mapping):
        raise TypeError("normalized input must be a JSON object")
    object.__setattr__(self, "values", snapshot)


def check_CollectionKey(self) -> None:
    _global_module_identifier(self.module_id)
    for field in ("collector_id", "source_id"):
        _identifier(getattr(self, field), field)
    if (
        not isinstance(self.parameters, NormalizedInput)
        or not isinstance(self.scope, OwnerScope)
        or type(self.key_version) is not int
        or (self.key_version < 1)
    ):
        raise ValueError("key_version must be at least 1")


def check_ConversationRef(self) -> None:
    for field in ("adapter_id", "conversation_id", "delivery_route"):
        value = getattr(self, field)
        if (
            not isinstance(value, str)
            or not value.strip()
            or any((character in value for character in ("/", "\\", "\n")))
        ):
            raise ValueError(f"{field} must be a bounded identifier")
    if not isinstance(self.kind, ConversationKind):
        raise TypeError("conversation kind must be a ConversationKind")


class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class SubscriptionRecord:
    """Persistable subscription identity and CAS version.

    Collection sharing is represented only by ``collection_key``. Per-owner
    filters and delivery preferences stay on the subscription record.
    """

    subscription_id: str
    revision: int
    module_id: str
    collection_key: CollectionKey
    owner_id: str
    grant: GrantReference | None
    recipient: ConversationRef
    notification_mode: str
    filters: JsonObject
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE
    type_id: str | None = None
    digest_schedule: DigestScheduleProfile | None = None

    def __post_init__(self) -> None:
        _identifier(self.subscription_id, "subscription_id")
        _identifier(self.owner_id, "owner_id")
        _identifier(self.notification_mode, "notification_mode")
        if self.type_id is not None:
            _identifier(self.type_id, "type_id")
        if self.digest_schedule is not None and (
            not isinstance(self.digest_schedule, DigestScheduleProfile)
        ):
            raise TypeError("digest_schedule must be a DigestScheduleProfile or None")
        _global_module_identifier(self.module_id)
        _positive_int(self.revision, "subscription revision")
        if not isinstance(self.collection_key, CollectionKey):
            raise TypeError("collection_key must be a CollectionKey")
        scope = self.collection_key.scope
        if self.collection_key.module_id != self.module_id:
            raise ValueError("subscription module must match collection key")
        if scope.user_id is not None and scope.user_id != self.owner_id:
            raise ValueError("private collection owner must match subscription owner")
        if scope.grant is not None and scope.grant != self.grant:
            raise ValueError("authorized subscription grant must match collection key")
        if scope.kind is OwnershipKind.PUBLIC and self.grant is not None:
            raise ValueError("public collection subscriptions cannot carry a grant")
        if scope.kind is OwnershipKind.USER and self.grant is not None:
            raise ValueError("user collection subscriptions cannot carry a grant")
        if not isinstance(self.grant, (GrantReference, type(None))):
            raise TypeError("grant must be a GrantReference or None")
        if not isinstance(self.recipient, ConversationRef):
            raise TypeError("recipient must be a ConversationRef")
        if (
            scope.kind is OwnershipKind.AUTHORIZED
            and self.recipient.kind is not ConversationKind.DIRECT
        ):
            raise ValueError("private_recipient_required")
        if not isinstance(self.status, SubscriptionStatus):
            raise TypeError("status must be a SubscriptionStatus")
        snapshot = freeze_json(self.filters)
        if not isinstance(snapshot, Mapping):
            raise TypeError("subscription filters must be a JSON object")
        object.__setattr__(self, "filters", snapshot)


@dataclass(frozen=True, slots=True)
class ActiveDigestSchedule:
    """Persisted active subscription eligible for restart schedule generation."""

    record: SubscriptionRecord

    def __post_init__(self) -> None:
        if not isinstance(self.record, SubscriptionRecord):
            raise TypeError("record must be a SubscriptionRecord")
        if (
            self.record.status is not SubscriptionStatus.ACTIVE
            or self.record.type_id is None
            or self.record.notification_mode != "digest"
            or (self.record.digest_schedule is None)
        ):
            raise ValueError("candidate must be an active typed digest subscription")

    @property
    def cursor(self) -> str:
        return self.record.subscription_id


@dataclass(frozen=True, slots=True)
class SubscriptionJobAssociation:
    subscription_id: str
    subscription_revision: int
    collection_key: CollectionKey
    cadence_seconds: float
    config_revision: int
    association_revision: int = 1

    def __post_init__(self) -> None:
        _identifier(self.subscription_id, "subscription_id")
        for name in (
            "subscription_revision",
            "config_revision",
            "association_revision",
        ):
            _positive_int(getattr(self, name), name)
        if not isinstance(self.collection_key, CollectionKey):
            raise TypeError("collection_key must be a CollectionKey")
        if (
            isinstance(self.cadence_seconds, bool)
            or not isinstance(self.cadence_seconds, (int, float))
            or (not isfinite(self.cadence_seconds))
            or (self.cadence_seconds <= 0)
        ):
            raise ValueError("cadence_seconds must be finite positive seconds")


class SubscriptionJobChangeKind(str, Enum):
    CREATE = "create"
    REVISE = "revise"
    CANCEL = "cancel"


@dataclass(frozen=True, slots=True)
class SubscriptionJobChange:
    """One atomic subscription and shared-job-link mutation command."""

    kind: SubscriptionJobChangeKind
    record: SubscriptionRecord
    association: SubscriptionJobAssociation | None
    expected_subscription_revision: int | None
    expected_association_revision: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SubscriptionJobChangeKind):
            raise TypeError("kind must be a SubscriptionJobChangeKind")
        if not isinstance(self.record, SubscriptionRecord):
            raise TypeError("record must be a SubscriptionRecord")
        if self.expected_subscription_revision is not None:
            _positive_int(
                self.expected_subscription_revision, "expected_subscription_revision"
            )
        if self.expected_association_revision is not None:
            _positive_int(
                self.expected_association_revision, "expected_association_revision"
            )
        association = self.association
        if association is not None and (
            not isinstance(association, SubscriptionJobAssociation)
        ):
            raise TypeError("association must be a SubscriptionJobAssociation or None")
        if self.kind is SubscriptionJobChangeKind.CREATE:
            if (
                self.record.status is not SubscriptionStatus.ACTIVE
                or self.record.revision != 1
                or self.expected_subscription_revision is not None
                or (self.expected_association_revision is not None)
                or (association is None)
                or (association.association_revision != 1)
            ):
                raise ValueError(
                    "create must insert revision-one subscription and link"
                )
        elif self.kind is SubscriptionJobChangeKind.REVISE:
            if (
                self.record.status is not SubscriptionStatus.ACTIVE
                or self.expected_subscription_revision is None
                or self.expected_association_revision is None
                or (self.record.revision != self.expected_subscription_revision + 1)
                or (association is None)
                or (
                    association.association_revision
                    != self.expected_association_revision + 1
                )
            ):
                raise ValueError("revise must advance both subscription and link CAS")
        elif (
            self.record.status is not SubscriptionStatus.CANCELLED
            or self.expected_subscription_revision is None
            or self.expected_association_revision is None
            or (self.record.revision != self.expected_subscription_revision + 1)
            or (association is not None)
        ):
            raise ValueError("cancel must advance subscription CAS and remove its link")
        if association is not None and (
            association.subscription_id != self.record.subscription_id
            or association.subscription_revision != self.record.revision
            or association.collection_key != self.record.collection_key
        ):
            raise ValueError("job association must match the resulting subscription")


@dataclass(frozen=True, slots=True)
class DigestMemberAssociation:
    """One digest member routed to a persisted UTC window and recipient."""

    window_id: str
    recipient: ConversationRef
    member: DigestMember
    event: DeliveryEvent

    def __post_init__(self) -> None:
        _identifier(self.window_id, "window_id")
        if not isinstance(self.recipient, ConversationRef):
            raise TypeError("recipient must be a ConversationRef")
        if not isinstance(self.member, DigestMember):
            raise TypeError("member must be a DigestMember")
        if not isinstance(self.event, DeliveryEvent):
            raise TypeError("event must be a DeliveryEvent carrying the saved document")
        if (
            self.member.event_key != self.event.event_key
            or self.member.event_version != self.event.event_version
            or self.member.subscription_id != self.event.subscription_id
            or (self.member.subscription_revision != self.event.subscription_revision)
            or (self.recipient != self.event.recipient)
        ):
            raise ValueError("digest member must exactly match its event and recipient")


@dataclass(frozen=True, slots=True)
class CadenceConfiguration:
    """Validated deployment cadence choices, with no implied product default."""

    allowed_seconds: tuple[float, ...]

    def __post_init__(self) -> None:
        values = tuple(self.allowed_seconds)
        if not values or any(
            (
                type(value) not in (int, float) or not isfinite(value) or value <= 0
                for value in values
            )
        ):
            raise ValueError("cadence choices must be finite positive seconds")
        if tuple(sorted(set(values))) != values:
            raise ValueError("cadence choices must be unique and ascending")
        object.__setattr__(self, "allowed_seconds", values)

    def validate_target(self, seconds: float) -> float:
        if (
            type(seconds) not in (int, float)
            or not isfinite(seconds)
            or seconds <= 0
            or (seconds not in self.allowed_seconds)
        ):
            raise ValueError("target cadence is not an allowed configured choice")
        return float(seconds)


@dataclass(frozen=True, slots=True)
class DigestWindow:
    """Persisted digest boundary; recovery reuses these exact UTC instants."""

    window_id: str
    timezone_name: str
    local_schedule_key: str
    utc_start: datetime
    utc_end: datetime
    due_at: datetime
    fold_policy: DstFoldPolicy
    gap_policy: DstGapPolicy
    members: tuple[DigestMember, ...]
    policy_revision: int = 1
    schedule_profile: DigestScheduleProfile | None = None
    schedule_recipient: ConversationRef | None = None

    def __post_init__(self) -> None:
        for name in ("window_id", "timezone_name", "local_schedule_key"):
            _identifier(getattr(self, name), name)
        try:
            ZoneInfo(self.timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("timezone_name must identify a valid timezone") from None
        for name in ("utc_start", "utc_end", "due_at"):
            value = getattr(self, name)
            _aware(value, name)
            if value.utcoffset().total_seconds() != 0:
                raise ValueError(f"{name} must be a UTC instant")
            object.__setattr__(self, name, value.astimezone(UTC))
        if not self.utc_start < self.utc_end or self.due_at < self.utc_end:
            raise ValueError("digest UTC window bounds and due time are inconsistent")
        if not isinstance(self.fold_policy, DstFoldPolicy):
            raise TypeError("fold_policy must be a DstFoldPolicy")
        if not isinstance(self.gap_policy, DstGapPolicy):
            raise TypeError("gap_policy must be a DstGapPolicy")
        _positive_int(self.policy_revision, "policy_revision")
        if (self.schedule_profile is None) != (self.schedule_recipient is None):
            raise ValueError("scheduled windows require both profile and recipient")
        if self.schedule_profile is not None and (
            not isinstance(self.schedule_profile, DigestScheduleProfile)
        ):
            raise TypeError("schedule_profile must be a DigestScheduleProfile")
        if self.schedule_recipient is not None and (
            not isinstance(self.schedule_recipient, ConversationRef)
        ):
            raise TypeError("schedule_recipient must be a ConversationRef")
        if self.schedule_profile is not None and (
            self.timezone_name != self.schedule_profile.timezone_name
            or self.fold_policy is not self.schedule_profile.fold_policy
            or self.gap_policy is not self.schedule_profile.gap_policy
            or (self.policy_revision != self.schedule_profile.policy_revision)
        ):
            raise ValueError("window DST fields must match its saved schedule profile")
        if self.schedule_profile is not None:
            try:
                date.fromisoformat(self.local_schedule_key)
            except ValueError:
                raise ValueError(
                    "scheduled local_schedule_key must be an ISO local date"
                ) from None
            expected_duration = self.schedule_profile.window_duration_seconds
            if (
                self.due_at != self.utc_end
                or (self.utc_end - self.utc_start).total_seconds() != expected_duration
            ):
                raise ValueError(
                    "scheduled UTC bounds must match saved elapsed duration and due boundary"
                )
        members = tuple(self.members)
        if any((not isinstance(item, DigestMember) for item in members)):
            raise TypeError("digest members must be DigestMember values")
        if len(set(members)) != len(members):
            raise ValueError("digest members must be unique")
        object.__setattr__(self, "members", members)


@dataclass(frozen=True, slots=True)
class DigestWindowSelector:
    """Select a persisted recipient/profile window covering one UTC event time."""

    profile: DigestScheduleProfile
    recipient: ConversationRef
    event_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.profile, DigestScheduleProfile):
            raise TypeError("profile must be a DigestScheduleProfile")
        if not isinstance(self.recipient, ConversationRef):
            raise TypeError("recipient must be a ConversationRef")
        _aware(self.event_at, "event_at")
        if self.event_at.utcoffset().total_seconds() != 0:
            raise ValueError("event_at must be a UTC instant")
        object.__setattr__(self, "event_at", self.event_at.astimezone(UTC))


@dataclass(frozen=True, slots=True)
class DueJobCursor:
    due_at: datetime
    job_key: str

    def __post_init__(self) -> None:
        _aware(self.due_at, "due_at")
        if self.due_at.utcoffset().total_seconds() != 0:
            raise ValueError("due_at must be a UTC instant")
        object.__setattr__(self, "due_at", self.due_at.astimezone(UTC))
        _identifier(self.job_key, "job_key")


@dataclass(frozen=True, slots=True)
class DigestRouteCursor:
    due_at: datetime
    window_id: str
    recipient_key: str

    def __post_init__(self) -> None:
        _aware(self.due_at, "due_at")
        if self.due_at.utcoffset().total_seconds() != 0:
            raise ValueError("due_at must be a UTC instant")
        object.__setattr__(self, "due_at", self.due_at.astimezone(UTC))
        _identifier(self.window_id, "window_id")
        _identifier(self.recipient_key, "recipient_key")


@dataclass(frozen=True, slots=True)
class DeliveryEventCursor:
    event_key: str
    event_version: int
    subscription_id: str
    subscription_revision: int

    def __post_init__(self) -> None:
        _identifier(self.event_key, "event_key")
        _positive_int(self.event_version, "event_version")
        _identifier(self.subscription_id, "subscription_id")
        _positive_int(self.subscription_revision, "subscription_revision")


@dataclass(frozen=True, slots=True)
class DigestRouteCandidate:
    """A due persisted window and exact recipient recovered from its members."""

    window_id: str
    recipient: ConversationRef
    due_at: datetime
    cursor_key: str

    def __post_init__(self) -> None:
        _identifier(self.window_id, "window_id")
        if not isinstance(self.recipient, ConversationRef):
            raise TypeError("recipient must be a ConversationRef")
        _identifier(self.cursor_key, "recipient_key")
        _aware(self.due_at, "due_at")
        if self.due_at.utcoffset().total_seconds() != 0:
            raise ValueError("due_at must be a UTC instant")
        object.__setattr__(self, "due_at", self.due_at.astimezone(UTC))

    @property
    def cursor(self) -> DigestRouteCursor:
        return DigestRouteCursor(self.due_at, self.window_id, self.cursor_key)


@dataclass(frozen=True, slots=True)
class DueCollectionJob:
    """Persisted due-job candidate; live module epoch is resolved by scheduler."""

    key: CollectionKey
    due_at: datetime
    cadence_seconds: float
    config_revision: int
    cursor_key: str

    def __post_init__(self) -> None:
        if not isinstance(self.key, CollectionKey):
            raise TypeError("key must be a CollectionKey")
        _identifier(self.cursor_key, "job_key")
        _aware(self.due_at, "due_at")
        if self.due_at.utcoffset().total_seconds() != 0:
            raise ValueError("due_at must be a UTC instant")
        object.__setattr__(self, "due_at", self.due_at.astimezone(UTC))
        if (
            isinstance(self.cadence_seconds, bool)
            or not isinstance(self.cadence_seconds, (int, float))
            or (not isfinite(self.cadence_seconds))
            or (self.cadence_seconds <= 0)
        ):
            raise ValueError("cadence_seconds must be finite positive seconds")
        _positive_int(self.config_revision, "config_revision")

    @property
    def cursor(self) -> DueJobCursor:
        return DueJobCursor(self.due_at, self.cursor_key)


class DigestMemberDisposition(str, Enum):
    INCLUDED = "included"
    CANCELLED = "cancelled"
    STALE_REVISION = "stale_revision"
    UNAUTHORIZED = "unauthorized"
    MODULE_UNAVAILABLE = "module_unavailable"


@dataclass(frozen=True, slots=True)
class DigestMemberReceipt:
    member: DigestMember
    disposition: DigestMemberDisposition

    def __post_init__(self) -> None:
        if not isinstance(self.member, DigestMember):
            raise TypeError("receipt member must be a DigestMember")
        if not isinstance(self.disposition, DigestMemberDisposition):
            raise TypeError("disposition must be a DigestMemberDisposition")


class DigestEnvelopeState(str, Enum):
    READY = "ready"
    CLAIMED = "claimed"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"


def digest_envelope_idempotency_key(window_id: str, recipient: ConversationRef) -> str:
    """Stable attempt-ledger key for one digest envelope route."""
    _identifier(window_id, "window_id")
    if not isinstance(recipient, ConversationRef):
        raise TypeError("recipient must be a ConversationRef")
    canonical = json.dumps(
        [
            "digest-envelope:v1",
            window_id,
            recipient.adapter_id,
            recipient.kind.value,
            recipient.conversation_id,
            recipient.delivery_route,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return "digest:v1:" + hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True, slots=True)
class DigestEnvelope:
    envelope_id: str
    window_id: str
    recipient: ConversationRef
    members: tuple[DigestMember, ...]
    revision: int = 1
    state: DigestEnvelopeState = DigestEnvelopeState.READY
    member_receipts: tuple[DigestMemberReceipt, ...] = ()
    member_associations: tuple[DigestMemberAssociation, ...] = ()
    delivery_attempts: tuple[DeliveryAttempt, ...] = ()
    claim_token: str | None = None
    claimed_at: datetime | None = None
    claim_expires_at: datetime | None = None
    retry_at: datetime | None = None

    def __post_init__(self) -> None:
        _identifier(self.envelope_id, "envelope_id")
        _identifier(self.window_id, "window_id")
        if not isinstance(self.recipient, ConversationRef):
            raise TypeError("recipient must be a ConversationRef")
        _positive_int(self.revision, "envelope revision")
        if not isinstance(self.state, DigestEnvelopeState):
            raise TypeError("state must be a DigestEnvelopeState")
        members = tuple(self.members)
        if any((not isinstance(item, DigestMember) for item in members)):
            raise TypeError("digest envelope members must be DigestMember values")
        if not members and self.state is not DigestEnvelopeState.CANCELLED:
            raise ValueError("ready digest envelopes require members")
        if len(set(members)) != len(members):
            raise ValueError("digest envelope members must be unique")
        associations = tuple(self.member_associations)
        if any(
            (not isinstance(item, DigestMemberAssociation) for item in associations)
        ):
            raise TypeError(
                "member_associations must contain DigestMemberAssociation values"
            )
        if any(
            (
                item.window_id != self.window_id or item.recipient != self.recipient
                for item in associations
            )
        ):
            raise ValueError(
                "envelope associations must match its window and recipient"
            )
        if len({item.member for item in associations}) != len(associations):
            raise ValueError("envelope member associations must be unique")
        if members != tuple((item.member for item in associations)):
            raise ValueError(
                "envelope must persist ordered event/documents for every member"
            )
        receipts = tuple(self.member_receipts)
        if any((not isinstance(item, DigestMemberReceipt) for item in receipts)):
            raise TypeError("member_receipts must be DigestMemberReceipt values")
        if len({item.member for item in receipts}) != len(receipts):
            raise ValueError("digest member receipts must be unique")
        attempts = tuple(self.delivery_attempts)
        if any((not isinstance(item, DeliveryAttempt) for item in attempts)):
            raise TypeError("delivery_attempts must contain DeliveryAttempt values")
        if self.retry_at is not None:
            _aware(self.retry_at, "retry_at")
            if self.retry_at.utcoffset().total_seconds() != 0:
                raise ValueError("retry_at must be a UTC instant")
            if self.state is not DigestEnvelopeState.FAILED:
                raise ValueError("only FAILED envelopes can have retry_at")
            object.__setattr__(self, "retry_at", self.retry_at.astimezone(UTC))
        expected_key = digest_envelope_idempotency_key(self.window_id, self.recipient)
        if any(
            (
                item.attempt_number != index or item.idempotency_key != expected_key
                for index, item in enumerate(attempts, start=1)
            )
        ):
            raise ValueError(
                "digest attempt ledger must be sequential and envelope-scoped"
            )
        claim_fields = (self.claim_token, self.claimed_at, self.claim_expires_at)
        has_claim = all((value is not None for value in claim_fields))
        if any((value is not None for value in claim_fields)) and (not has_claim):
            raise ValueError("digest claim token and UTC lease fields are all required")
        if has_claim:
            _identifier(self.claim_token or "", "claim_token")
            _aware(self.claimed_at, "claimed_at")
            _aware(self.claim_expires_at, "claim_expires_at")
            if (
                self.claimed_at.utcoffset().total_seconds() != 0
                or self.claim_expires_at.utcoffset().total_seconds() != 0
                or self.claim_expires_at <= self.claimed_at
            ):
                raise ValueError("digest claim must have an ordered UTC lease")
            object.__setattr__(self, "claimed_at", self.claimed_at.astimezone(UTC))
            object.__setattr__(
                self, "claim_expires_at", self.claim_expires_at.astimezone(UTC)
            )
        if (
            self.state in (DigestEnvelopeState.CLAIMED, DigestEnvelopeState.SENDING)
        ) != has_claim:
            raise ValueError("only claimed or sending envelopes retain a live claim")
        latest_state = attempts[-1].state if attempts else None
        expected_attempt_state = {
            DigestEnvelopeState.SENDING: DeliveryState.SENDING,
            DigestEnvelopeState.SENT: DeliveryState.SENT,
            DigestEnvelopeState.FAILED: DeliveryState.FAILED,
            DigestEnvelopeState.UNKNOWN: DeliveryState.UNKNOWN,
        }.get(self.state)
        if (
            expected_attempt_state is not None
            and latest_state is not expected_attempt_state
        ):
            raise ValueError("envelope state must match its latest delivery attempt")
        if self.state in (
            DigestEnvelopeState.READY,
            DigestEnvelopeState.CLAIMED,
        ) and latest_state not in (None, DeliveryState.FAILED):
            raise ValueError("only never-sent or known-failed envelopes may be claimed")
        if self.state is DigestEnvelopeState.CANCELLED and latest_state in (
            DeliveryState.SENDING,
            DeliveryState.UNKNOWN,
        ):
            raise ValueError("uncertain digest sends cannot be cancelled")
        object.__setattr__(self, "members", members)
        object.__setattr__(self, "member_associations", associations)
        object.__setattr__(self, "member_receipts", receipts)
        object.__setattr__(self, "delivery_attempts", attempts)


@dataclass(frozen=True, slots=True)
class DigestEnvelopeClaim:
    claim_token: str
    envelope: DigestEnvelope
    claimed_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        _identifier(self.claim_token, "claim_token")
        if not isinstance(self.envelope, DigestEnvelope):
            raise TypeError("envelope must be a DigestEnvelope")
        if self.envelope.state not in (
            DigestEnvelopeState.CLAIMED,
            DigestEnvelopeState.SENDING,
        ):
            raise ValueError("claim must reference a claimed or sending envelope")
        if self.envelope.claim_token != self.claim_token:
            raise ValueError("claim token must match the persisted envelope claim")
        _aware(self.claimed_at, "claimed_at")
        _aware(self.expires_at, "expires_at")
        if (
            self.claimed_at.utcoffset().total_seconds() != 0
            or self.expires_at.utcoffset().total_seconds() != 0
            or self.envelope.claimed_at != self.claimed_at.astimezone(UTC)
            or (self.envelope.claim_expires_at != self.expires_at.astimezone(UTC))
        ):
            raise ValueError("claim timestamps must match the persisted UTC lease")
        if self.expires_at <= self.claimed_at:
            raise ValueError("claim expiry must follow claim time")
        object.__setattr__(self, "claimed_at", self.claimed_at.astimezone(UTC))
        object.__setattr__(self, "expires_at", self.expires_at.astimezone(UTC))


class DeliveryState(str, Enum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"


def delivery_idempotency_key(
    event_key: str,
    event_version: int,
    subscription_id: str,
    subscription_revision: int,
    recipient: ConversationRef,
) -> str:
    """Stable v1 key scoped to event, subscription revision, and exact route."""
    _identifier(event_key, "event_key")
    _identifier(subscription_id, "subscription_id")
    for name, value in (
        ("event_version", event_version),
        ("subscription_revision", subscription_revision),
    ):
        _positive_int(value, name)
    if not isinstance(recipient, ConversationRef):
        raise TypeError("recipient must be a ConversationRef")
    canonical = json.dumps(
        [
            event_key,
            event_version,
            subscription_id,
            subscription_revision,
            recipient.adapter_id,
            recipient.kind.value,
            recipient.conversation_id,
            recipient.delivery_route,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return "delivery:v1:" + hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True, slots=True)
class DeliveryAttempt:
    attempt_number: int
    state: DeliveryState
    idempotency_key: str
    started_at: datetime
    completed_at: datetime | None = None
    error_code: str | None = None
    platform_message_id: str | None = None

    def __post_init__(self) -> None:
        _positive_int(self.attempt_number, "attempt_number")
        if not isinstance(self.state, DeliveryState):
            raise TypeError("state must be a DeliveryState")
        _identifier(self.idempotency_key, "idempotency_key")
        _aware(self.started_at, "started_at")
        if self.completed_at is not None:
            _aware(self.completed_at, "completed_at")
        if (
            self.state
            in (
                DeliveryState.SENT,
                DeliveryState.FAILED,
                DeliveryState.UNKNOWN,
                DeliveryState.CANCELLED,
            )
            and self.completed_at is None
        ):
            raise ValueError("terminal delivery attempts require completed_at")
        if self.state is DeliveryState.SENDING and self.completed_at is not None:
            raise ValueError("sending attempts cannot have completed_at")
        if self.error_code is not None:
            _identifier(self.error_code, "error_code")
        if self.platform_message_id is not None:
            _identifier(self.platform_message_id, "platform_message_id")
        if self.state is DeliveryState.UNKNOWN and self.error_code is not None:
            raise ValueError("unknown delivery cannot be represented as a failure")


@dataclass(frozen=True, slots=True)
class DeliveryEvent:
    event_key: str
    event_version: int
    subscription_id: str
    subscription_revision: int
    owner_id: str
    grant: GrantReference | None
    recipient: ConversationRef
    display_data: DisplayDocument
    idempotency_key: str
    state: DeliveryState = DeliveryState.PENDING
    attempt: DeliveryAttempt | None = None
    retry_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("event_key", "subscription_id", "owner_id", "idempotency_key"):
            _identifier(getattr(self, name), name)
        for name in ("event_version", "subscription_revision"):
            _positive_int(getattr(self, name), name)
        if not isinstance(self.grant, (GrantReference, type(None))):
            raise TypeError("grant must be a GrantReference or None")
        if not isinstance(self.recipient, ConversationRef):
            raise TypeError("recipient must be a ConversationRef")
        if not isinstance(self.display_data, DisplayDocument):
            raise TypeError("display_data must be a DisplayDocument")
        if self.grant is not None and (
            self.recipient.kind is not ConversationKind.DIRECT
            or self.display_data.privacy is not Privacy.PRIVATE
        ):
            raise ValueError("authorized delivery requires a private direct recipient")
        if not isinstance(self.state, DeliveryState):
            raise TypeError("state must be a DeliveryState")
        if self.attempt is not None and (not isinstance(self.attempt, DeliveryAttempt)):
            raise TypeError("attempt must be a DeliveryAttempt or None")
        if self.idempotency_key != delivery_idempotency_key(
            self.event_key,
            self.event_version,
            self.subscription_id,
            self.subscription_revision,
            self.recipient,
        ):
            raise ValueError("delivery idempotency key does not match its scope")
        if (
            self.attempt is not None
            and self.attempt.idempotency_key != self.idempotency_key
        ):
            raise ValueError("attempt idempotency key must match its delivery event")
        if self.attempt is not None and self.attempt.state is not self.state:
            raise ValueError("event state must match its latest attempt state")
        if self.state is DeliveryState.UNKNOWN and self.attempt is None:
            raise ValueError("unknown delivery requires its reconciliation attempt")
        if self.retry_at is not None:
            _aware(self.retry_at, "retry_at")
            if self.retry_at.utcoffset().total_seconds() != 0:
                raise ValueError("retry_at must be a UTC instant")
            if self.state is not DeliveryState.FAILED:
                raise ValueError("only known FAILED deliveries can have retry_at")
            object.__setattr__(self, "retry_at", self.retry_at.astimezone(UTC))

    @property
    def cursor(self) -> DeliveryEventCursor:
        return DeliveryEventCursor(
            self.event_key,
            self.event_version,
            self.subscription_id,
            self.subscription_revision,
        )


def check_Observation(self) -> None:
    _identifier(self.observation_id, "observation_id")
    if (
        not isinstance(self.key, CollectionKey)
        or type(self.data_version) is not int
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
        raise TypeError("observation completeness must be an ObservationCompleteness")
    if self.completeness is ObservationCompleteness.PARTIAL and (not covered):
        raise ValueError("partial observations require explicit covered_ids")
    if self.completeness is ObservationCompleteness.FAILED and covered:
        raise ValueError("failed observations cannot claim covered_ids")
    snapshot = freeze_json(self.payload)
    if not isinstance(snapshot, Mapping):
        raise TypeError("observation payload must be a JSON object")
    object.__setattr__(self, "covered_ids", covered)
    object.__setattr__(self, "payload", snapshot)


def check_SubscriptionView(self) -> None:
    for field in ("subscription_id", "owner_id", "notification_conversation_id"):
        _identifier(getattr(self, field), field)
    if (
        not isinstance(self.grant, (GrantReference, type(None)))
        or type(self.revision) is not int
        or self.revision < 1
    ):
        raise ValueError("subscription revision must be at least 1")
    snapshot = freeze_json(self.filters)
    if not isinstance(snapshot, Mapping):
        raise TypeError("subscription filters must be a JSON object")
    object.__setattr__(self, "filters", snapshot)


def check_EvaluationState(self) -> None:
    _positive_int(self.revision, "evaluation state revision")
    object.__setattr__(self, "value", freeze_json(self.value))


@dataclass(frozen=True, slots=True)
class ObservationCursor:
    observation_id: str
    data_version: int
    completeness: ObservationCompleteness
    covered_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _identifier(self.observation_id, "observation_id")
        _positive_int(self.data_version, "data_version")
        if not isinstance(self.completeness, ObservationCompleteness):
            raise TypeError("completeness must be an ObservationCompleteness")
        covered = tuple(self.covered_ids)
        for item in covered:
            _identifier(item, "covered_id")
        if len(set(covered)) != len(covered):
            raise ValueError("covered_ids must not contain duplicates")
        if self.completeness is ObservationCompleteness.PARTIAL and (not covered):
            raise ValueError("partial cursors require explicit covered_ids")
        if self.completeness is ObservationCompleteness.FAILED and covered:
            raise ValueError("failed cursors cannot claim covered_ids")
        object.__setattr__(self, "covered_ids", covered)


@dataclass(frozen=True, slots=True)
class SubscriptionEvaluationSnapshot:
    """Authoritative subscription-scoped inputs for one pure evaluation."""

    subscription: SubscriptionRecord
    state: EvaluationState | None
    cursor: ObservationCursor | None
    observation: Observation | None

    def __post_init__(self) -> None:
        if not isinstance(self.subscription, SubscriptionRecord):
            raise TypeError("subscription must be a SubscriptionRecord")
        if not isinstance(self.state, (EvaluationState, type(None))):
            raise TypeError("state must be an EvaluationState or None")
        if not isinstance(self.cursor, (ObservationCursor, type(None))):
            raise TypeError("cursor must be an ObservationCursor or None")
        if not isinstance(self.observation, (Observation, type(None))):
            raise TypeError("observation must be an Observation or None")
        if (self.cursor is None) != (self.observation is None):
            raise ValueError("cursor and last observation must be present together")
        if self.observation is not None and self.cursor is not None:
            if self.observation.key != self.subscription.collection_key:
                raise ValueError(
                    "last observation must match the subscription full key"
                )
            if (
                self.cursor.observation_id != self.observation.observation_id
                or self.cursor.data_version != self.observation.data_version
                or self.cursor.completeness is not self.observation.completeness
                or (self.cursor.covered_ids != self.observation.covered_ids)
            ):
                raise ValueError("cursor must describe the last observation")


@dataclass(frozen=True, slots=True)
class SubscriptionEvaluationCommit:
    subscription_id: str
    subscription_revision: int
    expected_state_revision: int | None
    state: EvaluationState
    cursor: ObservationCursor
    delivery_events: tuple[DeliveryEvent, ...] = ()
    digest_members: tuple[DigestMemberAssociation, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.subscription_id, "subscription_id")
        _positive_int(self.subscription_revision, "subscription_revision")
        if self.expected_state_revision is not None:
            _positive_int(self.expected_state_revision, "expected_state_revision")
        if not isinstance(self.state, EvaluationState):
            raise TypeError("state must be an EvaluationState")
        expected_state_version = (
            1
            if self.expected_state_revision is None
            else self.expected_state_revision + 1
        )
        if self.state.revision != expected_state_version:
            raise ValueError("evaluation state must advance exactly one revision")
        if not isinstance(self.cursor, ObservationCursor):
            raise TypeError("cursor must be an ObservationCursor")
        events = tuple(self.delivery_events)
        if any(
            (
                not isinstance(item, DeliveryEvent)
                or item.subscription_id != self.subscription_id
                or item.subscription_revision != self.subscription_revision
                for item in events
            )
        ):
            raise ValueError("delivery events must match this subscription revision")
        if len({(item.event_key, item.event_version) for item in events}) != len(
            events
        ):
            raise ValueError("delivery events must be unique")
        members = tuple(self.digest_members)
        if any(
            (
                not isinstance(item, DigestMemberAssociation)
                or item.member.subscription_id != self.subscription_id
                or item.member.subscription_revision != self.subscription_revision
                for item in members
            )
        ):
            raise ValueError(
                "digest associations must match this subscription revision"
            )
        if any((item.event not in events for item in members)):
            raise ValueError(
                "every digest member requires its exact event in this atomic commit"
            )
        if len({item.member for item in members}) != len(members):
            raise ValueError("digest members must be unique")
        object.__setattr__(self, "delivery_events", events)
        object.__setattr__(self, "digest_members", members)


@dataclass(frozen=True, slots=True)
class ObservationEvaluationCommit:
    observation: Observation
    subscriptions: tuple[SubscriptionEvaluationCommit, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.observation, Observation):
            raise TypeError("observation must be an Observation")
        subscriptions = tuple(self.subscriptions)
        if any(
            (
                not isinstance(item, SubscriptionEvaluationCommit)
                for item in subscriptions
            )
        ):
            raise TypeError("subscriptions must be SubscriptionEvaluationCommit values")
        if len({item.subscription_id for item in subscriptions}) != len(subscriptions):
            raise ValueError("observation subscription commits must be unique")
        observation = self.observation
        if observation.completeness is ObservationCompleteness.FAILED and subscriptions:
            raise ValueError("failed observations cannot advance evaluation state")
        for item in subscriptions:
            cursor = item.cursor
            if (
                cursor.observation_id != observation.observation_id
                or cursor.data_version != observation.data_version
                or cursor.completeness is not observation.completeness
                or (cursor.covered_ids != observation.covered_ids)
            ):
                raise ValueError(
                    "subscription cursor must match the committed observation"
                )
        object.__setattr__(self, "subscriptions", subscriptions)


def check_EvaluationDecision(self) -> None:
    object.__setattr__(self, "state", freeze_json(self.state))
    if not isinstance(self.triggered, bool):
        raise TypeError("triggered must be a bool")
    if self.triggered:
        _identifier(self.event_key or "", "event_key")
        if type(self.event_version) is not int or self.event_version < 1:
            raise ValueError("triggered decisions require event_version")
        if not isinstance(self.display_data, DisplayDocument):
            raise TypeError("triggered decisions require a display document")
    elif self.event_key is not None or self.event_version is not None:
        raise ValueError("non-triggered decisions cannot define an event")
    elif self.display_data is not None:
        raise TypeError("display_data must be a DisplayDocument")


def check_CollectionView(self) -> None:
    if not isinstance(self.key, CollectionKey):
        raise TypeError("collection view requires a CollectionKey")
    if self.invocation is not None and (
        not isinstance(self.invocation, InvocationView)
    ):
        raise TypeError("collection invocation requires an InvocationView")
    if self.deadline_monotonic is not None and (
        isinstance(self.deadline_monotonic, bool)
        or not isinstance(self.deadline_monotonic, (int, float))
        or (not isfinite(self.deadline_monotonic))
        or (self.deadline_monotonic <= 0)
    ):
        raise ValueError("collection deadline must be a monotonic float")


def check_IntervalLimits(self) -> None:
    values = (
        self.requested_seconds,
        self.module_minimum_seconds,
        self.runtime_minimum_seconds,
    )
    if any(
        (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or (not isfinite(value))
            or (value <= 0)
            for value in values
        )
    ):
        raise ValueError("intervals must be positive seconds")


def check_ScheduleDescriptor(self) -> None:
    for field in ("collector_id", "source_id"):
        _identifier(getattr(self, field), field)
    if (
        type(self.key_version) is not int
        or type(self.data_version) is not int
        or self.key_version < 1
        or (self.data_version < 1)
    ):
        raise ValueError("schedule versions must be positive")
    if not isinstance(self.shared_scope, OwnershipKind):
        raise TypeError("schedule shared scope must be an OwnershipKind")
    if not isinstance(self.trigger, ScheduleTrigger):
        raise TypeError("schedule trigger must be a ScheduleTrigger")
    if (
        isinstance(self.minimum_interval_seconds, bool)
        or not isinstance(self.minimum_interval_seconds, (int, float))
        or (not isfinite(self.minimum_interval_seconds))
        or (self.minimum_interval_seconds <= 0)
    ):
        raise ValueError("minimum interval must be positive")
    if self.trigger is ScheduleTrigger.PERIODIC:
        if self.default_interval_seconds is None:
            raise ValueError("periodic schedules require a default interval")
        if self.interval_config_key is None:
            raise ValueError("periodic schedules require an interval config key")
        if (
            isinstance(self.default_interval_seconds, bool)
            or not isinstance(self.default_interval_seconds, (int, float))
            or (not isfinite(self.default_interval_seconds))
            or (self.default_interval_seconds <= 0)
            or (self.default_interval_seconds < self.minimum_interval_seconds)
        ):
            raise ValueError(
                "default interval must be finite, positive, and at least the minimum"
            )
        _identifier(self.interval_config_key, "interval_config_key")
    elif (
        self.default_interval_seconds is not None
        or self.interval_config_key is not None
    ):
        raise ValueError("on-demand schedules cannot define interval configuration")
    snapshot = freeze_input_schema(self.input_schema)
    if not isinstance(snapshot, Mapping):
        raise TypeError("schedule input_schema must be a JSON object")
    if snapshot["type"] != "object":
        raise ValueError("schedule input_schema must be a closed object")
    object.__setattr__(self, "input_schema", snapshot)


def check_SubscriptionDescriptor(self) -> None:
    _identifier(self.type_id, "type_id")
    _identifier(self.collector_id, "collector_id")
    _identifier(self.matcher_id, "matcher_id")
    modes = tuple(self.notification_modes)
    if not modes or len(modes) != len(set(modes)):
        raise ValueError("notification modes must be non-empty and unique")
    for mode in modes:
        _identifier(mode, "notification mode")
    object.__setattr__(self, "notification_modes", modes)
    snapshot = freeze_input_schema(self.filter_schema)
    if not isinstance(snapshot, Mapping):
        raise TypeError("subscription filter_schema must be a JSON object")
    if snapshot["type"] != "object":
        raise ValueError("subscription filter_schema must be a closed object")
    object.__setattr__(self, "filter_schema", snapshot)


def validate_evaluation_decision(
    subscription: SubscriptionView,
    observation: Observation,
    decision: EvaluationDecision,
) -> EvaluationDecision:
    """Reject delivery documents that widen the collected data's scope."""
    from .validation_boundary import validate_contract

    validate_contract(subscription)
    validate_contract(observation)
    validate_contract(decision)
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
