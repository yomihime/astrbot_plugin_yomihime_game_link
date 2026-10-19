"""Actual Host/Core command output with offline upstream and decoded PNGs."""

from __future__ import annotations

import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from ygl_test_subject.adapters.astrbot.command_bridge import CommandHelp
from ygl_test_subject.core.contracts.administration import AdminAuthorizationDenied
from ygl_test_subject.core.contracts.services import ConfigFieldUpdate, ConfigPatch
from ygl_test_subject.presentation.card_bitmap import BitmapUnavailable
from ygl_test_subject.presentation.layout_renderer import render_document
from ygl_test_subject.services.configuration.defaults import (
    CORE_CONFIG_FIELDS,
    CORE_MODULE_ID,
    module_configuration_fields,
)
from ygl_test_subject.services.core_runtime import CoreRuntime

from tests.host import test_astrbot_runtime as fixture
from tests.host.test_market_integration import FixtureTransport
from yomihime_game_link_sdk.declarations import ConfigUpdateMode


class DeclarativeCommandPipelineTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.AstrBotRuntimeTests.setUp
    tearDown = fixture.AstrBotRuntimeTests.tearDown
    _runtime = fixture.AstrBotRuntimeTests._runtime
    _event = fixture.AstrBotRuntimeTests._event

    @staticmethod
    def _png(component):
        content = component.png
        with Image.open(BytesIO(content)) as png:
            if png.format != "PNG" or png.width != 1040 or png.height < 200:
                raise AssertionError("expected complete shared-layout PNG")
            png.verify()
        return content

    async def test_help_item_market_use_shared_renderer_through_real_core(self):
        class Transport(FixtureTransport):
            async def request(self, request):
                if request.source_id in ("xivapi_items", "garland_items"):
                    return await fixture._ItemReplyTransport.request(self, request)
                return await super().request(request)

        context, transport = fixture._Context(), Transport()
        runtime = self._runtime(context, http_transport_factory=lambda: transport)
        await runtime.initialize()
        captured = []

        def render(card, **kwargs):
            bitmap = render_document(card, **kwargs)
            captured.append((card, bitmap.content))
            return bitmap

        try:
            runtime._message_port._image_factory = lambda content: SimpleNamespace(
                png=content
            )
            with patch(
                "ygl_test_subject.presentation.command_card.render_document", render
            ):
                event = self._event(message="/ygl help")
                help_text = await runtime.handle_event(event)
                self.assertIsInstance(help_text, CommandHelp)
                self.assertEqual(len(help_text.source.modules), 1)
                self.assertEqual(help_text.source.recommendation, "ff14")
                await runtime.send_help(event, help_text)
                await runtime.send_help(event, help_text)
                self.assertEqual(len(context.calls), 1)
                help_card, help_png = captured[0]
                self.assertEqual(help_card.brand, "如月怜的游戏连结")
                self.assertEqual(help_card.title, "游戏，从这里连结")
                self.assertEqual(help_card.layout, "directory")
                self.assertEqual(
                    help_card.blocks[0].blocks[0].cells[0].asset.kind, "svg"
                )
                self.assertEqual(self._png(context.calls[0][1].chain[0]), help_png)
                for command, layout in (
                    ("/ygl ff14 item 100", "table"),
                    ("/ygl ff14 market 44091", "detail"),
                ):
                    event = self._event(message=command)
                    self.assertIsNone(await runtime.handle_event(event))
                    card, png = captured[-1]
                    self.assertEqual(self._png(context.calls[-1][1].chain[0]), png)
                    self.assertEqual(card.theme, "warm_paper")
                    self.assertIsNotNone(card.logo)
                    self.assertEqual(card.layout, layout)
                self.assertEqual(len(context.calls), 3)
                self.assertEqual(captured[-2][0].title, "Copper Ore")
                self.assertEqual(runtime.core_runtime._command_ingresses, {})
                self.assertEqual(
                    runtime.core_runtime.output._generated_png._entries, {}
                )
        finally:
            await runtime.terminate()

    async def test_declarative_failure_uses_complete_text_once(self):
        context = fixture._Context()
        runtime = self._runtime(
            context, http_transport_factory=fixture._ItemReplyTransport
        )
        await runtime.initialize()
        try:
            runtime._message_port._image_factory = lambda content: SimpleNamespace(
                png=content
            )
            with patch(
                "ygl_test_subject.presentation.command_card.render_document",
                side_effect=BitmapUnavailable("font_unavailable"),
            ):
                help_event = self._event(message="/ygl help")
                help_text = await runtime.handle_event(help_event)
                await runtime.send_help(help_event, help_text)
                await runtime.send_help(help_event, help_text)
                self.assertEqual(context.calls[0][1].chain[0].text, str(help_text))
                event = self._event(message="/ygl ff14 item 100")
                await runtime.handle_event(event)
                text = context.calls[-1][1].chain[0].text
                self.assertIn("Copper Ore", text)
                self.assertIn("A test item.", text)
                self.assertEqual(len(context.calls), 2)
        finally:
            await runtime.terminate()

    async def test_stale_help_or_changed_event_never_sends(self):
        context = fixture._Context()
        runtime = self._runtime(context, http_transport_factory=fixture._IdleTransport)
        await runtime.initialize()
        try:
            event = self._event(message="/ygl help")
            help_text = await runtime.handle_event(event)
            await runtime.core_runtime.lifecycle.stop("ff14/ff14", timeout=1)
            await runtime.send_help(event, help_text)
            self.assertEqual(context.calls, [])
            # A retained command receipt is tied to exact event facts.
            event = self._event(message="/ygl help")
            help_text = await runtime.handle_event(event)
            prepare = runtime.core_runtime.prepare_help_card

            async def change_event(source):
                result = await prepare(source)
                event.sender = "changed-sender"
                return result

            with patch.object(runtime.core_runtime, "prepare_help_card", change_event):
                await runtime.send_help(event, help_text)
            self.assertEqual(context.calls, [])
        finally:
            await runtime.terminate()

    async def test_authorized_theme_write_is_read_on_next_help_and_module_card(self):
        class Admin:
            adapter_id = request_id = session_id = "synthetic"

        admin = Admin()

        def factory(**kwargs):
            kwargs["admin_context_validator"] = (
                lambda _op, _inv, context, _gen: context is admin
            )
            return CoreRuntime(**kwargs)

        context = fixture._Context()
        runtime = self._runtime(
            context,
            http_transport_factory=fixture._ItemReplyTransport,
            core_factory=factory,
        )
        await runtime.initialize()
        captured = []

        def render(card, **kwargs):
            captured.append(card)
            return render_document(card, **kwargs)

        try:
            core = runtime.core_runtime
            await core.admin_credential_repository.bootstrap(bytes(range(32)))
            snapshot = await core.admin_facade.config_snapshot(
                None, CORE_MODULE_ID, authorization=admin
            )
            await core.admin_facade.update_config(
                None,
                CORE_MODULE_ID,
                ConfigPatch(
                    snapshot.revision,
                    (
                        ConfigFieldUpdate(
                            "image_theme", ConfigUpdateMode.REPLACE, value="deep_indigo"
                        ),
                    ),
                    CORE_CONFIG_FIELDS,
                ),
                authorization=admin,
            )
            runtime._message_port._image_factory = lambda content: SimpleNamespace(
                png=content
            )
            with patch(
                "ygl_test_subject.presentation.command_card.render_document", render
            ):
                event = self._event(message="/ygl help")
                await runtime.send_help(event, await runtime.handle_event(event))
                event = self._event(message="/ygl ff14 item 100")
                await runtime.handle_event(event)
            self.assertEqual(
                [card.theme for card in captured], ["deep_indigo", "deep_indigo"]
            )
            self.assertEqual(len(context.calls), 2)
            fields = module_configuration_fields(
                core.registry.snapshot().module("ff14/ff14").manifest.config_fields
            )
            snapshot = (
                await core.admin_facade.module_snapshot(
                    None, "ff14/ff14", authorization=admin
                )
            ).config
            change = ConfigPatch(
                snapshot.revision,
                (
                    ConfigFieldUpdate(
                        "reply_theme", ConfigUpdateMode.REPLACE, value="cool_white"
                    ),
                ),
                fields,
            )
            with self.assertRaises(AdminAuthorizationDenied):
                await core.admin_facade.update_config(
                    None, "ff14/ff14", change, authorization=Admin()
                )
            self.assertIsNone(
                (await core.theme_settings.current("ff14/ff14")).module_theme
            )
            await core.admin_facade.update_config(
                None, "ff14/ff14", change, authorization=admin
            )
            with patch(
                "ygl_test_subject.presentation.command_card.render_document", render
            ):
                event = self._event(message="/ygl ff14 item 100")
                await runtime.handle_event(event)
                event = self._event(message="/ygl help")
                await runtime.send_help(event, await runtime.handle_event(event))
            self.assertEqual(
                [card.theme for card in captured[-2:]], ["cool_white", "deep_indigo"]
            )
            snapshot = (
                await core.admin_facade.module_snapshot(
                    None, "ff14/ff14", authorization=admin
                )
            ).config
            await core.admin_facade.update_config(
                None,
                "ff14/ff14",
                ConfigPatch(
                    snapshot.revision,
                    (ConfigFieldUpdate("reply_theme", ConfigUpdateMode.CLEAR),),
                    fields,
                ),
                authorization=admin,
            )
            with patch(
                "ygl_test_subject.presentation.command_card.render_document", render
            ):
                await runtime.handle_event(self._event(message="/ygl ff14 item 100"))
            self.assertEqual(captured[-1].theme, "deep_indigo")
            self.assertEqual(len(context.calls), 5)
        finally:
            await runtime.terminate()
