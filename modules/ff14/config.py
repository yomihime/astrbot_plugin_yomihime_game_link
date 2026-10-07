"""Validated ordinary FF14 defaults, independent of the host and secret fields."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from yomihime_sdk.api.manifests import ConfigField

REGION = "ff14_default_region"
DAYS = "ff14_calendar_default_days"
TIMEZONE = "ff14_calendar_default_timezone"
DELIVERY_TIME = "ff14_calendar_default_delivery_time"
ORDINARY_FIELDS = frozenset((REGION, DAYS, TIMEZONE, DELIVERY_TIME))

# The manifest owns these defaults; Host legacy JSON is migration input only.
CALENDAR_CONFIG_FIELDS = (
    ConfigField(
        DAYS,
        description="日历默认天数",
        default=7,
        value_schema={"type": "integer", "minimum": 1, "maximum": 30},
        group="calendar",
    ),
    ConfigField(
        TIMEZONE,
        description="日历默认时区",
        default="Asia/Shanghai",
        group="calendar",
        value_schema={"type": "string", "minLength": 1, "maxLength": 128},
    ),
    ConfigField(
        DELIVERY_TIME,
        description="日历默认投递时间",
        default="08:00",
        group="calendar",
        value_schema={"type": "string", "minLength": 5, "maxLength": 5},
    ),
)


def _validate_timezone(value: object) -> None:
    FF14ConfigSnapshot(calendar_default_timezone=value)


def _validate_delivery_time(value: object) -> None:
    FF14ConfigSnapshot(calendar_default_delivery_time=value)


CALENDAR_VALUE_VALIDATORS = MappingProxyType(
    {
        TIMEZONE: _validate_timezone,
        DELIVERY_TIME: _validate_delivery_time,
    }
)


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
            values["core_defaults"]["default_region"]
            if "core_defaults" in values
            else values.get(REGION, "cn"),
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


def legacy_core_defaults(config: Mapping[str, object] | None):
    """Presence-preserving legacy region projection, with no migration writes."""
    if config is None:
        return MappingProxyType({})
    if not isinstance(config, Mapping):
        raise FF14ConfigError("ordinary_config")
    return MappingProxyType(
        {"default_region": config[REGION]} if REGION in config else {}
    )


def ff14_config_snapshot(config: Mapping[str, object] | None) -> FF14ConfigSnapshot:
    """Accept missing legacy fields; reject invalid or undeclared host inputs."""
    if config is None:
        config = {}
    if isinstance(config, Mapping):
        config = {
            key: value for key, value in config.items() if key != "web_public_origin"
        }
    if not isinstance(config, Mapping) or set(config) - ORDINARY_FIELDS:
        raise FF14ConfigError("ordinary_config")
    return FF14ConfigSnapshot.from_values(config)


__all__ = ["FF14ConfigError", "FF14ConfigSnapshot", "ORDINARY_FIELDS"]
