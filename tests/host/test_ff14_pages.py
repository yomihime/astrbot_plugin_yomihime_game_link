"""Public route scope, failure sanitization and exact handler lifecycle."""

import ast
import asyncio
import importlib.util
import inspect
import json
import sys
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.adapters.astrbot.ff14_pages import FF14Pages
from ygl_test_subject.adapters.astrbot.runtime import PLUGIN_NAME, AstrBotRuntime
from ygl_test_subject.adapters.astrbot.web_public import (
    HostPublicWebValidator,
    WebPublicRejected,
    project_result,
    query_parameters,
)
from ygl_test_subject.api.display import (
    DisplayDocument,
    FieldsBlock,
    ImageBlock,
    Link,
    LinksBlock,
    Privacy,
    TableBlock,
    TextBlock,
)
from ygl_test_subject.api.results import (
    CapabilityResult,
    ErrorCode,
    ErrorDetail,
    ResultStatus,
)
from ygl_test_subject.core.ports import PublicWebBinding

HOST_SOURCE = Path(
    "C:/Users/eveni/.astrbot_launcher/instances/6775391e-2fb9-4f7a-aeb5-401fcc4debc8/core/astrbot"
)


def _host_module(relative):
    spec = importlib.util.spec_from_file_location(
        "ygl_host_" + relative.replace("/", "_"), HOST_SOURCE / relative
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _host_contracts():
    """Use actual public DTO and unchanged Host auth/dispatch AST without startup."""
    import re
    from typing import Any, cast
    from urllib.parse import quote

    import jwt
    from fastapi import Request
    from starlette.responses import JSONResponse

    web = _host_module("api/web.py")
    responses = _host_module("dashboard/responses.py")
    page_auth = _host_module("dashboard/plugin_page_auth.py")
    namespace = {
        "__name__": "host_contract_probe",
        "jwt": jwt,
        "Request": Request,
        "JSONResponse": JSONResponse,
        "Any": Any,
        "cast": cast,
        "re": re,
        "quote": quote,
        "ApiError": responses.ApiError,
        "error": responses.error,
        "PluginPageAuth": page_auth.PluginPageAuth,
        "PluginRequest": web.PluginRequest,
        "bind_request_context": web.bind_request_context,
        "DASHBOARD_JWT_COOKIE_NAME": "astrbot_dashboard_jwt",
    }
    auth = ast.parse(
        (HOST_SOURCE / "dashboard/api/auth.py").read_text(encoding="utf-8")
    )
    names = {
        "_get_dashboard_state_username",
        "_extract_dashboard_jwt",
        "require_dashboard_user",
    }
    exec(
        compile(
            ast.Module(
                body=[
                    node for node in auth.body if getattr(node, "name", None) in names
                ],
                type_ignores=[],
            ),
            "actual_host_auth",
            "exec",
        ),
        namespace,
    )
    server = ast.parse(
        (HOST_SOURCE / "dashboard/server.py").read_text(encoding="utf-8")
    )
    owner = next(
        node
        for node in server.body
        if isinstance(node, ast.ClassDef)
        and any(
            getattr(child, "name", None) == "auth_middleware" for child in node.body
        )
    )
    methods = [
        node
        for node in owner.body
        if getattr(node, "name", None)
        in {"auth_middleware", "_validate_dashboard_token", "_extract_dashboard_jwt"}
    ]
    cls = ast.ClassDef(
        name="HostAuth", bases=[], keywords=[], body=methods, decorator_list=[]
    )
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])),
            "actual_host_middleware",
            "exec",
        ),
        namespace,
    )

    async def no_rate_limit(*_):
        return None

    namespace["HostAuth"]._apply_auth_rate_limit = no_rate_limit
    plugins = ast.parse(
        (HOST_SOURCE / "dashboard/api/plugins.py").read_text(encoding="utf-8")
    )
    names = {
        "_normalize_plugin_api_route",
        "_plugin_api_route_pattern",
        "_match_registered_web_api",
        "_plugin_extension_legacy_path",
        "_call_plugin_extension",
    }

    async def run_maybe_async(callback):
        value = callback()
        return await value if inspect.isawaitable(value) else value

    namespace["run_maybe_async"] = run_maybe_async
    exec(
        compile(
            ast.Module(
                body=[
                    node
                    for node in plugins.body
                    if getattr(node, "name", None) in names
                ],
                type_ignores=[],
            ),
            "actual_host_dispatch",
            "exec",
        ),
        namespace,
    )
    return web, namespace


def _token(**claims):
    import jwt

    return jwt.encode(
        {"username": "synthetic", "exp": int(time.time()) + 60, **claims},
        "synthetic-test-secret-not-host-at-least-32-bytes",
        algorithm="HS256",
    )


def _request(
    web,
    endpoint="items",
    *,
    token=None,
    origin="https://ui.test",
    body=None,
    extra=(),
    path=None,
    method="POST",
    username="synthetic",
):
    from starlette.requests import Request

    headers = [(b"content-type", b"application/json")]
    if token is not False:
        headers.append(
            (b"authorization", ("Bearer " + (token or _token())).encode("ascii"))
        )
    if origin is not False:
        headers.append((b"origin", origin.encode("ascii")))
    headers.extend(extra)
    raw = json.dumps({"query": "100"} if body is None else body).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": method,
            "scheme": "https",
            "path": path or f"/api/plug/{PLUGIN_NAME}/queries/{endpoint}",
            "query_string": b"",
            "headers": headers,
            "server": ("ui.test", 443),
            "client": ("127.0.0.1", 123),
        },
        receive,
    )
    return web.PluginRequest(request, plugin_name=PLUGIN_NAME, username=username)


class _QueryCore:
    def __init__(self, validator, status=ResultStatus.SUCCESS):
        self.validator, self.status = validator, status
        self.calls = []
        self.hook = None

    async def invoke_public_web(self, module_id, capability_id, parameters, *, proof):
        binding = self.validator.consume(
            proof, module_id=module_id, capability_id=capability_id, generation=7
        )
        assert self.validator.is_current(proof, binding) is True
        self.calls.append((module_id, capability_id, parameters, binding))
        if self.hook:
            await self.hook()
        self.validator.revoke(proof)
        return CapabilityResult(
            "synthetic",
            self.status,
            DisplayDocument("Synthetic", "public", (TextBlock("plain"),)),
        )


class PublicWebPagesTests(unittest.IsolatedAsyncioTestCase):
    def _fixture(self, status=ResultStatus.SUCCESS, config=None):
        web, _ = _host_contracts()
        runtime = AstrBotRuntime(
            _Context(),
            plugin_root="unused",
            data_dir="unused",
            config=config
            if config is not None
            else {"web_public_origin": "https://ui.test"},
        )
        runtime._generation = 13
        runtime._ready = True
        validator = HostPublicWebValidator(13, runtime._web_current)
        core = _QueryCore(validator, status)
        runtime._core = core
        validator.attach(core)
        runtime._web_validator = validator
        context = _Context()
        pages = FF14Pages(context, runtime)
        pages.register()
        return web, runtime, core, pages

    async def _call(self, web, pages, request, endpoint="items"):
        with (
            patch.dict(sys.modules, {"astrbot.api.web": web}),
            web.bind_request_context(request),
        ):
            return await pages._registrations[
                4 + list(("items", "character", "calendar")).index(endpoint)
            ][1]()

    async def test_three_exact_queries_and_unconfigured_web_leave_chat_runtime_ready(
        self,
    ):
        web, runtime, core, pages = self._fixture()
        for endpoint, body in (
            ("items", {"query": "100"}),
            (
                "character",
                {
                    "region": "global",
                    "server": "Cerberus",
                    "character": "Synthetic Hero",
                },
            ),
            ("calendar", {"region": "cn", "days": 7, "timezone": "UTC"}),
        ):
            response = await self._call(
                web, pages, _request(web, endpoint, body=body), endpoint
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(json.loads(response.body)["data"]["status"], "success")
        self.assertEqual(
            [call[1] for call in core.calls],
            ["item.lookup", "ff14.logs.character", "ff14.calendar.query"],
        )
        self.assertEqual(core.calls[1][2]["realm"], "global")
        self.assertEqual(core.calls[1][2]["metric"], "rdps")
        self.assertEqual(core.calls[0][3].generation, 7)  # Distinct from Host's 13.
        pages.close()
        for config in ({}, {"web_public_origin": "https://private@bad.test"}):
            web, runtime, core, pages = self._fixture(config=config)
            response = await self._call(web, pages, _request(web))
            self.assertEqual(response.status_code, 503)
            self.assertEqual(core.calls, [])
            self.assertTrue(runtime.ready)
            self.assertNotIn("private", json.dumps(runtime.public_web_status()))
            pages.close()

    async def test_legacy_request_auth_origin_method_path_and_json_rejections_query_zero(
        self,
    ):
        web, runtime, core, pages = self._fixture()
        cases = [
            {"token": False},
            {"username": None},
            {"origin": False},
            {"origin": "https://other.test"},
            {"extra": ((b"authorization", b"Bearer duplicate"),)},
            {"extra": ((b"origin", b"https://ui.test"),)},
            {"extra": ((b"x-api-key", b"synthetic"),)},
            {"token": _token(exp=True)},
            {"token": _token(exp=1.5)},
            {"token": _token(exp=1)},
            {"method": "GET"},
            {"path": f"/api/plug/{PLUGIN_NAME}/resource/items"},
            {
                "body": {
                    "query": "100",
                    "path": f"/api/plug/{PLUGIN_NAME}/queries/items",
                    "username": "synthetic",
                }
            },
            {"body": {"query": "x" * 4096}},
        ]
        for options in cases:
            with self.subTest(options=options):
                response = await self._call(web, pages, _request(web, **options))
                self.assertNotEqual(response.status_code, 200)
        self.assertEqual(core.calls, [])
        self.assertFalse(runtime._web_validator._requests)
        pages.close()

    async def test_three_result_statuses_reject_page_host_replacement_and_expiry_after_await(
        self,
    ):
        for status in (
            ResultStatus.SUCCESS,
            ResultStatus.PARTIAL_SUCCESS,
            ResultStatus.NEEDS_SELECTION,
        ):
            for mode in ("page", "host", "core", "expiry"):
                with self.subTest(status=status, mode=mode):
                    web, runtime, core, pages = self._fixture(status)

                    async def change():
                        if mode == "page":
                            pages.close()
                        elif mode == "host":
                            runtime._generation += 1
                        elif mode == "core":
                            runtime._core = object()
                        else:
                            runtime._web_validator._wall_clock = lambda: (
                                time.time() + 120
                            )

                    core.hook = change
                    if mode == "expiry":
                        with patch(
                            "ygl_test_subject.adapters.astrbot.runtime.time",
                            lambda: time.time() + 120,
                        ):
                            response = await self._call(web, pages, _request(web))
                    else:
                        response = await self._call(web, pages, _request(web))
                    self.assertNotEqual(response.status_code, 200)
                    self.assertEqual(json.loads(response.body)["data"], {})
                    pages.close()

    async def test_body_wait_uses_entry_deadline_and_page_close_stops_mint(self):
        web, runtime, core, pages = self._fixture()
        request = _request(web)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def delayed_body():
            entered.set()
            await release.wait()
            return b'{"query":"100"}'

        request.body = delayed_body
        pending = asyncio.create_task(self._call(web, pages, request))
        await entered.wait()
        pages.close()
        release.set()
        response = await pending
        self.assertNotEqual(response.status_code, 200)
        self.assertEqual(core.calls, [])
        self.assertFalse(runtime._web_validator._requests)

    async def test_body_read_cannot_renew_entry_deadline_and_is_bounded(self):
        web, runtime, core, pages = self._fixture()
        request = _request(web)
        now = [time.monotonic()]
        runtime._web_validator._clock = lambda: now[0]

        async def late_body():
            now[0] += 31
            return b'{"query":"100"}'

        request.body = late_body
        with patch(
            "ygl_test_subject.adapters.astrbot.runtime.monotonic", lambda: now[0]
        ):
            response = await self._call(web, pages, request)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(core.calls, [])
        self.assertFalse(runtime._web_validator._requests)
        pages.close()

        web, runtime, core, pages = self._fixture()
        request = _request(web)
        expired = asyncio.Event()

        async def blocked_body():
            try:
                await asyncio.Event().wait()
            finally:
                expired.set()

        request.body = blocked_body
        with patch.object(runtime, "public_web_remaining", return_value=0.01):
            response = await asyncio.wait_for(
                self._call(web, pages, request), timeout=1
            )
        await asyncio.wait_for(expired.wait(), timeout=1)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(core.calls, [])
        self.assertFalse(runtime._web_validator._requests)
        pages.close()

    async def test_actual_host_asgi_jwt_redirect_preserves_post_and_rejects_other_authority(
        self,
    ):
        import httpx
        from fastapi import FastAPI, Request

        web, namespace = _host_contracts()
        _, runtime, core, pages = self._fixture()
        app = FastAPI()
        app.state.core_lifecycle = SimpleNamespace(star_context=pages._context)
        app.state.jwt_secret = "synthetic-test-secret-not-host-at-least-32-bytes"
        auth = namespace["HostAuth"]()
        auth._jwt_secret = app.state.jwt_secret

        @app.middleware("http")
        async def actual_auth(request, call_next):
            request.state.dashboard_g = SimpleNamespace(username=None)
            response = await auth.auth_middleware(request)
            return response if response is not None else await call_next(request)

        @app.post("/api/v1/plugins/extensions/{plugin_path:path}")
        async def v1(plugin_path: str, request: Request):
            # Deliberately admit the broad v1 identity set. The actual legacy
            # Host middleware must still verify ordinary JWT after redirect.
            return await namespace["_call_plugin_extension"](
                plugin_path, request, "synthetic-v1"
            )

        @app.post("/api/plug/{plugin_path:path}")
        async def legacy(plugin_path: str, request: Request):
            username = await namespace["require_dashboard_user"](request)
            return await namespace["_call_plugin_extension"](
                plugin_path, request, username
            )

        path = f"/api/v1/plugins/extensions/{PLUGIN_NAME}/queries/items"
        legacy_path = f"/api/plug/{PLUGIN_NAME}/queries/items"
        headers = {"Authorization": "Bearer " + _token(), "Origin": "https://ui.test"}
        with patch.dict(sys.modules, {"astrbot.api.web": web}):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://ui.test"
            ) as client:
                redirect = await client.post(
                    path, headers=headers, json={"query": "100"}
                )
                self.assertEqual(redirect.status_code, 307)
                self.assertEqual(redirect.headers["location"], legacy_path)
                self.assertEqual(core.calls, [])
                response = await client.post(
                    path, headers=headers, json={"query": "100"}, follow_redirects=True
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(core.calls[0][2], {"query": "100"})
                cases = [
                    {
                        "Authorization": "Bearer "
                        + _token(
                            token_type="plugin_page_asset",
                            plugin_name=PLUGIN_NAME,
                            page_name="ff14",
                        ),
                        "Origin": "https://ui.test",
                    },
                    {
                        "Authorization": "Bearer synthetic-valid-api-key",
                        "Origin": "https://ui.test",
                    },
                    {
                        "X-API-Key": "synthetic-valid-api-key",
                        "Origin": "https://ui.test",
                    },
                    {"Authorization": "Bearer invalid", "Origin": "https://ui.test"},
                    {**headers, "X-API-Key": "synthetic-mixed"},
                    {**headers, "Origin": "https://other.test"},
                ]
                for candidate in cases:
                    response = await client.post(
                        path,
                        headers=candidate,
                        json={"query": "100"},
                        follow_redirects=True,
                    )
                    self.assertNotEqual(response.status_code, 200)
                response = await client.post(
                    path,
                    headers={
                        "Origin": "https://ui.test",
                        "Cookie": "astrbot_dashboard_jwt=" + _token(),
                    },
                    json={"query": "100"},
                    follow_redirects=True,
                )
                self.assertNotEqual(response.status_code, 200)
                duplicate = [
                    ("Authorization", headers["Authorization"]),
                    ("Authorization", headers["Authorization"]),
                    ("Origin", "https://ui.test"),
                ]
                response = await client.post(
                    legacy_path, headers=duplicate, json={"query": "100"}
                )
                self.assertNotEqual(response.status_code, 200)
                response = await client.post(
                    path,
                    headers=headers,
                    json={
                        "query": "100",
                        "path": legacy_path,
                        "username": "synthetic",
                        "Location": legacy_path,
                    },
                    follow_redirects=True,
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(len(core.calls), 1)
        pages.close()

    def test_pure_projection_statuses_blocks_privacy_resource_and_byte_bounds(self):
        document = DisplayDocument(
            "public",
            "plain",
            (
                TextBlock("text"),
                FieldsBlock({"count": 1}),
                TableBlock(("value",), (("plain",),)),
                LinksBlock((Link("source", "https://source.test/public"),)),
            ),
        )
        for status in (
            ResultStatus.SUCCESS,
            ResultStatus.PARTIAL_SUCCESS,
            ResultStatus.NEEDS_SELECTION,
        ):
            result = CapabilityResult("synthetic", status, document)
            data = project_result(result)
            self.assertEqual(data["status"], status.value)
            self.assertEqual(
                [block["kind"] for block in data["document"]["blocks"]],
                ["text", "fields", "table", "links"],
            )
        error = CapabilityResult(
            "error",
            ResultStatus.ERROR,
            error=ErrorDetail(ErrorCode.AUTH_REQUIRED, "private detail"),
        )
        data = project_result(error)
        self.assertEqual(data["error"]["code"], "auth_required")
        self.assertNotIn("private detail", json.dumps(data))
        private = DisplayDocument(
            "private", "private", (TextBlock("private"),), privacy=Privacy.PRIVATE
        )
        rejected = [
            CapabilityResult(
                "private", ResultStatus.SUCCESS, private, privacy=Privacy.PRIVATE
            ),
            object(),
            CapabilityResult(
                "resource",
                ResultStatus.SUCCESS,
                DisplayDocument("r", "r", (ImageBlock("a" * 64, "plain"),)),
            ),
            CapabilityResult(
                "huge",
                ResultStatus.SUCCESS,
                DisplayDocument("large", "plain", (TextBlock("x" * (256 * 1024)),)),
            ),
            CapabilityResult(
                "path",
                ResultStatus.SUCCESS,
                DisplayDocument("r", "r", (FieldsBlock({"path": "private"}),)),
            ),
        ]
        for result in rejected:
            with self.assertRaises(WebPublicRejected):
                project_result(result)
        bad = CapabilityResult("mutated", ResultStatus.SUCCESS, document)
        object.__setattr__(bad.document, "title", object())
        with self.assertRaises(WebPublicRejected):
            project_result(bad)

    def test_owned_proof_binding_anonymous_key_core_isolation_and_limits(self):
        web, runtime, core, pages = self._fixture()
        token = _token()
        states = [
            runtime.begin_public_web(
                _request(web, token=token),
                "items",
                f"/api/plug/{PLUGIN_NAME}/queries/items",
            )
            for _ in range(2)
        ]
        bindings = []
        for validator, ticket, *_ in states:
            with self.assertRaises(WebPublicRejected):
                validator.consume(
                    ticket,
                    module_id="ff14/ff14",
                    capability_id="item.lookup",
                    generation=1,
                )
            validator.mint(ticket)
            binding = validator.consume(
                ticket, module_id="ff14/ff14", capability_id="item.lookup", generation=1
            )
            bindings.append(binding)
            self.assertFalse(validator.is_current(ticket, replace(binding)))
            self.assertFalse(
                validator.is_current(
                    PublicWebBinding(
                        "ff14/ff14", "item.lookup", 1, "fake", time.monotonic() + 30
                    ),
                    binding,
                )
            )
            with self.assertRaises(WebPublicRejected):
                validator.consume(
                    ticket,
                    module_id="ff14/ff14",
                    capability_id="item.lookup",
                    generation=1,
                )
        self.assertEqual(bindings[0].bearer_key, bindings[1].bearer_key)
        self.assertNotIn(token, repr(runtime._web_validator._requests))
        other = HostPublicWebValidator(13, lambda *_: True)
        other.attach(object())
        proof = other.begin("items", token, int(time.time()) + 60)
        other.mint(proof)
        binding = other.consume(
            proof, module_id="ff14/ff14", capability_id="item.lookup", generation=1
        )
        self.assertNotEqual(binding.bearer_key, bindings[0].bearer_key)
        self.assertFalse(other.is_current(states[0][1], bindings[0]))
        pages.close()
        for state in states:
            runtime.finish_public_web(state)
        other.close()
        for body in (
            b'{"query":"ok","query":"other"}',
            b"\xff",
            b'{"query":"' + b"x" * 4096 + b'"}',
        ):
            with self.assertRaises(WebPublicRejected):
                query_parameters("items", body)
        for days in (0, 31, True):
            with self.assertRaises(WebPublicRejected):
                query_parameters(
                    "calendar",
                    json.dumps(
                        {"region": "cn", "days": days, "timezone": "UTC"}
                    ).encode(),
                )
        with self.assertRaises(WebPublicRejected):
            query_parameters(
                "calendar", b'{"region":"cn","days":7,"timezone":"not/a/zone"}'
            )


class _Context:
    def __init__(self, *, fail_second=False):
        self.registered_web_apis = []
        self.fail_second = fail_second

    def register_web_api(self, route, handler, methods, description):
        self.registered_web_apis.append((route, handler, methods, description))
        if self.fail_second and len(self.registered_web_apis) == 2:
            raise RuntimeError("private host detail")


class _PublicRuntime:
    def __init__(self):
        self.calls = []
        self.fail = False

    async def public_ff14_page_state(self, *, include_values=False):
        self.calls.append(include_values)
        if self.fail:
            raise RuntimeError("private credential and path")
        return {"schema_version": 1, "ordinary_config": {"state": "unknown"}}

    def public_module_catalog(self):
        self.calls.append("catalog")
        if self.fail:
            raise RuntimeError("private credential and path")
        return {"schema_version": 1, "catalog_revision": 0, "modules": []}

    def public_web_status(self):
        return {
            "schema_version": 1,
            "configuration": "unconfigured",
            "origin": None,
            "entry_ready": False,
        }

    @property
    def core_runtime(self):
        raise AssertionError("page adapter must not read Core")


class FF14PagesTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_four_get_and_three_post_handlers_preserve_projections(self):
        context, runtime = _Context(), _PublicRuntime()
        pages = FF14Pages(context, runtime)
        pages.register()
        pages.register()
        self.assertEqual(len(context.registered_web_apis), 7)
        self.assertEqual(
            [(entry[0], entry[2]) for entry in context.registered_web_apis],
            [
                (f"/{PLUGIN_NAME}/{endpoint}", ["GET"])
                for endpoint in ("overview", "settings", "catalog", "web-status")
            ]
            + [
                (f"/{PLUGIN_NAME}/queries/{endpoint}", ["POST"])
                for endpoint in ("items", "character", "calendar")
            ],
        )
        for endpoint, entry in zip(
            ("overview", "settings", "catalog"), context.registered_web_apis
        ):
            self.assertEqual(entry[0], f"/{PLUGIN_NAME}/{endpoint}")
            self.assertEqual(entry[2], ["GET"])
            result = await entry[1]()
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["data"]["schema_version"], 1)
        self.assertEqual(runtime.calls, [False, True, "catalog"])
        pages.close()
        self.assertEqual(context.registered_web_apis, [])

    async def test_exception_output_contains_only_fixed_error(self):
        context, runtime = _Context(), _PublicRuntime()
        pages = FF14Pages(context, runtime)
        pages.register()
        runtime.fail = True
        result = await context.registered_web_apis[0][1]()
        self.assertEqual(
            result,
            {
                "status": "error",
                "message": "状态暂时无法读取，请稍后重试。",
                "data": {},
            },
        )
        self.assertNotIn("private", str(result))
        self.assertEqual(await context.registered_web_apis[2][1](), result)
        pages.close()

    async def test_catalog_close_fences_sync_projection_and_old_handlers(self):
        context, runtime = _Context(), _PublicRuntime()
        pages = FF14Pages(context, runtime)
        pages.register()
        old = context.registered_web_apis[2][1]
        project = runtime.public_module_catalog

        def close_during_projection():
            data = project()
            pages.close()
            return data

        runtime.public_module_catalog = close_during_projection
        self.assertEqual(
            (await old())["message"], "页面状态读取已停止，请重新打开插件页面。"
        )
        pages.register()
        self.assertEqual((await old())["status"], "error")
        self.assertEqual(runtime.calls, ["catalog"])
        pages.close()

    async def test_cleanup_keeps_replacements_and_fences_captured_old_handler(self):
        context, runtime = _Context(), _PublicRuntime()
        pages = FF14Pages(context, runtime)
        pages.register()
        old = context.registered_web_apis[0][1]

        def replacement():
            return None

        context.registered_web_apis[0] = (
            f"/{PLUGIN_NAME}/overview",
            replacement,
            ["GET"],
            "replacement",
        )
        context.registered_web_apis.append(
            ("/other/plugin", replacement, ["GET"], "unrelated")
        )
        pages.close()
        self.assertEqual(len(context.registered_web_apis), 2)
        self.assertEqual((await old())["status"], "error")
        pages.register()
        self.assertEqual((await old())["status"], "error")
        self.assertEqual(runtime.calls, [])
        pages.close()

    async def test_close_fences_late_success_and_late_error(self):
        import asyncio

        for fails in (False, True):
            with self.subTest(fails=fails):
                entered, release = asyncio.Event(), asyncio.Event()

                class Runtime:
                    async def public_ff14_page_state(self, **_):
                        entered.set()
                        await release.wait()
                        if fails:
                            raise RuntimeError("private detail")
                        return {"schema_version": 2}

                pages = FF14Pages(_Context(), Runtime())
                pages.register()
                task = asyncio.create_task(pages._registrations[0][1]())
                await entered.wait()
                pages.close()
                pages.register()
                release.set()
                result = await task
                self.assertEqual(
                    result,
                    {
                        "status": "error",
                        "message": "页面状态读取已停止，请重新打开插件页面。",
                        "data": {},
                    },
                )
                pages.close()

    def test_failed_registration_rolls_back_even_after_host_insertion(self):
        context = _Context(fail_second=True)
        pages = FF14Pages(context, _PublicRuntime())
        with self.assertRaisesRegex(RuntimeError, "private host"):
            pages.register()
        self.assertEqual(context.registered_web_apis, [])
        pages.close()
