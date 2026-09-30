"""Local contract checks for the help-only AstrBot bridge."""

from __future__ import annotations

import unittest

from ygl_test_subject.adapters.astrbot.command_bridge import (
    AstrBotCommandBridge,
    CommandInvocation,
)
from ygl_test_subject.api.manifests import (
    CapabilityDescriptor,
    CapabilityEffect,
    CommandDescriptor,
    InvocationPolicy,
)
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
        self.assertIn("/ygl demo", unknown)

    def test_generic_command_parameters_follow_descriptor_schema(self) -> None:
        package, handlers = build_package()
        self.registry.register_package(package, {"demo": handlers})

        action = self.bridge.parse(_FakeEvent("/ygl demo 查询 alpha"))
        self.assertIsInstance(action, CommandInvocation)
        self.assertEqual(action.module_id, "sample/demo")
        self.assertEqual(action.operation_path, "查询")
        self.assertEqual(dict(action.parameters), {"item": "alpha"})

        multiword = self.bridge.parse(_FakeEvent("/ygl demo 查询 Copper Ore"))
        self.assertIsInstance(multiword, CommandInvocation)
        self.assertEqual(dict(multiword.parameters), {"item": "Copper Ore"})

        # Double quotes group one explicit argument; apostrophes remain text.
        quoted = self.bridge.parse(_FakeEvent('/ygl demo 查询 "demo route"'))
        self.assertIsInstance(quoted, CommandInvocation)
        self.assertEqual(dict(quoted.parameters), {"item": "demo route"})

        operation = next(
            item.operation_path
            for item in package.modules[0].commands
            if item.parameter_mapping
        )
        apostrophe = self.bridge.parse(
            _FakeEvent(f"/ygl demo {operation} Titan's Coffer")
        )
        self.assertIsInstance(apostrophe, CommandInvocation)
        self.assertEqual(dict(apostrophe.parameters), {"item": "Titan's Coffer"})

    def test_invalid_and_help_tokens_keep_text_only_behavior(self) -> None:
        package, handlers = build_package()
        self.registry.register_package(package, {"demo": handlers})

        self.assertIn(
            "/ygl demo", self.bridge.handle(_FakeEvent("/ygl demo help extra"))
        )
        self.assertIn("/ygl", self.bridge.handle(_FakeEvent('/ygl "demo route" help')))
        self.assertIn("/ygl", self.bridge.handle(_FakeEvent("/ygl demo 查询 player")))
        self.assertIn("/ygl demo", self.bridge.handle(_FakeEvent("/ygl missing")))

    def test_untrusted_event_fields_are_ignored_and_bad_text_fails_closed(self) -> None:
        response = self.bridge.handle(_FakeEvent("/ygl help"))
        self.assertNotIn("forged-admin", response)

        self.assertIn("用法：/ygl", self.bridge.handle(_FakeEvent(None)))
        self.assertIn(
            "用法：/ygl",
            self.bridge.handle(object()),  # type: ignore[arg-type]
        )

    def test_zero_argument_command_rejects_extra_tokens(self) -> None:
        package, handlers = build_package()
        self.registry.register_package(package, {"demo": handlers})
        command_path = next(
            command.operation_path
            for command in package.modules[0].commands
            if not command.parameter_mapping
        )

        response = self.bridge.handle(
            _FakeEvent(f"/ygl demo {command_path} unexpected")
        )

        self.assertIn("/ygl demo", response)

    def test_multword_required_tail_keeps_optional_prefix_unambiguous(self) -> None:
        capability = CapabilityDescriptor(
            "lookup",
            {
                "type": "object",
                "properties": {
                    "optional": {"type": "string"},
                    "query": {"type": "string"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            InvocationPolicy.COMMAND_ONLY,
            CapabilityEffect.READ_ONLY,
        )
        command = CommandDescriptor(
            "lookup", "lookup", {"optional": "optional", "query": "query"}, "lookup"
        )

        self.assertEqual(
            AstrBotCommandBridge._parse_parameters(
                command, capability, ["Copper", "Ore"]
            ),
            {"query": "Copper Ore"},
        )
        self.assertEqual(
            AstrBotCommandBridge._parse_parameters(
                command, capability, ["optional=global", "Copper", "Ore"]
            ),
            {"optional": "global", "query": "Copper Ore"},
        )

    def test_optional_positionals_are_single_tokens_and_assignments_are_exact(self):
        capability = CapabilityDescriptor(
            "manage",
            {
                "type": "object",
                "properties": {
                    "timezone": {"type": "string"},
                    "time": {"type": "string"},
                },
                "required": [],
                "additionalProperties": False,
            },
            InvocationPolicy.COMMAND_ONLY,
            CapabilityEffect.READ_ONLY,
        )
        command = CommandDescriptor(
            "subscribe",
            "subscribe",
            {"timezone": "timezone", "time": "time"},
            "manage",
        )

        self.assertEqual(
            AstrBotCommandBridge._parse_parameters(
                command, capability, ["Asia/Shanghai"]
            ),
            {"timezone": "Asia/Shanghai"},
        )
        self.assertEqual(
            AstrBotCommandBridge._parse_parameters(
                command, capability, ["Asia/Shanghai", "09:30"]
            ),
            {"timezone": "Asia/Shanghai", "time": "09:30"},
        )
        self.assertEqual(
            AstrBotCommandBridge._parse_parameters(
                command, capability, ["time=09:30", "timezone=Asia/Shanghai"]
            ),
            {"time": "09:30", "timezone": "Asia/Shanghai"},
        )
        self.assertIsNone(
            AstrBotCommandBridge._parse_parameters(
                command, capability, ["unknown=value"]
            )
        )
        self.assertIsNone(
            AstrBotCommandBridge._parse_parameters(
                command, capability, ["timezone=Asia/Shanghai", "timezone=UTC"]
            )
        )

    def test_zero_argument_schema_rejects_key_value_tokens(self):
        package, handlers = build_package()
        self.registry.register_package(package, {"demo": handlers})
        invocation = self.bridge.parse(_FakeEvent("/ygl demo status extra=value"))
        self.assertNotIsInstance(invocation, CommandInvocation)

    def test_quoted_non_tail_text_and_assignment_values_keep_spaces(self):
        capability = CapabilityDescriptor(
            "output",
            {
                "type": "object",
                "properties": {
                    "realm": {"type": "string", "enum": ["global", "cn"]},
                    "encounter": {"type": "string", "minLength": 1},
                    "difficulty": {"type": "string", "minLength": 1},
                    "job": {"type": "string", "minLength": 1},
                    "metric": {"type": "string", "enum": ["rdps", "ndps"]},
                    "period": {"type": "string", "enum": ["latest"]},
                },
                "required": ["realm", "encounter", "difficulty", "job"],
                "additionalProperties": False,
            },
            InvocationPolicy.COMMAND_ONLY,
            CapabilityEffect.READ_ONLY,
        )
        command = CommandDescriptor(
            "output",
            "output",
            {
                "realm": "realm",
                "encounter": "encounter",
                "difficulty": "difficulty",
                "job": "job",
                "metric": "metric",
                "period": "period",
            },
            "output",
        )
        tokens = AstrBotCommandBridge._tokenize(
            '/ygl output global "The Minstrel\'s Ballad" savage Paladin '
            'metric="rdps" period=latest'
        )
        self.assertEqual(
            tokens,
            [
                "/ygl",
                "output",
                "global",
                "The Minstrel's Ballad",
                "savage",
                "Paladin",
                "metric=rdps",
                "period=latest",
            ],
        )
        self.assertEqual(
            AstrBotCommandBridge._parse_parameters(command, capability, tokens[2:]),
            {
                "realm": "global",
                "encounter": "The Minstrel's Ballad",
                "difficulty": "savage",
                "job": "Paladin",
                "metric": "rdps",
                "period": "latest",
            },
        )
        self.assertIsNone(AstrBotCommandBridge._tokenize('/ygl output "open'))
        self.assertIsNone(AstrBotCommandBridge._tokenize('/ygl output "joined"suffix'))


if __name__ == "__main__":
    unittest.main()
