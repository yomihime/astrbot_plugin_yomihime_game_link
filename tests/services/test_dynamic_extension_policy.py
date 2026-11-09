"""Real SQLite and official Ed25519 policy boundary tests; synthetic keys only."""

import asyncio
import base64
import copy
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from ygl_test_subject.extensions.disk_manifest import parse_manifest
from ygl_test_subject.extensions.hot_update_protocol import payload_digest
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories_extension_policy import (
    ExtensionPolicyRepository,
    PolicyError,
    advance_release_head,
    assert_execution_policy,
)
from ygl_test_subject.services.extension_permissions import (
    permission_delta,
    permission_snapshot,
    permissions_within,
)
from ygl_test_subject.services.external_source_policies import (
    parse_package_source_policy,
)
from ygl_test_subject.services.publisher_trust import PublisherTrust

from tests.services.test_external_source_policies import source_policy_fixture

PLUGIN = {"author": "Synthetic Author", "name": "synthetic_plugin"}
PERMISSIONS = {"demo": {"capabilities": {"read": {"effect": "read_only"}}}}


def release(sequence=1, **changes):
    files = [{"path": "main.py", "size": 1, "sha256": hashlib.sha256(b"x").hexdigest()}]
    value = {
        "schema": "ygl.release/1",
        "plugin_id": PLUGIN,
        "package_id": "unknown_package",
        "release": "1.0.0",
        "channel": "stable",
        "release_sequence": sequence,
        "files": files,
        "payload_digest": payload_digest(files),
    }
    value.update(changes)
    return json.dumps(value).encode()


class DynamicPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "policy.sqlite"
        self.db = SQLiteDatabase(self.path)
        self.addAsyncCleanup(self.db.executor.close)
        self.repo = ExtensionPolicyRepository(self.db)
        self.trust = PublisherTrust(self.repo)
        self.key = Ed25519PrivateKey.generate()
        public = self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.key_id = hashlib.sha256(public).hexdigest()
        await self.repo.put_publisher(
            public,
            scope_policy={"channels": ["stable"]},
            actor_ref="admin",
            expected_revision=0,
        )
        await self.approve()

    async def approve(self, key=None, expected=0, permissions=None, plugin=None):
        return await self.repo.approve_package(
            "unknown_package",
            publisher_key_id=self.key_id if key is None else key,
            plugin_id=PLUGIN if plugin is None else plugin,
            permissions=PERMISSIONS if permissions is None else permissions,
            actor_ref="admin",
            expected_revision=expected,
        )

    def signature(self, raw, key=None):
        return {
            "algorithm": "ed25519",
            "key_id": self.key_id,
            "signature_base64": base64.b64encode((key or self.key).sign(raw)).decode(),
        }

    async def evaluate(self, raw=None, signature=True, permissions=None, plugin=None):
        raw = release() if raw is None else raw
        return await self.trust.evaluate(
            raw,
            self.signature(raw) if signature is True else signature,
            host_plugin_id=PLUGIN if plugin is None else plugin,
            permission_projection=PERMISSIONS if permissions is None else permissions,
        )

    async def test_explicit_publisher_ceiling_and_existing_revoked_grant(self):
        public = self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        await self.repo.put_publisher(
            public,
            scope_policy={"channels": ["stable"], "capability_ceiling": PERMISSIONS},
            actor_ref="admin",
            expected_revision=1,
        )
        raw = release(package_id="another_unknown")
        receipt = await self.evaluate(raw)
        self.assertEqual(receipt.grant_revision, 0)
        self.assertIsNone(await self.repo.grant("another_unknown"))
        self.assertTrue(await self.repo.execution_revision_valid(receipt))
        expanded = copy.deepcopy(PERMISSIONS)
        expanded["demo"]["capabilities"]["send"] = {"effect": "write"}
        with self.assertRaises(PolicyError):
            await self.evaluate(raw, permissions=expanded)
        await self.repo.revoke_package(
            "unknown_package", actor_ref="admin", expected_revision=1
        )
        with self.assertRaises(PolicyError):
            await self.evaluate()
        await self.repo.put_publisher(
            public,
            scope_policy={"channels": ["stable"]},
            actor_ref="admin",
            expected_revision=2,
        )
        self.assertFalse(await self.repo.execution_revision_valid(receipt))

    async def test_mutation_fence_runs_before_sql_and_failure_prevents_mutation(self):
        calls = []

        def fence(kind, key):
            calls.append((kind, key))
            raise RuntimeError("closed gate")

        repository = ExtensionPolicyRepository(self.db, before_mutation=fence)
        with self.assertRaises(RuntimeError):
            await repository.revoke_package(
                "unknown_package", actor_ref="admin", expected_revision=1
            )
        self.assertEqual(calls, [("package_grant", "unknown_package")])
        self.assertEqual((await self.repo.grant("unknown_package"))["revision"], 1)
        with self.assertRaises(ValueError):
            await repository.revoke_package(
                "../bad", actor_ref="admin", expected_revision=1
            )
        self.assertEqual(len(calls), 1)
        repository = ExtensionPolicyRepository(
            self.db, before_mutation=lambda kind, key: calls.append((kind, key))
        )
        with self.assertRaises(PolicyError):
            await repository.revoke_package(
                "unknown_package", actor_ref="admin", expected_revision=0
            )
        self.assertEqual(len(calls), 2)
        self.assertEqual((await self.repo.grant("unknown_package"))["revision"], 1)
        await repository.revoke_publisher(
            self.key_id, actor_ref="admin", expected_revision=1
        )
        self.assertEqual(calls[-1], ("publisher", self.key_id))

    async def test_hook_runs_before_first_task_yield_and_async_hook_rejected(self):
        calls = []

        class Executor:
            async def run_transaction(self, callback, **kwargs):
                calls.append("sql submitted")
                await asyncio.sleep(0)

        class Database:
            executor = Executor()

        repository = ExtensionPolicyRepository(
            Database(), before_mutation=lambda kind, key: calls.append("fenced")
        )
        operation = repository.revoke_package(
            "unknown_package", actor_ref="admin", expected_revision=1
        )
        try:
            self.assertIsNone(operation.send(None))
            self.assertEqual(calls, ["fenced", "sql submitted"])
        finally:
            operation.close()

        async def incorrect_hook(kind, key):
            raise AssertionError("must not run")

        repository = ExtensionPolicyRepository(self.db, before_mutation=incorrect_hook)
        with self.assertRaises(TypeError):
            await repository.revoke_package(
                "unknown_package", actor_ref="admin", expected_revision=1
            )
        self.assertEqual((await self.repo.grant("unknown_package"))["revision"], 1)

    async def test_admin_mutations_reject_malformed_authority(self):
        for bad in [
            [],
            {"owner": []},
            {"owner": {"kind": []}},
            {"owner": {"kind": {"a": float("nan")}}},
        ]:
            with self.assertRaises(ValueError):
                await self.approve(expected=1, permissions=bad)
        for package in ["../bad", "A", "a/other", "a" * 65]:
            with self.assertRaises(ValueError):
                await self.repo.approve_package(
                    package,
                    publisher_key_id=self.key_id,
                    plugin_id=PLUGIN,
                    permissions=PERMISSIONS,
                    actor_ref="admin",
                    expected_revision=0,
                )
        for revision in [True, -1, 9007199254740992]:
            with self.assertRaises(ValueError):
                await self.approve(expected=revision)
        with self.assertRaises(ValueError):
            await self.repo.approve_artifact(
                "0" * 64,
                package_id="unknown_package",
                plugin_id=PLUGIN,
                grant_revision=1,
                actor_ref="admin",
                expected_revision=0,
                revoked=1,
            )

    async def test_concurrent_approval_cas_only_one_wins(self):
        outcomes = await asyncio.gather(
            self.approve(expected=1), self.approve(expected=1), return_exceptions=True
        )
        self.assertEqual(sum(isinstance(item, dict) for item in outcomes), 1)
        failures = [item for item in outcomes if isinstance(item, PolicyError)]
        self.assertEqual([item.code for item in failures], ["revision_conflict"])
        self.assertEqual((await self.repo.grant("unknown_package"))["revision"], 2)

    async def test_head_rolls_back_with_failed_activation(self):
        receipt = await self.evaluate()

        def failed(unit):
            advance_release_head(
                unit,
                package_id=receipt.package_id,
                publisher_key_id=self.key_id,
                channel="stable",
                sequence=1,
                artifact_digest=receipt.artifact_digest,
            )
            raise RuntimeError("activation failed")

        with self.assertRaises(RuntimeError):
            await self.db.executor.run_transaction(failed)
        self.assertIsNone(
            await self.repo.head(receipt.package_id, self.key_id, "stable")
        )

    async def test_unknown_package_and_prepare_has_no_head_writes(self):
        receipt = await self.evaluate()
        self.assertTrue(await self.repo.execution_revision_valid(receipt))
        self.assertIsNone(
            await self.repo.head("unknown_package", self.key_id, "stable")
        )
        self.assertEqual((await self.repo.grant("unknown_package"))["revision"], 1)

    async def test_raw_bytes_signature_wrong_key_and_tamper(self):
        raw = release()
        for candidate, sig in [
            (raw + b" ", self.signature(raw)),
            (raw, self.signature(raw, Ed25519PrivateKey.generate())),
            (raw, {**self.signature(raw), "signature_base64": "!"}),
        ]:
            with self.assertRaises(PolicyError) as ctx:
                await self.evaluate(candidate, signature=sig)
            self.assertEqual(ctx.exception.code, "artifact_tampered")

    async def test_unsigned_approval_exact_digest_invalid_signature_never_falls_back(
        self,
    ):
        await self.approve(key="", expected=1)
        raw = release()
        await self.repo.approve_artifact(
            hashlib.sha256(raw).hexdigest(),
            package_id="unknown_package",
            plugin_id=PLUGIN,
            grant_revision=2,
            actor_ref="admin",
            expected_revision=0,
        )
        receipt = await self.evaluate(raw, signature=None)
        self.assertTrue(await self.repo.execution_revision_valid(receipt))
        with self.assertRaises(PolicyError):
            await self.evaluate(raw + b" ", signature=None)
        with self.assertRaises(PolicyError) as ctx:
            await self.evaluate(
                raw, signature={**self.signature(raw), "signature_base64": "!"}
            )
        self.assertEqual(ctx.exception.code, "artifact_tampered")
        await self.repo.approve_artifact(
            receipt.artifact_digest,
            package_id="unknown_package",
            plugin_id=PLUGIN,
            grant_revision=2,
            actor_ref="admin",
            expected_revision=1,
            revoked=True,
        )
        self.assertFalse(await self.repo.execution_revision_valid(receipt))

    async def test_identity_change_cannot_reuse_grant(self):
        changed = {"author": "Another", "name": "synthetic_plugin"}
        with self.assertRaises(PolicyError):
            await self.evaluate(release(plugin_id=changed), plugin=changed)
        with self.assertRaises(PolicyError):
            await self.evaluate(plugin=changed)
        other = (
            Ed25519PrivateKey.generate()
            .public_key()
            .public_bytes(Encoding.Raw, PublicFormat.Raw)
        )
        row = await self.repo.put_publisher(
            other,
            scope_policy={"channels": ["stable"]},
            actor_ref="admin",
            expected_revision=0,
        )
        self.assertNotEqual(row["key_id"], self.key_id)

    async def test_permission_delta_cas_and_revocation_at_sql_effect(self):
        expanded = copy.deepcopy(PERMISSIONS)
        expanded["demo"]["capabilities"]["send"] = {"effect": "write"}
        with self.assertRaises(PolicyError) as ctx:
            await self.evaluate(permissions=expanded)
        self.assertEqual(
            ctx.exception.permission_delta["added"], ["demo/capabilities/send"]
        )
        receipt = await self.evaluate()
        with self.assertRaises(PolicyError):
            await self.approve(expected=0)
        await self.repo.revoke_package(
            "unknown_package", actor_ref="admin", expected_revision=1
        )

        def effect(unit):
            assert_execution_policy(unit, receipt)
            unit.execute("CREATE TABLE forbidden_effect (id INTEGER)")

        with self.assertRaises(PolicyError):
            await self.db.executor.run_transaction(effect)
        self.assertFalse(await self.repo.execution_revision_valid(receipt))

    async def test_publisher_revocation_and_reopen_persistence(self):
        receipt = await self.evaluate()
        public = self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        await self.repo.put_publisher(
            public,
            scope_policy={"channels": ["stable"]},
            revoked=True,
            actor_ref="admin",
            expected_revision=1,
        )
        self.assertFalse(await self.repo.execution_revision_valid(receipt))
        await self.db.executor.close()
        reopened = SQLiteDatabase(self.path)
        self.addAsyncCleanup(reopened.executor.close)
        repo = ExtensionPolicyRepository(reopened)
        self.assertEqual((await repo.publisher(self.key_id))["revision"], 2)
        self.assertFalse(await repo.execution_revision_valid(receipt))

    async def test_head_activation_cas_rollback_and_equivocation(self):
        receipt = await self.evaluate()

        def accept(unit):
            advance_release_head(
                unit,
                package_id=receipt.package_id,
                publisher_key_id=self.key_id,
                channel="stable",
                sequence=1,
                artifact_digest=receipt.artifact_digest,
            )

        await self.db.executor.run_transaction(accept)
        with self.assertRaises(PolicyError):
            await self.evaluate(release(0))
        with self.assertRaises(PolicyError):
            await self.evaluate(release(release="1.0.1"))
        with self.assertRaises(PolicyError):
            await self.db.executor.run_transaction(accept)
        await self.evaluate(release(2))
        self.assertEqual(
            (await self.repo.head(receipt.package_id, self.key_id, "stable"))[
                "release_sequence"
            ],
            1,
        )


class SourcePolicyTests(unittest.TestCase):
    def fixture(self):
        manifest, old = source_policy_fixture()
        rows = [
            {"owner": owner, "flow_id": flow, **row}
            for owner, flows in old["authorizations"].items()
            for flow, row in flows.items()
        ]
        return json.dumps(manifest).encode(), {
            "schema": "ygl.source-policy/1",
            "credentials": old["credentials"],
            "authorizations": rows,
        }

    def parse(self, raw, value, **changes):
        payload = json.dumps(value).encode()
        args = {
            "b05_manifest_bytes": raw,
            "source_policy_path": "policy.json",
            "required_features": ["source.policies/1"],
            "release_inventory": [
                {
                    "path": "policy.json",
                    "size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            ],
        }
        args.update(changes)
        return parse_package_source_policy(payload, **args)

    def test_changed_injection_or_authorization_requires_new_grant(self):
        raw, policy = self.fixture()
        _, original = self.parse(raw, policy)
        changed = copy.deepcopy(policy)
        changed["credentials"][0]["injections"][0]["name"] = "X-New-Key"
        _, projection = self.parse(raw, changed)
        self.assertFalse(permissions_within(projection, original))
        changed = copy.deepcopy(policy)
        changed["authorizations"][0]["descriptor"]["poll_interval_seconds"] = 3
        _, projection = self.parse(raw, changed)
        self.assertFalse(permissions_within(projection, original))
        self.assertIn(
            "authorizations", original["$source_policy"]["policy"]["normalized"]
        )

    def test_signed_policy_descriptions_remain_in_full_policy_projection(self):
        raw, policy = self.fixture()
        _, original = self.parse(raw, policy)
        policy["credentials"][0]["description"] = "Revised signed policy explanation"
        _, changed = self.parse(raw, policy)
        self.assertFalse(permissions_within(changed, original))
        self.assertEqual(
            permission_delta(changed, original)["changed"],
            ["$source_policy/policy/normalized"],
        )

    def test_path_feature_inventory_duplicate_and_migrations_rejected(self):
        raw, policy = self.fixture()
        for changes in [
            {"source_policy_path": "../policy.json"},
            {"required_features": []},
            {"release_inventory": []},
        ]:
            with self.assertRaises(ValueError):
                self.parse(raw, policy, **changes)
        for value in [
            {**policy, "config_migrations": []},
            {**policy, "authorizations": policy["authorizations"] * 2},
        ]:
            with self.assertRaises(ValueError):
                self.parse(raw, value)
        with self.assertRaises(ValueError):
            self.parse(b"{}", policy)

    def test_unknown_fields_duplicate_json_and_stable_injection_order(self):
        raw, policy = self.fixture()
        with self.assertRaises(ValueError):
            self.parse(raw, {**policy, "unknown_authority": True})
        payload = b'{"schema":"ygl.source-policy/1","schema":"ygl.source-policy/1","credentials":[],"authorizations":[]}'
        with self.assertRaises(ValueError):
            parse_package_source_policy(
                payload,
                b05_manifest_bytes=raw,
                source_policy_path="policy.json",
                required_features=["source.policies/1"],
                release_inventory=[
                    {
                        "path": "policy.json",
                        "size": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                ],
            )
        first = policy["credentials"][0]["injections"][0]
        policy["credentials"][0]["injections"].append({**first, "name": "X-Second-Key"})
        _, before = self.parse(raw, policy)
        policy["credentials"][0]["injections"].reverse()
        _, after = self.parse(raw, policy)
        self.assertEqual(before, after)

    def test_display_and_version_do_not_change_projection(self):
        raw, _ = self.fixture()
        value = json.loads(raw)
        before = permission_snapshot(parse_manifest(raw))
        value["package_version"] = "2.0.0"
        value["display_name"] = "New name"
        self.assertEqual(
            before, permission_snapshot(parse_manifest(json.dumps(value).encode()))
        )


class PermissionProjectionTests(unittest.TestCase):
    def setUp(self):
        raw, _ = source_policy_fixture()
        self.manifest = parse_manifest(json.dumps(raw).encode())
        self.module = self.manifest.modules[0]
        self.before = permission_snapshot(self.manifest)

    def projection(self, module):
        return permission_snapshot(replace(self.manifest, modules=(module,)))

    def test_presentation_and_version_changes_have_no_permission_delta(self):
        module = self.module
        page = module.pages[0]
        field = next(field for field in module.config_fields if field.sensitive)
        variants = {
            "page_order": replace(module, pages=(replace(page, order=99),)),
            "page_role": replace(module, pages=(replace(page, role="config"),)),
            "page_title": replace(module, pages=(replace(page, title="New title"),)),
            "command_help": replace(
                module,
                commands=tuple(
                    replace(command, help_text="New help")
                    for command in module.commands
                ),
            ),
            "sensitive_description": replace(
                module,
                config_fields=tuple(
                    replace(item, description="New description")
                    if item.name == field.name
                    else item
                    for item in module.config_fields
                ),
            ),
            "module_version": replace(module, module_version="2.0.0"),
            # Pure projection test: an unsupported output version must still be
            # rejected by parse_manifest's separate compatibility validation.
            "output_version": replace(
                module,
                capabilities=tuple(
                    replace(cap, output_version="future.output")
                    for cap in module.capabilities
                ),
            ),
        }
        for name, changed in variants.items():
            with self.subTest(name=name):
                projection = self.projection(changed)
                self.assertEqual(
                    permission_delta(projection, self.before),
                    {"added": [], "removed": [], "changed": []},
                )
                self.assertTrue(permissions_within(projection, self.before))
        changed_package = replace(
            self.manifest, package_version="2.0.0", display_name="New package title"
        )
        self.assertEqual(permission_snapshot(changed_package), self.before)
        raw, _ = source_policy_fixture()
        raw["modules"][0]["capabilities"][0]["output_version"] = "future.output"
        with self.assertRaises(ValueError):
            parse_manifest(json.dumps(raw).encode())

    def test_tool_description_is_content_but_target_is_authority(self):
        from yomihime_game_link_sdk.declarations import ToolDescriptor

        tool = ToolDescriptor("status", "status", {}, "Original description")
        module = replace(self.module, tools=(tool,))
        before = self.projection(module)
        self.assertEqual(
            before,
            self.projection(
                replace(module, tools=(replace(tool, description="New description"),))
            ),
        )
        self.assertFalse(
            permissions_within(
                self.projection(
                    replace(module, tools=(replace(tool, capability_id="other"),))
                ),
                before,
            )
        )

    def test_access_effect_schema_origins_and_credential_scope_remain_authority(self):
        from yomihime_game_link_sdk.declarations import CapabilityEffect, PrivacyFloor

        module = self.module
        page = module.pages[0]
        cap = module.capabilities[0]
        variants = {
            "page_access": replace(
                module, pages=(replace(page, access="future_private"),)
            ),
            "page_capability": replace(
                module, pages=(replace(page, capability_id="other"),)
            ),
            "page_credentials": replace(
                module,
                pages=(replace(page, credential_fields=("credential_example",)),),
            ),
            "effect": replace(
                module, capabilities=(replace(cap, effect=CapabilityEffect.WRITE),)
            ),
            "privacy": replace(
                module, capabilities=(replace(cap, privacy_floor=PrivacyFloor.OWNER),)
            ),
            "origins": replace(
                module, capabilities=(replace(cap, invocation_origins=()),)
            ),
            "required_source": replace(
                module, capabilities=(replace(cap, required_sources=("api",)),)
            ),
            "schema": replace(
                module, capabilities=(replace(cap, input_schema={"type": "object"}),)
            ),
        }
        for name, changed in variants.items():
            with self.subTest(name=name):
                projection = self.projection(changed)
                self.assertFalse(permissions_within(projection, self.before))
                self.assertTrue(permission_delta(projection, self.before)["changed"])

    def test_page_list_reorder_is_content(self):
        first = self.module.pages[0]
        second = replace(first, route_id="other", role="aux", order=10)
        before = self.projection(replace(self.module, pages=(first, second)))
        self.assertEqual(
            before, self.projection(replace(self.module, pages=(second, first)))
        )
