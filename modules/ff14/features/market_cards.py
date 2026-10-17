"""Declarative market cards from validated, finite execution facts only."""

from __future__ import annotations

from datetime import datetime

from yomihime_game_link_sdk import cards
from yomihime_game_link_sdk.results import ErrorCode

from ..presentation import ff14_card
from ..query_resolution import MarketQuery
from .market_models import Listing, Quote, ScopeOutcome
from .market_sources import CatalogSnapshot


def world_name(
    catalog: CatalogSnapshot | None,
    query: MarketQuery,
    world_id: int | None,
    region: str,
) -> str | None:
    """Resolve only an exact World in the execution's validated query scope."""
    scope = query.scope
    if catalog is None or not catalog.catalog.available or type(world_id) is not int:
        return None
    if region not in scope.regions:
        return None
    for entry in catalog.catalog.entries:
        if entry.kind == "world" and entry.id == world_id and entry.region == region:
            if scope.kind == "world" and entry.id != scope.target:
                return None
            if scope.kind == "dc" and entry.dc_id != scope.target:
                return None
            return entry.name
    return None


_REGION_LABELS = {
    "China": "中国",
    "North-America": "北美",
    "Europe": "欧洲",
    "Japan": "日本",
    "Oceania": "大洋洲",
}
_INTENT_LABELS = {"min": "最低挂牌", "overview": "市场概览", "listings": "挂牌样本"}


def _region_label(region: str) -> str:
    return _REGION_LABELS.get(region, region)


def _age(stamp: datetime | None, observed: datetime) -> str:
    if stamp is None:
        return "未知"
    elapsed = (observed - stamp).total_seconds()
    if elapsed < 0:
        return "时间异常（晚于查询）"
    seconds = int(elapsed)
    if seconds < 1:
        return "刚刚"
    if seconds < 60:
        return f"{seconds} 秒前"
    if seconds < 3600:
        return f"{seconds // 60} 分钟前"
    if seconds < 86400:
        return f"{seconds // 3600} 小时前"
    return f"{seconds // 86400} 天前"


def _price(value: int | float | None) -> str:
    return "未知" if value is None else str(value)


def _server_label(query, catalog, region, world_id):
    name = world_name(catalog, query, world_id, region)
    if name is None:
        name = f"未知服务器（ID {world_id}）" if world_id is not None else "服务器未知"
    return f"{_region_label(region)} · {name}" if len(query.scope.regions) > 1 else name


def _world_block(query, catalog, region, world_id, uploaded_at, observed_at):
    return cards.Section(
        "",
        (
            cards.Text(
                _server_label(query, catalog, region, world_id), style="heading"
            ),
            cards.Text(f"世界上传：{_age(uploaded_at, observed_at)}", style="caption"),
        ),
        panel=False,
    )


def _minimum_row(query, catalog, quality, region, quote, observed_at):
    return cards.Row(
        (
            cards.Text(quality.upper(), style="heading"),
            cards.Text(f"{_price(quote.minimum)} Gil / 件", style="title"),
            _world_block(
                query,
                catalog,
                region,
                quote.minimum_world,
                quote.minimum_uploaded_at,
                observed_at,
            ),
        ),
        weights=(0.55, 1.4, 1.85),
    )


def _failure_label(outcome: ScopeOutcome) -> str:
    # Detailed source diagnostics remain intact in the fallback and fact data.
    # The card gives the user the actionable failure category, not internal keys.
    if outcome.reason == "unresolved_item":
        return "物品资料未解析"
    if outcome.stage == "deadline":
        return "查询超时"
    if outcome.code is ErrorCode.RATE_LIMITED:
        return "请求限流"
    if outcome.code is ErrorCode.UNPARSED:
        return "来源格式异常"
    if outcome.code in (ErrorCode.AUTH_REQUIRED, ErrorCode.AUTH_EXPIRED):
        return "来源授权不可用"
    return "来源查询失败"


def _coverage(outcomes: tuple[ScopeOutcome, ...], observed_at: datetime) -> str:
    summaries = []
    for outcome in outcomes:
        if outcome.failed:
            detail = _failure_label(outcome)
        else:
            provenance = outcome.provenance
            detail = _age(provenance.fetched_at if provenance else None, observed_at)
            if provenance and provenance.cached:
                detail += "（缓存）"
            elif provenance is None:
                detail = "获取时间、缓存状态未知"
            if not outcome.data or not outcome.data.has_data:
                detail += "，无可用记录"
            elif outcome.data.incomplete:
                detail += "，部分指标缺失"
        summaries.append(f"{_region_label(outcome.region)} {detail}")
    return "来源获取：" + "；".join(summaries)


def _scope_label(query: MarketQuery, catalog: CatalogSnapshot | None) -> str:
    regions = " / ".join(_region_label(region) for region in query.scope.regions)
    target = query.scope.target
    if query.scope.kind == "world":
        return _server_label(query, catalog, query.scope.regions[0], target)
    if target is not None:
        return f"{regions} · {target}"
    return f"{regions}全区"


def _listing_table(query, catalog, listings, observed_at):
    return cards.Table(
        (
            cards.Column("品质", weight=0.6),
            cards.Column("单价 / Gil", weight=1, align="right"),
            cards.Column("数量 / 堆", weight=0.85, align="right"),
            cards.Column("服务器", weight=1.65),
            cards.Column("来源时间", weight=1.85),
        ),
        tuple(
            (
                "HQ" if listing.hq else "NQ",
                str(listing.price_per_unit),
                str(listing.quantity),
                _server_label(query, catalog, listing.region, listing.world_id),
                f"上传：{_age(listing.uploaded_at, observed_at)}\n"
                f"审核：{_age(listing.reviewed_at, observed_at)}",
            )
            for listing in listings
        ),
    )


def build_market_card(
    query: MarketQuery,
    outcomes: tuple[ScopeOutcome, ...],
    listings: tuple[Listing, ...],
    minimums: tuple[tuple[str, str, Quote], ...],
    truncated: bool,
    observed_at: datetime,
    catalog: CatalogSnapshot | None = None,
) -> cards.Document:
    """Compact the existing intent without inventing missing prices or inventory.

    Exact diagnostic keys, timestamps and URLs stay in the unchanged fallback
    and typed facts. Cards retain every returned bounded price/quantity row,
    unknown metric, failed/empty scope and truncation warning.
    """
    blocks: list[cards.Block] = []
    if query.intent == "min":
        blocks.extend(
            cards.Section(
                "", (_minimum_row(query, catalog, quality, region, quote, observed_at),)
            )
            for quality, region, quote in minimums
        )
        qualities = ("nq", "hq") if query.quality == "all" else (query.quality,)
        for quality in qualities:
            if not any(row[0] == quality for row in minimums):
                blocks.append(
                    cards.Text(f"{quality.upper()}：最低挂牌未知", style="caption")
                )
        if not minimums:
            blocks.append(cards.Text("来源未提供最低挂牌指标", style="caption"))
        notes = (
            "已返回范围中的最低挂牌；非实时全量报价，不保证此刻可买",
            "时间为对应世界的数据上传时间；来源未提供该条数量和上架时间",
        )
    elif query.intent == "listings":
        blocks.append(_listing_table(query, catalog, listings, observed_at))
        if truncated:
            blocks.append(
                cards.Text("结果已截断，未展示其余区域或挂牌", style="caption")
            )
        notes = (
            "最多 5 条挂牌样本，按单价排序；非实时全量报价，不保证此刻可买",
            "上传与审核均不代表上架时间；数量是单条堆叠，不是全服库存",
        )
    else:
        for outcome in outcomes:
            if not outcome.data or not outcome.data.has_data:
                continue
            for quote in outcome.data.quotes:
                blocks.append(
                    cards.Section(
                        "",
                        (
                            _minimum_row(
                                query,
                                catalog,
                                quote.quality,
                                outcome.region,
                                quote,
                                observed_at,
                            ),
                            cards.PropertyGrid(
                                (
                                    cards.Property(
                                        "最近成交",
                                        f"{_price(quote.recent_purchase)} Gil · {_age(quote.recent_at, observed_at)}",
                                    ),
                                    cards.Property(
                                        "近四天成交均价 / Gil",
                                        _price(quote.average_sale_price),
                                    ),
                                    cards.Property(
                                        "近四天日均售出",
                                        _price(quote.daily_sale_velocity),
                                    ),
                                ),
                                columns=3,
                            ),
                        ),
                    )
                )
        notes = (
            "最低挂牌仅涵盖已返回范围；非实时全量报价，不保证此刻可买",
            "世界上传时间不代表挂牌时间；均价与日均售出为来源近四天指标",
        )
    blocks.extend(cards.Text(note, style="caption") for note in notes)
    quality = "" if query.quality == "all" else f"{query.quality.upper()} "
    return ff14_card(
        query.item_name or f"物品 {query.item_id}",
        category="市场",
        subtitle=f"物品 {query.item_id} · {_scope_label(query, catalog)} · {quality}{_INTENT_LABELS[query.intent]}",
        blocks=tuple(blocks),
        sources=(f"数据源：Universalis · {_coverage(outcomes, observed_at)}",),
    )


__all__ = ["build_market_card", "world_name"]
