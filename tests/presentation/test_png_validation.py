from __future__ import annotations

import asyncio
import builtins
import io
import struct
import unittest
import warnings
import zlib
from dataclasses import replace
from hashlib import sha256
from unittest.mock import patch

from PIL import Image, ImageFile, PngImagePlugin
from ygl_test_subject.presentation.card_bitmap import RenderedBitmap
from ygl_test_subject.presentation.png_validation import (
    PngValidationError,
    PngValidationLimits,
    validate_png,
)


def png_bitmap(*, mode="RGB", size=(3, 2)):
    with (
        Image.new(
            mode, size, (12, 34, 56) if mode == "RGB" else (12, 34, 56, 78)
        ) as image,
        io.BytesIO() as stream,
    ):
        image.save(stream, format="PNG")
        return RenderedBitmap(stream.getvalue(), *size)


def chunk(kind, payload):
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def replace_idat(bitmap, payload):
    return replace(
        bitmap,
        content=bitmap.content[:33] + chunk(b"IDAT", payload) + chunk(b"IEND", b""),
    )


class PngValidationTests(unittest.TestCase):
    def assert_reason(self, bitmap, reason="image_invalid", **kwargs):
        with self.assertRaises(PngValidationError) as caught:
            validate_png(bitmap, **kwargs)
        self.assertEqual(str(caught.exception), reason)

    def test_actual_rgb_rgba_png_metadata_and_deterministic_digest(self):
        for mode in ("RGB", "RGBA"):
            with self.subTest(mode=mode):
                bitmap = png_bitmap(mode=mode)
                value = validate_png(bitmap)
                self.assertEqual(
                    (value.width, value.height, value.size), (3, 2, len(bitmap.content))
                )
                self.assertEqual(value.digest, sha256(bitmap.content).hexdigest())

    def test_exact_lowered_byte_dimension_pixel_boundaries(self):
        bitmap = png_bitmap()
        exact = PngValidationLimits(len(bitmap.content), 3, 2, 6)
        validate_png(bitmap, limits=exact)
        for name, lowered in (
            ("max_bytes", len(bitmap.content) - 1),
            ("max_width", 2),
            ("max_height", 1),
            ("max_pixels", 5),
        ):
            with self.subTest(name=name):
                self.assert_reason(
                    bitmap, "image_budget", limits=replace(exact, **{name: lowered})
                )

    def test_exact_type_and_declared_metadata_checks(self):
        bitmap = png_bitmap()
        for candidate in (
            None,
            replace(bitmap, content=bytearray(bitmap.content)),
            replace(bitmap, width=True),
            replace(bitmap, width=4),
            replace(bitmap, height=3),
            replace(bitmap, media_type="image/jpeg"),
        ):
            with self.subTest(kind=type(candidate).__name__):
                self.assert_reason(candidate)
        for name, value in (
            ("max_bytes", 1_048_577),
            ("max_pixels", 8_000_001),
            ("max_width", True),
            ("max_height", 0),
        ):
            self.assert_reason(
                bitmap, limits=replace(PngValidationLimits(), **{name: value})
            )

    def test_actual_ihdr_caps_precede_decoder_and_declaration_mismatch(self):
        bitmap = png_bitmap()
        for width, height in ((2049, 1), (1, 8193), (2048, 4096)):
            content = (
                bitmap.content[:8]
                + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                + bitmap.content[33:]
            )
            with patch.object(
                Image, "open", side_effect=AssertionError("must reject before decode")
            ):
                self.assert_reason(replace(bitmap, content=content), "image_budget")

    def test_signature_truncation_crc_duplicate_header_tail_and_non_png(self):
        bitmap = png_bitmap()
        for content in (
            b"not PNG",
            bitmap.content[:20],
            bitmap.content[:-1],
            bitmap.content + b"SENSITIVE",
            bitmap.content[:33] + bitmap.content[8:33] + bitmap.content[33:],
            bitmap.content[:-5] + b"xxxxx",
        ):
            self.assert_reason(replace(bitmap, content=content))
        with Image.new("RGB", (3, 2)) as image, io.BytesIO() as stream:
            image.save(stream, format="JPEG")
            self.assert_reason(replace(bitmap, content=stream.getvalue()))

    def test_ancillary_metadata_apng_and_unneeded_color_modes_rejected(self):
        bitmap = png_bitmap()
        for kind, payload in (
            (b"tEXt", b"author\x00SENSITIVE"),
            (b"zTXt", b"note\x00\x00" + zlib.compress(b"SENSITIVE" * 1000)),
            (b"acTL", struct.pack(">II", 2, 0)),
        ):
            self.assert_reason(
                replace(
                    bitmap,
                    content=bitmap.content[:33]
                    + chunk(kind, payload)
                    + bitmap.content[33:],
                )
            )
        with Image.new("P", (3, 2)) as image, io.BytesIO() as stream:
            image.save(stream, format="PNG")
            self.assert_reason(replace(bitmap, content=stream.getvalue()))
        with (
            Image.new("RGB", (3, 2)) as first,
            Image.new("RGB", (3, 2), "red") as second,
            io.BytesIO() as stream,
        ):
            first.save(
                stream,
                format="PNG",
                save_all=True,
                append_images=[second],
                duration=100,
            )
            self.assert_reason(replace(bitmap, content=stream.getvalue()))

    def test_full_load_rejects_crc_correct_invalid_compressed_pixels(self):
        self.assert_reason(replace_idat(png_bitmap(), b"not-zlib-SENSITIVE"))

    def test_host_nondefault_globals_never_allow_incomplete_zlib_or_bad_filters(self):
        bitmap = png_bitmap()
        raw = (b"\x00" + bytes((12, 34, 56)) * 3) * 2
        valid_deflate = zlib.compress(raw)
        candidates = (
            valid_deflate[:-1],
            valid_deflate[:-5],
            zlib.compress(raw[:-1]),
            zlib.compress(raw + b"extra"),
            valid_deflate + b"unused-tail",
            zlib.compress(b"\xff" + raw[1:]),
        )
        before = (
            Image.MAX_IMAGE_PIXELS,
            ImageFile.LOAD_TRUNCATED_IMAGES,
            list(warnings.filters),
        )
        with (
            patch.object(ImageFile, "LOAD_TRUNCATED_IMAGES", True),
            patch.object(Image, "MAX_IMAGE_PIXELS", None),
        ):
            validate_png(bitmap)
            for compressed in candidates:
                with self.subTest(compressed_size=len(compressed)):
                    self.assert_reason(replace_idat(bitmap, compressed))
            self.assertIs(ImageFile.LOAD_TRUNCATED_IMAGES, True)
            self.assertIsNone(Image.MAX_IMAGE_PIXELS)
            self.assert_reason(
                bitmap, "image_budget", limits=PngValidationLimits(max_pixels=5)
            )
        with patch.object(Image, "MAX_IMAGE_PIXELS", 1):
            self.assert_reason(bitmap)
            self.assertEqual(Image.MAX_IMAGE_PIXELS, 1)
        after = (
            Image.MAX_IMAGE_PIXELS,
            ImageFile.LOAD_TRUNCATED_IMAGES,
            list(warnings.filters),
        )
        self.assertEqual(before, after)

    def test_contiguous_idat_chunks_and_bounded_standard_library_inflation(self):
        bitmap = png_bitmap(mode="RGBA", size=(512, 256))
        length = struct.unpack_from(">I", bitmap.content, 33)[0]
        compressed = bitmap.content[41 : 41 + length]
        pieces = (compressed[:1], compressed[1:4], compressed[4:31], compressed[31:])
        bitmap = replace(
            bitmap,
            content=bitmap.content[:33]
            + b"".join(chunk(b"IDAT", piece) for piece in pieces)
            + chunk(b"IEND", b""),
        )
        calls = []
        original = zlib.decompressobj

        class ObservedInflater:
            def __init__(inner):
                inner.actual = original()

            def decompress(inner, content, max_length):
                calls.append(max_length)
                self.assertGreater(max_length, 0)
                self.assertLessEqual(max_length, 65_536)
                return inner.actual.decompress(content, max_length)

            def flush(inner, *args):
                self.fail("validator must not use potentially unbounded flush")

            def __getattr__(inner, name):
                return getattr(inner.actual, name)

        with patch(
            "ygl_test_subject.presentation.png_validation.decompressobj",
            ObservedInflater,
        ):
            metadata = validate_png(bitmap)
        self.assertEqual((metadata.width, metadata.height), (512, 256))
        self.assertGreater(len(calls), len(pieces))

    def test_verify_then_reopen_load_and_close_buffers(self):
        original = Image.open
        seen = []

        def observe(stream, *args, **kwargs):
            seen.append(stream)
            return original(stream, *args, **kwargs)

        with patch.object(Image, "open", side_effect=observe) as opened:
            validate_png(png_bitmap())
        self.assertEqual(opened.call_count, 2)
        self.assertTrue(all(stream.closed for stream in seen))

    def test_decoder_failures_safe_missing_dependency_and_cancellation_propagates(self):
        bitmap = png_bitmap()
        with patch.object(Image, "open", side_effect=OSError("SENSITIVE/path/owner")):
            self.assert_reason(bitmap)
        with patch.object(Image, "open", side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                validate_png(bitmap)
        original = builtins.__import__

        def no_pillow(name, *args, **kwargs):
            if name == "PIL":
                raise ImportError("SENSITIVE")
            return original(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=no_pillow):
            self.assert_reason(bitmap, "dependency_missing")

    def test_pillow_and_warning_globals_unchanged_for_success_and_failure(self):
        before = (
            Image.MAX_IMAGE_PIXELS,
            ImageFile.LOAD_TRUNCATED_IMAGES,
            PngImagePlugin.MAX_TEXT_CHUNK,
            PngImagePlugin.MAX_TEXT_MEMORY,
            list(warnings.filters),
        )
        validate_png(png_bitmap())
        self.assert_reason(replace_idat(png_bitmap(), b"invalid"))
        after = (
            Image.MAX_IMAGE_PIXELS,
            ImageFile.LOAD_TRUNCATED_IMAGES,
            PngImagePlugin.MAX_TEXT_CHUNK,
            PngImagePlugin.MAX_TEXT_MEMORY,
            list(warnings.filters),
        )
        self.assertEqual(before, after)
