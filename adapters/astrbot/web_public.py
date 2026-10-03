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
        data["model_facts"] = _value(result.model_facts.facts)
    if result.status is ResultStatus.ERROR:
        if result.error is None or type(result.error.code) is not ErrorCode:
            raise WebPublicRejected("result_rejected")
        data["error"] = {
            "code": result.error.code.value,
            "message": "查询未完成，请检查输入或稍后重试。",
        }
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
