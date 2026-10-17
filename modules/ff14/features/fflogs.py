"""Public FFLogs character queries and independent output-statistics pages."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from dataclasses import replace
from decimal import Decimal
from html.parser import HTMLParser
from time import monotonic, time
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
from yomihime_game_link_sdk.storage import (
    CacheLookupStatus,
    CacheQuery,
    JsonObject,
    SecretMetadataState,
)

from .fflogs_cards import (
    CATALOG_LIMITATION,
    DETAIL_LIMITATION,
    HISTORY_INCOMPLETE,
    PROVIDER_CATALOG_SCOPE,
    character_card,
    percentile_text,
    ranking_period,
)
from .fflogs_catalog import (
    FFLOGS_CN_SOURCE,
    FFLOGS_GLOBAL_SOURCE,
    GRAPHQL_PATH,
    FFLogsCatalog,
    FFLogsGraphQLError,
    normalized_encounter_name,
    parse_graphql_envelope,
    resolve_output_metadata,
    savage_expansion,
    select_character_zones,
    ultimate_catalog_order,
    ultimate_group_key,
    ultimate_group_name,
)
from .fflogs_models import (
    CharacterRanking,
    CharacterRankingGroup,
    FFLogsPayloadError,
    OutputMetadata,
    PublicCharacter,
    RankingOrigin,
    RankingSelection,
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
CATALOG_BATCH_SIZE = 12
MAX_CHARACTER_ROWS = 40
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
        self._ranking_contract_cache = {}
        from .duty_order import DutyOrderCatalog

        self._duty_order = DutyOrderCatalog()
        from .chat_context import PendingTargets

        self._chat_pending = PendingTargets()

    def close(self) -> None:
        self._lifecycle = object()
        self._metadata_cache.clear()
        self._ranking_contract_cache.clear()
        self._duty_order.close()
        self._chat_pending.close()

    async def _character_metadata(
        self, scope, source_id, lifecycle, evidence=None, *, deadline=None, metrics=None
    ):
        """Enrich actual rows using the existing fixed schema/catalog contract.

        Cache is per realm/source and module lifetime. A failed enrichment must
        not discard valid public rankings or select a guessed old expansion.
        """
        cached = self._metadata_cache.get(source_id)
        if cached is not None and cached[0] > monotonic():
            if metrics is not None:
                metrics.cache("FF14CharacterMetadata", True)
            if evidence is not None:
                evidence.observe(
                    "FF14MetadataState", {"state": cached[2]}, cached=True, status=None
                )
            if evidence is not None and cached[1] is not None:
                from .fflogs_evidence import metadata_payload

                evidence.observe(
                    "FF14CharacterMetadata",
                    metadata_payload(cached[1]),
                    cached=True,
                    status=None,
                )
            return cached[1]

        def check_active():
            if self._lifecycle is not lifecycle:
                raise ServiceUnavailable()

        metadata = None
        state = "unavailable_internal"
        if metrics is not None:
            metrics.cache("FF14CharacterMetadata", False)
        try:
            remaining = min(
                METADATA_DEADLINE_SECONDS,
                max(0, deadline - monotonic())
                if deadline is not None
                else METADATA_DEADLINE_SECONDS,
            )
            if remaining <= 0:
                raise TimeoutError
            async with asyncio.timeout(remaining):
                metadata = await FFLogsCatalog(
                    scope.http,
                    source_id,
                    check_active=check_active,
                    observer=evidence.observe if evidence is not None else None,
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
        if evidence is not None:
            evidence.observe("FF14MetadataState", {"state": state}, status=None)
        return metadata

    async def _catalog_rankings(
        self,
        scope,
        source_id,
        character,
        metadata,
        identity_variables,
        lifecycle,
        evidence=None,
        *,
        deadline=None,
        cache_owner=None,
        metrics=None,
        budget=None,
    ):
        from .fflogs_history_cache import (
            HISTORY_CACHE_TTL,
            cache_pack_key,
            cache_packs,
            history_identity,
            pack_payload,
            restore_pack,
            snapshot_payload,
        )

        started = monotonic()
        stop_at = deadline if deadline is not None else started + 30
        targets = _catalog_query_targets(
            select_character_zones(metadata, character.zone_id)
        )
        all_ordered = _bounded_catalog_targets(targets)
        keys = tuple(
            dict.fromkeys(
                _target_key(target, character.metric) for target in all_ordered
            )
        )
        eligible = {
            _target_key(target, character.metric)
            for target in targets
            if target[2] != "ultimate" or target[3] is not None
        }
        identity = history_identity(
            source_id, character, identity_variables, metadata, cache_owner
        )
        cache = getattr(scope, "cache", None)
        packs = cache_packs(metadata, tuple(key for key in keys if key in eligible))
        cached, dates, loaded = {}, {}, {}
        fresh, acquired, attempted = {}, {}, set()
        summary = {
            "planned": len(keys),
            "eligible": len(eligible),
            "history_budget_ms": max(0, int((stop_at - started) * 1000)),
            **(budget or {}),
        }

        def active():
            if self._lifecycle is not lifecycle:
                raise ServiceUnavailable()

        async def cache_backend_guard():
            nonlocal scope, cache
            # The public cache facade deliberately collapses backend failures.
            # Rebind via the existing authority, never infer authority from errors.
            active()
            if cache_owner is None or self._services is None:
                raise ServiceUnavailable()
            async with asyncio.timeout(max(0, stop_at - monotonic())):
                renewed = await self._services.scopes.bind(cache_owner)
            # Binding rotates the exact caller proof. Never reuse an older facade.
            from types import SimpleNamespace

            scope = SimpleNamespace(
                http=metrics.wrap(renewed.http)
                if metrics is not None
                else renewed.http,
                cache=getattr(renewed, "cache", None),
            )
            cache = scope.cache
            active()

        available = max(0, stop_at - monotonic())
        write_reserve = min(2, available * 0.1) if cache is not None else 0
        fetch_stop = stop_at - write_reserve
        read_stop = min(fetch_stop, monotonic() + min(2, available * 0.1))
        read_started = monotonic()
        if cache is not None:
            for index, group in enumerate(packs):
                active()
                if monotonic() >= read_stop:
                    break
                try:
                    async with asyncio.timeout(max(0, read_stop - monotonic())):
                        lookup = await cache.lookup(
                            CacheQuery(cache_pack_key(identity, group))
                        )
                    snapshots, selections, times = {}, {}, {}
                    if (
                        lookup.status is CacheLookupStatus.HIT
                        and lookup.entry.expires_at.timestamp() > time()
                    ):
                        snapshots, selections, times = restore_pack(
                            lookup.entry.payload, identity, group
                        )
                        for key, selection in selections.items():
                            if (
                                not _selection_scope_matches(selection, key)
                                or not _selection_membership_matches(
                                    metadata, key[0], selection
                                )
                                or len({row.encounter_id for row in selection.rows})
                                != len(selection.rows)
                                or any(
                                    target[2] == "savage" and selection.size != 8
                                    for target in targets
                                    if _target_key(target, character.metric) == key
                                )
                            ):
                                raise FFLogsPayloadError("history pack selection")
                    loaded[index] = snapshots
                    cached.update(selections)
                    dates.update(times)
                except ServiceUnavailable:
                    await cache_backend_guard()
                    loaded[index] = {}
                except PermissionError:
                    raise
                except TimeoutError:
                    break
                except Exception:
                    # Corrupt/oversize codecs and ordinary storage failures are misses.
                    loaded[index] = {}
                active()
        summary["read_ms"] = max(0, int((monotonic() - read_started) * 1000))
        pairs = tuple(key for key in keys if key in eligible and key not in cached)
        fetch_started = monotonic()
        await self._fetch_catalog_rankings(
            scope,
            source_id,
            character,
            metadata,
            identity_variables,
            lifecycle,
            evidence,
            deadline=fetch_stop,
            targets=targets,
            pairs=pairs,
            outcomes=fresh,
            fetched=acquired,
            on_attempt=attempted.update,
            summary=summary,
        )
        summary["fetch_ms"] = max(0, int((monotonic() - fetch_started) * 1000))
        active()
        if any(value == "hidden" for value in fresh.values()):
            return replace(character, hidden=True, rankings=(), groups=())
        good = {
            key: value for key, value in fresh.items() if not isinstance(value, str)
        }
        outcomes = {**cached, **fresh}
        used_dates = {**dates, **acquired}
        unsaved = set()
        prepared = {}
        for key, selection in good.items():
            try:
                prepared[key] = snapshot_payload(selection, fetched_at=acquired[key])
            except (FFLogsPayloadError, ValueError, TypeError):
                unsaved.add(key)
        write_started = monotonic()
        saved = set()
        for index, group in enumerate(packs):
            updates = {key: prepared[key] for key in group if key in prepared}
            if not updates:
                continue
            if cache is None or monotonic() >= stop_at:
                unsaved.update(updates)
                continue
            try:
                # Loaded siblings remain intact. An unread pack is never overwritten.
                if index not in loaded:
                    unsaved.update(updates)
                    continue
                payload = pack_payload(identity, {**loaded[index], **updates}, group)
                async with asyncio.timeout(max(0, stop_at - monotonic())):
                    await cache.put(
                        cache_pack_key(identity, group),
                        payload,
                        ttl_seconds=HISTORY_CACHE_TTL,
                    )
                saved.update(updates)
            except ServiceUnavailable:
                await cache_backend_guard()
                unsaved.update(updates)
            except PermissionError:
                raise
            except TimeoutError:
                unsaved.update(prepared.keys() - saved)
                break
            except Exception:
                unsaved.update(updates)
            active()
        active()
        summary.update(
            cache_hit=len(cached),
            cache_positive=sum(bool(value.rows) for value in cached.values()),
            cache_empty=sum(not value.rows for value in cached.values()),
            attempted=len(attempted),
            fresh_positive=sum(bool(value.rows) for value in good.values()),
            fresh_empty=sum(not value.rows for value in good.values()),
            missing_alias=sum(value == "missing_alias" for value in fresh.values()),
            invalid=sum(
                value
                in {
                    "invalid",
                    "failed_membership",
                    "excluded_size",
                    "excluded_difficulty",
                }
                for value in fresh.values()
                if isinstance(value, str)
            ),
            request_failed=sum(value == "request_failed" for value in fresh.values()),
            not_queried=sum(
                key not in outcomes or outcomes[key] == "not_queried" for key in keys
            ),
            cache_unsaved=len(unsaved),
            write_ms=max(0, int((monotonic() - write_started) * 1000)),
            total_ms=max(0, int((monotonic() - started) * 1000)),
        )
        if evidence is not None:
            evidence.observe("FF14HistorySummary", summary, status=None)
        result = _assemble_catalog_rankings(character, metadata, targets, outcomes)
        if any(group.category == "ultimate" for group in result.groups):
            from .duty_order import apply_duty_order

            catalog = await self._duty_order.load(scope.http, deadline=stop_at)
            active()
            result = apply_duty_order(result, catalog)
        warnings = []
        if unsaved:
            warnings.append("部分已验证结果未能保存到缓存；后续调用可能需要重新查询。")
        if any(
            isinstance(value, str) and value != "not_queried"
            for value in fresh.values()
        ):
            warnings.append(
                "部分本次范围查询未成功；已取得的有效缓存仍按获取时间保留。"
            )
        return replace(
            result,
            catalog_warnings=(*result.catalog_warnings, *warnings),
            history_cache_hits=len(cached),
            history_fetched_at=min(used_dates.values()) if used_dates else None,
            history_cache_ttl=HISTORY_CACHE_TTL if used_dates else None,
        )

    async def _fetch_catalog_rankings(
        self,
        scope,
        source_id,
        character,
        metadata,
        identity_variables,
        lifecycle,
        evidence=None,
        *,
        deadline=None,
        targets=None,
        pairs=None,
        outcomes=None,
        fetched=None,
        on_attempt=None,
        summary=None,
    ):
        """Query bounded batches; every catalog target retains an outcome."""
        stop_at = deadline if deadline is not None else monotonic() + 30
        hidden = False
        limited = False

        async def batch(keys):
            nonlocal limited
            if self._lifecycle is not lifecycle:
                raise ServiceUnavailable()
            if monotonic() >= stop_at:
                return {key: "not_queried" for key in keys}
            variables = {
                key: identity_variables[key]
                for key in ("name", "serverSlug", "serverRegion", "metric")
            }
            # Source and character/server identity are fixed by this invocation.
            # Every alias retains its full requested scope, not its actual echo.
            arguments, aliases = [], []
            if any(key[4] == "Historical" for key in keys):
                variables["historicalTimeframe"] = "Historical"
                arguments.append("$historicalTimeframe: RankingTimeframeType!")
            for index, (
                zone_id,
                difficulty_id,
                partition_id,
                _,
                timeframe,
                spec_name,
            ) in enumerate(keys):
                variables[f"z{index}"] = zone_id
                variables[f"d{index}"] = difficulty_id
                arguments.extend((f"$z{index}: Int!", f"$d{index}: Int!"))
                if partition_id is not None:
                    variables[f"p{index}"] = partition_id
                    arguments.append(f"$p{index}: Int!")
                partition_argument = (
                    f", partition: $p{index}" if partition_id is not None else ""
                )
                timeframe_argument = (
                    ", timeframe: $historicalTimeframe"
                    if timeframe == "Historical"
                    else ""
                )
                # None means the job argument is omitted, not guessed from rows.
                assert spec_name is None
                aliases.append(
                    f"z{index}: zoneRankings(zoneID: $z{index}, difficulty: $d{index}, metric: $metric{partition_argument}{timeframe_argument})"
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
                async with asyncio.timeout(max(0, stop_at - monotonic())):
                    if on_attempt is not None:
                        on_attempt(keys)
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
                value = (
                    container.get("character") if isinstance(container, dict) else None
                )
                if isinstance(value, dict) and value.get("hidden") is True:
                    return {key: "hidden" for key in keys}
                if (
                    not isinstance(value, dict)
                    or value.get("id") != character.character_id
                    or value.get("name") != character.name
                    or value.get("hidden") is not False
                    or not isinstance(value.get("server"), dict)
                    or value["server"].get("name") != character.server_name
                ):
                    raise PermissionError("catalog character identity changed")
                if evidence is not None:
                    evidence.observe(
                        "FF14CatalogCharacter",
                        data,
                        status=response.status_code,
                        filters=variables,
                    )
            except SourceHttpError as error:
                if error.status_code in (401, 403):
                    raise PermissionError() from None
                if error.status_code == 429:
                    limited = True
                return {key: "request_failed" for key in keys}
            except (
                FFLogsGraphQLError,
                FFLogsPayloadError,
                TimeoutError,
            ):
                return {key: "request_failed" for key in keys}
            except (ServiceUnavailable, PermissionError):
                raise
            except Exception:
                return {key: "request_failed" for key in keys}
            result = {}
            for index, key in enumerate(keys):
                try:
                    if f"z{index}" not in value:
                        result[key] = "missing_alias"
                        continue
                    selection = parse_rankings(value[f"z{index}"])
                    categories = {
                        category
                        for target in targets
                        for category in (target[2],)
                        if _target_key(target, character.metric) == key
                    }
                    if "savage" in categories and (
                        selection.size != 8 or selection.difficulty_id != key[1]
                    ):
                        result[key] = (
                            "excluded_size"
                            if selection.size != 8
                            else "excluded_difficulty"
                        )
                        continue
                    if not _selection_scope_matches(selection, key) or len(
                        {r.encounter_id for r in selection.rows}
                    ) != len(selection.rows):
                        raise FFLogsPayloadError("catalog selection changed")
                    if not _selection_membership_matches(metadata, key[0], selection):
                        result[key] = "failed_membership"
                        continue
                    result[key] = selection
                    fetched[key] = time()
                except FFLogsPayloadError:
                    result[key] = "invalid"
            return result

        pending = iter(
            pairs[i : i + CATALOG_BATCH_SIZE]
            for i in range(0, len(pairs), CATALOG_BATCH_SIZE)
        )
        active_count = 0

        async def worker():
            nonlocal hidden, active_count
            for batch_keys in pending:
                if hidden or limited or monotonic() >= stop_at:
                    return
                active_count += 1
                if summary is not None:
                    summary["batches_attempted"] = (
                        summary.get("batches_attempted", 0) + 1
                    )
                    summary["max_inflight"] = max(
                        summary.get("max_inflight", 0), active_count
                    )
                try:
                    outcome = await batch(batch_keys)
                    outcomes.update(outcome)
                    hidden = hidden or any(
                        value == "hidden" for value in outcome.values()
                    )
                finally:
                    active_count -= 1

        tasks = [asyncio.create_task(worker()) for _ in range(min(2, len(pairs)))]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        return outcomes

    async def invoke(
        self, context: InvocationView, parameters: JsonObject
    ) -> CapabilityResult:
        explicit_command = "command" in parameters
        if explicit_command:
            from .fflogs_server import LOGS_USAGE, command_parameters

            if set(parameters) != {"command"} or (
                context is not None and context.origin is not InvocationOrigin.COMMAND
            ):
                return _error(ErrorCode.PARAMETER_ERROR, LOGS_USAGE)
            try:
                parameters = command_parameters(parameters["command"])
            except ValueError:
                return _selection("请使用新的 Logs 命令格式", (), LOGS_USAGE)
        if context is not None and context.origin is InvocationOrigin.LLM_TOOL:
            from .fflogs_chat import invoke_character_tool

            return await invoke_character_tool(self, context, parameters)
        from .fflogs_evidence import RequestMetrics

        metrics = RequestMetrics() if explicit_command else None
        observation = None
        if (
            context is not None
            and context.origin is InvocationOrigin.WEB_PUBLIC
            and any(key in parameters for key in ("timeframe", "spec_name"))
            and not isinstance(_character_inputs(parameters), CapabilityResult)
        ):
            from .fflogs_scope_contract import InvocationObservation

            metrics = RequestMetrics()
            observation = InvocationObservation(parameters, metrics)
        started = monotonic()
        stop_at = getattr(context, "deadline", None)
        if stop_at is None:
            stop_at = started + 30
        # Keep a portion of the granted budget for drawing and authorized delivery.
        work_stop = stop_at - min(5, max(0, stop_at - started) * 0.1)
        try:
            async with asyncio.timeout(max(0, stop_at - monotonic())):
                result = await self._execute(
                    context,
                    parameters,
                    capture_evidence=explicit_command,
                    deadline=work_stop,
                    metrics=metrics,
                    observation=observation,
                    budget={
                        "invocation_budget_ms": max(0, int((stop_at - started) * 1000)),
                        "output_reserve_ms": max(0, int((stop_at - work_stop) * 1000)),
                    },
                )
                return observation.attach(result) if observation is not None else result
        except TimeoutError:
            result = _error(ErrorCode.UPSTREAM_ERROR, "FFLogs 查询超时，请稍后重试。")
            if observation is not None:
                observation.reject("deadline")
                return observation.attach(result)
            return result
        finally:
            if metrics is not None:
                metrics.finish()

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
        capture_evidence=False,
        deadline=None,
        metrics=None,
        observation=None,
        budget=None,
    ) -> CapabilityResult:
        lifecycle = self._lifecycle
        inputs = _character_inputs(parameters)
        if isinstance(inputs, CapabilityResult):
            return inputs
        realm, server_label, name, server_hint, metric, zone_tuple = inputs
        advanced_filters = {
            key: parameters[key]
            for key in ("timeframe", "spec_name")
            if key in parameters
        }
        if advanced_filters and (
            tool
            or context is not None
            and context.origin is not InvocationOrigin.WEB_PUBLIC
        ):
            return _error(
                ErrorCode.PARAMETER_ERROR, "这些筛选条件仅支持正式网页单范围查询。"
            )
        if metrics is not None:
            from types import SimpleNamespace

            if scope is None:
                scope = await self._services.scopes.bind(context)
            observed_http = metrics.wrap(scope.http)
            scope = SimpleNamespace(
                http=observation.wrap(observed_http)
                if observation is not None
                else observed_http,
                cache=getattr(scope, "cache", None),
            )
        if realm is None:
            from ..query_resolution import QueryResolutionError
            from .fflogs_server import resolve_realm
            from .market_sources import MarketPayloadError

            try:
                if scope is None:
                    scope = await self._services.scopes.bind(context)
                realm = await resolve_realm(
                    scope.http,
                    getattr(scope, "cache", None),
                    server_label,
                    deadline=deadline if deadline is not None else monotonic() + 30,
                    metrics=metrics,
                )
                if self._lifecycle is not lifecycle:
                    raise ServiceUnavailable()
            except QueryResolutionError as exc:
                return _error(exc.code, str(exc))
            except (SourceHttpError, MarketPayloadError, ServiceUnavailable):
                return _error(
                    ErrorCode.UPSTREAM_ERROR,
                    "服务器目录暂不可用，无法判断 FFLogs 区域，请稍后重试。",
                )
        source_id, host = _realm_source(realm)
        from .fflogs_evidence import Evidence

        evidence = Evidence(source_id) if capture_evidence else None
        if metrics is not None:
            metrics.evidence = evidence
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
            if advanced_filters:
                from .fflogs_scope_contract import fetch_contract

                if observation is not None:
                    observation.stage = "schema"

                def check_active():
                    if self._lifecycle is not lifecycle:
                        raise ServiceUnavailable()

                cached = self._ranking_contract_cache.get(source_id)
                if cached is None or cached[0] <= monotonic():
                    if observation is not None:
                        observation.schema_cache = "module_miss"
                    contract = await fetch_contract(scope.http, source_id, check_active)
                    check_active()
                    self._ranking_contract_cache[source_id] = (
                        monotonic() + METADATA_CACHE_SECONDS,
                        contract,
                    )
                else:
                    if observation is not None:
                        observation.schema_cache = "module_hit"
                    contract = cached[1]
                if (
                    "timeframe" in advanced_filters
                    and advanced_filters["timeframe"] not in contract.timeframe_values
                ):
                    from yomihime_game_link_sdk.results import FactDocument

                    return replace(
                        _error(
                            ErrorCode.PARAMETER_ERROR,
                            "timeframe 不在来源当前支持的枚举中。",
                        ),
                        model_facts=FactDocument(
                            {
                                "status": "error",
                                "error": {
                                    "code": ErrorCode.PARAMETER_ERROR.value,
                                    "message": "timeframe 不在来源当前支持的枚举中。",
                                },
                                "supplement": contract.supplement(),
                            }
                        ),
                    )
            if resolved_server is None:
                if observation is not None:
                    observation.stage = "server-directory"
                catalog = FFLogsCatalog(scope.http, source_id)
                resolution = await catalog.resolve_server(
                    realm,
                    server_label,
                    **(
                        {"page_budget": 4}
                        if capture_evidence or advanced_filters
                        else {}
                    ),
                )
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
            if advanced_filters:
                declarations, arguments = [], []
                if "timeframe" in advanced_filters:
                    declarations.append("$timeframe: RankingTimeframeType!")
                    arguments.append("timeframe: $timeframe")
                    variables["timeframe"] = advanced_filters["timeframe"]
                if "spec_name" in advanced_filters:
                    declarations.append("$specName: String!")
                    arguments.append("specName: $specName")
                    variables["specName"] = advanced_filters["spec_name"]
                query = query.replace(
                    "$partition: Int!", "$partition: Int!, " + ", ".join(declarations)
                ).replace(
                    "partition: $partition",
                    "partition: $partition, " + ", ".join(arguments),
                )
            if observation is not None:
                observation.stage = "character-fetch"
            response = await scope.http.fetch(
                HttpRequest(
                    source_id,
                    GRAPHQL_PATH,
                    method="POST",
                    body=_graphql_body(query, variables),
                    headers={"Content-Type": "application/json"},
                )
            )
            if observation is not None:
                observation.stage = "graphql-envelope"
            data = parse_graphql_envelope(response.body, exact_numbers=True)
            character_data = data.get("characterData")
            raw_character = (
                character_data.get("character")
                if isinstance(character_data, dict)
                else None
            )
            if observation is not None:
                observation.stage = "character-shape"
                observation.character_shape(raw_character, name, server.name)
            if raw_character is None:
                return _error(
                    ErrorCode.NOT_FOUND,
                    "没有找到该服务器上的公开 FFLogs 角色记录。",
                )
            if not isinstance(raw_character, dict):
                raise FFLogsPayloadError("character projection is malformed")
            if raw_character.get("hidden") is True:
                if observation is not None:
                    observation.reject("not_public", "hidden", value=True)
                return _error(
                    ErrorCode.NOT_PUBLIC,
                    "该角色的 FFLogs 记录已隐藏；本模块不会查询私密数据。",
                )
            try:
                character = parse_public_character(raw_character)
            except FFLogsPayloadError:
                if observation is not None:
                    observation.locate_parse_failure(raw_character)
                raise
            if observation is not None:
                observation.stage = "identity"
            if (
                tool or capture_evidence or advanced_filters or zone_tuple is None
            ) and (
                " ".join(character.name.casefold().split())
                != " ".join(name.casefold().split())
                or " ".join(character.server_name.casefold().split())
                != " ".join(server.name.casefold().split())
            ):
                if observation is not None:
                    field = (
                        "name"
                        if observation.identity.get("name_matches") is not True
                        else "server.name"
                    )
                    observation.reject(
                        "identity_mismatch",
                        field,
                        value=raw_character.get("name")
                        if field == "name"
                        else raw_character["server"].get("name"),
                    )
                return _error(
                    ErrorCode.UNPARSED,
                    "FFLogs 返回的角色或服务器与本次目标不一致，未展示战绩。",
                )
            if observation is not None:
                observation.stage = "selection"
            if character.metric != metric:
                if observation is not None:
                    observation.reject(
                        "metric_mismatch",
                        "metric",
                        value=raw_character["rankings"].get("metric"),
                    )
                return _error(
                    ErrorCode.UNPARSED,
                    "FFLogs \u8fd4\u56de\u7684\u6307\u6807\u4e0e\u8bf7\u6c42\u4e0d\u4e00\u81f4\uff0c\u672a\u5c55\u793a\u8fd9\u7ec4\u6218\u7ee9\u3002",
                )
            if zone_tuple is not None and (
                (character.zone_id, character.difficulty_id) != zone_tuple[:2]
                or not (
                    character.partition == zone_tuple[2]
                    or (zone_tuple[2] == -1 and character.partition > 0)
                )
            ):
                if observation is not None:
                    fields = ("zone", "difficulty", "partition")
                    field = next(
                        field
                        for field, actual, expected in zip(
                            fields,
                            (
                                character.zone_id,
                                character.difficulty_id,
                                character.partition,
                            ),
                            zone_tuple,
                        )
                        if actual != expected
                    )
                    observation.reject(
                        "selection_mismatch",
                        field,
                        value=raw_character["rankings"].get(field),
                    )
                return _error(
                    ErrorCode.UNPARSED,
                    "FFLogs返回的副本、难度或分区与请求不一致。未展示战绩。",
                )
            if evidence is not None:
                evidence.observe(
                    "FF14PublicCharacterZone" if zone_tuple else "FF14PublicCharacter",
                    data,
                    status=response.status_code,
                    filters=variables,
                )
            if advanced_filters:
                from .fflogs_scope_contract import scoped_facts

                if observation is not None:
                    observation.stage = "row-fields"
                scoped = scoped_facts(
                    raw_character["rankings"],
                    {
                        "zone_id": zone_tuple[0],
                        "difficulty": zone_tuple[1],
                        "partition": zone_tuple[2],
                        "metric": metric,
                        **advanced_filters,
                    },
                    source_id,
                    observation=observation,
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
        metadata = None
        if not tool and zone_tuple is None:
            try:
                metadata = await self._character_metadata(
                    scope,
                    source_id,
                    lifecycle,
                    evidence,
                    deadline=deadline,
                    metrics=metrics,
                )
                if metadata is not None and metadata.character_catalog_verified:
                    character = await self._catalog_rankings(
                        scope,
                        source_id,
                        character,
                        metadata,
                        variables,
                        lifecycle,
                        evidence,
                        deadline=deadline,
                        cache_owner=context,
                        metrics=metrics,
                        budget=budget,
                    )
            except (ServiceUnavailable, PermissionError):
                return _error(ErrorCode.MODULE_UNAVAILABLE, "FFLogs 来源服务暂不可用。")
        if observation is not None:
            observation.stage = "result"
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
        if advanced_filters and result.error is None:
            from yomihime_game_link_sdk.results import FactDocument

            return replace(
                result,
                model_facts=FactDocument(
                    {"status": result.status.value, "error": None, "fflogs": scoped}
                ),
            )
        if (
            advanced_filters
            and result.error is not None
            and result.error.code is ErrorCode.NO_RECORDS
            and not scoped["total_rows"]
        ):
            from yomihime_game_link_sdk.results import FactDocument

            return replace(
                result,
                model_facts=FactDocument(
                    {
                        "status": "error",
                        "error": {
                            "code": result.error.code.value,
                            "message": result.error.message,
                        },
                        "supplement": {"fflogs": scoped},
                    }
                ),
            )
        return result


def _catalog_query_targets(targets):
    """Keep each explicit standard partition; unknown labels remain incomplete."""
    result = []
    for zone, difficulty, category in targets:
        if category != "ultimate":
            result.append((zone, difficulty, category, None))
            continue
        unknown = not zone.partitions
        for period in zone.partitions:
            name = period.name.casefold().strip()
            if name.startswith(
                (
                    "非标准阵容构成",
                    "non-standard composition",
                    "non standard composition",
                )
            ):
                continue
            if period.partition_id > 0 and name.startswith(
                ("标准阵容构成", "standard composition")
            ):
                result.append((zone, difficulty, category, period.partition_id))
            else:
                unknown = True
        if unknown or not any(
            target[:3] == (zone, difficulty, category) for target in result
        ):
            result.append((zone, difficulty, category, None))
    return tuple(result)


def _target_key(target, metric="rdps"):
    # These keys never leave one source/character/server invocation. None, -1
    # and positive partition requests are distinct regardless of response echo.
    return (
        target[0].zone_id,
        target[1].difficulty_id,
        target[3] if len(target) > 3 else None,
        metric,
        "Historical" if target[2] == "ultimate" else None,
        None,  # specName is deliberately omitted for all jobs.
    )


def _selection_scope_matches(selection, requested):
    partition = requested[2]
    return (
        selection.zone_id == requested[0]
        and selection.difficulty_id == requested[1]
        and selection.metric == requested[3]
        and (
            partition is None
            or selection.partition == partition
            or (partition == -1 and selection.partition > 0)
        )
    )


def _bounded_catalog_targets(targets, *, limit=None):
    """Cover distinct names/tiers before spending the budget on historical IDs."""
    buckets = {}
    for index, target in enumerate(targets):
        zone, _, category = target[:3]
        keys = (
            tuple(
                (category, ultimate_group_key(zone.name, item.name))
                for item in zone.encounters
            )
            if category == "ultimate"
            else ((category, zone.zone_id),)
        )
        for key in keys:
            buckets.setdefault(key, []).append(index)
    selected = {}
    categories = {
        kind: [
            indexes for (category, _), indexes in buckets.items() if category == kind
        ]
        for kind in ("ultimate", "savage")
    }
    fair_buckets = [
        categories[kind][index]
        for index in range(max(map(len, categories.values()), default=0))
        for kind in ("ultimate", "savage")
        if index < len(categories[kind])
    ]
    for depth in range(max((len(items) for items in fair_buckets), default=0)):
        for indexes in fair_buckets:
            if depth < len(indexes) and (limit is None or len(selected) < limit):
                selected.setdefault(_target_key(targets[indexes[depth]]), None)
    # Query order must retain this fair round-robin, not the original catalog
    # order. Assembly separately iterates `targets` for source display order.
    by_key = {}
    for target in targets:
        by_key.setdefault(_target_key(target), []).append(target)
    return tuple(target for key in selected for target in by_key[key])


def _selection_membership_matches(metadata, zone_id, selection):
    owners = {
        item.encounter_id
        for zone in metadata.zones
        if zone.zone_id == zone_id
        for item in zone.encounters
        if item.zone_id == zone_id
    }
    return all(row.encounter_id in owners for row in selection.rows)


def _assemble_catalog_rankings(character, metadata, targets, outcomes):
    """Select entire rows using raw percentiles; never combine source periods."""
    warnings = []
    groups = []
    ultimate = {}
    selected_expansion, expansion_known = savage_expansion(metadata, character.zone_id)
    if not expansion_known:
        warnings.append("部分零式目录人数或目标资料片未知，未据此猜测或回退。")
    anchor = next(
        (zone for zone in metadata.zones if zone.zone_id == character.zone_id), None
    )
    if (
        anchor is not None
        and selected_expansion is not None
        and selected_expansion != anchor.expansion_id
    ):
        warnings.append(
            "默认副本所属资料片目录无合格八人零式，已按来源资料片列表顺序回退。"
        )
    membership_names = []
    failed_names = []
    states = {"failed": 0, "not_queried": 0}
    excluded = []
    for zone, difficulty, category, requested_partition in targets:
        requested = _target_key(
            (zone, difficulty, category, requested_partition), character.metric
        )
        selection = outcomes.get(requested, "not_queried")
        if category == "ultimate" and requested_partition is None:
            selection = "not_queried"
        scope_failed = not isinstance(selection, str) and not _selection_scope_matches(
            selection, requested
        )
        if scope_failed:
            selection = "failed"
        state = selection if isinstance(selection, str) else "record"
        if state in {"missing_alias", "invalid", "request_failed"}:
            state = "failed"
        membership_failed = state == "failed_membership" or (
            not isinstance(selection, str)
            and not _selection_membership_matches(metadata, zone.zone_id, selection)
        )
        if membership_failed:
            selection, state = "failed", "failed"
            membership_names.append(zone.name)
        if state in {"excluded_size", "excluded_difficulty"}:
            excluded.append((zone.name, state))
            continue
        if state in states:
            states[state] += 1
            if state == "failed":
                failed_names.append(zone.name)
            if (
                not membership_failed
                and not scope_failed
                and category == "savage"
                and requested_partition is None
                and zone.zone_id == character.zone_id
                and difficulty.difficulty_id == character.difficulty_id
                and (
                    requested_partition is None
                    or requested_partition == character.partition
                )
                and (category != "savage" or character.size == 8)
            ):
                selection = RankingSelection(
                    character.metric,
                    character.zone_id,
                    character.difficulty_id,
                    character.partition,
                    character.size,
                    character.rankings,
                )
                state = "record"
        rows = (
            {}
            if isinstance(selection, str)
            else {row.encounter_id: row for row in selection.rows}
        )
        partition = (
            (requested_partition if requested_partition is not None else -1)
            if isinstance(selection, str)
            else selection.partition
        )
        partition_name = next(
            (item.name for item in zone.partitions if item.partition_id == partition),
            None,
        )
        origin = RankingOrigin(
            zone.zone_id,
            difficulty.difficulty_id,
            partition,
            zone.expansion_name,
            partition_name if category != "ultimate" else None,
            requested_partition=requested_partition,
            requested_timeframe=requested[4],
            requested_spec_name=requested[5],
        )
        values = []
        for encounter in zone.encounters:
            row = rows.get(encounter.encounter_id)
            if row is not None and normalized_encounter_name(
                row.encounter_name
            ) != normalized_encounter_name(encounter.name):
                row = None
                row_state = "failed"
                states["failed"] += 1
            else:
                row_state = (
                    state
                    if state != "record"
                    else "record"
                    if row is not None
                    else "no_records"
                )
            if row is None:
                row = CharacterRanking(
                    encounter.encounter_id,
                    encounter.name,
                    None,
                    None,
                    None,
                    source_error=row_state == "failed",
                    query_state=row_state,
                )
            row = replace(
                row,
                encounter_name=encounter.name,
                origin=origin if row_state == "record" else None,
                query_state=row_state,
            )
            values.append(row)
            if category == "ultimate":
                key = ultimate_group_key(zone.name, encounter.name)
                ultimate.setdefault(key, []).append((zone, difficulty, row))
        if category == "savage":
            groups.append(
                CharacterRankingGroup(
                    zone.zone_id,
                    zone.name,
                    difficulty.difficulty_id,
                    partition,
                    category,
                    zone.expansion_name or "—",
                    tuple(values),
                )
            )
    ultimate_groups = []
    catalog_order = ultimate_catalog_order(metadata, character.zone_id)
    for key in sorted(ultimate, key=lambda key: catalog_order.get(key, (1, 0, 0))):
        entries = ultimate[key]
        records = [entry for entry in entries if entry[2].query_state == "record"]
        complete = all(
            entry[2].query_state in {"record", "no_records"} for entry in entries
        )

        def order(entry):
            zone, difficulty, row = entry
            raw = (
                row.rank_percent_exact
                if row.rank_percent_exact is not None
                else Decimal(str(row.rank_percent))
                if row.rank_percent is not None
                else Decimal(-1)
            )
            return (
                raw.copy_negate(),
                zone.zone_id,
                row.encounter_id,
                difficulty.difficulty_id,
                row.origin.partition if row.origin is not None else -1,
            )

        if records:
            chosen = min(records, key=order)
        else:
            # A failed/unqueried historical ID prevents a no-records claim.
            priority = {"failed": 0, "not_queried": 1, "no_records": 2}
            chosen = min(
                entries,
                key=lambda entry: (priority.get(entry[2].query_state, 3), order(entry)),
            )
        zone, difficulty, row = chosen
        row = replace(
            row,
            encounter_name=ultimate_group_name(zone.name, row.encounter_name),
            history_complete=complete,
        )
        ultimate_groups.append(
            CharacterRankingGroup(
                zone.zone_id,
                zone.name,
                difficulty.difficulty_id,
                row.origin.partition if row.origin is not None else -1,
                "ultimate",
                zone.expansion_name or "—",
                (row,),
            )
        )
    if not ultimate_groups:
        warnings.append("未取得符合来源完整命名约定及归属校验的绝境战目录。")
    if not ultimate_groups or any(
        not row.history_complete for group in ultimate_groups for row in group.rankings
    ):
        warnings.append(HISTORY_INCOMPLETE)
    if not groups:
        warnings.append("未取得可核验的八人零式目录；未以角色成绩决定回退。")
    if states["failed"]:
        labels = "、".join(dict.fromkeys(failed_names))[:160]
        warnings.append(
            f"{labels}：{states['failed']} 个副本范围查询失败；保留已取得记录，历史最佳可能不完整。"
        )
    if states["not_queried"]:
        warnings.append(
            f"{states['not_queried']} 个副本范围受目标或时间上限限制未查询；结果不完整。"
        )
    if membership_names:
        labels = "、".join(dict.fromkeys(membership_names))[:160]
        warnings.append(f"{labels}：来源记录的副本归属不一致，该范围未展示成绩。")
    if excluded:
        labels = "、".join(dict.fromkeys(name for name, _ in excluded))[:160]
        warnings.append(f"{labels}：返回人数或难度不符合八人零式范围，已排除该副本。")
    all_groups = tuple(ultimate_groups + groups)
    return replace(
        character,
        groups=all_groups,
        rankings=tuple(row for group in all_groups for row in group.rankings),
        catalog_warnings=tuple(warnings),
        catalog_complete=not warnings and bool(all_groups),
        catalog_evaluated=True,
    )


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
    if realm is None and "realm" in parameters:
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
    elif (
        all(
            type(value) is int and 1 <= value <= 2_147_483_647
            for value in zone_values[:2]
        )
        and type(zone_values[2]) is int
        and (zone_values[2] == -1 or 1 <= zone_values[2] <= 2_147_483_647)
    ):
        zone = zone_values
    else:
        return _error(
            ErrorCode.PARAMETER_ERROR,
            "zone_id、difficulty 必须为正整数，partition 必须为 -1 或正整数，且三者同时提供。",
        )
    from .fflogs_scope_contract import TOKEN

    advanced = {
        key: parameters[key] for key in ("timeframe", "spec_name") if key in parameters
    }
    if advanced and (
        zone is None
        or realm is None
        or any(
            not isinstance(value, str) or not TOKEN.fullmatch(value)
            for value in advanced.values()
        )
    ):
        return _error(
            ErrorCode.PARAMETER_ERROR,
            "timeframe、spec_name 必须为有效名称，并提供明确区域及完整副本、难度、分区条件。",
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
    if not character.rankings and not character.catalog_evaluated:
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
        blocks.append(
            TextBlock(
                "分区范围：绝境战逐项查询来源目录中的标准阵容历史分区；零式使用各副本的 FFLogs 默认分区。"
            )
        )
    elif character.partition == -1:
        blocks.append(TextBlock("分区范围：全部分区。"))

    visible = character.rankings[:limit]
    if character.history_fetched_at is not None:
        from datetime import UTC, datetime

        fetched_label = datetime.fromtimestamp(
            character.history_fetched_at, UTC
        ).strftime("%Y-%m-%d %H:%M:%S UTC")
        blocks.append(
            TextBlock(
                f"历史缓存命中 {character.history_cache_hits} 个范围；最早获取：{fetched_label}；有效期900秒。"
            )
        )
    row_lines = []
    for ranking in visible:
        parts = [ranking.encounter_name]
        if ranking.query_state == "not_queried":
            parts.append("未查询")
        elif ranking.query_state == "no_records":
            parts.append("该范围查询成功，无公开记录")
        elif ranking.source_error:
            parts.append("数据暂不可用（ERR）")
        if ranking.origin is not None:
            parts.append(ranking_period(ranking))
        if ranking.best_amount is not None:
            parts.append(f"最佳 {metric_label} {ranking.best_amount:,.1f}")
        if ranking.rank_percent is not None:
            value = (
                ranking.rank_percent_exact
                if ranking.rank_percent_exact is not None
                else ranking.rank_percent
            )
            parts.append(f"排名百分位 {percentile_text(value)}")
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
            PROVIDER_CATALOG_SCOPE
            if character.catalog_evaluated or character.groups
            else CATALOG_LIMITATION
        )
    warnings.extend(character.catalog_warnings)
    if catalog_requested:
        blocks.append(
            TextBlock(
                "范围：绝境战取已查询来源返回的最高百分位；零式按默认副本所属资料片八人目录；不代表独立地区开放状态。"
                if character.catalog_evaluated or character.groups
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
        and (not catalog_requested or character.catalog_complete)
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
