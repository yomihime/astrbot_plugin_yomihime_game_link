"""Typed fact projection fixtures only; no network or model acceptance claims."""

import unittest
from dataclasses import replace
from datetime import timedelta

from ygl_test_subject.modules.ff14.features.market_handler import _execution_facts
from ygl_test_subject.modules.ff14.features.market_models import (
    Listing,
    MarketExecution,
    Quote,
    ScopeData,
    ScopeOutcome,
)
from ygl_test_subject.modules.ff14.features.market_sources import Provenance

from yomihime_sdk.api.display import DisplayDocument, TextBlock
from yomihime_sdk.api.results import CapabilityResult, ErrorCode, ResultStatus

from .test_market import NOW, REGION_IDS, catalog, query


class MarketFactsTests(unittest.TestCase):
    def execution(self, *, intent="min", snapshot=True, partial=False):
        q = query(region="cn", intent=intent)
        quotes = tuple(
            Quote(
                quality,
                price,
                REGION_IDS["China"],
                NOW - timedelta(hours=2),
                price + 9,
                REGION_IDS["China"],
                NOW - timedelta(hours=1),
                11.25,
                2.5,
            )
            for quality, price in (("nq", 100), ("hq", 500))
        )
        outcome = ScopeOutcome(
            "China",
            "China",
            ScopeData(quotes=quotes),
            Provenance(
                "https://universalis.app/synthetic", NOW - timedelta(minutes=20), True
            ),
        )
        return MarketExecution(
            q,
            (outcome,),
            (),
            tuple((r.quality, "China", r) for r in quotes),
            False,
            CapabilityResult(
                "fixture",
                ResultStatus.PARTIAL_SUCCESS if partial else ResultStatus.SUCCESS,
                DisplayDocument("Fixture", "fixture", (TextBlock("fixture"),)),
            ),
            NOW,
            catalog() if snapshot else None,
        )

    def test_canonical_rows_names_quality_source_and_separate_ages(self):
        facts = _execution_facts(self.execution())
        self.assertEqual(facts["fact_projection_version"], 2)
        self.assertEqual([r["price_per_unit"] for r in facts["minimums"]], [100, 500])
        for i, row in enumerate(facts["minimums"]):
            self.assertEqual(row["world_name"], "SyntheticChina")
            self.assertEqual(row["world_name_state"], "verified")
            self.assertEqual(row["world_id"], REGION_IDS["China"])
            self.assertEqual(row["time"]["age_seconds"], 7200)
            self.assertEqual(row["source"]["fetched_age_seconds"], 1200)
            self.assertTrue(row["source"]["cached"])
            self.assertEqual(row["source"]["provider"], "Universalis")
            self.assertIn("非实时", row["limitation"])
            metrics = facts["coverage"][0]["quotes"][i]
            self.assertEqual(metrics["minimum_ref"], f"market.minimums[{i}]")
            self.assertNotIn("minimum", metrics)
            self.assertNotIn("minimum_time", metrics)
            self.assertEqual(metrics["average_sale_price"], 11.25)
        self.assertTrue(facts["coverage_complete"])
        self.assertFalse(facts["limitations"]["complete_world_coverage"])
        self.assertFalse(facts["limitations"]["realtime_availability"])

    def test_missing_catalog_unknown_world_region_and_scope_do_not_guess(self):
        original = self.execution()
        for ex in (
            replace(original, catalog=None),
            replace(
                original,
                query=replace(
                    original.query,
                    scope=replace(original.query.scope, regions=("Japan",)),
                ),
            ),
            replace(
                original,
                query=replace(
                    original.query,
                    scope=replace(original.query.scope, kind="world", target=99999),
                ),
            ),
            replace(
                original,
                query=replace(
                    original.query,
                    scope=replace(original.query.scope, kind="dc", target="different"),
                ),
            ),
        ):
            with self.subTest(scope=ex.query.scope):
                row = _execution_facts(ex)["minimums"][0]
                self.assertIsNone(row["world_name"])
                self.assertEqual(row["world_name_state"], "unknown")
        unknown = replace(original.minimums[0][2], minimum_world=99999)
        row = _execution_facts(replace(original, minimums=(("nq", "China", unknown),)))[
            "minimums"
        ][0]
        self.assertIsNone(row["world_name"])
        self.assertEqual(row["world_id"], 99999)

    def test_nonchampion_and_nonidentical_triplets_keep_metrics(self):
        original = self.execution()
        for change in (
            {"minimum": 101},
            {"minimum_world": 99999},
            {"minimum_uploaded_at": None},
        ):
            different = replace(original.minimums[0][2], **change)
            ex = replace(
                original,
                outcomes=(
                    replace(original.outcomes[0], data=ScopeData(quotes=(different,))),
                ),
            )
            row = _execution_facts(ex)["coverage"][0]["quotes"][0]
            self.assertNotIn("minimum_ref", row)
            self.assertEqual(row["minimum"], different.minimum)
            self.assertEqual(row["minimum_world_id"], different.minimum_world)
        extra = replace(
            original.minimums[0][2], minimum=120, minimum_world=REGION_IDS["Japan"]
        )
        ex = replace(
            original,
            outcomes=original.outcomes
            + (ScopeOutcome("Japan", "Japan", ScopeData(quotes=(extra,))),),
        )
        self.assertEqual(
            _execution_facts(ex)["coverage"][1]["quotes"][0]["minimum"], 120
        )

    def test_partial_failed_empty_missing_null_and_listings(self):
        original = self.execution(partial=True)
        missing = Quote("nq", recent_purchase=99)
        ex = replace(
            original,
            outcomes=(
                replace(
                    original.outcomes[0],
                    data=ScopeData(quotes=(missing,), incomplete=True),
                ),
                ScopeOutcome("Japan", "Japan", code=ErrorCode.UPSTREAM_ERROR),
                ScopeOutcome("Europe", "Europe", ScopeData()),
            ),
            minimums=(),
        )
        facts = _execution_facts(ex)
        self.assertTrue(facts["partial"])
        self.assertFalse(facts["coverage_complete"])
        self.assertEqual(facts["limitations"]["failed_regions"], ["Japan"])
        row = facts["coverage"][0]["quotes"][0]
        self.assertIsNone(row["minimum"])
        self.assertIsNone(row["minimum_time"]["source_time"])
        self.assertEqual(facts["minimums"], [])
        listing = Listing(
            50, 3, False, REGION_IDS["China"], "China", None, NOW - timedelta(days=3), 0
        )
        row = _execution_facts(replace(original, listings=(listing,)))["listings"][0]
        self.assertEqual(row["world_name"], "SyntheticChina")
        self.assertIsNone(row["reviewed"]["age_seconds"])
        self.assertEqual(row["uploaded"]["age_seconds"], 259200)

    def test_explicit_default_and_overview_are_preserved(self):
        for intent in ("min", "overview"):
            ex = self.execution(intent=intent)
            self.assertEqual(_execution_facts(ex)["intent"], intent)
            self.assertEqual(_execution_facts(ex)["scope"]["source"], "explicit")
            q = query(intent=intent)
            self.assertEqual(
                _execution_facts(replace(ex, query=q))["scope"]["source"], "default"
            )


if __name__ == "__main__":
    unittest.main()
