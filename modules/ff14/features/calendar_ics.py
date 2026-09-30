from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil import rrule
from dateutil.tz import datetime_ambiguous, datetime_exists
from icalendar import Calendar

from .calendar_models import (
    CalendarOccurrence,
    CalendarParseResult,
    CalendarWarning,
    Completeness,
)

MAX_BYTES = 1_000_000
MAX_LINE_BYTES = 16_384
MAX_LINES = 40_000
MAX_PROPERTIES_PER_EVENT = 128
MAX_EVENTS = 2_000
MAX_CANDIDATES_PER_UID = 10_000
MAX_CANDIDATES_PER_SOURCE = 40_000
MAX_OCCURRENCES = 4_000
MAX_WARNINGS = 100
MAX_WARNING_LENGTH = 240
MAX_WINDOW = timedelta(days=32)
MAX_LOCAL_TIME_GAP = timedelta(days=3)

_WARNING_TEXT = {
    "INPUT_TOO_LARGE": "Calendar source exceeds the input size limit.",
    "INVALID_UTF8": "Calendar source is not valid UTF-8.",
    "LINE_LIMIT": "Calendar source exceeds a line or line-count limit.",
    "MALFORMED_SOURCE": "Calendar source could not be parsed completely.",
    "EVENT_LIMIT": "Calendar source exceeds the event limit.",
    "PROPERTY_LIMIT": "An event exceeds the property limit.",
    "INVALID_EVENT": "An event has unsupported or invalid fields.",
    "INVALID_TIMEZONE": "An event uses an unknown or unsupported timezone.",
    "FLOATING_TIME_UNSUPPORTED": "A timed event has no timezone or UTC marker.",
    "UNSUPPORTED_RECURRENCE": "An event uses unsupported recurrence data.",
    "RECURRENCE_BUDGET": "An event exceeded the recurrence expansion work budget.",
    "SOURCE_BUDGET": "The calendar source exceeded the recurrence expansion work budget.",
    "OUTPUT_LIMIT": "Calendar source exceeds the output occurrence limit.",
    "CONFLICTING_REVISION": "An event has conflicting revisions.",
    "ORPHAN_OVERRIDE": "An event override has no matching recurrence instance.",
}


class _SeriesProblem(Exception):
    def __init__(self, code: str, *, used: int = 0):
        self.code = code
        self.used = used


@dataclass(frozen=True, slots=True)
class _Event:
    component: Any
    uid: str
    recurrence_id: date | datetime | None
    start: date | datetime | None
    end: date | datetime | None
    summary: str
    cancelled: bool
    sequence: int
    stamp: datetime | None


def parse_ics(
    raw: bytes,
    *,
    window_start: datetime,
    window_end: datetime,
    display_timezone: str,
    source_id: str,
    reject_floating: bool = False,
) -> CalendarParseResult:
    """Parse a bounded ICS snapshot without network, storage, or clock access."""
    if not isinstance(raw, bytes):
        raise ValueError("raw must be bytes")
    if window_start.tzinfo is None or window_start.utcoffset() is None:
        raise ValueError("window_start must be aware")
    if window_end.tzinfo is None or window_end.utcoffset() is None:
        raise ValueError("window_end must be aware")
    if window_start >= window_end or window_end - window_start > MAX_WINDOW:
        raise ValueError("window must be positive and no longer than 32 days")
    if type(reject_floating) is not bool:
        raise ValueError("reject_floating must be bool")
    try:
        display_tz = ZoneInfo(display_timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("display_timezone must be a valid IANA timezone") from exc

    warnings: list[CalendarWarning] = []
    if len(raw) > MAX_BYTES:
        return _partial(source_id, warnings, "INPUT_TOO_LARGE")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return _partial(source_id, warnings, "INVALID_UTF8")
    physical_lines = raw.split(b"\n")
    if len(physical_lines) > MAX_LINES or any(
        len(line[:-1] if line.endswith(b"\r") else line) > MAX_LINE_BYTES
        for line in physical_lines
    ):
        return _partial(source_id, warnings, "LINE_LIMIT")

    try:
        calendar = Calendar.from_ical(text)
        if (
            getattr(calendar, "name", None) != "VCALENDAR"
            or str(calendar.get("VERSION", "")) != "2.0"
        ):
            return _partial(source_id, warnings, "MALFORMED_SOURCE")
        events = calendar.walk("VEVENT")
    except Exception:
        return _partial(source_id, warnings, "MALFORMED_SOURCE")
    if getattr(calendar, "errors", ()):
        _add_warning(warnings, "MALFORMED_SOURCE")
    if len(events) > MAX_EVENTS:
        return _partial(source_id, warnings, "EVENT_LIMIT")

    groups: dict[str, list[_Event]] = {}
    invalid_uids: set[str] = set()
    for component in events:
        try:
            item = _read_event(component, display_tz, reject_floating=reject_floating)
            if sum(1 for _ in component.property_items()) > MAX_PROPERTIES_PER_EVENT:
                raise _SeriesProblem("PROPERTY_LIMIT")
            groups.setdefault(item.uid, []).append(item)
        except _SeriesProblem as problem:
            uid = _safe_uid(component)
            if uid is None:
                _add_warning(warnings, problem.code)
            else:
                invalid_uids.add(uid)
                _add_warning(warnings, problem.code, uid)

    output: list[CalendarOccurrence] = []
    candidates_source = 0
    for uid, revisions in groups.items():
        if uid in invalid_uids:
            continue
        try:
            masters, overrides = _select_revisions(revisions)
            if len(masters) != 1:
                raise _SeriesProblem("CONFLICTING_REVISION")
            master = masters[0]
            if master.start is None:
                raise _SeriesProblem("INVALID_EVENT")
            series, used = _expand_series(
                master,
                overrides,
                display_tz,
                window_start,
                window_end,
                MAX_CANDIDATES_PER_UID,
                MAX_CANDIDATES_PER_SOURCE - candidates_source,
                source_id,
                reject_floating=reject_floating,
            )
            candidates_source += used
            if candidates_source > MAX_CANDIDATES_PER_SOURCE:
                raise _SeriesProblem("SOURCE_BUDGET")
            if len(output) + len(series) > MAX_OCCURRENCES:
                _add_warning(warnings, "OUTPUT_LIMIT")
                break
            output.extend(series)
        except _SeriesProblem as problem:
            candidates_source += problem.used
            _add_warning(warnings, problem.code, uid)
            if (
                candidates_source >= MAX_CANDIDATES_PER_SOURCE
                or problem.code == "SOURCE_BUDGET"
            ):
                break

    if invalid_uids or warnings:
        completeness = Completeness.PARTIAL
    else:
        completeness = Completeness.COMPLETE
    output.sort(
        key=lambda item: (_sort_time(item.start), item.uid, item.recurrence_identity)
    )
    return CalendarParseResult(
        source_id=source_id,
        completeness=completeness,
        occurrences=tuple(output),
        warnings=tuple(warnings),
    )


def _partial(
    source_id: str,
    warnings: list[CalendarWarning],
    code: str,
    uid: str | None = None,
) -> CalendarParseResult:
    _add_warning(warnings, code, uid)
    return CalendarParseResult(
        source_id, Completeness.PARTIAL, (), tuple(warnings[:MAX_WARNINGS])
    )


def _warning(code: str, uid: str | None = None) -> CalendarWarning:
    message = _WARNING_TEXT.get(code, "Calendar source contains unsupported data.")[
        :MAX_WARNING_LENGTH
    ]
    key = hashlib.sha256(uid.encode("utf-8")).hexdigest()[:12] if uid else None
    return CalendarWarning(code, message, key)


def _add_warning(
    warnings: list[CalendarWarning], code: str, uid: str | None = None
) -> None:
    if len(warnings) < MAX_WARNINGS:
        warnings.append(_warning(code, uid))


def _safe_uid(component: Any) -> str | None:
    try:
        value = component.decoded("UID")
    except Exception:
        return None
    return value if isinstance(value, str) and value else None


def _read_event(
    component: Any, display_tz: ZoneInfo, *, reject_floating: bool
) -> _Event:
    if any(name in component for name in ("DURATION", "EXRULE")):
        raise _SeriesProblem("INVALID_EVENT")
    if any(
        len(_properties(component, name)) > 1
        for name in (
            "UID",
            "DTSTART",
            "DTEND",
            "STATUS",
            "SEQUENCE",
            "DTSTAMP",
            "RECURRENCE-ID",
            "RRULE",
        )
    ):
        raise _SeriesProblem("INVALID_EVENT")
    uid = _safe_uid(component)
    if uid is None:
        raise _SeriesProblem("INVALID_EVENT")
    try:
        sequence = int(component.decoded("SEQUENCE", default=0))
        if sequence < 0:
            raise ValueError
        stamp = component.decoded("DTSTAMP") if "DTSTAMP" in component else None
        if stamp is not None and (
            not isinstance(stamp, datetime) or stamp.tzinfo is None
        ):
            raise ValueError
        start = (
            _temporal(
                component.get("DTSTART"),
                display_tz,
                reject_floating=reject_floating,
            )
            if "DTSTART" in component
            else None
        )
        end = (
            _temporal(
                component.get("DTEND"),
                display_tz,
                reject_floating=reject_floating,
            )
            if "DTEND" in component
            else None
        )
        recurrence_id = (
            _temporal(
                component.get("RECURRENCE-ID"),
                display_tz,
                reject_floating=reject_floating,
            )
            if "RECURRENCE-ID" in component
            else None
        )
        if start is not None and end is not None:
            _validate_pair(start, end)
        if start is not None and end is None:
            end = (
                start + timedelta(days=1)
                if isinstance(start, date) and not isinstance(start, datetime)
                else start
            )
        summary = str(component.decoded("SUMMARY", default=""))
        status = str(component.decoded("STATUS", default="")).upper()
        if status not in ("", "CONFIRMED", "TENTATIVE", "CANCELLED"):
            raise ValueError
        if "RECURRENCE-ID" in component:
            rid_params = component.get("RECURRENCE-ID").params
            if str(rid_params.get("RANGE", "")).upper() == "THISANDFUTURE":
                raise _SeriesProblem("UNSUPPORTED_RECURRENCE")
            if str(rid_params.get("RANGE", "")):
                raise ValueError
    except _SeriesProblem:
        raise
    except (KeyError, TypeError, ValueError, OverflowError):
        raise _SeriesProblem("INVALID_EVENT") from None
    return _Event(
        component,
        uid,
        recurrence_id,
        start,
        end,
        summary,
        status == "CANCELLED",
        sequence,
        stamp,
    )


def _temporal(
    property_value: Any, display_tz: ZoneInfo, *, reject_floating: bool
) -> date | datetime:
    if property_value is None:
        raise ValueError("missing temporal value")
    value = property_value.dt
    tzid = property_value.params.get("TZID")
    if isinstance(value, datetime):
        if tzid:
            try:
                zone = ZoneInfo(str(tzid))
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise _SeriesProblem("INVALID_TIMEZONE") from exc
            value = value.replace(tzinfo=None).replace(tzinfo=zone)
            return _resolve_local(value)
        if value.tzinfo is None:
            if reject_floating:
                raise _SeriesProblem("FLOATING_TIME_UNSUPPORTED")
            return _resolve_local(value.replace(tzinfo=display_tz))
        if value.utcoffset() != timedelta(0):
            raise _SeriesProblem("INVALID_TIMEZONE")
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        if tzid:
            raise _SeriesProblem("INVALID_TIMEZONE")
        return value
    raise _SeriesProblem("INVALID_EVENT")


def _resolve_local(value: datetime) -> datetime:
    if not datetime_exists(value):
        lower = 0
        upper = 1
        limit = int(MAX_LOCAL_TIME_GAP.total_seconds())
        while upper <= limit and not datetime_exists(value + timedelta(seconds=upper)):
            lower = upper
            upper *= 2
        if upper > limit:
            upper = limit
            if not datetime_exists(value + timedelta(seconds=upper)):
                raise _SeriesProblem("INVALID_TIMEZONE")
        while upper - lower > 1:
            middle = (lower + upper) // 2
            if datetime_exists(value + timedelta(seconds=middle)):
                upper = middle
            else:
                lower = middle
        value += timedelta(seconds=upper)
    if datetime_ambiguous(value):
        value = value.replace(fold=0)
    return value


def _validate_pair(start: date | datetime, end: date | datetime) -> None:
    if isinstance(start, datetime) != isinstance(end, datetime):
        raise _SeriesProblem("INVALID_EVENT")
    if isinstance(start, datetime):
        if (
            start.tzinfo is None
            or end.tzinfo is None
            or _duration(start, end) < timedelta(0)
        ):
            raise _SeriesProblem("INVALID_EVENT")
    elif end <= start:
        raise _SeriesProblem("INVALID_EVENT")


def _is_date_value(value: date | datetime) -> bool:
    return isinstance(value, date) and not isinstance(value, datetime)


def _validate_same_temporal_kind(
    start: date | datetime, value: date | datetime
) -> None:
    if isinstance(start, datetime) != isinstance(value, datetime):
        raise _SeriesProblem("UNSUPPORTED_RECURRENCE")
    if isinstance(start, datetime) and isinstance(value, datetime):
        if _tz_identity(start) != _tz_identity(value):
            raise _SeriesProblem("UNSUPPORTED_RECURRENCE")


def _tz_identity(value: datetime) -> str:
    tzinfo = value.tzinfo
    if tzinfo is None:
        return "floating"
    return getattr(
        tzinfo, "key", "UTC" if value.utcoffset() == timedelta(0) else str(tzinfo)
    )


def _select_revisions(events: list[_Event]) -> tuple[list[_Event], list[_Event]]:
    split: dict[str | None, list[_Event]] = {}
    for event in events:
        key = (
            _identity(event.recurrence_id) if event.recurrence_id is not None else None
        )
        split.setdefault(key, []).append(event)
    chosen: list[_Event] = []
    for candidates in split.values():
        candidates.sort(
            key=lambda item: (
                item.sequence,
                item.stamp or datetime.min.replace(tzinfo=timezone.utc),
            ),
            reverse=True,
        )
        top = candidates[0]
        ties = [
            item
            for item in candidates
            if (item.sequence, item.stamp) == (top.sequence, top.stamp)
        ]
        if len(ties) > 1 and any(
            item.component.to_ical() != top.component.to_ical() for item in ties[1:]
        ):
            raise _SeriesProblem("CONFLICTING_REVISION")
        chosen.append(top)
    masters = [item for item in chosen if item.recurrence_id is None]
    overrides = [item for item in chosen if item.recurrence_id is not None]
    return masters, overrides


def _expand_series(
    master: _Event,
    overrides: list[_Event],
    display_tz: ZoneInfo,
    window_start: datetime,
    window_end: datetime,
    uid_budget: int,
    source_budget: int,
    source_id: str,
    *,
    reject_floating: bool,
) -> tuple[list[CalendarOccurrence], int]:
    component = master.component
    start = master.start
    assert start is not None
    end = master.end
    assert end is not None
    duration = _duration(start, end)
    if any(
        name in component for name in ("RRULE", "RDATE", "EXDATE")
    ) and not isinstance(start, datetime):
        recurrence_start: datetime = datetime.combine(start, time.min)
    elif isinstance(start, datetime):
        recurrence_start = start
    else:
        recurrence_start = datetime.combine(start, time.min, tzinfo=display_tz)

    exdates: set[str] = set()
    rdates: list[date | datetime] = []
    exdate_count = 0
    try:
        if len(_properties(component, "RRULE")) > 1:
            raise _SeriesProblem("UNSUPPORTED_RECURRENCE")
        for prop in _properties(component, "EXDATE"):
            for value in prop.dts:
                exdate_count += 1
                candidate = _temporal(
                    value, display_tz, reject_floating=reject_floating
                )
                _validate_same_temporal_kind(start, candidate)
                exdates.add(_identity(candidate))
        for prop in _properties(component, "RDATE"):
            if str(prop.params.get("VALUE", "")).upper() == "PERIOD":
                raise _SeriesProblem("UNSUPPORTED_RECURRENCE")
            for value in prop.dts:
                candidate = _temporal(
                    value, display_tz, reject_floating=reject_floating
                )
                _validate_same_temporal_kind(start, candidate)
                rdates.append(candidate)
    except (AttributeError, TypeError, ValueError):
        raise _SeriesProblem("UNSUPPORTED_RECURRENCE") from None

    raw_candidates: Iterable[date | datetime]
    rule = component.get("RRULE")
    if rule is None:
        raw_candidates = [start, *rdates]
        used = len(raw_candidates) + exdate_count
    else:
        values = _validate_rule(rule, start)
        target = window_end.astimezone(timezone.utc)
        for item in overrides:
            rid = item.recurrence_id
            if rid is not None:
                _validate_same_temporal_kind(start, rid)
                target = max(target, _instant(rid, display_tz))
        recurrence, target, used = _bounded_recurrence(
            rule,
            values,
            start,
            recurrence_start,
            target,
            display_tz,
            len(rdates),
            exdate_count,
            uid_budget,
            source_budget,
        )
        candidates: list[date | datetime] = []
        emitted = 0
        try:
            for value in recurrence:
                emitted += 1
                if emitted + len(rdates) > min(uid_budget, source_budget):
                    code = (
                        "SOURCE_BUDGET"
                        if emitted + len(rdates) > source_budget
                        else "RECURRENCE_BUDGET"
                    )
                    raise _SeriesProblem(code, used=max(used, emitted + len(rdates)))
                normalized = (
                    _resolve_local(value)
                    if value.tzinfo is not None
                    else value.replace(tzinfo=display_tz)
                )
                if isinstance(start, date) and not isinstance(start, datetime):
                    candidate: date | datetime = normalized.date()
                else:
                    candidate = normalized
                candidates.append(candidate)
                if normalized.astimezone(timezone.utc) > target:
                    break
        except _SeriesProblem as problem:
            if problem.used:
                raise
            raise _SeriesProblem(problem.code, used=used) from None
        except Exception:
            raise _SeriesProblem("UNSUPPORTED_RECURRENCE") from None
        candidates.extend(rdates)
        raw_candidates = candidates
        used = max(used, emitted + len(rdates))
    if used > uid_budget:
        raise _SeriesProblem("RECURRENCE_BUDGET", used=used)
    if used > source_budget:
        raise _SeriesProblem("SOURCE_BUDGET", used=used)

    unique: dict[str, date | datetime] = {}
    for candidate in raw_candidates:
        identity = _identity(candidate)
        if identity not in exdates:
            unique[identity] = candidate
    override_map = {
        _identity(item.recurrence_id): item
        for item in overrides
        if item.recurrence_id is not None
    }
    occurrences: list[CalendarOccurrence] = []
    for identity, candidate in unique.items():
        override = override_map.pop(identity, None)
        if override is not None:
            if (
                override.start is not None
                and _is_date_value(override.start) != _is_date_value(start)
            ) or (
                override.end is not None
                and _is_date_value(override.end) != _is_date_value(start)
            ):
                raise _SeriesProblem("INVALID_EVENT", used=used)
            occurrence_start = (
                override.start if override.start is not None else candidate
            )
            try:
                occurrence_end = (
                    override.end
                    if override.end is not None
                    else _add_duration(occurrence_start, duration)
                )
            except (OverflowError, ValueError):
                raise _SeriesProblem("INVALID_EVENT", used=used) from None
            cancelled = override.cancelled
            summary = override.summary or master.summary
        else:
            occurrence_start = candidate
            try:
                occurrence_end = _add_duration(candidate, duration)
            except (OverflowError, ValueError):
                raise _SeriesProblem("INVALID_EVENT", used=used) from None
            cancelled = master.cancelled
            summary = master.summary
        assert occurrence_end is not None
        try:
            _validate_pair(occurrence_start, occurrence_end)
        except _SeriesProblem as problem:
            raise _SeriesProblem(problem.code, used=used) from None
        if _intersects(
            occurrence_start, occurrence_end, window_start, window_end, display_tz
        ):
            occurrences.append(
                CalendarOccurrence(
                    master.uid,
                    identity,
                    summary,
                    _display_value(occurrence_start, display_tz),
                    _display_value(occurrence_end, display_tz),
                    isinstance(occurrence_start, date)
                    and not isinstance(occurrence_start, datetime),
                    cancelled,
                    source_id,
                )
            )
    if override_map:
        raise _SeriesProblem("ORPHAN_OVERRIDE", used=used)
    return occurrences, used


def _validate_rule(rule: Any, start: date | datetime) -> dict[str, list[Any]]:
    allowed = {"FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY", "BYMONTHDAY", "WKST"}
    values = {str(key).upper(): list(value) for key, value in rule.items()}
    if not values or set(values) - allowed:
        raise _SeriesProblem("UNSUPPORTED_RECURRENCE")
    freq = values.get("FREQ", [None])
    if len(freq) != 1 or str(freq[0]).upper() not in {"DAILY", "WEEKLY", "MONTHLY"}:
        raise _SeriesProblem("UNSUPPORTED_RECURRENCE")
    if "COUNT" in values and "UNTIL" in values:
        raise _SeriesProblem("UNSUPPORTED_RECURRENCE")
    if any(
        len(values.get(k, [])) != 1
        for k in ("INTERVAL", "COUNT", "UNTIL", "WKST")
        if k in values
    ):
        raise _SeriesProblem("UNSUPPORTED_RECURRENCE")
    try:
        if (
            int(values.get("INTERVAL", [1])[0]) < 1
            or int(values.get("INTERVAL", [1])[0]) > 366
        ):
            raise ValueError
        if int(values.get("COUNT", [1])[0]) < 1:
            raise ValueError
    except (TypeError, ValueError):
        raise _SeriesProblem("UNSUPPORTED_RECURRENCE") from None
    if str(values.get("WKST", ["MO"])[0]).upper() != "MO":
        raise _SeriesProblem("UNSUPPORTED_RECURRENCE")
    if any(
        str(day).upper() not in {"MO", "TU", "WE", "TH", "FR", "SA", "SU"}
        for day in values.get("BYDAY", [])
    ):
        raise _SeriesProblem("UNSUPPORTED_RECURRENCE")
    try:
        month_days = [int(day) for day in values.get("BYMONTHDAY", [])]
    except (TypeError, ValueError):
        raise _SeriesProblem("UNSUPPORTED_RECURRENCE") from None
    if any(day < 1 or day > 31 for day in month_days):
        raise _SeriesProblem("UNSUPPORTED_RECURRENCE")
    until = values.get("UNTIL", [None])[0]
    if until is not None:
        if isinstance(start, datetime):
            if (
                not isinstance(until, datetime)
                or until.tzinfo is None
                or until.utcoffset() != timedelta(0)
            ):
                raise _SeriesProblem("UNSUPPORTED_RECURRENCE")
        elif isinstance(until, datetime) or not isinstance(until, date):
            raise _SeriesProblem("UNSUPPORTED_RECURRENCE")
    return values


def _bounded_recurrence(
    rule: Any,
    values: dict[str, list[Any]],
    start: date | datetime,
    recurrence_start: datetime,
    target: datetime,
    display_tz: ZoneInfo,
    rdate_count: int,
    exdate_count: int,
    uid_budget: int,
    source_budget: int,
) -> tuple[Any, datetime, int]:
    all_day = not isinstance(start, datetime)
    if all_day:
        target_day = target.astimezone(display_tz).date()
        until = values.get("UNTIL", [None])[0]
        if until is not None:
            target_day = min(target_day, until)
        target = datetime.combine(target_day, time.max, tzinfo=display_tz).astimezone(
            timezone.utc
        )
        limit_text = target_day.strftime("%Y%m%d")
        start_day = start
        span_days = max(1, (target_day - start_day).days + 1)
    else:
        assert isinstance(start, datetime)
        until = values.get("UNTIL", [None])[0]
        if until is not None:
            target = min(target, until.astimezone(timezone.utc))
        start_day = start.date()
        target_day = target.astimezone(start.tzinfo).date()
        span_days = max(1, (target_day - start_day).days + 1)
        limit = target.replace(microsecond=0)
        if target.microsecond:
            limit += timedelta(seconds=1)
        limit_text = limit.strftime("%Y%m%dT%H%M%SZ")

    work = span_days + rdate_count + exdate_count
    if work > uid_budget:
        raise _SeriesProblem("RECURRENCE_BUDGET", used=work)
    if work > source_budget:
        raise _SeriesProblem("SOURCE_BUDGET", used=work)
    rule_text = rule.to_ical().decode("ascii")
    if "COUNT" not in values:
        parts = [
            part
            for part in rule_text.split(";")
            if not part.upper().startswith("UNTIL=")
        ]
        rule_text = ";".join((*parts, f"UNTIL={limit_text}"))
    try:
        recurrence = rrule.rrulestr(rule_text, dtstart=recurrence_start)
    except Exception:
        raise _SeriesProblem("UNSUPPORTED_RECURRENCE") from None
    return recurrence, target, work


def _properties(component: Any, name: str) -> list[Any]:
    return [
        value for key, value in component.property_items() if str(key).upper() == name
    ]


def _add_duration(value: date | datetime, duration: timedelta | Any) -> date | datetime:
    if isinstance(value, datetime):
        return (value.astimezone(timezone.utc) + duration).astimezone(value.tzinfo)
    if isinstance(duration, timedelta):
        return value + duration
    return value


def _duration(start: date | datetime, end: date | datetime) -> timedelta:
    if isinstance(start, datetime) and isinstance(end, datetime):
        return end.astimezone(timezone.utc) - start.astimezone(timezone.utc)
    return end - start


def _identity(value: date | datetime | None) -> str:
    if value is None:
        return ""
    return value.isoformat()


def _display_value(value: date | datetime, display_tz: ZoneInfo) -> date | datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return _resolve_local(value.replace(tzinfo=display_tz))
        return value.astimezone(display_tz)
    return value


def _instant(value: date | datetime, display_tz: ZoneInfo) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    return datetime.combine(value, time.min, tzinfo=display_tz).astimezone(timezone.utc)


def _intersects(
    start: date | datetime,
    end: date | datetime,
    window_start: datetime,
    window_end: datetime,
    display_tz: ZoneInfo,
) -> bool:
    start_instant = _instant(start, display_tz)
    end_instant = _instant(end, display_tz)
    lower = window_start.astimezone(timezone.utc)
    upper = window_end.astimezone(timezone.utc)
    if start_instant == end_instant:
        return lower <= start_instant < upper
    return start_instant < upper and end_instant > lower


def _sort_time(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    return datetime.combine(value, time.min, tzinfo=timezone.utc)
