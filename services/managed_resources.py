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
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from yomihime_game_link_sdk.services import ResourceCacheStats
from yomihime_game_link_sdk.storage import OwnerScope, ResourceMetadata


@dataclass(frozen=True, slots=True)
class ResourceCachePolicy:
    max_file_bytes: int = 4 * 1024 * 1024
    max_bytes: int = 64 * 1024 * 1024
    max_items: int = 512
    ttl_seconds: float = 7 * 86400
    max_ttl_seconds: float = 30 * 86400
    cleanup_batch: int = 32

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


class ResourceCapacityError(RuntimeError):
    """A finite cache cannot admit bytes without disrupting leased resources."""


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
        self._lock = asyncio.Lock()
        self._readers: dict[tuple[str, str], int] = {}

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

    async def stats(self, scope: OwnerScope) -> ResourceCacheStats:
        size, count, evictions = await self.repository.managed_usage(scope)
        return ResourceCacheStats(
            size, count, self.policy.max_bytes, self.policy.max_items, evictions
        )

    async def _evict(self, asset_id, scope, check, authorization_deadline):
        if self._key(asset_id, scope) in self._readers:
            return False
        await check()
        await self.repository.managed_mark_evicting(
            asset_id, scope, authorization_deadline=authorization_deadline
        )
        await check()
        await self.files.remove_managed(asset_id, scope)
        await check()
        await self.repository.managed_finish_eviction(
            asset_id, scope, authorization_deadline=authorization_deadline
        )
        await check()
        return True

    async def _room(self, scope, asset_id, size, *, check, authorization_deadline, now):
        # All tombstones and expired candidates are SQL-index selected, bounded.
        scope_key = self.repository.managed_scope_key(scope)
        excluded = {asset_id} | {
            leased_asset
            for (leased_scope, leased_asset) in self._readers
            if leased_scope == scope_key
        }
        expired = await self.repository.managed_candidates(
            scope,
            now=now,
            limit=self.policy.cleanup_batch,
            expired_only=True,
            excluded_asset_ids=tuple(sorted(excluded)),
        )
        visited = set()
        for candidate, _ in expired:
            visited.add(candidate)
            if candidate != asset_id:
                await self._evict(candidate, scope, check, authorization_deadline)
        used, count, _ = await self.repository.managed_usage(scope)
        current = await self.repository.managed_entry(asset_id, scope)
        delta_size = size - (0 if current is None else current[1])
        delta_count = 1 if current is None else 0
        if (
            used + delta_size <= self.policy.max_bytes
            and count + delta_count <= self.policy.max_items
        ):
            return
        candidates = await self.repository.managed_candidates(
            scope,
            now=now,
            limit=max(0, self.policy.cleanup_batch - len(visited)),
            expired_only=False,
            excluded_asset_ids=tuple(sorted(excluded | visited)),
        )
        for candidate, candidate_size in candidates:
            if candidate == asset_id or candidate in visited:
                continue
            if await self._evict(candidate, scope, check, authorization_deadline):
                used -= candidate_size
                count -= 1
                if (
                    used + delta_size <= self.policy.max_bytes
                    and count + delta_count <= self.policy.max_items
                ):
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
    ) -> ResourceMetadata:
        if type(content) is not bytes:
            raise TypeError("resource content must be bytes")
        if len(content) > self.policy.max_file_bytes:
            raise ValueError("resource exceeds the per-file byte budget")
        ttl = self.policy.ttl_seconds if ttl_seconds is None else ttl_seconds
        if (
            isinstance(ttl, bool)
            or not isinstance(ttl, (int, float))
            or not math.isfinite(ttl)
            or ttl <= 0
            or ttl > self.policy.max_ttl_seconds
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
            # Never adopt existing unmanaged rows into retention policy.
            if current is None or managed is not None:
                await self._room(
                    scope,
                    asset_id,
                    len(content),
                    check=check,
                    authorization_deadline=authorization_deadline,
                    now=now,
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
                    return current
                registered = await self.repository.register_managed(
                    metadata,
                    module_id=module_id,
                    content_sha256=hashlib.sha256(content).hexdigest(),
                    now=now,
                    authorization_deadline=authorization_deadline,
                )
                await check()
                return registered
            finally:
                # Uncertain committed registration keeps its pending marker.
                if stage is not None and not committed:
                    await self.files.discard(stage)
