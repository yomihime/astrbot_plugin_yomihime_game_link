"""Validate capability parameters without coercion or authority decisions."""

from collections.abc import Mapping
from math import isfinite

from .manifests import CapabilityDescriptor
from .storage import JsonObject, freeze_json
from .storage import JsonValue as JsonValue


class ParameterError(ValueError):
    """Input does not match a registered capability's closed schema."""


def validate_parameters(
    capability: CapabilityDescriptor, parameters: object
) -> JsonObject:
    """Return a detached immutable object; never include input values in errors.

    The descriptor has already validated the limited schema vocabulary. This
    function validates data only. The caller still checks trusted origin,
    capability availability, owner scope and current revisions separately.
    """
    if not isinstance(capability, CapabilityDescriptor):
        raise TypeError("capability must be a CapabilityDescriptor")
    _validate(capability.input_schema, parameters, "parameters", 0)
    result = freeze_json(parameters)
    assert isinstance(result, Mapping)
    return result


def _validate(
    schema: Mapping[str, object], value: object, path: str, depth: int
) -> None:
    if depth > 64:
        raise ParameterError("parameters exceed supported nesting depth")
    kind = schema["type"]
    if kind == "object":
        if not isinstance(value, Mapping) or any(not isinstance(k, str) for k in value):
            raise ParameterError(f"{path}: expected object with string keys")
        properties = schema["properties"]
        if set(value) - set(properties) or set(schema["required"]) - set(value):
            raise ParameterError(f"{path}: unexpected or missing fields")
        for name, child in properties.items():
            if name in value:
                _validate(child, value[name], f"{path}.{name}", depth + 1)
        return
    if kind == "array":
        if not isinstance(value, (list, tuple)):
            raise ParameterError(f"{path}: expected array")
        for item in value:
            _validate(schema["items"], item, f"{path}[]", depth + 1)
        return
    valid = (
        (kind == "string" and isinstance(value, str))
        or (kind == "boolean" and isinstance(value, bool))
        or (
            kind == "integer" and isinstance(value, int) and not isinstance(value, bool)
        )
        or (
            kind == "number"
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        )
    )
    if not valid or (isinstance(value, float) and not isfinite(value)):
        raise ParameterError(f"{path}: expected finite {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ParameterError(f"{path}: value outside allowed choices")
    for field, compare in (
        ("minimum", lambda x, y: x < y),
        ("maximum", lambda x, y: x > y),
    ):
        if field in schema and compare(value, schema[field]):
            raise ParameterError(f"{path}: value outside allowed range")
    for field, compare in (
        ("minLength", lambda x, y: x < y),
        ("maxLength", lambda x, y: x > y),
    ):
        if field in schema and compare(len(value), schema[field]):
            raise ParameterError(f"{path}: text outside allowed length")
