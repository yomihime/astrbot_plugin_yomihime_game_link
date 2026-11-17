"""One durable Universalis directory shared by market and Logs requests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta

from ....yomihime_game_link_sdk.errors import (
    RevisionConflict,
    ServiceUnavailable,
    SourceHttpError,
    UniqueConstraintViolation,
)
from ....yomihime_game_link_sdk.results import ErrorCode
from ....yomihime_game_link_sdk.services import ConfigView, ModuleRecords
from ..config import server_directory_generation
from ..query_resolution import SUPPORTED_REGIONS, QueryResolutionError, _scope_token
from .market_sources import (
    DCS_PATH,
    SOURCE_ROOT,
    WORLDS_PATH,
    CatalogSnapshot,
    MarketPayloadError,
    MarketSession,
    Provenance,
    parse_catalog,
    plain_json,
    source_time,
)

COLLECTION = "server_directory"
KEY = "universalis"
UNKNOWN_COOLDOWN = timedelta(minutes=5)


def _snapshot(payload: object, generation: str) -> CatalogSnapshot | None:
    try:
        data = plain_json(payload)
        if not isinstance(data, dict):
            return None
        times = data.get("fetched_at_ms")
        if not isinstance(times, list) or len(times) != 2:
            return None
        stamps = [source_time(stamp, milliseconds=True) for stamp in times]
        if any(stamp is None for stamp in stamps):
            return None
        return CatalogSnapshot(
            parse_catalog(data.get("worlds"), data.get("dcs")),
            tuple(
                Provenance(SOURCE_ROOT + path, stamp, cached=True)
                for path, stamp in zip((WORLDS_PATH, DCS_PATH), stamps, strict=True)
            ),
            generation,
        )
    except (MarketPayloadError, QueryResolutionError):
        return None


def _has_targets(snapshot: CatalogSnapshot | None, targets) -> bool:
    if snapshot is None:
        return False
    for kind, token in targets:
        try:
            snapshot.catalog.find(kind, token)
        except QueryResolutionError as exc:
            # Already-validated tokens: the only unqualified parameter error
            # from find() is a missing entry. Ambiguous or unroutable worlds
            # are known identities and do not justify network traffic.
            if exc.code is ErrorCode.PARAMETER_ERROR and not exc.choices:
                return False
    return True


def _routable_ids(snapshot: CatalogSnapshot) -> set[tuple[str, int | str]]:
    return {
        (entry.kind, entry.id)
        for entry in snapshot.catalog.entries
        if entry.region in SUPPORTED_REGIONS
    }


class ServerDirectory:
    """Coalesce refreshes in this module, then use record CAS across instances.

    A claim stores the next allowed attempt while leaving the last complete
    snapshot intact. Each request opens fresh, invocation-bound record ports.
    The independent config generation fences reads, claims, and publication.
    """

    def __init__(self, config: ConfigView) -> None:
        self._config = config
        self._refresh = asyncio.Lock()

    async def _generation(self) -> str:
        return server_directory_generation((await self._config.current()).values)

    async def require_current(self, snapshot: CatalogSnapshot) -> None:
        if (
            snapshot.generation is not None
            and snapshot.generation != await self._generation()
        ):
            raise ServiceUnavailable()

    async def _load(self, records):
        # Load config after the record so an old generation cannot claim a
        # slot already written by a request from the new generation.
        current = await records.get(KEY)
        generation = await self._generation()
        value = current.value if current is not None else {}
        if value.get("generation") != generation:
            value = {}
        return current, generation, value, _snapshot(value.get("snapshot"), generation)

    async def read(
        self, session: MarketSession, records: ModuleRecords | None, targets
    ) -> CatalogSnapshot:
        if records is None:
            raise ServiceUnavailable()
        targets = tuple((kind, _scope_token(value, kind)) for kind, value in targets)
        collection = await records.collection(COLLECTION)
        _, _, _, snapshot = await self._load(collection)
        if _has_targets(snapshot, targets):
            return snapshot

        async with self._refresh:
            for _ in range(3):
                current, generation, value, snapshot = await self._load(collection)
                if _has_targets(snapshot, targets):
                    return snapshot
                now = session.wall_clock()
                try:
                    attempted = source_time(
                        value.get("attempted_at_ms"), milliseconds=True
                    )
                except MarketPayloadError:
                    attempted = None
                if (
                    attempted is not None
                    and timedelta(0) <= now - attempted < UNKNOWN_COOLDOWN
                ):
                    if snapshot is not None:
                        return snapshot
                    raise SourceHttpError("upstream_error")

                claim_value = {
                    "generation": generation,
                    "attempted_at_ms": int(now.timestamp() * 1000),
                }
                if snapshot is not None:
                    claim_value["snapshot"] = value["snapshot"]
                session.remaining()
                try:
                    if current is None:
                        claim = await collection.create(KEY, claim_value)
                    else:
                        claim = await collection.replace(
                            KEY, claim_value, expected_revision=current.revision
                        )
                except (RevisionConflict, UniqueConstraintViolation):
                    continue

                if generation != await self._generation():
                    raise ServiceUnavailable()
                fresh, payload = await session.fetch_catalog()
                if snapshot is not None and not _routable_ids(snapshot).issubset(
                    _routable_ids(fresh)
                ):
                    # A structurally valid but smaller response can strand
                    # old worlds as unroutable. A deliberate clear permits
                    # rebuilding against a changed upstream directory.
                    raise MarketPayloadError("directory_coverage_regressed")
                fresh = replace(fresh, generation=generation)
                await self.require_current(fresh)
                session.remaining()
                try:
                    await collection.replace(
                        KEY,
                        {**claim_value, "snapshot": payload},
                        expected_revision=claim.revision,
                    )
                except RevisionConflict:
                    # Another worker/clear already moved the slot. Do not
                    # reuse this network result against a newer revision.
                    raise ServiceUnavailable() from None
                await self.require_current(fresh)
                return fresh
        raise ServiceUnavailable()
