"""Durable collection and digest-window scheduling for B04.

The database ports own leases, jobs, windows, and due-scan cursors. This module
only keeps short-lived task handles so concurrent local callers can await the
same collection future; it never uses those handles as persisted job state.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from math import isfinite
from time import monotonic
from typing import Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from yomihime_game_link_sdk.contexts import InvocationOrigin, InvocationView
from yomihime_game_link_sdk.declarations import SourceDeclaration
from yomihime_game_link_sdk.services import InvocationServiceBinder
from yomihime_game_link_sdk.storage import (
    GrantReference,
    JsonObject,
    OwnerScope,
    OwnershipKind,
)
from yomihime_game_link_sdk.subscriptions import (
    CollectionKey,
    CollectionView,
    ConversationKind,
    ConversationRef,
    DigestScheduleProfile,
    IntervalLimits,
    NormalizedInput,
    Observation,
    ObservationCompleteness,
    ScheduleDescriptor,
    ScheduleTrigger,
)

from ..core.collection_context import require_collection_context
from ..core.context_issuer import ContextIssuer
from ..core.contracts.services import Grant, GrantStatus
from ..core.contracts.subscriptions import (
    ActiveDigestSchedule,
    CadenceConfiguration,
    DigestRouteCandidate,
    DigestRouteCursor,
    DigestWindow,
    DueCollectionJob,
    DueJobCursor,
    ObservationEvaluationCommit,
    SubscriptionRecord,
    SubscriptionStatus,
)
from ..core.contracts.validation import _validate as _validate_schema
from ..core.contracts.validation_boundary import validate_contract
from ..core.ports import (
    AdmissionPort,
    CollectionRunRequest,
    DigestWindowRepository,
    ExecutionLease,
    GrantStore,
    SchedulerRepository,
    SubscriptionJobRepository,
    SubscriptionStore,
)
from ..core.registry import RegisteredModule, Registry, RegistrySnapshot
from ..core.task_scope import ScopeCancelled, ScopeDeadlineExceeded, ScopeStaleError


class ExecutionClaimProofRegistry:
    """Short-lived proof that one exact repository claim passed its live CAS read."""

    def __init__(
        self,
        *,
        now: Callable[[], datetime],
        max_entries: int = 256,
    ) -> None:
        if not callable(now) or type(max_entries) is not int or max_entries < 1:
            raise ValueError("execution proof registry bounds are invalid")
        self._now = now
        self._max_entries = max_entries
        self._proofs: dict[int, ExecutionLease] = {}

    def _reap(self) -> datetime:
        now = _utc_now(self._now())
        for key, lease in tuple(self._proofs.items()):
            if lease.expires_at <= now:
                self._proofs.pop(key, None)
        return now

    def __call__(self, lease: ExecutionLease) -> bool:
        self._reap()
        return self._proofs.get(id(lease)) is lease

    def prove(self, lease: ExecutionLease) -> None:
        now = self._reap()
        if not isinstance(lease, ExecutionLease) or lease.expires_at <= now:
            raise CandidateRejected("execution claim is no longer current")
        if len(self._proofs) >= self._max_entries and id(lease) not in self._proofs:
            raise SchedulerError("too many active execution claim proofs")
        self._proofs[id(lease)] = lease

    def discard(self, lease: ExecutionLease) -> None:
        if self._proofs.get(id(lease)) is lease:
            self._proofs.pop(id(lease), None)


class TrustedPersistedRouteResolver(Protocol):
    """Host adapter that attests a saved conversation against the current owner."""

    async def resolve_current(
        self, owner_id: str, persisted_recipient: ConversationRef
    ) -> ConversationRef | None: ...

    async def resolve_private(
        self, owner_id: str, persisted_recipient: ConversationRef
    ) -> ConversationRef | None: ...


class EvaluationPreparer(Protocol):
    """Build the atomic per-subscription commit while S still owns the lease."""

    async def prepare_evaluations(
        self, lease: ExecutionLease, observation: Observation
    ) -> ObservationEvaluationCommit: ...


class SchedulerError(RuntimeError):
    """A registered schedule cannot safely be executed."""


class CandidateRejected(SchedulerError):
    """A persisted candidate no longer agrees with trusted live declarations."""


@dataclass(frozen=True, slots=True)
class SchedulerQuotas:
    """Deployment-selected in-process admission and finite work limits."""

    max_global: int
    max_per_module: int
    max_per_source: int
    max_scan_page: int
    collection_timeout_seconds: float
    runtime_minimum_seconds: float
    digest_lookback_days: int

    def __post_init__(self) -> None:
        for name in ("max_global", "max_per_module", "max_per_source", "max_scan_page"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a built-in positive integer")
        if (
            type(self.digest_lookback_days) is not int
            or not 0 <= self.digest_lookback_days <= 365
        ):
            raise ValueError(
                "digest_lookback_days must be an integer from 0 through 365"
            )
        for name in ("collection_timeout_seconds", "runtime_minimum_seconds"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite positive seconds")


@dataclass(frozen=True, slots=True)
class RescheduleConfiguration:
    """Deployment policy for completion-relative due times.

    Ratios are applied symmetrically around the selected base delay. Keeping
    each ratio below one guarantees positive jittered delays before source
    floors are applied. These fields intentionally have no product defaults.
    """

    failure_backoff_seconds: float
    success_jitter_ratio: float
    partial_jitter_ratio: float
    failure_jitter_ratio: float

    def __post_init__(self) -> None:
        if (
            type(self.failure_backoff_seconds) not in (int, float)
            or not isfinite(self.failure_backoff_seconds)
            or not 0 < self.failure_backoff_seconds <= 7 * 24 * 60 * 60
        ):
            raise ValueError(
                "failure_backoff_seconds must be positive and at most seven days"
            )
        for name in (
            "success_jitter_ratio",
            "partial_jitter_ratio",
            "failure_jitter_ratio",
        ):
            value = getattr(self, name)
            if (
                type(value) not in (int, float)
                or not isfinite(value)
                or not 0 <= value < 1
            ):
                raise ValueError(f"{name} must be finite and in [0, 1)")


@dataclass(frozen=True, slots=True)
class CollectionResult:
    lease: ExecutionLease
    observation: Observation | None
    committed: bool
    failed: bool = False


@dataclass(frozen=True, slots=True)
class ScanPage:
    processed: int
    next_cursor: object | None
    exhausted: bool


@dataclass(frozen=True, slots=True)
class _Principal:
    owner_id: str
    grant: GrantReference | None
    subscription_id: str
    subscription_revision: int


def _utc_now(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("scheduler clock must return an aware datetime")
    return value.astimezone(UTC)


def _verify_schema(schema: Mapping[str, object], value: object) -> JsonObject:
    _validate_schema(schema, value, "parameters", 0)
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise CandidateRejected("scheduled parameters must be an object")
    # NormalizedInput performs a detached, recursively immutable JSON copy.
    return validate_contract(NormalizedInput(value)).values


def canonical_collection_key(
    module: RegisteredModule,
    descriptor: ScheduleDescriptor,
    collector_parameters: JsonObject,
    scope: OwnerScope,
) -> CollectionKey:
    """Validate declaration/input/scope, then use the registered normalizer."""
    validate_contract(descriptor)
    validate_contract(collector_parameters)
    validate_contract(scope)
    if not module.enabled:
        raise CandidateRejected("module is disabled")
    if descriptor not in module.manifest.schedules:
        raise CandidateRejected("schedule is not registered")
    if descriptor.trigger is not ScheduleTrigger.PERIODIC:
        raise CandidateRejected("on-demand schedules cannot run periodically")
    if descriptor.shared_scope is OwnershipKind.PUBLIC:
        if scope.kind is not OwnershipKind.PUBLIC:
            raise CandidateRejected("schedule requires public collection scope")
    elif descriptor.shared_scope is not scope.kind:
        raise CandidateRejected("schedule ownership scope does not match")
    raw = _verify_schema(descriptor.input_schema, collector_parameters)
    try:
        collector = module.handlers.collectors[descriptor.collector_id]
        normalized = collector.normalize(raw)
    except Exception as exc:
        raise CandidateRejected("collector parameters could not be normalized") from exc
    if not isinstance(normalized, NormalizedInput):
        raise CandidateRejected("collector returned an invalid normalized input")
    return validate_contract(
        CollectionKey(
            module.module_id,
            descriptor.collector_id,
            descriptor.key_version,
            descriptor.source_id,
            normalized,
            scope,
        )
    )


def _route_equal(left: ConversationRef | None, right: ConversationRef) -> bool:
    validate_contract(left)
    validate_contract(right)
    return isinstance(left, ConversationRef) and left == right


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain_json(item) for item in value]
    return value


def _key_token(key: CollectionKey) -> str:
    validate_contract(key)
    scope = key.scope
    payload = [
        key.module_id,
        key.collector_id,
        key.key_version,
        key.source_id,
        _plain_json(key.parameters.values),
        scope.kind.value,
        scope.user_id,
        None if scope.grant is None else [scope.grant.grant_id, scope.grant.revision],
    ]
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def _resolve_local_boundary(
    local_date: date, profile: DigestScheduleProfile
) -> datetime | None:
    """Resolve the configured wall-clock boundary under its explicit DST policy."""
    validate_contract(profile)
    zone = ZoneInfo(profile.timezone_name)
    hour, minute = (int(part) for part in profile.local_time.split(":"))
    wall = datetime.combine(local_date, time(hour, minute))
    valid: dict[int, datetime] = {}
    for fold in (0, 1):
        candidate = wall.replace(tzinfo=zone, fold=fold)
        round_trip = candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
        if round_trip == wall:
            valid[fold] = candidate.astimezone(UTC)
    if valid:
        if len(valid) == 1 or valid.get(0) == valid.get(1):
            return next(iter(valid.values()))
        chosen = 0 if profile.fold_policy.value == "first_occurrence" else 1
        return valid[chosen]
    if profile.gap_policy.value == "skip":
        return None

    # A nonexistent HH:MM boundary advances to the first valid local instant.
    # Gaps in tzdb are bounded to 24 hours; 1-second probes preserve zones with
    # historical sub-minute transitions as well as modern DST changes.
    probe = wall
    for _ in range(24 * 60 * 60):
        probe += timedelta(seconds=1)
        for fold in (0, 1):
            candidate = probe.replace(tzinfo=zone, fold=fold)
            if candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == probe:
                return candidate.astimezone(UTC)
    raise CandidateRejected("timezone gap exceeds supported transition window")


def _digest_window_id(record: SubscriptionRecord, local_date: date) -> str:
    profile = record.digest_schedule
    if profile is None:
        raise CandidateRejected("subscription has no digest schedule")
    identity = {
        "profile": {
            "timezone": profile.timezone_name,
            "time": profile.local_time,
            "duration": profile.window_duration_seconds,
            "fold": profile.fold_policy.value,
            "gap": profile.gap_policy.value,
            "revision": profile.policy_revision,
        },
        "recipient": [
            record.recipient.adapter_id,
            record.recipient.kind.value,
            record.recipient.conversation_id,
            record.recipient.delivery_route,
        ],
        "local_date": local_date.isoformat(),
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return "digest:v1:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def digest_window_for_date(
    record: SubscriptionRecord, local_date: date
) -> DigestWindow | None:
    """Build a deterministic daily window; persistence decides whether it is new."""
    profile = record.digest_schedule
    if (
        record.status is not SubscriptionStatus.ACTIVE
        or record.notification_mode != "digest"
        or profile is None
    ):
        raise CandidateRejected("subscription is not an active digest schedule")
    due_at = _resolve_local_boundary(local_date, profile)
    if due_at is None:
        return None
    utc_end = due_at
    utc_start = utc_end - timedelta(seconds=profile.window_duration_seconds)
    local_key = local_date.isoformat()
    return DigestWindow(
        window_id=_digest_window_id(record, local_date),
        timezone_name=profile.timezone_name,
        local_schedule_key=local_key,
        utc_start=utc_start,
        utc_end=utc_end,
        due_at=due_at,
        fold_policy=profile.fold_policy,
        gap_policy=profile.gap_policy,
        members=(),
        policy_revision=profile.policy_revision,
        schedule_profile=profile,
        schedule_recipient=record.recipient,
    )


class SharedCollectionScheduler:
    """Execute persistent due jobs and materialize persisted digest windows."""

    def __init__(
        self,
        *,
        registry: Registry,
        issuer: ContextIssuer,
        lifecycle,
        admission: AdmissionPort,
        execution_claim_proofs: ExecutionClaimProofRegistry,
        binder_for: Callable[[str], InvocationServiceBinder],
        repository: SchedulerRepository,
        subscriptions: SubscriptionStore,
        job_links: SubscriptionJobRepository,
        digest_windows: DigestWindowRepository,
        grant_store: GrantStore,
        routes: TrustedPersistedRouteResolver,
        evaluation_preparer: EvaluationPreparer | None,
        cadence: CadenceConfiguration,
        reschedule: RescheduleConfiguration,
        quotas: SchedulerQuotas,
        now: Callable[[], datetime],
        random_source: Callable[[], float] = random.random,
        monotonic_clock: Callable[[], float] = monotonic,
        runtime_minimum_for: Callable[[RegisteredModule, ScheduleDescriptor], float]
        | None = None,
        source_minimum_for: Callable[[SourceDeclaration], float] | None = None,
    ) -> None:
        validate_contract(binder_for)
        validate_contract(runtime_minimum_for)
        validate_contract(source_minimum_for)
        self.registry = registry
        self.issuer = issuer
        self.lifecycle = lifecycle
        self.admission = admission
        self.execution_claim_proofs = execution_claim_proofs
        self.binder_for = binder_for
        self.repository = repository
        self.subscriptions = subscriptions
        self.job_links = job_links
        self.digest_windows = digest_windows
        self.grant_store = grant_store
        self.routes = routes
        self.evaluation_preparer = evaluation_preparer
        self.cadence = cadence
        self.reschedule = reschedule
        self.quotas = quotas
        self.now = now
        self.random_source = random_source
        self.monotonic_clock = monotonic_clock
        self.runtime_minimum_for = runtime_minimum_for or (
            lambda _module, _schedule: quotas.runtime_minimum_seconds
        )
        self.source_minimum_for = source_minimum_for or (
            lambda source: 60.0 / source.requests_per_minute
        )
        self._active: dict[str, asyncio.Task[CollectionResult | None]] = {}
        self._active_lock = asyncio.Lock()
        self._global_gate = asyncio.Semaphore(quotas.max_global)
        self._module_gates: dict[str, asyncio.Semaphore] = {}
        self._source_gates: dict[tuple[str, str], asyncio.Semaphore] = {}

    async def run_due_page(
        self, *, after_cursor: DueJobCursor | None = None, limit: int | None = None
    ) -> ScanPage:
        """Process one fair page. Persist ``next_cursor`` in the host between runs."""
        now = _utc_now(self.now())
        page_limit = (
            self.quotas.max_scan_page
            if limit is None
            else min(limit, self.quotas.max_scan_page)
        )
        if type(page_limit) is not int or page_limit < 1:
            raise ValueError("limit must be a positive integer")
        jobs = await self.repository.list_due_jobs(
            now=now, limit=page_limit, after_cursor=after_cursor
        )
        results = await asyncio.gather(
            *(self.run_due_job(candidate) for candidate in jobs)
        )
        del results
        return ScanPage(
            processed=len(jobs),
            next_cursor=None if not jobs else jobs[-1].cursor,
            exhausted=len(jobs) < page_limit,
        )

    async def run_due_job(self, candidate: DueCollectionJob) -> CollectionResult | None:
        """Resolve the live declaration then join or claim one persistent execution."""
        if not isinstance(candidate, DueCollectionJob):
            raise TypeError("candidate must be a DueCollectionJob")
        key_token = _key_token(candidate.key)
        async with self._active_lock:
            current = self._active.get(key_token)
            if current is None or current.done():
                scope = self.lifecycle.scope(candidate.key.module_id)
                current = scope.create_task(
                    self._execute_due(candidate), name=f"collection:{key_token}"
                )
                self._active[key_token] = current
                current.add_done_callback(
                    lambda task, key=key_token: self._forget_completed(key, task)
                )
        return await asyncio.shield(current)

    def _forget_completed(
        self, key: str, task: asyncio.Task[CollectionResult | None]
    ) -> None:
        # asyncio callbacks run serially on the owner loop. Removing synchronously
        # avoids creating a second, unscoped cleanup task for each completed job.
        if self._active.get(key) is task:
            self._active.pop(key, None)

    async def cancel_local(self, key: CollectionKey) -> bool:
        """Request local task cancellation; external IO cancellation is not implied."""
        validate_contract(key)
        key_token = _key_token(key)
        async with self._active_lock:
            task = self._active.get(key_token)
            if task is None or task.done():
                return False
            task.cancel()
            return True

    def _registered_schedule(
        self, key: CollectionKey
    ) -> tuple[
        RegistrySnapshot, RegisteredModule, ScheduleDescriptor, SourceDeclaration
    ]:
        validate_contract(key)
        snapshot = self.registry.snapshot()
        try:
            module = snapshot.module(key.module_id)
        except Exception as exc:
            raise CandidateRejected("module is not registered") from exc
        if not module.enabled:
            raise CandidateRejected("module is disabled")
        descriptor = next(
            (
                item
                for item in module.manifest.schedules
                if item.collector_id == key.collector_id
                and item.key_version == key.key_version
                and item.source_id == key.source_id
                and item.trigger is ScheduleTrigger.PERIODIC
            ),
            None,
        )
        if descriptor is None:
            raise CandidateRejected("schedule declaration is missing")
        collector = module.handlers.collectors.get(descriptor.collector_id)
        if collector is None or not callable(getattr(collector, "collect", None)):
            raise CandidateRejected("registered collector handler is missing")
        source = next(
            (
                item
                for item in module.manifest.sources
                if item.source_id == key.source_id
            ),
            None,
        )
        if source is None:
            raise CandidateRejected("source declaration is missing")
        normalized = canonical_collection_key(
            module, descriptor, key.parameters.values, key.scope
        )
        if normalized != key:
            raise CandidateRejected("persisted collection key is not canonical")
        return snapshot, module, descriptor, source

    async def _principal_for(self, key: CollectionKey) -> _Principal | None:
        validate_contract(key)
        links = await self.job_links.for_collection(key)
        for link in links:
            record = await self.subscriptions.current(link.subscription_id)
            if (
                record is None
                or record.status is not SubscriptionStatus.ACTIVE
                or record.collection_key != key
                or record.revision != link.subscription_revision
            ):
                continue
            scope = key.scope
            if scope.kind is OwnershipKind.PUBLIC:
                if record.grant is not None:
                    continue
            elif record.owner_id != scope.user_id or record.grant != scope.grant:
                continue
            if record.grant is not None:
                if not await self._grant_is_current(
                    record.grant, owner_id=record.owner_id, module_id=record.module_id
                ):
                    continue
            return _Principal(
                record.owner_id, record.grant, record.subscription_id, record.revision
            )
        return None

    async def _grant_is_current(
        self, reference: GrantReference, *, owner_id: str, module_id: str
    ) -> bool:
        """Prove the persisted subscription reference against the live Grant."""
        validate_contract(reference)
        grant = await self.grant_store.current_grant(reference.grant_id)
        if (
            not isinstance(grant, Grant)
            or grant.status is not GrantStatus.ACTIVE
            or grant.revision != reference.revision
            or grant.principal_id != owner_id
            or grant.module_id != module_id
        ):
            return False
        if grant.expires_at is not None:
            if _utc_now(grant.expires_at) <= _utc_now(self.now()):
                return False
        return True

    def _cadence_seconds(
        self,
        candidate: DueCollectionJob,
        module: RegisteredModule,
        schedule: ScheduleDescriptor,
        source: SourceDeclaration,
    ) -> float:
        validate_contract(schedule)
        validate_contract(source)
        limits = validate_contract(
            IntervalLimits(
                requested_seconds=candidate.cadence_seconds,
                module_minimum_seconds=schedule.minimum_interval_seconds,
                runtime_minimum_seconds=max(
                    self.quotas.runtime_minimum_seconds,
                    self.runtime_minimum_for(module, schedule),
                    max(
                        60.0 / source.requests_per_minute,
                        self.source_minimum_for(source),
                    ),
                ),
            )
        )
        available = tuple(
            value
            for value in self.cadence.allowed_seconds
            if value >= limits.target_seconds
        )
        if not available:
            raise CandidateRejected("no configured cadence satisfies current limits")
        return self.cadence.validate_target(min(available))

    def _next_due_at(
        self,
        observation: Observation,
        *,
        cadence_seconds: float,
        source: SourceDeclaration,
        completion_at: datetime,
    ) -> datetime:
        validate_contract(observation)
        validate_contract(source)
        if observation.completeness is ObservationCompleteness.FAILED:
            base_seconds = self.reschedule.failure_backoff_seconds
            jitter_ratio = self.reschedule.failure_jitter_ratio
        elif observation.completeness is ObservationCompleteness.PARTIAL:
            base_seconds = cadence_seconds
            jitter_ratio = self.reschedule.partial_jitter_ratio
        else:
            base_seconds = cadence_seconds
            jitter_ratio = self.reschedule.success_jitter_ratio
        sample = self.random_source()
        if (
            type(sample) not in (int, float)
            or not isfinite(sample)
            or not 0 <= sample <= 1
        ):
            raise SchedulerError("random source must return a finite value in [0, 1]")
        jittered = base_seconds * (1.0 + (2.0 * sample - 1.0) * jitter_ratio)
        source_floor = max(
            60.0 / source.requests_per_minute,
            self.source_minimum_for(source),
        )
        delay = max(jittered, source_floor)
        completed = max(
            _utc_now(completion_at), observation.collected_at.astimezone(UTC)
        )
        due_at = completed + timedelta(seconds=delay)
        if due_at <= observation.collected_at.astimezone(UTC):
            raise SchedulerError("computed next due time would create a hot loop")
        return due_at

    async def _execute_due(
        self, candidate: DueCollectionJob
    ) -> CollectionResult | None:
        lease: ExecutionLease | None = None
        invocation: InvocationView | None = None
        scheduled_lease = None
        try:
            _snapshot, module, schedule, source = self._registered_schedule(
                candidate.key
            )
            principal = await self._principal_for(candidate.key)
            if principal is None:
                return None
            if self.evaluation_preparer is None:
                # A persisted due job is subscription-backed. Do not publish an
                # observation separately from its matched-event transaction.
                return None
            cadence_seconds = self._cadence_seconds(candidate, module, schedule, source)
            module_gate = self._module_gates.setdefault(
                module.module_id, asyncio.Semaphore(self.quotas.max_per_module)
            )
            source_key = (module.module_id, source.source_id)
            source_gate = self._source_gates.setdefault(
                source_key, asyncio.Semaphore(self.quotas.max_per_source)
            )
            async with self._global_gate, module_gate, source_gate:
                live_snapshot = self.registry.snapshot()
                live_module = live_snapshot.modules.get(module.module_id)
                if live_module is None or not live_module.enabled:
                    return None
                live_schedule = next(
                    (
                        item
                        for item in live_module.manifest.schedules
                        if item.collector_id == schedule.collector_id
                        and item.key_version == schedule.key_version
                        and item.source_id == schedule.source_id
                    ),
                    None,
                )
                if live_schedule != schedule:
                    return None
                request = self._run_request(
                    candidate,
                    cadence_seconds,
                    live_module.epoch,
                    live_snapshot.revision,
                )
                lease = await self.repository.claim_due(
                    request, now=_utc_now(self.now())
                )
                if lease is None:
                    return None
                if not await self.repository.is_current(
                    lease, now=_utc_now(self.now())
                ):
                    return None
                # There is deliberately no await between the exact-object proof
                # and Lifecycle Admission consuming it.
                self.execution_claim_proofs.prove(lease)
                scheduled_lease = self.admission.admit_schedule(
                    lease, schedule.collector_id
                )
                timeout_seconds = min(
                    self.quotas.collection_timeout_seconds,
                    source.timeout_seconds,
                )
                deadline = self.monotonic_clock() + timeout_seconds
                invocation = self.issuer.issue(
                    origin=InvocationOrigin.SCHEDULER,
                    module_id=module.module_id,
                    module_epoch=live_module.epoch,
                    registry_revision=live_snapshot.revision,
                    actor_id=principal.owner_id,
                    deadline=deadline,
                    grant_id=None
                    if principal.grant is None
                    else principal.grant.grant_id,
                    grant_revision=None
                    if principal.grant is None
                    else principal.grant.revision,
                    capability_id=None,
                )
                self.issuer.attach_lease(invocation, scheduled_lease)
                binder = self.binder_for(module.module_id)
                bound = await binder.bind(invocation)
                self.admission.check(scheduled_lease)
                context = validate_contract(
                    CollectionView(
                        candidate.key,
                        deadline_monotonic=deadline,
                        invocation=invocation,
                    )
                )
                require_collection_context(
                    self.issuer, context, clock=self.monotonic_clock
                )
                checkpoint = await self.repository.current_checkpoint(
                    principal.subscription_id, candidate.key
                )
                await self._require_current_execution(
                    candidate.key, principal, lease, scheduled_lease
                )
                previous = (
                    None
                    if checkpoint is None
                    or checkpoint.fence != lease.subscription_fence
                    else checkpoint.snapshot.observation
                )
                handlers = self.lifecycle.handlers(module.module_id)
                work = bound.tasks.await_result(
                    handlers.collectors[schedule.collector_id].collect(
                        context, candidate.key.parameters, previous
                    )
                )
                observation = await asyncio.wait_for(work, timeout=timeout_seconds)
                if (
                    not isinstance(observation, Observation)
                    or observation.key != candidate.key
                ):
                    raise CandidateRejected(
                        "collector returned an observation for another key"
                    )
                completed_at = _utc_now(self.now())
                observation = validate_contract(
                    Observation(
                        observation.observation_id,
                        observation.key,
                        observation.data_version,
                        observation.source_observed_at,
                        completed_at,
                        observation.completeness,
                        observation.covered_ids,
                        observation.payload,
                    )
                )
                await self._require_current_execution(
                    candidate.key, principal, lease, scheduled_lease
                )
                evaluation_commit = await self.evaluation_preparer.prepare_evaluations(
                    lease, observation
                )
                if (
                    not isinstance(evaluation_commit, ObservationEvaluationCommit)
                    or evaluation_commit.observation != observation
                ):
                    raise CandidateRejected(
                        "evaluation preparer returned a mismatched commit"
                    )
                await self._require_current_execution(
                    candidate.key, principal, lease, scheduled_lease
                )
                if self.monotonic_clock() >= deadline:
                    raise ScopeDeadlineExceeded(
                        "collection deadline elapsed before commit"
                    )
                await self._require_current_execution(
                    candidate.key, principal, lease, scheduled_lease
                )
                completion_at = _utc_now(self.now())
                next_due_at = self._next_due_at(
                    observation,
                    cadence_seconds=cadence_seconds,
                    source=source,
                    completion_at=completion_at,
                )
                committed = await self.repository.commit_observation_with_evaluations(
                    lease, evaluation_commit, next_due_at=next_due_at
                )
                return CollectionResult(lease, observation, committed)
        except asyncio.CancelledError:
            raise
        except (ScopeCancelled, ScopeStaleError):
            return None
        except (ScopeDeadlineExceeded, asyncio.TimeoutError):
            if lease is None:
                return None
            return await self._commit_failed_outcome(
                candidate,
                principal,
                lease,
                cadence_seconds,
                source,
                scheduled_lease,
            )
        except Exception:
            if lease is None:
                return None
            return await self._commit_failed_outcome(
                candidate,
                principal,
                lease,
                cadence_seconds,
                source,
                scheduled_lease,
            )
        finally:
            if scheduled_lease is not None:
                self.admission.release(scheduled_lease)
            if invocation is not None:
                self.issuer.release(invocation)
            if lease is not None:
                self.execution_claim_proofs.discard(lease)
                await _release_execution_shielded(self.repository, lease)

    async def _commit_failed_outcome(
        self,
        candidate: DueCollectionJob,
        principal: _Principal,
        lease: ExecutionLease,
        cadence_seconds: float,
        source: SourceDeclaration,
        scheduled_lease,
    ) -> CollectionResult:
        """Persist a safe failure and retry time while the lease is still live.

        Collection timeout/deadline expiry stops the collector work, but the
        scheduler may still commit failure bookkeeping under the valid lease.
        Revocation, cancellation, epoch changes, and stale grants take the
        fail-closed release path in the caller instead.
        """
        validate_contract(source)
        failed = validate_contract(
            Observation(
                uuid4().hex,
                candidate.key,
                self._data_version(candidate.key),
                None,
                _utc_now(self.now()),
                ObservationCompleteness.FAILED,
                (),
                {},
            )
        )
        try:
            await self._require_current_execution(
                candidate.key, principal, lease, scheduled_lease
            )
            evaluation_commit = await self.evaluation_preparer.prepare_evaluations(
                lease, failed
            )
            if (
                not isinstance(evaluation_commit, ObservationEvaluationCommit)
                or evaluation_commit.observation != failed
            ):
                raise CandidateRejected(
                    "evaluation preparer returned a mismatched failed commit"
                )
            await self._require_current_execution(
                candidate.key, principal, lease, scheduled_lease
            )
            next_due_at = self._next_due_at(
                failed,
                cadence_seconds=cadence_seconds,
                source=source,
                completion_at=_utc_now(self.now()),
            )
            committed = await self.repository.commit_observation_with_evaluations(
                lease, evaluation_commit, next_due_at=next_due_at
            )
        except asyncio.CancelledError:
            raise
        except (ScopeCancelled, ScopeStaleError):
            return CollectionResult(lease, None, False)
        except Exception:
            return CollectionResult(lease, failed, False, failed=True)
        return CollectionResult(lease, failed, committed, failed=True)

    @staticmethod
    def _run_request(
        candidate: DueCollectionJob, cadence: float, epoch: int, registry_revision: int
    ):
        return CollectionRunRequest(
            candidate.key,
            candidate.due_at,
            cadence,
            candidate.config_revision,
            epoch,
            registry_revision,
        )

    def _data_version(self, key: CollectionKey) -> int:
        validate_contract(key)
        snapshot = self.registry.snapshot()
        module = snapshot.modules.get(key.module_id)
        if module is None:
            return 1
        descriptor = next(
            (
                item
                for item in module.manifest.schedules
                if item.collector_id == key.collector_id
            ),
            None,
        )
        return 1 if descriptor is None else descriptor.data_version

    async def _require_current_execution(
        self,
        key: CollectionKey,
        principal: _Principal,
        lease: ExecutionLease,
        scheduled_lease,
    ) -> None:
        validate_contract(key)
        if scheduled_lease is not None:
            self.admission.check(scheduled_lease)
        if not await self.repository.is_current(lease, now=_utc_now(self.now())):
            raise ScopeStaleError("persistent execution claim changed")
        snapshot = self.registry.snapshot()
        module = snapshot.modules.get(key.module_id)
        if module is None or not module.enabled or module.epoch != lease.module_epoch:
            raise ScopeStaleError("module registration changed")
        if principal.grant is not None:
            if not await self._grant_is_current(
                principal.grant, owner_id=principal.owner_id, module_id=key.module_id
            ):
                raise ScopeStaleError("authorization grant changed")
        if await self._principal_for(key) != principal:
            raise ScopeStaleError("subscription owner or grant changed")
        if not await self.repository.is_current(lease, now=_utc_now(self.now())):
            raise ScopeStaleError("persistent execution claim changed")
        if scheduled_lease is not None:
            self.admission.check(scheduled_lease)

    async def create_digest_windows_page(
        self,
        *,
        after_subscription_id: str | None = None,
        limit: int | None = None,
        local_dates: tuple[date, ...] | None = None,
    ) -> ScanPage:
        """Persist today's and next local daily windows for one stable page.

        ``after_subscription_id`` is host-persisted progress. If a host supplies
        no explicit dates, each candidate gets its current and next local date;
        deterministic IDs and repository create make retries/restarts idempotent.
        """
        page_limit = (
            self.quotas.max_scan_page
            if limit is None
            else min(limit, self.quotas.max_scan_page)
        )
        if type(page_limit) is not int or page_limit < 1:
            raise ValueError("limit must be a positive integer")
        candidates = await self.subscriptions.list_active_digest_schedules(
            limit=page_limit, after_subscription_id=after_subscription_id
        )
        for candidate in candidates:
            try:
                record = await self._current_digest_record(candidate)
                if record is None:
                    continue
                dates = local_dates
                if dates is None:
                    local_today = (
                        _utc_now(self.now())
                        .astimezone(ZoneInfo(record.digest_schedule.timezone_name))
                        .date()
                    )
                    dates = tuple(
                        local_today + timedelta(days=offset)
                        for offset in range(-self.quotas.digest_lookback_days, 2)
                    )
                for local_date in dates:
                    stable_id = _digest_window_id(record, local_date)
                    # Existing stored UTC boundaries remain authoritative across
                    # clock/tzdata changes; never reinterpret their local date.
                    if await self.digest_windows.get(stable_id) is not None:
                        continue
                    window = digest_window_for_date(record, local_date)
                    if window is None:
                        continue
                    # Repeat authority checks at the write boundary. The DB
                    # create is insert-if-absent and retains the first UTC bounds.
                    if await self._current_digest_record(candidate) != record:
                        break
                    await self.digest_windows.create(window)
            except CandidateRejected:
                continue
        return ScanPage(
            len(candidates),
            None if not candidates else candidates[-1].cursor,
            len(candidates) < page_limit,
        )

    async def _current_digest_record(
        self, candidate: ActiveDigestSchedule
    ) -> SubscriptionRecord | None:
        original = candidate.record
        record = await self.subscriptions.current(original.subscription_id)
        if (
            record is None
            or record.status is not SubscriptionStatus.ACTIVE
            or record.revision != original.revision
            or record.module_id != original.module_id
            or record.type_id != original.type_id
            or record.collection_key != original.collection_key
            or record.owner_id != original.owner_id
            or record.grant != original.grant
            or record.recipient != original.recipient
            or record.notification_mode != "digest"
            or record.digest_schedule != original.digest_schedule
            or record.filters != original.filters
        ):
            return None
        module = self.registry.snapshot().modules.get(record.module_id)
        if module is None or not module.enabled:
            return None
        subscription_type = next(
            (
                item
                for item in module.manifest.subscriptions
                if item.type_id == record.type_id
            ),
            None,
        )
        if (
            subscription_type is None
            or subscription_type.collector_id != record.collection_key.collector_id
        ):
            return None
        schedule = next(
            (
                item
                for item in module.manifest.schedules
                if item.collector_id == record.collection_key.collector_id
                and item.key_version == record.collection_key.key_version
                and item.source_id == record.collection_key.source_id
                and item.trigger is ScheduleTrigger.PERIODIC
            ),
            None,
        )
        if schedule is None or record.collection_key.module_id != record.module_id:
            return None
        if schedule.shared_scope is not record.collection_key.scope.kind:
            return None
        if record.grant is not None and not await self._grant_is_current(
            record.grant, owner_id=record.owner_id, module_id=record.module_id
        ):
            return None
        if record.collection_key.scope.kind is OwnershipKind.AUTHORIZED:
            if record.recipient.kind is not ConversationKind.DIRECT:
                return None
            resolved = await self.routes.resolve_private(
                record.owner_id, record.recipient
            )
        else:
            resolved = await self.routes.resolve_current(
                record.owner_id, record.recipient
            )
        if not _route_equal(resolved, record.recipient):
            return None
        return record

    async def list_due_digest_routes(
        self,
        *,
        after_cursor: DigestRouteCursor | None = None,
        limit: int | None = None,
    ) -> tuple[DigestRouteCandidate, ...]:
        """Recover expired delivery claims, then page persisted eligible routes."""
        now = _utc_now(self.now())
        await self.digest_windows.recover_expired_envelope_claims(
            before=now, recovered_at=now
        )
        page_limit = (
            self.quotas.max_scan_page
            if limit is None
            else min(limit, self.quotas.max_scan_page)
        )
        if type(page_limit) is not int or page_limit < 1:
            raise ValueError("limit must be a positive integer")
        return await self.digest_windows.list_due_routes(
            now=now, limit=page_limit, after_cursor=after_cursor
        )


async def _release_execution_shielded(
    repository: SchedulerRepository, lease: ExecutionLease
) -> None:
    """Drain exact-lease cleanup even when its module task is cancelled again."""
    cleanup = asyncio.create_task(repository.release(lease))
    while True:
        try:
            await asyncio.shield(cleanup)
            return
        except asyncio.CancelledError:
            if cleanup.done():
                return
        except Exception:
            return


__all__ = [
    "CandidateRejected",
    "CollectionResult",
    "ExecutionClaimProofRegistry",
    "EvaluationPreparer",
    "ScanPage",
    "SchedulerError",
    "SchedulerQuotas",
    "RescheduleConfiguration",
    "SharedCollectionScheduler",
    "TrustedPersistedRouteResolver",
    "canonical_collection_key",
    "digest_window_for_date",
]
