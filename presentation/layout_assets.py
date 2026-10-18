"""Bounded, in-memory static card assets, with no path or URL input API.

PNG/JPEG are decoded by Pillow. SVG uses the optional resvg_py wheel *after*
strict subset validation and reserialization. This is an input-format guard,
not a security sandbox or an isolation boundary for native image libraries.
Only paths, basic geometry, groups, transforms and presentation attributes are
supported. Text/fonts, references, CSS, scripts and external resources are not.
The caller owns and must close the returned RGBA image. Nothing is cached.
"""

from __future__ import annotations

import math
import re
import warnings
import xml.etree.ElementTree as ET
import zlib
from contextlib import closing
from io import BytesIO
from typing import Literal
from unicodedata import category

from yomihime_game_link_sdk.cards import Asset as Asset

from .card_bitmap import BitmapUnavailable

MAX_ASSET_BYTES = 1_048_576
MAX_SVG_BYTES = 131_072
MAX_SOURCE_SIDE = 4096
MAX_SOURCE_PIXELS = 8_000_000
MAX_OUTPUT_SIDE = 2048
MAX_OUTPUT_PIXELS = 4_000_000
MAX_SVG_ELEMENTS = 256
MAX_SVG_DEPTH = 16
MAX_PATH_CHARS = 32_768
MAX_TOTAL_PATH_CHARS = 65_536
MAX_PATH_NUMBERS = 8192
MAX_COORDINATE = 1_000_000


_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_NUMBER_RE = re.compile(_NUMBER)
_PATH_TOKEN = re.compile(r"[MmZzLlHhVvCcSsQqTtAa]|" + _NUMBER)
_TRANSFORM = re.compile(r"([A-Za-z]+)\s*\(([^()]*)\)")
_SVG_NS = "http://www.w3.org/2000/svg"
_TAGS = {
    "svg",
    "g",
    "path",
    "rect",
    "circle",
    "ellipse",
    "line",
    "polyline",
    "polygon",
    "title",
    "desc",
}
_PRESENTATION = {
    "fill",
    "stroke",
    "stroke-width",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-miterlimit",
    "fill-rule",
    "opacity",
    "fill-opacity",
    "stroke-opacity",
    "transform",
}
_GEOMETRY = {
    "svg": {"viewBox", "width", "height", "preserveAspectRatio", "version"},
    "g": set(),
    "path": {"d"},
    "rect": {"x", "y", "width", "height", "rx", "ry"},
    "circle": {"cx", "cy", "r"},
    "ellipse": {"cx", "cy", "rx", "ry"},
    "line": {"x1", "y1", "x2", "y2"},
    "polyline": {"points"},
    "polygon": {"points"},
    "title": set(),
    "desc": set(),
}
_METADATA = {"id", "role", "aria-label", "aria-labelledby"}
_NAMED_COLORS = {
    "black": "#000000",
    "white": "#ffffff",
    "red": "#ff0000",
    "green": "#008000",
    "blue": "#0000ff",
    "yellow": "#ffff00",
    "gray": "#808080",
    "grey": "#808080",
    "silver": "#c0c0c0",
    "maroon": "#800000",
    "purple": "#800080",
    "fuchsia": "#ff00ff",
    "lime": "#00ff00",
    "olive": "#808000",
    "navy": "#000080",
    "teal": "#008080",
    "aqua": "#00ffff",
}
_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _invalid():
    raise BitmapUnavailable("invalid_input")


def _overflow():
    raise BitmapUnavailable("layout_overflow")


def _check_dimensions(width, height, *, output=False):
    side = MAX_OUTPUT_SIDE if output else MAX_SOURCE_SIDE
    pixels = MAX_OUTPUT_PIXELS if output else MAX_SOURCE_PIXELS
    if type(width) is not int or type(height) is not int or width < 1 or height < 1:
        _invalid()
    if width > side or height > side or width * height > pixels:
        _overflow()


def _color(value, *, paint=False):
    if (
        type(value) is tuple
        and len(value) == 3
        and all(type(n) is int and 0 <= n <= 255 for n in value)
    ):
        value = "#" + "".join(f"{n:02x}" for n in value)
    if type(value) is not str:
        _invalid()
    if paint and value in ("none", "currentColor"):
        return value
    if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return value.lower()
    if paint and re.fullmatch(r"#[0-9a-fA-F]{3}", value):
        return "#" + "".join(c * 2 for c in value[1:].lower())
    if paint and value.lower() in _NAMED_COLORS:
        return _NAMED_COLORS[value.lower()]
    _invalid()


def _number(value):
    if not _NUMBER_RE.fullmatch(value):
        _invalid()
    number = float(value)
    if not math.isfinite(number) or abs(number) > MAX_COORDINATE:
        _overflow()
    return number


def _numbers(value):
    """Consume all characters; no units, expressions, inf/NaN or URLs."""
    matches = list(_NUMBER_RE.finditer(value))
    if len(matches) > MAX_PATH_NUMBERS:
        _overflow()
    end = 0
    result = []
    for match in matches:
        gap = value[end : match.start()]
        if gap.strip(" ,\t\r\n"):
            _invalid()
        if end and not gap and match.group()[0] not in "+-.":
            _invalid()
        result.append(_number(match.group()))
        end = match.end()
    if value[end:].strip(" ,\t\r\n"):
        _invalid()
    return result


def _path(value):
    if len(value) > MAX_PATH_CHARS:
        _overflow()
    matches = list(_PATH_TOKEN.finditer(value))
    if not matches or matches[0].group() not in ("M", "m"):
        _invalid()
    end = 0
    command = None
    parameters = []
    number_count = 0
    counts = {
        "M": 2,
        "L": 2,
        "H": 1,
        "V": 1,
        "C": 6,
        "S": 4,
        "Q": 4,
        "T": 2,
        "A": 7,
        "Z": 0,
    }

    def check():
        if command is None:
            return
        count = counts[command]
        if count == 0:
            if parameters:
                _invalid()
        elif not parameters or len(parameters) % count:
            _invalid()
        if command == "A":
            for offset in range(0, len(parameters), 7):
                if (
                    parameters[offset] < 0
                    or parameters[offset + 1] < 0
                    or parameters[offset + 3] not in (0, 1)
                    or parameters[offset + 4] not in (0, 1)
                ):
                    _invalid()

    previous_number = False
    for match in matches:
        token = match.group()
        gap = value[end : match.start()]
        if gap.strip(" ,\t\r\n"):
            _invalid()
        is_number = token[0] in "+-.0123456789"
        if is_number:
            if command is None or (
                previous_number and not gap and token[0] not in "+-."
            ):
                _invalid()
            parameters.append(_number(token))
            number_count += 1
            if number_count > MAX_PATH_NUMBERS:
                _overflow()
        else:
            check()
            command = token.upper()
            parameters = []
        end = match.end()
        previous_number = is_number
    if value[end:].strip(" ,\t\r\n"):
        _invalid()
    check()


def _multiply(a, b):
    result = (
        a[0] * b[0] + a[2] * b[1],
        a[1] * b[0] + a[3] * b[1],
        a[0] * b[2] + a[2] * b[3],
        a[1] * b[2] + a[3] * b[3],
        a[0] * b[4] + a[2] * b[5] + a[4],
        a[1] * b[4] + a[3] * b[5] + a[5],
    )
    if any(not math.isfinite(n) or abs(n) > MAX_COORDINATE for n in result):
        _overflow()
    return result


def _transform(value):
    matrix = _IDENTITY
    end = count = 0
    for match in _TRANSFORM.finditer(value):
        if value[end : match.start()].strip(" ,\t\r\n"):
            _invalid()
        args = _numbers(match[2])
        name = match[1]
        count += 1
        if count > 16:
            _overflow()
        if name == "matrix" and len(args) == 6:
            transform = tuple(args)
        elif name == "translate" and len(args) in (1, 2):
            transform = (1, 0, 0, 1, args[0], args[1] if len(args) == 2 else 0)
        elif name == "scale" and len(args) in (1, 2):
            transform = (args[0], 0, 0, args[1] if len(args) == 2 else args[0], 0, 0)
        elif name == "rotate" and len(args) in (1, 3):
            angle = math.radians(args[0])
            c, s = math.cos(angle), math.sin(angle)
            transform = (c, s, -s, c, 0, 0)
            if len(args) == 3:
                x, y = args[1:]
                transform = (c, s, -s, c, x - c * x + s * y, y - s * x - c * y)
        elif name in ("skewX", "skewY") and len(args) == 1:
            tangent = math.tan(math.radians(args[0]))
            transform = (
                (1, 0, tangent, 1, 0, 0)
                if name == "skewX"
                else (1, tangent, 0, 1, 0, 0)
            )
        else:
            _invalid()
        matrix = _multiply(matrix, transform)
        end = match.end()
    if not count or value[end:].strip(" ,\t\r\n"):
        _invalid()
    return matrix


def _prepare_svg(content, current_color, width, height, *, fit="contain"):
    if type(content) is bytes:
        if len(content) > MAX_SVG_BYTES:
            raise BitmapUnavailable("byte_limit")
        try:
            raw = content.decode("utf-8")
        except UnicodeDecodeError:
            raise BitmapUnavailable("invalid_input") from None
    elif type(content) is str:
        if len(content) > MAX_SVG_BYTES:
            raise BitmapUnavailable("byte_limit")
        try:
            if len(content.encode("utf-8")) > MAX_SVG_BYTES:
                raise BitmapUnavailable("byte_limit")
        except UnicodeEncodeError:
            raise BitmapUnavailable("invalid_input") from None
        raw = content
    else:
        _invalid()
    # Disallow declarations/entities/processing instructions before XML parsing.
    # UTF-8 only prevents alternate-encoding bypasses of this small lexical guard.
    if (
        "<!" in raw
        or "<?" in raw
        or re.search(r"url\s*\(", raw, re.I)
        or any(category(c).startswith("C") and c not in "\t\r\n" for c in raw)
    ):
        _invalid()
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise BitmapUnavailable("invalid_input") from None
    count = path_chars = 0
    pending = [(root, 1, _IDENTITY)]
    while pending:
        element, depth, parent_transform = pending.pop()
        count += 1
        if count > MAX_SVG_ELEMENTS or depth > MAX_SVG_DEPTH:
            _overflow()
        tag = element.tag
        if tag.startswith("{" + _SVG_NS + "}"):
            tag = tag[len(_SVG_NS) + 2 :]
        if tag not in _TAGS or (element is root) != (tag == "svg"):
            _invalid()
        element.tag = tag
        if (element.text or "").strip() and tag not in ("title", "desc"):
            _invalid()
        if (element.tail or "").strip() or (tag not in ("svg", "g") and len(element)):
            _invalid()
        if tag in ("title", "desc") and len(element.text or "") > 4096:
            _overflow()
        transform = parent_transform
        for attr, value in list(element.attrib.items()):
            if attr in _METADATA:
                if len(value) > 4096:
                    _overflow()
                del element.attrib[attr]
                continue
            if (
                attr not in _GEOMETRY[tag] | _PRESENTATION
                or len(value) > MAX_PATH_CHARS
            ):
                _invalid()
            if attr in ("fill", "stroke"):
                color = _color(value, paint=True)
                element.set(attr, current_color if color == "currentColor" else color)
            elif attr == "d":
                path_chars += len(value)
                if path_chars > MAX_TOTAL_PATH_CHARS:
                    _overflow()
                _path(value)
            elif attr == "points":
                numbers = _numbers(value)
                if len(numbers) < (6 if tag == "polygon" else 4) or len(numbers) % 2:
                    _invalid()
            elif attr == "viewBox":
                numbers = _numbers(value)
                if len(numbers) != 4 or numbers[2] <= 0 or numbers[3] <= 0:
                    _invalid()
            elif attr == "transform":
                transform = _multiply(parent_transform, _transform(value))
            elif attr == "preserveAspectRatio":
                if not re.fullmatch(
                    r"(?:none|x(?:Min|Mid|Max)Y(?:Min|Mid|Max)(?: (?:meet|slice))?)",
                    value,
                ):
                    _invalid()
            elif attr == "version":
                if value not in ("1.0", "1.1", "2.0"):
                    _invalid()
            elif attr == "fill-rule":
                if value not in ("evenodd", "nonzero"):
                    _invalid()
            elif attr == "stroke-linecap":
                if value not in ("butt", "round", "square"):
                    _invalid()
            elif attr == "stroke-linejoin":
                if value not in ("miter", "round", "bevel"):
                    _invalid()
            else:
                number = _number(value)
                if (
                    attr in ("width", "height", "r", "rx", "ry", "stroke-width")
                    and number < 0
                ):
                    _invalid()
                if (
                    attr in ("opacity", "fill-opacity", "stroke-opacity")
                    and not 0 <= number <= 1
                ):
                    _invalid()
                if attr == "stroke-miterlimit" and not 1 <= number <= 100:
                    _invalid()
                if (
                    tag == "svg"
                    and attr in ("width", "height")
                    and not 0 < number <= MAX_SOURCE_SIDE
                ):
                    _overflow()
        pending.extend((child, depth + 1, transform) for child in reversed(element))
    if "viewBox" not in root.attrib:
        _invalid()
    # All non-geometry metadata is discarded, not passed to the renderer.
    for element in root.iter():
        for child in list(element):
            if child.tag in ("title", "desc"):
                element.remove(child)
    root.set(
        "preserveAspectRatio", "xMidYMid " + ("slice" if fit == "cover" else "meet")
    )
    # resvg's size options fit its intrinsic viewport. Set that viewport to
    # the bounded target first, including for viewBox-only input.
    root.set("xmlns", _SVG_NS)
    root.set("width", str(width))
    root.set("height", str(height))
    return ET.tostring(root, encoding="utf-8")


def _pillow():
    try:
        from PIL import Image, ImageOps
    except ImportError:
        raise BitmapUnavailable("dependency_missing") from None
    return Image, ImageOps


def _png_without_metadata(content):
    """Validate chunk framing/CRC before Pillow sees compressed text/profiles.

    Non-rendering ancillary chunks are discarded without decompression. APNG
    chunks are rejected, including default-image animations that Pillow might
    otherwise expose as a single initially visible frame.
    """
    output = bytearray(content[:8])
    offset = 8
    chunks = 0
    seen_header = False
    while offset < len(content):
        chunks += 1
        if chunks > 4096:
            _overflow()
        if offset + 12 > len(content):
            _invalid()
        length = int.from_bytes(content[offset : offset + 4], "big")
        end = offset + 12 + length
        if end > len(content):
            _invalid()
        tag = content[offset + 4 : offset + 8]
        data = content[offset + 8 : end - 4]
        if not re.fullmatch(rb"[A-Za-z]{4}", tag) or zlib.crc32(
            tag + data
        ) != int.from_bytes(content[end - 4 : end], "big"):
            _invalid()
        if not seen_header:
            if tag != b"IHDR" or length != 13:
                _invalid()
            _check_dimensions(
                int.from_bytes(data[:4], "big"), int.from_bytes(data[4:8], "big")
            )
            seen_header = True
        elif tag == b"IHDR":
            _invalid()
        if tag in (b"acTL", b"fcTL", b"fdAT"):
            _invalid()
        if tag in (b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS"):
            output.extend(content[offset:end])
        elif not tag[0] & 32:
            _invalid()
        if tag == b"IEND":
            if length != 0 or end != len(content):
                _invalid()
            return bytes(output)
        offset = end
    _invalid()


def _raster(content, kind, width, height, *, fit="contain", exact_size=False):
    Image, ImageOps = _pillow()
    if type(content) is not bytes or not content:
        _invalid()
    if len(content) > MAX_ASSET_BYTES:
        raise BitmapUnavailable("byte_limit")
    expected = "PNG" if kind == "png" else "JPEG"
    signature = b"\x89PNG\r\n\x1a\n" if kind == "png" else b"\xff\xd8\xff"
    if not content.startswith(signature):
        _invalid()
    if kind == "png":
        content = _png_without_metadata(content)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with (
                BytesIO(content) as stream,
                closing(Image.open(stream, formats=[expected])) as source,
            ):
                if source.format != expected or getattr(source, "n_frames", 1) != 1:
                    _invalid()
                _check_dimensions(*source.size)
                if exact_size and source.size != (width, height):
                    _invalid()
                source.verify()
            with (
                BytesIO(content) as stream,
                closing(Image.open(stream, formats=[expected])) as source,
            ):
                source.load()
                with closing(source.convert("RGBA")) as converted:
                    resize = ImageOps.fit if fit == "cover" else ImageOps.contain
                    with closing(
                        resize(converted, (width, height), Image.Resampling.LANCZOS)
                    ) as fitted:
                        result = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                        try:
                            result.paste(
                                fitted,
                                (
                                    (width - fitted.width) // 2,
                                    (height - fitted.height) // 2,
                                ),
                            )
                            result.info.clear()
                            return result
                        except BaseException:
                            result.close()
                            raise
    except BitmapUnavailable:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise BitmapUnavailable("layout_overflow") from None
    except Exception:
        raise BitmapUnavailable("invalid_input") from None


def rasterize_asset(
    asset: Asset,
    width: int,
    height: int,
    current_color: str,
    *,
    fit: Literal["contain", "cover"] = "contain",
):
    """Return a newly owned RGBA raster at exact requested, bounded dimensions.

    Both formats are centered: contain pads transparently, cover crops. SVG
    uses its viewBox with the layout-selected meet/slice fitting. No implicit
    resource loading, file writes, executable fallback, or persistent cache.
    Errors expose only stable BitmapUnavailable reasons, never source content.
    """
    _check_dimensions(width, height, output=True)
    if type(fit) is not str or fit not in ("contain", "cover"):
        _invalid()
    color = _color(current_color)
    if (
        type(asset) is not Asset
        or type(asset.kind) is not str
        or asset.kind not in ("svg", "png", "jpeg")
        or type(asset.required) is not bool
        or type(asset.alt) is not str
        or len(asset.alt) > 512
        or any(category(c).startswith("C") for c in asset.alt)
    ):
        _invalid()
    if asset.kind != "svg":
        return _raster(asset.content, asset.kind, width, height, fit=fit)
    svg = _prepare_svg(asset.content, color, width, height, fit=fit)
    _pillow()
    try:
        import resvg_py
    except (ImportError, OSError):
        raise BitmapUnavailable("dependency_missing") from None
    try:
        content = resvg_py.svg_to_bytes(
            svg_string=svg.decode("utf-8"),
            width=width,
            height=height,
            skip_system_fonts=True,
        )
    except Exception:
        raise BitmapUnavailable("invalid_input") from None
    return _raster(content, "png", width, height, exact_size=True)
