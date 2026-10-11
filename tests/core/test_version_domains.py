"""Real offline Core version-domain receivers; not native Runtime.start."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from time import monotonic

from ygl_test_subject.core.contracts.manifests import ExtensionManifestABI
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.invocation import Gateway
from ygl_test_subject.core.lifecycle import _ServiceLifetime
from ygl_test_subject.core.ports import RootOutputOutcome
from ygl_test_subject.core.registry import Registry
from ygl_test_subject.extensions.disk_manifest import ManifestError, parse_manifest
from ygl_test_subject.infrastructure.sqlite.repositories_output import (
    SQLiteRootOutputRepository,
)
from ygl_test_subject.infrastructure.sqlite.repositories_subscriptions import (
    SQLiteDeliveryRepository,
    SQLiteSubscriptionStore,
)
from ygl_test_subject.services.identity import InvocationPrincipalResolver
from ygl_test_subject.services.output import (
    LifecycleApprovedSendScheduler,
    OutputService,
    OutputStatus,
)

import yomihime_game_link_sdk as ygl
from tests.fixtures.b03_runtime import build_runtime
from tests.fixtures.version_domain_module import Factory, Handler, result
from tests.services import test_output as outlet

ROOT = Path(__file__).resolve().parents[2]


def document():
    return {
        "schema_version": 1,
        "package_id": "version_pkg",
        "package_version": "1.0.0",
        "contract_version": "2.0",
        "author": "tests",
        "license": "MIT",
        "source": "offline",
        "modules": [
            {
                "module_id": "status",
                "route": "versioncheck",
                "category": "platform",
                "factory_entry": "tests.fixtures.version_domain_module:Factory",
                "module_version": "1.0.0",
                "capabilities": [
                    {
                        "capability_id": "read",
                        "input_schema": {
                            "type": "object",
                            "properties": {},
                            "required": [],
                            "additionalProperties": False,
                        },
                        "invocation_policy": "command_only",
                        "effect": "read_only",
                        "output_version": "1.8.0",
                        "required_capabilities": [
                            {
                                "module_id": "version_child/status",
                                "capability_id": "read",
                            }
                        ],
                    }
                ],
                "commands": [
                    {
                        "operation_path": "read",
                        "capability_id": "read",
                        "parameter_mapping": {},
                        "help_text": "read",
                    }
                ],
            }
        ],
    }


class VersionDomainTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="s2c-version-")
        self.addCleanup(self.temp.cleanup)
        self.runtime = await build_runtime(Path(self.temp.name))
        self.owned_modules = ["sample/alpha", "sample/beta"]
        self.addAsyncCleanup(self.close_runtime)
        child = document()
        child["package_id"] = "version_child"
        child["modules"][0]["route"] = "versionchild"
        del child["modules"][0]["capabilities"][0]["required_capabilities"]
        self.child_package = parse_manifest(json.dumps(child).encode())
        self.child_handler = Handler()
        self.child_factory = Factory(self.child_handler)
        self.child_instance = await self.install(self.child_package, self.child_factory)
        self.handler = Handler()
        self.factory = Factory(self.handler)
        self.package = parse_manifest(json.dumps(document()).encode())
        self.instance = await self.install(self.package, self.factory)
        lifecycle = self.runtime.lifecycle
        self.gateway = Gateway(
            self.runtime.registry,
            self.runtime.issuer,
            admission=lifecycle.admission,
            lifecycle=lifecycle,
        )
        self.messages = outlet._MessagePort()
        self.root_outputs = SQLiteRootOutputRepository(self.runtime.database)
        self.output = OutputService(
            issuer=self.runtime.issuer,
            admission=lifecycle.admission,
            send_scheduler=LifecycleApprovedSendScheduler(lifecycle),
            registry=self.runtime.registry,
            renderer=outlet._Renderer(),
            limits=ygl.DisplayLimits(2, 1024),
            conversations=outlet._Conversations(
                ygl.ConversationRef(
                    "test-adapter", ygl.ConversationKind.GROUP, "room", "test-route"
                )
            ),
            message_port=self.messages,
            deliveries=SQLiteDeliveryRepository(self.runtime.database),
            grants=self.runtime.repositories.grant_revocation,
            subscriptions=SQLiteSubscriptionStore(self.runtime.database),
            root_outputs=self.root_outputs,
            resource_visibility=outlet._ResourceVisibility(self.runtime.database),
            principal_resolver=InvocationPrincipalResolver(
                self.runtime.issuer,
                self.runtime.repositories.identities,
                identity_namespace="test-users",
                admission=lifecycle.admission,
            ),
            claim_lease=timedelta(minutes=1),
            send_timeout=5,
        )
        self.trace = []
        # Actual valid declaration, factory and owned lifecycle are required
        # before any unsupported-version assertion; no homemade proof/readiness.
        checked = await self.call(result())
        self.assertIs(checked.status, ygl.ResultStatus.SUCCESS)
        self.assertEqual(checked.schema_version, "1.8.0")
        self.assertEqual(checked.model_facts.schema_version, "1.8.0")
        self.assertEqual(checked.document.schema_version, "1.8.0")
        self.assertEqual(self.instance.starts, 1)
        self.trace.append(
            "actual_parse_current_register_factory_lifecycle_issuer_admit_bind_Gateway_positive"
        )

    async def close_runtime(self):
        for view in tuple(self.runtime.issuer._issued.values()):
            self.runtime.issuer.release(view)
        for module_id in reversed(self.owned_modules):
            await self.runtime.lifecycle.stop(module_id)
        await self.runtime.services.close_credentials()
        await self.runtime.database.executor.close(timeout=2)
        self.temp.cleanup()

    async def install(self, package, factory):
        module = package.modules[0]
        global_id = package.package_id + "/" + module.module_id
        self.runtime.registry.register_package(
            package,
            {module.module_id: ygl.ModuleHandlers({"read": factory.handler}, {}, {})},
        )
        install_id = global_id + "-install"
        lifetime = _ServiceLifetime((package.package_id, global_id, install_id))
        candidate_services = self.runtime.services.for_candidate(
            global_id, module, service_lifetime=lifetime
        )
        instance = await factory.create(candidate_services)
        lifecycle = self.runtime.lifecycle
        handlers = lifecycle.adopt_candidate(
            package.package_id, module, install_id, instance, service_lifetime=lifetime
        )
        lifecycle.install_dormant(
            package.package_id, global_id, install_id, instance, handlers
        )
        self.owned_modules.append(global_id)
        identity, _ = await lifecycle.start_candidate(global_id, global_id + "-start")
        await self.runtime.health.prepare(global_id, module)
        lifecycle.publish_committed_intent(
            global_id,
            global_id + "-start",
            identity,
            True,
            self.runtime.registry.snapshot().revision,
        )
        return instance

    async def call(self, original, *, route=False):
        snapshot = self.runtime.registry.snapshot()
        module = snapshot.module("version_pkg/status")
        view = self.runtime.issuer.issue(
            origin=ygl.InvocationOrigin.COMMAND,
            module_id=module.module_id,
            module_epoch=module.epoch,
            registry_revision=snapshot.revision,
            actor_id="alice",
            conversation_id="room",
            adapter_id="test-adapter",
            capability_id="read",
            deadline=monotonic() + 30,
        )
        try:
            self.runtime.lifecycle.admission.admit(view, "read")
            bound = await self.handler.services.scopes.bind(view)
            self.assertEqual(bound.invocation.invocation_id, view.invocation_id)
            before = len(self.handler.calls)
            self.handler.result = original
            checked = await self.gateway.invoke_command(view, "read", {})
            self.assertEqual(len(self.handler.calls), before + 1)
            self.assertEqual(self.handler.calls[-1].invocation_id, view.invocation_id)
            self.assertEqual(
                self.handler.bound.invocation.invocation_id, view.invocation_id
            )
            if route:
                published = await self.output.route(view, checked)
                self.assertIs(published.status, OutputStatus.SENT, published.error_code)
                self.last_output_invocation = view.invocation_id
            return checked
        finally:
            self.runtime.issuer.release(view)

    async def test_disk_default_matches_actual_output_domain(self):
        sdk_default = ygl.CapabilityDescriptor(
            "read",
            {"type": "object"},
            ygl.InvocationPolicy.COMMAND_ONLY,
            ygl.CapabilityEffect.READ_ONLY,
        ).output_version
        self.assertEqual(sdk_default, "1.8.0")
        for relative in (
            "modules/ff14/yomihime.manifest.json",
            "examples/offline_sample/manifest.json",
        ):
            original = json.loads((ROOT / relative).read_bytes())
            omitted = [
                (m["module_id"], c["capability_id"])
                for m in original["modules"]
                for c in m["capabilities"]
                if "output_version" not in c
            ]
            self.assertTrue(omitted, relative)
            explicit = json.loads(json.dumps(original))
            for module in explicit["modules"]:
                for capability in module["capabilities"]:
                    capability["output_version"] = "1.8.0"
            parsed_control = parse_manifest(json.dumps(explicit).encode())
            self.assertTrue(
                all(
                    c.output_version == "1.8.0"
                    for m in parsed_control.modules
                    for c in m.capabilities
                )
            )
            parsed = parse_manifest((ROOT / relative).read_bytes())
            versions = {
                (m.module_id, c.capability_id): c.output_version
                for m in parsed.modules
                for c in m.capabilities
            }
            for identity in omitted:
                with self.subTest(source=relative, capability=identity):
                    self.assertEqual(
                        versions[identity],
                        sdk_default,
                        "actual disk omitted declaration differs from accepted current output",
                    )
        self.trace.append(
            "V01_actual_FF_and_sample_bytes_parse_explicit_current_positive_omitted_default"
        )

    async def test_unsupported_descriptor_versions_refuse_before_registration(self):
        # Descriptive Python default must pass the actual Registry receiver,
        # independently of the disk parser's omitted-field default.
        declared = self.package.modules[0]
        original = declared.capabilities[0]
        python_default = ygl.CapabilityDescriptor(
            original.capability_id,
            original.input_schema,
            original.invocation_policy,
            original.effect,
            required_capabilities=original.required_capabilities,
        )
        python_package = replace(
            self.package, modules=(replace(declared, capabilities=(python_default,)),)
        )
        omitted_disk = document()
        del omitted_disk["modules"][0]["capabilities"][0]["output_version"]
        for source, package in (
            ("Python omitted", python_package),
            ("disk omitted", parse_manifest(json.dumps(omitted_disk).encode())),
            ("disk explicit", self.package),
        ):
            with self.subTest(current_positive=source):
                valid = Registry()
                valid.register_package(package, {"status": self.instance.handlers()})
                self.assertEqual(
                    valid.snapshot()
                    .module("version_pkg/status")
                    .manifest.capabilities[0]
                    .output_version,
                    "1.8.0",
                )

        class StringSubclass(str):
            pass

        values = tuple("1.%d.0" % n for n in range(8)) + (
            "2.0.0",
            "0.1.0a3",
            None,
            1,
            True,
            StringSubclass("1.8.0"),
        )
        for version in values:
            registry = Registry()
            registry.register_package(
                self.package, {"status": self.instance.handlers()}
            )
            before = registry.snapshot()
            packages = frozenset(registry._packages)
            factory_calls, handler_calls = (
                len(self.factory.calls),
                len(self.handler.calls),
            )
            old = self.package.modules[0]
            descriptor = replace(old.capabilities[0], output_version=version)
            module = replace(
                old, module_id="blocked", route="blocked", capabilities=(descriptor,)
            )
            package = replace(self.package, package_id="blocked_pkg", modules=(module,))
            rejected = False
            try:
                registry.register_package(
                    package, {"blocked": self.instance.handlers()}
                )
            except ValueError:
                rejected = True
            with self.subTest(
                version=version,
                value_type=type(version).__name__,
                receiver="Registry_reject",
            ):
                self.assertTrue(
                    rejected, "actual Registry accepted unsupported output declaration"
                )
            with self.subTest(
                version=version,
                value_type=type(version).__name__,
                receiver="Registry_unchanged",
            ):
                self.assertIs(registry.snapshot(), before)
                self.assertEqual(registry.snapshot().revision, before.revision)
                self.assertEqual(registry.snapshot().modules, before.modules)
                self.assertEqual(registry.snapshot().routes, before.routes)
                self.assertEqual(registry.snapshot().tools, before.tools)
                self.assertEqual(frozenset(registry._packages), packages)
            self.assertEqual(len(self.factory.calls), factory_calls)
            self.assertEqual(len(self.handler.calls), handler_calls)
            if type(version) is not StringSubclass:
                disk = document()
                disk["modules"][0]["capabilities"][0]["output_version"] = version
                with self.subTest(version=version, receiver="disk_reject"):
                    with self.assertRaises(ManifestError):
                        parse_manifest(json.dumps(disk).encode())
        self.trace.append(
            "V02_actual_Registry_reject_and_snapshot_packages_factory_handler_delta0"
        )

    async def dependency_call(self, original):
        snapshot = self.runtime.registry.snapshot()
        module = snapshot.module("version_pkg/status")
        view = self.runtime.issuer.issue(
            origin=ygl.InvocationOrigin.COMMAND,
            module_id=module.module_id,
            module_epoch=module.epoch,
            registry_revision=snapshot.revision,
            actor_id="alice",
            conversation_id="room",
            adapter_id="test-adapter",
            capability_id="read",
            deadline=monotonic() + 30,
        )
        try:
            self.runtime.lifecycle.admission.admit(view, "read")
            bound = await self.handler.services.scopes.bind(view)
            self.child_handler.result = original
            before = len(self.child_handler.calls)
            checked = await bound.dependencies.invoke(
                view, ygl.CapabilityReference("version_child/status", "read"), {}
            )
            self.assertEqual(len(self.child_handler.calls), before + 1)
            child = self.child_handler.calls[-1]
            self.assertEqual(child.parent_id, view.invocation_id)
            self.assertEqual(
                self.child_handler.bound.invocation.invocation_id, child.invocation_id
            )
            self.assertNotIn(child.invocation_id, self.runtime.issuer._issued)
            return checked
        finally:
            self.runtime.issuer.release(view)

    async def test_parsed_module_factory_bind_gateway_all_output_statuses(self):
        for status in ygl.ResultStatus:
            with self.subTest(status=status.value):
                original = result(status)
                checked = await self.call(original, route=True)
                self.assertIs(type(checked), ygl.CapabilityResult)
                self.assertIs(type(checked.model_facts), ygl.FactDocument)
                self.assertIs(checked.status, status)
                self.assertEqual(checked.schema_version, "1.8.0")
                self.assertEqual(checked.model_facts.schema_version, "1.8.0")
                self.assertEqual(checked.model_facts.facts, original.model_facts.facts)
                if status is ygl.ResultStatus.ERROR:
                    self.assertEqual(checked.error, original.error)
                    self.assertIsNone(checked.document)
                    self.assertEqual(
                        checked.model_facts.facts["supplement"]["archive"]["available"],
                        None,
                    )
                else:
                    self.assertIs(type(checked.document), ygl.DisplayDocument)
                    self.assertEqual(checked.document.schema_version, "1.8.0")
                    self.assertEqual(checked.document, original.document)
                row = await self.runtime.database.executor.run_read(
                    lambda unit: tuple(
                        unit.execute(
                            "SELECT state, output_outcome, receipt_status FROM b04_root_outputs WHERE root_invocation_id=?",
                            (self.last_output_invocation,),
                        ).fetchone()
                    )
                )
                self.assertEqual(
                    row, ("completed", RootOutputOutcome.MESSAGE.value, "accepted")
                )
        self.assertEqual(len(self.messages.calls), 4)
        self.assertFalse(self.runtime.transport.requests)
        self.trace.append(
            "V03_actual_factory_lifecycle_command_Gateway_Output_SQLite_claim_all_four_statuses"
        )

    async def test_each_domain_rejects_unsupported_values_at_its_receiver(self):
        for abi in ("1.0.0", "1.8.0", "3.0", "0.1.0a3", None, True):
            with self.subTest(module_ABI=abi):
                raw = document()
                raw["contract_version"] = abi
                with self.assertRaises(ManifestError):
                    parse_manifest(json.dumps(raw).encode())
                with self.assertRaises(ValueError):
                    validate_contract(replace(self.package, contract_version=abi))
        for disk in (True, 0, 2, "1"):
            with self.subTest(disk_schema=disk):
                raw = document()
                raw["schema_version"] = disk
                with self.assertRaises(ManifestError):
                    parse_manifest(json.dumps(raw).encode())
        self.assertEqual(ExtensionManifestABI().factory_abi_version, 1)
        for factory in (True, 0, 2, "1"):
            with self.subTest(factory_ABI=factory):
                with self.assertRaises(ValueError):
                    ExtensionManifestABI(factory_abi_version=factory)
        raw = document()
        raw["modules"][0]["capabilities"][0]["unknown_version"] = "1.8.0"
        with self.assertRaises(ManifestError):
            parse_manifest(json.dumps(raw).encode())
        self.assertIs(
            (await self.dependency_call(result())).status, ygl.ResultStatus.SUCCESS
        )
        for version in tuple("1.%d.0" % n for n in range(8)) + ("2.0.0", "unknown"):
            for field in ("result", "fact", "display"):
                with self.subTest(actual_version=version, component=field):
                    original = result()
                    if field == "result":
                        original = replace(original, schema_version=version)
                    elif field == "fact":
                        original = replace(
                            original,
                            model_facts=replace(
                                original.model_facts, schema_version=version
                            ),
                        )
                    else:
                        original = replace(
                            original,
                            document=replace(original.document, schema_version=version),
                        )
                    for receiver in (self.call, self.dependency_call):
                        checked = await receiver(original)
                        self.assertIs(checked.status, ygl.ResultStatus.ERROR)
                        self.assertEqual(checked.error.code, ygl.ErrorCode.UNKNOWN)
                        self.assertEqual(checked.schema_version, "1.8.0")
                        self.assertIsNone(checked.document)
                        self.assertIsNone(checked.model_facts)
                        self.assertNotIn(version, checked.error.message)
        self.assertFalse(self.messages.calls)
        self.assertFalse(self.runtime.transport.requests)
        self.trace.append(
            "V04_independent_ABI_disk_factory_and_actual_Gateway_boundDependency_result_fact_display_rejections"
        )

    async def test_controlled_candidate_rejects_bad_output_before_factory_capture(self):
        # Official scanner port consumes real bytes; not native filesystem discovery.
        from secrets import token_urlsafe
        from types import SimpleNamespace

        from ygl_test_subject.core.contracts.administration import AdminOperation
        from ygl_test_subject.extensions.discovery import DiscoveredPackage
        from ygl_test_subject.extensions.loader import (
            CandidateState,
            ExtensionCandidate,
            ExtensionLoader,
        )
        from ygl_test_subject.infrastructure.sqlite.repositories_admin_credentials import (
            SQLiteAdminCredentialRepository,
        )
        from ygl_test_subject.infrastructure.sqlite.repositories_runtime import (
            SQLiteModuleRuntimeRepository,
        )
        from ygl_test_subject.services.admin_authorization import (
            AdminAuthorizationService,
            _digest,
            _matches,
        )
        from ygl_test_subject.services.extension_runtime import (
            ExtensionCandidateUnavailable,
            ExtensionRuntime,
        )

        class Source:
            def __init__(self):
                self.capture_calls = self.resolve_calls = 0
                self.factory = Factory()

            async def capture(self, candidate):
                self.capture_calls += 1
                owner = self

                class Lease:
                    def resolve(self, entry):
                        if entry != "tests.fixtures.version_domain_module:Factory":
                            raise AssertionError("unexpected factory entry")
                        owner.resolve_calls += 1
                        return owner.factory

                    def release(self):
                        return None

                return Lease()

        versions = (
            ("1.8.0",)
            + tuple("1.%d.0" % n for n in range(8))
            + ("2.0.0", "0.1.0a3", None, True)
        )
        for index, version in enumerate(versions):
            with self.subTest(output_version=version):
                root = Path(self.temp.name) / ("candidate-%d" % index)
                real = await build_runtime(root)
                credentials = SQLiteAdminCredentialRepository(real.database)
                secret = token_urlsafe(48)
                await credentials.bootstrap(_digest(secret))
                state = await credentials.current()
                self.assertTrue(_matches(secret, state))
                session = SimpleNamespace(
                    adapter_id="version-admin",
                    request_id="request",
                    session_id="session",
                )
                auth = AdminAuthorizationService(
                    credentials,
                    admission=real.lifecycle.admission,
                    context_validator=lambda op,
                    invocation,
                    context,
                    generation: context is session and generation == state.generation,
                )

                async def validate_generation(grant):
                    await auth.validate_generation(
                        grant, operation=AdminOperation.SET_ENABLED
                    )

                raw = document()
                raw["package_id"] = "version_candidate"
                raw["modules"][0]["route"] = "versioncandidate"
                cap = raw["modules"][0]["capabilities"][0]
                del cap["required_capabilities"]
                cap["output_version"] = version
                payload = json.dumps(raw).encode()

                def scanner(_root):
                    try:
                        parsed = parse_manifest(payload)
                    except ManifestError:
                        package = DiscoveredPackage(
                            None, root / "version_candidate", None, "manifest_invalid"
                        )
                        return (
                            ExtensionCandidate(
                                package, CandidateState.INVALID, "manifest_invalid"
                            ),
                        )
                    package = DiscoveredPackage(
                        parsed.package_id, root / parsed.package_id, parsed
                    )
                    return (
                        ExtensionCandidate(
                            package, CandidateState.DISABLED, "not_enabled"
                        ),
                    )

                source = Source()
                extension = ExtensionRuntime(
                    root,
                    registry=real.registry,
                    lifecycle=real.lifecycle,
                    authorization=auth,
                    validate_admin_grant=validate_generation,
                    runtime_repository=SQLiteModuleRuntimeRepository(real.database),
                    module_services=real.services,
                    health_resolver=real.health,
                    factory_source=source,
                    scanner=scanner,
                    cleanup_timeout=1,
                )
                try:
                    before = real.registry.snapshot()
                    packages = frozenset(real.registry._packages)
                    candidate = ExtensionLoader(extension).scan()[0]
                    if version == "1.8.0":
                        self.assertIs(candidate.state, CandidateState.DISABLED)
                        active = await extension.set_enabled(
                            None,
                            "version_candidate/status",
                            True,
                            expected_registry_revision=before.revision,
                            authorization=session,
                        )
                        self.assertTrue(active.enabled)
                        self.assertEqual(
                            (
                                source.capture_calls,
                                source.resolve_calls,
                                len(source.factory.calls),
                            ),
                            (1, 1, 1),
                        )
                        self.assertIsNotNone(
                            real.lifecycle.instance("version_candidate/status")
                        )
                    else:
                        self.assertIs(candidate.state, CandidateState.INVALID)
                        self.assertEqual(candidate.reason_code, "manifest_invalid")
                        self.assertIsNone(candidate.package.manifest)
                        with self.assertRaises(ExtensionCandidateUnavailable):
                            await extension.set_enabled(
                                None,
                                "version_candidate/status",
                                True,
                                expected_registry_revision=before.revision,
                                authorization=session,
                            )
                        self.assertEqual(
                            (
                                source.capture_calls,
                                source.resolve_calls,
                                len(source.factory.calls),
                            ),
                            (0, 0, 0),
                        )
                        self.assertIs(real.registry.snapshot(), before)
                        self.assertEqual(frozenset(real.registry._packages), packages)
                        self.assertEqual(
                            real.registry.snapshot().revision, before.revision
                        )
                        self.assertEqual(real.registry.snapshot().routes, before.routes)
                        self.assertEqual(real.registry.snapshot().tools, before.tools)
                finally:
                    await extension.close(timeout=1)
                    auth.close()
                    for module_id in ("sample/alpha", "sample/beta"):
                        await real.lifecycle.stop(module_id)
                    await real.services.close_credentials()
                    await real.database.executor.close(timeout=2)
        self.trace.append(
            "V02_controlled_raw_parser_scanner_real_ExtensionLoader_Runtime_auth_current_enable_invalid_capture_resolve_create0"
        )

    async def test_source_storage_probe(self):
        from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
        from ygl_test_subject.infrastructure.sqlite.repositories_config import (
            SQLiteRecordRepository,
        )

        snapshot = self.runtime.registry.snapshot()
        module = snapshot.module("sample/alpha")
        view = self.runtime.issuer.issue(
            origin=ygl.InvocationOrigin.COMMAND,
            module_id=module.module_id,
            module_epoch=module.epoch,
            registry_revision=snapshot.revision,
            actor_id="alice",
            conversation_id="room",
            adapter_id="test-adapter",
            capability_id="read",
            deadline=monotonic() + 30,
        )
        try:
            self.runtime.lifecycle.admission.admit(view, "read")
            bound = await self.runtime.services.for_module("sample/alpha").scopes.bind(
                view
            )
            collection = await bound.records.collection("profiles")
            self.assertEqual(collection.scope.kind, ygl.OwnershipKind.USER)
            self.assertEqual(collection.scope.user_id, "alice")
            record = await collection.create(
                "release-neutral", {"public": "same-data", "missing": None}
            )
            self.assertIs(type(record), ygl.VersionedRecord)
            self.assertEqual(await collection.get("release-neutral"), record)
            owner = collection.scope
        finally:
            self.runtime.issuer.release(view)
        await self.call(result(), route=True)
        invocation_id = self.last_output_invocation
        database_path = self.runtime.database.path
        await self.runtime.database.executor.close(timeout=2)
        reopened = SQLiteDatabase(database_path)
        try:
            descriptor = next(
                x for x in module.manifest.collections if x.name == "profiles"
            )
            repository = SQLiteRecordRepository(reopened, module, self.runtime.lookup)
            actual = await repository.collection(module.module_id, descriptor, owner)
            restored = await actual.get("release-neutral")
            self.assertIs(type(restored), ygl.VersionedRecord)
            self.assertEqual(restored, record)
            row = await reopened.executor.run_read(
                lambda unit: tuple(
                    unit.execute(
                        "SELECT state,output_outcome,receipt_status FROM b04_root_outputs WHERE root_invocation_id=?",
                        (invocation_id,),
                    ).fetchone()
                )
            )
            self.assertEqual(
                row, ("completed", RootOutputOutcome.MESSAGE.value, "accepted")
            )
        finally:
            await reopened.executor.close(timeout=2)
        self.trace.append(
            "V05_actual_bound_USER_record_Output_claim_normal_close_reopen_same_registered_owner"
        )

    async def test_distribution_only_source_fixture_preserves_core_and_storage_semantics(
        self,
    ):
        import hashlib
        import shutil
        import subprocess
        import sys

        # Capture current, controlled build inputs; historical S2 evidence is
        # immutable and cannot be the current source fixture after an SDK change.
        paths = ["LICENSE", "pyproject.toml", "setup.py", "docs/module-sdk.md"]
        paths += [
            "yomihime_game_link_sdk/" + name
            for name in (
                "__init__.py",
                "contexts.py",
                "declarations.py",
                "display.py",
                "errors.py",
                "py.typed",
                "results.py",
                "services.py",
                "storage.py",
                "subscriptions.py",
                "version.py",
            )
        ]
        paths += [
            "examples/" + example + "/" + name
            for example in ("empty_module", "offline_sample")
            for name in ("README.md", "manifest.json", "module.py")
        ]
        self.assertEqual(len(set(paths)), 21)
        freeze = {
            "files": [
                {
                    "path": path,
                    "sha256": hashlib.sha256((ROOT / path).read_bytes()).hexdigest(),
                }
                for path in paths
            ]
        }
        self.assertEqual(ygl.__version__, "0.1.0a6")
        probe = r"""import hashlib, json, pathlib, sys, unittest
selected = pathlib.Path(sys.argv[1]).resolve()
repo = pathlib.Path(sys.argv[2]).resolve()
sys.path.insert(0, str(selected))
import yomihime_game_link_sdk as ygl
sys.path.append(str(repo))
import tests
from ygl_test_subject.core.contracts.version import CONTRACT_VERSION
from ygl_test_subject.core.contracts.manifests import EXTENSION_MANIFEST_ABI
names = [
 "tests.core.test_version_domains.VersionDomainTests.test_parsed_module_factory_bind_gateway_all_output_statuses",
 "tests.core.test_version_domains.VersionDomainTests.test_source_storage_probe",
 "tests.storage.test_display_document_upgrade.DisplayDocumentUpgradeTests.test_goldens_have_fixed_old_source_and_preserve_every_display_field",
 "tests.storage.test_display_document_upgrade.DisplayDocumentUpgradeTests.test_reopen_current_and_mixed_due_pagination_do_not_rewrite_legacy_rows",
 "tests.storage.test_display_document_upgrade.DisplayDocumentUpgradeTests.test_old_pending_and_failed_dispatch_use_real_renderer_and_deduplicate"]
class Result(unittest.TextTestResult):
 def __init__(self,*args,**kwargs):
  super().__init__(*args,**kwargs); self.methods={}; self.subtests=[]
 def addSuccess(self,t): self.methods[t.id()]="passed"; super().addSuccess(t)
 def addFailure(self,t,e): self.methods[t.id()]="failed"; super().addFailure(t,e)
 def addError(self,t,e): self.methods[t.id()]="error"; super().addError(t,e)
 def addSubTest(self,t,s,e):
  state="passed" if e is None else "failed" if issubclass(e[0],t.failureException) else "error"
  self.subtests.append(state)
  if e is not None: self.methods[t.id()]=state
  super().addSubTest(t,s,e)
r = unittest.TextTestRunner(verbosity=2,resultclass=Result).run(unittest.defaultTestLoader.loadTestsFromNames(names))
origins={}
for name,mod in tuple(sys.modules.items()):
 if name=="yomihime_game_link_sdk" or name.startswith("yomihime_game_link_sdk."):
  origin=pathlib.Path(mod.__file__).resolve()
  origins[name]=origin.relative_to(selected).as_posix()
assert ygl.CapabilityResult is sys.modules["yomihime_game_link_sdk.results"].CapabilityResult
assert ygl.FactDocument is sys.modules["yomihime_game_link_sdk.results"].FactDocument
assert ygl.DisplayDocument is sys.modules["yomihime_game_link_sdk.display"].DisplayDocument
print(json.dumps({"release":ygl.__version__,"ABI":ygl.MODULE_ABI_VERSION,"output":CONTRACT_VERSION,
 "disk":EXTENSION_MANIFEST_ABI.schema_version,"factory":EXTENSION_MANIFEST_ABI.factory_abi_version,
 "canonical":True,"origins":origins,"methods":r.methods,"testsRun":r.testsRun,
 "subtests":r.subtests,"failures":len(r.failures),"errors":len(r.errors),"skips":len(r.skipped),
 "old_rows_sha256":hashlib.sha256((repo/"tests/fixtures/display17/events.json").read_bytes()).hexdigest()}))
sys.exit(not r.wasSuccessful())
"""
        reports = []
        with tempfile.TemporaryDirectory(prefix="source-release-") as workspace:
            for profile, release in (
                ("current", "0.1.0a6"),
                ("counterfactual", "0.1.0a7"),
            ):
                with self.subTest(profile=profile):
                    selected = Path(workspace) / profile
                    changed = []
                    for entry in freeze["files"]:
                        original = ROOT / entry["path"]
                        payload = original.read_bytes()
                        self.assertEqual(
                            hashlib.sha256(payload).hexdigest(), entry["sha256"]
                        )
                        destination = selected / entry["path"]
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(original, destination)
                        if release != "0.1.0a6" and entry["path"] in (
                            "pyproject.toml",
                            "yomihime_game_link_sdk/version.py",
                        ):
                            altered = payload.replace(b"0.1.0a6", release.encode())
                            self.assertNotEqual(altered, payload)
                            destination.write_bytes(altered)
                            changed.append(entry["path"])
                        else:
                            self.assertEqual(destination.read_bytes(), payload)
                    self.assertEqual(
                        set(changed),
                        set()
                        if profile == "current"
                        else {"pyproject.toml", "yomihime_game_link_sdk/version.py"},
                    )
                    self.assertFalse(list(selected.rglob("*.dist-info")))
                    child = subprocess.run(
                        [
                            sys.executable,
                            "-I",
                            "-B",
                            "-c",
                            probe,
                            str(selected),
                            str(ROOT),
                        ],
                        capture_output=True,
                        text=True,
                        encoding="utf8",
                        timeout=90,
                    )
                    self.assertEqual(child.returncode, 0, child.stderr)
                    report = json.loads(child.stdout)
                    self.assertEqual(report["release"], release)
                    self.assertEqual(
                        (
                            report["ABI"],
                            report["output"],
                            report["disk"],
                            report["factory"],
                        ),
                        ("2.0", "1.8.0", 1, 1),
                    )
                    self.assertTrue(report["canonical"])
                    self.assertEqual(report["testsRun"], 5)
                    self.assertEqual(
                        (report["failures"], report["errors"], report["skips"]),
                        (0, 0, 0),
                    )
                    self.assertTrue(
                        all(x == "passed" for x in report["methods"].values())
                    )
                    report["changed_inputs"] = changed
                    reports.append(report)
                    for entry in freeze["files"]:
                        self.assertEqual(
                            hashlib.sha256(
                                (ROOT / entry["path"]).read_bytes()
                            ).hexdigest(),
                            entry["sha256"],
                        )
        self.assertEqual(reports[0]["old_rows_sha256"], reports[1]["old_rows_sha256"])
        self.assertEqual(reports[0]["origins"], reports[1]["origins"])
        self.trace.append({"V05_source_counterfactual_not_installed": reports})

    async def test_counterfactual_release_stays_denied_by_production_strict_bootstrap(
        self,
    ):
        import os
        import shutil
        import subprocess
        import sys

        from tests.host.test_b05_sdk_bootstrap import BOOTSTRAP_CASE, SDKBootstrapTests

        explicit_wheel = os.environ.get("YGL_TEST_SDK_WHEEL")
        self.assertTrue(
            explicit_wheel,
            "current a6 strict control requires an explicit actual wheel",
        )
        wheel = Path(explicit_wheel)
        self.assertTrue(wheel.is_file())
        self.assertEqual(wheel.name, "yomihime_game_link_sdk-0.1.0a6-py3-none-any.whl")
        import hashlib

        self.assertEqual(
            hashlib.sha256(wheel.read_bytes()).hexdigest(),
            "54d5a0766ae5b626e575ff0d1936b49ce14a7d7f57b6db1f62882653eea9c81e",
        )
        SDKBootstrapTests.setUpClass()
        try:
            fixture = SDKBootstrapTests(
                "test_cold_and_identical_preload_work_under_arbitrary_plugin_names"
            )
            plugin = fixture._plugin("actual-a6-strict-control")
            fixture._run_bootstrap(plugin, "cold")
            # Official validated Host projection, never an SDK installation.
            with tempfile.TemporaryDirectory(prefix="strict-counter-") as work:
                selected = Path(work)
                shutil.copytree(
                    ROOT / "yomihime_game_link_sdk",
                    selected / "yomihime_game_link_sdk",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                )
                marker = selected / "yomihime_game_link_sdk/version.py"
                original = marker.read_bytes()
                changed = original.replace(b"0.1.0a6", b"0.1.0a7")
                self.assertNotEqual(changed, original)
                marker.write_bytes(changed)
                fixture._run_bootstrap(plugin, "different", selected)
            # Isolate the package release guard from origin/hash rejection:
            # real a6 canonical preloads, same validated plugin origin, only
            # the descriptive loaded package release marker becomes a7.
            probe = BOOTSTRAP_CASE.replace(
                '__version__ = "9.9.9"', '__version__ = "0.1.0a7"'
            )
            self.assertNotEqual(probe, BOOTSTRAP_CASE)
            rejected = subprocess.run(
                [sys.executable, "-I", "-B", "-c", probe, str(plugin), "unsupported"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf8",
                timeout=30,
            )
            self.assertEqual(rejected.returncode, 0, rejected.stderr)
        finally:
            SDKBootstrapTests.tearDownClass()
        self.trace.append(
            "V07_explicit_actual_a6_validated_Host_projection_current_positive_a7_source_and_release_marker_strict_denial_no_install"
        )

    async def test_parsed_module_owned_tool_all_output_statuses(self):
        from types import SimpleNamespace

        from ygl_test_subject.adapters.astrbot.runtime import (
            _AstrBotMessageIngress,
            _MessageReceipts,
        )
        from ygl_test_subject.services.core_runtime import _HostIngressAuthority

        from tests.host import test_message_context as controlled

        raw = document()
        raw["package_id"] = "version_tool"
        raw["modules"][0]["route"] = "versiontool"
        raw["modules"][0]["capabilities"][0]["invocation_policy"] = (
            "natural_language_allowed"
        )
        del raw["modules"][0]["capabilities"][0]["required_capabilities"]
        raw["modules"][0]["tools"] = [
            {
                "name": "version_tool",
                "capability_id": "read",
                "parameter_mapping": {},
                "description": "Read public version fixture.",
            }
        ]
        parsed = parse_manifest(json.dumps(raw).encode())
        subject = Handler()
        factory = Factory(subject)
        instance = await self.install(parsed, factory)
        self.assertEqual(len(factory.calls), 1)
        self.assertEqual(instance.starts, 1)
        with controlled.tool_contracts() as host:

            class Context(host.Context, controlled.legacy.fixture._Context):
                def __init__(self):
                    controlled.legacy.fixture._Context.__init__(self)
                    manager = host.Manager.__new__(host.Manager)
                    manager.func_list = []
                    self.provider_manager = SimpleNamespace(llm_tools=manager)

            context = Context()
            receipts = _MessageReceipts()
            owned = {}
            receiver = _AstrBotMessageIngress(
                object(), receipts, lambda handle: owned["host"].current_owner(handle)
            )
            authority = _HostIngressAuthority(
                receiver.validate,
                receiver.current,
                lambda: owned["host"].require_accepting(),
            )
            current = controlled._OfflineHost(
                self.runtime, context, receiver, authority, self.gateway, self.output
            )
            owned["host"] = current
            current.accepting = True
            try:
                current.publisher.publish()
                tool = context.provider_manager.llm_tools.get_full_tool_set().get_tool(
                    "version_tool"
                )
                self.assertIsNotNone(tool)
                for status in ygl.ResultStatus:
                    with self.subTest(status=status.value):
                        subject.result = original = result(status)
                        wrapper = controlled.legacy.LLMToolTests.wrapper(
                            SimpleNamespace(host=host, context=context),
                            "Read the public version fixture.",
                        )
                        before = len(subject.calls)
                        facts = json.loads(await tool.call(wrapper))
                        self.assertEqual(len(subject.calls), before + 1)
                        invocation = subject.calls[-1]
                        self.assertIs(invocation.origin, ygl.InvocationOrigin.LLM_TOOL)
                        self.assertEqual(
                            subject.bound.invocation.invocation_id,
                            invocation.invocation_id,
                        )
                        self.assertIs(type(subject.message), ygl.MessageContext)
                        self.assertEqual(
                            subject.message.text, "Read the public version fixture."
                        )
                        self.assertEqual(facts, dict(original.model_facts.facts))
                        self.assertEqual(facts["status"], status.value)
                        self.assertNotIn(subject.message.text, json.dumps(facts))
                        row = await self.runtime.database.executor.run_read(
                            lambda unit: tuple(
                                unit.execute(
                                    "SELECT state,output_outcome,receipt_status FROM b04_root_outputs WHERE root_invocation_id=?",
                                    (invocation.invocation_id,),
                                ).fetchone()
                            )
                        )
                        self.assertEqual(
                            row,
                            ("completed", RootOutputOutcome.TOOL_RETURNED.value, None),
                        )
                        self.assertFalse(self.runtime.issuer._issued)
                self.assertEqual(len(subject.calls), 4)
                self.assertFalse(self.messages.calls)
                self.assertFalse(self.runtime.transport.requests)
                current.publisher.revoke()
                with self.assertRaises(PermissionError):
                    await tool.call(wrapper)
                self.assertEqual(len(subject.calls), 4)
                self.trace.append(
                    "V03_parsed_neutral_SDK_Factory_Lifecycle_real_Host_receipt_owned_tool_issuer_admit_bind_Gateway_Output_all_four_statuses_claim_no_send_HTTP_revoke"
                )
            finally:
                current.accepting = False
                receipts.close()
                current.publisher.revoke()
                current.publisher.cleanup()
