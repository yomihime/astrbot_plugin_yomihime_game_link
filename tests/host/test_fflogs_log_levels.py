"""Default Host levels, retaining the private INFO wire and sender contract."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.adapters.astrbot.fflogs_diagnostics import (
    MAX_BYTES,
    MAX_CHUNKS,
    OPERATIONS,
    FFLogsDiagnostics,
)
from ygl_test_subject.modules.ff14.features.fflogs_evidence import (
    LOGGER_NAME,
    MARKER,
    Evidence,
    RequestMetrics,
    metadata_payload,
)

from tests.host import test_fflogs_diagnostics as fixture
from tests.modules.ff14 import test_fflogs as source_fixture
from tests.modules.ff14 import test_fflogs_aggregation as ranking_fixture


class HostLogLevelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.logs = []
        api = ModuleType("astrbot.api")
        api.logger = SimpleNamespace(
            **{
                level: lambda message, level=level: self.logs.append((level, message))
                for level in ("debug", "info", "warning", "error", "exception")
            }
        )
        modules = patch.dict(
            sys.modules, {"astrbot": ModuleType("astrbot"), "astrbot.api": api}
        )
        modules.start()
        self.addCleanup(modules.stop)
        self.bridge = FFLogsDiagnostics()
        self.addCleanup(lambda: self.bridge.close())
        self.bridge.start()
        self.helpers = fixture.DiagnosticBridgeTests(methodName="runTest")

    def events(self):
        return [
            (level, json.loads(message.split(" ", 1)[1]))
            for level, message in self.logs
        ]

    @staticmethod
    def request_summary():
        return dict(
            kind="module_source_http_fetch",
            operations=[],
            elapsed_ms=0,
            underlying_http_cache="unknown",
            host_oauth="unobserved",
            network_attempts="unknown",
        )

    def test_default_host_routes_every_safe_operation_without_changing_wire(self):
        metadata = metadata_payload(ranking_fixture.catalog())
        samples = {
            "FF14OutputMetadataShape": (source_fixture._output_schema()["data"], 200),
            "FF14CharacterMetadata": (metadata, 200),
            "FF14OutputMetadata": (source_fixture._output_metadata()["data"], 200),
            **{
                operation: (source_fixture._character()["data"], 200)
                for operation in (
                    "FF14PublicCharacter",
                    "FF14PublicCharacterZone",
                    "FF14CatalogCharacter",
                )
            },
            "FF14MetadataState": ({"state": "verified_catalog"}, None),
            "FF14DutyOrderState": (
                dict(
                    state="complete",
                    complete=True,
                    pages=1,
                    rows=1,
                    matched=1,
                    unmatched=0,
                ),
                None,
            ),
            "FF14RequestSummary": (self.request_summary(), None),
            "FF14HistorySummary": (self.helpers.history_summary(), None),
        }
        evidence = Evidence("fflogs_public_cn")
        for operation, (payload, status) in samples.items():
            evidence.observe(operation, payload, status=status)
        observed = self.events()
        self.assertEqual({event["operation"] for _, event in observed}, OPERATIONS)
        self.assertEqual(self.bridge.level, logging.INFO)
        self.assertEqual(logging.getLogger(LOGGER_NAME).level, logging.INFO)
        for level, event in observed:
            expected = (
                "info"
                if event["operation"] in {"FF14RequestSummary", "FF14HistorySummary"}
                else "debug"
            )
            self.assertEqual(level, expected, event["operation"])
            self.assertFalse(event["truncated"])

    def test_summary_chunks_stay_info_and_ordinary_multichunks_are_debug(self):
        payload, _ = self.helpers.winners()
        row = {
            **payload["winners"][0],
            "spec": "合" * 50,
            "rankPercent_exact": "99." + "0" * 125,
        }
        payload.update(winners=[row] * 40, displayed=40, total=40)
        evidence = Evidence("fflogs_public_cn")
        evidence.observe("FF14HistorySummary", payload, status=None)
        summaries = self.events()
        self.assertGreater(len(summaries), 1)
        self.assertTrue(
            all(
                level == "info" and not event["truncated"] for level, event in summaries
            )
        )
        expected_body = json.loads("".join(event["payload"] for _, event in summaries))
        self.assertEqual(expected_body["winners"], payload["winners"])
        # Valid ordinary chunks are routed only after complete closed validation.
        self.logs.clear()
        event = self.helpers.event()
        safe = event["payload"]
        for index, piece in enumerate((safe[:10], safe[10:])):
            self.bridge.emit(
                self.helpers.record(
                    {**event, "chunk": index, "chunks": 2, "payload": piece}
                )
            )
        self.assertEqual(len(self.logs), 2)
        self.assertTrue(all(level == "debug" for level, _ in self.logs))

    def test_truncated_summary_and_budget_sentinel_are_debug_not_failures(self):
        self.bridge.emit(
            self.helpers.record(
                self.helpers.event(
                    operation="FF14HistorySummary",
                    status=None,
                    truncated=True,
                    payload="{}",
                )
            )
        )
        self.assertEqual([level for level, _ in self.logs], ["debug"])
        self.bridge.close()
        self.bridge.start()
        self.logs.clear()
        self.bridge._chunks = MAX_CHUNKS - 1
        for _ in range(2):
            self.bridge.emit(self.helpers.record(self.helpers.event()))
        self.assertEqual(len(self.logs), 1)
        self.assertEqual(self.events()[0][0], "debug")
        self.assertTrue(self.events()[0][1]["truncated"])
        self.assertEqual(self.bridge._chunks, MAX_CHUNKS)
        self.assertLessEqual(self.bridge._bytes, MAX_BYTES)

    def test_low_budget_still_reserves_two_info_summaries_and_deduplicates(self):
        evidence = Evidence("fflogs_public_cn")
        for index in range(60):
            evidence.observe(
                "FF14PublicCharacter",
                source_fixture._character()["data"],
                filters={"zoneId": index},
            )
        payload, _ = self.helpers.winners()
        for cached in (False, True):
            evidence.observe("FF14HistorySummary", payload, status=None, cached=cached)
            self.bridge.emit(self.helpers.record(self.events()[-1][1]))
        events = self.events()
        summaries = [
            (level, event)
            for level, event in events
            if event["operation"] == "FF14HistorySummary"
        ]
        self.assertEqual(len(summaries), 2)
        self.assertTrue(all(level == "info" for level, _ in summaries))
        self.assertEqual(sum(event["truncated"] for _, event in events), 1)
        self.assertTrue(
            all(
                level == "debug"
                for level, event in events
                if event["operation"] != "FF14HistorySummary"
            )
        )
        self.assertLessEqual(self.bridge._chunks, MAX_CHUNKS)
        self.assertLessEqual(self.bridge._bytes, MAX_BYTES)
        before = len(self.logs)
        self.bridge.close()
        self.bridge.emit(self.helpers.record(self.helpers.event()))
        self.assertEqual(len(self.logs), before)
        self.assertEqual(
            (self.bridge._pending, self.bridge._seen, self.bridge._correlations),
            ({}, set(), set()),
        )

    async def test_empty_records_cache_miss_and_cancellation_never_log_an_error(self):
        evidence = Evidence("fflogs_public_cn")
        evidence.observe("FF14PublicCharacter", {"characterData": {"character": None}})
        metrics = RequestMetrics()
        metrics.evidence = evidence
        metrics.cache("FF14CharacterMetadata", False)

        async def cancelled(request):
            raise asyncio.CancelledError

        request = SimpleNamespace(
            source_id="fflogs_public_cn",
            path="/api/v2/client",
            body=b'{"query":"query FF14PublicCharacter { synthetic }"}',
        )
        with self.assertRaises(asyncio.CancelledError):
            await metrics.wrap(SimpleNamespace(fetch=cancelled)).fetch(request)
        metrics.finish()
        self.assertTrue(all(level in {"debug", "info"} for level, _ in self.logs))
        summary = next(
            event
            for _, event in self.events()
            if event["operation"] == "FF14RequestSummary"
        )
        operations = {
            row["operation"]: row
            for row in json.loads(summary["payload"])["operations"]
        }
        self.assertEqual(operations["FF14CharacterMetadata"]["cache"], "module_miss")
        self.assertEqual(operations["FF14PublicCharacter"]["cancelled"], 1)
        self.assertEqual(operations["FF14PublicCharacter"]["failed"], 0)

    def test_callback_message_only_and_internal_info_filter_remain_exact(self):
        self.bridge.close()
        sent = []
        self.bridge = FFLogsDiagnostics(lambda message: sent.append(message))
        self.bridge.start()
        event = self.helpers.event()
        for level in (logging.DEBUG, logging.WARNING, logging.ERROR):
            record = self.helpers.record(event)
            record.levelno = level
            self.bridge.emit(record)
        self.assertEqual(sent, [])
        self.bridge.emit(self.helpers.record(event))
        self.assertEqual(
            sent,
            [
                MARKER
                + " "
                + json.dumps(event, separators=(",", ":"), ensure_ascii=True)
            ],
        )
        self.assertEqual(self.logs, [])
        self.bridge.close()
        self.bridge.emit(self.helpers.record(event))
        self.assertEqual(len(sent), 1)

    def test_default_logger_redaction_rejection_and_three_correlation_limit(self):
        private = source_fixture._character()["data"]
        private["characterData"]["character"].update(
            name="PRIVATE-ACTOR", token="PRIVATE-TOKEN"
        )
        Evidence("fflogs_public_cn").observe("FF14PublicCharacter", private)
        self.assertNotIn("PRIVATE-", repr(self.logs))
        count = len(self.logs)
        self.bridge.emit(
            self.helpers.record(self.helpers.event(payload='{"actor":"PRIVATE-TOKEN"}'))
        )
        record = self.helpers.record(self.helpers.event())
        record.exc_info = (ValueError, ValueError("PRIVATE-TOKEN"), None)
        self.bridge.emit(record)
        self.assertEqual(len(self.logs), count)
        # Rejected completed payloads still consume the existing admission
        # budget. Test the three-correlation limit on a fresh lifetime.
        self.bridge.close()
        self.bridge.start()
        self.logs.clear()
        for _ in range(4):
            Evidence("fflogs_public_cn").observe(
                "FF14RequestSummary", self.request_summary(), status=None
            )
        self.assertEqual(len(self.bridge._correlations), 3)
        self.assertEqual(sum(level == "info" for level, _ in self.logs), 3)
        self.assertTrue(all(level in {"debug", "info"} for level, _ in self.logs))
