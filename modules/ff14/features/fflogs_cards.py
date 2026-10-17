"""FF14-owned Logs facts, grading and declarative Logs presentation.

Catalog selection and historical winners are supplied by FF14's source
adapter. The painter never infers content or combines different selections.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation, localcontext

from yomihime_game_link_sdk import cards

from ..presentation import ff14_card
from .fflogs_jobs import JobLanguage, job_display
from .fflogs_models import OutputMetadata, PublicCharacter

CATALOG_LIMITATION = (
    "FFLogs 目录不足以核验完整范围；本次仅展示来源返回的副本成绩，"
    "未覆盖完整绝境战与当前资料片零式。"
)
PROVIDER_CATALOG_SCOPE = (
    "绝境战为已查询来源返回的最高百分位；零式按来源默认副本所属资料片及八人目录，"
    "不代表独立地区开放状态。"
)
HISTORY_INCOMPLETE = "历史成绩未查全，可能遗漏旧版本记录"
DETAIL_LIMITATION = "次数仅为当前筛选范围；最高数值与最佳百分位可能来自不同记录。"

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
    if not isinstance(value, Decimal) and len(text) > 128:
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


def percentile_text(value: object) -> str:
    """Pure shared text policy: exact decimal truncation, no drawing or IO."""
    if value is None:
        return "—"
    try:
        number = _number(value)
        if number > 100:
            raise ValueError("out-of-range percentile")
        with localcontext() as context:
            context.prec = 150
            displayed = number.quantize(Decimal("0.1"), rounding=ROUND_DOWN)
        return f"{displayed:.1f}%"
    except (InvalidOperation, ValueError):
        return "ERR"


def percentile(value: object) -> cards.Inline:
    """Classify exact raw value; both card and text use percentile_text labels."""
    label = percentile_text(value)
    if label == "—":
        return cards.Inline((cards.Span(label),))
    if label == "ERR":
        return cards.Inline((cards.Span(label, color="#d34b4b"),))
    number = _number(value)
    if number == Decimal("100"):
        return cards.Inline((cards.Span(label, emphasis="gold"),))
    color = next(color for floor, color in PERCENTILE_COLORS if number >= floor)
    return cards.Inline((cards.Span(label, color=color, emphasis="strong"),))


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
    return "本次返回副本范围"


def ranking_period(row) -> str:
    """Names are provider periods; numeric IDs never become patch versions."""
    origin = row.origin
    if origin is None:
        return "时期信息未提供"
    values = []
    if origin.expansion_name:
        values.append(f"资料片：{origin.expansion_name}")
    if origin.partition_name:
        values.append(f"分区：{origin.partition_name}")
    return " · ".join(values) if values else "时期信息未提供"


# Names are source metadata, not numeric expansion or partition IDs.
_EXPANSION_VERSIONS = {
    "a realm reborn": "2.x",
    "重生之境": "2.x",
    "新生エオルゼア": "2.x",
    "heavensward": "3.x",
    "苍穹之禁城": "3.x",
    "蒼天のイシュガルド": "3.x",
    "stormblood": "4.x",
    "红莲之狂潮": "4.x",
    "紅蓮のリベレーター": "4.x",
    "shadowbringers": "5.x",
    "暗影之逆焰": "5.x",
    "漆黒のヴィランズ": "5.x",
    "endwalker": "6.x",
    "晓月之终途": "6.x",
    "暁月のフィナーレ": "6.x",
    "dawntrail": "7.x",
    "金曦之遗辉": "7.x",
    "黄金のレガシー": "7.x",
}


def partition_version(name: str | None) -> str | None:
    """Accept an explicit version label, never a numeric partition ID."""
    if not name:
        return None
    # Composition labels alone carry no patch evidence. Multiple version
    # labels/ranges are ambiguous and deliberately fall back to expansion.
    versions = re.findall(r"(?<![\d.])([1-9]\d*\.\d{1,2})(?![\d.])", name)
    if len(versions) != 1:
        return None
    version = versions[0]
    if re.fullmatch(r"\s*" + re.escape(version) + r"\s*", name):
        return version
    if re.search(
        r"(?:patch|version|版本|补丁|パッチ)\s*" + re.escape(version), name, re.I
    ):
        return version
    if re.search(re.escape(version) + r"\s*(?:版本|补丁)", name):
        return version
    return None


def record_version(row) -> str:
    """The winning record's source period, not the encounter release patch."""
    if row.query_state != "record" or row.source_error or row.origin is None:
        return "—"
    return partition_version(row.origin.partition_name) or _EXPANSION_VERSIONS.get(
        (row.origin.expansion_name or "").strip().casefold(), "—"
    )


def character_card(
    character: PublicCharacter,
    *,
    realm: str,
    host: str,
    limit: int,
    explicit_selection: bool,
    metadata: OutputMetadata | None = None,
    job_language: JobLanguage = "zh",
) -> cards.Document:
    """Map real returned rows without manufacturing missing catalog entries."""
    metric = {"rdps": "rDPS", "ndps": "nDPS", "cdps": "cDPS"}[character.metric]
    selection = (
        "已指定副本、难度和分区"
        if explicit_selection
        else "FFLogs 默认副本、难度和分区"
    )
    sections = []
    displayed_count = 0
    if character.groups or character.catalog_evaluated:
        present = tuple(
            category
            for category in ("ultimate", "savage")
            if any(
                group.category == category and group.rankings
                for group in character.groups
            )
        )
        remaining = limit
        for category, title in (("ultimate", "绝境战"), ("savage", "零式")):
            groups = tuple(
                group for group in character.groups if group.category == category
            )
            if not groups:
                sections.append(
                    cards.Section(
                        title,
                        (cards.Text("该类别未取得可用目录，结果范围不完整。"),),
                        panel=False,
                    )
                )
                continue
            # Reserve one row for each later category when the budget permits.
            reserve = int(category == "ultimate" and "savage" in present)
            available = max(0, remaining - reserve) if remaining > 1 else remaining
            blocks = []
            ultimate_rows = []
            for group in groups:
                rows = group.rankings[:available]
                available -= len(rows)
                remaining -= len(rows)
                displayed_count += len(rows)
                if not rows:
                    continue
                if category == "savage":
                    period_name = (
                        rows[0].origin.partition_name
                        if rows[0].origin is not None
                        else None
                    )
                    blocks.append(
                        cards.Text(
                            group.zone_name
                            + (f" · {period_name}" if period_name else ""),
                            style="heading",
                        )
                    )
                    blocks.append(_ranking_table(rows, metric, job_language))
                else:
                    ultimate_rows.extend(rows)
            if ultimate_rows:
                blocks.append(
                    _ranking_table(
                        ultimate_rows, metric, job_language, include_record_version=True
                    )
                )
            if not blocks:
                blocks.append(cards.Text("受本图展示上限限制，此类别记录未展示。"))
            subtitle = (
                "已查询来源返回的最高百分位"
                if category == "ultimate"
                else groups[0].expansion_name
            )
            sections.append(
                cards.Section(
                    title,
                    tuple(blocks),
                    subtitle=subtitle,
                    panel=False,
                )
            )
    else:
        rows = character.rankings[:limit]
        displayed_count = len(rows)
        content = (
            (_ranking_table(rows, metric, job_language),)
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
                PROVIDER_CATALOG_SCOPE
                if character.groups or character.catalog_evaluated
                else CATALOG_LIMITATION,
                style="caption",
            ),
            cards.Text(DETAIL_LIMITATION, style="caption"),
            *(
                (
                    cards.Text(
                        f"历史缓存命中 {character.history_cache_hits} 个范围；最早获取：{datetime.fromtimestamp(character.history_fetched_at, UTC):%Y-%m-%d %H:%M:%S UTC}；有效期900秒。",
                        style="caption",
                    ),
                )
                if character.history_fetched_at is not None
                else ()
            ),
            *(
                (
                    cards.Text(
                        f"本图显示 {displayed_count}/{len(character.rankings)} 项；展示上限 {limit}，部分记录未展示。",
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
        sources=(f"数据源：{'cn.fflogs.com' if realm == 'cn' else 'www.fflogs.com'}",),
    )


__all__ = [
    "CATALOG_LIMITATION",
    "DETAIL_LIMITATION",
    "PERCENTILE_COLORS",
    "amount",
    "character_card",
    "kill_count",
    "percentile",
    "percentile_text",
    "selection_title",
    "ranking_period",
]


def _ranking_table(
    rankings, metric, job_language="zh", *, include_record_version=False
):
    rows = tuple(
        (
            "\n".join(
                (
                    row.encounter_name,
                    *(
                        (
                            {
                                "no_records": "查询成功，无公开记录",
                                "failed": "查询失败",
                                "not_queried": "未查询",
                            }[row.query_state],
                        )
                        if row.query_state != "record"
                        else ()
                    ),
                    *(
                        (HISTORY_INCOMPLETE,)
                        if not row.history_complete and row.query_state == "record"
                        else ()
                    ),
                )
            ),
            job_display(row.spec, job_language),
            *((record_version(row),) if include_record_version else ()),
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
            cards.Column(
                "副本",
                weight=(3.1 if job_language == "ja" else 2.9)
                if include_record_version
                else (2.7 if job_language == "ja" else 3.7),
            ),
            cards.Column(
                "职业",
                weight=2.6
                if job_language == "ja"
                else (1.8 if include_record_version else 1.6),
            ),
            *(
                (cards.Column("记录版本", weight=1.6, wrap="nowrap"),)
                if include_record_version
                else ()
            ),
            cards.Column(
                f"最高 {metric}",
                weight=1.9 if include_record_version else 1.8,
                align="right",
                wrap="nowrap",
            ),
            cards.Column(
                "过本次数",
                weight=1.6 if include_record_version else 1.4,
                align="right",
                wrap="nowrap",
            ),
            cards.Column(
                "最佳百分位",
                weight=1.9 if include_record_version else 1.8,
                align="right",
                valign="center",
                wrap="nowrap",
            ),
        ),
        rows=rows,
    )
