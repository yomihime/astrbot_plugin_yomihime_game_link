"""Bounded, offline rich-text styling reference, not a Core or SDK API.

The caller chooses semantic roles/grades; this file has no business-score mapping.
B is a conditional centered shadow; A is the selected gold-only decorative glow.
Both use the original glyph mask. The exact flat face is always composited last.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from renderer import SCALE, Canvas, FontConfig, Palette

MAX_SPAN_CHARS = 512
MAX_LINE_CHARS = 1024
MAX_SPANS = 32
MAX_FONT_SIZE = 96
MAX_PATCH_PIXELS = 1_000_000  # Actual 2x raster pixels, before any allocation.
MAX_PATCH_SIDE = 4096
MAX_LINE_MASK_PIXELS = 2_000_000
CONTRAST_THRESHOLD = 1.5  # Trigger heuristic, not accessibility certification.
SHADOW_BLUR_AT_35 = 1.0
SHADOW_OPACITY = 235
GLOW_LAYERS = ((1.2, 0.85), (3.8, 0.98), (7.2, 0.46))
GLOW_DARK_RGB = (238, 186, 61)
GLOW_DARK_CAPS = (160, 125, 65)
GLOW_LIGHT_COLOR = "#ebc361"
GLOW_LIGHT_OPACITY_CAP = 155
GLOW_LIGHT_POWER = 0.75
ERROR_COLOR = "#c83e4d"  # Reference choice, separate from the grade scale.
BUNDLED_FONT = (
    Path(__file__).resolve().parents[2]
    / "presentation/assets/fonts/NotoSansCJKsc-Regular.otf"
)


class Grade(str, Enum):
    GRAY = "gray"
    GREEN = "green"
    BLUE = "blue"
    PURPLE = "purple"
    ORANGE = "orange"
    PINK = "pink"
    GOLD = "gold"


GRADE_COLORS = {
    Grade.GRAY: "#666666",
    Grade.GREEN: "#1eff00",
    Grade.BLUE: "#0070ff",
    Grade.PURPLE: "#a335ee",
    Grade.ORANGE: "#ff8000",
    Grade.PINK: "#e268a8",
    Grade.GOLD: "#e5cc80",
}


class TextRole(str, Enum):
    NORMAL = "normal"
    SECONDARY = "secondary"
    STRONG = "strong"
    EMPHASIS = "emphasis"
    GRADED = "graded"
    ERROR = "error"


@dataclass(frozen=True)
class TextSpan:
    """GOLD asserts that the caller has evidence for the evaluation maximum.

    This renderer cannot establish that evidence. Non-percentile business values
    require the module's own explicit comparison scope and mapping. No HTML,
    resource paths, arbitrary colors, or drawing commands are accepted here.
    """

    text: str
    role: TextRole = TextRole.NORMAL
    grade: Grade | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not 0 < len(self.text) <= MAX_SPAN_CHARS:
            raise ValueError("Span text must contain 1..512 characters")
        if any(ord(char) < 32 or char in "\x7f\u2028\u2029" for char in self.text):
            raise ValueError("A rich-text span must be a single printable line")
        if not isinstance(self.role, TextRole):
            raise ValueError("Unknown text role")
        if self.role is TextRole.GRADED:
            if not isinstance(self.grade, Grade):
                raise ValueError("Graded text requires an explicit grade")
        elif self.grade is not None:
            raise ValueError("Only graded text may carry a grade")


@dataclass(frozen=True)
class DrawInfo:
    text: str
    face: str
    background: str
    contrast: float
    shadow: bool
    shade: str | None
    gold_glow: bool
    bounds: tuple[float, float, float, float]
    patch_bounds: tuple[int, int, int, int]
    mask_bounds: tuple[int, int, int, int] | None
    effect_bounds: tuple[int, int, int, int] | None


def reference_fonts(
    font: Path | None = None, bold_font: Path | None = None, font_index: int = 0
) -> FontConfig:
    """Reuse the repository font, or explicit local paths; never scan/download.

    The bundled asset has only a regular face. Bold requests reuse that face
    unless an explicit bold file is supplied. No synthetic thickening is used.
    """
    regular = Path(font) if font is not None else BUNDLED_FONT
    bold = Path(bold_font) if bold_font is not None else regular
    if type(font_index) is not int or not 0 <= font_index < 10:
        raise ValueError("Font face index must be 0..9")
    for path in (regular, bold):
        if path.suffix.lower() not in {".otf", ".ttf", ".ttc"} or not path.is_file():
            raise ValueError("An existing local OTF/TTF/TTC font is required")
    return FontConfig(regular, bold, font_index)


def rgb(color: str) -> tuple[int, int, int]:
    if len(color) != 7 or color[0] != "#":
        raise ValueError("Expected #rrggbb")
    return tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))


def luminance(color: str) -> float:
    channels = [value / 255 for value in rgb(color)]
    linear = [
        v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels
    ]
    return sum(a * b for a, b in zip(linear, (0.2126, 0.7152, 0.0722)))


def contrast_ratio(first: str, second: str) -> float:
    low, high = sorted((luminance(first), luminance(second)))
    return (high + 0.05) / (low + 0.05)


def needs_shadow(ratio: float) -> bool:
    return ratio < CONTRAST_THRESHOLD


def shadow_color(face: str, background: str) -> str:
    """Selected B prototype recipe: midpoint with the inverted actual surface.

    This retains the selected prototype's tint, not its superseded expanded
    outline geometry. Channel rounding uses Python's round (ties to even).
    """
    values = [round((a + 255 - b) / 2) for a, b in zip(rgb(face), rgb(background))]
    return "#" + "".join(f"{value:02x}" for value in values)


def face_color(span: TextSpan, palette: Palette) -> str:
    if span.role is TextRole.GRADED:
        return GRADE_COLORS[span.grade]
    return {
        TextRole.NORMAL: palette.text,
        TextRole.SECONDARY: palette.secondary,
        TextRole.STRONG: palette.title,
        TextRole.EMPHASIS: palette.accent,
        TextRole.ERROR: ERROR_COLOR,
    }[span.role]


def shadow_mask(mask: Image.Image, size: int) -> Image.Image:
    """B: original mask, zero offset/expansion/stroke, radius proportional to size."""
    radius = SHADOW_BLUR_AT_35 * size / 35 * SCALE
    return mask.filter(ImageFilter.GaussianBlur(radius)).point(
        lambda value: round(value * SHADOW_OPACITY / 255)
    )


def _validate_size(size: int) -> None:
    if type(size) is not int or not 8 <= size <= MAX_FONT_SIZE:
        raise ValueError("Reference font size must be 8..96")


def _gold_glow(patch: Image.Image, mask: Image.Image, size: int, dark: bool):
    original = patch.copy()
    extent = Image.new("L", mask.size)
    for radius, power in GLOW_LAYERS:
        alpha = mask.filter(ImageFilter.GaussianBlur(radius * size / 35 * SCALE))
        if dark:
            alpha = alpha.point(lambda value, p=power: min(255, round(value * p)))
            light = Image.merge(
                "RGB",
                tuple(
                    alpha.point(lambda value, c=c: round(value * c / 255))
                    for c in GLOW_DARK_RGB
                ),
            )
            patch = ImageChops.screen(patch, light)
        else:
            alpha = alpha.point(
                lambda value, p=power: min(
                    GLOW_LIGHT_OPACITY_CAP,
                    round(value * p * GLOW_LIGHT_POWER)
                )
            )
            patch = Image.composite(
                Image.new("RGB", mask.size, GLOW_LIGHT_COLOR),
                patch,
                alpha
            )
        extent = ImageChops.lighter(extent, alpha)
    if dark:
        radiance = ImageChops.subtract(patch, original)
        radiance = Image.merge(
            "RGB",
            tuple(
                channel.point(lambda value, cap=cap: min(value, cap))
                for channel, cap in zip(radiance.split(), GLOW_DARK_CAPS)
            ),
        )
        patch = ImageChops.add(original, radiance)
    return patch, extent


@dataclass(frozen=True)
class _PreparedSpan:
    info: DrawInfo
    mask: Image.Image
    size: int


def _prepare_span(
    canvas: Canvas,
    span: TextSpan,
    *,
    left: float,
    top: float,
    size: int,
    palette: Palette,
    bold: bool,
    right: float | None = None,
    center: float | None = None,
    remaining_pixels: int = MAX_PATCH_PIXELS,
) -> _PreparedSpan:
    _validate_size(size)
    coordinates = (left, top) if right is None else (right, center)
    if not all(math.isfinite(value) for value in coordinates):
        raise ValueError("Non-finite placement")
    font = canvas.font(size, bold)
    box = font.getbbox(span.text)
    width, height = box[2] - box[0], box[3] - box[1]
    pad = math.ceil(27 * size / 35 * SCALE)
    dimensions = (width + pad * 2, height + pad * 2)
    if max(dimensions) > MAX_PATCH_SIDE or dimensions[0] * dimensions[1] > min(
        MAX_PATCH_PIXELS,
        remaining_pixels
    ):
        raise ValueError("Text effect exceeds the bounded local raster budget")
    mask = Image.new("L", dimensions)
    ImageDraw.Draw(mask).text(
        (pad - box[0], pad - box[1]),
        span.text,
        font=font,
        fill=255
    )
    ink_bounds = mask.getbbox()
    if right is not None and ink_bounds is not None:
        # Font layout boxes contain side bearings (especially the em dash).
        # Align actual nonzero glyph pixels rather than an advance/layout box.
        x = round(right * SCALE - ink_bounds[2])
        y = round(center * SCALE - (ink_bounds[1] + ink_bounds[3]) / 2)
    else:
        x, y = round(left * SCALE) - pad, round(top * SCALE) - pad
    patch_bounds = (x, y, x + dimensions[0], y + dimensions[1])
    if (
        x < 0
        or y < 0
        or patch_bounds[2] > canvas.im.width
        or patch_bounds[3] > canvas.im.height
    ):
        raise ValueError("Text/effect would be clipped; reflow or use text fallback")
    patch = canvas.im.crop(patch_bounds).convert("RGB")
    face = face_color(span, palette)
    # Sample BEFORE effects: chips and row fills, never an assumed page color.
    # All runs in a line are prepared before any effects or faces are painted.
    surface = patch.convert("RGBA")
    surface.putalpha(mask.point(lambda value: 255 if value else 0))
    colors = surface.getcolors(MAX_PATCH_PIXELS)
    actual = {"#%02x%02x%02x" % color[:3] for _, color in colors if color[3]}
    background = min(
        actual or {face.lower()},
        key=lambda color: (contrast_ratio(face, color), color)
    )
    ratio = contrast_ratio(face, background)
    shadow = bool(ink_bounds) and needs_shadow(ratio)
    gold = bool(ink_bounds) and span.grade is Grade.GOLD
    extent = mask.copy()
    if gold:
        # Only the blur's alpha extent is needed for geometry diagnostics.
        for radius, power in GLOW_LAYERS:
            layer = mask.filter(ImageFilter.GaussianBlur(radius * size / 35 * SCALE))
            layer = layer.point(lambda value, p=power: min(255, round(value * p)))
            extent = ImageChops.lighter(extent, layer)
    if shadow:
        extent = ImageChops.lighter(extent, shadow_mask(mask, size))
    bounds = ink_bounds or (pad, pad, pad + width, pad + height)
    info = DrawInfo(
        span.text, face, background, ratio, shadow,
        shadow_color(face, background) if shadow else None, gold,
        tuple((origin + edge) / SCALE for origin, edge in zip((x, y, x, y), bounds)),
        patch_bounds, ink_bounds, extent.getbbox(),
    )
    return _PreparedSpan(info, mask, size)


def _paint_effects(canvas: Canvas, prepared: _PreparedSpan) -> None:
    info, mask, size = prepared.info, prepared.mask, prepared.size
    if not info.gold_glow and not info.shadow:
        return
    patch = canvas.im.crop(info.patch_bounds).convert("RGB")
    if info.gold_glow:
        patch, _ = _gold_glow(patch, mask, size, luminance(info.background) < 0.179)
    if info.shadow:
        patch = Image.composite(
            Image.new("RGB", mask.size, info.shade),
            patch,
            shadow_mask(mask, size)
        )
    canvas.im.paste(patch, info.patch_bounds[:2])


def _paint_face(canvas: Canvas, prepared: _PreparedSpan) -> None:
    info = prepared.info
    canvas.im.paste(rgb(info.face), info.patch_bounds, prepared.mask)
    canvas.drawn_text.append(info.text)


def draw_metric(
    canvas: Canvas,
    span: TextSpan,
    *,
    right: float,
    center: float,
    size: int,
    palette: Palette,
    bold: bool = True,
) -> DrawInfo:
    """Align nonzero glyph-mask bounds right/center, including a missing dash."""
    prepared = _prepare_span(
        canvas,
        span,
        left=0,
        top=0,
        right=right,
        center=center,
        size=size,
        palette=palette,
        bold=bold
    )
    _paint_effects(canvas, prepared)
    _paint_face(canvas, prepared)
    canvas.d = ImageDraw.Draw(canvas.im)
    return prepared.info


def draw_rich_line(
    canvas: Canvas,
    spans: Sequence[TextSpan],
    *,
    xy: tuple[float, float],
    size: int,
    palette: Palette,
    bold: bool = False,
) -> tuple[DrawInfo, ...]:
    """Draw bounded runs: original-surface sampling, all effects, then all faces.

    xy is the left baseline in logical output pixels. Roles choose color only;
    the caller chooses font weight explicitly. The reference is single-line;
    the module must reflow or fall back before sending if bounds are exceeded.
    No partial line is painted if validation/aggregate mask budgets fail.
    """
    _validate_size(size)
    if not 0 < len(spans) <= MAX_SPANS or sum(
        len(span.text) for span in spans
    ) > MAX_LINE_CHARS:
        raise ValueError("Rich line exceeds span/character budget")
    x, baseline = xy
    font = canvas.font(size, bold)
    prepared = []
    remaining = MAX_LINE_MASK_PIXELS
    for span in spans:
        box = font.getbbox(span.text, anchor="ls")
        item = _prepare_span(
            canvas,
            span,
            left=x + box[0] / SCALE,
            top=baseline + box[1] / SCALE,
            size=size,
            palette=palette,
            bold=bold,
            remaining_pixels=remaining
        )
        remaining -= item.mask.width * item.mask.height
        prepared.append(item)
        x += font.getlength(span.text) / SCALE
    for item in prepared:
        _paint_effects(canvas, item)
    for item in prepared:
        _paint_face(canvas, item)  # ALL crisp faces last, even beside gold glow.
    canvas.d = ImageDraw.Draw(canvas.im)
    return tuple(item.info for item in prepared)
