from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any

from .display import DisplayDocument, Privacy, TimeValue


class ResultStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    NEEDS_SELECTION = "needs_selection"
    ERROR = "error"


class ErrorCode(str, Enum):
    PARAMETER_ERROR = "parameter_error"
    UNBOUND = "unbound"
    AUTH_REQUIRED = "auth_required"
    AUTH_EXPIRED = "auth_expired"
    NOT_FOUND = "not_found"
    NOT_PUBLIC = "not_public"
    NO_RECORDS = "no_records"
    UNPARSED = "unparsed"
    RATE_LIMITED = "rate_limited"
    UPSTREAM_ERROR = "upstream_error"
    MODULE_UNAVAILABLE = "module_unavailable"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ErrorDetail:
    code: ErrorCode
    message: str


@dataclass(frozen=True)
class FactDocument:
    """Facts explicitly safe to expose to a model."""

    facts: Mapping[str, Any]
    sources: tuple[str, ...] = ()
    schema_version: str = "1.8.0"

    def __post_init__(self) -> None:
        if isinstance(self.facts, Mapping):
            object.__setattr__(self, "facts", MappingProxyType(dict(self.facts)))
        if self.sources is not None:
            object.__setattr__(self, "sources", tuple(self.sources))


@dataclass(frozen=True)
class CapabilityResult:
    result_id: str
    status: ResultStatus
    document: DisplayDocument | None = None
    model_facts: FactDocument | None = None
    provenance: tuple[str, ...] = ()
    timestamps: tuple[TimeValue, ...] = ()
    privacy: Privacy = Privacy.PUBLIC
    warnings: tuple[str, ...] = ()
    error: ErrorDetail | None = None
    schema_version: str = "1.8.0"

    def __post_init__(self) -> None:
        if self.provenance is not None:
            object.__setattr__(self, "provenance", tuple(self.provenance))
        if self.timestamps is not None:
            object.__setattr__(self, "timestamps", tuple(self.timestamps))
        if self.warnings is not None:
            object.__setattr__(self, "warnings", tuple(self.warnings))
