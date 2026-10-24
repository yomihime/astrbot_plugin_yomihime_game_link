"""Dense packing invariants and real bounded PNG output."""

import unittest
from dataclasses import replace

from ygl_test_subject.presentation.card_bitmap import BitmapUnavailable
from ygl_test_subject.presentation.layout_dense import plan_dense
from ygl_test_subject.presentation.layout_renderer import render_document_pages
from ygl_test_subject.presentation.png_validation import validate_png

from yomihime_game_link_sdk import cards


class DenseTests(unittest.TestCase):
    def node(self, spans):
        return cards.DenseCollage(
            tuple(
                cards.DenseCollageItem(str(i), span, title=f"Item {i}")
                for i, span in enumerate(spans)
            )
        )

    def test_backfill_determinism_coverage_and_exact_size(self):
        for count in (1, 60, 200, 481, 500, 1000):
            spans = [8, 7, 6, 5, 4, 3, 2] + [1] * max(0, count - 7)
            node = self.node(spans[:count])
            pages = plan_dense(node)
            self.assertEqual(pages, plan_dense(node))
            tiles = [tile for page in pages for tile in page.tiles]
            self.assertEqual(
                sorted(tile.rank for tile in tiles), list(range(1, count + 1))
            )
            self.assertEqual(len({tile.item.key for tile in tiles}), count)
            for page in pages:
                for i, tile in enumerate(page.tiles):
                    x, y, right, bottom = tile.box
                    self.assertEqual(
                        (right - x, bottom - y),
                        (
                            tile.item.span * node.cell_width,
                            tile.item.span * node.cell_height,
                        ),
                    )
                    for other in page.tiles[i + 1 :]:
                        a, b = tile.box, other.box
                        self.assertTrue(
                            a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]
                        )
        backfill = plan_dense(self.node([8, 8, 1]))[0].tiles[-1]
        self.assertEqual(backfill.box[:2], (16 * 54, 0))

    def test_scaling_preserves_cell_occupancy_and_logical_ties(self):
        node = self.node([1, 3, 3, 1, 8, 1])
        normal = plan_dense(node)
        high = plan_dense(
            replace(node, cell_width=72, cell_height=108, max_height=3456)
        )
        for a, b in zip(normal[0].tiles, high[0].tiles):
            self.assertEqual(a.rank, b.rank)
            self.assertEqual(tuple(v * 4 for v in a.box), tuple(v * 3 for v in b.box))
        self.assertEqual(
            [tile.rank for tile in normal[0].tiles if tile.item.span == 3], [2, 3]
        )

    def test_budget_rejects_without_partial_plan(self):
        with self.assertRaises(BitmapUnavailable):
            plan_dense(self.node([8] * 1000))
        with self.assertRaises(BitmapUnavailable):
            plan_dense(replace(self.node([8]), max_height=128))

    def test_real_dense_png_and_narrow_ratios(self):
        for height in (81, 96, 108):
            node = replace(self.node([8, 7, 4, 3] + [1] * 56), cell_height=height)
            card = cards.Document("Career", (node,), width=1080)
            pages = render_document_pages(card)
            self.assertTrue(pages)
            for page in pages:
                self.assertEqual(page.width, 1080)
                validate_png(page)
