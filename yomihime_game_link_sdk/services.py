from __future__ import annotations

from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from .contexts import InvocationView, MessageContext
from .declarations import CapabilityReference
from .results import CapabilityResult
from .storage import (
    CacheEntry,
    CacheLookup,
    CacheQuery,
    GrantReference,
    JsonObject,
    OwnerScope,
    RecordCollection,
    SecretMetadata,
)
from .storage import JsonValue as JsonValue
from .subscriptions import (
    Collector,
    SubscriptionEvaluator,
    SubscriptionRequest,
    SubscriptionView,
)


@dataclass(frozen=True, slots=True)
class ConfigSnapshot:
    revision: int
    values: JsonObject
    secret_metadata: tuple[SecretMetadata, ...] = ()
    target: "ConfigTarget | None" = None

    def __post_init__(self) -> None:
        if isinstance(self.values, Mapping):
            object.__setattr__(self, "values", MappingProxyType(dict(self.values)))
        if self.secret_metadata is not None:
            object.__setattr__(self, "secret_metadata", tuple(self.secret_metadata))


@dataclass(frozen=True, slots=True)
class ResolvedIdentity:
    identity_id: str
    provider: str
    subject: str
    principal_id: str | None = None


@dataclass(frozen=True, slots=True)
class ConfigTarget:
    principal_id: str
    module_id: str


@dataclass(frozen=True, slots=True)
class BindingView:
    binding_id: str
    revision: int
    identity: ResolvedIdentity
    is_default: bool


@dataclass(frozen=True, slots=True)
class HttpRequest:
    source_id: str
    path: str
    method: str = "GET"
    query: tuple[tuple[str, str], ...] = ()
    body: bytes | None = None
    headers: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        if self.query is not None:
            object.__setattr__(self, "query", tuple(self.query))
        if isinstance(self.headers, Mapping):
            object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes

    def __post_init__(self) -> None:
        if isinstance(self.headers, Mapping):
            object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


@dataclass(frozen=True, slots=True)
class ResourceReference:
    asset_id: str
    media_type: str
    scope: OwnerScope


class ConfigView(Protocol):
    async def current(self) -> ConfigSnapshot: ...


class IdentityResolver(Protocol):
    async def default_identity(
        self, invocation: InvocationView
    ) -> ResolvedIdentity | None: ...


class AccountOperations(Protocol):
    """Command-authorized account operations; the issuer validates provenance."""

    async def status(self, invocation: InvocationView) -> GrantReference | None: ...

    async def begin_login(self, invocation: InvocationView) -> str: ...

    async def bind(
        self, invocation: InvocationView, identity: ResolvedIdentity
    ) -> BindingView: ...

    async def bindings(self, invocation: InvocationView) -> tuple[BindingView, ...]: ...

    async def unbind(
        self, invocation: InvocationView, binding_id: str, *, expected_revision: int
    ) -> None: ...

    async def revoke(
        self, invocation: InvocationView, grant: GrantReference
    ) -> None: ...


class SubscriptionOperations(Protocol):
    """Module-facing subscription operations.

    Implementations must validate the exact B03 issuer object and require
    COMMAND origin before every operation. InvocationView.origin is not proof
    on its own, and internal or Tool invocation cannot promote authority.
    Executable create_request/revise_request use collector command input.
    Creation omits ID/revision; Core generates both. Repeated creates can make
    distinct subscriptions sharing a collection job; creation is not idempotent.
    Revision requires ID/revision and current CAS. cancel is revision-checked
    and removes this association while preserving other subscribers' jobs/data.
    SubscriptionView is matcher-facing and has no collector command data.
    A paused owner with fresh current proof may list/revise/cancel, not create;
    old issued contexts and service handles cannot survive module exit.
    """

    async def create_request(
        self, invocation: InvocationView, request: SubscriptionRequest
    ) -> SubscriptionView: ...

    async def revise_request(
        self, invocation: InvocationView, request: SubscriptionRequest
    ) -> SubscriptionView: ...

    async def list_current(
        self, invocation: InvocationView
    ) -> tuple[SubscriptionView, ...]: ...

    async def cancel(
        self,
        invocation: InvocationView,
        subscription_id: str,
        *,
        expected_revision: int,
    ) -> None: ...


class ModuleRecords(Protocol):
    """Returns a collection pre-bound to the invocation's authorized scope."""

    async def collection(self, name: str) -> RecordCollection: ...


class CacheAccess(Protocol):
    """Invocation-bound cache; Core derives the current owner's partition.

    lookup preserves HIT/MISS/EXPIRED/REJECTED. Only HIT carries an entry.
    Current authorization, invocation, deadline and service failures raise;
    cancellation propagates. They are never cache misses.
    """

    async def get(self, key: str) -> CacheEntry | None:
        """Default-partition lookup projection: full HIT entry or nonhit None.

        Preserves key, payload, expiry and revision; discards the distinction
        between MISS, EXPIRED and stored-row REJECTED. Does not catch errors.
        """
        ...

    async def lookup(self, request: CacheQuery) -> CacheLookup:
        """Canonical query with optional visibility, never an owner/proof selector."""
        ...

    async def put(
        self, key: str, payload: JsonObject, *, ttl_seconds: float
    ) -> CacheEntry: ...


class SourceHttp(Protocol):
    async def fetch(self, request: HttpRequest) -> HttpResponse: ...


class ResourceAccess(Protocol):
    """Register declared content in the scope bound by InvocationServiceBinder."""

    async def register(
        self, asset_id: str, media_type: str, content: bytes
    ) -> ResourceReference: ...

    async def read(self, asset_id: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class CallerCapability:
    """Issuer-bound identity of the capability making a dependency call.

    This is descriptive data.  A trusted binder issues and retains the exact
    object on a capability-bound DependencyInvoker; modules receive neither an
    issuer nor a caller argument with which to select another identity.
    """

    module_id: str
    capability_id: str
    registry_revision: int
    module_epoch: int


class DependencyInvoker(Protocol):
    """Invoker bound by the host to one issuer-created caller capability."""

    @property
    def caller_capability(self) -> CallerCapability: ...

    async def invoke(
        self,
        invocation: InvocationView,
        capability: str | CapabilityReference,
        parameters: JsonObject,
    ) -> "CapabilityResult": ...


class TaskScope(Protocol):
    @property
    def deadline_monotonic(self) -> float | None: ...

    def create_task(self, work: Awaitable[object], *, name: str) -> None: ...


@dataclass(frozen=True, slots=True)
class ModuleServices:
    config: ConfigView
    identities: IdentityResolver
    accounts: AccountOperations
    subscriptions: SubscriptionOperations
    scopes: "InvocationServiceBinder"


class MessageAccess(Protocol):
    """Read the current invocation's message without exposing host objects."""

    async def read(self) -> MessageContext: ...


class InvocationServices(Protocol):
    """Scope-sensitive handles bound by the core to one issued invocation."""

    @property
    def invocation(self) -> InvocationView: ...

    @property
    def message(self) -> MessageAccess: ...

    @property
    def records(self) -> ModuleRecords: ...

    @property
    def cache(self) -> CacheAccess: ...

    @property
    def http(self) -> SourceHttp: ...

    @property
    def resources(self) -> ResourceAccess: ...

    @property
    def dependencies(self) -> DependencyInvoker: ...

    @property
    def tasks(self) -> TaskScope: ...


class InvocationServiceBinder(Protocol):
    async def bind(self, invocation: InvocationView) -> InvocationServices: ...


@runtime_checkable
class CapabilityHandler(Protocol):
    async def invoke(
        self, context: InvocationView, parameters: JsonObject
    ) -> "CapabilityResult": ...


@dataclass(frozen=True, slots=True)
class ModuleHandlers:
    capabilities: Mapping[str, CapabilityHandler]
    collectors: Mapping[str, "Collector"]
    evaluators: Mapping[str, "SubscriptionEvaluator"]

    def __post_init__(self) -> None:
        if isinstance(self.capabilities, Mapping):
            object.__setattr__(
                self, "capabilities", MappingProxyType(dict(self.capabilities))
            )
        if isinstance(self.collectors, Mapping):
            object.__setattr__(
                self, "collectors", MappingProxyType(dict(self.collectors))
            )
        if isinstance(self.evaluators, Mapping):
            object.__setattr__(
                self, "evaluators", MappingProxyType(dict(self.evaluators))
            )


class HealthStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CapabilityHealth:
    status: HealthStatus
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class HealthReport:
    capabilities: Mapping[str, CapabilityHealth]

    def __post_init__(self) -> None:
        if isinstance(self.capabilities, Mapping):
            object.__setattr__(
                self, "capabilities", MappingProxyType(dict(self.capabilities))
            )


@runtime_checkable
class ModuleInstance(Protocol):
    def handlers(self) -> ModuleHandlers: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def check_health(self) -> HealthReport: ...


@runtime_checkable
class ModuleFactory(Protocol):
    """Factory ABI v1: one async ``create`` call with Core-issued services.

    Implementations are resolved only after static manifest validation and
    package-root containment checks. A factory receives no host object, admin
    authority, filesystem root, or unrestricted service container.
    """

    async def create(self, services: ModuleServices) -> ModuleInstance: ...
