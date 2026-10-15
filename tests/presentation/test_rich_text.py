"""Real fixed-font raster, resource, and internal rich-card contracts."""

import builtins
import dataclasses
import io
import sys
import types
import unittest
import weakref
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageChops, ImageDraw, ImageFont
from ygl_test_subject.presentation import card_bitmap as bitmap
from ygl_test_subject.presentation import rich_text as rich
from ygl_test_subject.presentation.bundled_font import BundledFontProvider


class RichTextTests(unittest.TestCase):
    def setUp(self):
        self.provider = BundledFontProvider()
        self.font = self.provider.load(20)
        with io.BytesIO(self.font.font_bytes) as stream:
            self.font = self.font.font_variant(font=stream, size=40)
        self.palette = rich.TextPalette("#4c5a69", "#738291", "#293645", "#7e94ab")

    def assert_reason(self, reason, function):
        with self.assertRaises(bitmap.BitmapUnavailable) as raised:
            function()
        self.assertEqual(raised.exception.reason, reason)
        self.assertEqual(str(raised.exception), reason)

    def test_exact_colors_strict_threshold_and_no_inferred_gold(self):
        self.assertEqual(
            tuple(rich.GRADE_COLORS.values()),
            (
                "#666666",
                "#1eff00",
                "#0070ff",
                "#a335ee",
                "#ff8000",
                "#e268a8",
                "#e5cc80",
            ),
        )
        self.assertTrue(rich.needs_shadow(1.499999))
        self.assertFalse(rich.needs_shadow(1.5))
        self.assertEqual(rich.shadow_color("#1eff00", "#edf1f5"), "#188605")
        with Image.new("RGB", (1200, 480), "#edf1f5") as image:
            session = rich.RichSession(image)
            info = session.add(
                rich.TextSpan("100.0"),
                font=self.font,
                size=20,
                palette=self.palette,
                right=500,
                center=120,
            )
            self.assertFalse(info.gold_glow)
            self.assertEqual(info.face, self.palette.text)

    def test_all_roles_actual_background_and_real_ink_alignment(self):
        for role in rich.TextRole:
            grade = rich.Grade.GREEN if role is rich.TextRole.GRADED else None
            span = rich.TextSpan(
                "—" if role is rich.TextRole.NORMAL else "ERR", role, grade
            )
            face = rich.face_color(span, self.palette)
            with Image.new("RGB", (1200, 480), face) as image:
                session = rich.RichSession(image)
                info = session.add(
                    span,
                    font=self.font,
                    size=20,
                    palette=self.palette,
                    right=500,
                    center=120,
                )
                self.assertTrue(info.shadow)
                self.assertEqual(info.contrast, 1)
                self.assertEqual(info.bounds[2], 500)
                self.assertLessEqual(
                    abs((info.bounds[1] + info.bounds[3]) / 2 - 120), 0.25
                )
                session.paint()

    def test_whole_card_effects_precede_every_crisp_face(self):
        for background in ("#f8fafc", "#1c2337"):
            with Image.new("RGB", (1200, 480), background) as image:
                session = rich.RichSession(image)
                infos = session.add_line(
                    (
                        rich.TextSpan("A", rich.TextRole.GRADED, rich.Grade.BLUE),
                        rich.TextSpan("100.0%", rich.TextRole.GRADED, rich.Grade.GOLD),
                    ),
                    xy=(100, 120),
                    font=self.font,
                    size=20,
                    palette=self.palette,
                )
                self.assertEqual(image.getpixel((200, 200)), rich.rgb(background))
                session.paint()
                self.assertTrue(infos[1].gold_glow)
                plan = session.plans[0]
                with rich.ExitStack() as stack:
                    mask = session._mask(plan, stack)
                    interior = stack.enter_context(
                        mask.point(lambda v: 255 if v == 255 else 0)
                    )
                    crop = stack.enter_context(image.crop(plan.info.patch_bounds))
                    solid = stack.enter_context(Image.new("RGB", crop.size, "#0070ff"))
                    delta = stack.enter_context(
                        ImageChops.difference(crop, solid).convert("L")
                    )
                    self.assertIsNone(ImageChops.multiply(delta, interior).getbbox())

    def test_shared_cumulative_pixels_measurements_and_work_caps(self):
        with Image.new("RGB", (1200, 480), "#edf1f5") as image:
            session = rich.RichSession(image, max_mask_pixels=100)
            self.assert_reason(
                "layout_overflow",
                lambda: session.add(
                    rich.TextSpan("A"),
                    font=self.font,
                    size=20,
                    palette=self.palette,
                    left=100,
                    top=100,
                ),
            )
            self.assertGreater(session.mask_pixels, 100)
            self.assert_reason(
                "layout_overflow", lambda: rich.RichSession(image, max_work_bytes=100)
            )
            session = rich.RichSession(image, max_measurements=1)
            self.assert_reason(
                "layout_overflow",
                lambda: session.add_line(
                    (rich.TextSpan("A"),),
                    xy=(100, 120),
                    font=self.font,
                    size=20,
                    palette=self.palette,
                ),
            )
            self.assertFalse(session.plans)
            for name, value in (
                ("max_work_bytes", rich.MAX_WORK_BYTES + 1),
                ("max_mask_pixels", rich.MAX_MASK_PIXELS + 1),
            ):
                self.assert_reason(
                    "invalid_input", lambda: rich.RichSession(image, **{name: value})
                )

    def test_patch_and_line_limits_fail_without_painting(self):
        with Image.new("RGB", (1200, 480), "#edf1f5") as image:
            before = image.tobytes()
            session = rich.RichSession(image)
            self.assert_reason(
                "layout_overflow",
                lambda: session.add_line(
                    (rich.TextSpan("A"), rich.TextSpan("B")),
                    xy=(100, 120),
                    font=self.font,
                    size=20,
                    palette=self.palette,
                    max_line_mask_pixels=1,
                ),
            )
            self.assertEqual(image.tobytes(), before)
            self.assertFalse(session.plans)
            with patch.object(
                Image, "new", side_effect=AssertionError("mask allocated")
            ):
                self.assert_reason(
                    "layout_overflow",
                    lambda: session.add(
                        rich.TextSpan("W" * 512),
                        font=self.font,
                        size=96,
                        palette=self.palette,
                        left=100,
                        top=100,
                    ),
                )

    def test_lowered_line_cap_admits_geometry_before_any_raster_work(self):
        with Image.new("RGB", (1200, 480), "#edf1f5") as image:
            session = rich.RichSession(image)
            before = image.tobytes()
            with (
                patch.object(Image, "new", side_effect=AssertionError("mask")) as new,
                patch.object(
                    Image.Image, "crop", side_effect=AssertionError("crop")
                ) as crop,
                patch.object(
                    Image.Image, "copy", side_effect=AssertionError("copy")
                ) as copy,
                patch.object(
                    Image.Image, "getbbox", side_effect=AssertionError("scan")
                ) as scan,
            ):
                self.assert_reason(
                    "layout_overflow",
                    lambda: session.add_line(
                        (rich.TextSpan("A"),),
                        xy=(100, 120),
                        font=self.font,
                        size=20,
                        palette=self.palette,
                        max_line_mask_pixels=1,
                    ),
                )
            for operation in (new, crop, copy, scan):
                operation.assert_not_called()
            self.assertGreater(session.measurements, 0)
            self.assertEqual(session.mask_pixels, 0)
            self.assertFalse(session.plans)
            self.assertEqual(image.tobytes(), before)

    def _natural_character_session(self, image, accepted_calls):
        session = rich.RichSession(image)
        for _ in range(accepted_calls):
            session.add(
                rich.TextSpan(" " * 32),
                font=self.font,
                size=20,
                palette=self.palette,
                left=100,
                top=100,
            )
        return session

    def _assert_line_rejected_without_raster(self, session, spans, **placement):
        before = (
            session.input_chars,
            session.mask_pixels,
            len(session.plans),
            session.measurements,
            session.image.tobytes(),
        )
        with (
            patch.object(Image, "new", side_effect=AssertionError("mask")) as new,
            patch.object(
                Image.Image, "crop", side_effect=AssertionError("crop")
            ) as crop,
            patch.object(
                Image.Image, "copy", side_effect=AssertionError("copy")
            ) as copy,
            patch.object(
                Image.Image, "getbbox", side_effect=AssertionError("scan")
            ) as scan,
        ):
            self.assert_reason(
                "layout_overflow",
                lambda: session.add_line(
                    spans,
                    xy=(100, 120),
                    font=self.font,
                    size=20,
                    palette=self.palette,
                    **placement,
                ),
            )
        for operation in (new, crop, copy, scan):
            operation.assert_not_called()
        self.assertEqual(
            (
                session.input_chars,
                session.mask_pixels,
                len(session.plans),
                session.measurements,
                session.image.tobytes(),
            ),
            before,
        )

    def test_natural_full_character_session_rejects_all_line_rasters(self):
        with rich.ExitStack() as stack:
            image = stack.enter_context(Image.new("RGB", (1200, 480), "#edf1f5"))
            session = self._natural_character_session(image, 750)
            try:
                self.assertEqual(session.input_chars, 24_000)
                for spans in (
                    (rich.TextSpan("A"),),
                    (rich.TextSpan("99"), rich.TextSpan("%", rich.TextRole.EMPHASIS)),
                ):
                    for placement in (
                        {},
                        {"right": 500},
                        {"center": 120},
                        {"right": 500, "center": 120},
                    ):
                        with self.subTest(spans=len(spans), placement=placement):
                            self._assert_line_rejected_without_raster(
                                session, spans, **placement
                            )
            finally:
                session.close()

    def test_natural_near_character_limit_admits_whole_line_once(self):
        with rich.ExitStack() as stack:
            image = stack.enter_context(Image.new("RGB", (1200, 480), "#edf1f5"))
            session = self._natural_character_session(image, 749)
            try:
                self.assertEqual(session.input_chars, 23_968)
                too_many = (
                    rich.TextSpan("A" * 16),
                    rich.TextSpan("B" * 17, rich.TextRole.EMPHASIS),
                )
                for placement in ({}, {"right": 500, "center": 120}):
                    self._assert_line_rejected_without_raster(
                        session, too_many, **placement
                    )
                before_pixels = image.tobytes()
                before_work, before_plans = session.mask_pixels, len(session.plans)
                shown = (
                    rich.TextSpan("A" * 16),
                    rich.TextSpan("B" * 16, rich.TextRole.EMPHASIS),
                )
                infos = session.add_line(
                    shown,
                    xy=(100, 120),
                    font=self.font,
                    size=20,
                    palette=self.palette,
                    right=500,
                    center=120,
                )
                self.assertEqual(
                    [info.text for info in infos], [span.text for span in shown]
                )
                self.assertEqual(session.input_chars, 24_000)
                self.assertEqual(len(session.plans), before_plans + 2)
                self.assertGreater(session.mask_pixels, before_work)
                self._assert_line_rejected_without_raster(
                    session, (rich.TextSpan("C"),), right=500, center=120
                )
                self.assertEqual(image.tobytes(), before_pixels)
                session.paint()
                self.assertNotEqual(image.tobytes(), before_pixels)
            finally:
                session.close()

    def test_cumulative_crop_copy_and_scan_are_admitted_before_work(self):
        with Image.new("RGB", (1200, 480), "#edf1f5") as image:
            probe = rich.RichSession(image)
            geometry = probe._geometry(rich.TextSpan("A"), self.font, 20)
            pixels = geometry.dimensions[0] * geometry.dimensions[1]
            before = image.tobytes()
            for multiplier, operation in ((3, "crop"), (4, "copy"), (5, "getbbox")):
                session = rich.RichSession(
                    image, max_mask_pixels=multiplier * pixels - 1
                )
                original = getattr(Image.Image, operation)
                observed = []

                def capture(surface, *args, **kwargs):
                    observed.append(surface.mode)
                    return original(surface, *args, **kwargs)

                with patch.object(Image.Image, operation, capture):
                    self.assert_reason(
                        "layout_overflow",
                        lambda: session.add(
                            rich.TextSpan("A"),
                            font=self.font,
                            size=20,
                            palette=self.palette,
                            left=100,
                            top=100,
                        ),
                    )
                # The first glyph getbbox is allowed; only the final extent
                # scan must be rejected. Crop/copy have no earlier counterpart.
                self.assertEqual(len(observed), 1 if operation == "getbbox" else 0)
                self.assertFalse(session.plans)
                self.assertEqual(image.tobytes(), before)
            exact = rich.RichSession(image, max_mask_pixels=5 * pixels)
            exact.add(
                rich.TextSpan("A"),
                font=self.font,
                size=20,
                palette=self.palette,
                left=100,
                top=100,
            )
            self.assertEqual(exact.mask_pixels, 5 * pixels)
            with patch.object(
                Image, "new", side_effect=AssertionError("late mask")
            ) as new:
                self.assert_reason("layout_overflow", exact.paint)
            new.assert_not_called()
            self.assertEqual(image.tobytes(), before)

    def test_new_card_complete_long_regular_text_and_alignment(self):
        words = "细Regular长名称" * 60
        spec = bitmap.CardSpec(
            rich.RichText((rich.TextSpan("游戏连结 · FF14 · Logs"),)),
            (
                bitmap.Section(
                    paragraphs=(words,),
                    columns=("名称", "数值"),
                    rows=(
                        bitmap.Row(
                            (
                                "未缺失",
                                rich.TextCell(
                                    rich.RichText(
                                        (
                                            rich.TextSpan(
                                                "0.0%",
                                                rich.TextRole.GRADED,
                                                rich.Grade.GRAY,
                                            ),
                                        )
                                    ),
                                    "right",
                                    "center",
                                ),
                            )
                        ),
                    ),
                ),
            ),
            width=740,
        )
        for palette in ("warm_paper", "cool_white", "deep_indigo"):
            result = bitmap.render_card(
                dataclasses.replace(spec, palette=palette), font_provider=self.provider
            )
            self.assertEqual(result.width, 740)
            self.assertGreater(result.height, 600)
            with Image.open(io.BytesIO(result.content)) as image:
                self.assertEqual(image.mode, "RGB")
                self.assertEqual(image.info, {})
        for field, limit in (
            ("max_chars", 2),
            ("max_measurements", 10),
            ("max_rich_work_bytes", 100),
            ("max_rich_mask_pixels", 100),
        ):
            self.assert_reason(
                "layout_overflow",
                lambda: bitmap.render_card(
                    spec,
                    font_provider=self.provider,
                    bounds=dataclasses.replace(bitmap.BitmapBounds(), **{field: limit}),
                ),
            )

    def test_font_variants_are_finite_exact_bytes_and_streams_closed(self):
        original = ImageFont.FreeTypeFont.font_variant
        seen = []
        borrowed = []
        calls = []

        def capture(face, **kwargs):
            self.assertIsInstance(kwargs["font"], io.BytesIO)
            self.assertEqual(kwargs["font"].getvalue(), self.provider._font_bytes)
            borrowed.append(face.path)
            seen.append(kwargs["font"])
            calls.append(kwargs["size"])
            return original(face, **kwargs)

        with patch.object(ImageFont.FreeTypeFont, "font_variant", capture):
            layout = bitmap._RichLayout(self.provider, bitmap.BitmapBounds())
        self.assertEqual(calls, [56, 44, 40, 32])
        self.assertTrue(all(stream.closed for stream in seen))
        self.assertTrue(all(not stream.closed for stream in borrowed))
        self.assertEqual(len(layout.fonts), 4)
        self.assertEqual(set(vars(self.provider)), {"_font_bytes"})
        self.assertTrue(
            all(
                face.font_bytes == self.provider._font_bytes
                for face in layout.fonts.values()
            )
        )

    def test_centered_cell_uses_actual_dash_and_numeric_ink(self):
        for shown in ("—", "0.0%", "ERR"):
            spec = bitmap.CardSpec(
                "结果",
                (
                    bitmap.Section(
                        columns=("名称", "百分位"),
                        rows=(
                            bitmap.Row(
                                (
                                    "会完整换行的长副本名称" * 8,
                                    rich.TextCell(
                                        rich.RichText((rich.TextSpan(shown),)),
                                        "right",
                                        "center",
                                    ),
                                )
                            ),
                        ),
                    ),
                ),
                width=420,
            )
            layout = bitmap._RichLayout(self.provider, bitmap.BitmapBounds())
            layout.build(spec)
            expected = (layout.panels[-1].top + layout.panels[-1].bottom) / 2
            seen = []
            original = rich.RichSession.add

            def capture(session, span, **kwargs):
                info = original(session, span, **kwargs)
                if span.text == shown:
                    seen.append(info)
                return info

            with patch.object(rich.RichSession, "add", capture):
                bitmap.render_card(spec, font_provider=self.provider)
            self.assertEqual(len(seen), 1)
            self.assertLessEqual(
                abs((seen[0].bounds[1] + seen[0].bounds[3]) / 2 - expected), 0.25
            )

    def test_multistyle_right_cell_positions_actual_whole_line_ink(self):
        cell = rich.TextCell(
            rich.RichText(
                (rich.TextSpan("99"), rich.TextSpan("%", rich.TextRole.EMPHASIS))
            ),
            "right",
            "center",
        )
        spec = bitmap.CardSpec(
            "结果",
            (bitmap.Section(columns=("列",), rows=(bitmap.Row((cell,)),)),),
            width=420,
        )
        captured = []
        original = rich.RichSession.paint

        def capture(session):
            captured.extend(
                plan for plan in session.plans if plan.info.text in ("99", "%")
            )
            return original(session)

        with patch.object(rich.RichSession, "paint", capture):
            bitmap.render_card(spec, font_provider=self.provider)
        self.assertEqual(len(captured), 2)
        actual = []
        for plan in captured:
            x0, y0, x1, y1 = plan.info.patch_bounds
            with rich.ExitStack() as stack:
                mask = stack.enter_context(Image.new("L", (x1 - x0, y1 - y0)))
                ImageDraw.Draw(mask).text(
                    (plan.pad - plan.box[0], plan.pad - plan.box[1]),
                    plan.info.text,
                    font=plan.font,
                    fill=255,
                )
                ink = mask.getbbox()
            actual.append((x0 + ink[0], y0 + ink[1], x0 + ink[2], y0 + ink[3]))
        self.assertEqual(max(box[2] for box in actual) / 2, 372)
        layout = bitmap._RichLayout(self.provider, bitmap.BitmapBounds())
        layout.build(spec)
        expected_center = (layout.panels[-1].top + layout.panels[-1].bottom) / 2
        self.assertLessEqual(
            abs(
                (min(box[1] for box in actual) + max(box[3] for box in actual)) / 4
                - expected_center
            ),
            0.25,
        )

    def test_all_blank_right_center_cells_preserve_layout_without_visible_ink(self):
        for content in (
            " ",
            "\u3000",
            rich.RichText(
                (rich.TextSpan(" "), rich.TextSpan("  ", rich.TextRole.EMPHASIS))
            ),
        ):
            spec = bitmap.CardSpec(
                "结果",
                (
                    bitmap.Section(
                        columns=("列",),
                        rows=(
                            bitmap.Row((rich.TextCell(content, "right", "center"),)),
                        ),
                    ),
                ),
                width=420,
            )
            seen = []
            original = rich.RichSession.paint

            def capture(session):
                seen.extend(
                    plan.info for plan in session.plans if plan.info.text.isspace()
                )
                return original(session)

            with patch.object(rich.RichSession, "paint", capture):
                result = bitmap.render_card(spec, font_provider=self.provider)
            self.assertGreater(result.height, 100)
            self.assertTrue(seen)
            self.assertLessEqual(abs(max(info.bounds[2] for info in seen) - 372), 0.25)
            for info in seen:
                self.assertIsNone(info.mask_bounds)
                self.assertIsNone(info.effect_bounds)
                self.assertFalse(info.shadow)
                self.assertFalse(info.gold_glow)
                self.assertEqual(info.patch_bounds, (0, 0, 0, 0))

    def test_background_draw_reference_is_released_before_encoding(self):
        references = []
        original_draw, original_save = ImageDraw.Draw, Image.Image.save

        def capture_draw(image, *args, **kwargs):
            draw = original_draw(image, *args, **kwargs)
            if image.mode == "RGB":
                references.append(weakref.ref(draw))
            return draw

        def capture_save(image, *args, **kwargs):
            self.assertTrue(references)
            self.assertTrue(all(reference() is None for reference in references))
            return original_save(image, *args, **kwargs)

        with (
            patch.object(ImageDraw, "Draw", capture_draw),
            patch.object(Image.Image, "save", capture_save),
        ):
            bitmap.render_card(
                bitmap.CardSpec(rich.RichText((rich.TextSpan("结果"),)), width=160),
                font_provider=self.provider,
            )

    def test_whole_session_input_and_font_storage_limits(self):
        with Image.new("RGB", (1200, 480), "#edf1f5") as image:
            session = rich.RichSession(image)
            session.input_chars = 24_000
            self.assert_reason(
                "layout_overflow",
                lambda: session.add(
                    rich.TextSpan("A"),
                    font=self.font,
                    size=20,
                    palette=self.palette,
                    left=100,
                    top=100,
                ),
            )
        with (
            patch.object(bitmap, "_MAX_RICH_FONT_STORAGE", 1),
            patch.object(
                ImageFont.FreeTypeFont,
                "font_variant",
                side_effect=AssertionError("copied"),
            ),
        ):
            self.assert_reason(
                "layout_overflow",
                lambda: bitmap._RichLayout(self.provider, bitmap.BitmapBounds()),
            )
        self.assert_reason(
            "layout_overflow",
            lambda: bitmap._RichLayout(
                self.provider,
                dataclasses.replace(bitmap.BitmapBounds(), max_rich_font_bytes=1),
            ),
        )

    def test_font_path_or_oversized_bytes_rejected_before_variant(self):
        base = self.provider.load(20)
        for value in (b"x" * (bitmap._MAX_RICH_FACE_BYTES + 1), "not bytes"):
            base.font_bytes = value
            with (
                patch.object(self.provider, "load", return_value=base),
                patch.object(
                    ImageFont.FreeTypeFont,
                    "font_variant",
                    side_effect=AssertionError("copied"),
                ),
            ):
                self.assert_reason(
                    "font_unavailable",
                    lambda: bitmap._RichLayout(self.provider, bitmap.BitmapBounds()),
                )

    def test_cached_provider_faces_and_streams_remain_reusable(self):
        cache = {size: self.provider.load(size) for size in (28, 22, 20, 16)}
        initial = {size: (id(face), face.path.tell()) for size, face in cache.items()}
        provider = self.provider

        class CachedProvider:
            def supports(self, point):
                return provider.supports(point)

            def load(self, size):
                return cache[size]

        try:
            spec = bitmap.CardSpec(rich.RichText((rich.TextSpan("可重复字体"),)))
            first = bitmap.render_card(spec, font_provider=CachedProvider())
            second = bitmap.render_card(spec, font_provider=CachedProvider())
            self.assertEqual(first, second)
            for size, face in cache.items():
                self.assertEqual((id(face), face.path.tell()), initial[size])
                self.assertFalse(face.path.closed)
                self.assertGreater(face.getbbox("可重复字体")[2], 0)
        finally:
            # This test owns these cached source streams; the renderer does not.
            for face in cache.values():
                face.path.close()

    def test_cancelled_preparation_closes_masks(self):
        created = []
        original = Image.new

        def capture(*args, **kwargs):
            image = original(*args, **kwargs)
            # Only our full run masks belong to this allocation contract;
            # ImageDraw also builds its own one-pixel brush images.
            if args[0] == "L" and args[1][0] > 16:
                created.append(image)
            return image

        with original("RGB", (1200, 480), "#edf1f5") as image:
            session = rich.RichSession(image)
            with (
                patch.object(Image, "new", capture),
                patch.object(
                    ImageDraw.ImageDraw, "text", side_effect=KeyboardInterrupt
                ),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    session.add(
                        rich.TextSpan("A"),
                        font=self.font,
                        size=20,
                        palette=self.palette,
                        left=100,
                        top=100,
                    )
            self.assertFalse(session.plans)
            self.assertTrue(created)
            for mask in created:
                with self.assertRaises(ValueError):
                    mask.getpixel((0, 0))

    def test_cancelled_gold_paint_closes_every_owned_surface(self):
        with Image.new("RGB", (1200, 480), "#1c2337") as image:
            session = rich.RichSession(image)
            session.add(
                rich.TextSpan("100.0%", rich.TextRole.GRADED, rich.Grade.GOLD),
                font=self.font,
                size=20,
                palette=self.palette,
                right=500,
                center=120,
            )
            created = []
            original = Image.Image._new

            def capture(parent, core):
                surface = original(parent, core)
                if surface.width > 16:
                    created.append(surface)
                return surface

            with (
                patch.object(Image.Image, "_new", capture),
                patch.object(ImageChops, "screen", side_effect=KeyboardInterrupt),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    session.paint()
            self.assertTrue(created)
            for surface in created:
                with self.assertRaises(ValueError):
                    surface.getpixel((0, 0))

    def test_import_is_lazy_without_optional_dependencies_or_io(self):
        module = types.ModuleType("ygl_test_subject.presentation._rich_import_probe")
        module.__package__ = "ygl_test_subject.presentation"
        source = compile(Path(rich.__file__).read_bytes(), rich.__file__, "exec")
        original = builtins.__import__

        def reject(name, *args, **kwargs):
            if name == "PIL" or name.startswith("PIL."):
                raise AssertionError("eager Pillow")
            return original(name, *args, **kwargs)

        with (
            patch.dict(sys.modules, {module.__name__: module}),
            patch("builtins.__import__", reject),
            patch("builtins.open", side_effect=AssertionError("IO")),
        ):
            exec(source, module.__dict__)
        self.assertTrue(callable(module.RichSession))
