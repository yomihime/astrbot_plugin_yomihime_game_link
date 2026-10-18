"""Offline closed logging bridge; actual Host broker acceptance is separate."""

from __future__ import annotations

import json
import logging
import unittest

from ygl_test_subject.adapters.astrbot.fflogs_diagnostics import FFLogsDiagnostics
from ygl_test_subject.modules.ff14.features.fflogs import _assemble_catalog_rankings
from ygl_test_subject.modules.ff14.features.fflogs_catalog import (
    parse_character_metadata,
)
from ygl_test_subject.modules.ff14.features.fflogs_evidence import (
    LOGGER_NAME,
    MARKER,
    Evidence,
    metadata_digest,
    metadata_payload,
    project,
    winner_summary,
)

from tests.modules.ff14 import test_fflogs as fixture
from tests.modules.ff14 import test_fflogs_aggregation as ranking_fixture


class DiagnosticBridgeTests(unittest.TestCase):
    def test_host_only_low_omission_updates_second_multichunk_winner_summary(self):
        payload, _ = self.winners()
        row = {
            **payload["winners"][0],
            "spec": 'diagnostic_low_omitted":false ' + "合" * 50,
            "rankPercent_exact": "99." + "0" * 125,
        }
        payload.update(winners=[row] * 40, displayed=40, total=40)
        first = Evidence("fflogs_public_cn")
        for index in range(47):
            first.observe(
                "FF14PublicCharacter",
                fixture._character()["data"],
                filters={"zoneId": index},
            )
        self.assertFalse(first.low_limited)
        first.observe("FF14HistorySummary", payload, status=None)
        second = Evidence("fflogs_public_cn")
        second.observe("FF14PublicCharacter", fixture._character()["data"])
        self.assertFalse(second.low_limited)
        self.assertTrue(self.bridge._low_limited)
        second.observe("FF14HistorySummary", payload, status=None, cached=True)
        events = [json.loads(message.split(" ", 1)[1]) for message in self.sent]
        summaries = [
            [
                event
                for event in events
                if event["operation"] == "FF14HistorySummary"
                and event["correlation"] == source.correlation
            ]
            for source in (first, second)
        ]
        self.assertGreater(len(summaries[1]), 1)
        bodies = [
            json.loads("".join(event["payload"] for event in chunks))
            for chunks in summaries
        ]
        self.assertFalse(bodies[0]["diagnostic_low_omitted"])
        self.assertTrue(bodies[1]["diagnostic_low_omitted"])
        self.assertEqual(
            {
                key: value
                for key, value in bodies[0].items()
                if key != "diagnostic_low_omitted"
            },
            {
                key: value
                for key, value in bodies[1].items()
                if key != "diagnostic_low_omitted"
            },
        )
        self.assertEqual(
            sum(len(event["payload"]) for event in summaries[1]),
            sum(len(event["payload"]) for event in summaries[0]) - 1,
        )
        for chunks in summaries:
            self.assertEqual(
                [event["chunk"] for event in chunks], list(range(len(chunks)))
            )
            self.assertTrue(
                all(
                    event["chunks"] == len(chunks) and not event["truncated"]
                    for event in chunks
                )
            )
        self.assertLessEqual(self.bridge._chunks, 64)
        self.assertLessEqual(self.bridge._bytes, 512 * 1024)

    def test_host_omission_rewrite_preserves_true_and_rejects_invalid_wire(self):
        payload, _ = self.winners()
        payload["diagnostic_low_omitted"] = True
        self.bridge.emit(
            self.record(
                self.event(
                    operation="FF14HistorySummary",
                    status=None,
                    payload=json.dumps(payload),
                )
            )
        )
        self.assertTrue(
            json.loads(json.loads(self.sent[0].split(" ", 1)[1])["payload"])[
                "diagnostic_low_omitted"
            ]
        )
        self.bridge.close()
        self.bridge.start()
        self.sent.clear()
        self.bridge._low_limited = True
        for changes in ({"diagnostic_low_omitted": 0}, {"actor": "PRIVATE"}):
            self.bridge.emit(
                self.record(
                    self.event(
                        operation="FF14HistorySummary",
                        status=None,
                        payload=json.dumps({**payload, **changes}),
                    )
                )
            )
            self.assertEqual(self.sent, [])
            self.bridge.close()
            self.bridge.start()
            self.bridge._low_limited = True

    def test_host_omission_rewrite_drops_noncanonical_wire_that_would_grow(self):
        payload, _ = self.winners()
        payload.update(diagnostic_low_omitted=False)
        payload["winners"][0]["bestAmount_model"] = 1000000000.0
        wire = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).replace(
            "1000000000.0", "1e9"
        )
        self.bridge._low_limited = True
        self.bridge.emit(
            self.record(
                self.event(operation="FF14HistorySummary", status=None, payload=wire)
            )
        )
        self.assertEqual(self.sent, [])

    def winners(self):
        metadata = ranking_fixture.catalog()
        targets = ranking_fixture.targets(metadata)
        role = _assemble_catalog_rankings(
            ranking_fixture.record(),
            metadata,
            targets,
            {ranking_fixture.key(t): ranking_fixture.selection(t) for t in targets},
        )
        return {**self.history_summary(), **winner_summary(role, metadata)}, metadata

    def test_winner_digest_reconstructs_from_safe_catalog_and_no_private_specs(self):
        payload, metadata = self.winners()
        safe = project("FF14CharacterMetadata", metadata_payload(metadata))
        reconstructed = parse_character_metadata({**safe, "gameData": {"classes": []}})
        self.assertEqual(metadata_digest(metadata), metadata_digest(reconstructed))
        Evidence("fflogs_public_cn").observe("FF14HistorySummary", payload, status=None)
        body = json.loads(json.loads(self.sent[0].split(" ", 1)[1])["payload"])
        self.assertEqual(body["winners"], payload["winners"])
        self.assertEqual(body["displayed"], len(body["winners"]))

    def test_winner_wire_rejects_every_untrusted_field_and_type(self):
        payload, _ = self.winners()
        row = payload["winners"][0]
        bad = {
            "category": "PRIVATE",
            "zone": True,
            "difficulty": 0,
            "requested_partition": "3",
            "partition": -2,
            "encounter": 2147483648,
            "composition": "PRIVATE",
            "echo": False,
            "rankPercent_exact": "NaN",
            "bestAmount_model": float("inf"),
            "spec": "https://private.invalid",
            "totalKills": True,
            "state": "PRIVATE",
            "history_complete": 1,
            "extra": "PRIVATE",
        }
        for key, value in bad.items():
            with self.subTest(key=key):
                self.bridge.close()
                self.bridge.start()
                body = {**payload, "winners": [{**row, key: value}], "displayed": 1}
                self.bridge.emit(
                    self.record(
                        self.event(
                            operation="FF14HistorySummary",
                            status=None,
                            payload=json.dumps(body),
                        )
                    )
                )
                self.assertEqual(self.sent, [])
        for changes in (
            {"winners": [row] * 41, "displayed": 41},
            {"metadata_sha256": "x" * 64},
            {"displayed": True},
            {"total": -1},
            {"complete": 1},
            {"winners": [{**row, "spec": "x" * 81}]},
            {"winners": [{**row, "rankPercent_exact": "1" * 161}]},
            {"winners": [{**row, "rankPercent_exact": "100.1"}]},
            {"winners": [{**row, "bestAmount_model": "3"}]},
        ):
            self.bridge.close()
            self.bridge.start()
            self.bridge.emit(
                self.record(
                    self.event(
                        operation="FF14HistorySummary",
                        status=None,
                        payload=json.dumps({**payload, **changes}),
                    )
                )
            )
            self.assertEqual(self.sent, [])

    def test_forty_winners_and_global_bytes_remain_bounded(self):
        payload, _ = self.winners()
        row = {
            **payload["winners"][0],
            "spec": "合" * 80,
            "rankPercent_exact": "99." + "0" * 125,
        }
        payload.update(winners=[row] * 40, displayed=40, total=40)
        evidence = Evidence("fflogs_public_cn")
        evidence.observe("FF14HistorySummary", payload, status=None)
        events = [json.loads(value.split(" ", 1)[1]) for value in self.sent]
        self.assertGreater(len(events), 1)
        self.assertLessEqual(len(events), 8)
        self.assertEqual(
            json.loads("".join(event["payload"] for event in events))["winners"],
            [row] * 40,
        )
        self.bridge._bytes = 512 * 1024 - 1
        evidence.observe("FF14HistorySummary", payload, status=None, cached=True)
        self.assertTrue(self.bridge._limited)

    def test_low_budget_preserves_two_final_summaries_and_close_clears_all(self):
        payload, _ = self.winners()
        first = Evidence("fflogs_public_cn")
        second = Evidence("fflogs_public_cn")
        for index in range(60):
            first.observe(
                "FF14PublicCharacter",
                fixture._character()["data"],
                filters={"zoneId": index},
            )
        self.assertTrue(first.low_limited)
        first.observe("FF14HistorySummary", payload, status=None)
        for index in range(60):
            second.observe(
                "FF14PublicCharacter",
                fixture._character()["data"],
                filters={"zoneId": index},
            )
        second.observe("FF14HistorySummary", payload, status=None, cached=True)
        summaries = [
            json.loads(value.split(" ", 1)[1])
            for value in self.sent
            if json.loads(value.split(" ", 1)[1])["operation"] == "FF14HistorySummary"
        ]
        self.assertEqual(len(summaries), 2)
        self.assertFalse(any(event["truncated"] for event in summaries))
        self.assertTrue(
            all(
                json.loads(event["payload"])["diagnostic_low_omitted"]
                for event in summaries
            )
        )
        self.assertLessEqual(self.bridge._chunks, 64)
        self.assertLessEqual(self.bridge._bytes, 512 * 1024)
        third = Evidence("fflogs_public_cn")
        third.observe("FF14HistorySummary", payload, status=None)
        self.assertEqual(
            len(
                [
                    value
                    for value in self.sent
                    if '"operation":"FF14HistorySummary"' in value
                ]
            ),
            2,
        )
        count = len(self.sent)
        self.bridge.close()
        first.observe("FF14HistorySummary", payload, status=None)
        self.assertEqual(len(self.sent), count)
        self.assertEqual(
            (self.bridge._chunks, self.bridge._high_chunks, self.bridge._low_chunks),
            (0, 0, 0),
        )
        self.assertEqual(self.bridge._summaries, set())

    def test_duty_state_emitter_forwards_cache_status_without_private_data(self):
        evidence = Evidence("fflogs_public_cn")
        payload = {
            "state": "complete",
            "complete": True,
            "pages": 1,
            "rows": 7,
            "actor": "PRIVATE",
        }
        evidence.observe("FF14DutyOrderState", payload, status=None)
        evidence.observe("FF14DutyOrderState", payload, status=None, cached=True)
        self.assertEqual(len(self.sent), 2)
        events = [json.loads(value.split(" ", 1)[1]) for value in self.sent]
        self.assertEqual([event["cached"] for event in events], [False, True])
        self.assertEqual(
            [json.loads(event["payload"])["state"] for event in events],
            ["complete", "complete"],
        )
        self.assertNotIn("PRIVATE", str(self.sent))

    def test_duty_state_wire_rejects_bad_status_extra_and_unbounded_values(self):
        payload = {
            "state": "complete",
            "complete": True,
            "pages": 1,
            "rows": 7,
            "matched": 7,
            "unmatched": 0,
        }
        bodies = [
            {**payload, "actor": "PRIVATE"},
            {**payload, "pages": 3},
            {**payload, "pages": True},
            {**payload, "rows": 201},
            {**payload, "state": "PRIVATE"},
            {**payload, "complete": 1},
            {**payload, "matched": 10001},
            {**payload, "unmatched": -1},
        ]
        for body in bodies:
            self.bridge.emit(
                self.record(
                    self.event(
                        operation="FF14DutyOrderState",
                        status=None,
                        payload=json.dumps(body),
                    )
                )
            )
        Evidence("fflogs_public_cn").observe("FF14DutyOrderState", payload, status=200)
        self.assertEqual(self.sent, [])

    def history_summary(self):
        return project(
            "FF14HistorySummary",
            {
                "planned": 111,
                "eligible": 111,
                "attempted": 111,
                "fresh_positive": 100,
                "fresh_empty": 11,
                "batches_attempted": 10,
                "max_inflight": 2,
                "history_budget_ms": 83000,
                "invocation_budget_ms": 90000,
                "output_reserve_ms": 5000,
            },
        )

    def test_real_history_emitter_forwards_exact_summary_once_and_deduplicates(self):
        payload = self.history_summary()
        self.assertEqual(len(payload), 22)
        evidence = Evidence("fflogs_public_cn")
        for _ in range(2):
            evidence.observe("FF14HistorySummary", payload, status=None)
        self.assertEqual(len(self.sent), 1)
        event = json.loads(self.sent[0].split(" ", 1)[1])
        self.assertEqual(event["operation"], "FF14HistorySummary")
        self.assertIsNone(event["status"])
        self.assertEqual(json.loads(event["payload"]), payload)
        self.assertNotIn("Synthetic Hero", self.sent[0])
        self.assertNotIn("canonicalID", self.sent[0])

    def test_real_history_emitter_rejects_status_and_strips_untrusted_input(self):
        payload = self.history_summary()
        for status in (200, 99, 600, True, "PRIVATE", {}, []):
            with self.subTest(status=status):
                Evidence("fflogs_public_cn").observe(
                    "FF14HistorySummary", payload, status=status
                )
                self.assertEqual(self.sent, [])
        Evidence("fflogs_public_cn").observe(
            "FF14HistorySummary",
            {
                **payload,
                "actor_id": "PRIVATE",
                "token": "PRIVATE",
                "request_failed": {"nested": "PRIVATE"},
                "attempted": True,
            },
            status=None,
        )
        self.assertEqual(len(self.sent), 1)
        event = json.loads(self.sent[0].split(" ", 1)[1])
        body = json.loads(event["payload"])
        self.assertEqual(set(body), set(payload))
        self.assertEqual(body["request_failed"], 0)
        self.assertEqual(body["attempted"], 0)
        self.assertNotIn("PRIVATE", self.sent[0])

    def test_history_wire_rejects_every_noninteger_or_out_of_range_counter(self):
        payload = self.history_summary()
        for field in payload:
            for value in (True, -1, 2147483648, 1.0, "PRIVATE", None, {}, []):
                with self.subTest(field=field, value=value):
                    self.bridge.close()
                    self.bridge.start()
                    self.bridge.emit(
                        self.record(
                            self.event(
                                operation="FF14HistorySummary",
                                status=None,
                                payload=json.dumps({**payload, field: value}),
                            )
                        )
                    )
                    self.assertEqual(self.sent, [])
        # The exact safe integer boundary remains legal for every field.
        self.bridge.close()
        self.bridge.start()
        maximum = dict.fromkeys(payload, 2147483647)
        Evidence("fflogs_public_global").observe(
            "FF14HistorySummary", maximum, status=None
        )
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(
            json.loads(json.loads(self.sent[0].split(" ", 1)[1])["payload"]), maximum
        )

    def test_history_wire_rejects_missing_extra_private_nested_and_unknown_fields(self):
        payload = self.history_summary()
        missing = dict(payload)
        missing.pop("planned")
        bodies = (
            missing,
            {**payload, "extra": 1},
            {**payload, "actor_id": "PRIVATE"},
            {**payload, "planned": {"nested": 111}},
            {"summary": payload},
            [],
        )
        for body in bodies:
            with self.subTest(body=body):
                self.bridge.close()
                self.bridge.start()
                self.bridge.emit(
                    self.record(
                        self.event(
                            operation="FF14HistorySummary",
                            status=None,
                            payload=json.dumps(body),
                        )
                    )
                )
                self.assertEqual(self.sent, [])

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
