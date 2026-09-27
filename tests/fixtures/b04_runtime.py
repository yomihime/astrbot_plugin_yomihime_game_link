"""Real SQLite B04 fixture; all source, resolver and message adapters are fakes."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Callable

from ygl_test_subject.api.contexts import InvocationOrigin, InvocationView
from ygl_test_subject.api.display import (
    DisplayDocument,
    DisplayLimits,
    Privacy,
    TextBlock,
)
from ygl_test_subject.api.manifests import (
    CapabilityDescriptor,
    CapabilityEffect,
    CommandDescriptor,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
    SourceDeclaration,
    ToolDescriptor,
)
from ygl_test_subject.api.results import CapabilityResult, ResultStatus
from ygl_test_subject.api.services import (
    CapabilityHealth,
    ConversationKind,
    ConversationRef,
    Grant,
    HealthReport,
    HealthStatus,
    ModuleHandlers,
)
from ygl_test_subject.api.storage import OwnershipKind
from ygl_test_subject.api.subscriptions import (
    CadenceConfiguration,
    CollectionView,
    EvaluationDecision,
    EvaluationState,
    NormalizedInput,
    Observation,
    ObservationCompleteness,
    ScheduleDescriptor,
    ScheduleTrigger,
    SubscriptionDescriptor,
    SubscriptionView,
)
from ygl_test_subject.api.version import CONTRACT_VERSION
from ygl_test_subject.core.admission import AdmissionController
from ygl_test_subject.core.context_issuer import ContextIssuer
from ygl_test_subject.core.lifecycle import LifecycleController
from ygl_test_subject.core.ports import (
    MessagePort,
    MessageReceipt,
    MessageStatus,
    MessageTarget,
    RenderedMessage,
)
from ygl_test_subject.core.registry import Registry
from ygl_test_subject.infrastructure.files import LocalSafeFileStore
from ygl_test_subject.infrastructure.http import HttpTransport, TransportRequest
from ygl_test_subject.infrastructure.secret_store import SQLiteSecretStore
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories import SQLiteRepositories
from ygl_test_subject.infrastructure.sqlite.repositories_output import (
    SQLiteRootOutputRepository,
)
from ygl_test_subject.presentation.rendering import (
    GenericDisplayRenderer,
    RenderingBounds,
)
from ygl_test_subject.services.b04_runtime import (
    B04Repositories,
    SQLiteResourceVisibilityProbe,
)
from ygl_test_subject.services.delivery import DeliveryService
from ygl_test_subject.services.identity import InvocationPrincipalResolver
from ygl_test_subject.services.module_services import (
    ModuleServicesFactory,
    RegistryRegistrationLookup,
)
from ygl_test_subject.services.output import (
    LifecycleApprovedSendScheduler,
    OutputService,
)
from ygl_test_subject.services.scheduler import (
    ExecutionClaimProofRegistry,
    RescheduleConfiguration,
    SchedulerQuotas,
    SharedCollectionScheduler,
)
from ygl_test_subject.services.subscriptions import SubscriptionOperationsService


class DeterministicClock:
    """Mutable, explicit UTC test clock; no wall-clock sleeps are used."""

    def __init__(self, current: datetime | None = None) -> None:
        self.current = current or datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


class _TestCodec:
    def encrypt(self, value: bytes) -> bytes:
        return b"b04-test:" + value[::-1]

    def decrypt(self, value: bytes) -> bytes:
        prefix = b"b04-test:"
        if not value.startswith(prefix):
            raise ValueError("invalid test envelope")
        return value[len(prefix) :][::-1]


class OfflineHttpTransport(HttpTransport):
    """Test double: records requests and never opens a network connection."""

    def __init__(self) -> None:
        self.requests: list[TransportRequest] = []

    async def request(self, request: TransportRequest):
        self.requests.append(request)
        from ygl_test_subject.api.services import HttpResponse

        return HttpResponse(200, {"content-type": "text/plain"}, b"test")


class RecordingMessagePort(MessagePort):
    """Test double for the host boundary; outcomes are controlled per call."""

    def __init__(self) -> None:
        self.calls: list[tuple[MessageTarget, RenderedMessage]] = []
        self.outcomes: list[MessageStatus] = []
        self.block = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def send(self, target: MessageTarget, payload: RenderedMessage):
        self.calls.append((target, payload))
        if self.block:
            self.started.set()
            await self.release.wait()
        status = self.outcomes.pop(0) if self.outcomes else MessageStatus.ACCEPTED
        return MessageReceipt(status, f"test-message-{len(self.calls)}")


class TrustedConversationResolver:
    """Test host resolver backed by persisted conversation references."""

    def __init__(self, issuer: ContextIssuer, conversations) -> None:
        self.issuer = issuer
        self.conversations = conversations

    async def resolve(self, invocation: InvocationView) -> ConversationRef | None:
        self.issuer.require(invocation)
        if invocation.adapter_id is None or invocation.conversation_id is None:
            return None
        result = await self.conversations.current(
            invocation.adapter_id, invocation.conversation_id
        )
        if (
            result is None
            or result.adapter_id != invocation.adapter_id
            or result.conversation_id != invocation.conversation_id
        ):
            return None
        return result


class PersistedRouteResolver:
    """Test host route adapter; private routes require a persisted DIRECT ref."""

    def __init__(self, conversations) -> None:
        self.conversations = conversations

    async def resolve_current(
        self, owner_id: str, persisted_recipient: ConversationRef
    ):
        del owner_id
        current = await self.conversations.current(
            persisted_recipient.adapter_id, persisted_recipient.conversation_id
        )
        if current != persisted_recipient:
            return None
        return current

    async def resolve_private(
        self, owner_id: str, persisted_recipient: ConversationRef
    ):
        current = await self.resolve_current(owner_id, persisted_recipient)
        return (
            current
            if current is not None and current.kind is ConversationKind.DIRECT
            else None
        )


class RenderBatchBarrier:
    """Controlled renderer wrapper for digest-member cancellation races."""

    def __init__(self) -> None:
        self.delegate = GenericDisplayRenderer(
            RenderingBounds(2000, 100, 100, 100, 1024 * 1024, 2048, 8, 64, 100)
        )
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def render(self, document, *, limits, audience):
        return await self.delegate.render(document, limits=limits, audience=audience)

    async def render_batch(self, batch, limits):
        self.started.set()
        await self.release.wait()
        return await self.delegate.render_batch(batch, limits)


class _FakeCollector:
    """Test-only collector with a deterministic observation queue."""

    def __init__(self, clock: DeterministicClock) -> None:
        self.clock = clock
        self.calls = 0
        self.next_completeness = ObservationCompleteness.COMPLETE
        self.block = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    def normalize(self, parameters):
        return NormalizedInput(parameters)

    async def collect(self, context: CollectionView, parameters, previous):
        self.calls += 1
        if self.block:
            self.started.set()
            await self.release.wait()
        completeness = self.next_completeness
        return Observation(
            f"observation-{self.calls}",
            context.key,
            self.calls,
            self.clock(),
            self.clock(),
            completeness,
            ("item-a",) if completeness is ObservationCompleteness.PARTIAL else (),
            {"value": 10, "source": "test-source"},
        )


class _FakeMatcher:
    """Test-only pure matcher; each user's threshold is evaluated separately."""

    def evaluate(
        self,
        subscription: SubscriptionView,
        observation: Observation,
        previous_state: EvaluationState | None,
    ):
        threshold = subscription.filters.get("minimum", 0)
        triggered = observation.payload.get("value", 0) >= threshold
        return EvaluationDecision(
            {"last": observation.observation_id},
            triggered,
            f"event-{observation.observation_id}-{subscription.subscription_id}"
            if triggered
            else None,
            observation.data_version if triggered else None,
            DisplayDocument(
                "Test feed",
                "Fixture result",
                (
                    TextBlock(
                        f"{subscription.subscription_id}:{observation.payload.get('value')}"
                    ),
                ),
                privacy=Privacy.PUBLIC
                if observation.key.scope.kind is OwnershipKind.PUBLIC
                else Privacy.PRIVATE,
            )
            if triggered
            else None,
        )


class _Handler:
    async def invoke(self, context, parameters):
        return CapabilityResult(
            "test",
            ResultStatus.SUCCESS,
            document=DisplayDocument("Test", "Fixture", (TextBlock("ok"),)),
        )


class _FixtureModule:
    """A real Lifecycle-owned fixture instance for integration coverage."""

    def __init__(self, handlers: ModuleHandlers) -> None:
        self._handlers = handlers

    def handlers(self) -> ModuleHandlers:
        return self._handlers

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def check_health(self) -> HealthReport:
        return HealthReport(
            {
                "read": CapabilityHealth(HealthStatus.AVAILABLE),
                "manage": CapabilityHealth(HealthStatus.AVAILABLE),
            }
        )


@dataclass(slots=True)
class B04Runtime:
    root: Path
    database: SQLiteDatabase
    registry: Registry
    issuer: ContextIssuer
    lifecycle: LifecycleController
    lookup: RegistryRegistrationLookup
    host_repositories: SQLiteRepositories
    repositories: B04Repositories
    module_factory: ModuleServicesFactory
    operations: SubscriptionOperationsService
    scheduler: SharedCollectionScheduler
    delivery: DeliveryService
    output: OutputService
    message_port: RecordingMessagePort
    resource_visibility: SQLiteResourceVisibilityProbe
    renderer: object
    collector: _FakeCollector
    clock: DeterministicClock
    resolver: TrustedConversationResolver

    def invocation(
        self,
        actor: str,
        conversation_id: str,
        *,
        origin=InvocationOrigin.COMMAND,
        grant: Grant | None = None,
        capability: str | None = None,
    ) -> InvocationView:
        module = self.registry.snapshot().module("sample/feed")
        capability_id = capability or (
            "read" if origin is InvocationOrigin.LLM_TOOL else "manage"
        )
        view = self.issuer.issue(
            origin=origin,
            module_id=module.module_id,
            module_epoch=module.epoch,
            registry_revision=self.registry.snapshot().revision,
            actor_id=actor,
            conversation_id=conversation_id,
            adapter_id="test-adapter",
            capability_id=capability_id,
            grant_id=None if grant is None else grant.grant_id,
            grant_revision=None if grant is None else grant.revision,
        )
        if origin in (InvocationOrigin.COMMAND, InvocationOrigin.LLM_TOOL):
            self.lifecycle.admission.admit(view, capability_id)
        return view


def _run_async_from_sync(coro):
    """Run fixture startup when unittest has already entered an event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    result = []
    errors = []

    def run():
        try:
            result.append(asyncio.run(coro))
        except BaseException as exc:  # propagate startup failures to the test
            errors.append(exc)

    thread = threading.Thread(target=run, name="b04-fixture-startup")
    thread.start()
    thread.join()
    if errors:
        raise errors[0]
    return result[0]


def build_runtime(
    root: str | Path,
    *,
    clock: DeterministicClock | None = None,
    renderer: object | None = None,
    resource_visibility_probe: SQLiteResourceVisibilityProbe | None = None,
    retry_at: Callable | None = None,
    send_timeout: float = 1.0,
) -> B04Runtime:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    clock = clock or DeterministicClock()
    schedule = ScheduleDescriptor(
        "catalog",
        1,
        "feed",
        1,
        {
            "type": "object",
            "properties": {"region": {"type": "string"}},
            "required": ["region"],
            "additionalProperties": False,
        },
        OwnershipKind.PUBLIC,
        ScheduleTrigger.PERIODIC,
        60.0,
        60.0,
        "cadence",
    )
    subscription = SubscriptionDescriptor(
        "feed-alert",
        "catalog",
        "feed-matcher",
        {
            "type": "object",
            "properties": {"minimum": {"type": "integer", "minimum": 0}},
            "required": ["minimum"],
            "additionalProperties": False,
        },
        ("instant", "digest"),
    )
    private_schedule = ScheduleDescriptor(
        "private-catalog",
        1,
        "feed",
        1,
        {
            "type": "object",
            "properties": {"region": {"type": "string"}},
            "required": ["region"],
            "additionalProperties": False,
        },
        OwnershipKind.AUTHORIZED,
        ScheduleTrigger.PERIODIC,
        60.0,
        60.0,
        "cadence",
    )
    private_subscription = SubscriptionDescriptor(
        "private-alert",
        "private-catalog",
        "feed-matcher",
        {
            "type": "object",
            "properties": {"minimum": {"type": "integer", "minimum": 0}},
            "required": ["minimum"],
            "additionalProperties": False,
        },
        ("instant", "digest"),
    )
    user_schedule = ScheduleDescriptor(
        "user-catalog",
        1,
        "feed",
        1,
        {
            "type": "object",
            "properties": {"region": {"type": "string"}},
            "required": ["region"],
            "additionalProperties": False,
        },
        OwnershipKind.USER,
        ScheduleTrigger.PERIODIC,
        60.0,
        60.0,
        "cadence",
    )
    user_subscription = SubscriptionDescriptor(
        "user-alert",
        "user-catalog",
        "feed-matcher",
        {
            "type": "object",
            "properties": {"minimum": {"type": "integer", "minimum": 0}},
            "required": ["minimum"],
            "additionalProperties": False,
        },
        ("instant", "digest"),
    )
    capability = CapabilityDescriptor(
        "read",
        {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        InvocationPolicy.NATURAL_LANGUAGE_ALLOWED,
        CapabilityEffect.READ_ONLY,
    )
    manage_capability = CapabilityDescriptor(
        "manage",
        {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        InvocationPolicy.COMMAND_ONLY,
        CapabilityEffect.READ_ONLY,
    )
    tool = ToolDescriptor("feed_read", "read", {}, "Test public feed")
    manifest = ModuleManifest(
        "feed",
        "feed",
        ModuleCategory.GAME,
        "tests.fixtures.b04_runtime:build_runtime",
        "1.0.0",
        (capability, manage_capability),
        commands=(
            CommandDescriptor(
                "manage_subscriptions", "manage", {}, "Manage subscriptions"
            ),
        ),
        tools=(tool,),
        schedules=(schedule, private_schedule, user_schedule),
        subscriptions=(subscription, private_subscription, user_subscription),
        sources=(SourceDeclaration("feed", "example.test", requests_per_minute=60),),
    )
    package = PackageManifest(
        "sample", "1.0.0", CONTRACT_VERSION, (manifest,), "tests", "MIT", "fixture"
    )
    registry = Registry()
    collector = _FakeCollector(clock)
    registry.register_package(
        package,
        {
            "feed": ModuleHandlers(
                {"read": _Handler(), "manage": _Handler()},
                {
                    "catalog": collector,
                    "private-catalog": collector,
                    "user-catalog": collector,
                },
                {"feed-matcher": _FakeMatcher()},
            )
        },
    )
    issuer = ContextIssuer()
    proofs = ExecutionClaimProofRegistry(now=clock)
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
        execution_claim_prover=proofs,
        utc_clock=clock,
    )
    lifecycle = LifecycleController(registry, issuer=issuer, admission=admission)
    lifecycle_ref["controller"] = lifecycle
    module = registry.snapshot().module("sample/feed")
    instance = _FixtureModule(module.handlers)
    operation_id = "b04-fixture-install"
    handlers = lifecycle.adopt_candidate(
        "sample", module.manifest, operation_id, instance
    )
    lifecycle.install_dormant("sample", "sample/feed", operation_id, instance, handlers)
    operation_id = "b04-fixture-start"
    identity, _ = _run_async_from_sync(
        lifecycle.start_candidate("sample/feed", operation_id)
    )
    lifecycle.publish_committed_intent(
        "sample/feed",
        operation_id,
        identity,
        True,
        registry.snapshot().revision,
    )
    lookup = RegistryRegistrationLookup(registry)
    database = SQLiteDatabase(root / "runtime.sqlite3")
    files = LocalSafeFileStore(root / "assets")
    secrets = SQLiteSecretStore(database, root / "secrets", codec=_TestCodec())
    host_repositories = SQLiteRepositories(
        database, lookup, file_store=files, secret_store=secrets
    )
    database.initialize()
    connection = database.connect()
    try:
        for principal_id, identity_namespace, external_user_id in (
            ("principal-alice", "b04-tests", "alice"),
            ("principal-bob", "b04-tests", "bob"),
        ):
            existing = connection.execute(
                "SELECT principal_id FROM principals "
                "WHERE identity_namespace=? AND external_user_id=?",
                (identity_namespace, external_user_id),
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO principals"
                    "(principal_id,identity_namespace,external_user_id) "
                    "VALUES(?,?,?)",
                    (principal_id, identity_namespace, external_user_id),
                )
            elif existing["principal_id"] != principal_id:
                raise RuntimeError(
                    "B04 fixture identity mapping conflicts with persisted data"
                )
        connection.commit()
    finally:
        connection.close()
    principal_resolver = InvocationPrincipalResolver(
        issuer,
        host_repositories.identities,
        identity_namespace="b04-tests",
        admission=lifecycle.admission,
    )
    b04 = B04Repositories(database)
    resolver = TrustedConversationResolver(issuer, host_repositories.conversations)
    routes = PersistedRouteResolver(host_repositories.conversations)
    cadence = CadenceConfiguration((60.0,))
    operations = SubscriptionOperationsService(
        issuer=issuer,
        admission=lifecycle.admission,
        registry=registry,
        resolver=resolver,
        cadence=type(
            "TestCadence", (), {"resolve": lambda self, _module, _schedule: (60.0, 1)}
        )(),
        subscriptions=b04.subscriptions,
        lifecycle=b04.lifecycle,
        jobs=b04.jobs,
        scheduler=b04.scheduler,
        digest_windows=b04.windows,
        grants=host_repositories.authorization,
        principal_resolver=principal_resolver,
        now=clock,
        max_subscriptions_per_owner=100,
    )

    module_factory = ModuleServicesFactory(
        registry,
        issuer,
        lifecycle,
        host_repositories,
        OfflineHttpTransport(),
        config_principal_id="host-config",
        identity_namespace="b04-tests",
        subscriptions=operations,
        principal_resolver=principal_resolver,
        utc_clock=clock,
    )

    def binder_factory(module_id):
        return module_factory.for_module(module_id).scopes

    scheduler = SharedCollectionScheduler(
        registry=registry,
        issuer=issuer,
        lifecycle=lifecycle,
        admission=lifecycle.admission,
        execution_claim_proofs=proofs,
        binder_for=binder_factory,
        repository=b04.scheduler,
        subscriptions=b04.subscriptions,
        job_links=b04.jobs,
        digest_windows=b04.windows,
        grant_store=host_repositories.authorization,
        routes=routes,
        evaluation_preparer=operations,
        cadence=cadence,
        reschedule=RescheduleConfiguration(120.0, 0.0, 0.0, 0.0),
        quotas=SchedulerQuotas(4, 2, 2, 20, 10.0, 1.0, 30),
        now=clock,
        random_source=lambda: 0.5,
        monotonic_clock=monotonic,
    )
    renderer = renderer or GenericDisplayRenderer(
        RenderingBounds(2000, 100, 100, 100, 1024 * 1024, 2048, 8, 64, 100)
    )
    message_port = RecordingMessagePort()
    send_scheduler = LifecycleApprovedSendScheduler(lifecycle)
    delivery = DeliveryService(
        deliveries=b04.deliveries,
        subscriptions=b04.subscriptions,
        grants=host_repositories.authorization,
        windows=b04.windows,
        registry=registry,
        admission=lifecycle.admission,
        send_scheduler=send_scheduler,
        routes=routes,
        renderer=renderer,
        message_port=message_port,
        limits=DisplayLimits(4, 1024 * 1024),
        now=clock,
        send_timeout=send_timeout,
        retry_at=retry_at,
        page_size=20,
    )
    resource_visibility = resource_visibility_probe or SQLiteResourceVisibilityProbe(
        database
    )
    output = OutputService(
        issuer=issuer,
        admission=lifecycle.admission,
        send_scheduler=send_scheduler,
        registry=registry,
        renderer=renderer,
        limits=DisplayLimits(4, 1024 * 1024),
        conversations=resolver,
        message_port=message_port,
        deliveries=b04.deliveries,
        grants=host_repositories.authorization,
        subscriptions=b04.subscriptions,
        root_outputs=SQLiteRootOutputRepository(database),
        resource_visibility=resource_visibility,
        principal_resolver=principal_resolver,
        claim_lease=timedelta(seconds=30),
        send_timeout=1.0,
        now=clock,
    )
    return B04Runtime(
        root,
        database,
        registry,
        issuer,
        lifecycle,
        lookup,
        host_repositories,
        b04,
        module_factory,
        operations,
        scheduler,
        delivery,
        output,
        message_port,
        resource_visibility,
        renderer,
        collector,
        clock,
        resolver,
    )


__all__ = [
    "B04Runtime",
    "DeterministicClock",
    "OfflineHttpTransport",
    "RecordingMessagePort",
    "SQLiteResourceVisibilityProbe",
    "build_runtime",
]
