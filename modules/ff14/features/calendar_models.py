from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class Completeness(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"


@dataclass(frozen=True, slots=True)
class CalendarOccurrence:
    uid: str
    recurrence_identity: str
    summary: str
    start: date | datetime
    end: date | datetime
    all_day: bool
    cancelled: bool
    source_id: str


@dataclass(frozen=True, slots=True)
class CalendarWarning:
    code: str
    message: str
    series_key: str | None = None


@dataclass(frozen=True, slots=True)
class CalendarParseResult:
    source_id: str
    completeness: Completeness
    occurrences: tuple[CalendarOccurrence, ...]
    warnings: tuple[CalendarWarning, ...]
