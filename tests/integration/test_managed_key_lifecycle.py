"""Independent synthetic inventory, restart, and credential lifecycle checks."""

from __future__ import annotations

import asyncio
import base64
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.core.context_issuer import ContextIssuer
from ygl_test_subject.core.contracts.services import Grant, GrantStatus
from ygl_test_subject.core.contracts.storage import SecretTarget
from ygl_test_subject.core.ports import RevisionConflict, SecretOwner
from ygl_test_subject.infrastructure.key_provider import SecretKeyUnavailable
from ygl_test_subject.infrastructure.managed_key_file import ManagedKeyFileError
from ygl_test_subject.infrastructure.managed_key_manager import (
    ManagedKeyConflict,
    ManagedKeyManager,
)
from ygl_test_subject.infrastructure.secret_codec import AESGCMSecretCodec
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

from yomihime_game_link_sdk.contexts import InvocationOrigin
from yomihime_game_link_sdk.storage import SecretRef


class _SyntheticFiles:
    """Keeps Core lifecycle tests independent of platform file permissions."""

    def __init__(self):
        self.saved: dict[str, bytes] = {}
        self.fail_create_once = False

    def entries(self):
        return tuple(f"master-{key_id}.key" for key_id in sorted(self.saved))

    def create(self, key_id, key):
        if self.fail_create_once:
            self.fail_create_once = False
            raise ManagedKeyFileError()
        if key_id in self.saved:
            raise ManagedKeyFileError("key_file_exists")
        self.saved[key_id] = key

    def read(self, key_id):
        try:
            value = self.saved[key_id]
        except KeyError:
            raise ManagedKeyFileError() from None
        if type(value) is not bytes or len(value) != 32:
            raise ManagedKeyFileError("key_file_invalid")
        return value

    def confirm_durable(self, key_id):
        return self.read(key_id)


class ManagedKeyLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db_path = self.root / "runtime.sqlite3"
        self.database = SQLiteDatabase(self.db_path)
        self.database.initialize()
        self.files = _SyntheticFiles()
        self.manager = self._manager(self.database)
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    async def asyncTearDown(self):
        await self.database.executor.close()

    def _manager(self, database):
        return ManagedKeyManager(
            database, self.root, self.root / "secrets", files=self.files
        )

    @staticmethod
    def _cas(status):
        return {
            "expected_revision": status["revision"],
            "expected_generation": status["generation"],
            "inventory_digest": status["inventory_digest"],
        }

    def _secret(self, *, module, token, field="client_secret", key=None):
        secret_dir = self.root / "secrets"
        secret_dir.mkdir(exist_ok=True)
        if key is None:
            key = self.manager.get_key()

        class _Key:
            def get_key(self):
                return key

        (secret_dir / f"{token}.blob").write_bytes(
            AESGCMSecretCodec(_Key()).encrypt(b"synthetic-credential-only")
        )
        connection = self.database.connect()
        try:
            connection.execute(
                "INSERT INTO config_state(principal_id,module_id,revision) "
                "VALUES('owner',?,1)",
                (module,),
            )
            connection.execute(
                "INSERT INTO config_entries(principal_id,module_id,field,secret_token,"
                "secret_principal_id,secret_module_id,secret_field,"
                "secret_operation_id,secret_state,revision) "
                "VALUES('owner',?,?,?,?,?,?,'op1','active',1)",
                (module, field, token, "owner", module, field),
            )
            connection.execute(
                "INSERT INTO secret_receipts(secret_token,principal_id,module_id,field,"
                "operation_id,expected_config_revision,ledger_revision,state,payload_name,"
                "key_generation) VALUES(?,'owner',? ,?,'op1',0,1,'active',?,?)",
                (token, module, field, f"{token}.blob", self._generation()),
            )
        finally:
            connection.close()

    def _generation(self):
        connection = self.database.connect()
        try:
            return connection.execute(
                "SELECT generation FROM managed_master_key WHERE singleton=1"
            ).fetchone()[0]
        finally:
            connection.close()

    async def test_restart_and_reset_keep_ordinary_config_but_revoke_all_owners(self):
        first = await self.manager.startup()
        self.assertEqual(first["state"], "ready")
        old_key = self.manager.get_key()
        self._secret(module="ff14/ff14", token="secret_ff14")
        self._secret(module="uninstalled/other", token="secret_other")
        connection = self.database.connect()
        try:
            connection.execute(
                "INSERT INTO config_entries(principal_id,module_id,field,value_json,revision) "
                "VALUES('owner','ff14/ff14','theme','\"paper\"',1)"
            )
            connection.execute(
                "INSERT INTO grants(grant_id,revision,principal_id,module_id,account_id,"
                "scopes_json,secret_token,secret_principal_id,secret_module_id,"
                "secret_field,secret_operation_id,status) "
                "VALUES('grant1',1,'owner','uninstalled/other','acct','[\"read\"]',"
                "'secret_other','owner','uninstalled/other','client_secret','op1','active')"
            )
            connection.execute(
                "INSERT INTO login_sessions(session_id,principal_id,module_id,generation,"
                "status,expires_at) VALUES('session1','owner','uninstalled/other',1,"
                "'pending','2099-01-01T00:00:00Z')"
            )
        finally:
            connection.close()
        before = await self.manager.status()
        self.assertEqual(
            {
                row["module_id"]: (row["config_count"], row["grant_count"])
                for row in before["impact"]
            },
            {"ff14/ff14": (1, 0), "uninstalled/other": (1, 1)},
        )
        second_database = SQLiteDatabase(self.db_path)
        second = self._manager(second_database)
        try:
            resumed = await second.startup()
            self.assertEqual(resumed["state"], "ready")
            self.assertEqual(resumed["generation"], first["generation"])
            self.assertEqual(second.get_key(), old_key)
            reset = await second.reset(
                **self._cas(before), confirmation="RESET ENCRYPTED CREDENTIALS"
            )
            self.assertEqual(reset["state"], "ready")
            self.assertNotEqual(second.get_key(), old_key)
            with self.assertRaises(SecretKeyUnavailable):
                self.manager.get_key()
            connection = self.database.connect()
            try:
                ordinary = connection.execute(
                    "SELECT value_json FROM config_entries WHERE field='theme'"
                ).fetchone()[0]
                self.assertEqual(ordinary, '"paper"')
                secrets = connection.execute(
                    "SELECT module_id,secret_token,secret_state FROM config_entries "
                    "WHERE field='client_secret' ORDER BY module_id"
                ).fetchall()
                self.assertEqual(
                    [tuple(row) for row in secrets],
                    [
                        ("ff14/ff14", None, "tombstoned"),
                        ("uninstalled/other", None, "tombstoned"),
                    ],
                )
                grant = connection.execute(
                    "SELECT grant_id,account_id,scopes_json,secret_token,status "
                    "FROM grants WHERE grant_id='grant1'"
                ).fetchone()
                self.assertEqual(
                    tuple(grant),
                    ("grant1", "acct", '["read"]', None, "unavailable"),
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT status FROM login_sessions WHERE session_id='session1'"
                    ).fetchone()[0],
                    "cancelled",
                )
            finally:
                connection.close()
        finally:
            await second_database.executor.close()

    async def test_orphan_payload_blocks_automatic_creation_and_reset(self):
        secret_dir = self.root / "secrets"
        secret_dir.mkdir()
        (secret_dir / "unowned.blob").write_bytes(b"synthetic-orphan")
        state = await self.manager.startup()
        self.assertEqual(state["state"], "unknown")
        self.assertGreater(state["unknown_count"], 0)
        self.assertEqual(self.files.saved, {})
        with self.assertRaisesRegex(ManagedKeyConflict, "inventory_unknown"):
            await self.manager.reset(
                **self._cas(state), confirmation="RESET ENCRYPTED CREDENTIALS"
            )

    async def test_concurrent_reset_commits_one_generation(self):
        before = await self.manager.startup()
        peer_database = SQLiteDatabase(self.db_path)
        peer = self._manager(peer_database)
        try:
            self.assertEqual((await peer.startup())["state"], "ready")
            common = {
                **self._cas(before),
                "confirmation": "RESET ENCRYPTED CREDENTIALS",
            }
            outcomes = await asyncio.gather(
                self.manager.reset(**common),
                peer.reset(**common),
                return_exceptions=True,
            )
            successes = [item for item in outcomes if isinstance(item, dict)]
            conflicts = [
                item for item in outcomes if isinstance(item, ManagedKeyConflict)
            ]
            self.assertEqual(len(successes), 1)
            self.assertEqual(len(conflicts), 1)
            self.assertIn(conflicts[0].code, {"stale_inventory", "operation_changed"})
            self.assertEqual(successes[0]["generation"], before["generation"] + 1)
            self.assertEqual(len(self.files.saved), 2)
        finally:
            await peer_database.executor.close()

    async def test_interrupted_file_creation_retries_one_journal_candidate(self):
        self.files.fail_create_once = True
        with self.assertRaises(ManagedKeyFileError):
            await self.manager.startup()
        pending = await self.manager.status()
        self.assertEqual(pending["state"], "initialization_failed")
        self.assertEqual(self.files.saved, {})
        recovered = await self.manager.retry(**self._cas(pending))
        self.assertEqual(recovered["state"], "ready")
        self.assertEqual(len(self.files.saved), 1)
        self.assertEqual(recovered["generation"], 1)

    async def test_wrong_environment_and_corrupt_payload_do_not_replace_key(self):
        ready = await self.manager.startup()
        key = self.manager.get_key()
        self._secret(module="ff14/ff14", token="secret_ff14")
        os.environ["YGL_SECRET_KEY"] = base64.b64encode(b"x" * 32).decode()
        conflict = await self.manager.status()
        self.assertEqual(conflict["state"], "source_conflict")
        with self.assertRaises(SecretKeyUnavailable):
            self.manager.get_key()
        os.environ.pop("YGL_SECRET_KEY")
        self.assertEqual(self.manager.get_key(), key)
        active = await self.manager.status()
        before_files = dict(self.files.saved)
        blob_path = self.root / "secrets" / "secret_ff14.blob"
        blob_path.write_bytes(b"damaged")
        connection = self.database.connect()
        try:
            before_rows = (
                tuple(connection.execute("SELECT * FROM config_entries")),
                tuple(connection.execute("SELECT * FROM secret_receipts")),
            )
        finally:
            connection.close()
        restarted = self._manager(SQLiteDatabase(self.db_path))
        try:
            result = await restarted.startup()
            self.assertEqual(result["state"], "unknown")
            self.assertEqual(result["reason_code"], "inventory_unknown")
            self.assertGreater(result["unknown_count"], 0)
            self.assertEqual(result["impact"], active["impact"])
            self.assertEqual(result["revision"], ready["revision"])
            self.assertEqual(self.files.saved, before_files)
            self.assertEqual(ready["generation"], result["generation"])
            self.assertEqual(blob_path.read_bytes(), b"damaged")
            connection = restarted.database.connect()
            try:
                self.assertEqual(
                    (
                        tuple(connection.execute("SELECT * FROM config_entries")),
                        tuple(connection.execute("SELECT * FROM secret_receipts")),
                    ),
                    before_rows,
                )
            finally:
                connection.close()
            with self.assertRaises(SecretKeyUnavailable):
                restarted.get_key()
            with self.assertRaisesRegex(ManagedKeyConflict, "inventory_unknown"):
                await restarted.reset(
                    **self._cas(result), confirmation="RESET ENCRYPTED CREDENTIALS"
                )
        finally:
            await restarted.database.executor.close()

    async def test_healthy_wrong_restore_keeps_ready_key_cipher_and_no_journal(self):
        await self.manager.startup()
        key = self.manager.get_key()
        self._secret(module="ff14/ff14", token="secret_ff14")
        before = await self.manager.status()
        old_files = dict(self.files.saved)
        blob_path = self.root / "secrets" / "secret_ff14.blob"
        old_blob = blob_path.read_bytes()
        with self.assertRaises(ManagedKeyConflict):
            await self.manager.restore(
                base64.b64encode(b"wrong-key".ljust(32, b"x")).decode(),
                **self._cas(before),
            )
        self.assertEqual(await self.manager.status(), before)
        self.assertEqual(self.manager.get_key(), key)
        self.assertEqual(self.files.saved, old_files)
        self.assertEqual(blob_path.read_bytes(), old_blob)
        connection = self.database.connect()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM managed_master_key_operations"
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()

    async def test_damaged_selected_file_requires_correct_restore_without_overwrite(
        self,
    ):
        selected = await self.manager.startup()
        original_key = self.manager.get_key()
        self._secret(module="ff14/ff14", token="secret_ff14")
        self._secret(module="uninstalled/other", token="secret_other")
        old_id = next(iter(self.files.saved))
        self.files.saved[old_id] = b"broken-selected-file"
        ciphertext = {
            name: (self.root / "secrets" / name).read_bytes()
            for name in ("secret_ff14.blob", "secret_other.blob")
        }
        blocked = await self.manager.status()
        self.assertFalse(blocked["ready"])
        self.assertEqual(blocked["generation"], selected["generation"])
        with self.assertRaises(ManagedKeyConflict):
            await self.manager.restore(
                base64.b64encode(b"x" * 32).decode(), **self._cas(blocked)
            )
        self.assertEqual(self.files.saved, {old_id: b"broken-selected-file"})
        self.assertEqual(await self.manager.status(), blocked)
        restored = await self.manager.restore(
            base64.b64encode(original_key).decode(), **self._cas(blocked)
        )
        self.assertTrue(restored["ready"])
        self.assertEqual(restored["generation"], selected["generation"])
        self.assertEqual(self.manager.get_key(), original_key)
        self.assertEqual(self.files.saved[old_id], b"broken-selected-file")
        self.assertEqual(len(self.files.saved), 2)
        for name, original in ciphertext.items():
            self.assertEqual((self.root / "secrets" / name).read_bytes(), original)

    async def test_half_written_reset_remains_blocked_then_retries_new_candidate(self):
        ready = await self.manager.startup()
        self._secret(module="ff14/ff14", token="secret_ff14")
        old_files = dict(self.files.saved)
        old_blob = (self.root / "secrets" / "secret_ff14.blob").read_bytes()
        before = await self.manager.status()
        arguments = {
            **self._cas(before),
            "confirmation": "RESET ENCRYPTED CREDENTIALS",
        }
        original_create = self.files.create

        def partial_write(key_id, _key):
            self.files.saved[key_id] = b"partial-candidate"
            raise ManagedKeyFileError("synthetic_flush_failure")

        with patch.object(self.files, "create", side_effect=partial_write):
            with self.assertRaises(ManagedKeyFileError):
                await self.manager.reset(**arguments)
        self.assertEqual(len(self.files.saved), 2)
        blocked = await self.manager.status()
        self.assertFalse(blocked["ready"])
        self.assertEqual(blocked["generation"], ready["generation"])
        restart_database = SQLiteDatabase(self.db_path)
        restart = self._manager(restart_database)
        try:
            still_blocked = await restart.startup()
            self.assertFalse(still_blocked["ready"])
            self.assertEqual(still_blocked["generation"], ready["generation"])
        finally:
            await restart_database.executor.close()
        self.assertEqual(
            (self.root / "secrets" / "secret_ff14.blob").read_bytes(), old_blob
        )
        for key_id, old_bytes in old_files.items():
            self.assertEqual(self.files.saved[key_id], old_bytes)
        with patch.object(self.files, "create", side_effect=original_create):
            recovered = await self.manager.reset(**arguments)
        self.assertTrue(recovered["ready"])
        self.assertEqual(recovered["generation"], ready["generation"] + 1)
        self.assertEqual(len(self.files.saved), 3)
        self.assertIn(b"partial-candidate", self.files.saved.values())
        for key_id, old_bytes in old_files.items():
            self.assertEqual(self.files.saved[key_id], old_bytes)
        connection = self.database.connect()
        try:
            self.assertEqual(
                tuple(
                    connection.execute(
                        "SELECT secret_token,secret_state FROM config_entries "
                        "WHERE module_id='ff14/ff14' AND field='client_secret'"
                    ).fetchone()
                ),
                (None, "tombstoned"),
            )
        finally:
            connection.close()

    async def test_mismatched_receipt_and_retired_live_row_never_report_ready(self):
        ready = await self.manager.startup()
        self._secret(module="uninstalled/other", token="secret_other")
        connection = self.database.connect()
        try:
            connection.execute(
                "UPDATE secret_receipts SET field='wrong_field' "
                "WHERE secret_token='secret_other'"
            )
        finally:
            connection.close()
        mismatched = await self.manager.status()
        self.assertFalse(mismatched["ready"])
        self.assertEqual(mismatched["state"], "unknown")
        self.assertGreater(mismatched["unknown_count"], 0)
        with self.assertRaises(ManagedKeyConflict):
            await self.manager.reset(
                **self._cas(mismatched),
                confirmation="RESET ENCRYPTED CREDENTIALS",
            )
        connection = self.database.connect()
        try:
            connection.execute(
                "UPDATE secret_receipts SET field='client_secret' "
                "WHERE secret_token='secret_other'"
            )
        finally:
            connection.close()
        repaired = await self.manager.status()
        self.assertTrue(repaired["ready"])
        reset = await self.manager.reset(
            **self._cas(repaired), confirmation="RESET ENCRYPTED CREDENTIALS"
        )
        self.assertEqual(reset["generation"], ready["generation"] + 1)
        connection = self.database.connect()
        try:
            connection.execute(
                "UPDATE secret_receipts SET state='orphan' "
                "WHERE secret_token='secret_other'"
            )
        finally:
            connection.close()
        old_live = await self.manager.status()
        self.assertEqual(old_live["state"], "unknown")
        self.assertFalse(old_live["ready"])
        self.assertGreater(old_live["unknown_count"], 0)

    async def test_pending_reset_fences_old_runtime_key_before_candidate_commit(self):
        ready = await self.manager.startup()
        old_key = self.manager.get_key()
        self._secret(module="ff14/ff14", token="secret_ff14")
        before = await self.manager.status()

        def interrupted(key_id, _key):
            self.files.saved[key_id] = b"partial-candidate"
            raise ManagedKeyFileError("synthetic_flush_failure")

        with patch.object(self.files, "create", side_effect=interrupted):
            with self.assertRaises(ManagedKeyFileError):
                await self.manager.reset(
                    **self._cas(before),
                    confirmation="RESET ENCRYPTED CREDENTIALS",
                )
        pending = await self.manager.status()
        self.assertFalse(pending["ready"])
        self.assertEqual(pending["generation"], ready["generation"])
        self.assertEqual(self.files.saved[next(iter(self.files.saved))], old_key)
        with self.assertRaises(SecretKeyUnavailable):
            self.manager.get_key()

    async def test_pending_reset_allows_exact_revoke_and_abandons_old_candidate(self):
        await self.manager.startup()
        self._secret(module="ff14/ff14", token="secret_ff14")
        repo = SQLiteAuthRepository(self.database, key_manager=self.manager)
        secret = SecretRef("secret_ff14", "owner", "ff14/ff14", "client_secret", "op1")
        grant = Grant(
            "grant-invalidation",
            1,
            "owner",
            "ff14/ff14",
            "acct-invalidation",
            ("read",),
            secret,
            GrantStatus.ACTIVE,
        )
        await repo.create_grant(grant, expected_revision=0)
        ordinary = Grant(
            "ordinary",
            1,
            "owner",
            "ff14/ff14",
            "acct-ordinary",
            ("read",),
            None,
            GrantStatus.ACTIVE,
        )
        await repo.create_grant(ordinary, expected_revision=0)
        connection = self.database.connect()
        try:
            connection.execute(
                "INSERT INTO cache_entries(cache_key,visibility,user_id,grant_id,"
                "grant_revision,payload_json,expires_at,source_version,revision,invalidated) "
                "VALUES('private','authorized','owner',?,1,'{}',?,1,1,0)",
                (grant.grant_id, (datetime.now(UTC) + timedelta(days=1)).isoformat()),
            )
            connection.execute(
                "INSERT INTO login_sessions(session_id,principal_id,module_id,generation,"
                "status,expires_at) VALUES('pending-login','owner','ff14/ff14',1,'pending',?)",
                ((datetime.now(UTC) + timedelta(minutes=10)).isoformat(),),
            )
        finally:
            connection.close()
        blob_path = self.root / "secrets" / "secret_ff14.blob"
        old_blob = blob_path.read_bytes()
        old_keys = dict(self.files.saved)

        def interrupted(key_id, _key):
            self.files.saved[key_id] = b"partial-candidate"
            raise ManagedKeyFileError("synthetic_flush_failure")

        before = await self.manager.status()
        with patch.object(self.files, "create", side_effect=interrupted):
            with self.assertRaises(ManagedKeyFileError):
                await self.manager.reset(
                    **self._cas(before),
                    confirmation="RESET ENCRYPTED CREDENTIALS",
                )
        store = SQLiteSecretStore(
            self.database,
            self.root / "secrets",
            codec=AESGCMSecretCodec(self.manager),
            key_manager=self.manager,
        )
        with self.assertRaises(SecretStoreUnavailable):
            await store.stage(
                b"synthetic-new",
                target=SecretTarget("owner", "ff14/ff14", "client_secret"),
                operation_id="op2",
                expected_config_revision=1,
            )
        connection = self.database.connect()
        try:
            pending = connection.execute(
                "SELECT operation_id,candidate_key_id,state FROM managed_master_key_operations "
                "WHERE state='preparing'"
            ).fetchone()
            self.assertIsNotNone(pending)
            pending_id, candidate_id = (
                pending["operation_id"],
                pending["candidate_key_id"],
            )
        finally:
            connection.close()
        with self.assertRaises(RevisionConflict):
            await repo.revoke_grant_with_invalidation(grant, expected_revision=0)
        with self.assertRaises(ValueError):
            await repo.revoke_grant_with_invalidation(
                replace(grant, principal_id="impostor"), expected_revision=1
            )
        connection = self.database.connect()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT state FROM managed_master_key_operations WHERE operation_id=?",
                    (pending_id,),
                ).fetchone()[0],
                "preparing",
            )
            self.assertEqual(
                connection.execute(
                    "SELECT invalidated FROM cache_entries WHERE cache_key='private'"
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()
        revoked = await repo.revoke_grant_with_invalidation(grant, expected_revision=1)
        self.assertEqual((revoked.status, revoked.revision), (GrantStatus.REVOKED, 2))
        self.assertEqual(await repo.current_grant(grant.grant_id), revoked)
        connection = self.database.connect()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT state FROM managed_master_key_operations WHERE operation_id=?",
                    (pending_id,),
                ).fetchone()[0],
                "abandoned",
            )
            self.assertEqual(
                connection.execute(
                    "SELECT invalidated FROM cache_entries WHERE cache_key='private'"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT status FROM login_sessions WHERE session_id='pending-login'"
                ).fetchone()[0],
                "cancelled",
            )
        finally:
            connection.close()
        self.assertEqual(blob_path.read_bytes(), old_blob)
        self.assertEqual(self.files.saved[candidate_id], b"partial-candidate")
        for key_id, key_bytes in old_keys.items():
            self.assertEqual(self.files.saved[key_id], key_bytes)
        with self.assertRaises(ManagedKeyConflict):
            await self.manager.reset(
                **self._cas(before), confirmation="RESET ENCRYPTED CREDENTIALS"
            )
        self.assertEqual(
            (await self.manager.status())["generation"], before["generation"]
        )
        revoked = await repo.revoke_grant(ordinary, expected_revision=1)
        self.assertEqual(revoked.status, GrantStatus.REVOKED)

    async def test_revoke_before_reset_keeps_terminal_grant_and_old_cipher_bytes(self):
        ready = await self.manager.startup()
        self._secret(module="ff14/ff14", token="secret_ff14")
        repo = SQLiteAuthRepository(self.database, key_manager=self.manager)
        grant = Grant(
            "grant-before-reset",
            1,
            "owner",
            "ff14/ff14",
            "acct-before-reset",
            ("read",),
            SecretRef("secret_ff14", "owner", "ff14/ff14", "client_secret", "op1"),
            GrantStatus.ACTIVE,
        )
        await repo.create_grant(grant, expected_revision=0)
        blob_path = self.root / "secrets" / "secret_ff14.blob"
        old_blob = blob_path.read_bytes()
        old_keys = dict(self.files.saved)
        connection = self.database.connect()
        try:
            connection.execute(
                "INSERT INTO cache_entries(cache_key,visibility,user_id,grant_id,"
                "grant_revision,payload_json,expires_at,source_version,revision,invalidated) "
                "VALUES('private','authorized','owner',?,1,'{}',?,1,1,0)",
                (grant.grant_id, (datetime.now(UTC) + timedelta(days=1)).isoformat()),
            )
        finally:
            connection.close()
        revoked = await repo.revoke_grant_with_invalidation(grant, expected_revision=1)
        self.assertEqual((revoked.status, revoked.revision), (GrantStatus.REVOKED, 2))
        connection = self.database.connect()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT invalidated FROM cache_entries WHERE cache_key='private'"
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()
        before = await self.manager.status()
        self.assertTrue(before["ready"])
        reset = await self.manager.reset(
            **self._cas(before), confirmation="RESET ENCRYPTED CREDENTIALS"
        )
        self.assertEqual(reset["generation"], ready["generation"] + 1)
        after = await repo.current_grant(grant.grant_id)
        self.assertEqual(after.status, GrantStatus.REVOKED)
        self.assertIsNone(after.secret_ref)
        self.assertEqual(after.revision, 3)
        self.assertEqual(blob_path.read_bytes(), old_blob)
        for key_id, key_bytes in old_keys.items():
            self.assertEqual(self.files.saved[key_id], key_bytes)

    async def test_reset_fences_old_grant_ticket_and_private_authority(self):
        await self.manager.startup()
        self._secret(module="ff14/ff14", token="secret_ff14")
        repo = SQLiteAuthRepository(self.database, key_manager=self.manager)
        grant = Grant(
            "grant-old-ticket",
            1,
            "owner",
            "ff14/ff14",
            "acct-old-ticket",
            ("read",),
            SecretRef("secret_ff14", "owner", "ff14/ff14", "client_secret", "op1"),
            GrantStatus.ACTIVE,
        )
        await repo.create_grant(grant, expected_revision=0)
        issuer = ContextIssuer()
        service = AuthorizationService(
            repo,
            repo,
            issuer,
            secret_available=lambda item: item.secret_ref is not None,
        )
        old_ticket = issuer.issue(
            origin=InvocationOrigin.COMMAND,
            module_id="ff14/ff14",
            module_epoch=1,
            registry_revision=1,
            actor_id="owner",
            conversation_id="private-owner",
            adapter_id="synthetic",
            grant_id=grant.grant_id,
            grant_revision=grant.revision,
        )
        try:
            self.assertEqual(await service.require_current_grant(old_ticket), grant)
            before = await self.manager.status()
            await self.manager.reset(
                **self._cas(before), confirmation="RESET ENCRYPTED CREDENTIALS"
            )
            current = await repo.current_grant(grant.grant_id)
            self.assertEqual(current.status, GrantStatus.UNAVAILABLE)
            self.assertIsNone(current.secret_ref)
            self.assertEqual(current.revision, grant.revision + 1)
            with self.assertRaises(AuthorizationConflict):
                await service.require_current_grant(old_ticket)
            self.assertIsNone(await service.status(old_ticket))
        finally:
            issuer.release(old_ticket)

    async def _reset_wins_before_cleanup_transaction(self, action):
        await self.manager.startup()
        self._secret(module="ff14/ff14", token="secret_ff14")
        before = await self.manager.status()
        secret = SecretRef("secret_ff14", "owner", "ff14/ff14", "client_secret", "op1")
        owner = SecretOwner("owner", "ff14/ff14", "client_secret", "op1")
        blob_path = self.root / "secrets" / "secret_ff14.blob"
        old_blob = blob_path.read_bytes()
        store = SQLiteSecretStore(
            self.database,
            self.root / "secrets",
            codec=AESGCMSecretCodec(self.manager),
            key_manager=self.manager,
        )
        original_run = self.database.executor.run_transaction
        injected = False

        async def reset_before_cleanup(worker, *args, **kwargs):
            nonlocal injected
            if worker.__name__ in {"cleanup", "mark"} and not injected:
                injected = True
                await self.manager.reset(
                    **self._cas(before), confirmation="RESET ENCRYPTED CREDENTIALS"
                )
            return await original_run(worker, *args, **kwargs)

        with patch.object(
            self.database.executor, "run_transaction", side_effect=reset_before_cleanup
        ):
            with self.assertRaises(SecretStoreUnavailable):
                if action == "delete":
                    await store.delete(secret, owner=owner)
                else:
                    await store.mark_orphan(secret, "synthetic-race", owner=owner)
        self.assertTrue(injected)
        self.assertEqual(blob_path.read_bytes(), old_blob)
        connection = self.database.connect()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT state FROM secret_receipts WHERE secret_token='secret_ff14'"
                ).fetchone()[0],
                "tombstoned",
            )
        finally:
            connection.close()

    async def test_reset_before_delete_transaction_preserves_old_blob_and_receipt(self):
        await self._reset_wins_before_cleanup_transaction("delete")

    async def test_reset_before_orphan_transaction_preserves_tombstone(self):
        await self._reset_wins_before_cleanup_transaction("mark_orphan")

    async def _cleanup_commits_before_reset(self, action):
        ready = await self.manager.startup()
        self._secret(module="ff14/ff14", token="secret_ff14")
        connection = self.database.connect()
        try:
            connection.execute(
                "DELETE FROM config_entries WHERE secret_token='secret_ff14'"
            )
        finally:
            connection.close()
        secret = SecretRef("secret_ff14", "owner", "ff14/ff14", "client_secret", "op1")
        owner = SecretOwner("owner", "ff14/ff14", "client_secret", "op1")
        blob_path = self.root / "secrets" / "secret_ff14.blob"
        old_blob = blob_path.read_bytes()
        store = SQLiteSecretStore(
            self.database,
            self.root / "secrets",
            codec=AESGCMSecretCodec(self.manager),
            key_manager=self.manager,
        )
        if action == "delete":
            await store.delete(secret, owner=owner)
            self.assertFalse(blob_path.exists())
        else:
            await store.mark_orphan(secret, "synthetic-compensation", owner=owner)
            self.assertEqual(blob_path.read_bytes(), old_blob)
        before = await self.manager.status()
        self.assertTrue(before["ready"])
        after = await self.manager.reset(
            **self._cas(before), confirmation="RESET ENCRYPTED CREDENTIALS"
        )
        self.assertEqual(after["generation"], ready["generation"] + 1)
        connection = self.database.connect()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT state FROM secret_receipts WHERE secret_token='secret_ff14'"
                ).fetchone()[0],
                "tombstoned",
            )
        finally:
            connection.close()

    async def test_delete_commits_before_reset_without_reference_resurrection(self):
        await self._cleanup_commits_before_reset("delete")

    async def test_orphan_commits_before_reset_without_reference_resurrection(self):
        await self._cleanup_commits_before_reset("mark_orphan")


if __name__ == "__main__":
    unittest.main()
