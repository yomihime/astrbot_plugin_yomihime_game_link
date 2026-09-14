"""Host and persistence ports used by core services, never by modules."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from ..api.contexts import InvocationView
from ..api.display import _asset
from ..api.results import CapabilityResult
from ..api.storage import GrantReference


@dataclass(frozen=True, slots=True)
class MessageTarget:
    conversation_id: str
    recipient_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.conversation_id, str) or not self.conversation_id:
            raise ValueError("conversation_id must be non-empty text")
        if self.recipient_id is not None and (
            not isinstance(self.recipient_id, str) or not self.recipient_id
        ):
            raise ValueError("recipient_id must be non-empty text when present")


@dataclass(frozen=True, slots=True)
class RenderedMessage:
    text: str
    resource_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("rendered text must be non-empty text")
        resource_ids = tuple(self.resource_ids)
        for resource_id in resource_ids:
            _asset(resource_id)
        object.__setattr__(self, "resource_ids", resource_ids)


@dataclass(frozen=True, slots=True)
class MessageReceipt:
    status: "MessageStatus"
    platform_message_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, MessageStatus):
            raise TypeError("message receipt status must be a MessageStatus")
        if self.platform_message_id is not None and not isinstance(
            self.platform_message_id, str
        ):
            raise TypeError("platform message id must be text")


class MessageStatus(str, Enum):
    ACCEPTED = "accepted"
    FAILED = "failed"
    UNKNOWN = "unknown"


class MessagePort(Protocol):
    async def send(
        self, target: MessageTarget, payload: RenderedMessage
    ) -> MessageReceipt: ...


class HostIdentityPort(Protocol):
    async def is_administrator(self, actor_id: str) -> bool: ...


class UnitOfWork(Protocol):
    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class UnitOfWorkFactory(Protocol):
    async def begin(self) -> UnitOfWork: ...


class GrantRepository(Protocol):
    async def current(self, grant_id: str) -> GrantReference | None: ...

    async def revoke(self, grant: GrantReference) -> None: ...


class SubscriptionRepository(Protocol):
    async def current_revision(self, subscription_id: str) -> int | None: ...


class ResultOutlet(Protocol):
    """The sole core-to-host result route; modules receive no message port."""

    async def deliver(
        self, invocation: InvocationView, result: CapabilityResult
    ) -> None: ...
