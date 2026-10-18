"""Human-only wording and bounded, deterministic help projections."""

import unittest
from dataclasses import replace

from ygl_test_subject.presentation.command_text import (
    command_title,
    project_command_heading,
    render_total_help,
)
from ygl_test_subject.presentation.rendering import (
    GenericDisplayRenderer,
    RenderingBounds,
)
from ygl_test_subject.presentation.text import TextPresenter

from tests.fixtures.minimal_module import build_package
from yomihime_game_link_sdk.declarations import ModuleDisplay
from yomihime_game_link_sdk.display import (
    CommandsBlock,
    DisplayDocument,
    DisplayLimits,
    TextBlock,
)


class CommandTextTests(unittest.TestCase):
    def test_command_projection_keeps_original_item_or_character_headings(self):
        manifest = replace(
            build_package()[0].modules[0], display=ModuleDisplay("展示名")
        )
        capability = manifest.capabilities[0]
        for original in (
            DisplayDocument("物品名", "物品 100", (TextBlock("正文"),)),
            DisplayDocument(
                "角色名", "服务器战绩", tuple(TextBlock(str(i)) for i in range(32))
            ),
        ):
            heading = project_command_heading(
                manifest, capability, original, max_heading_chars=3000
            )
            self.assertEqual(
                heading,
                ("游戏连结 · 展示名 · 查询", f"{original.title} · {original.subject}"),
            )
            projected = replace(original, title=heading[0], subject=heading[1])
            self.assertEqual(projected.ordered_blocks, original.ordered_blocks)
            self.assertEqual(
                len(projected.ordered_blocks), len(original.ordered_blocks)
            )
            self.assertIn(original.title, projected.subject)

    def test_alias_label_is_sorted_declaration_path_and_not_internal_id(self):
        manifest = build_package()[0].modules[0]
        command = manifest.commands[0]
        manifest = replace(
            manifest,
            commands=(
                replace(command, operation_path="z_alias"),
                replace(command, operation_path="a_alias"),
            ),
        )
        original = DisplayDocument("原标题", "原标题", ())
        self.assertEqual(
            project_command_heading(
                manifest, manifest.capabilities[0], original, max_heading_chars=3000
            ),
            ("游戏连结 · demo · a_alias", "原标题"),
        )
        self.assertEqual(
            project_command_heading(
                replace(manifest, commands=()),
                manifest.capabilities[0],
                original,
                max_heading_chars=3000,
            ),
            (original.title, original.subject),
        )

    def test_long_valid_original_headings_conservatively_keep_both_original_fields(
        self,
    ):
        manifest = build_package()[0].modules[0]
        original = DisplayDocument("题" * 1600, "说明" * 800, (TextBlock("正文"),))
        self.assertEqual(
            project_command_heading(
                manifest, manifest.capabilities[0], original, max_heading_chars=3000
            ),
            (original.title, original.subject),
        )

    def test_title_uses_supplied_display_name_without_rewriting_it(self):
        name = "很长的模块名称" * 8 + " FINAL FANTASY XIV"
        self.assertEqual(command_title(name, "帮助"), f"游戏连结 · {name} · 帮助")

    def test_seed_selects_from_full_directory_and_is_stable(self):
        document = DisplayDocument(
            "neutral", "ygl", (CommandsBlock(("/ygl alpha help", "/ygl beta help")),)
        )
        modules = (("甲", "alpha"), ("乙", "beta"))
        results = set()
        for seed in range(16):
            args = dict(seed=seed, presenter=TextPresenter(), max_chars=4000)
            result = render_total_help(document, modules, **args)
            self.assertEqual(result, render_total_help(document, modules, **args))
            self.assertIn("/ygl alpha help", result)
            self.assertIn("/ygl beta help", result)
            self.assertGreater(result.index("从 "), result.index("/ygl beta help"))
            results.add(result.splitlines()[-1])
        self.assertEqual(len(results), 2)

    def test_oversized_directory_keeps_explicit_truncation_without_recommendation(self):
        document = DisplayDocument("neutral", "ygl", (TextBlock("模块目录" * 100),))
        result = render_total_help(
            document,
            (("甲", "alpha"),),
            seed=1,
            presenter=TextPresenter(),
            max_chars=80,
        )
        self.assertLessEqual(len(result), 80)
        self.assertTrue(result.endswith("[内容已截断]"))
        self.assertNotIn("开始吧", result)

    def test_recommendation_is_omitted_if_only_it_exceeds_budget(self):
        document = DisplayDocument("neutral", "ygl", (TextBlock("完整目录"),))
        result = render_total_help(
            document,
            (("长名称" * 20, "alpha"),),
            seed=1,
            presenter=TextPresenter(),
            max_chars=40,
        )
        self.assertIn("完整目录", result)
        self.assertNotIn("内容已截断", result)
        self.assertNotIn("开始吧", result)


class CommandHeadingRenderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_projected_32_blocks_and_long_heading_fallback_preserve_text(self):
        manifest = build_package()[0].modules[0]
        renderer = GenericDisplayRenderer(
            RenderingBounds(3000, 100, 32, 50, 1, 1, 1, 32, 8)
        )
        for title, subject in (("具体物品名", "物品 100"), ("题" * 1600, "说" * 1600)):
            original = DisplayDocument(
                title, subject, tuple(TextBlock(f"正文{i}") for i in range(32))
            )
            heading = project_command_heading(
                manifest, manifest.capabilities[0], original, max_heading_chars=3000
            )
            projected = replace(original, title=heading[0], subject=heading[1])
            rendered = await renderer.render(projected, limits=DisplayLimits(2, 4096))
            self.assertIn(title, rendered.text)
            self.assertIn(subject, rendered.text)
            self.assertIn("正文31", rendered.text)
            self.assertNotIn("内容已截断", rendered.text)
