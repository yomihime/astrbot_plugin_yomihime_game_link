"""Host 4.29.1 persona/subagent replay for aggregate model tools."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from ygl_test_subject.adapters.astrbot.tool_publisher import (
    AstrBotToolPublisher,
    _factored_action_help,
)


class _PersonaManager:
    def __init__(self, personas=None):
        self.personas = personas or {}
        self.settings = None

    def get_persona_v3_by_id(self, persona_id):
        return self.personas.get(persona_id)

    async def resolve_selected_persona(self, **kwargs):
        self.settings = kwargs["provider_settings"]
        return None, {"tools": []}, None, None


class _Context:
    def __init__(self, config, personas=None, *, event_config=None):
        self.config = config
        self.event_config = config if event_config is None else event_config
        self.persona_manager = _PersonaManager(personas)
        self.subagent_orchestrator = SimpleNamespace(handoffs=[])
        tools = [
            SimpleNamespace(name="legacy_recent"),
            SimpleNamespace(name="legacy_profile"),
        ]
        self.provider_manager = SimpleNamespace(
            llm_tools=SimpleNamespace(func_list=tools)
        )

    def get_config(self, *, umo=None):
        if umo is None:
            return self.config
        if umo != "platform:conversation":
            raise AssertionError("configuration must be event-bound")
        return self.event_config


def _publisher(context):
    value = AstrBotToolPublisher.__new__(AstrBotToolPublisher)
    value.context = context
    return value


class ToolPublisherScopeTests(unittest.IsolatedAsyncioTestCase):
    def test_action_help_factors_only_exact_sentences_and_keeps_applicability(self):
        descriptors = (
            SimpleNamespace(
                action="first", description="Shared rule. First only! Shared rule."
            ),
            SimpleNamespace(action="second", description="Shared rule. Second only?"),
            SimpleNamespace(action="third", description="Third only. Shared rule."),
        )
        help_text = _factored_action_help(descriptors)
        self.assertEqual(
            help_text,
            "first: First only!；second: Second only?；third: Third only.；"
            "first/second/third: Shared rule.",
        )
        for sentence in ("Shared rule.", "First only!", "Second only?", "Third only."):
            self.assertEqual(help_text.count(sentence), 1)

    def test_subagent_scope_uses_host_default_not_event_profile(self):
        event = SimpleNamespace(unified_msg_origin="platform:conversation")
        context = _Context(
            {
                "subagent_orchestrator": {
                    "main_enable": True,
                    "remove_main_duplicate_tools": True,
                    "agents": [{"tools": ["legacy_recent"]}],
                }
            },
            event_config={
                "subagent_orchestrator": {
                    "main_enable": False,
                    "remove_main_duplicate_tools": False,
                }
            },
        )
        self.assertEqual(
            _publisher(context)._subagent_assigned_tools(event),
            frozenset({"legacy_recent"}),
        )

    def test_subagent_scope_rejects_missing_or_malformed_host_config(self):
        event = SimpleNamespace(unified_msg_origin="platform:conversation")
        enabled = {
            "main_enable": True,
            "remove_main_duplicate_tools": True,
        }
        missing_runtime = _Context({"subagent_orchestrator": {**enabled, "agents": []}})
        del missing_runtime.subagent_orchestrator
        invalid_handoffs = _Context(
            {"subagent_orchestrator": {**enabled, "agents": []}}
        )
        invalid_handoffs.subagent_orchestrator = object()
        contexts = (
            ("missing config API", SimpleNamespace()),
            ("invalid config", _Context(None)),
            ("invalid orchestrator", _Context({"subagent_orchestrator": []})),
            ("missing runtime", missing_runtime),
            ("invalid handoffs", invalid_handoffs),
            (
                "agents not list",
                _Context({"subagent_orchestrator": {**enabled, "agents": {}}}),
            ),
            (
                "agent not mapping",
                _Context({"subagent_orchestrator": {**enabled, "agents": [1]}}),
            ),
            (
                "enabled not bool",
                _Context(
                    {
                        "subagent_orchestrator": {
                            **enabled,
                            "agents": [{"enabled": "yes"}],
                        }
                    }
                ),
            ),
            (
                "tools not list",
                _Context(
                    {
                        "subagent_orchestrator": {
                            **enabled,
                            "agents": [{"tools": "legacy_recent"}],
                        }
                    }
                ),
            ),
        )
        for label, context in contexts:
            with self.subTest(label=label), self.assertRaises(PermissionError):
                _publisher(context)._subagent_assigned_tools(event)

    def test_subagent_scope_disabled_does_not_require_runtime(self):
        event = SimpleNamespace(unified_msg_origin="platform:conversation")
        for config in (
            {"main_enable": False, "remove_main_duplicate_tools": True},
            {"main_enable": True, "remove_main_duplicate_tools": False},
        ):
            context = _Context({"subagent_orchestrator": config})
            del context.subagent_orchestrator
            with self.subTest(config=config):
                self.assertEqual(
                    _publisher(context)._subagent_assigned_tools(event), frozenset()
                )

    def test_subagent_persona_none_falls_back_to_agent_tools_like_host(self):
        event = SimpleNamespace(unified_msg_origin="platform:conversation")
        cases = (
            ("missing persona", {}, "missing", ["legacy_recent"], {"legacy_recent"}),
            (
                "persona tools none",
                {"selected": {"tools": None}},
                "selected",
                ["legacy_recent"],
                {"legacy_recent"},
            ),
            (
                "persona empty list",
                {"selected": {"tools": []}},
                "selected",
                ["legacy_recent"],
                set(),
            ),
            (
                "persona subset",
                {"selected": {"tools": ["legacy_profile"]}},
                "selected",
                ["legacy_recent"],
                {"legacy_profile"},
            ),
        )
        for label, personas, persona_id, agent_tools, expected in cases:
            with self.subTest(label=label):
                config = {
                    "subagent_orchestrator": {
                        "main_enable": True,
                        "remove_main_duplicate_tools": True,
                        "agents": [{"persona_id": persona_id, "tools": agent_tools}],
                    }
                }
                context = _Context(config, personas)
                self.assertEqual(
                    _publisher(context)._subagent_assigned_tools(event),
                    frozenset(expected),
                )

    async def test_persona_config_uses_host_default_and_rejects_malformed_override(
        self,
    ):
        event = SimpleNamespace(
            unified_msg_origin="platform:conversation",
            get_platform_name=lambda: "platform",
        )
        request = SimpleNamespace(conversation=SimpleNamespace(persona_id=None))
        for persona, expected in (
            ({}, "default"),
            ({"persona_id": "runner"}, "runner"),
        ):
            context = _Context(
                {
                    "provider_settings": {"default_personality": "provider"},
                    "agent_runner": {"config": {"persona": persona}},
                }
            )
            await _publisher(context)._legacy_persona_names(event, request)
            self.assertEqual(
                context.persona_manager.settings["default_personality"], expected
            )

        for malformed in (None, "", 3):
            context = _Context(
                {
                    "provider_settings": {"default_personality": "restricted"},
                    "agent_runner": {"config": {"persona": {"persona_id": malformed}}},
                }
            )
            with self.subTest(malformed=malformed), self.assertRaises(PermissionError):
                await _publisher(context)._legacy_persona_names(event, request)


if __name__ == "__main__":
    unittest.main()
