"""Real PNG/JPEG/SVG static asset and bounded-rejection contracts.

SVG raster cases deliberately require the optional resvg_py binary wheel;
missing dependencies are not silently counted as visual compatibility passes.
"""

import builtins
import dataclasses
import io
import struct
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from PIL import Image, PngImagePlugin
from ygl_test_subject.presentation import layout_assets as assets
from ygl_test_subject.presentation.card_bitmap import BitmapUnavailable

METEOR = (
    Path(__file__).resolve().parents[2]
    / "examples/command_cards/assets/ff14-meteor.svg"
)
SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 10"><rect width="20" height="10" fill="currentColor"/></svg>'


def image_bytes(kind="PNG", size=(40, 20), color=(20, 40, 60, 128)):
    with Image.new("RGBA" if kind == "PNG" else "RGB", size, color) as image:
        with io.BytesIO() as stream:
            image.save(stream, format=kind)
            return stream.getvalue()


def chunk(tag, data):
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data))
    )


class LayoutAssetsTests(unittest.TestCase):
    def assert_reason(self, reason, call):
        with self.assertRaises(BitmapUnavailable) as raised:
            call()
        self.assertEqual(raised.exception.reason, reason)
        self.assertEqual(str(raised.exception), reason)
        self.assertIsNone(raised.exception.__cause__)

    def render(self, asset, width=80, height=80, color="#123456", **kwargs):
        return assets.rasterize_asset(asset, width, height, color, **kwargs)

    def test_png_and_jpeg_are_real_rgba_and_preserve_aspect_ratio(self):
        for kind in ("png", "jpeg"):
            color = (20, 40, 60, 128) if kind == "png" else (20, 40, 60)
            asset = assets.Asset(kind, image_bytes(kind.upper(), color=color))
            with self.render(asset) as result:
                self.assertEqual(result.size, (80, 80))
                self.assertEqual(result.mode, "RGBA")
                self.assertEqual(result.info, {})
                self.assertEqual(result.getbbox(), (0, 20, 80, 60))
                self.assertEqual(result.getpixel((0, 0)), (0, 0, 0, 0))
                self.assertEqual(
                    result.getpixel((40, 40))[3], 128 if kind == "png" else 255
                )

    def test_cover_crops_and_raster_color_is_not_tinted(self):
        asset = assets.Asset("png", image_bytes(color=(20, 40, 60, 255)))
        with self.render(asset, fit="cover", color="#ffffff") as first:
            with self.render(asset, fit="cover", color="#000000") as second:
                self.assertEqual(first.getbbox(), (0, 0, 80, 80))
                self.assertEqual(first.tobytes(), second.tobytes())
                self.assertEqual(first.getpixel((40, 40)), (20, 40, 60, 255))

    def test_assets_are_immutable_and_outputs_are_independently_owned(self):
        asset = assets.Asset("png", image_bytes())
        with self.assertRaises(dataclasses.FrozenInstanceError):
            asset.kind = "svg"
        first, second = self.render(asset), self.render(asset)
        first.close()
        try:
            self.assertEqual(second.size, (80, 80))
            second.getpixel((40, 40))
        finally:
            second.close()

    def test_raster_kind_signature_and_truncation_are_checked(self):
        png, jpeg = image_bytes(), image_bytes("JPEG", color=(1, 2, 3))
        for asset in (
            assets.Asset("png", jpeg),
            assets.Asset("jpeg", png),
            assets.Asset("png", png[:-10]),
            assets.Asset("jpeg", jpeg[:30]),
            assets.Asset("png", "/tmp/private.png"),
            assets.Asset("jpeg", b"GIF89a"),
            assets.Asset("png", png + b"trailing"),
        ):
            with self.subTest(asset=asset.kind):
                self.assert_reason("invalid_input", lambda: self.render(asset))

    def test_all_apng_frames_are_rejected(self):
        with (
            Image.new("RGBA", (4, 4), "red") as first,
            Image.new("RGBA", (4, 4), "blue") as second,
        ):
            with io.BytesIO() as stream:
                first.save(
                    stream,
                    format="PNG",
                    save_all=True,
                    append_images=[second],
                    duration=100,
                    loop=0,
                )
                data = stream.getvalue()
        self.assert_reason(
            "invalid_input", lambda: self.render(assets.Asset("png", data))
        )

    def test_png_text_and_profiles_are_discarded_without_decompression(self):
        png = image_bytes()
        metadata = chunk(b"zTXt", b"Comment\0\0" + zlib.compress(b"a" * 2_000_000))
        data = png[:33] + metadata + png[33:]
        with patch.object(
            PngImagePlugin,
            "_safe_zlib_decompress",
            side_effect=AssertionError("metadata decoded"),
        ):
            with self.render(assets.Asset("png", data)) as result:
                self.assertEqual(result.info, {})
                self.assertEqual(result.size, (80, 80))
        damaged = bytearray(data)
        damaged[45] ^= 1
        self.assert_reason(
            "invalid_input", lambda: self.render(assets.Asset("png", bytes(damaged)))
        )

    def test_source_dimensions_and_pixel_budget_fail_before_decoder(self):
        for size in ((4097, 1), (3000, 3000)):
            header = struct.pack(">IIBBBBB", *size, 8, 6, 0, 0, 0)
            data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IEND", b"")
            with patch.object(
                Image, "open", side_effect=AssertionError("decoder entered")
            ):
                self.assert_reason(
                    "layout_overflow", lambda: self.render(assets.Asset("png", data))
                )

    def test_byte_and_output_limits_precede_decode(self):
        with patch.object(Image, "open", side_effect=AssertionError("decoder entered")):
            self.assert_reason(
                "byte_limit",
                lambda: self.render(
                    assets.Asset("png", b"x" * (assets.MAX_ASSET_BYTES + 1))
                ),
            )
            self.assert_reason(
                "layout_overflow",
                lambda: self.render(assets.Asset("png", b""), width=2049),
            )
            self.assert_reason(
                "layout_overflow",
                lambda: self.render(assets.Asset("png", b""), width=2048, height=2048),
            )
            self.assert_reason(
                "invalid_input",
                lambda: self.render(assets.Asset("png", b""), width=True),
            )

    def test_exact_svg_current_color_on_light_and_dark_themes(self):
        for color, pixel in (
            ("#123456", (18, 52, 86, 255)),
            ("#f8fafc", (248, 250, 252, 255)),
        ):
            with patch(
                "socket.create_connection",
                side_effect=AssertionError("network requested"),
            ):
                with self.render(assets.Asset("svg", SVG), color=color) as result:
                    self.assertEqual(result.size, (80, 80))
                    self.assertEqual(result.getbbox(), (0, 20, 80, 60))
                    self.assertEqual(result.getpixel((40, 40)), pixel)
        with self.render(assets.Asset("svg", SVG), fit="cover") as result:
            self.assertEqual(result.getbbox(), (0, 0, 80, 80))

    def test_svg_exact_odd_dimensions_and_only_in_memory_renderer_arguments(self):
        import resvg_py

        actual = resvg_py.svg_to_bytes
        for dimensions in ((123, 87), (87, 123), (121, 53)):
            for fit in ("contain", "cover"):
                with patch.object(resvg_py, "svg_to_bytes", wraps=actual) as render:
                    with self.render(
                        assets.Asset("svg", SVG), *dimensions, fit=fit
                    ) as image:
                        self.assertEqual(image.size, dimensions)
                kwargs = render.call_args.kwargs
                self.assertEqual(
                    set(kwargs), {"svg_string", "width", "height", "skip_system_fonts"}
                )
                self.assertIs(kwargs["skip_system_fonts"], True)
                self.assertIn(
                    'xmlns="http://www.w3.org/2000/svg"', kwargs["svg_string"]
                )
                self.assertIn(f'width="{dimensions[0]}"', kwargs["svg_string"])
                self.assertIn(f'height="{dimensions[1]}"', kwargs["svg_string"])

    def test_svg_renderer_dimension_mismatch_is_not_silently_resized(self):
        import resvg_py

        with patch.object(
            resvg_py, "svg_to_bytes", return_value=image_bytes(size=(3, 2))
        ):
            self.assert_reason(
                "invalid_input", lambda: self.render(assets.Asset("svg", SVG))
            )

    def test_existing_meteor_is_supported_in_both_themes_and_hole_remains_open(self):
        raw = METEOR.read_bytes()
        alpha = []
        for color in ("#293645", "#eef1f7"):
            with self.render(
                assets.Asset("svg", raw, alt="FFXIV meteor"), 200, 200, color
            ) as result:
                with result.getchannel("A") as mask:
                    alpha.append(mask.tobytes())
                    opaque = sum(value == 255 for value in mask.get_flattened_data())
                    self.assertGreater(opaque, 4000)
                self.assertEqual(result.getpixel((100, 100))[3], 0)
                expected = tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))
                self.assertIn(expected + (255,), set(result.get_flattened_data()))
        self.assertEqual(alpha[0], alpha[1])

    def test_svg_geometry_paths_arcs_groups_transforms_and_opacity(self):
        svg = """<svg viewBox="0 0 100 100"><g transform="translate(5 5) scale(.9)" stroke="currentColor" fill="#f00" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="1" y="1" width="20" height="20" rx="3"/><circle cx="40" cy="15" r="10"/><ellipse cx="70" cy="15" rx="15" ry="8"/><line x1="0" y1="40" x2="90" y2="40"/><polyline points="0,60 10,50 20,60" fill="none"/><polygon points="30,60 40,50 50,60"/><path fill-rule="evenodd" fill-opacity=".5" d="M 60 60 C 60 50 70 50 70 60 S 80 70 80 60 Q 90 50 90 60 T 85 70 A 5 5 0 0 1 80 75 L 60 75 Z"/></g></svg>"""
        with self.render(assets.Asset("svg", svg), 100, 100) as result:
            self.assertIsNotNone(result.getbbox())
            self.assertGreater(len(set(result.get_flattened_data())), 20)

    def test_svg_references_and_unsupported_content_are_rejected_before_renderer(self):
        snippets = (
            "<script/>",
            "<foreignObject/>",
            "<text>word</text>",
            '<use href="#shape"/>',
            '<image href="https://example.org/image.png"/>',
            "<animate/>",
            "<filter/>",
            "<style>path {fill:red}</style>",
            '<path onclick="run()" d="M0 0Z"/>',
            '<path style="fill:red" d="M0 0Z"/>',
            '<path fill="url(#a)" d="M0 0Z"/>',
            '<g xml:base="file:///tmp/"><path d="M0 0Z"/></g>',
            '<path href="file:///tmp/private" d="M0 0Z"/>',
            '<g xmlns="https://example.org/"><path d="M0 0Z"/></g>',
            '<svg viewBox="0 0 10 10"/>',
            '<path d="M0 0Z" unknown="x"/>',
        )
        original = builtins.__import__

        def no_renderer(name, *args, **kwargs):
            if name == "resvg_py":
                raise AssertionError("invalid source reached renderer")
            return original(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=no_renderer):
            for snippet in snippets:
                with self.subTest(snippet=snippet):
                    svg = '<svg viewBox="0 0 10 10">' + snippet + "</svg>"
                    self.assert_reason(
                        "invalid_input", lambda: self.render(assets.Asset("svg", svg))
                    )

    def test_svg_declarations_encoding_numeric_syntax_and_bad_roots(self):
        cases = (
            '<!DOCTYPE svg [<!ENTITY x "expanded">]><svg viewBox="0 0 10 10"/>',
            '<?xml-stylesheet href="https://example.org/x"?><svg viewBox="0 0 10 10"/>',
            '<svg viewBox="0 0 10 10"><![CDATA[x]]></svg>',
            '<svg viewBox="0 0 10 10"><path d="M0 0L"/></svg>',
            '<svg viewBox="0 0 10 10"><path d="M0 0 A1 1 0 2 1 3 3"/></svg>',
            '<svg viewBox="0 0 10 10"><rect width="100%"/></svg>',
            '<svg viewBox="0 0 0 10"/>',
            "<svg/>",
            "<g/>",
            '<svg viewBox="0 0 10 10"><g opacity="2"/></svg>',
            '<svg viewBox="0 0 10 10"><path d="MNaN 0Z"/></svg>',
            '<svg viewBox="0 0 10 10"><g transform="translate(1) garbage"/></svg>',
            b"\xff\xfe<\x00s\x00v\x00g\x00/\x00>\x00",
        )
        for svg in cases:
            with self.subTest(svg=svg):
                self.assert_reason(
                    "invalid_input", lambda: self.render(assets.Asset("svg", svg))
                )

    def test_svg_element_depth_byte_and_numeric_limits(self):
        cases = (
            ("byte_limit", "<svg>" + " " * assets.MAX_SVG_BYTES + "</svg>"),
            (
                "layout_overflow",
                '<svg viewBox="0 0 10 10">'
                + "<g/>" * assets.MAX_SVG_ELEMENTS
                + "</svg>",
            ),
            (
                "layout_overflow",
                '<svg viewBox="0 0 10 10">'
                + "<g>" * assets.MAX_SVG_DEPTH
                + "</g>" * assets.MAX_SVG_DEPTH
                + "</svg>",
            ),
            ("layout_overflow", '<svg viewBox="0 0 1e100 10"/>'),
            (
                "layout_overflow",
                '<svg viewBox="0 0 10 10"><g transform="scale(1000000)"><g transform="scale(1000000)"/></g></svg>',
            ),
        )
        for reason, svg in cases:
            with self.subTest(reason=reason):
                self.assert_reason(
                    reason, lambda: self.render(assets.Asset("svg", svg))
                )

    def test_missing_svg_or_native_dependency_has_explicit_stable_error(self):
        original = builtins.__import__
        for error in (
            ImportError("missing renderer"),
            OSError("missing native extension"),
        ):

            def missing(name, *args, **kwargs):
                if name == "resvg_py":
                    raise error
                return original(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=missing):
                self.assert_reason(
                    "dependency_missing", lambda: self.render(assets.Asset("svg", SVG))
                )

    def test_bad_metadata_fit_and_optional_flag_do_not_hide_failure(self):
        for asset in (
            assets.Asset("bad", b"x"),
            assets.Asset("png", b"x", required=False),
            assets.Asset("png", b"x", required=1),
            assets.Asset("svg", SVG, alt="bad\x00"),
        ):
            self.assert_reason("invalid_input", lambda: self.render(asset))
        self.assert_reason(
            "invalid_input",
            lambda: self.render(assets.Asset("svg", SVG), fit="stretch"),
        )
        self.assert_reason(
            "invalid_input",
            lambda: self.render(assets.Asset("svg", SVG), color="url(x)"),
        )


if __name__ == "__main__":
    unittest.main()
