"""Invocation, grant and drain fences around the actual bound records service."""

from __future__ import annotations

import asyncio
import threading
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

import tests.integration.test_b03_services as runtime_fixture
from tests.storage.test_record_write_fencing import _wait_for
from yomihime_game_link_sdk.errors import AccessDenied
from yomihime_game_link_sdk.storage import GrantReference


class BoundRecordWriteFencingTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = runtime_fixture.B03ServiceAssemblyTests.asyncSetUp
    _view = runtime_fixture.B03ServiceAssemblyTests._view
    _grant_with_secret = runtime_fixture.B03ServiceAssemblyTests._grant_with_secret

    async def asyncTearDown(self):
        await self.runtime.services.close_credentials()
        await self.runtime.database.executor.close()
        self.temp.cleanup()

    async def _count(self, key):
        return await self.runtime.database.executor.run_read(
            lambda unit: unit.execute(
                "SELECT COUNT(*) FROM module_records WHERE record_key=?", (key,)
            ).fetchone()[0]
        )

    async def test_queued_bound_write_rejects_lifecycle_quiesce(self):
        invocation = self._view()
        bound = await self.runtime.services.for_module("sample/alpha").scopes.bind(
            invocation
        )
        records = await bound.records.collection("announcements")
        executor = self.runtime.database.executor
        original = executor.run_transaction
        entered, release = threading.Event(), threading.Event()

        async def pause_after_sql(callback, **kwargs):
            def paused(unit):
                result = callback(unit)
                # Reissued collection registration is also a transaction.
                # Pause only once the actual payload has been inserted.
                if (
                    unit.execute(
                        "SELECT 1 FROM module_records WHERE record_key='quiesce'"
                    ).fetchone()
                    is not None
                ):
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError("test did not release SQLite commit")
                return result

            return await original(paused, **kwargs)

        with patch.object(executor, "run_transaction", pause_after_sql):
            task = asyncio.create_task(records.create("quiesce", {"value": "late"}))
            try:
                await _wait_for(entered.is_set)
                self.runtime.lifecycle.quiesce("sample/alpha", "test-stop", "test")
            finally:
                release.set()
            with self.assertRaises(AccessDenied):
                await task
        self.assertEqual(await self._count("quiesce"), 0)

    async def test_cancelled_write_retains_mutation_until_worker_drains(self):
        invocation = self._view()
        bound = await self.runtime.services.for_module("sample/alpha").scopes.bind(
            invocation
        )
        records = await bound.records.collection("announcements")
        executor = self.runtime.database.executor
        original = executor.run_transaction
        entered, release = threading.Event(), threading.Event()
        acquired, waiting = asyncio.Event(), asyncio.Event()

        async def paused_write(callback, **kwargs):
            def paused(unit):
                result = callback(unit)
                if (
                    unit.execute(
                        "SELECT 1 FROM module_records WHERE record_key='cancel-drain'"
                    ).fetchone()
                    is not None
                ):
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError("test did not release SQLite worker")
                return result

            return await original(paused, **kwargs)

        async def mutate():
            waiting.set()
            async with self.runtime.lifecycle.admission.mutation("test-after-record"):
                acquired.set()

        with patch.object(executor, "run_transaction", paused_write):
            task = asyncio.create_task(records.create("cancel-drain", {"value": 1}))
            contender = None
            try:
                await _wait_for(entered.is_set)
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                contender = asyncio.create_task(mutate())
                await asyncio.wait_for(waiting.wait(), 2)
                self.assertFalse(task.done())
                self.assertFalse(acquired.is_set())
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            if contender is not None:
                await asyncio.wait_for(contender, 2)
        self.assertTrue(acquired.is_set())
        self.assertEqual(executor._outstanding, 0)
        # Cancellation alone does not undo a transaction already accepted.
        self.assertEqual(await self._count("cancel-drain"), 1)

    async def test_grant_revocation_wins_gate_before_waiting_record_write(self):
        grant = await self._grant_with_secret("record-revoke")
        services = self.runtime.services.for_module("sample/alpha")
        invocation = self._view(grant=grant)
        bound = await services.scopes.bind(invocation)
        records = await bound.records.collection("account_records")
        revoke_view = self._view()
        acquired, release, waiting = (asyncio.Event() for _ in range(3))
        original = self.runtime.lifecycle.admission.mutation

        @asynccontextmanager
        async def observed(owner):
            if owner == "records-write:sample/alpha":
                waiting.set()
            async with original(owner):
                if owner == "authorization-revoke:sample/alpha":
                    acquired.set()
                    await release.wait()
                yield

        with patch.object(self.runtime.lifecycle.admission, "mutation", observed):
            revoke = asyncio.create_task(
                services.accounts.revoke(
                    revoke_view, GrantReference(grant.grant_id, grant.revision)
                )
            )
            write = None
            try:
                await asyncio.wait_for(acquired.wait(), 2)
                write = asyncio.create_task(
                    records.create("old-grant", {"value": "late"})
                )
                await asyncio.wait_for(waiting.wait(), 2)
                self.assertFalse(write.done())
            finally:
                release.set()
            await revoke
            self.assertIsNotNone(write)
            with self.assertRaises(AccessDenied):
                await write
        self.assertEqual(await self._count("old-grant"), 0)


if __name__ == "__main__":
    unittest.main()
