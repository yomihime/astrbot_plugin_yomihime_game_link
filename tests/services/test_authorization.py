from __future__ import annotations

import tempfile
import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from ygl_test_subject.core.context_issuer import ContextIssuer
from ygl_test_subject.core.contracts.services import (
    Grant,
    GrantStatus,
    LoginSession,
    LoginSessionStatus,
)
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.ports import RevisionConflict, ScheduledLease
from ygl_test_subject.infrastructure.sqlite.repositories_auth import (
    SQLiteAuthRepository,
)
from ygl_test_subject.services.authorization import (
    AuthorizationConflict,
    AuthorizationPermissionError,
    AuthorizationService,
    AuthorizationUnavailable,
    LoginSessionExpired,
)

from yomihime_game_link_sdk.contexts import InvocationOrigin
from yomihime_game_link_sdk.storage import GrantReference, SecretRef


class _ScheduleAdmission:
    @asynccontextmanager
    async def mutation(self, owner: str):
        yield

    def check(self, lease: ScheduledLease) -> None:
        if not isinstance(lease, ScheduledLease):
            raise ValueError("invalid schedule lease")


class _Exchange:
    def __init__(self, grant: Grant | None = None) -> None:
        self.grant = grant

    async def verify(self, session):
        if self.grant is None:
            raise ValueError("unverified")
        return self.grant


class _AuthRepository:
    """Small port fake; SQLite semantics are covered by S3's repository tests."""

    def __init__(self) -> None:
        self.sessions: dict[str, LoginSession] = {}
        self.generations: dict[tuple[str, str], int] = {}
        self.grants: dict[str, Grant] = {}
        self.revocations: list[Grant] = []
        self.legacy_revoke_calls = 0
        self.fail_save = False
        self.fail_create = False
        self.fail_rotate = False
        self.before_complete = None

    async def current(self, session_id: str):
        return self.sessions.get(session_id)

    async def next_generation(self, principal_id, module_id, *, expected_generation):
        key = (principal_id, module_id)
        actual = self.generations.get(key, 0)
        if actual != expected_generation:
            raise RevisionConflict(
                "login_session_generation", expected_generation, actual
            )
        generation = actual + 1
        for session_id, session in tuple(self.sessions.items()):
            if (
                session.principal_id,
                session.module_id,
            ) == key and session.status is LoginSessionStatus.PENDING:
                self.sessions[session_id] = LoginSession(
                    session.session_id,
                    session.principal_id,
                    session.module_id,
                    session.generation,
                    LoginSessionStatus.CANCELLED,
                    session.expires_at,
                )
        session = LoginSession(
            f"login_{uuid4().hex}",
            principal_id,
            module_id,
            generation,
            LoginSessionStatus.PENDING,
            datetime.now(UTC) + timedelta(minutes=10),
        )
        self.generations[key] = generation
        self.sessions[session.session_id] = session
        return session

    async def save(self, session, *, expected_generation):
        if self.fail_save:
            raise RevisionConflict(
                "login_session", expected_generation, expected_generation + 1
            )
        current = self.sessions.get(session.session_id)
        if current is None or current.generation != expected_generation:
            raise RevisionConflict("login_session", expected_generation, 0)
        if (
            current.principal_id != session.principal_id
            or current.module_id != session.module_id
        ):
            raise ValueError("owner mismatch")
        if (
            current.status is not LoginSessionStatus.PENDING
            and current.status != session.status
        ):
            raise RevisionConflict(
                "login_session_state", expected_generation, current.generation
            )
        self.sessions[session.session_id] = session
        return session

    async def expire_pending_on_restart(self):
        count = 0
        for session_id, session in tuple(self.sessions.items()):
            if session.status is LoginSessionStatus.PENDING:
                self.sessions[session_id] = LoginSession(
                    session.session_id,
                    session.principal_id,
                    session.module_id,
                    session.generation,
                    LoginSessionStatus.EXPIRED,
                    session.expires_at,
                )
                count += 1
        return count

    async def current_grant(self, grant_id):
        return self.grants.get(grant_id)

    async def list_for(self, principal_id, module_id):
        return tuple(
            grant
            for grant in self.grants.values()
            if grant.principal_id == principal_id and grant.module_id == module_id
        )

    async def create_grant(self, grant, *, expected_revision):
        if (
            expected_revision != 0
            or grant.revision != 1
            or grant.grant_id in self.grants
        ):
            raise RevisionConflict("grant", expected_revision, 1)
        self.grants[grant.grant_id] = grant
        return grant

    async def rotate_grant(self, grant, *, expected_revision):
        current = self.grants.get(grant.grant_id)
        if current is None or current.revision != expected_revision:
            raise RevisionConflict("grant", expected_revision, 0)
        updated = Grant(
            grant.grant_id,
            expected_revision + 1,
            current.principal_id,
            current.module_id,
            grant.account_id,
            grant.scopes,
            grant.secret_ref,
            grant.status,
            grant.expires_at,
        )
        self.grants[grant.grant_id] = updated
        return updated

    async def revoke_grant(self, grant, *, expected_revision):
        self.legacy_revoke_calls += 1
        current = self.grants.get(grant.grant_id)
        if current is None or current.revision != expected_revision:
            raise RevisionConflict("grant", expected_revision, 0)
        revoked = Grant(
            current.grant_id,
            expected_revision + 1,
            current.principal_id,
            current.module_id,
            current.account_id,
            current.scopes,
            current.secret_ref,
            GrantStatus.REVOKED,
            current.expires_at,
        )
        self.grants[grant.grant_id] = revoked
        for session_id, session in tuple(self.sessions.items()):
            if (
                session.principal_id == current.principal_id
                and session.module_id == current.module_id
                and session.status is LoginSessionStatus.PENDING
            ):
                self.sessions[session_id] = LoginSession(
                    session.session_id,
                    session.principal_id,
                    session.module_id,
                    session.generation,
                    LoginSessionStatus.CANCELLED,
                    session.expires_at,
                )
        return revoked

    async def revoke_grant_with_invalidation(self, grant, *, expected_revision):
        revoked = await self.revoke_grant(grant, expected_revision=expected_revision)
        self.revocations.append(revoked)
        return revoked

    async def complete_login(
        self,
        session,
        grant,
        *,
        expected_generation,
        expected_grant_revision,
    ):
        if self.before_complete is not None:
            callback = self.before_complete
            self.before_complete = None
            await callback()
        current_session = self.sessions.get(session.session_id)
        latest = self.generations.get((session.principal_id, session.module_id), 0)
        if (
            current_session is None
            or current_session.status is not LoginSessionStatus.PENDING
            or current_session.generation != expected_generation
            or current_session.generation != latest
            or current_session.principal_id != grant.principal_id
            or current_session.module_id != grant.module_id
        ):
            raise RevisionConflict(
                "login_session_generation", expected_generation, latest
            )
        existing = self.grants.get(grant.grant_id)
        if expected_grant_revision is None:
            if self.fail_create:
                raise RuntimeError("simulated grant create failure")
            if existing is not None or any(
                item.status is GrantStatus.ACTIVE
                and item.principal_id == grant.principal_id
                and item.module_id == grant.module_id
                for item in self.grants.values()
            ):
                raise RevisionConflict("grant", 0, 1)
            if grant.revision != 1:
                raise RevisionConflict("grant", 0, grant.revision)
            persisted = grant
        else:
            if self.fail_rotate:
                raise RuntimeError("simulated grant rotate failure")
            if (
                existing is None
                or existing.revision != expected_grant_revision
                or existing.status is not GrantStatus.ACTIVE
                or grant.revision != expected_grant_revision + 1
            ):
                raise RevisionConflict(
                    "grant",
                    expected_grant_revision,
                    0 if existing is None else existing.revision,
                )
            persisted = Grant(
                grant.grant_id,
                expected_grant_revision + 1,
                existing.principal_id,
                existing.module_id,
                grant.account_id,
                grant.scopes,
                grant.secret_ref,
                GrantStatus.ACTIVE,
                grant.expires_at,
            )
        completed = LoginSession(
            current_session.session_id,
            current_session.principal_id,
            current_session.module_id,
            current_session.generation,
            LoginSessionStatus.COMPLETED,
            current_session.expires_at,
        )
        self.sessions[completed.session_id] = completed
        self.grants[persisted.grant_id] = persisted
        return completed, persisted


class AuthorizationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "runtime.sqlite3"
        self.repository = _AuthRepository()
        self.issuer = ContextIssuer()
        self.service = AuthorizationService(
            self.repository,
            self.repository,
            self.issuer,
            secret_available=lambda grant: grant.secret_ref is not None,
            exchange_verifier=self._trusted_exchange,
        )

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    async def _trusted_exchange(self, session, exchange):
        if not isinstance(exchange, _Exchange) or exchange.grant is None:
            raise ValueError("exchange rejected")
        return exchange.grant

    def _invocation(self, *, origin: InvocationOrigin = InvocationOrigin.COMMAND):
        return self.issuer.issue(
            origin=origin,
            module_id="package/module",
            module_epoch=1,
            registry_revision=1,
            actor_id="user-a",
            conversation_id="conversation-a",
            adapter_id="adapter-a",
        )

    def _grant(self, grant_id: str = "grant-a") -> Grant:
        return Grant(
            grant_id,
            1,
            "user-a",
            "package/module",
            "account-a",
            ("private.read",),
            validate_contract(
                SecretRef(
                    "secret_token_a",
                    "user-a",
                    "package/module",
                    "credential",
                    "exchange-a",
                )
            ),
            GrantStatus.ACTIVE,
        )

    def _schedule_authorization(self, *, now=None, secret_available=None):
        return AuthorizationService(
            self.repository,
            self.repository,
            self.issuer,
            admission=_ScheduleAdmission(),
            now=now or (lambda: datetime.now(UTC)),
            secret_available=secret_available or (lambda grant: True),
        )

    def _scheduled_grant_view(self, grant: Grant):
        lease = ScheduledLease(
            "schedule-lease-a",
            "execution-lease-a",
            "package/module",
            1,
            1,
            "collector-a",
            1,
            (),
        )
        view = self.issuer.issue(
            origin=InvocationOrigin.SCHEDULER,
            module_id="package/module",
            module_epoch=1,
            registry_revision=1,
            actor_id=grant.principal_id,
            grant_id=grant.grant_id,
            grant_revision=grant.revision,
        )
        self.issuer.attach_lease(view, lease)
        return view, lease

    async def test_command_login_status_complete_and_revoke(self) -> None:
        invocation = self._invocation()
        session_id = await self.service.begin_login(invocation)
        self.assertTrue(session_id.startswith("login_"))
        self.assertIsNone(await self.service.status(invocation))

        reference = await self.service.complete_login(
            invocation, session_id, _Exchange(self._grant())
        )
        self.assertEqual(reference, validate_contract(GrantReference("grant-a", 1)))
        self.assertEqual(await self.service.status(invocation), reference)
        details = await self.service.status_details(invocation)
        self.assertTrue(details.secret_available)
        self.assertEqual(details.grant_status, GrantStatus.ACTIVE)

        await self.service.revoke(invocation, reference)
        self.assertEqual(self.repository.revocations[-1].status, GrantStatus.REVOKED)
        self.assertIsNone(await self.service.status(invocation))

    async def test_revoke_without_atomic_coordinator_fails_before_cas(self) -> None:
        repository = _AuthRepository()
        repository.revoke_grant_with_invalidation = None
        service = AuthorizationService(
            repository,
            repository,
            self.issuer,
            secret_available=lambda grant: grant.secret_ref is not None,
            exchange_verifier=self._trusted_exchange,
        )
        invocation = self._invocation()
        reference = await service.complete_login(
            invocation,
            await service.begin_login(invocation),
            _Exchange(self._grant()),
        )

        with self.assertRaises(AuthorizationUnavailable):
            await service.revoke(invocation, reference)

        stored = await repository.current_grant(reference.grant_id)
        self.assertEqual(stored.status, GrantStatus.ACTIVE)
        self.assertEqual(stored.revision, reference.revision)
        self.assertEqual(repository.legacy_revoke_calls, 0)
        self.assertEqual(repository.revocations, [])

    async def test_new_generation_rejects_stale_callback(self) -> None:
        invocation = self._invocation()
        old_session = await self.service.begin_login(invocation)
        new_session = await self.service.reopen(invocation)
        self.assertNotEqual(old_session, new_session)
        with self.assertRaises(LoginSessionExpired):
            await self.service.complete_login(
                invocation, old_session, _Exchange(self._grant("grant-old"))
            )
        self.assertIsNone(await self.service.status(invocation))

    async def test_callback_from_another_conversation_is_rejected(self) -> None:
        invocation = self._invocation()
        session_id = await self.service.begin_login(invocation)
        other_conversation = self.issuer.issue(
            origin=InvocationOrigin.COMMAND,
            module_id="package/module",
            module_epoch=1,
            registry_revision=1,
            actor_id="user-a",
            conversation_id="conversation-b",
            adapter_id="adapter-a",
        )
        with self.assertRaises(AuthorizationPermissionError):
            await self.service.complete_login(
                other_conversation, session_id, _Exchange(self._grant())
            )
        self.assertIsNone(await self.service.status(invocation))

    async def test_unverified_exchange_and_non_command_do_not_change_state(
        self,
    ) -> None:
        invocation = self._invocation(origin=InvocationOrigin.LLM_TOOL)
        with self.assertRaises(AuthorizationPermissionError):
            await self.service.begin_login(invocation)

        command = self._invocation()
        session_id = await self.service.begin_login(command)
        with self.assertRaises(AuthorizationPermissionError):
            await self.service.complete_login(command, session_id, object())
        self.assertIsNone(await self.service.status(command))

    async def test_scheduled_grant_requires_exact_root_scheduler_lease(self) -> None:
        grant = self._grant()
        self.repository.grants[grant.grant_id] = grant
        service = self._schedule_authorization()
        view, lease = self._scheduled_grant_view(grant)

        self.assertIs(await service.require_scheduled_grant(view, lease), grant)
        copied = replace(lease, lease_id="copied-schedule-lease")
        with self.assertRaises(AuthorizationPermissionError):
            await service.require_scheduled_grant(view, copied)

        tool_view = self.issuer.issue(
            origin=InvocationOrigin.LLM_TOOL,
            module_id="package/module",
            module_epoch=1,
            registry_revision=1,
            actor_id=grant.principal_id,
            conversation_id="conversation-a",
            adapter_id="adapter-a",
            capability_id="private.read",
            grant_id=grant.grant_id,
            grant_revision=grant.revision,
        )
        self.issuer.attach_lease(tool_view, lease)
        with self.assertRaises(AuthorizationPermissionError):
            await service.require_scheduled_grant(tool_view, lease)

    async def test_scheduled_grant_checks_expiry_with_runtime_clock(self) -> None:
        now = datetime(2030, 1, 1, tzinfo=UTC)
        grant = replace(self._grant(), expires_at=now - timedelta(seconds=1))
        self.repository.grants[grant.grant_id] = grant
        service = self._schedule_authorization(now=lambda: now)
        view, lease = self._scheduled_grant_view(grant)

        with self.assertRaises(AuthorizationPermissionError):
            await service.require_scheduled_grant(view, lease)

    async def test_scheduled_grant_rechecks_row_after_secret_await(self) -> None:
        grant = self._grant()
        self.repository.grants[grant.grant_id] = grant

        async def revoke_during_secret_read(current: Grant) -> bool:
            self.repository.grants[current.grant_id] = replace(
                current,
                revision=current.revision + 1,
                status=GrantStatus.REVOKED,
            )
            return True

        service = self._schedule_authorization(
            secret_available=revoke_during_secret_read
        )
        view, lease = self._scheduled_grant_view(grant)

        with self.assertRaises(AuthorizationConflict):
            await service.require_scheduled_grant(view, lease)

    async def test_private_dependency_authority_rejects_tool_and_scheduler(
        self,
    ) -> None:
        grant = self._grant()
        service = self._schedule_authorization()
        for origin in (InvocationOrigin.LLM_TOOL, InvocationOrigin.SCHEDULER):
            kwargs = (
                {
                    "conversation_id": "conversation-a",
                    "adapter_id": "adapter-a",
                }
                if origin is InvocationOrigin.LLM_TOOL
                else {}
            )
            parent = self.issuer.issue(
                origin=origin,
                module_id="package/module",
                module_epoch=1,
                registry_revision=1,
                actor_id=grant.principal_id,
                capability_id="read",
                grant_id=grant.grant_id,
                grant_revision=grant.revision,
                **kwargs,
            )
            child = self.issuer.derive(
                parent,
                module_id=parent.module_id,
                module_epoch=parent.module_epoch,
                capability_id="private.read",
            )
            try:
                with self.assertRaises(AuthorizationPermissionError):
                    await service.require_dependency_grant(parent, child)
            finally:
                self.issuer.release(child)
                self.issuer.release(parent)

    async def test_caller_exchange_method_is_not_a_verifier(self) -> None:
        service = AuthorizationService(
            self.repository,
            self.repository,
            self.issuer,
            secret_available=lambda grant: grant.secret_ref is not None,
        )
        command = self._invocation()
        session_id = await service.begin_login(command)
        with self.assertRaises(AuthorizationPermissionError):
            await service.complete_login(
                command, session_id, _Exchange(self._grant("untrusted"))
            )
        self.assertEqual(self.repository.grants, {})

    async def test_atomic_rotate_failure_preserves_pending_session_and_grant(
        self,
    ) -> None:
        command = self._invocation()
        first_session = await self.service.begin_login(command)
        first_reference = await self.service.complete_login(
            command, first_session, _Exchange(self._grant())
        )
        second_session = await self.service.reopen(command)
        self.repository.fail_rotate = True
        with self.assertRaises(AuthorizationUnavailable):
            await self.service.complete_login(
                command, second_session, _Exchange(self._grant())
            )
        self.repository.fail_rotate = False
        current = await self.repository.current_grant(first_reference.grant_id)
        self.assertEqual(current.revision, first_reference.revision)
        self.assertEqual(current.status, GrantStatus.ACTIVE)
        self.assertEqual(
            (await self.repository.current(second_session)).status,
            LoginSessionStatus.PENDING,
        )
        self.assertEqual(self.repository.revocations, [])

    async def test_atomic_create_failure_preserves_pending_session(self) -> None:
        command = self._invocation()
        session_id = await self.service.begin_login(command)
        self.repository.fail_create = True
        with self.assertRaises(AuthorizationUnavailable):
            await self.service.complete_login(
                command, session_id, _Exchange(self._grant())
            )
        self.repository.fail_create = False
        self.assertIsNone(await self.repository.current_grant("grant-a"))
        self.assertEqual(
            (await self.repository.current(session_id)).status,
            LoginSessionStatus.PENDING,
        )

    async def test_new_generation_racing_completion_rejects_old_callback(self) -> None:
        invocation = self._invocation()
        old_session = await self.service.begin_login(invocation)
        newer = []

        async def begin_new_generation():
            newer.append(await self.service.reopen(invocation))

        self.repository.before_complete = begin_new_generation
        with self.assertRaises(AuthorizationConflict):
            await self.service.complete_login(
                invocation, old_session, _Exchange(self._grant())
            )
        self.assertIsNone(await self.repository.current_grant("grant-a"))
        self.assertEqual(len(newer), 1)
        self.assertEqual(
            (await self.repository.current(newer[0])).status,
            LoginSessionStatus.PENDING,
        )

    async def test_revoke_winning_completion_race_cancels_callback(self) -> None:
        invocation = self._invocation()
        first = await self.service.complete_login(
            invocation,
            await self.service.begin_login(invocation),
            _Exchange(self._grant()),
        )
        pending = await self.service.reopen(invocation)

        async def revoke_first():
            await self.service.revoke(invocation, first)

        self.repository.before_complete = revoke_first
        with self.assertRaises(AuthorizationConflict):
            await self.service.complete_login(
                invocation, pending, _Exchange(self._grant("fresh-grant"))
            )
        self.assertEqual(
            (await self.repository.current(pending)).status,
            LoginSessionStatus.CANCELLED,
        )
        self.assertEqual(
            (await self.repository.current_grant(first.grant_id)).status,
            GrantStatus.REVOKED,
        )

    async def test_restart_expires_pending_and_repository_keeps_grant_revoked(
        self,
    ) -> None:
        invocation = self._invocation()
        session_id = await self.service.begin_login(invocation)
        self.assertEqual(await self.service.restart(), 1)
        with self.assertRaises(LoginSessionExpired):
            await self.service.complete_login(
                invocation, session_id, _Exchange(self._grant())
            )

        reference = await self.service.complete_login(
            invocation,
            await self.service.begin_login(invocation),
            _Exchange(self._grant()),
        )
        await self.service.revoke(invocation, reference)
        revoked = await self.repository.current_grant(reference.grant_id)
        self.assertEqual(revoked.status, GrantStatus.REVOKED)

    async def test_sqlite_qualified_module_completion_and_restart(self) -> None:
        repository = SQLiteAuthRepository(self.database)
        service = AuthorizationService(
            repository,
            repository,
            self.issuer,
            secret_available=lambda grant: grant.secret_ref is not None,
            exchange_verifier=self._trusted_exchange,
        )
        invocation = self._invocation()
        expired_session_id = await service.begin_login(invocation)

        reopened_repository = SQLiteAuthRepository(self.database)
        reopened = AuthorizationService(
            reopened_repository,
            reopened_repository,
            self.issuer,
            secret_available=lambda grant: grant.secret_ref is not None,
            exchange_verifier=self._trusted_exchange,
        )
        with self.assertRaises(LoginSessionExpired):
            await reopened.complete_login(
                invocation,
                expired_session_id,
                _Exchange(self._grant()),
            )

        current_session_id = await reopened.begin_login(invocation)
        reference = await reopened.complete_login(
            invocation, current_session_id, _Exchange(self._grant())
        )
        persisted = await reopened_repository.current_grant(reference.grant_id)
        self.assertEqual(persisted.module_id, "package/module")
        self.assertEqual(persisted.revision, 1)
        self.assertEqual(await reopened.status(invocation), reference)

        next_session_id = await reopened.reopen(invocation)
        fresh_repository = SQLiteAuthRepository(self.database)
        fresh_service = AuthorizationService(
            fresh_repository,
            fresh_repository,
            self.issuer,
            secret_available=lambda grant: grant.secret_ref is not None,
            exchange_verifier=self._trusted_exchange,
        )
        with self.assertRaises(LoginSessionExpired):
            await fresh_service.complete_login(
                invocation,
                next_session_id,
                _Exchange(self._grant()),
            )
        rotated_session_id = await fresh_service.begin_login(invocation)
        rotated = await fresh_service.complete_login(
            invocation, rotated_session_id, _Exchange(self._grant())
        )
        self.assertEqual(rotated, validate_contract(GrantReference("grant-a", 2)))

        pending_after_login = await fresh_service.reopen(invocation)
        await fresh_service.revoke(invocation, rotated)
        with self.assertRaises(LoginSessionExpired):
            await fresh_service.complete_login(
                invocation,
                pending_after_login,
                _Exchange(self._grant("fresh-grant")),
            )
        self.assertIsNone(await fresh_repository.current_grant("fresh-grant"))
        self.assertEqual(
            (await fresh_repository.current(pending_after_login)).status,
            LoginSessionStatus.CANCELLED,
        )

    async def test_sqlite_reauthorize_same_account_after_revoke(self) -> None:
        repository = SQLiteAuthRepository(self.database)
        service = AuthorizationService(
            repository,
            repository,
            self.issuer,
            secret_available=lambda grant: grant.secret_ref is not None,
            exchange_verifier=self._trusted_exchange,
        )
        invocation = self._invocation()
        first = await service.complete_login(
            invocation,
            await service.begin_login(invocation),
            _Exchange(self._grant()),
        )
        await service.revoke(invocation, first)
        revoked = await repository.current_grant(first.grant_id)
        self.assertEqual(revoked.status, GrantStatus.REVOKED)
        self.assertEqual(revoked.revision, first.revision + 1)

        reauthorized = await service.complete_login(
            invocation,
            await service.begin_login(invocation),
            _Exchange(self._grant("fresh-grant-id")),
        )
        self.assertEqual(
            reauthorized,
            validate_contract(GrantReference(first.grant_id, revoked.revision + 1)),
        )
        current = await repository.current_grant(first.grant_id)
        self.assertEqual(current.status, GrantStatus.ACTIVE)
        self.assertEqual(current.account_id, "account-a")
        self.assertEqual(await service.status(invocation), reauthorized)
        with self.assertRaises(AuthorizationConflict):
            await service.revoke(invocation, first)

    async def test_sqlite_different_account_login_is_selected_by_status(self) -> None:
        repository = SQLiteAuthRepository(self.database)
        service = AuthorizationService(
            repository,
            repository,
            self.issuer,
            secret_available=lambda grant: grant.secret_ref is not None,
            exchange_verifier=self._trusted_exchange,
        )
        invocation = self._invocation()
        first = await service.complete_login(
            invocation,
            await service.begin_login(invocation),
            _Exchange(self._grant()),
        )
        await service.revoke(invocation, first)

        different_account = Grant(
            "grant-b",
            1,
            "user-a",
            "package/module",
            "account-b",
            ("private.read",),
            validate_contract(
                SecretRef(
                    "secret_token_b",
                    "user-a",
                    "package/module",
                    "credential",
                    "exchange-b",
                )
            ),
            GrantStatus.ACTIVE,
        )
        accepted = await service.complete_login(
            invocation,
            await service.begin_login(invocation),
            _Exchange(different_account),
        )
        self.assertEqual(accepted, validate_contract(GrantReference("grant-b", 1)))
        self.assertEqual(await service.status(invocation), accepted)
        details = await service.status_details(invocation)
        self.assertEqual(details.grant, accepted)
        self.assertEqual(details.grant_status, GrantStatus.ACTIVE)
        self.assertTrue(details.secret_available)


if __name__ == "__main__":
    unittest.main()
