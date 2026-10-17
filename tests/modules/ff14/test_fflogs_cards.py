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

from modules.ff14.features.fflogs import (
    CATALOG_DIAGNOSTIC_BYTES,
    FFLogsCharacterLookup,
    _bounded_catalog_targets,
    _catalog_diagnostic,
)
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
    CharacterRankingGroup,
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


def section_rows(section):
    return tuple(
        row
        for block in section.blocks
        if isinstance(block, cards.Table)
        for row in block.rows
    )


async def offline_catalog_reference(handler, invocation=None, parameters=None):
    """Retained mechanics tests now exercise the restored production route."""
    return await handler.invoke(invocation, parameters or TARGET)


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
    data["difficultyType"]["fields"].append(
        fixture._field(
            "sizes",
            {"kind": "LIST", "name": None, "ofType": fixture._type("SCALAR", "Int")},
        )
    )
    data["zoneType"]["fields"].extend(
        (
            fixture._field("frozen", fixture._type("SCALAR", "Boolean")),
            fixture._field("partitions", fixture._list_type("Partition")),
        )
    )
    data["partitionType"] = {
        "name": "Partition",
        "fields": [
            fixture._field(name, fixture._type("SCALAR", scalar))
            for name, scalar in (
                ("id", "Int"),
                ("name", "String"),
                ("default", "Boolean"),
            )
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
                "name": f"Synthetic zone {zone_id}"
                + (" Ultimate" if category == "Ultimate" else ""),
                "difficulties": [{"id": 3, "name": category, "sizes": [8]}],
                "frozen": False,
                "partitions": [
                    {"id": 3, "name": "标准阵容构成 (Synthetic)", "default": True}
                ]
                if category == "Ultimate"
                else [],
                "encounters": [
                    {
                        "id": zone_id * 10 + index,
                        "name": f"Synthetic Ultimate {zone_id}-{index}"
                        if category == "Ultimate"
                        else f"Synthetic {zone_id}-{index}",
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
            for expansion in (999, 20, 10)
        ],
    }
    return value


def catalog_rankings(
    payload, *, failed_zone=None, empty_zone=None, target_zone_ids=None, batch_index=0
):
    catalog = parse_character_metadata(payload["data"])
    targets = _bounded_catalog_targets(select_character_zones(catalog, 73))
    if target_zone_ids is not None:
        targets = tuple(
            target for target in targets if target[0].zone_id in target_zone_ids
        )
    else:
        targets = targets[batch_index * 12 : (batch_index + 1) * 12]
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
                "partition": 3 if "Ultimate" in zone.name else -1,
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
    def test_plain_percentile_and_card_share_exact_truncation_and_sentinels(self):
        from modules.ff14.features.fflogs_cards import percentile_text

        for value, text in (
            (Decimal("99.9762"), "99.9%"),
            ("98.9971", "98.9%"),
            (100, "100.0%"),
            (0, "0.0%"),
            (None, "—"),
            ("NaN", "ERR"),
            (True, "ERR"),
            (-1, "ERR"),
            (101, "ERR"),
        ):
            with self.subTest(value=value):
                self.assertEqual(percentile_text(value), text)
                self.assertEqual(percentile(value).spans[0].text, text)

    def test_unclassified_actual_rows_use_neutral_title_and_clear_scope_limitation(
        self,
    ):
        card = character_card(
            character(),
            realm="cn",
            host="https://cn.fflogs.com",
            limit=40,
            explicit_selection=False,
        )
        self.assertEqual(card.blocks[0].title, "本次返回副本范围")
        self.assertEqual(len(section_rows(card.blocks[0])), len(character().rankings))
        self.assertEqual(
            section_rows(card.blocks[0])[0][0], character().rankings[0].encounter_name
        )
        self.assertIn("未覆盖完整绝境战与当前资料片零式", CATALOG_LIMITATION)
        self.assertNotIn(card.blocks[0].title, ("绝境战", "零式"))

    def test_logs_source_footer_uses_region_domain_without_rewriting_facts_url(self):
        from types import SimpleNamespace

        for realm, domain, region in (
            ("cn", "cn.fflogs.com", "CN"),
            ("global", "www.fflogs.com", "EU"),
        ):
            with self.subTest(realm=realm):
                host = f"https://{domain}"
                card = character_card(
                    character(),
                    realm=realm,
                    host=host,
                    limit=24,
                    explicit_selection=False,
                )
                self.assertEqual(card.sources, (f"数据源：{domain}",))
                server = SimpleNamespace(
                    region=region,
                    name="Cerberus",
                    region_name="Synthetic",
                    subregion="Synthetic",
                )
                facts = character_facts(
                    character(), server, realm, "rdps", None, host, "2026-10-16"
                )
                self.assertTrue(facts["source"]["url"].startswith(f"{host}/character/"))

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
        self.assertEqual(rounded.text, "99.9%")
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
        self.assertEqual(span.text, "99.9%")
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
            selection_title(replace(record, zone_id=999), source), "本次返回副本范围"
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
        self.assertIn("本图显示 40/41 项；展示上限 40，部分记录未展示。", captions)
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

    def test_decimal_truncation_and_raw_grade_at_all_boundaries(self):
        for threshold, before_color, after_color in (
            (25, "#666666", "#1eff00"),
            (50, "#1eff00", "#0070ff"),
            (75, "#0070ff", "#a335ee"),
            (95, "#a335ee", "#ff8000"),
            (99, "#ff8000", "#e268a8"),
        ):
            for raw, color in (
                (Decimal(threshold) - Decimal("0.001"), before_color),
                (Decimal(threshold), after_color),
                (Decimal(threshold) + Decimal("0.001"), after_color),
            ):
                for source in (raw, str(raw)):
                    with self.subTest(source=source):
                        span = percentile(source).spans[0]
                        self.assertEqual(span.color, color)
                        self.assertNotEqual(span.emphasis, "gold")
        for source, label in (
            (0, "0.0%"),
            ("0.099", "0.0%"),
            (43, "43.0%"),
            ("95.29", "95.2%"),
            (99.95, "99.9%"),
            ("99.99", "99.9%"),
            (Decimal("99.999"), "99.9%"),
            (Decimal("100.000"), "100.0%"),
        ):
            with self.subTest(source=source):
                span = percentile(source).spans[0]
                self.assertEqual(span.text, label)
                self.assertEqual(span.emphasis == "gold", Decimal(str(source)) == 100)
        for invalid in (True, False, -0.01, "100.001", "NaN", "Infinity", {}, [], ""):
            with self.subTest(invalid=invalid):
                span = percentile(invalid).spans[0]
                self.assertEqual((span.text, span.color), ("ERR", "#d34b4b"))

    def test_tiers_preserve_source_names_order_and_job_without_version_placeholder(
        self,
    ):
        base = character()
        row = replace(base.rankings[0], spec="Astrologian")
        groups = tuple(
            CharacterRankingGroup(index, name, 3, -1, kind, "来源资料片", (row,))
            for index, name, kind in (
                (300, "来源绝境战", "ultimate"),
                (200, "真实团本分区甲", "savage"),
                (100, "真实团本分区乙", "savage"),
            )
        )
        record = replace(base, groups=groups, rankings=(row,) * 3)
        card = character_card(
            record, realm="cn", host="cn.fflogs.com", limit=40, explicit_selection=False
        )
        validate_card(card)
        sections = [block for block in card.blocks if isinstance(block, cards.Section)]
        self.assertEqual([section.title for section in sections], ["绝境战", "零式"])
        self.assertEqual(
            [
                block.content
                for block in sections[1].blocks
                if isinstance(block, cards.Text)
            ],
            ["真实团本分区甲", "真实团本分区乙"],
        )
        for section in sections:
            for table in section.blocks:
                if isinstance(table, cards.Table):
                    self.assertEqual(
                        table.rows[0][:2], (row.encounter_name, "占星术士")
                    )
                    self.assertEqual(table.columns[0].title, "副本")
        self.assertEqual(record.rankings[0].spec, "Astrologian")

    def test_display_budget_keeps_categories_and_reports_omitted_rows(self):
        base = character()
        row = base.rankings[0]
        groups = (
            CharacterRankingGroup(
                1, "Ultimate source", 3, -1, "ultimate", "E", (row,) * 40
            ),
            CharacterRankingGroup(2, "Savage source", 3, -1, "savage", "E", (row,) * 4),
        )
        record = replace(base, groups=groups, rankings=(row,) * 44)
        card = character_card(
            record, realm="cn", host="cn.fflogs.com", limit=40, explicit_selection=False
        )
        sections = [block for block in card.blocks if isinstance(block, cards.Section)]
        counts = [
            sum(
                len(block.rows)
                for block in section.blocks
                if isinstance(block, cards.Table)
            )
            for section in sections
        ]
        self.assertEqual(counts, [39, 1])
        self.assertTrue(
            any(
                "部分记录未展示" in block.content
                for block in card.blocks
                if isinstance(block, cards.Text)
            )
        )
        one = character_card(
            record, realm="cn", host="cn.fflogs.com", limit=1, explicit_selection=False
        )
        self.assertIn("未展示", one.blocks[1].blocks[0].content)

    def test_display_format_and_localized_job_never_replace_source_facts(self):
        from types import SimpleNamespace

        row = replace(
            character().rankings[0],
            rank_percent=99.99,
            rank_percent_exact=Decimal("99.99"),
            spec="Scholar",
        )
        record = replace(character(), rankings=(row,))
        server = SimpleNamespace(
            region="CN", name="Cerberus", region_name="China", subregion="Synthetic"
        )
        before = character_facts(
            record, server, "cn", "rdps", None, "https://cn.fflogs.com", "2026-10-16"
        )
        card = character_card(
            record, realm="cn", host="cn.fflogs.com", limit=40, explicit_selection=False
        )
        after = character_facts(
            record, server, "cn", "rdps", None, "https://cn.fflogs.com", "2026-10-16"
        )
        self.assertEqual(before, after)
        self.assertEqual(after["rankings"][0]["rank_percent"], 99.99)
        self.assertEqual(after["rankings"][0]["spec"], "Scholar")
        displayed = card.blocks[0].blocks[0].rows[0]
        self.assertEqual(displayed[1], "学者")
        self.assertEqual(displayed[4].spans[0].text, "99.9%")


class FFLogsProductionSafeScopeTests(unittest.IsolatedAsyncioTestCase):
    """Real invoke guard: single returned zone, no speculative discovery calls."""

    def assert_single_scope(self, result, http, expected_title="本次返回副本范围"):
        validate_contract(result)
        self.assertIsNone(result.error)
        sections = [
            block
            for block in result.document.card.blocks
            if isinstance(block, cards.Section)
        ]
        self.assertEqual([section.title for section in sections], [expected_title])
        rows = section_rows(sections[0])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "Synthetic Trial")
        self.assertNotIn(sections[0].title, ("绝境战", "零式"))
        self.assertEqual(result.document.card.sources, ("数据源：cn.fflogs.com",))
        self.assertEqual(len(http.requests), 3)
        queries = [json.loads(request.body)["query"] for request in http.requests]
        self.assertEqual(sum("FF14PublicCharacter" in query for query in queries), 1)
        for forbidden in (
            "__type",
            "FF14OutputMetadata",
            "FF14CharacterMetadata",
            "FF14CatalogCharacter",
        ):
            self.assertFalse(any(forbidden in query for query in queries), forbidden)
        captions = [
            block.content
            for block in result.document.card.blocks
            if isinstance(block, cards.Text)
        ]
        self.assertIn(CATALOG_LIMITATION, captions)

    async def test_default_discovers_source_policy_categories_without_other_expansions(
        self,
    ):
        catalog = expanded_metadata()
        http = fixture._FakeHttp(
            [
                *fixture._character_responses(realm="cn"),
                expanded_schema(),
                catalog,
                catalog_rankings(catalog),
            ]
        )
        handler = FFLogsCharacterLookup(fixture._services(http))
        self.addCleanup(handler.close)
        result = await handler.invoke(None, TARGET)
        sections = [
            item
            for item in result.document.card.blocks
            if isinstance(item, cards.Section)
        ]
        self.assertEqual([item.title for item in sections], ["绝境战", "零式"])
        self.assertEqual([len(section_rows(item)) for item in sections], [7, 13])
        self.assertIs(result.status, ResultStatus.SUCCESS)
        self.assertFalse(
            any("历史成绩未查全" in warning for warning in result.warnings)
        )
        self.assertIn("fflogs_public_cn", handler._metadata_cache)
        self.assertEqual(len(http.responses), 0)

    async def test_existing_catalog_cache_reuses_directory_and_queries_rankings(self):
        from time import monotonic

        catalog = expanded_metadata()
        http = fixture._FakeHttp(
            [*fixture._character_responses(realm="cn"), catalog_rankings(catalog)]
        )
        handler = FFLogsCharacterLookup(fixture._services(http))
        self.addCleanup(handler.close)
        handler._metadata_cache["fflogs_public_cn"] = (
            monotonic() + 60,
            parse_character_metadata(catalog["data"]),
            "verified_catalog",
        )
        result = await handler.invoke(None, TARGET)
        self.assertEqual(len(http.requests), 4)
        self.assertEqual(
            [
                item.title
                for item in result.document.card.blocks
                if isinstance(item, cards.Section)
            ],
            ["绝境战", "零式"],
        )
        self.assertIs(result.status, ResultStatus.SUCCESS)
        self.assertFalse(
            any(
                "__type" in json.loads(request.body)["query"]
                for request in http.requests
            )
        )

    async def test_explicit_filters_keep_actual_single_scope_without_discovery(self):
        http = fixture._FakeHttp(
            fixture._character_responses(
                realm="cn", character=fixture._character(difficulty=3, partition=2)
            )
        )
        handler = FFLogsCharacterLookup(fixture._services(http))
        result = await handler.invoke(
            None, {**TARGET, "zone_id": 73, "difficulty": 3, "partition": 2}
        )
        self.assert_single_scope(result, http)
        handler.close()

    async def test_no_result_keeps_no_records_error_without_catalog_discovery(self):
        body = fixture._character()
        body["data"]["characterData"]["character"]["rankings"]["rankings"] = []
        http = fixture._FakeHttp(
            fixture._character_responses(realm="cn", character=body)
        )
        handler = FFLogsCharacterLookup(fixture._services(http))
        result = await handler.invoke(None, TARGET)
        self.assertIs(result.error.code, ErrorCode.NO_RECORDS)
        self.assertIsNone(result.document)
        self.assertEqual(len(http.requests), 4)
        for request in http.requests:
            query = json.loads(request.body)["query"]
            self.assertNotIn("FF14CatalogCharacter", query)
        handler.close()


class FFLogsOfflineCatalogReferenceTests(unittest.IsolatedAsyncioTestCase):
    """Synthetic source mechanics retained for the restored aggregation contract."""

    def test_bounded_target_selection_keeps_single_category_and_small_order(self):
        targets = select_character_zones(
            parse_character_metadata(expanded_metadata()["data"]), 73
        )
        self.assertEqual(
            [item[0].zone_id for item in _bounded_catalog_targets(targets)[:6]],
            [100, 73, 101, 74, 102, 75],
        )
        self.assertEqual(set(_bounded_catalog_targets(targets)), set(targets))
        many = targets * 3
        savage_first = tuple(
            target for target in many if target[2] == "savage"
        ) + tuple(target for target in many if target[2] == "ultimate")
        selected = _bounded_catalog_targets(savage_first)
        self.assertEqual(
            len({(item[0].zone_id, item[1].difficulty_id) for item in selected}), 10
        )
        self.assertEqual({target[2] for target in selected}, {"ultimate", "savage"})
        single = tuple(target for target in many if target[2] == "ultimate")
        self.assertEqual(set(_bounded_catalog_targets(single)), set(single))

    def test_diagnostic_is_bounded_and_excludes_character_and_rankings(self):
        catalog = parse_character_metadata(expanded_metadata()["data"])
        template = catalog.zones[0]
        large = replace(
            catalog,
            zones=tuple(
                replace(
                    template,
                    zone_id=index + 1,
                    name="长" * 160,
                    difficulties=template.difficulties * 10,
                )
                for index in range(512)
            ),
        )
        private = replace(
            character(), name="private-character", server_name="private-server"
        )
        encoded = _catalog_diagnostic(large, "verified_catalog", private)
        self.assertLessEqual(len(encoded.encode("utf-8")), CATALOG_DIAGNOSTIC_BYTES)
        data = json.loads(encoded)
        self.assertEqual(data["zone_count"], 512)
        self.assertEqual(len(data["zones"]) + data["zones_omitted"], 512)
        self.assertTrue(
            all(zone["difficulties_omitted"] == 6 for zone in data["zones"])
        )
        for forbidden in (
            "private-character",
            "private-server",
            "best_amount",
            "rank_percent",
            "character_id",
            "rankings",
        ):
            self.assertNotIn(forbidden, encoded)
        unavailable = json.loads(
            _catalog_diagnostic(None, "unavailable_payload", private)
        )
        self.assertIsNone(unavailable["default_expansion_id"])
        self.assertEqual(unavailable["zones"], [])
        self.assertEqual(unavailable["targets_before"], {"ultimate": 0, "savage": 0})

    async def test_private_metadata_failure_has_safe_category_without_raw_errors(self):
        http = fixture._FakeHttp(
            [
                *fixture._character_responses(realm="cn"),
                {"errors": [{"message": "private-secret-key private-error"}]},
            ]
        )
        handler = FFLogsCharacterLookup(fixture._services(http))
        result = await offline_catalog_reference(handler, None, TARGET)
        self.assertIsNone(result.error)
        cached = handler._metadata_cache["fflogs_public_cn"]
        message = _catalog_diagnostic(cached[1], cached[2], character())
        data = json.loads(message)
        self.assertEqual(data["metadata_state"], "unavailable_graphql")
        for forbidden in (
            "private-secret-key",
            "private-error",
            "Synthetic Hero",
            "Cerberus",
            "bestAmount",
            "rankPercent",
        ):
            self.assertNotIn(forbidden, message)

    async def test_query_budget_preserves_both_catalog_categories(self):
        catalog = expanded_metadata(ultimate_count=13)
        http = fixture._FakeHttp(
            [
                *fixture._character_responses(realm="cn"),
                expanded_schema(),
                catalog,
                catalog_rankings(catalog, batch_index=0),
                catalog_rankings(catalog, batch_index=1),
            ]
        )
        result = await offline_catalog_reference(
            FFLogsCharacterLookup(fixture._services(http)), None, TARGET
        )
        batches = [
            json.loads(request.body)["variables"]
            for request in http.requests
            if "FF14CatalogCharacter" in json.loads(request.body)["query"]
        ]
        queried = {
            value
            for variables in batches
            for key, value in variables.items()
            if key.startswith("z")
        }
        self.assertEqual(len(queried), 16)
        self.assertTrue(
            all(
                sum(key.startswith("z") for key in variables) <= 12
                for variables in batches
            )
        )
        self.assertTrue(queried.intersection({73, 74, 75}))
        self.assertTrue(queried.intersection(range(100, 113)))
        self.assertFalse(queried.intersection({72, 9999, 43}))
        self.assertEqual(
            [
                block.title
                for block in result.document.card.blocks
                if isinstance(block, cards.Section)
            ],
            ["绝境战", "零式"],
        )

    async def test_missing_ultimate_catalog_is_explicit_on_card(self):
        catalog = expanded_metadata(ultimate_count=1)
        for zone in catalog["data"]["worldData"]["zones"]:
            if zone["difficulties"][0]["name"] == "Ultimate":
                zone["difficulties"][0]["name"] = "Unverified difficulty"
                zone["name"] = "Synthetic unclassified duty"
                for encounter in zone["encounters"]:
                    encounter["name"] = (
                        f"Synthetic unclassified encounter {encounter['id']}"
                    )
        http = fixture._FakeHttp(
            [
                *fixture._character_responses(realm="cn"),
                expanded_schema(),
                catalog,
                catalog_rankings(catalog),
            ]
        )
        result = await offline_catalog_reference(
            FFLogsCharacterLookup(fixture._services(http)), None, TARGET
        )
        self.assertTrue(any("绝境战目录" in warning for warning in result.warnings))
        captions = [
            block.content
            for block in result.document.card.blocks
            if isinstance(block, cards.Text)
        ]
        self.assertTrue(any("绝境战目录" in caption for caption in captions))
        self.assertEqual(result.document.card.blocks[0].title, "绝境战")
        self.assertEqual(section_rows(result.document.card.blocks[0]), ())

    async def test_missing_group_warning_survives_alias_failure(self):
        catalog = expanded_metadata(ultimate_count=1)
        for zone in catalog["data"]["worldData"]["zones"]:
            if zone["difficulties"][0]["name"] == "Ultimate":
                zone["difficulties"][0]["name"] = "Unverified difficulty"
                zone["name"] = "Synthetic unclassified duty"
                for encounter in zone["encounters"]:
                    encounter["name"] = (
                        f"Synthetic unclassified encounter {encounter['id']}"
                    )
        http = fixture._FakeHttp(
            [
                *fixture._character_responses(realm="cn"),
                expanded_schema(),
                catalog,
                {"errors": [{"message": "private-alias-error"}]},
            ]
        )
        result = await offline_catalog_reference(
            FFLogsCharacterLookup(fixture._services(http)), None, TARGET
        )
        self.assertIsNone(result.error)
        self.assertTrue(any("绝境战目录" in warning for warning in result.warnings))
        self.assertTrue(any("保留" in warning for warning in result.warnings))
        self.assertFalse(
            any("private-alias-error" in warning for warning in result.warnings)
        )

    async def test_unknown_difficulties_warn_without_guess_or_extra_query(self):
        catalog = expanded_metadata()
        for zone in catalog["data"]["worldData"]["zones"]:
            zone["difficulties"][0]["name"] = "Unknown difficulty"
            zone["name"] = "Synthetic unclassified duty"
            for encounter in zone["encounters"]:
                encounter["name"] = (
                    f"Synthetic unclassified encounter {encounter['id']}"
                )
        http = fixture._FakeHttp(
            [*fixture._character_responses(realm="cn"), expanded_schema(), catalog]
        )
        handler = FFLogsCharacterLookup(fixture._services(http))
        result = await offline_catalog_reference(handler, None, TARGET)
        self.assertIsNone(result.error)
        self.assertEqual(len(http.requests), 5)
        self.assertEqual(result.document.card.blocks[0].title, "绝境战")
        self.assertEqual(section_rows(result.document.card.blocks[0]), ())
        self.assertTrue(any("绝境战目录" in warning for warning in result.warnings))
        self.assertTrue(
            any("未以角色成绩决定回退" in warning for warning in result.warnings)
        )
        cached = handler._metadata_cache["fflogs_public_cn"]
        snapshot = json.loads(_catalog_diagnostic(cached[1], cached[2], character()))
        self.assertEqual(snapshot["targets_before"], {"ultimate": 0, "savage": 0})
        self.assertEqual(snapshot["final_groups"], {"ultimate": 0, "savage": 0})
        self.assertEqual(snapshot["metadata_state"], "verified_catalog")

    async def test_base_catalog_and_cached_failure_have_distinct_safe_states(self):
        for schema, directory, expected in (
            (fixture._output_schema(), metadata(), "base_catalog"),
            ({"data": {}}, None, "unavailable_payload"),
        ):
            with self.subTest(state=expected):
                responses = [*fixture._character_responses(realm="cn"), schema]
                if directory is not None:
                    responses.append(directory)
                responses.extend(fixture._character_responses(realm="cn"))
                http = fixture._FakeHttp(responses)
                handler = FFLogsCharacterLookup(fixture._services(http))
                await offline_catalog_reference(handler, None, TARGET)
                await offline_catalog_reference(handler, None, TARGET)
                self.assertEqual(len(http.requests), 8 if directory else 7)
                cached = handler._metadata_cache["fflogs_public_cn"]
                snapshot = json.loads(
                    _catalog_diagnostic(cached[1], cached[2], character())
                )
                self.assertEqual(snapshot["metadata_state"], expected)
                self.assertIsNone(snapshot["default_expansion_id"])
                self.assertEqual(
                    snapshot["targets_after"], {"ultimate": 0, "savage": 0}
                )
                handler.close()

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
                task = asyncio.create_task(
                    offline_catalog_reference(handler, None, TARGET)
                )
                await asyncio.wait_for(entered.wait(), 2)
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
        for phase in ("before_dispatch", "inflight"):
            with self.subTest(phase=phase):
                catalog = expanded_metadata()
                initial = fixture._character(difficulty=3)
                base = initial["data"]["characterData"]["character"]["rankings"]
                zone = next(
                    zone
                    for zone in catalog["data"]["worldData"]["zones"]
                    if zone["id"] == 73
                )
                base["rankings"][0]["encounter"] = {
                    key: zone["encounters"][0][key] for key in ("id", "name")
                }
                http = fixture._FakeHttp(
                    [
                        *fixture._character_responses(realm="cn", character=initial),
                        expanded_schema(),
                        catalog,
                    ]
                )
                original_fetch = http.fetch
                original_timeout = asyncio.timeout
                entered, cancelled = asyncio.Event(), asyncio.Event()
                active_timeouts, in_flight = {}, []

                def controlled_timeout(delay):
                    context = original_timeout(delay)
                    active_timeouts[asyncio.current_task()] = context
                    return context

                async def fetch(request):
                    if "FF14CatalogCharacter" in json.loads(request.body)["query"]:
                        in_flight.append(active_timeouts[asyncio.current_task()])
                        entered.set()
                        try:
                            await asyncio.Event().wait()
                        finally:
                            cancelled.set()
                    return await original_fetch(request)

                http.fetch = fetch
                handler = FFLogsCharacterLookup(fixture._services(http))
                original_rankings = handler._catalog_rankings

                async def rankings(*args, **kwargs):
                    if phase == "before_dispatch":
                        kwargs["deadline"] = 0
                    return await original_rankings(*args, **kwargs)

                task = None
                try:
                    with (
                        patch.object(handler, "_catalog_rankings", rankings),
                        patch(
                            "modules.ff14.features.fflogs.asyncio.timeout",
                            controlled_timeout,
                        ),
                    ):
                        task = asyncio.create_task(
                            offline_catalog_reference(handler, None, TARGET)
                        )
                        if phase == "inflight":
                            await asyncio.wait_for(entered.wait(), timeout=2)
                            self.assertEqual(len(in_flight), 1)
                            # Expire the production HTTP timeout only after fetch entered.
                            in_flight[0].reschedule(asyncio.get_running_loop().time())
                        result = await asyncio.wait_for(task, timeout=2)
                    if phase == "before_dispatch":
                        self.assertFalse(entered.is_set())
                        self.assertFalse(cancelled.is_set())
                        self.assertEqual(in_flight, [])
                        self.assertTrue(
                            any("未查询" in warning for warning in result.warnings)
                        )
                    else:
                        self.assertTrue(entered.is_set())
                        self.assertTrue(cancelled.is_set())
                        self.assertTrue(
                            any("保留" in warning for warning in result.warnings)
                        )
                    self.assertIsNone(result.error)
                    ultimate_rows = section_rows(result.document.card.blocks[0])
                    self.assertEqual(len(ultimate_rows), 7)
                    self.assertTrue(
                        all(
                            row[-1].spans[0].text
                            == ("—" if phase == "before_dispatch" else "ERR")
                            for row in ultimate_rows
                        )
                    )
                    savage_rows = section_rows(result.document.card.blocks[1])
                    self.assertTrue(
                        any(row[-1].spans[0].text == "93.5%" for row in savage_rows)
                    )
                    self.assertTrue(
                        any(
                            row[2].spans[0].text == "12,346"
                            and row[3].spans[0].text == "80"
                            for row in savage_rows
                        )
                    )
                finally:
                    if task is not None and not task.done():
                        task.cancel()
                    if task is not None:
                        await asyncio.gather(task, return_exceptions=True)
                    handler.close()

    async def test_alias_budget_and_duplicate_rows_are_explicit(self):
        catalog = expanded_metadata(ultimate_count=13)
        batch = catalog_rankings(catalog, batch_index=0)
        alias = batch["data"]["characterData"]["character"]["z2"]
        alias["rankings"].append(dict(alias["rankings"][0]))
        http = fixture._FakeHttp(
            [
                *fixture._character_responses(
                    realm="cn", character=fixture._character(difficulty=3)
                ),
                expanded_schema(),
                catalog,
                batch,
                catalog_rankings(catalog, batch_index=1),
            ]
        )
        result = await offline_catalog_reference(
            FFLogsCharacterLookup(fixture._services(http)), None, TARGET
        )
        variables = next(
            json.loads(request.body)["variables"]
            for request in http.requests
            if "FF14CatalogCharacter" in json.loads(request.body)["query"]
        )
        self.assertEqual(len([key for key in variables if key.startswith("z")]), 12)
        self.assertTrue(any("查询失败" in warning for warning in result.warnings))
        rows = result.document.card.blocks[0].blocks[0].rows
        self.assertEqual(rows[1][-1].spans[0].text, "ERR")

    async def test_offline_reference_selection_preserves_synthetic_groups_and_tiers(
        self,
    ):
        catalog = expanded_metadata()
        batch = catalog_rankings(catalog)
        batch["data"]["characterData"]["character"]["z0"]["partition"] = 3
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
        result = await offline_catalog_reference(
            FFLogsCharacterLookup(fixture._services(http)), None, TARGET
        )
        validate_contract(result)
        self.assertIsNone(result.error)
        self.assertIs(result.status, ResultStatus.SUCCESS)
        sections = [
            block
            for block in result.document.card.blocks
            if isinstance(block, cards.Section)
        ]
        self.assertEqual([section.title for section in sections], ["绝境战", "零式"])
        self.assertEqual([len(section_rows(section)) for section in sections], [7, 13])
        self.assertEqual(
            [
                block.content
                for block in sections[1].blocks
                if isinstance(block, cards.Text)
            ],
            ["Synthetic zone 73", "Synthetic zone 74", "Synthetic zone 75"],
        )
        self.assertEqual(sections[1].subtitle, "Synthetic expansion 20")
        text = "\n".join(
            block.text
            for block in result.document.ordered_blocks
            if hasattr(block, "text")
        )
        self.assertIn("绝境战逐项查询来源目录中的标准阵容历史分区", text)
        self.assertIn("零式使用各副本的 FFLogs 默认分区", text)
        self.assertNotIn("全部分区", text)
        self.assertNotIn("历史成绩未查全", text)
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
        self.assertEqual(query["variables"]["p0"], 3)

        # The same explicit request must reject an actual partition 2 response.
        batch["data"]["characterData"]["character"]["z0"]["partition"] = 2
        mismatched = fixture._FakeHttp(
            [
                *fixture._character_responses(
                    realm="cn", character=fixture._character(difficulty=3)
                ),
                expanded_schema(),
                catalog,
                batch,
            ]
        )
        rejected = await offline_catalog_reference(
            FFLogsCharacterLookup(fixture._services(mismatched)), None, TARGET
        )
        self.assertIs(rejected.status, ResultStatus.PARTIAL_SUCCESS)
        self.assertEqual(
            section_rows(rejected.document.card.blocks[0])[0][-1].spans[0].text, "ERR"
        )

    async def test_out_of_zone_encounter_fails_selection_for_known_and_unknown_ids(
        self,
    ):
        for encounter_id in (999999, 1010):
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
                result = await offline_catalog_reference(
                    FFLogsCharacterLookup(fixture._services(http)), None, TARGET
                )
                validate_contract(result)
                self.assertIsNone(result.error)
                self.assertIs(result.status, ResultStatus.PARTIAL_SUCCESS)
                self.assertEqual(
                    any("副本归属不一致" in warning for warning in result.warnings),
                    True,
                )
                captions = [
                    block.content
                    for block in result.document.card.blocks
                    if isinstance(block, cards.Text)
                ]
                self.assertEqual(
                    any("副本归属不一致" in caption for caption in captions),
                    True,
                )
                self.assertEqual(
                    [
                        len(section_rows(section))
                        for section in result.document.card.blocks[:2]
                    ],
                    [7, 13],
                )
                self.assertEqual(
                    section_rows(result.document.card.blocks[0])[0][-1].spans[0].text,
                    "ERR",
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
        result = await offline_catalog_reference(
            FFLogsCharacterLookup(fixture._services(http)), None, TARGET
        )
        sections = [
            block
            for block in result.document.card.blocks
            if isinstance(block, cards.Section)
        ]
        ultimate, savage = [section_rows(section) for section in sections]
        self.assertEqual(ultimate[0][-1].spans[0].emphasis, "gold")
        self.assertEqual(savage[0][-1].spans[0].text, "99.9%")
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
        self.assertTrue(
            all(
                kind == "ultimate"
                for _, _, kind in select_character_zones(data, 123456)
            )
        )
        changed = replace(
            data,
            zones=tuple(
                replace(zone, difficulties=()) if zone.expansion_id == 20 else zone
                for zone in data.zones
            ),
        )
        selected = select_character_zones(changed, 73)
        self.assertEqual(
            [zone.zone_id for zone, _, kind in selected if kind == "savage"], [72]
        )
        self.assertFalse(any(zone.zone_id == 9999 for zone, _, _ in selected))

    async def test_private_helper_enriches_synthetic_rows_and_caches(
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
        first = await offline_catalog_reference(handler, None, TARGET)
        validate_contract(first)
        self.assertEqual(first.document.card.blocks[0].title, "合成高难副本")
        self.assertIs(first.status, ResultStatus.PARTIAL_SUCCESS)
        second = await offline_catalog_reference(handler, None, TARGET)
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
        result = await offline_catalog_reference(
            FFLogsCharacterLookup(fixture._services(http)), None, TARGET
        )
        self.assertIsNone(result.error)
        self.assertIs(result.status, ResultStatus.PARTIAL_SUCCESS)
        self.assertEqual(result.document.card.blocks[0].title, "本次返回副本范围")
        self.assertEqual(len(result.document.card.blocks[0].blocks[0].rows), 1)
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
        task = asyncio.create_task(offline_catalog_reference(handler, None, TARGET))
        await asyncio.wait_for(entered.wait(), 2)
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
            result = await offline_catalog_reference(
                FFLogsCharacterLookup(fixture._services(http)), None, TARGET
            )
        self.assertTrue(cancelled.is_set())
        self.assertIsNone(result.error)
        self.assertIs(result.status, ResultStatus.PARTIAL_SUCCESS)


if __name__ == "__main__":
    unittest.main()
