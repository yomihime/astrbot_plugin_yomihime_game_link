from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Star, register


@register(
    "astrbot_plugin_yomihime_game_link",
    "yomihime",
    "怜的游戏连结：聚合游戏角色、战绩与资讯。",
    "0.1.0",
)
class YomihimeGameLink(Star):
    """Provide the entry point for the game information plugin."""

    @filter.command("ygl")
    async def game_link(self, event: AstrMessageEvent):
        """Show the plugin introduction and current development status.

        Args:
            event: The incoming command event.
        """
        yield event.plain_result(
            "Yomihime Game Link · 怜的游戏连结\n"
            "连接你的游戏世界。\n\n"
            "插件骨架已就绪。\n"
            "Dota2 战绩、FF14 Logs 与账号绑定功能正在规划中。"
        )
