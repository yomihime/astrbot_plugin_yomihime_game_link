"""Universalis finite market execution and SDK 1.4 display, not a capability."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime

from yomihime_game_link_sdk.display import (
    DisplayDocument,
    Link,
    LinksBlock,
    Privacy,
    TextBlock,
)
from yomihime_game_link_sdk.errors import SourceHttpError
from yomihime_game_link_sdk.results import (
    CapabilityResult,
    ErrorCode,
    ErrorDetail,
    ResultStatus,
)

from ..query_resolution import (
    SUPPORTED_REGIONS,
    MarketQuery,
    QueryResolutionError,
    request_plan,
)
from .market_models import Listing, MarketExecution, Quote, ScopeData, ScopeOutcome
from .market_sources import (
    CatalogSnapshot,
    MarketDeadlineError,
    MarketPayloadError,
    MarketSession,
    integer,
    number,
    source_time,
)

MAX_LISTINGS = 5


class MarketSourceIncompleteError(Exception):
    """A schema-valid source response did not resolve the requested item."""


def _mapping(value: object) -> Mapping:
    if not isinstance(value, Mapping):
        raise MarketPayloadError("invalid_object")
    return value


def _world(
    catalog: CatalogSnapshot, world_id: object, query: MarketQuery, region: str
) -> int | None:
    if world_id is None:
        return query.scope.target if query.scope.kind == "world" else None
    world_id = integer(world_id, minimum=1)
    try:
        entry = catalog.catalog.find("world", world_id)
    except QueryResolutionError:
        raise MarketPayloadError("unknown_world") from None
    if (
        entry.region != region
        or (query.scope.kind == "world" and entry.id != query.scope.target)
        or (query.scope.kind == "dc" and entry.dc_id != query.scope.target)
    ):
        raise MarketPayloadError("world_scope_mismatch")
    return world_id


def _world_name(catalog: CatalogSnapshot | None, query: MarketQuery, world_id, region):
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


def _layer(metrics: Mapping, field: str, level: str) -> Mapping | None:
    metric = metrics.get(field)
    if metric is None:
        return None
    value = _mapping(metric).get(level)
    return None if value is None else _mapping(value)


def parse_aggregated(
    payload: object, query: MarketQuery, region: str, catalog: CatalogSnapshot
) -> ScopeData:
    payload = _mapping(payload)
    failed = payload.get("failedItems")
    if failed is None:
        failed = []
    if not isinstance(failed, list) or len(failed) > 1:
        raise MarketPayloadError("invalid_failed_items")
    for item in failed:
        if integer(item, minimum=1) != query.item_id:
            raise MarketPayloadError("item_identity")
    results = payload.get("results")
    if "results" in payload and results is None and failed:
        results = []
    if not isinstance(results, list) or len(results) > 1:
        raise MarketPayloadError("invalid_results")
    if failed:
        if results:
            raise MarketPayloadError("conflicting_item_results")
        raise MarketSourceIncompleteError()
    if not results:
        return ScopeData()
    row = _mapping(results[0])
    if integer(row.get("itemId"), minimum=1) != query.item_id:
        raise MarketPayloadError("item_identity")
    uploads = row.get("worldUploadTimes")
    times = {}
    if uploads is not None:
        if not isinstance(uploads, list) or len(uploads) > 1024:
            raise MarketPayloadError("invalid_upload_times")
        for upload in uploads:
            upload = _mapping(upload)
            world_id = integer(upload.get("worldId"), minimum=1)
            if world_id in times:
                raise MarketPayloadError("duplicate_upload_world")
            times[world_id] = source_time(upload.get("timestamp"), milliseconds=True)
    quotes = []
    qualities = ("nq", "hq") if query.quality == "all" else (query.quality,)
    for quality in qualities:
        metrics = row.get(quality)
        if metrics is None:
            quotes.append(Quote(quality))
            continue
        metrics = _mapping(metrics)
        minimum = _layer(metrics, "minListing", query.scope.kind)
        recent = _layer(metrics, "recentPurchase", query.scope.kind)
        average = _layer(metrics, "averageSalePrice", query.scope.kind)
        velocity = _layer(metrics, "dailySaleVelocity", query.scope.kind)
        min_world = (
            _world(catalog, minimum.get("worldId"), query, region)
            if minimum is not None
            else None
        )
        recent_world = (
            _world(catalog, recent.get("worldId"), query, region)
            if recent is not None
            else None
        )
        quotes.append(
            Quote(
                quality,
                integer(minimum.get("price")) if minimum is not None else None,
                min_world,
                times.get(min_world),
                integer(recent.get("price")) if recent is not None else None,
                recent_world,
                source_time(recent.get("timestamp"), milliseconds=True)
                if recent is not None
                else None,
                number(average.get("price")) if average is not None else None,
                number(velocity.get("quantity")) if velocity is not None else None,
            )
        )
    return ScopeData(
        quotes=tuple(quotes),
        incomplete=any(
            q.incomplete or (q.minimum is not None and q.minimum_world is None)
            for q in quotes
        ),
    )


def parse_listings(
    payload: object, query: MarketQuery, region: str, catalog: CatalogSnapshot
) -> ScopeData:
    payload = _mapping(payload)
    if integer(payload.get("itemID"), minimum=1) != query.item_id:
        raise MarketPayloadError("item_identity")
    for key, expected in (
        ("worldID", query.scope.target if query.scope.kind == "world" else None),
        ("dcName", query.scope.target if query.scope.kind == "dc" else None),
        ("regionName", region if query.scope.kind == "region" else None),
    ):
        actual = payload.get(key)
        if actual is not None:
            if key == "worldID":
                integer(actual, minimum=1)
            if key == "regionName" and actual == "中国":
                actual = "China"
            if actual != expected:
                raise MarketPayloadError("scope_identity")
    has_data = payload.get("hasData")
    if has_data is not None and type(has_data) is not bool:
        raise MarketPayloadError("invalid_has_data")
    uploaded = source_time(payload.get("lastUploadTime"), milliseconds=True)
    world_uploads = payload.get("worldUploadTimes")
    times = {}
    if world_uploads is not None:
        if not isinstance(world_uploads, Mapping) or len(world_uploads) > 1024:
            raise MarketPayloadError("invalid_upload_times")
        for key, stamp in world_uploads.items():
            if not isinstance(key, str) or not key.isascii() or not key.isdecimal():
                raise MarketPayloadError("invalid_upload_world")
            world_id = _world(catalog, int(key), query, region)
            times[world_id] = source_time(stamp, milliseconds=True)
    rows = payload.get("listings")
    if not isinstance(rows, list) or len(rows) > 6:
        raise MarketPayloadError("invalid_listings")
    listings = []
    mismatch = False
    for ordinal, row in enumerate(rows):
        row = _mapping(row)
        if type(row.get("hq")) is not bool:
            raise MarketPayloadError("missing_quality")
        quality = "hq" if row["hq"] else "nq"
        world_id = _world(catalog, row.get("worldID"), query, region)
        listing = Listing(
            integer(row.get("pricePerUnit"), minimum=1),
            integer(row.get("quantity"), minimum=1),
            row["hq"],
            world_id,
            region,
            source_time(row.get("lastReviewTime"), milliseconds=False),
            uploaded if query.scope.kind == "world" else times.get(world_id),
            ordinal,
        )
        if query.quality != "all" and quality != query.quality:
            mismatch = True
            continue
        listings.append(listing)
    if has_data is False and listings:
        raise MarketPayloadError("has_data_contradiction")
    return ScopeData(
        listings=tuple(listings),
        uploaded_at=uploaded,
        has_data_marker=has_data,
        truncated=len(rows) == 6,
        incomplete=mismatch or any(listing.world_id is None for listing in listings),
    )


def _clean_aggregated(payload: object, query: MarketQuery) -> dict:
    payload = _mapping(payload)
    results = []
    for row in payload.get("results", []):
        row = _mapping(row)
        uploads = row.get("worldUploadTimes")
        result = {
            "itemId": row.get("itemId"),
            "worldUploadTimes": None
            if uploads is None
            else [
                {key: _mapping(upload).get(key) for key in ("worldId", "timestamp")}
                for upload in uploads
            ],
        }
        for quality in ("nq", "hq") if query.quality == "all" else (query.quality,):
            metrics = row.get(quality)
            if metrics is None:
                result[quality] = None
                continue
            cleaned = {}
            for field, keys in (
                ("minListing", ("price", "worldId")),
                ("recentPurchase", ("price", "timestamp", "worldId")),
                ("averageSalePrice", ("price",)),
                ("dailySaleVelocity", ("quantity",)),
            ):
                layers = _mapping(metrics).get(field)
                if layers is None:
                    cleaned[field] = None
                else:
                    value = _mapping(layers).get(query.scope.kind)
                    cleaned[field] = {
                        query.scope.kind: None
                        if value is None
                        else {key: _mapping(value).get(key) for key in keys}
                    }
            result[quality] = cleaned
        results.append(result)
    return {"results": results, "failedItems": payload.get("failedItems")}


def _clean_listings(payload: object) -> dict:
    payload = _mapping(payload)
    keys = (
        "itemID",
        "worldID",
        "dcName",
        "regionName",
        "lastUploadTime",
        "worldUploadTimes",
        "hasData",
    )
    cleaned = {key: payload.get(key) for key in keys}
    listing_keys = ("pricePerUnit", "quantity", "hq", "worldID", "lastReviewTime")
    cleaned["listings"] = [
        {key: _mapping(row).get(key) for key in listing_keys}
        for row in payload.get("listings", [])
    ]
    return cleaned


def _diagnostic(exc: Exception, region: str, target: str) -> ScopeOutcome:
    if isinstance(exc, MarketSourceIncompleteError):
        return ScopeOutcome(
            region,
            target,
            code=ErrorCode.UPSTREAM_ERROR,
            stage="source",
            reason="unresolved_item",
        )
    if isinstance(exc, MarketPayloadError):
        return ScopeOutcome(
            region, target, code=ErrorCode.UNPARSED, stage="parse", reason=exc.code
        )
    if isinstance(exc, MarketDeadlineError):
        return ScopeOutcome(
            region,
            target,
            code=ErrorCode.UPSTREAM_ERROR,
            stage="deadline",
            reason="deadline",
        )
    if isinstance(exc, SourceHttpError):
        code = (
            ErrorCode.RATE_LIMITED
            if exc.code in ("rate_limited", "concurrency_limited")
            or exc.status_code == 429
            else ErrorCode.UPSTREAM_ERROR
        )
        if exc.code == "invalid_response":
            code = ErrorCode.UNPARSED
        if exc.code == "credentials_unavailable":
            code = ErrorCode.AUTH_REQUIRED
        return ScopeOutcome(
            region,
            target,
            code=code,
            stage="http",
            reason=exc.code,
            status_code=exc.status_code,
        )
    if (
        isinstance(exc, PermissionError)
        and getattr(exc, "code", None) == "grant_expired"
    ):
        return ScopeOutcome(
            region,
            target,
            code=ErrorCode.AUTH_EXPIRED,
            stage="cache",
            reason="grant_expired",
        )
    return ScopeOutcome(
        region,
        target,
        code=ErrorCode.MODULE_UNAVAILABLE,
        stage="cache",
        reason="unavailable",
    )


def _age(stamp: datetime | None, observed: datetime) -> str:
    if stamp is None:
        return "未知"
    seconds = (observed - stamp).total_seconds()
    if seconds < 0:
        return "源时钟异常（未来时间）"
    return f"{stamp.isoformat()}，距本次查询 {int(seconds)} 秒"


def _price(value: int | float | None) -> str:
    return "未知" if value is None else str(value)


def _result(
    query: MarketQuery,
    outcomes: tuple[ScopeOutcome, ...],
    listings: tuple[Listing, ...],
    minimums: tuple[tuple[str, str, Quote], ...],
    truncated: bool,
    observed_at: datetime,
    catalog: CatalogSnapshot | None = None,
) -> CapabilityResult:
    usable = tuple(
        outcome for outcome in outcomes if outcome.data and outcome.data.has_data
    )
    failures = tuple(outcome for outcome in outcomes if outcome.failed)
    empty = tuple(
        outcome
        for outcome in outcomes
        if not outcome.failed and outcome.data and not outcome.data.has_data
    )
    if not usable:
        if failures:
            code = (
                failures[0].code
                if len(failures) == len(outcomes)
                and all(f.code == failures[0].code for f in failures)
                else ErrorCode.UPSTREAM_ERROR
            )
            message = "市场来源查询未完成；无法据此判断物品是否可交易或是否无挂牌。"
        else:
            code = ErrorCode.NO_RECORDS
            message = "来源暂无可用市场记录，无法判断是否无挂牌或是否可交易。"
        return CapabilityResult(
            f"ff14-market-{query.item_id}",
            ResultStatus.ERROR,
            privacy=Privacy.PUBLIC,
            error=ErrorDetail(code, message),
        )
    partial = bool(
        failures or empty or any(outcome.data.incomplete for outcome in usable)
    )

    def quote_context(region, world_id, uploaded_at):
        name = _world_name(catalog, query, world_id, region)
        world = f"{name or '服务器名称未知'}（World {world_id if world_id is not None else '未知'}）"
        provenance = next(
            (
                outcome.provenance
                for outcome in outcomes
                if outcome.region == region and not outcome.failed
            ),
            None,
        )
        fetched = _age(provenance.fetched_at if provenance else None, observed_at)
        cache = (
            "缓存原获取时间"
            if provenance and provenance.cached
            else "本次获取"
            if provenance
            else "缓存状态未知"
        )
        return f"{world}；World 数据上传 {_age(uploaded_at, observed_at)}；Universalis 来源获取 {fetched}（{cache}）；非全部 World 覆盖，非实时可买"

    blocks = [
        TextBlock(
            f"物品 ID：{query.item_id}；范围：{' / '.join(query.scope.regions)} / {query.scope.target or '全区'}（{query.scope.source}）；品质：{query.quality}；意图：{query.intent}"
        ),
        TextBlock("覆盖为本次成功返回的来源范围；不保证全部 World 上传或实时可买。"),
    ]
    for outcome in outcomes:
        state = (
            "失败"
            if outcome.failed
            else "无可用记录"
            if not outcome.data.has_data
            else "有可用记录"
        )
        blocks.append(
            TextBlock(
                f"{outcome.region} / {outcome.target}：{state}"
                + (
                    f"（{outcome.stage}/{outcome.reason}）"
                    if outcome.failed
                    else f"；获取 {outcome.provenance.fetched_at.isoformat()}"
                    + ("（缓存原获取时间）" if outcome.provenance.cached else "")
                )
            )
        )
    if query.intent == "listings":
        blocks.append(
            TextBlock(
                "有限挂牌样本，最多 5 条，按每单位 Gil 排序；数量为该条堆叠，不是全服库存或完整最低价排行。"
            )
        )
        for listing in listings:
            blocks.append(
                TextBlock(
                    f"{listing.region} {'HQ' if listing.hq else 'NQ'} 有限挂牌样本（最多 5 条）：{listing.price_per_unit} Gil/单位 × {listing.quantity}；{quote_context(listing.region, listing.world_id, listing.uploaded_at)}；来源最近审核 {_age(listing.reviewed_at, observed_at)}；不代表上架时间"
                )
            )
        if truncated:
            blocks.append(
                TextBlock("仅展示有限前缀，结果已截断；不能据此判断其余区域/挂牌。")
            )
    elif query.intent == "min":
        blocks.append(
            TextBlock(
                "按品质列出本次返回数据中的最低挂牌；来源未提供该条数量与上架时间，World 上传不代表挂牌时间。"
            )
        )
        for quality, region, quote in minimums:
            blocks.append(
                TextBlock(
                    f"{quality.upper()} 本次返回数据中的最低挂牌：{_price(quote.minimum)} Gil/单位，{region} / {quote_context(region, quote.minimum_world, quote.minimum_uploaded_at)}"
                )
            )
        for quality in ("nq", "hq") if query.quality == "all" else (query.quality,):
            if not any(row[0] == quality for row in minimums):
                blocks.append(
                    TextBlock(
                        f"{quality.upper()}：最低挂牌未知，不能补零或以成交价替代。"
                    )
                )
        if not minimums:
            blocks.append(
                TextBlock("最低挂牌指标缺失；其它成交指标存在，不能补零或当作挂牌价。")
            )
    else:
        for outcome in usable:
            for quote in outcome.data.quotes:
                blocks.append(
                    TextBlock(
                        f"{outcome.region} {quote.quality.upper()}：本次返回数据中的最低挂牌 {_price(quote.minimum)} Gil/单位；{quote_context(outcome.region, quote.minimum_world, quote.minimum_uploaded_at)}；World 上传不代表挂牌时间；最近成交 {_price(quote.recent_purchase)} Gil/单位（成交时间 {_age(quote.recent_at, observed_at)}）；最近四天成交均价 {_price(quote.average_sale_price)}；最近四天日均售出数量 {_price(quote.daily_sale_velocity)}。"
                    )
                )
    blocks.append(
        TextBlock(
            "缺值写未知，不补零；源更新时间只涉及所列 World，不证明整个区域全部挂牌新鲜。"
        )
    )
    blocks.append(
        LinksBlock(
            tuple(
                Link(f"Universalis {outcome.region}", outcome.provenance.url)
                for outcome in usable
            ),
            fallback_text="来源：Universalis。",
        )
    )
    document = DisplayDocument(
        title="FF14 市场查询",
        subject=f"FF14 市场 {query.item_id}",
        ordered_blocks=tuple(blocks),
        sources=("Universalis",),
        privacy=Privacy.PUBLIC,
    )
    return CapabilityResult(
        f"ff14-market-{query.item_id}",
        ResultStatus.PARTIAL_SUCCESS if partial else ResultStatus.SUCCESS,
        document=document,
        privacy=Privacy.PUBLIC,
        warnings=("部分覆盖或指标缺失；不承诺完整全服最低价。",) if partial else (),
    )


class MarketClient:
    def __init__(self, session: MarketSession, catalog: CatalogSnapshot) -> None:
        self.session, self.catalog = session, catalog

    async def execute(self, query: MarketQuery) -> MarketExecution:
        plan = request_plan(query)
        if any(region not in SUPPORTED_REGIONS for region in query.scope.regions):
            raise QueryResolutionError("市场范围尚未支持。", ErrorCode.UNSUPPORTED)
        targets = tuple(str(target) for target in plan.scopes)
        if query.scope.kind in ("world", "dc"):
            targets = (self.catalog.target(query.scope.kind, query.scope.target),)
            entry = self.catalog.catalog.find(query.scope.kind, query.scope.target)
            if query.scope.regions != (entry.region,):
                raise QueryResolutionError("目录与查询范围不一致。")
        if len(targets) > 4 or len(set(targets)) != len(targets):
            raise QueryResolutionError("市场请求范围无效。")
        regions = query.scope.regions
        catalog_regions = {
            entry.region for entry in self.catalog.catalog.entries if entry.kind == "dc"
        }
        if any(region not in catalog_regions for region in regions):
            raise QueryResolutionError(
                "目录未覆盖所需区域，不能缩小查询范围。", ErrorCode.UPSTREAM_ERROR
            )
        if len(targets) != len(regions):
            raise QueryResolutionError("市场请求范围无效。")

        async def one(target: str, region: str) -> ScopeOutcome:
            path = f"/api/v2/{'aggregated/' if plan.endpoint == 'aggregated' else ''}{target}/{plan.item_id}"
            parameters = plan.parameters
            if plan.endpoint != "aggregated":
                parameters += (
                    (
                        "fields",
                        "itemID,worldID,dcName,regionName,hasData,lastUploadTime,worldUploadTimes,listings.pricePerUnit,listings.quantity,listings.hq,listings.worldID,listings.lastReviewTime",
                    ),
                )
            parser = (
                parse_aggregated if plan.endpoint == "aggregated" else parse_listings
            )
            cleaner = (
                (lambda payload: _clean_aggregated(payload, query))
                if plan.endpoint == "aggregated"
                else _clean_listings
            )
            try:
                data, provenance = await self.session.read(
                    path,
                    parameters,
                    lambda payload: parser(payload, query, region, self.catalog),
                    clean=cleaner,
                    key_context=(
                        query.parser_version,
                        query.scope.kind,
                        query.quality,
                        query.intent,
                    ),
                )
                return ScopeOutcome(region, target, data, provenance)
            except (
                MarketPayloadError,
                MarketSourceIncompleteError,
                MarketDeadlineError,
                SourceHttpError,
                PermissionError,
            ) as exc:
                return _diagnostic(exc, region, target)

        work = [
            asyncio.create_task(one(target, region))
            for target, region in zip(targets, regions, strict=True)
        ]
        try:
            outcomes = tuple(await asyncio.gather(*work))
        finally:
            for task in work:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*work, return_exceptions=True)
        rows = [
            row for outcome in outcomes if outcome.data for row in outcome.data.listings
        ]
        rows.sort(
            key=lambda row: (
                row.price_per_unit,
                row.world_id if row.world_id is not None else 2_147_483_648,
                row.region,
                row.hq,
                row.quantity,
                row.ordinal,
            )
        )
        listings = tuple(rows[:MAX_LISTINGS])
        truncated = len(rows) > MAX_LISTINGS or any(
            outcome.data.truncated for outcome in outcomes if outcome.data
        )
        minimums = []
        for quality in ("nq", "hq") if query.quality == "all" else (query.quality,):
            candidates = [
                (
                    quote.minimum,
                    quote.minimum_world or 2_147_483_648,
                    outcome.region,
                    quote,
                )
                for outcome in outcomes
                if outcome.data
                for quote in outcome.data.quotes
                if quote.quality == quality and quote.minimum is not None
            ]
            if candidates:
                _, _, region, quote = min(candidates, key=lambda value: value[:3])
                minimums.append((quality, region, quote))
        minimums = tuple(minimums)
        observed_at = self.session.wall_clock()
        result = _result(
            query, outcomes, listings, minimums, truncated, observed_at, self.catalog
        )
        return MarketExecution(
            query,
            outcomes,
            listings,
            minimums,
            truncated,
            result,
            observed_at,
            self.catalog,
        )
