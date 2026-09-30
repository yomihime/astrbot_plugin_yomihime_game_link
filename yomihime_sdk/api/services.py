"""The narrow, scope-bound services supplied to an enabled module."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from .contexts import InvocationOrigin, InvocationView
from .display import _asset
from .manifests import CapabilityReference, ConfigField, ConfigUpdateMode
from .manifests import _identifier as _manifest_identifier
from .results import CapabilityResult, FactDocument
from .storage import (
    CacheEntry,
    CacheLookup,
    CacheVisibility,
    GrantReference,
    JsonObject,
    OwnerScope,
    RecordCollection,
    SecretMetadata,
    SecretReceipt,
    SecretReceiptState,
    SecretRef,
    SecretTarget,
    freeze_json,
    validate_module_id,
)

# Resolve the recursive JsonObject alias during get_type_hints on public DTOs.
from .storage import JsonValue as JsonValue
from .subscriptions import (
    Collector,
    ConversationKind,
    ConversationRef,
    DeliveryEvent,
    SubscriptionEvaluator,
    SubscriptionRequest,
    SubscriptionView,
)

_UNSAFE_MAPPING = object()


def _mapping_items_safely(value: Mapping[object, object]):
    try:
        return tuple(value.items())
    except Exception:
        return _UNSAFE_MAPPING


@dataclass(frozen=True, slots=True)
class ConfigSnapshot:
    revision: int
    values: JsonObject
    secret_metadata: tuple[SecretMetadata, ...] = ()
    target: "ConfigTarget | None" = None

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
        try:
            metadata = tuple(
                SecretMetadata.validate(item) for item in self.secret_metadata
            )
        except (AttributeError, TypeError, ValueError):
            raise ValueError("secret metadata invariants are invalid") from None
        if len({item.field for item in metadata}) != len(metadata):
            raise ValueError("secret metadata fields must be unique")
        for item in metadata:
            if item.secret_ref is not None and item.secret_ref.field != item.field:
                raise ValueError("secret metadata field does not match its ref")
        _validate_config_snapshot_values(snapshot)
        if self.target is not None:
            object.__setattr__(self, "target", ConfigTarget.validate(self.target))
        object.__setattr__(self, "values", snapshot)
        object.__setattr__(self, "secret_metadata", metadata)


@dataclass(frozen=True, slots=True)
class ResolvedIdentity:
    identity_id: str
    provider: str
    subject: str
    principal_id: str | None = None

    def __post_init__(self) -> None:
        for field in ("identity_id", "provider", "subject"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be non-empty text")
        if self.principal_id is not None and (
            not isinstance(self.principal_id, str) or not self.principal_id.strip()
        ):
            raise ValueError("principal_id must be non-empty text when present")


@dataclass(frozen=True, slots=True)
class ConfigTarget:
    principal_id: str
    module_id: str

    def __post_init__(self) -> None:
        if type(self.principal_id) is not str or not self.principal_id.strip():
            raise ValueError("config target fields must be bounded")
        if any(character in self.principal_id for character in ("/", "\\", "\n")):
            raise ValueError("config target fields must be bounded")
        try:
            validate_module_id(self.module_id)
        except ValueError:
            raise ValueError("config target fields must be bounded") from None

    @classmethod
    def validate(cls, value: object) -> "ConfigTarget":
        if not isinstance(value, cls):
            raise TypeError("config target must be a ConfigTarget")
        try:
            return cls(value.principal_id, value.module_id)
        except (AttributeError, TypeError, ValueError):
            raise ValueError("config target invariants are invalid") from None


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


_HTTP_HEADER_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+\Z")
_CREDENTIAL_HEADER_MARKERS = (
    "auth",
    "authorization",
    "authentication",
    "cookie",
    "bearer",
    "credential",
    "password",
    "token",
)
_TRANSPORT_CONTROLLED_REQUEST_HEADERS = frozenset(
    {
        "accept-encoding",
        "connection",
        "content-length",
        "host",
        "keep-alive",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)


class SourceHttpError(RuntimeError):
    """Stable, sanitized failure from the source HTTP boundary."""

    _MESSAGES = {
        "source_not_declared": "HTTP source is not declared",
        "request_rejected": "HTTP request is outside the declared source policy",
        "request_too_large": "HTTP request exceeds the source policy limit",
        "response_too_large": "HTTP response exceeds the source policy limit",
        "rate_limited": "HTTP source quota exceeded",
        "concurrency_limited": "HTTP source concurrency limit exceeded",
        "timeout": "HTTP source request timed out",
        "cancelled": "HTTP source request was cancelled",
        "credentials_unavailable": "HTTP source credentials are unavailable",
        "transport_failed": "HTTP source transport failed",
        "invalid_response": "HTTP source returned an invalid response",
        "upstream_error": "HTTP source returned an upstream error",
        "redirect_disallowed": "HTTP source redirect is not allowed",
    }

    def __init__(self, code: str, *, status_code: int | None = None) -> None:
        if code not in self._MESSAGES:
            code = "transport_failed"
        self.code = code
        self.status_code = status_code
        super().__init__(self._MESSAGES[code])


def _inspect_http_headers(headers: Mapping[str, str]):
    """Normalize a header mapping without exposing mapping or value errors."""
    try:
        items = tuple(headers.items())
        normalized: dict[str, str] = {}
        for key, value in items:
            if type(key) is not str or type(value) is not str:
                raise ValueError
            clean_key = str.strip(key)
            clean_value = str.strip(value)
            normalized_key = unicodedata.normalize("NFKC", clean_key)
            if (
                not clean_key
                or clean_key != key
                or normalized_key != clean_key
                or not _HTTP_HEADER_NAME.fullmatch(normalized_key)
                or any(
                    marker in normalized_key.lower()
                    for marker in _CREDENTIAL_HEADER_MARKERS
                )
                or any(
                    ord(character) < 32 or 0x7F <= ord(character) <= 0x9F
                    for character in normalized_key
                )
                or clean_value != value
                or any(
                    ord(character) < 32 or 0x7F <= ord(character) <= 0x9F
                    for character in clean_value
                )
            ):
                raise ValueError
            if normalized_key in normalized:
                raise ValueError
            normalized[normalized_key] = clean_value
        return MappingProxyType(normalized)
    except Exception:
        return _UNSAFE_MAPPING


def _safe_http_headers(headers: Mapping[str, str], *, response: bool = False):
    result = _inspect_http_headers(headers)
    if result is _UNSAFE_MAPPING:
        message = (
            "HTTP response headers cannot be inspected safely"
            if response
            else "HTTP credentials are managed by the declared source"
        )
        raise ValueError(message)
    return result


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
        except (TypeError, ValueError):
            raise ValueError("source_id must be a declared identifier") from None
        path = (
            unicodedata.normalize("NFKC", self.path)
            if isinstance(self.path, str)
            else ""
        )
        if (
            not path
            or not path.startswith("/")
            or "://" in path
            or path.startswith("//")
            or "\\" in path
            or "?" in path
            or "#" in path
            or any(segment in {".", ".."} for segment in path.split("/"))
            or "%" in path
            or any(
                character.isspace()
                or ord(character) < 32
                or 0x7F <= ord(character) <= 0x9F
                for character in path
            )
        ):
            raise ValueError(
                "HTTP requests require a declared source and relative path"
            )
        object.__setattr__(self, "path", path)
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
            or any(
                ord(character) < 32 or 0x7F <= ord(character) <= 0x9F
                for character in key
            )
            or any(
                ord(character) < 32 or 0x7F <= ord(character) <= 0x9F
                for character in value
            )
            for key, value in frozen_query
        ):
            raise ValueError("HTTP query values must be non-empty text")
        object.__setattr__(self, "query", frozen_query)
        headers = {} if self.headers is None else self.headers
        if not isinstance(headers, Mapping):
            raise TypeError("HTTP headers must be a mapping")
        safe_headers = _safe_http_headers(headers)
        if any(
            key.lower() in _TRANSPORT_CONTROLLED_REQUEST_HEADERS for key in safe_headers
        ):
            raise ValueError("HTTP request contains a transport-controlled header")
        object.__setattr__(self, "headers", safe_headers)


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
        object.__setattr__(
            self, "headers", _safe_http_headers(self.headers, response=True)
        )


@dataclass(frozen=True, slots=True)
class ResourceReference:
    asset_id: str
    media_type: str
    scope: OwnerScope

    def __post_init__(self) -> None:
        _asset(self.asset_id)
        if not isinstance(self.media_type, str) or not self.media_type.strip():
            raise ValueError("media_type must be non-empty text")
        object.__setattr__(self, "scope", OwnerScope.validate(self.scope))


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
    The four original methods retain their exact B03 signatures. B04 callers
    use create_request/revise_request because SubscriptionView is deliberately
    matcher-facing and has no collector command data.
    """

    async def create(
        self, invocation: InvocationView, subscription: SubscriptionView
    ) -> SubscriptionView: ...

    async def revise(
        self, invocation: InvocationView, subscription: SubscriptionView
    ) -> SubscriptionView: ...

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


class TrustedConversationResolver(Protocol):
    """Host resolver for a command's issuer-validated conversation context.

    Implementations must validate the exact invocation with the issuing
    ContextIssuer before looking up an adapter conversation. They may query
    only the adapter/conversation carried by that invocation, and must return
    a reference matching both values. InvocationView itself is descriptive and
    is never accepted as proof merely because its origin says COMMAND.
    """

    async def resolve(self, invocation: InvocationView) -> ConversationRef | None: ...


class TrustedPersistedRouteResolver(Protocol):
    """Revalidate a saved background recipient route against its owner.

    The injected host implementation checks the current adapter/conversation
    route and proves that its complete returned reference equals the persisted
    route. The DTO and stored kind field are never authority.
    """

    async def resolve_current(
        self, owner_id: str, persisted_recipient: ConversationRef
    ) -> ConversationRef | None:
        """Return the exact currently valid persisted route or None.

        This method can attest any supported route for schedule creation.
        Callers handling AUTHORIZED scope must use resolve_private.
        """
        ...

    async def resolve_private(
        self, owner_id: str, persisted_recipient: ConversationRef
    ) -> ConversationRef | None: ...


@dataclass(frozen=True, slots=True)
class AuthorizedRecipient:
    """Descriptive resolver result, never authority on its own."""

    conversation: ConversationRef | None
    refusal_code: str | None = None

    def __post_init__(self) -> None:
        if self.conversation is None:
            if self.refusal_code != "private_recipient_required":
                raise ValueError(
                    "recipient refusal requires private_recipient_required"
                )
        else:
            if not isinstance(self.conversation, ConversationRef):
                raise TypeError("conversation must be a ConversationRef")
            if self.conversation.kind is not ConversationKind.DIRECT:
                raise ValueError("authorized recipients must be direct conversations")
            if self.refusal_code is not None:
                raise ValueError("resolved recipient cannot include a refusal")

    @classmethod
    def refused(cls) -> "AuthorizedRecipient":
        return cls(None, "private_recipient_required")


async def resolve_authorized_recipient(
    invocation: InvocationView, resolver: TrustedConversationResolver
) -> AuthorizedRecipient:
    """Resolve the exact command context through a trusted injected resolver.

    The return DTO and its ConversationRef are descriptive values, not proof.
    Only the resolver's issuer validation attests the associated route.
    """
    if (
        not isinstance(invocation, InvocationView)
        or invocation.origin is not InvocationOrigin.COMMAND
        or invocation.adapter_id is None
        or invocation.conversation_id is None
    ):
        return AuthorizedRecipient.refused()
    try:
        conversation = await resolver.resolve(invocation)
    except Exception:
        return AuthorizedRecipient.refused()
    if (
        not isinstance(conversation, ConversationRef)
        or conversation.kind is not ConversationKind.DIRECT
        or conversation.adapter_id != invocation.adapter_id
        or conversation.conversation_id != invocation.conversation_id
    ):
        return AuthorizedRecipient.refused()
    return AuthorizedRecipient(conversation)


@dataclass(frozen=True, slots=True)
class CommandOutput:
    result: CapabilityResult

    def __post_init__(self) -> None:
        if not isinstance(self.result, CapabilityResult):
            raise TypeError("command output requires CapabilityResult")


@dataclass(frozen=True, slots=True)
class ToolOutput:
    facts: FactDocument

    def __post_init__(self) -> None:
        if not isinstance(self.facts, FactDocument):
            raise TypeError("tool output requires public FactDocument")


@dataclass(frozen=True, slots=True)
class SubscriptionOutput:
    event: DeliveryEvent

    def __post_init__(self) -> None:
        if not isinstance(self.event, DeliveryEvent):
            raise TypeError("subscription output requires DeliveryEvent")


class ModuleRecords(Protocol):
    """Returns a collection pre-bound to the invocation's authorized scope."""

    async def collection(self, name: str) -> RecordCollection: ...


class CacheAccess(Protocol):
    async def get(self, key: str) -> CacheEntry | None: ...

    async def lookup(self, request: "CacheAccessRequest") -> CacheLookup: ...

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

    def __post_init__(self) -> None:
        validate_module_id(self.module_id)
        if type(self.capability_id) is not str or not self.capability_id.strip():
            raise ValueError("caller capability_id must be bounded text")
        for field in ("registry_revision", "module_epoch"):
            value = getattr(self, field)
            if type(value) is not int or value < 1:
                raise ValueError(f"caller {field} must be a positive integer")


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
    """Factory ABI v1: one async ``create`` call with Core-issued services.

    Implementations are resolved only after static manifest validation and
    package-root containment checks. A factory receives no host object, admin
    authority, filesystem root, or unrestricted service container.
    """

    async def create(self, services: ModuleServices) -> ModuleInstance: ...


ConfigPatchMode = ConfigUpdateMode


@dataclass(frozen=True, slots=True, repr=False)
class SecretMaterial:
    """Explicit sensitive input accepted only by a declared sensitive field."""

    value: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.value, bytes) or not self.value:
            raise ValueError("secret material must be non-empty bytes")

    def __repr__(self) -> str:
        return "SecretMaterial(<redacted>)"


_SENSITIVE_FIELD = re.compile(
    r"(?:^|[_\-.])(api[_\-.]?key|auth(?:orization)?|bearer|cookie|credential|"
    r"key|password|private|refresh|secret|token)(?:$|[_\-.])",
    re.IGNORECASE,
)


def _validate_config_snapshot_values(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and _is_sensitive_field(key):
                if not isinstance(item, str) or item not in {
                    "configured",
                    "unset",
                    "redacted",
                    "disabled",
                    "unavailable",
                    "present",
                }:
                    raise ValueError("config snapshots cannot expose sensitive values")
            _validate_config_snapshot_values(item)
    elif isinstance(value, tuple):
        for item in value:
            _validate_config_snapshot_values(item)


def _is_sensitive_field(field: str) -> bool:
    if type(field) is not str:
        return True
    normalized = re.sub(r"[^a-z0-9]", "", field.lower())
    return bool(_SENSITIVE_FIELD.search(field)) or any(
        marker in normalized
        for marker in (
            "apikey",
            "accesskey",
            "auth",
            "bearer",
            "cookie",
            "credential",
            "password",
            "passwd",
            "pwd",
            "pin",
            "privatekey",
            "refreshtoken",
            "sessionid",
            "secret",
            "signingkey",
            "token",
        )
    )


@dataclass(frozen=True, slots=True)
class ConfigFieldUpdate:
    field: str
    mode: ConfigPatchMode
    value: JsonValue | None = None
    secret: SecretMaterial | None = None
    secret_ref: SecretRef | None = None
    receipt: SecretReceipt | None = None

    @classmethod
    def validate(cls, value: object) -> "ConfigFieldUpdate":
        if not isinstance(value, cls):
            raise TypeError("config update must be a ConfigFieldUpdate")
        try:
            return cls(
                value.field,
                value.mode,
                value.value,
                value.secret,
                value.secret_ref,
                value.receipt,
            )
        except (AttributeError, TypeError, ValueError):
            raise ValueError("config update invariants are invalid") from None

    def __post_init__(self) -> None:
        if type(self.field) is not str or not self.field.strip():
            raise ValueError("config field must be non-empty text")
        if any(character.isspace() or character in "/\\" for character in self.field):
            raise ValueError("config field must be a bounded identifier")
        if not isinstance(self.mode, ConfigPatchMode):
            raise TypeError("config update mode must be a ConfigPatchMode")
        if isinstance(self.value, SecretMaterial):
            if self.secret is not None:
                raise ValueError("secret material was supplied twice")
            object.__setattr__(self, "secret", self.value)
            object.__setattr__(self, "value", None)
        if self.secret_ref is not None:
            ref = SecretRef.validate(self.secret_ref)
            if ref.field != self.field:
                raise ValueError("secret metadata ref field does not match update")
            object.__setattr__(self, "secret_ref", ref)
        if self.receipt is not None:
            receipt = SecretReceipt.validate(self.receipt)
            if receipt.secret_ref.field != self.field:
                raise ValueError("secret receipt field does not match update")
            object.__setattr__(self, "receipt", receipt)
        if self.receipt is not None and self.secret_ref is not None:
            if self.receipt.secret_ref != self.secret_ref:
                raise ValueError("secret receipt and ref do not match")
        if self.mode is not ConfigPatchMode.REPLACE and (
            self.value is not None
            or self.secret is not None
            or self.secret_ref is not None
            or self.receipt is not None
        ):
            raise ValueError("keep and clear updates cannot contain values")
        if self.secret is not None and not isinstance(self.secret, SecretMaterial):
            raise TypeError("secret updates require SecretMaterial")
        if (
            self.mode is ConfigPatchMode.REPLACE
            and self.value is None
            and self.secret is None
            and self.secret_ref is None
            and self.receipt is None
        ):
            raise ValueError("replace updates require a value or SecretMaterial")
        if (
            sum(
                item is not None
                for item in (self.value, self.secret, self.secret_ref, self.receipt)
            )
            > 1
        ):
            raise ValueError("replace updates cannot contain multiple value kinds")
        if self.secret is not None and (
            self.secret_ref is not None or self.receipt is not None
        ):
            raise ValueError("secret material and metadata ref cannot be combined")
        if self.value is not None:
            snapshot = freeze_json(self.value)
            object.__setattr__(self, "value", snapshot)
            if _is_sensitive_field(self.field):
                raise ValueError("sensitive config values require SecretMaterial")


@dataclass(frozen=True, slots=True)
class ConfigPatch:
    expected_revision: int
    updates: tuple[ConfigFieldUpdate, ...]
    declared_fields: tuple[ConfigField, ...] = ()
    operation_id: str | None = None
    target: ConfigTarget | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.expected_revision, bool)
            or not isinstance(self.expected_revision, int)
            or self.expected_revision < 0
        ):
            raise ValueError("expected config revision must be a non-negative integer")
        updates = tuple(ConfigFieldUpdate.validate(item) for item in self.updates)
        if len({item.field for item in updates}) != len(updates):
            raise ValueError("config patch fields must be unique")
        declared_fields = tuple(self.declared_fields)
        if any(not isinstance(item, ConfigField) for item in declared_fields):
            raise TypeError("declared_fields require ConfigField values")
        declarations = {item.name: item for item in declared_fields}
        if len(declarations) != len(declared_fields):
            raise ValueError("declared config fields must be unique")
        for update in updates:
            declaration = declarations.get(update.field)
            if declared_fields and declaration is None:
                raise ValueError("config patch contains an undeclared field")
            if (
                update.secret is not None
                or update.secret_ref is not None
                or update.receipt is not None
            ) and (declaration is None or not declaration.sensitive):
                raise ValueError("SecretMaterial requires a declared sensitive field")
            if update.secret_ref is not None or update.receipt is not None:
                raise ValueError("config entry patches cannot carry store receipts")
            if (
                declaration is not None
                and declaration.sensitive
                and update.mode is ConfigPatchMode.REPLACE
                and update.secret is None
                and update.secret_ref is None
                and update.receipt is None
            ):
                raise ValueError(
                    "declared sensitive fields require SecretMaterial or SecretRef"
                )
            if update.secret_ref is not None:
                if (
                    not isinstance(self.operation_id, str)
                    or not self.operation_id.strip()
                ):
                    raise ValueError("secret metadata updates require an operation_id")
                if update.secret_ref.operation_id != self.operation_id:
                    raise ValueError("secret metadata operation does not match patch")
                if update.secret_ref.field != update.field:
                    raise ValueError("secret metadata ref field does not match patch")
        if self.operation_id is not None and (
            not isinstance(self.operation_id, str) or not self.operation_id.strip()
        ):
            raise ValueError("operation_id must be bounded text")
        if self.target is not None:
            object.__setattr__(self, "target", ConfigTarget.validate(self.target))
        object.__setattr__(self, "updates", updates)
        object.__setattr__(self, "declared_fields", declared_fields)

    def to_persisted(
        self,
        target: ConfigTarget,
        receipts: Mapping[str, SecretReceipt],
    ) -> "PersistedConfigPatch":
        """Replace staged secret material with store-issued metadata refs.

        The returned value is the only patch shape accepted by the persistence
        port.  Raw ``SecretMaterial`` never crosses that boundary.
        """
        target = ConfigTarget.validate(target)
        if self.target is not None and self.target != target:
            raise ValueError("config patch target does not match coordinator target")
        if not isinstance(receipts, Mapping):
            raise TypeError("secret receipts must be a mapping")
        items = _mapping_items_safely(receipts)
        if items is _UNSAFE_MAPPING:
            raise ValueError("secret receipts cannot be inspected safely")
        issued: dict[str, SecretReceipt] = {}
        for field, receipt in items:
            if type(field) is not str or field in issued:
                raise ValueError("secret receipt fields must be unique text")
            try:
                checked = SecretReceipt.validate(receipt)
            except (AttributeError, TypeError, ValueError):
                checked = _UNSAFE_MAPPING
            if checked is _UNSAFE_MAPPING:
                raise ValueError("secret receipt is not structurally valid")
            issued[field] = checked
        declarations = {item.name: item for item in self.declared_fields}
        required = {
            update.field
            for update in self.updates
            if declarations.get(update.field) is not None
            and declarations[update.field].sensitive
            and update.mode is ConfigPatchMode.REPLACE
            and update.secret is not None
        }
        if set(issued) != required:
            raise ValueError(
                "secret receipts must exactly match sensitive replacements"
            )
        updates: list[ConfigFieldUpdate] = []
        for update in self.updates:
            if update.secret is not None:
                ref = issued.get(update.field)
                if ref is None:
                    raise ValueError("every staged secret requires a store ref")
                if (
                    ref.target
                    != SecretTarget(target.principal_id, target.module_id, update.field)
                    or ref.operation_id != self.operation_id
                    or ref.expected_config_revision != self.expected_revision
                    or ref.state is not SecretReceiptState.STAGED
                ):
                    raise ValueError("secret receipt does not match config patch")
                updates.append(
                    ConfigFieldUpdate(
                        update.field,
                        update.mode,
                        receipt=ref,
                    )
                )
            else:
                updates.append(update)
        return PersistedConfigPatch(
            self.expected_revision,
            tuple(updates),
            self.declared_fields,
            self.operation_id,
            target,
        )


@dataclass(frozen=True, slots=True)
class PersistedConfigPatch:
    """A repository-safe config patch containing metadata, never raw secrets."""

    expected_revision: int
    updates: tuple[ConfigFieldUpdate, ...]
    declared_fields: tuple[ConfigField, ...]
    operation_id: str
    target: ConfigTarget

    @classmethod
    def validate_for(
        cls, target: ConfigTarget, value: object
    ) -> "PersistedConfigPatch":
        if not isinstance(value, cls):
            raise TypeError("persisted config patch must be a PersistedConfigPatch")
        target = ConfigTarget.validate(target)
        try:
            checked = cls(
                value.expected_revision,
                value.updates,
                value.declared_fields,
                value.operation_id,
                value.target,
            )
        except (AttributeError, TypeError, ValueError):
            raise ValueError("persisted config patch invariants are invalid") from None
        if checked.target != target:
            raise ValueError("persisted config patch target does not match repository")
        return checked

    def __post_init__(self) -> None:
        if (
            isinstance(self.expected_revision, bool)
            or not isinstance(self.expected_revision, int)
            or self.expected_revision < 0
        ):
            raise ValueError("expected config revision must be a non-negative integer")
        updates = tuple(ConfigFieldUpdate.validate(item) for item in self.updates)
        if len({item.field for item in updates}) != len(updates):
            raise ValueError("persisted config fields must be unique")
        declared = tuple(self.declared_fields)
        if not declared or any(not isinstance(item, ConfigField) for item in declared):
            raise ValueError("persisted config patches require declared fields")
        names = {item.name: item for item in declared}
        if len(names) != len(declared) or any(
            item.field not in names for item in updates
        ):
            raise ValueError("persisted config fields must be declared")
        if not isinstance(self.operation_id, str) or not self.operation_id.strip():
            raise ValueError("persisted config patches require an operation_id")
        target = ConfigTarget.validate(self.target)
        required: set[str] = set()
        provided: set[str] = set()
        for update in updates:
            if update.secret is not None:
                raise ValueError("raw SecretMaterial cannot enter persistence")
            declaration = names[update.field]
            if declaration.sensitive and update.mode is ConfigPatchMode.REPLACE:
                required.add(update.field)
                if update.receipt is None or update.secret_ref is not None:
                    raise ValueError("sensitive persisted replace requires one receipt")
                receipt = SecretReceipt.validate(update.receipt)
                if (
                    receipt.target
                    != SecretTarget(target.principal_id, target.module_id, update.field)
                    or receipt.operation_id != self.operation_id
                    or receipt.expected_config_revision != self.expected_revision
                    or receipt.state is not SecretReceiptState.STAGED
                ):
                    raise ValueError("secret receipt does not match persisted target")
                provided.add(update.field)
            elif update.receipt is not None or update.secret_ref is not None:
                raise ValueError("receipts require sensitive REPLACE updates")
            if declaration.sensitive and update.mode is ConfigPatchMode.REPLACE:
                if update.value is not None:
                    raise ValueError("sensitive persisted replace cannot contain value")
            if not declaration.sensitive and update.mode is ConfigPatchMode.REPLACE:
                if update.value is None:
                    raise ValueError("ordinary persisted replace requires a value")
        if provided != required:
            raise ValueError(
                "persisted receipts must exactly match sensitive replacements"
            )
        object.__setattr__(self, "updates", updates)
        object.__setattr__(self, "declared_fields", declared)
        object.__setattr__(self, "target", target)


class ConfigWriter(Protocol):
    async def update(self, module_id: str, patch: ConfigPatch) -> ConfigSnapshot: ...


def validate_config_patch(target: ConfigTarget, patch: ConfigPatch) -> ConfigPatch:
    """The trusted coordinator boundary for metadata-only sensitive updates."""
    target = ConfigTarget.validate(target)
    if not isinstance(patch, ConfigPatch):
        raise TypeError("patch must be a ConfigPatch")
    for update in patch.updates:
        if update.secret_ref is not None or update.receipt is not None:
            raise ValueError("entry config patches cannot carry receipts")
    if patch.target is not None and ConfigTarget.validate(patch.target) != target:
        raise ValueError("config patch target does not match coordinator target")
    return patch


class IdentityNamespace(str, Enum):
    QQ = "qq"
    DISCORD = "discord"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class Principal:
    principal_id: str
    identity_namespace: str
    external_user_id: str

    def __post_init__(self) -> None:
        for field in ("principal_id", "identity_namespace", "external_user_id"):
            value = getattr(self, field)
            if (
                not isinstance(value, str)
                or not value.strip()
                or any(character in value for character in ("/", "\\", "\n"))
            ):
                raise ValueError(f"{field} must be a bounded identifier")


@dataclass(frozen=True, slots=True)
class ConversationKey:
    """The composite identity used by binding persistence and lookup."""

    adapter_id: str
    conversation_id: str

    def __post_init__(self) -> None:
        for field in ("adapter_id", "conversation_id"):
            value = getattr(self, field)
            if (
                type(value) is not str
                or not value.strip()
                or any(character in value for character in ("/", "\\", "\n"))
            ):
                raise ValueError(f"{field} must be a bounded identifier")

    @classmethod
    def from_ref(cls, conversation: ConversationRef) -> "ConversationKey":
        if not isinstance(conversation, ConversationRef):
            raise TypeError("conversation must be a ConversationRef")
        return cls(conversation.adapter_id, conversation.conversation_id)


@dataclass(frozen=True, slots=True)
class Binding:
    binding_id: str
    revision: int
    principal_id: str
    module_id: str
    object_type: str
    object_id: str
    is_default: bool
    origin: str
    conversation_id: str
    adapter_id: str = ""

    def __post_init__(self) -> None:
        for field in (
            "binding_id",
            "principal_id",
            "object_type",
            "object_id",
            "origin",
            "conversation_id",
        ):
            value = getattr(self, field)
            if (
                not isinstance(value, str)
                or not value.strip()
                or any(character in value for character in ("/", "\\", "\n"))
            ):
                raise ValueError(f"{field} must be a bounded identifier")
        try:
            validate_module_id(self.module_id)
        except ValueError:
            raise ValueError("module_id must be a bounded identifier") from None
        if self.adapter_id != "" and (
            type(self.adapter_id) is not str
            or any(character in self.adapter_id for character in ("/", "\\", "\n"))
        ):
            raise ValueError("adapter_id must be a bounded identifier")
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 1
        ):
            raise ValueError("binding revision must be positive")
        if not isinstance(self.is_default, bool):
            raise TypeError("binding default marker must be a bool")

    @property
    def conversation_key(self) -> ConversationKey:
        return ConversationKey(self.adapter_id, self.conversation_id)

    @classmethod
    def validate_for_repository(cls, value: object) -> "Binding":
        if not isinstance(value, cls):
            raise TypeError("binding must be a Binding")
        try:
            binding = cls(
                value.binding_id,
                value.revision,
                value.principal_id,
                value.module_id,
                value.object_type,
                value.object_id,
                value.is_default,
                value.origin,
                value.conversation_id,
                value.adapter_id,
            )
            binding.conversation_key
            return binding
        except (AttributeError, TypeError, ValueError):
            raise ValueError(
                "binding requires an adapter-scoped conversation key"
            ) from None


@dataclass(frozen=True, slots=True)
class BindingDefaultSnapshot:
    """Immutable read of a binding and its independent default relation revision."""

    binding: Binding | None
    default_revision: int

    def __post_init__(self) -> None:
        if type(self.default_revision) is not int or self.default_revision < 0:
            raise ValueError("default revision must be a non-negative integer")
        if self.binding is None:
            if self.default_revision != 0:
                raise ValueError("missing default binding must have revision zero")
            return
        try:
            binding = Binding.validate_for_repository(self.binding)
        except (TypeError, ValueError):
            raise ValueError("default binding snapshot is invalid") from None
        if not binding.is_default:
            raise ValueError("default binding snapshot requires a default binding")
        if type(binding.revision) is not int or binding.revision < 1:
            raise ValueError("default binding snapshot requires a positive revision")
        if self.default_revision < 1:
            raise ValueError("present default binding requires a positive revision")
        object.__setattr__(self, "binding", binding)


class GrantStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class Grant:
    grant_id: str
    revision: int
    principal_id: str
    module_id: str
    account_id: str
    scopes: tuple[str, ...]
    secret_ref: SecretRef | None
    status: GrantStatus
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        for field in ("grant_id", "principal_id", "account_id"):
            value = getattr(self, field)
            if (
                not isinstance(value, str)
                or not value.strip()
                or any(character in value for character in ("/", "\\", "\n"))
            ):
                raise ValueError(f"{field} must be a bounded identifier")
        try:
            validate_module_id(self.module_id)
        except ValueError:
            raise ValueError("module_id must be a bounded identifier") from None
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 1
        ):
            raise ValueError("grant revision must be positive")
        scopes = tuple(self.scopes)
        if not scopes or any(
            not isinstance(scope, str) or not scope.strip() for scope in scopes
        ):
            raise ValueError("grant scopes must be non-empty text")
        if len(set(scopes)) != len(scopes):
            raise ValueError("grant scopes must be unique")
        if self.secret_ref is not None:
            try:
                ref = SecretRef.validate(self.secret_ref)
            except (TypeError, ValueError):
                raise ValueError(
                    "secret_ref is not an issued opaque reference"
                ) from None
            if ref.principal_id != self.principal_id or ref.module_id != self.module_id:
                raise ValueError("secret_ref is not owned by this grant")
            object.__setattr__(self, "secret_ref", ref)
        if not isinstance(self.status, GrantStatus):
            raise TypeError("grant status must be a GrantStatus")
        if self.expires_at is not None and (
            self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None
        ):
            raise ValueError("grant expiry must be timezone-aware")


class LoginSessionStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class LoginSession:
    session_id: str
    principal_id: str
    module_id: str
    generation: int
    status: LoginSessionStatus
    expires_at: datetime

    def __post_init__(self) -> None:
        for field in ("session_id", "principal_id"):
            value = getattr(self, field)
            if (
                not isinstance(value, str)
                or not value.strip()
                or any(character in value for character in ("/", "\\", "\n"))
            ):
                raise ValueError(f"{field} must be a bounded identifier")
        try:
            validate_module_id(self.module_id)
        except ValueError:
            raise ValueError("module_id must be a bounded identifier") from None
        if (
            isinstance(self.generation, bool)
            or not isinstance(self.generation, int)
            or self.generation < 1
        ):
            raise ValueError("login generation must be positive")
        if not isinstance(self.status, LoginSessionStatus):
            raise TypeError("login status must be a LoginSessionStatus")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("login expiry must be timezone-aware")


@dataclass(frozen=True, slots=True)
class CacheAccessRequest:
    key: str
    visibility: CacheVisibility
    scope: OwnerScope

    def __post_init__(self) -> None:
        if (
            not isinstance(self.key, str)
            or not self.key.strip()
            or any(marker in self.key for marker in ("/", "\\", ".."))
        ):
            raise ValueError("cache key must be a bounded key")
        if not isinstance(self.visibility, CacheVisibility):
            raise TypeError("cache visibility must be a CacheVisibility")
        scope = OwnerScope.validate(self.scope)
        if self.visibility is CacheVisibility.PUBLIC and scope.kind.value != "public":
            raise ValueError("public cache requests require a public scope")
        if self.visibility is CacheVisibility.USER and scope.kind.value != "user":
            raise ValueError("user cache requests require a user scope")
        if (
            self.visibility is CacheVisibility.AUTHORIZED
            and scope.kind.value != "authorized"
        ):
            raise ValueError("authorized cache requests require an authorized scope")
        object.__setattr__(self, "scope", scope)


def validate_cache_access_request(value: object) -> CacheAccessRequest:
    """Rebuild a cache request at the service boundary and reject forged DTOs."""
    if not isinstance(value, CacheAccessRequest):
        raise TypeError("cache request must be a CacheAccessRequest")
    try:
        return CacheAccessRequest(value.key, value.visibility, value.scope)
    except (AttributeError, TypeError, ValueError):
        raise ValueError("cache request invariants are invalid") from None


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
    ) -> CacheEntry: ...

    async def current_revision(self, request: CacheAccessRequest) -> int | None: ...


class SubscriptionUnavailable(RuntimeError):
    """Stable B03 placeholder error; B04 supplies the subscription runtime."""

    code = "subscription_unavailable"
    message = "subscriptions are unavailable in this contract version"

    def __init__(self) -> None:
        super().__init__(self.message)


# Explicit view aliases make the public DTO naming consistent across adapters.
PrincipalView = Principal
ConversationView = ConversationRef
BindingDTO = Binding
GrantView = Grant
LoginSessionView = LoginSession
Conversation = ConversationRef
BindingRecord = Binding
