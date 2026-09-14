"""Read-only invocation information supplied to module handlers.

``InvocationView`` is descriptive data, not an authority token.  The core
issuer keeps the corresponding private scope handle and checks it separately.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*\Z")
_GLOBAL_MODULE_ID = re.compile(
    r"[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*/[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*\Z"
)


class InvocationOrigin(StrEnum):
    """The trusted bridge that created an invocation."""

    COMMAND = "command"
    LLM_TOOL = "llm_tool"
    SCHEDULER = "scheduler"
    ADMIN = "admin"


def _require_identifier(value: str, field: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase, dotted identifier")


def _require_optional_identifier(value: str | None, field: str) -> None:
    if value is not None:
        _require_identifier(value, field)


def _require_opaque_id(value: str, field: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError(
            f"{field} must be non-empty text no longer than 256 characters"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{field} must not contain control characters")


def _require_optional_opaque_id(value: str | None, field: str) -> None:
    if value is not None:
        _require_opaque_id(value, field)


def _require_global_module_id(value: str) -> None:
    if not isinstance(value, str) or not _GLOBAL_MODULE_ID.fullmatch(value):
        raise ValueError("module_id must be a global package/module identifier")


@dataclass(frozen=True, slots=True)
class InvocationView:
    """An immutable, non-authorizing view of one capability invocation."""

    invocation_id: str
    origin: InvocationOrigin
    actor_id: str | None
    conversation_id: str | None
    module_id: str
    module_epoch: int
    registry_revision: int
    deadline: float | None = None
    parent_id: str | None = None
    grant_id: str | None = None
    grant_revision: int | None = None
    subscription_id: str | None = None
    subscription_revision: int | None = None

    def __post_init__(self) -> None:
        _require_opaque_id(self.invocation_id, "invocation_id")
        if not isinstance(self.origin, InvocationOrigin):
            raise TypeError("origin must be an InvocationOrigin")
        _require_optional_opaque_id(self.actor_id, "actor_id")
        _require_optional_opaque_id(self.conversation_id, "conversation_id")
        _require_global_module_id(self.module_id)
        _require_optional_opaque_id(self.parent_id, "parent_id")
        _require_optional_opaque_id(self.grant_id, "grant_id")
        _require_optional_opaque_id(self.subscription_id, "subscription_id")
        _require_positive_int(self.module_epoch, "module_epoch")
        _require_positive_int(self.registry_revision, "registry_revision")
        _require_paired_revision(self.grant_id, self.grant_revision, "grant")
        _require_paired_revision(
            self.subscription_id, self.subscription_revision, "subscription"
        )
        if self.deadline is not None:
            if isinstance(self.deadline, bool) or not isinstance(
                self.deadline, (int, float)
            ):
                raise TypeError("deadline must be a finite monotonic timestamp")
            if not math.isfinite(float(self.deadline)) or self.deadline <= 0:
                raise ValueError(
                    "deadline must be a finite positive monotonic timestamp"
                )
            object.__setattr__(self, "deadline", float(self.deadline))


def _require_positive_int(value: int, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    if value < 1:
        raise ValueError(f"{field} must be positive")


def _require_paired_revision(
    identifier: str | None, revision: int | None, field: str
) -> None:
    if (identifier is None) != (revision is None):
        raise ValueError(f"{field}_id and {field}_revision must be supplied together")
    if revision is not None:
        _require_positive_int(revision, f"{field}_revision")
