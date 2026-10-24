"""Private command projection: existing display content to a bounded PNG card.

There are no module-specific grades, HTTP reads, HTML or external assets here.
Text and image use the same complete projection; failure before dispatch chooses
text, or an explicit size hint when the complete text exceeds the send budget.
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass, replace

from yomihime_game_link_sdk import cards
from yomihime_game_link_sdk.declarations import ModuleManifest
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
from .command_text import FULL_BRAND
from .layout_renderer import (
    apply_theme_selection,
    render_document,
    render_document_pages,
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
    bitmaps: tuple[RenderedBitmap, ...] = ()


@dataclass(frozen=True)
class HelpModule:
    """One module from the exact availability-filtered help snapshot."""

    module_id: str
    epoch: int
    manifest: ModuleManifest
    summary: str


@dataclass(frozen=True)
class HelpCardSource:
    """Catalog facts retained alongside the bounded legacy text projection."""

    document: DisplayDocument
    registry_revision: int
    modules: tuple[HelpModule, ...] = ()
    recommendation: str | None = None
    directory: bool = False
    hints: tuple[str, ...] = ()


def build_help_card(source: HelpCardSource, *, logos=None) -> cards.Document:
    """Generic catalog-to-card projection; logos are already trusted bytes."""
    logos = logos or {}
    if not source.directory:
        if source.document.card is not None:
            return replace(
                source.document.card,
                brand=FULL_BRAND,
                logo=logos.get(source.modules[0].module_id) if source.modules else None,
                blocks=(
                    *source.document.card.blocks,
                    *(cards.Text(hint, style="caption") for hint in source.hints),
                ),
            )
        blocks = tuple(
            cards.Text(line)
            for block in source.document.ordered_blocks
            for line in TextPresenter()._render_block(block, _MAX_CONTENT)
        )
        return cards.Document(
            source.document.title,
            (*blocks, *(cards.Text(hint, style="caption") for hint in source.hints)),
            subtitle=source.document.subject,
            brand=FULL_BRAND,
            logo=logos.get(source.modules[0].module_id) if source.modules else None,
        )
    blocks = []
    recommended = None
    for module in source.modules:
        manifest = module.manifest
        display = manifest.display
        name = display.default_name if display else manifest.route
        short_name = display.short_name if display else None
        section = cards.Section(
            name,
            (
                cards.Text("查看模块命令与用法"),
                cards.Text(f"/ygl {manifest.route} help", style="heading"),
            ),
            subtitle=short_name or manifest.route,
            panel=False,
        )
        logo = logos.get(module.module_id)
        contents = (
            (
                cards.Row(
                    (cards.Image(logo, width=128, height=128), section), weights=(1, 5)
                ),
            )
            if logo is not None
            else (section,)
        )
        blocks.append(cards.Section("", contents))
        if manifest.route == source.recommendation:
            recommended = name
    if not blocks:
        blocks.append(
            cards.Section("暂无可用模块", (cards.Text("模块就绪后会在这里出现"),))
        )
    blocks.extend(cards.Text(hint, style="caption") for hint in source.hints)
    return cards.Document(
        "游戏，从这里连结",
        tuple(blocks),
        subtitle=(
            f"从{recommended}开始吧"
            if recommended
            else "输入 /ygl help，查看当前可用模块"
        ),
        brand=FULL_BRAND,
        layout="directory",
    )


class CommandCardRenderer:
    """Trusted, command-only presentation policy. Optional Pillow fails to text."""

    def __init__(
        self, *, max_text_chars=6000, max_text_lines=200, font_provider_factory=None
    ):
        self._max_text_chars = max_text_chars
        self._max_text_lines = max_text_lines
        self._text = TextPresenter()
        self._font_provider_factory = font_provider_factory or BundledFontProvider

    def __call__(
        self,
        document,
        *,
        audience,
        core_theme="warm_paper",
        module_theme=None,
        image=True,
    ):
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
            # The legacy facts and declarative card each have their own input
            # bounds. A large optional card must not consume the complete
            # fallback's budget or turn usable text into a size-only hint.
            dense = (
                document.card is not None
                and len(document.card.blocks) == 1
                and type(document.card.blocks[0]) is cards.DenseCollage
            )
            content_limit = 160_000 if dense else _MAX_CONTENT
            self._budget(replace(document, card=None), limit=content_limit)
            spec, text = self._project(document, audience, limit=content_limit)
        except BitmapUnavailable:
            return CommandPresentation(TOO_LARGE)
        fallback = text if self._fits_text(text) else TOO_LARGE
        if not image:
            return CommandPresentation(fallback)
        bitmaps = ()
        try:
            if document.card is not None:
                selected = apply_theme_selection(
                    document.card, core_theme, module_theme
                )
                if len(selected.blocks) == 1 and type(selected.blocks[0]) in (
                    cards.WeightedCollage,
                    cards.DenseCollage,
                ):
                    bitmaps = render_document_pages(
                        selected, font_provider=self._font_provider_factory()
                    )
                    bitmap = bitmaps[0]
                else:
                    bitmap = render_document(
                        selected,
                        font_provider=self._font_provider_factory(),
                    )
            else:
                bitmap = render_card(spec, font_provider=self._font_provider_factory())
        except Exception:
            bitmap = None
            bitmaps = ()
        return CommandPresentation(fallback, bitmap, bitmaps)

    def help(
        self, text: str, *, image: bool = True, card=None, core_theme="warm_paper"
    ) -> CommandPresentation:
        """Render the same coherent help facts supplied by the Core catalog."""
        if type(text) is not str or not text.strip():
            raise ValueError("invalid help text")
        fallback = text if self._fits_text(text) else TOO_LARGE
        title, _, body = text.partition("\n")
        if not image or len(text) > _MAX_CONTENT:
            return CommandPresentation(fallback)
        try:
            if card is not None:
                bitmap = render_document(
                    apply_theme_selection(card, core_theme),
                    font_provider=self._font_provider_factory(),
                )
            else:
                bitmap = render_card(
                    CardSpec(title, (Section(paragraphs=tuple(body.splitlines())),)),
                    font_provider=self._font_provider_factory(),
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
    def _budget(document, *, limit=_MAX_CONTENT):
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
                if characters > limit:
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

    def _project(self, document, audience, *, limit=_MAX_CONTENT):
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
                paragraphs = (block.fallback_text or f"图片不可用: {block.alt_text}",)
                section = Section(paragraphs=paragraphs)
            elif isinstance(block, ItemGridBlock):
                items = tuple(
                    item
                    for item in block.items
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
                        self._text._render_grid_item(item, limit)
                        for item in items
                        if item.asset_id is None
                    )
                    if not paragraphs:
                        if block.fallback_text is not None:
                            paragraphs = (block.fallback_text,)
                        elif block.required:
                            raise RenderingFailure()
                section = Section(paragraphs=paragraphs)
            elif isinstance(block, TableBlock):
                rows = tuple(
                    Row(tuple(self._text._format_value(value, limit) for value in row))
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
                paragraphs = tuple(self._text._render_block(block, limit))
                section = Section(paragraphs=paragraphs)
            used += sum(len(line) + 1 for line in paragraphs)
            if used > limit:
                raise BitmapUnavailable("layout_overflow")
            lines.extend(paragraphs)
            sections.append(section)
        sources = tuple(document.sources)
        if sources:
            lines.append("来源：" + " / ".join(sources))
        text = "\n".join(lines)
        if len(text) > limit:
            raise BitmapUnavailable("layout_overflow")
        return (
            CardSpec(
                document.title, tuple(sections), document.subject, sources=sources
            ),
            text,
        )
