"""Real PNG, geometry, bounded contract and shared production effects tests."""

import builtins
import dataclasses
import io
import unittest
from unittest.mock import patch

from PIL import Image as PillowImage
from PIL import ImageFont
from ygl_test_subject.presentation import layout_renderer as renderer
from ygl_test_subject.presentation.card_bitmap import BitmapBounds, BitmapUnavailable
from ygl_test_subject.presentation.layout_assets import Asset
from ygl_test_subject.presentation.layout_document import (
    Column,
    Document,
    Image,
    Inline,
    Property,
    PropertyGrid,
    Row,
    Section,
    Span,
    Table,
    Text,
    ThemePalette,
)
from ygl_test_subject.presentation.png_validation import validate_png
from ygl_test_subject.presentation.rich_text import (
    Grade,
    RichSession,
    RichText,
    TextCell,
    TextRole,
    TextSpan,
)

from yomihime_game_link_sdk.cards import Cell, TaggedText


class Font:
    def supports(self, codepoint):
        return 32 <= codepoint <= 126

    def load(self, size):
        return ImageFont.load_default(size=size)


class LayoutRendererTests(unittest.TestCase):
    def setUp(self):
        self.font = Font()

    def document(self, *blocks, **kwargs):
        return Document("A composed card", blocks=blocks, brand="GAME LINK", **kwargs)

    def render(self, document, **kwargs):
        return renderer.render_document(document, font_provider=self.font, **kwargs)

    def measure(self, document, **kwargs):
        return renderer.measure_document(document, font_provider=self.font, **kwargs)

    def assert_reason(self, reason, callback):
        with self.assertRaises(BitmapUnavailable) as raised:
            callback()
        self.assertEqual(raised.exception.reason, reason)

    def test_real_png_all_blocks_and_no_external_effects(self):
        image = PillowImage.new("RGB", (20, 40), "red")
        with io.BytesIO() as buffer:
            image.save(buffer, format="PNG")
            asset = Asset("png", buffer.getvalue(), "A red rectangle")
        image.close()
        document = self.document(
            Section(
                "A useful section",
                (
                    Row(
                        (
                            Image(asset, width=72, height=72, fit="cover"),
                            Text("A complete description"),
                        ),
                        weights=(1, 4),
                    ),
                    PropertyGrid(
                        (Property("Level", "640"), Property("Source", "Fixture")), 2
                    ),
                    Table(
                        (Column("Item", 3), Column("Value", 1, "right", "center")),
                        (
                            ("First", "100"),
                            ("Second", TextCell("25", "center", "bottom")),
                        ),
                    ),
                ),
            ),
            Text("Complete footer-side note", style="caption"),
            sources=("Source: offline fixture",),
            logo=asset,
        )
        result = self.render(document)
        validate_png(result)
        with PillowImage.open(io.BytesIO(result.content)) as actual:
            self.assertEqual(actual.mode, "RGB")
            self.assertEqual(actual.size, (result.width, result.height))
            self.assertEqual(actual.n_frames, 1)
            self.assertEqual(actual.info, {})
        self.assertEqual(result, self.render(document))

    def test_sdk_tags_are_explicit_and_cells_preserve_alignment(self):
        from ygl_test_subject.presentation.layout_markup import parse_inline_markup

        tagged = TaggedText("[blue]Value[/blue] [gold glow]100[/gold]")
        declaration = self.document(
            Table((Column("Score"),), ((Cell(tagged, "right", "bottom"),),))
        )
        expected = self.document(
            Table(
                (Column("Score"),),
                ((Cell(parse_inline_markup(tagged.source), "right", "bottom"),),),
            )
        )
        self.assertEqual(self.render(declaration), self.render(expected))
        literal = self.document(Text(tagged.source))
        parsed = self.document(Text(tagged))
        self.assertNotEqual(self.render(literal).content, self.render(parsed).content)

    def test_explicit_tag_source_budgets_fail_before_font_loading(self):
        class UnusedFont:
            def supports(self, _codepoint):
                raise AssertionError("invalid tags must fail before fonts")

        for source, reason in (
            ("x" * 4097, "layout_overflow"),
            ("[unknown]value[/unknown]", "invalid_input"),
            ("[blue]value[/gold]", "invalid_input"),
        ):
            with self.subTest(source=source[:40]):
                self.assert_reason(
                    reason,
                    lambda: renderer.measure_document(
                        self.document(Text(TaggedText(source))),
                        font_provider=UnusedFont(),
                    ),
                )
        # Tiny visible values cannot conceal more than the input budget
        # of styling syntax; the parser is never an unbounded work loophole.
        source = "[blue]x[/blue]" * 32
        document = self.document(*(Text(TaggedText(source)) for _ in range(60)))
        self.assert_reason(
            "layout_overflow",
            lambda: renderer.measure_document(document, font_provider=UnusedFont()),
        )

    def test_custom_palette_and_presets(self):
        palette = ThemePalette(
            "#102030",
            "#fdfdfd",
            "#abcdef",
            "#99aabb",
            "#778899",
            "#223344",
            "#556677",
            "#112233",
            "#5a6580",
        )
        result = self.render(self.document(Text("Custom colors"), theme=palette))
        with PillowImage.open(io.BytesIO(result.content)) as image:
            self.assertEqual(image.getpixel((0, 0)), (16, 32, 48))
            self.assertEqual(image.getpixel((35, 100)), (253, 253, 253))
        results = [
            self.render(self.document(Text("Theme"), theme=theme))
            for theme in renderer.THEMES
        ]
        self.assertEqual(len({r.content for r in results}), 3)
        self.assertEqual(len({r.height for r in results}), 1)

    def test_nested_row_grid_and_table_geometry_has_no_overlaps(self):
        document = self.document(
            Row(
                (
                    Section("Left", (Text("A " * 100),)),
                    Section("Right", (Text("Short"),)),
                ),
                valign="bottom",
            ),
            Table(
                (Column("Name", 3), Column("Count", 1, "right", "center")),
                (("Multiline\nname", "123"), ("Second", "4")),
            ),
            PropertyGrid(
                tuple(Property(str(i), "value " * 8) for i in range(5)), columns=3
            ),
        )
        measured = self.measure(document)
        boxes = {b.path: b for b in measured.boxes}
        self.assertLessEqual(boxes["blocks/0"].bounds[3], boxes["blocks/1"].bounds[1])
        self.assertLessEqual(boxes["blocks/1"].bounds[3], boxes["blocks/2"].bounds[1])
        self.assertEqual(
            boxes["blocks/0/cells/0"].bounds[3], boxes["blocks/0/cells/1"].bounds[3]
        )
        for box in measured.boxes:
            x0, y0, x1, y1 = box.bounds
            self.assertTrue(0 <= x0 <= x1 <= measured.width, box)
            self.assertTrue(0 <= y0 <= y1 <= measured.height, box)
        for a, b in zip(
            [boxes[f"blocks/1/rows/{i}"] for i in range(3)],
            [boxes[f"blocks/1/rows/{i}"] for i in range(1, 3)],
        ):
            self.assertLessEqual(a.bounds[3], b.bounds[1])
        self.render(document)

    def test_long_text_wraps_without_omission_and_nowrap_fails(self):
        content = "A long English word boundary paragraph. " * 25 + "Z" * 120
        document = self.document(Text(content), width=500)
        captured = []
        original = RichSession.add

        def record(session, span, **kwargs):
            captured.append(span.text)
            return original(session, span, **kwargs)

        with patch.object(RichSession, "add", record):
            result = self.render(document)
        self.assertIn(content, "".join(captured))
        self.assertGreater(result.height, 700)
        self.assert_reason(
            "layout_overflow",
            lambda: self.render(self.document(Text(content, wrap="nowrap"), width=500)),
        )
        table = Table((Column("Metric", wrap="nowrap"),), (("1234567890" * 20,),))
        self.assert_reason(
            "layout_overflow", lambda: self.render(self.document(table, width=500))
        )

    def test_raw_color_contrast_shadow_and_standard_gold_reuse_rich_session(self):
        captured = []
        original = RichSession.add

        def record(session, span, **kwargs):
            info = original(session, span, **kwargs)
            captured.append(info)
            return info

        document = self.document(
            Text(
                Inline(
                    (
                        Span("Low contrast", color="#fcf4e9"),
                        Span(" 100", emphasis="gold"),
                    )
                )
            ),
            theme=dataclasses.replace(renderer.THEMES["warm_paper"], surface="#fcf4e9"),
        )
        with patch.object(RichSession, "add", record):
            self.render(document)
        low = next(info for info in captured if info.text == "Low contrast")
        gold = next(info for info in captured if info.text == " 100")
        self.assertTrue(low.shadow)
        self.assertLess(low.contrast, 1.5)
        self.assertTrue(gold.gold_glow)
        self.assertEqual(gold.face, "#e5cc80")

    def test_bounds_reject_before_font_load_and_no_clipping(self):
        cases = (
            (self.document(Text("x" * 4097)), "layout_overflow"),
            (self.document(Text("x"), width=4096), "layout_overflow"),
            (self.document(Text("\x00")), "invalid_input"),
            (self.document(Text(Inline((Span("x", color="red"),)))), "invalid_input"),
            (
                self.document(
                    Text(Inline((Span("x", color="#ffffff", emphasis="gold"),)))
                ),
                "invalid_input",
            ),
            (
                self.document(Row((Text("x"),), weights=(float("nan"),))),
                "invalid_input",
            ),
            (self.document(Table((Column("x"),), (("a", "b"),))), "invalid_input"),
        )
        for document, reason in cases:
            with self.subTest(document=document):
                with patch.object(
                    Font, "load", side_effect=AssertionError("must validate first")
                ):
                    self.assert_reason(reason, lambda: self.render(document))
        self.assert_reason(
            "layout_overflow",
            lambda: self.render(
                self.document(Text("long " * 100)), bounds=BitmapBounds(max_height=100)
            ),
        )
        self.assert_reason(
            "byte_limit",
            lambda: self.render(
                self.document(Text("body")), bounds=BitmapBounds(max_bytes=100)
            ),
        )

    def test_optional_asset_fallback_and_required_asset_failure(self):
        optional = Asset("png", b"invalid", "Missing art", False)
        result = self.render(self.document(Image(optional, width=160, height=100)))
        validate_png(result)
        self.assert_reason(
            "invalid_input",
            lambda: self.render(
                self.document(Image(dataclasses.replace(optional, required=True)))
            ),
        )

    def test_right_aligned_metric_uses_actual_ink_and_vertical_center(self):
        document = self.document(
            Table(
                (Column("Name", 3), Column("Value", 1, "right", "center")),
                (("Two\nlines", " 100.0% "),),
            )
        )
        measured = self.measure(document)
        cell = next(box for box in measured.boxes if box.path == "blocks/0/rows/1/1")
        captured = []
        original = RichSession.add

        def record(session, span, **kwargs):
            info = original(session, span, **kwargs)
            captured.append(info)
            return info

        with patch.object(RichSession, "add", record):
            self.render(document)
        actual = next(info for info in captured if info.text == " 100.0% ")
        self.assertLessEqual(abs(actual.bounds[2] - cell.bounds[2]), 0.25)
        self.assertLessEqual(
            abs(
                (actual.bounds[1] + actual.bounds[3]) / 2
                - (cell.bounds[1] + cell.bounds[3]) / 2
            ),
            0.25,
        )

    def test_missing_pillow_has_stable_failure_for_measure_and_render(self):
        original = builtins.__import__

        def blocked(name, *args, **kwargs):
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError("private diagnostic must not leak")
            return original(name, *args, **kwargs)

        for call in (self.measure, self.render):
            with patch("builtins.__import__", side_effect=blocked):
                self.assert_reason(
                    "dependency_missing", lambda: call(self.document(Text("text")))
                )

    def test_theme_catalog_is_immutable_and_authoritative(self):
        options = renderer.list_themes()
        self.assertIsInstance(options, tuple)
        self.assertEqual(tuple(option.id for option in options), tuple(renderer.THEMES))
        self.assertEqual(
            tuple(option.label for option in options), ("暖桃纸", "冷白", "深靛")
        )
        for option in options:
            self.assertIs(option.palette, renderer.THEMES[option.id])
            with self.assertRaises(dataclasses.FrozenInstanceError):
                option.label = "changed"
            with self.assertRaises(dataclasses.FrozenInstanceError):
                option.palette.surface = "#000000"
        with self.assertRaises(TypeError):
            renderer.THEMES["warm_paper"] = renderer.THEMES["cool_white"]

    def test_none_follows_core_and_never_incoming_document_theme(self):
        custom = dataclasses.replace(renderer.THEMES["warm_paper"], surface="#fffedd")
        for incoming in ("warm_paper", "cool_white", "deep_indigo", custom):
            with self.subTest(incoming=incoming):
                document = self.document(Text("Follow Core"), theme=incoming)
                resolved = renderer.apply_theme_selection(document, "cool_white", None)
                self.assertIsNot(resolved, document)
                self.assertEqual(resolved.theme, "cool_white")
                self.assertIs(document.theme, incoming)
                expected = dataclasses.replace(document, theme="cool_white")
                self.assertEqual(self.render(resolved), self.render(expected))

    def test_core_cards_use_core_palette_without_module_setting(self):
        document = self.document(Text("Help directory"), theme="warm_paper")
        for option in renderer.list_themes():
            resolved = renderer.apply_theme_selection(document, option.id)
            self.assertEqual(resolved.theme, option.id)
            self.assertIs(resolved.blocks, document.blocks)
        self.assertEqual(document.theme, "warm_paper")

    def test_module_override_is_local_and_accepts_custom_palette(self):
        first = self.document(Text("First module"), theme="warm_paper")
        second = self.document(Text("Second module"), theme="deep_indigo")
        overridden = renderer.apply_theme_selection(first, "cool_white", "deep_indigo")
        following = renderer.apply_theme_selection(second, "cool_white", None)
        self.assertEqual(overridden.theme, "deep_indigo")
        self.assertEqual(following.theme, "cool_white")
        custom = dataclasses.replace(renderer.THEMES["warm_paper"], surface="#fffedd")
        customized = renderer.apply_theme_selection(first, "cool_white", custom)
        self.assertIs(customized.theme, custom)
        self.assertEqual(first.theme, "warm_paper")
        self.assertEqual(second.theme, "deep_indigo")
        self.assertEqual(renderer.THEMES["cool_white"].surface, "#f8fafc")
        self.assertEqual(
            self.render(customized),
            self.render(dataclasses.replace(first, theme=custom)),
        )

    def test_explicit_theme_selection_changes_only_theme(self):
        custom = dataclasses.replace(renderer.THEMES["warm_paper"], surface="#fffedd")
        document = self.document(Text("Original declaration"), theme=custom)
        original = dataclasses.asdict(document)
        for option in renderer.list_themes():
            resolved = renderer.apply_theme_selection(document, "cool_white", option.id)
            self.assertIsNot(resolved, document)
            self.assertEqual(resolved.theme, option.id)
            for field in dataclasses.fields(document):
                if field.name != "theme":
                    self.assertIs(
                        getattr(resolved, field.name), getattr(document, field.name)
                    )
            with self.assertRaises(dataclasses.FrozenInstanceError):
                resolved.theme = "warm_paper"
        self.assertEqual(dataclasses.asdict(document), original)

    def test_theme_override_preserves_explicit_colors_and_grade_effects(self):
        inline = Inline((Span("Explicit purple", color="#a335ee"),))
        rich = RichText((TextSpan("100", TextRole.GRADED, Grade.GOLD),))
        document = self.document(Text(inline), Text(rich))
        resolved = renderer.apply_theme_selection(document, "cool_white", "deep_indigo")
        self.assertIs(resolved.blocks, document.blocks)
        self.assertIs(resolved.blocks[0].content, inline)
        self.assertIs(resolved.blocks[1].content, rich)
        captured = []
        original = RichSession.add

        def record(session, span, **kwargs):
            info = original(session, span, **kwargs)
            captured.append(info)
            return info

        with patch.object(RichSession, "add", record):
            result = self.render(resolved)
        purple = next(info for info in captured if info.text == "Explicit purple")
        gold = next(info for info in captured if info.text == "100")
        self.assertEqual(purple.face, "#a335ee")
        self.assertEqual(gold.face, "#e5cc80")
        self.assertTrue(gold.gold_glow)
        with PillowImage.open(io.BytesIO(result.content)) as image:
            self.assertEqual(image.getpixel((0, 0)), (15, 20, 35))

    def test_theme_selection_rejects_unknown_ids_and_invalid_types(self):
        document = self.document(Text("Unchanged"))
        for invalid in ("unknown", "", "WARM_PAPER", "#ffffff", True, 0, [], {}):
            with self.subTest(invalid=invalid):
                self.assert_reason(
                    "invalid_input",
                    lambda: renderer.apply_theme_selection(document, invalid),
                )
                self.assert_reason(
                    "invalid_input",
                    lambda: renderer.apply_theme_selection(
                        document, "cool_white", invalid
                    ),
                )
                self.assert_reason(
                    "invalid_input",
                    lambda: renderer.apply_theme_selection(
                        document, invalid, "warm_paper"
                    ),
                )
        self.assert_reason(
            "invalid_input", lambda: renderer.apply_theme_selection(document, None)
        )
        self.assert_reason(
            "invalid_input",
            lambda: renderer.apply_theme_selection(object(), "cool_white"),
        )
        self.assertEqual(document.theme, "warm_paper")

    def test_malformed_custom_module_palette_fails_without_fallback(self):
        document = self.document(Text("Explicit module palette"))
        for field in dataclasses.fields(ThemePalette):
            for invalid in ("red", "#ffff", "#zzzzzz", "#１２３４５６", None, 123):
                with self.subTest(field=field.name, invalid=invalid):
                    palette = dataclasses.replace(
                        renderer.THEMES["warm_paper"], **{field.name: invalid}
                    )
                    self.assert_reason(
                        "invalid_input",
                        lambda: renderer.apply_theme_selection(
                            document, "cool_white", palette
                        ),
                    )
        self.assert_reason(
            "invalid_input",
            lambda: renderer.apply_theme_selection(
                document, "invalid-core", renderer.THEMES["warm_paper"]
            ),
        )

    def test_theme_catalog_and_selection_need_no_font_or_image_dependency(self):
        original = builtins.__import__

        def blocked(name, *args, **kwargs):
            if name == "PIL" or name.startswith("PIL."):
                raise AssertionError("Pure theme selection must not import Pillow")
            return original(name, *args, **kwargs)

        document = self.document(Text("Headless configuration"))
        with (
            patch("builtins.__import__", side_effect=blocked),
            patch.object(
                renderer,
                "BundledFontProvider",
                side_effect=AssertionError("No font IO"),
            ),
        ):
            choices = renderer.list_themes()
            selected = renderer.apply_theme_selection(document, choices[1].id)
        self.assertEqual(selected.theme, "cool_white")

    def test_immutable_contract_and_no_implicit_markup(self):
        document = self.document(Text("[gold glow]100[/gold]"))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            document.title = "changed"
        capture = []
        original = RichSession.add

        def record(session, span, **kwargs):
            info = original(session, span, **kwargs)
            capture.append(info)
            return info

        with patch.object(RichSession, "add", record):
            self.render(document)
        self.assertTrue(any(i.text == "[gold glow]100[/gold]" for i in capture))
        self.assertFalse(any(i.gold_glow for i in capture))


if __name__ == "__main__":
    unittest.main()
