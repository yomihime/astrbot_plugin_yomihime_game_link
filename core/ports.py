"""Host and persistence ports used by core services, never by modules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum, StrEnum
from math import isfinite
from typing import AsyncContextManager, Awaitable, Callable, Protocol

from ..api.administration import (
    AdminAuthorizationContext,
    AdminAuthorizationGrant,
    AdminOperation,
)
from ..api.contexts import (
    InvocationConversationKind,
    InvocationSubscriptionScope,
    InvocationView,
)
from ..api.display import _asset
from ..api.manifests import SourceDeclaration
from ..api.results import CapabilityResult
from ..api.services import (
    Binding,
    BindingDefaultSnapshot,
    CacheAccessRequest,
    CallerCapability,
    ConfigSnapshot,
    ConfigTarget,
    ConversationKey,
    ConversationKind,
    ConversationRef,
    Grant,
    HealthReport,
    LoginSession,
    PersistedConfigPatch,
    Principal,
    ResolvedIdentity,
)
from ..api.storage import (
    CacheEntry,
    CacheLookup,
    ClaimedSecretReceipt,
    CollectionDescriptor,
    DeclaredIndexQuery,
    GrantReference,
    JsonObject,
    OwnerScope,
    RecordPage,
    ResourceMetadata,
    SecretMetadata,
    SecretReceipt,
    SecretRef,
    SecretTarget,
    VersionedRecord,
    validate_module_id,
)
from ..api.storage import JsonValue as JsonValue
from ..api.subscriptions import (
    ActiveDigestSchedule,
    CollectionKey,
    DeliveryAttempt,
    DeliveryEvent,
    DeliveryEventCursor,
    DeliveryState,
    DigestEnvelope,
    DigestEnvelopeClaim,
    DigestMember,
    DigestMemberReceipt,
    DigestRouteCandidate,
    DigestRouteCursor,
    DigestWindow,
    DigestWindowSelector,
    DueCollectionJob,
    DueJobCursor,
    Observation,
    ObservationEvaluationCommit,
    SubscriptionEvaluationSnapshot,
    SubscriptionJobAssociation,
    SubscriptionJobChange,
    SubscriptionRecord,
)


def _bounded_identifier(value: str, field: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(character in value for character in ("/", "\\", "\n"))
    ):
        raise ValueError(f"{field} must be a bounded identifier")


def _positive_int(value: int, field: str) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field} must be a positive integer")


def _non_negative_int(value: int, field: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")


def _lease_text(value: str, field: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 512
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{field} must be bounded non-control text")


def _lease_dependencies(
    values: tuple[DependencyIdentity, ...], field: str
) -> tuple[DependencyIdentity, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{field} must contain DependencyIdentity values")
    try:
        dependencies = tuple(values)
    except TypeError:
        raise TypeError(f"{field} must be iterable") from None
    if any(not isinstance(item, DependencyIdentity) for item in dependencies):
        raise TypeError(f"{field} must contain DependencyIdentity values")
    keys = {(item.module_id, item.capability_id) for item in dependencies}
    if len(keys) != len(dependencies):
        raise ValueError(f"{field} cannot contain duplicate dependencies")
    return dependencies


def _delivery_members(
    values: tuple[DeliveryMemberIdentity, ...],
    *,
    field: str,
    allow_empty: bool,
) -> tuple[DeliveryMemberIdentity, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{field} must contain DeliveryMemberIdentity values")
    try:
        members = tuple(values)
    except TypeError:
        raise TypeError(f"{field} must be iterable") from None
    if any(not isinstance(item, DeliveryMemberIdentity) for item in members):
        raise TypeError(f"{field} must contain DeliveryMemberIdentity values")
    if not allow_empty and not members:
        raise ValueError(f"{field} must contain at least one member")
    keys = {
        (
            item.event_key,
            item.event_version,
            item.subscription_id,
            item.subscription_revision,
        )
        for item in members
    }
    if len(keys) != len(members):
        raise ValueError(f"{field} cannot contain duplicate delivery members")
    return members


@dataclass(frozen=True, slots=True)
class RunIdentity:
    """Immutable runtime identity; only Lifecycle allocates module epochs."""

    runtime_id: str
    module_id: str
    module_epoch: int

    def __post_init__(self) -> None:
        _lease_text(self.runtime_id, "runtime_id")
        validate_module_id(self.module_id)
        _positive_int(self.module_epoch, "module_epoch")


@dataclass(frozen=True, slots=True)
class DependencyIdentity:
    """Version of one declared capability dependency, not an authority token."""

    module_id: str
    module_epoch: int
    capability_id: str
    health_revision: int

    def __post_init__(self) -> None:
        validate_module_id(self.module_id)
        _positive_int(self.module_epoch, "module_epoch")
        _lease_text(self.capability_id, "capability_id")
        _non_negative_int(self.health_revision, "health_revision")


@dataclass(frozen=True, slots=True)
class AdmissionLease:
    """Immutable snapshot checked by AdmissionPort; construction grants nothing."""

    lease_id: str
    invocation_id: str
    module_id: str
    module_epoch: int
    capability_id: str
    health_revision: int
    registry_revision: int
    dependencies: tuple[DependencyIdentity, ...]

    def __post_init__(self) -> None:
        _lease_text(self.lease_id, "lease_id")
        _lease_text(self.invocation_id, "invocation_id")
        validate_module_id(self.module_id)
        _positive_int(self.module_epoch, "module_epoch")
        _lease_text(self.capability_id, "capability_id")
        _non_negative_int(self.health_revision, "health_revision")
        _non_negative_int(self.registry_revision, "registry_revision")
        object.__setattr__(
            self, "dependencies", _lease_dependencies(self.dependencies, "dependencies")
        )


@dataclass(frozen=True, slots=True)
class ScheduledLease:
    """Lease derived from a persisted scheduler execution claim."""

    lease_id: str
    execution_lease_id: str
    module_id: str
    module_epoch: int
    registry_revision: int
    collector_id: str
    key_version: int
    dependencies: tuple[DependencyIdentity, ...]

    def __post_init__(self) -> None:
        _lease_text(self.lease_id, "lease_id")
        _lease_text(self.execution_lease_id, "execution_lease_id")
        validate_module_id(self.module_id)
        _positive_int(self.module_epoch, "module_epoch")
        _non_negative_int(self.registry_revision, "registry_revision")
        _lease_text(self.collector_id, "collector_id")
        _positive_int(self.key_version, "key_version")
        object.__setattr__(
            self, "dependencies", _lease_dependencies(self.dependencies, "dependencies")
        )


class DeliveryWorkKind(StrEnum):
    EVENT = "event"
    DIGEST = "digest"


@dataclass(frozen=True, slots=True)
class DeliveryMemberIdentity:
    """One persisted digest/event member's current authority and trusted route."""

    event_key: str
    event_version: int
    subscription_id: str
    subscription_revision: int
    owner_id: str
    module_id: str
    grant_id: str | None
    grant_revision: int | None
    adapter_id: str
    conversation_id: str
    delivery_route: str
    conversation_kind: InvocationConversationKind
    subscription_scope: InvocationSubscriptionScope

    def __post_init__(self) -> None:
        for field in (
            "event_key",
            "subscription_id",
            "owner_id",
            "adapter_id",
            "conversation_id",
            "delivery_route",
        ):
            _lease_text(getattr(self, field), field)
        _positive_int(self.event_version, "event_version")
        _positive_int(self.subscription_revision, "subscription_revision")
        validate_module_id(self.module_id)
        if not isinstance(self.conversation_kind, InvocationConversationKind):
            raise TypeError("conversation_kind must be InvocationConversationKind")
        if not isinstance(self.subscription_scope, InvocationSubscriptionScope):
            raise TypeError("subscription_scope must be InvocationSubscriptionScope")
        if (self.grant_id is None) != (self.grant_revision is None):
            raise ValueError("grant_id and grant_revision must be present together")
        if self.grant_id is not None:
            _lease_text(self.grant_id, "grant_id")
            _positive_int(self.grant_revision, "grant_revision")
        if self.subscription_scope is InvocationSubscriptionScope.AUTHORIZED:
            if self.conversation_kind is not InvocationConversationKind.DIRECT:
                raise ValueError("authorized delivery requires a direct conversation")
            if self.grant_id is None:
                raise ValueError(
                    "authorized delivery requires a current Grant identity"
                )


@dataclass(frozen=True, slots=True)
class DeliveryLease:
    """Snapshot for persistent delivery; members are checked individually."""

    lease_id: str
    work_id: str
    kind: DeliveryWorkKind
    module_id: str
    module_epoch: int
    registry_revision: int
    collector_id: str
    key_version: int
    dependencies: tuple[DependencyIdentity, ...]
    members: tuple[DeliveryMemberIdentity, ...]

    def __post_init__(self) -> None:
        _lease_text(self.lease_id, "lease_id")
        _lease_text(self.work_id, "work_id")
        if not isinstance(self.kind, DeliveryWorkKind):
            raise TypeError("kind must be a DeliveryWorkKind")
        validate_module_id(self.module_id)
        _positive_int(self.module_epoch, "module_epoch")
        _non_negative_int(self.registry_revision, "registry_revision")
        _lease_text(self.collector_id, "collector_id")
        _positive_int(self.key_version, "key_version")
        object.__setattr__(
            self, "dependencies", _lease_dependencies(self.dependencies, "dependencies")
        )
        members = _delivery_members(self.members, field="members", allow_empty=False)
        if any(member.module_id != self.module_id for member in members):
            raise ValueError("delivery members must belong to the leased module")
        if self.kind is DeliveryWorkKind.EVENT and len(members) != 1:
            raise ValueError("event delivery requires exactly one member")
        object.__setattr__(self, "members", members)


@dataclass(frozen=True, slots=True)
class SendApproval:
    """Durable SENDING claim result; it does not authorize a platform call."""

    claim_id: str
    members: tuple[DeliveryMemberIdentity, ...]

    def __post_init__(self) -> None:
        _lease_text(self.claim_id, "claim_id")
        object.__setattr__(
            self,
            "members",
            _delivery_members(self.members, field="members", allow_empty=True),
        )


@dataclass(frozen=True, slots=True)
class SendPermit:
    """Last in-process send approval snapshot; never a platform receipt."""

    permit_id: str
    lease_id: str
    module_id: str
    module_epoch: int
    members: tuple[DeliveryMemberIdentity, ...]

    def __post_init__(self) -> None:
        _lease_text(self.permit_id, "permit_id")
        _lease_text(self.lease_id, "lease_id")
        validate_module_id(self.module_id)
        _positive_int(self.module_epoch, "module_epoch")
        members = _delivery_members(self.members, field="members", allow_empty=True)
        if any(member.module_id != self.module_id for member in members):
            raise ValueError("permit members must belong to its module")
        object.__setattr__(self, "members", members)


class ApprovedSendScheduler(Protocol):
    """Core-owned outbound scope; schedule must synchronously take task ownership."""

    def schedule(
        self, permit: SendPermit, task: Awaitable[object], *, name: str
    ) -> None: ...


class AdmissionPort(Protocol):
    """Single admission and last-send-approval boundary for one CoreRuntime.

    The values accepted here are snapshots only: every method checks them
    against current Lifecycle/health/registry state before granting work.
    """

    def admit(self, view: InvocationView, capability_id: str) -> AdmissionLease: ...

    def admit_schedule(
        self, execution: ExecutionLease, collector_id: str
    ) -> ScheduledLease: ...

    def admit_delivery(
        self,
        *,
        work_id: str,
        kind: DeliveryWorkKind,
        module_id: str,
        collector_id: str,
        key_version: int,
        members: tuple[DeliveryMemberIdentity, ...],
    ) -> DeliveryLease: ...

    def check(self, lease: AdmissionLease | ScheduledLease | DeliveryLease) -> None: ...

    def release(
        self, lease: AdmissionLease | ScheduledLease | DeliveryLease
    ) -> None: ...

    def activate(self, identity: RunIdentity, health: HealthReport) -> None: ...

    def close(self, identity: RunIdentity, reason: str) -> None: ...

    def mutation(self, owner: str) -> AsyncContextManager[None]: ...

    async def approve_and_schedule_send(
        self,
        lease: AdmissionLease | DeliveryLease,
        *,
        name: str,
        final_check: Callable[[], Awaitable[SendApproval | None]],
        sender: Callable[[SendPermit], Awaitable[object]],
        scheduler: ApprovedSendScheduler,
        abort_before_dispatch: Callable[[SendApproval, str], Awaitable[None]],
    ) -> SendPermit | None: ...


def _positive_finite(value: float, field: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{field} must be finite positive seconds")


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError(f"{field} must be a UTC instant")


class AdminAuthorizationPort(Protocol):
    """Core-owned per-operation authorization boundary for admin calls.

    Implementations reject missing/untrusted/expired contexts and unavailable
    credential state. Dashboard identity, plugin scope, API keys, and
    ``InvocationView`` alone never grant access. Reads and mutations are each
    checked independently; mutations must revalidate within their own write
    transaction and bind grants to the persisted credential generation.
    """

    async def authorize(
        self,
        operation: AdminOperation,
        *,
        invocation: InvocationView | None,
        context: AdminAuthorizationContext | None,
    ) -> AdminAuthorizationGrant: ...


@dataclass(frozen=True, slots=True)
class MessageTarget:
    """Descriptive target; its authorized flag and ConversationRef prove nothing.

    A host sender must validate the exact resolver-attested route and current
    owner/Grant state before delivery.
    """

    conversation_id: str
    recipient_id: str | None = None
    conversation: ConversationRef | None = None
    authorized: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.conversation_id, str) or not self.conversation_id:
            raise ValueError("conversation_id must be non-empty text")
        if self.recipient_id is not None and (
            not isinstance(self.recipient_id, str) or not self.recipient_id
        ):
            raise ValueError("recipient_id must be non-empty text when present")
        if self.conversation is not None:
            if not isinstance(self.conversation, ConversationRef):
                raise TypeError("conversation must be a ConversationRef")
            if self.conversation.conversation_id != self.conversation_id:
                raise ValueError("message target must match its conversation reference")
        if not isinstance(self.authorized, bool):
            raise TypeError("authorized must be a bool")
        if self.authorized and (
            self.conversation is None
            or self.conversation.kind is not ConversationKind.DIRECT
        ):
            raise ValueError(
                "authorized delivery requires a trusted direct conversation"
            )


@dataclass(frozen=True, slots=True)
class RenderedMessage:
    text: str
    resource_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("rendered text must be non-empty text")
        resource_ids = tuple(self.resource_ids)
        for resource_id in resource_ids:
            _asset(resource_id)
        object.__setattr__(self, "resource_ids", resource_ids)


@dataclass(frozen=True, slots=True)
class MessageReceipt:
    """Host send outcome; ACCEPTED means queued by platform, never user-read."""

    status: "MessageStatus"
    platform_message_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, MessageStatus):
            raise TypeError("message receipt status must be a MessageStatus")
        if self.platform_message_id is not None and not isinstance(
            self.platform_message_id, str
        ):
            raise TypeError("platform message id must be text")


class MessageStatus(str, Enum):
    ACCEPTED = "accepted"
    FAILED = "failed"
    UNKNOWN = "unknown"


class RootOutputState(str, Enum):
    """Durable state for one root invocation/output route."""

    CLAIMED = "claimed"
    SENDING = "sending"
    COMPLETED = "completed"
    UNKNOWN = "unknown"


class RootOutputOutcome(str, Enum):
    """Completed route kind; non-message paths carry no fake receipt."""

    MESSAGE = "message"
    TOOL_RETURNED = "tool_returned"
    SUBSCRIPTION_ENQUEUED = "subscription_enqueued"
    CONTROLLED_RESULT = "controlled_result"


def _output_identity(value: str, field: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{field} must be bounded non-control text")


@dataclass(frozen=True, slots=True)
class RootOutputClaim:
    """Persisted ownership and outcome of one root invocation/output identity.

    ``output_identity`` is normally ``CapabilityResult.result_id``. Rows are
    retained for a configured bounded period. The key covers retries of the
    same root invocation only; a later invocation is a new output identity.
    ``claim_generation`` is database allocated and never reused, even after a
    retained output row expires and a later retry creates a new row.
    """

    root_invocation_id: str
    output_identity: str
    payload_fingerprint: str
    state: RootOutputState
    owner_token: str
    claim_generation: int
    claimed_at: datetime
    lease_expires_at: datetime
    outcome: RootOutputOutcome | None = None
    receipt: MessageReceipt | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        _output_identity(self.root_invocation_id, "root_invocation_id")
        _output_identity(self.output_identity, "output_identity")
        if (
            not isinstance(self.payload_fingerprint, str)
            or len(self.payload_fingerprint) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.payload_fingerprint
            )
        ):
            raise ValueError(
                "payload_fingerprint must be a lowercase SHA-256 hex digest"
            )
        if not isinstance(self.state, RootOutputState):
            raise TypeError("state must be RootOutputState")
        if self.outcome is not None and not isinstance(self.outcome, RootOutputOutcome):
            raise TypeError("outcome must be RootOutputOutcome or None")
        _output_identity(self.owner_token, "owner_token")
        _positive_int(self.claim_generation, "claim_generation")
        _require_utc(self.claimed_at, "claimed_at")
        _require_utc(self.lease_expires_at, "lease_expires_at")
        if self.lease_expires_at <= self.claimed_at:
            raise ValueError("output claim lease must expire after it is claimed")
        if self.receipt is not None and not isinstance(self.receipt, MessageReceipt):
            raise TypeError("receipt must be a MessageReceipt")
        if self.error_code is not None:
            _bounded_identifier(self.error_code, "error_code")
        if self.state is RootOutputState.COMPLETED:
            if self.outcome is None:
                raise ValueError("completed output requires its route outcome")
            if (self.outcome is RootOutputOutcome.MESSAGE) != (
                self.receipt is not None
            ):
                raise ValueError("only message outcomes carry an exact MessageReceipt")
            if (
                self.error_code is not None
                and self.outcome is not RootOutputOutcome.CONTROLLED_RESULT
            ):
                raise ValueError("only controlled results may carry an error code")
            if (
                self.outcome is RootOutputOutcome.CONTROLLED_RESULT
                and self.error_code is None
            ):
                raise ValueError("controlled result requires a stable error code")
        elif self.outcome is not None or self.receipt is not None:
            raise ValueError(
                "unfinished or recovered outputs cannot carry an outcome receipt"
            )
        if (
            self.state is RootOutputState.UNKNOWN
            and self.error_code != "interrupted_send"
        ):
            raise ValueError("recovered UNKNOWN output requires interrupted_send state")
        if (
            self.state in (RootOutputState.CLAIMED, RootOutputState.SENDING)
            and self.error_code is not None
        ):
            raise ValueError("live output claims cannot carry an error code")


class RootOutputConflict(RuntimeError):
    """The same invocation/output key was retried with a different payload."""

    code = "root_output_conflict"


class RootOutputRepository(Protocol):
    """Durable root-unique CAS with a persistent fencing generation.

    One root invocation binds to one output identity and fingerprint while its
    row is retained. Every fresh claim and reclaim advances a database-owned,
    never-pruned generation. Transitions compare that generation as well as
    the owner token, so an old snapshot cannot act after reclaim or retention
    deletion/recreation, even if an owner token is reused.
    """

    async def claim(
        self,
        root_invocation_id: str,
        output_identity: str,
        payload_fingerprint: str,
        owner_token: str,
        *,
        now: datetime,
        lease_expires_at: datetime,
    ) -> RootOutputClaim: ...

    async def begin_sending(
        self,
        claim: RootOutputClaim,
        *,
        now: datetime,
        lease_expires_at: datetime,
    ) -> RootOutputClaim | None: ...

    async def abort_before_dispatch(
        self,
        claim: RootOutputClaim,
        *,
        completed_at: datetime,
        error_code: str,
    ) -> RootOutputClaim | None:
        """CAS an exact SENDING generation to a known-not-sent completion.

        Admission uses this only when outbound task registration definitely did
        not happen. The implementation matches the generation and owner token,
        then stores COMPLETED + CONTROLLED_RESULT with no receipt and the stable
        error code. Stale, non-SENDING, or UNKNOWN claims return None. Uncertain
        host IO must use ordinary UNKNOWN completion/recovery, never this path.
        """
        ...

    async def complete(
        self,
        claim: RootOutputClaim,
        receipt: MessageReceipt | None,
        *,
        outcome: RootOutputOutcome,
        completed_at: datetime,
        error_code: str | None = None,
    ) -> RootOutputClaim | None: ...

    async def recover_expired(
        self, *, before: datetime, recovered_at: datetime
    ) -> tuple[RootOutputClaim, ...]: ...


class MessagePort(Protocol):
    """Host send boundary; callers persist the attempt before and after IO.

    ACCEPTED records platform acceptance, FAILED records a known rejection,
    and UNKNOWN means acceptance cannot be determined. UNKNOWN is reconciled
    by policy and never blindly resent.
    """

    async def send(
        self, target: MessageTarget, payload: RenderedMessage
    ) -> MessageReceipt: ...


class HostIdentityPort(Protocol):
    async def is_administrator(self, actor_id: str) -> bool: ...


class UnitOfWork(Protocol):
    async def __aenter__(self) -> "UnitOfWork": ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

    async def close(self) -> None: ...

    @property
    def closed(self) -> bool: ...


class UnitOfWorkFactory(Protocol):
    async def begin(self) -> UnitOfWork: ...


class ModuleNotRegistered(ValueError):
    """Stable error returned when the injected registry has no module entry."""

    code = "module_not_registered"

    def __init__(self, module_id: str) -> None:
        validate_module_id(module_id)
        self.module_id = module_id
        super().__init__("module is not registered")


@dataclass(frozen=True, slots=True)
class ModuleRegistrationSnapshot:
    """A snapshot returned by a trusted lookup, not a registration proof."""

    module_id: str
    enabled: bool
    registry_revision: int
    epoch: int
    collections: tuple[CollectionDescriptor, ...] = ()

    def __post_init__(self) -> None:
        validate_module_id(self.module_id)
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be a bool")
        for field in ("registry_revision", "epoch"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field} must be a non-negative integer")
        try:
            collections = tuple(
                CollectionDescriptor.validate(item) for item in self.collections
            )
        except (AttributeError, TypeError, ValueError):
            raise ValueError("module collection declarations are invalid") from None
        if len({item.name for item in collections}) != len(collections):
            raise ValueError("module collection names must be unique")
        object.__setattr__(self, "collections", collections)


class ModuleRegistrationLookup(Protocol):
    """Trusted injection point for current registry membership.

    ``require_registered`` distinguishes membership from ``enabled``. Consumers
    must compare the returned registry revision/epoch and declared collections
    with any issued handle; a constructible snapshot alone is never accepted as
    proof of registration.
    """

    async def require_registered(
        self, module_id: str
    ) -> ModuleRegistrationSnapshot: ...


class CallerCapabilityIssuer(Protocol):
    """Trusted issuer/validator for the capability caller identity.

    ``require`` must validate the exact issuer-created object and its module,
    registry revision, and epoch against the invocation; a copied or manually
    constructed DTO is not an attestation.
    """

    def issue(
        self, invocation: InvocationView, capability_id: str
    ) -> CallerCapability: ...

    def require(
        self, invocation: InvocationView, caller: CallerCapability
    ) -> CallerCapability: ...


class GrantRepository(Protocol):
    async def current(self, grant_id: str) -> GrantReference | None: ...

    async def revoke(self, grant: GrantReference) -> None: ...


class SubscriptionRepository(Protocol):
    async def current_revision(self, subscription_id: str) -> int | None: ...


class SubscriptionStore(Protocol):
    """Internal command-authorized storage with explicit revision CAS semantics.

    create is insert-if-absent at revision 1. revise and cancel compare the
    caller's exact prior revision and atomically advance the record revision;
    a stale revision is a conflict and cannot change recipient or Grant scope.
    Implementations receive this port only behind issuer-validated COMMAND
    operations. Tool and internal invocations cannot acquire subscription
    authority by changing descriptive origin fields.
    """

    async def create(self, record: SubscriptionRecord) -> SubscriptionRecord: ...

    async def revise(
        self, record: SubscriptionRecord, *, expected_revision: int
    ) -> SubscriptionRecord: ...

    async def current(self, subscription_id: str) -> SubscriptionRecord | None: ...

    async def list_for_owner(
        self, owner_id: str, *, limit: int, after_subscription_id: str | None = None
    ) -> tuple[SubscriptionRecord, ...]:
        """Return active records for exactly one owner in stable ID order."""
        ...

    async def list_active_digest_schedules(
        self, *, limit: int, after_subscription_id: str | None = None
    ) -> tuple[ActiveDigestSchedule, ...]:
        """Scan global active digest schedules in subscription_id order.

        The exclusive after ID is stable across restart. Filter active rows
        with notification_mode=digest, saved type and digest profile before
        applying limit. The caller
        checkpoints the last candidate, drains pages, then resets to None after
        exhaustion so later IDs cannot starve earlier newly-added IDs.
        Returned persisted recipient/profile are candidates, not authority; S
        re-reads SubscriptionStore.current before every window and requires the
        exact active revision, type, profile, recipient, key scope and Grant.
        S also checks a current enabled Registry module/type/schedule and epoch,
        current GrantRepository reference when present, and an exact host route
        resolver result. AUTHORIZED scope requires resolve_private and DIRECT.
        Rechecks repeat immediately before the idempotent create call.
        """
        ...

    async def cancel(
        self, subscription_id: str, *, expected_revision: int
    ) -> SubscriptionRecord: ...


class SubscriptionJobRepository(Protocol):
    """Many subscription links may point to one unique shared collection job.

    ``expected_revision=None`` inserts the first association for a subscription;
    an integer CAS-revises that link. The collection job itself remains unique
    by the complete CollectionKey. Removing one link never deletes other links.
    """

    async def associate(
        self,
        association: SubscriptionJobAssociation,
        *,
        expected_revision: int | None,
    ) -> SubscriptionJobAssociation: ...

    async def remove(self, subscription_id: str, *, expected_revision: int) -> None: ...

    async def for_collection(
        self, key: CollectionKey
    ) -> tuple[SubscriptionJobAssociation, ...]: ...

    async def current_for_subscription(
        self, subscription_id: str
    ) -> SubscriptionJobAssociation | None: ...


class SubscriptionLifecycleRepository(Protocol):
    """Atomically change one subscription and its shared-job association.

    ``apply`` performs the requested CREATE/REVISE/CANCEL operation and both
    CAS checks in one transaction. A link failure, stale subscription revision,
    stale association revision, or any other exception rolls back both rows.
    CREATE and REVISE also supply the trusted first schedule for the associated
    collection key. CANCEL never supplies or arms a collection schedule.
    Command services must use this
    port for create/revise/cancel; the independent legacy ports remain useful
    for reads and non-lifecycle operations only.
    """

    async def apply(
        self,
        change: SubscriptionJobChange,
        *,
        initial_run: CollectionRunRequest | None = None,
    ) -> SubscriptionRecord: ...


@dataclass(frozen=True, slots=True)
class CollectionRunRequest:
    key: CollectionKey
    due_at: datetime
    cadence_seconds: float
    config_revision: int
    module_epoch: int
    registry_revision: int

    def __post_init__(self) -> None:
        if not isinstance(self.key, CollectionKey):
            raise TypeError("run request requires CollectionKey")
        _require_utc(self.due_at, "due_at")
        _positive_finite(self.cadence_seconds, "cadence_seconds")
        for name in ("config_revision", "module_epoch", "registry_revision"):
            _positive_int(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class ExecutionLease:
    key: CollectionKey
    token: str
    config_revision: int
    module_epoch: int
    registry_revision: int
    expires_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.key, CollectionKey):
            raise TypeError("lease requires CollectionKey")
        _bounded_identifier(self.token, "lease token")
        for name in ("config_revision", "module_epoch", "registry_revision"):
            _positive_int(getattr(self, name), name)
        _require_utc(self.expires_at, "expires_at")


class SchedulerRepository(Protocol):
    """Unique active lease per normalized CollectionKey, across workers.

    claim_due must use a persistent uniqueness boundary on the complete key,
    including scope, and only return one unexpired token. commit_observation
    accepts only that exact live token, the matching key, config revision,
    module epoch, and registry revision. A stale, revoked, disabled, expired,
    or mismatched result returns False and is not committed.
    """

    async def claim_due(
        self, request: CollectionRunRequest, *, now: datetime
    ) -> ExecutionLease | None: ...

    async def is_current(self, lease: ExecutionLease, *, now: datetime) -> bool:
        """Check one exact persistent lease immediately before scheduler admit.

        In one read transaction compare the complete collection key, token,
        config revision, module epoch, captured registry revision and persisted
        expiry with the row; return True only if it is the same unexpired lease
        and its expiry is later than ``now``. Do not compare against the current
        global Registry revision: this is a claim snapshot, not a live directory
        equality check. This proof does not replace the observation commit CAS.
        """
        ...

    async def list_due_jobs(
        self,
        *,
        now: datetime,
        limit: int,
        after_cursor: DueJobCursor | None = None,
    ) -> tuple[DueCollectionJob, ...]:
        """Page persisted candidates; module epoch and registry are live-resolved.

        Candidates contain the full key, due time, cadence, config revision,
        and cursor. Order by (due_at, job_key) and use strict greater-than
        after_cursor comparison. Return only due rows with an active current
        subscription association and no unexpired lease. The scheduler obtains
        current enabled module epoch and registry revision from Registry before
        claiming. Persisted values never attest current module state. Caller
        advances past every returned row, including live-check rejects, and
        resets only after exhausting the due set.
        """
        ...

    async def current_evaluation(
        self, subscription_id: str, collection_key: CollectionKey
    ) -> SubscriptionEvaluationSnapshot | None:
        """Read authoritative per-subscription evaluation inputs.

        Resolve the exact current subscription ID and full CollectionKey,
        including ownership scope, owner ID, and Grant reference/revision. A
        same-ID query with any different key or scope returns ``None``. The
        snapshot's SubscriptionRecord supplies the current subscription
        revision/status/filters; ``state.revision`` is the expected evaluation
        CAS revision (``None`` means no state has yet been committed).
        Return the saved cursor and observation together so callers never infer
        last-observation state from a collection-wide latest row.
        """
        ...

    async def commit_observation(
        self,
        lease: ExecutionLease,
        observation: Observation,
        *,
        next_due_at: datetime | None = None,
    ) -> bool:
        """Commit one observation and release its lease atomically.

        ``next_due_at`` is an optional explicit UTC reschedule chosen by the
        scheduler from completion time, outcome, configured backoff, and
        configured jitter. When omitted, preserve the legacy
        ``observation.collected_at + cadence_seconds`` behavior. An explicit
        value must be later than ``observation.collected_at`` to prevent an
        already-due hot loop; policy limits and completion-relative calculation
        belong to the scheduler.
        """
        ...

    async def commit_observation_with_evaluations(
        self,
        lease: ExecutionLease,
        commit: ObservationEvaluationCommit,
        *,
        next_due_at: datetime | None = None,
    ) -> bool:
        """Atomically commit observation, CAS states/cursors, and outputs.

        All immediate delivery events and window/recipient digest member
        associations in the commit share the observation transaction. Each
        association identifies its stable window and exact recipient route.
        Its window must already exist (or the transaction must reject); window
        creation persists UTC bounds and DST policy separately. Any stale
        subscription/state revision, lease, epoch, or registry revision rejects
        the entire commit. Evaluation pipelines must use this method instead
        of splitting writes across ``commit_observation`` and later repository
        calls. The scheduler must also check the current Registry snapshot
        immediately before this call. Without a lock shared with Registry, the
        snapshot check and database commit are not linearizable with concurrent
        module disable; this contract makes no stronger claim. Optional
        ``next_due_at`` is an explicit UTC reschedule chosen by the scheduler
        from completion time, outcome, configured backoff, and configured
        jitter. It must be later than the observation collection time and is
        committed with all other state under the live lease. When omitted,
        retain the legacy ``observation.collected_at + cadence_seconds`` rule.
        """
        ...

    async def release(self, lease: ExecutionLease) -> None: ...


class DigestWindowRepository(Protocol):
    """Persist stable windows and reuse their stored UTC bounds after restart."""

    async def create(self, window: DigestWindow) -> DigestWindow: ...

    async def get(self, window_id: str) -> DigestWindow | None: ...

    async def for_schedule(self, selector: DigestWindowSelector) -> DigestWindow | None:
        """Find a persisted profile/recipient window covering event_at.

        Stored UTC bounds and DST policy remain authoritative after profile or
        timezone database changes; this lookup never recalculates them. Bounds
        are half-open. On DST-induced overlap, choose earliest utc_end, breaking
        ties by window_id. Return None when no existing window covers the time;
        this read never creates one.
        """
        ...

    async def list_due_routes(
        self,
        *,
        now: datetime,
        limit: int,
        after_cursor: DigestRouteCursor | None = None,
    ) -> tuple[DigestRouteCandidate, ...]:
        """Page eligible due routes in (due_at, window_id, recipient_key) order.

        Use strict greater-than after_cursor comparison. Include an absent or
        READY envelope, or a FAILED envelope whose saved retry_at is due;
        exclude SENDING, SENT, UNKNOWN, CANCELLED, live claims and not-yet-due
        FAILED rows. D explicitly reopens due FAILED envelopes before claim.
        Candidates are data, not authorization. The caller advances past
        invalid candidates and resets only after exhausting the due set.
        """
        ...

    async def current_envelope(
        self, window_id: str, recipient: ConversationRef
    ) -> DigestEnvelope | None:
        """Read an existing envelope by window and complete persisted route.

        This lookup never creates an envelope and does not authorize a retry.
        A caller may inspect the returned state and retry_at, then use its
        persisted envelope_id and revision with retry_failed_envelope; that
        transition remains the sole CAS authority for reopening known failure.
        """
        ...

    async def add_members(
        self, window_id: str, members: tuple[DigestMember, ...]
    ) -> DigestWindow: ...

    async def claim_due_envelope(
        self,
        window_id: str,
        recipient: ConversationRef,
        *,
        now: datetime,
        lease_expires_at: datetime,
    ) -> DigestEnvelopeClaim | None:
        """Atomically claim the unique due envelope for this route.

        The idempotency key is the stable window ID and full recipient route
        (adapter, conversation kind/ID, and delivery route). A scan claims only
        READY envelopes and records one owner token plus its expiry. A competing
        scan while that lease is live returns ``None``; it never receives the
        existing owner's claim. ``lease_expires_at`` must be a future UTC time.
        A terminal SENT, FAILED, UNKNOWN, or CANCELLED envelope is never returned
        by a due scan. A known FAILED envelope must be explicitly reopened by
        ``retry_failed_envelope`` after caller-approved retry policy.
        """
        ...

    async def reconcile_envelope_members(
        self,
        claim: DigestEnvelopeClaim,
        receipts: tuple[DigestMemberReceipt, ...],
        *,
        expected_revision: int,
    ) -> DigestEnvelope:
        """CAS-record receipts and remove every non-INCLUDED member.

        Receipts must cover the exact currently claimed member set and are
        produced only after the owning service rechecks subscription revision,
        authorization, and module availability. Receipts are data, not authority.
        The claim token and envelope revision are both checked. The returned
        envelope atomically filters members, event/document associations, and
        receipts in the same order. If no member remains, it is CANCELLED.
        """
        ...

    async def begin_envelope_send(
        self,
        claim: DigestEnvelopeClaim,
        *,
        expected_revision: int,
        attempt_number: int,
        started_at: datetime,
    ) -> DigestEnvelope | None:
        """CAS the live claim to SENDING and append its ledger attempt.

        The attempt uses ``digest_envelope_idempotency_key(window, recipient)``
        and is committed before network IO. ``started_at`` must be before the
        persisted lease expiry. A stale/expired claim or competing attempt
        returns ``None`` and authorizes no send.
        """
        ...

    async def complete_envelope_send(
        self,
        claim: DigestEnvelopeClaim,
        attempt: DeliveryAttempt,
        *,
        expected_revision: int,
        retry_at: datetime | None = None,
    ) -> DigestEnvelope | None:
        """CAS the exact in-flight envelope attempt to SENT, FAILED, or UNKNOWN.

        Only terminal ``DeliveryAttempt`` states SENT, FAILED, or UNKNOWN are
        accepted. The attempt number, stable envelope idempotency key, live
        owner token, and envelope revision must all match. SENT maps accepted
        host delivery. A known FAILED result may carry retry_at selected by
        configured policy and is reopened only after that UTC instant. UNKNOWN
        remains unresolved and cannot be retried by due scanning.
        """
        ...

    async def abort_envelope_send(
        self,
        claim: DigestEnvelopeClaim,
        attempt: DeliveryAttempt,
        *,
        expected_revision: int,
    ) -> DigestEnvelope | None:
        """Record a known pre-dispatch cancellation and release the live claim.

        In one transaction match the claim token, envelope revision, and the
        last SENDING attempt's number, idempotency key, and started_at. ``attempt``
        must be FAILED with error_code ``cancelled_before_dispatch``. Commit the
        attempt and clear claim owner/expiry; return the envelope READY (or
        CANCELLED when it has no members), with no retry_at. Return None for any
        stale claim/revision/attempt or terminal state. UNKNOWN and SENT must
        never be reopened through this operation. This is distinct from the
        policy-controlled retry_failed_envelope path for a platform failure.
        """
        ...

    async def recover_expired_envelope_claims(
        self, *, before: datetime, recovered_at: datetime
    ) -> tuple[DigestEnvelope, ...]:
        """Recover expired claims without replaying uncertain network sends.

        ``before`` and ``recovered_at`` are UTC instants. An expired CLAIMED
        envelope with no in-flight attempt returns to READY
        and gets a new revision/token boundary. An expired SENDING envelope
        moves its exact attempt to UNKNOWN, clears ownership, and remains
        non-claimable until explicit reconciliation; it is never blindly resent.
        """
        ...

    async def retry_failed_envelope(
        self, envelope_id: str, *, expected_revision: int, now: datetime
    ) -> DigestEnvelope | None:
        """Reopen a known FAILED envelope only after its persisted retry_at.

        now must be a UTC instant; envelope retry_at must be set and due. The
        attempt ledger is retained. UNKNOWN, SENDING, SENT, and CANCELLED are
        not retryable through this transition.
        """
        ...


class DeliveryRepository(Protocol):
    """Persist events and attempts under event/version and idempotency keys.

    ACCEPTED maps to SENT and means platform acceptance, FAILED stays distinct,
    and UNKNOWN remains unresolved. Recovery changes stale SENDING to UNKNOWN;
    it never infers failure or authorizes a resend. Retry policy and unknown
    reconciliation remain deployment configuration.
    """

    async def create_event(self, event: DeliveryEvent) -> DeliveryEvent: ...

    async def current_event(
        self,
        event_key: str,
        event_version: int,
        *,
        subscription_id: str,
        subscription_revision: int,
    ) -> DeliveryEvent | None: ...

    async def record_attempt(
        self,
        event_key: str,
        event_version: int,
        attempt: DeliveryAttempt,
        *,
        subscription_id: str,
        subscription_revision: int,
        expected_state: DeliveryState,
        retry_at: datetime | None = None,
    ) -> DeliveryEvent:
        """Persist one terminal attempt and optional known-FAILED retry time.

        retry_at is a UTC instant, accepted only with a FAILED attempt; None
        means no automatic retry is scheduled. UNKNOWN never receives retry_at.
        """
        ...

    async def list_due_events(
        self,
        *,
        now: datetime,
        limit: int,
        after_cursor: DeliveryEventCursor | None = None,
    ) -> tuple[DeliveryEvent, ...]:
        """Page eligible instant-delivery events by stable delivery identity.

        Sort by (event_key, event_version, subscription_id,
        subscription_revision), strictly after ``after_cursor``. Return only PENDING or FAILED
        events, with FAILED events due, whose persisted subscription snapshot is
        in instant mode; digest-mode events are excluded for their envelope
        path. Stale or otherwise ineligible instant events may still be
        returned so dispatch can persist cleanup. Exclude SENDING, SENT,
        UNKNOWN, CANCELLED and not-yet-due failures. The dispatcher advances
        past invalid subscription/route candidates and never retries UNKNOWN.
        Caller resets only after exhausting the due set.
        """
        ...

    async def claim_sending(
        self,
        event_key: str,
        event_version: int,
        *,
        subscription_id: str,
        subscription_revision: int,
        expected_state: DeliveryState,
        attempt_number: int,
        started_at: datetime,
        now: datetime,
    ) -> DeliveryEvent | None:
        """CAS one eligible instant event to SENDING.

        ``subscription_id`` and ``subscription_revision`` select the exact
        subscriber delivery. Shared collection event keys can legitimately
        have one delivery per subscription and are not unique delivery IDs.
        ``attempt_number`` is the exact next number expected by the caller.
        ``None`` means state/attempt CAS lost; it must not append a duplicate.
        The persisted subscription must still be the exact active instant-mode
        revision; digest events are claimed only through their envelope. For
        FAILED, the saved retry_at must be present and no later than now. A
        retry claim clears retry_at. UNKNOWN is never claimable and
        implementations never replace an unresolved SENDING attempt.
        """
        ...

    async def mark_sending_unknown(
        self,
        event_key: str,
        event_version: int,
        *,
        subscription_id: str,
        subscription_revision: int,
        expected_attempt_number: int,
        expected_started_at: datetime,
        completed_at: datetime,
    ) -> DeliveryEvent | None:
        """Exact CAS of one still-SENDING attempt to UNKNOWN after recovery.

        The subscription ID and revision select one delivery among subscribers
        sharing the same event key/version. The attempt number and start instant
        identify the exact attempt; ``completed_at`` is the persisted recovery
        observation time. This does not infer platform failure or make the event
        retryable.
        """
        ...

    async def create_envelope(self, envelope: DigestEnvelope) -> DigestEnvelope: ...

    async def record_member_receipt(
        self, envelope_id: str, receipt: DigestMemberReceipt
    ) -> None: ...

    async def recover_stale_sending(
        self, *, before: datetime
    ) -> tuple[DeliveryEvent, ...]:
        """Move expired sending attempts to UNKNOWN; never request blind resend."""
        ...


class ResultOutlet(Protocol):
    """The sole core-to-host result route; modules receive no message port."""

    async def deliver(
        self, invocation: InvocationView, result: CapabilityResult
    ) -> None: ...


class RevisionConflict(RuntimeError):
    """Stable optimistic-concurrency failure with no record or secret values."""

    code = "revision_conflict"

    def __init__(self, resource: str, expected_revision: int, actual_revision: int):
        if (
            not isinstance(resource, str)
            or not resource
            or any(marker in resource for marker in ("/", "\\", "\n"))
        ):
            raise ValueError("resource must be a bounded identifier")
        if not isinstance(expected_revision, int) or not isinstance(
            actual_revision, int
        ):
            raise TypeError("revision values must be integers")
        self.resource = resource
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        super().__init__("expected revision does not match current revision")


class UniqueConstraintViolation(RuntimeError):
    code = "unique_constraint"

    def __init__(self, resource: str):
        if (
            not isinstance(resource, str)
            or not resource
            or any(marker in resource for marker in ("/", "\\", "\n"))
        ):
            raise ValueError("resource must be a bounded identifier")
        self.resource = resource
        super().__init__("unique constraint rejected")


class AuthorizationWindowExpired(PermissionError):
    """A private write reached its SQLite decision point after Grant expiry."""


class ConfigRepository(Protocol):
    """Config reads/CAS, including the internal admin-generation fence.

    ``update`` remains for low-level migration compatibility. Host-authorized
    configuration writes must use ``update_authorized`` so the implementation
    checks the operation grant generation in the same config CAS transaction.
    """

    async def current(self, target: ConfigTarget) -> ConfigSnapshot: ...

    async def update(
        self, target: ConfigTarget, patch: PersistedConfigPatch
    ) -> ConfigSnapshot: ...

    async def update_authorized(
        self,
        target: ConfigTarget,
        patch: PersistedConfigPatch,
        grant: AdminAuthorizationGrant,
    ) -> ConfigSnapshot:
        """CAS config and revalidate UPDATE_CONFIG generation in that transaction."""
        ...

    async def metadata(
        self, target: ConfigTarget, field: str
    ) -> SecretMetadata | None: ...


class SourceRepository(Protocol):
    async def declared(self, module_id: str) -> tuple[SourceDeclaration, ...]: ...


class RecordRepository(Protocol):
    async def collection(
        self, module_id: str, descriptor: CollectionDescriptor, owner: OwnerScope
    ) -> "RecordRepository": ...

    async def get(self, key: str) -> VersionedRecord | None: ...

    async def create(self, key: str, value: JsonObject) -> VersionedRecord: ...

    async def replace(
        self, key: str, value: JsonObject, *, expected_revision: int
    ) -> VersionedRecord: ...

    async def query(self, query: DeclaredIndexQuery) -> RecordPage: ...

    async def delete(self, key: str, *, expected_revision: int) -> None: ...


class IdentityRepository(Protocol):
    async def current_principal(self, principal_id: str) -> Principal | None: ...

    async def find_principal(
        self, identity_namespace: str, external_user_id: str
    ) -> Principal | None: ...

    async def save_principal(self, principal: Principal) -> Principal: ...

    async def current_identity(self, identity_id: str) -> ResolvedIdentity | None: ...

    async def save_identity(
        self, principal_id: str, identity: ResolvedIdentity
    ) -> ResolvedIdentity: ...


class ConversationRepository(Protocol):
    async def current(
        self, adapter_id: str, conversation_id: str
    ) -> ConversationRef | None: ...

    async def save(self, conversation: ConversationRef) -> ConversationRef: ...


class BindingRepository(Protocol):
    """Identity binding persistence with optimistic, transaction-scoped CAS."""

    async def current(self, binding_id: str) -> Binding | None: ...

    async def list_for(
        self, principal_id: str, module_id: str, conversation: ConversationKey
    ) -> tuple[Binding, ...]: ...

    async def current_default(
        self, principal_id: str, module_id: str, conversation: ConversationKey
    ) -> Binding | None: ...

    async def current_default_snapshot(
        self, principal_id: str, module_id: str, conversation: ConversationKey
    ) -> BindingDefaultSnapshot: ...

    async def save(self, binding: Binding, *, expected_revision: int) -> Binding: ...

    async def bind_default(
        self,
        binding: Binding,
        *,
        expected_binding_revision: int,
        expected_default_revision: int,
    ) -> Binding:
        """Atomically write a binding and its default pointer.

        Implementations validate both expected revisions in one UoW and make
        neither visible when either check fails; commit and rollback follow
        the enclosing :class:`UnitOfWork` contract.
        """
        ...

    async def replace_default(
        self,
        principal_id: str,
        module_id: str,
        conversation: ConversationKey,
        binding_id: str,
        *,
        expected_revision: int,
    ) -> Binding: ...

    async def clear_default(
        self,
        principal_id: str,
        module_id: str,
        conversation: ConversationKey,
        *,
        expected_revision: int,
    ) -> None: ...

    async def delete(self, binding_id: str, *, expected_revision: int) -> None: ...


class GrantStore(Protocol):
    async def current_grant(self, grant_id: str) -> Grant | None: ...

    async def list_for(
        self, principal_id: str, module_id: str
    ) -> tuple[Grant, ...]: ...

    async def create_grant(self, grant: Grant, *, expected_revision: int) -> Grant: ...

    async def rotate_grant(self, grant: Grant, *, expected_revision: int) -> Grant: ...

    async def revoke_grant(self, grant: Grant, *, expected_revision: int) -> Grant: ...


class GrantRevocationCoordinator(Protocol):
    """Atomically revoke one exact Grant and invalidate its private work."""

    async def revoke_grant_with_invalidation(
        self, grant: Grant, *, expected_revision: int
    ) -> Grant: ...


class LoginSessionRepository(Protocol):
    async def current(self, session_id: str) -> LoginSession | None: ...

    async def next_generation(
        self, principal_id: str, module_id: str, *, expected_generation: int
    ) -> LoginSession: ...

    async def save(
        self, session: LoginSession, *, expected_generation: int
    ) -> LoginSession: ...

    async def supersede_pending(
        self, principal_id: str, module_id: str, *, generation: int
    ) -> None: ...

    async def expire_pending_on_restart(self) -> int: ...


class AuthorizationRepository(GrantStore, LoginSessionRepository, Protocol):
    """Atomic authorization completion and revoke serialization boundary.

    The caller must perform provider verification and secret availability
    checks before entering this internal port. Implementations re-read and
    validate the persisted session and grant inside one write transaction.
    """

    async def complete_login(
        self,
        session: LoginSession,
        grant: Grant,
        *,
        expected_generation: int,
        expected_grant_revision: int | None,
    ) -> tuple[LoginSession, Grant]:
        """Atomically complete the verified session and create or rotate grant.

        ``expected_grant_revision=None`` means create and requires no existing
        active grant; an integer means rotate that exact grant revision. The
        implementation must lock the principal/module authority shared with
        ``revoke_grant`` so completion and revoke serialize. It must re-check
        that the persisted session is the latest pending, unexpired generation
        with matching owner/module and reject old, superseded, cancelled or
        expired sessions. Any failed session CAS or grant CAS rolls back both
        writes and exposes neither a completed session nor a new grant.
        """
        ...

    async def revoke_grant(self, grant: Grant, *, expected_revision: int) -> Grant:
        """Revoke under the same owner/module serialization as completion."""
        ...


class CacheRepository(Protocol):
    async def get(
        self, request: CacheAccessRequest, *, minimum_source_version: int | None = None
    ) -> CacheLookup: ...

    async def put(
        self,
        request: CacheAccessRequest,
        entry: CacheEntry,
        *,
        expected_revision: int | None = None,
        authorization_deadline: datetime | None = None,
    ) -> CacheEntry: ...

    async def invalidate(
        self,
        request: CacheAccessRequest,
        *,
        authorization_deadline: datetime | None = None,
    ) -> None: ...

    async def current_revision(self, request: CacheAccessRequest) -> int | None: ...


class ResourceRepository(Protocol):
    async def current_revision(
        self, asset_id: str, scope: OwnerScope
    ) -> int | None: ...

    async def register(
        self,
        metadata: ResourceMetadata,
        *,
        expected_revision: int | None = None,
        authorization_deadline: datetime | None = None,
    ) -> ResourceMetadata: ...

    async def registration_state(
        self, asset_id: str, scope: OwnerScope
    ) -> ResourceMetadata | None: ...

    async def metadata(
        self, asset_id: str, scope: OwnerScope
    ) -> ResourceMetadata | None: ...

    async def read(self, asset_id: str, scope: OwnerScope) -> bytes: ...

    async def delete(
        self,
        asset_id: str,
        scope: OwnerScope,
        *,
        authorization_deadline: datetime | None = None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class FileStage:
    operation_id: str
    asset_id: str
    scope: OwnerScope
    size_bytes: int
    metadata: ResourceMetadata | None = None

    def __post_init__(self) -> None:
        for field in ("operation_id", "asset_id"):
            value = getattr(self, field)
            if (
                not isinstance(value, str)
                or not value.strip()
                or any(character in value for character in ("/", "\\", "\n"))
            ):
                raise ValueError("file stage identifiers must be bounded")
        scope = OwnerScope.validate(self.scope)
        if self.metadata is not None:
            try:
                metadata = ResourceMetadata(
                    self.metadata.asset_id,
                    self.metadata.media_type,
                    self.metadata.scope,
                    self.metadata.size_bytes,
                    self.metadata.expires_at,
                    self.metadata.temporary,
                    self.metadata.revision,
                )
            except (AttributeError, TypeError, ValueError):
                raise ValueError("file stage metadata invariants are invalid") from None
            if metadata.asset_id != self.asset_id or metadata.scope != scope:
                raise ValueError("file stage metadata does not match its scope")
            object.__setattr__(self, "metadata", metadata)
        object.__setattr__(self, "scope", scope)
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise ValueError("file stage size must be non-negative")


class SafeFileStore(Protocol):
    async def stage(
        self, operation_id: str, content: bytes, scope: OwnerScope
    ) -> FileStage: ...

    async def commit(
        self, stage: FileStage, metadata: ResourceMetadata
    ) -> ResourceMetadata: ...

    async def read(self, asset_id: str, scope: OwnerScope) -> bytes: ...

    async def discard(self, stage: FileStage) -> None: ...

    async def mark_orphan(self, stage: FileStage, reason: str) -> None: ...

    async def recover_orphans(self) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class SecretOwner:
    principal_id: str
    module_id: str
    field: str
    operation_id: str

    def __post_init__(self) -> None:
        for value in (
            self.principal_id,
            self.field,
            self.operation_id,
        ):
            if (
                not isinstance(value, str)
                or not value.strip()
                or any(character in value for character in ("/", "\\", "\n"))
            ):
                raise ValueError("secret owner fields must be bounded")
        validate_module_id(self.module_id, "secret owner module_id")


def validate_secret_ref(secret_ref: SecretRef) -> SecretRef:
    """Rebuild reference syntax; issuance is checked by SecretReceiptLedger."""
    return SecretRef.validate(secret_ref)


class SecretStore(Protocol):
    """Payload port for the trusted coordinator; modules never receive it."""

    async def stage(
        self,
        value: bytes,
        *,
        target: SecretTarget,
        operation_id: str,
        expected_config_revision: int,
    ) -> SecretReceipt: ...

    async def put(self, value: bytes, *, owner: SecretOwner) -> SecretReceipt: ...

    async def delete(self, secret_ref: SecretRef, *, owner: SecretOwner) -> None: ...

    async def read(
        self, secret_ref: SecretRef, *, owner: SecretOwner
    ) -> bytes | None: ...

    async def mark_orphan(
        self, secret_ref: SecretRef, reason: str, *, owner: SecretOwner
    ) -> None: ...

    async def pending(self, owner: SecretOwner) -> tuple["SecretTransition", ...]: ...

    async def recover(
        self, transition: "SecretTransition", *, owner: SecretOwner
    ) -> "SecretTransition": ...


class SecretReceiptLedger(Protocol):
    """Persistent issuance facts and one-time config claims."""

    async def claim_for_config(
        self,
        receipt: SecretReceipt,
        *,
        target: SecretTarget,
        operation_id: str,
        expected_config_revision: int,
        expected_ledger_revision: int,
    ) -> ClaimedSecretReceipt: ...

    async def finalize_active(
        self, receipt: ClaimedSecretReceipt, *, metadata_revision: int
    ) -> SecretReceipt: ...

    async def mark_cas_conflict(self, receipt: SecretReceipt) -> SecretReceipt: ...

    async def pending(self, target: SecretTarget) -> tuple[SecretReceipt, ...]: ...


class SecretCompensationState(str, Enum):
    STAGED = "staged"
    CAS_CONFLICT = "cas_conflict"
    CLEANED = "cleaned"
    ORPHAN = "orphan"
    TOMBSTONED = "tombstoned"
    DELETE_FAILED = "delete_failed"
    RECOVERABLE = "recoverable"


@dataclass(frozen=True, slots=True)
class SecretTransition:
    state: SecretCompensationState
    secret_ref: SecretRef | None = None
    old_secret_ref: SecretRef | None = None
    operation_id: str = ""
    owner: SecretOwner | None = None
    metadata_revision: int = 0
    retry_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.state, SecretCompensationState):
            raise TypeError("secret transition state must be a SecretCompensationState")
        refs = []
        for ref in (self.secret_ref, self.old_secret_ref):
            if ref is not None:
                refs.append(validate_secret_ref(ref))
            else:
                refs.append(None)
        object.__setattr__(self, "secret_ref", refs[0])
        object.__setattr__(self, "old_secret_ref", refs[1])
        if not isinstance(self.operation_id, str) or not self.operation_id.strip():
            raise ValueError("secret transition requires an operation_id")
        if not isinstance(self.owner, SecretOwner):
            raise TypeError("secret transition requires a SecretOwner")
        for ref in (self.secret_ref, self.old_secret_ref):
            if ref is not None and (
                ref.principal_id != self.owner.principal_id
                or ref.module_id != self.owner.module_id
                or ref.field != self.owner.field
                or ref.operation_id != self.owner.operation_id
            ):
                raise ValueError(
                    "secret transition ref is not owned by its coordinator"
                )
        if self.owner.operation_id != self.operation_id:
            raise ValueError("secret transition operation does not match its owner")
        if (
            isinstance(self.metadata_revision, bool)
            or not isinstance(self.metadata_revision, int)
            or self.metadata_revision < 0
            or isinstance(self.retry_count, bool)
            or not isinstance(self.retry_count, int)
            or self.retry_count < 0
        ):
            raise ValueError("secret transition counters must be non-negative")
        if self.state is SecretCompensationState.STAGED and self.secret_ref is None:
            raise ValueError("staged transition requires a new secret ref")
        if (
            self.state
            in {
                SecretCompensationState.ORPHAN,
                SecretCompensationState.DELETE_FAILED,
                SecretCompensationState.RECOVERABLE,
            }
            and self.secret_ref is None
            and self.old_secret_ref is None
        ):
            raise ValueError("cleanup transition requires a ref to reconcile")


# Domain terminology aliases keep downstream cards on one frozen port object.
PrincipalRepository = IdentityRepository
SessionRepository = LoginSessionRepository
DefaultBindingRepository = BindingRepository
AssetRepository = ResourceRepository
FileRepository = SafeFileStore
