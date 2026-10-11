"""Universalis source contracts and a single invocation deadline (no registration)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar
from urllib.parse import urlencode

from yomihime_game_link_sdk.errors import SourceHttpError
from yomihime_game_link_sdk.services import CacheAccess, HttpRequest, SourceHttp

from ..query_resolution import SUPPORTED_REGIONS, CatalogEntry, ScopeCatalog

UNIVERSALIS_SOURCE = "universalis_market"
SOURCE_ROOT = "https://universalis.app"
WORLDS_PATH = "/api/v2/worlds"
DCS_PATH = "/api/v2/data-centers"
SOURCE_VERSION = "universalis-market-v1"
DIRECTORY_TTL = 300
MARKET_TTL = 30
MAX_RESPONSE_BYTES = 1_048_576
T = TypeVar("T")


class MarketPayloadError(ValueError):
    """Safe upstream shape failure; contains no response body or rejected value."""

    def __init__(self, code: str = "invalid_payload") -> None:
        self.code = code
        super().__init__("Universalis response shape is invalid")


class MarketDeadlineError(TimeoutError):
    pass


@dataclass(frozen=True, slots=True)
class Provenance:
    url: str
    fetched_at: datetime
    cached: bool = False


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    catalog: ScopeCatalog
    provenance: tuple[Provenance, ...]

    def target(self, kind: str, target_id: int | str) -> str:
        entry = self.catalog.find(kind, target_id)
        # Universalis resolves integer route segments as World IDs. Q1 DC IDs
        # remain useful canonical identities; the transport uses official names.
        return str(entry.id) if kind == "world" else entry.name


def integer(value: object, *, minimum: int = 0, maximum: int = 2_147_483_647) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise MarketPayloadError("invalid_integer")
    return value


def number(value: object) -> int | float:
    if (
        type(value) not in (int, float)
        or value < 0
        or value > 1e15
        or (type(value) is float and not math.isfinite(value))
    ):
        raise MarketPayloadError("invalid_number")
    return value


def text(value: object, *, limit: int = 160) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise MarketPayloadError("invalid_text")
    if any(ord(char) < 32 or 0x7F <= ord(char) <= 0x9F for char in value):
        raise MarketPayloadError("invalid_text")
    return value.strip()


def source_time(value: object, *, milliseconds: bool) -> datetime | None:
    if value is None or (type(value) is int and value == 0):
        return None
    integer(value, maximum=253_402_300_799_999 if milliseconds else 253_402_300_799)
    try:
        return datetime.fromtimestamp(value / (1000 if milliseconds else 1), UTC)
    except (ValueError, OverflowError, OSError):
        raise MarketPayloadError("invalid_timestamp") from None


def parse_catalog(worlds: object, data_centers: object) -> ScopeCatalog:
    """Normalize the two official arrays, without inventing World/DC aliases.

    China/中国 is the documented region spelling equivalence. Other directory
    regions remain explicit unsupported entries rather than being reassigned to
    one of the standard regions. Every World must have one verified DC parent.
    """
    if not isinstance(worlds, list) or not 1 <= len(worlds) <= 1024:
        raise MarketPayloadError("invalid_worlds")
    if not isinstance(data_centers, list) or not 1 <= len(data_centers) <= 64:
        raise MarketPayloadError("invalid_data_centers")
    names: dict[int, str] = {}
    for row in worlds:
        if not isinstance(row, Mapping):
            raise MarketPayloadError("invalid_world")
        world_id = integer(row.get("id"), minimum=1)
        if world_id in names:
            raise MarketPayloadError("duplicate_world")
        names[world_id] = text(row.get("name"))
    entries: list[CatalogEntry] = []
    assigned: set[int] = set()
    dc_ids: set[str] = set()
    for row in data_centers:
        if not isinstance(row, Mapping):
            raise MarketPayloadError("invalid_dc")
        dc_id = text(row.get("name"))
        if dc_id in dc_ids:
            raise MarketPayloadError("duplicate_dc")
        dc_ids.add(dc_id)
        region = text(row.get("region"))
        if region == "中国":
            region = "China"
        dc_name = dc_id
        if region in SUPPORTED_REGIONS and any(
            not (char.isalnum() or char == "-") for char in dc_name
        ):
            raise MarketPayloadError("unsafe_dc_name")
        member_ids = row.get("worlds")
        if not isinstance(member_ids, list) or len(member_ids) > 1024:
            raise MarketPayloadError("invalid_membership")
        entries.append(CatalogEntry("dc", dc_id, dc_name, region))
        for member in member_ids:
            member = integer(member, minimum=1)
            if member not in names or member in assigned:
                raise MarketPayloadError("inconsistent_membership")
            assigned.add(member)
            entries.append(CatalogEntry("world", member, names[member], region, dc_id))
    if assigned != set(names):
        raise MarketPayloadError("unmapped_world")
    return ScopeCatalog(tuple(entries))


def _json_bytes(body: bytes) -> object:
    if len(body) > MAX_RESPONSE_BYTES:
        raise MarketPayloadError("response_too_large")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise MarketPayloadError("duplicate_key")
            result[key] = value
        return result

    def constant(value):
        raise MarketPayloadError("invalid_number")

    try:
        return json.loads(body, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise MarketPayloadError() from None


def plain_json(value: object, depth: int = 0) -> object:
    if depth > 16:
        raise MarketPayloadError("payload_depth")
    if isinstance(value, Mapping):
        return {key: plain_json(item, depth + 1) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain_json(item, depth + 1) for item in value]
    return value


class MarketSession:
    """Request-bound HTTP/cache handles, one 30s budget and concurrency <=2.

    Create before name/default preparation; use run() to put that preparation
    under the same budget. No retries, background work, or process-wide cache.
    Cache TTLs are initial resource policies, not freshness promises.
    """

    def __init__(
        self,
        http: SourceHttp,
        cache: CacheAccess | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        deadline: float | None = None,
        gate: asyncio.Semaphore | None = None,
    ) -> None:
        self.http, self.cache = http, cache
        self.clock, self.wall_clock = clock, wall_clock
        self.deadline = (
            min(clock() + 30, deadline) if deadline is not None else clock() + 30
        )
        self._semaphore = gate if gate is not None else asyncio.Semaphore(2)

    def remaining(self) -> float:
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise MarketDeadlineError("Market query deadline expired")
        return remaining

    async def run(self, work: Awaitable[T], *, request_limit: bool = False) -> T:
        try:
            remaining = self.remaining()
        except BaseException:
            if hasattr(work, "close"):
                work.close()
            elif isinstance(work, asyncio.Future):
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)
            raise
        limit = min(10, remaining) if request_limit else remaining
        try:
            async with asyncio.timeout(limit):
                value = await work
        except TimeoutError:
            if self.clock() >= self.deadline:
                raise MarketDeadlineError("Market query deadline expired") from None
            raise SourceHttpError("timeout") from None
        self.remaining()  # injected-clock tests also reject data arriving late
        return value

    async def _fetch(self, path: str, parameters: tuple[tuple[str, str], ...]):
        async with self._semaphore:
            self.remaining()  # the semaphore queue consumes the same budget
            response = await self.run(
                self.http.fetch(
                    HttpRequest(UNIVERSALIS_SOURCE, path, query=parameters)
                ),
                request_limit=True,
            )
            if not 200 <= response.status_code < 300:
                raise SourceHttpError(
                    "rate_limited" if response.status_code == 429 else "upstream_error",
                    status_code=response.status_code,
                )
            return _json_bytes(response.body)

    async def read(
        self,
        path: str,
        parameters: tuple[tuple[str, str], ...],
        validate: Callable[[object], T],
        *,
        ttl: int = MARKET_TTL,
        clean: Callable[[object], object] | None = None,
        key_context: tuple[str, ...] = (),
        use_cache: bool = True,
    ) -> tuple[T, Provenance]:
        url = SOURCE_ROOT + path + (("?" + urlencode(parameters)) if parameters else "")
        key = (
            "market-v1-"
            + hashlib.sha256(
                json.dumps(
                    [SOURCE_VERSION, path, parameters, key_context],
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
        )
        if self.cache is not None and use_cache:
            entry = await self.run(self.cache.get(key))
            if entry is not None:
                payload = entry.payload
                try:
                    if (
                        payload.get("version") != SOURCE_VERSION
                        or payload.get("url") != url
                    ):
                        raise MarketPayloadError("cache_provenance")
                    fetched = source_time(
                        payload.get("fetched_at_ms"), milliseconds=True
                    )
                    if (
                        fetched is None
                        or fetched > self.wall_clock()
                        or entry.expires_at <= self.wall_clock()
                    ):
                        raise MarketPayloadError("cache_timestamp")
                    result = validate(plain_json(payload.get("body")))
                except MarketPayloadError:
                    pass
                else:
                    self.remaining()
                    return result, Provenance(url, fetched, True)
        body = await self.run(self._fetch(path, parameters))
        result = validate(body)
        self.remaining()
        fetched = self.wall_clock()
        if fetched.tzinfo is None or fetched.utcoffset() is None:
            raise ValueError("wall_clock must return an aware datetime")
        if self.cache is not None and use_cache:
            cleaned = clean(body) if clean else body
            self.remaining()
            payload = {
                "version": SOURCE_VERSION,
                "url": url,
                "fetched_at_ms": int(fetched.timestamp() * 1000),
                "body": cleaned,
            }
            try:
                await self.run(self.cache.put(key, payload, ttl_seconds=ttl))
            except (MarketDeadlineError, SourceHttpError) as exc:
                if isinstance(exc, SourceHttpError) and exc.code != "timeout":
                    raise
                # Data already fetched and parsed before expiry remains useful;
                # a cache write never creates a continuation beyond the deadline.
        return result, Provenance(url, fetched)

    async def catalog(self) -> CatalogSnapshot:
        key = "market-directory-v1"
        urls = (SOURCE_ROOT + WORLDS_PATH, SOURCE_ROOT + DCS_PATH)
        if self.cache is not None:
            entry = await self.run(self.cache.get(key))
            if entry is not None:
                try:
                    payload = plain_json(entry.payload)
                    if (
                        payload.get("version") != SOURCE_VERSION
                        or tuple(payload.get("urls", ())) != urls
                        or entry.expires_at <= self.wall_clock()
                    ):
                        raise MarketPayloadError("cache_provenance")
                    times = payload.get("fetched_at_ms")
                    if not isinstance(times, list) or len(times) != 2:
                        raise MarketPayloadError("cache_timestamp")
                    stamps = tuple(
                        source_time(stamp, milliseconds=True) for stamp in times
                    )
                    if any(
                        stamp is None or stamp > self.wall_clock() for stamp in stamps
                    ):
                        raise MarketPayloadError("cache_timestamp")
                    catalog = parse_catalog(payload.get("worlds"), payload.get("dcs"))
                except MarketPayloadError:
                    pass
                else:
                    self.remaining()
                    return CatalogSnapshot(
                        catalog,
                        tuple(
                            Provenance(url, stamp, True)
                            for url, stamp in zip(urls, stamps, strict=True)
                        ),
                    )

        def array(value):
            if not isinstance(value, list):
                raise MarketPayloadError("invalid_catalog_array")
            return value

        work = [
            asyncio.create_task(self.read(path, (), array, use_cache=False))
            for path in (WORLDS_PATH, DCS_PATH)
        ]
        try:
            worlds, dcs = await asyncio.gather(*work)
            catalog = parse_catalog(worlds[0], dcs[0])
            self.remaining()
            if self.cache is not None:
                payload = {
                    "version": SOURCE_VERSION,
                    "urls": list(urls),
                    "fetched_at_ms": [
                        int(value[1].fetched_at.timestamp() * 1000)
                        for value in (worlds, dcs)
                    ],
                    "worlds": [
                        {"id": row["id"], "name": row["name"]} for row in worlds[0]
                    ],
                    "dcs": [
                        {key: row[key] for key in ("name", "region", "worlds")}
                        for row in dcs[0]
                    ],
                }
                try:
                    await self.run(
                        self.cache.put(key, payload, ttl_seconds=DIRECTORY_TTL)
                    )
                except (MarketDeadlineError, SourceHttpError) as exc:
                    if isinstance(exc, SourceHttpError) and exc.code != "timeout":
                        raise
            return CatalogSnapshot(catalog, (worlds[1], dcs[1]))
        finally:
            for task in work:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*work, return_exceptions=True)


class MarketSourceClient:
    """Shared admission gate; start() owns each query's independent deadline."""

    def __init__(
        self,
        http: SourceHttp,
        cache: CacheAccess | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.http, self.cache, self.clock, self.wall_clock = (
            http,
            cache,
            clock,
            wall_clock,
        )
        self._gate = asyncio.Semaphore(2)

    def start(self, *, deadline: float | None = None) -> MarketSession:
        return self.start_bound(self.http, self.cache, deadline=deadline)

    def start_bound(
        self,
        http: SourceHttp,
        cache: CacheAccess | None,
        *,
        deadline: float | None = None,
    ) -> MarketSession:
        """Use freshly bound invocation ports when sharing this gate across calls.

        start() reuses the original binding and its lifetime; formal integration
        must pass each new invocation's HTTP/cache handles to start_bound().
        """
        return MarketSession(
            http,
            cache,
            clock=self.clock,
            wall_clock=self.wall_clock,
            deadline=deadline,
            gate=self._gate,
        )
