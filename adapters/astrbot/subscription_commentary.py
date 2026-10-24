"""Stateless subscription commentary through the configured AstrBot provider."""

from __future__ import annotations

import inspect
from uuid import uuid4


class AstrBotSubscriptionCommentary:
    """The adapter receives bounded public facts, never a session or Host event."""

    def __init__(self, context):
        self._context = context

    async def generate(self, request):
        select = getattr(self._context, "get_using_provider_async", None)
        if not callable(select):
            select = getattr(self._context, "get_using_provider", None)
        if not callable(select):
            return None
        # Omitting umo selects the deployment's provider without looking up
        # any group's conversational history, memory, or provider preference.
        provider = select()
        if inspect.isawaitable(provider):
            provider = await provider
        text_chat = getattr(provider, "text_chat", None)
        if not callable(text_chat):
            return None
        response = await text_chat(
            prompt=request.prompt,
            session_id="ygl-subscription-" + uuid4().hex,
            contexts=[],
            image_urls=[],
            func_tool=None,
            system_prompt="仅为给定公开事实生成简短评论。不要调用工具，不推测缺失事实。",
            persist=False,
        )
        text = getattr(response, "completion_text", None)
        return text.strip() if type(text) is str else None
