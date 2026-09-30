"""Bounded FFLogs schema, server-directory, and output-metadata requests."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from yomihime_sdk.api.services import HttpRequest, SourceHttp

from .fflogs_models import (
    DifficultyRecord,
    EncounterRecord,
    FFLogsPayloadError,
    GameSpecRecord,
    MetadataCandidate,
    MetadataResolution,
    OutputMetadata,
    RegionRecord,
    ServerRecord,
    ServerResolution,
    ZoneRecord,
)

FFLOGS_GLOBAL_SOURCE = "fflogs_public_global"
FFLOGS_CN_SOURCE = "fflogs_public_cn"
GRAPHQL_PATH = "/api/v2/client"
PAGE_SIZE = 250
MAX_SERVER_PAGES = 64
MAX_REGIONS = 64
MAX_ZONES = 512
MAX_METADATA_ITEMS = 10_000
MAX_METADATA_MATCHES = 200
CN_REGION_SLUG = "cn"
GLOBAL_REGION_SLUGS = frozenset({"na", "eu", "jp", "oc"})
_GRAPHQL_NAME = re.compile(r"[_A-Za-z][_0-9A-Za-z]*\Z", re.ASCII)

REGIONS_QUERY = """query FF14WorldRegions {
  worldData { regions { id name compactName slug } }
}"""

# This is a fixed candidate projection. Runtime schema introspection must prove
# every field and type before the data query is sent.
OUTPUT_METADATA_INTROSPECTION = """query FF14OutputMetadataShape {
  queryType: __type(name: "Query") {
    name fields { name type { kind name ofType { kind name ofType { kind name } } }
      args { name defaultValue type { kind name ofType { kind name ofType { kind name } } } }
    }
  }
  worldDataType: __type(name: "WorldData") {
    name fields { name type { kind name ofType { kind name ofType { kind name } } }
      args { name defaultValue type { kind name ofType { kind name ofType { kind name } } } }
    }
  }
  zoneType: __type(name: "Zone") {
    name fields { name type { kind name ofType { kind name ofType { kind name } } }
      args { name defaultValue type { kind name ofType { kind name ofType { kind name } } } }
    }
  }
  difficultyType: __type(name: "Difficulty") {
    name fields { name type { kind name ofType { kind name ofType { kind name } } }
      args { name defaultValue type { kind name ofType { kind name ofType { kind name } } } }
    }
  }
  encounterType: __type(name: "Encounter") {
    name fields { name type { kind name ofType { kind name ofType { kind name } } }
      args { name defaultValue type { kind name ofType { kind name ofType { kind name } } } }
    }
  }
  gameDataType: __type(name: "GameData") {
    name fields { name type { kind name ofType { kind name ofType { kind name } } }
      args { name defaultValue type { kind name ofType { kind name ofType { kind name } } } }
    }
  }
  gameClassType: __type(name: "GameClass") {
    name fields { name type { kind name ofType { kind name ofType { kind name } } }
      args { name defaultValue type { kind name ofType { kind name ofType { kind name } } } }
    }
  }
  gameSpecType: __type(name: "GameSpec") {
    name fields { name type { kind name ofType { kind name ofType { kind name } } }
      args { name defaultValue type { kind name ofType { kind name ofType { kind name } } } }
    }
  }
}"""

OUTPUT_METADATA_QUERY = """query FF14OutputMetadata {
  worldData {
    zones { id name difficulties { id name } encounters { id name } }
  }
  gameData { classes { specs { id name slug } } }
}"""


class FFLogsGraphQLError(ValueError):
    """A GraphQL envelope contains a bounded, nonempty errors list."""


class FFLogsCatalog:
    """Resolve readable labels only against bounded, schema-checked lists."""

    def __init__(self, http: SourceHttp, source_id: str) -> None:
        self._http = http
        self._source_id = source_id

    async def resolve_server(self, realm: str, server_label: str) -> ServerResolution:
        regions = parse_regions(await self._request(REGIONS_QUERY, {}))
        selected_regions = tuple(
            region for region in regions if _region_belongs_to_realm(region, realm)
        )
        servers: list[ServerRecord] = []
        seen_server_ids: set[int] = set()
        seen_slugs: set[tuple[int, str, str]] = set()
        pages_used = 0

        for directory_region in selected_regions:
            seen_pages: set[tuple[int, ...]] = set()
            for page_number in range(1, MAX_SERVER_PAGES + 1):
                if pages_used >= MAX_SERVER_PAGES:
                    return ServerResolution(
                        False, (), "server directory exceeded its total page budget"
                    )
                page = parse_server_page(
                    await self._request(
                        _server_page_query(),
                        {
                            "regionId": directory_region.region_id,
                            "limit": PAGE_SIZE,
                            "page": page_number,
                        },
                    ),
                    expected_page=page_number,
                    expected_size=PAGE_SIZE,
                    directory_region=directory_region,
                )
                pages_used += 1
                signature = tuple(item.server_id for item in page.servers)
                if signature in seen_pages:
                    return ServerResolution(
                        False, (), "server directory repeated a page"
                    )
                seen_pages.add(signature)
                for server in page.servers:
                    if server.server_id in seen_server_ids:
                        return ServerResolution(
                            False, (), "server directory repeated an entry"
                        )
                    seen_server_ids.add(server.server_id)
                    slug_key = (
                        directory_region.region_id,
                        server.region.casefold(),
                        server.slug.casefold(),
                    )
                    if slug_key in seen_slugs:
                        return ServerResolution(
                            False, (), "server directory repeated a region-local slug"
                        )
                    seen_slugs.add(slug_key)
                    servers.append(server)
                if not page.has_more_pages:
                    break
            else:
                return ServerResolution(
                    False, (), "server region exceeded its page budget"
                )

        matches = tuple(
            server for server in servers if _server_matches(server, server_label)
        )
        return ServerResolution(True, matches)

    async def load_output_metadata(self) -> OutputMetadata:
        parse_output_metadata_projection(
            await self._request(OUTPUT_METADATA_INTROSPECTION, {})
        )
        return parse_output_metadata(await self._request(OUTPUT_METADATA_QUERY, {}))

    async def _request(
        self, query: str, variables: dict[str, object]
    ) -> dict[str, Any]:
        body = json.dumps(
            {"query": query, "variables": variables},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        response = await self._http.fetch(
            HttpRequest(
                self._source_id,
                GRAPHQL_PATH,
                method="POST",
                body=body,
                headers={"Content-Type": "application/json"},
            )
        )
        return parse_graphql_envelope(response.body)


def parse_graphql_envelope(body: bytes) -> dict[str, Any]:
    if not isinstance(body, bytes) or len(body) > 1_000_000:
        raise FFLogsPayloadError("GraphQL response exceeds its parser budget")
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise FFLogsPayloadError("GraphQL response is not bounded JSON") from None
    if not isinstance(value, dict):
        raise FFLogsPayloadError("GraphQL response must be an object")
    errors = value.get("errors")
    if errors is not None:
        if not isinstance(errors, list) or len(errors) > 32:
            raise FFLogsPayloadError("GraphQL error envelope is malformed")
        if errors:
            raise FFLogsGraphQLError("FFLogs GraphQL query failed")
    data = value.get("data")
    if not isinstance(data, dict):
        raise FFLogsPayloadError("GraphQL data projection is missing")
    return data


def parse_output_metadata_projection(payload: dict[str, Any]) -> None:
    """Prove the one fixed metadata projection before sending its query."""

    expected_types = (
        "Query",
        "WorldData",
        "Zone",
        "Difficulty",
        "Encounter",
        "GameData",
        "GameClass",
        "GameSpec",
    )
    types: dict[str, dict[str, dict[str, Any]]] = {}
    for type_name in expected_types:
        type_info = payload.get(f"{_camel(type_name)}Type")
        if not isinstance(type_info, dict) or type_info.get("name") != type_name:
            raise FFLogsPayloadError(f"{type_name} schema is unavailable")
        types[type_name] = _field_map(type_info, type_name)

    required_fields = (
        ("Query", "worldData", "WorldData", False),
        ("Query", "gameData", "GameData", False),
        ("WorldData", "zones", "Zone", True),
        ("Zone", "difficulties", "Difficulty", True),
        ("Zone", "encounters", "Encounter", True),
        ("GameData", "classes", "GameClass", True),
        ("GameClass", "specs", "GameSpec", True),
    )
    for owner, field_name, target, is_list in required_fields:
        field = types[owner].get(field_name)
        if field is None or not _args_are_optional(field.get("args")):
            raise FFLogsPayloadError(f"{owner}.{field_name} has unknown arguments")
        checker = _is_list_of if is_list else _is_object
        if not checker(field.get("type"), target):
            raise FFLogsPayloadError(f"{owner}.{field_name} has an unknown type")

    for owner in ("Zone", "Difficulty", "Encounter", "GameSpec"):
        expected_fields = [("id", "Int"), ("name", "String")]
        if owner == "GameSpec":
            expected_fields.append(("slug", "String"))
        for field_name, expected in expected_fields:
            field = types[owner].get(field_name)
            if (
                field is None
                or not _is_scalar(field.get("type"), expected)
                or not _args_are_optional(field.get("args"))
            ):
                raise FFLogsPayloadError(f"{owner}.{field_name} has an unknown type")


def parse_regions(payload: dict[str, Any]) -> tuple[RegionRecord, ...]:
    world = payload.get("worldData")
    rows = world.get("regions") if isinstance(world, dict) else None
    if not isinstance(rows, list) or len(rows) > MAX_REGIONS:
        raise FFLogsPayloadError("region directory is malformed")
    result: list[RegionRecord] = []
    seen_ids: set[int] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise FFLogsPayloadError("region entry is malformed")
        region_id = _positive_int(row.get("id"))
        name = _short_text(row.get("name"), 120)
        compact_name = _optional_text(row.get("compactName"), 64)
        slug = _short_text(row.get("slug"), 64)
        if region_id in seen_ids:
            raise FFLogsPayloadError("region directory repeats an id")
        seen_ids.add(region_id)
        result.append(RegionRecord(region_id, name, compact_name, slug))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class ServerPage:
    servers: tuple[ServerRecord, ...]
    has_more_pages: bool


def parse_server_page(
    payload: dict[str, Any],
    *,
    expected_page: int,
    expected_size: int,
    directory_region: RegionRecord,
) -> ServerPage:
    world = payload.get("worldData")
    region = world.get("region") if isinstance(world, dict) else None
    pagination = region.get("servers") if isinstance(region, dict) else None
    rows = pagination.get("data") if isinstance(pagination, dict) else None
    has_more = (
        pagination.get("has_more_pages") if isinstance(pagination, dict) else None
    )
    if (
        not isinstance(rows, list)
        or len(rows) > expected_size
        or type(has_more) is not bool
    ):
        raise FFLogsPayloadError("server pagination page is malformed")
    current_page = pagination.get("current_page")
    if type(current_page) is not int or current_page != expected_page:
        raise FFLogsPayloadError("server pagination page index is inconsistent")
    per_page = pagination.get("per_page")
    if type(per_page) is not int or per_page != expected_size:
        raise FFLogsPayloadError("server pagination size is inconsistent")
    if not rows and has_more:
        raise FFLogsPayloadError("empty server page claims more pages")

    servers: list[ServerRecord] = []
    page_ids: set[int] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise FFLogsPayloadError("server entry is malformed")
        server_id = _positive_int(row.get("id"))
        if server_id in page_ids:
            raise FFLogsPayloadError("server page repeats an id")
        page_ids.add(server_id)
        region_object = row.get("region")
        region_id = _nested_region_id(region_object)
        region_name = _nested_region_text(region_object, "name")
        region_slug = _nested_region_text(region_object, "slug")
        if (
            region_id != directory_region.region_id
            or region_slug.casefold() != directory_region.slug.casefold()
        ):
            raise FFLogsPayloadError("server region differs from its directory region")
        subregion_object = row.get("subregion")
        servers.append(
            ServerRecord(
                server_id,
                _short_text(row.get("name"), 120),
                _optional_text(row.get("normalizedName"), 120),
                _short_text(row.get("slug"), 100),
                region_id,
                region_name,
                region_slug,
                _nested_region_id(subregion_object),
                _nested_region_text(subregion_object, "name"),
                directory_region.region_id,
                directory_region.name,
                directory_region.compact_name,
                directory_region.slug,
            )
        )
    return ServerPage(tuple(servers), has_more)


def parse_output_metadata(payload: dict[str, Any]) -> OutputMetadata:
    world = payload.get("worldData")
    raw_zones = world.get("zones") if isinstance(world, dict) else None
    game = payload.get("gameData")
    raw_classes = game.get("classes") if isinstance(game, dict) else None
    if (
        not isinstance(raw_zones, list)
        or len(raw_zones) > MAX_ZONES
        or not isinstance(raw_classes, list)
        or len(raw_classes) > 64
    ):
        raise FFLogsPayloadError("output metadata lists are malformed or over budget")

    zones: list[ZoneRecord] = []
    seen_zone_ids: set[int] = set()
    item_count = 0
    for row in raw_zones:
        if not isinstance(row, dict):
            raise FFLogsPayloadError("zone metadata entry is malformed")
        zone_id = _positive_int(row.get("id"))
        if zone_id in seen_zone_ids:
            raise FFLogsPayloadError("zone metadata repeats an id")
        seen_zone_ids.add(zone_id)
        name = _short_text(row.get("name"), 160)
        raw_difficulties = row.get("difficulties")
        raw_encounters = row.get("encounters")
        if (
            not isinstance(raw_difficulties, list)
            or len(raw_difficulties) > 32
            or not isinstance(raw_encounters, list)
            or len(raw_encounters) > MAX_METADATA_ITEMS
        ):
            raise FFLogsPayloadError("zone difficulty or encounter list is malformed")
        difficulties = _parse_named_id_list(
            raw_difficulties, DifficultyRecord, "difficulty"
        )
        encounters = _parse_named_id_list(raw_encounters, EncounterRecord, "encounter")
        item_count += len(difficulties) + len(encounters)
        if item_count > MAX_METADATA_ITEMS:
            raise FFLogsPayloadError("output metadata exceeded its item budget")
        zones.append(ZoneRecord(zone_id, name, difficulties, encounters))

    specs: list[GameSpecRecord] = []
    seen_spec_ids: set[int] = set()
    for game_class in raw_classes:
        raw_specs = game_class.get("specs") if isinstance(game_class, dict) else None
        if not isinstance(raw_specs, list) or len(raw_specs) > 256:
            raise FFLogsPayloadError("game class spec list is malformed")
        parsed_specs = _parse_named_id_list(raw_specs, GameSpecRecord, "job spec")
        for spec in parsed_specs:
            if spec.spec_id in seen_spec_ids:
                raise FFLogsPayloadError("job spec metadata repeats an id")
            seen_spec_ids.add(spec.spec_id)
            specs.append(spec)
    item_count += len(specs)
    if item_count > MAX_METADATA_ITEMS:
        raise FFLogsPayloadError("output metadata exceeded its item budget")
    return OutputMetadata(tuple(zones), tuple(specs))


def resolve_output_metadata(
    metadata: OutputMetadata,
    encounter_label: str,
    difficulty_label: str,
    class_label: str,
) -> MetadataResolution:
    specs = tuple(
        item for item in metadata.specs if _same_label(item.name, class_label)
    )
    if not specs:
        return MetadataResolution(True, ())
    matches: list[MetadataCandidate] = []
    for zone in metadata.zones:
        zone_match = _same_label(zone.name, encounter_label)
        encounters = (
            zone.encounters
            if zone_match
            else tuple(
                item
                for item in zone.encounters
                if _same_label(item.name, encounter_label)
            )
        )
        if not encounters:
            continue
        difficulties = tuple(
            item
            for item in zone.difficulties
            if _same_label(item.name, difficulty_label)
        )
        for encounter in encounters:
            for difficulty in difficulties:
                for game_class in specs:
                    matches.append(
                        MetadataCandidate(
                            zone.zone_id,
                            zone.name,
                            encounter.encounter_id,
                            encounter.name,
                            difficulty.difficulty_id,
                            difficulty.name,
                            game_class.spec_id,
                            game_class.name,
                            game_class.slug,
                        )
                    )
                    if len(matches) > MAX_METADATA_MATCHES:
                        return MetadataResolution(
                            False, (), "output metadata match budget exceeded"
                        )
    return MetadataResolution(True, tuple(matches))


def _parse_named_id_list(rows: list[Any], model: type, label: str) -> tuple:
    if len(rows) > MAX_METADATA_ITEMS:
        raise FFLogsPayloadError(f"{label} list exceeded its item budget")
    result = []
    seen_ids: set[int] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise FFLogsPayloadError(f"{label} metadata entry is malformed")
        item_id = _positive_int(row.get("id"))
        if item_id in seen_ids:
            raise FFLogsPayloadError(f"{label} metadata repeats an id")
        seen_ids.add(item_id)
        name = _short_text(row.get("name"), 160)
        if model is DifficultyRecord:
            result.append(DifficultyRecord(item_id, name))
        elif model is EncounterRecord:
            result.append(EncounterRecord(item_id, name))
        elif model is GameSpecRecord:
            slug = _short_text(row.get("slug"), 100)
            result.append(GameSpecRecord(item_id, name, slug))
        else:
            raise TypeError("unsupported metadata model")
    return tuple(result)


def _field_map(type_info: dict[str, Any], type_name: str) -> dict[str, dict[str, Any]]:
    fields = type_info.get("fields")
    if not isinstance(fields, list) or len(fields) > 256:
        raise FFLogsPayloadError(f"{type_name} fields are malformed")
    result: dict[str, dict[str, Any]] = {}
    for field in fields:
        if not isinstance(field, dict):
            raise FFLogsPayloadError(f"{type_name} field is malformed")
        name = field.get("name")
        if not isinstance(name, str) or not _GRAPHQL_NAME.fullmatch(name):
            raise FFLogsPayloadError(f"{type_name} field name is malformed")
        if name in result:
            raise FFLogsPayloadError(f"{type_name} field is duplicated")
        result[name] = field
    return result


def _args_are_optional(value: object) -> bool:
    if not isinstance(value, list) or len(value) > 64:
        return False
    names: set[str] = set()
    for arg in value:
        if not isinstance(arg, dict):
            return False
        name = arg.get("name")
        if (
            not isinstance(name, str)
            or not _GRAPHQL_NAME.fullmatch(name)
            or name in names
        ):
            return False
        names.add(name)
        if _has_required_nonnull(arg.get("type")) and arg.get("defaultValue") is None:
            return False
    return True


def _has_required_nonnull(type_info: object) -> bool:
    return isinstance(type_info, dict) and type_info.get("kind") == "NON_NULL"


def _is_scalar(type_info: object, expected_name: str) -> bool:
    list_seen, name = _type_leaf(type_info)
    return not list_seen and name == expected_name


def _is_object(type_info: object, expected_name: str) -> bool:
    list_seen, name = _type_leaf(type_info)
    return not list_seen and name == expected_name


def _is_list_of(type_info: object, expected_name: str) -> bool:
    list_seen, name = _type_leaf(type_info)
    return list_seen and name == expected_name


def _type_leaf(type_info: object) -> tuple[bool, str | None]:
    current = type_info
    list_seen = False
    for _ in range(8):
        if not isinstance(current, dict):
            return list_seen, None
        kind = current.get("kind")
        if kind == "LIST":
            list_seen = True
        if kind in {"LIST", "NON_NULL"}:
            current = current.get("ofType")
            continue
        name = current.get("name")
        return list_seen, name if isinstance(name, str) else None
    return list_seen, None


def _server_page_query() -> str:
    return """query FF14WorldServers($regionId: Int!, $limit: Int!, $page: Int!) {
  worldData {
    region(id: $regionId) {
      servers(limit: $limit, page: $page) {
        data { id name normalizedName slug region { id name slug } subregion { id name } }
        current_page per_page has_more_pages
      }
    }
  }
}"""


def _positive_int(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 2_147_483_647:
        raise FFLogsPayloadError("directory id is invalid")
    return value


def _short_text(value: object, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise FFLogsPayloadError("directory text is invalid")
    if _has_control(value):
        raise FFLogsPayloadError("directory text contains control characters")
    return value.strip()


def _optional_text(value: object, limit: int) -> str | None:
    return None if value is None else _short_text(value, limit)


def _server_matches(server: ServerRecord, value: str) -> bool:
    key = _normalized_label(value)
    return key in {
        _normalized_label(server.name),
        _normalized_label(server.normalized_name or ""),
    }


def _region_belongs_to_realm(region: RegionRecord, realm: str) -> bool:
    slug = region.slug.casefold()
    return slug == CN_REGION_SLUG if realm == "cn" else slug in GLOBAL_REGION_SLUGS


def _nested_region_text(value: object, field: str) -> str:
    if not isinstance(value, dict):
        raise FFLogsPayloadError("server region projection is malformed")
    return _short_text(value.get(field), 120 if field == "name" else 64)


def _nested_region_id(value: object) -> int:
    if not isinstance(value, dict):
        raise FFLogsPayloadError("server region projection is malformed")
    return _positive_int(value.get("id"))


def _same_label(left: str, right: str) -> bool:
    return _normalized_label(left) == _normalized_label(right)


def _normalized_label(value: str) -> str:
    return " ".join(value.casefold().split())


def _has_control(value: str) -> bool:
    return any(ord(char) < 32 or 0x7F <= ord(char) <= 0x9F for char in value)


def _camel(value: str) -> str:
    first, *rest = value.split("_")
    return first[:1].lower() + first[1:] + "".join(item.title() for item in rest)


__all__ = [
    "FFLOGS_CN_SOURCE",
    "FFLOGS_GLOBAL_SOURCE",
    "FFLogsCatalog",
    "FFLogsGraphQLError",
    "GRAPHQL_PATH",
    "MAX_SERVER_PAGES",
    "OUTPUT_METADATA_INTROSPECTION",
    "OUTPUT_METADATA_QUERY",
    "PAGE_SIZE",
    "parse_graphql_envelope",
    "parse_output_metadata",
    "parse_output_metadata_projection",
    "parse_regions",
    "parse_server_page",
    "resolve_output_metadata",
]
