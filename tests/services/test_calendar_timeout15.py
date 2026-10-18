"""Calendar deadlines use real asyncio cancellation with a controlled clock."""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import threading
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.core.invocation_budget import freeze_execution_timeouts
from ygl_test_subject.infrastructure.http import SourceHttpService
from ygl_test_subject.infrastructure.http_transport import AioHttpTransport
from ygl_test_subject.infrastructure.sqlite.database import SQLiteUnitOfWork
from ygl_test_subject.infrastructure.sync_diagnostics import SyncDiagnostics

from modules.ff14.features.calendar_snapshots import SOURCE_TIMEOUT_SECONDS
from tests.integration import test_ff14_runtime as integration_fixture
from tests.modules.ff14 import test_calendar_snapshots as snapshots_fixture
from tests.services import test_background_scheduler as background_fixture
from tests.services.test_http_transport import _ResponseContext, _Session
from yomihime_game_link_sdk.declarations import SourceDeclaration
from yomihime_game_link_sdk.services import HttpResponse
from yomihime_game_link_sdk.subscriptions import ObservationCompleteness


class ControlledLoopClock:
    """Advance the actual loop timer heap, without shortening product budgets."""

    def __enter__(self):
        self.loop = asyncio.get_running_loop()
        self.value = self.loop.time()
        self.patch = patch.object(self.loop, "time", self)
        self.patch.start()
        return self

    def __exit__(self, *args):
        self.patch.stop()

    def __call__(self):
        return self.value

    async def advance(self, seconds):
        self.value += seconds
        await settle()


async def settle():
    # Each timer/timeout/cancellation/result needs its own real loop turn.
    for _ in range(30):
        await asyncio.sleep(0)


async def cancel_task(task):
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


class _GatedTransport:
    def __init__(self, clock, responses=None):
        self.clock = clock
        self.calls = []
        self.gates = {
            name: asyncio.Event()
            for name in ("ff14_calendar_primary", "ff14_calendar_fallback")
        }
        self.responses = responses or {}
        self.cancelled = []

    async def request(self, request):
        self.calls.append((request, self.clock()))
        try:
            await self.gates[request.source_id].wait()
        except asyncio.CancelledError:
            self.cancelled.append(request.source_id)
            raise
        return self.responses.get(
            request.source_id,
            HttpResponse(
                200, {"content-type": "text/calendar"}, snapshots_fixture.feed()
            ),
        )


class CalendarTimeout15Tests(unittest.IsolatedAsyncioTestCase):
    def calendar(self, clock, transport):
        self.assertEqual(SOURCE_TIMEOUT_SECONDS, 15)
        manifest = json.loads(
            (
                Path(__file__).resolve().parents[2]
                / "modules/ff14/yomihime.manifest.json"
            ).read_bytes()
        )
        declared = {s["source_id"]: s for s in manifest["modules"][0]["sources"]}
        sources = []
        for name in ("ff14_calendar_primary", "ff14_calendar_fallback"):
            self.assertEqual(declared[name]["timeout_seconds"], 15)
            sources.append(
                SourceDeclaration(name, declared[name]["host"], timeout_seconds=15)
            )
        fixture = snapshots_fixture.CalendarSnapshotsTests()
        fixture.setup_services([])
        fixture.scopes.bound.http = SourceHttpService(sources, transport, clock=clock)
        return fixture

    def spawn(self, work):
        task = asyncio.create_task(work)
        self.addAsyncCleanup(cancel_task, task)
        return task

    async def test_primary_after_old_three_seconds_persists_and_hot_query_is_zero_http(
        self,
    ):
        with ControlledLoopClock() as clock:
            transport = _GatedTransport(clock)
            fixture = self.calendar(clock, transport)
            task = self.spawn(fixture.sync())
            await settle()
            self.assertEqual(len(transport.calls), 1)
            await clock.advance(13)
            self.assertFalse(task.done())
            transport.gates["ff14_calendar_primary"].set()
            await settle()
            self.assertTrue(task.done())
            await task
            result = await fixture.query_cn()
            self.assertIn("Local event", repr(result.document))
            self.assertEqual(
                [r.source_id for r, _ in transport.calls], ["ff14_calendar_primary"]
            )
            primary = fixture.records.rows["cn:ff14_calendar_primary"].value
            self.assertEqual(primary["last_status"], "complete")
            self.assertEqual(
                primary["complete"]["raw"].encode(), snapshots_fixture.feed()
            )
            self.assertEqual(transport.cancelled, [])

    async def test_primary_expires_at_fifteen_and_fallback_keeps_full_independent_budget(
        self,
    ):
        with ControlledLoopClock() as clock:
            transport = _GatedTransport(clock)
            fixture = self.calendar(clock, transport)
            task = self.spawn(fixture.sync())
            await settle()
            start = clock()
            # Windows timers may fire one loop clock resolution early. Stay
            # outside that resolution, then cross the actual 15s boundary.
            await clock.advance(14)
            self.assertEqual(len(transport.calls), 1)
            self.assertFalse(task.done())
            await clock.advance(1.01)
            self.assertEqual(
                [r.source_id for r, _ in transport.calls],
                ["ff14_calendar_primary", "ff14_calendar_fallback"],
            )
            fallback, entered = transport.calls[1]
            self.assertAlmostEqual(entered - start, 15.01)
            self.assertAlmostEqual(fallback.timeout_seconds, 15)
            await clock.advance(14)
            self.assertFalse(task.done())
            transport.gates["ff14_calendar_fallback"].set()
            await settle()
            self.assertTrue(task.done())
            await task
            self.assertEqual(transport.cancelled, ["ff14_calendar_primary"])
            self.assertEqual(
                fixture.records.rows["cn:ff14_calendar_fallback"].value["last_status"],
                "complete",
            )
            self.assertEqual(
                fixture.records.rows["cn:ff14_calendar_primary"].value["last_status"],
                "failed",
            )
            self.assertIn("Local event", repr((await fixture.query_cn()).document))

    async def test_full_fetch_budget_includes_queue_before_transport(self):
        with ControlledLoopClock() as clock:
            transport = _GatedTransport(clock)
            transport.gates["ff14_calendar_fallback"].set()
            fixture = self.calendar(clock, transport)
            http = fixture.scopes.bound.http
            # Occupy every SourceHttpService slot; no transport can enter.
            for _ in range(16):
                await http._global_semaphore.acquire()
            task = self.spawn(fixture.sync())
            await settle()
            await clock.advance(15.01)
            self.assertEqual(transport.calls, [])
            self.assertFalse(task.done())
            for _ in range(16):
                http._global_semaphore.release()
            await settle()
            await task
            self.assertEqual(
                [r.source_id for r, _ in transport.calls], ["ff14_calendar_fallback"]
            )
            self.assertEqual(
                fixture.records.rows["cn:ff14_calendar_primary"].value["last_status"],
                "failed",
            )

    async def test_full_body_after_headers_is_pending_until_read_and_closes_on_timeout(
        self,
    ):
        with ControlledLoopClock() as clock:
            response = _ResponseContext(
                chunks=(snapshots_fixture.feed(),), gate=asyncio.Event()
            )
            response.response.headers = {"Content-Type": "text/calendar"}
            session = _Session(response)
            transport = AioHttpTransport()
            transport._session = session
            self.addAsyncCleanup(transport.close)
            fixture = self.calendar(clock, transport)
            task = self.spawn(fixture.sync())
            await settle()
            self.assertTrue(response.response.content.started.is_set())
            await clock.advance(13)
            self.assertFalse(task.done())
            self.assertFalse(response.exited)
            response.response.content.gate.set()
            await settle()
            await task
            self.assertTrue(response.exited)
            self.assertEqual(len(session.calls), 1)
            self.assertAlmostEqual(session.calls[0][1]["timeout"].total, 15)
            self.assertEqual(
                fixture.records.rows["cn:ff14_calendar_primary"].value["last_status"],
                "complete",
            )
            # A new cold store makes headers available but never completes body.
            blocked = _ResponseContext(
                chunks=(b"BEGIN:VCALENDAR",), gate=asyncio.Event()
            )
            blocked.response.headers = {"Content-Type": "text/calendar"}
            session.context = blocked
            other = self.calendar(clock, transport)
            task = self.spawn(other.sync())
            await settle()
            await clock.advance(15.01)
            self.assertTrue(blocked.exited)
            self.assertFalse(task.done())
            await clock.advance(15.01)
            await settle()
            self.assertTrue(task.done())
            await task
            self.assertEqual(len(session.calls), 3)
            self.assertNotIn(
                "complete", other.records.rows["cn:ff14_calendar_primary"].value
            )
            self.assertNotIn(
                "complete", other.records.rows["cn:ff14_calendar_fallback"].value
            )
            await transport.close()
            self.assertTrue(transport.closed)
            self.assertEqual(session.close_count, 1)

    async def test_503_does_not_retry_and_fallback_success_or_double_failure_are_distinct(
        self,
    ):
        for fallback_status in (200, 503):
            with (
                self.subTest(fallback_status=fallback_status),
                ControlledLoopClock() as clock,
            ):
                transport = _GatedTransport(
                    clock,
                    {
                        "ff14_calendar_primary": HttpResponse(503, {}, b""),
                        "ff14_calendar_fallback": HttpResponse(
                            fallback_status, {}, snapshots_fixture.feed()
                        ),
                    },
                )
                for gate in transport.gates.values():
                    gate.set()
                fixture = self.calendar(clock, transport)
                await fixture.sync()
                self.assertEqual(
                    [r.source_id for r, _ in transport.calls],
                    ["ff14_calendar_primary", "ff14_calendar_fallback"],
                )
                self.assertEqual(
                    fixture.records.rows["cn:ff14_calendar_fallback"].value[
                        "last_status"
                    ],
                    "complete" if fallback_status == 200 else "failed",
                )

    async def test_outer_cancellation_drains_fetch_and_never_commits_late_response(
        self,
    ):
        with ControlledLoopClock() as clock:
            transport = _GatedTransport(clock)
            fixture = self.calendar(clock, transport)
            diagnostics = SyncDiagnostics()
            diagnostics.activate("a" * 32, "web_public")
            self.addCleanup(diagnostics.close)
            task = self.spawn(fixture.sync())
            await settle()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            before = repr(fixture.records.rows)
            for gate in transport.gates.values():
                gate.set()
            await clock.advance(45)
            self.assertEqual(repr(fixture.records.rows), before)
            self.assertEqual(transport.cancelled, ["ff14_calendar_primary"])
            self.assertEqual(len(transport.calls), 1)
            trace = diagnostics.snapshot()["attempts"][-1]
            self.assertTrue(any(e.get("code") == "cancelled" for e in trace["events"]))
            self.assertFalse(
                any(e["stage"] == "records_commit" for e in trace["events"])
            )


async def close_background(fixture):
    scheduler = getattr(fixture, "scheduler", None)
    if scheduler is not None:
        active = tuple(scheduler._active.values())
        for task in active:
            task.cancel()
        await asyncio.gather(*active, return_exceptions=True)
    if hasattr(fixture, "database"):
        await fixture.database.executor.close()
    if hasattr(fixture, "directory"):
        fixture.directory.cleanup()


class BackgroundTimeout15Tests(unittest.IsolatedAsyncioTestCase):
    async def background(self, *, declared=True):
        fixture = background_fixture.BackgroundSchedulerTests()
        self.addAsyncCleanup(close_background, fixture)
        await fixture.asyncSetUp()
        self.mono = [100.0]
        fixture.scheduler.monotonic_clock = lambda: self.mono[0]
        fixture.issuer._clock = fixture.scheduler.monotonic_clock
        fixture.scheduler.collector_timeouts = freeze_execution_timeouts(
            {("pkg/pricing", "prices"): 45} if declared else None
        )
        return fixture

    async def rows(self, fixture):
        return await fixture.database.executor.run_read(
            lambda unit: (
                tuple(
                    tuple(r)
                    for r in unit.execute(
                        "SELECT observation_id,completeness FROM b04_observations ORDER BY observation_id"
                    )
                ),
                tuple(
                    r[0]
                    for r in unit.execute(
                        "SELECT observation_id FROM b04_collection_jobs ORDER BY job_key"
                    )
                ),
                unit.execute(
                    "SELECT COUNT(*) FROM b04_collection_jobs WHERE lease_token IS NOT NULL"
                ).fetchone()[0],
            )
        )

    async def test_declared_lease55_and_remaining_wait_deducts_setup_default_stays_five(
        self,
    ):
        for declared in (True, False):
            with self.subTest(declared=declared):
                fixture = await self.background(declared=declared)
                candidate = (await fixture.candidates())[0]
                original_bind = fixture.binder.bind
                original_wait = asyncio.wait_for
                waits = []

                async def bind(view):
                    self.mono[0] += 7 if declared else 1
                    return await original_bind(view)

                async def wait(work, timeout):
                    waits.append(timeout)
                    return await original_wait(work, timeout)

                fixture.binder.bind = bind
                with patch(
                    "ygl_test_subject.services.scheduler.asyncio.wait_for",
                    side_effect=wait,
                ):
                    result = await fixture.scheduler.run_due_job(candidate)
                self.assertTrue(result.committed)
                self.assertEqual(waits[0], 38 if declared else 4)
                self.assertEqual(
                    result.lease.expires_at - fixture.now,
                    timedelta(seconds=55 if declared else 30),
                )
                self.assertEqual(
                    fixture.binder.invocations[0].deadline, 145 if declared else 105
                )
                self.assertEqual(
                    (await self.rows(fixture))[0], (("obs-1", "complete"),)
                )

    async def test_declared_singleflight_concurrent_workers_do_not_reenter(self):
        fixture = await self.background()
        candidate = (await fixture.candidates())[0]
        fixture.collector.finish.clear()
        first = asyncio.create_task(fixture.scheduler.run_due_job(candidate))
        self.addAsyncCleanup(cancel_task, first)
        await asyncio.wait_for(fixture.collector.started.wait(), 5)
        second = asyncio.create_task(fixture.scheduler.run_due_job(candidate))
        self.addAsyncCleanup(cancel_task, second)
        await settle()
        self.assertEqual(fixture.collector.calls, 1)
        fixture.collector.finish.set()
        results = await asyncio.gather(first, second)
        self.assertIs(results[0], results[1])
        self.assertEqual(len((await self.rows(fixture))[0]), 1)
        self.assertFalse(fixture.scheduler._active)
        self.assertFalse(fixture.lifecycle.admission._leases)

    async def test_real_commit_boundary_rejects_expiry_cancel_disable_and_old_lease(
        self,
    ):
        for mode in ("deadline", "cancel", "disable", "expired_lease"):
            for completeness in (
                ObservationCompleteness.COMPLETE,
                ObservationCompleteness.PARTIAL,
            ):
                with self.subTest(mode=mode, completeness=completeness):
                    fixture = await self.background()
                    fixture.collector.completeness = completeness
                    candidate = (await fixture.candidates())[0]
                    entered, release = asyncio.Event(), threading.Event()
                    self.addCleanup(release.set)
                    loop = asyncio.get_running_loop()
                    original = SQLiteUnitOfWork.commit_sync
                    intercepted = []

                    def commit(unit):
                        # This barrier is before product guard acquisition and
                        # physical COMMIT, after actual uncommitted SQL writes.
                        if unit._commit_guards and not intercepted:
                            intercepted.append(True)
                            loop.call_soon_threadsafe(entered.set)
                            if not release.wait(20):
                                raise AssertionError(
                                    "test COMMIT barrier was not released"
                                )
                        return original(unit)

                    with patch.object(SQLiteUnitOfWork, "commit_sync", commit):
                        task = asyncio.create_task(
                            fixture.scheduler.run_due_job(candidate)
                        )
                        self.addAsyncCleanup(cancel_task, task)
                        try:
                            await asyncio.wait_for(entered.wait(), 5)
                            self.assertEqual(
                                fixture.now,
                                background_fixture.datetime(
                                    2026, 1, 1, tzinfo=background_fixture.UTC
                                ),
                            )
                            if mode == "deadline":
                                self.mono[0] = (
                                    145.01  # UTC lease is still valid for 55s.
                                )
                            elif mode == "cancel":
                                self.assertTrue(
                                    await fixture.scheduler.cancel_local(candidate.key)
                                )
                                await settle()
                                self.assertFalse(
                                    task.done(), "accepted worker IO must drain"
                                )
                            elif mode == "disable":
                                stopping = asyncio.create_task(
                                    fixture.lifecycle.stop("pkg/pricing", timeout=5)
                                )
                                self.addAsyncCleanup(cancel_task, stopping)
                                await settle()
                                self.assertFalse(
                                    stopping.done(),
                                    "stop must drain accepted IO after closing admission",
                                )
                            else:
                                fixture.now += timedelta(seconds=56)
                            release.set()
                            if mode in ("cancel", "disable"):
                                with self.assertRaises(asyncio.CancelledError):
                                    await asyncio.wait_for(task, 5)
                                if mode == "disable":
                                    await asyncio.wait_for(stopping, 5)
                            else:
                                await asyncio.wait_for(task, 5)
                        finally:
                            release.set()
                            await cancel_task(task)
                    observations, pointers, active = await self.rows(fixture)
                    self.assertFalse(
                        any(state != "failed" for _, state in observations)
                    )
                    self.assertTrue(all(value is None for value in pointers))
                    if mode == "deadline":
                        self.assertEqual(
                            len(observations),
                            1,
                            "safe failure bookkeeping may persist under live lease",
                        )
                    else:
                        self.assertEqual(observations, ())
                    self.assertEqual(active, 0)
                    self.assertEqual(fixture.database.executor._outstanding, 0)
                    self.assertFalse(fixture.scheduler._active)
                    self.assertFalse(fixture.lifecycle.admission._leases)

    async def test_cancelling_a_shielded_waiter_keeps_exact_shared_worker_owned(self):
        fixture = await self.background()
        candidate = (await fixture.candidates())[0]
        fixture.collector.finish.clear()
        waiter = asyncio.create_task(fixture.scheduler.run_due_job(candidate))
        self.addAsyncCleanup(cancel_task, waiter)
        await asyncio.wait_for(fixture.collector.started.wait(), 5)
        workers = tuple(fixture.scheduler._active.values())
        self.assertEqual(len(workers), 1)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertFalse(workers[0].done())
        self.assertEqual(tuple(fixture.scheduler._active.values()), workers)
        fixture.collector.finish.set()
        result = await workers[0]
        self.assertTrue(result.committed)
        self.assertFalse(fixture.scheduler._active)
        self.assertEqual(len((await self.rows(fixture))[0]), 1)

    async def test_setup_exhaustion_does_not_start_a_fresh_full_wait(self):
        fixture = await self.background()
        original = fixture.binder.bind

        async def bind(view):
            self.mono[0] += 45.01
            return await original(view)

        fixture.binder.bind = bind
        result = await fixture.scheduler.run_due_job((await fixture.candidates())[0])
        self.assertTrue(result.failed)
        self.assertEqual(fixture.collector.calls, 0)
        self.assertEqual((await self.rows(fixture))[0][0][1], "failed")

    async def test_replaced_expired_token_cannot_commit_over_fresh_claim(self):
        from yomihime_game_link_sdk.subscriptions import Observation

        fixture = await self.background()
        candidate = (await fixture.candidates())[0]
        old = await fixture.claim(candidate)
        fixture.now += timedelta(seconds=31)
        fresh = await fixture.claim(candidate)
        self.assertIsNotNone(fresh)
        self.assertNotEqual(old.token, fresh.token)
        late = Observation(
            "old-token",
            candidate.key,
            1,
            None,
            fixture.now,
            ObservationCompleteness.COMPLETE,
            (),
            {"value": 1},
        )
        self.assertFalse(await fixture.repository.commit_observation(old, late))
        self.assertEqual((await self.rows(fixture))[0], ())
        self.assertTrue(await fixture.repository.is_current(fresh, now=fixture.now))
        await fixture.repository.release(fresh)

    async def test_safe_failed_actual_commit_requires_live_lease_and_never_advances_lastgood(
        self,
    ):
        from ygl_test_subject.core.task_scope import ScopeDeadlineExceeded

        for expiry_wins in (False, True):
            with self.subTest(expiry_wins=expiry_wins):
                fixture = await self.background()
                candidate = (await fixture.candidates())[0]
                start = fixture.now
                entered, release = asyncio.Event(), threading.Event()
                self.addCleanup(release.set)
                loop = asyncio.get_running_loop()
                original_commit = SQLiteUnitOfWork.commit_sync
                original_repository_commit = (
                    fixture.repository.commit_observation_with_evaluations
                )
                intercepted = []

                async def collect(*_args):
                    self.mono[0] += 45.01
                    raise ScopeDeadlineExceeded("controlled normal-output deadline")

                async def repository_commit(lease, commit, **kwargs):
                    self.assertIs(
                        commit.observation.completeness, ObservationCompleteness.FAILED
                    )
                    self.assertEqual(lease.expires_at - start, timedelta(seconds=55))
                    # Entry check is legal. Only the later physical COMMIT
                    # crosses lease expiry, independently of normal deadline.
                    fixture.now = start + timedelta(seconds=54.9)
                    return await original_repository_commit(lease, commit, **kwargs)

                def commit(unit):
                    failed = unit.execute(
                        "SELECT COUNT(*) FROM b04_observations WHERE completeness='failed'"
                    ).fetchone()[0]
                    if failed and not intercepted:
                        intercepted.append(True)
                        loop.call_soon_threadsafe(entered.set)
                        if not release.wait(20):
                            raise AssertionError(
                                "safe-failure test barrier was not released"
                            )
                    return original_commit(unit)

                fixture.collector.collect = collect
                fixture.repository.commit_observation_with_evaluations = (
                    repository_commit
                )
                with patch.object(SQLiteUnitOfWork, "commit_sync", commit):
                    task = asyncio.create_task(fixture.scheduler.run_due_job(candidate))
                    self.addAsyncCleanup(cancel_task, task)
                    try:
                        await asyncio.wait_for(entered.wait(), 5)
                        fixture.now = start + timedelta(
                            seconds=55.1 if expiry_wins else 54.95
                        )
                        release.set()
                        result = await asyncio.wait_for(task, 5)
                    finally:
                        release.set()
                        await cancel_task(task)
                observations, pointers, active = await self.rows(fixture)
                self.assertEqual(result.committed, not expiry_wins)
                self.assertEqual(len(observations), 0 if expiry_wins else 1)
                if observations:
                    self.assertEqual(observations[0][1], "failed")
                self.assertTrue(all(value is None for value in pointers))
                self.assertEqual(active, 0)
                if expiry_wins:
                    due = await fixture.database.executor.run_read(
                        lambda unit: tuple(
                            r[0]
                            for r in unit.execute(
                                "SELECT due_at FROM b04_collection_jobs ORDER BY job_key"
                            )
                        )
                    )
                    self.assertTrue(
                        all(value == candidate.due_at.isoformat() for value in due)
                    )
                self.assertEqual(fixture.database.executor._outstanding, 0)
                self.assertFalse(fixture.scheduler._active)


async def close_integration(fixture):
    for runtime in reversed(getattr(fixture, "_runtimes", ())):
        if not runtime.closed:
            if not await runtime.close(timeout=2):
                raise AssertionError("real Core failed normal fixture close")
        if runtime.database.executor._outstanding:
            raise AssertionError("SQLite worker still owns accepted jobs")
    temporary = getattr(fixture, "_temporary", None)
    if temporary is not None:
        temporary.cleanup()


class CalendarRealCoreTimeout15Tests(unittest.IsolatedAsyncioTestCase):
    async def core(self):
        fixture = integration_fixture.FF14CalendarRuntimeIntegrationTests()
        # Keep even failed setup inside this batch's registered TEMP tree.
        temporary = tempfile.TemporaryDirectory(prefix="timeout15-core-parent-")
        self.addCleanup(temporary.cleanup)
        self.addAsyncCleanup(close_integration, fixture)
        self.mono = [100.0]
        original = integration_fixture.CoreRuntime

        def create(**kwargs):
            return original(
                **kwargs,
                capability_timeouts={("ff14/ff14", "ff14.calendar.query"): 45},
                collector_timeouts={("ff14/ff14", "ff14.calendar.sync"): 45},
                monotonic_clock=lambda: self.mono[0],
            )

        with (
            patch.object(integration_fixture, "ROOT", Path(temporary.name)),
            patch.object(integration_fixture, "CoreRuntime", side_effect=create),
        ):
            await fixture.asyncSetUp()
        return fixture

    async def candidate(self, fixture):
        fixture.clock.advance(timedelta(hours=6))
        jobs = await fixture.runtime.b04_repositories.scheduler.list_due_jobs(
            now=fixture.clock(), limit=10
        )
        return next(job for job in jobs if job.key.parameters.values["region"] == "cn")

    async def test_real_core_slow_primary_and_full_fallback_persist_matching_observation(
        self,
    ):
        for mode in ("slow_primary", "primary_timeout"):
            with self.subTest(mode=mode):
                fixture = await self.core()
                candidate = await self.candidate(fixture)
                entered = {
                    name: asyncio.Event()
                    for name in ("ff14_calendar_primary", "ff14_calendar_fallback")
                }
                gates = {name: asyncio.Event() for name in entered}
                original = fixture.transport.request
                calls = []

                async def request(request):
                    calls.append(request)
                    entered[request.source_id].set()
                    await gates[request.source_id].wait()
                    return await original(request)

                fixture.transport.request = request
                with ControlledLoopClock() as clock:
                    self.mono[0] = clock()
                    task = asyncio.create_task(
                        fixture.runtime.scheduler.run_due_job(candidate)
                    )
                    self.addAsyncCleanup(cancel_task, task)
                    try:
                        # Let actual SQLite claim/read callbacks finish before
                        # moving time; no fake database latency is introduced.
                        await asyncio.wait_for(
                            entered["ff14_calendar_primary"].wait(), 5
                        )
                        if mode == "primary_timeout":
                            self.mono[0] += 15.01
                            await clock.advance(15.01)
                            await asyncio.wait_for(
                                entered["ff14_calendar_fallback"].wait(), 5
                            )
                            self.mono[0] += 14
                            await clock.advance(14)
                            self.assertFalse(task.done())
                            gates["ff14_calendar_fallback"].set()
                        else:
                            self.mono[0] += 13
                            await clock.advance(13)
                            self.assertFalse(task.done())
                            gates["ff14_calendar_primary"].set()
                        result = await task
                    finally:
                        for gate in gates.values():
                            gate.set()
                        await cancel_task(task)
                self.assertTrue(result.committed)
                self.assertFalse(result.failed)
                self.assertIs(
                    result.observation.completeness, ObservationCompleteness.COMPLETE
                )
                expected_source = (
                    "ff14_calendar_fallback"
                    if mode == "primary_timeout"
                    else "ff14_calendar_primary"
                )
                self.assertEqual(
                    [call.source_id for call in calls],
                    ["ff14_calendar_primary", "ff14_calendar_fallback"]
                    if mode == "primary_timeout"
                    else ["ff14_calendar_primary"],
                )
                self.assertEqual(
                    result.lease.expires_at - result.observation.collected_at,
                    timedelta(seconds=55),
                )
                (
                    observations,
                    records,
                ) = await fixture.runtime.database.executor.run_read(
                    lambda unit: (
                        tuple(
                            tuple(row)
                            for row in unit.execute(
                                "SELECT completeness,payload_json FROM b04_observations WHERE observation_id=?",
                                (result.observation.observation_id,),
                            )
                        ),
                        tuple(
                            row[0]
                            for row in unit.execute(
                                "SELECT payload_json FROM module_records WHERE collection='calendar_snapshots' AND record_key=?",
                                ("cn:" + expected_source,),
                            )
                        ),
                    )
                )
                self.assertEqual(observations[0][0], "complete")
                payload, snapshot = (
                    json.loads(observations[0][1]),
                    json.loads(records[0]),
                )
                self.assertEqual(snapshot["last_status"], "complete")
                self.assertEqual(snapshot["source_id"], expected_source)
                self.assertEqual(
                    snapshot["complete"]["version"],
                    hashlib.sha256(fixture.transport.calendar_ics).hexdigest(),
                )
                self.assertEqual(payload["region"], "cn")
                self.assertEqual(
                    payload["checked_at"], result.observation.collected_at.isoformat()
                )
                self.assertEqual(
                    payload["source_fetched_at"], snapshot["complete"]["fetched_at"]
                )
                self.assertIsNone(result.observation.source_observed_at)
                self.assertNotIn("snapshot_refresh_failed", payload["warnings"])
                self.assertEqual(
                    snapshot["complete"]["raw"].encode(), fixture.transport.calendar_ics
                )
                count = len(calls)
                outcome = await fixture._command("alice", "calendar", {"region": "cn"})
                self.assertEqual(outcome.result.status.value, "success")
                self.assertEqual(len(calls), count)
                self.assertFalse(fixture.runtime.scheduler._active)

    async def test_real_core_cancellation_drains_source_without_late_records_or_observation(
        self,
    ):
        fixture = await self.core()
        candidate = await self.candidate(fixture)
        fixture.transport.clock_ready.clear()
        fixture.transport.request_started.clear()
        task = asyncio.create_task(fixture.runtime.scheduler.run_due_job(candidate))
        self.addAsyncCleanup(cancel_task, task)

        async def state():
            return await fixture.runtime.database.executor.run_read(
                lambda unit: (
                    tuple(
                        tuple(row)
                        for row in unit.execute(
                            "SELECT record_key,revision,payload_json FROM module_records WHERE collection='calendar_snapshots' ORDER BY record_key"
                        )
                    ),
                    tuple(
                        tuple(row)
                        for row in unit.execute(
                            "SELECT observation_id,completeness FROM b04_observations ORDER BY observation_id"
                        )
                    ),
                )
            )

        try:
            await asyncio.wait_for(fixture.transport.request_started.wait(), 5)
            before = await state()
            self.assertTrue(await fixture.runtime.scheduler.cancel_local(candidate.key))
            with self.assertRaises(asyncio.CancelledError):
                await task
            fixture.transport.clock_ready.set()
            await settle()
            self.assertEqual(await state(), before)
            self.assertFalse(fixture.runtime.scheduler._active)
            self.assertEqual(fixture.runtime.database.executor._outstanding, 0)
        finally:
            fixture.transport.clock_ready.set()
            await cancel_task(task)
