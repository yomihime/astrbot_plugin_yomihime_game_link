from __future__ import annotations

import json
import unittest
from pathlib import Path

from ygl_test_subject.modules.ff14.models import (
    AcquisitionKind,
    AcquisitionRoute,
    ItemAmount,
    ItemCandidate,
    ItemRecord,
    ItemSearchPage,
    ItemSearchResult,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "tests" / "fixtures" / "ff14" / "items.json"


class FF14ItemModelTests(unittest.TestCase):
    def test_compact_models_keep_candidate_and_cursor_types(self) -> None:
        source = json.loads(FIXTURE.read_text(encoding="utf-8"))
        raw = source["xivcdn"]["search_first_page"]
        page = ItemSearchPage(
            candidates=tuple(
                ItemCandidate(row["row_id"], row["fields"]["Name"])
                for row in raw["results"]
            ),
            next_cursor=raw["next"],
            schema=raw["schema"],
            version=raw["version"],
        )
        self.assertEqual(page.candidates[0].item_id, 90001)
        self.assertEqual(page.next_cursor, "synthetic-opaque-cursor-page-2")
        result = ItemSearchResult(page.candidates, pages_read=1, truncated=True)
        self.assertTrue(result.truncated)

    def test_nested_level_relation_and_route_costs_are_item_specific(self) -> None:
        record = ItemRecord(
            item_id=90001,
            name="Synthetic Ore",
            item_level=7,
            equip_level=2,
            routes=(
                AcquisitionRoute(
                    AcquisitionKind.CRAFT,
                    "制作",
                    ("职业 ID：8",),
                    (ItemAmount(90002, 2, "Synthetic Token"),),
                ),
            ),
            source_urls=("https://example.test/item/90001",),
        )
        self.assertEqual(record.item_level, 7)
        self.assertEqual(record.routes[0].costs[0].amount, 2)
        self.assertFalse(record.routes[0].partial)

    def test_invalid_cursor_id_and_route_shape_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ItemSearchPage((), next_cursor="https://evil.example/next")
        with self.assertRaises(ValueError):
            ItemCandidate(0, "invalid")
        with self.assertRaises(ValueError):
            ItemAmount(1, 0)
        with self.assertRaises(TypeError):
            AcquisitionRoute("craft", "制作", ())  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
