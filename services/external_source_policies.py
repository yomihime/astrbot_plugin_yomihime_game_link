"""Build-selected external source policy, pinned to exact manifest bytes.

The file belongs to the installed Core release. A module's own declaration,
page, request or package cannot confer credential or authorization authority.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from types import MappingProxyType

from yomihime_game_link_sdk.contexts import InvocationOrigin
from yomihime_game_link_sdk.declarations import ModuleManifest

from ..core.contracts.storage import freeze_json
from ..core.invocation_budget import freeze_execution_timeouts
from ..core.policy import supports_public_read_only
from ..extensions.disk_manifest import parse_manifest
from ..extensions.page_resources import _read_file
from .extension_permissions import permission_snapshot, permissions_within
from .managed_source_credentials import ManagedSourceCredentialPolicy
from .source_authorizations import AuthorizationFlowPolicy
from .source_credentials import CredentialInjection, SourceCredentialPolicy

POLICY_FILE = "external-source-policies.json"


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate source policy field")
        result[key] = value
    return result


class ExternalSourcePolicies:
    def __init__(self, payload=None):
        self.payload = payload
        self.permission_snapshots = {}
        self.config_migrations = {}
        self.credential_policies = ()
        self.managed_credentials = ()
        self.authorizations = MappingProxyType({})
        self.manifests = MappingProxyType({})
        self.capability_timeouts = MappingProxyType({})
        self.timeout_manifests: Mapping[str, ModuleManifest] = MappingProxyType({})
        if payload is None:
            return
        if type(payload) is not bytes or len(payload) > 1048576:
            raise ValueError("external source policy budget exceeded")
        value = json.loads(payload, object_pairs_hook=_object)
        if (
            type(value) is not dict
            or set(value) != {"schema_version", "packages"}
            or type(value["schema_version"]) is not int
            or value["schema_version"] != 1
        ):
            raise ValueError("invalid external source policy")
        if type(value["packages"]) is not list or len(value["packages"]) > 32:
            raise ValueError("invalid external source packages")
        sources, managed, manifests, authorizations, timeouts = [], [], {}, {}, {}
        timeout_manifests: dict[str, ModuleManifest] = {}
        for package in value["packages"]:
            required = {"manifest", "credentials", "authorizations"}
            if (
                type(package) is not dict
                or not required <= set(package)
                or set(package)
                - required
                - {"capability_timeouts", "config_migrations"}
            ):
                raise ValueError("invalid external source package")
            if type(package["manifest"]) is not str:
                raise ValueError("invalid external source manifest")
            raw = package["manifest"].encode("utf-8")
            manifest = parse_manifest(raw)
            self.permission_snapshots[manifest.package_id] = permission_snapshot(
                manifest
            )
            if manifest.package_id in manifests:
                raise ValueError("duplicate external source package")
            manifests[manifest.package_id] = hashlib.sha256(raw).hexdigest()
            modules = {
                manifest.global_module_id(m.module_id): m for m in manifest.modules
            }
            migrations = package.get("config_migrations", [])
            if type(migrations) is not list or len(migrations) > 32:
                raise ValueError("invalid configuration compatibility rules")
            for rule in migrations:
                if (
                    type(rule) is not dict
                    or set(rule)
                    != {"owner", "field", "kind", "from_minimum", "to_minimum"}
                    or rule["kind"] != "integer_floor"
                    or rule["owner"] not in modules
                ):
                    raise ValueError("invalid configuration compatibility rule")
                field = next(
                    (
                        item
                        for item in modules[rule["owner"]].config_fields
                        if item.name == rule["field"]
                    ),
                    None,
                )
                if (
                    field is None
                    or field.sensitive
                    or field.value_schema is None
                    or field.value_schema.get("type") != "integer"
                    or type(rule["from_minimum"]) is not int
                    or type(rule["to_minimum"]) is not int
                    or not rule["from_minimum"] < rule["to_minimum"]
                    or field.value_schema.get("minimum") != rule["to_minimum"]
                ):
                    raise ValueError("configuration compatibility declaration mismatch")
            self.config_migrations[manifest.package_id] = tuple(
                freeze_json(rule) for rule in migrations
            )
            budgets = package.get("capability_timeouts", {})
            if type(budgets) is not dict:
                raise ValueError("invalid external execution budgets")
            declared_budgets = {}
            for owner, declarations in budgets.items():
                if owner not in modules or type(declarations) is not dict:
                    raise ValueError("external execution owner mismatch")
                allowed = {
                    (owner, cap.capability_id)
                    for cap in modules[owner].capabilities
                    if supports_public_read_only(cap)
                    and InvocationOrigin.WEB_PUBLIC in cap.effective_origins
                }
                declared_budgets.update(
                    freeze_execution_timeouts(
                        {
                            (owner, cap): seconds
                            for cap, seconds in declarations.items()
                        },
                        targets=allowed,
                    )
                )
                if declarations:
                    timeout_manifests[owner] = modules[owner]
            timeouts.update(declared_budgets)
            if (
                type(package["credentials"]) is not list
                or len(package["credentials"]) > 32
            ):
                raise ValueError("external credential policy budget exceeded")
            fields = set()
            for item in package["credentials"]:
                if type(item) is not dict or set(item) != {
                    "owner",
                    "source",
                    "field",
                    "group",
                    "description",
                    "optional",
                    "injections",
                }:
                    raise ValueError("invalid external credential policy")
                owner = item["owner"]
                if type(owner) is not str or owner not in modules:
                    raise ValueError("external credential owner mismatch")
                module = modules[owner]
                matches = [
                    source
                    for source in module.sources
                    if source.source_id == item["source"]
                ]
                if len(matches) != 1 or matches[0].credential_ref is not None:
                    raise ValueError("external source declaration mismatch")
                source = matches[0]
                if (
                    type(item["injections"]) is not list
                    or not 1 <= len(item["injections"]) <= 32
                ):
                    raise ValueError("external injection budget exceeded")
                rules = tuple(
                    CredentialInjection(**rule) for rule in item["injections"]
                )
                sources.append(
                    SourceCredentialPolicy(
                        owner,
                        source.source_id,
                        item["field"],
                        source.host,
                        tuple(dict.fromkeys(rule.path for rule in rules)),
                        kind="static",
                        injections=rules,
                        optional=item["optional"],
                    )
                )
                policy = ManagedSourceCredentialPolicy(
                    owner,
                    item["field"],
                    item["group"],
                    item["description"],
                    kind="static",
                )
                policy.validate_declaration(module)
                identity = (owner, item["field"])
                if identity in fields:
                    raise ValueError("duplicate external credential field")
                fields.add(identity)
                managed.append(policy)
            if type(package["authorizations"]) is not dict:
                raise ValueError("invalid external authorization policies")
            for owner, descriptors in package["authorizations"].items():
                if owner not in modules or type(descriptors) is not dict:
                    raise ValueError("external authorization owner mismatch")
                if len(descriptors) > 8:
                    raise ValueError("external authorization budget exceeded")
                for flow_id, row in descriptors.items():
                    if type(row) is not dict or set(row) != {
                        "credential_field",
                        "descriptor",
                    }:
                        raise ValueError("invalid external authorization descriptor")
                    if (owner, row["credential_field"]) not in fields:
                        raise ValueError("authorization field lacks credential policy")
                    flow = AuthorizationFlowPolicy.from_descriptor(row["descriptor"])
                    if flow.flow_id != flow_id:
                        raise ValueError("authorization flow identity mismatch")
                    for step in (flow.apply, flow.poll):
                        declared = next(
                            (
                                source
                                for source in modules[owner].sources
                                if source.source_id == step.source_id
                            ),
                            None,
                        )
                        if declared is None or not declared.authorization_only:
                            raise ValueError(
                                "authorization requires isolated declared sources"
                            )
                authorizations[owner] = freeze_json(descriptors)
            for owner, module in modules.items():
                if any(
                    f.sensitive and (owner, f.name) not in fields
                    for f in module.config_fields
                ):
                    raise ValueError("unreviewed external sensitive field")
        self.credential_policies = tuple(sources)
        self.managed_credentials = tuple(managed)
        self.manifests = MappingProxyType(manifests)
        self.authorizations = MappingProxyType(authorizations)
        self.capability_timeouts = freeze_execution_timeouts(timeouts)
        self.timeout_manifests = MappingProxyType(timeout_manifests)

    @classmethod
    def load(cls, root):
        path = root / POLICY_FILE
        if not path.exists() and not path.is_symlink():
            return cls()
        return cls(_read_file(path, root, 1048576))

    def permits(self, package):
        approved = self.permission_snapshots.get(package.package_id)
        return approved is not None and permissions_within(
            permission_snapshot(package.manifest), approved
        )
