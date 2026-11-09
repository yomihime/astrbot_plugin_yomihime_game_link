"""Started fake host IO settles through fenced real SQLite repositories."""

import asyncio
import unittest
from dataclasses import replace

from ygl_test_subject.bootstrap.coordinator import get_coordinator
from ygl_test_subject.core.contracts.subscriptions import (
    DeliveryState,
    DigestEnvelopeState,
)
from ygl_test_subject.core.ports import MessageReceipt, MessageStatus
from ygl_test_subject.services.delivery_settlement import DeliveryCompletionIdentity
from ygl_test_subject.services.runtime_effect_gate import (
    EffectRejected,
    RuntimeEffectGate,
)

from tests.services import test_delivery as fixtures


class DeliverySettlementTests(fixtures.DeliveryTests):
    async def _bind_runtime(self, service):
        self.kernel = get_coordinator()
        self.scope_key = (self.temp.name, object())
        acquired = await self.kernel.acquire_core(self.scope_key, object(), 0, "a" * 64)
        self.token = acquired["owner_token"]
        self.authorized = True
        self.gate = RuntimeEffectGate(
            self.kernel,
            self.scope_key,
            self.token,
            1,
            authority_check=lambda: self.authorized,
        )
        await self.db.executor.run_internal_transaction(
            lambda unit: unit.connection.execute(
                "INSERT INTO runtime_owners VALUES (?,?,1,0,1,1,0,?,?)",
                ("core", self.token, "a" * 64, self.kernel.boot_id),
            ).rowcount
        )
        self.db.executor.bind_effect_guard(self.gate)
        service._effect_guard = self.gate

    async def _fence(self):
        async def durable():
            await self.db.executor.run_internal_transaction(
                lambda unit: unit.connection.execute(
                    "UPDATE runtime_owners SET accepting=0 WHERE scope='core'"
                ).rowcount
            )
            return True

        async def cleanup():
            return True

        self.kernel.sync_fence(
            self.scope_key,
            self.token,
            cleanup,
            durable,
            lambda token, owner: dict(
                owner_token=token,
                tracked_inflight=0,
                cleanup_complete=True,
                durable_fence=True,
                resources_closed=True,
            ),
        )
        quiet = await self.kernel.await_quiet(self.scope_key, self.token)
        self.assertEqual(quiet["state"], "parked")
        successor = await self.kernel.acquire_core(
            self.scope_key, object(), 1, "b" * 64
        )
        self.assertEqual(successor["state"], "parked")

    async def _saved(self, digest, window):
        # Deliberately read through the privileged test fixture, not a stale guard.
        repository = self.windows if digest else self.deliveries
        guard = self.db.executor._effect_guard
        self.db.executor._effect_guard = None
        try:
            return (
                await repository.current_envelope(window.window_id, self.recipient)
                if digest
                else await repository.current_event(
                    "event-1", 1, subscription_id="sub-1", subscription_revision=1
                )
            )
        finally:
            self.db.executor._effect_guard = guard

    async def _settlement_case(self, *, digest, cancel, status, revoke_only=False):
        entered, release, receipt_entered, commit_release = (
            asyncio.Event() for _ in range(4)
        )
        calls = []

        class Port:
            async def send(_port, target, payload):
                calls.append((target, payload))
                if cancel == "timeout":
                    service._send_timeout = 0.02
                entered.set()
                await release.wait()
                return MessageReceipt(status, "late-receipt")

        service = self._service(port=Port())
        window, _event = (
            await self._digest_receipt_fixture("settlement")
            if digest
            else (None, self.event)
        )
        await self._bind_runtime(service)
        repository = self.windows if digest else self.deliveries
        method = "complete_envelope_send" if digest else "record_attempt"
        original = getattr(repository, method)
        permits = []

        async def finish(*args, **kwargs):
            permit = kwargs.get("completion_permit")
            if permit is not None:
                permits.append(permit)
                receipt_entered.set()
                await commit_release.wait()
            return await original(*args, **kwargs)

        setattr(repository, method, finish)
        dispatch = asyncio.create_task(
            service.dispatch_digest(window.window_id, self.recipient, now=self.now)
            if digest
            else service.dispatch_event("event-1", 1, "sub-1", 1)
        )
        try:
            await asyncio.wait_for(entered.wait(), 2)
            if revoke_only:
                self.authorized = False
            else:
                await self._fence()
            if cancel is True:
                dispatch.cancel()
                dispatch.cancel()
            await asyncio.sleep(0.04 if cancel == "timeout" else 0)
            self.assertFalse(dispatch.done())
            self.assertTrue(self.kernel._record(self.scope_key).tickets)
            with self.assertRaises(EffectRejected):
                await self.db.executor.run_transaction(lambda unit: None)
            with self.assertRaises(EffectRejected):
                await service.dispatch_event("event-1", 1, "sub-1", 1)
            release.set()
            await asyncio.wait_for(receipt_entered.wait(), 2)
            self.assertFalse(dispatch.done())
            self.assertTrue(self.kernel._record(self.scope_key).tickets)
            quiet = await self.kernel.await_quiet(self.scope_key, self.token)
            self.assertEqual(quiet["state"], "parked")
            permit = permits[0]
            with self.assertRaises(EffectRejected):
                permit.assert_exact(DeliveryCompletionIdentity("event", ("unrelated",)))
            # Even the receipt permit cannot authorize a different exact CAS.
            if not digest:
                saved = await self._saved(False, None)
                with self.assertRaises(EffectRejected):
                    await original(
                        saved.event_key,
                        saved.event_version,
                        replace(
                            saved.attempt,
                            attempt_number=2,
                            state=DeliveryState.UNKNOWN,
                            completed_at=self.now,
                        ),
                        subscription_id=saved.subscription_id,
                        subscription_revision=saved.subscription_revision,
                        expected_state=DeliveryState.SENDING,
                        completion_permit=permit,
                    )
            commit_release.set()
            if cancel is True:
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(dispatch, 2)
            else:
                result = await asyncio.wait_for(dispatch, 2)
                self.assertEqual(
                    result.state,
                    DeliveryState.SENT
                    if status is MessageStatus.ACCEPTED
                    else DeliveryState.UNKNOWN,
                )
            saved = await self._saved(digest, window)
            expected = (
                (
                    DigestEnvelopeState.SENT
                    if status is MessageStatus.ACCEPTED
                    else DigestEnvelopeState.UNKNOWN
                )
                if digest
                else (
                    DeliveryState.SENT
                    if status is MessageStatus.ACCEPTED
                    else DeliveryState.UNKNOWN
                )
            )
            self.assertEqual(saved.state, expected)
            self.assertEqual(len(calls), 1)
            self.assertFalse(self.kernel._record(self.scope_key).tickets)
            with self.assertRaises(EffectRejected):
                permit.assert_exact(permit.identity)
            # A fresh runtime can observe UNKNOWN but never auto-resend it.
            if status is MessageStatus.UNKNOWN:
                service._effect_guard = None
                self.db.executor._effect_guard = None
                if digest:
                    again = await service.dispatch_digest(
                        window.window_id, self.recipient, now=self.now
                    )
                    self.assertTrue(
                        again is None or again.state is DeliveryState.UNKNOWN
                    )
                else:
                    again = await service.dispatch_event("event-1", 1, "sub-1", 1)
                    self.assertEqual(again.state, DeliveryState.UNKNOWN)
                self.assertEqual(len(calls), 1)
        finally:
            release.set()
            commit_release.set()
            await asyncio.gather(dispatch, return_exceptions=True)
            await self.db.executor.close()

            async def done():
                return True

            self.kernel.sync_fence(
                self.scope_key,
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
            await self.kernel.await_quiet(self.scope_key, self.token)
            self.assertTrue(self.kernel.release_scope(self.scope_key, self.token))

    async def test_settlement_event_revoked_authority_can_only_complete(self):
        await self._settlement_case(
            digest=False, cancel=False, status=MessageStatus.ACCEPTED, revoke_only=True
        )

    async def test_settlement_digest_revoked_authority_can_only_complete(self):
        await self._settlement_case(
            digest=True, cancel=False, status=MessageStatus.ACCEPTED, revoke_only=True
        )

    async def test_settlement_event_fence_and_exact_cas(self):
        await self._settlement_case(
            digest=False, cancel=False, status=MessageStatus.ACCEPTED
        )

    async def test_settlement_digest_fence_and_exact_cas(self):
        await self._settlement_case(
            digest=True, cancel=False, status=MessageStatus.ACCEPTED
        )

    async def test_settlement_cancel_retains_event_late_receipt(self):
        await self._settlement_case(
            digest=False, cancel=True, status=MessageStatus.ACCEPTED
        )

    async def test_settlement_cancel_retains_digest_late_receipt(self):
        await self._settlement_case(
            digest=True, cancel=True, status=MessageStatus.ACCEPTED
        )

    async def test_settlement_timeout_retains_event_late_receipt(self):
        await self._settlement_case(
            digest=False, cancel="timeout", status=MessageStatus.ACCEPTED
        )

    async def test_settlement_timeout_retains_digest_late_receipt(self):
        await self._settlement_case(
            digest=True, cancel="timeout", status=MessageStatus.ACCEPTED
        )

    async def test_settlement_unknown_event_never_resent(self):
        await self._settlement_case(
            digest=False, cancel=False, status=MessageStatus.UNKNOWN
        )

    async def test_settlement_unknown_digest_never_resent(self):
        await self._settlement_case(
            digest=True, cancel=False, status=MessageStatus.UNKNOWN
        )


def load_tests(loader, _tests, _pattern):
    return unittest.TestSuite(
        DeliverySettlementTests(name)
        for name in loader.getTestCaseNames(DeliverySettlementTests)
        if name.startswith("test_settlement_")
    )
