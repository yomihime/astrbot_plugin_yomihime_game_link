"""Offline real Host/Core Logs command path, declared sources and decoded PNG."""

from __future__ import annotations

import json
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image
from ygl_test_subject.presentation.card_bitmap import BitmapUnavailable
from ygl_test_subject.presentation.layout_renderer import render_document

from tests.host import test_astrbot_runtime as runtime_fixture
from tests.host import test_fflogs_tools as fixture
from tests.modules.ff14 import test_fflogs_cards as card_fixture
from yomihime_game_link_sdk.services import HttpResponse


class DeclarativeLogsCommandTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.FFLogsToolHostTests.setUp
    tearDown = fixture.FFLogsToolHostTests.tearDown
    _runtime = fixture.FFLogsToolHostTests._runtime
    start = fixture.FFLogsToolHostTests.start
    start_logs = fixture.FFLogsToolHostTests.start_logs

    async def _start_catalog(self):
        await self.start_logs()
        self.command_region = "China"
        self.logs_sources = []
        self.duty_requests = []
        self.logs_operations = []
        self.directory_requests = []
        original = self.transport.callback
        catalog = card_fixture.expanded_metadata()

        async def upstream(request):
            if request.source_id == "xivapi_items":
                self.duty_requests.append(request)
                return {"results": [], "next": None}
            if request.source_id == "universalis_market":
                self.directory_requests.append(request.path)
                if request.path.endswith("worlds"):
                    return [
                        {"id": 71001, "name": "Cerberus"},
                        # Universalis can list worlds outside the DC directory.
                        {"id": 71002, "name": "Synthetic Cloud Test"},
                    ]
                return [
                    {
                        "name": "SyntheticDC",
                        "region": self.command_region,
                        "worlds": [71001],
                    }
                ]
            if request.source_id in ("fflogs_public_cn", "fflogs_public_global"):
                self.logs_sources.append(request.source_id)
                query = json.loads(request.body)["query"]
                self.logs_operations.append(query)
                if "FF14OutputMetadataShape" in query:
                    return card_fixture.expanded_schema()
                if "FF14CharacterMetadata" in query:
                    return catalog
                if "FF14CatalogCharacter" in query:
                    return card_fixture.catalog_rankings(catalog)
            return await original(request)

        self.transport.callback = upstream
        self.runtime._message_port._image_factory = lambda content: SimpleNamespace(
            png=content
        )

    async def _assert_command_png(self, *, region):
        await self._start_catalog()
        self.command_region = region
        self.captured = []

        def render(card, **kwargs):
            bitmap = render_document(card, **kwargs)
            self.captured.append((card, bitmap.content))
            return bitmap

        with patch(
            "ygl_test_subject.presentation.command_card.render_document", render
        ):
            for realm in ("国服" if region == "China" else "国际服",):
                event = runtime_fixture._Event(context=self.context)
                event.message = '/ygl ff14 logs Cerberus "Synthetic Hero"'
                self.assertIsNone(await self.runtime.handle_event(event))
                sent = self.context.calls[-1][1].chain[0]
                self.assertTrue(hasattr(sent, "png"), getattr(sent, "text", "no PNG"))
                card, content = self.captured[-1]
                self.assertEqual(sent.png, content)
                with Image.open(BytesIO(content)) as image:
                    self.assertEqual((image.format, image.width), ("PNG", 1400))
                    self.assertGreater(image.height, 1500)
                    image.verify()
                self.assertEqual(card.layout, "table")
                self.assertIsNotNone(card.logo)
                self.assertEqual(
                    [
                        sum(
                            len(block.rows)
                            for block in section.blocks
                            if hasattr(block, "rows")
                        )
                        for section in card.blocks
                        if hasattr(section, "blocks")
                    ],
                    [7, 13],
                )
                self.assertEqual(
                    [block.title for block in card.blocks if hasattr(block, "blocks")],
                    ["绝境战", "零式"],
                )
                self.assertIn(
                    "cn.fflogs.com" if realm == "国服" else "www.fflogs.com",
                    card.blocks[-1].content,
                )
                self.assertEqual(card.sources, ())
        self.assertEqual(len(self.context.calls), 1)
        self.assertEqual(
            set(self.logs_sources),
            {"fflogs_public_cn" if region == "China" else "fflogs_public_global"},
        )
        self.assertEqual(self.provider.trace, [])
        self.assertEqual(len(self.duty_requests), 1)
        self.assertEqual(self.duty_requests[0].path, "/api/search")
        self.assertEqual(dict(self.duty_requests[0].query)["query"], "ContentType=28")
        self.assertTrue(
            any(
                "FF14CatalogCharacter" in query
                or "FF14CharacterMetadata" in query
                or "FF14OutputMetadata" in query
                for query in self.logs_operations
            )
        )
        self.assertGreater(self.transport.exchanges, 0)
        self.assertEqual(self.runtime.core_runtime.output._generated_png._entries, {})

    async def test_cn_two_parameter_command_routes_without_provider(self):
        await self._assert_command_png(region="China")

    async def test_global_two_parameter_command_routes_without_provider(self):
        await self._assert_command_png(region="Europe")

    async def test_failed_stages_send_safe_group_hint_and_one_correlated_warning(self):
        await self._start_catalog()
        logger = MagicMock()
        original = self.transport.callback

        async def exchange_unavailable(request):
            return HttpResponse(503, {}, fixture.SENTINEL.encode())

        async def graphql_rejected(request):
            if request.source_id == "fflogs_public_cn":
                query = json.loads(request.body)["query"]
                if "FF14PublicCharacter" in query:
                    return {"errors": [{"message": fixture.SENTINEL}]}
            return await original(request)

        async def invoke(target, stage, category, status, hint):
            logger.reset_mock()
            event = runtime_fixture._Event(context=self.context)
            event.message = f"/ygl ff14 logs {target} PrivateRoleFixture"
            await self.runtime.handle_event(event)
            sent = self.context.calls[-1][1].chain[0].text
            self.assertIn(hint, sent)
            self.assertNotIn("PrivateRoleFixture", sent)
            self.assertNotIn(fixture.SENTINEL, sent)
            warnings = [
                call.args[0]
                for call in logger.warning.call_args_list
                if call.args and call.args[0].startswith("GAME_LINK_")
            ]
            self.assertEqual(len(warnings), 1)
            self.assertTrue(warnings[0].startswith("GAME_LINK_DIAGNOSTIC "))
            self.assertNotIn("PrivateRoleFixture", warnings[0])
            self.assertNotIn(fixture.SENTINEL, warnings[0])
            diagnostic = json.loads(warnings[0].split(" ", 1)[1])
            self.assertEqual(diagnostic["event_id"], "FF14LogsFailure")
            (row,) = diagnostic["payload"]["failures"]
            self.assertEqual(
                (row["stage"], row["category"], row["http_status"]),
                (stage, category, status),
            )
            self.assertGreaterEqual(row["elapsed_ms"], 0)
            matching_trace = next(
                trace
                for trace in self.runtime.core_runtime.sync_diagnostics.snapshot()[
                    "attempts"
                ]
                if trace["invocation_id"] == diagnostic["trace"]
            )
            self.assertEqual(matching_trace["capability_status"], "error")
            return diagnostic

        with patch("astrbot.api.logger", logger, create=True):
            await invoke(
                '"Synthetic Cloud Test"',
                "realm_directory",
                "region_unresolved",
                200,
                "无法判断 FFLogs 区域",
            )
            with patch.object(
                self.transport, "request_credential_exchange", exchange_unavailable
            ):
                auth = await invoke(
                    "Cerberus",
                    "server_directory",
                    "http_error",
                    503,
                    "授权或服务器目录查询失败",
                )
                self.assertEqual(
                    auth["authorization_failure"]["source"], "fflogs_public_cn"
                )
                self.assertEqual(auth["authorization_failure"]["stage"], "auth_failure")
                self.assertEqual(auth["authorization_failure"]["http_status"], 503)
            self.transport.callback = graphql_rejected
            await invoke(
                "Cerberus", "character_query", "graphql_error", 200, "角色查询失败"
            )

    async def test_old_three_positions_and_unknown_duplicate_options_never_query(self):
        await self._start_catalog()
        for tail in (
            '国服 Cerberus "Synthetic Hero"',
            "Cerberus Hero realm=cn",
            "Cerberus Hero metric=rdps metric=ndps",
        ):
            event = runtime_fixture._Event(context=self.context)
            event.message = "/ygl ff14 logs " + tail
            with (
                patch(
                    "ygl_test_subject.presentation.command_card.render_document",
                    side_effect=BitmapUnavailable("font_unavailable"),
                ),
                patch(
                    "ygl_test_subject.presentation.command_card.render_card",
                    side_effect=BitmapUnavailable("font_unavailable"),
                ),
            ):
                await self.runtime.handle_event(event)
            self.assertIn(
                "用法：/ygl ff14 logs <服务器>", self.context.calls[-1][1].chain[0].text
            )
        self.assertEqual(self.logs_sources, [])
        self.assertEqual(self.directory_requests, [])
        self.assertEqual(self.provider.trace, [])

    async def test_failed_image_keeps_complete_real_catalog_text(self):
        await self._start_catalog()
        with patch(
            "ygl_test_subject.presentation.command_card.render_document",
            side_effect=BitmapUnavailable("font_unavailable"),
        ):
            event = runtime_fixture._Event(context=self.context)
            event.message = '/ygl ff14 logs Cerberus "Synthetic Hero"'
            await self.runtime.handle_event(event)
        self.assertEqual(len(self.context.calls), 1)
        text = self.context.calls[0][1].chain[0].text
        self.assertNotIn("Synthetic Trial 0", text)
        self.assertIn("Synthetic Ultimate 100-0", text)
        self.assertIn("Synthetic 75-4", text)
        self.assertIn("12,345.5", text)
        self.assertIn("绝境战逐项查询来源目录中的标准阵容历史分区", text)
        self.assertIn("零式查询目录明确合格的标准与非标准阵容分区", text)
        self.assertIn("排除 Echo / 超越之力，再按原始百分位选优", text)
        self.assertNotIn("各副本的 FFLogs 默认分区", text)
        self.assertIn("绝境战取已查询来源返回的最高百分位", text)
        self.assertNotIn(fixture.SENTINEL, text)


if __name__ == "__main__":
    unittest.main()
