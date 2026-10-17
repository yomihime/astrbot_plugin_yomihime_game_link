"""Synthetic public game-data catalog fixtures; no live requests."""

import asyncio
import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from modules.ff14.features.duty_order import (
    DutyOrder,
    DutyOrderCatalog,
    apply_duty_order,
    parse_page,
)
from modules.ff14.features.fflogs_models import CharacterRankingGroup
from tests.modules.ff14 import test_fflogs_aggregation as fixture
from yomihime_game_link_sdk.errors import ServiceUnavailable, SourceHttpError

NAMES = (
    "巴哈姆特绝境战",
    "究极神兵绝境战",
    "亚历山大绝境战",
    "幻想龙诗绝境战",
    "欧米茄绝境验证战",
    "光暗未来绝境战",
    "妖星乱舞绝境战",
)


def payload(names=NAMES, cursor=None):
    return json.dumps(
        {
            "results": [
                {
                    "sheet": "ContentFinderCondition",
                    "row_id": i + 700,
                    "fields": {
                        "Name": name,
                        "SortKey": 1001 + i,
                        "ContentType@as(raw)": 28,
                    },
                }
                for i, name in enumerate(names)
            ],
            "next": cursor,
        }
    ).encode()


class Http:
    def __init__(self, response=None):
        self.requests = []
        self.response = response or payload()

    async def fetch(self, request):
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return SimpleNamespace(status_code=200, body=self.response)


class DutyOrderTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_managed_request_cache_and_close(self):
        http, client = Http(), DutyOrderCatalog()
        first = await client.load(http)
        self.assertTrue(first.complete)
        self.assertEqual(await client.load(http), first)
        self.assertEqual(len(http.requests), 1)
        request = http.requests[0]
        self.assertEqual(
            (request.source_id, request.path), ("xivapi_items", "/api/search")
        )
        self.assertEqual(dict(request.query)["query"], "ContentType=28")
        self.assertEqual(dict(request.query)["limit"], "100")
        client.close()
        await client.load(http)
        self.assertEqual(len(http.requests), 2)

    async def test_failures_negative_cache_timeout_and_revocation(self):
        client, http = DutyOrderCatalog(), Http(ValueError("invalid"))
        self.assertEqual(await client.load(http), DutyOrder())
        self.assertEqual(await client.load(http), DutyOrder())
        self.assertEqual(len(http.requests), 1)
        client.close()
        http.response = PermissionError()
        with self.assertRaises(PermissionError):
            await client.load(http)

        async def slow(request):
            await asyncio.sleep(10)

        with patch.object(http, "fetch", slow):
            from time import monotonic

            self.assertEqual(
                await client.load(http, deadline=monotonic() + 0.001), DutyOrder()
            )
        client.close()

        async def revoked(request):
            client.close()
            return SimpleNamespace(status_code=200, body=payload())

        with patch.object(http, "fetch", revoked):
            with self.assertRaises(ServiceUnavailable):
                await client.load(http)

    async def test_pagination_bounded_partial_and_bad_shapes(self):
        http, client = Http(payload(cursor="next-page")), DutyOrderCatalog()
        # Repeated cursors reject the whole unsafe result.
        self.assertEqual(await client.load(http), DutyOrder())
        self.assertEqual(len(http.requests), 2)
        for bad in (b"{}", payload().replace(b"28", b"27"), b"x" * 262145):
            with self.assertRaises(ValueError):
                parse_page(bad)

    async def test_dynamic_sort_exact_full_names_ambiguity_and_unknown_fallback(self):
        row = fixture.record().rankings[0]
        names = (*NAMES, "未来新增绝境战", "未匹配绝境战")
        groups = tuple(
            CharacterRankingGroup(
                i + 1,
                name,
                1,
                3,
                "ultimate",
                "Unknown",
                (replace(row, encounter_name=name),),
            )
            for i, name in enumerate(names)
        )
        character = replace(fixture.record(), groups=groups)
        parsed, _ = parse_page(payload(names[:-1]))
        result = apply_duty_order(character, DutyOrder(tuple(parsed), True))
        self.assertEqual(
            [g.rankings[0].encounter_name for g in result.groups],
            list(reversed(names[:-1])) + [names[-1]],
        )
        # Duplicate exact names are ambiguous even when their keys agree.
        result = apply_duty_order(
            character, DutyOrder(tuple(parsed + [parsed[-1]]), True)
        )
        self.assertEqual(
            [g.rankings[0].encounter_name for g in result.groups][-2:], list(names[-2:])
        )
        self.assertEqual(
            apply_duty_order(character, DutyOrder()).groups, character.groups
        )

    async def test_source_policy_denials_and_partial_catalog_never_promote_names(self):
        for code in ("source_not_declared", "request_rejected"):
            with self.assertRaises(PermissionError):
                await DutyOrderCatalog().load(Http(SourceHttpError(code)))
        self.assertEqual(
            await DutyOrderCatalog().load(Http(SourceHttpError("timeout"))), DutyOrder()
        )
        http = Http()
        responses = iter((payload(cursor="one"), payload(cursor="two")))

        async def pages(request):
            return SimpleNamespace(status_code=200, body=next(responses))

        with patch.object(http, "fetch", pages):
            partial = await DutyOrderCatalog().load(http)
        self.assertFalse(partial.complete)
        character = fixture.record()
        self.assertIs(apply_duty_order(character, partial), character)
