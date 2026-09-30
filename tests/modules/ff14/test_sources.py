from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = REPOSITORY_ROOT / "tests" / "fixtures" / "ff14" / "items.json"
FFLOGS_FIXTURE_PATH = REPOSITORY_ROOT / "tests" / "fixtures" / "ff14" / "fflogs.json"


def _walk_keys(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


class ItemSourceFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_fixture_declares_synthetic_content_and_omits_assets(self) -> None:
        self.assertTrue(self.fixture["fixture"]["synthetic"])
        self.assertIn("invented", self.fixture["fixture"]["note"])
        self.assertNotIn("icon", set(_walk_keys(self.fixture)))
        self.assertNotIn("image", set(_walk_keys(self.fixture)))

    def test_xivapi_candidates_and_cursor_are_not_an_implicit_selection(self) -> None:
        page = self.fixture["xivcdn"]["search_first_page"]
        candidates = page["results"]
        self.assertGreater(len(candidates), 1)
        self.assertEqual(
            len({candidate["row_id"] for candidate in candidates}), len(candidates)
        )
        self.assertTrue(all(candidate["fields"]["Name"] for candidate in candidates))
        self.assertEqual(page["next"], "synthetic-opaque-cursor-page-2")
        self.assertFalse(page["next"].startswith(("http://", "https://")))
        self.assertEqual(
            self.fixture["xivcdn"]["search_second_page"]["results"][0]["row_id"],
            90003,
        )

    def test_xivapi_item_level_fixture_preserves_relationship_shape(self) -> None:
        detail = self.fixture["xivcdn"]["item_detail"]
        self.assertEqual(detail["row_id"], 90001)
        level_item = detail["fields"]["LevelItem"]
        self.assertIsInstance(level_item, dict)
        self.assertEqual(level_item["sheet"], "ItemLevel")
        self.assertEqual(level_item["value"], 7)
        self.assertNotEqual(level_item["row_id"], detail["row_id"])

    def test_garland_route_groups_and_typed_partials_are_explicit(self) -> None:
        response = self.fixture["garland"]["linked_item_detail"]
        item = response["item"]
        expected_groups = {
            "nodes",
            "fishingSpots",
            "craft",
            "vendors",
            "tradeCurrency",
            "tradeShops",
            "drops",
            "instances",
            "quests",
        }
        self.assertTrue(expected_groups.issubset(item))
        partial_keys = {
            (str(partial["type"]), str(partial["id"]))
            for partial in response["partials"]
        }
        self.assertIn(("node", "7001"), partial_keys)
        self.assertIn(("quest", "7006"), partial_keys)
        self.assertIn(("item", "90002"), partial_keys)
        self.assertNotIn(("mob", "7999"), partial_keys)
        self.assertIn(7999, item["drops"])
        self.assertNotIn("icon", item)

    def test_absent_route_fields_and_upstream_errors_stay_non_committal(self) -> None:
        without_routes = self.fixture["garland"]["item_without_route_fields"]
        self.assertEqual(set(without_routes["item"]), {"id", "name", "description"})
        self.assertEqual(without_routes["partials"], [])
        self.assertIn(
            "do not prove unobtainable", without_routes["item"]["description"]
        )
        self.assertEqual(self.fixture["xivcdn"]["upstream_error"]["http_status"], 503)
        self.assertEqual(self.fixture["garland"]["upstream_error"]["http_status"], 429)


class FFLogsSourceEvidenceFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FFLOGS_FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_fixture_is_synthetic_and_contains_no_real_personal_or_report_data(
        self,
    ) -> None:
        marker = self.fixture["fixture"]
        self.assertTrue(marker["synthetic"])
        self.assertFalse(marker["contains_live_response"])
        self.assertFalse(marker["contains_credentials"])
        self.assertFalse(marker["contains_report_code"])
        self.assertIn("invented", marker["note"])

    def test_character_query_keeps_public_identity_and_rankings_json_separate(
        self,
    ) -> None:
        projection = self.fixture["documented_query_projection"]
        lookup = projection["character_lookup"]
        self.assertEqual(lookup["field"], "characterData.character")
        self.assertEqual(lookup["requires"], ["name", "serverSlug", "serverRegion"])
        self.assertEqual(lookup["ranking_return_type"], "JSON")
        self.assertFalse(lookup["private_fields_requested"])
        character = projection["character"]
        self.assertEqual(character["name"], "Synthetic Aster")
        self.assertTrue(character["zoneRankings"]["_opaque_synthetic_json_placeholder"])

    def test_unverified_metadata_and_pagination_have_no_assumed_wire_fields(
        self,
    ) -> None:
        schema = self.fixture["schema_index_excerpt"]
        self.assertEqual(
            schema["evidence_class"], "official_search_index_only_direct_docs_403"
        )
        self.assertEqual(schema["server_pagination_fields"], "NOT_VERIFIED")
        self.assertEqual(
            schema["ff_zone_difficulty_job_response_fields"], "NOT_VERIFIED"
        )
        region = self.fixture["directory_projection"]["synthetic_regions"][0]
        self.assertIsNone(region["server_pages"]["response_members"])
        self.assertEqual(region["server_pages"]["status"], "NOT_VERIFIED")
        self.assertEqual(
            self.fixture["directory_projection"]["china_host_and_region_support"],
            "NOT_VERIFIED",
        )

    def test_html_percentile_source_is_not_faked_as_a_graphql_result(self) -> None:
        statistics = self.fixture["statistics_page"]
        self.assertEqual(statistics["source_kind"], "public_html_separate_from_graphql")
        self.assertEqual(statistics["live_markup_status"], "NOT_VERIFIED")
        self.assertIsNone(statistics["live_markup"])
        self.assertIsNone(statistics["synthetic_percentile_values"])
        self.assertEqual(
            statistics["reference_percentile_datasets"], [10, 25, 50, 75, 95, 99, 100]
        )


if __name__ == "__main__":
    unittest.main()
