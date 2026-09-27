from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from secrets import token_urlsafe
from unittest.mock import patch

from ygl_test_subject.api.administration import AdminAuthorizationDenied
from ygl_test_subject.api.display import DisplayLimits, DisplayOutput
from ygl_test_subject.api.manifests import ConfigField
from ygl_test_subject.api.services import (
    ConfigFieldUpdate,
    ConfigPatch,
    ConfigPatchMode,
    ConfigTarget,
    SecretMaterial,
)
from ygl_test_subject.api.storage import SecretReceiptState, SecretTarget
from ygl_test_subject.core.ports import MessageReceipt, MessageStatus
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.services.admin_authorization import _digest
from ygl_test_subject.services.core_runtime import (
    CoreRuntime,
    CoreRuntimeCleanupPending,
)


class _Renderer:
    async def render(self, document, *, limits, audience):
        del limits, audience
        return DisplayOutput(document.title)

    async def render_batch(self, batch, limits):  # pragma: no cover - protocol only
        del batch, limits
        raise AssertionError("not used by administrative operations")


class _MessagePort:
    async def send(self, target, payload):
        del target, payload
        return MessageReceipt(MessageStatus.ACCEPTED, "message")


class _Codec:
    def encrypt(self, value: bytes) -> bytes:
        return b"admin-test:" + value[::-1]

    def decrypt(self, value: bytes) -> bytes:
        prefix = b"admin-test:"
        if not value.startswith(prefix):
            raise ValueError("invalid test envelope")
        return value[len(prefix) :][::-1]


class _AdminContext:
    adapter_id = "admin-adapter"
    request_id = "admin-request"
    session_id = "admin-session"


class AdminOperationsRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self.temp.name)
        self.runtime: CoreRuntime | None = None

    async def asyncTearDown(self) -> None:
        if self.runtime is not None and not self.runtime.closed:
            try:
                await self.runtime.close(timeout=0.5)
            except CoreRuntimeCleanupPending:
                pass
        self.temp.cleanup()

    def _runtime(self, *, context_validator=None) -> CoreRuntime:
        extension_root = self.root / "extensions"
        extension_root.mkdir(exist_ok=True)
        self.runtime = CoreRuntime(
            database=SQLiteDatabase(self.root / "core.sqlite3"),
            extension_root=extension_root,
            file_root=self.root / "files",
            secret_root=self.root / "secrets",
            secret_codec=_Codec(),
            http_transport=lambda request: request,
            renderer=_Renderer(),
            display_limits=DisplayLimits(2, 4096),
            message_port=_MessagePort(),
            admin_context_validator=context_validator or (lambda *_args: True),
            host_ingress_validator=lambda *_args: True,
            config_principal_id="host-config",
            identity_namespace="test-namespace",
            pump_interval=3600,
            cleanup_timeout=0.5,
        )
        return self.runtime

    async def _start_with_candidate(self, runtime: CoreRuntime) -> None:
        package = self.root / "extensions" / "admin"
        package.mkdir()
        (package / "yomihime.manifest.json").write_text(
            json.dumps(_config_manifest()), encoding="utf-8"
        )
        (package / "module.py").write_text(
            "raise AssertionError('static listing imported module source')\n",
            encoding="utf-8",
        )
        await runtime.start()

    async def _bootstrap(self, runtime: CoreRuntime) -> tuple[str, _AdminContext]:
        credential = token_urlsafe(32)
        await runtime.admin_credential_repository.bootstrap(_digest(credential))
        return credential, _AdminContext()

    async def test_real_facade_reads_catalog_and_updates_exact_config(self) -> None:
        runtime = self._runtime()
        await self._start_with_candidate(runtime)
        _credential, context = await self._bootstrap(runtime)

        listed = await runtime.admin_facade.list_modules(None, authorization=context)
        self.assertEqual(len(listed.modules), 1)
        self.assertEqual(listed.modules[0].status.module_id, "admin/mod")
        self.assertEqual(listed.modules[0].status.lifecycle.value, "discovered")
        self.assertEqual(
            runtime.extension_runtime.candidate("admin").package.manifest.package_id,
            "admin",
        )

        updated = await runtime.admin_facade.update_config(
            None,
            "admin/mod",
            ConfigPatch(
                1,
                (ConfigFieldUpdate("mode", ConfigPatchMode.REPLACE, value="safe"),),
                (ConfigField("mode", required=True, default="default"),),
            ),
            authorization=context,
        )
        self.assertEqual(updated.revision, 2)
        self.assertEqual(dict(updated.fields), {"mode": "configured"})
        persisted = await runtime.config_repository.current(
            ConfigTarget("host-config", "admin/mod")
        )
        self.assertEqual(persisted.values["mode"], "safe")
        self.assertTrue(await runtime.close(timeout=0.5))

    async def test_update_config_rechecks_generation_after_snapshot_await(self) -> None:
        runtime = self._runtime()
        await self._start_with_candidate(runtime)
        old_credential, context = await self._bootstrap(runtime)
        entered = asyncio.Event()
        release = asyncio.Event()
        snapshot = runtime.admin_operations._snapshot

        async def blocked_snapshot(*args, **kwargs):
            result = await snapshot(*args, **kwargs)
            entered.set()
            await release.wait()
            return result

        runtime.admin_operations._snapshot = blocked_snapshot
        update = asyncio.create_task(
            runtime.admin_facade.update_config(
                None,
                "admin/mod",
                ConfigPatch(
                    1,
                    (
                        ConfigFieldUpdate(
                            "mode", ConfigPatchMode.REPLACE, value="changed"
                        ),
                    ),
                    (ConfigField("mode", required=True, default="default"),),
                ),
                authorization=context,
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        new_credential = token_urlsafe(32)
        await runtime.admin_authorization.rotate(
            old_credential,
            new_credential,
            invocation=None,
            context=context,
        )
        release.set()
        with self.assertRaises(AdminAuthorizationDenied):
            await update
        current = await runtime.config_repository.current(
            ConfigTarget("host-config", "admin/mod")
        )
        self.assertEqual(current.revision, 2)
        self.assertEqual(current.values["mode"], "changed")
        self.assertTrue(await runtime.close(timeout=0.5))

    async def test_close_retains_worker_until_admin_validator_quiets(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def context_validator(*_args):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return True

        runtime = self._runtime(context_validator=context_validator)
        await self._start_with_candidate(runtime)
        await self._bootstrap(runtime)
        request = asyncio.create_task(
            runtime.admin_operations.list_modules(None, authorization=_AdminContext())
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        with self.assertRaises(CoreRuntimeCleanupPending) as caught:
            await runtime.close(timeout=0.02)
        self.assertEqual(caught.exception.component, "admin_operations")
        self.assertFalse(runtime.closed)
        state = await runtime.admin_credential_repository.current()
        self.assertEqual(state.generation, 1)

        release.set()
        with self.assertRaises(AdminAuthorizationDenied):
            await request
        self.assertTrue(await runtime.close(timeout=0.5))

    async def test_close_deadline_bounds_gate_wait_and_drains_config_commit(self):
        runtime = self._runtime()
        manifest = _config_manifest()
        manifest["modules"][0]["config_fields"].append(
            {"name": "token", "sensitive": True, "required": False}
        )
        package = self.root / "extensions" / "admin"
        package.mkdir()
        (package / "yomihime.manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        (package / "module.py").write_text(
            "raise AssertionError('disabled manifest imported source')\n",
            encoding="utf-8",
        )
        await runtime.start()
        _credential, context = await self._bootstrap(runtime)

        entered = asyncio.Event()
        release = asyncio.Event()
        repository_type = type(runtime.config_repository)
        update_authorized = repository_type.update_authorized

        async def committed_then_wait(repository, target, patch, grant):
            committed = await update_authorized(repository, target, patch, grant)
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return committed

        with patch.object(repository_type, "update_authorized", committed_then_wait):
            request = asyncio.create_task(
                runtime.admin_facade.update_config(
                    None,
                    "admin/mod",
                    ConfigPatch(
                        1,
                        (
                            ConfigFieldUpdate(
                                "token",
                                ConfigPatchMode.REPLACE,
                                secret=SecretMaterial(b"core-close-secret"),
                            ),
                        ),
                        (ConfigField("token", sensitive=True),),
                    ),
                    authorization=context,
                )
            )
            await asyncio.wait_for(entered.wait(), timeout=1)
            with self.assertRaises(CoreRuntimeCleanupPending) as caught:
                await runtime.close(timeout=0.02)
            self.assertEqual(caught.exception.component, "module_quiesce")
            self.assertFalse(runtime.closed)
            committed = await runtime.config_repository.current(
                ConfigTarget("host-config", "admin/mod")
            )
            self.assertEqual(committed.revision, 2)
            self.assertEqual(committed.secret_metadata[0].field, "token")
            self.assertEqual(
                (await runtime.admin_credential_repository.current()).generation,
                1,
            )

            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await request

        self.assertTrue(await runtime.close(timeout=0.5))

    async def test_revoke_denies_next_real_admin_operation(self) -> None:
        runtime = self._runtime()
        await runtime.start()
        credential, context = await self._bootstrap(runtime)
        listed = await runtime.admin_facade.list_modules(None, authorization=context)
        self.assertTrue(listed.is_empty)
        await runtime.admin_authorization.revoke(
            credential, invocation=None, context=context
        )
        with self.assertRaises(AdminAuthorizationDenied):
            await runtime.admin_operations.list_modules(None, authorization=context)
        self.assertTrue(await runtime.close(timeout=0.5))

    async def test_startup_recovers_secret_receipt_for_disabled_candidate(self) -> None:
        runtime = self._runtime()
        manifest = _config_manifest()
        fields = manifest["modules"][0]["config_fields"]
        fields.append({"name": "token", "sensitive": True, "required": False})
        package = self.root / "extensions" / "admin"
        package.mkdir()
        (package / "yomihime.manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        (package / "module.py").write_text(
            "raise AssertionError('disabled manifest imported source')\n",
            encoding="utf-8",
        )
        await runtime.database.executor.initialize()
        target = SecretTarget("host-config", "admin/mod", "token")
        staged = await runtime.secret_store.stage(
            b"orphaned-config-secret",
            target=target,
            operation_id="pending-config-secret",
            expected_config_revision=1,
        )
        self.assertEqual(staged.state, SecretReceiptState.STAGED)

        report = await runtime.start()
        self.assertEqual(report.config_failures, ())
        self.assertEqual(await runtime.secret_store.pending(target), ())
        self.assertEqual(
            runtime.extension_runtime.candidates()[0].state.value, "disabled"
        )
        self.assertTrue(await runtime.close(timeout=0.5))


def _config_manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "package_id": "admin",
        "package_version": "1.0.0",
        "contract_version": "1.1.0",
        "author": "tests",
        "license": "MIT",
        "source": "admin operation test package",
        "modules": [
            {
                "module_id": "mod",
                "route": "admin",
                "category": "platform",
                "factory_entry": "module:Factory",
                "module_version": "1.0.0",
                "capabilities": [
                    {
                        "capability_id": "read",
                        "input_schema": {
                            "type": "object",
                            "properties": {},
                            "required": [],
                        },
                        "invocation_policy": "natural_language_allowed",
                        "effect": "read_only",
                        "required_config": ["mode"],
                    }
                ],
                "commands": [
                    {
                        "operation_path": "read",
                        "capability_id": "read",
                        "parameter_mapping": {},
                        "help_text": "Read the package state",
                    }
                ],
                "config_fields": [
                    {
                        "name": "mode",
                        "sensitive": False,
                        "required": True,
                        "default": "default",
                    }
                ],
            }
        ],
    }


if __name__ == "__main__":
    unittest.main()
