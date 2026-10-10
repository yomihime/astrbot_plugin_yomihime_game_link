"""S3 receipts through real local Core components, without claiming global start."""

import asyncio
import copy
import json
import tempfile
import typing
import unittest
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.adapters.astrbot.admin_pages import AdminPages
from ygl_test_subject.adapters.astrbot.runtime import AstrBotRuntime
from ygl_test_subject.core.contracts import administration as admin
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.ports import Principal, RevisionConflict
from ygl_test_subject.services.configuration_migration import OrdinaryMigrationField
from ygl_test_subject.services.core_configuration import (
    DEFAULT_REGION,
    core_config_target,
)
from ygl_test_subject.services.core_runtime import CoreRuntime
from ygl_test_subject.services.managed_source_credentials import (
    ManagedSourceCredentialPolicy,
)

import yomihime_game_link_sdk as ygl
from tests.fixtures.settings_module import write_settings_module
from tests.services.test_admin_operations import _Codec, _MessagePort, _Renderer


class AdminReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        extensions = self.root / "extensions"
        self.owner = write_settings_module(extensions)
        manifest_path = extensions / "example/yomihime.manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf8"))
        manifest["modules"][0]["config_fields"].append(
            {"name": "credential_demo", "sensitive": True, "group": "source"}
        )
        manifest_path.write_text(json.dumps(manifest), encoding="utf8")
        write_settings_module(extensions, "other", "otherdemo")
        other_path = extensions / "other/yomihime.manifest.json"
        other = json.loads(other_path.read_text(encoding="utf8"))
        other_path.write_text(json.dumps(other), encoding="utf8")
        self.target = validate_contract(ygl.ConfigTarget("fixture", self.owner))
        self.core_target = core_config_target("fixture")
        label = validate_contract(ygl.ConfigField("label", default="before"))
        self.core = CoreRuntime(
            database=self.root / "runtime.sqlite3",
            extension_root=extensions,
            file_root=self.root / "files",
            secret_root=self.root / "secrets",
            secret_codec=_Codec(),
            http_transport=lambda request: request,
            renderer=_Renderer(),
            display_limits=validate_contract(ygl.DisplayLimits(2, 4096)),
            message_port=_MessagePort(),
            admin_context_validator=lambda *_: False,
            host_ingress_validator=lambda *_: False,
            config_principal_id="fixture",
            identity_namespace="fixture",
            cleanup_timeout=1,
            ordinary_config_resources={
                self.core_target: {"default_region"},
                self.target: {"label"},
            },
            managed_module_owners={self.owner},
            managed_source_credentials=(
                ManagedSourceCredentialPolicy(
                    self.owner, "credential_demo", "source", "Demo source"
                ),
            ),
            ordinary_migration_fields=(
                OrdinaryMigrationField(
                    self.core_target, DEFAULT_REGION, "legacy_region"
                ),
                OrdinaryMigrationField(self.target, label, "legacy_label"),
            ),
            ordinary_migration_id="s3-fixture",
        )
        # Legitimate component composition: no CoreRuntime.start, fake started
        # flag, admission override or authorization bypass.
        await self.core.database.executor.initialize()
        await self.core.repositories.identities.save_principal(
            Principal("alice", "fixture", "alice")
        )
        self.core.extension_runtime.scan(extensions)
        self.ops = self.core.admin_operations
        self.modules = self.core.admin_authorization.register_source(
            "s3-modules",
            resources=self.ops.module_resources(),
            operations={
                admin.AdminOperation.LIST_MODULES,
                admin.AdminOperation.MODULE_SNAPSHOT,
                admin.AdminOperation.SET_ENABLED,
                admin.AdminOperation.UNLOAD_MODULE,
            },
        )
        self.ordinary = self.core.admin_authorization.register_source(
            "s3-ordinary",
            resources=self.ops.ordinary_resources(),
            operations={
                admin.AdminOperation.READ_CONFIG,
                admin.AdminOperation.UPDATE_CONFIG,
                admin.AdminOperation.ROLLBACK_CONFIG,
            },
        )
        self.credentials = self.core.admin_authorization.register_source(
            "s3-credentials",
            resources=self.ops.credential_resources(),
            operations={
                admin.AdminOperation.READ_CONFIG,
                admin.AdminOperation.UPDATE_CONFIG,
            },
        )
        self.pages = AdminPages(None, None)

    async def asyncTearDown(self):
        await self.core.close(timeout=1)
        self.temp.cleanup()

    def proof(self, source):
        raw = object()
        return source.issue(
            subject="synthetic-admin",
            request=raw,
            expiry=__import__("time").time() + 60,
            operations=source.operations,
            resources=source.resources,
            live=lambda item: item is raw,
        )

    async def enabled(self):
        return await self.ops.set_enabled(
            None,
            self.owner,
            True,
            expected_registry_revision=self.core.registry.snapshot().revision,
            authorization=self.proof(self.modules),
        )

    async def unload(self, context=None):
        return await self.ops.unload_module(
            None,
            self.owner,
            expected_registry_revision=self.core.registry.snapshot().revision,
            authorization=context or self.proof(self.modules),
        )

    async def test_unload_receipt_actual_receiver_and_five_field_host_projection(self):
        status = await self.enabled()
        view = self.core.issuer.issue(
            origin=ygl.InvocationOrigin.COMMAND,
            module_id=self.owner,
            module_epoch=status.epoch,
            registry_revision=status.registry_revision,
            capability_id="inspect",
            actor_id="alice",
            conversation_id="room",
            adapter_id="fixture",
        )
        self.core.lifecycle.admission.admit(view, "inspect")
        bound = await self.core.module_services.for_module(self.owner).scopes.bind(view)
        saved = await self.core.config_repository.current(self.target)
        marker = self.root / "business-data"
        marker.write_bytes(b"retained-business-bytes")
        receipt = await self.unload()
        self.assertEqual(type(receipt).__name__, "ModuleUnloadReceipt")
        self.assertEqual(receipt.module_id, self.owner)
        self.assertEqual(receipt.state, "unloaded")
        self.assertEqual(
            receipt.registry_revision, self.core.registry.snapshot().revision
        )
        self.assertIs(receipt.data_retained, True)
        self.assertIs(receipt.reopen_required, True)
        projected = await self.pages._invoke(
            "module-unload",
            self.core,
            {
                "module_id": self.owner,
                "expected_registry_revision": receipt.registry_revision,
            },
            self.proof(self.modules),
        )
        self.assertEqual(
            projected,
            dict(
                module_id=self.owner,
                state="unloaded",
                registry_revision=receipt.registry_revision,
                data_retained=True,
                reopen_required=True,
            ),
        )
        self.assertEqual(
            (await self.core.config_repository.current(self.target)).values,
            saved.values,
        )
        self.assertEqual(marker.read_bytes(), b"retained-business-bytes")
        with self.assertRaises(ygl.InvalidInvocation):
            await bound.cache.get("old")
        restored = await self.enabled()
        self.assertGreater(restored.epoch, status.epoch)
        with self.assertRaises(ygl.InvalidInvocation):
            await bound.cache.get("old")

    async def test_unload_denial_conflict_and_failure_after_disable(self):
        status = await self.enabled()
        original = self.core.registry.snapshot()
        for proof in (None, copy.copy(self.proof(self.modules))):
            with self.subTest(proof="absent" if proof is None else "copied"):
                with self.assertRaises(admin.AdminAuthorizationDenied):
                    await self.ops.unload_module(
                        None,
                        self.owner,
                        expected_registry_revision=original.revision,
                        authorization=proof,
                    )
                self.assertIs(self.core.registry.snapshot(), original)
        stale = self.proof(self.modules)
        with self.assertRaises(RevisionConflict):
            await self.ops.unload_module(
                None,
                self.owner,
                expected_registry_revision=original.revision - 1,
                authorization=stale,
            )
        self.assertTrue(self.core.registry.is_active(self.owner))
        context = self.proof(self.modules)
        original_detach = self.core.extension_runtime.prepare_detach

        async def revoke_after_disable(owner):
            self.assertFalse(self.core.registry.is_active(owner))
            await original_detach(owner)
            self.modules.end(context)

        with patch.object(
            self.core.extension_runtime, "prepare_detach", revoke_after_disable
        ):
            with self.assertRaises(admin.AdminAuthorizationDenied):
                await self.unload(context)
        self.assertFalse(self.core.registry.is_active(self.owner))
        self.assertNotIn(self.owner, self.core.extension_runtime.unloaded_owners)
        snapshot = next(
            row
            for row in await self.ops.list_modules(
                None, authorization=self.proof(self.modules)
            )
            if row.status.module_id == self.owner
        )
        self.assertFalse(snapshot.status.enabled)
        # Reconcile the incomplete detach under a fresh authorized request,
        # rather than assuming an unknown previous operation was rolled back.
        await self.unload()
        await self.enabled()
        cancel = asyncio.CancelledError("synthetic cancellation")

        async def cancelled(owner):
            self.assertFalse(self.core.registry.is_active(owner))
            raise cancel

        with patch.object(self.core.extension_runtime, "prepare_detach", cancelled):
            with self.assertRaises(asyncio.CancelledError) as caught:
                await self.unload()
        self.assertIs(caught.exception, cancel)
        self.assertFalse(self.core.registry.is_active(self.owner))
        await self.enabled()
        self.assertGreater(self.core.lifecycle.state(self.owner).epoch, status.epoch)

    async def test_bounded_rollback_receipt_and_commit_uncertainty(self):
        migration = self.core.ordinary_config_migration
        await migration.migrate({"legacy_region": "global", "legacy_label": "migrated"})
        targets = (self.core_target, self.target)
        revisions = {
            target: (await self.core.config_repository.current(target)).revision
            for target in targets
        }
        before = {
            target: await self.core.config_repository.current(target)
            for target in targets
        }
        for invalid in (
            {**revisions, self.target: revisions[self.target] - 1},
            {**revisions, ygl.ConfigTarget("fixture", "foreign/module"): 1},
        ):
            with self.assertRaises((RevisionConflict, ValueError)):
                await self.ops.ordinary_rollback(
                    invalid, authorization=self.proof(self.ordinary)
                )
            self.assertEqual(
                {
                    target: await self.core.config_repository.current(target)
                    for target in targets
                },
                before,
            )
        receipt = await self.ops.ordinary_rollback(
            revisions, authorization=self.proof(self.ordinary)
        )
        self.assertEqual(type(receipt).__name__, "OrdinaryRollbackReceipt")
        self.assertIs(receipt.rolled_back, True)
        await migration.migrate({"legacy_region": "global", "legacy_label": "migrated"})
        revisions = {
            target: (await self.core.config_repository.current(target)).revision
            for target in targets
        }
        projected = await self.pages._invoke(
            "rollback",
            self.core,
            {"expected_revisions": {t.module_id: r for t, r in revisions.items()}},
            self.proof(self.ordinary),
        )
        self.assertEqual(projected, {"rolled_back": True})
        # The real transaction may commit before late source-current rejection.
        await migration.migrate({"legacy_region": "global", "legacy_label": "migrated"})
        revisions = {
            target: (await self.core.config_repository.current(target)).revision
            for target in targets
        }
        context = self.proof(self.ordinary)
        original = migration.rollback

        async def committed_then_revoke(expected, *, grant):
            await original(expected, grant=grant)
            self.ordinary.end(context)

        with patch.object(migration, "rollback", committed_then_revoke):
            with self.assertRaises(admin.AdminAuthorizationDenied):
                await self.ops.ordinary_rollback(revisions, authorization=context)
        self.assertFalse(await migration.complete())

    async def test_recovery_projection_declared_shape_and_host_safe_failures(self):
        expected = getattr(admin, "ManagementRecoveryProjection", None)
        self.assertIsNotNone(expected)
        self.assertEqual(
            typing.get_type_hints(expected), {"recovered": typing.Literal[True]}
        )
        for method in (
            CoreRuntime.recover_management,
            AstrBotRuntime.recover_management,
        ):
            self.assertIs(typing.get_type_hints(method)["return"], expected)
        with self.assertRaises(admin.AdminAuthorizationDenied):
            await self.core.recover_management(
                {}, authorization=self.proof(self.ordinary)
            )
        self.assertFalse(self.core.started)
        self.assertFalse(self.core.registry.snapshot().modules)

    async def test_receipts_reject_noncanonical_shape_and_host_extras(self):
        class Text(str):
            pass

        for args in (
            (Text(self.owner), "unloaded", 0, True, True),
            (self.owner, Text("unloaded"), 0, True, True),
            (self.owner, "active", 0, True, True),
            (self.owner, "unloaded", True, True, True),
            (self.owner, "unloaded", -1, True, True),
            (self.owner, "unloaded", 0, 1, True),
        ):
            with self.subTest(args=args):
                with self.assertRaises((TypeError, ValueError)):
                    admin.ModuleUnloadReceipt(*args)
        unknown = admin.ModuleUnloadReceipt(self.owner, None, None, None, None)
        self.assertIsNone(unknown.state)
        for value in (None, 1, "true"):
            with self.assertRaises(TypeError):
                admin.OrdinaryRollbackReceipt(value)

        class Extra(admin.ModuleUnloadReceipt):
            pass

        extra = Extra(self.owner, "unloaded", 0, True, True)
        object.__setattr__(extra, "unrelated", "synthetic-private-extra")
        with patch.object(self.ops, "unload_module", return_value=extra):
            with self.assertRaises(TypeError):
                await self.pages._invoke(
                    "module-unload",
                    self.core,
                    {"module_id": self.owner, "expected_registry_revision": 0},
                    self.proof(self.modules),
                )

        class RollbackExtra(admin.OrdinaryRollbackReceipt):
            pass

        with patch.object(
            self.ops, "ordinary_rollback", return_value=RollbackExtra(True)
        ):
            with self.assertRaises(TypeError):
                await self.pages._invoke(
                    "rollback",
                    self.core,
                    {"expected_revisions": {}},
                    self.proof(self.ordinary),
                )
