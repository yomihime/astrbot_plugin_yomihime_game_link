"""Synthetic server directories and closed two-position Logs commands."""

from __future__ import annotations

import unittest

from modules.ff14.features.fflogs_server import command_parameters, realm_from_catalog
from modules.ff14.query_resolution import (
    CatalogEntry,
    QueryResolutionError,
    ScopeCatalog,
)
from yomihime_game_link_sdk.results import ErrorCode


class LogsCommandSyntaxTests(unittest.TestCase):
    def test_two_names_quotes_and_explicit_filters(self):
        self.assertEqual(
            command_parameters(
                '潮风亭 "Synthetic Hero" metric=rdps zone_id=1 difficulty=2 partition=3'
            ),
            {
                "server": "潮风亭",
                "character": "Synthetic Hero",
                "metric": "rdps",
                "zone_id": 1,
                "difficulty": 2,
                "partition": 3,
            },
        )
        self.assertEqual(
            command_parameters('"Synthetic World" Hero server_hint="Synthetic DC"')[
                "server_hint"
            ],
            "Synthetic DC",
        )

    def test_old_three_positions_unknown_duplicate_and_bad_quotes_are_rejected(self):
        for text in (
            '国服 "Synthetic World" "Synthetic Hero"',
            'cn "Synthetic World" "Synthetic Hero"',
            "潮风亭 Hero extra",
            "潮风亭 Hero realm=cn",
            "潮风亭 Hero unknown=x",
            "潮风亭 Hero metric=rdps metric=ndps",
            '潮风亭 "Hero',
            "潮风亭",
            '潮风亭 ""',
            "潮风亭 Hero partition=-1",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                command_parameters(text)


class ServerRealmTests(unittest.TestCase):
    def catalog(self, *worlds):
        entries = []
        for index, (name, region) in enumerate(worlds):
            dc = f"Synthetic DC {index}"
            entries.extend(
                (
                    CatalogEntry("dc", dc, dc, region),
                    CatalogEntry("world", index + 1, name, region, dc),
                )
            )
        return ScopeCatalog(tuple(entries))

    def test_directory_ownership_overrides_language_and_keeps_id_namespace(self):
        catalog = self.catalog(("English China", "China"), ("中文名字", "Japan"))
        self.assertEqual(realm_from_catalog(catalog, "English China"), "cn")
        self.assertEqual(realm_from_catalog(catalog, "中文名字"), "global")

    def test_unknown_unavailable_ambiguous_and_unsupported_are_distinct(self):
        cases = (
            (self.catalog(("Known", "China")), "Unknown", ErrorCode.NOT_FOUND),
            (ScopeCatalog((), available=False), "Known", ErrorCode.UPSTREAM_ERROR),
            (
                self.catalog(("Shared", "China"), ("Shared", "Japan")),
                "Shared",
                ErrorCode.PARAMETER_ERROR,
            ),
            (self.catalog(("Other", "Unsupported")), "Other", ErrorCode.UNSUPPORTED),
        )
        for catalog, name, code in cases:
            with (
                self.subTest(name=name, code=code),
                self.assertRaises(QueryResolutionError) as caught,
            ):
                realm_from_catalog(catalog, name)
            self.assertIs(caught.exception.code, code)

    def test_repeated_name_within_one_realm_leaves_fflogs_disambiguation(self):
        catalog = self.catalog(("Shared", "Europe"), ("Shared", "Japan"))
        self.assertEqual(realm_from_catalog(catalog, "Shared"), "global")
        same_region = self.catalog(("Shared", "Europe"), ("Shared", "Europe"))
        self.assertEqual(realm_from_catalog(same_region, "Shared"), "global")
