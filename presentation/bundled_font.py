"""Explicit trusted Noto Sans CJK SC Regular provider, original face zero.

Import performs no asset IO or optional dependency import. Each provider holds
one verified immutable 16,437,364-byte font snapshot for its lifetime. No global
font/byte cache exists. Each render loads four independent Pillow faces from
that snapshot; Pillow may retain its own copies until those faces are released.
These are ownership bounds, not a precise heap/RSS or memory-erasure claim.
"""

from bisect import bisect_right
from hashlib import sha256
from io import BytesIO
from pathlib import Path

from ._font_coverage import RANGES
from .card_bitmap import BitmapUnavailable

FONT_FILENAME = "NotoSansCJKsc-Regular.otf"
FONT_SIZE = 16_437_364
FONT_SHA256 = "2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b"
FACE_INDEX = 0
_SIZES = (16, 20, 22, 28)


class BundledFontProvider:
    """No path, URL, owner or system-font input; exact packaged asset only.

    Construct per render/request and release after use. ``load`` has no cache
    and only accepts the renderer's four sizes. Callers retaining providers or
    returned faces are responsible for their lifetime.
    """

    def __init__(self) -> None:
        try:
            path = Path(__file__).parent / "assets" / "fonts" / FONT_FILENAME
            with path.open("rb") as stream:
                content = stream.read(FONT_SIZE + 1)
            if len(content) != FONT_SIZE or sha256(content).hexdigest() != FONT_SHA256:
                raise ValueError
        except Exception:
            raise BitmapUnavailable("font_unavailable") from None
        self._font_bytes = content

    def supports(self, codepoint: int) -> bool:
        if type(codepoint) is not int or not 0 <= codepoint <= 0x10FFFF:
            return False
        index = bisect_right(RANGES, (codepoint, 0x110000)) - 1
        return index >= 0 and RANGES[index][0] <= codepoint <= RANGES[index][1]

    def load(self, size: int) -> object:
        if type(size) is not int or size not in _SIZES:
            raise BitmapUnavailable("font_unavailable")
        try:
            from PIL import ImageFont
        except ImportError:
            raise BitmapUnavailable("dependency_missing") from None
        except Exception:
            raise BitmapUnavailable("font_unavailable") from None
        try:
            # Both integrity verification and face loading use this same bytes
            # object. Never reopen a path after checking its digest.
            return ImageFont.truetype(BytesIO(self._font_bytes), size, index=FACE_INDEX)
        except Exception:
            raise BitmapUnavailable("font_unavailable") from None
