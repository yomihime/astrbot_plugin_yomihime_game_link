"""Regression checks for admission before invoking a module handler."""

import asyncio
import unittest
from time import monotonic

from ygl_test_subject.core.admission import AdmissionError
from ygl_test_subject.core.handler_dispatch import HandlerDispatch
from ygl_test_subject.core.task_scope import ScopeDeadlineExceeded


class HandlerDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_foreground_reservation_and_cancelled_waiter(self):
        dispatch = HandlerDispatch(capacity=2, reserved=1, queue_limit=1)
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def blocking():
            calls.append("background")
            entered.set()
            await release.wait()

        def run(handler, background=True):
            return dispatch.run(
                handler, background=background, deadline=None, clock=monotonic
            )

        active = asyncio.create_task(run(blocking))
        await entered.wait()
        queued = asyncio.create_task(run(blocking))
        await asyncio.sleep(0)
        with self.assertRaises(AdmissionError):
            await run(blocking)

        async def foreground():
            calls.append("foreground")

        await asyncio.wait_for(run(foreground, False), 1)
        queued.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await queued
        release.set()
        await active
        self.assertEqual(calls, ["background", "foreground"])
        await run(foreground)
        self.assertEqual(calls[-1], "foreground")

    async def test_wait_spends_original_deadline_and_does_not_call_handler(self):
        dispatch = HandlerDispatch(capacity=2, reserved=1)
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocking():
            entered.set()
            await release.wait()

        async def never():
            self.fail("expired work entered the handler")

        active = asyncio.create_task(
            dispatch.run(blocking, background=True, deadline=None, clock=monotonic)
        )
        await entered.wait()
        try:
            with self.assertRaises(ScopeDeadlineExceeded):
                await dispatch.run(
                    never, background=True, deadline=monotonic() + 0.02, clock=monotonic
                )
        finally:
            release.set()
            await active

    async def test_queued_authority_is_rechecked_before_handler_entry(self):
        dispatch = HandlerDispatch(capacity=2, reserved=1)
        entered, release = asyncio.Event(), asyncio.Event()
        invoked = []

        async def blocking():
            entered.set()
            await release.wait()

        async def handler():
            invoked.append(True)

        async def revoke():
            raise PermissionError("source revoked while waiting")

        active = asyncio.create_task(
            dispatch.run(blocking, background=True, deadline=None, clock=monotonic)
        )
        await entered.wait()
        queued = asyncio.create_task(
            dispatch.run(
                handler,
                background=True,
                deadline=None,
                clock=monotonic,
                after_wait=revoke,
            )
        )
        await asyncio.sleep(0)
        release.set()
        await active
        with self.assertRaises(PermissionError):
            await queued
        self.assertEqual(invoked, [])
        await dispatch.run(handler, background=True, deadline=None, clock=monotonic)
        self.assertEqual(invoked, [True])
