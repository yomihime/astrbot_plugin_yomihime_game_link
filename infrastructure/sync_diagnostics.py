"""Bounded, memory-only calendar synchronization diagnostics.

The extension uses a stdlib logging signal, not a private Core import or an SDK
addition. This handler never formats the message/arguments and never propagates
to Host logs. Only the current runtime's ContextVar can receive a signal.
"""

from __future__ import annotations

import logging
import re
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import dataclass
from time import monotonic
from uuid import uuid4
from weakref import WeakSet, finalize

LOGGER_NAME = "yomihime_game_link.calendar_sync_diagnostic"
MAX_ATTEMPTS = 8
MAX_EVENTS = 48
_STAGES = frozenset(
    "core_start bind collector_start collector_return validate core_commit "
    "core_failure core_final sync_start sync_end source_start records_read "
    "records_claim records_commit source_skip source_end http_queue http_quota "
    "http_transport network_attempt transport_error http_response http_end decode parse".split()
)
_CODES = frozenset(
    "ok unknown error cancelled stale core_deadline collector_timeout source_deadline "
    "http_deadline invalid_utf8 call_error_commit_unknown revision_conflict "
    "source_not_declared request_rejected rate_limited credentials_unavailable "
    "redirect_disallowed upstream_error response_too_large invalid_response "
    "transport_failed timeout complete partial failed interrupted due cold manual "
    "cooldown cached not_due committed not_committed collector_error validation_error "
    "not_applicable in_progress completed transport_timeout dns_error connect_error "
    "tls_error connect_timeout read_timeout connection_closed response_payload "
    "connection_io other_transport".split()
)
_WARNINGS = frozenset(
    "INPUT_TOO_LARGE INVALID_UTF8 LINE_LIMIT MALFORMED_SOURCE EVENT_LIMIT "
    "PROPERTY_LIMIT INVALID_EVENT INVALID_TIMEZONE FLOATING_TIME_UNSUPPORTED "
    "UNSUPPORTED_RECURRENCE RECURRENCE_BUDGET SOURCE_BUDGET OUTPUT_LIMIT "
    "CONFLICTING_REVISION ORPHAN_OVERRIDE".split()
)
_SOURCES = frozenset(("ff14_calendar_primary", "ff14_calendar_fallback"))
_MODULE_STAGES = frozenset(
    "bind sync_start sync_end source_start records_read records_claim "
    "records_commit source_skip source_end decode parse".split()
)
_ID = re.compile(r"[0-9a-f]{32}\Z")


@dataclass(frozen=True)
class _Context:
    store: "SyncDiagnostics"
    invocation_id: str
    origin: str
    source: str | None = None
    started: float = 0.0
    budget_ms: int | None = None


_current: ContextVar[_Context | None] = ContextVar(
    "calendar_sync_diagnostic", default=None
)
_users: WeakSet = WeakSet()
_logger = logging.getLogger(LOGGER_NAME)
_previous_logger_settings = None


class _SignalHandler(logging.Handler):
    def emit(self, record):
        try:
            fields = record.__dict__.get("ygl_sync")
            if type(fields) is dict:
                stage = fields.get("stage")
                if type(stage) is str and stage in _MODULE_STAGES:
                    emit(
                        stage,
                        **{
                            key: value
                            for key, value in fields.items()
                            if key != "stage"
                        },
                    )
        except BaseException:
            # Diagnostics are never an authority or a reason to cancel work.
            pass


_handler = _SignalHandler()


def _detach_bridge():
    global _previous_logger_settings
    if not _users:
        _logger.removeHandler(_handler)
        if _previous_logger_settings is not None:
            level, propagate = _previous_logger_settings
            _logger.setLevel(level)
            _logger.propagate = propagate
            _previous_logger_settings = None


def emit(stage: str, **fields) -> None:
    """Record only closed metadata; failures cannot alter business semantics."""
    try:
        context = _current.get()
        if context is not None:
            context.store.record(context, stage, fields)
    except BaseException:
        pass


class SyncDiagnostics:
    """At most eight attempts of 48 events each, with a reserved Core final."""

    def __init__(self):
        global _previous_logger_settings
        self._attempts = OrderedDict()
        self._closed = False
        if not _users:
            _previous_logger_settings = (_logger.level, _logger.propagate)
        # Register before WeakSet so its removal callback runs before finalizer.
        self._finalizer = finalize(self, _detach_bridge)
        _users.add(self)
        if _handler not in _logger.handlers:
            _logger.addHandler(_handler)
        _logger.setLevel(logging.DEBUG)
        _logger.propagate = False

    def activate(self, invocation_id, origin, *, public=True) -> None:
        try:
            if not public:
                return _current.set(None)
            if (
                self._closed
                or type(invocation_id) is not str
                or not _ID.fullmatch(invocation_id)
            ):
                return
            origin = (
                origin
                if type(origin) is str
                and origin in ("scheduler", "command", "llm_tool", "web_public")
                else "unknown"
            )
            context = _current.get()
            if (
                context is None
                or context.store is not self
                or context.invocation_id != invocation_id
            ):
                return _current.set(
                    _Context(self, invocation_id, origin, started=monotonic())
                )
        except BaseException:
            pass

    @staticmethod
    def reset(token):
        try:
            if token is not None:
                _current.reset(token)
        except BaseException:
            pass

    def record(self, context, stage, fields):
        if self._closed or type(stage) is not str or stage not in _STAGES:
            return
        trace = self._attempts.get(context.invocation_id)
        if trace is not None and trace.get("_terminal"):
            return
        if trace is None:
            if stage == "core_start":
                budget = fields.get("budget_ms")
                if type(budget) is int and 0 <= budget <= 1_000_000:
                    _current.set(
                        _Context(
                            self,
                            context.invocation_id,
                            context.origin,
                            context.source,
                            context.started,
                            budget,
                        )
                    )
            # PUBLIC Core context is prepared for all capabilities; only the
            # fixed extension signal opts this invocation into retained data.
            if stage != "sync_start":
                return
            if (
                type(fields.get("region")) is not str
                or fields["region"] not in ("cn", "global")
                or type(fields.get("trigger")) is not str
                or fields["trigger"] not in ("due", "cold", "manual")
            ):
                return
            if (
                type(fields.get("region")) is not str
                or fields["region"] not in ("cn", "global")
                or type(fields.get("trigger")) is not str
                or fields["trigger"] not in ("due", "cold", "manual")
            ):
                return
            trace = {
                "attempt_id": uuid4().hex,
                "invocation_id": context.invocation_id,
                "origin": context.origin,
                "region": None,
                "trigger": "background" if context.origin == "scheduler" else "unknown",
                "network_attempts": 0,
                # session.request entry is not proof of headers sent or peer receipt.
                "wire_issued": "unknown",
                "records_evidence": "service_call_return_or_error_commit_unknown",
                "core_final": "in_progress",
                "core_failure_code": None,
                "core_budget_ms": context.budget_ms,
                "capability_status": None,
                "observation_completeness": None,
                "events": [
                    {
                        "stage": "core_start",
                        "elapsed_ms": 0,
                        "budget_ms": context.budget_ms,
                        "network_attempts": 0,
                    }
                ],
                "dropped_events": 0,
                "_started": context.started,
            }
            self._attempts[context.invocation_id] = trace
            while len(self._attempts) > MAX_ATTEMPTS:
                self._attempts.popitem(last=False)
        region = fields.get("region")
        if type(region) is str and region in ("cn", "global"):
            trace["region"] = region
        trigger = fields.get("trigger")
        if type(trigger) is str and trigger in ("due", "cold", "manual"):
            trace["trigger"] = trigger
        source = fields.get("source")
        if type(source) is str and source in _SOURCES:
            context = _Context(
                self,
                context.invocation_id,
                context.origin,
                source,
                context.started,
                context.budget_ms,
            )
            _current.set(context)
        event = {
            "stage": stage,
            "elapsed_ms": min(
                86_400_000, max(0, int((monotonic() - trace["_started"]) * 1000))
            ),
        }
        if context.source is not None:
            event["source"] = context.source
        code = fields.get("code")
        if type(code) is str:
            event["code"] = code if code in _CODES else "unknown"
        status = fields.get("http_status")
        if type(status) is int and 100 <= status <= 599:
            event["http_status"] = status
        completeness = fields.get("completeness")
        if type(completeness) is str and completeness in (
            "complete",
            "partial",
            "failed",
        ):
            event["completeness"] = completeness
        warnings = fields.get("warnings")
        if type(warnings) in (tuple, list):
            event["warnings"] = list(
                dict.fromkeys(
                    w for w in warnings[:16] if type(w) is str and w in _WARNINGS
                )
            )
        for key in ("occurrence_count", "warning_count", "budget_ms"):
            value = fields.get(key)
            if type(value) is int and 0 <= value <= 1_000_000:
                event[key] = value
        result_status = fields.get("capability_status")
        if type(result_status) is str and result_status in (
            "success",
            "partial_success",
            "error",
            "needs_selection",
        ):
            event["capability_status"] = result_status
            trace["capability_status"] = result_status
        observation = fields.get("observation_completeness")
        if type(observation) is str and observation in (
            "complete",
            "partial",
            "failed",
        ):
            event["observation_completeness"] = observation
            trace["observation_completeness"] = observation
        if stage == "network_attempt":
            trace["network_attempts"] = min(999, trace["network_attempts"] + 1)
        event["network_attempts"] = trace["network_attempts"]
        if stage == "core_final":
            trace["core_final"] = (
                "error"
                if event.get("code") == "completed"
                and trace["core_failure_code"] is not None
                else event.get("code", "unknown")
            )
            event["code"] = trace["core_final"]
            trace["_terminal"] = True
        if stage == "core_failure":
            if (
                trace["core_failure_code"] is None
                or event.get("code") == "core_deadline"
            ):
                trace["core_failure_code"] = event.get("code", "unknown")
        # Preserve the final classification even after a noisy extension hits its cap.
        events = trace["events"]
        if len(events) >= MAX_EVENTS - 1 and stage != "core_final":
            trace["dropped_events"] = min(999, trace["dropped_events"] + 1)
        elif len(events) < MAX_EVENTS:
            events.append(event)

    def snapshot(self):
        """A detached JSON projection, for the authenticated management route only."""
        import copy

        return {
            "schema_version": 1,
            "retention": "memory_only",
            "max_attempts": MAX_ATTEMPTS,
            "max_events": MAX_EVENTS,
            "attempts": [
                copy.deepcopy({k: v for k, v in trace.items() if not k.startswith("_")})
                for trace in self._attempts.values()
            ],
        }

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._attempts.clear()
        context = _current.get()
        if context is not None and context.store is self:
            _current.set(None)
        _users.discard(self)
        self._finalizer()


__all__ = ["SyncDiagnostics", "emit"]
