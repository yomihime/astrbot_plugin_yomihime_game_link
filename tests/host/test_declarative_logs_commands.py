"""Offline real Host/Core Logs command path, declared sources and decoded PNG."""

from __future__ import annotations

import json
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from ygl_test_subject.presentation.card_bitmap import BitmapUnavailable
from ygl_test_subject.presentation.layout_renderer import render_document

from tests.host import test_astrbot_runtime as runtime_fixture
from tests.host import test_fflogs_tools as fixture
from tests.modules.ff14 import test_fflogs_cards as card_fixture


class DeclarativeLogsCommandTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.FFLogsToolHostTests.setUp
    tearDown = fixture.FFLogsToolHostTests.tearDown
    _runtime = fixture.FFLogsToolHostTests._runtime
    start = fixture.FFLogsToolHostTests.start
    start_logs = fixture.FFLogsToolHostTests.start_logs

    async def _start_catalog(self):
        await self.start_logs()
        original = self.transport.callback
        catalog = card_fixture.expanded_metadata()

        async def upstream(request):
            if request.source_id in ("fflogs_public_cn", "fflogs_public_global"):
                query = json.loads(request.body)["query"]
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

    async def test_cn_and_global_command_outputs_are_real_long_shared_pngs(self):
        await self._start_catalog()
        self.captured = []

        def render(card, **kwargs):
            bitmap = render_document(card, **kwargs)
            self.captured.append((card, bitmap.content))
            return bitmap

        with patch(
            "ygl_test_subject.presentation.command_card.render_document", render
        ):
            for realm in ("国服", "国际服"):
                event = runtime_fixture._Event(context=self.context)
                event.message = f'/ygl ff14 logs {realm} Cerberus "Synthetic Hero"'
                self.assertIsNone(await self.runtime.handle_event(event))
                sent = self.context.calls[-1][1].chain[0]
                self.assertTrue(hasattr(sent, "png"), getattr(sent, "text", "no PNG"))
                card, content = self.captured[-1]
                self.assertEqual(sent.png, content)
                with Image.open(BytesIO(content)) as image:
                    self.assertEqual((image.format, image.width), ("PNG", 1040))
                    self.assertGreater(image.height, 1500)
                    image.verify()
                self.assertEqual(card.layout, "table")
                self.assertIsNotNone(card.logo)
                self.assertEqual(
                    [len(section.blocks[0].rows) for section in card.blocks[:2]],
                    [7, 13],
                )
                self.assertIn(
                    "cn.fflogs.com" if realm == "国服" else "www.fflogs.com",
                    card.sources[0],
                )
        self.assertEqual(len(self.context.calls), 2)
        self.assertEqual(self.provider.trace, [])
        self.assertGreater(self.transport.exchanges, 0)
        self.assertEqual(self.runtime.core_runtime.output._generated_png._entries, {})

    async def test_failed_image_keeps_complete_real_catalog_text(self):
        await self._start_catalog()
        with patch(
            "ygl_test_subject.presentation.command_card.render_document",
            side_effect=BitmapUnavailable("font_unavailable"),
        ):
            event = runtime_fixture._Event(context=self.context)
            event.message = '/ygl ff14 logs 国服 Cerberus "Synthetic Hero"'
            await self.runtime.handle_event(event)
        self.assertEqual(len(self.context.calls), 1)
        text = self.context.calls[0][1].chain[0].text
        self.assertIn("Synthetic 100-0", text)
        self.assertIn("Synthetic 75-4", text)
        self.assertIn("12,345.5", text)
        self.assertIn("17", text)
        self.assertIn("各副本 FFLogs 默认分区", text)
        self.assertNotIn("全部分区", text)
        self.assertNotIn(fixture.SENTINEL, text)


if __name__ == "__main__":
    unittest.main()
