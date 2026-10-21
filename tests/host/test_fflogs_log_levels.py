"""Generic safe forwarding never treats business/absence/cancel as Host errors."""

import asyncio
import json
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.adapters.astrbot.diagnostics import forward_diagnostic

from modules.ff14.features.fflogs_evidence import Evidence, RequestMetrics
from tests.host import test_fflogs_diagnostics as bridge_fixture


class HostLogLevelTests(unittest.IsolatedAsyncioTestCase):
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
