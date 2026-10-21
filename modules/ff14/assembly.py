"""Reviewed pure support; JSON never selects executable callbacks."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from zoneinfo import ZoneInfo

from .config import (
    CALENDAR_CONFIG_FIELDS,
    CALENDAR_VALUE_VALIDATORS,
    ORDINARY_FIELDS,
    REGION,
    REGION_CONFIG_FIELD,
    FF14ConfigSnapshot,
)

REQUEST_LIMIT = 4096


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("request_rejected")
        result[key] = value
    return result


def query_parameters(endpoint: str, body: bytes) -> dict:
    if type(body) is not bytes or len(body) > REQUEST_LIMIT:
        raise ValueError("request_rejected")
    try:
        values = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        if endpoint == "market":
            # Only the authenticated business handler validates semantics so
            # rejected object queries also invalidate old candidate generations.
            if type(values) is not dict:
                raise ValueError
            return {"input": body.decode("utf-8")}
        expected = {
            "items": {"query"},
            "character": {"region", "server", "character"},
            "calendar": {"region", "days", "timezone"},
        }[endpoint]
        if type(values) is not dict or set(values) != expected:
            raise ValueError
        limits = {"query": 120, "server": 100, "character": 120, "timezone": 128}
        for key, maximum in limits.items():
            if key in values and (
                type(values[key]) is not str
                or not values[key].strip()
                or len(values[key]) > maximum
            ):
                raise ValueError
        if endpoint != "items" and values["region"] not in ("cn", "global"):
            raise ValueError
        if endpoint == "character":
            return {
                "realm": values["region"],
                "server": values["server"],
                "character": values["character"],
                "metric": "rdps",
            }
        if endpoint == "calendar":
            if type(values["days"]) is not int or not 1 <= values["days"] <= 30:
                raise ValueError
            ZoneInfo(values["timezone"])
        return values
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ValueError("request_rejected") from None


def ordinary_snapshot(values, defaults=None):
    return FF14ConfigSnapshot.from_values(
        {name: value for name, value in values.items() if name in ORDINARY_FIELDS}
    )


def legacy_config_copies(principal_id):
    # Reviewed Host assembly consumes this pure declaration, never ModuleServices.
    return (
        {
            "policy_id": "ff14.default-region",
            "source_owner": "game_link/core",
            "source_field": "default_region",
            "target_owner": "ff14/ff14",
            "target_field": REGION,
            "target_schema": dict(REGION_CONFIG_FIELD.value_schema),
        },
    )


@dataclass(frozen=True, slots=True)
class ReviewedSupport:
    module_id: str = "ff14/ff14"
    validators: object = None
    metadata: object = None
    ordinary_config_fields = tuple(field.name for field in CALENDAR_CONFIG_FIELDS) + (
        "ff14_subscriptions_enabled",
    )

    def with_metadata(self, metadata):
        return replace(self, metadata=metadata)

    ordinary_snapshot = staticmethod(ordinary_snapshot)
    legacy_config_copies = staticmethod(legacy_config_copies)
    query_parameters = staticmethod(query_parameters)


SUPPORT = ReviewedSupport(validators=CALENDAR_VALUE_VALIDATORS)
