"""Offline version, catalog ordering and production card contract tests."""

import unittest
from dataclasses import replace

from ygl_test_subject.core.contracts.cards import validate_card

from modules.ff14.features.fflogs_cards import character_card, record_version
from modules.ff14.features.fflogs_catalog import (
    ultimate_catalog_order,
    ultimate_group_key,
)
from modules.ff14.features.fflogs_models import (
    CharacterRankingGroup,
    EncounterRecord,
    RankingOrigin,
)
from tests.modules.ff14 import test_fflogs_aggregation as fixture
from yomihime_game_link_sdk import cards


class LogsTablePolishTests(unittest.TestCase):
    def test_version_only_uses_explicit_labels_or_known_expansion_names(self):
        row = fixture.record().rankings[0]
        for expansion, partition, expected in (
            ("Shadowbringers", None, "5.x"),
            ("晓月之终途", "标准阵容构成", "6.x"),
            ("Dawntrail", "Standard Composition (Patch 7.05)", "7.05"),
            ("Dawntrail", "6.5", "6.5"),
            ("Future", "Patch 10.1", "10.1"),
            ("Unknown expansion", "Standard Composition 3", "—"),
            (None, None, "—"),
            ("Endwalker", "Patch 6.4 - 6.5", "6.x"),
        ):
            with self.subTest(expansion=expansion, partition=partition):
                candidate = replace(
                    row, origin=RankingOrigin(99, 1, 705, expansion, partition)
                )
                self.assertEqual(record_version(candidate), expected)
                self.assertEqual(
                    record_version(replace(candidate, query_state="no_records")), "—"
                )
                self.assertEqual(
                    record_version(replace(candidate, source_error=True)), "—"
                )

    def test_only_ultimate_adds_version_and_removes_repeated_footer(self):
        row = replace(
            fixture.record().rankings[0],
            origin=RankingOrigin(99, 1, 3, "Endwalker", None),
        )
        groups = tuple(
            CharacterRankingGroup(
                99, "Synthetic duty", 1, 3, category, "Endwalker", (row,)
            )
            for category in ("ultimate", "savage")
        )
        document = character_card(
            replace(fixture.record(), groups=groups),
            realm="cn",
            host="https://cn.fflogs.com",
            limit=40,
            explicit_selection=False,
        )
        validate_card(document)
        ultimate, savage = document.blocks[:2]
        table = ultimate.blocks[0]
        self.assertEqual(
            [c.title for c in table.columns],
            ["副本", "职业", "记录版本", "最高 rDPS", "过本次数", "最佳百分位"],
        )
        self.assertEqual(table.rows[0][2], "6.x")
        self.assertEqual(len(ultimate.blocks), 1)
        self.assertEqual(
            len(next(b for b in savage.blocks if isinstance(b, cards.Table)).columns), 5
        )
        self.assertNotIn("资料片：", str(document))

    def test_catalog_anchor_survives_reissues_unknown_names_and_id_shuffling(self):
        source = fixture.catalog()
        old = source.zones[4]
        reissue = replace(
            old,
            zone_id=9999,
            expansion_id=20,
            encounters=(EncounterRecord(99990, old.encounters[0].name, 9999),),
        )
        future = fixture.zone(
            2,
            "New Unknown Ultimate",
            expansion=30,
            difficulty="普通",
            encounters=(EncounterRecord(20, "New Unknown Ultimate", 2),),
        )
        source = replace(
            source, zones=(reissue, future, *source.zones), expansion_order=(30, 20, 10)
        )
        order = ultimate_catalog_order(source, 73)
        keys = sorted(order, key=order.get)
        self.assertEqual(
            keys[0], ultimate_group_key(future.name, future.encounters[0].name)
        )
        self.assertEqual(keys[-1], ultimate_group_key(old.name, old.encounters[0].name))
        # Unknown expansion evidence is last and stable; IDs never imply dates.
        unknown = replace(future, expansion_id=None)
        fallback = ultimate_catalog_order(
            replace(source, zones=(unknown, *source.zones[2:])), 73
        )
        self.assertEqual(fallback[keys[0]][0], 1)

    def test_extra_column_stays_within_existing_forty_row_contract_budget(self):
        row = replace(
            fixture.record().rankings[0],
            origin=RankingOrigin(99, 1, 3, "Endwalker", None),
        )
        group = CharacterRankingGroup(
            99, "Synthetic Ultimate", 1, 3, "ultimate", "Endwalker", (row,) * 40
        )
        document = character_card(
            replace(fixture.record(), groups=(group,), rankings=(row,) * 40),
            realm="cn",
            host="https://cn.fflogs.com",
            limit=40,
            explicit_selection=False,
        )
        validate_card(document)
        self.assertEqual(len(document.blocks[0].blocks[0].rows), 40)
        from ygl_test_subject.presentation.layout_renderer import measure_document

        # Semantic row/column budgets remain unchanged. Raster memory limits
        # still apply independently to tall documents (including old tables).
        compact = replace(
            document,
            blocks=(
                replace(
                    document.blocks[0],
                    blocks=(
                        replace(
                            document.blocks[0].blocks[0],
                            rows=document.blocks[0].blocks[0].rows[:7],
                        ),
                    ),
                ),
            ),
        )
        self.assertGreater(measure_document(compact).height, 500)

    def test_winner_period_and_budget_do_not_reorder_catalog_identities(self):
        from modules.ff14.features.fflogs import _assemble_catalog_rankings

        source = fixture.catalog()
        targets = fixture.targets(source)
        expected = None
        for winner in (101, 102):
            outcomes = {
                fixture.key(target): fixture.selection(
                    target, raw="100" if target[0].zone_id == winner else "50"
                )
                for target in targets
            }
            result = _assemble_catalog_rankings(
                fixture.record(), source, targets, outcomes
            )
            names = [
                g.rankings[0].encounter_name
                for g in result.groups
                if g.category == "ultimate"
            ]
            if expected is None:
                expected = names
            self.assertEqual(names, expected)
        partial = _assemble_catalog_rankings(fixture.record(), source, targets, {})
        self.assertEqual(
            [
                g.rankings[0].encounter_name
                for g in partial.groups
                if g.category == "ultimate"
            ],
            expected,
        )
