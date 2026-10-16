"""Pure item-to-card projection from the same normalized command facts."""

from __future__ import annotations

from yomihime_game_link_sdk import cards

from ..models import AcquisitionKind, AcquisitionRoute, ItemRecord
from ..presentation import ff14_card


def _route_details(route: AcquisitionRoute) -> tuple[str, ...]:
    """Structured costs replace only their exact redundant generated summary."""
    cost_summary = "材料/货币：" + "、".join(
        f"{cost.name or f'物品 ID {cost.item_id}'} × {cost.amount}"
        for cost in route.costs
    )
    return tuple(
        detail
        for detail in route.details
        if not (route.costs and detail == cost_summary)
    )


def _cost_blocks(
    route: AcquisitionRoute, *, show_partial: bool = True
) -> tuple[cards.Block, ...]:
    blocks: list[cards.Block] = []
    for offset in range(0, len(route.costs), 2):
        blocks.append(
            cards.Row(
                tuple(
                    cards.Text(
                        f"{cost.name or f'物品 ID {cost.item_id}'}  × {cost.amount}"
                    )
                    for cost in route.costs[offset : offset + 2]
                )
            )
        )
    if route.partial and show_partial:
        blocks.append(cards.Text("部分信息；来源关联尚不完整", style="caption"))
    return tuple(blocks)


def _route_blocks(route: AcquisitionRoute) -> tuple[cards.Block, ...]:
    details = _route_details(route)
    # Names are already normalized module facts. Display metadata compactly;
    # no source is queried and no text is parsed into new business facts.
    metadata_prefixes = (
        "记录 ID：",
        "区域：",
        "坐标：",
        "职业 ID：",
        "等级：",
        "商店：",
    )
    if details and not details[0].startswith(metadata_prefixes):
        title, remaining = details[0], (route.label, *details[1:])
    else:
        title, remaining = route.label, details
    blocks: list[cards.Block] = [cards.Text(title, style="heading")]
    if remaining:
        blocks.append(cards.Text(" · ".join(remaining), style="caption"))
    blocks.extend(_cost_blocks(route))
    return tuple(blocks)


def build_item_card(record: ItemRecord) -> cards.Document:
    """Preserve all bounded routes/warnings without adding screenshot facts."""
    blocks: list[cards.Block] = [
        cards.PropertyGrid(
            (
                cards.Property("物品 ID", str(record.item_id)),
                cards.Property(
                    "物品等级",
                    str(record.item_level)
                    if record.item_level is not None
                    else "未提供",
                ),
                cards.Property(
                    "装备等级",
                    str(record.equip_level)
                    if record.equip_level is not None
                    else "未提供",
                ),
            ),
            columns=3,
        )
    ]
    if record.description:
        blocks.append(cards.Text(record.description))
    routes = tuple(
        route for route in record.routes if route.kind is not AcquisitionKind.CRAFT
    )
    if routes:
        blocks.append(
            cards.Section(
                "获取途径",
                tuple(block for route in routes for block in _route_blocks(route)),
                subtitle="已记录的有限途径；缺少关联不代表不可获得",
            )
        )
    for route in record.routes:
        if route.kind is AcquisitionKind.CRAFT:
            blocks.append(
                cards.Section(
                    "制作材料",
                    _cost_blocks(
                        route,
                        show_partial=(
                            f"Garland 的{route.label}关联信息不完整。"
                            not in record.warnings
                            or sum(
                                other.kind is AcquisitionKind.CRAFT
                                and other.label == route.label
                                for other in record.routes
                            )
                            != 1
                        ),
                    ),
                    subtitle=" · ".join((route.label, *_route_details(route))),
                )
            )
    if not record.routes:
        blocks.append(
            cards.Section(
                "获取途径",
                (
                    cards.Text(
                        "Garland 当前未提供可解析的获取途径；这不代表物品不可获得。"
                    ),
                ),
            )
        )
    if record.warnings:
        blocks.extend(
            cards.Text(f"来源提示：{warning}", style="caption")
            for warning in record.warnings
        )
    return ff14_card(
        record.name,
        category="物品",
        subtitle="最终幻想 XIV · 物品详情",
        blocks=tuple(blocks),
        sources=("数据源：XIVAPI-compatible (xivcdn) · Garland Tools CN",),
    )


__all__ = ["build_item_card"]
