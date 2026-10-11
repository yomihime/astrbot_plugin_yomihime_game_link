"""Bounded declaration metadata; field registration never grants value access."""

from __future__ import annotations

import json
from collections.abc import Mapping

from yomihime_game_link_sdk.declarations import ConfigField
from yomihime_game_link_sdk.services import ConfigTarget

from ..core.contracts.validation_boundary import validate_contract

MAX_FIELDS = 128
MAX_BYTES = 262144
MAX_STRING = 4096


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _check_strings(value):
    if isinstance(value, str):
        if len(value) > MAX_STRING:
            raise ValueError("configuration catalog string budget exceeded")
    elif isinstance(value, dict):
        for key, item in value.items():
            _check_strings(key)
            _check_strings(item)
    elif isinstance(value, list):
        for item in value:
            _check_strings(item)


def project_configuration_catalog(
    declarations, resources, *, principal_id, validator_check
):
    fields = []
    for module_id, declared in sorted(declarations.items()):
        target = validate_contract(ConfigTarget(principal_id, module_id))
        for field in sorted(declared, key=lambda item: item.name):
            if not isinstance(field, ConfigField):
                raise ValueError("configuration catalog declaration is invalid")
            if field.sensitive:
                continue
            readable = field.name in resources.get(target, ())
            editable = readable
            blocked_reason = None if readable else "not_granted"
            if readable:
                try:
                    validator_check({target: {field.name}})
                except ValueError:
                    editable = False
                    blocked_reason = "semantic_validator_unavailable"
            fields.append(
                {
                    "module_id": module_id,
                    "name": field.name,
                    "description": field.description,
                    "group": field.group,
                    "value_schema": _plain(field.value_schema),
                    "default": _plain(field.default),
                    "required": field.required,
                    "readable": readable,
                    "editable": editable,
                    "blocked_reason": blocked_reason,
                }
            )
            if len(fields) > MAX_FIELDS:
                raise ValueError("configuration catalog field budget exceeded")
    result = {"schema_version": 1, "fields": fields}
    _check_strings(result)
    payload = json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(payload) > MAX_BYTES:
        raise ValueError("configuration catalog byte budget exceeded")
    return result
