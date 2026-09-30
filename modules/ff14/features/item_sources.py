"""FF14 item-source requests and bounded source-shape normalization."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from yomihime_sdk.api.services import HttpRequest, SourceHttp, SourceHttpError

from ..models import (
    AcquisitionKind,
    AcquisitionRoute,
    ItemAmount,
    ItemCandidate,
    ItemRecord,
    ItemSearchPage,
)

XIVAPI_SOURCE = "xivapi_items"
GARLAND_SOURCE = "garland_items"
XIVAPI_SEARCH_PATH = "/api/search"
XIVAPI_ITEM_PATH = "/api/sheet/Item"
GARLAND_ITEM_PATH = "/db/doc/item/chs/3"
PAGE_SIZE = 3
MAX_SEARCH_PAGES = 2
MAX_ROUTE_ITEMS_PER_GROUP = 5
MAX_TOTAL_ROUTES = 24
MAX_COSTS_PER_ROUTE = 8
MAX_PARTIALS = 512
_ASCII_INTEGER = re.compile(r"[0-9]+\Z", re.ASCII)


class ItemPayloadError(ValueError):
    """An upstream response does not match the bounded item shape."""


@dataclass(frozen=True, slots=True)
class ItemLookupSnapshot:
    record: ItemRecord
    partial: bool


class ItemSourceClient:
    """Use only a request-bound SourceHttp port and fixed source paths."""

    def __init__(self, http: SourceHttp) -> None:
        self._http = http

    async def search(self, query: str) -> tuple[tuple[ItemCandidate, ...], bool]:
        cursor: str | None = None
        seen_cursors: set[str] = set()
        found: list[ItemCandidate] = []
        seen_ids: set[int] = set()

        for page_number in range(MAX_SEARCH_PAGES):
            if cursor is None:
                parameters = (
                    ("sheets", "Item"),
                    ("query", f'Name~"{query}"'),
                    ("fields", "Name"),
                    ("language", "chs"),
                    ("limit", str(PAGE_SIZE)),
                )
            else:
                parameters = (
                    ("fields", "Name"),
                    ("language", "chs"),
                    ("limit", str(PAGE_SIZE)),
                    ("cursor", cursor),
                )
            response = await self._http.fetch(
                HttpRequest(XIVAPI_SOURCE, XIVAPI_SEARCH_PATH, query=parameters)
            )
            page = parse_item_search_page(_json_object(response.body))
            for candidate in page.candidates:
                if candidate.item_id not in seen_ids:
                    seen_ids.add(candidate.item_id)
                    found.append(candidate)
            if page.next_cursor is None:
                return tuple(found), False
            if page.next_cursor in seen_cursors:
                raise ItemPayloadError("repeated cursor")
            seen_cursors.add(page.next_cursor)
            if page_number + 1 == MAX_SEARCH_PAGES:
                return tuple(found), True
            cursor = page.next_cursor

        return tuple(found), False

    async def lookup(self, item_id: int) -> ItemLookupSnapshot:
        response = await self._http.fetch(
            HttpRequest(
                XIVAPI_SOURCE,
                f"{XIVAPI_ITEM_PATH}/{item_id}",
                query=(
                    ("fields", "Name,Description,LevelItem,LevelEquip"),
                    ("language", "chs"),
                ),
            )
        )
        primary = parse_xivapi_item_detail(
            _json_object(response.body), requested_id=item_id
        )
        warnings: tuple[str, ...]
        routes: tuple[AcquisitionRoute, ...]
        try:
            garland_response = await self._http.fetch(
                HttpRequest(
                    GARLAND_SOURCE,
                    f"{GARLAND_ITEM_PATH}/{item_id}.json",
                )
            )
            routes, warnings = parse_garland_acquisition(
                _json_object(garland_response.body), requested_id=item_id
            )
        except SourceHttpError as exc:
            routes = ()
            warnings = (_source_warning(exc),)
        except ItemPayloadError:
            routes = ()
            warnings = ("Garland 获取途径响应格式无法识别。",)
        record = ItemRecord(
            item_id=primary.item_id,
            name=primary.name,
            description=primary.description,
            item_level=primary.item_level,
            equip_level=primary.equip_level,
            routes=routes,
            source_urls=(
                f"https://xivapi-v2.xivcdn.com/api/sheet/Item/{item_id}",
                f"https://garlandtools.cn/db/#item/{item_id}",
            ),
            warnings=warnings,
        )
        partial = bool(warnings) or any(route.partial for route in routes)
        return ItemLookupSnapshot(record, partial)


def parse_item_search_page(payload: dict[str, Any]) -> ItemSearchPage:
    """Parse the small search envelope without selecting a row."""

    results = payload.get("results")
    if not isinstance(results, list) or len(results) > PAGE_SIZE:
        raise ItemPayloadError("invalid results")
    candidates: list[ItemCandidate] = []
    for row in results:
        if not isinstance(row, dict):
            raise ItemPayloadError("invalid search row")
        row_id = row.get("row_id")
        fields = row.get("fields")
        name = fields.get("Name") if isinstance(fields, dict) else None
        if type(row_id) is not int or row_id < 1 or not _short_text(name, 160):
            raise ItemPayloadError("invalid candidate")
        candidates.append(ItemCandidate(row_id, name.strip()))
    try:
        next_cursor = _opaque_cursor(payload.get("next"))
        return ItemSearchPage(
            candidates=tuple(candidates),
            next_cursor=next_cursor,
            schema=_optional_text(payload.get("schema"), 128),
            version=_optional_text(payload.get("version"), 128),
        )
    except (TypeError, ValueError) as exc:
        raise ItemPayloadError("invalid search metadata") from exc


def parse_xivapi_item_detail(
    payload: dict[str, Any], *, requested_id: int
) -> ItemRecord:
    """Extract display fields and a nested item-level relationship safely."""

    if type(payload.get("row_id")) is not int or payload.get("row_id") != requested_id:
        raise ItemPayloadError("item identity mismatch")
    fields = payload.get("fields")
    if not isinstance(fields, dict):
        raise ItemPayloadError("missing fields")
    name = fields.get("Name")
    if not _short_text(name, 160):
        raise ItemPayloadError("missing name")
    description = fields.get("Description")
    if description is not None and not _short_text(description, 1_000):
        description = None
    equip_level = fields.get("LevelEquip")
    if type(equip_level) is not int or equip_level < 0:
        equip_level = None
    item_level: int | None = None
    relation = fields.get("LevelItem")
    if isinstance(relation, dict):
        relation_value = relation.get("value")
        if type(relation_value) is int and relation_value >= 0:
            item_level = relation_value
    return ItemRecord(
        item_id=requested_id,
        name=name.strip(),
        description=description.strip() if isinstance(description, str) else None,
        item_level=item_level,
        equip_level=equip_level,
    )


def parse_garland_acquisition(
    payload: dict[str, Any], *, requested_id: int
) -> tuple[tuple[AcquisitionRoute, ...], tuple[str, ...]]:
    """Resolve only typed `(partial.type, partial.id)` links from Garland."""

    item = payload.get("item")
    partials = payload.get("partials", [])
    if not isinstance(item, dict) or _integer(item.get("id")) != requested_id:
        raise ItemPayloadError("Garland item identity mismatch")
    if not isinstance(partials, list) or len(partials) > MAX_PARTIALS:
        raise ItemPayloadError("invalid Garland partials")
    linked: dict[tuple[str, str], dict[str, Any]] = {}
    for partial in partials:
        if not isinstance(partial, dict):
            continue
        kind = partial.get("type")
        identifier = _id_text(partial.get("id"))
        obj = partial.get("obj")
        if isinstance(kind, str) and identifier is not None and isinstance(obj, dict):
            linked[(kind, identifier)] = obj

    warnings: list[str] = []
    routes: list[AcquisitionRoute] = []
    group_specs = (
        ("nodes", AcquisitionKind.NODE, "node", "採集點"),
        ("fishingSpots", AcquisitionKind.FISHING, "fishing", "釣場"),
        ("vendors", AcquisitionKind.VENDOR, "npc", "商販"),
        ("drops", AcquisitionKind.DROP, "mob", "掉落來源"),
        ("instances", AcquisitionKind.INSTANCE, "instance", "副本"),
        ("quests", AcquisitionKind.QUEST, "quest", "任務"),
    )
    recognized_groups = 0
    for field, kind, partial_type, label in group_specs:
        if field not in item:
            continue
        recognized_groups += 1
        identifiers = item[field]
        if not isinstance(identifiers, list):
            warnings.append(f"Garland 的 {label}字段无法解析。")
            continue
        if len(identifiers) > MAX_ROUTE_ITEMS_PER_GROUP:
            warnings.append(f"Garland 的 {label}列表已截断。")
        for identifier_value in identifiers[:MAX_ROUTE_ITEMS_PER_GROUP]:
            identifier = _id_text(identifier_value)
            if identifier is None:
                warnings.append(f"Garland 的 {label}含无效记录。")
                continue
            route, unresolved = _linked_route(
                kind,
                label,
                identifier,
                linked.get((partial_type, identifier)),
            )
            routes.append(route)
            if unresolved:
                warnings.append(f"Garland 的{label}详情未能按类型和 ID 关联。")

    embedded_specs = (
        ("craft", AcquisitionKind.CRAFT, "制作"),
        ("tradeCurrency", AcquisitionKind.CURRENCY_TRADE, "货币兑换"),
        ("tradeShops", AcquisitionKind.SHOP_TRADE, "商店兑换"),
    )
    for field, kind, label in embedded_specs:
        if field not in item:
            continue
        recognized_groups += 1
        entries = item[field]
        if not isinstance(entries, list):
            warnings.append(f"Garland 的 {label}字段无法解析。")
            continue
        if len(entries) > MAX_ROUTE_ITEMS_PER_GROUP:
            warnings.append(f"Garland 的 {label}列表已截断。")
        for entry in entries[:MAX_ROUTE_ITEMS_PER_GROUP]:
            if not isinstance(entry, dict):
                warnings.append(f"Garland 的 {label}含无效记录。")
                continue
            route, unresolved = _embedded_route(kind, label, entry, linked)
            routes.append(route)
            if unresolved:
                warnings.append(f"Garland 的{label}关联信息不完整。")

    if recognized_groups == 0:
        warnings.append("Garland 未提供可识别的获取途径记录；这不代表物品不可获得。")
    elif not routes and not warnings:
        warnings.append("Garland 本次没有可解析的获取途径；这不代表物品不可获得。")
    if len(routes) > MAX_TOTAL_ROUTES:
        routes = routes[:MAX_TOTAL_ROUTES]
        warnings.append("获取途径总数已截断。")
    return tuple(routes), tuple(dict.fromkeys(warnings))


def _linked_route(
    kind: AcquisitionKind,
    label: str,
    identifier: str,
    obj: dict[str, Any] | None,
) -> tuple[AcquisitionRoute, bool]:
    details: list[str] = [f"记录 ID：{identifier}"]
    unresolved = obj is None
    if obj is not None:
        name = obj.get("n")
        if _short_text(name, 160):
            details.insert(0, name.strip())
        else:
            unresolved = True
        zone = obj.get("z", obj.get("l"))
        if _short_text(zone, 120):
            details.append(f"区域：{zone.strip()}")
        coordinates = obj.get("c")
        if (
            isinstance(coordinates, (list, tuple))
            and len(coordinates) == 2
            and all(type(value) in {int, float} for value in coordinates)
        ):
            details.append(f"坐标：{coordinates[0]}, {coordinates[1]}")
    if unresolved:
        details.append("来源详情未关联")
    return AcquisitionRoute(kind, label, tuple(details), partial=unresolved), unresolved


def _embedded_route(
    kind: AcquisitionKind,
    label: str,
    entry: dict[str, Any],
    linked: dict[tuple[str, str], dict[str, Any]],
) -> tuple[AcquisitionRoute, bool]:
    details: list[str] = []
    unresolved = False
    shop = entry.get("shop")
    if _short_text(shop, 120):
        details.append(f"商店：{shop.strip()}")
    job = entry.get("job")
    level = entry.get("lvl")
    if type(job) is int and job >= 0:
        details.append(f"职业 ID：{job}")
    if type(level) is int and level >= 0:
        details.append(f"等级：{level}")

    costs: list[ItemAmount] = []
    if kind is AcquisitionKind.CRAFT:
        raw_costs = entry.get("ingredients", [])
    else:
        raw_costs = []
        listings = entry.get("listings", [])
        if not isinstance(listings, list):
            listings = []
            unresolved = True
        for listing in listings[:MAX_ROUTE_ITEMS_PER_GROUP]:
            if not isinstance(listing, dict):
                unresolved = True
                continue
            currency = listing.get("currency", [])
            if isinstance(currency, list):
                raw_costs.extend(currency[:MAX_COSTS_PER_ROUTE])
            else:
                unresolved = True
    if not isinstance(raw_costs, list):
        raw_costs = []
        unresolved = True
    if len(raw_costs) > MAX_COSTS_PER_ROUTE:
        unresolved = True
    for raw in raw_costs[:MAX_COSTS_PER_ROUTE]:
        if not isinstance(raw, dict):
            unresolved = True
            continue
        item_id = _integer(raw.get("id"))
        amount = _integer(raw.get("amount"))
        if item_id is None or amount is None or amount < 1:
            unresolved = True
            continue
        partial = linked.get(("item", str(item_id)))
        name = partial.get("n") if partial is not None else None
        if not _short_text(name, 120):
            name = None
            unresolved = True
        costs.append(ItemAmount(item_id, amount, name))
    if costs:
        rendered = "、".join(
            f"{cost.name or f'物品 ID {cost.item_id}'} × {cost.amount}"
            for cost in costs
        )
        details.append(f"材料/货币：{rendered}")
    elif kind is not AcquisitionKind.CRAFT or entry.get("ingredients"):
        unresolved = True
    if not details:
        details.append("来源记录存在，但未提供可展示的细节")
        unresolved = True
    return AcquisitionRoute(
        kind, label, tuple(details), tuple(costs), unresolved
    ), unresolved


def _source_warning(error: SourceHttpError) -> str:
    if error.code == "rate_limited" or error.status_code == 429:
        return "Garland 请求频率受限；基础物品信息仍来自 XIVAPI-compatible。"
    if error.status_code == 404:
        return "Garland 未返回该 ID 的详情；这不代表物品不可获得。"
    return "Garland 暂不可用；基础物品信息仍来自 XIVAPI-compatible。"


def _json_object(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ItemPayloadError("invalid JSON") from None
    if not isinstance(value, dict):
        raise ItemPayloadError("expected object")
    return value


def _opaque_cursor(value: object) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or value.startswith(("http://", "https://", "//"))
        or _has_control(value)
    ):
        raise ValueError("invalid cursor")
    return value


def _short_text(value: object, limit: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= limit
        and not _has_control(value)
    )


def _optional_text(value: object, limit: int) -> str | None:
    if value is None:
        return None
    if not _short_text(value, limit):
        raise ValueError("invalid optional text")
    return value


def _has_control(value: str) -> bool:
    return any(ord(char) < 32 or 0x7F <= ord(char) <= 0x9F for char in value)


def _integer(value: object) -> int | None:
    maximum = 2_147_483_647
    if type(value) is int:
        return value if 1 <= value <= maximum else None
    if isinstance(value, str) and len(value) <= 10 and _ASCII_INTEGER.fullmatch(value):
        parsed = int(value)
        return parsed if 1 <= parsed <= maximum else None
    return None


def _id_text(value: object) -> str | None:
    parsed = _integer(value)
    return str(parsed) if parsed is not None and parsed > 0 else None


__all__ = [
    "GARLAND_SOURCE",
    "XIVAPI_SOURCE",
    "ItemLookupSnapshot",
    "ItemPayloadError",
    "ItemSourceClient",
    "parse_garland_acquisition",
    "parse_item_search_page",
    "parse_xivapi_item_detail",
]
