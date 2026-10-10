"""Non-game declaration, exact deployment policy and persistent-data retention."""

import asyncio
import json
import tempfile
import time
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

from ygl_test_subject.core.contracts.administration import (
    AdminAuthorizationDenied,
    AdminOperation,
)
from ygl_test_subject.core.contracts.services import ConfigFieldUpdate, ConfigPatch
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.ports import RevisionConflict
from ygl_test_subject.services.core_configuration import core_config_target
from ygl_test_subject.services.core_runtime import CoreRuntime
from ygl_test_subject.services.managed_source_credentials import (
    ManagedSourceCredentialPolicy,
)

from tests.fixtures.settings_module import write_settings_module
from tests.services.test_admin_operations import _Codec, _MessagePort, _Renderer
from yomihime_game_link_sdk.declarations import ConfigUpdateMode
from yomihime_game_link_sdk.display import DisplayLimits
from yomihime_game_link_sdk.services import ConfigTarget


class NeutralModuleSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        extensions = self.root / "extensions"
        self.owner = write_settings_module(extensions)
        manifest_path = extensions / "example/yomihime.manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["modules"][0]["config_fields"].append(
            {"name": "credential_demo", "sensitive": True, "group": "source"}
        )
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        write_settings_module(extensions, "other", "otherdemo")
        self.target = validate_contract(ConfigTarget("fixture", self.owner))
        self.core_arguments = dict(
            database=self.root / "runtime.sqlite3",
            extension_root=extensions,
            file_root=self.root / "files",
            secret_root=self.root / "secrets",
            secret_codec=_Codec(),
            http_transport=lambda request: request,
            renderer=_Renderer(),
            display_limits=validate_contract(DisplayLimits(2, 4096)),
            message_port=_MessagePort(),
            admin_context_validator=lambda *_: False,
            host_ingress_validator=lambda *_: False,
            config_principal_id="fixture",
            identity_namespace="fixture",
            pump_interval=3600,
            cleanup_timeout=1,
            ordinary_config_resources={
                core_config_target("fixture"): {"default_region"},
                self.target: {"label"},
            },
            managed_module_owners={self.owner},
            managed_source_credentials=(
                ManagedSourceCredentialPolicy(
                    self.owner, "credential_demo", "source", "Demo source"
                ),
            ),
        )
        await self._start_core()

    async def _start_core(self):
        self.core = CoreRuntime(**self.core_arguments)
        await self.core.start()
        self.ordinary = self.core.admin_authorization.register_source(
            "ordinary",
            resources=self.core.admin_operations.ordinary_resources(),
            operations={AdminOperation.READ_CONFIG, AdminOperation.UPDATE_CONFIG},
        )
        self.modules = self.core.admin_authorization.register_source(
            "modules",
            resources=self.core.admin_operations.module_resources(),
            operations={
                AdminOperation.LIST_MODULES,
                AdminOperation.SET_ENABLED,
                AdminOperation.UNLOAD_MODULE,
            },
        )
        self.credentials = self.core.admin_authorization.register_source(
            "credentials",
            resources=self.core.admin_operations.credential_resources(),
            operations={AdminOperation.READ_CONFIG, AdminOperation.UPDATE_CONFIG},
        )

    async def asyncTearDown(self):
        await self.core.close(timeout=1)
        self.temp.cleanup()

    def proof(self, source):
        request = object()
        return source.issue(
            subject="synthetic",
            request=request,
            expiry=time.time() + 30,
            operations=source.operations,
            resources=source.resources,
            live=lambda actual: actual is request,
        )

    async def _unload_candidate_and_restore(self):
        ops = self.core.admin_operations
        self.assertNotIn(self.owner, self.core.registry.snapshot().modules)
        ordinary = self.proof(self.ordinary)
        credentials = self.proof(self.credentials)
        modules = self.proof(self.modules)
        rows = await ops.ordinary_snapshot(authorization=ordinary)
        declarations = ops.ordinary_declarations(self.target)
        await ops.update_config(
            None,
            self.owner,
            ConfigPatch(
                rows[self.owner]["revision"],
                (
                    ConfigFieldUpdate(
                        "label", ConfigUpdateMode.REPLACE, value="retained"
                    ),
                ),
                declarations,
            ),
            authorization=ordinary,
        )
        status = await ops.credential_status(authorization=credentials)
        await ops.credential_update(
            {
                "module_id": self.owner,
                "expected_revision": status[self.owner]["revision"],
                "updates": [
                    {
                        "field": "credential_demo",
                        "mode": "replace",
                        "value": {
                            "client_id": "fixture-client",
                            "client_secret": "fixture-secret",
                        },
                    }
                ],
            },
            authorization=credentials,
        )
        before = await self.core.config_repository.current(self.target)
        marker = self.root / "persistent-business-data"
        marker.write_bytes(b"retain")
        capture = self.core.extension_runtime.factory_source.capture
        with patch.object(
            self.core.extension_runtime.factory_source,
            "capture",
            AsyncMock(wraps=capture),
        ) as captured:
            result = await ops.unload_module(
                None,
                self.owner,
                expected_registry_revision=self.core.registry.snapshot().revision,
                authorization=modules,
            )
            self.assertEqual(result.state, "unloaded")
            self.assertIn(self.owner, self.core.extension_runtime.unloaded_owners)
            captured.assert_not_awaited()
        revision = result.registry_revision
        repeated = await ops.unload_module(
            None,
            self.owner,
            expected_registry_revision=revision,
            authorization=modules,
        )
        self.assertEqual(repeated, result)
        self.assertNotIn(
            self.owner, await ops.ordinary_snapshot(authorization=ordinary)
        )
        catalog = await ops.ordinary_catalog(authorization=ordinary)
        self.assertFalse(
            any(row["module_id"] == self.owner for row in catalog["fields"])
        )
        self.assertEqual(
            await ops.credential_catalog(authorization=credentials),
            {"schema_version": 1, "fields": []},
        )
        self.assertEqual(await ops.credential_status(authorization=credentials), {})
        with self.assertRaises(AdminAuthorizationDenied):
            await ops.update_config(
                None,
                self.owner,
                ConfigPatch(
                    before.revision,
                    (
                        ConfigFieldUpdate(
                            "label", ConfigUpdateMode.REPLACE, value="late"
                        ),
                    ),
                    declarations,
                ),
                authorization=ordinary,
            )
        with self.assertRaises(AdminAuthorizationDenied):
            await ops.credential_update(
                {
                    "module_id": self.owner,
                    "expected_revision": before.revision,
                    "updates": [{"field": "credential_demo", "mode": "clear"}],
                },
                authorization=credentials,
            )
        after = await self.core.config_repository.current(self.target)
        self.assertEqual(after, before)
        self.assertEqual(marker.read_bytes(), b"retain")
        await ops.set_enabled(
            None,
            self.owner,
            True,
            expected_registry_revision=revision,
            authorization=modules,
        )
        self.assertTrue(self.core.registry.is_active(self.owner))
        self.assertNotIn(self.owner, self.core.extension_runtime.unloaded_owners)
        self.assertIn(self.owner, await ops.ordinary_snapshot(authorization=ordinary))
        restored = await ops.credential_status(authorization=credentials)
        self.assertEqual(
            restored[self.owner]["fields"]["credential_demo"], "configured"
        )
        self.assertEqual(
            (await self.core.config_repository.current(self.target)).values,
            before.values,
        )

    async def test_never_enabled_candidate_unload_revokes_management_and_restores(self):
        await self._unload_candidate_and_restore()

    async def test_false_intent_cold_start_candidate_unload_revokes_and_restores(self):
        ops = self.core.admin_operations
        context = self.proof(self.modules)
        await ops.set_enabled(
            None,
            self.owner,
            True,
            expected_registry_revision=self.core.registry.snapshot().revision,
            authorization=context,
        )
        await ops.set_enabled(
            None,
            self.owner,
            False,
            expected_registry_revision=self.core.registry.snapshot().revision,
            authorization=context,
        )
        await self.core.close(timeout=1)
        await self._start_core()
        intent = await self.core.extension_runtime.runtime_repository.current_intent(
            "example", "demo"
        )
        self.assertFalse(intent.desired_enabled)
        await self._unload_candidate_and_restore()

    async def test_candidate_unload_requires_authorization_and_registry_cas(self):
        ops = self.core.admin_operations
        revision = self.core.registry.snapshot().revision
        with self.assertRaises(AdminAuthorizationDenied):
            await ops.unload_module(
                None,
                self.owner,
                expected_registry_revision=revision,
                authorization=self.proof(self.ordinary),
            )
        with self.assertRaises(RevisionConflict):
            await ops.unload_module(
                None,
                self.owner,
                expected_registry_revision=revision + 1,
                authorization=self.proof(self.modules),
            )
        self.assertNotIn(self.owner, self.core.extension_runtime.unloaded_owners)

    async def test_status_queued_behind_unload_uses_only_remaining_resources(self):
        ops = self.core.admin_operations
        modules = self.proof(self.modules)
        credentials = self.proof(self.credentials)
        await ops.set_enabled(
            None,
            self.owner,
            True,
            expected_registry_revision=self.core.registry.snapshot().revision,
            authorization=modules,
        )
        cleanup_started, release_cleanup, status_queued = (
            asyncio.Event() for _ in range(3)
        )
        retire = self.core.extension_runtime.module_services.retire_module_credentials
        mutation = self.core.lifecycle.admission.mutation

        async def gated_cleanup(_services, *args, **kwargs):
            cleanup_started.set()
            await release_cleanup.wait()
            await retire(*args, **kwargs)

        @asynccontextmanager
        async def observed_mutation(reason):
            if reason == "admin-credential-status":
                status_queued.set()
            async with mutation(reason):
                yield

        with (
            patch.object(
                type(self.core.extension_runtime.module_services),
                "retire_module_credentials",
                gated_cleanup,
            ),
            patch.object(self.core.lifecycle.admission, "mutation", observed_mutation),
            patch.object(
                self.core.config_repository,
                "current",
                AsyncMock(wraps=self.core.config_repository.current),
            ) as reads,
        ):
            unload = asyncio.create_task(
                ops.unload_module(
                    None,
                    self.owner,
                    expected_registry_revision=self.core.registry.snapshot().revision,
                    authorization=modules,
                )
            )
            try:
                await asyncio.wait_for(cleanup_started.wait(), 2)
                status = asyncio.create_task(
                    ops.credential_status(authorization=credentials)
                )
                await asyncio.wait_for(status_queued.wait(), 2)
                self.assertFalse(status.done())
                release_cleanup.set()
                await asyncio.wait_for(unload, 2)
                self.assertEqual(await asyncio.wait_for(status, 2), {})
                self.assertFalse(
                    any(call.args[0] == self.target for call in reads.call_args_list)
                )
            finally:
                release_cleanup.set()
                await asyncio.gather(
                    unload,
                    *([status] if "status" in locals() else []),
                    return_exceptions=True,
                )

    async def test_loaded_and_disabled_credential_status_remains_manageable(self):
        ops = self.core.admin_operations
        modules = self.proof(self.modules)
        credentials = self.proof(self.credentials)
        for enabled in (True, False):
            await ops.set_enabled(
                None,
                self.owner,
                enabled,
                expected_registry_revision=self.core.registry.snapshot().revision,
                authorization=modules,
            )
            status = await ops.credential_status(authorization=credentials)
            self.assertEqual(status[self.owner]["fields"], {"credential_demo": "unset"})
        self.credentials.end(credentials)
        with self.assertRaises(AdminAuthorizationDenied):
            await ops.credential_status(authorization=credentials)

    async def _queued_write_across_unload(
        self, *, credential, restore=False, unload=True
    ):
        ops = self.core.admin_operations
        source = self.credentials if credential else self.ordinary
        context = self.proof(source)
        modules = self.proof(self.modules)
        before = await self.core.config_repository.current(self.target)
        selected, release = asyncio.Event(), asyncio.Event()
        configuration = ops._configuration

        async def delayed_configuration(selection):
            coordinator = await configuration(selection)
            selected.set()
            await release.wait()
            return coordinator

        async def write():
            if credential:
                return await ops.credential_update(
                    {
                        "module_id": self.owner,
                        "expected_revision": before.revision,
                        "updates": [{"field": "credential_demo", "mode": "clear"}],
                    },
                    authorization=context,
                )
            return await ops.update_config(
                None,
                self.owner,
                ConfigPatch(
                    before.revision,
                    (
                        ConfigFieldUpdate(
                            "label", ConfigUpdateMode.REPLACE, value="late"
                        ),
                    ),
                    ops.ordinary_declarations(self.target),
                ),
                authorization=context,
            )

        with patch.object(ops, "_configuration", delayed_configuration):
            task = asyncio.create_task(write())
            try:
                await asyncio.wait_for(selected.wait(), 2)
                if unload:
                    await ops.unload_module(
                        None,
                        self.owner,
                        expected_registry_revision=self.core.registry.snapshot().revision,
                        authorization=modules,
                    )
                if restore:
                    await ops.set_enabled(
                        None,
                        self.owner,
                        True,
                        expected_registry_revision=self.core.registry.snapshot().revision,
                        authorization=modules,
                    )
                release.set()
                if unload:
                    with self.assertRaises(AdminAuthorizationDenied):
                        await asyncio.wait_for(task, 2)
                    self.assertEqual(
                        await self.core.config_repository.current(self.target), before
                    )
                else:
                    await asyncio.wait_for(task, 2)
                    self.assertEqual(
                        (await self.core.config_repository.current(self.target)).values[
                            "label"
                        ],
                        "late",
                    )
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)

    async def test_queued_candidate_ordinary_write_cannot_commit_after_unload(self):
        await self._queued_write_across_unload(credential=False)

    async def test_queued_candidate_credential_write_cannot_commit_after_unload(self):
        await self._queued_write_across_unload(credential=True)

    async def test_old_candidate_write_cannot_commit_after_unload_and_restore(self):
        await self._queued_write_across_unload(credential=False, restore=True)

    async def test_old_candidate_credential_write_cannot_commit_after_restore(self):
        await self._queued_write_across_unload(credential=True, restore=True)

    async def test_candidate_write_remains_valid_across_first_registration(self):
        await self._queued_write_across_unload(
            credential=False, restore=True, unload=False
        )

    async def test_status_waiting_lock_rejects_revoked_authorization_without_read(self):
        ops = self.core.admin_operations
        context = self.proof(self.credentials)
        queued = asyncio.Event()
        mutation = self.core.lifecycle.admission.mutation

        @asynccontextmanager
        async def observed_mutation(reason):
            if reason == "admin-credential-status":
                queued.set()
            async with mutation(reason):
                yield

        lock = self.core.lifecycle.admission._mutation_lock
        await lock.acquire()
        with (
            patch.object(self.core.lifecycle.admission, "mutation", observed_mutation),
            patch.object(
                self.core.config_repository,
                "current",
                AsyncMock(wraps=self.core.config_repository.current),
            ) as reads,
        ):
            task = asyncio.create_task(ops.credential_status(authorization=context))
            try:
                await asyncio.wait_for(queued.wait(), 2)
                self.credentials.end(context)
                lock.release()
                with self.assertRaises(AdminAuthorizationDenied):
                    await asyncio.wait_for(task, 2)
                reads.assert_not_awaited()
            finally:
                if lock.locked():
                    lock.release()
                await asyncio.gather(task, return_exceptions=True)

    async def test_status_revalidates_authorization_after_metadata_read(self):
        ops = self.core.admin_operations
        context = self.proof(self.credentials)
        current = self.core.config_repository.current

        async def revoke_after_read(*args, **kwargs):
            snapshot = await current(*args, **kwargs)
            self.credentials.end(context)
            return snapshot

        with patch.object(self.core.config_repository, "current", revoke_after_read):
            with self.assertRaises(AdminAuthorizationDenied):
                await ops.credential_status(authorization=context)

    async def test_neutral_module_and_core_settings_survive_owner_unload_restore(self):
        ops = self.core.admin_operations
        context = self.proof(self.ordinary)
        catalog = await ops.ordinary_catalog(authorization=context)
        rows = await ops.ordinary_snapshot(authorization=context)
        self.assertEqual(set(rows), {"game_link/core", self.owner})
        other = next(f for f in catalog["fields"] if f["module_id"] == "other/demo")
        self.assertFalse(other["readable"])
        with self.assertRaises(AdminAuthorizationDenied):
            await ops.update_config(
                None,
                "other/demo",
                ConfigPatch(
                    1,
                    (
                        ConfigFieldUpdate(
                            "label", ConfigUpdateMode.REPLACE, value="unauthorized"
                        ),
                    ),
                    (),
                ),
                authorization=context,
            )
        await ops.update_config(
            None,
            self.owner,
            ConfigPatch(
                rows[self.owner]["revision"],
                (
                    ConfigFieldUpdate(
                        "label", ConfigUpdateMode.REPLACE, value="persisted"
                    ),
                ),
                ops.ordinary_declarations(self.target),
            ),
            authorization=context,
        )
        self.ordinary.end(context)
        context = self.proof(self.modules)
        with self.assertRaises(AdminAuthorizationDenied):
            await ops.ordinary_snapshot(authorization=context)
        await ops.set_enabled(
            None,
            self.owner,
            True,
            expected_registry_revision=self.core.registry.snapshot().revision,
            authorization=context,
        )
        saved = await self.core.config_repository.current(self.target)
        marker = self.root / "persistent-business-data"
        marker.write_bytes(b"retain")
        result = await ops.unload_module(
            None,
            self.owner,
            expected_registry_revision=self.core.registry.snapshot().revision,
            authorization=context,
        )
        self.assertEqual(result.state, "unloaded")
        self.assertNotIn(self.owner, self.core.registry.snapshot().modules)
        self.assertEqual(
            (await self.core.config_repository.current(self.target)).values,
            saved.values,
        )
        self.assertEqual(marker.read_bytes(), b"retain")
        await ops.set_enabled(
            None,
            self.owner,
            True,
            expected_registry_revision=self.core.registry.snapshot().revision,
            authorization=context,
        )
        self.assertTrue(self.core.registry.is_active(self.owner))
        self.assertEqual(
            self.core.registry.snapshot().module(self.owner).manifest.pages[0].route_id,
            "items",
        )
        self.modules.end(context)


class SharedPackageRestoreTests(unittest.IsolatedAsyncioTestCase):
    proof = NeutralModuleSettingsTests.proof
    asyncTearDown = NeutralModuleSettingsTests.asyncTearDown

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self.temp.name)
        extensions = self.root / "extensions"
        self.owner = write_settings_module(extensions)
        manifest_path = extensions / "example/yomihime.manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        sibling = json.loads(json.dumps(manifest["modules"][0]))
        sibling["module_id"] = "second"
        sibling["route"] = "second"
        sibling["pages"][0]["entry"] = "second.js"
        sibling["resources"][0]["path"] = "second.js"
        (extensions / "example/second.js").write_bytes(
            (extensions / "example/page.js").read_bytes()
        )
        manifest["modules"].append(sibling)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.target = validate_contract(ConfigTarget("fixture", self.owner))
        self.core = CoreRuntime(
            database=self.root / "runtime.sqlite3",
            extension_root=extensions,
            file_root=self.root / "files",
            secret_root=self.root / "secrets",
            secret_codec=_Codec(),
            http_transport=lambda request: request,
            renderer=_Renderer(),
            display_limits=validate_contract(DisplayLimits(2, 4096)),
            message_port=_MessagePort(),
            admin_context_validator=lambda *_: False,
            host_ingress_validator=lambda *_: False,
            config_principal_id="fixture",
            identity_namespace="fixture",
            pump_interval=3600,
            cleanup_timeout=1,
            ordinary_config_resources={
                core_config_target("fixture"): {"default_region"},
                self.target: {"label"},
                validate_contract(ConfigTarget("fixture", "example/second")): {"label"},
            },
            managed_module_owners={self.owner, "example/second"},
        )
        await self.core.start()
        self.modules = self.core.admin_authorization.register_source(
            "modules",
            resources=self.core.admin_operations.module_resources(),
            operations={
                AdminOperation.LIST_MODULES,
                AdminOperation.SET_ENABLED,
                AdminOperation.UNLOAD_MODULE,
            },
        )

    async def test_restore_one_owner_does_not_reregister_unloaded_sibling(self):
        ops = self.core.admin_operations
        context = self.proof(self.modules)

        async def enabled(owner):
            return await ops.set_enabled(
                None,
                owner,
                True,
                expected_registry_revision=self.core.registry.snapshot().revision,
                authorization=context,
            )

        async def unload(owner):
            return await ops.unload_module(
                None,
                owner,
                expected_registry_revision=self.core.registry.snapshot().revision,
                authorization=context,
            )

        await enabled(self.owner)
        await enabled("example/second")
        second_identity = self.core.lifecycle.current_identity("example/second")
        await unload(self.owner)
        self.assertTrue(self.core.registry.is_active("example/second"))
        self.assertIs(
            self.core.lifecycle.current_identity("example/second"), second_identity
        )
        self.assertIn("example", self.core.extension_runtime._built)
        await unload("example/second")
        self.assertEqual(dict(self.core.registry.snapshot().modules), {})
        self.assertNotIn("example", self.core.extension_runtime._built)
        await enabled(self.owner)
        self.assertNotIn("example/second", self.core.registry.snapshot().modules)
        self.modules.end(context)
