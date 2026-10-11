from __future__ import annotations

import asyncio
import contextlib
import io
import stat
import tempfile
import unittest
from pathlib import Path
from secrets import token_urlsafe
from types import SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.core.admission import AdmissionController
from ygl_test_subject.core.context_issuer import ContextIssuer
from ygl_test_subject.core.contracts.administration import (
    AdminAuthorizationDenied,
    AdminOperation,
)
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.registry import Registry
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories_admin_credentials import (
    AdminCredentialStatus,
    SQLiteAdminCredentialRepository,
)
from ygl_test_subject.scripts.admin_credentials import (
    LocalMaintenanceAuthorizationError,
    _parser,
    _run,
)
from ygl_test_subject.services.admin_authorization import (
    AdminAuthorizationService,
    AdminCredentialOperation,
    _digest,
)

from yomihime_game_link_sdk.services import CapabilityHealth, HealthStatus


class _Context:
    adapter_id = "adapter"
    request_id = "request"

    def __init__(self, session_id: str = "session", *, expired: bool = False) -> None:
        self.session_id = session_id
        self.expired = expired


class AdminAuthorizationServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = SQLiteDatabase(Path(self.temp.name) / "core.sqlite3")
        self.database.initialize()
        self.repository = SQLiteAdminCredentialRepository(self.database)
        self.admission = AdmissionController(
            Registry(),
            ContextIssuer(),
            current_run=lambda _module_id: None,
            is_active=lambda _identity: False,
            health_query=lambda _module_id, _capability_id: (
                validate_contract(CapabilityHealth(HealthStatus.UNKNOWN)),
                0,
            ),
        )
        self.service = self._service()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _service(self, *, context_validator=None, admission=True):
        return AdminAuthorizationService(
            self.repository,
            admission=self.admission if admission else None,
            context_validator=context_validator,
        )

    async def test_weak_input_and_secret_storage_redaction(self) -> None:
        for weak in ("", "password", "x" * 43, " hunter2 "):
            with self.assertRaises(ValueError) as caught:
                _digest(weak)
            if weak:
                self.assertNotIn(weak, str(caught.exception))

        secret = token_urlsafe(32)
        state = await self.repository.bootstrap(_digest(secret))
        self.assertEqual(state.status, AdminCredentialStatus.ACTIVE)
        self.assertEqual(len(state.verifier_digest or b""), 32)
        self.assertNotIn(secret.encode(), self.database.path.read_bytes())

    async def test_rotate_and_revoke_without_validator_fail_without_side_effects(
        self,
    ) -> None:
        current = token_urlsafe(32)
        await self.repository.bootstrap(_digest(current))
        for operation in (
            self.service.rotate(
                current,
                token_urlsafe(32),
                invocation=None,
                context=_Context(),
            ),
            self.service.revoke(current, invocation=None, context=_Context()),
        ):
            with self.assertRaises(AdminAuthorizationDenied):
                await operation
        state = await self.repository.current()
        self.assertEqual(
            (state.status, state.generation), (AdminCredentialStatus.ACTIVE, 1)
        )
        self.assertEqual(state.verifier_digest, _digest(current))

    async def test_rotate_and_revoke_without_admission_fail_closed(self) -> None:
        current = token_urlsafe(32)
        await self.repository.bootstrap(_digest(current))
        trusted = _Context()
        service = self._service(
            admission=False,
            context_validator=lambda _op, _inv, context, _generation: (
                context is trusted
            ),
        )
        with self.assertRaises(AdminAuthorizationDenied):
            await service.rotate(
                current,
                token_urlsafe(32),
                invocation=None,
                context=trusted,
            )
        with self.assertRaises(AdminAuthorizationDenied):
            await service.revoke(current, invocation=None, context=trusted)
        state = await self.repository.current()
        self.assertEqual(
            (state.status, state.generation), (AdminCredentialStatus.ACTIVE, 1)
        )
        self.assertEqual(state.verifier_digest, _digest(current))

    async def test_fake_and_expired_contexts_fail_closed(self) -> None:
        current = token_urlsafe(32)
        await self.repository.bootstrap(_digest(current))
        trusted = _Context()
        service = self._service(
            context_validator=lambda _op, _inv, context, generation: (
                context is trusted and not context.expired and generation == 1
            ),
        )
        for context in (_Context(), _Context(expired=True), None):
            with self.assertRaises(AdminAuthorizationDenied):
                await service.rotate(
                    current,
                    token_urlsafe(32),
                    invocation=None,
                    context=context,
                )
            with self.assertRaises(AdminAuthorizationDenied):
                await service.revoke(current, invocation=None, context=context)
        state = await self.repository.current()
        self.assertEqual(state.generation, 1)
        self.assertEqual(state.verifier_digest, _digest(current))

    async def test_old_generation_context_cannot_revoke_after_rotation(self) -> None:
        current = token_urlsafe(32)
        await self.repository.bootstrap(_digest(current))

        class _BoundContext(_Context):
            generation = 1

        context = _BoundContext()
        service = self._service(
            context_validator=lambda _op, _inv, actual, generation: (
                actual.generation == generation
            ),
        )
        new = token_urlsafe(32)
        await service.rotate(current, new, invocation=None, context=context)
        with self.assertRaises(AdminAuthorizationDenied):
            await service.revoke(new, invocation=None, context=context)
        state = await self.repository.current()
        self.assertEqual(
            (state.status, state.generation), (AdminCredentialStatus.ACTIVE, 2)
        )

    async def test_current_credential_and_trusted_context_rotate_then_revoke(
        self,
    ) -> None:
        old = token_urlsafe(32)
        await self.repository.bootstrap(_digest(old))
        approved = _Context()
        seen = []

        async def validator(operation, _invocation, context, generation):
            seen.append((operation, generation))
            return context is approved and generation in (1, 2)

        service = self._service(context_validator=validator)
        with self.assertRaises(AdminAuthorizationDenied):
            await service.rotate(
                token_urlsafe(32),
                token_urlsafe(32),
                invocation=None,
                context=approved,
            )
        self.assertEqual((await self.repository.current()).generation, 1)

        current = token_urlsafe(32)
        rotated = await service.rotate(old, current, invocation=None, context=approved)
        self.assertEqual(
            (rotated.status, rotated.generation), (AdminCredentialStatus.ACTIVE, 2)
        )
        with self.assertRaises(AdminAuthorizationDenied):
            await service.rotate(
                old,
                token_urlsafe(32),
                invocation=None,
                context=approved,
            )
        revoked = await service.revoke(current, invocation=None, context=approved)
        self.assertEqual(
            (revoked.status, revoked.generation), (AdminCredentialStatus.REVOKED, 3)
        )
        self.assertEqual(
            seen,
            [
                (AdminCredentialOperation.ROTATE, 1),
                (AdminCredentialOperation.REVOKE, 2),
            ],
        )
        self.assertNotIn(current.encode(), self.database.path.read_bytes())

    async def test_validate_generation_fences_stale_and_wrong_operation_grants(
        self,
    ) -> None:
        current = token_urlsafe(32)
        await self.repository.bootstrap(_digest(current))
        trusted = _Context()
        service = self._service(
            context_validator=lambda _op, _inv, context, _generation: (
                context is trusted
            )
        )
        grant = await service.authorize(
            AdminOperation.UPDATE_CONFIG, invocation=None, context=trusted
        )
        await service.validate_generation(grant, operation=AdminOperation.UPDATE_CONFIG)
        with self.assertRaises(AdminAuthorizationDenied):
            await service.validate_generation(
                grant, operation=AdminOperation.SET_ENABLED
            )

        await service.rotate(
            current,
            token_urlsafe(32),
            invocation=None,
            context=trusted,
        )
        with self.assertRaises(AdminAuthorizationDenied):
            await service.validate_generation(
                grant, operation=AdminOperation.UPDATE_CONFIG
            )

    async def test_admin_rotation_waits_for_shared_mutation_gate(self) -> None:
        current = token_urlsafe(32)
        await self.repository.bootstrap(_digest(current))
        trusted = _Context()
        validator_entered = asyncio.Event()
        validator_release = asyncio.Event()

        async def validator(_operation, _invocation, context, _generation):
            validator_entered.set()
            await validator_release.wait()
            return context is trusted

        service = self._service(context_validator=validator)
        rotation = asyncio.create_task(
            service.rotate(
                current,
                token_urlsafe(32),
                invocation=None,
                context=trusted,
            )
        )
        await asyncio.wait_for(validator_entered.wait(), timeout=2)
        async with self.admission.mutation("test-hold-admin-generation"):
            validator_release.set()
            await asyncio.sleep(0)
            state = await self.repository.current()
            self.assertEqual(
                (state.status, state.generation),
                (AdminCredentialStatus.ACTIVE, 1),
            )
        rotated = await rotation
        self.assertEqual(
            (rotated.status, rotated.generation), (AdminCredentialStatus.ACTIVE, 2)
        )

    async def test_each_admin_read_write_needs_context_validator(self) -> None:
        await self.repository.bootstrap(_digest(token_urlsafe(32)))
        with self.assertRaises(AdminAuthorizationDenied):
            await self.service.authorize(
                AdminOperation.LIST_MODULES, invocation=None, context=_Context()
            )
        trusted = _Context()
        seen = []

        async def validator(operation, _invocation, context, generation):
            seen.append((operation, generation))
            return context is trusted

        service = self._service(context_validator=validator)
        grant = await service.authorize(
            AdminOperation.LIST_MODULES, invocation=None, context=trusted
        )
        self.assertEqual(grant.generation, 1)
        with self.assertRaises(AdminAuthorizationDenied):
            await service.revalidate(
                grant,
                operation=AdminOperation.SET_ENABLED,
                invocation=None,
                context=trusted,
            )
        self.assertEqual(seen, [(AdminOperation.LIST_MODULES, 1)])

    async def test_rotation_generation_change_during_validation_rejects(self) -> None:
        current = token_urlsafe(32)
        await self.repository.bootstrap(_digest(current))
        context = _Context()

        async def validator(*_args):
            await self.repository.revoke(1)
            return True

        service = self._service(context_validator=validator)
        with self.assertRaises(AdminAuthorizationDenied):
            await service.rotate(
                current,
                token_urlsafe(32),
                invocation=None,
                context=context,
            )
        state = await self.repository.current()
        self.assertEqual(
            (state.status, state.generation), (AdminCredentialStatus.REVOKED, 2)
        )

    async def test_mutation_boundary_fences_generation_inside_transaction(self) -> None:
        await self.repository.bootstrap(_digest(token_urlsafe(32)))
        context = _Context()
        service = self._service(
            context_validator=lambda _op, _inv, actual, _gen: actual is context,
        )
        grant = await service.authorize(
            AdminOperation.SET_ENABLED, invocation=None, context=context
        )
        async with self.database.unit_of_work(begin_mode="IMMEDIATE") as unit:
            service.assert_generation_current(unit, grant, AdminOperation.SET_ENABLED)
        await self.repository.revoke(grant.generation)
        async with self.database.unit_of_work(begin_mode="IMMEDIATE") as unit:
            with self.assertRaises(AdminAuthorizationDenied):
                service.assert_generation_current(
                    unit, grant, AdminOperation.SET_ENABLED
                )

    async def test_cli_rejects_noninteractive_entry_before_prompt_or_storage_change(
        self,
    ) -> None:
        input_prompt = patch(
            "ygl_test_subject.scripts.admin_credentials.getpass.getpass"
        )
        with patch(
            "ygl_test_subject.scripts.admin_credentials.sys.stdin.isatty",
            return_value=False,
        ):
            with (
                patch("ygl_test_subject.scripts.admin_credentials._POSIX", True),
                patch(
                    "ygl_test_subject.scripts.admin_credentials.os",
                    SimpleNamespace(
                        name="posix", getuid=lambda: 1234, geteuid=lambda: 1234
                    ),
                ),
                input_prompt as prompt,
                self.assertRaises(LocalMaintenanceAuthorizationError),
            ):
                await _run(self.database.path, "bootstrap")
        prompt.assert_not_called()
        self.assertEqual(
            (await self.repository.current()).status,
            AdminCredentialStatus.UNINITIALIZED,
        )

    async def test_cli_rejects_group_writable_direct_parent_before_prompt(self) -> None:
        from ygl_test_subject.scripts import admin_credentials

        database_path = self.database.path.resolve()
        database_parent = database_path.parent
        script_path = Path(admin_credentials.__file__).resolve()
        owner_id = 1234

        def fake_stat(path: Path):
            if path in (database_path, script_path):
                mode = stat.S_IFREG | 0o600
            elif path == database_parent:
                mode = stat.S_IFDIR | 0o720
            else:
                mode = stat.S_IFDIR | 0o700
            return SimpleNamespace(st_mode=mode, st_uid=owner_id)

        with (
            patch("ygl_test_subject.scripts.admin_credentials._POSIX", True),
            patch(
                "ygl_test_subject.scripts.admin_credentials.os",
                SimpleNamespace(
                    name="posix",
                    getuid=lambda: owner_id,
                    geteuid=lambda: owner_id,
                    access=lambda *_: True,
                ),
            ),
            patch(
                "ygl_test_subject.scripts.admin_credentials.sys.stdin.isatty",
                return_value=True,
            ),
            patch("ygl_test_subject.scripts.admin_credentials.Path.stat", fake_stat),
            patch(
                "ygl_test_subject.scripts.admin_credentials.getpass.getpass"
            ) as prompt,
        ):
            with self.assertRaises(LocalMaintenanceAuthorizationError):
                await _run(database_path, "bootstrap")
        prompt.assert_not_called()
        self.assertEqual(
            (await self.repository.current()).status,
            AdminCredentialStatus.UNINITIALIZED,
        )

    def test_cli_does_not_accept_credential_arguments_or_echo_them(self) -> None:
        secret = token_urlsafe(32)
        output = io.StringIO()
        with contextlib.redirect_stderr(output), self.assertRaises(SystemExit):
            _parser().parse_args(["--database", "core.sqlite3", "bootstrap", secret])
        self.assertNotIn(secret, output.getvalue())


if __name__ == "__main__":
    unittest.main()
