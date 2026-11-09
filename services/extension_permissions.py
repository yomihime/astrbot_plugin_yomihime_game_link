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


def permission_snapshot(manifest, source_policy=None):
    """Only approved authority; compatibility is validated before this projection.

    Page role/order select UI placement, not page grants; access and field scopes
    remain authority. Capability output_version is a separate compatibility check.
    Signed source_policy is intentionally conservative: its complete normalized
    credential/authorization policy (including descriptive text) is approved.
    """
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
                item.name: {
                    key: value
                    for key, value in plain(item).items()
                    if key != "description"
                }
                for item in module.config_fields
                if item.sensitive
            },
            "jobs": {item.job_id: plain(item) for item in module.jobs},
            "subscriptions": {
                item.type_id: plain(item) for item in module.subscriptions
            },
            "schedules": {item.collector_id: plain(item) for item in module.schedules},
            "collections": {item.name: plain(item) for item in module.collections},
            "pages": {
                item.route_id: {
                    key: value
                    for key, value in plain(item).items()
                    if key not in {"title", "entry", "styles", "order", "role"}
                }
                for item in module.pages
            },
            "commands": {
                item.operation_path: {
                    key: value
                    for key, value in plain(item).items()
                    if key != "help_text"
                }
                for item in module.commands
            },
            "tools": {
                item.name: {
                    key: value
                    for key, value in plain(item).items()
                    if key != "description"
                }
                for item in module.tools
            },
            "budget_fields": {
                item.name: {
                    "default": plain(item.default),
                    "value_schema": plain(item.value_schema),
                    "required": item.required,
                }
                for item in module.config_fields
                if item.name in budget_fields
            },
            "capabilities": {
                item.capability_id: {
                    key: value
                    for key, value in plain(item).items()
                    if key != "output_version"
                }
                for item in module.capabilities
            },
        }
    if source_policy is not None:
        result["$source_policy"] = {"policy": {"normalized": plain(source_policy)}}
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


def permission_delta(candidate, approved):
    """Stable authority differences, independent of display and release metadata."""

    def flatten(snapshot):
        return {
            f"{owner}/{kind}/{name}": value
            for owner, kinds in snapshot.items()
            for kind, entries in kinds.items()
            for name, value in entries.items()
        }

    new, old = flatten(candidate), flatten(approved)
    return {
        "added": sorted(new.keys() - old.keys()),
        "removed": sorted(old.keys() - new.keys()),
        "changed": sorted(
            key for key in new.keys() & old.keys() if new[key] != old[key]
        ),
    }
