"""Operator provisioning tests using the real Core SQLite and secret stores."""

from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from secrets import token_urlsafe
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ygl_test_subject.adapters.astrbot.bundled import install_bundled_ff14
from ygl_test_subject.adapters.astrbot.runtime import PLUGIN_NAME, AstrBotRuntime
from ygl_test_subject.api.administration import AdminOperation
from ygl_test_subject.api.services import (
    ConfigFieldUpdate,
    ConfigPatch,
    ConfigPatchMode,
    ConfigTarget,
    SecretMaterial,
)
from ygl_test_subject.core.ports import RevisionConflict, SecretOwner
from ygl_test_subject.infrastructure.sqlite.repositories_admin_credentials import (
    AdminCredentialStatus,
)
from ygl_test_subject.scripts import admin_credentials
from ygl_test_subject.scripts import configure_source_credentials as cli
from ygl_test_subject.services.admin_authorization import _digest

ROOT = Path(__file__).resolve().parents[2]
_ADMIN = token_urlsafe(32)
_CLIENT_ID = "test-client-id-do-not-print"
_CLIENT_SECRET = "test-client-secret-do-not-print"


class SourceCredentialCliTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="ygl-source-credentials-", dir=ROOT
        )
        self.root = Path(self.temporary.name)
        self.data_dir = self.root / "data"
        self.data_dir.mkdir()
        self.plugin_root = self.root / "plugin"
        (self.plugin_root / "modules").mkdir(parents=True)
        shutil.copytree(
            ROOT / "modules" / "ff14",
            self.plugin_root / "modules" / "ff14",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        self.key = base64.b64encode(bytes(range(32))).decode("ascii")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def _seed_database(self) -> None:
        installation = install_bundled_ff14(self.plugin_root, self.data_dir)
        self.assertTrue(installation.trusted)
        policies = AstrBotRuntime._bundled_source_credential_policies(
            installation.extension_root
        )
        authority = cli._SessionAuthority()
        core, transport = cli._new_core(
            self.data_dir, installation.extension_root, authority, policies
        )
        try:
            await core.database.executor.initialize()
            await core.admin_credential_repository.bootstrap(_digest(_ADMIN))
            self.assertIs(await core.close(), True)
            self.assertFalse(transport.closed)
            await transport.close()
            self.assertTrue(transport.closed)
        finally:
            if not core.closed:
                await core.close()
            if not transport.closed:
                await transport.close()

    async def _read_config_state(self, alias: str) -> tuple[str, int]:
        installation = install_bundled_ff14(self.plugin_root, self.data_dir)
        policies = AstrBotRuntime._bundled_source_credential_policies(
            installation.extension_root
        )
        authority = cli._SessionAuthority()
        core, transport = cli._new_core(
            self.data_dir, installation.extension_root, authority, policies
        )
        try:
            await core.database.executor.initialize()
            core.extension_runtime.scan(installation.extension_root)
            failures = await core.admin_operations.recover_discovered_configuration()
            self.assertFalse(any(row.module_id == cli.MODULE_ID for row in failures))
            state = await core.admin_credential_repository.current()
            self.assertIs(state.status, AdminCredentialStatus.ACTIVE)
            session = authority.issue(state.generation)
            snapshot = await core.admin_facade.module_snapshot(
                None, cli.MODULE_ID, authorization=session
            )
            self.assertEqual(snapshot.status.lifecycle.value, "discovered")
            self.assertFalse(core._started)
            self.assertIsNone(core._pump_task)
            return snapshot.config.fields[alias], snapshot.config.revision
        finally:
            authority.revoke()
            self.assertIs(await core.close(), True)
            await transport.close()

    async def _read_config_status(self, alias: str) -> str:
        return (await self._read_config_state(alias))[0]

    async def _read_secret_payload(self, alias: str) -> bytes:
        installation = install_bundled_ff14(self.plugin_root, self.data_dir)
        policies = AstrBotRuntime._bundled_source_credential_policies(
            installation.extension_root
        )
        authority = cli._SessionAuthority()
        core, transport = cli._new_core(
            self.data_dir, installation.extension_root, authority, policies
        )
        try:
            await core.database.executor.initialize()
            snapshot = await core.config_repository.current(
                ConfigTarget(PLUGIN_NAME, cli.MODULE_ID)
            )
            metadata = next(
                item for item in snapshot.secret_metadata if item.field == alias
            )
            self.assertIsNotNone(metadata.secret_ref)
            ref = metadata.secret_ref
            return await core.secret_store.read(
                ref,
                owner=SecretOwner(PLUGIN_NAME, cli.MODULE_ID, alias, ref.operation_id),
            )
        finally:
            self.assertIs(await core.close(), True)
            await transport.close()

    def _secret_storage_snapshot(self) -> tuple[tuple[str, bytes], ...]:
        secret_root = self.data_dir / "secrets"
        if not secret_root.exists():
            return ()
        return tuple(
            sorted(
                (path.relative_to(secret_root).as_posix(), path.read_bytes())
                for path in secret_root.rglob("*")
                if path.is_file()
            )
        )

    def test_session_is_exact_generation_bound_limited_expiring_and_revocable(
        self,
    ) -> None:
        now = [100.0]
        authority = cli._SessionAuthority(clock=lambda: now[0])
        session = authority.issue(9)
        for operation in (AdminOperation.MODULE_SNAPSHOT, AdminOperation.UPDATE_CONFIG):
            self.assertTrue(authority.validate(operation, None, session, 9))
        self.assertFalse(
            authority.validate(AdminOperation.SET_ENABLED, None, session, 9)
        )
        self.assertFalse(
            authority.validate(AdminOperation.UPDATE_CONFIG, object(), session, 9)
        )
        self.assertFalse(
            authority.validate(AdminOperation.UPDATE_CONFIG, None, session, 10)
        )
        self.assertFalse(
            authority.validate(AdminOperation.UPDATE_CONFIG, None, replace(session), 9)
        )
        now[0] = session.expires_at
        self.assertFalse(
            authority.validate(AdminOperation.MODULE_SNAPSHOT, None, session, 9)
        )
        authority.revoke()
        self.assertFalse(
            authority.validate(AdminOperation.MODULE_SNAPSHOT, None, session, 9)
        )

    def test_arguments_have_no_secret_flags_and_error_does_not_echo_values(
        self,
    ) -> None:
        self.assertIn("--realm", cli._parser().format_help())
        self.assertNotIn("--client-secret", cli._parser().format_help())
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
            cli._parser().parse_args(
                [
                    "--data-dir",
                    str(self.data_dir),
                    "--realm",
                    "cn",
                    "set",
                    "--client-secret",
                    _CLIENT_SECRET,
                ]
            )
        self.assertIn("values are not accepted", stderr.getvalue())
        self.assertNotIn(_CLIENT_SECRET, stderr.getvalue())

    def test_client_fields_match_core_512_character_limit(self) -> None:
        cli._validate_client_material("i" * 512, "s" * 512)
        with self.assertRaisesRegex(ValueError, "client ID is invalid"):
            cli._validate_client_material("i" * 513, "s")
        with self.assertRaisesRegex(ValueError, "client secret is invalid"):
            cli._validate_client_material("i", "s" * 513)

    async def test_real_facade_set_reopen_clear_and_secret_redaction(self) -> None:
        await self._seed_database()
        with patch.dict(os.environ, {"YGL_SECRET_KEY": self.key}):
            prompts = iter((_ADMIN, _CLIENT_ID, _CLIENT_SECRET))
            labels: list[str] = []

            def hidden_prompt(label: str) -> str:
                labels.append(label)
                return next(prompts)

            updated = await cli._configure(
                self.data_dir,
                "set",
                "cn",
                plugin_root=self.plugin_root,
                prompt=hidden_prompt,
            )
            self.assertEqual(len(labels), 3)
            self.assertTrue(all("hidden" in label for label in labels))
            alias = "credential_fflogs_cn"
            self.assertEqual(updated.fields[alias], "configured")
            self.assertIn(alias, updated.sensitive_fields)
            self.assertEqual(await self._read_config_status(alias), "configured")
            stored_payload = await self._read_secret_payload(alias)
            self.assertEqual(
                json.loads(stored_payload),
                {
                    "schema": 1,
                    "client_id": _CLIENT_ID,
                    "client_secret": _CLIENT_SECRET,
                },
            )

            secret_files = tuple((self.data_dir / "secrets").rglob("*"))
            stored_files = tuple(path for path in secret_files if path.is_file())
            self.assertTrue(stored_files)
            for path in (self.data_dir / "runtime.sqlite3", *stored_files):
                self.assertNotIn(_CLIENT_SECRET.encode(), path.read_bytes())

            cleared = await cli._configure(
                self.data_dir,
                "clear",
                "cn",
                current_credential=_ADMIN,
                clear_confirmation="CLEAR",
                plugin_root=self.plugin_root,
            )
            self.assertGreater(cleared.revision, updated.revision)
            self.assertNotEqual(cleared.fields[alias], "configured")
            self.assertNotEqual(await self._read_config_status(alias), "configured")

    async def test_wrong_admin_and_missing_key_fail_without_config_change(self) -> None:
        await self._seed_database()
        with patch.dict(os.environ, {"YGL_SECRET_KEY": self.key}):
            before = await self._read_config_state("credential_fflogs_cn")
            with self.assertRaises(PermissionError):
                await cli._configure(
                    self.data_dir,
                    "set",
                    "cn",
                    current_credential="not-the-admin-credential",
                    client_id=_CLIENT_ID,
                    client_secret=_CLIENT_SECRET,
                    plugin_root=self.plugin_root,
                )
            for invalid_key in (None, base64.b64encode(b"short").decode("ascii")):
                environment = (
                    {} if invalid_key is None else {"YGL_SECRET_KEY": invalid_key}
                )
                with patch.dict(os.environ, environment, clear=True):
                    with self.assertRaises(Exception):
                        await cli._configure(
                            self.data_dir,
                            "set",
                            "cn",
                            current_credential=_ADMIN,
                            client_id=_CLIENT_ID,
                            client_secret=_CLIENT_SECRET,
                            plugin_root=self.plugin_root,
                        )
            self.assertEqual(
                await self._read_config_state("credential_fflogs_cn"), before
            )

    async def test_oversized_secret_fails_without_config_or_secret_store_change(
        self,
    ) -> None:
        await self._seed_database()
        alias = "credential_fflogs_cn"
        with patch.dict(os.environ, {"YGL_SECRET_KEY": self.key}):
            before_config = await self._read_config_state(alias)
            before_secrets = self._secret_storage_snapshot()
            with self.assertRaisesRegex(ValueError, "client secret is invalid"):
                await cli._configure(
                    self.data_dir,
                    "set",
                    "cn",
                    current_credential=_ADMIN,
                    client_id="i" * 512,
                    client_secret="s" * 513,
                    plugin_root=self.plugin_root,
                )
            self.assertEqual(await self._read_config_state(alias), before_config)
            self.assertEqual(self._secret_storage_snapshot(), before_secrets)

    async def test_real_admin_facade_rejects_stale_config_revision(self) -> None:
        await self._seed_database()
        with patch.dict(os.environ, {"YGL_SECRET_KEY": self.key}):
            await cli._configure(
                self.data_dir,
                "set",
                "cn",
                current_credential=_ADMIN,
                client_id=_CLIENT_ID,
                client_secret=_CLIENT_SECRET,
                plugin_root=self.plugin_root,
            )
            installation = install_bundled_ff14(self.plugin_root, self.data_dir)
            policies = AstrBotRuntime._bundled_source_credential_policies(
                installation.extension_root
            )
            authority = cli._SessionAuthority()
            core, transport = cli._new_core(
                self.data_dir, installation.extension_root, authority, policies
            )
            try:
                await core.database.executor.initialize()
                core.extension_runtime.scan(installation.extension_root)
                self.assertFalse(
                    await core.admin_operations.recover_discovered_configuration()
                )
                state = await core.admin_credential_repository.current()
                session = authority.issue(state.generation)
                before = await core.admin_facade.module_snapshot(
                    None, cli.MODULE_ID, authorization=session
                )
                candidate = core.extension_runtime.candidate("ff14")
                module = next(
                    item
                    for item in candidate.package.manifest.modules
                    if item.module_id == "ff14"
                )
                alias = "credential_fflogs_cn"
                clear = ConfigFieldUpdate(alias, ConfigPatchMode.CLEAR)
                cleared = await core.admin_facade.update_config(
                    None,
                    cli.MODULE_ID,
                    ConfigPatch(
                        before.config.revision,
                        (clear,),
                        declared_fields=tuple(module.config_fields),
                    ),
                    authorization=session,
                )
                with self.assertRaises(RevisionConflict):
                    await core.admin_facade.update_config(
                        None,
                        cli.MODULE_ID,
                        ConfigPatch(
                            before.config.revision,
                            (
                                ConfigFieldUpdate(
                                    alias,
                                    ConfigPatchMode.REPLACE,
                                    secret=SecretMaterial(b"stale replacement"),
                                ),
                            ),
                            declared_fields=tuple(module.config_fields),
                        ),
                        authorization=session,
                    )
                after = await core.admin_facade.module_snapshot(
                    None, cli.MODULE_ID, authorization=session
                )
                self.assertEqual(after.config.revision, cleared.revision)
                self.assertNotEqual(after.config.fields[alias], "configured")
            finally:
                authority.revoke()
                self.assertIs(await core.close(), True)
                await transport.close()

    def test_main_only_reports_safe_result(self) -> None:
        stdout = io.StringIO()
        with (
            patch.object(cli, "authorize_local_maintenance") as authorize,
            patch.object(
                cli,
                "_configure",
                new=AsyncMock(return_value=SimpleNamespace(revision=4)),
            ),
            contextlib.redirect_stdout(stdout),
        ):
            result = cli.main(
                ["--data-dir", str(self.data_dir), "--realm", "global", "set"]
            )
        self.assertEqual(result, 0)
        authorize.assert_called_once_with(self.data_dir / "runtime.sqlite3")
        self.assertIn("国际服", stdout.getvalue())
        self.assertNotIn(_CLIENT_ID, stdout.getvalue())
        self.assertNotIn(_CLIENT_SECRET, stdout.getvalue())

    async def test_platform_authorizer_remains_fail_closed_without_acl_proof(
        self,
    ) -> None:
        await self._seed_database()
        with patch.object(admin_credentials, "_POSIX", False):
            with self.assertRaises(
                admin_credentials.LocalMaintenanceAuthorizationError
            ):
                admin_credentials.authorize_local_maintenance(
                    self.data_dir / "runtime.sqlite3"
                )

    def test_module_help_runs_from_package_parent_with_local_sdk_bootstrap(
        self,
    ) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                f"{ROOT.name}.scripts.configure_source_credentials",
                "--help",
            ],
            cwd=ROOT.parent,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--data-dir", completed.stdout)
        self.assertIn("set", completed.stdout)
        self.assertNotIn("client_secret", completed.stdout)


if __name__ == "__main__":
    unittest.main()
