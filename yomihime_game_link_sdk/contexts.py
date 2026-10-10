from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class InvocationOrigin(StrEnum):
    """The trusted bridge that created an invocation."""

    COMMAND = "command"
    WEB_PUBLIC = "web_public"
    LLM_TOOL = "llm_tool"
    SCHEDULER = "scheduler"
    SUBSCRIPTION = "subscription"
    ADMIN = "admin"


class InvocationConversationKind(StrEnum):
    """Trusted route kind attached only to subscription invocations."""

    DIRECT = "direct"
    GROUP = "group"


class InvocationSubscriptionScope(StrEnum):
    """Persisted subscription data scope supplied by the trusted host."""

    PUBLIC = "public"
    USER = "user"
    AUTHORIZED = "authorized"


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
    adapter_id: str | None = None
    capability_id: str | None = None
    delivery_route: str | None = None
    conversation_kind: InvocationConversationKind | None = None
    subscription_scope: InvocationSubscriptionScope | None = None
    public_session_id: str | None = None


@dataclass(frozen=True, slots=True)
class MessageContext:
    """Necessary message data from a bound read; never an authority proof."""

    text: str = field(repr=False)
    event_ref: str = field(repr=False)
