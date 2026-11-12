"""Synthetic durable sends: optional images cannot discard complete news text."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from ygl_test_subject.adapters.astrbot.message_port import AstrBotMessagePort
from ygl_test_subject.core.ports import MessageStatus, MessageTarget, RenderedMessage

from yomihime_game_link_sdk.subscriptions import ConversationKind, ConversationRef


class NewsMessagePortTests(unittest.IsolatedAsyncioTestCase):
    def target(self):
        return MessageTarget(
            "news-group",
            conversation=ConversationRef(
                "synthetic-platform",
                ConversationKind.GROUP,
                "news-group",
                "synthetic-platform",
            ),
        )

    async def test_image_construction_failure_sends_complete_text_once(self):
        context = SimpleNamespace(send_message=AsyncMock(return_value=True))
        image = Mock(side_effect=ValueError("image component unavailable"))
        port = AstrBotMessagePort(
            context,
            plain_factory=lambda text: ("text", text),
            chain_factory=tuple,
            image_factory=image,
        )
        text = "Title; category; time; summary; https://example.invalid/news/1"
        receipt = await port.send(
            self.target(), RenderedMessage(text, png_bytes=b"png")
        )
        self.assertIs(receipt.status, MessageStatus.ACCEPTED)
        context.send_message.assert_awaited_once_with(
            "synthetic-platform:GroupMessage:news-group", (("text", text),)
        )
        image.assert_called_once_with(b"png")

    async def test_unknown_image_send_never_attempts_text_resend(self):
        for result in (None, ValueError("dispatch outcome unknown")):
            send = AsyncMock(
                return_value=result if not isinstance(result, Exception) else None,
                side_effect=result if isinstance(result, Exception) else None,
            )
            port = AstrBotMessagePort(
                SimpleNamespace(send_message=send),
                plain_factory=lambda text: ("text", text),
                chain_factory=tuple,
                image_factory=lambda content: ("image", content),
            )
            receipt = await port.send(
                self.target(), RenderedMessage("Complete text", png_bytes=b"png")
            )
            self.assertIs(receipt.status, MessageStatus.UNKNOWN)
            send.assert_awaited_once_with(
                "synthetic-platform:GroupMessage:news-group",
                (("text", "Complete text"), ("image", b"png")),
            )

    async def test_official_qq_opaque_ids_and_instance_routes_are_preserved(self):
        uid = "A1B2C3D4E5F60718293A4B5C6D7E8F90"
        for instance in ("default_123456", "official_second"):
            for kind, message_type in (
                (ConversationKind.DIRECT, "FriendMessage"),
                (ConversationKind.GROUP, "GroupMessage"),
            ):
                with self.subTest(instance=instance, kind=kind):
                    lookup = Mock(
                        return_value=SimpleNamespace(
                            meta=lambda: SimpleNamespace(
                                name="qq_official", id=instance
                            )
                        )
                    )
                    send = AsyncMock(return_value=True)
                    port = AstrBotMessagePort(
                        SimpleNamespace(send_message=send, get_platform_inst=lookup),
                        plain_factory=lambda text: text,
                        chain_factory=tuple,
                    )
                    target = MessageTarget(
                        uid, conversation=ConversationRef(instance, kind, uid, instance)
                    )
                    receipt = await port.send(target, RenderedMessage("news"))
                    self.assertIs(receipt.status, MessageStatus.UNKNOWN)
                    lookup.assert_called_once_with(instance)
                    send.assert_awaited_once_with(
                        f"{instance}:{message_type}:{uid}", ("news",)
                    )

    async def test_context_result_is_not_fabricated_platform_receipt(self):
        for name, result, expected in (
            ("qq_official", True, MessageStatus.UNKNOWN),
            ("qq_official", False, MessageStatus.FAILED),
            ("qq_official", None, MessageStatus.UNKNOWN),
            ("qq_official_webhook", True, MessageStatus.UNKNOWN),
            ("qq_official_webhook", False, MessageStatus.FAILED),
            ("qq_official_webhook", None, MessageStatus.UNKNOWN),
            ("aiocqhttp", True, MessageStatus.ACCEPTED),
        ):
            with self.subTest(name=name, result=result):
                send = AsyncMock(return_value=result)
                port = AstrBotMessagePort(
                    SimpleNamespace(
                        send_message=send,
                        get_platform_inst=lambda _: SimpleNamespace(
                            meta=lambda: SimpleNamespace(name=name)
                        ),
                    ),
                    plain_factory=str,
                    chain_factory=tuple,
                )
                receipt = await port.send(self.target(), RenderedMessage("news"))
                self.assertIs(receipt.status, expected)
                self.assertIsNone(receipt.platform_message_id)
                send.assert_awaited_once()

    async def test_platform_disappearing_or_metadata_failure_is_unknown(self):
        for lookup in (Mock(return_value=None), Mock(side_effect=RuntimeError)):
            send = AsyncMock(return_value=True)
            port = AstrBotMessagePort(
                SimpleNamespace(
                    send_message=send,
                    get_platform_inst=lookup,
                ),
                plain_factory=str,
                chain_factory=tuple,
            )
            receipt = await port.send(self.target(), RenderedMessage("news"))
            self.assertIs(receipt.status, MessageStatus.UNKNOWN)
            send.assert_awaited_once()
