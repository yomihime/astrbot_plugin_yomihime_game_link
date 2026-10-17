"""Optional FF14 duty-menu ordering from the managed public game-data source.

SortKey is menu order, not a release date. No ranking or version provenance is
created here; FFLogs identities are matched only by unique normalized full names.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from time import monotonic

from yomihime_game_link_sdk.errors import ServiceUnavailable, SourceHttpError
from yomihime_game_link_sdk.services import HttpRequest

from .fflogs_catalog import normalized_encounter_name

SOURCE = "xivapi_items"
PATH = "/api/search"
FIELDS = "Name,SortKey,ContentType@as(raw)"
PAGE_LIMIT = 100
MAX_PAGES = 2
CACHE_SECONDS = 86400
FAILURE_SECONDS = 60


@dataclass(frozen=True, slots=True)
class DutyOrder:
    names: tuple[tuple[str, int], ...] = ()
    complete: bool = False


def parse_page(body):
    if not isinstance(body, bytes) or len(body) > 262144:
        raise ValueError("duty catalog size")
    value = json.loads(body)
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("results"), list)
        or len(value["results"]) > PAGE_LIMIT
    ):
        raise ValueError("duty catalog shape")
    rows = []
    for row in value["results"]:
        if not isinstance(row, dict) or row.get("sheet") != "ContentFinderCondition":
            raise ValueError("duty catalog sheet")
        fields = row.get("fields")
        if not isinstance(fields, dict):
            raise ValueError("duty catalog fields")
        name, rank, kind = (
            fields.get("Name"),
            fields.get("SortKey"),
            fields.get("ContentType@as(raw)"),
        )
        # XIVAPI raw projections retain the requested projection as field key.
        if (
            not isinstance(name, str)
            or not 1 <= len(name.strip()) <= 160
            or type(rank) is not int
            or not 0 < rank <= 2147483647
            or type(kind) is not int
            or kind != 28
        ):
            raise ValueError("duty catalog row")
        rows.append((normalized_encounter_name(name).casefold(), rank))
    cursor = value.get("next")
    if cursor is not None and (
        not isinstance(cursor, str)
        or not 1 <= len(cursor) <= 4096
        or any(ord(c) < 32 for c in cursor)
    ):
        raise ValueError("duty catalog cursor")
    return rows, cursor


class DutyOrderCatalog:
    """Per-handler bounded cache; never retain request-bound HTTP authority."""

    def __init__(self):
        self._epoch = object()
        self._cached = None

    def close(self):
        self._epoch = object()
        self._cached = None

    async def load(self, http, *, deadline=None):
        if self._cached is not None and self._cached[0] > monotonic():
            return self._cached[1]
        remaining = 3 if deadline is None else min(3, max(0, deadline - monotonic()))
        if remaining <= 0:
            return DutyOrder()
        epoch = self._epoch
        try:
            async with asyncio.timeout(remaining):
                rows, seen, cursor, complete = [], set(), None, False
                for _ in range(MAX_PAGES):
                    query = (
                        ("fields", FIELDS),
                        ("language", "chs"),
                        ("limit", str(PAGE_LIMIT)),
                    )
                    query += (
                        (("cursor", cursor),)
                        if cursor is not None
                        else (
                            ("sheets", "ContentFinderCondition"),
                            ("query", "ContentType=28"),
                        )
                    )
                    response = await http.fetch(HttpRequest(SOURCE, PATH, query=query))
                    if response.status_code != 200:
                        raise ValueError("duty catalog status")
                    page, cursor = parse_page(response.body)
                    rows.extend(page)
                    if cursor is None:
                        complete = True
                        break
                    if cursor in seen:
                        raise ValueError("repeated duty cursor")
                    seen.add(cursor)
                result = DutyOrder(tuple(rows), complete)
            ttl = CACHE_SECONDS if result.complete else FAILURE_SECONDS
        except SourceHttpError as error:
            if error.code in {"source_not_declared", "request_rejected"}:
                raise PermissionError() from None
            if error.code == "cancelled":
                raise ServiceUnavailable() from None
            result, ttl = DutyOrder(), FAILURE_SECONDS
        except (ServiceUnavailable, PermissionError):
            raise
        except Exception:
            result, ttl = DutyOrder(), FAILURE_SECONDS
        if self._epoch is not epoch:
            raise ServiceUnavailable()
        self._cached = (monotonic() + ttl, result)
        return result


def apply_duty_order(character, catalog):
    """Unique exact names only; unresolved groups retain their existing order."""
    if not catalog.complete:
        return character
    candidates = {}
    for name, rank in catalog.names:
        candidates.setdefault(name, []).append(rank)
    names = {name: values[0] for name, values in candidates.items() if len(values) == 1}
    ultimate = [group for group in character.groups if group.category == "ultimate"]
    group_names = [
        normalized_encounter_name(group.rankings[0].encounter_name).casefold()
        if group.rankings
        else ""
        for group in ultimate
    ]
    unique = {name for name in group_names if group_names.count(name) == 1}
    ordered = sorted(
        enumerate(ultimate),
        key=lambda item: (0, -names[group_names[item[0]]], item[0])
        if group_names[item[0]] in names and group_names[item[0]] in unique
        else (1, 0, item[0]),
    )
    groups = tuple(group for _, group in ordered) + tuple(
        group for group in character.groups if group.category != "ultimate"
    )
    return replace(
        character,
        groups=groups,
        rankings=tuple(row for group in groups for row in group.rankings),
    )
