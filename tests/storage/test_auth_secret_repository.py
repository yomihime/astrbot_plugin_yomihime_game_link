from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ygl_test_subject.api.contexts import InvocationOrigin
from ygl_test_subject.api.services import (
    Grant,
    GrantStatus,
    LoginSession,
    LoginSessionStatus,
)
from ygl_test_subject.api.storage import (
    ClaimedSecretReceipt,
    GrantReference,
    SecretRef,
    SecretTarget,
)
from ygl_test_subject.core.context_issuer import ContextIssuer
from ygl_test_subject.core.ports import RevisionConflict, SecretOwner
from ygl_test_subject.infrastructure.secret_store import (
    SecretStoreUnavailable,
    SQLiteSecretStore,
)
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories_auth import (
    SQLiteAuthRepository,
)
from ygl_test_subject.services.authorization import (
    AuthorizationConflict,
    AuthorizationService,
)


class _Codec:
    def encrypt(self, value: bytes) -> bytes:
        return b"envelope:" + value[::-1]

    def decrypt(self, value: bytes) -> bytes:
        if not value.startswith(b"envelope:"):
            raise ValueError("invalid envelope")
        return value[len(b"envelope:") :][::-1]


class AuthSecretRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "runtime.sqlite3"

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    async def test_grant_cas_and_restart(self) -> None:
        repo = SQLiteAuthRepository(self.db)
        grant = Grant(
            "grant-a",
            1,
            "user-a",
            "steam",
            "account",
            ("read",),
            None,
            GrantStatus.ACTIVE,
        )
        self.assertEqual(await repo.create_grant(grant, expected_revision=0), grant)
        with self.assertRaises(RevisionConflict):
            await repo.revoke_grant(
                Grant(
                    "grant-a",
                    2,
                    "user-a",
                    "steam",
                    "account",
                    ("read",),
                    None,
                    GrantStatus.REVOKED,
                ),
                expected_revision=0,
            )
        revoked = await repo.revoke_grant(
            Grant(
                "grant-a",
                2,
                "user-a",
                "steam",
                "account",
                ("read",),
                None,
                GrantStatus.REVOKED,
            ),
            expected_revision=1,
        )
        self.assertEqual(revoked.status, GrantStatus.REVOKED)
        self.assertEqual(
            (await SQLiteAuthRepository(self.db).current_grant("grant-a")).revision, 2
        )

    async def test_login_generation_and_restart_expire_pending(self) -> None:
        repo = SQLiteAuthRepository(self.db)
        session = await repo.next_generation("user-a", "steam", expected_generation=0)
        self.assertEqual(session.generation, 1)
        reopened = SQLiteAuthRepository(self.db)
        await reopened.expire_pending_on_restart()
        restored = await reopened.current(session.session_id)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.status, LoginSessionStatus.EXPIRED)
        with self.assertRaises(RevisionConflict):
            await reopened.next_generation("user-a", "steam", expected_generation=0)

    async def test_qualified_module_ids_flow_through_real_authorization_and_isolate(
        self,
    ) -> None:
        repo = SQLiteAuthRepository(self.db, expire_on_open=False)
        issuer = ContextIssuer()
        service = AuthorizationService(
            repo,
            repo,
            issuer,
            secret_available=lambda grant: grant.secret_ref is not None,
        )
        invocation = issuer.issue(
            origin=InvocationOrigin.COMMAND,
            module_id="package/module",
            module_epoch=1,
            registry_revision=1,
            actor_id="user-a",
            conversation_id="conversation-a",
            adapter_id="adapter-a",
        )

        session_id = await service.begin_login(invocation)
        session = await repo.current(session_id)
        self.assertEqual(session.module_id, "package/module")
        self.assertIsNone(await service.status(invocation))

        grant = Grant(
            "grant-qualified",
            1,
            "user-a",
            "package/module",
            "account-qualified",
            ("private.read",),
            SecretRef(
                "secret_qualified",
                "user-a",
                "package/module",
                "credential",
                "operation-qualified",
            ),
            GrantStatus.ACTIVE,
        )
        await repo.create_grant(grant, expected_revision=0)
        self.assertEqual(
            await service.status(invocation),
            GrantReference("grant-qualified", 1),
        )

        self.assertEqual(await service.restart(), 1)
        self.assertEqual(
            (await repo.current(session_id)).status, LoginSessionStatus.EXPIRED
        )
        next_session_id = await service.begin_login(invocation)
        self.assertEqual(
            (await repo.current(next_session_id)).generation,
            2,
        )

        other_invocation = issuer.issue(
            origin=InvocationOrigin.COMMAND,
            module_id="package/other",
            module_epoch=1,
            registry_revision=1,
            actor_id="user-a",
            conversation_id="conversation-a",
            adapter_id="adapter-a",
        )
        other_session_id = await service.begin_login(other_invocation)
        self.assertEqual(
            (await repo.current(other_session_id)).module_id, "package/other"
        )
        self.assertIsNone(await service.status(other_invocation))
        self.assertEqual(
            tuple(
                grant.module_id
                for grant in await repo.list_for("user-a", "package/module")
            ),
            ("package/module",),
        )
        self.assertEqual(await repo.list_for("user-a", "package/other"), ())

    async def test_secret_ledger_is_one_shot_and_payload_is_outside_sqlite(
        self,
    ) -> None:
        store = SQLiteSecretStore(self.db, self.root / "secrets", codec=_Codec())
        target = SecretTarget("user-a", "steam", "token")
        receipt = await store.stage(
            b"RAW_TOKEN", target=target, operation_id="op-1", expected_config_revision=3
        )
        claimed = await store.claim_for_config(
            receipt,
            target=target,
            operation_id="op-1",
            expected_config_revision=3,
            expected_ledger_revision=receipt.ledger_revision,
        )
        with self.assertRaises(RevisionConflict):
            await store.claim_for_config(
                receipt,
                target=target,
                operation_id="op-1",
                expected_config_revision=3,
                expected_ledger_revision=receipt.ledger_revision,
            )
        await store.finalize_active(claimed, metadata_revision=4)
        payload = next((self.root / "secrets").glob("*.blob")).read_bytes()
        self.assertNotIn(b"RAW_TOKEN", payload)
        connection = sqlite3.connect(self.db)
        try:
            values = connection.execute("SELECT * FROM secret_receipts").fetchone()
            self.assertNotIn(b"RAW_TOKEN", repr(values).encode())
        finally:
            connection.close()
        del values
        owner = SecretOwner("user-a", "steam", "token", "op-1")
        self.assertEqual(
            await store.read(receipt.secret_ref, owner=owner), b"RAW_TOKEN"
        )

    async def test_missing_codec_is_controlled_unavailable(self) -> None:
        store = SQLiteSecretStore(self.db, self.root / "secrets")
        with self.assertRaises(SecretStoreUnavailable):
            await store.stage(
                b"secret",
                target=SecretTarget("user-a", "steam", "token"),
                operation_id="op-1",
                expected_config_revision=0,
            )
        self.assertEqual(list((self.root / "secrets").iterdir()), [])

    async def test_secret_owner_and_claimed_receipt_are_ledger_bound(self) -> None:
        store = SQLiteSecretStore(self.db, self.root / "secrets", codec=_Codec())
        target = SecretTarget("user-a", "steam", "token")
        receipt = await store.stage(
            b"RAW_TOKEN",
            target=target,
            operation_id="op-1",
            expected_config_revision=3,
        )
        wrong_ref = SecretRef(
            receipt.secret_ref.token, "user-b", "steam", "token", "op-1"
        )
        wrong_owner = SecretOwner("user-b", "steam", "token", "op-1")
        with self.assertRaises(ValueError):
            await store.read(wrong_ref, owner=wrong_owner)
        with self.assertRaises(ValueError):
            await store.delete(wrong_ref, owner=wrong_owner)
        self.assertEqual(
            await store.read(
                receipt.secret_ref,
                owner=SecretOwner("user-a", "steam", "token", "op-1"),
            ),
            b"RAW_TOKEN",
        )
        claimed = await store.claim_for_config(
            receipt,
            target=target,
            operation_id="op-1",
            expected_config_revision=3,
            expected_ledger_revision=receipt.ledger_revision,
        )
        forged = ClaimedSecretReceipt(
            wrong_ref,
            SecretTarget("user-b", "steam", "token"),
            claimed.operation_id,
            claimed.expected_config_revision,
            claimed.ledger_revision,
        )
        with self.assertRaises(RevisionConflict):
            await store.finalize_active(forged, metadata_revision=4)
        pending = await store.pending(target)
        self.assertEqual(pending[0].state.value, "claimed")

    async def test_superseded_login_session_cannot_complete(self) -> None:
        repo = SQLiteAuthRepository(self.db, expire_on_open=False)
        old = await repo.next_generation("user-a", "steam", expected_generation=0)
        new = await repo.next_generation("user-a", "steam", expected_generation=1)
        with self.assertRaises(RevisionConflict):
            await repo.save(
                LoginSession(
                    old.session_id,
                    old.principal_id,
                    old.module_id,
                    old.generation,
                    LoginSessionStatus.COMPLETED,
                    old.expires_at,
                ),
                expected_generation=old.generation,
            )
        self.assertEqual(
            (await repo.current(old.session_id)).status, LoginSessionStatus.CANCELLED
        )
        self.assertEqual(
            (await repo.current(new.session_id)).status, LoginSessionStatus.PENDING
        )

    async def test_expired_pending_cannot_be_completed_with_later_expiry(self) -> None:
        repo = SQLiteAuthRepository(self.db, expire_on_open=False)
        session = await repo.next_generation("user-a", "steam", expected_generation=0)
        connection = sqlite3.connect(self.db)
        try:
            past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
            connection.execute(
                "UPDATE login_sessions SET expires_at=? WHERE session_id=?",
                (past, session.session_id),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(RevisionConflict):
            await repo.save(
                LoginSession(
                    session.session_id,
                    session.principal_id,
                    session.module_id,
                    session.generation,
                    LoginSessionStatus.COMPLETED,
                    datetime.now(UTC) + timedelta(hours=1),
                ),
                expected_generation=session.generation,
            )
        connection = sqlite3.connect(self.db)
        try:
            persisted_status = connection.execute(
                "SELECT status FROM login_sessions WHERE session_id=?",
                (session.session_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(persisted_status, "expired")
        self.assertEqual(
            (await repo.current(session.session_id)).status, LoginSessionStatus.EXPIRED
        )

    async def test_session_owner_and_module_are_bound_to_persisted_row(self) -> None:
        repo = SQLiteAuthRepository(self.db, expire_on_open=False)
        session = await repo.next_generation("user-a", "steam", expected_generation=0)
        with self.assertRaises(ValueError):
            await repo.save(
                LoginSession(
                    session.session_id,
                    "user-b",
                    "other-module",
                    session.generation,
                    LoginSessionStatus.COMPLETED,
                    session.expires_at,
                ),
                expected_generation=session.generation,
            )
        restored = await repo.current(session.session_id)
        self.assertEqual(restored.principal_id, "user-a")
        self.assertEqual(restored.module_id, "steam")
        self.assertEqual(restored.status, LoginSessionStatus.PENDING)

    @staticmethod
    def _grant(
        grant_id: str = "grant-a",
        revision: int = 1,
        *,
        principal_id: str = "user-a",
        module_id: str = "steam",
        account_id: str = "account",
        status: GrantStatus = GrantStatus.ACTIVE,
    ) -> Grant:
        return Grant(
            grant_id,
            revision,
            principal_id,
            module_id,
            account_id,
            ("read",),
            None,
            status,
        )

    async def test_complete_login_rejects_superseded_generation_atomically(
        self,
    ) -> None:
        repo = SQLiteAuthRepository(self.db, expire_on_open=False)
        old = await repo.next_generation("user-a", "steam", expected_generation=0)
        latest = await repo.next_generation("user-a", "steam", expected_generation=1)
        with self.assertRaises(RevisionConflict):
            await repo.complete_login(
                LoginSession(
                    old.session_id,
                    old.principal_id,
                    old.module_id,
                    old.generation,
                    LoginSessionStatus.COMPLETED,
                    old.expires_at,
                ),
                self._grant(),
                expected_generation=old.generation,
                expected_grant_revision=None,
            )
        self.assertEqual(
            (await repo.current(old.session_id)).status, LoginSessionStatus.CANCELLED
        )
        self.assertEqual(
            (await repo.current(latest.session_id)).status, LoginSessionStatus.PENDING
        )
        self.assertIsNone(await repo.current_grant("grant-a"))

    async def test_complete_login_grant_insert_failure_rolls_back_session(self) -> None:
        repo = SQLiteAuthRepository(self.db, expire_on_open=False)
        session = await repo.next_generation("user-a", "steam", expected_generation=0)
        connection = sqlite3.connect(self.db)
        try:
            connection.execute(
                "CREATE TRIGGER reject_grant_insert BEFORE INSERT ON grants "
                "BEGIN SELECT RAISE(ABORT, 'forced grant failure'); END"
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(sqlite3.IntegrityError):
            await repo.complete_login(
                LoginSession(
                    session.session_id,
                    session.principal_id,
                    session.module_id,
                    session.generation,
                    LoginSessionStatus.COMPLETED,
                    session.expires_at,
                ),
                self._grant(),
                expected_generation=session.generation,
                expected_grant_revision=None,
            )
        self.assertEqual(
            (await repo.current(session.session_id)).status, LoginSessionStatus.PENDING
        )
        self.assertIsNone(await repo.current_grant("grant-a"))

    async def test_complete_login_session_write_failure_rolls_back_grant(self) -> None:
        repo = SQLiteAuthRepository(self.db, expire_on_open=False)
        session = await repo.next_generation("user-a", "steam", expected_generation=0)
        connection = sqlite3.connect(self.db)
        try:
            connection.execute(
                "CREATE TRIGGER reject_session_completion "
                "BEFORE UPDATE OF status ON login_sessions "
                "WHEN NEW.status='completed' "
                "BEGIN SELECT RAISE(ABORT, 'forced session failure'); END"
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(sqlite3.IntegrityError):
            await repo.complete_login(
                LoginSession(
                    session.session_id,
                    session.principal_id,
                    session.module_id,
                    session.generation,
                    LoginSessionStatus.COMPLETED,
                    session.expires_at,
                ),
                self._grant(),
                expected_generation=session.generation,
                expected_grant_revision=None,
            )
        self.assertEqual(
            (await repo.current(session.session_id)).status, LoginSessionStatus.PENDING
        )
        self.assertIsNone(await repo.current_grant("grant-a"))

    async def test_complete_login_rejects_persisted_expired_session(self) -> None:
        repo = SQLiteAuthRepository(self.db, expire_on_open=False)
        session = await repo.next_generation("user-a", "steam", expected_generation=0)
        connection = sqlite3.connect(self.db)
        try:
            past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
            connection.execute(
                "UPDATE login_sessions SET expires_at=? WHERE session_id=?",
                (past, session.session_id),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(RevisionConflict):
            await repo.complete_login(
                LoginSession(
                    session.session_id,
                    session.principal_id,
                    session.module_id,
                    session.generation,
                    LoginSessionStatus.COMPLETED,
                    datetime.now(UTC) + timedelta(hours=1),
                ),
                self._grant(),
                expected_generation=session.generation,
                expected_grant_revision=None,
            )
        connection = sqlite3.connect(self.db)
        try:
            status = connection.execute(
                "SELECT status FROM login_sessions WHERE session_id=?",
                (session.session_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(status, "pending")
        self.assertIsNone(await repo.current_grant("grant-a"))

    async def test_complete_login_rotate_and_restart_persist_both_rows(self) -> None:
        repo = SQLiteAuthRepository(self.db, expire_on_open=False)
        original = self._grant()
        await repo.create_grant(original, expected_revision=0)
        session = await repo.next_generation("user-a", "steam", expected_generation=0)
        completed, rotated = await repo.complete_login(
            LoginSession(
                session.session_id,
                session.principal_id,
                session.module_id,
                session.generation,
                LoginSessionStatus.COMPLETED,
                session.expires_at,
            ),
            self._grant(revision=2, account_id="account-rotated"),
            expected_generation=session.generation,
            expected_grant_revision=1,
        )
        self.assertEqual(completed.status, LoginSessionStatus.COMPLETED)
        self.assertEqual(rotated.revision, 2)
        reopened = SQLiteAuthRepository(self.db)
        self.assertEqual(
            (await reopened.current(session.session_id)).status,
            LoginSessionStatus.COMPLETED,
        )
        self.assertEqual((await reopened.current_grant("grant-a")).revision, 2)

    async def test_complete_login_reauthorizes_exact_revoked_grant_revision(
        self,
    ) -> None:
        repo = SQLiteAuthRepository(self.db, expire_on_open=False)
        original = self._grant()
        await repo.create_grant(original, expected_revision=0)
        old_reference = GrantReference(original.grant_id, original.revision)
        revoked = await repo.revoke_grant(
            self._grant(revision=2, status=GrantStatus.REVOKED),
            expected_revision=1,
        )
        session = await repo.next_generation("user-a", "steam", expected_generation=0)
        completed_session = LoginSession(
            session.session_id,
            session.principal_id,
            session.module_id,
            session.generation,
            LoginSessionStatus.COMPLETED,
            session.expires_at,
        )

        with self.assertRaises(RevisionConflict):
            await repo.complete_login(
                completed_session,
                self._grant(
                    revision=3,
                    account_id="different-account",
                ),
                expected_generation=session.generation,
                expected_grant_revision=revoked.revision,
            )
        self.assertEqual(
            (await repo.current(session.session_id)).status,
            LoginSessionStatus.PENDING,
        )
        self.assertEqual(
            (await repo.current_grant(original.grant_id)).status,
            GrantStatus.REVOKED,
        )

        completed, reauthorized = await repo.complete_login(
            completed_session,
            self._grant(revision=3),
            expected_generation=session.generation,
            expected_grant_revision=revoked.revision,
        )
        self.assertEqual(completed.status, LoginSessionStatus.COMPLETED)
        self.assertEqual(
            (reauthorized.grant_id, reauthorized.revision, reauthorized.status),
            (original.grant_id, 3, GrantStatus.ACTIVE),
        )
        self.assertEqual(
            tuple(
                (grant.account_id, grant.revision)
                for grant in await repo.list_for("user-a", "steam")
            ),
            ((original.account_id, 3),),
        )

        service = AuthorizationService(
            repo,
            repo,
            ContextIssuer(),
            secret_available=lambda grant: grant.secret_ref is not None,
        )
        with self.assertRaises(AuthorizationConflict):
            await service._current_grant("user-a", "steam", old_reference)

    async def test_complete_login_checks_expiry_after_waiting_for_write_lock(
        self,
    ) -> None:
        repo = SQLiteAuthRepository(
            SQLiteDatabase(self.db, timeout=4), expire_on_open=False
        )
        session = await repo.next_generation("user-a", "steam", expected_generation=0)
        expiry = datetime.now(UTC) + timedelta(milliseconds=650)
        connection = sqlite3.connect(self.db)
        try:
            connection.execute(
                "UPDATE login_sessions SET expires_at=? WHERE session_id=?",
                (expiry.isoformat(), session.session_id),
            )
            connection.commit()
        finally:
            connection.close()

        lock_started = threading.Event()
        release_lock = threading.Event()

        def hold_write_lock() -> None:
            lock_connection = sqlite3.connect(self.db, timeout=4, isolation_level=None)
            try:
                lock_connection.execute("BEGIN IMMEDIATE")
                lock_started.set()
                if not release_lock.wait(timeout=4):
                    raise TimeoutError("test lock was not released")
                lock_connection.commit()
            finally:
                lock_connection.close()

        holder = threading.Thread(target=hold_write_lock)
        holder.start()
        self.assertTrue(lock_started.wait(timeout=2))
        completion = asyncio.create_task(
            repo.complete_login(
                LoginSession(
                    session.session_id,
                    session.principal_id,
                    session.module_id,
                    session.generation,
                    LoginSessionStatus.COMPLETED,
                    expiry,
                ),
                self._grant(),
                expected_generation=session.generation,
                expected_grant_revision=None,
            )
        )
        await asyncio.sleep(0.9)
        release_lock.set()
        with self.assertRaises(RevisionConflict):
            await asyncio.wait_for(completion, timeout=5)
        holder.join(timeout=2)
        self.assertFalse(holder.is_alive())

        connection = sqlite3.connect(self.db)
        try:
            status = connection.execute(
                "SELECT status FROM login_sessions WHERE session_id=?",
                (session.session_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(status, "pending")
        self.assertIsNone(await repo.current_grant("grant-a"))

    async def test_complete_login_grant_cas_conflict_rolls_back_session(self) -> None:
        repo = SQLiteAuthRepository(self.db, expire_on_open=False)
        original = self._grant()
        await repo.create_grant(original, expected_revision=0)
        await repo.rotate_grant(self._grant(revision=2), expected_revision=1)
        session = await repo.next_generation("user-a", "steam", expected_generation=0)
        with self.assertRaises(RevisionConflict):
            await repo.complete_login(
                LoginSession(
                    session.session_id,
                    session.principal_id,
                    session.module_id,
                    session.generation,
                    LoginSessionStatus.COMPLETED,
                    session.expires_at,
                ),
                self._grant(revision=2, account_id="account-rotated"),
                expected_generation=session.generation,
                expected_grant_revision=1,
            )
        self.assertEqual(
            (await repo.current(session.session_id)).status, LoginSessionStatus.PENDING
        )
        persisted = await repo.current_grant("grant-a")
        self.assertEqual((persisted.revision, persisted.account_id), (2, "account"))

    async def test_revoke_and_completion_race_is_serialized_and_fences_pending(
        self,
    ) -> None:
        repo = SQLiteAuthRepository(self.db, expire_on_open=False)
        original = self._grant()
        await repo.create_grant(original, expected_revision=0)
        session = await repo.next_generation("user-a", "steam", expected_generation=0)
        rotate = LoginSession(
            session.session_id,
            session.principal_id,
            session.module_id,
            session.generation,
            LoginSessionStatus.COMPLETED,
            session.expires_at,
        )

        async def complete() -> object:
            try:
                return await repo.complete_login(
                    rotate,
                    self._grant(revision=2, account_id="account-rotated"),
                    expected_generation=session.generation,
                    expected_grant_revision=1,
                )
            except BaseException as exc:
                return exc

        async def revoke() -> object:
            try:
                return await repo.revoke_grant(
                    self._grant(revision=2, status=GrantStatus.REVOKED),
                    expected_revision=1,
                )
            except BaseException as exc:
                return exc

        results = await asyncio.wait_for(
            asyncio.gather(complete(), revoke()), timeout=5
        )
        self.assertEqual(
            sum(isinstance(result, BaseException) for result in results), 1
        )
        current_session = await repo.current(session.session_id)
        current_grant = await repo.current_grant("grant-a")
        if current_session.status is LoginSessionStatus.COMPLETED:
            self.assertEqual(current_grant.revision, 2)
            self.assertEqual(current_grant.status, GrantStatus.ACTIVE)
        else:
            self.assertEqual(current_session.status, LoginSessionStatus.CANCELLED)
            self.assertEqual(current_grant.revision, 2)
            self.assertEqual(current_grant.status, GrantStatus.REVOKED)

        # Explicitly exercise the revoke-first serialization order, including a
        # callback that proposes a different Grant ID.
        db2 = self.root / "revoke-first.sqlite3"
        repo2 = SQLiteAuthRepository(db2, expire_on_open=False)
        await repo2.create_grant(original, expected_revision=0)
        pending = await repo2.next_generation("user-a", "steam", expected_generation=0)
        await repo2.revoke_grant(
            self._grant(revision=2, status=GrantStatus.REVOKED), expected_revision=1
        )
        with self.assertRaises(RevisionConflict):
            await repo2.complete_login(
                LoginSession(
                    pending.session_id,
                    pending.principal_id,
                    pending.module_id,
                    pending.generation,
                    LoginSessionStatus.COMPLETED,
                    pending.expires_at,
                ),
                self._grant("fresh-grant"),
                expected_generation=pending.generation,
                expected_grant_revision=None,
            )
        self.assertEqual(
            (await repo2.current(pending.session_id)).status,
            LoginSessionStatus.CANCELLED,
        )
        self.assertIsNone(await repo2.current_grant("fresh-grant"))
