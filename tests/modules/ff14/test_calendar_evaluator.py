from __future__ import annotations

import hashlib
import unittest
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.presentation.rendering import (
    GenericDisplayRenderer,
    RenderingBounds,
)

from modules.ff14.features.calendar_evaluator import (
    CalendarDailySummaryEvaluator,
    build_calendar_document,
    filter_occurrences,
    load_timezone,
)
from yomihime_game_link_sdk.display import DisplayLimits, Privacy, TableBlock
from yomihime_game_link_sdk.storage import OwnerScope
from yomihime_game_link_sdk.subscriptions import (
    CollectionKey,
    EvaluationState,
    NormalizedInput,
    Observation,
    ObservationCompleteness,
    SubscriptionView,
)

PRIMARY = "ff14_calendar_primary"
FALLBACK = "ff14_calendar_fallback"
_SOURCE_URLS = {
    (
        "cn",
        PRIMARY,
    ): "https://calendar.google.com/calendar/ical/up88drvlnnh2t77hbpqq8v33i2cngfh7%40import.calendar.google.com/public/basic.ics",
    (
        "cn",
        FALLBACK,
    ): "https://p66-caldav.icloud.com/published/2/MTAyMTk3MTMxMjExMDIxOXsjasy7WUO0EcKVz7qGEuVjjTlRkgd6WOZM171uxP_u-QM51M24lHzRlAQir-oodDRRTzZeusSLbw0snkZoqI4",
    (
        "global",
        PRIMARY,
    ): "https://calendar.google.com/calendar/ical/1gpnler51bgs1ajti10ao946ou367bf6%40import.calendar.google.com/public/basic.ics",
    (
        "global",
        FALLBACK,
    ): "https://p66-caldav.icloud.com/published/2/MTAyMTk3MTMxMjExMDIxOXsjasy7WUO0EcKVz7qGEuVzSK8L9ZRQYf1sxUFeH1A1a22GJLf6nfk2-CZNYMv5iOxCNlUR-umbJKFWWAUVRp8",
}


def _subscription(
    *,
    revision: int = 1,
    region: str = "cn",
    timezone_name: str = "Asia/Shanghai",
    local_time: str = "08:00",
) -> SubscriptionView:
    return validate_contract(
        SubscriptionView(
            "sub-calendar",
            revision,
            "owner-private",
            None,
            "conversation-private",
            {
                "kind": "daily_summary",
                "region": region,
                "timezone": timezone_name,
                "time": local_time,
            },
        )
    )


def _occurrence(
    summary: str,
    start: str,
    end: str,
    *,
    all_day: bool = False,
    cancelled: bool = False,
    source_id: str = PRIMARY,
) -> dict[str, object]:
    return {
        "summary": summary,
        "start": start,
        "end": end,
        "all_day": all_day,
        "cancelled": cancelled,
        "source_id": source_id,
    }


def _observation(
    collected_at: datetime,
    occurrences: tuple[dict[str, object], ...] = (),
    *,
    region: str = "cn",
    source_id: str = PRIMARY,
    completeness: ObservationCompleteness = ObservationCompleteness.COMPLETE,
    covered_ids: tuple[str, ...] = (),
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> Observation:
    utc_day = collected_at.astimezone(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start = window_start or utc_day - timedelta(days=1)
    end = window_end or utc_day + timedelta(days=9)
    variant = {
        ("cn", PRIMARY): "cn_google",
        ("cn", FALLBACK): "cn_icloud",
        ("global", PRIMARY): "global_google",
        ("global", FALLBACK): "global_icloud",
    }[(region, source_id)]
    key = validate_contract(
        CollectionKey(
            "ff14/ff14",
            "ff14.calendar.collect",
            1,
            PRIMARY,
            validate_contract(NormalizedInput({"region": region})),
            OwnerScope.public(),
        )
    )
    payload: dict[str, object] = {
        "schema_version": 1,
        "completeness": completeness.value,
        "region": region,
        "source_id": source_id,
        "source_variant": variant,
        "source_url": _SOURCE_URLS[(region, source_id)],
        "source_version": hashlib.sha256(b"fixed test source").hexdigest(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "occurrences": occurrences,
    }
    return validate_contract(
        Observation(
            f"obs-{collected_at.isoformat()}-{source_id}-{completeness.value}",
            key,
            1,
            None,
            collected_at,
            completeness,
            covered_ids,
            payload,
        )
    )


def _state(decision, revision: int = 1) -> EvaluationState:
    return validate_contract(EvaluationState(revision, decision.state))


class CalendarEvaluationTests(unittest.TestCase):
    def test_all_reviewed_variants_and_untrusted_qualified_flag_preserve_state(self):
        evaluator = CalendarDailySummaryEvaluator()
        for region in ("cn", "global"):
            subscription = _subscription(region=region)
            baseline = evaluator.evaluate(
                subscription,
                _observation(datetime(2026, 9, 29, 23, 59, tzinfo=UTC), region=region),
                None,
            )
            previous = _state(baseline)
            for source_id in (PRIMARY, FALLBACK):
                observation = _observation(
                    datetime(2026, 9, 30, tzinfo=UTC),
                    region=region,
                    source_id=source_id,
                )
                self.assertTrue(
                    evaluator.evaluate(subscription, observation, previous).triggered
                )
                for patch in (
                    {"source_id": "unknown", "qualified": True},
                    {"source_variant": "unknown", "qualified": True},
                    {
                        "source_url": "https://unreviewed.invalid/private",
                        "qualified": True,
                    },
                ):
                    with self.subTest(
                        region=region, source_id=source_id, field=next(iter(patch))
                    ):
                        untrusted = replace(
                            observation, payload={**dict(observation.payload), **patch}
                        )
                        decision = evaluator.evaluate(subscription, untrusted, previous)
                        self.assertFalse(decision.triggered)
                        self.assertEqual(dict(decision.state), dict(previous.value))

    def test_cross_month_ongoing_event_stays_in_local_window(self):
        selected = filter_occurrences(
            (
                _occurrence(
                    "Across month", "2026-09-30T23:00:00Z", "2026-10-01T01:00:00Z"
                ),
            ),
            local_date=date(2026, 10, 1),
            days=7,
            zone=load_timezone("Asia/Shanghai"),
            now=datetime(2026, 10, 1, tzinfo=UTC),
        )
        self.assertEqual([item.summary for item in selected], ["Across month"])

    def test_baseline_before_due_triggers_at_due_and_emits_public_document(
        self,
    ) -> None:
        evaluator = CalendarDailySummaryEvaluator()
        subscription = _subscription()
        before_due = _observation(datetime(2026, 9, 29, 23, 59, tzinfo=UTC))

        baseline = evaluator.evaluate(subscription, before_due, None)
        self.assertFalse(baseline.triggered)
        self.assertEqual(baseline.state["baseline_local_date"], "2026-09-30")

        at_due = _observation(
            datetime(2026, 9, 30, 0, 0, tzinfo=UTC),
            (
                _occurrence(
                    "Tomorrow's trial",
                    "2026-09-30T03:00:00Z",
                    "2026-09-30T04:00:00Z",
                ),
            ),
        )
        emitted = evaluator.evaluate(subscription, at_due, _state(baseline))
        self.assertTrue(emitted.triggered)
        self.assertEqual(emitted.event_key, "ff14.calendar.daily.2026-09-30")
        self.assertEqual(emitted.event_version, 1)
        self.assertIs(emitted.display_data.privacy, Privacy.PUBLIC)
        self.assertEqual(emitted.state["last_event_local_date"], "2026-09-30")
        self.assertEqual(emitted.state["emitted_local_dates"], ("2026-09-30",))
        self.assertNotIn("owner-private", repr(emitted.display_data))
        self.assertNotIn("sub-calendar", repr(emitted.display_data))

    def test_first_observation_at_or_after_due_baselines_tomorrow(self) -> None:
        evaluator = CalendarDailySummaryEvaluator()
        subscription = _subscription()
        at_due = evaluator.evaluate(
            subscription,
            _observation(datetime(2026, 9, 30, 0, 0, tzinfo=UTC)),
            None,
        )
        self.assertFalse(at_due.triggered)
        self.assertEqual(at_due.state["baseline_local_date"], "2026-10-01")

        later_same_day = evaluator.evaluate(
            subscription,
            _observation(datetime(2026, 9, 30, 1, 0, tzinfo=UTC)),
            _state(at_due),
        )
        self.assertFalse(later_same_day.triggered)
        self.assertEqual(later_same_day.state["baseline_local_date"], "2026-10-01")

        next_day = evaluator.evaluate(
            subscription,
            _observation(datetime(2026, 10, 1, 0, 0, tzinfo=UTC)),
            _state(later_same_day),
        )
        self.assertTrue(next_day.triggered)
        self.assertEqual(next_day.event_key, "ff14.calendar.daily.2026-10-01")

    def test_delivery_filter_keeps_ongoing_and_exclusive_seventh_day_boundary(
        self,
    ) -> None:
        zone = load_timezone("Asia/Shanghai")
        now = datetime(2026, 9, 30, 0, 0, tzinfo=UTC)
        occurrences = (
            _occurrence(
                "Ongoing",
                "2026-09-29T23:00:00Z",
                "2026-09-30T01:00:00Z",
            ),
            _occurrence(
                "Already ended", "2026-09-29T20:00:00Z", "2026-09-29T21:00:00Z"
            ),
            _occurrence(
                "All day",
                "2026-09-30",
                "2026-10-01",
                all_day=True,
            ),
            _occurrence(
                "Last included day",
                "2026-10-06T15:00:00Z",
                "2026-10-06T15:30:00Z",
            ),
            _occurrence(
                "Outside exclusive end",
                "2026-10-06T16:00:00Z",
                "2026-10-06T17:00:00Z",
            ),
            _occurrence(
                "Cancelled",
                "2026-10-01T02:00:00Z",
                "2026-10-01T03:00:00Z",
                cancelled=True,
            ),
        )

        selected = filter_occurrences(
            occurrences,
            local_date=date(2026, 9, 30),
            days=7,
            zone=zone,
            now=now,
        )

        self.assertEqual(
            [item.summary for item in selected],
            ["All day", "Ongoing", "Last included day"],
        )
        all_day = next(item for item in selected if item.summary == "All day")
        self.assertEqual(all_day.end, date(2026, 10, 1))
        document = build_calendar_document(
            occurrences,
            local_date=date(2026, 9, 30),
            days=7,
            timezone_name="Asia/Shanghai",
            now=now,
            source_variant="cn_google",
            privacy=Privacy.PUBLIC,
        )
        table = next(
            block for block in document.ordered_blocks if isinstance(block, TableBlock)
        )
        self.assertEqual(table.rows[0][2], "2026-09-30（全天，排他结束）")

    def test_subscription_revision_preserves_same_day_emission_watermark(self) -> None:
        evaluator = CalendarDailySummaryEvaluator()
        initial = _subscription()
        baseline = evaluator.evaluate(
            initial,
            _observation(datetime(2026, 9, 29, 23, 59, tzinfo=UTC)),
            None,
        )
        first = evaluator.evaluate(
            initial,
            _observation(datetime(2026, 9, 30, 0, 0, tzinfo=UTC)),
            _state(baseline),
        )
        self.assertTrue(first.triggered)

        revised = _subscription(revision=2, local_time="10:00")
        revision_baseline = evaluator.evaluate(
            revised,
            _observation(datetime(2026, 9, 30, 1, 0, tzinfo=UTC)),
            _state(first),
        )
        self.assertFalse(revision_baseline.triggered)
        self.assertEqual(revision_baseline.state["baseline_local_date"], "2026-09-30")
        self.assertEqual(revision_baseline.state["last_event_local_date"], "2026-09-30")
        self.assertEqual(
            revision_baseline.state["emitted_local_dates"], ("2026-09-30",)
        )

        after_new_due = evaluator.evaluate(
            revised,
            _observation(datetime(2026, 9, 30, 2, 0, tzinfo=UTC)),
            _state(revision_baseline),
        )
        self.assertFalse(after_new_due.triggered)
        self.assertEqual(after_new_due.state["emitted_local_dates"], ("2026-09-30",))

    def test_timezone_change_cannot_reemit_an_older_date_from_bounded_history(self):
        evaluator = CalendarDailySummaryEvaluator()
        original_zone = ZoneInfo("Pacific/Kiritimati")
        before_first_due = datetime(2026, 9, 30, 7, 0, tzinfo=original_zone)
        first_due = datetime(2026, 9, 30, 8, 0, tzinfo=original_zone)
        initial = _subscription(timezone_name="Pacific/Kiritimati")
        first_baseline = evaluator.evaluate(
            initial,
            _observation(before_first_due.astimezone(UTC)),
            None,
        )
        first = evaluator.evaluate(
            initial,
            _observation(first_due.astimezone(UTC)),
            _state(first_baseline),
        )
        self.assertTrue(first.triggered)
        self.assertEqual(first.event_key, "ff14.calendar.daily.2026-09-30")

        second_day_due = datetime(2026, 10, 1, 8, 0, tzinfo=original_zone)
        second = evaluator.evaluate(
            initial,
            _observation(second_day_due.astimezone(UTC)),
            _state(first),
        )
        self.assertTrue(second.triggered)
        self.assertEqual(second.event_key, "ff14.calendar.daily.2026-10-01")

        revised = _subscription(
            revision=2,
            timezone_name="Etc/GMT+12",
            local_time="20:00",
        )
        revised_zone = ZoneInfo("Etc/GMT+12")
        before_revisited_due = datetime(2026, 9, 30, 7, 0, tzinfo=revised_zone)
        reset = evaluator.evaluate(
            revised,
            _observation(before_revisited_due.astimezone(UTC)),
            _state(second),
        )
        self.assertFalse(reset.triggered)
        self.assertEqual(reset.state["last_event_local_date"], "2026-10-01")
        self.assertEqual(
            reset.state["emitted_local_dates"], ("2026-09-30", "2026-10-01")
        )

        revisited_due = datetime(2026, 9, 30, 20, 0, tzinfo=revised_zone)
        replay = evaluator.evaluate(
            revised,
            _observation(revisited_due.astimezone(UTC)),
            _state(reset),
        )
        self.assertFalse(replay.triggered)
        self.assertEqual(replay.state["emitted_local_dates"][-1], "2026-10-01")

    def test_source_switch_and_duplicate_observation_do_not_repeat_daily_event(
        self,
    ) -> None:
        evaluator = CalendarDailySummaryEvaluator()
        subscription = _subscription()
        baseline = evaluator.evaluate(
            subscription,
            _observation(datetime(2026, 9, 29, 23, 59, tzinfo=UTC)),
            None,
        )
        primary = evaluator.evaluate(
            subscription,
            _observation(datetime(2026, 9, 30, 0, 0, tzinfo=UTC)),
            _state(baseline),
        )
        self.assertTrue(primary.triggered)

        fallback_obs = _observation(
            datetime(2026, 9, 30, 0, 10, tzinfo=UTC),
            source_id=FALLBACK,
        )
        fallback = evaluator.evaluate(subscription, fallback_obs, _state(primary))
        self.assertFalse(fallback.triggered)
        replay = evaluator.evaluate(subscription, fallback_obs, _state(fallback))
        self.assertFalse(replay.triggered)
        self.assertEqual(replay.state["emitted_local_dates"], ("2026-09-30",))

    def test_partial_or_failed_observations_preserve_prior_evaluation_state(
        self,
    ) -> None:
        evaluator = CalendarDailySummaryEvaluator()
        subscription = _subscription()
        baseline = evaluator.evaluate(
            subscription,
            _observation(datetime(2026, 9, 29, 23, 59, tzinfo=UTC)),
            None,
        )
        previous = _state(baseline)
        for completeness, covered in (
            (ObservationCompleteness.PARTIAL, ("covered-item",)),
            (ObservationCompleteness.FAILED, ()),
        ):
            with self.subTest(completeness=completeness):
                incomplete = _observation(
                    datetime(2026, 9, 30, 0, 5, tzinfo=UTC),
                    (
                        _occurrence(
                            "Previously known activity",
                            "2026-09-30T01:00:00Z",
                            "2026-09-30T02:00:00Z",
                        ),
                    ),
                    completeness=completeness,
                    covered_ids=covered,
                )
                decision = evaluator.evaluate(subscription, incomplete, previous)
                self.assertFalse(decision.triggered)
                self.assertEqual(dict(decision.state), dict(previous.value))
                self.assertIsNone(decision.display_data)
                self.assertIsNone(decision.event_key)

    def test_utc_minus_12_and_plus_14_local_date_windows_are_covered(self) -> None:
        evaluator = CalendarDailySummaryEvaluator()
        for timezone_name in ("Etc/GMT+12", "Pacific/Kiritimati"):
            with self.subTest(timezone=timezone_name):
                zone = ZoneInfo(timezone_name)
                subscription = _subscription(timezone_name=timezone_name)
                before_due = datetime(2026, 9, 30, 7, 0, tzinfo=zone)
                at_due = datetime(2026, 9, 30, 8, 0, tzinfo=zone)
                baseline = evaluator.evaluate(
                    subscription,
                    _observation(before_due.astimezone(UTC)),
                    None,
                )
                delivery = evaluator.evaluate(
                    subscription,
                    _observation(at_due.astimezone(UTC)),
                    _state(baseline),
                )
                self.assertTrue(delivery.triggered)
                self.assertEqual(delivery.event_key, "ff14.calendar.daily.2026-09-30")

    def test_due_time_in_dst_gap_runs_at_first_valid_local_instant(self) -> None:
        evaluator = CalendarDailySummaryEvaluator()
        subscription = _subscription(
            timezone_name="America/New_York", local_time="02:30"
        )
        before_gap = datetime(2026, 3, 8, 1, 59, tzinfo=ZoneInfo("America/New_York"))
        first_valid = datetime(2026, 3, 8, 3, 0, tzinfo=ZoneInfo("America/New_York"))
        baseline = evaluator.evaluate(
            subscription,
            _observation(before_gap.astimezone(UTC)),
            None,
        )
        delivery = evaluator.evaluate(
            subscription,
            _observation(first_valid.astimezone(UTC)),
            _state(baseline),
        )
        self.assertTrue(delivery.triggered)
        self.assertEqual(delivery.event_key, "ff14.calendar.daily.2026-03-08")

    def test_ambiguous_due_time_uses_first_fold_instant_and_never_repeats(self) -> None:
        evaluator = CalendarDailySummaryEvaluator()
        subscription = _subscription(
            timezone_name="America/New_York", local_time="01:30"
        )
        zone = ZoneInfo("America/New_York")
        before_due = datetime(2026, 11, 1, 0, 59, tzinfo=zone)
        baseline = evaluator.evaluate(
            subscription,
            _observation(before_due.astimezone(UTC)),
            None,
        )
        second_fold_0115 = evaluator.evaluate(
            subscription,
            _observation(datetime(2026, 11, 1, 6, 15, tzinfo=UTC)),
            _state(baseline),
        )
        self.assertTrue(second_fold_0115.triggered)
        self.assertEqual(second_fold_0115.event_key, "ff14.calendar.daily.2026-11-01")

        second_fold_0130 = evaluator.evaluate(
            subscription,
            _observation(datetime(2026, 11, 1, 6, 30, tzinfo=UTC)),
            _state(second_fold_0115),
        )
        self.assertFalse(second_fold_0130.triggered)

        first_fold_baseline = evaluator.evaluate(
            subscription,
            _observation(datetime(2026, 11, 1, 4, 59, tzinfo=UTC)),
            None,
        )
        first_fold_due = evaluator.evaluate(
            subscription,
            _observation(datetime(2026, 11, 1, 5, 30, tzinfo=UTC)),
            _state(first_fold_baseline),
        )
        self.assertTrue(first_fold_due.triggered)
        for instant in (
            datetime(2026, 11, 1, 6, 15, tzinfo=UTC),
            datetime(2026, 11, 1, 6, 30, tzinfo=UTC),
        ):
            with self.subTest(second_fold_instant=instant):
                repeated = evaluator.evaluate(
                    subscription,
                    _observation(instant),
                    _state(first_fold_due),
                )
                self.assertFalse(repeated.triggered)

    def test_first_complete_in_second_fold_baselines_tomorrow(self) -> None:
        evaluator = CalendarDailySummaryEvaluator()
        subscription = _subscription(
            timezone_name="America/New_York", local_time="01:30"
        )
        second_fold_0115 = _observation(datetime(2026, 11, 1, 6, 15, tzinfo=UTC))
        baseline = evaluator.evaluate(subscription, second_fold_0115, None)
        self.assertFalse(baseline.triggered)
        self.assertEqual(baseline.state["baseline_local_date"], "2026-11-02")

        second_fold_0130 = evaluator.evaluate(
            subscription,
            _observation(datetime(2026, 11, 1, 6, 30, tzinfo=UTC)),
            _state(baseline),
        )
        self.assertFalse(second_fold_0130.triggered)
        self.assertEqual(second_fold_0130.state["baseline_local_date"], "2026-11-02")

    def test_emitted_date_history_is_bounded(self) -> None:
        evaluator = CalendarDailySummaryEvaluator()
        subscription = _subscription()
        state = None
        for day_number in range(40):
            local_day = date(2026, 9, 1) + timedelta(days=day_number)
            before = datetime.combine(local_day, time(7), ZoneInfo("Asia/Shanghai"))
            at_due = datetime.combine(local_day, time(8), ZoneInfo("Asia/Shanghai"))
            baseline = evaluator.evaluate(
                subscription,
                _observation(before.astimezone(UTC)),
                state,
            )
            delivered = evaluator.evaluate(
                subscription,
                _observation(at_due.astimezone(UTC)),
                _state(baseline),
            )
            self.assertTrue(delivered.triggered)
            state = _state(delivered)
        self.assertEqual(len(state.value["emitted_local_dates"]), 32)
        self.assertEqual(state.value["emitted_local_dates"][-1], "2026-10-10")


class CalendarSummaryRenderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_v1_without_warnings_and_safe_warning_projection_reach_renderer(
        self,
    ):
        evaluator = CalendarDailySummaryEvaluator()
        subscription = _subscription()
        baseline = evaluator.evaluate(
            subscription, _observation(datetime(2026, 9, 29, 23, 59, tzinfo=UTC)), None
        )
        observation = _observation(
            datetime(2026, 9, 30, tzinfo=UTC), source_id=FALLBACK
        )
        renderer = GenericDisplayRenderer(
            RenderingBounds(8000, 100, 10, 20, 32, 128, 8, 64, 32)
        )
        for warnings in (
            None,
            (
                "primary_source_failed",
                "parser:INVALID_EVENT",
                "parser:synthetic-secret",
                "synthetic-secret",
                7,
            ),
        ):
            with self.subTest(old_v1=warnings is None):
                current = (
                    observation
                    if warnings is None
                    else replace(
                        observation,
                        payload={**dict(observation.payload), "warnings": warnings},
                    )
                )
                decision = evaluator.evaluate(subscription, current, _state(baseline))
                self.assertTrue(decision.triggered)
                rendered = await renderer.render(
                    decision.display_data,
                    limits=validate_contract(
                        DisplayLimits(max_pages=2, max_image_bytes=16)
                    ),
                )
                text = rendered.text
                self.assertIn("已使用合格备用日历来源", text)
                self.assertIn("来源更新时间未知", text)
                self.assertIn("不保证覆盖全部活动", text)
                self.assertIn("该公开来源在当前窗口未返回活动", text)
                self.assertNotIn("synthetic-secret", text)
                self.assertNotIn("primary_source", text)
                self.assertNotIn("主日历源未能完整解析", text)
                if warnings is None:
                    self.assertNotIn("主日历源暂不可用", text)
                else:
                    self.assertIn("主日历源暂不可用，已尝试备用源", text)
                    self.assertIn("日历部分内容不支持自动处理", text)
                    self.assertLess(
                        text.index("获取时间"), text.index("主日历源暂不可用")
                    )


if __name__ == "__main__":
    unittest.main()
