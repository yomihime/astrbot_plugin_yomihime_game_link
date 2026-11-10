"""Offline real Core, scheduler, SQLite and AstrBot adapter news chain.

Uses the repository's independently pinned Host source contract and synthetic
HTTP/messages only. It does not start a real AstrBot Host or contact an IM bot.
"""

import asyncio
import json
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ygl_test_subject.services.core_runtime import CoreRuntime

from tests.fixtures.runtime_cleanup import terminate_and_drain
from tests.host import test_astrbot_runtime as fixture
from tests.modules.ff14.test_news import page, row
from yomihime_game_link_sdk.services import HttpResponse


class ManualPumpCore(CoreRuntime):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Exact manual ticks need deterministic due times in this fixture.
        self.scheduler.random_source = lambda: 0.5

    async def _pump_loop(self):
        # Test owns explicit ticks; all production scheduler services remain real.
        await asyncio.Future()


class NewsTransport(fixture._ItemReplyTransport):
    def __init__(self):
        super().__init__()
        self.rows = [row(900), row(800)]
        self.fail_page = False

    async def request(self, request):
        if request.source_id != "ff14_news_official":
            return await super().request(request)
        self.requests.append(request)
        payload = page(self.rows)
        if request.path == "/api/news/newsDetail":
            news_id = dict(request.query)["id"]
            payload = {
                "Code": "0",
                "Data": next(
                    dict(r, Content="<p>维护已结束，可以登录游戏。</p>")
                    for r in self.rows
                    if str(r["Id"]) == news_id
                ),
            }
        return HttpResponse(
            503 if self.fail_page else 200, {}, json.dumps(payload).encode()
        )


class FF14NewsRuntimeTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.AstrBotRuntimeTests.setUp
    tearDown = fixture.AstrBotRuntimeTests.tearDown
    _runtime = fixture.AstrBotRuntimeTests._runtime
    _event = fixture.AstrBotRuntimeTests._event

    async def start(self, *, targets=(), admins=("test-platform-1:user-17",)):
        self.now = datetime.now(UTC)
        self.context, self.transport = fixture._Context(), NewsTransport()

        def core_factory(**kwargs):
            return ManualPumpCore(**kwargs, utc_clock=lambda: self.now)

        self.runtime = self._runtime(
            self.context,
            config={
                "ff14_news_admins": list(admins),
                "ff14_news_targets": list(targets),
            },
            core_factory=core_factory,
            http_transport_factory=lambda: self.transport,
        )
        await self.runtime.initialize()
        self.addAsyncCleanup(terminate_and_drain, self.runtime)
        self.assertTrue(self.runtime.ready)
        self.core = self.runtime.core_runtime
        # Browser acquisition is a separate integration boundary; never open
        # a browser or use the live official site from this offline Host test.
        from io import BytesIO

        from PIL import Image

        png = BytesIO()
        Image.new("RGB", (485, 100), "white").save(png, format="PNG")
        self.core._official_webpage_renderer = SimpleNamespace(
            render=AsyncMock(return_value=png.getvalue())
        )
        self.runtime._message_port._image_factory = lambda content: SimpleNamespace(
            png=content
        )
        await self.core._configured_public_subscriptions.reconcile()
        self.assertFalse(self.core._configured_public_subscriptions.failures)

    async def tick(self):
        await self.core.scheduler.run_due_page()
        self.now += timedelta(seconds=31)
        await self.core.delivery.dispatch_due_events()
        await self.core.delivery.dispatch_due_digests()

    async def sql(self, sql):
        return await self.core.database.executor.run_read(
            lambda unit: [tuple(row) for row in unit.execute(sql).fetchall()]
        )

    async def test_addhere_permissions_and_repeated_command_are_real_core_idempotent(
        self,
    ):
        await self.start()
        event = self._event(message="/ygl ff14 news addhere")
        event.sender = "ordinary-member"
        await self.runtime.handle_event(event)
        self.assertEqual(
            await self.sql("SELECT subscription_id FROM b04_subscriptions"), []
        )
        await self.runtime.handle_event(self._event(message="/ygl ff14 news addhere"))
        await self.runtime.handle_event(self._event(message="/ygl ff14 news addhere"))
        saved = await self.sql("SELECT record_json FROM b04_subscriptions")
        self.assertEqual(len(saved), 1)
        await self.tick()
        self.assertFalse([c for c in self.context.calls if len(c[1].chain) > 1])

    async def test_config_overlap_baseline_three_event_drain_and_text_png_same_send(
        self,
    ):
        await self.start(targets=["test-platform-1:GroupMessage:group-42"])
        await self.runtime.handle_event(self._event(message="/ygl ff14 news addhere"))
        self.assertEqual(
            len(await self.sql("SELECT subscription_id FROM b04_subscriptions")), 2
        )
        await self.tick()
        before = len(self.context.calls)
        self.transport.rows = [
            row(900),
            row(7),
            row(800),
            row(1),
            row(3),
            row(2),
            row(4),
        ]
        self.now += timedelta(seconds=900)
        await self.tick()
        messages = self.context.calls[before:]
        self.assertEqual(len(messages), 3)
        for session, chain in messages:
            self.assertEqual(session, "test-platform-1:GroupMessage:group-42")
            self.assertEqual(len(chain.chain), 2)
            self.assertIn("Synthetic news", chain.chain[0].text)
            self.assertIn("维护已结束", chain.chain[0].text)
            self.assertNotIn("网页截图暂不可用", chain.chain[0].text)
            self.assertNotIn("官网类别", chain.chain[0].text)
            self.assertIn("https://ff.web.sdo.com/", chain.chain[0].text)
            self.assertTrue(chain.chain[1].png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.now += timedelta(seconds=900)
        await self.tick()
        self.assertEqual(len(self.context.calls) - before, 5)
        self.transport.rows = [
            row(900),
            row(7, Title="Edited news"),
            row(800),
            row(1),
            row(3),
            row(2),
            row(4),
        ]
        self.now += timedelta(seconds=900)
        await self.tick()
        self.assertEqual(len(self.context.calls) - before, 5)

    async def test_screenshot_failure_keeps_one_text_message_without_replay(self):
        await self.start(targets=["test-platform-1:GroupMessage:group-42"])
        await self.tick()
        self.core._official_webpage_renderer.render.side_effect = RuntimeError(
            "unavailable"
        )
        self.transport.rows += [row(7)]
        self.now += timedelta(seconds=900)
        await self.tick()
        self.assertEqual(len(self.context.calls), 1)
        chain = self.context.calls[0][1].chain
        self.assertEqual(len(chain), 1)
        self.assertIn("网页截图暂不可用", chain[0].text)
        self.assertIn("indexm.html#/newstab/newscont/7", chain[0].text)
        self.now += timedelta(seconds=900)
        await self.tick()
        self.assertEqual(len(self.context.calls), 1)

    async def test_text_renderer_failure_preserves_title_summary_link_and_image(self):
        await self.start(targets=["test-platform-1:GroupMessage:group-42"])
        await self.tick()
        self.core.delivery._renderer = SimpleNamespace(
            render=AsyncMock(side_effect=RuntimeError("render unavailable"))
        )
        self.transport.rows += [row(7)]
        self.now += timedelta(seconds=900)
        await self.tick()
        self.assertEqual(len(self.context.calls), 1)
        chain = self.context.calls[0][1].chain
        self.assertEqual(len(chain), 2)
        self.assertIn("Synthetic news 7", chain[0].text)
        self.assertIn("维护已结束", chain[0].text)
        self.assertIn("indexm.html#/newstab/newscont/7", chain[0].text)
        self.assertNotIn("网页截图暂不可用", chain[0].text)

    async def test_unknown_keeps_uncertainty_and_does_not_resend(self):
        await self.start(targets=["test-platform-1:GroupMessage:group-42"])
        await self.tick()
        self.transport.rows += [row(7)]
        sends = []

        async def unknown(session, chain):
            sends.append((session, chain))
            return None

        self.context.send_message = unknown
        self.now += timedelta(seconds=900)
        await self.tick()
        self.assertEqual(len(sends), 1)
        saved = await self.sql("SELECT state FROM b04_digest_envelopes")
        self.assertEqual([r[0] for r in saved], ["unknown"])
        self.now += timedelta(seconds=900)
        await self.tick()
        self.assertEqual(len(sends), 1)

    async def test_config_reconcile_is_idempotent_and_removal_preserves_addhere(self):
        from tests.fixtures.configuration import write_value
        from yomihime_game_link_sdk.services import ConfigTarget

        await self.start(targets=["test-platform-1:GroupMessage:group-42"])
        await self.runtime.handle_event(self._event(message="/ygl ff14 news addhere"))
        await self.core._configured_public_subscriptions.reconcile()
        self.assertEqual(
            len(
                await self.sql(
                    "SELECT subscription_id FROM b04_subscriptions WHERE status='active'"
                )
            ),
            2,
        )
        # Explicit isolated fixture input; production config writes are covered
        # separately through the actual administrator API contract.
        target = ConfigTarget("astrbot_plugin_yomihime_game_link", "ff14/ff14")
        await self.core.database.executor.run_transaction(
            lambda unit: write_value(unit, target, "ff14_news_targets", [], 2)
        )
        await self.core._configured_public_subscriptions.reconcile()
        self.assertFalse(self.core._configured_public_subscriptions.failures)
        saved = await self.sql("SELECT status FROM b04_subscriptions ORDER BY status")
        self.assertEqual(saved, [("active",), ("cancelled",)])
        self.assertEqual(
            len(await self.sql("SELECT subscription_id FROM b04_subscription_jobs")), 1
        )

    async def test_reopen_keeps_baseline_and_does_not_replay_same_official_id(self):
        await self.start(targets=["test-platform-1:GroupMessage:group-42"])
        await self.tick()
        self.transport.rows += [row(7)]
        self.now += timedelta(seconds=900)
        await self.tick()
        self.assertEqual(len(self.context.calls), 1)
        await terminate_and_drain(self.runtime)

        def core_factory(**kwargs):
            return ManualPumpCore(**kwargs, utc_clock=lambda: self.now)

        self.runtime = self._runtime(
            self.context,
            core_factory=core_factory,
            http_transport_factory=lambda: self.transport,
        )
        await self.runtime.initialize()
        self.addAsyncCleanup(terminate_and_drain, self.runtime)
        self.core = self.runtime.core_runtime
        # Browser acquisition is a separate integration boundary; never open
        # a browser or use the live official site from this offline Host test.
        from io import BytesIO

        from PIL import Image

        png = BytesIO()
        Image.new("RGB", (485, 100), "white").save(png, format="PNG")
        self.core._official_webpage_renderer = SimpleNamespace(
            render=AsyncMock(return_value=png.getvalue())
        )
        await self.core._configured_public_subscriptions.reconcile()
        self.assertFalse(self.core._configured_public_subscriptions.failures)
        self.transport.rows[-1] = row(7, Title="Edited after reopen")
        self.now += timedelta(seconds=900)
        await self.tick()
        self.assertEqual(len(self.context.calls), 1)

    async def test_changed_live_manifest_cannot_inherit_configuration_authority(self):
        from dataclasses import replace

        await self.start(targets=["test-platform-1:GroupMessage:group-42"])
        self.core._trusted_bundled_manifests = {
            "ff14/ff14": replace(
                self.core.registry.snapshot().module("ff14/ff14").manifest,
                module_version="unreviewed-version",
            )
        }
        await self.core._configured_public_subscriptions.reconcile()
        self.assertEqual(
            self.core._configured_public_subscriptions.failures,
            (("ff14/ff14", "configured_subscription_unavailable"),),
        )
        self.assertEqual(
            len(await self.sql("SELECT subscription_id FROM b04_subscriptions")), 1
        )

    async def test_failed_scan_preserves_state_then_recovers(self):
        await self.start(targets=["test-platform-1:FriendMessage:user-17"])
        await self.tick()
        self.transport.rows += [row(7)]
        self.transport.fail_page = True
        self.now += timedelta(seconds=900)
        await self.tick()
        self.assertEqual(self.context.calls, [])
        self.transport.fail_page = False
        self.now += timedelta(seconds=3600)
        await self.tick()
        self.assertEqual(len(self.context.calls), 1)
        self.assertEqual(
            self.context.calls[0][0], "test-platform-1:FriendMessage:user-17"
        )
