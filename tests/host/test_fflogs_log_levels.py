"""Closed failure warnings and debug-only source summaries."""

import asyncio
import json
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.adapters.astrbot.diagnostics import forward_diagnostic
from ygl_test_subject.infrastructure.sync_diagnostics import SyncDiagnostics, emit
from ygl_test_subject.modules.ff14.features.fflogs import FFLogsCharacterLookup
from ygl_test_subject.modules.ff14.features.fflogs_evidence import (
    Evidence,
    RequestMetrics,
)
from ygl_test_subject.modules.ff14.features.fflogs_failures import (
    EVENT,
    STAGE_MESSAGES,
    LookupFailures,
)
from ygl_test_subject.services.diagnostics import (
    BoundDiagnostics,
    DiagnosticPolicy,
    DiagnosticStore,
)
from ygl_test_subject.services.output import OutputService

from tests.host import test_fflogs_diagnostics as bridge_fixture
from tests.modules.ff14 import test_fflogs as ff_fixture
from yomihime_game_link_sdk import ErrorCode, InvocationOrigin, SourceHttpError


class HostLogLevelTests(unittest.IsolatedAsyncioTestCase):
    def failure_bridge(self, *, max_bytes=512 * 1024):
        fixture = bridge_fixture.DiagnosticBridgeTests()
        fixture.setUp()
        self.addCleanup(fixture.store.close)
        warnings, fallback = [], []
        logger = SimpleNamespace(warning=warnings.append)
        store = DiagnosticStore(
            warning_sink=lambda event: forward_diagnostic(
                event, logger=logger, warning=True
            )
        )
        self.addCleanup(store.close)
        sync = SyncDiagnostics(warning_sink=fallback.append)
        token = sync.activate(fixture.view.invocation_id, "command")
        self.addCleanup(sync.reset, token)
        self.addCleanup(sync.close)
        diagnostics = BoundDiagnostics(
            fixture.view,
            fixture.declarations,
            DiagnosticPolicy(
                frozenset({EVENT}), max_bytes=max_bytes, warning_event_ids={EVENT}
            ),
            store,
            fixture.current,
        )
        return diagnostics, warnings, fallback

    async def test_directory_payload_error_warns_with_http_200_and_safe_command_hint(
        self,
    ):
        diagnostics, warnings, fallback = self.failure_bridge(max_bytes=1)
        http = ff_fixture._FakeHttp(
            [
                [{"id": 1, "name": "PRIVATE_WORLD"}],
                [{"name": "PRIVATE_DC", "region": "China", "worlds": [2]}],
            ]
        )
        lookup = FFLogsCharacterLookup(
            ff_fixture._services(http, diagnostics=diagnostics)
        )
        ctx = SimpleNamespace(
            origin=InvocationOrigin.COMMAND,
            module_id="ff14/ff14",
            capability_id="ff14.logs.character",
        )
        result = await lookup.invoke(ctx, {"command": "PRIVATE_WORLD PRIVATE_ROLE"})
        self.assertEqual(result.error.code, ErrorCode.UPSTREAM_ERROR)
        self.assertEqual(len(http.requests), 2)
        emit("core_final", code="completed", failure_code=result.error.code.value)
        self.assertEqual(fallback, [])
        self.assertEqual(len(warnings), 1)
        self.assertNotIn("PRIVATE_", warnings[0])
        event = json.loads(warnings[0].split(" ", 1)[1])
        self.assertEqual(event["trace"], "a" * 32)
        self.assertEqual(event["event_id"], EVENT)
        (row,) = event["payload"]["failures"]
        self.assertEqual(row["source"], "universalis_market")
        self.assertEqual(row["stage"], "realm_directory")
        self.assertEqual(row["category"], "payload_parse")
        self.assertEqual(row["http_status"], 200)
        reviewed = {
            (ctx.module_id, ctx.capability_id, result.error.code): {
                STAGE_MESSAGES["realm_directory"]
            }
        }
        rendered = await OutputService._render(
            SimpleNamespace(_command_error_messages=reviewed), result, invocation=ctx
        )
        self.assertIn(STAGE_MESSAGES["realm_directory"], rendered.text)

    async def test_source_stages_and_auth_attachment_share_one_warning(self):
        diagnostics, warnings, fallback = self.failure_bridge()
        emit(
            "auth_failure",
            source="fflogs_public_cn",
            code="upstream_error",
            http_status=503,
        )
        failures = LookupFailures()
        failures.port = diagnostics
        failures.begin("fflogs_public_cn", "server_directory")
        failures.source_error(SourceHttpError("upstream_error", status_code=503))
        failures.begin("fflogs_public_cn", "ranking_history")
        failures.source_error(SourceHttpError("upstream_error", status_code=403))
        failures.source_error(SourceHttpError("upstream_error", status_code=403))
        await failures.flush()
        await failures.flush()
        self.assertEqual(warnings, [])
        emit("core_final", code="completed", failure_code="upstream_error")
        self.assertEqual(fallback, [])
        self.assertEqual(len(warnings), 1)
        event = json.loads(warnings[0].split(" ", 1)[1])
        self.assertEqual(event["authorization_failure"]["stage"], "auth_failure")
        self.assertEqual(event["authorization_failure"]["http_status"], 503)
        history = event["payload"]["failures"][-1]
        self.assertEqual(
            (
                history["stage"],
                history["category"],
                history["http_status"],
                history["count"],
            ),
            ("ranking_history", "forbidden", 403, 2),
        )

    async def test_module_warning_does_not_log_after_cancellation_or_revocation(self):
        for termination in ("cancelled", "stale"):
            with self.subTest(termination=termination):
                diagnostics, warnings, fallback = self.failure_bridge()
                failures = LookupFailures()
                failures.port = diagnostics
                failures.begin("fflogs_public_cn", "ranking_history")
                failures.source_error(
                    SourceHttpError("upstream_error", status_code=503)
                )
                await failures.flush()
                emit("core_failure", code=termination)
                emit("core_final", code="error", failure_code="upstream_error")
                self.assertEqual((warnings, fallback), ([], []))

    async def test_actual_default_host_sink_is_debug_only_and_generic(self):
        levels = []
        api = ModuleType("astrbot.api")
        api.logger = SimpleNamespace(
            **{
                level: lambda text, level=level: levels.append((level, text))
                for level in ("debug", "info", "warning", "error", "exception")
            }
        )
        event = {
            "owner": "neutral/sample",
            "epoch": 1,
            "trace": "0" * 32,
            "event_id": "Summary",
            "payload": {"state": "empty"},
        }
        with patch.dict(
            sys.modules, {"astrbot": ModuleType("astrbot"), "astrbot.api": api}
        ):
            forward_diagnostic(event)
        self.assertEqual([level for level, _ in levels], ["debug"])
        self.assertEqual(json.loads(levels[0][1].split(" ", 1)[1]), event)

    async def test_request_cancel_and_empty_cache_state_remain_safe_module_facts(self):
        fixture = bridge_fixture.DiagnosticBridgeTests()
        fixture.setUp()
        try:
            evidence = Evidence("fflogs_public_cn", fixture.port)
            evidence.observe(
                "FF14PublicCharacter", {"characterData": {"character": None}}
            )
            metrics = RequestMetrics()
            metrics.evidence = evidence
            metrics.cache("FF14CharacterMetadata", False)

            async def cancelled(request):
                raise asyncio.CancelledError()

            request = SimpleNamespace(
                source_id="fflogs_public_cn",
                path="/api/v2/client",
                body=b'{"query":"query FF14PublicCharacter { synthetic }"}',
            )
            with self.assertRaises(asyncio.CancelledError):
                await metrics.wrap(SimpleNamespace(fetch=cancelled)).fetch(request)
            metrics.finish()
            await evidence.flush()
            summary = next(
                event
                for event in fixture.events()
                if event["event_id"] == "FF14RequestSummary"
            )
            operations = {
                row["operation"]: row
                for row in summary["payload"]["payload"]["operations"]
            }
            self.assertEqual(
                operations["FF14CharacterMetadata"]["cache"], "module_miss"
            )
            self.assertEqual(operations["FF14PublicCharacter"]["cancelled"], 1)
            self.assertEqual(operations["FF14PublicCharacter"]["failed"], 0)
        finally:
            fixture.store.close()
