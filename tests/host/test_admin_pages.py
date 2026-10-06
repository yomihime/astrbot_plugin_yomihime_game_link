"""Fixed real Host auth/dispatch AST, synthetic tokens and isolated Core storage."""

import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI, Request
from ygl_test_subject.adapters.astrbot.admin_pages import AdminPages
from ygl_test_subject.adapters.astrbot.runtime import PLUGIN_NAME, AstrBotRuntime
from ygl_test_subject.adapters.astrbot.web_admin import verified_dashboard_request
from ygl_test_subject.api.administration import AdminAuthorizationDenied

from tests.host.astrbot_contract import host_contracts
from tests.host.test_astrbot_runtime import _IdleTransport, _MessageChain, _Plain
from tests.host.test_ff14_pages import _Context, _token


class AdminPagesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.context = _Context()
        self.transport = _IdleTransport()
        plugin_root = Path(self.temp.name) / "plugin"
        shutil.copytree(
            Path(__file__).resolve().parents[2] / "modules/ff14",
            plugin_root / "modules/ff14",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        (Path(self.temp.name) / "data").mkdir()
        self.runtime = AstrBotRuntime(
            self.context,
            plugin_root=plugin_root,
            data_dir=Path(self.temp.name) / "data",
            config={},
            raw_legacy_config={"ff14_default_region": "PRIVATE-INVALID-LEGACY"},
            http_transport_factory=lambda: self.transport,
            plain_factory=_Plain,
            chain_factory=_MessageChain,
        )
        await self.runtime.initialize()
        self.pages = AdminPages(self.context, self.runtime)
        self.pages.register()
        self.web, self.ns = host_contracts()
        self.auth = self.ns["HostAuth"]()
        self.auth._jwt_secret = "synthetic-test-secret-not-host-at-least-32-bytes"
        self.app = FastAPI()
        self.app.state.jwt_secret = self.auth._jwt_secret
        self.app.state.core_lifecycle = SimpleNamespace(star_context=self.context)
        self.adapter = self.ns["FastAPIAppAdapter"](self.app)
        self.adapter._dashboard_server = self.auth

        @self.app.middleware("http")
        async def auth(request, call_next):
            request.state.dashboard_g = SimpleNamespace(username=None)
            result = await self.auth.auth_middleware(request)
            return result if result is not None else await call_next(request)

        @self.app.post("/api/v1/plugins/extensions/{plugin_path:path}")
        async def v1(plugin_path: str, request: Request):
            return await self.ns["_call_plugin_extension"](
                plugin_path, request, "v1-broad-identity"
            )

        @self.app.post("/api/plug/{plugin_path:path}")
        async def legacy(plugin_path: str, request: Request):
            username = await self.ns["require_dashboard_user"](request)
            return await self.ns["_call_plugin_extension"](
                plugin_path, request, username
            )

        astrbot = ModuleType("astrbot")
        astrbot.__version__ = "4.28.2"
        self.modules = patch.dict(
            sys.modules, {"astrbot": astrbot, "astrbot.api.web": self.web}
        )
        self.modules.start()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="https://ui.test"
        )
        self.headers = {
            "Authorization": "Bearer " + _token(),
            "Origin": "https://ui.test",
        }

    async def asyncTearDown(self):
        await self.client.aclose()
        self.pages.close()
        await self.runtime.terminate()
        self.modules.stop()
        self.temp.cleanup()

    async def call(self, endpoint, data, *, headers=None, v1=True):
        base = "/api/v1/plugins/extensions/" if v1 else "/api/plug/"
        return await self.client.post(
            f"{base}{PLUGIN_NAME}/admin/{endpoint}",
            headers=self.headers if headers is None else headers,
            json=data,
            follow_redirects=True,
        )

    async def test_catalog_uses_existing_host_proof_redirect_and_empty_body(self):
        response = await self.call("catalog", {})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.history[0].status_code, 307)
        self.assertEqual(response.headers["cache-control"], "no-store")
        data = response.json()["data"]
        self.assertEqual(set(data), {"schema_version", "fields"})
        self.assertEqual(
            len([field for field in data["fields"] if field["readable"]]), 4
        )
        self.assertNotIn("PRIVATE-INVALID-LEGACY", response.text)
        self.assertEqual(
            (await self.call("catalog", {"module_id": "ff14/ff14"})).status_code, 400
        )
        self.assertEqual((await self.call("catalog", {}, headers={})).status_code, 401)
        asset = {
            **self.headers,
            "Authorization": "Bearer "
            + _token(
                token_type="plugin_page_asset",
                plugin_name=PLUGIN_NAME,
                page_name="management",
            ),
        }
        self.assertEqual(
            (await self.call("catalog", {}, headers=asset)).status_code, 401
        )
        self.assertEqual(self.pages.requests, {})
        self.assertEqual(self.runtime._admin_source._proofs, {})

    async def test_actual_host_read_update_conflict_recover_and_limited_rollback(self):
        self.assertFalse(self.runtime.ready)
        self.assertTrue(self.runtime.core_runtime.configuration_blocked)
        response = await self.call("read", {})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(set(data), {"game_link/core", "ff14/ff14"})
        self.assertNotIn("PRIVATE-INVALID-LEGACY", response.text)
        revision = data["game_link/core"]["revision"]
        patchdata = {
            "module_id": "game_link/core",
            "expected_revision": revision,
            "updates": [
                {"field": "default_region", "mode": "replace", "value": "global"}
            ],
        }
        response = await self.call("update", patchdata)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(self.runtime.ready)
        self.assertEqual((await self.call("update", patchdata)).status_code, 409)
        data = (await self.call("read", {})).json()["data"]
        response = await self.call(
            "recover",
            {
                "expected_revisions": {k: v["revision"] for k, v in data.items()},
                "complete_from_current": False,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(self.runtime.ready)
        self.assertTrue(self.runtime.core_runtime.started)
        data = (await self.call("read", {})).json()["data"]
        response = await self.call(
            "rollback",
            {"expected_revisions": {k: v["revision"] for k, v in data.items()}},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            self.runtime._raw_legacy_config,
            {"ff14_default_region": "PRIVATE-INVALID-LEGACY"},
        )
        self.assertEqual(
            (
                await self.runtime.core_runtime.admin_credential_repository.current()
            ).generation,
            0,
        )
        self.assertEqual(self.pages.requests, {})
        self.assertEqual(self.runtime._admin_source._proofs, {})

    async def test_ordinary_jwt_required_and_origin_identity_body_scope_rejected(self):
        cases = [
            {
                **self.headers,
                "Authorization": "Bearer "
                + _token(
                    token_type="plugin_page_asset",
                    plugin_name=PLUGIN_NAME,
                    page_name="management",
                ),
            },
            {**self.headers, "Authorization": "Bearer synthetic-active-api-key"},
            {"X-API-Key": "synthetic-active-api-key", "Origin": "https://ui.test"},
            {
                "Cookie": "astrbot_dashboard_jwt=" + _token(),
                "Origin": "https://ui.test",
            },
            {**self.headers, "Origin": "https://other.test"},
            {**self.headers, "X-API-Key": "synthetic-active-api-key"},
            {
                **self.headers,
                "Authorization": "Bearer " + _token(exp=int(time.time()) - 1),
            },
            {
                **self.headers,
                "Authorization": "Bearer " + _token(exp=float(time.time() + 60)),
            },
            {**self.headers, "Authorization": "Bearer " + _token(token_type="unknown")},
            [
                ("Authorization", self.headers["Authorization"]),
                ("Authorization", self.headers["Authorization"]),
                ("Origin", "https://ui.test"),
            ],
        ]
        for headers in cases:
            response = await self.call("read", {}, headers=headers)
            self.assertNotEqual(response.status_code, 200, response.text)
        for body in (
            {"role": "admin"},
            {"username": "synthetic"},
            {"authorization": {}},
            {"session_id": "query"},
        ):
            self.assertEqual((await self.call("read", body)).status_code, 400)
        for module, field in (
            ("ff14/ff14", "credential_fflogs_global"),
            ("game_link/core", "extra"),
            ("other/mod", "default_region"),
        ):
            response = await self.call(
                "update",
                {
                    "module_id": module,
                    "expected_revision": 1,
                    "updates": [
                        {"field": field, "mode": "replace", "value": "untrusted"}
                    ],
                },
            )
            self.assertEqual(response.status_code, 400, response.text)
        response = await self.call(
            "recover", {"expected_revisions": {}, "complete_from_current": True}
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertFalse(self.runtime.ready)
        self.assertEqual(self.runtime._admin_source._proofs, {})

    async def test_missing_server_version_subject_mismatch_duplicate_size_and_old_handler(
        self,
    ):
        from starlette.requests import Request as StarletteRequest

        path = f"/api/plug/{PLUGIN_NAME}/admin/read"
        request = StarletteRequest(
            {
                "type": "http",
                "method": "POST",
                "path": path,
                "scheme": "https",
                "query_string": b"",
                "headers": [
                    (k.lower().encode(), v.encode()) for k, v in self.headers.items()
                ],
                "app": self.app,
                "server": ("ui.test", 443),
            }
        )
        request.state.dashboard_g = SimpleNamespace(username="synthetic")
        facade = self.web.PluginRequest(request, username="other")
        with self.assertRaises(AdminAuthorizationDenied):
            verified_dashboard_request(facade, path)
        facade.username = "synthetic"
        sys.modules["astrbot"].__version__ = "4.29.0"
        with self.assertRaises(AdminAuthorizationDenied):
            verified_dashboard_request(facade, path)
        sys.modules["astrbot"].__version__ = "4.28.2"
        for content in (b'{"role":1,"role":2}', b"[]", b"{" + b" " * 9000 + b"}"):
            response = await self.client.post(
                path,
                headers={**self.headers, "Content-Type": "application/json"},
                content=content,
            )
            self.assertEqual(response.status_code, 400, response.text)
        del self.app.state.dashboard_app_adapter
        with self.assertRaises(AdminAuthorizationDenied):
            verified_dashboard_request(facade, path)
        old = self.pages.registrations[0][1]
        self.pages.close()
        self.pages.register()
        with self.web.bind_request_context(SimpleNamespace(path=path, method="POST")):
            response = await old()
        self.assertEqual(response.status_code, 403)
