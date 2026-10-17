"""Synthetic history-source requests; no real identities or ranking fixtures."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from types import SimpleNamespace

from modules.ff14.features.fflogs import (
    FFLogsCharacterLookup,
    _assemble_catalog_rankings,
    _bounded_catalog_targets,
    _catalog_query_targets,
    _target_key,
)
from modules.ff14.features.fflogs_cards import HISTORY_INCOMPLETE, ranking_period
from modules.ff14.features.fflogs_catalog import select_character_zones
from modules.ff14.features.fflogs_models import EncounterRecord, OutputMetadata
from tests.modules.ff14 import test_fflogs as handler_fixture
from tests.modules.ff14 import test_fflogs_aggregation as fixture
from tests.modules.ff14 import test_fflogs_cards as card_fixture
from yomihime_game_link_sdk.results import ResultStatus


def catalog():
    return OutputMetadata(
        (
            fixture.zone(73, "Synthetic anchor tier"),
            fixture.zone(74, "Synthetic second tier"),
            fixture.zone(75, "Synthetic third tier"),
            *(
                fixture.zone(
                    900 + index,
                    f"Synthetic Ultimate source {index}",
                    expansion=10 if index < 7 else 20,
                    difficulty="普通",
                    encounters=(
                        EncounterRecord(
                            9000 + index, "Synthetic Shared Ultimate", 900 + index
                        ),
                    ),
                )
                for index in range(10)
            ),
        ),
        (),
        True,
        (20, 10),
    )


class HistoryPlanTests(unittest.TestCase):
    def test_all_historical_standard_sources_and_three_default_tiers(
        self,
    ):
        targets = _catalog_query_targets(select_character_zones(catalog(), 73))
        self.assertEqual(len(targets), 13)
        self.assertEqual(len(_bounded_catalog_targets(targets)), 13)
        ultimate = [target for target in targets if target[2] == "ultimate"]
        savage = [target for target in targets if target[2] == "savage"]
        self.assertEqual(
            {target[0].zone_id for target in ultimate}, set(range(900, 910))
        )
        self.assertEqual(
            {_target_key(target)[2:] for target in ultimate},
            {(3, "rdps", "Historical", None)},
        )
        self.assertEqual(
            {_target_key(target)[2:] for target in savage}, {(None, "rdps", None, None)}
        )
        target = ultimate[0]
        keys = {_target_key((*target[:3], partition)) for partition in (None, -1, 3)}
        self.assertEqual(len(keys), 3)
        self.assertNotEqual(_target_key(target), _target_key(target, "ndps"))

    def test_raw_winner_keeps_old_source_entire_row_and_never_invents_row_partition(
        self,
    ):
        source = catalog()
        targets = _catalog_query_targets(select_character_zones(source, 73))
        outcomes = {
            _target_key(target): fixture.selection(target, raw="98.9971")
            for target in targets
        }
        old = next(target for target in targets if target[0].zone_id == 900)
        outcomes[_target_key(old)] = fixture.selection(
            old, raw="99.99999999999999999", amount=0, kills=6, job="RedMage"
        )
        result = _assemble_catalog_rankings(fixture.record(), source, targets, outcomes)
        row = result.groups[0].rankings[0]
        self.assertEqual(
            (row.encounter_id, row.best_amount, row.total_kills, row.spec),
            (9000, 0, 6, "RedMage"),
        )
        self.assertEqual(str(row.rank_percent_exact), "99.99999999999999999")
        self.assertEqual(row.origin.partition, 3)
        self.assertEqual(row.origin.requested_partition, 3)
        self.assertEqual(row.origin.requested_timeframe, "Historical")
        self.assertIsNone(row.origin.requested_spec_name)
        self.assertIsNone(row.origin.partition_name)
        self.assertEqual(ranking_period(row), "资料片：Synthetic expansion 10")
        self.assertTrue(row.history_complete)
        self.assertTrue(result.catalog_complete)
        self.assertNotIn(HISTORY_INCOMPLETE, result.catalog_warnings)
        for state in ("failed", "not_queried"):
            changed = dict(outcomes)
            changed[_target_key(old)] = state
            partial = _assemble_catalog_rankings(
                fixture.record(), source, targets, changed
            )
            self.assertEqual(partial.groups[0].rankings[0].query_state, "record")
            self.assertFalse(partial.groups[0].rankings[0].history_complete)
            self.assertIn(HISTORY_INCOMPLETE, partial.catalog_warnings)
            self.assertFalse(partial.catalog_complete)

    def test_base_actual_partition_and_other_requested_keys_cannot_complete_explicit_scope(
        self,
    ):
        source = catalog()
        targets = _catalog_query_targets(select_character_zones(source, 73))
        old = next(target for target in targets if target[0].zone_id == 900)
        initial = fixture.selection(old)
        base = replace(
            fixture.record(),
            zone_id=900,
            difficulty_id=1,
            partition=3,
            rankings=initial.rows,
        )
        outcomes = {
            _target_key(target): fixture.selection(target)
            for target in targets
            if target != old
        }
        for requested in (None, -1):
            changed = dict(outcomes)
            changed[_target_key((*old[:3], requested))] = initial
            result = _assemble_catalog_rankings(base, source, targets, changed)
            self.assertFalse(result.groups[0].rankings[0].history_complete)
            self.assertIn(HISTORY_INCOMPLETE, result.catalog_warnings)
            self.assertFalse(result.catalog_complete)
            self.assertFalse(
                any(row.origin and row.origin.zone_id == 900 for row in result.rankings)
            )


class HistoryBatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_invoke_current_empty_or_null_zero_still_loads_old_history(
        self,
    ):
        for current_empty in (True, False):
            with self.subTest(current_empty=current_empty):
                metadata = card_fixture.expanded_metadata(ultimate_count=1)
                old_source = metadata["data"]["worldData"]["zones"][0]
                old_source["name"] = "合成旧资料片绝境战"
                old_source["encounters"][0]["name"] = "合成旧资料片绝境战"
                base = handler_fixture._character(zone_id=73, difficulty=3)
                current = base["data"]["characterData"]["character"]["rankings"][
                    "rankings"
                ]
                if current_empty:
                    current.clear()
                else:
                    current[0].update(
                        rankPercent=None, bestAmount=0, totalKills=0, bestSpec=None
                    )
                batch = card_fixture.catalog_rankings(metadata)
                aliases = batch["data"]["characterData"]["character"]
                for alias, selection in aliases.items():
                    if not alias.startswith("z"):
                        continue
                    if selection["zone"] == 100:
                        selection["partition"] = 3
                        selection["rankings"][0].update(
                            rankPercent=93.4567,
                            bestAmount=7654.321,
                            totalKills=6,
                            bestSpec="RedMage",
                        )
                    else:
                        selection["rankings"] = []
                http = handler_fixture._FakeHttp(
                    [
                        *handler_fixture._character_responses(
                            realm="cn", character=base
                        ),
                        card_fixture.expanded_schema(),
                        metadata,
                        batch,
                    ]
                )
                handler = FFLogsCharacterLookup(handler_fixture._services(http))
                self.addCleanup(handler.close)
                result = await handler.invoke(None, card_fixture.TARGET)
                self.assertIs(result.status, ResultStatus.SUCCESS)
                self.assertIsNone(result.error)
                self.assertEqual((len(http.requests), len(http.duty_requests)), (6, 1))
                wire = json.loads(
                    next(
                        request.body
                        for request in reversed(http.requests)
                        if request.body is not None
                    )
                )
                self.assertIn("FF14CatalogCharacter", wire["query"])
                self.assertEqual(wire["variables"]["p0"], 3)
                self.assertEqual(wire["variables"]["historicalTimeframe"], "Historical")
                sections = [
                    block
                    for block in result.document.card.blocks
                    if hasattr(block, "blocks")
                ]
                row = card_fixture.section_rows(sections[0])[0]
                self.assertIn("合成旧资料片绝境战", row[0])
                self.assertEqual(row[1], "赤魔法师")
                self.assertEqual(row[-1].spans[0].text, "93.4%")
                self.assertEqual(row[3].spans[0].text, "7,654")
                self.assertEqual(row[4].spans[0].text, "6")
                self.assertNotIn(HISTORY_INCOMPLETE, str(result.document.card))
                self.assertNotIn(HISTORY_INCOMPLETE, result.warnings)

    async def test_same_zone_mixed_categories_keep_requested_scopes_and_alias_results_separate(
        self,
    ):
        mixed = fixture.zone(
            950,
            "Synthetic mixed duty",
            encounters=(
                EncounterRecord(9501, "Synthetic Mixed Ultimate", 950),
                EncounterRecord(9502, "Synthetic ordinary boss", 950),
            ),
        )
        metadata = OutputMetadata(
            (mixed, fixture.zone(73, "Synthetic anchor tier")), (), True, (20,)
        )
        requests = []
        actual_partition = [3]

        async def fetch(request):
            data = json.loads(request.body)
            requests.append(data)
            variables = data["variables"]
            value = dict(
                id=42, name="Synthetic Hero", hidden=False, server={"name": "Cerberus"}
            )
            for alias, zone_id in variables.items():
                if not alias.startswith("z"):
                    continue
                index = alias[1:]
                requested = variables.get("p" + index)
                members = (
                    mixed.encounters if zone_id == 950 else metadata.zones[1].encounters
                )
                value[alias] = dict(
                    metric="rdps",
                    zone=zone_id,
                    difficulty=variables["d" + index],
                    partition=actual_partition[0],
                    size=8,
                    rankings=[
                        dict(
                            encounter={"id": item.encounter_id, "name": item.name},
                            rankPercent=17 if requested == 3 else 99,
                            bestAmount=1700 if requested == 3 else 9900,
                            totalKills=1,
                            bestSpec="Scholar",
                        )
                        for item in members
                    ],
                )
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
            fixture.record(),
            metadata,
            dict(
                name="Synthetic Hero",
                serverSlug="cerberus",
                serverRegion="CN",
                metric="rdps",
            ),
            handler._lifecycle,
        )
        self.assertEqual(len(requests), 1)
        wire = requests[0]
        self.assertEqual(wire["variables"]["historicalTimeframe"], "Historical")
        self.assertIn("$historicalTimeframe: RankingTimeframeType!", wire["query"])
        self.assertNotIn("specName", wire["query"])
        self.assertNotIn("specName", wire["variables"])
        for alias, zone_id in wire["variables"].items():
            if not alias.startswith("z"):
                continue
            index = alias[1:]
            arguments = wire["query"].split(alias + ": zoneRankings(")[1].split(")")[0]
            if "p" + index in wire["variables"]:
                self.assertEqual(wire["variables"]["p" + index], 3)
                self.assertIn("timeframe: $historicalTimeframe", arguments)
            else:
                self.assertNotIn("partition:", arguments)
                self.assertNotIn("timeframe:", arguments)
        ultimate = result.groups[0].rankings[0]
        savage = next(
            group
            for group in result.groups
            if group.category == "savage" and group.zone_id == 950
        ).rankings[0]
        self.assertEqual((ultimate.rank_percent, ultimate.best_amount), (17, 1700))
        self.assertEqual((savage.rank_percent, savage.best_amount), (99, 9900))
        self.assertEqual(ultimate.origin.requested_partition, 3)
        self.assertIsNone(savage.origin.requested_partition)
        self.assertEqual(ultimate.origin.partition, savage.origin.partition)
        self.assertTrue(result.catalog_complete)
        actual_partition[0] = 0
        rejected = await handler._catalog_rankings(
            SimpleNamespace(http=SimpleNamespace(fetch=fetch)),
            "fflogs_public_cn",
            fixture.record(),
            metadata,
            dict(
                name="Synthetic Hero",
                serverSlug="cerberus",
                serverRegion="CN",
                metric="rdps",
            ),
            handler._lifecycle,
        )
        self.assertEqual(rejected.groups[0].rankings[0].query_state, "failed")
        self.assertIsNone(rejected.groups[0].rankings[0].origin)
        self.assertIn(HISTORY_INCOMPLETE, rejected.catalog_warnings)
        self.assertFalse(rejected.catalog_complete)
