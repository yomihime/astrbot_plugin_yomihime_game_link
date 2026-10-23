"""Core-private, invocation-scoped collage resource and finished-page handling."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from hashlib import sha256

from yomihime_game_link_sdk.cards import Asset, ResourceImage, WeightedCollage
from yomihime_game_link_sdk.errors import CacheUnavailable
from yomihime_game_link_sdk.storage import (
    CacheLookupStatus,
    CacheQuery,
    CacheVisibility,
)

from ..presentation.card_bitmap import RenderedBitmap
from ..presentation.png_validation import (
    PngValidationError,
    PngValidationLimits,
    validate_png,
)
from .managed_resources import ResourceCapacityError

_PAGE_LIMIT = 4_194_304
_TOTAL_LIMIT = 16_777_216
_SOURCE_LIMIT = 33_554_432
_CACHE_TTL = 3600.0
_PAGE_LIMITS = PngValidationLimits(
    max_bytes=_PAGE_LIMIT, max_pixels=(128 * 1024 * 1024) // 36
)


class RequiredCollageResourceUnavailable(Exception):
    """A required declared image cannot be resolved for this invocation."""


async def resolve_collage_resources(card, scope, current):
    """Replace only declared resource references with bounded in-memory assets."""
    collage = card.blocks[0]
    if type(collage) is not WeightedCollage:
        raise ValueError("collage declaration is unavailable")
    items = []
    versions = []
    total = 0
    resolved = {}
    for item in collage.items:
        source = item.image
        if type(source) is not ResourceImage:
            items.append(item)
            continue
        key = (source.visibility, source.asset_id)
        if key not in resolved:
            try:
                metadata = await scope.resources.metadata(
                    source.asset_id, visibility=source.visibility
                )
                await current()
                if metadata is None:
                    raise FileNotFoundError("resource is unavailable")
                media = metadata.media_type
                if media not in ("image/png", "image/jpeg"):
                    raise ValueError("unsupported collage resource")
                if not 0 < metadata.size_bytes <= 1_048_576:
                    raise ValueError("collage source budget")
                content = await scope.resources.read(
                    source.asset_id, visibility=source.visibility
                )
                await current()
                if len(content) != metadata.size_bytes:
                    raise ValueError("collage resource changed")
            except (FileNotFoundError, ValueError):
                resolved[key] = None
            else:
                resolved[key] = (
                    media,
                    content,
                    metadata.revision,
                    sha256(content).hexdigest(),
                )
        snapshot = resolved[key]
        if snapshot is None:
            if source.required:
                raise RequiredCollageResourceUnavailable() from None
            items.append(replace(item, image=None))
            versions.append((source.asset_id, source.visibility.value, "missing"))
            continue
        media, content, revision, digest = snapshot
        total += len(content)
        if total > _SOURCE_LIMIT:
            raise ValueError("collage source budget")
        versions.append(
            (
                source.asset_id,
                source.visibility.value,
                revision,
                digest,
            )
        )
        items.append(
            replace(
                item,
                image=Asset(
                    "png" if media == "image/png" else "jpeg",
                    content,
                    source.alt,
                    source.required,
                ),
            )
        )
    return replace(card, blocks=(replace(collage, items=tuple(items)),)), tuple(
        versions
    )


async def load_finished_pages(scope, key: str, current):
    """Read USER bytes only; bad/missing manifest or page is a cache miss."""
    try:
        found = await scope.cache.lookup(CacheQuery(key, CacheVisibility.USER))
        await current()
    except CacheUnavailable:
        await current()
        return None
    if found.status is not CacheLookupStatus.HIT or found.entry is None:
        return None
    payload = found.entry.payload
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema") != 1
        or payload.get("key") != key
    ):
        return None
    pages = payload.get("pages")
    if type(pages) not in (list, tuple) or not 1 <= len(pages) <= 8:
        return None
    bitmaps = []
    total = 0
    for page in pages:
        if not isinstance(page, Mapping) or set(page) != {
            "id",
            "width",
            "height",
            "sha256",
        }:
            return None
        asset_id, width, height, digest = (
            page["id"],
            page["width"],
            page["height"],
            page["sha256"],
        )
        if (
            type(asset_id) is not str
            or type(width) is not int
            or type(height) is not int
            or type(digest) is not str
            or len(digest) != 64
        ):
            return None
        try:
            content = await scope.resources.read(
                asset_id, visibility=CacheVisibility.USER
            )
            await current()
        except FileNotFoundError:
            await current()
            return None
        total += len(content)
        if total > _TOTAL_LIMIT or sha256(content).hexdigest() != digest:
            return None
        try:
            bitmap = RenderedBitmap(content, width, height)
            validate_png(bitmap, limits=_PAGE_LIMITS, collage_profile=True)
        except (ValueError, TypeError, PngValidationError):
            return None
        bitmaps.append(bitmap)
    return tuple(bitmaps)


async def store_finished_pages(scope, key: str, bitmaps, current) -> None:
    """Publish a USER manifest only after every page is stored successfully."""
    pages = []
    for bitmap in bitmaps:
        try:
            reference = await scope.resources.store(
                "image/png",
                bitmap.content,
                visibility=CacheVisibility.USER,
                ttl_seconds=_CACHE_TTL,
            )
            await current()
        except (CacheUnavailable, ResourceCapacityError):
            await current()
            return
        pages.append(
            {
                "id": reference.asset_id,
                "width": bitmap.width,
                "height": bitmap.height,
                "sha256": sha256(bitmap.content).hexdigest(),
            }
        )
    try:
        await scope.cache.put(
            key,
            {"schema": 1, "key": key, "pages": pages},
            ttl_seconds=_CACHE_TTL,
            visibility=CacheVisibility.USER,
        )
        await current()
    except CacheUnavailable:
        await current()
        return
