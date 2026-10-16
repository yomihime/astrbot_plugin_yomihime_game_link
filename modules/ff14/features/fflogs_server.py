"""Closed Logs command syntax and deterministic public server-region lookup."""

from __future__ import annotations

import shlex

from yomihime_game_link_sdk.results import ErrorCode

from ..query_resolution import GLOBAL_REGIONS, QueryResolutionError

LOGS_USAGE = "用法：/ygl ff14 logs <服务器> <角色名>；含空格的名称请用双引号。"
_OPTIONS = frozenset({"server_hint", "metric", "zone_id", "difficulty", "partition"})
_INTEGER_OPTIONS = frozenset({"zone_id", "difficulty", "partition"})


def command_parameters(text: str) -> dict:
    """Exactly two names; all optional filters have explicit key=value bounds."""
    if not isinstance(text, str) or len(text) > 1024 or "\\" in text:
        raise ValueError(LOGS_USAGE)
    try:
        tokens = shlex.split(text)
    except ValueError:
        raise ValueError(LOGS_USAGE) from None
    positional = []
    result = {}
    for token in tokens:
        if "=" not in token:
            positional.append(token)
            continue
        key, value = token.split("=", 1)
        if key not in _OPTIONS or key in result or not value:
            raise ValueError(LOGS_USAGE)
        if key in _INTEGER_OPTIONS:
            if not value.isascii() or not value.isdigit() or len(value) > 10:
                raise ValueError(LOGS_USAGE)
            value = int(value)
        result[key] = value
    if len(positional) != 2 or any(not item.strip() for item in positional):
        raise ValueError(LOGS_USAGE)
    return {"server": positional[0], "character": positional[1], **result}


def realm_from_catalog(catalog, server_label: str) -> str:
    """Use a World directory only for realm, never as an FFLogs server ID."""
    try:
        world = catalog.find("world", server_label)
        regions = {world.region}
    except QueryResolutionError as exc:
        if not exc.choices:
            if exc.code in {ErrorCode.UPSTREAM_ERROR, ErrorCode.UNSUPPORTED}:
                raise
            raise QueryResolutionError(
                "服务器目录中没有该服务器，请核对名称。", ErrorCode.NOT_FOUND
            ) from None
        regions = {entry.region for entry in exc.choices}
        if "China" in regions and len(regions) != 1:
            raise QueryResolutionError(
                "服务器名称对应多个区域，请使用明确的服务器名称。"
            ) from None
    if regions == {"China"}:
        return "cn"
    if regions and regions.issubset(GLOBAL_REGIONS):
        return "global"
    raise QueryResolutionError("该服务器所属区域尚未支持。", ErrorCode.UNSUPPORTED)


async def resolve_realm(
    http, cache, server_label: str, *, deadline: float, metrics=None
) -> str:
    from .market_sources import MarketSourceClient

    session = MarketSourceClient(None).start_bound(http, cache, deadline=deadline)
    public = await session.catalog()
    if metrics is not None and len(public.provenance) == 2:
        for operation, provenance in zip(
            ("UniversalisWorlds", "UniversalisDataCenters"),
            public.provenance,
            strict=True,
        ):
            metrics.cache(operation, provenance.cached)
    return realm_from_catalog(public.catalog, server_label)
