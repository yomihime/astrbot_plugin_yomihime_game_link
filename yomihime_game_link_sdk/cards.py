"""Host-independent declarative cards. Core owns validation, layout and painting.

Modules supply already-decided content, colors and in-memory static assets.
Constructors are descriptive; no paths, URLs, CSS, fonts or game scoring API.
The enclosing DisplayDocument controls the privacy of the entire card.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from .storage import CacheVisibility

Align: TypeAlias = Literal["left", "center", "right"]
VAlign: TypeAlias = Literal["top", "center", "bottom"]


@dataclass(frozen=True)
class Asset:
    """Static image source, never a path/URL or permission to fetch one.

    SVG content is UTF-8 text or bytes; PNG/JPEG content is immutable bytes.
    Core enforces format, byte, pixel and element limits. Required assets fail
    the whole card on decode failure; optional ones may use their alt text.
    """

    kind: Literal["svg", "png", "jpeg"]
    content: str | bytes
    alt: str = ""
    required: bool = True


@dataclass(frozen=True)
class ResourceImage:
    """Opaque prepared resource; the current invocation must authorize reads.

    PUBLIC and USER describe resource partitions, never grant access. Rendering
    performs no network IO. The enclosing DisplayDocument owns result privacy.
    """

    asset_id: str
    visibility: CacheVisibility = CacheVisibility.PUBLIC
    alt: str = ""
    required: bool = False


@dataclass(frozen=True)
class CollageItem:
    """Generic positive weight, stable unique key and literal display labels."""

    key: str
    weight: int | float
    image: Asset | ResourceImage | None = None
    title: str = ""
    caption: str = ""


@dataclass(frozen=True)
class WeightedCollage:
    """A sole document body composed by Core into bounded, indexed PNG pages.

    Actual integer tile area is monotonic in weight, with deterministic key
    ties. This is not an exact proportional-area chart: relative target weights
    are capped at 16:1 and shelf widths at 2:1 to retain at least two thirds of
    the preferred source aspect when covered. Core may limit label overlays to
    two lines; ordered_blocks must retain the complete text fallback.
    """

    items: tuple[CollageItem, ...]
    max_height: int = 2800
    max_pages: int = 8
    preferred_aspect: float = 2 / 3


@dataclass(frozen=True)
class DenseCollageItem:
    """One square grid span; tuple order is the caller's logical ranking."""

    key: str
    span: int
    image: Asset | ResourceImage | None = None
    title: str = ""
    caption: str = ""


@dataclass(frozen=True)
class DenseCollage:
    """Deterministic bounded grid, with edge-to-edge public content width.

    Document.width equals columns * cell_width. A span occupies k by k cells.
    Pages may contain nonconsecutive logical ranks, without clipping tiles.
    cover crops proportionally; contain preserves all of the source image.
    No game-specific weights or thresholds belong in this declaration.
    """

    items: tuple[DenseCollageItem, ...]
    columns: int = 20
    cell_width: int = 54
    cell_height: int = 81
    max_height: int = 2592
    max_pages: int = 8
    fit: Literal["cover", "contain"] = "cover"


@dataclass(frozen=True)
class Span:
    """Explicit color is #rrggbb. ``gold`` requests the fixed gold glow style.

    The gold style uses the established #e5cc80 face and cannot override color.
    Ordinary gold-colored text is ``Span(text, color='#e5cc80')``. No score or
    grade threshold is inferred from text, and strings are never parsed as tags.
    """

    text: str
    color: str | None = None
    emphasis: Literal["normal", "strong", "secondary", "emphasis", "gold"] = "normal"


@dataclass(frozen=True)
class Inline:
    spans: tuple[Span, ...]


@dataclass(frozen=True)
class TaggedText:
    """Explicit opt-in to Core's bounded inline tag grammar.

    Ordinary strings remain literal. Prefer Inline/Span for external text;
    when composing source, escape backslashes and square brackets with a
    preceding backslash. Core parses tags; the SDK performs no parsing.
    """

    source: str


Content: TypeAlias = str | Inline | TaggedText


@dataclass(frozen=True)
class ThemePalette:
    outside: str
    surface: str
    panel: str
    panel_alt: str
    border: str
    text: str
    secondary: str
    title: str
    accent: str


@dataclass(frozen=True)
class ThemeOption:
    """Immutable Core palette choice; IDs are stable, labels are display text."""

    id: str
    label: str
    palette: ThemePalette


@dataclass(frozen=True)
class Text:
    content: Content
    style: Literal["title", "heading", "body", "caption", "stat"] = "body"
    align: Align = "left"
    wrap: Literal["wrap", "nowrap"] = "wrap"


@dataclass(frozen=True)
class Section:
    title: Content
    blocks: tuple[Block, ...] = ()
    subtitle: Content = ""
    panel: bool = True


@dataclass(frozen=True)
class Row:
    cells: tuple[Block, ...]
    weights: tuple[int | float, ...] = ()
    valign: VAlign = "center"


@dataclass(frozen=True)
class Property:
    label: Content
    value: Content


@dataclass(frozen=True)
class PropertyGrid:
    properties: tuple[Property, ...]
    columns: int = 2


@dataclass(frozen=True)
class Column:
    title: Content
    weight: int | float = 1
    align: Align = "left"
    valign: VAlign = "center"
    wrap: Literal["wrap", "nowrap"] = "wrap"


@dataclass(frozen=True)
class Cell:
    """Optional per-cell alignment; table columns supply the usual defaults."""

    content: Content
    align: Align = "left"
    valign: VAlign = "top"


@dataclass(frozen=True)
class Table:
    columns: tuple[Column, ...]
    rows: tuple[tuple[Content | Cell, ...], ...]


@dataclass(frozen=True)
class Image:
    asset: Asset
    width: int | None = None
    height: int = 160
    align: Align = "left"
    caption: Content = ""
    fit: Literal["contain", "cover"] = "contain"


Block: TypeAlias = (
    Text | Section | Row | PropertyGrid | Table | Image | WeightedCollage | DenseCollage
)


@dataclass(frozen=True)
class Document:
    title: Content
    blocks: tuple[Block, ...] = ()
    subtitle: Content = ""
    brand: Content = "游戏连结"
    logo: Asset | None = None
    theme: Literal["warm_paper", "cool_white", "deep_indigo"] | ThemePalette = (
        "warm_paper"
    )
    layout: Literal["directory", "detail", "table"] = "detail"
    sources: tuple[Content, ...] = ()
    powered_by: Content = "Powered by yomihime/astrbot_plugin_yomihime_game_link"
    width: int = 1040


__all__ = [
    "Align",
    "VAlign",
    "Asset",
    "ResourceImage",
    "CollageItem",
    "WeightedCollage",
    "DenseCollageItem",
    "DenseCollage",
    "Span",
    "Inline",
    "Content",
    "TaggedText",
    "ThemePalette",
    "ThemeOption",
    "Text",
    "Section",
    "Row",
    "Property",
    "PropertyGrid",
    "Column",
    "Cell",
    "Table",
    "Image",
    "Block",
    "Document",
]
