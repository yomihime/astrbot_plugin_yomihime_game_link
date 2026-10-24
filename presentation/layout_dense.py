"""Deterministic bounded integer-grid packing of caller-selected tile spans."""

from __future__ import annotations

from yomihime_game_link_sdk.cards import Asset, DenseCollage, DenseCollageItem

from .card_bitmap import BitmapUnavailable
from .layout_collage import CollagePage, CollageTile

DENSE_LAYOUT_VERSION = "dense-first-fit-v1"
MAX_DENSE_ITEMS = 1000
MAX_DENSE_PAGE_ITEMS = 512


def plan_dense(node: DenseCollage, *, max_height: int | None = None):
    """Pack largest squares first, then backfill holes on earlier pages.

    Input tuple position is logical rank; physical pages may contain disjoint
    rank ranges. A failed budget rejects the plan rather than dropping items.
    Cell dimensions are public output pixels, independent of card margins.
    """
    if type(node) is not DenseCollage:
        raise BitmapUnavailable("invalid_input")
    if (
        type(node.items) is not tuple
        or not 1 <= len(node.items) <= MAX_DENSE_ITEMS
        or type(node.columns) is not int
        or not 8 <= node.columns <= 32
        or type(node.cell_width) is not int
        or not 24 <= node.cell_width <= 96
        or type(node.cell_height) is not int
        or not 24 <= node.cell_height <= 192
        or not 400 <= node.columns * node.cell_width <= 2048
        or type(node.max_height) is not int
        or not 128 <= node.max_height <= 6000
        or type(node.max_pages) is not int
        or not 1 <= node.max_pages <= 8
        or node.fit not in ("cover", "contain")
    ):
        raise BitmapUnavailable("invalid_input")
    keys = set()
    for item in node.items:
        if (
            type(item) is not DenseCollageItem
            or type(item.key) is not str
            or not 0 < len(item.key) <= 128
            or item.key in keys
            or type(item.span) is not int
            or not 1 <= item.span <= min(8, node.columns)
        ):
            raise BitmapUnavailable("invalid_input")
        keys.add(item.key)
    height = node.max_height if max_height is None else min(node.max_height, max_height)
    rows = height // node.cell_height
    if rows < max(item.span for item in node.items):
        raise BitmapUnavailable("layout_overflow")
    occupied: list[list[int]] = []
    pages: list[list[CollageTile]] = []
    source_sizes: list[dict[int, int]] = []
    for rank, item in sorted(
        enumerate(node.items, 1), key=lambda pair: (-pair[1].span, pair[0])
    ):
        k = item.span
        placement = None
        for page_index in range(len(pages) + 1):
            if page_index == len(pages):
                if len(pages) >= node.max_pages:
                    break
                occupied.append([0] * rows)
                pages.append([])
                source_sizes.append({})
            if len(pages[page_index]) >= MAX_DENSE_PAGE_ITEMS:
                continue
            for y in range(rows - k + 1):
                for x in range(node.columns - k + 1):
                    mask = ((1 << k) - 1) << x
                    if all(
                        not occupied[page_index][line] & mask
                        for line in range(y, y + k)
                    ):
                        placement = page_index, x, y, mask
                        break
                if placement is not None:
                    break
            if placement is not None:
                break
        if placement is None:
            raise BitmapUnavailable("layout_overflow")
        page_index, x, y, mask = placement
        for line in range(y, y + k):
            occupied[page_index][line] |= mask
        pages[page_index].append(
            CollageTile(
                item,
                (
                    x * node.cell_width,
                    y * node.cell_height,
                    (x + k) * node.cell_width,
                    (y + k) * node.cell_height,
                ),
                rank,
            )
        )
        if type(item.image) is Asset:
            content = item.image.content
            source_sizes[page_index][id(content)] = (
                len(content.encode("utf-8")) if type(content) is str else len(content)
            )
    if any(sum(page.values()) > 8 * 1024 * 1024 for page in source_sizes):
        raise BitmapUnavailable("byte_limit")
    unique_sources = {key: size for page in source_sizes for key, size in page.items()}
    if sum(unique_sources.values()) > 32 * 1024 * 1024:
        raise BitmapUnavailable("byte_limit")
    return tuple(
        CollagePage(
            index + 1,
            len(pages),
            node.columns * node.cell_width,
            max(tile.box[3] for tile in tiles),
            tuple(tiles),
            len(node.items),
        )
        for index, tiles in enumerate(pages)
    )
