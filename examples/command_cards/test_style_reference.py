"""Offline contract, geometry and raster checks for the selected style reference."""

import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import ff14_logs_reference as logs
import renderer as cards
import rich_text_reference as rich
from PIL import Image, ImageChops, ImageDraw, ImageFilter


class MetricContractTests(unittest.TestCase):
    def test_exact_grade_colors_and_raw_boundaries(self):
        expected = (
            "#666666",
            "#1eff00",
            "#0070ff",
            "#a335ee",
            "#ff8000",
            "#e268a8",
            "#e5cc80",
        )
        self.assertEqual(tuple(rich.GRADE_COLORS.values()), expected)
        for raw, grade in (
            (0, rich.Grade.GRAY),
            ("24.999", rich.Grade.GRAY),
            (25, rich.Grade.GREEN),
            ("49.999", rich.Grade.GREEN),
            (50, rich.Grade.BLUE),
            ("74.999", rich.Grade.BLUE),
            (75, rich.Grade.PURPLE),
            ("94.999", rich.Grade.PURPLE),
            (95, rich.Grade.ORANGE),
            ("98.999", rich.Grade.ORANGE),
            (99, rich.Grade.PINK),
            ("99.999999", rich.Grade.PINK),
            (100, rich.Grade.GOLD),
        ):
            with self.subTest(raw=raw):
                self.assertIs(logs.percentile(raw).grade, grade)

    def test_rounding_never_promotes_to_max(self):
        self.assertEqual(
            logs.percentile(Decimal("99.96")),
            rich.TextSpan("99.9%", rich.TextRole.GRADED, rich.Grade.PINK),
        )
        self.assertEqual(logs.percentile("44.65").text, "44.6%")
        self.assertIs(logs.percentile("100.000").grade, rich.Grade.GOLD)
        self.assertEqual(logs.rdps("12345.5").text, "12,346")
        self.assertEqual(logs.rdps("12345.499999").text, "12,345")
        self.assertEqual(logs.rdps("1234567.89").text, "1,234,568")

    def test_zero_missing_error_and_unevaluable_are_distinct(self):
        for fn in (logs.percentile, logs.rdps, logs.count):
            with self.subTest(function=fn.__name__):
                self.assertEqual(fn(None), rich.TextSpan("—"))
                self.assertEqual(
                    fn(logs.Unevaluable("无法评价")), rich.TextSpan("无法评价")
                )
                self.assertNotEqual(fn(0).text, "—")
                for raw in (
                    True,
                    False,
                    -1,
                    "NaN",
                    "Infinity",
                    "bad",
                    object(),
                    "",
                    "9" * 129,
                    10**1000,
                    Decimal("1e1000000"),
                    Decimal("1e-1000000"),
                ):
                    self.assertEqual(fn(raw), rich.TextSpan("ERR", rich.TextRole.ERROR))
        self.assertEqual(logs.percentile(0).text, "0.0%")
        self.assertEqual(logs.rdps(0).text, "0")
        self.assertEqual(logs.count(0).text, "0")
        self.assertEqual(logs.count(1234).text, "1234")
        self.assertEqual(logs.count("1.1").role, rich.TextRole.ERROR)
        self.assertEqual(logs.percentile(101).role, rich.TextRole.ERROR)

    def test_generic_roles_do_not_infer_business_grades(self):
        p = logs.THEMES["cool_white"]
        self.assertEqual(rich.face_color(logs.rdps(100), p), p.text)
        self.assertEqual(rich.face_color(logs.count(100), p), p.text)
        self.assertEqual(
            rich.face_color(rich.TextSpan("ERR", rich.TextRole.ERROR), p),
            rich.ERROR_COLOR,
        )
        for role, color in (
            (rich.TextRole.NORMAL, p.text),
            (rich.TextRole.SECONDARY, p.secondary),
            (rich.TextRole.STRONG, p.title),
            (rich.TextRole.EMPHASIS, p.accent),
        ):
            self.assertEqual(rich.face_color(rich.TextSpan("评价文字", role), p), color)
        for args in (
            ("",),
            ("x" * 513,),
            ("a\nb",),
            ("x", "normal"),
            ("x", rich.TextRole.NORMAL, rich.Grade.GOLD),
            ("x", rich.TextRole.GRADED),
        ):
            with self.assertRaises(ValueError):
                rich.TextSpan(*args)


class EffectTests(unittest.TestCase):
    def canvas(self, background="cool_white"):
        p = logs.THEMES[background]
        return cards.Canvas(p.paper, 600, 240, fonts=rich.reference_fonts()), p

    def metric(self, span, theme="cool_white", size=35):
        c, p = self.canvas(theme)
        c.rect((50, 50, 550, 190), p.tint)
        info = rich.draw_metric(c, span, right=460, center=120, size=size, palette=p)
        return c, p, info

    def test_exact_strict_contrast_threshold_and_selected_shade(self):
        self.assertTrue(rich.needs_shadow(1.49999999))
        self.assertFalse(rich.needs_shadow(1.5))
        self.assertFalse(rich.needs_shadow(1.50000001))
        self.assertAlmostEqual(rich.contrast_ratio("#000000", "#ffffff"), 21)
        self.assertEqual(rich.shadow_color("#1eff00", "#edf1f5"), "#188605")

    def test_shadow_is_unexpanded_zero_offset_gaussian_mask(self):
        mask = Image.new("L", (160, 160))
        ImageDraw.Draw(mask).rectangle((60, 60, 100, 100), fill=255)
        expected = mask.filter(ImageFilter.GaussianBlur(2)).point(
            lambda value: round(value * 235 / 255)
        )
        actual = rich.shadow_mask(mask, 35)
        self.assertIsNone(ImageChops.difference(actual, expected).getbbox())
        self.assertEqual(actual.getpixel((59, 80)), actual.getpixel((101, 80)))
        self.assertEqual(actual.getpixel((80, 59)), actual.getpixel((80, 101)))

    def test_actual_chip_background_controls_shadow_not_page_background(self):
        c, p = self.canvas("deep_indigo")
        c.rect((50, 50, 550, 190), "#edf1f5")
        info = rich.draw_metric(
            c, logs.percentile(25), right=460, center=120, size=35, palette=p
        )
        self.assertEqual(info.background, "#edf1f5")
        self.assertTrue(info.shadow)
        self.assertFalse(rich.needs_shadow(rich.contrast_ratio(info.face, p.paper)))
        c, p = self.canvas()
        c.rect((50, 50, 550, 190), rich.GRADE_COLORS[rich.Grade.GREEN])
        info = rich.draw_metric(
            c, logs.percentile(25), right=460, center=120, size=35, palette=p
        )
        self.assertEqual(info.contrast, 1)
        self.assertTrue(info.shadow)

    def test_each_theme_preserves_crisp_exact_faces_and_unclipped_effects(self):
        for theme in logs.THEMES:
            for grade in rich.Grade:
                with self.subTest(theme=theme, grade=grade):
                    span = rich.TextSpan("100.0%", rich.TextRole.GRADED, grade)
                    c, p, info = self.metric(span, theme)
                    self.assertEqual(info.face, rich.GRADE_COLORS[grade])
                    self.assertEqual(
                        info.shadow,
                        rich.needs_shadow(rich.contrast_ratio(info.face, p.tint)),
                    )
                    self.assertEqual(info.gold_glow, grade is rich.Grade.GOLD)
                    self.assertEqual(info.bounds[2], 460)
                    self.assertLessEqual(
                        abs((info.bounds[1] + info.bounds[3]) / 2 - 120), 0.25
                    )
                    x0, y0, x1, y1 = info.patch_bounds
                    for bounds in (info.mask_bounds, info.effect_bounds):
                        self.assertGreater(bounds[0], 0)
                        self.assertGreater(bounds[1], 0)
                        self.assertLess(bounds[2], x1 - x0)
                        self.assertLess(bounds[3], y1 - y0)
                    # Reconstruct the ORIGINAL glyph mask; every fully covered
                    # interior pixel must remain the exact flat grade RGB.
                    font = c.font(35, True)
                    box = font.getbbox(span.text)
                    mask = Image.new("L", (x1 - x0, y1 - y0))
                    pad = 54
                    ImageDraw.Draw(mask).text(
                        (pad - box[0], pad - box[1]), span.text, font=font, fill=255
                    )
                    crop = c.im.crop(info.patch_bounds)
                    interior = mask.point(lambda value: 255 if value == 255 else 0)
                    difference = ImageChops.difference(
                        crop, Image.new("RGB", crop.size, info.face)
                    ).convert("L")
                    self.assertIsNotNone(interior.getbbox())
                    self.assertIsNone(
                        ImageChops.multiply(difference, interior).getbbox()
                    )

    def test_no_forced_dark_gray_and_no_rounded_max_glow(self):
        _, _, info = self.metric(logs.percentile(0), "deep_indigo")
        self.assertFalse(info.shadow)
        for theme in logs.THEMES:
            _, _, info = self.metric(logs.percentile("99.96"), theme)
            self.assertFalse(info.gold_glow)
            self.assertEqual(info.face, "#e268a8")

    def test_size_scaling_and_multiple_inline_roles(self):
        for size in (24, 35, 48):
            _, _, info = self.metric(logs.percentile(100), size=size)
            x0, y0, x1, y1 = info.patch_bounds
            self.assertLess((x1 - x0) * (y1 - y0), rich.MAX_PATCH_PIXELS)
            self.assertGreater(info.effect_bounds[0], 0)
        c, p = self.canvas()
        infos = rich.draw_rich_line(
            c,
            (
                rich.TextSpan("结果 "),
                logs.percentile("99.96"),
                rich.TextSpan(" / "),
                rich.TextSpan("异常", rich.TextRole.ERROR),
            ),
            xy=(60, 120),
            size=24,
            palette=p,
        )
        self.assertEqual(len(infos), 4)
        self.assertEqual(c.drawn_text, ["结果 ", "99.9%", " / ", "异常"])
        self.assertFalse(infos[1].gold_glow)

    def test_actual_ink_alignment_including_missing_dash(self):
        for span in (
            logs.percentile(100),
            logs.percentile(0),
            logs.percentile(None),
            rich.TextSpan("ERR", rich.TextRole.ERROR),
        ):
            _, _, info = self.metric(span)
            x, y, _, _ = info.patch_bounds
            left, top, right, bottom = info.mask_bounds
            self.assertEqual((x + right) / cards.SCALE, 460)
            self.assertLessEqual(
                abs((y + (top + bottom) / 2) / cards.SCALE - 120), 0.25
            )

    def test_adjacent_gold_effect_does_not_recolor_previous_solid_face(self):
        for theme in logs.THEMES:
            c, p = self.canvas(theme)
            spans = (
                rich.TextSpan("A", rich.TextRole.GRADED, rich.Grade.BLUE),
                logs.percentile(100),
            )
            infos = rich.draw_rich_line(
                c, spans, xy=(100, 120), size=35, palette=p, bold=True
            )
            first = infos[0]
            font = c.font(35, True)
            box = font.getbbox("A")
            x0, y0, x1, y1 = first.patch_bounds
            mask = Image.new("L", (x1 - x0, y1 - y0))
            ImageDraw.Draw(mask).text(
                (54 - box[0], 54 - box[1]), "A", font=font, fill=255
            )
            interior = mask.point(lambda value: 255 if value == 255 else 0)
            delta = ImageChops.difference(
                c.im.crop(first.patch_bounds), Image.new("RGB", mask.size, "#0070ff")
            ).convert("L")
            self.assertIsNone(ImageChops.multiply(delta, interior).getbbox())
            self.assertEqual(infos[1].background, p.paper.lower())

    def test_low_contrast_normal_secondary_and_unevaluable_use_same_shadow(self):
        for role in (rich.TextRole.NORMAL, rich.TextRole.SECONDARY):
            c, p = self.canvas()
            p = replace(p, text=p.paper, secondary=p.paper)
            info = rich.draw_metric(
                c,
                rich.TextSpan("无法评价", role),
                right=460,
                center=120,
                size=35,
                palette=p,
            )
            self.assertTrue(info.shadow)
            self.assertFalse(info.gold_glow)
            self.assertEqual(info.face, p.paper)

    def test_whitespace_advances_without_paint_and_line_failure_is_atomic(self):
        c, p = self.canvas()
        before = c.im.tobytes()
        infos = rich.draw_rich_line(
            c, (rich.TextSpan(" "),), xy=(60, 120), size=24, palette=p
        )
        self.assertIsNone(infos[0].mask_bounds)
        self.assertFalse(infos[0].shadow)
        self.assertEqual(c.im.tobytes(), before)
        with patch("rich_text_reference.MAX_LINE_MASK_PIXELS", 30000):
            with self.assertRaisesRegex(ValueError, "raster budget"):
                rich.draw_rich_line(
                    c,
                    (rich.TextSpan("A"), rich.TextSpan("B")),
                    xy=(60, 120),
                    size=35,
                    palette=p,
                )
        self.assertEqual(c.im.tobytes(), before)
        with self.assertRaisesRegex(ValueError, "clipped"):
            rich.draw_rich_line(
                c,
                (rich.TextSpan("A"), rich.TextSpan("W" * 15)),
                xy=(200, 120),
                size=35,
                palette=p,
            )
        self.assertEqual(c.im.tobytes(), before)

    def test_budgets_reject_before_mask_allocation_and_never_clip(self):
        c, p = self.canvas()
        c.font(96, True)
        with patch(
            "rich_text_reference.Image.new",
            side_effect=AssertionError("No mask allocation expected"),
        ):
            with self.assertRaisesRegex(ValueError, "raster budget"):
                rich.draw_metric(
                    c,
                    rich.TextSpan("W" * 512),
                    right=460,
                    center=120,
                    size=96,
                    palette=p,
                )
        for size in (0, 97, True):
            with self.assertRaises(ValueError):
                rich.draw_metric(
                    c, logs.percentile(25), right=460, center=120, size=size, palette=p
                )
        with self.assertRaisesRegex(ValueError, "clipped"):
            rich.draw_metric(
                c, logs.percentile(100), right=599, center=120, size=35, palette=p
            )
        with self.assertRaises(ValueError):
            rich.draw_rich_line(
                c, (rich.TextSpan("a"),) * 33, xy=(60, 120), size=24, palette=p
            )


class CardReferenceTests(unittest.TestCase):
    def test_full_synthetic_card_source_footer_rows_and_zero(self):
        for theme in logs.THEMES:
            with self.subTest(theme=theme):
                canvas = logs.render_card(theme=theme)
                self.assertEqual(len(logs.DEMO_LOGS.ultimate), 7)
                self.assertEqual(len(logs.DEMO_LOGS.savage), 13)
                self.assertEqual(len(canvas.metric_layout), 20)
                self.assertEqual(
                    sum(info.gold_glow for info in canvas.metric_layout), 1
                )
                self.assertIn("ChatGPT", canvas.drawn_text)
                self.assertIn("@ OpenAI", canvas.drawn_text)
                self.assertIn("0", canvas.drawn_text)
                self.assertIn("—", canvas.drawn_text)
                self.assertIn("数据源：cn.fflogs.com", canvas.drawn_text)
                self.assertIn(logs.FOOTER, canvas.drawn_text)
                self.assertFalse(
                    any(
                        "条记录" in text or "示例" in text or "样式" in text
                        for text in canvas.drawn_text
                    )
                )
                with tempfile.TemporaryDirectory() as folder:
                    path = canvas.save(Path(folder) / "card.png")
                    with Image.open(path) as image:
                        self.assertEqual(image.size, (canvas.w, canvas.h))
                    self.assertLess(path.stat().st_size, 1_000_000)
                    with self.assertRaises(FileExistsError):
                        canvas.save(path)

    def test_fonts_are_explicit_and_render_never_fetches_or_scans(self):
        with (
            patch(
                "renderer.discover_fonts",
                side_effect=AssertionError("No installed-font scan"),
            ),
            patch("pathlib.Path.rglob", side_effect=AssertionError("No font scan")),
            patch("socket.create_connection", side_effect=AssertionError("No network")),
            patch(
                "urllib.request.urlopen",
                side_effect=AssertionError("No resource fetch"),
            ),
        ):
            canvas = logs.render_card(replace(logs.DEMO_LOGS, ultimate=(), savage=()))
        self.assertEqual(canvas.font_config.regular, rich.BUNDLED_FONT)
        self.assertEqual(canvas.font_config.bold, rich.BUNDLED_FONT)
        self.assertEqual(canvas.drawn_text.count("未提供该组记录"), 2)
        self.assertNotIn("未通关", canvas.drawn_text)
        with self.assertRaises(ValueError):
            rich.reference_fonts(Path("https://example.invalid/font.otf"))

    def test_region_source_dynamic_height_and_input_limits(self):
        one = replace(
            logs.DEMO_LOGS,
            ultimate=logs.DEMO_LOGS.ultimate[:1],
            savage=(),
            region="GLOBAL",
        )
        canvas = logs.render_card(one)
        self.assertIn("数据源：www.fflogs.com", canvas.drawn_text)
        self.assertNotIn("数据源：cn.fflogs.com", canvas.drawn_text)
        self.assertLess(canvas.h, logs.render_card().h)
        for data in (
            replace(one, region="unknown"),
            replace(one, character="x" * 129),
            replace(one, ultimate=one.ultimate * 49),
            replace(
                one,
                ultimate=(replace(one.ultimate[0], encounter="很长的副本名称" * 10),),
            ),
        ):
            with self.assertRaises(ValueError):
                logs.render_card(data)

    def test_import_has_no_render_or_output(self):
        with tempfile.TemporaryDirectory() as folder:
            environment = dict(
                os.environ, PYTHONPATH=str(cards.ROOT), PYTHONDONTWRITEBYTECODE="1"
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-c",
                    "import ff14_logs_reference; import rich_text_reference",
                ],
                cwd=folder,
                env=environment,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "")
            self.assertEqual(list(Path(folder).iterdir()), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
