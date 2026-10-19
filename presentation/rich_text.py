"""Internal bounded rich text. No business grading, paths, or eager Pillow IO.

Sessions sample the original surface, then paint ALL effects before ANY faces.
Only small placement records survive preparation; masks belong to one run and
are closed on success, failure and cancellation. Budgets model owned rasters,
not Python/FreeType heap, codec internals, or process RSS.
"""

from __future__ import annotations

import math
from contextlib import ExitStack as _ExitStack
from contextlib import closing
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Sequence
from unicodedata import category

SCALE = 2
MAX_SPAN_CHARS = 512
MAX_LINE_CHARS = 1024
MAX_SPANS = 32
MAX_PATCH_PIXELS = 1_000_000
MAX_PATCH_SIDE = 4096
MAX_LINE_MASK_PIXELS = 2_000_000
MAX_WORK_BYTES = 128 * 1024 * 1024
MAX_MASK_PIXELS = 64_000_000
CONTRAST_THRESHOLD = 1.5
SHADOW_OPACITY = 235
GLOW_LAYERS = ((1.2, 0.85), (3.8, 0.98), (7.2, 0.46))
GLOW_DARK_RGB = (238, 186, 61)
GLOW_DARK_CAPS = (160, 125, 65)
GLOW_LIGHT_COLOR = "#ebc361"
GLOW_LIGHT_OPACITY_CAP = 155
GLOW_LIGHT_POWER = 0.75
ERROR_COLOR = "#c83e4d"
BUFFER_RADIUS = 0.25  # Final pixels; half a mask pixel at production 2x.
BUFFER_OPACITY = 0.15
BUFFER_FEATHER = 0.08  # Final pixels.


class ExitStack(_ExitStack):
    """Own raster cores explicitly: Pillow Image.__exit__ does not close them."""

    def enter_context(self, image):
        self.callback(image.close)
        return image


def _fail(reason="layout_overflow"):
    from .card_bitmap import BitmapUnavailable

    raise BitmapUnavailable(reason)


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
    """GOLD is the module's evidence assertion, never inferred from a number."""

    text: str
    role: TextRole = TextRole.NORMAL
    grade: Grade | None = None
    explicit_color: bool = False

    def __post_init__(self):
        if (
            type(self.text) is not str
            or not 0 < len(self.text) <= MAX_SPAN_CHARS
            or any(
                category(c).startswith("C") or c in "\u2028\u2029" for c in self.text
            )
            or type(self.explicit_color) is not bool
            or not isinstance(self.role, TextRole)
            or (
                not isinstance(self.grade, Grade)
                if self.role is TextRole.GRADED
                else self.grade is not None
            )
        ):
            raise ValueError("Invalid bounded text span")


@dataclass(frozen=True)
class RichText:
    spans: tuple[TextSpan, ...]


@dataclass(frozen=True)
class TextCell:
    content: str | RichText
    align: str = "left"
    valign: str = "top"


@dataclass(frozen=True)
class TextPalette:
    text: str
    secondary: str
    title: str
    accent: str


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
    buffer: bool = False


def rgb(color: str) -> tuple[int, int, int]:
    if type(color) is not str or len(color) != 7 or color[0] != "#":
        raise ValueError("Expected #rrggbb")
    return tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))


def luminance(color: str) -> float:
    values = (v / 255 for v in rgb(color))
    return sum(
        (v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4) * weight
        for v, weight in zip(values, (0.2126, 0.7152, 0.0722))
    )


def contrast_ratio(first: str, second: str) -> float:
    low, high = sorted((luminance(first), luminance(second)))
    return (high + 0.05) / (low + 0.05)


def needs_shadow(ratio: float) -> bool:
    return ratio < CONTRAST_THRESHOLD


def shadow_color(face: str, background: str) -> str:
    return "#" + "".join(
        f"{round((a + 255 - b) / 2):02x}" for a, b in zip(rgb(face), rgb(background))
    )


def face_color(span: TextSpan, palette: TextPalette) -> str:
    if span.role is TextRole.GRADED:
        return GRADE_COLORS[span.grade]
    return {
        TextRole.NORMAL: palette.text,
        TextRole.SECONDARY: palette.secondary,
        TextRole.STRONG: palette.title,
        TextRole.EMPHASIS: palette.accent,
        TextRole.ERROR: ERROR_COLOR,
    }[span.role]


def shadow_mask(mask, size: int):
    """Original glyph Gaussian, without expansion, offset or stroke.

    Standalone diagnostic helper; sessions account for both intermediate masks.
    The returned image belongs to the caller.
    """
    from PIL import ImageFilter

    with closing(mask.filter(ImageFilter.GaussianBlur(size / 35 * SCALE))) as blurred:
        return blurred.point(lambda value: round(value * SHADOW_OPACITY / 255))


@dataclass(frozen=True)
class _Plan:
    info: DrawInfo
    font: object
    size: int
    box: tuple[int, int, int, int]
    pad: int


@dataclass(frozen=True)
class _Geometry:
    span: TextSpan
    font: object
    size: int
    box: tuple[int, int, int, int]
    pad: int
    dimensions: tuple[int, int]


@dataclass(frozen=True)
class _RunMetrics:
    box: tuple[int, int, int, int]
    advance: float


def _measure_run(font, text, measure):
    """Match the independently shaped run used by the painter, at 2x scale."""
    box = measure(font, "getbbox", text, anchor="ls")
    advance = measure(font, "getlength", text)
    if (
        len(box) != 4
        or any(type(v) is not int or abs(v) > 1_000_000 for v in box)
        or box[2] < box[0]
        or box[3] < box[1]
        or type(advance) not in (int, float)
        or not math.isfinite(advance)
        or not 0 <= advance <= 1_000_000
    ):
        _fail("font_unavailable")
    return _RunMetrics(tuple(box), advance)


@dataclass(frozen=True)
class _LineMetrics:
    box: tuple[int, int, int, int] | None = None
    advance: float = 0

    def append(self, run):
        # The mask painter rounds each run's origin, not the joined string's
        # advance. Keep the same raster extents, including negative bearings.
        x = round(self.advance)
        box = (x + run.box[0], run.box[1], x + run.box[2], run.box[3])
        if self.box is not None:
            box = (
                min(self.box[0], box[0]),
                min(self.box[1], box[1]),
                max(self.box[2], box[2]),
                max(self.box[3], box[3]),
            )
        return _LineMetrics(box, self.advance + run.advance)

    @property
    def width(self):
        return max(self.box[2], math.ceil(self.advance)) - min(0, self.box[0])


_UNMEASURED = object()


class RichSession:
    """One card/session, never reset between helper calls.

    RGB surfaces are charged at four bytes/pixel. Work admission reserves a
    32-byte/pixel local patch pool and a full-size resize intermediate. Mask
    construction AND scan work share a cumulative 64M-pixel ceiling. This is
    an owned-raster model, not a C extension allocator or RSS sandbox.
    """

    def __init__(
        self,
        image,
        *,
        max_work_bytes=MAX_WORK_BYTES,
        max_mask_pixels=MAX_MASK_PIXELS,
        max_measurements=60_000,
        measure: Callable | None = None,
    ):
        if (
            image.mode != "RGB"
            or image.width % SCALE
            or image.height % SCALE
            or image.width > 2048 * SCALE
            or image.height > 8192 * SCALE
            or image.width * image.height > 8_000_000 * SCALE * SCALE
        ):
            _fail()
        for value, hard in (
            (max_work_bytes, MAX_WORK_BYTES),
            (max_mask_pixels, MAX_MASK_PIXELS),
            (max_measurements, 60_000),
        ):
            if type(value) is not int or not 1 <= value <= hard:
                _fail("invalid_input")
        self.image = image
        self.max_work_bytes = max_work_bytes
        self.max_mask_pixels = max_mask_pixels
        self.max_measurements = max_measurements
        self.measurements = self.mask_pixels = 0
        self.input_chars = 0
        self.measure_callback = measure
        self.plans: list[_Plan] = []
        self.painted = False
        self.closed = False
        self.finished = 0
        self.peak_patch_pixels = 0
        self._check_work(0)

    def _check_work(self, patch_pixels):
        n = self.image.width * self.image.height // 4
        required = max(16 * n + 32 * patch_pixels, 36 * n, 4 * n + 2_097_152 + 65_536)
        if required > self.max_work_bytes:
            _fail()
        self.peak_patch_pixels = max(self.peak_patch_pixels, patch_pixels)

    def _charge(self, pixels):
        self.mask_pixels += pixels
        if self.mask_pixels > self.max_mask_pixels:
            _fail()

    def _measure(self, font, method, text, **kwargs):
        self.measurements += 1
        if self.measurements > self.max_measurements:
            _fail()
        try:
            if self.measure_callback is not None:
                return self.measure_callback(font, method, text, **kwargs)
            return getattr(font, method)(text, **kwargs)
        except Exception as error:
            from .card_bitmap import BitmapUnavailable

            if isinstance(error, BitmapUnavailable):
                raise
            _fail("font_unavailable")

    def _mask(self, plan, stack):
        from PIL import Image, ImageDraw

        x0, y0, x1, y1 = plan.info.patch_bounds
        self._charge((x1 - x0) * (y1 - y0))
        mask = stack.enter_context(Image.new("L", (x1 - x0, y1 - y0)))
        ImageDraw.Draw(mask).text(
            (plan.pad - plan.box[0], plan.pad - plan.box[1]),
            plan.info.text,
            font=plan.font,
            fill=255,
        )
        return mask

    def _shadow(self, mask, size, stack):
        self._charge(mask.width * mask.height * 2)
        return stack.enter_context(shadow_mask(mask, size))

    def _buffer(self, mask, stack):
        """Approved outer-only A mask; reserve all seven patch rasters first.

        Interpolate radius 0 and 1 at 2x instead of rounding 0.25 final px
        up to one mask pixel. Every temporary has explicit exception-safe
        ownership. The existing 32-byte/patch-pixel live-work allowance covers
        these eight L masks (including the borrowed glyph) without a canvas.
        """
        from PIL import Image, ImageChops, ImageFilter

        self._charge(mask.width * mask.height * 7)
        high = stack.enter_context(mask.filter(ImageFilter.MaxFilter(3)))
        blended = stack.enter_context(Image.blend(mask, high, BUFFER_RADIUS * SCALE))
        high.close()
        grown = stack.enter_context(
            blended.filter(ImageFilter.GaussianBlur(BUFFER_FEATHER * SCALE))
        )
        blended.close()
        outer = stack.enter_context(ImageChops.subtract(grown, mask))
        grown.close()
        inverse = stack.enter_context(ImageChops.invert(mask))
        weighted = stack.enter_context(ImageChops.multiply(outer, inverse))
        outer.close()
        inverse.close()
        alpha = stack.enter_context(weighted.point(lambda a: round(a * BUFFER_OPACITY)))
        weighted.close()
        return alpha

    def _geometry(self, span, font, size):
        """Finite measurements only; no mask, crop, copy or pixel scan."""
        if self.painted or self.closed:
            _fail("invalid_input")
        if type(span) is not TextSpan or type(size) is not int or not 8 <= size <= 96:
            _fail("invalid_input")
        box = self._measure(font, "getbbox", span.text)
        if (
            len(box) != 4
            or any(type(v) is not int or abs(v) > 1_000_000 for v in box)
            or box[2] < box[0]
            or box[3] < box[1]
        ):
            _fail("font_unavailable")
        pad = math.ceil(27 * size / 35 * SCALE)
        dimensions = (box[2] - box[0] + 2 * pad, box[3] - box[1] + 2 * pad)
        pixels = dimensions[0] * dimensions[1]
        if max(dimensions) > MAX_PATCH_SIDE or pixels > MAX_PATCH_PIXELS:
            _fail()
        self._check_work(pixels)
        return _Geometry(span, font, size, tuple(box), pad, dimensions)

    @staticmethod
    def _coordinates(values):
        if not all(type(v) in (int, float) and math.isfinite(v) for v in values):
            _fail("invalid_input")

    @staticmethod
    def _mask_plan(geometry, face=""):
        dummy = DrawInfo(
            geometry.span.text,
            face,
            face,
            1,
            False,
            None,
            False,
            (),
            (0, 0, *geometry.dimensions),
            None,
            None,
        )
        return _Plan(dummy, geometry.font, geometry.size, geometry.box, geometry.pad)

    def add(
        self,
        span,
        *,
        font,
        size,
        palette,
        left=0,
        top=0,
        right=None,
        center=None,
    ):
        """Prepare only. Actual background is sampled before painting starts."""
        self._coordinates((left, top) if right is None else (right, center))
        geometry = self._geometry(span, font, size)
        return self._add_geometry(geometry, palette, left, top, right, center)

    def _add_geometry(
        self,
        geometry,
        palette,
        left,
        top,
        right=None,
        center=None,
        known_ink=_UNMEASURED,
    ):
        span, font, size = geometry.span, geometry.font, geometry.size
        box, pad, dimensions = geometry.box, geometry.pad, geometry.dimensions
        pixels = dimensions[0] * dimensions[1]
        self.input_chars += len(span.text)
        if self.input_chars > 24_000:
            _fail()
        face = face_color(span, palette)
        rgb(face)
        plan = self._mask_plan(geometry, face)
        with ExitStack() as stack:
            mask = None
            ink = known_ink
            if ink is _UNMEASURED:
                mask = self._mask(plan, stack)
                self._charge(pixels)
                ink = mask.getbbox()
            if ink is None:
                # Whitespace still has logical advance/layout, but no visible
                # ink/effect exists to crop or clip. Never use default zero
                # coordinates for a right/center no-ink run.
                advance = self._measure(font, "getlength", span.text) / SCALE
                logical_left = left if right is None else right - advance
                logical_top = top if center is None else center
                info = DrawInfo(
                    span.text,
                    face,
                    face.lower(),
                    1,
                    False,
                    None,
                    False,
                    (logical_left, logical_top, logical_left + advance, logical_top),
                    (0, 0, 0, 0),
                    None,
                    None,
                )
                self.plans.append(_Plan(info, font, size, tuple(box), pad))
                return info
            if mask is None:
                mask = self._mask(plan, stack)
            if right is not None:
                x = round(right * SCALE - ink[2])
                y = round(center * SCALE - (ink[1] + ink[3]) / 2)
            else:
                x, y = round(left * SCALE) - pad, round(top * SCALE) - pad
            bounds = (x, y, x + dimensions[0], y + dimensions[1])
            if (
                x < 0
                or y < 0
                or bounds[2] > self.image.width
                or bounds[3] > self.image.height
            ):
                _fail()
            # Reserve before allocating or scanning the corresponding raster.
            self._charge(pixels)
            patch = stack.enter_context(self.image.crop(bounds))
            actual = None
            best = None
            # Streaming scan: no per-pixel Python container or unbounded color set.
            cached = {}
            for color, alpha in zip(patch.getdata(), mask.getdata()):
                if not alpha:
                    continue
                ratio = cached.get(color)
                if ratio is None:
                    ratio = contrast_ratio(face, "#%02x%02x%02x" % color)
                    if len(cached) < 256:
                        cached[color] = ratio
                candidate = (ratio, color)
                if best is None or candidate < best:
                    best, actual = candidate, color
            background = (
                "#%02x%02x%02x" % actual if actual is not None else face.lower()
            )
            ratio = contrast_ratio(face, background)
            shadow = ink is not None and needs_shadow(ratio)
            gold = ink is not None and span.grade is Grade.GOLD
            self._charge(pixels)
            extent = stack.enter_context(mask.copy())
            from PIL import ImageChops, ImageFilter

            if gold:
                for radius, power in GLOW_LAYERS:
                    self._charge(pixels * 3)
                    with closing(
                        mask.filter(
                            ImageFilter.GaussianBlur(radius * size / 35 * SCALE)
                        )
                    ) as blurred:
                        with closing(
                            blurred.point(lambda v, p=power: min(255, round(v * p)))
                        ) as alpha:
                            replacement = ImageChops.lighter(extent, alpha)
                    extent.close()
                    extent = stack.enter_context(replacement)
            if shadow:
                self._charge(pixels)
                with ExitStack() as local:
                    alpha = self._shadow(mask, size, local)
                    replacement = ImageChops.lighter(extent, alpha)
                extent.close()
                extent = stack.enter_context(replacement)
            buffered = span.explicit_color or span.role is TextRole.GRADED
            if buffered:
                self._charge(pixels)
                with ExitStack() as local:
                    alpha = self._buffer(mask, local)
                    replacement = ImageChops.lighter(extent, alpha)
                extent.close()
                extent = stack.enter_context(replacement)
            self._charge(pixels)
            effect_bounds = extent.getbbox()
            edges = ink
            info = DrawInfo(
                span.text,
                face,
                background,
                ratio,
                shadow,
                shadow_color(face, background) if shadow else None,
                gold,
                tuple(
                    (origin + edge) / SCALE for origin, edge in zip((x, y, x, y), edges)
                ),
                bounds,
                ink,
                effect_bounds,
                buffered,
            )
        self.plans.append(_Plan(info, font, size, tuple(box), pad))
        return info

    def add_line(
        self,
        spans: Sequence[TextSpan],
        *,
        xy,
        font,
        size,
        palette,
        max_line_mask_pixels=MAX_LINE_MASK_PIXELS,
        right=None,
        center=None,
    ):
        if (
            type(max_line_mask_pixels) is not int
            or not 1 <= max_line_mask_pixels <= MAX_LINE_MASK_PIXELS
        ):
            _fail("invalid_input")
        if (
            not 0 < len(spans) <= MAX_SPANS
            or any(type(span) is not TextSpan for span in spans)
            or sum(len(span.text) for span in spans) > MAX_LINE_CHARS
        ):
            _fail()
        # Admit the whole line against the shared input quota before geometry
        # or alignment masks. Per-add checks below still count each span once.
        if self.input_chars + sum(len(span.text) for span in spans) > 24_000:
            _fail()
        start = len(self.plans)
        x, baseline = xy
        self._coordinates((x, baseline))
        if right is not None:
            self._coordinates((right,))
        if center is not None:
            self._coordinates((center,))
        pixels = 0
        advance = 0
        placements = []
        try:
            # The entire line's finite geometry and aggregate admission precede
            # even its first glyph mask. A lowered cap cannot permit work first.
            for span in spans:
                geometry = self._geometry(span, font, size)
                run = _measure_run(font, span.text, self._measure)
                placements.append(
                    (
                        geometry,
                        x + (round(advance) + run.box[0]) / SCALE,
                        baseline + run.box[1] / SCALE,
                    )
                )
                pixels += geometry.dimensions[0] * geometry.dimensions[1]
                if pixels > max_line_mask_pixels:
                    _fail()
                advance += run.advance
            ink_bounds = [_UNMEASURED] * len(placements)
            shift_x = shift_y = 0
            if right is not None or center is not None:
                union = None
                # Sequential masks keep at most one run raster alive. Retain
                # only actual ink bounds, then sample final positioned surfaces.
                for index, (geometry, left, top) in enumerate(placements):
                    plan = self._mask_plan(geometry)
                    with ExitStack() as stack:
                        mask = self._mask(plan, stack)
                        self._charge(geometry.dimensions[0] * geometry.dimensions[1])
                        ink = mask.getbbox()
                    ink_bounds[index] = ink
                    if ink is not None:
                        px = round(left * SCALE) - geometry.pad
                        py = round(top * SCALE) - geometry.pad
                        bounds = (px + ink[0], py + ink[1], px + ink[2], py + ink[3])
                        union = (
                            bounds
                            if union is None
                            else (
                                min(union[0], bounds[0]),
                                min(union[1], bounds[1]),
                                max(union[2], bounds[2]),
                                max(union[3], bounds[3]),
                            )
                        )
                if union is not None:
                    if right is not None:
                        shift_x = round(right * SCALE - union[2])
                    if center is not None:
                        shift_y = round(center * SCALE - (union[1] + union[3]) / 2)
                else:
                    # No glyphs: retain the full line's measured advances.
                    if right is not None:
                        shift_x = round((right - x) * SCALE - advance)
                    if center is not None:
                        shift_y = round((center - baseline) * SCALE)
            for (geometry, left, top), ink in zip(placements, ink_bounds):
                self._add_geometry(
                    geometry,
                    palette,
                    left + shift_x / SCALE,
                    top + shift_y / SCALE,
                    known_ink=ink,
                )
        except BaseException:
            del self.plans[start:]
            raise
        return tuple(plan.info for plan in self.plans[start:])

    def _gold(self, patch, mask, plan):
        from PIL import Image, ImageChops, ImageFilter

        pixels = mask.width * mask.height
        dark = luminance(plan.info.background) < 0.179
        with ExitStack() as ownership:
            self._charge(pixels)
            original = ownership.enter_context(patch.copy())
            for radius, power in GLOW_LAYERS:
                with ExitStack() as stack:
                    self._charge(pixels * 2)
                    blurred = stack.enter_context(
                        mask.filter(
                            ImageFilter.GaussianBlur(radius * plan.size / 35 * SCALE)
                        )
                    )
                    alpha = stack.enter_context(
                        blurred.point(
                            (lambda v, p=power: min(255, round(v * p)))
                            if dark
                            else (
                                lambda v, p=power: min(
                                    GLOW_LIGHT_OPACITY_CAP,
                                    round(v * p * GLOW_LIGHT_POWER),
                                )
                            )
                        )
                    )
                    blurred.close()
                    if dark:
                        self._charge(pixels * 3)
                        channels = []
                        for color in GLOW_DARK_RGB:
                            channels.append(
                                stack.enter_context(
                                    alpha.point(lambda v, c=color: round(v * c / 255))
                                )
                            )
                        light = stack.enter_context(Image.merge("RGB", channels))
                        for channel in channels:
                            channel.close()
                        result = ImageChops.screen(patch, light)
                    else:
                        light = stack.enter_context(
                            Image.new("RGB", mask.size, GLOW_LIGHT_COLOR)
                        )
                        result = Image.composite(light, patch, alpha)
                ownership.enter_context(result)
                patch.close()
                patch = result
            if dark:
                with ExitStack() as stack:
                    radiance = stack.enter_context(ImageChops.subtract(patch, original))
                    patch.close()
                    self._charge(pixels * 6)
                    channels = list(radiance.split())
                    for channel in channels:
                        stack.enter_context(channel)
                    capped = []
                    for channel, cap in zip(channels, GLOW_DARK_CAPS):
                        capped.append(
                            stack.enter_context(
                                channel.point(lambda v, c=cap: min(v, c))
                            )
                        )
                        channel.close()
                    light = stack.enter_context(Image.merge("RGB", capped))
                    radiance.close()
                    for channel in capped:
                        channel.close()
                    patch = ownership.enter_context(ImageChops.add(original, light))
            # The caller receives one owned result. Even interruption while
            # creating it closes every intermediate registered above.
            self._charge(pixels)
            return patch.copy()

    def paint(self):
        """Once for an entire card, or incrementally for a standalone adapter.

        Incremental batches share cumulative budgets. Card adapters MUST queue
        all text first and call this only after all backgrounds are complete.
        """
        from PIL import Image

        if self.closed:
            _fail("invalid_input")
        self.painted = True
        plans = self.plans[self.finished :]
        for plan in plans:
            info = plan.info
            if not info.gold_glow and not info.shadow:
                continue
            with ExitStack() as stack:
                mask = self._mask(plan, stack)
                self._charge(mask.width * mask.height)
                patch = self.image.crop(info.patch_bounds)
                try:
                    if info.gold_glow:
                        patch = self._gold(patch, mask, plan)
                    if info.shadow:
                        with ExitStack() as local:
                            alpha = self._shadow(mask, plan.size, local)
                            color = local.enter_context(
                                Image.new("RGB", mask.size, info.shade)
                            )
                            result = Image.composite(color, patch, alpha)
                        patch.close()
                        patch = result
                    self.image.paste(patch, info.patch_bounds[:2])
                finally:
                    patch.close()
        for plan in plans:
            if not plan.info.buffer:
                continue
            with ExitStack() as stack:
                mask = self._mask(plan, stack)
                alpha = self._buffer(mask, stack)
                shade = max(
                    ("#fafafa", "#202127"),
                    key=lambda color: contrast_ratio(plan.info.face, color),
                )
                self.image.paste(rgb(shade), plan.info.patch_bounds, alpha)
        for plan in plans:
            if plan.info.mask_bounds is None:
                continue
            with ExitStack() as stack:
                mask = self._mask(plan, stack)
                self.image.paste(rgb(plan.info.face), plan.info.patch_bounds, mask)
        self.finished = len(self.plans)
        return tuple(plan.info for plan in plans)

    def close(self):
        """Release plans/font references. The borrowed canvas belongs to caller."""
        self.closed = True
        self.plans.clear()
        self.measure_callback = None
        self.image = None
