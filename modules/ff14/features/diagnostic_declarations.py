"""FF14-owned diagnostic field whitelist, not runtime authority."""

from yomihime_game_link_sdk import DiagnosticDeclaration


def text(limit=160, nullable=False, values=None):
    r = {"type": ["string", "null"] if nullable else "string", "maxLength": limit}
    if values is not None:
        r["enum"] = values
    return r


def integer(low=0, high=2147483647, nullable=False):
    return {
        "type": ["integer", "null"] if nullable else "integer",
        "minimum": low,
        "maximum": high,
    }


def boolean(nullable=False):
    return {"type": ["boolean", "null"] if nullable else "boolean"}


def obj(fields, optional=()):
    return {
        "type": "object",
        "properties": fields,
        "required": [key for key in fields if key not in optional],
        "additionalProperties": False,
    }


def array(items, limit):
    return {"type": "array", "items": items, "maxItems": limit}


def nullable(rule):
    return {**rule, "type": [rule["type"], "null"]}


NUMBER = text(160, True)
NAMED = obj({"id": NUMBER, "name": text(160, True)})
OWNER = obj({"id": NUMBER})
ROW = obj(
    {
        "raw_field_names": array(text(128), 64),
        "raw_field_names_truncated": boolean(),
        "encounter": NAMED,
        "rankPercent": NUMBER,
        "bestAmount": NUMBER,
        "totalKills": NUMBER,
        "bestSpec": text(80, True),
        "spec": text(80, True),
    }
)
# Projection converts numeric raw values to exact strings for one stable wire type.
SELECTION = nullable(
    obj(
        {
            "zone": NUMBER,
            "difficulty": NUMBER,
            "partition": NUMBER,
            "size": NUMBER,
            "metric": text(32, True),
            "rankings": array(ROW, 500),
        }
    )
)
CHARACTER = nullable(
    obj(
        {"rankings": SELECTION, **{f"z{i}": SELECTION for i in range(12)}},
        ["rankings", *[f"z{i}" for i in range(12)]],
    )
)
CHAR_BODY = obj({"characterData": obj({"character": CHARACTER})})
DIFFICULTY = obj(
    {**NAMED["properties"], "sizes": nullable(array(integer(), 32))}, ["sizes"]
)
ZONE = obj(
    {
        **NAMED["properties"],
        "difficulties": array(DIFFICULTY, 32),
        "encounters": array(obj({**NAMED["properties"], "zone": OWNER}, ["zone"]), 512),
        "frozen": boolean(True),
        "partitions": array(obj({**NAMED["properties"], "default": boolean(True)}), 64),
    },
    ["frozen", "partitions"],
)
META = obj(
    {
        "worldData": obj(
            {
                "zones": array(ZONE, 512),
                "expansions": array(
                    obj({**NAMED["properties"], "zones": array(OWNER, 512)}), 64
                ),
            },
            ["expansions"],
        )
    }
)
WINNER = obj(
    {
        "category": text(values=["ultimate", "savage"]),
        "zone": integer(1),
        "difficulty": integer(1),
        "requested_partition": integer(-1, nullable=True),
        "partition": integer(-1, nullable=True),
        "encounter": integer(1),
        "composition": text(values=["standard", "nonstandard", "unknown"]),
        "echo": text(values=["excluded", "not_echo", "unknown"]),
        "rankPercent_exact": text(160, True),
        "bestAmount_model": {
            "type": ["number", "null"],
            "minimum": 0,
            "maximum": 1000000000,
        },
        "spec": text(80, True),
        "totalKills": integer(nullable=True),
        "state": text(
            values=["record", "no_records", "failed", "not_queried", "excluded_echo"]
        ),
        "history_complete": boolean(),
    }
)
COUNTS = "planned eligible cache_hit cache_positive cache_empty attempted fresh_positive fresh_empty missing_alias invalid request_failed not_queried cache_unsaved batches_attempted max_inflight read_ms fetch_ms write_ms total_ms history_budget_ms invocation_budget_ms output_reserve_ms".split()
OPTIONAL = {
    "metadata_sha256": text(64),
    "winners": array(WINNER, 40),
    "displayed": integer(0, 40),
    "total": integer(0, 10000),
    "complete": boolean(),
    "partition_eligible": integer(0, 10000),
    "partition_echo_excluded": integer(0, 10000),
    "partition_unknown": integer(0, 10000),
    "diagnostic_low_omitted": boolean(),
}
HISTORY = obj({**{key: integer() for key in COUNTS}, **OPTIONAL}, OPTIONAL)
REQUEST_OPS = "UniversalisWorlds UniversalisDataCenters OtherBusinessRead FF14WorldRegions FF14WorldServers FF14PublicCharacter FF14PublicCharacterZone FF14OutputMetadataShape FF14CharacterMetadata FF14OutputMetadata FF14CatalogCharacter".split()
REQUEST = obj(
    {
        "kind": text(values=["module_source_http_fetch"]),
        "operations": array(
            obj(
                {
                    "operation": text(values=REQUEST_OPS),
                    **{
                        key: integer()
                        for key in "attempts returned failed cancelled elapsed_ms".split()
                    },
                    "cache": text(values=["module_hit", "module_miss", "unknown"]),
                }
            ),
            11,
        ),
        "elapsed_ms": integer(),
        "underlying_http_cache": text(values=["unknown"]),
        "host_oauth": text(values=["unobserved"]),
        "network_attempts": text(values=["unknown"]),
    }
)


def type_schema(depth=0):
    fields = {"kind": text(32, True), "name": text(128, True)}
    if depth < 5:
        fields["ofType"] = type_schema(depth + 1)
    return nullable(obj(fields, ["ofType"]))


FIELD = obj(
    {
        "name": text(128),
        "type": type_schema(),
        "args": array(
            obj(
                {
                    "name": text(128),
                    "type": type_schema(),
                    "defaultValue": text(256, True),
                }
            ),
            64,
        ),
    }
)
SHAPE = obj(
    {
        key: nullable(obj({"name": text(128, True), "fields": array(FIELD, 256)}))
        for key in "queryType worldDataType zoneType difficultyType encounterType gameDataType gameClassType gameSpecType expansionType partitionType".split()
    }
)
PAYLOADS = {
    "FF14OutputMetadataShape": SHAPE,
    "FF14CharacterMetadata": META,
    "FF14OutputMetadata": META,
    "FF14PublicCharacter": CHAR_BODY,
    "FF14PublicCharacterZone": CHAR_BODY,
    "FF14CatalogCharacter": CHAR_BODY,
    "FF14MetadataState": obj(
        {
            "state": text(
                values="verified_catalog base_catalog unavailable_source unavailable_graphql unavailable_payload unavailable_service unavailable_permission unavailable_timeout unavailable_internal".split()
            )
        }
    ),
    "FF14RequestSummary": REQUEST,
    "FF14HistorySummary": HISTORY,
    "FF14DutyOrderState": obj(
        {
            "state": text(values=["complete", "incomplete", "unavailable", "deadline"]),
            "complete": boolean(),
            "pages": integer(0, 2),
            "rows": integer(0, 200),
            "matched": integer(0, 10000),
            "unmatched": integer(0, 10000),
        }
    ),
}


# Source and response metadata are explicit; identity/trace belong only to Core.
def envelope(payload):
    return obj(
        {
            "source": text(values=["fflogs_public_cn", "fflogs_public_global"]),
            "status": integer(100, 599, True),
            "cached": boolean(),
            "payload": payload,
        }
    )


def compact_schema(rule):
    import json
    from collections import Counter

    counts = Counter()

    def count(value):
        if isinstance(value, dict):
            if type(value.get("type")) in (str, list):
                counts[json.dumps(value, sort_keys=True, separators=(",", ":"))] += 1
            for child in value.values():
                count(child)
        elif isinstance(value, list):
            for child in value:
                count(child)

    count(rule)
    ids = {value: f"s{i}" for i, (value, n) in enumerate(counts.items()) if n > 0}
    definitions = {}

    def rewrite(value, root=False):
        if isinstance(value, dict):
            key = json.dumps(value, sort_keys=True, separators=(",", ":"))
            if not root and key in ids:
                name = ids[key]
                if name not in definitions:
                    definitions[name] = rewrite(value, True)
                return {"$ref": "#/$defs/" + name}
            return {key: rewrite(child) for key, child in value.items()}
        if isinstance(value, list):
            return [rewrite(child) for child in value]
        return value

    result = rewrite(rule, True)
    if definitions:
        result["$defs"] = definitions
    return result


DECLARATIONS = tuple(
    DiagnosticDeclaration(key, compact_schema(envelope(value)))
    for key, value in PAYLOADS.items()
)
