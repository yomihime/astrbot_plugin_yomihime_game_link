"""Local contract checks for the help-only AstrBot bridge."""

from __future__ import annotations

import unittest

from ygl_test_subject.adapters.astrbot.command_bridge import AstrBotCommandBridge
from ygl_test_subject.core.registry import Registry
from ygl_test_subject.tests.fixtures.minimal_module import build_package


class _FakeEvent:
    def __init__(self, message: object) -> None:
        self.message = message
        # These user-controlled or host-specific fields must never become a
        # Core identity, authorization grant, or command parameter source.
        self.sender_id = "forged-admin"
        self.role = "admin"

    def get_message_str(self) -> object:
        return self.message


class AstrBotCommandBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = Registry()
        self.bridge = AstrBotCommandBridge(self.registry)

    def test_root_and_explicit_help_use_core_zero_module_document(self) -> None:
        root = self.bridge.handle(_FakeEvent("/ygl"))
        explicit = self.bridge.handle(_FakeEvent("  /ygl    help  "))

        self.assertIn("当前没有已注册模块", root)
        self.assertIn("暂无可用模块命令", root)
        self.assertEqual(root, explicit)

    def test_module_and_unknown_route_help_use_help_catalog(self) -> None:
        package, handlers = build_package()
        self.registry.register_package(package, {"demo": handlers})

        known = self.bridge.handle(_FakeEvent("/ygl demo help"))
        unknown = self.bridge.handle(_FakeEvent("/ygl missing help"))

        self.assertIn("demo 模块帮助", known)
        self.assertIn("/ygl demo 查询", known)
        self.assertIn("未找到模块路由：missing", unknown)
        self.assertIn("/ygl help", unknown)

    def test_extra_tokens_quotes_and_non_help_actions_are_rejected(self) -> None:
        for message in (
            "/ygl demo help extra",
            '/ygl "demo route" help',
            "/ygl demo 查询 player",
            "/ygl missing",
        ):
            with self.subTest(message=message):
                self.assertIn("用法：/ygl", self.bridge.handle(_FakeEvent(message)))

    def test_untrusted_event_fields_are_ignored_and_bad_text_fails_closed(self) -> None:
        response = self.bridge.handle(_FakeEvent("/ygl help"))
        self.assertNotIn("forged-admin", response)

        self.assertIn("用法：/ygl", self.bridge.handle(_FakeEvent(None)))
        self.assertIn(
            "用法：/ygl",
            self.bridge.handle(object()),  # type: ignore[arg-type]
        )


if __name__ == "__main__":
    unittest.main()
