"""Strict validation of internal PNG payloads, with no eager Pillow import.

Only the IHDR/IDAT/IEND subset emitted by the card renderer is accepted. This
bounds container scanning and excludes compressed metadata before decoding;
Pillow remains the decoder, not a CPU or process-memory sandbox.
"""

from dataclasses import dataclass, fields
from hashlib import sha256
from io import BytesIO
from struct import unpack_from
from zlib import crc32, decompressobj
from zlib import error as ZlibError

from .card_bitmap import RenderedBitmap


class PngValidationError(Exception):
    """A stable reason without decoder, path or payload diagnostics."""

    def __init__(self, reason: str = "image_invalid") -> None:
        self.reason = (
            reason
            if reason in ("image_invalid", "image_budget", "dependency_missing")
            else "image_invalid"
        )
        super().__init__(self.reason)


@dataclass(frozen=True)
class PngValidationLimits:
    max_bytes: int = 1_048_576
    max_width: int = 2048
    max_height: int = 8192
    max_pixels: int = 8_000_000


@dataclass(frozen=True)
class ValidatedPngMetadata:
    digest: str
    width: int
    height: int
    size: int


def check_png_input(bitmap: RenderedBitmap, limits: PngValidationLimits) -> None:
    """Cheap checks suitable before reserving memory or awaiting authority."""
    if type(limits) is not PngValidationLimits:
        raise PngValidationError()
    hard = PngValidationLimits()
    for item in fields(limits):
        value = getattr(limits, item.name)
        if type(value) is not int or not 1 <= value <= getattr(hard, item.name):
            raise PngValidationError()
    if (
        type(bitmap) is not RenderedBitmap
        or type(bitmap.content) is not bytes
        or type(bitmap.width) is not int
        or type(bitmap.height) is not int
        or type(bitmap.media_type) is not str
        or bitmap.media_type != "image/png"
        or bitmap.width < 1
        or bitmap.height < 1
    ):
        raise PngValidationError()
    if (
        len(bitmap.content) > limits.max_bytes
        or bitmap.width > limits.max_width
        or bitmap.height > limits.max_height
        or bitmap.width * bitmap.height > limits.max_pixels
    ):
        raise PngValidationError("image_budget")
    if not bitmap.content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise PngValidationError()


def _container(bitmap: RenderedBitmap, limits: PngValidationLimits) -> None:
    content = bitmap.content
    position = 8
    seen_header = seen_data = False
    inflater = None
    decoded = expected = stride = 0

    def consume(payload: memoryview) -> None:
        # Standard-library inflater only: bounded chunks, no join or flush.
        # Validate filter *tags* without reversing any filter or decoding pixels.
        nonlocal decoded
        remaining = payload
        try:
            while remaining:
                output = inflater.decompress(
                    remaining, min(65_536, expected - decoded + 1)
                )
                if decoded + len(output) > expected:
                    raise PngValidationError()
                if any(
                    output[index] > 4
                    for index in range((-decoded) % stride, len(output), stride)
                ):
                    raise PngValidationError()
                decoded += len(output)
                if inflater.unused_data:
                    raise PngValidationError()
                tail = inflater.unconsumed_tail
                if tail and len(tail) >= len(remaining) and not output:
                    raise PngValidationError()
                remaining = tail
        except ZlibError:
            raise PngValidationError() from None

    while position + 12 <= len(content):
        length = unpack_from(">I", content, position)[0]
        end = position + 12 + length
        if end > len(content):
            raise PngValidationError()
        kind = content[position + 4 : position + 8]
        # A view avoids copying an arbitrary (up to 1 MiB) chunk for CRC.
        chunk = memoryview(content)[position + 4 : end - 4]
        if crc32(chunk) & 0xFFFFFFFF != unpack_from(">I", content, end - 4)[0]:
            raise PngValidationError()
        if kind == b"IHDR" and not seen_header and position == 8 and length == 13:
            width, height, depth, color, compression, filtering, interlace = (
                unpack_from(">IIBBBBB", content, position + 8)
            )
            if (
                width < 1
                or height < 1
                or depth != 8
                or color not in (2, 6)
                or compression != 0
                or filtering != 0
                or interlace != 0
            ):
                raise PngValidationError()
            if (
                width > limits.max_width
                or height > limits.max_height
                or width * height > limits.max_pixels
            ):
                raise PngValidationError("image_budget")
            if (width, height) != (bitmap.width, bitmap.height):
                raise PngValidationError()
            stride = width * (3 if color == 2 else 4) + 1
            expected = height * stride
            inflater = decompressobj()
            seen_header = True
        elif kind == b"IDAT" and seen_header:
            consume(memoryview(content)[position + 8 : end - 4])
            seen_data = True
        elif (
            kind == b"IEND"
            and seen_header
            and seen_data
            and length == 0
            and end == len(content)
        ):
            if (
                not inflater.eof
                or inflater.unconsumed_tail
                or inflater.unused_data
                or decoded != expected
            ):
                raise PngValidationError()
            return
        else:
            raise PngValidationError()
        position = end
    raise PngValidationError()


def validate_png(
    bitmap: RenderedBitmap, *, limits: PngValidationLimits = PngValidationLimits()
) -> ValidatedPngMetadata:
    """Validate actual PNG bytes, then verify and fully decode with closed IO."""
    check_png_input(bitmap, limits)
    _container(bitmap, limits)
    try:
        from PIL import Image
    except ImportError:
        raise PngValidationError("dependency_missing") from None
    try:
        # Respect a stricter host policy without warning-filter or Pillow-global
        # changes. A relaxed/disabled host limit cannot relax our own caps.
        if (
            Image.MAX_IMAGE_PIXELS is not None
            and bitmap.width * bitmap.height > Image.MAX_IMAGE_PIXELS
        ):
            raise PngValidationError()
        for verify in (True, False):
            with (
                BytesIO(bitmap.content) as stream,
                Image.open(stream, formats=("PNG",)) as image,
            ):
                if (
                    image.format != "PNG"
                    or image.size != (bitmap.width, bitmap.height)
                    or image.mode not in ("RGB", "RGBA")
                    or image.n_frames != 1
                    or getattr(image, "is_animated", False)
                ):
                    raise PngValidationError()
                if verify:
                    image.verify()
                else:
                    image.load()
    except Exception:
        raise PngValidationError() from None
    return ValidatedPngMetadata(
        sha256(bitmap.content).hexdigest(),
        bitmap.width,
        bitmap.height,
        len(bitmap.content),
    )
