"""Human-only wording and bounded, deterministic help projections."""

import unittest

from ygl_test_subject.presentation.command_text import (
    command_title,
    render_total_help,
)
from ygl_test_subject.presentation.text import TextPresenter

from yomihime_game_link_sdk.display import CommandsBlock, DisplayDocument, TextBlock


class CommandTextTests(unittest.TestCase):
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
