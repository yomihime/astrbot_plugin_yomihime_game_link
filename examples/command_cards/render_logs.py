#!/usr/bin/env python3
"""One offline Logs card: two groups, synthetic records, dynamic height.

The encounter labels are illustrative layout fixtures, not a production content
catalog. No API calls, credentials, game data discovery, or plugin changes.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Sequence

from renderer import (
    FF14,
    PALETTES,
    Canvas,
    Palette,
    add_font_arguments,
    badge,
    configure_fonts,
    fit_text,
    linkmark,
    output_directory,
    wrap_text,
)

WIDTH = 1040
ROW_HEIGHT, TABLE_HEADER_HEIGHT = 64, 57
FIRST_TABLE_TOP, GROUP_GAP, FOOTER_HEIGHT = 286, 89, 224
ENCOUNTER_X, JOB_X, VERSION_RIGHT, RDPS_RIGHT, PERCENT_RIGHT = 76, 424, 654, 790, 951


@dataclass(frozen=True)
class Record:
    """Keep raw numeric precision; the two best metrics can be different runs.

    `job` and `version` describe the best-percentile record only. The highest
    rDPS is independently selected. It must never silently become the percentile
    record's rDPS merely because the values share a visual row.
    """

    encounter: str
    best_percentile: Decimal | None
    job: str | None
    highest_rdps: Decimal | None
    version: str | None = None


@dataclass(frozen=True)
class LogsData:
    character: str
    world: str
    region: str
    ultimate: tuple[Record, ...]
    savage: tuple[Record, ...]
    savage_title: str


# These names demonstrate label width and phase rows, not live availability.
# All percentages and rDPS values below are invented, including all 20 rows.
DEMO_LOGS = LogsData(
    character="示例冒险者",
    world="示例服务器",
    region="CN",
    ultimate=(
        Record("绝妖星", Decimal("44.65"), "武士", Decimal("41234.567890123"), "7.51"),
        Record(
            "绝伊甸", Decimal("52.34"), "占星术士", Decimal("16123.456789012"), "7.4"
        ),
        Record("绝欧", Decimal("76.25"), "白魔法师", Decimal("6712.500000001"), "6.5"),
        Record(
            "绝龙诗", Decimal("63.46"), "白魔法师", Decimal("3821.499999999"), "6.28"
        ),
        Record("绝亚", Decimal("81.54"), "白魔法师", Decimal("6321.222222222"), "5.x"),
        Record(
            "绝神兵", Decimal("85.24"), "赤魔法师", Decimal("5543.777777777"), "5.4"
        ),
        Record(
            "绝巴哈", Decimal("94.24"), "赤魔法师", Decimal("6032.123456789"), "5.4"
        ),
    ),
    savage=(
        Record(
            "M12S本体 林德布鲁姆 II",
            Decimal("94.24"),
            "学者",
            Decimal("23123.234567891"),
        ),
        Record(
            "M12S门神 林德布鲁姆", Decimal("95.44"), "学者", Decimal("24123.345678912")
        ),
        Record("M11S 霸王", Decimal("98.14"), "学者", Decimal("23210.456789123")),
        Record("M10S 极限兄弟", Decimal("93.34"), "学者", Decimal("22310.567891234")),
        Record("M9S 致命美人", Decimal("94.64"), "学者", Decimal("23610.678912345")),
        Record("M8S 剑嚎", Decimal("90.44"), "占星术士", Decimal("18123.789123456")),
        Record(
            "M7S 野蛮恨心", Decimal("93.64"), "占星术士", Decimal("18810.891234567")
        ),
        Record(
            "M6S 狂热糖潮", Decimal("74.24"), "占星术士", Decimal("19710.912345678")
        ),
        Record(
            "M5S 热舞绿光", Decimal("89.84"), "占星术士", Decimal("17810.123456789")
        ),
        Record("M4S 狡雷", Decimal("98.64"), "贤者", Decimal("15610.234567891")),
        Record(
            "M3S 野蛮爆弹狂人", Decimal("95.84"), "贤者", Decimal("14810.345678912")
        ),
        Record("M2S 蜂蜂小甜心", Decimal("99.95"), "贤者", Decimal("15810.456789123")),
        Record("M1S 黑猫", Decimal("98.44"), "贤者", Decimal("15410.567891234")),
    ),
    savage_title="7.0 · 零式阿卡狄亚登天斗技场",
)


def decimal_value(value: Decimal | str | int) -> Decimal:
    number = value if isinstance(value, Decimal) else Decimal(str(value))
    if not number.is_finite():
        raise ValueError("The display value must be finite")
    return number


def format_percentile(value: Decimal | str | int | None) -> str:
    """One decimal using explicit half-up rounding; absent is not zero."""
    if value is None:
        return "—"
    number = decimal_value(value)
    if not Decimal(0) <= number <= Decimal(100):
        raise ValueError("Percentile must be between zero and 100")
    return f"{number.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP):.1f}%"


def format_rdps(value: Decimal | str | int | None) -> str:
    """Round the display only to a grouped integer, preserving the raw Decimal."""
    if value is None:
        return "—"
    number = decimal_value(value)
    if number < 0:
        raise ValueError("rDPS must not be negative")
    displayed = number.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"{displayed:,}"


def encounter_lines(c: Canvas, text: str) -> tuple[int, list[str]]:
    """Keep normal labels at 28px; at most two bounded lines for long labels."""
    if c.width(text, 28, True) <= 330:
        return 28, [text]
    lines = wrap_text(c, text, 330, 24, True)
    if len(lines) > 2:
        lines = [lines[0], fit_text(c, "".join(lines[1:]), 330, 24, 24, True)[1]]
    return 24, lines


def row_height(c: Canvas, row: Record) -> int:
    return 82 if len(encounter_lines(c, row.encounter)[1]) > 1 else ROW_HEIGHT


def table_bottom(c: Canvas, top: int, rows: Sequence[Record]) -> int:
    return (
        top
        + TABLE_HEADER_HEIGHT
        + (sum(row_height(c, row) for row in rows) if rows else ROW_HEIGHT)
    )


def draw_group(
    c: Canvas,
    p: Palette,
    title: str,
    rows: Sequence[Record],
    top: int,
    *,
    versions: bool,
) -> int:
    bottom = table_bottom(c, top, rows)
    c.fitted((57, top - 40), title, 750, 29, 21, p.title, True)
    c.text((983, top - 35), f"{len(rows)} 条样例", 23, p.secondary, anchor="rt")
    c.rr((57, top, 983, bottom), 17, p.panel)
    c.rr((57, top, 983, top + TABLE_HEADER_HEIGHT + 17), 17, p.tint)
    c.rect(
        (57, top + TABLE_HEADER_HEIGHT - 1, 983, top + TABLE_HEADER_HEIGHT + 17),
        p.panel,
    )
    header_y = top + 17
    c.text((ENCOUNTER_X, header_y), "副本", 23, p.secondary, True)
    c.text((JOB_X, header_y), "职业", 23, p.secondary, True)
    if versions:
        c.text((VERSION_RIGHT, header_y), "记录版本", 23, p.secondary, True, "rt")
    c.text((RDPS_RIGHT, header_y), "最高 rDPS", 23, p.secondary, True, "rt")
    c.text((964, header_y), "最佳百分位", 23, p.accent, True, "rt")

    y = top + TABLE_HEADER_HEIGHT
    if not rows:
        c.text((76, y + 18), "未提供该组记录", 26, p.secondary)
    for i, row in enumerate(rows):
        height = row_height(c, row)
        if i % 2:
            if i == len(rows) - 1:
                c.rr((58, y, 982, y + height - 1), 16, p.paper)
                c.rect((58, y, 982, y + 18), p.paper)
            else:
                c.rect((58, y, 982, y + height), p.paper)
        if i:
            c.line((76, y, 964, y), p.rule)
        size, lines = encounter_lines(c, row.encounter)
        name_y = y + (height - len(lines) * (size + 4)) / 2
        for index, line in enumerate(lines):
            c.text((ENCOUNTER_X, name_y + index * (size + 4)), line, size, p.text, True)
        middle_y = y + (height - 28) / 2
        c.fitted((JOB_X, middle_y), row.job or "—", 112, 28, 20, p.text)
        if versions:
            c.fitted(
                (VERSION_RIGHT, middle_y),
                row.version or "—",
                96,
                28,
                20,
                p.secondary,
                anchor="rt",
            )
        c.fitted(
            (RDPS_RIGHT, middle_y),
            format_rdps(row.highest_rdps),
            120,
            28,
            19,
            p.text,
            anchor="rt",
        )
        c.rr((828, y + (height - 44) / 2, 966, y + (height + 44) / 2), 11, p.tint)
        c.fitted(
            (PERCENT_RIGHT, y + (height - 36) / 2),
            format_percentile(row.best_percentile),
            119,
            31,
            21,
            p.price,
            True,
            "rt",
        )
        y += height
    c.rr((57, top, 983, bottom), 17, None, p.border)
    c.line((57, top + TABLE_HEADER_HEIGHT, 983, top + TABLE_HEADER_HEIGHT), p.border)
    return bottom


def render_card(data: LogsData = DEMO_LOGS, palette: str = "warm_paper") -> Canvas:
    p = PALETTES[palette]
    # Use a tiny measurement surface; fonts and available row text set the height.
    measure = Canvas(p.outside, width=WIDTH, height=1)
    first_bottom = table_bottom(measure, FIRST_TABLE_TOP, data.ultimate)
    second_top = first_bottom + GROUP_GAP
    second_bottom = table_bottom(measure, second_top, data.savage)
    height = second_bottom + FOOTER_HEIGHT
    c = Canvas(p.outside, WIDTH, height)
    c.rr((22, 22, WIDTH - 22, height - 22), 26, p.paper)
    c.rr((35, 35, WIDTH - 35, height - 35), 21, None, p.border)
    linkmark(c, 57, 53, p.accent)
    c.text((109, 57), "游戏连结 · FF14 · 战绩", 24, p.text, True)
    badge(c, (854, 49, 983, 88), "合成样例", p, 23)
    c.line((57, 112, 983, 112), p.rule)

    c.fitted((57, 140), data.character, 295, 43, 27, p.title, True)
    c.fitted((370, 155), "@ " + data.world, 395, 29, 21, p.text)
    badge(c, (794, 151, 854, 189), data.region, p, 23)
    c.text((59, 200), "FFLogs 战绩展示示意 · 全部数值为合成数据", 24, p.secondary)
    c.rr((894, 137, 983, 226), 23, p.tint)
    c.svg(FF14.icon, (901, 144), 75, p.accent)

    draw_group(c, p, "绝境战", data.ultimate, FIRST_TABLE_TOP, versions=True)
    draw_group(c, p, data.savage_title, data.savage, second_top, versions=False)
    c.text(
        (57, second_bottom + 25),
        "职业 / 版本随最佳百分位记录；最高 rDPS 独立取值",
        23,
        p.secondary,
    )
    c.text(
        (57, second_bottom + 60),
        "两项最佳可能来自不同场次；不同职业与版本不直接横比",
        23,
        p.secondary,
    )
    c.text(
        (57, second_bottom + 95),
        "副本名称仅为固定排版样例，不代表实时完整目录",
        22,
        p.secondary,
    )
    c.line((57, second_bottom + 137, 983, second_bottom + 137), p.rule)
    c.text((57, second_bottom + 154), "如月怜的游戏连结", 21, p.muted)
    c.text((983, second_bottom + 154), "离线设计原型", 21, p.muted, anchor="rt")
    return c


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_font_arguments(parser)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output folder; existing image files are never overwritten",
    )
    parser.add_argument("--palette", choices=tuple(PALETTES), default="warm_paper")
    args = parser.parse_args()
    configure_fonts(args)
    output = output_directory(args.output_dir)
    print(
        render_card(palette=args.palette).save(output / f"03_logs_{args.palette}.png")
    )


if __name__ == "__main__":
    main()
