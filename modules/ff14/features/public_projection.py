"""FF14-owned public market fact semantics, independent of Host projection."""

import re
from collections.abc import Mapping


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def validate_public_market_facts(facts: Mapping) -> dict:
    """Explicit public market/candidate whitelist, independent of display text."""
    if set(facts) not in ({"market"}, {"market", "selection"}):
        raise ValueError("invalid_public_market_facts")
    data = _plain(facts)
    market = data["market"]
    if type(market) is not dict or set(market) != {
        "query",
        "scope",
        "quality",
        "intent",
        "module_revision",
        "core_revision",
        "coverage",
        "truncated",
    }:
        raise ValueError("invalid_public_market_facts")
    scope = market["scope"]
    if (
        type(scope) is not dict
        or set(scope) != {"kind", "target", "regions", "source"}
        or scope["kind"] not in ("world", "dc", "region")
        or scope["source"] not in ("explicit", "default")
        or type(scope["regions"]) is not list
        or not 1 <= len(scope["regions"]) <= 4
        or any(
            r not in ("China", "North-America", "Europe", "Japan", "Oceania")
            for r in scope["regions"]
        )
        or type(market["query"]) is not str
        or not 1 <= len(market["query"]) <= 120
        or market["quality"] not in ("all", "nq", "hq")
        or market["intent"] not in ("overview", "min", "listings")
        or type(market["truncated"]) is not bool
        or any(
            type(market[k]) is not int or market[k] < 0
            for k in ("module_revision", "core_revision")
        )
        or type(market["coverage"]) is not list
        or len(market["coverage"]) > 4
    ):
        raise ValueError("invalid_public_market_facts")
    target = scope["target"]
    if (
        scope["kind"] == "world"
        and (type(target) is not int or not 1 <= target <= 2147483647)
        or scope["kind"] == "dc"
        and (type(target) is not str or not 1 <= len(target) <= 100)
        or scope["kind"] == "region"
        and target is not None
    ):
        raise ValueError("invalid_public_market_facts")
    for coverage in market["coverage"]:
        if (
            type(coverage) is not dict
            or set(coverage)
            != {
                "region",
                "target",
                "state",
                "stage",
                "reason",
                "status_code",
                "cached",
                "fetched_at",
            }
            or coverage["region"] not in scope["regions"]
            or coverage["state"] not in ("available", "empty", "failed")
            or type(coverage["target"]) is not str
            or len(coverage["target"]) > 100
            or coverage["stage"]
            not in (None, "parse", "source", "http", "deadline", "cache")
            or coverage["reason"] is not None
            and (
                type(coverage["reason"]) is not str
                or not re.fullmatch(r"[a-z_]{1,64}", coverage["reason"])
            )
            or coverage["fetched_at"] is not None
            and type(coverage["fetched_at"]) is not str
            or coverage["cached"] is not None
            and type(coverage["cached"]) is not bool
            or coverage["status_code"] is not None
            and (
                type(coverage["status_code"]) is not int
                or not 100 <= coverage["status_code"] <= 599
            )
        ):
            raise ValueError("invalid_public_market_facts")
    if "selection" in data:
        choice = data["selection"]
        if (
            type(choice) is not dict
            or set(choice)
            != {"kind", "batch_id", "generation", "candidates", "truncated"}
            or choice["kind"] != "item"
            or type(choice["truncated"]) is not bool
            or any(
                type(choice[k]) is not str
                or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", choice[k])
                for k in ("batch_id", "generation")
            )
            or type(choice["candidates"]) is not list
            or len(choice["candidates"]) > 6
        ):
            raise ValueError("invalid_public_market_facts")
        for candidate in choice["candidates"]:
            if (
                type(candidate) is not dict
                or set(candidate) != {"item_id", "name"}
                or type(candidate["item_id"]) is not int
                or not 1 <= candidate["item_id"] <= 2147483647
                or type(candidate["name"]) is not str
                or not 1 <= len(candidate["name"]) <= 160
            ):
                raise ValueError("invalid_public_market_facts")
    return data
