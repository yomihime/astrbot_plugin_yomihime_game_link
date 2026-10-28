"""Static registered capabilities, not a live health or credential check."""

from yomihime_game_link_sdk import cards
from yomihime_game_link_sdk.display import DisplayDocument, Privacy, TextBlock

from ..presentation import ff14_card

# Mirrors the manifest's declared collector default, guarded by a contract test.
CALENDAR_DEFAULT_INTERVAL_SECONDS = 900


def build_status_document() -> DisplayDocument:
    capabilities = ("物品查询", "市场查询", "FFLogs 公开角色战绩", "活动日历")
    requirements = (
        ("FFLogs 战绩", "国服、国际服分别配置凭据"),
        ("物品 / 市场 / 日历", "无需 FFLogs 凭据"),
    )
    minutes = CALENDAR_DEFAULT_INTERVAL_SECONDS // 60
    caveat = "以上为模块已接入能力说明，未执行实时健康或凭据检查"
    blocks = (
        cards.Section(
            "已接入能力",
            (
                cards.Table(
                    (cards.Column("能力", 3), cards.Column("接入情况", 1)),
                    tuple((name, "已接入") for name in capabilities),
                ),
            ),
        ),
        cards.Section(
            "使用条件",
            (
                cards.Table(
                    (cards.Column("能力", 2), cards.Column("凭据要求", 3)),
                    requirements,
                ),
            ),
        ),
        cards.Section(
            "日历订阅",
            (
                cards.PropertyGrid(
                    (
                        cards.Property("每日摘要", "需显式订阅"),
                        cards.Property("默认采集间隔", f"{minutes} 分钟"),
                        cards.Property("更新方式", "按采集周期更新"),
                    ),
                    columns=3,
                ),
                cards.Text(
                    "实际送达时间可能受采集与调度周期影响而延迟", style="caption"
                ),
            ),
        ),
        cards.Text(caveat, style="caption"),
    )
    return DisplayDocument(
        title="FF14 模块状态",
        subject="已接入能力与使用条件",
        ordered_blocks=(
            TextBlock("已接入能力：" + "、".join(capabilities)),
            TextBlock(
                "；".join(f"{name}：{condition}" for name, condition in requirements)
            ),
            TextBlock(
                f"日历每日摘要需显式订阅；默认采集间隔为 {minutes} 分钟。"
                "按采集周期更新，实际送达时间可能受采集与调度周期影响而延迟。"
            ),
            TextBlock(caveat),
        ),
        card=ff14_card(
            "模块状态",
            category="状态",
            subtitle="最终幻想 XIV · 状态",
            blocks=blocks,
            layout="table",
        ),
        sources=("FF14 module capabilities",),
        privacy=Privacy.PUBLIC,
    )
