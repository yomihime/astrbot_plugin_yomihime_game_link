"""Long output pages are exact joins of small Chromium raster captures."""

import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image, ImageDraw
from ygl_test_subject.presentation.webpage_images import capture_region


class TiledCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_rows_beyond_16384_are_not_repeated_or_dropped(self):
        async def screenshot(**kwargs):
            self.assertTrue(kwargs["full_page"])
            box = kwargs["clip"]
            self.assertLessEqual(box["height"], 4096)
            with Image.new("RGB", (box["width"], box["height"])) as im:
                draw = ImageDraw.Draw(im)
                for row in range(box["height"]):
                    absolute = box["y"] + row
                    draw.line(
                        (0, row, im.width, row),
                        fill=(absolute // 256, absolute % 256, 17),
                    )
                out = BytesIO()
                im.save(out, format="PNG")
                return out.getvalue()

        page = SimpleNamespace(screenshot=AsyncMock(side_effect=screenshot))
        result = await capture_region(page, x=0, y=200, width=20, height=22500)
        self.assertEqual(result.format, "PNG")
        self.assertEqual(page.screenshot.await_count, 6)
        with Image.open(BytesIO(result.content)) as im:
            for row in range(im.height):
                self.assertEqual(
                    im.getpixel((10, row)), ((row + 200) // 256, (row + 200) % 256, 17)
                )

    async def test_incomplete_tile_is_rejected_instead_of_stretched(self):
        out = BytesIO()
        Image.new("RGB", (20, 1)).save(out, format="PNG")
        page = SimpleNamespace(screenshot=AsyncMock(return_value=out.getvalue()))
        with self.assertRaisesRegex(ValueError, "tile_invalid"):
            await capture_region(page, x=0, y=0, width=20, height=100)
