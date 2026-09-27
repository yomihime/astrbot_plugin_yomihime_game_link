from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from secrets import token_urlsafe
from unittest.mock import patch

from ygl_test_subject.api.administration import AdminOperation
from ygl_test_subject.api.display import (
    DisplayDocument,
    DisplayLimits,
    DisplayOutput,
    Privacy,
    TextBlock,
)
from ygl_test_subject.api.results import CapabilityResult, ResultStatus
from ygl_test_subject.api.services import (
    CapabilityHealth,
    Grant,
    GrantStatus,
    HealthReport,
    HealthStatus,
    ModuleHandlers,
    Principal,
)
from ygl_test_subject.api.storage import GrantReference, OwnerScope
from ygl_test_subject.api.subscriptions import (
    CollectionKey,
    ConversationKind,
    NormalizedInput,
)
from ygl_test_subject.core.admission import AdmissionError
from ygl_test_subject.core.ports import (
    ExecutionLease,
    MessageReceipt,
    MessageStatus,
    SecretOwner,
)
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.services.admin_authorization import _digest
from ygl_test_subject.services.core_runtime import (
    CoreRuntime,
    CoreRuntimeCleanupPending,
    HostIngress,
)
from ygl_test_subject.services.extension_runtime import ExtensionCleanupPending


class _Renderer:
    def __init__(self, *, entered=None, release=None) -> None:
        self.entered = entered
        self.release = release

    async def render(self, document, *, limits, audience):
        del limits, audience
        if self.entered is not None:
            self.entered.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                await self.release.wait()
        return DisplayOutput(document.title)

    async def render_batch(self, batch, limits):  # pragma: no cover - protocol path
        del batch, limits
        raise AssertionError("root invocation does not render a digest batch")


class _MessagePort:
    def __init__(self) -> None:
        self.calls = []

    async def send(self, target, payload):
        self.calls.append((target, payload))
        return MessageReceipt(MessageStatus.ACCEPTED, "message-1")


class _Handler:
    def __init__(self) -> None:
        self.grant = None

    async def invoke(self, context, parameters):
        del parameters
        self.grant = (context.grant_id, context.grant_revision)
        return CapabilityResult(
            "private-result",
            ResultStatus.SUCCESS,
            document=DisplayDocument(
                "Private result",
                "Private result",
                (TextBlock("authorized"),),
                privacy=Privacy.PRIVATE,
            ),
            privacy=Privacy.PRIVATE,
        )


class _Instance:
    def __init__(self, handler: _Handler) -> None:
        self._handler = handler
        self.started = False
        self.stopped = False

    def handlers(self):
        return ModuleHandlers({"private": self._handler}, {}, {})

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def check_health(self) -> HealthReport:
        return HealthReport({"private": CapabilityHealth(HealthStatus.AVAILABLE)})


class _Factory:
    def __init__(self, handler: _Handler) -> None:
        self.handler = handler
        self.instances: list[_Instance] = []

    async def create(self, services):
        if services is None:
            raise TypeError("the real ModuleServices must be supplied")
        instance = _Instance(self.handler)
        self.instances.append(instance)
        return instance


class _FactoryBundle:
    def __init__(self, factory: _Factory) -> None:
        self.factory = factory
        self.release_calls = 0

    def resolve(self, factory_entry: str):
        if factory_entry != "factory:create":
            raise LookupError("unexpected factory entry")
        return self.factory

    def release(self) -> None:
        self.release_calls += 1


class _FactorySource:
    def __init__(self, factory: _Factory | None = None) -> None:
        self.factory = factory
        self.capture_calls = 0
        self.bundle: _FactoryBundle | None = None

    async def capture(self, candidate):
        del candidate
        self.capture_calls += 1
        if self.factory is None:
            raise AssertionError("zero-module runtime must not capture a factory")
        self.bundle = _FactoryBundle(self.factory)
        return self.bundle


class _AdminContext:
    adapter_id = "test-adapter"
    request_id = "test-request"
    session_id = "test-session"


class _TestCodec:
    def encrypt(self, value: bytes) -> bytes:
        return b"test-envelope:" + value[::-1]

    def decrypt(self, value: bytes) -> bytes:
        prefix = b"test-envelope:"
        if not value.startswith(prefix):
            raise ValueError("invalid test envelope")
        return value[len(prefix) :][::-1]


class CoreRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # The Windows discovery backend qualifies the checked-out local volume.
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self.temp.name)

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    def _runtime(
        self,
        *,
        source: _FactorySource | None = None,
        ingress_validator=None,
        renderer=None,
        pump_interval: float = 3600,
        utc_clock=None,
    ) -> CoreRuntime:
        (self.root / "extensions").mkdir(exist_ok=True)
        return CoreRuntime(
            database=SQLiteDatabase(self.root / "core.sqlite3"),
            extension_root=self.root / "extensions",
            file_root=self.root / "files",
            secret_root=self.root / "secrets",
            secret_codec=_TestCodec(),
            http_transport=lambda request: request,
            renderer=renderer or _Renderer(),
            display_limits=DisplayLimits(2, 4096),
            message_port=_MessagePort(),
            admin_context_validator=lambda *_args: True,
            host_ingress_validator=ingress_validator or (lambda *_args: True),
            config_principal_id="host-config",
            identity_namespace="test-namespace",
            factory_source=source,
            utc_clock=utc_clock or (lambda: datetime.now(UTC)),
            pump_interval=pump_interval,
            cleanup_timeout=0.2,
        )

    async def test_zero_module_runtime_starts_and_closes(self) -> None:
        source = _FactorySource()
        runtime = self._runtime(source=source)
        report = await runtime.start()
        self.assertEqual(report.module_statuses, ())
        self.assertTrue(runtime.accepting)
        self.assertTrue(await runtime.close(timeout=0.5))
        self.assertTrue(runtime.closed)
        self.assertEqual(source.capture_calls, 0)

    async def test_core_close_deadline_retains_candidate_owner_for_retry(self) -> None:
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        release = asyncio.Event()

        class _InvalidCandidate:
            def __init__(self) -> None:
                self.stop_calls = 0

            def handlers(self):
                return None

            async def start(self) -> None:
                return None

            async def check_health(self) -> HealthReport:
                return HealthReport({})

            async def stop(self) -> None:
                self.stop_calls += 1
                if self.stop_calls == 1:
                    raise RuntimeError("injected first stop failure")
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    await release.wait()

        class _InvalidFactory:
            def __init__(self) -> None:
                self.instance = _InvalidCandidate()

            async def create(self, services):
                if services is None:
                    raise TypeError("the real ModuleServices must be supplied")
                return self.instance

        factory = _InvalidFactory()
        source = _FactorySource(factory)  # type: ignore[arg-type]
        runtime = self._runtime(source=source)
        try:
            with self.assertRaises(ExtensionCleanupPending):
                await self._activate_private(runtime)

            flight = runtime.extension_runtime._build_flights["private"]
            candidate_key = ("private", "private/mod", flight.operation_id)
            self.assertIn(candidate_key, runtime.lifecycle._candidates)
            self.assertEqual(source.bundle.release_calls, 0)

            with self.assertRaises(CoreRuntimeCleanupPending) as caught:
                await runtime.close(timeout=0.03)
            self.assertEqual(caught.exception.component, "extension_lifecycle")
            await asyncio.wait_for(entered.wait(), timeout=0.5)
            await asyncio.wait_for(cancelled.wait(), timeout=0.5)
            self.assertFalse(runtime.closed)
            self.assertFalse(runtime.extension_runtime.closed)
            self.assertIs(runtime.extension_runtime._build_flights["private"], flight)
            self.assertIsNotNone(flight.candidate_cleanup_task)
            self.assertFalse(flight.candidate_cleanup_task.done())
            self.assertIn(candidate_key, runtime.lifecycle._candidates)
            self.assertFalse(
                runtime.lifecycle._candidates[candidate_key][0].stop_task.done()
            )
            self.assertEqual(source.bundle.release_calls, 0)

            release.set()
            self.assertTrue(await runtime.close(timeout=1))
            self.assertTrue(runtime.closed)
            self.assertTrue(runtime.extension_runtime.closed)
            self.assertFalse(runtime.extension_runtime._build_flights)
            self.assertNotIn(candidate_key, runtime.lifecycle._candidates)
            self.assertEqual(source.bundle.release_calls, 1)
        finally:
            release.set()
            if not runtime.closed:
                await runtime.close(timeout=1)

    async def test_private_command_uses_real_grant_reference_and_authorization(self):
        handler = _Handler()
        source = _FactorySource(_Factory(handler))
        runtime = self._runtime(source=source)
        await self._activate_private(runtime)
        await self._install_private_grant(runtime)
        ingress = HostIngress(
            "adapter-one",
            "alice",
            "conversation-one",
            "route-one",
            ConversationKind.DIRECT,
            object(),
            GrantReference("grant-one", 1),
        )
        outcome = await runtime.invoke_command(
            "private/mod", "read", {}, ingress=ingress
        )
        self.assertEqual(handler.grant, ("grant-one", 1))
        self.assertEqual(len(runtime.output._message_port.calls), 1)
        self.assertEqual(runtime.output._message_port.calls[0][0].recipient_id, "alice")
        self.assertTrue(outcome.result.document.privacy is Privacy.PRIVATE)
        self.assertTrue(outcome.output.result is not None)
        with self.assertRaises(PermissionError):
            await runtime.invoke_tool(
                "private/mod", "private_tool", {}, ingress=ingress
            )
        self.assertTrue(await runtime.close(timeout=0.5))

    async def test_private_result_is_hidden_when_grant_is_revoked_during_render(self):
        handler = _Handler()
        entered = asyncio.Event()
        release = asyncio.Event()
        runtime = self._runtime(
            source=_FactorySource(_Factory(handler)),
            renderer=_Renderer(entered=entered, release=release),
        )
        await self._activate_private(runtime)
        await self._install_private_grant(runtime)
        request = asyncio.create_task(
            runtime.invoke_command(
                "private/mod",
                "read",
                {},
                ingress=_ingress(grant_reference=GrantReference("grant-one", 1)),
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        grant = await runtime.repositories.authorization.current_grant("grant-one")
        self.assertIsNotNone(grant)
        await runtime.repositories.authorization.revoke_grant(
            grant, expected_revision=1
        )
        release.set()

        outcome = await request
        self.assertIsNone(outcome.result)
        self.assertIsNone(outcome.output.result)
        self.assertEqual(outcome.output.status.value, "failed")
        self.assertEqual(outcome.output.error_code, "grant_unavailable")
        self.assertEqual(runtime.output._message_port.calls, [])
        self.assertTrue(await runtime.close(timeout=0.5))

    async def test_private_result_is_hidden_when_admin_disables_during_render(self):
        handler = _Handler()
        entered = asyncio.Event()
        release = asyncio.Event()
        runtime = self._runtime(
            source=_FactorySource(_Factory(handler)),
            renderer=_Renderer(entered=entered, release=release),
        )
        context = await self._activate_private(runtime)
        await self._install_private_grant(runtime)
        request = asyncio.create_task(
            runtime.invoke_command(
                "private/mod",
                "read",
                {},
                ingress=_ingress(grant_reference=GrantReference("grant-one", 1)),
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        disabled = await runtime.admin_operations.set_enabled(
            None,
            "private/mod",
            False,
            expected_registry_revision=runtime.registry.snapshot().revision,
            authorization=context,
        )
        self.assertFalse(disabled.enabled)
        release.set()

        outcome = await request
        self.assertIsNone(outcome.result)
        self.assertIsNone(outcome.output.result)
        self.assertEqual(outcome.output.status.value, "failed")
        self.assertTrue(outcome.output.error_code)
        self.assertEqual(runtime.output._message_port.calls, [])
        self.assertTrue(await runtime.close(timeout=0.5))

    async def test_default_filesystem_source_builds_and_closes_real_package(self):
        runtime = self._runtime()
        package_root = self.root / "extensions" / "private"
        package_root.mkdir(parents=True, exist_ok=True)
        (package_root / "yomihime.manifest.json").write_text(
            json.dumps(_private_manifest(factory_entry="module:Factory")),
            encoding="utf-8",
        )
        (package_root / "module.py").write_text(
            _packaged_factory_source(), encoding="utf-8"
        )
        await runtime.start()
        await runtime.admin_credential_repository.bootstrap(_digest(token_urlsafe(32)))
        status = await runtime.admin_operations.set_enabled(
            None,
            "private/mod",
            True,
            expected_registry_revision=runtime.registry.snapshot().revision,
            authorization=_AdminContext(),
        )
        self.assertTrue(status.enabled)
        self.assertTrue(status.lifecycle.value == "active")
        self.assertTrue(await runtime.close(timeout=0.5))

    async def test_core_close_fences_late_committed_enable_and_startup_recovers(self):
        runtime = self._runtime(source=_FactorySource(_Factory(_Handler())))
        package_root = self.root / "extensions" / "private"
        package_root.mkdir(parents=True, exist_ok=True)
        (package_root / "yomihime.manifest.json").write_text(
            json.dumps(_private_manifest()), encoding="utf-8"
        )
        await runtime.start()
        await runtime.admin_credential_repository.bootstrap(_digest(token_urlsafe(32)))
        committed = asyncio.Event()
        release = asyncio.Event()
        repository_type = type(runtime.runtime_repository)
        commit_intent = repository_type.commit_intent

        async def committed_then_wait(repository, operation_id, grant):
            intent = await commit_intent(repository, operation_id, grant)
            committed.set()
            await release.wait()
            return intent

        with patch.object(repository_type, "commit_intent", committed_then_wait):
            request = asyncio.create_task(
                runtime.admin_operations.set_enabled(
                    None,
                    "private/mod",
                    True,
                    expected_registry_revision=runtime.registry.snapshot().revision,
                    authorization=_AdminContext(),
                )
            )
            await asyncio.wait_for(committed.wait(), timeout=2)
            operation = runtime.extension_runtime._operations[
                ("private", "private/mod")
            ]
            self.assertEqual(operation.phase.value, "committing")

            with self.assertRaises(CoreRuntimeCleanupPending) as caught:
                await runtime.close(timeout=0.02)
            self.assertEqual(caught.exception.component, "module_quiesce")
            self.assertFalse(runtime.closed)
            self.assertNotIn("private/mod", runtime.lifecycle.admission._open)
            self.assertFalse(request.done())

            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await request
            intent = await runtime.runtime_repository.current_intent("private", "mod")
            self.assertIsNotNone(intent)
            self.assertTrue(intent.desired_enabled)
            journal = await runtime.runtime_repository.current_journal(
                intent.operation_id
            )
            self.assertEqual(journal.phase.value, "recovery_required")
            self.assertFalse(runtime.registry.snapshot().module("private/mod").enabled)
            self.assertNotIn("private/mod", runtime.lifecycle.admission._open)

        self.assertTrue(await runtime.close(timeout=0.5))
        recovered = self._runtime(source=_FactorySource(_Factory(_Handler())))
        report = await recovered.start()
        self.assertEqual(report.extension_failures, ())
        self.assertTrue(recovered.registry.snapshot().module("private/mod").enabled)
        self.assertEqual(
            recovered.lifecycle.state("private/mod").lifecycle.value, "active"
        )
        self.assertTrue(await recovered.close(timeout=0.5))

    async def test_core_injects_same_utc_clock_into_real_admission_and_claim_proof(
        self,
    ):
        fixed = datetime(2026, 1, 1, tzinfo=UTC)
        runtime = self._runtime(utc_clock=lambda: fixed)
        await runtime.start()
        key = CollectionKey(
            "missing/mod",
            "collector",
            1,
            "source",
            NormalizedInput({}),
            OwnerScope.public(),
        )
        lease = ExecutionLease(
            key,
            "fixed-time-claim",
            1,
            1,
            1,
            fixed + timedelta(seconds=30),
        )
        runtime.execution_claim_proofs.prove(lease)
        self.assertTrue(runtime.execution_claim_proofs(lease))
        self.assertEqual(runtime.lifecycle.admission._now_utc(), fixed)
        with self.assertRaisesRegex(AdmissionError, "module is not registered"):
            runtime.lifecycle.admission.admit_schedule(lease, "collector")
        self.assertTrue(await runtime.close(timeout=0.5))

    async def test_core_startup_close_fences_late_restore_publication(self):
        package_root = self.root / "extensions" / "private"
        package_root.mkdir(parents=True, exist_ok=True)
        (package_root / "yomihime.manifest.json").write_text(
            json.dumps(_private_manifest()), encoding="utf-8"
        )
        original = self._runtime(source=_FactorySource(_Factory(_Handler())))
        await original.start()
        await original.admin_credential_repository.bootstrap(_digest(token_urlsafe(32)))
        grant = await original.admin_authorization.authorize(
            AdminOperation.SET_ENABLED,
            invocation=None,
            context=_AdminContext(),
        )
        await original.runtime_repository.prepare(
            "startup-restore-enable",
            "private",
            "mod",
            True,
            expected_intent_revision=0,
            expected_registry_revision=0,
            grant=grant,
        )
        persisted = await original.runtime_repository.commit_intent(
            "startup-restore-enable", grant
        )
        await original.close(timeout=0.5)

        recovering = self._runtime(source=_FactorySource(_Factory(_Handler())))
        entered = asyncio.Event()
        release = asyncio.Event()
        repository = recovering.runtime_repository
        current_journal = repository.current_journal
        blocked = False

        async def current_journal_with_barrier(operation_id):
            nonlocal blocked
            if operation_id == persisted.operation_id and not blocked:
                blocked = True
                entered.set()
                await release.wait()
            return await current_journal(operation_id)

        repository.current_journal = current_journal_with_barrier
        startup = asyncio.create_task(recovering.start())
        await asyncio.wait_for(entered.wait(), timeout=2)

        with self.assertRaises(CoreRuntimeCleanupPending) as caught:
            await recovering.close(timeout=0.02)
        self.assertEqual(caught.exception.component, "startup")
        self.assertFalse(recovering.closed)
        self.assertNotIn("private/mod", recovering.lifecycle.admission._open)
        self.assertFalse(recovering.registry.snapshot().modules["private/mod"].enabled)

        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await startup
        intent = await repository.current_intent("private", "mod")
        journal = await repository.current_journal(persisted.operation_id)
        self.assertEqual(intent, persisted)
        self.assertTrue(intent.desired_enabled)
        self.assertEqual(journal.phase.value, "recovery_required")
        self.assertNotIn("private/mod", recovering.lifecycle.admission._open)
        self.assertFalse(recovering.registry.snapshot().modules["private/mod"].enabled)
        self.assertTrue(await recovering.close(timeout=0.5))

        restored = self._runtime(source=_FactorySource(_Factory(_Handler())))
        report = await restored.start()
        self.assertTrue(restored.registry.snapshot().modules["private/mod"].enabled)
        self.assertEqual(
            restored.lifecycle.state("private/mod").lifecycle.value, "active"
        )
        self.assertEqual(report.extension_failures, ())
        self.assertTrue(await restored.close(timeout=0.5))

    async def test_close_during_ingress_validation_is_rechecked(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def validator(_origin, _ingress):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return True

        runtime = self._runtime(ingress_validator=validator)
        await runtime.start()
        ingress = _ingress()
        request = asyncio.create_task(
            runtime.invoke_command("missing/module", "read", {}, ingress=ingress)
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        with self.assertRaises(CoreRuntimeCleanupPending) as caught:
            await runtime.close(timeout=0.02)
        self.assertEqual(caught.exception.component, "host_ingress")
        release.set()
        with self.assertRaises(RuntimeError):
            await request
        self.assertTrue(await runtime.close(timeout=0.5))

    async def test_close_quiesces_modules_before_pending_pump_drain(self) -> None:
        handler = _Handler()
        runtime = self._runtime(source=_FactorySource(_Factory(handler)))
        await self._activate_private(runtime)

        entered = asyncio.Event()
        release = asyncio.Event()

        async def pending_page():
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()

        runtime.scheduler.run_due_page = pending_page
        runtime._pump_task = asyncio.get_running_loop().create_task(
            runtime._pump_loop()
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        with self.assertRaises(CoreRuntimeCleanupPending) as caught:
            await runtime.close(timeout=0.02)
        self.assertEqual(caught.exception.component, "scheduler_pump")
        self.assertFalse(runtime.closed)
        self.assertIsNone(runtime.lifecycle.admission._open.get("private/mod"))
        release.set()
        self.assertTrue(await runtime.close(timeout=0.5))

    async def test_close_keeps_worker_open_until_renderer_root_flight_quiets(self):
        handler = _Handler()
        entered = asyncio.Event()
        release = asyncio.Event()
        runtime = self._runtime(
            source=_FactorySource(_Factory(handler)),
            renderer=_Renderer(entered=entered, release=release),
        )
        await self._activate_private(runtime)
        await self._install_private_grant(runtime)
        request = asyncio.create_task(
            runtime.invoke_command(
                "private/mod",
                "read",
                {},
                ingress=_ingress(grant_reference=GrantReference("grant-one", 1)),
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        with self.assertRaises(CoreRuntimeCleanupPending) as caught:
            await runtime.close(timeout=0.02)
        self.assertEqual(caught.exception.component, "host_ingress")
        self.assertFalse(runtime.closed)
        self.assertEqual(
            (await runtime.admin_credential_repository.current()).generation, 1
        )
        release.set()
        try:
            await request
        except BaseException:
            pass
        self.assertFalse(runtime._host_flights)
        self.assertTrue(await runtime.close(timeout=0.5))

    async def test_close_during_route_publication_is_rechecked(self):
        runtime = self._runtime(source=_FactorySource(_Factory(_Handler())))
        await self._activate_private(runtime)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def blocked_publish(_publisher, reference):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return reference

        with patch.object(
            type(runtime.trusted_route_publisher), "publish", blocked_publish
        ):
            request = asyncio.create_task(
                runtime.invoke_command("private/mod", "read", {}, ingress=_ingress())
            )
            await asyncio.wait_for(entered.wait(), timeout=1)
            with self.assertRaises(CoreRuntimeCleanupPending) as caught:
                await runtime.close(timeout=0.02)
            self.assertEqual(caught.exception.component, "host_ingress")
            release.set()
            with self.assertRaises(RuntimeError):
                await request
        self.assertTrue(await runtime.close(timeout=0.5))

    async def test_close_during_startup_keeps_executor_owned_until_retry(self):
        runtime = self._runtime()
        entered = asyncio.Event()
        release = asyncio.Event()
        initialize = runtime.database.executor.initialize

        async def blocked_initialize(_executor):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return await initialize()

        with patch.object(
            type(runtime.database.executor), "initialize", blocked_initialize
        ):
            startup = asyncio.create_task(runtime.start())
            await asyncio.wait_for(entered.wait(), timeout=1)
            with self.assertRaises(CoreRuntimeCleanupPending) as caught:
                await runtime.close(timeout=0.02)
            self.assertEqual(caught.exception.component, "startup")
            self.assertFalse(runtime.closed)
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await startup
        self.assertFalse(runtime.accepting)
        self.assertTrue(await runtime.close(timeout=0.5))

    async def test_close_timeout_also_bounds_wait_for_close_owner(self):
        runtime = self._runtime()
        await runtime.start()
        await runtime._close_lock.acquire()
        try:
            with self.assertRaises(CoreRuntimeCleanupPending) as caught:
                await runtime.close(timeout=0.02)
            self.assertEqual(caught.exception.component, "close_lock")
            self.assertFalse(runtime.closed)
            self.assertEqual(
                (await runtime.admin_credential_repository.current()).generation,
                0,
            )
        finally:
            runtime._close_lock.release()
        self.assertTrue(await runtime.close(timeout=0.5))

    async def _activate_private(self, runtime: CoreRuntime) -> _AdminContext:
        package_root = self.root / "extensions" / "private"
        package_root.mkdir(parents=True, exist_ok=True)
        (package_root / "yomihime.manifest.json").write_text(
            json.dumps(_private_manifest()), encoding="utf-8"
        )
        await runtime.start()
        self.assertEqual(
            runtime.extension_runtime.candidates()[0].state.value, "disabled"
        )
        await runtime.admin_credential_repository.bootstrap(_digest(token_urlsafe(32)))
        context = _AdminContext()
        status = await runtime.admin_operations.set_enabled(
            None,
            "private/mod",
            True,
            expected_registry_revision=runtime.registry.snapshot().revision,
            authorization=context,
        )
        self.assertTrue(status.enabled)
        return context

    async def _install_private_grant(self, runtime: CoreRuntime) -> None:
        await runtime.repositories.identities.save_principal(
            Principal("principal-alice", "test-namespace", "alice")
        )
        receipt = await runtime.secret_store.put(
            b"test-private-secret",
            owner=SecretOwner(
                "principal-alice", "private/mod", "credential", "grant-operation"
            ),
        )
        grant = Grant(
            "grant-one",
            1,
            "principal-alice",
            "private/mod",
            "account-one",
            ("private",),
            receipt.secret_ref,
            GrantStatus.ACTIVE,
        )
        await runtime.repositories.authorization.create_grant(
            grant, expected_revision=0
        )


def _ingress(*, grant_reference: GrantReference | None = None) -> HostIngress:
    return HostIngress(
        "adapter-one",
        "alice",
        "conversation-one",
        "route-one",
        ConversationKind.DIRECT,
        object(),
        grant_reference,
    )


def _private_manifest(*, factory_entry: str = "factory:create") -> dict[str, object]:
    return {
        "schema_version": 1,
        "package_id": "private",
        "package_version": "1.0.0",
        "contract_version": "1.1.0",
        "author": "tests",
        "license": "MIT",
        "source": "test source bundle",
        "modules": [
            {
                "module_id": "mod",
                "route": "private",
                "category": "game",
                "factory_entry": factory_entry,
                "module_version": "1.0.0",
                "capabilities": [
                    {
                        "capability_id": "private",
                        "input_schema": {
                            "type": "object",
                            "properties": {},
                            "required": [],
                        },
                        "invocation_policy": "command_only",
                        "effect": "read_only",
                        "privacy_floor": "private",
                    }
                ],
                "commands": [
                    {
                        "operation_path": "read",
                        "capability_id": "private",
                        "parameter_mapping": {},
                        "help_text": "Read private data",
                    }
                ],
            }
        ],
    }


def _packaged_factory_source() -> str:
    return """
from yomihime_sdk.api.display import DisplayDocument, Privacy, TextBlock
from yomihime_sdk.api.results import CapabilityResult, ResultStatus
from yomihime_sdk.api.services import (
    CapabilityHealth, HealthReport, HealthStatus, ModuleHandlers,
)


class Handler:
    async def invoke(self, context, parameters):
        del context, parameters
        return CapabilityResult(
            "package-result",
            ResultStatus.SUCCESS,
            document=DisplayDocument(
                "Packaged", "Packaged", (TextBlock("ok"),),
                privacy=Privacy.PRIVATE,
            ),
            privacy=Privacy.PRIVATE,
        )


class Instance:
    def __init__(self):
        self.handler = Handler()

    def handlers(self):
        return ModuleHandlers({"private": self.handler}, {}, {})

    async def start(self):
        return None

    async def stop(self):
        return None

    async def check_health(self):
        return HealthReport({
            "private": CapabilityHealth(HealthStatus.AVAILABLE),
        })


class Factory:
    async def create(self, services):
        if services is None:
            raise TypeError("real ModuleServices required")
        return Instance()
"""


if __name__ == "__main__":
    unittest.main()
