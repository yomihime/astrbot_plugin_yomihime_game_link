"""Controlled sinks exercise actual ticket lifetime; no network or Host claims."""

import asyncio
import tempfile
import unittest

from ygl_test_subject.bootstrap.coordinator import get_coordinator
from ygl_test_subject.services.runtime_effect_gate import (
    EffectRejected,
    RuntimeEffectGate,
    invocation_guard,
    run_effect,
)


class EffectFencingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.context = object()
        self.scope = (self.temp.name, self.context)
        self.kernel = get_coordinator()
        self.owner = object()
        result = await self.kernel.acquire_core(self.scope, self.owner, 0, "a" * 64)
        self.token = result["owner_token"]
        self.core = RuntimeEffectGate(self.kernel, self.scope, self.token, 1)

    async def asyncTearDown(self):
        async def done():
            return True

        self.kernel.sync_fence(
            self.scope,
            self.token,
            done,
            done,
            lambda token, owner: dict(
                owner_token=token,
                tracked_inflight=0,
                cleanup_complete=True,
                durable_fence=True,
                resources_closed=True,
            ),
        )
        await self.kernel.await_quiet(self.scope, self.token)
        self.assertTrue(self.kernel.release_scope(self.scope, self.token))
        self.temp.cleanup()

    def package(self, name):
        return RuntimeEffectGate(
            self.kernel,
            self.scope,
            self.token,
            1,
            package_id=name,
            package_token=name * 64,
            package_epoch=1,
            authority_check=lambda: None,
            policy_assert=lambda unit: None,
        )

    async def test_queued_fence_and_sibling_isolation(self):
        a, b = self.package("a"), self.package("b")
        operation = a.capture()
        a.close()
        try:
            with self.assertRaises(EffectRejected):
                operation.mark_executing()
            sibling = b.capture()
            sibling.mark_executing()
            sibling.finish()
        finally:
            operation.finish()

    async def test_canceled_waiter_retains_actual_transport_ticket(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def transport():
            entered.set()
            await release.wait()
            return "accepted"

        with invocation_guard(self.core):
            waiter = asyncio.create_task(run_effect(transport))
        await entered.wait()
        waiter.cancel()
        await asyncio.sleep(0)
        self.core.close()
        self.assertFalse(waiter.done())
        self.assertEqual(1, len(self.kernel._record(self.scope).tickets))
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertFalse(self.kernel._record(self.scope).tickets)

    async def test_scheduled_before_fence_never_calls_sink(self):
        called = []

        async def send():
            called.append(True)

        with invocation_guard(self.core):
            task = asyncio.create_task(run_effect(send))
        self.core.close()
        with self.assertRaises(EffectRejected):
            await task
        self.assertEqual([], called)

    async def test_policy_rechecked_at_execution(self):
        allowed = [True]
        gate = RuntimeEffectGate(
            self.kernel,
            self.scope,
            self.token,
            1,
            package_id="a",
            package_token="a" * 64,
            package_epoch=1,
            authority_check=lambda: allowed[0],
            policy_assert=lambda unit: None,
        )
        operation = gate.capture()
        allowed[0] = False
        try:
            with self.assertRaisesRegex(EffectRejected, "grant_revoked"):
                operation.mark_executing()
        finally:
            operation.finish()

    async def test_package_cannot_omit_policy_authority(self):
        with self.assertRaises(ValueError):
            RuntimeEffectGate(
                self.kernel,
                self.scope,
                self.token,
                1,
                package_id="a",
                package_token="a" * 64,
                package_epoch=1,
            )

    async def test_real_http_service_rechecks_after_quota_wait(self):
        from ygl_test_subject.infrastructure.http import SourceHttpService

        from yomihime_game_link_sdk.declarations import SourceDeclaration
        from yomihime_game_link_sdk.services import HttpRequest, HttpResponse

        calls = []

        class Transport:
            async def request(self, request):
                calls.append(request)
                return HttpResponse(200, {}, b"ok")

        service = SourceHttpService(
            (SourceDeclaration("public", "example.test"),), Transport()
        )
        await service._global_semaphore.acquire()
        # Occupy every remaining slot to force a real service admission await.
        for _ in range(15):
            await service._global_semaphore.acquire()
        with invocation_guard(self.core):
            task = asyncio.create_task(service.fetch(HttpRequest("public", "/item")))
        await asyncio.sleep(0)
        self.core.close()
        service._global_semaphore.release()
        with self.assertRaises(Exception):
            await task
        self.assertEqual([], calls)

    async def test_real_http_redirect_fence_denies_next_hop(self):
        from ygl_test_subject.infrastructure.http import SourceHttpService

        from yomihime_game_link_sdk.declarations import SourceDeclaration
        from yomihime_game_link_sdk.services import HttpRequest, HttpResponse

        calls = []
        gate = self.core

        class Transport:
            async def request(self, request):
                calls.append(request)
                gate.close()
                return HttpResponse(302, {"Location": "/next/item"}, b"")

        declaration = SourceDeclaration(
            "public",
            "example.test",
            max_redirects=2,
            redirect_path_prefixes=("/next/",),
        )
        service = SourceHttpService((declaration,), Transport())
        with invocation_guard(self.core):
            with self.assertRaises(Exception):
                await service.fetch(HttpRequest("public", "/start"))
        self.assertEqual(1, len(calls))
        self.assertFalse(self.kernel._record(self.scope).tickets)

    async def test_actual_transport_rechecks_after_session_await(self):
        from yarl import URL
        from ygl_test_subject.infrastructure.http_transport import AioHttpTransport

        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        class Session:
            def request(self, *args, **kwargs):
                calls.append(True)
                raise AssertionError("fenced network must never start")

        class Transport(AioHttpTransport):
            async def _get_session(self):
                entered.set()
                await release.wait()
                return Session()

        transport = Transport()
        with invocation_guard(self.core):
            task = asyncio.create_task(
                transport._send("GET", URL("https://example.test/"), None, {}, 1, 1024)
            )
        await entered.wait()
        self.core.close()
        self.assertEqual(1, len(self.kernel._record(self.scope).tickets))
        release.set()
        with self.assertRaises(EffectRejected):
            await task
        self.assertEqual([], calls)

    async def test_closed_lease_cannot_be_reopened(self):
        gate = self.package("a")
        gate.close()
        with self.assertRaises(EffectRejected):
            gate.open()
        fresh = RuntimeEffectGate(
            self.kernel, self.scope, self.token, 1, initially_open=False
        )
        with self.assertRaises(EffectRejected):
            fresh.capture()
        fresh.open()
        operation = fresh.capture()
        operation.finish()
        fresh.close()
        with self.assertRaises(EffectRejected):
            fresh.open()

    async def test_actual_delivery_child_retains_message_until_completion(self):
        from contextlib import asynccontextmanager

        from ygl_test_subject.services.delivery import DeliveryService
        from ygl_test_subject.services.delivery_settlement import (
            event_completion_identity,
        )

        entered, release = asyncio.Event(), asyncio.Event()

        class Admission:
            @asynccontextmanager
            async def mutation(self, name):
                yield

            def check(self, lease):
                pass

        class Port:
            async def send(self, target, payload):
                entered.set()
                await release.wait()
                return "accepted"

        async def eligible():
            return True

        async def abort():
            raise AssertionError("already-started message cannot be aborted")

        service = object.__new__(DeliveryService)
        service._admission, service._message_port = Admission(), Port()
        service._effect_guard = self.core
        identity = event_completion_identity(
            "event", 1, "subscription", 1, 1, "start", "idempotency"
        )
        state = {}
        with invocation_guard(self.core):
            child = await service._start_io(
                object(),
                state,
                eligible,
                lambda: True,
                abort,
                object(),
                object(),
                identity,
            )
        await entered.wait()
        child.cancel()
        self.core.close()
        await asyncio.sleep(0)
        self.assertFalse(child.done())
        self.assertEqual(2, len(self.kernel._record(self.scope).tickets))
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await child
        # IO has drained; exact receipt settlement remains independently owned.
        self.assertEqual(1, len(self.kernel._record(self.scope).tickets))
        state["completion_permit"].assert_exact(identity)
        state["completion_permit"].finish()
        self.assertFalse(self.kernel._record(self.scope).tickets)

    async def test_explicit_guard_propagates_to_nested_actual_sink(self):
        from ygl_test_subject.services.runtime_effect_gate import check_current_effect

        async def factory():
            self.core.close()
            check_current_effect()
            raise AssertionError("closed explicit guard must reach nested sinks")

        with self.assertRaises(EffectRejected):
            await run_effect(factory, guard=self.core)
        self.assertFalse(self.kernel._record(self.scope).tickets)
