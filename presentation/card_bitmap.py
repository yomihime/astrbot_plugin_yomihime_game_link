"""Internal, bounded PNG cards; no Host, asset, business or font discovery API.

FontProvider is a trusted injection point, not a font parser. Its coverage claim
must have been reviewed against the exact font/face returned by ``load``. This
module checks every visible codepoint against that claim; it cannot establish
the truth of an arbitrary provider's claim. ``bundled_font`` supplies a separate
explicit provider for one pinned, reviewed face; no provider is selected here.
"""

from contextlib import closing
from dataclasses import dataclass, fields
from io import BytesIO
from typing import Literal, Protocol
from unicodedata import category

from .rich_text import RichText, TextCell, TextSpan

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
    cells: tuple[str | RichText | TextCell, ...]


@dataclass(frozen=True)
class Section:
    heading: str | RichText = ""
    paragraphs: tuple[str | RichText, ...] = ()
    columns: tuple[str | RichText, ...] = ()
    rows: tuple[Row, ...] = ()


@dataclass(frozen=True)
class CardSpec:
    title: str | RichText
    sections: tuple[Section, ...] = ()
    subtitle: str | RichText = ""
    sources: tuple[str | RichText, ...] = ()
    limitations: tuple[str | RichText, ...] = ()
    width: int = 960
    palette: Literal[
        "warm_paper", "soft_lilac", "cool_mint", "cool_white", "deep_indigo"
    ] = "warm_paper"


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
    max_rich_work_bytes: int = 128 * 1024 * 1024
    max_rich_mask_pixels: int = 64_000_000
    max_rich_font_bytes: int = 224 * 1024 * 1024


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
    "cool_white": ("#e8edf5", "#f8fafc", "#4c5a69", "#7e94ab", "#edf1f5"),
    "deep_indigo": ("#0f1423", "#1c2337", "#d0d8e7", "#8a9fd2", "#222c42"),
}

# Rich-only owned-byte model; this is not a font hash or a process RSS cap.
_MAX_RICH_FACE_BYTES = 16 * 1024 * 1024
_MAX_RICH_FONT_STORAGE = 14 * _MAX_RICH_FACE_BYTES


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
        if type(value) is RichText:
            if (
                type(value.spans) is not tuple
                or not 0 < len(value.spans) <= 32
                or any(type(span) is not TextSpan for span in value.spans)
            ):
                raise BitmapUnavailable("invalid_input")
            value = "".join(span.text for span in value.spans)
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
                if type(value) is TextCell:
                    if value.align not in ("left", "right") or value.valign not in (
                        "top",
                        "center",
                    ):
                        raise BitmapUnavailable("invalid_input")
                    value = value.content
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


@dataclass(frozen=True)
class _RichLine:
    spans: tuple[TextSpan, ...]
    height: int
    box: tuple[int, int, int, int]


@dataclass
class _RichPlacement:
    x: float
    y: float
    width: int
    size: int
    lines: tuple[_RichLine, ...]
    align: str = "left"
    center: float | None = None


def _has_rich(spec):
    return any(
        type(v) is not str
        for v in (
            spec.title,
            spec.subtitle,
            *(section.heading for section in spec.sections),
            *(v for section in spec.sections for v in section.paragraphs),
            *(v for section in spec.sections for v in section.columns),
            *(
                v
                for section in spec.sections
                for row in section.rows
                for v in row.cells
            ),
            *spec.sources,
            *spec.limitations,
        )
    )


class _RichLayout:
    """Four derived faces, no retained legacy FontSet or repeated loads.

    The explicit bytes stream forbids font_variant's path fallback. At most
    one borrowed base and four derived faces are directly held during construction.
    A provider may retain four cached base faces/streams. The storage model counts
    all four touched borrowed font_bytes/streams, four derived byte stores, the
    transient variant stream and one provider snapshot allowance (14 stores).
    Only our derived streams are closed; provider caches remain reusable. This
    does not bound an opaque provider's unrelated caches or C extension RSS.
    """

    def __init__(self, provider, bounds):
        from PIL import ImageFont

        self.bounds = bounds
        self.measurements = self.output_lines = 0
        self.fonts = {}
        self.texts = []
        self.panels = []
        base = None
        borrowed_bytes = 0
        try:
            for size in (28, 22, 20, 16):
                base = provider.load(size)
                if (
                    type(base) is not ImageFont.FreeTypeFont
                    or type(getattr(base, "font_bytes", None)) is not bytes
                    or not 0 < len(base.font_bytes) <= _MAX_RICH_FACE_BYTES
                    or type(base.path) is not BytesIO
                    or type(base.index) is not int
                    or not 0 <= base.index < 10
                ):
                    raise ValueError
                with base.path.getbuffer() as view:
                    if view.nbytes != len(base.font_bytes):
                        raise ValueError
                # Snapshot allowance + ALL touched borrowed bytes/streams +
                # existing derived bytes + current base and new stream/bytes.
                # Caching providers invalidate the earlier eight-store estimate.
                # Reject BEFORE making the new copies, without closing inputs.
                owned = (
                    _MAX_RICH_FACE_BYTES
                    + borrowed_bytes
                    + sum(len(face.font_bytes) for face in self.fonts.values())
                    + 4 * len(base.font_bytes)
                )
                if owned > min(_MAX_RICH_FONT_STORAGE, bounds.max_rich_font_bytes):
                    raise BitmapUnavailable("layout_overflow")
                with BytesIO(base.font_bytes) as stream:
                    derived = base.font_variant(
                        font=stream, size=size * 2, index=base.index
                    )
                if derived.font_bytes != base.font_bytes or derived.index != base.index:
                    raise ValueError
                self.fonts[size] = derived
                borrowed_bytes += 2 * len(base.font_bytes)
                base = None
        except BitmapUnavailable:
            self.fonts.clear()
            raise
        except Exception:
            self.fonts.clear()
            raise BitmapUnavailable("font_unavailable") from None
        except BaseException:
            self.fonts.clear()
            raise
        finally:
            # The provider may cache its returned face and source stream. Both
            # are borrowed; only streams constructed here may be closed here.
            base = None
            derived = None

    def measure(self, font, method, text, **kwargs):
        self.measurements += 1
        if self.measurements > self.bounds.max_measurements:
            raise BitmapUnavailable("layout_overflow")
        try:
            value = getattr(font, method)(text, **kwargs)
            if method == "getbbox":
                if (
                    len(value) != 4
                    or any(type(n) is not int or abs(n) > 1_000_000 for n in value)
                    or value[2] < value[0]
                    or value[3] < value[1]
                ):
                    raise ValueError
            elif type(value) not in (int, float) or not 0 <= value <= 1_000_000:
                raise ValueError
            return value
        except BitmapUnavailable:
            raise
        except Exception:
            raise BitmapUnavailable("font_unavailable") from None

    def wrap(self, value, size, width, default_role=None):
        from .rich_text import TextRole, _LineMetrics, _measure_run

        if width <= 0:
            raise BitmapUnavailable("layout_overflow")
        tokens = (
            [(c, default_role or TextRole.NORMAL, None) for c in value]
            if type(value) is str
            else [(c, span.role, span.grade) for span in value.spans for c in span.text]
        )
        lines = []
        runs = []
        # One prefix per completed run. Extending the tail remeasures only that
        # run, so wrapping remains two font calls per candidate, not per span.
        prefixes = [_LineMetrics()]
        chars = 0
        font = self.fonts[size]

        def emit():
            self.output_lines += 1
            if self.output_lines > self.bounds.max_output_lines:
                raise BitmapUnavailable("layout_overflow")
            box = prefixes[-1].box
            if box is None:
                box = self.measure(font, "getbbox", "", anchor="ls")
            lines.append(
                _RichLine(
                    tuple(runs), max(size + 8, (box[3] - box[1] + 1) // 2 + 8), box
                )
            )

        def candidate(char, role, grade):
            extend = (
                runs
                and runs[-1].role is role
                and runs[-1].grade is grade
                and len(runs[-1].text) < 512
            )
            span = TextSpan(runs[-1].text + char if extend else char, role, grade)
            prefix = prefixes[-2] if extend else prefixes[-1]
            metrics = prefix.append(_measure_run(font, span.text, self.measure))
            return span, metrics, extend

        for char, role, grade in tokens:
            if char == "\n":
                emit()
                runs, prefixes, chars = [], [_LineMetrics()], 0
                continue
            span, metrics, extend = candidate(char, role, grade)
            if metrics.width > width * 2 or chars >= 1024:
                if not runs:
                    raise BitmapUnavailable("layout_overflow")
                emit()
                runs, prefixes, chars = [], [_LineMetrics()], 0
                span, metrics, extend = candidate(char, role, grade)
                if metrics.width > width * 2:
                    raise BitmapUnavailable("layout_overflow")
            if extend:
                runs[-1], prefixes[-1] = span, metrics
            else:
                runs.append(span)
                prefixes.append(metrics)
                if len(runs) > 32:
                    raise BitmapUnavailable("layout_overflow")
            chars += 1
        emit()
        return tuple(lines)

    def text(self, value, x, y, width, size, accent=False):
        from .rich_text import TextRole

        align = "left"
        if type(value) is TextCell:
            align = value.align
            value = value.content
        lines = self.wrap(
            value, size, width, TextRole.EMPHASIS if accent else TextRole.NORMAL
        )
        self.texts.append(_RichPlacement(x, y, width, size, lines, align))
        return sum(line.height for line in lines)

    def build(self, spec):
        # Same content order and fixed four-size hierarchy as the legacy path.
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
                edges = [40 + width * i // count for i in range(count + 1)]
                for cells in (section.columns, *(row.cells for row in section.rows)):
                    top, start = y, len(self.texts)
                    heights = [
                        self.text(
                            cell, edges[i] + 8, y + 8, edges[i + 1] - edges[i] - 16, 20
                        )
                        for i, cell in enumerate(cells)
                    ]
                    height = max(heights)
                    for cell, text, cell_height in zip(
                        cells, self.texts[start:], heights
                    ):
                        if type(cell) is TextCell and cell.valign == "center":
                            text.y += (height - cell_height) / 2
                            if len(text.lines) == 1:
                                text.center = top + 8 + height / 2
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
        # Check before the 2x canvas is allocated. Local patches are checked by
        # the session as each measured patch size becomes known.
        if (
            max(36 * spec.width * height, 4 * spec.width * height + 2_162_688)
            > self.bounds.max_rich_work_bytes
        ):
            raise BitmapUnavailable("layout_overflow")
        return height


def _render_rich(spec, provider, bounds, Image, ImageDraw):
    from .rich_text import RichSession, TextPalette

    layout = _RichLayout(provider, bounds)
    session = None
    draw = None
    try:
        height = layout.build(spec)
        outside, paper, ink, accent, panel = _PALETTES[spec.palette]
        palette = TextPalette(ink, ink, accent, accent)
        with closing(Image.new("RGB", (spec.width * 2, height * 2), outside)) as image:
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle(
                (32, 32, spec.width * 2 - 34, height * 2 - 34), radius=44, fill=paper
            )
            for box in layout.panels:
                draw.rounded_rectangle(
                    (80, box.top * 2, spec.width * 2 - 80, (box.bottom - 2) * 2),
                    radius=16,
                    fill=panel,
                )
            # ImageDraw retains its own ImagingCore reference. Background work
            # is complete; release our draw before resizing/closing the canvas.
            draw = None
            session = RichSession(
                image,
                max_work_bytes=bounds.max_rich_work_bytes,
                max_mask_pixels=bounds.max_rich_mask_pixels,
                max_measurements=bounds.max_measurements,
                measure=layout.measure,
            )
            for text in layout.texts:
                y = text.y
                font = layout.fonts[text.size]
                for line in text.lines:
                    if line.spans:
                        if text.align == "right" and len(line.spans) == 1:
                            infos = (
                                session.add(
                                    line.spans[0],
                                    font=font,
                                    size=text.size,
                                    palette=palette,
                                    right=text.x + text.width,
                                    center=text.center
                                    if text.center is not None
                                    else y + (line.box[3] - line.box[1]) / 4,
                                ),
                            )
                        else:
                            x = text.x
                            baseline_box = line.box
                            baseline = y - baseline_box[1] / 2
                            if text.center is not None:
                                baseline = (
                                    text.center
                                    - (baseline_box[1] + baseline_box[3]) / 4
                                )
                            infos = session.add_line(
                                line.spans,
                                xy=(x - baseline_box[0] / 2, baseline),
                                font=font,
                                size=text.size,
                                palette=palette,
                                right=text.x + text.width
                                if text.align == "right"
                                else None,
                                center=(
                                    text.center
                                    if text.center is not None
                                    else y + (line.box[3] - line.box[1]) / 4
                                )
                                if text.align == "right"
                                else None,
                            )
                        top = (
                            text.center - line.height / 2
                            if text.center is not None
                            else y
                        )
                        # Validate actual glyph ink before any faces/effects are
                        # painted. Never crop a run to conceal a layout mismatch.
                        if any(
                            info.mask_bounds is not None
                            and (
                                info.bounds[0] < text.x
                                or info.bounds[2] > text.x + text.width
                                or info.bounds[1] < top
                                or info.bounds[3] > top + line.height
                            )
                            for info in infos
                        ):
                            raise BitmapUnavailable("layout_overflow")
                    y += line.height
            session.paint()
            with closing(
                image.resize((spec.width, height), Image.Resampling.LANCZOS)
            ) as final:
                image.close()
                with _BoundedBuffer(bounds.max_bytes) as buffer:
                    final.save(buffer, format="PNG", optimize=False, compress_level=9)
                    content = buffer.getvalue()
        return RenderedBitmap(content, spec.width, height)
    finally:
        draw = None
        if session is not None:
            session.close()
        layout.fonts.clear()


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
    if _has_rich(spec):
        try:
            return _render_rich(spec, font_provider, bounds, Image, ImageDraw)
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
