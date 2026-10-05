from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from secrets import token_urlsafe
from types import SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.api.administration import AdminOperation
from ygl_test_subject.api.display import (
    DisplayDocument,
    DisplayLimits,
    DisplayOutput,
    Privacy,
    TextBlock,
)
from ygl_test_subject.api.manifests import (
    CapabilityDescriptor,
    CapabilityEffect,
    CommandDescriptor,
    ConfigField,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PrivacyFloor,
    SourceDeclaration,
)
from ygl_test_subject.api.results import CapabilityResult, ResultStatus
from ygl_test_subject.api.services import (
    CapabilityHealth,
    ConfigFieldUpdate,
    ConfigPatchMode,
    ConfigTarget,
    Grant,
    GrantStatus,
    HealthReport,
    HealthStatus,
    ModuleHandlers,
    PersistedConfigPatch,
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
from ygl_test_subject.extensions.discovery import discover_packages
from ygl_test_subject.extensions.loader import CandidateState, ExtensionCandidate
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.services.admin_authorization import _digest
from ygl_test_subject.services.core_runtime import (
    CoreRuntime,
    CoreRuntimeCleanupPending,
    HostIngress,
    TrustedSubscriptionGate,
    _is_safe_bundled_default,
    _matches_trusted_bundled_manifest,
)
from ygl_test_subject.services.extension_runtime import ExtensionCleanupPending
from ygl_test_subject.services.source_credentials import SourceCredentialPolicy

from tests.contracts.test_context_issuer import _PublicWebProofs


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


class _OwnerHandler(_Handler):
    def __init__(self) -> None:
        super().__init__()
        self.services = None
        self.owner_rows = None

    async def invoke(self, context, parameters):
        self.owner_rows = await self.services.subscriptions.list_current(context)
        return await super().invoke(context, parameters)


class _OwnerFactory(_Factory):
    async def create(self, services):
        self.handler.services = services
        return await super().create(services)


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
    def test_exact_trusted_bundle_match_covers_every_manifest_surface(self) -> None:
        from ygl_test_subject.extensions.discovery import discover_packages

        package_root = Path(__file__).resolve().parents[2] / "modules"
        packages = tuple(
            item
            for item in discover_packages(package_root)
            if item.package_id == "ff14"
        )
        self.assertEqual(len(packages), 1)
        self.assertTrue(packages[0].valid)
        expected = packages[0].manifest.modules[0]

        self.assertTrue(_matches_trusted_bundled_manifest(expected, expected))
        added_capability = CapabilityDescriptor(
            "unreviewed.operation",
            {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            InvocationPolicy.COMMAND_ONLY,
            CapabilityEffect.READ_ONLY,
        )

        def changed_status(**changes):
            # Preserve descriptor constraints while varying the original status surface.
            self.assertEqual(
                sum(cap.capability_id == "status" for cap in expected.capabilities), 1
            )
            return tuple(
                replace(cap, **changes) if cap.capability_id == "status" else cap
                for cap in expected.capabilities
            )

        cases = (
            replace(expected, capabilities=expected.capabilities + (added_capability,)),
            replace(
                expected,
                capabilities=changed_status(effect=CapabilityEffect.WRITE),
            ),
            replace(
                expected,
                capabilities=changed_status(privacy_floor=PrivacyFloor.PRIVATE),
            ),
            replace(
                expected,
                sources=(replace(expected.sources[0], host="changed.invalid"),)
                + expected.sources[1:],
            ),
            replace(
                expected,
                config_fields=(replace(expected.config_fields[0], required=True),)
                + expected.config_fields[1:],
            ),
            replace(
                expected,
                schedules=(
                    replace(
                        expected.schedules[0],
                        default_interval_seconds=(
                            expected.schedules[0].default_interval_seconds + 60
                        ),
                    ),
                ),
            ),
            replace(
                expected,
                subscriptions=(
                    replace(
                        expected.subscriptions[0],
                        notification_modes=(
                            *expected.subscriptions[0].notification_modes,
                            "digest",
                        ),
                    ),
                ),
            ),
        )
        for candidate in cases:
            with self.subTest(candidate=candidate):
                self.assertFalse(_matches_trusted_bundled_manifest(candidate, expected))

        raw_credential = replace(
            expected,
            sources=(
                replace(expected.sources[0], credential_ref="credential_unreviewed"),
                *expected.sources[1:],
            ),
        )
        self.assertFalse(
            _matches_trusted_bundled_manifest(raw_credential, raw_credential)
        )

    def test_trusted_defaults_allow_public_read_only_sources_without_credentials(
        self,
    ) -> None:
        capability = CapabilityDescriptor(
            "lookup",
            {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            InvocationPolicy.COMMAND_ONLY,
            CapabilityEffect.READ_ONLY,
            required_sources=("items",),
        )

        def manifest(sources: tuple[SourceDeclaration, ...]) -> ModuleManifest:
            return ModuleManifest(
                "items",
                "items",
                ModuleCategory.GAME,
                "module:Factory",
                "1.0.0",
                (capability,),
                commands=(
                    CommandDescriptor(
                        "lookup", "lookup", {"query": "query"}, "Look up item"
                    ),
                ),
                sources=sources,
            )

        self.assertTrue(
            _is_safe_bundled_default(
                manifest((SourceDeclaration("items", "example.invalid"),)),
                frozenset({"lookup"}),
            )
        )
        # A missing declaration remains a capability-level UNKNOWN readiness
        # fact; it does not make the public module itself ineligible to start.
        self.assertTrue(_is_safe_bundled_default(manifest(()), frozenset({"lookup"})))
        self.assertFalse(
            _is_safe_bundled_default(
                manifest(
                    (
                        SourceDeclaration(
                            "items",
                            "example.invalid",
                            credential_ref="credential_example",
                        ),
                    )
                ),
                frozenset({"lookup"}),
            )
        )

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
        source_credential_policies=(),
        pump_interval: float = 3600,
        utc_clock=None,
        module_host_config_snapshots=None,
        trusted_bundled_manifests=None,
        trusted_subscription_gates=None,
        public_web_validator=None,
        public_web_capabilities=frozenset(),
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
            source_credential_policies=source_credential_policies,
            factory_source=source,
            utc_clock=utc_clock or (lambda: datetime.now(UTC)),
            pump_interval=pump_interval,
            cleanup_timeout=0.2,
            module_host_config_snapshots=module_host_config_snapshots,
            trusted_bundled_manifests=trusted_bundled_manifests,
            trusted_subscription_gates=trusted_subscription_gates,
            public_web_validator=public_web_validator,
            public_web_capabilities=public_web_capabilities,
        )

    async def _public_web_runtime(self, *, detached=False, block_root=False):
        from ygl_test_subject.services.module_services import InvocationBindingError

        class WebHandler:
            services = None

            def __init__(inner):
                inner.views = []
                inner.entered, inner.release = asyncio.Event(), asyncio.Event()
                inner.late_rejected = False

            async def invoke(inner, view, parameters):
                inner.views.append(view)
                bound = await inner.services.scopes.bind(view)
                if block_root:
                    inner.entered.set()
                    while not inner.release.is_set():
                        try:
                            await inner.release.wait()
                        except asyncio.CancelledError:
                            pass
                if detached:

                    async def late():
                        raise AssertionError("sealed work ran")

                    async def work():
                        inner.entered.set()
                        while not inner.release.is_set():
                            try:
                                await inner.release.wait()
                            except asyncio.CancelledError:
                                pass
                        try:
                            bound.tasks.create_task(late(), name="late-after-close")
                        except InvocationBindingError:
                            inner.late_rejected = True

                    bound.tasks.create_task(work(), name="web-detached")
                    await inner.entered.wait()
                return CapabilityResult(
                    "web-result",
                    ResultStatus.SUCCESS,
                    document=DisplayDocument("Public", "Web", (TextBlock("ok"),)),
                )

        class WebFactory(_Factory):
            async def create(inner, services):
                inner.handler.services = services
                return await super().create(services)

        handler, proofs = WebHandler(), _PublicWebProofs()
        runtime = self._runtime(
            source=_FactorySource(WebFactory(handler)),
            public_web_validator=proofs,
            public_web_capabilities=frozenset({("private/mod", "private")}),
        )
        manifest = _private_manifest()
        manifest["contract_version"] = "1.4.0"
        manifest["modules"][0]["capabilities"][0].update(
            invocation_policy="command_and_public_web", privacy_floor="public"
        )
        package_root = self.root / "extensions/private"
        package_root.mkdir(parents=True)
        (package_root / "yomihime.manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        await runtime.start()
        await runtime.admin_credential_repository.bootstrap(_digest(token_urlsafe(32)))
        await runtime.admin_operations.set_enabled(
            None,
            "private/mod",
            True,
            expected_registry_revision=runtime.registry.snapshot().revision,
            authorization=_AdminContext(),
        )
        return runtime, handler, proofs

    async def test_public_web_real_composition_has_no_chat_principal_route_or_output(
        self,
    ):
        runtime, handler, proofs = await self._public_web_runtime()
        try:
            with (
                patch.object(
                    type(runtime.trusted_route_publisher),
                    "publish",
                    side_effect=AssertionError("chat route"),
                ),
                patch.object(
                    type(runtime.output),
                    "route",
                    side_effect=AssertionError("chat output"),
                ),
                patch.object(
                    type(runtime._ingress_principal_provisioner),
                    "ensure",
                    side_effect=AssertionError("chat principal"),
                ),
            ):
                result = await runtime.invoke_public_web(
                    "private/mod",
                    "private",
                    {},
                    proof=proofs.new("private/mod", "private"),
                )
            self.assertEqual(result.status, ResultStatus.SUCCESS)
            self.assertEqual(runtime.output._message_port.calls, [])
            self.assertIsNone(handler.views[0].actor_id)
            self.assertFalse(runtime.issuer._issued)
            self.assertFalse(proofs.active)
        finally:
            self.assertTrue(await runtime.close(timeout=0.5))

    async def test_public_web_close_fences_before_acquiring_shared_close_lock(self):
        runtime, handler, proofs = await self._public_web_runtime(block_root=True)
        request = asyncio.create_task(
            runtime.invoke_public_web(
                "private/mod", "private", {}, proof=proofs.new("private/mod", "private")
            )
        )
        await asyncio.wait_for(handler.entered.wait(), 1)
        self.assertTrue(proofs.active)
        await runtime._close_lock.acquire()
        closing = asyncio.create_task(runtime.close(timeout=0.5))
        try:
            await asyncio.sleep(0)
            self.assertTrue(runtime.gateway._web_closed)
            self.assertTrue(runtime.accepting)  # Preserve original chat lock ordering.
            self.assertFalse(proofs.active)
            self.assertFalse(runtime.issuer._issued)
            self.assertTrue(runtime.gateway.public_web_pending)
        finally:
            handler.release.set()
            runtime._close_lock.release()
            self.assertTrue(await closing)
            await request

    async def test_public_web_close_timeout_retains_owned_tasks_and_can_retry(self):
        runtime, handler, proofs = await self._public_web_runtime(detached=True)
        await runtime.invoke_public_web(
            "private/mod", "private", {}, proof=proofs.new("private/mod", "private")
        )
        try:
            with self.assertRaises(CoreRuntimeCleanupPending) as caught:
                await runtime.close(timeout=0.01)
            self.assertEqual(caught.exception.component, "public_web")
            self.assertTrue(runtime.cleanup_pending)
            self.assertFalse(runtime.closed)
            self.assertTrue(runtime.gateway.public_web_pending)
            self.assertEqual(runtime.database.executor.state, "OPEN")
        finally:
            handler.release.set()
            self.assertTrue(await runtime.close(timeout=0.5))
        self.assertFalse(runtime.gateway.public_web_pending)
        self.assertTrue(handler.late_rejected)

    async def test_public_gate_reason_priorities_and_live_readiness(self):
        from ygl_test_subject.core.ports import PersistedSubscriptionGateRead

        expected, gates, _ = self._gate_packages()
        runtime = self._runtime(
            source=_FactorySource(_Factory(_Handler())),
            trusted_bundled_manifests=expected,
            trusted_subscription_gates=gates,
        )
        with patch.object(
            runtime.b04_repositories.lifecycle,
            "read_subscription_gate_state",
            side_effect=AssertionError("read would initialize"),
        ) as read:
            self.assertEqual(
                (await runtime.subscription_gate_state("private/mod")).reason,
                "state_unknown",
            )
            read.assert_not_called()
            self.assertFalse(runtime.database.path.exists())
        await runtime.start()
        try:
            self.assertEqual(
                (await runtime.subscription_gate_state("unknown/mod")).reason,
                "unsupported",
            )
            cases = (
                (
                    PersistedSubscriptionGateRead(
                        True, None, None, "initialization_invalid"
                    ),
                    None,
                    False,
                    "initialization_invalid",
                ),
                (
                    PersistedSubscriptionGateRead(True, False, False, None),
                    False,
                    False,
                    "gate_disabled",
                ),
                (
                    PersistedSubscriptionGateRead(True, True, False, None),
                    True,
                    False,
                    "module_disabled",
                ),
                (
                    PersistedSubscriptionGateRead(True, True, True, None),
                    True,
                    True,
                    None,
                ),
            )
            for persisted, enabled, can_run, reason in cases:
                with (
                    self.subTest(reason=reason),
                    patch.object(
                        runtime.b04_repositories.lifecycle,
                        "read_subscription_gate_state",
                        return_value=persisted,
                    ),
                ):
                    result = await runtime.subscription_gate_state("private/mod")
                    self.assertEqual(
                        (result.enabled, result.can_run, result.reason),
                        (enabled, can_run, reason),
                    )
            with patch.object(
                runtime.b04_repositories.lifecycle,
                "read_subscription_gate_state",
                side_effect=RuntimeError("private detail"),
            ):
                result = await runtime.subscription_gate_state("private/mod")
                self.assertEqual(
                    (result.enabled, result.can_run, result.reason),
                    (None, None, "state_unknown"),
                )
            with (
                patch.object(runtime, "_accepting", False),
                patch.object(
                    runtime.b04_repositories.lifecycle,
                    "read_subscription_gate_state",
                    return_value=PersistedSubscriptionGateRead(True, True, True, None),
                ),
            ):
                self.assertEqual(
                    (await runtime.subscription_gate_state("private/mod")).reason,
                    "runtime_not_ready",
                )
        finally:
            await runtime.close(timeout=0.5)

    async def test_public_gate_read_uses_shared_mutation_and_rechecks_close(self):
        from ygl_test_subject.core.ports import PersistedSubscriptionGateRead

        expected, gates, _ = self._gate_packages()
        runtime = self._runtime(
            source=_FactorySource(_Factory(_Handler())),
            trusted_bundled_manifests=expected,
            trusted_subscription_gates=gates,
        )
        await runtime.start()
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocked(_):
            entered.set()
            await release.wait()
            return PersistedSubscriptionGateRead(True, True, True, None)

        try:
            with patch.object(
                runtime.b04_repositories.lifecycle,
                "read_subscription_gate_state",
                side_effect=blocked,
            ):
                task = asyncio.create_task(
                    runtime.subscription_gate_state("private/mod")
                )
                await entered.wait()
                mutation_entered = asyncio.Event()

                async def contender():
                    async with runtime.lifecycle.admission.mutation("test-contender"):
                        mutation_entered.set()

                contender_task = asyncio.create_task(contender())
                await asyncio.sleep(0)
                self.assertFalse(mutation_entered.is_set())
                runtime._closing = True
                release.set()
                self.assertEqual((await task).reason, "runtime_not_ready")
                await contender_task
                runtime._closing = False
        finally:
            release.set()
            await runtime.close(timeout=0.5)

    def _gate_packages(self):
        extension_root = self.root / "extensions"
        extension_root.mkdir(exist_ok=True)
        for package_id in ("private", "second"):
            package_root = extension_root / package_id
            package_root.mkdir()
            manifest = _private_manifest()
            manifest["package_id"] = package_id
            manifest["modules"][0]["route"] = package_id
            manifest["modules"][0]["config_fields"] = [
                {"name": "gate", "default": True}
            ]
            (package_root / "yomihime.manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            discovered = discover_packages(self.root / "extensions")
            self.assertTrue(all(item.valid for item in discovered), repr(discovered))
        packages = discover_packages(extension_root)
        self.assertEqual(len(packages), 2)
        expected = {
            f"{item.package_id}/mod": item.manifest.modules[0] for item in packages
        }
        gates = {
            module_id: TrustedSubscriptionGate(
                expected[module_id], "gate", item._provenance.manifest_sha256
            )
            for item in packages
            for module_id in (f"{item.package_id}/mod",)
        }
        candidates = tuple(
            ExtensionCandidate(item, CandidateState.DISABLED) for item in packages
        )
        return expected, gates, candidates

    def _gate_sql(self, runtime, sql):
        connection = runtime.database.connect()
        try:
            rows = [tuple(row) for row in connection.execute(sql).fetchall()]
            connection.commit()
            return rows
        finally:
            connection.close()

    async def test_complete_trusted_gate_map_consumes_one_batch_and_never_repairs_after_complete(
        self,
    ):
        expected, requested, _ = self._gate_packages()
        now = datetime(2026, 10, 2, 12, tzinfo=UTC)
        runtime = self._runtime(
            source=_FactorySource(_Factory(_Handler())),
            utc_clock=lambda: now,
            trusted_bundled_manifests=expected,
            trusted_subscription_gates=requested,
        )
        immutable = dict(requested)
        requested.clear()
        self.assertIs(runtime.config_repository, runtime.repositories.config)
        self.assertIs(
            runtime.admin_operations._config_repository, runtime.config_repository
        )
        with self.assertRaises(TypeError):
            runtime._trusted_subscription_gates["other/mod"] = immutable["private/mod"]
        initialize = runtime.config_repository.initialize_subscription_gates
        with patch.object(
            runtime.config_repository, "initialize_subscription_gates", wraps=initialize
        ) as called:
            await runtime.start()
            called.assert_awaited_once()
        self.assertEqual(
            self._gate_sql(
                runtime, "SELECT COUNT(*) FROM subscription_gate_initializations"
            ),
            [(2,)],
        )
        self.assertEqual(
            self._gate_sql(
                runtime,
                "SELECT value_json,subscription_transition_at FROM config_entries ORDER BY module_id",
            ),
            [("true", now.isoformat()), ("true", now.isoformat())],
        )
        for module_id in immutable:
            binding = runtime.b04_repositories.lifecycle._subscription_gate_bindings[
                module_id
            ]
            self.assertEqual(binding.target, ConfigTarget("host-config", module_id))
            self.assertEqual(
                (binding.package_id, binding.module_id), tuple(module_id.split("/", 1))
            )
            self.assertEqual(binding.field, immutable[module_id].field)
            self.assertIsNotNone(
                await runtime.b04_repositories.lifecycle.current_fence(module_id)
            )
            self.assertEqual(
                runtime.b04_repositories.scheduler._subscription_gate_bindings[
                    module_id
                ],
                binding,
            )
        with self.assertRaises(TypeError):
            runtime.b04_repositories.lifecycle._subscription_gate_bindings[
                "other/mod"
            ] = binding
        target = ConfigTarget("host-config", "private/mod")
        await runtime.config_repository.update(
            target,
            PersistedConfigPatch(
                1,
                (ConfigFieldUpdate("gate", ConfigPatchMode.REPLACE, value=False),),
                (ConfigField("gate", default=True),),
                "pause",
                target,
            ),
        )
        before = self._gate_sql(
            runtime, "SELECT * FROM config_entries ORDER BY module_id"
        )
        await runtime.close(timeout=0.5)
        reopened = self._runtime(
            source=_FactorySource(_Factory(_Handler())),
            trusted_bundled_manifests=expected,
            trusted_subscription_gates=immutable,
        )
        await reopened.start()
        self.assertEqual(
            self._gate_sql(reopened, "SELECT * FROM config_entries ORDER BY module_id"),
            before,
        )
        self._gate_sql(
            reopened, "DELETE FROM config_entries WHERE module_id='second/mod'"
        )
        await reopened.close(timeout=0.5)
        missing = self._runtime(
            source=_FactorySource(_Factory(_Handler())),
            trusted_bundled_manifests=expected,
            trusted_subscription_gates=immutable,
        )
        await missing.start()
        self.assertEqual(
            self._gate_sql(
                missing, "SELECT value_json FROM config_entries ORDER BY module_id"
            ),
            [("false",)],
        )
        self.assertEqual(
            self._gate_sql(
                missing, "SELECT COUNT(*) FROM subscription_gate_initializations"
            ),
            [(2,)],
        )
        await missing.close(timeout=0.5)

    async def test_any_untrusted_or_unmatched_candidate_keeps_entire_gate_bootstrap_pending(
        self,
    ):
        expected, gates, candidates = self._gate_packages()
        first = candidates[0]
        provenance = first.package._provenance
        variants = (
            candidates[:1],
            candidates + (first,),
            (
                replace(first, package=replace(first.package, _provenance=None)),
                candidates[1],
            ),
            (
                replace(
                    first,
                    package=replace(
                        first.package, _provenance=replace(provenance, _seal=None)
                    ),
                ),
                candidates[1],
            ),
            (
                replace(
                    first,
                    package=replace(
                        first.package,
                        _provenance=SimpleNamespace(
                            trusted=True,
                            package_id=first.package.package_id,
                            manifest_sha256=provenance.manifest_sha256,
                        ),
                    ),
                ),
                candidates[1],
            ),
            (
                replace(
                    first,
                    package=replace(
                        first.package,
                        _provenance=replace(provenance, manifest_sha256=b"x" * 32),
                    ),
                ),
                candidates[1],
            ),
            (
                replace(
                    first,
                    package=replace(
                        first.package,
                        _provenance=replace(provenance, package_id="outsider"),
                    ),
                ),
                candidates[1],
            ),
            (
                replace(
                    first,
                    package=replace(
                        first.package,
                        manifest=replace(
                            first.package.manifest,
                            modules=(
                                replace(
                                    first.package.manifest.modules[0], module_id="other"
                                ),
                            ),
                        ),
                    ),
                ),
                candidates[1],
            ),
            (
                replace(
                    first,
                    package=replace(
                        first.package,
                        manifest=replace(
                            first.package.manifest,
                            modules=(
                                replace(
                                    first.package.manifest.modules[0],
                                    config_fields=(ConfigField("gate", default=1),),
                                ),
                            ),
                        ),
                    ),
                ),
                candidates[1],
            ),
        )
        for index, mutated in enumerate(variants):
            with self.subTest(index=index):
                runtime = self._runtime(
                    source=_FactorySource(_Factory(_Handler())),
                    trusted_bundled_manifests=expected,
                    trusted_subscription_gates=gates,
                )
                with (
                    patch.object(
                        runtime.extension_runtime, "scan", return_value=mutated
                    ),
                    patch.object(
                        runtime.config_repository,
                        "initialize_subscription_gates",
                        side_effect=AssertionError("partial bootstrap"),
                    ) as initialize,
                ):
                    await runtime.start()
                    initialize.assert_not_called()
                self.assertEqual(
                    self._gate_sql(
                        runtime, "SELECT phase FROM subscription_gate_bootstrap"
                    ),
                    [("pending",)],
                )
                self.assertEqual(
                    self._gate_sql(
                        runtime, "SELECT * FROM subscription_gate_initializations"
                    ),
                    [],
                )
                self.assertEqual(
                    self._gate_sql(runtime, "SELECT * FROM config_entries"), []
                )
                await runtime.close(timeout=0.5)

    async def test_descriptor_equal_but_changed_manifest_bytes_do_not_initialize_gates(
        self,
    ):
        expected, gates, _ = self._gate_packages()
        path = self.root / "extensions/private/yomihime.manifest.json"
        path.write_text(path.read_text("utf-8") + "\n", encoding="utf-8")
        runtime = self._runtime(
            source=_FactorySource(_Factory(_Handler())),
            trusted_bundled_manifests=expected,
            trusted_subscription_gates=gates,
        )
        await runtime.start()
        self.assertEqual(
            self._gate_sql(runtime, "SELECT phase FROM subscription_gate_bootstrap"),
            [("pending",)],
        )
        self.assertEqual(self._gate_sql(runtime, "SELECT * FROM config_entries"), [])
        await runtime.close(timeout=0.5)

    async def test_empty_partial_and_invalid_gate_composition_cannot_consume_pending(
        self,
    ):
        expected, gates, _ = self._gate_packages()
        runtime = self._runtime(
            source=_FactorySource(_Factory(_Handler())),
            trusted_bundled_manifests=expected,
            trusted_subscription_gates={},
        )
        # No gate request still permits the existing manifest lifecycle contract.
        await runtime.start()
        self.assertEqual(
            self._gate_sql(runtime, "SELECT phase FROM subscription_gate_bootstrap"),
            [("pending",)],
        )
        await runtime.close(timeout=0.5)
        for invalid in (
            {"private/mod": gates["private/mod"]},
            {**gates, "other/mod": gates["private/mod"]},
            {**gates, "private/mod": replace(gates["private/mod"], field="unknown")},
            {
                **gates,
                "private/mod": replace(gates["private/mod"], manifest_sha256=b"short"),
            },
        ):
            with self.subTest(keys=tuple(invalid)), self.assertRaises(ValueError):
                self._runtime(
                    trusted_bundled_manifests=expected,
                    trusted_subscription_gates=invalid,
                )
        for declaration in (
            ConfigField("gate", default=1),
            ConfigField("gate", default=False),
            ConfigField("gate", sensitive=True),
        ):
            invalid_manifest = replace(
                expected["private/mod"], config_fields=(declaration,)
            )
            invalid_expected = {**expected, "private/mod": invalid_manifest}
            invalid_gates = {
                **gates,
                "private/mod": replace(gates["private/mod"], manifest=invalid_manifest),
            }
            with self.subTest(declaration=declaration), self.assertRaises(ValueError):
                self._runtime(
                    trusted_bundled_manifests=invalid_expected,
                    trusted_subscription_gates=invalid_gates,
                )

    async def test_host_config_mapping_is_copied_frozen_and_forwarded(self):
        requested = {"sample/alpha": {"host_only": {"days": [2, 3]}}}
        runtime = self._runtime(module_host_config_snapshots=requested)
        requested["sample/alpha"]["host_only"]["days"].append(4)
        requested["other/module"] = {}
        self.assertEqual(
            runtime.module_host_config_snapshots["sample/alpha"]["host_only"]["days"],
            (2, 3),
        )
        self.assertNotIn("other/module", runtime.module_host_config_snapshots)
        with self.assertRaises(TypeError):
            runtime.module_host_config_snapshots["sample/alpha"]["host_only"][
                "days"
            ] = ()
        self.assertEqual(
            runtime.module_services._module_host_config_snapshots,
            runtime.module_host_config_snapshots,
        )
        await runtime.start()
        self.assertTrue(await runtime.close())

    def test_host_config_mapping_rejects_invalid_module_or_values(self):
        for snapshots in (
            {"../module": {}},
            {"sample/alpha": []},
            {"sample/alpha": {"nan": float("nan")}},
        ):
            with self.subTest(snapshots_type=type(snapshots).__name__):
                with self.assertRaises((ValueError, TypeError)):
                    self._runtime(module_host_config_snapshots=snapshots)

    async def test_catalog_real_zero_one_two_modules_and_lifecycle(self):
        capability_ids = (
            "private",
            "public_read",
            "public_web",
            "public_write",
            "owner",
        )

        class Instance(_Instance):
            def handlers(self):
                return ModuleHandlers(
                    {key: self._handler for key in capability_ids}, {}, {}
                )

            async def check_health(self):
                return HealthReport(
                    {
                        key: CapabilityHealth(HealthStatus.AVAILABLE)
                        for key in capability_ids
                    }
                )

        class Factory(_Factory):
            async def create(self, services):
                self.assert_services = services
                instance = Instance(self.handler)
                self.instances.append(instance)
                return instance

        factory = Factory(_Handler())
        runtime = self._runtime(source=_FactorySource(factory))
        await runtime.start()
        await runtime.admin_credential_repository.bootstrap(_digest(token_urlsafe(32)))
        self.assertEqual(runtime.public_module_catalog()["modules"], [])
        self.assertEqual(runtime.public_module_catalog()["runtime"], {"state": "ready"})
        self.assertTrue(await runtime.close())
        self.assertEqual(
            runtime.public_module_catalog()["runtime"], {"state": "closed"}
        )

        for count, package_id in enumerate(("zeta", "alpha"), start=1):
            manifest = _private_manifest()
            manifest["package_id"] = package_id
            module = manifest["modules"][0]
            module["route"] = package_id
            base = module["capabilities"][0]
            module["capabilities"] = [
                {
                    **base,
                    "capability_id": key,
                    "privacy_floor": privacy,
                    "invocation_policy": policy,
                    "effect": effect,
                }
                for key, privacy, policy, effect in (
                    ("private", "private", "command_only", "read_only"),
                    ("public_read", "public", "command_only", "read_only"),
                    ("public_web", "public", "command_and_public_web", "read_only"),
                    ("public_write", "public", "command_only", "write"),
                    ("owner", "owner", "command_only", "read_only"),
                )
            ]
            module["config_fields"] = [
                {
                    "name": "ordinary",
                    "required": True,
                    "default": "SENTINEL_DEFAULT",
                    "description": "SENTINEL_DESCRIPTION",
                },
                {
                    "name": "SENTINEL_SECRET_FIELD",
                    "sensitive": True,
                    "description": "SENTINEL_SECRET_DESCRIPTION",
                },
            ]
            package_root = self.root / "extensions" / package_id
            package_root.mkdir()
            (package_root / "yomihime.manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            runtime = self._runtime(
                source=_FactorySource(factory),
                module_host_config_snapshots={
                    f"{package_id}/mod": {"host_only": "SENTINEL_CURRENT_VALUE"}
                },
            )
            try:
                await runtime.start()
                for known in ("zeta", "alpha")[:count]:
                    await runtime.admin_operations.set_enabled(
                        None,
                        f"{known}/mod",
                        True,
                        expected_registry_revision=runtime.registry.snapshot().revision,
                        authorization=_AdminContext(),
                    )
                catalog = runtime.public_module_catalog()
                self.assertEqual(len(catalog["modules"]), count)
                self.assertEqual(
                    [item["module_id"] for item in catalog["modules"]],
                    sorted(item["module_id"] for item in catalog["modules"]),
                )
                selected = next(
                    item
                    for item in catalog["modules"]
                    if item["module_id"] == f"{package_id}/mod"
                )
                self.assertEqual(selected["state"], "loaded")
                with (
                    patch.object(
                        type(runtime.repositories.config),
                        "current",
                        side_effect=AssertionError("catalog read configuration"),
                    ),
                    patch.object(
                        type(runtime.module_services),
                        "for_module",
                        side_effect=AssertionError("catalog called module services"),
                    ),
                ):
                    catalog = runtime.public_module_catalog()
                self.assertNotIn("SENTINEL", json.dumps(catalog))
                selected = next(
                    item
                    for item in catalog["modules"]
                    if item["module_id"] == f"{package_id}/mod"
                )
                self.assertEqual(selected["state"], "loaded")
                self.assertEqual(
                    selected["config_fields"], [{"name": "ordinary", "required": True}]
                )
                self.assertEqual(
                    selected["capabilities"],
                    [
                        {
                            "capability_id": "public_read",
                            "invocation_policy": "command_only",
                            "web_declared": False,
                        },
                        {
                            "capability_id": "public_web",
                            "invocation_policy": "command_and_public_web",
                            "web_declared": True,
                        },
                    ],
                )
                self.assertNotIn("can_invoke", json.dumps(catalog))
                with patch.object(runtime, "_closing", True):
                    closing = runtime.public_module_catalog()
                self.assertEqual(closing["runtime"], {"state": "closing"})
                self.assertTrue(
                    all(item["state"] == "unavailable" for item in closing["modules"])
                )
                state = runtime.lifecycle.state(f"{package_id}/mod")
                original_state = runtime.lifecycle.state
                for changes, reason in (
                    ({"cleanup_pending": True}, "cleanup_pending"),
                    ({"epoch": state.epoch + 1}, "identity_mismatch"),
                    ({"identity": replace(state.identity)}, "identity_mismatch"),
                ):
                    with patch.object(
                        runtime.lifecycle,
                        "state",
                        side_effect=lambda key, changes=changes: (
                            replace(state, **changes)
                            if key == state.module_id
                            else original_state(key)
                        ),
                    ):
                        observed = runtime.public_module_catalog()
                    changed = next(
                        item
                        for item in observed["modules"]
                        if item["module_id"] == state.module_id
                    )
                    self.assertEqual(
                        (changed["state"], changed["reason"]), ("unavailable", reason)
                    )
                with patch.object(
                    runtime.lifecycle,
                    "state",
                    side_effect=RuntimeError("SENTINEL_PRIVATE_REASON"),
                ):
                    with self.assertRaises(RuntimeError):
                        runtime.public_module_catalog()
                with patch.object(
                    runtime.lifecycle,
                    "state",
                    side_effect=lambda key: replace(
                        original_state(key), registry_revision=0
                    ),
                ):
                    with self.assertRaises(RuntimeError):
                        runtime.public_module_catalog()
                await runtime.admin_operations.set_enabled(
                    None,
                    state.module_id,
                    False,
                    expected_registry_revision=runtime.registry.snapshot().revision,
                    authorization=_AdminContext(),
                )
                self.assertEqual(
                    next(
                        item
                        for item in runtime.public_module_catalog()["modules"]
                        if item["module_id"] == state.module_id
                    )["state"],
                    "disabled",
                )
            finally:
                self.assertTrue(await runtime.close())
        self.assertEqual(runtime.output._message_port.calls, [])

    async def test_catalog_failures_are_not_empty_success(self):
        runtime = self._runtime()
        await runtime.start()
        try:
            with patch.object(
                runtime.registry, "snapshot", side_effect=RuntimeError("SENTINEL_ERROR")
            ):
                with self.assertRaises(RuntimeError):
                    runtime.public_module_catalog()
            from ygl_test_subject.core.registry import RegistrySnapshot
            from ygl_test_subject.services.module_catalog import (
                ModuleCatalogUnavailable,
                project_module_catalog,
            )

            snapshot = RegistrySnapshot(
                1, {f"sample/m{i}": None for i in range(65)}, {}, {}
            )
            with self.assertRaises(ModuleCatalogUnavailable):
                project_module_catalog(
                    snapshot,
                    dict.fromkeys(snapshot.modules),
                    dict.fromkeys(snapshot.modules),
                    runtime_state="ready",
                )
        finally:
            self.assertTrue(await runtime.close())

    async def test_zero_module_runtime_starts_and_closes(self) -> None:
        source = _FactorySource()
        runtime = self._runtime(source=source)
        report = await runtime.start()
        self.assertEqual(report.module_statuses, ())
        self.assertTrue(runtime.accepting)
        self.assertTrue(await runtime.close(timeout=0.5))
        self.assertTrue(runtime.closed)
        self.assertEqual(source.capture_calls, 0)

    async def test_source_credential_policies_are_passed_to_module_services(self):
        policies = (
            SourceCredentialPolicy(
                "ff14/ff14",
                "fflogs_public_global",
                "credential_fflogs_global",
                "www.fflogs.com",
                ("/api/v2/client",),
                "www.fflogs.com",
                "/oauth/token",
            ),
        )
        runtime = self._runtime(source_credential_policies=policies)
        self.assertEqual(runtime.module_services._source_credential_policies, policies)
        await runtime.start()
        self.assertTrue(await runtime.close(timeout=0.5))

    async def test_close_retires_module_credentials_before_sqlite_executor(self):
        runtime = self._runtime()
        events = []
        service_type = type(runtime.module_services)
        original_credentials_close = service_type.close_credentials

        async def close_credentials(factory):
            events.append("credentials")
            await original_credentials_close(factory)

        executor = runtime.database.executor
        executor_type = type(executor)
        original_executor_close = executor_type.close

        async def close_executor(worker, **kwargs):
            events.append("sqlite")
            await original_executor_close(worker, **kwargs)

        with (
            patch.object(service_type, "close_credentials", close_credentials),
            patch.object(executor_type, "close", close_executor),
        ):
            await runtime.start()
            self.assertTrue(await runtime.close(timeout=0.5))

        self.assertLess(events.index("credentials"), events.index("sqlite"))

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

    async def test_owner_command_uses_shared_proof_and_releases_it_after_output(self):
        handler = _OwnerHandler()
        runtime = self._runtime(source=_FactorySource(_OwnerFactory(handler)))
        manifest = _private_manifest()
        manifest["modules"][0]["capabilities"][0]["privacy_floor"] = "owner"
        package_root = self.root / "extensions" / "private"
        package_root.mkdir(parents=True, exist_ok=True)
        (package_root / "yomihime.manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        await runtime.start()
        await runtime.admin_credential_repository.bootstrap(_digest(token_urlsafe(32)))
        enabled = await runtime.admin_operations.set_enabled(
            None,
            "private/mod",
            True,
            expected_registry_revision=runtime.registry.snapshot().revision,
            authorization=_AdminContext(),
        )
        self.assertTrue(enabled.enabled)
        await runtime.repositories.identities.save_principal(
            Principal("principal-alice", "test-namespace", "adapter-one:alice")
        )
        ingress = HostIngress(
            "adapter-one",
            "adapter-one:alice",
            "conversation-one",
            "route-one",
            ConversationKind.DIRECT,
            object(),
        )

        outcome = await runtime.invoke_command(
            "private/mod", "read", {}, ingress=ingress
        )

        self.assertEqual(handler.grant, (None, None))
        self.assertEqual(handler.owner_rows, ())
        self.assertIs(outcome.result.privacy, Privacy.PRIVATE)
        self.assertEqual(outcome.output.status.value, "sent")
        self.assertEqual(len(runtime.output._message_port.calls), 1)
        self.assertEqual(
            runtime.output._message_port.calls[0][0].recipient_id,
            "adapter-one:alice",
        )
        self.assertEqual(runtime.owner_authority._proofs, {})
        self.assertTrue(await runtime.close(timeout=0.5))

    async def test_owner_command_trusted_ingress_provisions_principal_before_handler(
        self,
    ):
        handler = _OwnerHandler()
        runtime = self._runtime(source=_FactorySource(_OwnerFactory(handler)))
        manifest = _private_manifest()
        manifest["modules"][0]["capabilities"][0]["privacy_floor"] = "owner"
        package_root = self.root / "extensions" / "private"
        package_root.mkdir(parents=True, exist_ok=True)
        (package_root / "yomihime.manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        await runtime.start()
        await runtime.admin_credential_repository.bootstrap(_digest(token_urlsafe(32)))
        enabled = await runtime.admin_operations.set_enabled(
            None,
            "private/mod",
            True,
            expected_registry_revision=runtime.registry.snapshot().revision,
            authorization=_AdminContext(),
        )
        self.assertTrue(enabled.enabled)
        ingress = HostIngress(
            "adapter-one",
            "adapter-one:new-user",
            "conversation-new-user",
            "route-new-user",
            ConversationKind.DIRECT,
            object(),
        )

        outcome = await runtime.invoke_command(
            "private/mod", "read", {}, ingress=ingress
        )

        self.assertEqual(handler.owner_rows, ())
        self.assertIsNotNone(outcome.result)
        self.assertEqual(outcome.output.status.value, "sent")
        principal = await runtime.repositories.identities.find_principal(
            "test-namespace", "adapter-one:new-user"
        )
        self.assertIsNotNone(principal)
        self.assertEqual(len(runtime.output._message_port.calls), 1)
        self.assertEqual(runtime.owner_authority._proofs, {})
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
