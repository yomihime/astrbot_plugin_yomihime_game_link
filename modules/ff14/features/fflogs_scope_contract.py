"""Fixed, bounded schema validation for explicit FFLogs ranking filters."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from time import monotonic

from yomihime_game_link_sdk.services import HttpRequest

from .fflogs_catalog import GRAPHQL_PATH, parse_graphql_envelope
from .fflogs_models import FFLogsPayloadError


def _kind(value, *, present=True):
    if not present:
        return "missing"
    if value is None:
        return "null"
    return {
        bool: "boolean",
        int: "integer",
        float: "number",
        Decimal: "number",
        str: "string",
        list: "list",
        dict: "object",
    }.get(type(value), "other")


def _text_shape(value, maximum):
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= maximum
        and not any(ord(char) < 32 or 0x7F <= ord(char) <= 0x9F for char in value)
    )


class InvocationObservation:
    """One explicit FF14 web invocation's closed error-only observation."""

    def __init__(self, parameters, metrics):
        self.stage = "parameters"
        self.requested = {
            key: parameters.get(key)
            for key in (
                "realm",
                "zone_id",
                "difficulty",
                "partition",
                "timeframe",
                "spec_name",
            )
        }
        self.requested["metric"] = parameters.get("metric", "rdps").casefold()
        self.metrics = metrics
        self.last_http_status = None
        self.source_error = None
        self.shape = {}
        self.identity = {}
        self.selection = None
        self.selection_types = None
        self.row_count = None
        self.failure = {"guard": "stage_failed", "field": None, "index": None}
        self.schema_cache = "unknown"

    def wrap(self, http):
        observation = self

        class ObservedFF14Http:
            async def fetch(self, request):
                observation.last_http_status = None
                observation.source_error = None
                try:
                    response = await http.fetch(request)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    from yomihime_game_link_sdk.errors import SourceHttpError

                    if isinstance(error, SourceHttpError):
                        known = {
                            "invalid_response",
                            "rate_limited",
                            "timeout",
                            "credentials_unavailable",
                            "request_rejected",
                            "response_too_large",
                            "request_too_large",
                            "redirect_rejected",
                            "http_error",
                            "upstream_error",
                            "transport_error",
                            "network_error",
                        }
                        observation.source_error = (
                            error.code if error.code in known else "other_source_error"
                        )
                        status = error.status_code
                        observation.last_http_status = (
                            status
                            if type(status) is int and 100 <= status <= 599
                            else None
                        )
                    else:
                        observation.source_error = (
                            "timeout"
                            if isinstance(error, TimeoutError)
                            else "other_fetch_error"
                        )
                    raise
                status = response.status_code
                observation.last_http_status = (
                    status if type(status) is int and 100 <= status <= 599 else None
                )
                return response

        return ObservedFF14Http()

    def reject(
        self, guard, field=None, index=None, *, value=None, present=True, matches=None
    ):
        self.failure = {
            "guard": guard,
            "field": field,
            "index": index,
            "type": _kind(value, present=present),
            "present": present,
        }
        if field in {"bestSpec", "spec"}:
            self.failure.update(
                token_valid=isinstance(value, str)
                and TOKEN.fullmatch(value) is not None,
                non_ascii=isinstance(value, str) and not value.isascii(),
                matches=matches,
            )

    def character_shape(self, raw, expected_name, expected_server):
        self.shape["character"] = _kind(raw)
        if not isinstance(raw, dict):
            return
        server = raw.get("server")
        selection = raw.get("rankings")
        rows = selection.get("rankings") if isinstance(selection, dict) else None
        self.shape.update(
            server=_kind(server, present="server" in raw),
            rankings=_kind(selection, present="rankings" in raw),
            rows=_kind(
                rows, present=isinstance(selection, dict) and "rankings" in selection
            ),
        )
        server_name = server.get("name") if isinstance(server, dict) else None
        name = raw.get("name")
        name_valid, server_valid = _text_shape(name, 120), _text_shape(server_name, 120)

        def normalize(text):
            return " ".join(text.casefold().split())

        hidden = raw.get("hidden")
        identifier, canonical = raw.get("id"), raw.get("canonicalID")
        identifiers_valid = (
            type(identifier) is int
            and 1 <= identifier <= 2_147_483_647
            and (
                canonical is None
                or type(canonical) is int
                and 1 <= canonical <= 2_147_483_647
            )
        )
        self.identity = {
            "hidden": hidden if type(hidden) is bool else None,
            "character_shape_valid": identifiers_valid
            and name_valid
            and server_valid
            and type(hidden) is bool,
            "name_matches": normalize(name) == normalize(expected_name)
            if name_valid
            else None,
            "server_matches": normalize(server_name) == normalize(expected_server)
            if server_valid
            else None,
        }
        if (
            hidden is not False
            or not self.identity["character_shape_valid"]
            or self.identity["name_matches"] is not True
            or self.identity["server_matches"] is not True
        ):
            return
        if isinstance(selection, dict):
            self.selection_types = {
                key: _kind(selection.get(key), present=key in selection)
                for key in ("zone", "difficulty", "partition", "size", "metric")
            }
            self.selection = {
                key: selection.get(key)
                if type(selection.get(key)) is int and abs(selection[key]) <= 2**53
                else None
                for key in ("zone", "difficulty", "partition", "size")
            }
            self.selection["metric"] = (
                selection.get("metric")
                if type(selection.get("metric")) is str
                and selection["metric"] in {"rdps", "ndps", "cdps"}
                else None
            )
            self.row_count = len(rows) if isinstance(rows, list) else None

    def locate_parse_failure(self, raw):
        """Locate with the existing FF14 validators; never retain their text."""
        from . import fflogs_models as models

        if not isinstance(raw, dict):
            self.reject("parse_shape", "character", value=raw)
            return
        checks = [
            ("id", lambda value: models._positive_int(value, "id")),
            (
                "canonicalID",
                lambda value: models._optional_positive_int(value, "canonical id"),
            ),
            ("name", lambda value: models._short_text(value, 120, "name")),
            ("hidden", lambda value: type(value) is bool),
            ("server", lambda value: isinstance(value, dict)),
        ]

        def first_failure(container, validators, index=None, prefix=None):
            for key, validate in validators:
                try:
                    valid = validate(container.get(key))
                    if valid is False:
                        raise FFLogsPayloadError("observed invalid field")
                except (FFLogsPayloadError, TypeError, ValueError, ArithmeticError):
                    self.reject(
                        "parse_field",
                        f"{prefix}.{key}" if prefix else key,
                        index,
                        value=container.get(key),
                        present=key in container,
                    )
                    return True
            return False

        if first_failure(raw, checks):
            return
        if first_failure(
            raw["server"],
            [("name", lambda value: models._short_text(value, 120, "server name"))],
            prefix="server",
        ):
            return
        if (
            self.identity.get("hidden") is not False
            or self.identity.get("name_matches") is not True
            or self.identity.get("server_matches") is not True
        ):
            self.reject("parse_shape", "character", value=raw)
            return
        selection = raw.get("rankings")
        if not isinstance(selection, dict):
            self.reject(
                "parse_shape", "rankings", value=selection, present="rankings" in raw
            )
            return
        rows = selection.get("rankings")
        if not isinstance(rows, list) or len(rows) > 500:
            self.reject(
                "parse_shape", "rankings", value=rows, present="rankings" in selection
            )
            return
        headers = [
            (
                "metric",
                lambda value: models._short_text(value, 32, "metric").casefold()
                in {"rdps", "ndps", "cdps"},
            ),
            ("zone", lambda value: models._positive_int(value, "zone")),
            ("difficulty", lambda value: models._positive_int(value, "difficulty")),
            ("partition", models._bounded_partition),
            ("size", lambda value: models._nonnegative_int(value, "size")),
        ]
        if first_failure(selection, headers):
            return
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                self.reject("parse_shape", "row", index, value=row)
                return
            encounter = row.get("encounter")
            if not isinstance(encounter, dict):
                self.reject(
                    "parse_shape",
                    "encounter",
                    index,
                    value=encounter,
                    present="encounter" in row,
                )
                return
            if first_failure(
                encounter,
                [
                    ("id", lambda value: models._positive_int(value, "encounter id")),
                    (
                        "name",
                        lambda value: models._short_text(value, 160, "encounter name"),
                    ),
                ],
                index,
                prefix="encounter",
            ):
                return
            job_field = "bestSpec" if "bestSpec" in row else "spec"
            validators = [
                (
                    "rankPercent",
                    lambda value: models._optional_number(value, 0.0, 100.0),
                ),
                (
                    "bestAmount",
                    lambda value: models._optional_number(value, 0.0, 1_000_000_000.0),
                ),
                (
                    job_field,
                    lambda value: models._short_text(value, 80, "spec")
                    if value is not None
                    else None,
                ),
                (
                    "totalKills",
                    lambda value: models._nonnegative_int(value, "total kills")
                    if value is not None
                    else None,
                ),
            ]
            if first_failure(row, validators, index):
                return

    def attach(self, result):
        if result.error is None or result.model_facts is not None:
            return result
        from dataclasses import replace

        from yomihime_game_link_sdk.results import FactDocument

        def count(operation_names):
            rows = [self.metrics.operations.get(name, {}) for name in operation_names]
            return {
                key: min(2_147_483_647, sum(max(0, item.get(key, 0)) for item in rows))
                for key in ("attempts", "returned", "failed", "cancelled", "elapsed_ms")
            }

        observation = {
            "version": 1,
            "stage": self.stage,
            "requested": self.requested,
            "fetches": {
                "schema": count(("OtherBusinessRead",)),
                "directory": count(("FF14WorldRegions", "FF14WorldServers")),
                "character": count(("FF14PublicCharacterZone",)),
            },
            "last_http_status": self.last_http_status,
            "source_error": self.source_error,
            "elapsed_ms": min(
                2_147_483_647, max(0, int((monotonic() - self.metrics.started) * 1000))
            ),
            "underlying_cache": "unknown",
            "schema_cache": self.schema_cache,
            "shape": self.shape,
            "identity": self.identity,
            "selection": self.selection,
            "selection_types": self.selection_types,
            "row_count": self.row_count,
            "failure": self.failure,
            "observation_truncated": False,
        }

        def bounded(value):
            pending, visited = [(value, 0)], 0
            while pending:
                item, depth = pending.pop()
                visited += 1
                if (
                    visited > 480
                    or depth > 12
                    or isinstance(item, str)
                    and len(item) > 4096
                ):
                    return False
                if isinstance(item, dict):
                    pending.extend(
                        (child, depth + 1) for pair in item.items() for child in pair
                    )
                elif isinstance(item, list):
                    pending.extend((child, depth + 1) for child in item)
            return (
                len(
                    json.dumps(value, ensure_ascii=False, allow_nan=False).encode(
                        "utf-8"
                    )
                )
                <= 200_000
            )

        if not bounded(observation):
            observation = {
                key: observation[key]
                for key in (
                    "version",
                    "stage",
                    "requested",
                    "fetches",
                    "last_http_status",
                    "source_error",
                    "elapsed_ms",
                    "underlying_cache",
                )
            }
            observation.update(observation_truncated=True)
        return replace(
            result,
            model_facts=FactDocument(
                {
                    "status": "error",
                    "error": {
                        "code": result.error.code.value,
                        "message": result.error.message,
                    },
                    "supplement": {"fflogs_observation": observation},
                }
            ),
        )


TOKEN = re.compile(r"[_A-Za-z][_0-9A-Za-z]{0,79}\Z", re.ASCII)
MAX_FACT_ROWS = 12
MAX_ENUM_DESCRIPTION = 512
MAX_ENUM_TEXT = 4096
MAX_SCHEMA_HELP_BYTES = 16_384
SHAPE_QUERY = """query FF14CharacterRankingShape {
  timeframe: __type(name: "RankingTimeframeType") {
    name kind enumValues { name description }
  }
  character: __type(name: "Character") {
    name fields { name args {
      name defaultValue type { kind name ofType { kind name } }
    } }
  }
}"""


@dataclass(frozen=True)
class RankingContract:
    timeframe_type: str
    arguments: tuple[tuple[str, str, str | None], ...]
    timeframe_values: tuple[str, ...]
    timeframe_descriptions: tuple[str | None, ...]

    def supplement(self):
        return {
            "timeframe_enum": list(self.timeframe_values),
            "timeframe_descriptions": [
                {"name": name, "description": description}
                for name, description in zip(
                    self.timeframe_values, self.timeframe_descriptions
                )
            ],
            "arguments": [
                {"name": name, "type": kind, "default": default}
                for name, kind, default in self.arguments
            ],
        }


def _leaf(value):
    if not isinstance(value, dict):
        raise FFLogsPayloadError("ranking argument type is unavailable")
    if value.get("kind") == "NON_NULL":
        if value.get("name") is not None:
            raise FFLogsPayloadError("ranking argument wrapper is malformed")
        value = value.get("ofType")
    if not isinstance(value, dict) or value.get("kind") not in {"ENUM", "SCALAR"}:
        raise FFLogsPayloadError("ranking argument type is unsupported")
    name = value.get("name")
    if not isinstance(name, str) or not TOKEN.fullmatch(name):
        raise FFLogsPayloadError("ranking argument type is malformed")
    if value.get("ofType") is not None:
        raise FFLogsPayloadError("ranking argument leaf is malformed")
    return value["kind"], name


def _arguments(data):
    character = data.get("character")
    if not isinstance(character, dict) or character.get("name") != "Character":
        raise FFLogsPayloadError("character schema is unavailable")
    fields = character.get("fields")
    if not isinstance(fields, list) or len(fields) > 128:
        raise FFLogsPayloadError("character schema is outside its bound")
    if any(
        not isinstance(field, dict)
        or not isinstance(field.get("name"), str)
        or not TOKEN.fullmatch(field["name"])
        for field in fields
    ):
        raise FFLogsPayloadError("character field is malformed")
    if len({field["name"] for field in fields}) != len(fields):
        raise FFLogsPayloadError("character field is duplicated")
    matches = [
        field
        for field in fields
        if isinstance(field, dict) and field.get("name") == "zoneRankings"
    ]
    if len(matches) != 1:
        raise FFLogsPayloadError("ranking schema is unavailable")
    arguments = matches[0].get("args")
    if not isinstance(arguments, list) or len(arguments) > 32:
        raise FFLogsPayloadError("ranking arguments are outside their bound")
    indexed, seen = {}, set()
    expected = {
        "zoneID": ("SCALAR", "Int"),
        "difficulty": ("SCALAR", "Int"),
        "partition": ("SCALAR", "Int"),
        "metric": ("ENUM", "CharacterPageRankingMetricType"),
        "specName": ("SCALAR", "String"),
    }
    for argument in arguments:
        if not isinstance(argument, dict):
            raise FFLogsPayloadError("ranking argument is malformed")
        name = argument.get("name")
        if not isinstance(name, str) or not TOKEN.fullmatch(name) or name in seen:
            raise FFLogsPayloadError("ranking argument is malformed")
        seen.add(name)
        default = argument.get("defaultValue")
        # Default values are schema literals, never arbitrary upstream prose.
        if default is not None and (
            not isinstance(default, str)
            or len(default) > 80
            or not re.fullmatch(r"[-_A-Za-z0-9.\" ]{1,80}", default)
        ):
            raise FFLogsPayloadError("ranking argument default is malformed")
        if name not in {*expected, "timeframe"}:
            if (
                isinstance(argument.get("type"), dict)
                and argument["type"].get("kind") == "NON_NULL"
                and default is None
            ):
                raise FFLogsPayloadError("ranking requires an unsupported argument")
            continue
        if (
            name in {"specName", "timeframe"}
            and isinstance(argument.get("type"), dict)
            and argument["type"].get("kind") == "NON_NULL"
            and default is None
        ):
            raise FFLogsPayloadError("optional ranking filter became required")
        indexed[name] = (_leaf(argument.get("type")), default)
    if any(indexed.get(name, (None,))[0] != kind for name, kind in expected.items()):
        raise FFLogsPayloadError("ranking argument contract is unverified")
    timeframe = indexed.get("timeframe")
    if timeframe is None or timeframe[0] != ("ENUM", "RankingTimeframeType"):
        raise FFLogsPayloadError("ranking timeframe is unverified")
    return timeframe[0][1], tuple(
        (name, indexed[name][0][1], indexed[name][1])
        for name in (*expected, "timeframe")
    )


async def fetch_contract(http, source_id, check_active):
    async def request(query, variables):
        check_active()
        response = await http.fetch(
            HttpRequest(
                source_id,
                GRAPHQL_PATH,
                method="POST",
                body=json.dumps(
                    {"query": query, "variables": variables}, separators=(",", ":")
                ).encode(),
                headers={"Content-Type": "application/json"},
            )
        )
        check_active()
        return parse_graphql_envelope(response.body)

    data = await request(SHAPE_QUERY, {})
    enum_name, arguments = _arguments(data)
    enum = data.get("timeframe")
    if (
        not isinstance(enum, dict)
        or enum.get("name") != enum_name
        or enum.get("kind") != "ENUM"
    ):
        raise FFLogsPayloadError("ranking timeframe enum is unverified")
    values = enum.get("enumValues")
    if not isinstance(values, list) or not 1 <= len(values) <= 32:
        raise FFLogsPayloadError("ranking timeframe enum is outside its bound")
    names, descriptions, text_size = [], [], 0
    for value in values:
        name = value.get("name") if isinstance(value, dict) else None
        if not isinstance(name, str) or not TOKEN.fullmatch(name) or name in names:
            raise FFLogsPayloadError("ranking timeframe enum is malformed")
        description = value.get("description")
        if description is not None and (
            not isinstance(description, str)
            or len(description) > MAX_ENUM_DESCRIPTION
            or any(ord(char) < 32 and char not in "\n\r\t" for char in description)
            or re.search(r"bearer\s|access_token|client_secret", description, re.I)
        ):
            raise FFLogsPayloadError("ranking timeframe description is malformed")
        if description is not None and not description.strip():
            description = None
        text_size += len(name) + len(description or "")
        if text_size > MAX_ENUM_TEXT:
            raise FFLogsPayloadError(
                "ranking timeframe descriptions exceed their bound"
            )
        names.append(name)
        descriptions.append(description)
    contract = RankingContract(enum_name, arguments, tuple(names), tuple(descriptions))
    if (
        len(json.dumps(contract.supplement(), ensure_ascii=False).encode("utf-8"))
        > MAX_SCHEMA_HELP_BYTES
    ):
        raise FFLogsPayloadError("ranking timeframe help exceeds its byte bound")
    return contract


def scoped_facts(raw, requested, source_id, *, observation=None):
    """Retain only normal source facts, with exact numbers and nulls intact."""
    rows = raw["rankings"]
    # Filtering is a condition of the whole returned selection, including rows
    # beyond either the fact or display limit. Truncation never grants trust.
    for index, row in enumerate(rows):
        for key in ("bestSpec", "spec"):
            value = row.get(key)
            if value is not None and (
                not isinstance(value, str) or not TOKEN.fullmatch(value)
            ):
                if observation is not None:
                    observation.reject("job_shape", key, index, value=value)
                raise FFLogsPayloadError("ranking job is malformed")
            if (
                requested.get("spec_name") is not None
                and value is not None
                and value != requested["spec_name"]
            ):
                if observation is not None:
                    observation.reject(
                        "job_mismatch", key, index, value=value, matches=False
                    )
                raise FFLogsPayloadError("ranking job differs from requested filter")
    result = []
    for row in rows[:MAX_FACT_ROWS]:
        projected = {
            "encounter_id": row["encounter"]["id"],
            "encounter_name": row["encounter"]["name"],
        }
        for key in ("rankPercent", "bestAmount", "totalKills"):
            value = row.get(key)
            projected[key] = (
                str(value)
                if value is not None
                and isinstance(value, (Decimal, int, float))
                and not isinstance(value, bool)
                else None
            )
        for key in ("bestSpec", "spec"):
            value = row.get(key)
            projected[key] = value
        projected["job_verified"] = (
            any(projected[key] is not None for key in ("bestSpec", "spec"))
            if "spec_name" in requested
            else None
        )
        projected["missing"] = [
            key
            for key in ("rankPercent", "bestAmount", "totalKills", "bestSpec", "spec")
            if key not in row or row[key] is None
        ]
        result.append(projected)
    return {
        "source_id": source_id,
        "requested": requested,
        "selection": {
            key: raw.get(key)
            for key in ("zone", "difficulty", "partition", "size", "metric")
        },
        "rankings": result,
        "total_rows": len(rows),
        "truncated": len(rows) > MAX_FACT_ROWS,
    }
