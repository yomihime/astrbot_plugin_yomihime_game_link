"""Bounded, identity-free evidence from existing explicit Logs source calls."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from secrets import token_hex

LOGGER_NAME = "yomihime_game_link.ff14.evidence"
MARKER = "FF14_LOGS_EVIDENCE"
MAX_BYTES = 512 * 1024
MAX_CHUNKS = 64
CHUNK_BYTES = 6000
OPERATIONS = frozenset(
    {
        "FF14OutputMetadataShape",
        "FF14CharacterMetadata",
        "FF14OutputMetadata",
        "FF14PublicCharacter",
        "FF14PublicCharacterZone",
        "FF14CatalogCharacter",
        "FF14MetadataState",
    }
)
_NAME = re.compile(r"[_A-Za-z][_0-9A-Za-z]{0,127}\Z")
_LOGGER = logging.getLogger(LOGGER_NAME)


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


def project(operation, payload):
    """Closed projection; never retains identity, URLs, errors or auth fields."""
    if operation not in OPERATIONS or not isinstance(payload, dict):
        return {}
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
                _named(row) for row in zone.get("difficulties", ())[:32]
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
                    {"id": row.difficulty_id, "name": row.name}
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
            }
        )
        if zone.expansion_id is not None:
            expansion = expansions.setdefault(
                zone.expansion_id,
                {"id": zone.expansion_id, "name": zone.expansion_name, "zones": []},
            )
            expansion["zones"].append({"id": zone.zone_id})
    return {"worldData": {"zones": zones, "expansions": list(expansions.values())}}


def _exceeds_projection_limits(operation, payload):
    if operation == "FF14MetadataState":
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
        projection_limited = _exceeds_projection_limits(operation, payload)
        safe = {} if projection_limited else project(operation, payload)
        body = json.dumps(
            safe, ensure_ascii=True, separators=(",", ":"), allow_nan=False
        )
        pieces = [
            body[index : index + CHUNK_BYTES]
            for index in range(0, len(body), CHUNK_BYTES)
        ]
        truncated = (
            projection_limited
            or self.bytes + len(body) > MAX_BYTES
            or self.chunks + len(pieces) >= MAX_CHUNKS
        )
        if truncated:
            pieces = ["{}"]
            self.limited = True
        if self.chunks >= MAX_CHUNKS:
            return
        self.bytes += sum(len(piece) for piece in pieces)
        self.chunks += len(pieces)
        safe_filters = {
            key: value
            for key, value in (filters or {}).items()
            if (
                key in {"metric", "zoneId", "difficulty", "partition"}
                or re.fullmatch(r"[zd][0-9]{1,2}", key)
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
