"""Synthetic catalog policies and bounded historical selections; no live data."""

from __future__ import annotations

import asyncio
import json
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.core.contracts.diagnostics import diagnostic_payload
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.modules.ff14.features.diagnostic_declarations import DECLARATIONS
from ygl_test_subject.modules.ff14.features.fflogs import (
    FFLogsCharacterLookup,
    _assemble_catalog_rankings,
    _bounded_catalog_targets,
    _catalog_query_targets,
    _character_result,
    _target_key,
)
from ygl_test_subject.modules.ff14.features.fflogs_cards import (
    character_card,
    percentile_text,
)
from ygl_test_subject.modules.ff14.features.fflogs_catalog import (
    character_metadata_supported,
    parse_character_metadata,
    select_character_zones,
)
from ygl_test_subject.modules.ff14.features.fflogs_models import (
    CharacterRanking,
    DifficultyRecord,
    EncounterRecord,
    OutputMetadata,
    PartitionRecord,
    RankingSelection,
    ZoneRecord,
)

from tests.modules.ff14 import test_fflogs as fixture
from tests.modules.ff14 import test_fflogs_cards as catalog_fixture
from yomihime_game_link_sdk import DiagnosticDeclaration


def zone(
    ident,
    name,
    *,
    size=8,
    difficulty="Savage",
    expansion=20,
    encounters=None,
    partitions=(PartitionRecord(3, "标准阵容构成 (Patch 7.4)", True),),
):
    return ZoneRecord(
        ident,
        name,
        (
            DifficultyRecord(
                3 if difficulty == "Savage" else 1,
                difficulty,
                None if size is None else (size,),
            ),
        ),
        tuple(
            encounters
            or (EncounterRecord(ident * 10, f"Synthetic encounter {ident}", ident),)
        ),
        expansion,
        f"Synthetic expansion {expansion}",
        False,
        partitions,
    )


def catalog():
    periods = (PartitionRecord(3, "标准阵容构成 (Synthetic)", True),)
    return OutputMetadata(
        (
            zone(73, "Synthetic current tier"),
            zone(74, "Synthetic four player", size=4),
            zone(75, "Synthetic large group", size=48),
            zone(76, "Synthetic normal", difficulty="普通"),
            zone(
                101,
                "Synthetic Ultimate old",
                difficulty="普通",
                expansion=10,
                encounters=(
                    EncounterRecord(1001, "Synthetic Ultimate Full Name", 101),
                ),
                partitions=periods,
            ),
            zone(
                102,
                "Synthetic Ultimate recent",
                difficulty="普通",
                encounters=(
                    EncounterRecord(1002, "Synthetic Ultimate Full Name", 102),
                ),
                partitions=periods,
            ),
            zone(
                103,
                "Synthetic English Zone",
                difficulty="普通",
                encounters=(EncounterRecord(1003, "合成妖星绝境战", 103),),
                partitions=periods,
            ),
            zone(
                104,
                "Synthetic Ultimate alias",
                difficulty="普通",
                encounters=(
                    EncounterRecord(1001, "Synthetic Ultimate Full Name", 101),
                ),
            ),
            zone(72, "Synthetic prior tier", expansion=10),
        ),
        (),
        True,
        (20, 10),
    )


def record():
    return catalog_fixture.character()


def targets(metadata=None):
    return _catalog_query_targets(select_character_zones(metadata or catalog(), 73))


def selection(
    target, *, raw="99.9762", amount=12345.5, kills=5, job="Scholar", rows=True, size=8
):
    source, difficulty, _, period = target
    ranks = (
        tuple(
            CharacterRanking(
                item.encounter_id,
                item.name,
                float(raw),
                amount,
                job,
                Decimal(raw),
                kills,
            )
            for item in source.encounters
        )
        if rows
        else ()
    )
    return RankingSelection(
        "rdps",
        source.zone_id,
        difficulty.difficulty_id,
        3 if period == -1 else period if period is not None else -1,
        size,
        ranks,
    )


def key(target):
    return _target_key(target)


class CatalogPolicyTests(unittest.TestCase):
    def test_ordinary_ultimate_mixed_names_and_canonical_ownership(self):
        selected = select_character_zones(catalog(), 73)
        self.assertEqual(
            [item[0].zone_id for item in selected if item[2] == "ultimate"],
            [101, 102, 103],
        )
        self.assertEqual(
            [item[0].zone_id for item in selected if item[2] == "savage"], [73]
        )
        self.assertTrue(
            all(item[1].name == "普通" for item in selected if item[2] == "ultimate")
        )
        self.assertEqual(
            len(targets()), 4
        )  # all three independent historical sources, one tier

    def test_fallback_is_catalog_only_and_unknown_sizes_cannot_trigger_it(self):
        source = catalog()
        changed = replace(
            source,
            zones=tuple(
                replace(item, difficulties=()) if item.zone_id == 73 else item
                for item in source.zones
            ),
        )
        # Current expansion still has known 4/48/normal, none is eight-player Savage.
        self.assertEqual(
            [
                item[0].zone_id
                for item in select_character_zones(changed, 73)
                if item[2] == "savage"
            ],
            [72],
        )
        unknown = replace(
            source,
            zones=tuple(
                replace(item, difficulties=(DifficultyRecord(3, "Savage", None),))
                if item.zone_id == 73
                else item
                for item in source.zones
            ),
        )
        self.assertEqual(
            [
                item[0].zone_id
                for item in select_character_zones(unknown, 73)
                if item[2] == "savage"
            ],
            [73],
        )
        pending = targets(unknown)
        validated = _assemble_catalog_rankings(
            record(), unknown, pending, {key(item): selection(item) for item in pending}
        )
        self.assertEqual(
            [group.zone_id for group in validated.groups if group.category == "savage"],
            [73],
        )
        self.assertFalse(validated.catalog_complete)
        no_kills = {
            key(target): selection(target, rows=False) for target in targets(source)
        }
        result = _assemble_catalog_rankings(record(), source, targets(source), no_kills)
        self.assertEqual(
            [group.zone_id for group in result.groups if group.category == "savage"],
            [73],
        )
        self.assertTrue(all(row.query_state == "no_records" for row in result.rankings))

    def test_raw_decimal_winner_keeps_entire_selected_period_and_stable_ties(self):
        source = catalog()
        candidates = targets(source)
        outcomes = {key(target): selection(target) for target in candidates}
        winner = next(target for target in candidates if key(target)[:3] == (102, 1, 3))
        outcomes[key(winner)] = selection(
            winner, raw="99.99999999999999999", amount=22222, kills=9, job="Astrologian"
        )
        result = _assemble_catalog_rankings(record(), source, candidates, outcomes)
        row = next(group.rankings[0] for group in result.groups if group.zone_id == 102)
        self.assertEqual(
            (row.encounter_id, row.best_amount, row.total_kills, row.spec),
            (1002, 22222, 9, "Astrologian"),
        )
        self.assertEqual(row.origin.partition_name, "标准阵容构成 (Synthetic)")
        self.assertEqual(row.origin.requested_partition, 3)
        self.assertEqual(row.origin.requested_timeframe, "Historical")
        self.assertEqual(percentile_text(row.rank_percent_exact), "99.9%")
        self.assertEqual(percentile_text(Decimal("99.9762")), "99.9%")
        equal = {key(target): selection(target) for target in candidates}
        result = _assemble_catalog_rankings(record(), source, candidates, equal)
        self.assertEqual(
            (result.groups[1].zone_id, result.groups[1].partition), (101, 3)
        )
        # Provider expansion list is not a chronology contract, so stable IDs win ties.

    def test_failure_unqueried_no_records_and_missing_period_are_distinct(self):
        source = catalog()
        candidates = targets(source)
        outcomes = {key(target): selection(target, rows=False) for target in candidates}
        outcomes[(101, 1, 3, "rdps", "Historical", None)] = "failed"
        outcomes.pop((103, 1, 3, "rdps", "Historical", None))
        result = _assemble_catalog_rankings(record(), source, candidates, outcomes)
        self.assertEqual(result.groups[1].rankings[0].query_state, "failed")
        self.assertEqual(result.groups[0].rankings[0].query_state, "not_queried")
        self.assertEqual(result.groups[-1].rankings[0].query_state, "no_records")
        self.assertFalse(result.catalog_complete)
        self.assertTrue(any("未查询" in warning for warning in result.catalog_warnings))
        missing = replace(
            source,
            zones=tuple(
                replace(item, expansion_name=None) if item.zone_id == 101 else item
                for item in source.zones
            ),
        )
        candidates = targets(missing)
        outcomes = {
            key(target): selection(
                target, raw="100" if target[0].zone_id == 101 else "98.9971"
            )
            for target in candidates
        }
        result = _assemble_catalog_rankings(record(), missing, candidates, outcomes)
        card = character_card(
            result,
            realm="cn",
            host="https://cn.fflogs.com",
            limit=40,
            explicit_selection=False,
        )
        self.assertEqual(card.blocks[0].blocks[0].rows[1][2], "—")
        self.assertTrue(
            result.catalog_complete
        )  # missing period name is not a missed query

    def test_public_kill_bounds_use_distinct_duty_and_difficulty_without_summing(self):
        source = catalog()
        tier = replace(
            source.zones[0],
            partitions=(
                *source.zones[0].partitions,
                PartitionRecord(4, "非标准阵容构成 (Patch 7.4)", False),
                PartitionRecord(5, "标准阵容构成 (Patch 7.5)", False),
                PartitionRecord(6, "超越之力 (Patch 7.5)", False),
            ),
        )
        old_ultimate = replace(
            source.zones[4],
            difficulties=(
                *source.zones[4].difficulties,
                DifficultyRecord(2, "Synthetic alternate", (8,)),
            ),
        )
        source = replace(
            source, zones=(tier, *source.zones[1:4], old_ultimate, *source.zones[5:])
        )
        candidates = targets(source)
        attrs = {
            (73, 3, 3): dict(raw="99", kills=4, job="Scholar"),
            (73, 3, 4): dict(raw="30", kills=12, job="Warrior"),
            (73, 3, 5): dict(raw="20", kills=12, job="Scholar"),
            (101, 1, 3): dict(raw="60", kills=22, job="Astrologian"),
            (102, 1, 3): dict(raw="100", kills=3, job="Warrior"),
            (101, 2, 3): dict(raw="10", kills=56, job="Monk"),
            (103, 1, 3): dict(kills=0),
        }
        outcomes = {
            key(target): selection(target, **attrs[key(target)[:3]])
            for target in candidates
        }
        result = _assemble_catalog_rankings(record(), source, candidates, outcomes)
        self.assertNotIn((73, 3, 6), [key(target)[:3] for target in candidates])
        by_zone = {group.zone_id: group.rankings[0] for group in result.groups}
        for zone_id, wanted in (
            (73, (4, 12, "Scholar", Decimal("99"))),
            (102, (3, 22, "Warrior", Decimal("100"))),
            (103, (0, None, "Scholar", Decimal("99.9762"))),
        ):
            row = by_zone[zone_id]
            self.assertEqual(
                (
                    row.total_kills,
                    row.public_kills_lower_bound,
                    row.spec,
                    row.rank_percent_exact,
                ),
                wanted,
            )
        output = _character_result(
            result, "https://cn.fflogs.com", "CN", "rdps", None, "cn"
        )
        ultimate, savage = output.document.card.blocks[:2]
        self.assertEqual(
            {row[4].spans[0].text for row in ultimate.blocks[0].rows}, {"22", "—"}
        )
        self.assertEqual(savage.blocks[1].rows[0][4].spans[0].text, "12")
        text = str(output.document.ordered_blocks)
        self.assertIn("公开过本至少 12 次", text)
        self.assertIn("公开过本次数未知", text)

    def test_partial_public_kill_bound_and_empty_ranges(self):
        source = catalog()
        candidates = targets(source)
        outcomes = {key(target): selection(target, rows=False) for target in candidates}
        for target in candidates:
            if target[0].zone_id == 101:
                outcomes[key(target)] = "failed"
            elif target[0].zone_id == 102:
                outcomes[key(target)] = selection(target, kills=14)
        result = _assemble_catalog_rankings(record(), source, candidates, outcomes)
        by_zone = {group.zone_id: group.rankings[0] for group in result.groups}
        self.assertEqual(by_zone[102].public_kills_lower_bound, 14)
        self.assertFalse(by_zone[102].history_complete)
        card = character_card(
            result,
            realm="cn",
            host="https://cn.fflogs.com",
            limit=40,
            explicit_selection=False,
        )
        self.assertIn(
            "14", [row[4].spans[0].text for row in card.blocks[0].blocks[0].rows]
        )
        self.assertIn("部分记录未取得", card.subtitle)
        self.assertEqual(card.blocks[-1].content, "版本对应最佳 rDPS 百分位。")
        self.assertEqual(by_zone[73].query_state, "no_records")
        self.assertIsNone(by_zone[73].public_kills_lower_bound)

    def test_budget_counts_partition_targets_and_covers_distinct_groups(self):
        source = catalog()
        expanded = replace(
            source,
            zones=(
                *(zone(73 + i, f"Synthetic tier {i}") for i in range(3)),
                *(
                    zone(
                        200 + i,
                        f"Synthetic Ultimate {i}",
                        difficulty="普通",
                        partitions=tuple(
                            PartitionRecord(j, f"标准阵容构成 (Synthetic {j})", j == 1)
                            for j in range(1, 21)
                        ),
                    )
                    for i in range(55)
                ),
            ),
        )
        candidates = targets(expanded)
        selected = _bounded_catalog_targets(candidates)
        self.assertEqual(len({key(target) for target in selected}), 1103)
        self.assertEqual({target[2] for target in selected}, {"ultimate", "savage"})
        self.assertEqual(
            {target[0].zone_id for target in selected if target[2] == "savage"},
            {73, 74, 75},
        )
        self.assertEqual(
            {target[0].zone_id for target in selected if target[2] == "ultimate"},
            set(range(200, 255)),
        )
        outcomes = {key(target): selection(target) for target in selected}
        result = _assemble_catalog_rankings(record(), expanded, candidates, outcomes)
        self.assertTrue(all(row.history_complete for row in result.rankings))
        self.assertFalse(
            any("未查询" in warning for warning in result.catalog_warnings)
        )

    def test_schema_gates_new_values_and_missing_sizes_remains_unknown(self):
        schema = catalog_fixture.expanded_schema()["data"]
        self.assertTrue(character_metadata_supported(schema))
        schema["difficultyType"]["fields"] = [
            item
            for item in schema["difficultyType"]["fields"]
            if item["name"] != "sizes"
        ]
        self.assertFalse(character_metadata_supported(schema))
        raw = catalog_fixture.expanded_metadata()
        for item in raw["data"]["worldData"]["zones"]:
            for difficulty in item["difficulties"]:
                difficulty.pop("sizes", None)
        parsed = parse_character_metadata(raw["data"])
        self.assertTrue(
            all(
                difficulty.sizes is None
                for item in parsed.zones
                for difficulty in item.difficulties
            )
        )

    def test_generic_encounter_does_not_merge_different_complete_zone_names(self):
        source = replace(
            catalog(),
            zones=(
                zone(73, "Synthetic tier"),
                zone(
                    801,
                    "Synthetic Red Ultimate",
                    difficulty="普通",
                    encounters=(EncounterRecord(8001, "Generic Boss", 801),),
                ),
                zone(
                    802,
                    "Synthetic Blue Ultimate",
                    difficulty="普通",
                    encounters=(EncounterRecord(8002, "Generic Boss", 802),),
                ),
            ),
        )
        candidates = targets(source)
        result = _assemble_catalog_rankings(
            record(),
            source,
            candidates,
            {key(item): selection(item) for item in candidates},
        )
        self.assertEqual(
            len([group for group in result.groups if group.category == "ultimate"]), 2
        )
        self.assertIn(
            "Synthetic Red Ultimate", result.groups[0].rankings[0].encounter_name
        )


class BatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_wave_covers_tiers_before_history_and_shared_deadline(self):
        from ygl_test_subject.modules.ff14.features import fflogs

        source = OutputMetadata(
            (
                *(
                    zone(
                        200 + i,
                        f"Synthetic Ultimate {i}",
                        difficulty="普通",
                        partitions=(
                            PartitionRecord(1, "标准阵容构成 (Synthetic)", True),
                        ),
                    )
                    for i in range(60)
                ),
                *(zone(i, f"Synthetic tier {i}") for i in (73, 74, 75)),
            ),
            (),
            True,
            (20,),
        )
        clock = [0.0]
        calls = []

        async def fetch(request):
            variables = json.loads(request.body)["variables"]
            calls.append(variables)
            await asyncio.sleep(0)
            clock[0] = 16.0
            value = {
                "id": 42,
                "name": "Synthetic Hero",
                "hidden": False,
                "server": {"name": "Cerberus"},
            }
            for alias, zone_id in variables.items():
                if alias.startswith("z"):
                    index = alias[1:]
                    value[alias] = {
                        "zone": zone_id,
                        "difficulty": variables[f"d{index}"],
                        "partition": variables.get(f"p{index}", -1),
                        "metric": "rdps",
                        "size": 8,
                        "rankings": [],
                    }
            return SimpleNamespace(
                status_code=200,
                body=json.dumps(
                    {"data": {"characterData": {"character": value}}}
                ).encode(),
            )

        handler = FFLogsCharacterLookup(None)
        self.addCleanup(handler.close)
        with patch.object(fflogs, "monotonic", side_effect=lambda: clock[0]):
            result = await handler._catalog_rankings(
                SimpleNamespace(http=SimpleNamespace(fetch=fetch)),
                "fflogs_public_cn",
                replace(record(), rankings=()),
                source,
                {
                    "name": "Synthetic Hero",
                    "serverSlug": "cerberus",
                    "serverRegion": "CN",
                    "metric": "rdps",
                },
                handler._lifecycle,
                deadline=15,
            )
        self.assertEqual(len(calls), 2)
        self.assertTrue(
            {200, 73, 74, 75}.issubset(
                {value for alias, value in calls[0].items() if alias.startswith("z")}
            )
        )
        self.assertTrue(
            all(
                row.query_state == "no_records"
                for group in result.groups
                if group.category == "savage"
                for row in group.rankings
            )
        )
        self.assertTrue(any("未查询" in warning for warning in result.catalog_warnings))
        self.assertFalse(result.catalog_complete)
        self.assertEqual(
            [group.zone_id for group in result.groups if group.category == "savage"],
            [73, 74, 75],
        )
        self.assertEqual(len(result.groups), 63)

    async def test_cancel_stops_two_inflight_batches_and_never_starts_queued_targets(
        self,
    ):
        source = OutputMetadata(
            (
                *(
                    zone(
                        200 + i,
                        f"Synthetic Ultimate {i}",
                        difficulty="普通",
                        partitions=(
                            PartitionRecord(1, "标准阵容构成 (Synthetic)", True),
                        ),
                    )
                    for i in range(60)
                ),
                *(zone(i, f"Synthetic tier {i}") for i in (73, 74, 75)),
            ),
            (),
            True,
            (20,),
        )
        calls, exited = [], []
        entered = asyncio.Event()

        async def fetch(request):
            calls.append(json.loads(request.body)["variables"])
            if len(calls) == 2:
                entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                exited.append(True)

        handler = FFLogsCharacterLookup(None)
        self.addCleanup(handler.close)
        task = asyncio.create_task(
            handler._catalog_rankings(
                SimpleNamespace(http=SimpleNamespace(fetch=fetch)),
                "fflogs_public_cn",
                record(),
                source,
                {
                    "name": "Synthetic Hero",
                    "serverSlug": "cerberus",
                    "serverRegion": "CN",
                    "metric": "rdps",
                },
                handler._lifecycle,
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), 2)
            self.assertTrue(
                {73, 74, 75}.issubset(
                    {
                        value
                        for alias, value in calls[0].items()
                        if alias.startswith("z")
                    }
                )
            )
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual((len(calls), len(exited)), (2, 2))
        await asyncio.sleep(0)
        self.assertEqual(len(calls), 2)

    async def test_known_foreign_encounters_fail_savage_and_ordinary_ultimate_across_batches(
        self,
    ):
        source = OutputMetadata(
            (
                zone(73, "Synthetic tier"),
                *(
                    zone(
                        200 + i,
                        f"Synthetic Ultimate {i}",
                        difficulty="普通",
                        partitions=(
                            PartitionRecord(1, "标准阵容构成 (Synthetic)", True),
                        ),
                    )
                    for i in range(25)
                ),
            ),
            (),
            True,
            (20,),
        )
        by_id = {item.zone_id: item for item in source.zones}
        for invalid_zone in (73, 200):
            with self.subTest(invalid_zone=invalid_zone):
                calls = []

                async def fetch(request):
                    variables = json.loads(request.body)["variables"]
                    calls.append(variables)
                    value = {
                        "id": 42,
                        "name": "Synthetic Hero",
                        "hidden": False,
                        "server": {"name": "Cerberus"},
                    }
                    for alias, zone_id in variables.items():
                        if alias.startswith("z"):
                            index = alias[1:]
                            owner = (
                                by_id[201]
                                if zone_id == invalid_zone
                                else by_id[zone_id]
                            )
                            value[alias] = {
                                "zone": zone_id,
                                "difficulty": variables[f"d{index}"],
                                "partition": variables.get(f"p{index}", -1),
                                "metric": "rdps",
                                "size": 8,
                                "rankings": [
                                    {
                                        "encounter": {
                                            "id": item.encounter_id,
                                            "name": item.name,
                                        },
                                        "rankPercent": 99,
                                        "bestAmount": 12345,
                                        "totalKills": 5,
                                        "bestSpec": "Scholar",
                                    }
                                    for item in owner.encounters
                                ],
                            }
                    return SimpleNamespace(
                        status_code=200,
                        body=json.dumps(
                            {"data": {"characterData": {"character": value}}}
                        ).encode(),
                    )

                handler = FFLogsCharacterLookup(None)
                self.addCleanup(handler.close)
                result = await handler._catalog_rankings(
                    SimpleNamespace(http=SimpleNamespace(fetch=fetch)),
                    "fflogs_public_cn",
                    record(),
                    source,
                    {
                        "name": "Synthetic Hero",
                        "serverSlug": "cerberus",
                        "serverRegion": "CN",
                        "metric": "rdps",
                    },
                    handler._lifecycle,
                )
                self.assertEqual(len(calls), 2)
                invalid = next(
                    group for group in result.groups if group.zone_id == invalid_zone
                )
                self.assertEqual(invalid.rankings[0].query_state, "failed")
                self.assertTrue(invalid.rankings[0].source_error)
                self.assertEqual(
                    invalid.rankings[0].encounter_id,
                    by_id[invalid_zone].encounters[0].encounter_id,
                )
                self.assertIsNone(invalid.rankings[0].rank_percent)
                self.assertFalse(result.catalog_complete)
                self.assertTrue(
                    any(
                        "副本归属不一致" in warning
                        for warning in result.catalog_warnings
                    )
                )

    async def test_contradictory_actual_savage_size_or_difficulty_excludes_entire_tier(
        self,
    ):
        from ygl_test_subject.modules.ff14.features.fflogs import _character_result

        source = OutputMetadata((zone(73, "Synthetic tier"),), (), True, (20,))
        for size, difficulty in ((4, 3), (48, 3), (8, 1)):
            with self.subTest(size=size, difficulty=difficulty):
                value = {
                    "id": 42,
                    "name": "Synthetic Hero",
                    "hidden": False,
                    "server": {"name": "Cerberus"},
                    "z0": {
                        "metric": "rdps",
                        "zone": 73,
                        "difficulty": difficulty,
                        "size": size,
                        "partition": -1,
                        "rankings": [],
                    },
                }
                http = fixture._FakeHttp(
                    [{"data": {"characterData": {"character": value}}}]
                )
                handler = FFLogsCharacterLookup(None)
                self.addCleanup(handler.close)
                evidence = []
                result = await handler._catalog_rankings(
                    SimpleNamespace(http=http),
                    "fflogs_public_cn",
                    record(),
                    source,
                    {
                        "name": "Synthetic Hero",
                        "serverSlug": "cerberus",
                        "serverRegion": "CN",
                        "metric": "rdps",
                    },
                    handler._lifecycle,
                    SimpleNamespace(
                        observe=lambda op, data, **kwargs: evidence.append(data)
                    ),
                )
                self.assertEqual(result.groups, ())
                self.assertEqual(result.rankings, ())
                self.assertTrue(
                    any("已排除" in warning for warning in result.catalog_warnings)
                )
                output = _character_result(
                    result,
                    "https://cn.fflogs.com",
                    "CN",
                    "rdps",
                    None,
                    "cn",
                    metadata=source,
                )
                self.assertIsNone(output.error)
                sections = [
                    item
                    for item in output.document.card.blocks
                    if hasattr(item, "blocks")
                ]
                self.assertEqual([item.title for item in sections], ["绝境战", "零式"])
                self.assertFalse(
                    any(
                        hasattr(block, "rows")
                        for item in sections
                        for block in item.blocks
                    )
                )
                self.assertEqual(
                    evidence[0]["characterData"]["character"]["z0"]["size"], size
                )

    async def test_command_metrics_count_module_fetches_without_identity_or_network_claims(
        self,
    ):
        diagnostics = fixture._Diagnostics()
        metadata = catalog_fixture.expanded_metadata()
        http = fixture._FakeHttp(
            [
                [{"id": 71001, "name": "Cerberus"}],
                [{"name": "SyntheticDC", "region": "China", "worlds": [71001]}],
                *fixture._character_responses(realm="cn"),
                catalog_fixture.expanded_schema(),
                metadata,
                catalog_fixture.catalog_rankings(metadata),
            ]
        )
        handler = FFLogsCharacterLookup(
            fixture._services(http, diagnostics=diagnostics)
        )
        self.addCleanup(handler.close)
        result = await handler.invoke(None, {"command": 'Cerberus "Synthetic Hero"'})
        self.assertIsNone(result.error)
        summary_event = next(
            item
            for item in diagnostics.events
            if item["event_id"] == "FF14RequestSummary"
        )
        manifest_path = (
            Path(__file__).parents[3] / "modules/ff14/yomihime.manifest.json"
        )
        manifest = json.loads(manifest_path.read_bytes())
        emitted = next(
            item
            for item in manifest["modules"][0]["diagnostics"]
            if item["event_id"] == summary_event["event_id"]
        )
        installed_declaration = validate_contract(DiagnosticDeclaration(**emitted))
        self.assertEqual(
            installed_declaration,
            validate_contract(
                next(
                    item
                    for item in DECLARATIONS
                    if item.event_id == summary_event["event_id"]
                )
            ),
        )
        validated, _ = diagnostic_payload(
            installed_declaration.payload_schema, summary_event["payload"]
        )
        self.assertEqual(validated, summary_event["payload"])
        summary = summary_event["payload"]["payload"]
        self.assertEqual(summary["history"]["refresh_wait_ms"], 0)
        self.assertGreater(summary["history"]["planned"], 0)
        self.assertGreater(summary["history"]["attempted"], 0)
        self.assertGreaterEqual(summary["history"]["ranking_fetch_ms"], 0)
        self.assertGreaterEqual(summary["history"]["duty_order_ms"], 0)
        self.assertGreaterEqual(
            summary["history"]["fetch_ms"], summary["history"]["ranking_fetch_ms"]
        )
        self.assertEqual(summary_event["payload"]["source"], "fflogs_public_cn")
        self.assertIsNone(summary_event["payload"]["status"])
        self.assertFalse(summary_event["payload"]["cached"])
        self.assertEqual(
            sum(item["attempts"] for item in summary["operations"]),
            len(http.requests) + len(http.duty_requests) + len(http.kill_requests),
        )
        self.assertEqual((len(http.requests), len(http.duty_requests)), (8, 1))
        self.assertEqual(len(http.kill_requests), 1)
        kill_operation = next(
            row
            for row in summary["operations"]
            if row["operation"] == "FF14PublicKillCounts"
        )
        self.assertEqual(
            (kill_operation["attempts"], kill_operation["cache"]), (1, "module_miss")
        )
        self.assertEqual(summary["network_attempts"], "unknown")
        self.assertEqual(summary["host_oauth"], "unobserved")
        self.assertEqual(
            {
                item["cache"]
                for item in summary["operations"]
                if item["operation"].startswith("Universalis")
            },
            {"module_miss"},
        )
        self.assertNotIn("Synthetic Hero", json.dumps(summary))
        self.assertNotIn("Cerberus", json.dumps(summary))

    async def test_multiple_batches_max_two_inflight_and_exact_partition_queries(self):
        source = catalog()
        source = replace(
            source,
            zones=(
                source.zones[0],
                *(
                    zone(
                        300 + i,
                        f"Synthetic Ultimate {i}",
                        difficulty="普通",
                        partitions=tuple(
                            PartitionRecord(j, f"标准阵容构成 (Synthetic {j})", j == 1)
                            for j in range(1, 11)
                        ),
                    )
                    for i in range(55)
                ),
            ),
        )
        candidates = targets(source)
        calls, active, peak = [], 0, 0

        async def fetch(request):
            nonlocal active, peak
            data = json.loads(request.body)
            calls.append(data)
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            value = {
                "id": 42,
                "name": "Synthetic Hero",
                "hidden": False,
                "server": {"name": "Cerberus"},
            }
            for name, zone_id in data["variables"].items():
                if name.startswith("z"):
                    i = name[1:]
                    target = next(
                        item
                        for item in candidates
                        if key(item)[:3]
                        == (
                            zone_id,
                            data["variables"][f"d{i}"],
                            data["variables"].get(f"p{i}"),
                        )
                    )
                    rank = selection(target)
                    value[name] = {
                        "metric": rank.metric,
                        "zone": rank.zone_id,
                        "difficulty": rank.difficulty_id,
                        "partition": rank.partition,
                        "size": rank.size,
                        "rankings": [
                            {
                                "encounter": {
                                    "id": row.encounter_id,
                                    "name": row.encounter_name,
                                },
                                "rankPercent": 99.9762,
                                "bestAmount": 1000,
                                "totalKills": 1,
                                "bestSpec": "Scholar",
                            }
                            for row in rank.rows
                        ],
                    }
            active -= 1
            return SimpleNamespace(
                status_code=200,
                body=json.dumps(
                    {"data": {"characterData": {"character": value}}}
                ).encode(),
            )

        handler = FFLogsCharacterLookup(None)
        self.addCleanup(handler.close)
        result = await handler._catalog_rankings(
            SimpleNamespace(http=SimpleNamespace(fetch=fetch)),
            "fflogs_public_cn",
            record(),
            source,
            {
                "name": "Synthetic Hero",
                "serverSlug": "cerberus",
                "serverRegion": "CN",
                "metric": "rdps",
            },
            handler._lifecycle,
        )
        self.assertEqual((len(calls), peak), (23, 2))
        self.assertEqual(
            sum(
                sum(key.startswith("z") for key in item["variables"]) for item in calls
            ),
            551,
        )
        self.assertTrue(all("partition: $p" in call["query"] for call in calls))
        self.assertTrue(result.catalog_complete)
