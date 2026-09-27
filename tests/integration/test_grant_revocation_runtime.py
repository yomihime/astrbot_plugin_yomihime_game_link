from __future__ import annotations

import asyncio
import tempfile
import unittest
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.api.services import Grant, GrantStatus
from ygl_test_subject.api.storage import GrantReference, OwnershipKind
from ygl_test_subject.api.subscriptions import (
    SubscriptionRequest,
    SubscriptionStatus,
)
from ygl_test_subject.core.ports import SecretOwner
from ygl_test_subject.services.subscriptions import SubscriptionOperationError

from tests.fixtures.b04_runtime import DeterministicClock, build_runtime


class GrantRevocationRuntimeConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="grant-revoke-race-")
        self.root = Path(self.temp.name)
        self.clock = DeterministicClock()
        self.runtime = build_runtime(self.root, clock=self.clock)
        self.module_services = self.runtime.module_factory.for_module("sample/feed")
        self.subscriptions = self.module_services.subscriptions
        self.accounts = self.module_services.accounts

    def tearDown(self) -> None:
        self.temp.cleanup()

    async def _conversation(self, actor: str) -> None:
        from ygl_test_subject.api.services import ConversationKind, ConversationRef

        await self.runtime.host_repositories.conversations.save(
            ConversationRef(
                "test-adapter",
                ConversationKind.DIRECT,
                actor,
                f"private-route-{actor}",
            )
        )

    async def _grant(self, actor: str, grant_id: str) -> Grant:
        principal_id = f"principal-{actor}"
        receipt = await self.runtime.host_repositories.secret_store.put(
            f"grant-secret-{grant_id}".encode(),
            owner=SecretOwner(
                principal_id,
                "sample/feed",
                "credential",
                f"seed-{grant_id}",
            ),
        )
        grant = Grant(
            grant_id,
            1,
            principal_id,
            "sample/feed",
            f"account-{actor}",
            ("read",),
            receipt.secret_ref,
            GrantStatus.ACTIVE,
            self.clock() + timedelta(hours=2),
        )
        await self.runtime.host_repositories.authorization.create_grant(
            grant, expected_revision=0
        )
        return grant

    @staticmethod
    def _private_request() -> SubscriptionRequest:
        return SubscriptionRequest(
            "private-alert",
            {"region": "global"},
            {"minimum": 0},
            "instant",
        )

    async def _create_private(self, actor: str, grant: Grant):
        await self._conversation(actor)
        invocation = self.runtime.invocation(actor, actor, grant=grant)
        try:
            return await self.subscriptions.create_request(
                invocation, self._private_request()
            )
        finally:
            self.runtime.issuer.release(invocation)

    async def _records_for_grant(self, principal_id: str, reference: GrantReference):
        records = await self.runtime.repositories.subscriptions.list_for_owner(
            principal_id, limit=100
        )
        return tuple(record for record in records if record.grant == reference)

    async def _due_jobs_for_grant(self, reference: GrantReference):
        due = await self.runtime.repositories.scheduler.list_due_jobs(
            now=self.clock(), limit=100
        )
        return tuple(
            job
            for job in due
            if job.key.scope.kind is OwnershipKind.AUTHORIZED
            and job.key.scope.grant == reference
        )

    async def _create_public(self, actor: str):
        from ygl_test_subject.api.services import ConversationKind, ConversationRef

        await self.runtime.host_repositories.conversations.save(
            ConversationRef(
                "test-adapter",
                ConversationKind.DIRECT,
                actor,
                f"private-route-{actor}",
            )
        )
        invocation = self.runtime.invocation(actor, actor)
        try:
            return await self.subscriptions.create_request(
                invocation,
                SubscriptionRequest(
                    "feed-alert",
                    {"region": "global"},
                    {"minimum": 0},
                    "instant",
                ),
            )
        finally:
            self.runtime.issuer.release(invocation)

    async def test_revoke_holds_admission_before_old_grant_create(self) -> None:
        """A creator that read ACTIVE first must recheck after revoke wins the gate."""

        grant = await self._grant("alice", "grant-revoke-wins")
        reference = GrantReference(grant.grant_id, grant.revision)
        await self._conversation("alice")
        old_invocation = self.runtime.invocation("alice", "alice", grant=grant)
        revoke_invocation = self.runtime.invocation("alice", "alice")
        revoke_acquired = asyncio.Event()
        release_revoke_body = asyncio.Event()
        creator_requested_gate = asyncio.Event()
        creator_acquired_gate = asyncio.Event()
        original_mutation = self.runtime.lifecycle.admission.mutation

        @asynccontextmanager
        async def observed_mutation(owner: str):
            if owner == "subscription:create:sample/feed":
                creator_requested_gate.set()
                async with original_mutation(owner):
                    creator_acquired_gate.set()
                    yield
                return
            async with original_mutation(owner):
                if owner == "authorization-revoke:sample/feed":
                    revoke_acquired.set()
                    await release_revoke_body.wait()
                yield

        async def create_with_old_view():
            return await self.subscriptions.create_request(
                old_invocation, self._private_request()
            )

        revoke_task = None
        create_task = None
        try:
            with patch.object(
                self.runtime.lifecycle.admission,
                "mutation",
                observed_mutation,
            ):
                revoke_task = asyncio.create_task(
                    self.accounts.revoke(revoke_invocation, reference)
                )
                await asyncio.wait_for(revoke_acquired.wait(), timeout=3)

                records_before = await self._records_for_grant(
                    "principal-alice", reference
                )
                jobs_before = await self._due_jobs_for_grant(reference)
                self.assertEqual(records_before, ())
                self.assertEqual(jobs_before, ())

                create_task = asyncio.create_task(create_with_old_view())
                await asyncio.wait_for(creator_requested_gate.wait(), timeout=3)
                self.assertFalse(creator_acquired_gate.is_set())
                grant_while_revoke_holds_gate = (
                    await self.runtime.host_repositories.authorization.current_grant(
                        grant.grant_id
                    )
                )
                self.assertIsNotNone(grant_while_revoke_holds_gate)
                self.assertIs(grant_while_revoke_holds_gate.status, GrantStatus.ACTIVE)
                self.assertEqual(
                    await self._records_for_grant("principal-alice", reference), ()
                )
                self.assertEqual(await self._due_jobs_for_grant(reference), ())

                release_revoke_body.set()
                await asyncio.wait_for(revoke_task, timeout=5)
                revoked = (
                    await self.runtime.host_repositories.authorization.current_grant(
                        grant.grant_id
                    )
                )
                self.assertIsNotNone(revoked)
                self.assertIs(revoked.status, GrantStatus.REVOKED)
                self.assertEqual(revoked.revision, reference.revision + 1)

                with self.assertRaises(SubscriptionOperationError):
                    await asyncio.wait_for(create_task, timeout=5)
                self.assertTrue(creator_acquired_gate.is_set())
                self.assertEqual(
                    await self._records_for_grant("principal-alice", reference), ()
                )
                self.assertEqual(await self._due_jobs_for_grant(reference), ())
        finally:
            release_revoke_body.set()
            for task in (revoke_task, create_task):
                if task is not None and not task.done():
                    await asyncio.gather(task, return_exceptions=True)
            self.runtime.issuer.release(old_invocation)
            self.runtime.issuer.release(revoke_invocation)

    async def test_create_holds_admission_then_revoke_converges_by_scope(self) -> None:
        """A committed private create is invalidated without touching other scopes."""

        alice_grant = await self._grant("alice", "grant-create-wins")
        bob_grant = await self._grant("bob", "grant-other-owner")
        alice_reference = GrantReference(alice_grant.grant_id, alice_grant.revision)
        bob_reference = GrantReference(bob_grant.grant_id, bob_grant.revision)
        alice_public = await self._create_public("alice")
        bob_public = await self._create_public("bob")
        bob_private = await self._create_private("bob", bob_grant)

        public_record = await self.runtime.repositories.subscriptions.current(
            alice_public.subscription_id
        )
        bob_public_record = await self.runtime.repositories.subscriptions.current(
            bob_public.subscription_id
        )
        bob_private_record = await self.runtime.repositories.subscriptions.current(
            bob_private.subscription_id
        )
        public_link = await self.runtime.repositories.jobs.current_for_subscription(
            public_record.subscription_id
        )
        bob_public_link = await self.runtime.repositories.jobs.current_for_subscription(
            bob_public_record.subscription_id
        )
        bob_private_link = (
            await self.runtime.repositories.jobs.current_for_subscription(
                bob_private_record.subscription_id
            )
        )
        self.assertIsNotNone(public_record)
        self.assertIsNotNone(bob_public_record)
        self.assertIsNotNone(bob_private_record)
        self.assertIsNotNone(public_link)
        self.assertIsNotNone(bob_public_link)
        self.assertIsNotNone(bob_private_link)
        self.assertIs(public_record.status, SubscriptionStatus.ACTIVE)
        self.assertIs(bob_private_record.status, SubscriptionStatus.ACTIVE)
        public_jobs_before = tuple(
            job.key
            for job in await self.runtime.repositories.scheduler.list_due_jobs(
                now=self.clock(), limit=100
            )
            if job.key.scope.kind is OwnershipKind.PUBLIC
        )
        bob_jobs_before = tuple(
            job.key
            for job in await self.runtime.repositories.scheduler.list_due_jobs(
                now=self.clock(), limit=100
            )
            if job.key.scope.kind is OwnershipKind.AUTHORIZED
            and job.key.scope.grant == bob_reference
        )

        await self._conversation("alice")
        create_invocation = self.runtime.invocation("alice", "alice", grant=alice_grant)
        revoke_invocation = self.runtime.invocation("alice", "alice")
        create_commit_entered = asyncio.Event()
        release_create_commit = asyncio.Event()
        revoke_requested = asyncio.Event()
        revoke_acquired = asyncio.Event()
        release_revoke_body = asyncio.Event()
        original_apply = self.runtime.repositories.lifecycle.apply
        original_mutation = self.runtime.lifecycle.admission.mutation

        async def block_create_at_real_commit(change, *, initial_run=None):
            if change.record.grant == alice_reference:
                create_commit_entered.set()
                await release_create_commit.wait()
            return await original_apply(change, initial_run=initial_run)

        @asynccontextmanager
        async def observed_mutation(owner: str):
            if owner == "authorization-revoke:sample/feed":
                revoke_requested.set()
                async with original_mutation(owner):
                    revoke_acquired.set()
                    await release_revoke_body.wait()
                    yield
                return
            async with original_mutation(owner):
                yield

        async def create_with_grant():
            return await self.subscriptions.create_request(
                create_invocation, self._private_request()
            )

        create_task = None
        revoke_task = None
        try:
            with patch.object(
                self.runtime.lifecycle.admission,
                "mutation",
                observed_mutation,
            ):
                with patch.object(
                    self.runtime.repositories.lifecycle,
                    "apply",
                    block_create_at_real_commit,
                ):
                    create_task = asyncio.create_task(create_with_grant())
                    await asyncio.wait_for(create_commit_entered.wait(), timeout=3)

                    revoke_task = asyncio.create_task(
                        self.accounts.revoke(revoke_invocation, alice_reference)
                    )
                    await asyncio.wait_for(revoke_requested.wait(), timeout=3)
                    self.assertFalse(revoke_acquired.is_set())

                    release_create_commit.set()
                    created = await asyncio.wait_for(create_task, timeout=5)
                    await asyncio.wait_for(revoke_acquired.wait(), timeout=3)

                    committed_private = (
                        await self.runtime.repositories.subscriptions.current(
                            created.subscription_id
                        )
                    )
                    committed_link = (
                        await self.runtime.repositories.jobs.current_for_subscription(
                            created.subscription_id
                        )
                    )
                    self.assertIsNotNone(committed_private)
                    self.assertIs(committed_private.status, SubscriptionStatus.ACTIVE)
                    self.assertEqual(committed_private.revision, 1)
                    self.assertIsNotNone(committed_link)
                    self.assertEqual(
                        await self._records_for_grant(
                            "principal-alice", alice_reference
                        ),
                        (committed_private,),
                    )
                    self.assertEqual(
                        len(await self._due_jobs_for_grant(alice_reference)), 1
                    )

                    release_revoke_body.set()
                    await asyncio.wait_for(revoke_task, timeout=5)

            revoked = await self.runtime.host_repositories.authorization.current_grant(
                alice_grant.grant_id
            )
            self.assertIsNotNone(revoked)
            self.assertIs(revoked.status, GrantStatus.REVOKED)
            self.assertEqual(revoked.revision, alice_reference.revision + 1)

            cancelled_private = await self.runtime.repositories.subscriptions.current(
                created.subscription_id
            )
            self.assertIsNotNone(cancelled_private)
            self.assertIs(cancelled_private.status, SubscriptionStatus.CANCELLED)
            self.assertEqual(cancelled_private.revision, 2)
            self.assertEqual(cancelled_private.grant, alice_reference)
            self.assertIsNone(
                await self.runtime.repositories.jobs.current_for_subscription(
                    created.subscription_id
                )
            )
            self.assertEqual(
                await self._records_for_grant("principal-alice", alice_reference),
                (),
            )
            self.assertEqual(await self._due_jobs_for_grant(alice_reference), ())

            current_public = await self.runtime.repositories.subscriptions.current(
                public_record.subscription_id
            )
            current_bob_public = await self.runtime.repositories.subscriptions.current(
                bob_public_record.subscription_id
            )
            current_bob_private = await self.runtime.repositories.subscriptions.current(
                bob_private_record.subscription_id
            )
            self.assertEqual(current_public, public_record)
            self.assertEqual(current_bob_public, bob_public_record)
            self.assertEqual(current_bob_private, bob_private_record)
            self.assertIs(current_public.status, SubscriptionStatus.ACTIVE)
            self.assertIs(current_bob_private.status, SubscriptionStatus.ACTIVE)
            self.assertEqual(
                await self.runtime.repositories.jobs.current_for_subscription(
                    public_record.subscription_id
                ),
                public_link,
            )
            self.assertEqual(
                await self.runtime.repositories.jobs.current_for_subscription(
                    bob_public_record.subscription_id
                ),
                bob_public_link,
            )
            self.assertEqual(
                await self.runtime.repositories.jobs.current_for_subscription(
                    bob_private_record.subscription_id
                ),
                bob_private_link,
            )
            public_jobs_after = tuple(
                job.key
                for job in await self.runtime.repositories.scheduler.list_due_jobs(
                    now=self.clock(), limit=100
                )
                if job.key.scope.kind is OwnershipKind.PUBLIC
            )
            bob_jobs_after = tuple(
                job.key
                for job in await self.runtime.repositories.scheduler.list_due_jobs(
                    now=self.clock(), limit=100
                )
                if job.key.scope.kind is OwnershipKind.AUTHORIZED
                and job.key.scope.grant == bob_reference
            )
            self.assertEqual(public_jobs_after, public_jobs_before)
            self.assertEqual(bob_jobs_after, bob_jobs_before)
        finally:
            release_create_commit.set()
            release_revoke_body.set()
            for task in (create_task, revoke_task):
                if task is not None and not task.done():
                    await asyncio.gather(task, return_exceptions=True)
            self.runtime.issuer.release(create_invocation)
            self.runtime.issuer.release(revoke_invocation)


if __name__ == "__main__":
    unittest.main()
