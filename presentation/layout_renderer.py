"""Shared measured layout and production rich-text paint for declarative cards.

Nothing here projects a game result, chooses grade thresholds, parses markup,
reads the network or dispatches messages. All text uses the pinned bundled face
unless trusted tests explicitly inject a reviewed FontProvider. Backgrounds and
assets are completed before the existing RichSession samples contrast or paints
its B shadow / A gold-glow effects.

BitmapBounds controls text, layout and RichSession-owned raster work. Asset
decoding has separate bounded source/output limits; codec/native heap and total
process RSS are not claimed to be covered by the rich-text work-byte model.
"""

from __future__ import annotations

import math
from contextlib import closing
from dataclasses import dataclass, fields, replace
from io import BytesIO
from types import MappingProxyType
from unicodedata import category

from yomihime_game_link_sdk.cards import Cell, TaggedText

from .bundled_font import BundledFontProvider
from .card_bitmap import BitmapBounds, BitmapUnavailable, FontProvider, RenderedBitmap
from .layout_document import (
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
    ThemeOption,
    ThemePalette,
)
from .rich_text import (
    Grade,
    RichSession,
    RichText,
    TextCell,
    TextPalette,
    TextRole,
    TextSpan,
    _LineMetrics,
    _measure_run,
    face_color,
    rgb,
)

THEMES = MappingProxyType(
    {
        "warm_paper": ThemePalette(
            "#f3e8de",
            "#fff9f1",
            "#fcf4e9",
            "#f0e3d3",
            "#dfcdb8",
            "#5b4033",
            "#92735d",
            "#51392c",
            "#ad7950",
        ),
        "cool_white": ThemePalette(
            "#e8edf5",
            "#f8fafc",
            "#ffffff",
            "#edf1f5",
            "#ccd6e2",
            "#4c5a69",
            "#738291",
            "#293645",
            "#7e94ab",
        ),
        "deep_indigo": ThemePalette(
            "#0f1423",
            "#1c2337",
            "#222c42",
            "#11182b",
            "#3b4561",
            "#d0d8e7",
            "#a3afc8",
            "#edf0f6",
            "#8a9fd2",
        ),
    }
)

_THEME_LABELS = MappingProxyType(
    {
        "warm_paper": "暖桃纸",
        "cool_white": "冷白",
        "deep_indigo": "深靛",
    }
)


def list_themes() -> tuple[ThemeOption, ...]:
    """Return stable Core choices from the exact catalog used by the renderer.

    This is a pure staging API. It does not load fonts, persist settings, add an
    SDK export or serve a configuration page. Core chooses one concrete ID.
    Future module selectors prepend "follow Core", whose stored value is None.
    """
    return tuple(
        ThemeOption(theme_id, _THEME_LABELS[theme_id], palette)
        for theme_id, palette in THEMES.items()
    )


def apply_theme_selection(
    document: Document,
    core_theme: str,
    module_theme: str | ThemePalette | None = None,
) -> Document:
    """Apply the Core/module theme policy to one immutable document.

    Core cards pass only a concrete Core catalog ID. A module with None follows
    that Core setting; an explicit module ID or custom palette overrides only
    this document. The incoming document's theme is never a policy fallback.
    Text, explicit colors, grade semantics and assets remain unchanged.

    The caller supplies an authorized settings snapshot. This pure helper does
    not authenticate callers, persist settings, wire a UI or select ownership.
    Unknown IDs and malformed custom palettes fail instead of silently falling
    back, including when an invalid Core ID accompanies a module override.
    """
    if type(document) is not Document:
        raise BitmapUnavailable("invalid_input")
    if type(core_theme) is not str or core_theme not in THEMES:
        raise BitmapUnavailable("invalid_input")
    if module_theme is None:
        selected = core_theme
    elif type(module_theme) is str and module_theme in THEMES:
        selected = module_theme
    elif type(module_theme) is ThemePalette:
        for field in fields(module_theme):
            _color(getattr(module_theme, field.name))
        selected = module_theme
    else:
        raise BitmapUnavailable("invalid_input")
    return replace(document, theme=selected)


@dataclass(frozen=True)
class LayoutTokens:
    margin: int
    gap: int
    panel_padding: int
    row_padding: int
    radius: int


TOKENS = {
    "directory": LayoutTokens(56, 22, 24, 16, 20),
    "detail": LayoutTokens(56, 18, 22, 14, 18),
    "table": LayoutTokens(56, 18, 18, 12, 16),
}


@dataclass(frozen=True)
class LayoutBox:
    """Logical bounds for diagnostics; containers intentionally enclose children."""

    path: str
    kind: str
    bounds: tuple[float, float, float, float]


@dataclass(frozen=True)
class MeasuredDocument:
    width: int
    height: int
    boxes: tuple[LayoutBox, ...]


@dataclass(frozen=True)
class _Line:
    spans: tuple[Span, ...]
    box: tuple[int, int, int, int]
    advance: float
    height: int


@dataclass
class _Text:
    x: float
    y: float
    width: float
    height: float
    size: int
    lines: tuple[_Line, ...]
    align: str


@dataclass
class _Shape:
    box: tuple[float, float, float, float]
    fill: str
    radius: int = 0
    outline: str | None = None


@dataclass
class _Image:
    asset: object
    box: tuple[float, float, float, float]
    fit: str = "contain"


def _fail(reason="invalid_input"):
    raise BitmapUnavailable(reason)


def _tagged_inline(value):
    """Explicit SDK opt-in only; ordinary strings are never parsed as tags."""
    from .layout_markup import parse_inline_markup

    try:
        return parse_inline_markup(value.source)
    except (TypeError, ValueError):
        _fail()


def _tuple(value, maximum=1024):
    if type(value) is not tuple:
        _fail()
    if len(value) > maximum:
        _fail("layout_overflow")


def _number(value, low, high):
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or not low <= value <= high
    ):
        _fail()


def _color(value):
    if (
        type(value) is not str
        or len(value) != 7
        or value[0] != "#"
        or any(char not in "0123456789abcdefABCDEF" for char in value[1:])
    ):
        _fail()
    try:
        rgb(value)
    except (TypeError, ValueError):
        _fail()


def _validate(document, bounds):
    """Validate the complete tree and text quotas before font or image allocation."""
    from .layout_assets import Asset

    if type(document) is not Document or type(bounds) is not BitmapBounds:
        _fail()
    hard = BitmapBounds()
    for field in fields(bounds):
        value = getattr(bounds, field.name)
        if type(value) is not int or not 1 <= value <= getattr(hard, field.name):
            _fail()
    if type(document.width) is not int or document.width < 400:
        _fail()
    if document.width > bounds.max_width:
        _fail("layout_overflow")
    if type(document.layout) is not str or document.layout not in TOKENS:
        _fail()
    if type(document.theme) is str:
        if document.theme not in THEMES:
            _fail()
        palette = THEMES[document.theme]
    elif type(document.theme) is ThemePalette:
        palette = document.theme
    else:
        _fail()
    for field in fields(palette):
        _color(getattr(palette, field.name))
    strings = []
    total = raw_extra = lines = nodes = rows = sections = asset_count = asset_bytes = 0

    def content(value):
        nonlocal total, raw_extra, lines
        if type(value) is TaggedText:
            if (
                type(value.source) is not str
                or len(value.source) > bounds.max_string_chars
                or total + raw_extra + len(value.source) > bounds.max_chars
            ):
                _fail("layout_overflow")
            parsed = _tagged_inline(value)
            raw_extra += len(value.source) - sum(
                len(span.text) for span in parsed.spans
            )
            value = parsed
        if type(value) is str:
            text = value
        elif type(value) is Inline:
            _tuple(value.spans, 32)
            if not value.spans:
                _fail()
            for span in value.spans:
                if type(span) is not Span or type(span.text) is not str:
                    _fail()
                if not 0 < len(span.text) <= 512:
                    _fail("layout_overflow")
                if span.emphasis not in (
                    "normal",
                    "strong",
                    "secondary",
                    "emphasis",
                    "gold",
                ):
                    _fail()
                if span.color is not None:
                    _color(span.color)
                    if span.emphasis == "gold":
                        _fail()
            text = "".join(span.text for span in value.spans)
        elif type(value) is RichText:
            _tuple(value.spans, 32)
            if not value.spans or any(
                type(span) is not TextSpan for span in value.spans
            ):
                _fail()
            text = "".join(span.text for span in value.spans)
        else:
            _fail()
        if any(
            c != "\n" and (category(c).startswith("C") or c in "\u2028\u2029")
            for c in text
        ):
            _fail()
        total += len(text)
        lines += text.count("\n") + 1
        if (
            len(text) > bounds.max_string_chars
            or total + raw_extra > bounds.max_chars
            or lines > bounds.max_input_lines
        ):
            _fail("layout_overflow")
        strings.append(text)

    def align(value):
        if type(value) is not str or value not in ("left", "center", "right"):
            _fail()

    def valign(value):
        if type(value) is not str or value not in ("top", "center", "bottom"):
            _fail()

    def asset(value):
        nonlocal asset_count, asset_bytes
        if type(value) is not Asset:
            _fail()
        if type(value.alt) is not str or type(value.required) is not bool:
            _fail()
        content(value.alt)
        # Decoder performs format-level validation; admit source bytes first.
        if value.kind not in ("svg", "png", "jpeg") or type(value.content) not in (
            str,
            bytes,
        ):
            _fail()
        if len(value.content) > 1_048_576:
            _fail("byte_limit")
        asset_count += 1
        asset_bytes += (
            len(value.content.encode("utf-8"))
            if type(value.content) is str
            else len(value.content)
        )
        if asset_bytes > 8_388_608:
            _fail("byte_limit")
        if asset_count > 32:
            _fail("layout_overflow")

    def blocks(values, depth):
        _tuple(values, 128)
        for value in values:
            block(value, depth)

    def block(value, depth=0):
        nonlocal nodes, rows, sections
        nodes += 1
        if depth > 8 or nodes > 512:
            _fail("layout_overflow")
        if type(value) is Text:
            content(value.content)
            if value.style not in ("title", "heading", "body", "caption", "stat"):
                _fail()
            align(value.align)
            if value.wrap not in ("wrap", "nowrap"):
                _fail()
        elif type(value) is Section:
            sections += 1
            if sections > bounds.max_sections:
                _fail("layout_overflow")
            content(value.title)
            content(value.subtitle)
            if type(value.panel) is not bool:
                _fail()
            blocks(value.blocks, depth + 1)
        elif type(value) is Row:
            _tuple(value.cells, bounds.max_columns)
            _tuple(value.weights, bounds.max_columns)
            if not value.cells or (
                value.weights and len(value.weights) != len(value.cells)
            ):
                _fail()
            for weight in value.weights:
                _number(weight, 0.1, 100)
            valign(value.valign)
            blocks(value.cells, depth + 1)
        elif type(value) is PropertyGrid:
            _tuple(value.properties, 64)
            if type(value.columns) is not int or not 1 <= value.columns <= min(
                4, bounds.max_columns
            ):
                _fail()
            for prop in value.properties:
                if type(prop) is not Property:
                    _fail()
                content(prop.label)
                content(prop.value)
        elif type(value) is Table:
            _tuple(value.columns, bounds.max_columns)
            _tuple(value.rows, bounds.max_rows)
            rows += len(value.rows)
            if not value.columns or rows > bounds.max_rows:
                _fail("layout_overflow")
            for column in value.columns:
                if type(column) is not Column:
                    _fail()
                content(column.title)
                _number(column.weight, 0.1, 100)
                align(column.align)
                valign(column.valign)
                if column.wrap not in ("wrap", "nowrap"):
                    _fail()
            for row in value.rows:
                _tuple(row, bounds.max_columns)
                if len(row) != len(value.columns):
                    _fail()
                for cell in row:
                    if type(cell) in (TextCell, Cell):
                        align(cell.align)
                        valign(cell.valign)
                        cell = cell.content
                    content(cell)
        elif type(value) is Image:
            asset(value.asset)
            align(value.align)
            if value.width is not None and (
                type(value.width) is not int or not 16 <= value.width <= 2048
            ):
                _fail()
            if type(value.height) is not int or not 16 <= value.height <= 1024:
                _fail()
            content(value.caption)
            if value.fit not in ("contain", "cover"):
                _fail()
        else:
            _fail()

    for value in (
        document.title,
        document.subtitle,
        document.brand,
        document.powered_by,
    ):
        content(value)
    _tuple(document.sources, 32)
    for value in document.sources:
        content(value)
    if document.logo is not None:
        asset(document.logo)
    blocks(document.blocks, 0)
    return palette, strings


class _Layout:
    def __init__(self, document, palette, provider, bounds):
        try:
            from PIL import ImageFont
        except Exception:
            _fail("dependency_missing")

        self.document, self.palette, self.bounds = document, palette, bounds
        self.tokens = TOKENS[document.layout]
        self.fonts = {}
        self.texts, self.shapes, self.images, self.boxes = [], [], [], []
        self.measurements = self.output_lines = 0
        borrowed = 0
        try:
            for source_size, size in ((16, 16), (20, 24), (22, 28), (28, 40)):
                base = provider.load(source_size)
                if (
                    type(base) is not ImageFont.FreeTypeFont
                    or type(getattr(base, "font_bytes", None)) is not bytes
                    or not 0 < len(base.font_bytes) <= 16 * 1024 * 1024
                    or type(base.path) is not BytesIO
                    or type(base.index) is not int
                    or not 0 <= base.index < 10
                ):
                    _fail("font_unavailable")
                with base.path.getbuffer() as view:
                    if view.nbytes != len(base.font_bytes):
                        _fail("font_unavailable")
                owned = (
                    16 * 1024 * 1024
                    + borrowed
                    + sum(len(f.font_bytes) for f in self.fonts.values())
                    + 4 * len(base.font_bytes)
                )
                if owned > bounds.max_rich_font_bytes:
                    _fail("layout_overflow")
                with BytesIO(base.font_bytes) as stream:
                    face = base.font_variant(
                        font=stream, size=size * 2, index=base.index
                    )
                if face.font_bytes != base.font_bytes or face.index != base.index:
                    _fail("font_unavailable")
                self.fonts[size] = face
                borrowed += 2 * len(base.font_bytes)
                base = None
        except BitmapUnavailable:
            self.fonts.clear()
            raise
        except Exception:
            self.fonts.clear()
            _fail("font_unavailable")

    def measure(self, font, method, text, **kwargs):
        self.measurements += 1
        if self.measurements > self.bounds.max_measurements:
            _fail("layout_overflow")
        try:
            return getattr(font, method)(text, **kwargs)
        except Exception:
            _fail("font_unavailable")

    def spans(self, value, emphasis):
        if type(value) is TaggedText:
            value = _tagged_inline(value)
        if type(value) is str:
            return (Span(value, emphasis=emphasis),)
        if type(value) is Inline:
            return value.spans
        palette = TextPalette(
            self.palette.text,
            self.palette.secondary,
            self.palette.title,
            self.palette.accent,
        )
        return tuple(
            Span(span.text, emphasis="gold")
            if span.grade is Grade.GOLD
            else Span(span.text, color=face_color(span, palette))
            for span in value.spans
        )

    def wrap(self, value, size, width, emphasis, wrapping="wrap"):
        if width <= 0:
            _fail("layout_overflow")
        lines, runs, prefixes, chars = [], [], [_LineMetrics()], 0
        font = self.fonts[size]

        def emit():
            self.output_lines += 1
            if self.output_lines > self.bounds.max_output_lines:
                _fail("layout_overflow")
            metrics = prefixes[-1]
            box = metrics.box or (0, 0, 0, 0)
            lines.append(
                _Line(
                    tuple(runs),
                    box,
                    metrics.advance,
                    max(size + 12, math.ceil((box[3] - box[1]) / 2) + 10),
                )
            )

        def candidate(char, source):
            extend = (
                runs
                and (runs[-1].color, runs[-1].emphasis)
                == (source.color, source.emphasis)
                and len(runs[-1].text) < 512
            )
            span = replace(source, text=runs[-1].text + char if extend else char)
            metrics = (prefixes[-2] if extend else prefixes[-1]).append(
                _measure_run(font, span.text, self.measure)
            )
            return span, metrics, extend

        tokens = [
            (char, source)
            for source in self.spans(value, emphasis)
            for char in source.text
        ]
        index, last_break = 0, None
        while index < len(tokens):
            char, source = tokens[index]
            if char == "\n":
                emit()
                runs, prefixes, chars, last_break = [], [_LineMetrics()], 0, None
                index += 1
                continue
            span, metrics, extend = candidate(char, source)
            if (
                metrics.width > width * 2
                or chars >= 1024
                or (not extend and len(runs) >= 32)
            ):
                if not runs or wrapping == "nowrap":
                    _fail("layout_overflow")
                if last_break is not None:
                    # Prefer a word boundary, retaining every supplied character.
                    # A long unbroken token or CJK text still wraps by glyph.
                    break_index, keep = last_break
                    trimmed, prefixes = [], [_LineMetrics()]
                    for run in runs:
                        if keep <= 0:
                            break
                        part = replace(run, text=run.text[:keep])
                        trimmed.append(part)
                        prefixes.append(
                            prefixes[-1].append(
                                _measure_run(font, part.text, self.measure)
                            )
                        )
                        keep -= len(part.text)
                    runs = trimmed
                    index = break_index
                emit()
                runs, prefixes, chars, last_break = [], [_LineMetrics()], 0, None
                continue
            if extend:
                runs[-1], prefixes[-1] = span, metrics
            else:
                runs.append(span)
                prefixes.append(metrics)
            chars += 1
            index += 1
            if char.isspace():
                last_break = index, chars
        emit()
        return tuple(lines)

    def text(
        self,
        value,
        x,
        y,
        width,
        size,
        path,
        emphasis="normal",
        align="left",
        wrapping="wrap",
    ):
        lines = self.wrap(value, size, width, emphasis, wrapping)
        height = sum(line.height for line in lines)
        self.texts.append(_Text(x, y, width, height, size, lines, align))
        self.boxes.append(LayoutBox(path, "text", (x, y, x + width, y + height)))
        return height

    def mark(self):
        return tuple(
            len(values) for values in (self.texts, self.shapes, self.images, self.boxes)
        )

    def shift(self, start, end, dy):
        for text in self.texts[start[0] : end[0]]:
            text.y += dy
        for shape in self.shapes[start[1] : end[1]]:
            x0, y0, x1, y1 = shape.box
            shape.box = (x0, y0 + dy, x1, y1 + dy)
        for image in self.images[start[2] : end[2]]:
            x0, y0, x1, y1 = image.box
            image.box = (x0, y0 + dy, x1, y1 + dy)
        for index in range(start[3], end[3]):
            box = self.boxes[index]
            x0, y0, x1, y1 = box.bounds
            self.boxes[index] = replace(box, bounds=(x0, y0 + dy, x1, y1 + dy))

    def stack(self, blocks, x, y, width, path):
        top = y
        for index, block in enumerate(blocks):
            if index:
                y += self.tokens.gap
            y += self.block(block, x, y, width, f"{path}/{index}")
        return y - top

    def columns(self, weights, x, width, gap):
        available = width - gap * (len(weights) - 1)
        if available <= 0:
            _fail("layout_overflow")
        total, used = sum(weights), 0
        result = []
        for weight in weights:
            column_width = available * weight / total
            if column_width < 32:
                _fail("layout_overflow")
            result.append((x + used, column_width))
            used += column_width + gap
        return result

    def block(self, value, x, y, width, path):
        top, pad = y, self.tokens.panel_padding
        p = self.palette
        if type(value) is Text:
            size, role = {
                "title": (40, "strong"),
                "heading": (28, "strong"),
                "body": (24, "normal"),
                "caption": (16, "secondary"),
                "stat": (28, "strong"),
            }[value.style]
            return self.text(
                value.content, x, y, width, size, path, role, value.align, value.wrap
            )
        if type(value) is Section:
            start_shape = len(self.shapes)
            inset = pad if value.panel else 0
            inner_x, inner_w = x + inset, width - inset * 2
            y += inset
            if value.title:
                self.shapes.append(
                    _Shape((inner_x, y + 3, inner_x + 4, y + 29), p.accent, 2)
                )
                y += self.text(
                    value.title,
                    inner_x + 15,
                    y,
                    inner_w - 15,
                    28,
                    path + "/title",
                    "strong",
                )
            if value.subtitle:
                y += 4 + self.text(
                    value.subtitle,
                    inner_x,
                    y + 4,
                    inner_w,
                    16,
                    path + "/subtitle",
                    "secondary",
                )
            if value.blocks:
                if value.title or value.subtitle:
                    y += 14
                y += self.stack(value.blocks, inner_x, y, inner_w, path + "/blocks")
            y += inset
            if value.panel:
                self.shapes.insert(
                    start_shape,
                    _Shape(
                        (x, top, x + width, y), p.panel, self.tokens.radius, p.border
                    ),
                )
        elif type(value) is Row:
            positions = self.columns(
                value.weights or (1,) * len(value.cells), x, width, self.tokens.gap
            )
            cells = []
            for index, (block, (left, cell_width)) in enumerate(
                zip(value.cells, positions)
            ):
                start = self.mark()
                height = self.block(block, left, y, cell_width, f"{path}/cells/{index}")
                cells.append((height, start, self.mark()))
            height = max(cell[0] for cell in cells)
            for h, start, end in cells:
                self.shift(
                    start,
                    end,
                    (height - h) * {"top": 0, "center": 0.5, "bottom": 1}[value.valign],
                )
            y += height
        elif type(value) is PropertyGrid:
            positions = self.columns((1,) * value.columns, x, width, 16)
            for offset in range(0, len(value.properties), value.columns):
                heights = []
                for index, prop in enumerate(
                    value.properties[offset : offset + value.columns]
                ):
                    left, cell_width = positions[index]
                    cell_path = f"{path}/properties/{offset + index}"
                    height = self.text(
                        prop.label,
                        left,
                        y,
                        cell_width,
                        16,
                        cell_path + "/label",
                        "secondary",
                    )
                    height += 2 + self.text(
                        prop.value,
                        left,
                        y + height + 2,
                        cell_width,
                        24,
                        cell_path + "/value",
                        "strong",
                    )
                    heights.append(height)
                y += max(heights)
                if offset + value.columns < len(value.properties):
                    y += 16
        elif type(value) is Table:
            positions = self.columns(
                tuple(column.weight for column in value.columns), x, width, 0
            )
            for row_index, row in enumerate(
                (tuple(c.title for c in value.columns), *value.rows)
            ):
                row_top, entries = y, []
                for index, (cell, column, (left, cell_width)) in enumerate(
                    zip(row, value.columns, positions)
                ):
                    align, valign = column.align, column.valign
                    if type(cell) in (TextCell, Cell):
                        align, valign, cell = cell.align, cell.valign, cell.content
                    start = self.mark()
                    height = self.text(
                        cell,
                        left + 12,
                        y + self.tokens.row_padding,
                        cell_width - 24,
                        24 if row_index == 0 or self.document.layout != "table" else 28,
                        f"{path}/rows/{row_index}/{index}",
                        "secondary" if row_index == 0 else "normal",
                        align,
                        "wrap" if row_index == 0 else column.wrap,
                    )
                    entries.append((height, valign, start, self.mark()))
                content_height = max(entry[0] for entry in entries)
                for height, valign, start, end in entries:
                    self.shift(
                        start,
                        end,
                        (content_height - height)
                        * {"top": 0, "center": 0.5, "bottom": 1}[valign],
                    )
                y += content_height + self.tokens.row_padding * 2
                self.shapes.append(
                    _Shape(
                        (x, row_top, x + width, y),
                        p.panel_alt if row_index == 0 else p.panel,
                    )
                )
                self.shapes.append(_Shape((x, y - 0.5, x + width, y), p.border))
                self.boxes.append(
                    LayoutBox(
                        f"{path}/rows/{row_index}",
                        "table_row",
                        (x, row_top, x + width, y),
                    )
                )
        elif type(value) is Image:
            image_width = (
                value.width if value.width is not None else min(width, value.height)
            )
            if image_width > width:
                _fail("layout_overflow")
            left = (
                x
                + (width - image_width)
                * {"left": 0, "center": 0.5, "right": 1}[value.align]
            )
            self.images.append(
                _Image(
                    value.asset,
                    (left, y, left + image_width, y + value.height),
                    value.fit,
                )
            )
            y += value.height
            if value.caption:
                y += 8 + self.text(
                    value.caption,
                    x,
                    y + 8,
                    width,
                    16,
                    path + "/caption",
                    "secondary",
                    value.align,
                )
        self.boxes.append(
            LayoutBox(path, type(value).__name__.lower(), (x, top, x + width, y))
        )
        if y > self.bounds.max_height:
            _fail("layout_overflow")
        return y - top

    def build(self):
        d, p, t = self.document, self.palette, self.tokens
        x, width, y = t.margin, d.width - t.margin * 2, 52
        header_width = width - 112 if d.logo is not None else width
        if d.logo is not None:
            self.images.append(
                _Image(d.logo, (d.width - t.margin - 88, y, d.width - t.margin, y + 88))
            )
            self.boxes.append(LayoutBox("header/logo", "image", self.images[-1].box))
        if d.brand:
            y += (
                self.text(d.brand, x, y, header_width, 16, "header/brand", "emphasis")
                + 7
            )
        y += self.text(d.title, x, y, header_width, 40, "header/title", "strong")
        if d.subtitle:
            y += 8 + self.text(
                d.subtitle, x, y + 8, header_width, 24, "header/subtitle", "secondary"
            )
        y = max(y, 140 if d.logo is not None else y) + 22
        self.shapes.append(_Shape((x, y, x + 58, y + 3), p.accent, 1))
        self.shapes.append(_Shape((x + 70, y + 1, x + width, y + 2), p.border))
        y += 25
        y += self.stack(d.blocks, x, y, width, "blocks")
        y += 26
        self.shapes.append(_Shape((x, y, x + width, y + 1), p.border))
        y += 16
        for index, source in enumerate(d.sources):
            y += (
                self.text(
                    source, x, y, width, 16, f"footer/sources/{index}", "secondary"
                )
                + 4
            )
        if d.powered_by:
            y += (
                self.text(
                    d.powered_by,
                    x,
                    y + 4,
                    width,
                    16,
                    "footer/powered_by",
                    "secondary",
                    "left",
                )
                + 4
            )
        height = math.ceil(y + 40)
        pixels = d.width * height
        if (
            height > self.bounds.max_height
            or pixels > self.bounds.max_pixels
            or max(36 * pixels, 4 * pixels + 2_162_688)
            > self.bounds.max_rich_work_bytes
        ):
            _fail("layout_overflow")
        return MeasuredDocument(d.width, height, tuple(self.boxes))


def _prepare(document, bounds, font_provider):
    palette, strings = _validate(document, bounds)
    provider = font_provider if font_provider is not None else BundledFontProvider()
    try:
        for value in strings:
            for char in value:
                if char != "\n" and provider.supports(ord(char)) is not True:
                    _fail("glyph_unavailable")
    except BitmapUnavailable:
        raise
    except Exception:
        _fail("font_unavailable")
    layout = _Layout(document, palette, provider, bounds)
    try:
        return layout, layout.build()
    except BaseException:
        layout.fonts.clear()
        raise


def measure_document(
    document: Document,
    *,
    bounds: BitmapBounds = BitmapBounds(),
    font_provider: FontProvider | None = None,
) -> MeasuredDocument:
    """Measure without raster allocation. Container bounds include their children."""
    layout, measured = _prepare(document, bounds, font_provider)
    layout.fonts.clear()
    return measured


def _paint_text(layout, session):
    p = layout.palette
    roles = {
        "normal": p.text,
        "secondary": p.secondary,
        "strong": p.title,
        "emphasis": p.accent,
        "gold": "#e5cc80",
    }
    for text in layout.texts:
        y, font = text.y, layout.fonts[text.size]
        for line in text.lines:
            left_bearing = min(0, line.box[0]) / 2
            line_width = (
                max(line.box[2], math.ceil(line.advance)) - min(0, line.box[0])
            ) / 2
            x = (
                text.x
                - left_bearing
                + (text.width - line_width)
                * {"left": 0, "center": 0.5, "right": 1}[text.align]
            )
            ink_height = (line.box[3] - line.box[1]) / 2
            baseline = y + (line.height - ink_height) / 2 - line.box[1] / 2
            advance = 0
            for span in line.spans:
                color = span.color or roles[span.emphasis]
                palette = TextPalette(color, color, color, color)
                source = (
                    TextSpan(span.text, TextRole.GRADED, Grade.GOLD)
                    if span.emphasis == "gold"
                    else TextSpan(span.text)
                )
                run = _measure_run(font, span.text, layout.measure)
                if text.align == "right" and len(line.spans) == 1:
                    # Existing production alignment uses actual glyph mask ink,
                    # including negative bearings and trailing whitespace.
                    info = session.add(
                        source,
                        font=font,
                        size=text.size,
                        palette=palette,
                        right=text.x + text.width,
                        center=y + line.height / 2,
                    )
                else:
                    info = session.add(
                        source,
                        font=font,
                        size=text.size,
                        palette=palette,
                        left=x + (round(advance) + run.box[0]) / 2,
                        top=baseline + run.box[1] / 2,
                    )
                if info.mask_bounds is not None and (
                    info.bounds[0] < text.x - 0.51
                    or info.bounds[2] > text.x + text.width + 0.51
                    or info.bounds[1] < y - 0.51
                    or info.bounds[3] > y + line.height + 0.51
                ):
                    _fail("layout_overflow")
                advance += run.advance
            y += line.height
    session.paint()


def render_document(
    document: Document,
    *,
    bounds: BitmapBounds = BitmapBounds(),
    font_provider: FontProvider | None = None,
) -> RenderedBitmap:
    """Render a complete bounded PNG or raise BitmapUnavailable; never truncate.

    Optional image decode failure becomes a visible, measured alt-text fallback;
    required images fail the whole render. Neither path performs dispatch.
    """
    from .card_bitmap import _BoundedBuffer
    from .layout_assets import rasterize_asset

    try:
        from PIL import Image as PillowImage
        from PIL import ImageDraw
    except Exception:
        _fail("dependency_missing")
    layout, measured = _prepare(document, bounds, font_provider)
    session = None
    try:
        p = layout.palette
        with closing(
            PillowImage.new("RGB", (measured.width * 2, measured.height * 2), p.outside)
        ) as image:
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle(
                (36, 36, measured.width * 2 - 36, measured.height * 2 - 36),
                radius=44,
                fill=p.surface,
                outline=p.border,
                width=2,
            )
            for shape in layout.shapes:
                draw.rounded_rectangle(
                    tuple(round(v * 2) for v in shape.box),
                    radius=shape.radius * 2,
                    fill=shape.fill,
                    outline=shape.outline,
                    width=2,
                )
            for item in layout.images:
                x0, y0, x1, y1 = item.box
                try:
                    with closing(
                        rasterize_asset(
                            item.asset,
                            round(x1 - x0) * 2,
                            round(y1 - y0) * 2,
                            p.accent,
                            fit=item.fit,
                        )
                    ) as asset:
                        image.paste(asset, (round(x0 * 2), round(y0 * 2)), asset)
                except BitmapUnavailable:
                    if item.asset.required:
                        raise
                    draw.rounded_rectangle(
                        tuple(round(v * 2) for v in item.box),
                        radius=12,
                        fill=p.panel_alt,
                        outline=p.border,
                        width=2,
                    )
                    alt = item.asset.alt or "Image unavailable"
                    start = len(layout.texts)
                    h = layout.text(
                        alt,
                        x0 + 10,
                        y0 + 10,
                        x1 - x0 - 20,
                        16,
                        "asset/fallback",
                        "secondary",
                        "center",
                    )
                    if h > y1 - y0 - 20:
                        _fail("layout_overflow")
                    layout.texts[start].y += (y1 - y0 - 20 - h) / 2
            draw = None
            session = RichSession(
                image,
                max_work_bytes=bounds.max_rich_work_bytes,
                max_mask_pixels=bounds.max_rich_mask_pixels,
                max_measurements=bounds.max_measurements,
                measure=layout.measure,
            )
            _paint_text(layout, session)
            with closing(
                image.resize(
                    (measured.width, measured.height), PillowImage.Resampling.LANCZOS
                )
            ) as final:
                image.close()
                with _BoundedBuffer(bounds.max_bytes) as buffer:
                    final.save(buffer, format="PNG", optimize=False, compress_level=9)
                    content = buffer.getvalue()
        return RenderedBitmap(content, measured.width, measured.height)
    finally:
        if session is not None:
            session.close()
        layout.fonts.clear()
