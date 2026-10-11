"""Immutable, bounded market values. No seller/retainer identity is retained."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from yomihime_game_link_sdk.results import CapabilityResult, ErrorCode

from ..query_resolution import MarketQuery
from .market_sources import CatalogSnapshot, Provenance


@dataclass(frozen=True, slots=True)
class Quote:
    quality: str
    minimum: int | float | None = None
    minimum_world: int | None = None
    minimum_uploaded_at: datetime | None = None
    recent_purchase: int | float | None = None
    recent_world: int | None = None
    recent_at: datetime | None = None
    average_sale_price: int | float | None = None
    daily_sale_velocity: int | float | None = None

    @property
    def has_data(self) -> bool:
        return any(
            value is not None
            for value in (
                self.minimum,
                self.recent_purchase,
                self.average_sale_price,
                self.daily_sale_velocity,
            )
        )

    @property
    def incomplete(self) -> bool:
        return any(
            value is None
            for value in (
                self.minimum,
                self.recent_purchase,
                self.average_sale_price,
                self.daily_sale_velocity,
            )
        )


@dataclass(frozen=True, slots=True)
class Listing:
    price_per_unit: int
    quantity: int
    hq: bool
    world_id: int | None
    region: str
    reviewed_at: datetime | None
    uploaded_at: datetime | None
    ordinal: int


@dataclass(frozen=True, slots=True)
class ScopeData:
    quotes: tuple[Quote, ...] = ()
    listings: tuple[Listing, ...] = ()
    uploaded_at: datetime | None = None
    has_data_marker: bool | None = None
    truncated: bool = False
    incomplete: bool = False

    @property
    def has_data(self) -> bool:
        return bool(self.listings) or any(quote.has_data for quote in self.quotes)


@dataclass(frozen=True, slots=True)
class ScopeOutcome:
    region: str
    target: str
    data: ScopeData | None = None
    provenance: Provenance | None = None
    code: ErrorCode | None = None
    stage: str = "market"
    reason: str | None = None
    status_code: int | None = None

    @property
    def failed(self) -> bool:
        return self.code is not None


@dataclass(frozen=True, slots=True)
class MarketExecution:
    query: MarketQuery
    outcomes: tuple[ScopeOutcome, ...]
    listings: tuple[Listing, ...]
    minimums: tuple[tuple[str, str, Quote], ...]
    truncated: bool
    result: CapabilityResult
    observed_at: datetime
    catalog: CatalogSnapshot | None = None
