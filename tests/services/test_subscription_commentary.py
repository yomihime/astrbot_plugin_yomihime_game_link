"""Host model requests carry only isolated public-event facts."""

import unittest
from types import SimpleNamespace

from ygl_test_subject.adapters.astrbot.subscription_commentary import (
    AstrBotSubscriptionCommentary,
)

from yomihime_game_link_sdk.subscriptions import CommentaryRequest


class SubscriptionCommentaryAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_host_model_gets_no_group_history_or_tools_and_does_not_persist(self):
        calls = []

        async def text_chat(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(completion_text=" public comment ")

        async def selected():
            return SimpleNamespace(text_chat=text_chat)

        adapter = AstrBotSubscriptionCommentary(
            SimpleNamespace(get_using_provider_async=selected)
        )
        request = CommentaryRequest("public-settlement", 1, "bounded public facts")
        self.assertEqual(await adapter.generate(request), "public comment")
        await adapter.generate(request)
        for call in calls:
            self.assertEqual(call["prompt"], request.prompt)
            self.assertEqual(call["contexts"], [])
            self.assertEqual(call["image_urls"], [])
            self.assertIsNone(call["func_tool"])
            self.assertFalse(call["persist"])
            self.assertNotIn("umo", call)
        self.assertNotEqual(calls[0]["session_id"], calls[1]["session_id"])

    async def test_unconfigured_host_provider_returns_fallback(self):
        adapter = AstrBotSubscriptionCommentary(
            SimpleNamespace(get_using_provider=lambda: None)
        )
        self.assertIsNone(
            await adapter.generate(CommentaryRequest("public", 1, "facts"))
        )
