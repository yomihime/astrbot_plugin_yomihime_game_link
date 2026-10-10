from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.core.contracts.storage import SecretReceiptState, SecretTarget
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.ports import SecretCompensationState, SecretOwner
from ygl_test_subject.infrastructure.secret_codec import AESGCMSecretCodec
from ygl_test_subject.infrastructure.secret_store import SQLiteSecretStore
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.executor import SQLiteExecutorClosedError
from ygl_test_subject.infrastructure.sqlite.repositories import SQLiteRepositories
from ygl_test_subject.infrastructure.sqlite.repositories_cache_resources import (
    SQLiteResourceRepository,
)
from ygl_test_subject.services.b04_runtime import (
    B04Repositories,
    SQLiteResourceVisibilityProbe,
)

from yomihime_game_link_sdk.storage import OwnerScope, ResourceMetadata


class _Codec:
    def encrypt(self, value: bytes) -> bytes:
        return b"envelope:" + value[::-1]

    def decrypt(self, value: bytes) -> bytes:
        if not value.startswith(b"envelope:"):
            raise ValueError("invalid envelope")
        return value[len(b"envelope:") :][::-1]


class _MutableKeyProvider:
    def __init__(self, key: bytes | None) -> None:
        self.key = key

    def get_key(self) -> bytes:
        if self.key is None:
            raise RuntimeError("private deployment key is unavailable")
        return self.key


class _RegistrationLookup:
    def require_registered(self, *_args: object) -> None:
        return None


class _FileStore:
    def stage(self, *_args: object, **_kwargs: object) -> None:
        return None

    def commit(self, *_args: object, **_kwargs: object) -> None:
        return None

    def read(self, *_args: object, **_kwargs: object) -> None:
        return None

    def discard(self, *_args: object, **_kwargs: object) -> None:
        return None

    def mark_orphan(self, *_args: object, **_kwargs: object) -> None:
        return None


class SQLiteSecretStoreWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "runtime.sqlite3"
        self.secret_root = self.root / "secrets"
        self.database = SQLiteDatabase(self.db_path)
        self.store = SQLiteSecretStore(self.database, self.secret_root, codec=_Codec())

    async def asyncTearDown(self) -> None:
        await self.database.executor.close(timeout=2)
        self.temp.cleanup()

    async def test_aes_gcm_codec_roundtrip_nonce_tamper_and_key_fail_closed(self):
        provider = _MutableKeyProvider(b"a" * 32)
        codec = AESGCMSecretCodec(provider)
        plaintext = b'{"schema":1,"client_secret":"never-on-disk"}'
        first = codec.encrypt(plaintext)
        second = codec.encrypt(plaintext)
        self.assertNotEqual(first, second)
        self.assertNotIn(b"never-on-disk", first)
        self.assertEqual(codec.decrypt(first), plaintext)
        self.assertEqual(codec.decrypt(second), plaintext)

        tampered = bytearray(first)
        tampered[-1] ^= 1
        with self.assertRaisesRegex(ValueError, "unavailable") as changed:
            codec.decrypt(bytes(tampered))
        self.assertNotIn("private", str(changed.exception))

        provider.key = b"b" * 32
        with self.assertRaisesRegex(ValueError, "unavailable"):
            codec.decrypt(first)
        provider.key = None
        with self.assertRaisesRegex(ValueError, "unavailable") as missing:
            codec.encrypt(plaintext)
        self.assertNotIn("private", str(missing.exception))

    async def test_composition_is_io_free_until_async_database_initialization(self):
        B04Repositories(self.database)
        SQLiteRepositories(
            self.database,
            _RegistrationLookup(),
            file_store=_FileStore(),
            secret_store=self.store,
        )

        self.assertFalse(self.db_path.exists())
        self.assertFalse(self.secret_root.exists())
        self.assertEqual(await self.database.executor.initialize(), 100)
        self.assertTrue(self.db_path.is_file())
        self.assertFalse(self.secret_root.exists())

    async def test_secret_receipt_transitions_use_worker_and_detached_results(self):
        worker_threads: list[tuple[int, str]] = []
        loop_thread_id = threading.get_ident()
        original_connect = self.database._connect

        def record_worker_connect():
            thread = threading.current_thread()
            worker_threads.append((threading.get_ident(), thread.name))
            return original_connect()

        self.database._connect = record_worker_connect
        target = SecretTarget("user-a", "package/module", "token")
        owner = SecretOwner("user-a", "package/module", "token", "op-1")
        try:
            receipt = await self.store.stage(
                b"secret-value",
                target=target,
                operation_id=owner.operation_id,
                expected_config_revision=7,
            )
            self.assertEqual((await self.store.pending(target)), (receipt,))
            claimed = await self.store.claim_for_config(
                receipt,
                target=target,
                operation_id=owner.operation_id,
                expected_config_revision=7,
                expected_ledger_revision=receipt.ledger_revision,
            )
            self.assertEqual(claimed.ledger_revision, receipt.ledger_revision + 1)
            pending = await self.store.pending(owner)
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].state.value, "staged")

            await self.store.mark_orphan(
                receipt.secret_ref, "index update failed", owner=owner
            )
            orphaned = await self.store.pending(target)
            self.assertEqual(len(orphaned), 1)
            self.assertEqual(orphaned[0].state, SecretReceiptState.ORPHAN)
            payload_path = self.secret_root / f"{receipt.secret_ref.token}.blob"
            self.assertTrue(payload_path.is_file())

            await self.store.delete(receipt.secret_ref, owner=owner)
            self.assertFalse(payload_path.exists())
            self.assertEqual(await self.store.pending(target), ())
        finally:
            self.database._connect = original_connect

        self.assertTrue(worker_threads)
        self.assertTrue(
            all(
                thread_id != loop_thread_id and name == "yomihime-sqlite-worker"
                for thread_id, name in worker_threads
            )
        )

    async def test_stage_cancelled_before_queue_acceptance_returns_with_capacity_full(
        self,
    ):
        await self.database.executor.initialize()
        started = threading.Event()
        release = threading.Event()

        def block_worker(_unit):
            started.set()
            if not release.wait(2):
                raise TimeoutError("test worker was not released")
            return 1

        blocker = asyncio.create_task(self.database.executor.run_read(block_worker))
        queued_reads: list[asyncio.Task[int]] = []
        stage_task: asyncio.Task[object] | None = None
        try:
            await _wait_for_thread_event(started)
            queued_reads = [
                asyncio.create_task(self.database.executor.run_read(lambda _unit: 1))
                for _ in range(63)
            ]
            await _wait_for_outstanding(self.database.executor, 64)
            stage_task = asyncio.create_task(
                self.store.stage(
                    b"cancel-before-admission",
                    target=SecretTarget("user-a", "module-a", "token"),
                    operation_id="cancel-before-admission",
                    expected_config_revision=0,
                )
            )
            await asyncio.sleep(0.02)
            self.assertEqual(list(self.secret_root.glob("*.blob")), [])
            stage_task.cancel()
            stage_task.cancel()
            done, _ = await asyncio.wait({stage_task}, timeout=0.25)
            self.assertIn(stage_task, done)
            with self.assertRaises(asyncio.CancelledError):
                await stage_task
            self.assertEqual(self.database.executor._outstanding, 64)
            self.assertEqual(list(self.secret_root.glob("*.blob")), [])
        finally:
            release.set()
            await blocker
            if queued_reads:
                await asyncio.gather(*queued_reads)

        self.assertEqual(
            await self.store.pending(SecretTarget("user-a", "module-a", "token")),
            (),
        )
        self.assertEqual(list(self.secret_root.glob("*.blob")), [])

    async def test_accepted_reservation_repeated_cancel_leaves_recoverable_owner(self):
        await self.database.executor.initialize()
        started = threading.Event()
        release = threading.Event()

        def block_worker(_unit):
            started.set()
            if not release.wait(2):
                raise TimeoutError("test worker was not released")
            return 1

        blocker = asyncio.create_task(self.database.executor.run_read(block_worker))
        stage_task: asyncio.Task[object] | None = None
        target = SecretTarget("user-a", "module-a", "token")
        try:
            await _wait_for_thread_event(started)
            stage_task = asyncio.create_task(
                self.store.stage(
                    b"cancel-after-admission",
                    target=target,
                    operation_id="cancel-after-admission",
                    expected_config_revision=0,
                )
            )
            await _wait_for_outstanding(self.database.executor, 2)
            self.assertEqual(list(self.secret_root.glob("*.blob")), [])
            stage_task.cancel()
            await asyncio.sleep(0.01)
            stage_task.cancel()
            await asyncio.sleep(0.01)
            self.assertFalse(stage_task.done())
        finally:
            release.set()
            await blocker

        with self.assertRaises(asyncio.CancelledError):
            await stage_task
        pending = await self.store.pending(target)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].state, SecretReceiptState.RECOVERABLE)
        self.assertEqual(list(self.secret_root.glob("*.blob")), [])

        await self.database.executor.close(timeout=2)
        reopened = SQLiteDatabase(self.db_path)
        reopened_store = SQLiteSecretStore(reopened, self.secret_root, codec=_Codec())
        try:
            pending_after_restart = await reopened_store.pending(target)
            self.assertEqual(pending_after_restart, pending)
            await reopened_store.delete(
                pending[0].secret_ref,
                owner=SecretOwner(
                    "user-a", "module-a", "token", "cancel-after-admission"
                ),
            )
        finally:
            await reopened.executor.close(timeout=2)

    async def test_accepted_finalize_repeated_cancel_keeps_staged_owner(self):
        await self.database.executor.initialize()
        target = SecretTarget("user-a", "module-a", "token")
        finalization_started = threading.Event()
        release_finalization = threading.Event()
        original = self.database.executor.run_transaction
        calls = 0

        async def block_finalize(callback, *, begin_mode="DEFERRED"):
            nonlocal calls
            calls += 1
            if calls != 2:
                return await original(callback, begin_mode=begin_mode)

            def callback_then_block(unit):
                result = callback(unit)
                finalization_started.set()
                if not release_finalization.wait(2):
                    raise TimeoutError("finalize callback was not released")
                return result

            return await original(callback_then_block, begin_mode=begin_mode)

        self.database.executor.run_transaction = block_finalize
        stage_task = asyncio.create_task(
            self.store.stage(
                b"accepted-finalize",
                target=target,
                operation_id="accepted-finalize",
                expected_config_revision=3,
            )
        )
        try:
            await _wait_for_thread_event(finalization_started)
            stage_task.cancel()
            await asyncio.sleep(0.01)
            stage_task.cancel()
            await asyncio.sleep(0.01)
            self.assertFalse(stage_task.done())
        finally:
            release_finalization.set()

        with self.assertRaises(asyncio.CancelledError):
            await stage_task
        pending = await self.store.pending(target)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].state, SecretReceiptState.STAGED)
        owner = SecretOwner("user-a", "module-a", "token", "accepted-finalize")
        self.assertEqual(
            await self.store.read(pending[0].secret_ref, owner=owner),
            b"accepted-finalize",
        )

        await self.database.executor.close(timeout=2)
        reopened = SQLiteDatabase(self.db_path)
        reopened_store = SQLiteSecretStore(reopened, self.secret_root, codec=_Codec())
        try:
            pending_after_restart = await reopened_store.pending(target)
            self.assertEqual(pending_after_restart, pending)
            self.assertEqual(
                await reopened_store.read(pending[0].secret_ref, owner=owner),
                b"accepted-finalize",
            )
            await reopened_store.delete(pending[0].secret_ref, owner=owner)
        finally:
            await reopened.executor.close(timeout=2)

    async def test_closing_executor_leaves_recoverable_owner_for_written_payload(self):
        await self.database.executor.initialize()
        target = SecretTarget("user-a", "module-a", "token")
        original = self.database.executor.run_transaction
        second_transaction = asyncio.Event()
        continue_finalization = asyncio.Event()
        calls = 0

        async def gate_finalization(callback, *, begin_mode="DEFERRED"):
            nonlocal calls
            calls += 1
            if calls == 2:
                second_transaction.set()
                await continue_finalization.wait()
            return await original(callback, begin_mode=begin_mode)

        self.database.executor.run_transaction = gate_finalization
        stage_task = asyncio.create_task(
            self.store.stage(
                b"closing-preserves-owner",
                target=target,
                operation_id="closing-preserves-owner",
                expected_config_revision=0,
            )
        )
        try:
            await asyncio.wait_for(second_transaction.wait(), timeout=1)
            blobs = list(self.secret_root.glob("*.blob"))
            self.assertEqual(len(blobs), 1)
            pending_before_close = await self.store.pending(target)
            self.assertEqual(len(pending_before_close), 1)
            self.assertEqual(
                pending_before_close[0].state, SecretReceiptState.RECOVERABLE
            )
            await self.database.executor.close(timeout=1)
            continue_finalization.set()
            with self.assertRaises(SQLiteExecutorClosedError):
                await stage_task
        finally:
            continue_finalization.set()
            if not stage_task.done():
                await stage_task

        reopened = SQLiteDatabase(self.db_path)
        reopened_store = SQLiteSecretStore(reopened, self.secret_root, codec=_Codec())
        try:
            owner = SecretOwner(
                "user-a", "module-a", "token", "closing-preserves-owner"
            )
            pending = await reopened_store.pending(owner)
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].state, SecretCompensationState.RECOVERABLE)
            self.assertTrue(
                (self.secret_root / f"{pending[0].secret_ref.token}.blob").is_file()
            )
            cleaned = await reopened_store.recover(pending[0], owner=owner)
            self.assertEqual(cleaned.state, SecretCompensationState.CLEANED)
            self.assertEqual(await reopened_store.pending(owner), ())
            self.assertEqual(list(self.secret_root.glob("*.blob")), [])
        finally:
            await reopened.executor.close(timeout=2)

    async def test_bounded_visibility_probe_uses_worker_and_fails_closed(self):
        resources = SQLiteResourceRepository(self.database)
        await resources.register(
            validate_contract(
                ResourceMetadata(
                    "asset_privateprobe",
                    "text/plain",
                    OwnerScope.user("alice"),
                    12,
                    None,
                    False,
                    1,
                )
            )
        )
        probe = SQLiteResourceVisibilityProbe(self.database)
        loop_thread_id = threading.get_ident()
        worker_threads: list[tuple[int, str]] = []
        original_connect = self.database.connect

        def observe_connect() -> sqlite3.Connection:
            thread = threading.current_thread()
            worker_threads.append((threading.get_ident(), thread.name))
            return original_connect()

        with patch.object(self.database, "connect", side_effect=observe_connect):
            self.assertTrue(
                await probe.contains_non_public_resource_reference(
                    "prefix-asset_privateprobe-suffix"
                )
            )
        self.assertEqual(len(worker_threads), 1)
        self.assertNotEqual(worker_threads[0][0], loop_thread_id)
        self.assertEqual(worker_threads[0][1], "yomihime-sqlite-worker")

        class FailingDatabase(SQLiteDatabase):
            def connect(self):
                raise sqlite3.OperationalError("injected probe failure")

        failing_database = FailingDatabase(self.root / "probe-failure.sqlite3")
        failing_probe = SQLiteResourceVisibilityProbe(failing_database)
        try:
            with self.assertRaisesRegex(RuntimeError, "probe failed"):
                await failing_probe.contains_non_public_resource_reference("anything")
        finally:
            await failing_database.executor.close(timeout=2)


async def _wait_for_thread_event(event: threading.Event) -> None:
    deadline = asyncio.get_running_loop().time() + 1
    while not event.is_set() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.005)
    if not event.is_set():
        raise AssertionError("worker callback did not start")


async def _wait_for_outstanding(executor, expected: int) -> None:
    deadline = asyncio.get_running_loop().time() + 1
    while (
        executor._outstanding < expected
        and asyncio.get_running_loop().time() < deadline
    ):
        await asyncio.sleep(0.005)
    if executor._outstanding != expected:
        raise AssertionError(
            f"expected {expected} accepted jobs, found {executor._outstanding}"
        )
