"""Acceptance tests for the snapshot-backed two-level help catalog."""

from __future__ import annotations

import unittest

from ygl_test_subject.api.display import CommandsBlock, TextBlock
from ygl_test_subject.api.manifests import (
    CapabilityDescriptor,
    CapabilityEffect,
    CommandDescriptor,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
)
from ygl_test_subject.api.services import ModuleHandlers
from ygl_test_subject.api.version import CONTRACT_VERSION
from ygl_test_subject.core.help_catalog import HelpCatalog
from ygl_test_subject.core.registry import Registry


class _Handler:
    def __init__(self) -> None:
        self.calls = 0

    async def invoke(self, context, parameters):  # pragma: no cover - never called
        self.calls += 1
        return None


def _module(
    module_id: str,
    route: str,
    *,
    operation_prefix: str = "查询",
    command_only_operation: str = "绑定",
    handler: _Handler | None = None,
) -> tuple[ModuleManifest, ModuleHandlers, _Handler]:
    query = CapabilityDescriptor(
        "query",
        {"type": "object", "additionalProperties": False},
        InvocationPolicy.NATURAL_LANGUAGE_ALLOWED,
        CapabilityEffect.READ_ONLY,
    )
    account = CapabilityDescriptor(
        "account",
        {"type": "object", "additionalProperties": False},
        InvocationPolicy.COMMAND_ONLY,
        CapabilityEffect.READ_ONLY,
    )
    query_command = CommandDescriptor(operation_prefix, "query", {}, "普通查询")
    account_command = CommandDescriptor(
        command_only_operation, "account", {}, "命令操作"
    )
    module = ModuleManifest(
        module_id,
        route,
        ModuleCategory.GAME,
        "test.module:Factory",
        "1.0.0",
        (query, account),
        (query_command, account_command),
    )
    actual_handler = handler or _Handler()
    return (
        module,
        ModuleHandlers(
            capabilities={"query": actual_handler, "account": actual_handler},
            collectors={},
            evaluators={},
        ),
        actual_handler,
    )


def _package(package_id: str, *modules: ModuleManifest) -> PackageManifest:
    return PackageManifest(
        package_id,
        "1.0.0",
        CONTRACT_VERSION,
        modules,
        "Tests",
        "AGPL-3.0",
        "offline tests",
    )


class HelpCatalogTests(unittest.TestCase):
    def test_hp01_zero_modules_has_explicit_empty_state_and_help_prompt(self) -> None:
        document = HelpCatalog().total(Registry().snapshot())

        self.assertIsInstance(document.ordered_blocks[0], TextBlock)
        text = document.ordered_blocks[0].text
        self.assertIn("没有已注册模块", text)
        self.assertIn("/ygl help", text)
        self.assertIsInstance(document.ordered_blocks[1], CommandsBlock)

    def test_hp02_total_help_filters_natural_language_commands(self) -> None:
        module, handlers, _ = _module("first", "zeta")
        registry = Registry()
        registry.register_package(_package("pkg", module), {"first": handlers})

        document = HelpCatalog().total(registry.snapshot())
        commands = document.ordered_blocks[1].commands
        self.assertIn("/ygl zeta help", commands)
        self.assertIn("/ygl zeta 绑定", commands)
        self.assertNotIn("/ygl zeta 查询", commands)

    def test_hp03_module_help_lists_all_commands_and_disabled_state(self) -> None:
        module, handlers, _ = _module("first", "zeta")
        registry = Registry()
        registry.register_package(_package("pkg", module), {"first": handlers})

        document = HelpCatalog().module(registry.snapshot(), "zeta")
        intro = document.ordered_blocks[0]
        commands = document.ordered_blocks[1]
        self.assertIsInstance(intro, TextBlock)
        self.assertIn("未启用", intro.text)
        self.assertIsInstance(commands, CommandsBlock)
        self.assertIn("/ygl zeta 查询", commands.commands[0])
        self.assertIn("/ygl zeta 绑定", commands.commands[1])
        self.assertIn("允许自然语言", commands.commands[0])
        self.assertIn("必须通过命令", commands.commands[1])

    def test_hp04_unknown_route_is_friendly_and_same_operation_names_are_scoped(
        self,
    ) -> None:
        first, first_handlers, _ = _module("first", "alpha")
        second, second_handlers, _ = _module("second", "beta")
        registry = Registry()
        registry.register_package(
            _package("pkg", first, second),
            {"first": first_handlers, "second": second_handlers},
        )
        catalog = HelpCatalog()

        unknown = catalog.module(registry.snapshot(), "missing")
        self.assertIn("未找到模块路由", unknown.ordered_blocks[0].text)
        self.assertEqual(("/ygl help",), unknown.ordered_blocks[1].commands)

        alpha = catalog.module(registry.snapshot(), "alpha")
        beta = catalog.module(registry.snapshot(), "beta")
        self.assertTrue(
            all(
                command.startswith("/ygl alpha ")
                for command in alpha.ordered_blocks[1].commands
            )
        )
        self.assertTrue(
            all(
                command.startswith("/ygl beta ")
                for command in beta.ordered_blocks[1].commands
            )
        )

    def test_hp05_help_uses_one_snapshot_and_never_calls_handlers(self) -> None:
        handler = _Handler()
        module, handlers, _ = _module("first", "alpha", handler=handler)
        registry = Registry()
        registry.register_package(_package("pkg", module), {"first": handlers})
        disabled = registry.snapshot()
        catalog = HelpCatalog()

        document = catalog.module(disabled, "alpha")
        registry.set_enabled("pkg/first", True)
        old_document = catalog.module(disabled, "alpha")
        new_document = catalog.module(registry.snapshot(), "alpha")

        self.assertEqual(
            "未启用",
            old_document.ordered_blocks[0].text.split("状态：")[1].split("\n")[0],
        )
        self.assertIn("已启用", new_document.ordered_blocks[0].text)
        self.assertIn("未启用", document.ordered_blocks[0].text)
        self.assertEqual(handler.calls, 0)


if __name__ == "__main__":
    unittest.main()
