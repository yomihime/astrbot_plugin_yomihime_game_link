"""Narrow private FF14 evidence forwarding; no request or credential access."""

from __future__ import annotations

import json
import logging
import re
from decimal import Decimal, InvalidOperation

LOGGER_NAME = "yomihime_game_link.ff14.evidence"
MARKER = "FF14_LOGS_EVIDENCE"
MAX_BYTES, MAX_CHUNKS = 512 * 1024, 64
LOW_CHUNKS, SUMMARY_CHUNKS = 47, 8
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


def _text(value, limit=160):
    return (
        value is None
        or isinstance(value, str)
        and len(value) <= limit
        and not any(ord(char) < 32 for char in value)
        and re.search(
            r"https?://|bearer\s|authorization|client_secret|access_token", value, re.I
        )
        is None
    )


def _number(value):
    return (
        value is None
        or type(value) is int
        and abs(value) <= 2**53
        or isinstance(value, str)
        and len(value) <= 160
        and re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?(?:[Ee][+-]?[0-9]+)?", value)
        is not None
    )


def _object(value, required, optional=None):
    return (
        isinstance(value, dict)
        and set(required) <= set(value) <= set(required) | set(optional or {})
        and all((required | (optional or {}))[key](item) for key, item in value.items())
    )


def _list(value, predicate, limit):
    return (
        isinstance(value, list)
        and len(value) <= limit
        and all(predicate(item) for item in value)
    )


def _named(value):
    return _object(value, {"id": _number, "name": _text})


def _type(value, depth=0):
    return (
        value is None
        or depth <= 5
        and _object(
            value,
            {
                "kind": lambda item: _text(item, 32),
                "name": lambda item: _text(item, 128),
            },
            {"ofType": lambda item: _type(item, depth + 1)},
        )
    )


def _winner(value):
    def identifier(item, low=1, nullable=False):
        return (
            nullable and item is None or type(item) is int and low <= item <= 2147483647
        )

    def exact(item):
        if item is None:
            return True
        if (
            not isinstance(item, str)
            or len(item) > 160
            or re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?(?:[Ee][+-]?[0-9]+)?", item)
            is None
        ):
            return False
        try:
            number = Decimal(item)
            return number.is_finite() and 0 <= number <= 100
        except InvalidOperation:
            return False

    return _object(
        value,
        {
            "category": lambda item: item in {"ultimate", "savage"},
            "zone": identifier,
            "difficulty": identifier,
            "requested_partition": lambda item: identifier(item, -1, True),
            "partition": lambda item: identifier(item, -1, True),
            "encounter": identifier,
            "composition": lambda item: item in {"standard", "nonstandard", "unknown"},
            "echo": lambda item: item in {"excluded", "not_echo", "unknown"},
            "rankPercent_exact": exact,
            "bestAmount_model": lambda item: item is None
            or type(item) in {int, float}
            and 0 <= item <= 1000000000,
            "spec": lambda item: _text(item, 80),
            "totalKills": lambda item: item is None
            or type(item) is int
            and 0 <= item <= 2147483647,
            "state": lambda item: item
            in {"record", "no_records", "failed", "not_queried", "excluded_echo"},
            "history_complete": lambda item: type(item) is bool,
        },
    )


def _safe_payload(operation, body):
    """Validate a closed diagnostic wire format without importing game code."""
    if operation == "FF14DutyOrderState":
        return _object(
            body,
            {
                "state": lambda item: item
                in {"complete", "incomplete", "unavailable", "deadline"},
                "complete": lambda item: type(item) is bool,
                "pages": lambda item: type(item) is int and 0 <= item <= 2,
                "rows": lambda item: type(item) is int and 0 <= item <= 200,
                "matched": lambda item: type(item) is int and 0 <= item <= 10000,
                "unmatched": lambda item: type(item) is int and 0 <= item <= 10000,
            },
        )
    if operation == "FF14HistorySummary":
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

        def count(value):
            return type(value) is int and 0 <= value <= 2147483647

        optional = {
            "metadata_sha256": lambda item: isinstance(item, str)
            and re.fullmatch(r"[0-9a-f]{64}", item) is not None,
            "winners": lambda item: _list(item, _winner, 40),
            "displayed": lambda item: type(item) is int and 0 <= item <= 40,
            "total": lambda item: type(item) is int and 0 <= item <= 10000,
            "complete": lambda item: type(item) is bool,
        }
        bundle = set(optional)
        optional.update(
            {
                key: lambda item: type(item) is int and 0 <= item <= 10000
                for key in (
                    "partition_eligible",
                    "partition_echo_excluded",
                    "partition_unknown",
                )
            }
        )
        optional["diagnostic_low_omitted"] = lambda item: type(item) is bool
        return _object(body, dict.fromkeys(fields, count), optional) and (
            not (set(body) & set(optional))
            or bundle <= set(body)
            and body["displayed"] == len(body["winners"]) <= body["total"]
        )
    if operation == "FF14RequestSummary":
        operations = {
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

        def count(value):
            return type(value) is int and 0 <= value <= 2147483647

        def entry(value):
            return _object(
                value,
                {
                    "operation": lambda item: item in operations,
                    **dict.fromkeys(
                        ("attempts", "returned", "failed", "cancelled", "elapsed_ms"),
                        count,
                    ),
                    "cache": lambda item: item
                    in {"module_hit", "module_miss", "unknown"},
                },
            )

        return _object(
            body,
            {
                "kind": lambda item: item == "module_source_http_fetch",
                "operations": lambda item: _list(item, entry, 11),
                "elapsed_ms": count,
                "underlying_http_cache": lambda item: item == "unknown",
                "host_oauth": lambda item: item == "unobserved",
                "network_attempts": lambda item: item == "unknown",
            },
        )
    if operation == "FF14MetadataState":
        return _object(
            body,
            {
                "state": lambda value: value
                in {
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
            },
        )
    if operation == "FF14OutputMetadataShape":

        def token(value):
            return (
                isinstance(value, str)
                and re.fullmatch(r"[_A-Za-z][_0-9A-Za-z]{0,127}", value) is not None
            )

        def argument(value):
            return _object(
                value,
                {
                    "name": token,
                    "type": _type,
                    "defaultValue": lambda item: _text(item, 256),
                },
            )

        def field(value):
            return _object(
                value,
                {
                    "name": token,
                    "type": _type,
                    "args": lambda item: _list(item, argument, 64),
                },
            )

        def definition(value):
            return value is None or _object(
                value,
                {
                    "name": lambda item: _text(item, 128),
                    "fields": lambda item: _list(item, field, 256),
                },
            )

        return _object(
            body,
            dict.fromkeys(
                (
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
                ),
                definition,
            ),
        )
    if operation in {"FF14CharacterMetadata", "FF14OutputMetadata"}:

        def owner(value):
            return _object(value, {"id": _number})

        def encounter(value):
            return _object(value, {"id": _number, "name": _text}, {"zone": owner})

        def difficulty(value):
            return _object(
                value,
                {"id": _number, "name": _text},
                {
                    "sizes": lambda item: item is None
                    or _list(
                        item,
                        lambda size: type(size) is int and 0 <= size <= 2147483647,
                        32,
                    )
                },
            )

        def partition(value):
            return _object(
                value,
                {
                    "id": _number,
                    "name": _text,
                    "default": lambda item: type(item) is bool,
                },
            )

        def zone(value):
            return _object(
                value,
                {
                    "id": _number,
                    "name": _text,
                    "difficulties": lambda item: _list(item, difficulty, 32),
                    "encounters": lambda item: _list(item, encounter, 10000),
                },
                {
                    "frozen": lambda item: item is None or type(item) is bool,
                    "partitions": lambda item: _list(item, partition, 64),
                },
            )

        def expansion(value):
            return _object(
                value,
                {
                    "id": _number,
                    "name": _text,
                    "zones": lambda item: _list(item, owner, 512),
                },
            )

        def world(value):
            return _object(
                value,
                {"zones": lambda item: _list(item, zone, 512)},
                {"expansions": lambda item: _list(item, expansion, 64)},
            )

        return _object(body, {"worldData": world})

    def row(value):
        return _object(
            value,
            {
                "raw_field_names": lambda item: _list(
                    item,
                    lambda name: isinstance(name, str)
                    and re.fullmatch(r"[_A-Za-z][_0-9A-Za-z]{0,127}", name) is not None,
                    64,
                ),
                "raw_field_names_truncated": lambda item: type(item) is bool,
                "encounter": _named,
                "rankPercent": _number,
                "bestAmount": _number,
                "totalKills": _number,
                "bestSpec": lambda item: _text(item, 80),
                "spec": lambda item: _text(item, 80),
            },
        )

    def selection(value):
        return value is None or _object(
            value,
            {
                "zone": _number,
                "difficulty": _number,
                "partition": _number,
                "size": _number,
                "metric": lambda item: _text(item, 32),
                "rankings": lambda item: _list(item, row, 500),
            },
        )

    def character(value):
        return (
            value is None
            or isinstance(value, dict)
            and len(value) <= 13
            and all(
                (key == "rankings" or re.fullmatch(r"z[0-9]{1,2}", key))
                and selection(item)
                for key, item in value.items()
            )
        )

    return _object(
        body, {"characterData": lambda value: _object(value, {"character": character})}
    )


_FIELDS = frozenset(
    {
        "v",
        "correlation",
        "source",
        "operation",
        "time",
        "status",
        "cached",
        "filters",
        "chunk",
        "chunks",
        "truncated",
        "payload",
    }
)


class FFLogsDiagnostics(logging.Handler):
    """At most three explicit command correlations per runtime lifetime."""

    def __init__(self, send=None):
        super().__init__(logging.INFO)
        self.fflogs_evidence_sink = True
        self._send = send
        self._installed = False
        self._pending = {}
        self._correlations = set()
        self._seen = set()
        self._bytes = 0
        self._chunks = 0
        self._limited = False
        self._low_chunks = 0
        self._high_chunks = 0
        self._low_limited = False
        self._summaries = set()
        self._sentinel = False

    def start(self):
        if self._installed:
            return
        logger = logging.getLogger(LOGGER_NAME)
        if any(isinstance(handler, FFLogsDiagnostics) for handler in logger.handlers):
            return
        self._logger = logger
        self._old_level, self._old_propagate = logger.level, logger.propagate
        logger.addHandler(self)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        self._installed = True

    def close(self):
        if self._installed:
            self._logger.removeHandler(self)
            if self._logger.level == logging.INFO:
                self._logger.setLevel(self._old_level)
            if self._logger.propagate is False:
                self._logger.propagate = self._old_propagate
            self._installed = False
        self._pending.clear()
        self._correlations.clear()
        self._seen.clear()
        self._bytes = self._chunks = 0
        self._limited = False
        self._low_chunks = self._high_chunks = 0
        self._low_limited = self._sentinel = False
        self._summaries.clear()
        super().close()

    def emit(self, record):
        if (
            not self._installed
            or self._limited
            or record.name != LOGGER_NAME
            or record.levelno != logging.INFO
            or record.exc_info
            or record.stack_info
        ):
            return
        if (
            record.msg != MARKER + " %s"
            or not isinstance(record.args, tuple)
            or len(record.args) != 1
        ):
            return
        try:
            self._accept(record.args[0])
        except Exception:
            # Never send original malformed records or exception text to Host.
            return

    def _accept(self, raw):
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > 8192:
            return
        event = json.loads(raw)
        if (
            not isinstance(event, dict)
            or set(event) != _FIELDS
            or type(event["v"]) is not int
            or event["v"] != 1
        ):
            return
        correlation, operation = event["correlation"], event["operation"]
        if (
            not isinstance(correlation, str)
            or not re.fullmatch(r"[0-9a-f]{24}", correlation)
            or operation not in OPERATIONS
        ):
            return
        if event["source"] not in {"fflogs_public_cn", "fflogs_public_global"}:
            return
        if (
            type(event["cached"]) is not bool
            or type(event["truncated"]) is not bool
            or operation in {"FF14HistorySummary", "FF14DutyOrderState"}
            and event["status"] is not None
            or not (
                (
                    event["cached"]
                    or operation
                    in {
                        "FF14MetadataState",
                        "FF14RequestSummary",
                        "FF14HistorySummary",
                        "FF14DutyOrderState",
                    }
                )
                and event["status"] is None
                or type(event["status"]) is int
                and 100 <= event["status"] <= 599
            )
        ):
            return
        filters = event["filters"]
        if (
            not isinstance(filters, dict)
            or len(filters) > 40
            or any(
                not isinstance(key, str)
                or not (
                    key in {"metric", "zoneId", "difficulty", "partition"}
                    or re.fullmatch(r"[zdp](?:[0-9]|1[01])", key)
                )
                or not (
                    type(value) is int
                    and -1 <= value <= 2_147_483_647
                    or isinstance(value, str)
                    and key == "metric"
                    and value in {"rdps", "ndps", "cdps"}
                )
                for key, value in filters.items()
            )
        ):
            return
        if not isinstance(event["time"], str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T[0-9:.]+\+00:00", event["time"]
        ):
            return
        index, count, piece = event["chunk"], event["chunks"], event["payload"]
        if (
            type(index) is not int
            or type(count) is not int
            or not 0 <= index < count <= MAX_CHUNKS
            or not isinstance(piece, str)
            or len(piece) > 6000
            or not piece.isascii()
        ):
            return
        if correlation not in self._correlations and len(self._correlations) >= 3:
            return
        # Fixed closed numeric alias filters identify batches; response order
        # and timestamps do not. Replay of the same batch remains suppressed.
        key = (correlation, operation, event["cached"], tuple(sorted(filters.items())))
        if key in self._seen:
            return
        high = operation == "FF14HistorySummary"
        if high and (
            count > SUMMARY_CHUNKS
            or key not in self._pending
            and (
                len(self._summaries) >= 2
                or self._high_chunks + count > 2 * SUMMARY_CHUNKS
            )
        ):
            return
        if self._bytes + len(piece) > MAX_BYTES or self._chunks >= MAX_CHUNKS - int(
            not self._sentinel
        ):
            self._limited = True
            self._pending.clear()
            if (
                not self._sentinel
                and self._chunks < MAX_CHUNKS
                and self._bytes + 2 <= MAX_BYTES
            ):
                self._sentinel = True
                self._chunks += 1
                self._bytes += 2
                self._forward(
                    [
                        {
                            **event,
                            "chunk": 0,
                            "chunks": 1,
                            "truncated": True,
                            "payload": "{}",
                        }
                    ]
                )
            return
        if not high and (
            self._low_limited
            or key not in self._pending
            and self._low_chunks + count > LOW_CHUNKS
        ):
            self._low_limited = True
            self._pending = {
                key: value
                for key, value in self._pending.items()
                if key[1] == "FF14HistorySummary"
            }
            if not self._sentinel:
                self._sentinel = True
                if self._bytes + 2 > MAX_BYTES:
                    self._limited = True
                    return
                self._chunks += 1
                self._bytes += 2
                self._forward(
                    [
                        {
                            **event,
                            "chunk": 0,
                            "chunks": 1,
                            "truncated": True,
                            "payload": "{}",
                        }
                    ]
                )
            return
        header = {
            key: value
            for key, value in event.items()
            if key not in {"chunk", "payload", "time"}
        }
        entry = self._pending.setdefault(key, (header, {}, []))
        if entry[0] != header or index in entry[1]:
            return
        self._correlations.add(correlation)
        self._bytes += len(piece)
        self._chunks += 1
        if high:
            self._high_chunks += 1
            self._summaries.add(key)
        else:
            self._low_chunks += 1
        entry[1][index] = piece
        entry[2].append(event)
        if len(entry[1]) != count:
            return
        body = json.loads("".join(entry[1][i] for i in range(count)))
        self._pending.pop(key, None)
        self._seen.add(key)
        if event["truncated"]:
            if body != {}:
                return
        elif not _safe_payload(operation, body):
            return
        accepted = sorted(entry[2], key=lambda value: value["chunk"])
        if high and self._low_limited and body.get("diagnostic_low_omitted") is False:
            # The module knows only its request budget; this sink also owns a
            # lifetime budget. Change the validated field, never text inside a
            # value. Canonical module false->true shrinks by one ASCII byte.
            body["diagnostic_low_omitted"] = True
            rewritten = json.dumps(
                body, ensure_ascii=True, separators=(",", ":"), allow_nan=False
            )
            if len(rewritten) > sum(len(item["payload"]) for item in accepted):
                # A noncanonical wire must not enlarge the admitted budget.
                return
            offset, rewritten_events = 0, []
            for item in accepted:
                length = len(item["payload"])
                rewritten_events.append(
                    {**item, "payload": rewritten[offset : offset + length]}
                )
                offset += length
            accepted = rewritten_events
            # Original input accounting stays conservative; no extra chunks.
        self._forward(accepted)

    def _forward(self, events):
        if self._send is None:
            from astrbot.api import logger

            def send(message):
                logger.info(message)
        else:
            send = self._send
        for accepted in events:
            send(
                MARKER
                + " "
                + json.dumps(accepted, separators=(",", ":"), ensure_ascii=True)
            )
