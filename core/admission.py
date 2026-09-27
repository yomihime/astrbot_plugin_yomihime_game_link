"""Single in-process admission gate and send approval boundary."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from ..api.contexts import InvocationView
from ..api.manifests import CapabilityReference, ModuleManifest
from ..api.services import CapabilityHealth, HealthReport, HealthStatus
from .context_issuer import ContextIssuer, InvalidInvocation
from .ports import (
    AdmissionLease,
    AdmissionPort,
    ApprovedSendScheduler,
    DeliveryLease,
    DeliveryMemberIdentity,
    DeliveryWorkKind,
    DependencyIdentity,
    ExecutionLease,
    RunIdentity,
    ScheduledLease,
    SendApproval,
    SendPermit,
)
from .registry import RegisteredModule, Registry, RegistryError


class AdmissionError(PermissionError):
    """A request cannot be admitted or its lease is no longer current."""

    code = "module_unavailable"


class InvalidAdmissionLease(AdmissionError):
    """The supplied lease was not issued by this AdmissionController."""

    code = "invalid_admission_lease"


class CapabilityUnavailable(AdmissionError):
    """The requested capability or one of its required dependencies is down."""

    code = "capability_unavailable"


class MutationReentryError(RuntimeError):
    """A mutation callback attempted to acquire the non-reentrant control gate."""


HealthQuery = Callable[[str, str], tuple[CapabilityHealth, int]]
CurrentRun = Callable[[str], RunIdentity | None]
IsActive = Callable[[RunIdentity], bool]
ExecutionClaimProver = Callable[[ExecutionLease], bool]
UtcClock = Callable[[], datetime]


@dataclass(slots=True)
class _LeaseRecord:
    lease: AdmissionLease | ScheduledLease | DeliveryLease
    identity: RunIdentity
    view: InvocationView | None = None
    capability_id: str | None = None
    collector_id: str | None = None
    execution: ExecutionLease | None = None
    dependencies: tuple[DependencyIdentity, ...] = ()


class AdmissionController(AdmissionPort):
    """One gate, one mutation lock, and identity-based issued lease tracking.

    Lifecycle remains the sole owner of module instances and runtime epochs.
    Admission retains only the currently open gate identity and issued lease
    objects needed to reject copied DTOs and release caller references.
    """

    def __init__(
        self,
        registry: Registry,
        issuer: ContextIssuer,
        *,
        current_run: CurrentRun,
        is_active: IsActive,
        health_query: HealthQuery,
        execution_claim_prover: ExecutionClaimProver | None = None,
        utc_clock: UtcClock = lambda: datetime.now(UTC),
    ) -> None:
        if not isinstance(registry, Registry):
            raise TypeError("registry must be a Registry")
        if not isinstance(issuer, ContextIssuer):
            raise TypeError("issuer must be a ContextIssuer")
        if not callable(current_run) or not callable(is_active):
            raise TypeError("Lifecycle callbacks must be callable")
        if not callable(health_query):
            raise TypeError("health_query must be callable")
        if execution_claim_prover is not None and not callable(execution_claim_prover):
            raise TypeError("execution_claim_prover must be callable")
        if not callable(utc_clock):
            raise TypeError("utc_clock must be callable")
        self.registry = registry
        self.issuer = issuer
        self._current_run = current_run
        self._is_active = is_active
        self._health_query = health_query
        self._execution_claim_prover = execution_claim_prover
        self._utc_clock = utc_clock
        self._open: dict[str, RunIdentity] = {}
        self._leases: dict[str, _LeaseRecord] = {}
        self._view_leases: dict[str, str] = {}
        self._mutation_lock = asyncio.Lock()
        self._mutation_context: ContextVar[object | None] = ContextVar(
            f"admission_mutation_{id(self)}", default=None
        )
        self._active_mutation_token: object | None = None
        self.issuer.add_release_observer(self._on_invocation_release)

    def admit(self, view: InvocationView, capability_id: str) -> AdmissionLease:
        """Issue a root lease from an exact active issuer-owned view."""
        try:
            trusted = self.issuer.require(view)
        except InvalidInvocation as exc:
            raise AdmissionError("invocation is not active") from exc
        if (
            not isinstance(capability_id, str)
            or not capability_id
            or trusted.module_id == ""
            or trusted.capability_id != capability_id
        ):
            raise AdmissionError("capability does not match the trusted invocation")
        snapshot, module = self._registered(trusted.module_id)
        identity = self._active_identity(module)
        if trusted.module_epoch != identity.module_epoch:
            raise AdmissionError("invocation belongs to an old module epoch")
        if (
            self._view_leases.get(trusted.invocation_id) is not None
            or self.issuer.lease_for(trusted) is not None
        ):
            raise AdmissionError("invocation already has an admission lease")
        descriptor = self._capability(module.manifest, capability_id)
        _, health_revision = self._available_health(module.module_id, capability_id)
        dependencies = self._dependencies(module.module_id, module.manifest, descriptor)
        lease = AdmissionLease(
            lease_id=uuid4().hex,
            invocation_id=trusted.invocation_id,
            module_id=module.module_id,
            module_epoch=identity.module_epoch,
            capability_id=capability_id,
            health_revision=health_revision,
            registry_revision=snapshot.revision,
            dependencies=dependencies,
        )
        self.issuer.attach_lease(trusted, lease)
        try:
            self._record(lease, identity, view=trusted, capability_id=capability_id)
            self._view_leases[trusted.invocation_id] = lease.lease_id
        except BaseException:
            self.issuer.detach_lease_for_cleanup(trusted, lease)
            raise
        return lease

    def admit_schedule(
        self, execution: ExecutionLease, collector_id: str
    ) -> ScheduledLease:
        """Admit a scheduler run only from its exact short-lived source proof."""
        if not isinstance(execution, ExecutionLease):
            raise AdmissionError("scheduler claim is invalid")
        prover = self._execution_claim_prover
        if prover is None:
            raise AdmissionError("scheduler claim proof is not configured")
        try:
            proved = prover(execution)
        except Exception as exc:
            raise AdmissionError("scheduler claim proof failed") from exc
        if proved is not True:
            raise AdmissionError("scheduler claim is not current")
        if execution.expires_at <= self._now_utc():
            raise AdmissionError("scheduler claim has expired")
        snapshot, module = self._registered(execution.key.module_id)
        identity = self._active_identity(module)
        if execution.module_epoch != identity.module_epoch:
            raise AdmissionError("scheduler claim belongs to an old module epoch")
        schedule = self._schedule(
            module.manifest, collector_id, execution.key.key_version
        )
        if (
            schedule.source_id != execution.key.source_id
            or execution.key.scope.kind is not schedule.shared_scope
        ):
            raise AdmissionError("scheduler claim does not match its declaration")
        lease = ScheduledLease(
            lease_id=uuid4().hex,
            execution_lease_id=execution.token,
            module_id=module.module_id,
            module_epoch=identity.module_epoch,
            registry_revision=snapshot.revision,
            collector_id=collector_id,
            key_version=execution.key.key_version,
            dependencies=(),
        )
        self._record(
            lease,
            identity,
            collector_id=collector_id,
            execution=execution,
        )
        return lease

    def admit_delivery(
        self,
        *,
        work_id: str,
        kind: DeliveryWorkKind,
        module_id: str,
        collector_id: str,
        key_version: int,
        members: tuple[DeliveryMemberIdentity, ...],
    ) -> DeliveryLease:
        """Issue a delivery lease from persisted-work identities, not a view."""
        snapshot, module = self._registered(module_id)
        identity = self._active_identity(module)
        self._schedule(module.manifest, collector_id, key_version)
        lease = DeliveryLease(
            lease_id=uuid4().hex,
            work_id=work_id,
            kind=kind,
            module_id=module_id,
            module_epoch=identity.module_epoch,
            registry_revision=snapshot.revision,
            collector_id=collector_id,
            key_version=key_version,
            dependencies=(),
            members=members,
        )
        self._record(lease, identity, collector_id=collector_id)
        return lease

    def check(self, lease: AdmissionLease | ScheduledLease | DeliveryLease) -> None:
        """Check an exact issued lease against current run and health state."""
        record = self._require_record(lease)
        snapshot, module = self._registered(lease.module_id)
        identity = self._active_identity(module)
        if (
            record.identity is not identity
            or lease.module_epoch != identity.module_epoch
        ):
            raise AdmissionError("lease belongs to an old module epoch")
        if record.view is not None:
            try:
                self.issuer.require(record.view)
                if self.issuer.lease_for(record.view) is not lease:
                    raise InvalidInvocation("invocation lease sidecar changed")
            except InvalidInvocation as exc:
                raise AdmissionError("invocation is no longer active") from exc
        if isinstance(lease, AdmissionLease):
            descriptor = self._capability(module.manifest, lease.capability_id)
            _, revision = self._available_health(module.module_id, lease.capability_id)
            if revision != lease.health_revision:
                raise CapabilityUnavailable("capability health changed")
            dependencies = self._dependencies(
                module.module_id, module.manifest, descriptor
            )
            if dependencies != lease.dependencies:
                raise CapabilityUnavailable("required capability identity changed")
        else:
            self._schedule(module.manifest, lease.collector_id, lease.key_version)
            if isinstance(lease, ScheduledLease):
                self._check_execution_proof(record.execution)
                if (
                    record.execution is None
                    or record.execution.expires_at <= self._now_utc()
                ):
                    raise AdmissionError("scheduler claim has expired")
        # Registry revision is intentionally not compared: it is a directory hint.
        del snapshot

    def release(self, lease: AdmissionLease | ScheduledLease | DeliveryLease) -> None:
        """Drop one exact lease record; repeated release is harmless."""
        if not isinstance(lease, (AdmissionLease, ScheduledLease, DeliveryLease)):
            raise InvalidAdmissionLease("unrecognized lease")
        record = self._leases.get(lease.lease_id)
        if record is None:
            return
        if record.lease is not lease:
            raise InvalidAdmissionLease("lease object is not issuer-owned")
        if record.view is not None:
            self.issuer.detach_lease_for_cleanup(record.view, lease)
        self._forget(lease.lease_id)

    def activate(self, identity: RunIdentity, health: HealthReport) -> None:
        """Open a gate only after Lifecycle published its active projection."""
        if not isinstance(identity, RunIdentity) or not isinstance(
            health, HealthReport
        ):
            raise TypeError("activation requires a RunIdentity and HealthReport")
        if (
            not self._is_active(identity)
            or self._current_run(identity.module_id) is not identity
        ):
            raise AdmissionError("Lifecycle has not published this run")
        existing = self._open.get(identity.module_id)
        if existing is not None and existing is not identity:
            raise AdmissionError("another module epoch still has an open gate")
        _, module = self._registered(identity.module_id)
        if not module.enabled or module.epoch != identity.module_epoch:
            raise AdmissionError("Registry projection does not match this run")
        expected = {item.capability_id for item in module.manifest.capabilities}
        if set(health.capabilities) != expected:
            raise AdmissionError("health report does not cover declared capabilities")
        self._open[identity.module_id] = identity

    def close(self, identity: RunIdentity, reason: str) -> None:
        """Close the exact active gate synchronously before epoch invalidation."""
        if not isinstance(identity, RunIdentity):
            raise TypeError("close requires a RunIdentity")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be non-empty text")
        if self._open.get(identity.module_id) is identity:
            self._open.pop(identity.module_id, None)

    @asynccontextmanager
    async def mutation(self, owner: str) -> AsyncIterator[None]:
        """Acquire the shared non-reentrant mutation/send control gate."""
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError("mutation owner must be non-empty text")
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("mutation requires an asyncio task")
        inherited = self._mutation_context.get()
        if inherited is not None and inherited is self._active_mutation_token:
            raise MutationReentryError("mutation gate is not reentrant")
        await self._mutation_lock.acquire()
        owner = object()
        self._active_mutation_token = owner
        context_token = self._mutation_context.set(owner)
        try:
            yield
        finally:
            self._active_mutation_token = None
            self._mutation_context.reset(context_token)
            self._mutation_lock.release()

    async def approve_and_schedule_send(
        self,
        lease: AdmissionLease | DeliveryLease,
        *,
        name: str,
        final_check: Callable[[], Awaitable[SendApproval | None]],
        sender: Callable[[SendPermit], Awaitable[object]],
        scheduler: ApprovedSendScheduler,
        abort_before_dispatch: Callable[[SendApproval, str], Awaitable[None]],
    ) -> SendPermit | None:
        """Linearize final checks, durable claim, permit, and task ownership."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("send name must be non-empty text")
        if not callable(final_check) or not callable(sender):
            raise TypeError("send checks and sender must be callable")
        if not callable(getattr(scheduler, "schedule", None)):
            raise TypeError("scheduler must synchronously accept approved work")
        if not callable(abort_before_dispatch):
            raise TypeError("abort_before_dispatch must be callable")
        async with self.mutation(f"send:{name}"):
            self.check(lease)
            approval = await final_check()
            if approval is None:
                return None
            if not isinstance(approval, SendApproval):
                raise TypeError("final_check must return SendApproval or None")
            if isinstance(lease, DeliveryLease):
                allowed_members = set(lease.members)
                if any(member not in allowed_members for member in approval.members):
                    await _shield_abort(
                        abort_before_dispatch, approval, "invalid_approval_members"
                    )
                    raise AdmissionError("send approval widened delivery membership")
            elif approval.members:
                await _shield_abort(
                    abort_before_dispatch, approval, "unexpected_approval_members"
                )
                raise AdmissionError("root send approval cannot add delivery members")
            send_work: Awaitable[object] | None = None
            try:
                # This synchronous check also rechecks the InvocationView
                # deadline. Any rejected post-claim check is compensated.
                self.check(lease)
                permit = SendPermit(
                    permit_id=uuid4().hex,
                    lease_id=lease.lease_id,
                    module_id=lease.module_id,
                    module_epoch=lease.module_epoch,
                    members=approval.members,
                )
                # No await is allowed between permit issuance and task ownership.
                send_work = sender(permit)
                if not hasattr(send_work, "__await__"):
                    raise TypeError("sender must return an awaitable")
                scheduler.schedule(permit, send_work, name=name)
            except BaseException as exc:
                _close_awaitable(send_work)
                try:
                    await _shield_abort(
                        abort_before_dispatch,
                        approval,
                        type(exc).__name__,
                    )
                except BaseException:
                    # Keep the dispatch failure visible; the abort callback is
                    # still drained before returning from this control section.
                    pass
                raise
            return permit

    def _registered(self, module_id: str) -> tuple[object, RegisteredModule]:
        try:
            snapshot = self.registry.snapshot()
            return snapshot, snapshot.module(module_id)
        except RegistryError as exc:
            raise AdmissionError("module is not registered") from exc

    def _active_identity(self, module: RegisteredModule) -> RunIdentity:
        identity = self._current_run(module.module_id)
        if (
            identity is None
            or self._open.get(module.module_id) is not identity
            or not self._is_active(identity)
            or not module.enabled
            or module.epoch != identity.module_epoch
        ):
            raise AdmissionError("module has no open Lifecycle admission")
        return identity

    def _capability(self, manifest: ModuleManifest, capability_id: str):
        descriptor = next(
            (
                item
                for item in manifest.capabilities
                if item.capability_id == capability_id
            ),
            None,
        )
        if descriptor is None:
            raise AdmissionError("capability is not declared")
        return descriptor

    def _available_health(self, module_id: str, capability_id: str):
        try:
            value = self._health_query(module_id, capability_id)
        except Exception as exc:
            raise CapabilityUnavailable("capability health is unavailable") from exc
        if (
            not isinstance(value, tuple)
            or len(value) != 2
            or not isinstance(value[0], CapabilityHealth)
            or isinstance(value[1], bool)
            or not isinstance(value[1], int)
            or value[1] < 0
        ):
            raise CapabilityUnavailable("capability health query is invalid")
        health, revision = value
        if health.status is not HealthStatus.AVAILABLE:
            raise CapabilityUnavailable("capability is not available")
        return health, revision

    def _dependencies(
        self, module_id: str, manifest: ModuleManifest, descriptor
    ) -> tuple[DependencyIdentity, ...]:
        dependencies: list[DependencyIdentity] = []
        for required in descriptor.required_capabilities:
            if isinstance(required, CapabilityReference):
                target_id = required.module_id
                target_capability = required.capability_id
            else:
                package_id = module_id.split("/", 1)[0]
                target_id = f"{package_id}/{manifest.module_id}"
                target_capability = required
            _, target = self._registered(target_id)
            target_identity = self._active_identity(target)
            if not any(
                item.capability_id == target_capability
                for item in target.manifest.capabilities
            ):
                raise CapabilityUnavailable("required capability is not declared")
            _, revision = self._available_health(target_id, target_capability)
            dependencies.append(
                DependencyIdentity(
                    module_id=target_id,
                    module_epoch=target_identity.module_epoch,
                    capability_id=target_capability,
                    health_revision=revision,
                )
            )
        return tuple(
            sorted(dependencies, key=lambda item: (item.module_id, item.capability_id))
        )

    def _schedule(self, manifest: ModuleManifest, collector_id: str, key_version: int):
        schedule = next(
            (
                item
                for item in manifest.schedules
                if item.collector_id == collector_id and item.key_version == key_version
            ),
            None,
        )
        if schedule is None:
            raise AdmissionError("collector is not declared")
        return schedule

    def _record(
        self,
        lease: AdmissionLease | ScheduledLease | DeliveryLease,
        identity: RunIdentity,
        *,
        view: InvocationView | None = None,
        capability_id: str | None = None,
        collector_id: str | None = None,
        execution: ExecutionLease | None = None,
    ) -> None:
        if lease.lease_id in self._leases:
            raise RuntimeError("lease identifier collision")
        dependencies = getattr(lease, "dependencies", ())
        self._leases[lease.lease_id] = _LeaseRecord(
            lease=lease,
            identity=identity,
            view=view,
            capability_id=capability_id,
            collector_id=collector_id,
            execution=execution,
            dependencies=dependencies,
        )

    def _require_record(
        self, lease: AdmissionLease | ScheduledLease | DeliveryLease
    ) -> _LeaseRecord:
        if not isinstance(lease, (AdmissionLease, ScheduledLease, DeliveryLease)):
            raise InvalidAdmissionLease("unrecognized lease")
        record = self._leases.get(lease.lease_id)
        if record is None or record.lease is not lease:
            raise InvalidAdmissionLease("lease object was not issued by this runtime")
        return record

    def _check_execution_proof(self, execution: ExecutionLease | None) -> None:
        if execution is None or self._execution_claim_prover is None:
            raise AdmissionError("scheduler claim proof is unavailable")
        try:
            current = self._execution_claim_prover(execution)
        except Exception as exc:
            raise AdmissionError("scheduler claim proof failed") from exc
        if current is not True:
            raise AdmissionError("scheduler claim is no longer current")

    def _now_utc(self) -> datetime:
        value = self._utc_clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise AdmissionError("UTC clock returned an invalid timestamp")
        return value.astimezone(UTC)

    def _on_invocation_release(self, view: InvocationView) -> None:
        lease_id = self._view_leases.get(view.invocation_id)
        if lease_id is not None:
            self._forget(lease_id)

    def _forget(self, lease_id: str) -> None:
        record = self._leases.pop(lease_id, None)
        if (
            record is not None
            and record.view is not None
            and self._view_leases.get(record.view.invocation_id) == lease_id
        ):
            self._view_leases.pop(record.view.invocation_id, None)


async def _shield_abort(
    abort: Callable[[SendApproval, str], Awaitable[None]],
    approval: SendApproval,
    reason: str,
) -> None:
    task = asyncio.create_task(
        abort(approval, reason), name="admission-abort-before-send"
    )
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError


def _close_awaitable(work: Awaitable[object] | None) -> None:
    close = getattr(work, "close", None)
    if callable(close):
        close()
