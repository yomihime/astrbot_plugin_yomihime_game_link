"""Pure calendar query filtering and daily subscription evaluation."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from types import MappingProxyType
from typing import Mapping, Sequence
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.tz import datetime_exists, resolve_imaginary

from yomihime_game_link_sdk.display import (
    DisplayDocument,
    Link,
    LinksBlock,
    Privacy,
    TableBlock,
    TextBlock,
    TimeValue,
)
from yomihime_game_link_sdk.storage import OwnershipKind
from yomihime_game_link_sdk.subscriptions import (
    EvaluationDecision,
    EvaluationState,
    Observation,
    ObservationCompleteness,
    SubscriptionView,
)

from .calendar_models import CalendarOccurrence

SUMMARY_TYPE_ID = "ff14.calendar.daily_summary"
FILTER_KIND = "daily_summary"
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_LOCAL_TIME = "08:00"
DAILY_WINDOW_DAYS = 7
MAX_REMEMBERED_DATES = 32
MAX_DISPLAY_EVENTS = 15
MAX_SUMMARY_LENGTH = 160
MAX_PAYLOAD_OCCURRENCES = 2_000
_LOCAL_TIME = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z")
_SOURCE_LABELS = {
    "cn_google": "国服主日历（Google Calendar）",
    "cn_icloud": "国服备用日历（iCloud）",
    "global_google": "国际服主日历（Google Calendar）",
    "global_icloud": "国际服备用日历（iCloud）",
}
_SOURCE_URLS = MappingProxyType(
    {
        (
            "cn",
            "ff14_calendar_primary",
        ): "https://calendar.google.com/calendar/ical/up88drvlnnh2t77hbpqq8v33i2cngfh7%40import.calendar.google.com/public/basic.ics",
        (
            "cn",
            "ff14_calendar_fallback",
        ): "https://p66-caldav.icloud.com/published/2/MTAyMTk3MTMxMjExMDIxOXsjasy7WUO0EcKVz7qGEuVjjTlRkgd6WOZM171uxP_u-QM51M24lHzRlAQir-oodDRRTzZeusSLbw0snkZoqI4",
        (
            "global",
            "ff14_calendar_primary",
        ): "https://calendar.google.com/calendar/ical/1gpnler51bgs1ajti10ao946ou367bf6%40import.calendar.google.com/public/basic.ics",
        (
            "global",
            "ff14_calendar_fallback",
        ): "https://p66-caldav.icloud.com/published/2/MTAyMTk3MTMxMjExMDIxOXsjasy7WUO0EcKVz7qGEuVzSK8L9ZRQYf1sxUFeH1A1a22GJLf6nfk2-CZNYMv5iOxCNlUR-umbJKFWWAUVRp8",
    }
)
_SOURCE_VARIANTS = MappingProxyType(
    {
        ("cn", "ff14_calendar_primary"): "cn_google",
        ("cn", "ff14_calendar_fallback"): "cn_icloud",
        ("global", "ff14_calendar_primary"): "global_google",
        ("global", "ff14_calendar_fallback"): "global_icloud",
    }
)
_WARNING_TEXT = {
    "primary_source_failed": "主日历源暂不可用，已尝试备用源。",
    "fallback_source_failed": "备用日历源暂不可用。",
    "primary_source_partial": "主日历源未能完整解析，已尝试备用源。",
    "fallback_source_partial": "备用日历源内容不完整。",
    "primary_source_unqualified": "主日历源未获资格，未启用。",
    "fallback_source_unqualified": "备用日历源未获资格，未启用。",
    "source_rate_limited": "日历源请求频率受限。",
    "source_timeout": "日历源请求超时。",
    "source_unavailable": "日历源暂不可用。",
    "source_partial": "来源未完整解析，自动摘要暂不可用。",
    "stored_occurrence_limit": "日历内容超过自动摘要存储上限。",
}
_PARSER_WARNING_CODES = frozenset(
    {
        "INPUT_TOO_LARGE",
        "INVALID_UTF8",
        "LINE_LIMIT",
        "MALFORMED_SOURCE",
        "EVENT_LIMIT",
        "PROPERTY_LIMIT",
        "INVALID_EVENT",
        "INVALID_TIMEZONE",
        "FLOATING_TIME_UNSUPPORTED",
        "UNSUPPORTED_RECURRENCE",
        "RECURRENCE_BUDGET",
        "SOURCE_BUDGET",
        "OUTPUT_LIMIT",
        "CONFLICTING_REVISION",
        "ORPHAN_OVERRIDE",
    }
)


@dataclass(frozen=True, slots=True)
class CalendarSourceSpec:
    source_id: str
    variant: str
    url: str
    path: str


def calendar_source_spec(region: str, source_id: str) -> CalendarSourceSpec | None:
    """Reviewed identity/region/format only; not freshness or all-event coverage."""
    if not isinstance(region, str) or not isinstance(source_id, str):
        return None
    key = (region, source_id)
    variant, url = _SOURCE_VARIANTS.get(key), _SOURCE_URLS.get(key)
    if variant is None or url is None:
        return None
    return CalendarSourceSpec(source_id, variant, url, unquote(urlsplit(url).path))


def calendar_warning_messages(codes: object) -> tuple[str, ...]:
    """Persisted warning values never become raw user-visible text."""
    if not isinstance(codes, (tuple, list)):
        return ()
    messages = []
    for code in codes[:100]:
        if not isinstance(code, str):
            continue
        message = _WARNING_TEXT.get(code)
        if code.startswith("parser:") and code[7:] in _PARSER_WARNING_CODES:
            message = "日历部分内容不支持自动处理。"
        if message is not None and message not in messages:
            messages.append(message)
    return tuple(messages)


@dataclass(frozen=True, slots=True)
class DisplayOccurrence:
    summary: str
    start: date | datetime
    end: date | datetime
    all_day: bool
    source_id: str


def parse_local_time(value: object) -> time:
    if not isinstance(value, str) or not _LOCAL_TIME.fullmatch(value):
        raise ValueError("time must use HH:MM")
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def load_timezone(value: object) -> ZoneInfo:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError("timezone must be an IANA name")
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("timezone must be an IANA name") from None


def filter_occurrences(
    occurrences: Sequence[CalendarOccurrence | Mapping[str, object]],
    *,
    local_date: date,
    days: int,
    zone: ZoneInfo,
    now: datetime,
) -> tuple[DisplayOccurrence, ...]:
    """Select active/upcoming events intersecting a half-open local-date window."""
    if type(days) is not int or not 1 <= days <= 30:
        raise ValueError("days must be between 1 and 30")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    local_end_date = local_date + timedelta(days=days)
    start_at = _local_midnight(local_date, zone).astimezone(UTC)
    end_at = _local_midnight(local_end_date, zone).astimezone(UTC)
    now_utc = now.astimezone(UTC)

    selected: list[DisplayOccurrence] = []
    for raw in occurrences:
        try:
            occurrence = _as_display_occurrence(raw)
        except (TypeError, ValueError, OverflowError):
            continue
        if _is_cancelled(raw):
            continue
        if occurrence.all_day:
            start_day = occurrence.start
            end_day = occurrence.end
            assert isinstance(start_day, date) and not isinstance(start_day, datetime)
            assert isinstance(end_day, date) and not isinstance(end_day, datetime)
            intersects = start_day < local_end_date and end_day > local_date
            active_or_upcoming = end_day > local_date
        else:
            start_value = occurrence.start
            end_value = occurrence.end
            assert isinstance(start_value, datetime) and isinstance(end_value, datetime)
            start_instant = start_value.astimezone(UTC)
            end_instant = end_value.astimezone(UTC)
            if start_instant == end_instant:
                intersects = start_at <= start_instant < end_at
                active_or_upcoming = start_instant >= now_utc
            else:
                intersects = start_instant < end_at and end_instant > start_at
                active_or_upcoming = end_instant > now_utc
        if intersects and active_or_upcoming:
            selected.append(occurrence)

    selected.sort(key=lambda item: _sort_key(item, zone))
    return tuple(selected)


def build_calendar_document(
    occurrences: Sequence[CalendarOccurrence | Mapping[str, object]],
    *,
    local_date: date,
    days: int,
    timezone_name: str,
    now: datetime,
    source_variant: str,
    source_url: str | None = None,
    interpretation_timezone: str | None = None,
    completeness: ObservationCompleteness = ObservationCompleteness.COMPLETE,
    warning_codes: object = (),
    privacy: Privacy,
) -> DisplayDocument:
    zone = load_timezone(timezone_name)
    selected = filter_occurrences(
        occurrences,
        local_date=local_date,
        days=days,
        zone=zone,
        now=now,
    )
    if source_variant not in _SOURCE_LABELS:
        raise ValueError("calendar source is not qualified")
    if completeness not in (
        ObservationCompleteness.COMPLETE,
        ObservationCompleteness.PARTIAL,
    ):
        raise ValueError("calendar document needs a parsed source")
    source_label = _SOURCE_LABELS[source_variant]
    local_now = now.astimezone(zone)
    blocks: list[TextBlock | TableBlock] = [
        TextBlock(
            f"范围：{local_date.isoformat()} 起 {days} 个本地日历日（{timezone_name}）"
        ),
        TextBlock(f"数据源：{source_label}；获取时间：{local_now:%Y-%m-%d %H:%M %Z}。"),
    ]
    blocks.append(
        TextBlock("来源更新时间未知；仅汇总所选公开日历来源，不保证覆盖全部活动。")
    )
    if source_variant in {"cn_icloud", "global_icloud"}:
        blocks.append(TextBlock("已使用合格备用日历来源。"))
    if completeness is ObservationCompleteness.PARTIAL:
        blocks.append(TextBlock("来源未完整解析，自动摘要暂不可用。"))
    blocks.extend(
        TextBlock(message)
        for message in calendar_warning_messages(warning_codes)
        if message != "来源未完整解析，自动摘要暂不可用。"
        or completeness is not ObservationCompleteness.PARTIAL
    )
    if not selected:
        blocks.append(
            TextBlock(
                "来源未完整解析，无法确认当前窗口是否无活动。"
                if completeness is ObservationCompleteness.PARTIAL
                else "该公开来源在当前窗口未返回活动。"
            )
        )
    else:
        visible = selected[:MAX_DISPLAY_EVENTS]
        rows = tuple(
            (
                _bounded_summary(item.summary),
                _format_start(item, zone),
                _format_end(item, zone),
            )
            for item in visible
        )
        blocks.append(TableBlock(("活动", "开始", "结束"), rows))
        if len(selected) > len(visible):
            blocks.append(
                TextBlock(f"另有 {len(selected) - len(visible)} 项活动未显示。")
            )
    if source_url is not None:
        blocks.append(
            LinksBlock(
                (Link("查看公开日历源", source_url),),
                fallback_text="可查看对应公开日历源。",
            )
        )
    if interpretation_timezone is not None:
        blocks.append(
            TextBlock(
                f"无时区标记的时间按 {interpretation_timezone} 解释；这不代表发布者时区。"
            )
        )
    return DisplayDocument(
        title="FF14 活动日历摘要" if days == DAILY_WINDOW_DAYS else "FF14 活动日历",
        subject=f"{local_date.isoformat()} 起 {days} 个本地日历日",
        ordered_blocks=tuple(blocks),
        sources=(source_label,),
        timestamps=(TimeValue(local_now, timezone_name),),
        privacy=privacy,
    )


class CalendarDailySummaryEvaluator:
    """Evaluate one seven-local-day summary without clock or storage access."""

    def evaluate(
        self,
        subscription: SubscriptionView,
        observation: Observation,
        previous_state: EvaluationState | None,
    ) -> EvaluationDecision:
        prior = _state_object(previous_state)
        if observation.completeness is not ObservationCompleteness.COMPLETE:
            return EvaluationDecision(_preserved_state(prior), False)

        try:
            settings = _subscription_settings(subscription)
            _validate_collection_key(observation, settings["region"])
            snapshot = _complete_payload(observation, settings["region"])
            zone = load_timezone(settings["timezone"])
            due_time = parse_local_time(settings["time"])
        except (TypeError, ValueError, KeyError):
            return EvaluationDecision(_preserved_state(prior), False)

        collected_at = observation.collected_at
        local_now = collected_at.astimezone(zone)
        today = local_now.date()
        schedule_fingerprint = _schedule_fingerprint(
            settings["region"], settings["timezone"], settings["time"]
        )
        previous_revision = prior.get("subscription_revision")
        same_schedule = (
            previous_revision == subscription.revision
            and prior.get("schedule_fingerprint") == schedule_fingerprint
        )
        due_at = _local_due_instant(today, due_time, zone)
        is_due = collected_at.astimezone(UTC) >= due_at
        last_event_date = _parse_date(prior.get("last_event_local_date"))
        emitted_dates = _parse_emitted_dates(prior.get("emitted_local_dates"))
        if last_event_date is None and emitted_dates:
            last_event_date = emitted_dates[-1]

        if not same_schedule:
            baseline = today if not is_due else today + timedelta(days=1)
            state = _evaluation_state(
                subscription.revision,
                schedule_fingerprint,
                baseline,
                last_event_date,
                emitted_dates,
            )
            return EvaluationDecision(state, False)

        baseline = _parse_date(prior.get("baseline_local_date"))
        if baseline is None:
            baseline = today if not is_due else today + timedelta(days=1)
        if today < baseline or not is_due:
            return EvaluationDecision(
                _evaluation_state(
                    subscription.revision,
                    schedule_fingerprint,
                    baseline,
                    last_event_date,
                    emitted_dates,
                ),
                False,
            )
        if last_event_date == today:
            return EvaluationDecision(
                _evaluation_state(
                    subscription.revision,
                    schedule_fingerprint,
                    baseline,
                    last_event_date,
                    emitted_dates,
                ),
                False,
            )

        window_start = _local_midnight(today, zone).astimezone(UTC)
        window_end = _local_midnight(
            today + timedelta(days=DAILY_WINDOW_DAYS), zone
        ).astimezone(UTC)
        if not _covers(snapshot, window_start, window_end):
            return EvaluationDecision(
                _evaluation_state(
                    subscription.revision,
                    schedule_fingerprint,
                    baseline,
                    last_event_date,
                    emitted_dates,
                ),
                False,
            )

        if today in emitted_dates:
            return EvaluationDecision(
                _evaluation_state(
                    subscription.revision,
                    schedule_fingerprint,
                    baseline,
                    last_event_date,
                    emitted_dates,
                ),
                False,
            )
        occurrences = snapshot["occurrences"]
        document = build_calendar_document(
            occurrences,
            local_date=today,
            days=DAILY_WINDOW_DAYS,
            timezone_name=settings["timezone"],
            now=collected_at,
            source_variant=snapshot["source_variant"],
            source_url=snapshot["source_url"],
            privacy=Privacy.PUBLIC,
            warning_codes=snapshot["warning_codes"],
        )
        state = _evaluation_state(
            subscription.revision,
            schedule_fingerprint,
            baseline,
            today,
            _remember_date(emitted_dates, today),
        )
        return EvaluationDecision(
            state,
            True,
            event_key=f"ff14.calendar.daily.{today.isoformat()}",
            event_version=1,
            display_data=document,
        )


def _subscription_settings(subscription: SubscriptionView) -> dict[str, str]:
    if not isinstance(subscription, SubscriptionView):
        raise TypeError("subscription view required")
    filters = subscription.filters
    if filters.get("kind") != FILTER_KIND:
        raise ValueError("unsupported subscription kind")
    region = filters.get("region")
    if region not in ("cn", "global"):
        raise ValueError("unsupported region")
    timezone_name = filters.get("timezone", DEFAULT_TIMEZONE)
    local_time = filters.get("time", DEFAULT_LOCAL_TIME)
    load_timezone(timezone_name)
    parse_local_time(local_time)
    return {"region": region, "timezone": timezone_name, "time": local_time}


def _complete_payload(observation: Observation, region: str) -> Mapping[str, object]:
    if observation.data_version != 1:
        raise ValueError("unsupported observation data version")
    payload = observation.payload
    if (
        payload.get("schema_version") != 1
        or payload.get("region") != region
        or payload.get("completeness") != ObservationCompleteness.COMPLETE.value
    ):
        raise ValueError("observation payload is not a complete calendar snapshot")
    source_id = payload.get("source_id")
    spec = calendar_source_spec(region, source_id)
    if spec is None:
        raise ValueError("unknown calendar source")
    source_variant = payload.get("source_variant")
    if source_variant != spec.variant:
        raise ValueError("calendar source region mismatch")
    source_url = payload.get("source_url")
    if source_url != spec.url:
        raise ValueError("invalid calendar source URL")
    source_version = payload.get("source_version")
    if not isinstance(source_version, str) or not re.fullmatch(
        r"[0-9a-f]{64}", source_version
    ):
        raise ValueError("invalid source version")
    occurrences = payload.get("occurrences")
    if (
        not isinstance(occurrences, (tuple, list))
        or len(occurrences) > MAX_PAYLOAD_OCCURRENCES
    ):
        raise ValueError("invalid occurrence collection")
    try:
        window_start = datetime.fromisoformat(str(payload["window_start"]))
        window_end = datetime.fromisoformat(str(payload["window_end"]))
    except (KeyError, TypeError, ValueError):
        raise ValueError("invalid source window") from None
    if (
        window_start.tzinfo is None
        or window_end.tzinfo is None
        or window_start.utcoffset() != timedelta(0)
        or window_end.utcoffset() != timedelta(0)
        or window_start >= window_end
    ):
        raise ValueError("invalid source window")
    for occurrence in occurrences:
        item = _as_display_occurrence(occurrence)
        if item.source_id != source_id or len(item.summary) > 240:
            raise ValueError("invalid source occurrence")
    return {
        "source_id": source_id,
        "source_variant": source_variant,
        "source_url": source_url,
        "source_version": source_version,
        "window_start": window_start.astimezone(UTC),
        "window_end": window_end.astimezone(UTC),
        "occurrences": occurrences,
        "warning_codes": payload.get("warnings", ()),
    }


def _validate_collection_key(observation: Observation, region: str) -> None:
    key = observation.key
    if (
        key.module_id.rsplit("/", 1)[-1] != "ff14"
        or key.collector_id != "ff14.calendar.collect"
        or key.key_version != 1
        or key.source_id != "ff14_calendar_primary"
        or key.scope.kind is not OwnershipKind.PUBLIC
        or dict(key.parameters.values) != {"region": region}
    ):
        raise ValueError("calendar observation key does not match the public contract")


def _covers(
    snapshot: Mapping[str, object], required_start: datetime, required_end: datetime
) -> bool:
    start = snapshot["window_start"]
    end = snapshot["window_end"]
    return (
        isinstance(start, datetime)
        and isinstance(end, datetime)
        and start <= required_start
        and end >= required_end
    )


def _state_object(previous_state: EvaluationState | None) -> Mapping[str, object]:
    if previous_state is None or not isinstance(previous_state.value, Mapping):
        return {}
    return previous_state.value


def _preserved_state(prior: Mapping[str, object]) -> Mapping[str, object]:
    return dict(prior)


def _evaluation_state(
    subscription_revision: int,
    fingerprint: str,
    baseline: date,
    last_event_date: date | None,
    emitted_dates: tuple[date, ...] = (),
) -> Mapping[str, object]:
    return {
        "subscription_revision": subscription_revision,
        "schedule_fingerprint": fingerprint,
        "baseline_local_date": baseline.isoformat(),
        "last_event_local_date": None
        if last_event_date is None
        else last_event_date.isoformat(),
        "emitted_local_dates": tuple(day.isoformat() for day in emitted_dates),
    }


def _parse_emitted_dates(value: object) -> tuple[date, ...]:
    if not isinstance(value, (tuple, list)):
        return ()
    dates: list[date] = []
    for item in value:
        parsed = _parse_date(item)
        if parsed is not None and parsed not in dates:
            dates.append(parsed)
    return tuple(dates[-MAX_REMEMBERED_DATES:])


def _remember_date(emitted_dates: tuple[date, ...], today: date) -> tuple[date, ...]:
    if today in emitted_dates:
        return emitted_dates
    return (*emitted_dates, today)[-MAX_REMEMBERED_DATES:]


def _schedule_fingerprint(region: str, timezone_name: str, local_time: str) -> str:
    value = f"{region}\0{timezone_name}\0{local_time}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _local_midnight(day: date, zone: ZoneInfo) -> datetime:
    value = datetime.combine(day, time.min).replace(tzinfo=zone, fold=0)
    if not datetime_exists(value):
        value = resolve_imaginary(value)
    return value


def _local_due_instant(day: date, due_time: time, zone: ZoneInfo) -> datetime:
    """Resolve a scheduled wall time to its first real instant on that date.

    Ambiguous wall times use fold zero. A gap advances to the first valid local
    second after the gap, rather than shifting by the gap's full duration.
    """

    wall_time = datetime.combine(day, due_time)
    candidate = wall_time.replace(tzinfo=zone, fold=0)
    if datetime_exists(candidate):
        return candidate.astimezone(UTC)

    shifted = resolve_imaginary(candidate).replace(tzinfo=None)
    gap = shifted - wall_time
    if gap <= timedelta(0) or gap > timedelta(days=3):
        raise ValueError("timezone gap cannot be resolved safely")

    low = 1
    high = int(gap.total_seconds())
    while low < high:
        offset = (low + high) // 2
        probe = (wall_time + timedelta(seconds=offset)).replace(tzinfo=zone, fold=0)
        if datetime_exists(probe):
            high = offset
        else:
            low = offset + 1
    first_valid = (wall_time + timedelta(seconds=low)).replace(tzinfo=zone, fold=0)
    if not datetime_exists(first_valid):
        raise ValueError("timezone gap cannot be resolved safely")
    return first_valid.astimezone(UTC)


def _as_display_occurrence(
    raw: CalendarOccurrence | Mapping[str, object],
) -> DisplayOccurrence:
    if isinstance(raw, CalendarOccurrence):
        return DisplayOccurrence(
            summary=raw.summary,
            start=raw.start,
            end=raw.end,
            all_day=raw.all_day,
            source_id=raw.source_id,
        )
    if not isinstance(raw, Mapping):
        raise TypeError("occurrence must be a mapping")
    all_day = raw.get("all_day")
    if type(all_day) is not bool:
        raise ValueError("invalid occurrence kind")
    summary = raw.get("summary")
    source_id = raw.get("source_id")
    if not isinstance(summary, str) or not isinstance(source_id, str):
        raise ValueError("invalid occurrence text")
    start = _parse_temporal(raw.get("start"), all_day)
    end = _parse_temporal(raw.get("end"), all_day)
    if type(raw.get("cancelled", False)) is not bool:
        raise ValueError("invalid cancellation marker")
    return DisplayOccurrence(summary, start, end, all_day, source_id)


def _parse_temporal(value: object, all_day: bool) -> date | datetime:
    if not isinstance(value, str):
        raise ValueError("invalid occurrence time")
    if all_day:
        result = date.fromisoformat(value)
        return result
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    result = datetime.fromisoformat(normalized)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timed occurrence must be timezone-aware")
    return result


def _is_cancelled(raw: CalendarOccurrence | Mapping[str, object]) -> bool:
    if isinstance(raw, CalendarOccurrence):
        return raw.cancelled
    return raw.get("cancelled", False) is True


def _sort_key(item: DisplayOccurrence, zone: ZoneInfo) -> tuple[datetime, str]:
    if item.all_day:
        assert isinstance(item.start, date) and not isinstance(item.start, datetime)
        start_at = _local_midnight(item.start, zone).astimezone(UTC)
    else:
        assert isinstance(item.start, datetime)
        start_at = item.start.astimezone(UTC)
    return start_at, item.summary


def _bounded_summary(summary: str) -> str:
    clean = " ".join(summary.split())[:MAX_SUMMARY_LENGTH]
    return clean or "未命名活动"


def _format_start(item: DisplayOccurrence, zone: ZoneInfo) -> str:
    if item.all_day:
        assert isinstance(item.start, date) and not isinstance(item.start, datetime)
        return f"{item.start.isoformat()}（全天）"
    assert isinstance(item.start, datetime)
    return item.start.astimezone(zone).strftime("%Y-%m-%d %H:%M")


def _format_end(item: DisplayOccurrence, zone: ZoneInfo) -> str:
    if item.all_day:
        assert isinstance(item.end, date) and not isinstance(item.end, datetime)
        inclusive_end = item.end - timedelta(days=1)
        return f"{inclusive_end.isoformat()}（全天，排他结束）"
    assert isinstance(item.end, datetime)
    if item.end == item.start:
        return "单时点"
    return item.end.astimezone(zone).strftime("%Y-%m-%d %H:%M")


__all__ = [
    "CalendarDailySummaryEvaluator",
    "DEFAULT_LOCAL_TIME",
    "DEFAULT_TIMEZONE",
    "DAILY_WINDOW_DAYS",
    "FILTER_KIND",
    "SUMMARY_TYPE_ID",
    "build_calendar_document",
    "filter_occurrences",
    "load_timezone",
    "parse_local_time",
]
