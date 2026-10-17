"""Fixed approved A buffer, semantic intent, and bounded raster ownership."""

import io
import unittest
from contextlib import ExitStack as StdExitStack
from unittest.mock import patch

from PIL import Image, ImageChops, ImageDraw, ImageFilter
from ygl_test_subject.presentation import card_bitmap as bitmap
from ygl_test_subject.presentation import rich_text as rich
from ygl_test_subject.presentation.bundled_font import BundledFontProvider
from ygl_test_subject.presentation.layout_renderer import render_document

from yomihime_game_link_sdk.cards import Document, Inline, Span, Text


class SemanticBufferTests(unittest.TestCase):
    def setUp(self):
        font = BundledFontProvider().load(20)
        with io.BytesIO(font.font_bytes) as stream:
            self.font = font.font_variant(font=stream, size=40)
        self.palette = rich.TextPalette("#0070ff", "#738291", "#293645", "#7e94ab")

    def test_fractional_mask_matches_approved_formula(self):
        with Image.new("RGB", (100, 100)) as canvas, rich.ExitStack() as stack:
            session = rich.RichSession(canvas)
            mask = stack.enter_context(Image.new("L", (20, 20)))
            ImageDraw.Draw(mask).rectangle((6, 6, 13, 13), fill=255)
            ImageDraw.Draw(mask).line((5, 6, 5, 13), fill=100)
            actual = session._buffer(mask, stack)
            high = stack.enter_context(mask.filter(ImageFilter.MaxFilter(3)))
            blended = stack.enter_context(Image.blend(mask, high, 0.5))
            grown = stack.enter_context(blended.filter(ImageFilter.GaussianBlur(0.16)))
            outer = stack.enter_context(ImageChops.subtract(grown, mask))
            inverse = stack.enter_context(ImageChops.invert(mask))
            weighted = stack.enter_context(ImageChops.multiply(outer, inverse))
            expected = stack.enter_context(weighted.point(lambda a: round(a * 0.15)))
            self.assertEqual(actual.tobytes(), expected.tobytes())
            self.assertEqual(session.mask_pixels, 7 * 400)
            self.assertEqual(actual.getpixel((10, 10)), 0)
            self.assertGreater(actual.getpixel((14, 10)), 0)
            self.assertLess(actual.getpixel((14, 10)), round(255 * 0.15))

    def test_buffer_budget_reserved_before_any_temporary(self):
        with Image.new("RGB", (100, 100)) as canvas, Image.new("L", (20, 20)) as mask:
            session = rich.RichSession(canvas, max_mask_pixels=7 * 400 - 1)
            with (
                rich.ExitStack() as stack,
                patch.object(
                    Image.Image,
                    "filter",
                    side_effect=AssertionError("allocated before admission"),
                ),
            ):
                with self.assertRaises(bitmap.BitmapUnavailable) as raised:
                    session._buffer(mask, stack)
                self.assertEqual(raised.exception.reason, "layout_overflow")

    def test_semantic_intent_and_exact_face_fill(self):
        for background in ("#f8fafc", "#1c2337"):
            for span, enabled in (
                (rich.TextSpan("100.0%"), False),
                (rich.TextSpan("100.0%", explicit_color=True), True),
                (rich.TextSpan("100.0%", rich.TextRole.GRADED, rich.Grade.GOLD), True),
            ):
                with Image.new("RGB", (1200, 480), background) as canvas:
                    session = rich.RichSession(canvas)
                    info = session.add(
                        span,
                        font=self.font,
                        size=20,
                        palette=self.palette,
                        left=100,
                        top=100,
                    )
                    self.assertEqual(info.buffer, enabled)
                    with rich.ExitStack() as stack:
                        mask = session._mask(session.plans[0], stack)
                        session.paint()
                        patch_image = stack.enter_context(
                            canvas.crop(info.patch_bounds)
                        )
                        pixels = [
                            color
                            for color, a in zip(patch_image.getdata(), mask.getdata())
                            if a == 255
                        ]
                        self.assertTrue(pixels)
                        self.assertTrue(
                            all(color == rich.rgb(info.face) for color in pixels)
                        )
                    session.close()

    def test_declarative_intent_and_ordinary_bytes_unchanged(self):
        for theme in ("warm_paper", "deep_indigo"):
            document = Document(
                title="Theme",
                theme=theme,
                blocks=(
                    Text(
                        Inline(
                            (
                                Span("normal"),
                                Span("secondary", emphasis="secondary"),
                                Span("strong", emphasis="strong"),
                                Span("emphasis", emphasis="emphasis"),
                            )
                        )
                    ),
                ),
            )
            with patch.object(
                rich.RichSession,
                "_buffer",
                side_effect=AssertionError("themed text buffered"),
            ):
                actual = render_document(document).content
            self.assertEqual(actual, render_document(document).content)
            seen = []
            original = rich.RichSession.add

            def capture(session, span, **kwargs):
                info = original(session, span, **kwargs)
                seen.append((span.text, info.buffer))
                return info

            document = Document(
                title="Theme",
                theme=theme,
                blocks=(
                    Text(
                        Inline(
                            (
                                Span("explicit", color="#0070ff"),
                                Span("gold", emphasis="gold"),
                            )
                        )
                    ),
                ),
            )
            with patch.object(rich.RichSession, "add", capture):
                render_document(document)
            self.assertIn(("explicit", True), seen)
            self.assertIn(("gold", True), seen)
            self.assertIn(("Theme", False), seen)

    def test_legacy_wrap_preserves_color_intent_and_run_boundaries(self):
        layout = bitmap._RichLayout(BundledFontProvider(), bitmap.BitmapBounds())
        value = rich.RichText(
            (rich.TextSpan("A"), rich.TextSpan("BC", explicit_color=True))
        )
        lines = layout.wrap(value, 20, 200)
        self.assertEqual(
            [(span.text, span.explicit_color) for line in lines for span in line.spans],
            [("A", False), ("BC", True)],
        )

    def test_buffer_temporary_masks_close_on_failure(self):
        created = []
        original = Image.Image.filter

        def capture(image, filter):
            result = original(image, filter)
            created.append(result)
            return result

        with Image.new("RGB", (100, 100)) as canvas, Image.new("L", (20, 20)) as mask:
            session = rich.RichSession(canvas)
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                with rich.ExitStack() as stack, StdExitStack() as patches:
                    patches.enter_context(patch.object(Image.Image, "filter", capture))
                    patches.enter_context(
                        patch.object(
                            ImageChops,
                            "subtract",
                            side_effect=RuntimeError("interrupted"),
                        )
                    )
                    session._buffer(mask, stack)
            for image in created:
                with self.assertRaises(ValueError):
                    image.getpixel((0, 0))
