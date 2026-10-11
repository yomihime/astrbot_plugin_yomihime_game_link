"""Typed fact projection fixtures only; no network or model acceptance claims."""

import unittest
from dataclasses import replace
from datetime import timedelta

from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.modules.ff14.features.market_handler import _execution_facts
from ygl_test_subject.modules.ff14.features.market_models import (
    Listing,
    MarketExecution,
    Quote,
    ScopeData,
    ScopeOutcome,
)
from ygl_test_subject.modules.ff14.features.market_sources import Provenance

from yomihime_game_link_sdk.display import DisplayDocument, TextBlock
from yomihime_game_link_sdk.results import CapabilityResult, ErrorCode, ResultStatus

from .test_market import NOW, REGION_IDS, catalog, query


class MarketFactsTests(unittest.TestCase):
    def execution(self, *, intent="overview", snapshot=True, partial=False):
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
            validate_contract(
                CapabilityResult(
                    "fixture",
                    ResultStatus.PARTIAL_SUCCESS if partial else ResultStatus.SUCCESS,
                    validate_contract(
                        DisplayDocument(
                            "Fixture",
                            "fixture",
                            (validate_contract(TextBlock("fixture")),),
                        )
                    ),
                )
            ),
            NOW,
            catalog() if snapshot else None,
        )

    def test_canonical_rows_names_quality_source_and_separate_ages(self):
        facts = _execution_facts(self.execution())
        self.assertEqual(facts["fact_projection_version"], 3)
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
        self.assertIn("World数据上传", facts["answer_guidance"])
        self.assertIn("最多5条有限样本", facts["answer_guidance"])
        self.assertIn("标题/表头", facts["answer_guidance"])
        self.assertIn("source.fetched_at", facts["minimums"][0]["limitation"])
        self.assertIn("未知不互替", facts["minimums"][0]["limitation"])

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

    def test_min_projects_only_price_metrics_and_preserves_null_minimum(self):
        original = self.execution(intent="min")
        facts = _execution_facts(original)
        for row in facts["coverage"][0]["quotes"]:
            self.assertEqual(set(row), {"quality", "minimum_ref", "missing"})
            self.assertEqual(row["missing"], [])
        missing = replace(original.outcomes[0], data=ScopeData(quotes=(Quote("nq"),)))
        row = _execution_facts(replace(original, outcomes=(missing,), minimums=()))[
            "coverage"
        ][0]["quotes"][0]
        self.assertIsNone(row["minimum"])
        self.assertIsNone(row["minimum_time"]["source_time"])
        self.assertEqual(row["missing"], ["minimum"])

    def test_error_projection_selects_fields_and_preserves_missing_and_json_numbers(
        self,
    ):
        from decimal import Decimal

        from ygl_test_subject.modules.ff14.features.market_handler import (
            _error_execution_facts,
            _tool_facts,
        )

        from yomihime_game_link_sdk.results import ErrorDetail

        original = self.execution()
        quote = Quote(
            "nq",
            average_sale_price=Decimal("11.25"),
            daily_sale_velocity=Decimal("2.5"),
        )
        data = ScopeData(quotes=(quote,), incomplete=True)
        outcome = replace(original.outcomes[0], data=data)
        error = CapabilityResult(
            "error-fixture",
            ResultStatus.ERROR,
            error=ErrorDetail(
                ErrorCode.NO_RECORDS, "来源暂无可用市场记录，请等待新指令。"
            ),
            warnings=("PRIVATE_WARNING_CANARY",),
        )
        execution = replace(
            original,
            query=replace(original.query, original_query="PRIVATE_MESSAGE_CANARY"),
            outcomes=(outcome,),
            minimums=(),
            result=error,
        )
        facts = _error_execution_facts(execution)
        result = validate_contract(
            replace(
                error, model_facts=_tool_facts(error, {"supplement": {"market": facts}})
            )
        )
        body = result.model_facts.facts
        self.assertEqual(set(body), {"status", "error", "supplement"})
        row = body["supplement"]["market"]["coverage"][0]["quotes"][0]
        self.assertIsNone(row["minimum"])
        self.assertIsNone(row["recent_purchase"])
        self.assertEqual(row["average_sale_price"], 11.25)
        self.assertEqual(row["daily_sale_velocity"], 2.5)
        self.assertEqual(tuple(row["missing"]), ("minimum", "recent_purchase"))
        self.assertTrue(body["supplement"]["market"]["coverage"][0]["cached"])
        self.assertEqual(body["supplement"]["market"]["minimums"], ())
        self.assertNotIn("warnings", body["supplement"]["market"])
        self.assertNotIn("original_query", body["supplement"]["market"])
        self.assertIn("等待用户明确新指令", facts["answer_guidance"])
        rendered = repr(body)
        for canary in ("PRIVATE_WARNING_CANARY", "PRIVATE_MESSAGE_CANARY"):
            self.assertNotIn(canary, rendered)
        # Direct module constructor selects fields; actual Core receiver
        # acceptance of its JSON representation is checked above, without proof claims.
        self.assertEqual(result.error, error.error)

    def test_error_failure_and_empty_diagnostics_do_not_claim_success(self):
        from ygl_test_subject.modules.ff14.features.market_handler import (
            _error_execution_facts,
            _tool_facts,
        )

        from yomihime_game_link_sdk.results import ErrorDetail

        original = self.execution()
        failed = ScopeOutcome(
            "China",
            "China",
            code=ErrorCode.UPSTREAM_ERROR,
            stage="http",
            reason="upstream_error",
            status_code=503,
        )
        empty = ScopeOutcome("Japan", "Japan", ScopeData(quotes=(Quote("nq"),)))
        error = CapabilityResult(
            "error-fixture",
            ResultStatus.ERROR,
            error=ErrorDetail(ErrorCode.UPSTREAM_ERROR, "来源未完成。"),
        )
        facts = _error_execution_facts(
            replace(original, outcomes=(failed, empty), minimums=(), result=error)
        )
        rows = facts["coverage"]
        self.assertEqual([row["state"] for row in rows], ["failed", "empty"])
        self.assertEqual(rows[0]["status_code"], 503)
        self.assertEqual(rows[0]["reason"], "upstream_error")
        self.assertIsNone(rows[1]["quotes"][0]["minimum"])
        self.assertFalse(
            any(key in facts for key in ("partial", "coverage_complete", "warnings"))
        )
        self.assertEqual(facts["minimums"], [])
        self.assertEqual(facts["listings"], [])
        validate_contract(
            replace(
                error, model_facts=_tool_facts(error, {"supplement": {"market": facts}})
            )
        )

    def test_error_constructor_integral_zero_and_finite_decimal_numbers(self):
        from decimal import Decimal

        from ygl_test_subject.modules.ff14.features.market_handler import (
            _error_execution_facts,
            _tool_facts,
        )

        from yomihime_game_link_sdk.results import ErrorDetail

        original = self.execution()
        error = CapabilityResult(
            "error-numeric-fixture",
            ResultStatus.ERROR,
            error=ErrorDetail(ErrorCode.NO_RECORDS, "来源暂无可用市场记录。"),
        )
        fields = (
            "minimum",
            "recent_purchase",
            "average_sale_price",
            "daily_sale_velocity",
        )
        for value, expected, value_type in (
            (Decimal("7"), 7, int),
            (Decimal("0"), 0, int),
            (Decimal("0.0"), 0, int),
            (Decimal("-0"), 0, int),
            (Decimal("11.25"), 11.25, float),
            (None, None, type(None)),
        ):
            for field in fields:
                with self.subTest(value=value, field=field):
                    quote = Quote("nq", **{field: value})
                    outcome = replace(
                        original.outcomes[0], data=ScopeData(quotes=(quote,))
                    )
                    execution = replace(
                        original, outcomes=(outcome,), minimums=(), result=error
                    )
                    facts = _error_execution_facts(execution)
                    result = validate_contract(
                        replace(
                            error,
                            model_facts=_tool_facts(
                                error, {"supplement": {"market": facts}}
                            ),
                        )
                    )
                    row = result.model_facts.facts["supplement"]["market"]["coverage"][
                        0
                    ]["quotes"][0]
                    self.assertEqual(row[field], expected)
                    self.assertIs(type(row[field]), value_type)
                    self.assertEqual(result.status, ResultStatus.ERROR)
                    self.assertEqual(result.error, error.error)
                    self.assertEqual(
                        result.model_facts.facts["supplement"]["market"]["minimums"], ()
                    )
                    self.assertEqual(
                        result.model_facts.facts["supplement"]["market"]["listings"], ()
                    )

    def test_error_constructor_rejects_nonfinite_overflow_and_nonzero_underflow(self):
        from decimal import Decimal

        from ygl_test_subject.modules.ff14.features.market_handler import (
            _error_execution_facts,
            _tool_facts,
        )

        from yomihime_game_link_sdk.results import ErrorDetail

        original = self.execution()
        error = CapabilityResult(
            "error-numeric-fixture",
            ResultStatus.ERROR,
            error=ErrorDetail(ErrorCode.NO_RECORDS, "来源暂无可用市场记录。"),
        )
        fields = (
            "minimum",
            "recent_purchase",
            "average_sale_price",
            "daily_sale_velocity",
        )
        # A nonintegral Decimal above binary64 range reaches the float branch;
        # integral prices keep the existing exact integer pipeline.
        for value in (
            Decimal("NaN"),
            Decimal("sNaN"),
            Decimal("Infinity"),
            Decimal("-Infinity"),
            Decimal("1" + "0" * 400 + ".5"),
            Decimal("1e-999"),
            Decimal("-1e-999"),
        ):
            for field in fields:
                with self.subTest(value=value, field=field):

                    def execution(number):
                        quote = Quote("nq", **{field: number})
                        outcome = replace(
                            original.outcomes[0], data=ScopeData(quotes=(quote,))
                        )
                        return replace(
                            original, outcomes=(outcome,), minimums=(), result=error
                        )

                    # Same constructor and field accepts genuine zero first.
                    positive = _error_execution_facts(execution(Decimal("0")))
                    validate_contract(
                        replace(
                            error,
                            model_facts=_tool_facts(
                                error, {"supplement": {"market": positive}}
                            ),
                        )
                    )
                    self.assertEqual(positive["coverage"][0]["quotes"][0][field], 0)
                    with self.assertRaisesRegex(
                        ValueError, "^invalid public price$"
                    ) as rejected:
                        _error_execution_facts(execution(value))
                    self.assertIsNone(rejected.exception.__cause__)
                    self.assertIsNone(rejected.exception.__context__)


if __name__ == "__main__":
    unittest.main()
