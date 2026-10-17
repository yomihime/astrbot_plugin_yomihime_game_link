"""Offline acceptance of declarative examples, without independent painting."""

from __future__ import annotations

import ast
import json
import tempfile
import unittest
from dataclasses import fields, is_dataclass, replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import tests  # noqa: F401 - Registers the plugin namespace for Core boundary tests.
from examples.command_cards import declarative_samples as samples
from presentation.card_bitmap import RenderedBitmap
from yomihime_game_link_sdk.cards import (
    Document,
    Inline,
    PropertyGrid,
    Section,
    Span,
    Table,
)

ROOT = Path(__file__).resolve().parents[2]


def walk(value):
    yield value
    if is_dataclass(value):
        for field in fields(value):
            yield from walk(getattr(value, field.name))
    elif isinstance(value, (tuple, list)):
        for child in value:
            yield from walk(child)


class DeclarationTests(unittest.TestCase):
    def test_four_specimens_and_both_logs_themes(self):
        docs = samples.sample_documents()
        self.assertEqual(len(docs), 5)
        self.assertTrue(all(type(doc) is Document for doc in docs.values()))
        self.assertEqual(
            {d.layout for d in docs.values()}, {"directory", "detail", "table"}
        )
        for doc in docs.values():
            self.assertEqual(doc.powered_by, samples.FOOTER)
            self.assertEqual(doc.width, 1040)
            # The same public declaration must pass the real Core boundary.
            from ygl_test_subject.core.contracts.cards import validate_card

            validate_card(doc)
        self.assertEqual(docs["01_help_warm_paper"].brand, "如月怜的游戏连结")
        self.assertEqual(docs["02_item_warm_paper"].brand, "游戏连结 · FF14 · 物品")
        self.assertEqual(docs["03_logs_cool_white"].brand, "游戏连结 · FF14 · Logs")
        self.assertEqual(docs["05_market_warm_paper"].brand, "游戏连结 · FF14 · 市场")
        light = docs["03_logs_cool_white"]
        dark = docs["04_logs_deep_indigo"]
        self.assertEqual(replace(light, theme=dark.theme), dark)

    def test_help_uses_bundled_manifest_catalog_and_available_only(self):
        catalog = samples.help_catalog_fixture()
        manifest = json.loads(
            (ROOT / "modules/ff14/yomihime.manifest.json").read_text(encoding="utf-8")
        )
        declared = manifest["modules"][0]
        self.assertEqual(catalog[0].name, declared["display"]["default_name"])
        self.assertEqual(catalog[0].route, declared["route"])
        self.assertEqual(
            catalog[0].commands,
            tuple(c["operation_path"] for c in declared["commands"]),
        )
        doc = samples.build_help_document(catalog)
        self.assertEqual(doc.subtitle, "从最终幻想XIV开始吧")
        strings = tuple(v for v in walk(doc) if isinstance(v, str))
        self.assertIn("/ygl ff14 help", strings)
        self.assertEqual(len(doc.blocks), 1)
        for flag in ("loaded", "enabled", "available"):
            empty = samples.build_help_document((replace(catalog[0], **{flag: False}),))
            self.assertNotIn("/ygl ff14 help", tuple(walk(empty)))
            self.assertEqual(empty.blocks[0].title, "暂无可用模块")
        self.assertEqual(len(samples.build_help_document(catalog + catalog).blocks), 1)
        other = samples.CatalogModule("other", "另一个模块", "other", ("status",))
        other_doc = samples.build_help_document((other,), seed=14)
        other_strings = tuple(v for v in walk(other_doc) if isinstance(v, str))
        self.assertIn("查看模块命令与用法", other_strings)
        self.assertNotIn("物品 · 市场 · 战绩 · 活动日历", other_strings)
        self.assertEqual(
            samples.build_help_document(catalog + (other,), seed=14),
            samples.build_help_document(catalog + (other,), seed=14),
        )

    def test_item_preserves_facts_omissions_sources_and_approved_design(self):
        doc = samples.build_item_document()
        self.assertEqual(doc.title, "犎牛牛排")
        self.assertEqual(doc.theme, "warm_paper")
        self.assertIsNotNone(doc.logo)
        props = doc.blocks[0]
        self.assertIsInstance(props, PropertyGrid)
        self.assertEqual(
            [(p.label, p.value) for p in props.properties],
            [("物品 ID", "44091"), ("物品等级", "684"), ("装备等级", "1")],
        )
        strings = tuple(v for v in walk(doc) if isinstance(v, str))
        for expected in (
            "缇姨家的塔可饼的老主顾",
            "任务 · 图莱尤拉 · 记录 ID 70996",
            "职业 ID 15 · 等级 99",
            "亚斯拉尼蒜头  × 2",
            "物品 ID 8  × 8",
            "物品 ID 13  × 8",
            "Garland 制作关联信息不完整",
        ):
            self.assertIn(expected, strings)
        self.assertEqual(len(doc.blocks[2].blocks[0].cells), 2)
        self.assertEqual(
            samples.ITEM_MATERIALS[-2:], (("物品 ID 8", "× 8"), ("物品 ID 13", "× 8"))
        )
        self.assertEqual(len(doc.sources), 1)
        self.assertFalse(any("https://" in text for text in strings))
        self.assertEqual(len(samples.ITEM_SOURCE_URLS), 2)

    def test_market_preserves_historical_prices_world_upload_ages_and_scope(self):
        doc = samples.build_market_document()
        self.assertEqual(doc.title, "犎牛牛排")
        self.assertEqual(doc.subtitle, "物品 44091 · 中国全区")
        self.assertEqual(doc.brand, "游戏连结 · FF14 · 市场")
        self.assertEqual(doc.theme, "warm_paper")
        self.assertIsNotNone(doc.logo)
        self.assertEqual(doc.sources, ("数据源：Universalis",))
        panels = [block for block in doc.blocks if isinstance(block, Section)]
        self.assertEqual(len(panels), 2)
        self.assertEqual(
            samples.MARKET_FIXTURE,
            (
                ("NQ", "30", "静语庄园", "世界上传于 12 分钟前"),
                ("HQ", "500", "神拳痕", "世界上传于 2 小时前"),
            ),
        )
        for panel, fixture in zip(panels, samples.MARKET_FIXTURE, strict=True):
            quality, price, world, upload_age = fixture
            row = panel.blocks[0]
            self.assertEqual(row.cells[0].content, quality)
            self.assertEqual(row.cells[1].content, f"{price} Gil / 件")
            self.assertEqual(row.cells[1].style, "title")
            self.assertEqual(row.cells[1].wrap, "nowrap")
            self.assertEqual(row.cells[2].blocks[0].content, world)
            self.assertEqual(row.cells[2].blocks[1].content, upload_age)
        strings = tuple(v for v in walk(doc) if isinstance(v, str))
        self.assertIn(
            "有限返回样本中的最低价；非实时全量报价，不保证此刻仍可买到", strings
        )
        self.assertIn("时间为对应世界的上传时间，并非该条挂牌时间", strings)
        for forbidden in ("演示数据", "合成", "fixture", "实时有货", "保证最低"):
            self.assertFalse(any(forbidden in text for text in strings))

    def test_logs_groups_alignment_regions_and_no_debug_labels(self):
        doc = samples.build_logs_document()
        self.assertEqual(doc.title, "ChatGPT @ OpenAI")
        self.assertEqual(len(doc.blocks), 2)
        self.assertTrue(all(isinstance(section, Section) for section in doc.blocks))
        self.assertEqual([len(s.blocks[0].rows) for s in doc.blocks], [7, 13])
        for section in doc.blocks:
            table = section.blocks[0]
            self.assertIsInstance(table, Table)
            self.assertEqual(len(table.columns), 5)
            self.assertEqual(table.columns[3].title, "过本次数")
            self.assertTrue(all(c.wrap == "nowrap" for c in table.columns[2:]))
            self.assertEqual(table.columns[-1].align, "right")
            self.assertEqual(table.columns[-1].valign, "center")
        strings = tuple(v for v in walk(doc) if isinstance(v, str))
        for forbidden in ("13 条记录", "13条记录", "样式图例", "合成", "fixture"):
            self.assertFalse(any(forbidden in text for text in strings))
        self.assertEqual(doc.sources, ("数据源：cn.fflogs.com",))
        self.assertEqual(
            samples.build_logs_document(region="Global").sources,
            ("数据源：www.fflogs.com",),
        )
        with self.assertRaises(ValueError):
            samples.build_logs_document(region="other")

    def test_no_independent_painter_or_legacy_renderer_dependencies(self):
        source = Path(samples.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = [
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        ]
        imports += [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        ]
        forbidden = (
            "PIL",
            "renderer",
            "ff14_logs_reference",
            "rich_text_reference",
            "cairosvg",
            "presentation.rich_text",
            "presentation.layout_document",
            "presentation.layout_assets",
        )
        for name in imports:
            self.assertFalse(
                any(name == f or name.startswith(f + ".") for f in forbidden), name
            )
        self.assertIn("presentation.layout_renderer", imports)
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        self.assertEqual(
            sum(
                isinstance(c.func, ast.Name) and c.func.id == "render_document"
                for c in calls
            ),
            1,
        )


class MetricTests(unittest.TestCase):
    def test_raw_grades_before_rounding_and_standard_colors(self):
        for raw, grade in (
            (0, "#666666"),
            ("24.999", "#666666"),
            (25, "#1eff00"),
            ("49.999", "#1eff00"),
            (50, "#0070ff"),
            ("74.999", "#0070ff"),
            (75, "#a335ee"),
            ("94.999", "#a335ee"),
            (95, "#ff8000"),
            ("98.999", "#ff8000"),
            (99, "#e268a8"),
            ("99.96", "#e268a8"),
            (100, "gold"),
        ):
            span = samples.percentile(raw)
            self.assertEqual(span.emphasis, "gold" if grade == "gold" else "normal")
            self.assertEqual(span.color, None if grade == "gold" else grade)
        self.assertEqual(
            samples.percentile(Decimal("99.96")),
            Span("99.9%", color="#e268a8"),
        )
        self.assertEqual(samples.percentile("100"), Span("100.0%", emphasis="gold"))
        docs = samples.build_logs_document()
        pink = docs.blocks[1].blocks[0].rows[0][-1]
        self.assertEqual(pink, Inline((samples.percentile("99.7"),)))

    def test_missing_zero_unevaluable_error_and_precision(self):
        for function in (samples.percentile, samples.rdps, samples.count):
            self.assertEqual(function(None), Span("—"))
            self.assertEqual(
                function(samples.Unevaluable("无法评价")), Span("无法评价")
            )
            self.assertNotEqual(function(0).text, "—")
            for raw in (
                True,
                False,
                -1,
                "NaN",
                "Infinity",
                "bad",
                "",
                object(),
                "9" * 129,
                10**1000,
                Decimal("1e1000000"),
                Decimal("1e-1000000"),
            ):
                self.assertEqual(function(raw), Span("ERR", color="#c83e4d"))
        self.assertEqual(samples.rdps("41234.567890123"), Span("41,235"))
        self.assertEqual(samples.rdps("12345.499999"), Span("12,345"))
        self.assertEqual(samples.percentile("44.65").text, "44.6%")
        self.assertEqual(samples.count(0), Span("0"))
        self.assertEqual(samples.count("1.1").color, "#c83e4d")
        self.assertEqual(samples.percentile("100.1").color, "#c83e4d")


class OutputTests(unittest.TestCase):
    def test_render_entry_calls_the_shared_renderer(self):
        doc = samples.build_item_document()
        expected = RenderedBitmap(b"fake", 1040, 1000)
        with patch.object(samples, "render_document", return_value=expected) as render:
            self.assertIs(samples.render_sample(doc), expected)
        render.assert_called_once_with(doc)

    def test_outputs_are_review_only_no_overwrite_and_provenance_is_external(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            expected = RenderedBitmap(b"fixture-not-a-real-png", 1040, 1000)
            with patch.object(
                samples, "render_sample", return_value=expected
            ) as render:
                provenance = samples.render_samples(output)
                self.assertEqual(render.call_count, 5)
                with self.assertRaises(FileExistsError):
                    samples.render_samples(output)
                self.assertEqual(render.call_count, 5)
            saved = json.loads((output / "provenance.json").read_text(encoding="utf-8"))
            self.assertEqual(
                saved["item"]["source_urls"], list(samples.ITEM_SOURCE_URLS)
            )
            self.assertIn("User-approved item-detail", saved["item"]["design"])
            self.assertIn("not a live query", saved["item"]["design"])
            self.assertIn("not a live Universalis quote", saved["market"]["scope"])
            self.assertIn("not individual listing times", saved["market"]["timestamps"])
            self.assertIn("05_market_warm_paper", saved["images"])
            self.assertEqual(len(provenance["images"]), 5)
        with self.assertRaises(ValueError):
            samples.render_samples(ROOT / "examples/command_cards/samples/new")

    def test_shared_renderer_renders_all_samples_with_bounded_output(self):
        for name, doc in samples.sample_documents().items():
            with self.subTest(sample=name):
                output = samples.render_sample(doc)
                self.assertTrue(output.content.startswith(b"\x89PNG\r\n\x1a\n"))
                self.assertEqual(output.width, 1040)
                self.assertLessEqual(output.height, 8192)
                self.assertLessEqual(len(output.content), 1_048_576)
                if doc.layout == "table":
                    self.assertGreater(output.height, 1400)


if __name__ == "__main__":
    unittest.main()
