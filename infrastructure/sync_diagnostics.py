"""Bounded memory-only Core execution and HTTP stage observations."""

from __future__ import annotations

import copy
import re
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import dataclass
from time import monotonic

from yomihime_game_link_sdk.errors import AccessDenied, OperationTimeout

MAX_ATTEMPTS = 8
MAX_EVENTS = 48
_STAGES = frozenset(
    "core_start collector_start collector_return validate core_commit core_failure core_final http_queue http_quota http_transport network_attempt transport_error http_response http_end".split()
)
_LABEL = re.compile(r"[a-zA-Z][a-zA-Z0-9_.-]{0,95}\Z")


@dataclass(frozen=True)
class _Context:
    store: object
    invocation_id: str
    origin: str
    started: float


_current = ContextVar("runtime_diagnostic", default=None)


def emit(stage, **fields):
    context = _current.get()
    if context is not None:
        try:
            context.store.record(context, stage, fields)
        except (AccessDenied, OperationTimeout):
            raise
        except Exception:
            pass


class SyncDiagnostics:
    def __init__(self):
        self._attempts = OrderedDict()
        self._closed = False

    def activate(self, invocation_id, origin, *, public=True):
        if self._closed or not public:
            return _current.set(None)
        if type(invocation_id) is not str or not re.fullmatch(
            r"[0-9a-f]{32}", invocation_id
        ):
            return None
        context = _current.get()
        if (
            context is not None
            and context.store is self
            and context.invocation_id == invocation_id
        ):
            return None
        return _current.set(
            _Context(
                self,
                invocation_id,
                origin
                if origin in {"scheduler", "command", "llm_tool", "web_public"}
                else "unknown",
                monotonic(),
            )
        )

    @staticmethod
    def reset(token):
        if token is not None:
            _current.reset(token)

    def record(self, context, stage, fields):
        if self._closed or stage not in _STAGES:
            return
        trace = self._attempts.get(context.invocation_id)
        if trace is None:
            trace = {
                "invocation_id": context.invocation_id,
                "origin": context.origin,
                "events": [],
                "dropped_events": 0,
                "terminal": False,
                "network_attempts": 0,
                "wire_issued": "unknown",
                "core_final": "in_progress",
                "core_failure_code": None,
                "core_budget_ms": None,
                "capability_status": None,
                "observation_completeness": None,
            }
            self._attempts[context.invocation_id] = trace
            while len(self._attempts) > MAX_ATTEMPTS:
                self._attempts.popitem(last=False)
        if trace["terminal"]:
            return
        event = {
            "stage": stage,
            "elapsed_ms": min(
                86400000, max(0, int((monotonic() - context.started) * 1000))
            ),
        }
        for key in ("source", "code", "capability_status", "observation_completeness"):
            value = fields.get(key)
            if type(value) is str and _LABEL.fullmatch(value):
                event[key] = value
        for key in ("budget_ms", "http_status"):
            value = fields.get(key)
            if type(value) is int and 0 <= value <= 1000000:
                event[key] = value
        if stage == "network_attempt":
            trace["network_attempts"] += 1
        event["network_attempts"] = trace["network_attempts"]
        if stage == "core_start":
            trace["core_budget_ms"] = event.get("budget_ms")
        for key in ("capability_status", "observation_completeness"):
            if key in event:
                trace[key] = event[key]
        if stage == "core_failure" and (
            trace["core_failure_code"] is None or event.get("code") == "core_deadline"
        ):
            trace["core_failure_code"] = event.get("code", "unknown")
        if stage == "core_final":
            trace["core_final"] = (
                "error"
                if event.get("code") == "completed" and trace["core_failure_code"]
                else event.get("code", "unknown")
            )
            event["code"] = trace["core_final"]
        if len(trace["events"]) >= MAX_EVENTS - 1 and stage != "core_final":
            trace["dropped_events"] += 1
        elif len(trace["events"]) < MAX_EVENTS:
            trace["events"].append(event)
        if stage == "core_final":
            trace["terminal"] = True

    def snapshot(self):
        return {
            "schema_version": 2,
            "retention": "memory_only",
            "max_attempts": MAX_ATTEMPTS,
            "max_events": MAX_EVENTS,
            "attempts": copy.deepcopy(list(self._attempts.values())),
        }

    def close(self):
        self._closed = True
        self._attempts.clear()
        current = _current.get()
        if current is not None and current.store is self:
            _current.set(None)


__all__ = ["SyncDiagnostics", "emit"]
