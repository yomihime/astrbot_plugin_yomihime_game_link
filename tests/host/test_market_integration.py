"""Isolated real Runtime/Core/SourceHttp/cache composition with fixture upstream."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
import unittest
from dataclasses import replace
from time import monotonic
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI, Request
from ygl_test_subject.adapters.astrbot.ff14_pages import FF14Pages
from ygl_test_subject.adapters.astrbot.runtime import PLUGIN_NAME
from ygl_test_subject.adapters.astrbot.web_public import query_parameters
from ygl_test_subject.api.administration import AdminAuthorizationDenied
from ygl_test_subject.api.services import HttpResponse

from tests.host import test_astrbot_runtime as runtime_fixture
from tests.host import test_ff14_pages as page_fixture
from tests.host.astrbot_contract import host_contracts
from tests.modules.ff14.test_market import (
    DCS,
    REGION_IDS,
    WORLDS,
    aggregated,
    currently,
    listing,
)


class FixtureTransport(runtime_fixture._IdleTransport):
    """All price/directory rows are marked synthetic; no live HTTP exists."""

    def __init__(self):
        super().__init__()
        self.requests = []
        self.callback = None

    async def request(self, request):
        self.requests.append(request)
        if self.callback:
            body = await self.callback(request)
            if body is not None:
                return (
                    body
                    if isinstance(body, HttpResponse)
                    else HttpResponse(200, {}, json.dumps(body).encode())
                )
        if request.path == "/api/v2/worlds":
            body = WORLDS
        elif request.path == "/api/v2/data-centers":
            body = DCS
        elif request.source_id == "xivapi_items":
            body = {
                "results": [
                    dict(row_id=i, fields={"Name": f"Synthetic Item {i}"})
                    for i in (44091, 44092)
                ]
            }
        elif "/aggregated/" in request.path:
            region = request.path.split("/")[-2]
            level = "region"
            if region.isdigit():
                region = next(
                    r for r, world in REGION_IDS.items() if world == int(region)
                )
                level = "world"
            elif region.startswith("Synthetic"):
                region = next(dc["region"] for dc in DCS if dc["name"] == region)
                level = "dc"
            body = aggregated(region, level=level)
        else:
            body = currently(
                rows=[listing(100 + i, hq=True, ordinal=i) for i in range(6)]
            )
            if request.path.split("/")[-2].isdigit():
                body.pop("regionName")
                body["worldID"] = int(request.path.split("/")[-2])
        return HttpResponse(200, {}, json.dumps(body).encode())


class MarketHostIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_reply_uses_natural_renderer_and_verified_price_context(self):
        await self.start()
        event = runtime_fixture._Event()
        event.message = "/ygl ff14 market 44091 region=cn quality=hq intent=min"
        self.assertIsNone(await self.runtime.handle_event(event))
        reply = self.context.calls[-1][1].chain[0].text
        self.assertIn("SyntheticChina", reply)
        self.assertIn("本次返回数据中的最低挂牌", reply)
        self.assertIn("Universalis", reply)
        for prefix in ("标题:", "对象:", "文本:", "字段:"):
            self.assertNotIn(prefix, reply)
        self.assertNotIn("synthetic-private-seller", reply)
        self.assertEqual(len(self.transport.requests), 3)

    # Reuse temporary plugin/DB composition without inheriting its test methods.
    setUp = runtime_fixture.AstrBotRuntimeTests.setUp
    tearDown = runtime_fixture.AstrBotRuntimeTests.tearDown
    _runtime = runtime_fixture.AstrBotRuntimeTests._runtime

    async def start(self):
        self.transport = FixtureTransport()
        self.context = runtime_fixture._Context()
        self.runtime = self._runtime(
            self.context,
            http_transport_factory=lambda: self.transport,
            config={"web_public_origin": "https://ui.test"},
        )
        await self.runtime.initialize()
        self.addAsyncCleanup(self.runtime.terminate)
        self.web, _ = page_fixture._host_contracts()
        self.token = page_fixture._token()

    async def public(self, body, *, token=None):
        request = page_fixture._request(
            self.web, "market", token=token or self.token, body=body
        )
        state = self.runtime.begin_public_web(
            request, "market", f"/api/plug/{PLUGIN_NAME}/queries/market"
        )
        try:
            return await self.runtime.invoke_public_web(
                state, "market", query_parameters("market", json.dumps(body).encode())
            )
        finally:
            self.runtime.finish_public_web(state)

    async def test_registered_handler_real_http_cache_sdk_and_public_projection(self):
        await self.start()
        parameters = dict(
            query="44091", server="90001", quality="hq", intent="listings"
        )
        first = await self.public(parameters)
        second = await self.public(parameters)
        self.assertEqual(first["status"], "success")
        facts = first["model_facts"]["market"]
        self.assertEqual(
            (facts["scope"]["kind"], facts["scope"]["target"], facts["quality"]),
            ("world", 90001, "hq"),
        )
        self.assertTrue(facts["truncated"])
        self.assertFalse(facts["coverage"][0]["cached"])
        self.assertTrue(second["model_facts"]["market"]["coverage"][0]["cached"])
        self.assertEqual(len(self.transport.requests), 3)
        self.assertLessEqual(len(first["document"]["blocks"]), 32)
        self.assertNotIn("public_session", json.dumps(second))
        self.assertTrue(self.runtime.core_runtime.database is not None)

    async def test_fixed_host_auth_dispatch_real_market_handler_and_protocol_denials(
        self,
    ):
        await self.start()
        web, namespace = host_contracts()
        pages = FF14Pages(page_fixture._Context(), self.runtime)
        pages.register()
        self.addCleanup(pages.close)
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

        headers = {
            "Authorization": "Bearer " + self.token,
            "Origin": "https://ui.test",
            "Content-Type": "application/json",
        }
        route = f"/api/plug/{PLUGIN_NAME}/queries/market"
        with patch.dict(sys.modules, {"astrbot.api.web": web}):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://ui.test"
            ) as client:
                response = await client.post(
                    route,
                    headers=headers,
                    json={"query": "44091", "quality": "hq", "intent": "min"},
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["data"]["status"], "success")
                pending = await client.post(
                    route, headers=headers, json={"query": "Synthetic Item"}
                )
                selection = pending.json()["data"]["model_facts"]["selection"]
                for body in (
                    '{"query":"44091","query":"44092"}',
                    '{"query":NaN}',
                    "[]",
                    "x" * 4097,
                ):
                    rejected = await client.post(route, headers=headers, content=body)
                    self.assertEqual(rejected.status_code, 400, rejected.text)
                wrong_origin = await client.post(
                    route,
                    headers={**headers, "Origin": "https://other.test"},
                    json={"query": "44091"},
                )
                self.assertEqual(wrong_origin.status_code, 403)
                no_auth = await client.post(
                    route,
                    headers={"Origin": "https://ui.test"},
                    json={"query": "44091"},
                )
                self.assertEqual(no_auth.status_code, 401)
                selected = await client.post(
                    route,
                    headers=headers,
                    json={
                        "selection": {
                            "batch_id": selection["batch_id"],
                            "generation": selection["generation"],
                            "item_id": 44091,
                        }
                    },
                )
                self.assertEqual(selected.json()["data"]["status"], "success")

    async def test_item_selection_session_isolation_replay_and_invalid_new_object(self):
        await self.start()
        candidate = await self.public(
            dict(query="Synthetic Item", quality="hq", intent="min")
        )
        self.assertEqual(candidate["status"], "needs_selection")
        selection = candidate["model_facts"]["selection"]
        choice = {
            "selection": {key: selection[key] for key in ("batch_id", "generation")}
        }
        choice["selection"]["item_id"] = 44091
        other = await self.public(
            choice, token=page_fixture._token(username="other-session")
        )
        self.assertEqual(other["error"]["code"], "parameter_error")
        selected = await self.public(choice)
        self.assertEqual(selected["status"], "success")
        self.assertEqual(selected["model_facts"]["market"]["quality"], "hq")
        self.assertEqual(selected["model_facts"]["market"]["query"], "44091")
        self.assertEqual(
            (await self.public(choice))["error"]["code"], "parameter_error"
        )
        candidate = await self.public({"query": "Synthetic Item"})
        selection = candidate["model_facts"]["selection"]
        bad = await self.public({"query": "44091", "owner": "forged"})
        self.assertEqual(bad["error"]["code"], "parameter_error")
        choice["selection"].update(
            batch_id=selection["batch_id"], generation=selection["generation"]
        )
        self.assertEqual(
            (await self.public(choice))["error"]["code"], "parameter_error"
        )

    async def test_command_tail_quotes_natural_and_choose_use_same_handler(self):
        await self.start()
        event = runtime_fixture._Event()
        event.message = (
            '/ygl ff14 market 44091 server="SyntheticChina" quality=hq intent=listings'
        )
        await self.runtime.handle_event(event)
        self.assertIn("hq", self.context.calls[-1][1].chain[0].text)
        event.message = (
            '/ygl ff14 market 查一下"SyntheticChina"的"Synthetic Item"多少钱'
        )
        await self.runtime.handle_event(event)
        text = self.context.calls[-1][1].chain[0].text
        self.assertIn("候选", text)
        command = next(
            line for line in text.splitlines() if "/ygl ff14 market choose" in line
        )
        event.message = command.replace("<物品ID>", "44091").strip()
        await self.runtime.handle_event(event)
        self.assertIn("44091", self.context.calls[-1][1].chain[0].text)

    async def test_market_late_result_cannot_publish_after_new_query(self):
        await self.start()
        entered, release = asyncio.Event(), asyncio.Event()

        async def callback(request):
            if "/aggregated/" in request.path and request.path.endswith("44091"):
                entered.set()
                await release.wait()
            return None

        self.transport.callback = callback
        first = asyncio.create_task(self.public({"query": "44091"}))
        await entered.wait()
        # This accepted JSON object is invalid but must establish a newer query.
        newer = await self.public({"query": "44092", "quality": None})
        self.assertEqual(newer["error"]["code"], "parameter_error")
        release.set()
        self.assertEqual((await first)["error"]["code"], "parameter_error")

    async def test_directory_and_name_late_results_are_fenced_by_handler(self):
        for stage in ("directory", "name"):
            with self.subTest(stage=stage):
                await self.start()
                entered, release = asyncio.Event(), asyncio.Event()
                blocked = False

                async def callback(request):
                    nonlocal blocked
                    matches = (
                        request.path == "/api/v2/worlds"
                        if stage == "directory"
                        else request.source_id == "xivapi_items"
                    )
                    if matches and not blocked:
                        blocked = True
                        entered.set()
                        await release.wait()
                    return None

                self.transport.callback = callback
                first = asyncio.create_task(self.public({"query": "Synthetic Item"}))
                await entered.wait()
                newer = await self.public({"query": "44091", "quality": None})
                self.assertEqual(newer["error"]["code"], "parameter_error")
                release.set()
                self.assertEqual((await first)["error"]["code"], "parameter_error")
                await self.runtime.terminate()

    async def test_handler_config_snapshot_fixed_selection_and_new_query_new_defaults(
        self,
    ):
        await self.start()
        from tests.modules.ff14.test_query_resolution import _Config

        config = (
            _Config()
        )  # Explicit synthetic composite snapshot, not a Host admin path.
        handler = (
            self.runtime.core_runtime.registry.snapshot()
            .module("ff14/ff14")
            .handlers.capabilities["ff14.market.query"]
        )
        handler.services = replace(handler.services, config=config)
        candidate = await self.public(
            {"query": "Synthetic Item", "quality": "hq", "intent": "min"}
        )
        choice = candidate["model_facts"]["selection"]
        config.region, config.core_revision, config.module_revision = "global", 7, 8
        selected = await self.public(
            {
                "selection": dict(
                    batch_id=choice["batch_id"],
                    generation=choice["generation"],
                    item_id=44091,
                )
            }
        )
        facts = selected["model_facts"]["market"]
        self.assertEqual(
            (
                facts["scope"]["regions"],
                facts["core_revision"],
                facts["module_revision"],
            ),
            (["China"], 2, 3),
        )
        self.assertEqual(config.calls, 1)
        fresh = (await self.public({"query": "44091"}))["model_facts"]["market"]
        self.assertEqual(
            (fresh["core_revision"], fresh["module_revision"], len(fresh["coverage"])),
            (7, 8, 4),
        )
        self.assertEqual(config.calls, 2)

    async def test_handler_deadline_includes_config_and_no_late_market_request(self):
        await self.start()
        from tests.modules.ff14.test_query_resolution import _Config

        now = [monotonic()]

        class Config(_Config):
            async def current(self):
                snapshot = await super().current()
                now[0] += 31
                return snapshot

        handler = (
            self.runtime.core_runtime.registry.snapshot()
            .module("ff14/ff14")
            .handlers.capabilities["ff14.market.query"]
        )
        handler.clock = handler.source.clock = lambda: now[0]
        handler.services = replace(handler.services, config=Config())
        result = await self.public({"query": "44091"})
        self.assertEqual(result["error"]["code"], "upstream_error")
        self.assertEqual(len(self.transport.requests), 2)
        self.assertTrue(
            all(
                r.path in ("/api/v2/worlds", "/api/v2/data-centers")
                for r in self.transport.requests
            )
        )

    async def test_handler_generation_precedes_slow_config_capture(self):
        await self.start()
        from tests.modules.ff14.test_query_resolution import _Config

        entered, release = asyncio.Event(), asyncio.Event()

        class Config(_Config):
            async def current(self):
                snapshot = await super().current()
                if self.calls == 1:
                    entered.set()
                    await release.wait()
                return snapshot

        config = Config()
        handler = (
            self.runtime.core_runtime.registry.snapshot()
            .module("ff14/ff14")
            .handlers.capabilities["ff14.market.query"]
        )
        handler.services = replace(handler.services, config=config)
        first = asyncio.create_task(self.public({"query": "44091"}))
        await entered.wait()
        newer = await self.public({"query": "44091", "quality": None})
        self.assertEqual(newer["error"]["code"], "parameter_error")
        release.set()
        self.assertEqual((await first)["error"]["code"], "parameter_error")
        self.assertEqual(config.calls, 2)
        self.assertFalse(any("aggregated" in r.path for r in self.transport.requests))

    async def test_handler_candidate_expiry_and_module_restart_do_not_restore_state(
        self,
    ):
        await self.start()
        candidate = await self.public({"query": "Synthetic Item"})
        selection = candidate["model_facts"]["selection"]
        choice = {
            "selection": dict(
                batch_id=selection["batch_id"],
                generation=selection["generation"],
                item_id=44091,
            )
        }
        handler = (
            self.runtime.core_runtime.registry.snapshot()
            .module("ff14/ff14")
            .handlers.capabilities["ff14.market.query"]
        )
        handler.registry._clock = lambda: monotonic() + 301
        self.assertEqual(
            (await self.public(choice))["error"]["code"], "parameter_error"
        )
        handler.registry._clock = monotonic
        candidate = await self.public({"query": "Synthetic Item"})
        selection = candidate["model_facts"]["selection"]
        choice["selection"].update(
            batch_id=selection["batch_id"], generation=selection["generation"]
        )
        await self.runtime.terminate()
        self.transport = FixtureTransport()  # Restart receives a fresh transport too.
        await self.runtime.initialize()
        self.assertEqual(
            (await self.public(choice))["error"]["code"], "parameter_error"
        )

    async def test_default_host_admin_false_cannot_upgrade_public_session_or_role(self):
        await self.start()
        core = self.runtime.core_runtime
        from ygl_test_subject.api.services import (
            ConfigFieldUpdate,
            ConfigPatch,
            ConfigPatchMode,
        )
        from ygl_test_subject.services.core_configuration import (
            CORE_CONFIG_FIELDS,
            CORE_MODULE_ID,
            core_config_target,
        )

        from tests.services.test_admin_operations import _digest, token_urlsafe

        target = core_config_target(PLUGIN_NAME)
        await core.admin_credential_repository.bootstrap(_digest(token_urlsafe(32)))
        before = await core.config_repository.current(target)
        for forged in (
            SimpleNamespace(
                adapter_id="dashboard",
                request_id="fake",
                session_id="fake",
                is_admin=True,
            ),
            {"username": "synthetic", "public_session_id": "forged", "grant": "fake"},
            None,
        ):
            with (
                self.subTest(context=type(forged).__name__),
                self.assertRaises(AdminAuthorizationDenied),
            ):
                await core.admin_facade.module_snapshot(
                    None, "ff14/ff14", authorization=forged
                )
            with self.assertRaises(AdminAuthorizationDenied):
                await core.admin_facade.update_config(
                    None,
                    CORE_MODULE_ID,
                    ConfigPatch(
                        before.revision,
                        (
                            ConfigFieldUpdate(
                                "default_region",
                                ConfigPatchMode.REPLACE,
                                value="global",
                            ),
                        ),
                        CORE_CONFIG_FIELDS,
                        target=target,
                    ),
                    authorization=forged,
                )
        after = await core.config_repository.current(target)
        self.assertEqual(before, after)
        self.assertEqual(
            (await self.public({"query": "44091", "grant_id": "fake"}))["error"][
                "code"
            ],
            "parameter_error",
        )

    async def test_public_market_and_candidate_projection_is_closed_and_typed(self):
        from ygl_test_subject.adapters.astrbot.web_public import (
            WebPublicRejected,
            _market_facts,
        )

        await self.start()
        result = await self.public({"query": "44091", "server": "90001"})
        facts = result["model_facts"]
        self.assertEqual(_market_facts(facts), facts)
        for mutate in (
            lambda f: f.update(owner="forged"),
            lambda f: f["market"].update(raw_body="secret"),
            lambda f: f["market"]["scope"].update(target=True),
            lambda f: f["market"]["coverage"][0].update(seller="secret"),
            lambda f: f["market"]["coverage"][0].update(stage="invented"),
            lambda f: f["market"]["coverage"][0].update(status_code=False),
        ):
            invalid = copy.deepcopy(facts)
            mutate(invalid)
            with self.assertRaises(WebPublicRejected):
                _market_facts(invalid)
        selection = (await self.public({"query": "Synthetic Item"}))["model_facts"]
        self.assertEqual(_market_facts(selection), selection)
        for mutate in (
            lambda f: f["selection"].update(generation=1),
            lambda f: f["selection"].update(owner="forged"),
            lambda f: f["selection"]["candidates"][0].update(item_id=True),
        ):
            invalid = copy.deepcopy(selection)
            mutate(invalid)
            with self.assertRaises(WebPublicRejected):
                _market_facts(invalid)
