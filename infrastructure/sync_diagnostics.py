"""Bounded memory-only Core execution and HTTP stage observations."""

from __future__ import annotations

import copy
import re
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import dataclass
from time import monotonic

from ..yomihime_game_link_sdk.errors import AccessDenied, OperationTimeout

MAX_ATTEMPTS = 8
MAX_EVENTS = 48
_CACHE_FAILURE_STAGES = frozenset({"cache_read", "cache_write"})
_STAGES = (
    frozenset(
        "core_start bind handler_start handler_return publication output_start output_return collector_start collector_return validate core_commit core_failure core_final http_queue http_quota http_transport network_attempt transport_error http_response http_end auth_failure".split()
    )
    | _CACHE_FAILURE_STAGES
)
_LABEL = re.compile(r"[a-zA-Z][a-zA-Z0-9_.-]{0,95}\Z")
_LOGICAL_ID = re.compile(r"[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*\Z")


def _logical_identity(origin, module_id, capability_id, collector_id):
    """Only trusted declaration identifiers; invalid metadata is never logged."""

    def valid(value):
        return type(value) is str and len(value) <= 96 and _LOGICAL_ID.fullmatch(value)

    if type(module_id) is not str or len(module_id) > 193:
        return ()
    parts = module_id.split("/")
    if len(parts) != 2 or not all(valid(part) for part in parts):
        return ()
    identity = [("module_id", module_id)]
    if origin == "scheduler":
        if (
            capability_id is not None
            or collector_id is not None
            and not valid(collector_id)
        ):
            return ()
        if collector_id is not None:
            identity.append(("collector_id", collector_id))
    elif origin in {"command", "llm_tool", "web_public"}:
        if (
            collector_id is not None
            or capability_id is not None
            and not valid(capability_id)
        ):
            return ()
        if capability_id is not None:
            identity.append(("capability_id", capability_id))
    else:
        return ()
    return tuple(identity)


_WARNING_CODES = frozenset(
    "upstream_error unparsed auth_required auth_expired module_unavailable unknown rate_limited".split()
)
_FAILURE_CODES = frozenset(
    {"core_deadline", "validation_error", "error", "collector_timeout"}
)
_QUIET_TERMINATIONS = frozenset({"cancelled", "stale"})
_HTTP_FAILURE_CODES = frozenset(
    "source_not_declared request_rejected request_too_large response_too_large rate_limited concurrency_limited timeout credentials_unavailable transport_failed invalid_response upstream_error redirect_disallowed http_deadline error".split()
)


@dataclass
class _Context:
    store: object
    invocation_id: str
    origin: str
    started: float
    trace: dict | None = None
    pending_warning: tuple | None = None
    terminal: bool = False
    http_failure: dict | None = None
    auth_failure: dict | None = None
    phase: str = "core_start"
    identity: tuple[tuple[str, str], ...] = ()


_current = ContextVar("runtime_diagnostic", default=None)


def defer_failure_report(invocation_id, sink, event):
    """Keep at most one reviewed warning until the invocation is terminal."""
    context = _current.get()
    if (
        context is not None
        and context.invocation_id == invocation_id
        and not context.terminal
        and context.pending_warning is None
    ):
        context.pending_warning = (sink, event)


def related_auth_failure(invocation_id):
    context = _current.get()
    if (
        context is not None
        and context.invocation_id == invocation_id
        and context.auth_failure is not None
    ):
        return dict(context.auth_failure)
    return None


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
    def __init__(self, *, warning_sink=None, trace_sink=None, trace_enabled=None):
        self._attempts = OrderedDict()
        self._closed = False
        self._warning_sink = warning_sink
        self._trace_sink = trace_sink
        self._trace_enabled = trace_enabled

    def activate(
        self,
        invocation_id,
        origin,
        *,
        public=True,
        module_id=None,
        capability_id=None,
        collector_id=None,
    ):
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
                identity=_logical_identity(
                    origin, module_id, capability_id, collector_id
                ),
            )
        )

    @staticmethod
    def reset(token):
        if token is not None:
            _current.reset(token)

    def record(self, context, stage, fields):
        if self._closed or context.terminal or stage not in _STAGES:
            return
        # Recent-history eviction must not reset a live invocation's counters
        # or the failure classification used to suppress a stale warning.
        trace = context.trace
        if trace is None:
            trace = {
                "invocation_id": context.invocation_id,
                "origin": context.origin,
                **dict(context.identity),
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
            context.trace = trace
        if self._attempts.get(context.invocation_id) is not trace:
            self._attempts[context.invocation_id] = trace
            while len(self._attempts) > MAX_ATTEMPTS:
                self._attempts.popitem(last=False)
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
        if stage in {
            "bind",
            "handler_start",
            "handler_return",
            "publication",
            "output_start",
            "output_return",
            "collector_start",
            "collector_return",
            "validate",
            "core_commit",
        }:
            context.phase = stage
        if stage == "network_attempt":
            trace["network_attempts"] += 1
        if stage in _CACHE_FAILURE_STAGES and event.get("code") == "cache_unavailable":
            # Keep degradation visible even after the bounded event list fills.
            counts = trace.setdefault("cache_failures", {})
            counts[stage] = min(1_000_000, counts.get(stage, 0) + 1)
        if event.get("code") in _HTTP_FAILURE_CODES and (
            stage == "auth_failure"
            or stage == "http_end"
            and (
                context.http_failure is None
                or context.http_failure["stage"] != "auth_failure"
                or context.http_failure["source"] != event.get("source")
                or context.http_failure["category"] != event["code"]
            )
        ):
            status = event.get("http_status")
            context.http_failure = {
                "stage": stage,
                "source": event.get("source"),
                "category": event["code"],
                "http_status": status
                if status is not None and 100 <= status <= 599
                else None,
                "elapsed_ms": event["elapsed_ms"],
            }
            if stage == "auth_failure":
                context.auth_failure = context.http_failure
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
            context.terminal = True
            trace["terminal"] = True
            business_code = fields.get("failure_code")
            runtime_code = trace["core_failure_code"]
            quiet = (
                event["code"] in _QUIET_TERMINATIONS
                or runtime_code in _QUIET_TERMINATIONS
            )
            warning_reported = False
            pending_warning = context.pending_warning
            context.pending_warning = None
            if not quiet and pending_warning is not None:
                sink, envelope = pending_warning
                try:
                    sink({**envelope, "elapsed_ms": event["elapsed_ms"]})
                    warning_reported = True
                except Exception:
                    # The generic projection is still available as a fallback.
                    pass
            collection_failed = (
                context.origin == "scheduler"
                and trace["observation_completeness"] == "failed"
            )
            partial_upstream_failure = (
                trace["capability_status"] == "partial_success"
                and context.http_failure is not None
            )
            if (
                self._warning_sink is not None
                and not quiet
                and not warning_reported
                and (
                    business_code in _WARNING_CODES
                    or runtime_code in _FAILURE_CODES
                    or collection_failed
                    or partial_upstream_failure
                )
            ):
                failure = {
                    "trace": context.invocation_id,
                    "origin": context.origin,
                    **dict(context.identity),
                    "stage": "collection"
                    if context.origin == "scheduler"
                    else "capability",
                    "category": runtime_code
                    if runtime_code in _FAILURE_CODES
                    else (
                        "collection_failed"
                        if collection_failed
                        else "partial_upstream_failure"
                        if partial_upstream_failure
                        else business_code
                    ),
                    "elapsed_ms": event["elapsed_ms"],
                    "failure_stage": (
                        context.phase
                        if runtime_code in _FAILURE_CODES
                        else context.http_failure["stage"]
                        if context.http_failure is not None
                        else "collector_return"
                        if collection_failed
                        else context.phase
                    ),
                }
                if context.http_failure is not None:
                    failure["last_http_failure"] = context.http_failure
                try:
                    self._warning_sink(failure)
                except (AccessDenied, OperationTimeout):
                    raise
                except Exception:
                    pass
            if self._trace_sink is not None and (
                self._trace_enabled is None or self._trace_enabled()
            ):
                # One bounded terminal trace, copied only when DEBUG is enabled.
                # Values here are Core-filtered metadata, never module payloads.
                self._trace_sink(copy.deepcopy(trace))

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
