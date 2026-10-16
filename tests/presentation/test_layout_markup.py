"""Explicit inline grammar, roundtrip escaping and unchanged style evidence."""

import unittest

from ygl_test_subject.presentation import layout_markup as markup
from ygl_test_subject.presentation.layout_document import Inline, Span, Text


class LayoutMarkupTests(unittest.TestCase):
    def parse(self, source):
        return markup.parse_inline_markup(source)

    def test_required_color_gold_glow_and_strong_alias_examples(self):
        result = self.parse(
            "[purple]text[/purple] [gold glow]100.0%[/gold] [b]label[/b]"
        )
        self.assertEqual(
            result,
            Inline(
                (
                    Span("text", "#a335ee"),
                    Span(" "),
                    Span("100.0%", emphasis="gold"),
                    Span(" "),
                    Span("label", emphasis="strong"),
                )
            ),
        )

    def test_gold_without_glow_and_numbers_stay_ordinary(self):
        self.assertEqual(
            self.parse("[gold]100.0%[/gold]"), Inline((Span("100.0%", "#e5cc80"),))
        )
        self.assertEqual(self.parse("100.0%"), Inline((Span("100.0%"),)))
        self.assertEqual(
            Text("[gold glow]100.0%[/gold]").content, "[gold glow]100.0%[/gold]"
        )

    def test_exact_colors_and_theme_roles(self):
        for name, color in markup._COLORS.items():
            self.assertEqual(
                self.parse(f"[{name}]x[/{name}]"), Inline((Span("x", color),))
            )
        for role in ("strong", "secondary", "emphasis"):
            self.assertEqual(
                self.parse(f"[{role}]x[/{role}]"), Inline((Span("x", emphasis=role),))
            )
        self.assertEqual(
            self.parse("[color=#AbCdEf]x[/color]"), Inline((Span("x", "#abcdef"),))
        )

    def test_nesting_inherits_color_and_restores_outer_style(self):
        self.assertEqual(
            self.parse("[purple]one [b]two[/b] three[/purple] four"),
            Inline(
                (
                    Span("one ", "#a335ee"),
                    Span("two", "#a335ee", "strong"),
                    Span(" three", "#a335ee"),
                    Span(" four"),
                )
            ),
        )
        self.assertEqual(
            self.parse("[b]a[secondary]b[/secondary]c[/b]"),
            Inline(
                (
                    Span("a", emphasis="strong"),
                    Span("b", emphasis="secondary"),
                    Span("c", emphasis="strong"),
                )
            ),
        )

    def test_adjacent_same_styles_merge_without_changing_text(self):
        self.assertEqual(
            self.parse("[purple]one[/purple][purple]two[/purple]"),
            Inline((Span("onetwo", "#a335ee"),)),
        )
        self.assertEqual(self.parse("a[b][/b]b"), Inline((Span("ab"),)))

    def test_escaped_brackets_backslashes_and_html_remain_literal(self):
        source = r"[gold glow]no[/gold] C:\folder\name <b>raw & text</b>"
        escaped = markup.escape_inline_markup(source)
        self.assertEqual(self.parse(escaped), Inline((Span(source),)))
        self.assertEqual(
            self.parse(r"\[purple\]no\[/purple\]"),
            Inline((Span("[purple]no[/purple]"),)),
        )

    def test_rejects_unknown_attributes_nesting_closers_and_escapes(self):
        sources = (
            "[purple]x",
            "[/purple]x",
            "[purple][b]x[/purple][/b]",
            "[purple glow]x[/purple]",
            "[b color=red]x[/b]",
            "[color=red]x[/color]",
            "[color=#123]x[/color]",
            "[gold glow=true]x[/gold]",
            "[gold glow]x[/gold glow]",
            "[unknown]x[/unknown]",
            "[b]x[/strong]",
            "x]",
            "[",
            "\\",
            r"\n",
            "[B]x[/B]",
            "",
        )
        for source in sources:
            with self.subTest(source=source):
                with self.assertRaisesRegex(
                    markup.MarkupError, "^Invalid bounded inline markup$"
                ):
                    self.parse(source)

    def test_glow_cannot_be_combined_with_explicit_colors(self):
        for source in (
            "[purple][gold glow]x[/gold][/purple]",
            "[gold glow][purple]x[/purple][/gold]",
            "[gold glow][b][color=#123456]x[/color][/b][/gold]",
            "[gold][gold glow]x[/gold][/gold]",
        ):
            with self.subTest(source=source):
                with self.assertRaises(markup.MarkupError):
                    self.parse(source)

    def test_bounds_split_spans_without_truncating(self):
        source = "x" * 1024
        parsed = self.parse(source)
        self.assertEqual([len(span.text) for span in parsed.spans], [512, 512])
        self.assertEqual("".join(span.text for span in parsed.spans), source)
        for source in (
            "x" * 1025,
            "x" * (markup.MAX_MARKUP_CHARS + 1),
            "[b]" * 9 + "x" + "[/b]" * 9,
            "[b]a[/b]b" * 17,
        ):
            with self.assertRaises(markup.MarkupError):
                self.parse(source)

    def test_controls_and_multiline_are_not_inline_input(self):
        for source in (
            "hello\nworld",
            "tab\tvalue",
            "\x00",
            "\u2028",
            "\u2029",
            123,
            None,
        ):
            with self.assertRaises(markup.MarkupError):
                self.parse(source)
            with self.assertRaises(markup.MarkupError):
                markup.escape_inline_markup(source)
        with self.assertRaises(markup.MarkupError):
            markup.escape_inline_markup("[" * 5000)


if __name__ == "__main__":
    unittest.main()
