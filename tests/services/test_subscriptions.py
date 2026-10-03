"""B04-M command and matcher probes using real SQLite persistence."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.api.contexts import InvocationOrigin
from ygl_test_subject.api.display import DisplayDocument, Privacy, TextBlock
from ygl_test_subject.api.manifests import (
    CapabilityDescriptor,
    CapabilityEffect,
    CommandDescriptor,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
    PrivacyFloor,
)
from ygl_test_subject.api.services import (
    CapabilityHealth,
    Grant,
    GrantStatus,
    HealthReport,
    HealthStatus,
    ModuleHandlers,
    Principal,
    SubscriptionUnavailable,
)
from ygl_test_subject.api.storage import GrantReference, OwnerScope, OwnershipKind
from ygl_test_subject.api.subscriptions import (
    CollectionKey,
    ConversationKind,
    ConversationRef,
    DigestScheduleProfile,
    DigestWindow,
    DstFoldPolicy,
    DstGapPolicy,
    EvaluationDecision,
    NormalizedInput,
    Observation,
    ObservationCompleteness,
    ScheduleDescriptor,
    ScheduleTrigger,
    SubscriptionDescriptor,
    SubscriptionRecord,
    SubscriptionRequest,
    SubscriptionStatus,
    SubscriptionView,
)
from ygl_test_subject.api.version import CONTRACT_VERSION
from ygl_test_subject.core.context_issuer import ContextIssuer, InvalidInvocation
from ygl_test_subject.core.lifecycle import LifecycleController
from ygl_test_subject.core.ports import CollectionRunRequest, RevisionConflict
from ygl_test_subject.core.registry import Registry
from ygl_test_subject.infrastructure.sqlite import repositories_subscriptions
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories_auth import SQLiteGrantStore
from ygl_test_subject.infrastructure.sqlite.repositories_identity import (
    SQLiteIdentityRepository,
)
from ygl_test_subject.infrastructure.sqlite.repositories_subscriptions import (
    SQLiteDeliveryRepository,
    SQLiteDigestWindowRepository,
    SQLiteSchedulerRepository,
    SQLiteSubscriptionJobRepository,
    SQLiteSubscriptionLifecycleRepository,
    SQLiteSubscriptionStore,
)
from ygl_test_subject.services.identity import InvocationPrincipalResolver
from ygl_test_subject.services.owner_authority import OwnerRouteProofAuthority
from ygl_test_subject.services.subscriptions import (
    DigestWindowUnavailable,
    PrivateRecipientRequired,
    SubscriptionOperationError,
    SubscriptionOperationsService,
)

from tests.fixtures.b04_runtime import (
    _run_async_from_sync,
    initialize_subscription_gate_fixture,
    replace_subscription_gate_fixture,
    synthetic_subscription_gate_bindings,
)


class _Collector:
    def normalize(self, parameters):
        return NormalizedInput(parameters)

    async def collect(self, context, parameters, previous):  # pragma: no cover
        raise AssertionError("M must not recollect")


class _Handler:
    async def invoke(self, context, parameters):  # pragma: no cover
        raise AssertionError("not used")


class _SubscriptionModuleInstance:
    def __init__(self, handlers):
        self._handlers = handlers

    def handlers(self):
        return self._handlers

    async def start(self):
        return None

    async def stop(self):
        return None

    async def check_health(self):
        return HealthReport({"query": CapabilityHealth(HealthStatus.AVAILABLE)})


class _ThresholdEvaluator:
    def __init__(self, *, privacy: Privacy) -> None:
        self.privacy = privacy
        self.calls = 0

    def evaluate(self, subscription, observation, previous_state):
        self.calls += 1
        previous = 0
        if previous_state is not None and isinstance(previous_state.value, Mapping):
            previous = previous_state.value.get("last_version", 0)
        triggered = (
            observation.payload["score"] >= subscription.filters["threshold"]
            and observation.data_version > previous
        )
        version = max(previous, observation.data_version)
        document = (
            DisplayDocument(
                "Update",
                "A threshold was reached",
                (TextBlock("score changed"),),
                privacy=self.privacy,
            )
            if triggered
            else None
        )
        return EvaluationDecision(
            {"last_version": version},
            triggered,
            "shared-threshold" if triggered else None,
            observation.data_version if triggered else None,
            document,
        )


class _Resolver:
    def __init__(self, issuer: ContextIssuer) -> None:
        self.issuer = issuer
        self.overrides: dict[str, ConversationRef | None] = {}
        self.after_resolve = None

    async def resolve(self, invocation):
        self.issuer.require(invocation)
        if invocation.invocation_id in self.overrides:
            resolved = self.overrides[invocation.invocation_id]
        else:
            kind = (
                ConversationKind.GROUP
                if invocation.conversation_id.startswith("group-")
                else ConversationKind.DIRECT
            )
            resolved = ConversationRef(
                invocation.adapter_id,
                kind,
                invocation.conversation_id,
                f"route-{invocation.conversation_id}",
            )
        if self.after_resolve is not None:
            await self.after_resolve(invocation, resolved)
        return resolved


class _MutablePrincipalResolver:
    def __init__(self, principal_id: str) -> None:
        self.value = principal_id

    async def principal_id(self, invocation) -> str:
        return self.value


class _Cadence:
    def __init__(self, seconds: float = 30, revision: int = 1) -> None:
        self.seconds = seconds
        self.revision = revision

    def resolve(self, module_id, schedule):
        return self.seconds, self.revision


class _RepositoryClockMeta(type):
    def __instancecheck__(cls, instance):
        return isinstance(instance, datetime)


class _RepositoryClock(datetime, metaclass=_RepositoryClockMeta):
    """Fixed UTC clock used by SQLite's live-lease commit guard in tests."""

    @classmethod
    def now(cls, tz=None):
        current = datetime(2026, 9, 25, 12, tzinfo=UTC)
        return current if tz is None else current.astimezone(tz)


class _GrantRepository:
    def __init__(self) -> None:
        self.values: dict[str, Grant] = {}

    async def current_grant(self, grant_id):
        return self.values.get(grant_id)

    async def revoke_grant(self, grant, *, expected_revision):  # pragma: no cover
        self.values.pop(grant.grant_id, None)


def _schedule(collector_id: str, scope: OwnershipKind) -> ScheduleDescriptor:
    return ScheduleDescriptor(
        collector_id,
        1,
        "source",
        1,
        {
            "type": "object",
            "properties": {"region": {"type": "string"}},
            "required": ["region"],
        },
        scope,
        ScheduleTrigger.PERIODIC,
        30,
        60,
        f"{collector_id}_cadence",
    )


def _subscription(type_id: str, collector_id: str) -> SubscriptionDescriptor:
    return SubscriptionDescriptor(
        type_id,
        collector_id,
        f"{type_id}_matcher",
        {
            "type": "object",
            "properties": {"threshold": {"type": "number", "minimum": 0}},
            "required": ["threshold"],
        },
        ("instant", "digest"),
    )


class B04SubscriptionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        repository_clock = patch.object(
            repositories_subscriptions, "datetime", _RepositoryClock
        )
        repository_clock.start()
        self.addCleanup(repository_clock.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.now = datetime(2026, 9, 25, 12, tzinfo=UTC)
        self.eval_now = self.now
        self.db = SQLiteDatabase(Path(self.temp.name) / "runtime.sqlite3")
        self.db.initialize()
        self.identities = SQLiteIdentityRepository(self.db)
        await self.identities.save_principal(Principal("u1", "test-users", "u1"))
        await self.identities.save_principal(Principal("u2", "test-users", "u2"))
        await self.identities.save_principal(
            Principal("principal-u1", "test-users", "external-u1")
        )
        self.bindings = synthetic_subscription_gate_bindings(("sample/game",))
        await initialize_subscription_gate_fixture(self.db, self.bindings, self.now)
        self.store = SQLiteSubscriptionStore(self.db)
        self.lifecycle = SQLiteSubscriptionLifecycleRepository(
            self.db, subscription_gate_bindings=self.bindings
        )
        self.jobs = SQLiteSubscriptionJobRepository(self.db)
        self.scheduler = SQLiteSchedulerRepository(
            self.db, subscription_gate_bindings=self.bindings
        )
        self.windows = SQLiteDigestWindowRepository(self.db)
        self.delivery = SQLiteDeliveryRepository(self.db)
        self.issuer = ContextIssuer()
        self.resolver = _Resolver(self.issuer)
        self.grants = _GrantRepository()
        self.public_evaluator = _ThresholdEvaluator(privacy=Privacy.PUBLIC)
        self.private_evaluator = _ThresholdEvaluator(privacy=Privacy.PRIVATE)
        self.registry = Registry()
        manifest = ModuleManifest(
            "game",
            "game",
            ModuleCategory.GAME,
            "test.module:Factory",
            "0.1.0",
            (
                CapabilityDescriptor(
                    "query",
                    {"type": "object", "additionalProperties": False},
                    InvocationPolicy.COMMAND_ONLY,
                    CapabilityEffect.READ_ONLY,
                ),
            ),
            commands=(
                CommandDescriptor("subscribe", "query", {}, "Manage subscriptions"),
            ),
            schedules=(
                _schedule("public-data", OwnershipKind.PUBLIC),
                _schedule("authorized-data", OwnershipKind.AUTHORIZED),
                _schedule("user-data", OwnershipKind.USER),
            ),
            subscriptions=(
                _subscription("public-alert", "public-data"),
                _subscription("private-alert", "authorized-data"),
                _subscription("user-alert", "user-data"),
            ),
        )
        self.registry.register_package(
            PackageManifest(
                "sample",
                "0.1.0",
                CONTRACT_VERSION,
                (manifest,),
                "Tests",
                "AGPL-3.0",
                "local",
            ),
            {
                "game": ModuleHandlers(
                    capabilities={"query": _Handler()},
                    collectors={
                        "public-data": _Collector(),
                        "authorized-data": _Collector(),
                        "user-data": _Collector(),
                    },
                    evaluators={
                        "public-alert_matcher": self.public_evaluator,
                        "private-alert_matcher": self.private_evaluator,
                        "user-alert_matcher": self.private_evaluator,
                    },
                ),
            },
        )
        self.runtime_lifecycle = LifecycleController(self.registry, issuer=self.issuer)
        module = self.registry.snapshot().module("sample/game")
        instance = _SubscriptionModuleInstance(module.handlers)
        install_operation = "subscription-test-install"
        handlers = self.runtime_lifecycle.adopt_candidate(
            "sample", module.manifest, install_operation, instance
        )
        self.runtime_lifecycle.install_dormant(
            "sample", "sample/game", install_operation, instance, handlers
        )
        start_operation = "subscription-test-start"
        identity, _ = _run_async_from_sync(
            self.runtime_lifecycle.start_candidate("sample/game", start_operation)
        )
        self.runtime_lifecycle.publish_committed_intent(
            "sample/game",
            start_operation,
            identity,
            True,
            self.registry.snapshot().revision,
        )
        self.snapshot = self.registry.snapshot()
        self.module = self.snapshot.module("sample/game")
        self.principal_resolver = InvocationPrincipalResolver(
            self.issuer,
            self.identities,
            identity_namespace="test-users",
            admission=self.runtime_lifecycle.admission,
        )
        self.service = SubscriptionOperationsService(
            issuer=self.issuer,
            admission=self.runtime_lifecycle.admission,
            registry=self.registry,
            resolver=self.resolver,
            cadence=_Cadence(),
            subscriptions=self.store,
            lifecycle=self.lifecycle,
            jobs=self.jobs,
            scheduler=self.scheduler,
            digest_windows=self.windows,
            grants=self.grants,
            principal_resolver=self.principal_resolver,
            now=lambda: self.now,
            max_subscriptions_per_owner=8,
        )

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    def invocation(
        self,
        actor: str = "u1",
        conversation: str | None = None,
        *,
        origin: InvocationOrigin = InvocationOrigin.COMMAND,
        grant: GrantReference | None = None,
        issuer: ContextIssuer | None = None,
    ):
        issuer = issuer or self.issuer
        view = issuer.issue(
            origin=origin,
            module_id="sample/game",
            module_epoch=self.module.epoch,
            registry_revision=self.snapshot.revision,
            actor_id=actor,
            conversation_id=conversation or f"direct-{actor}",
            adapter_id="adapter",
            grant_id=None if grant is None else grant.grant_id,
            grant_revision=None if grant is None else grant.revision,
            capability_id="query",
        )
        if issuer is self.issuer:
            try:
                self.runtime_lifecycle.admission.admit(view, "query")
            except Exception:
                pass
        return view

    @staticmethod
    def grant_details(
        reference: GrantReference,
        *,
        principal_id="u1",
        module_id="sample/game",
        status=GrantStatus.ACTIVE,
        expires_at=None,
    ) -> Grant:
        return Grant(
            reference.grant_id,
            reference.revision,
            principal_id,
            module_id,
            "account-1",
            ("read",),
            None,
            status,
            expires_at,
        )

    def service_with_grants(self, grants):
        return SubscriptionOperationsService(
            issuer=self.issuer,
            admission=self.runtime_lifecycle.admission,
            registry=self.registry,
            resolver=self.resolver,
            cadence=_Cadence(),
            subscriptions=self.store,
            lifecycle=self.lifecycle,
            jobs=self.jobs,
            scheduler=self.scheduler,
            digest_windows=self.windows,
            grants=grants,
            principal_resolver=self.principal_resolver,
            now=lambda: self.now,
            max_subscriptions_per_owner=8,
        )

    async def add_sql_grant(
        self,
        grants: SQLiteGrantStore,
        grant_id: str,
        *,
        principal_id: str = "u1",
        module_id: str = "sample/game",
        expires_at: datetime | None = None,
        revoke: bool = False,
    ) -> GrantReference:
        created = await grants.create_grant(
            Grant(
                grant_id,
                1,
                principal_id,
                module_id,
                f"account-{grant_id}",
                ("read",),
                None,
                GrantStatus.ACTIVE,
                expires_at,
            ),
            expected_revision=0,
        )
        if revoke:
            created = await grants.revoke_grant(
                created, expected_revision=created.revision
            )
        return GrantReference(created.grant_id, created.revision)

    @staticmethod
    def request(
        type_id: str = "public-alert",
        *,
        mode: str = "instant",
        threshold: float = 5,
        region: str = "US",
        digest_schedule: DigestScheduleProfile | None = None,
        subscription_id: str | None = None,
        expected_revision: int | None = None,
    ) -> SubscriptionRequest:
        return SubscriptionRequest(
            type_id,
            {"region": region},
            {"threshold": threshold},
            mode,
            digest_schedule,
            subscription_id,
            expected_revision,
        )

    async def create(
        self,
        actor: str = "u1",
        *,
        type_id: str = "public-alert",
        mode: str = "instant",
        threshold: float = 5,
        conversation: str | None = None,
        grant: GrantReference | None = None,
        digest_schedule: DigestScheduleProfile | None = None,
    ):
        if grant is not None:
            self.grants.values[grant.grant_id] = self.grant_details(
                grant, principal_id=actor
            )
        return await self.service.create_request(
            self.invocation(actor, conversation, grant=grant),
            self.request(
                type_id,
                mode=mode,
                threshold=threshold,
                digest_schedule=digest_schedule,
            ),
        )

    async def _owner_service(self, actor="u1", conversation=None):
        owner_issuer = ContextIssuer()
        owner_registry = Registry()
        owner_module = replace(
            self.module.manifest,
            capabilities=(
                replace(
                    self.module.manifest.capabilities[0],
                    privacy_floor=PrivacyFloor.OWNER,
                ),
            ),
        )
        owner_package = PackageManifest(
            "sample",
            "0.1.0",
            CONTRACT_VERSION,
            (owner_module,),
            "Tests",
            "AGPL-3.0",
            "local",
        )
        owner_handlers = self.module.handlers
        owner_registry.register_package(owner_package, {"game": owner_handlers})
        owner_lifecycle = LifecycleController(owner_registry, issuer=owner_issuer)
        registered = owner_registry.snapshot().module("sample/game")
        instance = _SubscriptionModuleInstance(registered.handlers)
        install_id = "owner-subscription-install"
        handlers = owner_lifecycle.adopt_candidate(
            "sample", registered.manifest, install_id, instance
        )
        owner_lifecycle.install_dormant(
            "sample", "sample/game", install_id, instance, handlers
        )
        run_id = "owner-subscription-start"
        identity, _ = await owner_lifecycle.start_candidate("sample/game", run_id)
        owner_lifecycle.publish_committed_intent(
            "sample/game", run_id, identity, True, owner_registry.snapshot().revision
        )
        principals = _MutablePrincipalResolver(actor)
        routes = _Resolver(owner_issuer)
        authority = OwnerRouteProofAuthority(
            owner_issuer,
            owner_lifecycle.admission,
            principals,
            routes,
        )
        snapshot = owner_registry.snapshot()
        view = owner_issuer.issue(
            origin=InvocationOrigin.COMMAND,
            module_id="sample/game",
            module_epoch=snapshot.modules["sample/game"].epoch,
            registry_revision=snapshot.revision,
            actor_id=actor,
            conversation_id=conversation or f"direct-{actor}",
            adapter_id="adapter",
            capability_id="query",
        )
        lease = owner_lifecycle.admission.admit(view, "query")
        await authority.capture(view, lease)
        service = SubscriptionOperationsService(
            issuer=owner_issuer,
            admission=owner_lifecycle.admission,
            registry=owner_registry,
            resolver=routes,
            cadence=_Cadence(),
            subscriptions=self.store,
            lifecycle=self.lifecycle,
            jobs=self.jobs,
            scheduler=self.scheduler,
            digest_windows=self.windows,
            grants=self.grants,
            principal_resolver=principals,
            owner_authority=authority,
            now=lambda: self.now,
            max_subscriptions_per_owner=8,
        )
        return service, view, principals, authority

    async def record_for(self, view):
        return await self.store.current(view.subscription_id)

    async def observation_for(
        self,
        view,
        *,
        identity="obs",
        version=1,
        score=9,
        completeness=ObservationCompleteness.COMPLETE,
        covered=(),
    ):
        record = await self.record_for(view)
        return Observation(
            identity,
            record.collection_key,
            version,
            self.now,
            self.now,
            completeness,
            covered,
            {"score": score},
        )

    async def evaluate(self, observation):
        self.eval_now += timedelta(seconds=31)
        request = CollectionRunRequest(
            observation.key,
            self.eval_now - timedelta(seconds=1),
            30,
            1,
            self.module.epoch,
            self.snapshot.revision,
        )
        lease = await self.scheduler.claim_due(request, now=self.eval_now)
        self.assertIsNotNone(lease)
        try:
            commit = await self.service.prepare_evaluations(lease, observation)
            self.assertEqual(commit.observation, observation)
            return await self.scheduler.commit_observation_with_evaluations(
                lease, commit
            )
        finally:
            await self.scheduler.release(lease)

    async def test_paused_owner_can_list_revise_cancel_but_cannot_create(self):
        created = await self.create()
        await replace_subscription_gate_fixture(
            self.db, self.bindings, "sample/game", False, self.now
        )
        self.assertEqual(await self.service.list_current(self.invocation()), (created,))
        with self.assertRaises(SubscriptionOperationError):
            await self.create()
        revised = await self.service.revise_request(
            self.invocation(),
            self.request(
                threshold=7,
                subscription_id=created.subscription_id,
                expected_revision=1,
            ),
        )
        self.assertEqual(revised.revision, 2)
        await self.service.cancel(
            self.invocation(), created.subscription_id, expected_revision=2
        )
        self.assertEqual(await self.service.list_current(self.invocation()), ())

    async def test_old_checkpoint_gets_module_none_but_retains_real_cas_and_next_revision(
        self,
    ):
        created = await self.create()
        baseline = await self.observation_for(created, identity="baseline", score=0)
        self.assertTrue(await self.evaluate(baseline))
        await replace_subscription_gate_fixture(
            self.db, self.bindings, "sample/game", False, self.now
        )
        await replace_subscription_gate_fixture(
            self.db, self.bindings, "sample/game", True, self.now
        )
        previous_inputs = []

        def evaluator(subscription, observation, previous_state):
            previous_inputs.append(previous_state)
            return EvaluationDecision(
                {"baseline": observation.data_version}, False, None, None, None
            )

        for version, old_null in ((2, False), (3, True)):
            if old_null:
                await self.db.executor.run_transaction(
                    lambda u: u.execute(
                        "UPDATE b04_evaluation_states SET gate_revision=NULL,intent_revision=NULL"
                    ).rowcount,
                    begin_mode="IMMEDIATE",
                )
            old = await self.scheduler.current_checkpoint(
                created.subscription_id, baseline.key
            )
            observed = await self.observation_for(
                created, identity=f"fresh-{version}", version=version
            )
            with patch.object(self.public_evaluator, "evaluate", side_effect=evaluator):
                self.assertTrue(await self.evaluate(observed))
            self.assertIsNone(previous_inputs[-1])
            saved = await self.scheduler.current_checkpoint(
                created.subscription_id, baseline.key
            )
            self.assertEqual(
                saved.expected_state_revision, old.expected_state_revision + 1
            )
            self.assertEqual(saved.snapshot.state.value, {"baseline": version})
            self.assertEqual(saved.fence.gate_revision, 3)
        self.assertEqual(
            await self.delivery.list_due_events(now=self.now, limit=10), ()
        )

    async def test_pause_during_jobs_await_stops_evaluation_before_module_call(self):
        created = await self.create()
        observation = await self.observation_for(created)
        original = self.jobs.for_collection

        async def paused_read(key):
            links = await original(key)
            await replace_subscription_gate_fixture(
                self.db, self.bindings, "sample/game", False, self.now
            )
            return links

        with (
            patch.object(self.jobs, "for_collection", side_effect=paused_read),
            self.assertRaises(SubscriptionOperationError),
        ):
            await self.evaluate(observation)
        self.assertEqual(self.public_evaluator.calls, 0)
        snapshot = await self.scheduler.current_evaluation(
            created.subscription_id, observation.key
        )
        self.assertIsNone(snapshot.state)

    async def test_m01_command_create_revise_list_cancel_and_revision_cas(self):
        created = await self.create()
        self.assertEqual(created.revision, 1)
        listed = await self.service.list_current(self.invocation())
        self.assertEqual(listed, (created,))
        revised = await self.service.revise_request(
            self.invocation(),
            self.request(
                threshold=7,
                subscription_id=created.subscription_id,
                expected_revision=1,
            ),
        )
        self.assertEqual(revised.revision, 2)
        with self.assertRaises(RevisionConflict):
            await self.service.revise_request(
                self.invocation(),
                self.request(
                    threshold=8,
                    subscription_id=created.subscription_id,
                    expected_revision=1,
                ),
            )
        await self.service.cancel(
            self.invocation(), created.subscription_id, expected_revision=2
        )
        self.assertEqual(await self.service.list_current(self.invocation()), ())

    async def test_owner_management_lists_public_and_user_but_excludes_authorized(self):
        shared = await self.create(type_id="public-alert")
        personal = await self.create(type_id="user-alert")
        authorized = await self.create(
            type_id="private-alert", grant=GrantReference("owner-grant", 1)
        )
        service, view, _, _ = await self._owner_service()

        listed = await service.list_current(view)

        self.assertEqual(
            {item.subscription_id for item in listed},
            {shared.subscription_id, personal.subscription_id},
        )
        self.assertNotIn(
            authorized.subscription_id, {item.subscription_id for item in listed}
        )
        revised = await service.revise_request(
            view,
            self.request(
                threshold=8,
                subscription_id=shared.subscription_id,
                expected_revision=1,
            ),
        )
        self.assertEqual(revised.revision, 2)
        with self.assertRaises(RevisionConflict):
            await service.revise_request(
                view,
                self.request(
                    threshold=9,
                    subscription_id=shared.subscription_id,
                    expected_revision=1,
                ),
            )
        with self.assertRaises(SubscriptionOperationError):
            await service.cancel(view, authorized.subscription_id, expected_revision=1)

    async def test_owner_principal_drift_refuses_mutation(self):
        created = await self.create()
        service, view, principals, _ = await self._owner_service()
        principals.value = "principal-b"

        with self.assertRaises(SubscriptionOperationError):
            await service.cancel(view, created.subscription_id, expected_revision=1)

        self.assertEqual(
            (await self.store.current(created.subscription_id)).revision, 1
        )

    async def test_owner_cannot_cancel_foreign_or_cross_module_rows(self):
        route = ConversationRef(
            "adapter", ConversationKind.DIRECT, "direct-u1", "route-direct-u1"
        )
        rows = (
            SubscriptionRecord(
                "foreign-owner",
                1,
                "sample/game",
                CollectionKey(
                    "sample/game",
                    "public-data",
                    1,
                    "source",
                    NormalizedInput({"region": "US"}),
                    OwnerScope.public(),
                ),
                "u2",
                None,
                route,
                "instant",
                {"threshold": 5},
                SubscriptionStatus.ACTIVE,
            ),
            SubscriptionRecord(
                "cross-module",
                1,
                "other/game",
                CollectionKey(
                    "other/game",
                    "public-data",
                    1,
                    "source",
                    NormalizedInput({"region": "US"}),
                    OwnerScope.public(),
                ),
                "u1",
                None,
                route,
                "instant",
                {"threshold": 5},
                SubscriptionStatus.ACTIVE,
            ),
        )
        for row in rows:
            await self.store.create(row)
        service, view, _, _ = await self._owner_service()

        for row in rows:
            with self.subTest(subscription_id=row.subscription_id):
                with self.assertRaises(SubscriptionOperationError):
                    await service.cancel(view, row.subscription_id, expected_revision=1)
                self.assertEqual(await self.store.current(row.subscription_id), row)

    async def test_m01_concurrent_revision_race_reports_revision_conflict(self):
        created = await self.create()
        results = await asyncio.gather(
            self.service.revise_request(
                self.invocation(),
                self.request(
                    threshold=7,
                    subscription_id=created.subscription_id,
                    expected_revision=1,
                ),
            ),
            self.service.revise_request(
                self.invocation(),
                self.request(
                    threshold=8,
                    subscription_id=created.subscription_id,
                    expected_revision=1,
                ),
            ),
            return_exceptions=True,
        )

        self.assertEqual(sum(not isinstance(item, Exception) for item in results), 1)
        conflicts = [item for item in results if isinstance(item, RevisionConflict)]
        self.assertEqual(len(conflicts), 1)
        saved = await self.store.current(created.subscription_id)
        self.assertEqual(saved.revision, 2)

    async def test_m02_tool_nested_forged_and_other_issuer_cannot_write(self):
        request = self.request()
        for origin in (InvocationOrigin.LLM_TOOL,):
            with self.assertRaises(SubscriptionOperationError):
                await self.service.create_request(
                    self.invocation(origin=origin), request
                )
        nested = self.issuer.derive(
            self.invocation(), module_id="sample/game", module_epoch=self.module.epoch
        )
        with self.assertRaises(SubscriptionOperationError):
            await self.service.create_request(nested, request)
        forged = type(nested)(
            **{field: getattr(nested, field) for field in nested.__dataclass_fields__}
        )
        with self.assertRaises(InvalidInvocation):
            await self.service.create_request(forged, request)
        other = ContextIssuer()
        foreign = self.invocation(issuer=other)
        with self.assertRaises(InvalidInvocation):
            await self.service.create_request(foreign, request)
        self.assertEqual(await self.store.list_for_owner("u1", limit=10), ())

    async def test_m03_actor_conversation_ownership_and_m04_cross_user_are_denied(self):
        saved = await self.create()
        for invocation in (
            self.invocation("u2"),
            self.invocation("u1", "direct-other"),
        ):
            request = self.request(
                threshold=8,
                subscription_id=saved.subscription_id,
                expected_revision=1,
            )
            with self.assertRaises(SubscriptionOperationError):
                await self.service.revise_request(invocation, request)
            with self.assertRaises(SubscriptionOperationError):
                await self.service.cancel(
                    invocation, saved.subscription_id, expected_revision=1
                )
        self.assertEqual((await self.store.current(saved.subscription_id)).revision, 1)

    async def test_m05_authorized_subscription_requires_current_exact_grant(self):
        grant = GrantReference("grant-1", 2)
        with self.assertRaises(SubscriptionOperationError):
            await self.service.create_request(
                self.invocation(grant=grant), self.request("private-alert")
            )
        self.grants.values[grant.grant_id] = self.grant_details(grant)
        saved = await self.create(type_id="private-alert", grant=grant)
        self.assertEqual((await self.record_for(saved)).grant, grant)
        self.grants.values.pop(grant.grant_id)
        with self.assertRaises(SubscriptionOperationError):
            await self.service.revise_request(
                self.invocation(grant=grant),
                self.request(
                    "private-alert",
                    subscription_id=saved.subscription_id,
                    expected_revision=1,
                ),
            )

    async def test_sqlite_grants_reject_wrong_owner_module_expired_and_revoked(self):
        grants = SQLiteGrantStore(self.db)
        service = self.service_with_grants(grants)
        expired_at = min(self.now, datetime.now(UTC)) - timedelta(seconds=1)
        invalid = (
            (
                await self.add_sql_grant(
                    grants, "grant-wrong-owner", principal_id="u2"
                ),
                "u1",
            ),
            (
                await self.add_sql_grant(
                    grants, "grant-wrong-module", module_id="sample/other"
                ),
                "u1",
            ),
            (
                await self.add_sql_grant(
                    grants, "grant-expired", expires_at=expired_at
                ),
                "u1",
            ),
            (
                await self.add_sql_grant(grants, "grant-revoked", revoke=True),
                "u1",
            ),
        )
        for reference, actor in invalid:
            with self.subTest(grant_id=reference.grant_id):
                with self.assertRaises(SubscriptionOperationError):
                    await service.create_request(
                        self.invocation(actor, grant=reference),
                        self.request("private-alert"),
                    )
        self.assertEqual(await self.store.list_for_owner("u1", limit=10), ())
        self.assertEqual(await self.store.list_for_owner("u2", limit=10), ())

    async def test_revoked_sqlite_grant_blocks_revise_and_prepares_no_evaluation(self):
        grants = SQLiteGrantStore(self.db)
        service = self.service_with_grants(grants)
        reference = await self.add_sql_grant(grants, "grant-evaluation")
        saved = await service.create_request(
            self.invocation(grant=reference), self.request("private-alert")
        )
        current_grant = await grants.current_grant(reference.grant_id)
        revoked = await grants.revoke_grant(
            current_grant, expected_revision=current_grant.revision
        )
        revoked_ref = GrantReference(revoked.grant_id, revoked.revision)
        with self.assertRaises(SubscriptionOperationError):
            await service.revise_request(
                self.invocation(grant=revoked_ref),
                self.request(
                    "private-alert",
                    threshold=9,
                    subscription_id=saved.subscription_id,
                    expected_revision=1,
                ),
            )
        observation = await self.observation_for(saved)
        self.eval_now += timedelta(seconds=31)
        lease = await self.scheduler.claim_due(
            CollectionRunRequest(
                observation.key,
                self.eval_now - timedelta(seconds=1),
                30,
                1,
                self.module.epoch,
                self.snapshot.revision,
            ),
            now=self.eval_now,
        )
        self.assertIsNotNone(lease)
        try:
            commit = await service.prepare_evaluations(lease, observation)
        finally:
            await self.scheduler.release(lease)
        self.assertEqual(commit.subscriptions, ())
        snapshot = await self.scheduler.current_evaluation(
            saved.subscription_id, observation.key
        )
        self.assertIsNone(snapshot.state)
        self.assertIsNone(snapshot.cursor)
        self.assertEqual(self.private_evaluator.calls, 0)
        self.assertIsNone(
            await self.delivery.current_event(
                "shared-threshold",
                1,
                subscription_id=saved.subscription_id,
                subscription_revision=1,
            )
        )

    async def test_sqlite_grant_revoked_during_resolver_cannot_write_lifecycle(self):
        grants = SQLiteGrantStore(self.db)
        service = self.service_with_grants(grants)

        async def revoke(grant_id):
            current = await grants.current_grant(grant_id)
            await grants.revoke_grant(current, expected_revision=current.revision)

        create_ref = await self.add_sql_grant(grants, "grant-create-race")
        create_invocation = self.invocation(grant=create_ref)

        async def revoke_create(invocation, _resolved):
            if invocation.grant_id == create_ref.grant_id:
                self.resolver.after_resolve = None
                await revoke(create_ref.grant_id)

        self.resolver.after_resolve = revoke_create
        with self.assertRaises(SubscriptionOperationError):
            await service.create_request(
                create_invocation, self.request("private-alert")
            )
        self.assertEqual(await self.store.list_for_owner("u1", limit=10), ())
        self.assertEqual(
            await self.jobs.for_collection(
                CollectionKey(
                    "sample/game",
                    "authorized-data",
                    1,
                    "source",
                    NormalizedInput({"region": "US"}),
                    OwnerScope.authorized("u1", create_ref),
                )
            ),
            (),
        )

        revise_ref = await self.add_sql_grant(grants, "grant-revise-race")
        revised_target = await service.create_request(
            self.invocation(grant=revise_ref), self.request("private-alert")
        )
        revise_invocation = self.invocation(grant=revise_ref)
        revise_resolutions = 0

        async def revoke_during_revise(invocation, _resolved):
            nonlocal revise_resolutions
            if invocation.grant_id == revise_ref.grant_id:
                revise_resolutions += 1
                if revise_resolutions == 2:
                    self.resolver.after_resolve = None
                    await revoke(revise_ref.grant_id)

        self.resolver.after_resolve = revoke_during_revise
        with self.assertRaises(SubscriptionOperationError):
            await service.revise_request(
                revise_invocation,
                self.request(
                    "private-alert",
                    threshold=8,
                    subscription_id=revised_target.subscription_id,
                    expected_revision=1,
                ),
            )
        self.assertEqual(
            (await self.record_for(revised_target)).revision,
            1,
        )
        self.assertEqual(
            tuple(
                link.subscription_revision
                for link in await self.jobs.for_collection(
                    (await self.record_for(revised_target)).collection_key
                )
            ),
            (1,),
        )

        cancel_ref = await self.add_sql_grant(grants, "grant-cancel-race")
        cancelled_target = await service.create_request(
            self.invocation(grant=cancel_ref), self.request("private-alert")
        )
        cancel_invocation = self.invocation(grant=cancel_ref)

        async def revoke_during_cancel(invocation, _resolved):
            if invocation.grant_id == cancel_ref.grant_id:
                self.resolver.after_resolve = None
                await revoke(cancel_ref.grant_id)

        self.resolver.after_resolve = revoke_during_cancel
        with self.assertRaises(SubscriptionOperationError):
            await service.cancel(
                cancel_invocation,
                cancelled_target.subscription_id,
                expected_revision=1,
            )
        self.assertEqual(
            (await self.record_for(cancelled_target)).status,
            SubscriptionStatus.ACTIVE,
        )
        self.assertEqual(
            tuple(
                link.subscription_id
                for link in await self.jobs.for_collection(
                    (await self.record_for(cancelled_target)).collection_key
                )
            ),
            (cancelled_target.subscription_id,),
        )

    async def test_m06_invalid_type_filter_mode_timezone_and_window_are_rejected(self):
        with self.assertRaises(SubscriptionOperationError):
            await self.service.create_request(
                self.invocation(), self.request("undeclared-alert")
            )
        with self.assertRaises(SubscriptionOperationError):
            await self.service.create_request(
                self.invocation(), self.request(threshold="many")
            )
        with self.assertRaises(SubscriptionOperationError):
            await self.service.create_request(
                self.invocation(), self.request(mode="email")
            )
        with self.assertRaises(SubscriptionOperationError):
            await self.service.create_request(
                self.invocation(), self.request(mode="digest")
            )
        with self.assertRaises(ValueError):
            DigestScheduleProfile(
                "Not/AZone",
                "08:00",
                3600,
                DstFoldPolicy.FIRST_OCCURRENCE,
                DstGapPolicy.SKIP,
            )
        self.assertEqual(await self.store.list_for_owner("u1", limit=10), ())

    async def test_m07_shared_observation_is_matched_with_independent_user_thresholds(
        self,
    ):
        low = await self.create("u1", threshold=4)
        high = await self.create("u2", threshold=12)
        obs = await self.observation_for(low, score=8)
        self.assertEqual(
            (await self.record_for(low)).collection_key,
            (await self.record_for(high)).collection_key,
        )
        self.assertTrue(await self.evaluate(obs))
        low_state = await self.scheduler.current_evaluation(
            low.subscription_id, obs.key
        )
        high_state = await self.scheduler.current_evaluation(
            high.subscription_id, obs.key
        )
        self.assertEqual(low_state.state.value["last_version"], 1)
        self.assertEqual(high_state.state.value["last_version"], 1)
        self.assertIsNotNone(
            await self.delivery.current_event(
                "shared-threshold",
                1,
                subscription_id=low.subscription_id,
                subscription_revision=1,
            )
        )
        self.assertIsNone(
            await self.delivery.current_event(
                "shared-threshold",
                1,
                subscription_id=high.subscription_id,
                subscription_revision=1,
            )
        )

    async def test_m08_complete_and_partial_observations_save_exact_coverage_cursor(
        self,
    ):
        created = await self.create()
        partial = await self.observation_for(
            created,
            identity="partial",
            completeness=ObservationCompleteness.PARTIAL,
            covered=("id-1",),
        )
        self.assertTrue(await self.evaluate(partial))
        snapshot = await self.scheduler.current_evaluation(
            created.subscription_id, partial.key
        )
        self.assertEqual(snapshot.cursor.covered_ids, ("id-1",))
        complete = await self.observation_for(created, identity="complete", version=2)
        self.assertTrue(await self.evaluate(complete))
        snapshot = await self.scheduler.current_evaluation(
            created.subscription_id, complete.key
        )
        self.assertEqual(snapshot.cursor.completeness, ObservationCompleteness.COMPLETE)

    async def test_m09_failed_observation_preserves_previous_state_and_cursor(self):
        created = await self.create()
        first = await self.observation_for(created, identity="first")
        self.assertTrue(await self.evaluate(first))
        failed = await self.observation_for(
            created,
            identity="failed",
            version=2,
            completeness=ObservationCompleteness.FAILED,
        )
        self.assertTrue(await self.evaluate(failed))
        snapshot = await self.scheduler.current_evaluation(
            created.subscription_id, failed.key
        )
        self.assertEqual(snapshot.cursor.observation_id, "first")
        self.assertEqual(snapshot.state.revision, 1)

    async def test_m10_same_threshold_does_not_repeat_event_but_new_version_does(self):
        created = await self.create()
        first = await self.observation_for(created, identity="one", version=1)
        self.assertTrue(await self.evaluate(first))
        second = await self.observation_for(created, identity="two", version=1)
        self.assertTrue(await self.evaluate(second))
        self.assertEqual(self.public_evaluator.calls, 2)
        changed = await self.observation_for(created, identity="three", version=2)
        self.assertTrue(await self.evaluate(changed))
        self.assertIsNotNone(
            await self.delivery.current_event(
                "shared-threshold",
                2,
                subscription_id=created.subscription_id,
                subscription_revision=1,
            )
        )

    async def test_m11_evaluation_and_delivery_write_rollback_together(self):
        created = await self.create()
        record = await self.record_for(created)
        # A trigger forces failure after observation/evaluation work begins.
        connection = self.db.connect()
        try:
            connection.execute(
                "CREATE TRIGGER reject_b04_delivery BEFORE INSERT ON b04_delivery_events BEGIN SELECT RAISE(ABORT, 'blocked'); END"
            )
            connection.commit()
        finally:
            connection.close()
        obs = await self.observation_for(created)
        with self.assertRaises(Exception):
            await self.evaluate(obs)
        snapshot = await self.scheduler.current_evaluation(
            created.subscription_id, record.collection_key
        )
        self.assertIsNone(snapshot.state)
        self.assertIsNone(snapshot.cursor)
        self.assertIsNone(
            await self.delivery.current_event(
                "shared-threshold",
                1,
                subscription_id=created.subscription_id,
                subscription_revision=1,
            )
        )

    async def test_m12_revision_race_rejects_stale_evaluation_commit(self):
        created = await self.create()
        observation = await self.observation_for(created)
        original_commit = self.scheduler.commit_observation_with_evaluations

        async def revise_then_commit(lease, commit):
            await self.service.revise_request(
                self.invocation(),
                self.request(
                    threshold=6,
                    subscription_id=created.subscription_id,
                    expected_revision=1,
                ),
            )
            return await original_commit(lease, commit)

        self.scheduler.commit_observation_with_evaluations = revise_then_commit
        self.assertFalse(await self.evaluate(observation))
        self.scheduler.commit_observation_with_evaluations = original_commit
        snapshot = await self.scheduler.current_evaluation(
            created.subscription_id, observation.key
        )
        self.assertIsNone(snapshot.state)
        self.assertIsNone(snapshot.cursor)

        cancelled = await self.create("u2")
        cancel_observation = await self.observation_for(
            cancelled, identity="cancel-race", version=2
        )

        async def cancel_then_commit(lease, commit):
            await self.service.cancel(
                self.invocation("u2"),
                cancelled.subscription_id,
                expected_revision=1,
            )
            return await original_commit(lease, commit)

        self.scheduler.commit_observation_with_evaluations = cancel_then_commit
        self.assertFalse(await self.evaluate(cancel_observation))
        self.scheduler.commit_observation_with_evaluations = original_commit
        snapshot = await self.scheduler.current_evaluation(
            cancelled.subscription_id, cancel_observation.key
        )
        self.assertIsNone(snapshot.state)
        self.assertIsNone(snapshot.cursor)

    async def test_m13_cancelled_shared_subscriber_does_not_remove_other_job_link(self):
        cancelled = await self.create("u1")
        active = await self.create("u2")
        record = await self.record_for(cancelled)
        await self.service.cancel(
            self.invocation("u1"), cancelled.subscription_id, expected_revision=1
        )
        links = await self.jobs.for_collection(record.collection_key)
        self.assertEqual(
            tuple(item.subscription_id for item in links), (active.subscription_id,)
        )
        observation = await self.observation_for(active)
        self.assertTrue(await self.evaluate(observation))
        cancelled_snapshot = await self.scheduler.current_evaluation(
            cancelled.subscription_id, record.collection_key
        )
        self.assertIsNone(cancelled_snapshot.state)
        self.assertIsNotNone(
            await self.scheduler.current_evaluation(
                active.subscription_id, record.collection_key
            )
        )

    async def _digest_window(self, record, profile, *, window_id="window-1"):
        window = DigestWindow(
            window_id,
            profile.timezone_name,
            "2026-09-25",
            self.now - timedelta(hours=1),
            self.now + timedelta(hours=1),
            self.now + timedelta(hours=1),
            profile.fold_policy,
            profile.gap_policy,
            (),
            profile.policy_revision,
            profile,
            record.recipient,
        )
        await self.windows.create(window)

    @staticmethod
    def profile(timezone="UTC", local_time="13:00"):
        return DigestScheduleProfile(
            timezone,
            local_time,
            7200,
            DstFoldPolicy.FIRST_OCCURRENCE,
            DstGapPolicy.SKIP,
        )

    async def test_m14_digest_members_reuse_persisted_window_and_documents(self):
        profile = self.profile()
        first = await self.create("u1", mode="digest", digest_schedule=profile)
        second = await self.create(
            "u1", mode="digest", threshold=6, digest_schedule=profile
        )
        first_record = await self.record_for(first)
        await self._digest_window(first_record, profile)
        obs = await self.observation_for(first)
        self.assertTrue(await self.evaluate(obs))
        window = await self.windows.get("window-1")
        self.assertEqual(len(window.members), 2)
        self.assertEqual(
            {member.subscription_id for member in window.members},
            {first.subscription_id, second.subscription_id},
        )
        self.assertEqual(self.public_evaluator.calls, 2)
        for member in window.members:
            event = await self.delivery.current_event(
                member.event_key,
                member.event_version,
                subscription_id=member.subscription_id,
                subscription_revision=member.subscription_revision,
            )
            self.assertEqual(event.display_data.title, "Update")

    async def test_m15_digest_profiles_and_recipients_do_not_cross_windows(self):
        p1 = self.profile("UTC", "13:00")
        p2 = self.profile("Europe/Paris", "14:00")
        first = await self.create("u1", mode="digest", digest_schedule=p1)
        second = await self.create("u2", mode="digest", digest_schedule=p2)
        r1, r2 = await self.record_for(first), await self.record_for(second)
        await self._digest_window(r1, p1, window_id="window-utc")
        await self._digest_window(r2, p2, window_id="window-paris")
        self.assertTrue(await self.evaluate(await self.observation_for(first)))
        self.assertEqual(len((await self.windows.get("window-utc")).members), 1)
        self.assertEqual(len((await self.windows.get("window-paris")).members), 1)
        self.assertNotEqual(r1.recipient, r2.recipient)

    async def test_m16_group_authorized_instant_and_digest_fail_without_writes(self):
        grant = GrantReference("grant-group", 1)
        self.grants.values[grant.grant_id] = self.grant_details(grant)
        invocation = self.invocation("u1", "group-one", grant=grant)
        before = await self.store.list_for_owner("u1", limit=10)
        with self.assertRaises(PrivateRecipientRequired):
            await self.service.create_request(invocation, self.request("private-alert"))
        profile = self.profile()
        with self.assertRaises(PrivateRecipientRequired):
            await self.service.create_request(
                invocation,
                self.request("private-alert", mode="digest", digest_schedule=profile),
            )
        self.assertEqual(await self.store.list_for_owner("u1", limit=10), before)

    async def test_authorized_revise_rejects_same_id_direct_route_change(self):
        grant = GrantReference("grant-revise-route", 1)
        saved = await self.create(
            type_id="private-alert", grant=grant, conversation="direct-u1"
        )
        before = await self.record_for(saved)
        invocation = self.invocation("u1", "direct-u1", grant=grant)
        self.resolver.overrides[invocation.invocation_id] = ConversationRef(
            "adapter", ConversationKind.DIRECT, "direct-u1", "route-migrated"
        )
        with self.assertRaises(PrivateRecipientRequired):
            await self.service.revise_request(
                invocation,
                self.request(
                    "private-alert",
                    threshold=8,
                    subscription_id=saved.subscription_id,
                    expected_revision=1,
                ),
            )
        self.assertEqual(await self.record_for(saved), before)

    async def test_authorized_cancel_rejects_group_route_with_same_conversation_id(
        self,
    ):
        grant = GrantReference("grant-cancel-group", 1)
        saved = await self.create(
            type_id="private-alert", grant=grant, conversation="direct-u1"
        )
        before = await self.record_for(saved)
        invocation = self.invocation("u1", "direct-u1", grant=grant)
        self.resolver.overrides[invocation.invocation_id] = ConversationRef(
            "adapter", ConversationKind.GROUP, "direct-u1", "route-group"
        )
        with self.assertRaises(PrivateRecipientRequired):
            await self.service.cancel(
                invocation, saved.subscription_id, expected_revision=1
            )
        self.assertEqual(await self.record_for(saved), before)

    async def test_declared_but_unimplemented_notification_mode_is_rejected(self):
        registered = self.module.manifest
        weekly = replace(registered.subscriptions[0], notification_modes=("weekly",))
        manifest = replace(
            registered,
            schedules=(registered.schedules[0],),
            subscriptions=(weekly,),
        )
        registry = Registry()
        registry.register_package(
            PackageManifest(
                "sample",
                "0.1.0",
                CONTRACT_VERSION,
                (manifest,),
                "Tests",
                "AGPL-3.0",
                "local",
            ),
            {
                "game": ModuleHandlers(
                    capabilities={"query": _Handler()},
                    collectors={"public-data": _Collector()},
                    evaluators={"public-alert_matcher": self.public_evaluator},
                )
            },
        )
        alternate_lifecycle = LifecycleController(registry, issuer=self.issuer)
        alternate_module = registry.snapshot().module("sample/game")
        alternate_instance = _SubscriptionModuleInstance(alternate_module.handlers)
        alternate_install = "subscription-test-alternate-install"
        alternate_handlers = alternate_lifecycle.adopt_candidate(
            "sample", manifest, alternate_install, alternate_instance
        )
        alternate_lifecycle.install_dormant(
            "sample",
            "sample/game",
            alternate_install,
            alternate_instance,
            alternate_handlers,
        )
        alternate_start = "subscription-test-alternate-start"
        alternate_identity, _ = await alternate_lifecycle.start_candidate(
            "sample/game", alternate_start
        )
        alternate_lifecycle.publish_committed_intent(
            "sample/game",
            alternate_start,
            alternate_identity,
            True,
            registry.snapshot().revision,
        )
        module = registry.snapshot().module("sample/game")
        invocation = self.issuer.issue(
            origin=InvocationOrigin.COMMAND,
            module_id="sample/game",
            module_epoch=module.epoch,
            registry_revision=registry.snapshot().revision,
            actor_id="u1",
            conversation_id="direct-u1",
            adapter_id="adapter",
            capability_id="query",
        )
        alternate_lifecycle.admission.admit(invocation, "query")
        alternate_principal_resolver = InvocationPrincipalResolver(
            self.issuer,
            self.identities,
            identity_namespace="test-users",
            admission=alternate_lifecycle.admission,
        )
        service = SubscriptionOperationsService(
            issuer=self.issuer,
            admission=alternate_lifecycle.admission,
            registry=registry,
            resolver=self.resolver,
            cadence=_Cadence(),
            subscriptions=self.store,
            lifecycle=self.lifecycle,
            jobs=self.jobs,
            scheduler=self.scheduler,
            digest_windows=self.windows,
            grants=self.grants,
            principal_resolver=alternate_principal_resolver,
            now=lambda: self.now,
            max_subscriptions_per_owner=8,
        )
        with self.assertRaises(SubscriptionOperationError):
            await service.create_request(invocation, self.request(mode="weekly"))
        self.assertEqual(await self.store.list_for_owner("u1", limit=10), ())

    async def test_m17_missing_or_forged_conversation_route_fails_closed(self):
        grant = GrantReference("grant-route", 1)
        self.grants.values[grant.grant_id] = self.grant_details(grant)
        invocation = self.invocation(grant=grant)
        self.resolver.overrides[invocation.invocation_id] = None
        with self.assertRaises(PrivateRecipientRequired):
            await self.service.create_request(invocation, self.request("private-alert"))
        forged = self.invocation("u1", "direct-forged", grant=grant)
        self.resolver.overrides[forged.invocation_id] = ConversationRef(
            "adapter", ConversationKind.DIRECT, "other-direct", "route-forged"
        )
        with self.assertRaises(PrivateRecipientRequired):
            await self.service.create_request(forged, self.request("private-alert"))
        self.assertEqual(await self.store.list_for_owner("u1", limit=10), ())

    async def test_m18_public_group_subscription_is_allowed_without_authorized_authority(
        self,
    ):
        invocation = self.invocation("u1", "group-public")
        created = await self.service.create_request(invocation, self.request())
        record = await self.record_for(created)
        self.assertEqual(record.recipient.kind, ConversationKind.GROUP)
        self.assertIsNone(record.grant)

    async def test_principal_owner_and_first_run_survive_subscription_revision(self):
        invocation = self.invocation("external-u1", "direct-external-u1")
        created = await self.service.create_request(
            invocation, self.request("user-alert")
        )
        record = await self.record_for(created)
        self.assertEqual(record.owner_id, "principal-u1")
        self.assertEqual(record.collection_key.scope, OwnerScope.user("principal-u1"))

        due_jobs = await self.scheduler.list_due_jobs(now=self.now, limit=20)
        due = next(job for job in due_jobs if job.key == record.collection_key)
        self.assertEqual(due.due_at, self.now)
        link = await self.jobs.current_for_subscription(created.subscription_id)
        self.assertIsNotNone(link)
        lease = await self.scheduler.claim_due(
            CollectionRunRequest(
                record.collection_key,
                due.due_at,
                link.cadence_seconds,
                link.config_revision,
                self.module.epoch,
                self.registry.snapshot().revision,
            ),
            now=self.now,
        )
        self.assertIsNotNone(lease)

        revised = await self.service.revise_request(
            invocation,
            self.request(
                "user-alert",
                threshold=8,
                subscription_id=created.subscription_id,
                expected_revision=1,
            ),
        )
        self.assertEqual(revised.revision, 2)
        revised_record = await self.record_for(revised)
        self.assertEqual(revised_record.owner_id, "principal-u1")
        self.assertTrue(await self.scheduler.is_current(lease, now=self.now))
        listed = await self.service.list_current(invocation)
        self.assertEqual(
            tuple(item.subscription_id for item in listed),
            (created.subscription_id,),
        )

        grant = GrantReference("grant-external-u1", 1)
        self.grants.values[grant.grant_id] = self.grant_details(
            grant, principal_id="principal-u1"
        )
        authorized_invocation = self.invocation(
            "external-u1", "direct-external-u1", grant=grant
        )
        private = await self.service.create_request(
            authorized_invocation, self.request("private-alert")
        )
        private_record = await self.record_for(private)
        self.assertEqual(private_record.owner_id, "principal-u1")
        self.assertEqual(
            private_record.collection_key.scope,
            OwnerScope.authorized("principal-u1", grant),
        )

    async def test_matcher_must_remain_synchronous_and_failure_writes_nothing(self):
        created = await self.create()

        async def async_evaluate(subscription, observation, previous_state):
            return EvaluationDecision({"last_version": observation.data_version}, False)

        self.public_evaluator.evaluate = async_evaluate
        observation = await self.observation_for(created)
        request = CollectionRunRequest(
            observation.key,
            self.eval_now,
            30,
            1,
            self.module.epoch,
            self.snapshot.revision,
        )
        lease = await self.scheduler.claim_due(request, now=self.eval_now)
        self.assertIsNotNone(lease)
        try:
            with self.assertRaises(SubscriptionOperationError):
                await self.service.prepare_evaluations(lease, observation)
        finally:
            await self.scheduler.release(lease)
        snapshot = await self.scheduler.current_evaluation(
            created.subscription_id, observation.key
        )
        self.assertIsNone(snapshot.state)
        self.assertIsNone(snapshot.cursor)

    async def test_digest_trigger_without_persisted_window_does_not_advance_state(self):
        profile = self.profile()
        created = await self.create("u1", mode="digest", digest_schedule=profile)
        with self.assertRaises(DigestWindowUnavailable):
            await self.evaluate(await self.observation_for(created))
        snapshot = await self.scheduler.current_evaluation(
            created.subscription_id, (await self.record_for(created)).collection_key
        )
        self.assertIsNone(snapshot.state)
        self.assertIsNone(snapshot.cursor)

    async def test_legacy_b03_create_and_revise_are_fail_closed(self):
        view = SubscriptionView("sub", 1, "u1", None, "direct-u1", {})
        with self.assertRaises(SubscriptionUnavailable):
            await self.service.create(self.invocation(), view)
        with self.assertRaises(SubscriptionUnavailable):
            await self.service.revise(self.invocation(), view)
        self.assertEqual(await self.store.list_for_owner("u1", limit=10), ())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
