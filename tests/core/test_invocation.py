from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from uuid import uuid4

from ygl_test_subject.api.contexts import InvocationOrigin
from ygl_test_subject.api.display import (
    DisplayDocument,
    FieldsBlock,
    Privacy,
    TextBlock,
)
from ygl_test_subject.api.manifests import (
    CapabilityDescriptor,
    CapabilityEffect,
    CommandDescriptor,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
    PrivacyFloor,
    ToolDescriptor,
)
from ygl_test_subject.api.results import (
    CapabilityResult,
    ErrorCode,
    ErrorDetail,
    FactDocument,
    ResultStatus,
)
from ygl_test_subject.api.services import (
    CapabilityHealth,
    HealthReport,
    HealthStatus,
    ModuleHandlers,
)
from ygl_test_subject.api.version import CONTRACT_VERSION
from ygl_test_subject.core.context_issuer import ContextIssuer
from ygl_test_subject.core.health import HealthResolver
from ygl_test_subject.core.invocation import Gateway
from ygl_test_subject.core.lifecycle import LifecycleController
from ygl_test_subject.core.registry import Registry

from tests.contracts.test_context_issuer import _PublicWebProofs


class PublicWebGatewayTests(IsolatedAsyncioTestCase):
    async def _ready_web(self, handler=None, capability=None, *, deployment=True):
        fixture = GatewayTests()
        fixture.setUp()
        proofs = _PublicWebProofs()
        fixture.issuer = ContextIssuer(
            public_web_validator=proofs,
            public_web_capabilities=frozenset({("testpkg/records", "record.query")})
            if deployment
            else frozenset(),
        )
        handler, command_view = await fixture._ready(
            capability or _capability(policy=InvocationPolicy.COMMAND_AND_PUBLIC_WEB),
            handler,
            tool=False,
        )
        fixture.issuer.release(command_view)
        gateway = fixture._gateway()
        self.fixture, self.proofs, self.gateway = fixture, proofs, gateway
        return handler

    async def _invoke(self, *, key="bearer-one", proof=None):
        proof = (
            proof
            if proof is not None
            else self.proofs.new("testpkg/records", "record.query", key=key)
        )
        return await self.gateway.invoke_public_web(
            proof, "testpkg/records", "record.query", {"record_id": "one"}
        )

    async def test_web_success_exact_proof_and_parameter_privacy_boundaries(self):
        handler = await self._ready_web()
        result = await self._invoke()
        self.assertEqual(result.status, ResultStatus.SUCCESS)
        view = handler.calls[0][0]
        self.assertEqual(view.origin, InvocationOrigin.WEB_PUBLIC)
        self.assertIsNone(view.actor_id)
        self.assertIsNone(view.conversation_id)
        self.assertIsNone(view.adapter_id)
        self.assertFalse(self.proofs.active)
        self.assertFalse(self.gateway.public_web_pending)
        self.assertEqual(
            (await self._invoke(proof=object())).error.code,
            ErrorCode.MODULE_UNAVAILABLE,
        )
        proof = self.proofs.new("testpkg/records", "record.query")
        bad = await self.gateway.invoke_public_web(
            proof, "testpkg/records", "record.query", {"id": "one"}
        )
        self.assertEqual(bad.error.code, ErrorCode.PARAMETER_ERROR)
        self.assertEqual(len(handler.calls), 1)
        handler.result = _result(privacy=Privacy.PRIVATE)
        self.assertEqual((await self._invoke()).status, ResultStatus.ERROR)

    async def test_all_data_statuses_return_valid_documents_while_current(self):
        for status in (
            ResultStatus.SUCCESS,
            ResultStatus.PARTIAL_SUCCESS,
            ResultStatus.NEEDS_SELECTION,
        ):
            with self.subTest(status=status):
                await self._ready_web(_Handler(replace(_result(), status=status)))
                result = await self._invoke()
                self.assertIs(result.status, status)
                self.assertIsNotNone(result.document)
                self.assertIsNone(result.error)
                self.assertFalse(self.proofs.active)
                self.assertFalse(self.gateway.public_web_pending)

    async def test_all_data_statuses_are_fenced_in_every_publication_window(self):
        for status in (
            ResultStatus.SUCCESS,
            ResultStatus.PARTIAL_SUCCESS,
            ResultStatus.NEEDS_SELECTION,
        ):
            for mode in ("fake_deadline", "real_deadline", "close_fence"):
                with self.subTest(status=status, mode=mode):
                    await self._ready_web(_Handler(replace(_result(), status=status)))
                    if mode == "fake_deadline":
                        tick = [1000.0]
                        self.gateway._clock = lambda: tick[0]
                        self.fixture.issuer._clock = lambda: tick[0]
                        revoke = self.proofs.revoke

                        def release_and_expire(proof):
                            revoke(proof)
                            tick[0] = 1031.0

                        self.proofs.revoke = release_and_expire
                        result = await self._invoke()
                    else:
                        create = asyncio.create_task
                        if mode == "real_deadline":
                            self.gateway._handler_timeout = 0.02

                        def create_with_publication_callback(
                            work, *, name=None, **kwargs
                        ):
                            task = create(work, name=name, **kwargs)
                            if name == "gateway:public_web":
                                if mode == "real_deadline":
                                    task.add_done_callback(lambda _: time.sleep(0.04))
                                else:
                                    task.add_done_callback(
                                        lambda _: self.gateway.fence_public_web()
                                    )
                            return task

                        with patch(
                            "asyncio.create_task", create_with_publication_callback
                        ):
                            result = await self._invoke()
                    self.assertIs(result.status, ResultStatus.ERROR)
                    self.assertEqual(result.error.code, ErrorCode.MODULE_UNAVAILABLE)
                    self.assertIsNone(result.document)
                    self.assertIsNone(result.model_facts)
                    self.assertFalse(self.proofs.active)
                    self.assertTrue(await self.gateway.drain_public_web(0.5))

    async def test_no_map_and_non_opted_in_private_owner_write_are_rejected(self):
        for options in (
            {"policy": InvocationPolicy.COMMAND_ONLY},
            {"policy": InvocationPolicy.NATURAL_LANGUAGE_ALLOWED},
            {"privacy": PrivacyFloor.PRIVATE, "policy": InvocationPolicy.COMMAND_ONLY},
            {"privacy": PrivacyFloor.OWNER, "policy": InvocationPolicy.COMMAND_ONLY},
            {"effect": CapabilityEffect.WRITE},
        ):
            with self.subTest(options=options):
                handler = await self._ready_web(capability=_capability(**options))
                self.assertEqual(
                    (await self._invoke()).error.code, ErrorCode.UNSUPPORTED
                )
                self.assertEqual(handler.calls, [])
        handler = await self._ready_web(deployment=False)
        self.assertEqual((await self._invoke()).error.code, ErrorCode.UNSUPPORTED)
        self.assertEqual(handler.calls, [])

    async def test_root_creation_and_pre_yield_expiry_release_authority(self):
        await self._ready_web()
        proof = self.proofs.new("testpkg/records", "record.query")
        with patch("asyncio.create_task", side_effect=RuntimeError("synthetic")):
            self.assertEqual(
                (await self._invoke(proof=proof)).error.code,
                ErrorCode.MODULE_UNAVAILABLE,
            )
        self.assertIn(proof, self.proofs.revoked)
        self.assertFalse(self.gateway.public_web_pending)

        check = self.proofs.is_current
        checks = 0

        def expired_before_register(proof, binding):
            nonlocal checks
            checks += 1
            return check(proof, binding) if checks < 3 else False

        proof = self.proofs.new("testpkg/records", "record.query")
        with patch.object(self.proofs, "is_current", expired_before_register):
            self.assertEqual(
                (await self._invoke(proof=proof)).error.code,
                ErrorCode.MODULE_UNAVAILABLE,
            )
        self.assertIn(proof, self.proofs.revoked)
        self.assertTrue(await self.gateway.drain_public_web(0.5))
        self.assertFalse(self.fixture.issuer._issued)

    async def test_publication_rechecks_fake_deadline_after_release_hook(self):
        await self._ready_web()
        tick = [1000.0]
        self.gateway._clock = lambda: tick[0]
        self.fixture.issuer._clock = lambda: tick[0]
        revoke = self.proofs.revoke

        def release_and_expire(proof):
            revoke(proof)
            tick[0] = 1031.0

        self.proofs.revoke = release_and_expire
        self.assertEqual(
            (await self._invoke()).error.code, ErrorCode.MODULE_UNAVAILABLE
        )
        self.assertFalse(self.proofs.active)
        self.assertTrue(await self.gateway.drain_public_web(0.5))

    async def test_publication_rechecks_real_deadline_after_root_callback(self):
        await self._ready_web()
        self.gateway._handler_timeout = 0.02
        create = asyncio.create_task

        def delay_root_completion(work, *, name=None, **kwargs):
            task = create(work, name=name, **kwargs)
            if name == "gateway:public_web":
                task.add_done_callback(lambda _: time.sleep(0.04))
            return task

        with patch("asyncio.create_task", delay_root_completion):
            result = await self._invoke()
        self.assertEqual(result.error.code, ErrorCode.MODULE_UNAVAILABLE)
        self.assertFalse(self.proofs.active)
        self.assertTrue(await self.gateway.drain_public_web(0.5))

    async def test_unpublished_completed_flight_is_owned_and_close_revokes_it(self):
        await self._ready_web()
        proof = self.proofs.new("testpkg/records", "record.query")
        create = asyncio.create_task
        observations = []

        def close_before_publication(task):
            flight = next(iter(self.gateway._web_flights.values()))
            observations.append(
                (
                    task.done(),
                    flight.work_finished,
                    not flight.sealed,
                    proof in self.proofs.active,
                )
            )
            self.gateway.fence_public_web()
            observations.append((flight.sealed, proof not in self.proofs.active))

        def root_callback_after_flight_callback(work, *, name=None, **kwargs):
            task = create(work, name=name, **kwargs)
            if name == "gateway:public_web":
                # Flight registers synchronously; this appends after its _done,
                # so the completed-but-unpublished ownership window is tested.
                asyncio.get_running_loop().call_soon(
                    task.add_done_callback, close_before_publication
                )
            return task

        with patch("asyncio.create_task", root_callback_after_flight_callback):
            result = await self._invoke(proof=proof)
        self.assertEqual(observations, [(True, True, True, True), (True, True)])
        self.assertEqual(result.error.code, ErrorCode.MODULE_UNAVAILABLE)
        self.assertEqual(self.gateway._web_generation, 2)
        self.assertTrue(await self.gateway.drain_public_web(0.5))

    async def test_publication_rechecks_proof_admission_generation_epoch_and_policy(
        self,
    ):
        for mode in ("proof", "admission", "generation", "epoch", "policy"):
            with self.subTest(mode=mode):
                handler = await self._ready_web()
                proof = self.proofs.new("testpkg/records", "record.query")
                create = asyncio.create_task

                def invalidate_before_publication(_task):
                    view = handler.calls[0][0]
                    if mode == "proof":
                        self.proofs.revoke(proof)
                    elif mode == "admission":
                        self.fixture.lifecycle.admission.release(
                            self.fixture.issuer.lease_for(view)
                        )
                    elif mode == "generation":
                        self.gateway._web_generation += 1
                    elif mode == "epoch":
                        snapshot = self.fixture.registry.snapshot()
                        modules = dict(snapshot.modules)
                        modules[view.module_id] = replace(
                            modules[view.module_id], epoch=view.module_epoch + 1
                        )
                        self.fixture.registry._snapshot = replace(
                            snapshot, modules=modules
                        )
                    else:
                        descriptor = (
                            self.fixture.registry.snapshot()
                            .module(view.module_id)
                            .manifest.capabilities[0]
                        )
                        object.__setattr__(
                            descriptor,
                            "invocation_policy",
                            InvocationPolicy.COMMAND_ONLY,
                        )

                def changed_root_callback(work, *, name=None, **kwargs):
                    task = create(work, name=name, **kwargs)
                    if name == "gateway:public_web":
                        task.add_done_callback(invalidate_before_publication)
                    return task

                with patch("asyncio.create_task", changed_root_callback):
                    result = await self._invoke(proof=proof)
                self.assertEqual(result.error.code, ErrorCode.MODULE_UNAVAILABLE)
                self.assertFalse(self.proofs.active)
                self.assertTrue(await self.gateway.drain_public_web(0.5))

    async def test_four_global_two_same_bearer_and_actual_done_release(self):
        started, release = asyncio.Event(), asyncio.Event()
        handler = await self._ready_web(_Handler(started=started, release=release))
        calls = [
            asyncio.create_task(self._invoke(key=key)) for key in ("a", "a", "b", "b")
        ]
        try:

            async def all_started():
                while len(handler.calls) < 4:
                    await asyncio.sleep(0)

            await asyncio.wait_for(all_started(), 1)
            self.assertEqual(
                (await self._invoke(key="c")).error.code, ErrorCode.RATE_LIMITED
            )
            calls[0].cancel()
            calls[0].cancel()
            with self.assertRaises(asyncio.CancelledError):
                await calls[0]
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertEqual(
                (await self._invoke(key="b")).error.code, ErrorCode.RATE_LIMITED
            )
        finally:
            release.set()
            await asyncio.gather(*calls, return_exceptions=True)
            self.assertTrue(await self.gateway.drain_public_web(0.5))
        self.assertEqual((await self._invoke(key="a")).status, ResultStatus.SUCCESS)

    async def test_detached_work_holds_budget_and_late_scope_spawn_is_closed(self):
        release, started, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        late_called = []

        class Detached(_Handler):
            scope = None
            late_rejected = False

            async def invoke(inner, context, parameters):
                child_started = asyncio.Event()

                async def late():
                    late_called.append(True)

                async def detached():
                    started.set()
                    child_started.set()
                    while not release.is_set():
                        try:
                            await release.wait()
                        except asyncio.CancelledError:
                            cancelled.set()
                    from ygl_test_subject.core.task_scope import ScopeCancelled

                    try:
                        inner.scope.create_task(late(), name="late-scope-spawn")
                    except ScopeCancelled:
                        inner.late_rejected = True

                inner.scope.create_task(detached(), name="detached-web-work")
                await child_started.wait()
                return _result()

        handler = await self._ready_web(Detached())
        handler.scope = self.fixture.lifecycle.scope("testpkg/records")
        self.assertEqual((await self._invoke()).status, ResultStatus.SUCCESS)
        await asyncio.wait_for(cancelled.wait(), 1)
        self.assertTrue(self.gateway.public_web_pending)
        self.assertFalse(self.proofs.active)
        self.assertEqual((await self._invoke()).status, ResultStatus.SUCCESS)
        self.assertEqual((await self._invoke()).error.code, ErrorCode.RATE_LIMITED)
        self.assertFalse(await self.gateway.drain_public_web(0.01))
        release.set()
        self.assertTrue(await self.gateway.drain_public_web(0.5))
        self.assertTrue(handler.late_rejected)
        self.assertEqual(late_called, [])

    async def test_deadline_and_synchronous_fence_revoke_before_drain(self):
        entered, release = asyncio.Event(), asyncio.Event()
        await self._ready_web(_Handler(started=entered, release=release))
        proof = self.proofs.new(
            "testpkg/records",
            "record.query",
            deadline=asyncio.get_running_loop().time() + 0.02,
        )
        self.assertEqual(
            (await self._invoke(proof=proof)).error.code, ErrorCode.MODULE_UNAVAILABLE
        )
        self.assertIn(proof, self.proofs.revoked)
        self.assertTrue(await self.gateway.drain_public_web(0.5))
        request = asyncio.create_task(self._invoke())
        while (
            len(
                self.fixture.registry.snapshot()
                .module("testpkg/records")
                .handlers.capabilities["record.query"]
                .calls
            )
            < 2
        ):
            await asyncio.sleep(0)
        self.gateway.fence_public_web()
        self.assertFalse(self.proofs.active)
        self.assertTrue(
            all(flight.sealed for flight in self.gateway._web_flights.values())
        )
        self.assertEqual(
            (await self._invoke()).error.code, ErrorCode.MODULE_UNAVAILABLE
        )
        await request
        self.assertTrue(await self.gateway.drain_public_web(0.5))


def _result(*, privacy: Privacy = Privacy.PUBLIC) -> CapabilityResult:
    document = DisplayDocument("Result", "subject", (TextBlock("ok"),), privacy=privacy)
    return CapabilityResult(
        "result", ResultStatus.SUCCESS, document=document, privacy=privacy
    )


class _Handler:
    def __init__(self, result: object | None = None, *, started=None, release=None):
        self.calls: list[tuple[object, object]] = []
        self.result = _result() if result is None else result
        self.started = started
        self.release = release

    async def invoke(self, context, parameters):
        self.calls.append((context, parameters))
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            await self.release.wait()
        if isinstance(self.result, BaseException):
            raise self.result
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


class _BrokenMapping(Mapping):
    def __init__(self, failure: str):
        self.failure = failure

    def __iter__(self):
        if self.failure == "iter":
            raise RuntimeError("parameter iterator secret")
        return iter(("id",))

    def __len__(self):
        return 1

    def __getitem__(self, key):
        if self.failure == "getitem":
            raise RuntimeError("parameter value secret")
        return "record"


def _capability(
    capability_id: str = "record.query",
    *,
    policy: InvocationPolicy = InvocationPolicy.NATURAL_LANGUAGE_ALLOWED,
    effect: CapabilityEffect = CapabilityEffect.READ_ONLY,
    privacy: PrivacyFloor = PrivacyFloor.PUBLIC,
    required_config: tuple[str, ...] = (),
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_id,
        {
            "type": "object",
            "properties": {"record_id": {"type": "string", "minLength": 1}},
            "required": ["record_id"],
        },
        policy,
        effect,
        privacy_floor=privacy,
        required_config=required_config,
    )


def _package(capability: CapabilityDescriptor, *, command=True, tool=True):
    commands = (
        (
            CommandDescriptor(
                "查询", capability.capability_id, {"id": "record_id"}, "query"
            ),
        )
        if command
        else ()
    )
    tools = (
        (
            ToolDescriptor(
                "record_query", capability.capability_id, {"id": "record_id"}, "query"
            ),
        )
        if tool
        else ()
    )
    module = ModuleManifest(
        "records",
        "records",
        ModuleCategory.GAME,
        "tests.module:Factory",
        "1.0.0",
        (capability,),
        commands,
        tools,
    )
    package = PackageManifest(
        "testpkg",
        "1.0.0",
        CONTRACT_VERSION,
        (module,),
        "Tests",
        "MIT",
        "offline",
    )
    return package


class GatewayTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.registry = Registry()
        self.issuer = ContextIssuer()
        self.lifecycle = None
        self.health = None

    def _new_runtime(self):
        self.health = HealthResolver(config_snapshot=self._empty_config)
        self.lifecycle = LifecycleController(
            self.registry,
            issuer=self.issuer,
            capability_health_query=self.health.query,
        )
        self.health.bind_runtime(self.lifecycle, self.lifecycle.admission)

    async def _empty_config(self, module_id):
        from ygl_test_subject.api.services import ConfigSnapshot, ConfigTarget

        return ConfigSnapshot(1, {}, target=ConfigTarget("test-host", module_id))

    def _gateway(self):
        return Gateway(
            self.registry,
            self.issuer,
            admission=self.lifecycle.admission,
            lifecycle=self.lifecycle,
        )

    async def _ready(self, capability=None, handler=None, *, command=True, tool=True):
        if self.registry.snapshot().modules:
            self.registry = Registry()
            self.issuer = ContextIssuer()
        self._new_runtime()
        capability = capability or _capability()
        handler = handler or _Handler()
        self.registry.register_package(
            _package(capability, command=command, tool=tool),
            {"records": ModuleHandlers({capability.capability_id: handler}, {}, {})},
        )
        module = self.registry.snapshot().module("testpkg/records")
        instance = _Instance(
            module.handlers,
            tuple(item.capability_id for item in module.manifest.capabilities),
        )
        install_id = uuid4().hex
        handlers = self.lifecycle.adopt_candidate(
            "testpkg", module.manifest, install_id, instance
        )
        self.lifecycle.install_dormant(
            "testpkg", module.module_id, install_id, instance, handlers
        )
        run_id = uuid4().hex
        identity, _ = await self.lifecycle.start_candidate(module.module_id, run_id)
        await self.health.prepare(module.module_id, module.manifest)
        self.lifecycle.publish_committed_intent(
            module.module_id,
            run_id,
            identity,
            True,
            self.registry.snapshot().revision,
        )
        snapshot = self.registry.snapshot()
        module = snapshot.module("testpkg/records")
        view = self.issuer.issue(
            origin=InvocationOrigin.COMMAND,
            module_id="testpkg/records",
            module_epoch=snapshot.modules["testpkg/records"].epoch,
            registry_revision=snapshot.revision,
            actor_id="actor",
            conversation_id="conversation",
            capability_id=capability.capability_id,
        )
        try:
            self.lifecycle.admission.admit(view, capability.capability_id)
        except Exception:
            # Unavailable HealthResolver state is exercised through Gateway.
            pass
        return handler, view

    async def test_iv01_command_and_tool_map_parameters(self):
        handler, view = await self._ready()
        gateway = self._gateway()
        result = await gateway.invoke_command(view, "查询", {"id": "one"})
        self.assertEqual(result.status, ResultStatus.SUCCESS)
        self.assertEqual(handler.calls[-1][1], {"record_id": "one"})

        tool_view = self.issuer.issue(
            origin=InvocationOrigin.LLM_TOOL,
            module_id=view.module_id,
            module_epoch=view.module_epoch,
            registry_revision=view.registry_revision,
            actor_id="actor",
            conversation_id="conversation",
            capability_id="record.query",
        )
        result = await gateway.invoke_tool(tool_view, "record_query", {"id": "two"})
        self.assertEqual(result.status, ResultStatus.SUCCESS)
        self.assertEqual(handler.calls[-1][1], {"record_id": "two"})

    async def test_iv02_origin_and_policy_boundaries(self):
        handler, command_view = await self._ready()
        gateway = self._gateway()
        wrong = await gateway.invoke_tool(command_view, "record_query", {"id": "x"})
        self.assertEqual(wrong.error.code, ErrorCode.UNSUPPORTED)
        self.assertEqual(len(handler.calls), 0)
        missing = await gateway.invoke_tool(
            self.issuer.issue(
                origin=InvocationOrigin.LLM_TOOL,
                module_id=command_view.module_id,
                module_epoch=command_view.module_epoch,
                registry_revision=command_view.registry_revision,
                actor_id="actor",
                conversation_id="conversation",
                capability_id="record.query",
            ),
            "missing",
            {"id": "x"},
        )
        self.assertEqual(missing.error.code, ErrorCode.NOT_FOUND)

        command_only = _capability(
            policy=InvocationPolicy.COMMAND_ONLY, effect=CapabilityEffect.WRITE
        )
        handler, view = await self._ready(command_only, command=True, tool=False)
        unsupported = await self._gateway().invoke_command(view, "查询", {"id": "x"})
        self.assertEqual(unsupported.status, ResultStatus.SUCCESS)
        self.assertEqual(len(handler.calls), 1)

        private = _capability(
            policy=InvocationPolicy.COMMAND_ONLY, privacy=PrivacyFloor.PRIVATE
        )
        handler, view = await self._ready(private, command=True, tool=False)
        denied_private = await self._gateway().invoke_command(view, "查询", {"id": "x"})
        self.assertEqual(denied_private.error.code, ErrorCode.MODULE_UNAVAILABLE)
        self.assertEqual(handler.calls, [])

        # Tamper with a validated declaration after registration to exercise
        # the Gateway policy independently of ModuleManifest construction.
        capability = _capability()
        handler, _ = await self._ready(capability, command=True, tool=True)
        object.__setattr__(
            capability, "invocation_policy", InvocationPolicy.COMMAND_ONLY
        )
        snapshot = self.registry.snapshot()
        module = snapshot.modules["testpkg/records"]
        tool_view = self.issuer.issue(
            origin=InvocationOrigin.LLM_TOOL,
            module_id="testpkg/records",
            module_epoch=module.epoch,
            registry_revision=snapshot.revision,
            actor_id="actor",
            conversation_id="conversation",
            capability_id=capability.capability_id,
        )
        denied = await self._gateway().invoke_tool(
            tool_view, "record_query", {"id": "x"}
        )
        self.assertEqual(denied.error.code, ErrorCode.UNSUPPORTED)
        self.assertEqual(handler.calls, [])

    async def test_iv03_trust_revision_epoch_and_disabled(self):
        handler, view = await self._ready()
        gateway = self._gateway()
        forged = replace(view, invocation_id="forged")
        self.assertEqual(
            (await gateway.invoke_command(forged, "查询", {"id": "x"})).error.code,
            ErrorCode.MODULE_UNAVAILABLE,
        )
        current = self.registry.snapshot().modules["testpkg/records"]
        old = self.issuer.issue(
            origin=InvocationOrigin.COMMAND,
            module_id=view.module_id,
            module_epoch=current.epoch,
            registry_revision=view.registry_revision - 1,
            actor_id="actor",
            conversation_id="conversation",
            capability_id="record.query",
        )
        self.assertEqual(
            (await gateway.invoke_command(old, "查询", {"id": "x"})).status,
            ResultStatus.SUCCESS,
        )
        self.issuer.release(old)
        epoch = self.issuer.issue(
            origin=InvocationOrigin.COMMAND,
            module_id=view.module_id,
            module_epoch=current.epoch + 1,
            registry_revision=view.registry_revision,
            actor_id="actor",
            conversation_id="conversation",
            capability_id="record.query",
        )
        self.assertEqual(
            (await gateway.invoke_command(epoch, "查询", {"id": "x"})).error.code,
            ErrorCode.MODULE_UNAVAILABLE,
        )
        await self.lifecycle.stop("testpkg/records")
        self.assertEqual(
            (await gateway.invoke_command(view, "查询", {"id": "x"})).error.code,
            ErrorCode.MODULE_UNAVAILABLE,
        )
        self.assertEqual(len(handler.calls), 1)

    async def test_iv04_parameter_errors_are_stable(self):
        handler, view = await self._ready()
        gateway = self._gateway()
        for parameters in ({}, {"secret": "do-not-echo"}, {"id": 1}):
            result = await gateway.invoke_command(view, "查询", parameters)
            self.assertEqual(result.error.code, ErrorCode.PARAMETER_ERROR)
            self.assertNotIn("do-not-echo", result.error.message)
        for broken in (_BrokenMapping("iter"), _BrokenMapping("getitem")):
            result = await gateway.invoke_command(view, "查询", broken)
            self.assertEqual(result.error.code, ErrorCode.PARAMETER_ERROR)
            self.assertNotIn("secret", result.error.message)
            self.assertIsNone(getattr(result.error, "__cause__", None))
            self.assertIsNone(getattr(result.error, "__context__", None))
        self.assertEqual(handler.calls, [])

    async def test_iv05_release_or_disable_rejects_late_result(self):
        started = asyncio.Event()
        release = asyncio.Event()
        handler, view = await self._ready(
            handler=_Handler(started=started, release=release)
        )
        gateway = self._gateway()
        pending = asyncio.create_task(gateway.invoke_command(view, "查询", {"id": "x"}))
        await started.wait()
        self.issuer.release(view)
        release.set()
        self.assertEqual((await pending).error.code, ErrorCode.MODULE_UNAVAILABLE)

        started = asyncio.Event()
        release = asyncio.Event()
        handler, view = await self._ready(
            handler=_Handler(started=started, release=release)
        )
        gateway = self._gateway()
        pending = asyncio.create_task(gateway.invoke_command(view, "查询", {"id": "x"}))
        await started.wait()
        await self.lifecycle.stop("testpkg/records")
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await pending

    async def test_iv06_handler_failures_and_private_results_are_hidden(self):
        secret = "sensitive exception detail"
        handler, view = await self._ready(handler=_Handler(RuntimeError(secret)))
        result = await self._gateway().invoke_command(view, "查询", {"id": "x"})
        self.assertEqual(result.error.code, ErrorCode.UNKNOWN)
        self.assertNotIn(secret, result.error.message)

        handler, view = await self._ready(handler=_Handler(object()))
        result = await self._gateway().invoke_command(view, "查询", {"id": "x"})
        self.assertEqual(result.error.code, ErrorCode.UNKNOWN)

        malformed = _result()
        object.__setattr__(malformed.document, "privacy", Privacy.PRIVATE)
        handler, view = await self._ready(handler=_Handler(malformed))
        result = await self._gateway().invoke_command(view, "查询", {"id": "x"})
        self.assertEqual(result.error.code, ErrorCode.UNKNOWN)

        malformed_text = _result()
        object.__setattr__(malformed_text.document.ordered_blocks[0], "text", object())
        handler, view = await self._ready(handler=_Handler(malformed_text))
        result = await self._gateway().invoke_command(view, "查询", {"id": "x"})
        self.assertEqual(result.error.code, ErrorCode.UNKNOWN)

        malformed_facts = _result()
        facts = FactDocument({"safe": "value"})
        object.__setattr__(facts, "facts", object())
        object.__setattr__(malformed_facts, "model_facts", facts)
        handler, view = await self._ready(handler=_Handler(malformed_facts))
        result = await self._gateway().invoke_command(view, "查询", {"id": "x"})
        self.assertEqual(result.error.code, ErrorCode.UNKNOWN)

        handler, view = await self._ready(
            handler=_Handler(_result(privacy=Privacy.PRIVATE))
        )
        result = await self._gateway().invoke_command(view, "查询", {"id": "x"})
        self.assertEqual(result.error.code, ErrorCode.UNKNOWN)

    async def test_iv07_unimplemented_dependencies_and_cancellation(self):
        capability = _capability(required_config=("source.api_key",))
        handler, view = await self._ready(capability)
        result = await self._gateway().invoke_command(view, "查询", {"id": "x"})
        self.assertEqual(result.error.code, ErrorCode.MODULE_UNAVAILABLE)
        self.assertEqual(handler.calls, [])

        started = asyncio.Event()
        release = asyncio.Event()
        handler, view = await self._ready(
            handler=_Handler(started=started, release=release)
        )
        task = asyncio.create_task(
            self._gateway().invoke_command(view, "查询", {"id": "x"})
        )
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_iv08_returned_result_is_deeply_detached(self):
        facts_backing = {"safe": "before"}
        facts = FactDocument({"safe": "initial"})
        object.__setattr__(facts, "facts", MappingProxyType(facts_backing))
        nested_backing = {"inner": "before"}
        fields_backing = {"payload": MappingProxyType(nested_backing)}
        fields = FieldsBlock({"payload": {"inner": "initial"}})
        object.__setattr__(fields, "fields", MappingProxyType(fields_backing))
        document = DisplayDocument("Result", "subject", (fields,))
        payload = CapabilityResult(
            "result", ResultStatus.SUCCESS, document=document, model_facts=facts
        )
        handler, view = await self._ready(handler=_Handler(payload))
        out = await self._gateway().invoke_command(view, "查询", {"id": "x"})

        self.assertIsNot(out, payload)
        self.assertIsNot(out.document, payload.document)
        self.assertIsNot(
            out.document.ordered_blocks[0], payload.document.ordered_blocks[0]
        )
        self.assertIsNot(out.document.ordered_blocks[0].fields, fields.fields)
        self.assertIsNot(out.model_facts, payload.model_facts)
        self.assertIsNot(out.model_facts.facts, payload.model_facts.facts)
        facts_backing.clear()
        facts_backing["secret"] = object()
        nested_backing.clear()
        nested_backing["secret"] = object()
        object.__setattr__(fields, "fields", {"changed": object()})
        object.__setattr__(payload, "document", None)
        self.assertEqual(dict(out.model_facts.facts), {"safe": "before"})
        self.assertEqual(
            dict(out.document.ordered_blocks[0].fields),
            {"payload": {"inner": "before"}},
        )
        with self.assertRaises(TypeError):
            out.model_facts.facts["x"] = "y"
        self.assertEqual(handler.calls[0][1], {"record_id": "x"})

    async def test_iv09_error_result_is_rebuilt(self):
        payload = CapabilityResult(
            "error-result",
            ResultStatus.ERROR,
            error=ErrorDetail(ErrorCode.UPSTREAM_ERROR, "upstream unavailable"),
        )
        handler, view = await self._ready(handler=_Handler(payload))
        out = await self._gateway().invoke_command(view, "查询", {"id": "x"})
        self.assertIsNot(out, payload)
        self.assertIsNot(out.error, payload.error)
        self.assertEqual(out.error.code, ErrorCode.UPSTREAM_ERROR)
        self.assertEqual(out.error.message, "upstream unavailable")
        object.__setattr__(payload.error, "message", "secret")
        object.__setattr__(payload, "error", None)
        self.assertEqual(out.error.message, "upstream unavailable")
        self.assertEqual(handler.calls[0][1], {"record_id": "x"})


if __name__ == "__main__":
    import unittest

    unittest.main()
