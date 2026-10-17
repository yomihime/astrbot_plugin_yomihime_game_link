"""Bounded, identity-free evidence from existing explicit Logs source calls."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from secrets import token_hex
from time import monotonic

LOGGER_NAME = "yomihime_game_link.ff14.evidence"
MARKER = "FF14_LOGS_EVIDENCE"
MAX_BYTES = 512 * 1024
MAX_CHUNKS = 64
CHUNK_BYTES = 6000
LOW_CHUNKS = 47
SUMMARY_CHUNKS = 8
WINNER_STATES = frozenset(
    {"record", "no_records", "failed", "not_queried", "excluded_echo"}
)
OPERATIONS = frozenset(
    {
        "FF14OutputMetadataShape",
        "FF14CharacterMetadata",
        "FF14OutputMetadata",
        "FF14PublicCharacter",
        "FF14PublicCharacterZone",
        "FF14CatalogCharacter",
        "FF14MetadataState",
        "FF14RequestSummary",
        "FF14HistorySummary",
        "FF14DutyOrderState",
    }
)
_NAME = re.compile(r"[_A-Za-z][_0-9A-Za-z]{0,127}\Z")
_LOGGER = logging.getLogger(LOGGER_NAME)
REQUEST_OPERATIONS = frozenset(
    {
        "UniversalisWorlds",
        "UniversalisDataCenters",
        "OtherBusinessRead",
        "FF14WorldRegions",
        "FF14WorldServers",
        "FF14PublicCharacter",
        "FF14PublicCharacterZone",
        "FF14OutputMetadataShape",
        "FF14CharacterMetadata",
        "FF14OutputMetadata",
        "FF14CatalogCharacter",
    }
)


class RequestMetrics:
    """Invocation-local business fetch counts, never socket/OAuth telemetry."""

    def __init__(self):
        self.started = monotonic()
        self.operations = {}
        self.evidence = None

    def _entry(self, operation):
        return self.operations.setdefault(
            operation,
            {
                "operation": operation,
                "attempts": 0,
                "returned": 0,
                "failed": 0,
                "cancelled": 0,
                "elapsed_ms": 0,
                "cache": "unknown",
            },
        )

    def cache(self, operation, cached):
        if operation in REQUEST_OPERATIONS and type(cached) is bool:
            self._entry(operation)["cache"] = "module_hit" if cached else "module_miss"

    def wrap(self, http):
        metrics = self

        class ObservedHttp:
            async def fetch(self, request):
                operation = "OtherBusinessRead"
                if request.source_id == "universalis_market":
                    if request.path.endswith("/worlds"):
                        operation = "UniversalisWorlds"
                    elif request.path.endswith("/data-centers"):
                        operation = "UniversalisDataCenters"
                elif request.source_id in {"fflogs_public_cn", "fflogs_public_global"}:
                    try:
                        query = json.loads(request.body).get("query", "")
                        match = re.match(r"\s*query\s+([_A-Za-z][_0-9A-Za-z]*)", query)
                        if match and match[1] in REQUEST_OPERATIONS:
                            operation = match[1]
                    except (TypeError, ValueError, AttributeError):
                        pass
                entry = metrics._entry(operation)
                entry["attempts"] += 1
                started = monotonic()
                try:
                    response = await http.fetch(request)
                    entry["returned"] += 1
                    if response.status_code >= 400:
                        entry["failed"] += 1
                    return response
                except asyncio.CancelledError:
                    entry["cancelled"] += 1
                    raise
                except Exception:
                    entry["failed"] += 1
                    raise
                finally:
                    entry["elapsed_ms"] += max(0, int((monotonic() - started) * 1000))

        return ObservedHttp()

    def finish(self):
        if self.evidence is not None:
            self.evidence.observe(
                "FF14RequestSummary",
                {
                    "kind": "module_source_http_fetch",
                    "operations": list(self.operations.values()),
                    "elapsed_ms": max(0, int((monotonic() - self.started) * 1000)),
                    "underlying_http_cache": "unknown",
                    "host_oauth": "unobserved",
                    "network_attempts": "unknown",
                },
                status=None,
            )


def _text(value, limit=160):
    if (
        not isinstance(value, str)
        or len(value) > limit
        or any(ord(c) < 32 for c in value)
    ):
        return None
    if re.search(
        r"https?://|bearer\s|authorization|client_secret|access_token", value, re.I
    ):
        return None
    return value


def _number(value):
    if isinstance(value, Decimal):
        return str(value) if value.is_finite() and len(str(value)) <= 160 else None
    if type(value) is int and abs(value) <= 2**53:
        return value
    if type(value) is float:
        return _number(Decimal(str(value)))
    if isinstance(value, str) and len(value) <= 160:
        try:
            return value if Decimal(value).is_finite() else None
        except ArithmeticError:
            pass
    return None


def _named(row):
    if not isinstance(row, dict):
        return {}
    result = {"id": _number(row.get("id")), "name": _text(row.get("name"))}
    return result


def _type(row, depth=0):
    if not isinstance(row, dict) or depth > 5:
        return None
    result = {"kind": _text(row.get("kind"), 32), "name": _text(row.get("name"), 128)}
    if row.get("ofType") is not None:
        result["ofType"] = _type(row["ofType"], depth + 1)
    return result


def _winner(row):
    if not isinstance(row, dict):
        return None

    def identifier(value, low=1, nullable=False):
        return (
            nullable
            and value is None
            or type(value) is int
            and low <= value <= 2147483647
        )

    def numeric(value, high):
        if value is None:
            return True
        if type(value) not in {int, float, str} or len(str(value)) > 160:
            return False
        try:
            number = Decimal(str(value))
            return number.is_finite() and 0 <= number <= high
        except ArithmeticError:
            return False

    required = {
        "category",
        "zone",
        "difficulty",
        "requested_partition",
        "partition",
        "encounter",
        "composition",
        "echo",
        "rankPercent_exact",
        "bestAmount_model",
        "spec",
        "totalKills",
        "state",
        "history_complete",
    }
    if set(row) != required or not (
        row["category"] in {"ultimate", "savage"}
        and identifier(row["zone"])
        and identifier(row["difficulty"])
        and identifier(row["requested_partition"], -1, True)
        and identifier(row["partition"], -1, True)
        and identifier(row["encounter"])
        and row["composition"] in {"standard", "nonstandard", "unknown"}
        and row["echo"] in {"excluded", "not_echo", "unknown"}
        and (row["rankPercent_exact"] is None or type(row["rankPercent_exact"]) is str)
        and (
            row["rankPercent_exact"] is None
            or re.fullmatch(
                r"[+-]?[0-9]+(?:\.[0-9]+)?(?:[Ee][+-]?[0-9]+)?",
                row["rankPercent_exact"],
            )
            is not None
        )
        and numeric(row["rankPercent_exact"], 100)
        and type(row["bestAmount_model"]) in {int, float, type(None)}
        and numeric(row["bestAmount_model"], 1000000000)
        and (row["spec"] is None or _text(row["spec"], 80) == row["spec"])
        and (
            row["totalKills"] is None
            or type(row["totalKills"]) is int
            and 0 <= row["totalKills"] <= 2147483647
        )
        and row["state"] in WINNER_STATES
        and type(row["history_complete"]) is bool
    ):
        return None
    return dict(row)


def project(operation, payload):
    """Closed projection; never retains identity, URLs, errors or auth fields."""
    if operation not in OPERATIONS or not isinstance(payload, dict):
        return {}
    if operation == "FF14DutyOrderState":
        return {
            "state": payload.get("state")
            if payload.get("state")
            in {"complete", "incomplete", "unavailable", "deadline"}
            else "unavailable",
            "complete": payload.get("complete") is True,
            **{
                key: payload[key]
                if type(payload.get(key)) is int and 0 <= payload[key] <= limit
                else 0
                for key, limit in (
                    ("pages", 2),
                    ("rows", 200),
                    ("matched", 10000),
                    ("unmatched", 10000),
                )
            },
        }
    if operation == "FF14HistorySummary":
        # Closed, identity-free counts. Never put one entry per target in facts.
        fields = (
            "planned",
            "eligible",
            "cache_hit",
            "cache_positive",
            "cache_empty",
            "attempted",
            "fresh_positive",
            "fresh_empty",
            "missing_alias",
            "invalid",
            "request_failed",
            "not_queried",
            "cache_unsaved",
            "batches_attempted",
            "max_inflight",
            "read_ms",
            "fetch_ms",
            "write_ms",
            "total_ms",
            "history_budget_ms",
            "invocation_budget_ms",
            "output_reserve_ms",
        )
        result = {
            key: payload[key]
            if type(payload.get(key)) is int and 0 <= payload[key] <= 2147483647
            else 0
            for key in fields
        }
        winners = payload.get("winners")
        digest = payload.get("metadata_sha256")
        if (
            isinstance(digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", digest)
            and isinstance(winners, list)
            and len(winners) <= 40
            and all(_winner(row) is not None for row in winners)
            and type(payload.get("displayed")) is int
            and payload["displayed"] == len(winners)
            and type(payload.get("total")) is int
            and len(winners) <= payload["total"] <= 10000
            and type(payload.get("complete")) is bool
        ):
            result.update(
                metadata_sha256=digest,
                winners=winners,
                displayed=len(winners),
                total=payload["total"],
                complete=payload["complete"],
            )
            for key in (
                "partition_eligible",
                "partition_echo_excluded",
                "partition_unknown",
            ):
                if type(payload.get(key)) is int and 0 <= payload[key] <= 10000:
                    result[key] = payload[key]
            if type(payload.get("diagnostic_low_omitted")) is bool:
                result["diagnostic_low_omitted"] = payload["diagnostic_low_omitted"]
        return result
    if operation == "FF14RequestSummary":

        def count(value):
            return value if type(value) is int and 0 <= value <= 2147483647 else 0

        return {
            "kind": "module_source_http_fetch",
            "operations": [
                {
                    "operation": row["operation"],
                    **{
                        key: count(row.get(key))
                        for key in (
                            "attempts",
                            "returned",
                            "failed",
                            "cancelled",
                            "elapsed_ms",
                        )
                    },
                    "cache": row.get("cache")
                    if row.get("cache") in {"module_hit", "module_miss", "unknown"}
                    else "unknown",
                }
                for row in (payload.get("operations") or ())[: len(REQUEST_OPERATIONS)]
                if isinstance(row, dict) and row.get("operation") in REQUEST_OPERATIONS
            ],
            "elapsed_ms": count(payload.get("elapsed_ms")),
            "underlying_http_cache": "unknown",
            "host_oauth": "unobserved",
            "network_attempts": "unknown",
        }
    if operation == "FF14MetadataState":
        states = {
            "verified_catalog",
            "base_catalog",
            "unavailable_source",
            "unavailable_graphql",
            "unavailable_payload",
            "unavailable_service",
            "unavailable_permission",
            "unavailable_timeout",
            "unavailable_internal",
        }
        return {
            "state": payload.get("state")
            if payload.get("state") in states
            else "unavailable_internal"
        }
    if operation == "FF14OutputMetadataShape":
        result = {}
        for key in (
            "queryType",
            "worldDataType",
            "zoneType",
            "difficultyType",
            "encounterType",
            "gameDataType",
            "gameClassType",
            "gameSpecType",
            "expansionType",
            "partitionType",
        ):
            row = payload.get(key)
            if not isinstance(row, dict):
                result[key] = None
                continue
            fields = []
            for field in (row.get("fields") or ())[:256]:
                if not isinstance(field, dict) or not _NAME.fullmatch(
                    field.get("name", "")
                ):
                    continue
                args = [
                    {
                        "name": arg["name"],
                        "type": _type(arg.get("type")),
                        "defaultValue": _text(arg.get("defaultValue"), 256),
                    }
                    for arg in (field.get("args") or ())[:64]
                    if isinstance(arg, dict)
                    and isinstance(arg.get("name"), str)
                    and _NAME.fullmatch(arg["name"])
                ]
                fields.append(
                    {
                        "name": field["name"],
                        "type": _type(field.get("type")),
                        "args": args,
                    }
                )
            result[key] = {"name": _text(row.get("name"), 128), "fields": fields}
        return result
    if operation in {"FF14CharacterMetadata", "FF14OutputMetadata"}:
        world = payload.get("worldData", {})
        result = {"worldData": {"zones": []}}
        if not isinstance(world, dict):
            return result
        for zone in world.get("zones", ())[:512]:
            if not isinstance(zone, dict):
                continue
            value = _named(zone)
            value["difficulties"] = [
                {
                    **_named(row),
                    **(
                        {
                            "sizes": None
                            if row["sizes"] is None
                            else [_number(item) for item in row["sizes"][:32]]
                        }
                        if isinstance(row, dict)
                        and "sizes" in row
                        and (row["sizes"] is None or isinstance(row["sizes"], list))
                        else {}
                    ),
                }
                for row in zone.get("difficulties", ())[:32]
            ]
            if "frozen" in zone and (
                zone["frozen"] is None or type(zone["frozen"]) is bool
            ):
                value["frozen"] = zone["frozen"]
            if isinstance(zone.get("partitions"), list):
                value["partitions"] = [
                    {
                        **_named(row),
                        "default": row.get("default")
                        if type(row.get("default")) is bool
                        else None,
                    }
                    for row in zone["partitions"][:64]
                    if isinstance(row, dict)
                ]
            value["encounters"] = []
            for encounter in zone.get("encounters", ())[:10000]:
                entry = _named(encounter)
                if isinstance(encounter, dict) and isinstance(
                    encounter.get("zone"), dict
                ):
                    entry["zone"] = {"id": _number(encounter["zone"].get("id"))}
                value["encounters"].append(entry)
            result["worldData"]["zones"].append(value)
        if isinstance(world.get("expansions"), list):
            result["worldData"]["expansions"] = [
                {
                    **_named(row),
                    "zones": [
                        {"id": _number(zone.get("id"))}
                        for zone in row.get("zones", ())[:512]
                        if isinstance(zone, dict)
                    ],
                }
                for row in world["expansions"][:64]
                if isinstance(row, dict)
            ]
        return result
    container = payload.get("characterData", {})
    character = container.get("character") if isinstance(container, dict) else None
    result = {"characterData": {"character": None}}
    if not isinstance(character, dict):
        return result
    selections = {}
    for alias, selection in character.items():
        if alias != "rankings" and not re.fullmatch(r"z[0-9]{1,2}", alias):
            continue
        if not isinstance(selection, dict):
            selections[alias] = None
            continue
        value = {
            key: _number(selection.get(key))
            for key in ("zone", "difficulty", "partition", "size")
        }
        value["metric"] = _text(selection.get("metric"), 32)
        value["rankings"] = []
        for row in selection.get("rankings", ())[:500]:
            if not isinstance(row, dict):
                continue
            raw_fields = sorted(
                key for key in row if isinstance(key, str) and _NAME.fullmatch(key)
            )
            value["rankings"].append(
                {
                    "raw_field_names": raw_fields[:64],
                    "raw_field_names_truncated": len(raw_fields) > 64
                    or len(raw_fields) != len(row),
                    "encounter": _named(row.get("encounter")),
                    "rankPercent": _number(row.get("rankPercent")),
                    "bestAmount": _number(row.get("bestAmount")),
                    "totalKills": _number(row.get("totalKills")),
                    "bestSpec": _text(row.get("bestSpec"), 80),
                    "spec": _text(row.get("spec"), 80),
                }
            )
        selections[alias] = value
    result["characterData"]["character"] = selections
    return result


def metadata_payload(metadata):
    expansions = {}
    zones = []
    for zone in metadata.zones:
        zones.append(
            {
                "id": zone.zone_id,
                "name": zone.name,
                "difficulties": [
                    {
                        "id": row.difficulty_id,
                        "name": row.name,
                        "sizes": None if row.sizes is None else list(row.sizes),
                    }
                    for row in zone.difficulties
                ],
                "encounters": [
                    {
                        "id": row.encounter_id,
                        "name": row.name,
                        "zone": {"id": row.zone_id},
                    }
                    for row in zone.encounters
                ],
                "frozen": zone.frozen,
                "partitions": [
                    {"id": row.partition_id, "name": row.name, "default": row.default}
                    for row in zone.partitions
                ],
            }
        )
        if zone.expansion_id is not None:
            expansion = expansions.setdefault(
                zone.expansion_id,
                {"id": zone.expansion_id, "name": zone.expansion_name, "zones": []},
            )
            expansion["zones"].append({"id": zone.zone_id})
    order = {item: index for index, item in enumerate(metadata.expansion_order)}
    return {
        "worldData": {
            "zones": zones,
            "expansions": sorted(
                expansions.values(), key=lambda item: order.get(item["id"], len(order))
            ),
        }
    }


def metadata_digest(metadata):
    """Reconstructible from the safe catalog: no job specs or private fields."""
    return hashlib.sha256(
        json.dumps(
            project("FF14CharacterMetadata", metadata_payload(metadata)),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    ).hexdigest()


def winner_summary(character, metadata, limit=40):
    """Final selected/displayed rows, including when fetched from history cache."""
    from .fflogs_cards import displayed_rankings
    from .fflogs_catalog import (
        partition_composition,
        savage_partition_qualification,
        select_character_zones,
    )

    displayed = displayed_rankings(character, limit)
    owners = {id(row): group for group in character.groups for row in group.rankings}
    zones = {zone.zone_id: zone for zone in metadata.zones}
    winners = []
    for row in displayed:
        group, origin = owners[id(row)], row.origin
        zone = zones[group.zone_id]
        period = next(
            (
                item
                for item in zone.partitions
                if origin is not None and item.partition_id == origin.partition
            ),
            None,
        )
        rule = (
            savage_partition_qualification(period)
            if group.category == "savage" and period is not None
            else "unknown"
        )
        winners.append(
            {
                "category": group.category,
                "zone": origin.zone_id if origin else group.zone_id,
                "difficulty": origin.difficulty_id if origin else group.difficulty_id,
                "requested_partition": origin.requested_partition if origin else None,
                "partition": origin.partition if origin else None,
                "encounter": row.encounter_id,
                "composition": (
                    origin.composition
                    or partition_composition(origin.partition_name)
                    or "unknown"
                )
                if origin
                else "unknown",
                "echo": "excluded"
                if row.query_state == "excluded_echo"
                else "not_echo"
                if rule in {"standard", "nonstandard"}
                else "unknown",
                "rankPercent_exact": str(row.rank_percent_exact)
                if row.rank_percent_exact is not None
                else str(row.rank_percent)
                if row.rank_percent is not None
                else None,
                "bestAmount_model": row.best_amount,
                "spec": row.spec,
                "totalKills": row.total_kills,
                "state": row.query_state,
                "history_complete": row.history_complete,
            }
        )
    counts = {
        "partition_eligible": 0,
        "partition_echo_excluded": 0,
        "partition_unknown": 0,
    }
    for zone, _, category in select_character_zones(metadata, character.zone_id):
        if category != "savage":
            continue
        if not zone.partitions:
            counts["partition_unknown"] += 1
        for period in zone.partitions:
            qualification = savage_partition_qualification(period)
            counts[
                "partition_eligible"
                if qualification in {"standard", "nonstandard"}
                else "partition_echo_excluded"
                if qualification == "echo"
                else "partition_unknown"
            ] += 1
    return {
        "metadata_sha256": metadata_digest(metadata),
        "winners": winners,
        "displayed": len(winners),
        "total": len(character.rankings),
        "complete": character.catalog_complete,
        **counts,
    }


def _exceeds_projection_limits(operation, payload):
    if operation in {"FF14MetadataState", "FF14RequestSummary", "FF14DutyOrderState"}:
        return False
    if operation == "FF14OutputMetadataShape":
        for row in payload.values():
            if not isinstance(row, dict):
                continue
            fields = row.get("fields") or ()
            if len(fields) > 256 or any(
                len(field.get("args") or ()) > 64
                for field in fields
                if isinstance(field, dict)
            ):
                return True
    elif operation in {"FF14CharacterMetadata", "FF14OutputMetadata"}:
        world = payload.get("worldData") or {}
        zones, expansions = world.get("zones") or (), world.get("expansions") or ()
        return (
            len(zones) > 512
            or len(expansions) > 64
            or sum(
                len(row.get("encounters") or ()) + len(row.get("difficulties") or ())
                for row in zones
                if isinstance(row, dict)
            )
            > 10000
            or any(
                len(row.get("difficulties") or ()) > 32
                for row in zones
                if isinstance(row, dict)
            )
            or any(
                len(row.get("zones") or ()) > 512
                for row in expansions
                if isinstance(row, dict)
            )
            or any(
                len(row.get("partitions") or ()) > 64
                or any(
                    len(item.get("sizes") or ()) > 32
                    for item in row.get("difficulties", ())
                    if isinstance(item, dict)
                )
                for row in zones
                if isinstance(row, dict)
            )
        )
    else:
        character = (payload.get("characterData") or {}).get("character") or {}
        return any(
            len(row.get("rankings") or ()) > 500
            for row in character.values()
            if isinstance(row, dict)
        )
    return False


class Evidence:
    def __init__(self, source):
        self.source = source
        self.correlation = token_hex(12)
        self.bytes = 0
        self.chunks = 0
        self.limited = False
        self.low_chunks = 0
        self.high_chunks = 0
        self.summary_count = 0
        self.low_limited = False
        self.sentinel = False

    def observe(self, operation, payload, *, status=200, cached=False, filters=None):
        try:
            self._observe(
                operation, payload, status=status, cached=cached, filters=filters
            )
        except Exception:
            # Diagnostic failures never affect the source business result.
            return

    def _observe(self, operation, payload, *, status, cached, filters):
        if (
            self.limited
            or not _LOGGER.isEnabledFor(logging.INFO)
            or not any(
                getattr(handler, "fflogs_evidence_sink", False)
                for handler in _LOGGER.handlers
            )
        ):
            return
        if operation not in OPERATIONS or self.source not in {
            "fflogs_public_cn",
            "fflogs_public_global",
        }:
            return
        if (
            operation == "FF14HistorySummary"
            and isinstance(payload, dict)
            and "winners" in payload
        ):
            payload = {**payload, "diagnostic_low_omitted": self.low_limited}
        projection_limited = _exceeds_projection_limits(operation, payload)
        safe = {} if projection_limited else project(operation, payload)
        body = json.dumps(
            safe, ensure_ascii=True, separators=(",", ":"), allow_nan=False
        )
        pieces = [
            body[index : index + CHUNK_BYTES]
            for index in range(0, len(body), CHUNK_BYTES)
        ]
        high = operation == "FF14HistorySummary"
        if high and (len(pieces) > SUMMARY_CHUNKS or self.summary_count >= 2):
            return
        low_limited = not high and (
            self.low_limited or self.low_chunks + len(pieces) > LOW_CHUNKS
        )
        truncated = (
            projection_limited
            or self.bytes + len(body) > MAX_BYTES
            or self.chunks + len(pieces) > MAX_CHUNKS - int(not self.sentinel)
            or low_limited
        )
        if truncated:
            if low_limited:
                self.low_limited = True
                if self.sentinel:
                    return
            pieces = ["{}"]
            self.sentinel = True
            if not low_limited:
                self.limited = True
        if self.chunks >= MAX_CHUNKS:
            return
        if self.bytes + sum(len(piece) for piece in pieces) > MAX_BYTES:
            self.limited = True
            return
        self.bytes += sum(len(piece) for piece in pieces)
        self.chunks += len(pieces)
        if not truncated:
            if high:
                self.high_chunks += len(pieces)
                self.summary_count += 1
            else:
                self.low_chunks += len(pieces)
        safe_filters = {
            key: value
            for key, value in (filters or {}).items()
            if (
                key in {"metric", "zoneId", "difficulty", "partition"}
                or re.fullmatch(r"[zdp](?:[0-9]|1[01])", key)
            )
            and (type(value) is int or value in {"rdps", "ndps", "cdps"})
        }
        for index, piece in enumerate(pieces):
            event = {
                "v": 1,
                "correlation": self.correlation,
                "source": self.source,
                "operation": operation,
                "time": datetime.now(timezone.utc).isoformat(),
                "status": status,
                "cached": cached,
                "filters": safe_filters,
                "chunk": index,
                "chunks": len(pieces),
                "truncated": truncated,
                "payload": piece,
            }
            _LOGGER.info(
                MARKER + " %s",
                json.dumps(event, separators=(",", ":"), ensure_ascii=True),
            )
