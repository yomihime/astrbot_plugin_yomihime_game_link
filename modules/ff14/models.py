"""Small FF14 item values normalized from the declared public sources."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AcquisitionKind(str, Enum):
    NODE = "node"
    FISHING = "fishing"
    CRAFT = "craft"
    VENDOR = "vendor"
    CURRENCY_TRADE = "currency_trade"
    SHOP_TRADE = "shop_trade"
    DROP = "drop"
    INSTANCE = "instance"
    QUEST = "quest"


@dataclass(frozen=True, slots=True)
class ItemCandidate:
    item_id: int
    name: str

    def __post_init__(self) -> None:
        if type(self.item_id) is not int or self.item_id < 1:
            raise ValueError("item_id must be a positive integer")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("candidate name must be non-empty text")
        object.__setattr__(self, "name", self.name.strip())


@dataclass(frozen=True, slots=True)
class ItemSearchPage:
    candidates: tuple[ItemCandidate, ...]
    next_cursor: str | None = None
    schema: str | None = None
    version: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.candidates, tuple) or any(
            not isinstance(item, ItemCandidate) for item in self.candidates
        ):
            raise TypeError("candidates must be a tuple of ItemCandidate values")
        ids = tuple(item.item_id for item in self.candidates)
        if len(ids) != len(set(ids)):
            raise ValueError("candidate item IDs must be unique within a page")
        if self.next_cursor is not None and (
            not isinstance(self.next_cursor, str)
            or not self.next_cursor
            or len(self.next_cursor) > 256
            or self.next_cursor.startswith(("http://", "https://", "//"))
            or any(
                ord(char) < 32 or 0x7F <= ord(char) <= 0x9F for char in self.next_cursor
            )
        ):
            raise ValueError("next_cursor must be a bounded opaque token")
        for name in ("schema", "version"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or len(value) > 128):
                raise ValueError(f"{name} must be bounded text when present")


@dataclass(frozen=True, slots=True)
class ItemSearchResult:
    candidates: tuple[ItemCandidate, ...]
    pages_read: int
    truncated: bool

    def __post_init__(self) -> None:
        if not isinstance(self.candidates, tuple) or any(
            not isinstance(item, ItemCandidate) for item in self.candidates
        ):
            raise TypeError("candidates must be a tuple of ItemCandidate values")
        ids = tuple(item.item_id for item in self.candidates)
        if len(ids) != len(set(ids)):
            raise ValueError("search result item IDs must be unique")
        if type(self.pages_read) is not int or self.pages_read < 0:
            raise ValueError("pages_read must be a non-negative integer")
        if type(self.truncated) is not bool:
            raise TypeError("truncated must be bool")


@dataclass(frozen=True, slots=True)
class ItemAmount:
    item_id: int
    amount: int
    name: str | None = None

    def __post_init__(self) -> None:
        if type(self.item_id) is not int or self.item_id < 1:
            raise ValueError("cost item_id must be a positive integer")
        if type(self.amount) is not int or self.amount < 1:
            raise ValueError("cost amount must be a positive integer")
        if self.name is not None and (
            not isinstance(self.name, str) or not self.name.strip()
        ):
            raise ValueError("cost name must be non-empty text when present")
        if self.name is not None:
            object.__setattr__(self, "name", self.name.strip())


@dataclass(frozen=True, slots=True)
class AcquisitionRoute:
    kind: AcquisitionKind
    label: str
    details: tuple[str, ...]
    costs: tuple[ItemAmount, ...] = ()
    partial: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.kind, AcquisitionKind):
            raise TypeError("kind must be an AcquisitionKind")
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("route label must be non-empty text")
        if not isinstance(self.details, tuple) or any(
            not isinstance(value, str) or not value.strip() for value in self.details
        ):
            raise ValueError("route details must be non-empty text values")
        if not isinstance(self.costs, tuple) or any(
            not isinstance(value, ItemAmount) for value in self.costs
        ):
            raise TypeError("route costs must be a tuple of ItemAmount values")
        if type(self.partial) is not bool:
            raise TypeError("partial must be bool")
        object.__setattr__(self, "label", self.label.strip())
        object.__setattr__(
            self, "details", tuple(value.strip() for value in self.details)
        )


@dataclass(frozen=True, slots=True)
class ItemRecord:
    item_id: int
    name: str
    description: str | None = None
    item_level: int | None = None
    equip_level: int | None = None
    routes: tuple[AcquisitionRoute, ...] = ()
    source_urls: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.item_id) is not int or self.item_id < 1:
            raise ValueError("item_id must be a positive integer")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("item name must be non-empty text")
        for field_name in ("item_level", "equip_level"):
            value = getattr(self, field_name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{field_name} must be a non-negative integer")
        if self.description is not None and not isinstance(self.description, str):
            raise TypeError("description must be text when present")
        if not isinstance(self.routes, tuple) or any(
            not isinstance(value, AcquisitionRoute) for value in self.routes
        ):
            raise TypeError("routes must be a tuple of AcquisitionRoute values")
        if not isinstance(self.source_urls, tuple) or any(
            not isinstance(value, str) or not value.startswith("https://")
            for value in self.source_urls
        ):
            raise ValueError("source_urls must contain HTTPS links")
        if not isinstance(self.warnings, tuple) or any(
            not isinstance(value, str) or not value.strip() for value in self.warnings
        ):
            raise ValueError("warnings must be non-empty text values")
        object.__setattr__(self, "name", self.name.strip())
        if self.description is not None:
            object.__setattr__(self, "description", self.description.strip() or None)


__all__ = [
    "AcquisitionKind",
    "AcquisitionRoute",
    "ItemAmount",
    "ItemCandidate",
    "ItemRecord",
    "ItemSearchPage",
    "ItemSearchResult",
]
