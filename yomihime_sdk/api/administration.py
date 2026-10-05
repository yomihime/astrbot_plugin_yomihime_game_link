"""Redacted administration snapshots exposed to a future management adapter."""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping, Protocol, TypedDict

from .contexts import InvocationView
from .services import ConfigPatch, ConfigTarget
from .storage import validate_module_id

_SENSITIVE_TEXT = re.compile(
    r"(?:secret|token|cookie|password|passwd|pwd|pin|api[_-]?key|access[_-]?key|"
    r"authorization|authentication|bearer|credential|private[_-]?key|"
    r"refresh[_-]?token|session[_-]?id|signing[_-]?key)",
    re.IGNORECASE,
)


def _sensitive_name(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.lower())
    return bool(_SENSITIVE_TEXT.search(value)) or any(
        marker in normalized
        for marker in (
            "secret",
            "token",
            "cookie",
            "password",
            "passwd",
            "pwd",
            "pin",
            "apikey",
            "accesskey",
            "authorization",
            "authentication",
            "bearer",
            "credential",
            "privatekey",
            "refreshtoken",
            "sessionid",
            "signingkey",
        )
    )


_REDACTED_VALUES = frozenset(
    {"configured", "unset", "redacted", "disabled", "unavailable", "present"}
)
ADMIN_ERROR_MESSAGES: Mapping[str, str] = MappingProxyType(
    {
        "admin_authorization_denied": "Administrative authorization is unavailable.",
        "invalid_config": "Configuration was rejected.",
        "module_not_found": "Module was not found.",
        "operation_failed": "The operation failed.",
        "operation_unavailable": "The operation is unavailable.",
        "revision_conflict": "The resource changed. Refresh and try again.",
    }
)
ADMIN_REASON_CODES = frozenset(
    {
        "dependency_missing",
        "health_unknown",
        "invalid_config",
        "module_not_found",
        "needs_config",
        "operation_failed",
        "operation_unavailable",
        "revision_conflict",
        "stale_epoch",
        "stop_cancelled",
        "stop_timeout",
        "unregistered",
    }
)

# Additive B05 admin contract.  The shared B02/B04 contract version stays
# unchanged; this revision identifies the management-only surface.
ADMIN_CONTRACT_REVISION = "H-ADMIN-02"


class AdminOperation(StrEnum):
    LIST_MODULES = "list_modules"
    MODULE_SNAPSHOT = "module_snapshot"
    SET_ENABLED = "set_enabled"
    UPDATE_CONFIG = "update_config"
    READ_CONFIG = "read_config"
    ROLLBACK_CONFIG = "rollback_config"
    RECOVER_CONFIG = "recover_config"


class OrdinaryFieldProjection(TypedDict):
    """Validated ordinary value; invalid raw values must be projected as None."""

    value: object | None
    state: str
    present: bool
    source: str


class OrdinaryConfigProjection(TypedDict):
    revision: int
    fields: Mapping[str, OrdinaryFieldProjection]


AdminResourcePolicy = Mapping[ConfigTarget, frozenset[str]]


class AdminAuthorizationContext(Protocol):
    """Opaque host-attested request/session facts, never client claims.

    Implementations must only be created by a trusted host adapter. These
    descriptive fields do not prove authorization; Core verifies the current
    registered source, object ownership, resource policy, expiry and source
    epoch on every call. Native credentials retain their own durable generation.
    """

    @property
    def adapter_id(self) -> str: ...

    @property
    def request_id(self) -> str: ...

    @property
    def session_id(self) -> str: ...


@dataclass(frozen=True, slots=True)
class AdminAuthorizationGrant:
    """Short-lived Core-issued evidence for one authorized operation."""

    operation: AdminOperation
    generation: int
    _effect: object | None = dataclass_field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.operation, AdminOperation):
            raise TypeError("operation must be an AdminOperation")
        if type(self.generation) is not int or self.generation < 1:
            raise ValueError("authorization generation must be positive")


class AdminAuthorizationDenied(PermissionError):
    """Stable fail-closed denial without request or credential details."""

    code = "admin_authorization_denied"

    def __init__(self) -> None:
        super().__init__("administrative authorization is unavailable")


class ModuleLifecycle(StrEnum):
    DISCOVERED = "discovered"
    STARTING = "starting"
    ACTIVE = "active"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class ModuleHealth(StrEnum):
    NEEDS_CONFIG = "needs_config"
    DEPENDENCY_MISSING = "dependency_missing"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CONNECTION_ERROR = "connection_error"
    MODULE_ERROR = "module_error"


def _id(value: str, field: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(character in value for character in ("/", "\\", "\n"))
    ):
        raise ValueError(f"{field} must be a bounded identifier")


_UNSAFE_MAPPING = object()


def _inspect_mapping(value: Mapping[str, str]):
    try:
        items = tuple(value.items())
        result: dict[str, str] = {}
        for key, item in items:
            if type(key) is not str or type(item) is not str:
                raise ValueError("admin mapping contains invalid text")
            key_text = str.strip(key)
            item_text = str.strip(item)
            if (
                not key_text
                or any(
                    ord(character) < 32 or 0x7F <= ord(character) <= 0x9F
                    for character in key_text
                )
                or not item_text
                or any(
                    ord(character) < 32 or 0x7F <= ord(character) <= 0x9F
                    for character in item_text
                )
            ):
                raise ValueError("admin mapping contains invalid text")
            if key_text in result:
                raise ValueError("admin mapping keys must be unique")
            result[key_text] = item_text
    except Exception:
        return _UNSAFE_MAPPING
    return result


def _safe_mapping(value: Mapping[str, str]) -> dict[str, str]:
    result = _inspect_mapping(value)
    if result is _UNSAFE_MAPPING:
        raise ValueError("admin mapping cannot be inspected safely")
    return result


def _reason_code(value: object) -> None:
    if type(value) is not str or value not in ADMIN_REASON_CODES:
        raise ValueError("reason code is not in the public code catalog")


@dataclass(frozen=True, slots=True)
class ModuleStatus:
    module_id: str
    enabled: bool
    lifecycle: ModuleLifecycle
    health: ModuleHealth
    epoch: int
    registry_revision: int
    reason_code: str | None = None

    def __post_init__(self) -> None:
        validate_module_id(self.module_id)
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be a bool")
        if not isinstance(self.lifecycle, ModuleLifecycle):
            raise TypeError("lifecycle must be a ModuleLifecycle")
        if not isinstance(self.health, ModuleHealth):
            raise TypeError("health must be a ModuleHealth")
        for field in ("epoch", "registry_revision"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field} must be a non-negative integer")
        if self.reason_code is not None:
            _reason_code(self.reason_code)


@dataclass(frozen=True, slots=True)
class ConfigSummary:
    module_id: str
    revision: int
    fields: Mapping[str, str]
    sensitive_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validate_module_id(self.module_id)
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 0
        ):
            raise ValueError("config summary revision must be non-negative")
        if not isinstance(self.fields, Mapping):
            raise TypeError("config summary fields must be a mapping")
        sensitive = tuple(self.sensitive_fields)
        if any(
            not isinstance(item, str) or not item.strip() for item in sensitive
        ) or len(set(sensitive)) != len(sensitive):
            raise ValueError("sensitive field names must be text")
        safe_fields = _safe_mapping(self.fields)
        for field, value in safe_fields.items():
            if _sensitive_name(field) or field in sensitive:
                if value.lower() not in _REDACTED_VALUES:
                    raise ValueError(
                        "sensitive config fields require a redacted status"
                    )
            elif _SENSITIVE_TEXT.search(value):
                raise ValueError("admin mapping contains a sensitive value")
        object.__setattr__(self, "fields", MappingProxyType(safe_fields))
        object.__setattr__(self, "sensitive_fields", sensitive)


@dataclass(frozen=True, slots=True)
class CapabilitySummary:
    capability_id: str
    available: bool
    reason_code: str | None = None

    def __post_init__(self) -> None:
        _id(self.capability_id, "capability_id")
        if not isinstance(self.available, bool):
            raise TypeError("available must be a bool")
        if self.reason_code is not None:
            _reason_code(self.reason_code)


@dataclass(frozen=True, slots=True)
class DataCount:
    collection: str
    count: int

    def __post_init__(self) -> None:
        _id(self.collection, "collection")
        if (
            isinstance(self.count, bool)
            or not isinstance(self.count, int)
            or self.count < 0
        ):
            raise ValueError("data count must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class AdminError:
    code: str
    message: str
    retryable: bool = False

    def __post_init__(self) -> None:
        if type(self.code) is not str or self.code not in ADMIN_ERROR_MESSAGES:
            raise ValueError("admin error code is not in the public code catalog")
        if (
            type(self.message) is not str
            or self.message != ADMIN_ERROR_MESSAGES[self.code]
        ):
            raise ValueError("admin error message must match its public code")
        if not isinstance(self.retryable, bool):
            raise TypeError("retryable must be a bool")


@dataclass(frozen=True, slots=True)
class ModuleAdminSnapshot:
    status: ModuleStatus
    config: ConfigSummary
    capabilities: tuple[CapabilitySummary, ...] = ()
    data_counts: tuple[DataCount, ...] = ()
    error: AdminError | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ModuleStatus):
            raise TypeError("status must be a ModuleStatus")
        if not isinstance(self.config, ConfigSummary):
            raise TypeError("config must be a ConfigSummary")
        if self.status.module_id != self.config.module_id:
            raise ValueError(
                "module status and config summary must refer to one module"
            )
        capabilities = tuple(self.capabilities)
        counts = tuple(self.data_counts)
        if any(not isinstance(item, CapabilitySummary) for item in capabilities):
            raise TypeError("capabilities require CapabilitySummary values")
        if any(not isinstance(item, DataCount) for item in counts):
            raise TypeError("data_counts require DataCount values")
        if len({item.capability_id for item in capabilities}) != len(capabilities):
            raise ValueError("capability IDs must be unique")
        if len({item.collection for item in counts}) != len(counts):
            raise ValueError("data collections must be unique")
        if self.error is not None and not isinstance(self.error, AdminError):
            raise TypeError("error must be an AdminError")
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "data_counts", counts)


@dataclass(frozen=True, slots=True)
class CoreConfigSummary:
    """Reserved Core config projection; this target has no module lifecycle."""

    revision: int
    default_region: str | None
    raw_present: bool
    state: str = "valid"
    module_id: str = "game_link/core"

    def __post_init__(self):
        if self.module_id != "game_link/core":
            raise ValueError("Core config target is reserved")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("Core config revision must be positive")
        if self.state not in {"valid", "invalid"} or (
            self.state == "invalid" and self.default_region is not None
        ):
            raise ValueError("invalid Core config state")
        if self.state == "valid" and (
            type(self.default_region) is not str
            or self.default_region not in {"cn", "global"}
        ):
            raise ValueError("invalid Core region")
        if type(self.raw_present) is not bool:
            raise TypeError("Core config presence must be bool")


class AdminOperations(Protocol):
    """Host-independent administrative operations.

    Implementations authorize every call through Core. A trusted authorization
    context may authorize an operation when invocation is ``None``; a missing
    or untrusted authorization context is denied. The invocation is descriptive
    and never grants administrative authority by itself.
    """

    async def ordinary_snapshot(
        self, *, authorization: AdminAuthorizationContext
    ) -> Mapping[str, OrdinaryConfigProjection]:
        """Read only deployment-declared ordinary resources with source-owned proof."""
        ...

    async def ordinary_rollback(
        self,
        expected_revisions: Mapping[ConfigTarget, int],
        *,
        authorization: AdminAuthorizationContext,
    ) -> Mapping[str, bool]:
        """Bounded migration rollback, never an entire database restore."""
        ...

    async def config_snapshot(
        self,
        invocation: InvocationView | None,
        module_id: str,
        *,
        authorization: AdminAuthorizationContext | None = None,
    ) -> CoreConfigSummary: ...

    async def list_modules(
        self,
        invocation: InvocationView | None,
        *,
        authorization: AdminAuthorizationContext | None = None,
    ) -> tuple[ModuleAdminSnapshot, ...]: ...

    async def module_snapshot(
        self,
        invocation: InvocationView | None,
        module_id: str,
        *,
        authorization: AdminAuthorizationContext | None = None,
    ) -> ModuleAdminSnapshot: ...

    async def set_enabled(
        self,
        invocation: InvocationView | None,
        module_id: str,
        enabled: bool,
        *,
        expected_registry_revision: int,
        authorization: AdminAuthorizationContext | None = None,
    ) -> ModuleStatus: ...

    async def update_config(
        self,
        invocation: InvocationView | None,
        module_id: str,
        patch: ConfigPatch,
        *,
        authorization: AdminAuthorizationContext | None = None,
    ) -> ConfigSummary: ...


# Names used by the design document remain available as explicit aliases.
ModuleStatusDTO = ModuleStatus
ConfigSummaryDTO = ConfigSummary
CapabilitySummaryDTO = CapabilitySummary
DataCountDTO = DataCount
AdminErrorDTO = AdminError
