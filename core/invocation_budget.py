"""Core-owned time budgets; no capability or game-specific exceptions."""

from dataclasses import dataclass
from math import isfinite

from yomihime_game_link_sdk.contexts import InvocationOrigin


@dataclass(frozen=True)
class InvocationBudgetPolicy:
    command_timeout: float = 90.0

    def __post_init__(self):
        value = self.command_timeout
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
            or not 0 < value <= 90
        ):
            raise ValueError("command_timeout must be finite and within (0, 90]")

    def seconds(self, origin, handler_timeout):
        if origin is InvocationOrigin.COMMAND:
            return float(self.command_timeout)
        if origin is InvocationOrigin.WEB_PUBLIC:
            return min(30.0, handler_timeout)
        return handler_timeout

    def deadline(self, origin, now, handler_timeout, trusted_deadline=None):
        deadline = now + self.seconds(origin, handler_timeout)
        return (
            min(deadline, trusted_deadline)
            if trusted_deadline is not None
            else deadline
        )
