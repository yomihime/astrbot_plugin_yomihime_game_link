"""Generic compact help, FF14 declarations, rendering and unavailable output."""

import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.adapters.astrbot.command_bridge import AstrBotCommandBridge
from ygl_test_subject.core.help_catalog import HelpCatalog
from ygl_test_subject.extensions.disk_manifest import parse_manifest
from ygl_test_subject.presentation.command_card import (
    CommandCardRenderer,
    build_help_card,
)
from ygl_test_subject.presentation.layout_renderer import (
    measure_document,
    render_document,
)
from ygl_test_subject.presentation.png_validation import validate_png

from tests.core.test_help_catalog import Registry, _Handler, _module, _package
from yomihime_game_link_sdk import cards
from yomihime_game_link_sdk.services import ModuleHandlers

ROOT = Path(__file__).resolve().parents[2]


class Event:
    def __init__(self, text):
        self.text = text

    def get_message_str(self):
        return self.text


class CompactHelpTests(unittest.TestCase):
    def setUp(self):
        self.package = parse_manifest(
            (ROOT / "modules/ff14/yomihime.manifest.json").read_bytes()
        )
        self.manifest = self.package.modules[0]
        self.handler = _Handler()
        self.handler.normalize = lambda value: value
        self.handler.collect = self.handler.invoke
        self.handler.evaluate = self.handler.invoke
        self.registry = Registry()
        self.registry.register_package(
            self.package,
            {
                "ff14": ModuleHandlers(
                    {c.capability_id: self.handler for c in self.manifest.capabilities},
                    {c.collector_id: self.handler for c in self.manifest.schedules},
                    {s.matcher_id: self.handler for s in self.manifest.subscriptions},
                )
            },
        )
        self.registry.is_active = lambda module: True
        self.catalog = HelpCatalog(active_query=lambda module: True)
        self.bridge = AstrBotCommandBridge(self.registry, catalog=self.catalog)

    def test_five_boxes_have_declared_usage_then_summary_and_no_policy(self):
        response = self.bridge.parse(Event("/ygl ff14 help"))
        card = build_help_card(response.source)
        boxes = [b for b in card.blocks if isinstance(b, cards.Section)]
        self.assertEqual(len(boxes), 5)
        self.assertEqual(
            [b.blocks[0].content.spans[0].text.split()[2] for b in boxes],
            ["market", "item", "logs", "calendar", "status"],
        )
        for box in boxes:
            usage, summary = box.blocks[0].content.spans
            self.assertEqual(usage.emphasis, "strong")
            self.assertEqual(summary.emphasis, "secondary")
            self.assertIn(summary.text, response)
            self.assertIn(usage.text.strip(), response)
        self.assertNotIn("calendar subscribe", response)
        self.assertNotIn("output", response)
        self.assertNotIn("可用性由执行时", response)
        self.assertEqual(card.layout, "table")
        self.assertEqual(card.theme, "warm_paper")

    def test_calendar_children_and_each_detail_are_reachable(self):
        response = self.bridge.parse(Event("/ygl ff14 calendar help"))
        self.assertEqual(response.count("查看活动日历与订阅指令。"), 1)
        self.assertNotIn("概览：", response)
        for command in self.manifest.commands:
            if command.operation_path.startswith("calendar "):
                self.assertIn(command.operation_path + " help", response)
                detail = self.bridge.parse(
                    Event("/ygl ff14 " + command.operation_path + " help")
                )
                self.assertIn("权限：仅本人私聊可用", detail)
                self.assertIn("用法：", detail)
        self.assertEqual(self.handler.calls, 0)

    def test_output_has_no_command_capability_tool_page_or_invocation(self):
        self.assertNotIn(
            "ff14.logs.output_percentile",
            [c.capability_id for c in self.manifest.capabilities],
        )
        self.assertNotIn("output", [c.operation_path for c in self.manifest.commands])
        self.assertNotIn("ff14.logs.output_percentile", repr(self.manifest.tools))
        self.assertNotIn("ff14.logs.output_percentile", repr(self.manifest.pages))
        for text in ("/ygl ff14 output cn raid savage job", "/ygl ff14 output help"):
            self.assertIn("未找到命令", self.bridge.parse(Event(text)))
        self.assertEqual(self.handler.calls, 0)

    def test_generic_prefix_hierarchy_and_legacy_safe_help_fallback(self):
        module, handlers, _ = _module("demo", "other", operation_prefix="group")
        first = replace(
            module.commands[0],
            help_text="Group description\n概览：/ygl other group <name>\n用法：/ygl other group <name> [limit=2]",
        )
        child = replace(
            first,
            operation_path="group nested deep",
            help_text="Child description\n概览：/ygl WRONG group <name>",
        )
        module = replace(module, commands=(first, child, module.commands[1]))
        registry = Registry()
        registry.register_package(_package("generic", module), {"demo": handlers})
        overview = self.catalog.module(registry.snapshot(), "other")
        self.assertEqual(len(overview.card.blocks), 3)
        self.assertNotIn("nested", repr(overview))
        details = self.catalog.command(registry.snapshot(), "other", "group")
        self.assertIn("/ygl other group nested deep help", repr(details))
        self.assertEqual(
            HelpCatalog._overview("other", child)[0],
            "/ygl other group nested deep help",
        )
        oversized = replace(
            first, help_text="Summary\n概览：/ygl other group " + "x" * 513
        )
        self.assertEqual(
            HelpCatalog._overview("other", oversized)[0], "/ygl other group help"
        )

    def test_legacy_summary_that_looks_like_marker_is_not_dropped(self):
        module, handlers, _ = _module("demo", "other")
        command = replace(module.commands[0], help_text="概览：legacy summary")
        module = replace(module, commands=(command,))
        registry = Registry()
        registry.register_package(_package("legacy", module), {"demo": handlers})
        details = self.catalog.command(
            registry.snapshot(), "other", command.operation_path
        )
        self.assertEqual(details.ordered_blocks[0].text, "概览：legacy summary")
        self.assertEqual(details.card.blocks[0].content, "概览：legacy summary")
        self.assertEqual(
            HelpCatalog._overview("other", command)[0], "/ygl other 查询 help"
        )

    def test_real_render_and_exact_text_fallback(self):
        response = self.bridge.parse(Event("/ygl ff14 help"))
        card = build_help_card(response.source)
        bitmap = render_document(card)
        validate_png(bitmap)
        layout = measure_document(card)
        self.assertEqual(bitmap.width, 1040)
        self.assertEqual(bitmap.height, layout.height)
        with patch(
            "ygl_test_subject.presentation.command_card.render_document",
            side_effect=RuntimeError("unavailable"),
        ):
            fallback = CommandCardRenderer().help(str(response), card=card)
        self.assertIsNone(fallback.bitmap)
        self.assertEqual(fallback.text, str(response))
        self.assertIn("/ygl ff14 logs <服务器名> <角色名>", fallback.text)

    def test_root_directory_projection_ignores_detail_cards(self):
        root = self.bridge.parse(Event("/ygl help"))
        self.assertIsNone(root.source.document.card)
        card = build_help_card(root.source)
        self.assertEqual(card.title, "游戏，从这里连结")
        self.assertEqual(card.layout, "directory")
        self.assertEqual(len(card.blocks), 1)
        self.assertNotIn("<物品", repr(card))
