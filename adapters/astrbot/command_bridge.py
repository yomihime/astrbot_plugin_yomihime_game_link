"""Small AstrBot-to-Core bridge for the help-only command surface."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Protocol

from ...core.help_catalog import HelpCatalog
from ...core.registry import Registry
from ...presentation.text import TextPresenter

_ROUTE = re.compile(r"[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*\Z")
_USAGE = "用法：/ygl [help | <模块路由> help]。模块路由不支持引号；多余参数会被拒绝。"


class MessageTextEvent(Protocol):
    """The sole host event capability needed to render public help."""

    def get_message_str(self) -> str: ...


class AstrBotCommandBridge:
    """Dispatch root and module help without deriving actor identity or actions."""

    def __init__(
        self,
        registry: Registry,
        *,
        catalog: HelpCatalog | None = None,
        presenter: TextPresenter | None = None,
        max_chars: int = 4000,
    ) -> None:
        if not isinstance(registry, Registry):
            raise TypeError("registry must be a Registry")
        if type(max_chars) is not int or max_chars < 1:
            raise ValueError("max_chars must be a positive integer")
        self._registry = registry
        self._catalog = catalog or HelpCatalog()
        self._presenter = presenter or TextPresenter()
        self._max_chars = max_chars

    def handle(self, event: MessageTextEvent) -> str:
        """Render one help response from trusted raw host text and a Core snapshot."""

        get_message_str: Callable[[], object] | None = getattr(
            event, "get_message_str", None
        )
        if not callable(get_message_str):
            return _USAGE
        try:
            message = get_message_str()
        except Exception:
            return _USAGE
        if not isinstance(message, str):
            return _USAGE

        # AstrBot v4.28.0 normalizes whitespace before filtering commands. Mirror
        # its token boundaries here; quoted strings are ordinary tokens, not a
        # shell-like quoting language. W0-H must verify target parser behavior.
        tokens = message.split()
        if tokens and tokens[0] == "/ygl":
            tokens = tokens[1:]
        elif tokens and tokens[0] == "ygl":
            tokens = tokens[1:]
        else:
            return _USAGE

        snapshot = self._registry.snapshot()
        if not tokens or tokens == ["help"]:
            document = self._catalog.total(snapshot)
        elif (
            len(tokens) == 2
            and _ROUTE.fullmatch(tokens[0]) is not None
            and tokens[1] == "help"
        ):
            document = self._catalog.module(snapshot, tokens[0])
        else:
            return _USAGE
        return self._presenter.render(document, max_chars=self._max_chars)


__all__ = ["AstrBotCommandBridge", "MessageTextEvent"]
