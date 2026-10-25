"""Real Dashboard/runtime/SQLite authorization guards with an offline QR source."""

import asyncio
import hashlib
import json
import os
import shutil
import sys
import time
import unittest
import uuid
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI, Request
from ygl_test_subject.adapters.astrbot.admin_pages import AdminPages
from ygl_test_subject.adapters.astrbot.runtime import AstrBotRuntime
from ygl_test_subject.core.contracts.administration import (
    AdminAuthorizationDenied,
    AdminOperation,
)
from ygl_test_subject.extensions.static_page_projection import StaticPageProjection
from ygl_test_subject.infrastructure.private_storage import resolve_private_storage
from ygl_test_subject.infrastructure.sqlite.repositories_config import (
    SQLiteConfigRepository,
)
from ygl_test_subject.infrastructure.windows.private_storage import (
    create_private_directory,
    identity_private_base,
)

from tests.host import test_admin_pages as admin_fixture
from tests.host.astrbot_contract import host_contracts
from tests.host.test_external_packages import SOURCE, _Font
from tests.host.test_ff14_pages import _Context, _token
from tests.services.test_source_authorizations import descriptor
from yomihime_game_link_sdk.services import ConfigTarget, HttpResponse

OWNER = "example/demo"
FIELD = "credential_example"


class _AuthorizationTransport:
    def __init__(self):
        self.calls = []
        self.hook = None

    async def request(self, request):
        self.calls.append((request.module_id, request.source_id, request.path))
        if self.hook is not None:
            await self.hook()
        if request.source_id != "example_auth":
            raise AssertionError(
                "Only the declared offline authorization source may run"
            )
        if request.path == "/apply":
            result = {
                "url": "https://identity.example.test/scan?ticket=synthetic-ticket"
            }
        elif request.path == "/check":
            result = {"state": 20, "token": "synthetic-token", "account": "user-one"}
        else:
            raise AssertionError("Unexpected offline authorization path")
        return HttpResponse(200, {}, json.dumps({"result": result}).encode())

    async def close(self):
        pass

    async def request_credential_exchange(self, request):
        raise AssertionError(
            "QR authorization must not perform OAuth credential exchange"
        )


@unittest.skipUnless(os.name == "nt", "native Windows private storage and capture")
class ModuleSourceAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    call = admin_fixture.AdminPagesTests.call

    async def asyncSetUp(self):
        self.root = create_private_directory(
            identity_private_base(), "ygl-module-auth-" + uuid.uuid4().hex
        )
        self.addCleanup(shutil.rmtree, self.root)
        installation = self.root / "dlc"
        package = installation / "packages" / "example"
        package.mkdir(parents=True)
        (installation / "main.py").write_text("# synthetic outer plugin\n")
        (package / "module.py").write_text(SOURCE)
        (package / "shared.py").write_text("VALUE = 17\n")
        (package / "pages").mkdir()
        entry = b"export function mount(){return {dispose(){}};}\n"
        (package / "pages/entry.js").write_bytes(entry)
        manifest = {
            "schema_version": 1,
            "package_id": "example",
            "package_version": "1.0.0",
            "contract_version": "2.0",
            "author": "test",
            "license": "MIT",
            "source": "https://example.invalid/test",
            "modules": [
                {
                    "module_id": "demo",
                    "route": "demo",
                    "category": "platform",
                    "factory_entry": "module:Factory",
                    "module_version": "1.0.0",
                    "capabilities": [],
                    "commands": [],
                    "sources": [
                        {"source_id": "api", "host": "api.example.test"},
                        {
                            "source_id": "example_auth",
                            "host": "auth.example.test",
                            "authorization_only": True,
                        },
                    ],
                    "config_fields": [
                        {
                            "name": FIELD,
                            "sensitive": True,
                            "group": "example_key",
                            "description": "Synthetic key",
                        }
                    ],
                    "pages": [
                        {
                            "route_id": "settings",
                            "role": "config",
                            "title": "Settings",
                            "entry": "pages/entry.js",
                            "styles": [],
                            "access": "public_web",
                            "credential_fields": [FIELD],
                        }
                    ],
                    "resources": [
                        {
                            "path": "pages/entry.js",
                            "sha256": hashlib.sha256(entry).hexdigest(),
                        }
                    ],
                }
            ],
        }
        raw = json.dumps(manifest).encode()
        (package / "yomihime.manifest.json").write_bytes(raw)
        plugin = self.root / "core"
        plugin.mkdir()
        (plugin / "external-page-projection.json").write_bytes(
            StaticPageProjection((raw,)).serialize()
        )
        policy = {
            "schema_version": 1,
            "packages": [
                {
                    "manifest": raw.decode(),
                    "credentials": [
                        {
                            "owner": OWNER,
                            "source": "api",
                            "field": FIELD,
                            "group": "example_key",
                            "description": "Synthetic key",
                            "optional": False,
                            "injections": [
                                {
                                    "path": "/v1/items/*",
                                    "method": "GET",
                                    "location": "header",
                                    "name": "X-Example-Key",
                                }
                            ],
                        }
                    ],
                    "authorizations": {
                        OWNER: {
                            "example_qr": {
                                "credential_field": FIELD,
                                "descriptor": descriptor(),
                            }
                        }
                    },
                }
            ],
        }
        (plugin / "external-source-policies.json").write_text(json.dumps(policy))
        self.context = _Context()
        self.instance = object()
        metadata = SimpleNamespace(
            name="external_example",
            activated=True,
            star_cls=self.instance,
            module=SimpleNamespace(__file__=str(installation / "main.py")),
        )
        self.context.get_registered_star = (
            lambda name: metadata if name == metadata.name else None
        )
        self.transport = _AuthorizationTransport()
        private = create_private_directory(self.root, "private")
        layout = patch(
            "ygl_test_subject.adapters.astrbot.runtime.resolve_private_storage",
            lambda data: resolve_private_storage(data, private_base=private),
        )
        layout.start()
        self.addCleanup(layout.stop)
        key = patch(
            "ygl_test_subject.adapters.astrbot.runtime.EnvironmentKeyProvider.get_key",
            return_value=bytes(range(32)),
        )
        key.start()
        self.addCleanup(key.stop)
        data = self.root / "data"
        data.mkdir()
        self.runtime = AstrBotRuntime(
            self.context,
            plugin_root=plugin,
            data_dir=data,
            bundled_selection=None,
            font_manager_factory=_Font,
            http_transport_factory=lambda: self.transport,
            config={"web_public_origin": "https://ui.test"},
        )
        self.addAsyncCleanup(self.runtime.terminate)
        await self.runtime.initialize()
        await self.runtime.extension_host.register_package(
            plugin_name=metadata.name,
            plugin_instance=self.instance,
            package_root=str(package),
        )
        self.core = self.runtime.core_runtime
        self.assertIsInstance(self.core.config_repository, SQLiteConfigRepository)
        # Factory creation must establish the HTTP handle on a cold registration.
        # No capability invocation or manual service priming precedes this assertion.
        self.assertIn(OWNER, self.core.module_services._http_by_module)
        self.assertEqual(self.transport.calls, [])
        module = self.core.registry.snapshot().module(OWNER)
        self.assertEqual(
            self.core.module_services._http_by_module[OWNER][0], module.manifest
        )
        identity = self.core.lifecycle.current_identity(OWNER)
        self.binding = {
            "owner": OWNER,
            "page": "settings",
            "runtime_id": identity.runtime_id,
            "module_epoch": module.epoch,
        }
        self.pages = AdminPages(self.context, self.runtime)
        self.pages.register()
        self.addCleanup(self.pages.close)
        self.web, self.ns = host_contracts()
        self.auth = self.ns["HostAuth"]()
        self.auth._jwt_secret = "synthetic-test-secret-not-host-at-least-32-bytes"
        self.app = FastAPI()
        self.app.state.jwt_secret = self.auth._jwt_secret
        self.app.state.core_lifecycle = SimpleNamespace(star_context=self.context)
        adapter = self.ns["FastAPIAppAdapter"](self.app)
        adapter._dashboard_server = self.auth

        @self.app.middleware("http")
        async def auth(request, call_next):
            request.state.dashboard_g = SimpleNamespace(username=None)
            result = await self.auth.auth_middleware(request)
            return result if result is not None else await call_next(request)

        @self.app.post("/api/v1/plugins/extensions/{plugin_path:path}")
        async def v1(plugin_path: str, request: Request):
            return await self.ns["_call_plugin_extension"](
                plugin_path, request, "v1-identity"
            )

        @self.app.post("/api/plug/{plugin_path:path}")
        async def legacy(plugin_path: str, request: Request):
            username = await self.ns["require_dashboard_user"](request)
            return await self.ns["_call_plugin_extension"](
                plugin_path, request, username
            )

        astrbot = ModuleType("astrbot")
        astrbot.__version__ = "4.29.1"
        modules = patch.dict(
            sys.modules, {"astrbot": astrbot, "astrbot.api.web": self.web}
        )
        modules.start()
        self.addCleanup(modules.stop)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="https://ui.test"
        )
        self.addAsyncCleanup(self.client.aclose)
        self.headers = {
            "Authorization": "Bearer " + _token(),
            "Origin": "https://ui.test",
        }

    async def session(self):
        response = await self.call("module-page-session", self.binding)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]["page_session"]

    async def status(self, proof):
        response = await self.call(
            "module-credential-status", self.binding | {"page_session": proof}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]

    async def authorize(
        self, proof, revision, *, step="begin", session="", **selectors
    ):
        return await self.call(
            "module-credential-authorize",
            self.binding
            | {
                "page_session": proof,
                "expected_revision": revision,
                "flow_id": "example_qr",
                "step": step,
                "session_id": session,
            }
            | selectors,
        )

    async def test_cold_begin_reaches_controlled_source_with_real_repository(self):
        proof = await self.session()
        snapshot = await self.status(proof)
        self.assertFalse(snapshot["fields"][FIELD]["configured"])
        response = await self.authorize(proof, snapshot["revision"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["session"]["state"], "waiting")
        self.assertEqual(self.transport.calls, [(OWNER, "example_auth", "/apply")])
        self.assertEqual(await self.status(proof), snapshot)

    async def test_real_authorization_poll_commits_token_and_subject_once(self):
        proof = await self.session()
        before = await self.status(proof)
        response = await self.authorize(proof, before["revision"])
        self.assertEqual(response.status_code, 200, response.text)
        session = response.json()["data"]["session"]["session_id"]
        qr = await self.authorize(proof, before["revision"], step="qr", session=session)
        self.assertEqual(qr.status_code, 200, qr.text)
        self.assertTrue(qr.json()["data"]["qr_png"].startswith("iVBORw0KGgo"))
        await asyncio.sleep(2)
        poll = await self.authorize(
            proof, before["revision"], step="poll", session=session
        )
        self.assertEqual(poll.status_code, 200, poll.text)
        result = poll.json()["data"]
        self.assertEqual(result["session"]["state"], "authorized")
        self.assertEqual(result["session"]["authorized_subject"], "user-one")
        after = await self.status(proof)
        self.assertEqual(after["revision"], before["revision"] + 1)
        self.assertTrue(after["fields"][FIELD]["configured"])
        self.assertTrue(after["fields"][FIELD]["decryptable"])
        self.assertEqual(
            result["receipt"], {"module_id": OWNER, "revision": after["revision"]}
        )
        self.assertEqual(
            self.transport.calls,
            [(OWNER, "example_auth", "/apply"), (OWNER, "example_auth", "/check")],
        )
        repeated = await self.authorize(
            proof, before["revision"], step="poll", session=session
        )
        self.assertEqual(repeated.status_code, 409, repeated.text)
        self.assertEqual(await self.status(proof), after)
        self.assertEqual(len(self.transport.calls), 2)

    async def test_foreign_owner_page_runtime_epoch_and_flow_never_reach_source(self):
        proof = await self.session()
        before = await self.status(proof)
        for selector in (
            {"owner": "other/demo"},
            {"page": "home"},
            {"runtime_id": "stale-runtime"},
            {"module_epoch": self.binding["module_epoch"] + 1},
            {"flow_id": "other_flow"},
        ):
            with self.subTest(selector=selector):
                response = await self.authorize(proof, before["revision"], **selector)
                self.assertEqual(response.status_code, 403, response.text)
                self.assertEqual(response.json()["code"], "admin_authorization_denied")
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(await self.status(proof), before)

    async def test_revision_conflict_precedes_source_exchange(self):
        proof = await self.session()
        before = await self.status(proof)
        response = await self.authorize(proof, before["revision"] + 1)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "revision_conflict")
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(await self.status(proof), before)

    async def test_read_only_source_can_read_status_but_cannot_begin(self):
        proof = await self.session()
        before = await self.status(proof)
        source = self.runtime._credential_admin_source
        with patch.object(
            source, "operations", frozenset({AdminOperation.READ_CONFIG})
        ):
            self.assertEqual(await self.status(proof), before)
            response = await self.authorize(proof, before["revision"])
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(await self.status(proof), before)

    async def test_authorize_proof_is_limited_to_selected_credential_fields(self):
        proof = await self.session()
        before = await self.status(proof)
        source = self.runtime._credential_admin_source
        with patch.object(source, "issue", wraps=source.issue) as issue:
            response = await self.authorize(proof, before["revision"])
        self.assertEqual(response.status_code, 200, response.text)
        issued = issue.call_args.kwargs
        self.assertEqual(
            issued["operations"],
            {AdminOperation.READ_CONFIG, AdminOperation.UPDATE_CONFIG},
        )
        self.assertEqual(
            issued["resources"],
            {
                ConfigTarget(
                    self.core.admin_operations.config_principal_id, OWNER
                ): frozenset({FIELD})
            },
        )

    async def test_repository_still_rejects_update_grant_for_current_read(self):
        source = self.runtime._credential_admin_source
        target = ConfigTarget(self.core.admin_operations.config_principal_id, OWNER)
        context = source.issue(
            subject="synthetic",
            request=object(),
            expiry=time.time() + 5,
            operations={AdminOperation.UPDATE_CONFIG},
            resources={target: {FIELD}},
            live=lambda actual: True,
        )
        try:
            grant = await self.core.admin_authorization.authorize(
                AdminOperation.UPDATE_CONFIG,
                invocation=None,
                context=context,
                resources={target: {FIELD}},
            )
            with self.assertRaises(AdminAuthorizationDenied):
                await self.core.config_repository.current(
                    target, grant=grant, operation=AdminOperation.UPDATE_CONFIG
                )
        finally:
            source.end(context)

    async def test_rotated_page_session_rejects_old_proof_and_cancel_writes_nothing(
        self,
    ):
        proof = await self.session()
        before = await self.status(proof)
        response = await self.authorize(proof, before["revision"])
        self.assertEqual(response.status_code, 200, response.text)
        session = response.json()["data"]["session"]["session_id"]
        rotated = await self.call(
            "module-page-session", self.binding | {"previous_session": proof}
        )
        self.assertEqual(rotated.status_code, 200, rotated.text)
        current = rotated.json()["data"]["page_session"]
        denied = await self.authorize(
            proof, before["revision"], step="cancel", session=session
        )
        self.assertEqual(denied.status_code, 403, denied.text)
        cancelled = await self.authorize(
            current, before["revision"], step="cancel", session=session
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["data"]["session"]["state"], "cancelled")
        self.assertEqual(await self.status(current), before)
        self.assertEqual(len(self.transport.calls), 1)

    async def test_closing_page_during_begin_revokes_result_without_credential_write(
        self,
    ):
        proof = await self.session()
        before = await self.status(proof)
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocked():
            entered.set()
            await release.wait()

        self.transport.hook = blocked
        request = asyncio.create_task(self.authorize(proof, before["revision"]))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            closed = await self.call(
                "module-page-session-close", {"page_session": proof}
            )
            self.assertEqual(closed.status_code, 200, closed.text)
        finally:
            release.set()
        response = await request
        self.assertEqual(response.status_code, 403, response.text)
        current = await self.session()
        self.assertEqual(await self.status(current), before)
        self.assertEqual(len(self.transport.calls), 1)
