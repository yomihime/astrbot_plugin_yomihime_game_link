"""Real FFLogs candidate facts to declarative cards; all upstream is synthetic."""

from __future__ import annotations

import asyncio
import json
import unittest
from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch

from ygl_test_subject.core.contracts.cards import validate_card
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.public_result import public_fact_strings

from modules.ff14.features.fflogs import FFLogsCharacterLookup
from modules.ff14.features.fflogs_cards import (
    CATALOG_LIMITATION,
    amount,
    character_card,
    kill_count,
    percentile,
    selection_title,
)
from modules.ff14.features.fflogs_catalog import (
    CHARACTER_METADATA_QUERY,
    OUTPUT_METADATA_QUERY,
    parse_character_metadata,
    parse_graphql_envelope,
    parse_output_metadata,
    select_character_zones,
)
from modules.ff14.features.fflogs_chat import character_facts
from modules.ff14.features.fflogs_models import (
    FFLogsPayloadError,
    parse_public_character,
)
from yomihime_game_link_sdk import cards
from yomihime_game_link_sdk.results import ErrorCode, FactDocument, ResultStatus

from . import test_fflogs as fixture

TARGET = {"realm": "cn", "server": "Cerberus", "character": "Synthetic Hero"}


def character():
    return parse_public_character(
        fixture._character()["data"]["characterData"]["character"]
    )


def metadata():
    data = fixture._output_metadata()
    data["data"]["worldData"]["zones"][0]["id"] = 73
    return data


def expanded_schema():
    value = fixture._output_schema()
    data = value["data"]
    data["worldDataType"]["fields"].append(
        fixture._field("expansions", fixture._list_type("Expansion"))
    )
    data["encounterType"]["fields"].append(
        fixture._field("zone", fixture._type("OBJECT", "Zone"))
    )
    data["expansionType"] = {
        "name": "Expansion",
        "fields": [
            fixture._field("id", fixture._type("SCALAR", "Int")),
            fixture._field("name", fixture._type("SCALAR", "String")),
            fixture._field("zones", fixture._list_type("Zone")),
        ],
    }
    return value


def expanded_metadata(*, ultimate_count=7):
    value = fixture._output_metadata()
    zones = []
    for zone_id, expansion, category, count in (
        *((100 + index, 10, "Ultimate", 1) for index in range(ultimate_count)),
        (73, 20, "Savage", 4),
        (74, 20, "Savage", 4),
        (75, 20, "Savage", 5),
        (72, 10, "Savage", 4),
        (9999, 999, "Savage", 4),
    ):
        zones.append(
            {
                "id": zone_id,
                "name": f"Synthetic zone {zone_id}",
                "difficulties": [{"id": 3, "name": category}],
                "encounters": [
                    {
                        "id": zone_id * 10 + index,
                        "name": f"Synthetic {zone_id}-{index}",
                        "zone": {"id": zone_id},
                    }
                    for index in range(count)
                ],
                "_expansion": expansion,
            }
        )
    # A legacy alias is not a new encounter: source explicitly names its owner.
    zones.append(
        {
            "id": 43,
            "name": "Synthetic legacy alias",
            "difficulties": [{"id": 3, "name": "Ultimate"}],
            "encounters": [dict(zones[0]["encounters"][0])],
            "_expansion": 10,
        }
    )
    value["data"]["worldData"] = {
        "zones": zones,
        "expansions": [
            {
                "id": expansion,
                "name": f"Synthetic expansion {expansion}",
                "zones": [
                    {"id": zone["id"]}
                    for zone in zones
                    if zone["_expansion"] == expansion
                ],
            }
            for expansion in (10, 20, 999)
        ],
    }
    return value


def catalog_rankings(payload, *, failed_zone=None, empty_zone=None):
    catalog = parse_character_metadata(payload["data"])
    targets = select_character_zones(catalog, 73)[:12]
    value = {
        "id": 42,
        "name": "Synthetic Hero",
        "hidden": False,
        "server": {"name": "Cerberus"},
    }
    for index, (zone, difficulty, _) in enumerate(targets):
        value[f"z{index}"] = (
            None
            if zone.zone_id == failed_zone
            else {
                "metric": "rdps",
                "zone": zone.zone_id,
                "difficulty": difficulty.difficulty_id,
                "partition": -1,
                "size": 8,
                "rankings": []
                if zone.zone_id == empty_zone
                else [
                    {
                        "encounter": {"id": row.encounter_id, "name": row.name},
                        "rankPercent": 100 if index == 0 else 99.96,
                        "bestAmount": 12345.5,
                        "totalKills": 17,
                        "bestSpec": "SyntheticSpec",
                    }
                    for row in zone.encounters
                ],
            }
        )
    result = {"data": {"characterData": {"character": value}}}
    if failed_zone is not None:
        result["errors"] = [{"message": "Synthetic alias failure; never publish this"}]
    return result


class FFLogsCardTests(unittest.TestCase):
    def test_raw_grades_rounding_missing_and_abnormal_are_distinct(self):
        for raw, color in (
            (0, "#666666"),
            (25, "#1eff00"),
            (50, "#0070ff"),
            (75, "#a335ee"),
            (95, "#ff8000"),
            (99, "#e268a8"),
        ):
            with self.subTest(raw=raw):
                span = percentile(raw).spans[0]
                self.assertEqual(span.color, color)
                self.assertEqual(span.text, f"{raw:.1f}%")
        rounded = percentile(Decimal("99.999999999999999999999")).spans[0]
        self.assertEqual(rounded.text, "100.0%")
        self.assertEqual(rounded.color, "#e268a8")
        self.assertNotEqual(rounded.emphasis, "gold")
        self.assertEqual(percentile(100).spans[0].emphasis, "gold")
        self.assertEqual(percentile(None).spans[0].text, "—")
        for invalid in (True, -1, 101, "NaN", "unknown"):
            self.assertEqual(percentile(invalid).spans[0].text, "ERR")
        self.assertEqual(amount(12345.5).spans[0].text, "12,346")
        self.assertEqual(amount(None).spans[0].text, "—")
        self.assertEqual(kill_count(None).spans[0].text, "—")
        self.assertEqual(kill_count(0).spans[0].text, "0")
        self.assertEqual(kill_count(True).spans[0].text, "ERR")

    def test_exact_json_percentile_never_becomes_raw_gold_through_float(self):
        body = json.dumps(fixture._character()).replace(
            "93.5", "99.999999999999999999999"
        )
        payload = parse_graphql_envelope(body.encode(), exact_numbers=True)
        record = parse_public_character(payload["characterData"]["character"])
        card = character_card(
            record,
            realm="cn",
            host="https://cn.fflogs.com",
            limit=24,
            explicit_selection=False,
        )
        span = card.blocks[0].blocks[0].rows[0][4].spans[0]
        self.assertEqual(span.text, "100.0%")
        self.assertEqual(span.color, "#e268a8")
        self.assertNotEqual(span.emphasis, "gold")

    def test_count_uses_only_optional_source_field_not_size_or_samples(self):
        raw = fixture._character()["data"]["characterData"]["character"]
        row = raw["rankings"]["rankings"][0]
        self.assertEqual(parse_public_character(raw).rankings[0].total_kills, 80)
        row.pop("totalKills")
        raw["rankings"]["size"] = 999
        self.assertIsNone(parse_public_character(raw).rankings[0].total_kills)
        row["totalKills"] = 0
        self.assertEqual(parse_public_character(raw).rankings[0].total_kills, 0)
        for invalid in (True, -1, 1.5, "80"):
            row["totalKills"] = invalid
            with self.assertRaises(FFLogsPayloadError):
                parse_public_character(raw)

    def test_dynamic_zone_title_uses_exact_catalog_identity_and_unknown_stays_unknown(
        self,
    ):
        record = character()
        source = parse_output_metadata(metadata()["data"])
        self.assertEqual(selection_title(record, source), "合成高难副本")
        changed = replace(
            source, zones=(replace(source.zones[0], name="A future provider zone"),)
        )
        self.assertEqual(selection_title(record, changed), "A future provider zone")
        self.assertEqual(
            selection_title(replace(record, zone_id=999), source), "本次查询范围"
        )
        card = character_card(
            record,
            realm="cn",
            host="https://cn.fflogs.com",
            limit=24,
            explicit_selection=False,
            metadata=source,
        )
        validate_card(card)
        table = card.blocks[0].blocks[0]
        self.assertIsInstance(table, cards.Table)
        self.assertEqual(len(table.columns), 5)
        self.assertEqual(
            (table.columns[-1].align, table.columns[-1].valign), ("right", "center")
        )
        self.assertEqual(len(table.rows), 1)
        self.assertIn(
            CATALOG_LIMITATION,
            [block.content for block in card.blocks if isinstance(block, cards.Text)],
        )

    def test_empty_card_and_many_rows_are_not_fabricated(self):
        empty = replace(character(), rankings=())
        card = character_card(
            empty,
            realm="global",
            host="https://www.fflogs.com",
            limit=24,
            explicit_selection=False,
        )
        self.assertIsInstance(card.blocks[0].blocks[0], cards.Text)
        self.assertNotIn("ERR", card.blocks[0].blocks[0].content)
        rows = replace(character(), rankings=character().rankings * 24)
        validate_card(
            character_card(
                rows,
                realm="cn",
                host="https://cn.fflogs.com",
                limit=24,
                explicit_selection=False,
            )
        )
        overflow = replace(
            character(),
            rankings=character().rankings * 41,
            catalog_warnings=("来源失败",) * 5,
        )
        card = character_card(
            overflow,
            realm="cn",
            host="https://cn.fflogs.com",
            limit=40,
            explicit_selection=False,
        )
        captions = [
            block.content for block in card.blocks if isinstance(block, cards.Text)
        ]
        self.assertIn("共 41 项，本图仅显示前 40 项。", captions)
        self.assertTrue(any("另有 2 项" in text for text in captions))

    def test_extended_facts_remain_bounded_json_and_keep_source_scope(self):
        from types import SimpleNamespace

        server = SimpleNamespace(
            region="CN", name="Cerberus", region_name="China", subregion="Synthetic"
        )
        record = replace(character(), rankings=character().rankings * 20)
        facts = character_facts(
            record, server, "cn", "rdps", None, "https://cn.fflogs.com", "2026-10-16"
        )
        list(public_fact_strings(FactDocument({"fflogs": facts})))
        self.assertEqual(facts["rankings"][0]["total_kills"], 80)
        self.assertFalse(
            facts["limitations"]["complete_ultimate_current_expansion_catalog"]
        )
        self.assertEqual(
            facts["limitations"]["total_kills_scope"], "returned_selection_not_lifetime"
        )


class FFLogsCardSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_and_cancel_fence_the_aliased_catalog_request(self):
        for action in ("close", "cancel"):
            with self.subTest(action=action):
                catalog = expanded_metadata()
                http = fixture._FakeHttp(
                    [
                        *fixture._character_responses(realm="cn"),
                        expanded_schema(),
                        catalog,
                        catalog_rankings(catalog),
                    ]
                )
                entered, release, exited = (
                    asyncio.Event(),
                    asyncio.Event(),
                    asyncio.Event(),
                )
                original = http.fetch

                async def fetch(request):
                    if "FF14CatalogCharacter" in json.loads(request.body)["query"]:
                        entered.set()
                        try:
                            await release.wait()
                        finally:
                            exited.set()
                    return await original(request)

                http.fetch = fetch
                handler = FFLogsCharacterLookup(fixture._services(http))
                task = asyncio.create_task(handler.invoke(None, TARGET))
                await entered.wait()
                if action == "close":
                    handler.close()
                    release.set()
                    result = await task
                    self.assertIs(result.error.code, ErrorCode.MODULE_UNAVAILABLE)
                    self.assertIsNone(result.document)
                    self.assertEqual(handler._metadata_cache, {})
                else:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                self.assertTrue(exited.is_set())

    async def test_aliased_timeout_preserves_initial_rows(self):
        catalog = expanded_metadata()
        http = fixture._FakeHttp(
            [
                *fixture._character_responses(realm="cn"),
                expanded_schema(),
                catalog,
            ]
        )
        original = http.fetch
        cancelled = asyncio.Event()

        async def fetch(request):
            if "FF14CatalogCharacter" in json.loads(request.body)["query"]:
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            return await original(request)

        http.fetch = fetch
        with patch(
            "modules.ff14.features.fflogs.CATALOG_RANKING_DEADLINE_SECONDS", 0.01
        ):
            result = await FFLogsCharacterLookup(fixture._services(http)).invoke(
                None, TARGET
            )
        self.assertTrue(cancelled.is_set())
        self.assertIsNone(result.error)
        self.assertEqual(len(result.document.card.blocks[0].blocks[0].rows), 1)
        self.assertTrue(any("保留" in warning for warning in result.warnings))

    async def test_alias_budget_and_duplicate_rows_are_explicit(self):
        catalog = expanded_metadata(ultimate_count=13)
        batch = catalog_rankings(catalog)
        alias = batch["data"]["characterData"]["character"]["z1"]
        alias["rankings"].append(dict(alias["rankings"][0]))
        http = fixture._FakeHttp(
            [
                *fixture._character_responses(
                    realm="cn", character=fixture._character(difficulty=3)
                ),
                expanded_schema(),
                catalog,
                batch,
            ]
        )
        result = await FFLogsCharacterLookup(fixture._services(http)).invoke(
            None, TARGET
        )
        variables = json.loads(http.requests[-1].body)["variables"]
        self.assertEqual(len([key for key in variables if key.startswith("z")]), 12)
        self.assertTrue(any("12" in warning for warning in result.warnings))
        rows = result.document.card.blocks[0].blocks[0].rows
        self.assertEqual(rows[1][-1].spans[0].text, "ERR")

    async def test_conditional_live_path_selects_all_provider_ultimates_and_default_expansion_tiers(
        self,
    ):
        catalog = expanded_metadata()
        batch = catalog_rankings(catalog)
        batch["data"]["characterData"]["character"]["z0"]["partition"] = 2
        http = fixture._FakeHttp(
            [
                *fixture._character_responses(
                    realm="cn", character=fixture._character(difficulty=3)
                ),
                expanded_schema(),
                catalog,
                batch,
            ]
        )
        result = await FFLogsCharacterLookup(fixture._services(http)).invoke(
            None, TARGET
        )
        validate_contract(result)
        self.assertIsNone(result.error)
        self.assertIs(result.status, ResultStatus.PARTIAL_SUCCESS)
        sections = [
            block
            for block in result.document.card.blocks
            if isinstance(block, cards.Section)
        ]
        self.assertEqual([section.title for section in sections], ["绝境战", "零式"])
        self.assertEqual([len(section.blocks[0].rows) for section in sections], [7, 13])
        self.assertEqual(sections[1].subtitle, "Synthetic expansion 20")
        text = "\n".join(
            block.text
            for block in result.document.ordered_blocks
            if hasattr(block, "text")
        )
        self.assertIn("各副本 FFLogs 默认分区", text)
        self.assertNotIn("全部分区", text)
        self.assertEqual(len(http.requests), 6)
        self.assertEqual(
            json.loads(http.requests[-2].body)["query"], CHARACTER_METADATA_QUERY
        )
        query = json.loads(http.requests[-1].body)
        self.assertIn("FF14CatalogCharacter", query["query"])
        self.assertEqual(
            len([key for key in query["variables"] if key.startswith("z")]), 10
        )
        self.assertNotIn(9999, query["variables"].values())
        self.assertNotIn(72, query["variables"].values())
        self.assertNotIn(43, query["variables"].values())

    async def test_unknown_returned_encounter_warns_but_known_alias_only_dedupes(self):
        for encounter_id, should_warn in ((999999, True), (1010, False)):
            with self.subTest(encounter_id=encounter_id):
                catalog = expanded_metadata()
                batch = catalog_rankings(catalog)
                rows = batch["data"]["characterData"]["character"]["z0"]["rankings"]
                rows.append(
                    {
                        **rows[0],
                        "encounter": {
                            "id": encounter_id,
                            "name": "Synthetic additional encounter",
                        },
                    }
                )
                http = fixture._FakeHttp(
                    [
                        *fixture._character_responses(
                            realm="cn", character=fixture._character(difficulty=3)
                        ),
                        expanded_schema(),
                        catalog,
                        batch,
                    ]
                )
                result = await FFLogsCharacterLookup(fixture._services(http)).invoke(
                    None, TARGET
                )
                validate_contract(result)
                self.assertIsNone(result.error)
                self.assertIs(result.status, ResultStatus.PARTIAL_SUCCESS)
                self.assertEqual(
                    any("1 项目录未知记录" in warning for warning in result.warnings),
                    should_warn,
                )
                captions = [
                    block.content
                    for block in result.document.card.blocks
                    if isinstance(block, cards.Text)
                ]
                self.assertEqual(
                    any("1 项目录未知记录" in caption for caption in captions),
                    should_warn,
                )
                self.assertEqual(
                    [
                        len(section.blocks[0].rows)
                        for section in result.document.card.blocks[:2]
                    ],
                    [7, 13],
                )

    async def test_partial_alias_error_retains_success_and_distinguishes_no_records(
        self,
    ):
        catalog = expanded_metadata()
        http = fixture._FakeHttp(
            [
                *fixture._character_responses(
                    realm="cn", character=fixture._character(difficulty=3)
                ),
                expanded_schema(),
                catalog,
                catalog_rankings(catalog, failed_zone=74, empty_zone=75),
            ]
        )
        result = await FFLogsCharacterLookup(fixture._services(http)).invoke(
            None, TARGET
        )
        sections = [
            block
            for block in result.document.card.blocks
            if isinstance(block, cards.Section)
        ]
        ultimate, savage = [section.blocks[0].rows for section in sections]
        self.assertEqual(ultimate[0][-1].spans[0].emphasis, "gold")
        self.assertEqual(savage[0][-1].spans[0].text, "100.0%")
        self.assertNotEqual(savage[0][-1].spans[0].emphasis, "gold")
        self.assertEqual(savage[4][-1].spans[0].text, "ERR")
        self.assertEqual(savage[8][-1].spans[0].text, "—")
        self.assertTrue(
            any("Synthetic zone 74" in warning for warning in result.warnings)
        )
        self.assertFalse(
            any("never publish this" in warning for warning in result.warnings)
        )

    def test_dynamic_selection_grows_and_never_uses_max_expansion_or_name_dedupe(self):
        data = parse_character_metadata(expanded_metadata(ultimate_count=8)["data"])
        targets = select_character_zones(data, 73)
        self.assertEqual(sum(kind == "ultimate" for _, _, kind in targets), 8)
        self.assertEqual(sum(kind == "savage" for _, _, kind in targets), 3)
        self.assertEqual(sum(len(zone.encounters) for zone, _, _ in targets), 21)
        self.assertEqual(select_character_zones(data, 123456), ())
        changed = replace(
            data,
            zones=tuple(
                replace(zone, difficulties=()) if zone.expansion_id == 20 else zone
                for zone in data.zones
            ),
        )
        selected = select_character_zones(changed, 73)
        self.assertFalse(any(kind == "savage" for _, _, kind in selected))
        self.assertFalse(any(zone.zone_id in (72, 9999) for zone, _, _ in selected))

    async def test_actual_handler_enriches_real_rows_with_fixed_catalog_and_caches(
        self,
    ):
        http = fixture._FakeHttp(
            [
                *fixture._character_responses(realm="cn"),
                fixture._output_schema(),
                metadata(),
                *fixture._character_responses(realm="cn"),
            ]
        )
        handler = FFLogsCharacterLookup(fixture._services(http))
        first = await handler.invoke(None, TARGET)
        validate_contract(first)
        self.assertEqual(first.document.card.blocks[0].title, "合成高难副本")
        self.assertIs(first.status, ResultStatus.PARTIAL_SUCCESS)
        second = await handler.invoke(None, TARGET)
        self.assertEqual(second.document.card.blocks[0].title, "合成高难副本")
        self.assertEqual(len(http.requests), 8)
        self.assertEqual(
            sum(
                json.loads(req.body)["query"] == OUTPUT_METADATA_QUERY
                for req in http.requests
            ),
            1,
        )
        self.assertTrue(
            all(
                req.source_id == "fflogs_public_cn" and req.path == "/api/v2/client"
                for req in http.requests
            )
        )
        handler.close()
        self.assertEqual(handler._metadata_cache, {})

    async def test_unknown_metadata_preserves_actual_partial_results(self):
        http = fixture._FakeHttp(
            [*fixture._character_responses(realm="cn"), {"data": {}}]
        )
        result = await FFLogsCharacterLookup(fixture._services(http)).invoke(
            None, TARGET
        )
        self.assertIsNone(result.error)
        self.assertIs(result.status, ResultStatus.PARTIAL_SUCCESS)
        self.assertEqual(result.document.card.blocks[0].title, "本次查询范围")
        self.assertEqual(len(result.document.card.blocks[0].blocks[0].rows), 1)
        self.assertEqual(len(http.requests), 4)

    async def test_no_result_keeps_no_records_error_when_catalog_is_unavailable(self):
        body = fixture._character()
        body["data"]["characterData"]["character"]["rankings"]["rankings"] = []
        http = fixture._FakeHttp(
            fixture._character_responses(realm="cn", character=body)
        )
        result = await FFLogsCharacterLookup(fixture._services(http)).invoke(
            None, TARGET
        )
        self.assertIs(result.error.code, ErrorCode.NO_RECORDS)
        self.assertIsNone(result.document)
        self.assertEqual(len(http.requests), 4)

    async def test_close_during_metadata_prevents_second_request_or_stale_card(self):
        http = fixture._FakeHttp(
            [
                *fixture._character_responses(realm="cn"),
                fixture._output_schema(),
                metadata(),
            ]
        )
        entered, release = asyncio.Event(), asyncio.Event()
        original = http.fetch

        async def fetch(request):
            if "FF14OutputMetadataShape" in json.loads(request.body)["query"]:
                entered.set()
                await release.wait()
            return await original(request)

        http.fetch = fetch
        handler = FFLogsCharacterLookup(fixture._services(http))
        task = asyncio.create_task(handler.invoke(None, TARGET))
        await entered.wait()
        handler.close()
        release.set()
        result = await task
        self.assertIs(result.error.code, ErrorCode.MODULE_UNAVAILABLE)
        self.assertEqual(len(http.requests), 4)
        self.assertEqual(handler._metadata_cache, {})

    async def test_metadata_timeout_is_bounded_and_does_not_lose_rankings(self):
        http = fixture._FakeHttp(fixture._character_responses(realm="cn"))
        original = http.fetch
        cancelled = asyncio.Event()

        async def fetch(request):
            if "FF14OutputMetadataShape" in json.loads(request.body)["query"]:
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            return await original(request)

        http.fetch = fetch
        with patch("modules.ff14.features.fflogs.METADATA_DEADLINE_SECONDS", 0.01):
            result = await FFLogsCharacterLookup(fixture._services(http)).invoke(
                None, TARGET
            )
        self.assertTrue(cancelled.is_set())
        self.assertIsNone(result.error)
        self.assertIs(result.status, ResultStatus.PARTIAL_SUCCESS)


if __name__ == "__main__":
    unittest.main()
