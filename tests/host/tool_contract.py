"""Execute fixed Host tool classes/methods without starting Host or any provider.

Unrelated imports, startup constructors and unused transport types are omitted.
The schemas, wrappers, publisher, event accessors and permission methods below
are unchanged AST nodes from the hash-verified independent Host checkout.
"""

from __future__ import annotations

import ast
import contextlib
import logging
import sys
from collections.abc import AsyncGenerator, Awaitable, Callable
from enum import StrEnum
from types import ModuleType, SimpleNamespace
from typing import Any, Generic
from unittest.mock import patch

import jsonschema
from pydantic import Field, model_validator
from pydantic.dataclasses import dataclass
from typing_extensions import TypeVar

from .astrbot_contract import validate_host_source, validate_test_dependencies


def _execute(module, source, classes=(), methods=None, functions=False):
    tree = ast.parse(source)
    nodes = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and functions:
            nodes.append(node)
        if isinstance(node, ast.ClassDef) and node.name in classes:
            if methods is not None:
                node.body = [
                    item for item in node.body if getattr(item, "name", None) in methods
                ]
            nodes.append(node)
    future = ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0
    )
    exec(
        compile(
            ast.fix_missing_locations(
                ast.Module(body=[future, *nodes], type_ignores=[])
            ),
            module.__name__,
            "exec",
        ),
        module.__dict__,
    )


@contextlib.contextmanager
def tool_contracts():
    _, sources = validate_host_source()
    validate_test_dependencies()
    names = (
        "astrbot",
        "astrbot.core",
        "astrbot.core.agent",
        "astrbot.core.agent.tool",
        "astrbot.core.agent.run_context",
        "astrbot.core.platform",
        "astrbot.core.platform.astr_message_event",
        "astrbot.core.star",
        "astrbot.core.star.context",
        "astrbot.core.astr_agent_context",
        "astrbot.core.provider",
        "astrbot.core.provider.func_tool_manager",
        "astrbot.core.pipeline.process_stage.stage",
    )
    modules = {name: ModuleType(name) for name in names}
    for module in modules.values():
        module.__dict__.update(
            Any=Any,
            Generic=Generic,
            Field=Field,
            dataclass=dataclass,
            model_validator=model_validator,
            jsonschema=jsonschema,
            Callable=Callable,
            Awaitable=Awaitable,
            AsyncGenerator=AsyncGenerator,
        )
    with patch.dict(sys.modules, modules):
        wrapper = modules["astrbot.core.agent.run_context"]
        wrapper.TContext = TypeVar("TContext", default=Any)
        wrapper.Message = Any
        _execute(
            wrapper, sources["astrbot/core/agent/run_context.py"], ("ContextWrapper",)
        )
        tool = modules["astrbot.core.agent.tool"]
        tool.__dict__.update(
            TContext=wrapper.TContext,
            ContextWrapper=wrapper.ContextWrapper,
            ParametersType=dict[str, Any],
            ToolExecResult=str,
            MessageEventResult=Any,
            deprecated=lambda **_: lambda value: value,
        )
        _execute(
            tool,
            sources["astrbot/core/agent/tool.py"],
            ("ToolSchema", "FunctionTool", "ToolSet"),
        )
        event = modules["astrbot.core.platform.astr_message_event"]
        event.abc = SimpleNamespace(ABC=object)
        event.MessageType = StrEnum(
            "MessageType",
            {"GROUP_MESSAGE": "GroupMessage", "FRIEND_MESSAGE": "FriendMessage"},
        )
        _execute(
            event,
            sources["astrbot/core/platform/astr_message_event.py"],
            ("AstrMessageEvent",),
            {
                "get_platform_id",
                "get_message_str",
                "get_message_type",
                "get_session_id",
                "session_id",
                "get_sender_id",
                "is_admin",
                "should_call_llm",
                "get_extra",
                "set_extra",
                "get_result",
                "is_stopped",
            },
        )
        context = modules["astrbot.core.star.context"]
        context.__dict__.update(
            logger=logging.getLogger("fixed-host-tools"),
            star_registry=[],
            _PLUGIN_MODULE_FLAGS={"builtin_stars", "plugins"},
            StarMetadata=Any,
            FunctionTool=tool.FunctionTool,
        )
        _execute(
            context,
            sources["astrbot/core/star/context.py"],
            ("Context",),
            {"add_llm_tools"},
            functions=True,
        )
        agent = modules["astrbot.core.astr_agent_context"]
        agent.__dict__.update(
            Context=context.Context, AstrMessageEvent=event.AstrMessageEvent
        )
        _execute(
            agent, sources["astrbot/core/astr_agent_context.py"], ("AstrAgentContext",)
        )
        manager = modules["astrbot.core.provider.func_tool_manager"]

        class Preferences:
            def __init__(self):
                self.permissions = {}

            async def global_get(self, key, default):
                assert key == "tool_permissions"
                return self.permissions

        preferences = Preferences()
        manager.__dict__.update(
            FunctionTool=tool.FunctionTool, ToolSet=tool.ToolSet, sp=preferences
        )
        tree = ast.parse(sources["astrbot/core/provider/func_tool_manager.py"])
        owners = [
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name in {"FunctionToolManager", "_PermissionGuardedTool"}
        ]
        for owner in owners:
            if owner.name == "FunctionToolManager":
                owner.body = [
                    node
                    for node in owner.body
                    if getattr(node, "name", None)
                    in {
                        "remove_func",
                        "get_func",
                        "_default_permission",
                        "_check_tool_permission",
                        "get_full_tool_set",
                    }
                ]
        future = ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")], level=0
        )
        exec(
            compile(
                ast.fix_missing_locations(
                    ast.Module(body=[future, *owners], type_ignores=[])
                ),
                manager.__name__,
                "exec",
            ),
            manager.__dict__,
        )
        stage = modules["astrbot.core.pipeline.process_stage.stage"]
        stage.__dict__.update(Stage=object, register_stage=lambda value: value)
        stage.ProviderRequest = type("ProviderRequest", (), {})
        _execute(
            stage,
            sources["astrbot/core/pipeline/process_stage/stage.py"],
            ("ProcessStage",),
            {"process"},
        )
        yield SimpleNamespace(
            ProcessStage=stage.ProcessStage,
            FunctionTool=tool.FunctionTool,
            ToolSet=tool.ToolSet,
            ContextWrapper=wrapper.ContextWrapper,
            Context=context.Context,
            AstrAgentContext=agent.AstrAgentContext,
            Event=event.AstrMessageEvent,
            MessageType=event.MessageType,
            Manager=manager.FunctionToolManager,
            preferences=preferences,
        )
