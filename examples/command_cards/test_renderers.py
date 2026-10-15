"""Offline unit/layout tests. Tests write only to temporary directories."""

import os
import re
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import render_logs as logs
import renderer as cards


class FormatterTests(unittest.TestCase):
    def test_rdps_rounds_half_up_and_groups_thousands(self):
        cases = {
            "12345.49999999": "12,345",
            "12345.50000000": "12,346",
            "1234567.890123": "1,234,568",
            "0.5": "1",
            "0": "0",
        }
        for raw, displayed in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(logs.format_rdps(Decimal(raw)), displayed)
        self.assertEqual(logs.format_rdps(None), "—")

    def test_percentiles_have_exactly_one_decimal(self):
        for raw, expected in (
            (43, "43.0%"),
            ("95.25", "95.3%"),
            ("95.24", "95.2%"),
            ("99.95", "100.0%"),
            (100, "100.0%"),
            (0, "0.0%"),
        ):
            self.assertEqual(logs.format_percentile(raw), expected)
        self.assertEqual(logs.format_percentile(None), "—")

    def test_raw_decimal_precision_survives_display(self):
        row = logs.DEMO_LOGS.ultimate[0]
        original_rdps, original_percent = row.highest_rdps, row.best_percentile
        logs.format_rdps(row.highest_rdps)
        logs.format_percentile(row.best_percentile)
        self.assertIs(row.highest_rdps, original_rdps)
        self.assertIs(row.best_percentile, original_percent)
        self.assertEqual(row.highest_rdps, Decimal("41234.567890123"))

    def test_invalid_values_rejected_and_missing_metrics_independent(self):
        for value in ("NaN", "Infinity", "-1"):
            with self.assertRaises(ValueError):
                logs.format_rdps(value)
        for value in ("NaN", "Infinity", "-1", "100.1"):
            with self.assertRaises(ValueError):
                logs.format_percentile(value)
        row = replace(logs.DEMO_LOGS.ultimate[0], best_percentile=None)
        self.assertEqual(logs.format_percentile(row.best_percentile), "—")
        self.assertEqual(logs.format_rdps(row.highest_rdps), "41,235")


class HelpSelectionTests(unittest.TestCase):
    def test_zero_modules_has_no_recommendation(self):
        data = cards.build_help_data((), seed=1)
        self.assertIsNone(data.suggested)
        self.assertEqual(data.active_modules, ())
        self.assertNotIn("FF14", data.opener)
        self.assertIn("没有可用模块", data.opener)
        cards.render_help(data)

    def test_one_module_is_selected_for_every_seed(self):
        self.assertEqual(tuple(m.key for m in cards.MODULES), ("ff14",))
        for seed in range(25):
            self.assertIs(
                cards.build_help_data(cards.MODULES, seed=seed).suggested, cards.FF14
            )

    def test_only_loaded_enabled_available_unique_modules_are_selected(self):
        fixtures = (
            replace(cards.FF14, key="unloaded", loaded=False),
            replace(cards.FF14, key="disabled", enabled=False),
            replace(cards.FF14, key="unavailable", available=False),
            cards.FF14,
            cards.FF14,
        )
        for seed in range(25):
            data = cards.build_help_data(fixtures, seed=seed)
            self.assertEqual(data.active_modules, (cards.FF14,))
            self.assertIs(data.suggested, cards.FF14)

    def test_seed_is_repeatable_and_display_name_is_used(self):
        first = replace(cards.FF14, key="internal_a", display_name="测试模块甲")
        second = replace(cards.FF14, key="internal_b", display_name="测试模块乙")
        fixtures = (first, second)
        self.assertEqual(
            cards.build_help_data(fixtures, seed=14),
            cards.build_help_data(fixtures, seed=14),
        )
        self.assertEqual(
            {cards.build_help_data(fixtures, seed=n).suggested for n in range(30)},
            set(fixtures),
        )
        self.assertNotIn("internal_", cards.build_help_data(fixtures, seed=14).opener)

    def test_choose_once_then_reuse_across_palettes(self):
        with patch("renderer.random.Random") as factory:
            factory.return_value.choice.return_value = cards.FF14
            data = cards.build_help_data(cards.MODULES)
            factory.return_value.choice.assert_called_once()
        with patch(
            "renderer.random.Random",
            side_effect=AssertionError("Rendering must not reselect"),
        ):
            for palette in cards.PALETTES:
                self.assertEqual(cards.render_help(data, palette).im.size, (1840, 1600))


class RenderingTests(unittest.TestCase):
    def test_long_market_and_module_labels_stay_in_bounds(self):
        long = replace(
            cards.DEMO_MARKET,
            item="长名称布局检查" * 20,
            quotes=(
                replace(
                    cards.DEMO_MARKET.quotes[0],
                    price=9_999_999_999,
                    world="长世界名称" * 20,
                ),
                cards.DEMO_MARKET.quotes[1],
            ),
        )
        market = cards.render_market(long)
        module = replace(
            cards.FF14,
            display_name="长模块名布局检查" * 20,
            help_command="/ygl " + "long_command_" * 20,
        )
        help_card = cards.render_help(cards.build_help_data((module,), seed=14))
        self.assertEqual(market.w, 920)
        self.assertEqual(help_card.h, 800)
        self.assertTrue(any("…" in text for text in market.drawn_text))
        self.assertTrue(any(text.endswith(" 开始吧") for text in help_card.drawn_text))

    def test_text_fit_obeys_reserved_width(self):
        canvas = cards.Canvas("#FFFFFF")
        for text in ("很长的中文物品名" * 15, "A" * 200, "9,999,999,999"):
            size, fitted = cards.fit_text(canvas, text, 218, 69, 30, True)
            self.assertLessEqual(canvas.width(fitted, size, True), 218)
        with self.assertRaises(ValueError):
            cards.fit_text(canvas, "测试", 1, 24, 18)

    def test_palettes_are_deterministic(self):
        self.assertEqual(
            tuple(cards.PALETTES), ("warm_paper", "soft_lilac", "cool_mint")
        )
        with patch(
            "renderer.random.Random", side_effect=AssertionError("No random palettes")
        ):
            a = cards.render_market(cards.DEMO_MARKET)
            b = cards.render_market(cards.DEMO_MARKET)
        self.assertEqual(a.im.tobytes(), b.im.tobytes())
        self.assertNotEqual(
            a.im.getpixel((0, 0)),
            cards.render_market(cards.DEMO_MARKET, "cool_mint").im.getpixel((0, 0)),
        )

    def test_logs_is_one_card_with_twenty_synthetic_rows(self):
        canvas = logs.render_card()
        self.assertEqual(len(logs.DEMO_LOGS.ultimate), 7)
        self.assertEqual(len(logs.DEMO_LOGS.savage), 13)
        self.assertEqual(canvas.drawn_text.count("游戏连结 · FF14 · 战绩"), 1)
        self.assertEqual(canvas.drawn_text.count("绝境战"), 1)
        self.assertEqual(canvas.drawn_text.count("最高 rDPS"), 2)
        self.assertEqual(canvas.drawn_text.count("最佳百分位"), 2)
        percentages = [text for text in canvas.drawn_text if text.endswith("%")]
        self.assertEqual(len(percentages), 20)
        self.assertTrue(all(re.fullmatch(r"\d+\.\d%", text) for text in percentages))
        self.assertIn("示例冒险者", canvas.drawn_text)
        self.assertIn("@ 示例服务器", canvas.drawn_text)
        self.assertIn(
            "两项最佳可能来自不同场次；不同职业与版本不直接横比", canvas.drawn_text
        )
        self.assertIn("100.0%", percentages)

    def test_logs_height_tracks_rows_and_long_labels(self):
        full = logs.render_card()
        one = replace(logs.DEMO_LOGS, ultimate=logs.DEMO_LOGS.ultimate[:1], savage=())
        shorter = logs.render_card(one)
        long_record = replace(
            one.ultimate[0],
            encounter="超长副本名称排版检查" * 15,
            job="超长职业名称" * 10,
            version="长版本" * 10,
            highest_rdps=Decimal("123456789.123456789"),
        )
        longer_row = logs.render_card(
            replace(
                one,
                ultimate=(long_record,),
                character="长角色名" * 15,
                world="长服务器名" * 15,
                savage_title="长分类标题" * 30,
            )
        )
        self.assertGreater(full.h, shorter.h)
        self.assertGreater(longer_row.h, shorter.h)
        self.assertIn("未提供该组记录", shorter.drawn_text)
        self.assertNotIn("未通关", shorter.drawn_text)
        self.assertTrue(any("…" in text for text in longer_row.drawn_text))

    def test_svg_is_monochrome_and_not_cropped(self):
        for size in (24, 56, 75):
            icon = cards.rasterize_svg(cards.FF14.icon.resolve(), size * 2, "#B68061")
            left, top, right, bottom = icon.getchannel("A").getbbox()
            self.assertGreater(left, 0)
            self.assertGreater(top, 0)
            self.assertLess(right, size * 2)
            self.assertLess(bottom, size * 2)
            pixels = icon.load()
            opaque = {
                pixels[x, y][:3]
                for x in range(icon.width)
                for y in range(icon.height)
                if pixels[x, y][3] == 255
            }
            self.assertEqual(opaque, {(182, 128, 97)})

    def test_unsupported_svg_format_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / "bad.svg"
            for markup in (
                '<svg viewBox="0 0 10 10"><script/></svg>',
                '<svg viewBox="0 0 10 10"><image href="remote.png"/></svg>',
                '<svg viewBox="0 0 10 10"><path fill="red" d="M0 0Z"/></svg>',
            ):
                file.write_text(markup)
                with self.assertRaises(ValueError):
                    cards.prepare_svg(file, "#FFFFFF")

    def test_image_bounds_and_non_destructive_save(self):
        for width, height in ((0, 1), (2049, 1), (1, 8193), (2048, 8192)):
            with self.assertRaises(ValueError):
                cards.Canvas("#FFFFFF", width, height)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "card.png"
            canvas = logs.render_card()
            canvas.save(path)
            original = path.read_bytes()
            with cards.Image.open(path) as image:
                self.assertEqual(image.size, (canvas.w, canvas.h))
                self.assertLessEqual(image.width, cards.MAX_WIDTH)
                self.assertLessEqual(image.height, cards.MAX_HEIGHT)
            self.assertLess(len(original), 1_000_000)
            with self.assertRaises(FileExistsError):
                canvas.save(path)
            self.assertEqual(path.read_bytes(), original)

    def test_import_has_no_output_or_generated_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            environment = dict(
                os.environ, PYTHONPATH=str(cards.ROOT), PYTHONDONTWRITEBYTECODE="1"
            )
            result = subprocess.run(
                [sys.executable, "-B", "-c", "import renderer; import render_logs"],
                cwd=temporary,
                env=environment,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "")
            self.assertEqual(list(Path(temporary).iterdir()), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
