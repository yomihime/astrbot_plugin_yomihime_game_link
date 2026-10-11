from __future__ import annotations

import asyncio
import math
import tempfile
import time
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from secrets import token_urlsafe
from unittest.mock import patch

from ygl_test_subject.core.context_issuer import ContextIssuer
from ygl_test_subject.core.contracts.administration import (
    AdminAuthorizationDenied,
    AdminOperation,
    ModuleLifecycle,
)
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.health import HealthResolver
from ygl_test_subject.core.lifecycle import (
    LifecycleController,
    LifecycleError,
    ModuleNotFound,
)
from ygl_test_subject.core.ports import RevisionConflict
from ygl_test_subject.core.registry import Registry, RegistryError
from ygl_test_subject.extensions.discovery import DiscoveredPackage
from ygl_test_subject.extensions.loader import CandidateState, ExtensionCandidate
from ygl_test_subject.infrastructure.files import LocalSafeFileStore
from ygl_test_subject.infrastructure.secret_store import SQLiteSecretStore
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories import SQLiteRepositories
from ygl_test_subject.infrastructure.sqlite.repositories_admin_credentials import (
    AdminCredentialStatus,
    SQLiteAdminCredentialRepository,
)
from ygl_test_subject.infrastructure.sqlite.repositories_runtime import (
    RuntimeJournalPhase,
    SQLiteModuleRuntimeRepository,
)
from ygl_test_subject.services.admin_authorization import (
    AdminAuthorizationService,
    _digest,
)
from ygl_test_subject.services.extension_runtime import (
    ExtensionCandidateStale,
    ExtensionCleanupPending,
    ExtensionRuntime,
    ExtensionRuntimeError,
)
from ygl_test_subject.services.module_services import (
    ModuleServicesFactory,
    RegistryRegistrationLookup,
)

from yomihime_game_link_sdk.declarations import (
    CapabilityDescriptor,
    CapabilityEffect,
    CommandDescriptor,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
)
from yomihime_game_link_sdk.errors import InvalidInvocation
from yomihime_game_link_sdk.services import (
    CapabilityHealth,
    HealthReport,
    HealthStatus,
    ModuleHandlers,
    ModuleServices,
)
from yomihime_game_link_sdk.version import MODULE_ABI_VERSION


class _AttestedContext:
    adapter_id = "test-adapter"
    request_id = "test-request"

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id


class _Handler:
    async def invoke(self, context, parameters):
        del context, parameters
        return None


class _Instance:
    def __init__(
        self,
        *,
        invalid_handlers: bool = False,
        fail_start: bool = False,
        fail_health: bool = False,
        stop_failures: int = 0,
    ) -> None:
        self._handlers = validate_contract(ModuleHandlers({"read": _Handler()}, {}, {}))
        self.invalid_handlers = invalid_handlers
        self.fail_start = fail_start
        self.fail_health = fail_health
        self.stop_failures = stop_failures
        self.started = 0
        self.stopped = 0

    def handlers(self):
        return None if self.invalid_handlers else self._handlers

    async def start(self) -> None:
        self.started += 1
        if self.fail_start:
            raise RuntimeError("injected start failure")

    async def stop(self) -> None:
        self.stopped += 1
        if self.stop_failures:
            self.stop_failures -= 1
            raise RuntimeError("injected stop failure")

    async def check_health(self) -> HealthReport:
        if self.fail_health:
            raise RuntimeError("injected health failure")
        return validate_contract(
            HealthReport(
                {"read": validate_contract(CapabilityHealth(HealthStatus.AVAILABLE))}
            )
        )


class _Factory:
    def __init__(
        self,
        *,
        block_create: bool = False,
        invalid_handlers: bool = False,
        invalid_handlers_on_call: int | None = None,
        fail_create: bool = False,
        fail_start: bool = False,
        fail_health: bool = False,
        stop_failures: int = 0,
        stop_failures_on_call: dict[int, int] | None = None,
    ) -> None:
        self.block_create = block_create
        self.invalid_handlers = invalid_handlers
        self.invalid_handlers_on_call = invalid_handlers_on_call
        self.fail_create = fail_create
        self.fail_start = fail_start
        self.fail_health = fail_health
        self.stop_failures = stop_failures
        self.stop_failures_on_call = stop_failures_on_call or {}
        self.create_started = asyncio.Event()
        self.release_create = asyncio.Event()
        self.instances: list[_Instance] = []
        self.create_calls = 0
        self.services_seen: list[ModuleServices] = []

    async def create(self, services: ModuleServices) -> _Instance:
        if not isinstance(services, ModuleServices):
            raise TypeError("the real candidate services must be supplied")
        self.create_calls += 1
        self.services_seen.append(services)
        self.create_started.set()
        if self.block_create:
            await self.release_create.wait()
        if self.fail_create:
            raise RuntimeError("factory failure detail")
        instance = _Instance(
            invalid_handlers=(
                self.invalid_handlers
                or self.create_calls == self.invalid_handlers_on_call
            ),
            fail_start=self.fail_start,
            fail_health=self.fail_health,
            stop_failures=self.stop_failures_on_call.get(
                self.create_calls, self.stop_failures
            ),
        )
        self.instances.append(instance)
        return instance


class _BundleLease:
    def __init__(self, source: _FactorySource) -> None:
        self.source = source
        self.resolve_calls = 0
        self.release_calls = 0

    def resolve(self, factory_entry: str) -> _Factory:
        if factory_entry != "factory:build":
            raise LookupError("unknown factory entry")
        self.resolve_calls += 1
        return self.source.factory

    def release(self) -> None:
        self.release_calls += 1


class _FactorySource:
    """Explicit captured-source boundary; runtime components remain real."""

    def __init__(self, factory: _Factory, *, block_capture: bool = False) -> None:
        self.factory = factory
        self.block_capture = block_capture
        self.capture_started = asyncio.Event()
        self.release_capture = asyncio.Event()
        self.capture_calls = 0
        self.candidates: list[ExtensionCandidate] = []
        self.leases: list[_BundleLease] = []

    async def capture(self, candidate: ExtensionCandidate) -> _BundleLease:
        self.capture_calls += 1
        self.candidates.append(candidate)
        self.capture_started.set()
        if self.block_capture:
            await self.release_capture.wait()
        lease = _BundleLease(self)
        self.leases.append(lease)
        return lease


class _CloseBudgetClock:
    """Control deadline branches without depending on a sub-tick wall budget."""

    def __init__(self) -> None:
        self.active = False
        self.guard_expired = False

    def time(self) -> float:
        elapsed = time.perf_counter() - self.started
        if self.active and elapsed >= 0.5:
            # A broken entry barrier must still fail within real bounded time.
            self.guard_expired = True
            self.current = max(self.current, self.initial + elapsed)
        return self.current

    def advance(self, interval: float) -> None:
        if not self.active or interval <= 0:
            raise AssertionError("clock advances require an active positive interval")
        self.current += interval

    @contextmanager
    def control(self):
        loop = asyncio.get_running_loop()
        real_time = loop.time
        self.initial = self.current = real_time()
        self.started = time.perf_counter()
        self.active = True
        # Frozen/explicit time has no early timer window. Keeping Windows'
        # 15.625ms resolution would fire the original 10ms timers before entry.
        with (
            patch.object(loop, "time", self.time),
            patch.object(loop, "_clock_resolution", 0.0),
        ):
            try:
                yield self
            finally:
                self.active = False
                guard = time.perf_counter() + 0.5
                while real_time() < self.current:
                    if time.perf_counter() >= guard:
                        raise AssertionError("real loop clock did not catch up")
                    time.sleep(0.001)
        if self.guard_expired:
            raise AssertionError("stop entry exceeded the real 0.5s guard")


class ExtensionRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    async def asyncSetUp(self) -> None:
        self.runtime: ExtensionRuntime | None = None
        self.database: SQLiteDatabase | None = None

    async def asyncTearDown(self) -> None:
        if self.runtime is not None and not self.runtime.closed:
            try:
                await self.runtime.close(timeout=0.5)
            except ExtensionCleanupPending:
                pass
        if self.database is not None:
            await self.database.executor.close(timeout=1)
        self.temp.cleanup()

    async def _runtime(
        self,
        *,
        factory: _Factory | None = None,
        block_capture: bool = False,
        empty_catalog: bool = False,
        manifest: PackageManifest | None = None,
        database_name: str = "runtime.sqlite3",
    ) -> tuple[ExtensionRuntime, ExtensionCandidate | None, _FactorySource]:
        manifest = _package_manifest() if manifest is None else manifest
        candidate = None if empty_catalog else _candidate(self.root, manifest)
        candidates = () if candidate is None else (candidate,)
        registry = Registry()
        issuer = ContextIssuer()
        health = HealthResolver(config_snapshot=_unused_config_snapshot)
        lifecycle = LifecycleController(
            registry,
            issuer=issuer,
            capability_health_query=health.query,
        )
        health.bind_runtime(lifecycle, lifecycle.admission)

        self.database = SQLiteDatabase(self.root / database_name)
        await self.database.executor.initialize()
        credentials = SQLiteAdminCredentialRepository(self.database)
        self.admin_secret = token_urlsafe(48)
        await credentials.bootstrap(_digest(self.admin_secret))
        self.trusted_context = _AttestedContext("generation-one")
        contexts = {1: self.trusted_context}

        def context_validator(_operation, _invocation, context, generation):
            return context is contexts.get(generation)

        authorization = AdminAuthorizationService(
            credentials,
            admission=lifecycle.admission,
            context_validator=context_validator,
        )

        lookup = RegistryRegistrationLookup(registry)
        secret_store = SQLiteSecretStore(self.database, self.root / "secrets")
        repositories = SQLiteRepositories(
            self.database,
            lookup,
            file_store=LocalSafeFileStore(self.root / "assets"),
            secret_store=secret_store,
        )
        module_services = ModuleServicesFactory(
            registry,
            issuer,
            lifecycle,
            repositories,
            lambda _request: None,
            config_principal_id="host-config",
            identity_namespace="test-users",
        )
        selected_factory = factory or _Factory()
        source = _FactorySource(selected_factory, block_capture=block_capture)

        async def validate_generation(grant):
            await authorization.validate_generation(
                grant, operation=AdminOperation.SET_ENABLED
            )

        runtime = ExtensionRuntime(
            self.root / "extensions",
            registry=registry,
            lifecycle=lifecycle,
            authorization=authorization,
            validate_admin_grant=validate_generation,
            runtime_repository=SQLiteModuleRuntimeRepository(self.database),
            module_services=module_services,
            health_resolver=health,
            factory_source=source,
            scanner=lambda _root: candidates,
            cleanup_timeout=0.5,
        )
        self.runtime = runtime
        self.authorization = authorization
        self.credentials = credentials
        self.registry = registry
        self.lifecycle = lifecycle
        self.runtime_repository = runtime.runtime_repository
        self.contexts = contexts
        return runtime, candidate, source

    async def _enable(
        self,
        runtime: ExtensionRuntime,
        expected_revision: int,
        module_id: str = "sample/mod",
    ):
        return await runtime.set_enabled(
            None,
            module_id,
            True,
            expected_registry_revision=expected_revision,
            authorization=self.trusted_context,
        )

    async def test_enable_disable_reenable_reuses_one_lifecycle_owned_instance(self):
        runtime, candidate, source = await self._runtime()
        self.assertEqual(runtime.candidates(), ())
        runtime.scan()
        self.assertIs(runtime.candidate("sample"), candidate)
        self.assertIs(runtime.candidates()[0], candidate)

        active = await self._enable(runtime, 0)
        self.assertTrue(active.enabled)
        self.assertEqual(active.lifecycle, ModuleLifecycle.ACTIVE)
        self.assertEqual((source.capture_calls, source.factory.create_calls), (1, 1))
        instance = source.factory.instances[0]
        self.assertEqual((instance.started, instance.stopped), (1, 0))

        disabled = await runtime.set_enabled(
            None,
            "sample/mod",
            False,
            expected_registry_revision=self.registry.snapshot().revision,
            authorization=self.trusted_context,
        )
        self.assertFalse(disabled.enabled)
        self.assertEqual(disabled.lifecycle, ModuleLifecycle.STOPPED)
        self.assertEqual((instance.started, instance.stopped), (1, 1))

        active_again = await self._enable(runtime, self.registry.snapshot().revision)
        self.assertTrue(active_again.enabled)
        self.assertEqual(active_again.lifecycle, ModuleLifecycle.ACTIVE)
        self.assertEqual((source.capture_calls, source.factory.create_calls), (1, 1))
        self.assertIs(self.lifecycle.instance("sample/mod"), instance)
        # Disable retains this exact instance; its configuration is still usable.
        await source.factory.services_seen[0].config.current()

        await runtime.close(timeout=0.5)
        self.assertTrue(runtime.closed)
        self.assertEqual(instance.stopped, 2)
        self.assertEqual(source.leases[0].release_calls, 1)
        await runtime.module_services.close_credentials()
        with self.assertRaises(InvalidInvocation):
            await source.factory.services_seen[0].config.current()

    async def test_candidate_detach_does_not_build_and_restore_requires_exact_source(
        self,
    ):
        runtime, candidate, source = await self._runtime()
        runtime.scan()
        async with self.lifecycle.admission.mutation("test-candidate-detach"):
            await runtime.prepare_detach("sample/mod")
            runtime.detached("sample/mod")
        self.assertEqual((source.capture_calls, source.factory.create_calls), (0, 0))
        self.assertEqual(dict(self.registry.snapshot().modules), {})
        self.assertIs(runtime._detached_candidates["sample/mod"], candidate)
        self.assertEqual(runtime.owner_generation("sample/mod"), 1)
        runtime.detached("sample/mod")
        self.assertEqual(runtime.owner_generation("sample/mod"), 1)
        runtime._catalog["sample"] = replace(candidate)
        with self.assertRaises(ExtensionCandidateStale):
            await self._enable(runtime, 0)
        self.assertEqual(source.capture_calls, 0)
        runtime._catalog["sample"] = candidate
        await self._enable(runtime, 0)
        self.assertTrue(self.registry.is_active("sample/mod"))
        self.assertEqual(runtime.owner_generation("sample/mod"), 1)
        self.assertIs(source.candidates[0], candidate)

    async def test_candidate_cleanup_pending_blocks_restore_until_retry(self):
        runtime, _candidate, source = await self._runtime()
        runtime.scan()
        services = runtime.module_services
        retire = type(services).retire_module_credentials

        async def fail_cleanup(_services, *_args, **_kwargs):
            raise RuntimeError("injected cleanup failure")

        with patch.object(type(services), "retire_module_credentials", fail_cleanup):
            async with self.lifecycle.admission.mutation("test-candidate-detach"):
                with self.assertRaises(RuntimeError):
                    await runtime.prepare_detach("sample/mod")
        with self.assertRaises(ExtensionCleanupPending):
            await self._enable(runtime, 0)
        self.assertEqual((source.capture_calls, source.factory.create_calls), (0, 0))
        self.assertNotIn("sample/mod", runtime.unloaded_owners)
        self.assertIs(type(services).retire_module_credentials, retire)
        async with self.lifecycle.admission.mutation("test-candidate-detach-retry"):
            await runtime.prepare_detach("sample/mod")
            runtime.detached("sample/mod")
        await self._enable(runtime, 0)
        self.assertTrue(self.registry.is_active("sample/mod"))

    async def test_concurrent_same_direction_requests_join_one_build_and_start(self):
        factory = _Factory(block_create=True)
        runtime, _candidate, source = await self._runtime(factory=factory)
        runtime.scan()
        first = asyncio.create_task(self._enable(runtime, 0))
        await asyncio.wait_for(factory.create_started.wait(), timeout=2)
        operation = runtime._operations[("sample", "sample/mod")]
        second = asyncio.create_task(self._enable(runtime, 0))

        async def joined() -> None:
            while operation.waiters != 2:
                await asyncio.sleep(0.001)

        await asyncio.wait_for(joined(), timeout=2)
        factory.release_create.set()
        left, right = await asyncio.gather(first, second)

        self.assertEqual(left.lifecycle, ModuleLifecycle.ACTIVE)
        self.assertEqual(right.lifecycle, ModuleLifecycle.ACTIVE)
        self.assertEqual((source.capture_calls, factory.create_calls), (1, 1))
        self.assertEqual(factory.instances[0].started, 1)
        await runtime.close(timeout=0.5)

    async def test_cancelled_waiter_adopts_and_cleans_late_factory_result(self):
        factory = _Factory(block_create=True)
        runtime, _candidate, source = await self._runtime(factory=factory)
        runtime.scan()
        caller = asyncio.create_task(self._enable(runtime, 0))
        await asyncio.wait_for(factory.create_started.wait(), timeout=2)
        operation = runtime._operations[("sample", "sample/mod")]

        caller.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        self.assertTrue(operation.cancel_requested)
        factory.release_create.set()
        result = await asyncio.gather(operation.task, return_exceptions=True)

        self.assertIsInstance(result[0], asyncio.CancelledError)
        self.assertEqual(factory.create_calls, 1)
        self.assertEqual(factory.instances[0].stopped, 1)
        self.assertEqual(source.leases[0].release_calls, 1)
        self.assertEqual(self.registry.snapshot().modules, {})
        self.assertFalse(runtime._build_flights)
        with self.assertRaises(InvalidInvocation):
            await factory.services_seen[0].config.current()
        await runtime.close(timeout=0.5)

    async def test_cancel_after_sqlite_commit_drains_to_published_result(self):
        runtime, _candidate, _source = await self._runtime()
        runtime.scan()
        commit_acknowledged = asyncio.Event()
        release_ack = asyncio.Event()
        drain_started = asyncio.Event()
        commit = runtime.runtime_repository.commit_intent
        drain = runtime._drain_owned_operation

        async def delayed_commit(operation_id, grant):
            intent = await commit(operation_id, grant)
            commit_acknowledged.set()
            await release_ack.wait()
            return intent

        async def observed_drain(task):
            drain_started.set()
            await drain(task)

        runtime.runtime_repository.commit_intent = delayed_commit
        runtime._drain_owned_operation = observed_drain
        caller = asyncio.create_task(self._enable(runtime, 0))
        await asyncio.wait_for(commit_acknowledged.wait(), timeout=2)
        operation = runtime._operations[("sample", "sample/mod")]
        self.assertTrue(operation._linearized)
        caller.cancel()
        await asyncio.wait_for(drain_started.wait(), timeout=2)
        self.assertFalse(caller.done())
        self.assertFalse(operation.task.done())

        release_ack.set()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        self.assertEqual(
            self.lifecycle.status("sample/mod").lifecycle,
            ModuleLifecycle.ACTIVE,
        )
        journal = await self.runtime_repository.current_journal(operation.operation_id)
        self.assertEqual(journal.phase, RuntimeJournalPhase.APPLIED)
        await runtime.close(timeout=0.5)

    async def test_close_during_committing_enable_never_publishes_admission(self):
        runtime, _candidate, source = await self._runtime()
        runtime.scan()
        committed = asyncio.Event()
        release = asyncio.Event()
        commit = runtime.runtime_repository.commit_intent

        async def delayed_commit(operation_id, grant):
            intent = await commit(operation_id, grant)
            committed.set()
            await release.wait()
            return intent

        runtime.runtime_repository.commit_intent = delayed_commit
        caller = asyncio.create_task(self._enable(runtime, 0))
        await asyncio.wait_for(committed.wait(), timeout=2)
        operation = runtime._operations[("sample", "sample/mod")]
        self.assertEqual(operation.phase.value, "committing")

        with self.assertRaises(ExtensionCleanupPending):
            await runtime.close(timeout=0.02)
        self.assertFalse(runtime.closed)
        self.assertNotIn("sample/mod", self.lifecycle.admission._open)
        self.assertEqual(source.leases[0].release_calls, 0)

        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        intent = await self.runtime_repository.current_intent("sample", "mod")
        self.assertIsNotNone(intent)
        self.assertTrue(intent.desired_enabled)
        journal = await self.runtime_repository.current_journal(intent.operation_id)
        self.assertEqual(journal.phase, RuntimeJournalPhase.RECOVERY_REQUIRED)
        self.assertFalse(self.registry.snapshot().module("sample/mod").enabled)
        self.assertNotIn("sample/mod", self.lifecycle.admission._open)

        await runtime.close(timeout=0.5)
        self.assertTrue(runtime.closed)
        self.assertEqual(source.leases[0].release_calls, 1)

    async def test_rotation_during_capture_prevents_resolve_and_factory_execution(self):
        runtime, _candidate, source = await self._runtime(block_capture=True)
        runtime.scan()
        request = asyncio.create_task(self._enable(runtime, 0))
        await asyncio.wait_for(source.capture_started.wait(), timeout=2)
        replacement = token_urlsafe(48)
        rotated = await self.authorization.rotate(
            self.admin_secret,
            replacement,
            invocation=None,
            context=self.trusted_context,
        )
        self.assertEqual(rotated.generation, 2)
        self.contexts[2] = _AttestedContext("generation-two")
        source.release_capture.set()

        with self.assertRaises(AdminAuthorizationDenied):
            await request
        self.assertEqual(source.capture_calls, 1)
        self.assertEqual(source.leases[0].resolve_calls, 0)
        self.assertEqual(source.leases[0].release_calls, 1)
        self.assertEqual(source.factory.create_calls, 0)
        self.assertEqual(self.registry.snapshot().modules, {})
        current = await self.credentials.current()
        self.assertEqual(
            (current.status, current.generation), (AdminCredentialStatus.ACTIVE, 2)
        )
        await runtime.close(timeout=0.5)

    async def test_startup_restores_only_the_committed_current_intent_without_new_commit(
        self,
    ):
        runtime, _candidate, source = await self._runtime()
        grant = await self.authorization.authorize(
            AdminOperation.SET_ENABLED,
            invocation=None,
            context=self.trusted_context,
        )
        await self.runtime_repository.prepare(
            "persisted-enable",
            "sample",
            "mod",
            True,
            expected_intent_revision=0,
            expected_registry_revision=0,
            grant=grant,
        )
        persisted = await self.runtime_repository.commit_intent(
            "persisted-enable", grant
        )

        statuses = await runtime.restore_startup()

        self.assertEqual(len(statuses), 1)
        self.assertEqual(statuses[0].lifecycle, ModuleLifecycle.ACTIVE)
        self.assertTrue(statuses[0].enabled)
        self.assertEqual(source.factory.create_calls, 1)
        current = await self.runtime_repository.current_intent("sample", "mod")
        self.assertEqual(current, persisted)
        journal = await self.runtime_repository.current_journal("persisted-enable")
        self.assertEqual(journal.phase, RuntimeJournalPhase.APPLIED)
        self.assertEqual(runtime.restore_failures, ())
        await runtime.close(timeout=0.5)

    async def test_second_module_handler_failure_never_registers_a_partial_package(
        self,
    ):
        factory = _Factory(invalid_handlers_on_call=2)
        runtime, _candidate, source = await self._runtime(
            factory=factory,
            manifest=_package_manifest(("mod", "other")),
        )
        runtime.scan()

        with self.assertRaises(ExtensionRuntimeError):
            await self._enable(runtime, 0)

        self.assertEqual(self.registry.snapshot().modules, {})
        self.assertEqual(factory.create_calls, 2)
        self.assertEqual([item.stopped for item in factory.instances], [1, 1])
        self.assertEqual(source.leases[0].release_calls, 1)
        self.assertFalse(runtime._build_flights)
        self.assertEqual(await self.runtime_repository.list_intents(), ())
        await runtime.close(timeout=0.5)

    async def test_start_and_health_failures_compensate_prepared_intent(self):
        for factory in (_Factory(fail_start=True), _Factory(fail_health=True)):
            with self.subTest(fail_start=factory.fail_start):
                runtime, _candidate, source = await self._runtime(
                    factory=factory,
                    database_name=(
                        "start-failure.sqlite3"
                        if factory.fail_start
                        else "health-failure.sqlite3"
                    ),
                )
                runtime.scan()
                operation_ids: list[str] = []
                prepare = runtime.runtime_repository.prepare

                async def capture_prepare(operation_id, *args, **kwargs):
                    operation_ids.append(operation_id)
                    return await prepare(operation_id, *args, **kwargs)

                runtime.runtime_repository.prepare = capture_prepare
                with self.assertRaises(ExtensionRuntimeError):
                    await self._enable(runtime, 0)

                self.assertEqual(len(operation_ids), 1)
                journal = await self.runtime_repository.current_journal(
                    operation_ids[0]
                )
                self.assertEqual(journal.phase, RuntimeJournalPhase.COMPENSATED)
                intent = await self.runtime_repository.current_intent("sample", "mod")
                self.assertIsNone(intent)
                self.assertFalse(self.registry.snapshot().module("sample/mod").enabled)
                self.assertEqual(factory.instances[0].stopped, 1)
                with self.assertRaises(ModuleNotFound):
                    self.lifecycle.instance("sample/mod")
                self.assertEqual(source.leases[0].release_calls, 0)
                await runtime.close(timeout=0.5)
                await self.database.executor.close(timeout=1)

    async def test_health_prepare_failure_stops_the_candidate_without_opening_gate(
        self,
    ):
        factory = _Factory()
        runtime, _candidate, _source = await self._runtime(factory=factory)
        runtime.scan()
        operation_ids: list[str] = []
        prepare = runtime.runtime_repository.prepare

        async def capture_prepare(operation_id, *args, **kwargs):
            operation_ids.append(operation_id)
            return await prepare(operation_id, *args, **kwargs)

        runtime.runtime_repository.prepare = capture_prepare

        async def fail_health_prepare(module_id, manifest):
            del module_id, manifest
            raise RuntimeError("injected HealthResolver.prepare failure")

        runtime.health_resolver.prepare = fail_health_prepare
        with self.assertRaises(ExtensionRuntimeError):
            await self._enable(runtime, 0)

        journal = await self.runtime_repository.current_journal(operation_ids[0])
        self.assertEqual(journal.phase, RuntimeJournalPhase.COMPENSATED)
        self.assertIsNone(await self.runtime_repository.current_intent("sample", "mod"))
        self.assertFalse(self.registry.snapshot().module("sample/mod").enabled)
        self.assertEqual(factory.instances[0].stopped, 1)
        with self.assertRaises(ModuleNotFound):
            self.lifecycle.instance("sample/mod")
        await runtime.close(timeout=0.5)

    async def test_real_sqlite_commit_failure_keeps_registry_closed_and_journal_compensated(
        self,
    ):
        runtime, _candidate, _source = await self._runtime()
        runtime.scan()
        await self.database.executor.run_transaction(
            lambda unit: unit.execute(
                "CREATE TRIGGER reject_runtime_intent "
                "BEFORE INSERT ON module_runtime_intents "
                "BEGIN SELECT RAISE(ABORT, 'injected commit failure'); END"
            ).rowcount
        )
        operation_ids: list[str] = []
        prepare = runtime.runtime_repository.prepare

        async def capture_prepare(operation_id, *args, **kwargs):
            operation_ids.append(operation_id)
            return await prepare(operation_id, *args, **kwargs)

        runtime.runtime_repository.prepare = capture_prepare
        with self.assertRaises(ExtensionRuntimeError):
            await self._enable(runtime, 0)

        self.assertEqual(len(operation_ids), 1)
        self.assertIsNone(await self.runtime_repository.current_intent("sample", "mod"))
        journal = await self.runtime_repository.current_journal(operation_ids[0])
        self.assertEqual(journal.phase, RuntimeJournalPhase.COMPENSATED)
        self.assertFalse(self.registry.snapshot().module("sample/mod").enabled)
        self.assertEqual(
            self.lifecycle.status("sample/mod").lifecycle, ModuleLifecycle.STOPPED
        )
        await runtime.close(timeout=0.5)

    async def test_registry_publish_failure_compensates_durable_intent(self):
        runtime, _candidate, _source = await self._runtime()
        runtime.scan()
        original_publish = self.registry._publish_lifecycle_projection
        failed = False

        def fail_first_enable(module_id, *, enabled, epoch, expected_revision=None):
            nonlocal failed
            if enabled and not failed:
                failed = True
                raise RegistryError("injected Registry publication failure")
            return original_publish(
                module_id,
                enabled=enabled,
                epoch=epoch,
                expected_revision=expected_revision,
            )

        self.registry._publish_lifecycle_projection = fail_first_enable
        with self.assertRaises(ExtensionRuntimeError):
            await self._enable(runtime, 0)

        intent = await self.runtime_repository.current_intent("sample", "mod")
        self.assertIsNotNone(intent)
        self.assertFalse(intent.desired_enabled)
        journal = await self.runtime_repository.current_journal(intent.operation_id)
        self.assertEqual(journal.phase, RuntimeJournalPhase.COMPENSATED)
        self.assertFalse(self.registry.snapshot().module("sample/mod").enabled)
        await runtime.close(timeout=0.5)

    async def test_sibling_modules_keep_independent_instances_and_gates_on_failure(
        self,
    ):
        factory = _Factory()
        runtime, _candidate, _source = await self._runtime(
            factory=factory,
            manifest=_package_manifest(("mod", "other")),
        )
        runtime.scan()
        first = await self._enable(runtime, 0, "sample/mod")
        second = await self._enable(
            runtime, self.registry.snapshot().revision, "sample/other"
        )
        self.assertEqual(first.lifecycle, ModuleLifecycle.ACTIVE)
        self.assertEqual(second.lifecycle, ModuleLifecycle.ACTIVE)
        sibling = factory.instances[0]
        target = factory.instances[1]

        stopped = await runtime.set_enabled(
            None,
            "sample/other",
            False,
            expected_registry_revision=self.registry.snapshot().revision,
            authorization=self.trusted_context,
        )
        self.assertEqual(stopped.lifecycle, ModuleLifecycle.STOPPED)
        target.fail_start = True
        with self.assertRaises(ExtensionRuntimeError):
            await self._enable(
                runtime, self.registry.snapshot().revision, "sample/other"
            )

        self.assertEqual(
            self.lifecycle.status("sample/mod").lifecycle, ModuleLifecycle.ACTIVE
        )
        self.assertEqual(self.lifecycle.instance("sample/mod"), sibling)
        self.assertTrue(self.registry.snapshot().module("sample/mod").enabled)
        self.assertFalse(self.registry.snapshot().module("sample/other").enabled)
        self.assertEqual(sibling.stopped, 0)
        await runtime.close(timeout=0.5)

    async def test_opposite_intent_waits_for_activation_then_disables_that_instance(
        self,
    ):
        factory = _Factory()
        runtime, _candidate, _source = await self._runtime(factory=factory)
        runtime.scan()
        health_entered = asyncio.Event()
        release_health = asyncio.Event()
        prepare_health = runtime.health_resolver.prepare

        async def blocked_health_prepare(module_id, manifest):
            health_entered.set()
            await release_health.wait()
            await prepare_health(module_id, manifest)

        runtime.health_resolver.prepare = blocked_health_prepare
        enable = asyncio.create_task(self._enable(runtime, 0))
        await asyncio.wait_for(health_entered.wait(), timeout=2)
        second_context = _AttestedContext("generation-one-second")
        original_validator = self.authorization.context_validator
        self.authorization.context_validator = (
            lambda operation, invocation, context, generation: (
                True
                if context is second_context
                else original_validator(operation, invocation, context, generation)
            )
        )
        original_authorize = self.authorization.authorize
        second_authorizations = 0
        second_authorized = asyncio.Event()

        async def observe_second_authorization(
            operation, *, invocation, context, resources=None
        ):
            nonlocal second_authorizations
            grant = await original_authorize(
                operation, invocation=invocation, context=context, resources=resources
            )
            if context is second_context:
                second_authorizations += 1
                if second_authorizations == 2:
                    second_authorized.set()
            return grant

        self.authorization.authorize = observe_second_authorization
        disable = asyncio.create_task(
            runtime.set_enabled(
                None,
                "sample/mod",
                False,
                expected_registry_revision=0,
                authorization=second_context,
            )
        )
        await asyncio.wait_for(second_authorized.wait(), timeout=2)
        await asyncio.sleep(0)
        self.assertFalse(disable.done())
        release_health.set()

        enabled, disabled = await asyncio.gather(enable, disable)

        self.assertEqual(enabled.lifecycle, ModuleLifecycle.ACTIVE)
        self.assertEqual(disabled.lifecycle, ModuleLifecycle.STOPPED)
        self.assertFalse(disabled.enabled)
        self.assertEqual(factory.create_calls, 1)
        self.assertEqual(factory.instances[0].started, 1)
        self.assertEqual(factory.instances[0].stopped, 1)
        self.assertFalse(self.registry.snapshot().module("sample/mod").enabled)
        await runtime.close(timeout=0.5)

    async def test_unrelated_registry_change_is_not_rebased_for_waiting_request(
        self,
    ):
        runtime, _candidate, _source = await self._runtime()
        runtime.scan()
        health_entered = asyncio.Event()
        release_health = asyncio.Event()
        prepare_health = runtime.health_resolver.prepare

        async def blocked_health_prepare(module_id, manifest):
            health_entered.set()
            await release_health.wait()
            await prepare_health(module_id, manifest)

        runtime.health_resolver.prepare = blocked_health_prepare
        enable = asyncio.create_task(self._enable(runtime, 0))
        await asyncio.wait_for(health_entered.wait(), timeout=2)

        sample_manifest = _package_manifest()
        external_manifest = replace(
            sample_manifest,
            package_id="external",
            modules=(
                replace(
                    sample_manifest.modules[0],
                    module_id="external",
                    route="external",
                ),
            ),
        )
        self.registry.register_package(
            external_manifest,
            {
                "external": validate_contract(
                    ModuleHandlers({"read": _Handler()}, {}, {})
                )
            },
        )
        disable = asyncio.create_task(
            runtime.set_enabled(
                None,
                "sample/mod",
                False,
                expected_registry_revision=0,
                authorization=self.trusted_context,
            )
        )
        with self.assertRaises(RevisionConflict):
            await disable

        release_health.set()
        with self.assertRaises(RevisionConflict):
            await enable
        self.assertFalse(self.registry.snapshot().module("sample/mod").enabled)
        self.assertIn("external/external", self.registry.snapshot().modules)
        await runtime.close(timeout=0.5)

    async def test_failed_close_retains_bundle_and_retry_finishes_cleanup(self):
        factory = _Factory(stop_failures=1)
        runtime, _candidate, source = await self._runtime(factory=factory)
        runtime.scan()
        await self._enable(runtime, 0)
        instance = factory.instances[0]

        with self.assertRaises(ExtensionCleanupPending):
            await runtime.close(timeout=0.5)

        self.assertFalse(runtime.closed)
        self.assertEqual(source.leases[0].release_calls, 0)
        self.assertEqual(self.lifecycle.state("sample/mod").cleanup_pending, True)
        await runtime.close(timeout=0.5)

        self.assertTrue(runtime.closed)
        self.assertEqual(instance.stopped, 2)
        self.assertEqual(source.leases[0].release_calls, 1)

    async def test_failed_repair_candidate_remains_owned_until_close_is_quiet(self):
        factory = _Factory(stop_failures_on_call={3: 3})
        runtime, _candidate, source = await self._runtime(
            factory=factory,
            manifest=_package_manifest(("mod", "other")),
        )
        runtime.scan()
        await self._enable(runtime, 0, "sample/mod")
        sibling = factory.instances[0]
        failed_instance = factory.instances[1]
        failed_instance.fail_start = True
        with self.assertRaises(ExtensionRuntimeError):
            await self._enable(
                runtime, self.registry.snapshot().revision, "sample/other"
            )
        self.assertEqual(failed_instance.started, 1)
        self.assertEqual(failed_instance.stopped, 1)
        failed_instance.fail_start = False

        install_dormant = self.lifecycle.install_dormant
        install_failed = False

        def fail_repair_install(*args, **kwargs):
            nonlocal install_failed
            if not install_failed:
                install_failed = True
                raise LifecycleError("injected repair install failure")
            return install_dormant(*args, **kwargs)

        self.lifecycle.install_dormant = fail_repair_install
        with self.assertRaises(ExtensionCleanupPending):
            await self._enable(
                runtime, self.registry.snapshot().revision, "sample/other"
            )

        self.assertTrue(install_failed)
        self.assertEqual(sibling.started, 1)
        self.assertEqual(sibling.stopped, 0)
        self.assertEqual(
            self.lifecycle.status("sample/mod").lifecycle, ModuleLifecycle.ACTIVE
        )
        self.assertEqual(len(self.lifecycle._candidates), 1)
        self.assertEqual(len(runtime._repair_flights), 1)
        repair_id, flight = next(iter(runtime._repair_flights.items()))
        self.assertEqual(flight.operation_id, repair_id)
        self.assertIn(("sample", "sample/other", repair_id), self.lifecycle._candidates)
        repair_candidate = factory.instances[2]
        self.assertEqual(repair_candidate.started, 0)
        self.assertEqual(repair_candidate.stopped, 1)
        self.assertEqual(source.leases[0].release_calls, 0)

        with self.assertRaises(ExtensionCleanupPending):
            await self._enable(
                runtime, self.registry.snapshot().revision, "sample/other"
            )
        self.assertEqual(factory.create_calls, 3)
        self.assertEqual(repair_candidate.stopped, 2)
        self.assertEqual(len(runtime._repair_flights), 1)
        self.assertIn(("sample", "sample/other", repair_id), self.lifecycle._candidates)

        with self.assertRaises(ExtensionCleanupPending):
            await runtime.close(timeout=0.5)
        self.assertFalse(runtime.closed)
        self.assertEqual(len(self.lifecycle._candidates), 1)
        self.assertEqual(repair_candidate.stopped, 3)
        self.assertEqual(source.leases[0].release_calls, 0)
        self.assertIs(self.lifecycle.instance("sample/mod"), sibling)
        self.assertEqual(
            self.lifecycle.status("sample/mod").lifecycle, ModuleLifecycle.ACTIVE
        )

        await runtime.close(timeout=0.5)

        self.assertTrue(runtime.closed)
        self.assertEqual(len(self.lifecycle._candidates), 0)
        self.assertFalse(runtime._repair_flights)
        self.assertEqual(repair_candidate.started, 0)
        self.assertEqual(repair_candidate.stopped, 4)
        self.assertIs(self.lifecycle.instance("sample/mod"), sibling)
        self.assertEqual(sibling.stopped, 1)
        self.assertEqual(source.leases[0].release_calls, 1)

    async def _retained_repair_candidate(self):
        factory = _Factory(stop_failures_on_call={3: 1})
        runtime, _candidate, source = await self._runtime(
            factory=factory,
            manifest=_package_manifest(("mod", "other")),
        )
        runtime.scan()
        await self._enable(runtime, 0, "sample/mod")
        sibling = factory.instances[0]
        failed_instance = factory.instances[1]
        failed_instance.fail_start = True
        with self.assertRaises(ExtensionRuntimeError):
            await self._enable(
                runtime, self.registry.snapshot().revision, "sample/other"
            )
        failed_instance.fail_start = False

        install_dormant = self.lifecycle.install_dormant
        install_failed = False

        def fail_repair_install(*args, **kwargs):
            nonlocal install_failed
            if not install_failed:
                install_failed = True
                raise LifecycleError("injected repair install failure")
            return install_dormant(*args, **kwargs)

        self.lifecycle.install_dormant = fail_repair_install
        with self.assertRaises(ExtensionCleanupPending):
            await self._enable(
                runtime, self.registry.snapshot().revision, "sample/other"
            )

        return runtime, source, sibling, factory.instances[2]

    async def test_close_deadline_bounds_retained_repair_candidate_and_retry(self):
        (
            runtime,
            source,
            sibling,
            repair_candidate,
        ) = await self._retained_repair_candidate()
        close_clock = _CloseBudgetClock()
        repair_entered = asyncio.Event()
        repair_cancelled = asyncio.Event()
        release_repair = asyncio.Event()

        async def slow_repair_stop():
            repair_candidate.stopped += 1
            repair_entered.set()
            if close_clock.active:
                self.assertLess(close_clock.time(), close_clock.initial + 0.01)
                close_clock.advance(0.011)
            try:
                await release_repair.wait()
            except asyncio.CancelledError:
                repair_cancelled.set()
                await release_repair.wait()

        repair_candidate.stop = slow_repair_stop
        repair_id, flight = next(iter(runtime._repair_flights.items()))
        candidate_key = ("sample", "sample/other", repair_id)
        self.assertIn(candidate_key, self.lifecycle._candidates)
        self.assertIs(self.lifecycle.instance("sample/mod"), sibling)

        with close_clock.control():
            with self.assertRaises(ExtensionCleanupPending):
                await runtime.close(timeout=0.01)
        await asyncio.wait_for(repair_entered.wait(), timeout=0.5)
        await asyncio.wait_for(repair_cancelled.wait(), timeout=0.5)
        self.assertFalse(runtime.closed)
        self.assertIn(repair_id, runtime._repair_flights)
        self.assertIn(candidate_key, self.lifecycle._candidates)
        self.assertIsNotNone(flight.candidate_cleanup_task)
        self.assertFalse(flight.candidate_cleanup_task.done())
        candidate_record = self.lifecycle._candidates[candidate_key][0]
        self.assertIs(candidate_record.instance, repair_candidate)
        self.assertFalse(candidate_record.stop_task.done())
        self.assertTrue(runtime._closing)
        self.assertIs(self.lifecycle.instance("sample/mod"), sibling)
        self.assertEqual(source.leases[0].release_calls, 0)

        release_repair.set()
        await runtime.close(timeout=0.5)
        self.assertTrue(runtime.closed)
        self.assertFalse(runtime._repair_flights)
        self.assertNotIn(candidate_key, self.lifecycle._candidates)
        self.assertEqual(source.leases[0].release_calls, 1)
        self.assertIsNone(flight.candidate_cleanup_task)
        self.assertTrue(candidate_record.stop_task.done())
        self.assertEqual(repair_candidate.stopped, 2)
        self.assertEqual(sibling.stopped, 1)

    async def _unregistered_build_candidates(self):
        factory = _Factory(
            invalid_handlers_on_call=2,
            stop_failures_on_call={1: 1, 2: 1},
        )
        runtime, _candidate, source = await self._runtime(
            factory=factory,
            manifest=_package_manifest(("mod", "other")),
        )
        runtime.scan()
        with self.assertRaises(ExtensionCleanupPending):
            await self._enable(runtime, 0)

        flight = runtime._build_flights["sample"]
        self.assertEqual(flight.adopted_module_ids, ["sample/mod", "sample/other"])
        self.assertEqual(len(self.lifecycle._candidates), 2)
        return runtime, source, factory, flight

    async def test_close_deadline_is_shared_by_unregistered_build_candidates(self):
        runtime, source, factory, flight = await self._unregistered_build_candidates()
        close_clock = _CloseBudgetClock()
        entered = {
            module_id: asyncio.Event() for module_id in flight.adopted_module_ids
        }
        cancelled = {
            module_id: asyncio.Event() for module_id in flight.adopted_module_ids
        }
        release = {
            module_id: asyncio.Event() for module_id in flight.adopted_module_ids
        }

        for module_id, instance in zip(flight.adopted_module_ids, factory.instances):

            async def slow_stop(
                module_id=module_id,
                instance=instance,
            ):
                instance.stopped += 1
                entered[module_id].set()
                if close_clock.active:
                    self.assertLess(close_clock.time(), close_clock.initial + 0.01)
                    close_clock.advance(0.011)
                try:
                    await release[module_id].wait()
                except asyncio.CancelledError:
                    cancelled[module_id].set()
                    await release[module_id].wait()

            instance.stop = slow_stop

        with close_clock.control():
            with self.assertRaises(ExtensionCleanupPending):
                await runtime.close(timeout=0.01)
        await asyncio.wait_for(entered["sample/other"].wait(), timeout=0.5)
        await asyncio.wait_for(cancelled["sample/other"].wait(), timeout=0.5)
        self.assertFalse(entered["sample/mod"].is_set())
        self.assertFalse(runtime.closed)
        self.assertTrue(runtime._closing)
        self.assertIs(runtime._build_flights["sample"], flight)
        self.assertIsNotNone(flight.candidate_cleanup_task)
        self.assertFalse(flight.candidate_cleanup_task.done())
        self.assertEqual(source.leases[0].release_calls, 0)
        for module_id in flight.adopted_module_ids:
            key = ("sample", module_id, flight.operation_id)
            self.assertIn(key, self.lifecycle._candidates)
        candidate_record = self.lifecycle._candidates[
            ("sample", "sample/other", flight.operation_id)
        ][0]
        self.assertIs(candidate_record.instance, factory.instances[1])
        self.assertFalse(candidate_record.stop_task.done())

        retry = asyncio.create_task(runtime.close(timeout=0.5))
        release["sample/other"].set()
        await asyncio.wait_for(entered["sample/mod"].wait(), timeout=0.5)
        self.assertFalse(retry.done())
        self.assertEqual(source.leases[0].release_calls, 0)
        release["sample/mod"].set()
        await retry

        self.assertTrue(runtime.closed)
        self.assertFalse(runtime._build_flights)
        self.assertFalse(self.lifecycle._candidates)
        self.assertEqual(source.leases[0].release_calls, 1)
        self.assertIsNone(flight.candidate_cleanup_task)
        self.assertTrue(candidate_record.stop_task.done())
        self.assertEqual([instance.stopped for instance in factory.instances], [2, 2])

    async def test_close_exhausted_budget_retains_repair_candidate_before_stop(self):
        (
            runtime,
            source,
            sibling,
            repair_candidate,
        ) = await self._retained_repair_candidate()
        repair_id, flight = next(iter(runtime._repair_flights.items()))
        key = ("sample", "sample/other", repair_id)
        record = self.lifecycle._candidates[key][0]
        self.assertIs(record.instance, repair_candidate)
        previous_stop = record.stop_task
        self.assertTrue(previous_stop.done())
        close_clock = _CloseBudgetClock()
        discard = runtime._discard_flight_candidates

        async def exhaust_before_cleanup(selected, *, deadline):
            self.assertIs(selected, flight)
            self.assertEqual(deadline, close_clock.initial + 0.01)
            close_clock.advance(0.011)
            return await discard(selected, deadline=deadline)

        with (
            close_clock.control(),
            patch.object(runtime, "_discard_flight_candidates", exhaust_before_cleanup),
        ):
            with self.assertRaises(ExtensionCleanupPending):
                await runtime.close(timeout=0.01)

        self.assertFalse(runtime.closed)
        self.assertTrue(runtime._closing)
        self.assertIs(runtime._repair_flights[repair_id], flight)
        self.assertTrue(flight.cleanup_pending)
        self.assertIsNone(flight.candidate_cleanup_task)
        self.assertIs(self.lifecycle._candidates[key][0], record)
        self.assertEqual(record.candidate_key, key)
        self.assertIs(record.stop_task, previous_stop)
        self.assertEqual(repair_candidate.stopped, 1)
        self.assertIs(self.lifecycle.instance("sample/mod"), sibling)
        self.assertEqual(sibling.stopped, 0)
        self.assertEqual(source.leases[0].release_calls, 0)
        with self.assertRaises(ExtensionRuntimeError):
            runtime.scan()

        await runtime.close(timeout=0.5)
        self.assertTrue(runtime.closed)
        self.assertFalse(runtime._repair_flights)
        self.assertFalse(runtime._build_flights)
        self.assertNotIn(key, self.lifecycle._candidates)
        self.assertIsNone(flight.candidate_cleanup_task)
        self.assertTrue(record.stop_task.done())
        self.assertEqual(repair_candidate.stopped, 2)
        self.assertIs(self.lifecycle.instance("sample/mod"), sibling)
        self.assertEqual(sibling.stopped, 1)
        self.assertEqual(source.leases[0].release_calls, 1)

    async def test_close_exhausted_budget_retains_build_candidates_before_stop(self):
        runtime, source, factory, flight = await self._unregistered_build_candidates()
        records = {
            ("sample", module_id, flight.operation_id): self.lifecycle._candidates[
                ("sample", module_id, flight.operation_id)
            ][0]
            for module_id in flight.adopted_module_ids
        }
        previous_stops = {key: record.stop_task for key, record in records.items()}
        for record in records.values():
            self.assertTrue(record.stop_task.done())
        close_clock = _CloseBudgetClock()
        discard = runtime._discard_flight_candidates

        async def exhaust_before_cleanup(selected, *, deadline):
            self.assertIs(selected, flight)
            self.assertEqual(deadline, close_clock.initial + 0.01)
            close_clock.advance(0.011)
            return await discard(selected, deadline=deadline)

        with (
            close_clock.control(),
            patch.object(runtime, "_discard_flight_candidates", exhaust_before_cleanup),
        ):
            with self.assertRaises(ExtensionCleanupPending):
                await runtime.close(timeout=0.01)

        self.assertFalse(runtime.closed)
        self.assertTrue(runtime._closing)
        self.assertIs(runtime._build_flights["sample"], flight)
        self.assertTrue(flight.cleanup_pending)
        self.assertIsNone(flight.candidate_cleanup_task)
        self.assertEqual(source.leases[0].release_calls, 0)
        self.assertEqual([instance.stopped for instance in factory.instances], [1, 1])
        for key, record in records.items():
            self.assertIs(self.lifecycle._candidates[key][0], record)
            self.assertEqual(record.candidate_key, key)
            self.assertIs(record.stop_task, previous_stops[key])
        with self.assertRaises(ExtensionRuntimeError):
            runtime.scan()

        await runtime.close(timeout=0.5)
        self.assertTrue(runtime.closed)
        self.assertFalse(runtime._build_flights)
        self.assertFalse(runtime._repair_flights)
        self.assertFalse(self.lifecycle._candidates)
        self.assertIsNone(flight.candidate_cleanup_task)
        for record in records.values():
            self.assertTrue(record.stop_task.done())
        self.assertEqual([instance.stopped for instance in factory.instances], [2, 2])
        self.assertEqual(source.leases[0].release_calls, 1)

    async def test_runtime_and_close_timeouts_reject_nonfinite_and_bool(self):
        runtime, _candidate, _source = await self._runtime()
        for timeout in (True, math.nan, math.inf):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ValueError):
                    ExtensionRuntime(
                        runtime.extension_root,
                        registry=self.registry,
                        lifecycle=self.lifecycle,
                        authorization=self.authorization,
                        validate_admin_grant=runtime._validate_admin_grant,
                        runtime_repository=self.runtime_repository,
                        module_services=runtime.module_services,
                        health_resolver=runtime.health_resolver,
                        factory_source=_FactorySource(_Factory()),
                        cleanup_timeout=timeout,
                    )
                with self.assertRaises(ValueError):
                    await runtime.close(timeout=timeout)
        self.assertFalse(runtime.closed)
        await runtime.close(timeout=0.5)

    async def test_zero_module_runtime_closes_without_creating_a_bundle(self):
        runtime, _candidate, source = await self._runtime(empty_catalog=True)
        self.assertEqual(runtime.scan(), ())
        self.assertEqual(await runtime.restore_startup(), ())
        await runtime.close(timeout=0.5)
        self.assertTrue(runtime.closed)
        self.assertEqual((source.capture_calls, source.factory.create_calls), (0, 0))

    async def test_invalid_factory_handlers_leave_no_partial_registry_or_lease(self):
        factory = _Factory(invalid_handlers=True)
        runtime, _candidate, source = await self._runtime(factory=factory)
        runtime.scan()

        with self.assertRaises(ExtensionRuntimeError):
            await self._enable(runtime, 0)

        self.assertEqual(self.registry.snapshot().modules, {})
        self.assertEqual(factory.create_calls, 1)
        self.assertEqual(factory.instances[0].stopped, 1)
        self.assertEqual(source.leases[0].release_calls, 1)
        self.assertFalse(runtime._build_flights)
        with self.assertRaises(InvalidInvocation):
            await factory.services_seen[0].config.current()
        await runtime.close(timeout=0.5)

    async def test_factory_failure_revokes_only_its_exact_bundle(self):
        factory = _Factory(fail_create=True)
        runtime, _candidate, source = await self._runtime(factory=factory)
        runtime.scan()
        with self.assertRaises(ExtensionRuntimeError):
            await self._enable(runtime, 0)
        self.assertEqual(factory.create_calls, 1)
        self.assertFalse(self.registry.snapshot().modules)
        with self.assertRaises(InvalidInvocation):
            await factory.services_seen[0].config.current()
        factory.fail_create = False
        active = await self._enable(runtime, self.registry.snapshot().revision)
        self.assertTrue(active.enabled)
        self.assertEqual(factory.create_calls, 2)
        await factory.services_seen[1].config.current()
        with self.assertRaises(InvalidInvocation):
            await factory.services_seen[0].config.current()
        self.assertIs(self.lifecycle.instance("sample/mod"), factory.instances[0])
        await runtime.close(timeout=0.5)
        await runtime.module_services.close_credentials()
        with self.assertRaises(InvalidInvocation):
            await factory.services_seen[1].config.current()


def _package_manifest(module_ids: tuple[str, ...] = ("mod",)) -> PackageManifest:
    capability = validate_contract(
        CapabilityDescriptor(
            capability_id="read",
            input_schema={"type": "object"},
            invocation_policy=InvocationPolicy.NATURAL_LANGUAGE_ALLOWED,
            effect=CapabilityEffect.READ_ONLY,
        )
    )
    modules = tuple(
        validate_contract(
            ModuleManifest(
                module_id=module_id,
                route=module_id,
                category=ModuleCategory.GAME,
                factory_entry="factory:build",
                module_version="1.0.0",
                capabilities=(capability,),
                commands=(
                    validate_contract(
                        CommandDescriptor(
                            operation_path="read",
                            capability_id="read",
                            parameter_mapping={},
                            help_text="read",
                        )
                    ),
                ),
            )
        )
        for module_id in module_ids
    )
    return validate_contract(
        PackageManifest(
            package_id="sample",
            package_version="1.0.0",
            contract_version=MODULE_ABI_VERSION,
            modules=modules,
            author="tests",
            license="MIT",
            source="captured test bundle",
        )
    )


def _candidate(root: Path, manifest: PackageManifest) -> ExtensionCandidate:
    package = DiscoveredPackage(
        manifest.package_id,
        root / manifest.package_id,
        manifest,
    )
    return ExtensionCandidate(package, CandidateState.DISABLED, "not_enabled")


async def _unused_config_snapshot(module_id: str):
    del module_id
    raise AssertionError("the test manifest declares no configuration fields")


if __name__ == "__main__":
    unittest.main()
