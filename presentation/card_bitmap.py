"""Internal, bounded PNG cards; no Host, asset, business or font discovery API.

FontProvider is a trusted injection point, not a font parser. Its coverage claim
must have been reviewed against the exact font/face returned by ``load``. This
module checks every visible codepoint against that claim; it cannot establish
the truth of an arbitrary provider's claim. No production provider is supplied.
"""

from dataclasses import dataclass, fields
from io import BytesIO
from typing import Literal, Protocol
from unicodedata import category

BitmapReason = Literal[
    "dependency_missing",
    "font_unavailable",
    "glyph_unavailable",
    "layout_overflow",
    "byte_limit",
    "invalid_input",
]


class BitmapUnavailable(Exception):
    """Stable, non-sensitive failure; underlying exceptions are never included."""

    def __init__(self, reason: BitmapReason):
        self.reason = (
            reason
            if reason
            in (
                "dependency_missing",
                "font_unavailable",
                "glyph_unavailable",
                "layout_overflow",
                "byte_limit",
                "invalid_input",
            )
            else "invalid_input"
        )
        super().__init__(self.reason)


class FontProvider(Protocol):
    """Trusted code only: exact-face loader and independently reviewed coverage.

    ``supports`` must return a bool for a Unicode codepoint; getmask/nonempty
    pixels are not evidence of coverage. ``load`` returns a Pillow font at the
    requested size. Font paths/URLs are deliberately absent from card inputs.
    """

    def supports(self, codepoint: int) -> bool: ...

    def load(self, size: int) -> object: ...


@dataclass(frozen=True)
class Row:
    cells: tuple[str, ...]


@dataclass(frozen=True)
class Section:
    heading: str = ""
    paragraphs: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()
    rows: tuple[Row, ...] = ()


@dataclass(frozen=True)
class CardSpec:
    title: str
    sections: tuple[Section, ...] = ()
    subtitle: str = ""
    sources: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    width: int = 960
    palette: Literal["warm_paper", "soft_lilac", "cool_mint"] = "warm_paper"


@dataclass(frozen=True)
class BitmapBounds:
    """Callers may lower these hard caps, never increase them.

    These are pure drawing budgets, not a platform's sending capabilities.
    Input lines count each string's explicit newline-separated lines, including
    empty strings. Output lines count wrapped lines in every cell separately.
    Measurements count every font bounding-box call, including empty lines.
    """

    max_width: int = 2048
    max_height: int = 8192
    max_pixels: int = 8_000_000
    max_bytes: int = 1_048_576
    max_chars: int = 24_000
    max_string_chars: int = 4096
    max_input_lines: int = 1000
    max_output_lines: int = 1600
    max_sections: int = 32
    max_rows: int = 200
    max_columns: int = 8
    max_measurements: int = 60_000


@dataclass(frozen=True)
class RenderedBitmap:
    content: bytes
    width: int
    height: int
    media_type: str = "image/png"


_PALETTES = {
    "warm_paper": ("#f3e8de", "#fff9f1", "#5b4033", "#84573e", "#f6e9dd"),
    "soft_lilac": ("#ece8f3", "#fcf9ff", "#493754", "#73528a", "#efe7f6"),
    "cool_mint": ("#eaf5f0", "#f9fffc", "#354d45", "#537e6e", "#d6e9df"),
}


def _validate(spec: CardSpec, bounds: BitmapBounds) -> tuple[str, ...]:
    if type(spec) is not CardSpec or type(bounds) is not BitmapBounds:
        raise BitmapUnavailable("invalid_input")
    hard = BitmapBounds()
    for field in fields(bounds):
        value = getattr(bounds, field.name)
        if type(value) is not int or not 1 <= value <= getattr(hard, field.name):
            raise BitmapUnavailable("invalid_input")
    if (
        type(spec.width) is not int
        or spec.width < 160
        or type(spec.palette) is not str
        or spec.palette not in _PALETTES
    ):
        raise BitmapUnavailable("invalid_input")
    if spec.width > bounds.max_width:
        raise BitmapUnavailable("layout_overflow")
    strings: list[str] = []
    total = lines = 0

    def add(value: str) -> None:
        nonlocal total, lines
        if type(value) is not str:
            raise BitmapUnavailable("invalid_input")
        total += len(value)
        lines += value.count("\n") + 1
        if (
            len(value) > bounds.max_string_chars
            or total > bounds.max_chars
            or lines > bounds.max_input_lines
        ):
            raise BitmapUnavailable("layout_overflow")
        if any(char != "\n" and category(char).startswith("C") for char in value):
            raise BitmapUnavailable("invalid_input")
        strings.append(value)

    add(spec.title)
    add(spec.subtitle)
    for sequence in (spec.sections, spec.sources, spec.limitations):
        if type(sequence) is not tuple:
            raise BitmapUnavailable("invalid_input")
    if len(spec.sections) > bounds.max_sections:
        raise BitmapUnavailable("layout_overflow")
    row_count = 0
    for section in spec.sections:
        if type(section) is not Section:
            raise BitmapUnavailable("invalid_input")
        for sequence in (section.paragraphs, section.columns, section.rows):
            if type(sequence) is not tuple:
                raise BitmapUnavailable("invalid_input")
        if len(section.columns) > bounds.max_columns:
            raise BitmapUnavailable("layout_overflow")
        add(section.heading)
        for value in section.paragraphs:
            add(value)
        for value in section.columns:
            add(value)
        row_count += len(section.rows)
        if row_count > bounds.max_rows:
            raise BitmapUnavailable("layout_overflow")
        for row in section.rows:
            if (
                type(row) is not Row
                or type(row.cells) is not tuple
                or not section.columns
                or len(row.cells) != len(section.columns)
            ):
                raise BitmapUnavailable("invalid_input")
            for value in row.cells:
                add(value)
    for value in spec.limitations:
        add(value)
    for value in spec.sources:
        add(value)
    return tuple(strings)


@dataclass(frozen=True)
class _Line:
    text: str
    bbox: tuple[int, int, int, int]
    height: int


@dataclass(frozen=True)
class _Text:
    x: int
    y: int
    size: int
    lines: tuple[_Line, ...]
    accent: bool


@dataclass(frozen=True)
class _Panel:
    top: int
    bottom: int


class _Layout:
    def __init__(self, provider: FontProvider, bounds: BitmapBounds):
        self.bounds = bounds
        self.measurements = self.output_lines = 0
        self.texts: list[_Text] = []
        self.panels: list[_Panel] = []
        try:
            self.fonts = {size: provider.load(size) for size in (28, 22, 20, 16)}
        except Exception:
            raise BitmapUnavailable("font_unavailable") from None

    def measure(self, text: str, size: int) -> tuple[int, int, int, int]:
        self.measurements += 1
        if self.measurements > self.bounds.max_measurements:
            raise BitmapUnavailable("layout_overflow")
        try:
            box = self.fonts[size].getbbox(text)
            if (
                len(box) != 4
                or any(type(n) is not int or abs(n) > 1_000_000 for n in box)
                or box[2] < box[0]
                or box[3] < box[1]
            ):
                raise ValueError
            return tuple(box)
        except Exception:
            raise BitmapUnavailable("font_unavailable") from None

    def wrap(self, text: str, size: int, width: int) -> tuple[_Line, ...]:
        if width <= 0:
            raise BitmapUnavailable("layout_overflow")
        lines = []

        def emit(value: str, box: tuple[int, int, int, int]) -> None:
            self.output_lines += 1
            if self.output_lines > self.bounds.max_output_lines:
                raise BitmapUnavailable("layout_overflow")
            lines.append(_Line(value, box, max(size + 8, box[3] - box[1] + 8)))

        # Keep all characters and explicit blank lines, including trailing ones.
        # Character wrapping also handles unbroken English words and numbers.
        for explicit_line in text.split("\n"):
            current = ""
            box = self.measure("", size)
            for char in explicit_line:
                candidate = current + char
                candidate_box = self.measure(candidate, size)
                if candidate_box[2] - candidate_box[0] > width:
                    if not current:
                        raise BitmapUnavailable("layout_overflow")
                    emit(current, box)
                    current = char
                    box = self.measure(char, size)
                    if box[2] - box[0] > width:
                        raise BitmapUnavailable("layout_overflow")
                else:
                    current, box = candidate, candidate_box
            emit(current, box)
        return tuple(lines)

    def text(
        self, value: str, x: int, y: int, width: int, size: int, accent: bool = False
    ) -> int:
        lines = self.wrap(value, size, width)
        self.texts.append(_Text(x, y, size, lines, accent))
        return sum(line.height for line in lines)

    def build(self, spec: CardSpec) -> int:
        width = spec.width - 80
        y = 36 + self.text(spec.title, 40, 36, width, 28, True)
        if spec.subtitle:
            y += 6 + self.text(spec.subtitle, 40, y + 6, width, 20)
        y += 20
        for section in spec.sections:
            if section.heading:
                y += self.text(section.heading, 40, y, width, 22, True) + 8
            for paragraph in section.paragraphs:
                y += self.text(paragraph, 40, y, width, 20) + 8
            if section.columns:
                count = len(section.columns)
                # Integer boundaries distribute remainder without clipping.
                edges = [40 + width * i // count for i in range(count + 1)]
                for cells in (section.columns, *(row.cells for row in section.rows)):
                    top = y
                    height = 0
                    for index, cell in enumerate(cells):
                        height = max(
                            height,
                            self.text(
                                cell,
                                edges[index] + 8,
                                y + 8,
                                edges[index + 1] - edges[index] - 16,
                                20,
                            ),
                        )
                    y += height + 16
                    self.panels.append(_Panel(top, y))
            y += 16
        for value in (*spec.limitations, *spec.sources):
            y += self.text(value, 40, y, width, 16) + 6
        height = y + 30
        if (
            height > self.bounds.max_height
            or spec.width * height > self.bounds.max_pixels
        ):
            raise BitmapUnavailable("layout_overflow")
        return height


class _BoundedBuffer(BytesIO):
    def __init__(self, limit: int):
        super().__init__()
        self.limit = limit

    def write(self, data: bytes) -> int:
        if self.tell() + len(data) > self.limit:
            raise BitmapUnavailable("byte_limit")
        return super().write(data)


def render_card(
    spec: CardSpec,
    *,
    font_provider: FontProvider,
    bounds: BitmapBounds = BitmapBounds(),
) -> RenderedBitmap:
    """Render all supplied text or fail before returning any image.

    No files, metadata, random choices, font substitution, ellipses or sends.
    Dependency and trusted font failures are safe codes, never exception text.
    """
    strings = _validate(spec, bounds)
    try:
        from PIL import Image, ImageDraw
    except Exception:
        raise BitmapUnavailable("dependency_missing") from None
    try:
        for value in strings:
            for char in value:
                if char != "\n" and font_provider.supports(ord(char)) is not True:
                    raise BitmapUnavailable("glyph_unavailable")
    except BitmapUnavailable:
        raise
    except Exception:
        raise BitmapUnavailable("font_unavailable") from None
    layout = _Layout(font_provider, bounds)
    height = layout.build(spec)
    background, paper, ink, accent, panel = _PALETTES[spec.palette]
    # All line heights, width, height and pixels have been checked before new().
    try:
        with Image.new("RGB", (spec.width, height), background) as image:
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle(
                (16, 16, spec.width - 17, height - 17), radius=22, fill=paper
            )
            for box in layout.panels:
                draw.rounded_rectangle(
                    (40, box.top, spec.width - 40, box.bottom - 2),
                    radius=8,
                    fill=panel,
                )
            for text in layout.texts:
                y = text.y
                for line in text.lines:
                    if line.text:
                        left, top, _, _ = line.bbox
                        draw.text(
                            (text.x - left, y - top),
                            line.text,
                            font=layout.fonts[text.size],
                            fill=accent if text.accent else ink,
                        )
                    y += line.height
            with _BoundedBuffer(bounds.max_bytes) as buffer:
                image.save(buffer, format="PNG", optimize=False, compress_level=9)
                content = buffer.getvalue()
    except BitmapUnavailable:
        raise
    except Exception:
        raise BitmapUnavailable("font_unavailable") from None
    return RenderedBitmap(content, spec.width, height)
