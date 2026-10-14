"""Proof-bound command/page market handler over the Q1 and S1 contracts."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal
from math import isfinite
from time import monotonic

from yomihime_game_link_sdk.contexts import InvocationOrigin, InvocationView
from yomihime_game_link_sdk.display import DisplayDocument, Privacy, TextBlock
from yomihime_game_link_sdk.errors import ServiceUnavailable, SourceHttpError
from yomihime_game_link_sdk.results import (
    CapabilityResult,
    ErrorCode,
    ErrorDetail,
    FactDocument,
    ResultStatus,
)
from yomihime_game_link_sdk.services import ModuleServices

from ..query_resolution import (
    CandidateBatch,
    CandidateRegistry,
    MarketQuery,
    MarketQueryResolver,
    QueryCoordinator,
    QueryResolutionError,
    QueryTicket,
    TrustedOwner,
    validate_tool_query,
)
from .chat_context import chat_owner, semantic_action
from .item_sources import ItemPayloadError, ItemSourceClient
from .market import MarketClient, _diagnostic
from .market import _world_name as _catalog_world_name
from .market_models import MarketExecution
from .market_scope_chat import MarketScopeChoices
from .market_sources import MarketDeadlineError, MarketPayloadError, MarketSourceClient
from .public_projection import validate_public_market_facts


def _stamp(stamp, observed):
    return dict(
        source_time=stamp.isoformat() if stamp else None,
        age_seconds=(observed - stamp).total_seconds() if stamp else None,
        future_time=stamp > observed if stamp else None,
    )


def _world_name(execution: MarketExecution, world_id, region):
    """Use only the execution's validated snapshot and exact numeric identity."""
    return _catalog_world_name(execution.catalog, execution.query, world_id, region)


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
        ),
    )


def _execution_facts(execution: MarketExecution) -> dict:
    """Public typed prices from execution values, never display text or raw HTTP."""
    query, observed = execution.query, execution.observed_at
    facts = _query_facts(query)
    facts["scope"]["default_basis"] = query.scope.default_basis
    facts.update(
        fact_projection_version=3,
        time_semantics=dict(
            minimum="world_upload",
            reviewed="source_review",
            uploaded="world_upload",
            fetched_at="source_retrieval",
            listing_created_at=None,
        ),
        item_id=query.item_id,
        item_name=query.item_name,
        original_query=query.original_query or query.query,
        currency="Gil",
        price_unit="per_item",
        observed_at=observed.isoformat(),
        truncated=execution.truncated,
        partial=execution.result.status is ResultStatus.PARTIAL_SUCCESS,
    )
    if facts["original_query"] == facts["query"]:
        del facts[
            "original_query"
        ]  # Preserve distinct originals, omit exact duplicate.
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
            if query.intent == "min":
                projected = {
                    key: projected[key]
                    for key in (
                        "quality",
                        "minimum",
                        "minimum_world_id",
                        "minimum_time",
                    )
                }
                projected["missing"] = ["minimum"] if quote.minimum is None else []
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
        # v3: successful scopes have no failure diagnostic. Aggregated quotes
        # carry their own World upload time, so an absent scope upload is redundant.
        if not outcome.failed:
            for key in ("stage", "reason", "status_code"):
                coverage[-1].pop(key)
        if not data or data.uploaded_at is None:
            coverage[-1].pop("uploaded")
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


def _error_execution_facts(execution: MarketExecution) -> dict:
    """Select public error diagnostics; never copy success facts or source bodies."""
    query, observed = execution.query, execution.observed_at
    coverage = []
    for outcome in execution.outcomes[:4]:
        data, provenance = outcome.data, outcome.provenance
        quotes = (
            [
                dict(
                    quality=quote.quality,
                    minimum=_error_number(quote.minimum),
                    recent_purchase=_error_number(quote.recent_purchase),
                    average_sale_price=_error_number(quote.average_sale_price),
                    daily_sale_velocity=_error_number(quote.daily_sale_velocity),
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
                for quote in data.quotes[:2]
                if data
            ]
            if data
            else []
        )
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
                reason=outcome.reason if outcome.failed else None,
                status_code=outcome.status_code if outcome.failed else None,
                cached=provenance.cached if provenance else None,
                fetched_at=provenance.fetched_at.isoformat() if provenance else None,
                fetched_age_seconds=(observed - provenance.fetched_at).total_seconds()
                if provenance
                else None,
                source=provenance.url if provenance else None,
                incomplete=data.incomplete if data else None,
                truncated=data.truncated if data else None,
                quotes=quotes,
            )
        )
    facts = _query_facts(query)
    facts["scope"]["default_basis"] = query.scope.default_basis
    facts.update(
        item_id=query.item_id,
        item_name=query.item_name,
        currency="Gil",
        price_unit="per_item",
        observed_at=observed.isoformat(),
        truncated=execution.truncated,
        coverage=coverage,
        minimums=[],
        listings=[],
    )
    return facts


def _error_number(value):
    # Error supplements use JSON numbers; success facts retain their Decimal path.
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("invalid public price")
        if value == value.to_integral_value():
            value = int(value)
        else:
            number = float(value)
            if number == 0 and value != 0:
                raise ValueError("invalid public price")
            value = number
    if value is None or type(value) is int:
        return value
    if type(value) is float and isfinite(value):
        return value
    raise ValueError("invalid public price")


def _fact_values(value):
    if type(value) is float:
        return Decimal(str(value))
    if isinstance(value, Mapping):
        return {key: _fact_values(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return tuple(_fact_values(child) for child in value)
    return value


def _tool_facts(result: CapabilityResult, facts: dict | None = None) -> FactDocument:
    if result.status is ResultStatus.ERROR:
        return FactDocument(
            dict(
                status="error",
                error=dict(code=result.error.code.value, message=result.error.message),
                **(facts or {}),
            )
        )
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
    if context.origin is InvocationOrigin.LLM_TOOL:
        return chat_owner(context)
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
        self._lifecycle = object()
        self.scope_choices = MarketScopeChoices(clock=clock)
        # Only start_bound is used: each query gets freshly authorized ports.
        self.source = MarketSourceClient(None, clock=clock)

    async def invoke(
        self, context: InvocationView, parameters: Mapping
    ) -> CapabilityResult:
        started = self.clock()
        deadline = min(started + 30, context.deadline or started + 30)
        ticket = None
        scope_state = None
        scope_values = None
        lifecycle = self._lifecycle

        def failure(code, message):
            return _error(
                code, message, tool=context.origin is InvocationOrigin.LLM_TOOL
            )

        try:
            scope = await self.services.scopes.bind(context)
            message = None

            async def guard():
                if self._lifecycle is not lifecycle:
                    raise PermissionError("module lifecycle changed")
                if ticket is not None:
                    self.registry._current(ticket)
                if scope_state is not None:
                    self.scope_choices.current(owner, scope_state)
                if context.origin is InvocationOrigin.LLM_TOOL:
                    current = await scope.message.read()
                    if message is not None and current != message:
                        raise PermissionError("message context changed")
                if self._lifecycle is not lifecycle:
                    raise PermissionError("module lifecycle changed")
                if ticket is not None:
                    self.registry._current(ticket)
                if scope_state is not None:
                    self.scope_choices.current(owner, scope_state)

            if context.origin is InvocationOrigin.LLM_TOOL:
                message = await scope.message.read()
            owner = _owner(context)
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
                    # Validate before any registry mutation, including retries.
                    if not isinstance(parameters, Mapping):
                        raise QueryResolutionError("查询参数必须为对象。")
                    action = semantic_action(parameters)
                    if action not in ("continue", "clarify") or "query" in parameters:
                        validate_tool_query(parameters.get("query"), message.text)
                    scope_retry = self.scope_choices.event_query(
                        owner, message.event_ref, action
                    )
                    if scope_retry is not None:
                        scope_state = scope_retry
                        ticket = scope_state.values["ticket"]
                        await guard()
                        return self._scope_candidates(scope_state)
                    retry = self.registry.tool_query(
                        owner,
                        message.event_ref,
                        message.text,
                        parameters.get("query", ""),
                        action=action,
                    )
                    if retry is not None:
                        ticket = QueryTicket(owner, retry.generation)
                        await guard()
                        return self._candidates(context, retry)
                self.scope_choices.invalidate(owner)
                ticket = self.registry.begin(
                    owner,
                    source_ref=message.event_ref
                    if context.origin is InvocationOrigin.LLM_TOOL
                    else None,
                )  # Before bind/config/catalog await.
            scope_selection = False
            if selecting and context.origin is InvocationOrigin.LLM_TOOL:
                choice = parameters["selection"]
                if isinstance(choice, Mapping) and "scope_kind" in choice:
                    if set(choice) - {
                        "batch_id",
                        "generation",
                        "scope_kind",
                        "world_id",
                        "dc_id",
                        "action",
                    }:
                        raise QueryResolutionError("范围选择包含未知参数。")
                    scope_state, scope_values = self.scope_choices.choose(
                        owner, message.event_ref, choice
                    )
                    ticket = scope_state.values["ticket"]
                    await guard()
                    if scope_values is None:
                        return self._scope_candidates(scope_state)
                    scope_selection = True
            async with asyncio.timeout(max(0, deadline - self.clock())):
                session = self.source.start_bound(
                    scope.http, scope.cache, deadline=deadline
                )
                if selecting and not scope_selection:
                    if set(parameters) != {"selection"}:
                        raise QueryResolutionError("候选选择不能同时提交新查询。")
                    choice = parameters["selection"]
                    if (
                        not isinstance(choice, Mapping)
                        or set(choice) - {"batch_id", "generation", "item_id", "action"}
                        or not {"batch_id", "generation"}.issubset(choice)
                    ):
                        raise QueryResolutionError("候选选择参数无效。")
                    if context.origin is InvocationOrigin.LLM_TOOL:
                        action = choice.get("action", "select")
                        if action == "clarify" and "item_id" not in choice:
                            retry = self.registry.clarify(
                                owner,
                                choice["batch_id"],
                                choice["generation"],
                                event_ref=message.event_ref,
                            )
                            ticket = QueryTicket(owner, retry.generation)
                            await guard()
                            return self._candidates(context, retry)
                        if action != "select" or "item_id" not in choice:
                            raise QueryResolutionError(
                                "选择必须唯一；未明确时使用 clarify。"
                            )
                    elif set(choice) != {"batch_id", "generation", "item_id"}:
                        raise QueryResolutionError("候选选择参数无效。")
                    choose = (
                        self.registry.choose_confirmed
                        if context.origin is InvocationOrigin.LLM_TOOL
                        else self.registry.choose
                    )
                    if context.origin is InvocationOrigin.LLM_TOOL:
                        scope_state = self.scope_choices.item_event(
                            owner, message.event_ref, choice["generation"]
                        )
                    query = choose(
                        owner,
                        choice["batch_id"],
                        choice["generation"],
                        choice["item_id"],
                        retain=True,
                        **(
                            {"event_ref": message.event_ref, "text": message.text}
                            if context.origin is InvocationOrigin.LLM_TOOL
                            else {}
                        ),
                    )
                    ticket = QueryTicket(owner, choice["generation"])
                    catalog = await session.catalog()
                    await guard()
                else:
                    catalog = (
                        scope_state.values["catalog"]
                        if scope_selection
                        else await session.catalog()
                    )
                    await guard()
                    coordinator = QueryCoordinator(
                        MarketQueryResolver(
                            self.scope_choices.selection_catalog(scope_state).catalog
                            if scope_selection
                            else catalog.catalog
                        ),
                        self.registry,
                    )
                    entry, values = (
                        (
                            "tool"
                            if context.origin is InvocationOrigin.LLM_TOOL
                            else "structured"
                        ),
                        scope_values if scope_selection else parameters,
                    )
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
                    try:
                        resolved = await session.run(
                            coordinator.resolve(
                                owner,
                                values,
                                self.services,
                                ItemSourceClient(scope.http),
                                entry=entry,
                                ticket=ticket,
                                retain_until_result=True,
                                guard=guard,
                                defaults=scope_state.values["defaults"]
                                if scope_selection
                                else None,
                            )
                        )
                    except QueryResolutionError as exc:
                        if context.origin is InvocationOrigin.LLM_TOOL and exc.choices:
                            scope_state = self.scope_choices.publish(
                                owner, message.event_ref, ticket, exc, catalog
                            )
                            await guard()
                            return self._scope_candidates(scope_state)
                        raise
                    await guard()
                    if isinstance(resolved, CandidateBatch):
                        self.registry._current(ticket)
                        if scope_selection:
                            self.registry._current(
                                ticket
                            ).confirmation_ref = message.event_ref
                        return self._candidates(
                            context,
                            resolved,
                            sources=tuple(p.url for p in catalog.provenance),
                        )
                    query = resolved
                if scope_state is not None:
                    catalog = self.scope_choices.selection_catalog(scope_state, catalog)
                execution = await MarketClient(session, catalog).execute(query)
                await guard()
                self.registry.finish(
                    ticket, query
                )  # Fence late market results before publishing.
                result = execution.result
                if result.status is ResultStatus.ERROR:
                    if context.origin is not InvocationOrigin.LLM_TOOL:
                        return result
                    return replace(
                        result,
                        model_facts=_tool_facts(
                            result,
                            {
                                "supplement": {
                                    "market": _error_execution_facts(execution)
                                }
                            },
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
                            key: row.get(key)
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
                    return replace(
                        result,
                        model_facts=FactDocument(
                            validate_public_market_facts({"market": public})
                        ),
                    )
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
            ServiceUnavailable,
        ) as exc:
            outcome = _diagnostic(exc, "", "")
            return failure(outcome.code, "市场查询来源未完成，请稍后重试。")
        except TimeoutError:
            return failure(ErrorCode.UPSTREAM_ERROR, "市场查询达到总时间限制。")
        finally:
            if ticket is not None:
                self.registry._release_confirmed(ticket)

    @staticmethod
    def _scope_candidates(state):
        kind = state.candidates[0].kind
        candidates = []
        for index, entry in enumerate(state.candidates):
            values = {"ordinal": index + 1, "name": entry.name, "region": entry.region}
            if kind == "world":
                values.update(world_id=entry.id, dc_id=str(entry.dc_id))
            else:
                values["dc_id"] = str(entry.id)
            candidates.append(values)
        return CapabilityResult(
            "ff14-market-scope-selection",
            ResultStatus.NEEDS_SELECTION,
            DisplayDocument(
                "FF14 市场范围候选",
                state.values["parameters"]["query"],
                tuple(
                    TextBlock(f"{row['name']} / {row['region']} / {row['dc_id']}")
                    for row in candidates
                ),
            ),
            model_facts=FactDocument(
                {
                    "status": "needs_selection",
                    "error": None,
                    "market": {
                        "request": dict(state.values["parameters"]),
                        "core_revision": state.values["defaults"].core_revision,
                        "module_revision": state.values["defaults"].module_revision,
                        "actual_scope": None,
                        "source": {
                            "provider": "Universalis",
                            "directory_urls": [
                                row.url for row in state.values["catalog"].provenance
                            ],
                            "retrieved_at": [
                                row.fetched_at.isoformat()
                                for row in state.values["catalog"].provenance
                            ],
                            "cached": [
                                row.cached for row in state.values["catalog"].provenance
                            ],
                        },
                    },
                    "selection": {
                        "kind": "scope",
                        "scope_kind": kind,
                        "batch_id": state.batch_id,
                        "generation": state.generation,
                        "state": "active",
                        "candidates": candidates,
                        "truncated": state.values["truncated"],
                    },
                }
            ),
        )

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
        if context.origin is InvocationOrigin.LLM_TOOL:
            facts["scope"]["default_basis"] = resolved.query.scope.default_basis
            selection["state"] = "active"
            selection["candidates"] = [
                dict(row, ordinal=i + 1, distinction={"quality": None})
                for i, row in enumerate(candidates)
            ]
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
            blocks.append(TextBlock("请明确选择一个候选；有疑问可继续澄清。"))
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
                )
                if context.origin is InvocationOrigin.LLM_TOOL
                else validate_public_market_facts(
                    {"market": facts, "selection": selection}
                )
            ),
        )

    def close(self) -> None:
        self.registry = CandidateRegistry(clock=self.clock)
        self.scope_choices.close()
        self._lifecycle = object()
