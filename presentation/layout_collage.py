"""Generic integer shelf planning; no image codec, network or business rules.

Tile areas are assigned after integer partitioning, so rounding cannot invert
weight ordering. Target weights cap at 16:1; shelf width weights cap at 2:1.
These are display tiers, not an exact proportional-area visualization.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

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
LAYOUT_VERSION = "integer-shelves-v1"


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
        """Exact global weight ranks on this physical layout page.

        Integer-area assignment can put nonadjacent ranks on one page; a
        min/max label would then falsely claim the omitted ranks are present.
        """
        ranks = sorted(tile.rank for tile in self.tiles)
        ranges = []
        for rank in ranks:
            if ranges and rank == ranges[-1][1] + 1:
                ranges[-1] = (ranges[-1][0], rank)
            else:
                ranges.append((rank, rank))
        return tuple(ranges)


def plan_collage(node: WeightedCollage, width: int) -> tuple[CollagePage, ...]:
    """Partition an already-admitted collage with global integer-area ordering.

    The source-aspect rectangle bound is mathematical. Actual asset ratios are
    checked separately by the existing decoder, which contains sources that
    would exceed the allowed crop. Every source remains proportionally scaled.
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
    peak = ordered[0].weight
    floor = max(ordered[-1].weight / peak, 1 / 16)
    weights = [max(item.weight / peak, floor) / floor for item in ordered]
    unit = width * width / 24 * len(weights) / sum(weights)
    n = len(ordered)
    costs = [math.inf] * (n + 1)
    choices = [None] * n
    costs[n] = 0
    aspect_low = node.preferred_aspect * MIN_COVER_FRACTION
    aspect_high = node.preferred_aspect / MIN_COVER_FRACTION
    for start in range(n - 1, -1, -1):
        for count in range(1, min(6, n - start) + 1):
            local = weights[start : start + count]
            clipped = [max(weight, local[0] / 2) for weight in local]
            total = sum(clipped)
            edges = [round(width * sum(clipped[:i]) / total) for i in range(count + 1)]
            widths = [edges[i + 1] - edges[i] for i in range(count)]
            if min(widths) < 90 or max(widths) > 1024:
                continue
            low = math.ceil(max(widths) / aspect_high)
            high = min(1024, node.max_height, math.floor(min(widths) / aspect_low))
            if low > high or not math.isfinite(costs[start + count]):
                continue
            target = (
                width / node.preferred_aspect if n == 1 else unit * sum(local) / width
            )
            height = max(low, min(high, round(target)))
            fit_cost = sum(
                math.log(w * height / (unit * target_weight)) ** 2
                for w, target_weight in zip(widths, local)
            )
            aspect_cost = (
                sum(math.log(w / height / node.preferred_aspect) ** 2 for w in widths)
                * 0.25
            )
            score = fit_cost + aspect_cost + costs[start + count] + 0.01
            if score < costs[start]:
                costs[start] = score
                choices[start] = (count, height, edges)
    rectangles = []
    dimensions = []
    start = page = y = used = 0
    while start < n:
        choice = choices[start]
        if choice is None:
            raise BitmapUnavailable("layout_overflow")
        count, height, edges = choice
        if y and (
            y + height > node.max_height or used + count > MAX_COLLAGE_PAGE_ITEMS
        ):
            dimensions.append((width, y))
            page += 1
            y = used = 0
        rectangles.extend(
            (page, (edges[i], y, edges[i + 1], y + height)) for i in range(count)
        )
        y += height
        used += count
        start += count
    dimensions.append((width, y))
    if len(dimensions) > node.max_pages:
        raise BitmapUnavailable("layout_overflow")
    rectangles.sort(
        key=lambda value: (
            -(value[1][2] - value[1][0]) * (value[1][3] - value[1][1]),
            value,
        )
    )
    tiles = [[] for _ in dimensions]
    source_bytes = [0 for _ in dimensions]
    for rank, (item, (page, box)) in enumerate(zip(ordered, rectangles), 1):
        if type(item.image) is Asset:
            source_bytes[page] += (
                len(item.image.content.encode("utf-8"))
                if type(item.image.content) is str
                else len(item.image.content)
            )
        tiles[page].append(CollageTile(item, box, rank))
    if (
        sum(source_bytes) > MAX_COLLAGE_SOURCE_BYTES
        or max(source_bytes) > MAX_COLLAGE_PAGE_SOURCE_BYTES
    ):
        raise BitmapUnavailable("byte_limit")
    return tuple(
        CollagePage(i + 1, len(dimensions), w, h, tuple(tiles[i]), n)
        for i, (w, h) in enumerate(dimensions)
    )
