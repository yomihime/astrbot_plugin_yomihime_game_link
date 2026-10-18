"""FF14-owned visual declarations; the host owns layout, themes and painting."""

from __future__ import annotations

from typing import Literal

from yomihime_game_link_sdk import cards


def ff14_card(
    title: cards.Content,
    *,
    category: str,
    blocks: tuple[cards.Block, ...] = (),
    subtitle: cards.Content = "",
    layout: Literal["directory", "detail", "table"] = "detail",
    sources: tuple[cards.Content, ...] = (),
) -> cards.Document:
    """Share branding while Core binds registered resources and settings.

    A missing explicit logo inherits manifest display.logo_resource. Core reads
    its pinned captured bytes: modules must not rely on runtime filesystem paths.
    """
    return cards.Document(
        title=title,
        subtitle=subtitle,
        brand=f"游戏连结 · FF14 · {category}",
        logo=None,
        blocks=blocks,
        layout=layout,
        sources=sources,
    )


__all__ = ["ff14_card"]
