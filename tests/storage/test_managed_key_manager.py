"""Synthetic key lifecycle; no deployment key or running Host is accessed."""

from __future__ import annotations

import asyncio
import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.infrastructure.key_provider import SecretKeyUnavailable
from ygl_test_subject.infrastructure.managed_key_file import ManagedKeyFileError
from ygl_test_subject.infrastructure.managed_key_manager import (
    ManagedKeyConflict,
    ManagedKeyManager,
)
from ygl_test_subject.infrastructure.secret_codec import AESGCMSecretCodec
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase


class _Files:
    def __init__(self) -> None:
        self.saved: dict[str, bytes] = {}
        self.confirm_fails = False

    def entries(self) -> tuple[str, ...]:
        return tuple(sorted(f"master-{key_id}.key" for key_id in self.saved))

    def create(self, key_id: str, key: bytes) -> None:
        if key_id in self.saved:
            raise ManagedKeyFileError("key_file_exists")
        self.saved[key_id] = key

    def read(self, key_id: str) -> bytes:
        try:
            value = self.saved[key_id]
        except KeyError:
            raise ManagedKeyFileError() from None
        if len(value) != 32:
            raise ManagedKeyFileError("key_file_invalid")
        return value

    def confirm_durable(self, key_id: str) -> bytes:
        if self.confirm_fails:
            raise ManagedKeyFileError()
        return self.read(key_id)


class _Key:
    def __init__(self, key: bytes) -> None:
        self.key = key

    def get_key(self) -> bytes:
        return self.key


class ManagedKeyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = SQLiteDatabase(self.root / "runtime.sqlite3")
        self.database.initialize()
        self.files = _Files()
        self.manager = ManagedKeyManager(
            self.database, self.root, self.root / "secrets", files=self.files
        )
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    async def asyncTearDown(self) -> None:
        await self.database.executor.close()

    def _insert_secret(self, key: bytes) -> None:
        token = "secret_synthetic_01"
        root = self.root / "secrets"
        root.mkdir()
        (root / f"{token}.blob").write_bytes(
            AESGCMSecretCodec(_Key(key)).encrypt(b"SYNTHETIC ONLY")
        )
        connection = self.database.connect()
        try:
            connection.execute(
                "INSERT INTO config_state(principal_id,module_id,revision) VALUES('owner','game_link/ff14',1)"
            )
            connection.execute(
                "INSERT INTO config_entries(principal_id,module_id,field,secret_token,secret_principal_id,secret_module_id,secret_field,secret_operation_id,secret_state,revision) "
                "VALUES('owner','game_link/ff14','client_secret',?,'owner','game_link/ff14','client_secret','op1','active',1)",
                (token,),
            )
            connection.execute(
                "INSERT INTO secret_receipts(secret_token,principal_id,module_id,field,operation_id,expected_config_revision,ledger_revision,state,payload_name) "
                "VALUES(?,'owner','game_link/ff14','client_secret','op1',0,1,'active',?)",
                (token, f"{token}.blob"),
            )
        finally:
            connection.close()

    async def test_two_managers_publish_one_key(self) -> None:
        other_database = SQLiteDatabase(self.root / "runtime.sqlite3", timeout=5.0)
        other = ManagedKeyManager(
            other_database, self.root, self.root / "secrets", files=self.files
        )
        try:
            results = await asyncio.gather(self.manager.startup(), other.startup())
        finally:
            await other_database.executor.close()
        self.assertEqual([result["state"] for result in results], ["ready", "ready"])
        self.assertEqual(len(self.files.saved), 1)
        self.assertEqual(self.manager.get_key(), other.get_key())

    async def test_legacy_ciphertext_blocks_creation_and_restore_validates_all(
        self,
    ) -> None:
        old_key = bytes(range(32))
        self._insert_secret(old_key)
        status = await self.manager.startup()
        self.assertEqual(status["state"], "needs_restore")
        self.assertEqual(self.files.saved, {})
        kwargs = {
            "expected_revision": status["revision"],
            "expected_generation": status["generation"],
            "inventory_digest": status["inventory_digest"],
        }
        with self.assertRaises(ManagedKeyConflict):
            await self.manager.restore(base64.b64encode(b"x" * 32).decode(), **kwargs)
        self.assertEqual(self.files.saved, {})
        restored = await self.manager.restore(
            base64.b64encode(old_key).decode(), **kwargs
        )
        self.assertEqual(restored["state"], "ready")
        self.assertEqual(self.manager.get_key(), old_key)

    async def test_reset_invalidates_references_and_recognizes_retired_files(
        self,
    ) -> None:
        old_key = bytes(range(32))
        self._insert_secret(old_key)
        status = await self.manager.startup()
        status = await self.manager.restore(
            base64.b64encode(old_key).decode(),
            expected_revision=status["revision"],
            expected_generation=status["generation"],
            inventory_digest=status["inventory_digest"],
        )
        before = self.manager.get_key()
        with self.assertRaises(ManagedKeyConflict):
            await self.manager.reset(
                expected_revision=status["revision"],
                expected_generation=status["generation"],
                inventory_digest=status["inventory_digest"],
                confirmation="wrong",
            )
        reset = await self.manager.reset(
            expected_revision=status["revision"],
            expected_generation=status["generation"],
            inventory_digest=status["inventory_digest"],
            confirmation="RESET ENCRYPTED CREDENTIALS",
        )
        self.assertEqual(reset["state"], "ready")
        self.assertNotEqual(before, self.manager.get_key())
        self.assertEqual(reset["unknown_count"], 0)
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT secret_token,secret_state FROM config_entries WHERE field='client_secret'"
            ).fetchone()
            self.assertEqual(tuple(row), (None, "tombstoned"))
        finally:
            connection.close()

    async def test_explicit_empty_environment_never_falls_back(self) -> None:
        os.environ["YGL_SECRET_KEY"] = ""
        status = await self.manager.startup()
        self.assertEqual(status["state"], "initialization_failed")
        self.assertEqual(self.files.saved, {})

    async def test_corrupt_selected_file_restores_to_new_id_without_overwrite(
        self,
    ) -> None:
        old_key = bytes(range(32))
        self._insert_secret(old_key)
        status = await self.manager.startup()
        status = await self.manager.restore(
            base64.b64encode(old_key).decode(),
            expected_revision=status["revision"],
            expected_generation=status["generation"],
            inventory_digest=status["inventory_digest"],
        )
        old_id = next(iter(self.files.saved))
        self.files.saved[old_id] = b"x" * 32
        unavailable = await self.manager.status()
        self.assertEqual(unavailable["reason_code"], "managed_key_conflict")
        with self.assertRaises(ManagedKeyConflict):
            await self.manager.restore(
                base64.b64encode(b"y" * 32).decode(),
                expected_revision=unavailable["revision"],
                expected_generation=unavailable["generation"],
                inventory_digest=unavailable["inventory_digest"],
            )
        restored = await self.manager.restore(
            base64.b64encode(old_key).decode(),
            expected_revision=unavailable["revision"],
            expected_generation=unavailable["generation"],
            inventory_digest=unavailable["inventory_digest"],
        )
        self.assertEqual(restored["state"], "ready")
        self.assertEqual(restored["unknown_count"], 0)
        self.assertEqual(self.files.saved[old_id], b"x" * 32)
        self.assertEqual(len(self.files.saved), 2)
        self.assertEqual(self.manager.get_key(), old_key)

    async def test_matching_environment_retires_corrupt_managed_file(self) -> None:
        old_key = bytes(range(32))
        self._insert_secret(old_key)
        status = await self.manager.startup()
        status = await self.manager.restore(
            base64.b64encode(old_key).decode(),
            expected_revision=status["revision"],
            expected_generation=status["generation"],
            inventory_digest=status["inventory_digest"],
        )
        old_id = next(iter(self.files.saved))
        self.files.saved[old_id] = b"x" * 32
        os.environ["YGL_SECRET_KEY"] = base64.b64encode(old_key).decode()
        unavailable = await self.manager.status()
        restored = await self.manager.restore(
            base64.b64encode(old_key).decode(),
            expected_revision=unavailable["revision"],
            expected_generation=unavailable["generation"],
            inventory_digest=unavailable["inventory_digest"],
        )
        self.assertEqual(restored["state"], "ready")
        self.assertEqual(restored["source"], "environment")
        self.assertEqual(restored["unknown_count"], 0)
        self.assertEqual(self.files.saved[old_id], b"x" * 32)

    async def test_live_reference_to_tombstoned_or_wrong_owner_is_unknown(self) -> None:
        self._insert_secret(bytes(range(32)))
        connection = self.database.connect()
        try:
            connection.execute(
                "UPDATE secret_receipts SET state='tombstoned' "
                "WHERE secret_token='secret_synthetic_01'"
            )
        finally:
            connection.close()
        status = await self.manager.startup()
        self.assertEqual(status["state"], "unknown")
        self.assertGreater(status["unknown_count"], 0)
        connection = self.database.connect()
        try:
            connection.execute(
                "UPDATE secret_receipts SET state='active' "
                "WHERE secret_token='secret_synthetic_01'"
            )
            connection.execute(
                "UPDATE config_entries SET secret_principal_id='other' "
                "WHERE secret_token='secret_synthetic_01'"
            )
        finally:
            connection.close()
        status = await self.manager.startup()
        self.assertEqual(status["state"], "unknown")
        self.assertGreater(status["unknown_count"], 0)

    async def test_half_written_initialize_candidate_is_abandoned_on_retry(
        self,
    ) -> None:
        real_create = self.files.create

        def interrupted(key_id: str, _key: bytes) -> None:
            self.files.saved[key_id] = b"partial"
            raise ManagedKeyFileError()

        with patch.object(self.files, "create", side_effect=interrupted):
            with self.assertRaises(ManagedKeyFileError):
                await self.manager.startup()
        pending = await self.manager.status()
        self.assertEqual(pending["reason_code"], "operation_incomplete")
        self.assertEqual(len(self.files.saved), 1)
        with patch.object(self.files, "create", side_effect=real_create):
            recovered = await self.manager.retry(
                expected_revision=pending["revision"],
                expected_generation=pending["generation"],
                inventory_digest=pending["inventory_digest"],
            )
        self.assertEqual(recovered["state"], "ready")
        self.assertEqual(recovered["unknown_count"], 0)
        self.assertIn(b"partial", self.files.saved.values())
        self.assertEqual(len(self.files.saved), 2)

    async def test_half_written_reset_candidate_requires_confirmed_retry(self) -> None:
        status = await self.manager.startup()
        real_create = self.files.create

        def interrupted(key_id: str, _key: bytes) -> None:
            self.files.saved[key_id] = b"partial"
            raise ManagedKeyFileError()

        arguments = {
            "expected_revision": status["revision"],
            "expected_generation": status["generation"],
            "inventory_digest": status["inventory_digest"],
            "confirmation": "RESET ENCRYPTED CREDENTIALS",
        }
        with patch.object(self.files, "create", side_effect=interrupted):
            with self.assertRaises(ManagedKeyFileError):
                await self.manager.reset(**arguments)
        pending = await self.manager.status()
        self.assertEqual(pending["reason_code"], "operation_incomplete")
        with self.assertRaises(SecretKeyUnavailable):
            self.manager.get_key()
        with patch.object(self.files, "create", side_effect=real_create):
            recovered = await self.manager.reset(**arguments)
        self.assertEqual(recovered["state"], "ready")
        self.assertEqual(recovered["unknown_count"], 0)
        self.assertIn(b"partial", self.files.saved.values())

    async def test_full_initialize_candidate_needs_fresh_durability_ack(self) -> None:
        original_create = self.files.create

        def unacknowledged_create(key_id: str, key: bytes) -> None:
            original_create(key_id, key)
            raise ManagedKeyFileError()

        with patch.object(self.files, "create", side_effect=unacknowledged_create):
            with self.assertRaises(ManagedKeyFileError):
                await self.manager.startup()
        candidate_id, candidate_key = next(iter(self.files.saved.items()))
        self.files.confirm_fails = True
        with self.assertRaises(ManagedKeyFileError):
            await self.manager.startup()
        self.assertEqual(
            (await self.manager.status())["reason_code"], "operation_incomplete"
        )
        self.assertEqual(self.files.saved, {candidate_id: candidate_key})
        with self.assertRaises(SecretKeyUnavailable):
            self.manager.get_key()
        self.files.confirm_fails = False
        self.assertEqual((await self.manager.startup())["state"], "ready")
        self.assertEqual(self.files.saved, {candidate_id: candidate_key})
        self.assertEqual(self.manager.get_key(), candidate_key)

    async def test_full_reset_candidate_needs_fresh_durability_ack(self) -> None:
        ready = await self.manager.startup()
        old_ids = set(self.files.saved)
        arguments = {
            "expected_revision": ready["revision"],
            "expected_generation": ready["generation"],
            "inventory_digest": ready["inventory_digest"],
            "confirmation": "RESET ENCRYPTED CREDENTIALS",
        }
        original_create = self.files.create

        def unacknowledged_create(key_id: str, key: bytes) -> None:
            original_create(key_id, key)
            raise ManagedKeyFileError()

        with patch.object(self.files, "create", side_effect=unacknowledged_create):
            with self.assertRaises(ManagedKeyFileError):
                await self.manager.reset(**arguments)
        candidate_id = next(
            key_id for key_id in self.files.saved if key_id not in old_ids
        )
        candidate_key = self.files.saved[candidate_id]
        self.files.confirm_fails = True
        with self.assertRaises(ManagedKeyFileError):
            await self.manager.reset(**arguments)
        self.assertEqual(
            (await self.manager.status())["reason_code"], "operation_incomplete"
        )
        self.assertEqual(self.files.saved[candidate_id], candidate_key)
        with self.assertRaises(SecretKeyUnavailable):
            self.manager.get_key()
        self.files.confirm_fails = False
        self.assertEqual((await self.manager.reset(**arguments))["state"], "ready")
        self.assertEqual(self.files.saved[candidate_id], candidate_key)
        self.assertEqual(len(self.files.saved), 2)
        self.assertEqual(self.manager.get_key(), candidate_key)

    async def test_full_restore_candidate_needs_fresh_durability_ack(self) -> None:
        key = bytes(range(32))
        self._insert_secret(key)
        status = await self.manager.startup()
        arguments = {
            "expected_revision": status["revision"],
            "expected_generation": status["generation"],
            "inventory_digest": status["inventory_digest"],
        }
        encoded = base64.b64encode(key).decode()
        original_create = self.files.create

        def unacknowledged_create(key_id: str, candidate: bytes) -> None:
            original_create(key_id, candidate)
            raise ManagedKeyFileError()

        with patch.object(self.files, "create", side_effect=unacknowledged_create):
            with self.assertRaises(ManagedKeyFileError):
                await self.manager.restore(encoded, **arguments)
        candidate_id, candidate_key = next(iter(self.files.saved.items()))
        self.files.confirm_fails = True
        with self.assertRaises(ManagedKeyFileError):
            await self.manager.restore(encoded, **arguments)
        self.assertEqual(
            (await self.manager.status())["reason_code"], "operation_incomplete"
        )
        self.assertEqual(self.files.saved, {candidate_id: candidate_key})
        self.files.confirm_fails = False
        self.assertEqual(
            (await self.manager.restore(encoded, **arguments))["state"], "ready"
        )
        self.assertEqual(self.files.saved, {candidate_id: candidate_key})
        self.assertEqual(self.manager.get_key(), key)

    async def test_durable_initialize_candidate_is_reused_by_concurrent_retry(
        self,
    ) -> None:
        original_create = self.files.create

        def interrupted_after_flush(key_id: str, key: bytes) -> None:
            original_create(key_id, key)
            raise ManagedKeyFileError()

        with patch.object(self.files, "create", side_effect=interrupted_after_flush):
            with self.assertRaises(ManagedKeyFileError):
                await self.manager.startup()
        key_id, durable_key = next(iter(self.files.saved.items()))
        pending = await self.manager.status()
        arguments = {
            "expected_revision": pending["revision"],
            "expected_generation": pending["generation"],
            "inventory_digest": pending["inventory_digest"],
        }
        databases = [
            SQLiteDatabase(self.root / "runtime.sqlite3", timeout=5.0) for _ in range(2)
        ]
        managers = [
            ManagedKeyManager(
                database, self.root, self.root / "secrets", files=self.files
            )
            for database in databases
        ]
        try:
            results = await asyncio.gather(
                *(manager.retry(**arguments) for manager in managers)
            )
            self.assertEqual(
                [result["state"] for result in results], ["ready", "ready"]
            )
            self.assertEqual(self.files.saved, {key_id: durable_key})
            self.assertEqual(
                [manager.get_key() for manager in managers], [durable_key] * 2
            )
        finally:
            for database in databases:
                await database.executor.close()

    async def test_durable_initialize_candidate_is_reused_by_fresh_startup(
        self,
    ) -> None:
        original_create = self.files.create

        def interrupted_after_flush(key_id: str, key: bytes) -> None:
            original_create(key_id, key)
            raise ManagedKeyFileError()

        with patch.object(self.files, "create", side_effect=interrupted_after_flush):
            with self.assertRaises(ManagedKeyFileError):
                await self.manager.startup()
        key_id, durable_key = next(iter(self.files.saved.items()))
        database = SQLiteDatabase(self.root / "runtime.sqlite3", timeout=5.0)
        resumed = ManagedKeyManager(
            database, self.root, self.root / "secrets", files=self.files
        )
        try:
            result = await resumed.startup()
            self.assertEqual(result["state"], "ready")
            self.assertEqual(self.files.saved, {key_id: durable_key})
            self.assertEqual(resumed.get_key(), durable_key)
        finally:
            await database.executor.close()

    async def test_durable_reset_candidate_is_reused_after_restart(self) -> None:
        ready = await self.manager.startup()
        original_ids = set(self.files.saved)
        original_create = self.files.create

        def interrupted_after_flush(key_id: str, key: bytes) -> None:
            original_create(key_id, key)
            raise ManagedKeyFileError()

        arguments = {
            "expected_revision": ready["revision"],
            "expected_generation": ready["generation"],
            "inventory_digest": ready["inventory_digest"],
            "confirmation": "RESET ENCRYPTED CREDENTIALS",
        }
        with patch.object(self.files, "create", side_effect=interrupted_after_flush):
            with self.assertRaises(ManagedKeyFileError):
                await self.manager.reset(**arguments)
        candidate_id = next(
            key_id for key_id in self.files.saved if key_id not in original_ids
        )
        durable_key = self.files.saved[candidate_id]
        database = SQLiteDatabase(self.root / "runtime.sqlite3", timeout=5.0)
        resumed = ManagedKeyManager(
            database, self.root, self.root / "secrets", files=self.files
        )
        try:
            result = await resumed.reset(**arguments)
            self.assertEqual(result["state"], "ready")
            self.assertEqual(len(self.files.saved), 2)
            self.assertEqual(resumed.get_key(), durable_key)
        finally:
            await database.executor.close()

    async def test_durable_restore_candidate_requires_matching_input(self) -> None:
        original_key = bytes(range(32))
        self._insert_secret(original_key)
        status = await self.manager.startup()
        original_create = self.files.create

        def interrupted_after_flush(key_id: str, key: bytes) -> None:
            original_create(key_id, key)
            raise ManagedKeyFileError()

        arguments = {
            "expected_revision": status["revision"],
            "expected_generation": status["generation"],
            "inventory_digest": status["inventory_digest"],
        }
        encoded = base64.b64encode(original_key).decode()
        with patch.object(self.files, "create", side_effect=interrupted_after_flush):
            with self.assertRaises(ManagedKeyFileError):
                await self.manager.restore(encoded, **arguments)
        candidate_id, durable_key = next(iter(self.files.saved.items()))
        connection = self.database.connect()
        try:
            pending_id = connection.execute(
                "SELECT operation_id FROM managed_master_key_operations WHERE state='preparing'"
            ).fetchone()[0]
        finally:
            connection.close()
        with self.assertRaises(ManagedKeyConflict):
            await self.manager.restore(
                base64.b64encode(b"x" * 32).decode(), **arguments
            )
        connection = self.database.connect()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT operation_id FROM managed_master_key_operations WHERE state='preparing'"
                ).fetchone()[0],
                pending_id,
            )
        finally:
            connection.close()
        self.assertEqual(self.files.saved, {candidate_id: durable_key})
        database = SQLiteDatabase(self.root / "runtime.sqlite3", timeout=5.0)
        resumed = ManagedKeyManager(
            database, self.root, self.root / "secrets", files=self.files
        )
        try:
            restored = await resumed.restore(encoded, **arguments)
            self.assertEqual(restored["state"], "ready")
            self.assertEqual(self.files.saved, {candidate_id: durable_key})
            self.assertEqual(resumed.get_key(), original_key)
        finally:
            await database.executor.close()

    async def test_old_generation_live_orphan_blocks_ready_after_reset(self) -> None:
        self._insert_secret(bytes(range(32)))
        status = await self.manager.startup()
        status = await self.manager.restore(
            base64.b64encode(bytes(range(32))).decode(),
            expected_revision=status["revision"],
            expected_generation=status["generation"],
            inventory_digest=status["inventory_digest"],
        )
        reset = await self.manager.reset(
            expected_revision=status["revision"],
            expected_generation=status["generation"],
            inventory_digest=status["inventory_digest"],
            confirmation="RESET ENCRYPTED CREDENTIALS",
        )
        self.assertEqual(reset["state"], "ready")
        connection = self.database.connect()
        try:
            connection.execute(
                "UPDATE secret_receipts SET state='orphan' "
                "WHERE secret_token='secret_synthetic_01'"
            )
        finally:
            connection.close()
        status = await self.manager.status()
        self.assertEqual(status["state"], "unknown")
        self.assertGreater(status["unknown_count"], 0)

    async def test_restore_classifies_io_and_crypto_failure_without_writing(
        self,
    ) -> None:
        original_key = bytes(range(32))
        self._insert_secret(original_key)
        status = await self.manager.startup()
        arguments = {
            "expected_revision": status["revision"],
            "expected_generation": status["generation"],
            "inventory_digest": status["inventory_digest"],
        }
        encoded = base64.b64encode(original_key).decode()
        for patched, target, failure, expected in (
            (
                "read",
                "pathlib.Path.read_bytes",
                PermissionError("synthetic-denied"),
                "inventory_unknown",
            ),
            (
                "cipher",
                "ygl_test_subject.infrastructure.managed_key_manager.AESGCM",
                RuntimeError("synthetic-unavailable"),
                "encryption_component_unavailable",
            ),
            (
                "decrypt_value_error",
                "ygl_test_subject.infrastructure.managed_key_manager.AESGCMSecretCodec._decrypt_payload",
                ValueError("synthetic-component-failure"),
                "encryption_component_unavailable",
            ),
        ):
            with self.subTest(fault=patched), patch(target, side_effect=failure):
                with self.assertRaises(ManagedKeyConflict) as raised:
                    await self.manager.restore(encoded, **arguments)
                self.assertEqual(raised.exception.code, expected)
            self.assertEqual(self.files.saved, {})
            connection = self.database.connect()
            try:
                selector = connection.execute(
                    "SELECT source,generation,revision FROM managed_master_key"
                ).fetchone()
                pending = connection.execute(
                    "SELECT count(*) FROM managed_master_key_operations"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(tuple(selector), ("unselected", 0, 1))
            self.assertEqual(pending, 0)

    async def test_invalid_ciphertext_envelope_is_unknown_not_a_wrong_key(self) -> None:
        key = bytes(range(32))
        self._insert_secret(key)
        blob = self.root / "secrets" / "secret_synthetic_01.blob"
        blob.write_bytes(b"synthetic-malformed")
        status = await self.manager.startup()
        with self.assertRaises(ManagedKeyConflict) as raised:
            await self.manager.restore(
                base64.b64encode(key).decode(),
                expected_revision=status["revision"],
                expected_generation=status["generation"],
                inventory_digest=status["inventory_digest"],
            )
        self.assertEqual(raised.exception.code, "inventory_unknown")
        self.assertEqual(self.files.saved, {})

    async def test_selected_status_distinguishes_io_and_component_from_bad_cipher(
        self,
    ) -> None:
        key = bytes(range(32))
        self._insert_secret(key)
        initial = await self.manager.startup()
        await self.manager.restore(
            base64.b64encode(key).decode(),
            expected_revision=initial["revision"],
            expected_generation=initial["generation"],
            inventory_digest=initial["inventory_digest"],
        )
        with patch("pathlib.Path.read_bytes", side_effect=PermissionError()):
            blocked = await self.manager.status()
        self.assertEqual(
            (blocked["state"], blocked["reason_code"]), ("unknown", "inventory_unknown")
        )
        self.assertGreater(blocked["unknown_count"], 0)
        with patch(
            "ygl_test_subject.infrastructure.managed_key_manager.AESGCM",
            side_effect=RuntimeError("synthetic-unavailable"),
        ):
            unavailable = await self.manager.status()
        self.assertEqual(
            (unavailable["state"], unavailable["reason_code"]),
            ("initialization_failed", "encryption_component_unavailable"),
        )

    async def test_pending_restore_rejects_wrong_key_before_candidate_changes(
        self,
    ) -> None:
        original_key = bytes(range(32))
        self._insert_secret(original_key)
        status = await self.manager.startup()
        arguments = {
            "expected_revision": status["revision"],
            "expected_generation": status["generation"],
            "inventory_digest": status["inventory_digest"],
        }
        blob = self.root / "secrets" / "secret_synthetic_01.blob"
        original_blob = blob.read_bytes()

        def interrupted_partial_create(key_id: str, key: bytes) -> None:
            self.files.saved[key_id] = b"partial"
            raise ManagedKeyFileError("key_file_invalid")

        with patch.object(self.files, "create", side_effect=interrupted_partial_create):
            with self.assertRaises(ManagedKeyFileError):
                await self.manager.restore(
                    base64.b64encode(original_key).decode(), **arguments
                )
        saved = dict(self.files.saved)
        connection = self.database.connect()
        try:
            before = tuple(
                connection.execute(
                    "SELECT source,generation,revision,key_id FROM managed_master_key"
                ).fetchone()
            )
            journal = tuple(
                tuple(row)
                for row in connection.execute(
                    "SELECT operation_id,state,candidate_key_id FROM managed_master_key_operations"
                )
            )
            receipt = tuple(
                connection.execute(
                    "SELECT state,payload_name,key_generation FROM secret_receipts"
                ).fetchone()
            )
        finally:
            connection.close()
        with self.assertRaises(ManagedKeyConflict) as raised:
            await self.manager.restore(
                base64.b64encode(b"x" * 32).decode(), **arguments
            )
        self.assertEqual(raised.exception.code, "key_does_not_decrypt_inventory")
        connection = self.database.connect()
        try:
            self.assertEqual(
                tuple(
                    connection.execute(
                        "SELECT source,generation,revision,key_id FROM managed_master_key"
                    ).fetchone()
                ),
                before,
            )
            self.assertEqual(
                tuple(
                    tuple(row)
                    for row in connection.execute(
                        "SELECT operation_id,state,candidate_key_id FROM managed_master_key_operations"
                    )
                ),
                journal,
            )
            self.assertEqual(
                tuple(
                    connection.execute(
                        "SELECT state,payload_name,key_generation FROM secret_receipts"
                    ).fetchone()
                ),
                receipt,
            )
        finally:
            connection.close()
        self.assertEqual(self.files.saved, saved)
        self.assertEqual(blob.read_bytes(), original_blob)


if __name__ == "__main__":
    unittest.main()
