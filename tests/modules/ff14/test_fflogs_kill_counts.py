"""FFLogs kill counts from bounded, public fight evidence."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from ygl_test_subject.modules.ff14.features.fflogs_kill_counts import (
    MAX_RANKS,
    MAX_TARGETS,
    KillCount,
    KillCountCharacterHidden,
    fetch_kill_counts,
)
from ygl_test_subject.yomihime_game_link_sdk.errors import AccessDenied, SourceHttpError
from ygl_test_subject.yomihime_game_link_sdk.services import HttpResponse

_START = 1_700_000_000_000
_STEP = 1_800_000


def fight(report, fight_id, start, duration=720_000, spec="RedMage"):
    return {
        "spec": spec,
        "startTime": start,
        "duration": duration,
        "report": {
            "code": f"{report:016d}",
            "fightID": fight_id,
            "startTime": start - 100,
        },
    }


def ranking(zone, rows, *, partition=-1, difficulty=100, **fields):
    return {
        "zone": zone,
        "partition": partition,
        "difficulty": difficulty,
        "metric": "rdps",
        "totalKills": len(rows),
        "ranks": rows,
        **fields,
    }


class FakeHttp:
    def __init__(self, response):
        self.response = response
        self.requests = []

    async def fetch(self, request):
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def response(aliases, *, identity=None, errors=None, http=200):
    character = {
        "id": 123,
        "name": "Example Hero",
        "hidden": False,
        "server": {"name": "Example World"},
        **aliases,
        **(identity or {}),
    }
    body = {"data": {"characterData": {"character": character}}}
    if errors is not None:
        body["errors"] = errors
    return HttpResponse(http, {}, json.dumps(body).encode("utf-8"))


class KillCountTests(unittest.IsolatedAsyncioTestCase):
    async def fetch(self, upstream, *targets):
        http = FakeHttp(upstream)
        result = await fetch_kill_counts(
            SimpleNamespace(http=http),
            "fflogs_public_cn",
            character_id=123,
            name="Example Hero",
            server_name="Example World",
            server_slug="example-world",
            server_region="cn",
            targets=targets,
        )
        return result, http.requests

    async def test_historical_ultimates_across_jobs_and_savage_stay_independent(self):
        historic = [fight(1, index + 1, _START + index * _STEP) for index in range(3)]
        recent = [
            fight(2, index + 1, _START + (index + 10) * _STEP, spec=spec)
            for index, spec in enumerate(["Bard"] * 4 + ["Ninja"] * 6)
        ]
        result, requests = await self.fetch(
            response(
                {
                    "k0": ranking(23, [], partition=3),
                    "k1": ranking(30, historic, partition=3),
                    "k2": ranking(43, []),
                    "k3": ranking(59, recent),
                    "k4": ranking(61, [fight(3, 1, _START)], difficulty=101),
                }
            ),
            (23, 100, 1042, "ultimate"),
            (30, 100, 1048, "ultimate"),
            (43, 100, 1061, "ultimate"),
            (59, 100, 1074, "ultimate"),
            (61, 101, 1078, "savage"),
        )
        self.assertEqual(
            result, {"ultimate": KillCount(13, False), "savage": KillCount(1, True)}
        )
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request.source_id, "fflogs_public_cn")
        query = json.loads(request.body)["query"]
        self.assertEqual(query.count("includePrivateLogs: false"), 5)
        self.assertEqual(query.count("partition: -1"), 5)
        self.assertEqual(query.count("metric: rdps"), 5)
        self.assertNotIn("specName", query)

    async def test_deduplicates_exact_reports_and_overlapping_cross_report_times(self):
        first = fight(1, 1, _START)
        copy = fight(2, 8, _START + 800)
        next_fight = fight(3, 1, _START + _STEP)
        result, _ = await self.fetch(
            response(
                {
                    "k0": ranking(30, [first, next_fight]),
                    "k1": ranking(59, [first, copy]),
                }
            ),
            (30, 100, 1048, "ultimate"),
            (59, 100, 1074, "ultimate"),
        )
        self.assertEqual(result, {"ultimate": KillCount(2, False)})

    async def test_exact_duplicate_across_complete_aliases_remains_one_count(self):
        first = fight(1, 1, _START)
        result, _ = await self.fetch(
            response({"k0": ranking(30, [first]), "k1": ranking(59, [first])}),
            (30, 100, 1048, "ultimate"),
            (59, 100, 1074, "ultimate"),
        )
        self.assertEqual(result, {"ultimate": KillCount(1, True)})

    async def test_zero_requires_verified_complete_scope(self):
        result, _ = await self.fetch(
            response({"k0": ranking(30, []), "k1": ranking(43, [], partition=1)}),
            (30, 100, 1048, "zero"),
            (43, 100, 1061, "unknown"),
            (59, 100, 1074, "absent"),
        )
        self.assertEqual(result, {"zero": KillCount(0, True)})

    async def test_rejects_malformed_or_oversized_selections(self):
        first = fight(1, 1, _START)
        wrong_metric = ranking(43, [first])
        wrong_metric["metric"] = "cdps"
        wrong_total = ranking(59, [first])
        wrong_total["totalKills"] = 20
        size_limit = ranking(61, [first] * (MAX_RANKS + 1))
        result, _ = await self.fetch(
            response(
                {
                    "k0": ranking(31, [first]),
                    "k1": wrong_metric,
                    "k2": wrong_total,
                    "k3": size_limit,
                }
            ),
            (30, 100, 1048, "scope"),
            (43, 100, 1061, "metric"),
            (59, 100, 1074, "count"),
            (61, 100, 1078, "limit"),
        )
        self.assertEqual(result, {})

    async def test_invalid_rows_do_not_count_as_distinct_kills(self):
        invalid = fight(1, True, _START)
        valid = fight(1, 1, _START + _STEP)
        result, _ = await self.fetch(
            response({"k0": ranking(30, [invalid, valid])}),
            (30, 100, 1048, "ultimate"),
        )
        self.assertEqual(result, {"ultimate": KillCount(1, False)})

    async def test_target_limit_marks_omitted_history_incomplete(self):
        targets = tuple(
            (30, 100, 1048 + index, "ultimate") for index in range(MAX_TARGETS + 1)
        )
        first = fight(1, 1, _START)
        result, requests = await self.fetch(
            response(
                {f"k{index}": ranking(30, [first]) for index in range(MAX_TARGETS)}
            ),
            *targets,
        )
        self.assertEqual(result, {"ultimate": KillCount(1, False)})
        self.assertEqual(
            json.loads(requests[0].body)["query"].count("encounterRankings"),
            MAX_TARGETS,
        )

    async def test_rejects_changed_identity_hidden_characters_and_partial_errors(self):
        aliases = {"k0": ranking(30, [fight(1, 1, _START)])}
        for upstream in (
            response(aliases, identity={"id": 124}),
            response(aliases, identity={"name": "Different Hero"}),
            response(aliases, identity={"server": {"name": "Different World"}}),
            response(aliases, errors=[{"path": ["characterData", "character", "k1"]}]),
            response(aliases, http=503),
            SourceHttpError("rate_limited"),
        ):
            with self.subTest(upstream=upstream):
                result, _ = await self.fetch(upstream, (30, 100, 1048, "ultimate"))
                self.assertEqual(result, {})
        with self.assertRaises(AccessDenied):
            await self.fetch(
                AccessDenied("source_revoked"), (30, 100, 1048, "ultimate")
            )
        with self.assertRaises(KillCountCharacterHidden):
            await self.fetch(
                response(aliases, identity={"hidden": True}),
                (30, 100, 1048, "ultimate"),
            )
        with self.assertRaises(KillCountCharacterHidden):
            await self.fetch(
                response(aliases, identity={"hidden": True}, errors=[{}]),
                (30, 100, 1048, "ultimate"),
            )
