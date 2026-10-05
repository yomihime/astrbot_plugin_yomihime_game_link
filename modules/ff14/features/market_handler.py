"""Proof-bound command/page market handler over the Q1 and S1 contracts."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import replace
from time import monotonic

from yomihime_sdk.api.contexts import InvocationOrigin, InvocationView
from yomihime_sdk.api.display import DisplayDocument, Privacy, TextBlock
from yomihime_sdk.api.results import (
    CapabilityResult,
    ErrorCode,
    ErrorDetail,
    FactDocument,
    ResultStatus,
)
from yomihime_sdk.api.services import ModuleServices, SourceHttpError

from ..query_resolution import (
    CandidateBatch,
    CandidateRegistry,
    MarketQuery,
    MarketQueryResolver,
    QueryCoordinator,
    QueryResolutionError,
    QueryTicket,
    TrustedOwner,
)
from .item_sources import ItemPayloadError, ItemSourceClient
from .market import MarketClient, _diagnostic
from .market_sources import MarketDeadlineError, MarketPayloadError, MarketSourceClient


def _owner(context: InvocationView) -> TrustedOwner:
    if context.origin is InvocationOrigin.WEB_PUBLIC and context.public_session_id:
        # Authenticated bearer-session grouping, not a Core actor or tab identity.
        return TrustedOwner(
            context.public_session_id, context.public_session_id, "web_public"
        )
    if context.origin is InvocationOrigin.COMMAND and all(
        (context.actor_id, context.conversation_id, context.adapter_id)
    ):
        return TrustedOwner(
            context.actor_id, context.conversation_id, context.adapter_id
        )
    raise QueryResolutionError(
        "缺少可信查询会话，无法续接候选。", ErrorCode.UNSUPPORTED
    )


def _query_facts(query: MarketQuery) -> dict:
    return dict(
        query=query.query,
        scope=dict(
            kind=query.scope.kind,
            target=query.scope.target,
            regions=query.scope.regions,
            source=query.scope.source,
        ),
        quality=query.quality,
        intent=query.intent,
        module_revision=query.module_revision,
        core_revision=query.core_revision,
        coverage=[],
        truncated=False,
    )


def _error(code: ErrorCode, message: str) -> CapabilityResult:
    return CapabilityResult(
        "ff14-market-query",
        ResultStatus.ERROR,
        error=ErrorDetail(code, message),
        privacy=Privacy.PUBLIC,
    )


def _unique_object(pairs):
    values = {}
    for key, value in pairs:
        if key in values:
            raise ValueError
        values[key] = value
    return values


class MarketQueryHandler:
    """One module-local candidate table and two-slot market source gate."""

    def __init__(self, services: ModuleServices, *, clock=monotonic) -> None:
        self.services, self.clock = services, clock
        self.registry = CandidateRegistry(clock=clock)
        # Only start_bound is used: each query gets freshly authorized ports.
        self.source = MarketSourceClient(None, clock=clock)

    async def invoke(
        self, context: InvocationView, parameters: Mapping
    ) -> CapabilityResult:
        started = self.clock()
        deadline = min(started + 30, context.deadline or started + 30)
        ticket = None
        try:
            owner = _owner(context)
            if isinstance(parameters, Mapping) and set(parameters) == {"input"}:
                try:
                    parameters = json.loads(
                        parameters["input"],
                        object_pairs_hook=_unique_object,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                    )
                    if not isinstance(parameters, dict):
                        raise ValueError
                except (ValueError, TypeError, RecursionError):
                    self.registry.begin(owner)
                    raise QueryResolutionError("查询输入必须为对象。") from None
            if isinstance(parameters, Mapping) and set(parameters) == {"command"}:
                raw = parameters["command"]
                if isinstance(raw, str) and raw.startswith("choose "):
                    parts = raw.split()
                    if (
                        len(parts) != 4
                        or not parts[3].isascii()
                        or not parts[3].isdigit()
                    ):
                        raise QueryResolutionError(
                            "选择用法：choose <批次> <generation> <物品ID>。"
                        )
                    parameters = {
                        "selection": dict(
                            batch_id=parts[1],
                            generation=parts[2],
                            item_id=int(parts[3]),
                        )
                    }
            selecting = isinstance(parameters, Mapping) and set(parameters) == {
                "selection"
            }
            if not selecting:
                ticket = self.registry.begin(owner)  # Before bind/config/catalog await.
            async with asyncio.timeout(max(0, deadline - self.clock())):
                scope = await self.services.scopes.bind(context)
                session = self.source.start_bound(
                    scope.http, scope.cache, deadline=deadline
                )
                if selecting:
                    if set(parameters) != {"selection"}:
                        raise QueryResolutionError("候选选择不能同时提交新查询。")
                    choice = parameters["selection"]
                    if not isinstance(choice, Mapping) or set(choice) != {
                        "batch_id",
                        "generation",
                        "item_id",
                    }:
                        raise QueryResolutionError("候选选择参数无效。")
                    query = self.registry.choose(
                        owner,
                        choice["batch_id"],
                        choice["generation"],
                        choice["item_id"],
                        retain=True,
                    )
                    ticket = QueryTicket(owner, choice["generation"])
                    catalog = await session.catalog()
                else:
                    catalog = await session.catalog()
                    coordinator = QueryCoordinator(
                        MarketQueryResolver(catalog.catalog), self.registry
                    )
                    entry, values = "structured", parameters
                    if isinstance(parameters, Mapping) and "command" in parameters:
                        if context.origin is not InvocationOrigin.COMMAND or set(
                            parameters
                        ) != {"command"}:
                            raise QueryResolutionError("命令输入参数无效。")
                        values = parameters["command"]
                        if not isinstance(values, str):
                            raise QueryResolutionError("命令参数必须为文本。")
                        entry = (
                            "natural"
                            if (
                                values.startswith("查一下")
                                or values.endswith("多少钱")
                                or values.endswith("什么价")
                                or values.endswith("哪里最便宜")
                            )
                            else "command"
                        )
                    resolved = await session.run(
                        coordinator.resolve(
                            owner,
                            values,
                            self.services,
                            ItemSourceClient(scope.http),
                            entry=entry,
                            ticket=ticket,
                            retain_until_result=True,
                        )
                    )
                    if isinstance(resolved, CandidateBatch):
                        facts = _query_facts(resolved.query)
                        candidates = [
                            dict(item_id=item.item_id, name=item.name)
                            for item in resolved.candidates
                        ]
                        selection = dict(
                            kind="item",
                            batch_id=resolved.batch_id,
                            generation=resolved.generation,
                            candidates=candidates,
                            truncated=resolved.truncated,
                        )
                        blocks = [
                            TextBlock(
                                "请选择物品；范围、品质与配置版本保持本次查询上下文。"
                            )
                        ]
                        blocks.extend(
                            TextBlock(f"{item['item_id']}：{item['name']}")
                            for item in candidates
                        )
                        if context.origin is InvocationOrigin.COMMAND:
                            blocks.append(
                                TextBlock(
                                    f"/ygl ff14 market choose {resolved.batch_id} {resolved.generation} <物品ID>"
                                )
                            )
                        self.registry._current(ticket)
                        return CapabilityResult(
                            "ff14-market-selection",
                            ResultStatus.NEEDS_SELECTION,
                            DisplayDocument(
                                "FF14 市场物品候选",
                                resolved.query.query,
                                tuple(blocks),
                                sources=tuple(p.url for p in catalog.provenance),
                            ),
                            model_facts=FactDocument(
                                {"market": facts, "selection": selection}
                            ),
                        )
                    query = resolved
                execution = await MarketClient(session, catalog).execute(query)
                self.registry.finish(
                    ticket, query
                )  # Fence late market results before publishing.
                result = execution.result
                if result.status is ResultStatus.ERROR:
                    return result
                facts = _query_facts(query)
                facts["truncated"] = execution.truncated
                facts["coverage"] = [
                    dict(
                        region=o.region,
                        target=o.target,
                        state="failed"
                        if o.failed
                        else "available"
                        if o.data.has_data
                        else "empty",
                        stage=o.stage if o.failed else None,
                        reason=o.reason,
                        status_code=o.status_code,
                        cached=o.provenance.cached if o.provenance else None,
                        fetched_at=o.provenance.fetched_at.isoformat()
                        if o.provenance
                        else None,
                    )
                    for o in execution.outcomes
                ]
                return replace(result, model_facts=FactDocument({"market": facts}))
        except QueryResolutionError as exc:
            return _error(exc.code, str(exc))
        except ItemPayloadError:
            return _error(ErrorCode.UNPARSED, "物品候选来源格式无法识别。")
        except (
            MarketPayloadError,
            MarketDeadlineError,
            SourceHttpError,
            PermissionError,
        ) as exc:
            outcome = _diagnostic(exc, "", "")
            return _error(outcome.code, "市场查询来源未完成，请稍后重试。")
        except TimeoutError:
            return _error(ErrorCode.UPSTREAM_ERROR, "市场查询达到总时间限制。")

    def close(self) -> None:
        self.registry = CandidateRegistry(clock=self.clock)
