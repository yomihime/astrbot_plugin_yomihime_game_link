"""B04-R persistence probes over real temporary SQLite files."""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.api.display import DisplayDocument, Privacy, TextBlock
from ygl_test_subject.api.storage import GrantReference, OwnerScope
from ygl_test_subject.api.subscriptions import (
    CollectionKey,
    ConversationKind,
    ConversationRef,
    DeliveryAttempt,
    DeliveryEvent,
    DeliveryState,
    DigestEnvelope,
    DigestEnvelopeState,
    DigestMember,
    DigestMemberAssociation,
    DigestMemberDisposition,
    DigestMemberReceipt,
    DigestScheduleProfile,
    DigestWindow,
    DigestWindowSelector,
    DstFoldPolicy,
    DstGapPolicy,
    EvaluationState,
    NormalizedInput,
    Observation,
    ObservationCompleteness,
    ObservationCursor,
    ObservationEvaluationCommit,
    SubscriptionEvaluationCommit,
    SubscriptionJobAssociation,
    SubscriptionJobChange,
    SubscriptionJobChangeKind,
    SubscriptionRecord,
    SubscriptionStatus,
    delivery_idempotency_key,
    digest_envelope_idempotency_key,
)
from ygl_test_subject.core.ports import (
    CollectionRunRequest,
    RevisionConflict,
    UniqueConstraintViolation,
)
from ygl_test_subject.infrastructure.sqlite import repositories_subscriptions
from ygl_test_subject.infrastructure.sqlite.database import (
    SQLiteDatabase,
    SQLiteUnitOfWork,
)
from ygl_test_subject.infrastructure.sqlite.repositories_subscriptions import (
    SQLiteDeliveryRepository,
    SQLiteDigestWindowRepository,
    SQLiteSchedulerRepository,
    SQLiteSubscriptionJobRepository,
    SQLiteSubscriptionLifecycleRepository,
    SQLiteSubscriptionStore,
)

from tests.fixtures.b04_runtime import (
    create_digest_envelope_fixture,
    create_subscription_event_fixture,
    initialize_subscription_gate_fixture,
    replace_subscription_gate_fixture,
    synthetic_subscription_gate_bindings,
)


class _RepositoryClockMeta(type):
    def __instancecheck__(cls, instance):
        return isinstance(instance, datetime)


class _RepositoryClock(datetime, metaclass=_RepositoryClockMeta):
    """Fixed UTC clock used by SQLite's live-lease commit guard in tests."""

    @classmethod
    def now(cls, tz=None):
        current = datetime(2026, 9, 25, 12, tzinfo=UTC)
        return current if tz is None else current.astimezone(tz)


class B04SubscriptionRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        repository_clock = patch.object(
            repositories_subscriptions, "datetime", _RepositoryClock
        )
        repository_clock.start()
        self.addCleanup(repository_clock.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "runtime.sqlite3"
        self.db = SQLiteDatabase(self.path)
        self.now = datetime(2026, 9, 25, 12, tzinfo=UTC)
        self.bindings = synthetic_subscription_gate_bindings(("sample/game",))
        self.lifecycle = SQLiteSubscriptionLifecycleRepository(
            self.db, subscription_gate_bindings=self.bindings
        )
        self.jobs = SQLiteSubscriptionJobRepository(self.db)
        self.subscriptions = SQLiteSubscriptionStore(self.db)
        self.scheduler = SQLiteSchedulerRepository(
            self.db, subscription_gate_bindings=self.bindings
        )
        self.windows = SQLiteDigestWindowRepository(
            self.db, subscription_gate_bindings=self.bindings
        )
        self.delivery = SQLiteDeliveryRepository(
            self.db, subscription_gate_bindings=self.bindings
        )

    async def asyncSetUp(self) -> None:
        await initialize_subscription_gate_fixture(
            self.db, self.bindings, self.now - timedelta(days=2)
        )

    async def test_gate_projection_is_one_read_transaction_without_private_or_write_access(
        self,
    ):
        import sqlite3

        allowed = {
            "subscription_gate_bootstrap",
            "subscription_gate_initializations",
            "config_entries",
            "module_runtime_intents",
        }
        statements = []
        original = self.db.executor.run_read

        async def guarded(callback):
            def read(unit):
                changes = unit.connection.total_changes

                def authorize(action, table, column, *_):
                    if action == sqlite3.SQLITE_READ:
                        self.assertIn(table, allowed)
                        self.assertNotIn("secret", column)
                    if action in (
                        sqlite3.SQLITE_INSERT,
                        sqlite3.SQLITE_UPDATE,
                        sqlite3.SQLITE_DELETE,
                    ):
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK

                unit.connection.set_authorizer(authorize)
                unit.connection.set_trace_callback(statements.append)
                try:
                    result = callback(unit)
                    self.assertEqual(unit.connection.total_changes, changes)
                    return result
                finally:
                    unit.connection.set_authorizer(None)
                    unit.connection.set_trace_callback(None)

            return await original(read)

        with patch.object(self.db.executor, "run_read", side_effect=guarded) as reads:
            result = await self.lifecycle.read_subscription_gate_state("sample/game")
        self.assertTrue(result.supported)
        self.assertTrue(result.enabled)
        self.assertTrue(result.intent_enabled)
        self.assertIsNone(result.reason)
        self.assertEqual(reads.call_count, 1)
        self.assertEqual(sum(s.startswith("SELECT") for s in statements), 4)
        self.assertEqual(
            (
                await SQLiteSubscriptionLifecycleRepository(
                    self.db
                ).read_subscription_gate_state("sample/game")
            ).reason,
            "unsupported",
        )

    async def test_gate_projection_preserves_false_but_prioritizes_corruption(self):
        tables = (
            "subscription_gate_bootstrap",
            "subscription_gate_initializations",
            "config_entries",
            "module_runtime_intents",
        )

        def backup(unit):
            return {
                table: [dict(row) for row in unit.execute("SELECT * FROM " + table)]
                for table in tables
            }

        saved = await self.db.executor.run_read(backup)
        cases = (
            (
                "UPDATE config_entries SET value_json='false',subscription_transition_at=NULL",
                False,
                None,
            ),
            (
                "UPDATE config_entries SET value_json='true',subscription_transition_at=NULL",
                None,
                "fence_invalid",
            ),
            ("UPDATE config_entries SET value_json='1'", None, "config_invalid"),
            ("UPDATE config_entries SET value_json='broken'", None, "config_invalid"),
            ("DELETE FROM config_entries", None, "config_missing"),
            (
                "UPDATE subscription_gate_bootstrap SET phase='pending'",
                None,
                "initialization_invalid",
            ),
            (
                "DELETE FROM subscription_gate_initializations",
                None,
                "initialization_invalid",
            ),
            (
                "UPDATE config_entries SET value_json='false',revision=0",
                None,
                "fence_invalid",
            ),
            (
                "UPDATE config_entries SET value_json='false'; DELETE FROM module_runtime_intents",
                None,
                "fence_invalid",
            ),
            (
                "UPDATE config_entries SET value_json='false'; UPDATE module_runtime_intents SET desired_enabled=2",
                None,
                "fence_invalid",
            ),
            (
                "UPDATE config_entries SET value_json='false'; UPDATE module_runtime_intents SET intent_revision=0",
                None,
                "fence_invalid",
            ),
            (
                "UPDATE config_entries SET value_json='false'; UPDATE module_runtime_intents SET updated_at='broken'",
                None,
                "fence_invalid",
            ),
            (
                "UPDATE config_entries SET value_json='false',subscription_transition_at=NULL; UPDATE module_runtime_intents SET desired_enabled=0,updated_at='broken'",
                False,
                None,
            ),
        )
        for sql, enabled, reason in cases:
            with self.subTest(sql=sql):

                def mutate(unit):
                    unit.execute("PRAGMA ignore_check_constraints=ON")
                    for statement in sql.split(";"):
                        unit.execute(statement)
                    unit.execute("PRAGMA ignore_check_constraints=OFF")

                await self.db.executor.run_transaction(mutate, begin_mode="IMMEDIATE")
                result = await self.lifecycle.read_subscription_gate_state(
                    "sample/game"
                )
                self.assertEqual(result.enabled, enabled)
                self.assertEqual(result.reason, reason)

                def restore(unit):
                    for table in tables:
                        unit.execute("DELETE FROM " + table)
                        for row in saved[table]:
                            columns = ",".join(row)
                            unit.execute(
                                f"INSERT INTO {table}({columns}) VALUES ({','.join('?' for _ in row)})",
                                tuple(row.values()),
                            )

                await self.db.executor.run_transaction(restore, begin_mode="IMMEDIATE")

    async def test_delivery_null_or_old_fences_and_missing_policy_never_reopen(self):
        record = await self._create()
        legacy = await self.delivery.create_event(
            self._event(record, event_key="legacy-null")
        )
        fresh = await create_subscription_event_fixture(
            self.delivery, self.bindings, self._event(record, event_key="fresh")
        )

        async def claim(repository, event, state=DeliveryState.PENDING, number=1):
            return await repository.claim_sending(
                event.event_key,
                event.event_version,
                subscription_id=event.subscription_id,
                subscription_revision=event.subscription_revision,
                expected_state=state,
                attempt_number=number,
                started_at=self.now,
                now=self.now,
            )

        self.assertIsNone(await claim(self.delivery, legacy))
        self.assertIsNone(await claim(SQLiteDeliveryRepository(self.db), fresh))
        sending = await claim(self.delivery, fresh)
        self.assertTrue(await self.delivery.is_current_for_send(sending))
        self.assertFalse(
            await self.delivery.is_current_for_send(
                replace(
                    sending,
                    attempt=replace(
                        sending.attempt, started_at=self.now - timedelta(seconds=1)
                    ),
                )
            )
        )
        failed = await self.delivery.record_attempt(
            fresh.event_key,
            fresh.event_version,
            DeliveryAttempt(
                1,
                DeliveryState.FAILED,
                fresh.idempotency_key,
                self.now,
                self.now,
                "temporary",
            ),
            subscription_id=fresh.subscription_id,
            subscription_revision=fresh.subscription_revision,
            expected_state=DeliveryState.SENDING,
            retry_at=self.now,
        )
        await replace_subscription_gate_fixture(
            self.db, self.bindings, "sample/game", False, self.now
        )
        await replace_subscription_gate_fixture(
            self.db, self.bindings, "sample/game", True, self.now
        )
        self.assertIsNone(await claim(self.delivery, failed, DeliveryState.FAILED, 2))
        self.assertEqual(
            await self.delivery.current_event(
                fresh.event_key,
                1,
                subscription_id=record.subscription_id,
                subscription_revision=1,
            ),
            failed,
        )
        stamps = await self.db.executor.run_read(
            lambda unit: tuple(
                tuple(row)
                for row in unit.execute(
                    "SELECT event_key,gate_revision,intent_revision FROM b04_delivery_events ORDER BY event_key"
                ).fetchall()
            )
        )
        self.assertEqual(stamps, (("fresh", 1, 1), ("legacy-null", None, None)))

    async def test_digest_due_must_exceed_both_cutoffs_and_stale_failed_stays_failed(
        self,
    ):
        record = await self._create()
        intent_cutoff = self.now - timedelta(hours=1)
        await self.db.executor.run_transaction(
            lambda unit: unit.execute(
                "UPDATE module_runtime_intents SET intent_revision=2,updated_at=? WHERE package_id='sample' AND module_id='game'",
                (intent_cutoff.isoformat(),),
            ).rowcount,
            begin_mode="IMMEDIATE",
        )
        for label, due in (
            ("equal", intent_cutoff),
            ("before", intent_cutoff - timedelta(microseconds=1)),
            ("after", intent_cutoff + timedelta(microseconds=1)),
        ):
            with self.subTest(label=label):
                event = self._event(record, event_key="boundary-" + label)
                member = DigestMember(record.subscription_id, 1, event.event_key, 1)
                window = DigestWindow(
                    "boundary-" + label,
                    "UTC",
                    "daily",
                    intent_cutoff - timedelta(hours=2),
                    intent_cutoff - timedelta(hours=1),
                    due,
                    DstFoldPolicy.FIRST_OCCURRENCE,
                    DstGapPolicy.SKIP,
                    (member,),
                )
                await self.windows.create(window)
                envelope = DigestEnvelope(
                    "envelope-" + label,
                    window.window_id,
                    record.recipient,
                    (member,),
                    member_associations=(
                        DigestMemberAssociation(
                            window.window_id, record.recipient, member, event
                        ),
                    ),
                )
                await create_digest_envelope_fixture(
                    self.delivery, self.bindings, envelope
                )
                claim = await self.windows.claim_due_envelope(
                    window.window_id,
                    record.recipient,
                    now=self.now,
                    lease_expires_at=self.now + timedelta(minutes=1),
                )
                if label != "after":
                    self.assertIsNone(claim)
                    self.assertEqual(
                        await self.windows.current_envelope(
                            window.window_id, record.recipient
                        ),
                        envelope,
                    )
                    continue
                sending = await self.windows.begin_envelope_send(
                    claim,
                    expected_revision=claim.envelope.revision,
                    attempt_number=1,
                    started_at=self.now,
                )
                self.assertTrue(
                    await self.windows.is_current_for_send(claim, sending, now=self.now)
                )
                attempt = DeliveryAttempt(
                    1,
                    DeliveryState.FAILED,
                    sending.delivery_attempts[-1].idempotency_key,
                    self.now,
                    claim.expires_at + timedelta(seconds=1),
                    "temporary",
                )
                failed = await self.windows.complete_envelope_send(
                    claim,
                    attempt,
                    expected_revision=sending.revision,
                    retry_at=claim.expires_at + timedelta(seconds=2),
                )
                self.assertEqual(failed.state, DigestEnvelopeState.FAILED)
                self.now = claim.expires_at + timedelta(seconds=3)
                await replace_subscription_gate_fixture(
                    self.db, self.bindings, "sample/game", False, self.now
                )
                await replace_subscription_gate_fixture(
                    self.db, self.bindings, "sample/game", True, self.now
                )
                self.assertIsNone(
                    await self.windows.retry_failed_envelope(
                        failed.envelope_id,
                        expected_revision=failed.revision,
                        now=self.now,
                    )
                )
                self.assertEqual(
                    await self.windows.current_envelope(
                        window.window_id, record.recipient
                    ),
                    failed,
                )

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _key(scope: OwnerScope | None = None) -> CollectionKey:
        return CollectionKey(
            "sample/game",
            "collector",
            1,
            "source",
            NormalizedInput({"b": 2, "a": 1}),
            scope or OwnerScope.public(),
        )

    @staticmethod
    def _recipient(user: str = "u1") -> ConversationRef:
        return ConversationRef(
            "adapter", ConversationKind.DIRECT, user, f"private-{user}"
        )

    def _record(
        self,
        subscription_id: str = "sub-1",
        *,
        revision: int = 1,
        owner: str = "u1",
        key: CollectionKey | None = None,
        status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
        type_id: str | None = None,
        digest_schedule: DigestScheduleProfile | None = None,
        notification_mode: str = "instant",
    ) -> SubscriptionRecord:
        key = key or self._key()
        grant = key.scope.grant
        return SubscriptionRecord(
            subscription_id,
            revision,
            key.module_id,
            key,
            owner,
            grant,
            self._recipient(owner),
            notification_mode,
            {"secret-filter": "do-not-log"},
            status,
            type_id,
            digest_schedule,
        )

    def _association(
        self,
        record: SubscriptionRecord,
        *,
        revision: int = 1,
        assoc_revision: int = 1,
        cadence: float = 30,
    ) -> SubscriptionJobAssociation:
        return SubscriptionJobAssociation(
            record.subscription_id,
            record.revision,
            record.collection_key,
            cadence,
            1,
            assoc_revision,
        )

    async def _create(
        self,
        record: SubscriptionRecord | None = None,
        *,
        initial_run: CollectionRunRequest | None = None,
    ) -> SubscriptionRecord:
        record = record or self._record()
        change = SubscriptionJobChange(
            SubscriptionJobChangeKind.CREATE,
            record,
            self._association(record),
            None,
            None,
        )
        return await self.lifecycle.apply(
            change,
            initial_run=initial_run or self._run(record.collection_key),
        )

    def _run(self, key: CollectionKey | None = None) -> CollectionRunRequest:
        return CollectionRunRequest(
            key or self._key(), self.now - timedelta(seconds=1), 30, 1, 1, 1
        )

    async def _stored_job_schedule(self, key: CollectionKey):
        identity = repositories_subscriptions._job_key(key)

        def _read(unit):
            row = unit.execute(
                "SELECT due_at,cadence_seconds,config_revision,module_epoch,registry_revision,lease_token,lease_expires_at FROM b04_collection_jobs WHERE job_key=?",
                (identity,),
            ).fetchone()
            return None if row is None else tuple(row)

        return await self.db.executor.run_read(_read)

    def _observation(
        self,
        key: CollectionKey | None = None,
        *,
        identity: str = "obs-1",
        completeness: ObservationCompleteness = ObservationCompleteness.COMPLETE,
        covered: tuple[str, ...] = (),
    ) -> Observation:
        return Observation(
            identity,
            key or self._key(),
            1,
            self.now,
            self.now,
            completeness,
            covered,
            {"snapshot": [1, 2]},
        )

    def _event(
        self,
        record: SubscriptionRecord,
        *,
        event_key: str = "event-1",
        version: int = 1,
        text: str = "private payload",
    ) -> DeliveryEvent:
        doc = DisplayDocument(
            "title", "subject", (TextBlock(text),), privacy=Privacy.PRIVATE
        )
        return DeliveryEvent(
            event_key,
            version,
            record.subscription_id,
            record.revision,
            record.owner_id,
            record.grant,
            record.recipient,
            doc,
            delivery_idempotency_key(
                event_key,
                version,
                record.subscription_id,
                record.revision,
                record.recipient,
            ),
        )

    def _window(
        self,
        window_id: str = "window-1",
        *,
        due: datetime | None = None,
        members: tuple[DigestMember, ...] = (),
    ) -> DigestWindow:
        start = self.now - timedelta(hours=2)
        end = self.now - timedelta(hours=1)
        due = due or self.now - timedelta(minutes=1)
        return DigestWindow(
            window_id,
            "Europe/Paris",
            "daily-12",
            start,
            end,
            due,
            DstFoldPolicy.FIRST_OCCURRENCE,
            DstGapPolicy.SKIP,
            members,
        )

    async def _lease(self, key: CollectionKey | None = None):
        request = self._run(key)
        lease = await self.scheduler.claim_due(request, now=self.now)
        self.assertIsNotNone(lease)
        return lease

    async def _digest_claim_fixture(self, suffix: str = "reconcile"):
        record = await self._create(self._record(f"sub-{suffix}", owner="u1"))
        window = self._window(f"window-{suffix}")
        await self.windows.create(window)
        event = self._event(record, event_key=f"event-{suffix}")
        member = DigestMember(
            record.subscription_id, record.revision, event.event_key, 1
        )
        association = DigestMemberAssociation(
            window.window_id, record.recipient, member, event
        )
        observation = self._observation(identity=f"obs-{suffix}")
        item = SubscriptionEvaluationCommit(
            record.subscription_id,
            record.revision,
            None,
            EvaluationState(1, {}),
            ObservationCursor(
                observation.observation_id,
                1,
                observation.completeness,
                (),
            ),
            (event,),
            (association,),
        )
        lease = await self._lease()
        self.assertTrue(
            await self.scheduler.commit_observation_with_evaluations(
                lease, ObservationEvaluationCommit(observation, (item,))
            )
        )
        claim = await self.windows.claim_due_envelope(
            window.window_id,
            record.recipient,
            now=self.now,
            lease_expires_at=self.now + timedelta(minutes=1),
        )
        return record, window, event, member, claim

    async def _immutable_digest_fixture(
        self, suffix: str, *, failed_event: bool = False
    ):
        key = CollectionKey(
            "sample/game",
            "collector",
            1,
            "source",
            NormalizedInput({"immutable-history": suffix}),
            OwnerScope.public(),
        )
        record = await self._create(self._record(f"sub-immutable-{suffix}", key=key))
        event = self._event(record, event_key=f"event-immutable-{suffix}")
        window = self._window(f"window-immutable-{suffix}")
        await self.windows.create(window)
        member = DigestMember(
            record.subscription_id,
            record.revision,
            event.event_key,
            event.event_version,
        )
        association = DigestMemberAssociation(
            window.window_id, record.recipient, member, event
        )
        observation = self._observation(key, identity=f"obs-immutable-{suffix}")
        item = SubscriptionEvaluationCommit(
            record.subscription_id,
            record.revision,
            None,
            EvaluationState(1, {}),
            ObservationCursor(
                observation.observation_id,
                1,
                observation.completeness,
                (),
            ),
            (event,),
            (association,),
        )
        lease = await self._lease(key)
        self.assertTrue(
            await self.scheduler.commit_observation_with_evaluations(
                lease, ObservationEvaluationCommit(observation, (item,))
            )
        )
        claim = await self.windows.claim_due_envelope(
            window.window_id,
            record.recipient,
            now=self.now,
            lease_expires_at=self.now + timedelta(minutes=1),
        )
        self.assertIsNotNone(claim)
        if failed_event:
            failed_attempt = DeliveryAttempt(
                1,
                DeliveryState.FAILED,
                event.idempotency_key,
                self.now,
                self.now,
                error_code="temporary",
            )
            event = await self.delivery.record_attempt(
                event.event_key,
                event.event_version,
                failed_attempt,
                subscription_id=event.subscription_id,
                subscription_revision=event.subscription_revision,
                expected_state=DeliveryState.PENDING,
                retry_at=self.now,
            )
        return record, window, event, member, claim

    async def test_unmapped_execution_is_closed_even_with_valid_persisted_gate(self):
        record = await self._create()
        lease = await self._lease()
        unbound_lifecycle = SQLiteSubscriptionLifecycleRepository(self.db)
        unbound_scheduler = SQLiteSchedulerRepository(self.db)
        self.assertIsNone(await unbound_lifecycle.current_fence(record.module_id))
        with self.assertRaises(PermissionError):
            await unbound_lifecycle.apply(
                SubscriptionJobChange(
                    SubscriptionJobChangeKind.CREATE,
                    self._record("unmapped"),
                    self._association(self._record("unmapped")),
                    None,
                    None,
                ),
                initial_run=self._run(),
            )
        self.assertIsNone(await unbound_scheduler.claim_due(self._run(), now=self.now))
        self.assertFalse(await unbound_scheduler.is_current(lease, now=self.now))
        self.assertFalse(
            await unbound_scheduler.commit_observation(lease, self._observation())
        )
        self.assertIsNone(await self.subscriptions.current("unmapped"))

    async def test_pause_resume_and_intent_toggle_invalidate_old_lease_but_reclaim_recurring_job(
        self,
    ):
        record = await self._create()
        before = await self.db.executor.run_read(
            lambda u: tuple(
                u.execute(
                    "SELECT gate_revision,intent_revision FROM b04_collection_jobs"
                ).fetchone()
            )
        )
        self.assertEqual(before, (None, None))
        lease = await self._lease()
        self.assertIsNotNone(lease.subscription_fence)
        await replace_subscription_gate_fixture(
            self.db, self.bindings, record.module_id, False, self.now
        )
        self.assertFalse(await self.scheduler.is_current(lease, now=self.now))
        self.assertFalse(
            await self.scheduler.commit_observation(lease, self._observation())
        )
        await self.scheduler.release(lease)
        self.assertIsNone(await self.scheduler.claim_due(self._run(), now=self.now))
        await replace_subscription_gate_fixture(
            self.db, self.bindings, record.module_id, True, self.now
        )
        renewed = await self._lease()
        self.assertNotEqual(renewed.subscription_fence, lease.subscription_fence)
        self.assertFalse(
            await self.scheduler.commit_observation(lease, self._observation())
        )
        await replace_subscription_gate_fixture(
            self.db, self.bindings, record.module_id, True, self.now
        )
        self.assertTrue(await self.scheduler.is_current(renewed, now=self.now))
        await self.db.executor.run_transaction(
            lambda u: u.execute(
                "UPDATE module_runtime_intents SET desired_enabled=0,intent_revision=2"
            ).rowcount,
            begin_mode="IMMEDIATE",
        )
        self.assertFalse(await self.scheduler.is_current(renewed, now=self.now))
        await self.db.executor.run_transaction(
            lambda u: u.execute(
                "UPDATE module_runtime_intents SET desired_enabled=1,intent_revision=3"
            ).rowcount,
            begin_mode="IMMEDIATE",
        )
        self.assertFalse(await self.scheduler.is_current(renewed, now=self.now))
        await self.scheduler.release(renewed)
        fresh = await self._lease()
        self.assertEqual(fresh.subscription_fence.intent_revision, 3)
        self.assertTrue(await self.scheduler.is_current(fresh, now=self.now))
        self.assertFalse(
            await self.scheduler.is_current(
                replace(fresh, subscription_fence=None), now=self.now
            )
        )

    async def test_old_null_or_different_event_stamp_rolls_back_checkpoint_cursor_and_observation(
        self,
    ):
        record = await self._create()
        first = self._observation(identity="baseline")
        lease = await self._lease()
        initial = SubscriptionEvaluationCommit(
            record.subscription_id,
            1,
            None,
            EvaluationState(1, {"seen": "baseline"}),
            ObservationCursor(
                first.observation_id,
                first.data_version,
                first.completeness,
                first.covered_ids,
            ),
        )
        self.assertTrue(
            await self.scheduler.commit_observation_with_evaluations(
                lease, ObservationEvaluationCommit(first, (initial,))
            )
        )
        event = self._event(record)
        await self.delivery.create_event(event)
        self.now += timedelta(seconds=31)
        lease = await self._lease()
        observation = self._observation(identity="must-rollback")
        candidate = SubscriptionEvaluationCommit(
            record.subscription_id,
            1,
            1,
            EvaluationState(2, {"seen": "new"}),
            ObservationCursor(
                observation.observation_id,
                observation.data_version,
                observation.completeness,
                observation.covered_ids,
            ),
            (event,),
        )

        def snapshot(unit):
            return (
                tuple(unit.execute("SELECT * FROM b04_evaluation_states").fetchone()),
                tuple(
                    unit.execute(
                        "SELECT observation_id FROM b04_collection_jobs"
                    ).fetchone()
                ),
                tuple(
                    tuple(row) for row in unit.execute("SELECT * FROM b04_observations")
                ),
            )

        before = await self.db.executor.run_read(snapshot)
        for stamp in ((None, None), (99, 99)):
            with self.subTest(stamp=stamp):
                await self.db.executor.run_transaction(
                    lambda u: u.execute(
                        "UPDATE b04_delivery_events SET gate_revision=?,intent_revision=?",
                        stamp,
                    ).rowcount,
                    begin_mode="IMMEDIATE",
                )
                self.assertFalse(
                    await self.scheduler.commit_observation_with_evaluations(
                        lease, ObservationEvaluationCommit(observation, (candidate,))
                    )
                )
                self.assertEqual(await self.db.executor.run_read(snapshot), before)
                actual = await self.db.executor.run_read(
                    lambda u: tuple(
                        u.execute(
                            "SELECT gate_revision,intent_revision FROM b04_delivery_events"
                        ).fetchone()
                    )
                )
                self.assertEqual(actual, stamp)
                self.assertTrue(await self.scheduler.is_current(lease, now=self.now))

    async def test_r01_real_full_migration_reopens_at_0100(self) -> None:
        self.assertEqual(self.db.initialize(), 100)
        self.assertEqual(self.db.initialize(), 100)
        self.assertEqual(self.db.schema_version(), 100)
        reopened = SQLiteDatabase(self.path)
        self.assertEqual(reopened.schema_version(), 100)

    async def test_repository_sql_callbacks_run_on_worker_thread(self) -> None:
        caller_thread = threading.get_ident()
        sql_threads: list[int] = []
        original_execute = SQLiteUnitOfWork.execute

        def track_execute(unit, sql, parameters=()):
            sql_threads.append(threading.get_ident())
            return original_execute(unit, sql, parameters)

        record = self._record()
        with patch.object(SQLiteUnitOfWork, "execute", track_execute):
            await self.subscriptions.create(record)
        self.assertTrue(sql_threads)
        self.assertNotIn(caller_thread, sql_threads)
        self.assertEqual(len(set(sql_threads)), 1)

        sql_threads.clear()
        with patch.object(SQLiteUnitOfWork, "execute", track_execute):
            self.assertEqual(
                await self.subscriptions.current(record.subscription_id), record
            )
        self.assertTrue(sql_threads)
        self.assertNotIn(caller_thread, sql_threads)
        self.assertEqual(len(set(sql_threads)), 1)

    async def test_r01_owner_and_active_digest_schedule_scans_page_stably(self) -> None:
        profile = DigestScheduleProfile(
            "Europe/Paris",
            "12:00",
            3600,
            DstFoldPolicy.FIRST_OCCURRENCE,
            DstGapPolicy.SKIP,
        )
        records = (
            self._record("sub-a", owner="u1"),
            self._record(
                "sub-b",
                owner="u1",
                type_id="sample/game:item",
                digest_schedule=profile,
                notification_mode="digest",
            ),
            self._record("sub-c", owner="u1"),
            self._record(
                "sub-d",
                owner="u2",
                type_id="sample/game:item",
                digest_schedule=profile,
                notification_mode="digest",
            ),
        )
        for record in records:
            await self._create(record)
        cancelled = self._record(
            "sub-c", revision=2, owner="u1", status=SubscriptionStatus.CANCELLED
        )
        await self.lifecycle.apply(
            SubscriptionJobChange(
                SubscriptionJobChangeKind.CANCEL, cancelled, None, 1, 1
            )
        )

        first = await self.subscriptions.list_for_owner("u1", limit=2)
        self.assertEqual(
            tuple(item.subscription_id for item in first), ("sub-a", "sub-b")
        )
        second = await self.subscriptions.list_for_owner(
            "u1", limit=2, after_subscription_id=first[-1].subscription_id
        )
        self.assertEqual(second, ())
        schedules = await self.subscriptions.list_active_digest_schedules(limit=1)
        self.assertEqual(
            tuple(item.record.subscription_id for item in schedules), ("sub-b",)
        )
        reopened = SQLiteSubscriptionStore(SQLiteDatabase(self.path))
        resumed = await reopened.list_active_digest_schedules(
            limit=1, after_subscription_id=schedules[0].cursor
        )
        self.assertEqual(
            tuple(item.record.subscription_id for item in resumed), ("sub-d",)
        )
        self.assertEqual(
            await reopened.list_active_digest_schedules(
                limit=1, after_subscription_id=resumed[0].cursor
            ),
            (),
        )
        persisted = await reopened.current("sub-b")
        self.assertEqual(persisted.digest_schedule, profile)
        self.assertEqual(persisted.type_id, "sample/game:item")

    async def test_r01_scheduled_digest_window_selector_survives_restart(self) -> None:
        profile = DigestScheduleProfile(
            "UTC",
            "11:00",
            3600,
            DstFoldPolicy.FIRST_OCCURRENCE,
            DstGapPolicy.SKIP,
        )
        recipient = self._recipient()
        window = DigestWindow(
            "scheduled-window",
            "UTC",
            "2026-09-25",
            datetime(2026, 9, 25, 10, tzinfo=UTC),
            datetime(2026, 9, 25, 11, tzinfo=UTC),
            datetime(2026, 9, 25, 11, tzinfo=UTC),
            DstFoldPolicy.FIRST_OCCURRENCE,
            DstGapPolicy.SKIP,
            (),
            schedule_profile=profile,
            schedule_recipient=recipient,
        )
        await self.windows.create(window)
        selector = DigestWindowSelector(
            profile, recipient, datetime(2026, 9, 25, 10, 30, tzinfo=UTC)
        )
        reopened = SQLiteDigestWindowRepository(
            SQLiteDatabase(self.path), subscription_gate_bindings=self.bindings
        )
        found = await reopened.for_schedule(selector)
        self.assertEqual(found, window)
        self.assertIsNone(
            await reopened.for_schedule(
                DigestWindowSelector(
                    profile,
                    self._recipient("other"),
                    datetime(2026, 9, 25, 10, 30, tzinfo=UTC),
                )
            )
        )

    async def test_r02_concurrent_public_claim_has_one_winner(self) -> None:
        request = self._run()
        results = await asyncio.gather(
            *(self.scheduler.claim_due(request, now=self.now) for _ in range(2))
        )
        self.assertEqual(sum(item is not None for item in results), 1)

    async def test_scheduler_lease_current_matches_exact_persistent_claim(self) -> None:
        lease = await self._lease()
        self.assertTrue(await self.scheduler.is_current(lease, now=self.now))

        stale = (
            replace(lease, key=self._key(OwnerScope.user("other"))),
            replace(lease, token="stale-token"),
            replace(lease, config_revision=lease.config_revision + 1),
            replace(lease, module_epoch=lease.module_epoch + 1),
            replace(lease, registry_revision=lease.registry_revision + 1),
            replace(lease, expires_at=lease.expires_at + timedelta(seconds=1)),
        )
        for candidate in stale:
            with self.subTest(candidate=candidate):
                self.assertFalse(
                    await self.scheduler.is_current(candidate, now=self.now)
                )
        self.assertFalse(await self.scheduler.is_current(lease, now=lease.expires_at))

        unrelated = CollectionRunRequest(
            self._key(OwnerScope.user("unrelated")),
            self.now - timedelta(seconds=1),
            30,
            1,
            1,
            99,
        )
        other_lease = await self.scheduler.claim_due(unrelated, now=self.now)
        self.assertIsNotNone(other_lease)
        self.assertTrue(await self.scheduler.is_current(lease, now=self.now))

        await self.scheduler.release(lease)
        self.assertFalse(await self.scheduler.is_current(lease, now=self.now))

    async def test_r02_due_job_scan_uses_active_links_and_composite_cursor(
        self,
    ) -> None:
        record = await self._create()
        lease = await self._lease()
        self.assertEqual(await self.scheduler.list_due_jobs(now=self.now, limit=5), ())
        await self.scheduler.release(lease)
        other_key = self._key(OwnerScope.user("u2"))
        await self._create(self._record("sub-2", owner="u2", key=other_key))
        other_lease = await self._lease(other_key)
        await self.scheduler.release(other_lease)
        due = await self.scheduler.list_due_jobs(now=self.now, limit=1)
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0].cursor.job_key, due[0].cursor_key)
        second_page = await self.scheduler.list_due_jobs(
            now=self.now, limit=1, after_cursor=due[0].cursor
        )
        self.assertEqual(len(second_page), 1)
        self.assertNotEqual(due[0].key, second_page[0].key)
        self.assertIn(record.collection_key, (due[0].key, second_page[0].key))
        self.assertIn(other_key, (due[0].key, second_page[0].key))
        self.assertEqual(
            await self.scheduler.list_due_jobs(
                now=self.now, limit=1, after_cursor=second_page[0].cursor
            ),
            (),
        )

    async def test_lifecycle_create_arms_immediate_job_and_reopens_persisted_schedule(
        self,
    ) -> None:
        record = self._record()
        association = self._association(record, cadence=45)
        initial_run = CollectionRunRequest(record.collection_key, self.now, 45, 1, 7, 9)
        saved = await self.lifecycle.apply(
            SubscriptionJobChange(
                SubscriptionJobChangeKind.CREATE,
                record,
                association,
                None,
                None,
            ),
            initial_run=initial_run,
        )

        self.assertEqual(saved, record)
        due = await self.scheduler.list_due_jobs(now=self.now, limit=5)
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0].key, record.collection_key)
        self.assertEqual(due[0].due_at, self.now)
        self.assertEqual(due[0].cadence_seconds, 45)
        self.assertEqual(due[0].config_revision, 1)
        self.assertEqual(
            await self._stored_job_schedule(record.collection_key),
            (self.now.isoformat(), 45.0, 1, 7, 9, None, None),
        )

        reopened = SQLiteSchedulerRepository(
            SQLiteDatabase(self.path), subscription_gate_bindings=self.bindings
        )
        restored = await reopened.list_due_jobs(now=self.now, limit=5)
        self.assertEqual(restored, due)

    async def test_lifecycle_create_arms_legacy_null_job(self) -> None:
        record = self._record()
        legacy_request = CollectionRunRequest(
            record.collection_key,
            self.now + timedelta(hours=1),
            30,
            1,
            1,
            1,
        )
        self.assertIsNone(await self.scheduler.claim_due(legacy_request, now=self.now))
        self.assertEqual(
            await self._stored_job_schedule(record.collection_key),
            (None, None, None, None, None, None, None),
        )

        await self._create(
            record,
            initial_run=CollectionRunRequest(
                record.collection_key, self.now, 30, 1, 3, 4
            ),
        )
        due = await self.scheduler.list_due_jobs(now=self.now, limit=5)
        self.assertEqual(tuple(job.key for job in due), (record.collection_key,))
        self.assertEqual(
            await self._stored_job_schedule(record.collection_key),
            (self.now.isoformat(), 30.0, 1, 3, 4, None, None),
        )

    async def test_lifecycle_shared_create_preserves_armed_schedule_and_lease(
        self,
    ) -> None:
        first = self._record("sub-1", owner="u1")
        first_run = self._run(first.collection_key)
        await self._create(first, initial_run=first_run)
        lease = await self.scheduler.claim_due(first_run, now=self.now)
        self.assertIsNotNone(lease)

        second = self._record("sub-2", owner="u2")
        second_association = SubscriptionJobAssociation(
            second.subscription_id,
            second.revision,
            second.collection_key,
            60,
            2,
            1,
        )
        second_run = CollectionRunRequest(
            second.collection_key,
            self.now + timedelta(hours=1),
            60,
            2,
            8,
            9,
        )
        await self.lifecycle.apply(
            SubscriptionJobChange(
                SubscriptionJobChangeKind.CREATE,
                second,
                second_association,
                None,
                None,
            ),
            initial_run=second_run,
        )

        self.assertTrue(await self.scheduler.is_current(lease, now=self.now))
        await self.scheduler.release(lease)
        due = await self.scheduler.list_due_jobs(now=self.now, limit=5)
        self.assertEqual(tuple(job.key for job in due), (first.collection_key,))
        self.assertEqual(due[0].due_at, first_run.due_at)
        self.assertEqual(due[0].cadence_seconds, first_run.cadence_seconds)
        self.assertEqual(due[0].config_revision, first_run.config_revision)
        self.assertEqual(
            await self._stored_job_schedule(first.collection_key),
            (
                first_run.due_at.isoformat(),
                first_run.cadence_seconds,
                first_run.config_revision,
                first_run.module_epoch,
                first_run.registry_revision,
                None,
                None,
            ),
        )

    async def test_lifecycle_revise_arms_new_key_and_cas_failure_rolls_it_back(
        self,
    ) -> None:
        original = self._record("sub-revise", owner="u1")
        await self._create(original)
        new_key = self._key(OwnerScope.user("u1"))
        revised = self._record(
            original.subscription_id,
            revision=2,
            owner="u1",
            key=new_key,
        )
        association = self._association(revised, assoc_revision=2, cadence=42)
        initial_run = CollectionRunRequest(new_key, self.now, 42, 1, 4, 6)
        await self.lifecycle.apply(
            SubscriptionJobChange(
                SubscriptionJobChangeKind.REVISE,
                revised,
                association,
                1,
                1,
            ),
            initial_run=initial_run,
        )
        due = await self.scheduler.list_due_jobs(now=self.now, limit=5)
        self.assertEqual(tuple(job.key for job in due), (new_key,))
        self.assertEqual(due[0].due_at, self.now)
        self.assertEqual(due[0].cadence_seconds, 42)

        failed_key = CollectionKey(
            "sample/game",
            "collector",
            1,
            "alternate-source",
            NormalizedInput({"b": 2, "a": 1}),
            OwnerScope.user("u1"),
        )
        failed_record = self._record(
            original.subscription_id,
            revision=3,
            owner="u1",
            key=failed_key,
        )
        failed_association = self._association(
            failed_record, assoc_revision=4, cadence=50
        )
        with self.assertRaises(RevisionConflict):
            await self.lifecycle.apply(
                SubscriptionJobChange(
                    SubscriptionJobChangeKind.REVISE,
                    failed_record,
                    failed_association,
                    2,
                    3,
                ),
                initial_run=CollectionRunRequest(failed_key, self.now, 50, 1, 5, 7),
            )
        self.assertIsNone(await self._stored_job_schedule(failed_key))
        self.assertEqual(
            (await self.subscriptions.current(original.subscription_id)).revision,
            2,
        )

    async def test_lifecycle_initial_run_validation_rejects_missing_invalid_and_cancel(
        self,
    ) -> None:
        record = self._record()
        association = self._association(record)
        change = SubscriptionJobChange(
            SubscriptionJobChangeKind.CREATE,
            record,
            association,
            None,
            None,
        )
        with self.assertRaises(ValueError):
            await self.lifecycle.apply(change)
        with self.assertRaises(TypeError):
            CollectionRunRequest(record.collection_key, self.now, 30, 1, 1)

        other_key = self._key(OwnerScope.user("other"))
        invalid_runs = (
            CollectionRunRequest(other_key, self.now, 30, 1, 1, 1),
            CollectionRunRequest(record.collection_key, self.now, 31, 1, 1, 1),
            CollectionRunRequest(record.collection_key, self.now, 30, 2, 1, 1),
            object(),
        )
        for initial_run in invalid_runs:
            with self.subTest(initial_run=initial_run):
                with self.assertRaises((TypeError, ValueError)):
                    await self.lifecycle.apply(change, initial_run=initial_run)

        malformed_utc = object.__new__(CollectionRunRequest)
        for name, value in (
            ("key", record.collection_key),
            ("due_at", datetime(2026, 9, 25, 12)),
            ("cadence_seconds", 30),
            ("config_revision", 1),
            ("module_epoch", 1),
            ("registry_revision", 1),
        ):
            object.__setattr__(malformed_utc, name, value)
        with self.assertRaises(ValueError):
            await self.lifecycle.apply(change, initial_run=malformed_utc)
        self.assertIsNone(await self._stored_job_schedule(record.collection_key))

        saved = await self._create(record)
        cancelled = self._record(
            saved.subscription_id,
            revision=2,
            owner=saved.owner_id,
            status=SubscriptionStatus.CANCELLED,
        )
        with self.assertRaises(ValueError):
            await self.lifecycle.apply(
                SubscriptionJobChange(
                    SubscriptionJobChangeKind.CANCEL,
                    cancelled,
                    None,
                    1,
                    1,
                ),
                initial_run=self._run(record.collection_key),
            )

    async def test_r03_private_scopes_are_part_of_collection_identity(self) -> None:
        keys = (
            self._key(OwnerScope.user("u1")),
            self._key(OwnerScope.user("u2")),
            self._key(OwnerScope.authorized("u1", GrantReference("g", 1))),
            self._key(OwnerScope.authorized("u1", GrantReference("g", 2))),
        )
        leases = [
            await self.scheduler.claim_due(self._run(key), now=self.now) for key in keys
        ]
        self.assertTrue(all(lease is not None for lease in leases))
        self.assertEqual(len({lease.token for lease in leases}), 4)

    async def test_r04_shared_job_lifecycle_cancel_only_removes_one_link(self) -> None:
        first = self._record("sub-1", owner="u1")
        second = self._record("sub-2", owner="u2")
        await self._create(first)
        await self._create(second)
        self.assertEqual(
            await self.jobs.current_for_subscription(first.subscription_id),
            self._association(first),
        )
        self.assertEqual(
            await self.jobs.current_for_subscription(second.subscription_id),
            self._association(second),
        )
        self.assertIsNone(await self.jobs.current_for_subscription("missing"))
        self.assertEqual(len(await self.jobs.for_collection(first.collection_key)), 2)
        cancelled = self._record(
            "sub-1", revision=2, owner="u1", status=SubscriptionStatus.CANCELLED
        )
        await self.lifecycle.apply(
            SubscriptionJobChange(
                SubscriptionJobChangeKind.CANCEL, cancelled, None, 1, 1
            )
        )
        links = await self.jobs.for_collection(first.collection_key)
        self.assertEqual(tuple(item.subscription_id for item in links), ("sub-2",))
        self.assertIsNone(
            await self.jobs.current_for_subscription(first.subscription_id)
        )

    async def test_r17_job_reads_preserve_saved_link_revision_and_due_scan_filters_stale_link(
        self,
    ) -> None:
        record = await self._create()
        await self.subscriptions.revise(self._record(revision=2), expected_revision=1)

        saved_link = self._association(record)
        self.assertEqual(
            await self.jobs.current_for_subscription(record.subscription_id),
            saved_link,
        )
        self.assertEqual(
            await self.jobs.for_collection(record.collection_key), (saved_link,)
        )

        async with self.db.unit_of_work() as unit:
            unit.execute(
                "UPDATE b04_collection_jobs SET due_at=?",
                ((self.now - timedelta(seconds=1)).isoformat(),),
            )
        self.assertEqual(await self.scheduler.list_due_jobs(now=self.now, limit=10), ())

    async def test_r05_subscription_lifecycle_cas_and_rollback(self) -> None:
        record = await self._create()
        revised = self._record(revision=2)
        await self.lifecycle.apply(
            SubscriptionJobChange(
                SubscriptionJobChangeKind.REVISE,
                revised,
                self._association(revised, assoc_revision=2),
                1,
                1,
            ),
            initial_run=self._run(revised.collection_key),
        )
        stale_record = self._record(revision=2)
        with self.assertRaises(RevisionConflict):
            await self.lifecycle.apply(
                SubscriptionJobChange(
                    SubscriptionJobChangeKind.REVISE,
                    stale_record,
                    self._association(stale_record, assoc_revision=2),
                    1,
                    1,
                ),
                initial_run=self._run(stale_record.collection_key),
            )
        current = await self.subscriptions.current(record.subscription_id)
        self.assertEqual(current.revision, 2)
        async with self.db.unit_of_work(begin_mode="IMMEDIATE") as unit:
            link = unit.execute(
                "SELECT association_revision FROM b04_subscription_jobs WHERE subscription_id='sub-1'"
            ).fetchone()
        self.assertEqual(link[0], 2)
        rejected = self._record("sub-rollback", owner="u2")
        connection = self.db.connect()
        try:
            connection.execute(
                "CREATE TRIGGER reject_link BEFORE INSERT ON b04_subscription_jobs BEGIN SELECT RAISE(ABORT,'injected association failure'); END"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(sqlite3.IntegrityError):
            await self._create(rejected)
        self.assertIsNone(await self.subscriptions.current(rejected.subscription_id))

    async def test_r06_partial_cursor_and_failed_observation_preserves_snapshot(
        self,
    ) -> None:
        record = await self._create()
        self.now += timedelta(seconds=31)
        lease = await self._lease()
        observation = self._observation(
            completeness=ObservationCompleteness.PARTIAL, covered=("id-1",)
        )
        state = EvaluationState(1, {"seen": "id-1"})
        commit = SubscriptionEvaluationCommit(
            record.subscription_id,
            1,
            None,
            state,
            ObservationCursor(
                observation.observation_id,
                1,
                observation.completeness,
                observation.covered_ids,
            ),
        )
        self.assertTrue(
            await self.scheduler.commit_observation_with_evaluations(
                lease, ObservationEvaluationCommit(observation, (commit,))
            )
        )
        snapshot = await self.scheduler.current_evaluation(
            record.subscription_id, record.collection_key
        )
        self.assertEqual(snapshot.state.revision, 1)
        self.assertEqual(snapshot.cursor.covered_ids, ("id-1",))
        self.assertEqual(snapshot.observation.observation_id, "obs-1")
        self.assertIsNone(
            await self.scheduler.current_evaluation(
                record.subscription_id,
                self._key(OwnerScope.user("u1")),
            )
        )
        async with self.db.unit_of_work() as unit:
            before = unit.execute(
                "SELECT observation_id FROM b04_collection_jobs"
            ).fetchone()[0]
        self.now += timedelta(seconds=31)
        lease = await self._lease()
        failed = self._observation(
            identity="obs-failed", completeness=ObservationCompleteness.FAILED
        )
        self.assertTrue(await self.scheduler.commit_observation(lease, failed))
        async with self.db.unit_of_work() as unit:
            after = unit.execute(
                "SELECT observation_id FROM b04_collection_jobs"
            ).fetchone()[0]
            cursor = unit.execute(
                "SELECT cursor_json FROM b04_evaluation_states WHERE subscription_id='sub-1'"
            ).fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual(json_load(cursor).observation_id, "obs-1")
        self.assertEqual(
            (
                await self.scheduler.current_evaluation(
                    record.subscription_id, record.collection_key
                )
            ).observation.observation_id,
            "obs-1",
        )

    async def test_r07_observation_state_event_digest_commit_rolls_back_together(
        self,
    ) -> None:
        record = await self._create()
        window = self._window()
        await self.windows.create(window)
        lease = await self._lease()
        observation = self._observation()
        event = self._event(record)
        member = DigestMember(
            record.subscription_id, 1, event.event_key, event.event_version
        )
        association = DigestMemberAssociation(
            window.window_id, record.recipient, member, event
        )
        item = SubscriptionEvaluationCommit(
            record.subscription_id,
            1,
            None,
            EvaluationState(1, {"n": 1}),
            ObservationCursor(
                observation.observation_id, 1, observation.completeness, ()
            ),
            (event,),
            (association,),
        )
        connection = self.db.connect()
        try:
            connection.execute(
                "CREATE TRIGGER fail_digest BEFORE INSERT ON b04_digest_members BEGIN SELECT RAISE(ABORT,'forced'); END"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(sqlite3.IntegrityError):
            await self.scheduler.commit_observation_with_evaluations(
                lease, ObservationEvaluationCommit(observation, (item,))
            )
        connection = self.db.connect()
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM b04_observations").fetchone()[
                    0
                ],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM b04_delivery_events"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM b04_evaluation_states"
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

    async def test_r07_expired_lease_cannot_commit_after_write_lock(self) -> None:
        lease = await self._lease()
        current = self.now
        expires_at = current - timedelta(seconds=1)
        collected_at = current - timedelta(seconds=2)
        connection = self.db.connect()
        try:
            connection.execute(
                "UPDATE b04_collection_jobs SET lease_expires_at=? WHERE lease_token=?",
                (expires_at.isoformat(), lease.token),
            )
            connection.commit()
        finally:
            connection.close()
        observation = Observation(
            "obs-expired-lease",
            lease.key,
            1,
            collected_at,
            collected_at,
            ObservationCompleteness.COMPLETE,
            (),
            {"snapshot": [1]},
        )
        next_due_at = current + timedelta(minutes=10)
        async with self.db.unit_of_work() as unit:
            before_due = unit.execute(
                "SELECT due_at FROM b04_collection_jobs WHERE lease_token=?",
                (lease.token,),
            ).fetchone()
        self.assertFalse(
            await self.scheduler.commit_observation(
                lease, observation, next_due_at=next_due_at
            )
        )
        async with self.db.unit_of_work() as unit:
            count = unit.execute("SELECT COUNT(*) FROM b04_observations").fetchone()[0]
            due_at = unit.execute(
                "SELECT due_at FROM b04_collection_jobs WHERE lease_token=?",
                (lease.token,),
            ).fetchone()
        self.assertEqual(count, 0)
        self.assertEqual(due_at, before_due)

    async def test_r07_commit_uses_injected_clock_and_rechecks_after_lock_wait(
        self,
    ) -> None:
        started_at = datetime(2026, 1, 15, 8, tzinfo=UTC)
        current = [started_at]
        self.now = started_at
        self.scheduler = SQLiteSchedulerRepository(
            self.db,
            subscription_gate_bindings=self.bindings,
            utc_clock=lambda: current[0],
        )

        await self._create()
        live_lease = await self._lease()
        self.now = started_at + timedelta(seconds=1)
        current[0] = self.now
        self.assertTrue(
            await self.scheduler.commit_observation(
                live_lease, self._observation(identity="obs-injected-clock")
            )
        )

        delayed_key = self._key(OwnerScope.user("u2"))
        self.now = started_at + timedelta(minutes=1)
        current[0] = self.now
        delayed_record = self._record("sub-delayed-commit", owner="u2", key=delayed_key)
        await self._create(delayed_record)
        delayed_lease = await self._lease(delayed_key)
        delayed_observation = self._observation(
            delayed_key, identity="obs-expired-after-lock-wait"
        )

        connection = self.db.connect()
        commit_task = None
        try:
            connection.execute("BEGIN IMMEDIATE")
            commit_task = asyncio.create_task(
                self.scheduler.commit_observation(delayed_lease, delayed_observation)
            )
            await asyncio.sleep(0.05)
            self.assertFalse(commit_task.done())
            current[0] = delayed_lease.expires_at
            connection.rollback()
        finally:
            connection.close()
        self.assertIsNotNone(commit_task)
        self.assertFalse(await commit_task)
        async with self.db.unit_of_work() as unit:
            observations = unit.execute(
                "SELECT observation_id FROM b04_observations ORDER BY observation_id"
            ).fetchall()
        self.assertEqual([row[0] for row in observations], ["obs-injected-clock"])

    async def test_r18_explicit_next_due_is_atomic_outcome_specific_and_survives_restart(
        self,
    ) -> None:
        await self._create()
        started_at = datetime.now(UTC)
        request = CollectionRunRequest(
            self._key(), started_at - timedelta(seconds=1), 30, 1, 1, 1
        )
        lease = await self.scheduler.claim_due(request, now=started_at)
        self.assertIsNotNone(lease)
        success_due = started_at + timedelta(minutes=7)
        success_observation = self._observation(identity="obs-explicit-success")
        success_observation = Observation(
            success_observation.observation_id,
            success_observation.key,
            success_observation.data_version,
            started_at,
            started_at,
            success_observation.completeness,
            success_observation.covered_ids,
            success_observation.payload,
        )
        success_commit = ObservationEvaluationCommit(success_observation, ())
        with self.assertRaisesRegex(ValueError, "UTC instant"):
            await self.scheduler.commit_observation_with_evaluations(
                lease,
                success_commit,
                next_due_at=datetime.now(),
            )
        with self.assertRaisesRegex(ValueError, "later than collection time"):
            await self.scheduler.commit_observation_with_evaluations(
                lease,
                success_commit,
                next_due_at=started_at,
            )
        self.assertTrue(
            await self.scheduler.commit_observation_with_evaluations(
                lease,
                success_commit,
                next_due_at=success_due,
            )
        )

        reopened_scheduler = SQLiteSchedulerRepository(
            SQLiteDatabase(self.path), subscription_gate_bindings=self.bindings
        )
        due = await reopened_scheduler.list_due_jobs(now=success_due, limit=10)
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0].due_at, success_due)

        failed_started = success_due
        failed_request = CollectionRunRequest(
            self._key(), failed_started - timedelta(seconds=1), 30, 1, 1, 1
        )
        failed_lease = await reopened_scheduler.claim_due(
            failed_request, now=failed_started
        )
        self.assertIsNotNone(failed_lease)
        failed_due = failed_started + timedelta(minutes=19)
        failed = Observation(
            "obs-explicit-failed",
            failed_lease.key,
            2,
            failed_started + timedelta(seconds=1),
            failed_started + timedelta(seconds=1),
            ObservationCompleteness.FAILED,
            (),
            {"error": "temporary"},
        )
        self.assertTrue(
            await reopened_scheduler.commit_observation(
                failed_lease, failed, next_due_at=failed_due
            )
        )
        due_after_failure = await reopened_scheduler.list_due_jobs(
            now=failed_due, limit=10
        )
        self.assertEqual(len(due_after_failure), 1)
        self.assertEqual(due_after_failure[0].due_at, failed_due)

    async def test_r08_delivery_idempotency_and_attempt_ledger(self) -> None:
        record = await self._create()
        event = self._event(record)
        self.assertEqual(
            await create_subscription_event_fixture(
                self.delivery, self.bindings, event
            ),
            event,
        )
        self.assertEqual(
            await create_subscription_event_fixture(
                self.delivery, self.bindings, event
            ),
            event,
        )
        sending = await self.delivery.claim_sending(
            event.event_key,
            event.event_version,
            subscription_id=event.subscription_id,
            subscription_revision=event.subscription_revision,
            expected_state=DeliveryState.PENDING,
            attempt_number=1,
            started_at=self.now,
            now=self.now,
        )
        self.assertEqual(sending.state, DeliveryState.SENDING)
        failed_attempt = DeliveryAttempt(
            1,
            DeliveryState.FAILED,
            event.idempotency_key,
            self.now,
            self.now + timedelta(seconds=1),
            error_code="transient",
        )
        failed = await self.delivery.record_attempt(
            event.event_key,
            event.event_version,
            failed_attempt,
            subscription_id=event.subscription_id,
            subscription_revision=event.subscription_revision,
            expected_state=DeliveryState.SENDING,
            retry_at=self.now + timedelta(seconds=2),
        )
        self.assertEqual(failed.state, DeliveryState.FAILED)
        reopened = SQLiteDeliveryRepository(
            SQLiteDatabase(self.path), subscription_gate_bindings=self.bindings
        )
        sending_again = await reopened.claim_sending(
            event.event_key,
            event.event_version,
            subscription_id=event.subscription_id,
            subscription_revision=event.subscription_revision,
            expected_state=DeliveryState.FAILED,
            attempt_number=2,
            started_at=self.now + timedelta(seconds=2),
            now=self.now + timedelta(seconds=2),
        )
        self.assertEqual(sending_again.attempt.attempt_number, 2)
        sent_attempt = DeliveryAttempt(
            2,
            DeliveryState.SENT,
            event.idempotency_key,
            self.now + timedelta(seconds=2),
            self.now + timedelta(seconds=3),
            platform_message_id="msg-2",
        )
        sent = await reopened.record_attempt(
            event.event_key,
            event.event_version,
            sent_attempt,
            subscription_id=event.subscription_id,
            subscription_revision=event.subscription_revision,
            expected_state=DeliveryState.SENDING,
        )
        self.assertEqual(sent.state, DeliveryState.SENT)
        self.assertEqual(
            (
                await reopened.current_event(
                    event.event_key,
                    1,
                    subscription_id=event.subscription_id,
                    subscription_revision=event.subscription_revision,
                )
            ).attempt.attempt_number,
            2,
        )
        async with self.db.unit_of_work() as unit:
            attempts = unit.execute(
                "SELECT attempt_number,state,attempt_json FROM b04_delivery_attempts WHERE event_key=? ORDER BY attempt_number",
                (event.event_key,),
            ).fetchall()
        self.assertEqual(
            tuple((item[0], item[1]) for item in attempts), ((1, "failed"), (2, "sent"))
        )
        self.assertEqual(json_load(attempts[0][2]).error_code, "transient")

    async def test_r08_due_delivery_retry_is_durable_and_cursor_pages(self) -> None:
        record = await self._create()
        events = tuple(
            [
                await create_subscription_event_fixture(
                    self.delivery,
                    self.bindings,
                    self._event(record, event_key=f"paged-{letter}"),
                )
                for letter in ("a", "b", "c")
            ]
        )
        first = await self.delivery.list_due_events(now=self.now, limit=1)
        second = await self.delivery.list_due_events(
            now=self.now, limit=1, after_cursor=first[0].cursor
        )
        self.assertEqual(
            tuple(item.event_key for item in first + second), ("paged-a", "paged-b")
        )

        await self.delivery.claim_sending(
            events[0].event_key,
            events[0].event_version,
            subscription_id=events[0].subscription_id,
            subscription_revision=events[0].subscription_revision,
            expected_state=DeliveryState.PENDING,
            attempt_number=1,
            started_at=self.now,
            now=self.now,
        )
        failed_attempt = DeliveryAttempt(
            1,
            DeliveryState.FAILED,
            events[0].idempotency_key,
            self.now,
            self.now + timedelta(seconds=1),
            error_code="temporary",
        )
        retry_at = self.now + timedelta(seconds=10)
        await self.delivery.record_attempt(
            events[0].event_key,
            events[0].event_version,
            failed_attempt,
            subscription_id=events[0].subscription_id,
            subscription_revision=events[0].subscription_revision,
            expected_state=DeliveryState.SENDING,
            retry_at=retry_at,
        )
        reopened = SQLiteDeliveryRepository(
            SQLiteDatabase(self.path), subscription_gate_bindings=self.bindings
        )
        self.assertEqual(
            (
                await reopened.current_event(
                    events[0].event_key,
                    events[0].event_version,
                    subscription_id=events[0].subscription_id,
                    subscription_revision=events[0].subscription_revision,
                )
            ).retry_at,
            retry_at,
        )
        self.assertNotIn(
            "paged-a",
            tuple(
                item.event_key
                for item in await reopened.list_due_events(now=self.now, limit=5)
            ),
        )
        self.assertIn(
            "paged-a",
            tuple(
                item.event_key
                for item in await reopened.list_due_events(now=retry_at, limit=5)
            ),
        )
        self.assertIsNone(
            await reopened.claim_sending(
                events[0].event_key,
                events[0].event_version,
                subscription_id=events[0].subscription_id,
                subscription_revision=events[0].subscription_revision,
                expected_state=DeliveryState.FAILED,
                attempt_number=2,
                started_at=retry_at - timedelta(seconds=1),
                now=retry_at - timedelta(seconds=1),
            )
        )
        retry = await reopened.claim_sending(
            events[0].event_key,
            events[0].event_version,
            subscription_id=events[0].subscription_id,
            subscription_revision=events[0].subscription_revision,
            expected_state=DeliveryState.FAILED,
            attempt_number=2,
            started_at=retry_at,
            now=retry_at,
        )
        self.assertEqual(retry.attempt.attempt_number, 2)

    async def test_r08_due_event_pages_filter_digest_before_limit_and_keep_stale(self):
        digest_records = tuple(
            [
                await self._create(
                    self._record(f"sub-digest-{index:02d}", notification_mode="digest")
                )
                for index in range(8)
            ]
        )
        cancelled = await self._create(
            self._record("sub-cancelled", notification_mode="digest")
        )
        stale = await self._create(
            self._record("sub-stale", notification_mode="instant")
        )
        instant = await self._create(
            self._record("sub-instant-z", notification_mode="instant")
        )
        await self.subscriptions.cancel(
            cancelled.subscription_id, expected_revision=cancelled.revision
        )
        await self.subscriptions.revise(
            replace(stale, revision=stale.revision + 1, notification_mode="digest"),
            expected_revision=stale.revision,
        )

        digest_events = tuple(
            (record, f"a-digest-{index:02d}")
            for index, record in enumerate(digest_records)
        )
        for record, event_key in (
            *digest_events,
            (cancelled, "c-cancelled"),
            (stale, "d-stale-revision"),
            (instant, "z-instant"),
        ):
            await create_subscription_event_fixture(
                self.delivery, self.bindings, self._event(record, event_key=event_key)
            )

        keys = []
        cursor = None
        while True:
            page = await self.delivery.list_due_events(
                now=self.now, limit=1, after_cursor=cursor
            )
            if not page:
                break
            self.assertEqual(len(page), 1)
            keys.append(page[0].event_key)
            cursor = page[0].cursor

        # Exact active digest rows are filtered before LIMIT; stale and
        # cancelled rows still reach dispatch for their existing cleanup.
        self.assertEqual(keys, ["c-cancelled", "d-stale-revision", "z-instant"])

    async def test_r08_claim_sending_requires_exact_active_instant_subscription(self):
        digest = await self._create(
            self._record("sub-claim-digest", notification_mode="digest")
        )
        event = await create_subscription_event_fixture(
            self.delivery, self.bindings, self._event(digest, event_key="claim-digest")
        )
        failed = DeliveryAttempt(
            1,
            DeliveryState.FAILED,
            event.idempotency_key,
            self.now,
            self.now,
            error_code="temporary",
        )
        await self.delivery.record_attempt(
            event.event_key,
            event.event_version,
            failed,
            subscription_id=event.subscription_id,
            subscription_revision=event.subscription_revision,
            expected_state=DeliveryState.PENDING,
            retry_at=self.now,
        )
        self.assertEqual(
            await self.delivery.list_due_events(now=self.now, limit=10), ()
        )
        self.assertIsNone(
            await self.delivery.claim_sending(
                event.event_key,
                event.event_version,
                subscription_id=event.subscription_id,
                subscription_revision=event.subscription_revision,
                expected_state=DeliveryState.FAILED,
                attempt_number=2,
                started_at=self.now,
                now=self.now,
            )
        )

        revised = replace(
            digest, revision=digest.revision + 1, notification_mode="instant"
        )
        await self.subscriptions.revise(revised, expected_revision=digest.revision)
        self.assertIsNone(
            await self.delivery.claim_sending(
                event.event_key,
                event.event_version,
                subscription_id=event.subscription_id,
                subscription_revision=event.subscription_revision,
                expected_state=DeliveryState.FAILED,
                attempt_number=2,
                started_at=self.now,
                now=self.now,
            )
        )
        current = await self.delivery.current_event(
            event.event_key,
            event.event_version,
            subscription_id=event.subscription_id,
            subscription_revision=event.subscription_revision,
        )
        self.assertEqual(current.state, DeliveryState.FAILED)
        self.assertEqual(current.attempt.attempt_number, 1)

        current_event = await create_subscription_event_fixture(
            self.delivery, self.bindings, self._event(revised, event_key="claim-digest")
        )
        claimed = await self.delivery.claim_sending(
            current_event.event_key,
            current_event.event_version,
            subscription_id=current_event.subscription_id,
            subscription_revision=current_event.subscription_revision,
            expected_state=DeliveryState.PENDING,
            attempt_number=1,
            started_at=self.now,
            now=self.now,
        )
        self.assertEqual(claimed.subscription_revision, revised.revision)

    async def test_immutable_digest_history_fences_event_cleanup_and_claims(self):
        state_cases = (
            ("sending", DeliveryState.SENDING, False),
            ("unknown", DeliveryState.UNKNOWN, True),
            ("sent", DeliveryState.SENT, True),
        )
        for suffix, envelope_state, failed_event in state_cases:
            with self.subTest(envelope_state=envelope_state):
                (
                    record,
                    window,
                    event,
                    member,
                    claim,
                ) = await self._immutable_digest_fixture(
                    suffix, failed_event=failed_event
                )
                receipt = DigestMemberReceipt(member, DigestMemberDisposition.INCLUDED)
                reconciled = await self.windows.reconcile_envelope_members(
                    claim,
                    (receipt,),
                    expected_revision=claim.envelope.revision,
                )
                sending = await self.windows.begin_envelope_send(
                    claim,
                    expected_revision=reconciled.revision,
                    attempt_number=1,
                    started_at=self.now,
                )
                self.assertIsNotNone(sending)
                envelope = sending
                if envelope_state is not DeliveryState.SENDING:
                    attempt = DeliveryAttempt(
                        1,
                        envelope_state,
                        digest_envelope_idempotency_key(
                            window.window_id, record.recipient
                        ),
                        self.now,
                        self.now + timedelta(seconds=1),
                        platform_message_id=(
                            "platform-digest-1"
                            if envelope_state is DeliveryState.SENT
                            else None
                        ),
                    )
                    envelope = await self.windows.complete_envelope_send(
                        claim, attempt, expected_revision=sending.revision
                    )
                    self.assertIsNotNone(envelope)
                self.assertEqual(envelope.state.value, envelope_state.value)

                before = await self._immutable_event_snapshot(event, envelope)
                due = await self.delivery.list_due_events(now=self.now, limit=20)
                self.assertNotIn(event.event_key, {item.event_key for item in due})
                self.assertIsNone(
                    await self.delivery.claim_sending(
                        event.event_key,
                        event.event_version,
                        subscription_id=event.subscription_id,
                        subscription_revision=event.subscription_revision,
                        expected_state=event.state,
                        attempt_number=(
                            event.attempt.attempt_number if event.attempt else 0
                        )
                        + 1,
                        started_at=self.now,
                        now=self.now,
                    )
                )
                cancellation = DeliveryAttempt(
                    (event.attempt.attempt_number if event.attempt else 0) + 1,
                    DeliveryState.CANCELLED,
                    event.idempotency_key,
                    self.now,
                    self.now,
                    error_code="subscription_cancelled",
                )
                with self.assertRaises(RevisionConflict):
                    await self.delivery.record_attempt(
                        event.event_key,
                        event.event_version,
                        cancellation,
                        subscription_id=event.subscription_id,
                        subscription_revision=event.subscription_revision,
                        expected_state=event.state,
                    )
                after = await self._immutable_event_snapshot(event, envelope)
                self.assertEqual(after, before)

        (
            ready_record,
            ready_window,
            ready_event,
            _member,
            ready_claim,
        ) = await self._immutable_digest_fixture("ready")
        recovered = await self.windows.recover_expired_envelope_claims(
            before=ready_claim.expires_at, recovered_at=ready_claim.expires_at
        )
        self.assertGreaterEqual(len(recovered), 1)
        ready_envelope = await self.windows.current_envelope(
            ready_window.window_id, ready_record.recipient
        )
        self.assertEqual(ready_envelope.state, DigestEnvelopeState.READY)
        await self.subscriptions.cancel(
            ready_record.subscription_id, expected_revision=ready_record.revision
        )

        stale_key = CollectionKey(
            "sample/game",
            "collector",
            1,
            "source",
            NormalizedInput({"immutable-history": "unrelated-stale"}),
            OwnerScope.public(),
        )
        stale_record = await self._create(
            self._record("sub-unrelated-stale", key=stale_key)
        )
        stale_event = await create_subscription_event_fixture(
            self.delivery,
            self.bindings,
            self._event(stale_record, event_key="event-unrelated-stale"),
        )
        await self.subscriptions.cancel(
            stale_record.subscription_id, expected_revision=stale_record.revision
        )

        due = await self.delivery.list_due_events(
            now=self.now + timedelta(minutes=2), limit=20
        )
        self.assertEqual(
            {item.event_key for item in due},
            {ready_event.event_key, stale_event.event_key},
        )
        first_due_page = await self.delivery.list_due_events(
            now=self.now + timedelta(minutes=2), limit=1
        )
        self.assertEqual(
            tuple(item.event_key for item in first_due_page), (ready_event.event_key,)
        )
        cancelled = DeliveryAttempt(
            1,
            DeliveryState.CANCELLED,
            ready_event.idempotency_key,
            self.now,
            self.now,
            error_code="subscription_cancelled",
        )
        saved_ready = await self.delivery.record_attempt(
            ready_event.event_key,
            ready_event.event_version,
            cancelled,
            subscription_id=ready_event.subscription_id,
            subscription_revision=ready_event.subscription_revision,
            expected_state=DeliveryState.PENDING,
        )
        self.assertEqual(saved_ready.state, DeliveryState.CANCELLED)

    async def _immutable_event_snapshot(self, event, envelope):
        def _read(unit):
            event_row = unit.execute(
                "SELECT event_json FROM b04_delivery_events WHERE event_key=? "
                "AND event_version=? AND subscription_id=? AND subscription_revision=?",
                (
                    event.event_key,
                    event.event_version,
                    event.subscription_id,
                    event.subscription_revision,
                ),
            ).fetchone()
            attempts = unit.execute(
                "SELECT attempt_json FROM b04_delivery_attempts WHERE event_key=? "
                "AND event_version=? AND subscription_id=? AND subscription_revision=? "
                "ORDER BY attempt_number",
                (
                    event.event_key,
                    event.event_version,
                    event.subscription_id,
                    event.subscription_revision,
                ),
            ).fetchall()
            envelope_row = unit.execute(
                "SELECT envelope_json FROM b04_digest_envelopes WHERE envelope_id=?",
                (envelope.envelope_id,),
            ).fetchone()
            receipts = unit.execute(
                "SELECT receipt_json FROM b04_digest_member_receipts "
                "WHERE envelope_id=? ORDER BY subscription_id,event_key,event_version",
                (envelope.envelope_id,),
            ).fetchall()
            return (
                event_row[0],
                tuple(row[0] for row in attempts),
                envelope_row[0],
                tuple(row[0] for row in receipts),
            )

        return await self.db.executor.run_read(_read)

    async def test_immutable_digest_history_keeps_inflight_event_completion_valid(
        self,
    ):
        for suffix, result_state in (
            ("instant-sent", DeliveryState.SENT),
            ("instant-unknown", DeliveryState.UNKNOWN),
        ):
            with self.subTest(result_state=result_state):
                (
                    record,
                    window,
                    event,
                    member,
                    claim,
                ) = await self._immutable_digest_fixture(suffix)
                event_sending = await self.delivery.claim_sending(
                    event.event_key,
                    event.event_version,
                    subscription_id=event.subscription_id,
                    subscription_revision=event.subscription_revision,
                    expected_state=DeliveryState.PENDING,
                    attempt_number=1,
                    started_at=self.now,
                    now=self.now,
                )
                self.assertIsNotNone(event_sending)
                receipt = DigestMemberReceipt(member, DigestMemberDisposition.INCLUDED)
                reconciled = await self.windows.reconcile_envelope_members(
                    claim,
                    (receipt,),
                    expected_revision=claim.envelope.revision,
                )
                envelope_sending = await self.windows.begin_envelope_send(
                    claim,
                    expected_revision=reconciled.revision,
                    attempt_number=1,
                    started_at=self.now,
                )
                digest_attempt = DeliveryAttempt(
                    1,
                    DeliveryState.SENT,
                    digest_envelope_idempotency_key(window.window_id, record.recipient),
                    self.now,
                    self.now + timedelta(seconds=1),
                    platform_message_id="digest-receipt",
                )
                await self.windows.complete_envelope_send(
                    claim,
                    digest_attempt,
                    expected_revision=envelope_sending.revision,
                )
                terminal_cleanup = DeliveryAttempt(
                    1,
                    DeliveryState.CANCELLED,
                    event.idempotency_key,
                    event_sending.attempt.started_at,
                    self.now + timedelta(seconds=1),
                    error_code="cancelled_before_dispatch",
                )
                with self.assertRaises(RevisionConflict):
                    await self.delivery.record_attempt(
                        event.event_key,
                        event.event_version,
                        terminal_cleanup,
                        subscription_id=event.subscription_id,
                        subscription_revision=event.subscription_revision,
                        expected_state=DeliveryState.SENDING,
                    )
                event_attempt = DeliveryAttempt(
                    1,
                    result_state,
                    event.idempotency_key,
                    self.now,
                    self.now + timedelta(seconds=1),
                    platform_message_id=(
                        "instant-receipt"
                        if result_state is DeliveryState.SENT
                        else None
                    ),
                )
                finished = await self.delivery.record_attempt(
                    event.event_key,
                    event.event_version,
                    event_attempt,
                    subscription_id=event.subscription_id,
                    subscription_revision=event.subscription_revision,
                    expected_state=DeliveryState.SENDING,
                )
                self.assertEqual(finished.state, result_state)

    async def test_r08_shared_event_key_has_independent_subscription_attempts(
        self,
    ) -> None:
        records = (self._record("sub-a", owner="u1"), self._record("sub-b", owner="u1"))
        events = []
        for record in records:
            await self._create(record)
            event = self._event(record, event_key="shared-event")
            events.append(
                await create_subscription_event_fixture(
                    self.delivery, self.bindings, event
                )
            )
        reopened = SQLiteDeliveryRepository(
            SQLiteDatabase(self.path), subscription_gate_bindings=self.bindings
        )
        for index, event in enumerate(events, start=1):
            self.assertEqual(
                await reopened.current_event(
                    "shared-event",
                    1,
                    subscription_id=event.subscription_id,
                    subscription_revision=event.subscription_revision,
                ),
                event,
            )
            self.assertIsNone(
                await reopened.current_event(
                    "shared-event",
                    1,
                    subscription_id=event.subscription_id,
                    subscription_revision=event.subscription_revision + 1,
                )
            )
            sending = await reopened.claim_sending(
                "shared-event",
                1,
                subscription_id=event.subscription_id,
                subscription_revision=event.subscription_revision,
                expected_state=DeliveryState.PENDING,
                attempt_number=1,
                started_at=self.now + timedelta(seconds=index),
                now=self.now + timedelta(seconds=index),
            )
            self.assertEqual(sending.subscription_id, event.subscription_id)
            failed = DeliveryAttempt(
                1,
                DeliveryState.FAILED,
                event.idempotency_key,
                self.now + timedelta(seconds=index),
                self.now + timedelta(seconds=index + 1),
                error_code=f"failure-{event.subscription_id}",
            )
            await reopened.record_attempt(
                "shared-event",
                1,
                failed,
                subscription_id=event.subscription_id,
                subscription_revision=event.subscription_revision,
                expected_state=DeliveryState.SENDING,
                retry_at=self.now + timedelta(seconds=index + 2),
            )
            retry = await reopened.claim_sending(
                "shared-event",
                1,
                subscription_id=event.subscription_id,
                subscription_revision=event.subscription_revision,
                expected_state=DeliveryState.FAILED,
                attempt_number=2,
                started_at=self.now + timedelta(seconds=index + 2),
                now=self.now + timedelta(seconds=index + 2),
            )
            self.assertEqual(retry.subscription_id, event.subscription_id)
            sent = DeliveryAttempt(
                2,
                DeliveryState.SENT,
                event.idempotency_key,
                self.now + timedelta(seconds=index + 2),
                self.now + timedelta(seconds=index + 3),
                platform_message_id=f"message-{event.subscription_id}",
            )
            await reopened.record_attempt(
                "shared-event",
                1,
                sent,
                subscription_id=event.subscription_id,
                subscription_revision=event.subscription_revision,
                expected_state=DeliveryState.SENDING,
            )
        for event in events:
            current = await reopened.current_event(
                "shared-event",
                1,
                subscription_id=event.subscription_id,
                subscription_revision=event.subscription_revision,
            )
            self.assertEqual(current.state, DeliveryState.SENT)
            self.assertEqual(current.attempt.attempt_number, 2)
        async with self.db.unit_of_work() as unit:
            rows = unit.execute(
                "SELECT subscription_id,attempt_number,state FROM b04_delivery_attempts WHERE event_key='shared-event' ORDER BY subscription_id,attempt_number"
            ).fetchall()
        self.assertEqual(
            tuple(tuple(row) for row in rows),
            (
                ("sub-a", 1, "failed"),
                ("sub-a", 2, "sent"),
                ("sub-b", 1, "failed"),
                ("sub-b", 2, "sent"),
            ),
        )

    async def test_r08_revised_subscription_can_store_same_event_version(self) -> None:
        original = await self._create(self._record("sub-revision"))
        old_event = await create_subscription_event_fixture(
            self.delivery,
            self.bindings,
            self._event(original, event_key="stable-event"),
        )
        revised = self._record("sub-revision", revision=2)
        await self.lifecycle.apply(
            SubscriptionJobChange(
                SubscriptionJobChangeKind.REVISE,
                revised,
                self._association(revised, assoc_revision=2),
                1,
                1,
            ),
            initial_run=self._run(revised.collection_key),
        )
        new_event = await create_subscription_event_fixture(
            self.delivery, self.bindings, self._event(revised, event_key="stable-event")
        )
        self.assertEqual(
            (old_event.event_key, old_event.event_version),
            (new_event.event_key, new_event.event_version),
        )
        self.assertNotEqual(old_event.idempotency_key, new_event.idempotency_key)
        self.assertEqual(
            await self.delivery.current_event(
                "stable-event",
                1,
                subscription_id="sub-revision",
                subscription_revision=1,
            ),
            old_event,
        )
        self.assertEqual(
            await self.delivery.current_event(
                "stable-event",
                1,
                subscription_id="sub-revision",
                subscription_revision=2,
            ),
            new_event,
        )
        claimed = await self.delivery.claim_sending(
            "stable-event",
            1,
            subscription_id="sub-revision",
            subscription_revision=2,
            expected_state=DeliveryState.PENDING,
            attempt_number=1,
            started_at=self.now,
            now=self.now,
        )
        self.assertEqual(claimed.subscription_revision, 2)

    async def test_r08_completion_must_match_claimed_start_time(self) -> None:
        record = await self._create()
        event = await create_subscription_event_fixture(
            self.delivery, self.bindings, self._event(record)
        )
        await self.delivery.claim_sending(
            event.event_key,
            event.event_version,
            subscription_id=event.subscription_id,
            subscription_revision=event.subscription_revision,
            expected_state=DeliveryState.PENDING,
            attempt_number=1,
            started_at=self.now,
            now=self.now,
        )
        mismatched = DeliveryAttempt(
            1,
            DeliveryState.SENT,
            event.idempotency_key,
            self.now + timedelta(seconds=1),
            self.now + timedelta(seconds=2),
        )
        with self.assertRaises(RevisionConflict):
            await self.delivery.record_attempt(
                event.event_key,
                event.event_version,
                mismatched,
                subscription_id=event.subscription_id,
                subscription_revision=event.subscription_revision,
                expected_state=DeliveryState.SENDING,
            )
        current = await self.delivery.current_event(
            event.event_key,
            event.event_version,
            subscription_id=event.subscription_id,
            subscription_revision=event.subscription_revision,
        )
        self.assertEqual(current.state, DeliveryState.SENDING)
        self.assertEqual(current.attempt.started_at, self.now)
        async with self.db.unit_of_work() as unit:
            row = unit.execute(
                "SELECT state,attempt_json FROM b04_delivery_attempts WHERE event_key=? AND subscription_id=? AND subscription_revision=? AND attempt_number=1",
                (event.event_key, event.subscription_id, event.subscription_revision),
            ).fetchone()
        self.assertEqual(row[0], "sending")
        self.assertEqual(json_load(row[1]).started_at, self.now)
        accepted = DeliveryAttempt(
            1,
            DeliveryState.SENT,
            event.idempotency_key,
            self.now,
            self.now + timedelta(seconds=2),
        )
        result = await self.delivery.record_attempt(
            event.event_key,
            event.event_version,
            accepted,
            subscription_id=event.subscription_id,
            subscription_revision=event.subscription_revision,
            expected_state=DeliveryState.SENDING,
        )
        self.assertEqual(result.state, DeliveryState.SENT)

    async def test_r09_restart_recovers_sending_as_unknown_without_new_attempt(
        self,
    ) -> None:
        record = await self._create()
        event = await create_subscription_event_fixture(
            self.delivery, self.bindings, self._event(record)
        )
        await self.delivery.claim_sending(
            event.event_key,
            1,
            subscription_id=event.subscription_id,
            subscription_revision=event.subscription_revision,
            expected_state=DeliveryState.PENDING,
            attempt_number=1,
            started_at=self.now,
            now=self.now,
        )
        reopened = SQLiteDeliveryRepository(
            SQLiteDatabase(self.path), subscription_gate_bindings=self.bindings
        )
        recovered = await reopened.recover_stale_sending(
            before=self.now + timedelta(minutes=1)
        )
        self.assertEqual(recovered[0].state, DeliveryState.UNKNOWN)
        self.assertEqual(recovered[0].attempt.attempt_number, 1)
        self.assertIsNone(
            await reopened.claim_sending(
                event.event_key,
                1,
                subscription_id=event.subscription_id,
                subscription_revision=event.subscription_revision,
                expected_state=DeliveryState.UNKNOWN,
                attempt_number=2,
                started_at=self.now + timedelta(minutes=2),
                now=self.now + timedelta(minutes=2),
            )
        )

    async def test_r09_unknown_transition_uses_exact_subscription_selector(
        self,
    ) -> None:
        record = await self._create()
        event = await create_subscription_event_fixture(
            self.delivery, self.bindings, self._event(record)
        )
        await self.delivery.claim_sending(
            event.event_key,
            event.event_version,
            subscription_id=event.subscription_id,
            subscription_revision=event.subscription_revision,
            expected_state=DeliveryState.PENDING,
            attempt_number=1,
            started_at=self.now,
            now=self.now,
        )
        kwargs = {
            "expected_attempt_number": 1,
            "expected_started_at": self.now,
            "completed_at": self.now + timedelta(minutes=1),
        }
        self.assertIsNone(
            await self.delivery.mark_sending_unknown(
                event.event_key,
                event.event_version,
                subscription_id=event.subscription_id,
                subscription_revision=event.subscription_revision + 1,
                **kwargs,
            )
        )
        unknown = await self.delivery.mark_sending_unknown(
            event.event_key,
            event.event_version,
            subscription_id=event.subscription_id,
            subscription_revision=event.subscription_revision,
            **kwargs,
        )
        self.assertEqual(unknown.state, DeliveryState.UNKNOWN)
        reopened = SQLiteDeliveryRepository(
            SQLiteDatabase(self.path), subscription_gate_bindings=self.bindings
        )
        recovered = await reopened.current_event(
            event.event_key,
            event.event_version,
            subscription_id=event.subscription_id,
            subscription_revision=event.subscription_revision,
        )
        self.assertEqual(recovered.attempt.state, DeliveryState.UNKNOWN)

    async def test_r10_owner_and_grant_scope_are_persisted_without_filter_leaks(
        self,
    ) -> None:
        private_key = self._key(
            OwnerScope.authorized("u1", GrantReference("grant-private", 4))
        )
        record = self._record(key=private_key)
        await self._create(record)
        connection = self.db.connect()
        try:
            row = connection.execute(
                "SELECT owner_id,scope_kind,scope_user,grant_id,grant_revision,record_json FROM b04_subscriptions WHERE subscription_id='sub-1'"
            ).fetchone()
            self.assertEqual(
                tuple(row[:5]), ("u1", "authorized", "u1", "grant-private", 4)
            )
            self.assertIn("do-not-log", row["record_json"])
        finally:
            connection.close()
        self.assertIsNone(await self.subscriptions.current_for_owner("sub-1", "u2"))
        self.assertEqual(
            (await self.subscriptions.current_for_owner("sub-1", "u1")).owner_id, "u1"
        )

    async def test_r11_digest_window_and_member_association_are_unique(self) -> None:
        record = await self._create()
        window = self._window()
        await self.windows.create(window)
        event = self._event(record)
        member = DigestMember(record.subscription_id, 1, event.event_key, 1)
        assoc = DigestMemberAssociation(
            window.window_id, record.recipient, member, event
        )
        lease = await self._lease()
        observation = self._observation()
        item = SubscriptionEvaluationCommit(
            record.subscription_id,
            1,
            None,
            EvaluationState(1, {}),
            ObservationCursor("obs-1", 1, ObservationCompleteness.COMPLETE, ()),
            (event,),
            (assoc,),
        )
        self.assertTrue(
            await self.scheduler.commit_observation_with_evaluations(
                lease, ObservationEvaluationCommit(observation, (item,))
            )
        )
        again = await self.windows.create(window)
        self.assertEqual(again.utc_start, window.utc_start)
        self.assertEqual(again.members, (member,))

    async def test_r12_digest_envelopes_are_partitioned_by_trusted_route(self) -> None:
        window = self._window()
        await self.windows.create(window)
        evaluation_commits = []
        observation = self._observation()
        for index, owner in enumerate(("u1", "u2"), start=1):
            record = self._record(f"sub-{index}", owner=owner)
            await self._create(record)
            event = self._event(record, event_key=f"event-{index}")
            member = DigestMember(record.subscription_id, 1, event.event_key, 1)
            association = DigestMemberAssociation(
                window.window_id, record.recipient, member, event
            )
            evaluation_commits.append(
                SubscriptionEvaluationCommit(
                    record.subscription_id,
                    1,
                    None,
                    EvaluationState(1, {}),
                    ObservationCursor(
                        observation.observation_id, 1, observation.completeness, ()
                    ),
                    (event,),
                    (association,),
                )
            )
        lease = await self._lease()
        self.assertTrue(
            await self.scheduler.commit_observation_with_evaluations(
                lease,
                ObservationEvaluationCommit(observation, tuple(evaluation_commits)),
            )
        )
        due_routes = await self.windows.list_due_routes(now=self.now, limit=1)
        next_routes = await self.windows.list_due_routes(
            now=self.now, limit=1, after_cursor=due_routes[0].cursor
        )
        self.assertEqual(len(due_routes), 1)
        self.assertEqual(len(next_routes), 1)
        self.assertNotEqual(due_routes[0].cursor, next_routes[0].cursor)
        claims = [
            await self.windows.claim_due_envelope(
                window.window_id,
                self._recipient(owner),
                now=self.now,
                lease_expires_at=self.now + timedelta(minutes=1),
            )
            for owner in ("u1", "u2")
        ]
        self.assertEqual(
            len({claim.envelope.envelope_id for claim in claims if claim}), 2
        )
        self.assertEqual(
            {claim.envelope.recipient.conversation_id for claim in claims}, {"u1", "u2"}
        )

    async def test_r12_digest_retry_policy_is_durable_and_route_scan_waits(
        self,
    ) -> None:
        record = await self._create()
        window = self._window()
        await self.windows.create(window)
        self.assertIsNone(
            await self.windows.current_envelope(window.window_id, record.recipient)
        )
        self.assertIsNone(
            await self.windows.current_envelope(
                window.window_id, self._recipient("other")
            )
        )
        event = self._event(record)
        member = DigestMember(
            record.subscription_id, record.revision, event.event_key, 1
        )
        association = DigestMemberAssociation(
            window.window_id, record.recipient, member, event
        )
        lease = await self._lease()
        observation = self._observation()
        item = SubscriptionEvaluationCommit(
            record.subscription_id,
            record.revision,
            None,
            EvaluationState(1, {}),
            ObservationCursor(
                observation.observation_id, 1, observation.completeness, ()
            ),
            (event,),
            (association,),
        )
        self.assertTrue(
            await self.scheduler.commit_observation_with_evaluations(
                lease, ObservationEvaluationCommit(observation, (item,))
            )
        )
        claim = await self.windows.claim_due_envelope(
            window.window_id,
            record.recipient,
            now=self.now,
            lease_expires_at=self.now + timedelta(minutes=1),
        )
        sending = await self.windows.begin_envelope_send(
            claim,
            expected_revision=claim.envelope.revision,
            attempt_number=1,
            started_at=self.now,
        )
        attempt = DeliveryAttempt(
            1,
            DeliveryState.FAILED,
            digest_envelope_idempotency_key(window.window_id, record.recipient),
            self.now,
            self.now + timedelta(seconds=1),
            error_code="temporary",
        )
        retry_at = self.now + timedelta(seconds=10)
        failed = await self.windows.complete_envelope_send(
            claim,
            attempt,
            expected_revision=sending.revision,
            retry_at=retry_at,
        )
        self.assertEqual(failed.retry_at, retry_at)

        reopened = SQLiteDigestWindowRepository(
            SQLiteDatabase(self.path), subscription_gate_bindings=self.bindings
        )
        self.assertEqual(
            await reopened.current_envelope(window.window_id, record.recipient), failed
        )
        route_variants = (
            ConversationRef(
                "other-adapter",
                record.recipient.kind,
                record.recipient.conversation_id,
                record.recipient.delivery_route,
            ),
            ConversationRef(
                record.recipient.adapter_id,
                ConversationKind.GROUP,
                record.recipient.conversation_id,
                record.recipient.delivery_route,
            ),
            ConversationRef(
                record.recipient.adapter_id,
                record.recipient.kind,
                "other-conversation",
                record.recipient.delivery_route,
            ),
            ConversationRef(
                record.recipient.adapter_id,
                record.recipient.kind,
                record.recipient.conversation_id,
                "other-route",
            ),
        )
        for route in route_variants:
            self.assertIsNone(await reopened.current_envelope(window.window_id, route))
        self.assertIsNone(
            await reopened.current_envelope("other-window", record.recipient)
        )
        self.assertIsNone(
            await reopened.retry_failed_envelope(
                failed.envelope_id,
                expected_revision=failed.revision - 1,
                now=retry_at,
            )
        )
        self.assertIsNone(
            await reopened.retry_failed_envelope(
                failed.envelope_id,
                expected_revision=failed.revision,
                now=retry_at - timedelta(seconds=1),
            )
        )
        self.assertEqual(
            await reopened.list_due_routes(
                now=retry_at - timedelta(seconds=1), limit=5
            ),
            (),
        )
        due_route = await reopened.list_due_routes(now=retry_at, limit=5)
        self.assertEqual(len(due_route), 1)
        ready = await reopened.retry_failed_envelope(
            failed.envelope_id, expected_revision=failed.revision, now=retry_at
        )
        self.assertEqual(ready.state, DigestEnvelopeState.READY)
        self.assertIsNone(ready.retry_at)
        claim_again = await reopened.claim_due_envelope(
            window.window_id,
            record.recipient,
            now=retry_at,
            lease_expires_at=retry_at + timedelta(minutes=1),
        )
        self.assertEqual(len(claim_again.envelope.delivery_attempts), 1)

    async def test_digest_abort_releases_exact_claim_and_preserves_attempt_ledger(
        self,
    ) -> None:
        record, window, _event, member, old_claim = await self._digest_claim_fixture(
            "abort-send"
        )
        recovered_at = old_claim.expires_at
        recovered = await self.windows.recover_expired_envelope_claims(
            before=recovered_at, recovered_at=recovered_at
        )
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].state, DigestEnvelopeState.READY)

        claim = await self.windows.claim_due_envelope(
            window.window_id,
            record.recipient,
            now=recovered_at,
            lease_expires_at=recovered_at + timedelta(minutes=1),
        )
        self.assertNotEqual(claim.claim_token, old_claim.claim_token)
        sending = await self.windows.begin_envelope_send(
            claim,
            expected_revision=claim.envelope.revision,
            attempt_number=1,
            started_at=recovered_at,
        )
        idempotency_key = digest_envelope_idempotency_key(
            window.window_id, record.recipient
        )
        completed_at = recovered_at + timedelta(seconds=1)
        cancelled = DeliveryAttempt(
            1,
            DeliveryState.FAILED,
            idempotency_key,
            recovered_at,
            completed_at,
            error_code="cancelled_before_dispatch",
        )

        self.assertIsNone(
            await self.windows.abort_envelope_send(
                old_claim, cancelled, expected_revision=sending.revision
            )
        )
        self.assertIsNone(
            await self.windows.abort_envelope_send(
                claim, cancelled, expected_revision=sending.revision - 1
            )
        )
        invalid_attempts = (
            DeliveryAttempt(
                1,
                DeliveryState.CANCELLED,
                idempotency_key,
                recovered_at,
                completed_at,
                error_code="cancelled_before_dispatch",
            ),
            DeliveryAttempt(
                1,
                DeliveryState.FAILED,
                idempotency_key,
                recovered_at,
                completed_at,
                error_code="temporary",
            ),
            DeliveryAttempt(
                2,
                DeliveryState.FAILED,
                idempotency_key,
                recovered_at,
                completed_at,
                error_code="cancelled_before_dispatch",
            ),
            DeliveryAttempt(
                1,
                DeliveryState.FAILED,
                "wrong-idempotency-key",
                recovered_at,
                completed_at,
                error_code="cancelled_before_dispatch",
            ),
            DeliveryAttempt(
                1,
                DeliveryState.FAILED,
                idempotency_key,
                recovered_at + timedelta(seconds=1),
                completed_at + timedelta(seconds=1),
                error_code="cancelled_before_dispatch",
            ),
        )
        for invalid in invalid_attempts:
            with self.subTest(invalid=invalid):
                self.assertIsNone(
                    await self.windows.abort_envelope_send(
                        claim, invalid, expected_revision=sending.revision
                    )
                )
        self.assertEqual(
            await self.windows.current_envelope(window.window_id, record.recipient),
            sending,
        )

        aborted = await self.windows.abort_envelope_send(
            claim, cancelled, expected_revision=sending.revision
        )
        self.assertEqual(aborted.state, DigestEnvelopeState.READY)
        self.assertEqual(aborted.revision, sending.revision + 1)
        self.assertIsNone(aborted.claim_token)
        self.assertIsNone(aborted.claimed_at)
        self.assertIsNone(aborted.claim_expires_at)
        self.assertIsNone(aborted.retry_at)
        self.assertEqual(aborted.members, (member,))
        self.assertEqual(aborted.delivery_attempts, (cancelled,))
        self.assertEqual(
            await self.windows.current_envelope(window.window_id, record.recipient),
            aborted,
        )

        retry_claim = await self.windows.claim_due_envelope(
            window.window_id,
            record.recipient,
            now=completed_at,
            lease_expires_at=completed_at + timedelta(minutes=1),
        )
        self.assertEqual(retry_claim.envelope.delivery_attempts, (cancelled,))
        receipt = DigestMemberReceipt(member, DigestMemberDisposition.INCLUDED)
        reconciled = await self.windows.reconcile_envelope_members(
            retry_claim,
            (receipt,),
            expected_revision=retry_claim.envelope.revision,
        )
        self.assertEqual(reconciled.members, (member,))
        self.assertEqual(reconciled.delivery_attempts, (cancelled,))
        second_send = await self.windows.begin_envelope_send(
            retry_claim,
            expected_revision=reconciled.revision,
            attempt_number=2,
            started_at=completed_at + timedelta(seconds=1),
        )
        self.assertEqual(len(second_send.delivery_attempts), 2)
        self.assertEqual(second_send.delivery_attempts[0], cancelled)

    async def test_expired_unrecovered_digest_claim_accepts_exact_no_send_abort(
        self,
    ) -> None:
        record, window, _event, member, claim = await self._digest_claim_fixture(
            "abort-expired"
        )
        sending = await self.windows.begin_envelope_send(
            claim,
            expected_revision=claim.envelope.revision,
            attempt_number=1,
            started_at=self.now,
        )
        completed_at = claim.expires_at + timedelta(seconds=1)
        attempt = DeliveryAttempt(
            1,
            DeliveryState.FAILED,
            digest_envelope_idempotency_key(window.window_id, record.recipient),
            self.now,
            completed_at,
            error_code="cancelled_before_dispatch",
        )

        aborted = await self.windows.abort_envelope_send(
            claim, attempt, expected_revision=sending.revision
        )
        self.assertEqual(aborted.state, DigestEnvelopeState.READY)
        self.assertEqual(aborted.revision, sending.revision + 1)
        self.assertEqual(aborted.members, (member,))
        self.assertEqual(aborted.delivery_attempts, (attempt,))
        self.assertIsNone(aborted.claim_token)
        self.assertIsNone(aborted.claimed_at)
        self.assertIsNone(aborted.claim_expires_at)
        self.assertIsNone(aborted.retry_at)
        self.assertEqual(
            await self.windows.recover_expired_envelope_claims(
                before=completed_at, recovered_at=completed_at
            ),
            (),
        )
        self.assertEqual(
            await self.windows.current_envelope(window.window_id, record.recipient),
            aborted,
        )

    async def test_recovered_unknown_digest_claim_rejects_expired_no_send_abort(
        self,
    ) -> None:
        record, window, _event, _member, claim = await self._digest_claim_fixture(
            "abort-recovered-unknown"
        )
        sending = await self.windows.begin_envelope_send(
            claim,
            expected_revision=claim.envelope.revision,
            attempt_number=1,
            started_at=self.now,
        )
        recovered_at = claim.expires_at + timedelta(seconds=1)
        recovered = await self.windows.recover_expired_envelope_claims(
            before=recovered_at, recovered_at=recovered_at
        )
        self.assertEqual(len(recovered), 1)
        unknown = recovered[0]
        self.assertEqual(unknown.state, DigestEnvelopeState.UNKNOWN)
        self.assertEqual(unknown.delivery_attempts[-1].state, DeliveryState.UNKNOWN)

        abort_attempt = DeliveryAttempt(
            1,
            DeliveryState.FAILED,
            digest_envelope_idempotency_key(window.window_id, record.recipient),
            sending.delivery_attempts[-1].started_at,
            recovered_at + timedelta(seconds=1),
            error_code="cancelled_before_dispatch",
        )
        self.assertIsNone(
            await self.windows.abort_envelope_send(
                claim, abort_attempt, expected_revision=unknown.revision
            )
        )
        self.assertEqual(
            await self.windows.current_envelope(window.window_id, record.recipient),
            unknown,
        )

    async def test_digest_abort_never_reopens_unknown_send(self) -> None:
        record, window, _event, _member, claim = await self._digest_claim_fixture(
            "abort-unknown"
        )
        sending = await self.windows.begin_envelope_send(
            claim,
            expected_revision=claim.envelope.revision,
            attempt_number=1,
            started_at=self.now,
        )
        key = digest_envelope_idempotency_key(window.window_id, record.recipient)
        unknown_attempt = DeliveryAttempt(
            1,
            DeliveryState.UNKNOWN,
            key,
            self.now,
            self.now + timedelta(seconds=1),
        )
        unknown = await self.windows.complete_envelope_send(
            claim, unknown_attempt, expected_revision=sending.revision
        )
        self.assertEqual(unknown.state, DigestEnvelopeState.UNKNOWN)
        abort_attempt = DeliveryAttempt(
            1,
            DeliveryState.FAILED,
            key,
            self.now,
            self.now + timedelta(seconds=2),
            error_code="cancelled_before_dispatch",
        )
        self.assertIsNone(
            await self.windows.abort_envelope_send(
                claim, abort_attempt, expected_revision=unknown.revision
            )
        )
        self.assertEqual(
            await self.windows.current_envelope(window.window_id, record.recipient),
            unknown,
        )

    async def test_digest_abort_never_reopens_sent_send(self) -> None:
        record, window, _event, _member, claim = await self._digest_claim_fixture(
            "abort-sent"
        )
        sending = await self.windows.begin_envelope_send(
            claim,
            expected_revision=claim.envelope.revision,
            attempt_number=1,
            started_at=self.now,
        )
        key = digest_envelope_idempotency_key(window.window_id, record.recipient)
        sent_attempt = DeliveryAttempt(
            1,
            DeliveryState.SENT,
            key,
            self.now,
            self.now + timedelta(seconds=1),
            platform_message_id="message-1",
        )
        sent = await self.windows.complete_envelope_send(
            claim, sent_attempt, expected_revision=sending.revision
        )
        self.assertEqual(sent.state, DigestEnvelopeState.SENT)
        abort_attempt = DeliveryAttempt(
            1,
            DeliveryState.FAILED,
            key,
            self.now,
            self.now + timedelta(seconds=2),
            error_code="cancelled_before_dispatch",
        )
        self.assertIsNone(
            await self.windows.abort_envelope_send(
                claim, abort_attempt, expected_revision=sent.revision
            )
        )
        self.assertEqual(
            await self.windows.current_envelope(window.window_id, record.recipient),
            sent,
        )

    async def test_digest_abort_rolls_back_after_cross_table_constraint_failure(
        self,
    ) -> None:
        record, window, _event, _member, claim = await self._digest_claim_fixture(
            "abort-rollback"
        )
        sending = await self.windows.begin_envelope_send(
            claim,
            expected_revision=claim.envelope.revision,
            attempt_number=1,
            started_at=self.now,
        )
        abort_attempt = DeliveryAttempt(
            1,
            DeliveryState.FAILED,
            digest_envelope_idempotency_key(window.window_id, record.recipient),
            self.now,
            self.now + timedelta(seconds=1),
            error_code="cancelled_before_dispatch",
        )
        async with self.db.unit_of_work(begin_mode="IMMEDIATE") as unit:
            unit.execute(
                "CREATE TRIGGER force_digest_receipt_fk_failure "
                "AFTER UPDATE ON b04_digest_envelopes BEGIN "
                "INSERT INTO b04_digest_member_receipts "
                "(envelope_id,subscription_id,subscription_revision,event_key,event_version,disposition,receipt_json) "
                "VALUES ('missing-envelope','missing-subscription',1,'missing-event',1,'included','{}'); "
                "END"
            )

        with self.assertRaises(sqlite3.IntegrityError):
            await self.windows.abort_envelope_send(
                claim, abort_attempt, expected_revision=sending.revision
            )
        persisted = await self.windows.current_envelope(
            window.window_id, record.recipient
        )
        self.assertEqual(persisted, sending)

    async def test_r12_receipts_replace_current_set_after_pre_send_crash(self) -> None:
        record, window, event, member, claim = await self._digest_claim_fixture(
            "pre-send-recovery"
        )
        receipt = DigestMemberReceipt(member, DigestMemberDisposition.INCLUDED)
        reconciled = await self.windows.reconcile_envelope_members(
            claim, (receipt,), expected_revision=claim.envelope.revision
        )
        self.assertEqual(reconciled.member_receipts, (receipt,))

        recovery_time = claim.expires_at + timedelta(seconds=1)
        reopened = SQLiteDigestWindowRepository(
            SQLiteDatabase(self.path), subscription_gate_bindings=self.bindings
        )
        recovered = await reopened.recover_expired_envelope_claims(
            before=recovery_time, recovered_at=recovery_time
        )
        self.assertEqual(recovered[0].state, DigestEnvelopeState.READY)
        self.assertEqual(recovered[0].member_receipts, (receipt,))
        retry_claim = await reopened.claim_due_envelope(
            window.window_id,
            record.recipient,
            now=recovery_time,
            lease_expires_at=recovery_time + timedelta(minutes=1),
        )
        reconciled_again = await reopened.reconcile_envelope_members(
            retry_claim,
            (receipt,),
            expected_revision=retry_claim.envelope.revision,
        )
        self.assertEqual(reconciled_again.member_receipts, (receipt,))
        self.assertEqual(reconciled_again.members, (member,))
        self.assertEqual(
            reconciled_again.member_associations[0].event.event_key, event.event_key
        )

    async def test_r12_receipts_replace_current_set_after_failed_retry(self) -> None:
        record, window, _event, member, claim = await self._digest_claim_fixture(
            "failed-retry"
        )
        receipt = DigestMemberReceipt(member, DigestMemberDisposition.INCLUDED)
        reconciled = await self.windows.reconcile_envelope_members(
            claim, (receipt,), expected_revision=claim.envelope.revision
        )
        sending = await self.windows.begin_envelope_send(
            claim,
            expected_revision=reconciled.revision,
            attempt_number=1,
            started_at=self.now,
        )
        first_attempt = DeliveryAttempt(
            1,
            DeliveryState.FAILED,
            digest_envelope_idempotency_key(window.window_id, record.recipient),
            self.now,
            self.now + timedelta(seconds=1),
            error_code="temporary",
        )
        retry_at = self.now + timedelta(seconds=10)
        failed = await self.windows.complete_envelope_send(
            claim,
            first_attempt,
            expected_revision=sending.revision,
            retry_at=retry_at,
        )

        reopened = SQLiteDigestWindowRepository(
            SQLiteDatabase(self.path), subscription_gate_bindings=self.bindings
        )
        ready = await reopened.retry_failed_envelope(
            failed.envelope_id, expected_revision=failed.revision, now=retry_at
        )
        self.assertEqual(ready.state, DigestEnvelopeState.READY)
        retry_claim = await reopened.claim_due_envelope(
            window.window_id,
            record.recipient,
            now=retry_at,
            lease_expires_at=retry_at + timedelta(minutes=1),
        )
        reconciled_again = await reopened.reconcile_envelope_members(
            retry_claim,
            (receipt,),
            expected_revision=retry_claim.envelope.revision,
        )
        self.assertEqual(reconciled_again.member_receipts, (receipt,))
        second_send = await reopened.begin_envelope_send(
            retry_claim,
            expected_revision=reconciled_again.revision,
            attempt_number=2,
            started_at=retry_at,
        )
        sent = DeliveryAttempt(
            2,
            DeliveryState.SENT,
            digest_envelope_idempotency_key(window.window_id, record.recipient),
            retry_at,
            retry_at + timedelta(seconds=1),
        )
        completed = await reopened.complete_envelope_send(
            retry_claim,
            sent,
            expected_revision=second_send.revision,
        )
        self.assertEqual(completed.state, DigestEnvelopeState.SENT)
        self.assertEqual(len(completed.delivery_attempts), 2)

    async def test_r13_window_bounds_survive_restart_and_due_claim_is_single(
        self,
    ) -> None:
        record = await self._create()
        window = self._window()
        await self.windows.create(window)
        event = self._event(record)
        member = DigestMember(record.subscription_id, 1, event.event_key, 1)
        assoc = DigestMemberAssociation(
            window.window_id, record.recipient, member, event
        )
        lease = await self._lease()
        obs = self._observation()
        item = SubscriptionEvaluationCommit(
            record.subscription_id,
            1,
            None,
            EvaluationState(1, {}),
            ObservationCursor(obs.observation_id, 1, obs.completeness, ()),
            (event,),
            (assoc,),
        )
        await self.scheduler.commit_observation_with_evaluations(
            lease, ObservationEvaluationCommit(obs, (item,))
        )
        reopened = SQLiteDigestWindowRepository(
            SQLiteDatabase(self.path), subscription_gate_bindings=self.bindings
        )
        recovered = await reopened.get(window.window_id)
        self.assertEqual(
            (recovered.utc_start, recovered.utc_end, recovered.due_at),
            (window.utc_start, window.utc_end, window.due_at),
        )
        claims = await asyncio.gather(
            *(
                reopened.claim_due_envelope(
                    window.window_id,
                    record.recipient,
                    now=self.now,
                    lease_expires_at=self.now + timedelta(minutes=2),
                )
                for _ in range(2)
            )
        )
        self.assertEqual(sum(claim is not None for claim in claims), 1)
        claim = next(item for item in claims if item is not None)
        expired = self.now + timedelta(minutes=2, seconds=1)
        recovered_claim = await reopened.recover_expired_envelope_claims(
            before=expired, recovered_at=expired + timedelta(seconds=1)
        )
        self.assertEqual(recovered_claim[0].state.value, "ready")
        self.assertEqual(len(await reopened.list_due_routes(now=self.now, limit=5)), 1)
        second_claim = await reopened.claim_due_envelope(
            window.window_id,
            record.recipient,
            now=expired + timedelta(seconds=1),
            lease_expires_at=expired + timedelta(minutes=2),
        )
        self.assertNotEqual(second_claim.claim_token, claim.claim_token)
        sending = await reopened.begin_envelope_send(
            second_claim,
            expected_revision=second_claim.envelope.revision,
            attempt_number=1,
            started_at=expired + timedelta(seconds=2),
        )
        self.assertEqual(sending.state.value, "sending")
        recovered_send = await reopened.recover_expired_envelope_claims(
            before=expired + timedelta(minutes=2, seconds=1),
            recovered_at=expired + timedelta(minutes=2, seconds=2),
        )
        self.assertEqual(recovered_send[0].state, DigestEnvelopeState.UNKNOWN)
        self.assertEqual(recovered_send[0].delivery_attempts[0].attempt_number, 1)
        self.assertEqual(
            await reopened.current_envelope(window.window_id, record.recipient),
            recovered_send[0],
        )
        self.assertIsNone(
            await reopened.retry_failed_envelope(
                recovered_send[0].envelope_id,
                expected_revision=recovered_send[0].revision,
                now=expired + timedelta(minutes=3),
            )
        )
        self.assertEqual(
            await reopened.list_due_routes(now=expired + timedelta(minutes=3), limit=5),
            (),
        )
        self.assertIsNone(
            await reopened.claim_due_envelope(
                window.window_id,
                record.recipient,
                now=expired + timedelta(minutes=3),
                lease_expires_at=expired + timedelta(minutes=4),
            )
        )

    async def test_r14_removing_one_digest_member_keeps_other_member(self) -> None:
        window = self._window()
        await self.windows.create(window)
        associations = []
        commits = []
        observation = self._observation()
        for index in (1, 2):
            record = self._record(f"sub-{index}", owner="u1")
            await self._create(record)
            event = self._event(record, event_key=f"event-{index}")
            member = DigestMember(record.subscription_id, 1, event.event_key, 1)
            association = DigestMemberAssociation(
                window.window_id, record.recipient, member, event
            )
            associations.append(association)
            commits.append(
                SubscriptionEvaluationCommit(
                    record.subscription_id,
                    1,
                    None,
                    EvaluationState(1, {}),
                    ObservationCursor(
                        observation.observation_id, 1, observation.completeness, ()
                    ),
                    (event,),
                    (association,),
                )
            )
        lease = await self._lease()
        self.assertTrue(
            await self.scheduler.commit_observation_with_evaluations(
                lease, ObservationEvaluationCommit(observation, tuple(commits))
            )
        )
        claim = await self.windows.claim_due_envelope(
            window.window_id,
            self._recipient(),
            now=self.now,
            lease_expires_at=self.now + timedelta(minutes=2),
        )
        receipts = tuple(
            DigestMemberReceipt(
                item.member,
                DigestMemberDisposition.CANCELLED
                if item.member.subscription_id == "sub-1"
                else DigestMemberDisposition.INCLUDED,
            )
            for item in associations
        )
        reconciled = await self.windows.reconcile_envelope_members(
            claim, receipts, expected_revision=claim.envelope.revision
        )
        self.assertEqual(
            tuple(member.subscription_id for member in reconciled.members), ("sub-2",)
        )
        self.assertEqual(len(reconciled.member_associations), 1)

    async def test_r14_reconcile_and_send_recheck_current_subscription(self) -> None:
        window = self._window("window-recheck")
        await self.windows.create(window)
        records = (
            self._record("sub-cancelled", owner="u1"),
            self._record("sub-revised", owner="u1"),
        )
        commits = []
        observation = self._observation(identity="obs-recheck")
        for index, record in enumerate(records, start=1):
            await self._create(record)
            event = self._event(record, event_key=f"recheck-event-{index}")
            member = DigestMember(record.subscription_id, 1, event.event_key, 1)
            association = DigestMemberAssociation(
                window.window_id, record.recipient, member, event
            )
            commits.append(
                SubscriptionEvaluationCommit(
                    record.subscription_id,
                    1,
                    None,
                    EvaluationState(1, {}),
                    ObservationCursor(
                        observation.observation_id,
                        1,
                        observation.completeness,
                        (),
                    ),
                    (event,),
                    (association,),
                )
            )
        lease = await self._lease()
        self.assertTrue(
            await self.scheduler.commit_observation_with_evaluations(
                lease, ObservationEvaluationCommit(observation, tuple(commits))
            )
        )
        claim = await self.windows.claim_due_envelope(
            window.window_id,
            self._recipient(),
            now=self.now,
            lease_expires_at=self.now + timedelta(minutes=2),
        )
        cancelled = self._record(
            "sub-cancelled", revision=2, owner="u1", status=SubscriptionStatus.CANCELLED
        )
        await self.lifecycle.apply(
            SubscriptionJobChange(
                SubscriptionJobChangeKind.CANCEL, cancelled, None, 1, 1
            )
        )
        receipts = tuple(
            DigestMemberReceipt(member, DigestMemberDisposition.INCLUDED)
            for member in claim.envelope.members
        )
        reconciled = await self.windows.reconcile_envelope_members(
            claim, receipts, expected_revision=claim.envelope.revision
        )
        self.assertEqual(
            tuple(member.subscription_id for member in reconciled.members),
            ("sub-revised",),
        )
        self.assertEqual(
            tuple(item.disposition for item in reconciled.member_receipts),
            (DigestMemberDisposition.CANCELLED, DigestMemberDisposition.INCLUDED),
        )

        revised = self._record("sub-revised", revision=2, owner="u1")
        await self.lifecycle.apply(
            SubscriptionJobChange(
                SubscriptionJobChangeKind.REVISE,
                revised,
                self._association(revised, assoc_revision=2),
                1,
                1,
            ),
            initial_run=self._run(revised.collection_key),
        )
        self.assertIsNone(
            await self.windows.begin_envelope_send(
                claim,
                expected_revision=reconciled.revision,
                attempt_number=1,
                started_at=self.now,
            )
        )

    async def test_r15_dst_policy_and_utc_fields_round_trip(self) -> None:
        window = DigestWindow(
            "dst-window",
            "Europe/Paris",
            "fold-2026-10-25",
            datetime(2026, 10, 25, 0, tzinfo=UTC),
            datetime(2026, 10, 25, 2, tzinfo=UTC),
            datetime(2026, 10, 25, 2, tzinfo=UTC),
            DstFoldPolicy.SECOND_OCCURRENCE,
            DstGapPolicy.NEXT_VALID_INSTANT,
            (),
            policy_revision=7,
        )
        await self.windows.create(window)
        actual = await SQLiteDigestWindowRepository(
            SQLiteDatabase(self.path), subscription_gate_bindings=self.bindings
        ).get(window.window_id)
        self.assertEqual(actual, window)
        with self.assertRaises(UniqueConstraintViolation):
            await self.windows.create(
                DigestWindow(
                    window.window_id,
                    window.timezone_name,
                    window.local_schedule_key,
                    window.utc_start,
                    window.utc_end,
                    window.due_at,
                    window.fold_policy,
                    DstGapPolicy.SKIP,
                    (),
                    policy_revision=8,
                )
            )

    async def test_r16_conflicts_and_storage_errors_do_not_expose_private_payload(
        self,
    ) -> None:
        private = self._record(
            key=self._key(
                OwnerScope.authorized("u1", GrantReference("secret-grant", 1))
            )
        )
        await self._create(private)
        with self.assertRaises(RevisionConflict) as caught:
            await self.subscriptions.revise(
                self._record(revision=3, key=private.collection_key),
                expected_revision=2,
            )
        self.assertNotIn("secret-grant", str(caught.exception))
        self.assertNotIn("do-not-log", str(caught.exception))


def json_load(value: str):
    from ygl_test_subject.infrastructure.sqlite.repositories_subscriptions import _load

    return _load(value)


if __name__ == "__main__":
    unittest.main()
