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

from yomihime_game_link_sdk.errors import ServiceUnavailable
from yomihime_game_link_sdk.results import CapabilityResult, ErrorCode, FactDocument

ROLE_FIELDS = frozenset({"realm", "server", "character"})
OPTIONAL_FIELDS = frozenset(
    {"server_hint", "metric", "zone_id", "difficulty", "partition"}
)
FILTER_FIELDS = frozenset({"zone_id", "difficulty", "partition"})
ROW_LIMIT = 20
SERVER_LIMIT = 6
CLARIFICATION = (
    "请在一条新消息中完整提供区域、服务器和角色名，例如："
    '查 FFLogs 国际服 Cerberus "Synthetic Hero"。角色名或服务器名含空格时请加引号；'
    "只补充区域或说“刚才那个”不能续接。"
)


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


def validate_message_parameters(parameters, text):
    from .fflogs import _character_inputs

    if not isinstance(parameters, Mapping) or any(
        key not in ROLE_FIELDS | OPTIONAL_FIELDS for key in parameters
    ):
        raise ValueError(CLARIFICATION)
    actual = message_parameters(text)
    requested = _character_inputs(parameters)
    supplied = _character_inputs(actual)
    if isinstance(requested, CapabilityResult) or isinstance(
        supplied, CapabilityResult
    ):
        raise ValueError(CLARIFICATION)
    # Compare canonical realm/metric, exact full role/server/hint and filter trio.
    # Only the existing service's omitted defaults may be added by the model.
    if requested != supplied:
        raise ValueError(CLARIFICATION)
    return actual


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
        "answer_guidance": (
            "仅回答本次公开角色排名与实际指标/筛选，注明FFLogs来源及有限返回项。"
            "retrieved_at是获取时间，source_updated_at未知，不推断战斗日期。"
            "None/missing是未知，不补零；保留partial、warnings与truncated。"
            "角色rank_percent不代表全体输出分位、战斗分析或职业比较。"
            "结果足够时直接回答，不自行改角色、服务器、区域或过滤条件。"
        ),
    }


def server_facts(matches, realm, server_label, name):
    return {
        "realm": realm,
        "server": server_label,
        "character": name,
        "candidates": [
            {
                "name": server.name,
                "region": server.region,
                "directory_region": server.directory_region_name,
                "subregion": server.subregion,
            }
            for server in matches[:SERVER_LIMIT]
        ],
        "truncated": len(matches) > SERVER_LIMIT,
        "user_confirmation": (
            "服务器名称有重名，请在一条新消息中完整重述区域、服务器和角色，"
            "并加server_hint=上方区域或数据中心名称（含空格加引号）；不能猜选或只回复序号。"
        ),
    }


async def invoke_character_tool(handler, context, parameters):
    from .fflogs import _error

    try:
        if not all((context.actor_id, context.conversation_id, context.adapter_id)):
            return tool_result(
                _error(
                    ErrorCode.UNSUPPORTED, "缺少可信用户/会话绑定，无法发起角色查询。"
                )
            )
        generation = handler._lifecycle
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
            actual = validate_message_parameters(parameters, message.text)

            async def guard():
                if handler._lifecycle is not generation:
                    raise PermissionError
                if monotonic() >= deadline:
                    raise TimeoutError
                if await scope.message.read() != message:
                    raise PermissionError
                if handler._lifecycle is not generation:
                    raise PermissionError

            class GuardedHttp:
                async def fetch(self, request):
                    await guard()
                    result = await scope.http.fetch(request)
                    await guard()
                    return result

            result = await handler._execute(
                context,
                actual,
                scope=SimpleNamespace(http=GuardedHttp()),
                tool=True,
                guard=guard,
            )
            await guard()
            return tool_result(result)
    except ValueError:
        return tool_result(_error(ErrorCode.PARAMETER_ERROR, CLARIFICATION))
    except PermissionError:
        return tool_result(
            _error(
                ErrorCode.UNSUPPORTED,
                "当前真实用户消息或模块生命周期已失效，请重新完整查询。",
            )
        )
    except ServiceUnavailable:
        return tool_result(
            _error(ErrorCode.MODULE_UNAVAILABLE, "FFLogs 来源服务暂不可用。")
        )
    except TimeoutError:
        return tool_result(
            _error(
                ErrorCode.UPSTREAM_ERROR, "FFLogs 角色查询超时，请稍后按原条件重试。"
            )
        )


def fetched_at():
    return datetime.now(timezone.utc).isoformat()
