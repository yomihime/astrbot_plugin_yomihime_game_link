"""Verified Core admin proofs, inert real artifacts and persistent SQLite CAS."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from ygl_test_subject.bootstrap.artifact_cache import ArtifactCache
from ygl_test_subject.core.contracts.administration import (
    AdminAuthorizationDenied,
    AdminAuthorizationGrant,
    AdminOperation,
)
from ygl_test_subject.extensions.hot_update_protocol import payload_digest
from ygl_test_subject.extensions.runtime_bundle import RuntimeBundleError
from ygl_test_subject.infrastructure.sqlite.database import (
    SQLiteDatabase,
    SQLiteUnitOfWork,
)
from ygl_test_subject.infrastructure.sqlite.repositories_admin_credentials import (
    SQLiteAdminCredentialRepository,
)
from ygl_test_subject.infrastructure.sqlite.repositories_extension_policy import (
    ExtensionPolicyRepository,
)
from ygl_test_subject.services.admin_authorization import (
    AdminAuthorizationService,
    _digest,
)
from ygl_test_subject.services.extension_discovery import ExtensionDiscovery
from ygl_test_subject.services.hot_update_admin import HotUpdateAdmin
from ygl_test_subject.services.publisher_trust import PublisherTrust
from ygl_test_subject.yomihime_game_link_sdk.services import ConfigTarget

from tests.contracts.test_hot_update_protocol import manifest as envelope_fixture

PACKAGE = "unknown_package"
PLUGIN = {"author": "tests", "name": "unknown_plugin"}


class HotUpdateAdminTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "plugin"
        self.root.mkdir()
        self.key = Ed25519PrivateKey.generate()
        self.public = self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.key_id = hashlib.sha256(self.public).hexdigest()
        self.write_artifact()
        self.metadata = SimpleNamespace(
            **PLUGIN,
            activated=True,
            version="1.2.3",
            star_cls=object(),
            root_dir_name="plugin",
            module=SimpleNamespace(__file__=str(self.root / "main.py")),
        )
        self.context = SimpleNamespace(get_all_stars=lambda: [self.metadata])
        self.db = SQLiteDatabase(self.base / "state.sqlite")
        await self.db.executor.initialize()
        self.addAsyncCleanup(self.db.executor.close)
        self.credential_repository = SQLiteAdminCredentialRepository(self.db)
        self.auth = AdminAuthorizationService(self.credential_repository)
        self.addCleanup(self.auth.close)
        self.resources = {
            ConfigTarget("host", "game_link/core"): frozenset({"modules"})
        }
        self.source = self.auth.register_source(
            "verified-host",
            resources=self.resources,
            operations={AdminOperation.LIST_MODULES, AdminOperation.SET_ENABLED},
        )
        self.live = True
        self.generation = SimpleNamespace(receipt={"core_epoch": 3})
        self.runtime = SimpleNamespace(ready=True, _hot_generation=self.generation)
        self.cache = ArtifactCache(self.base / "cache")
        self.addCleanup(self.cache.close)
        self.repo = ExtensionPolicyRepository(self.db)
        self.manager = SimpleNamespace(
            repository=self.repo,
            generation=self.generation,
            runtime=self.runtime,
            core=SimpleNamespace(
                admin_operations=SimpleNamespace(
                    module_resources=lambda: self.resources
                )
            ),
            _closed=False,
            discovery=ExtensionDiscovery(self.context),
            cache=self.cache,
            packages={},
            endpoint=SimpleNamespace(_operations={}),
            trust=PublisherTrust(self.repo),
            _current=lambda package: True,
        )
        self.runtime._core = self.manager.core
        self.runtime._independent_packages = self.manager
        self.controller = HotUpdateAdmin(
            self.manager, self.auth, management_source=self.source
        )

    def write_artifact(
        self, *, signed=False, changed=False, invalid_signature=False, ordinary=False
    ):
        descriptor = {
            "schema": "ygl.plugin/1",
            "role": "dlc",
            "package_id": PACKAGE,
            "manifest": "business/manifest.json",
            "control_protocol": {"name": "ygl.control", "major": 1},
        }
        envelope = envelope_fixture()
        envelope.update(package_id=PACKAGE)
        if ordinary:
            envelope["required_features"].append("control.retire/1")
        if changed:
            envelope["modules"][0]["capabilities"][0]["input_schema"]["properties"][
                "value"
            ]["minimum"] = 1.25
        files = {
            "main.py": b"# inert host shell\n",
            "metadata.yaml": b"name: unknown_plugin\n",
            "requirements.txt": b"",
            "ygl-plugin.json": json.dumps(descriptor).encode(),
            "business/manifest.json": json.dumps(envelope).encode(),
            "business/sample/factory.py": b"raise AssertionError('must never import during inspection')\n",
        }
        for path, content in files.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        inventory = [
            {
                "path": path,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            for path, content in files.items()
        ]
        release = {
            "schema": "ygl.release/1",
            "plugin_id": PLUGIN,
            "package_id": PACKAGE,
            "release": "1.2.3",
            "channel": "stable",
            "release_sequence": 1,
            "payload_digest": payload_digest(inventory),
            "files": inventory,
        }
        if ordinary:
            release["schema"] = "ygl.release/2"
            del release["channel"], release["release_sequence"]
        raw = json.dumps(release).encode()
        (self.root / "release.json").write_bytes(raw)
        signature = self.root / "release.sig.json"
        if signed:
            signature.write_text(
                json.dumps(
                    {
                        "algorithm": "ed25519",
                        "key_id": self.key_id,
                        "signature_base64": base64.b64encode(
                            (b"x" * 64) if invalid_signature else self.key.sign(raw)
                        ).decode(),
                    }
                )
            )
        elif signature.exists():
            signature.unlink()
        return hashlib.sha256(raw).hexdigest()

    async def grant(self, operation=AdminOperation.SET_ENABLED):
        request = object()
        context = self.source.issue(
            subject="verified-admin",
            request=request,
            expiry=time.time() + 30,
            operations={operation},
            resources=self.resources,
            live=lambda value: self.live and value is request,
        )
        return await self.auth.authorize(
            operation, invocation=None, context=context, resources=self.resources
        )

    async def call(self, action, grant=None, **data):
        if grant is None:
            grant = await self.grant(
                AdminOperation.LIST_MODULES
                if action in {"status", "inspect"}
                else AdminOperation.SET_ENABLED
            )
        return await self.controller.request(
            action, {"core_epoch": 3, **data}, grant=grant
        )

    async def inspect(self):
        result = await self.call("inspect", package_id=PACKAGE)
        self.assertTrue(result["ok"], result)
        return result

    async def test_host_install_is_visible_and_does_not_offer_legacy_approval(self):
        expected_digest = self.write_artifact(ordinary=True)
        plan = await self.inspect()
        self.assertEqual(plan["signature_state"], "host_install")
        self.assertEqual(plan["installed_digest"], expected_digest)
        self.assertEqual(plan["grant_revision"], 0)
        self.assertEqual(plan["artifact_revision"], 0)
        self.assertIsNone(plan["reason_code"])
        for action in ("package-approve", "artifact-approve"):
            result = await self.call(
                action, package_id=PACKAGE, plan_id=plan["plan_id"], expected_revision=0
            )
            self.assertEqual(result["error"]["code"], "host_install_managed")
        self.assertIsNone(await self.repo.grant(PACKAGE))
        self.assertIsNone(await self.repo.approval(expected_digest))
        self.metadata.version = "2.0.0"
        result = await self.call("inspect", package_id=PACKAGE)
        self.assertEqual(result["error"]["code"], "artifact_tampered")

    async def test_inert_inspection_status_has_no_secrets_and_both_versions(self):
        plan = await self.inspect()
        self.assertEqual(plan["installed_version"], "1.2.3")
        self.assertIsNone(plan["running_version"])
        self.assertEqual(plan["state"], "approval_required")
        self.assertTrue(plan["permissions_delta"]["added"])
        encoded = json.dumps(plan)
        self.assertNotIn(str(self.root), encoded)
        self.assertNotIn("owner_token", encoded)
        self.assertNotIn("session", encoded)
        self.assertEqual(self.cache.candidates, set())
        self.manager.packages[PACKAGE] = SimpleNamespace(
            bundle=SimpleNamespace(release_doc={"release": "1.0.0"}),
            receipt=SimpleNamespace(artifact_digest="f" * 64),
            fallback=True,
        )
        plan = await self.inspect()
        self.assertEqual(plan["running_version"], "1.0.0")
        self.assertEqual(plan["state"], "fallback")
        result = await self.call("status")
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["packages"]), 1)

    async def test_pending_prepare_diagnostic_preserves_owned_cleanup_state(self):
        for action in ("package-approve", "artifact-approve"):
            plan = await self.inspect()
            result = await self.call(
                action, package_id=PACKAGE, plan_id=plan["plan_id"], expected_revision=0
            )
            self.assertTrue(result["ok"], result)
        self.manager.endpoint._operations["pending"] = SimpleNamespace(
            instance=self.metadata.star_cls,
            operation_id="pending",
            reply={
                "state": "cleanup_pending",
                "error": {"code": "prepare_timeout"},
            },
        )
        result = await self.call("status")
        item = result["packages"][0]
        self.assertEqual(item["state"], "cleanup_pending")
        self.assertEqual(item["reason_code"], "prepare_timeout")
        self.assertEqual(item["operation_id"], "pending")
        for fallback in (False, True):
            self.manager.packages[PACKAGE] = SimpleNamespace(
                bundle=SimpleNamespace(release_doc={"release": "1.0.0"}),
                receipt=SimpleNamespace(artifact_digest="f" * 64),
                fallback=fallback,
            )
            item = (await self.call("status"))["packages"][0]
            self.assertEqual(item["state"], "fallback" if fallback else "active")
            self.assertEqual(item["reason_code"], "prepare_timeout")

    async def test_forged_grant_wrong_resource_and_stale_generation_rejected(self):
        forged = AdminAuthorizationGrant(AdminOperation.SET_ENABLED, 1)
        result = await self.call(
            "publisher-enroll",
            grant=forged,
            public_key_base64=base64.b64encode(self.public).decode(),
            channels=["stable"],
            expected_revision=0,
        )
        self.assertEqual(result["error"]["code"], "admin_authorization_denied")
        grant = await self.grant()
        result = await self.controller.request(
            "package-revoke",
            {"core_epoch": 2, "package_id": PACKAGE, "expected_revision": 0},
            grant=grant,
        )
        self.assertEqual(result["error"]["code"], "stale_generation")
        self.assertIsNone(await self.repo.publisher(self.key_id))

    async def test_unsigned_package_and_exact_artifact_approval_cas_and_persistence(
        self,
    ):
        plan = await self.inspect()
        result = await self.call(
            "package-approve",
            package_id=PACKAGE,
            plan_id=plan["plan_id"],
            expected_revision=0,
        )
        self.assertTrue(result["ok"], result)
        plan = await self.inspect()
        result = await self.call(
            "artifact-approve",
            package_id=PACKAGE,
            plan_id=plan["plan_id"],
            expected_revision=0,
        )
        self.assertTrue(result["ok"], result)
        approval = await self.repo.approval(plan["installed_digest"])
        self.assertEqual(approval["grant_revision"], 1)
        reopened = SQLiteDatabase(self.db.path)
        self.addAsyncCleanup(reopened.executor.close)
        self.assertEqual(
            (await ExtensionPolicyRepository(reopened).grant(PACKAGE))["revision"], 1
        )
        result = await self.call(
            "package-revoke", package_id=PACKAGE, expected_revision=0
        )
        self.assertEqual(result["error"]["code"], "revision_conflict")
        result = await self.call(
            "artifact-revoke",
            package_id=PACKAGE,
            artifact_digest=plan["installed_digest"],
            expected_revision=1,
        )
        self.assertTrue(result["ok"])
        result = await self.call(
            "package-revoke", package_id=PACKAGE, expected_revision=1
        )
        self.assertTrue(result["ok"])

    async def test_plan_change_and_browser_authority_injection_rejected(self):
        plan = await self.inspect()
        self.write_artifact(changed=True)
        result = await self.call(
            "package-approve",
            package_id=PACKAGE,
            plan_id=plan["plan_id"],
            expected_revision=0,
        )
        self.assertEqual(result["error"]["code"], "plan_changed")
        result = await self.call(
            "package-approve",
            package_id=PACKAGE,
            plan_id=plan["plan_id"],
            expected_revision=0,
            permissions={"allow": "everything"},
        )
        self.assertEqual(result["error"]["code"], "invalid_request")
        self.assertIsNone(await self.repo.grant(PACKAGE))

    async def test_publisher_enrollment_revocation_and_invalid_signature_not_unsigned(
        self,
    ):
        self.write_artifact(signed=True)
        plan = await self.inspect()
        self.assertEqual(plan["signature_state"], "publisher_untrusted")
        result = await self.call(
            "publisher-enroll",
            public_key_base64=base64.b64encode(self.public).decode(),
            channels=["stable"],
            expected_revision=0,
        )
        self.assertTrue(result["ok"], result)
        plan = await self.inspect()
        self.assertEqual(plan["signature_state"], "verified")
        result = await self.call(
            "package-approve",
            package_id=PACKAGE,
            plan_id=plan["plan_id"],
            expected_revision=0,
        )
        self.assertTrue(result["ok"], result)
        result = await self.call(
            "publisher-revoke", key_id=self.key_id, expected_revision=1
        )
        self.assertTrue(result["ok"], result)
        self.write_artifact(signed=True, invalid_signature=True)
        result = await self.call("inspect", package_id=PACKAGE)
        self.assertEqual(result["error"]["code"], "artifact_tampered")
        self.assertIsNone(
            await self.repo.approval(
                self.write_artifact(signed=True, invalid_signature=True)
            )
        )

    async def test_local_issuer_cannot_gain_global_trust_for_single_owner_or_empty_catalog(
        self,
    ):
        local = self.auth.register_source(
            "local-owner",
            resources=self.resources,
            operations={AdminOperation.SET_ENABLED},
        )
        request = object()
        context = local.issue(
            subject="local-admin",
            request=request,
            expiry=time.time() + 30,
            operations={AdminOperation.SET_ENABLED},
            resources=self.resources,
            live=lambda value: value is request,
        )
        grant = await self.auth.authorize(
            AdminOperation.SET_ENABLED,
            invocation=None,
            context=context,
            resources=self.resources,
        )
        for resources in [self.resources, {}]:
            self.manager.core.admin_operations.module_resources = lambda: resources
            result = await self.call(
                "publisher-enroll",
                grant=grant,
                public_key_base64=base64.b64encode(self.public).decode(),
                channels=["stable"],
                expected_revision=0,
            )
            self.assertEqual(result["error"]["code"], "admin_authorization_denied")
        self.assertIsNone(await self.repo.publisher(self.key_id))
        self.controller.management_source = lambda: self.source
        grant = await self.grant()
        self.controller.management_source = lambda: local
        result = await self.call(
            "publisher-enroll",
            grant=grant,
            public_key_base64=base64.b64encode(self.public).decode(),
            channels=["stable"],
            expected_revision=0,
        )
        self.assertEqual(result["error"]["code"], "admin_authorization_denied")

    async def test_exact_global_issuer_still_requires_full_request_resource_scope(self):
        other = ConfigTarget("host", "other/owner")
        resources = {**self.resources, other: frozenset({"modules"})}
        source = self.auth.register_source(
            "full-global-fixture",
            resources=resources,
            operations={AdminOperation.SET_ENABLED},
        )
        request = object()
        context = source.issue(
            subject="partial-proof",
            request=request,
            expiry=time.time() + 30,
            operations={AdminOperation.SET_ENABLED},
            resources=self.resources,
            live=lambda value: value is request,
        )
        grant = await self.auth.authorize(
            AdminOperation.SET_ENABLED,
            invocation=None,
            context=context,
            resources=self.resources,
        )
        self.controller.management_source = source
        self.manager.core.admin_operations.module_resources = lambda: resources
        result = await self.call(
            "publisher-enroll",
            grant=grant,
            public_key_base64=base64.b64encode(self.public).decode(),
            channels=["stable"],
            expected_revision=0,
        )
        self.assertEqual(result["error"]["code"], "admin_authorization_denied")
        self.assertIsNone(await self.repo.publisher(self.key_id))

    async def test_async_worker_admin_check_cannot_skip_authorization(self):
        async def invalid_check(unit):
            return None

        with self.assertRaises(TypeError):
            await self.repo.put_publisher(
                self.public,
                scope_policy={"channels": ["stable"]},
                actor_ref="audit-is-not-authorization",
                expected_revision=0,
                admin_check=invalid_check,
            )
        self.assertIsNone(await self.repo.publisher(self.key_id))

    async def test_status_preserves_running_details_for_broken_installed_artifact(self):
        self.manager.packages[PACKAGE] = SimpleNamespace(
            bundle=SimpleNamespace(release_doc={"release": "1.0.0"}),
            receipt=SimpleNamespace(artifact_digest="f" * 64),
            fallback=True,
        )
        (self.root / "release.json").write_bytes(b"broken release")
        result = await self.call("status")
        self.assertTrue(result["ok"], result)
        item = result["packages"][0]
        self.assertIsNone(item["installed_version"])
        self.assertEqual(item["running_version"], "1.0.0")
        self.assertEqual(item["state"], "fallback")
        self.assertEqual(item["reason_code"], "artifact_invalid")
        self.assertNotIn(str(self.root), json.dumps(item))

    async def test_status_lists_publishers_without_installed_packages_and_no_private_metadata(
        self,
    ):
        await self.repo.put_publisher(
            self.public,
            scope_policy={"channels": ["preview", "stable"]},
            actor_ref="PRIVATE ACTOR",
            expected_revision=0,
        )
        await self.repo.revoke_publisher(
            self.key_id, actor_ref="PRIVATE ACTOR", expected_revision=1
        )
        self.context.get_all_stars = lambda: []
        result = await self.call("status")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["packages"], [])
        self.assertEqual(
            result["publishers"],
            [
                {
                    "key_id": self.key_id,
                    "public_key_base64": base64.b64encode(self.public).decode(),
                    "channels": ["preview", "stable"],
                    "revision": 2,
                    "revoked": True,
                }
            ],
        )
        self.assertNotIn("PRIVATE ACTOR", json.dumps(result))
        self.assertNotIn(str(self.base), json.dumps(result))
        self.assertEqual((await self.repo.publisher(self.key_id))["revision"], 2)

    async def test_status_core_facts_are_real_raw_release_digests_and_nullable(self):
        result = await self.call("status")
        self.assertEqual(
            result["core"],
            {
                "installed_version": None,
                "running_version": None,
                "installed_digest": None,
                "running_digest": None,
                "phase": None,
                "ready": True,
            },
        )
        self.generation.owner = SimpleNamespace(
            _installed_version="2.0.0",
            _running_version="1.0.0",
            _installed_digest="a" * 64,
            _running_digest="b" * 64,
            _state="fallback",
        )
        self.generation.bundle = SimpleNamespace(digest="c" * 64)
        result = await self.call("status")
        self.assertEqual(
            result["core"],
            {
                "installed_version": "2.0.0",
                "running_version": "1.0.0",
                "installed_digest": "a" * 64,
                "running_digest": "b" * 64,
                "phase": "fallback",
                "ready": True,
            },
        )
        self.generation.owner = SimpleNamespace(_state="cleanup_pending")
        result = await self.call("status")
        self.assertEqual(result["core"]["phase"], "cleanup_pending")
        self.assertIsNone(result["core"]["installed_digest"])
        self.assertIsNone(result["core"]["running_digest"])

    async def test_budget_inspection_diagnostic_preserves_active_runtime_without_false_fallback(
        self,
    ):
        digest = self.write_artifact()
        self.manager.packages[PACKAGE] = SimpleNamespace(
            bundle=SimpleNamespace(release_doc={"release": "1.2.3"}),
            receipt=SimpleNamespace(artifact_digest=digest),
            fallback=False,
            installed_version="1.2.3",
        )

        async def exhausted(*args, **kwargs):
            raise RuntimeBundleError("runtime_bundle_budget_exceeded")

        self.cache.capture = exhausted
        result = await self.call("status")
        self.assertTrue(result["ok"], result)
        item = result["packages"][0]
        self.assertEqual(item["state"], "active")
        self.assertEqual(item["running_version"], "1.2.3")
        self.assertEqual(item["running_digest"], digest)
        self.assertEqual(item["reason_code"], "runtime_bundle_budget_exceeded")
        self.assertIsNone(item["installed_digest"])

    async def test_status_trust_diagnostic_keeps_explicit_fallback_and_stable_reason(
        self,
    ):
        self.manager.packages[PACKAGE] = SimpleNamespace(
            bundle=SimpleNamespace(release_doc={"release": "1.0.0"}),
            receipt=SimpleNamespace(artifact_digest="f" * 64),
            fallback=True,
        )

        async def untrusted(*args, **kwargs):
            from ygl_test_subject.infrastructure.sqlite.repositories_extension_policy import (
                PolicyError,
            )

            raise PolicyError("publisher_untrusted")

        self.cache.capture = untrusted
        result = await self.call("status")
        self.assertEqual(result["packages"][0]["state"], "fallback")
        self.assertEqual(result["packages"][0]["reason_code"], "publisher_untrusted")

    async def test_distinct_installed_version_does_not_invent_fallback_during_transition(
        self,
    ):
        self.manager.packages[PACKAGE] = SimpleNamespace(
            bundle=SimpleNamespace(release_doc={"release": "1.0.0"}),
            receipt=SimpleNamespace(artifact_digest="f" * 64),
            fallback=False,
        )
        result = await self.inspect()
        self.assertEqual(result["state"], "active")
        self.assertNotEqual(result["installed_digest"], result["running_digest"])

    async def test_publisher_inventory_worker_and_commit_proofs_are_enforced(self):
        await self.repo.put_publisher(
            self.public,
            scope_policy={"channels": ["stable"]},
            actor_ref="admin",
            expected_revision=0,
        )
        grant = await self.grant(AdminOperation.LIST_MODULES)
        started, release = threading.Event(), threading.Event()

        def block(unit):
            started.set()
            if not release.wait(5):
                raise RuntimeError("fixture timeout")

        blocker = asyncio.create_task(self.db.executor.run_transaction(block))
        await asyncio.to_thread(started.wait, 3)
        pending = asyncio.create_task(self.controller._publishers(3, grant))
        await asyncio.sleep(0)
        self.source.close()
        release.set()
        await blocker
        with self.assertRaises(AdminAuthorizationDenied):
            await pending

    async def test_publisher_inventory_revocation_after_select_denies_commit(self):
        await self.repo.put_publisher(
            self.public,
            scope_policy={"channels": ["stable"]},
            actor_ref="admin",
            expected_revision=0,
        )
        grant = await self.grant(AdminOperation.LIST_MODULES)
        original = SQLiteUnitOfWork.execute

        def revoke_after_select(unit, sql, parameters=()):
            result = original(unit, sql, parameters)
            if (
                "SELECT key_id,public_key,scope_policy,revision,revoked FROM extension_publisher_trust"
                in sql
            ):
                self.source.close()
            return result

        with patch.object(SQLiteUnitOfWork, "execute", revoke_after_select):
            with self.assertRaises(AdminAuthorizationDenied):
                await self.controller._publishers(3, grant)

    async def test_plan_revision_conflict_and_queued_content_change_rejected(self):
        first = await self.inspect()
        second = await self.inspect()
        result = await self.call(
            "package-approve",
            package_id=PACKAGE,
            plan_id=first["plan_id"],
            expected_revision=0,
        )
        self.assertTrue(result["ok"], result)
        result = await self.call(
            "package-approve",
            package_id=PACKAGE,
            plan_id=second["plan_id"],
            expected_revision=0,
        )
        self.assertEqual(result["error"]["code"], "revision_conflict")
        current = await self.inspect()
        self.repo.before_mutation = lambda *_: self.write_artifact(changed=True)
        result = await self.call(
            "package-approve",
            package_id=PACKAGE,
            plan_id=current["plan_id"],
            expected_revision=1,
        )
        self.assertEqual(result["error"]["code"], "plan_changed")
        self.assertEqual((await self.repo.grant(PACKAGE))["revision"], 1)

    async def test_malformed_public_keys_and_channels_rejected(self):
        for key in [
            "!" * 44,
            base64.b64encode(b"short").decode(),
            base64.b64encode(b"x" * 33).decode(),
        ]:
            result = await self.call(
                "publisher-enroll",
                public_key_base64=key,
                channels=["stable"],
                expected_revision=0,
            )
            self.assertEqual(result["error"]["code"], "invalid_request")
        result = await self.call(
            "publisher-enroll",
            public_key_base64=base64.b64encode(self.public).decode(),
            channels=["arbitrary"],
            expected_revision=0,
        )
        self.assertEqual(result["error"]["code"], "invalid_request")

    async def test_source_revoked_after_capture_await_prevents_approval(self):
        plan = await self.inspect()
        original = self.manager.cache.capture

        async def capture(*args, **kwargs):
            result = await original(*args, **kwargs)
            self.source.close()
            return result

        self.manager.cache.capture = capture
        result = await self.call(
            "package-approve",
            package_id=PACKAGE,
            plan_id=plan["plan_id"],
            expected_revision=0,
        )
        self.assertEqual(result["error"]["code"], "admin_authorization_denied")
        self.assertIsNone(await self.repo.grant(PACKAGE))

    async def test_worker_time_source_revocation_after_enqueue_rolls_back(self):
        started, release = threading.Event(), threading.Event()

        def block(unit):
            started.set()
            if not release.wait(5):
                raise RuntimeError("test blocker timeout")

        blocker = asyncio.create_task(self.db.executor.run_transaction(block))
        await asyncio.to_thread(started.wait, 3)
        queued = asyncio.Event()
        self.repo.before_mutation = lambda *_: queued.set()
        grant = await self.grant()
        operation = asyncio.create_task(
            self.call(
                "publisher-enroll",
                grant=grant,
                public_key_base64=base64.b64encode(self.public).decode(),
                channels=["stable"],
                expected_revision=0,
            )
        )
        try:
            await asyncio.wait_for(queued.wait(), 3)
            await asyncio.sleep(0)
            self.source.close()
        finally:
            release.set()
        await blocker
        result = await operation
        self.assertEqual(result["error"]["code"], "admin_authorization_denied")
        self.assertIsNone(await self.repo.publisher(self.key_id))

    async def test_source_revocation_after_worker_check_fails_commit(self):
        grant = await self.grant()
        check = self.controller._worker_check(3, grant, AdminOperation.SET_ENABLED)

        def revoke_after_check(unit):
            check(unit)
            self.source.close()

        with self.assertRaises(AdminAuthorizationDenied):
            await self.repo.put_publisher(
                self.public,
                scope_policy={"channels": ["stable"]},
                actor_ref="verified-audit-only",
                expected_revision=0,
                admin_check=revoke_after_check,
            )
        self.assertIsNone(await self.repo.publisher(self.key_id))

    async def test_native_credential_revoked_between_capture_await(self):
        self.auth.context_validator = lambda *args: True
        await self.credential_repository.bootstrap(_digest("A" * 43))
        context = SimpleNamespace(
            adapter_id="native-fixture",
            request_id="verified-request",
            session_id="verified-session",
        )
        grant = await self.auth.authorize(
            AdminOperation.SET_ENABLED, invocation=None, context=context
        )
        plan = await self.inspect()
        original = self.manager.cache.capture

        async def capture(*args, **kwargs):
            result = await original(*args, **kwargs)
            await self.credential_repository.revoke(1)
            return result

        self.manager.cache.capture = capture
        result = await self.call(
            "package-approve",
            grant=grant,
            package_id=PACKAGE,
            plan_id=plan["plan_id"],
            expected_revision=0,
        )
        self.assertEqual(result["error"]["code"], "admin_authorization_denied")
        self.assertIsNone(await self.repo.grant(PACKAGE))

    async def test_retry_uses_fresh_request_ids_without_exposing_lease(self):
        requests = []

        async def request(instance, message):
            requests.append(message)
            return {
                "ok": True,
                "state": "queued",
                "operation_id": "synthetic-operation",
                "lease": "SECRET",
            }

        self.manager.endpoint.request = request
        for _ in range(2):
            reply = await self.call("retry", package_id=PACKAGE)
            self.assertEqual(reply["operation_id"], "synthetic-operation")
            self.assertNotIn("SECRET", json.dumps(reply))
        self.assertNotEqual(requests[0]["request_id"], requests[1]["request_id"])
