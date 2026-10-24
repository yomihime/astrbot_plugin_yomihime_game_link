"""Trusted Host installation approval; never exposed as a module service."""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from time import time

from yomihime_game_link_sdk.errors import RevisionConflict
from yomihime_game_link_sdk.host import ExtensionHostError

from ..extensions.disk_manifest import parse_manifest
from ..extensions.page_resources import _check_path, _read_file, read_page_resources
from .extension_permissions import permission_digest, permission_snapshot
from .external_source_policies import ExternalSourcePolicies


def _encode(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


class ExternalInstallationTrust:
    """Approvals bind actual Host plugin, package root and sensitive snapshot.

    Build-selected legacy policies may seed an equivalent first installation.
    A package file is only a proposed policy, never its own approval.
    """

    def __init__(
        self, database, context, build_policies=None, *, config_principal_id=None
    ):
        self.database, self.context = database, context
        self.build_policies = build_policies or ExternalSourcePolicies()
        self.policies = self.build_policies
        self.config_principal_id = config_principal_id

    async def load(self):
        def read(unit):
            return [
                dict(row)
                for row in unit.execute(
                    "SELECT * FROM extension_installation_grants ORDER BY package_id"
                ).fetchall()
            ]

        rows = await self.database.executor.run_transaction(read)
        packages = {}
        if self.build_policies.payload is not None:
            packages.update(
                (parse_manifest(item["manifest"].encode()).package_id, item)
                for item in json.loads(self.build_policies.payload)["packages"]
            )
        for row in rows:
            packages[row["package_id"]] = json.loads(row["approved_policy"])
        if len(packages) > 32:
            raise ValueError("external installation budget exceeded")
        self.policies = ExternalSourcePolicies(
            _encode({"schema_version": 1, "packages": list(packages.values())}).encode()
        )
        return self.policies

    def _candidate(self, plugin_name, package_id):
        import re

        if (
            type(plugin_name) is not str
            or type(package_id) is not str
            or not re.fullmatch(r"[a-z][a-z0-9_.-]*", package_id)
        ):
            raise ValueError("invalid installation identity")
        getter = getattr(self.context, "get_registered_star", None)
        metadata = getter(plugin_name) if callable(getter) else None
        module_file = getattr(getattr(metadata, "module", None), "__file__", None)
        if (
            metadata is None
            or metadata.name != plugin_name
            or metadata.activated is not True
            or type(module_file) is not str
        ):
            raise ExtensionHostError("plugin_instance_unavailable")
        installation = Path(module_file).absolute().parent
        root = installation / "packages" / package_id
        _check_path(root, installation)
        if not root.is_dir():
            raise ExtensionHostError("package_root_rejected")
        raw = _read_file(root / "yomihime.manifest.json", root, 1048576)
        manifest = parse_manifest(raw)
        if manifest.package_id != package_id:
            raise ExtensionHostError("package_identity_mismatch")
        read_page_resources(root, manifest)
        path = root / "yomihime.source-policy.json"
        if path.exists() or path.is_symlink():
            proposed = json.loads(_read_file(path, root, 1048576))
            if (
                type(proposed) is not dict
                or set(proposed)
                - {
                    "schema_version",
                    "credentials",
                    "authorizations",
                    "capability_timeouts",
                    "config_migrations",
                }
                or proposed.get("schema_version") != 1
            ):
                raise ValueError("invalid proposed source policy")
        else:
            proposed = {"credentials": [], "authorizations": {}}
        item = {
            "manifest": raw.decode("utf-8"),
            "credentials": proposed.get("credentials", []),
            "authorizations": proposed.get("authorizations", {}),
        }
        if "capability_timeouts" in proposed:
            item["capability_timeouts"] = proposed["capability_timeouts"]
        if "config_migrations" in proposed:
            item["config_migrations"] = proposed["config_migrations"]
        ExternalSourcePolicies(
            _encode({"schema_version": 1, "packages": [item]}).encode()
        )
        return str(root.resolve(strict=True)), raw, manifest, item, metadata.star_cls

    async def plan(self, plugin_name, package_id):
        root, raw, manifest, item, instance = await asyncio.to_thread(
            self._candidate, plugin_name, package_id
        )

        def read(unit):
            row = unit.execute(
                "SELECT revision,plugin_name,package_root,permission_digest FROM extension_installation_grants WHERE package_id=?",
                (package_id,),
            ).fetchone()
            return dict(row) if row else None

        old = await self.database.executor.run_transaction(read)
        if old and (old["plugin_name"] != plugin_name or old["package_root"] != root):
            raise ExtensionHostError("package_already_owned")
        snapshot = permission_snapshot(manifest)
        material = {
            "plugin_name": plugin_name,
            "package_id": package_id,
            "package_root": root,
            "manifest_digest": hashlib.sha256(raw).hexdigest(),
            "policy": item,
            "expected_revision": old["revision"] if old else 0,
        }
        digest = hashlib.sha256(_encode(material).encode()).hexdigest()
        if (
            getattr(self.context.get_registered_star(plugin_name), "star_cls", None)
            is not instance
        ):
            raise ExtensionHostError("plugin_instance_unavailable")
        return {
            "plan_id": digest,
            "package_id": package_id,
            "plugin_name": plugin_name,
            "expected_revision": material["expected_revision"],
            "manifest_digest": material["manifest_digest"],
            "permission_digest": permission_digest(snapshot),
            "permissions": snapshot,
            "source_policy": {
                key: value for key, value in item.items() if key != "manifest"
            },
            "restart_required": True,
        }

    async def approve(
        self,
        plugin_name,
        package_id,
        *,
        plan_id,
        expected_revision,
        actor,
        current,
        commit_fence,
    ):
        if (
            type(expected_revision) is not int
            or expected_revision < 0
            or type(actor) is not str
            or not actor
            or not callable(current)
            or not callable(commit_fence)
        ):
            raise ValueError("trusted installation approval required")
        current()
        plan = await self.plan(plugin_name, package_id)
        if plan["plan_id"] != plan_id or plan["expected_revision"] != expected_revision:
            raise RevisionConflict(
                "extension_installation", expected_revision, plan["expected_revision"]
            )
        root, raw, manifest, item, instance = await asyncio.to_thread(
            self._candidate, plugin_name, package_id
        )
        if hashlib.sha256(raw).hexdigest() != plan["manifest_digest"]:
            raise RevisionConflict(
                "extension_installation_content",
                expected_revision,
                plan["expected_revision"],
            )
        if {key: value for key, value in item.items() if key != "manifest"} != plan[
            "source_policy"
        ]:
            raise RevisionConflict(
                "extension_installation_policy",
                expected_revision,
                plan["expected_revision"],
            )
        current()

        def check_instance():
            current()
            metadata = self.context.get_registered_star(plugin_name)
            if (
                metadata is None
                or metadata.activated is not True
                or metadata.star_cls is not instance
            ):
                raise ExtensionHostError("plugin_instance_unavailable")

        @contextmanager
        def authorized_commit():
            # Revocation holds this same trusted Host authority lock. Keep it
            # through SQLite commit, rather than only checking before return.
            with commit_fence():
                check_instance()
                yield

        def apply(unit):
            check_instance()
            unit.add_commit_guard(authorized_commit)
            row = unit.execute(
                "SELECT revision,plugin_name,package_root FROM extension_installation_grants WHERE package_id=?",
                (package_id,),
            ).fetchone()
            revision = row["revision"] if row else 0
            if (
                revision != expected_revision
                or row
                and (row["plugin_name"] != plugin_name or row["package_root"] != root)
            ):
                raise RevisionConflict(
                    "extension_installation", expected_revision, revision
                )
            revision += 1
            unit.execute(
                "INSERT INTO extension_installation_grants VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(package_id) DO UPDATE SET revision=excluded.revision,approved_policy=excluded.approved_policy,permission_digest=excluded.permission_digest,approved_manifest_digest=excluded.approved_manifest_digest,approved_by=excluded.approved_by,approved_at=excluded.approved_at",
                (
                    package_id,
                    plugin_name,
                    root,
                    revision,
                    _encode(item),
                    plan["permission_digest"],
                    plan["manifest_digest"],
                    actor,
                    time(),
                ),
            )
            unit.execute(
                "INSERT INTO extension_installation_audit(package_id,revision,action,content_digest,permission_digest,actor,recorded_at) VALUES(?,?,?,?,?,?,?)",
                (
                    package_id,
                    revision,
                    "trusted_approval",
                    plan["manifest_digest"],
                    plan["permission_digest"],
                    actor,
                    time(),
                ),
            )
            current()
            return {
                "package_id": package_id,
                "revision": revision,
                "restart_required": True,
            }

        return await self.database.executor.run_transaction(
            apply, begin_mode="IMMEDIATE"
        )

    async def bind(self, plugin_name, root, package, *, current=None):
        """Called only after actual Host owner/root validation, never by module."""
        root = str(Path(root).resolve(strict=True))
        package_id = package.package_id
        if not self.policies.permits(package):
            raise ExtensionHostError("installation_approval_required")
        payload = json.loads(self.policies.payload)
        item = next(
            item
            for item in payload["packages"]
            if parse_manifest(item["manifest"].encode()).package_id == package_id
        )
        approved_digest = permission_digest(
            permission_snapshot(parse_manifest(item["manifest"].encode()))
        )

        def apply(unit):
            if current is not None and current() is not True:
                raise ExtensionHostError("plugin_instance_unavailable")
            row = unit.execute(
                "SELECT * FROM extension_installation_grants WHERE package_id=?",
                (package_id,),
            ).fetchone()
            if row is None:
                unit.execute(
                    "INSERT INTO extension_installation_grants VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        package_id,
                        plugin_name,
                        root,
                        1,
                        _encode(item),
                        approved_digest,
                        self.policies.manifests[package_id],
                        "core_build_policy_migration",
                        time(),
                    ),
                )
                unit.execute(
                    "INSERT INTO extension_installation_audit(package_id,revision,action,content_digest,permission_digest,actor,recorded_at) VALUES(?,?,?,?,?,?,?)",
                    (
                        package_id,
                        1,
                        "equivalent_build_policy_migration",
                        package._provenance.manifest_sha256.hex(),
                        approved_digest,
                        "core_build_policy_migration",
                        time(),
                    ),
                )
            elif row["plugin_name"] != plugin_name or row["package_root"] != root:
                raise ExtensionHostError("package_already_owned")
            for rule in self.policies.config_migrations.get(package_id, ()):
                if self.config_principal_id is None:
                    raise ValueError(
                        "configuration migration requires a trusted principal"
                    )
                rule_digest = permission_digest(dict(rule))
                identity = (self.config_principal_id, rule["owner"], rule["field"])
                old = unit.execute(
                    "SELECT value_json,revision FROM config_entries WHERE principal_id=? AND module_id=? AND field=?",
                    identity,
                ).fetchone()
                if old is None:
                    continue
                value = json.loads(old["value_json"])
                if (
                    type(value) is not int
                    or not rule["from_minimum"] <= value < rule["to_minimum"]
                ):
                    continue
                if unit.execute(
                    "SELECT 1 FROM extension_config_compatibility_audit WHERE package_id=? AND principal_id=? AND module_id=? AND field=? AND rule_digest=?",
                    (package_id, *identity, rule_digest),
                ).fetchone():
                    raise ValueError("previously migrated configuration regressed")
                state = unit.execute(
                    "SELECT revision FROM config_state WHERE principal_id=? AND module_id=?",
                    identity[:2],
                ).fetchone()
                if state is None:
                    raise ValueError("configuration state is incomplete")
                revision = state["revision"] + 1
                encoded = json.dumps(rule["to_minimum"])
                unit.execute(
                    "UPDATE config_state SET revision=? WHERE principal_id=? AND module_id=? AND revision=?",
                    (revision, *identity[:2], state["revision"]),
                )
                unit.execute(
                    "UPDATE config_entries SET value_json=?,revision=? WHERE principal_id=? AND module_id=? AND field=? AND revision=?",
                    (encoded, revision, *identity, old["revision"]),
                )
                unit.execute(
                    "INSERT INTO extension_config_compatibility_audit VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        package_id,
                        *identity,
                        rule_digest,
                        state["revision"],
                        revision,
                        old["value_json"],
                        encoded,
                        time(),
                    ),
                )
            if current is not None and current() is not True:
                raise ExtensionHostError("plugin_instance_unavailable")

        await self.database.executor.run_transaction(apply, begin_mode="IMMEDIATE")
