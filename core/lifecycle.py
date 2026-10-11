"""Module lifecycle gates backed by immutable Registry snapshots."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from math import isfinite
from time import monotonic
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4

from yomihime_game_link_sdk.contexts import InvocationView
from yomihime_game_link_sdk.declarations import ModuleManifest
from yomihime_game_link_sdk.services import (
    CapabilityHealth,
    HealthReport,
    HealthStatus,
    ModuleHandlers,
    ModuleInstance,
)

from ..core.contracts.administration import ModuleHealth, ModuleLifecycle, ModuleStatus
from ..core.contracts.validation_boundary import validate_contract
from .admission import AdmissionController, AdmissionError
from .context_issuer import ContextIssuer, InvalidInvocation
from .ports import AdmissionPort, ExecutionLease, RunIdentity
from .registry import Registry, RegistryError
from .task_scope import (
    ScopeStaleError,
    ScopeStopTimeout,
    TaskScope,
)


class LifecycleError(RuntimeError):
    """Base lifecycle gate failure."""


class ModuleNotFound(LifecycleError):
    """The requested module is absent from the current Registry snapshot."""


class ModuleDisabled(LifecycleError):
    """The Registry's enabled intent does not admit new work."""


class ModuleStopping(LifecycleError):
    """The module is isolated while its old work is being cleaned up."""


class StaleEpochError(LifecycleError, ScopeStaleError):
    """The module epoch or Registry revision changed at a gate."""


class LifecycleStopTimeout(LifecycleError, ScopeStopTimeout):
    """Cleanup exceeded its limit; the module remains isolated."""


class LifecycleOperationTimeout(LifecycleError, TimeoutError):
    """An instance lifecycle method exceeded its finite bound."""


@dataclass(frozen=True, slots=True)
class LifecycleState:
    module_id: str
    enabled: bool
    lifecycle: ModuleLifecycle
    epoch: int
    registry_revision: int
    reason_code: str | None = None
    scope: TaskScope | None = None
    identity: RunIdentity | None = None
    health_report: HealthReport | None = None
    health_revisions: Mapping[str, int] | None = None
    cleanup_pending: bool = False

    def __post_init__(self) -> None:
        if self.health_revisions is not None:
            revisions = dict(self.health_revisions)
            if any(
                not isinstance(key, str)
                or not key
                or type(value) is not int
                or value < 0
                for key, value in revisions.items()
            ):
                raise ValueError("health revisions must map ids to non-negative ints")
            object.__setattr__(self, "health_revisions", MappingProxyType(revisions))

    @property
    def active(self) -> bool:
        return self.lifecycle is ModuleLifecycle.ACTIVE


@dataclass(slots=True)
class _InstanceRecord:
    package_id: str
    instance: ModuleInstance
    handlers: ModuleHandlers | None
    installation_operation_id: str
    manifest: ModuleManifest | None = None
    candidate_key: tuple[str, str, str] | None = None
    candidate_cleanup_started: bool = False
    start_attempted: bool = False
    running: bool = False
    stop_completed: bool = False
    ever_active: bool = False
    setup_task: asyncio.Task[Any] | None = None
    stop_task: asyncio.Task[Any] | None = None
    service_lifetime: "_ServiceLifetime | None" = None


class _ServiceLifetime:
    """Core-private lifetime of one exact constructed instance's services.

    This is a local cleanup fence, not an authorization proof. Invocation,
    principal, admission and permission checks remain owned by their services.
    """

    __slots__ = ("candidate_key", "_active", "__weakref__")

    def __init__(self, candidate_key: tuple[str, str, str]):
        self.candidate_key = candidate_key
        self._active = True

    def check(self) -> None:
        if not self._active:
            raise InvalidInvocation()

    def revoke(self) -> None:
        self._active = False


class LifecycleController:
    """Coordinate module admission without maintaining a second Registry.

    The local map stores only lifecycle status and its current scope.  Every
    admission, await boundary, and result boundary reads a fresh Registry
    snapshot and compares both revision and module epoch.
    """

    def __init__(
        self,
        registry: Registry,
        *,
        issuer: ContextIssuer | None = None,
        admission: AdmissionPort | None = None,
        runtime_id: str | None = None,
        clock: Callable[[], float] = monotonic,
        stop_timeout: float = 5.0,
        setup_timeout: float = 30.0,
        capability_health_query: Callable[
            [str, str, CapabilityHealth, int], tuple[CapabilityHealth, int]
        ]
        | None = None,
        execution_claim_prover: Callable[[ExecutionLease], bool] | None = None,
        utc_clock: Callable[[], datetime] | None = None,
    ) -> None:
        validate_contract(capability_health_query)
        if not isinstance(registry, Registry):
            raise TypeError("registry must be a Registry")
        _finite_timeout(stop_timeout, "stop_timeout")
        _finite_positive(setup_timeout, "setup_timeout")
        if runtime_id is not None and (
            not isinstance(runtime_id, str) or not runtime_id
        ):
            raise ValueError("runtime_id must be non-empty text")
        if capability_health_query is not None and not callable(
            capability_health_query
        ):
            raise TypeError("capability_health_query must be callable")
        if utc_clock is not None and not callable(utc_clock):
            raise TypeError("utc_clock must be callable")
        self.registry = registry
        self.issuer = issuer or ContextIssuer(clock=clock)
        self._clock = clock
        self._stop_timeout = float(stop_timeout)
        self._setup_timeout = float(setup_timeout)
        self._runtime_id = runtime_id or uuid4().hex
        self._capability_health_query = capability_health_query
        self._states: dict[str, LifecycleState] = {}
        self._instances: dict[str, _InstanceRecord] = {}
        self._candidates: dict[tuple[str, str, str], list[_InstanceRecord]] = {}
        self._operations: dict[str, str] = {}
        self._epochs = {
            module_id: module.epoch
            for module_id, module in registry.snapshot().modules.items()
        }
        self.registry._bind_lifecycle_owner(self, self._is_active_module)
        if admission is not None:
            self.admission = admission
        else:
            admission_options = {} if utc_clock is None else {"utc_clock": utc_clock}
            self.admission = AdmissionController(
                registry,
                self.issuer,
                current_run=self._current_run,
                is_active=self._is_active_identity,
                health_query=self.capability_health,
                execution_claim_prover=execution_claim_prover,
                **admission_options,
            )

    def start(self, module_id: str) -> LifecycleState:
        """Legacy synchronous entry is idempotent only after real activation."""
        self._module_snapshot(module_id)
        previous = self._states.get(module_id)
        if (
            previous is not None
            and previous.lifecycle is ModuleLifecycle.ACTIVE
            and previous.identity is not None
            and self._is_active_identity(previous.identity)
        ):
            return self.state(module_id)
        raise LifecycleError("Lifecycle.start cannot bypass instance activation")

    def install_dormant(
        self,
        package_id: str,
        module_id: str,
        operation_id: str,
        instance: ModuleInstance,
        handlers: ModuleHandlers,
    ) -> None:
        """Transfer one constructed, non-started instance to Lifecycle."""
        validate_contract(instance)
        validate_contract(handlers)
        snapshot, module = self._module_snapshot(module_id)
        _operation_text(operation_id)
        if not isinstance(package_id, str) or not package_id.strip():
            raise ValueError("package_id must be non-empty text")
        if module_id != f"{package_id}/{module.manifest.module_id}":
            raise LifecycleError("package and module identity do not match")
        if not isinstance(instance, ModuleInstance):
            raise TypeError("instance must implement ModuleInstance")
        if not isinstance(handlers, ModuleHandlers):
            raise TypeError("handlers must be ModuleHandlers")
        _validate_instance_methods(instance)
        Registry._validate_handlers(module.manifest, handlers)
        state = self._states.get(module_id)
        if state is not None and state.lifecycle in (
            ModuleLifecycle.ACTIVE,
            ModuleLifecycle.STARTING,
            ModuleLifecycle.STOPPING,
        ):
            raise LifecycleError("cannot replace an instance while it is running")
        if state is not None and state.cleanup_pending:
            raise LifecycleError("cannot replace an instance while cleanup is pending")
        key = (package_id, module_id, operation_id)
        candidates = self._candidates.get(key)
        candidate = next(
            (item for item in candidates or () if item.instance is instance), None
        )
        if candidates and candidate is None:
            raise LifecycleError("candidate key belongs to another instance")
        if candidate is not None:
            if candidate.candidate_cleanup_started:
                raise LifecycleError("candidate cleanup has already started")
            if candidate.handlers is not handlers:
                raise LifecycleError("handlers do not belong to the exact candidate")
            if candidate.manifest != module.manifest:
                raise LifecycleError("candidate declaration does not match Registry")
        else:
            raise LifecycleError("instance was not adopted by Lifecycle")
        existing = self._instances.get(module_id)
        if existing is not None and existing is not candidate:
            raise LifecycleError("module already has an installed instance")
        if self._candidates_for_module(module_id, excluding=key):
            raise LifecycleError("another candidate for this module is unresolved")
        self._claim_operation(module_id, operation_id, replace_terminal=True)
        self.registry._install_lifecycle_handlers(module_id, handlers)
        self._instances[module_id] = candidate
        self._remove_candidate(key, candidate)
        latest = self.registry.snapshot()
        installed = latest.module(module_id)
        epoch = max(self._epochs.get(module_id, 0), installed.epoch)
        self._epochs[module_id] = epoch
        self._states[module_id] = LifecycleState(
            module_id=module_id,
            enabled=installed.enabled,
            lifecycle=(
                ModuleLifecycle.STOPPED
                if not installed.enabled
                else ModuleLifecycle.DISCOVERED
            ),
            epoch=epoch,
            registry_revision=latest.revision,
        )
        self._operations.pop(module_id, None)

    async def start_candidate(
        self, module_id: str, operation_id: str
    ) -> tuple[RunIdentity, HealthReport]:
        """Start the installed instance behind a closed admission gate."""
        _operation_text(operation_id)
        snapshot, module = self._module_snapshot(module_id)
        if self._candidates_for_module(module_id):
            raise LifecycleError("module has an unresolved candidate cleanup")
        self._claim_operation(module_id, operation_id)
        record = self._instances.get(module_id)
        if record is None:
            raise LifecycleError("module has no installed instance")
        previous = self._states.get(module_id)
        if previous is not None and previous.cleanup_pending:
            raise LifecycleError("old module work is still cleaning up")
        if record.start_attempted and not record.stop_completed:
            raise LifecycleError("previous instance cleanup has not completed")
        if previous is not None and previous.lifecycle in (
            ModuleLifecycle.ACTIVE,
            ModuleLifecycle.STARTING,
            ModuleLifecycle.STOPPING,
        ):
            raise LifecycleError("module is already running or changing state")
        epoch = max(self._epochs.get(module_id, 0), module.epoch) + 1
        self._epochs[module_id] = epoch
        identity = RunIdentity(self._runtime_id, module_id, epoch)
        scope = self._make_scope(identity)
        self._states[module_id] = LifecycleState(
            module_id=module_id,
            enabled=module.enabled,
            lifecycle=ModuleLifecycle.STARTING,
            epoch=epoch,
            registry_revision=snapshot.revision,
            scope=scope,
            identity=identity,
        )
        record.start_attempted = True
        record.stop_completed = False
        try:
            await self._run_instance_method(record, "start", operation_id)
            self._assert_starting_candidate(module_id, operation_id, identity)
            record.running = True
            self._assert_operation(module_id, operation_id)
            health = await self._run_instance_method(
                record, "check_health", operation_id
            )
            self._assert_starting_candidate(module_id, operation_id, identity)
            self._assert_operation(module_id, operation_id)
            _validate_health(module.manifest, health)
            revisions = {capability_id: 1 for capability_id in health.capabilities}
            self._states[module_id] = replace_state(
                self._states[module_id],
                health_report=health,
                health_revisions=revisions,
                registry_revision=self.registry.snapshot().revision,
            )
            return identity, health
        except BaseException as exc:
            current = self._states.get(module_id)
            if current is not None and current.identity is identity:
                self.admission.close(identity, "start_failed")
                scope.cancel()
                pending = bool(scope.tasks) or (
                    record.setup_task is not None and not record.setup_task.done()
                )
                self._states[module_id] = replace_state(
                    current,
                    lifecycle=ModuleLifecycle.FAILED,
                    reason_code=(
                        "stop_cancelled"
                        if isinstance(exc, asyncio.CancelledError)
                        else "operation_failed"
                    ),
                    cleanup_pending=pending,
                )
                if not record.stop_completed and not pending:
                    try:
                        await self._run_instance_method(
                            record,
                            "stop",
                            operation_id,
                            timeout=self._stop_timeout,
                        )
                    except BaseException:
                        pass
                    else:
                        record.running = False
                        record.start_attempted = False
                        record.stop_completed = True
                current = self._states.get(module_id)
                if current is not None and current.identity is identity:
                    self._states[module_id] = replace_state(
                        current,
                        cleanup_pending=(
                            current.cleanup_pending
                            or self._instance_cleanup_pending(module_id)
                        ),
                    )
            raise

    def quiesce(self, module_id: str, operation_id: str, reason: str) -> RunIdentity:
        """Close admission and invalidate the current epoch synchronously."""
        _operation_text(operation_id)
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be non-empty text")
        snapshot, module = self._module_snapshot(module_id)
        state = self._states.get(module_id)
        if state is None:
            raise LifecycleError("module has no Lifecycle state")
        self._claim_operation(module_id, operation_id, replace_terminal=True)
        if state.lifecycle is ModuleLifecycle.ACTIVE and state.identity is not None:
            self.admission.close(state.identity, reason)
            if state.scope is not None:
                state.scope.cancel()
        elif state.lifecycle not in (
            ModuleLifecycle.STOPPING,
            ModuleLifecycle.FAILED,
            ModuleLifecycle.STOPPED,
            ModuleLifecycle.DISCOVERED,
        ):
            raise LifecycleError("module cannot be quiesced from its current state")

        # Repeated stop after a failed cleanup keeps the already-invalidated
        # identity and the exact scope/instance references.
        if state.lifecycle in (ModuleLifecycle.STOPPING, ModuleLifecycle.FAILED):
            if (
                state.identity is not None
                and state.identity.module_epoch == module.epoch
            ):
                self._states[module_id] = replace_state(
                    state,
                    lifecycle=ModuleLifecycle.STOPPING,
                    reason_code=None,
                    enabled=module.enabled,
                    registry_revision=snapshot.revision,
                )
                return state.identity

        epoch = max(self._epochs.get(module_id, 0), module.epoch) + 1
        self._epochs[module_id] = epoch
        invalidated = RunIdentity(self._runtime_id, module_id, epoch)
        published = self.registry._publish_lifecycle_projection(
            module_id,
            enabled=module.enabled,
            epoch=epoch,
            expected_revision=snapshot.revision,
        )
        self._states[module_id] = LifecycleState(
            module_id=module_id,
            enabled=module.enabled,
            lifecycle=ModuleLifecycle.STOPPING,
            epoch=epoch,
            registry_revision=published.revision,
            reason_code=None,
            scope=state.scope,
            identity=invalidated,
            health_report=state.health_report,
            health_revisions=state.health_revisions,
            cleanup_pending=state.cleanup_pending,
        )
        return invalidated

    async def stop_candidate(
        self, module_id: str, operation_id: str, timeout: float
    ) -> LifecycleState:
        """Stop the old scope and instance within one finite cleanup budget."""
        _finite_timeout(timeout, "timeout")
        self._module_snapshot(module_id)
        self._claim_operation(module_id, operation_id, replace_terminal=True)
        state = self._states.get(module_id)
        if state is None or state.lifecycle not in (
            ModuleLifecycle.STOPPING,
            ModuleLifecycle.FAILED,
            ModuleLifecycle.STOPPED,
        ):
            raise LifecycleError("module must be quiesced before stopping")
        deadline = self._clock() + float(timeout)
        try:
            if state.scope is not None:
                remaining = max(0.0, deadline - self._clock())
                await state.scope.stop(remaining)
            record = self._instances.get(module_id)
            if record is not None and not record.stop_completed:
                remaining = max(0.0, deadline - self._clock())
                if record.setup_task is not None and not record.setup_task.done():
                    record.setup_task.cancel()
                    await _wait_owned_task(record.setup_task, remaining)
                    self._assert_operation(module_id, operation_id)
                await self._run_instance_method(
                    record, "stop", operation_id, timeout=remaining
                )
                record.running = False
                record.start_attempted = False
                record.stop_completed = True
            snapshot, module = self._module_snapshot(module_id)
            stopped = replace_state(
                self._states[module_id],
                enabled=module.enabled,
                lifecycle=ModuleLifecycle.STOPPED,
                epoch=module.epoch,
                registry_revision=snapshot.revision,
                reason_code=None,
                scope=None,
                cleanup_pending=False,
            )
            self._states[module_id] = stopped
            self._operations.pop(module_id, None)
            return stopped
        except asyncio.CancelledError:
            self._mark_cleanup_failed(module_id, "stop_cancelled")
            raise
        except (ScopeStopTimeout, LifecycleOperationTimeout):
            self._mark_cleanup_failed(module_id, "stop_timeout")
            raise LifecycleStopTimeout(
                "module cleanup exceeded its finite bound"
            ) from None
        except BaseException:
            self._mark_cleanup_failed(module_id, "stop_failed")
            raise

    async def rollback_unpublished_candidate(
        self,
        module_id: str,
        operation_id: str,
        timeout: float,
        *,
        installation_operation_id: str,
    ) -> bool:
        """Stop and release one exact instance that never became ACTIVE."""
        _operation_text(operation_id)
        _operation_text(installation_operation_id)
        _finite_timeout(timeout, "timeout")
        snapshot, module = self._module_snapshot(module_id)
        record = self._instances.get(module_id)
        if record is None:
            return False
        if record.installation_operation_id != installation_operation_id:
            raise LifecycleError("installation operation does not own this instance")
        if record.ever_active:
            raise LifecycleError("a previously ACTIVE instance cannot be rolled back")
        state = self._states.get(module_id)
        if state is None:
            raise LifecycleError("module has no Lifecycle state")
        if state.lifecycle is ModuleLifecycle.ACTIVE:
            raise LifecycleError("an ACTIVE instance cannot be rolled back")
        if state.lifecycle not in (
            ModuleLifecycle.STARTING,
            ModuleLifecycle.STOPPING,
            ModuleLifecycle.FAILED,
            ModuleLifecycle.STOPPED,
            ModuleLifecycle.DISCOVERED,
        ):
            raise LifecycleError("module state cannot be rolled back")
        if module.enabled:
            raise LifecycleError("Registry projection must be disabled to roll back")

        current_operation = self._operations.get(module_id)
        if current_operation is None:
            self._operations[module_id] = operation_id
        elif current_operation != operation_id:
            raise LifecycleError("another module operation owns this rollback")

        current_identity = state.identity
        if current_identity is not None:
            try:
                self.admission.close(current_identity, "unpublished_rollback")
            except BaseException:
                return self._mark_rollback_pending(module_id, "rollback_gate_failed")
        if state.scope is not None:
            state.scope.cancel()
        if record.setup_task is not None and not record.setup_task.done():
            record.setup_task.cancel()

        needs_epoch_invalidation = state.lifecycle is ModuleLifecycle.STARTING or (
            current_identity is not None
            and current_identity.module_epoch > module.epoch
        )
        if needs_epoch_invalidation:
            epoch = max(self._epochs.get(module_id, 0), module.epoch, state.epoch) + 1
            try:
                published = self.registry._publish_lifecycle_projection(
                    module_id,
                    enabled=False,
                    epoch=epoch,
                    expected_revision=snapshot.revision,
                )
            except (LifecycleError, RegistryError):
                return self._mark_rollback_pending(
                    module_id, "rollback_projection_failed"
                )
            self._epochs[module_id] = epoch
            current_identity = RunIdentity(self._runtime_id, module_id, epoch)
            registry_revision = published.revision
        else:
            epoch = max(self._epochs.get(module_id, 0), module.epoch, state.epoch)
            registry_revision = snapshot.revision
        stopping = replace_state(
            state,
            enabled=False,
            lifecycle=ModuleLifecycle.STOPPING,
            epoch=epoch,
            registry_revision=registry_revision,
            identity=current_identity,
            reason_code="unpublished_rollback",
        )
        self._states[module_id] = stopping

        loop = asyncio.get_running_loop()
        deadline = loop.time() + float(timeout)

        def remaining() -> float:
            return max(0.0, deadline - loop.time())

        try:
            if stopping.scope is not None:
                await stopping.scope.stop(remaining())
            setup_task = record.setup_task
            if setup_task is not None and not setup_task.done():
                await _wait_owned_task(setup_task, remaining())
            stop_task = record.stop_task
            if stop_task is not None and not stop_task.done():
                await _wait_owned_task(stop_task, remaining())
            if (
                stop_task is not None
                and stop_task.done()
                and not _task_failed(stop_task)
            ):
                record.stop_completed = True
            if not record.stop_completed:
                await self._run_instance_method(
                    record,
                    "stop",
                    operation_id,
                    timeout=remaining(),
                )
            record.running = False
            record.start_attempted = False

            current_module = self.registry.snapshot().module(module_id)
            if current_module.enabled:
                return self._mark_rollback_pending(
                    module_id, "rollback_registry_reenabled"
                )
            if self._instances.get(module_id) is not record:
                return self._mark_rollback_pending(
                    module_id, "rollback_instance_changed"
                )
            if self._operations.get(module_id) != operation_id:
                return self._mark_rollback_pending(
                    module_id, "rollback_operation_changed"
                )
            if stopping.scope is not None and (
                stopping.scope.tasks or stopping.scope.cleanup_pending
            ):
                return self._mark_rollback_pending(module_id, "rollback_scope_pending")
            if any(
                task is not None and not task.done()
                for task in (record.setup_task, record.stop_task)
            ):
                return self._mark_rollback_pending(module_id, "rollback_task_pending")
            if not record.stop_completed:
                return self._mark_rollback_pending(
                    module_id, "rollback_stop_incomplete"
                )
        except asyncio.CancelledError:
            self._mark_rollback_pending(module_id, "rollback_cancelled")
            raise
        except BaseException:
            return self._mark_rollback_pending(module_id, "rollback_failed")

        self._instances.pop(module_id, None)
        if record.service_lifetime is not None:
            record.service_lifetime.revoke()
        self._states.pop(module_id, None)
        if self._operations.get(module_id) == operation_id:
            self._operations.pop(module_id, None)
        return True

    def _mark_rollback_pending(self, module_id: str, reason_code: str) -> bool:
        try:
            self._mark_cleanup_failed(module_id, reason_code)
        except BaseException:
            pass
        state = self._states.get(module_id)
        if state is not None:
            self._states[module_id] = replace_state(
                state,
                enabled=False,
                lifecycle=ModuleLifecycle.FAILED,
                reason_code=reason_code,
                cleanup_pending=True,
            )
        return False

    def publish_committed_intent(
        self,
        module_id: str,
        operation_id: str,
        identity: RunIdentity,
        enabled: bool,
        expected_registry_revision: int,
    ) -> ModuleStatus:
        """Publish a durable intent projection and open its gate if enabled."""
        _operation_text(operation_id)
        if not isinstance(identity, RunIdentity):
            raise TypeError("identity must be a Lifecycle-issued RunIdentity")
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be bool")
        self._assert_operation(module_id, operation_id)
        state = self._states.get(module_id)
        if state is None or state.identity is not identity:
            raise LifecycleError("identity is not the current Lifecycle candidate")
        if enabled:
            if (
                state.lifecycle is not ModuleLifecycle.STARTING
                or state.health_report is None
            ):
                raise LifecycleError("candidate has not completed start and health")
        elif state.lifecycle not in (
            ModuleLifecycle.STOPPING,
            ModuleLifecycle.FAILED,
            ModuleLifecycle.STOPPED,
        ):
            raise LifecycleError("module must be quiesced before disabling")
        try:
            snapshot = self.registry._publish_lifecycle_projection(
                module_id,
                enabled=enabled,
                epoch=identity.module_epoch,
                expected_revision=expected_registry_revision,
            )
        except RegistryError as exc:
            if enabled:
                self.admission.close(identity, "projection_conflict")
                self._states[module_id] = replace_state(
                    state,
                    lifecycle=ModuleLifecycle.FAILED,
                    reason_code="revision_conflict",
                    cleanup_pending=(
                        (state.scope is not None and state.scope.cleanup_pending)
                        or self._instance_cleanup_pending(module_id)
                    ),
                )
            raise LifecycleError("Registry projection publication failed") from exc
        if enabled:
            active = replace_state(
                state,
                enabled=True,
                lifecycle=ModuleLifecycle.ACTIVE,
                registry_revision=snapshot.revision,
                reason_code=None,
            )
            self._states[module_id] = active
            try:
                self.admission.activate(identity, active.health_report)
            except BaseException:
                self.admission.close(identity, "activation_failed")
                self._states[module_id] = replace_state(
                    active,
                    lifecycle=ModuleLifecycle.FAILED,
                    reason_code="operation_failed",
                    cleanup_pending=(
                        (active.scope is not None and active.scope.cleanup_pending)
                        or self._instance_cleanup_pending(module_id)
                    ),
                )
                raise
            record = self._instances.get(module_id)
            if record is not None:
                record.ever_active = True
            self._operations.pop(module_id, None)
        else:
            self._states[module_id] = replace_state(
                state,
                enabled=False,
                lifecycle=(
                    ModuleLifecycle.STOPPED
                    if state.lifecycle is ModuleLifecycle.STOPPED
                    else ModuleLifecycle.STOPPING
                ),
                registry_revision=snapshot.revision,
            )
        self.registry._notify()
        return self.status(module_id)

    async def stop(
        self, module_id: str, *, timeout: float | None = None
    ) -> LifecycleState:
        """Compatibility local disable; production coordinates durable intent outside."""
        self._module_snapshot(module_id)
        operation_id = uuid4().hex
        identity = self.quiesce(module_id, operation_id, "legacy_stop")
        self.publish_committed_intent(
            module_id,
            operation_id,
            identity,
            False,
            self.registry.snapshot().revision,
        )
        return await self.stop_candidate(
            module_id,
            operation_id,
            self._stop_timeout if timeout is None else timeout,
        )

    def guard(
        self,
        module_id: str,
        *,
        epoch: int | None = None,
        registry_revision: int | None = None,
        invocation: InvocationView | None = None,
    ) -> LifecycleState:
        """Check Lifecycle liveness; invocation authority is owned by Admission."""

        validate_contract(invocation)
        snapshot, module = self._module_snapshot(module_id)
        state = self._states.get(module_id)
        if epoch is not None and module.epoch != epoch:
            raise StaleEpochError("module epoch is stale")
        if invocation is not None and (
            invocation.module_id != module_id or invocation.module_epoch != module.epoch
        ):
            raise StaleEpochError("invocation does not match current module")
        if state is None or state.lifecycle is not ModuleLifecycle.ACTIVE:
            if state is not None and state.lifecycle is ModuleLifecycle.STOPPING:
                raise ModuleStopping("module is stopping")
            raise ModuleDisabled("module is not active")
        if not module.enabled or state.identity is None:
            raise ModuleDisabled("module is disabled")
        expected_epoch = state.identity.module_epoch if epoch is None else epoch
        if (
            module.epoch != expected_epoch
            or state.identity.module_epoch != module.epoch
        ):
            raise StaleEpochError("module run identity is stale")
        if invocation is not None:
            try:
                self.issuer.require(invocation)
                lease = self.issuer.lease_for(invocation)
                if lease is None:
                    raise InvalidInvocation("invocation has no admission lease")
                self.admission.check(lease)  # type: ignore[arg-type]
            except (InvalidInvocation, AdmissionError) as exc:
                raise StaleEpochError("invocation is no longer admitted") from exc
        # registry_revision remains accepted for compatibility, but is not an
        # execution fence: unrelated directory publication cannot stale a run.
        del snapshot, registry_revision
        return replace(
            state,
            enabled=module.enabled,
            registry_revision=self.registry.snapshot().revision,
        )

    def check_before_await(self, module_id: str, **kwargs: Any) -> LifecycleState:
        return self.guard(module_id, **kwargs)

    def check_after_await(self, module_id: str, **kwargs: Any) -> LifecycleState:
        return self.guard(module_id, **kwargs)

    ensure_current = guard
    check_commit = guard

    def scope(self, module_id: str) -> TaskScope:
        state = self.guard(module_id)
        if state.scope is None:  # pragma: no cover - active always has a scope
            raise LifecycleError("active module has no task scope")
        return state.scope

    def current_identity(self, module_id: str) -> RunIdentity | None:
        """Return the current run identity for Core-owned admission wiring."""
        state = self._states.get(module_id)
        return None if state is None else state.identity

    def instance(self, module_id: str) -> ModuleInstance:
        """Return the Lifecycle-owned installed instance to trusted Core code."""
        try:
            return self._instances[module_id].instance
        except KeyError as exc:
            raise ModuleNotFound("module has no installed instance") from exc

    def detach_stopped(self, module_id: str) -> None:
        """Detach only a fully drained exact instance; retain monotonic epochs."""
        state = self.state(module_id)
        record = self._instances.get(module_id)
        if (
            state.enabled
            or state.lifecycle is not ModuleLifecycle.STOPPED
            or state.cleanup_pending
            or state.scope is not None
            or self._candidates_for_module(module_id)
            or (record is not None and not record.stop_completed)
        ):
            raise LifecycleError("module cleanup has not completed")
        self.registry._detach_lifecycle_module(self, module_id)
        self._instances.pop(module_id, None)
        if record is not None and record.service_lifetime is not None:
            record.service_lifetime.revoke()
        self._states.pop(module_id, None)
        self._operations.pop(module_id, None)

    def restore_registration(self, package_id, manifest, handlers):
        module_id = f"{package_id}/{manifest.module_id}"
        if module_id in self._instances or module_id in self._states:
            raise LifecycleError("previous module ownership remains")
        return self.registry._restore_lifecycle_module(
            self, package_id, manifest, handlers
        )

    def handlers(self, module_id: str) -> ModuleHandlers:
        """Return handlers captured from the installed instance."""
        try:
            handlers = self._instances[module_id].handlers
        except KeyError as exc:
            raise ModuleNotFound("module has no installed handlers") from exc
        if handlers is None:  # pragma: no cover - installed records are validated
            raise ModuleNotFound("module has no installed handlers")
        return handlers

    def service_lifetime(self, module_id: str) -> _ServiceLifetime:
        record = self._instances.get(module_id)
        if record is None or record.service_lifetime is None:
            raise ModuleNotFound("module has no installed service lifetime")
        record.service_lifetime.check()
        return record.service_lifetime

    def owns_service_lifetime(self, lifetime: _ServiceLifetime) -> bool:
        return any(
            record.service_lifetime is lifetime for record in self._instances.values()
        ) or any(
            record.service_lifetime is lifetime
            for records in self._candidates.values()
            for record in records
        )

    def adopt_candidate(
        self,
        package_id: str,
        manifest: ModuleManifest,
        operation_id: str,
        instance: ModuleInstance,
        *,
        service_lifetime: _ServiceLifetime | None = None,
    ) -> ModuleHandlers:
        """Own a factory result before calling user code or validating handlers."""
        validate_contract(manifest)
        validate_contract(instance)
        if not isinstance(package_id, str) or not package_id.strip():
            raise ValueError("package_id must be non-empty text")
        if not isinstance(manifest, ModuleManifest):
            raise TypeError("manifest must be a ModuleManifest")
        _operation_text(operation_id)
        module_id = f"{package_id}/{manifest.module_id}"
        key = (package_id, module_id, operation_id)
        if service_lifetime is not None and (
            type(service_lifetime) is not _ServiceLifetime
            or service_lifetime.candidate_key != key
        ):
            raise LifecycleError("service lifetime does not belong to candidate")
        if self._is_instance_owned(instance):
            raise LifecycleError("instance is already owned by Lifecycle")
        record = _InstanceRecord(
            package_id=package_id,
            instance=instance,
            handlers=None,
            installation_operation_id=operation_id,
            manifest=manifest,
            candidate_key=key,
            service_lifetime=service_lifetime or _ServiceLifetime(key),
        )
        # Ownership is established before even looking up instance.handlers.
        self._candidates.setdefault(key, []).append(record)
        try:
            if not isinstance(instance, ModuleInstance):
                raise TypeError("instance must implement ModuleInstance")
            handler_method = getattr(instance, "handlers", None)
            if not callable(handler_method):
                raise TypeError("instance handlers method is unavailable")
            handlers = handler_method()
            record.handlers = handlers
            if not isinstance(handlers, ModuleHandlers):
                _close_unstarted_awaitable(handlers)
                raise TypeError("instance handlers must return ModuleHandlers")
            _validate_instance_methods(instance)
            Registry._validate_handlers(manifest, handlers)
            return handlers
        except BaseException:
            # The exact record remains owned so the caller can always discard it.
            raise

    async def discard_candidate(
        self,
        package_id: str,
        module_id: str,
        operation_id: str,
        timeout: float,
    ) -> bool:
        """Boundedly stop unregistered factory results without Registry lookup."""
        _finite_timeout(timeout, "timeout")
        if not isinstance(package_id, str) or not package_id.strip():
            raise ValueError("package_id must be non-empty text")
        if not isinstance(module_id, str) or not module_id.startswith(f"{package_id}/"):
            raise ValueError("module_id must be a global id for package_id")
        _operation_text(operation_id)
        key = (package_id, module_id, operation_id)
        candidates = self._candidates.get(key)
        if not candidates:
            # A successful install transfers the record out of this key. In
            # particular, a stale key can never stop its now-active instance.
            return True
        all_stopped = True
        for record in tuple(candidates):
            if self._instances.get(module_id) is record:
                self._remove_candidate(key, record)
                continue
            if not await self._discard_candidate_record(record, timeout):
                all_stopped = False
        return all_stopped

    async def _discard_candidate_record(
        self, record: _InstanceRecord, timeout: float
    ) -> bool:
        key = record.candidate_key
        if key is None or not any(
            item is record for item in self._candidates.get(key, ())
        ):
            return True
        record.candidate_cleanup_started = True
        task = record.stop_task
        if task is None or (task.done() and _task_failed(task)):
            if task is not None:
                _consume_task(task)
                record.stop_task = None
            try:
                method = getattr(record.instance, "stop", None)
            except BaseException:
                return False
            if not callable(method):
                return False
            try:
                awaitable = method()
            except BaseException:
                return False
            if not asyncio.iscoroutine(awaitable) and not hasattr(
                awaitable, "__await__"
            ):
                return False
            try:
                task = asyncio.create_task(
                    awaitable,
                    name=f"candidate-stop:{key[1]}:{key[2][:8]}",
                )
            except BaseException:
                _close_unstarted_awaitable(awaitable)
                return False
            record.stop_task = task

        try:
            completed = await _wait_candidate_stop(
                task,
                timeout,
                cleanup_timeout=min(
                    self._stop_timeout, TaskScope.DEFAULT_CLEANUP_TIMEOUT
                ),
            )
        except asyncio.CancelledError:
            if task.done() and not _task_failed(task):
                if record.service_lifetime is not None:
                    record.service_lifetime.revoke()
                self._remove_candidate(key, record)
            raise
        except BaseException:
            return False
        if not completed:
            return False
        if record.service_lifetime is not None:
            record.service_lifetime.revoke()
        self._remove_candidate(key, record)
        return True

    def _is_instance_owned(self, instance: object) -> bool:
        if any(record.instance is instance for record in self._instances.values()):
            return True
        return any(
            record.instance is instance
            for records in self._candidates.values()
            for record in records
        )

    def _candidates_for_module(
        self,
        module_id: str,
        *,
        excluding: tuple[str, str, str] | None = None,
    ) -> tuple[_InstanceRecord, ...]:
        return tuple(
            record
            for key, records in self._candidates.items()
            if key[1] == module_id and key != excluding
            for record in records
        )

    def _remove_candidate(
        self, key: tuple[str, str, str], record: _InstanceRecord
    ) -> None:
        records = self._candidates.get(key)
        if records is None:
            return
        self._candidates[key] = [item for item in records if item is not record]
        if not self._candidates[key]:
            self._candidates.pop(key, None)

    def capability_health(
        self, module_id: str, capability_id: str
    ) -> tuple[CapabilityHealth, int]:
        """Read one capability's effective HealthIndex value and revision.

        The Lifecycle-owned startup report is the fallback source. A Core
        HealthIndex may be injected to combine dependency/config/provider state;
        it returns a projection for this query rather than a second mutable
        lifecycle state store.
        """
        state = self._states.get(module_id)
        if state is None or state.health_report is None:
            raise LifecycleError("module has no health report")
        try:
            health = state.health_report.capabilities[capability_id]
            revisions = state.health_revisions or {}
            revision = revisions[capability_id]
        except KeyError as exc:
            raise LifecycleError("capability has no health entry") from exc
        if self._capability_health_query is not None:
            value = self._capability_health_query(
                module_id, capability_id, health, revision
            )
        else:
            value = (health, revision)
        if (
            not isinstance(value, tuple)
            or len(value) != 2
            or not isinstance(value[0], CapabilityHealth)
            or isinstance(value[1], bool)
            or not isinstance(value[1], int)
            or value[1] < 0
        ):
            raise LifecycleError("health provider returned an invalid value")
        return value

    def _current_run(self, module_id: str) -> RunIdentity | None:
        return self.current_identity(module_id)

    def _is_active_identity(self, identity: RunIdentity) -> bool:
        state = self._states.get(identity.module_id)
        return (
            state is not None
            and state.lifecycle is ModuleLifecycle.ACTIVE
            and state.identity is identity
            and not state.cleanup_pending
        )

    def _is_active_module(self, module_id: str) -> bool:
        state = self._states.get(module_id)
        return state is not None and state.lifecycle is ModuleLifecycle.ACTIVE

    def _make_scope(self, identity: RunIdentity) -> TaskScope:
        return validate_contract(
            TaskScope(
                identity.module_id,
                identity.module_epoch,
                current_check=lambda: self.guard(
                    identity.module_id, epoch=identity.module_epoch
                ),
                cleanup_timeout=self._stop_timeout,
                on_cleanup_timeout=lambda reason: self._scope_cleanup_timeout(
                    identity.module_id, identity, reason
                ),
            )
        )

    async def _run_instance_method(
        self,
        record: _InstanceRecord,
        method_name: str,
        operation_id: str,
        *,
        timeout: float | None = None,
    ) -> Any:
        module_id = next(
            (key for key, value in self._instances.items() if value is record),
            None,
        )
        if module_id is None:
            raise LifecycleError("instance is no longer owned by Lifecycle")
        self._assert_operation(module_id, operation_id)
        task_attr = "stop_task" if method_name == "stop" else "setup_task"
        task = getattr(record, task_attr)
        if task is None or task.done():
            method = getattr(record.instance, method_name, None)
            if not callable(method):
                raise LifecycleError(f"instance {method_name} method is unavailable")
            try:
                awaitable = method()
            except BaseException:
                raise
            if not asyncio.iscoroutine(awaitable) and not hasattr(
                awaitable, "__await__"
            ):
                raise TypeError(f"instance {method_name} must return an awaitable")
            task = asyncio.create_task(
                awaitable,
                name=f"module:{module_id}:{method_name}:{operation_id[:8]}",
            )
            setattr(record, task_attr, task)
            task.add_done_callback(
                lambda completed, owner=record, attr=task_attr: _clear_task_ref(
                    owner, attr, completed
                )
            )
        bound = self._setup_timeout if timeout is None else float(timeout)
        try:
            _, pending = await asyncio.wait({task}, timeout=bound)
        except asyncio.CancelledError:
            task.cancel()
            try:
                await _wait_owned_task(
                    task, min(self._stop_timeout, TaskScope.DEFAULT_CLEANUP_TIMEOUT)
                )
            except LifecycleOperationTimeout:
                pass
            else:
                if method_name == "stop" and task.done() and not _task_failed(task):
                    record.stop_completed = True
            raise
        if pending:
            task.cancel()
            cleanup_bound = min(self._stop_timeout, TaskScope.DEFAULT_CLEANUP_TIMEOUT)
            try:
                await _wait_owned_task(task, cleanup_bound)
            except LifecycleOperationTimeout:
                raise LifecycleOperationTimeout(
                    f"instance {method_name} ignored bounded cancellation"
                ) from None
            if method_name == "stop" and task.done() and not _task_failed(task):
                record.stop_completed = True
            _consume_task(task)
            raise LifecycleOperationTimeout(
                f"instance {method_name} exceeded its finite bound"
            )
        self._assert_operation(module_id, operation_id)
        result = task.result()
        if method_name == "stop":
            record.stop_completed = True
        return result

    def _claim_operation(
        self, module_id: str, operation_id: str, *, replace_terminal: bool = False
    ) -> None:
        _operation_text(operation_id)
        current = self._operations.get(module_id)
        if current is None or current == operation_id:
            self._operations[module_id] = operation_id
            return
        state = self._states.get(module_id)
        if replace_terminal and (
            state is None
            or state.lifecycle
            in (
                ModuleLifecycle.DISCOVERED,
                ModuleLifecycle.STOPPED,
                ModuleLifecycle.FAILED,
            )
        ):
            self._operations[module_id] = operation_id
            return
        raise LifecycleError("another module operation owns this Lifecycle state")

    def _assert_operation(self, module_id: str, operation_id: str) -> None:
        if self._operations.get(module_id) != operation_id:
            raise LifecycleError("module operation was superseded")

    def _assert_starting_candidate(
        self, module_id: str, operation_id: str, identity: RunIdentity
    ) -> None:
        self._assert_operation(module_id, operation_id)
        state = self._states.get(module_id)
        if (
            state is None
            or state.identity is not identity
            or state.lifecycle is not ModuleLifecycle.STARTING
        ):
            raise LifecycleError("module candidate is no longer starting")

    def _mark_cleanup_failed(self, module_id: str, reason_code: str) -> None:
        state = self._states.get(module_id)
        if state is None:
            return
        if state.identity is not None:
            self.admission.close(state.identity, reason_code)
        self._states[module_id] = replace_state(
            state,
            lifecycle=ModuleLifecycle.FAILED,
            reason_code=reason_code,
            cleanup_pending=(
                (state.scope is not None and state.scope.cleanup_pending)
                or self._instance_cleanup_pending(module_id)
            ),
        )

    def _instance_cleanup_pending(self, module_id: str) -> bool:
        record = self._instances.get(module_id)
        return bool(
            record is not None
            and (
                not record.stop_completed
                or any(
                    task is not None and not task.done()
                    for task in (record.setup_task, record.stop_task)
                )
            )
        )

    def _scope_cleanup_timeout(
        self, module_id: str, identity: RunIdentity, reason: str
    ) -> None:
        state = self._states.get(module_id)
        if state is None or state.scope is None or state.identity is not identity:
            return
        self.admission.close(identity, reason)
        try:
            snapshot, module = self._module_snapshot(module_id)
            epoch = max(self._epochs.get(module_id, 0), module.epoch) + 1
            self._epochs[module_id] = epoch
            isolated = RunIdentity(self._runtime_id, module_id, epoch)
            updated = self.registry._publish_lifecycle_projection(
                module_id,
                enabled=module.enabled,
                epoch=epoch,
                expected_revision=snapshot.revision,
            )
            revision = updated.revision
        except (LifecycleError, RegistryError):
            isolated = identity
            try:
                revision = self.registry.snapshot().revision
            except Exception:
                revision = state.registry_revision
        self._states[module_id] = replace_state(
            state,
            lifecycle=ModuleLifecycle.FAILED,
            identity=isolated,
            epoch=isolated.module_epoch,
            registry_revision=revision,
            reason_code="stop_timeout",
            cleanup_pending=True,
        )

    def state(self, module_id: str) -> LifecycleState:
        snapshot, module = self._module_snapshot(module_id)
        state = self._states.get(module_id)
        if state is None:
            lifecycle = (
                ModuleLifecycle.STOPPED
                if not module.enabled
                else ModuleLifecycle.DISCOVERED
            )
            return LifecycleState(
                module_id,
                module.enabled,
                lifecycle,
                module.epoch,
                snapshot.revision,
            )
        return replace_state(
            state,
            enabled=module.enabled,
            registry_revision=snapshot.revision,
            epoch=max(state.epoch, module.epoch),
        )

    def status(self, module_id: str) -> ModuleStatus:
        state = self.state(module_id)
        if state.lifecycle is ModuleLifecycle.FAILED:
            health = ModuleHealth.MODULE_ERROR
        elif (
            state.lifecycle is ModuleLifecycle.ACTIVE
            and state.health_report is not None
        ):
            health = (
                ModuleHealth.HEALTHY
                if all(
                    item.status is HealthStatus.AVAILABLE
                    for item in state.health_report.capabilities.values()
                )
                else ModuleHealth.DEGRADED
            )
        else:
            health = ModuleHealth.DEGRADED
        return ModuleStatus(
            module_id=state.module_id,
            enabled=state.enabled,
            lifecycle=state.lifecycle,
            health=health,
            epoch=state.epoch,
            registry_revision=state.registry_revision,
            reason_code=state.reason_code,
        )

    def _module_snapshot(self, module_id: str):
        snapshot = self.registry.snapshot()
        try:
            return snapshot, snapshot.module(module_id)
        except RegistryError as exc:
            raise ModuleNotFound(str(exc)) from exc


def replace_state(state: LifecycleState, **changes: Any) -> LifecycleState:
    values = {
        "module_id": state.module_id,
        "enabled": state.enabled,
        "lifecycle": state.lifecycle,
        "epoch": state.epoch,
        "registry_revision": state.registry_revision,
        "reason_code": state.reason_code,
        "scope": state.scope,
        "identity": state.identity,
        "health_report": state.health_report,
        "health_revisions": state.health_revisions,
        "cleanup_pending": state.cleanup_pending,
    }
    values.update(changes)
    return LifecycleState(**values)


__all__ = [
    "LifecycleController",
    "LifecycleError",
    "LifecycleState",
    "LifecycleStopTimeout",
    "ModuleDisabled",
    "ModuleHealth",
    "ModuleLifecycle",
    "ModuleNotFound",
    "ModuleStopping",
    "StaleEpochError",
    "TaskScope",
]


def _finite_timeout(value: float, field: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{field} must be finite non-negative seconds")


def _finite_positive(value: float, field: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{field} must be finite positive seconds")


def _operation_text(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError("operation_id must be bounded non-empty text")


def _validate_instance_methods(instance: ModuleInstance) -> None:
    validate_contract(instance)
    for name in ("handlers", "start", "stop", "check_health"):
        try:
            method = getattr(instance, name)
        except Exception as exc:
            raise TypeError(f"instance {name} method is unavailable") from exc
        if not callable(method):
            raise TypeError(f"instance {name} method is unavailable")


def _validate_health(manifest: ModuleManifest, health: object) -> None:
    validate_contract(manifest)
    if type(health) is not HealthReport:
        raise TypeError("instance check_health must return HealthReport")
    validate_contract(health)
    expected = {item.capability_id for item in manifest.capabilities}
    if set(health.capabilities) != expected:
        raise LifecycleError("health report must cover every declared capability")


def _clear_task_ref(
    record: _InstanceRecord, attr: str, task: asyncio.Task[Any]
) -> None:
    if attr == "stop_task" and task.done() and not _task_failed(task):
        record.stop_completed = True
    if getattr(record, attr) is task:
        setattr(record, attr, None)
    _consume_task(task)


def _consume_task(task: asyncio.Task[Any]) -> None:
    if task.cancelled():
        return
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        return


async def _wait_owned_task(task: asyncio.Task[Any], timeout: float) -> None:
    """Drain one Lifecycle-owned task without defeating a repeated cancel."""
    _finite_timeout(timeout, "cleanup timeout")
    deadline = asyncio.get_running_loop().time() + timeout
    while not task.done():
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise LifecycleOperationTimeout("owned task cleanup timed out")
        try:
            await asyncio.wait({task}, timeout=remaining)
        except asyncio.CancelledError:
            continue
    _consume_task(task)


async def _wait_candidate_stop(
    task: asyncio.Task[Any], timeout: float, *, cleanup_timeout: float
) -> bool:
    """Wait a finite interval, then cancel and drain without losing ownership."""
    _finite_timeout(timeout, "candidate cleanup timeout")
    _finite_timeout(cleanup_timeout, "candidate cancellation cleanup timeout")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    cleanup_started = False
    cancelled = False
    while not task.done():
        remaining = deadline - loop.time()
        if remaining <= 0:
            task.cancel()
            if not cleanup_started:
                cleanup_started = True
                deadline = loop.time() + cleanup_timeout
            else:
                break
            continue
        try:
            await asyncio.wait({task}, timeout=remaining)
        except asyncio.CancelledError:
            cancelled = True
            task.cancel()
            if not cleanup_started:
                cleanup_started = True
                deadline = loop.time() + cleanup_timeout

    if cancelled:
        raise asyncio.CancelledError
    if not task.done():
        return False
    if _task_failed(task):
        _consume_task(task)
        return False
    return True


def _task_failed(task: asyncio.Task[Any]) -> bool:
    if task.cancelled():
        return True
    try:
        return task.exception() is not None
    except asyncio.CancelledError:
        return True


def _close_unstarted_awaitable(value: object) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass
