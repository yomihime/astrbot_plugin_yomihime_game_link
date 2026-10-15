"""Human command wording; never used to construct tool facts."""

from __future__ import annotations

import random
from dataclasses import replace

from yomihime_game_link_sdk.display import DisplayDocument, TextBlock

from .text import TextPresenter

FULL_BRAND = "如月怜的游戏连结"


def command_title(module_name: str, feature: str) -> str:
    """Use a declared display name supplied by the caller."""

    return f"游戏连结 · {module_name} · {feature}"


def render_total_help(
    document: DisplayDocument,
    modules: tuple[tuple[str, str], ...],
    *,
    seed: int,
    presenter: TextPresenter,
    max_chars: int,
) -> str:
    """Append an event-stable recommendation only if the whole view fits.

    The directory always precedes the optional recommendation. Existing text
    bounds and explicit truncation remain in force for oversized directories.
    No event or registry objects are retained by this projection.
    """

    directory = replace(document, title=FULL_BRAND)
    text = presenter.render(directory, max_chars=max_chars)
    if not modules:
        return text
    name, route = random.Random(seed).choice(modules)
    recommendation = f"从 {name} 开始吧：/ygl {route} help"
    # Avoid a truncated recommendation or recommending before the complete
    # directory can be shown. This also covers names containing the marker.
    if len(text) + 1 + len(recommendation) > max_chars:
        return text
    candidate = replace(
        directory,
        ordered_blocks=directory.ordered_blocks + (TextBlock(recommendation),),
    )
    return presenter.render(candidate, max_chars=max_chars)
