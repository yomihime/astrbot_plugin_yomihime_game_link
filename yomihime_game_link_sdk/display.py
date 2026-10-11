from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Any, Protocol


class Privacy(str, Enum):
    PUBLIC = "public"
    PRIVATE = "private"


@dataclass(frozen=True, slots=True)
class DigestMember:
    """Stable presentation association to one event and subscription revision."""

    subscription_id: str
    subscription_revision: int
    event_key: str
    event_version: int


@dataclass(frozen=True)
class NumberValue:
    value: Decimal | int | str
    unit: str = ""
    precision: int | None = None


@dataclass(frozen=True)
class MoneyValue:
    value: Decimal | int | str
    currency: str
    precision: int | None = None


@dataclass(frozen=True)
class TimeValue:
    value: datetime | date
    timezone_name: str | None = None


@dataclass(frozen=True)
class TextBlock:
    text: str
    fallback_text: str | None = None
    required: bool = True
    kind: str = field(default="text", init=False)


@dataclass(frozen=True)
class FieldsBlock:
    fields: Mapping[str, Any]
    fallback_text: str | None = None
    required: bool = True
    kind: str = field(default="fields", init=False)

    def __post_init__(self) -> None:
        if isinstance(self.fields, Mapping):
            object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))


@dataclass(frozen=True)
class MetricsBlock:
    metrics: Mapping[str, NumberValue | MoneyValue | Any]
    fallback_text: str | None = None
    required: bool = True
    kind: str = field(default="metrics", init=False)

    def __post_init__(self) -> None:
        if isinstance(self.metrics, Mapping):
            object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))


@dataclass(frozen=True)
class TableBlock:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    fallback_text: str | None = None
    required: bool = True
    kind: str = field(default="table", init=False)

    def __post_init__(self) -> None:
        if self.columns is not None:
            object.__setattr__(self, "columns", tuple(self.columns))
        if self.rows is not None:
            object.__setattr__(self, "rows", tuple(self.rows))


@dataclass(frozen=True)
class ItemGridBlock:
    items: tuple[GridItem, ...]
    fallback_text: str | None = None
    required: bool = True
    kind: str = field(default="item_grid", init=False)

    def __post_init__(self) -> None:
        if self.items is not None:
            object.__setattr__(self, "items", tuple(self.items))


@dataclass(frozen=True)
class GridItem:
    label: str
    value: Any = None
    asset_id: str | None = None
    visibility: Privacy = Privacy.PUBLIC


@dataclass(frozen=True)
class ImageBlock:
    asset_id: str
    alt_text: str
    visibility: Privacy = Privacy.PUBLIC
    fallback_text: str | None = None
    required: bool = False
    kind: str = field(default="image", init=False)


@dataclass(frozen=True)
class SeriesBlock:
    points: tuple[tuple[TimeValue, NumberValue | MoneyValue], ...]
    fallback_text: str | None = None
    required: bool = False
    kind: str = field(default="series", init=False)

    def __post_init__(self) -> None:
        if self.points is not None:
            object.__setattr__(self, "points", tuple(self.points))


@dataclass(frozen=True)
class LinksBlock:
    links: tuple[Link, ...]
    fallback_text: str | None = None
    required: bool = False
    kind: str = field(default="links", init=False)

    def __post_init__(self) -> None:
        if self.links is not None:
            object.__setattr__(self, "links", tuple(self.links))


@dataclass(frozen=True)
class Link:
    label: str
    url: str


@dataclass(frozen=True)
class CommandsBlock:
    commands: tuple[str, ...]
    fallback_text: str | None = None
    required: bool = False
    kind: str = field(default="commands", init=False)

    def __post_init__(self) -> None:
        if self.commands is not None:
            object.__setattr__(self, "commands", tuple(self.commands))


DisplayBlock = (
    TextBlock
    | FieldsBlock
    | MetricsBlock
    | TableBlock
    | ItemGridBlock
    | ImageBlock
    | SeriesBlock
    | LinksBlock
    | CommandsBlock
)


@dataclass(frozen=True)
class UnknownBlock:
    kind: str
    required: bool = False
    fallback_text: str | None = None


@dataclass(frozen=True)
class DisplayDocument:
    title: str
    subject: str
    ordered_blocks: tuple[DisplayBlock | UnknownBlock, ...]
    sources: tuple[str, ...] = ()
    timestamps: tuple[TimeValue, ...] = ()
    privacy: Privacy = Privacy.PUBLIC
    schema_version: str = "1.8.0"

    def __post_init__(self) -> None:
        if self.ordered_blocks is not None:
            object.__setattr__(self, "ordered_blocks", tuple(self.ordered_blocks))
        if self.sources is not None:
            object.__setattr__(self, "sources", tuple(self.sources))
        if self.timestamps is not None:
            object.__setattr__(self, "timestamps", tuple(self.timestamps))


@dataclass(frozen=True)
class DisplayOutput:
    """Renderer-neutral text and resource references."""

    text: str
    resource_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.resource_ids is not None:
            object.__setattr__(self, "resource_ids", tuple(self.resource_ids))


@dataclass(frozen=True)
class DisplayLimits:
    max_pages: int
    max_image_bytes: int


class DisplayAudience(str, Enum):
    """Presentation visibility class; this value does not grant authorization."""

    PUBLIC = "public"
    PRIVATE = "private"


@dataclass(frozen=True)
class DisplayBatchMember:
    member: DigestMember
    document: DisplayDocument


@dataclass(frozen=True)
class DisplayBatch:
    members: tuple[DisplayBatchMember, ...]
    audience: DisplayAudience

    def __post_init__(self) -> None:
        if self.members is not None:
            object.__setattr__(self, "members", tuple(self.members))


class DisplayRenderer(Protocol):
    """Generic renderer; audience is presentation-only, never an auth proof.

    For PUBLIC, implementations must not emit private content. PRIVATE is only
    supplied after an upstream trusted service has filtered the audience; this
    renderer does not validate that service or authorize resource access.
    """

    async def render(
        self,
        document: DisplayDocument,
        *,
        limits: DisplayLimits,
        audience: DisplayAudience = DisplayAudience.PUBLIC,
    ) -> DisplayOutput: ...

    async def render_batch(
        self, batch: DisplayBatch, limits: DisplayLimits
    ) -> DisplayOutput: ...
