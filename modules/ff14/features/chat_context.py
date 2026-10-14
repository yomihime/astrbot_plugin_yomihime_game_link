"""Bounded FF14 conversational business state, independent of Host history.

Semantic fields come from the existing model conversation. They never carry
identity or authorization: all state keys and event fences use InvocationView
and the current MessageAccess result supplied by Core.
"""

from __future__ import annotations

import secrets
from collections import OrderedDict
from dataclasses import dataclass, field
from time import monotonic

from ..query_resolution import QueryResolutionError, TrustedOwner

LANGUAGES = ("zh", "non_zh", "unknown")
ACTIONS = ("query", "continue", "correction", "clarify")


def semantic_action(parameters):
    action = parameters.get("action", "query")
    if type(action) is not str or action not in ACTIONS:
        raise QueryResolutionError("action 必须为 query/continue/correction/clarify。")
    return action


def chat_owner(context):
    if not all((context.actor_id, context.conversation_id, context.adapter_id)):
        raise QueryResolutionError("缺少可信用户/会话绑定。")
    return TrustedOwner(
        context.actor_id,
        context.conversation_id,
        f"llm_tool:{context.adapter_id}:{context.module_id}:{context.module_epoch}",
    )


def language_region(parameters, default):
    """Interpret language of the user's request, never names or reply language."""
    request = parameters.get("request_language", "unknown")
    previous = parameters.get("context_language", "unknown")
    if any(
        type(value) is not str or value not in LANGUAGES
        for value in (request, previous)
    ):
        raise QueryResolutionError("请求语言必须为 zh/non_zh/unknown。")
    language = request if request != "unknown" else previous
    if language == "unknown":
        return default, "core_default"
    return ("cn" if language == "zh" else "global"), (
        "request_language" if request != "unknown" else "context_language"
    )


@dataclass(slots=True)
class PendingTarget:
    generation: str
    event_ref: str
    expires: float
    values: dict = field(default_factory=dict)
    batch_id: str | None = None
    candidates: tuple = ()
    consumed: bool = False
    default_basis: str = "core_default"
    explicit_realm: bool = False
    confirmation_ref: str | None = None


class PendingTargets:
    """At most 128 current targets, expiring in at most five minutes."""

    def __init__(self, *, clock=monotonic, ttl=300, capacity=128):
        if type(ttl) not in (int, float) or not 0 < ttl <= 300:
            raise ValueError("invalid pending TTL")
        if type(capacity) is not int or not 1 <= capacity <= 128:
            raise ValueError("invalid pending capacity")
        self.clock, self.ttl, self.capacity = clock, ttl, capacity
        self.states = OrderedDict()

    def get(self, key):
        for owner in tuple(self.states):
            if self.states[owner].expires <= self.clock():
                del self.states[owner]
        return self.states.get(key)

    def begin(self, key, event_ref, values):
        self.get(key)
        self.states.pop(key, None)
        while len(self.states) >= self.capacity:
            self.states.popitem(last=False)
        state = PendingTarget(
            secrets.token_urlsafe(18), event_ref, self.clock() + self.ttl, dict(values)
        )
        self.states[key] = state
        return state

    def current(self, key, state):
        if self.get(key) is not state:
            raise QueryResolutionError("查询状态已失效，请重新查询。")

    def close(self):
        self.states.clear()
