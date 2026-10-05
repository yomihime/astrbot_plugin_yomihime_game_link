"""Synthetic but source-valid acquisition responses for display budget tests."""

from __future__ import annotations

import json

from ygl_test_subject.adapters.astrbot.web_public import project_result
from ygl_test_subject.modules.ff14.features.item_sources import ItemSourceClient
from ygl_test_subject.modules.ff14.features.items import ItemLookup

from tests.modules.ff14.test_items import _FakeHttp, _Scopes

LINKED_GROUPS = ("nodes", "fishingSpots", "vendors", "drops", "instances", "quests")


def source_responses(counts=(5, 5, 5, 5, 5, 5), *, extra_warnings=False):
    primary = {
        "row_id": 90001,
        "fields": {
            "Name": "Synthetic display budget item",
            "Description": "Public description retained across projection.",
            "LevelItem": {"value": 7},
            "LevelEquip": 1,
        },
    }
    item = {"id": 90001}
    for group, count in zip(LINKED_GROUPS, counts, strict=True):
        item[group] = [str(1000 + index) for index in range(count)]
    if extra_warnings:
        # Valid envelope with bounded, unparseable optional acquisition entries.
        for group in ("craft", "tradeCurrency", "tradeShops"):
            item[group] = [None] * 6
    return primary, {"item": item, "partials": []}


async def lookup_result(counts=(5, 5, 5, 5, 5, 5), *, extra_warnings=False):
    http = _FakeHttp(list(source_responses(counts, extra_warnings=extra_warnings)))
    services = type("Services", (), {"scopes": _Scopes(http)})()
    return await ItemLookup(services).invoke(None, {"query": "90001"})


async def source_snapshot(counts=(5, 5, 5, 5, 5, 5), *, extra_warnings=False):
    http = _FakeHttp(list(source_responses(counts, extra_warnings=extra_warnings)))
    return await ItemSourceClient(http).lookup(90001)


async def export_projections():
    cases = {
        "boundary": ((5, 5, 5, 4, 1, 1), False),
        "overflow": ((5, 5, 5, 5, 1, 1), False),
        "route_cap": ((5, 5, 5, 5, 5, 5), False),
        "warnings": ((6, 6, 6, 6, 6, 6), True),
    }
    return {
        name: project_result(await lookup_result(counts, extra_warnings=warnings))
        for name, (counts, warnings) in cases.items()
    }


if __name__ == "__main__":
    import asyncio

    print(json.dumps(asyncio.run(export_projections()), ensure_ascii=False))
