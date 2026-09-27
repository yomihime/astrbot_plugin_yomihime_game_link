"""Strict parser for the host-independent B05 extension manifest ABI."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

from yomihime_sdk.api.manifests import (
    EXTENSION_DESCRIPTOR_FIELDS,
    EXTENSION_MANIFEST_ABI,
    EXTENSION_MANIFEST_MAX_BYTES,
    EXTENSION_MANIFEST_MAX_STRING_LENGTH,
    EXTENSION_MANIFEST_SCHEMA_VERSION,
    EXTENSION_PACKAGE_FIELDS,
    CapabilityDescriptor,
    CapabilityEffect,
    CapabilityReference,
    CommandDescriptor,
    ConfigField,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
    PrivacyFloor,
    SourceDeclaration,
    ToolDescriptor,
)
from yomihime_sdk.api.storage import (
    CollectionDescriptor,
    CollectionIndex,
    OwnershipKind,
)
from yomihime_sdk.api.subscriptions import (
    ScheduleDescriptor,
    ScheduleTrigger,
    SubscriptionDescriptor,
)


class ManifestError(ValueError):
    """Stable, non-sensitive manifest validation failure."""


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError("duplicate JSON key")
        result[key] = value
    return result


def _walk_budget(value: object) -> tuple[int, int]:
    nodes = 0
    schemas = 0

    def visit(item: object, depth: int, in_schema: bool = False) -> None:
        nonlocal nodes, schemas
        nodes += 1
        if nodes > EXTENSION_MANIFEST_ABI.max_schema_nodes:
            raise ManifestError("manifest node budget exceeded")
        if depth > EXTENSION_MANIFEST_ABI.max_schema_depth:
            raise ManifestError("manifest nesting budget exceeded")
        if type(item) is str:
            if len(item) > EXTENSION_MANIFEST_MAX_STRING_LENGTH:
                raise ManifestError("manifest string budget exceeded")
            return
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str:
                    raise ManifestError("manifest object keys must be strings")
                if len(key) > EXTENSION_MANIFEST_MAX_STRING_LENGTH:
                    raise ManifestError("manifest string budget exceeded")
                is_schema = in_schema or key in {"input_schema", "filter_schema"}
                if is_schema:
                    schemas += 1
                visit(child, depth + 1, is_schema)
        elif type(item) is list:
            if len(item) > EXTENSION_MANIFEST_ABI.max_array_items:
                raise ManifestError("manifest array budget exceeded")
            for child in item:
                visit(child, depth + 1, in_schema)
        elif type(item) is float and not math.isfinite(item):
            raise ManifestError("manifest numbers must be finite")

    visit(value, 0)
    if schemas > EXTENSION_MANIFEST_ABI.max_schema_nodes:
        raise ManifestError("manifest schema node budget exceeded")
    return nodes, schemas


def _shape(value: object, kind: str, label: str) -> Any:
    if kind == "object" and type(value) is dict:
        return value
    if kind == "array" and type(value) is list:
        return value
    if kind == "string" and type(value) is str:
        return value
    if kind == "boolean" and type(value) is bool:
        return value
    if kind == "integer" and type(value) is int:
        return value
    if kind == "number" and type(value) in (int, float):
        return value
    raise ManifestError(f"invalid {label} type")


def _keys(
    value: object, descriptor: str, required: set[str] = frozenset()
) -> dict[str, Any]:
    obj = _shape(value, "object", descriptor)
    allowed = EXTENSION_DESCRIPTOR_FIELDS[descriptor]
    if set(obj) - allowed:
        raise ManifestError(f"unknown {descriptor} field")
    if required - set(obj):
        raise ManifestError(f"missing {descriptor} field")
    return obj


def _list(obj: Mapping[str, Any], key: str) -> list[Any]:
    if key not in obj:
        return []
    return _shape(obj[key], "array", key)


def _enum(enum_type: type, value: object, field: str):
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        raise ManifestError(f"invalid {field}") from None


def _schema(value: object, field: str) -> Mapping[str, object]:
    obj = _shape(value, "object", field)
    # Let the Core schema constructor enforce its closed keyword vocabulary.
    return obj


def _map_reference(value: object) -> str | CapabilityReference:
    if type(value) is str:
        return value
    obj = _keys(value, "capability_reference", {"module_id", "capability_id"})
    return CapabilityReference(obj["module_id"], obj["capability_id"])


def _map_capability(value: object) -> CapabilityDescriptor:
    obj = _keys(
        value,
        "capability",
        {"capability_id", "input_schema", "invocation_policy", "effect"},
    )
    return CapabilityDescriptor(
        capability_id=obj["capability_id"],
        input_schema=_schema(obj["input_schema"], "input_schema"),
        invocation_policy=_enum(
            InvocationPolicy, obj["invocation_policy"], "invocation_policy"
        ),
        effect=_enum(CapabilityEffect, obj["effect"], "effect"),
        output_version=obj.get("output_version", "1.1.0"),
        privacy_floor=_enum(
            PrivacyFloor, obj.get("privacy_floor", "public"), "privacy_floor"
        ),
        required_config=tuple(_list(obj, "required_config")),
        required_sources=tuple(_list(obj, "required_sources")),
        required_capabilities=tuple(
            _map_reference(item) for item in _list(obj, "required_capabilities")
        ),
    )


def _map_module(value: object) -> ModuleManifest:
    obj = _keys(
        value,
        "module",
        {
            "module_id",
            "route",
            "category",
            "factory_entry",
            "module_version",
            "capabilities",
        },
    )
    commands = []
    for raw in _list(obj, "commands"):
        item = _keys(
            raw,
            "command",
            {"operation_path", "capability_id", "parameter_mapping", "help_text"},
        )
        commands.append(CommandDescriptor(**item))
    tools = []
    for raw in _list(obj, "tools"):
        item = _keys(
            raw, "tool", {"name", "capability_id", "parameter_mapping", "description"}
        )
        tools.append(ToolDescriptor(**item))
    schedules = []
    for raw in _list(obj, "schedules"):
        item = _keys(
            raw,
            "schedule",
            {
                "collector_id",
                "key_version",
                "source_id",
                "data_version",
                "input_schema",
                "shared_scope",
                "trigger",
                "minimum_interval_seconds",
            },
        )
        item["input_schema"] = _schema(item["input_schema"], "schedule input_schema")
        item["shared_scope"] = _enum(
            OwnershipKind, item["shared_scope"], "shared_scope"
        )
        item["trigger"] = _enum(ScheduleTrigger, item["trigger"], "schedule trigger")
        schedules.append(ScheduleDescriptor(**item))
    subscriptions = []
    for raw in _list(obj, "subscriptions"):
        item = _keys(
            raw,
            "subscription",
            {
                "type_id",
                "collector_id",
                "matcher_id",
                "filter_schema",
                "notification_modes",
            },
        )
        item["filter_schema"] = _schema(item["filter_schema"], "filter_schema")
        item["notification_modes"] = tuple(
            _shape(item["notification_modes"], "array", "notification_modes")
        )
        subscriptions.append(SubscriptionDescriptor(**item))
    config_fields = []
    for raw in _list(obj, "config_fields"):
        item = _keys(raw, "config_field", {"name"})
        config_fields.append(ConfigField(**item))
    sources = []
    for raw in _list(obj, "sources"):
        item = _keys(raw, "source", {"source_id", "host"})
        sources.append(SourceDeclaration(credential_ref=None, **item))
    collections = []
    for raw in _list(obj, "collections"):
        item = _keys(raw, "collection", {"name", "schema_version", "owner_kind"})
        indexes = []
        for raw_index in _list(item, "indexes"):
            indexes.append(
                CollectionIndex(
                    **_keys(raw_index, "collection_index", {"name", "field"})
                )
            )
        item["indexes"] = tuple(indexes)
        item["owner_kind"] = _enum(
            OwnershipKind, item["owner_kind"], "collection owner_kind"
        )
        collections.append(CollectionDescriptor(**item))
    for key in ("capabilities",):
        if len(_list(obj, key)) > EXTENSION_MANIFEST_ABI.max_declarations_per_module:
            raise ManifestError("module declaration budget exceeded")
    counts = sum(
        len(_list(obj, key))
        for key in (
            "capabilities",
            "commands",
            "tools",
            "schedules",
            "subscriptions",
            "config_fields",
            "sources",
            "collections",
        )
    )
    if counts > EXTENSION_MANIFEST_ABI.max_declarations_per_module:
        raise ManifestError("module declaration budget exceeded")
    return ModuleManifest(
        module_id=obj["module_id"],
        route=obj["route"],
        category=_enum(ModuleCategory, obj["category"], "module category"),
        factory_entry=obj["factory_entry"],
        module_version=obj["module_version"],
        capabilities=tuple(
            _map_capability(item) for item in _list(obj, "capabilities")
        ),
        commands=tuple(commands),
        tools=tuple(tools),
        schedules=tuple(schedules),
        subscriptions=tuple(subscriptions),
        config_fields=tuple(config_fields),
        sources=tuple(sources),
        collections=tuple(collections),
    )


def parse_manifest(data: bytes | bytearray | memoryview) -> PackageManifest:
    """Parse bounded JSON and map it to Core DTOs without importing extensions."""
    if type(data) not in (bytes, bytearray, memoryview):
        raise ManifestError("manifest input must be bytes")
    try:
        byte_count = memoryview(data).nbytes
    except (TypeError, ValueError):
        raise ManifestError("manifest input must be bytes") from None
    if byte_count > EXTENSION_MANIFEST_MAX_BYTES:
        raise ManifestError("manifest byte budget exceeded")
    payload = bytes(data)
    if len(payload) > EXTENSION_MANIFEST_MAX_BYTES:
        raise ManifestError("manifest byte budget exceeded")
    try:
        text = payload.decode("utf-8", errors="strict")
        raw = json.loads(
            text,
            object_pairs_hook=_object_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ManifestError("invalid JSON number")
            ),
        )
    except ManifestError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise ManifestError("invalid manifest JSON") from None
    _walk_budget(raw)
    root = _shape(raw, "object", "manifest")
    if set(root) != EXTENSION_PACKAGE_FIELDS:
        raise ManifestError("manifest fields do not match schema v1")
    if (
        type(root["schema_version"]) is not int
        or root["schema_version"] != EXTENSION_MANIFEST_SCHEMA_VERSION
    ):
        raise ManifestError("unsupported extension manifest schema version")
    modules_raw = _shape(root["modules"], "array", "modules")
    if len(modules_raw) > EXTENSION_MANIFEST_ABI.max_modules:
        raise ManifestError("package module budget exceeded")
    try:
        return PackageManifest(
            package_id=_shape(root["package_id"], "string", "package_id"),
            package_version=_shape(
                root["package_version"], "string", "package_version"
            ),
            contract_version=_shape(
                root["contract_version"], "string", "contract_version"
            ),
            modules=tuple(_map_module(item) for item in modules_raw),
            author=_shape(root["author"], "string", "author"),
            license=_shape(root["license"], "string", "license"),
            source=_shape(root["source"], "string", "source"),
        )
    except ManifestError:
        raise
    except (TypeError, ValueError, KeyError, RecursionError):
        raise ManifestError("manifest descriptor validation failed") from None
