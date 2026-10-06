"""Fixed metadata projection; declarations grant no value or Web authority."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from ..api.administration import ModuleLifecycle
from ..api.manifests import (
    EXTENSION_MANIFEST_MAX_BYTES,
    EXTENSION_MANIFEST_MAX_STRING_LENGTH,
    EXTENSION_MODULE_MAX_DECLARATIONS,
    EXTENSION_PACKAGE_MAX_MODULES,
    CapabilityEffect,
    InvocationPolicy,
    PrivacyFloor,
)
from ..core.lifecycle import LifecycleState
from ..core.ports import RunIdentity
from ..core.registry import RegistrySnapshot


class ModuleCatalogUnavailable(RuntimeError):
    """The directory could not be read as a consistent metadata snapshot."""


def project_module_catalog(
    snapshot: RegistrySnapshot,
    states: Mapping[str, LifecycleState],
    identities: Mapping[str, RunIdentity | None],
    *,
    runtime_state: str,
) -> dict:
    """Copy only fixed public metadata fields, without performing IO."""
    if runtime_state not in ("ready", "not_ready", "closing", "closed"):
        raise ModuleCatalogUnavailable("module catalog runtime state is unavailable")
    if set(states) != set(snapshot.modules) or set(identities) != set(snapshot.modules):
        raise ModuleCatalogUnavailable("module catalog lifecycle is unavailable")
    if len(snapshot.modules) > EXTENSION_PACKAGE_MAX_MODULES:
        raise ModuleCatalogUnavailable("module catalog exceeds its metadata budget")
    modules = []
    for module_id, module in sorted(snapshot.modules.items()):
        state = states[module_id]
        if (
            state.module_id != module_id
            or state.registry_revision != snapshot.revision
            or state.enabled is not module.enabled
        ):
            raise ModuleCatalogUnavailable("module catalog lifecycle is inconsistent")
        identity = state.identity
        if module.enabled is False:
            availability, reason = "disabled", "module_disabled"
        elif runtime_state != "ready":
            availability, reason = "unavailable", "runtime_not_ready"
        elif state.cleanup_pending:
            availability, reason = "unavailable", "cleanup_pending"
        elif state.lifecycle is not ModuleLifecycle.ACTIVE:
            availability, reason = "unavailable", "module_not_active"
        elif (
            identity is None
            or identity is not identities[module_id]
            or identity.module_id != module_id
            or identity.module_epoch != module.epoch
            or state.epoch != module.epoch
        ):
            availability, reason = "unavailable", "identity_mismatch"
        else:
            availability, reason = "loaded", None
        manifest = module.manifest
        if (
            len(module_id) > 2 * EXTENSION_MANIFEST_MAX_STRING_LENGTH + 1
            or len(manifest.route) > EXTENSION_MANIFEST_MAX_STRING_LENGTH
            or len(manifest.module_version) > EXTENSION_MANIFEST_MAX_STRING_LENGTH
            or len(manifest.capabilities) > EXTENSION_MODULE_MAX_DECLARATIONS
            or len(manifest.config_fields) > EXTENSION_MODULE_MAX_DECLARATIONS
            or any(
                len(item.capability_id) > EXTENSION_MANIFEST_MAX_STRING_LENGTH
                for item in manifest.capabilities
            )
        ):
            raise ModuleCatalogUnavailable("module catalog exceeds its metadata budget")
        modules.append(
            {
                "module_id": module_id,
                "route": manifest.route,
                "category": manifest.category.value,
                "version": manifest.module_version,
                "enabled": module.enabled,
                "lifecycle": state.lifecycle.value,
                "state": availability,
                "reason": reason,
                "module_epoch": state.epoch,
                "runtime_id": identity.runtime_id if availability == "loaded" else None,
                "asset_version": hashlib.sha256(
                    json.dumps(
                        [
                            (item.path, item.sha256)
                            for item in sorted(
                                manifest.resources, key=lambda item: item.path
                            )
                        ],
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
                "pages": [
                    {
                        "route_id": page.route_id,
                        "title": page.title,
                        "order": page.order,
                        "access": page.access,
                        "capability_id": page.capability_id,
                        "entry": f"module-assets/{module_id}/{page.entry}",
                        "styles": [
                            f"module-assets/{module_id}/{path}" for path in page.styles
                        ],
                    }
                    for page in sorted(
                        manifest.pages, key=lambda item: (item.order, item.route_id)
                    )
                    if availability == "loaded"
                ],
                "resources": [
                    {
                        "path": f"module-assets/{module_id}/{item.path}",
                        "sha256": item.sha256,
                    }
                    for item in manifest.resources
                    if availability == "loaded"
                ],
                "capabilities": [
                    {
                        "capability_id": capability.capability_id,
                        "invocation_policy": capability.invocation_policy.value,
                        "web_declared": capability.invocation_policy
                        is InvocationPolicy.COMMAND_AND_PUBLIC_WEB,
                    }
                    for capability in sorted(
                        manifest.capabilities, key=lambda item: item.capability_id
                    )
                    if capability.privacy_floor is PrivacyFloor.PUBLIC
                    and capability.effect is CapabilityEffect.READ_ONLY
                ],
                "config_fields": [
                    {"name": field.name, "required": field.required}
                    for field in sorted(
                        manifest.config_fields, key=lambda item: item.name
                    )
                    if field.sensitive is False
                ],
            }
        )
    result = {
        "schema_version": 1,
        "catalog_revision": snapshot.revision,
        "runtime": {"state": runtime_state},
        "modules": modules,
    }
    if (
        len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
        > EXTENSION_MANIFEST_MAX_BYTES
    ):
        raise ModuleCatalogUnavailable("module catalog exceeds its metadata budget")
    return result
