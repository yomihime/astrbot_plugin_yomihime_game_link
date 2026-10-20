"""Fixed real Host auth/dispatch AST, synthetic tokens and isolated Core storage."""

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import MethodType, ModuleType, SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI, Request
from ygl_test_subject.adapters.astrbot.admin_pages import AdminPages
from ygl_test_subject.adapters.astrbot.runtime import PLUGIN_NAME, AstrBotRuntime
from ygl_test_subject.adapters.astrbot.web_admin import verified_dashboard_request
from ygl_test_subject.core.contracts.administration import AdminAuthorizationDenied

from tests.fixtures.managed_font import OfflineManagedFont
from tests.host.astrbot_contract import host_contracts
from tests.host.test_astrbot_runtime import _IdleTransport, _MessageChain, _Plain
from tests.host.test_ff14_pages import _Context, _token


class AdminPagesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        private_parent = os.environ.get("YGL_MANAGED_KEY_TEST_ROOT")
        self.temp = tempfile.TemporaryDirectory(
            dir=getattr(
                self,
                "fixture_parent",
                Path(private_parent) if private_parent else Path.cwd(),
            )
        )
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
            font_manager_factory=OfflineManagedFont,
            plugin_root=plugin_root,
            data_dir=Path(self.temp.name) / "data",
            config={},
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
        astrbot.__version__ = "4.29.1"
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

    def direct_request(self):
        from starlette.requests import Request as StarletteRequest

        path = f"/api/plug/{PLUGIN_NAME}/admin/read"
        raw = StarletteRequest(
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
        raw.state.dashboard_g = SimpleNamespace(username="synthetic")
        return self.web.PluginRequest(raw, username="synthetic"), path

    async def test_bound_host_verifier_receives_exact_token_and_path(self):
        facade, path = self.direct_request()
        original = self.auth._validate_dashboard_token
        calls = []

        def observed(server, token, path):
            self.assertIs(server, self.auth)
            calls.append((token, path))
            return original(token, path)

        with patch.object(
            self.auth, "_validate_dashboard_token", MethodType(observed, self.auth)
        ):
            raw, username, expires = verified_dashboard_request(facade, path)
        self.assertIs(raw, facade._request)
        self.assertEqual(username, "synthetic")
        self.assertGreater(expires, time.time())
        self.assertLessEqual(expires, time.time() + 30)
        self.assertEqual(calls, [(self.headers["Authorization"][7:], path)])

    async def test_unsupported_host_verifier_contracts_fail_closed(self):
        facade, path = self.direct_request()
        calls = []

        def missing_path(server, token):
            calls.append("missing_path")
            raise AssertionError("unsupported verifier must not run")

        def extra_required(server, token, path, required):
            calls.append("extra_required")
            raise AssertionError("unsupported verifier must not run")

        def keyword_only(server, token, *, path):
            calls.append("keyword_only")
            raise AssertionError("unsupported verifier must not run")

        async def asynchronous(server, token, path):
            raise AssertionError("unsupported verifier must not run")

        def generator(server, token, path):
            yield None

        async def asynchronous_generator(server, token, path):
            yield None

        class SpoofedBoundCallable:
            def __init__(self, server):
                self.__self__ = server

            def __call__(self, token, request_path):
                calls.append("spoofed_callable")
                return {"username": "synthetic", "exp": int(time.time()) + 60}, ""

        incompatible = [
            SpoofedBoundCallable(self.auth),
            None,
            lambda token, path: ({}, ""),
            self.ns["HostAuth"]()._validate_dashboard_token,
            *(
                MethodType(method, self.auth)
                for method in (
                    missing_path,
                    extra_required,
                    keyword_only,
                    asynchronous,
                    generator,
                    asynchronous_generator,
                )
            ),
        ]
        for verifier in incompatible:
            with (
                self.subTest(verifier=verifier),
                patch.object(self.auth, "_validate_dashboard_token", verifier),
            ):
                with self.assertRaises(AdminAuthorizationDenied):
                    verified_dashboard_request(facade, path)
        self.assertEqual(calls, [])
        for adapter in (
            None,
            SimpleNamespace(_app=object(), _dashboard_server=self.auth),
        ):
            with patch.object(self.app.state, "dashboard_app_adapter", adapter):
                with self.assertRaises(AdminAuthorizationDenied):
                    verified_dashboard_request(facade, path)
        with patch.object(self.adapter, "_dashboard_server", None):
            with self.assertRaises(AdminAuthorizationDenied):
                verified_dashboard_request(facade, path)

    async def test_compatible_positional_verifier_does_not_depend_on_parameter_names(
        self,
    ):
        facade, path = self.direct_request()
        original = self.auth._validate_dashboard_token

        def renamed(server, credential, request_path, optional=None):
            return original(credential, request_path)

        def variadic(server, *args):
            return original(*args)

        for method in (renamed, variadic):
            with (
                self.subTest(method=method),
                patch.object(
                    self.auth,
                    "_validate_dashboard_token",
                    MethodType(method, self.auth),
                ),
            ):
                self.assertEqual(
                    verified_dashboard_request(facade, path)[1], "synthetic"
                )

    async def test_host_verifier_invalid_results_and_raw_identity_fail_closed(self):
        facade, path = self.direct_request()
        valid = {"username": "synthetic", "exp": int(time.time()) + 60}

        class EmptyError(str):
            pass

        results = [
            None,
            [],
            [valid, ""],
            (valid,),
            (valid, "", "extra"),
            (valid, None),
            (valid, False),
            (valid, 0),
            (valid, []),
            (valid, {}),
            (valid, b""),
            (valid, EmptyError("")),
            (valid, "rejected"),
            (None, ""),
            ([], ""),
            ({}, ""),
            *(
                ({**valid, "exp": expires}, "")
                for expires in (
                    None,
                    True,
                    float(time.time() + 60),
                    int(time.time()) - 1,
                )
            ),
            *(
                ({**valid, "username": username}, "")
                for username in (None, "", " ", "other", 123)
            ),
            ({**valid, "token_type": "plugin_page_asset"}, ""),
            ({**valid, "token_type": None}, ""),
        ]
        for result in results:

            def invalid(server, token, path):
                return result

            with (
                self.subTest(result=result),
                patch.object(
                    self.auth,
                    "_validate_dashboard_token",
                    MethodType(invalid, self.auth),
                ),
            ):
                with self.assertRaises(AdminAuthorizationDenied):
                    verified_dashboard_request(facade, path)
        for key, value in (("method", "GET"), ("path", path + "/other")):
            facade, path = self.direct_request()
            facade._request.scope[key] = value
            if hasattr(facade._request, "_url"):
                del facade._request._url
            with self.assertRaises(AdminAuthorizationDenied):
                verified_dashboard_request(facade, path)
        for key, value in (("method", "GET"), ("path", path + "/other")):
            facade, path = self.direct_request()
            setattr(facade, key, value)
            with self.assertRaises(AdminAuthorizationDenied):
                verified_dashboard_request(facade, path)
        facade, path = self.direct_request()
        facade._request.state.dashboard_g.username = "other"
        with self.assertRaises(AdminAuthorizationDenied):
            verified_dashboard_request(facade, path)

    async def test_recover_ready_core_is_rejected_without_shutdown(self):
        core = self.runtime.core_runtime
        rows = (await self.call("read", {})).json()["data"]
        response = await self.call(
            "recover",
            {"expected_revisions": {key: row["revision"] for key, row in rows.items()}},
        )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIs(self.runtime.core_runtime, core)
        self.assertTrue(self.runtime.ready)
        self.assertTrue(core.started)
        self.assertEqual((await self.call("read", {})).status_code, 200)
        self.assertEqual(self.runtime._admin_source._proofs, {})

    async def test_catalog_uses_existing_host_proof_redirect_and_empty_body(self):
        response = await self.call("catalog", {})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.history[0].status_code, 307)
        self.assertEqual(response.headers["cache-control"], "no-store")
        data = response.json()["data"]
        self.assertEqual(set(data), {"schema_version", "fields"})
        self.assertEqual(
            len([field for field in data["fields"] if field["readable"]]), 8
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

    async def test_sync_diagnostics_is_bounded_metadata_under_existing_admin_proof(
        self,
    ):
        from ygl_test_subject.infrastructure.sync_diagnostics import emit

        diagnostics = self.runtime._core.sync_diagnostics
        trace_id = "a" * 32
        token = diagnostics.activate(trace_id, "web_public")
        try:
            emit(
                "core_start",
                budget_ms=10000,
                body="PRIVATE-DIAGNOSTIC-SENTINEL",
            )
            emit("core_final", code="completed", capability_status="error")
        finally:
            diagnostics.reset(token)
        # A legitimate later job can be the newest rolling trace at request time.
        competing_id = "b" * 32
        token = diagnostics.activate(competing_id, "scheduler")
        try:
            emit("core_start", budget_ms=10000)
        finally:
            diagnostics.reset(token)
        response = await self.call("sync-diagnostics", {})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.history[0].status_code, 307)
        self.assertEqual(response.headers["cache-control"], "no-store")
        data = response.json()["data"]
        self.assertEqual(data["retention"], "memory_only")
        by_id = {item["invocation_id"]: item for item in data["attempts"]}
        self.assertIn(trace_id, by_id)
        self.assertIn(competing_id, by_id)
        trace = by_id[trace_id]
        self.assertEqual(trace["core_final"], "completed")
        self.assertEqual(trace["capability_status"], "error")
        self.assertEqual(
            [event["stage"] for event in trace["events"]],
            ["core_start", "core_final"],
        )
        self.assertEqual(trace["core_budget_ms"], 10000)
        self.assertEqual(trace["events"][0]["budget_ms"], 10000)
        self.assertNotIn("body", trace["events"][0])
        self.assertEqual(by_id[competing_id]["origin"], "scheduler")
        self.assertEqual(by_id[competing_id]["core_final"], "in_progress")
        self.assertNotIn("PRIVATE-DIAGNOSTIC-SENTINEL", response.text)
        self.assertEqual(
            (await self.call("sync-diagnostics", {"region": "cn"})).status_code, 400
        )
        self.assertEqual(
            (await self.call("sync-diagnostics", {}, headers={})).status_code, 401
        )
        wrong_origin = {**self.headers, "Origin": "https://other.test"}
        self.assertEqual(
            (await self.call("sync-diagnostics", {}, headers=wrong_origin)).status_code,
            403,
        )
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
            (await self.call("sync-diagnostics", {}, headers=asset)).status_code, 401
        )
        self.assertEqual(self.pages.requests, {})
        self.assertEqual(self.runtime._admin_source._proofs, {})

    async def test_calendar_sync_interval_auth_schema_cas_and_refresh(self):
        name = "ff14_calendar_sync_interval_seconds"
        owner = "ff14/ff14"
        catalog = (await self.call("catalog", {})).json()["data"]["fields"]
        field = next(
            field
            for field in catalog
            if field["module_id"] == owner and field["name"] == name
        )
        self.assertEqual(field["description"], "日历同步周期")
        self.assertEqual(field["group"], "calendar")
        self.assertEqual(field["default"], 21600)
        self.assertEqual(
            field["value_schema"], {"type": "integer", "enum": [21600, 43200, 86400]}
        )
        self.assertTrue(field["readable"])
        self.assertTrue(field["editable"])
        row = (await self.call("read", {})).json()["data"][owner]
        self.assertEqual(row["fields"][name]["value"], 21600)

        def body(value):
            return {
                "module_id": owner,
                "expected_revision": row["revision"],
                "updates": [{"field": name, "mode": "replace", "value": value}],
            }

        for value in (None, True, False, 0, 900, 21601, 21600.0, "21600"):
            with self.subTest(value=value):
                response = await self.call("update", body(value))
                self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(
            (await self.call("update", body(43200), headers={})).status_code, 401
        )
        self.assertEqual((await self.call("read", {})).json()["data"][owner], row)
        for value in (43200, 86400, 21600):
            patchdata = body(value)
            response = await self.call("update", patchdata)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual((await self.call("update", patchdata)).status_code, 409)
            row = (await self.call("read", {})).json()["data"][owner]
            self.assertEqual(row["revision"], response.json()["data"]["revision"])
            self.assertEqual(row["fields"][name]["value"], value)
            self.assertEqual(row["fields"][name]["source"], "sqlite")
        clear = {
            "module_id": owner,
            "expected_revision": row["revision"],
            "updates": [{"field": name, "mode": "clear"}],
        }
        response = await self.call("update", clear)
        self.assertEqual(response.status_code, 200, response.text)
        row = (await self.call("read", {})).json()["data"][owner]
        self.assertEqual(
            row["fields"][name],
            {"value": 21600, "state": "valid", "source": "default", "present": False},
        )
        self.assertTrue(self.runtime.ready)
        self.assertTrue(self.runtime.core_runtime.started)
        self.assertEqual(self.pages.requests, {})
        self.assertEqual(self.runtime._admin_source._proofs, {})

    async def test_theme_settings_use_existing_host_auth_read_write_and_cas(self):
        from ygl_test_subject.presentation.layout_renderer import list_themes

        catalog = (await self.call("catalog", {})).json()["data"]["fields"]
        expected = [
            {"value": option.id, "label": option.label} for option in list_themes()
        ]
        themes = {
            (field["module_id"], field["name"]): field
            for field in catalog
            if field["group"] == "reply_style"
        }
        self.assertEqual(themes[("game_link/core", "image_theme")]["choices"], expected)
        self.assertEqual(
            themes[("ff14/ff14", "reply_theme")]["choices"],
            [{"value": None, "label": "跟随核心"}, *expected],
        )
        before = (await self.call("read", {})).json()["data"]
        self.assertIsNone(before["ff14/ff14"]["fields"]["reply_theme"]["value"])

        async def save(owner, field, value, *, clear=False):
            current = (await self.call("read", {})).json()["data"][owner]
            body = {
                "module_id": owner,
                "expected_revision": current["revision"],
                "updates": [
                    {"field": field, "mode": "clear"}
                    if clear
                    else {"field": field, "mode": "replace", "value": value}
                ],
            }
            response = await self.call("update", body)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual((await self.call("update", body)).status_code, 409)

        await save("game_link/core", "image_theme", "deep_indigo")
        await save("ff14/ff14", "reply_theme", "cool_white")
        selected = await self.runtime.core_runtime.theme_settings.current("ff14/ff14")
        self.assertEqual(
            (selected.core_theme, selected.module_theme), ("deep_indigo", "cool_white")
        )
        help_selected = await self.runtime.core_runtime.theme_settings.current()
        self.assertEqual(
            (help_selected.core_theme, help_selected.module_theme),
            ("deep_indigo", None),
        )
        await save("ff14/ff14", "reply_theme", None, clear=True)
        selected = await self.runtime.core_runtime.theme_settings.current("ff14/ff14")
        self.assertEqual(
            (selected.core_theme, selected.module_theme), ("deep_indigo", None)
        )
        rows = (await self.call("read", {})).json()["data"]
        for owner, field, value in (
            ("game_link/core", "image_theme", "unknown"),
            ("ff14/ff14", "reply_theme", None),
        ):
            response = await self.call(
                "update",
                {
                    "module_id": owner,
                    "expected_revision": rows[owner]["revision"],
                    "updates": [{"field": field, "mode": "replace", "value": value}],
                },
            )
            self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.pages.requests, {})
        self.assertEqual(self.runtime._admin_source._proofs, {})

    async def test_reviewed_subscription_gate_bool_grant_and_exact_cas_boundaries(self):
        """Real reviewed composition with isolated storage and offline transport."""
        from ygl_test_subject.modules.ff14.config import ORDINARY_FIELDS

        gate = "ff14_subscriptions_enabled"
        self.assertNotIn(gate, ORDINARY_FIELDS)
        declarations = (await self.call("catalog", {})).json()["data"]["fields"]
        visible = {
            f["name"]
            for f in declarations
            if f["module_id"] == "ff14/ff14" and f["readable"]
        }
        self.assertEqual(
            visible,
            {
                gate,
                "reply_theme",
                "ff14_default_region",
                "ff14_calendar_default_days",
                "ff14_calendar_default_timezone",
                "ff14_calendar_default_delivery_time",
                "ff14_calendar_sync_interval_seconds",
            },
        )
        field = next(f for f in declarations if f["name"] == gate)
        self.assertEqual(field["value_schema"], {"type": "boolean"})
        self.assertIs(field["default"], True)
        self.assertTrue(field["editable"])
        row = (await self.call("read", {})).json()["data"]["ff14/ff14"]
        self.assertIs(row["fields"][gate]["value"], True)
        revision = row["revision"]

        def body(value, module="ff14/ff14", name=gate, mode="replace"):
            update = {"field": name, "mode": mode}
            if mode == "replace":
                update["value"] = value
            return {
                "module_id": module,
                "expected_revision": revision,
                "updates": [update],
            }

        for value in (1, 0, "true", "false", None, {}, []):
            response = await self.call("update", body(value))
            self.assertEqual(response.status_code, 400)
        for payload in (
            body(None, mode="clear"),
            body(False, module="game_link/core"),
            body(False, module="other/mod"),
            body(False, name="ff14_calendar_interval_seconds"),
        ):
            self.assertEqual((await self.call("update", payload)).status_code, 400)
        for headers in (
            {},
            {**self.headers, "Origin": "https://other.test"},
            {"X-API-Key": "synthetic-active-api-key", "Origin": "https://ui.test"},
        ):
            self.assertNotEqual(
                (await self.call("update", body(False), headers=headers)).status_code,
                200,
            )
        self.assertEqual((await self.call("read", {})).json()["data"]["ff14/ff14"], row)
        for value in (False, True):
            old = body(value)
            response = await self.call("update", old)
            self.assertEqual(response.status_code, 200)
            revision = response.json()["data"]["revision"]
            self.assertEqual((await self.call("update", old)).status_code, 409)
            current = (await self.call("read", {})).json()["data"]["ff14/ff14"]
            self.assertEqual(current["revision"], revision)
            self.assertIs(current["fields"][gate]["value"], value)
        self.assertTrue(self.runtime.ready)
        self.assertTrue(self.runtime.core_runtime.started)
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
        self.assertTrue(self.runtime.ready)
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
        self.assertEqual(verified_dashboard_request(facade, path)[1], "synthetic")
        sys.modules["astrbot"].__version__ = "4.29.1"
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

    async def test_font_status_retry_and_authenticated_bounded_upload(self):
        from ygl_test_subject.presentation.bundled_font import (
            FONT_FILENAME,
            FONT_SHA256,
            FONT_SIZE,
        )

        content = (
            Path(__file__).resolve().parents[2]
            / "presentation/assets/fonts"
            / FONT_FILENAME
        ).read_bytes()
        response = await self.call("font-status", {})
        self.assertEqual(response.status_code, 200, response.text)
        status = response.json()["data"]
        self.assertEqual(status["sha256"], FONT_SHA256)
        self.assertEqual(status["size"], FONT_SIZE)
        self.assertNotIn(str(self.temp.name), response.text)
        self.assertEqual((await self.call("font-retry", {})).status_code, 200)
        self.assertEqual(
            (await self.call("font-retry", {"url": "https://evil.test"})).status_code,
            400,
        )
        path = f"/api/v1/plugins/extensions/{PLUGIN_NAME}/admin/font-upload"
        response = await self.client.post(
            path,
            headers=self.headers,
            files={"file": (FONT_FILENAME, content, "application/octet-stream")},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["source"], "canonical")
        for files in (
            {"file": (FONT_FILENAME, b"invalid", "application/octet-stream")},
            {"file": (FONT_FILENAME, b"x" * FONT_SIZE, "application/octet-stream")},
            [
                ("file", (FONT_FILENAME, content, "application/octet-stream")),
                ("extra", ("x", b"x")),
            ],
        ):
            response = await self.client.post(
                path, headers=self.headers, files=files, follow_redirects=True
            )
            self.assertEqual(response.status_code, 400, response.text)
        response = await self.client.post(
            path,
            headers={},
            files={"file": (FONT_FILENAME, content)},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.pages.requests, {})
        self.assertEqual(self.runtime._admin_source._proofs, {})
