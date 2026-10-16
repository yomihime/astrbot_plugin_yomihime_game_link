"""Finite trusted OS font candidates; never accepts a module or user path."""

import os
import struct
from io import BytesIO
from pathlib import Path

from .bundled_font import BundledFontProvider
from .card_bitmap import BitmapUnavailable

MAX_SYSTEM_BYTES = 16 * 1024 * 1024


def _cmap(content):
    """Read bounded Unicode cmap 4/12 of face zero, excluding glyph zero."""

    def u16(offset):
        return struct.unpack_from(">H", content, offset)[0]

    def u32(offset):
        return struct.unpack_from(">I", content, offset)[0]

    base = u32(12) if content[:4] == b"ttcf" else 0
    count = u16(base + 4)
    if count > 256:
        raise ValueError
    table = None
    for i in range(count):
        pos = base + 12 + i * 16
        if content[pos : pos + 4] == b"cmap":
            table = u32(pos + 8)
            length = u32(pos + 12)
            if table + length > len(content):
                raise ValueError
            break
    if table is None or u16(table + 2) > 256:
        raise ValueError
    points = set()
    work = 0
    records = {}
    for i in range(u16(table + 2)):
        rec = table + 4 + i * 8
        records[(u16(rec), u16(rec + 2))] = rec
    # Match the standard Unicode preference used by FreeType/fontTools.
    preferred = ((3, 10), (0, 6), (0, 4), (3, 1), (0, 3), (0, 2), (0, 1), (0, 0))
    selected = next((records[key] for key in preferred if key in records), None)
    if selected is None:
        raise ValueError
    for rec in (selected,):
        platform, encoding = u16(rec), u16(rec + 2)
        if platform != 0 and (platform, encoding) not in ((3, 1), (3, 10)):
            continue
        pos = table + u32(rec + 4)
        fmt = u16(pos)
        if fmt == 12:
            groups = u32(pos + 12)
            if groups > 65536 or pos + 16 + groups * 12 > len(content):
                raise ValueError
            for j in range(groups):
                p = pos + 16 + j * 12
                start, end, glyph = u32(p), u32(p + 4), u32(p + 8)
                if start > end or end > 0x10FFFF:
                    raise ValueError
                work += end - start + 1
                if work > 2_228_224:
                    raise ValueError
                points.update(range(start + (glyph == 0), end + 1))
        elif fmt == 4:
            segments = u16(pos + 6) // 2
            if segments > 8192 or pos + u16(pos + 2) > len(content):
                raise ValueError
            ends = pos + 14
            starts = ends + 2 * segments + 2
            deltas = starts + 2 * segments
            offsets = deltas + 2 * segments
            for j in range(segments):
                start, end = u16(starts + j * 2), u16(ends + j * 2)
                if start > end:
                    raise ValueError
                work += end - start + 1
                if work > 2_228_224:
                    raise ValueError
                delta, offset = u16(deltas + j * 2), u16(offsets + j * 2)
                for cp in range(start, min(end, 65534) + 1):
                    glyph = (
                        u16(offsets + j * 2 + offset + (cp - start) * 2)
                        if offset
                        else cp
                    )
                    if glyph and ((glyph + delta) & 65535):
                        points.add(cp)
    if not points:
        raise ValueError
    return frozenset(points)


def system_candidates():
    # Fixed absolute OS locations, no cwd, PATH, fontconfig command or module input.
    candidates = (
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    )
    if os.name == "nt":
        candidates = (
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/msyh.ttf"),
            Path("C:/Windows/Fonts/simhei.ttf"),
            Path("C:/Windows/Fonts/simsun.ttc"),
            Path("C:/Windows/Fonts/simkai.ttf"),
            Path("C:/Windows/Fonts/simfang.ttf"),
            Path("C:/Windows/Fonts/arial.ttf"),
        )
    return tuple(path for path in candidates if path.is_absolute())


class SystemFontProvider(BundledFontProvider):
    def __init__(self):
        for path in system_candidates():
            try:
                with path.open("rb") as stream:
                    content = stream.read(MAX_SYSTEM_BYTES + 1)
                if not 0 < len(content) <= MAX_SYSTEM_BYTES:
                    continue
                coverage = _cmap(content)
                from PIL import ImageFont

                face = ImageFont.truetype(BytesIO(content), 16, index=0)
                face.getmask("Ag").getbbox()
                self._font_bytes, self._coverage = content, coverage
                return
            except Exception:
                continue
        raise BitmapUnavailable("font_unavailable")

    def supports(self, codepoint):
        return type(codepoint) is int and codepoint in self._coverage
