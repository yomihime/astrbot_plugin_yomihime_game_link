"""Generate the two levels of help from an immutable registry snapshot."""

from __future__ import annotations

from collections.abc import Callable

from ..api.display import CommandsBlock, DisplayDocument, TextBlock
from ..api.manifests import InvocationPolicy, PrivacyFloor
from ..api.services import CapabilityHealth, HealthStatus
from .registry import RegistryError, RegistrySnapshot


class HelpCatalog:
    """Build renderer-neutral help documents from one registry snapshot.

    The catalog deliberately accepts a snapshot instead of a ``Registry``.
    This means a complete help response describes one coherent revision and
    cannot observe a registry update half way through rendering.
    """

    def __init__(
        self,
        health_query: Callable[[str, str], tuple[CapabilityHealth, int]] | None = None,
    ) -> None:
        if health_query is not None and not callable(health_query):
            raise TypeError("health_query must be callable")
        self._health_query = health_query

    def total(self, snapshot: RegistrySnapshot) -> DisplayDocument:
        """Return root help for command-only operations and module routes."""

        self._require_snapshot(snapshot)
        modules = self._sorted_modules(snapshot)
        commands: list[str] = []
        for module in modules:
            route = module.manifest.route
            commands.append(f"/ygl {route} help")
            commands.extend(
                self._command_line(
                    module, command_only=True, health_query=self._health_query
                )
            )

        if modules:
            intro = TextBlock("总帮助：列出必须使用命令的操作，以及各模块的帮助入口。")
            blocks = (intro, CommandsBlock(tuple(commands)))
        else:
            blocks = (
                TextBlock(
                    "当前没有已注册模块。使用 /ygl help 查看帮助；接入模块后，"
                    "这里会显示模块帮助入口。"
                ),
                CommandsBlock((), fallback_text="暂无可用模块命令。"),
            )
        return DisplayDocument("Yomihime Game Link 帮助", "ygl", blocks)

    def module(self, snapshot: RegistrySnapshot, route: str) -> DisplayDocument:
        """Return all declared commands for ``route`` and its status."""

        self._require_snapshot(snapshot)
        if not isinstance(route, str):
            return self._unknown_route_document("未知模块")
        try:
            module = snapshot.module_for_route(route)
        except RegistryError:
            return self._unknown_route_document(route)

        status = "已启用" if module.enabled else "未启用"
        manifest = module.manifest
        intro = TextBlock(
            f"模块：{manifest.route}\n状态：{status}\n"
            "帮助展示不代表当前允许调用；执行时仍会校验模块状态和调用来源。"
        )
        commands = tuple(
            self._command_line(
                module, command_only=False, health_query=self._health_query
            )
        )
        if not commands:
            command_block = CommandsBlock((), fallback_text="该模块没有声明命令。")
        else:
            command_block = CommandsBlock(commands)
        return DisplayDocument(
            f"{manifest.route} 模块帮助", manifest.route, (intro, command_block)
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
    def _command_line(
        module,
        *,
        command_only: bool,
        health_query: Callable[[str, str], tuple[CapabilityHealth, int]] | None,
    ) -> list[str]:
        manifest = module.manifest
        capabilities = {
            capability.capability_id: capability for capability in manifest.capabilities
        }
        lines: list[str] = []
        for command in manifest.commands:
            capability = capabilities[command.capability_id]
            if command_only and capability.invocation_policy not in (
                InvocationPolicy.COMMAND_ONLY,
                InvocationPolicy.COMMAND_AND_PUBLIC_WEB,
            ):
                continue
            command_text = f"/ygl {manifest.route} {command.operation_path}"
            if command_only:
                lines.append(command_text)
                continue
            policy = {
                InvocationPolicy.COMMAND_ONLY: "必须通过命令",
                InvocationPolicy.COMMAND_AND_PUBLIC_WEB: "命令或已授权的公开网页入口",
                InvocationPolicy.NATURAL_LANGUAGE_ALLOWED: "允许自然语言",
            }[capability.invocation_policy]
            privacy = {
                PrivacyFloor.PRIVATE: "需要个人授权",
                PrivacyFloor.OWNER: "仅本人私聊可用",
                PrivacyFloor.PUBLIC: "公开能力",
            }[capability.privacy_floor]
            status = "模块未启用" if not module.enabled else "可用性由执行时状态校验"
            if health_query is not None:
                status = HelpCatalog._health_label(
                    health_query, module.module_id, capability.capability_id
                )
            lines.append(
                f"{command_text} — {command.help_text}（{policy}；{privacy}；{status}）"
            )
        return lines

    @staticmethod
    def _health_label(health_query, module_id: str, capability_id: str) -> str:
        try:
            health, revision = health_query(module_id, capability_id)
            if (
                not isinstance(health, CapabilityHealth)
                or isinstance(revision, bool)
                or not isinstance(revision, int)
                or revision < 0
            ):
                raise ValueError
        except Exception:
            return "状态未知"
        if health.status is HealthStatus.AVAILABLE:
            return "当前可用"
        if health.status is HealthStatus.UNAVAILABLE:
            return "当前不可用"
        return "状态未知"

    @staticmethod
    def _unknown_route_document(route: str) -> DisplayDocument:
        return DisplayDocument(
            "Yomihime Game Link 帮助",
            "ygl",
            (
                TextBlock(
                    f"未找到模块路由：{route}。请使用 /ygl help 查看已注册模块。"
                ),
                CommandsBlock(("/ygl help",)),
            ),
        )
