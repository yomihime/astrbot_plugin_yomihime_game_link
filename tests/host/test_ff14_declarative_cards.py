"""Actual offline command dispatch through registry/Core/Host to PNG bytes."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.presentation import command_card

from tests.host import test_astrbot_runtime as runtime_fixture
from tests.host.test_market_integration import FixtureTransport
from tests.modules.ff14.test_item_market_cards import item_snapshot, text
from yomihime_game_link_sdk.services import HttpResponse


class ItemMarketTransport(FixtureTransport):
    async def request(self, request):
        record = item_snapshot()
        if request.source_id == "xivapi_items" and "/sheet/Item/" in request.path:
            body = {
                "row_id": record.item_id,
                "fields": {
                    "Name": record.name,
                    "LevelItem": {"value": record.item_level},
                    "LevelEquip": record.equip_level,
                },
            }
        elif request.source_id == "garland_items":
            body = {
                "item": {
                    "id": record.item_id,
                    "quests": ["70996"],
                    "craft": [
                        {
                            "job": 15,
                            "lvl": 99,
                            "ingredients": [
                                {"id": cost.item_id, "amount": cost.amount}
                                for cost in record.routes[1].costs
                            ],
                        }
                    ],
                },
                "partials": [
                    {
                        "type": "quest",
                        "id": "70996",
                        "obj": {"n": record.routes[0].details[0], "z": "图莱尤拉"},
                    },
                    *(
                        {"type": "item", "id": cost.item_id, "obj": {"n": cost.name}}
                        for cost in record.routes[1].costs
                        if cost.name
                    ),
                ],
            }
        else:
            return await super().request(request)
        self.requests.append(request)
        return validate_contract(HttpResponse(200, {}, json.dumps(body).encode()))


class FF14DeclarativeCommandTests(unittest.IsolatedAsyncioTestCase):
    setUp = runtime_fixture.AstrBotRuntimeTests.setUp
    tearDown = runtime_fixture.AstrBotRuntimeTests.tearDown
    _runtime = runtime_fixture.AstrBotRuntimeTests._runtime
    _event = runtime_fixture.AstrBotRuntimeTests._event

    async def start(self):
        self.context = runtime_fixture._Context()
        self.transport = ItemMarketTransport()
        self.runtime = self._runtime(
            self.context, http_transport_factory=lambda: self.transport
        )
        await self.runtime.initialize()
        self.addAsyncCleanup(self.runtime.terminate)
        self.runtime._message_port._image_factory = lambda content: SimpleNamespace(
            png=content
        )

        async def wrong_destination(*_args):
            raise AssertionError("command output must use the exact inbound event")

        self.context.send_message = wrong_destination

    async def test_registered_item_and_all_market_intents_reach_shared_png_renderer(
        self,
    ):
        await self.start()
        commands = (
            ("/ygl ff14 item 44091", "物品"),
            ("/ygl ff14 market 44091 region=cn intent=min", "市场"),
            ("/ygl ff14 market 44091 region=cn intent=overview", "市场"),
            ("/ygl ff14 market 44091 region=cn intent=listings", "市场"),
        )
        with (
            patch.object(
                command_card, "render_document", wraps=command_card.render_document
            ) as renderer,
            patch.object(
                command_card,
                "render_card",
                side_effect=AssertionError(
                    "legacy painter must not handle declarative cards"
                ),
            ),
        ):
            for index, (command, category) in enumerate(commands):
                with self.subTest(command=command):
                    self.assertIsNone(
                        await self.runtime.handle_event(self._event(message=command))
                    )
                    self.assertEqual(len(self.context.calls), index + 1)
                    payload = self.context.calls[-1][1].chain[0].png
                    self.assertTrue(payload.startswith(b"\x89PNG\r\n\x1a\n"))
                    self.assertLessEqual(len(payload), 1_048_576)
                    card = renderer.call_args.args[0]
                    self.assertIn(category, card.brand)
                    self.assertIsNotNone(card.logo)
                    if category == "物品":
                        self.assertEqual(card.title, item_snapshot().name)
                        for marker in (
                            "684",
                            "70996",
                            "制作材料",
                            "物品 ID 13",
                            "制作关联信息不完整",
                        ):
                            self.assertIn(marker, text(card))
                    else:
                        for marker in (
                            "SyntheticChina",
                            "Universalis",
                            "实际在售情况以游戏内为准",
                        ):
                            self.assertIn(marker, text(card))
                        self.assertNotIn("synthetic-private-retainer", text(card))
                        self.assertNotIn("synthetic-private-seller", text(card))
            self.assertEqual(renderer.call_count, len(commands))
        core = self.runtime.core_runtime
        self.assertEqual(core._command_ingresses, {})
        self.assertEqual(core.output._generated_png._entries, {})

    async def test_declarative_render_failure_preserves_complete_text_and_links(self):
        await self.start()
        with patch.object(
            command_card,
            "render_document",
            side_effect=RuntimeError("offline renderer failure"),
        ):
            self.assertIsNone(
                await self.runtime.handle_event(
                    self._event(message="/ygl ff14 item 44091")
                )
            )
        fallback = self.context.calls[-1][1].chain[0].text
        for marker in (
            "犎牛牛排",
            "物品 ID：44091",
            "684",
            "70996",
            "材料/货币",
            "物品 ID 13 × 8",
            "Garland 的制作关联信息不完整",
        ):
            self.assertIn(marker, fallback)
        for url in item_snapshot().source_urls:
            self.assertIn(url, fallback)
        resource = self.runtime.core_runtime.output._generated_png
        self.assertTrue(resource is None or resource._entries == {})
