"""Closed metadata, actual Core finals, and deterministic no-network failures."""

from __future__ import annotations

import asyncio
import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.task_scope import ScopeDeadlineExceeded
from ygl_test_subject.infrastructure.http import SourceHttpService
from ygl_test_subject.infrastructure.http_transport import AioHttpTransport
from ygl_test_subject.infrastructure.sync_diagnostics import (
    MAX_ATTEMPTS,
    MAX_EVENTS,
    SyncDiagnostics,
    emit,
    measure_stage,
    record_cache_batch_results,
)
from ygl_test_subject.modules.ff14.features.calendar_diagnostics import DECLARATION
from ygl_test_subject.services.diagnostics import (
    BoundDiagnostics,
    DiagnosticPolicy,
    DiagnosticStore,
)

from tests.core import test_invocation as web_fixture
from tests.modules.ff14 import test_calendar_snapshots as calendar_fixture
from tests.modules.ff14.test_calendar import _ics
from tests.services import test_background_scheduler as background_fixture
from tests.services.test_http_transport import _request, _ResponseContext, _Session
from tests.services.test_source_http import _FakeTransport
from yomihime_game_link_sdk.declarations import SourceDeclaration
from yomihime_game_link_sdk.errors import (
    AccessDenied,
    OperationTimeout,
    ParameterError,
    ServiceUnavailable,
    SourceHttpError,
)
from yomihime_game_link_sdk.results import ErrorCode, ResultStatus
from yomihime_game_link_sdk.services import HttpRequest
from yomihime_game_link_sdk.storage import CacheLookup, CacheLookupStatus

SECRET = "PRIVATE-SENTINEL-https://secret.invalid/?token=secret-C:/private/secret"


class CalendarDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = DiagnosticStore()

    async def asyncTearDown(self):
        self.store.close()

    def calendar(self, responses):
        self.store.close()
        self.store = DiagnosticStore()
        fixture = calendar_fixture.CalendarSnapshotsTests()
        fixture.setup_services(responses)

        async def current():
            pass

        view = SimpleNamespace(
            invocation_id="a" * 32, module_id="ff14/ff14", module_epoch=1
        )
        fixture.scopes.bound.diagnostics = BoundDiagnostics(
            view,
            (validate_contract(DECLARATION),),
            DiagnosticPolicy(frozenset({"CalendarSync"})),
            self.store,
            current,
        )
        return fixture

    def events(self):
        return [event["payload"] for event in self.store.snapshot()]

    def codes(self):
        return [event.get("code") for event in self.events()]

    async def test_complete_and_legal_empty_have_persisted_success_metadata(self):
        for feed in (calendar_fixture.feed(), _ics()):
            fixture = self.calendar([feed])
            await fixture.sync()
            events = self.events()
            parsed = next(
                e for e in events if e["stage"] == "parse" and "completeness" in e
            )
            self.assertEqual(parsed["completeness"], "complete")
            self.assertEqual(parsed["occurrence_count"] > 0, feed != _ics())
            self.assertTrue(
                any(
                    e["stage"] == "records_commit" and e.get("code") == "ok"
                    for e in events
                )
            )
            self.assertFalse(any("network_attempts" in event for event in events))

    async def test_source_deadline_and_http_status_are_distinct(self):
        fixture = self.calendar([])

        async def blocked(_):
            await asyncio.Event().wait()

        fixture.http.fetch = blocked
        with patch(
            "ygl_test_subject.modules.ff14.features.calendar_snapshots.SOURCE_TIMEOUT_SECONDS",
            0.005,
        ):
            await fixture.sync()
        self.assertEqual(self.codes().count("source_deadline"), 2)
        self.assertFalse(any("network_attempts" in event for event in self.events()))
        fixture = self.calendar(
            [SourceHttpError("upstream_error", status_code=503), _ics()]
        )
        await fixture.sync()
        self.assertTrue(
            any(
                e.get("http_status") == 503 and e.get("code") == "upstream_error"
                for e in self.events()
            )
        )

    async def test_invalid_utf8_and_partial_warning_codes_do_not_copy_feed(self):
        fixture = self.calendar([b"\xff", ("bad calendar " + SECRET).encode()])
        await fixture.sync()
        self.assertIn("invalid_utf8", self.codes())
        parsed = next(e for e in self.events() if e.get("completeness") == "partial")
        self.assertIn("MALFORMED_SOURCE", parsed["warnings"])
        self.assertNotIn(SECRET, json.dumps(self.store.snapshot()))

    async def test_records_failure_reports_call_error_not_rollback_and_keeps_exception(
        self,
    ):
        fixture = self.calendar([_ics()])
        collection = await fixture.records.collection("calendar_snapshots")
        error = RuntimeError(SECRET)
        with patch.object(collection, "replace", side_effect=error):
            with self.assertRaises(RuntimeError) as caught:
                await fixture.sync()
        self.assertIs(caught.exception, error)
        self.assertTrue(
            any(
                e["stage"] == "records_commit"
                and e.get("code") == "call_error_commit_unknown"
                for e in self.events()
            )
        )
        self.assertNotIn(SECRET, json.dumps(self.store.snapshot()))

    async def test_cancelled_fetch_propagates_without_committing_result(self):
        fixture = self.calendar([])
        entered = asyncio.Event()

        async def blocked(_):
            entered.set()
            await asyncio.Event().wait()

        fixture.http.fetch = blocked
        task = asyncio.create_task(fixture.sync())
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIn("cancelled", self.codes())
        self.assertFalse(any(e["stage"] == "records_commit" for e in self.events()))

    async def test_sink_exception_does_not_change_success_or_cancellation(self):
        fixture = self.calendar([_ics()])
        with patch.object(self.store, "_sink", side_effect=RuntimeError(SECRET)):
            await fixture.sync()
        fixture = self.calendar([])

        async def cancelled(_):
            raise asyncio.CancelledError()

        fixture.http.fetch = cancelled
        with patch.object(self.store, "_sink", side_effect=asyncio.CancelledError()):
            with self.assertRaises(asyncio.CancelledError):
                await fixture.sync()


class BoundDiagnosticsFailureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.check = AsyncMock()
        self.store = Mock(spec=DiagnosticStore)
        event = validate_contract(DECLARATION)
        self.port = BoundDiagnostics(
            SimpleNamespace(),
            (event,),
            DiagnosticPolicy(frozenset({event.event_id})),
            self.store,
            self.check,
        )

    async def test_optional_store_failure_cannot_hide_revocation_on_recheck(self):
        self.store.record.side_effect = RuntimeError(SECRET)
        self.check.side_effect = [None, AccessDenied("grant_revoked")]

        with self.assertRaises(AccessDenied) as raised:
            await self.port.emit("CalendarSync", {})

        self.assertEqual(raised.exception.code, "grant_revoked")
        self.assertEqual(self.check.await_count, 2)
        self.store.record.assert_called_once()

    async def test_store_control_and_payload_failures_still_propagate(self):
        for failure in (
            AccessDenied(),
            OperationTimeout(),
            ServiceUnavailable(),
            ParameterError(),
            asyncio.CancelledError(),
        ):
            with self.subTest(failure=type(failure).__name__):
                self.check.reset_mock()
                self.store.record.side_effect = failure
                with self.assertRaises(type(failure)):
                    await self.port.emit("CalendarSync", {})
                expected_checks = (
                    1 if isinstance(failure, asyncio.CancelledError) else 2
                )
                self.assertEqual(self.check.await_count, expected_checks)


class SyncDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = SyncDiagnostics()
        self.store.activate("a" * 32, "web_public")

    async def asyncTearDown(self):
        self.store.close()

    def attempt(self):
        return self.store.snapshot()["attempts"][-1]

    def codes(self):
        return [event.get("code") for event in self.attempt()["events"]]

    def test_cache_batch_reports_counts_without_keys_or_values(self):
        emit("core_start")
        missing = CacheLookup(CacheLookupStatus.MISS)
        expired = CacheLookup(CacheLookupStatus.EXPIRED)
        record_cache_batch_results((missing, expired))
        record_cache_batch_results((missing,))
        self.assertEqual(
            self.attempt()["cache_batch_results"], {"miss": 2, "expired": 1}
        )

    def test_overlapping_http_wall_and_cumulative_survive_event_limit(self):
        emit("core_start")
        for _ in range(MAX_EVENTS):
            emit("http_queue")
        with patch(
            "ygl_test_subject.infrastructure.sync_diagnostics.perf_counter_ns",
            side_effect=[0, 4_000_000, 10_000_000, 15_000_000],
        ):
            with measure_stage("http"):
                with self.assertRaises(asyncio.CancelledError):
                    with measure_stage("http"):
                        raise asyncio.CancelledError
        self.assertEqual(
            self.attempt()["stage_timings"]["http"],
            {"count": 2, "wall_us": 15000, "cumulative_us": 21000},
        )

    async def test_queued_request_is_not_counted_and_transport_entry_is_counted(self):
        transport = _FakeTransport()
        service = SourceHttpService(
            (SourceDeclaration("calendar", "example.test", timeout_seconds=0.01),),
            transport,
            max_concurrency=1,
        )
        await service._global_semaphore.acquire()
        try:
            with self.assertRaises(SourceHttpError):
                await service.fetch(HttpRequest("calendar", "/test"))
        finally:
            service._global_semaphore.release()
        self.assertEqual(len(transport.calls), 0)
        self.assertIn("http_deadline", self.codes())
        self.assertEqual(self.attempt()["network_attempts"], 0)

        production = AioHttpTransport()
        session = _Session(_ResponseContext())
        production._session = session
        try:
            await production.request(_request(query=(("secret", SECRET),)))
            self.assertEqual(self.attempt()["network_attempts"], 1)
            timings = self.attempt()["stage_timings"]
            self.assertEqual(timings["http." + _request().source_id]["count"], 1)
            self.assertGreaterEqual(
                timings["http"]["wall_us"],
                timings["http." + _request().source_id]["wall_us"],
            )
            self.assertFalse(session.calls[0][1]["allow_redirects"])
            self.assertTrue(
                any(e.get("http_status") == 200 for e in self.attempt()["events"])
            )
        finally:
            await production.close()
        self.assertNotIn(SECRET, json.dumps(self.store.snapshot()))

    async def test_transport_exception_families_are_closed_and_business_code_unchanged(
        self,
    ):
        for error, code in (
            (aiohttp.ClientConnectorDNSError(None, OSError(1, SECRET)), "dns_error"),
            (
                aiohttp.ClientConnectorCertificateError(None, ValueError(SECRET)),
                "tls_error",
            ),
            (aiohttp.ServerDisconnectedError(SECRET), "connection_closed"),
            (aiohttp.ClientPayloadError(SECRET), "response_payload"),
            (TimeoutError(SECRET), "transport_timeout"),
            (RuntimeError(SECRET), "unknown"),
        ):
            transport = AioHttpTransport()
            with patch.object(transport, "_send", side_effect=error):
                with self.assertRaises(SourceHttpError) as caught:
                    await transport.request(_request())
            self.assertEqual(
                caught.exception.code,
                "timeout" if isinstance(error, TimeoutError) else "transport_failed",
            )
            self.assertEqual(self.attempt()["events"][-1]["code"], code)
            await transport.close()
        self.assertNotIn(SECRET, json.dumps(self.store.snapshot()))

    async def test_real_client_retry_loop_is_two_by_default_and_one_in_controlled_transport(
        self,
    ):
        # The real aiohttp _request loop and public middleware both execute.
        # Only connector/send are injected: this fixture opens no socket.
        for error in (
            aiohttp.ServerDisconnectedError(SECRET),
            aiohttp.ClientOSError(1, SECRET),
        ):
            connection = SimpleNamespace(protocol=Mock(), close=Mock())
            connect = AsyncMock(return_value=connection)
            send = AsyncMock(side_effect=error)
            async with aiohttp.ClientSession(trust_env=False) as baseline:
                with (
                    patch.object(aiohttp.TCPConnector, "connect", connect),
                    patch.object(aiohttp.ClientRequest, "send", send),
                ):
                    with self.assertRaises(type(error)):
                        await baseline.get(
                            "https://example.invalid/test", allow_redirects=False
                        )
            self.assertEqual(send.await_count, 2)
            self.assertTrue(baseline.closed)
            production = AioHttpTransport()
            connect.reset_mock()
            send.reset_mock()
            try:
                with (
                    patch.object(aiohttp.TCPConnector, "connect", connect),
                    patch.object(aiohttp.ClientRequest, "send", send),
                ):
                    with self.assertRaises(SourceHttpError) as caught:
                        await production.request(_request())
                self.assertEqual(caught.exception.code, "transport_failed")
                self.assertEqual(send.await_count, 1)
                self.assertEqual(connect.await_count, 1)
                self.assertNotIn(SECRET, str(caught.exception))
            finally:
                session = production._session
                await production.close()
                self.assertTrue(session.closed)

    async def test_actual_background_core_success_typed_deadline_timeout_cancel_and_commit_error(
        self,
    ):
        for mode in ("success", "deadline", "timeout", "cancel", "commit_error"):
            with self.subTest(mode=mode):
                fixture = background_fixture.BackgroundSchedulerTests()
                await fixture.asyncSetUp()
                try:
                    fixture.scheduler.diagnostics = self.store
                    candidate = (await fixture.candidates())[0]
                    original = fixture.collector.collect
                    entered = asyncio.Event()

                    async def collect(context, parameters, previous):
                        entered.set()
                        if mode == "deadline":
                            raise ScopeDeadlineExceeded(SECRET)
                        if mode in ("timeout", "cancel"):
                            await asyncio.Event().wait()
                        return await original(context, parameters, previous)

                    fixture.collector.collect = collect
                    if mode == "timeout":
                        fixture.scheduler.quotas = replace(
                            fixture.scheduler.quotas, collection_timeout_seconds=0.02
                        )
                    if mode == "commit_error":
                        fixture.repository.commit_observation_with_evaluations = (
                            unittest.mock.AsyncMock(side_effect=RuntimeError(SECRET))
                        )
                    task = asyncio.create_task(fixture.scheduler.run_due_job(candidate))
                    if mode == "cancel":
                        await entered.wait()
                        self.assertTrue(
                            await fixture.scheduler.cancel_local(candidate.key)
                        )
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                    else:
                        result = await task
                        self.assertEqual(result.committed, mode != "commit_error")
                    trace = self.attempt()
                    self.assertEqual(
                        trace["core_final"],
                        {
                            "success": "completed",
                            "deadline": "error",
                            "timeout": "error",
                            "cancel": "cancelled",
                            "commit_error": "not_committed",
                        }[mode],
                    )
                    if mode == "deadline":
                        self.assertEqual(trace["core_failure_code"], "core_deadline")
                    if mode == "timeout":
                        self.assertEqual(
                            trace["core_failure_code"], "collector_timeout"
                        )
                    self.assertEqual(
                        trace["observation_completeness"],
                        None
                        if mode == "cancel"
                        else "complete"
                        if mode == "success"
                        else "failed",
                    )
                    self.assertNotIn(SECRET, json.dumps(self.store.snapshot()))
                finally:
                    await fixture.asyncTearDown()

    async def test_actual_public_gateway_records_core_final_separate_from_result_and_cancellation(
        self,
    ):
        fixture = web_fixture.PublicWebGatewayTests()
        entered = asyncio.Event()

        class Handler(web_fixture._Handler):
            async def invoke(inner, invocation, parameters):
                entered.set()
                return await super().invoke(invocation, parameters)

        handler = await fixture._ready_web(Handler())
        fixture.gateway._sync_diagnostics = self.store
        try:
            result = await fixture._invoke()
            self.assertEqual(result.status, ResultStatus.SUCCESS)
            trace = self.attempt()
            self.assertEqual(trace["origin"], "web_public")
            self.assertEqual(trace["core_final"], "completed")
            self.assertEqual(trace["capability_status"], "success")
            self.assertIsNone(trace["observation_completeness"])
            self.assertGreater(trace["core_budget_ms"], 0)
            entered.clear()
            handler.release = asyncio.Event()
            task = asyncio.create_task(fixture._invoke())
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(self.attempt()["core_final"], "cancelled")
        finally:
            if handler.release is not None:
                handler.release.set()
            fixture.gateway.fence_public_web()
            self.assertTrue(await fixture.gateway.drain_public_web(1))

    async def test_actual_public_gateway_total_deadline_is_explicit_not_inferred_elapsed(
        self,
    ):
        fixture = web_fixture.PublicWebGatewayTests()

        class Handler(web_fixture._Handler):
            async def invoke(inner, invocation, parameters):
                await asyncio.Event().wait()

        await fixture._ready_web(Handler())
        fixture.gateway._sync_diagnostics = self.store
        proof = fixture.proofs.new(
            "testpkg/records",
            "record.query",
            deadline=asyncio.get_running_loop().time() + 0.1,
        )
        try:
            result = await fixture._invoke(proof=proof)
            self.assertEqual(result.status, ResultStatus.ERROR)
            self.assertEqual(self.attempt()["core_final"], "error")
            self.assertEqual(self.attempt()["core_failure_code"], "core_deadline")
            self.assertIsNone(self.attempt()["observation_completeness"])
        finally:
            fixture.gateway.fence_public_web()
            self.assertTrue(await fixture.gateway.drain_public_web(1))

    async def test_publication_staleness_is_quiet_and_deadlines_are_reported(self):
        for mode in ("closed", "proof_revoked", "deadline_before", "deadline_after"):
            with self.subTest(mode=mode):
                fixture = web_fixture.PublicWebGatewayTests()
                await fixture._ready_web()
                warnings = []
                diagnostics = SyncDiagnostics(warning_sink=warnings.append)
                self.addCleanup(diagnostics.close)
                fixture.gateway._sync_diagnostics = diagnostics
                tick = [1000.0]
                fixture.gateway._clock = fixture.fixture.issuer._clock = lambda: tick[0]
                proof = fixture.proofs.new("testpkg/records", "record.query")
                revoke = fixture.proofs.revoke
                create = asyncio.create_task

                def change_before_publication(_task):
                    if mode == "closed":
                        fixture.gateway.fence_public_web()
                    elif mode == "proof_revoked":
                        revoke(proof)
                    elif mode == "deadline_before":
                        tick[0] = 1031.0

                def create_task(work, *, name=None, **kwargs):
                    task = create(work, name=name, **kwargs)
                    if name == "gateway:public_web":
                        task.add_done_callback(change_before_publication)
                    return task

                def revoke_on_cleanup(proof):
                    revoke(proof)
                    if mode == "deadline_after":
                        tick[0] = 1031.0

                fixture.proofs.revoke = revoke_on_cleanup
                try:
                    with patch("asyncio.create_task", create_task):
                        result = await fixture._invoke(proof=proof)
                    self.assertIs(result.error.code, ErrorCode.MODULE_UNAVAILABLE)
                    trace = diagnostics.snapshot()["attempts"][-1]
                    expired = mode.startswith("deadline")
                    self.assertEqual(
                        trace["core_failure_code"],
                        "core_deadline" if expired else "stale",
                    )
                    self.assertEqual(
                        [warning["category"] for warning in warnings],
                        ["core_deadline"] if expired else [],
                    )
                finally:
                    fixture.gateway.fence_public_web()
                    self.assertTrue(await fixture.gateway.drain_public_web(0.5))

    async def test_command_publication_distinguishes_release_from_deadline(self):
        for mode in ("released", "deadline"):
            with self.subTest(mode=mode):
                fixture = web_fixture.GatewayTests()
                fixture.setUp()
                started = asyncio.Event()
                release = asyncio.Event()
                _, view = await fixture._ready(
                    handler=web_fixture._Handler(started=started, release=release)
                )
                now = [asyncio.get_running_loop().time()]
                if mode == "deadline":
                    fixture.issuer.release(view)
                    fixture.issuer._clock = lambda: now[0]
                    view = fixture.issuer.issue(
                        origin=view.origin,
                        module_id=view.module_id,
                        module_epoch=view.module_epoch,
                        registry_revision=view.registry_revision,
                        actor_id=view.actor_id,
                        conversation_id=view.conversation_id,
                        capability_id=view.capability_id,
                        deadline=now[0] + 30,
                    )
                warnings = []
                diagnostics = SyncDiagnostics(warning_sink=warnings.append)
                self.addCleanup(diagnostics.close)
                gateway = fixture._gateway()
                gateway._sync_diagnostics = diagnostics
                pending = asyncio.create_task(
                    gateway.invoke_command(view, "查询", {"id": "x"})
                )
                try:
                    await asyncio.wait_for(started.wait(), 1)
                    if mode == "released":
                        fixture.issuer.release(view)
                    else:
                        now[0] += 31
                    release.set()
                    result = await pending
                    self.assertIs(result.error.code, ErrorCode.MODULE_UNAVAILABLE)
                    trace = diagnostics.snapshot()["attempts"][-1]
                    expired = mode == "deadline"
                    self.assertEqual(
                        trace["core_failure_code"],
                        "core_deadline" if expired else "stale",
                    )
                    self.assertEqual(
                        [warning["category"] for warning in warnings],
                        ["core_deadline"] if expired else [],
                    )
                finally:
                    release.set()
                    if not pending.done():
                        await pending
                    if mode == "deadline":
                        fixture.issuer.release(view)

    async def test_terminal_warning_ignores_stale_and_cancelled_work(self):
        warnings = []
        diagnostics = SyncDiagnostics(warning_sink=warnings.append)
        self.addCleanup(diagnostics.close)
        for number, (runtime_code, final_code, expected_warning) in enumerate(
            (
                ("stale", "error", None),
                ("cancelled", "error", None),
                (None, "cancelled", None),
                (None, "stale", None),
                ("core_deadline", "error", "core_deadline"),
                ("error", "error", "error"),
                (None, "completed", "module_unavailable"),
            )
        ):
            with self.subTest(runtime_code=runtime_code, final_code=final_code):
                trace_id = f"{number:032x}"
                token = diagnostics.activate(trace_id, "command")
                try:
                    warnings.clear()
                    emit("core_start", budget_ms=1000)
                    if runtime_code is not None:
                        emit("core_failure", code=runtime_code)
                    emit(
                        "core_final",
                        code=final_code,
                        capability_status="error",
                        failure_code="module_unavailable",
                    )
                    trace = diagnostics.snapshot()["attempts"][-1]
                    self.assertEqual(trace["invocation_id"], trace_id)
                    self.assertEqual(trace["core_final"], final_code)
                    self.assertEqual(trace["core_failure_code"], runtime_code)
                    self.assertEqual(
                        [row["category"] for row in warnings],
                        [] if expected_warning is None else [expected_warning],
                    )
                finally:
                    diagnostics.reset(token)

    async def test_ring_terminal_public_opt_in_and_runtime_isolated(self):
        emit("core_start", budget_ms=1234)
        for _ in range(1000):
            emit(
                "http_response",
                http_status=200,
                body=SECRET,
                region="cn",
                warnings=[SECRET],
            )
        emit("core_final", code="completed", capability_status="error")
        trace = self.attempt()
        self.assertEqual(len(trace["events"]), MAX_EVENTS)
        self.assertEqual(trace["events"][-1]["stage"], "core_final")
        self.assertEqual(trace["core_budget_ms"], 1234)
        self.assertGreater(trace["dropped_events"], 0)
        self.assertNotIn(SECRET, json.dumps(trace))
        self.assertNotIn("region", json.dumps(trace))
        other = SyncDiagnostics()
        token = other.activate("f" * 32, "command")
        try:
            emit("core_start", budget_ms=10)
            self.assertEqual(len(other.snapshot()["attempts"]), 1)
            self.assertEqual(len(self.store.snapshot()["attempts"]), 1)
        finally:
            other.reset(token)
            other.close()
        token = self.store.activate("e" * 32, "command", public=False)
        emit("core_start", budget_ms=10)
        self.assertEqual(len(self.store.snapshot()["attempts"]), 1)
        self.store.reset(token)
        for number in range(20):
            self.store.activate(f"{number:032x}", "command")
            emit("core_start", budget_ms=10)
        self.assertEqual(len(self.store.snapshot()["attempts"]), MAX_ATTEMPTS)
