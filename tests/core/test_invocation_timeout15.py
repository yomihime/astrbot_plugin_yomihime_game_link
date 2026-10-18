"""Reviewed execution ceilings reach real Core entry points without renewal."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.core.invocation_budget import InvocationBudgetPolicy
from ygl_test_subject.extensions.disk_manifest import parse_manifest
from ygl_test_subject.infrastructure.sync_diagnostics import SyncDiagnostics
from ygl_test_subject.services.core_runtime import CoreRuntime

from modules.ff14.features.calendar_snapshots import _diagnostic
from tests.core import test_invocation as web_fixture
from tests.services import test_core_runtime as core_fixture
from yomihime_game_link_sdk.results import ResultStatus


class InvocationTimeout15Tests(unittest.IsolatedAsyncioTestCase):
    async def test_real_host_passes_reviewed_maps_to_real_core_without_raising_defaults(
        self,
    ):
        from tests.host.test_astrbot_runtime import (
            AstrBotRuntimeTests,
            _Context,
            _ItemReplyTransport,
        )

        fixture = AstrBotRuntimeTests()
        fixture.temporary = tempfile.TemporaryDirectory(prefix="timeout15-host-")
        self.addCleanup(fixture.temporary.cleanup)
        fixture.root = Path(fixture.temporary.name)
        fixture.data_dir = fixture.root / "plugin-data"
        fixture.data_dir.mkdir()
        fixture.plugin_root = fixture.root / "plugin"
        (fixture.plugin_root / "modules").mkdir(parents=True)
        shutil.copytree(
            Path(__file__).resolve().parents[2] / "modules/ff14",
            fixture.plugin_root / "modules/ff14",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        captured = []

        def create(**kwargs):
            captured.append(kwargs)
            return CoreRuntime(**kwargs)

        transport = _ItemReplyTransport()
        runtime = fixture._runtime(
            _Context(), http_transport_factory=lambda: transport, core_factory=create
        )
        self.addAsyncCleanup(runtime.terminate)
        await runtime.initialize()
        core = runtime.core_runtime
        self.assertEqual(
            dict(captured[0]["capability_timeouts"]),
            {("ff14/ff14", "ff14.calendar.query"): 45},
        )
        self.assertEqual(
            dict(captured[0]["collector_timeouts"]),
            {("ff14/ff14", "ff14.calendar.sync"): 45},
        )
        self.assertEqual(core.scheduler.quotas.collection_timeout_seconds, 10)
        self.assertEqual(core._budget_policy.command_timeout, 90)
        self.assertIs(core.gateway._budget_policy, core._budget_policy)
        await fixture._assert_initial_public_calendar_sync(runtime, transport)
        await runtime.terminate()
        self.assertTrue(transport.closed)

    async def test_real_public_gateway_uses_selected_ceiling_and_manual_final_is_not_observation(
        self,
    ):
        for declared, trusted, elapsed, success in (
            (45, 200, 31, True),
            (None, 200, 31, False),
            (45, 107, 8, False),
            (45, 107, 6, True),
        ):
            with self.subTest(declared=declared, trusted=trusted, elapsed=elapsed):
                now = [100.0]
                fixture = web_fixture.PublicWebGatewayTests()
                views = []

                class Handler(web_fixture._Handler):
                    async def invoke(inner, invocation, parameters):
                        _diagnostic("sync_start", region="cn", trigger="manual")
                        views.append(invocation)
                        now[0] += elapsed
                        return await super().invoke(invocation, parameters)

                await fixture._ready_web(Handler())
                gateway = fixture.gateway
                gateway._clock = lambda: now[0]
                fixture.fixture.issuer._clock = gateway._clock
                fixture.proofs.clock = gateway._clock
                fixture.fixture.lifecycle.scope(
                    "testpkg/records"
                )._clock = gateway._clock
                gateway._budget_policy = InvocationBudgetPolicy(
                    capability_timeouts={("testpkg/records", "record.query"): declared}
                    if declared is not None
                    else None
                )
                diagnostics = SyncDiagnostics()
                gateway._sync_diagnostics = diagnostics
                try:
                    result = await fixture._invoke(
                        proof=fixture.proofs.new(
                            "testpkg/records",
                            "record.query",
                            deadline=trusted,
                        )
                    )
                    self.assertEqual(result.status is ResultStatus.SUCCESS, success)
                    self.assertEqual(
                        views[0].deadline, min(100 + (declared or 30), trusted)
                    )
                    trace = diagnostics.snapshot()["attempts"][-1]
                    self.assertEqual(
                        trace["core_final"], "completed" if success else "error"
                    )
                    self.assertIsNone(trace["observation_completeness"])
                    self.assertFalse(gateway.public_web_pending)
                finally:
                    gateway.fence_public_web()
                    self.assertTrue(await gateway.drain_public_web(1))
                    diagnostics.close()

    async def test_core_defensively_rejects_unknown_or_wrong_kind_reviewed_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = core_fixture.CoreRuntimeTests()
            fixture.root = Path(temporary)
            with patch.object(
                core_fixture, "CoreRuntime", side_effect=lambda **kwargs: kwargs
            ):
                kwargs = fixture._runtime()
            raw = (
                Path(__file__).resolve().parents[2]
                / "modules/ff14/yomihime.manifest.json"
            ).read_bytes()
            manifest = parse_manifest(raw).modules[0]
            kwargs["trusted_bundled_manifests"] = {"ff14/ff14": manifest}
            for key, value in (
                ("capability_timeouts", {("ff14/ff14", "ff14.calendar.sync"): 45}),
                ("collector_timeouts", {("ff14/ff14", "ff14.calendar.query"): 45}),
                ("capability_timeouts", {("unknown/owner", "ff14.calendar.query"): 45}),
                ("collector_timeouts", {("ff14/ff14", "unknown"): 45}),
                ("capability_timeouts", {("ff14/ff14", "ff14.calendar.query"): True}),
            ):
                with self.subTest(kind=key, value=value), self.assertRaises(ValueError):
                    CoreRuntime(**kwargs, **{key: value})
