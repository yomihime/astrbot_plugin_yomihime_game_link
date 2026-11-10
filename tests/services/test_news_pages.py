"""Public news pages remain one ordered, all-or-text Host dispatch."""

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from ygl_test_subject.adapters.astrbot.message_port import AstrBotMessagePort
from ygl_test_subject.core.ports import MessageStatus, MessageTarget, RenderedMessage
from ygl_test_subject.services.delivery import DeliveryService

from yomihime_game_link_sdk.display import (
    DisplayDocument,
    Link,
    LinksBlock,
    TextBlock,
    UnknownBlock,
)
from yomihime_game_link_sdk.subscriptions import ConversationKind, ConversationRef


class NewsPagesTests(unittest.IsolatedAsyncioTestCase):
    def target(self, *, authorized=False):
        return MessageTarget(
            "recipient",
            recipient_id="recipient" if authorized else None,
            authorized=authorized,
            conversation=ConversationRef(
                "platform", ConversationKind.DIRECT, "recipient", "platform"
            ),
        )

    def port(self, send, image=None):
        return AstrBotMessagePort(
            SimpleNamespace(send_message=send),
            plain_factory=lambda text: ("text", text),
            chain_factory=tuple,
            image_factory=image or (lambda content: ("image", content)),
        )

    async def test_ordered_pages_one_dispatch(self):
        send = AsyncMock(return_value=True)
        result = await self.port(send).send(
            self.target(), RenderedMessage("complete text", png_pages=(b"a", b"b"))
        )
        self.assertIs(result.status, MessageStatus.ACCEPTED)
        send.assert_awaited_once_with(
            "platform:FriendMessage:recipient",
            (("text", "complete text"), ("image", b"a"), ("image", b"b")),
        )

    async def test_failed_second_page_discards_all_images(self):
        send = AsyncMock(return_value=True)
        image = Mock(side_effect=[("image", b"a"), ValueError("broken")])
        await self.port(send, image).send(
            self.target(), RenderedMessage("complete text", png_pages=(b"a", b"b"))
        )
        send.assert_awaited_once_with(
            "platform:FriendMessage:recipient",
            (("text", "complete text\n网页截图暂不可用，请查看原文。"),),
        )

    async def test_unknown_never_retries(self):
        for error in (None, RuntimeError("unknown")):
            send = AsyncMock(return_value=None, side_effect=error)
            result = await self.port(send).send(
                self.target(), RenderedMessage("text", png_pages=(b"a", b"b"))
            )
            self.assertIs(result.status, MessageStatus.UNKNOWN)
            send.assert_awaited_once()

    async def test_authorized_pages_rejected(self):
        send = AsyncMock()
        result = await self.port(send).send(
            self.target(authorized=True), RenderedMessage("text", png_pages=(b"a",))
        )
        self.assertIs(result.status, MessageStatus.FAILED)
        send.assert_not_awaited()

    def test_fallback_preserves_text_blocks_and_optional_subject(self):
        document = DisplayDocument(
            title="标题",
            subject="简述",
            ordered_blocks=(
                LinksBlock(
                    (
                        Link(
                            "官方原文",
                            "https://ff.web.sdo.com/web8/index.html#/newstab/newscont/123",
                        ),
                    )
                ),
                UnknownBlock(
                    kind="official_desktop_screenshot_v1:" + "a" * 64,
                    required=False,
                    fallback_text="截图不可用",
                ),
            ),
        )
        document = replace(
            document,
            ordered_blocks=(TextBlock("类别：公告\n时间：今天"),)
            + document.ordered_blocks,
        )
        for subject in ("简述", None):
            result = DeliveryService._public_text_fallback(
                replace(document, subject=subject), screenshot_available=True
            )
            self.assertIn("类别：公告\n时间：今天", result.text)
            self.assertIn("https://ff.web.sdo.com/", result.text)
            self.assertNotIn("None", result.text)
            self.assertNotIn("暂不可用", result.text)
