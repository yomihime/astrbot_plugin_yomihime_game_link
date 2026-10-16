"""Private command projection: existing display content to a bounded PNG card.

There are no module-specific grades, HTTP reads, HTML or external assets here.
Text and image use the same complete projection; failure before dispatch chooses
text, or an explicit size hint when the complete text exceeds the send budget.
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass

from yomihime_game_link_sdk.display import (
    DisplayAudience,
    DisplayDocument,
    ImageBlock,
    ItemGridBlock,
    Privacy,
    TableBlock,
)

from ..core.contracts.validation_boundary import validate_contract
from ..core.contracts.version import CONTRACT_VERSION
from .bundled_font import BundledFontProvider
from .card_bitmap import (
    BitmapUnavailable,
    CardSpec,
    RenderedBitmap,
    Row,
    Section,
    render_card,
)
from .png_validation import validate_png
from .rendering import RenderingFailure
from .text import TextPresenter

TOO_LARGE = "内容过长，无法完整展示。请缩小查询范围后重试。"
_MAX_CONTENT = 24_000


@dataclass(frozen=True)
class CommandPresentation:
    text: str
    bitmap: RenderedBitmap | None = None


class CommandCardRenderer:
    """Trusted, command-only presentation policy. Optional Pillow fails to text."""

    def __init__(self, *, max_text_chars=6000, max_text_lines=200):
        self._max_text_chars = max_text_chars
        self._max_text_lines = max_text_lines
        self._text = TextPresenter()

    def __call__(self, document, *, audience):
        validate_contract(document)
        validate_contract(audience)
        if type(document) is not DisplayDocument or (
            document.privacy is Privacy.PRIVATE
            and audience is not DisplayAudience.PRIVATE
        ):
            raise ValueError("invalid command document")
        if document.schema_version != CONTRACT_VERSION:
            return CommandPresentation("不支持的显示版本")
        try:
            self._budget(document)
            spec, text = self._project(document, audience)
        except BitmapUnavailable:
            return CommandPresentation(TOO_LARGE)
        fallback = text if self._fits_text(text) else TOO_LARGE
        try:
            bitmap = render_card(spec, font_provider=BundledFontProvider())
        except Exception:
            bitmap = None
        return CommandPresentation(fallback, bitmap)

    def help(self, text: str, *, image: bool = True) -> CommandPresentation:
        """The Host owns help text and its event-local recommendation."""
        if type(text) is not str or not text.strip():
            raise ValueError("invalid help text")
        fallback = text if self._fits_text(text) else TOO_LARGE
        title, _, body = text.partition("\n")
        if not image or len(text) > _MAX_CONTENT:
            return CommandPresentation(fallback)
        try:
            bitmap = render_card(
                CardSpec(title, (Section(paragraphs=tuple(body.splitlines())),)),
                font_provider=BundledFontProvider(),
            )
            # Help has no module result resource; validate its actual PNG bytes
            # here before handing it to the exact-event Host adapter.
            validate_png(bitmap)
        except Exception:
            bitmap = None
        return CommandPresentation(fallback, bitmap)

    def _fits_text(self, text):
        return (
            len(text) <= self._max_text_chars
            and len(text.splitlines()) <= self._max_text_lines
        )

    @staticmethod
    def _budget(document):
        # Preflight nested scalar structures before formatting or allocating a
        # bitmap. Container width, total text and nesting are all bounded.
        nodes = characters = 0

        def visit(value, depth=0):
            nonlocal nodes, characters
            nodes += 1
            if nodes > 4096 or depth > 16:
                raise BitmapUnavailable("layout_overflow")
            if isinstance(value, str):
                characters += len(value)
                if characters > _MAX_CONTENT:
                    raise BitmapUnavailable("layout_overflow")
            elif type(value) is int and value.bit_length() > 16384:
                raise BitmapUnavailable("layout_overflow")
            elif is_dataclass(value):
                for item in fields(value):
                    visit(getattr(value, item.name), depth + 1)
            elif isinstance(value, Mapping):
                if len(value) > 4096:
                    raise BitmapUnavailable("layout_overflow")
                for key, item in value.items():
                    visit(key, depth + 1)
                    visit(item, depth + 1)
            elif isinstance(value, (tuple, list)):
                if len(value) > 4096:
                    raise BitmapUnavailable("layout_overflow")
                for item in value:
                    visit(item, depth + 1)

        visit(document)

    def _project(self, document, audience):
        lines = [document.title, document.subject]
        sections = []
        used = sum(map(len, lines)) + 1
        for block in document.ordered_blocks:
            if isinstance(block, ImageBlock):
                if (
                    block.visibility is Privacy.PRIVATE
                    and audience is DisplayAudience.PUBLIC
                ):
                    continue
                if block.required and block.fallback_text is None:
                    raise RenderingFailure()
                paragraphs = (
                    block.fallback_text or f"图片不可用: {block.alt_text}",
                )
                section = Section(paragraphs=paragraphs)
            elif isinstance(block, ItemGridBlock):
                items = tuple(
                    item for item in block.items
                    if not (
                        item.visibility is Privacy.PRIVATE
                        and audience is DisplayAudience.PUBLIC
                    )
                )
                if block.required and any(item.asset_id is not None for item in items):
                    if block.fallback_text is None:
                        raise RenderingFailure()
                    paragraphs = (block.fallback_text,)
                else:
                    paragraphs = tuple(
                        self._text._render_grid_item(item, _MAX_CONTENT)
                        for item in items if item.asset_id is None
                    )
                    if not paragraphs:
                        if block.fallback_text is not None:
                            paragraphs = (block.fallback_text,)
                        elif block.required:
                            raise RenderingFailure()
                section = Section(paragraphs=paragraphs)
            elif isinstance(block, TableBlock):
                rows = tuple(
                    Row(tuple(
                        self._text._format_value(value, _MAX_CONTENT) for value in row
                    ))
                    for row in block.rows
                )
                section = Section(columns=block.columns, rows=rows)
                paragraphs = (
                    " | ".join(block.columns),
                    *(" | ".join(row.cells) for row in rows),
                )
                if not rows and block.fallback_text:
                    section = Section(paragraphs=(block.fallback_text,))
                    paragraphs = (block.fallback_text,)
            else:
                paragraphs = tuple(self._text._render_block(block, _MAX_CONTENT))
                section = Section(paragraphs=paragraphs)
            used += sum(len(line) + 1 for line in paragraphs)
            if used > _MAX_CONTENT:
                raise BitmapUnavailable("layout_overflow")
            lines.extend(paragraphs)
            sections.append(section)
        sources = tuple(document.sources)
        if sources:
            lines.append("来源：" + " / ".join(sources))
        text = "\n".join(lines)
        if len(text) > _MAX_CONTENT:
            raise BitmapUnavailable("layout_overflow")
        return (
            CardSpec(document.title, tuple(sections), document.subject, sources=sources),
            text,
        )
