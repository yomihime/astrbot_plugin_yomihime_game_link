"""Offline exact-event command boundary; no running Host or IM delivery claims."""

import asyncio
import base64
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.adapters.astrbot.message_port import AstrBotMessagePort
from ygl_test_subject.adapters.astrbot.runtime import (
    AstrBotRuntime,
    _AstrBotMessageIngress,
    _MessageReceipts,
)
from ygl_test_subject.core.ports import MessageStatus, MessageTarget, RenderedMessage
from ygl_test_subject.presentation.card_bitmap import BitmapUnavailable, RenderedBitmap
from ygl_test_subject.presentation.command_card import TOO_LARGE, CommandCardRenderer
from ygl_test_subject.services.output import OutputStatus

from tests.fixtures.generated_png import GeneratedPngFixture
from yomihime_game_link_sdk.contexts import InvocationOrigin
from yomihime_game_link_sdk.display import (
    DisplayAudience,
    DisplayDocument,
    DisplayOutput,
    FieldsBlock,
    GridItem,
    ImageBlock,
    ItemGridBlock,
    Privacy,
    TableBlock,
    TextBlock,
)
from yomihime_game_link_sdk.subscriptions import ConversationKind, ConversationRef


class _Context:
    async def send_message(self, *_):
        raise AssertionError("commands must not use session fan-out")


class _Event:
    def __init__(self, *, returned=None, error=None):
        self.sender = "user"
        self.message = "/ygl ff14 item 100"
        self.calls = []
        self.returned = returned
        self.error = error
        self.entered = asyncio.Event()
        self.gate = None

    def get_platform_id(self):
        return "adapter"

    def get_sender_id(self):
        return self.sender

    def get_session_id(self):
        return "conversation"

    def get_message_type(self):
        return "FriendMessage"

    def get_message_str(self):
        return self.message

    def chain_result(self, components):
        return SimpleNamespace(chain=components)

    async def send(self, chain):
        self.calls.append(chain)
        self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return self.returned


class _Image:
    @classmethod
    def fromBytes(cls, content):
        return SimpleNamespace(file="base64://" + base64.b64encode(content).decode())


class ExactEventImageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.receiver = _AstrBotMessageIngress(
            object(), _MessageReceipts(), lambda _: True
        )
        self.current = True
        self.port = AstrBotMessagePort(
            _Context(),
            plain_factory=lambda text: SimpleNamespace(text=text),
            image_factory=_Image.fromBytes,
            command_current=lambda ingress: self.current and self.receiver.validate(
                InvocationOrigin.COMMAND, ingress
            ),
        )
        self.event = _Event()
        self.ingress = self.receiver.make(self.event)
        self.target = MessageTarget(
            "conversation", "adapter:user",
            ConversationRef(
                "adapter", ConversationKind.DIRECT, "conversation", "adapter"
            ),
        )
        self.payload = RenderedMessage(
            "complete deterministic text", png_bytes=b"bounded-png"
        )

    async def test_standard_bytes_one_exact_event_none_is_unknown_no_retry(self):
        receipt = await self.port.send_command(self.ingress, self.target, self.payload)
        self.assertIs(receipt.status, MessageStatus.UNKNOWN)
        self.assertEqual(len(self.event.calls), 1)
        encoded = self.event.calls[0].chain[0].file.removeprefix("base64://")
        self.assertEqual(base64.b64decode(encoded), b"bounded-png")
        again = await self.port.send_command(self.ingress, self.target, self.payload)
        self.assertIs(again.status, MessageStatus.UNKNOWN)
        self.assertEqual(len(self.event.calls), 1)

    async def test_unknown_send_exception_never_duplicates_text(self):
        self.event.error = ValueError("after platform enqueue")
        receipt = await self.port.send_command(self.ingress, self.target, self.payload)
        self.assertIs(receipt.status, MessageStatus.UNKNOWN)
        self.assertEqual(len(self.event.calls), 1)
        self.assertFalse(hasattr(self.event.calls[0].chain[0], "text"))

    async def test_component_failure_falls_back_before_first_send(self):
        self.port._image_factory = lambda _: (_ for _ in ()).throw(ValueError())
        await self.port.send_command(self.ingress, self.target, self.payload)
        self.assertEqual(len(self.event.calls), 1)
        self.assertEqual(self.event.calls[0].chain[0].text, self.payload.text)

    async def test_wrong_owner_changed_event_and_shutdown_never_send(self):
        wrong = replace(self.target, recipient_id="adapter:someone-else")
        receipt = await self.port.send_command(self.ingress, wrong, self.payload)
        self.assertIs(receipt.status, MessageStatus.FAILED)
        self.event.sender = "somebody-else"
        receipt = await self.port.send_command(self.ingress, self.target, self.payload)
        self.assertIs(receipt.status, MessageStatus.FAILED)
        self.event.sender = "user"
        self.current = False
        receipt = await self.port.send_command(self.ingress, self.target, self.payload)
        self.assertIs(receipt.status, MessageStatus.FAILED)
        self.assertEqual(self.event.calls, [])

    async def test_changed_command_text_and_private_image_are_rejected(self):
        self.event.message = "/ygl ff14 status"
        receipt = await self.port.send_command(self.ingress, self.target, self.payload)
        self.assertIs(receipt.status, MessageStatus.FAILED)
        self.event.message = "/ygl ff14 item 100"
        private_target = replace(self.target, authorized=True)
        receipt = await self.port.send_command(self.ingress, private_target, self.payload)
        self.assertIs(receipt.status, MessageStatus.FAILED)
        self.assertEqual(self.event.calls, [])

    async def test_cancelled_send_is_not_reissued_and_event_is_not_retained(self):
        self.event.gate = asyncio.Event()
        task = asyncio.create_task(
            self.port.send_command(self.ingress, self.target, self.payload)
        )
        await self.event.entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await self.port.send_command(self.ingress, self.target, self.payload)
        self.assertEqual(len(self.event.calls), 1)
        self.assertIs(
            self.event._ygl_command_send_marker, self.port._command_send_marker
        )

    async def test_many_retained_events_do_not_exhaust_global_send_quota(self):
        events = [_Event() for _ in range(160)]
        for event in events:
            receipt = await self.port.send_command(
                self.receiver.make(event), self.target, self.payload
            )
            self.assertIs(receipt.status, MessageStatus.UNKNOWN)
            self.assertEqual(len(event.calls), 1)

    async def test_durable_session_sender_rejects_png(self):
        receipt = await self.port.send(self.target, self.payload)
        self.assertIs(receipt.status, MessageStatus.FAILED)


class HelpImageLifetimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_help_aggregate_budget_and_unload_cancel_live_sends(self):
        class Core:
            accepting = True
            def stop_accepting_host_ingress(self):
                self.accepting = False
            async def close(self):
                return True
        runtime = AstrBotRuntime(_Context(), plugin_root=".", data_dir=".")
        runtime._ready = True
        runtime._core = Core()
        runtime._message_port = AstrBotMessagePort(
            _Context(),
            plain_factory=lambda text: SimpleNamespace(text=text),
            image_factory=_Image.fromBytes,
            command_current=runtime._command_current,
        )
        events = [_Event() for _ in range(6)]
        for event in events:
            event.gate = asyncio.Event()
        tasks = [
            asyncio.create_task(runtime.send_help(event, "帮助\n完整目录"))
            for event in events
        ]
        for event in events:
            await asyncio.wait_for(event.entered.wait(), 10)
        self.assertEqual(runtime._help_image_count, 3)
        self.assertTrue(all(hasattr(event.calls[0].chain[0], "file") for event in events[:3]))
        self.assertTrue(all(hasattr(event.calls[0].chain[0], "text") for event in events[3:]))
        await runtime.terminate()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.assertEqual(runtime._help_image_count, 0)
        self.assertEqual(runtime._help_tasks, set())
        self.assertTrue(all(len(event.calls) == 1 for event in events))


class CommandProjectionTests(unittest.TestCase):
    def setUp(self):
        self.renderer = CommandCardRenderer()
        self.document = DisplayDocument(
            "物品", "Copper Ore",
            (
                FieldsBlock({"等级": 1, "数量": 0}),
                TableBlock(("名称", "值"), (("长名称", "完整内容"),)),
            ),
            sources=("XIVAPI",),
        )

    def test_existing_content_generates_bounded_png_without_module_changes(self):
        value = self.renderer(self.document, audience=DisplayAudience.PUBLIC)
        self.assertTrue(value.bitmap.content.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertLessEqual(len(value.bitmap.content), 1_048_576)
        self.assertIn("数量: 0", value.text)
        self.assertIn("来源：XIVAPI", value.text)

    def test_render_failure_preserves_full_text(self):
        with patch(
            "ygl_test_subject.presentation.command_card.render_card",
            side_effect=BitmapUnavailable("font_unavailable"),
        ):
            value = self.renderer(self.document, audience=DisplayAudience.PUBLIC)
        self.assertIsNone(value.bitmap)
        self.assertIn("Copper Ore", value.text)
        self.assertIn("完整内容", value.text)

    def test_both_budgets_exceeded_returns_explicit_hint_not_partial_facts(self):
        huge = replace(self.document, ordered_blocks=(TextBlock("事实" * 13_000),))
        value = self.renderer(huge, audience=DisplayAudience.PUBLIC)
        self.assertIsNone(value.bitmap)
        self.assertEqual(value.text, TOO_LARGE)

    def test_image_can_hold_more_than_text_and_failure_asks_to_narrow(self):
        renderer = CommandCardRenderer(max_text_chars=20)
        value = renderer(self.document, audience=DisplayAudience.PUBLIC)
        self.assertIsNotNone(value.bitmap)
        self.assertEqual(value.text, TOO_LARGE)
        with patch(
            "ygl_test_subject.presentation.command_card.render_card",
            side_effect=BitmapUnavailable("byte_limit"),
        ):
            failed = renderer(self.document, audience=DisplayAudience.PUBLIC)
        self.assertIsNone(failed.bitmap)
        self.assertEqual(failed.text, TOO_LARGE)

    def test_image_geometry_overflow_keeps_complete_text_when_it_fits(self):
        document = replace(
            self.document,
            ordered_blocks=tuple(TextBlock(str(index)) for index in range(33)),
        )
        value = self.renderer(document, audience=DisplayAudience.PUBLIC)
        self.assertIsNone(value.bitmap)
        self.assertNotEqual(value.text, TOO_LARGE)
        self.assertIn("\n32\n来源：XIVAPI", value.text)

    def test_required_assets_need_explicit_fallback(self):
        from ygl_test_subject.presentation.rendering import RenderingFailure
        for block in (
            ImageBlock("asset", "alt is not the image", required=True),
            ItemGridBlock((GridItem("label", 1, asset_id="asset"),)),
        ):
            document = replace(self.document, ordered_blocks=(block,))
            with self.subTest(block=block):
                with self.assertRaises(RenderingFailure):
                    self.renderer(document, audience=DisplayAudience.PUBLIC)
                fallback = replace(block, fallback_text="complete explicit fallback")
                value = self.renderer(
                    replace(document, ordered_blocks=(fallback,)),
                    audience=DisplayAudience.PUBLIC,
                )
                self.assertIn("complete explicit fallback", value.text)
                self.assertIsNotNone(value.bitmap)

    def test_help_rejects_invalid_actual_png_before_host(self):
        with patch(
            "ygl_test_subject.presentation.command_card.render_card",
            return_value=RenderedBitmap(b"not-png", 1, 1),
        ):
            value = self.renderer.help("帮助\n完整帮助内容")
        self.assertIsNone(value.bitmap)
        self.assertEqual(value.text, "帮助\n完整帮助内容")

    def test_help_uses_same_production_renderer(self):
        value = self.renderer.help("夜美姬游戏互联\nFF14 /ygl ff14 help\n默认区域提示：cn")
        self.assertIsNotNone(value.bitmap)
        self.assertIn("默认区域提示：cn", value.text)


class AuthorizedCommandImageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = GeneratedPngFixture()
        await self.h.start()
        self.sent = []
        self.h.output._command_card_renderer = CommandCardRenderer()

        async def sender(view, result, target, payload, *, image_current=None):
            self.h.issuer.require(view)
            await self.h.output._validate_command_current(view, result, target)
            if image_current is not None:
                image_current()
            self.sent.append(payload)
            from ygl_test_subject.core.ports import MessageReceipt
            return MessageReceipt(MessageStatus.UNKNOWN)

        self.h.output._command_sender = sender

    async def asyncTearDown(self):
        if self.h.output._generated_png is not None:
            self.h.output._generated_png.close()
        await self.h.stop()

    async def test_real_authority_png_one_read_unknown_replay_no_second_send(self):
        view, result = self.h.new_view(), self.h.new_result()
        outcome = await self.h.output.route(view, result)
        self.assertIs(outcome.status, OutputStatus.UNKNOWN)
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.sent[0].png_bytes.startswith(b"\x89PNG"))
        replay = await self.h.output.route(view, result)
        self.assertIs(replay.status, OutputStatus.UNKNOWN)
        self.assertEqual(len(self.sent), 1)
        coordinator = self.h.output._generated_png
        self.assertEqual(
            (coordinator._raw_bytes, coordinator._conversion_bytes), (0, 0)
        )
        self.assertEqual(coordinator._entries, {})

    async def test_render_failure_sends_one_complete_text(self):
        with patch(
            "ygl_test_subject.presentation.command_card.render_card",
            side_effect=BitmapUnavailable("font_unavailable"),
        ):
            outcome = await self.h.output.route(self.h.new_view(), self.h.new_result())
        self.assertIs(outcome.status, OutputStatus.UNKNOWN)
        self.assertEqual(len(self.sent), 1)
        self.assertIsNone(self.sent[0].png_bytes)
        self.assertIn("body-SENSITIVE", self.sent[0].text)

    async def test_granted_commands_keep_original_sender_and_never_render_images(self):
        class TextRenderer:
            async def render(self, *_args, **_kwargs):
                return DisplayOutput("complete authorized text")

        legacy_sends = []

        class LegacyPort:
            async def send(self, target, payload):
                from ygl_test_subject.core.ports import MessageReceipt

                legacy_sends.append((target, payload))
                return MessageReceipt(MessageStatus.UNKNOWN)

        self.h.output._renderer = TextRenderer()
        self.h.output._message_port = LegacyPort()

        def forbidden_image(*_args, **_kwargs):
            raise AssertionError("private image forbidden")

        self.h.output._command_card_renderer = forbidden_image
        grant = self.h.grant()
        for privacy in (Privacy.PRIVATE, Privacy.PUBLIC):
            with self.subTest(privacy=privacy):
                view = self.h.new_view(grant_id=grant.grant_id, grant_revision=1)
                outcome = await self.h.output.route(view, self.h.new_result(privacy))
                self.assertIs(outcome.status, OutputStatus.UNKNOWN)
                target, payload = legacy_sends[-1]
                self.assertTrue(target.authorized)
                self.assertIsNone(payload.png_bytes)
                self.assertEqual(payload.text, "complete authorized text")
        self.assertEqual(len(legacy_sends), 2)
        self.assertEqual(self.sent, [])
        self.assertIsNone(self.h.output._generated_png)

    async def test_grant_revoked_during_preparation_never_reaches_either_sender(self):
        class TextRenderer:
            async def render(self, *_args, **_kwargs):
                return DisplayOutput("complete authorized text")

        self.h.output._renderer = TextRenderer()
        grant = self.h.grant()
        view = self.h.new_view(grant_id=grant.grant_id, grant_revision=1)
        self.h.routes.arm()
        task = asyncio.create_task(
            self.h.output.route(view, self.h.new_result(Privacy.PRIVATE))
        )
        await self.h.routes.entered.wait()
        async with self.h.admission.mutation("revoke-test-grant"):
            self.h.grants.value = None
        self.h.routes.resume.set()
        outcome = await task
        self.assertIs(outcome.status, OutputStatus.FAILED)
        self.assertEqual(outcome.error_code, "grant_unavailable")
        self.assertEqual(self.sent, [])
        self.assertIsNone(self.h.output._generated_png)

    async def test_owner_command_keeps_original_approved_text_sender(self):
        owner = GeneratedPngFixture()
        await owner.start(owner=True)
        sent = []

        class TextPort:
            async def render(self, *_args, **_kwargs):
                return DisplayOutput("complete owner text")

            async def send(self, target, payload):
                from ygl_test_subject.core.ports import MessageReceipt

                sent.append((target, payload))
                return MessageReceipt(MessageStatus.ACCEPTED)

        def forbidden(*_args, **_kwargs):
            raise AssertionError("OWNER must not enter public image preparation")

        try:
            owner.output._renderer = TextPort()
            owner.output._message_port = TextPort()
            owner.output._command_card_renderer = forbidden
            owner.output._command_sender = forbidden
            outcome = await owner.output.route(owner.view, owner.result)
            self.assertIs(outcome.status, OutputStatus.SENT)
            self.assertEqual(len(sent), 1)
            self.assertTrue(sent[0][0].authorized)
            self.assertEqual(sent[0][1].text, "complete owner text")
            self.assertIsNone(sent[0][1].png_bytes)
            self.assertIsNone(owner.output._generated_png)
        finally:
            await owner.stop()

    async def test_core_exact_event_sender_rejects_granted_text_before_preparation(self):
        from ygl_test_subject.services.core_runtime import CoreRuntime

        grant = self.h.grant()
        view = self.h.new_view(grant_id=grant.grant_id, grant_revision=1)
        result = self.h.new_result(Privacy.PRIVATE)
        target = await self.h.output._target(view, result.privacy)
        receipt = await CoreRuntime._send_command(
            SimpleNamespace(), view, result, target, RenderedMessage("private")
        )
        self.assertIs(receipt.status, MessageStatus.FAILED)

    async def test_registration_budget_failure_falls_back_before_dispatch(self):
        from ygl_test_subject.services.generated_png import (
            GeneratedPngCoordinator, GeneratedPngError,
        )
        with patch.object(
            GeneratedPngCoordinator, "register",
            side_effect=GeneratedPngError("image_budget"),
        ):
            outcome = await self.h.output.route(self.h.new_view(), self.h.new_result())
        self.assertIs(outcome.status, OutputStatus.UNKNOWN)
        self.assertEqual(len(self.sent), 1)
        self.assertIsNone(self.sent[0].png_bytes)
        self.assertEqual(self.h.output._generated_png._entries, {})

    async def test_revoked_issuer_before_image_read_never_sends(self):
        view = self.h.new_view()
        original = self.h.output._command_card_renderer

        def renderer(*args, **kwargs):
            value = original(*args, **kwargs)
            self.h.issuer.release(view)
            return value

        self.h.output._command_card_renderer = renderer
        outcome = await self.h.output.route(view, self.h.new_result())
        self.assertIs(outcome.status, OutputStatus.FAILED)
        self.assertEqual(self.sent, [])

    async def test_cancel_preparing_image_releases_registered_context(self):
        from ygl_test_subject.services.generated_png import GeneratedPngCoordinator
        entered = asyncio.Event()
        gate = asyncio.Event()
        async def blocked_register(*_args):
            entered.set()
            await gate.wait()
        with patch.object(GeneratedPngCoordinator, "register", blocked_register):
            task = asyncio.create_task(
                self.h.output.route(self.h.new_view(), self.h.new_result())
            )
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(self.sent, [])
        self.assertEqual(self.h.output._generated_png._entries, {})

    async def test_expiry_after_preparation_is_fenced_before_dispatch(self):
        original = self.h.output._command_sender
        async def sender(view, result, target, payload, *, image_current=None):
            await asyncio.sleep(0)
            self.h.output._generated_png._clock = lambda: float("inf")
            return await original(
                view, result, target, payload, image_current=image_current
            )
        self.h.output._command_sender = sender
        await self.h.output.route(self.h.new_view(), self.h.new_result())
        self.assertEqual(self.sent, [])
        self.assertEqual(self.h.output._generated_png._entries, {})

    async def test_unload_before_output_never_sends(self):
        view = self.h.new_view()
        await self.h.lifecycle.stop("sample/demo", timeout=1)
        outcome = await self.h.output.route(view, self.h.new_result())
        self.assertIs(outcome.status, OutputStatus.FAILED)
        self.assertEqual(self.sent, [])
