"""Operator provisioning tests using the real Core SQLite and secret stores."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import getpass
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
import warnings
from dataclasses import replace
from pathlib import Path
from secrets import token_urlsafe
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ygl_test_subject.adapters.astrbot.bundled import install_bundled_ff14
from ygl_test_subject.adapters.astrbot.runtime import PLUGIN_NAME
from ygl_test_subject.core.contracts.administration import AdminOperation
from ygl_test_subject.core.contracts.services import (
    ConfigFieldUpdate,
    ConfigPatch,
    SecretMaterial,
)
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.ports import RevisionConflict, SecretOwner
from ygl_test_subject.infrastructure.sqlite.repositories_admin_credentials import (
    AdminCredentialStatus,
)
from ygl_test_subject.scripts import admin_credentials
from ygl_test_subject.scripts import configure_source_credentials as cli
from ygl_test_subject.services.admin_authorization import _digest

from tests.host.assembly_contract import selected_assembly
from yomihime_game_link_sdk.declarations import ConfigUpdateMode
from yomihime_game_link_sdk.services import ConfigTarget

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
        # These are Core/configuration tests, not OS qualification. Their
        # shared-workspace fixtures deliberately lack private Windows ACLs.
        self.guard_patch = patch.object(
            cli, "local_maintenance_guard", return_value=contextlib.nullcontext()
        )
        self.guard_patch.start()
        self.addCleanup(self.guard_patch.stop)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def _seed_database(self) -> None:
        installation = install_bundled_ff14(self.plugin_root, self.data_dir)
        self.assertTrue(installation.trusted)
        assembly = selected_assembly(self.plugin_root, PLUGIN_NAME)
        authority = cli._SessionAuthority()
        core, transport = cli._new_core(
            self.data_dir,
            installation.extension_root,
            authority,
            assembly,
            installation.inventory,
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
        assembly = selected_assembly(self.plugin_root, PLUGIN_NAME)
        authority = cli._SessionAuthority()
        core, transport = cli._new_core(
            self.data_dir,
            installation.extension_root,
            authority,
            assembly,
            installation.inventory,
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
        assembly = selected_assembly(self.plugin_root, PLUGIN_NAME)
        authority = cli._SessionAuthority()
        core, transport = cli._new_core(
            self.data_dir,
            installation.extension_root,
            authority,
            assembly,
            installation.inventory,
        )
        try:
            await core.database.executor.initialize()
            snapshot = await core.config_repository.current(
                validate_contract(ConfigTarget(PLUGIN_NAME, cli.MODULE_ID))
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

    async def test_real_set_rotation_removes_old_ciphertext_and_clear_removes_new(self):
        await self._seed_database()
        alias = "credential_fflogs_global"
        with patch.dict(os.environ, {"YGL_SECRET_KEY": self.key}):
            first = await cli._configure(
                self.data_dir,
                "set",
                "global",
                current_credential=_ADMIN,
                client_id=_CLIENT_ID,
                client_secret=_CLIENT_SECRET,
                plugin_root=self.plugin_root,
            )
            old_paths = {name for name, _ in self._secret_storage_snapshot()}
            self.assertTrue(old_paths)
            rotated = await cli._configure(
                self.data_dir,
                "set",
                "global",
                current_credential=_ADMIN,
                client_id="synthetic-rotated-id",
                client_secret="synthetic-rotated-secret",
                plugin_root=self.plugin_root,
            )
            new_paths = {name for name, _ in self._secret_storage_snapshot()}
            self.assertGreater(rotated.revision, first.revision)
            self.assertTrue(new_paths)
            self.assertTrue(old_paths.isdisjoint(new_paths))
            self.assertEqual(
                json.loads(await self._read_secret_payload(alias))["client_secret"],
                "synthetic-rotated-secret",
            )
            await cli._configure(
                self.data_dir,
                "clear",
                "global",
                current_credential=_ADMIN,
                clear_confirmation="CLEAR",
                plugin_root=self.plugin_root,
            )
            self.assertEqual(self._secret_storage_snapshot(), ())

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

    async def test_guard_rejects_before_install_or_core_io(self) -> None:
        with (
            patch.object(
                cli,
                "local_maintenance_guard",
                side_effect=PermissionError("OS proof rejected"),
            ),
            patch.object(cli, "install_bundled_ff14") as install,
            patch.object(cli, "_new_core") as factory,
            self.assertRaises(PermissionError),
        ):
            await cli._configure(
                self.data_dir, "set", "cn", plugin_root=self.plugin_root
            )
        install.assert_not_called()
        factory.assert_not_called()

    async def test_guard_stays_held_through_cancelled_core_and_transport_close(
        self,
    ) -> None:
        await self._seed_database()
        events = []

        @contextlib.contextmanager
        def held(*_args, **_kwargs):
            events.append("guard_enter")
            try:
                yield
            finally:
                events.append("guard_exit")

        class Core:
            close_attempts = 0
            closed = False
            database = SimpleNamespace(
                executor=SimpleNamespace(
                    initialize=AsyncMock(side_effect=asyncio.CancelledError())
                )
            )

            async def close(self):
                self.close_attempts += 1
                if self.close_attempts == 1:
                    events.append("core_pending")
                    raise cli.CoreRuntimeCleanupPending("sqlite_executor")
                events.append("core_close")
                self.closed = True
                return True

        class Transport:
            closed = False

            async def close(self):
                events.append("transport_close")
                self.closed = True

        with (
            patch.object(cli, "local_maintenance_guard", held),
            patch.object(cli, "_new_core", return_value=(Core(), Transport())),
            self.assertRaises(asyncio.CancelledError),
        ):
            await cli._configure(
                self.data_dir, "clear", "cn", plugin_root=self.plugin_root
            )
        self.assertEqual(
            events,
            [
                "guard_enter",
                "core_pending",
                "core_close",
                "transport_close",
                "guard_exit",
            ],
        )

    def test_hidden_input_warning_never_falls_back_to_echo(self) -> None:
        fallback = []

        def unsafe_prompt(_label):
            warnings.warn("synthetic unavailable console", getpass.GetPassWarning)
            fallback.append(True)
            return "would-have-been-echoed"

        with self.assertRaises(getpass.GetPassWarning):
            admin_credentials._read_hidden("hidden: ", prompt=unsafe_prompt)
        self.assertEqual(fallback, [])

    async def test_admin_guard_precedes_repository_and_survives_cancelled_action(self):
        events = []

        @contextlib.contextmanager
        def held(*_args, **_kwargs):
            events.append("guard_enter")
            try:
                yield
            finally:
                events.append("guard_exit")

        class Executor:
            state = "CLOSING"

            async def close(self):
                events.append("repository_close")
                self.state = "CLOSED"

        class Repository:
            def __init__(self, _database):
                events.append("repository_open")
                self.database = SimpleNamespace(executor=Executor())

            async def bootstrap(self, _digest):
                raise asyncio.CancelledError()

        with (
            patch.object(admin_credentials, "local_maintenance_guard", held),
            patch.object(
                admin_credentials, "SQLiteAdminCredentialRepository", Repository
            ),
            patch.object(admin_credentials, "_read", return_value=_ADMIN),
            self.assertRaises(asyncio.CancelledError),
        ):
            await admin_credentials._run(self.data_dir / "runtime.sqlite3", "bootstrap")
        self.assertEqual(
            events,
            ["guard_enter", "repository_open", "repository_close", "guard_exit"],
        )
        with (
            patch.object(
                admin_credentials,
                "local_maintenance_guard",
                side_effect=PermissionError("OS proof rejected"),
            ),
            patch.object(
                admin_credentials, "SQLiteAdminCredentialRepository"
            ) as factory,
            self.assertRaises(PermissionError),
        ):
            await admin_credentials._run(self.data_dir / "runtime.sqlite3", "bootstrap")
        factory.assert_not_called()

    def test_loaded_code_root_cannot_be_substituted(self) -> None:
        with self.assertRaises(admin_credentials.LocalMaintenanceAuthorizationError):
            with admin_credentials.local_maintenance_guard(
                self.data_dir / "runtime.sqlite3", plugin_root=self.plugin_root
            ):
                self.fail("an arbitrary code root authorized maintenance")

    async def test_admin_pending_close_and_repeated_cancel_keep_guard_until_drained(
        self,
    ):
        events = []
        entered = asyncio.Event()
        release = asyncio.Event()

        @contextlib.contextmanager
        def held(*_args, **_kwargs):
            events.append("guard_enter")
            try:
                yield
            finally:
                events.append("guard_exit")

        class Executor:
            attempts = 0
            state = "CLOSING"

            async def close(self):
                self.attempts += 1
                if self.attempts == 1:
                    events.append("sqlite_pending")
                    raise admin_credentials.SQLiteExecutorCloseTimeout(
                        "synthetic pending"
                    )
                entered.set()
                await release.wait()
                events.append("repository_close")
                self.state = "CLOSED"

        class Repository:
            def __init__(self, _database):
                self.database = SimpleNamespace(executor=Executor())

            async def bootstrap(self, _digest):
                raise asyncio.CancelledError()

        with (
            patch.object(admin_credentials, "local_maintenance_guard", held),
            patch.object(
                admin_credentials, "SQLiteAdminCredentialRepository", Repository
            ),
            patch.object(admin_credentials, "_read", return_value=_ADMIN),
        ):
            operation = asyncio.create_task(
                admin_credentials._run(self.data_dir / "runtime.sqlite3", "bootstrap")
            )
            await asyncio.wait_for(entered.wait(), 2)
            for _ in range(2):
                operation.cancel()
                await asyncio.sleep(0)
                self.assertFalse(operation.done())
                self.assertNotIn("guard_exit", events)
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await operation
        self.assertEqual(
            events, ["guard_enter", "sqlite_pending", "repository_close", "guard_exit"]
        )

    async def test_unexpected_cleanup_failure_is_not_retried_or_hidden(self):
        # A generic resource can independently report a completed close after
        # an earlier error. This is not a failed transport.close recovery.
        for failure_in in ("close",):
            with self.subTest(failure_in=failure_in):
                entered = asyncio.Event()
                closed = [False]
                guard_held = []
                error = ValueError("synthetic cleanup error")
                close = AsyncMock(side_effect=error if failure_in == "close" else None)

                async def after_close():
                    entered.set()
                    if failure_in == "after":
                        raise error

                after = AsyncMock(side_effect=after_close)
                stderr = io.StringIO()

                async def operation():
                    with contextlib.ExitStack() as stack:
                        guard_held.append(True)
                        stack.callback(guard_held.clear)
                        await admin_credentials._finish_cleanup(
                            close,
                            confirmed_closed=lambda: closed[0],
                            pending=(admin_credentials.SQLiteExecutorCloseTimeout,),
                            after=after,
                        )

                with contextlib.redirect_stderr(stderr):
                    task = asyncio.create_task(operation())
                    await asyncio.wait_for(entered.wait(), 2)
                    await asyncio.sleep(0.1)
                    self.assertFalse(task.done())
                    self.assertEqual(guard_held, [True])
                    closed[0] = True
                    with self.assertRaises(ValueError) as failure:
                        await task
                    self.assertIs(failure.exception, error)
                self.assertEqual(guard_held, [])
                close.assert_awaited_once()
                after.assert_awaited_once()
                self.assertEqual(stderr.getvalue().count("cleanup is unconfirmed"), 1)
                self.assertNotIn("synthetic cleanup error", stderr.getvalue())

    async def test_cleanup_own_cancellation_and_proof_failures_hold_until_confirmed(
        self,
    ):
        for source, error in (
            ("close", asyncio.CancelledError("synthetic close cancellation")),
            ("after", asyncio.CancelledError("synthetic after cancellation")),
            ("proof", RuntimeError("synthetic proof failure")),
            ("proof", asyncio.CancelledError("synthetic proof cancellation")),
        ):
            with self.subTest(source=source, error=type(error).__name__):
                entered = asyncio.Event()
                actual_closed = [False]
                guard_held = []
                proof_calls = []

                async def close_resource():
                    if source == "close":
                        raise error

                async def after_resource():
                    entered.set()
                    if source == "after":
                        raise error

                def proof():
                    proof_calls.append(True)
                    if source == "proof" and not actual_closed[0]:
                        raise error
                    return actual_closed[0]

                close, after = (
                    AsyncMock(side_effect=close_resource),
                    AsyncMock(side_effect=after_resource),
                )

                async def operation():
                    with contextlib.ExitStack() as stack:
                        guard_held.append(True)
                        stack.callback(guard_held.clear)
                        await admin_credentials._finish_cleanup(
                            close, confirmed_closed=proof, after=after
                        )

                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    task = asyncio.create_task(operation())
                    await asyncio.wait_for(entered.wait(), 2)
                    await asyncio.sleep(0.12)
                    self.assertFalse(task.done())
                    self.assertEqual(guard_held, [True])
                    self.assertGreaterEqual(len(proof_calls), 2)
                    close.assert_awaited_once()
                    after.assert_awaited_once()
                    actual_closed[0] = True
                    with self.assertRaises(type(error)) as failure:
                        await task
                    self.assertIs(failure.exception, error)
                self.assertEqual(guard_held, [])
                self.assertEqual(stderr.getvalue().count("cleanup is unconfirmed"), 1)
                self.assertNotIn("synthetic", stderr.getvalue())

    async def test_transport_failed_after_early_closed_flag_stays_in_owned_child(self):
        await self._seed_database()
        code = textwrap.dedent(
            """
            import asyncio, contextlib, sys
            from pathlib import Path
            from types import SimpleNamespace
            from unittest.mock import AsyncMock, patch
            import tests
            from ygl_test_subject.scripts import configure_source_credentials as cli

            @contextlib.contextmanager
            def guard(*args, **kwargs):
                print('GUARD_ENTER', flush=True)
                try:
                    yield
                finally:
                    print('GUARD_EXIT', flush=True)

            class Core:
                closed = False
                database = SimpleNamespace(executor=SimpleNamespace(
                    initialize=AsyncMock(side_effect=asyncio.CancelledError())))
                async def close(self):
                    self.closed = True
                    return True

            class Transport:
                closed = False
                async def close(self):
                    self.closed = True
                    print('TRANSPORT_FAILED', flush=True)
                    raise RuntimeError('synthetic transport cleanup failure')

            async def run():
                with patch.object(cli, 'local_maintenance_guard', guard), \
                     patch.object(cli, '_new_core', return_value=(Core(), Transport())):
                    await cli._configure(Path(sys.argv[2]), 'clear', 'cn',
                                         plugin_root=Path(sys.argv[1]))
            asyncio.run(run())
            """
        )
        # This is a synthetic CLI fault in an explicitly owned child, not a
        # native OS qualification. Never flip the transport flag to recover.
        await self._assert_owned_fault_child_waits(
            code,
            "TRANSPORT_FAILED",
            ["GUARD_ENTER", "TRANSPORT_FAILED"],
            str(self.plugin_root),
            str(self.data_dir),
        )

    async def test_permanently_unknown_proof_keeps_owned_child_protected(self):
        code = textwrap.dedent(
            """
            import asyncio, contextlib
            import tests
            from ygl_test_subject.scripts import admin_credentials as cli
            async def close(): print('CLOSE_ONCE', flush=True)
            async def after(): print('AFTER_ONCE', flush=True)
            queried = False
            def proof():
                global queried
                if not queried:
                    print('PROOF_FAILED', flush=True)
                    queried = True
                raise asyncio.CancelledError('synthetic permanent proof failure')
            async def run():
                print('GUARD_ENTER', flush=True)
                try:
                    await cli._finish_cleanup(close, confirmed_closed=proof, after=after)
                finally:
                    print('GUARD_EXIT', flush=True)
            asyncio.run(run())
            """
        )
        # There is no test recovery flag: only terminating this owned process
        # can end a persistently unverifiable close, just as in the CLI contract.
        await self._assert_owned_fault_child_waits(
            code,
            "PROOF_FAILED",
            ["GUARD_ENTER", "CLOSE_ONCE", "AFTER_ONCE", "PROOF_FAILED"],
        )

    async def _assert_owned_fault_child_waits(self, code, ready, expected, *arguments):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-X",
            "utf8",
            "-c",
            code,
            *arguments,
            cwd=ROOT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        observed = []
        try:
            while ready not in observed:
                line = await asyncio.wait_for(process.stdout.readline(), 5)
                self.assertTrue(line, "owned fault child exited before close failure")
                observed.append(line.decode().strip())
            await asyncio.sleep(0.15)
            self.assertIsNone(process.returncode)
            self.assertEqual(observed, expected)
        finally:
            if process.returncode is None:
                process.terminate()
            stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
        self.assertEqual(stdout, b"")
        self.assertEqual(stderr.decode().count("cleanup is unconfirmed"), 1)
        self.assertNotIn("synthetic transport cleanup failure", stderr.decode())

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
            assembly = selected_assembly(self.plugin_root, PLUGIN_NAME)
            authority = cli._SessionAuthority()
            core, transport = cli._new_core(
                self.data_dir,
                installation.extension_root,
                authority,
                assembly,
                installation.inventory,
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
                clear = ConfigFieldUpdate(alias, ConfigUpdateMode.CLEAR)
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
                                    ConfigUpdateMode.REPLACE,
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

        async def configured(*_args, label_sink=None):
            label_sink("国际服")
            return SimpleNamespace(revision=4)

        with (
            patch.object(
                cli,
                "_configure",
                new=AsyncMock(side_effect=configured),
            ),
            contextlib.redirect_stdout(stdout),
        ):
            result = cli.main(
                ["--data-dir", str(self.data_dir), "--realm", "global", "set"]
            )
        self.assertEqual(result, 0)
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

    def test_both_official_clis_keep_exact_sdk_path_release_and_abi_pins(self):
        code = r"""
import importlib, sys
from pathlib import Path
root, module, mismatch = sys.argv[1:]
sys.path.insert(0, root)
import yomihime_game_link_sdk as sdk
from yomihime_game_link_sdk import version
if mismatch == 'version':
    sdk.__version__ = '0.1.0a4'
elif mismatch == 'abi':
    version.MODULE_ABI_VERSION = '1.7.0'
elif mismatch == 'path':
    sdk.__file__ = str(Path(root).parent / 'foreign-sdk' / '__init__.py')
elif mismatch == 'future':
    sdk.__version__ = '0.1.0a7'
else:
    raise AssertionError('unknown probe')
try:
    importlib.import_module(module)
except RuntimeError as exc:
    assert str(exc) == 'the pinned plugin-local SDK is unavailable'
    print('EXACT_PIN_REJECTED')
else:
    raise AssertionError('unreviewed SDK was accepted')
"""
        for cli_name in ("admin_credentials", "configure_source_credentials"):
            module = f"{ROOT.name}.scripts.{cli_name}"
            with self.subTest(cli=cli_name, operation="help"):
                help_result = subprocess.run(
                    [sys.executable, "-m", module, "--help"],
                    cwd=ROOT.parent,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(help_result.returncode, 0, help_result.stderr)
                self.assertIn("usage:", help_result.stdout)
            mismatches = ["version", "abi", "path", "future"]
            for mismatch in mismatches:
                with self.subTest(cli=cli_name, mismatch=mismatch):
                    result = subprocess.run(
                        [sys.executable, "-c", code, str(ROOT), module, mismatch],
                        cwd=ROOT.parent,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("EXACT_PIN_REJECTED", result.stdout)


if __name__ == "__main__":
    unittest.main()
