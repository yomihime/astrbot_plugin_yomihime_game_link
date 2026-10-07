"""Acceptance tests for the snapshot-backed two-level help catalog."""

from __future__ import annotations

import unittest
from dataclasses import replace

from ygl_test_subject.api.contexts import InvocationOrigin
from ygl_test_subject.api.display import CommandsBlock, TextBlock
from ygl_test_subject.api.manifests import (
    CapabilityDescriptor,
    CapabilityEffect,
    CommandDescriptor,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
    PrivacyFloor,
)
from ygl_test_subject.api.services import ModuleHandlers
from ygl_test_subject.api.version import CONTRACT_VERSION
from ygl_test_subject.core.help_catalog import HelpCatalog
from ygl_test_subject.core.registry import Registry as CoreRegistry


class Registry(CoreRegistry):
    """Active declaration fixture; lifecycle behavior is tested separately."""

    def register_package(self, manifest, handlers_by_module):
        super().register_package(manifest, handlers_by_module)
        for module in manifest.modules:
            self.set_enabled(manifest.global_module_id(module.module_id), True)
        return self.snapshot()


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
    def test_explicit_three_origins_keep_command_visible_with_full_entry_labels(self):
        for policy in (
            InvocationPolicy.NATURAL_LANGUAGE_ALLOWED,
            InvocationPolicy.COMMAND_AND_PUBLIC_WEB,
        ):
            with self.subTest(policy=policy):
                module, _, handler = _module("catalog", "catalog")
                query, account = module.capabilities
                query = replace(
                    query,
                    invocation_policy=policy,
                    invocation_origins=(
                        InvocationOrigin.COMMAND,
                        InvocationOrigin.WEB_PUBLIC,
                        InvocationOrigin.LLM_TOOL,
                    ),
                )
                module = replace(module, capabilities=(query, account))
                registry = Registry()
                registry.register_package(
                    _package("explicit", module),
                    {
                        "catalog": ModuleHandlers(
                            {"query": handler, "account": handler}, {}, {}
                        )
                    },
                )
                catalog = HelpCatalog(active_query=lambda module: True)
                total = catalog.total(registry.snapshot()).ordered_blocks[1].commands
                self.assertIn("/ygl catalog 查询", total)
                line = next(
                    line
                    for line in catalog.module(registry.snapshot(), "catalog")
                    .ordered_blocks[1]
                    .commands
                    if line.startswith("/ygl catalog 查询 ")
                )
                self.assertIn("命令 / 已授权公开网页 / 普通聊天工具", line)
                self.assertNotIn("允许自然语言", line)
                self.assertEqual(handler.calls, 0)

    def test_public_web_opt_in_stays_in_command_help_without_natural_language_label(
        self,
    ) -> None:
        module, _, handler = _module("catalog", "catalog")
        web = CapabilityDescriptor(
            "web_read",
            {"type": "object", "additionalProperties": False},
            InvocationPolicy.COMMAND_AND_PUBLIC_WEB,
            CapabilityEffect.READ_ONLY,
        )
        module = replace(
            module,
            capabilities=module.capabilities + (web,),
            commands=module.commands
            + (CommandDescriptor("web", "web_read", {}, "Read"),),
        )
        registry = Registry()
        registry.register_package(
            _package("web-tests", module),
            {
                "catalog": ModuleHandlers(
                    {item.capability_id: handler for item in module.capabilities},
                    {},
                    {},
                )
            },
        )
        catalog = HelpCatalog(active_query=lambda module_id: True)
        total = catalog.total(registry.snapshot()).ordered_blocks[1].commands
        self.assertIn("/ygl catalog web", total)
        self.assertIn("/ygl catalog 绑定", total)
        self.assertNotIn("/ygl catalog 查询", total)
        lines = (
            catalog.module(registry.snapshot(), "catalog").ordered_blocks[1].commands
        )
        web_line = next(line for line in lines if line.startswith("/ygl catalog web "))
        self.assertIn("命令或已授权的公开网页入口", web_line)
        self.assertNotIn("允许自然语言", web_line)
        self.assertIn(
            "必须通过命令",
            next(line for line in lines if line.startswith("/ygl catalog 绑定 ")),
        )
        self.assertIn(
            "允许自然语言",
            next(line for line in lines if line.startswith("/ygl catalog 查询 ")),
        )
        self.assertEqual(handler.calls, 0)

    def test_hp01_zero_modules_has_explicit_empty_state_and_help_prompt(self) -> None:
        document = HelpCatalog(active_query=lambda module_id: True).total(
            Registry().snapshot()
        )

        self.assertIsInstance(document.ordered_blocks[0], TextBlock)
        text = document.ordered_blocks[0].text
        self.assertIn("没有已注册模块", text)
        self.assertIn("/ygl help", text)
        self.assertIsInstance(document.ordered_blocks[1], CommandsBlock)

    def test_hp02_total_help_filters_natural_language_commands(self) -> None:
        module, handlers, _ = _module("first", "zeta")
        registry = Registry()
        registry.register_package(_package("pkg", module), {"first": handlers})

        document = HelpCatalog(active_query=lambda module_id: True).total(
            registry.snapshot()
        )
        commands = document.ordered_blocks[1].commands
        self.assertIn("/ygl zeta help", commands)
        self.assertIn("/ygl zeta 绑定", commands)
        self.assertNotIn("/ygl zeta 查询", commands)

    def test_hp03_module_help_lists_all_commands_for_active_state(self) -> None:
        module, handlers, _ = _module("first", "zeta")
        registry = Registry()
        registry.register_package(_package("pkg", module), {"first": handlers})

        document = HelpCatalog(active_query=lambda module_id: True).module(
            registry.snapshot(), "zeta"
        )
        intro = document.ordered_blocks[0]
        commands = document.ordered_blocks[1]
        self.assertIsInstance(intro, TextBlock)
        self.assertIn("已启用", intro.text)
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
        catalog = HelpCatalog(active_query=lambda module_id: True)

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
        registry.set_enabled("pkg/first", False)
        disabled = registry.snapshot()
        catalog = HelpCatalog(active_query=lambda module_id: True)

        document = catalog.module(disabled, "alpha")
        registry.set_enabled("pkg/first", True)
        old_document = catalog.module(disabled, "alpha")
        new_document = catalog.module(registry.snapshot(), "alpha")

        self.assertIn("未找到模块路由", old_document.ordered_blocks[0].text)
        self.assertIn("已启用", new_document.ordered_blocks[0].text)
        self.assertIn("未找到模块路由", document.ordered_blocks[0].text)
        self.assertEqual(handler.calls, 0)

    def test_hp06_owner_floor_is_described_as_private_direct_use(self) -> None:
        owner = CapabilityDescriptor(
            "owner_read",
            {"type": "object", "additionalProperties": False},
            InvocationPolicy.COMMAND_ONLY,
            CapabilityEffect.READ_ONLY,
            privacy_floor=PrivacyFloor.OWNER,
        )
        handler = _Handler()
        module = ModuleManifest(
            "owner",
            "owner",
            ModuleCategory.PLATFORM,
            "test.module:Factory",
            "1.0.0",
            (owner,),
            (CommandDescriptor("list", "owner_read", {}, "List mine"),),
        )
        registry = Registry()
        registry.register_package(
            _package("owner-tests", module),
            {"owner": ModuleHandlers({"owner_read": handler}, {}, {})},
        )

        document = HelpCatalog(active_query=lambda module_id: True).module(
            registry.snapshot(), "owner"
        )

        self.assertIn("仅本人私聊可用", document.ordered_blocks[1].commands[0])
        self.assertEqual(handler.calls, 0)


if __name__ == "__main__":
    unittest.main()
