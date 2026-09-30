"""Narrow FFLogs domain values; raw GraphQL documents stay in the adapters."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


class FFLogsPayloadError(ValueError):
    """An FFLogs payload does not match the bounded candidate shape."""


@dataclass(frozen=True, slots=True)
class RegionRecord:
    region_id: int
    name: str
    compact_name: str | None
    slug: str


@dataclass(frozen=True, slots=True)
class ServerRecord:
    server_id: int
    name: str
    normalized_name: str | None
    slug: str
    region_id: int
    region_name: str
    region: str
    subregion_id: int
    subregion: str | None = None
    directory_region_id: int = 0
    directory_region_name: str = ""
    directory_region_compact_name: str | None = None
    directory_region_slug: str = ""


@dataclass(frozen=True, slots=True)
class ServerResolution:
    complete: bool
    matches: tuple[ServerRecord, ...]
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class DifficultyRecord:
    difficulty_id: int
    name: str


@dataclass(frozen=True, slots=True)
class EncounterRecord:
    encounter_id: int
    name: str


@dataclass(frozen=True, slots=True)
class ZoneRecord:
    zone_id: int
    name: str
    difficulties: tuple[DifficultyRecord, ...]
    encounters: tuple[EncounterRecord, ...]


@dataclass(frozen=True, slots=True)
class GameSpecRecord:
    spec_id: int
    name: str
    slug: str


@dataclass(frozen=True, slots=True)
class OutputMetadata:
    zones: tuple[ZoneRecord, ...]
    specs: tuple[GameSpecRecord, ...]


@dataclass(frozen=True, slots=True)
class MetadataCandidate:
    zone_id: int
    zone_name: str
    encounter_id: int
    encounter_name: str
    difficulty_id: int
    difficulty_name: str
    spec_id: int
    spec_name: str
    spec_slug: str


@dataclass(frozen=True, slots=True)
class MetadataResolution:
    complete: bool
    matches: tuple[MetadataCandidate, ...]
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class CharacterRanking:
    encounter_id: int
    encounter_name: str
    rank_percent: float | None
    best_amount: float | None
    spec: str | None


@dataclass(frozen=True, slots=True)
class RankingSelection:
    metric: str
    zone_id: int
    difficulty_id: int
    partition: int
    size: int
    rows: tuple[CharacterRanking, ...]


@dataclass(frozen=True, slots=True)
class PublicCharacter:
    character_id: int
    canonical_id: int | None
    name: str
    server_name: str
    hidden: bool
    metric: str
    zone_id: int
    difficulty_id: int
    partition: int
    size: int
    rankings: tuple[CharacterRanking, ...]


def parse_rankings(value: object) -> RankingSelection:
    """Parse the locked reference's single strict ``rankings`` candidate shape."""

    if not isinstance(value, dict):
        raise FFLogsPayloadError("rankings must be an object")
    rows = value.get("rankings")
    if not isinstance(rows, list) or len(rows) > 500:
        raise FFLogsPayloadError("rankings must be a bounded list")
    metric = _short_text(value.get("metric"), 32, "metric").casefold()
    if metric not in {"rdps", "ndps", "cdps"}:
        raise FFLogsPayloadError("ranking metric is unsupported")
    zone_id = _positive_int(value.get("zone"), "zone id")
    difficulty_id = _positive_int(value.get("difficulty"), "difficulty id")
    partition = _bounded_partition(value.get("partition"))
    size = _nonnegative_int(value.get("size"), "size")

    result: list[CharacterRanking] = []
    for row in rows:
        if not isinstance(row, dict):
            raise FFLogsPayloadError("ranking row must be an object")
        encounter = row.get("encounter")
        if not isinstance(encounter, dict):
            raise FFLogsPayloadError("ranking encounter must be an object")
        encounter_id = _positive_int(encounter.get("id"), "encounter id")
        encounter_name = _short_text(encounter.get("name"), 160, "encounter name")
        rank_percent = _optional_number(row.get("rankPercent"), 0.0, 100.0)
        best_amount = _optional_number(row.get("bestAmount"), 0.0, 1_000_000_000.0)
        spec = row.get("bestSpec", row.get("spec"))
        if spec is not None:
            spec = _short_text(spec, 80, "spec")
        result.append(
            CharacterRanking(
                encounter_id, encounter_name, rank_percent, best_amount, spec
            )
        )
    return RankingSelection(
        metric, zone_id, difficulty_id, partition, size, tuple(result)
    )


def parse_public_character(value: object) -> PublicCharacter:
    """Parse only the documented identity projection and candidate rankings."""

    if not isinstance(value, dict):
        raise FFLogsPayloadError("character must be an object")
    character_id = _positive_int(value.get("id"), "character id")
    canonical_id = _optional_positive_int(value.get("canonicalID"), "canonical id")
    name = _short_text(value.get("name"), 120, "character name")
    hidden = value.get("hidden")
    if type(hidden) is not bool:
        raise FFLogsPayloadError("hidden visibility flag is required")
    server = value.get("server")
    if not isinstance(server, dict):
        raise FFLogsPayloadError("server projection is required")
    server_name = _short_text(server.get("name"), 120, "server name")
    ranking_selection = parse_rankings(value.get("rankings"))
    return PublicCharacter(
        character_id,
        canonical_id,
        name,
        server_name,
        hidden,
        ranking_selection.metric,
        ranking_selection.zone_id,
        ranking_selection.difficulty_id,
        ranking_selection.partition,
        ranking_selection.size,
        ranking_selection.rows,
    )


def _positive_int(value: object, field: str) -> int:
    if type(value) is not int or not 1 <= value <= 2_147_483_647:
        raise FFLogsPayloadError(f"{field} must be a positive bounded integer")
    return value


def _optional_positive_int(value: object, field: str) -> int | None:
    return None if value is None else _positive_int(value, field)


def _bounded_partition(value: object) -> int:
    if type(value) is not int or not -1 <= value <= 2_147_483_647:
        raise FFLogsPayloadError("partition is outside its supported range")
    return value


def _nonnegative_int(value: object, field: str) -> int:
    if type(value) is not int or not 0 <= value <= 2_147_483_647:
        raise FFLogsPayloadError(f"{field} is outside its supported range")
    return value


def _optional_number(value: object, minimum: float, maximum: float) -> float | None:
    if value is None:
        return None
    if type(value) not in {int, float}:
        raise FFLogsPayloadError("ranking number has an invalid type")
    number = float(value)
    if not isfinite(number) or not minimum <= number <= maximum:
        raise FFLogsPayloadError("ranking number is outside its supported range")
    return number


def _short_text(value: object, limit: int, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > limit
        or _has_control(value)
    ):
        raise FFLogsPayloadError(f"{field} is invalid")
    return value.strip()


def _has_control(value: str) -> bool:
    return any(ord(char) < 32 or 0x7F <= ord(char) <= 0x9F for char in value)


__all__ = [
    "CharacterRanking",
    "DifficultyRecord",
    "EncounterRecord",
    "FFLogsPayloadError",
    "GameSpecRecord",
    "MetadataCandidate",
    "MetadataResolution",
    "OutputMetadata",
    "PublicCharacter",
    "RankingSelection",
    "RegionRecord",
    "ServerRecord",
    "ServerResolution",
    "ZoneRecord",
    "parse_public_character",
    "parse_rankings",
]
