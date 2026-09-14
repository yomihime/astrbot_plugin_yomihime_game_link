"""Stable capability result and model-fact contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from .display import DisplayDocument, Privacy, TimeValue, _text, _valid_value
from .version import CONTRACT_VERSION


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

    def __post_init__(self) -> None:
        if isinstance(self.code, str):
            object.__setattr__(self, "code", ErrorCode(self.code))
        elif not isinstance(self.code, ErrorCode):
            raise TypeError("code must be ErrorCode")
        _text(self.message, "error message")


@dataclass(frozen=True)
class FactDocument:
    """Facts explicitly safe to expose to a model."""

    facts: Mapping[str, Any]
    sources: tuple[str, ...] = ()
    schema_version: str = CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CONTRACT_VERSION:
            raise ValueError("unsupported fact contract version")
        if not isinstance(self.facts, Mapping):
            raise TypeError("facts must be a mapping")
        object.__setattr__(self, "facts", _valid_value(self.facts))
        object.__setattr__(
            self, "sources", tuple(_text(x, "source") for x in self.sources)
        )


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
    schema_version: str = CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CONTRACT_VERSION:
            raise ValueError("unsupported result contract version")
        _text(self.result_id, "result_id")
        if isinstance(self.status, str):
            object.__setattr__(self, "status", ResultStatus(self.status))
        elif not isinstance(self.status, ResultStatus):
            raise TypeError("status must be ResultStatus")
        if isinstance(self.privacy, str):
            object.__setattr__(self, "privacy", Privacy(self.privacy))
        elif not isinstance(self.privacy, Privacy):
            raise TypeError("privacy must be Privacy")
        object.__setattr__(
            self, "provenance", tuple(_text(x, "provenance") for x in self.provenance)
        )
        timestamps = tuple(self.timestamps)
        if any(
            not isinstance(x, TimeValue) or not isinstance(x.value, datetime)
            for x in timestamps
        ):
            raise TypeError(
                "result timestamps require timezone-aware TimeValue datetimes"
            )
        object.__setattr__(self, "timestamps", timestamps)
        object.__setattr__(
            self, "warnings", tuple(_text(x, "warning") for x in self.warnings)
        )
        for name, expected in (
            ("document", DisplayDocument),
            ("model_facts", FactDocument),
            ("error", ErrorDetail),
        ):
            value = getattr(self, name)
            if value is not None and not isinstance(value, expected):
                raise TypeError(f"{name} must be {expected.__name__}")
        if self.document is not None and self.document.privacy != self.privacy:
            raise ValueError("result and document privacy must match")
        if self.model_facts is not None and self.privacy is Privacy.PRIVATE:
            raise ValueError("private results cannot contain model facts")
        if (
            self.status in (ResultStatus.SUCCESS, ResultStatus.PARTIAL_SUCCESS)
            and self.document is None
        ):
            raise ValueError("successful results require a document")
        if self.status is ResultStatus.ERROR and self.document is not None:
            raise ValueError("error results cannot contain a document")
        if self.status is ResultStatus.ERROR and self.model_facts is not None:
            raise ValueError("error results cannot contain model facts")
        if self.status is ResultStatus.NEEDS_SELECTION and self.document is None:
            raise ValueError("selection results require a candidate document")
        if self.status is ResultStatus.ERROR and self.error is None:
            raise ValueError("error results require stable error detail")
        if self.status is not ResultStatus.ERROR and self.error is not None:
            raise ValueError("only error results may contain error detail")


__all__ = [
    "CapabilityResult",
    "ErrorCode",
    "ErrorDetail",
    "FactDocument",
    "ResultStatus",
]
