"""Core-owned defaults and explicitly Host-reviewed execution ceilings."""

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType

from yomihime_game_link_sdk.contexts import InvocationOrigin


def freeze_execution_timeouts(value, *, targets=None):
    """Copy bounded Host declarations; never accept request-supplied policy."""
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, Mapping) or len(value) > 128:
        raise ValueError("invalid execution timeout mapping")
    frozen = {}
    for key, seconds in value.items():
        if (
            type(key) is not tuple
            or len(key) != 2
            or any(type(part) is not str or not part or len(part) > 256 for part in key)
            or targets is not None
            and key not in targets
            or type(seconds) not in (int, float)
            or not 0 < seconds <= 90
            or not isfinite(seconds)
        ):
            raise ValueError("invalid execution timeout declaration")
        frozen[key] = float(seconds)
    return MappingProxyType(frozen)


@dataclass(frozen=True)
class InvocationBudgetPolicy:
    command_timeout: float = 90.0
    capability_timeouts: Mapping | None = None

    def __post_init__(self):
        value = self.command_timeout
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
            or not 0 < value <= 90
        ):
            raise ValueError("command_timeout must be finite and within (0, 90]")
        object.__setattr__(
            self,
            "capability_timeouts",
            freeze_execution_timeouts(self.capability_timeouts),
        )

    def seconds(self, origin, handler_timeout, *, module_id=None, capability_id=None):
        if origin is InvocationOrigin.COMMAND:
            return float(self.command_timeout)
        if origin is InvocationOrigin.WEB_PUBLIC:
            declared = self.capability_timeouts.get((module_id, capability_id))
            if declared is not None:
                return declared
            return min(30.0, handler_timeout)
        return handler_timeout

    def deadline(
        self,
        origin,
        now,
        handler_timeout,
        trusted_deadline=None,
        *,
        module_id=None,
        capability_id=None,
    ):
        deadline = now + self.seconds(
            origin, handler_timeout, module_id=module_id, capability_id=capability_id
        )
        return (
            min(deadline, trusted_deadline)
            if trusted_deadline is not None
            else deadline
        )
