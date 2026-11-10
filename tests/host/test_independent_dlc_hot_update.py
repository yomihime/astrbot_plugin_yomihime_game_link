"""Offline public-registry and real queued-endpoint contract tests."""

import asyncio
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from PIL import Image as PillowImage
from ygl_test_subject.adapters.astrbot.runtime import AstrBotRuntime
from ygl_test_subject.bootstrap.coordinator import get_coordinator
from ygl_test_subject.services.core_runtime import CoreRuntime
from ygl_test_subject.services.extension_discovery import ExtensionDiscovery
from ygl_test_subject.services.extension_transition import ExtensionControlEndpoint
from ygl_test_subject.services.external_packages import (
    IndependentPackageRuntime,
    _HotPackage,
)
from ygl_test_subject.services.runtime_effect_gate import RuntimeEffectGate
from ygl_test_subject.services.source_credentials import (
    CredentialInjection,
    SourceCredentialPolicy,
)

from yomihime_game_link_sdk import cards
from yomihime_game_link_sdk.declarations import (
    ModuleDisplay,
    PageResource,
    SourceDeclaration,
)
from yomihime_game_link_sdk.display import DisplayAudience, DisplayDocument, TextBlock
from yomihime_game_link_sdk.errors import InvalidInvocation
from yomihime_game_link_sdk.host import ExtensionHostError
from yomihime_game_link_sdk.services import HealthStatus


class IndependentSourceHealthTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.kernel = get_coordinator()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.scope = (self.temp.name, object())
        result = await self.kernel.acquire_core(self.scope, object(), 0, "a" * 64)
        self.token = result["owner_token"]
        self.addAsyncCleanup(self.close_scope)
        self.granted = True
        self.gate = RuntimeEffectGate(
            self.kernel,
            self.scope,
            self.token,
            1,
            package_id="unknown_dlc",
            package_token="b" * 64,
            package_epoch=1,
            authority_check=lambda: self.granted,
            policy_assert=lambda unit: None,
        )
        self.policy = SourceCredentialPolicy(
            "unknown_dlc/demo",
            "managed",
            "credential_api_key",
            "api.example.test",
            ("/data",),
            kind="static",
            injections=(CredentialInjection("/data", "GET", "header", "X-Key"),),
        )
        self.module = SimpleNamespace(
            module_id="demo",
            sources=(
                SourceDeclaration("public", "public.example.test"),
                SourceDeclaration("managed", "api.example.test"),
            ),
        )
        self.package = _HotPackage(
            installation=None,
            bundle=None,
            receipt=None,
            candidate=SimpleNamespace(
                package=SimpleNamespace(
                    manifest=SimpleNamespace(modules=(self.module,))
                )
            ),
            gate=self.gate,
            epoch=1,
            cache_key="test",
            policy=SimpleNamespace(credential_policies=(self.policy,)),
            instances={},
            lifetimes={},
            transition_id="test",
            published=True,
        )
        self.runtime = AstrBotRuntime.__new__(AstrBotRuntime)
        self.runtime._extension_host = None
        self.runtime._trusted_bundle = False
        self.runtime._assembly = None
        self.runtime._http_transport = SimpleNamespace(closed=False)
        self.runtime._transport_closed = False
        self.packages = IndependentPackageRuntime.__new__(IndependentPackageRuntime)
        self.packages._closed = False
        self.packages.packages = {"unknown_dlc": self.package}
        self.packages.core = SimpleNamespace(
            module_services=SimpleNamespace(_source_credential_policies=(self.policy,))
        )
        self.runtime._independent_packages = self.packages

    async def close_scope(self):
        self.gate.close()

        async def done():
            return True

        self.kernel.sync_fence(
            self.scope,
            self.token,
            done,
            done,
            lambda token, owner: dict(
                owner_token=token,
                tracked_inflight=0,
                cleanup_complete=True,
                durable_fence=True,
                resources_closed=True,
            ),
        )
        await self.kernel.await_quiet(self.scope, self.token)
        self.assertTrue(self.kernel.release_scope(self.scope, self.token))

    async def health(self, source="public", owner="unknown_dlc/demo"):
        return (await self.runtime._source_health(owner, source)).status

    async def test_public_and_managed_sources_need_no_secret_for_wiring_health(self):
        # No secret store is supplied. Actual credential errors belong to HTTP.
        self.assertIs(await self.health(), HealthStatus.AVAILABLE)
        self.assertIs(await self.health("managed"), HealthStatus.AVAILABLE)

    async def test_missing_or_mismatched_credential_policy_fails_closed(self):
        factory = self.packages.core.module_services
        factory._source_credential_policies = ()
        self.assertIs(await self.health("managed"), HealthStatus.UNAVAILABLE)
        self.assertIs(await self.health(), HealthStatus.AVAILABLE)
        factory._source_credential_policies = (self.policy,)
        self.module.sources = (SourceDeclaration("managed", "wrong.example.test"),)
        self.assertIs(await self.health("managed"), HealthStatus.UNAVAILABLE)

    async def test_unknown_package_module_source_and_direct_alias_fail_closed(self):
        for owner, source in (
            ("missing/demo", "public"),
            ("unknown_dlc/missing", "public"),
            ("unknown_dlc/demo", "missing"),
        ):
            with self.subTest(owner=owner, source=source):
                self.assertIs(
                    await self.health(source, owner), HealthStatus.UNAVAILABLE
                )
        self.module.sources = (SourceDeclaration("public", "api.example.test", "key"),)
        self.assertIs(await self.health(), HealthStatus.UNAVAILABLE)

    async def test_grant_revocation_and_closed_gate_fail_closed(self):
        self.granted = False
        self.assertIs(await self.health(), HealthStatus.UNAVAILABLE)
        self.granted = True
        self.assertIs(await self.health(), HealthStatus.AVAILABLE)
        self.gate.close()
        self.assertIs(await self.health(), HealthStatus.UNAVAILABLE)

    async def test_unpublished_closed_runtime_and_transport_fail_closed(self):
        self.package.published = False
        self.assertIs(await self.health(), HealthStatus.UNAVAILABLE)
        self.package.published = True
        self.packages._closed = True
        self.assertIs(await self.health(), HealthStatus.UNAVAILABLE)
        self.packages._closed = False
        self.runtime._http_transport.closed = True
        self.assertIs(await self.health(), HealthStatus.UNAVAILABLE)
        self.runtime._http_transport.closed = False
        self.runtime._transport_closed = True
        self.assertIs(await self.health(), HealthStatus.UNAVAILABLE)

    async def test_legacy_source_readiness_still_uses_owned_transport(self):
        del self.runtime._independent_packages
        self.runtime._extension_host = SimpleNamespace(source_ready=lambda *_: True)
        self.assertIs(await self.health(), HealthStatus.AVAILABLE)
        self.runtime._http_transport = None
        self.assertIs(await self.health(), HealthStatus.UNAVAILABLE)

    async def test_non_hot_bundled_source_readiness_without_independent_runtime(self):
        self.runtime = AstrBotRuntime(
            SimpleNamespace(),
            plugin_root=Path(self.temp.name),
            data_dir=Path(self.temp.name) / "data",
            font_manager_factory=lambda root: SimpleNamespace(provider=lambda: None),
        )
        self.assertFalse(hasattr(self.runtime, "_independent_packages"))
        self.runtime._http_transport = SimpleNamespace(closed=False)
        self.runtime._trusted_bundle = True
        self.runtime._assembly = SimpleNamespace(
            module_id="bundled/demo",
            source_hosts={"public": "public.example.test"},
            credential_policies=(),
        )
        self.runtime._source_credential_policies = ()
        module = SimpleNamespace(manifest=self.module)
        self.runtime._core = SimpleNamespace(
            registry=SimpleNamespace(
                snapshot=lambda: SimpleNamespace(module=lambda owner: module)
            )
        )
        self.assertIs(await self.health(owner="bundled/demo"), HealthStatus.AVAILABLE)
        self.assertIs(
            await self.health("missing", "bundled/demo"), HealthStatus.UNAVAILABLE
        )

    def logo_core(self):
        with io.BytesIO() as buffer:
            with PillowImage.new("RGB", (8, 8), "red") as logo:
                logo.save(buffer, format="PNG")
            self.logo_bytes = buffer.getvalue()
        self.module.display = ModuleDisplay("Demo", logo_resource="assets/logo.png")
        self.module.resources = (
            PageResource(
                "assets/logo.png", hashlib.sha256(self.logo_bytes).hexdigest()
            ),
        )

        def resource(path):
            self.assertEqual(path, "package/assets/logo.png")
            return self.logo_bytes

        self.package.bundle = SimpleNamespace(
            resource_root=Path(self.temp.name), read_resource=resource
        )
        self.package.candidate.package.package_dir = Path(self.temp.name) / "package"
        self.package.candidate.package._provenance = None
        registered = SimpleNamespace(manifest=self.module, epoch=1)
        self.current_module = registered
        core = CoreRuntime.__new__(CoreRuntime)
        core.registry = SimpleNamespace(
            snapshot=lambda: SimpleNamespace(module=lambda owner: self.current_module),
            is_active=lambda row: row is self.current_module,
        )
        core.extension_runtime = SimpleNamespace(
            candidate=lambda owner: self.package.candidate
        )
        core._command_card_resource_reader = self.runtime._read_command_card_resource
        return core

    async def test_independent_logo_renders_declared_card_png(self):
        from ygl_test_subject.presentation.command_card import CommandCardRenderer
        from ygl_test_subject.presentation.png_validation import validate_png

        from tests.presentation.test_layout_renderer import Font

        core = self.logo_core()
        logo = await core._command_card_logo("unknown_dlc/demo")
        self.assertEqual(logo.content, self.logo_bytes)
        document = DisplayDocument(
            "Demo",
            "Result",
            (TextBlock("Complete app details"),),
            card=cards.Document(
                "Demo",
                (cards.Text("Complete app details"),),
                brand="GAME LINK",
                logo=logo,
            ),
        )
        presentation = CommandCardRenderer(font_provider_factory=Font)(
            document, audience=DisplayAudience.PUBLIC
        )
        self.assertIsNotNone(presentation.bitmap)
        validate_png(presentation.bitmap)
        self.assertIn("Complete app details", presentation.text)

    async def test_independent_logo_rejects_revoked_gate_and_missing_reader(self):
        core = self.logo_core()
        self.granted = False
        with self.assertRaises(ExtensionHostError):
            await core._command_card_logo("unknown_dlc/demo")
        self.granted = True
        core._command_card_resource_reader = None
        with self.assertRaises(ValueError):
            await core._command_card_logo("unknown_dlc/demo")

    async def test_independent_logo_rejects_manifest_unknown_resource_and_bad_hash(
        self,
    ):
        core = self.logo_core()
        with self.assertRaises(ExtensionHostError):
            self.packages.read_module_resource(
                "unknown_dlc/demo", SimpleNamespace(module_id="demo"), "assets/logo.png"
            )
        with self.assertRaises(ExtensionHostError):
            self.packages.read_module_resource(
                "unknown_dlc/demo", self.module, "unknown.png"
            )
        self.package.bundle.read_resource = lambda path: b"corrupt"
        with self.assertRaises(ExtensionHostError):
            await core._command_card_logo("unknown_dlc/demo")

    async def test_independent_logo_rechecks_package_and_gate_after_read(self):
        core = self.logo_core()

        def revoked(path):
            self.granted = False
            return self.logo_bytes

        self.package.bundle.read_resource = revoked
        with self.assertRaises(ExtensionHostError):
            await core._command_card_logo("unknown_dlc/demo")
        self.granted = True

        def replaced(path):
            self.packages.packages["unknown_dlc"] = object()
            return self.logo_bytes

        self.package.bundle.read_resource = replaced
        with self.assertRaises(ExtensionHostError):
            await core._command_card_logo("unknown_dlc/demo")

    async def test_logo_rechecks_registry_generation_and_reader_hash(self):
        core = self.logo_core()

        async def changed(module_id, manifest, path):
            content = await self.runtime._read_command_card_resource(
                module_id, manifest, path
            )
            self.current_module = SimpleNamespace(manifest=self.module, epoch=2)
            return content

        core._command_card_resource_reader = changed
        with self.assertRaises(InvalidInvocation):
            await core._command_card_logo("unknown_dlc/demo")

        async def corrupt(module_id, manifest, path):
            return b"corrupt"

        core._command_card_resource_reader = corrupt
        with self.assertRaises(ValueError):
            await core._command_card_logo("unknown_dlc/demo")

    async def test_bundled_and_legacy_logo_keep_provenance_capture(self):
        core = self.logo_core()
        provenance = object()
        self.package.candidate.package._provenance = provenance

        async def forbidden(*args):
            raise AssertionError(
                "legacy capture must not use independent resource reader"
            )

        core._command_card_resource_reader = forbidden
        with patch(
            "ygl_test_subject.extensions.page_resources.capture_page_resources",
            return_value={"assets/logo.png": self.logo_bytes},
        ) as capture:
            logo = await core._command_card_logo("unknown_dlc/demo")
        capture.assert_called_once_with(
            provenance, self.package.candidate.package.manifest
        )
        self.assertEqual(logo.content, self.logo_bytes)


class LogicalArtifactPathsTest(unittest.TestCase):
    def test_manifest_business_root_remains_posix_on_windows(self):
        from pathlib import PureWindowsPath
        from unittest.mock import patch

        from ygl_test_subject.services import external_packages

        logical = "packages/unknown/manifest.json"
        self.assertEqual(str(PureWindowsPath(logical).parent), "packages\\unknown")
        with patch.object(external_packages, "Path", PureWindowsPath):
            self.assertEqual(
                external_packages._manifest_business_root(logical), "packages/unknown"
            )


class IndependentDiscoveryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.registry = []
        self.context = SimpleNamespace(get_all_stars=lambda: self.registry)
        self.discovery = ExtensionDiscovery(self.context)
        self.started, self.fenced, self.removed = [], [], []
        self.allow = asyncio.Event()
        self.allow.set()

        async def activate(installation):
            self.started.append(installation.package_id)
            await self.allow.wait()
            return {
                "state": "active",
                "package_epoch": 1,
                "installed_version": "1.0.0",
                "running_version": "1.0.0",
            }

        async def withdraw(instance):
            self.removed.append(instance)

        self.endpoint = ExtensionControlEndpoint(
            self.discovery,
            core_epoch=1,
            activate=activate,
            withdraw=withdraw,
            fence=self.fenced.append,
        )
        self.addAsyncCleanup(self.endpoint.close)

    def install(self, package_id, name=None):
        name = name or package_id
        root = Path(self.temp.name) / name
        root.mkdir()
        (root / "main.py").write_text("")
        raw = json.dumps(
            {
                "schema": "ygl.plugin/1",
                "role": "dlc",
                "package_id": package_id,
                "manifest": "package/manifest.json",
                "control_protocol": {"name": "ygl.control", "major": 1},
            }
        ).encode()
        (root / "ygl-plugin.json").write_bytes(raw)
        instance = object()
        metadata = SimpleNamespace(
            name=name,
            activated=True,
            star_cls=instance,
            root_dir_name=name,
            module=SimpleNamespace(__file__=str(root / "main.py")),
        )
        self.registry.append(metadata)
        return instance, hashlib.sha256(raw).hexdigest(), metadata

    async def connect(self, instance, digest, request_id=None):
        return await self.endpoint.request(
            instance,
            {
                "protocol": "ygl.control/1",
                "op": "connect",
                "request_id": request_id or str(uuid4()),
                "descriptor_digest": digest,
            },
        )

    async def status(self, instance, operation):
        return await self.endpoint.request(
            instance,
            {"protocol": "ygl.control/1", "op": "status", "operation_id": operation},
        )

    async def test_unknown_packages_replay_conflict_and_sibling_isolation(self):
        a, digest, _ = self.install("never_predeclared")
        b, bd, _ = self.install("another_unknown")
        request_id = str(uuid4())
        queued = await self.connect(a, digest, request_id)
        self.assertEqual(queued["state"], "queued")
        replay = await self.connect(a, digest, request_id)
        self.assertEqual(queued["operation_id"], replay["operation_id"])
        conflict = await self.connect(a, "0" * 64, request_id)
        self.assertEqual(conflict["error"]["code"], "request_conflict")
        qb = await self.connect(b, bd)
        await asyncio.sleep(0)
        sa = await self.status(a, queued["operation_id"])
        sb = await self.status(b, qb["operation_id"])
        self.assertEqual(sa["state"], "active")
        self.assertEqual(sb["state"], "active")
        self.assertEqual(self.started.count("never_predeclared"), 1)
        await self.endpoint.request(
            a, {"protocol": "ygl.control/1", "op": "disconnect", "lease": sa["lease"]}
        )
        self.assertEqual(self.fenced, [a])
        self.assertEqual((await self.status(b, qb["operation_id"]))["state"], "active")
        self.assertEqual([x.instance for x in self.discovery.scan()], [b])

    async def test_fresh_retries_share_lease_without_exhausting_capacity(self):
        self.endpoint.capacity = 4
        instance, digest, metadata = self.install("retry")
        leases = set()
        for _ in range(12):
            queued = await self.connect(instance, digest)
            self.assertTrue(queued["ok"])
            await asyncio.sleep(0)
            status = await self.status(instance, queued["operation_id"])
            self.assertEqual(status["state"], "active")
            leases.add(status["lease"])
        self.assertEqual(len(leases), 1)
        self.endpoint.retire_instance(instance)
        self.assertEqual(
            (await self.status(instance, queued["operation_id"]))["state"], "disabled"
        )
        self.registry.remove(metadata)
        successor, successor_digest, _ = self.install("retry", "new")
        queued = await self.connect(successor, successor_digest)
        self.assertTrue(queued["ok"])
        await asyncio.sleep(0)
        status = await self.status(successor, queued["operation_id"])
        self.assertEqual(status["state"], "active")
        await self.endpoint.request(
            instance,
            {
                "protocol": "ygl.control/1",
                "op": "disconnect",
                "lease": next(iter(leases)),
            },
        )
        self.assertNotIn(successor, self.fenced)

    async def test_duplicate_identity_and_stale_instance_fail_closed(self):
        old, digest, old_meta = self.install("duplicate", "old")
        new, _, _ = self.install("duplicate", "new")
        self.assertEqual(self.discovery.scan(), ())
        self.assertEqual(self.discovery.rejections["duplicate"], "owner_conflict")
        self.registry.remove(old_meta)
        self.assertEqual(
            (await self.connect(old, digest))["error"]["code"], "stale_instance"
        )
        self.discovery.tombstone(old)
        self.assertIs(self.discovery.scan()[0].instance, new)

    async def test_prepare_revalidates_registry_and_never_publishes_disabled(self):
        instance, digest, metadata = self.install("pending")
        self.allow.clear()
        queued = await self.connect(instance, digest)
        await asyncio.sleep(0)
        metadata.activated = False
        self.allow.set()
        await asyncio.sleep(0)
        result = await self.status(instance, queued["operation_id"])
        self.assertEqual(result["error"]["code"], "stale_instance")
        self.assertIn(instance, self.fenced)
        self.assertIn(instance, self.removed)

    async def test_disconnect_old_lease_cannot_revoke_new_instance(self):
        old, digest, metadata = self.install("replace")
        qo = await self.connect(old, digest)
        await asyncio.sleep(0)
        old_status = await self.status(old, qo["operation_id"])
        self.registry.remove(metadata)
        new, nd, _ = self.install("replace", "replacement")
        qn = await self.connect(new, nd)
        await asyncio.sleep(0)
        await self.endpoint.request(
            new,
            {
                "protocol": "ygl.control/1",
                "op": "disconnect",
                "lease": old_status["lease"],
            },
        )
        self.assertNotIn(new, self.fenced)
        self.assertEqual(
            (await self.status(new, qn["operation_id"]))["state"], "active"
        )
        await self.endpoint.request(
            old,
            {
                "protocol": "ygl.control/1",
                "op": "disconnect",
                "lease": old_status["lease"],
            },
        )
        self.assertEqual(self.fenced, [old])
        self.assertIs(self.discovery.scan()[0].instance, new)

    async def test_repeated_close_cancels_activation_once_and_retains_withdrawal(self):
        active, digest, _ = self.install("close_active")
        ready = await self.connect(active, digest)
        await asyncio.sleep(0)
        self.assertEqual(
            (await self.status(active, ready["operation_id"]))["state"], "active"
        )
        blocked, digest, _ = self.install("close_preparing")
        self.allow.clear()
        queued = await self.connect(blocked, digest)
        await asyncio.sleep(0)
        activation = self.endpoint._operations[queued["operation_id"]].task
        entered, release = asyncio.Event(), asyncio.Event()
        cancelled = []

        async def withdraw(instance):
            self.assertIs(instance, active)
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
            self.removed.append(instance)

        self.endpoint.withdraw = withdraw
        self.addCleanup(release.set)
        observer = asyncio.create_task(self.endpoint.close())
        await asyncio.wait_for(entered.wait(), 1)
        actual = self.endpoint._operations[ready["operation_id"]].task
        observer.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await observer
        second = asyncio.create_task(self.endpoint.close())
        await asyncio.sleep(0)
        self.assertIs(self.endpoint._operations[ready["operation_id"]].task, actual)
        self.assertFalse(actual.done())
        self.assertTrue(activation.cancelled())
        self.assertEqual(activation.cancelling(), 1)
        self.assertEqual(cancelled, [])
        release.set()
        await asyncio.wait_for(second, 1)
        self.assertEqual(self.removed, [active])

    async def test_cancellation_does_not_cancel_queued_operation(self):
        instance, digest, _ = self.install("cancel")
        self.allow.clear()
        queued = await self.connect(instance, digest)
        await asyncio.sleep(0)
        self.assertEqual(
            (await self.status(instance, queued["operation_id"]))["state"], "preparing"
        )
        self.allow.set()
        await asyncio.sleep(0)
        self.assertEqual(
            (await self.status(instance, queued["operation_id"]))["state"], "active"
        )

    async def test_json_only_and_bounded_pending_queue(self):
        instance, digest, _ = self.install("bound")
        self.endpoint.capacity = 1
        self.allow.clear()
        await self.connect(instance, digest)
        self.assertEqual(
            (await self.connect(instance, digest))["error"]["code"], "busy"
        )
        result = await self.endpoint.request(
            instance, {"protocol": "ygl.control/1", "op": "describe", "obj": object()}
        )
        self.assertFalse(result["ok"])
        self.allow.set()
