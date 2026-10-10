"""Versioned, renderer-neutral display data contracts."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Mapping

from yomihime_game_link_sdk.display import CommandsBlock as CommandsBlock
from yomihime_game_link_sdk.display import DigestMember as DigestMember
from yomihime_game_link_sdk.display import DisplayAudience as DisplayAudience
from yomihime_game_link_sdk.display import DisplayBatch as DisplayBatch
from yomihime_game_link_sdk.display import DisplayBatchMember as DisplayBatchMember
from yomihime_game_link_sdk.display import DisplayDocument as DisplayDocument
from yomihime_game_link_sdk.display import DisplayLimits as DisplayLimits
from yomihime_game_link_sdk.display import DisplayOutput as DisplayOutput
from yomihime_game_link_sdk.display import DisplayRenderer as DisplayRenderer
from yomihime_game_link_sdk.display import FieldsBlock as FieldsBlock
from yomihime_game_link_sdk.display import GridItem as GridItem
from yomihime_game_link_sdk.display import ImageBlock as ImageBlock
from yomihime_game_link_sdk.display import ItemGridBlock as ItemGridBlock
from yomihime_game_link_sdk.display import Link as Link
from yomihime_game_link_sdk.display import LinksBlock as LinksBlock
from yomihime_game_link_sdk.display import MetricsBlock as MetricsBlock
from yomihime_game_link_sdk.display import MoneyValue as MoneyValue
from yomihime_game_link_sdk.display import NumberValue as NumberValue
from yomihime_game_link_sdk.display import Privacy as Privacy
from yomihime_game_link_sdk.display import SeriesBlock as SeriesBlock
from yomihime_game_link_sdk.display import TableBlock as TableBlock
from yomihime_game_link_sdk.display import TextBlock as TextBlock
from yomihime_game_link_sdk.display import TimeValue as TimeValue
from yomihime_game_link_sdk.display import UnknownBlock as UnknownBlock

from ...core.contracts.validation_boundary import validate_contract
from .version import CONTRACT_VERSION as CONTRACT_VERSION


def _frozen(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({k: _frozen(v) for k, v in value.items()})
    if isinstance(value, (list, set, frozenset)):
        return tuple((_frozen(v) for v in value))
    if isinstance(value, tuple):
        return tuple((_frozen(v) for v in value))
    return value


def _text(value: str, name: str = "text") -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be text")
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value


def check_DigestMember(self) -> None:
    _text(self.subscription_id, "subscription_id")
    _text(self.event_key, "event_key")
    for name in ("subscription_revision", "event_version"):
        value = getattr(self, name)
        if type(value) is not int or value < 1:
            raise ValueError(f"{name} must be a built-in positive integer")


def _fallback(value: str | None) -> None:
    if value is not None:
        _text(value, "fallback_text")


def _block_options(required: bool, fallback: str | None) -> None:
    if not isinstance(required, bool):
        raise TypeError("required must be a boolean")
    _fallback(fallback)


def _asset(value: str) -> str:
    _text(value, "asset_id")
    if "/" in value or "\\" in value or value in {".", ".."} or (".." in value):
        raise ValueError("asset_id must be a resource identifier")
    if not re.fullmatch("[A-Za-z0-9][A-Za-z0-9_.:-]*", value):
        raise ValueError("asset_id must be a resource identifier")
    return value


def _valid_value(value: Any) -> Any:
    if isinstance(value, Decimal) and (not value.is_finite()):
        raise ValueError("display numbers must be finite")
    if value is None or isinstance(
        value, (str, int, bool, Decimal, NumberValue, MoneyValue, TimeValue)
    ):
        return value
    if isinstance(value, Mapping):
        if any((not isinstance(k, str) for k in value)):
            raise TypeError("mapping keys must be text")
        return MappingProxyType({k: _valid_value(v) for k, v in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple((_valid_value(v) for v in value))
    raise TypeError("display values must be scalar or typed values")


def check_NumberValue(self) -> None:
    if not isinstance(self.value, (Decimal, int, str)) or isinstance(self.value, bool):
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


def check_MoneyValue(self) -> None:
    validate_contract(NumberValue(self.value, precision=self.precision))
    if not re.fullmatch("[A-Z]{3}", self.currency):
        raise ValueError("currency must be an ISO-style three-letter code")


def check_TimeValue(self) -> None:
    if self.timezone_name is not None:
        _text(self.timezone_name, "timezone_name")
    if isinstance(self.value, datetime):
        if self.value.tzinfo is None or self.value.utcoffset() is None:
            raise ValueError("datetime must include an explicit timezone")
    elif not isinstance(self.value, date):
        raise TypeError("value must be a date or timezone-aware datetime")
    if (
        isinstance(self.value, date)
        and (not isinstance(self.value, datetime))
        and (not self.timezone_name)
    ):
        raise ValueError("date must include timezone_name")


def check_TextBlock(self) -> None:
    _text(self.text)
    _block_options(self.required, self.fallback_text)


def check_FieldsBlock(self) -> None:
    if not isinstance(self.fields, Mapping) or not self.fields:
        raise ValueError("fields must be a non-empty mapping")
    _block_options(self.required, self.fallback_text)
    object.__setattr__(self, "fields", _valid_value(self.fields))


def check_MetricsBlock(self) -> None:
    if not isinstance(self.metrics, Mapping) or not self.metrics:
        raise ValueError("metrics must be a non-empty mapping")
    _block_options(self.required, self.fallback_text)
    if any(
        (not isinstance(v, (NumberValue, MoneyValue)) for v in self.metrics.values())
    ):
        raise TypeError("metrics require NumberValue or MoneyValue")
    object.__setattr__(self, "metrics", _valid_value(self.metrics))


def check_TableBlock(self) -> None:
    cols = tuple((_text(x, "column") for x in self.columns))
    _block_options(self.required, self.fallback_text)
    rows = tuple((tuple((_valid_value(x) for x in row)) for row in self.rows))
    if not cols or any((len(row) != len(cols) for row in rows)):
        raise ValueError("table rows must match columns")
    object.__setattr__(self, "columns", cols)
    object.__setattr__(self, "rows", rows)


def check_ItemGridBlock(self) -> None:
    _block_options(self.required, self.fallback_text)
    if any((not isinstance(item, GridItem) for item in self.items)):
        raise TypeError("item_grid requires GridItem instances")
    object.__setattr__(self, "items", tuple(self.items))
    if self.required and (not self.items) and (self.fallback_text is None):
        raise ValueError("required item_grid needs items or fallback_text")


def check_GridItem(self) -> None:
    _text(self.label, "label")
    object.__setattr__(self, "value", _valid_value(self.value))
    if self.asset_id is not None:
        _asset(self.asset_id)
    if isinstance(self.visibility, str):
        object.__setattr__(
            self, "visibility", validate_contract(Privacy(self.visibility))
        )
    elif not isinstance(self.visibility, Privacy):
        raise TypeError("visibility must be Privacy")


def check_ImageBlock(self) -> None:
    _asset(self.asset_id)
    _text(self.alt_text, "alt_text")
    _block_options(self.required, self.fallback_text)
    if isinstance(self.visibility, str):
        object.__setattr__(
            self, "visibility", validate_contract(Privacy(self.visibility))
        )
    elif not isinstance(self.visibility, Privacy):
        raise TypeError("visibility must be Privacy")


def check_SeriesBlock(self) -> None:
    _block_options(self.required, self.fallback_text)
    object.__setattr__(self, "points", tuple((tuple(point) for point in self.points)))
    if any(
        (
            len(p) != 2
            or not isinstance(p[0], TimeValue)
            or (not isinstance(p[1], (NumberValue, MoneyValue)))
            for p in self.points
        )
    ):
        raise ValueError("series points require time and value")


def check_LinksBlock(self) -> None:
    _block_options(self.required, self.fallback_text)
    normalized = []
    for link in self.links:
        if not isinstance(link, Link):
            raise TypeError("links requires Link instances")
        normalized.append(link)
    object.__setattr__(self, "links", tuple(normalized))


def check_Link(self) -> None:
    _text(self.label, "label")
    if not isinstance(self.url, str) or not (
        self.url.startswith("https://") or self.url.startswith("http://")
    ):
        raise ValueError("link url must use http or https")


def check_CommandsBlock(self) -> None:
    _block_options(self.required, self.fallback_text)
    object.__setattr__(
        self, "commands", tuple((_text(x, "command") for x in self.commands))
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


def check_UnknownBlock(self) -> None:
    _block_options(self.required, self.fallback_text)
    _text(self.kind, "kind")
    if self.fallback_text is None and (not self.required):
        raise ValueError("unknown optional block requires fallback_text")
    if self.fallback_text is not None:
        _text(self.fallback_text, "fallback_text")
    if self.required:
        raise ValueError("unknown required block is unsupported")


def check_DisplayDocument(self) -> None:
    _text(self.title, "title")
    _text(self.subject, "subject")
    if self.schema_version != "1.8.0":
        raise ValueError("unsupported display contract version")
    object.__setattr__(self, "ordered_blocks", tuple(self.ordered_blocks))
    object.__setattr__(
        self, "sources", tuple((_text(x, "source") for x in self.sources))
    )
    object.__setattr__(self, "timestamps", tuple(self.timestamps))
    if isinstance(self.privacy, str):
        object.__setattr__(self, "privacy", validate_contract(Privacy(self.privacy)))
    elif not isinstance(self.privacy, Privacy):
        raise TypeError("privacy must be Privacy")
    if any((not isinstance(x, (TimeValue,)) for x in self.timestamps)):
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
    if any((not isinstance(x, valid_types) for x in self.ordered_blocks)):
        raise TypeError("ordered_blocks contains unsupported block")
    if any(
        (
            isinstance(x, ImageBlock)
            and self.privacy is Privacy.PUBLIC
            and (x.visibility is Privacy.PRIVATE)
            for x in self.ordered_blocks
        )
    ):
        raise ValueError("image visibility cannot exceed document privacy")
    if any(
        (
            isinstance(x, ItemGridBlock)
            and any((item.visibility is Privacy.PRIVATE for item in x.items))
            and (self.privacy is Privacy.PUBLIC)
            for x in self.ordered_blocks
        )
    ):
        raise ValueError("private grid assets require a private document")


def check_DisplayOutput(self) -> None:
    _text(self.text, "rendered text")
    resources = tuple(self.resource_ids)
    for resource_id in resources:
        _asset(resource_id)
    if len(set(resources)) != len(resources):
        raise ValueError("resource_ids must be unique")
    object.__setattr__(self, "resource_ids", resources)


def check_DisplayLimits(self) -> None:
    for name in ("max_pages", "max_image_bytes"):
        value = getattr(self, name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer")


def check_DisplayBatchMember(self) -> None:
    if not isinstance(self.member, DigestMember):
        raise TypeError("batch member requires a DigestMember")
    if not isinstance(self.document, DisplayDocument):
        raise TypeError("batch document requires a DisplayDocument")


def check_DisplayBatch(self) -> None:
    members = tuple(self.members)
    if not members:
        raise ValueError("display batch requires at least one member")
    if any((not isinstance(item, DisplayBatchMember) for item in members)):
        raise TypeError("display batch members must be DisplayBatchMember values")
    if len({item.member for item in members}) != len(members):
        raise ValueError("display batch members must be unique")
    if isinstance(self.audience, str):
        object.__setattr__(
            self, "audience", validate_contract(DisplayAudience(self.audience))
        )
    elif not isinstance(self.audience, DisplayAudience):
        raise TypeError("audience must be a DisplayAudience")
    if self.audience is DisplayAudience.PUBLIC and any(
        (item.document.privacy is Privacy.PRIVATE for item in members)
    ):
        raise ValueError("private documents cannot be promoted to public audience")
    object.__setattr__(self, "members", members)
