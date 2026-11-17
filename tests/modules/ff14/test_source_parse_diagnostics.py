"""Only actual source parse failures produce bounded, deduplicated warnings."""

import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.infrastructure.sync_diagnostics import SyncDiagnostics, emit
from ygl_test_subject.modules.ff14.features.item_sources import (
    ItemPayloadError,
    ItemSourceClient,
)
from ygl_test_subject.modules.ff14.features.market import MarketClient
from ygl_test_subject.modules.ff14.features.market_sources import MarketSession
from ygl_test_subject.modules.ff14.features.source_diagnostics import (
    DECLARATION,
    EVENT,
    report_parse_failure,
)
from ygl_test_subject.services.diagnostics import DiagnosticPolicy, DiagnosticStore

from tests.modules.ff14.test_items import FIXTURE, _FakeHttp
from tests.modules.ff14.test_market import (
    DCS,
    NOW,
    WORLDS_PATH,
    FakeCache,
    FakeHttp,
    aggregated,
    catalog,
    query,
)
from tests.modules.ff14.test_news import INVOCATION
from yomihime_game_link_sdk import DiagnosticDeclaration
from yomihime_game_link_sdk.errors import (
    AccessDenied,
    OperationTimeout,
    ServiceUnavailable,
)
from yomihime_game_link_sdk.results import ResultStatus
from yomihime_game_link_sdk.services import HttpResponse
from yomihime_game_link_sdk.storage import CacheEntry

SECRET = "PRIVATE-SENTINEL https://private.invalid/?token=secret"


class SourceParseDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_directory_shape_failure_has_its_own_stage(self):
        from ygl_test_subject.modules.ff14.features.market_sources import (
            MarketPayloadError,
        )

        port = SimpleNamespace(emit=AsyncMock())
        session = MarketSession(
            FakeHttp(lambda request: [] if request.path == WORLDS_PATH else DCS),
            diagnostics=port,
        )
        with self.assertRaises(MarketPayloadError):
            await session.catalog()
        self.assertEqual(port.emit.await_args.args[1]["stage"], "market_directory")
        port.emit.assert_awaited_once()

    async def test_item_stages_emit_only_fixed_fields(self):
        fixture = json.loads(FIXTURE.read_text())
        for stage, responses in (
            ("item_search", [SECRET.encode()]),
            ("item_detail", [{"private": SECRET}]),
            ("item_acquisition", [fixture["xivcdn"]["item_detail"], SECRET.encode()]),
        ):
            with self.subTest(stage=stage):
                port = SimpleNamespace(emit=AsyncMock())
                source = ItemSourceClient(_FakeHttp(responses), diagnostics=port)
                if stage == "item_acquisition":
                    result = await source.lookup(90001)
                    self.assertTrue(result.partial)
                else:
                    with self.assertRaises(ItemPayloadError):
                        if stage == "item_search":
                            await source.search(SECRET)
                        else:
                            await source.lookup(90001)
                name, payload = port.emit.await_args.args
                self.assertEqual(name, EVENT)
                self.assertEqual(set(payload), {"source", "stage", "category"})
                self.assertEqual(payload["stage"], stage)
                self.assertEqual(payload["category"], "payload_parse")
                self.assertNotIn(SECRET, repr(payload))

    async def test_market_json_and_shape_failures_have_correct_stage(self):
        for intent, stage in (
            ("overview", "market_aggregated"),
            ("listings", "market_listings"),
        ):
            for response in (
                HttpResponse(200, {}, SECRET.encode()),
                {"private": SECRET},
            ):
                with self.subTest(intent=intent, response=type(response).__name__):
                    port = SimpleNamespace(emit=AsyncMock())
                    session = MarketSession(
                        FakeHttp(lambda request: response), diagnostics=port
                    )
                    result = await MarketClient(session, catalog()).execute(
                        query(intent=intent)
                    )
                    self.assertIs(result.result.status, ResultStatus.ERROR)
                    name, payload = port.emit.await_args.args
                    self.assertEqual(name, EVENT)
                    self.assertEqual(
                        payload,
                        {
                            "source": "universalis_market",
                            "stage": stage,
                            "category": "payload_parse",
                        },
                    )
                    port.emit.assert_awaited_once()

    async def test_multiple_market_failures_and_core_terminal_emit_one_warning(self):
        warnings = []
        declaration = validate_contract(DECLARATION)
        store = DiagnosticStore(warning_sink=warnings.append)
        policy = DiagnosticPolicy(
            {EVENT}, warning_event_ids={EVENT}, event_limits={EVENT: 1}
        )

        async def record(name, payload):
            store.record(INVOCATION, name, declaration.payload_schema, payload, policy)

        diagnostics = SyncDiagnostics(warning_sink=warnings.append)
        token = diagnostics.activate(INVOCATION.invocation_id, "command")
        try:
            emit("core_start", budget_ms=30000)
            session = MarketSession(
                FakeHttp(lambda request: {}), diagnostics=SimpleNamespace(emit=record)
            )
            result = await MarketClient(session, catalog()).execute(
                query(region="global")
            )
            emit(
                "core_final",
                code="completed",
                capability_status="error",
                failure_code="unparsed",
            )
        finally:
            diagnostics.reset(token)
            diagnostics.close()
            store.close()
        self.assertIs(result.result.status, ResultStatus.ERROR)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["event_id"], EVENT)
        self.assertIs(type(warnings[0]["elapsed_ms"]), int)
        self.assertGreaterEqual(warnings[0]["elapsed_ms"], 0)
        self.assertEqual(set(warnings[0]["payload"]), {"source", "stage", "category"})

    async def test_bad_cache_then_successful_source_does_not_warn(self):
        port = SimpleNamespace(emit=AsyncMock())
        cache = FakeCache()
        session = MarketSession(
            FakeHttp(lambda request: aggregated()),
            cache,
            wall_clock=lambda: NOW,
            diagnostics=port,
        )
        await MarketClient(session, catalog()).execute(query())
        key, entry = next(iter(cache.values.items()))
        payload = dict(entry.payload)
        payload["body"] = {"private": SECRET}
        cache.values[key] = validate_contract(
            CacheEntry(key, payload, entry.expires_at)
        )
        result = await MarketClient(session, catalog()).execute(query())
        self.assertIs(result.result.status, ResultStatus.SUCCESS)
        port.emit.assert_not_awaited()

    async def test_optional_sink_failure_and_control_semantics(self):
        port = SimpleNamespace(emit=AsyncMock(side_effect=RuntimeError(SECRET)))
        with self.assertRaises(ItemPayloadError):
            await ItemSourceClient(_FakeHttp([b"bad json"]), diagnostics=port).search(
                SECRET
            )
        for error in (
            AccessDenied(),
            ServiceUnavailable(),
            OperationTimeout(),
            asyncio.CancelledError(),
        ):
            with self.subTest(error=type(error).__name__):
                port.emit.side_effect = error
                with self.assertRaises(type(error)) as caught:
                    await report_parse_failure(port, "item_search")
                self.assertIs(caught.exception, error)

    def test_installed_schema_and_warning_policy_match(self):
        root = Path(__file__).parents[3] / "modules" / "ff14"
        manifest = json.loads(
            (root / "yomihime.manifest.json").read_text(encoding="utf-8")
        )
        declared = next(
            item
            for item in manifest["modules"][0]["diagnostics"]
            if item["event_id"] == EVENT
        )
        self.assertEqual(
            validate_contract(DiagnosticDeclaration(**declared)),
            validate_contract(DECLARATION),
        )
        policy = json.loads((root / "assembly.json").read_text(encoding="utf-8"))[
            "diagnostics"
        ]
        self.assertIn(EVENT, policy["events"])
        self.assertIn(EVENT, policy["warning_events"])
        self.assertEqual(policy["event_limits"][EVENT], 1)
