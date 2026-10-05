"""Shared item input validation and exact-first bounded candidate lookup."""

from __future__ import annotations

import re
from typing import Protocol

from ..models import ItemCandidate

MAX_QUERY_LENGTH = 120
_ASCII_INTEGER = re.compile(r"[0-9]+\Z", re.ASCII)


def has_control(value: str) -> bool:
    return any(ord(char) < 32 or 0x7F <= ord(char) <= 0x9F for char in value)


def validate_item_query(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("请提供物品名称或数字 ID。")
    if has_control(value):
        raise ValueError(f"查询长度需为 1 到 {MAX_QUERY_LENGTH} 个字符。")
    value = value.strip()
    if not value or len(value) > MAX_QUERY_LENGTH:
        raise ValueError(f"查询长度需为 1 到 {MAX_QUERY_LENGTH} 个字符。")
    if _ASCII_INTEGER.fullmatch(value):
        if not 1 <= int(value) <= 2_147_483_647:
            raise ValueError("物品 ID 超出允许范围。")
    elif '"' in value or "\\" in value:
        raise ValueError("名称查询暂不支持双引号或反斜线；请改用物品 ID。")
    return value


def item_id_from_query(query: str) -> int | None:
    return int(query) if _ASCII_INTEGER.fullmatch(query) else None


class ItemSearch(Protocol):
    async def search(
        self, query: str, *, exact: bool = False
    ) -> tuple[tuple[ItemCandidate, ...], bool]: ...


async def search_item_candidates(
    source: ItemSearch, query: str
) -> tuple[tuple[ItemCandidate, ...], bool]:
    candidates, truncated = await source.search(query, exact=True)
    if not candidates and not truncated:
        candidates, truncated = await source.search(query)
    return candidates, truncated
