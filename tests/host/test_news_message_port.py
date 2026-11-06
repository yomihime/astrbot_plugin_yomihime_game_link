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
