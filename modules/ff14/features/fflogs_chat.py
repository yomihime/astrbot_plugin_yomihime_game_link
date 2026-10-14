"""Current-message evidence and bounded public facts for role ranking tools."""

from __future__ import annotations

import asyncio
import re
import shlex
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from time import monotonic
from types import SimpleNamespace
from urllib.parse import quote

from yomihime_game_link_sdk.display import DisplayDocument, TextBlock
from yomihime_game_link_sdk.errors import ServiceUnavailable, SourceHttpError
from yomihime_game_link_sdk.results import (
    CapabilityResult,
    ErrorCode,
    FactDocument,
    ResultStatus,
)

from ..query_resolution import QueryResolutionError
from .chat_context import chat_owner, language_region, semantic_action

ROLE_FIELDS = frozenset({"realm", "server", "character"})
OPTIONAL_FIELDS = frozenset(
    {"server_hint", "metric", "zone_id", "difficulty", "partition"}
)
FILTER_FIELDS = frozenset({"zone_id", "difficulty", "partition"})
ROW_LIMIT = 20
SERVER_LIMIT = 6
CLARIFICATION = "请补充缺少的服务器或角色名；有歧义时请明确一个目标。"


def message_parameters(text: str) -> dict:
    """Closed whole-message grammar: names are full fields, never substrings."""
    if not isinstance(text, str) or "\\" in text or len(text) > 1024:
        raise ValueError(CLARIFICATION)
    text = text.strip().rstrip("。.!！").strip()
    natural = re.fullmatch(
        r"(?:帮我)?(?:查一下|查询|查)\s*(?:(?:fflogs|logs)\s*)?"
        r"(?P<realm>国际服|global|国服|cn)\s*(?P<server>.+?)\s*"
        r"(?:服务器)?(?:上的|的|上)\s*(?P<character>.+?)\s*"
        r"(?:的)?(?:(?:fflogs|logs)\s*)?战绩(?:\s+(?P<options>.+))?",
        text,
        flags=re.I,
    )
    if natural is not None:
        values = {key: natural[key].strip() for key in ("realm", "server", "character")}
        # Reuse the same closed optional key/value parser below. The actual
        # message fixes complete name boundaries before comparing model fields.
        text = natural["options"] or ""
    else:
        values = None
    text = re.sub(
        r"^(?:/ygl\s+ff14\s+logs\s+|(?:帮我)?(?:查一下|查询|查)\s*(?:(?:fflogs|logs)\s*)?|(?:fflogs|logs)\s+)",
        "",
        text,
        count=1,
        flags=re.I,
    )
    try:
        tokens = shlex.split(text)
    except ValueError:
        raise ValueError(CLARIFICATION) from None
    if values is not None:
        tail, allowed = tokens, OPTIONAL_FIELDS
    elif len(tokens) >= 3 and not any("=" in token for token in tokens[:3]):
        values = dict(zip(("realm", "server", "character"), tokens[:3]))
        tail = tokens[3:]
        allowed = OPTIONAL_FIELDS
    else:
        values, tail = {}, tokens
        allowed = ROLE_FIELDS | OPTIONAL_FIELDS
    for token in tail:
        if "=" not in token:
            raise ValueError(CLARIFICATION)
        key, value = token.split("=", 1)
        if key not in allowed or key in values or not value:
            raise ValueError(CLARIFICATION)
        if key in FILTER_FIELDS:
            if not value.isascii() or not value.isdigit() or len(value) > 10:
                raise ValueError(CLARIFICATION)
            value = int(value)
        values[key] = value
    if not ROLE_FIELDS.issubset(values):
        raise ValueError(CLARIFICATION)
    return values


CHAT_FIELDS = frozenset(
    {
        "action",
        "request_language",
        "context_language",
        "batch_id",
        "generation",
        "server_id",
    }
)


def validate_message_parameters(parameters, text):
    """Semantic DTO validation only. No parsing or copying Host private history."""
    from .fflogs import _character_inputs

    if not isinstance(parameters, Mapping) or set(parameters) - (
        ROLE_FIELDS | OPTIONAL_FIELDS | CHAT_FIELDS
    ):
        raise ValueError(CLARIFICATION)
    semantic_action(parameters)
    language_region(parameters, "cn")  # validates enum even on explicit scopes
    values = {
        key: value
        for key, value in parameters.items()
        if key in ROLE_FIELDS | OPTIONAL_FIELDS
    }
    probe = {
        "realm": "cn",
        "server": "Synthetic Server",
        "character": "Synthetic Hero",
        **values,
    }
    validated = _character_inputs(probe)
    if isinstance(validated, CapabilityResult):
        raise ValueError(validated.error.message)
    if "realm" in values:
        values["realm"] = validated[0]
    if "metric" in values:
        values["metric"] = validated[4]
    return values


def tool_result(result, values=None):
    if result.model_facts is not None:
        return result
    facts = {"status": result.status.value, "error": None}
    if result.error:
        facts["error"] = {
            "code": result.error.code.value,
            "message": result.error.message,
        }
    else:
        facts.update(values or {})
    return replace(result, model_facts=FactDocument(facts))


def credential_error_facts(result, state, source_code):
    result = tool_result(result)
    if source_code != "credentials_unavailable":
        return result
    public_state = "configured_but_unavailable" if state == "configured" else state
    values = dict(result.model_facts.facts)
    values["supplement"] = {
        "fflogs": {"credential_state": public_state, "credential_failure_reason": None}
    }
    return replace(result, model_facts=FactDocument(values))


def character_facts(character, server, realm, metric, zone, host, retrieved_at):
    rows = [
        {
            "encounter_id": row.encounter_id,
            "encounter_name": row.encounter_name,
            "spec": row.spec,
            "rank_percent": row.rank_percent,
            "best_amount": row.best_amount,
            "missing": [
                field
                for field in ("spec", "rank_percent", "best_amount")
                if getattr(row, field) is None
            ],
        }
        for row in character.rankings[:ROW_LIMIT]
    ]
    url = (
        f"{host}/character/{quote(server.region, safe='')}/"
        f"{quote(character.server_name, safe='')}/{quote(character.name, safe='')}"
    )
    return {
        "realm": realm,
        "character": {
            "id": character.character_id,
            "canonical_id": character.canonical_id,
            "name": character.name,
            "server_name": character.server_name,
        },
        "resolved_server": {
            "name": server.name,
            "region": server.region,
            "region_name": server.region_name,
            "subregion": server.subregion,
        },
        "selection": {
            "metric": metric,
            "zone_id": character.zone_id,
            "difficulty_id": character.difficulty_id,
            "partition": character.partition,
            "requested_filter_group": dict(
                zip(("zone_id", "difficulty", "partition"), zone)
            )
            if zone
            else None,
            "selection_source": "explicit" if zone else "fflogs_default",
        },
        "rankings": rows,
        "source": {
            "provider": "FFLogs",
            "url": url,
            "retrieved_at": retrieved_at,
            "source_updated_at": None,
        },
        "returned_rows": len(rows),
        "total_rows": len(character.rankings),
        "source_size": character.size,
        "truncated": len(character.rankings) > ROW_LIMIT,
        "limitations": {
            "public_only": True,
            "finite_returned_rankings": True,
            "battle_analysis": False,
            "global_output_percentiles": False,
        },
    }


def server_facts(matches, realm, server_label, name):
    return {
        "realm": realm,
        "server": server_label,
        "character": name,
        "candidates": [
            {
                "server_id": server.server_id,
                "ordinal": i + 1,
                "name": server.name,
                "region": server.region,
                "directory_region": server.directory_region_name,
                "subregion": server.subregion,
            }
            for i, server in enumerate(matches[:SERVER_LIMIT])
        ],
        "truncated": len(matches) > SERVER_LIMIT,
    }


async def invoke_character_tool(handler, context, parameters):
    from .fflogs import _error
    from .market_sources import MarketPayloadError, MarketSourceClient

    state = None
    key = None
    try:
        owner = chat_owner(context)
        key = (owner, context.capability_id)
        lifecycle = handler._lifecycle
        started = monotonic()
        deadline = min(
            started + 30,
            context.deadline if context.deadline is not None else started + 30,
        )
        if monotonic() >= deadline:
            raise TimeoutError
        async with asyncio.timeout(max(0, deadline - monotonic())):
            scope = await handler._services.scopes.bind(context)
            message = await scope.message.read()
            values = validate_message_parameters(parameters, message.text)
            action = semantic_action(parameters)
            pending = handler._chat_pending.get(key)

            async def guard():
                if handler._lifecycle is not lifecycle or monotonic() >= deadline:
                    if monotonic() >= deadline:
                        raise TimeoutError
                    raise PermissionError
                if (
                    await scope.message.read() != message
                    or handler._lifecycle is not lifecycle
                ):
                    raise PermissionError
                if state is not None:
                    handler._chat_pending.current(key, state)

            class GuardedHttp:
                async def fetch(self, request):
                    await guard()
                    response = await scope.http.fetch(request)
                    await guard()
                    return response

            async def clarification(current, *, missing=()):
                await guard()
                facts = {
                    "pending": True,
                    "missing": list(missing),
                    "target": dict(current.values),
                    "generation": current.generation,
                    "state": "active",
                }
                if current.batch_id:
                    facts.update(
                        server_facts(
                            current.candidates,
                            current.values["realm"],
                            current.values["server"],
                            current.values["character"],
                        )
                    )
                    facts["batch_id"] = current.batch_id
                return tool_result(
                    CapabilityResult(
                        "ff14-logs-pending",
                        ResultStatus.NEEDS_SELECTION,
                        document=DisplayDocument(
                            "FFLogs 查询待补充",
                            "公开角色目标",
                            (TextBlock(CLARIFICATION),),
                        ),
                    ),
                    {"fflogs": facts},
                )

            selected = None
            if (
                "server_id" in parameters
                or "batch_id" in parameters
                or "generation" in parameters
            ):
                if (
                    pending is None
                    or pending.consumed
                    or not pending.batch_id
                    or pending.event_ref == message.event_ref
                    or parameters.get("batch_id") != pending.batch_id
                    or parameters.get("generation") != pending.generation
                ):
                    raise ValueError("服务器候选批次无效或已过期。")
                state = pending
                if action == "clarify" and "server_id" not in parameters and not values:
                    return await clarification(state)
                if action != "continue" or values:
                    raise ValueError("候选续接不能同时更改角色或查询条件。")
                server_id = parameters.get("server_id")
                if type(server_id) is not int:
                    raise ValueError("请选择唯一服务器候选。")
                matches = [
                    row for row in pending.candidates if row.server_id == server_id
                ]
                if len(matches) != 1:
                    raise ValueError("服务器 ID 不属于当前候选。")
                selected = matches[0]
                state.consumed = True
                state.confirmation_ref = message.event_ref
                actual = dict(state.values)
                basis = state.default_basis
            else:
                if (
                    pending is not None
                    and message.event_ref == pending.confirmation_ref
                ):
                    raise ValueError("确认消息不能更改正在查询或已消费的角色目标。")
                if pending is not None and pending.event_ref == message.event_ref:
                    if values != pending.values or pending.consumed:
                        raise ValueError("原消息不能更改待选目标或确认候选。")
                    state = pending
                    return await clarification(state)
                if action == "clarify":
                    if values:
                        raise ValueError("未明确目标时不得提交猜选参数。")
                    if pending is None:
                        return tool_result(
                            _error(ErrorCode.PARAMETER_ERROR, CLARIFICATION)
                        )
                    state = pending
                    return await clarification(state)
                actual = (
                    dict(pending.values)
                    if pending is not None
                    and not pending.consumed
                    and action in ("continue", "correction")
                    else {}
                )
                # A correction updates the supplied fields while preserving only
                # existing validated missing fields; it always starts a new generation.
                if (
                    pending is not None
                    and action == "correction"
                    and not pending.consumed
                ):
                    actual = dict(pending.values)
                actual.update(values)
                explicit_realm = "realm" in values or (
                    pending is not None
                    and action in ("continue", "correction")
                    and pending.explicit_realm
                    and "realm" in actual
                )
                if not explicit_realm:
                    actual.pop("realm", None)
                if (
                    pending is not None
                    and pending.batch_id
                    and action == "continue"
                    and values == pending.values
                ):
                    state = pending
                    return await clarification(state)
                state = handler._chat_pending.begin(key, message.event_ref, actual)
                state.explicit_realm = explicit_realm
                missing = {"server", "character"} - actual.keys()
                if missing:
                    return await clarification(state, missing=sorted(missing))

                # Independent namespaces: Universalis supplies region only;
                # FFLogs resolves its own server_id and slug in _execute.
                if "realm" in actual:
                    from .fflogs import _credential_state

                    credential_state = await _credential_state(
                        handler._services, actual["realm"]
                    )
                    await guard()
                    if credential_state != "configured":
                        return tool_result(
                            await handler._execute(
                                context,
                                actual,
                                scope=SimpleNamespace(http=GuardedHttp()),
                                tool=True,
                                guard=guard,
                            )
                        )
                server_region = None
                public_source = MarketSourceClient(None)
                session = public_source.start_bound(
                    GuardedHttp(), getattr(scope, "cache", None), deadline=deadline
                )
                try:
                    public = await session.catalog()
                    try:
                        world = public.catalog.find("world", actual["server"])
                    except QueryResolutionError as exc:
                        regions = {entry.region for entry in exc.choices}
                        world = None
                        if len(regions) == 1:
                            server_region = "cn" if regions == {"China"} else "global"
                    if world is not None:
                        server_region = "cn" if world.region == "China" else "global"
                except (SourceHttpError, MarketPayloadError, ServiceUnavailable):
                    # An unavailable or missing public directory never proves a
                    # Logs server absent. Its authenticated directory remains authoritative.
                    pass
                await guard()
                explicit = actual.get("realm")
                if (
                    explicit is not None
                    and server_region is not None
                    and explicit != server_region
                ):
                    return tool_result(
                        _error(
                            ErrorCode.PARAMETER_ERROR,
                            "明确区域与服务器归属冲突，请澄清。",
                        )
                    )
                if explicit is not None:
                    basis = "explicit_region"
                elif server_region is not None:
                    actual["realm"], basis = server_region, "server_region"
                else:
                    snapshot = await handler._services.config.current()
                    await guard()
                    core = snapshot.values.get("core_defaults", {})
                    default = core.get("default_region")
                    # A complete scope must be known, not guessed from a name.
                    region, basis = language_region(parameters, default)
                    if region not in ("cn", "global"):
                        return await clarification(state, missing=("realm",))
                    actual["realm"] = region
                state.values = dict(actual)
                state.default_basis = basis

            def candidates(matches):
                import secrets

                handler._chat_pending.current(key, state)
                state.candidates = tuple(matches[:SERVER_LIMIT])
                state.batch_id = secrets.token_urlsafe(24)
                state.event_ref = message.event_ref
                return {
                    "batch_id": state.batch_id,
                    "generation": state.generation,
                    "state": "active",
                }

            result = await handler._execute(
                context,
                actual,
                scope=SimpleNamespace(http=GuardedHttp()),
                tool=True,
                guard=guard,
                resolved_server=selected,
                server_candidates=candidates,
            )
            await guard()
            result = tool_result(result)
            if result.error is None and not state.batch_id:
                state.consumed = True
            if result.error is None and "fflogs" in result.model_facts.facts:
                facts = dict(result.model_facts.facts)
                details = dict(facts["fflogs"])
                details["default_basis"] = state.default_basis
                facts["fflogs"] = details
                result = replace(result, model_facts=FactDocument(facts))
            return result
    except (ValueError, QueryResolutionError) as exc:
        return tool_result(_error(ErrorCode.PARAMETER_ERROR, str(exc)))
    except PermissionError:
        return tool_result(
            _error(ErrorCode.UNSUPPORTED, "当前真实用户消息或模块生命周期已失效。")
        )
    except ServiceUnavailable:
        return tool_result(
            _error(ErrorCode.MODULE_UNAVAILABLE, "FFLogs 来源服务暂不可用。")
        )
    except TimeoutError:
        return tool_result(_error(ErrorCode.UPSTREAM_ERROR, "FFLogs 角色查询超时。"))


def fetched_at():
    return datetime.now(timezone.utc).isoformat()
