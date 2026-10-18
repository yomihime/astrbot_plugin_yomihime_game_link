"""Bounded, durable source snapshots shared by queries and reminder evaluation.

Only Core-issued invocations may open records or fetch sources. No background
loop or task is owned here: the Core background schedule invokes synchronize.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Callable

from yomihime_game_link_sdk.errors import (
    RevisionConflict,
    SourceHttpError,
    UniqueConstraintViolation,
)
from yomihime_game_link_sdk.services import HttpRequest, ModuleServices
from yomihime_game_link_sdk.subscriptions import ObservationCompleteness

from .calendar_evaluator import CalendarSourceSpec, calendar_source_spec
from .calendar_ics import MAX_BYTES, parse_ics
from .calendar_models import CalendarOccurrence, Completeness

COLLECTION = "calendar_snapshots"
PRIMARY = "ff14_calendar_primary"
FALLBACK = "ff14_calendar_fallback"
MANUAL_COOLDOWN_SECONDS = 300
SOURCE_TIMEOUT_SECONDS = 3.0
MAX_REMINDER_AGE = timedelta(hours=48)


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
    fetched_at: datetime | None = None


def source_key(region: str, spec: CalendarSourceSpec) -> str:
    """One fixed slot per region/source; its URL fingerprint is fenced in value."""
    return f"{region}:{spec.source_id}"


def _instant(value: object) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value))
        return result.astimezone(UTC) if result.utcoffset() is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


class CalendarSnapshots:
    """Per-module singleflight plus persisted source attempt/cooldown CAS.

    Each reviewed region/source/URL keeps at most one complete and one partial
    raw UTF-8 feed (each <= MAX_BYTES), not an accumulating occurrence history.
    Re-parsing raw data preserves query-time floating timezone interpretation
    and arbitrary future recurrence windows without extra HTTP.
    """

    def __init__(
        self, services: ModuleServices, *, clock: Callable[[], datetime] | None = None
    ):
        self.services = services
        self.clock = clock or (lambda: datetime.now(UTC))
        self._locks: dict[str, asyncio.Lock] = {}

    def _now(self) -> datetime:
        value = self.clock()
        if value.utcoffset() is None:
            raise ValueError("calendar clock must be aware")
        return value.astimezone(UTC)

    async def synchronize(
        self, invocation, region: str, *, interval_seconds: int, mode: str = "due"
    ) -> bool:
        if region not in ("cn", "global") or mode not in ("due", "cold", "manual"):
            raise ValueError("invalid calendar synchronization input")
        if type(interval_seconds) is not int or interval_seconds not in (
            21600,
            43200,
            86400,
        ):
            raise ValueError("invalid calendar synchronization interval")
        bound = await self.services.scopes.bind(invocation)
        records = await bound.records.collection(COLLECTION)
        return await self._synchronize_bound(
            records, bound.http, region, interval_seconds, mode
        )

    async def _synchronize_bound(self, records, http, region, interval_seconds, mode):
        cooled_down = False
        for source_id in (PRIMARY, FALLBACK):
            spec = calendar_source_spec(region, source_id)
            if spec is None:
                continue
            value, limited = await self._synchronize_source(
                records, http, region, spec, interval_seconds, mode
            )
            cooled_down = cooled_down or limited
            # A complete primary is sufficient, including a fresh cached one.
            # A failed/partial primary may try fallback but never merges feeds.
            if (
                value
                and value.get("last_status") == "complete"
                and value.get("complete")
            ):
                break
        return cooled_down

    async def _synchronize_source(self, records, http, region, spec, interval, mode):
        key = source_key(region, spec)
        async with self._locks.setdefault(key, asyncio.Lock()):
            now = self._now()
            current = await records.get(key)
            value = dict(current.value) if current is not None else {}
            fingerprint = hashlib.sha256(spec.url.encode()).hexdigest()
            if value.get("url_fingerprint") != fingerprint:
                # A URL change is a new source identity. Reuse the bounded slot,
                # never its old payload, cooldown or due state.
                value = {}
            next_due = _instant(value.get("next_due_at"))
            anchor = _instant(value.get("schedule_anchor_at"))
            if anchor is not None:
                # Configuration changes use the existing background anchor.
                next_due = anchor + timedelta(seconds=interval)
            retry_after = _instant(value.get("retry_after"))
            if mode == "due" and next_due is not None and now < next_due:
                return value, False
            if mode == "cold" and (value.get("complete") or value.get("partial")):
                return value, False
            if (
                mode in ("cold", "manual")
                and retry_after is not None
                and now < retry_after
            ):
                return value, mode == "manual"
            # Claim before network IO. Restart and concurrent service instances
            # observe this durable bound even if the original task is cancelled.
            if mode != "manual" or next_due is None or now >= next_due:
                anchor = now
            assert anchor is not None
            value.update(
                {
                    "schema_version": 1,
                    "region": region,
                    "source_id": spec.source_id,
                    "url_fingerprint": hashlib.sha256(spec.url.encode()).hexdigest(),
                    "last_attempt_at": now.isoformat(),
                    "schedule_anchor_at": anchor.isoformat(),
                    "next_due_at": (anchor + timedelta(seconds=interval)).isoformat(),
                    "retry_after": (
                        now + timedelta(seconds=MANUAL_COOLDOWN_SECONDS)
                    ).isoformat(),
                    "last_status": "interrupted",
                }
            )
            try:
                claim = (
                    await records.create(key, value)
                    if current is None
                    else await records.replace(
                        key, value, expected_revision=current.revision
                    )
                )
            except (RevisionConflict, UniqueConstraintViolation):
                latest = await records.get(key)
                return (
                    dict(latest.value) if latest is not None else {}
                ), mode == "manual"
            try:
                # Each source has an independent budget. A slow primary cannot
                # consume the fallback's budget inside Core's ten-second run.
                async with asyncio.timeout(SOURCE_TIMEOUT_SECONDS):
                    response = await http.fetch(HttpRequest(spec.source_id, spec.path))
                if not 200 <= response.status_code < 300:
                    raise SourceHttpError(
                        "upstream_error", status_code=response.status_code
                    )
                raw = response.body
                if len(raw) > MAX_BYTES:
                    raise SourceHttpError("response_too_large")
                text = raw.decode("utf-8", errors="strict")
                start = datetime.combine(now.date() - timedelta(days=1), time.min, UTC)
                parsed = parse_ics(
                    raw,
                    window_start=start,
                    window_end=start + timedelta(days=32),
                    display_timezone="UTC",
                    source_id=spec.source_id,
                    reject_floating=False,
                )
                complete = parsed.completeness is Completeness.COMPLETE
                field = "complete" if complete else "partial"
                value[field] = {
                    "raw": text,
                    "fetched_at": self._now().isoformat(),
                    "version": hashlib.sha256(raw).hexdigest(),
                }
                value["last_status"] = field
                if complete:
                    # Success makes the obsolete partial slot unnecessary.
                    value.pop("partial", None)
            except SourceHttpError as exc:
                if exc.code == "cancelled":
                    raise
                value["last_status"] = "failed"
            except (TimeoutError, UnicodeError):
                value["last_status"] = "failed"
            # No catch for cancellation or authority/service errors: they must
            # propagate. The bound records service fences late module epochs.
            await records.replace(key, value, expected_revision=claim.revision)
            return value, False

    async def read(
        self,
        invocation,
        region: str,
        *,
        window_start: datetime,
        window_end: datetime,
        display_timezone: str,
        reject_floating: bool,
        interval_seconds: int,
        cold_sync: bool = False,
        manual: bool = False,
    ) -> CalendarSourceSnapshot:
        bound = await self.services.scopes.bind(invocation)
        records = await bound.records.collection(COLLECTION)
        values = [] if manual else await self._values(records, region)
        limited = False
        if manual or (
            cold_sync
            and not any(v.get("complete") or v.get("partial") for _, v in values)
        ):
            limited = await self._synchronize_bound(
                records,
                bound.http,
                region,
                interval_seconds,
                "manual" if manual else "cold",
            )
            values = await self._values(records, region)
        now = self._now()
        choices = []
        # Complete data always wins over partial data, even after failed refresh.
        # Prefer the newest successful source; primary wins timestamp ties.
        for spec, value in values:
            for field in ("complete", "partial"):
                stored = value.get(field)
                if not isinstance(stored, dict) and not hasattr(stored, "get"):
                    continue
                fetched_at = _instant(stored.get("fetched_at"))
                raw_text = stored.get("raw")
                if (
                    fetched_at is None
                    or not isinstance(raw_text, str)
                    or len(raw_text) > MAX_BYTES
                ):
                    continue
                raw = raw_text.encode("utf-8")
                if len(raw) > MAX_BYTES or hashlib.sha256(
                    raw
                ).hexdigest() != stored.get("version"):
                    continue
                parsed = parse_ics(
                    raw,
                    window_start=window_start,
                    window_end=window_end,
                    display_timezone=display_timezone,
                    source_id=spec.source_id,
                    reject_floating=reject_floating,
                )
                complete = (
                    field == "complete" and parsed.completeness is Completeness.COMPLETE
                )
                warnings = [f"parser:{item.code}" for item in parsed.warnings]
                if spec.source_id == FALLBACK and any(
                    other.source_id == PRIMARY and state.get("last_status") == "failed"
                    for other, state in values
                ):
                    warnings.append("primary_source_failed")
                age = max(timedelta(0), now - fetched_at)
                if age >= timedelta(seconds=2 * interval_seconds):
                    warnings.append("snapshot_stale")
                if (
                    value.get("last_status") in ("failed", "interrupted", "partial")
                    and field == "complete"
                ):
                    warnings.append("snapshot_refresh_failed")
                if limited:
                    warnings.append("refresh_cooldown")
                status = (
                    ObservationCompleteness.COMPLETE
                    if complete
                    else ObservationCompleteness.PARTIAL
                )
                if reject_floating and age > MAX_REMINDER_AGE:
                    status = ObservationCompleteness.FAILED
                    warnings.append("snapshot_too_old")
                snapshot = CalendarSourceSnapshot(
                    region,
                    status,
                    window_start,
                    window_end,
                    spec.source_id,
                    spec.variant,
                    spec.url,
                    stored["version"],
                    parsed.occurrences,
                    tuple(dict.fromkeys(warnings)),
                    fetched_at,
                )
                choices.append(
                    (
                        field == "complete",
                        bool(parsed.occurrences) if field == "partial" else True,
                        fetched_at,
                        spec.source_id == PRIMARY,
                        snapshot,
                    )
                )
        if choices:
            return max(choices, key=lambda item: item[:4])[-1]
        return CalendarSourceSnapshot(
            region,
            ObservationCompleteness.FAILED,
            window_start,
            window_end,
            warning_codes=("source_unavailable",),
        )

    async def _values(self, records, region):
        values = []
        for source_id in (PRIMARY, FALLBACK):
            spec = calendar_source_spec(region, source_id)
            if spec is None:
                continue
            record = await records.get(source_key(region, spec))
            if (
                record is not None
                and record.value.get("region") == region
                and record.value.get("source_id") == source_id
                and record.value.get("url_fingerprint")
                == hashlib.sha256(spec.url.encode()).hexdigest()
            ):
                values.append((spec, record.value))
        return values
