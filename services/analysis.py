"""Bounded command-only model analysis over explicitly model-safe facts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import replace

from yomihime_game_link_sdk.display import TextBlock

from ..core.public_result import public_fact_strings


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


class CommandAnalysisService:
    def __init__(self, port=None, *, max_active=2, max_entries=128, ttl_seconds=300):
        self.port = port
        self.max_active, self.max_entries, self.ttl_seconds = (
            max_active,
            max_entries,
            ttl_seconds,
        )
        self.active = 0
        self.cache = OrderedDict()

    async def apply(self, invocation, target, result, *, current):
        request = result.analysis_request
        if request is None or result.document is None:
            return result
        await current()
        text, reason = None, "模型未配置"
        if result.model_facts is not None:
            # The real publication validator, including keys, sources and limits.
            tuple(public_fact_strings(result.model_facts))
        if self.port is not None and result.model_facts is not None:
            try:
                deadline = time.monotonic() + request.timeout_seconds
                if invocation.deadline is not None:
                    deadline = min(deadline, invocation.deadline)
                async with asyncio.timeout_at(deadline):
                    prepared = await self.port.prepare()
                await current()
                if prepared is not None:
                    identity = (
                        invocation.actor_id,
                        invocation.adapter_id,
                        invocation.conversation_id,
                        invocation.delivery_route,
                        repr(target),
                        invocation.module_id,
                        invocation.module_epoch,
                        invocation.capability_id,
                        result.privacy.value,
                        invocation.grant_id,
                        invocation.grant_revision,
                        request.template_id,
                        request.template_version,
                        request.focus_key,
                        request.instructions,
                        prepared.identity,
                        _plain(result.model_facts.facts),
                    )
                    key = hashlib.sha256(
                        json.dumps(
                            identity, ensure_ascii=False, sort_keys=True, default=str
                        ).encode()
                    ).hexdigest()
                    cached = self.cache.get(key)
                    now = time.monotonic()
                    if cached is not None and cached[0] > now:
                        text = cached[1]
                        self.cache.move_to_end(key)
                    elif self.active >= self.max_active:
                        reason = "模型请求额度暂不可用"
                    else:
                        self.active += 1
                        try:
                            remaining = deadline - now
                            if remaining <= 0:
                                raise TimeoutError
                            async with asyncio.timeout(remaining):
                                text = await prepared.generate(
                                    request, _plain(result.model_facts.facts)
                                )
                            await current()
                            if (
                                type(text) is not str
                                or not text.strip()
                                or len(text) > request.max_output_chars
                            ):
                                text, reason = None, "模型返回无效或超出输出预算"
                            else:
                                text = text.strip()
                                self.cache[key] = (
                                    time.monotonic() + self.ttl_seconds,
                                    text,
                                )
                                self.cache.move_to_end(key)
                                while len(self.cache) > self.max_entries:
                                    self.cache.popitem(last=False)
                        finally:
                            self.active -= 1
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                reason = "模型调用超时"
            except Exception as exc:
                kind = type(exc).__name__.lower()
                reason = (
                    "模型额度不足"
                    if any(s in kind for s in ("quota", "ratelimit", "credit"))
                    else "模型调用失败"
                )
        await current()
        # No fallback or late model output bypasses the original command proof.
        note = (
            "AI详细分析：\n" + text
            if text
            else "AI分析未生成：" + reason + "；以下保留结构化事实报告。"
        )
        document = replace(
            result.document,
            ordered_blocks=(*result.document.ordered_blocks, TextBlock(note)),
            card=None,
        )
        return replace(result, document=document, analysis_request=None)
