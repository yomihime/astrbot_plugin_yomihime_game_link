from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from ygl_test_subject.api.contexts import InvocationOrigin
from ygl_test_subject.api.manifests import (
    CapabilityDescriptor,
    CapabilityEffect,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
    SourceDeclaration,
)
from ygl_test_subject.api.services import (
    CapabilityHealth,
    Grant,
    GrantStatus,
    HealthReport,
    HealthStatus,
    ModuleHandlers,
)
from ygl_test_subject.api.storage import GrantReference, OwnerScope, OwnershipKind
from ygl_test_subject.api.subscriptions import (
    CadenceConfiguration,
    CollectionKey,
    ConversationKind,
    ConversationRef,
    DigestScheduleProfile,
    DstFoldPolicy,
    DstGapPolicy,
    DueCollectionJob,
    EvaluationState,
    NormalizedInput,
    Observation,
    ObservationCompleteness,
    ObservationCursor,
    ObservationEvaluationCommit,
    ScheduleDescriptor,
    ScheduleTrigger,
    SubscriptionDescriptor,
    SubscriptionEvaluationCommit,
    SubscriptionJobAssociation,
    SubscriptionJobChange,
    SubscriptionJobChangeKind,
    SubscriptionRecord,
    SubscriptionStatus,
)
from ygl_test_subject.core.admission import AdmissionController
from ygl_test_subject.core.context_issuer import ContextIssuer
from ygl_test_subject.core.lifecycle import LifecycleController
from ygl_test_subject.core.ports import CollectionRunRequest, ExecutionLease
from ygl_test_subject.core.registry import Registry
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories_subscriptions import (
    SQLiteSchedulerRepository,
    SQLiteSubscriptionJobRepository,
    SQLiteSubscriptionLifecycleRepository,
    SQLiteSubscriptionStore,
)
from ygl_test_subject.services.scheduler import (
    ExecutionClaimProofRegistry,
    RescheduleConfiguration,
    SchedulerQuotas,
    SharedCollectionScheduler,
    canonical_collection_key,
    digest_window_for_date,
)

from tests.fixtures.b04_runtime import _run_async_from_sync


class _Collector:
    def __init__(
        self, completeness=ObservationCompleteness.COMPLETE, fail=False
    ) -> None:
        self.started = asyncio.Event()
        self.finish = asyncio.Event()
        self.calls = 0
        self.completeness = completeness
        self.fail = fail

    def normalize(self, parameters):
        return NormalizedInput(dict(parameters))

    async def collect(self, context, parameters, previous):
        self.calls += 1
        self.started.set()
        await self.finish.wait()
        if self.fail:
            raise RuntimeError("collector failed")
        return Observation(
            f"obs-{self.calls}",
            context.key,
            1,
            None,
            datetime(2026, 1, 1, tzinfo=UTC),
            self.completeness,
            ("item-x",) if self.completeness is ObservationCompleteness.PARTIAL else (),
            {"value": 1},
        )


class _Capability:
    async def invoke(self, *_args):
        return None


class _Evaluator:
    def evaluate(self, *_args):
        raise AssertionError("scheduler does not run subscription evaluators")


class _Tasks:
    async def await_result(self, work):
        return await work


class _Binder:
    def __init__(self) -> None:
        self.invocations = []

    async def bind(self, invocation):
        self.invocations.append(invocation)
        return SimpleNamespace(tasks=_Tasks())


class _SchedulerModuleInstance:
    def __init__(self, handlers):
        self._handlers = handlers

    def handlers(self):
        return self._handlers

    async def start(self):
        return None

    async def stop(self):
        return None

    async def check_health(self):
        return HealthReport({"read.data": CapabilityHealth(HealthStatus.AVAILABLE)})


class _Repo:
    def __init__(self, now):
        self.now = now
        self.claims = 0
        self.commits = []
        self.next_due = []
        self.releases = []
        self.evaluation = None
        self.active = {}

    async def claim_due(self, request, *, now):
        self.claims += 1
        lease = ExecutionLease(
            request.key,
            "lease-token",
            request.config_revision,
            request.module_epoch,
            request.registry_revision,
            now + timedelta(seconds=request.cadence_seconds),
        )

        self.active[lease.token] = lease
        return lease

    async def is_current(self, lease, *, now):
        return self.active.get(lease.token) is lease and lease.expires_at > now

    async def current_evaluation(self, subscription_id, collection_key):
        return self.evaluation

    async def commit_observation(self, lease, observation):
        self.commits.append((lease, observation))
        return True

    async def commit_observation_with_evaluations(
        self, lease, commit, *, next_due_at=None
    ):
        self.commits.append((lease, commit.observation, commit))
        self.next_due.append(next_due_at)
        return True

    async def release(self, lease):
        self.releases.append(lease)
        self.active.pop(lease.token, None)


class _SubscriptionStore:
    def __init__(self, records):
        self.records = {record.subscription_id: record for record in records}
        self.active_digest = []

    async def current(self, subscription_id):
        return self.records.get(subscription_id)

    async def list_active_digest_schedules(self, *, limit, after_subscription_id=None):
        records = [
            r
            for r in self.active_digest
            if after_subscription_id is None or r.cursor > after_subscription_id
        ]
        return tuple(records[:limit])


class _JobLinks:
    def __init__(self, associations):
        self.associations = associations

    async def for_collection(self, key):
        return tuple(item for item in self.associations if item.collection_key == key)


class _Grants:
    def __init__(self, values=()):
        self.values = {item.grant_id: item for item in values}

    async def current_grant(self, grant_id):
        return self.values.get(grant_id)


class _Routes:
    async def resolve_current(self, owner_id, recipient):
        return recipient

    async def resolve_private(self, owner_id, recipient):
        return recipient


class _Prepare:
    def __init__(self, advance=None):
        self.advance = advance

    async def prepare_evaluations(self, lease, observation):
        if self.advance is not None:
            self.advance()
        return ObservationEvaluationCommit(observation, ())


class _MutableClock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


class _Windows:
    def __init__(self):
        self.rows = {}
        self.created = []
        self.recoveries = []
        self.routes = ()

    async def create(self, window):
        existing = self.rows.setdefault(window.window_id, window)
        self.created.append(window.window_id)
        return existing

    async def get(self, window_id):
        return self.rows.get(window_id)

    async def recover_expired_envelope_claims(self, *, before, recovered_at):
        self.recoveries.append((before, recovered_at))
        return ()

    async def list_due_routes(self, *, now, limit, after_cursor=None):
        return self.routes[:limit]


def _schedule(*, scope=OwnershipKind.PUBLIC):
    return ScheduleDescriptor(
        collector_id="prices",
        key_version=1,
        source_id="market",
        data_version=1,
        input_schema={
            "type": "object",
            "properties": {"item": {"type": "string"}},
            "required": ["item"],
        },
        shared_scope=scope,
        trigger=ScheduleTrigger.PERIODIC,
        minimum_interval_seconds=30,
        default_interval_seconds=60,
        interval_config_key="poll_seconds",
    )


def _profile(
    timezone="America/New_York",
    local_time="01:30",
    fold=DstFoldPolicy.FIRST_OCCURRENCE,
    gap=DstGapPolicy.SKIP,
    revision=1,
):
    return DigestScheduleProfile(timezone, local_time, 3600, fold, gap, revision)


def _record(
    *,
    subscription_id="sub-1",
    owner="alice",
    key=None,
    mode="instant",
    profile=None,
    recipient=None,
    grant=None,
    type_id="prices_alert",
):
    key = key or CollectionKey(
        "pkg/pricing",
        "prices",
        1,
        "market",
        NormalizedInput({"item": "x"}),
        OwnerScope.public(),
    )
    return SubscriptionRecord(
        subscription_id,
        1,
        key.module_id,
        key,
        owner,
        grant,
        recipient
        or ConversationRef("test", ConversationKind.DIRECT, owner, f"route-{owner}"),
        mode,
        {},
        SubscriptionStatus.ACTIVE,
        type_id,
        profile,
    )


def _authorized_record(*, mode="instant", profile=None):
    grant = GrantReference("grant-1", 1)
    key = CollectionKey(
        "pkg/pricing",
        "prices",
        1,
        "market",
        NormalizedInput({"item": "x"}),
        OwnerScope.authorized("alice", grant),
    )
    return _record(key=key, grant=grant, mode=mode, profile=profile)


def _runtime(
    *,
    collector=None,
    records=(),
    links=(),
    repo=None,
    subscriptions=None,
    windows=None,
    now=None,
    random_value=0.5,
    reschedule=None,
    clock=None,
    evaluation_preparer=None,
    job_links=None,
    grant_store=None,
    collection_timeout_seconds=5.0,
    scope=OwnershipKind.PUBLIC,
):
    collector = collector or _Collector()
    schedule = _schedule(scope=scope)
    module = ModuleManifest(
        "pricing",
        "pricing",
        ModuleCategory.GAME,
        "test.module:Factory",
        "0.1.0",
        (
            CapabilityDescriptor(
                "read.data",
                {"type": "object", "properties": {}, "required": []},
                InvocationPolicy.NATURAL_LANGUAGE_ALLOWED,
                CapabilityEffect.READ_ONLY,
            ),
        ),
        schedules=(schedule,),
        subscriptions=(
            SubscriptionDescriptor(
                "prices_alert",
                "prices",
                "matcher",
                {"type": "object", "properties": {}, "required": []},
                ("instant", "digest"),
            ),
        ),
        sources=(SourceDeclaration("market", "example.test", requests_per_minute=60),),
    )
    handlers = ModuleHandlers(
        {"read.data": _Capability()}, {"prices": collector}, {"matcher": _Evaluator()}
    )
    registry = Registry()
    registry.register_package(
        PackageManifest(
            "pkg", "0.1.0", "1.1.0", (module,), "Tests", "AGPL-3.0", "offline"
        ),
        {"pricing": handlers},
    )
    current_time = now or datetime(2026, 1, 1, tzinfo=UTC)
    issuer = ContextIssuer(clock=lambda: 1.0)
    proof_registry = ExecutionClaimProofRegistry(now=clock or (lambda: current_time))
    lifecycle_ref = {}
    admission = AdmissionController(
        registry,
        issuer,
        current_run=lambda module_id: lifecycle_ref["controller"].current_identity(
            module_id
        ),
        is_active=lambda identity: lifecycle_ref["controller"]._is_active_identity(
            identity
        ),
        health_query=lambda module_id, capability_id: lifecycle_ref[
            "controller"
        ].capability_health(module_id, capability_id),
        execution_claim_prover=proof_registry,
        utc_clock=clock or (lambda: current_time),
    )
    lifecycle = LifecycleController(registry, issuer=issuer, admission=admission)
    lifecycle_ref["controller"] = lifecycle
    instance = _SchedulerModuleInstance(handlers)
    install_operation = "scheduler-test-install"
    owned_handlers = lifecycle.adopt_candidate(
        "pkg", module, install_operation, instance
    )
    lifecycle.install_dormant(
        "pkg", "pkg/pricing", install_operation, instance, owned_handlers
    )
    start_operation = "scheduler-test-start"
    identity, _ = _run_async_from_sync(
        lifecycle.start_candidate("pkg/pricing", start_operation)
    )
    lifecycle.publish_committed_intent(
        "pkg/pricing",
        start_operation,
        identity,
        True,
        registry.snapshot().revision,
    )
    records = tuple(records)
    links = tuple(links)
    repo = repo or _Repo(current_time)
    subs = subscriptions or _SubscriptionStore(records)
    binder = _Binder()
    windows = windows or _Windows()
    scheduler = SharedCollectionScheduler(
        registry=registry,
        issuer=issuer,
        lifecycle=lifecycle,
        admission=lifecycle.admission,
        execution_claim_proofs=proof_registry,
        binder_for=lambda _module_id: binder,
        repository=repo,
        subscriptions=subs,
        job_links=job_links or _JobLinks(links),
        digest_windows=windows,
        grant_store=grant_store
        or _Grants(
            Grant(
                record.grant.grant_id,
                record.grant.revision,
                record.owner_id,
                record.module_id,
                "test-account",
                ("read",),
                None,
                GrantStatus.ACTIVE,
            )
            for record in records
            if record.grant is not None
        ),
        routes=_Routes(),
        evaluation_preparer=evaluation_preparer or _Prepare(),
        cadence=CadenceConfiguration((30.0, 60.0, 120.0)),
        quotas=SchedulerQuotas(4, 2, 2, 20, collection_timeout_seconds, 15.0, 2),
        reschedule=reschedule or RescheduleConfiguration(300.0, 0.2, 0.1, 0.5),
        now=clock or (lambda: current_time),
        monotonic_clock=lambda: 1.0,
        random_source=lambda: random_value,
    )
    key = CollectionKey(
        "pkg/pricing",
        "prices",
        1,
        "market",
        NormalizedInput({"item": "x"}),
        (
            OwnerScope.public()
            if scope is OwnershipKind.PUBLIC
            else OwnerScope.authorized("alice", GrantReference("grant-1", 1))
        ),
    )
    return scheduler, collector, repo, registry, key, subs, windows, binder


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    def test_key_uses_registered_normalizer_and_scope(self):
        registry = Registry()
        scheduler, _, _, registry, _, _, _, _ = _runtime()
        module = registry.snapshot().module("pkg/pricing")
        descriptor = module.manifest.schedules[0]
        first = canonical_collection_key(
            module, descriptor, {"item": "x"}, OwnerScope.public()
        )
        second = canonical_collection_key(
            module, descriptor, {"item": "x"}, OwnerScope.public()
        )
        self.assertEqual(first, second)
        self.assertEqual(first.parameters.values, {"item": "x"})
        with self.assertRaises(Exception):
            canonical_collection_key(
                module, descriptor, {"unknown": "x"}, OwnerScope.public()
            )
        disabled = replace(module, enabled=False)
        with self.assertRaises(Exception):
            canonical_collection_key(
                disabled, descriptor, {"item": "x"}, OwnerScope.public()
            )

    async def test_public_concurrent_users_share_one_claim_and_future(self):
        alice = _record(subscription_id="sub-a", owner="alice")
        bob = _record(subscription_id="sub-b", owner="bob")
        links = tuple(
            SubscriptionJobAssociation(r.subscription_id, 1, r.collection_key, 60, 1)
            for r in (alice, bob)
        )
        scheduler, collector, repo, _, key, _, _, binder = _runtime(
            records=(alice, bob), links=links
        )
        candidate = DueCollectionJob(
            key, datetime(2026, 1, 1, tzinfo=UTC), 60, 1, "job"
        )
        first = asyncio.create_task(scheduler.run_due_job(candidate))
        try:
            await asyncio.wait_for(collector.started.wait(), 2)
        except TimeoutError:
            self.fail(
                f"collector did not start: calls={collector.calls}, claims={repo.claims}, "
                f"first={first.result() if first.done() else None}, commits={repo.commits}"
            )
        second = asyncio.create_task(scheduler.run_due_job(candidate))
        collector.finish.set()
        left, right = await asyncio.gather(first, second)
        self.assertIs(left, right)
        self.assertEqual(collector.calls, 1)
        self.assertEqual(repo.claims, 1)
        self.assertEqual(binder.invocations[0].origin, InvocationOrigin.SCHEDULER)
        self.assertEqual(
            repo.commits[0][1].collected_at, datetime(2026, 1, 1, tzinfo=UTC)
        )

    async def test_scheduler_rejects_untrusted_grant_authority_before_collection(self):
        record = _authorized_record()
        base = Grant(
            "grant-1",
            1,
            "alice",
            "pkg/pricing",
            "test-account",
            ("read",),
            None,
            GrantStatus.ACTIVE,
        )
        cases = (
            (
                "wrong owner",
                Grant(
                    "grant-1",
                    1,
                    "mallory",
                    "pkg/pricing",
                    "acct",
                    ("read",),
                    None,
                    GrantStatus.ACTIVE,
                ),
            ),
            (
                "wrong module",
                Grant(
                    "grant-1",
                    1,
                    "alice",
                    "pkg/other",
                    "acct",
                    ("read",),
                    None,
                    GrantStatus.ACTIVE,
                ),
            ),
            (
                "revoked",
                Grant(
                    "grant-1",
                    1,
                    "alice",
                    "pkg/pricing",
                    "acct",
                    ("read",),
                    None,
                    GrantStatus.REVOKED,
                ),
            ),
            (
                "expired",
                Grant(
                    "grant-1",
                    1,
                    "alice",
                    "pkg/pricing",
                    "acct",
                    ("read",),
                    None,
                    GrantStatus.ACTIVE,
                    datetime(2025, 12, 31, tzinfo=UTC),
                ),
            ),
            (
                "stale revision",
                Grant(
                    "grant-1",
                    2,
                    "alice",
                    "pkg/pricing",
                    "acct",
                    ("read",),
                    None,
                    GrantStatus.ACTIVE,
                ),
            ),
        )
        self.assertEqual(base.revision, record.grant.revision)
        for label, grant in cases:
            with self.subTest(label=label):
                collector = _Collector()
                store = _Grants((grant,))
                link = SubscriptionJobAssociation(
                    record.subscription_id, 1, record.collection_key, 60, 1
                )
                scheduler, _, repo, _, key, _, _, _ = _runtime(
                    collector=collector,
                    records=(record,),
                    links=(link,),
                    grant_store=store,
                    scope=OwnershipKind.AUTHORIZED,
                )
                result = await scheduler.run_due_job(
                    DueCollectionJob(
                        key, datetime(2026, 1, 1, tzinfo=UTC), 60, 1, "job"
                    )
                )
                self.assertIsNone(result)
                self.assertEqual(collector.calls, 0)
                self.assertEqual(repo.claims, 0)
                self.assertEqual(repo.commits, [])

    async def test_grant_revocation_during_collection_prevents_commit(self):
        record = _authorized_record()
        active = Grant(
            "grant-1",
            1,
            "alice",
            "pkg/pricing",
            "test-account",
            ("read",),
            None,
            GrantStatus.ACTIVE,
        )
        store = _Grants((active,))
        collector = _Collector()
        link = SubscriptionJobAssociation(
            record.subscription_id, 1, record.collection_key, 60, 1
        )
        scheduler, _, repo, _, key, _, _, _ = _runtime(
            collector=collector,
            records=(record,),
            links=(link,),
            grant_store=store,
            scope=OwnershipKind.AUTHORIZED,
        )
        task = asyncio.create_task(
            scheduler.run_due_job(
                DueCollectionJob(key, datetime(2026, 1, 1, tzinfo=UTC), 60, 1, "job")
            )
        )
        await collector.started.wait()
        store.values["grant-1"] = Grant(
            "grant-1",
            1,
            "alice",
            "pkg/pricing",
            "test-account",
            ("read",),
            None,
            GrantStatus.REVOKED,
        )
        collector.finish.set()
        self.assertIsNone(await task)
        self.assertEqual(repo.commits, [])

    async def test_digest_window_page_rejects_grant_with_wrong_owner(self):
        record = _authorized_record(mode="digest", profile=_profile())
        from ygl_test_subject.api.subscriptions import ActiveDigestSchedule

        subscriptions = _SubscriptionStore((record,))
        subscriptions.active_digest = [ActiveDigestSchedule(record)]
        wrong_owner = Grant(
            "grant-1",
            1,
            "mallory",
            "pkg/pricing",
            "test-account",
            ("read",),
            None,
            GrantStatus.ACTIVE,
        )
        windows = _Windows()
        scheduler, _, _, _, _, _, _, _ = _runtime(
            records=(record,),
            subscriptions=subscriptions,
            windows=windows,
            grant_store=_Grants((wrong_owner,)),
            scope=OwnershipKind.AUTHORIZED,
        )
        await scheduler.create_digest_windows_page(local_dates=(date(2026, 1, 1),))
        self.assertEqual(windows.rows, {})

    async def test_complete_due_time_uses_post_evaluation_completion_and_jitter(self):
        clock = _MutableClock(datetime(2026, 1, 1, tzinfo=UTC))
        collector = _Collector()
        preparer = _Prepare(lambda: clock.advance(20))
        record = _record()
        link = SubscriptionJobAssociation(
            record.subscription_id, 1, record.collection_key, 60, 1
        )
        scheduler, _, repo, _, key, _, _, _ = _runtime(
            collector=collector,
            records=(record,),
            links=(link,),
            clock=clock,
            evaluation_preparer=preparer,
            random_value=0.75,
            reschedule=RescheduleConfiguration(300, 0.2, 0.1, 0.5),
        )
        candidate = DueCollectionJob(
            key, datetime(2026, 1, 1, tzinfo=UTC), 60, 1, "job"
        )
        task = asyncio.create_task(scheduler.run_due_job(candidate))
        await collector.started.wait()
        collector.finish.set()
        result = await task
        self.assertTrue(result.committed)
        self.assertEqual(
            repo.next_due,
            [datetime(2026, 1, 1, 0, 1, 26, tzinfo=UTC)],
        )
        self.assertEqual(
            repo.commits[0][1].collected_at, datetime(2026, 1, 1, tzinfo=UTC)
        )

    async def test_partial_due_time_uses_cadence_with_partial_jitter(self):
        collector = _Collector(ObservationCompleteness.PARTIAL)
        record = _record()
        link = SubscriptionJobAssociation(
            record.subscription_id, 1, record.collection_key, 60, 1
        )
        scheduler, _, repo, _, key, _, _, _ = _runtime(
            collector=collector,
            records=(record,),
            links=(link,),
            random_value=0.0,
            reschedule=RescheduleConfiguration(300, 0.2, 0.1, 0.5),
        )
        candidate = DueCollectionJob(
            key, datetime(2026, 1, 1, tzinfo=UTC), 60, 1, "job"
        )
        task = asyncio.create_task(scheduler.run_due_job(candidate))
        await collector.started.wait()
        collector.finish.set()
        result = await task
        self.assertEqual(
            result.observation.completeness, ObservationCompleteness.PARTIAL
        )
        self.assertEqual(repo.next_due, [datetime(2026, 1, 1, 0, 0, 54, tzinfo=UTC)])

    async def test_failed_due_time_uses_backoff_and_cannot_hot_loop(self):
        collector = _Collector(fail=True)
        record = _record()
        link = SubscriptionJobAssociation(
            record.subscription_id, 1, record.collection_key, 60, 1
        )
        scheduler, _, repo, _, key, _, _, _ = _runtime(
            collector=collector,
            records=(record,),
            links=(link,),
            random_value=0.0,
            reschedule=RescheduleConfiguration(300, 0.2, 0.1, 0.5),
        )
        candidate = DueCollectionJob(
            key, datetime(2026, 1, 1, tzinfo=UTC), 60, 1, "job"
        )
        task = asyncio.create_task(scheduler.run_due_job(candidate))
        await collector.started.wait()
        collector.finish.set()
        result = await task
        self.assertTrue(result.failed)
        self.assertEqual(
            result.observation.completeness, ObservationCompleteness.FAILED
        )
        self.assertEqual(repo.next_due, [datetime(2026, 1, 1, 0, 2, 30, tzinfo=UTC)])
        self.assertGreater(repo.next_due[0], result.observation.collected_at)

    async def test_timeout_commits_failure_backoff_and_sqlite_keeps_old_cursor(self):
        with tempfile.TemporaryDirectory() as temporary:
            db = SQLiteDatabase(Path(temporary) / "scheduler.sqlite3")
            db.initialize()
            repository = SQLiteSchedulerRepository(db)
            subscriptions = SQLiteSubscriptionStore(db)
            job_links = SQLiteSubscriptionJobRepository(db)
            lifecycle = SQLiteSubscriptionLifecycleRepository(db)
            record = _record()
            association = SubscriptionJobAssociation(
                record.subscription_id, record.revision, record.collection_key, 60, 1
            )
            await lifecycle.apply(
                SubscriptionJobChange(
                    SubscriptionJobChangeKind.CREATE,
                    record,
                    association,
                    None,
                    None,
                ),
                initial_run=CollectionRunRequest(
                    record.collection_key,
                    datetime.now(UTC) - timedelta(seconds=1),
                    60,
                    1,
                    1,
                    1,
                ),
            )

            started_at = datetime.now(UTC)
            initial = Observation(
                "prior-observation",
                record.collection_key,
                1,
                started_at,
                started_at,
                ObservationCompleteness.COMPLETE,
                (),
                {"value": "preserved"},
            )
            initial_lease = await repository.claim_due(
                CollectionRunRequest(
                    record.collection_key,
                    started_at - timedelta(seconds=1),
                    60,
                    1,
                    1,
                    1,
                ),
                now=started_at,
            )
            self.assertIsNotNone(initial_lease)
            initial_eval = SubscriptionEvaluationCommit(
                record.subscription_id,
                record.revision,
                None,
                EvaluationState(1, {"state": "prior"}),
                ObservationCursor(
                    initial.observation_id,
                    initial.data_version,
                    initial.completeness,
                    initial.covered_ids,
                ),
            )
            initial_due = started_at + timedelta(seconds=1)
            self.assertTrue(
                await repository.commit_observation_with_evaluations(
                    initial_lease,
                    ObservationEvaluationCommit(initial, (initial_eval,)),
                    next_due_at=initial_due,
                )
            )

            clock = _MutableClock(initial_due)
            collector = _Collector()
            scheduler, _, _, _, key, _, _, _ = _runtime(
                collector=collector,
                records=(record,),
                subscriptions=subscriptions,
                repo=repository,
                job_links=job_links,
                now=initial_due,
                clock=clock,
                collection_timeout_seconds=0.02,
                random_value=0.5,
                reschedule=RescheduleConfiguration(300, 0.2, 0.1, 0.5),
            )
            due_page = await repository.list_due_jobs(now=initial_due, limit=10)
            self.assertEqual(len(due_page), 1)
            timed_out = asyncio.create_task(scheduler.run_due_job(due_page[0]))
            await asyncio.wait_for(collector.started.wait(), timeout=1)
            result = await timed_out
            self.assertIsNotNone(result)
            self.assertTrue(result.failed)
            self.assertTrue(result.committed)
            self.assertEqual(
                result.observation.completeness, ObservationCompleteness.FAILED
            )

            current = await repository.current_evaluation(record.subscription_id, key)
            self.assertEqual(current.observation, initial)
            self.assertEqual(current.cursor.observation_id, initial.observation_id)
            self.assertEqual(current.state, EvaluationState(1, {"state": "prior"}))
            async with db.unit_of_work() as unit:
                stored_failure = unit.execute(
                    "SELECT observation_id FROM b04_observations WHERE observation_id=?",
                    (result.observation.observation_id,),
                ).fetchone()
                job_row = unit.execute(
                    "SELECT observation_id,due_at,lease_token FROM b04_collection_jobs"
                ).fetchone()
            self.assertIsNotNone(stored_failure)
            self.assertEqual(job_row["observation_id"], initial.observation_id)
            self.assertGreater(datetime.fromisoformat(job_row["due_at"]), initial_due)
            self.assertIsNone(job_row["lease_token"])
            self.assertEqual(
                await repository.list_due_jobs(now=initial_due, limit=10), ()
            )
            self.assertIsNone(
                await repository.claim_due(
                    CollectionRunRequest(
                        key,
                        due_page[0].due_at,
                        due_page[0].cadence_seconds,
                        due_page[0].config_revision,
                        1,
                        1,
                    ),
                    now=initial_due,
                )
            )

            retry_at = datetime.fromisoformat(job_row["due_at"])
            recovered_page = await repository.list_due_jobs(now=retry_at, limit=10)
            self.assertEqual(len(recovered_page), 1)
            self.assertEqual(recovered_page[0].key, key)
            retry_snapshot = scheduler.registry.snapshot()
            retry_module = retry_snapshot.modules[key.module_id]
            retry_lease = await repository.claim_due(
                CollectionRunRequest(
                    key,
                    recovered_page[0].due_at,
                    recovered_page[0].cadence_seconds,
                    recovered_page[0].config_revision,
                    retry_module.epoch,
                    retry_snapshot.revision,
                ),
                now=retry_at,
            )
            self.assertIsNotNone(retry_lease)
            await repository.release(retry_lease)

    async def test_stale_registry_epoch_rejects_commit_and_releases_claim(self):
        record = _record()
        link = SubscriptionJobAssociation(
            record.subscription_id, 1, record.collection_key, 60, 1
        )
        scheduler, collector, repo, registry, key, _, _, _ = _runtime(
            records=(record,), links=(link,)
        )
        candidate = DueCollectionJob(
            key, datetime(2026, 1, 1, tzinfo=UTC), 60, 1, "job"
        )
        task = asyncio.create_task(scheduler.run_due_job(candidate))
        await collector.started.wait()
        operation = "scheduler-test-stop"
        identity = scheduler.lifecycle.quiesce("pkg/pricing", operation, "test_disable")
        scheduler.lifecycle.publish_committed_intent(
            "pkg/pricing", operation, identity, False, registry.snapshot().revision
        )
        await scheduler.lifecycle.stop_candidate("pkg/pricing", operation, 1.0)
        collector.finish.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(repo.commits)
        self.assertEqual(len(repo.releases), 1)

    async def test_local_cancellation_releases_lease_without_committing(self):
        record = _record()
        link = SubscriptionJobAssociation(
            record.subscription_id, 1, record.collection_key, 60, 1
        )
        scheduler, collector, repo, _, key, _, _, _ = _runtime(
            records=(record,), links=(link,)
        )
        candidate = DueCollectionJob(
            key, datetime(2026, 1, 1, tzinfo=UTC), 60, 1, "job"
        )
        task = asyncio.create_task(scheduler.run_due_job(candidate))
        await collector.started.wait()
        self.assertTrue(await scheduler.cancel_local(key))
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(repo.commits)
        self.assertEqual(len(repo.releases), 1)

    def test_digest_fall_back_occurrences_have_distinct_stable_utc_bounds(self):
        first_profile = _profile(fold=DstFoldPolicy.FIRST_OCCURRENCE)
        second_profile = _profile(fold=DstFoldPolicy.SECOND_OCCURRENCE, revision=2)
        first = digest_window_for_date(
            _record(mode="digest", profile=first_profile), date(2024, 11, 3)
        )
        second = digest_window_for_date(
            _record(mode="digest", profile=second_profile), date(2024, 11, 3)
        )
        self.assertEqual(first.due_at, datetime(2024, 11, 3, 5, 30, tzinfo=UTC))
        self.assertEqual(second.due_at, datetime(2024, 11, 3, 6, 30, tzinfo=UTC))
        self.assertNotEqual(first.window_id, second.window_id)
        self.assertEqual(
            first.window_id,
            digest_window_for_date(
                _record(mode="digest", profile=first_profile), date(2024, 11, 3)
            ).window_id,
        )

    def test_digest_spring_gap_skip_or_next_valid(self):
        skipped = digest_window_for_date(
            _record(
                mode="digest",
                profile=_profile(local_time="02:30", gap=DstGapPolicy.SKIP),
            ),
            date(2024, 3, 10),
        )
        next_valid = digest_window_for_date(
            _record(
                mode="digest",
                profile=_profile(
                    local_time="02:30", gap=DstGapPolicy.NEXT_VALID_INSTANT, revision=2
                ),
            ),
            date(2024, 3, 10),
        )
        self.assertIsNone(skipped)
        self.assertEqual(next_valid.due_at, datetime(2024, 3, 10, 7, 0, tzinfo=UTC))

    async def test_digest_page_rechecks_authority_and_reuses_deterministic_window(self):
        profile = _profile()
        record = _record(mode="digest", profile=profile)
        subscriptions = _SubscriptionStore((record,))
        from ygl_test_subject.api.subscriptions import ActiveDigestSchedule

        subscriptions.active_digest = [ActiveDigestSchedule(record)]
        windows = _Windows()
        scheduler, _, _, _, _, _, _, _ = _runtime(
            records=(record,), subscriptions=subscriptions, windows=windows
        )
        # Supply stable dates so the test does not depend on the wall clock.
        first = await scheduler.create_digest_windows_page(
            local_dates=(date(2026, 1, 1),)
        )
        second = await scheduler.create_digest_windows_page(
            local_dates=(date(2026, 1, 1),)
        )
        self.assertEqual(first.processed, 1)
        self.assertEqual(second.processed, 1)
        self.assertEqual(len(windows.rows), 1)
        self.assertEqual(
            windows.rows[next(iter(windows.rows))].utc_start,
            datetime(2026, 1, 1, 5, 30, tzinfo=UTC),
        )

    def test_digest_window_identity_is_shared_by_profile_and_recipient(self):
        profile = _profile()
        route = ConversationRef("test", ConversationKind.DIRECT, "alice", "route-alice")
        first = _record(
            subscription_id="sub-a",
            owner="alice",
            mode="digest",
            profile=profile,
            recipient=route,
        )
        second = _record(
            subscription_id="sub-b",
            owner="alice",
            mode="digest",
            profile=profile,
            recipient=route,
        )
        first_window = digest_window_for_date(first, date(2026, 1, 1))
        second_window = digest_window_for_date(second, date(2026, 1, 1))
        self.assertEqual(first_window.window_id, second_window.window_id)
        self.assertEqual(first_window.utc_start, second_window.utc_start)

    async def test_due_route_scan_recovers_before_paging_and_uses_returned_route(self):
        route = ConversationRef("test", ConversationKind.DIRECT, "alice", "route-alice")
        from ygl_test_subject.api.subscriptions import DigestRouteCandidate

        windows = _Windows()
        windows.routes = (
            DigestRouteCandidate(
                "window", route, datetime(2026, 1, 1, tzinfo=UTC), "route-key"
            ),
        )
        scheduler, _, _, _, _, _, windows, _ = _runtime(windows=windows)
        result = await scheduler.list_due_digest_routes()
        self.assertEqual(result, windows.routes)
        self.assertEqual(windows.recoveries[0][0], windows.recoveries[0][1])


if __name__ == "__main__":
    unittest.main()
