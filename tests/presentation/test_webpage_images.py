"""Actual browser-image encoding and unchanged ordinary-card bounds."""

import unittest
from io import BytesIO
from unittest.mock import patch

from PIL import Image
from ygl_test_subject.core.ports import RenderedMessage
from ygl_test_subject.presentation.png_validation import PngValidationLimits
from ygl_test_subject.presentation.webpage_images import (
    ImageBudgetError,
    encode_page,
)


class WebpageImageTests(unittest.TestCase):
    def test_webpage_budget_is_explicit_and_card_limits_stay_unchanged(self):
        page = b"x" * 4_500_000
        with self.assertRaises(ValueError):
            RenderedMessage("card", png_pages=(page,))
        RenderedMessage("public news", png_pages=(page,), image_profile="webpage")
        with self.assertRaises(ValueError):
            RenderedMessage(
                "news", png_pages=(b"x" * 4_900_001,), image_profile="webpage"
            )
        with self.assertRaises(ValueError):
            RenderedMessage("news", png_pages=(page,) * 6, image_profile="webpage")
        with self.assertRaises(ValueError):
            RenderedMessage("news", png_bytes=b"x", image_profile="webpage")

    def make_png(self, image):
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    def test_activity_dimensions_stay_one_lossless_native_image(self):
        with Image.new("RGB", (1200, 5950), "white") as image:
            encoded = encode_page(self.make_png(image))
        self.assertEqual((encoded.width, encoded.height), (1200, 5950))
        self.assertEqual(encoded.format, "PNG")
        self.assertIsNone(encoded.quality)
        self.assertEqual(PngValidationLimits().max_bytes, 1048576)

    def test_png_over_budget_uses_full_resolution_high_quality_jpeg(self):
        with Image.effect_noise((512, 512), 30).convert("RGB") as image:
            raw = self.make_png(image)
        with patch(
            "ygl_test_subject.presentation.webpage_images.MAX_PAGE_BYTES", 250000
        ):
            encoded = encode_page(raw)
        self.assertEqual(encoded.format, "JPEG")
        self.assertGreaterEqual(encoded.quality, 88)
        with Image.open(BytesIO(encoded.content)) as image:
            self.assertEqual(image.size, (512, 512))
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.layer[0][1:3], (1, 1))  # no chroma subsampling

    def test_huge_dimensions_and_non_png_source_fail_before_conversion(self):
        with Image.new("RGB", (1, 43201)) as image:
            with self.assertRaises(ImageBudgetError):
                encode_page(self.make_png(image))
        with self.assertRaises(Exception):
            encode_page(b"not a browser PNG")
