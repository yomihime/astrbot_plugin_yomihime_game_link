"""Rank-contiguous integer shelf planning; no codec, network or business rules.

Weights select a few visible area tiers. This is a bounded composition, not an
exact proportional-area chart. Shelf sizes never decrease in rank order, and
integer heights enforce each later shelf's largest area <= the prior shelf's
smallest area. Pages cut only between shelves, so their ranks are continuous.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from yomihime_game_link_sdk.cards import Asset, CollageItem, WeightedCollage

from .card_bitmap import BitmapUnavailable

MAX_COLLAGE_ITEMS = 200
MAX_COLLAGE_PAGES = 8
MAX_COLLAGE_PAGE_ITEMS = 64
MAX_COLLAGE_SOURCE_BYTES = 32 * 1024 * 1024
MAX_COLLAGE_PAGE_SOURCE_BYTES = 8 * 1024 * 1024
MAX_COLLAGE_PNG_BYTES = 4 * 1024 * 1024
MAX_COLLAGE_TOTAL_PNG_BYTES = 16 * 1024 * 1024
MIN_COVER_FRACTION = 2 / 3
LAYOUT_VERSION = "contiguous-tier-shelves-v2"


@dataclass(frozen=True)
class CollageTile:
    item: CollageItem
    box: tuple[int, int, int, int]
    rank: int

    @property
    def area(self):
        x, y, right, bottom = self.box
        return (right - x) * (bottom - y)


@dataclass(frozen=True)
class CollagePage:
    index: int
    count: int
    width: int
    height: int
    tiles: tuple[CollageTile, ...]
    total_items: int

    @property
    def rank_ranges(self) -> tuple[tuple[int, int], ...]:
        """Exact rank segment on this physical layout page."""
        ranks = sorted(tile.rank for tile in self.tiles)
        ranges = []
        for rank in ranks:
            if ranges and rank == ranges[-1][1] + 1:
                ranges[-1] = (ranges[-1][0], rank)
            else:
                ranges.append((rank, rank))
        return tuple(ranges)


@dataclass(frozen=True)
class _Shelf:
    widths: tuple[int, ...]
    low_height: int
    high_height: int
    nominal_height: int
    first_rank: int


def plan_collage(node: WeightedCollage, width: int) -> tuple[CollagePage, ...]:
    """Partition sorted ranks into complete shelves and continuous page ranges.

    Rank is descending weight then key. Dynamic programming selects shelf size
    tiers; weights under 1.4x spread share the same target tier. The row-height
    bound below proves monotonic *actual pixel area*, including rounding and
    page boundaries. Every shelf partitions its full integer content width.
    """
    if (
        type(node) is not WeightedCollage
        or type(width) is not int
        or not 90 <= width <= 2048
        or type(node.items) is not tuple
        or not 1 <= len(node.items) <= MAX_COLLAGE_ITEMS
        or type(node.max_height) is not int
        or not 128 <= node.max_height <= 2800
        or type(node.max_pages) is not int
        or not 1 <= node.max_pages <= MAX_COLLAGE_PAGES
        or type(node.preferred_aspect) not in (int, float)
        or not math.isfinite(node.preferred_aspect)
        or not 0.5 <= node.preferred_aspect <= 2
    ):
        raise BitmapUnavailable("invalid_input")
    keys = set()
    for item in node.items:
        if (
            type(item) is not CollageItem
            or type(item.key) is not str
            or not item.key
            or item.key in keys
            or type(item.weight) not in (int, float)
            or (type(item.weight) is int and item.weight.bit_length() > 1024)
            or not math.isfinite(item.weight)
            or item.weight <= 0
        ):
            raise BitmapUnavailable("invalid_input")
        keys.add(item.key)
    ordered = sorted(node.items, key=lambda item: (-item.weight, item.key))
    n = len(ordered)
    maximum = min(8, width // 90, n)
    if maximum < (1 if n == 1 else 2):
        raise BitmapUnavailable("layout_overflow")
    base = min(maximum, n if n <= 8 else (6 if n <= 64 else 8))
    desired = [base] * n
    peak = ordered[0].weight

    def cap_without_split(cap: int) -> int:
        cap = min(n, cap)
        while cap and cap < n and ordered[cap - 1].weight == ordered[cap].weight:
            cap -= 1
        return cap

    if n > 8:
        if ordered[-1].weight / peak < 0.7:
            for index in range(cap_without_split(max(4, n // 5))):
                if ordered[index].weight / peak >= 0.25:
                    desired[index] = max(2, base - 2)
        if ordered[-1].weight / peak < 1 / 3:
            for index in range(cap_without_split(max(2, n // 20))):
                if ordered[index].weight / peak >= 0.5:
                    desired[index] = max(2, base - 4)

    @lru_cache(None)
    def choose(start: int, previous: int) -> tuple[float, tuple[int, ...]]:
        if start == n:
            return 0, ()
        best = (math.inf, ())
        for count in range(previous, maximum + 1):
            if start + count > n:
                break
            future, rest = choose(start + count, count)
            penalty = sum(
                math.log(count / target) ** 2
                for target in desired[start : start + count]
            )
            candidate = (future + penalty + 0.001, (count,) + rest)
            if candidate[0] < best[0]:
                best = candidate
        return best

    counts = choose(0, 1 if n == 1 else 2)[1]
    if not counts:
        raise BitmapUnavailable("layout_overflow")
    aspect_low = node.preferred_aspect * MIN_COVER_FRACTION
    aspect_high = node.preferred_aspect / MIN_COVER_FRACTION
    shelves: list[_Shelf] = []
    position = 0
    for row_index, count in enumerate(counts):
        local = ordered[position : position + count]
        # Single-row small samples show up to 2:1 width tiers. Later rows use
        # equal widths so repeated extreme splits cannot invert row areas.
        width_weights = (
            [max(item.weight / local[0].weight, 0.5) for item in local]
            if n <= 8 and row_index == 0
            else [1] * count
        )
        total = sum(width_weights)
        edges = [
            round(width * sum(width_weights[:offset]) / total)
            for offset in range(count)
        ] + [width]
        widths = sorted((edges[i + 1] - edges[i] for i in range(count)), reverse=True)
        if min(widths) < 90 and n <= 8:
            edges = [round(width * i / count) for i in range(count)] + [width]
            widths = sorted(
                (edges[i + 1] - edges[i] for i in range(count)), reverse=True
            )
        if min(widths) < 90 or max(widths) > 1024:
            raise BitmapUnavailable("layout_overflow")
        low = math.ceil(max(widths) / aspect_high)
        high = min(1024, node.max_height, math.floor(min(widths) / aspect_low))
        if low > high:
            raise BitmapUnavailable("layout_overflow")
        nominal = max(low, min(high, round((width / count) / node.preferred_aspect)))
        shelves.append(_Shelf(tuple(widths), low, high, nominal, position + 1))
        position += count

    # Backward minimum heights reserve enough area for all following shelves.
    # Integer ceil avoids one-pixel inversions after many uneven-width rows.
    needed = [0] * len(shelves)
    for index in range(len(shelves) - 1, -1, -1):
        shelf = shelves[index]
        required = shelf.low_height
        if index + 1 < len(shelves):
            following = shelves[index + 1]
            numerator = max(following.widths) * needed[index + 1]
            required = max(
                required, (numerator + min(shelf.widths) - 1) // min(shelf.widths)
            )
        if required > shelf.high_height:
            raise BitmapUnavailable("layout_overflow")
        needed[index] = required

    pages: list[list[CollageTile]] = [[]]
    page_heights = [0]
    source_bytes = [0]
    previous_min_area = 0
    for index, shelf in enumerate(shelves):
        height = max(shelf.nominal_height, needed[index])
        if previous_min_area:
            height = min(height, previous_min_area // max(shelf.widths))
        if height < needed[index]:
            raise BitmapUnavailable("layout_overflow")
        page = len(pages) - 1
        if pages[page] and (
            page_heights[page] + height > node.max_height
            or len(pages[page]) + len(shelf.widths) > MAX_COLLAGE_PAGE_ITEMS
        ):
            if len(pages) >= node.max_pages:
                raise BitmapUnavailable("layout_overflow")
            pages.append([])
            page_heights.append(0)
            source_bytes.append(0)
            page += 1
        x = 0
        y = page_heights[page]
        for offset, tile_width in enumerate(shelf.widths):
            rank = shelf.first_rank + offset
            item = ordered[rank - 1]
            pages[page].append(
                CollageTile(item, (x, y, x + tile_width, y + height), rank)
            )
            if type(item.image) is Asset:
                content = item.image.content
                source_bytes[page] += (
                    len(content.encode("utf-8"))
                    if type(content) is str
                    else len(content)
                )
            x += tile_width
        page_heights[page] += height
        previous_min_area = min(shelf.widths) * height
    if (
        sum(source_bytes) > MAX_COLLAGE_SOURCE_BYTES
        or max(source_bytes) > MAX_COLLAGE_PAGE_SOURCE_BYTES
    ):
        raise BitmapUnavailable("byte_limit")
    return tuple(
        CollagePage(index + 1, len(pages), width, page_heights[index], tuple(tiles), n)
        for index, tiles in enumerate(pages)
    )
