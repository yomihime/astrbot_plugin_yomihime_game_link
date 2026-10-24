"""Core-private, exact-object preparation before the protected send handoff."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from yomihime_game_link_sdk.contexts import InvocationView
from yomihime_game_link_sdk.results import CapabilityResult

from ..core.ports import MessageReceipt, MessageTarget


@dataclass(frozen=True, slots=True, eq=False)
class PreparedCommand:
    """Trusted composition output, never an SDK value or a send authorization.

    The Host chain is already built. Only the scheduled, gate-approved sender
    can use this object, after checking its exact source and current leases.
    """

    invocation: InvocationView
    result: CapabilityResult
    target: MessageTarget
    check_current: Callable[[], None]
    dispatch: Callable[[], Awaitable[MessageReceipt]]

    def require(self, invocation, result, target) -> None:
        if (
            self.invocation is not invocation
            or self.result is not result
            or self.target != target
        ):
            raise PermissionError("prepared command source changed")
        self.check_current()
