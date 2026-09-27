from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ygl_test_subject.core.ports import (
    MessageReceipt,
    MessageStatus,
    RootOutputClaim,
    RootOutputConflict,
    RootOutputOutcome,
    RootOutputState,
)
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories_output import (
    SQLiteRootOutputRepository,
)


class SQLiteRootOutputRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "output.sqlite3"
        self.db = SQLiteDatabase(self.path)
        self.repo = SQLiteRootOutputRepository(self.db)
        self.now = datetime(2026, 9, 25, 12, tzinfo=UTC)
        self.key = ("root-1", "result-1")
        self.fingerprint = "a" * 64

    def tearDown(self) -> None:
        self.temp.cleanup()

    async def test_r01_claim_is_single_owner_and_exact_replay_returns_receipt(self):
        self.assertEqual(self.db.initialize(), 80)
        claims = await asyncio.gather(
            self.repo.claim(
                *self.key,
                self.fingerprint,
                "owner-a",
                now=self.now,
                lease_expires_at=self.now + timedelta(minutes=1),
            ),
            self.repo.claim(
                *self.key,
                self.fingerprint,
                "owner-b",
                now=self.now,
                lease_expires_at=self.now + timedelta(minutes=1),
            ),
        )
        self.assertEqual(claims[0], claims[1])
        claim = claims[0]
        sending = await self.repo.begin_sending(
            claim,
            now=self.now,
            lease_expires_at=self.now + timedelta(minutes=2),
        )
        self.assertIsNotNone(sending)
        self.assertEqual(sending.state, RootOutputState.SENDING)
        self.assertIsNone(
            await self.repo.begin_sending(
                claims[1],
                now=self.now,
                lease_expires_at=self.now + timedelta(minutes=2),
            )
        )
        receipt = MessageReceipt(MessageStatus.ACCEPTED, "platform-42")
        completed = await self.repo.complete(
            sending,
            receipt,
            outcome=RootOutputOutcome.MESSAGE,
            completed_at=self.now + timedelta(seconds=2),
        )
        self.assertEqual(completed.receipt, receipt)
        replay = await self.repo.claim(
            *self.key,
            self.fingerprint,
            "retry-owner",
            now=self.now + timedelta(seconds=3),
            lease_expires_at=self.now + timedelta(minutes=3),
        )
        self.assertEqual(replay, completed)
        with self.assertRaises(RootOutputConflict):
            await self.repo.claim(
                *self.key,
                "b" * 64,
                "retry-owner",
                now=self.now + timedelta(seconds=3),
                lease_expires_at=self.now + timedelta(minutes=3),
            )

    async def test_r01_one_root_invocation_cannot_claim_two_output_identities(self):
        results = await asyncio.gather(
            self.repo.claim(
                "root-shared",
                "result-a",
                self.fingerprint,
                "owner-a",
                now=self.now,
                lease_expires_at=self.now + timedelta(minutes=1),
            ),
            self.repo.claim(
                "root-shared",
                "result-b",
                self.fingerprint,
                "owner-b",
                now=self.now,
                lease_expires_at=self.now + timedelta(minutes=1),
            ),
            return_exceptions=True,
        )
        self.assertEqual(
            sum(isinstance(value, RootOutputClaim) for value in results), 1
        )
        self.assertEqual(
            sum(isinstance(value, RootOutputConflict) for value in results), 1
        )

    async def test_r02_expired_pre_send_claim_can_be_reclaimed(self):
        first = await self.repo.claim(
            *self.key,
            self.fingerprint,
            "owner-a",
            now=self.now,
            lease_expires_at=self.now + timedelta(seconds=1),
        )
        reclaimed = await self.repo.claim(
            *self.key,
            self.fingerprint,
            "owner-b",
            now=self.now + timedelta(seconds=2),
            lease_expires_at=self.now + timedelta(minutes=1),
        )
        self.assertNotEqual(first.owner_token, reclaimed.owner_token)
        self.assertEqual(reclaimed.state, RootOutputState.CLAIMED)
        self.assertGreater(reclaimed.claim_generation, first.claim_generation)
        self.assertIsNone(
            await self.repo.begin_sending(
                first,
                now=self.now + timedelta(seconds=2),
                lease_expires_at=self.now + timedelta(minutes=1),
            )
        )

    async def test_r02_reused_owner_token_does_not_authorize_stale_claim(self):
        first = await self.repo.claim(
            *self.key,
            self.fingerprint,
            "reused-owner",
            now=self.now,
            lease_expires_at=self.now + timedelta(seconds=1),
        )
        reclaimed = await self.repo.claim(
            *self.key,
            self.fingerprint,
            "reused-owner",
            now=self.now + timedelta(seconds=2),
            lease_expires_at=self.now + timedelta(minutes=1),
        )
        self.assertEqual(first.owner_token, reclaimed.owner_token)
        self.assertGreater(reclaimed.claim_generation, first.claim_generation)
        self.assertIsNone(
            await self.repo.begin_sending(
                first,
                now=self.now + timedelta(seconds=2),
                lease_expires_at=self.now + timedelta(minutes=2),
            )
        )
        self.assertIsNotNone(
            await self.repo.begin_sending(
                reclaimed,
                now=self.now + timedelta(seconds=2),
                lease_expires_at=self.now + timedelta(minutes=2),
            )
        )

    async def test_r02_actual_unknown_receipt_is_distinct_from_recovered_send(self):
        claim = await self.repo.claim(
            *self.key,
            self.fingerprint,
            "owner-a",
            now=self.now,
            lease_expires_at=self.now + timedelta(minutes=1),
        )
        sending = await self.repo.begin_sending(
            claim,
            now=self.now,
            lease_expires_at=self.now + timedelta(minutes=1),
        )
        returned = await self.repo.complete(
            sending,
            MessageReceipt(MessageStatus.UNKNOWN),
            outcome=RootOutputOutcome.MESSAGE,
            completed_at=self.now + timedelta(seconds=1),
        )
        self.assertEqual(returned.state, RootOutputState.COMPLETED)
        self.assertEqual(returned.receipt.status, MessageStatus.UNKNOWN)
        self.assertIsNone(returned.error_code)

    async def test_abort_before_dispatch_closes_only_exact_sending_claim(self):
        claimed = await self.repo.claim(
            *self.key,
            self.fingerprint,
            "owner-a",
            now=self.now,
            lease_expires_at=self.now + timedelta(minutes=1),
        )
        sending = await self.repo.begin_sending(
            claimed,
            now=self.now,
            lease_expires_at=self.now + timedelta(minutes=2),
        )
        self.assertIsNotNone(sending)
        stale_token = replace(sending, owner_token="owner-stale")
        self.assertIsNone(
            await self.repo.abort_before_dispatch(
                stale_token,
                completed_at=self.now + timedelta(seconds=1),
                error_code="cancelled_before_dispatch",
            )
        )

        aborted = await self.repo.abort_before_dispatch(
            sending,
            completed_at=self.now + timedelta(seconds=1),
            error_code="cancelled_before_dispatch",
        )
        self.assertEqual(aborted.state, RootOutputState.COMPLETED)
        self.assertEqual(aborted.outcome, RootOutputOutcome.CONTROLLED_RESULT)
        self.assertEqual(aborted.error_code, "cancelled_before_dispatch")
        self.assertIsNone(aborted.receipt)
        self.assertIsNone(
            await self.repo.complete(
                sending,
                MessageReceipt(MessageStatus.ACCEPTED, "late-platform-id"),
                outcome=RootOutputOutcome.MESSAGE,
                completed_at=self.now + timedelta(seconds=2),
            )
        )

    async def test_abort_before_dispatch_cannot_rewrite_recovered_unknown(self):
        claimed = await self.repo.claim(
            "root-recovered",
            "result-recovered",
            self.fingerprint,
            "owner-a",
            now=self.now,
            lease_expires_at=self.now + timedelta(minutes=1),
        )
        sending = await self.repo.begin_sending(
            claimed,
            now=self.now,
            lease_expires_at=self.now + timedelta(minutes=1),
        )
        await self.repo.recover_expired(
            before=self.now + timedelta(minutes=2),
            recovered_at=self.now + timedelta(minutes=2),
        )
        self.assertIsNone(
            await self.repo.abort_before_dispatch(
                sending,
                completed_at=self.now + timedelta(minutes=2),
                error_code="cancelled_before_dispatch",
            )
        )
        replay = await self.repo.claim(
            "root-recovered",
            "result-recovered",
            self.fingerprint,
            "owner-b",
            now=self.now + timedelta(minutes=2),
            lease_expires_at=self.now + timedelta(minutes=3),
        )
        self.assertEqual(replay.state, RootOutputState.UNKNOWN)

    async def test_abort_before_dispatch_cannot_rewrite_recreated_generation(self):
        short = SQLiteRootOutputRepository(self.db, retention=timedelta(days=1))
        claimed = await short.claim(
            "root-recreated",
            "result-recreated",
            self.fingerprint,
            "owner-a",
            now=self.now,
            lease_expires_at=self.now + timedelta(minutes=1),
        )
        sending = await short.begin_sending(
            claimed,
            now=self.now,
            lease_expires_at=self.now + timedelta(minutes=1),
        )
        await short.complete(
            sending,
            MessageReceipt(MessageStatus.ACCEPTED, "platform-1"),
            outcome=RootOutputOutcome.MESSAGE,
            completed_at=self.now,
        )
        later = self.now + timedelta(days=2)
        recreated = await short.claim(
            "root-recreated",
            "result-recreated",
            self.fingerprint,
            "owner-b",
            now=later,
            lease_expires_at=later + timedelta(minutes=1),
        )
        self.assertGreater(recreated.claim_generation, sending.claim_generation)
        self.assertIsNone(
            await short.abort_before_dispatch(
                sending,
                completed_at=later,
                error_code="cancelled_before_dispatch",
            )
        )

    async def test_r03_expired_send_recovers_unknown_across_restart(self):
        claimed = await self.repo.claim(
            *self.key,
            self.fingerprint,
            "owner-a",
            now=self.now,
            lease_expires_at=self.now + timedelta(minutes=1),
        )
        sending = await self.repo.begin_sending(
            claimed,
            now=self.now,
            lease_expires_at=self.now + timedelta(minutes=1),
        )
        recovered_at = self.now + timedelta(minutes=2)
        reopened = SQLiteRootOutputRepository(SQLiteDatabase(self.path))
        recovered = await reopened.recover_expired(
            before=recovered_at, recovered_at=recovered_at
        )
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].state, RootOutputState.UNKNOWN)
        self.assertIsNone(recovered[0].receipt)
        self.assertEqual(recovered[0].error_code, "interrupted_send")
        replay = await reopened.claim(
            *self.key,
            self.fingerprint,
            "retry-owner",
            now=recovered_at,
            lease_expires_at=recovered_at + timedelta(minutes=1),
        )
        self.assertEqual(replay.state, RootOutputState.UNKNOWN)
        self.assertIsNone(
            await reopened.complete(
                sending,
                MessageReceipt(MessageStatus.ACCEPTED, "late-platform-id"),
                outcome=RootOutputOutcome.MESSAGE,
                completed_at=recovered_at + timedelta(seconds=1),
            )
        )

    async def test_r04_terminal_retention_is_explicit_and_bounded(self):
        short = SQLiteRootOutputRepository(self.db, retention=timedelta(days=1))
        claimed = await short.claim(
            *self.key,
            self.fingerprint,
            "owner-a",
            now=self.now,
            lease_expires_at=self.now + timedelta(minutes=1),
        )
        sending = await short.begin_sending(
            claimed,
            now=self.now,
            lease_expires_at=self.now + timedelta(minutes=1),
        )
        await short.complete(
            sending,
            MessageReceipt(MessageStatus.FAILED),
            outcome=RootOutputOutcome.MESSAGE,
            completed_at=self.now,
        )
        later = self.now + timedelta(days=2)
        new_claim = await short.claim(
            *self.key,
            self.fingerprint,
            "owner-a",
            now=later,
            lease_expires_at=later + timedelta(minutes=1),
        )
        self.assertEqual(new_claim.state, RootOutputState.CLAIMED)
        self.assertGreater(new_claim.claim_generation, claimed.claim_generation)
        stale_after_recreate = await short.begin_sending(
            claimed,
            now=later,
            lease_expires_at=later + timedelta(minutes=2),
        )
        self.assertIsNone(stale_after_recreate)
        tool = await short.complete(
            new_claim,
            None,
            outcome=RootOutputOutcome.TOOL_RETURNED,
            completed_at=later,
        )
        self.assertEqual(tool.outcome, RootOutputOutcome.TOOL_RETURNED)
        self.assertIsNone(tool.receipt)
        self.assertIsNone(
            await short.complete(
                sending,
                MessageReceipt(MessageStatus.ACCEPTED, "stale-platform-id"),
                outcome=RootOutputOutcome.MESSAGE,
                completed_at=later + timedelta(seconds=1),
            )
        )
        with self.assertRaises(ValueError):
            SQLiteRootOutputRepository(self.db, retention=timedelta(days=3651))


if __name__ == "__main__":
    unittest.main()
