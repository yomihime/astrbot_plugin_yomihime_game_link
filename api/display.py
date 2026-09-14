"""Versioned, renderer-neutral display data contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from .version import CONTRACT_VERSION


class Privacy(str, Enum):
    PUBLIC = "public"
    PRIVATE = "private"


def _frozen(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({k: _frozen(v) for k, v in value.items()})
    if isinstance(value, (list, set, frozenset)):
        return tuple(_frozen(v) for v in value)
    if isinstance(value, tuple):
        return tuple(_frozen(v) for v in value)
    return value


def _text(value: str, name: str = "text") -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be text")
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value


def _fallback(value: str | None) -> None:
    if value is not None:
        _text(value, "fallback_text")


def _block_options(required: bool, fallback: str | None) -> None:
    if not isinstance(required, bool):
        raise TypeError("required must be a boolean")
    _fallback(fallback)


def _asset(value: str) -> str:
    _text(value, "asset_id")
    if "/" in value or "\\" in value or value in {".", ".."} or ".." in value:
        raise ValueError("asset_id must be a resource identifier")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", value):
        raise ValueError("asset_id must be a resource identifier")
    return value


def _valid_value(value: Any) -> Any:
    if isinstance(value, Decimal) and not value.is_finite():
        raise ValueError("display numbers must be finite")
    if value is None or isinstance(
        value, (str, int, bool, Decimal, NumberValue, MoneyValue, TimeValue)
    ):
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(k, str) for k in value):
            raise TypeError("mapping keys must be text")
        return MappingProxyType({k: _valid_value(v) for k, v in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_valid_value(v) for v in value)
    raise TypeError("display values must be scalar or typed values")


@dataclass(frozen=True)
class NumberValue:
    value: Decimal | int | str
    unit: str = ""
    precision: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.value, (Decimal, int, str)) or isinstance(
            self.value, bool
        ):
            raise TypeError("number value cannot be bool")
        try:
            number = Decimal(str(self.value))
        except Exception as exc:
            raise TypeError("value must be an exact numeric value") from exc
        if not number.is_finite():
            raise ValueError("numeric value must be finite")
        if not isinstance(self.unit, str):
            raise TypeError("unit must be text")
        if self.precision is not None and (
            not isinstance(self.precision, int)
            or isinstance(self.precision, bool)
            or self.precision < 0
        ):
            raise ValueError("precision must be a non-negative integer")
        object.__setattr__(self, "unit", self.unit or "")


@dataclass(frozen=True)
class MoneyValue:
    value: Decimal | int | str
    currency: str
    precision: int | None = None

    def __post_init__(self) -> None:
        NumberValue(self.value, precision=self.precision)
        if not re.fullmatch(r"[A-Z]{3}", self.currency):
            raise ValueError("currency must be an ISO-style three-letter code")


@dataclass(frozen=True)
class TimeValue:
    value: datetime | date
    timezone_name: str | None = None

    def __post_init__(self) -> None:
        if self.timezone_name is not None:
            _text(self.timezone_name, "timezone_name")
        if isinstance(self.value, datetime):
            if self.value.tzinfo is None or self.value.utcoffset() is None:
                raise ValueError("datetime must include an explicit timezone")
        elif not isinstance(self.value, date):
            raise TypeError("value must be a date or timezone-aware datetime")
        if (
            isinstance(self.value, date)
            and not isinstance(self.value, datetime)
            and not self.timezone_name
        ):
            raise ValueError("date must include timezone_name")


@dataclass(frozen=True)
class TextBlock:
    text: str
    fallback_text: str | None = None
    required: bool = True
    kind: str = field(default="text", init=False)

    def __post_init__(self) -> None:
        _text(self.text)
        _block_options(self.required, self.fallback_text)


@dataclass(frozen=True)
class FieldsBlock:
    fields: Mapping[str, Any]
    fallback_text: str | None = None
    required: bool = True
    kind: str = field(default="fields", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.fields, Mapping) or not self.fields:
            raise ValueError("fields must be a non-empty mapping")
        _block_options(self.required, self.fallback_text)
        object.__setattr__(self, "fields", _valid_value(self.fields))


@dataclass(frozen=True)
class MetricsBlock:
    metrics: Mapping[str, NumberValue | MoneyValue | Any]
    fallback_text: str | None = None
    required: bool = True
    kind: str = field(default="metrics", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.metrics, Mapping) or not self.metrics:
            raise ValueError("metrics must be a non-empty mapping")
        _block_options(self.required, self.fallback_text)
        if any(
            not isinstance(v, (NumberValue, MoneyValue)) for v in self.metrics.values()
        ):
            raise TypeError("metrics require NumberValue or MoneyValue")
        object.__setattr__(self, "metrics", _valid_value(self.metrics))


@dataclass(frozen=True)
class TableBlock:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    fallback_text: str | None = None
    required: bool = True
    kind: str = field(default="table", init=False)

    def __post_init__(self) -> None:
        cols = tuple(_text(x, "column") for x in self.columns)
        _block_options(self.required, self.fallback_text)
        rows = tuple(tuple(_valid_value(x) for x in row) for row in self.rows)
        if not cols or any(len(row) != len(cols) for row in rows):
            raise ValueError("table rows must match columns")
        object.__setattr__(self, "columns", cols)
        object.__setattr__(self, "rows", rows)


@dataclass(frozen=True)
class ItemGridBlock:
    items: tuple[GridItem, ...]
    fallback_text: str | None = None
    required: bool = True
    kind: str = field(default="item_grid", init=False)

    def __post_init__(self) -> None:
        _block_options(self.required, self.fallback_text)
        if any(not isinstance(item, GridItem) for item in self.items):
            raise TypeError("item_grid requires GridItem instances")
        object.__setattr__(self, "items", tuple(self.items))
        if self.required and not self.items and self.fallback_text is None:
            raise ValueError("required item_grid needs items or fallback_text")


@dataclass(frozen=True)
class GridItem:
    label: str
    value: Any = None
    asset_id: str | None = None
    visibility: Privacy = Privacy.PUBLIC

    def __post_init__(self) -> None:
        _text(self.label, "label")
        object.__setattr__(self, "value", _valid_value(self.value))
        if self.asset_id is not None:
            _asset(self.asset_id)
        if isinstance(self.visibility, str):
            object.__setattr__(self, "visibility", Privacy(self.visibility))
        elif not isinstance(self.visibility, Privacy):
            raise TypeError("visibility must be Privacy")


@dataclass(frozen=True)
class ImageBlock:
    asset_id: str
    alt_text: str
    visibility: Privacy = Privacy.PUBLIC
    fallback_text: str | None = None
    required: bool = False
    kind: str = field(default="image", init=False)

    def __post_init__(self) -> None:
        _asset(self.asset_id)
        _text(self.alt_text, "alt_text")
        _block_options(self.required, self.fallback_text)
        if isinstance(self.visibility, str):
            object.__setattr__(self, "visibility", Privacy(self.visibility))
        elif not isinstance(self.visibility, Privacy):
            raise TypeError("visibility must be Privacy")


@dataclass(frozen=True)
class SeriesBlock:
    points: tuple[tuple[TimeValue, NumberValue | MoneyValue], ...]
    fallback_text: str | None = None
    required: bool = False
    kind: str = field(default="series", init=False)

    def __post_init__(self) -> None:
        _block_options(self.required, self.fallback_text)
        object.__setattr__(self, "points", tuple(tuple(point) for point in self.points))
        if any(
            len(p) != 2
            or not isinstance(p[0], TimeValue)
            or not isinstance(p[1], (NumberValue, MoneyValue))
            for p in self.points
        ):
            raise ValueError("series points require time and value")


@dataclass(frozen=True)
class LinksBlock:
    links: tuple[Link, ...]
    fallback_text: str | None = None
    required: bool = False
    kind: str = field(default="links", init=False)

    def __post_init__(self) -> None:
        _block_options(self.required, self.fallback_text)
        normalized = []
        for link in self.links:
            if not isinstance(link, Link):
                raise TypeError("links requires Link instances")
            normalized.append(link)
        object.__setattr__(self, "links", tuple(normalized))


@dataclass(frozen=True)
class Link:
    label: str
    url: str

    def __post_init__(self) -> None:
        _text(self.label, "label")
        if not isinstance(self.url, str) or not (
            self.url.startswith("https://") or self.url.startswith("http://")
        ):
            raise ValueError("link url must use http or https")


@dataclass(frozen=True)
class CommandsBlock:
    commands: tuple[str, ...]
    fallback_text: str | None = None
    required: bool = False
    kind: str = field(default="commands", init=False)

    def __post_init__(self) -> None:
        _block_options(self.required, self.fallback_text)
        object.__setattr__(
            self, "commands", tuple(_text(x, "command") for x in self.commands)
        )


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

    def __post_init__(self) -> None:
        _block_options(self.required, self.fallback_text)
        _text(self.kind, "kind")
        if self.fallback_text is None and not self.required:
            raise ValueError("unknown optional block requires fallback_text")
        if self.fallback_text is not None:
            _text(self.fallback_text, "fallback_text")
        if self.required:
            raise ValueError("unknown required block is unsupported")


@dataclass(frozen=True)
class DisplayDocument:
    title: str
    subject: str
    ordered_blocks: tuple[DisplayBlock | UnknownBlock, ...]
    sources: tuple[str, ...] = ()
    timestamps: tuple[TimeValue, ...] = ()
    privacy: Privacy = Privacy.PUBLIC
    schema_version: str = CONTRACT_VERSION

    def __post_init__(self) -> None:
        _text(self.title, "title")
        _text(self.subject, "subject")
        if self.schema_version != CONTRACT_VERSION:
            raise ValueError("unsupported display contract version")
        object.__setattr__(self, "ordered_blocks", tuple(self.ordered_blocks))
        object.__setattr__(
            self, "sources", tuple(_text(x, "source") for x in self.sources)
        )
        object.__setattr__(self, "timestamps", tuple(self.timestamps))
        if isinstance(self.privacy, str):
            object.__setattr__(self, "privacy", Privacy(self.privacy))
        elif not isinstance(self.privacy, Privacy):
            raise TypeError("privacy must be Privacy")
        if any(not isinstance(x, (TimeValue,)) for x in self.timestamps):
            raise TypeError("timestamps must be TimeValue instances")
        valid_types = (
            TextBlock,
            FieldsBlock,
            MetricsBlock,
            TableBlock,
            ItemGridBlock,
            ImageBlock,
            SeriesBlock,
            LinksBlock,
            CommandsBlock,
            UnknownBlock,
        )
        if any(not isinstance(x, valid_types) for x in self.ordered_blocks):
            raise TypeError("ordered_blocks contains unsupported block")
        if any(
            isinstance(x, ImageBlock)
            and self.privacy is Privacy.PUBLIC
            and x.visibility is Privacy.PRIVATE
            for x in self.ordered_blocks
        ):
            raise ValueError("image visibility cannot exceed document privacy")
        if any(
            isinstance(x, ItemGridBlock)
            and any(item.visibility is Privacy.PRIVATE for item in x.items)
            and self.privacy is Privacy.PUBLIC
            for x in self.ordered_blocks
        ):
            raise ValueError("private grid assets require a private document")
