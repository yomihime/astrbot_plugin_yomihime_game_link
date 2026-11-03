"""Real Host auth, page sessions and Gateway; deterministic SDK-only module."""

import asyncio
import copy
import json
import math
import os
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.adapters.astrbot.public_pages import PublicPages
from ygl_test_subject.adapters.astrbot.web_public import (
    HostPublicWebValidator,
    WebPublicRejected,
)
from ygl_test_subject.extensions.static_page_projection import StaticPageProjection
from ygl_test_subject.infrastructure.private_storage import resolve_private_storage

from tests.host import test_admin_pages as harness
from tests.host import test_module_page_http as page_fixture

ROOT = Path(__file__).resolve().parents[2]


class DashboardModuleInvokeTests(page_fixture.unittest.IsolatedAsyncioTestCase):
    call = harness.AdminPagesTests.call
    session = page_fixture.ModulePageHTTPTests.session
    asyncTearDown = harness.AdminPagesTests.asyncTearDown

    async def asyncSetUp(self):
        factory = harness.AstrBotRuntime
        environment = patch.dict(os.environ, {"YGL_SECRET_KEY": ""})
        environment.start()
        self.addCleanup(environment.stop)

        def runtime(context, **kwargs):
            plugin = kwargs["plugin_root"]
            if os.name == "nt":
                from ygl_test_subject.infrastructure.windows.private_storage import (
                    create_private_directory,
                )

                private = create_private_directory(plugin.parent, "private")
                layout = patch(
                    "ygl_test_subject.adapters.astrbot.runtime.resolve_private_storage",
                    lambda data: resolve_private_storage(data, private_base=private),
                )
                layout.start()
                self.addCleanup(layout.stop)
            package = plugin.parent / "dlc/packages/minimal"
            package.mkdir(parents=True)
            example = ROOT / "examples/modules/minimal"
            manifest = json.loads((example / "yomihime.manifest.json").read_bytes())
            module = manifest["modules"][0]
            query = copy.deepcopy(module["capabilities"][0])
            query["capability_id"] = "query"
            module["capabilities"].append(query)
            page = copy.deepcopy(module["pages"][0])
            page.update(route_id="query", role="aux", capability_id="query")
            module["pages"].append(page)
            source = (example / "module.py").read_text(encoding="utf-8")
            source = source.replace(
                '"status": Status()', '"status": Status(), "query": Status()'
            )
            source = source.replace(
                '"status": CapabilityHealth(status=HealthStatus.AVAILABLE)',
                '"status": CapabilityHealth(status=HealthStatus.AVAILABLE), "query": CapabilityHealth(status=HealthStatus.AVAILABLE)',
            )
            (package / "module.py").write_text(source, encoding="utf-8")
            (package / "pages").mkdir()
            (package / "pages/entry.js").write_bytes(
                (example / "pages/entry.js").read_bytes()
            )
            raw = json.dumps(manifest).encode()
            (package / "yomihime.manifest.json").write_bytes(raw)
            (plugin / "external-page-projection.json").write_bytes(
                StaticPageProjection((raw,)).serialize()
            )
            outer = package.parents[1] / "main.py"
            outer.write_text("# synthetic test plugin\n", encoding="utf-8")
            self.package = package
            self.plugin_instance = object()
            metadata = SimpleNamespace(
                name="synthetic_dashboard",
                activated=True,
                star_cls=self.plugin_instance,
                module=SimpleNamespace(__file__=str(outer)),
            )
            context.get_registered_star = (
                lambda name: metadata if name == metadata.name else None
            )
            return factory(context, **kwargs)

        with patch.object(harness, "AstrBotRuntime", runtime):
            await harness.AdminPagesTests.asyncSetUp(self)
        await self.runtime.extension_host.register_package(
            plugin_name="synthetic_dashboard",
            plugin_instance=self.plugin_instance,
            package_root=str(self.package),
        )
        self.public = PublicPages(self.context, self.runtime)
        self.public.register()
        self.addCleanup(self.public.close)
        identity = self.runtime.core_runtime.lifecycle.current_identity("minimal/demo")
        self.assertIsNotNone(identity)
        self.binding = dict(
            owner="minimal/demo",
            page="home",
            runtime_id=identity.runtime_id,
            module_epoch=identity.module_epoch,
        )

    async def test_unconfigured_dashboard_status_and_query_use_real_gateway(self):
        self.assertFalse(self.runtime.public_web_status()["entry_ready"])
        # The old native client posts this public envelope after successful
        # bootstrap. Keep the actual rejection as the before/after control.
        await self.session()
        old = await self.client.post(
            f"/api/plug/{harness.PLUGIN_NAME}/invoke",
            headers=self.headers,
            json={
                "owner": self.binding["owner"],
                "page": "home",
                "capability_id": "status",
                "parameters": {},
            },
        )
        self.assertEqual(
            (old.status_code, old.json()["code"]), (503, "entry_unavailable")
        )
        for page, capability in (("home", "status"), ("query", "query")):
            self.binding["page"] = page
            proof = await self.session()
            response = await self.call(
                "module-invoke",
                self.binding
                | {
                    "page_session": proof,
                    "capability_id": capability,
                    "parameters": {},
                },
            )
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["status"], "ok")
            self.assertEqual(body["data"]["schema_version"], 1)
            self.assertEqual(body["data"]["status"], "success")
            self.assertEqual(body["data"]["document"]["blocks"][0]["kind"], "text")

    async def query(self, proof, **changes):
        return await self.call(
            "module-invoke",
            self.binding
            | {
                "page_session": proof,
                "capability_id": "status",
                "parameters": {},
                **changes,
            },
        )

    async def test_public_origin_guard_unchanged_and_preview_contract(self):
        proof = await self.session()
        response = await self.call(
            "module-preview",
            self.binding
            | {
                "page_session": proof,
                "capability_id": "status",
                "parameters": {},
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(
            (data["status"], data["image_state"], data["images"]),
            ("success", "none", []),
        )
        envelope = {
            "owner": self.binding["owner"],
            "page": "home",
            "capability_id": "status",
            "parameters": {},
        }
        path = f"/api/plug/{harness.PLUGIN_NAME}/invoke"
        denied = await self.client.post(path, headers=self.headers, json=envelope)
        self.assertEqual(denied.status_code, 503)
        self.assertEqual(denied.json()["code"], "entry_unavailable")
        self.runtime._web_origin = "https://ui.test"
        self.runtime._web_state = "configured"
        accepted = await self.client.post(path, headers=self.headers, json=envelope)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["data"]["status"], "success")
        foreign = await self.client.post(
            path,
            headers=self.headers | {"Origin": "https://foreign.test"},
            json=envelope,
        )
        self.assertEqual(foreign.status_code, 403)

    async def test_identity_and_session_forgeries_do_not_enter_gateway(self):
        proof = await self.session()
        gateway = self.runtime.core_runtime.gateway
        with patch.object(
            gateway, "invoke_public_web", side_effect=AssertionError("must not invoke")
        ):
            for changes in (
                {"page_session": "synthetic-forged"},
                {"owner": "other/demo"},
                {"page": "query"},
                {"capability_id": "query"},
                {"runtime_id": "wrong"},
                {"module_epoch": self.binding["module_epoch"] + 1},
            ):
                with self.subTest(fields=sorted(changes)):
                    rejected = await self.query(proof, **changes)
                    self.assertEqual(rejected.status_code, 403)
                    self.assertNotIn("document", rejected.json().get("data", {}))
            extra = await self.query(proof, native_dashboard=True)
            self.assertEqual(extra.status_code, 400)
            no_proof = await self.call(
                "module-invoke",
                self.binding | {"capability_id": "status", "parameters": {}},
            )
            self.assertEqual(no_proof.status_code, 403)
            no_auth = await self.call("module-invoke", self.binding, headers={})
            self.assertIn(no_auth.status_code, (401, 403))

    async def test_executing_query_fences_close_revoke_expiry_epoch_generation(self):
        handler = self.runtime.core_runtime.lifecycle.handlers(
            "minimal/demo"
        ).capabilities["status"]
        original = handler.invoke
        generation = self.runtime._generation
        sessions = self.runtime._page_sessions
        clock = sessions._clock
        verifier = self.auth._validate_dashboard_token
        lifecycle = self.runtime.core_runtime.lifecycle
        identity = lifecycle.current_identity("minimal/demo")
        for change in ("close", "revoke", "expiry", "epoch", "generation"):
            proof = await self.session()

            async def invoke(_handler, context, parameters):
                if change == "close":
                    closed = await self.call(
                        "module-page-session-close", {"page_session": proof}
                    )
                    self.assertEqual(closed.status_code, 200)
                elif change == "revoke":
                    self.auth._validate_dashboard_token = MethodType(
                        lambda *_: (None, "synthetic-revoked"), self.auth
                    )
                elif change == "expiry":
                    sessions._clock = lambda: clock() + 31
                elif change == "epoch":
                    lifecycle.current_identity = (
                        lambda owner: replace(
                            identity, module_epoch=identity.module_epoch + 1
                        )
                        if owner == identity.module_id
                        else None
                    )
                else:
                    self.runtime._generation += 1
                return await original(context, parameters)

            try:
                with patch.object(handler, "invoke", MethodType(invoke, handler)):
                    response = await self.query(proof)
                self.assertEqual(response.status_code, 403, change)
                self.assertNotIn("document", response.json().get("data", {}))
                self.assertEqual(self.runtime._web_validator._requests, {})
            finally:
                self.auth._validate_dashboard_token = verifier
                self.runtime._generation = generation
                sessions._clock = clock
                lifecycle.current_identity = MethodType(
                    type(lifecycle).current_identity, lifecycle
                )

    async def test_cancelled_real_http_request_releases_proof_and_children(self):
        handler = self.runtime.core_runtime.lifecycle.handlers(
            "minimal/demo"
        ).capabilities["status"]
        entered = asyncio.Event()
        stopped = asyncio.Event()

        async def blocked(*_):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        proof = await self.session()
        with patch.object(handler, "invoke", blocked):
            task = asyncio.create_task(self.query(proof))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                await asyncio.wait_for(stopped.wait(), 3)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.runtime._web_validator._requests, {})
        self.assertEqual(self.pages.requests, {})

    async def test_ticket_is_released_if_post_begin_deadline_check_rejects(self):
        proof = await self.session()
        validator = self.runtime._web_validator
        with patch.object(
            validator,
            "deadline_facts",
            side_effect=WebPublicRejected("session_expired"),
        ):
            response = await self.query(proof)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "operation_unavailable")
        self.assertEqual(validator._requests, {})

    async def test_final_watcher_join_cannot_publish_after_session_or_route_revocation(
        self,
    ):
        create = asyncio.create_task
        handler = self.runtime.core_runtime.lifecycle.handlers(
            "minimal/demo"
        ).capabilities["status"]
        original = handler.invoke
        for change in ("session", "route"):
            proof = await self.session()
            entered = asyncio.Event()

            async def watcher():
                try:
                    entered.set()
                    await asyncio.Event().wait()
                finally:
                    if change == "session":
                        self.runtime._page_sessions.revoke_owner("minimal/demo")
                    else:
                        self.runtime._web_validator.replace_external_bindings({})

            def task_factory(coroutine, *, name=None, **kwargs):
                if name == "ygl:module-invoke-disconnect":
                    coroutine.close()
                    coroutine = watcher()
                return create(coroutine, name=name, **kwargs)

            async def invoke(_handler, context, parameters):
                await entered.wait()
                return await original(context, parameters)

            try:
                with (
                    patch.object(asyncio, "create_task", task_factory),
                    patch.object(handler, "invoke", MethodType(invoke, handler)),
                ):
                    response = await self.query(proof)
                self.assertEqual(response.status_code, 403, change)
                self.assertNotIn("document", response.json().get("data", {}))
                self.assertEqual(self.runtime._web_validator._requests, {})
            finally:
                self.runtime._refresh_external_public_web_bindings()


class TrustedPageCurrentTests(unittest.TestCase):
    def test_private_current_callback_keeps_public_expiry_contract_and_fails_closed(
        self,
    ):
        endpoint = ("minimal/demo", "home", "status")
        validator = HostPublicWebValidator(
            1, lambda *_: True, bindings={endpoint: ("minimal/demo", "status")}
        )
        validator.attach(object())
        expiry = time.time() + 0.75
        with self.assertRaises(WebPublicRejected):
            validator.begin(endpoint, "synthetic", expiry)
        proof = validator.begin(endpoint, "synthetic", expiry, current=lambda: True)
        self.assertGreater(validator.remaining(proof), 0)
        self.assertLessEqual(validator.remaining(proof), 0.75)
        validator.revoke(proof)
        for value in (True, math.nan, math.inf, -math.inf, time.time() - 1):
            with self.subTest(kind=type(value).__name__):
                with self.assertRaises(WebPublicRejected):
                    validator.begin(endpoint, "synthetic", value, current=lambda: True)

        def raises():
            raise ValueError("synthetic")

        for current in (lambda: 1, lambda: object(), raises):
            with self.assertRaises(WebPublicRejected):
                validator.begin(endpoint, "synthetic", expiry, current=current)
        self.assertEqual(validator._requests, {})
