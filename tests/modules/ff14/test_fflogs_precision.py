"""Exact parser-to-history ordering and bounded Decimal display regressions."""

from __future__ import annotations

import json
import unittest
from decimal import ROUND_UP, Decimal, getcontext, localcontext

from modules.ff14.features.fflogs import (
    _assemble_catalog_rankings,
    _catalog_query_targets,
    _target_key,
)
from modules.ff14.features.fflogs_cards import percentile, percentile_text
from modules.ff14.features.fflogs_catalog import (
    parse_graphql_envelope,
    select_character_zones,
)
from modules.ff14.features.fflogs_models import (
    EncounterRecord,
    FFLogsPayloadError,
    OutputMetadata,
    parse_rankings,
)
from tests.modules.ff14 import test_fflogs_aggregation as fixture


def catalog(reverse=False):
    historical = [
        fixture.zone(
            ident,
            "Synthetic precision source",
            difficulty="普通",
            encounters=(
                EncounterRecord(ident * 10, "Synthetic Precision Ultimate", ident),
            ),
        )
        for ident in (900, 901)
    ]
    if reverse:
        historical.reverse()
    return OutputMetadata(
        (*historical, fixture.zone(73, "Synthetic anchor tier")), (), True, (20,)
    )


def parsed(target, raw, *, amount=1234.25, job="Scholar", kills=3):
    """Use the production exact JSON envelope and ranking parser, not manual rows."""
    zone, difficulty, _, _ = target
    item = zone.encounters[0]
    payload = json.dumps(
        {
            "data": {
                "selection": {
                    "metric": "rdps",
                    "zone": zone.zone_id,
                    "difficulty": difficulty.difficulty_id,
                    "partition": 3,
                    "size": 8,
                    "rankings": [
                        {
                            "encounter": {"id": item.encounter_id, "name": item.name},
                            "rankPercent": None,
                            "bestAmount": amount,
                            "totalKills": kills,
                            "bestSpec": job,
                        }
                    ],
                }
            }
        }
    )
    if raw is not None:
        payload = payload.replace('"rankPercent": null', '"rankPercent": ' + str(raw))
    envelope = parse_graphql_envelope(payload.encode(), exact_numbers=True)
    return parse_rankings(envelope["selection"])


def assembled(
    low,
    high,
    *,
    reverse=False,
    low_job="Scholar",
    high_job="RedMage",
    high_amount=9999.75,
    high_kills=9,
):
    source = catalog(reverse)
    targets = _catalog_query_targets(select_character_zones(source, 73))
    outcomes = {}
    for target in targets:
        ident = target[0].zone_id
        outcomes[_target_key(target)] = parsed(
            target,
            high if ident == 901 else low,
            amount=high_amount if ident == 901 else 1111.25,
            job=high_job if ident == 901 else low_job,
            kills=high_kills if ident == 901 else 1,
        )
    return _assemble_catalog_rankings(
        fixture.record(), source, targets, outcomes
    ), outcomes


class ExactHistoryPrecisionTests(unittest.TestCase):
    def test_supported_beyond_context_boundary_selects_higher_whole_row(self):
        low = "99.00000000000000000000000000001"
        high = "99.00000000000000000000000000002"
        for precision in (1, 7, 28, 150):
            for reverse in (False, True):
                with (
                    self.subTest(precision=precision, reverse=reverse),
                    localcontext() as context,
                ):
                    context.prec = precision
                    context.rounding = ROUND_UP
                    result, _ = assembled(low, high, reverse=reverse)
                    row = result.groups[0].rankings[0]
                    self.assertEqual(row.origin.zone_id, 901)
                    self.assertEqual(row.encounter_id, 9010)
                    self.assertEqual(row.rank_percent_exact, Decimal(high))
                    self.assertEqual(
                        (row.spec, row.best_amount, row.total_kills),
                        ("RedMage", 9999.75, 9),
                    )
                    self.assertTrue(row.history_complete)
                    self.assertTrue(result.catalog_complete)
                    self.assertEqual(
                        (context.prec, context.rounding), (precision, ROUND_UP)
                    )
                    self.assertIs(getcontext(), context)

    def test_full_128_digit_inputs_survive_parser_order_and_display(self):
        for low, high, label in (
            ("99." + "0" * 125 + "1", "99." + "0" * 125 + "2", "99.0%"),
            ("0." + "1" * 128, "0." + "1" * 127 + "2", "0.1%"),
        ):
            for precision in (2, 28, 128):
                for reverse in (False, True):
                    with (
                        self.subTest(precision=precision, reverse=reverse, label=label),
                        localcontext() as context,
                    ):
                        context.prec = precision
                        result, outcomes = assembled(low, high, reverse=reverse)
                        self.assertTrue(
                            all(
                                len(value.rows[0].rank_percent_exact.as_tuple().digits)
                                == 128
                                for value in outcomes.values()
                            )
                        )
                        row = result.groups[0].rankings[0]
                        self.assertEqual(row.origin.zone_id, 901)
                        self.assertEqual(row.rank_percent_exact, Decimal(high))
                        self.assertEqual(percentile_text(row.rank_percent_exact), label)
                        self.assertEqual(
                            percentile(row.rank_percent_exact).spans[0].text, label
                        )
                        self.assertEqual(context.prec, precision)

    def test_true_numeric_ties_with_trailing_zeros_keep_stable_source(self):
        for reverse in (False, True):
            for precision in (1, 28, 150):
                with (
                    self.subTest(reverse=reverse, precision=precision),
                    localcontext() as context,
                ):
                    context.prec = precision
                    result, _ = assembled("99.000000", "99", reverse=reverse)
                    row = result.groups[0].rankings[0]
                    self.assertEqual(
                        (row.origin.zone_id, row.encounter_id), (900, 9000)
                    )
                    self.assertEqual(
                        (row.spec, row.best_amount, row.total_kills),
                        ("Scholar", 1111.25, 1),
                    )
                    self.assertEqual(str(row.rank_percent_exact), "99.000000")

    def test_missing_and_literal_zero_stay_distinct_and_zero_beats_missing(self):
        for reverse in (False, True):
            result, outcomes = assembled(
                None, "0", reverse=reverse, high_job=None, high_amount=0, high_kills=0
            )
            missing = next(
                value.rows[0] for key, value in outcomes.items() if key[0] == 900
            )
            row = result.groups[0].rankings[0]
            self.assertIsNone(missing.rank_percent_exact)
            self.assertEqual(row.origin.zone_id, 901)
            self.assertEqual(row.rank_percent_exact, Decimal("0"))
            self.assertEqual(percentile_text(missing.rank_percent_exact), "—")
            self.assertEqual(percentile_text(row.rank_percent_exact), "0.0%")
            self.assertIsNone(row.spec)
            self.assertEqual((row.best_amount, row.total_kills), (0, 0))

    def test_near_100_max_precision_is_truncated_non_gold_and_exact_100_is_gold(self):
        near = "99." + "9" * 126
        for precision in (1, 7, 28, 150):
            with self.subTest(precision=precision), localcontext() as context:
                context.prec = precision
                result, _ = assembled("99", near)
                raw = result.groups[0].rankings[0].rank_percent_exact
                self.assertEqual(len(raw.as_tuple().digits), 128)
                self.assertEqual(percentile_text(raw), "99.9%")
                shown = percentile(raw).spans[0]
                self.assertEqual(shown.text, "99.9%")
                self.assertNotEqual(shown.emphasis, "gold")
                self.assertEqual(shown.color, "#e268a8")
                exact, _ = assembled(near, "100")
                raw_100 = exact.groups[0].rankings[0].rank_percent_exact
                self.assertEqual(raw_100, Decimal("100"))
                self.assertEqual(percentile(raw_100).spans[0].emphasis, "gold")
                self.assertEqual(percentile_text(raw_100), "100.0%")
                self.assertEqual(context.prec, precision)

    def test_string_budget_and_illegal_decimal_precision_are_not_relaxed(self):
        valid = Decimal("99." + "9" * 126)
        self.assertEqual(len(valid.as_tuple().digits), 128)
        self.assertGreater(len(str(valid)), 128)
        self.assertEqual(percentile_text(str(valid)), "ERR")
        self.assertEqual(percentile_text(Decimal("99." + "9" * 127)), "ERR")
        self.assertEqual(percentile_text(Decimal("1e-129")), "ERR")
        target = _catalog_query_targets(select_character_zones(catalog(), 73))[0]
        for raw in ("99." + "9" * 127, "1e-129"):
            with self.subTest(raw=raw), self.assertRaises(FFLogsPayloadError):
                parsed(target, raw)
