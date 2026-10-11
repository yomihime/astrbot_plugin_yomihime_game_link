from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from uuid import uuid4

from ygl_test_subject.core.context_issuer import ContextIssuer
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.health import HealthResolver
from ygl_test_subject.core.invocation import Gateway
from ygl_test_subject.core.lifecycle import LifecycleController
from ygl_test_subject.core.registry import Registry

from yomihime_game_link_sdk.contexts import InvocationOrigin
from yomihime_game_link_sdk.declarations import (
    CapabilityDescriptor,
    CapabilityEffect,
    CapabilityReference,
    CommandDescriptor,
    ConfigField,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
    SourceDeclaration,
)
from yomihime_game_link_sdk.display import DisplayDocument, TextBlock
from yomihime_game_link_sdk.results import CapabilityResult, ResultStatus
from yomihime_game_link_sdk.services import (
    CapabilityHealth,
    ConfigSnapshot,
    ConfigTarget,
    HealthReport,
    HealthStatus,
    ModuleHandlers,
)
from yomihime_game_link_sdk.version import MODULE_ABI_VERSION


class _Handler:
    def __init__(self, capability_id: str) -> None:
        self.capability_id = capability_id
        self.calls: list[object] = []

    async def invoke(self, context, parameters):
        self.calls.append((context, parameters))
        return validate_contract(
            CapabilityResult(
                f"health-{self.capability_id}",
                ResultStatus.SUCCESS,
                document=validate_contract(
                    DisplayDocument(
                        "Health fixture",
                        "health",
                        (validate_contract(TextBlock(self.capability_id)),),
                    )
                ),
            )
        )


class _Instance:
    def __init__(
        self, handlers: ModuleHandlers, capability_ids: tuple[str, ...]
    ) -> None:
        self._handlers = handlers
        self._capability_ids = capability_ids

    def handlers(self) -> ModuleHandlers:
        return self._handlers

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def check_health(self) -> HealthReport:
        return validate_contract(
            HealthReport(
                {
                    item: validate_contract(CapabilityHealth(HealthStatus.AVAILABLE))
                    for item in self._capability_ids
                }
            )
        )


def _descriptor(
    capability_id: str,
    *,
    required_capabilities: tuple[CapabilityReference, ...] = (),
    required_config: tuple[str, ...] = (),
    required_sources: tuple[str, ...] = (),
) -> CapabilityDescriptor:
    return validate_contract(
        CapabilityDescriptor(
            capability_id,
            {"type": "object", "properties": {}, "required": []},
            InvocationPolicy.NATURAL_LANGUAGE_ALLOWED,
            CapabilityEffect.READ_ONLY,
            required_capabilities=required_capabilities,
            required_config=required_config,
            required_sources=required_sources,
        )
    )


class HealthResolverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._new_runtime(self._config_snapshot)

    def _new_runtime(self, config_snapshot, source_health=None) -> None:
        self.registry = Registry()
        self.issuer = ContextIssuer()
        self.health = HealthResolver(
            config_snapshot=config_snapshot,
            source_health=source_health,
        )
        self.lifecycle = LifecycleController(
            self.registry,
            issuer=self.issuer,
            capability_health_query=self.health.query,
        )
        self.health.bind_runtime(self.lifecycle, self.lifecycle.admission)
        self.handlers: dict[tuple[str, str], _Handler] = {}

    async def _config_snapshot(self, module_id: str) -> ConfigSnapshot:
        return validate_contract(
            ConfigSnapshot(
                1,
                {"enabled": False, "limit": 0},
                target=validate_contract(ConfigTarget("health-test", module_id)),
            )
        )

    async def _register_and_activate(
        self,
        modules: tuple[ModuleManifest, ...],
    ) -> None:
        package = validate_contract(
            PackageManifest(
                "health",
                "1.0.0",
                MODULE_ABI_VERSION,
                modules,
                "Core health tests",
                "MIT",
                "offline health fixture",
            )
        )
        package_handlers: dict[str, ModuleHandlers] = {}
        for manifest in modules:
            capabilities = {}
            for capability in manifest.capabilities:
                handler = _Handler(capability.capability_id)
                self.handlers[(manifest.module_id, capability.capability_id)] = handler
                capabilities[capability.capability_id] = handler
            package_handlers[manifest.module_id] = validate_contract(
                ModuleHandlers(
                    capabilities=capabilities,
                    collectors={},
                    evaluators={},
                )
            )
        self.registry.register_package(package, package_handlers)

        # Dependencies are activated first so preparation observes the same
        # full graph that command admission will later check.
        ordered = sorted(
            modules,
            key=lambda item: (item.module_id == "alpha", item.module_id),
        )
        for manifest in ordered:
            module_id = f"health/{manifest.module_id}"
            module = self.registry.snapshot().module(module_id)
            instance = _Instance(
                module.handlers,
                tuple(capability.capability_id for capability in manifest.capabilities),
            )
            install_id = uuid4().hex
            adopted = self.lifecycle.adopt_candidate(
                "health", manifest, install_id, instance
            )
            self.lifecycle.install_dormant(
                "health", module_id, install_id, instance, adopted
            )
            operation_id = uuid4().hex
            identity, _ = await self.lifecycle.start_candidate(module_id, operation_id)
            await self.health.prepare(module_id, manifest)
            self.lifecycle.publish_committed_intent(
                module_id,
                operation_id,
                identity,
                True,
                self.registry.snapshot().revision,
            )

    async def _restart_module(self, manifest: ModuleManifest) -> None:
        module_id = f"health/{manifest.module_id}"
        operation_id = uuid4().hex
        identity, _ = await self.lifecycle.start_candidate(module_id, operation_id)
        await self.health.prepare(module_id, manifest)
        self.lifecycle.publish_committed_intent(
            module_id,
            operation_id,
            identity,
            True,
            self.registry.snapshot().revision,
        )

    def _manifest(self) -> tuple[ModuleManifest, ModuleManifest]:
        query = _descriptor(
            "query",
            required_capabilities=(
                validate_contract(CapabilityReference("health/beta", "read")),
            ),
        )
        status = _descriptor("status")
        configured = _descriptor("configured", required_config=("enabled", "limit"))
        source_backed = _descriptor("source_backed", required_sources=("catalog",))
        limit_only = _descriptor("limit_only", required_config=("limit",))
        alpha = validate_contract(
            ModuleManifest(
                "alpha",
                "alpha",
                ModuleCategory.GAME,
                "tests.core.test_health:factory",
                "1.0.0",
                (query, status, configured, source_backed, limit_only),
                commands=(
                    validate_contract(
                        CommandDescriptor("query", "query", {}, "dependent query")
                    ),
                    validate_contract(
                        CommandDescriptor("status", "status", {}, "independent status")
                    ),
                ),
                config_fields=(
                    validate_contract(ConfigField("enabled", default=True)),
                    validate_contract(ConfigField("limit", default=1)),
                ),
                sources=(
                    validate_contract(SourceDeclaration("catalog", "offline.test")),
                ),
            )
        )
        beta = validate_contract(
            ModuleManifest(
                "beta",
                "beta",
                ModuleCategory.GAME,
                "tests.core.test_health:factory",
                "1.0.0",
                (_descriptor("read"),),
            )
        )
        return alpha, beta

    def _view(self, capability_id: str):
        module = self.registry.snapshot().module("health/alpha")
        view = self.issuer.issue(
            origin=InvocationOrigin.COMMAND,
            module_id="health/alpha",
            module_epoch=module.epoch,
            registry_revision=self.registry.snapshot().revision,
            actor_id="actor",
            conversation_id="conversation",
            capability_id=capability_id,
        )
        self.lifecycle.admission.admit(view, capability_id)
        return view

    async def test_dependency_health_degrades_only_the_declaring_capability(self):
        alpha, beta = self._manifest()
        await self._register_and_activate((alpha, beta))

        query_view = self._view("query")
        status_view = self._view("status")
        await self.lifecycle.stop("health/beta")

        self.assertTrue(self.registry.snapshot().module("health/alpha").enabled)
        self.assertTrue(self.lifecycle.state("health/alpha").active)
        query_health, _ = self.health.current("health/alpha", "query")
        status_health, _ = self.health.current("health/alpha", "status")
        self.assertEqual(query_health.status, HealthStatus.UNAVAILABLE)
        self.assertEqual(query_health.reason, "dependency_unavailable")
        self.assertEqual(status_health.status, HealthStatus.AVAILABLE)

        gateway = Gateway(
            self.registry,
            self.issuer,
            admission=self.lifecycle.admission,
            lifecycle=self.lifecycle,
        )
        denied = await gateway.invoke_command(query_view, "query", {})
        self.assertEqual(denied.error.code.value, "module_unavailable")
        allowed = await gateway.invoke_command(status_view, "status", {})
        self.assertEqual(allowed.status, ResultStatus.SUCCESS)
        self.assertEqual(self.handlers[("alpha", "query")].calls, [])
        self.assertEqual(len(self.handlers[("alpha", "status")].calls), 1)

    async def test_false_zero_config_are_present_and_unprobed_source_is_unknown(self):
        alpha, beta = self._manifest()
        await self._register_and_activate((alpha, beta))

        configured, _ = self.health.current("health/alpha", "configured")
        source_backed, _ = self.health.current("health/alpha", "source_backed")
        self.assertEqual(configured.status, HealthStatus.AVAILABLE)
        self.assertEqual(source_backed.status, HealthStatus.UNKNOWN)
        self.assertEqual(source_backed.reason, "source_unknown")

    async def test_config_publication_is_field_scoped_and_rejects_stale_snapshot(self):
        alpha, beta = self._manifest()
        await self._register_and_activate((alpha, beta))
        configured_before = self.health.current("health/alpha", "configured")
        limit_before = self.health.current("health/alpha", "limit_only")
        status_before = self.health.current("health/alpha", "status")
        newer = validate_contract(
            ConfigSnapshot(
                2,
                {"enabled": True, "limit": 0},
                target=validate_contract(ConfigTarget("health-test", "health/alpha")),
            )
        )
        async with self.lifecycle.admission.mutation("test-config-publish"):
            self.health.publish_config(
                "health/alpha", newer, changed_fields=frozenset({"enabled"})
            )
        configured_after = self.health.current("health/alpha", "configured")
        limit_after = self.health.current("health/alpha", "limit_only")
        status_after = self.health.current("health/alpha", "status")
        self.assertEqual(configured_after[0].status, HealthStatus.AVAILABLE)
        self.assertGreater(configured_after[1], configured_before[1])
        self.assertEqual(limit_after[1], limit_before[1])
        self.assertEqual(status_after[1], status_before[1])

        stale = validate_contract(
            ConfigSnapshot(
                1,
                {"limit": 0},
                target=validate_contract(ConfigTarget("health-test", "health/alpha")),
            )
        )
        async with self.lifecycle.admission.mutation("test-stale-config-publish"):
            with self.assertRaises(ValueError):
                self.health.publish_config(
                    "health/alpha", stale, changed_fields=frozenset({"enabled"})
                )
        self.assertEqual(
            self.health.current("health/alpha", "configured"), configured_after
        )

    async def test_latest_source_refresh_wins_when_probes_finish_out_of_order(self):
        first_started = asyncio.Event()
        second_started = asyncio.Event()
        release_first = asyncio.Event()
        release_second = asyncio.Event()
        calls = 0

        async def source_provider(module_id, source_id):
            nonlocal calls
            calls += 1
            if calls == 1:
                return validate_contract(CapabilityHealth(HealthStatus.AVAILABLE))
            if calls == 2:
                first_started.set()
                await release_first.wait()
                return validate_contract(
                    CapabilityHealth(HealthStatus.UNAVAILABLE, "old_probe")
                )
            if calls == 3:
                second_started.set()
                await release_second.wait()
                return validate_contract(CapabilityHealth(HealthStatus.AVAILABLE))
            raise AssertionError("unexpected source probe")

        self._new_runtime(self._config_snapshot, source_provider)
        alpha, beta = self._manifest()
        await self._register_and_activate((alpha, beta))

        older = asyncio.create_task(
            self.health.refresh_source("health/alpha", "catalog")
        )
        await asyncio.wait_for(first_started.wait(), timeout=2)
        newer = asyncio.create_task(
            self.health.refresh_source("health/alpha", "catalog")
        )
        await asyncio.wait_for(second_started.wait(), timeout=2)
        release_second.set()
        self.assertTrue(await newer)
        available = self.health.current("health/alpha", "source_backed")
        self.assertEqual(available[0].status, HealthStatus.AVAILABLE)

        release_first.set()
        self.assertFalse(await older)
        self.assertEqual(
            self.health.current("health/alpha", "source_backed"), available
        )

    async def test_source_refresh_result_is_discarded_after_run_replacement(self):
        refresh_started = asyncio.Event()
        release_refresh = asyncio.Event()
        calls = 0

        async def source_provider(module_id, source_id):
            nonlocal calls
            calls += 1
            if calls == 1:
                return validate_contract(CapabilityHealth(HealthStatus.AVAILABLE))
            if calls == 2:
                refresh_started.set()
                await release_refresh.wait()
                return validate_contract(
                    CapabilityHealth(HealthStatus.UNAVAILABLE, "old_run_probe")
                )
            return validate_contract(CapabilityHealth(HealthStatus.AVAILABLE))

        self._new_runtime(self._config_snapshot, source_provider)
        alpha, beta = self._manifest()
        await self._register_and_activate((alpha, beta))
        stale_refresh = asyncio.create_task(
            self.health.refresh_source("health/alpha", "catalog")
        )
        await asyncio.wait_for(refresh_started.wait(), timeout=2)

        await self.lifecycle.stop("health/alpha")
        await self._restart_module(alpha)
        current = self.health.current("health/alpha", "source_backed")
        self.assertEqual(current[0].status, HealthStatus.AVAILABLE)

        release_refresh.set()
        self.assertFalse(await stale_refresh)
        self.assertEqual(self.health.current("health/alpha", "source_backed"), current)

    async def test_declared_dependency_cycle_fails_closed(self):
        alpha, beta = self._manifest()
        cycle_read = _descriptor(
            "read",
            required_capabilities=(
                validate_contract(CapabilityReference("health/alpha", "query")),
            ),
        )
        beta = replace(beta, capabilities=(cycle_read,))
        await self._register_and_activate((alpha, beta))

        self.assertTrue(self.lifecycle.state("health/alpha").active)
        self.assertTrue(self.lifecycle.state("health/beta").active)
        health, _ = self.health.current("health/alpha", "query")
        self.assertEqual(health.status, HealthStatus.UNAVAILABLE)
        self.assertEqual(health.reason, "dependency_unavailable")

    async def test_superseded_async_prepare_cannot_overwrite_new_run_snapshot(self):
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        calls = 0

        async def config_provider(module_id):
            nonlocal calls
            calls += 1
            if calls == 1:
                first_started.set()
                await release_first.wait()
                return validate_contract(
                    ConfigSnapshot(
                        1,
                        {"limit": 0},
                        target=validate_contract(
                            ConfigTarget("health-test", module_id)
                        ),
                    )
                )
            return validate_contract(
                ConfigSnapshot(
                    2,
                    {"enabled": False},
                    target=validate_contract(ConfigTarget("health-test", module_id)),
                )
            )

        self._new_runtime(config_provider)
        configured = _descriptor("configured", required_config=("enabled",))
        manifest = validate_contract(
            ModuleManifest(
                "alpha",
                "alpha",
                ModuleCategory.GAME,
                "tests.core.test_health:factory",
                "1.0.0",
                (configured,),
                config_fields=(
                    validate_contract(ConfigField("enabled", default=True)),
                ),
            )
        )
        package = validate_contract(
            PackageManifest(
                "health",
                "1.0.0",
                MODULE_ABI_VERSION,
                (manifest,),
                "Core health tests",
                "MIT",
                "offline health fixture",
            )
        )
        handlers = validate_contract(
            ModuleHandlers({"configured": _Handler("configured")}, {}, {})
        )
        self.registry.register_package(package, {"alpha": handlers})

        async def install_and_start():
            module_id = "health/alpha"
            installed = self.registry.snapshot().module(module_id)
            instance = _Instance(installed.handlers, ("configured",))
            install_id = uuid4().hex
            adopted = self.lifecycle.adopt_candidate(
                "health", manifest, install_id, instance
            )
            self.lifecycle.install_dormant(
                "health", module_id, install_id, instance, adopted
            )
            operation_id = uuid4().hex
            identity, _ = await self.lifecycle.start_candidate(module_id, operation_id)
            return module_id, install_id, operation_id, identity

        (
            module_id,
            install_id,
            first_operation,
            first_identity,
        ) = await install_and_start()
        old_prepare = asyncio.create_task(self.health.prepare(module_id, manifest))
        await asyncio.wait_for(first_started.wait(), timeout=2)

        # The host rollback path invalidates this unpublished run while its
        # external config read remains outstanding.
        self.assertTrue(
            await self.lifecycle.rollback_unpublished_candidate(
                module_id,
                first_operation,
                5.0,
                installation_operation_id=install_id,
            )
        )
        module_id, _, second_operation, second_identity = await install_and_start()
        await self.health.prepare(module_id, manifest)
        self.lifecycle.publish_committed_intent(
            module_id,
            second_operation,
            second_identity,
            True,
            self.registry.snapshot().revision,
        )
        current = self.health.current(module_id, "configured")
        self.assertEqual(current[0].status, HealthStatus.AVAILABLE)

        release_first.set()
        await old_prepare
        self.assertEqual(self.health.current(module_id, "configured"), current)


if __name__ == "__main__":
    unittest.main()
