"""Offline fixed Host dispatch with actual FF14 product output, not a live Host."""

import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI, Request
from ygl_test_subject.adapters.astrbot.runtime import PLUGIN_NAME
from ygl_test_subject.adapters.astrbot.web_public import project_result

from tests.fixtures.ff14.item_display import lookup_result
from tests.host import test_ff14_pages as page_tests
from tests.host.astrbot_contract import host_contracts


class ItemDisplayHostContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_valid_item_results_pass_fixed_host_dispatch_and_projection(
        self,
    ):
        web, namespace = host_contracts()
        _, runtime, core, pages = page_tests.PublicWebPagesTests()._fixture()
        result = None

        async def invoke(module_id, capability_id, parameters, *, proof):
            self.assertEqual(capability_id, "item.lookup")
            self.assertEqual(parameters, {"query": "90001"})
            binding = core.validator.consume(
                proof, module_id=module_id, capability_id=capability_id, generation=7
            )
            self.assertTrue(core.validator.is_current(proof, binding))
            core.validator.revoke(proof)
            return result

        core.invoke_public_web = invoke
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

        @app.post("/api/plug/{plugin_path:path}")
        async def legacy(plugin_path: str, request: Request):
            username = await namespace["require_dashboard_user"](request)
            return await namespace["_call_plugin_extension"](
                plugin_path, request, username
            )

        try:
            with patch.dict(sys.modules, {"astrbot.api.web": web}):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="https://ui.test"
                ) as client:
                    for counts, warnings in (
                        ((5, 5, 5, 4, 1, 1), False),
                        ((5, 5, 5, 5, 1, 1), False),
                        ((5, 5, 5, 5, 5, 5), False),
                        ((6, 6, 6, 6, 6, 6), True),
                    ):
                        with self.subTest(counts=counts, warnings=warnings):
                            result = await lookup_result(
                                counts, extra_warnings=warnings
                            )
                            response = await client.post(
                                f"/api/plug/{PLUGIN_NAME}/queries/items",
                                headers={
                                    "Authorization": "Bearer " + page_tests._token(),
                                    "Origin": "https://ui.test",
                                },
                                json={"query": "90001"},
                            )
                            self.assertEqual(response.status_code, 200, response.text)
                            payload = json.loads(response.content)
                            self.assertEqual(payload["status"], "ok")
                            self.assertEqual(payload["data"], project_result(result))
        finally:
            pages.close()
