"""Finite failure observations and reviewed command text for FFLogs."""

from time import monotonic

from ....yomihime_game_link_sdk.errors import ServiceUnavailable, SourceHttpError
from ....yomihime_game_link_sdk.results import ErrorCode

EVENT = "FF14LogsFailure"
SOURCES = ("universalis_market", "fflogs_public_cn", "fflogs_public_global")
STAGE_MESSAGES = {
    "realm_directory": "服务器目录暂不可用，无法判断 FFLogs 区域，请稍后重试。",
    "authentication": "FFLogs 授权暂不可用，请联系管理员检查对应区域的来源配置。",
    "server_directory": "FFLogs 授权或服务器目录查询失败，请稍后重试。",
    "character_query": "FFLogs 角色查询失败，请稍后重试。",
    "ranking_schema": "FFLogs 排名筛选条件暂不可用，请稍后重试。",
    "ranking_metadata": "FFLogs 战绩目录暂不可用。",
    "ranking_history": "FFLogs 历史成绩查询未完成。",
}
CATEGORIES = (
    "credentials_unavailable",
    "unauthorized",
    "forbidden",
    "rate_limited",
    "timeout",
    "http_error",
    "transport_error",
    "request_rejected",
    "payload_parse",
    "graphql_error",
    "incomplete_directory",
    "identity_mismatch",
    "selection_mismatch",
    "service_unavailable",
    "internal_error",
    "region_unresolved",
)
COMMAND_HINTS = {
    ErrorCode.UPSTREAM_ERROR: frozenset(STAGE_MESSAGES.values()),
    ErrorCode.UNPARSED: frozenset(STAGE_MESSAGES.values()),
    ErrorCode.MODULE_UNAVAILABLE: frozenset(STAGE_MESSAGES.values()),
}


def _status(value):
    return value if type(value) is int and 100 <= value <= 599 else None


def _elapsed(started):
    return min(86400000, max(0, int((monotonic() - started) * 1000)))


def _http_category(error, status):
    if status == 401:
        return "unauthorized"
    if status == 403:
        return "forbidden"
    if status == 429 or error.code in {"rate_limited", "concurrency_limited"}:
        return "rate_limited"
    return {
        "credentials_unavailable": "credentials_unavailable",
        "timeout": "timeout",
        "upstream_error": "http_error",
        "transport_failed": "transport_error",
        "invalid_response": "payload_parse",
        "response_too_large": "payload_parse",
    }.get(error.code, "request_rejected")


class LookupFailures:
    """Aggregate equivalent failures by stage and status, then emit once."""

    def __init__(self):
        self.port = None
        self.started = monotonic()
        self.stage = self.source = self.last_status = None
        self.stage_started = self.started
        self.failures = {}

    def begin(self, source, stage):
        self.source, self.stage = source, stage
        self.last_status = None
        self.stage_started = monotonic()

    def wrap(self, http):
        failures = self

        class ObservedHttp:
            async def fetch(self, request):
                failures.last_status = None
                response = await http.fetch(request)
                failures.last_status = _status(response.status_code)
                return response

        return ObservedHttp()

    def record(self, category, *, status=None, started=None):
        if self.source is None or self.stage is None:
            return
        stage = (
            "authentication"
            if category in {"credentials_unavailable", "unauthorized"}
            and self.source != "universalis_market"
            else self.stage
        )
        key = (self.source, stage, category, _status(status))
        if key not in self.failures:
            if len(self.failures) >= 8:
                return
            self.failures[key] = {
                "source": self.source,
                "stage": stage,
                "category": category,
                "http_status": key[3],
                "elapsed_ms": 0,
                "count": 0,
            }
        row = self.failures[key]
        row["count"] = min(10000, row["count"] + 1)
        row["elapsed_ms"] = max(
            row["elapsed_ms"],
            _elapsed(self.stage_started if started is None else started),
        )

    def source_error(self, error, *, started=None):
        if isinstance(error, SourceHttpError):
            status = _status(error.status_code)
            self.record(_http_category(error, status), status=status, started=started)
        elif isinstance(error, TimeoutError):
            self.record("timeout", started=started)
        elif isinstance(error, ServiceUnavailable):
            self.record("service_unavailable", started=started)
        elif not isinstance(error, PermissionError):
            self.record("internal_error", started=started)

    async def flush(self):
        if not self.failures or self.port is None:
            return
        try:
            await self.port.emit(
                EVENT,
                {
                    "failures": list(self.failures.values()),
                    "elapsed_ms": _elapsed(self.started),
                },
            )
        except Exception:
            # Optional telemetry cannot change the lookup result or expose a
            # rejected payload. Cancellation still follows the caller.
            pass
