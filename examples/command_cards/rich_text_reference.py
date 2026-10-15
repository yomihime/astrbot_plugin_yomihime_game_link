"""Offline Canvas adapter for the production rich-text engine.

No rendering algorithm lives here. FF14 mappings remain in its own example.
The standalone command loads only presentation's inert package, never AstrBot.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from PIL import Image as Image  # Shared module permits allocation probes.
from PIL import ImageDraw
from renderer import FontConfig

_PRESENTATION = Path(__file__).resolve().parents[2] / "presentation"
_PACKAGE = "_ygl_card_presentation"
if _PACKAGE not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        _PACKAGE,
        _PRESENTATION / "__init__.py",
        submodule_search_locations=[str(_PRESENTATION)],
    )
    _package = importlib.util.module_from_spec(_spec)
    sys.modules[_PACKAGE] = _package
    _spec.loader.exec_module(_package)
_rich = __import__(_PACKAGE + ".rich_text", fromlist=["rich_text"])
_bitmap = __import__(_PACKAGE + ".card_bitmap", fromlist=["card_bitmap"])

Grade = _rich.Grade
TextRole = _rich.TextRole
TextSpan = _rich.TextSpan
DrawInfo = _rich.DrawInfo
GRADE_COLORS = _rich.GRADE_COLORS
MAX_SPAN_CHARS = _rich.MAX_SPAN_CHARS
MAX_LINE_CHARS = _rich.MAX_LINE_CHARS
MAX_SPANS = _rich.MAX_SPANS
MAX_FONT_SIZE = _rich.MAX_FONT_SIZE
MAX_PATCH_PIXELS = _rich.MAX_PATCH_PIXELS
MAX_PATCH_SIDE = _rich.MAX_PATCH_SIDE
MAX_LINE_MASK_PIXELS = _rich.MAX_LINE_MASK_PIXELS
CONTRAST_THRESHOLD = _rich.CONTRAST_THRESHOLD
ERROR_COLOR = _rich.ERROR_COLOR
rgb = _rich.rgb
luminance = _rich.luminance
contrast_ratio = _rich.contrast_ratio
needs_shadow = _rich.needs_shadow
shadow_color = _rich.shadow_color
face_color = _rich.face_color
shadow_mask = _rich.shadow_mask
BUNDLED_FONT = _PRESENTATION / "assets/fonts/NotoSansCJKsc-Regular.otf"


def reference_fonts(font=None, bold_font=None, font_index=0):
    regular = Path(font) if font is not None else BUNDLED_FONT
    bold = Path(bold_font) if bold_font is not None else regular
    if type(font_index) is not int or not 0 <= font_index < 10:
        raise ValueError("Font face index must be 0..9")
    for path in (regular, bold):
        if path.suffix.lower() not in {".otf", ".ttf", ".ttc"} or not path.is_file():
            raise ValueError("An existing local OTF/TTF/TTC font is required")
    return FontConfig(regular, bold, font_index)


def _session(canvas):
    if not hasattr(canvas, "rich_session"):
        canvas.rich_session = _rich.RichSession(canvas.im)
    return canvas.rich_session


def _prepare_more(session):
    # Standalone helper batches share one budget. Full cards queue every text
    # operation and paint once after backgrounds, through flush_card_text().
    session.painted = False


def _error():
    raise ValueError(
        "Text exceeds raster budget or would be clipped; use text fallback"
    ) from None


def draw_metric(canvas, span, *, right, center, size, palette, bold=True):
    try:
        session = _session(canvas)
        _prepare_more(session)
        info = session.add(
            span,
            right=right,
            center=center,
            size=size,
            palette=palette,
            font=canvas.font(size, bold),
        )
        if not getattr(canvas, "defer_rich_text", False):
            session.paint()
            canvas.drawn_text.append(span.text)
        canvas.d = ImageDraw.Draw(canvas.im)
        return info
    except _bitmap.BitmapUnavailable:
        _error()


def draw_rich_line(canvas, spans, *, xy, size, palette, bold=False):
    try:
        session = _session(canvas)
        _prepare_more(session)
        infos = session.add_line(
            spans,
            xy=xy,
            size=size,
            palette=palette,
            font=canvas.font(size, bold),
            max_line_mask_pixels=MAX_LINE_MASK_PIXELS,
        )
        if not getattr(canvas, "defer_rich_text", False):
            session.paint()
            canvas.drawn_text.extend(span.text for span in spans)
        canvas.d = ImageDraw.Draw(canvas.im)
        return infos
    except _bitmap.BitmapUnavailable:
        _error()


def queue_card_text(canvas, xy, text, size, fill, bold=False, anchor="lt"):
    """Capture an existing layout operation; backgrounds are still being drawn."""
    canvas.queued_card_text.append((xy, text, size, fill, bold, anchor))


def flush_card_text(canvas, palette):
    """All backgrounds now exist. Prepare all roles, all effects, all faces."""
    try:
        session = _session(canvas)
        for xy, text, size, fill, bold, anchor in canvas.queued_card_text:
            if not text:
                continue
            font = canvas.font(size, bold)
            box = session._measure(font, "getbbox", text, anchor=anchor)
            local = _rich.TextPalette(fill, fill, fill, fill)
            session.add(
                TextSpan(text),
                font=font,
                size=size,
                palette=local,
                left=xy[0] + box[0] / 2,
                top=xy[1] + box[1] / 2,
            )
        for span, right, center, size in canvas.queued_metrics:
            info = session.add(
                span,
                font=canvas.font(size, True),
                size=size,
                palette=palette,
                right=right,
                center=center,
            )
            canvas.metric_layout.append(info)
        session.paint()
        canvas.drawn_text.extend(operation[1] for operation in canvas.queued_card_text)
        canvas.drawn_text.extend(span.text for span, *_ in canvas.queued_metrics)
        canvas.d = ImageDraw.Draw(canvas.im)
    except _bitmap.BitmapUnavailable:
        _error()
