"""Host-selected release assembly; declarations never confer authority."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..api.manifests import InvocationPolicy, SourceDeclaration
from ..api.services import ConfigTarget
from ..extensions.disk_manifest import parse_manifest
from .configuration_migration import OrdinaryMigrationField
from .core_configuration import CORE_CONFIG_FIELDS, core_config_target
from .core_runtime import TrustedSubscriptionGate
from .managed_source_credentials import ManagedSourceCredentialPolicy
from .source_credentials import (
    SourceCredentialPolicy,
    bind_source_credential_declarations,
)


@dataclass(frozen=True, slots=True)
class CapturedAssemblyInventory:
    package_id: str
    manifest: bytes
    files: Mapping[str, bytes]

    def __post_init__(self):
        if type(self.manifest) is not bytes or any(
            type(key) is not str or type(value) is not bytes
            for key, value in self.files.items()
        ):
            raise ValueError("invalid assembly inventory")
        object.__setattr__(self, "files", MappingProxyType(dict(self.files)))


@dataclass(frozen=True, slots=True)
class ReviewedAssembly:
    module_id: str
    manifests: Mapping
    gates: Mapping
    source_hosts: Mapping
    credential_policies: tuple
    managed_credentials: tuple
    credential_realms: Mapping
    validators: Mapping
    migration_fields: tuple
    migration_id: str
    public_bindings: Mapping
    support: object

    @property
    def public_capabilities(self):
        return frozenset(self.public_bindings.values())

    def ordinary_resources(self, principal_id):
        """Reviewed executable policy, separate from migration/declaration metadata."""
        names = frozenset(getattr(self.support, "ordinary_config_fields", ()))
        if not names:
            return {}
        declared = {
            f.name
            for f in self.manifests[self.module_id].config_fields
            if not f.sensitive
        }
        if not names <= declared:
            raise ValueError("reviewed ordinary policy mismatches declaration")
        return {ConfigTarget(principal_id, self.module_id): names}


@dataclass(frozen=True, slots=True)
class LegacyAssemblySupport:
    """No new policies or executable support for pre-descriptor packages."""

    module_id: str

    def ordinary_snapshot(self, _values, _defaults):
        return None



def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate assembly key")
        result[key] = value
    return result


def _keys(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError("invalid assembly fields")


def _string(value):
    if (
        type(value) is not str
        or not value
        or len(value) > 512
        or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)
    ):
        raise ValueError("invalid assembly string")
    return value


def assemble_reviewed(inventory, *, principal_id, support, selected=False):
    """Only an explicit Host choice of reviewed published input grants trust.

    The inventory supplies immutable consistency bytes, not release authority.
    Pure support callbacks are supplied by that same reviewed assembly, never
    named/imported by JSON. They receive only ordinary public configuration.
    """
    if selected is not True or type(inventory) is not CapturedAssemblyInventory:
        raise ValueError("reviewed assembly was not selected")
    package = parse_manifest(inventory.manifest)
    owner = _string(support.module_id)
    matches = [
        m for m in package.modules if package.global_module_id(m.module_id) == owner
    ]
    if package.package_id != inventory.package_id or len(matches) != 1:
        raise ValueError("reviewed assembly owner mismatch")
    module = matches[0]
    manifests = MappingProxyType({owner: module})
    empty = MappingProxyType({})
    raw = inventory.files.get("assembly.json")
    if raw is None:
        # Historical bundles without a reviewed descriptor obtain no new grants.
        return ReviewedAssembly(
            owner,
            manifests,
            empty,
            empty,
            (),
            (),
            empty,
            empty,
            (),
            "legacy-no-assembly",
            empty,
            support,
        )
    if len(raw) > 32768:
        raise ValueError("assembly budget exceeded")
    try:
        descriptor = json.loads(raw, object_pairs_hook=_object)
    except (RecursionError, UnicodeError) as exc:
        raise ValueError("invalid assembly JSON") from exc
    nodes = 0

    def walk(value, depth=0):
        nonlocal nodes
        nodes += 1
        if nodes > 512 or depth > 8:
            raise ValueError("assembly structure budget exceeded")
        if type(value) is str:
            _string(value)
        elif type(value) is dict:
            for key, child in value.items():
                _string(key)
                walk(child, depth + 1)
        elif type(value) is list:
            for child in value:
                walk(child, depth + 1)
        elif value is not None and type(value) not in (bool, int):
            raise ValueError("invalid assembly value")

    walk(descriptor)
    _keys(
        descriptor,
        (
            "version",
            "module_id",
            "sources",
            "credentials",
            "gate",
            "migration",
            "public",
            "page_sources",
            "credential_regions",
        ),
    )
    if (
        type(descriptor["version"]) is not int
        or descriptor["version"] != 1
        or descriptor["module_id"] != owner
    ):
        raise ValueError("assembly version or owner mismatch")
    fields = {f.name: f for f in module.config_fields}
    sources = {s.source_id: s for s in module.sources}
    hosts = descriptor["sources"]
    if type(hosts) is not dict or len(hosts) > 32:
        raise ValueError("invalid assembly sources")
    for source_id, host in hosts.items():
        _string(source_id)
        _string(host)
        SourceDeclaration(source_id, host)
        # A missing/mismatched ordinary source is local capability degradation.
    credentials = descriptor["credentials"]
    if type(credentials) is not list or len(credentials) > 32:
        raise ValueError("invalid assembly credentials")
    policies, managed, aliases, credential_sources, realms = [], [], set(), set(), {}
    for item in credentials:
        _keys(
            item,
            (
                "source",
                "alias",
                "resource_paths",
                "token_host",
                "token_path",
                "scope",
                "group",
                "description",
                "region",
                "label",
            ),
        )
        source_id, alias = _string(item["source"]), _string(item["alias"])
        if (
            source_id not in hosts
            or source_id in credential_sources
            or alias in aliases
        ):
            raise ValueError("duplicate or unknown credential reference")
        aliases.add(alias)
        credential_sources.add(source_id)
        region, label = _string(item["region"]), _string(item["label"])
        if region in realms:
            raise ValueError("duplicate credential region")
        realms[region] = (alias, label)
        source, field = sources.get(source_id), fields.get(alias)
        if source is None and field is None:
            continue  # Legacy bundles with neither half have no OAuth grant.
        if (
            source is None
            or field is None
            or source.host != hosts[source_id]
            or source.credential_ref is not None
            or not field.sensitive
            or field.required
            or field.default is not None
        ):
            raise ValueError("trusted credential declaration is incomplete")
        paths = item["resource_paths"]
        if type(paths) is not list or not 1 <= len(paths) <= 16:
            raise ValueError("invalid assembly resource paths")
        policies.append(
            SourceCredentialPolicy(
                owner,
                source_id,
                alias,
                hosts[source_id],
                tuple(_string(p) for p in paths),
                _string(item["token_host"]),
                _string(item["token_path"]),
                item["scope"],
            )
        )
        policy = ManagedSourceCredentialPolicy(
            owner, alias, _string(item["group"]), _string(item["description"])
        )
        policy.validate_declaration(module)
        managed.append(policy)
    bind_source_credential_declarations(
        owner, module.sources, module.config_fields, policies
    )
    if any(f.sensitive and f.name not in aliases for f in module.config_fields):
        raise ValueError("unreviewed sensitive assembly field")
    gates = {}
    gate = descriptor["gate"]
    _string(gate)
    if gate not in fields:
        raise ValueError("unknown assembly gate")
    if gate in fields:
        field = fields[gate]
        if field.sensitive or field.default is not True:
            raise ValueError("invalid assembly gate")
        gates[owner] = TrustedSubscriptionGate(
            module, gate, hashlib.sha256(inventory.manifest).digest()
        )
    migration = descriptor["migration"]
    _keys(migration, ("id", "fields"))
    migration_id = _string(migration["id"])
    if type(migration["fields"]) is not list or len(migration["fields"]) > 32:
        raise ValueError("invalid migration fields")
    validators = dict(support.validators)
    if any(
        name not in fields or fields[name].sensitive or not callable(check)
        for name, check in validators.items()
    ):
        raise ValueError("invalid reviewed validators")
    migrations, targets, legacy_keys = [], set(), set()
    for item in migration["fields"]:
        _keys(item, ("target", "field", "legacy"))
        name, legacy = _string(item["field"]), _string(item["legacy"])
        if item["target"] == "$core":
            candidates = {f.name: f for f in CORE_CONFIG_FIELDS}
            target, validator = core_config_target(principal_id), None
        elif item["target"] == owner:
            candidates = fields
            target, validator = ConfigTarget(principal_id, owner), validators.get(name)
        else:
            raise ValueError("migration target mismatch")
        field = candidates.get(name)
        if (
            field is None
            or field.sensitive
            or name == gate
            or (target, name) in targets
            or legacy in legacy_keys
        ):
            raise ValueError("migration field mismatch")
        targets.add((target, name))
        legacy_keys.add(legacy)
        migrations.append(OrdinaryMigrationField(target, field, legacy, validator))
    public = descriptor["public"]
    if (
        type(public) is not dict
        or len(public) > 16
        or len(set(public.values())) != len(public)
    ):
        raise ValueError("invalid public assembly bindings")
    capabilities = {c.capability_id: c for c in module.capabilities}
    bindings = {}
    for endpoint, capability in public.items():
        _string(endpoint)
        _string(capability)
        declaration = capabilities.get(capability)
        if (
            declaration is None
            or declaration.invocation_policy
            is not InvocationPolicy.COMMAND_AND_PUBLIC_WEB
        ):
            raise ValueError("public capability reference mismatch")
        bindings[endpoint] = (owner, capability)
    for name, allowed in (("page_sources", hosts), ("credential_regions", realms)):
        values = descriptor[name]
        if (
            type(values) is not list
            or len(values) > 32
            or len(set(values)) != len(values)
        ):
            raise ValueError("invalid page assembly references")
        for value in values:
            _string(value)
            if allowed is not None and value not in allowed:
                raise ValueError("unknown page source")
    # Callback code and its stable semantic validator identities belong to the
    # reviewed module. Freeze declarative metadata supplied to pure projection.
    from ..api.storage import freeze_json

    return ReviewedAssembly(
        owner,
        manifests,
        MappingProxyType(gates),
        MappingProxyType(dict(hosts)),
        tuple(policies),
        tuple(managed),
        MappingProxyType(realms),
        MappingProxyType({owner: MappingProxyType(validators)}),
        tuple(migrations),
        migration_id,
        MappingProxyType(bindings),
        support.with_metadata(
            freeze_json(
                {
                    "page_sources": descriptor["page_sources"],
                    "credential_regions": descriptor["credential_regions"],
                }
            )
        ),
    )
