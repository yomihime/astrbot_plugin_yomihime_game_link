"""Real Pillow PNG tests with an explicit, limited ASCII font declaration.

No production font/provider is implied by this fixture. Local finite CJK visual
checks use a separately recorded trusted system face, outside these fixtures.
"""

import builtins
import dataclasses
import io
import os
import socket
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont
from ygl_test_subject.presentation import card_bitmap as bitmap
from ygl_test_subject.presentation.card_bitmap import (
    BitmapBounds,
    BitmapUnavailable,
    CardSpec,
    Row,
    Section,
    render_card,
)


class AsciiFont:
    """Pillow's packaged default Latin font; no paths or system font search."""

    def supports(self, codepoint):
        return 32 <= codepoint <= 126

    def load(self, size):
        return ImageFont.load_default(size=size)


class CardBitmapTests(unittest.TestCase):
    def setUp(self):
        self.font = AsciiFont()
        self.spec = CardSpec("Result 123", (Section("Details", ("All values.",)),))

    def render(self, spec=None, **kwargs):
        return render_card(spec or self.spec, font_provider=self.font, **kwargs)

    def assert_reason(self, reason, call):
        with self.assertRaises(BitmapUnavailable) as raised:
            call()
        self.assertEqual(raised.exception.reason, reason)
        self.assertEqual(str(raised.exception), reason)
        self.assertIsNone(raised.exception.__cause__)
        return raised.exception

    def test_real_single_frame_rgb_png_without_extra_metadata(self):
        result = self.render()
        self.assertEqual(result.media_type, "image/png")
        self.assertTrue(result.content.startswith(b"\x89PNG\r\n\x1a\n"))
        with Image.open(io.BytesIO(result.content)) as image:
            image.load()
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.mode, "RGB")
            self.assertEqual(image.n_frames, 1)
            self.assertEqual(image.size, (result.width, result.height))
            self.assertEqual(image.info, {})
            self.assertGreater(len(set(image.getdata())), 4)

    def test_deterministic_palettes_and_immutable_inputs(self):
        original = dataclasses.asdict(self.spec)
        images = []
        for palette in ("warm_paper", "soft_lilac", "cool_mint"):
            spec = dataclasses.replace(self.spec, palette=palette)
            first, second = self.render(spec), self.render(spec)
            self.assertEqual(first, second)
            images.append(first)
        self.assertEqual(len({image.content for image in images}), 3)
        self.assertEqual(len({(image.width, image.height) for image in images}), 1)
        self.assertEqual(dataclasses.asdict(self.spec), original)
        for value, name, replacement in (
            (self.spec, "title", "changed"),
            (Section(), "heading", "changed"),
            (Row(("a",)), "cells", ("b",)),
            (BitmapBounds(), "max_bytes", 2),
        ):
            with self.assertRaises(dataclasses.FrozenInstanceError):
                setattr(value, name, replacement)

    def test_complete_long_words_numbers_sources_and_limits_are_drawn(self):
        words = "LongUnbrokenEnglishName" * 8
        digits = "1234567890" * 15
        source = "Source: synthetic fixture with a deliberately long attribution. " * 3
        limit = "Limit: partial synthetic scope, no implied real data. " * 3
        spec = CardSpec(
            "Heading",
            (
                Section(
                    "Section", (words,), ("Name", "Number"), (Row((words, digits)),)
                ),
            ),
            subtitle="Subtitle",
            sources=(source,),
            limitations=(limit,),
            width=420,
        )
        drawn = []
        actual = ImageDraw.ImageDraw.text

        def record(draw, xy, text, *args, **kwargs):
            drawn.append(text)
            return actual(draw, xy, text, *args, **kwargs)

        with patch.object(ImageDraw.ImageDraw, "text", record):
            result = self.render(spec)
        self.assertGreater(result.height, 1000)
        self.assertEqual(
            "".join(drawn),
            "HeadingSubtitleSection"
            + words
            + "NameNumber"
            + words
            + digits
            + limit
            + source,
        )
        self.assertNotIn("...", "".join(drawn))

    def test_explicit_blank_lines_trailing_newlines_and_spaces_are_preserved(self):
        spec = CardSpec("a\n\nb\n", (Section(paragraphs=("  a  \n", "")),))
        layout = bitmap._Layout(self.font, BitmapBounds())
        height = layout.build(spec)
        self.assertEqual(
            [[line.text for line in text.lines] for text in layout.texts],
            [["a", "", "b", ""], ["  a  ", ""], [""]],
        )
        self.assertEqual(self.render(spec).height, height)
        self.assertGreater(height, self.render(CardSpec("ab")).height)

    def test_every_ink_box_fits_full_canvas_and_table_cells(self):
        spec = CardSpec(
            "Italic-like jgQ 123 " * 5,
            (
                Section(
                    columns=("First", "Second"), rows=(Row(("ABC" * 30, "9" * 60)),)
                ),
            ),
            width=320,
        )
        layout = bitmap._Layout(self.font, BitmapBounds())
        height = layout.build(spec)
        for text in layout.texts:
            y = text.y
            for line in text.lines:
                self.assertGreaterEqual(text.x, 40)
                self.assertLessEqual(
                    text.x + line.bbox[2] - line.bbox[0], spec.width - 40
                )
                self.assertLessEqual(y + line.bbox[3] - line.bbox[1], height - 30)
                y += line.height
        self.assertEqual(self.render(spec).height, height)

    def test_output_geometry_and_byte_caps_exact_then_one_less(self):
        result = self.render()
        values = {
            "max_width": result.width,
            "max_height": result.height,
            "max_pixels": result.width * result.height,
            "max_bytes": len(result.content),
        }
        for name, value in values.items():
            with self.subTest(cap=name):
                self.assertEqual(
                    self.render(
                        bounds=dataclasses.replace(BitmapBounds(), **{name: value})
                    ),
                    result,
                )
                self.assert_reason(
                    "byte_limit" if name == "max_bytes" else "layout_overflow",
                    lambda: self.render(
                        bounds=dataclasses.replace(BitmapBounds(), **{name: value - 1})
                    ),
                )

    def test_character_and_input_line_budgets_exact_then_plus_one(self):
        for name, value, valid, invalid in (
            ("max_chars", 3, CardSpec("abc"), CardSpec("abcd")),
            ("max_string_chars", 3, CardSpec("abc"), CardSpec("abcd")),
            ("max_input_lines", 3, CardSpec("a\nb"), CardSpec("a\nb\nc")),
        ):
            with self.subTest(cap=name):
                bounds = dataclasses.replace(BitmapBounds(), **{name: value})
                self.render(valid, bounds=bounds)
                self.assert_reason(
                    "layout_overflow", lambda: self.render(invalid, bounds=bounds)
                )

    def test_sections_rows_columns_exact_then_plus_one(self):
        cases = (
            (
                "max_sections",
                CardSpec("a", (Section(),)),
                CardSpec("a", (Section(), Section())),
            ),
            (
                "max_rows",
                CardSpec("a", (Section(columns=("c",), rows=(Row(("a",)),)),)),
                CardSpec(
                    "a", (Section(columns=("c",), rows=(Row(("a",)), Row(("b",)))),)
                ),
            ),
            (
                "max_columns",
                CardSpec("a", (Section(columns=("c",)),)),
                CardSpec("a", (Section(columns=("c", "d")),)),
            ),
        )
        for name, valid, invalid in cases:
            with self.subTest(cap=name):
                bounds = dataclasses.replace(BitmapBounds(), **{name: 1})
                self.render(valid, bounds=bounds)
                self.assert_reason(
                    "layout_overflow", lambda: self.render(invalid, bounds=bounds)
                )

    def test_output_lines_and_measurements_exact_then_plus_one(self):
        self.render(
            CardSpec("a"),
            bounds=dataclasses.replace(
                BitmapBounds(), max_output_lines=1, max_measurements=2
            ),
        )
        for bounds in (
            dataclasses.replace(BitmapBounds(), max_output_lines=1),
            dataclasses.replace(BitmapBounds(), max_measurements=2),
        ):
            self.assert_reason(
                "layout_overflow", lambda: self.render(CardSpec("a\nb"), bounds=bounds)
            )
        self.assert_reason(
            "layout_overflow",
            lambda: self.render(
                CardSpec("a"),
                bounds=dataclasses.replace(BitmapBounds(), max_measurements=1),
            ),
        )

    def test_no_caller_can_raise_any_hard_cap_or_supply_invalid_budget(self):
        for field in dataclasses.fields(BitmapBounds()):
            default = getattr(BitmapBounds(), field.name)
            for value in (default + 1, 0, -1, True, 1.0):
                with self.subTest(cap=field.name, value=value):
                    self.assert_reason(
                        "invalid_input",
                        lambda: self.render(
                            bounds=dataclasses.replace(
                                BitmapBounds(), **{field.name: value}
                            )
                        ),
                    )

    def test_hard_input_boundaries_are_enforced_without_allocating_images(self):
        hard = BitmapBounds()
        bitmap._validate(CardSpec("a" * 4096), hard)
        self.assert_reason(
            "layout_overflow", lambda: bitmap._validate(CardSpec("a" * 4097), hard)
        )
        for name, valid, invalid in (
            (
                "sections",
                CardSpec("a", (Section(),) * 32),
                CardSpec("a", (Section(),) * 33),
            ),
            (
                "columns",
                CardSpec("a", (Section(columns=("c",) * 8),)),
                CardSpec("a", (Section(columns=("c",) * 9),)),
            ),
            (
                "rows",
                CardSpec("a", (Section(columns=("c",), rows=(Row(("a",)),) * 200),)),
                CardSpec("a", (Section(columns=("c",), rows=(Row(("a",)),) * 201),)),
            ),
            (
                "chars",
                CardSpec("a" * 4000, sources=("b" * 4000,) * 5),
                CardSpec("a" * 4000, sources=("b" * 4000,) * 5 + ("x",)),
            ),
            (
                "lines",
                CardSpec("a", sources=("\n" * 997,)),
                CardSpec("a", sources=("\n" * 998,)),
            ),
        ):
            with self.subTest(cap=name):
                bitmap._validate(valid, hard)
                self.assert_reason(
                    "layout_overflow", lambda: bitmap._validate(invalid, hard)
                )
        with patch.object(Image, "new", side_effect=AssertionError("allocated")):
            self.assert_reason(
                "layout_overflow", lambda: self.render(CardSpec("a\n" * 500))
            )

    def test_hard_geometry_boundaries_with_real_png_and_preallocation_rejection(self):
        widest = self.render(CardSpec("a", width=2048))
        self.assertEqual(widest.width, 2048)
        self.assert_reason(
            "layout_overflow", lambda: self.render(CardSpec("a", width=2049))
        )
        tallest = CardSpec(
            "a", (Section("a", ("",) * 221),), sources=("", ""), width=160
        )
        result = self.render(tallest)
        self.assertEqual(result.height, 8192)
        largest = CardSpec(
            "a",
            (Section("a", ("",) * 102), Section(), Section()),
            sources=("",) * 4,
            width=2000,
        )
        result = self.render(largest)
        self.assertEqual((result.width, result.height), (2000, 4000))
        self.assertEqual(result.width * result.height, 8_000_000)
        with patch.object(Image, "new", side_effect=AssertionError("allocated")):
            self.assert_reason(
                "layout_overflow",
                lambda: self.render(
                    tallest, bounds=dataclasses.replace(BitmapBounds(), max_height=8191)
                ),
            )
            self.assert_reason(
                "layout_overflow",
                lambda: self.render(
                    largest,
                    bounds=dataclasses.replace(BitmapBounds(), max_pixels=7_999_999),
                ),
            )

    def test_hard_line_measurement_and_encoded_buffer_budget_boundaries(self):
        layout = bitmap._Layout(self.font, BitmapBounds())
        self.assertEqual(len(layout.wrap("\n" * 1599, 16, 100)), 1600)
        self.assert_reason("layout_overflow", lambda: layout.wrap("", 16, 100))
        layout = bitmap._Layout(self.font, BitmapBounds())
        for _ in range(60_000):
            layout.measure("", 16)
        self.assert_reason("layout_overflow", lambda: layout.measure("", 16))
        with bitmap._BoundedBuffer(1_048_576) as buffer:
            self.assertEqual(buffer.write(b"x" * 1_048_576), 1_048_576)
            self.assert_reason("byte_limit", lambda: buffer.write(b"x"))

    def test_narrow_cell_single_wide_glyph_and_bad_shapes_fail(self):
        self.assert_reason(
            "layout_overflow",
            lambda: self.render(
                CardSpec("a", (Section(columns=("W",) * 8),), width=160)
            ),
        )
        layout = bitmap._Layout(self.font, BitmapBounds())
        self.assert_reason("layout_overflow", lambda: layout.wrap("W", 28, 1))
        for invalid in (
            CardSpec("a", sections=[]),
            CardSpec("a", (Section(paragraphs=["x"]),)),
            CardSpec("a", (Section(rows=(Row(("a",)),)),)),
            CardSpec("a", (Section(columns=("a",), rows=(Row(("a", "b")),)),)),
            CardSpec("a", sources=(123,)),
            CardSpec("a", sources="https://example.invalid"),
            CardSpec("a", width=True),
            CardSpec("a", width=159),
            CardSpec("a", palette="unknown"),
            CardSpec("a\t"),
            CardSpec("a\x00"),
        ):
            with self.subTest(invalid=invalid):
                self.assert_reason("invalid_input", lambda: self.render(invalid))

    def test_missing_dependency_is_safe_and_lazy(self):
        actual = builtins.__import__

        def reject(name, *args, **kwargs):
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError("secret/path")
            return actual(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=reject):
            self.assert_reason("dependency_missing", self.render)

    def test_missing_glyph_never_loads_or_draws_substitute_font(self):
        with patch.object(self.font, "load", side_effect=AssertionError("loaded")):
            self.assert_reason(
                "glyph_unavailable", lambda: self.render(CardSpec("长中文名称" * 100))
            )
        with patch.object(self.font, "supports", return_value=1):
            self.assert_reason("glyph_unavailable", self.render)

    def test_provider_loading_coverage_metrics_and_draw_errors_are_safe(self):
        for method in ("load", "supports"):
            with patch.object(
                self.font, method, side_effect=RuntimeError("secret/path")
            ):
                self.assert_reason("font_unavailable", self.render)
        with patch.object(self.font, "load", return_value=object()):
            self.assert_reason("font_unavailable", self.render)
        with patch.object(
            ImageDraw.ImageDraw, "text", side_effect=RuntimeError("secret/path")
        ):
            self.assert_reason("font_unavailable", self.render)
        self.assertEqual(str(BitmapUnavailable("secret/path")), "invalid_input")

    def test_import_performs_no_pillow_io_scanning_network_or_print(self):
        path = Path(bitmap.__file__)
        source = compile(path.read_text(encoding="utf-8"), str(path), "exec")
        module = types.ModuleType("bitmap_import_probe")
        actual = builtins.__import__

        def reject_pillow(name, *args, **kwargs):
            if name == "PIL" or name.startswith("PIL."):
                raise AssertionError("eager Pillow import")
            return actual(name, *args, **kwargs)

        def forbidden(*args, **kwargs):
            raise AssertionError("import side effect")

        with (
            patch.dict(sys.modules, {module.__name__: module}),
            patch("builtins.__import__", side_effect=reject_pillow),
            patch("builtins.open", side_effect=forbidden),
            patch("builtins.print", side_effect=forbidden),
            patch.object(os, "listdir", side_effect=forbidden),
            patch.object(os, "scandir", side_effect=forbidden),
            patch.object(socket, "socket", side_effect=forbidden),
        ):
            exec(source, module.__dict__)
        self.assertTrue(callable(module.render_card))


if __name__ == "__main__":
    unittest.main()
