"""SDK-only public calendar query and scheduled collection."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Callable, Mapping

from yomihime_sdk.api.contexts import InvocationView
from yomihime_sdk.api.display import Privacy
from yomihime_sdk.api.results import (
    CapabilityResult,
    ErrorCode,
    ErrorDetail,
    ResultStatus,
)
from yomihime_sdk.api.services import (
    HttpRequest,
    ModuleServices,
    SourceHttp,
    SourceHttpError,
)
from yomihime_sdk.api.storage import JsonObject, OwnerScope
from yomihime_sdk.api.subscriptions import (
    CollectionView,
    NormalizedInput,
    Observation,
    ObservationCompleteness,
)

from ..config import FF14ConfigSnapshot
from .calendar_evaluator import (
    DAILY_WINDOW_DAYS,
    CalendarSourceSpec,
    build_calendar_document,
    calendar_source_spec,
    calendar_warning_messages,
    load_timezone,
)
from .calendar_ics import MAX_WINDOW, parse_ics
from .calendar_models import CalendarOccurrence, CalendarParseResult, Completeness

MODULE_ID = "ff14"
COLLECTOR_ID = "ff14.calendar.collect"
SCHEDULE_SOURCE_ID = "ff14_calendar_primary"
FALLBACK_SOURCE_ID = "ff14_calendar_fallback"
KEY_VERSION = 1
DATA_VERSION = 1
DEFAULT_QUERY_DAYS = DAILY_WINDOW_DAYS
MAX_QUERY_DAYS = 30
MAX_STORED_OCCURRENCES = 2_000
MAX_STORED_SUMMARY = 240

_REGIONS = {"cn": "国服", "global": "国际服"}


@dataclass(frozen=True, slots=True)
class CalendarSourceSnapshot:
    region: str
    completeness: ObservationCompleteness
    window_start: datetime
    window_end: datetime
    source_id: str | None = None
    source_variant: str | None = None
    source_url: str | None = None
    source_version: str | None = None
    occurrences: tuple[CalendarOccurrence, ...] = ()
    warning_codes: tuple[str, ...] = ()


class CalendarSourceReader:
    """Fetch only fixed declared sources and parse one source at a time."""

    def __init__(self, http: SourceHttp) -> None:
        self._http = http

    async def read(
        self,
        region: str,
        *,
        window_start: datetime,
        window_end: datetime,
        display_timezone: str,
        reject_floating: bool,
    ) -> CalendarSourceSnapshot:
        region = normalize_region(region)
        failures: list[str] = []
        attempts: list[tuple[CalendarSourceSpec, bytes, CalendarParseResult]] = []
        for kind, source_id in (
            ("primary", SCHEDULE_SOURCE_ID),
            ("fallback", FALLBACK_SOURCE_ID),
        ):
            spec = calendar_source_spec(region, source_id)
            if spec is None:
                failures.append(f"{kind}_source_unqualified")
                continue
            try:
                response = await self._http.fetch(
                    HttpRequest(spec.source_id, spec.path)
                )
                if response.status_code < 200 or response.status_code >= 300:
                    raise SourceHttpError(
                        "upstream_error", status_code=response.status_code
                    )
                parsed = parse_ics(
                    response.body,
                    window_start=window_start,
                    window_end=window_end,
                    display_timezone=display_timezone,
                    source_id=source_id,
                    reject_floating=reject_floating,
                )
                attempts.append((spec, response.body, parsed))
            except SourceHttpError as exc:
                if exc.code == "cancelled":
                    raise
                failures.extend(_source_failure_codes(kind, exc))
                continue

            if parsed.completeness is Completeness.COMPLETE:
                warnings = _attempt_warnings(parsed.warnings, failures)
                return _snapshot_from_attempt(
                    region,
                    spec,
                    response.body,
                    parsed,
                    window_start,
                    window_end,
                    warnings,
                )
            failures.append(
                "primary_source_partial"
                if kind == "primary"
                else "fallback_source_partial"
            )

        # Full feeds win above; partial feeds with events precede partial empty windows.
        if attempts:
            spec, raw, parsed = next(
                (attempt for attempt in attempts if attempt[2].occurrences), attempts[0]
            )
            return _snapshot_from_attempt(
                region,
                spec,
                raw,
                parsed,
                window_start,
                window_end,
                _attempt_warnings(parsed.warnings, failures),
                completeness=ObservationCompleteness.PARTIAL,
            )
        return CalendarSourceSnapshot(
            region=region,
            completeness=ObservationCompleteness.FAILED,
            window_start=window_start,
            window_end=window_end,
            warning_codes=tuple(_unique(failures + ["source_unavailable"])),
        )


class CalendarCollector:
    """Periodic SDK collector for shared public calendar snapshots."""

    def __init__(
        self,
        services: ModuleServices,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._services = services
        self._clock = clock or _utc_now

    def normalize(self, parameters: JsonObject) -> NormalizedInput:
        if not isinstance(parameters, Mapping):
            raise ValueError("calendar parameters must be an object")
        if set(parameters) != {"region"}:
            raise ValueError("calendar parameters only accept region")
        return NormalizedInput({"region": normalize_region(parameters["region"])})

    async def collect(
        self,
        context: CollectionView,
        parameters: NormalizedInput,
        previous: Observation | None,
    ) -> Observation:
        del previous
        if context.invocation is None:
            raise ValueError("scheduled calendar collection requires an invocation")
        if (
            context.key.module_id.rsplit("/", 1)[-1] != MODULE_ID
            or context.key.collector_id != COLLECTOR_ID
            or context.key.key_version != KEY_VERSION
            or context.key.source_id != SCHEDULE_SOURCE_ID
            or context.key.scope != OwnerScope.public()
            or context.key.parameters != self.normalize(dict(parameters.values))
        ):
            raise ValueError("calendar collection key does not match its contract")
        region = str(parameters.values["region"])
        started_at = _aware_utc(self._clock())
        start_of_utc_date = datetime.combine(started_at.date(), time.min, tzinfo=UTC)
        window_start = start_of_utc_date - timedelta(days=1)
        window_end = start_of_utc_date + timedelta(days=9)
        if window_end - window_start > MAX_WINDOW:
            raise ValueError("calendar collection window exceeds parser policy")
        bound = await self._services.scopes.bind(context.invocation)
        snapshot = await CalendarSourceReader(bound.http).read(
            region,
            window_start=window_start,
            window_end=window_end,
            display_timezone="UTC",
            reject_floating=True,
        )
        stored_occurrences = snapshot.occurrences[:MAX_STORED_OCCURRENCES]
        completeness = snapshot.completeness
        warnings = list(snapshot.warning_codes)
        if len(snapshot.occurrences) > len(stored_occurrences):
            completeness = ObservationCompleteness.PARTIAL
            warnings.append("stored_occurrence_limit")
        covered_ids = tuple(
            _occurrence_id(snapshot.source_id or "", item)
            for item in stored_occurrences
        )
        if completeness is ObservationCompleteness.PARTIAL and not covered_ids:
            completeness = ObservationCompleteness.FAILED
        if completeness is ObservationCompleteness.FAILED:
            covered_ids = ()
        payload = {
            "schema_version": DATA_VERSION,
            "region": region,
            "completeness": completeness.value,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "source_id": snapshot.source_id or "",
            "source_variant": snapshot.source_variant or "",
            "source_url": snapshot.source_url or "",
            "source_version": snapshot.source_version or "",
            "warnings": tuple(_unique(warnings)),
            "occurrences": tuple(
                _serialize_occurrence(item, snapshot.source_id or "")
                for item in stored_occurrences
            ),
        }
        return Observation(
            observation_id=str(uuid.uuid4()),
            key=context.key,
            data_version=DATA_VERSION,
            source_observed_at=None,
            collected_at=started_at,
            completeness=completeness,
            covered_ids=covered_ids
            if completeness is ObservationCompleteness.PARTIAL
            else (),
            payload=payload,
        )


class CalendarQuery:
    """One-shot public calendar query using the same fixed source reader."""

    def __init__(
        self,
        services: ModuleServices,
        *,
        clock: Callable[[], datetime] | None = None,
        config: FF14ConfigSnapshot | None = None,
    ) -> None:
        self._services = services
        self._clock = clock or _utc_now
        self._config = config or FF14ConfigSnapshot()

    async def invoke(
        self, context: InvocationView, parameters: JsonObject
    ) -> CapabilityResult:
        normalized = _normalize_query(parameters, self._config)
        if isinstance(normalized, CapabilityResult):
            return normalized
        region, days, timezone_name = normalized
        zone = load_timezone(timezone_name)
        now = _aware_utc(self._clock())
        local_date = now.astimezone(zone).date()
        # UTC date padding covers the chosen local window even at offset/date edges.
        utc_start = datetime.combine(local_date - timedelta(days=1), time.min, UTC)
        utc_end = datetime.combine(local_date + timedelta(days=days + 1), time.min, UTC)
        if utc_end - utc_start > MAX_WINDOW:
            return _error(ErrorCode.PARAMETER_ERROR, "查询天数超出日历范围。")
        try:
            bound = await self._services.scopes.bind(context)
            snapshot = await CalendarSourceReader(bound.http).read(
                region,
                window_start=utc_start,
                window_end=utc_end,
                display_timezone=timezone_name,
                reject_floating=False,
            )
        except SourceHttpError as exc:
            if exc.code == "cancelled":
                raise
            return _source_error(exc)
        except Exception:
            return _error(ErrorCode.MODULE_UNAVAILABLE, "日历查询暂不可用。")

        retrieved_at = _aware_utc(self._clock())
        if snapshot.completeness is ObservationCompleteness.FAILED:
            return _error(ErrorCode.UPSTREAM_ERROR, "日历源当前不可用，查询未完成。")
        document = build_calendar_document(
            snapshot.occurrences,
            local_date=local_date,
            days=days,
            timezone_name=timezone_name,
            now=retrieved_at,
            source_variant=snapshot.source_variant or "",
            source_url=snapshot.source_url,
            interpretation_timezone=timezone_name,
            completeness=snapshot.completeness,
            warning_codes=snapshot.warning_codes,
            privacy=Privacy.PUBLIC,
        )
        warnings = calendar_warning_messages(snapshot.warning_codes)
        return CapabilityResult(
            result_id=f"ff14-calendar-{region}-{local_date.isoformat()}",
            status=ResultStatus.PARTIAL_SUCCESS
            if snapshot.completeness is ObservationCompleteness.PARTIAL
            else ResultStatus.SUCCESS,
            document=document,
            privacy=Privacy.PUBLIC,
            warnings=warnings,
        )


def normalize_region(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("region must be cn or global")
    aliases = {
        "cn": "cn",
        "global": "global",
        "国服": "cn",
        "国际服": "global",
    }
    try:
        return aliases[value.strip().lower() if value.isascii() else value.strip()]
    except KeyError:
        raise ValueError("region must be cn or global") from None


def _normalize_query(
    parameters: JsonObject,
    config: FF14ConfigSnapshot | None = None,
) -> tuple[str, int, str] | CapabilityResult:
    config = config or FF14ConfigSnapshot()
    if not isinstance(parameters, Mapping):
        return _error(ErrorCode.PARAMETER_ERROR, "请提供日历区域。")
    allowed = {"region", "days", "timezone"}
    if set(parameters) - allowed or "region" not in parameters:
        return _error(ErrorCode.PARAMETER_ERROR, "请提供有效的日历区域和查询参数。")
    try:
        region = normalize_region(parameters["region"])
    except ValueError:
        return _error(ErrorCode.PARAMETER_ERROR, "区域仅支持国服或国际服。")
    days = parameters.get("days", config.calendar_default_days)
    if type(days) is not int or not 1 <= days <= MAX_QUERY_DAYS:
        return _error(ErrorCode.PARAMETER_ERROR, "查询天数需为 1 到 30 天。")
    timezone_name = parameters.get("timezone", config.calendar_default_timezone)
    try:
        load_timezone(timezone_name)
    except ValueError:
        return _error(ErrorCode.PARAMETER_ERROR, "请提供有效的 IANA 时区。")
    return region, days, timezone_name


def _snapshot_from_attempt(
    region: str,
    spec: CalendarSourceSpec,
    raw: bytes,
    parsed: CalendarParseResult,
    window_start: datetime,
    window_end: datetime,
    warnings: tuple[str, ...],
    *,
    completeness: ObservationCompleteness | None = None,
) -> CalendarSourceSnapshot:
    status = completeness or (
        ObservationCompleteness.COMPLETE
        if parsed.completeness is Completeness.COMPLETE
        else ObservationCompleteness.PARTIAL
    )
    return CalendarSourceSnapshot(
        region=region,
        completeness=status,
        window_start=window_start,
        window_end=window_end,
        source_id=spec.source_id,
        source_variant=spec.variant,
        source_url=spec.url,
        source_version=hashlib.sha256(raw).hexdigest(),
        occurrences=parsed.occurrences,
        warning_codes=warnings,
    )


def _attempt_warnings(
    parser_warnings: tuple[object, ...], failures: list[str]
) -> tuple[str, ...]:
    warnings = list(failures)
    warnings.extend(
        f"parser:{getattr(item, 'code', 'partial')}" for item in parser_warnings
    )
    return tuple(_unique(warnings))


def _source_failure_codes(kind: str, error: SourceHttpError) -> tuple[str, ...]:
    location = "primary" if kind == "primary" else "fallback"
    generic = f"{location}_source_failed"
    if error.code == "rate_limited" or error.status_code == 429:
        return generic, "source_rate_limited"
    if error.code == "timeout":
        return generic, "source_timeout"
    return (generic,)


def _serialize_occurrence(
    occurrence: CalendarOccurrence, source_id: str
) -> dict[str, object]:
    summary = " ".join(occurrence.summary.split())[:MAX_STORED_SUMMARY]
    return {
        "summary": summary,
        "start": occurrence.start.isoformat(),
        "end": occurrence.end.isoformat(),
        "all_day": occurrence.all_day,
        "cancelled": occurrence.cancelled,
        "source_id": source_id,
    }


def _occurrence_id(source_id: str, occurrence: CalendarOccurrence) -> str:
    material = "\0".join(
        (source_id, occurrence.uid, occurrence.recurrence_identity)
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _error(code: ErrorCode, message: str) -> CapabilityResult:
    return CapabilityResult(
        result_id="ff14-calendar-error",
        status=ResultStatus.ERROR,
        privacy=Privacy.PUBLIC,
        error=ErrorDetail(code, message),
    )


def _source_error(error: SourceHttpError) -> CapabilityResult:
    del error
    return _error(ErrorCode.UPSTREAM_ERROR, "日历源当前不可用，查询未完成。")


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("calendar clock must return an aware datetime")
    return value.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


__all__ = [
    "CalendarCollector",
    "CalendarQuery",
    "CalendarSourceReader",
    "CalendarSourceSnapshot",
    "COLLECTOR_ID",
    "DATA_VERSION",
    "FALLBACK_SOURCE_ID",
    "KEY_VERSION",
    "SCHEDULE_SOURCE_ID",
]
