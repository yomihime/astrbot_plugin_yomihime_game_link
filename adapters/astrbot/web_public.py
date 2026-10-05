"""Narrow public web facts, exact Host proof ownership and pure JSON projection."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from math import isfinite
from time import monotonic, time
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from ...api.display import (
    DisplayDocument,
    FieldsBlock,
    Link,
    LinksBlock,
    MetricsBlock,
    MoneyValue,
    NumberValue,
    Privacy,
    TableBlock,
    TextBlock,
    TimeValue,
)
from ...api.results import CapabilityResult, ErrorCode, ResultStatus
from ...core.ports import PublicWebBinding

QUERY_CAPABILITIES = {
    "items": "item.lookup",
    "character": "ff14.logs.character",
    "calendar": "ff14.calendar.query",
    "market": "ff14.market.query",
}
DEPLOYED_CAPABILITIES = frozenset(
    ("ff14/ff14", value) for value in QUERY_CAPABILITIES.values()
)
REQUEST_LIMIT = 4096  # Processing limit, not a bound on Host's underlying allocation.
RESPONSE_LIMIT = 256 * 1024
PROOF_TTL = 30.0


class WebPublicRejected(ValueError):
    """Only a fixed public code leaves this boundary."""


def normalize_origin(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 2048
        or value != value.strip()
    ):
        raise WebPublicRejected("origin_invalid")
    if any(char.isspace() or ord(char) < 32 for char in value) or any(
        char in value for char in ("*", "\\", "?", "#", "%")
    ):
        raise WebPublicRejected("origin_invalid")
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        hostname = parsed.hostname.encode("idna").decode("ascii").lower()
        if ":" in hostname:
            import ipaddress

            hostname = "[" + str(ipaddress.IPv6Address(hostname)) + "]"
        elif not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", hostname):
            raise ValueError
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
        suffix = (
            ""
            if port is None or (parsed.scheme, port) in (("http", 80), ("https", 443))
            else f":{port}"
        )
        return f"{parsed.scheme}://{hostname}{suffix}"
    except (ValueError, UnicodeError):
        raise WebPublicRejected("origin_invalid") from None


def origin_configuration(config: object) -> tuple[str, str | None]:
    value = config.get("web_public_origin", "") if isinstance(config, Mapping) else ""
    if value == "":
        return "unconfigured", None
    try:
        return "configured", normalize_origin(value)
    except WebPublicRejected:
        return "invalid", None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise WebPublicRejected("request_rejected")
        result[key] = value
    return result


def validated_bearer(
    request: object, origin: str | None, legacy_path: str
) -> tuple[str, int]:
    """Read exp only AFTER the exact legacy Host-authenticated Bearer gate.

    This does not verify JWT or authorize its claims. The public Host legacy
    endpoint has already established username from the ordinary signed JWT.
    """
    if (
        not origin
        or request.path != legacy_path
        or request.method != "POST"
        or type(request.username) is not str
        or not request.username.strip()
    ):
        raise WebPublicRejected("request_rejected")
    headers = request.headers
    authorization = headers.getlist("authorization")
    origins = headers.getlist("origin")
    if len(authorization) != 1 or len(origins) != 1 or headers.getlist("x-api-key"):
        raise WebPublicRejected("request_rejected")
    if normalize_origin(origins[0]) != origin:
        raise WebPublicRejected("origin_mismatch")
    match = re.fullmatch(
        r"Bearer ([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)", authorization[0]
    )
    if match is None or len(match[1]) > 8192:
        raise WebPublicRejected("request_rejected")
    token = match[1]
    try:
        payload = token.split(".")[1]
        claims = json.loads(
            base64.b64decode(
                payload + "=" * (-len(payload) % 4), altchars=b"-_", validate=True
            ).decode("utf-8"),
            object_pairs_hook=_unique_object,
        )
        expiry = claims.get("exp") if type(claims) is dict else None
        if type(expiry) is not int or expiry <= 0 or expiry <= time():
            raise ValueError
    except (ValueError, UnicodeError, TypeError):
        raise WebPublicRejected("session_expired") from None
    return token, expiry


def query_parameters(endpoint: str, body: bytes) -> dict:
    if type(body) is not bytes or len(body) > REQUEST_LIMIT:
        raise WebPublicRejected("request_rejected")
    try:
        values = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        if endpoint == "market":
            # Only the authenticated business handler validates semantics so
            # rejected object queries also invalidate old candidate generations.
            if type(values) is not dict:
                raise ValueError
            return {"input": body.decode("utf-8")}
        expected = {
            "items": {"query"},
            "character": {"region", "server", "character"},
            "calendar": {"region", "days", "timezone"},
        }[endpoint]
        if type(values) is not dict or set(values) != expected:
            raise ValueError
        limits = {"query": 120, "server": 100, "character": 120, "timezone": 128}
        for key, maximum in limits.items():
            if key in values and (
                type(values[key]) is not str
                or not values[key].strip()
                or len(values[key]) > maximum
            ):
                raise ValueError
        if endpoint != "items" and values["region"] not in ("cn", "global"):
            raise ValueError
        if endpoint == "character":
            return {
                "realm": values["region"],
                "server": values["server"],
                "character": values["character"],
                "metric": "rdps",
            }
        if endpoint == "calendar":
            if type(values["days"]) is not int or not 1 <= values["days"] <= 30:
                raise ValueError
            ZoneInfo(values["timezone"])
        return values
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise WebPublicRejected("request_rejected") from None


@dataclass(slots=True)
class _RequestFacts:
    endpoint: str
    generation: int
    key: str
    expiry: int
    deadline: float
    minted: bool = False
    binding: PublicWebBinding | None = None


class HostPublicWebValidator:
    """One Core's exact proof table. No raw Bearer bytes are retained."""

    def __init__(self, generation: int, current, *, clock=monotonic, wall_clock=time):
        self._generation = generation
        self._current = current
        self._clock, self._wall_clock = clock, wall_clock
        self._salt = secrets.token_bytes(32)
        self._core = None
        self._closed = False
        self._requests: dict[object, _RequestFacts] = {}

    def attach(self, core: object) -> None:
        if self._core is not None or self._closed:
            raise WebPublicRejected("entry_unavailable")
        self._core = core

    def _require(self, proof: object) -> _RequestFacts:
        if type(proof) is not object:
            raise WebPublicRejected("request_rejected")
        facts = self._requests.get(proof)
        if (
            self._closed
            or facts is None
            or self._core is None
            or self._current(self._core, self._generation) is not True
            or facts.generation != self._generation
            or self._clock() >= facts.deadline
            or self._wall_clock() >= facts.expiry
        ):
            raise WebPublicRejected("entry_unavailable")
        return facts

    def begin(
        self, endpoint: str, token: str, expiry: int, *, started_at: float | None = None
    ) -> object:
        if endpoint not in QUERY_CAPABILITIES or type(expiry) is not int:
            raise WebPublicRejected("request_rejected")
        now = self._clock()
        deadline = min(
            (now if started_at is None else started_at) + PROOF_TTL,
            now + expiry - self._wall_clock(),
        )
        ticket = object()
        self._requests[ticket] = _RequestFacts(
            endpoint,
            self._generation,
            hmac.new(self._salt, token.encode("ascii"), hashlib.sha256).hexdigest(),
            expiry,
            deadline,
        )
        try:
            self._require(ticket)
        except BaseException:
            self.revoke(ticket)
            raise
        return ticket

    def remaining(self, ticket: object) -> float:
        facts = self._require(ticket)
        return min(facts.deadline - self._clock(), facts.expiry - self._wall_clock())

    def deadline_facts(self, ticket: object) -> tuple[float, int]:
        facts = self._require(ticket)
        return facts.deadline, facts.expiry

    def mint(self, ticket: object) -> object:
        facts = self._require(ticket)
        if facts.minted:
            raise WebPublicRejected("request_rejected")
        facts.minted = True
        return ticket

    def consume(
        self, proof: object, *, module_id: str, capability_id: str, generation: int
    ) -> PublicWebBinding:
        facts = self._require(proof)
        if (
            not facts.minted
            or facts.binding is not None
            or module_id != "ff14/ff14"
            or capability_id != QUERY_CAPABILITIES[facts.endpoint]
        ):
            raise WebPublicRejected("request_rejected")
        # The Core-supplied generation is distinct from the Host generation.
        binding = PublicWebBinding(
            module_id, capability_id, generation, facts.key, facts.deadline
        )
        facts.binding = binding
        return binding

    def is_current(self, proof: object, binding: PublicWebBinding) -> bool:
        try:
            facts = self._require(proof)
            return facts.minted and facts.binding is binding
        except WebPublicRejected:
            return False

    def revoke(self, proof: object) -> None:
        if type(proof) is object:
            self._requests.pop(proof, None)

    def close(self) -> None:
        self._closed = True
        self._requests.clear()


def _value(value: object, depth: int = 0):
    if depth > 8:
        raise WebPublicRejected("result_rejected")
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is Decimal:
        if not value.is_finite():
            raise WebPublicRejected("result_rejected")
        return str(value)
    if type(value) is float and isfinite(value):
        return value
    if type(value) in (NumberValue, MoneyValue):
        if type(value.value) not in (Decimal, int, str):
            raise WebPublicRejected("result_rejected")
        try:
            NumberValue(value.value, precision=value.precision)
        except (TypeError, ValueError):
            raise WebPublicRejected("result_rejected") from None
        data = {"value": str(value.value), "precision": value.precision}
        data["currency" if type(value) is MoneyValue else "unit"] = _value(
            value.currency if type(value) is MoneyValue else value.unit, depth + 1
        )
        return data
    if type(value) is TimeValue and type(value.value) in (date, datetime):
        return {
            "value": value.value.isoformat(),
            "timezone": _value(value.timezone_name, depth + 1),
        }
    if isinstance(value, Mapping) and len(value) <= 256:
        if any(
            type(key) is not str
            or key.lower() in ("path", "resource_id", "asset_id", "html")
            for key in value
        ):
            raise WebPublicRejected("result_rejected")
        return {key: _value(item, depth + 1) for key, item in value.items()}
    if type(value) in (tuple, list) and len(value) <= 512:
        return [_value(item, depth + 1) for item in value]
    raise WebPublicRejected("result_rejected")


def _block(block: object) -> dict:
    if type(block) is TextBlock:
        if type(block.text) is not str:
            raise WebPublicRejected("result_rejected")
        return {"kind": "text", "text": block.text}
    if type(block) in (FieldsBlock, MetricsBlock):
        key = "fields" if type(block) is FieldsBlock else "metrics"
        return {"kind": key, key: _value(getattr(block, key))}
    if type(block) is TableBlock:
        return {
            "kind": "table",
            "columns": _value(block.columns),
            "rows": _value(block.rows),
        }
    if type(block) is LinksBlock and all(type(link) is Link for link in block.links):
        return {
            "kind": "links",
            "links": [
                {"label": _value(link.label), "url": _value(link.url)}
                for link in block.links
            ],
        }
    raise WebPublicRejected("result_rejected")


def _market_facts(facts: Mapping) -> dict:
    """Explicit public market/candidate whitelist, independent of display text."""
    if set(facts) not in ({"market"}, {"market", "selection"}):
        raise WebPublicRejected("result_rejected")
    data = _value(facts)
    market = data["market"]
    if type(market) is not dict or set(market) != {
        "query",
        "scope",
        "quality",
        "intent",
        "module_revision",
        "core_revision",
        "coverage",
        "truncated",
    }:
        raise WebPublicRejected("result_rejected")
    scope = market["scope"]
    if (
        type(scope) is not dict
        or set(scope) != {"kind", "target", "regions", "source"}
        or scope["kind"] not in ("world", "dc", "region")
        or scope["source"] not in ("explicit", "default")
        or type(scope["regions"]) is not list
        or not 1 <= len(scope["regions"]) <= 4
        or any(
            r not in ("China", "North-America", "Europe", "Japan", "Oceania")
            for r in scope["regions"]
        )
        or type(market["query"]) is not str
        or not 1 <= len(market["query"]) <= 120
        or market["quality"] not in ("all", "nq", "hq")
        or market["intent"] not in ("overview", "min", "listings")
        or type(market["truncated"]) is not bool
        or any(
            type(market[k]) is not int or market[k] < 0
            for k in ("module_revision", "core_revision")
        )
        or type(market["coverage"]) is not list
        or len(market["coverage"]) > 4
    ):
        raise WebPublicRejected("result_rejected")
    target = scope["target"]
    if (
        scope["kind"] == "world"
        and (type(target) is not int or not 1 <= target <= 2147483647)
        or scope["kind"] == "dc"
        and (type(target) is not str or not 1 <= len(target) <= 100)
        or scope["kind"] == "region"
        and target is not None
    ):
        raise WebPublicRejected("result_rejected")
    for coverage in market["coverage"]:
        if (
            type(coverage) is not dict
            or set(coverage)
            != {
                "region",
                "target",
                "state",
                "stage",
                "reason",
                "status_code",
                "cached",
                "fetched_at",
            }
            or coverage["region"] not in scope["regions"]
            or coverage["state"] not in ("available", "empty", "failed")
            or type(coverage["target"]) is not str
            or len(coverage["target"]) > 100
            or coverage["stage"]
            not in (None, "parse", "source", "http", "deadline", "cache")
            or coverage["reason"] is not None
            and (
                type(coverage["reason"]) is not str
                or not re.fullmatch(r"[a-z_]{1,64}", coverage["reason"])
            )
            or coverage["fetched_at"] is not None
            and type(coverage["fetched_at"]) is not str
            or coverage["cached"] is not None
            and type(coverage["cached"]) is not bool
            or coverage["status_code"] is not None
            and (
                type(coverage["status_code"]) is not int
                or not 100 <= coverage["status_code"] <= 599
            )
        ):
            raise WebPublicRejected("result_rejected")
    if "selection" in data:
        choice = data["selection"]
        if (
            type(choice) is not dict
            or set(choice)
            != {"kind", "batch_id", "generation", "candidates", "truncated"}
            or choice["kind"] != "item"
            or type(choice["truncated"]) is not bool
            or any(
                type(choice[k]) is not str
                or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", choice[k])
                for k in ("batch_id", "generation")
            )
            or type(choice["candidates"]) is not list
            or len(choice["candidates"]) > 6
        ):
            raise WebPublicRejected("result_rejected")
        for candidate in choice["candidates"]:
            if (
                type(candidate) is not dict
                or set(candidate) != {"item_id", "name"}
                or type(candidate["item_id"]) is not int
                or not 1 <= candidate["item_id"] <= 2147483647
                or type(candidate["name"]) is not str
                or not 1 <= len(candidate["name"]) <= 160
            ):
                raise WebPublicRejected("result_rejected")
    return data


def project_result(result: CapabilityResult) -> dict:
    if (
        type(result) is not CapabilityResult
        or result.privacy is not Privacy.PUBLIC
        or type(result.status) is not ResultStatus
    ):
        raise WebPublicRejected("result_rejected")
    document = result.document
    if document is not None:
        if (
            type(document) is not DisplayDocument
            or document.privacy is not Privacy.PUBLIC
            or len(document.ordered_blocks) > 32
        ):
            raise WebPublicRejected("result_rejected")
        projected = {
            "title": _value(document.title),
            "subject": _value(document.subject),
            "blocks": [_block(block) for block in document.ordered_blocks],
            "sources": _value(document.sources),
            "timestamps": _value(document.timestamps),
        }
    else:
        projected = None
    data = {
        "schema_version": 1,
        "status": result.status.value,
        "privacy": "public",
        "document": projected,
        "provenance": _value(result.provenance),
        "timestamps": _value(result.timestamps),
        "warnings": _value(result.warnings),
        "model_facts": None,
        "error": None,
    }
    if result.model_facts is not None:
        data["model_facts"] = (
            _market_facts(result.model_facts.facts)
            if result.result_id.startswith("ff14-market-")
            else _value(result.model_facts.facts)
        )
    if result.status is ResultStatus.ERROR:
        if result.error is None or type(result.error.code) is not ErrorCode:
            raise WebPublicRejected("result_rejected")
        data["error"] = {
            "code": result.error.code.value,
            "message": "查询未完成，请检查输入或稍后重试。",
        }
        if (
            result.result_id.startswith("ff14-market-")
            and result.error.code is ErrorCode.PARAMETER_ERROR
        ):
            data["error"]["message"] = (
                "查询参数或候选无效；范围有歧义时请使用明确 World ID 或 DC 规范名，候选失效时请重新查询。"
            )
    try:
        encoded = json.dumps(
            {"status": "ok", "message": "", "data": data},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise WebPublicRejected("result_rejected") from None
    if len(encoded) > RESPONSE_LIMIT:
        raise WebPublicRejected("result_rejected")
    return data
