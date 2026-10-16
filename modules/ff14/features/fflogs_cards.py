"""FF14-owned Logs facts, grading and declarative five-column presentation.

This adapter never discovers content from display names. The current source
contract returns a single ranking selection, so its truthful group is that
selection. A future verified catalog can supply zone labels, but neither this
module nor the painter substitutes a synthetic expansion/Ultimate catalog.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext

from yomihime_game_link_sdk import cards

from ..presentation import ff14_card
from .fflogs_models import OutputMetadata, PublicCharacter

CATALOG_LIMITATION = "仅展示本次返回记录；完整绝境战与资料片零式目录暂不可用。"
PROVIDER_CATALOG_SCOPE = (
    "范围以 FFLogs 目录及默认副本所属资料片为准，不代表地区开放状态。"
)
DETAIL_LIMITATION = "次数仅为当前筛选范围；缺失数据与记录版本显示为 —。"

# FFLogs grade colors are game policy, never inferred by Core or altered by theme.
PERCENTILE_COLORS = (
    (Decimal("99"), "#e268a8"),
    (Decimal("95"), "#ff8000"),
    (Decimal("75"), "#a335ee"),
    (Decimal("50"), "#0070ff"),
    (Decimal("25"), "#1eff00"),
    (Decimal("0"), "#666666"),
)


def _number(value: object) -> Decimal:
    if type(value) not in {int, float, Decimal, str}:
        raise ValueError("unsupported numeric type")
    text = str(value)
    if len(text) > 128:
        raise ValueError("numeric budget exceeded")
    number = Decimal(text)
    if (
        not number.is_finite()
        or number < 0
        or number.adjusted() >= 18
        or len(number.as_tuple().digits) > 128
        or number.as_tuple().exponent < -128
    ):
        raise ValueError("invalid metric")
    return number.copy_abs() if number == 0 else number


def percentile(value: object) -> cards.Inline:
    """Classify exact raw percentile; display rounding never awards gold."""
    if value is None:
        return cards.Inline((cards.Span("—"),))
    try:
        number = _number(value)
        if number > 100:
            raise ValueError("out-of-range percentile")
        with localcontext() as context:
            context.prec = 150
            displayed = number.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        label = f"{displayed:.1f}%"
        if number == Decimal("100"):
            return cards.Inline((cards.Span(label, emphasis="gold"),))
        color = next(color for floor, color in PERCENTILE_COLORS if number >= floor)
        return cards.Inline((cards.Span(label, color=color, emphasis="strong"),))
    except (InvalidOperation, ValueError):
        return cards.Inline((cards.Span("ERR", color="#d34b4b"),))


def amount(value: object) -> cards.Inline:
    """Round only presentation to thousands-separated integers, half-up."""
    if value is None:
        return cards.Inline((cards.Span("—"),))
    try:
        with localcontext() as context:
            context.prec = 150
            number = _number(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return cards.Inline((cards.Span(f"{number:,}", emphasis="strong"),))
    except (InvalidOperation, ValueError):
        return cards.Inline((cards.Span("ERR", color="#d34b4b"),))


def kill_count(value: object) -> cards.Inline:
    """Only the parsed per-encounter totalKills is supplied here, never a size."""
    if value is None:
        return cards.Inline((cards.Span("—"),))
    if type(value) is not int or not 0 <= value <= 2_147_483_647:
        return cards.Inline((cards.Span("ERR", color="#d34b4b"),))
    return cards.Inline((cards.Span(str(value), emphasis="strong"),))


def selection_title(
    character: PublicCharacter, metadata: OutputMetadata | None = None
) -> str:
    """Use an exact source zone identity, never names, ID order or a fixture."""
    if metadata is not None:
        matches = tuple(
            zone for zone in metadata.zones if zone.zone_id == character.zone_id
        )
        if len(matches) == 1:
            return matches[0].name
    return "本次查询范围"


def character_card(
    character: PublicCharacter,
    *,
    realm: str,
    host: str,
    limit: int,
    explicit_selection: bool,
    metadata: OutputMetadata | None = None,
) -> cards.Document:
    """Map real returned rows without manufacturing missing catalog entries."""
    metric = {"rdps": "rDPS", "ndps": "nDPS", "cdps": "cDPS"}[character.metric]
    selection = (
        "已指定副本、难度和分区"
        if explicit_selection
        else "FFLogs 默认副本、难度和分区"
    )
    sections = []
    remaining = limit
    if character.groups:
        for category, title in (("ultimate", "绝境战"), ("savage", "零式")):
            groups = tuple(
                group for group in character.groups if group.category == category
            )
            if not groups:
                continue
            group_rows = tuple(row for group in groups for row in group.rankings)[
                :remaining
            ]
            remaining -= len(group_rows)
            if not group_rows:
                continue
            subtitle = (
                "FFLogs 目录" if category == "ultimate" else groups[0].expansion_name
            )
            sections.append(
                cards.Section(
                    title,
                    (_ranking_table(group_rows, metric),),
                    subtitle=subtitle,
                    panel=False,
                )
            )
    else:
        rows = character.rankings[:limit]
        content = (
            (_ranking_table(rows, metric),)
            if rows
            else (cards.Text("当前筛选条件下没有公开战绩记录。"),)
        )
        sections.append(
            cards.Section(
                selection_title(character, metadata),
                content,
                subtitle=selection,
                panel=False,
            )
        )
    return ff14_card(
        f"{character.name} @ {character.server_name}",
        category="Logs",
        subtitle=f"最终幻想 XIV · FFLogs · {'CN' if realm == 'cn' else 'Global'}",
        layout="table",
        blocks=(
            *sections,
            cards.Text(
                PROVIDER_CATALOG_SCOPE if character.groups else CATALOG_LIMITATION,
                style="caption",
            ),
            cards.Text(DETAIL_LIMITATION, style="caption"),
            *(
                (
                    cards.Text(
                        f"共 {len(character.rankings)} 项，本图仅显示前 {limit} 项。",
                        style="caption",
                    ),
                )
                if len(character.rankings) > limit
                else ()
            ),
            *(
                cards.Text(message, style="caption")
                for message in character.catalog_warnings[:3]
            ),
            *(
                (
                    cards.Text(
                        f"另有 {len(character.catalog_warnings) - 3} 项来源提示，当前结果可能不完整。",
                        style="caption",
                    ),
                )
                if len(character.catalog_warnings) > 3
                else ()
            ),
        ),
        sources=(f"数据源：{host}",),
    )


__all__ = [
    "CATALOG_LIMITATION",
    "DETAIL_LIMITATION",
    "PERCENTILE_COLORS",
    "amount",
    "character_card",
    "kill_count",
    "percentile",
    "selection_title",
]


def _ranking_table(rankings, metric):
    rows = tuple(
        (
            f"{row.encounter_name}\n记录版本 —",
            row.spec or "—",
            amount("invalid") if row.source_error else amount(row.best_amount),
            kill_count("invalid") if row.source_error else kill_count(row.total_kills),
            percentile("invalid")
            if row.source_error
            else percentile(
                row.rank_percent_exact
                if row.rank_percent_exact is not None
                else row.rank_percent
            ),
        )
        for row in rankings
    )
    return cards.Table(
        columns=(
            cards.Column("副本 / 记录版本", weight=3.7),
            cards.Column("职业", weight=1.6),
            cards.Column(f"最高 {metric}", weight=1.8, align="right", wrap="nowrap"),
            cards.Column("过本次数", weight=1.4, align="right", wrap="nowrap"),
            cards.Column(
                "最佳百分位", weight=1.8, align="right", valign="center", wrap="nowrap"
            ),
        ),
        rows=rows,
    )
