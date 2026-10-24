"""Bounded lifecycle for newly managed binary resources.

One repository-owned coordinator serializes allocations and eviction decisions.
Readers hold leases while copying bytes. SQL is the inventory: no per-call
filesystem scans, and no pre-existing/legacy asset enters the eviction set.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from yomihime_game_link_sdk.errors import ParameterError
from yomihime_game_link_sdk.services import ResourceCacheClass, ResourceCacheStats
from yomihime_game_link_sdk.storage import OwnerScope, OwnershipKind, ResourceMetadata


@dataclass(frozen=True, slots=True)
class ResourceCachePolicy:
    max_file_bytes: int = 4 * 1024 * 1024
    max_bytes: int = 64 * 1024 * 1024
    max_items: int = 512
    ttl_seconds: float = 7 * 86400
    max_ttl_seconds: float = 30 * 86400
    cleanup_batch: int = 32
    raw_reserved_fraction: float = 0.75

    def __post_init__(self):
        for value in (
            self.max_file_bytes,
            self.max_bytes,
            self.max_items,
            self.cleanup_batch,
        ):
            if type(value) is not int or value <= 0:
                raise ValueError("resource policy bounds must be positive integers")
        if self.max_file_bytes > self.max_bytes:
            raise ValueError("resource file bound exceeds partition capacity")
        for value in (self.ttl_seconds, self.max_ttl_seconds):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError("resource policy expiry must be finite and positive")
        if self.ttl_seconds > self.max_ttl_seconds:
            raise ValueError("resource default expiry exceeds maximum")
        if (
            type(self.raw_reserved_fraction) not in (float, int)
            or not 0 <= self.raw_reserved_fraction <= 1
        ):
            raise ValueError("resource raw reservation is invalid")


class ResourceCapacityError(RuntimeError):
    """A finite cache cannot admit bytes without disrupting leased resources."""


@dataclass(frozen=True, slots=True)
class ResourceIntegritySnapshot:
    metadata: ResourceMetadata
    digest: str
    identity: tuple | None
    content: bytes | None = None


async def drain_on_cancel(operation: Awaitable):
    """Keep accepted mutations owned and ordered until they settle."""
    task = asyncio.create_task(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        # Retrieve any error without replacing caller cancellation.
        if task.done() and not task.cancelled():
            task.exception()
        raise


class ManagedResourceCache:
    def __init__(self, repository, *, policy: ResourceCachePolicy | None = None):
        self.repository = repository
        self.files = repository.file_store
        self.policy = policy or ResourceCachePolicy()
        self._initial_policy = self.policy
        self._public_policy = (
            ResourceCachePolicy(max_bytes=512 * 1024 * 1024, max_items=8192)
            if policy is None
            else self.policy
        )
        self._lock = asyncio.Lock()
        self._readers: dict[tuple[str, str], int] = {}

    def _policy(self, scope):
        # Explicit test/deployment policy replacements remain authoritative.
        return (
            self._public_policy
            if scope.kind is OwnershipKind.PUBLIC
            and self.policy is self._initial_policy
            else self.policy
        )

    def _key(self, asset_id: str, scope: OwnerScope) -> tuple[str, str]:
        return self.repository.managed_scope_key(scope), asset_id

    @asynccontextmanager
    async def lease(self, asset_id: str, scope: OwnerScope) -> AsyncIterator[None]:
        key = self._key(asset_id, scope)
        async with self._lock:
            self._readers[key] = self._readers.get(key, 0) + 1
        try:
            yield
        finally:
            # No await: event-loop atomic release is safe even under cancellation.
            remaining = self._readers[key] - 1
            if remaining:
                self._readers[key] = remaining
            else:
                self._readers.pop(key)

    @asynccontextmanager
    async def lease_many(self, references):
        """Pin one finite source portfolio with one awaited lock boundary."""
        keys = tuple(
            dict.fromkeys(self._key(asset_id, scope) for asset_id, scope in references)
        )
        async with self._lock:
            for key in keys:
                self._readers[key] = self._readers.get(key, 0) + 1
        try:
            yield
        finally:
            for key in keys:
                remaining = self._readers[key] - 1
                if remaining:
                    self._readers[key] = remaining
                else:
                    self._readers.pop(key)

    async def read(self, asset_id: str, scope: OwnerScope, *, read=None) -> bytes:
        async with self.lease(asset_id, scope):
            entry = await self.repository.managed_entry(asset_id, scope)
            if entry is not None and entry[4]:
                raise FileNotFoundError("resource is unavailable")
            metadata = await self.repository.metadata(asset_id, scope)
            if metadata is None:
                raise FileNotFoundError("resource is unavailable")
            try:
                content = await (read or self.repository.read)(asset_id, scope)
            except (FileNotFoundError, ValueError):
                raise FileNotFoundError("resource is unavailable") from None
            if (
                type(content) is not bytes
                or len(content) != metadata.size_bytes
                or self.files.content_id(content, scope) != asset_id
            ):
                raise FileNotFoundError("resource is unavailable")
            if entry is not None:
                if hashlib.sha256(content).hexdigest() != entry[0]:
                    raise FileNotFoundError("resource is unavailable")
                await self.repository.managed_touch(asset_id, scope, datetime.now(UTC))
            return content

    async def snapshot(self, asset_id: str, scope: OwnerScope):
        """Validate live index and file version before reusing verified digest."""
        async with self.lease(asset_id, scope):
            metadata = await self.repository.metadata(asset_id, scope)
            entry = await self.repository.managed_entry(asset_id, scope)
            if metadata is None or (
                entry is not None
                and (entry[4] or datetime.fromisoformat(entry[2]) <= datetime.now(UTC))
            ):
                raise FileNotFoundError("resource is unavailable")
            identity = await self.files.identity(asset_id, scope)
            saved = await self.repository.integrity_snapshot(asset_id, scope)
            if (
                identity is not None
                and saved is not None
                and saved[0] == metadata.revision
                and saved[2] == identity
                and (entry is None or saved[1] == entry[0])
            ):
                if entry is not None:
                    await self.repository.managed_touch(
                        asset_id, scope, datetime.now(UTC)
                    )
                return ResourceIntegritySnapshot(metadata, saved[1], identity)
            content = await self.read(asset_id, scope)
            after = await self.files.identity(asset_id, scope)
            latest = await self.repository.metadata(asset_id, scope)
            if identity != after or latest != metadata:
                raise FileNotFoundError("resource changed")
            digest = hashlib.sha256(content).hexdigest()
            if identity is not None:
                await self.repository.remember_integrity(
                    asset_id, scope, metadata.revision, digest, identity
                )
            return ResourceIntegritySnapshot(metadata, digest, identity, content)

    async def snapshots(self, asset_ids, scope, *, retained_budget=None):
        """One authority-bound portfolio; changed versions use the full verifier."""
        retained_budget = (
            retained_budget if retained_budget is not None else {"bytes": 0}
        )
        async with AsyncExitStack() as readers:
            for asset_id in asset_ids:
                await readers.enter_async_context(self.lease(asset_id, scope))
            portfolio = await self.repository.integrity_portfolio(asset_ids, scope)
            result = {}
            now = datetime.now(UTC)
            for asset_id in asset_ids:
                record = portfolio.get(asset_id)
                result[asset_id] = None
                if record is None:
                    continue
                metadata, entry, saved = record
                if (
                    metadata.media_type not in ("image/png", "image/jpeg")
                    or not 0 < metadata.size_bytes <= 1_048_576
                ):
                    continue
                if (metadata.expires_at is not None and metadata.expires_at <= now) or (
                    entry is not None
                    and (entry[4] or datetime.fromisoformat(entry[2]) <= now)
                ):
                    continue
                try:
                    identity = await self.files.identity(asset_id, scope)
                    if (
                        identity is not None
                        and saved is not None
                        and saved[0] == metadata.revision
                        and saved[2] == identity
                        and (entry is None or saved[1] == entry[0])
                    ):
                        result[asset_id] = ResourceIntegritySnapshot(
                            metadata, saved[1], identity
                        )
                    else:
                        result[asset_id] = await self.snapshot(asset_id, scope)
                        if result[asset_id].content is not None:
                            retained_budget["bytes"] += len(result[asset_id].content)
                            if retained_budget["bytes"] > 33_554_432:
                                raise ParameterError("collage source budget")
                except FileNotFoundError:
                    continue
            await self.repository.managed_touch_many(
                tuple(
                    asset_id
                    for asset_id, snapshot in result.items()
                    if snapshot is not None
                ),
                scope,
                datetime.now(UTC),
            )
            return result

    async def read_snapshot(self, snapshot: ResourceIntegritySnapshot):
        metadata = snapshot.metadata
        asset_id, scope = metadata.asset_id, metadata.scope
        async with self.lease(asset_id, scope):
            latest = await self.repository.metadata(asset_id, scope)
            entry = await self.repository.managed_entry(asset_id, scope)
            if latest != metadata or (
                entry is not None and (entry[4] or entry[0] != snapshot.digest)
            ):
                raise FileNotFoundError("resource changed")
            identity = await self.files.identity(asset_id, scope)
            if identity != snapshot.identity:
                raise FileNotFoundError("resource changed")
            content = snapshot.content
            if content is None:
                content = await self.read(asset_id, scope)
            if (
                hashlib.sha256(content).hexdigest() != snapshot.digest
                or self.files.content_id(content, scope) != asset_id
            ):
                raise FileNotFoundError("resource changed")
            if await self.files.identity(asset_id, scope) != identity:
                raise FileNotFoundError("resource changed")
            if entry is not None:
                await self.repository.managed_touch(asset_id, scope, datetime.now(UTC))
            return content

    async def stats(self, scope: OwnerScope) -> ResourceCacheStats:
        size, count, evictions = await self.repository.managed_usage(scope)
        policy = self._policy(scope)
        return ResourceCacheStats(
            size, count, policy.max_bytes, policy.max_items, evictions
        )

    async def _evict(self, asset_id, scope, check, authorization_deadline):
        if self._key(asset_id, scope) in self._readers:
            return False
        await check()
        marked = await self.repository.managed_mark_evicting(
            asset_id, scope, authorization_deadline=authorization_deadline
        )
        if not marked:
            return False
        await check()
        await self.files.remove_managed(asset_id, scope)
        await check()
        await self.repository.managed_finish_eviction(
            asset_id, scope, authorization_deadline=authorization_deadline
        )
        await check()
        return True

    async def _room(
        self, scope, asset_id, size, *, check, authorization_deadline, now, cache_class
    ):
        policy = self._policy(scope)
        # Bound each indexed query by cleanup_batch and the whole operation by
        # the initial managed partition inventory, rather than one query batch.
        scope_key = self.repository.managed_scope_key(scope)
        excluded = {asset_id} | {
            leased_asset
            for (leased_scope, leased_asset) in self._readers
            if leased_scope == scope_key
        }
        excluded.update(await self.repository.pinned_asset_ids(scope, now))
        used, count, _ = await self.repository.managed_usage(scope)
        inventory_count = count
        current = await self.repository.managed_entry(asset_id, scope)
        delta_size = size - (0 if current is None else current[1])
        delta_count = 1 if current is None else 0

        def fits():
            return (
                used + delta_size <= policy.max_bytes
                and count + delta_count <= policy.max_items
            )

        visited = set()
        # Expiry/tombstones have priority; LRU is only needed for admission.
        for expired_only in (True, False):
            while len(visited) < inventory_count:
                await check()
                raw_bytes, raw_count = await self.repository.managed_class_usage(
                    scope, "raw"
                )
                protect_raw = (
                    not expired_only
                    and cache_class is not ResourceCacheClass.RAW
                    and (
                        raw_bytes
                        <= int(policy.max_bytes * policy.raw_reserved_fraction)
                        or raw_count
                        <= int(policy.max_items * policy.raw_reserved_fraction)
                    )
                )
                candidates = await self.repository.managed_candidates(
                    scope,
                    now=now,
                    limit=min(policy.cleanup_batch, inventory_count - len(visited)),
                    expired_only=expired_only,
                    excluded_asset_ids=tuple(sorted(excluded | visited)),
                    protect_raw=protect_raw,
                )
                fresh = [
                    (candidate, candidate_size)
                    for candidate, candidate_size in candidates
                    if candidate not in excluded and candidate not in visited
                ]
                if not fresh:
                    break
                for candidate, candidate_size in fresh:
                    visited.add(candidate)
                    if await self._evict(
                        candidate, scope, check, authorization_deadline
                    ):
                        used -= candidate_size
                        count -= 1
                    if fits():
                        return
            if fits():
                return
        raise ResourceCapacityError("resource cache capacity is unavailable")

    async def store(
        self,
        media_type: str,
        content: bytes,
        scope: OwnerScope,
        *,
        module_id: str,
        ttl_seconds: float | None,
        check: Callable[[], Awaitable[None]],
        authorization_deadline=None,
        cache_class: ResourceCacheClass = ResourceCacheClass.DEFAULT,
    ) -> ResourceMetadata:
        if type(content) is not bytes:
            raise TypeError("resource content must be bytes")
        if type(cache_class) is not ResourceCacheClass:
            raise ValueError("invalid resource cache class")
        policy = self._policy(scope)
        if len(content) > policy.max_file_bytes:
            raise ValueError("resource exceeds the per-file byte budget")
        ttl = policy.ttl_seconds if ttl_seconds is None else ttl_seconds
        if (
            isinstance(ttl, bool)
            or not isinstance(ttl, (int, float))
            or not math.isfinite(ttl)
            or ttl <= 0
            or ttl > policy.max_ttl_seconds
        ):
            raise ValueError("resource ttl is outside the supported range")
        # Validate before staging; arbitrary media strings must leave no bytes.
        import re

        if type(media_type) is not str or not re.fullmatch(
            r"[a-zA-Z][a-zA-Z0-9!#$&^_.+-]*/[a-zA-Z0-9][a-zA-Z0-9!#$&^_.+-]*",
            media_type,
        ):
            raise ValueError("resource media type is invalid")
        if media_type.lower().startswith("image/"):
            from io import BytesIO

            from PIL import Image

            try:
                with Image.open(BytesIO(content)) as source:
                    if source.width * source.height > 16_777_216:
                        raise ValueError("resource image exceeds the pixel budget")
                    if Image.MIME.get(source.format, "").lower() != media_type.lower():
                        raise ValueError("resource image does not match media type")
                    source.verify()
            except Exception:
                raise ValueError(
                    "resource image is invalid or exceeds its budget"
                ) from None
        async with self._lock:
            await check()
            now = datetime.now(UTC)
            asset_id = self.files.content_id(content, scope)
            current = await self.repository.registration_state(asset_id, scope)
            managed = await self.repository.managed_entry(asset_id, scope)
            if (
                current is not None
                and managed is None
                and current.expires_at is not None
                and current.expires_at <= now
            ):
                raise ValueError(
                    "expired legacy resource must be registered with a new lifetime before storage"
                )
            # Never adopt existing unmanaged rows into retention policy.
            if current is None or managed is not None:
                await self._room(
                    scope,
                    asset_id,
                    len(content),
                    check=check,
                    authorization_deadline=authorization_deadline,
                    now=now,
                    cache_class=cache_class,
                )
            await check()
            valid = False
            if current is not None:
                try:
                    existing = await self.files.read(asset_id, scope)
                    valid = existing == content
                except FileNotFoundError:
                    pass
            metadata = ResourceMetadata(
                asset_id,
                media_type,
                scope,
                len(content),
                now + timedelta(seconds=float(ttl)),
                True,
            )
            stage = None
            committed = False
            try:
                if not valid:
                    stage = await self.files.stage(
                        f"managed_{uuid4().hex}", content, scope
                    )
                    await check()
                    await self.files.replace_managed(stage)
                    committed = True
                await check()
                if current is not None and managed is None:
                    # Reuse/repair never changes an unmanaged asset's lifetime.
                    await self.files.confirm(current)
                    await check()
                    identity = await self.files.identity(asset_id, scope)
                    if identity is not None:
                        await self.repository.remember_integrity(
                            asset_id,
                            scope,
                            current.revision,
                            hashlib.sha256(content).hexdigest(),
                            identity,
                        )
                        await check()
                    return current
                registered = await self.repository.register_managed(
                    metadata,
                    module_id=module_id,
                    content_sha256=hashlib.sha256(content).hexdigest(),
                    now=now,
                    authorization_deadline=authorization_deadline,
                    cache_class=cache_class.value,
                )
                await check()
                identity = await self.files.identity(asset_id, scope)
                if identity is not None:
                    await self.repository.remember_integrity(
                        asset_id,
                        scope,
                        registered.revision,
                        hashlib.sha256(content).hexdigest(),
                        identity,
                    )
                    await check()
                return registered
            finally:
                # Uncertain committed registration keeps its pending marker.
                if stage is not None and not committed:
                    await self.files.discard(stage)
