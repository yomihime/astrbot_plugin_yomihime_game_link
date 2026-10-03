"""Validated ordinary FF14 defaults, independent of the host and secret fields."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

REGION = "ff14_default_region"
DAYS = "ff14_calendar_default_days"
TIMEZONE = "ff14_calendar_default_timezone"
DELIVERY_TIME = "ff14_calendar_default_delivery_time"
ORDINARY_FIELDS = frozenset((REGION, DAYS, TIMEZONE, DELIVERY_TIME))


class FF14ConfigError(ValueError):
    """A bounded field identifier; never includes the rejected input."""

    def __init__(self, field: str) -> None:
        self.field = field if field in ORDINARY_FIELDS else "ordinary_config"
        super().__init__(self.field)


@dataclass(frozen=True, slots=True)
class FF14ConfigSnapshot:
    default_region: str = "cn"
    calendar_default_days: int = 7
    calendar_default_timezone: str = "Asia/Shanghai"
    calendar_default_delivery_time: str = "08:00"

    def __post_init__(self) -> None:
        if type(self.default_region) is not str or self.default_region not in (
            "cn",
            "global",
        ):
            raise FF14ConfigError(REGION)
        if type(self.calendar_default_days) is not int or not (
            1 <= self.calendar_default_days <= 30
        ):
            raise FF14ConfigError(DAYS)
        zone = self.calendar_default_timezone
        if (
            type(zone) is not str
            or not zone
            or len(zone) > 128
            or any(ord(character) < 32 or ord(character) == 127 for character in zone)
        ):
            raise FF14ConfigError(TIMEZONE)
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError, TypeError, OSError):
            raise FF14ConfigError(TIMEZONE) from None
        delivery_time = self.calendar_default_delivery_time
        if (
            type(delivery_time) is not str
            or re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", delivery_time) is None
        ):
            raise FF14ConfigError(DELIVERY_TIME)

    @classmethod
    def from_values(cls, values: Mapping[str, object]) -> FF14ConfigSnapshot:
        """Read only ordinary fields from the combined SDK config view."""
        if not isinstance(values, Mapping):
            raise FF14ConfigError("ordinary_config")
        return cls(
            values.get(REGION, "cn"),
            values.get(DAYS, 7),
            values.get(TIMEZONE, "Asia/Shanghai"),
            values.get(DELIVERY_TIME, "08:00"),
        )

    def as_values(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                REGION: self.default_region,
                DAYS: self.calendar_default_days,
                TIMEZONE: self.calendar_default_timezone,
                DELIVERY_TIME: self.calendar_default_delivery_time,
            }
        )


__all__ = ["FF14ConfigError", "FF14ConfigSnapshot", "ORDINARY_FIELDS"]
