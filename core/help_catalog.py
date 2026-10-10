"""Generate layered help from an immutable registry snapshot."""

from __future__ import annotations

from collections.abc import Callable

from yomihime_game_link_sdk.contexts import InvocationOrigin
from yomihime_game_link_sdk.declarations import InvocationPolicy, PrivacyFloor
from yomihime_game_link_sdk.display import CommandsBlock, DisplayDocument, TextBlock
from yomihime_game_link_sdk.services import CapabilityHealth, HealthStatus

from ..core.contracts.validation_boundary import validate_contract
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
                f"{module.manifest.route}：" + self._module_summary(module)
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
                        "当前没有已注册模块。使用 /ygl help 查看帮助；接入模块后，"
                        "这里会显示模块帮助入口。"
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

        status = "已启用" if module.enabled else "未启用"
        manifest = module.manifest
        intro = validate_contract(
            TextBlock(
                f"模块：{manifest.route}\n状态：{status}\n"
                "在命令路径后加 help 查看参数与例子；执行时仍校验状态和调用来源。"
            )
        )
        commands = tuple(
            self._command_line(
                module, command_only=False, health_query=self._health_query
            )
        )
        if not commands:
            command_block = validate_contract(
                CommandsBlock((), fallback_text="该模块没有声明命令。")
            )
        else:
            command_block = validate_contract(CommandsBlock(commands))
        examples = tuple(
            line
            for command in manifest.commands
            for line in command.help_text.splitlines()[1:]
            if line.startswith("示例：")
        )[:2]
        blocks = (intro, command_block)
        if examples:
            blocks += (validate_contract(TextBlock("\n".join(examples))),)
        return validate_contract(
            DisplayDocument(f"{manifest.route} 模块帮助", manifest.route, blocks)
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
        line = next(
            line
            for line in self._command_line(
                module, command_only=False, health_query=self._health_query
            )
            if line.startswith(f"/ygl {route} {operation_path} help —")
        )
        return validate_contract(
            DisplayDocument(
                f"{route} {operation_path} 帮助",
                route,
                (
                    validate_contract(TextBlock(line)),
                    validate_contract(TextBlock(command.help_text)),
                ),
            )
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
        validate_contract(health_query)
        manifest = module.manifest
        capabilities = {
            capability.capability_id: capability for capability in manifest.capabilities
        }
        lines: list[str] = []
        for command in manifest.commands:
            capability = capabilities[command.capability_id]
            if command_only and (
                InvocationOrigin.COMMAND not in capability.effective_origins
                or (
                    capability.invocation_origins is None
                    and capability.invocation_policy
                    is InvocationPolicy.NATURAL_LANGUAGE_ALLOWED
                )
            ):
                continue
            command_text = f"/ygl {manifest.route} {command.operation_path} help"
            if command_only:
                lines.append(command_text)
                continue
            policy = {
                InvocationPolicy.COMMAND_ONLY: "必须通过命令",
                InvocationPolicy.COMMAND_AND_PUBLIC_WEB: "命令或已授权的公开网页入口",
                InvocationPolicy.NATURAL_LANGUAGE_ALLOWED: "允许自然语言",
            }[capability.invocation_policy]
            if capability.invocation_origins is not None:
                policy = " / ".join(
                    {
                        InvocationOrigin.COMMAND: "命令",
                        InvocationOrigin.WEB_PUBLIC: "已授权公开网页",
                        InvocationOrigin.LLM_TOOL: "普通聊天工具",
                    }[origin]
                    for origin in capability.effective_origins
                )
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
                f"{command_text} — {command.help_text.splitlines()[0]}（{policy}；{privacy}；{status}）"
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
