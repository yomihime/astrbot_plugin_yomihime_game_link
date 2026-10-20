"""Restricted module page operations; trusted Core composition only."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass

from yomihime_game_link_sdk import ConfigTarget, ConfigUpdateMode
from yomihime_game_link_sdk.storage import SecretMetadataState

from ..core.contracts.administration import AdminAuthorizationDenied, AdminOperation
from ..core.contracts.services import ConfigFieldUpdate, ConfigPatch
from ..core.ports import SecretOwner
from .configuration.defaults import MODULE_REPLY_THEME, module_configuration_fields
from .configuration.legacy_copy import effective_configuration
from .configuration.operations import validate_configuration_value


@dataclass(frozen=True, slots=True)
class ModulePageGrant:
    """Host-checked current session/page binding, never caller authority."""

    owner: str
    page: str
    runtime_id: str
    module_epoch: int
    config_fields: frozenset[str]
    credential_fields: frozenset[str]

    def __post_init__(self):
        object.__setattr__(self, "config_fields", frozenset(self.config_fields))
        object.__setattr__(self, "credential_fields", frozenset(self.credential_fields))


_ACTIONS = {
    "module-config-current": set(),
    "module-config-save": {"expected_revision", "values"},
    "module-config-clear": {"expected_revision", "fields"},
    "module-credential-status": set(),
    "module-credential-replace": {"expected_revision", "field", "pair"},
    "module-credential-clear": {"expected_revision", "field", "confirmation"},
}
_IDENTITY = {"owner", "page", "runtime_id", "module_epoch"}


_SCHEMA_KEYS = {
    "type",
    "properties",
    "required",
    "items",
    "enum",
    "minimum",
    "maximum",
    "minLength",
    "maxLength",
    "additionalProperties",
}


def _descriptor_json(value):
    nodes = 0

    def copy(item, depth=0):
        nonlocal nodes
        nodes += 1
        if nodes > 512 or depth > 8:
            raise ValueError("configuration descriptor limit")
        if isinstance(item, Mapping):
            if len(item) > 128 or any(
                type(key) is not str or len(key) > 128 for key in item
            ):
                raise ValueError("invalid descriptor object")
            return {key: copy(child, depth + 1) for key, child in item.items()}
        if type(item) in (tuple, list):
            if len(item) > 128:
                raise ValueError("invalid descriptor array")
            return [copy(child, depth + 1) for child in item]
        if type(item) is str:
            if len(item) > 4096 or any(ord(c) < 32 and c not in "\n\t" for c in item):
                raise ValueError("invalid descriptor text")
            return item
        if item is None or type(item) in (bool, int):
            return item
        if type(item) is float and math.isfinite(item):
            return item
        raise ValueError("invalid descriptor value")

    result = copy(value)
    if (
        len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        > 16384
    ):
        raise ValueError("configuration descriptor byte limit")
    return result


def _public_schema(schema):
    if schema is None:
        return None
    if not isinstance(schema, Mapping) or set(schema) - _SCHEMA_KEYS:
        raise ValueError("unsupported public config schema")
    result = {}
    for key, value in schema.items():
        if key == "properties":
            result[key] = {name: _public_schema(child) for name, child in value.items()}
        elif key == "items":
            result[key] = _public_schema(value)
        else:
            result[key] = _descriptor_json(value)
    return result


def _field_descriptor(owner, declaration, choices_provider):
    if declaration.sensitive:
        raise AdminAuthorizationDenied
    descriptor = {
        "description": declaration.description,
        "value_schema": _public_schema(declaration.value_schema),
        "default": declaration.default,
    }
    if choices_provider is not None:
        choices = choices_provider(owner, declaration)
        if choices is not None:
            if type(choices) not in (list, tuple) or len(choices) > 64:
                raise ValueError("invalid configuration choices")
            if any(
                type(item) is not dict
                or set(item) != {"value", "label"}
                or type(item["label"]) is not str
                or len(item["label"]) > 128
                for item in choices
            ):
                raise ValueError("invalid configuration choice")
            descriptor["choices"] = choices
    return _descriptor_json(descriptor)


async def module_page_operation(
    ops,
    action,
    data,
    *,
    authorization,
    runtime_id,
    check_page_binding,
    descriptor_choices=None,
):
    if (
        action not in _ACTIONS
        or type(data) is not dict
        or set(data) != _IDENTITY | _ACTIONS[action]
    ):
        raise ValueError("invalid module page request")
    owner, page, epoch = data["owner"], data["page"], data["module_epoch"]
    if (
        any(
            type(data[name]) is not str or not data[name] or len(data[name]) > 256
            for name in ("owner", "page", "runtime_id")
        )
        or type(epoch) is not int
        or epoch < 1
        or data["runtime_id"] != runtime_id
        or owner == "game_link/core"
    ):
        raise AdminAuthorizationDenied

    async def current():
        ops._require_accepting()
        result = await check_page_binding(owner, page, epoch, action)
        if type(result) is not ModulePageGrant or (
            result.owner,
            result.page,
            result.runtime_id,
            result.module_epoch,
        ) != (owner, page, runtime_id, epoch):
            raise AdminAuthorizationDenied
        module = ops.registry.snapshot().modules.get(owner)
        if (
            module is None
            or not module.enabled
            or module.epoch != epoch
            or not any(p.route_id == page for p in module.manifest.pages)
        ):
            raise AdminAuthorizationDenied
        declared_page = next(p for p in module.manifest.pages if p.route_id == page)
        if not result.config_fields <= set(
            declared_page.config_fields
        ) or not result.credential_fields <= set(declared_page.credential_fields):
            raise AdminAuthorizationDenied
        fields = {
            field.name: field
            for field in module_configuration_fields(module.manifest.config_fields)
        }
        if any(
            name not in fields or fields[name].sensitive
            for name in result.config_fields
        ) or any(
            name not in fields or not fields[name].sensitive
            for name in result.credential_fields
        ):
            raise AdminAuthorizationDenied
        return result, module, fields

    binding, module, declarations = await current()
    target = ConfigTarget(ops.config_principal_id, owner)
    is_credential = action.startswith("module-credential-")
    permitted = binding.credential_fields if is_credential else binding.config_fields
    policy = (
        ops.credential_resources(active=True)
        if is_credential
        else ops.ordinary_resources()
    )
    if not permitted <= set(policy.get(target, ())):
        raise AdminAuthorizationDenied
    read = action in {"module-config-current", "module-credential-status"}
    if read:
        grant = await ops.authorization.authorize(
            AdminOperation.READ_CONFIG,
            invocation=None,
            context=authorization,
            resources={target: permitted},
        )
        async with ops.lifecycle.admission.mutation("module-page-read"):
            await current()
            await ops.authorization.validate_generation(
                grant, operation=AdminOperation.READ_CONFIG
            )
            snapshot = await ops._config_repository.current(
                target, grant=grant, operation=AdminOperation.READ_CONFIG
            )
            if snapshot.target != target:
                raise AdminAuthorizationDenied
            origins = {}
            if not is_credential:
                policies = tuple(
                    policy
                    for policy in getattr(ops, "_legacy_config_policies", {}).values()
                    if policy.target_owner == owner and policy.target_field in permitted
                )
                snapshot, origins = await effective_configuration(
                    ops._config_repository,
                    snapshot,
                    policies,
                    tuple(declarations[name] for name in permitted),
                )
            metadata = {item.field: item for item in snapshot.secret_metadata}
            fields = {}
            for name in sorted(permitted):
                if is_credential:
                    meta = metadata.get(name)
                    configured = (
                        meta is not None
                        and meta.state is SecretMetadataState.ACTIVE
                        and meta.secret_ref is not None
                    )
                    decryptable = False
                    if configured:
                        ref = meta.secret_ref
                        if (ref.principal_id, ref.module_id, ref.field) != (
                            target.principal_id,
                            owner,
                            name,
                        ):
                            raise AdminAuthorizationDenied
                        try:
                            material = await ops.secret_store.read(
                                ref,
                                owner=SecretOwner(
                                    ref.principal_id,
                                    ref.module_id,
                                    ref.field,
                                    ref.operation_id,
                                ),
                            )
                            decryptable = type(material) is bytes
                        except (OSError, ValueError, RuntimeError):
                            decryptable = False
                        finally:
                            material = None
                    fields[name] = {
                        "configured": configured,
                        "decryptable": decryptable,
                        "upstream": "unverified",
                    }
                else:
                    if name in metadata:
                        raise AdminAuthorizationDenied
                    declaration = declarations[name]
                    present = name in snapshot.values and name not in origins
                    value = snapshot.values.get(name, declaration.default)
                    try:
                        if declaration == MODULE_REPLY_THEME and not present:
                            pass
                        else:
                            validate_configuration_value(
                                declaration,
                                value,
                                ops._module_config_validators.get(owner, {}).get(name),
                            )
                    except (ValueError, TypeError):
                        value, state = None, "invalid"
                    else:
                        state = "valid"
                    fields[name] = {
                        "value": value,
                        "state": state,
                        "present": present,
                        "source": origins.get(name, "sqlite" if present else "default"),
                    }
            for name in origins:
                fields[name]["migration_pending"] = True
            await ops.authorization.validate_generation(
                grant, operation=AdminOperation.READ_CONFIG
            )
            final, _, _ = await current()
            if final != binding:
                raise AdminAuthorizationDenied
            result = {"revision": snapshot.revision, "fields": fields}
            if not is_credential:
                result["descriptors"] = {
                    name: _field_descriptor(
                        owner, declarations[name], descriptor_choices
                    )
                    for name in sorted(permitted)
                }
            return result
    revision = data["expected_revision"]
    if type(revision) is not int or revision < 1:
        raise ValueError("invalid module page revision")
    if is_credential:
        name = data["field"]
        if type(name) is not str or name not in permitted:
            raise AdminAuthorizationDenied
        clear = action == "module-credential-clear"
        if clear and data["confirmation"] != "CLEAR MODULE CREDENTIAL":
            raise ValueError("confirmation required")
        item = {"field": name, "mode": "clear" if clear else "replace"}
        if not clear:
            pair = data["pair"]
            if type(pair) is not dict or set(pair) != {"client_id", "client_secret"}:
                raise ValueError("complete credential pair required")
            item["value"] = pair
        await current()
        receipt = await ops.credential_update(
            {"module_id": owner, "expected_revision": revision, "updates": [item]},
            authorization=authorization,
        )
    else:
        clear = action == "module-config-clear"
        values = data["fields"] if clear else data["values"]
        if clear:
            if type(values) is not list or len(set(values)) != len(values):
                raise ValueError("invalid config clear fields")
            names = set(values)
        else:
            if type(values) is not dict:
                raise ValueError("invalid config values")
            names = set(values)
        if not names or len(names) > 128 or not names <= permitted:
            raise AdminAuthorizationDenied
        patch = ConfigPatch(
            revision,
            tuple(
                ConfigFieldUpdate(
                    name,
                    ConfigUpdateMode.CLEAR
                    if clear or name == MODULE_REPLY_THEME.name and values[name] is None
                    else ConfigUpdateMode.REPLACE,
                    value=None if clear else values[name],
                )
                for name in sorted(names)
            ),
            tuple(declarations.values()),
        )
        await current()
        receipt = await ops.update_config(
            None, owner, patch, authorization=authorization
        )
        receipt = {"revision": receipt.revision}
    final, _, _ = await current()
    if final != binding:
        raise AdminAuthorizationDenied
    return {"revision": receipt["revision"]}
