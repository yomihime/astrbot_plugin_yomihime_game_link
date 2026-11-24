"""Crash-summary rows and ciphertext are owned by one credential issuance attempt."""

import asyncio
import secrets
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.core.ports import SecretOwner
from ygl_test_subject.infrastructure.secret_codec import AESGCMSecretCodec
from ygl_test_subject.infrastructure.secret_store import SQLiteSecretStore
from ygl_test_subject.services.player_issuer import BoundPlayerIssuer
from ygl_test_subject.services.player_source import PlayerReadPolicy

from tests.fixtures.b03_runtime import build_runtime


class CredentialCompensation(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory(dir=Path.cwd())))
        runtime = await build_runtime(root)
        self.executor = runtime.database.executor
        self.addAsyncCleanup(self.executor.close)
        self.directory = root / "player-credentials"
        key = secrets.token_bytes(32)
        self.store = SQLiteSecretStore(
            runtime.database,
            self.directory,
            codec=AESGCMSecretCodec(SimpleNamespace(get_key=lambda: key)),
        )
        self.grants = runtime.repositories.authorization
        self.policy = PlayerReadPolicy(
            "sample/alpha", "catalog", "example.test", "/items", "private.read"
        )
        self.issuer = BoundPlayerIssuer(
            SimpleNamespace(policy=self.policy), self.store, self.grants
        )
        self.committed = {}
        for player in ("alice", "bob"):
            owner = self.owner(player, "completed")
            token = secrets.token_urlsafe()
            grant = await self.stage(owner, token)
            await self.grants.create_grant(grant, expected_revision=0)
            self.committed[player] = (owner, grant, token.encode())
        self.existing_files = set(self.directory.glob("*.blob"))

    def owner(self, principal, operation):
        return SecretOwner(
            principal, self.policy.module_id, self.policy.field, operation
        )

    async def stage(self, owner, token):
        return await self.issuer._stage_grant(
            owner, {"sub": owner.principal_id, "exp": int(time.time()) + 300}, token
        )

    async def check_existing(self):
        for owner, grant, material in self.committed.values():
            self.assertEqual(
                (await self.grants.current_grant(grant.grant_id)).secret_ref,
                grant.secret_ref,
            )
            self.assertTrue(
                await self.store.read(grant.secret_ref, owner=owner) == material
            )
        self.assertEqual(set(self.directory.glob("*.blob")), self.existing_files)

    async def test_cancel_after_accepted_finalize_drains_receipt_before_propagating(
        self,
    ):
        accepted, release = threading.Event(), threading.Event()
        cleanup_started, finish_cleanup = asyncio.Event(), asyncio.Event()
        transaction, pending = self.executor.run_transaction, self.store.pending

        async def gated(callback, **kwargs):
            if callback.__name__ != "finalize":
                return await transaction(callback, **kwargs)

            def finalize_held(unit):
                value = callback(unit)
                accepted.set()
                if not release.wait(5):
                    raise TimeoutError("test did not release SQLite")
                return value

            return await transaction(finalize_held, **kwargs)

        async def paused(owner):
            cleanup_started.set()
            await finish_cleanup.wait()
            return await pending(owner)

        owner = self.owner("alice", "in-flight")
        with (
            patch.object(self.executor, "run_transaction", gated),
            patch.object(self.store, "pending", paused),
        ):
            task = asyncio.create_task(self.stage(owner, secrets.token_urlsafe()))
            try:
                self.assertTrue(await asyncio.to_thread(accepted.wait, 5))
                self.assertEqual(
                    len(set(self.directory.glob("*.blob")) - self.existing_files), 1
                )
                task.cancel()
                release.set()
                await asyncio.wait_for(cleanup_started.wait(), 5)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
            finally:
                release.set()
                finish_cleanup.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(await self.store.pending(owner), ())
        for _ in range(2):
            await self.issuer.recover_failed_attempt(owner, RuntimeError())
            for saved, _, _ in self.committed.values():
                await self.issuer.recover_failed_attempt(saved, RuntimeError())
        await self.check_existing()

    async def test_written_payload_without_finalize_is_recovered(self):
        transaction = self.executor.run_transaction

        async def fail_before_finalize(callback, **kwargs):
            if callback.__name__ == "finalize":
                self.assertEqual(
                    len(set(self.directory.glob("*.blob")) - self.existing_files), 1
                )
                raise RuntimeError("finalize unavailable")
            return await transaction(callback, **kwargs)

        owner = self.owner("bob", "failed-finalize")
        with (
            patch.object(self.executor, "run_transaction", fail_before_finalize),
            self.assertRaisesRegex(RuntimeError, "finalize unavailable"),
        ):
            await self.stage(owner, secrets.token_urlsafe())
        self.assertEqual(await self.store.pending(owner), ())
        await self.check_existing()
