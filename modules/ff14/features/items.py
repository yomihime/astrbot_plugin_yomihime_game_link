"""Command orchestration and public display for FF14 item lookup."""

from __future__ import annotations

from yomihime_game_link_sdk.contexts import InvocationView
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
from yomihime_game_link_sdk.services import ModuleServices
from yomihime_game_link_sdk.storage import JsonObject

from ..models import ItemCandidate, ItemRecord
from .item_resolution import (
    item_id_from_query,
    search_item_candidates,
    validate_item_query,
)
from .item_sources import ItemPayloadError, ItemSourceClient


class ItemLookup:
    """Invoke the source adapter and map outcomes into SDK display contracts."""

    def __init__(self, services: ModuleServices) -> None:
        self._services = services

    async def invoke(
        self, context: InvocationView, parameters: JsonObject
    ) -> CapabilityResult:
        try:
            query = validate_item_query(parameters.get("query"))
        except ValueError as exc:
            return _error(ErrorCode.PARAMETER_ERROR, str(exc))

        try:
            scope = await self._services.scopes.bind(context)
        except Exception:
            return _error(ErrorCode.MODULE_UNAVAILABLE, "物品查询服务暂不可用。")
        source = ItemSourceClient(scope.http)

        item_id = item_id_from_query(query)
        if item_id is not None:
            return await self._lookup_id(source, item_id)

        try:
            candidates, truncated = await search_item_candidates(source, query)
        except SourceHttpError as exc:
            return _source_error(exc, operation="search")
        except ItemPayloadError:
            return _error(ErrorCode.UNPARSED, "XIVAPI 返回的物品候选格式无法识别。")

        if not candidates and not truncated:
            return _error(ErrorCode.NOT_FOUND, "没有找到匹配的物品。")
        if len(candidates) != 1 or truncated:
            return _selection_result(candidates, truncated=truncated)
        return await self._lookup_id(source, candidates[0].item_id)

    async def _lookup_id(
        self, source: ItemSourceClient, item_id: int
    ) -> CapabilityResult:
        try:
            result = await source.lookup(item_id)
        except SourceHttpError as exc:
            return _source_error(exc, operation="item")
        except ItemPayloadError:
            return _error(ErrorCode.UNPARSED, "XIVAPI 返回的物品详情格式无法识别。")
        return _item_result(result.record, partial=result.partial)


def _item_result(record: ItemRecord, *, partial: bool) -> CapabilityResult:
    blocks = [
        TextBlock(f"物品 ID：{record.item_id}"),
        TextBlock(
            f"物品等级：{record.item_level if record.item_level is not None else '未提供'}；"
            f"装备等级：{record.equip_level if record.equip_level is not None else '未提供'}"
        ),
    ]
    if record.description:
        blocks.append(TextBlock(record.description))
    if record.routes:
        blocks.append(TextBlock("获取途径（有限展示，缺少关联不代表不可获得）："))
        for route in record.routes:
            completeness = "（部分信息）" if route.partial else ""
            blocks.append(
                TextBlock(f"• {route.label}{completeness}：{'；'.join(route.details)}")
            )
    else:
        blocks.append(
            TextBlock("Garland 当前未提供可解析的获取途径；这不代表物品不可获得。")
        )
    # Source routes are capped at 24. Keep the independently bounded warnings
    # together so a valid item stays within the public 32-block contract.
    if record.warnings:
        blocks.append(
            TextBlock("\n".join(f"提示：{warning}" for warning in record.warnings))
        )
    blocks.append(
        LinksBlock(
            (
                Link("XIVAPI-compatible 详情", record.source_urls[0]),
                Link("Garland Tools 国服详情", record.source_urls[1]),
            ),
            fallback_text="来源链接见 XIVAPI-compatible 与 Garland Tools。",
        )
    )
    document = DisplayDocument(
        title=record.name,
        subject=f"FF14 物品 {record.item_id}",
        ordered_blocks=tuple(blocks),
        sources=("XIVAPI-compatible (xivcdn)", "Garland Tools CN"),
        privacy=Privacy.PUBLIC,
    )
    return CapabilityResult(
        result_id=f"ff14-item-{record.item_id}",
        status=ResultStatus.PARTIAL_SUCCESS if partial else ResultStatus.SUCCESS,
        document=document,
        privacy=Privacy.PUBLIC,
        warnings=record.warnings,
    )


def _selection_result(
    candidates: tuple[ItemCandidate, ...], *, truncated: bool
) -> CapabilityResult:
    lines = [f"{candidate.name}（ID {candidate.item_id}）" for candidate in candidates]
    if truncated:
        lines.append("候选列表达到本次分页上限，结果未穷尽。")
    if candidates:
        lines.append("请复制目标物品的数字 ID 查询详情。")
    else:
        lines.append("候选列表未穷尽，请缩短或修改名称后重试；不能据此判断物品不存在。")
    document = DisplayDocument(
        title="物品候选",
        subject="名称查询需要选择物品 ID",
        ordered_blocks=tuple(TextBlock(line) for line in lines),
        sources=("XIVAPI-compatible (xivcdn)",),
        privacy=Privacy.PUBLIC,
    )
    return CapabilityResult(
        result_id="ff14-item-candidates",
        status=ResultStatus.NEEDS_SELECTION,
        document=document,
        privacy=Privacy.PUBLIC,
    )


def _source_error(error: SourceHttpError, *, operation: str) -> CapabilityResult:
    if error.code == "rate_limited" or error.status_code == 429:
        return _error(
            ErrorCode.RATE_LIMITED, "XIVAPI-compatible 请求频率受限，请稍后再试。"
        )
    if operation == "item" and error.status_code == 404:
        return _error(ErrorCode.NOT_FOUND, "未找到该物品 ID。")
    if error.code == "invalid_response":
        return _error(ErrorCode.UNPARSED, "XIVAPI-compatible 响应无法解析。")
    return _error(
        ErrorCode.UPSTREAM_ERROR,
        "XIVAPI-compatible 暂不可用，物品查询未完成。",
    )


def _error(code: ErrorCode, message: str) -> CapabilityResult:
    return CapabilityResult(
        result_id="ff14-item-error",
        status=ResultStatus.ERROR,
        privacy=Privacy.PUBLIC,
        error=ErrorDetail(code, message),
    )


__all__ = ["ItemLookup"]
