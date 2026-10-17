"""Independent exact-cmap checks and finite rendering with the packaged face."""

import builtins
import hashlib
import io
import os
import socket
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from fontTools.ttLib import TTFont
from PIL import ImageFont
from ygl_test_subject.presentation import bundled_font as bundled
from ygl_test_subject.presentation._font_coverage import RANGES
from ygl_test_subject.presentation.card_bitmap import (
    BitmapUnavailable,
    CardSpec,
    Section,
    render_card,
)


class BundledFontTests(unittest.TestCase):
    def assert_reason(self, reason, call):
        with self.assertRaises(BitmapUnavailable) as raised:
            call()
        self.assertEqual(raised.exception.reason, reason)
        self.assertEqual(str(raised.exception), reason)
        self.assertIsNone(raised.exception.__cause__)

    def test_independent_exact_cmap_full_inventory_and_canonical_digest(self):
        provider = bundled.BundledFontProvider()
        with TTFont(io.BytesIO(provider._font_bytes), fontNumber=0) as face:
            points = sorted(
                cp
                for cp, name in face.getBestCmap().items()
                if name != ".notdef" and face.getGlyphID(name) != 0
            )
        generated = [cp for start, end in RANGES for cp in range(start, end + 1)]
        self.assertEqual(generated, points)
        self.assertEqual((len(points), len(RANGES)), (44810, 2032))
        canonical = "".join(f"U+{cp:04X}\n" for cp in points).encode("ascii")
        self.assertEqual(
            hashlib.sha256(canonical).hexdigest(),
            "11e617d04fdf15ac1a700a30ef81b60e38d7e6116c0d1338e1811225f3df89a3",
        )
        # Compare the complete Unicode domain, not just positive examples.
        expected = set(points)
        self.assertEqual(
            [cp for cp in range(0x110000) if provider.supports(cp)], points
        )
        for value in (True, False, -1, 0x110000, 1.5, "A", None, 0xD800, 0xDFFF):
            self.assertFalse(provider.supports(value))
        self.assertNotIn(0x1F600, expected)

    def test_verified_bytes_are_same_face_source_without_path_reopening(self):
        provider = bundled.BundledFontProvider()
        original = ImageFont.truetype
        calls = []

        def capture(source, size, **kwargs):
            self.assertIsInstance(source, io.BytesIO)
            self.assertEqual(source.getvalue(), provider._font_bytes)
            self.assertEqual(kwargs, {"index": 0})
            calls.append(size)
            return original(source, size, **kwargs)

        with (
            patch.object(Path, "open", side_effect=AssertionError("reopened")),
            patch.object(ImageFont, "truetype", side_effect=capture),
        ):
            result = render_card(
                CardSpec(
                    "游戏连结 · 字体验收",
                    (
                        Section(
                            "犎牛牛排",
                            (
                                "0123456789，。！？：；（）【】《》—…",
                                "角色长名辰星之誓者" * 9,
                            ),
                        ),
                    ),
                    width=740,
                ),
                font_provider=provider,
            )
        self.assertEqual(calls, [28, 22, 20, 16])
        self.assertGreater(result.height, 250)
        self.assertEqual(set(vars(provider)), {"_font_bytes"})

    def test_unsupported_glyph_refuses_render_before_loading(self):
        provider = bundled.BundledFontProvider()
        with patch.object(provider, "load", side_effect=AssertionError("loaded")):
            self.assert_reason(
                "glyph_unavailable",
                lambda: render_card(CardSpec("😀"), font_provider=provider),
            )

    def test_missing_short_corrupt_and_oversized_assets_are_safe_and_bounded(self):
        actual = bundled.BundledFontProvider()._font_bytes
        with patch.object(Path, "open", side_effect=FileNotFoundError("secret/path")):
            self.assert_reason("font_unavailable", bundled.BundledFontProvider)
        for content in (b"", actual[:-1], b"x" + actual[1:], actual + b"x" * 100):
            stream = io.BytesIO(content)
            with patch.object(Path, "open", return_value=stream):
                self.assert_reason("font_unavailable", bundled.BundledFontProvider)

        class BoundedStream(io.BytesIO):
            def read(self, size=-1):
                if size != bundled.FONT_SIZE + 1:
                    raise AssertionError("unbounded font read")
                return super().read(size)

        with patch.object(Path, "open", return_value=BoundedStream(actual)):
            bundled.BundledFontProvider()

    def test_loader_size_dependency_and_exception_mapping(self):
        provider = bundled.BundledFontProvider()
        for size in (True, 0, 15, 17, 10000, "28", None):
            self.assert_reason("font_unavailable", lambda: provider.load(size))
        with patch.object(
            ImageFont, "truetype", side_effect=RuntimeError("secret/path")
        ):
            self.assert_reason("font_unavailable", lambda: provider.load(28))
        original = builtins.__import__

        def reject(name, *args, **kwargs):
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError("secret/path")
            return original(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=reject):
            self.assert_reason("dependency_missing", lambda: provider.load(28))
        with patch("builtins.__import__", side_effect=OSError("secret/path")):
            self.assert_reason("font_unavailable", lambda: provider.load(28))

    def test_import_is_lazy_without_io_dependencies_scanning_or_network(self):
        source = compile(Path(bundled.__file__).read_bytes(), bundled.__file__, "exec")
        module = types.ModuleType("ygl_test_subject.presentation._bundled_import_probe")
        module.__package__ = "ygl_test_subject.presentation"
        original = builtins.__import__

        def reject(name, *args, **kwargs):
            if name.split(".")[0] in {"PIL", "fontTools"}:
                raise AssertionError("eager optional import")
            return original(name, *args, **kwargs)

        def forbidden(*args, **kwargs):
            raise AssertionError("import side effect")

        with (
            patch.dict(sys.modules, {module.__name__: module}),
            patch("builtins.__import__", side_effect=reject),
            patch("builtins.open", side_effect=forbidden),
            patch.object(Path, "open", side_effect=forbidden),
            patch.object(os, "scandir", side_effect=forbidden),
            patch.object(os, "listdir", side_effect=forbidden),
            patch.object(socket, "socket", side_effect=forbidden),
        ):
            exec(source, module.__dict__)
        self.assertTrue(callable(module.BundledFontProvider))


if __name__ == "__main__":
    unittest.main()
