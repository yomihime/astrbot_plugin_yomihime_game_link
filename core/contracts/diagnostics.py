"""Finite data-only diagnostic schemas and bounded safe payloads."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from threading import RLock
from types import MappingProxyType

_LABEL = re.compile(r"[a-zA-Z][a-zA-Z0-9_.-]{0,95}\Z")
_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}
_SCHEMA_FIELDS = {
    "object": frozenset(
        {"type", "enum", "properties", "required", "additionalProperties"}
    ),
    "array": frozenset({"type", "enum", "items", "maxItems"}),
    "string": frozenset({"type", "enum", "maxLength"}),
    "integer": frozenset({"type", "enum", "minimum", "maximum"}),
    "number": frozenset({"type", "enum", "minimum", "maximum"}),
    "boolean": frozenset({"type", "enum"}),
    "null": frozenset({"type", "enum"}),
}
_ROOT_SCHEMA_FIELDS = {
    kind: allowed | {"$defs"} for kind, allowed in _SCHEMA_FIELDS.items()
}
_SECRET_FIELDS = frozenset(
    "authorization bearer token secret credential credentials apikey clientsecret "
    "accesstoken refreshtoken password ciphertext proof masterkey privatekey "
    "cookie setcookie headers requestheaders responseheaders".split()
)
_AUTH_MATERIAL = re.compile(
    r"(?:\b(?:bearer|basic)\s+\S+|\b(?:authorization|cookie|set-cookie)\s*:|"
    r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----|"
    r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|"
    r"(?:[?&]|\b)(?:api[_-]?key|client[_-]?secret|access[_-]?token|"
    r"refresh[_-]?token|password|proof)\s*[=:])",
    re.I,
)

_FROZEN_SCHEMA_LIMIT = 32
_FROZEN_SCHEMAS = {}
_FROZEN_SCHEMA_LOCK = RLock()


def _detached_immutable_output(value):
    """Qualify only a freshly built output; never use this as input authority.

    Compatibility permits str subclasses in some type/required tuples. Those
    caller-owned leaves can carry mutable comparison behavior and disqualify
    the entire root from identity reuse.
    """
    seen = set()

    def visit(item):
        kind = type(item)
        if (
            kind is str
            or kind is int
            or kind is float
            or kind is bool
            or kind is type(None)
        ):
            return True
        if kind is not MappingProxyType and kind is not tuple:
            return False
        if id(item) in seen:
            return True
        seen.add(id(item))
        if kind is MappingProxyType:
            return all(type(key) is str and visit(child) for key, child in item.items())
        return all(visit(child) for child in item)

    return visit(value)


def _sensitive_field(name, rule):
    compact = re.sub(r"[^a-z0-9]", "", name.lower())

    def sensitive(text):
        return any(text.endswith(root) for root in _SECRET_FIELDS)

    if sensitive(compact):
        return True
    # Credential status is useful, but must be a boolean or reviewed finite
    # state enum, never an unrestricted string carrying the underlying value.
    for suffix in ("configured", "present", "available", "valid", "status", "state"):
        if compact.endswith(suffix) and sensitive(compact[: -len(suffix)]):
            return not (
                set(rule["type"]) <= {"boolean", "null"}
                or rule.get("enum")
                and all(
                    value is None
                    or type(value) is str
                    and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value)
                    for value in rule["enum"]
                )
            )
    return False


def freeze_diagnostic_schema(schema):
    # Hold the exact output root strongly: identity alone must not permit id
    # reuse. Foreign dict/proxy/custom mappings always take the full walk.
    with _FROZEN_SCHEMA_LOCK:
        cached = _FROZEN_SCHEMAS.get(id(schema))
        if cached is not None and cached is schema:
            return schema
    nodes = 0
    definitions = schema.get("$defs", {}) if isinstance(schema, Mapping) else {}
    if not isinstance(definitions, Mapping) or len(definitions) > 128:
        raise ValueError("invalid local diagnostic definitions")
    cache, active = {}, set()

    def walk(value, depth=0):
        nonlocal nodes
        nodes += 1
        if nodes > 2048 or depth > 16 or not isinstance(value, Mapping):
            raise ValueError("diagnostic schema budget exceeded")
        if "$ref" in value:
            ref = value["$ref"]
            if (
                set(value) != {"$ref"}
                or type(ref) is not str
                or not ref.startswith("#/$defs/")
                or ref[8:] not in definitions
                or ref in active
            ):
                raise ValueError("invalid diagnostic local reference")
            if ref in cache:
                return cache[ref]
            active.add(ref)
            try:
                cache[ref] = walk(definitions[ref[8:]], depth)
            finally:
                active.remove(ref)
            return cache[ref]
        kind = value.get("type")
        # Only canonical single builtin strings avoid repeated temporary sets.
        # All caller-controlled subclasses and compound types keep the original
        # checks; every node is still read, validated and detached on each call.
        canonical = type(kind) is str or (
            type(kind) is tuple and len(kind) == 1 and type(kind[0]) is str
        )
        if canonical:
            types = (kind,) if type(kind) is str else kind
            base = types[0]
            if base not in _TYPES:
                raise ValueError("invalid diagnostic schema type")
            allowed = _ROOT_SCHEMA_FIELDS[base] if depth == 0 else _SCHEMA_FIELDS[base]
        else:
            types = (
                (kind,)
                if type(kind) is str
                else tuple(kind)
                if type(kind) in (tuple, list)
                else ()
            )
            if not types or len(set(types)) != len(types) or not set(types) <= _TYPES:
                raise ValueError("invalid diagnostic schema type")
            base = next((item for item in types if item != "null"), "null")
            if len(set(types) - {base, "null"}) > 0:
                raise ValueError("only nullable diagnostic types are allowed")
            allowed = {"type", "enum"}
            if depth == 0:
                allowed.add("$defs")
        result = {"type": types}
        if base == "object":
            if not canonical:
                allowed |= {"properties", "required", "additionalProperties"}
            props = value.get("properties")
            required = value.get("required", ())
            if (
                not isinstance(props, Mapping)
                or len(props) > 128
                or value.get("additionalProperties", False) is not False
                or type(required) not in (tuple, list)
                or len(set(required)) != len(required)
                or not set(required) <= set(props)
            ):
                raise ValueError("diagnostic objects must be closed")
            if any(type(key) is not str or not _LABEL.fullmatch(key) for key in props):
                raise ValueError("invalid diagnostic field")
            children = {key: walk(child, depth + 1) for key, child in props.items()}
            if any(_sensitive_field(key, child) for key, child in children.items()):
                raise ValueError("sensitive diagnostic field rejected")
            result.update(
                properties=MappingProxyType(children),
                required=tuple(required),
                additionalProperties=False,
            )
        elif base == "array":
            if not canonical:
                allowed |= {"items", "maxItems"}
            limit = value.get("maxItems")
            if type(limit) is not int or not 0 <= limit <= 512:
                raise ValueError("diagnostic arrays require a finite limit")
            result.update(items=walk(value.get("items"), depth + 1), maxItems=limit)
        elif base == "string":
            if not canonical:
                allowed |= {"maxLength"}
            limit = value.get("maxLength")
            if type(limit) is not int or not 0 <= limit <= 4096:
                raise ValueError("diagnostic strings require a finite limit")
            result["maxLength"] = limit
        elif base in {"number", "integer"}:
            if not canonical:
                allowed |= {"minimum", "maximum"}
            for key in ("minimum", "maximum"):
                limit = value.get(key)
                if type(limit) not in (int, float) or not math.isfinite(limit):
                    raise ValueError("diagnostic numbers require finite bounds")
                result[key] = limit
            if result["minimum"] > result["maximum"]:
                raise ValueError("invalid diagnostic bounds")
        if set(value) - allowed:
            raise ValueError("unknown diagnostic schema fields")
        if "enum" in value:
            values = value["enum"]
            if (
                type(values) not in (tuple, list)
                or not values
                or len(values) > 128
                or any(
                    type(x) not in (str, int, float, bool, type(None)) for x in values
                )
            ):
                raise ValueError("invalid diagnostic enum")
            result["enum"] = tuple(values)
        return MappingProxyType(result)

    frozen = walk(schema)
    for name, definition in definitions.items():
        if type(name) is not str or not _LABEL.fullmatch(name):
            raise ValueError("invalid local diagnostic definition name")
        walk({"$ref": "#/$defs/" + name})
    if frozen["type"] != ("object",):
        raise ValueError("diagnostic root must be an object")
    # This is the sole insertion point, after full validation/detachment and
    # only for outputs entirely composed of our containers and builtin leaves.
    if _detached_immutable_output(frozen):
        with _FROZEN_SCHEMA_LOCK:
            if len(_FROZEN_SCHEMAS) >= _FROZEN_SCHEMA_LIMIT:
                _FROZEN_SCHEMAS.pop(next(iter(_FROZEN_SCHEMAS)))
            _FROZEN_SCHEMAS[id(frozen)] = frozen
    return frozen


class DiagnosticLimitExceeded(ValueError):
    pass


def diagnostic_payload(schema, payload):
    nodes = 0

    def visit(rule, value, depth=0):
        nonlocal nodes
        nodes += 1
        if nodes > 512 or depth > 8:
            raise DiagnosticLimitExceeded("diagnostic payload budget exceeded")
        types = rule["type"]
        kind = {
            dict: "object",
            list: "array",
            tuple: "array",
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
            type(None): "null",
        }.get(type(value))
        if kind not in types and not (kind == "integer" and "number" in types):
            raise ValueError("diagnostic type mismatch")
        if kind == "string" and len(value) > rule["maxLength"]:
            raise DiagnosticLimitExceeded("diagnostic string limit")
        if "enum" in rule and value not in rule["enum"]:
            raise ValueError("diagnostic enum mismatch")
        if kind == "null":
            return None
        if kind == "object":
            nodes += len(value)
            if nodes > 512:
                raise DiagnosticLimitExceeded("diagnostic node limit")
            props = rule["properties"]
            if not set(rule["required"]) <= set(value) or not set(value) <= set(props):
                raise ValueError("diagnostic object mismatch")
            if any(_sensitive_field(key, props[key]) for key in value):
                raise ValueError("sensitive diagnostic field rejected")
            return {
                key: visit(props[key], child, depth + 1) for key, child in value.items()
            }
        if kind == "array":
            if len(value) > rule["maxItems"]:
                raise DiagnosticLimitExceeded("diagnostic array limit")
            return [visit(rule["items"], child, depth + 1) for child in value]
        if kind == "string":
            if any(ord(c) < 32 for c in value) or _AUTH_MATERIAL.search(value):
                raise ValueError("unsafe diagnostic string")
        if kind in {"number", "integer"}:
            if (
                not math.isfinite(value)
                or not rule["minimum"] <= value <= rule["maximum"]
            ):
                raise ValueError("diagnostic number limit")
        return value

    result = visit(schema, payload)
    encoded = json.dumps(
        result, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")
    if len(encoded) > 65536:
        raise DiagnosticLimitExceeded("diagnostic byte limit")
    return result, len(encoded)
