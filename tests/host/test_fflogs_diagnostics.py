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

    def test_catalog_new_closed_fields_and_two_batches_keep_values_and_dedupe(self):
        evidence = Evidence("fflogs_public_cn")
        metadata = {
            "worldData": {
                "zones": [
                    {
                        "id": 1,
                        "name": "Synthetic Ultimate",
                        "frozen": False,
                        "difficulties": [{"id": 2, "name": "普通", "sizes": [8]}],
                        "partitions": [
                            {"id": 3, "name": "Synthetic period", "default": True}
                        ],
                        "encounters": [
                            {"id": 4, "name": "Synthetic Ultimate", "zone": {"id": 1}}
                        ],
                    }
                ]
            }
        }
        evidence.observe("FF14CharacterMetadata", metadata)
        event = json.loads(self.sent[0].split(" ", 1)[1])
        projected = json.loads(event["payload"])["worldData"]["zones"][0]
        self.assertEqual(projected["difficulties"][0]["sizes"], [8])
        self.assertEqual(projected["partitions"][0]["name"], "Synthetic period")
        self.assertIs(projected["frozen"], False)
        first = {"metric": "rdps", "z0": 1, "d0": 2, "p0": 3}
        second = {"p0": 4, "d0": 2, "z0": 1, "metric": "rdps"}
        evidence.observe(
            "FF14CatalogCharacter", fixture._character()["data"], filters=first
        )
        evidence.observe(
            "FF14CatalogCharacter", fixture._character()["data"], filters=second
        )
        evidence.observe(
            "FF14CatalogCharacter",
            fixture._character()["data"],
            filters=dict(reversed(list(first.items()))),
        )
        self.assertEqual(len(self.sent), 3)

    def test_new_catalog_fields_reject_unknown_private_types_and_filter_budget(self):
        base = {"id": 1, "name": "Synthetic", "difficulties": [], "encounters": []}
        for index, changes in enumerate(
            (
                {"frozen": "PRIVATE"},
                {
                    "partitions": [
                        {
                            "id": 1,
                            "name": "Synthetic",
                            "default": True,
                            "secret": "PRIVATE",
                        }
                    ]
                },
                {
                    "difficulties": [
                        {"id": 1, "name": "Synthetic", "sizes": ["PRIVATE"]}
                    ]
                },
                {"private": "PRIVATE"},
            )
        ):
            body = {"worldData": {"zones": [{**base, **changes}]}}
            self.bridge.emit(
                self.record(
                    self.event(
                        operation="FF14CharacterMetadata",
                        payload=json.dumps(body),
                        filters={"zoneId": index + 1},
                    )
                )
            )
        self.assertEqual(self.sent, [])
        for filters in (
            {"p0": "PRIVATE"},
            {"p0": True},
            {"unknown": 1},
            {f"z{i}": 1 for i in range(41)},
        ):
            self.bridge.emit(
                self.record(self.event(filters=filters, correlation="b" * 24))
            )
        self.assertEqual(self.sent, [])
        filters = {
            "metric": "rdps",
            **{f"{prefix}{i}": i for i in range(12) for prefix in "zdp"},
        }
        self.bridge.emit(self.record(self.event(filters=filters, correlation="b" * 24)))
        self.assertEqual(len(self.sent), 1)

    def test_request_summary_is_closed_and_never_claims_underlying_network_counts(self):
        body = {
            "kind": "module_source_http_fetch",
            "operations": [
                {
                    "operation": "UniversalisWorlds",
                    "attempts": 1,
                    "returned": 1,
                    "failed": 0,
                    "cancelled": 0,
                    "elapsed_ms": 3,
                    "cache": "module_miss",
                }
            ],
            "elapsed_ms": 5,
            "underlying_http_cache": "unknown",
            "host_oauth": "unobserved",
            "network_attempts": "unknown",
        }
        for index, change in enumerate(
            (
                {"url": "PRIVATE"},
                {"network_attempts": 1},
                {"operations": [{**body["operations"][0], "identity": "PRIVATE"}]},
                {"operations": [{**body["operations"][0], "operation": "Arbitrary"}]},
            )
        ):
            self.bridge.emit(
                self.record(
                    self.event(
                        operation="FF14RequestSummary",
                        status=None,
                        filters={"zoneId": index},
                        payload=json.dumps({**body, **change}),
                    )
                )
            )
        self.assertEqual(self.sent, [])
        self.bridge.emit(
            self.record(
                self.event(
                    operation="FF14RequestSummary",
                    status=None,
                    payload=json.dumps(body),
                )
            )
        )
        self.assertEqual(len(self.sent), 1)

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

    def test_three_correlations_only_and_duplicate_init_restore_only_owned_settings(
        self,
    ):
        logger = logging.getLogger(LOGGER_NAME)
        count = len(logger.handlers)
        self.bridge.start()
        self.assertEqual(len(logger.handlers), count)
        for value in "abcd":
            self.bridge.emit(self.record(self.event(correlation=value * 24)))
        self.assertEqual(len(self.sent), 3)
        logger.setLevel(logging.ERROR)
        logger.propagate = True
        self.bridge.close()
        self.assertEqual(logger.level, logging.ERROR)
        self.assertTrue(logger.propagate)
        self.assertEqual(self.bridge._correlations, set())
        self.assertEqual(self.bridge._seen, set())

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
