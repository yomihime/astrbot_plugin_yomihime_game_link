"""Validated, immutable static declarations for an extension package."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from .schema import freeze_input_schema
from .storage import CollectionDescriptor, JsonValue
from .subscriptions import ScheduleDescriptor, SubscriptionDescriptor
from .version import COMPATIBLE_CONTRACT_VERSIONS, CONTRACT_VERSION

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*\Z")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?\Z")

# Host-independent disk manifest ABI. Parsing and filesystem containment belong
# to the extension loader; these constants bound that parser's accepted input.
EXTENSION_MANIFEST_FILENAME = "yomihime.manifest.json"
EXTENSION_MANIFEST_SCHEMA_VERSION = 1
EXTENSION_MANIFEST_MAX_BYTES = 262_144
EXTENSION_PACKAGE_MAX_MODULES = 64
EXTENSION_MODULE_MAX_DECLARATIONS = 128
EXTENSION_SCHEMA_MAX_DEPTH = 16
EXTENSION_SCHEMA_MAX_NODES = 2_048
EXTENSION_MANIFEST_MAX_STRING_LENGTH = 4_096
EXTENSION_MANIFEST_MAX_ARRAY_ITEMS = 256
EXTENSION_MANIFEST_ROOT_POLICY = "host_supplied_read_only_root"
EXTENSION_FACTORY_ABI_VERSION = 1

EXTENSION_PACKAGE_FIELDS = frozenset(
    {
        "schema_version",
        "package_id",
        "package_version",
        "contract_version",
        "modules",
        "author",
        "license",
        "source",
    }
)
EXTENSION_DESCRIPTOR_FIELDS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "module": frozenset(
            {
                "module_id",
                "route",
                "category",
                "factory_entry",
                "module_version",
                "capabilities",
                "commands",
                "tools",
                "schedules",
                "subscriptions",
                "config_fields",
                "sources",
                "collections",
            }
        ),
        "capability": frozenset(
            {
                "capability_id",
                "input_schema",
                "invocation_policy",
                "effect",
                "output_version",
                "privacy_floor",
                "required_config",
                "required_sources",
                "required_capabilities",
            }
        ),
        "capability_reference": frozenset({"module_id", "capability_id"}),
        "command": frozenset(
            {"operation_path", "capability_id", "parameter_mapping", "help_text"}
        ),
        "tool": frozenset(
            {"name", "capability_id", "parameter_mapping", "description"}
        ),
        "schedule": frozenset(
            {
                "collector_id",
                "key_version",
                "source_id",
                "data_version",
                "input_schema",
                "shared_scope",
                "trigger",
                "minimum_interval_seconds",
                "default_interval_seconds",
                "interval_config_key",
            }
        ),
        "subscription": frozenset(
            {
                "type_id",
                "collector_id",
                "matcher_id",
                "filter_schema",
                "notification_modes",
            }
        ),
        "config_field": frozenset(
            {"name", "sensitive", "required", "default", "description"}
        ),
        "source": frozenset(
            {
                "source_id",
                "host",
                "timeout_seconds",
                "requests_per_minute",
            }
        ),
        "collection": frozenset(
            {"name", "schema_version", "owner_kind", "indexes", "retention_category"}
        ),
        "collection_index": frozenset({"name", "field"}),
    }
)


def is_compatible_contract_version(value: str) -> bool:
    """Return whether a package/descriptor can be read by this runtime."""
    return isinstance(value, str) and value in COMPATIBLE_CONTRACT_VERSIONS


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


def _global_module_identifier(value: str, field: str = "module_id") -> None:
    if type(value) is not str or value.count("/") != 1:
        raise ValueError(f"{field} must be a package/module identifier")
    package_id, module_id = value.split("/")
    _identifier(package_id, "package_id")
    _identifier(module_id, "module_id")


@dataclass(frozen=True, slots=True)
class CapabilityReference:
    """A module-qualified capability dependency target."""

    module_id: str
    capability_id: str

    def __post_init__(self) -> None:
        _global_module_identifier(self.module_id)
        if type(self.capability_id) is not str:
            raise ValueError("capability_id must be a built-in string")
        _identifier(self.capability_id, "capability_id")

    @classmethod
    def validate(cls, value: object) -> "CapabilityReference":
        if not isinstance(value, cls):
            raise TypeError("capability reference must be a CapabilityReference")
        try:
            return cls(value.module_id, value.capability_id)
        except (AttributeError, TypeError, ValueError):
            raise ValueError("capability reference invariants are invalid") from None


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


def _capability_dependency_tuple(
    values: tuple[str | CapabilityReference, ...], field: str
) -> tuple[str | CapabilityReference, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple")
    checked: list[str | CapabilityReference] = []
    keys: set[tuple[str | None, str]] = set()
    for value in values:
        if isinstance(value, CapabilityReference):
            item: str | CapabilityReference = CapabilityReference.validate(value)
            key = (item.module_id, item.capability_id)
        elif type(value) is str:
            _identifier(value, field)
            item = value
            key = (None, value)
        else:
            raise TypeError(f"{field} requires capability identifiers or references")
        if key in keys:
            raise ValueError(f"{field} contains duplicates")
        keys.add(key)
        checked.append(item)
    return tuple(checked)


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    """A capability declaration and its dependency requirements.

    Legacy string requirements remain module-local.  A ``CapabilityReference``
    is required for a module-qualified dependency target.
    """

    capability_id: str
    input_schema: Mapping[str, object]
    invocation_policy: InvocationPolicy
    effect: CapabilityEffect
    output_version: str = CONTRACT_VERSION
    privacy_floor: PrivacyFloor = PrivacyFloor.PUBLIC
    required_config: tuple[str, ...] = ()
    required_sources: tuple[str, ...] = ()
    required_capabilities: tuple[str | CapabilityReference, ...] = ()

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
        if self.output_version not in COMPATIBLE_CONTRACT_VERSIONS:
            raise ValueError("output_version is not compatible with this runtime")
        input_schema = freeze_input_schema(self.input_schema)
        if input_schema["type"] != "object":
            raise ValueError("capability input_schema must be a closed object")
        object.__setattr__(self, "input_schema", input_schema)
        for field in ("required_config", "required_sources"):
            _identifier_tuple(getattr(self, field), field)
        object.__setattr__(
            self,
            "required_capabilities",
            _capability_dependency_tuple(
                self.required_capabilities, "required_capabilities"
            ),
        )


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
    schedules: tuple[ScheduleDescriptor, ...] = ()
    subscriptions: tuple[SubscriptionDescriptor, ...] = ()
    config_fields: tuple["ConfigField", ...] = ()
    sources: tuple["SourceDeclaration", ...] = ()
    collections: tuple[CollectionDescriptor, ...] = ()

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
        _descriptor_tuple(self.schedules, ScheduleDescriptor, "schedules")
        _descriptor_tuple(self.subscriptions, SubscriptionDescriptor, "subscriptions")
        _descriptor_tuple(self.config_fields, ConfigField, "config_fields")
        _descriptor_tuple(self.sources, SourceDeclaration, "sources")
        _descriptor_tuple(self.collections, CollectionDescriptor, "collections")
        capabilities = {item.capability_id: item for item in self.capabilities}
        _unique(tuple(item.capability_id for item in self.capabilities), "capabilities")
        _unique(tuple(item.operation_path for item in self.commands), "commands")
        _unique(tuple(item.name for item in self.tools), "tools")
        _unique(
            tuple(item.collector_id for item in self.schedules), "schedule collectors"
        )
        _unique(tuple(item.type_id for item in self.subscriptions), "subscriptions")
        _unique(tuple(item.name for item in self.config_fields), "config fields")
        _unique(tuple(item.source_id for item in self.sources), "sources")
        _unique(tuple(item.name for item in self.collections), "collections")
        declared_collectors = {item.collector_id for item in self.schedules}
        if any(
            item.collector_id not in declared_collectors for item in self.subscriptions
        ):
            raise ValueError("subscription references an undeclared collector")
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
class ExtensionManifestABI:
    """Versioned declaration for the strict, host-independent disk envelope.

    ``PackageManifest`` remains the validated Core model. A loader must reject
    unknown keys, duplicate JSON keys, oversized/deep documents, and invalid
    descriptor shapes before mapping to that model or importing a factory.
    """

    schema_version: int = EXTENSION_MANIFEST_SCHEMA_VERSION
    filename: str = EXTENSION_MANIFEST_FILENAME
    max_bytes: int = EXTENSION_MANIFEST_MAX_BYTES
    max_modules: int = EXTENSION_PACKAGE_MAX_MODULES
    max_declarations_per_module: int = EXTENSION_MODULE_MAX_DECLARATIONS
    max_schema_depth: int = EXTENSION_SCHEMA_MAX_DEPTH
    max_schema_nodes: int = EXTENSION_SCHEMA_MAX_NODES
    max_string_length: int = EXTENSION_MANIFEST_MAX_STRING_LENGTH
    max_array_items: int = EXTENSION_MANIFEST_MAX_ARRAY_ITEMS
    root_policy: str = EXTENSION_MANIFEST_ROOT_POLICY
    factory_abi_version: int = EXTENSION_FACTORY_ABI_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != EXTENSION_MANIFEST_SCHEMA_VERSION
        ):
            raise ValueError("unsupported extension manifest schema version")
        if self.filename != EXTENSION_MANIFEST_FILENAME:
            raise ValueError("extension manifest filename is fixed")
        if self.root_policy != EXTENSION_MANIFEST_ROOT_POLICY:
            raise ValueError("extension package root must be host supplied")
        expected_limits = {
            "max_bytes": EXTENSION_MANIFEST_MAX_BYTES,
            "max_modules": EXTENSION_PACKAGE_MAX_MODULES,
            "max_declarations_per_module": EXTENSION_MODULE_MAX_DECLARATIONS,
            "max_schema_depth": EXTENSION_SCHEMA_MAX_DEPTH,
            "max_schema_nodes": EXTENSION_SCHEMA_MAX_NODES,
            "max_string_length": EXTENSION_MANIFEST_MAX_STRING_LENGTH,
            "max_array_items": EXTENSION_MANIFEST_MAX_ARRAY_ITEMS,
            "factory_abi_version": EXTENSION_FACTORY_ABI_VERSION,
        }
        for name, expected in expected_limits.items():
            value = getattr(self, name)
            if type(value) is not int or value != expected:
                raise ValueError(f"{name} is fixed by the extension manifest ABI")


EXTENSION_MANIFEST_ABI = ExtensionManifestABI()


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
        if self.contract_version not in COMPATIBLE_CONTRACT_VERSIONS:
            raise ValueError("contract_version is not compatible with this runtime")
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


class ConfigUpdateMode(StrEnum):
    KEEP = "keep"
    REPLACE = "replace"
    CLEAR = "clear"


def _safe_token(value: str, field: str) -> None:
    _text(value, field)
    if any(character.isspace() or character in "/\\?#" for character in value):
        raise ValueError(f"{field} must be a bounded token")


@dataclass(frozen=True, slots=True)
class ConfigField:
    """A declared configuration field; secret values are kept in SecretStore."""

    name: str
    sensitive: bool = False
    required: bool = False
    default: JsonValue | None = None
    description: str = ""

    def __post_init__(self) -> None:
        _field_name(self.name, "config field name")
        if not isinstance(self.sensitive, bool) or not isinstance(self.required, bool):
            raise TypeError("config sensitivity and required markers must be bool")
        if self.sensitive and self.default is not None:
            raise ValueError("sensitive config fields cannot contain a default value")
        if self.default is not None:
            from .storage import freeze_json

            object.__setattr__(self, "default", freeze_json(self.default))
        if self.description:
            _text(self.description, "config field description")


@dataclass(frozen=True, slots=True)
class SourceDeclaration:
    """A bounded source target; credentials and paths are never supplied by modules."""

    source_id: str
    host: str
    credential_ref: str | None = None
    timeout_seconds: float = 10.0
    requests_per_minute: int = 60

    def __post_init__(self) -> None:
        _identifier(self.source_id, "source_id")
        host = (
            unicodedata.normalize("NFKC", self.host)
            if isinstance(self.host, str)
            else ""
        )
        _safe_token(host, "source host")
        if (
            "://" in host
            or ":" in host
            or "@" in host
            or any(
                ord(character) < 32 or 0x7F <= ord(character) <= 0x9F
                for character in host
            )
        ):
            raise ValueError(
                "source host must be a host token, not a URL or credential"
            )
        object.__setattr__(self, "host", host)
        if self.credential_ref is not None:
            _safe_token(self.credential_ref, "credential_ref")
            if not self.credential_ref.startswith("credential_"):
                raise ValueError(
                    "credential_ref must be issued by the credential store"
                )
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("source timeout must be a finite positive number")
        if (
            isinstance(self.requests_per_minute, bool)
            or not isinstance(self.requests_per_minute, int)
            or self.requests_per_minute < 1
        ):
            raise ValueError("source quota must be a positive integer")


# Explicit aliases keep terminology used by the architecture documents importable.
ConfigFieldDescriptor = ConfigField
CollectionDeclaration = CollectionDescriptor
SourceDescriptor = SourceDeclaration
SourceSpec = SourceDeclaration
