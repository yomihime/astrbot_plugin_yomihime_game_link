"""Synthetic fail-closed scope regressions; no real API identities or responses."""

from __future__ import annotations

import json
import unittest
from time import monotonic

from modules.ff14.features.fflogs import FFLogsCharacterLookup
from modules.ff14.features.fflogs_catalog import (
    FFLOGS_CN_SOURCE,
    parse_character_metadata,
)
from tests.modules.ff14 import test_fflogs as fixture
from tests.modules.ff14 import test_fflogs_cards as card_fixture
from yomihime_game_link_sdk.results import ResultStatus

TARGET = {"realm": "cn", "server": "Cerberus", "character": "Synthetic Hero"}


class SafeScopeTests(unittest.IsolatedAsyncioTestCase):
    def http(self):
        metadata = card_fixture.expanded_metadata()
        # Synthetic non-targets deliberately share the same old label and
        # expansion; source selection sizes cannot prove a target catalog.
        metadata["data"]["worldData"]["zones"][7]["difficulties"][0]["sizes"] = [48]
        metadata["data"]["worldData"]["zones"][8]["difficulties"][0]["sizes"] = [4]
        alias = card_fixture.catalog_rankings(metadata)
        return fixture._FakeHttp(
            fixture._character_responses(realm="cn")
            + [card_fixture.expanded_schema(), metadata, alias]
        )

    async def test_automatic_scope_excludes_non_eight_player_catalog_and_keeps_groups(
        self,
    ):
        http = self.http()
        handler = FFLogsCharacterLookup(fixture._services(http))
        self.addCleanup(handler.close)
        result = await handler.invoke(None, TARGET)
        operations = [json.loads(request.body) for request in http.requests]
        aliases = [
            operation
            for operation in operations
            if "FF14CatalogCharacter" in operation["query"]
        ]
        self.assertEqual(len(aliases), 1)
        queried = {
            value
            for name, value in aliases[0]["variables"].items()
            if name.startswith("z")
        }
        self.assertNotIn(73, queried)
        self.assertNotIn(74, queried)
        self.assertIn(75, queried)
        self.assertIs(result.status, ResultStatus.SUCCESS)
        sections = [
            block for block in result.document.card.blocks if hasattr(block, "blocks")
        ]
        self.assertEqual([section.title for section in sections], ["绝境战", "零式"])
        self.assertEqual(
            [
                sum(
                    len(block.rows)
                    for block in section.blocks
                    if hasattr(block, "rows")
                )
                for section in sections
            ],
            [7, 5],
        )
        self.assertFalse(
            any("历史成绩未查全" in warning for warning in result.warnings)
        )

    async def test_cached_catalog_reuses_verified_policy_without_new_metadata(self):
        metadata = card_fixture.expanded_metadata()
        http = fixture._FakeHttp(
            [
                *fixture._character_responses(realm="cn"),
                card_fixture.catalog_rankings(metadata),
            ]
        )
        handler = FFLogsCharacterLookup(fixture._services(http))
        self.addCleanup(handler.close)
        handler._metadata_cache[FFLOGS_CN_SOURCE] = (
            monotonic() + 60,
            parse_character_metadata(metadata["data"]),
            "verified_catalog",
        )
        result = await handler.invoke(None, TARGET)
        self.assertEqual(len(http.requests), 4)
        self.assertIs(result.status, ResultStatus.SUCCESS)
        self.assertEqual(len(result.document.card.blocks[0].blocks[0].rows), 7)
        self.assertFalse(
            any(
                "Metadata" in json.loads(request.body)["query"]
                for request in http.requests
            )
        )

    async def test_explicit_filter_group_stays_exact_without_metadata_queries(self):
        character = fixture._character(zone_id=123, difficulty=7, partition=2)
        http = fixture._FakeHttp(
            fixture._character_responses(realm="cn", character=character)
        )
        handler = FFLogsCharacterLookup(fixture._services(http))
        self.addCleanup(handler.close)
        result = await handler.invoke(
            None, {**TARGET, "zone_id": 123, "difficulty": 7, "partition": 2}
        )
        self.assertIs(result.status, ResultStatus.SUCCESS)
        request = fixture._character_request(http)
        variables = json.loads(request.body)["variables"]
        self.assertEqual(
            (variables["zoneId"], variables["difficulty"], variables["partition"]),
            (123, 7, 2),
        )
        self.assertEqual(len(http.requests), 3)

    async def test_text_fallback_percentile_uses_raw_exact_truncation(self):
        for raw in ("99.9762", "99.99999999999999999"):
            with self.subTest(raw=raw):
                character = fixture._character(partition=2)
                body = json.dumps(character).replace("93.5", raw).encode()
                http = fixture._FakeHttp(
                    fixture._character_responses(realm="cn", character=body)
                )
                handler = FFLogsCharacterLookup(fixture._services(http))
                self.addCleanup(handler.close)
                result = await handler.invoke(
                    None, {**TARGET, "zone_id": 73, "difficulty": 101, "partition": 2}
                )
                text = "\n".join(
                    block.text
                    for block in result.document.ordered_blocks
                    if hasattr(block, "text")
                )
                self.assertIn("排名百分位 99.9%", text)
                self.assertNotIn("排名百分位 100.0%", text)
