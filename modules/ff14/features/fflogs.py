"""Public FFLogs character queries and independent output-statistics pages."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from dataclasses import replace
from html.parser import HTMLParser
from time import monotonic
from urllib.parse import quote

from yomihime_game_link_sdk.contexts import InvocationOrigin, InvocationView
from yomihime_game_link_sdk.display import (
    DisplayDocument,
    Link,
    LinksBlock,
    Privacy,
    TextBlock,
)
from yomihime_game_link_sdk.errors import ServiceUnavailable, SourceHttpError
from yomihime_game_link_sdk.results import (
    CapabilityResult,
    ErrorCode,
    ErrorDetail,
    ResultStatus,
)
from yomihime_game_link_sdk.services import ConfigSnapshot, HttpRequest, ModuleServices
from yomihime_game_link_sdk.storage import JsonObject, SecretMetadataState

from .fflogs_cards import (
    CATALOG_LIMITATION,
    DETAIL_LIMITATION,
    PROVIDER_CATALOG_SCOPE,
    character_card,
)
from .fflogs_catalog import (
    FFLOGS_CN_SOURCE,
    FFLOGS_GLOBAL_SOURCE,
    GRAPHQL_PATH,
    FFLogsCatalog,
    FFLogsGraphQLError,
    parse_graphql_envelope,
    resolve_output_metadata,
    select_character_zones,
)
from .fflogs_models import (
    CharacterRanking,
    CharacterRankingGroup,
    FFLogsPayloadError,
    OutputMetadata,
    PublicCharacter,
    parse_public_character,
    parse_rankings,
)

SUPPORTED_METRICS = ("rdps", "ndps", "cdps")
STAT_PERCENTILES = (10, 25, 50, 75, 95, 99, 100)
VERIFIED_CHARACTER_REALMS = frozenset({"cn"})
VERIFIED_OUTPUT_METADATA_REALMS = frozenset({"cn"})
LIVE_STATISTICS_MARKUP_VERIFIED = False
MAX_HTML_BYTES = 1_000_000
MAX_STAT_POINTS = 512
MAX_HTML_TAGS = 20_000
METADATA_DEADLINE_SECONDS = 5
METADATA_CACHE_SECONDS = 900
METADATA_FAILURE_CACHE_SECONDS = 60
MAX_CHARACTER_ZONES = 12
MAX_CHARACTER_ROWS = 40
CATALOG_RANKING_DEADLINE_SECONDS = 15
CATALOG_DIAGNOSTIC_BYTES = 16_384
_LOGGER = logging.getLogger(__name__)
_INTEGER = re.compile(r"[0-9]+\Z", re.ASCII)
_NUMBER = re.compile(r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?\Z", re.ASCII)

_CHARACTER_DEFAULT_QUERY = """query FF14PublicCharacter(
  $name: String!, $serverSlug: String!, $serverRegion: String!,
  $metric: CharacterPageRankingMetricType!
) {
  characterData {
    character(name: $name, serverSlug: $serverSlug, serverRegion: $serverRegion) {
      id canonicalID name hidden server { name }
      rankings: zoneRankings(metric: $metric)
    }
  }
}"""

_CHARACTER_ZONE_QUERY = """query FF14PublicCharacterZone(
  $name: String!, $serverSlug: String!, $serverRegion: String!,
  $metric: CharacterPageRankingMetricType!, $zoneId: Int!,
  $difficulty: Int!, $partition: Int!
) {
  characterData {
    character(name: $name, serverSlug: $serverSlug, serverRegion: $serverRegion) {
      id canonicalID name hidden server { name }
      rankings: zoneRankings(
        metric: $metric, zoneID: $zoneId, difficulty: $difficulty, partition: $partition
      )
    }
  }
}"""


class StatisticsPageError(FFLogsPayloadError):
    """An HTML response is a challenge or unknown statistics-page shape."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class FFLogsCharacterLookup:
    """Resolve an explicit directory entry and query its public character."""

    def __init__(self, services: ModuleServices) -> None:
        self._services = services
        self._lifecycle = object()
        self._metadata_cache: dict[str, tuple[float, OutputMetadata | None, str]] = {}
        from .chat_context import PendingTargets

        self._chat_pending = PendingTargets()

    def close(self) -> None:
        self._lifecycle = object()
        self._metadata_cache.clear()
        self._chat_pending.close()

    async def _character_metadata(self, scope, source_id, lifecycle):
        """Enrich actual rows using the existing fixed schema/catalog contract.

        Cache is per realm/source and module lifetime. A failed enrichment must
        not discard valid public rankings or select a guessed old expansion.
        """
        cached = self._metadata_cache.get(source_id)
        if cached is not None and cached[0] > monotonic():
            return cached[1]

        def check_active():
            if self._lifecycle is not lifecycle:
                raise ServiceUnavailable()

        metadata = None
        state = "unavailable_internal"
        try:
            async with asyncio.timeout(METADATA_DEADLINE_SECONDS):
                metadata = await FFLogsCatalog(
                    scope.http, source_id, check_active=check_active
                ).load_character_metadata()
            state = (
                "verified_catalog"
                if metadata.character_catalog_verified
                else "base_catalog"
            )
        except SourceHttpError:
            state = "unavailable_source"
        except FFLogsGraphQLError:
            state = "unavailable_graphql"
        except FFLogsPayloadError:
            state = "unavailable_payload"
        except ServiceUnavailable:
            state = "unavailable_service"
        except PermissionError:
            state = "unavailable_permission"
        except TimeoutError:
            state = "unavailable_timeout"
        except Exception:
            # Source error strings/response bodies are never public metadata.
            pass
        check_active()
        ttl = (
            METADATA_CACHE_SECONDS
            if metadata is not None
            else METADATA_FAILURE_CACHE_SECONDS
        )
        self._metadata_cache[source_id] = (monotonic() + ttl, metadata, state)
        return metadata

    async def _catalog_rankings(
        self, scope, source_id, character, metadata, identity_variables, lifecycle
    ):
        targets = select_character_zones(metadata, character.zone_id)
        warnings = []
        if not any(category == "ultimate" for _, _, category in targets):
            warnings.append("未取得可核验的绝境战目录，本次结果不包含完整绝境战范围。")
        if not any(category == "savage" for _, _, category in targets):
            warnings.append(
                "默认副本所属资料片未取得零式目录记录，未自动改选旧资料片。"
            )
        if not targets:
            return replace(character, catalog_warnings=tuple(warnings))
        if len(targets) > MAX_CHARACTER_ZONES:
            warnings.append(f"目录范围超过本次 {MAX_CHARACTER_ZONES} 个副本查询上限。")
        targets = _bounded_catalog_targets(targets)
        variables = {
            key: identity_variables[key]
            for key in ("name", "serverSlug", "serverRegion", "metric")
        }
        arguments = []
        aliases = []
        for index, (zone, difficulty, _) in enumerate(targets):
            variables[f"z{index}"] = zone.zone_id
            variables[f"d{index}"] = difficulty.difficulty_id
            arguments.extend((f"$z{index}: Int!", f"$d{index}: Int!"))
            aliases.append(
                f"z{index}: zoneRankings(zoneID: $z{index}, difficulty: $d{index}, metric: $metric)"
            )
        query = (
            "query FF14CatalogCharacter($name: String!, $serverSlug: String!, "
            "$serverRegion: String!, $metric: CharacterPageRankingMetricType!, "
            + ", ".join(arguments)
            + ") { characterData { character(name: $name, serverSlug: $serverSlug, "
            "serverRegion: $serverRegion) { id name hidden server { name } "
            + " ".join(aliases)
            + " } } }"
        )
        try:
            if self._lifecycle is not lifecycle:
                raise ServiceUnavailable()
            async with asyncio.timeout(CATALOG_RANKING_DEADLINE_SECONDS):
                response = await scope.http.fetch(
                    HttpRequest(
                        source_id,
                        GRAPHQL_PATH,
                        method="POST",
                        body=_graphql_body(query, variables),
                        headers={"Content-Type": "application/json"},
                    )
                )
            if self._lifecycle is not lifecycle:
                raise ServiceUnavailable()
            data = parse_graphql_envelope(
                response.body, exact_numbers=True, allow_partial_errors=True
            )
            container = data.get("characterData")
            value = container.get("character") if isinstance(container, dict) else None
            if isinstance(value, dict) and value.get("hidden") is True:
                return replace(character, hidden=True, rankings=(), groups=())
            if (
                not isinstance(value, dict)
                or value.get("id") != character.character_id
                or value.get("name") != character.name
                or value.get("hidden") is not False
                or not isinstance(value.get("server"), dict)
                or value["server"].get("name") != character.server_name
            ):
                raise FFLogsPayloadError("catalog character identity changed")
        except (SourceHttpError, FFLogsGraphQLError, FFLogsPayloadError, TimeoutError):
            return replace(
                character,
                catalog_warnings=tuple(warnings)
                + ("部分副本查询失败，保留已取得的记录。",),
            )
        groups = []
        known_encounter_ids = {
            item.encounter_id
            for catalog_zone in metadata.zones
            for item in catalog_zone.encounters
        }
        for index, (zone, difficulty, category) in enumerate(targets):
            try:
                selection = parse_rankings(value.get(f"z{index}"))
                if (
                    selection.zone_id != zone.zone_id
                    or selection.difficulty_id != difficulty.difficulty_id
                    or selection.metric != character.metric
                ):
                    raise FFLogsPayloadError("catalog ranking selection changed")
                if len({row.encounter_id for row in selection.rows}) != len(
                    selection.rows
                ):
                    raise FFLogsPayloadError("catalog encounter identity is duplicated")
                expected = {item.encounter_id for item in zone.encounters}
                unknown_count = sum(
                    row.encounter_id not in known_encounter_ids
                    for row in selection.rows
                )
                if unknown_count:
                    warnings.append(
                        f"{zone.name}：返回 {unknown_count} 项目录未知记录，本次结果可能不完整。"
                    )
                rankings = {
                    row.encounter_id: row
                    for row in selection.rows
                    if row.encounter_id in expected
                }
                rows = tuple(
                    rankings.get(item.encounter_id)
                    or CharacterRanking(item.encounter_id, item.name, None, None, None)
                    for item in zone.encounters
                )
                groups.append(
                    CharacterRankingGroup(
                        zone.zone_id,
                        zone.name,
                        difficulty.difficulty_id,
                        selection.partition,
                        category,
                        zone.expansion_name or "—",
                        rows,
                    )
                )
            except FFLogsPayloadError:
                warnings.append(f"{zone.name}：本次未取得可用记录。")
                if (zone.zone_id, difficulty.difficulty_id) == (
                    character.zone_id,
                    character.difficulty_id,
                ):
                    groups.append(
                        CharacterRankingGroup(
                            zone.zone_id,
                            zone.name,
                            difficulty.difficulty_id,
                            character.partition,
                            category,
                            zone.expansion_name or "—",
                            character.rankings,
                        )
                    )
                else:
                    groups.append(
                        CharacterRankingGroup(
                            zone.zone_id,
                            zone.name,
                            difficulty.difficulty_id,
                            -1,
                            category,
                            zone.expansion_name or "—",
                            tuple(
                                CharacterRanking(
                                    item.encounter_id,
                                    item.name,
                                    None,
                                    None,
                                    None,
                                    source_error=True,
                                )
                                for item in zone.encounters
                            ),
                        )
                    )
        if not groups:
            return replace(character, catalog_warnings=tuple(warnings))
        groups.sort(key=lambda group: group.category != "ultimate")
        return replace(
            character,
            groups=tuple(groups),
            rankings=tuple(row for group in groups for row in group.rankings),
            catalog_warnings=tuple(warnings),
        )

    async def invoke(
        self, context: InvocationView, parameters: JsonObject
    ) -> CapabilityResult:
        if context is not None and context.origin is InvocationOrigin.LLM_TOOL:
            from .fflogs_chat import invoke_character_tool

            return await invoke_character_tool(self, context, parameters)
        try:
            async with asyncio.timeout(30):
                return await self._execute(context, parameters)
        except TimeoutError:
            return _error(ErrorCode.UPSTREAM_ERROR, "FFLogs 查询超时，请稍后重试。")

    async def _execute(
        self,
        context,
        parameters,
        *,
        scope=None,
        tool=False,
        guard=None,
        resolved_server=None,
        server_candidates=None,
    ) -> CapabilityResult:
        lifecycle = self._lifecycle
        inputs = _character_inputs(parameters)
        if isinstance(inputs, CapabilityResult):
            return inputs
        realm, server_label, name, server_hint, metric, zone_tuple = inputs
        source_id, host = _realm_source(realm)
        credential_state = await _credential_state(self._services, realm)

        def source_error(error):
            result = _http_error(error, credential_state=credential_state)
            if tool:
                from .fflogs_chat import credential_error_facts

                return credential_error_facts(result, credential_state, error.code)
            return result

        if guard is not None:
            await guard()
        if credential_state != "configured":
            return source_error(SourceHttpError("credentials_unavailable"))
        try:
            if scope is None:
                scope = await self._services.scopes.bind(context)
            if resolved_server is None:
                catalog = FFLogsCatalog(scope.http, source_id)
                resolution = await catalog.resolve_server(realm, server_label)
            else:
                from types import SimpleNamespace

                resolution = SimpleNamespace(complete=True, matches=(resolved_server,))
        except SourceHttpError as exc:
            return source_error(exc)
        except (FFLogsPayloadError, FFLogsGraphQLError):
            return _error(
                ErrorCode.UNPARSED, "FFLogs 服务器目录格式暂不可用，未尝试自动猜测。"
            )
        except (PermissionError, ServiceUnavailable, TimeoutError):
            if tool:
                raise
            return _error(ErrorCode.MODULE_UNAVAILABLE, "FFLogs 来源服务暂不可用。")
        except Exception:
            return _error(ErrorCode.MODULE_UNAVAILABLE, "FFLogs 来源服务暂不可用。")

        if not resolution.complete:
            return _error(
                ErrorCode.UNPARSED,
                "FFLogs 服务器目录不完整，无法安全选择服务器。",
            )
        matches = tuple(
            item
            for item in resolution.matches
            if server_hint is None or _server_hint_matches(item, server_hint)
        )
        if not matches:
            return _error(
                ErrorCode.NOT_FOUND,
                "所选区服目录中没有匹配的服务器名称。",
            )
        if len(matches) > 1:
            limit = 6 if tool else 20
            choices = tuple(_server_choice(item) for item in matches[:limit])
            result = _selection(
                "服务器名称有重名，请选择区域",
                choices,
                "可将某个区域或数据中心名称作为 server_hint 后重试。",
            )
            if tool:
                from .fflogs_chat import server_facts, tool_result

                selection = server_candidates(matches) if server_candidates else {}
                return tool_result(
                    result,
                    {
                        "fflogs": {
                            **server_facts(matches, realm, server_label, name),
                            **selection,
                        }
                    },
                )
            return result
        server = matches[0]

        variables: dict[str, object] = {
            "name": name,
            "serverSlug": server.slug,
            "serverRegion": server.region,
            "metric": metric,
        }
        if zone_tuple is None:
            query = _CHARACTER_DEFAULT_QUERY
        else:
            query = _CHARACTER_ZONE_QUERY
            variables.update(
                zoneId=zone_tuple[0], difficulty=zone_tuple[1], partition=zone_tuple[2]
            )
        try:
            response = await scope.http.fetch(
                HttpRequest(
                    source_id,
                    GRAPHQL_PATH,
                    method="POST",
                    body=_graphql_body(query, variables),
                    headers={"Content-Type": "application/json"},
                )
            )
            data = parse_graphql_envelope(response.body, exact_numbers=True)
            character_data = data.get("characterData")
            raw_character = (
                character_data.get("character")
                if isinstance(character_data, dict)
                else None
            )
            if raw_character is None:
                return _error(
                    ErrorCode.NOT_FOUND,
                    "没有找到该服务器上的公开 FFLogs 角色记录。",
                )
            if not isinstance(raw_character, dict):
                raise FFLogsPayloadError("character projection is malformed")
            if raw_character.get("hidden") is True:
                return _error(
                    ErrorCode.NOT_PUBLIC,
                    "该角色的 FFLogs 记录已隐藏；本模块不会查询私密数据。",
                )
            character = parse_public_character(raw_character)
            if tool and (
                " ".join(character.name.casefold().split())
                != " ".join(name.casefold().split())
                or " ".join(character.server_name.casefold().split())
                != " ".join(server.name.casefold().split())
            ):
                return _error(
                    ErrorCode.UNPARSED,
                    "FFLogs 返回的角色或服务器与本次目标不一致，未展示战绩。",
                )
            if character.metric != metric:
                return _error(
                    ErrorCode.UNPARSED,
                    "FFLogs \u8fd4\u56de\u7684\u6307\u6807\u4e0e\u8bf7\u6c42\u4e0d\u4e00\u81f4\uff0c\u672a\u5c55\u793a\u8fd9\u7ec4\u6218\u7ee9\u3002",
                )
            if (
                zone_tuple is not None
                and (
                    character.zone_id,
                    character.difficulty_id,
                    character.partition,
                )
                != zone_tuple
            ):
                return _error(
                    ErrorCode.UNPARSED,
                    "FFLogs返回的副本、难度或分区与请求不一致。未展示战绩。",
                )
        except SourceHttpError as exc:
            return source_error(exc)
        except FFLogsGraphQLError:
            return _error(ErrorCode.UPSTREAM_ERROR, "FFLogs 未接受公开角色查询。")
        except FFLogsPayloadError:
            return _error(
                ErrorCode.UNPARSED,
                "FFLogs 角色记录格式暂不可用，未提取不认识的字段。",
            )
        except (PermissionError, ServiceUnavailable, TimeoutError):
            if tool:
                raise
            return _error(ErrorCode.MODULE_UNAVAILABLE, "FFLogs 来源服务暂不可用。")
        except Exception:
            return _error(ErrorCode.MODULE_UNAVAILABLE, "FFLogs 来源服务暂不可用。")
        if guard is not None:
            await guard()
        try:
            metadata = (
                await self._character_metadata(scope, source_id, lifecycle)
                if not tool and zone_tuple is None
                else None
            )
        except ServiceUnavailable:
            return _error(ErrorCode.MODULE_UNAVAILABLE, "FFLogs 来源服务暂不可用。")
        if guard is not None:
            await guard()
        if metadata is not None and metadata.character_catalog_verified:
            try:
                character = await self._catalog_rankings(
                    scope, source_id, character, metadata, variables, lifecycle
                )
            except (ServiceUnavailable, PermissionError):
                return _error(ErrorCode.MODULE_UNAVAILABLE, "FFLogs 来源服务暂不可用。")
        if not tool and zone_tuple is None:
            cached = self._metadata_cache.get(source_id)
            _LOGGER.info(
                "FF14_LOGS_CATALOG %s",
                _catalog_diagnostic(
                    metadata, cached[2] if cached else "unavailable_internal", character
                ),
            )
        result = _character_result(
            character,
            host,
            server.region,
            metric,
            zone_tuple,
            realm,
            limit=20 if tool else MAX_CHARACTER_ROWS,
            partial_on_truncation=tool,
            metadata=metadata,
            catalog_requested=not tool and zone_tuple is None,
        )
        if tool and result.error is None:
            from .fflogs_chat import character_facts, fetched_at, tool_result

            facts = character_facts(
                character, server, realm, metric, zone_tuple, host, fetched_at()
            )
            facts.update(
                partial=result.status is ResultStatus.PARTIAL_SUCCESS,
                warnings=list(result.warnings),
            )
            return tool_result(result, {"fflogs": facts})
        return result


def _bounded_catalog_targets(targets):
    """Keep both verified categories represented without increasing the budget."""
    if len(targets) <= MAX_CHARACTER_ZONES:
        return targets
    reserved = {
        next(index for index, target in enumerate(targets) if target[2] == category)
        for category in ("ultimate", "savage")
        if any(target[2] == category for target in targets)
    }
    for index in range(len(targets)):
        if len(reserved) >= MAX_CHARACTER_ZONES:
            break
        reserved.add(index)
    return tuple(target for index, target in enumerate(targets) if index in reserved)


def _catalog_diagnostic(metadata, state, character):
    """Serialize only bounded directory facts, never identity, ranks or errors."""
    zones = metadata.zones if metadata is not None else ()
    anchor = next((zone for zone in zones if zone.zone_id == character.zone_id), None)
    targets = select_character_zones(metadata, character.zone_id) if metadata else ()
    bounded = _bounded_catalog_targets(targets)

    def counts(values):
        return {
            category: sum(value[2] == category for value in values)
            for category in ("ultimate", "savage")
        }

    priority = {zone.zone_id for zone, _, _ in bounded} | {character.zone_id}
    ordered = sorted(zones, key=lambda zone: zone.zone_id not in priority)
    listed = [
        {
            "zone_id": zone.zone_id,
            "name": zone.name[:80],
            "expansion_id": zone.expansion_id,
            "canonical_encounters": sum(
                item.zone_id == zone.zone_id for item in zone.encounters
            ),
            "difficulties": [
                {"id": item.difficulty_id, "name": item.name[:80]}
                for item in zone.difficulties[:4]
            ],
            "difficulties_omitted": max(0, len(zone.difficulties) - 4),
        }
        for zone in ordered[:32]
    ]
    snapshot = {
        "metadata_state": state,
        "default_zone_id": character.zone_id,
        "default_expansion_id": anchor.expansion_id if anchor else None,
        "zone_count": len(zones),
        "zones": listed,
        "zones_omitted": len(zones) - len(listed),
        "targets_before": counts(targets),
        "targets_after": counts(bounded),
        "final_groups": {
            category: sum(group.category == category for group in character.groups)
            for category in ("ultimate", "savage")
        },
    }
    while True:
        encoded = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) <= CATALOG_DIAGNOSTIC_BYTES:
            return encoded
        listed.pop()
        snapshot["zones_omitted"] += 1


class FFLogsOutputPercentiles:
    """Resolve readable metadata, then read seven statistics-page datasets."""

    def __init__(self, services: ModuleServices) -> None:
        self._services = services

    async def invoke(
        self, context: InvocationView, parameters: JsonObject
    ) -> CapabilityResult:
        inputs = _statistics_inputs(parameters)
        if isinstance(inputs, CapabilityResult):
            return inputs
        realm, encounter_label, difficulty_label, job_label, metric, period = inputs
        source_id, _ = _realm_source(realm)
        stats_source_id, stats_host = _realm_source(realm, statistics=True)
        credential_state = await _credential_state(self._services, realm)
        if credential_state != "configured":
            return _http_error(
                SourceHttpError("credentials_unavailable"),
                credential_state=credential_state,
            )
        try:
            scope = await self._services.scopes.bind(context)
            catalog = FFLogsCatalog(scope.http, source_id)
            metadata = await catalog.load_output_metadata()
            resolution = resolve_output_metadata(
                metadata, encounter_label, difficulty_label, job_label
            )
        except SourceHttpError as exc:
            return _http_error(exc, credential_state=credential_state)
        except FFLogsGraphQLError:
            return _error(ErrorCode.UPSTREAM_ERROR, "FFLogs 未接受副本与职业目录查询。")
        except FFLogsPayloadError:
            return _error(
                ErrorCode.UNPARSED,
                "FFLogs 副本、难度或职业目录格式暂不可用。",
            )
        except Exception:
            return _error(ErrorCode.MODULE_UNAVAILABLE, "FFLogs 来源服务暂不可用。")

        if not resolution.complete:
            return _error(
                ErrorCode.UNPARSED,
                "FFLogs 目录无法安全解析，未选择任何首领或职业。",
            )
        if not resolution.matches:
            return _error(
                ErrorCode.NOT_FOUND,
                "FFLogs 目录中没有匹配的首领、副本难度和职业名称。",
            )
        if len(resolution.matches) > 1:
            return _selection(
                "名称对应多个FFLogs候选，请选择完整项目",
                tuple(_metadata_choice(item) for item in resolution.matches[:20]),
                "请使用上方完整副本、首领、难度和职业名称重试。",
            )
        candidate = resolution.matches[0]

        values: dict[int, float] = {}
        warnings: list[str] = []
        for percentile in STAT_PERCENTILES:
            query = [
                ("boss", str(candidate.encounter_id)),
                ("difficulty", str(candidate.difficulty_id)),
                ("class", "Global"),
                ("spec", candidate.spec_slug),
                ("dataset", str(percentile)),
            ]
            if metric != "rdps":
                query.append(("dpstype", metric))
            request = HttpRequest(
                stats_source_id,
                f"/zone/statistics/{candidate.zone_id}",
                query=tuple(query),
            )
            try:
                response = await scope.http.fetch(request)
                value = parse_statistics_cell(
                    response.body, expected_percentile=percentile
                )
            except SourceHttpError as exc:
                if values:
                    warnings.append(
                        f"第 {percentile} 百分位暂不可用：{_safe_http_code(exc)}"
                    )
                    break
                return _http_error(exc, page=True)
            except StatisticsPageError as exc:
                if values:
                    warnings.append(
                        f"\u7b2c {percentile} \u767e\u5206\u4f4d\u6682\u4e0d\u53ef\u7528\u3002"
                    )
                    break
                code = ErrorCode.UNPARSED
                text = (
                    "FFLogs 返回了人机验证页面，未提取任何分位数据。"
                    if exc.code == "human_verification"
                    else "FFLogs 统计页面格式尚未验证或已变化，未提取数据。"
                )
                return _error(code, text)
            values[percentile] = value

        if not values:
            return _error(ErrorCode.UNPARSED, "没有可安全显示的 FFLogs 分位数据。")
        schema_verified = realm in VERIFIED_OUTPUT_METADATA_REALMS
        partial = len(values) != len(STAT_PERCENTILES) or not (
            schema_verified and LIVE_STATISTICS_MARKUP_VERIFIED
        )
        document = _statistics_document(candidate, metric, period, values, stats_host)
        if not schema_verified:
            warnings.append("该区域的副本与职业目录尚未完成核验，请核对候选名称。")
        if not LIVE_STATISTICS_MARKUP_VERIFIED:
            warnings.append("FFLogs 统计页面格式尚未完成核验，当前数值仅供核对。")
        return CapabilityResult(
            result_id=(
                f"ff14-fflogs-output-{candidate.zone_id}-"
                f"{candidate.encounter_id}-{candidate.spec_id}"
            ),
            status=ResultStatus.PARTIAL_SUCCESS if partial else ResultStatus.SUCCESS,
            document=document,
            privacy=Privacy.PUBLIC,
            warnings=tuple(warnings),
        )


def parse_statistics_cell(body: bytes, *, expected_percentile: int) -> float:
    """Read only a single explicit primary statistics cell, never loose numbers."""

    if expected_percentile not in STAT_PERCENTILES:
        raise ValueError("unsupported statistics percentile")
    if not isinstance(body, bytes) or len(body) > MAX_HTML_BYTES:
        raise StatisticsPageError("html_budget")
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise StatisticsPageError("unknown_markup") from None
    lowered = text.casefold()
    if any(
        marker in lowered
        for marker in (
            "human verification",
            "verify you are human",
            "just a moment",
            "enable javascript and cookies",
            "cf-chl-",
            "captcha",
        )
    ):
        raise StatisticsPageError("human_verification")
    parser = _PrimaryCellParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        raise StatisticsPageError("unknown_markup") from None
    if parser.tag_count > MAX_HTML_TAGS or len(parser.cells) != 1:
        raise StatisticsPageError("unknown_markup")
    cell = parser.cells[0].strip()
    if not _NUMBER.fullmatch(cell):
        raise StatisticsPageError("unknown_markup")
    value = float(cell.replace(",", ""))
    if not math.isfinite(value) or not 0 <= value <= 1_000_000_000:
        raise StatisticsPageError("unknown_markup")
    return value


class _PrimaryCellParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cells: list[str] = []
        self.tag_count = 0
        self._active = False
        self._target_depth = 0
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tag_count += 1
        if self.tag_count > MAX_HTML_TAGS:
            return
        if tag.casefold() != "td":
            return
        if self._active:
            self._target_depth += 1
            return
        classes = next(
            (value for name, value in attrs if name.casefold() == "class"), None
        )
        tokens = set(classes.split()) if isinstance(classes, str) else set()
        if {"main-table-number", "primary"}.issubset(tokens):
            self._active = True
            self._target_depth = 1
            self._text = []

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "td" and self._active:
            self._target_depth -= 1
            if self._target_depth == 0:
                self.cells.append("".join(self._text))
                self._active = False
                self._text = []

    def handle_data(self, data: str) -> None:
        if self._active:
            self._text.append(data)


def _character_inputs(parameters: JsonObject):
    realm = _canonical_realm(parameters.get("realm"))
    server = _bounded_text(parameters.get("server"), 100)
    name = _bounded_text(parameters.get("character"), 120)
    hint = parameters.get("server_hint")
    if hint is not None:
        hint = _bounded_text(hint, 64)
        if hint is None:
            return _error(ErrorCode.PARAMETER_ERROR, "server_hint 格式无效。")
    if realm is None:
        return _error(
            ErrorCode.PARAMETER_ERROR,
            "realm 请输入国服或国际服（也接受 cn/global）。",
        )
    if any(item is None for item in (server, name)):
        return _error(
            ErrorCode.PARAMETER_ERROR,
            "请提供服务器名称和角色名称。",
        )
    metric_value = parameters.get("metric", "rdps")
    metric = metric_value.casefold() if isinstance(metric_value, str) else None
    if metric not in SUPPORTED_METRICS:
        return _error(ErrorCode.PARAMETER_ERROR, "metric 只支持 rdps、ndps、cdps。")
    zone_values = tuple(
        parameters.get(key) for key in ("zone_id", "difficulty", "partition")
    )
    if all(value is None for value in zone_values):
        zone = None
    elif all(
        type(value) is int and 1 <= value <= 2_147_483_647 for value in zone_values
    ):
        zone = zone_values
    else:
        return _error(
            ErrorCode.PARAMETER_ERROR,
            "zone_id、difficulty、partition 必须同时提供，且均为正整数。",
        )
    return realm, server, name, hint, metric, zone


def _statistics_inputs(parameters: JsonObject):
    realm = _canonical_realm(parameters.get("realm"))
    encounter = _bounded_text(parameters.get("encounter"), 160)
    difficulty = _bounded_text(parameters.get("difficulty"), 100)
    job = _bounded_text(parameters.get("job"), 100)
    if realm is None:
        return _error(
            ErrorCode.PARAMETER_ERROR,
            "realm 请输入国服或国际服（也接受 cn/global）。",
        )
    if any(value is None for value in (encounter, difficulty, job)):
        return _error(
            ErrorCode.PARAMETER_ERROR,
            "请提供 FFLogs 中可读的副本或首领、难度、职业名称。",
        )
    metric_value = parameters.get("metric", "rdps")
    metric = metric_value.casefold() if isinstance(metric_value, str) else None
    if metric not in SUPPORTED_METRICS:
        return _error(ErrorCode.PARAMETER_ERROR, "metric 只支持 rdps、ndps、cdps。")
    period_value = parameters.get("period", "latest")
    period = period_value.casefold() if isinstance(period_value, str) else None
    if period != "latest":
        return _error(
            ErrorCode.PARAMETER_ERROR,
            "period 目前只支持 latest；尚未提供历史日期范围查询。",
        )
    return realm, encounter, difficulty, job, metric, period


def _character_result(
    character: PublicCharacter,
    host: str,
    server_region: str,
    metric: str,
    zone: tuple[int, int, int] | None,
    realm: str,
    *,
    limit: int = 40,
    partial_on_truncation: bool = False,
    metadata: OutputMetadata | None = None,
    catalog_requested: bool = True,
) -> CapabilityResult:
    if character.hidden:
        return _error(
            ErrorCode.NOT_PUBLIC,
            "该角色的 FFLogs 记录已隐藏；本模块不会查询私密数据。",
        )
    if not character.rankings:
        return _error(
            ErrorCode.NO_RECORDS,
            "该角色当前筛选条件下没有公开战绩记录。",
        )

    metric_label = {"rdps": "rDPS", "ndps": "nDPS", "cdps": "cDPS"}[metric]
    blocks = [
        TextBlock(f"角色：{character.name} @ {character.server_name}"),
        TextBlock(f"战绩指标：{metric_label}；数据来自 FFLogs 公开记录。"),
        TextBlock(
            "查询条件：FFLogs 默认副本、难度和分区。"
            if zone is None
            else "查询条件：已应用指定的副本、难度和分区。"
        ),
    ]
    if character.groups:
        blocks.append(TextBlock("分区范围：各副本 FFLogs 默认分区。"))
    elif character.partition == -1:
        blocks.append(TextBlock("分区范围：全部分区。"))

    visible = character.rankings[:limit]
    row_lines = []
    for ranking in visible:
        parts = [ranking.encounter_name]
        if ranking.source_error:
            parts.append("数据暂不可用（ERR）")
        if ranking.best_amount is not None:
            parts.append(f"最佳 {metric_label} {ranking.best_amount:,.1f}")
        if ranking.rank_percent is not None:
            parts.append(f"排名百分位 {ranking.rank_percent:.1f}%")
        if ranking.total_kills is not None:
            parts.append(f"本次统计过本次数 {ranking.total_kills}")
        row_lines.append(" · ".join(parts))
    # Preserve the existing 40-row command bound without violating the generic
    # 32-block document budget. Every displayed row remains in text fallback.
    blocks.extend(
        TextBlock("\n".join(row_lines[index : index + 8]))
        for index in range(0, len(row_lines), 8)
    )

    warnings: list[str] = []
    verified = realm in VERIFIED_CHARACTER_REALMS
    if not verified:
        warnings.append("国际服角色数据尚未完成核验，当前结果仅供参考。")
    if catalog_requested:
        warnings.append(
            PROVIDER_CATALOG_SCOPE if character.groups else CATALOG_LIMITATION
        )
    warnings.extend(character.catalog_warnings)
    if catalog_requested:
        blocks.append(
            TextBlock(
                "范围：FFLogs 目录绝境战与默认副本所属资料片零式；目录不代表游戏开放状态。"
                if character.groups
                else CATALOG_LIMITATION
            )
        )
    blocks.append(TextBlock(DETAIL_LIMITATION))
    if len(character.rankings) > len(visible):
        warnings.append(f"战绩较多，结果仅显示前 {limit} 项。")
    url = (
        f"{host}/character/{quote(server_region, safe='')}/"
        f"{quote(character.server_name, safe='')}/{quote(character.name, safe='')}"
    )
    blocks.append(LinksBlock((Link("FFLogs 公开角色页面", url),)))
    document = DisplayDocument(
        title=f"FFLogs 角色战绩：{character.name}",
        subject=f"{character.server_name} 上的公开角色战绩",
        ordered_blocks=tuple(blocks),
        sources=("FFLogs 公开角色记录",),
        privacy=Privacy.PUBLIC,
        card=character_card(
            character,
            realm=realm,
            host=host,
            limit=limit,
            explicit_selection=zone is not None,
            metadata=metadata,
        ),
    )
    return CapabilityResult(
        result_id=f"ff14-fflogs-character-{character.character_id}",
        status=ResultStatus.SUCCESS
        if verified
        and not catalog_requested
        and (not partial_on_truncation or len(visible) == len(character.rankings))
        else ResultStatus.PARTIAL_SUCCESS,
        document=document,
        privacy=Privacy.PUBLIC,
        warnings=tuple(warnings),
    )


def _statistics_document(
    candidate,
    metric: str,
    period: str,
    values: dict[int, float],
    host: str,
) -> DisplayDocument:
    metric_label = {"rdps": "rDPS", "ndps": "nDPS", "cdps": "cDPS"}[metric]
    blocks = [
        TextBlock(
            "副本、首领、难度、职业："
            f"{candidate.zone_name} / {candidate.encounter_name} / "
            f"{candidate.difficulty_name} / {candidate.spec_name}。"
        ),
        TextBlock(f"指标：{metric_label}；统计周期：FFLogs 默认（{period}）。"),
    ]
    for percentile in STAT_PERCENTILES:
        value = values.get(percentile)
        blocks.append(
            TextBlock(
                f"第 {percentile} 百分位：{value:,.2f}"
                if value is not None
                else f"第 {percentile} 百分位：暂不可用"
            )
        )
    blocks.extend(
        (
            TextBlock("数值来自 FFLogs 公开统计页面。"),
            LinksBlock(
                (
                    Link(
                        "FFLogs 统计页面",
                        f"{host}/zone/statistics/{candidate.zone_id}",
                    ),
                )
            ),
        )
    )
    return DisplayDocument(
        title="FFLogs 输出分位",
        subject=(
            f"{candidate.spec_name}：{candidate.encounter_name} "
            f"（{candidate.difficulty_name}）"
        ),
        ordered_blocks=tuple(blocks),
        sources=("FFLogs 公开统计页面",),
        privacy=Privacy.PUBLIC,
    )


def _graphql_body(query: str, variables: dict[str, object]) -> bytes:
    return json.dumps(
        {"query": query, "variables": variables},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _realm_source(realm: str, *, statistics: bool = False) -> tuple[str, str]:
    if realm == "cn":
        return (
            ("fflogs_stats_cn", "https://cn.fflogs.com")
            if statistics
            else (FFLOGS_CN_SOURCE, "https://cn.fflogs.com")
        )
    return (
        ("fflogs_stats_global", "https://www.fflogs.com")
        if statistics
        else (FFLOGS_GLOBAL_SOURCE, "https://www.fflogs.com")
    )


async def _credential_state(services, realm):
    """Inspect this module's redacted metadata; never decrypt credentials."""
    try:
        snapshot = await services.config.current()
        if not isinstance(snapshot, ConfigSnapshot):
            return "unknown"
        alias = "credential_fflogs_cn" if realm == "cn" else "credential_fflogs_global"
        metadata = next(
            (item for item in snapshot.secret_metadata if item.field == alias), None
        )
        if metadata is None or metadata.state is SecretMetadataState.TOMBSTONED:
            return "unset"
        return (
            "configured" if metadata.state is SecretMetadataState.ACTIVE else "unusable"
        )
    except Exception:
        return "unknown"


def _http_error(
    error: SourceHttpError, *, page: bool = False, credential_state="configured"
) -> CapabilityResult:
    if error.code == "credentials_unavailable":
        return _error(
            ErrorCode.AUTH_REQUIRED,
            "该 FFLogs 区域尚未配置访问凭据；请从 AstrBot 插件管理页打开配置管理。"
            if credential_state == "unset"
            else "无法确认该 FFLogs 区域的凭据配置状态；请管理员从 AstrBot 插件管理页核对来源凭据。"
            if credential_state == "unknown"
            else "该 FFLogs 区域已配置但凭据暂不可用；原因未公开，请管理员核对配置管理中的来源凭据与加密状态。",
        )
    if error.code == "rate_limited" or error.status_code == 429:
        return _error(ErrorCode.RATE_LIMITED, "FFLogs 请求过于频繁，请稍后再试。")
    if error.status_code == 401 or (error.status_code == 403 and not page):
        return _error(
            ErrorCode.AUTH_EXPIRED, "FFLogs 拒绝了当前访问凭据，请检查凭据配置。"
        )
    if page and error.status_code == 403:
        return _error(
            ErrorCode.UPSTREAM_ERROR,
            "FFLogs \u7edf\u8ba1\u9875\u9762\u5f53\u524d\u6682\u4e0d\u53ef\u8bbf\u95ee\u3002",
        )
    if error.code == "invalid_response":
        return _error(ErrorCode.UNPARSED, "FFLogs 返回了无法识别的响应。")
    if error.code == "timeout":
        return _error(ErrorCode.UPSTREAM_ERROR, "FFLogs 请求超时，请稍后按原条件重试。")
    if page and error.status_code == 404:
        return _error(ErrorCode.NOT_FOUND, "FFLogs 中没有找到所请求的页面或角色。")
    return _error(ErrorCode.UPSTREAM_ERROR, "FFLogs 服务暂时不可用。")


def _error(code: ErrorCode, message: str) -> CapabilityResult:
    return CapabilityResult(
        result_id="ff14-fflogs-error",
        status=ResultStatus.ERROR,
        privacy=Privacy.PUBLIC,
        error=ErrorDetail(code, message),
    )


def _selection(
    title: str,
    values: tuple[str, ...],
    instruction: str = "请补充可读的区域或数据中心名称后重试；内部标识由目录解析。",
) -> CapabilityResult:
    lines = [
        *values,
        instruction,
    ]
    return CapabilityResult(
        result_id="ff14-fflogs-selection",
        status=ResultStatus.NEEDS_SELECTION,
        privacy=Privacy.PUBLIC,
        document=DisplayDocument(
            title=title,
            subject="FFLogs 查询存在多个候选项",
            ordered_blocks=tuple(TextBlock(item) for item in lines),
            sources=("FFLogs 公开目录",),
            privacy=Privacy.PUBLIC,
        ),
    )


def _bounded_text(value: object, maximum: int) -> str | None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or any(ord(char) < 32 or 0x7F <= ord(char) <= 0x9F for char in value)
    ):
        return None
    return value.strip()


def _canonical_realm(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().casefold()
    if normalized in {"cn", "国服"}:
        return "cn"
    if normalized in {"global", "国际服"}:
        return "global"
    return None


def _server_hint_matches(server, value: str) -> bool:
    hint = " ".join(value.casefold().split())
    labels = (
        server.directory_region_name,
        server.directory_region_compact_name or "",
        server.region_name,
        server.region,
        server.subregion or "",
    )
    return any(" ".join(label.casefold().split()) == hint for label in labels)


def _server_choice(server) -> str:
    location_parts: list[str] = []
    for part in (
        server.directory_region_name,
        server.directory_region_compact_name or "",
        server.region_name,
        server.subregion or "",
    ):
        normalized = " ".join(part.casefold().split())
        if part and normalized not in {
            " ".join(previous.casefold().split()) for previous in location_parts
        }:
            location_parts.append(part)
    location = " / ".join(location_parts)
    return f"{server.name} — {location or '区域信息缺失'}"


def _metadata_choice(candidate) -> str:
    return (
        f"{candidate.zone_name} — {candidate.encounter_name} / "
        f"{candidate.difficulty_name} / {candidate.spec_name}"
    )


def _positive_id(value: object) -> int | None:
    return value if type(value) is int and 1 <= value <= 2_147_483_647 else None


def _safe_http_code(error: SourceHttpError) -> str:
    if error.code == "rate_limited" or error.status_code == 429:
        return "请求频率受限"
    if error.code == "timeout":
        return "请求超时"
    return "来源暂不可用"


__all__ = [
    "FFLogsCharacterLookup",
    "FFLogsOutputPercentiles",
    "LIVE_STATISTICS_MARKUP_VERIFIED",
    "VERIFIED_CHARACTER_REALMS",
    "VERIFIED_OUTPUT_METADATA_REALMS",
    "StatisticsPageError",
    "parse_statistics_cell",
]
