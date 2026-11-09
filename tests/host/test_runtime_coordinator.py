"""Synthetic lifecycle and real local file-lock contention tests."""

from __future__ import annotations

import asyncio
import importlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ygl_test_subject.bootstrap.coordinator import CoordinatorConflict, get_coordinator
from ygl_test_subject.infrastructure.runtime_owner_lock import (
    RuntimeOwnerLock,
    RuntimeOwnerLockError,
)


def proof(token: str, owner: object) -> dict[str, object]:
    del owner
    return {
        "owner_token": token,
        "tracked_inflight": 0,
        "cleanup_complete": True,
        "durable_fence": True,
        "resources_closed": True,
    }


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.context = object()
        self.scope = (self.temp.name, self.context)
        self.kernel = get_coordinator()
        self.first = object()
        self.digest = "a" * 64

    async def asyncTearDown(self) -> None:
        # Isolated test scopes can intentionally remain parked after failure.
        root = str(Path(self.temp.name).resolve())
        with self.kernel.lock:
            record = self.kernel.scopes.pop(root, None)
        if record is not None:
            record.file_lock.close()

    async def acquire(self, epoch: int = 0, owner: object | None = None):
        return await self.kernel.acquire_core(
            self.scope, owner or self.first, epoch, self.digest
        )

    async def test_takeover_waits_for_exact_cleanup_and_tickets(self) -> None:
        first = await self.acquire()
        token = first["owner_token"]
        self.assertEqual((await self.acquire(1))["state"], "parked")
        gate_calls = []
        self.assertTrue(
            self.kernel.register_gate(self.scope, token, lambda: gate_calls.append(1))
        )
        ticket = self.kernel.queue_ticket(self.scope, token)
        self.assertTrue(self.kernel.mark_executing(self.scope, token, ticket))
        event = asyncio.Event()
        factories = []

        async def durable() -> bool:
            return True

        async def cleanup() -> None:
            factories.append(1)
            await event.wait()

        cleanup_id = self.kernel.sync_fence(self.scope, token, cleanup, durable, proof)
        task = self.kernel.scopes[str(Path(self.temp.name).resolve())].cleanup
        self.assertEqual(gate_calls, [1])
        self.assertFalse(self.kernel.mark_executing(self.scope, token, ticket))
        self.assertIsNone(self.kernel.queue_ticket(self.scope, token))
        self.assertEqual(
            self.kernel.sync_fence(self.scope, token, cleanup, durable, proof),
            cleanup_id,
        )
        self.assertEqual(
            (await self.kernel.await_quiet(self.scope, token, 0.01))["state"], "parked"
        )
        waiter = asyncio.create_task(self.kernel.await_quiet(self.scope, token))
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertIs(
            self.kernel.scopes[str(Path(self.temp.name).resolve())].cleanup, task
        )
        self.assertEqual((await self.acquire(1, object()))["state"], "parked")
        event.set()
        await task
        self.assertEqual(factories, [1])
        self.assertEqual(
            (await self.kernel.await_quiet(self.scope, token))["state"], "parked"
        )
        self.assertTrue(self.kernel.finish_ticket(self.scope, token, ticket))
        self.assertEqual(
            (await self.kernel.await_quiet(self.scope, token))["state"], "quiet"
        )
        second = await self.acquire(1, object())
        self.assertEqual(second["state"], "acquired")
        self.assertEqual(second["core_epoch"], 2)
        self.assertFalse(self.kernel.finish_ticket(self.scope, token, ticket))
        self.assertIsNone(
            self.kernel.sync_fence(self.scope, token, cleanup, durable, proof)
        )

    async def test_failed_cleanup_and_bad_proof_park(self) -> None:
        first = await self.acquire()
        token = first["owner_token"]

        async def durable() -> bool:
            return True

        async def bad() -> None:
            raise RuntimeError("failure")

        self.kernel.sync_fence(self.scope, token, bad, durable, proof)
        self.assertEqual(
            (await self.kernel.await_quiet(self.scope, token))["state"], "parked"
        )
        self.assertEqual((await self.acquire(1, object()))["state"], "parked")

    async def test_invalid_proof_and_false_durable_fence_park(self) -> None:
        first = await self.acquire()
        token = first["owner_token"]

        async def done() -> bool:
            return True

        self.kernel.sync_fence(
            self.scope,
            token,
            done,
            done,
            lambda actual, owner: {**proof(actual, owner), "resources_closed": False},
        )
        self.assertEqual(
            (await self.kernel.await_quiet(self.scope, token))["state"], "parked"
        )
        self.assertEqual((await self.acquire(1, object()))["state"], "parked")

        other_scope = (str(Path(self.temp.name) / "other"), self.context)
        Path(other_scope[0]).mkdir()
        second = await self.kernel.acquire_core(other_scope, object(), 0, self.digest)

        async def unconfirmed() -> bool:
            return False

        self.kernel.sync_fence(
            other_scope, second["owner_token"], done, unconfirmed, proof
        )
        self.assertEqual(
            (await self.kernel.await_quiet(other_scope, second["owner_token"]))[
                "state"
            ],
            "parked",
        )
        with self.kernel.lock:
            record = self.kernel.scopes.pop(str(Path(other_scope[0]).resolve()))
        record.file_lock.close()

    async def test_ten_full_generations_and_explicit_lock_release(self) -> None:
        owner = object()
        seen = set()

        async def done() -> bool:
            return True

        for epoch in range(10):
            result = await self.acquire(epoch, owner)
            self.assertEqual(result["state"], "acquired")
            self.assertEqual(result["core_epoch"], epoch + 1)
            token = result["owner_token"]
            self.assertNotIn(token, seen)
            seen.add(token)
            self.kernel.sync_fence(self.scope, token, done, done, proof)
            self.assertEqual(
                (await self.kernel.await_quiet(self.scope, token))["state"], "quiet"
            )
            owner = object()
        self.assertTrue(self.kernel.release_scope(self.scope, token))
        lock = RuntimeOwnerLock(self.temp.name)
        lock.close()

    async def test_failed_gate_closes_others_and_never_quiet(self) -> None:
        first = await self.acquire()
        token = first["owner_token"]
        closed = []

        def failed_close() -> None:
            closed.append("failed")
            raise RuntimeError("gate failed")

        self.kernel.register_gate(self.scope, token, failed_close)
        self.kernel.register_gate(self.scope, token, lambda: closed.append("other"))

        async def done() -> bool:
            return True

        self.kernel.sync_fence(self.scope, token, done, done, proof)
        self.assertEqual(closed, ["failed", "other"])
        self.assertEqual(
            (await self.kernel.await_quiet(self.scope, token))["state"], "parked"
        )
        self.assertEqual((await self.acquire(1, object()))["state"], "parked")

    async def test_context_conflict_and_release_preserves_epoch(self) -> None:
        first = await self.acquire()
        token = first["owner_token"]
        other_scope = (self.temp.name, object())
        other = await self.kernel.acquire_core(other_scope, object(), 1, self.digest)
        self.assertEqual(other["error"], "coordinator_conflict")

        async def done() -> bool:
            return True

        self.kernel.sync_fence(self.scope, token, done, done, proof)
        self.assertEqual(
            (await self.kernel.await_quiet(self.scope, token))["state"], "quiet"
        )
        self.assertTrue(self.kernel.release_scope(self.scope, token))
        self.assertEqual(
            (await self.kernel.acquire_core(other_scope, object(), 0, self.digest))[
                "error"
            ],
            "stale_epoch",
        )
        self.assertEqual(
            (await self.kernel.acquire_core(other_scope, object(), 1, self.digest))[
                "state"
            ],
            "acquired",
        )

    async def test_reload_reuses_anchor_and_conflict_does_not_overwrite(self) -> None:
        first = await self.acquire()
        token = first["owner_token"]
        original = sys.modules["_ygl_runtime_coordinator_v1"]
        for _ in range(10):
            sys.modules.pop("ygl_test_subject.bootstrap.coordinator", None)
            module = importlib.import_module("ygl_test_subject.bootstrap.coordinator")
            self.assertIs(module.get_coordinator(), self.kernel)
            self.assertEqual(
                (
                    await module.get_coordinator().acquire_core(
                        self.scope, object(), 1, self.digest
                    )
                )["state"],
                "parked",
            )
        self.assertIs(sys.modules["_ygl_runtime_coordinator_v1"], original)
        self.assertEqual(
            self.kernel.scopes[str(Path(self.temp.name).resolve())].token, token
        )
        sys.modules["_ygl_runtime_coordinator_v1"] = object()
        try:
            with self.assertRaises(CoordinatorConflict):
                get_coordinator()
            self.assertIs(type(sys.modules["_ygl_runtime_coordinator_v1"]), object)
        finally:
            sys.modules["_ygl_runtime_coordinator_v1"] = original
        sys.modules["_ygl_runtime_coordinator_v1"] = None
        try:
            with self.assertRaises(CoordinatorConflict):
                get_coordinator()
            self.assertIsNone(sys.modules["_ygl_runtime_coordinator_v1"])
        finally:
            sys.modules["_ygl_runtime_coordinator_v1"] = original


class ProcessLockTests(unittest.TestCase):
    def test_import_through_arbitrary_plugin_parent(self) -> None:
        root = Path(__file__).resolve().parents[2]
        script = """
import importlib, sys, types
for name, path in [('data', []), ('data.plugins', []), ('data.plugins.random_slug', [sys.argv[1]])]:
    package = types.ModuleType(name)
    package.__path__ = path
    sys.modules[name] = package
module = importlib.import_module('data.plugins.random_slug.bootstrap.coordinator')
assert module.RuntimeOwnerLock.__module__ == 'data.plugins.random_slug.infrastructure.runtime_owner_lock'
assert module.get_coordinator().protocol_major == 1
"""
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run(
                [sys.executable, "-c", script, str(root)],
                cwd=cwd,
                env={**os.environ, "PYTHONPATH": ""},
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_same_process_and_subprocess_contention(self) -> None:
        if os.name != "posix":
            self.skipTest("Linux/POSIX lock test")
        with tempfile.TemporaryDirectory() as root:
            lock = RuntimeOwnerLock(root)
            try:
                with self.assertRaises(RuntimeOwnerLockError) as caught:
                    RuntimeOwnerLock(root)
                self.assertEqual(caught.exception.code, "data_dir_busy")
                script = (
                    "from infrastructure.runtime_owner_lock import RuntimeOwnerLock;"
                    "RuntimeOwnerLock(__import__('sys').argv[1])"
                )
                result = subprocess.run(
                    [sys.executable, "-c", script, root],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("data_dir_busy", result.stderr)
            finally:
                lock.close()
            second = RuntimeOwnerLock(root)
            second.close()

    def test_symlink_and_hardlink_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / ".ygl-runtime-owner.lock"
            source = Path(root) / "source"
            source.write_text("x")
            path.symlink_to(source)
            with self.assertRaises(RuntimeOwnerLockError):
                RuntimeOwnerLock(root)
            path.unlink()
            os.link(source, path)
            with self.assertRaises(RuntimeOwnerLockError):
                RuntimeOwnerLock(root)


if __name__ == "__main__":
    unittest.main()
