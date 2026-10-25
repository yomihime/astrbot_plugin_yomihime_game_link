"""Installation permission identity is separate from changing content hashes."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum


def plain(value):
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {
            field.name: plain(getattr(value, field.name)) for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {key: plain(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(child) for child in value]
    return value


def permission_snapshot(manifest):
    """Only approved authority. Version, UI and ordinary config are content."""
    result = {}
    for module in manifest.modules:
        budget_fields = {
            source.request_budget_config
            for source in module.sources
            if source.request_budget_config is not None
        }
        result[module.module_id] = {
            "sources": {item.source_id: plain(item) for item in module.sources},
            "sensitive_fields": {
                item.name: plain(item)
                for item in module.config_fields
                if item.sensitive
            },
            "jobs": {item.job_id: plain(item) for item in module.jobs},
            "budget_fields": {
                item.name: {
                    "default": plain(item.default),
                    "value_schema": plain(item.value_schema),
                    "required": item.required,
                }
                for item in module.config_fields
                if item.name in budget_fields
            },
            "write_capabilities": {
                item.capability_id: plain(item)
                for item in module.capabilities
                if item.effect.value != "read_only"
            },
        }
    return result


def permissions_within(candidate, approved):
    """Existing grants may be reduced; any expansion needs trusted approval."""
    for owner, permissions in candidate.items():
        old = approved.get(owner)
        if old is None:
            if any(permissions.values()):
                return False
            continue
        for kind, values in permissions.items():
            for name, value in values.items():
                if old.get(kind, {}).get(name) != value:
                    return False
    return True


def permission_digest(snapshot):
    return hashlib.sha256(
        json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()
