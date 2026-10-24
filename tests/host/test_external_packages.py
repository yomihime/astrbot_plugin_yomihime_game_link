"""Real Core, SQLite, safe disk capture and SDK factories; no Host or network."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import unittest
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from time import time
from types import SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.adapters.astrbot.bundled import DEFAULT_BUNDLE_SELECTION
from ygl_test_subject.adapters.astrbot.public_pages import PublicPages
from ygl_test_subject.adapters.astrbot.runtime import PLUGIN_NAME, AstrBotRuntime
from ygl_test_subject.adapters.astrbot.web_public import WebPublicRejected
from ygl_test_subject.core.contracts.administration import AdminOperation
from ygl_test_subject.extensions.static_page_projection import StaticPageProjection
from ygl_test_subject.infrastructure.private_storage import resolve_private_storage
from ygl_test_subject.infrastructure.windows.private_storage import (
    create_private_directory,
    identity_private_base,
)
from ygl_test_subject.services.extension_runtime import ExtensionCleanupPending

from tests.host.test_ff14_pages import _host_contracts, _request
from yomihime_game_link_sdk.host import ExtensionHostError
from yomihime_game_link_sdk.services import HealthStatus

ROOT = Path(__file__).resolve().parents[2]


class _Font:
    def __init__(self, _root):
        pass

    def provider(self):
        return None

    def start(self):
        pass

    async def close(self):
        pass


class _Transport:
    closed = False

    async def request(self, _request):
        raise AssertionError("offline test must not request a network resource")

    request_credential_exchange = request

    async def close(self):
        self.closed = True


class _Context:
    def __init__(self):
        self.stars = {}

    def get_registered_star(self, name):
        return self.stars.get(name)

    async def send_message(self, *_args):
        return True


SOURCE = """from yomihime_game_link_sdk.services import ModuleHandlers, HealthReport
from .shared import VALUE
class Instance:
    def handlers(self): return ModuleHandlers(capabilities={}, collectors={}, evaluators={})
    async def start(self): self.started = VALUE
    async def stop(self): self.started = None
    async def check_health(self): return HealthReport(capabilities={})
class Factory:
    async def create(self, services):
        self.services = services
        return Instance()
"""


@unittest.skipUnless(os.name == "nt", "native Windows capture and private storage")
class ExternalPackageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = create_private_directory(
            identity_private_base(), "ygl-external-test-" + uuid.uuid4().hex
        )
        self.addCleanup(shutil.rmtree, self.root)
        self.installation = self.root / "dlc"
        self.package = self.installation / "packages" / "steam_dlc"
        self.package.mkdir(parents=True)
        (self.installation / "main.py").write_text("# outer plugin\n", encoding="utf-8")
        self.owner = object()
        self.context = _Context()
        self.metadata = SimpleNamespace(
            name="external_dlc",
            activated=True,
            star_cls=self.owner,
            module=SimpleNamespace(__file__=str(self.installation / "main.py")),
        )
        self.context.stars[self.metadata.name] = self.metadata
        self.manifest = {
            "schema_version": 1,
            "package_id": "steam_dlc",
            "package_version": "1.0.0",
            "contract_version": "2.0",
            "author": "test",
            "license": "MIT",
            "source": "https://example.invalid/test",
            "modules": [
                {
                    "module_id": name,
                    "route": name,
                    "category": "platform",
                    "factory_entry": "module:Factory",
                    "module_version": "1.0.0",
                    "capabilities": [],
                    "commands": [],
                    "sources": [],
                }
                for name in ("steam", "dota2", "cs2", "deadlock")
            ],
        }
        self._write_manifest()
        (self.package / "module.py").write_text(SOURCE, encoding="utf-8")
        (self.package / "shared.py").write_text("VALUE = 17\n", encoding="utf-8")
        (self.root / "core-data").mkdir()
        self.runtime = AstrBotRuntime(
            self.context,
            plugin_root=ROOT,
            data_dir=self.root / "core-data",
            font_manager_factory=_Font,
            http_transport_factory=_Transport,
            bundled_selection=None,
            config={"web_public_origin": "https://ui.test"},
        )
        self.addAsyncCleanup(self.runtime.terminate)
        self.key_patch = patch(
            "ygl_test_subject.adapters.astrbot.runtime.EnvironmentKeyProvider.get_key",
            return_value=bytes(range(32)),
        )
        self.key_patch.start()
        self.addCleanup(self.key_patch.stop)
        private = create_private_directory(self.root, "private-storage")
        self.storage_patch = patch(
            "ygl_test_subject.adapters.astrbot.runtime.resolve_private_storage",
            lambda path: resolve_private_storage(path, private_base=private),
        )
        self.storage_patch.start()
        self.addCleanup(self.storage_patch.stop)
        await self.runtime.initialize()
        self.host = self.runtime.extension_host
        self.assertIsNotNone(self.host)
        self.core = self.runtime.core_runtime

    def _write_manifest(self):
        (self.package / "yomihime.manifest.json").write_text(
            json.dumps(self.manifest), encoding="utf-8"
        )

    async def _register(self):
        return await self.host.register_package(
            plugin_name=self.metadata.name,
            plugin_instance=self.metadata.star_cls,
            package_root=str(self.package),
        )

    def _subscription_fixture(self):
        module = self.manifest["modules"][1]
        module["sources"] = [{"source_id": "source", "host": "example.test"}]
        module["config_fields"] = [
            {
                "name": "subscription_enabled",
                "default": True,
                "value_schema": {"type": "boolean"},
            },
            {
                "name": "poll_interval_seconds",
                "default": 300,
                "value_schema": {"type": "integer", "minimum": 60},
            },
        ]
        schema = {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }
        module["schedules"] = [
            {
                "collector_id": "watch",
                "key_version": 1,
                "source_id": "source",
                "data_version": 1,
                "input_schema": schema,
                "shared_scope": "public",
                "trigger": "periodic",
                "minimum_interval_seconds": 60,
                "default_interval_seconds": 300,
                "interval_config_key": "poll_interval_seconds",
            }
        ]
        module["subscriptions"] = [
            {
                "type_id": "watch",
                "collector_id": "watch",
                "matcher_id": "watch",
                "filter_schema": schema,
                "notification_modes": ["instant"],
                "enabled_config_key": "subscription_enabled",
            }
        ]
        self._write_manifest()
        source = (
            SOURCE.replace(
                "return ModuleHandlers(capabilities={}, collectors={}, evaluators={})",
                'return ModuleHandlers(capabilities={}, collectors={"watch": self} '
                'if self.owner == "steam_dlc/dota2" else {}, evaluators={"watch": self} '
                'if self.owner == "steam_dlc/dota2" else {})',
            )
            .replace(
                "class Instance:\n",
                "class Instance:\n"
                "    def __init__(self, owner): self.owner = owner\n"
                "    def normalize(self, value): raise AssertionError('not collected')\n"
                "    async def collect(self, request): raise AssertionError('not collected')\n"
                "    def evaluate(self, request): raise AssertionError('not evaluated')\n",
            )
            .replace(
                "return Instance()",
                "return Instance((await services.config.current()).target.module_id)",
            )
        )
        # The factory receives real scoped services; its module identity is fixed
        # by the existing config target, rather than by test ambient authority.
        (self.package / "module.py").write_text(source, encoding="utf-8")

    @asynccontextmanager
    async def _subscription_pump_barrier(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.core._register_declared_subscription_gates

        async def guarded(manifests):
            if asyncio.current_task() is self.core._pump_task:
                entered.set()
                await release.wait()
            await original(manifests)

        self.core._pump_task.cancel()
        await asyncio.gather(self.core._pump_task, return_exceptions=True)
        with patch.object(self.core, "_register_declared_subscription_gates", guarded):
            self.core._pump_task = asyncio.create_task(self.core._pump_loop())
            await entered.wait()
            try:
                yield
            finally:
                self.core._pump_task.cancel()
                await asyncio.gather(self.core._pump_task, return_exceptions=True)
                release.set()

    def _subscription_sql(self, sql):
        connection = self.core.database.connect()
        try:
            rows = [tuple(row) for row in connection.execute(sql).fetchall()]
            connection.commit()
            return rows
        finally:
            connection.close()

    async def test_external_register_has_first_subscription_fence_without_pump(self):
        self._subscription_fixture()
        async with self._subscription_pump_barrier():
            lease = await self._register()
            self.assertTrue(lease._complete)
            self.assertIsNotNone(
                await self.core.b04_repositories.lifecycle.current_fence(
                    "steam_dlc/dota2"
                )
            )
            binding = self.core.b04_repositories.lifecycle._subscription_gate_bindings[
                "steam_dlc/dota2"
            ]
            self.assertTrue(binding.declared)
            self.assertFalse(self.core._trusted_subscription_gates)
            self.assertEqual(
                self._subscription_sql("SELECT phase FROM subscription_gate_bootstrap"),
                [("pending",)],
            )

    async def test_external_reregister_preserves_false_and_missing_marker(self):
        self._subscription_fixture()
        async with self._subscription_pump_barrier():
            lease = await self._register()
            self._subscription_sql(
                "UPDATE config_entries SET value_json='false' "
                "WHERE module_id='steam_dlc/dota2' AND field='subscription_enabled'"
            )
            await self.host.unregister_package(lease)
            lease = await self._register()
            self.assertIsNone(
                await self.core.b04_repositories.lifecycle.current_fence(
                    "steam_dlc/dota2"
                )
            )
            self.assertEqual(
                self._subscription_sql(
                    "SELECT value_json FROM config_entries "
                    "WHERE module_id='steam_dlc/dota2' AND field='subscription_enabled'"
                ),
                [("false",)],
            )
            self._subscription_sql(
                "UPDATE config_entries SET value_json='true' "
                "WHERE module_id='steam_dlc/dota2' AND field='subscription_enabled'"
            )
            self._subscription_sql(
                "DELETE FROM b04_declared_subscription_gates "
                "WHERE module_id='steam_dlc/dota2'"
            )
            await self.host.unregister_package(lease)
            await self._register()
            self.assertIsNone(
                await self.core.b04_repositories.lifecycle.current_fence(
                    "steam_dlc/dota2"
                )
            )
            self.assertEqual(
                self._subscription_sql(
                    "SELECT COUNT(*) FROM b04_declared_subscription_gates "
                    "WHERE module_id='steam_dlc/dota2'"
                ),
                [(0,)],
            )

    async def test_external_register_preserves_disabled_intent(self):
        self._subscription_fixture()
        await self.core.runtime_repository.seed_default_enabled_if_absent(
            "steam_dlc",
            "dota2",
            expected_registry_revision=self.core.registry.snapshot().revision,
        )
        self._subscription_sql(
            "UPDATE module_runtime_intents SET desired_enabled=0 "
            "WHERE package_id='steam_dlc' AND module_id='dota2'"
        )
        async with self._subscription_pump_barrier():
            await self._register()
            intent = await self.core.runtime_repository.current_intent(
                "steam_dlc", "dota2"
            )
            self.assertFalse(intent.desired_enabled)
            self.assertIsNone(
                await self.core.b04_repositories.lifecycle.current_fence(
                    "steam_dlc/dota2"
                )
            )
            self.assertEqual(
                self._subscription_sql(
                    "SELECT value_json FROM config_entries "
                    "WHERE module_id='steam_dlc/dota2' AND field='subscription_enabled'"
                ),
                [("true",)],
            )
            module = self.core.registry.snapshot().modules.get("steam_dlc/dota2")
            self.assertTrue(module is None or not module.enabled)

    def _public_page_fixture(self):
        example = ROOT / "examples/modules/minimal"
        self.manifest = json.loads((example / "yomihime.manifest.json").read_bytes())
        self.manifest["package_id"] = "steam_dlc"
        self._write_manifest()
        shutil.copyfile(example / "module.py", self.package / "module.py")
        shutil.copytree(example / "pages", self.package / "pages")
        self.runtime._static_page_projection = StaticPageProjection(
            ((self.package / "yomihime.manifest.json").read_bytes(),)
        )
        return ("steam_dlc/demo", "home", "status")

    async def _public_call(
        self, endpoint, *, parameters=None, origin="https://ui.test"
    ):
        web, _ = _host_contracts()
        context = SimpleNamespace(register_web_api=lambda *_args: None)
        pages = PublicPages(context, self.runtime)
        pages.register()
        self.addCleanup(pages.close)
        request = _request(
            web,
            path=f"/api/plug/{PLUGIN_NAME}/invoke",
            origin=origin,
            body=dict(
                zip(("owner", "page", "capability_id"), endpoint),
                parameters={} if parameters is None else parameters,
            ),
        )
        with (
            patch.dict(sys.modules, {"astrbot.api.web": web}),
            web.bind_request_context(request),
        ):
            return await next(
                handler
                for route, handler in pages._registrations
                if route == f"/{PLUGIN_NAME}/invoke"
            )()

    async def test_external_public_page_real_http_without_bundled_deployment(self):
        endpoint = self._public_page_fixture()
        lease = await self._register()
        self.assertTrue(lease.active)
        response = await self._public_call(endpoint)
        self.assertEqual(response.status_code, 200, response.body)
        result = json.loads(response.body)["data"]
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["model_facts"], {"sample": "minimal", "ready": True})

    async def test_external_public_page_has_explicit_core_deployment(self):
        endpoint = self._public_page_fixture()
        await self._register()
        module = self.core.registry.snapshot().module(endpoint[0])
        self.assertTrue(
            self.core.issuer.allows_public_web(
                endpoint[0], module.manifest.capabilities[0]
            )
        )

    def _public_begin(self, endpoint):
        web, _ = _host_contracts()
        request = _request(web, path=f"/api/plug/{PLUGIN_NAME}/invoke")
        return self.runtime.begin_public_web(request, endpoint, request.path)

    async def test_external_public_page_selectors_projection_and_origin_fail_closed(
        self,
    ):
        endpoint = self._public_page_fixture()
        await self._register()
        for target in (
            (endpoint[0], "missing", endpoint[2]),
            (endpoint[0], endpoint[1], "undeclared"),
            ("foreign/demo", endpoint[1], endpoint[2]),
        ):
            response = await self._public_call(target)
            self.assertEqual(response.status_code, 400)
        self.assertEqual(
            (await self._public_call(endpoint, origin="https://else.test")).status_code,
            403,
        )
        bad_parameters = await self._public_call(endpoint, parameters={"extra": True})
        self.assertEqual(json.loads(bad_parameters.body)["data"]["status"], "error")
        state = self._public_begin(endpoint)
        self.runtime._static_page_projection = StaticPageProjection()
        self.runtime._refresh_external_public_web_bindings()
        with self.assertRaises(WebPublicRejected):
            self.runtime.public_web_remaining(state)
        self.assertEqual((await self._public_call(endpoint)).status_code, 400)
        module = self.core.registry.snapshot().module(endpoint[0])
        self.assertFalse(
            self.core.issuer.allows_public_web(
                endpoint[0], module.manifest.capabilities[0]
            )
        )

    async def test_external_public_capability_without_page_is_not_deployed(self):
        endpoint = self._public_page_fixture()
        self.manifest["modules"][0]["pages"][0].pop("capability_id")
        self._write_manifest()
        self.runtime._static_page_projection = StaticPageProjection(
            ((self.package / "yomihime.manifest.json").read_bytes(),)
        )
        await self._register()
        self.assertEqual((await self._public_call(endpoint)).status_code, 400)
        self.assertFalse(self.host.public_web_page_bindings())

    async def test_external_page_rejects_undeclared_private_write_and_command_only(
        self,
    ):
        self._public_page_fixture()
        original = json.dumps(self.manifest)
        cases = (
            {"capability_id": "missing"},
            {"invocation_policy": "command_only", "invocation_origins": ["command"]},
            {"privacy_floor": "private"},
            {"privacy_floor": "owner"},
            {"effect": "write"},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                self.manifest = json.loads(original)
                self.manifest["modules"][0]["capabilities"][0].update(changes)
                self._write_manifest()
                with self.assertRaisesRegex(
                    ExtensionHostError, "package_manifest_invalid"
                ):
                    await self._register()
                self.assertFalse(self.host._packages)
                self.assertFalse(self.runtime._web_validator._bindings)

    async def test_external_public_page_exact_lease_idempotency_replacement_and_restart(
        self,
    ):
        endpoint = self._public_page_fixture()
        first = await self._register()
        state = self._public_begin(endpoint)
        self.assertIs(await self._register(), first)
        self.runtime.public_web_remaining(state)
        self.metadata.star_cls = object()
        second = await self._register()
        self.assertIsNot(first, second)
        with self.assertRaises(WebPublicRejected):
            self.runtime.public_web_remaining(state)
        self.assertEqual((await self._public_call(endpoint)).status_code, 200)
        next_state = self._public_begin(endpoint)
        await self.runtime.terminate()
        await self.runtime.initialize()
        self.host, self.core = self.runtime.extension_host, self.runtime.core_runtime
        await self._register()
        with self.assertRaises(WebPublicRejected):
            self.runtime.public_web_remaining(next_state)
        self.assertEqual((await self._public_call(endpoint)).status_code, 200)

    async def test_external_public_withdraw_fences_before_async_cleanup(self):
        endpoint = self._public_page_fixture()
        lease = await self._register()
        state = self._public_begin(endpoint)
        entered, release = asyncio.Event(), asyncio.Event()
        detach = self.core.extension_runtime.detach_host_package

        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
            return await detach(*args, **kwargs)

        with patch.object(self.core.extension_runtime, "detach_host_package", delayed):
            task = asyncio.create_task(self.host.unregister_package(lease))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                self.assertFalse(lease.active)
                with self.assertRaises(WebPublicRejected):
                    self.runtime.public_web_remaining(state)
                self.assertEqual((await self._public_call(endpoint)).status_code, 503)
            finally:
                release.set()
                await task
        self.assertEqual((await self._public_call(endpoint)).status_code, 400)

    async def test_external_public_handler_revocation_and_cancellation_do_not_publish(
        self,
    ):
        endpoint = self._public_page_fixture()
        await self._register()
        handler = self.core.lifecycle.handlers(endpoint[0]).capabilities[endpoint[2]]
        invoke = handler.invoke
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                self.metadata.activated = True
                entered, release, finished = (
                    asyncio.Event(),
                    asyncio.Event(),
                    asyncio.Event(),
                )

                async def delayed(view, parameters):
                    entered.set()
                    try:
                        await release.wait()
                        return await invoke(view, parameters)
                    finally:
                        finished.set()

                with patch.object(handler, "invoke", delayed):
                    task = asyncio.create_task(self._public_call(endpoint))
                    try:
                        await asyncio.wait_for(entered.wait(), 2)
                        if cancel:
                            task.cancel()
                            with self.assertRaises(asyncio.CancelledError):
                                await task
                        else:
                            self.metadata.activated = False
                            release.set()
                            self.assertEqual((await task).status_code, 503)
                    finally:
                        release.set()
                        if not task.done():
                            task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        await asyncio.wait_for(finished.wait(), 2)
                self.assertTrue(await self.core.gateway.drain_public_web(2))
                self.assertFalse(self.core.gateway.public_web_pending)

    async def test_external_public_final_host_fence_before_and_after_projection(self):
        from ygl_test_subject.adapters.astrbot import runtime as runtime_module

        endpoint = self._public_page_fixture()
        await self._register()
        invoke = self.core.invoke_public_web
        project = runtime_module.project_result
        for phase in ("returned", "projected"):
            with self.subTest(phase=phase):
                self.metadata.activated = True

                async def returned(*args, **kwargs):
                    result = await invoke(*args, **kwargs)
                    self.metadata.activated = False
                    return result

                def projected(result):
                    data = project(result)
                    self.metadata.activated = False
                    return data

                target, attribute, replacement = (
                    (self.core, "invoke_public_web", returned)
                    if phase == "returned"
                    else (runtime_module, "project_result", projected)
                )
                with patch.object(target, attribute, replacement):
                    response = await self._public_call(endpoint)
                self.assertEqual(response.status_code, 503)

    async def test_external_public_page_preserves_bundled_web_deployment(self):
        await self.runtime.terminate()
        (self.root / "bundled-data").mkdir()
        runtime = AstrBotRuntime(
            self.context,
            plugin_root=self._ff14_fixture(),
            data_dir=self.root / "bundled-data",
            font_manager_factory=_Font,
            http_transport_factory=_Transport,
            bundled_selection=DEFAULT_BUNDLE_SELECTION,
            config={"web_public_origin": "https://ui.test"},
        )
        self.addAsyncCleanup(runtime.terminate)
        await runtime.initialize()
        self.runtime, self.core, self.host = (
            runtime,
            runtime.core_runtime,
            runtime.extension_host,
        )
        bundled = dict(runtime._web_validator._bindings)
        endpoint = self._public_page_fixture()
        await self._register()
        self.assertTrue(bundled)
        self.assertTrue(bundled.items() <= runtime._web_validator._bindings.items())
        self.assertEqual((await self._public_call(endpoint)).status_code, 200)
        for owner, capability_id in runtime._assembly.public_capabilities:
            manifest = self.core.registry.snapshot().module(owner).manifest
            capability = next(
                c for c in manifest.capabilities if c.capability_id == capability_id
            )
            self.assertTrue(self.core.issuer.allows_public_web(owner, capability))

    async def test_external_public_registration_is_unpublished_until_complete(self):
        endpoint = self._public_page_fixture()
        restore = self.core.extension_runtime.restore_host_package

        async def observed(candidate):
            self.assertFalse(self.host.public_web_page_bindings())
            self.assertEqual((await self._public_call(endpoint)).status_code, 400)
            await restore(candidate)
            self.assertFalse(self.host.public_web_page_bindings())
            self.assertEqual((await self._public_call(endpoint)).status_code, 400)

        with patch.object(
            self.core.extension_runtime, "restore_host_package", observed
        ):
            await self._register()
        self.assertEqual((await self._public_call(endpoint)).status_code, 200)

    async def test_external_public_cleanup_failure_keeps_old_proof_unusable(self):
        endpoint = self._public_page_fixture()
        lease = await self._register()
        state = self._public_begin(endpoint)

        async def pending(*_args, **_kwargs):
            raise ExtensionCleanupPending("synthetic pending cleanup")

        with patch.object(self.core.extension_runtime, "detach_host_package", pending):
            with self.assertRaises(ExtensionCleanupPending):
                await self.host.unregister_package(lease)
        self.assertFalse(lease.active)
        with self.assertRaises(WebPublicRejected):
            self.runtime.public_web_remaining(state)
        self.assertEqual((await self._public_call(endpoint)).status_code, 503)
        await self.host.unregister_package(lease)
        await self._register()
        with self.assertRaises(WebPublicRejected):
            self.runtime.public_web_remaining(state)
        self.assertEqual((await self._public_call(endpoint)).status_code, 200)

    async def test_external_public_partial_publish_failure_withdraws_both_gates(self):
        endpoint = self._public_page_fixture()
        await self._register()
        state = self._public_begin(endpoint)
        validator = self.runtime._web_validator
        replace = validator.replace_external_bindings
        calls = []

        def rejected_once(bindings):
            calls.append(bindings)
            if len(calls) == 1:
                raise ValueError("synthetic rejected publication")
            replace(bindings)

        with patch.object(validator, "replace_external_bindings", rejected_once):
            with self.assertRaises(ValueError):
                self.runtime._refresh_external_public_web_bindings()
        self.assertFalse(validator._bindings)
        module = self.core.registry.snapshot().module(endpoint[0])
        self.assertFalse(
            self.core.issuer.allows_public_web(
                endpoint[0], module.manifest.capabilities[0]
            )
        )
        self.runtime._refresh_external_public_web_bindings()
        with self.assertRaises(WebPublicRejected):
            self.runtime.public_web_remaining(state)
        self.assertEqual((await self._public_call(endpoint)).status_code, 200)

    async def test_four_modules_real_capture_idempotency_and_exact_old_lease(self):
        first = await self._register()
        self.assertTrue(first.active)
        self.assertIs(await self._register(), first)
        first_epochs = {}
        for name in ("steam", "dota2", "cs2", "deadlock"):
            owner = "steam_dlc/" + name
            module = self.core.registry.snapshot().module(owner)
            self.assertTrue(module.enabled)
            self.assertEqual(self.core.lifecycle.instance(owner).started, 17)
            first_epochs[owner] = module.epoch
        self.assertEqual(len(self.core.admin_operations.module_resources()), 4)
        source_count = len(self.core.admin_authorization._sources)
        self.core.extension_runtime.scan()
        self.assertIsNotNone(self.core.extension_runtime.candidate("steam_dlc"))
        self.metadata.star_cls = object()
        replacement = await self._register()
        self.assertEqual(len(self.core.admin_authorization._sources), source_count)
        self.assertFalse(first.active)
        self.assertTrue(replacement.active)
        for owner, epoch in first_epochs.items():
            self.assertGreater(self.core.registry.snapshot().module(owner).epoch, epoch)
        await self.host.unregister_package(first)
        self.assertTrue(replacement.active)
        await self.host.unregister_package(replacement)
        self.assertFalse(replacement.active)
        self.assertFalse(self.core.registry.snapshot().modules)
        self.assertIsNone(self.core.extension_runtime.candidate("steam_dlc"))
        self.assertTrue(
            (
                await self.core.runtime_repository.current_intent("steam_dlc", "steam")
            ).desired_enabled
        )

    async def test_wrong_instance_and_path_rejected_before_registration(self):
        with self.assertRaisesRegex(ExtensionHostError, "plugin_instance_unavailable"):
            await self.host.register_package(
                plugin_name="external_dlc",
                plugin_instance=object(),
                package_root=str(self.package),
            )
        with self.assertRaisesRegex(ExtensionHostError, "package_root_rejected"):
            await self.host.register_package(
                plugin_name="external_dlc",
                plugin_instance=self.owner,
                package_root=str(ROOT / "modules" / "ff14"),
            )
        self.assertFalse(self.core.registry.snapshot().modules)

    async def test_same_runtime_source_change_requires_restart(self):
        lease = await self._register()
        (self.package / "shared.py").write_text("VALUE = 23\n", encoding="utf-8")
        with self.assertRaisesRegex(ExtensionHostError, "restart_required"):
            await self._register()
        self.assertTrue(lease.active)
        self.assertEqual(self.core.lifecycle.instance("steam_dlc/steam").started, 17)

    async def test_core_close_revokes_bridge_and_stale_lease(self):
        lease = await self._register()
        await self.runtime.terminate()
        self.assertFalse(lease.active)
        with self.assertRaisesRegex(ExtensionHostError, "core_unavailable"):
            await self._register()
        await self.host.unregister_package(lease)

    async def test_no_credential_source_health_and_module_policy_are_isolated(self):
        self.manifest["modules"][1]["sources"] = [
            {"source_id": "public_api", "host": "api.opendota.com"}
        ]
        self._write_manifest()
        await self._register()
        health = await self.runtime._source_health("steam_dlc/dota2", "public_api")
        self.assertIs(health.status, HealthStatus.AVAILABLE)
        other = await self.runtime._source_health("steam_dlc/steam", "public_api")
        self.assertIs(other.status, HealthStatus.UNAVAILABLE)
        resources = self.core.admin_operations.module_resources("steam_dlc/dota2")
        target = next(iter(resources))
        self.assertEqual(target.principal_id, PLUGIN_NAME)
        source = self.runtime._module_admin_source
        self.assertIn(target, source.resources)
        self.assertIn(AdminOperation.UNLOAD_MODULE, source.operations)

    async def test_disabled_user_intent_survives_dlc_and_core_reconnect(self):
        first = await self._register()
        source = self.runtime._module_admin_source
        resources = self.core.admin_operations.module_resources("steam_dlc/steam")
        context = source.issue(
            subject="synthetic-admin",
            request=self,
            expiry=time() + 30,
            operations={AdminOperation.SET_ENABLED},
            resources=resources,
            live=lambda request: request is self,
        )
        try:
            await self.core.admin_operations.set_enabled(
                None,
                "steam_dlc/steam",
                False,
                expected_registry_revision=self.core.registry.snapshot().revision,
                authorization=context,
            )
        finally:
            source.end(context)
        await self.host.unregister_package(first)
        second = await self._register()
        self.assertTrue(second.active)
        self.assertFalse(
            self.core.registry.snapshot().module("steam_dlc/steam").enabled
        )
        old_host = self.host
        await self.runtime.terminate()
        await self.runtime.initialize()
        self.host = self.runtime.extension_host
        self.core = self.runtime.core_runtime
        self.assertIsNot(self.host, old_host)
        self.assertFalse(second.active)
        third = await self._register()
        self.assertTrue(third.active)
        self.assertFalse(
            self.core.registry.snapshot().module("steam_dlc/steam").enabled
        )
        self.assertTrue(self.core.registry.snapshot().module("steam_dlc/dota2").enabled)
        await old_host.unregister_package(second)
        self.assertTrue(third.active)

    async def test_failed_second_factory_leaves_no_partial_route_or_policy(self):
        (self.package / "module.py").write_text(
            SOURCE.replace(
                "self.services = services",
                "self.calls = getattr(self, 'calls', 0) + 1\n"
                "        if self.calls == 2: raise RuntimeError('synthetic failure')",
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
            await self._register()
        self.assertFalse(self.core.registry.snapshot().modules)
        self.assertIsNone(self.core.extension_runtime.candidate("steam_dlc"))
        self.assertIsNone(self.runtime._module_admin_source)
        self.assertFalse(self.host._packages)

    async def test_ff14_and_external_packages_keep_distinct_catalogs_and_factories(
        self,
    ):
        await self.runtime.terminate()
        (self.root / "bundled-data").mkdir()
        runtime = AstrBotRuntime(
            self.context,
            plugin_root=self._ff14_fixture(),
            data_dir=self.root / "bundled-data",
            font_manager_factory=_Font,
            http_transport_factory=_Transport,
            bundled_selection=DEFAULT_BUNDLE_SELECTION,
        )
        self.addAsyncCleanup(runtime.terminate)
        await runtime.initialize()
        self.runtime, self.core = runtime, runtime.core_runtime
        self.host = runtime.extension_host
        await self._register()
        self.assertTrue(self.core.registry.snapshot().module("ff14/ff14").enabled)
        self.assertTrue(self.core.registry.snapshot().module("steam_dlc/steam").enabled)
        self.core.extension_runtime.scan()
        self.assertEqual(
            {c.package.package_id for c in self.core.extension_runtime.candidates()},
            {"ff14", "steam_dlc"},
        )
        self.assertEqual(len(self.core.admin_operations.module_resources()), 5)

    async def test_stop_timeout_revokes_lease_and_retains_exact_retry_ownership(self):
        (self.package / "module.py").write_text(
            "import asyncio\nSLOW = True\n"
            + SOURCE.replace(
                "async def stop(self): self.started = None",
                "async def stop(self):\n"
                "        global SLOW\n"
                "        if SLOW:\n"
                "            SLOW = False\n"
                "            await asyncio.sleep(60)\n"
                "        self.started = None",
            ),
            encoding="utf-8",
        )
        lease = await self._register()
        self.host._timeout = 0.05
        with self.assertRaises((ExtensionHostError, ExtensionCleanupPending)):
            await self.host.unregister_package(lease)
        self.assertFalse(lease.active)
        self.assertIs(self.host._packages["steam_dlc"], lease)
        self.host._timeout = 1
        await self.host.unregister_package(lease)
        self.assertFalse(self.core.registry.snapshot().modules)
        self.assertFalse(self.host._packages)

    async def test_registration_timeout_owns_late_factory_and_cleans_after_retry(self):
        (self.package / "module.py").write_text(
            "import asyncio\n"
            + SOURCE.replace(
                "self.services = services",
                "await asyncio.sleep(0.25)\n        self.services = services",
            ),
            encoding="utf-8",
        )
        self.host._timeout = 0.05
        self.host._registration_timeout = 0.05
        with self.assertRaisesRegex(ExtensionHostError, "cleanup_pending"):
            await self._register()
        retained = self.host._packages.get("steam_dlc")
        if retained is not None:
            self.assertFalse(retained.active)
        await asyncio.sleep(0.3)
        self.host._timeout = 1
        if retained is not None:
            await self.host.unregister_package(retained)
        self.assertFalse(self.core.registry.snapshot().modules)
        self.assertIsNone(self.core.extension_runtime.candidate("steam_dlc"))
        self.assertEqual(self.host._sources._filesystem.reserved_bytes, 0)

    def _ff14_fixture(self):
        source = Path(
            os.environ.get("YGL_TEST_FF14_BUNDLE_SOURCE", ROOT / "modules" / "ff14")
        )
        plugin = self.root / "bundled-plugin"
        shutil.copytree(
            source,
            plugin / "modules" / "ff14",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        return plugin


if __name__ == "__main__":
    unittest.main()
