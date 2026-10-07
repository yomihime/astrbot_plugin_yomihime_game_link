"""Authorized extension activation owned by one Core runtime.

Discovery remains inert.  This service is the only bridge from an exact
catalog candidate to a captured factory bundle, Lifecycle-owned instances,
and the durable module-intent journal.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from math import isfinite
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from ..api.administration import (
    AdminAuthorizationContext,
    AdminAuthorizationDenied,
    AdminAuthorizationGrant,
    AdminOperation,
    ModuleHealth,
    ModuleLifecycle,
    ModuleStatus,
)
from ..api.contexts import InvocationView
from ..api.manifests import ModuleManifest, PackageManifest
from ..api.services import ConfigTarget, ModuleFactory, ModuleHandlers, ModuleInstance
from ..core.health import HealthResolver
from ..core.lifecycle import LifecycleController, LifecycleError
from ..core.ports import AdminAuthorizationPort, RevisionConflict, RunIdentity
from ..core.registry import Registry
from ..extensions.discovery import DiscoveryRootError, discover_packages
from ..extensions.loader import CandidateState, ExtensionCandidate
from ..infrastructure.sqlite.repositories_runtime import (
    ModuleRuntimeIntent,
    RuntimeJournalPhase,
    SQLiteModuleRuntimeRepository,
)
from ..services.module_services import ModuleServicesFactory


class FactoryBundleLease(Protocol):
    """One immutable, runtime-owned source snapshot for a package."""

    def resolve(self, factory_entry: str) -> ModuleFactory: ...

    def release(self) -> None: ...


class FactorySourcePort(Protocol):
    """Capture source only after an authorized exact candidate is selected."""

    async def capture(self, candidate: ExtensionCandidate) -> FactoryBundleLease: ...


class ExtensionRuntimeError(RuntimeError):
    """A stable, non-sensitive failure at the host activation boundary."""

    code = "operation_failed"


class ExtensionCandidateUnavailable(ExtensionRuntimeError):
    code = "module_not_found"


class ExtensionCandidateStale(ExtensionRuntimeError):
    code = "revision_conflict"


class ExtensionCleanupPending(ExtensionRuntimeError):
    code = "operation_unavailable"


class ModuleOperationPhase(StrEnum):
    CREATING = "creating"
    STARTING = "starting"
    COMMITTING = "committing"
    PUBLISHING = "publishing"
    CLEANUP_PENDING = "cleanup_pending"
    DONE = "done"


@dataclass(slots=True)
class ModuleOperation:
    """One module's owned transition; Lifecycle remains instance/epoch owner."""

    operation_id: str
    package_id: str
    module_id: str
    desired_enabled: bool
    phase: ModuleOperationPhase
    expected_registry_revision: int
    expected_intent_revision: int
    task: asyncio.Task[ModuleStatus] | None = None
    cancel_requested: bool = False
    waiters: int = 0
    installation_operation_id: str | None = None
    identity: RunIdentity | None = None
    _build_flight: _BuildFlight | None = field(default=None, repr=False)
    _build_attached: bool = field(default=False, repr=False)
    _grant: AdminAuthorizationGrant | None = field(default=None, repr=False)
    _invocation: InvocationView | None = field(default=None, repr=False)
    _authorization: AdminAuthorizationContext | None = field(default=None, repr=False)
    _initial_registry_revision: int = field(default=0, repr=False)
    _final_registry_revision: int | None = field(default=None, repr=False)
    _prepared: bool = field(default=False, repr=False)
    _committed_intent: ModuleRuntimeIntent | None = field(default=None, repr=False)
    _restore: bool = field(default=False, repr=False)
    _linearized: bool = field(default=False, repr=False)


@dataclass(slots=True)
class _CandidatePackage:
    candidate: ExtensionCandidate
    manifest: PackageManifest
    build_operation_id: str
    lease: FactoryBundleLease
    installation_operation_ids: dict[str, str]
    base_registry_revision: int
    final_registry_revision: int
    complete: bool = False


@dataclass(slots=True)
class _BuildFlight:
    package_id: str
    candidate: ExtensionCandidate
    operation_id: str
    base_registry_revision: int
    task: asyncio.Task[_CandidatePackage] | None = None
    waiters: int = 0
    cancel_requested: bool = False
    registered: bool = False
    final_registry_revision: int | None = None
    owner_grant: AdminAuthorizationGrant | None = None
    owner_invocation: InvocationView | None = None
    owner_authorization: AdminAuthorizationContext | None = None
    restore: bool = False
    requested_module_id: str | None = None
    lease: FactoryBundleLease | None = None
    adopted_module_ids: list[str] = field(default_factory=list)
    installed_instance_ids: dict[str, str] = field(default_factory=dict)
    cleanup_pending: bool = False
    candidate_cleanup_task: asyncio.Task[bool] | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class ExtensionRestoreFailure:
    module_id: str
    reason_code: str


CandidateScanner = Callable[[str | Path | None], tuple[ExtensionCandidate, ...]]


def _is_finite_positive(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return isfinite(float(value)) and value > 0
    except (OverflowError, TypeError, ValueError):
        return False


def _scan_candidates(root: str | Path | None) -> tuple[ExtensionCandidate, ...]:
    """Turn inert manifest discovery into the loader's stable candidate DTO."""
    result: list[ExtensionCandidate] = []
    for package in discover_packages(root):
        if not package.valid:
            result.append(
                ExtensionCandidate(package, CandidateState.INVALID, "manifest_invalid")
            )
        else:
            result.append(
                ExtensionCandidate(package, CandidateState.DISABLED, "not_enabled")
            )
    return tuple(result)


class ExtensionRuntime:
    """The single Core owner for package construction and module activation.

    The constructor takes the real runtime components.  Tests may substitute
    only the static source port; Registry, Lifecycle, Admission, SQLite and
    ModuleServicesFactory remain the production implementations.
    """

    def __init__(
        self,
        extension_root: str | Path,
        *,
        registry: Registry,
        lifecycle: LifecycleController,
        authorization: AdminAuthorizationPort,
        validate_admin_grant: Callable[[AdminAuthorizationGrant], Awaitable[None]],
        runtime_repository: SQLiteModuleRuntimeRepository,
        module_services: ModuleServicesFactory,
        health_resolver: HealthResolver,
        factory_source: FactorySourcePort,
        cleanup_timeout: float = 5.0,
        scanner: CandidateScanner = _scan_candidates,
    ) -> None:
        if (
            not isinstance(extension_root, (str, Path))
            or not str(extension_root).strip()
        ):
            raise ValueError("extension_root is required")
        if not isinstance(registry, Registry) or not isinstance(
            lifecycle, LifecycleController
        ):
            raise TypeError("extension runtime requires the real Registry/Lifecycle")
        if lifecycle.registry is not registry:
            raise ValueError("Lifecycle must own the supplied Registry")
        if not isinstance(runtime_repository, SQLiteModuleRuntimeRepository):
            raise TypeError("runtime_repository must be SQLiteModuleRuntimeRepository")
        if not isinstance(module_services, ModuleServicesFactory):
            raise TypeError("module_services must be ModuleServicesFactory")
        if not isinstance(health_resolver, HealthResolver):
            raise TypeError("health_resolver must be HealthResolver")
        if not callable(getattr(authorization, "authorize", None)):
            raise TypeError("authorization must implement AdminAuthorizationPort")
        if not callable(validate_admin_grant):
            raise TypeError("validate_admin_grant must be callable")
        if not callable(getattr(factory_source, "capture", None)):
            raise TypeError("factory_source must implement FactorySourcePort")
        if not _is_finite_positive(cleanup_timeout):
            raise ValueError("cleanup_timeout must be finite and positive")
        if not callable(scanner):
            raise TypeError("scanner must be callable")
        self.extension_root = Path(extension_root)
        self.registry = registry
        self.lifecycle = lifecycle
        self.admission = lifecycle.admission
        self.authorization = authorization
        self._validate_admin_grant = validate_admin_grant
        self.runtime_repository = runtime_repository
        self.module_services = module_services
        self.health_resolver = health_resolver
        self.factory_source = factory_source
        self.cleanup_timeout = float(cleanup_timeout)
        self._scanner = scanner
        self._catalog: dict[str, ExtensionCandidate] = {}
        self._built: dict[str, _CandidatePackage] = {}
        self._build_flights: dict[str, _BuildFlight] = {}
        self._repair_flights: dict[str, _BuildFlight] = {}
        self._operations: dict[tuple[str, str], ModuleOperation] = {}
        self._restore_failures: tuple[ExtensionRestoreFailure, ...] = ()
        self._closing = False
        self._closed = False

    @property
    def restore_failures(self) -> tuple[ExtensionRestoreFailure, ...]:
        """Startup diagnostics for durable intents that could not be projected."""
        return self._restore_failures

    @property
    def closed(self) -> bool:
        return self._closed

    def scan(self, root: str | Path | None = None) -> tuple[ExtensionCandidate, ...]:
        """Refresh inert candidates without reading or executing Python source."""
        if self._closing or self._closed:
            raise ExtensionRuntimeError("extension runtime is closing")
        scan_root = self.extension_root if root is None else root
        try:
            discovered = self._scanner(scan_root)
        except DiscoveryRootError as exc:
            raise ExtensionCandidateUnavailable(
                "extension root is unavailable"
            ) from exc

        seen: set[str] = set()
        visible: list[ExtensionCandidate] = []
        for candidate in discovered:
            package_id = candidate.package.package_id
            if package_id is None:
                visible.append(candidate)
                continue
            seen.add(package_id)
            installed = self._built.get(package_id)
            if installed is None:
                self._catalog[package_id] = candidate
                visible.append(candidate)
                continue
            # An installed package keeps its original manifest and lease.  A
            # changed scan is visible as restart-required, never adopted as a
            # hot replacement or fed back into capture.
            if candidate.package != installed.candidate.package:
                visible.append(
                    ExtensionCandidate(
                        installed.candidate.package,
                        CandidateState.DISABLED,
                        "restart_required",
                    )
                )
            else:
                visible.append(installed.candidate)

        for package_id, installed in self._built.items():
            if package_id not in seen:
                visible.append(
                    ExtensionCandidate(
                        installed.candidate.package,
                        CandidateState.DISABLED,
                        "restart_required",
                    )
                )
        for package_id in tuple(self._catalog):
            if package_id not in seen and package_id not in self._built:
                self._catalog.pop(package_id, None)
        return tuple(visible)

    def candidate(self, package_id: str) -> ExtensionCandidate | None:
        """Return the exact current catalog candidate used by capture."""
        if type(package_id) is not str or not package_id.strip():
            raise ValueError("package_id must be non-empty text")
        installed = self._built.get(package_id)
        return (
            installed.candidate
            if installed is not None
            else self._catalog.get(package_id)
        )

    def candidates(self) -> tuple[ExtensionCandidate, ...]:
        """Return the current exact catalog objects without triggering a scan."""
        return tuple(self._catalog.values())

    @property
    def unloaded_owners(self):
        return frozenset(getattr(self, "_unloaded_owners", ()))

    async def set_enabled(
        self,
        invocation: InvocationView | None,
        module_id: str,
        enabled: bool,
        *,
        expected_registry_revision: int,
        authorization: AdminAuthorizationContext | None = None,
    ) -> ModuleStatus:
        """Authorize and serialize one module transition, never the package."""
        if type(enabled) is not bool:
            raise TypeError("enabled must be bool")
        if (
            type(expected_registry_revision) is not int
            or expected_registry_revision < 0
        ):
            raise ValueError("expected_registry_revision must be non-negative")
        package_id, local_id = self._split_module_id(module_id)
        if enabled and module_id in getattr(self, "_unload_pending", set()):
            raise ExtensionCleanupPending("module unload cleanup is pending")
        original = getattr(self, "_detached_candidates", {}).get(module_id)
        if (
            enabled
            and original is not None
            and self._catalog.get(package_id) is not original
        ):
            raise ExtensionCandidateStale("unloaded module trusted candidate changed")
        initial = await self._authorize(invocation, authorization, module_id)
        expected_revision = expected_registry_revision

        while True:
            wait_for: ModuleOperation | None = None
            selected: ModuleOperation | None = None
            grant = await self._reauthorize(
                invocation, authorization, initial.generation, module_id
            )
            async with self.admission.mutation(f"extension-request:{module_id}"):
                self._require_open()
                current_revision = self.registry.snapshot().revision
                key = (package_id, module_id)
                existing = self._operations.get(key)
                if current_revision != expected_revision and not (
                    existing is not None
                    and self._matches_controlled_build_revision(
                        existing, expected_revision, current_revision
                    )
                ):
                    raise RevisionConflict(
                        "registry", expected_revision, current_revision
                    )
                await self._validate_grant(grant)
                if existing is not None:
                    if existing.desired_enabled is enabled:
                        existing.waiters += 1
                        selected = existing
                    else:
                        wait_for = existing
                else:
                    intent = await self.runtime_repository.current_intent(
                        package_id, local_id
                    )
                    if self._is_noop(module_id, enabled, intent):
                        return self._status_or_unregistered(module_id)
                    op = ModuleOperation(
                        operation_id=uuid4().hex,
                        package_id=package_id,
                        module_id=module_id,
                        desired_enabled=enabled,
                        phase=(
                            ModuleOperationPhase.CREATING
                            if enabled and package_id not in self._built
                            else ModuleOperationPhase.STARTING
                        ),
                        expected_registry_revision=current_revision,
                        expected_intent_revision=(
                            0 if intent is None else intent.intent_revision
                        ),
                        waiters=1,
                        _grant=grant,
                        _invocation=invocation,
                        _authorization=authorization,
                        _initial_registry_revision=current_revision,
                    )
                    op.task = asyncio.create_task(
                        self._run_operation(op),
                        name=f"extension-{('enable' if enabled else 'disable')}:{module_id}",
                    )
                    self._operations[key] = op
                    selected = op

            if wait_for is not None:
                try:
                    assert wait_for.task is not None
                    await asyncio.shield(wait_for.task)
                except asyncio.CancelledError:
                    raise
                except BaseException:
                    # Opposite intent callers re-read the repository and CAS;
                    # failure in the preceding intent does not authorize a retry.
                    pass
                final_revision = wait_for._final_registry_revision
                if final_revision is None:
                    build = wait_for._build_flight
                    if (
                        build is not None
                        and build.registered
                        and build.base_registry_revision == expected_revision
                        and build.final_registry_revision
                        == self.registry.snapshot().revision
                    ):
                        final_revision = build.final_registry_revision
                if (
                    final_revision is not None
                    and wait_for._initial_registry_revision == expected_revision
                    and self.registry.snapshot().revision == final_revision
                ):
                    expected_revision = final_revision
                continue

            assert selected is not None and selected.task is not None
            try:
                return await asyncio.shield(selected.task)
            except asyncio.CancelledError as cancelled:
                if selected._linearized:
                    await self._drain_owned_operation(selected.task)
                raise cancelled
            finally:
                selected.waiters = max(0, selected.waiters - 1)
                if (
                    selected.waiters == 0
                    and not selected.task.done()
                    and selected.phase
                    not in (
                        ModuleOperationPhase.COMMITTING,
                        ModuleOperationPhase.PUBLISHING,
                    )
                ):
                    selected.cancel_requested = True
                    self._cancel_build_waiter(selected)

    async def restore_startup(self) -> tuple[ModuleStatus, ...]:
        """Rebuild only current durable enabled intents with fresh run epochs."""
        self._require_open()
        if not self._catalog:
            self.scan()
        pending = await self.runtime_repository.list_pending_journal()
        intents = await self.runtime_repository.list_intents()
        current_by_key = {(item.package_id, item.module_id): item for item in intents}
        failures: list[ExtensionRestoreFailure] = []

        for journal in pending:
            if journal.phase is RuntimeJournalPhase.PREPARED:
                current = current_by_key.get((journal.package_id, journal.module_id))
                if current is not None and current.operation_id == journal.operation_id:
                    failures.append(
                        ExtensionRestoreFailure(
                            f"{journal.package_id}/{journal.module_id}",
                            "intent_journal_inconsistent",
                        )
                    )
                    continue
                try:
                    await self.runtime_repository.mark_phase(
                        journal.operation_id,
                        RuntimeJournalPhase.COMPENSATED,
                        failure_code="startup_interrupted",
                    )
                except Exception:
                    failures.append(
                        ExtensionRestoreFailure(
                            f"{journal.package_id}/{journal.module_id}",
                            "journal_recovery_pending",
                        )
                    )
                continue
            current = current_by_key.get((journal.package_id, journal.module_id))
            if current is None or current.operation_id != journal.operation_id:
                # A later intent is authoritative; an older committed journal
                # is historical and must never resurrect its former value.
                if journal.phase in (
                    RuntimeJournalPhase.COMMITTED,
                    RuntimeJournalPhase.RECOVERY_REQUIRED,
                ):
                    try:
                        await self.runtime_repository.mark_phase(
                            journal.operation_id,
                            RuntimeJournalPhase.APPLIED,
                            failure_code="superseded_intent",
                        )
                    except Exception:
                        failures.append(
                            ExtensionRestoreFailure(
                                f"{journal.package_id}/{journal.module_id}",
                                "journal_recovery_pending",
                            )
                        )
            elif not current.desired_enabled and journal.phase in (
                RuntimeJournalPhase.COMMITTED,
                RuntimeJournalPhase.RECOVERY_REQUIRED,
            ):
                # A committed false intent is already the authoritative
                # projection; there is no instance to resurrect.
                try:
                    await self.runtime_repository.mark_phase(
                        journal.operation_id, RuntimeJournalPhase.APPLIED
                    )
                except Exception:
                    failures.append(
                        ExtensionRestoreFailure(
                            f"{journal.package_id}/{journal.module_id}",
                            "journal_recovery_pending",
                        )
                    )

        results: list[ModuleStatus] = []
        for intent in intents:
            if not intent.desired_enabled:
                continue
            module_id = f"{intent.package_id}/{intent.module_id}"
            try:
                status = await self._restore_intent(intent)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                code = getattr(exc, "code", "operation_failed")
                failures.append(ExtensionRestoreFailure(module_id, str(code)))
                try:
                    status = self._status(module_id)
                except Exception:
                    status = self._unregistered_status(module_id)
            results.append(status)
        self._restore_failures = tuple(failures)
        return tuple(results)

    async def close(self, *, timeout: float = 5.0) -> None:
        """Stop accepting work and retain every bundle until true quiet."""
        if not _is_finite_positive(timeout):
            raise ValueError("timeout must be finite and positive")
        if self._closed:
            return
        self.stop_accepting()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + float(timeout)

        tasks: set[asyncio.Task[object]] = {
            operation.task
            for operation in self._operations.values()
            if operation.task is not None
        }
        tasks.update(
            flight.task
            for flight in self._build_flights.values()
            if flight.task is not None
        )
        if tasks:
            _, pending = await asyncio.wait(
                tasks, timeout=max(0.0, deadline - loop.time())
            )
            if pending:
                raise ExtensionCleanupPending(
                    "extension operations have not reached a quiet point"
                )

        for operation_id, flight in tuple(self._repair_flights.items()):
            if not await self._discard_flight_candidates(flight, deadline=deadline):
                raise ExtensionCleanupPending(
                    f"repair candidate cleanup remains pending: {flight.package_id}"
                )
            self._repair_flights.pop(operation_id, None)

        for package_id, built in tuple(self._built.items()):
            for module in built.manifest.modules:
                module_id = f"{package_id}/{module.module_id}"
                try:
                    self.lifecycle.instance(module_id)
                except Exception:
                    continue
                op_id = uuid4().hex
                try:
                    async with self.admission.mutation(f"extension-close:{module_id}"):
                        state = self.lifecycle.state(module_id)
                        if (
                            state.lifecycle is not ModuleLifecycle.STOPPED
                            or state.enabled
                        ):
                            identity = self.lifecycle.quiesce(
                                module_id, op_id, "runtime_shutdown"
                            )
                        else:
                            identity = self.lifecycle.quiesce(
                                module_id, op_id, "runtime_shutdown"
                            )
                    state = await self.lifecycle.stop_candidate(
                        module_id, op_id, self._remaining(deadline)
                    )
                    if state.cleanup_pending:
                        raise ExtensionCleanupPending(
                            f"module cleanup remains pending: {module_id}"
                        )
                    del identity
                except ExtensionCleanupPending:
                    raise
                except BaseException as exc:
                    raise ExtensionCleanupPending(
                        f"module cleanup remains pending: {module_id}"
                    ) from exc
            try:
                built.lease.release()
            except BaseException as exc:
                raise ExtensionCleanupPending(
                    f"source bundle release remains pending: {package_id}"
                ) from exc
            self._built.pop(package_id, None)
            registered_flight = self._build_flights.get(package_id)
            if registered_flight is not None and registered_flight.registered:
                registered_flight.lease = None
                self._build_flights.pop(package_id, None)

        for package_id, flight in tuple(self._build_flights.items()):
            if flight.registered or flight.lease is None:
                continue
            if not await self._discard_flight_candidates(flight, deadline=deadline):
                raise ExtensionCleanupPending(
                    f"candidate cleanup remains pending: {package_id}"
                )
            try:
                flight.lease.release()
            except BaseException as exc:
                raise ExtensionCleanupPending(
                    f"source bundle release remains pending: {package_id}"
                ) from exc
            self._build_flights.pop(package_id, None)

        self._closed = True

    def stop_accepting(self) -> None:
        """Synchronously fence new and late activation before shutdown awaits."""
        if self._closed:
            return
        self._closing = True
        for operation in tuple(self._operations.values()):
            if operation.phase not in (
                ModuleOperationPhase.COMMITTING,
                ModuleOperationPhase.PUBLISHING,
                ModuleOperationPhase.DONE,
            ):
                operation.cancel_requested = True
                self._cancel_build_waiter(operation)
        for flight in self._build_flights.values():
            if not flight.registered:
                flight.cancel_requested = True

    async def _run_operation(self, operation: ModuleOperation) -> ModuleStatus:
        try:
            if operation.desired_enabled:
                return await self._enable(operation)
            return await self._disable(operation)
        except asyncio.CancelledError:
            raise
        except (ExtensionRuntimeError, RevisionConflict, AdminAuthorizationDenied):
            raise
        except Exception as exc:
            raise ExtensionRuntimeError("module operation failed") from exc
        finally:
            key = (operation.package_id, operation.module_id)
            if self._operations.get(key) is operation:
                if operation.phase is not ModuleOperationPhase.CLEANUP_PENDING:
                    operation.phase = ModuleOperationPhase.DONE
                self._operations.pop(key, None)
            if operation._build_attached and operation._build_flight is not None:
                self._detach_build_waiter(operation._build_flight)
                operation._build_attached = False

    async def _enable(self, operation: ModuleOperation) -> ModuleStatus:
        built = await self._ensure_package_built(operation)
        if operation.cancel_requested:
            raise asyncio.CancelledError
        await self._repair_missing_module(built, operation)
        self._ensure_module_installed(built, operation)
        grant = operation._grant
        if grant is None:
            raise ExtensionRuntimeError("enable requires an admin grant")
        grant = await self._reauthorize(
            operation._invocation,
            operation._authorization,
            grant.generation,
            operation.module_id,
        )

        async with self.admission.mutation(
            f"extension-prepare-enable:{operation.module_id}"
        ):
            self._check_operation(operation)
            await self._validate_grant(grant)
            operation._grant = grant
            self._check_registry_revision(operation)
            current_intent = await self.runtime_repository.current_intent(
                operation.package_id, self._local_module_id(operation.module_id)
            )
            self._check_intent_revision(operation, current_intent)
            if operation.cancel_requested:
                raise asyncio.CancelledError
            await self.runtime_repository.prepare(
                operation.operation_id,
                operation.package_id,
                self._local_module_id(operation.module_id),
                True,
                expected_intent_revision=operation.expected_intent_revision,
                expected_registry_revision=operation.expected_registry_revision,
                grant=grant,
            )
            operation._prepared = True
            operation.phase = ModuleOperationPhase.STARTING

        try:
            identity, _ = await self.lifecycle.start_candidate(
                operation.module_id, operation.operation_id
            )
            operation.identity = identity
            if operation.cancel_requested:
                raise asyncio.CancelledError
            await self.health_resolver.prepare(
                operation.module_id,
                self.registry.snapshot().module(operation.module_id).manifest,
            )
            if operation.cancel_requested:
                raise asyncio.CancelledError
            grant = await self._reauthorize(
                operation._invocation,
                operation._authorization,
                grant.generation,
                operation.module_id,
            )

            publication_error: BaseException | None = None
            async with self.admission.mutation(
                f"extension-commit-enable:{operation.module_id}"
            ):
                self._check_operation(operation)
                # Complete context/session authorization before entering the
                # shared gate.  Inside it, only the bounded generation check
                # and exact local state checks may await.
                await self._validate_grant(grant)
                operation._grant = grant
                self._check_registry_revision(operation)
                self._check_current_identity(operation, identity)
                if operation.cancel_requested:
                    raise asyncio.CancelledError
                operation.phase = ModuleOperationPhase.COMMITTING
                operation._linearized = True
                committed = await self.runtime_repository.commit_intent(
                    operation.operation_id, grant
                )
                operation._committed_intent = committed
                operation.phase = ModuleOperationPhase.PUBLISHING
                if self._closing:
                    # The durable true intent remains the recovery authority,
                    # but a shutdown fence must not let this late operation
                    # publish an open Admission gate. The cancellation handler
                    # records RECOVERY_REQUIRED and rolls back this unpublished
                    # Lifecycle candidate while retaining failed cleanup owners.
                    await self._mark_recovery_required(
                        operation, "shutdown_before_publish"
                    )
                    raise asyncio.CancelledError
                try:
                    status = self.lifecycle.publish_committed_intent(
                        operation.module_id,
                        operation.operation_id,
                        identity,
                        True,
                        operation.expected_registry_revision,
                    )
                    getattr(self, "_unloaded_owners", set()).discard(
                        operation.module_id
                    )
                except BaseException as exc:
                    self._repair_closed_projection(operation, identity)
                    publication_error = exc
                else:
                    operation._final_registry_revision = (
                        self.registry.snapshot().revision
                    )
                    try:
                        await self.runtime_repository.mark_phase(
                            operation.operation_id, RuntimeJournalPhase.APPLIED
                        )
                    except Exception:
                        # Durable COMMITTED intent remains the recovery authority.
                        pass
            if publication_error is not None:
                await self._compensate_committed(operation, grant)
                raise publication_error
            return status
        except asyncio.CancelledError:
            await self._abort_enable(operation)
            raise
        except BaseException as exc:
            await self._abort_enable(operation)
            if isinstance(
                exc, (ExtensionRuntimeError, RevisionConflict, AdminAuthorizationDenied)
            ):
                raise
            raise ExtensionRuntimeError("module activation failed") from exc

    async def _disable(self, operation: ModuleOperation) -> ModuleStatus:
        grant = operation._grant
        if grant is None:
            raise ExtensionRuntimeError("disable requires an admin grant")
        identity: RunIdentity | None = None
        quiesced = False
        installed = self._is_registered(operation.module_id)
        grant = await self._reauthorize(
            operation._invocation,
            operation._authorization,
            grant.generation,
            operation.module_id,
        )
        try:
            async with self.admission.mutation(
                f"extension-prepare-disable:{operation.module_id}"
            ):
                self._check_operation(operation)
                await self._validate_grant(grant)
                operation._grant = grant
                self._check_registry_revision(operation)
                current = await self.runtime_repository.current_intent(
                    operation.package_id, self._local_module_id(operation.module_id)
                )
                self._check_intent_revision(operation, current)
                if installed:
                    identity = self.lifecycle.quiesce(
                        operation.module_id,
                        operation.operation_id,
                        "admin_disabled",
                    )
                    quiesced = True
                    operation.identity = identity
                    operation.expected_registry_revision = (
                        self.registry.snapshot().revision
                    )
                await self.runtime_repository.prepare(
                    operation.operation_id,
                    operation.package_id,
                    self._local_module_id(operation.module_id),
                    False,
                    expected_intent_revision=operation.expected_intent_revision,
                    expected_registry_revision=operation.expected_registry_revision,
                    grant=grant,
                )
                operation._prepared = True
                if operation.cancel_requested:
                    await self.runtime_repository.mark_phase(
                        operation.operation_id,
                        RuntimeJournalPhase.COMPENSATED,
                        failure_code="cancelled_before_commit",
                    )
                    raise asyncio.CancelledError
                operation.phase = ModuleOperationPhase.COMMITTING
                operation._linearized = True
                committed = await self.runtime_repository.commit_intent(
                    operation.operation_id, grant
                )
                operation._committed_intent = committed
                operation.phase = ModuleOperationPhase.PUBLISHING
                if installed and identity is not None:
                    self.lifecycle.publish_committed_intent(
                        operation.module_id,
                        operation.operation_id,
                        identity,
                        False,
                        operation.expected_registry_revision,
                    )
                operation._final_registry_revision = self.registry.snapshot().revision
        except BaseException as original_error:
            if (
                operation._committed_intent is not None
                and operation.phase is ModuleOperationPhase.PUBLISHING
            ):
                await self._mark_recovery_required(operation, "disable_publish_pending")
            if operation._prepared and operation._committed_intent is None:
                try:
                    journal = await self.runtime_repository.current_journal(
                        operation.operation_id
                    )
                    current = await self.runtime_repository.current_intent(
                        operation.package_id, self._local_module_id(operation.module_id)
                    )
                    if (
                        journal is not None
                        and journal.phase
                        in (
                            RuntimeJournalPhase.COMMITTED,
                            RuntimeJournalPhase.RECOVERY_REQUIRED,
                        )
                        and current is not None
                        and current.operation_id == operation.operation_id
                        and not current.desired_enabled
                    ):
                        operation._committed_intent = current
                        await self._mark_recovery_required(
                            operation, "disable_publish_pending"
                        )
                    elif (
                        journal is not None
                        and journal.phase is RuntimeJournalPhase.PREPARED
                    ):
                        await self.runtime_repository.mark_phase(
                            operation.operation_id,
                            RuntimeJournalPhase.COMPENSATED,
                            failure_code="disable_failed_before_commit",
                        )
                except Exception:
                    pass
            if installed and quiesced:
                try:
                    current_state = self.lifecycle.state(operation.module_id)
                    if current_state.lifecycle is ModuleLifecycle.ACTIVE:
                        identity = self.lifecycle.quiesce(
                            operation.module_id,
                            operation.operation_id,
                            "disable_failed",
                        )
                        operation.identity = identity
                    elif current_state.lifecycle is ModuleLifecycle.FAILED:
                        identity = self.lifecycle.quiesce(
                            operation.module_id,
                            operation.operation_id,
                            "disable_failed",
                        )
                        operation.identity = identity
                    else:
                        identity = current_state.identity
                    if identity is not None:
                        stopped = await self.lifecycle.stop_candidate(
                            operation.module_id,
                            operation.operation_id,
                            self.cleanup_timeout,
                        )
                        if stopped.cleanup_pending:
                            raise ExtensionCleanupPending(
                                "module cleanup remains pending"
                            )
                except BaseException as cleanup_error:
                    operation.phase = ModuleOperationPhase.CLEANUP_PENDING
                    if not isinstance(original_error, asyncio.CancelledError):
                        raise ExtensionCleanupPending(
                            "module cleanup remains pending"
                        ) from cleanup_error
            raise original_error

        if installed:
            try:
                state = await self.lifecycle.stop_candidate(
                    operation.module_id,
                    operation.operation_id,
                    self.cleanup_timeout,
                )
                if state.cleanup_pending:
                    raise ExtensionCleanupPending("module cleanup remains pending")
            except BaseException as exc:
                operation.phase = ModuleOperationPhase.CLEANUP_PENDING
                await self._mark_recovery_required(operation, "cleanup_pending")
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise ExtensionCleanupPending("module cleanup remains pending") from exc
        try:
            await self.runtime_repository.mark_phase(
                operation.operation_id, RuntimeJournalPhase.APPLIED
            )
        except Exception:
            pass
        if installed:
            return self._status(operation.module_id)
        return self._unregistered_status(operation.module_id)

    async def _ensure_package_built(
        self, operation: ModuleOperation
    ) -> _CandidatePackage:
        built = self._built.get(operation.package_id)
        if built is not None:
            if not built.complete:
                raise ExtensionCleanupPending("package installation is incomplete")
            self._controlled_build_rebase(operation, built)
            return built

        flight: _BuildFlight
        async with self.admission.mutation(
            f"extension-build-flight:{operation.package_id}"
        ):
            self._check_operation(operation)
            current_revision = self.registry.snapshot().revision
            if current_revision != operation.expected_registry_revision:
                raise RevisionConflict(
                    "registry", operation.expected_registry_revision, current_revision
                )
            candidate = self._catalog.get(operation.package_id)
            if candidate is None or candidate.state is CandidateState.INVALID:
                raise ExtensionCandidateUnavailable("package candidate is unavailable")
            existing_flight = self._build_flights.get(operation.package_id)
            if existing_flight is not None:
                if existing_flight.cleanup_pending:
                    raise ExtensionCleanupPending(
                        "package candidate cleanup is pending"
                    )
                if existing_flight.candidate is not candidate:
                    raise ExtensionCandidateStale("package candidate changed")
                flight = existing_flight
            else:
                if self._is_package_registered(operation.package_id):
                    raise ExtensionRuntimeError(
                        "package is registered without an owned source bundle"
                    )
                grant = operation._grant
                flight = _BuildFlight(
                    package_id=operation.package_id,
                    candidate=candidate,
                    operation_id=uuid4().hex,
                    base_registry_revision=operation.expected_registry_revision,
                    owner_grant=grant,
                    owner_invocation=operation._invocation,
                    owner_authorization=operation._authorization,
                    restore=operation._restore,
                    requested_module_id=operation.module_id,
                )
                flight.task = asyncio.create_task(
                    self._build_package(flight),
                    name=f"extension-build:{operation.package_id}",
                )
                self._build_flights[operation.package_id] = flight
            flight.waiters += 1
            operation._build_flight = flight
            operation._build_attached = True

        assert flight.task is not None
        try:
            built = await asyncio.shield(flight.task)
            if operation.cancel_requested:
                raise asyncio.CancelledError
            self._controlled_build_rebase(operation, built)
            return built
        finally:
            if operation._build_attached:
                self._detach_build_waiter(flight)
                operation._build_attached = False

    async def _build_package(self, flight: _BuildFlight) -> _CandidatePackage:
        lease: FactoryBundleLease | None = None
        package_registered = False
        package: _CandidatePackage | None = None
        try:
            self._check_build_live(flight)
            await self._reauthorize_build(flight)
            lease = await self.factory_source.capture(flight.candidate)
            if (
                lease is None
                or not callable(getattr(lease, "resolve", None))
                or not callable(getattr(lease, "release", None))
            ):
                raise ExtensionRuntimeError("factory source returned an invalid lease")
            flight.lease = lease
            await self._reauthorize_build(flight)
            async with self.admission.mutation(
                f"extension-capture-check:{flight.package_id}"
            ):
                await self._validate_build_grant(flight)
                self._check_build_live(flight)
                self._check_build_current(flight)
                self._require_exact_catalog_candidate(flight.candidate)
                self._check_registry_value(flight.base_registry_revision)

            manifest = flight.candidate.package.manifest
            if not isinstance(manifest, PackageManifest):
                raise ExtensionCandidateUnavailable("candidate manifest is invalid")
            instances: dict[str, ModuleInstance] = {}
            handlers: dict[str, ModuleHandlers] = {}
            modules = tuple(
                module for module in manifest.modules
                if f"{manifest.package_id}/{module.module_id}" not in self.unloaded_owners
                or f"{manifest.package_id}/{module.module_id}" == flight.requested_module_id
            )
            for module in modules:
                self._check_build_live(flight)
                await self._reauthorize_build(flight)
                async with self.admission.mutation(
                    f"extension-resolve:{flight.package_id}/{module.module_id}"
                ):
                    await self._validate_build_grant(flight)
                    self._check_build_live(flight)
                    self._check_build_current(flight)
                    self._require_exact_catalog_candidate(flight.candidate)
                    self._check_registry_value(flight.base_registry_revision)
                # No await separates leaving the gate from resolving the
                # already captured immutable bundle and entering create().
                factory = lease.resolve(module.factory_entry)
                if not isinstance(factory, ModuleFactory):
                    raise ExtensionRuntimeError("factory entry is invalid")
                local_id = module.module_id
                module_id = f"{manifest.package_id}/{local_id}"
                services = self.module_services.for_candidate(module_id, module)
                instance = await factory.create(services)
                # Ownership is transferred before validation or cancellation
                # checks so a late create result can never be abandoned.
                flight.adopted_module_ids.append(module_id)
                module_handlers = self.lifecycle.adopt_candidate(
                    manifest.package_id,
                    module,
                    flight.operation_id,
                    instance,
                )
                instances[module_id] = instance
                handlers[local_id] = module_handlers
                self._check_build_live(flight)

            await self._reauthorize_build(flight)
            async with self.admission.mutation(
                f"extension-register:{flight.package_id}"
            ):
                await self._validate_build_grant(flight)
                self._check_build_live(flight)
                self._check_build_current(flight)
                self._require_exact_catalog_candidate(flight.candidate)
                self._check_registry_value(flight.base_registry_revision)
                registered = self.registry.register_package(
                    replace(manifest, modules=modules), handlers
                )
                package_registered = True
                flight.registered = True
                package = _CandidatePackage(
                    flight.candidate,
                    manifest,
                    flight.operation_id,
                    lease,
                    {},
                    flight.base_registry_revision,
                    registered.revision,
                )
                self._built[flight.package_id] = package
                expected_revision = registered.revision
                for module in modules:
                    module_id = f"{manifest.package_id}/{module.module_id}"
                    if self.registry.snapshot().revision != expected_revision:
                        raise RevisionConflict(
                            "registry",
                            expected_revision,
                            self.registry.snapshot().revision,
                        )
                    self.lifecycle.install_dormant(
                        manifest.package_id,
                        module_id,
                        flight.operation_id,
                        instances[module_id],
                        handlers[module.module_id],
                    )
                    package.installation_operation_ids[module_id] = flight.operation_id
                    flight.installed_instance_ids[module_id] = flight.operation_id
                    expected_revision = self.registry.snapshot().revision
                package.complete = True
                package.final_registry_revision = expected_revision
                flight.final_registry_revision = expected_revision
                return package
        except BaseException:
            if package_registered:
                # Registry/Lifecycle have transferred the lease and any exact
                # installed instances. Uninstalled adopted candidates still
                # have their explicit Lifecycle cleanup owner.
                if not await self._discard_flight_candidates(flight):
                    flight.cleanup_pending = True
                raise
            if lease is not None:
                if not await self._discard_flight_candidates(flight):
                    flight.cleanup_pending = True
                    raise ExtensionCleanupPending(
                        "candidate cleanup remains pending"
                    ) from None
                try:
                    lease.release()
                except BaseException as release_error:
                    flight.cleanup_pending = True
                    raise ExtensionCleanupPending(
                        "source bundle release remains pending"
                    ) from release_error
                lease = None
                flight.lease = None
            raise
        finally:
            if not flight.cleanup_pending and (package_registered or lease is None):
                if self._build_flights.get(flight.package_id) is flight:
                    self._build_flights.pop(flight.package_id, None)

    async def _repair_missing_module(
        self, built: _CandidatePackage, operation: ModuleOperation
    ) -> None:
        module = self._manifest_module(built.manifest, operation.module_id)
        for prior_id, prior in tuple(self._repair_flights.items()):
            if (
                prior.package_id != operation.package_id
                or operation.module_id not in prior.adopted_module_ids
            ):
                continue
            if not await self._discard_flight_candidates(prior):
                operation.phase = ModuleOperationPhase.CLEANUP_PENDING
                raise ExtensionCleanupPending(
                    "previous module candidate cleanup remains pending"
                )
            self._repair_flights.pop(prior_id, None)
        try:
            self.lifecycle.instance(operation.module_id)
            if self.lifecycle.state(operation.module_id).cleanup_pending:
                raise ExtensionCleanupPending("module cleanup is pending")
            return
        except ExtensionCleanupPending:
            raise
        except Exception:
            pass
        install_id = uuid4().hex
        operation.installation_operation_id = install_id
        flight = _BuildFlight(
            package_id=operation.package_id,
            candidate=built.candidate,
            operation_id=install_id,
            base_registry_revision=operation.expected_registry_revision,
            owner_grant=operation._grant,
            owner_invocation=operation._invocation,
            owner_authorization=operation._authorization,
            restore=operation._restore,
        )
        # A repair flight can outlive its request when an adopted candidate
        # fails to stop. Keep its exact Lifecycle operation key enumerable so
        # later repair and close retry cleanup before the shared source lease
        # can be released.
        self._repair_flights[install_id] = flight
        try:
            await self._reauthorize_build(flight)
            async with self.admission.mutation(
                f"extension-repair-check:{operation.module_id}"
            ):
                self._check_operation(operation)
                await self._validate_build_grant(flight)
                self._check_registry_revision(operation)
                self._require_exact_built_package(built)
                try:
                    self.lifecycle.instance(operation.module_id)
                except Exception:
                    pass
                else:
                    return
            factory = built.lease.resolve(module.factory_entry)
            if not isinstance(factory, ModuleFactory):
                raise ExtensionRuntimeError("factory entry is invalid")
            services = self.module_services.for_candidate(operation.module_id, module)
            instance = await factory.create(services)
            # Adopt synchronously as the first step after a possibly late
            # factory return; cancellation is checked only after ownership.
            flight.adopted_module_ids.append(operation.module_id)
            handlers = self.lifecycle.adopt_candidate(
                operation.package_id, module, install_id, instance
            )
            if operation.cancel_requested:
                raise asyncio.CancelledError
            await self._reauthorize_build(flight)
            async with self.admission.mutation(
                f"extension-install-retry:{operation.module_id}"
            ):
                self._check_operation(operation)
                await self._validate_build_grant(flight)
                self._check_registry_revision(operation)
                self._require_exact_built_package(built)
                if not self._is_registered(operation.module_id):
                    self.lifecycle.restore_registration(
                        operation.package_id, module, handlers
                    )
                self.lifecycle.install_dormant(
                    operation.package_id,
                    operation.module_id,
                    install_id,
                    instance,
                    handlers,
                )
                built.installation_operation_ids[operation.module_id] = install_id
                operation.expected_registry_revision = self.registry.snapshot().revision
        except BaseException as exc:
            if flight.adopted_module_ids:
                try:
                    clean = await self._discard_flight_candidates(flight)
                except asyncio.CancelledError:
                    flight.cleanup_pending = True
                    operation.phase = ModuleOperationPhase.CLEANUP_PENDING
                    raise
                if not clean:
                    operation.phase = ModuleOperationPhase.CLEANUP_PENDING
                    raise ExtensionCleanupPending(
                        "candidate cleanup remains pending"
                    ) from exc
            self._repair_flights.pop(install_id, None)
            raise
        self._repair_flights.pop(install_id, None)
        operation.installation_operation_id = built.installation_operation_ids.get(
            operation.module_id, install_id
        )

    async def _restore_intent(self, intent: ModuleRuntimeIntent) -> ModuleStatus:
        self._require_open()
        module_id = f"{intent.package_id}/{intent.module_id}"
        if self._is_registered(module_id):
            status = self._status(module_id)
            if status.lifecycle is ModuleLifecycle.ACTIVE and status.enabled:
                await self._mark_intent_applied(intent)
                return status
        candidate = self._catalog.get(intent.package_id)
        if candidate is None or candidate.state is CandidateState.INVALID:
            raise ExtensionCandidateUnavailable(
                "persisted module has no valid candidate"
            )
        key = (intent.package_id, module_id)
        if key in self._operations:
            raise ExtensionCleanupPending("module already has an owned operation")
        operation = ModuleOperation(
            operation_id=uuid4().hex,
            package_id=intent.package_id,
            module_id=module_id,
            desired_enabled=True,
            phase=ModuleOperationPhase.CREATING,
            expected_registry_revision=self.registry.snapshot().revision,
            expected_intent_revision=intent.intent_revision,
            _initial_registry_revision=self.registry.snapshot().revision,
            _restore=True,
        )
        operation.task = asyncio.current_task()
        self._operations[key] = operation
        try:
            built = await self._ensure_package_built(operation)
            await self._repair_missing_module(built, operation)
            self._ensure_module_installed(built, operation)
            async with self.admission.mutation(f"restore-check:{module_id}"):
                self._check_operation(operation)
                current = await self.runtime_repository.current_intent(
                    intent.package_id, intent.module_id
                )
                self._check_operation(operation)
                if current != intent:
                    raise RevisionConflict(
                        "runtime_intent",
                        intent.intent_revision,
                        0 if current is None else current.intent_revision,
                    )
                self._check_registry_revision(operation)
                operation.phase = ModuleOperationPhase.STARTING

            identity, _ = await self.lifecycle.start_candidate(
                module_id, operation.operation_id
            )
            operation.identity = identity
            if operation.cancel_requested:
                raise asyncio.CancelledError
            await self.health_resolver.prepare(
                module_id,
                self.registry.snapshot().module(module_id).manifest,
            )
            if operation.cancel_requested:
                raise asyncio.CancelledError

            async with self.admission.mutation(f"restore-check:{module_id}"):
                self._check_operation(operation)
                current = await self.runtime_repository.current_intent(
                    intent.package_id, intent.module_id
                )
                if current != intent:
                    raise RevisionConflict(
                        "runtime_intent",
                        intent.intent_revision,
                        0 if current is None else current.intent_revision,
                    )
                self._check_current_identity(operation, identity)
                journal = await self.runtime_repository.current_journal(
                    intent.operation_id
                )
                # This final check follows every awaited repository read inside
                # the publication gate. stop_accepting() can therefore fence a
                # restore even when close timed out while this gate was held.
                self._check_operation(operation)
                if (
                    journal is not None
                    and journal.phase is RuntimeJournalPhase.PREPARED
                ):
                    raise ExtensionRuntimeError(
                        "committed intent has a prepared journal"
                    )
                current_revision = self.registry.snapshot().revision
                status = self.lifecycle.publish_committed_intent(
                    module_id,
                    operation.operation_id,
                    identity,
                    True,
                    current_revision,
                )
                operation._final_registry_revision = self.registry.snapshot().revision
            await self._mark_intent_applied(intent)
            return status
        except asyncio.CancelledError:
            await self._mark_intent_recovery_required(
                intent, "startup_restore_interrupted"
            )
            await self._abort_enable(operation)
            raise
        except BaseException:
            await self._abort_enable(operation)
            await self._mark_intent_recovery_required(intent, "startup_restore_failed")
            raise
        finally:
            if self._operations.get(key) is operation:
                self._operations.pop(key, None)

    async def _mark_intent_applied(self, intent: ModuleRuntimeIntent) -> None:
        journal = await self.runtime_repository.current_journal(intent.operation_id)
        if journal is None or journal.phase not in (
            RuntimeJournalPhase.COMMITTED,
            RuntimeJournalPhase.RECOVERY_REQUIRED,
        ):
            return
        try:
            await self.runtime_repository.mark_phase(
                intent.operation_id, RuntimeJournalPhase.APPLIED
            )
        except Exception:
            # The current committed intent remains authoritative at next start.
            pass

    async def _mark_intent_recovery_required(
        self, intent: ModuleRuntimeIntent, failure_code: str
    ) -> None:
        try:
            current = await self.runtime_repository.current_intent(
                intent.package_id, intent.module_id
            )
            if current != intent:
                return
            journal = await self.runtime_repository.current_journal(intent.operation_id)
            if journal is not None and journal.phase is RuntimeJournalPhase.COMMITTED:
                await self.runtime_repository.mark_phase(
                    intent.operation_id,
                    RuntimeJournalPhase.RECOVERY_REQUIRED,
                    failure_code=failure_code,
                )
        except Exception:
            pass

    async def _abort_enable(self, operation: ModuleOperation) -> None:
        if (
            operation.phase is ModuleOperationPhase.COMMITTING
            and operation._committed_intent is None
        ):
            # A worker result can become uncertain when an exception occurs
            # after the transaction has started.  Re-read its durable journal
            # before deciding whether this was a pre-commit compensation.
            try:
                journal = await self.runtime_repository.current_journal(
                    operation.operation_id
                )
                current = await self.runtime_repository.current_intent(
                    operation.package_id, self._local_module_id(operation.module_id)
                )
                if (
                    journal is not None
                    and journal.phase
                    in (
                        RuntimeJournalPhase.COMMITTED,
                        RuntimeJournalPhase.RECOVERY_REQUIRED,
                    )
                    and current is not None
                    and current.operation_id == operation.operation_id
                    and current.desired_enabled
                ):
                    operation._committed_intent = current
                    await self._mark_recovery_required(
                        operation, "commit_result_uncertain"
                    )
            except Exception:
                pass
        if operation._prepared and operation._committed_intent is None:
            try:
                await self.runtime_repository.mark_phase(
                    operation.operation_id,
                    RuntimeJournalPhase.COMPENSATED,
                    failure_code=(
                        "cancelled_before_commit"
                        if operation.cancel_requested
                        else "operation_failed"
                    ),
                )
            except Exception:
                pass
        built = self._built.get(operation.package_id)
        if built is None:
            return
        try:
            self.lifecycle.instance(operation.module_id)
            state = self.lifecycle.state(operation.module_id)
        except Exception:
            return
        installation_id = (
            operation.installation_operation_id
            or built.installation_operation_ids.get(operation.module_id)
        )
        try:
            if installation_id is not None:
                clean = await self.lifecycle.rollback_unpublished_candidate(
                    operation.module_id,
                    operation.operation_id,
                    self.cleanup_timeout,
                    installation_operation_id=installation_id,
                )
                if clean:
                    return
        except LifecycleError:
            pass
        except BaseException:
            pass

        # Previously active instances cannot be released.  Keep them owned,
        # close the current epoch, and finish bounded stop instead.
        try:
            state = self.lifecycle.state(operation.module_id)
            if state.lifecycle is ModuleLifecycle.ACTIVE:
                identity = self.lifecycle.quiesce(
                    operation.module_id,
                    operation.operation_id,
                    "activation_failed",
                )
            elif state.lifecycle in (
                ModuleLifecycle.STARTING,
                ModuleLifecycle.FAILED,
                ModuleLifecycle.STOPPING,
            ):
                identity = state.identity
                if state.lifecycle is ModuleLifecycle.FAILED and identity is not None:
                    identity = self.lifecycle.quiesce(
                        operation.module_id,
                        operation.operation_id,
                        "activation_failed",
                    )
            else:
                identity = None
            if identity is not None:
                operation.identity = identity
                stopped = await self.lifecycle.stop_candidate(
                    operation.module_id,
                    operation.operation_id,
                    self.cleanup_timeout,
                )
                if stopped.cleanup_pending:
                    raise ExtensionCleanupPending("module cleanup remains pending")
                return
        except BaseException:
            pass
        operation.phase = ModuleOperationPhase.CLEANUP_PENDING

    def _repair_closed_projection(
        self, operation: ModuleOperation, identity: RunIdentity
    ) -> None:
        """Invalidate a failed publication without lying about durable intent."""
        try:
            state = self.lifecycle.state(operation.module_id)
            if state.lifecycle is ModuleLifecycle.ACTIVE:
                operation.identity = self.lifecycle.quiesce(
                    operation.module_id,
                    operation.operation_id,
                    "publication_failed",
                )
            elif state.identity is not None:
                operation.identity = state.identity
            del identity
        except BaseException:
            pass

    async def _compensate_committed(
        self, operation: ModuleOperation, grant: AdminAuthorizationGrant
    ) -> None:
        committed = operation._committed_intent
        if committed is None:
            return
        compensated = False
        try:
            async with self.admission.mutation(
                f"extension-compensate:{operation.module_id}"
            ):
                await self.runtime_repository.compensate(
                    operation.operation_id,
                    expected_intent_revision=committed.intent_revision,
                    grant=grant,
                    failure_code="publication_failed",
                )
                compensated = True
                state = self.lifecycle.state(operation.module_id)
                identity = state.identity
                if (
                    identity is not None
                    and self.registry.snapshot().module(operation.module_id).enabled
                ):
                    if state.lifecycle is ModuleLifecycle.ACTIVE:
                        identity = self.lifecycle.quiesce(
                            operation.module_id,
                            operation.operation_id,
                            "publication_failed",
                        )
                        operation.identity = identity
                    self.lifecycle.publish_committed_intent(
                        operation.module_id,
                        operation.operation_id,
                        identity,
                        False,
                        self.registry.snapshot().revision,
                    )
        except BaseException:
            if not compensated:
                await self._mark_recovery_required(operation, "publication_failed")
        await self._abort_enable(operation)

    async def _mark_recovery_required(
        self, operation: ModuleOperation, failure_code: str
    ) -> None:
        try:
            await self.runtime_repository.mark_phase(
                operation.operation_id,
                RuntimeJournalPhase.RECOVERY_REQUIRED,
                failure_code=failure_code,
            )
        except Exception:
            pass

    async def _reauthorize_build(self, flight: _BuildFlight) -> None:
        grant = flight.owner_grant
        if flight.restore:
            return
        if grant is None:
            raise AdminAuthorizationDenied
        current = await self.authorization.revalidate(
            grant,
            operation=AdminOperation.SET_ENABLED,
            invocation=flight.owner_invocation,
            context=flight.owner_authorization,
        )
        if current.generation != grant.generation:
            raise AdminAuthorizationDenied

    async def _validate_grant(self, grant: AdminAuthorizationGrant) -> None:
        """Check persisted generation while the caller owns Admission.mutation."""
        if (
            not isinstance(grant, AdminAuthorizationGrant)
            or grant.operation is not AdminOperation.SET_ENABLED
        ):
            raise AdminAuthorizationDenied
        await self._validate_admin_grant(grant)

    async def _validate_build_grant(self, flight: _BuildFlight) -> None:
        if flight.restore:
            return
        grant = flight.owner_grant
        if grant is None:
            raise AdminAuthorizationDenied
        await self._validate_grant(grant)

    def _check_build_current(self, flight: _BuildFlight) -> None:
        if self._build_flights.get(flight.package_id) is not flight:
            raise ExtensionRuntimeError("package build was superseded")
        if self._catalog.get(flight.package_id) is not flight.candidate:
            raise ExtensionCandidateStale("catalog candidate changed during build")

    async def _authorize(
        self,
        invocation: InvocationView | None,
        context: AdminAuthorizationContext | None,
        module_id: str,
    ) -> AdminAuthorizationGrant:
        grant = await self.authorization.authorize(
            AdminOperation.SET_ENABLED,
            invocation=invocation,
            context=context,
            resources={
                ConfigTarget(self.module_services._config_principal_id, module_id): {
                    "__module_lifecycle__"
                }
            },
        )
        if (
            not isinstance(grant, AdminAuthorizationGrant)
            or grant.operation is not AdminOperation.SET_ENABLED
        ):
            raise AdminAuthorizationDenied
        return grant

    async def _reauthorize(
        self,
        invocation: InvocationView | None,
        context: AdminAuthorizationContext | None,
        generation: int,
        module_id: str,
    ) -> AdminAuthorizationGrant:
        grant = await self._authorize(invocation, context, module_id)
        if grant.generation != generation:
            raise AdminAuthorizationDenied
        return grant

    def _check_build_live(self, flight: _BuildFlight) -> None:
        if flight.cancel_requested:
            raise asyncio.CancelledError

    def _check_operation(self, operation: ModuleOperation) -> None:
        key = (operation.package_id, operation.module_id)
        if self._operations.get(key) is not operation:
            raise ExtensionRuntimeError("module operation was superseded")
        if self._closing and operation.phase not in (
            ModuleOperationPhase.COMMITTING,
            ModuleOperationPhase.PUBLISHING,
        ):
            operation.cancel_requested = True
        if operation.cancel_requested and operation.phase not in (
            ModuleOperationPhase.COMMITTING,
            ModuleOperationPhase.PUBLISHING,
        ):
            raise asyncio.CancelledError

    def _check_registry_revision(self, operation: ModuleOperation) -> None:
        current = self.registry.snapshot().revision
        if current != operation.expected_registry_revision:
            raise RevisionConflict(
                "registry", operation.expected_registry_revision, current
            )

    def _check_registry_value(self, expected: int) -> None:
        current = self.registry.snapshot().revision
        if current != expected:
            raise RevisionConflict("registry", expected, current)

    def _check_intent_revision(
        self, operation: ModuleOperation, intent: ModuleRuntimeIntent | None
    ) -> None:
        actual = 0 if intent is None else intent.intent_revision
        if actual != operation.expected_intent_revision:
            raise RevisionConflict(
                "runtime_intent", operation.expected_intent_revision, actual
            )

    def _check_current_identity(
        self, operation: ModuleOperation, identity: RunIdentity
    ) -> None:
        state = self.lifecycle.state(operation.module_id)
        if (
            state.identity is not identity
            or state.lifecycle is not ModuleLifecycle.STARTING
        ):
            raise ExtensionRuntimeError("module candidate is no longer starting")

    def _require_exact_catalog_candidate(self, candidate: ExtensionCandidate) -> None:
        if self._catalog.get(candidate.package.package_id) is not candidate:
            raise ExtensionCandidateStale("catalog candidate changed during capture")

    def _require_exact_built_package(self, package: _CandidatePackage) -> None:
        if self._built.get(package.manifest.package_id) is not package:
            raise ExtensionCandidateStale("installed package ownership changed")

    def _controlled_build_rebase(
        self, operation: ModuleOperation, built: _CandidatePackage
    ) -> None:
        current = self.registry.snapshot().revision
        if (
            operation.expected_registry_revision == built.base_registry_revision
            and current == built.final_registry_revision
        ):
            # The only permitted rebase is the exact register_package plus
            # install_dormant revision sequence owned by this build.
            operation.expected_registry_revision = built.final_registry_revision

    @staticmethod
    def _matches_controlled_build_revision(
        operation: ModuleOperation, expected_revision: int, current_revision: int
    ) -> bool:
        """Accept only the exact Registry revision created by this flight."""
        build = operation._build_flight
        return bool(
            operation._initial_registry_revision == expected_revision
            and build is not None
            and build.registered
            and build.base_registry_revision == expected_revision
            and build.final_registry_revision == current_revision
        )

    def _cancel_build_waiter(self, operation: ModuleOperation) -> None:
        flight = operation._build_flight
        if not operation._build_attached or flight is None:
            return
        self._detach_build_waiter(flight)
        operation._build_attached = False

    @staticmethod
    def _detach_build_waiter(flight: _BuildFlight) -> None:
        flight.waiters = max(0, flight.waiters - 1)
        if flight.waiters == 0 and not flight.registered:
            flight.cancel_requested = True

    async def _discard_flight_candidates(
        self, flight: _BuildFlight, *, deadline: float | None = None
    ) -> bool:
        """Clean exact flight candidates, preserving a close-bound waiter task."""

        if deadline is None:
            task = flight.candidate_cleanup_task
            if task is not None:
                if not task.done():
                    flight.cleanup_pending = True
                    return False
                flight.candidate_cleanup_task = None
                try:
                    if task.result():
                        flight.cleanup_pending = False
                        return True
                except BaseException:
                    pass
            return await self._discard_flight_candidates_until(flight, None)

        loop = asyncio.get_running_loop()
        existing = flight.candidate_cleanup_task is not None
        attempts = 2 if existing else 1
        for attempt in range(attempts):
            remaining = deadline - loop.time()
            if remaining <= 0:
                flight.cleanup_pending = True
                return False

            task = flight.candidate_cleanup_task
            if task is None:
                task = asyncio.create_task(
                    self._discard_flight_candidates_until(flight, deadline),
                    name=f"extension-candidate-cleanup:{flight.package_id}",
                )
                flight.candidate_cleanup_task = task

            remaining = deadline - loop.time()
            if remaining <= 0:
                flight.cleanup_pending = True
                return False
            try:
                _done, pending_tasks = await asyncio.wait({task}, timeout=remaining)
            except asyncio.CancelledError:
                flight.cleanup_pending = True
                raise
            if pending_tasks:
                flight.cleanup_pending = True
                return False

            if flight.candidate_cleanup_task is task:
                flight.candidate_cleanup_task = None
            try:
                clean = task.result()
            except BaseException:
                clean = False

            if loop.time() >= deadline:
                flight.cleanup_pending = True
                return False
            if clean:
                flight.cleanup_pending = False
                return True
            flight.cleanup_pending = True
            if attempt + 1 >= attempts:
                return False
        return False

    async def _discard_flight_candidates_until(
        self, flight: _BuildFlight, deadline: float | None
    ) -> bool:
        """Use one deadline across the candidate keys owned by this flight."""

        pending = False
        loop = asyncio.get_running_loop()
        for module_id in reversed(tuple(dict.fromkeys(flight.adopted_module_ids))):
            timeout = self.cleanup_timeout
            if deadline is not None:
                timeout = deadline - loop.time()
                if timeout <= 0:
                    pending = True
                    break
            try:
                clean = await self.lifecycle.discard_candidate(
                    flight.package_id,
                    module_id,
                    flight.operation_id,
                    timeout,
                )
            except asyncio.CancelledError:
                flight.cleanup_pending = True
                raise
            except BaseException:
                clean = False
            pending = pending or not clean
        flight.cleanup_pending = pending
        return not pending

    def _ensure_module_installed(
        self, built: _CandidatePackage, operation: ModuleOperation
    ) -> None:
        if not built.complete:
            raise ExtensionCleanupPending("package installation is incomplete")
        self._manifest_module(built.manifest, operation.module_id)
        try:
            self.lifecycle.instance(operation.module_id)
        except Exception:
            return
        if self.lifecycle.state(operation.module_id).cleanup_pending:
            raise ExtensionCleanupPending("module cleanup is pending")
        operation.installation_operation_id = built.installation_operation_ids.get(
            operation.module_id
        )

    def detached(self, module_id):
        """Release installed-instance bookkeeping; retain the trusted source lease."""
        package_id, _ = self._split_module_id(module_id)
        built = self._built.get(package_id)
        if built is not None:
            built.installation_operation_ids.pop(module_id, None)
        getattr(self, "_unload_pending", set()).discard(module_id)
        if not hasattr(self, "_unloaded_owners"):
            self._unloaded_owners = set()
        self._unloaded_owners.add(module_id)

    async def prepare_detach(self, module_id):
        """Retire owner-scoped caches and the final package source lease before detach."""
        if not hasattr(self, "_unload_pending"):
            self._unload_pending = set()
            self._detached_candidates = {}
        self._unload_pending.add(module_id)
        package_id, _ = self._split_module_id(module_id)
        registered = self.registry.snapshot().module(module_id)
        await self.module_services.retire_module_credentials(
            module_id, manifest=registered.manifest
        )
        self.health_resolver._runs.pop(module_id, None)
        self.health_resolver._configs.pop(module_id, None)
        built = self._built.get(package_id)
        if built is not None:
            if self._catalog.get(package_id) is not built.candidate:
                raise ExtensionCandidateStale(
                    "unloaded module trusted candidate changed"
                )
            self._detached_candidates[module_id] = built.candidate
            if not any(
                owner != module_id and owner.startswith(package_id + "/")
                for owner in self.registry.snapshot().modules
            ):
                built.lease.release()
                self._built.pop(package_id, None)

    def _is_noop(
        self,
        module_id: str,
        desired_enabled: bool,
        intent: ModuleRuntimeIntent | None,
    ) -> bool:
        current_intent = False if intent is None else intent.desired_enabled
        if current_intent is not desired_enabled:
            return False
        if not self._is_registered(module_id):
            return not desired_enabled
        try:
            status = self.lifecycle.status(module_id)
        except Exception:
            return False
        if desired_enabled:
            return status.enabled and status.lifecycle is ModuleLifecycle.ACTIVE
        return not status.enabled and status.lifecycle is ModuleLifecycle.STOPPED

    def _status(self, module_id: str) -> ModuleStatus:
        return self.lifecycle.status(module_id)

    def _status_or_unregistered(self, module_id: str) -> ModuleStatus:
        try:
            return self._status(module_id)
        except Exception:
            return self._unregistered_status(module_id)

    def _unregistered_status(self, module_id: str) -> ModuleStatus:
        return ModuleStatus(
            module_id,
            False,
            ModuleLifecycle.STOPPED,
            ModuleHealth.DEGRADED,
            0,
            self.registry.snapshot().revision,
            "unregistered",
        )

    def _is_registered(self, module_id: str) -> bool:
        return module_id in self.registry.snapshot().modules

    def _is_package_registered(self, package_id: str) -> bool:
        prefix = f"{package_id}/"
        return any(
            module_id.startswith(prefix)
            for module_id in self.registry.snapshot().modules
        )

    @staticmethod
    def _manifest_module(manifest: PackageManifest, module_id: str) -> ModuleManifest:
        expected = f"{manifest.package_id}/"
        if not module_id.startswith(expected):
            raise ExtensionCandidateUnavailable("module does not belong to package")
        local = module_id[len(expected) :]
        for module in manifest.modules:
            if module.module_id == local:
                return module
        raise ExtensionCandidateUnavailable("module is not declared in the package")

    @staticmethod
    def _split_module_id(module_id: str) -> tuple[str, str]:
        if type(module_id) is not str or module_id.count("/") != 1:
            raise ValueError("module_id must be a global package/module id")
        package_id, local_id = module_id.split("/", 1)
        if not package_id or not local_id:
            raise ValueError("module_id must be a global package/module id")
        return package_id, local_id

    @staticmethod
    def _local_module_id(module_id: str) -> str:
        return module_id.split("/", 1)[1]

    @staticmethod
    def _remaining(deadline: float) -> float:
        return max(0.001, deadline - asyncio.get_running_loop().time())

    @staticmethod
    async def _drain_owned_operation(task: asyncio.Task[ModuleStatus]) -> None:
        """Do not return a cancelled caller while its commit owner is unsettled."""
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                # A second caller cancellation cannot detach the commit owner.
                continue
            except BaseException:
                break

    def _require_open(self) -> None:
        if self._closing or self._closed:
            raise ExtensionRuntimeError("extension runtime is closing")


__all__ = [
    "ExtensionCandidateStale",
    "ExtensionCandidateUnavailable",
    "ExtensionCleanupPending",
    "ExtensionRestoreFailure",
    "ExtensionRuntime",
    "ExtensionRuntimeError",
    "FactoryBundleLease",
    "FactorySourcePort",
    "ModuleOperation",
    "ModuleOperationPhase",
]
