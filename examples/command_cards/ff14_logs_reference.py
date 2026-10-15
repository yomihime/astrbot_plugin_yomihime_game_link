#!/usr/bin/env python3
"""Synthetic full-expansion Logs layout using the selected rich-text styles.

All names/catalog rows and numeric records are offline layout fixtures, NOT a
current region catalog, an API response, or a real character's records. The
module adapter must independently validate source, scope, catalog and MAX
semantics before calling percentile(). Nothing here probes FFLogs or selects a
production catalog. No runtime/SDK integration is claimed.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from pathlib import Path

from renderer import (
    FF14,
    Canvas,
    FontConfig,
    Palette,
    linkmark,
    output_directory,
    wrap_text,
)
from rich_text_reference import (
    Grade,
    TextRole,
    TextSpan,
    face_color,
    flush_card_text,
    queue_card_text,
    reference_fonts,
)

WIDTH = 1040
LEFT, RIGHT = 57, 983
NAME_X, NAME_W, JOB_X, RDPS_R, COUNT_R, PCT_R = 77, 285, 390, 661, 781, 963
MAX_ROWS = 48
MAX_LABEL_CHARS = 128
MAX_NUMERIC_CHARS = 128
MAX_INTEGER_DIGITS = 18
FOOTER = "Powered by yomihime/astrbot_plugin_yomihime_game_link"
THEMES = {
    "cool_white": Palette(
        "冷白",
        "#E8EDF5",
        "#F8FAFC",
        "#CCD6E2",
        "#DFE5EC",
        "#FFFFFF",
        "#EDF1F5",
        "#4C5A69",
        "#293645",
        "#7E94AB",
        "#5C7186",
        "#738291",
        "#95A0AD",
    ),
    "deep_indigo": Palette(
        "深靛",
        "#0F1423",
        "#1C2337",
        "#3B4561",
        "#333E58",
        "#222C42",
        "#11182B",
        "#D0D8E7",
        "#EDF0F6",
        "#8A9FD2",
        "#B8C8E8",
        "#A3AFC8",
        "#8F9AB2",
    ),
}
# Business mapping belongs to this FF14 example, never the generic rich-text file.
PERCENTILE_GRADES = (
    (Decimal("100"), Grade.GOLD),
    (Decimal("99"), Grade.PINK),
    (Decimal("95"), Grade.ORANGE),
    (Decimal("75"), Grade.PURPLE),
    (Decimal("50"), Grade.BLUE),
    (Decimal("25"), Grade.GREEN),
    (Decimal("0"), Grade.GRAY),
)


@dataclass(frozen=True)
class Unevaluable:
    """Validated source text that has no meaningful numerical evaluation."""

    text: str

    def __post_init__(self):
        TextSpan(self.text)  # Same single-line/length limits; preserve text verbatim.


@dataclass(frozen=True)
class Record:
    """job/version refer to the percentile record; highest rDPS is independent.

    total_kills is a supplied FFLogs totalKills-like count, not len(ranks), nor
    lifetime clears. This fixture does not assert an unverified API JSON shape.
    """

    encounter: str
    best_percentile: object
    job: str
    highest_rdps: object
    total_kills: object
    version: str


@dataclass(frozen=True)
class LogsData:
    character: str
    world: str
    region: str
    ultimate: tuple[Record, ...]
    savage: tuple[Record, ...]
    savage_title: str


# Synthetic catalog fixture: seven ultimates plus ALL three illustrative raid
# tiers (13 boss/phase rows), not just the latest tier. It is not a live catalog.
DEMO_LOGS = LogsData(
    "ChatGPT",
    "OpenAI",
    "CN",
    (
        Record("妖星绝境战", "100", "武士", "41234.567890123", 31, "7.51"),
        Record("光暗未来绝境战", "99.3", "占星术士", "16123.456789012", 46, "7.4"),
        Record("欧米茄绝境验证战", "95", "白魔法师", "6712.5", 18, "6.5"),
        Record("幻想龙诗绝境战", "75", "白魔法师", "3821.49", 24, "6.28"),
        Record("亚历山大绝境战", "50", "白魔法师", "6321.22", 9, "5.x"),
        Record("究极神兵绝境战", "25", "赤魔法师", "5543.78", 6, "5.4"),
        Record("巴哈姆特绝境战", "0", "赤魔法师", "6032.12", 2, "5.4"),
    ),
    (
        Record("M12S 本体\n林德布鲁姆 II", "99.7", "学者", "23123.23", 12, "7.4"),
        Record("M12S 门神\n林德布鲁姆", "96.4", "学者", "24123.35", 16, "7.4"),
        Record("M11S 霸王", "82.1", "学者", "23210.46", 21, "7.4"),
        Record("M10S 极限兄弟", "61.7", "学者", "22310.57", 17, "7.4"),
        Record("M9S 致命美人", None, "—", None, 0, "—"),
        Record("M8S 剑嚎", "90.4", "占星术士", "18123.79", 13, "7.2"),
        Record("M7S 野蛮恨心", "93.6", "占星术士", "18810.89", 14, "7.2"),
        Record("M6S 狂热糖潮", "74.2", "占星术士", "19710.91", 22, "7.2"),
        Record("M5S 热舞绿光", "89.8", "占星术士", "17810.12", 11, "7.2"),
        Record("M4S 狡雷", "98.6", "贤者", "15610.23", 24, "7.0"),
        Record("M3S 野蛮爆弹狂人", "95.8", "贤者", "14810.35", 17, "7.0"),
        Record("M2S 蜂蜂小甜心", "99.4", "贤者", "15810.46", 21, "7.0"),
        Record("M1S 黑猫", "98.4", "贤者", "15410.57", 30, "7.0"),
    ),
    "零式阿卡狄亚登天斗技场",
)


def _number(raw: object) -> Decimal:
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float, Decimal)):
        raise ValueError("Invalid metric type")
    if isinstance(raw, int) and raw.bit_length() > 64:
        raise ValueError("Metric exceeds numeric input budget")
    if isinstance(raw, Decimal):
        if not raw.is_finite() or len(raw.as_tuple().digits) > MAX_NUMERIC_CHARS:
            raise ValueError("Invalid or oversized Decimal")
        value = raw
    else:
        source = str(raw)
        if len(source) > MAX_NUMERIC_CHARS:
            raise ValueError("Metric exceeds numeric input budget")
        value = Decimal(source)
    if (
        not value.is_finite()
        or value < 0
        or value.adjusted() >= MAX_INTEGER_DIGITS
        or value.as_tuple().exponent < -MAX_NUMERIC_CHARS
    ):
        raise ValueError("Invalid, negative or oversized metric")
    return value.copy_abs() if value == 0 else value


def _special(raw: object) -> TextSpan | None:
    if raw is None:
        return TextSpan("—")
    if isinstance(raw, Unevaluable):
        return TextSpan(raw.text)
    return None


def percentile(raw: object) -> TextSpan:
    """Format a source/scope-validated FFLogs percentile, classifying raw first.

    raw=100 means the caller has established the real source's maximum in the
    applicable comparison scope. This function does not fetch/prove that fact.
    It must not be used to normalize unrelated scores, damage, or prices.
    """
    other = _special(raw)
    if other is not None:
        return other
    try:
        value = _number(raw)
        if value > 100:
            raise ValueError("Out-of-range percentile")
        grade = next(
            grade for threshold, grade in PERCENTILE_GRADES if value >= threshold
        )
        with localcontext() as context:
            context.prec = MAX_NUMERIC_CHARS + MAX_INTEGER_DIGITS
            displayed = value.quantize(Decimal(".1"), rounding=ROUND_HALF_UP)
        return TextSpan(f"{displayed:.1f}%", TextRole.GRADED, grade)
    except (ValueError, InvalidOperation):
        return TextSpan("ERR", TextRole.ERROR)


def rdps(raw: object) -> TextSpan:
    """Integer thousands; half-up is the reference's explicit rounding choice."""
    other = _special(raw)
    if other is not None:
        return other
    try:
        with localcontext() as context:
            context.prec = MAX_NUMERIC_CHARS + MAX_INTEGER_DIGITS
            value = _number(raw).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return TextSpan(f"{value:,}")
    except (ValueError, InvalidOperation):
        return TextSpan("ERR", TextRole.ERROR)


def count(raw: object) -> TextSpan:
    other = _special(raw)
    if other is not None:
        return other
    try:
        value = _number(raw)
        if value != value.to_integral_value():
            raise ValueError("Count must be an integer")
        return TextSpan(str(int(value)))
    except (ValueError, InvalidOperation):
        return TextSpan("ERR", TextRole.ERROR)


def _name_lines(canvas: Canvas, name: str) -> list[str]:
    lines = [
        line
        for part in name.split("\n")
        for line in wrap_text(canvas, part, NAME_W, 29, True)
    ]
    if not 1 <= len(lines) <= 2:
        raise ValueError(
            "Encounter needs more than two lines; use complete text fallback"
        )
    return lines


def _row_height(canvas: Canvas, row: Record) -> int:
    return 108 if len(_name_lines(canvas, row.encounter)) == 1 else 140


def _validate_data(data: LogsData) -> None:
    if data.region not in {"CN", "GLOBAL"}:
        raise ValueError("Explicit CN or GLOBAL source region required")
    if len(data.ultimate) + len(data.savage) > MAX_ROWS:
        raise ValueError("Too many rows for this bounded reference")
    labels = [data.character, data.world, data.savage_title]
    for row in data.ultimate + data.savage:
        labels.extend((row.encounter, row.job, row.version))
    for label in labels:
        if not isinstance(label, str) or not 0 < len(label) <= MAX_LABEL_CHARS:
            raise ValueError("Label exceeds bounded text contract")
        for part in label.split("\n"):
            TextSpan(part)


def _cell_text(canvas, text, x, y, width, size, color, bold=False, anchor="lt"):
    if canvas.width(text, size, bold) > width:
        raise ValueError("Cell text exceeds width; use complete text fallback")
    canvas.text((x, y), text, size, color, bold, anchor)


def _group(
    canvas: Canvas, title: str, rows: tuple[Record, ...], y: int, palette: Palette
) -> int:
    p = palette
    _cell_text(canvas, title, LEFT, y, RIGHT - LEFT, 33, p.title, True)
    top = y + 55
    end = top + 62 + (sum(_row_height(canvas, row) for row in rows) if rows else 108)
    canvas.rr((LEFT, top, RIGHT, end), 18, p.panel)
    canvas.rr((LEFT, top, RIGHT, top + 80), 18, p.tint)
    canvas.rect((LEFT, top + 62, RIGHT, top + 80), p.panel)
    for x, text, anchor in (
        (NAME_X, "副本 / 记录版本", "lt"),
        (JOB_X, "职业", "lt"),
        (RDPS_R, "最高 rDPS", "rt"),
        (COUNT_R, "过本次数", "rt"),
        (PCT_R, "最佳百分位", "rt"),
    ):
        canvas.text((x, top + 19), text, 23, p.secondary, True, anchor)
    canvas.line((LEFT, top + 62, RIGHT, top + 62), p.border)
    yy = top + 62
    if not rows:
        canvas.text((NAME_X, yy + 36), "未提供该组记录", 27, p.text)
    for index, row in enumerate(rows):
        height = _row_height(canvas, row)
        if index % 2:
            canvas.rect((LEFT + 1, yy, RIGHT - 1, yy + height - 1), p.paper)
        if index:
            canvas.line((NAME_X, yy, PCT_R, yy), p.rule)
        lines = _name_lines(canvas, row.encounter)
        for line_index, line in enumerate(lines):
            canvas.text((NAME_X, yy + 21 + line_index * 36), line, 29, p.text, True)
        _cell_text(
            canvas,
            f"记录版本  {row.version}",
            NAME_X,
            yy + 26 + len(lines) * 36,
            NAME_W,
            22,
            p.secondary,
        )
        mid = yy + height / 2 - 17
        _cell_text(canvas, row.job, JOB_X, mid + 3, 125, 27, p.text)
        for value, right, size, width in (
            (rdps(row.highest_rdps), RDPS_R, 31, 140),
            (count(row.total_kills), COUNT_R - 20, 29, 85),
        ):
            _cell_text(
                canvas,
                value.text,
                right,
                mid,
                width,
                size,
                face_color(value, p),
                True,
                "rt",
            )
        span = percentile(row.best_percentile)
        if canvas.width(span.text, 35, True) > 140:
            raise ValueError("Percentile text exceeds cell; use complete text fallback")
        canvas.rr(
            (816, yy + (height - 54) / 2, 968, yy + (height + 54) / 2), 11, p.tint
        )
        canvas.queued_metrics.append((span, 955, yy + height / 2, 35))
        yy += height
    canvas.rr((LEFT, top, RIGHT, end), 18, None, p.border)
    return end


def render_card(
    data: LogsData = DEMO_LOGS,
    theme: str = "cool_white",
    *,
    fonts: FontConfig | None = None,
) -> Canvas:
    """Render already-selected facts; never discover a catalog or fetch resources."""
    _validate_data(data)
    p = THEMES[theme]
    fonts = fonts or reference_fonts()
    measure = Canvas(p.outside, WIDTH, 1, fonts=fonts)
    body_height = sum(_row_height(measure, row) for row in data.ultimate + data.savage)
    body_height += 108 * (int(not data.ultimate) + int(not data.savage))
    height = 720 + body_height
    canvas = Canvas(p.outside, WIDTH, height, fonts=fonts)
    canvas.metric_layout = []
    canvas.queued_card_text = []
    canvas.queued_metrics = []
    # Capture ordinary text too: one production session samples the completed
    # background, then paints every effect before every crisp face.
    canvas.text = (
        lambda xy,
        text,
        size=24,
        fill="#333333",
        bold=False,
        anchor="lt": queue_card_text(canvas, xy, text, size, fill, bold, anchor)
    )
    canvas.rr((22, 22, WIDTH - 22, height - 22), 26, p.paper)
    canvas.rr((35, 35, WIDTH - 35, height - 35), 21, None, p.border)
    linkmark(canvas, 57, 54, p.accent)
    canvas.text((109, 57), "游戏连结 · FF14 · Logs", 25, p.text, True)
    canvas.line((57, 113, 983, 113), p.rule)
    _cell_text(canvas, data.character, 57, 151, 420, 44, p.title, True)
    world_x = 57 + canvas.width(data.character, 44, True) + 24
    _cell_text(canvas, "@ " + data.world, world_x, 166, 850 - world_x, 30, p.text)
    canvas.rr((894, 136, 983, 225), 22, p.tint)
    canvas.svg(FF14.icon, (901, 143), 75, p.accent)
    y = _group(canvas, "绝境战", data.ultimate, 255, p)
    y = _group(canvas, data.savage_title, data.savage, y + 45, p)
    source = "cn.fflogs.com" if data.region == "CN" else "www.fflogs.com"
    canvas.text((57, y + 24), "数据源：" + source, 22, p.secondary)
    canvas.line((57, y + 68, 983, y + 68), p.rule)
    canvas.text((57, y + 90), FOOTER, 25, p.muted)
    flush_card_text(canvas, p)
    return canvas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--theme", choices=(*THEMES, "both"), default="both")
    parser.add_argument("--font", type=Path)
    parser.add_argument("--bold-font", type=Path)
    parser.add_argument("--font-index", type=int, default=0)
    args = parser.parse_args()
    fonts = reference_fonts(args.font, args.bold_font, args.font_index)
    output = output_directory(args.output_dir)
    results = []
    for theme in THEMES if args.theme == "both" else (args.theme,):
        canvas = render_card(theme=theme, fonts=fonts)
        path = canvas.save(output / f"logs_style_{theme}.png")
        results.append(
            {
                "file": path.name,
                "theme": theme,
                "size": [canvas.w, canvas.h],
                "bytes": path.stat().st_size,
                "metrics": [asdict(info) for info in canvas.metric_layout],
            }
        )
    manifest = {
        "data": (
            "Entire catalog, character and all records are synthetic offline "
            "fixtures; no API/catalog verification or query occurred."
        ),
        "font": (
            "Explicit local font; bundled Regular is reused for bold unless overridden."
        ),
        "results": results,
    }
    with (output / "style_reference_checks.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
    print(
        json.dumps(
            {"output": str(output), "files": [result["file"] for result in results]},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
