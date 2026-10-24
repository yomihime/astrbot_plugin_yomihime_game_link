"""Core-private, invocation-scoped collage resource and finished-page handling."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from hashlib import sha256
from io import BytesIO

from yomihime_game_link_sdk.cards import (
    Asset,
    DenseCollage,
    ResourceImage,
    WeightedCollage,
)
from yomihime_game_link_sdk.errors import CacheUnavailable, ParameterError
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
from .module_services import _BoundCache, _BoundResources

_PAGE_LIMIT = 4_194_304
_TOTAL_LIMIT = 16_777_216
_SOURCE_LIMIT = 33_554_432
_CACHE_TTL = 3600.0
_DERIVED_VERSION = "core-tile-v1"
_PAGE_LIMITS = PngValidationLimits(
    max_bytes=_PAGE_LIMIT, max_pixels=(128 * 1024 * 1024) // 36
)


class RequiredCollageResourceUnavailable(Exception):
    """A required declared image cannot be resolved for this invocation."""


class CollageBudgetExceeded(ValueError):
    """The complete collage cannot fit its declared finite source budget."""

    def __init__(self):
        super().__init__("collage source budget")


def _resize_derivative(content, size, fit):
    """Canonical source-only pixels; shared cache writers call this themselves."""
    from PIL import Image, ImageOps

    width, height = size
    with Image.open(BytesIO(content)) as original:
        if original.width * original.height > 16_777_216:
            raise ValueError("collage image budget")
        with original.convert("RGBA") as rgba:
            transform = ImageOps.fit if fit == "cover" else ImageOps.contain
            with transform(rgba, size, method=Image.Resampling.LANCZOS) as resized:
                with Image.new("RGBA", size, (0, 0, 0, 0)) as canvas:
                    canvas.alpha_composite(
                        resized,
                        ((width - resized.width) // 2, (height - resized.height) // 2),
                    )
                    with BytesIO() as output:
                        canvas.save(output, format="PNG")
                        derivative = output.getvalue()
    validate_png(RenderedBitmap(derivative, width, height))
    return derivative


async def _derived_image(
    scope,
    source,
    snapshot,
    size,
    fit,
    current,
    *,
    may_cache,
    raw_cache,
    visibility=CacheVisibility.USER,
):
    """A source-only derivative; retention never contains selection data.

    Source snapshots are checked before this lookup. Grant-derived bytes are
    invocation-only. PNG bytes are fully validated even on derivative hits.
    """
    width, height = size
    public_shared = (
        may_cache
        and source.visibility is CacheVisibility.PUBLIC
        and type(scope.cache) is _BoundCache
    )
    if public_shared:
        visibility = CacheVisibility.PUBLIC
    if (
        visibility is CacheVisibility.PUBLIC
        and source.visibility is not CacheVisibility.PUBLIC
    ):
        raise ValueError("private source cannot enter public derivative cache")
    key = (
        "collage-tile-"
        + sha256(
            f"{_DERIVED_VERSION}:{snapshot.digest}:{width}:{height}:{fit}".encode()
        ).hexdigest()
    )
    if may_cache:
        try:
            found = (
                await scope.cache._collage_public_lookup(
                    scope.resources, snapshot, size, fit
                )
                if public_shared
                else await scope.cache.lookup(CacheQuery(key, visibility))
            )
            await current()
            if found.status is CacheLookupStatus.HIT and found.entry is not None:
                payload = found.entry.payload
                if (
                    isinstance(payload, Mapping)
                    and payload.get("version") == _DERIVED_VERSION
                    and type(payload.get("id")) is str
                    and type(payload.get("sha256")) is str
                ):
                    content = await scope.resources.read(
                        payload["id"], visibility=visibility
                    )
                    await current()
                    if sha256(content).hexdigest() == payload["sha256"]:
                        validate_png(RenderedBitmap(content, width, height))
                        return Asset("png", content, source.alt, source.required)
        except (CacheUnavailable, FileNotFoundError, PngValidationError):
            await current()
    source_key = source.visibility, source.asset_id
    if source_key not in raw_cache:
        raw_cache[source_key] = await scope.resources._collage_read(
            snapshot, source.visibility
        )
        await current()
    content = raw_cache[source_key]
    prepared = None
    try:
        if public_shared:
            prepared = await scope.resources._collage_store_derivative(
                snapshot, size, fit, content
            )
            await current()
            derivative = prepared.content
        else:
            derivative = _resize_derivative(content, size, fit)
    except FileNotFoundError:
        raise
    except (ValueError, OSError, PngValidationError):
        # Existing optional/required decoder semantics remain authoritative.
        return Asset(
            "png" if snapshot.metadata.media_type == "image/png" else "jpeg",
            content,
            source.alt,
            source.required,
        )
    if may_cache:
        try:
            if public_shared:
                if prepared.reference is not None:
                    await scope.cache._collage_public_put(prepared)
            else:
                ref = await scope.resources.store(
                    "image/png",
                    derivative,
                    visibility=visibility,
                    ttl_seconds=7 * 86400,
                )
                await current()
                await scope.cache.put(
                    key,
                    {
                        "version": _DERIVED_VERSION,
                        "id": ref.asset_id,
                        "sha256": sha256(derivative).hexdigest(),
                    },
                    ttl_seconds=7 * 86400,
                    visibility=visibility,
                )
            await current()
        except (CacheUnavailable, ResourceCapacityError, ParameterError):
            await current()
    return Asset("png", derivative, source.alt, source.required)


async def inspect_collage_resources(card, scope, current):
    """Obtain authority-bound version evidence without fully reading hot sources.

    Custom/test resource ports retain their public metadata/read behavior and
    never get trusted private hooks by merely copying their method names.
    """
    if type(scope.resources) is not _BoundResources:
        resolved, versions = await resolve_collage_resources(card, scope, current)
        return resolved, versions, None
    snapshots, versions = {}, []
    retained_bytes = 0
    references = tuple(
        dict.fromkeys(
            (item.image.asset_id, item.image.visibility)
            for item in card.blocks[0].items
            if type(item.image) is ResourceImage
        )
    )
    try:
        portfolio = (
            await scope.resources._collage_snapshots(references) if references else {}
        )
    except ParameterError as exc:
        if str(exc) == "collage source budget":
            raise CollageBudgetExceeded() from None
        raise
    await current()
    for item in card.blocks[0].items:
        source = item.image
        if type(source) is not ResourceImage:
            continue
        key = source.visibility, source.asset_id
        if key not in snapshots:
            try:
                snapshot = portfolio[key]
                if (
                    snapshot is None
                    or snapshot.metadata.media_type not in ("image/png", "image/jpeg")
                    or not 0 < snapshot.metadata.size_bytes <= 1_048_576
                ):
                    snapshot = None
            except (FileNotFoundError, ValueError):
                snapshot = None
            snapshots[key] = snapshot
            if snapshot is not None and snapshot.content is not None:
                retained_bytes += len(snapshot.content)
                if retained_bytes > _SOURCE_LIMIT:
                    raise CollageBudgetExceeded()
        snapshot = snapshots[key]
        if snapshot is None:
            if source.required:
                raise RequiredCollageResourceUnavailable()
            versions.append((source.asset_id, source.visibility.value, "missing"))
        else:
            versions.append(
                (
                    source.asset_id,
                    source.visibility.value,
                    snapshot.metadata.revision,
                    snapshot.digest,
                )
            )
    return card, tuple(versions), snapshots


async def collage_snapshots_current(scope, snapshots, current):
    """Recheck source revocation/expiry/version after awaiting finished pages."""
    if snapshots is None:
        return True
    if not snapshots:
        return True
    portfolio = await scope.resources._collage_snapshots(
        tuple((asset_id, visibility) for visibility, asset_id in snapshots)
    )
    await current()
    for (visibility, asset_id), previous in snapshots.items():
        latest = portfolio[visibility, asset_id]
        if (latest is None) != (previous is None):
            return False
        if latest is not None and (
            latest.metadata != previous.metadata
            or latest.digest != previous.digest
            or latest.identity != previous.identity
        ):
            return False
    return True


async def resolve_collage_resources(
    card,
    scope,
    current,
    *,
    snapshots=None,
    may_cache_derivatives=False,
    derivative_visibility=CacheVisibility.USER,
):
    options = dict(
        snapshots=snapshots,
        may_cache_derivatives=may_cache_derivatives,
        derivative_visibility=derivative_visibility,
    )
    if snapshots is not None and type(scope.resources) is _BoundResources:
        async with scope.resources._collage_source_leases(snapshots):
            await current()
            result = await _resolve_collage_resources(card, scope, current, **options)
            if not await collage_snapshots_current(scope, snapshots, current):
                raise FileNotFoundError("collage source changed during rendering")
            await current()
            return result
    return await _resolve_collage_resources(card, scope, current, **options)


async def _resolve_collage_resources(
    card,
    scope,
    current,
    *,
    snapshots=None,
    may_cache_derivatives=False,
    derivative_visibility=CacheVisibility.USER,
):
    """Replace only declared resource references with bounded in-memory assets."""
    collage = card.blocks[0]
    if type(collage) not in (WeightedCollage, DenseCollage):
        raise ValueError("collage declaration is unavailable")
    items = []
    versions = []
    total = 0
    resolved = {}
    derivatives = {}
    raw_cache = {}
    remaining = {}
    for entry in collage.items:
        if type(entry.image) is ResourceImage:
            source_key = entry.image.visibility, entry.image.asset_id
            remaining[source_key] = remaining.get(source_key, 0) + 1
    for item in collage.items:
        source = item.image
        if type(source) is not ResourceImage:
            items.append(item)
            continue
        key = (source.visibility, source.asset_id)
        if (
            type(collage) is DenseCollage
            and snapshots is not None
            and snapshots[key] is not None
        ):
            variant = (
                key,
                item.span,
                collage.cell_width,
                collage.cell_height,
                collage.fit,
            )
            if variant not in derivatives:
                try:
                    derivatives[variant] = await _derived_image(
                        scope,
                        source,
                        snapshots[key],
                        (
                            item.span * collage.cell_width,
                            item.span * collage.cell_height,
                        ),
                        collage.fit,
                        current,
                        may_cache=may_cache_derivatives,
                        raw_cache=raw_cache,
                        visibility=derivative_visibility,
                    )
                except FileNotFoundError:
                    if source.required:
                        raise RequiredCollageResourceUnavailable() from None
                    versions.append(
                        (source.asset_id, source.visibility.value, "missing")
                    )
                    items.append(replace(item, image=None))
                    continue
                if sum(len(body) for body in raw_cache.values()) > _SOURCE_LIMIT:
                    raise CollageBudgetExceeded()
                total += len(derivatives[variant].content)
                if total > _SOURCE_LIMIT:
                    raise CollageBudgetExceeded()
            items.append(
                replace(
                    item,
                    image=replace(
                        derivatives[variant], alt=source.alt, required=source.required
                    ),
                )
            )
            snapshot = snapshots[key]
            versions.append(
                (
                    source.asset_id,
                    source.visibility.value,
                    snapshot.metadata.revision,
                    snapshot.digest,
                )
            )
            remaining[key] -= 1
            if not remaining[key]:
                raw_cache.pop(key, None)
            continue
        if key not in resolved:
            try:
                resources = scope.resources
                if type(resources) is _BoundResources:
                    if snapshots is None:
                        metadata, content = await resources._read_collage_reference(
                            source.asset_id, source.visibility
                        )
                    else:
                        snapshot = snapshots[key]
                        if snapshot is None:
                            raise FileNotFoundError("resource is unavailable")
                        metadata = snapshot.metadata
                        content = await resources._collage_read(
                            snapshot, source.visibility
                        )
                    await current()
                else:
                    metadata = await resources.metadata(
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
                if type(resources) is not _BoundResources:
                    content = await resources.read(
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
            raise CollageBudgetExceeded()
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
    from yomihime_game_link_sdk.services import ResourceCacheClass

    """Publish a USER manifest only after every page is stored successfully."""
    pages = []
    for bitmap in bitmaps:
        try:
            reference = await scope.resources.store(
                "image/png",
                bitmap.content,
                visibility=CacheVisibility.USER,
                ttl_seconds=_CACHE_TTL,
                cache_class=ResourceCacheClass.OUTPUT,
            )
            await current()
        except (CacheUnavailable, ResourceCapacityError, ParameterError):
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
