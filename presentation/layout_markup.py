"""Explicit, bounded inline tags for declarative card text; never HTML or eval.

Call ``parse_inline_markup`` deliberately. Ordinary Document/Text strings stay
literal. Closing tags must nest exactly. Escape literal brackets and backslashes
with ``\\[`` / ``\\]`` / ``\\\\``; unknown escapes and unescaped ``]`` are errors.

Supported tags: [color=#rrggbb]...[/color], [strong], [secondary], [emphasis],
[b] (semantic strong alias, not a separate bold font face), and the
gray/green/blue/purple/orange/pink/gold color aliases.
[gold] is only a color. [gold glow] explicitly requests the fixed gold face and
existing glow effect. It cannot be nested with explicit colors. The innermost
emphasis wins; text is never inspected for scores or assigned a grade.
"""

from __future__ import annotations

import re
from unicodedata import category

from .layout_document import Inline, Span
from .rich_text import MAX_LINE_CHARS, MAX_SPAN_CHARS, MAX_SPANS

MAX_MARKUP_CHARS = 8192
MAX_MARKUP_DEPTH = 8
_COLORS = {
    "gray": "#666666",
    "green": "#1eff00",
    "blue": "#0070ff",
    "purple": "#a335ee",
    "orange": "#ff8000",
    "pink": "#e268a8",
    "gold": "#e5cc80",
}
_EMPHASIS = {
    "strong": "strong",
    "b": "strong",
    "secondary": "secondary",
    "emphasis": "emphasis",
}


class MarkupError(ValueError):
    """Stable invalid-input error without echoing source text."""

    def __init__(self):
        super().__init__("Invalid bounded inline markup")


def _validate_text(text):
    if (
        type(text) is not str
        or len(text) > MAX_MARKUP_CHARS
        or any(category(c).startswith("C") or c in "\u2028\u2029" for c in text)
    ):
        raise MarkupError()


def escape_inline_markup(text: str) -> str:
    """Escape plain single-line text for deliberate insertion into tagged text.

    Escaping is not automatic; never parse arbitrary upstream content instead
    of passing it as a plain string. The returned source remains bounded.
    """
    _validate_text(text)
    escaped = text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
    if len(escaped) > MAX_MARKUP_CHARS:
        raise MarkupError()
    return escaped


def parse_inline_markup(text: str) -> Inline:
    """Parse the documented closed-tag grammar to immutable Inline/Span values.

    Non-empty output is at most 1024 visible characters, 32 spans of at most
    512 characters each, and eight nested tags. Empty input or style-only text
    is rejected: use the ordinary empty string when a field is optional.
    """
    _validate_text(text)
    spans: list[Span] = []
    stack: list[tuple[str, str | None, str, bool]] = []
    color = None
    emphasis = "normal"
    buffer: list[str] = []
    visible = 0

    def flush():
        nonlocal visible
        if not buffer:
            return
        content = "".join(buffer)
        buffer.clear()
        visible += len(content)
        if visible > MAX_LINE_CHARS:
            raise MarkupError()
        if (
            spans
            and spans[-1].color == color
            and spans[-1].emphasis == emphasis
            and len(spans[-1].text) < MAX_SPAN_CHARS
        ):
            available = MAX_SPAN_CHARS - len(spans[-1].text)
            prior = spans.pop()
            spans.append(Span(prior.text + content[:available], color, emphasis))
            content = content[available:]
        for start in range(0, len(content), MAX_SPAN_CHARS):
            spans.append(Span(content[start : start + MAX_SPAN_CHARS], color, emphasis))
            if len(spans) > MAX_SPANS:
                raise MarkupError()

    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 1
            if index == len(text) or text[index] not in "[]\\":
                raise MarkupError()
            buffer.append(text[index])
            index += 1
        elif char == "]":
            raise MarkupError()
        elif char != "[":
            buffer.append(char)
            index += 1
        else:
            flush()
            end = text.find("]", index + 1)
            if end < 0 or end - index > 64:
                raise MarkupError()
            tag = text[index + 1 : end]
            if tag.startswith("/"):
                if not stack or tag[1:] != stack[-1][0]:
                    raise MarkupError()
                _, color, emphasis, _ = stack.pop()
            else:
                if len(stack) >= MAX_MARKUP_DEPTH:
                    raise MarkupError()
                old_color, old_emphasis = color, emphasis
                glow = tag == "gold glow"
                if glow:
                    if color is not None:
                        raise MarkupError()
                    name, emphasis = "gold", "gold"
                elif tag in _COLORS or re.fullmatch(r"color=#[0-9a-fA-F]{6}", tag):
                    if emphasis == "gold" or any(frame[3] for frame in stack):
                        raise MarkupError()
                    if tag in _COLORS:
                        name, color = tag, _COLORS[tag]
                    else:
                        name, color = "color", tag[6:].lower()
                elif tag in _EMPHASIS:
                    name, emphasis = tag, _EMPHASIS[tag]
                else:
                    raise MarkupError()
                stack.append((name, old_color, old_emphasis, glow))
            index = end + 1
    flush()
    if stack or not spans:
        raise MarkupError()
    return Inline(tuple(spans))
