from __future__ import annotations

import asyncio
from time import monotonic
from unittest import IsolatedAsyncioTestCase
from uuid import uuid4

from ygl_test_subject.api.contexts import InvocationOrigin
from ygl_test_subject.api.display import DisplayDocument, Privacy, TextBlock
from ygl_test_subject.api.manifests import (
    CapabilityDescriptor,
    CapabilityEffect,
    CapabilityReference,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
    PrivacyFloor,
)
from ygl_test_subject.api.results import CapabilityResult, ErrorCode, ResultStatus
from ygl_test_subject.api.services import (
    CallerCapability,
    CapabilityHealth,
    HealthReport,
    HealthStatus,
    ModuleHandlers,
)
from ygl_test_subject.api.version import CONTRACT_VERSION
from ygl_test_subject.core.context_issuer import ContextIssuer
from ygl_test_subject.core.lifecycle import LifecycleController
from ygl_test_subject.core.ports import CallerCapabilityIssuer
from ygl_test_subject.core.registry import Registry
from ygl_test_subject.services.dependency_calls import DependencyInvoker


def _result(label: str = "ok") -> CapabilityResult:
    return CapabilityResult(
        f"result-{label}",
        ResultStatus.SUCCESS,
        document=DisplayDocument(
            label, "subject", (TextBlock(label),), privacy=Privacy.PUBLIC
        ),
    )


def _capability(
    capability_id: str,
    *,
    required: tuple[str, ...] = (),
    policy: InvocationPolicy = InvocationPolicy.NATURAL_LANGUAGE_ALLOWED,
    effect: CapabilityEffect = CapabilityEffect.READ_ONLY,
    privacy: PrivacyFloor = PrivacyFloor.PUBLIC,
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_id,
        {"type": "object", "properties": {}, "required": []},
        policy,
        effect,
        privacy_floor=privacy,
        required_capabilities=required,
    )


def _module(module_id: str, capabilities: tuple[CapabilityDescriptor, ...]):
    return ModuleManifest(
        module_id,
        module_id,
        ModuleCategory.PLATFORM,
        "tests.module:Factory",
        "1.0.0",
        capabilities,
    )


def _package(*modules: ModuleManifest) -> PackageManifest:
    return PackageManifest(
        "dependency-tests",
        "1.0.0",
        CONTRACT_VERSION,
        modules,
        "Tests",
        "MIT",
        "offline",
    )


class _Handler:
    def __init__(self, result: CapabilityResult, *, started=None, release=None):
        self.result = result
        self.contexts = []
        self.started = started
        self.release = release

    async def invoke(self, context, parameters):
        self.contexts.append((context, parameters))
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            await self.release.wait()
        return self.result


class _Instance:
    def __init__(self, handlers, capability_ids):
        self._handlers = handlers
        self._capability_ids = capability_ids

    def handlers(self):
        return self._handlers

    async def start(self):
        return None

    async def stop(self):
        return None

    async def check_health(self):
        return HealthReport(
            {
                capability_id: CapabilityHealth(HealthStatus.AVAILABLE)
                for capability_id in self._capability_ids
            }
        )


class _CallerIssuer:
    def __init__(self):
        self._issued = {}

    def issue(self, invocation, capability_id):
        caller = CallerCapability(
            invocation.module_id,
            capability_id,
            invocation.registry_revision,
            invocation.module_epoch,
        )
        self._issued[(invocation.invocation_id, capability_id)] = caller
        return caller

    def require(self, invocation, caller):
        if not isinstance(caller, CallerCapability):
            raise PermissionError("caller is not recognized")
        issued = self._issued.get((invocation.invocation_id, caller.capability_id))
        if issued is not caller:
            raise PermissionError("caller is not recognized")
        if (
            caller.module_id != invocation.module_id
            or caller.registry_revision != invocation.registry_revision
            or caller.module_epoch != invocation.module_epoch
        ):
            raise PermissionError("caller is stale")
        return caller


class _FakeClock:
    def __init__(self, value: float = 0.0):
        self.value = value

    def __call__(self) -> float:
        return self.value


class DependencyInvokerTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.registry = Registry()
        self.clock = _FakeClock()
        self.issuer = ContextIssuer(clock=self.clock)
        self.lifecycle = LifecycleController(
            self.registry, issuer=self.issuer, clock=self.clock
        )
        self.caller_issuer: CallerCapabilityIssuer = _CallerIssuer()

    async def _activate_modules(self, module_ids):
        for short_id in module_ids:
            module_id = f"dependency-tests/{short_id}"
            module = self.registry.snapshot().module(module_id)
            instance = _Instance(
                module.handlers,
                tuple(item.capability_id for item in module.manifest.capabilities),
            )
            install_id = uuid4().hex
            handlers = self.lifecycle.adopt_candidate(
                "dependency-tests", module.manifest, install_id, instance
            )
            self.lifecycle.install_dormant(
                "dependency-tests", module_id, install_id, instance, handlers
            )
            operation_id = uuid4().hex
            identity, _ = await self.lifecycle.start_candidate(module_id, operation_id)
            self.lifecycle.publish_committed_intent(
                module_id,
                operation_id,
                identity,
                True,
                self.registry.snapshot().revision,
            )

    async def _restart_installed(self, module_id):
        operation_id = uuid4().hex
        identity, _ = await self.lifecycle.start_candidate(module_id, operation_id)
        self.lifecycle.publish_committed_intent(
            module_id,
            operation_id,
            identity,
            True,
            self.registry.snapshot().revision,
        )

    def _issue_parent(
        self,
        module_id: str,
        capability_id: str,
        *,
        origin=InvocationOrigin.COMMAND,
        deadline=None,
        grant_id=None,
        grant_revision=None,
    ):
        snapshot = self.registry.snapshot()
        module = snapshot.module(module_id)
        invocation = self.issuer.issue(
            origin=origin,
            module_id=module_id,
            module_epoch=module.epoch,
            registry_revision=snapshot.revision,
            actor_id="actor",
            conversation_id="conversation",
            capability_id=capability_id,
            deadline=deadline,
            grant_id=grant_id,
            grant_revision=grant_revision,
        )
        self.lifecycle.admission.admit(invocation, capability_id)
        return invocation

    async def _ready(
        self,
        source_caps,
        target_cap,
        target_handler=None,
        *,
        caller_id=None,
        deadline=None,
        grant_id=None,
        grant_revision=None,
    ):
        target_handler = target_handler or _Handler(_result("dependency"))
        source_handler = _Handler(_result("source"))
        source = _module("source", tuple(source_caps))
        target = _module("target", (target_cap,))
        self.registry.register_package(
            _package(source, target),
            {
                "source": ModuleHandlers(
                    {cap.capability_id: source_handler for cap in source_caps}, {}, {}
                ),
                "target": ModuleHandlers(
                    {target_cap.capability_id: target_handler}, {}, {}
                ),
            },
        )
        await self._activate_modules(("source", "target"))
        invocation = self._issue_parent(
            "dependency-tests/source",
            caller_id or source_caps[0].capability_id,
            deadline=deadline,
            grant_id=grant_id,
            grant_revision=grant_revision,
        )
        caller = self.caller_issuer.issue(
            invocation, caller_id or source_caps[0].capability_id
        )
        return invocation, target_handler, caller

    def _invoker(self, caller):
        return DependencyInvoker(
            self.registry,
            self.issuer,
            self.lifecycle,
            self.caller_issuer,
            caller,
            clock=self.clock,
        )

    async def test_declared_cross_module_call_drops_module_scoped_grant(self):
        invocation, handler, caller = await self._ready(
            (
                _capability(
                    "source.query",
                    required=(
                        CapabilityReference("dependency-tests/target", "target.query"),
                    ),
                ),
            ),
            _capability("target.query"),
            deadline=monotonic() + 60,
            grant_id="grant",
            grant_revision=1,
        )
        result = await self._invoker(caller).invoke(
            invocation,
            CapabilityReference("dependency-tests/target", "target.query"),
            {},
        )
        self.assertEqual(result.status, ResultStatus.SUCCESS)
        child = handler.contexts[0][0]
        self.assertEqual(child.parent_id, invocation.invocation_id)
        self.assertEqual(child.origin, invocation.origin)
        self.assertEqual(child.actor_id, invocation.actor_id)
        self.assertIsNone(child.grant_id)
        self.assertIsNone(child.grant_revision)
        self.assertEqual(child.deadline, invocation.deadline)
        self.assertEqual(child.registry_revision, invocation.registry_revision)

    async def test_child_capability_identity_binds_nested_dependencies(self):
        target_ref = CapabilityReference("dependency-tests/target", "target.query")
        nested_ref = CapabilityReference("dependency-tests/nested", "nested.query")
        source_capability = _capability("source.query", required=(target_ref,))
        target_capability = _capability("target.query", required=(nested_ref,))
        nested_handler = _Handler(_result("nested"))

        class _NestedTargetHandler:
            def __init__(self):
                self.capability_ids = []
                self.nested_results = []
                self.inherited_results = []

            async def invoke(self, context, parameters):
                self.capability_ids.append(context.capability_id)
                target_caller = self_outer.caller_issuer.issue(
                    context, context.capability_id
                )
                nested_invoker = self_outer._invoker(target_caller)
                self.nested_results.append(
                    await nested_invoker.invoke(context, nested_ref, {})
                )
                inherited_source_caller = self_outer.caller_issuer.issue(
                    context, "source.query"
                )
                inherited_invoker = self_outer._invoker(inherited_source_caller)
                self.inherited_results.append(
                    await inherited_invoker.invoke(context, nested_ref, {})
                )
                return _result("target")

        self_outer = self
        target_handler = _NestedTargetHandler()
        source = _module("source", (source_capability,))
        target = _module("target", (target_capability,))
        nested = _module("nested", (_capability("nested.query"),))
        self.registry.register_package(
            _package(source, target, nested),
            {
                "source": ModuleHandlers(
                    {"source.query": _Handler(_result("source"))}, {}, {}
                ),
                "target": ModuleHandlers({"target.query": target_handler}, {}, {}),
                "nested": ModuleHandlers({"nested.query": nested_handler}, {}, {}),
            },
        )
        await self._activate_modules(("source", "target", "nested"))
        invocation = self._issue_parent("dependency-tests/source", "source.query")
        source_caller = self.caller_issuer.issue(invocation, "source.query")

        result = await self._invoker(source_caller).invoke(invocation, target_ref, {})

        self.assertEqual(result.status, ResultStatus.SUCCESS)
        self.assertEqual(target_handler.capability_ids, ["target.query"])
        self.assertNotEqual(
            target_handler.capability_ids[0], source_caller.capability_id
        )
        self.assertEqual(target_handler.nested_results[0].status, ResultStatus.SUCCESS)
        self.assertEqual(len(nested_handler.contexts), 1)
        rejected = target_handler.inherited_results[0]
        self.assertEqual(rejected.error.code, ErrorCode.UNSUPPORTED)
        self.assertEqual(len(nested_handler.contexts), 1)

    async def test_qualified_reference_selects_the_declared_same_named_target(self):
        source_cap = _capability(
            "source.query",
            required=(CapabilityReference("dependency-tests/other", "target.query"),),
        )
        source = _module("source", (source_cap,))
        first_handler = _Handler(_result("first-target"))
        other_handler = _Handler(_result("other-target"))
        first = _module("first", (_capability("target.query"),))
        other = _module("other", (_capability("target.query"),))
        self.registry.register_package(
            _package(source, first, other),
            {
                "source": ModuleHandlers(
                    {"source.query": _Handler(_result("source"))}, {}, {}
                ),
                "first": ModuleHandlers({"target.query": first_handler}, {}, {}),
                "other": ModuleHandlers({"target.query": other_handler}, {}, {}),
            },
        )
        await self._activate_modules(("source", "first", "other"))
        invocation = self._issue_parent("dependency-tests/source", "source.query")
        caller = self.caller_issuer.issue(invocation, "source.query")
        result = await self._invoker(caller).invoke(
            invocation,
            CapabilityReference("dependency-tests/other", "target.query"),
            {},
        )
        self.assertEqual(result.document.title, "other-target")
        self.assertEqual(first_handler.contexts, [])
        self.assertEqual(len(other_handler.contexts), 1)

    async def test_undeclared_ambiguous_and_command_only_are_rejected(self):
        invocation, handler, _caller = await self._ready(
            (
                _capability(
                    "source.query",
                    required=(
                        CapabilityReference("dependency-tests/target", "target.query"),
                    ),
                ),
            ),
            _capability("target.query", policy=InvocationPolicy.COMMAND_ONLY),
        )
        tool_caller = None
        tool_invocation = self._issue_parent(
            invocation.module_id, "source.query", origin=InvocationOrigin.LLM_TOOL
        )
        tool_caller = self.caller_issuer.issue(tool_invocation, "source.query")
        result = await self._invoker(tool_caller).invoke(
            tool_invocation,
            CapabilityReference("dependency-tests/target", "target.query"),
            {},
        )
        self.assertEqual(result.error.code, ErrorCode.MODULE_UNAVAILABLE)
        self.assertEqual(handler.contexts, [])
        result = await self._invoker(_caller).invoke(
            invocation, CapabilityReference("dependency-tests/target", "missing"), {}
        )
        self.assertEqual(result.error.code, ErrorCode.UNSUPPORTED)
        result = await self._invoker(_caller).invoke(
            invocation,
            CapabilityReference("dependency-tests/target", "target.query"),
            {"unexpected": True},
        )
        self.assertEqual(result.error.code, ErrorCode.PARAMETER_ERROR)

    async def test_bound_caller_selects_only_its_declared_dependencies(self):
        target_ref = CapabilityReference("dependency-tests/target", "target.query")
        invocation, handler, caller = await self._ready(
            (
                _capability("source.query", required=(target_ref,)),
                _capability("source.other"),
            ),
            _capability("target.query"),
            caller_id="source.other",
        )
        result = await self._invoker(caller).invoke(invocation, target_ref, {})
        self.assertEqual(result.error.code, ErrorCode.UNSUPPORTED)
        self.assertEqual(handler.contexts, [])

        forged = CallerCapability(
            invocation.module_id,
            "source.query",
            invocation.registry_revision,
            invocation.module_epoch,
        )
        result = await self._invoker(forged).invoke(invocation, target_ref, {})
        self.assertEqual(result.error.code, ErrorCode.MODULE_UNAVAILABLE)

        declared_caller = self.caller_issuer.issue(invocation, "source.query")
        result = await self._invoker(declared_caller).invoke(invocation, target_ref, {})
        self.assertEqual(result.status, ResultStatus.SUCCESS)

    async def test_parent_release_rejects_late_result(self):
        started = asyncio.Event()
        release = asyncio.Event()
        invocation, handler, caller = await self._ready(
            (
                _capability(
                    "source.query",
                    required=(
                        CapabilityReference("dependency-tests/target", "target.query"),
                    ),
                ),
            ),
            _capability("target.query"),
            _Handler(_result("late"), started=started, release=release),
        )
        invoker = self._invoker(caller)
        pending = asyncio.create_task(
            invoker.invoke(
                invocation,
                CapabilityReference("dependency-tests/target", "target.query"),
                {},
            )
        )
        await started.wait()
        self.issuer.release(invocation)
        release.set()
        result = await pending
        self.assertEqual(result.status, ResultStatus.ERROR)
        self.assertEqual(result.error.code, ErrorCode.MODULE_UNAVAILABLE)

    async def test_target_cancellation_propagates_and_new_epoch_recovers(self):
        started = asyncio.Event()
        release = asyncio.Event()
        invocation, handler, caller = await self._ready(
            (
                _capability(
                    "source.query",
                    required=(
                        CapabilityReference("dependency-tests/target", "target.query"),
                    ),
                ),
            ),
            _capability("target.query"),
            _Handler(_result("cancelled"), started=started, release=release),
        )
        invoker = self._invoker(caller)
        pending = asyncio.create_task(
            invoker.invoke(
                invocation,
                CapabilityReference("dependency-tests/target", "target.query"),
                {},
            )
        )
        await started.wait()
        stopping = asyncio.create_task(self.lifecycle.stop("dependency-tests/target"))
        with self.assertRaises(asyncio.CancelledError):
            await pending
        release.set()
        await stopping

        # Re-enable both modules to publish a new common Registry revision and
        # fresh epochs; the old invocation remains stale.
        await self.lifecycle.stop("dependency-tests/source")
        await self._restart_installed("dependency-tests/source")
        await self._restart_installed("dependency-tests/target")
        fresh = self._issue_parent("dependency-tests/source", "source.query")
        fresh_caller = self.caller_issuer.issue(fresh, "source.query")
        self.assertEqual(
            (
                await self._invoker(fresh_caller).invoke(
                    fresh,
                    CapabilityReference("dependency-tests/target", "target.query"),
                    {},
                )
            ).status,
            ResultStatus.SUCCESS,
        )

    async def test_deadline_exhausted_before_task_creation(self):
        target_ref = CapabilityReference("dependency-tests/target", "target.query")
        started = asyncio.Event()
        invocation, handler, caller = await self._ready(
            (_capability("source.query", required=(target_ref,)),),
            _capability("target.query"),
            _Handler(_result("expired"), started=started),
            deadline=5.0,
        )
        self.clock.value = 5.0

        result = await self._invoker(caller).invoke(invocation, target_ref, {})

        self.assertEqual(result.error.code, ErrorCode.MODULE_UNAVAILABLE)
        self.assertEqual(handler.contexts, [])
        self.assertFalse(started.is_set())
        self.assertEqual(self.lifecycle.scope(target_ref.module_id).tasks, ())

    async def test_deadline_expires_after_task_creation_and_cleans_up(self):
        target_ref = CapabilityReference("dependency-tests/target", "target.query")
        started = asyncio.Event()
        invocation, handler, caller = await self._ready(
            (_capability("source.query", required=(target_ref,)),),
            _capability("target.query"),
            _Handler(_result("expired"), started=started),
            deadline=5.0,
        )
        scope = self.lifecycle.scope(target_ref.module_id)
        create_task = scope.create_task

        def create_then_expire(work, *, name):
            task = create_task(work, name=name)
            self.clock.value = 5.0
            return task

        scope.create_task = create_then_expire
        result = await self._invoker(caller).invoke(invocation, target_ref, {})

        self.assertEqual(result.error.code, ErrorCode.MODULE_UNAVAILABLE)
        self.assertEqual(handler.contexts, [])
        self.assertFalse(started.is_set())
        self.assertEqual(scope.tasks, ())

    async def test_cycle_and_depth_are_rejected(self):
        first_handler = _Handler(_result("first"))
        holder = {}

        class _Nested:
            async def invoke(self, context, parameters):
                nested_caller = self_outer.caller_issuer.issue(context, "second.call")
                nested = DependencyInvoker(
                    self_outer.registry,
                    self_outer.issuer,
                    self_outer.lifecycle,
                    self_outer.caller_issuer,
                    nested_caller,
                    max_depth=holder["max_depth"],
                )
                return await nested.invoke(
                    context,
                    CapabilityReference("dependency-tests/first", "first.call"),
                    parameters,
                )

        self_outer = self

        first = _module(
            "first",
            (
                _capability(
                    "first.call",
                    required=(
                        CapabilityReference("dependency-tests/second", "second.call"),
                    ),
                ),
            ),
        )
        second = _module(
            "second",
            (
                _capability(
                    "second.call",
                    required=(
                        CapabilityReference("dependency-tests/first", "first.call"),
                    ),
                ),
            ),
        )
        self.registry.register_package(
            _package(first, second),
            {
                "first": ModuleHandlers({"first.call": first_handler}, {}, {}),
                "second": ModuleHandlers({"second.call": _Nested()}, {}, {}),
            },
        )
        await self._activate_modules(("first", "second"))
        root = self._issue_parent("dependency-tests/first", "first.call")
        root_caller = self.caller_issuer.issue(root, "first.call")
        invoker = self._invoker(root_caller)
        holder["max_depth"] = 8
        result = await invoker.invoke(
            root,
            CapabilityReference("dependency-tests/second", "second.call"),
            {},
        )
        self.assertEqual(result.status, ResultStatus.ERROR)
        self.assertEqual(result.error.code, ErrorCode.UNSUPPORTED)

        depth_limited = DependencyInvoker(
            self.registry,
            self.issuer,
            self.lifecycle,
            self.caller_issuer,
            root_caller,
            max_depth=1,
        )
        holder["max_depth"] = 1
        result = await depth_limited.invoke(
            root,
            CapabilityReference("dependency-tests/second", "second.call"),
            {},
        )
        self.assertEqual(result.status, ResultStatus.ERROR)
        self.assertEqual(result.error.code, ErrorCode.UNSUPPORTED)

    async def test_legacy_string_dependency_stays_within_one_module(self):
        source = _module(
            "source",
            (
                _capability("source.query", required=("local.lookup",)),
                _capability("local.lookup"),
            ),
        )
        handler = _Handler(_result("local"))
        self.registry.register_package(
            _package(source),
            {
                "source": ModuleHandlers(
                    {
                        "source.query": _Handler(_result("source")),
                        "local.lookup": handler,
                    },
                    {},
                    {},
                )
            },
        )
        await self._activate_modules(("source",))
        invocation = self._issue_parent("dependency-tests/source", "source.query")
        caller = self.caller_issuer.issue(invocation, "source.query")
        result = await self._invoker(caller).invoke(invocation, "local.lookup", {})
        self.assertEqual(result.status, ResultStatus.SUCCESS)
