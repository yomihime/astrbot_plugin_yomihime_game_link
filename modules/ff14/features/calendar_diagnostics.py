"""FF14 calendar event schema and invocation-local SDK transport."""

from contextvars import ContextVar
from functools import wraps

from yomihime_game_link_sdk import DiagnosticDeclaration

from .diagnostic_declarations import array, integer, obj, text

STAGES = "bind sync_start sync_end source_start records_read records_claim records_commit source_skip source_end decode parse".split()
WARNINGS = "INPUT_TOO_LARGE INVALID_UTF8 LINE_LIMIT MALFORMED_SOURCE EVENT_LIMIT PROPERTY_LIMIT INVALID_EVENT INVALID_TIMEZONE FLOATING_TIME_UNSUPPORTED UNSUPPORTED_RECURRENCE RECURRENCE_BUDGET SOURCE_BUDGET OUTPUT_LIMIT CONFLICTING_REVISION ORPHAN_OVERRIDE".split()
CODES = "ok unknown error cancelled stale source_deadline invalid_utf8 call_error_commit_unknown revision_conflict source_not_declared request_rejected rate_limited credentials_unavailable redirect_disallowed upstream_error response_too_large invalid_response transport_failed timeout complete partial failed interrupted due cold manual cooldown cached not_due committed not_committed".split()
FIELDS = {
    "stage": text(values=STAGES),
    "region": text(values=["cn", "global"]),
    "source": text(values=["ff14_calendar_primary", "ff14_calendar_fallback"]),
    "trigger": text(values=["due", "cold", "manual"]),
    "code": text(values=CODES),
    "completeness": text(values=["complete", "partial", "failed"]),
    "warnings": array(text(values=WARNINGS), 16),
    "occurrence_count": integer(0, 1000000),
    "warning_count": integer(0, 1000000),
    "budget_ms": integer(0, 1000000),
    "http_status": integer(100, 599),
}
DECLARATION = DiagnosticDeclaration(
    "CalendarSync", obj(FIELDS, [key for key in FIELDS if key != "stage"])
)
_bound = ContextVar("ff14_calendar_diagnostics", default=None)


def diagnosed(method):
    @wraps(method)
    async def run(self, invocation, *args, **kwargs):
        bound = await self.services.scopes.bind(invocation)
        token = _bound.set(bound)
        try:
            return await method(self, invocation, *args, **kwargs)
        finally:
            _bound.reset(token)

    return run


async def diagnostic(stage, **fields):
    bound = _bound.get()
    if bound is None:
        return
    port = getattr(bound, "diagnostics", None)
    if "code" in fields and fields["code"] not in CODES:
        fields["code"] = "unknown"
    if "warnings" in fields:
        fields["warnings"] = [
            code for code in fields["warnings"][:16] if code in WARNINGS
        ]
    # No response means no HTTP status, not a malformed integer field.
    if fields.get("http_status", 1) is None:
        fields.pop("http_status")
    if port is not None:
        await port.emit("CalendarSync", {"stage": stage, **fields})


def current_bound():
    return _bound.get()
