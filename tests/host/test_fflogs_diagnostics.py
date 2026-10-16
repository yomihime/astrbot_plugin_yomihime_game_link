"""Offline closed logging bridge; actual Host broker acceptance is separate."""

from __future__ import annotations

import json
import logging
import unittest

from ygl_test_subject.adapters.astrbot.fflogs_diagnostics import FFLogsDiagnostics
from ygl_test_subject.modules.ff14.features.fflogs_evidence import (
    LOGGER_NAME,
    MARKER,
    Evidence,
)

from tests.modules.ff14 import test_fflogs as fixture


class DiagnosticBridgeTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.bridge = FFLogsDiagnostics(self.sent.append)
        self.bridge.start()
        self.addCleanup(self.bridge.close)

    def record(self, event, *, logger=LOGGER_NAME):
        return logging.LogRecord(
            logger,
            logging.INFO,
            __file__,
            1,
            MARKER + " %s",
            (json.dumps(event),),
            None,
        )

    def event(self, **changes):
        return {
            "v": 1,
            "correlation": "a" * 24,
            "source": "fflogs_public_cn",
            "operation": "FF14PublicCharacter",
            "time": "2026-10-16T00:00:00+00:00",
            "status": 200,
            "cached": False,
            "filters": {},
            "chunk": 0,
            "chunks": 1,
            "truncated": False,
            "payload": '{"characterData":{"character":null}}',
            **changes,
        }

    def test_real_module_emitter_forwards_only_closed_safe_score(self):
        Evidence("fflogs_public_cn").observe(
            "FF14PublicCharacter", fixture._character()["data"]
        )
        self.assertEqual(len(self.sent), 1)
        event = json.loads(self.sent[0].split(" ", 1)[1])
        self.assertEqual(event["source"], "fflogs_public_cn")
        self.assertNotIn("Synthetic Hero", self.sent[0])
        self.assertNotIn("canonicalID", self.sent[0])
        self.assertIn("rankPercent", self.sent[0])

    def test_unknown_module_operation_source_fields_and_private_payload_are_rejected(
        self,
    ):
        events = (
            self.event(source="xivapi_items"),
            self.event(operation="ArbitraryGraphQL"),
            self.event(extra="PRIVATE"),
            self.event(payload='{"characterData":{"character":{"name":"PRIVATE"}}}'),
            self.event(chunks=65),
            self.event(payload="x" * 8193),
        )
        for event in events:
            self.bridge.emit(self.record(event))
        self.bridge.emit(self.record(self.event(), logger="other.module"))
        self.assertEqual(self.sent, [])

    def test_partial_duplicate_and_late_chunks_not_forwarded(self):
        body = self.event()["payload"]
        first = self.event(payload=body[:10], chunks=2)
        self.bridge.emit(self.record(first))
        self.bridge.emit(self.record(first))
        self.assertEqual(self.sent, [])
        self.bridge.emit(self.record(self.event(payload=body[10:], chunks=2, chunk=1)))
        self.assertEqual(len(self.sent), 2)
        self.bridge.close()
        self.bridge.emit(self.record(self.event()))
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.bridge._pending, {})

    def test_closed_bridge_does_not_emit_into_root_logging(self):
        self.bridge.close()
        with self.assertNoLogs(logging.getLogger(), logging.INFO):
            Evidence("fflogs_public_cn").observe(
                "FF14PublicCharacter", fixture._character()["data"]
            )

    def test_two_correlations_only_and_duplicate_init_restore_only_owned_settings(self):
        logger = logging.getLogger(LOGGER_NAME)
        count = len(logger.handlers)
        self.bridge.start()
        self.assertEqual(len(logger.handlers), count)
        for value in "abc":
            self.bridge.emit(self.record(self.event(correlation=value * 24)))
        self.assertEqual(len(self.sent), 2)
        logger.setLevel(logging.ERROR)
        logger.propagate = True
        self.bridge.close()
        self.assertEqual(logger.level, logging.ERROR)
        self.assertTrue(logger.propagate)

    def test_truncated_is_explicit_and_malformed_source_exception_is_never_logged(self):
        self.bridge.emit(self.record(self.event(truncated=True, payload="{}")))
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(json.loads(self.sent[0].split(" ", 1)[1])["truncated"])
        record = self.record(self.event(correlation="b" * 24))
        record.exc_info = (ValueError, ValueError("PRIVATE"), None)
        self.bridge.emit(record)
        self.assertEqual(len(self.sent), 1)

    def test_bridge_global_budget_reports_limit_once_and_clears_pending(self):
        self.bridge._chunks = 63
        self.bridge.emit(self.record(self.event()))
        self.bridge.emit(self.record(self.event(correlation="b" * 24)))
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(json.loads(self.sent[0].split(" ", 1)[1])["truncated"])
        self.assertTrue(self.bridge._limited)
        self.assertEqual(self.bridge._pending, {})
