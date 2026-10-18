"""Actual selected Host proof -> real Core -> Host final-hop deadline checks."""

from __future__ import annotations

import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.adapters.astrbot import runtime as runtime_module
from ygl_test_subject.adapters.astrbot.web_public import (
    HostPublicWebValidator,
    WebPublicRejected,
)
from ygl_test_subject.services.core_runtime import CoreRuntime

from tests.host import test_astrbot_runtime as host_fixture
from tests.host.test_ff14_pages import _host_contracts, _request, _token

ROOT = Path(__file__).resolve().parents[2]


class PublicWebTimeout15Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mono = [1000.0]
        self.wall = [float(int(time.time()))]
        fixture = host_fixture.AstrBotRuntimeTests()
        fixture.temporary = tempfile.TemporaryDirectory(prefix="timeout15-host-proof-")
        self.addCleanup(fixture.temporary.cleanup)
        fixture.root = Path(fixture.temporary.name)
        fixture.data_dir = fixture.root / "plugin-data"
        fixture.data_dir.mkdir()
        fixture.plugin_root = fixture.root / "plugin"
        (fixture.plugin_root / "modules").mkdir(parents=True)
        shutil.copytree(
            ROOT / "modules/ff14",
            fixture.plugin_root / "modules/ff14",
            ignore=shutil.ignore_patterns("__pycache__"),
        )

        def create(**kwargs):
            return CoreRuntime(**kwargs, monotonic_clock=lambda: self.mono[0])

        self.transport = host_fixture._ItemReplyTransport()
        self.runtime = fixture._runtime(
            host_fixture._Context(),
            config={"web_public_origin": "https://ui.test"},
            http_transport_factory=lambda: self.transport,
            core_factory=create,
        )
        # Registered before initialize so failed setup is owned too.
        self.addAsyncCleanup(self.runtime.terminate)
        await self.runtime.initialize()
        await fixture._assert_initial_public_calendar_sync(self.runtime, self.transport)
        self.runtime._web_validator._clock = lambda: self.mono[0]
        self.runtime._web_validator._wall_clock = lambda: self.wall[0]
        # Lifecycle's legacy TaskScope clock defaults to real monotonic. This
        # controlled test must use one clock domain at every awaited boundary.
        scope = self.runtime.core_runtime.lifecycle.scope("ff14/ff14")
        scope._clock = lambda: self.mono[0]
        self.assertEqual(
            (
                self.runtime._web_validator._clock(),
                self.runtime.core_runtime.gateway._clock(),
                self.runtime.core_runtime.issuer._clock(),
                scope._clock(),
            ),
            (self.mono[0],) * 4,
        )
        self.web, _ = _host_contracts()
        self.handler = self.runtime.core_runtime.lifecycle.handlers(
            "ff14/ff14"
        ).capabilities["ff14.calendar.query"]
        self.original = self.handler.invoke
        self.views = []

    def clock_patch(self):
        from contextlib import ExitStack

        stack = ExitStack()
        stack.enter_context(
            patch.object(runtime_module, "monotonic", lambda: self.mono[0])
        )
        stack.enter_context(patch.object(runtime_module, "time", lambda: self.wall[0]))
        stack.enter_context(
            patch(
                "ygl_test_subject.adapters.astrbot.web_public.time",
                lambda: self.wall[0],
            )
        )
        return stack

    def advance_handler(self, seconds):
        async def invoke(view, parameters):
            self.views.append(view)
            self.mono[0] += seconds
            self.wall[0] += seconds
            return await self.original(view, parameters)

        self.handler.invoke = invoke

    def begin(self, endpoint="calendar", *, jwt_seconds=60, setup_seconds=0):
        request = _request(
            self.web,
            endpoint,
            token=_token(exp=int(self.wall[0]) + jwt_seconds),
            body={"region": "cn"} if endpoint == "calendar" else {"query": "100"},
        )
        return self.runtime.begin_public_web(
            request, endpoint, request.path, started_at=self.mono[0] - setup_seconds
        )

    async def test_selected_calendar_real_host_proof_reaches_core_and_final_hop_after30(
        self,
    ):
        with self.clock_patch():
            for elapsed in (31, 44):
                with self.subTest(elapsed=elapsed):
                    self.advance_handler(elapsed)
                    start = self.mono[0]
                    state = self.begin()
                    self.assertEqual(state[4][0], start + 45)
                    count = len(self.transport.requests)
                    try:
                        result = await self.runtime.invoke_public_web(
                            state,
                            "calendar",
                            {"region": "cn", "days": 7, "timezone": "UTC"},
                        )
                    finally:
                        self.runtime.finish_public_web(state)
                    self.assertEqual(result["status"], "success", result)
                    self.assertEqual(self.views[-1].deadline, start + 45)
                    self.assertEqual(self.views[-1].origin.value, "web_public")
                    self.assertEqual(len(self.transport.requests), count)
                    self.assertFalse(self.runtime._web_validator._requests)
                    self.assertFalse(
                        self.runtime.core_runtime.gateway.public_web_pending
                    )

    async def test_jwt_and_already_spent_entry_budget_remain_shorter_than45(self):
        with self.clock_patch():
            for jwt_seconds, setup, elapsed, expected_budget in (
                (20, 0, 21, 20),
                (60, 40, 6, 5),
            ):
                with self.subTest(jwt_seconds=jwt_seconds, setup=setup):
                    self.advance_handler(elapsed)
                    start = self.mono[0]
                    state = self.begin(jwt_seconds=jwt_seconds, setup_seconds=setup)
                    self.assertEqual(state[4][0], start + expected_budget)
                    try:
                        with self.assertRaises(WebPublicRejected):
                            await self.runtime.invoke_public_web(
                                state,
                                "calendar",
                                {"region": "cn", "days": 7, "timezone": "UTC"},
                            )
                    finally:
                        self.runtime.finish_public_web(state)
                    self.assertEqual(self.views[-1].deadline, start + expected_budget)
                    self.assertFalse(
                        self.runtime.core_runtime.gateway.public_web_pending
                    )

    async def test_actual_host_final_projection_rechecks_deadline_and_generation(self):
        with self.clock_patch():
            self.advance_handler(0)
            state = self.begin()
            original = runtime_module.project_result

            def project(result):
                data = original(result)
                self.mono[0] += 45.1
                self.wall[0] += 45.1
                return data

            try:
                with (
                    patch.object(runtime_module, "project_result", side_effect=project),
                    self.assertRaises(WebPublicRejected),
                ):
                    await self.runtime.invoke_public_web(
                        state,
                        "calendar",
                        {"region": "cn", "days": 7, "timezone": "UTC"},
                    )
            finally:
                self.runtime.finish_public_web(state)
            self.assertFalse(self.runtime.core_runtime.gateway.public_web_pending)
            # The independent Host generation fence is also checked after await.
            state = self.begin()
            generation_hits = []

            async def invoke(view, parameters):
                result = await self.original(view, parameters)
                generation_hits.append(True)
                self.runtime._generation += 1
                return result

            self.handler.invoke = invoke
            try:
                with self.assertRaises(WebPublicRejected):
                    await self.runtime.invoke_public_web(
                        state,
                        "calendar",
                        {"region": "cn", "days": 7, "timezone": "UTC"},
                    )
            finally:
                self.runtime.finish_public_web(state)
            self.assertEqual(generation_hits, [True])

    async def test_actual_host_noncalendar_tickets_keep_default30(self):
        with self.clock_patch():
            for endpoint in ("items", "market", "character"):
                state = self.begin(endpoint)
                try:
                    self.assertEqual(state[4][0], self.mono[0] + 30)
                finally:
                    self.runtime.finish_public_web(state)


class HostProofBudgetValidationTests(unittest.TestCase):
    def test_mapping_is_closed_copied_and_old_constructor_default_is_compatible(self):
        bindings = {"calendar": ("neutral/module", "read")}
        supplied = {("neutral/module", "read"): 45}
        validator = HostPublicWebValidator(
            1,
            lambda *_: True,
            bindings=bindings,
            clock=lambda: 100,
            wall_clock=lambda: 1000,
            capability_timeouts=supplied,
        )
        validator.attach(object())
        supplied[("neutral/module", "read")] = 90
        ticket = validator.begin("calendar", "synthetic-session", 1060)
        self.assertEqual(validator.deadline_facts(ticket), (145, 1060))
        validator.revoke(ticket)
        legacy = HostPublicWebValidator(
            1,
            lambda *_: True,
            bindings=bindings,
            clock=lambda: 100,
            wall_clock=lambda: 1000,
        )
        legacy.attach(object())
        ticket = legacy.begin("calendar", "synthetic-session", 1060)
        self.assertEqual(legacy.deadline_facts(ticket), (130, 1060))
        legacy.revoke(ticket)
        for values in (
            {("unknown/module", "read"): 45},
            {("neutral/module", "read"): True},
            {("neutral/module", "read"): float("nan")},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                HostPublicWebValidator(
                    1, lambda *_: True, bindings=bindings, capability_timeouts=values
                )
