"""Proof-bound command/page market handler over the Q1 and S1 contracts."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from decimal import Decimal
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
from .market_models import MarketExecution
from .market_sources import MarketDeadlineError, MarketPayloadError, MarketSourceClient


def _stamp(stamp, observed):
    return dict(
        source_time=stamp.isoformat() if stamp else None,
        age_seconds=(observed - stamp).total_seconds() if stamp else None,
        future_time=stamp > observed if stamp else None,
    )


def _world_name(execution: MarketExecution, world_id, region):
    """Use only the execution's validated snapshot and exact numeric identity."""
    catalog, scope = execution.catalog, execution.query.scope
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


def _quote_context(execution: MarketExecution, world_id, region):
    name = _world_name(execution, world_id, region)
    provenance = next(
        (
            row.provenance
            for row in execution.outcomes
            if row.region == region and not row.failed
        ),
        None,
    )
    return dict(
        world_name=name,
        world_name_state="verified" if name is not None else "unknown",
        source=dict(
            provider="Universalis",
            url=provenance.url if provenance else None,
            fetched_at=provenance.fetched_at.isoformat() if provenance else None,
            fetched_age_seconds=(
                execution.observed_at - provenance.fetched_at
            ).total_seconds()
            if provenance
            else None,
            cached=provenance.cached if provenance else None,
        ),
        limitation="仅本次返回范围/有限样本；非全部 World 覆盖，非实时可买；Gil/件。",
    )


def _execution_facts(execution: MarketExecution) -> dict:
    """Public typed prices from execution values, never display text or raw HTTP."""
    query, observed = execution.query, execution.observed_at
    facts = _query_facts(query)
    facts.update(
        fact_projection_version=2,
        answer_guidance="成功或可用 partial 足够时直接回答；来源与范围/非实时限定紧邻价格，缺值写未知。无新用户范围不补查服务器；不推断 HQ 用途、补货或购买建议。",
        item_id=query.item_id,
        item_name=query.item_name,
        original_query=query.original_query or query.query,
        currency="Gil",
        price_unit="per_item",
        observed_at=observed.isoformat(),
        truncated=execution.truncated,
        partial=execution.result.status is ResultStatus.PARTIAL_SUCCESS,
    )
    facts["limitations"] = dict(
        complete_world_coverage=False,
        realtime_availability=False,
        minimum_scope="available_returned_scopes",
        listings_scope="bounded_returned_sample",
        listings_limit=5,
        failed_regions=[
            outcome.region for outcome in execution.outcomes if outcome.failed
        ],
        empty_regions=[
            outcome.region
            for outcome in execution.outcomes
            if outcome.data and not outcome.data.has_data
        ],
    )
    facts["coverage_complete"] = not any(
        outcome.failed
        or not outcome.data
        or not outcome.data.has_data
        or outcome.data.incomplete
        for outcome in execution.outcomes
    )
    facts["warnings"] = [warning[:256] for warning in execution.result.warnings[:8]]
    coverage = []
    for outcome in execution.outcomes[:4]:
        data, provenance = outcome.data, outcome.provenance
        quotes = []
        for quote in data.quotes[:2] if data else ():
            projected = dict(
                quality=quote.quality,
                minimum=quote.minimum,
                minimum_world_id=quote.minimum_world,
                minimum_time=_stamp(quote.minimum_uploaded_at, observed),
                recent_purchase=quote.recent_purchase,
                recent_world_id=quote.recent_world,
                recent_time=_stamp(quote.recent_at, observed),
                average_sale_price=quote.average_sale_price,
                daily_sale_velocity=quote.daily_sale_velocity,
                missing=[
                    name
                    for name in (
                        "minimum",
                        "recent_purchase",
                        "average_sale_price",
                        "daily_sale_velocity",
                    )
                    if getattr(quote, name) is None
                ],
            )
            for index, (quality, region, minimum) in enumerate(execution.minimums[:2]):
                if (
                    quality == quote.quality
                    and region == outcome.region
                    and minimum.minimum == quote.minimum
                    and minimum.minimum_world == quote.minimum_world
                    and minimum.minimum_uploaded_at == quote.minimum_uploaded_at
                ):
                    # v2: exact duplicate minimum triplet lives only in minimums.
                    for key in ("minimum", "minimum_world_id", "minimum_time"):
                        del projected[key]
                    projected["minimum_ref"] = f"market.minimums[{index}]"
                    break
            quotes.append(projected)
        coverage.append(
            dict(
                region=outcome.region,
                target=outcome.target,
                state="failed"
                if outcome.failed
                else "available"
                if data and data.has_data
                else "empty",
                stage=outcome.stage if outcome.failed else None,
                reason=outcome.reason,
                status_code=outcome.status_code,
                cached=provenance.cached if provenance else None,
                fetched_at=provenance.fetched_at.isoformat() if provenance else None,
                fetched_age_seconds=(observed - provenance.fetched_at).total_seconds()
                if provenance
                else None,
                source=provenance.url if provenance else None,
                uploaded=_stamp(data.uploaded_at if data else None, observed),
                incomplete=data.incomplete if data else None,
                truncated=data.truncated if data else None,
                quotes=quotes,
            )
        )
    facts["coverage"] = coverage
    facts["minimums"] = [
        dict(
            quality=quality,
            region=region,
            price_per_unit=quote.minimum,
            world_id=quote.minimum_world,
            time=_stamp(quote.minimum_uploaded_at, observed),
            **_quote_context(execution, quote.minimum_world, region),
        )
        for quality, region, quote in execution.minimums[:2]
    ]
    facts["listings"] = [
        dict(
            price_per_unit=row.price_per_unit,
            quantity=row.quantity,
            quality="hq" if row.hq else "nq",
            world_id=row.world_id,
            region=row.region,
            reviewed=_stamp(row.reviewed_at, observed),
            uploaded=_stamp(row.uploaded_at, observed),
            **_quote_context(execution, row.world_id, row.region),
        )
        for row in execution.listings[:5]
    ]
    return facts


def _fact_values(value):
    if type(value) is float:
        return Decimal(str(value))
    if isinstance(value, Mapping):
        return {key: _fact_values(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return tuple(_fact_values(child) for child in value)
    return value


def _tool_facts(result: CapabilityResult, facts: dict | None = None) -> FactDocument:
    return FactDocument(
        _fact_values(
            dict(
                status=result.status.value,
                error=dict(code=result.error.code.value, message=result.error.message)
                if result.error
                else None,
                **(facts or {}),
            )
        )
    )


def _owner(context: InvocationView) -> TrustedOwner:
    if context.origin is InvocationOrigin.WEB_PUBLIC and context.public_session_id:
        # Authenticated bearer-session grouping, not a Core actor or tab identity.
        return TrustedOwner(
            context.public_session_id, context.public_session_id, "web_public"
        )
    if context.origin in (InvocationOrigin.COMMAND, InvocationOrigin.LLM_TOOL) and all(
        (context.actor_id, context.conversation_id, context.adapter_id)
    ):
        return TrustedOwner(
            context.actor_id,
            context.conversation_id,
            f"llm_tool:{context.adapter_id}"
            if context.origin is InvocationOrigin.LLM_TOOL
            else context.adapter_id,
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


def _error(code: ErrorCode, message: str, *, tool: bool = False) -> CapabilityResult:
    if tool:
        message += " 请等待用户明确新指令，不要自行换参数或改 query 绕过确认。"
    result = CapabilityResult(
        "ff14-market-query",
        ResultStatus.ERROR,
        error=ErrorDetail(code, message),
        privacy=Privacy.PUBLIC,
    )
    return replace(result, model_facts=_tool_facts(result)) if tool else result


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
        self._tool_event = ContextVar("ff14_trusted_tool_event", default=None)

    @contextmanager
    def _bind_tool_event(self, event, text, actor_id, conversation_id, adapter_id):
        """Internal runtime binding, absent from SDK/model parameters."""
        owner = TrustedOwner(actor_id, conversation_id, f"llm_tool:{adapter_id}")
        token = self._tool_event.set((owner, event, text))
        try:
            yield
        finally:
            self._tool_event.reset(token)

    async def invoke(
        self, context: InvocationView, parameters: Mapping
    ) -> CapabilityResult:
        started = self.clock()
        deadline = min(started + 30, context.deadline or started + 30)
        ticket = None

        def failure(code, message):
            return _error(
                code, message, tool=context.origin is InvocationOrigin.LLM_TOOL
            )

        try:
            owner = _owner(context)
            evidence = self._tool_event.get()
            if context.origin is InvocationOrigin.LLM_TOOL and (
                evidence is None or evidence[0] != owner or type(evidence[2]) is not str
            ):
                raise QueryResolutionError(
                    "缺少本次真实用户消息绑定。", ErrorCode.UNSUPPORTED
                )
            if context.capability_id == "ff14.market.select":
                parameters = {"selection": parameters}
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
                if context.origin is InvocationOrigin.LLM_TOOL:
                    retry = self.registry.tool_query(
                        owner, evidence[1], evidence[2], parameters.get("query", "")
                    )
                    if retry is not None:
                        return self._candidates(context, retry)
                ticket = self.registry.begin(
                    owner,
                    source_event=evidence[1]
                    if context.origin is InvocationOrigin.LLM_TOOL
                    else None,
                )  # Before bind/config/catalog await.
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
                    choose = (
                        self.registry.choose_confirmed
                        if context.origin is InvocationOrigin.LLM_TOOL
                        else self.registry.choose
                    )
                    query = choose(
                        owner,
                        choice["batch_id"],
                        choice["generation"],
                        choice["item_id"],
                        retain=True,
                        **(
                            {"event": evidence[1], "text": evidence[2]}
                            if context.origin is InvocationOrigin.LLM_TOOL
                            else {}
                        ),
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
                        self.registry._current(ticket)
                        return self._candidates(
                            context,
                            resolved,
                            sources=tuple(p.url for p in catalog.provenance),
                        )
                    query = resolved
                execution = await MarketClient(session, catalog).execute(query)
                self.registry.finish(
                    ticket, query
                )  # Fence late market results before publishing.
                result = execution.result
                if context.origin is InvocationOrigin.LLM_TOOL and result.error:
                    result = replace(
                        result,
                        error=ErrorDetail(
                            result.error.code,
                            result.error.message
                            + " 请等待用户明确新指令，不要自行换参数或改 query 绕过确认。",
                        ),
                    )
                facts = _execution_facts(execution)
                if context.origin is not InvocationOrigin.LLM_TOOL:
                    if result.status is ResultStatus.ERROR:
                        return result
                    public = _query_facts(query)
                    public["truncated"] = execution.truncated
                    public["coverage"] = [
                        {
                            key: row[key]
                            for key in (
                                "region",
                                "target",
                                "state",
                                "stage",
                                "reason",
                                "status_code",
                                "cached",
                                "fetched_at",
                            )
                        }
                        for row in facts["coverage"]
                    ]
                    return replace(result, model_facts=FactDocument({"market": public}))
                return replace(
                    result, model_facts=_tool_facts(result, {"market": facts})
                )
        except QueryResolutionError as exc:
            return failure(exc.code, str(exc))
        except ItemPayloadError:
            return failure(ErrorCode.UNPARSED, "物品候选来源格式无法识别。")
        except (
            MarketPayloadError,
            MarketDeadlineError,
            SourceHttpError,
            PermissionError,
        ) as exc:
            outcome = _diagnostic(exc, "", "")
            return failure(outcome.code, "市场查询来源未完成，请稍后重试。")
        except TimeoutError:
            return failure(ErrorCode.UPSTREAM_ERROR, "市场查询达到总时间限制。")
        finally:
            if ticket is not None:
                self.registry._release_confirmed(ticket)

    def _candidates(self, context, resolved, *, sources=()):
        facts = _query_facts(resolved.query)
        candidates = [
            dict(item_id=item.item_id, name=item.name) for item in resolved.candidates
        ]
        selection = dict(
            kind="item",
            batch_id=resolved.batch_id,
            generation=resolved.generation,
            candidates=candidates,
            truncated=resolved.truncated,
        )
        blocks = [TextBlock("请选择物品；范围、品质与配置版本保持本次查询上下文。")]
        blocks.extend(
            TextBlock(f"{item['item_id']}：{item['name']}") for item in candidates
        )
        if context.origin is InvocationOrigin.COMMAND:
            blocks.append(
                TextBlock(
                    f"/ygl ff14 market choose {resolved.batch_id} {resolved.generation} <物品ID>"
                )
            )
        elif context.origin is InvocationOrigin.LLM_TOOL:
            blocks.append(
                TextBlock(
                    "请回复唯一完整名称或“完整名称那个”；也可回复“选择物品 <ID>”。暂不支持序号。"
                )
            )
        return CapabilityResult(
            "ff14-market-selection",
            ResultStatus.NEEDS_SELECTION,
            DisplayDocument(
                "FF14 市场物品候选",
                resolved.query.query,
                tuple(blocks),
                sources=sources,
            ),
            model_facts=FactDocument(
                dict(
                    status="needs_selection",
                    error=None,
                    market=facts,
                    selection=selection,
                    user_confirmation="回复唯一完整名称或“完整名称那个”；暂不支持序号",
                    id_confirmation="选择物品 <ID>（兼容方式）",
                    name_confirmation="回复唯一完整名称或“完整名称那个”；暂不支持序号",
                )
                if context.origin is InvocationOrigin.LLM_TOOL
                else {"market": facts, "selection": selection}
            ),
        )

    def close(self) -> None:
        self.registry = CandidateRegistry(clock=self.clock)
