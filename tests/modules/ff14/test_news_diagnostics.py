"""Offline failure diagnostics do not change news baselines or send messages."""

import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.infrastructure.sync_diagnostics import SyncDiagnostics, emit
from ygl_test_subject.modules.ff14.features.diagnostic_declarations import (
    NEWS_FAILURE_DECLARATION,
)
from ygl_test_subject.modules.ff14.features.news import SOURCE, NewsCollector
from ygl_test_subject.services.diagnostics import DiagnosticPolicy, DiagnosticStore

from tests.modules.ff14.test_news import INVOCATION, KEY, NOW, page, row
from yomihime_game_link_sdk import DiagnosticDeclaration
from yomihime_game_link_sdk.errors import (
    AccessDenied,
    OperationTimeout,
    SourceHttpError,
)
from yomihime_game_link_sdk.services import HttpResponse
from yomihime_game_link_sdk.subscriptions import (
    CollectionView,
    NormalizedInput,
    ObservationCompleteness,
)

SECRET = "PRIVATE-SENTINEL https://private.invalid/?token=secret"


class NewsFailureDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self, responses, port, previous=None):
        async def fetch(request):
            response = responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            if isinstance(response, HttpResponse):
                return response
            return HttpResponse(200, {}, json.dumps(response).encode())

        services = SimpleNamespace(
            scopes=SimpleNamespace(
                bind=AsyncMock(
                    return_value=SimpleNamespace(
                        http=SimpleNamespace(fetch=fetch), diagnostics=port
                    )
                )
            )
        )
        return await NewsCollector(services, clock=lambda: NOW).collect(
            CollectionView(KEY, invocation=INVOCATION), NormalizedInput({}), previous
        )

    async def test_failure_categories_stages_and_payloads_are_safe(self):
        for error, category in [
            (TimeoutError(SECRET), "timeout"),
            (OSError(SECRET), "transport_error"),
            (ValueError(SECRET), "payload_parse"),
            (SourceHttpError("upstream_error", status_code=503), "http_error"),
            (SourceHttpError("upstream_error", status_code=429), "rate_limited"),
            (ExceptionGroup(SECRET, [ValueError(SECRET)]), "payload_parse"),
        ]:
            with self.subTest(category=category):
                port = SimpleNamespace(emit=AsyncMock())
                result = await self.collect([error], port)
                self.assertIs(result.completeness, ObservationCompleteness.FAILED)
                name, payload = port.emit.await_args.args
                self.assertEqual(name, "FF14NewsFailure")
                self.assertEqual(payload["category"], category)
                self.assertEqual(payload["stage"], "listing")
                self.assertEqual(payload["source"], SOURCE)
                self.assertNotIn(SECRET, repr(payload))
                self.assertIsNone(result.payload["known_ids"])

    async def test_detail_parse_failure_keeps_status_and_emits_one_real_warning(self):
        warnings = []
        declaration = validate_contract(NEWS_FAILURE_DECLARATION)
        store = DiagnosticStore(warning_sink=warnings.append)
        policy = DiagnosticPolicy(
            {"FF14NewsFailure"}, warning_event_ids={"FF14NewsFailure"}
        )

        async def record(name, payload):
            store.record(INVOCATION, name, declaration.payload_schema, payload, policy)

        listing = page([row(7)])
        baseline = await self.collect(
            [listing, listing], SimpleNamespace(emit=AsyncMock())
        )
        diagnostics = SyncDiagnostics(warning_sink=warnings.append)
        token = diagnostics.activate(INVOCATION.invocation_id, "scheduler")
        try:
            emit("core_start", budget_ms=90000)
            result = await self.collect(
                [listing, listing, HttpResponse(200, {}, b"bad json")],
                SimpleNamespace(emit=record),
                previous=baseline,
            )
            emit("core_final", code="completed", observation_completeness="failed")
        finally:
            diagnostics.reset(token)
            diagnostics.close()
        self.assertIs(result.completeness, ObservationCompleteness.FAILED)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["payload"]["stage"], "detail")
        self.assertEqual(warnings[0]["payload"]["http_status"], 200)
        self.assertEqual(warnings[0]["payload"]["category"], "payload_parse")

    async def test_control_failures_are_not_converted_to_failed_observations(self):
        for error in [
            asyncio.CancelledError(),
            AccessDenied(),
            OperationTimeout(),
            SourceHttpError("cancelled"),
        ]:
            port = SimpleNamespace(emit=AsyncMock())
            with (
                self.subTest(error=type(error).__name__),
                self.assertRaises(type(error)),
            ):
                await self.collect([error], port)
            port.emit.assert_not_awaited()

    async def test_grouped_control_failure_takes_precedence_over_business_errors(self):
        denied = AccessDenied()
        port = SimpleNamespace(emit=AsyncMock())
        with self.assertRaises(AccessDenied) as caught:
            await self.collect(
                [
                    ExceptionGroup(
                        SECRET,
                        [
                            ValueError(SECRET),
                            OperationTimeout(),
                            ExceptionGroup(SECRET, [denied]),
                        ],
                    )
                ],
                port,
            )
        self.assertIs(caught.exception, denied)
        port.emit.assert_not_awaited()

    async def test_optional_telemetry_error_preserves_failure_but_authority_propagates(
        self,
    ):
        port = SimpleNamespace(emit=AsyncMock(side_effect=RuntimeError(SECRET)))
        result = await self.collect([ValueError(SECRET)], port)
        self.assertIs(result.completeness, ObservationCompleteness.FAILED)
        port.emit.side_effect = AccessDenied()
        with self.assertRaises(AccessDenied):
            await self.collect([ValueError(SECRET)], port)

    def test_failed_observation_gets_fallback_warning_but_cancel_is_quiet(self):
        for final, expected in [("completed", 1), ("cancelled", 0), ("stale", 0)]:
            warnings = []
            diagnostics = SyncDiagnostics(warning_sink=warnings.append)
            token = diagnostics.activate("a" * 32, "scheduler")
            try:
                emit("core_start", budget_ms=90000)
                emit("http_end", source=SOURCE, code="timeout")
                emit("core_final", code=final, observation_completeness="failed")
                self.assertEqual(len(warnings), expected)
                if expected:
                    self.assertEqual(warnings[0]["category"], "collection_failed")
                    self.assertEqual(warnings[0]["stage"], "collection")
                    self.assertEqual(
                        warnings[0]["last_http_failure"]["category"], "timeout"
                    )
            finally:
                diagnostics.reset(token)
                diagnostics.close()

    async def test_success_does_not_emit_warning(self):
        port = SimpleNamespace(emit=AsyncMock())
        listing = page([])
        result = await self.collect([listing, listing], port)
        self.assertIs(result.completeness, ObservationCompleteness.COMPLETE)
        port.emit.assert_not_awaited()

    def test_installed_declaration_and_warning_policy_match(self):
        root = Path(__file__).parents[3] / "modules" / "ff14"
        manifest = json.loads(
            (root / "yomihime.manifest.json").read_text(encoding="utf-8")
        )
        declared = next(
            item
            for item in manifest["modules"][0]["diagnostics"]
            if item["event_id"] == "FF14NewsFailure"
        )
        self.assertEqual(
            validate_contract(DiagnosticDeclaration(**declared)),
            validate_contract(NEWS_FAILURE_DECLARATION),
        )
        policy = json.loads((root / "assembly.json").read_text(encoding="utf-8"))[
            "diagnostics"
        ]
        self.assertIn("FF14NewsFailure", policy["events"])
        self.assertIn("FF14NewsFailure", policy["warning_events"])
        self.assertEqual(policy["event_limits"]["FF14NewsFailure"], 1)
