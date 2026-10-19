"""Generate layered help from an immutable registry snapshot."""

from __future__ import annotations

from collections.abc import Callable

from yomihime_game_link_sdk import cards
from yomihime_game_link_sdk.declarations import PrivacyFloor
from yomihime_game_link_sdk.display import CommandsBlock, DisplayDocument, TextBlock
from yomihime_game_link_sdk.services import CapabilityHealth

from .contracts.validation_boundary import validate_contract
from .registry import RegisteredModule, RegistryError, RegistrySnapshot


class HelpCatalog:
    """Build renderer-neutral help documents from one registry snapshot.

    The catalog deliberately accepts a snapshot instead of a ``Registry``.
    This means a complete help response describes one coherent revision and
    cannot observe a registry update half way through rendering.
    """

    def __init__(
        self,
        health_query: Callable[[str, str], tuple[CapabilityHealth, int]] | None = None,
        active_query: Callable[[RegisteredModule], bool] | None = None,
    ) -> None:
        validate_contract(health_query)
        if health_query is not None and not callable(health_query):
            raise TypeError("health_query must be callable")
        self._health_query = health_query
        self._active_query = active_query

    def _visible(self, module) -> bool:
        if not module.enabled:
            return False
        if self._active_query is None:
            return False
        try:
            return self._active_query(module) is True
        except Exception:
            return False

    def total(self, snapshot: RegistrySnapshot) -> DisplayDocument:
        """Return only the active module directory and its help routes."""

        self._require_snapshot(snapshot)
        modules = tuple(
            module for module in self._sorted_modules(snapshot) if self._visible(module)
        )
        commands: list[str] = []
        for module in modules:
            route = module.manifest.route
            commands.append(f"/ygl {route} help")

        if modules:
            summaries = "\n".join(
                f"{self.display_name(module)}（{module.manifest.route}）："
                + self._module_summary(module)
                for module in modules
            )
            intro = validate_contract(
                TextBlock("总帮助：当前活跃模块；使用模块帮助查看能力。\n" + summaries)
            )
            blocks = (intro, validate_contract(CommandsBlock(tuple(commands))))
        else:
            blocks = (
                validate_contract(
                    TextBlock(
                        "当前没有活跃可用模块。使用 /ygl help 查看帮助；"
                        "模块可用后，这里会显示模块帮助入口。"
                    )
                ),
                validate_contract(
                    CommandsBlock((), fallback_text="暂无可用模块命令。")
                ),
            )
        return validate_contract(
            DisplayDocument("Yomihime Game Link 帮助", "ygl", blocks)
        )

    @staticmethod
    def display_name(module: RegisteredModule) -> str:
        """Use declared display metadata, with the declared route as fallback."""

        display = module.manifest.display
        return display.default_name if display is not None else module.manifest.route

    @staticmethod
    def _module_summary(module: RegisteredModule) -> str:
        summaries = tuple(
            command.help_text.splitlines()[0][:24]
            for command in module.manifest.commands[:3]
        )
        if summaries:
            return "；".join(summaries)
        return f"{module.manifest.category.value} 模块，暂无已声明命令。"

    def module(self, snapshot: RegistrySnapshot, route: str) -> DisplayDocument:
        """Return all declared commands for ``route`` and its status."""

        self._require_snapshot(snapshot)
        if not isinstance(route, str):
            return self._unknown_route_document("未知模块")
        try:
            module = snapshot.module_for_route(route)
        except RegistryError:
            return self._unknown_route_document(route)
        if not self._visible(module):
            return self._unknown_route_document(route)

        manifest = module.manifest
        commands = self._children(manifest.commands)
        entries = tuple(self._overview(manifest.route, command) for command in commands)
        hint = "子命令与可选参数：在对应命令后加 help"
        blocks = (
            validate_contract(TextBlock(hint)),
            validate_contract(
                CommandsBlock(
                    tuple(f"{usage}\n{summary}" for usage, summary in entries),
                    fallback_text="该模块没有声明命令。" if not entries else None,
                )
            ),
        )
        card_blocks = tuple(self._compact_card(*entry) for entry in entries)
        return validate_contract(
            DisplayDocument(
                f"{manifest.route} 模块帮助",
                manifest.route,
                blocks,
                card=cards.Document(
                    f"{self.display_name(module)} · 模块帮助",
                    (*card_blocks, cards.Text(hint, style="caption")),
                    layout="table",
                ),
            )
        )

    def command(
        self, snapshot: RegistrySnapshot, route: str, operation_path: str
    ) -> DisplayDocument:
        """Render details only for an active module's exact declared path."""
        self._require_snapshot(snapshot)
        try:
            module = snapshot.module_for_route(route)
        except RegistryError:
            return self._unknown_route_document(route)
        if not self._visible(module):
            return self._unknown_route_document(route)
        command = next(
            (
                item
                for item in module.manifest.commands
                if item.operation_path == operation_path
            ),
            None,
        )
        if command is None:
            return validate_contract(
                DisplayDocument(
                    "命令帮助",
                    route,
                    (
                        validate_contract(
                            TextBlock(
                                f"未找到命令。请使用 /ygl {route} help 查看能力。"
                            )
                        ),
                    ),
                )
            )
        # The module owns wording. Only explicitly declared labels are parsed;
        # legacy prose stays intact, and the overview-only line is not repeated.
        lines = command.help_text.splitlines()
        detail_lines = [
            lines[0],
            *(line for line in lines[1:] if not line.startswith("概览：")),
        ]
        capability = next(
            item
            for item in module.manifest.capabilities
            if item.capability_id == command.capability_id
        )
        if capability.privacy_floor is PrivacyFloor.OWNER:
            detail_lines.append("权限：仅本人私聊可用")
        elif capability.privacy_floor is PrivacyFloor.PRIVATE:
            detail_lines.append("权限：需要个人授权")
        children = self._children(module.manifest.commands, operation_path)
        entries = tuple(self._overview(route, child) for child in children)
        blocks = [validate_contract(TextBlock("\n".join(detail_lines)))]
        card_blocks = [cards.Text(detail_lines[0])]
        for line in detail_lines[1:]:
            label, separator, body = line.partition("：")
            if separator and label in ("用法", "示例", "参数", "限制", "权限"):
                card_blocks.append(cards.Section(label, (cards.Text(body),)))
            else:
                card_blocks.append(cards.Text(line))
        if entries:
            blocks.append(
                validate_contract(
                    CommandsBlock(
                        tuple(
                            f"{usage}\n{summary}\n/ygl {route} {child.operation_path} help"
                            for child, (usage, summary) in zip(children, entries)
                        )
                    )
                )
            )
            card_blocks.append(
                cards.Text("子命令 · 在命令后加 help 查看参数", style="heading")
            )
            card_blocks.extend(self._compact_card(*entry) for entry in entries)
        return validate_contract(
            DisplayDocument(
                f"{route} {operation_path} 帮助",
                route,
                tuple(blocks),
                card=cards.Document(
                    f"{self.display_name(module)} · {operation_path} 帮助",
                    tuple(card_blocks),
                    layout="table",
                ),
            )
        )

    @staticmethod
    def _children(commands, parent=""):
        """Nearest declared prefix wins; undeclared intermediate paths need no metadata."""
        paths = {command.operation_path for command in commands}
        result = []
        for command in commands:
            parts = command.operation_path.split()
            ancestors = [
                " ".join(parts[:i])
                for i in range(1, len(parts))
                if " ".join(parts[:i]) in paths
            ]
            if (ancestors[-1] if ancestors else "") == parent:
                result.append(command)
        return tuple(result)

    @staticmethod
    def _overview(route, command):
        # 概览 is an optional, bounded single-line declaration, never inferred by
        # stripping optional arguments from free-form usage or descriptions.
        prefix = f"/ygl {route} {command.operation_path}"
        usage = next(
            (
                line.removeprefix("概览：").strip()
                for line in command.help_text.splitlines()[1:]
                if line.startswith("概览：")
            ),
            "",
        )
        if len(usage) > 512 or not (usage == prefix or usage.startswith(prefix + " ")):
            usage = prefix + " help"
        return usage, command.help_text.splitlines()[0]

    @staticmethod
    def _compact_card(usage, summary):
        return cards.Section(
            "",
            (
                cards.Text(
                    cards.Inline(
                        (
                            cards.Span(usage + "\n", emphasis="strong"),
                            cards.Span(summary, emphasis="secondary"),
                        )
                    )
                ),
            ),
        )

    @staticmethod
    def _require_snapshot(snapshot: RegistrySnapshot) -> None:
        if not isinstance(snapshot, RegistrySnapshot):
            raise TypeError("snapshot must be a RegistrySnapshot")

    @staticmethod
    def _sorted_modules(snapshot: RegistrySnapshot):
        return tuple(
            sorted(snapshot.modules.values(), key=lambda item: item.manifest.route)
        )

    @staticmethod
    def _unknown_route_document(route: str) -> DisplayDocument:
        return validate_contract(
            DisplayDocument(
                "Yomihime Game Link 帮助",
                "ygl",
                (
                    validate_contract(
                        TextBlock(
                            f"未找到模块路由：{route}。请使用 /ygl help 查看已注册模块。"
                        )
                    ),
                    validate_contract(CommandsBlock(("/ygl help",))),
                ),
            )
        )
