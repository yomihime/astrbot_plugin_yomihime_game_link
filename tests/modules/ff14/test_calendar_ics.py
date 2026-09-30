from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from modules.ff14.features import calendar_ics
from modules.ff14.features.calendar_ics import parse_ics
from modules.ff14.features.calendar_models import Completeness


def _calendar(*events: str) -> bytes:
    body = "\r\n".join(
        (
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Tests//FF14//EN",
            *events,
            "END:VCALENDAR",
            "",
        )
    )
    return body.encode("utf-8")


def _event(uid: str, *properties: str) -> str:
    return "\r\n".join(("BEGIN:VEVENT", f"UID:{uid}", *properties, "END:VEVENT"))


def _parse(
    raw: bytes,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    reject_floating: bool = False,
):
    zone = ZoneInfo("Asia/Shanghai")
    return parse_ics(
        raw,
        window_start=start or datetime(2026, 9, 28, tzinfo=zone),
        window_end=end or datetime(2026, 10, 5, tzinfo=zone),
        display_timezone="Asia/Shanghai",
        source_id="synthetic-test",
        reject_floating=reject_floating,
    )


class CalendarIcsTests(unittest.TestCase):
    def test_floating_policy_is_opt_in_and_applies_to_all_temporal_properties(
        self,
    ) -> None:
        valid_aware = _event(
            "aware",
            "DTSTART:20260929T010000Z",
            "DTEND:20260929T020000Z",
        )
        default = _parse(
            _calendar(
                valid_aware,
                _event(
                    "floating-default",
                    "DTSTART:20260929T090000",
                    "DTEND:20260929T100000",
                ),
            )
        )
        self.assertEqual(default.completeness, Completeness.COMPLETE)
        self.assertEqual(len(default.occurrences), 2)

        cases = {
            "DTSTART": _calendar(
                _event(
                    "floating-start",
                    "DTSTART:20260929T090000",
                    "DTEND:20260929T100000",
                )
            ),
            "DTEND": _calendar(
                _event(
                    "floating-end",
                    "DTSTART:20260929T010000Z",
                    "DTEND:20260929T100000",
                )
            ),
            "RDATE": _calendar(
                _event(
                    "floating-rdate",
                    "DTSTART;TZID=Asia/Shanghai:20260929T090000",
                    "DTEND;TZID=Asia/Shanghai:20260929T100000",
                    "RDATE:20261001T090000",
                )
            ),
            "EXDATE": _calendar(
                _event(
                    "floating-exdate",
                    "DTSTART;TZID=Asia/Shanghai:20260929T090000",
                    "DTEND;TZID=Asia/Shanghai:20260929T100000",
                    "EXDATE:20261001T090000",
                )
            ),
            "RECURRENCE-ID": _calendar(
                _event(
                    "floating-rid",
                    "DTSTART;TZID=Asia/Shanghai:20260929T090000",
                    "DTEND;TZID=Asia/Shanghai:20260929T100000",
                    "RRULE:FREQ=DAILY;COUNT=2",
                ),
                _event(
                    "floating-rid",
                    "RECURRENCE-ID:20260930T090000",
                    "DTSTART;TZID=Asia/Shanghai:20260930T110000",
                    "DTEND;TZID=Asia/Shanghai:20260930T120000",
                ),
            ),
        }
        for property_name, raw in cases.items():
            with self.subTest(property=property_name):
                result = _parse(raw, reject_floating=True)
                self.assertEqual(result.completeness, Completeness.PARTIAL)
                self.assertEqual(result.occurrences, ())
                self.assertTrue(
                    any(
                        warning.code == "FLOATING_TIME_UNSUPPORTED"
                        for warning in result.warnings
                    )
                )

        all_day = _parse(
            _calendar(
                _event(
                    "date-only",
                    "DTSTART;VALUE=DATE:20260929",
                    "DTEND;VALUE=DATE:20260930",
                )
            ),
            reject_floating=True,
        )
        self.assertEqual(all_day.completeness, Completeness.COMPLETE)
        self.assertEqual(len(all_day.occurrences), 1)

    def test_all_day_end_is_exclusive_and_text_escapes_are_decoded(self) -> None:
        result = _parse(
            _calendar(
                _event(
                    "fair-1",
                    "SUMMARY:Rock\\, Paper\\;\r\n Fold\\nScissors",
                    "DTSTART;VALUE=DATE:20260929",
                    "DTEND;VALUE=DATE:20260930",
                )
            )
        )
        self.assertEqual(result.completeness, Completeness.COMPLETE)
        self.assertEqual(len(result.occurrences), 1)
        event = result.occurrences[0]
        self.assertEqual(event.summary, "Rock, Paper;Fold\nScissors")
        self.assertEqual(event.start.isoformat(), "2026-09-29")
        self.assertEqual(event.end.isoformat(), "2026-09-30")
        self.assertTrue(event.all_day)
        self.assertEqual(event.source_id, "synthetic-test")

    def test_window_is_half_open_for_timed_and_all_day_events(self) -> None:
        zone = ZoneInfo("Asia/Shanghai")
        start = datetime(2026, 9, 29, tzinfo=zone)
        end = datetime(2026, 9, 30, tzinfo=zone)
        result = _parse(
            _calendar(
                _event(
                    "touch-end", "DTSTART:20260929T150000Z", "DTEND:20260929T160000Z"
                ),
                _event(
                    "touch-window-start",
                    "DTSTART:20260928T150000Z",
                    "DTEND:20260928T160000Z",
                ),
                _event("point-at-window-start", "DTSTART:20260928T160000Z"),
                _event(
                    "day-before",
                    "DTSTART;VALUE=DATE:20260928",
                    "DTEND;VALUE=DATE:20260929",
                ),
            ),
            start=start,
            end=end,
        )
        self.assertEqual(result.completeness, Completeness.COMPLETE)
        self.assertEqual(
            {item.uid for item in result.occurrences},
            {"touch-end", "point-at-window-start"},
        )

    def test_multimonth_oneoff_is_checked_by_overlap_not_duration_limit(self) -> None:
        result = _parse(
            _calendar(
                _event(
                    "long-event",
                    "DTSTART:20260101T000000Z",
                    "DTEND:20261001T000000Z",
                )
            )
        )
        self.assertEqual(result.completeness, Completeness.COMPLETE)
        self.assertEqual([item.uid for item in result.occurrences], ["long-event"])

    def test_known_tzid_uses_fold_first_and_gap_forward_policy(self) -> None:
        spring = _parse(
            _calendar(
                _event(
                    "spring-gap",
                    'DTSTART;TZID="America/New_York":20260308T023000',
                    "DTEND;TZID=America/New_York:20260308T033000",
                ),
            ),
            start=datetime(2026, 3, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
            end=datetime(2026, 3, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
        fall = _parse(
            _calendar(
                _event(
                    "fall-fold",
                    "DTSTART;TZID=America/New_York:20261101T013000",
                    "DTEND:20261101T063000Z",
                ),
            ),
            start=datetime(2026, 10, 25, tzinfo=ZoneInfo("Asia/Shanghai")),
            end=datetime(2026, 11, 8, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
        self.assertEqual(spring.completeness, Completeness.COMPLETE)
        self.assertEqual(fall.completeness, Completeness.COMPLETE)
        self.assertEqual(
            spring.occurrences[0].start.isoformat(), "2026-03-08T15:00:00+08:00"
        )
        self.assertEqual(
            fall.occurrences[0].start.isoformat(), "2026-11-01T13:30:00+08:00"
        )
        self.assertEqual(
            fall.occurrences[0].end.isoformat(), "2026-11-01T14:30:00+08:00"
        )

    def test_unknown_tzid_is_partial_and_does_not_emit_that_uid(self) -> None:
        result = _parse(
            _calendar(
                _event("broken-zone", "DTSTART;TZID=Nowhere/Missing:20260929T090000"),
                _event("known", "DTSTART:20260929T010000Z"),
            )
        )
        self.assertEqual(result.completeness, Completeness.PARTIAL)
        self.assertEqual([item.uid for item in result.occurrences], ["known"])
        self.assertIn("INVALID_TIMEZONE", {warning.code for warning in result.warnings})

    def test_recurrence_applies_exdate_rdate_and_exact_cancelled_override(self) -> None:
        result = _parse(
            _calendar(
                _event(
                    "weekly",
                    "SUMMARY:Trial",
                    "DTSTART;TZID=Asia/Shanghai:20260928T090000",
                    "DTEND;TZID=Asia/Shanghai:20260928T100000",
                    "RRULE:FREQ=WEEKLY;COUNT=3;BYDAY=MO",
                    "RDATE;TZID=Asia/Shanghai:20260930T090000",
                    "EXDATE;TZID=Asia/Shanghai:20261005T090000",
                ),
                _event(
                    "weekly",
                    "RECURRENCE-ID;TZID=Asia/Shanghai:20260928T090000",
                    "DTSTART;TZID=Asia/Shanghai:20260928T110000",
                    "DTEND;TZID=Asia/Shanghai:20260928T120000",
                    "STATUS:CANCELLED",
                ),
            )
        )
        self.assertEqual(result.completeness, Completeness.COMPLETE)
        events = {item.recurrence_identity: item for item in result.occurrences}
        self.assertEqual(len(events), 2)
        moved = events["2026-09-28T09:00:00+08:00"]
        self.assertTrue(moved.cancelled)
        self.assertEqual(moved.start.isoformat(), "2026-09-28T11:00:00+08:00")
        self.assertIn("2026-09-30T09:00:00+08:00", events)

    def test_daily_and_monthly_recurrence_subset(self) -> None:
        result = _parse(
            _calendar(
                _event("daily", "DTSTART:20260928T010000Z", "RRULE:FREQ=DAILY;COUNT=3"),
                _event(
                    "monthly",
                    "DTSTART;TZID=Asia/Shanghai:20260901T090000",
                    "RRULE:FREQ=MONTHLY;COUNT=3;BYMONTHDAY=1",
                ),
            ),
            start=datetime(2026, 9, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
            end=datetime(2026, 10, 3, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
        self.assertEqual(result.completeness, Completeness.COMPLETE)
        grouped = {}
        for item in result.occurrences:
            grouped.setdefault(item.uid, []).append(item)
        self.assertEqual(len(grouped["daily"]), 3)
        self.assertEqual(
            [item.start.isoformat() for item in grouped["monthly"]],
            ["2026-09-01T09:00:00+08:00", "2026-10-01T09:00:00+08:00"],
        )

    def test_all_day_recurrence_respects_date_until(self) -> None:
        result = _parse(
            _calendar(
                _event(
                    "daily-days",
                    "DTSTART;VALUE=DATE:20260928",
                    "RRULE:FREQ=DAILY;UNTIL=20260930",
                )
            )
        )
        self.assertEqual(result.completeness, Completeness.COMPLETE)
        self.assertEqual(
            [item.start.isoformat() for item in result.occurrences],
            ["2026-09-28", "2026-09-29", "2026-09-30"],
        )

    def test_weekly_recurrence_keeps_local_wall_clock_across_dst(self) -> None:
        result = _parse(
            _calendar(
                _event(
                    "dst-weekly",
                    "DTSTART;TZID=America/New_York:20260301T090000",
                    "RRULE:FREQ=WEEKLY;COUNT=2",
                )
            ),
            start=datetime(2026, 3, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
            end=datetime(2026, 3, 16, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
        self.assertEqual(result.completeness, Completeness.COMPLETE)
        self.assertEqual(
            [item.start.isoformat() for item in result.occurrences],
            ["2026-03-01T22:00:00+08:00", "2026-03-08T21:00:00+08:00"],
        )

    def test_highest_sequence_revision_wins_and_conflicting_tie_is_partial(
        self,
    ) -> None:
        selected = _parse(
            _calendar(
                _event(
                    "revision", "SUMMARY:Old", "SEQUENCE:1", "DTSTART:20260929T010000Z"
                ),
                _event(
                    "revision", "SUMMARY:New", "SEQUENCE:2", "DTSTART:20260929T010000Z"
                ),
            )
        )
        self.assertEqual(selected.completeness, Completeness.COMPLETE)
        self.assertEqual(selected.occurrences[0].summary, "New")

        conflicted = _parse(
            _calendar(
                _event(
                    "revision", "SUMMARY:One", "SEQUENCE:2", "DTSTART:20260929T010000Z"
                ),
                _event(
                    "revision", "SUMMARY:Two", "SEQUENCE:2", "DTSTART:20260929T010000Z"
                ),
            )
        )
        self.assertEqual(conflicted.completeness, Completeness.PARTIAL)
        self.assertEqual(conflicted.occurrences, ())
        self.assertIn(
            "CONFLICTING_REVISION", {warning.code for warning in conflicted.warnings}
        )

    def test_orphan_and_range_override_are_partial(self) -> None:
        orphan = _parse(
            _calendar(
                _event(
                    "orphan",
                    "DTSTART:20260929T010000Z",
                    "RRULE:FREQ=DAILY;COUNT=2",
                ),
                _event(
                    "orphan",
                    "RECURRENCE-ID:20261010T010000Z",
                    "DTSTART:20261010T020000Z",
                    "STATUS:CANCELLED",
                ),
            )
        )
        self.assertEqual(orphan.completeness, Completeness.PARTIAL)
        self.assertEqual(orphan.occurrences, ())
        self.assertIn("ORPHAN_OVERRIDE", {warning.code for warning in orphan.warnings})

        range_override = _parse(
            _calendar(
                _event(
                    "range",
                    "DTSTART:20260929T010000Z",
                    "RRULE:FREQ=DAILY;COUNT=2",
                ),
                _event(
                    "range",
                    "RECURRENCE-ID;RANGE=THISANDFUTURE:20260930T010000Z",
                    "DTSTART:20260930T020000Z",
                ),
            )
        )
        self.assertEqual(range_override.completeness, Completeness.PARTIAL)
        self.assertEqual(range_override.occurrences, ())

    def test_override_temporal_kind_mismatch_is_partial_for_its_uid(self) -> None:
        result = _parse(
            _calendar(
                _event(
                    "timed-master",
                    "DTSTART:20260928T010000Z",
                    "DTEND:20260928T020000Z",
                    "RRULE:FREQ=DAILY;COUNT=2",
                ),
                _event(
                    "timed-master",
                    "RECURRENCE-ID:20260928T010000Z",
                    "DTSTART;VALUE=DATE:20260928",
                    "DTEND;VALUE=DATE:20260929",
                ),
                _event("unaffected", "DTSTART:20260929T010000Z"),
            )
        )
        self.assertEqual(result.completeness, Completeness.PARTIAL)
        self.assertEqual([item.uid for item in result.occurrences], ["unaffected"])
        self.assertIn("INVALID_EVENT", {warning.code for warning in result.warnings})

    def test_override_end_before_inherited_start_is_partial_for_its_uid(self) -> None:
        result = _parse(
            _calendar(
                _event(
                    "negative-end",
                    "DTSTART:20260928T010000Z",
                    "DTEND:20260928T020000Z",
                    "RRULE:FREQ=DAILY;COUNT=2",
                ),
                _event(
                    "negative-end",
                    "RECURRENCE-ID:20260928T010000Z",
                    "DTEND:20260927T000000Z",
                ),
                _event("unaffected", "DTSTART:20260929T010000Z"),
            )
        )
        self.assertEqual(result.completeness, Completeness.PARTIAL)
        self.assertEqual([item.uid for item in result.occurrences], ["unaffected"])
        self.assertIn("INVALID_EVENT", {warning.code for warning in result.warnings})

    def test_unsupported_rule_makes_only_its_series_partial(self) -> None:
        result = _parse(
            _calendar(
                _event(
                    "yearly",
                    "DTSTART:20260929T010000Z",
                    "RRULE:FREQ=YEARLY;COUNT=2",
                ),
                _event("single", "DTSTART:20260930T010000Z"),
            )
        )
        self.assertEqual(result.completeness, Completeness.PARTIAL)
        self.assertEqual({item.uid for item in result.occurrences}, {"single"})
        self.assertIn(
            "UNSUPPORTED_RECURRENCE", {warning.code for warning in result.warnings}
        )

    def test_old_unbounded_series_stops_at_candidate_budget(self) -> None:
        result = _parse(
            _calendar(
                _event(
                    "ancient",
                    "DTSTART:19000101T000000Z",
                    "RRULE:FREQ=DAILY",
                )
            )
        )
        self.assertEqual(result.completeness, Completeness.PARTIAL)
        self.assertEqual(result.occurrences, ())
        self.assertIn(
            "RECURRENCE_BUDGET", {warning.code for warning in result.warnings}
        )

    def test_old_sparse_rule_fails_before_dateutil_can_seek(self) -> None:
        result = _parse(
            _calendar(
                _event(
                    "sparse-ancient",
                    "DTSTART:19000131T000000Z",
                    "RRULE:FREQ=MONTHLY;BYMONTHDAY=31",
                )
            )
        )
        self.assertEqual(result.completeness, Completeness.PARTIAL)
        self.assertEqual(result.occurrences, ())
        self.assertIn(
            "RECURRENCE_BUDGET", {warning.code for warning in result.warnings}
        )

    def test_event_property_and_output_budgets_degrade_the_source(self) -> None:
        with patch.object(calendar_ics, "MAX_EVENTS", 1):
            event_limited = _parse(
                _calendar(
                    _event("first", "DTSTART:20260929T010000Z"),
                    _event("second", "DTSTART:20260930T010000Z"),
                )
            )
        self.assertEqual(event_limited.completeness, Completeness.PARTIAL)
        self.assertEqual(event_limited.occurrences, ())
        self.assertEqual(event_limited.warnings[0].code, "EVENT_LIMIT")

        with patch.object(calendar_ics, "MAX_PROPERTIES_PER_EVENT", 2):
            property_limited = _parse(
                _calendar(
                    _event(
                        "property-count",
                        "SUMMARY:Too many properties",
                        "DTSTART:20260929T010000Z",
                    )
                )
            )
        self.assertEqual(property_limited.completeness, Completeness.PARTIAL)
        self.assertEqual(property_limited.occurrences, ())
        self.assertEqual(property_limited.warnings[0].code, "PROPERTY_LIMIT")

        with patch.object(calendar_ics, "MAX_OCCURRENCES", 1):
            output_limited = _parse(
                _calendar(
                    _event("first", "DTSTART:20260929T010000Z"),
                    _event("second", "DTSTART:20260930T010000Z"),
                )
            )
        self.assertEqual(output_limited.completeness, Completeness.PARTIAL)
        self.assertEqual(len(output_limited.occurrences), 1)
        self.assertEqual(output_limited.warnings[0].code, "OUTPUT_LIMIT")

    def test_source_budget_covers_candidates_across_uids(self) -> None:
        with patch.object(calendar_ics, "MAX_CANDIDATES_PER_SOURCE", 13):
            result = _parse(
                _calendar(
                    _event(
                        "first", "DTSTART:20260928T010000Z", "RRULE:FREQ=DAILY;COUNT=3"
                    ),
                    _event(
                        "second", "DTSTART:20260928T020000Z", "RRULE:FREQ=DAILY;COUNT=3"
                    ),
                )
            )
        self.assertEqual(result.completeness, Completeness.PARTIAL)
        self.assertEqual({item.uid for item in result.occurrences}, {"first"})
        self.assertIn("SOURCE_BUDGET", {warning.code for warning in result.warnings})

    def test_input_limits_return_partial_without_echoing_source(self) -> None:
        invalid = _parse(b"BEGIN:VCALENDAR\r\n\xff\r\nEND:VCALENDAR")
        self.assertEqual(invalid.completeness, Completeness.PARTIAL)
        self.assertEqual(invalid.warnings[0].code, "INVALID_UTF8")
        self.assertNotIn("\xff", invalid.warnings[0].message)

        malformed = _parse(b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n")
        self.assertEqual(malformed.completeness, Completeness.PARTIAL)
        self.assertEqual(malformed.warnings[0].code, "MALFORMED_SOURCE")

        with patch.object(calendar_ics, "MAX_BYTES", 4):
            oversized = _parse(_calendar(_event("large", "DTSTART:20260929T090000Z")))
        self.assertEqual(oversized.completeness, Completeness.PARTIAL)
        self.assertEqual(oversized.warnings[0].code, "INPUT_TOO_LARGE")

        with patch.object(calendar_ics, "MAX_LINES", 2):
            over_lines = _parse(_calendar(_event("lines", "DTSTART:20260929T090000Z")))
        self.assertEqual(over_lines.completeness, Completeness.PARTIAL)
        self.assertEqual(over_lines.warnings[0].code, "LINE_LIMIT")

        with patch.object(calendar_ics, "MAX_WARNINGS", 2):
            warning_limited = _parse(
                _calendar(
                    _event("bad-1", "DTSTART;TZID=Nowhere/One:20260929T090000"),
                    _event("bad-2", "DTSTART;TZID=Nowhere/Two:20260929T090000"),
                    _event("bad-3", "DTSTART;TZID=Nowhere/Three:20260929T090000"),
                )
            )
        self.assertEqual(warning_limited.completeness, Completeness.PARTIAL)
        self.assertEqual(len(warning_limited.warnings), 2)

    def test_invalid_caller_window_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_ics(
                b"",
                window_start=datetime(2026, 9, 28),
                window_end=datetime(2026, 9, 29, tzinfo=timezone.utc),
                display_timezone="UTC",
                source_id="synthetic-test",
            )


if __name__ == "__main__":
    unittest.main()
