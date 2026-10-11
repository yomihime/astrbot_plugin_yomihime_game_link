"""Stable capability result and model-fact contracts."""

from __future__ import annotations

from datetime import datetime
from math import isfinite
from types import MappingProxyType
from typing import Mapping

from yomihime_game_link_sdk.display import DisplayDocument as DisplayDocument
from yomihime_game_link_sdk.display import Privacy as Privacy
from yomihime_game_link_sdk.display import TimeValue as TimeValue
from yomihime_game_link_sdk.results import CapabilityResult as CapabilityResult
from yomihime_game_link_sdk.results import ErrorCode as ErrorCode
from yomihime_game_link_sdk.results import ErrorDetail as ErrorDetail
from yomihime_game_link_sdk.results import FactDocument as FactDocument
from yomihime_game_link_sdk.results import ResultStatus as ResultStatus

from ...core.contracts.validation_boundary import validate_contract
from .display import _text as _text
from .display import _valid_value as _valid_value
from .version import CONTRACT_VERSION as CONTRACT_VERSION


def check_ErrorDetail(self) -> None:
    if isinstance(self.code, str):
        object.__setattr__(self, "code", validate_contract(ErrorCode(self.code)))
    elif not isinstance(self.code, ErrorCode):
        raise TypeError("code must be ErrorCode")
    _text(self.message, "error message")


def _valid_fact_value(value):
    """Facts add finite JSON floats; display typed values keep their own domain."""
    if type(value) is float:
        if not isfinite(value):
            raise ValueError("fact numbers must be finite")
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("fact mapping keys must be text")
        return MappingProxyType(
            {key: _valid_fact_value(child) for key, child in value.items()}
        )
    if isinstance(value, (tuple, list)):
        return tuple(_valid_fact_value(child) for child in value)
    return _valid_value(value)


def check_FactDocument(self) -> None:
    if self.schema_version != "1.8.0":
        raise ValueError("unsupported fact contract version")
    if not isinstance(self.facts, Mapping):
        raise TypeError("facts must be a mapping")
    object.__setattr__(self, "facts", _valid_fact_value(self.facts))
    object.__setattr__(
        self, "sources", tuple((_text(x, "source") for x in self.sources))
    )


def check_CapabilityResult(self) -> None:
    if self.schema_version != "1.8.0":
        raise ValueError("unsupported result contract version")
    _text(self.result_id, "result_id")
    if isinstance(self.status, str):
        object.__setattr__(self, "status", validate_contract(ResultStatus(self.status)))
    elif not isinstance(self.status, ResultStatus):
        raise TypeError("status must be ResultStatus")
    if isinstance(self.privacy, str):
        object.__setattr__(self, "privacy", validate_contract(Privacy(self.privacy)))
    elif not isinstance(self.privacy, Privacy):
        raise TypeError("privacy must be Privacy")
    object.__setattr__(
        self, "provenance", tuple((_text(x, "provenance") for x in self.provenance))
    )
    timestamps = tuple(self.timestamps)
    if any(
        (
            not isinstance(x, TimeValue) or not isinstance(x.value, datetime)
            for x in timestamps
        )
    ):
        raise TypeError("result timestamps require timezone-aware TimeValue datetimes")
    object.__setattr__(self, "timestamps", timestamps)
    object.__setattr__(
        self, "warnings", tuple((_text(x, "warning") for x in self.warnings))
    )
    for name, expected in (
        ("document", DisplayDocument),
        ("model_facts", FactDocument),
        ("error", ErrorDetail),
    ):
        value = getattr(self, name)
        if value is not None and (not isinstance(value, expected)):
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
    if (
        self.status is ResultStatus.ERROR
        and self.model_facts is not None
        and (
            self.schema_version not in ("1.6.0", "1.7.0", "1.8.0")
            or self.model_facts.schema_version not in ("1.6.0", "1.7.0", "1.8.0")
            or self.privacy is not Privacy.PUBLIC
        )
    ):
        raise ValueError("error results cannot contain model facts")
    if self.status is ResultStatus.NEEDS_SELECTION and self.document is None:
        raise ValueError("selection results require a candidate document")
    if self.status is ResultStatus.ERROR and self.error is None:
        raise ValueError("error results require stable error detail")
    if self.status is not ResultStatus.ERROR and self.error is not None:
        raise ValueError("only error results may contain error detail")
    if self.status is ResultStatus.ERROR and self.model_facts is not None:
        from ..public_result import preflight_error_facts

        preflight_error_facts(self)
