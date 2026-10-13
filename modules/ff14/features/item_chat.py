"""Message-bound item tools over the existing item source and result contracts."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from dataclasses import replace
from time import monotonic

from yomihime_game_link_sdk.contexts import InvocationOrigin
from yomihime_game_link_sdk.display import DisplayDocument, TextBlock
from yomihime_game_link_sdk.errors import ServiceUnavailable, SourceHttpError
from yomihime_game_link_sdk.results import (
    CapabilityResult,
    ErrorCode,
    FactDocument,
    ResultStatus,
)

from ..query_resolution import (
    CandidateRegistry,
    ItemQuery,
    QueryResolutionError,
    QueryTicket,
    TrustedOwner,
    validate_tool_query,
)
from .item_resolution import (
    item_id_from_query,
    search_item_candidates,
    validate_item_query,
)
from .item_sources import ItemPayloadError, ItemSourceClient
from .items import _error, _item_result, _source_error

ITEM_SELECT = "ff14.item.select"
ROUTE_LIMIT = 6
COST_LIMIT = 4
DETAIL_LIMIT = 5


def _query_evidence(value, text):
    """Item ID evidence has an item grammar, independent of market intents."""
    try:
        query = validate_item_query(value)
    except ValueError as exc:
        raise QueryResolutionError(str(exc)) from exc
    if item_id_from_query(query) is None:
        return validate_tool_query(query, text)
    token = re.escape(str(int(query)))
    if not (
        re.search(
            r"(?:物品\s*ID|(?<![a-z0-9_])item\s+ID)\s*[:：#]?\s*"
            + token
            + r"(?![0-9])",
            text,
            re.I,
        )
        or re.fullmatch(
            r"\s*(?:查物品|查询物品|查一下|查询|查)?\s*"
            + token
            + r"\s*(?:的?(?:信息|详情|获取途径|怎么获得))?[。.!！]?\s*",
            text,
        )
    ):
        raise QueryResolutionError(
            "物品 ID 需要本条真实用户消息明确提供。", ErrorCode.UNSUPPORTED
        )
    return query


def _facts(result, values=None, *, sources=()):
    facts = {"status": result.status.value, "error": None}
    if result.error:
        # Generic ERROR projection only; no FF14 whitelist added to Core.
        facts["error"] = {
            "code": result.error.code.value,
            "message": result.error.message,
        }
    else:
        facts.update(values or {})
    return replace(result, model_facts=FactDocument(facts, sources=tuple(sources)))


def _record_facts(record, query, *, partial):
    """Bound typed source values; never reparse a display document into facts.

    Six routes x four costs keeps the key/value traversal below Core's 512-node
    budget even at source limits. Every projection omission is explicit.
    """
    routes = []
    truncated = len(record.routes) > ROUTE_LIMIT
    for route in record.routes[:ROUTE_LIMIT]:
        omitted = len(route.costs) > COST_LIMIT or len(route.details) > DETAIL_LIMIT
        truncated |= omitted
        routes.append(
            {
                "kind": route.kind.value,
                "label": route.label,
                "details": list(route.details[:DETAIL_LIMIT]),
                "costs": [
                    {"item_id": cost.item_id, "amount": cost.amount, "name": cost.name}
                    for cost in route.costs[:COST_LIMIT]
                ],
                "partial": route.partial,
                "truncated": omitted,
            }
        )
    return {
        "query": query.original_query or query.query,
        "item_id": record.item_id,
        "name": record.name,
        "description": record.description,
        "item_level": record.item_level,
        "equip_level": record.equip_level,
        "missing": [
            field
            for field in ("description", "item_level", "equip_level")
            if getattr(record, field) is None
        ],
        "routes": routes,
        "routes_state": "available" if record.routes else "unknown",
        "routes_complete": False,
        "routes_truncated": truncated,
        "partial": partial or truncated,
        "warnings": list(record.warnings[:8]),
        "warnings_truncated": len(record.warnings) > 8,
        "sources": [
            {"provider": "XIVAPI-compatible (xivcdn)", "url": record.source_urls[0]},
            {"provider": "Garland Tools CN", "url": record.source_urls[1]},
        ],
        "answer_guidance": (
            "仅依据本次物品字段与有限获取途径回答，注明来源。None与missing表示未知，"
            "不得补零、推断不存在/不可获得、用途或价格。routes不代表全部获取途径；"
            "partial、truncated及warnings需保留。成功或可用partial足够时直接回答。"
        ),
    }


class ItemChat:
    """One item-only ephemeral registry, closed with its module lifecycle."""

    def __init__(self, services, *, clock=monotonic):
        self.services, self.clock = services, clock
        self.registry = CandidateRegistry(clock=clock)

    def close(self):
        self.registry = CandidateRegistry(clock=self.clock)

    async def invoke(self, context, parameters):
        ticket = None
        published = False
        operation = "search"

        def failure(code, message):
            return _facts(
                _error(
                    code, message + " 请等待用户明确新指令，不要改 query 绕过候选确认。"
                )
            )

        try:
            if context.origin is not InvocationOrigin.LLM_TOOL:
                raise QueryResolutionError(
                    "物品选择工具仅支持聊天入口。", ErrorCode.UNSUPPORTED
                )
            if not all((context.actor_id, context.conversation_id, context.adapter_id)):
                raise QueryResolutionError(
                    "缺少可信用户/会话绑定。", ErrorCode.UNSUPPORTED
                )
            owner = TrustedOwner(
                context.actor_id,
                context.conversation_id,
                f"llm_tool:{context.adapter_id}",
            )
            started = self.clock()
            deadline = min(
                started + 30,
                context.deadline if context.deadline is not None else started + 30,
            )
            if self.clock() >= deadline:
                raise TimeoutError
            async with asyncio.timeout(max(0, deadline - self.clock())):
                scope = await self.services.scopes.bind(context)
                message = await scope.message.read()

                async def guard():
                    if self.clock() >= deadline:
                        raise TimeoutError
                    if await scope.message.read() != message:
                        raise PermissionError("message context changed")
                    if ticket is not None:
                        self.registry._current(ticket)

                source = ItemSourceClient(scope.http)
                if context.capability_id == ITEM_SELECT:
                    if not isinstance(parameters, Mapping) or set(parameters) != {
                        "batch_id",
                        "generation",
                        "item_id",
                    }:
                        raise QueryResolutionError("候选选择参数无效。")
                    query = self.registry.choose_confirmed(
                        owner,
                        parameters["batch_id"],
                        parameters["generation"],
                        parameters["item_id"],
                        event_ref=message.event_ref,
                        text=message.text,
                        retain=True,
                    )
                    ticket = QueryTicket(owner, parameters["generation"])
                else:
                    if not isinstance(parameters, Mapping) or set(parameters) != {
                        "query"
                    }:
                        raise QueryResolutionError("物品查询只接受 query。")
                    query_text = _query_evidence(parameters["query"], message.text)
                    retry = self.registry.tool_query(
                        owner, message.event_ref, message.text, query_text
                    )
                    if retry is not None:
                        ticket = QueryTicket(owner, retry.generation)
                        await guard()
                        published = True
                        return self._candidates(retry)
                    ticket = self.registry.begin(owner, source_ref=message.event_ref)
                    query = ItemQuery(query_text, item_id_from_query(query_text))
                    if query.item_id is None:
                        candidates, truncated = await search_item_candidates(
                            source, query.query
                        )
                        await guard()
                        if not candidates and not truncated:
                            raise QueryResolutionError(
                                "没有找到匹配的物品。", ErrorCode.NOT_FOUND
                            )
                        if len(candidates) != 1 or truncated:
                            batch = self.registry.publish(
                                ticket, query, candidates, truncated
                            )
                            result = self._candidates(batch)
                            published = True
                            return result
                        candidate = candidates[0]
                        if candidate.item_id > 2_147_483_647:
                            raise QueryResolutionError(
                                "物品 ID 无效。", ErrorCode.UNPARSED
                            )
                        query = replace(
                            query, item_id=candidate.item_id, item_name=candidate.name
                        )
                operation = "item"
                snapshot = await source.lookup(query.item_id)
                await guard()
                values = _record_facts(snapshot.record, query, partial=snapshot.partial)
                result = _item_result(snapshot.record, partial=values["partial"])
                result = _facts(
                    result, {"item": values}, sources=snapshot.record.source_urls
                )
                self.registry.finish(ticket, query)
                return result
        except QueryResolutionError as exc:
            return failure(exc.code, str(exc))
        except SourceHttpError as exc:
            result = _source_error(exc, operation=operation)
            return failure(result.error.code, result.error.message)
        except ItemPayloadError:
            return failure(ErrorCode.UNPARSED, "物品来源格式无法识别。")
        except PermissionError:
            return failure(ErrorCode.UNSUPPORTED, "真实用户消息绑定已失效。")
        except ServiceUnavailable:
            return failure(ErrorCode.MODULE_UNAVAILABLE, "物品查询服务暂不可用。")
        except TimeoutError:
            return failure(ErrorCode.UPSTREAM_ERROR, "物品查询达到总时间限制。")
        finally:
            if ticket is not None and not published:
                self.registry.abandon(ticket)

    @staticmethod
    def _candidates(batch):
        candidates = [
            {"item_id": item.item_id, "name": item.name} for item in batch.candidates
        ]
        lines = [
            TextBlock(f"{item['name']}（ID {item['item_id']}）") for item in candidates
        ]
        if batch.truncated:
            lines.append(TextBlock("本次候选未穷尽，请明确名称或选择当前候选。"))
        lines.append(
            TextBlock("请回复唯一完整名称或“选择物品 <ID>”；疑问、否定和序号不能确认。")
        )
        result = CapabilityResult(
            "ff14-item-selection",
            ResultStatus.NEEDS_SELECTION,
            document=DisplayDocument(
                "FF14 物品候选",
                batch.query.query,
                tuple(lines),
                sources=("XIVAPI-compatible (xivcdn)",),
            ),
        )
        return _facts(
            result,
            {
                "item": {"query": batch.query.query},
                "selection": {
                    "kind": "item_lookup",
                    "batch_id": batch.batch_id,
                    "generation": batch.generation,
                    "candidates": candidates,
                    "truncated": batch.truncated,
                },
                "user_confirmation": (
                    "等待同一用户的新消息确认唯一完整名称，或“选择物品 <ID>”；"
                    "不要猜选、用查询工具绕过选择或调用市场选择工具。"
                ),
            },
        )
