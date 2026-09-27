"""The deliberately small, immutable schema vocabulary used by contracts."""

from __future__ import annotations

import math
from collections.abc import Mapping
from types import MappingProxyType

_SCHEMA_TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean"})
_SCHEMA_KEYS = frozenset(
    {
        "type",
        "properties",
        "required",
        "items",
        "enum",
        "description",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "additionalProperties",
    }
)


def _text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")


def _unique(values: tuple[object, ...], field: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field} contains duplicates")


def _freeze_value(value: object, field: str) -> object:
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{field} contains a non-string key")
            frozen[key] = _freeze_value(item, field)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item, field) for item in value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{field} contains a non-finite number")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"{field} contains an unsupported value")


def freeze_input_schema(schema: Mapping[str, object]) -> Mapping[str, object]:
    """Validate and freeze the limited closed-schema vocabulary used by C00."""
    if not isinstance(schema, Mapping):
        raise TypeError("input_schema must be a mapping")
    unknown = set(schema) - _SCHEMA_KEYS
    if unknown:
        raise ValueError("input_schema contains unsupported constraints")
    schema_type = schema.get("type")
    if not isinstance(schema_type, str) or schema_type not in _SCHEMA_TYPES:
        raise ValueError("input_schema requires a supported type")
    frozen: dict[str, object] = {"type": schema_type}
    if "description" in schema:
        _text(schema["description"], "input_schema description")
        frozen["description"] = schema["description"]
    if schema_type == "object":
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            raise TypeError("input_schema properties must be a mapping")
        frozen_properties: dict[str, object] = {}
        for name, child in properties.items():
            _text(name, "input_schema property name")
            if not isinstance(child, Mapping):
                raise TypeError("input_schema property must be a mapping")
            frozen_properties[name] = freeze_input_schema(child)
        frozen["properties"] = MappingProxyType(frozen_properties)
        required = schema.get("required", ())
        if not isinstance(required, (list, tuple)) or not all(
            isinstance(name, str) and name in properties for name in required
        ):
            raise ValueError("input_schema required must name declared properties")
        required_tuple = tuple(required)
        _unique(required_tuple, "input_schema required")
        frozen["required"] = required_tuple
        additional = schema.get("additionalProperties", False)
        if not isinstance(additional, bool):
            raise TypeError("input_schema additionalProperties must be a boolean")
        if additional:
            raise ValueError("input_schema additionalProperties must be false")
        frozen["additionalProperties"] = additional
        if set(schema) - {
            "type",
            "properties",
            "required",
            "description",
            "additionalProperties",
        }:
            raise ValueError("object input_schema contains incompatible constraints")
    elif schema_type == "array":
        items = schema.get("items")
        if not isinstance(items, Mapping):
            raise ValueError("array input_schema requires items")
        frozen["items"] = freeze_input_schema(items)
        if set(schema) - {"type", "items", "description"}:
            raise ValueError("array input_schema contains incompatible constraints")
    else:
        _freeze_scalar_constraints(schema, schema_type, frozen)
    return MappingProxyType(frozen)


def _freeze_scalar_constraints(
    schema: Mapping[str, object], schema_type: str, frozen: dict[str, object]
) -> None:
    allowed = {"type", "description", "enum"}
    if schema_type in {"integer", "number"}:
        allowed |= {"minimum", "maximum"}
    if schema_type == "string":
        allowed |= {"minLength", "maxLength"}
    if set(schema) - allowed:
        raise ValueError("input_schema contains incompatible constraints")
    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, (list, tuple)) or not enum:
            raise ValueError("input_schema enum must be a non-empty sequence")
        frozen_enum = tuple(_freeze_value(value, "input_schema enum") for value in enum)
        if len(frozen_enum) != len(set(frozen_enum)):
            raise ValueError("input_schema enum contains duplicates")
        if not all(_matches_schema_type(value, schema_type) for value in frozen_enum):
            raise ValueError("input_schema enum values must match its type")
        frozen["enum"] = frozen_enum
    if schema_type == "integer":
        _bounded_pair(schema, frozen, "minimum", "maximum", (int,), non_negative=False)
    elif schema_type == "number":
        _bounded_pair(
            schema, frozen, "minimum", "maximum", (int, float), non_negative=False
        )
    elif schema_type == "string":
        _bounded_pair(
            schema, frozen, "minLength", "maxLength", (int,), non_negative=True
        )
    if "enum" in frozen:
        _validate_enum_bounds(frozen, schema_type)


def _matches_schema_type(value: object, schema_type: str) -> bool:
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        )
    return schema_type == "boolean" and isinstance(value, bool)


def _validate_enum_bounds(frozen: Mapping[str, object], schema_type: str) -> None:
    enum = frozen["enum"]
    if not isinstance(enum, tuple):
        return
    if schema_type in {"integer", "number"}:
        minimum = frozen.get("minimum")
        maximum = frozen.get("maximum")
        if minimum is not None and any(value < minimum for value in enum):
            raise ValueError("input_schema enum is below minimum")
        if maximum is not None and any(value > maximum for value in enum):
            raise ValueError("input_schema enum exceeds maximum")
    if schema_type == "string":
        minimum = frozen.get("minLength")
        maximum = frozen.get("maxLength")
        if minimum is not None and any(len(value) < minimum for value in enum):
            raise ValueError("input_schema enum is shorter than minLength")
        if maximum is not None and any(len(value) > maximum for value in enum):
            raise ValueError("input_schema enum exceeds maxLength")


def _bounded_pair(
    schema: Mapping[str, object],
    frozen: dict[str, object],
    lower_name: str,
    upper_name: str,
    expected: tuple[type[object], ...],
    *,
    non_negative: bool,
) -> None:
    for field in (lower_name, upper_name):
        if field not in schema:
            continue
        value = schema[field]
        if isinstance(value, bool) or not isinstance(value, expected):
            raise TypeError(f"input_schema {field} has an invalid value")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"input_schema {field} must be finite")
        if non_negative and value < 0:
            raise ValueError(f"input_schema {field} must be non-negative")
        frozen[field] = value
    if (
        lower_name in frozen
        and upper_name in frozen
        and frozen[lower_name] > frozen[upper_name]
    ):
        raise ValueError("input_schema lower bound exceeds upper bound")


__all__ = ["freeze_input_schema"]
