"""Generic collage geometry and actual bounded PNG rendering."""

import io
import math
import random
import unittest

from PIL import Image as PillowImage
from ygl_test_subject.presentation import layout_renderer
from ygl_test_subject.presentation.card_bitmap import BitmapBounds, BitmapUnavailable
from ygl_test_subject.presentation.command_card import CommandCardRenderer
from ygl_test_subject.presentation.layout_assets import rasterize_asset
from ygl_test_subject.presentation.layout_collage import plan_collage
from ygl_test_subject.presentation.layout_renderer import (
    render_document,
    render_document_pages,
)
from ygl_test_subject.presentation.png_validation import validate_png

from yomihime_game_link_sdk import cards
from yomihime_game_link_sdk.display import DisplayAudience, DisplayDocument, TextBlock


def asset(width=120, height=180):
    with PillowImage.new("RGB", (width, height), "#7190a1") as source:
        with io.BytesIO() as output:
            source.save(output, format="PNG")
            return cards.Asset("png", output.getvalue(), "cover", False)


def document(count, image=None, weights=None, **options):
    weights = weights or [1] * count
    items = tuple(
        cards.CollageItem(f"key-{i:03}", weights[i], image, f"Game {i}", f"Number {i}")
        for i in range(count)
    )
    return cards.Document(
        "Library",
        (cards.WeightedCollage(items, **options),),
        brand="GAME LINK",
        powered_by="Source evidence",
    )


class CollageTests(unittest.TestCase):
    def test_integer_tiles_fill_pages_and_global_area_is_monotonic(self):
        for count, weights in (
            (1, [1]),
            (2, [100, 1]),
            (33, [1] * 33),
            (60, [i + 1 for i in range(60)]),
            (200, [1 + (i % 11) for i in range(200)]),
        ):
            with self.subTest(count=count):
                node = document(count, weights=weights).blocks[0]
                pages = plan_collage(node, 928)
                self.assertLessEqual(len(pages), 8)
                all_tiles = [tile for page in pages for tile in page.tiles]
                self.assertEqual(len(all_tiles), count)
                ranked = sorted(
                    all_tiles, key=lambda tile: (-tile.item.weight, tile.item.key)
                )
                self.assertTrue(
                    all(a.area >= b.area for a, b in zip(ranked, ranked[1:]))
                )
                self.assertEqual(
                    [tile.rank for tile in ranked], list(range(1, count + 1))
                )
                for page in pages:
                    represented = {
                        rank
                        for start, end in page.rank_ranges
                        for rank in range(start, end + 1)
                    }
                    self.assertEqual(represented, {tile.rank for tile in page.tiles})
                    self.assertEqual(
                        sum(tile.area for tile in page.tiles), page.width * page.height
                    )
                    self.assertLessEqual(len(page.tiles), 64)
                    self.assertLessEqual(page.height, node.max_height)
                    for tile in page.tiles:
                        x0, y0, x1, y1 = tile.box
                        self.assertTrue(0 <= x0 < x1 <= page.width)
                        self.assertTrue(0 <= y0 < y1 <= page.height)
                    for index, a in enumerate(page.tiles):
                        for b in page.tiles[index + 1 :]:
                            self.assertTrue(
                                a.box[2] <= b.box[0]
                                or b.box[2] <= a.box[0]
                                or a.box[3] <= b.box[1]
                                or b.box[3] <= a.box[1]
                            )

    def test_rank_pages_are_contiguous_and_labeled_in_order(self):
        card = document(200, weights=[1 + i % 11 for i in range(200)])
        pages = plan_collage(card.blocks[0], 928)
        self.assertGreater(len(pages), 1)
        first = 1
        for page in pages:
            last = first + len(page.tiles) - 1
            self.assertEqual(page.rank_ranges, ((first, last),))
            expected = str(first) if first == last else f"{first}–{last}"
            layout, _ = layout_renderer._prepare(
                card, BitmapBounds(max_bytes=4 * 1024 * 1024), None, page
            )
            try:
                text = [
                    "".join(span.text for line in block.lines for span in line.spans)
                    for block in layout.texts
                ]
                self.assertIn(f"本页全局排名：{expected}", text)
                self.assertTrue(
                    any(
                        f"第 {page.index}/{page.count} 页（排名顺序）" in line
                        for line in text
                    )
                )
            finally:
                layout.fonts.clear()
            first = last + 1
        self.assertEqual(first, 201)

    def test_extreme_and_near_equal_weights_keep_rank_area_order(self):
        cases = (
            [1e300, 1e-300],
            [1e300] + [1e-300] * 7,
            [1 + i * 1e-6 for i in range(200)],
            [math.sqrt(1 + (200 - i) ** 3) for i in range(200)],
        )
        for weights in cases:
            with self.subTest(count=len(weights), peak=max(weights)):
                node = document(len(weights), weights=weights).blocks[0]
                pages = plan_collage(node, 928)
                ranks = [tile.rank for page in pages for tile in page.tiles]
                areas = [tile.area for page in pages for tile in page.tiles]
                self.assertEqual(ranks, list(range(1, len(weights) + 1)))
                self.assertTrue(all(a >= b for a, b in zip(areas, areas[1:])))
                self.assertLessEqual(len(pages), 8)
        extreme = plan_collage(document(2, weights=[1e300, 1e-300]).blocks[0], 928)
        self.assertGreater(extreme[0].tiles[0].area, extreme[0].tiles[1].area)

    def test_variable_width_aspect_and_extreme_weights_preserve_geometry(self):
        rng = random.Random(20261023)
        for _ in range(25):
            count = rng.randint(1, 200)
            width = rng.randint(288, 1936)
            aspect = rng.uniform(0.5, 2)
            weights = [10 ** rng.uniform(-300, 300) for _ in range(count)]
            node = document(count, weights=weights, preferred_aspect=aspect).blocks[0]
            pages = plan_collage(node, width)
            tiles = [tile for page in pages for tile in page.tiles]
            self.assertEqual([tile.rank for tile in tiles], list(range(1, count + 1)))
            self.assertTrue(all(a.area >= b.area for a, b in zip(tiles, tiles[1:])))
            self.assertLessEqual(len(pages), 8)
            for page in pages:
                self.assertEqual(
                    sum(tile.area for tile in page.tiles), width * page.height
                )
                self.assertEqual(
                    page.rank_ranges, ((page.tiles[0].rank, page.tiles[-1].rank),)
                )
                for tile in page.tiles:
                    x0, y0, x1, y1 = tile.box
                    ratio = (x1 - x0) / (y1 - y0) / aspect
                    self.assertGreaterEqual(ratio + 1e-9, 2 / 3)
                    self.assertLessEqual(ratio, 1.5 + 1e-9)

    def test_reordered_inputs_have_same_tiles(self):
        node = document(60, weights=[i % 7 + 1 for i in range(60)]).blocks[0]
        shuffled = cards.WeightedCollage(tuple(reversed(node.items)))

        def mapping(value):
            return {
                tile.item.key: (page.index, tile.box)
                for page in plan_collage(value, 928)
                for tile in page.tiles
            }

        self.assertEqual(mapping(node), mapping(shuffled))

    def test_real_png_pages_and_single_page_guard(self):
        for count in (1, 2, 60, 200):
            with self.subTest(count=count):
                card = document(count, asset() if count <= 2 else None)
                pages = render_document_pages(card)
                self.assertGreaterEqual(len(pages), 1)
                self.assertLessEqual(len(pages), 8)
                self.assertLessEqual(
                    sum(len(page.content) for page in pages), 16 * 1024 * 1024
                )
                for page in pages:
                    self.assertLessEqual(len(page.content), 4 * 1024 * 1024)
                    validate_png(page)
                if count == 1:
                    self.assertEqual(pages[0].width, 760)
        with self.assertRaises(BitmapUnavailable):
            render_document(document(1))

    def test_optional_decode_uses_fallback_and_required_fails(self):
        broken = cards.Asset("png", b"\x89PNG\r\n\x1a\ninvalid", "Missing cover", False)
        self.assertEqual(len(render_document_pages(document(1, broken))), 1)
        required = cards.Asset("png", broken.content, broken.alt, True)
        with self.assertRaises(BitmapUnavailable):
            render_document_pages(document(1, required))

    def test_extreme_aspect_source_is_contained(self):
        wide = asset(400, 40)
        with rasterize_asset(
            wide, 240, 360, "#ffffff", fit="cover", min_cover_fraction=2 / 3
        ) as image:
            self.assertEqual(image.getpixel((0, 0))[3], 0)
            self.assertEqual(image.getpixel((120, 180))[3], 255)

    def test_page_count_and_source_budget_fail_closed(self):
        with self.assertRaises(BitmapUnavailable):
            render_document_pages(document(200, max_pages=1))
        image = asset(120, 180)
        node = cards.WeightedCollage(
            tuple(cards.CollageItem(str(i), 1, image) for i in range(200))
        )
        self.assertTrue(
            math.isfinite(
                sum(
                    tile.area for page in plan_collage(node, 928) for tile in page.tiles
                )
            )
        )
        large = cards.Asset(
            "png", b"\x89PNG\r\n\x1a\n" + b"x" * 200_000, required=False
        )
        with self.assertRaises(BitmapUnavailable) as raised:
            render_document_pages(document(60, large))
        self.assertEqual(raised.exception.reason, "byte_limit")

    def test_command_projection_carries_all_pages_and_complete_fallback(self):
        source = DisplayDocument(
            "Library", "subject", (TextBlock("Full item list"),), card=document(200)
        )
        presentation = CommandCardRenderer()(source, audience=DisplayAudience.PUBLIC)
        self.assertIn("Full item list", presentation.text)
        self.assertGreater(len(presentation.bitmaps), 1)
        self.assertIs(presentation.bitmap, presentation.bitmaps[0])
        self.assertEqual(
            len({id(bitmap) for bitmap in presentation.bitmaps}),
            len(presentation.bitmaps),
        )


if __name__ == "__main__":
    unittest.main()
