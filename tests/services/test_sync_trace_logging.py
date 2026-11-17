"""Bounded terminal DEBUG traces complement single safe failure warnings."""

import json
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ygl_test_subject.adapters.astrbot.diagnostics import forward_sync_trace
from ygl_test_subject.infrastructure.sync_diagnostics import (
    MAX_EVENTS,
    SyncDiagnostics,
    emit,
)

SECRET = "private https://secret.invalid/?token=secret /private/file"


class SyncTraceLoggingTests(unittest.TestCase):
    def run_trace(self, diagnostics, *, public=True, final="completed"):
        token = diagnostics.activate("a" * 32, "scheduler", public=public)
        try:
            emit("core_start", budget_ms=1000, body=SECRET)
            emit("bind", url=SECRET)
            emit("collector_start", headers={"Authorization": SECRET})
            emit("http_end", source="public_source", code="timeout", http_status=504)
            for _ in range(100):
                emit("network_attempt", message=SECRET)
            emit("core_final", code=final, observation_completeness="failed")
            emit("core_final", code=final, observation_completeness="failed")
        finally:
            diagnostics.reset(token)

    def test_debug_trace_is_bounded_redacted_and_single_with_warning(self):
        traces, warnings = [], []
        diagnostics = SyncDiagnostics(
            trace_sink=traces.append, warning_sink=warnings.append
        )
        self.run_trace(diagnostics)
        self.assertEqual(len(traces), 1)
        self.assertEqual(len(warnings), 1)
        self.assertLessEqual(len(traces[0]["events"]), MAX_EVENTS)
        self.assertGreater(traces[0]["dropped_events"], 0)
        self.assertEqual(traces[0]["events"][-1]["stage"], "core_final")
        self.assertEqual(warnings[0]["failure_stage"], "http_end")
        self.assertNotIn(SECRET, json.dumps(traces + warnings))
        self.assertIn("bind", [e["stage"] for e in traces[0]["events"]])

    def test_disabled_debug_does_not_copy_or_emit_trace(self):
        sink = Mock()
        diagnostics = SyncDiagnostics(trace_sink=sink, trace_enabled=lambda: False)
        with patch(
            "ygl_test_subject.infrastructure.sync_diagnostics.copy.deepcopy",
            side_effect=AssertionError("disabled debug allocated a trace"),
        ):
            self.run_trace(diagnostics)
        sink.assert_not_called()

    def test_private_diagnostics_do_not_enter_debug_or_warning_sinks(self):
        sink = Mock()
        diagnostics = SyncDiagnostics(trace_sink=sink, warning_sink=sink)
        self.run_trace(diagnostics, public=False)
        sink.assert_not_called()
        self.assertEqual(diagnostics.snapshot()["attempts"], [])

    def test_cancellation_has_debug_trace_but_no_warning(self):
        traces, warnings = [], []
        diagnostics = SyncDiagnostics(
            trace_sink=traces.append, warning_sink=warnings.append
        )
        self.run_trace(diagnostics, final="cancelled")
        self.assertEqual(len(traces), 1)
        self.assertEqual(warnings, [])

    def test_trace_sink_failure_does_not_change_execution(self):
        diagnostics = SyncDiagnostics(trace_sink=Mock(side_effect=RuntimeError(SECRET)))
        self.run_trace(diagnostics)
        self.assertTrue(diagnostics.snapshot()["attempts"][0]["terminal"])

    def test_adapter_respects_debug_level_before_serialization(self):
        logger = SimpleNamespace(isEnabledFor=Mock(return_value=False), debug=Mock())
        forward_sync_trace({"unserializable": object()}, logger=logger)
        logger.isEnabledFor.assert_called_with(logging.DEBUG)
        logger.debug.assert_not_called()
        logger.isEnabledFor.return_value = True
        forward_sync_trace({"trace": "a" * 32, "events": []}, logger=logger)
        self.assertTrue(logger.debug.call_args.args[0].startswith("GAME_LINK_TRACE "))

    def test_partial_warning_requires_actual_http_failure_and_deduplicates(self):
        for status, http_failed, final, expected in [
            ("partial_success", True, "completed", 1),
            ("partial_success", False, "completed", 0),
            ("success", True, "completed", 0),
            ("error", False, "completed", 0),
            ("partial_success", True, "cancelled", 0),
        ]:
            with self.subTest(status=status, http_failed=http_failed, final=final):
                warnings = []
                diagnostics = SyncDiagnostics(warning_sink=warnings.append)
                token = diagnostics.activate("a" * 32, "command")
                try:
                    emit("core_start", budget_ms=1000)
                    emit("handler_start")
                    if http_failed:
                        emit("http_end", source="public_source", code="timeout")
                    emit("core_final", code=final, capability_status=status)
                    emit("core_final", code=final, capability_status=status)
                    self.assertEqual(len(warnings), expected)
                    if expected:
                        self.assertEqual(
                            warnings[0]["category"], "partial_upstream_failure"
                        )
                finally:
                    diagnostics.reset(token)
                    diagnostics.close()


class TraceLogicalIdentityTests(unittest.TestCase):
    def test_bound_logical_ids_appear_in_trace_and_fallback_before_http(self):
        for origin, extra in [
            ("command", {"capability_id": "ff14.logs.character"}),
            ("web_public", {"capability_id": "ff14.logs.character"}),
            ("scheduler", {"collector_id": "news.cn"}),
        ]:
            warnings, traces = [], []
            diagnostics = SyncDiagnostics(
                warning_sink=warnings.append, trace_sink=traces.append
            )
            token = diagnostics.activate(
                "a" * 32, origin, module_id="ff14/ff14", **extra
            )
            try:
                emit("core_start", budget_ms=1000)
                emit("bind")
                emit("core_failure", code="error")
                emit("core_final", code="error")
                for event in (warnings[0], traces[0]):
                    self.assertEqual(event["module_id"], "ff14/ff14")
                    for key, value in extra.items():
                        self.assertEqual(event[key], value)
                    self.assertNotIn("parameters", event)
                    self.assertNotIn("actor_id", event)
                    self.assertNotIn("conversation_id", event)
            finally:
                diagnostics.reset(token)
                diagnostics.close()

    def test_invalid_or_mixed_identity_is_not_logged(self):
        for identity in [
            {"module_id": SECRET, "capability_id": "safe"},
            {"module_id": "ff14/ff14", "capability_id": SECRET},
            {"module_id": "ff14/ff14", "capability_id": "x" * 97},
            {"module_id": "ff14/ff14", "collector_id": "news.cn"},
            {"module_id": "ff14/../../private", "capability_id": "safe"},
            {
                "module_id": "ff14/ff14",
                "capability_id": "safe",
                "collector_id": "news.cn",
            },
        ]:
            traces = []
            diagnostics = SyncDiagnostics(trace_sink=traces.append)
            token = diagnostics.activate("a" * 32, "command", **identity)
            try:
                emit("core_final", code="completed")
                self.assertNotIn("module_id", traces[0])
                self.assertNotIn("capability_id", traces[0])
                self.assertNotIn("collector_id", traces[0])
                self.assertNotIn(SECRET, json.dumps(traces))
            finally:
                diagnostics.reset(token)
                diagnostics.close()


class LiveLogicalIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_gateway_supplies_declaration_ids_on_handler_failure(self):
        from unittest.mock import AsyncMock

        from tests.core import test_invocation as fixture_module

        fixture = fixture_module.GatewayTests()
        fixture.setUp()
        handler, view = await fixture._ready()
        handler.invoke = AsyncMock(side_effect=RuntimeError(SECRET))
        warnings, traces = [], []
        diagnostics = SyncDiagnostics(
            warning_sink=warnings.append, trace_sink=traces.append
        )
        gateway = fixture._gateway()
        gateway._sync_diagnostics = diagnostics
        try:
            await gateway.invoke_command(view, "查询", {"id": "private-query-sentinel"})
            for event in (warnings[0], traces[0]):
                self.assertEqual(event["module_id"], view.module_id)
                self.assertEqual(event["capability_id"], view.capability_id)
                self.assertNotIn("collector_id", event)
            self.assertNotIn("private-query-sentinel", json.dumps(warnings + traces))
            self.assertNotIn(SECRET, json.dumps(warnings + traces))
        finally:
            fixture.issuer.release(view)
            await fixture.lifecycle.stop(view.module_id)
            diagnostics.close()

    async def test_real_scheduler_supplies_declaration_ids_on_bind_failure(self):
        from unittest.mock import AsyncMock

        from tests.services import test_background_scheduler as fixture_module

        fixture = fixture_module.BackgroundSchedulerTests()
        await fixture.asyncSetUp()
        warnings, traces = [], []
        diagnostics = SyncDiagnostics(
            warning_sink=warnings.append, trace_sink=traces.append
        )
        fixture.scheduler.diagnostics = diagnostics
        fixture.binder.bind = AsyncMock(side_effect=RuntimeError(SECRET))
        try:
            candidate = (await fixture.candidates())[0]
            await fixture.scheduler.run_due_job(candidate)
            for event in (warnings[0], traces[0]):
                self.assertEqual(event["module_id"], candidate.key.module_id)
                self.assertEqual(event["collector_id"], candidate.key.collector_id)
                self.assertNotIn("capability_id", event)
            self.assertEqual(warnings[0]["failure_stage"], "bind")
            self.assertNotIn(SECRET, json.dumps(warnings + traces))
        finally:
            diagnostics.close()
            await fixture.asyncTearDown()

    async def test_real_public_web_supplies_declaration_ids_on_handler_failure(self):
        from unittest.mock import AsyncMock

        from tests.core import test_invocation as fixture_module

        fixture = fixture_module.PublicWebGatewayTests()
        handler = SimpleNamespace(invoke=AsyncMock(side_effect=RuntimeError(SECRET)))
        await fixture._ready_web(handler=handler)
        warnings, traces = [], []
        diagnostics = SyncDiagnostics(
            warning_sink=warnings.append, trace_sink=traces.append
        )
        fixture.gateway._sync_diagnostics = diagnostics
        try:
            await fixture._invoke()
            for event in (warnings[0], traces[0]):
                self.assertEqual(event["module_id"], "testpkg/records")
                self.assertEqual(event["capability_id"], "record.query")
                self.assertNotIn("collector_id", event)
            self.assertNotIn(SECRET, json.dumps(warnings + traces))
        finally:
            fixture.gateway.fence_public_web()
            self.assertTrue(await fixture.gateway.drain_public_web(0.5))
            diagnostics.close()
