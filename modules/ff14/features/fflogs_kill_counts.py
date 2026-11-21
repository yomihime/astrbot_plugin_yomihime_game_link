"""Conservative counts from public encounter fight evidence."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from math import ceil

from ....yomihime_game_link_sdk.errors import SourceHttpError
from ....yomihime_game_link_sdk.services import HttpRequest
from .fflogs_catalog import GRAPHQL_PATH, FFLogsGraphQLError, parse_graphql_envelope
from .fflogs_models import FFLogsPayloadError

MAX_TARGETS = 32
MAX_RANKS = 256
ALL_PARTITIONS = -1
_REPORT_CODE = re.compile(r"[A-Za-z0-9]{16}")
_CLOCK_MARGIN_MS = 10_000
_MAX_TIMESTAMP_MS = 32_503_680_000_000


@dataclass(frozen=True, slots=True)
class KillCount:
    count: int
    complete: bool


class KillCountCharacterHidden(Exception):
    """The same character withdrew public visibility during the lookup."""


@dataclass(frozen=True, slots=True)
class _Fight:
    report_code: str
    fight_id: int
    start: int
    end: int


def _is_int(value, low, high):
    return type(value) is int and low <= value <= high


def _identity(value):
    return " ".join(value.casefold().split()) if isinstance(value, str) else None


def _request(targets, name, server_slug, server_region):
    selections = [
        f"k{index}: encounterRankings(encounterID: {encounter}, difficulty: {difficulty}, "
        "metric: rdps, partition: -1, includePrivateLogs: false)"
        for index, (_, difficulty, encounter, _) in enumerate(targets)
    ]
    query = """
query FF14PublicKillCounts(
  $name: String!, $serverSlug: String!, $serverRegion: String!
) {
  characterData {
    character(name: $name, serverSlug: $serverSlug, serverRegion: $serverRegion) {
      id name hidden server { name }
      %s
    }
  }
}
""" % "\n      ".join(selections)
    return json.dumps(
        {
            "query": query,
            "variables": {
                "name": name,
                "serverSlug": server_slug,
                "serverRegion": server_region,
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _fight(row):
    if not isinstance(row, dict) or not isinstance(row.get("report"), dict):
        return None
    report = row["report"]
    code = report.get("code")
    fight_id = report.get("fightID")
    report_start = report.get("startTime")
    start = row.get("startTime")
    duration = row.get("duration")
    if (
        not isinstance(code, str)
        or _REPORT_CODE.fullmatch(code) is None
        or not _is_int(fight_id, 1, 2_147_483_647)
        or not _is_int(report_start, 1, _MAX_TIMESTAMP_MS)
        or not _is_int(start, report_start, _MAX_TIMESTAMP_MS)
        or type(duration) not in (int, Decimal)
        or isinstance(duration, Decimal)
        and not duration.is_finite()
        # Accept only durations plausible as milliseconds for these encounters.
        or not 60_000 <= duration <= 7_200_000
    ):
        return None
    return _Fight(code, fight_id, start, start + ceil(duration))


def _parse_alias(value, zone, difficulty, encounter):
    if not isinstance(value, dict):
        return (), False
    partition = value.get("partition")
    total = value.get("totalKills")
    ranks = value.get("ranks")
    if (
        not _is_int(value.get("zone"), zone, zone)
        or not _is_int(value.get("difficulty"), difficulty, difficulty)
        or "encounterID" in value
        and not _is_int(value["encounterID"], encounter, encounter)
        or value.get("metric") != "rdps"
        or not _is_int(partition, ALL_PARTITIONS, 2_147_483_647)
        or partition == 0
        or not _is_int(total, 0, MAX_RANKS)
        or not isinstance(ranks, list)
        or len(ranks) != total
    ):
        return (), False
    parsed = [_fight(row) for row in ranks]
    fights = tuple(fight for fight in parsed if fight is not None)
    complete = partition == ALL_PARTITIONS and len(fights) == total
    complete = complete and len({(f.report_code, f.fight_id) for f in fights}) == total
    return fights, complete


def _aggregate(fights, complete):
    unique = {}
    for fight in fights:
        key = (fight.report_code, fight.fight_id)
        interval = (fight.start, fight.end)
        previous = unique.get(key)
        if previous is not None and previous != interval:
            # Repeated report identities with disagreeing times get the wider interval.
            interval = min(previous[0], fight.start), max(previous[1], fight.end)
            complete = False
        unique[key] = interval

    # Earliest end first gives a maximal set of mutually separated intervals.
    # Near starts or overlapping copies from other reports can represent one kill.
    count = 0
    next_start = 0
    for start, end in sorted(unique.values(), key=lambda interval: interval[1]):
        if start >= next_start:
            count += 1
            next_start = end + _CLOCK_MARGIN_MS
    return KillCount(count, complete and count == len(unique))


async def fetch_kill_counts(
    scope,
    source_id,
    *,
    character_id,
    name,
    server_name,
    server_slug,
    server_region,
    targets,
    http=None,
) -> dict:
    """Read one bounded all-jobs batch; missing groups represent unknown counts.

    The caller supplies verified catalog identity for each target and decides
    whether the given targets cover the entire history for that display row.
    """
    if not targets or not _is_int(character_id, 1, 2_147_483_647):
        return {}
    targets = tuple(dict.fromkeys(targets))
    requested = targets[:MAX_TARGETS]
    if any(
        not all(
            _is_int(value, 1, 2_147_483_647) for value in (zone, difficulty, encounter)
        )
        for zone, difficulty, encounter, _ in requested
    ):
        return {}
    partial = False
    try:
        response = await (http if http is not None else scope.http).fetch(
            HttpRequest(
                source_id,
                GRAPHQL_PATH,
                method="POST",
                body=_request(requested, name, server_slug, server_region),
                headers={"Content-Type": "application/json"},
            )
        )
        if response.status_code != 200:
            return {}
        try:
            data = parse_graphql_envelope(response.body, exact_numbers=True)
        except FFLogsGraphQLError:
            # Honor a verified visibility withdrawal even on a partial reply.
            # Alias data from a response with errors never contributes counts.
            partial = True
            data = parse_graphql_envelope(
                response.body, exact_numbers=True, allow_partial_errors=True
            )
    except SourceHttpError as exc:
        if exc.code == "cancelled":
            raise
        return {}
    except FFLogsPayloadError:
        return {}

    character_data = data.get("characterData")
    character = (
        character_data.get("character") if isinstance(character_data, dict) else None
    )
    if not isinstance(character, dict):
        return {}
    server = character.get("server")
    if (
        not _is_int(character.get("id"), character_id, character_id)
        or _identity(character.get("name")) != _identity(name)
        or not isinstance(server, dict)
        or _identity(server.get("name")) != _identity(server_name)
    ):
        return {}
    if character.get("hidden") is True:
        raise KillCountCharacterHidden
    if partial or character.get("hidden") is not False:
        return {}

    by_duty = defaultdict(list)
    incomplete = {duty for _, _, _, duty in targets[MAX_TARGETS:]}
    for index, (zone, difficulty, encounter, duty) in enumerate(requested):
        fights, complete = _parse_alias(
            character.get(f"k{index}"), zone, difficulty, encounter
        )
        by_duty[duty].extend(fights)
        if not complete:
            incomplete.add(duty)
    counts = {}
    for duty, fights in by_duty.items():
        result = _aggregate(fights, duty not in incomplete)
        if result.count or result.complete:
            counts[duty] = result
    return counts
