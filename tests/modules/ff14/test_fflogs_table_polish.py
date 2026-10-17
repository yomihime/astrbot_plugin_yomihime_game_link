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

    def test_catalog_tables_share_version_columns_and_remove_repeated_footer(self):
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
            len(next(b for b in savage.blocks if isinstance(b, cards.Table)).columns), 6
        )
        savage_table = next(b for b in savage.blocks if isinstance(b, cards.Table))
        self.assertEqual(savage_table.columns, table.columns)
        self.assertEqual(savage_table.rows[0][2], "6.x")
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

    def test_full_duty_names_headers_and_jobs_remain_single_line(self):
        from ygl_test_subject.presentation.layout_renderer import measure_document

        from modules.ff14.features.fflogs_jobs import JOBS
        from tests.modules.ff14.test_duty_order import NAMES

        for language in ("zh", "ja"):
            for name in NAMES:
                rows = tuple(
                    replace(
                        fixture.record().rankings[0],
                        encounter_name=name,
                        spec=job.source_name,
                        origin=RankingOrigin(99, 1, 3, "Endwalker", None),
                    )
                    for job in JOBS
                )
                group = CharacterRankingGroup(
                    99, name, 1, 3, "ultimate", "Endwalker", rows
                )
                document = character_card(
                    replace(fixture.record(), groups=(group,), rankings=rows),
                    realm="cn",
                    host="https://cn.fflogs.com",
                    limit=40,
                    explicit_selection=False,
                    job_language=language,
                )
                self.assertEqual(document.width, 1240)
                measured = measure_document(document)
                cells = [
                    box
                    for box in measured.boxes
                    if box.kind == "text" and "/rows/" in box.path
                ]
                self.assertEqual(len(cells), (len(rows) + 1) * 6)
                for cell in cells:
                    self.assertEqual(
                        cell.bounds[3] - cell.bounds[1],
                        36 if "/rows/0/" in cell.path else 40,
                    )
                self.assertEqual(
                    [row[0] for row in document.blocks[0].blocks[0].rows],
                    [name] * len(rows),
                )

    def test_row_status_and_history_notes_survive_outside_cells(self):
        base = fixture.record().rankings[0]
        rows = tuple(
            replace(
                base,
                encounter_name=f"合成绝境战{i}",
                query_state=state,
                history_complete=False,
            )
            for i, state in enumerate(("record", "no_records", "failed", "not_queried"))
        )
        group = CharacterRankingGroup(
            99, "合成绝境战", 1, 3, "ultimate", "Endwalker", rows
        )
        document = character_card(
            replace(fixture.record(), groups=(group,), rankings=rows),
            realm="cn",
            host="https://cn.fflogs.com",
            limit=40,
            explicit_selection=False,
        )
        table, *notes = document.blocks[0].blocks
        self.assertTrue(all("\n" not in row[0] for row in table.rows))
        self.assertEqual(len(notes), 4)
        for row, note, message in zip(
            rows,
            notes,
            ("历史成绩未查全", "查询成功，无公开记录", "查询失败", "未查询"),
        ):
            self.assertIn(row.encounter_name, note.content)
            self.assertIn(message, note.content)

    def test_overlong_single_line_keeps_full_value_and_uses_existing_fallback(self):
        from ygl_test_subject.presentation.card_bitmap import BitmapUnavailable
        from ygl_test_subject.presentation.layout_renderer import measure_document

        name = "未来来源完整绝境战名称" * 12
        row = replace(fixture.record().rankings[0], encounter_name=name)
        group = CharacterRankingGroup(99, name, 1, 3, "ultimate", "Unknown", (row,))
        document = character_card(
            replace(fixture.record(), groups=(group,), rankings=(row,)),
            realm="cn",
            host="https://cn.fflogs.com",
            limit=40,
            explicit_selection=False,
        )
        self.assertEqual(document.blocks[0].blocks[0].rows[0][0], name)
        with self.assertRaises(BitmapUnavailable) as raised:
            measure_document(document)
        self.assertEqual(raised.exception.reason, "layout_overflow")

    def test_ordinary_full_english_duties_use_bounded_wide_variant(self):
        from ygl_test_subject.presentation.layout_renderer import measure_document

        names = (
            "The Unending Coil of Bahamut (Ultimate)",
            "The Weapon's Refrain (Ultimate)",
            "The Epic of Alexander (Ultimate)",
            "Dragonsong's Reprise (Ultimate)",
            "The Omega Protocol (Ultimate)",
            "Futures Rewritten (Ultimate)",
            "Dancing Mad (Ultimate)",
            "Synthetic Ultimate 100-0",
        )
        for language in ("zh", "ja", "en"):
            rows = tuple(
                replace(
                    fixture.record().rankings[0],
                    encounter_name=name,
                    spec="Pictomancer",
                    origin=RankingOrigin(99, 1, 3, "Endwalker", None),
                )
                for name in names
            )
            group = CharacterRankingGroup(
                99, "Synthetic source", 1, 3, "ultimate", "Endwalker", rows
            )
            document = character_card(
                replace(fixture.record(), groups=(group,), rankings=rows),
                realm="global",
                host="https://www.fflogs.com",
                limit=40,
                explicit_selection=False,
                job_language=language,
            )
            self.assertEqual(document.width, 1500)
            self.assertEqual(
                tuple(row[0] for row in document.blocks[0].blocks[0].rows), names
            )
            for cell in measure_document(document).boxes:
                if cell.kind == "text" and "/rows/" in cell.path:
                    self.assertEqual(
                        cell.bounds[3] - cell.bounds[1],
                        36 if "/rows/0/" in cell.path else 40,
                    )

    def test_explicit_full_english_duty_also_keeps_single_line(self):
        from ygl_test_subject.presentation.layout_renderer import measure_document

        name = "The Unending Coil of Bahamut (Ultimate)"
        row = replace(
            fixture.record().rankings[0], encounter_name=name, spec="Gunbreaker"
        )
        document = character_card(
            replace(
                fixture.record(), rankings=(row,), groups=(), catalog_evaluated=False
            ),
            realm="global",
            host="https://www.fflogs.com",
            limit=40,
            explicit_selection=True,
            job_language="ja",
        )
        self.assertEqual(document.width, 1500)
        self.assertEqual(len(document.blocks[0].blocks[0].columns), 5)
        self.assertEqual(document.blocks[0].blocks[0].rows[0][0], name)
        for cell in measure_document(document).boxes:
            if cell.kind == "text" and "/rows/" in cell.path:
                self.assertEqual(
                    cell.bounds[3] - cell.bounds[1],
                    36 if "/rows/0/" in cell.path else 40,
                )

    def test_full_english_seven_plus_thirteen_savage_render_within_existing_budget(
        self,
    ):
        from ygl_test_subject.presentation.layout_renderer import render_document

        names = (
            "The Unending Coil of Bahamut (Ultimate)",
            "The Weapon's Refrain (Ultimate)",
            "The Epic of Alexander (Ultimate)",
            "Dragonsong's Reprise (Ultimate)",
            "The Omega Protocol (Ultimate)",
            "Futures Rewritten (Ultimate)",
            "Dancing Mad (Ultimate)",
        )
        base = fixture.record().rankings[0]
        ultimate = tuple(replace(base, encounter_name=name) for name in names)
        groups = [
            CharacterRankingGroup(
                99, "Synthetic Ultimate source", 1, 3, "ultimate", "Endwalker", ultimate
            )
        ]
        for tier, count in enumerate((4, 4, 5)):
            rows = tuple(
                replace(base, encounter_name=f"Synthetic {73 + tier}-{index}")
                for index in range(count)
            )
            groups.append(
                CharacterRankingGroup(
                    73 + tier,
                    f"Synthetic Savage tier {tier}",
                    3,
                    3,
                    "savage",
                    "Dawntrail",
                    rows,
                )
            )
        rows = tuple(row for group in groups for row in group.rankings)
        document = character_card(
            replace(
                fixture.record(),
                groups=tuple(groups),
                rankings=rows,
                history_fetched_at=0,
            ),
            realm="global",
            host="https://www.fflogs.com",
            limit=40,
            explicit_selection=False,
        )
        result = render_document(document)
        self.assertEqual(result.width, 1500)
        self.assertEqual(len(rows), 20)
        self.assertGreater(result.height, 1500)

    def test_savage_version_uses_source_evidence_or_dash(self):
        rows = (
            replace(
                fixture.record().rankings[0],
                origin=RankingOrigin(99, 3, 3, "Dawntrail", None),
            ),
            replace(fixture.record().rankings[0], origin=None),
        )
        group = CharacterRankingGroup(99, "合成零式", 3, 3, "savage", "Dawntrail", rows)
        document = character_card(
            replace(fixture.record(), groups=(group,), rankings=rows),
            realm="cn",
            host="",
            limit=40,
            explicit_selection=False,
        )
        table = next(
            block
            for section in document.blocks
            if isinstance(section, cards.Section)
            for block in section.blocks
            if isinstance(block, cards.Table)
        )
        self.assertEqual([row[2] for row in table.rows], ["7.x", "—"])
