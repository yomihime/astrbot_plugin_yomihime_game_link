"""Synthetic privacy, bounded evidence and source-number precision checks."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

from ygl_test_subject.core.contracts.validation_boundary import validate_contract

from modules.ff14.features.fflogs_catalog import parse_graphql_envelope
from modules.ff14.features.fflogs_chat import character_facts
from modules.ff14.features.fflogs_evidence import (
    MAX_CHUNKS,
    Evidence,
    project,
)
from modules.ff14.features.fflogs_models import parse_public_character
from tests.modules.ff14 import test_fflogs as fixture
from yomihime_game_link_sdk.results import FactDocument


class EvidenceTests(unittest.TestCase):
    def test_duty_state_projection_is_closed_bounded_and_identity_free(self):
        safe = project(
            "FF14DutyOrderState",
            {
                "state": "complete",
                "complete": True,
                "pages": 2,
                "rows": 200,
                "actor": "PRIVATE",
                "url": "https://private.invalid",
            },
        )
        self.assertEqual(
            safe,
            {
                "state": "complete",
                "complete": True,
                "pages": 2,
                "rows": 200,
                "matched": 0,
                "unmatched": 0,
            },
        )
        self.assertEqual(
            project(
                "FF14DutyOrderState", {"state": "PRIVATE", "pages": True, "rows": 201}
            ),
            {
                "state": "unavailable",
                "complete": False,
                "pages": 0,
                "rows": 0,
                "matched": 0,
                "unmatched": 0,
            },
        )

    def test_character_identity_auth_and_report_links_never_survive_projection(self):
        raw = fixture._character()["data"]
        character = raw["characterData"]["character"]
        character.update(
            name="PRIVATE-NAME",
            id=918273,
            server={"name": "PRIVATE-SERVER"},
            token="PRIVATE-TOKEN",
            report="https://private.invalid",
        )
        character["rankings"]["rankings"][0].update(
            private_token="PRIVATE-TOKEN", reportID="PRIVATE-REPORT"
        )
        character["rankings"]["rankings"][0].pop("totalKills", None)
        safe = project("FF14PublicCharacter", raw)
        text = json.dumps(safe)
        for forbidden in ("PRIVATE-", "918273", '"server"', '"canonicalID"'):
            self.assertNotIn(forbidden, text)
        row = safe["characterData"]["character"]["rankings"]["rankings"][0]
        self.assertIn("private_token", row["raw_field_names"])
        self.assertNotIn("totalKills", row["raw_field_names"])
        self.assertFalse(row["raw_field_names_truncated"])
        self.assertIn("rankPercent", text)

    def test_original_json_decimal_is_preserved_before_float_facts_conversion(self):
        raw = json.dumps(fixture._character()).replace("93.5", "99.999999999999999999")
        data = parse_graphql_envelope(raw.encode(), exact_numbers=True)
        row = parse_public_character(data["characterData"]["character"]).rankings[0]
        self.assertEqual(row.rank_percent_exact, Decimal("99.999999999999999999"))
        self.assertEqual(
            project("FF14PublicCharacter", data)["characterData"]["character"][
                "rankings"
            ]["rankings"][0]["rankPercent"],
            "99.999999999999999999",
        )

    def test_exact_fact_supplement_is_sparse_and_20_rows_keep_existing_budget(self):
        character = parse_public_character(
            fixture._character()["data"]["characterData"]["character"]
        )
        server = SimpleNamespace(
            name="Synthetic Server",
            region="CN",
            region_name="China",
            subregion="SyntheticDC",
        )
        plain = character_facts(
            character,
            server,
            "cn",
            "rdps",
            None,
            "https://cn.fflogs.com",
            "2026-10-16T00:00:00Z",
        )
        self.assertNotIn("rank_percent_exact", plain["rankings"][0])
        rows = tuple(
            replace(
                character.rankings[0],
                encounter_id=index + 1,
                rank_percent=100.0,
                rank_percent_exact=Decimal("99.999999999999999999"),
            )
            for index in range(20)
        )
        facts = character_facts(
            replace(character, rankings=rows),
            server,
            "cn",
            "rdps",
            None,
            "https://cn.fflogs.com",
            "2026-10-16T00:00:00Z",
        )
        self.assertEqual(len(facts["rankings"]), 20)
        self.assertEqual(facts["rankings"][0]["rank_percent"], 100.0)
        self.assertEqual(
            facts["rankings"][0]["rank_percent_exact"], "99.999999999999999999"
        )
        validate_contract(FactDocument({"fflogs": facts}))

    def test_schema_absent_partition_is_explicit_and_fields_include_arguments(self):
        raw = fixture._output_schema()["data"]
        safe = project("FF14OutputMetadataShape", raw)
        self.assertIsNone(safe["partitionType"])
        self.assertEqual(project("FF14OutputMetadataShape", safe), safe)
        self.assertIn("type", safe["zoneType"]["fields"][0])
        self.assertIn("args", safe["zoneType"]["fields"][0])

    def test_emitter_hard_limit_and_invalid_payload_cannot_break_business(self):
        evidence = Evidence(
            "fflogs_public_cn", SimpleNamespace(emit=lambda *args: None)
        )
        evidence.chunks = MAX_CHUNKS - 1
        evidence.observe("FF14OutputMetadataShape", {})
        evidence.observe("FF14OutputMetadataShape", {})
        self.assertTrue(evidence.limited)
        self.assertEqual(evidence._pending, [])
        self.assertLessEqual(evidence.chunks, MAX_CHUNKS)
        evidence.observe("FF14OutputMetadataShape", {"zoneType": {"fields": 7}})
