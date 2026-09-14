"""Validated, immutable static declarations for an extension package."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from .version import CONTRACT_VERSION

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*\Z")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?\Z")


class InvocationPolicy(StrEnum):
    COMMAND_ONLY = "command_only"
    NATURAL_LANGUAGE_ALLOWED = "natural_language_allowed"


class CapabilityEffect(StrEnum):
    READ_ONLY = "read_only"
    WRITE = "write"


class PrivacyFloor(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"


class ModuleCategory(StrEnum):
    GAME = "game"
    PLATFORM = "platform"


def _identifier(value: str, field: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase, dotted identifier")


def _text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")


def _version(value: str, field: str) -> None:
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise ValueError(f"{field} must be a semantic version")


def _unique(values: tuple[str, ...], field: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field} contains duplicates")


def _frozen_mapping(value: Mapping[str, object], field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping")
    frozen: dict[str, object] = {}
    for key, item in value.items():
        _field_name(key, f"{field} key")
        frozen[key] = _freeze_value(item, field)
    return MappingProxyType(frozen)


def _freeze_value(value: object, field: str) -> object:
    if isinstance(value, Mapping):
        return _frozen_mapping(value, field)
    if isinstance(value, list):
        return tuple(_freeze_value(item, field) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze_value(item, field) for item in value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{field} contains a non-finite number")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"{field} contains an unsupported value")


def _identifier_tuple(values: tuple[str, ...], field: str) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple")
    for value in values:
        _identifier(value, field)
    _unique(values, field)
    return values


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    capability_id: str
    input_schema: Mapping[str, object]
    invocation_policy: InvocationPolicy
    effect: CapabilityEffect
    output_version: str = CONTRACT_VERSION
    privacy_floor: PrivacyFloor = PrivacyFloor.PUBLIC
    required_config: tuple[str, ...] = ()
    required_sources: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.capability_id, "capability_id")
        if not isinstance(self.invocation_policy, InvocationPolicy):
            raise TypeError("invocation_policy must be an InvocationPolicy")
        if not isinstance(self.effect, CapabilityEffect):
            raise TypeError("effect must be a CapabilityEffect")
        if not isinstance(self.privacy_floor, PrivacyFloor):
            raise TypeError("privacy_floor must be a PrivacyFloor")
        if (
            self.privacy_floor is PrivacyFloor.PRIVATE
            and self.invocation_policy is not InvocationPolicy.COMMAND_ONLY
        ):
            raise ValueError("private capabilities must be command_only")
        if self.output_version != CONTRACT_VERSION:
            raise ValueError(f"output_version must be {CONTRACT_VERSION}")
        input_schema = _freeze_schema(self.input_schema)
        if input_schema["type"] != "object":
            raise ValueError("capability input_schema must be a closed object")
        object.__setattr__(self, "input_schema", input_schema)
        for field in ("required_config", "required_sources", "required_capabilities"):
            _identifier_tuple(getattr(self, field), field)


@dataclass(frozen=True, slots=True)
class CommandDescriptor:
    operation_path: str
    capability_id: str
    parameter_mapping: Mapping[str, str]
    help_text: str

    def __post_init__(self) -> None:
        _operation_path(self.operation_path)
        _identifier(self.capability_id, "capability_id")
        _text(self.help_text, "help_text")
        mapping = _frozen_mapping(self.parameter_mapping, "parameter_mapping")
        if not all(isinstance(value, str) and value for value in mapping.values()):
            raise ValueError("parameter_mapping values must be non-empty strings")
        object.__setattr__(self, "parameter_mapping", mapping)


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    name: str
    capability_id: str
    parameter_mapping: Mapping[str, str]
    description: str

    def __post_init__(self) -> None:
        _identifier(self.name, "name")
        _identifier(self.capability_id, "capability_id")
        _text(self.description, "description")
        mapping = _frozen_mapping(self.parameter_mapping, "parameter_mapping")
        if not all(isinstance(value, str) and value for value in mapping.values()):
            raise ValueError("parameter_mapping values must be non-empty strings")
        object.__setattr__(self, "parameter_mapping", mapping)


@dataclass(frozen=True, slots=True)
class ModuleManifest:
    module_id: str
    route: str
    category: ModuleCategory
    factory_entry: str
    module_version: str
    capabilities: tuple[CapabilityDescriptor, ...]
    commands: tuple[CommandDescriptor, ...] = ()
    tools: tuple[ToolDescriptor, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.module_id, "module_id")
        _route(self.route)
        if not isinstance(self.category, ModuleCategory):
            raise TypeError("category must be a ModuleCategory")
        _entry_point(self.factory_entry)
        _version(self.module_version, "module_version")
        _descriptor_tuple(self.capabilities, CapabilityDescriptor, "capabilities")
        _descriptor_tuple(self.commands, CommandDescriptor, "commands")
        _descriptor_tuple(self.tools, ToolDescriptor, "tools")
        capabilities = {item.capability_id: item for item in self.capabilities}
        _unique(tuple(item.capability_id for item in self.capabilities), "capabilities")
        _unique(tuple(item.operation_path for item in self.commands), "commands")
        _unique(tuple(item.name for item in self.tools), "tools")
        for command in self.commands:
            capability = capabilities.get(command.capability_id)
            if capability is None:
                raise ValueError("command references an undeclared capability")
            _validate_parameter_mapping(command.parameter_mapping, capability)
        for tool in self.tools:
            capability = capabilities.get(tool.capability_id)
            if capability is None:
                raise ValueError("tool references an undeclared capability")
            if capability.privacy_floor is PrivacyFloor.PRIVATE:
                raise ValueError("tool cannot expose a private capability")
            if capability.invocation_policy is InvocationPolicy.COMMAND_ONLY:
                raise ValueError("tool cannot expose a command_only capability")
            if capability.effect is CapabilityEffect.WRITE:
                raise ValueError("tool cannot expose a write capability")
            _validate_parameter_mapping(tool.parameter_mapping, capability)


@dataclass(frozen=True, slots=True)
class PackageManifest:
    package_id: str
    package_version: str
    contract_version: str
    modules: tuple[ModuleManifest, ...]
    author: str
    license: str
    source: str

    def __post_init__(self) -> None:
        _identifier(self.package_id, "package_id")
        _version(self.package_version, "package_version")
        if self.contract_version != CONTRACT_VERSION:
            raise ValueError(f"contract_version must be {CONTRACT_VERSION}")
        _descriptor_tuple(self.modules, ModuleManifest, "modules")
        _text(self.author, "author")
        _text(self.license, "license")
        _text(self.source, "source")
        _unique(tuple(item.module_id for item in self.modules), "modules")
        _unique(tuple(item.route for item in self.modules), "module routes")
        _unique(
            tuple(
                f"{item.route}\x00{command.operation_path}"
                for item in self.modules
                for command in item.commands
            ),
            "commands",
        )
        _unique(
            tuple(tool.name for item in self.modules for tool in item.tools), "tools"
        )

    def global_module_id(self, module_id: str) -> str:
        """Return the package-qualified ID for one declared local module."""
        _identifier(module_id, "module_id")
        if module_id not in {module.module_id for module in self.modules}:
            raise ValueError("module_id is not declared by this package")
        return f"{self.package_id}/{module_id}"


def _descriptor_tuple(
    value: tuple[object, ...], expected: type[object], field: str
) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if not all(isinstance(item, expected) for item in value):
        raise TypeError(f"{field} contains an invalid descriptor")


def _route(value: str) -> None:
    _identifier(value, "route")
    if value == "help":
        raise ValueError("help is a reserved route")


def _operation_path(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "/" in value
    ):
        raise ValueError("operation_path must be an unrooted sequence of tokens")
    parts = value.split(" ")
    if any(
        not part
        or any(character.isspace() or ord(character) < 32 for character in part)
        for part in parts
    ):
        raise ValueError("operation_path must be an unrooted sequence of tokens")
    if "help" in parts:
        raise ValueError("help is a reserved operation path")


def _field_name(value: object, field: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError(f"{field} must be non-empty text")
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise ValueError(f"{field} contains whitespace or control characters")


def _entry_point(value: str) -> None:
    if not isinstance(value, str) or value.count(":") != 1:
        raise ValueError("factory_entry must be a module:attribute entry point")
    module, attribute = value.split(":")
    if (
        not module
        or not attribute
        or not all(part.isidentifier() for part in module.split("."))
    ):
        raise ValueError("factory_entry must be a module:attribute entry point")
    if not attribute.isidentifier():
        raise ValueError("factory_entry must be a module:attribute entry point")


def _validate_parameter_mapping(
    mapping: Mapping[str, str], capability: CapabilityDescriptor
) -> None:
    properties = capability.input_schema["properties"]
    required = capability.input_schema["required"]
    if not isinstance(properties, Mapping) or not isinstance(required, tuple):
        raise ValueError("capability input_schema must be an object schema")
    targets = tuple(mapping.values())
    if len(targets) != len(set(targets)):
        raise ValueError("parameter_mapping cannot map multiple inputs to one field")
    if any(target not in properties for target in targets):
        raise ValueError("parameter_mapping targets an undeclared capability field")
    if not set(required).issubset(targets):
        raise ValueError("parameter_mapping omits a required capability field")


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


def _freeze_schema(schema: Mapping[str, object]) -> Mapping[str, object]:
    """Freeze the deliberately small input-schema vocabulary used by C00."""
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
            frozen_properties[name] = _freeze_schema(child)
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
        frozen["items"] = _freeze_schema(items)
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
