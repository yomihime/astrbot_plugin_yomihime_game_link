"""The narrow, scope-bound services supplied to an enabled module."""

from __future__ import annotations

from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from .contexts import InvocationView
from .display import _asset
from .manifests import _identifier as _manifest_identifier
from .results import CapabilityResult
from .storage import (
    CacheEntry,
    GrantReference,
    JsonObject,
    OwnerScope,
    RecordCollection,
    freeze_json,
)

# Resolve the recursive JsonObject alias during get_type_hints on public DTOs.
from .storage import JsonValue as JsonValue
from .subscriptions import Collector, SubscriptionEvaluator, SubscriptionView


@dataclass(frozen=True, slots=True)
class ConfigSnapshot:
    revision: int
    values: JsonObject

    def __post_init__(self) -> None:
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 1
        ):
            raise ValueError("config revision must be at least 1")
        snapshot = freeze_json(self.values)
        if not isinstance(snapshot, Mapping):
            raise TypeError("config values must be a JSON object")
        object.__setattr__(self, "values", snapshot)


@dataclass(frozen=True, slots=True)
class ResolvedIdentity:
    identity_id: str
    provider: str
    subject: str

    def __post_init__(self) -> None:
        for field in ("identity_id", "provider", "subject"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be non-empty text")


@dataclass(frozen=True, slots=True)
class BindingView:
    binding_id: str
    revision: int
    identity: ResolvedIdentity
    is_default: bool

    def __post_init__(self) -> None:
        if not isinstance(self.binding_id, str) or not self.binding_id.strip():
            raise ValueError("binding_id must be non-empty text")
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 1
        ):
            raise ValueError("binding revision must be a positive integer")
        if not isinstance(self.identity, ResolvedIdentity):
            raise TypeError("binding identity must be a ResolvedIdentity")
        if not isinstance(self.is_default, bool):
            raise TypeError("binding default marker must be a bool")


@dataclass(frozen=True, slots=True)
class HttpRequest:
    source_id: str
    path: str
    method: str = "GET"
    query: tuple[tuple[str, str], ...] = ()
    body: bytes | None = None
    headers: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        try:
            _manifest_identifier(self.source_id, "source_id")
        except (TypeError, ValueError) as exc:
            raise ValueError("source_id must be a declared identifier") from exc
        if (
            not self.path
            or "://" in self.path
            or self.path.startswith("//")
            or "\\" in self.path
            or ".." in self.path.split("/")
        ):
            raise ValueError(
                "HTTP requests require a declared source and relative path"
            )
        if self.method not in {"GET", "POST"}:
            raise ValueError("HTTP method must be GET or POST")
        if self.method == "GET" and self.body is not None:
            raise ValueError("GET requests cannot carry a body")
        if self.body is not None and not isinstance(self.body, bytes):
            raise TypeError("HTTP body must be bytes")
        query = tuple(self.query)
        if any(not isinstance(pair, tuple) or len(pair) != 2 for pair in query):
            raise TypeError("HTTP query must contain key/value tuples")
        frozen_query = tuple((pair[0], pair[1]) for pair in query)
        if any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or not key
            or not value
            for key, value in frozen_query
        ):
            raise ValueError("HTTP query values must be non-empty text")
        object.__setattr__(self, "query", frozen_query)
        headers = self.headers or {}
        if not isinstance(headers, Mapping):
            raise TypeError("HTTP headers must be a mapping")
        if any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or not key
            or key.lower() in {"authorization", "cookie"}
            for key, value in headers.items()
        ):
            raise ValueError("HTTP credentials are managed by the declared source")
        object.__setattr__(self, "headers", MappingProxyType(dict(headers)))


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes

    def __post_init__(self) -> None:
        if (
            isinstance(self.status_code, bool)
            or not isinstance(self.status_code, int)
            or not 100 <= self.status_code <= 599
        ):
            raise ValueError("HTTP status code must be between 100 and 599")
        if not isinstance(self.headers, Mapping):
            raise TypeError("HTTP headers must be a mapping")
        if not isinstance(self.body, bytes):
            raise TypeError("HTTP response body must be bytes")
        if any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in self.headers.items()
        ):
            raise TypeError("HTTP headers must contain text values")
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


@dataclass(frozen=True, slots=True)
class ResourceReference:
    asset_id: str
    media_type: str
    scope: OwnerScope

    def __post_init__(self) -> None:
        _asset(self.asset_id)
        if not isinstance(self.media_type, str) or not self.media_type.strip():
            raise ValueError("media_type must be non-empty text")
        if not isinstance(self.scope, OwnerScope):
            raise TypeError("resource scope must be an OwnerScope")


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
    async def create(
        self, invocation: InvocationView, subscription: SubscriptionView
    ) -> SubscriptionView: ...

    async def revise(
        self, invocation: InvocationView, subscription: SubscriptionView
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
    async def get(self, key: str) -> CacheEntry | None: ...

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


class DependencyInvoker(Protocol):
    async def invoke(
        self, invocation: InvocationView, capability_id: str, parameters: JsonObject
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


class InvocationServices(Protocol):
    """Scope-sensitive handles bound by the core to one issued invocation."""

    @property
    def invocation(self) -> InvocationView: ...

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
        for name, handlers in (
            ("capabilities", self.capabilities),
            ("collectors", self.collectors),
            ("evaluators", self.evaluators),
        ):
            if not isinstance(handlers, Mapping) or any(
                not isinstance(key, str) or not key for key in handlers
            ):
                raise ValueError(f"{name} must map non-empty identifiers")
            object.__setattr__(self, name, MappingProxyType(dict(handlers)))


class HealthStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CapabilityHealth:
    status: HealthStatus
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, HealthStatus):
            raise TypeError("health status must be a HealthStatus")
        if self.reason is not None and (
            not isinstance(self.reason, str) or not self.reason
        ):
            raise ValueError("health reason must be non-empty text when present")


@dataclass(frozen=True, slots=True)
class HealthReport:
    capabilities: Mapping[str, CapabilityHealth]

    def __post_init__(self) -> None:
        if not isinstance(self.capabilities, Mapping) or any(
            not isinstance(capability_id, str)
            or not capability_id
            or not isinstance(health, CapabilityHealth)
            for capability_id, health in self.capabilities.items()
        ):
            raise TypeError("health report requires capability health mappings")
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
    async def create(self, services: ModuleServices) -> ModuleInstance: ...
