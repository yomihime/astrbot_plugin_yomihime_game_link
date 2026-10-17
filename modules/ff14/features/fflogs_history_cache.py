"""Bounded FF14 ranking snapshots; cache never determines query coverage."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from time import time

from .fflogs_models import FFLogsPayloadError, parse_rankings

HISTORY_CACHE_TTL = 900
MAX_CACHE_BYTES = 65_536
MAX_CACHE_NODES = 512
POLICY = "explicit-standard-history-pack-v2"


def _digest(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
    ).hexdigest()


def history_identity(source, character, variables, metadata, owner=None):
    """Only a fresh, public, identity-checked character can select snapshots."""
    catalog = _digest(asdict(metadata))
    return _digest(
        [
            POLICY,
            source,
            "cn" if source.endswith("_cn") else "global",
            character.character_id,
            character.canonical_id,
            character.name,
            character.server_name,
            variables["serverSlug"],
            variables["serverRegion"],
            catalog,
            getattr(getattr(owner, "origin", None), "value", None),
            getattr(owner, "actor_id", None),
            getattr(owner, "grant_id", None),
            getattr(owner, "grant_revision", None),
        ]
    )


def snapshot_key(identity, requested):
    # JSON preserves null, -1 and each positive partition independently.
    return "fflogs-history-" + _digest([identity, requested])


def cache_packs(metadata, requested):
    """Stable directory bounds, never observed rows or acquisition times."""
    counts = {zone.zone_id: len(zone.encounters) for zone in metadata.zones}
    groups, current, nodes = [], [], 7
    for key in requested:
        # Outer pack:7; entry/request:10; snapshot:19+15 per full source row.
        weight = 29 + 15 * counts[key[0]]
        if current and (nodes + weight > MAX_CACHE_NODES or len(current) >= 12):
            groups.append(tuple(current))
            current, nodes = [], 7
        current.append(key)
        nodes += weight
    if current:
        groups.append(tuple(current))
    return tuple(groups)


def cache_pack_key(identity, planned):
    return "fflogs-history-pack-" + _digest([POLICY, identity, planned])


def pack_payload(identity, snapshots, planned):
    value = {
        "version": POLICY,
        "identity": identity,
        "entries": [
            {"requested": list(key), "snapshot": snapshots[key]}
            for key in planned
            if key in snapshots
        ],
    }
    _bounded(value)
    return value


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(child) for child in value]
    return value


def restore_pack(payload, identity, planned):
    value = _plain(payload)
    _bounded(value)
    if (
        set(value) != {"version", "identity", "entries"}
        or value["version"] != POLICY
        or value["identity"] != identity
        or not isinstance(value["entries"], list)
        or len(value["entries"]) > len(planned)
    ):
        raise FFLogsPayloadError("history pack identity")
    snapshots, selections, dates, seen = {}, {}, {}, set()
    for entry in value["entries"]:
        if not isinstance(entry, dict) or set(entry) != {"requested", "snapshot"}:
            raise FFLogsPayloadError("history pack entry")
        requested = entry["requested"]
        if not isinstance(requested, list) or len(requested) != 6:
            raise FFLogsPayloadError("history pack scope")
        if (
            type(requested[0]) is not int
            or type(requested[1]) is not int
            or type(requested[2]) not in (int, type(None))
            or type(requested[3]) is not str
            or type(requested[4]) not in (str, type(None))
            or requested[5] is not None
        ):
            raise FFLogsPayloadError("history pack scope types")
        key = tuple(requested)
        if key not in planned or key in seen:
            raise FFLogsPayloadError("history pack scope")
        seen.add(key)
        snapshot = entry["snapshot"]
        if isinstance(snapshot, dict) and type(snapshot.get("fetched_at")) in (
            int,
            float,
        ):
            # A later sibling write must not renew this scope's acquisition TTL.
            if time() - snapshot["fetched_at"] >= HISTORY_CACHE_TTL:
                continue
        selection, fetched = restore_snapshot(entry["snapshot"])
        snapshots[key], selections[key], dates[key] = (
            entry["snapshot"],
            selection,
            fetched,
        )
    return snapshots, selections, dates


def _bounded(value):
    remaining = MAX_CACHE_NODES

    def visit(item):
        nonlocal remaining
        remaining -= 1
        if remaining < 0:
            raise FFLogsPayloadError("history snapshot node limit")
        if isinstance(item, Mapping):
            for key, child in item.items():
                visit(key)
                visit(child)
        elif isinstance(item, (tuple, list)):
            for child in item:
                visit(child)

    visit(value)
    if (
        len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode())
        > MAX_CACHE_BYTES
    ):
        raise FFLogsPayloadError("history snapshot byte limit")


def snapshot_payload(selection, *, fetched_at=None):
    selection_data = {
        "metric": selection.metric,
        "zone": selection.zone_id,
        "difficulty": selection.difficulty_id,
        "partition": selection.partition,
        "size": selection.size,
        "rankings": [
            {
                "encounter": {"id": row.encounter_id, "name": row.encounter_name},
                "rankPercent": str(row.rank_percent_exact)
                if row.rank_percent_exact is not None
                else None,
                "bestAmount": row.best_amount,
                "bestSpec": row.spec,
                "totalKills": row.total_kills,
            }
            for row in selection.rows
        ],
    }
    payload = {
        "version": POLICY,
        "fetched_at": time() if fetched_at is None else fetched_at,
        "selection": selection_data,
    }
    _bounded(payload)
    return payload


def restore_snapshot(payload):
    """Revalidate the narrow JSON projection through the production parser."""

    value = _plain(payload)
    _bounded(value)
    if (
        set(value) != {"version", "fetched_at", "selection"}
        or value["version"] != POLICY
    ):
        raise FFLogsPayloadError("history snapshot version")
    fetched = value["fetched_at"]
    if (
        type(fetched) not in (int, float)
        or not 0 <= time() - fetched < HISTORY_CACHE_TTL
    ):
        raise FFLogsPayloadError("history snapshot time")
    selection = value["selection"]
    if not isinstance(selection, dict) or set(selection) != {
        "metric",
        "zone",
        "difficulty",
        "partition",
        "size",
        "rankings",
    }:
        raise FFLogsPayloadError("history snapshot selection")
    rows = selection["rankings"]
    if not isinstance(rows, list) or len(rows) > 500:
        raise FFLogsPayloadError("history snapshot rows")
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "encounter",
            "rankPercent",
            "bestAmount",
            "bestSpec",
            "totalKills",
        }:
            raise FFLogsPayloadError("history snapshot row")
        raw = row["rankPercent"]
        if raw is not None:
            if type(raw) is not str or len(raw) > 132:
                raise FFLogsPayloadError("history snapshot number")
            try:
                row["rankPercent"] = Decimal(raw)
            except InvalidOperation:
                raise FFLogsPayloadError("history snapshot number") from None
    return parse_rankings(selection), fetched
