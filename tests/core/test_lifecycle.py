from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from time import monotonic
from uuid import uuid4

from ygl_test_subject.api.administration import ModuleLifecycle
from ygl_test_subject.api.contexts import InvocationOrigin
from ygl_test_subject.api.manifests import (
    CapabilityDescriptor,
    CapabilityEffect,
    CommandDescriptor,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
    ToolDescriptor,
)
from ygl_test_subject.api.services import (
    CapabilityHealth,
    HealthReport,
    HealthStatus,
    ModuleHandlers,
)
from ygl_test_subject.api.version import CONTRACT_VERSION
from ygl_test_subject.core.admission import AdmissionError
from ygl_test_subject.core.context_issuer import InvalidInvocation
from ygl_test_subject.core.lifecycle import (
    LifecycleController,
    LifecycleError,
    LifecycleStopTimeout,
    StaleEpochError,
)
from ygl_test_subject.core.registry import Registry, RegistryError
from ygl_test_subject.core.task_scope import (
    ScopeDeadlineExceeded,
    ScopeStopTimeout,
    TaskScope,
)


class _Handler:
    def __init__(self) -> None:
        self.calls = 0

    async def invoke(self, context, parameters):
        self.calls += 1
        return None


class _Instance:
    def __init__(self, handlers: ModuleHandlers) -> None:
        self._handlers = handlers
        self.started = 0
        self.stopped = 0

    def handlers(self) -> ModuleHandlers:
        return self._handlers

    async def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        self.stopped += 1

    async def check_health(self) -> HealthReport:
        return HealthReport({"read": CapabilityHealth(HealthStatus.AVAILABLE)})


class _InvalidHandlersInstance(_Instance):
    def handlers(self):
        return None


class _RaisingHandlersInstance(_Instance):
    def handlers(self):
        raise RuntimeError("handlers construction failed")


class _StubbornStopInstance(_Instance):
    def __init__(self, handlers):
        super().__init__(handlers)
        self.stop_started = asyncio.Event()
        self.release_stop = asyncio.Event()

    async def stop(self) -> None:
        self.stopped += 1
        self.stop_started.set()
        while not self.release_stop.is_set():
            try:
                await self.release_stop.wait()
            except asyncio.CancelledError:
                continue


class _FailingCleanupInstance(_Instance):
    def __init__(
        self,
        handlers: ModuleHandlers,
        *,
        fail_start: bool = False,
        fail_health: bool = False,
        stop_failures: int = 1,
    ) -> None:
        super().__init__(handlers)
        self._start_failures = int(fail_start)
        self._health_failures = int(fail_health)
        self._stop_failures = stop_failures

    async def start(self) -> None:
        self.started += 1
        if self._start_failures:
            self._start_failures -= 1
            raise RuntimeError("start failed")

    async def check_health(self) -> HealthReport:
        if self._health_failures:
            self._health_failures -= 1
            raise RuntimeError("health failed")
        return await super().check_health()

    async def stop(self) -> None:
        self.stopped += 1
        if self._stop_failures:
            self._stop_failures -= 1
            raise RuntimeError("stop failed")


def _registry() -> Registry:
    capability = CapabilityDescriptor(
        capability_id="read",
        input_schema={"type": "object"},
        invocation_policy=InvocationPolicy.NATURAL_LANGUAGE_ALLOWED,
        effect=CapabilityEffect.READ_ONLY,
    )
    module = ModuleManifest(
        module_id="mod",
        route="mod",
        category=ModuleCategory.GAME,
        factory_entry="tests:Factory",
        module_version="1.0.0",
        capabilities=(capability,),
        commands=(
            CommandDescriptor(
                operation_path="read",
                capability_id="read",
                parameter_mapping={},
                help_text="read",
            ),
        ),
    )
    package = PackageManifest(
        package_id="pkg",
        package_version="1.0.0",
        contract_version=CONTRACT_VERSION,
        modules=(module,),
        author="tests",
        license="AGPL-3.0",
        source="offline",
    )
    registry = Registry()
    handlers = ModuleHandlers({"read": _Handler()}, {}, {})
    registry.register_package(package, {"mod": handlers})
    return registry


async def _activate(
    registry: Registry,
    controller: LifecycleController,
    *,
    operation_id: str | None = None,
):
    module = registry.snapshot().module("pkg/mod")
    handlers = module.handlers
    instance = _Instance(handlers)
    install_id = operation_id or uuid4().hex
    handlers = controller.adopt_candidate("pkg", module.manifest, install_id, instance)
    controller.install_dormant("pkg", "pkg/mod", install_id, instance, handlers)
    run_id = uuid4().hex
    identity, _ = await controller.start_candidate("pkg/mod", run_id)
    controller.publish_committed_intent(
        "pkg/mod", run_id, identity, True, registry.snapshot().revision
    )
    return controller.state("pkg/mod"), instance


def _candidate_package(package_id, module):
    return PackageManifest(
        package_id=package_id,
        package_version="1.0.0",
        contract_version=CONTRACT_VERSION,
        modules=(module,),
        author="tests",
        license="AGPL-3.0",
        source="offline",
    )


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_injected_utc_clock_is_forwarded_to_real_admission(self):
        fixed = datetime(2026, 1, 1, tzinfo=UTC)
        controller = LifecycleController(_registry(), utc_clock=lambda: fixed)
        self.assertEqual(controller.admission._now_utc(), fixed)

    async def test_real_instance_activation_and_sync_start_is_idempotent_only(
        self,
    ) -> None:
        registry = _registry()
        controller = LifecycleController(registry)
        with self.assertRaises(LifecycleError):
            controller.start("pkg/mod")
        state, instance = await _activate(registry, controller)
        self.assertEqual(state.lifecycle, ModuleLifecycle.ACTIVE)
        self.assertIs(controller.start("pkg/mod").identity, state.identity)
        with self.assertRaises(RegistryError):
            registry.set_enabled("pkg/mod", False)
        self.assertEqual((instance.started, instance.stopped), (1, 0))

    async def test_candidate_is_owned_before_handlers_and_can_be_discarded_unregistered(
        self,
    ) -> None:
        registry = Registry()
        controller = LifecycleController(registry)
        manifest = _registry().snapshot().module("pkg/mod").manifest
        handlers = ModuleHandlers({"read": _Handler()}, {}, {})
        for instance, expected in (
            (_InvalidHandlersInstance(handlers), TypeError),
            (_RaisingHandlersInstance(handlers), RuntimeError),
        ):
            with self.subTest(instance=type(instance).__name__):
                operation_id = uuid4().hex
                with self.assertRaises(expected):
                    controller.adopt_candidate("pkg", manifest, operation_id, instance)

                self.assertEqual(registry.snapshot().revision, 0)
                self.assertIs(
                    controller._candidates[("pkg", "pkg/mod", operation_id)][
                        0
                    ].instance,
                    instance,
                )
                self.assertTrue(
                    await controller.discard_candidate(
                        "pkg", "pkg/mod", operation_id, timeout=0.1
                    )
                )
                self.assertEqual(instance.stopped, 1)
                self.assertNotIn(
                    ("pkg", "pkg/mod", operation_id), controller._candidates
                )

    async def test_late_factory_result_is_adopted_after_request_cancellation(self):
        registry = Registry()
        controller = LifecycleController(registry)
        manifest = _registry().snapshot().module("pkg/mod").manifest
        instance = _Instance(ModuleHandlers({"read": _Handler()}, {}, {}))
        release_factory = asyncio.Event()
        returned = asyncio.Event()

        async def factory():
            await release_factory.wait()
            returned.set()
            return instance

        factory_flight = asyncio.create_task(factory())

        async def caller():
            return await asyncio.shield(factory_flight)

        request = asyncio.create_task(caller())
        await asyncio.sleep(0)
        request.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await request

        self.assertFalse(factory_flight.done())
        self.assertEqual(instance.stopped, 0)
        release_factory.set()
        late_instance = await factory_flight
        self.assertIs(late_instance, instance)
        self.assertTrue(returned.is_set())

        operation_id = uuid4().hex
        controller.adopt_candidate("pkg", manifest, operation_id, late_instance)
        self.assertTrue(
            await controller.discard_candidate(
                "pkg", "pkg/mod", operation_id, timeout=0.1
            )
        )
        self.assertEqual(instance.stopped, 1)
        self.assertFalse(controller._candidates)

    async def test_registry_route_and_tool_conflicts_discard_owned_candidates(self):
        registry = _registry()
        controller = LifecycleController(registry)
        base = registry.snapshot().module("pkg/mod").manifest
        route_manifest = replace(base, module_id="route_collision", route="mod")
        route_handlers = ModuleHandlers({"read": _Handler()}, {}, {})
        route_instance = _Instance(route_handlers)
        route_operation = uuid4().hex
        route_handlers = controller.adopt_candidate(
            "route_pkg", route_manifest, route_operation, route_instance
        )
        with self.assertRaises(RegistryError):
            registry.register_package(
                _candidate_package("route_pkg", route_manifest),
                {"route_collision": route_handlers},
            )
        self.assertTrue(
            await controller.discard_candidate(
                "route_pkg",
                "route_pkg/route_collision",
                route_operation,
                timeout=0.1,
            )
        )
        self.assertEqual(route_instance.stopped, 1)
        self.assertNotIn("route_pkg/route_collision", registry.snapshot().modules)

        tool = ToolDescriptor(
            name="shared_tool",
            capability_id="read",
            parameter_mapping={},
            description="A shared test Tool",
        )
        tool_manifest = replace(base, tools=(tool,))
        tool_registry = Registry()
        tool_registry.register_package(
            _candidate_package("pkg", tool_manifest),
            {"mod": ModuleHandlers({"read": _Handler()}, {}, {})},
        )
        tool_controller = LifecycleController(tool_registry)
        duplicate_tool_manifest = replace(
            base,
            module_id="tool_collision",
            route="tool_collision",
            tools=(tool,),
        )
        tool_handlers = ModuleHandlers({"read": _Handler()}, {}, {})
        tool_instance = _Instance(tool_handlers)
        tool_operation = uuid4().hex
        tool_handlers = tool_controller.adopt_candidate(
            "tool_pkg", duplicate_tool_manifest, tool_operation, tool_instance
        )
        with self.assertRaises(RegistryError):
            tool_registry.register_package(
                _candidate_package("tool_pkg", duplicate_tool_manifest),
                {"tool_collision": tool_handlers},
            )
        self.assertTrue(
            await tool_controller.discard_candidate(
                "tool_pkg",
                "tool_pkg/tool_collision",
                tool_operation,
                timeout=0.1,
            )
        )
        self.assertEqual(tool_instance.stopped, 1)
        self.assertNotIn("tool_pkg/tool_collision", tool_registry.snapshot().modules)

    async def test_candidate_discard_isolated_from_active_siblings_and_old_key(self):
        registry = _registry()
        controller = LifecycleController(registry)
        active, instance = await _activate(registry, controller)
        self.assertTrue(
            await controller.discard_candidate(
                "pkg",
                "pkg/mod",
                controller._instances["pkg/mod"].installation_operation_id,
                timeout=0.1,
            )
        )
        self.assertIs(controller.current_identity("pkg/mod"), active.identity)
        self.assertEqual(instance.stopped, 0)

        other_manifest = replace(
            registry.snapshot().module("pkg/mod").manifest,
            module_id="sibling",
            route="sibling",
        )
        package = _candidate_package("sibling_pkg", other_manifest)
        registry.register_package(
            package,
            {"sibling": ModuleHandlers({"read": _Handler()}, {}, {})},
        )
        sibling_instance = _Instance(
            registry.snapshot().module("sibling_pkg/sibling").handlers
        )
        sibling_operation = uuid4().hex
        sibling_handlers = controller.adopt_candidate(
            "sibling_pkg", other_manifest, sibling_operation, sibling_instance
        )
        controller.install_dormant(
            "sibling_pkg",
            "sibling_pkg/sibling",
            sibling_operation,
            sibling_instance,
            sibling_handlers,
        )
        sibling_run = uuid4().hex
        sibling_identity, _ = await controller.start_candidate(
            "sibling_pkg/sibling", sibling_run
        )
        controller.publish_committed_intent(
            "sibling_pkg/sibling",
            sibling_run,
            sibling_identity,
            True,
            registry.snapshot().revision,
        )

        replacement = _Instance(ModuleHandlers({"read": _Handler()}, {}, {}))
        candidate_operation = uuid4().hex
        controller.adopt_candidate(
            "pkg",
            registry.snapshot().module("pkg/mod").manifest,
            candidate_operation,
            replacement,
        )
        self.assertTrue(
            await controller.discard_candidate(
                "pkg", "pkg/mod", candidate_operation, timeout=0.1
            )
        )
        self.assertEqual(replacement.stopped, 1)
        self.assertEqual(instance.stopped, 0)
        self.assertEqual(sibling_instance.stopped, 0)
        self.assertIs(controller.current_identity("pkg/mod"), active.identity)
        self.assertIs(
            controller.current_identity("sibling_pkg/sibling"), sibling_identity
        )

    async def test_install_cannot_orphan_a_stopped_dormant_instance(self):
        registry = _registry()
        controller = LifecycleController(registry)
        manifest = registry.snapshot().module("pkg/mod").manifest
        instance_a = _Instance(ModuleHandlers({"read": _Handler()}, {}, {}))
        operation_a = uuid4().hex
        handlers_a = controller.adopt_candidate(
            "pkg", manifest, operation_a, instance_a
        )
        controller.install_dormant(
            "pkg", "pkg/mod", operation_a, instance_a, handlers_a
        )

        instance_b = _Instance(ModuleHandlers({"read": _Handler()}, {}, {}))
        operation_b = uuid4().hex
        handlers_b = controller.adopt_candidate(
            "pkg", manifest, operation_b, instance_b
        )
        with self.assertRaises(LifecycleError):
            controller.install_dormant(
                "pkg", "pkg/mod", operation_b, instance_b, handlers_b
            )

        self.assertIs(controller.instance("pkg/mod"), instance_a)
        self.assertEqual(instance_a.stopped, 0)
        self.assertTrue(
            any(
                record.instance is instance_b
                for record in controller._candidates[("pkg", "pkg/mod", operation_b)]
            )
        )
        self.assertTrue(
            await controller.discard_candidate(
                "pkg", "pkg/mod", operation_b, timeout=0.1
            )
        )
        self.assertEqual(instance_b.stopped, 1)

        run_operation = uuid4().hex
        identity, _ = await controller.start_candidate("pkg/mod", run_operation)
        controller.publish_committed_intent(
            "pkg/mod",
            run_operation,
            identity,
            True,
            registry.snapshot().revision,
        )
        self.assertIs(controller.instance("pkg/mod"), instance_a)
        self.assertEqual(instance_a.started, 1)

    async def test_rollback_stops_and_releases_never_started_dormant_candidate(self):
        registry = _registry()
        controller = LifecycleController(registry)
        manifest = registry.snapshot().module("pkg/mod").manifest
        instance = _Instance(ModuleHandlers({"read": _Handler()}, {}, {}))
        install_id = uuid4().hex
        handlers = controller.adopt_candidate("pkg", manifest, install_id, instance)
        controller.install_dormant("pkg", "pkg/mod", install_id, instance, handlers)

        self.assertTrue(
            await controller.rollback_unpublished_candidate(
                "pkg/mod",
                uuid4().hex,
                0.1,
                installation_operation_id=install_id,
            )
        )
        self.assertEqual(instance.started, 0)
        self.assertEqual(instance.stopped, 1)
        self.assertNotIn("pkg/mod", controller._instances)
        self.assertNotIn("pkg/mod", controller._states)
        self.assertFalse(registry.snapshot().module("pkg/mod").enabled)

    async def test_rollback_after_commit_conflict_uses_distinct_install_and_run_ids(
        self,
    ):
        registry = _registry()
        controller = LifecycleController(registry)
        manifest = registry.snapshot().module("pkg/mod").manifest
        instance = _Instance(ModuleHandlers({"read": _Handler()}, {}, {}))
        install_id = uuid4().hex
        handlers = controller.adopt_candidate("pkg", manifest, install_id, instance)
        controller.install_dormant("pkg", "pkg/mod", install_id, instance, handlers)

        start_id = uuid4().hex
        identity, _ = await controller.start_candidate("pkg/mod", start_id)
        before_rollback_epoch = controller.state("pkg/mod").epoch
        with self.assertRaises(LifecycleError):
            controller.publish_committed_intent(
                "pkg/mod",
                start_id,
                identity,
                True,
                registry.snapshot().revision + 1,
            )
        self.assertTrue(controller.state("pkg/mod").cleanup_pending)
        self.assertFalse(registry.snapshot().module("pkg/mod").enabled)
        with self.assertRaises(LifecycleError):
            await controller.start_candidate("pkg/mod", start_id)
        self.assertEqual(instance.started, 1)

        self.assertTrue(
            await controller.rollback_unpublished_candidate(
                "pkg/mod",
                start_id,
                0.1,
                installation_operation_id=install_id,
            )
        )
        self.assertEqual(instance.started, 1)
        self.assertEqual(instance.stopped, 1)
        self.assertGreater(controller._epochs["pkg/mod"], before_rollback_epoch)
        self.assertNotIn("pkg/mod", controller._instances)

    async def test_rollback_rejects_stale_install_id_and_previously_active_instance(
        self,
    ):
        registry = _registry()
        controller = LifecycleController(registry)
        manifest = registry.snapshot().module("pkg/mod").manifest
        instance = _Instance(ModuleHandlers({"read": _Handler()}, {}, {}))
        install_id = uuid4().hex
        handlers = controller.adopt_candidate("pkg", manifest, install_id, instance)
        controller.install_dormant("pkg", "pkg/mod", install_id, instance, handlers)

        with self.assertRaises(LifecycleError):
            await controller.rollback_unpublished_candidate(
                "pkg/mod",
                uuid4().hex,
                0.1,
                installation_operation_id=uuid4().hex,
            )
        self.assertIs(controller.instance("pkg/mod"), instance)
        self.assertEqual(instance.stopped, 0)

        run_id = uuid4().hex
        identity, _ = await controller.start_candidate("pkg/mod", run_id)
        controller.publish_committed_intent(
            "pkg/mod", run_id, identity, True, registry.snapshot().revision
        )
        await controller.stop("pkg/mod")
        with self.assertRaises(LifecycleError):
            await controller.rollback_unpublished_candidate(
                "pkg/mod",
                uuid4().hex,
                0.1,
                installation_operation_id=install_id,
            )
        self.assertIs(controller.instance("pkg/mod"), instance)
        self.assertEqual(instance.stopped, 1)

    async def test_rollback_repeated_cancel_keeps_noncooperative_instance_owned(self):
        registry = _registry()
        controller = LifecycleController(registry, stop_timeout=0.02)
        manifest = registry.snapshot().module("pkg/mod").manifest
        instance = _StubbornStopInstance(ModuleHandlers({"read": _Handler()}, {}, {}))
        install_id = uuid4().hex
        handlers = controller.adopt_candidate("pkg", manifest, install_id, instance)
        controller.install_dormant("pkg", "pkg/mod", install_id, instance, handlers)
        start_id = uuid4().hex
        await controller.start_candidate("pkg/mod", start_id)

        rollback = asyncio.create_task(
            controller.rollback_unpublished_candidate(
                "pkg/mod",
                start_id,
                0.02,
                installation_operation_id=install_id,
            )
        )
        await instance.stop_started.wait()
        rollback.cancel()
        await asyncio.sleep(0)
        rollback.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await rollback

        pending = controller.state("pkg/mod")
        self.assertEqual(pending.lifecycle, ModuleLifecycle.FAILED)
        self.assertTrue(pending.cleanup_pending)
        self.assertIs(controller.instance("pkg/mod"), instance)
        self.assertFalse(registry.snapshot().module("pkg/mod").enabled)
        with self.assertRaises(LifecycleError):
            await controller.start_candidate("pkg/mod", uuid4().hex)
        stop_task = controller._instances["pkg/mod"].stop_task
        self.assertIsNotNone(stop_task)

        instance.release_stop.set()
        if stop_task is not None:
            await asyncio.gather(stop_task, return_exceptions=True)
        self.assertTrue(
            await controller.rollback_unpublished_candidate(
                "pkg/mod",
                start_id,
                0.1,
                installation_operation_id=install_id,
            )
        )
        self.assertNotIn("pkg/mod", controller._instances)

    async def test_failed_start_or_health_cleanup_blocks_until_same_owner_stops(self):
        for failure_stage in ("start", "health"):
            with self.subTest(failure_stage=failure_stage):
                registry = _registry()
                controller = LifecycleController(registry)
                manifest = registry.snapshot().module("pkg/mod").manifest
                instance = _FailingCleanupInstance(
                    ModuleHandlers({"read": _Handler()}, {}, {}),
                    fail_start=failure_stage == "start",
                    fail_health=failure_stage == "health",
                )
                install_id = uuid4().hex
                handlers = controller.adopt_candidate(
                    "pkg", manifest, install_id, instance
                )
                controller.install_dormant(
                    "pkg", "pkg/mod", install_id, instance, handlers
                )
                operation_id = uuid4().hex

                with self.assertRaisesRegex(RuntimeError, f"{failure_stage} failed"):
                    await controller.start_candidate("pkg/mod", operation_id)

                failed = controller.state("pkg/mod")
                self.assertEqual(failed.lifecycle, ModuleLifecycle.FAILED)
                self.assertTrue(failed.cleanup_pending)
                self.assertFalse(controller._instances["pkg/mod"].stop_completed)
                self.assertFalse(registry.snapshot().module("pkg/mod").enabled)
                self.assertIs(controller.instance("pkg/mod"), instance)
                with self.assertRaises(LifecycleError):
                    await controller.start_candidate("pkg/mod", operation_id)
                with self.assertRaises(LifecycleError):
                    await controller.start_candidate("pkg/mod", uuid4().hex)
                self.assertEqual(instance.started, 1)
                with self.assertRaises(AdmissionError):
                    controller.admission.activate(
                        failed.identity,
                        HealthReport(
                            {"read": CapabilityHealth(HealthStatus.AVAILABLE)}
                        ),
                    )

                stopped = await controller.stop_candidate("pkg/mod", operation_id, 0.1)
                self.assertEqual(stopped.lifecycle, ModuleLifecycle.STOPPED)
                self.assertFalse(stopped.cleanup_pending)
                self.assertTrue(controller._instances["pkg/mod"].stop_completed)

                retry_id = uuid4().hex
                identity, _ = await controller.start_candidate("pkg/mod", retry_id)
                controller.publish_committed_intent(
                    "pkg/mod",
                    retry_id,
                    identity,
                    True,
                    registry.snapshot().revision,
                )
                self.assertEqual(
                    controller.state("pkg/mod").lifecycle, ModuleLifecycle.ACTIVE
                )
                self.assertEqual(instance.stopped, 2)

    async def test_stop_candidate_exception_keeps_cleanup_pending_and_allows_retry(
        self,
    ):
        registry = _registry()
        controller = LifecycleController(registry)
        manifest = registry.snapshot().module("pkg/mod").manifest
        instance = _FailingCleanupInstance(
            ModuleHandlers({"read": _Handler()}, {}, {}),
            stop_failures=1,
        )
        install_id = uuid4().hex
        handlers = controller.adopt_candidate("pkg", manifest, install_id, instance)
        controller.install_dormant("pkg", "pkg/mod", install_id, instance, handlers)
        start_id = uuid4().hex
        identity, _ = await controller.start_candidate("pkg/mod", start_id)
        controller.publish_committed_intent(
            "pkg/mod", start_id, identity, True, registry.snapshot().revision
        )

        with self.assertRaisesRegex(RuntimeError, "stop failed"):
            await controller.stop("pkg/mod")
        failed = controller.state("pkg/mod")
        self.assertEqual(failed.lifecycle, ModuleLifecycle.FAILED)
        self.assertTrue(failed.cleanup_pending)
        self.assertFalse(registry.snapshot().module("pkg/mod").enabled)
        self.assertIs(controller.instance("pkg/mod"), instance)
        with self.assertRaises(LifecycleError):
            await controller.start_candidate("pkg/mod", uuid4().hex)

        stopped = await controller.stop("pkg/mod")
        self.assertEqual(stopped.lifecycle, ModuleLifecycle.STOPPED)
        self.assertFalse(stopped.cleanup_pending)
        run_id = uuid4().hex
        identity, _ = await controller.start_candidate("pkg/mod", run_id)
        controller.publish_committed_intent(
            "pkg/mod", run_id, identity, True, registry.snapshot().revision
        )
        self.assertEqual(controller.state("pkg/mod").lifecycle, ModuleLifecycle.ACTIVE)

    async def test_admission_activation_failure_requires_instance_cleanup_before_restart(
        self,
    ):
        registry = _registry()
        controller = LifecycleController(registry)
        manifest = registry.snapshot().module("pkg/mod").manifest
        instance = _FailingCleanupInstance(
            ModuleHandlers({"read": _Handler()}, {}, {}), stop_failures=0
        )
        install_id = uuid4().hex
        handlers = controller.adopt_candidate("pkg", manifest, install_id, instance)
        controller.install_dormant("pkg", "pkg/mod", install_id, instance, handlers)
        start_id = uuid4().hex
        identity, _ = await controller.start_candidate("pkg/mod", start_id)
        activate = controller.admission.activate

        def fail_activation(candidate, health):
            raise AdmissionError("injected activation failure")

        controller.admission.activate = fail_activation
        with self.assertRaisesRegex(AdmissionError, "injected activation failure"):
            controller.publish_committed_intent(
                "pkg/mod", start_id, identity, True, registry.snapshot().revision
            )
        controller.admission.activate = activate

        failed = controller.state("pkg/mod")
        self.assertEqual(failed.lifecycle, ModuleLifecycle.FAILED)
        self.assertTrue(failed.cleanup_pending)
        self.assertTrue(registry.snapshot().module("pkg/mod").enabled)
        self.assertFalse(controller._instances["pkg/mod"].stop_completed)
        with self.assertRaises(AdmissionError):
            controller.admission.activate(
                identity,
                HealthReport({"read": CapabilityHealth(HealthStatus.AVAILABLE)}),
            )
        with self.assertRaises(LifecycleError):
            await controller.start_candidate("pkg/mod", start_id)

        stopped = await controller.stop("pkg/mod")
        self.assertEqual(stopped.lifecycle, ModuleLifecycle.STOPPED)
        self.assertFalse(stopped.cleanup_pending)
        retry_id = uuid4().hex
        identity, _ = await controller.start_candidate("pkg/mod", retry_id)
        controller.publish_committed_intent(
            "pkg/mod", retry_id, identity, True, registry.snapshot().revision
        )
        self.assertEqual(controller.state("pkg/mod").lifecycle, ModuleLifecycle.ACTIVE)

    async def test_noncooperative_candidate_stop_survives_repeated_cancellation(self):
        registry = _registry()
        controller = LifecycleController(registry, stop_timeout=0.02)
        manifest = registry.snapshot().module("pkg/mod").manifest
        instance = _StubbornStopInstance(ModuleHandlers({"read": _Handler()}, {}, {}))
        operation_id = uuid4().hex
        controller.adopt_candidate("pkg", manifest, operation_id, instance)

        discard = asyncio.create_task(
            controller.discard_candidate("pkg", "pkg/mod", operation_id, 0.02)
        )
        await instance.stop_started.wait()
        discard.cancel()
        await asyncio.sleep(0)
        discard.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await discard

        self.assertTrue(controller._candidates[("pkg", "pkg/mod", operation_id)])
        candidate = controller._candidates[("pkg", "pkg/mod", operation_id)][0]
        self.assertIsNotNone(candidate.stop_task)
        with self.assertRaises(LifecycleError):
            await controller.start_candidate("pkg/mod", uuid4().hex)

        instance.release_stop.set()
        if candidate.stop_task is not None:
            await asyncio.gather(candidate.stop_task, return_exceptions=True)
        self.assertTrue(
            await controller.discard_candidate("pkg", "pkg/mod", operation_id, 0.1)
        )
        self.assertFalse(controller._candidates)

    async def test_admission_uses_exact_view_lease_and_ignores_directory_revision(
        self,
    ) -> None:
        registry = _registry()
        controller = LifecycleController(registry)
        state, _ = await _activate(registry, controller)
        issuer = controller.issuer
        view = issuer.issue(
            origin=InvocationOrigin.COMMAND,
            module_id="pkg/mod",
            module_epoch=state.epoch,
            registry_revision=state.registry_revision,
            actor_id="user",
            conversation_id="conversation",
            capability_id="read",
        )
        lease = controller.admission.admit(view, "read")
        controller.guard("pkg/mod", invocation=view)
        controller.admission.check(lease)

        unrelated = _registry().snapshot().module("pkg/mod")
        registry.register_package(
            PackageManifest(
                package_id="other",
                package_version="1.0.0",
                contract_version=CONTRACT_VERSION,
                modules=(
                    replace(unrelated.manifest, module_id="other", route="other"),
                ),
                author="tests",
                license="AGPL-3.0",
                source="offline",
            ),
            {"other": ModuleHandlers({"read": _Handler()}, {}, {})},
        )
        controller.guard(
            "pkg/mod",
            epoch=state.epoch,
            registry_revision=state.registry_revision,
            invocation=view,
        )

        copied = replace(lease)
        with self.assertRaises(AdmissionError):
            controller.admission.check(copied)
        issuer.release(view)
        with self.assertRaises(InvalidInvocation):
            issuer.require(view)
        with self.assertRaises(AdmissionError):
            controller.admission.check(lease)

    async def test_stop_quiesces_before_cleanup_and_restart_allocates_new_epoch(
        self,
    ) -> None:
        registry = _registry()
        controller = LifecycleController(registry)
        old, instance = await _activate(registry, controller)
        old_scope = old.scope
        handler = controller.handlers("pkg/mod").capabilities["read"]
        view = controller.issuer.issue(
            origin=InvocationOrigin.COMMAND,
            module_id="pkg/mod",
            module_epoch=old.epoch,
            registry_revision=old.registry_revision,
            actor_id="user",
            conversation_id="conversation",
            capability_id="read",
        )
        lease = controller.admission.admit(view, "read")
        task = old_scope.create_task(asyncio.sleep(0), name="short-work")
        stopped = await controller.stop("pkg/mod")
        self.assertEqual(stopped.lifecycle, ModuleLifecycle.STOPPED)
        self.assertTrue(task.done())
        self.assertEqual(instance.stopped, 1)
        with self.assertRaises(StaleEpochError):
            controller.guard("pkg/mod", epoch=old.epoch)
        with self.assertRaises(AdmissionError):
            controller.admission.check(lease)
        self.assertEqual(handler.calls, 0)

        # The old compatibility projection cannot manufacture an active run.
        registry.set_enabled("pkg/mod", True)
        with self.assertRaises(LifecycleError):
            controller.start("pkg/mod")

    async def test_noncooperative_task_remains_owned_and_gate_closed_until_retry(
        self,
    ) -> None:
        registry = _registry()
        controller = LifecycleController(registry, stop_timeout=0.03)
        state, _ = await _activate(registry, controller)
        release = asyncio.Event()
        started = asyncio.Event()

        async def stubborn() -> None:
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()

        state.scope.create_task(stubborn(), name="noncooperative")
        await started.wait()
        with self.assertRaises(LifecycleStopTimeout):
            await controller.stop("pkg/mod")
        failed = controller.state("pkg/mod")
        self.assertEqual(failed.lifecycle, ModuleLifecycle.FAILED)
        self.assertTrue(failed.cleanup_pending)
        self.assertTrue(state.scope.tasks)
        self.assertFalse(registry.snapshot().module("pkg/mod").enabled)
        with self.assertRaises(LifecycleError):
            await controller.start_candidate("pkg/mod", uuid4().hex)

        release.set()
        await state.scope.wait(1)
        stopped = await controller.stop("pkg/mod")
        self.assertEqual(stopped.lifecycle, ModuleLifecycle.STOPPED)
        self.assertFalse(stopped.cleanup_pending)

    async def test_cancelled_stop_is_bounded_and_does_not_release_pending_task(
        self,
    ) -> None:
        registry = _registry()
        controller = LifecycleController(registry, stop_timeout=0.04)
        state, _ = await _activate(registry, controller)
        release = asyncio.Event()
        started = asyncio.Event()

        async def stubborn() -> None:
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()

        state.scope.create_task(stubborn(), name="cancel-stop")
        await started.wait()
        stopper = asyncio.create_task(controller.stop("pkg/mod"))
        await asyncio.sleep(0)
        stopper.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await stopper
        failed = controller.state("pkg/mod")
        self.assertEqual(failed.reason_code, "stop_cancelled")
        self.assertTrue(failed.cleanup_pending)
        self.assertTrue(state.scope.tasks)
        release.set()
        await state.scope.wait(1)
        await controller.stop("pkg/mod")

    async def test_task_scope_deadline_and_zero_turn_cancel_close_awaitable(
        self,
    ) -> None:
        deadline_scope = TaskScope("pkg/mod", 1, deadline_monotonic=monotonic() + 0.05)
        started = asyncio.Event()
        task = deadline_scope.create_task(_signal_after(started), name="deadline")
        await started.wait()
        # The explicit TaskScope.run path actively times out a running awaitable.
        with self.assertRaises(ScopeDeadlineExceeded):
            await deadline_scope.run(
                asyncio.sleep(2), name="bounded", deadline_monotonic=0.01
            )
        with self.assertRaises(ScopeDeadlineExceeded):
            await task

        scope = TaskScope("pkg/mod", 2, cleanup_timeout=0.02)
        work = asyncio.sleep(1)
        task = scope.create_task(work, name="never-started")
        scope.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await scope.wait(1)
        self.assertIsNone(work.cr_frame)
        self.assertFalse(scope.tasks)

    async def test_repeated_cancel_during_cleanup_keeps_task_and_notifies_owner(
        self,
    ) -> None:
        notifications = []
        scope = TaskScope(
            "pkg/mod",
            3,
            cleanup_timeout=0.03,
            on_cleanup_timeout=notifications.append,
        )
        release = asyncio.Event()
        started = asyncio.Event()

        async def stubborn() -> None:
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()

        owner = asyncio.create_task(
            scope.run(stubborn(), name="repeat-cancel", deadline_monotonic=None)
        )
        await started.wait()
        owner.cancel()
        await asyncio.sleep(0)
        owner.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await owner
        self.assertTrue(scope.cleanup_pending)
        self.assertEqual(notifications, ["cancel_cleanup_timeout"])
        release.set()
        await scope.wait(1)
        self.assertFalse(scope.cleanup_pending)

    async def test_live_deadline_timeout_closes_admission_and_keeps_io_owned(self):
        registry = _registry()
        controller = LifecycleController(registry, stop_timeout=0.03)
        state, _ = await _activate(registry, controller)
        view = controller.issuer.issue(
            origin=InvocationOrigin.COMMAND,
            module_id="pkg/mod",
            module_epoch=state.epoch,
            registry_revision=state.registry_revision,
            actor_id="user",
            conversation_id="conversation",
            capability_id="read",
        )
        lease = controller.admission.admit(view, "read")
        release = asyncio.Event()
        started = asyncio.Event()

        async def stubborn() -> None:
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()

        run_task = asyncio.create_task(
            state.scope.run(
                stubborn(),
                name="deadline-io",
                deadline_monotonic=monotonic() + 0.03,
            )
        )
        await started.wait()
        with self.assertRaises(ScopeStopTimeout):
            await run_task
        isolated = controller.state("pkg/mod")
        self.assertEqual(isolated.lifecycle, ModuleLifecycle.FAILED)
        self.assertTrue(isolated.cleanup_pending)
        self.assertGreater(isolated.epoch, state.epoch)
        with self.assertRaises(AdmissionError):
            controller.admission.check(lease)
        self.assertTrue(state.scope.tasks)

        release.set()
        await state.scope.wait(1)
        await controller.stop("pkg/mod")


async def _signal_after(event: asyncio.Event) -> None:
    event.set()
    await asyncio.sleep(1)


if __name__ == "__main__":
    unittest.main()
