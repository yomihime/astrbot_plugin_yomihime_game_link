from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest import mock

from modules.ff14.features.calendar import CalendarCollector, CalendarQuery
from modules.ff14.features.calendar_evaluator import calendar_source_spec
from modules.ff14.features.calendar_ics import MAX_BYTES
from modules.ff14.features.calendar_snapshots import (
    MANUAL_COOLDOWN_SECONDS,
    CalendarSnapshots,
    source_key,
)
from tests.modules.ff14.test_calendar import (
    _ConfigView,
    _event,
    _FakeHttp,
    _ics,
    _invocation,
    _Scopes,
)
from yomihime_game_link_sdk.errors import SourceHttpError
from yomihime_game_link_sdk.results import ResultStatus
from yomihime_game_link_sdk.storage import OwnerScope
from yomihime_game_link_sdk.subscriptions import (
    CollectionKey,
    CollectionView,
    NormalizedInput,
    ObservationCompleteness,
)

BASE = datetime(2026, 9, 30, 0, tzinfo=UTC)


def feed(title="Local event", *, floating=False):
    zone = "" if floating else "Z"
    return _ics(
        _event(
            "persistent",
            f"SUMMARY:{title}",
            f"DTSTART:20260930T120000{zone}",
            f"DTEND:20260930T130000{zone}",
            "RRULE:FREQ=DAILY;COUNT=100",
        )
    )


class CalendarSnapshotsTests(unittest.IsolatedAsyncioTestCase):
    def setup_services(self, responses):
        self.now = BASE
        self.http = _FakeHttp(responses)
        self.scopes = _Scopes(self.http)
        self.services = type(
            "Services", (), {"config": _ConfigView(), "scopes": self.scopes}
        )()
        self.store = CalendarSnapshots(self.services, clock=lambda: self.now)
        self.query = CalendarQuery(
            self.services, clock=lambda: self.now, snapshots=self.store
        )
        self.records = self.scopes.bound.records

    async def sync(self, *, mode="due", region="cn"):
        return await self.store.synchronize(
            _invocation(), region, interval_seconds=21600, mode=mode
        )

    async def query_cn(self, **parameters):
        return await self.query.invoke(_invocation(), {"region": "cn", **parameters})

    async def observe(self):
        parameters = NormalizedInput({"region": "cn"})
        context = CollectionView(
            CollectionKey(
                "ff14/ff14",
                "ff14.calendar.collect",
                1,
                "ff14_calendar_primary",
                parameters,
                OwnerScope.public(),
            ),
            deadline_monotonic=1000,
            invocation=_invocation(),
        )
        return await CalendarCollector(
            self.services, clock=lambda: self.now, snapshots=self.store
        ).collect(context, parameters, None)

    async def test_cold_once_hot_and_expired_query_never_refetch(self):
        self.setup_services([feed()])
        first = await self.query_cn(days=30)
        self.assertIs(first.status, ResultStatus.SUCCESS)
        self.assertIn("Local event", repr(first.document))
        await self.query_cn()
        self.now += timedelta(hours=13)
        stale = await self.query_cn()
        self.assertEqual(len(self.http.requests), 1)
        self.assertIn("本地日历已陈旧", str(stale.warnings))
        self.assertEqual(stale.document.timestamps[0].value, BASE)

    async def test_query_background_and_manual_share_singleflight(self):
        self.setup_services([feed()])
        original = self.http.fetch
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(request):
            entered.set()
            await release.wait()
            return await original(request)

        self.http.fetch = delayed
        first = asyncio.create_task(self.query_cn())
        await entered.wait()
        others = [asyncio.create_task(self.query_cn(refresh=True)) for _ in range(5)]
        background = asyncio.create_task(self.sync())
        release.set()
        results = await asyncio.gather(first, *others, background)
        self.assertTrue(all(r.status is ResultStatus.SUCCESS for r in results[:-1]))
        self.assertEqual(len(self.http.requests), 1)

    async def test_manual_cooldown_is_persistent_and_boundary_is_inclusive(self):
        self.setup_services([feed("Old"), feed("New")])
        await self.query_cn()
        restarted = CalendarSnapshots(self.services, clock=lambda: self.now)
        self.query = CalendarQuery(
            self.services, clock=lambda: self.now, snapshots=restarted
        )
        self.now += timedelta(seconds=MANUAL_COOLDOWN_SECONDS - 1)
        limited = await self.query_cn(refresh=True)
        self.assertIn("刷新间隔为5分钟", str(limited.warnings))
        self.assertEqual(len(self.http.requests), 1)
        self.now += timedelta(seconds=1)
        refreshed = await self.query_cn(refresh=True)
        self.assertIn("New", repr(refreshed.document))
        self.assertEqual(len(self.http.requests), 2)

    async def test_next_due_survives_restart(self):
        self.setup_services([feed(), feed("Later")])
        await self.sync()
        self.store = CalendarSnapshots(self.services, clock=lambda: self.now)
        self.now += timedelta(hours=6) - timedelta(seconds=1)
        await self.sync()
        self.assertEqual(len(self.http.requests), 1)
        self.now += timedelta(seconds=1)
        await self.sync()
        self.assertEqual(len(self.http.requests), 2)

    async def test_manual_refresh_does_not_postpone_background_anchor(self):
        self.setup_services([feed("Initial"), feed("Manual"), feed("Scheduled")])
        await self.sync()
        self.now += timedelta(hours=1)
        await self.query_cn(refresh=True)
        self.now = BASE + timedelta(hours=6)
        await self.sync()
        self.assertEqual(len(self.http.requests), 3)
        self.assertIn("Scheduled", repr((await self.query_cn()).document))

    async def test_interval_change_reuses_anchor_and_preserves_success_time(self):
        self.setup_services([feed(), feed("Later")])
        await self.sync()
        self.now += timedelta(hours=6)
        await self.store.synchronize(_invocation(), "cn", interval_seconds=86400)
        self.assertEqual(len(self.http.requests), 1)
        self.assertEqual((await self.query_cn()).document.timestamps[0].value, BASE)
        self.now += timedelta(hours=18)
        await self.store.synchronize(_invocation(), "cn", interval_seconds=86400)
        self.assertEqual(len(self.http.requests), 2)

    async def test_failure_retains_same_source_complete_snapshot(self):
        self.setup_services(
            [
                feed("Keep"),
                SourceHttpError("timeout"),
                SourceHttpError("upstream_error"),
            ]
        )
        await self.sync()
        self.now += timedelta(hours=6)
        await self.sync()
        result = await self.query_cn()
        self.assertIs(result.status, ResultStatus.SUCCESS)
        self.assertIn("Keep", repr(result.document))
        self.assertIn("同步未成功", str(result.warnings))
        self.assertEqual(result.document.timestamps[0].value, BASE)
        self.assertEqual(len(self.http.requests), 3)

    async def test_partial_is_separate_and_empty_complete_replaces_full(self):
        partial = _ics(_event("bad", "DTSTART:INVALID", "SUMMARY:Partial"))
        self.setup_services(
            [feed("Keep"), partial, SourceHttpError("upstream_error"), _ics()]
        )
        await self.sync()
        self.now += timedelta(hours=6)
        await self.sync()
        key = source_key("cn", calendar_source_spec("cn", "ff14_calendar_primary"))
        row = self.records.rows[key]
        self.assertIn("complete", row.value)
        self.assertIn("partial", row.value)
        self.assertIn("Keep", repr((await self.query_cn()).document))
        self.now += timedelta(hours=6)
        await self.sync()
        row = self.records.rows[key]
        self.assertNotIn("partial", row.value)
        empty = await self.query_cn()
        self.assertNotIn("Keep", repr(empty.document))
        self.assertIn("当前窗口未返回活动", repr(empty.document))

    async def test_fallback_uses_reserved_budget_and_primary_success_skips_it(self):
        self.setup_services([feed("Fallback")])
        original = self.http.fetch

        async def slow_primary(request):
            if request.source_id == "ff14_calendar_primary":
                self.http.requests.append(request)
                await asyncio.Event().wait()
            return await original(request)

        self.http.fetch = slow_primary
        with mock.patch(
            "modules.ff14.features.calendar_snapshots.SOURCE_TIMEOUT_SECONDS", 0.01
        ):
            await self.sync()
        self.assertEqual(
            [r.source_id for r in self.http.requests],
            ["ff14_calendar_primary", "ff14_calendar_fallback"],
        )
        self.assertIn("Fallback", repr((await self.query_cn()).document))

    async def test_no_cache_failure_retries_are_bounded_across_restart(self):
        self.setup_services(
            [SourceHttpError("timeout"), SourceHttpError("timeout"), feed()]
        )
        self.assertIs((await self.query_cn()).status, ResultStatus.ERROR)
        self.query = CalendarQuery(self.services, clock=lambda: self.now)
        for _ in range(5):
            self.assertIs((await self.query_cn()).status, ResultStatus.ERROR)
        self.assertEqual(len(self.http.requests), 2)
        self.now += timedelta(minutes=5)
        self.assertIs((await self.query_cn()).status, ResultStatus.SUCCESS)
        self.assertEqual(len(self.http.requests), 3)

    async def test_regions_and_url_fingerprints_do_not_reuse_other_data(self):
        self.setup_services([feed("CN"), feed("GLOBAL"), feed("NEW URL")])
        await self.sync()
        await self.sync(region="global")
        self.assertIn("CN", repr((await self.query_cn()).document))
        result = await self.query.invoke(_invocation(), {"region": "global"})
        self.assertIn("GLOBAL", repr(result.document))

        def changed(region, source):
            spec = calendar_source_spec(region, source)
            return replace(spec, url=spec.url + "?revision=2") if spec else None

        with mock.patch(
            "modules.ff14.features.calendar_snapshots.calendar_source_spec",
            side_effect=changed,
        ):
            result = await self.query_cn()
        self.assertIn("NEW URL", repr(result.document))
        self.assertEqual(len(self.records.rows), 2)

    async def test_raw_snapshot_preserves_floating_query_timezone_and_30_day_window(
        self,
    ):
        self.setup_services([feed(floating=True)])
        utc = await self.query_cn(days=30, timezone="UTC")
        tokyo = await self.query_cn(days=30, timezone="Asia/Tokyo")
        self.assertIs(utc.status, ResultStatus.SUCCESS)
        self.assertIs(tokyo.status, ResultStatus.SUCCESS)
        self.assertIn("12:00", repr(tokyo.document))
        self.assertEqual(len(self.http.requests), 1)
        observation = await self.observe()
        self.assertIsNot(observation.completeness, ObservationCompleteness.COMPLETE)
        self.assertEqual(len(self.http.requests), 1)

    async def test_reminders_never_fetch_and_reject_snapshot_over_48_hours(self):
        self.setup_services([feed()])
        missing = await self.observe()
        self.assertIs(missing.completeness, ObservationCompleteness.FAILED)
        self.assertEqual(self.http.requests, [])
        await self.sync()
        self.now += timedelta(hours=48)
        exact = await self.observe()
        self.assertIs(exact.completeness, ObservationCompleteness.COMPLETE)
        self.assertEqual(exact.payload["source_fetched_at"], BASE.isoformat())
        self.now += timedelta(microseconds=1)
        old = await self.observe()
        self.assertIs(old.completeness, ObservationCompleteness.FAILED)
        self.assertEqual(len(self.http.requests), 1)

    async def test_invalid_or_oversize_content_cannot_replace_complete(self):
        self.setup_services(
            [feed("Keep"), b"x" * (MAX_BYTES + 1), SourceHttpError("timeout")]
        )
        await self.sync()
        self.now += timedelta(hours=6)
        await self.sync()
        self.assertIn("Keep", repr((await self.query_cn()).document))
        self.assertTrue(
            all(
                len(str(row.value)) < MAX_BYTES + 5000
                for row in self.records.rows.values()
            )
        )

    async def test_invalid_refresh_parameter_does_not_fetch(self):
        self.setup_services([])
        self.assertIs((await self.query_cn(refresh="true")).status, ResultStatus.ERROR)
        self.assertEqual(self.http.requests, [])
