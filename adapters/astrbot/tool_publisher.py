"""Owned FunctionTools over the existing Host provider chain and Core ingress."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping
from copy import copy
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from ...core.contracts.services import ToolOutput
from ...core.policy import tool_allowed
from ...services.core_runtime import HostIngressUnavailable
from ...services.output import OutputStatus


def _plain(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {key: _plain(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(child) for child in value]
    return value


def _tool_parameters(descriptor, capability):
    mapping = descriptor.parameter_mapping
    schema = _plain(capability.input_schema)
    return {
        "type": "object",
        "properties": {
            name: schema["properties"][field] for name, field in mapping.items()
        },
        "required": [
            name for name, field in mapping.items() if field in schema["required"]
        ],
        "additionalProperties": False,
    }


def _schema_envelope(left, right):
    """Return a provider-portable envelope; exact action schemas remain authoritative."""
    if left == right:
        return left
    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        raise ToolPublicationError("incompatible model tool parameter schemas")
    if left.get("type") != right.get("type"):
        raise ToolPublicationError("incompatible model tool parameter types")
    merged = {"type": left.get("type")}
    if merged["type"] == "object":
        left_props = left.get("properties", {})
        right_props = right.get("properties", {})
        if not isinstance(left_props, Mapping) or not isinstance(right_props, Mapping):
            raise ToolPublicationError("invalid model tool object schema")
        properties = {}
        for name in left_props.keys() | right_props.keys():
            if name in left_props and name in right_props:
                properties[name] = _schema_envelope(left_props[name], right_props[name])
            else:
                properties[name] = _plain(
                    left_props[name] if name in left_props else right_props[name]
                )
        merged["properties"] = properties
        merged["required"] = sorted(
            set(left.get("required", ())) & set(right.get("required", ()))
        )
        merged["additionalProperties"] = False
    elif merged["type"] == "array" and "items" in left and "items" in right:
        merged["items"] = _schema_envelope(left["items"], right["items"])
    if "enum" in left and "enum" in right:
        merged["enum"] = list(dict.fromkeys((*left["enum"], *right["enum"])))
    for key, selector in (
        ("minimum", min),
        ("exclusiveMinimum", min),
        ("minLength", min),
        ("minItems", min),
        ("maximum", max),
        ("exclusiveMaximum", max),
        ("maxLength", max),
        ("maxItems", max),
    ):
        if key in left and key in right:
            merged[key] = selector(left[key], right[key])
    return merged


def _factored_action_help(descriptors):
    """Emit every exact description sentence once with its action applicability."""
    descriptions = {
        descriptor.action: tuple(
            dict.fromkeys(
                piece
                for piece in re.split(r"(?<=[.!?])\s+", descriptor.description.strip())
                if piece
            )
        )
        for descriptor in descriptors
    }
    sentence_actions = {}
    for action, sentences in descriptions.items():
        for sentence in sentences:
            actions = sentence_actions.setdefault(sentence, [])
            if action not in actions:
                actions.append(action)

    sections = []
    for action, sentences in descriptions.items():
        unique = [
            sentence for sentence in sentences if sentence_actions[sentence] == [action]
        ]
        if unique:
            sections.append(f"{action}: {' '.join(unique)}")

    shared = defaultdict(list)
    for sentence, actions in sentence_actions.items():
        if len(actions) > 1:
            shared[tuple(actions)].append(sentence)
    sections.extend(
        f"{'/'.join(actions)}: {' '.join(sentences)}"
        for actions, sentences in shared.items()
    )
    return "；".join(sections)


def _aggregate_parameters(manifest, descriptors):
    capabilities = {cap.capability_id: cap for cap in manifest.capabilities}
    properties = {}
    for descriptor in descriptors:
        params = _tool_parameters(descriptor, capabilities[descriptor.capability_id])
        for name, schema in params["properties"].items():
            properties[name] = (
                _schema_envelope(properties[name], schema)
                if name in properties
                else _plain(schema)
            )
    actions = [descriptor.action for descriptor in descriptors]
    action_help = _factored_action_help(descriptors)
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": actions,
                "description": action_help,
            },
            "arguments": {
                "type": "object",
                "properties": properties,
                "required": [],
                "additionalProperties": False,
            },
        },
        "required": ["action", "arguments"],
        "additionalProperties": False,
    }


@dataclass(slots=True)
class ToolHandle:
    owner: object
    module_id: str
    epoch: int
    revision: int
    descriptor: object | None
    manager: object
    published_name: str
    manifest: object
    action_descriptors: Mapping[str, object] | None = None
    tool: object | None = None
    revoked: bool = False


class ToolPublicationError(RuntimeError):
    pass


class AstrBotToolPublisher:
    """Retain exact objects, revoke before cleanup, never change Host preferences."""

    def __init__(self, context, core, invoke):
        self.context, self.core, self.invoke = context, core, invoke
        self.owner = object()
        self.handles: list[ToolHandle] = []
        self.closed = False
        self.failure = None
        self._unsubscribe = core.registry.observe(self._changed)

    def _changed(self):
        try:
            self.publish()
        except Exception as exc:
            self.failure = type(exc).__name__

    def _current(self, handle):
        if self.closed or handle.revoked or handle.owner is not self.owner:
            return False
        try:
            module = self.core.registry.snapshot().module(handle.module_id)
            current = (
                module.enabled
                and module.epoch == handle.epoch
                and self.core.registry.is_active(module)
                and self.context.provider_manager.llm_tools is handle.manager
                and any(tool is handle.tool for tool in handle.manager.func_list)
                and not any(
                    tool is not handle.tool
                    and getattr(tool, "name", None) == handle.published_name
                    for tool in handle.manager.func_list
                )
                and module.manifest is handle.manifest
            )
            if not current:
                return False
            if handle.action_descriptors is None:
                return (
                    handle.descriptor in module.manifest.tools
                    and handle.published_name == handle.descriptor.name
                    and module.manifest.model_tool_name is None
                )
            expected = {
                descriptor.action: descriptor for descriptor in module.manifest.tools
            }
            return (
                module.manifest.model_tool_name == handle.published_name
                and None not in expected
                and dict(handle.action_descriptors) == expected
                and not any(
                    tool is not handle.tool
                    and tool.name
                    in {descriptor.name for descriptor in expected.values()}
                    for tool in handle.manager.func_list
                )
            )
        except Exception:
            return False

    def scoped_current(self, handle, action, descriptor):
        return (
            self.exposed_current(handle)
            and handle.action_descriptors is not None
            and type(action) is str
            and handle.action_descriptors.get(action) is descriptor
            and descriptor in handle.manifest.tools
            and descriptor.action == action
        )

    def exposed_current(self, handle):
        return self._current(handle) and getattr(handle.tool, "active", None) is True

    def revoke(self):
        self.closed = True
        self._unsubscribe()
        for handle in self.handles:
            handle.revoked = True

    def cleanup(self):
        """Synchronous exact list removal; retain failed handles for retry."""
        failed = False
        for handle in tuple(self.handles):
            if not handle.revoked:
                continue
            try:
                tools = handle.manager.func_list
                tools[:] = [tool for tool in tools if tool is not handle.tool]
                if any(tool is handle.tool for tool in handle.manager.func_list):
                    raise ToolPublicationError("tool cleanup pending")
            except Exception:
                failed = True
                continue
            self.handles.remove(handle)
        if failed:
            raise ToolPublicationError("tool cleanup pending")

    def publish(self):
        if self.closed:
            raise ToolPublicationError("publisher revoked")
        for handle in self.handles:
            if not self._current(handle):
                handle.revoked = True
        self.cleanup()
        manager = self.context.provider_manager.llm_tools
        snapshot = self.core.registry.snapshot()
        batch = []
        for module in snapshot.modules.values():
            if not module.enabled or not self.core.registry.is_active(module):
                continue
            eligible = []
            for descriptor in module.manifest.tools:
                capability = next(
                    cap
                    for cap in module.manifest.capabilities
                    if cap.capability_id == descriptor.capability_id
                )
                if not tool_allowed(capability):
                    continue
                eligible.append((descriptor, capability))
            if module.manifest.model_tool_name is not None:
                if len(eligible) != len(module.manifest.tools):
                    raise ToolPublicationError(
                        "model tool contains an ineligible action"
                    )
                legacy_names = {descriptor.name for descriptor, _ in eligible}
                if any(tool.name in legacy_names for tool in manager.func_list):
                    raise ToolPublicationError("legacy tool identity already owned")
                action_descriptors = MappingProxyType(
                    {descriptor.action: descriptor for descriptor, _ in eligible}
                )
                specs = ((module.manifest.model_tool_name, None, action_descriptors),)
            else:
                specs = tuple(
                    (descriptor.name, descriptor, None) for descriptor, _ in eligible
                )
            for published_name, descriptor, action_descriptors in specs:
                owned = next(
                    (
                        handle
                        for handle in self.handles
                        if self._current(handle)
                        and handle.module_id == module.module_id
                        and handle.published_name == published_name
                    ),
                    None,
                )
                if owned is not None:
                    if not any(tool is owned.tool for tool in manager.func_list):
                        owned.revoked = True
                        raise ToolPublicationError("owned tool replaced")
                    continue
                if any(tool.name == published_name for tool in manager.func_list):
                    raise ToolPublicationError("tool name already owned")
                handle = ToolHandle(
                    self.owner,
                    module.module_id,
                    module.epoch,
                    snapshot.revision,
                    descriptor,
                    manager,
                    published_name,
                    module.manifest,
                    action_descriptors,
                )
                if action_descriptors is None:
                    capability = next(
                        cap
                        for cap in module.manifest.capabilities
                        if cap.capability_id == descriptor.capability_id
                    )
                    handle.tool = self._tool(handle, capability)
                else:
                    handle.tool = self._aggregate_tool(handle)
                batch.append(handle)
        if len({handle.tool.name for handle in batch}) != len(batch):
            raise ToolPublicationError("duplicate tool names in batch")
        self.handles.extend(batch)
        try:
            for handle in batch:
                # Recheck each name immediately before the synchronous Host call.
                if any(tool.name == handle.tool.name for tool in manager.func_list):
                    raise ToolPublicationError("tool name already owned")
                self.context.add_llm_tools(handle.tool)
                if not any(tool is handle.tool for tool in manager.func_list):
                    raise ToolPublicationError("host did not retain tool")
        except BaseException:
            for handle in batch:
                handle.revoked = True
            self.cleanup()
            raise
        self.failure = None

    def retire_owner(self, module_id):
        """Revoke and remove only exact tools belonging to this owner."""
        for handle in self.handles:
            if handle.module_id == module_id:
                handle.revoked = True
        self.cleanup()
        if any(handle.module_id == module_id for handle in self.handles):
            raise ToolPublicationError("owner tool cleanup remains pending")

    async def _inactive_tool_names(self):
        try:
            from astrbot.api import sp

            values = await sp.global_get("inactivated_llm_tools", [])
            if not isinstance(values, list):
                raise TypeError("invalid Host inactive tool configuration")
            names = set()
            for value in values:
                if type(value) is not str or not value:
                    raise TypeError("invalid Host inactive tool name")
                names.add(value)
            return frozenset(names)
        except Exception:
            raise PermissionError("cannot prove Host inactive tool scope") from None

    def _subagent_assigned_tools(self, event):
        try:
            config = self.context.get_config()
            if not isinstance(config, Mapping):
                raise TypeError("invalid Host configuration")
            orchestrator = config.get("subagent_orchestrator", {})
            if not isinstance(orchestrator, Mapping):
                raise TypeError("invalid Host subagent configuration")
            main_enabled = orchestrator.get("main_enable", False)
            remove_duplicates = orchestrator.get("remove_main_duplicate_tools", False)
            if type(main_enabled) is not bool or type(remove_duplicates) is not bool:
                raise TypeError("invalid Host subagent flags")
            if not main_enabled or not remove_duplicates:
                return frozenset()
            subagents = getattr(self.context, "subagent_orchestrator", None)
            if subagents is None:
                raise TypeError("Host subagent runtime unavailable")
            handoffs_value = getattr(subagents, "handoffs", None)
            if not isinstance(handoffs_value, (tuple, list)):
                raise TypeError("invalid Host subagent handoffs")
            handoffs = tuple(handoffs_value)
            assigned = set()
            agents = orchestrator.get("agents", [])
            if not isinstance(agents, list):
                raise TypeError("invalid Host subagent agents")
            for item in agents:
                if not isinstance(item, Mapping):
                    raise TypeError("invalid Host subagent agent")
                enabled = item.get("enabled", True)
                if type(enabled) is not bool:
                    raise TypeError("invalid Host subagent enabled flag")
                if not enabled:
                    continue
                persona_tools = None
                persona_id = item.get("persona_id")
                if persona_id is not None and not isinstance(persona_id, str):
                    raise TypeError("invalid Host subagent persona")
                if persona_id:
                    persona = self.context.persona_manager.get_persona_v3_by_id(
                        persona_id
                    )
                    if persona is not None:
                        if not isinstance(persona, Mapping):
                            raise TypeError("invalid Host subagent persona")
                        persona_tools = persona.get("tools")
                tools = item.get("tools", [])
                if persona_tools is not None:
                    tools = persona_tools
                if tools is None:
                    assigned.update(
                        tool.name
                        for tool in self.context.provider_manager.llm_tools.func_list
                        if not any(tool is handoff for handoff in handoffs)
                    )
                    continue
                if not isinstance(tools, list):
                    raise TypeError("invalid Host subagent tools")
                assigned.update(name for value in tools if (name := str(value).strip()))
            return frozenset(assigned)
        except Exception:
            raise PermissionError("cannot prove Host subagent tool scope") from None

    async def _legacy_persona_names(self, event, request):
        conversation = getattr(request, "conversation", None)
        if conversation is None:
            raise PermissionError("conversation-bound persona required")
        config = self.context.get_config(umo=event.unified_msg_origin)
        if not isinstance(config, Mapping):
            raise PermissionError("invalid Host persona configuration")
        provider_settings = config.get("provider_settings", {})
        agent_runner = config.get("agent_runner", {})
        if not isinstance(provider_settings, Mapping) or not isinstance(
            agent_runner, Mapping
        ):
            raise PermissionError("invalid Host persona configuration")
        runner = agent_runner.get("config", {})
        if not isinstance(runner, Mapping):
            raise PermissionError("invalid Host persona configuration")
        persona_config = runner.get("persona", {})
        if not isinstance(persona_config, Mapping):
            raise PermissionError("invalid Host persona configuration")
        persona_id = persona_config.get("persona_id", "default")
        if type(persona_id) is not str or not persona_id.strip():
            raise PermissionError("invalid Host persona configuration")
        settings = dict(provider_settings)
        settings["default_personality"] = persona_id
        _, persona, _, _ = await self.context.persona_manager.resolve_selected_persona(
            umo=event.unified_msg_origin,
            conversation_persona_id=conversation.persona_id,
            platform_name=event.get_platform_name(),
            provider_settings=settings,
        )
        if not persona or persona.get("tools") is None:
            return None
        tools = persona.get("tools")
        if not isinstance(tools, list):
            raise PermissionError("invalid persona tool scope")
        return frozenset(str(name) for name in tools)

    async def _scope_handoffs(self, event, tool_set, inactive):
        """Project only explicit legacy subsets into request-local handoffs."""
        try:
            config = self.context.get_config()
            if not isinstance(config, Mapping):
                raise TypeError("invalid Host configuration")
            orchestrator = config.get("subagent_orchestrator", {})
            if not isinstance(orchestrator, Mapping):
                raise TypeError("invalid Host subagent configuration")
            main_enabled = orchestrator.get("main_enable", False)
            if type(main_enabled) is not bool:
                raise TypeError("invalid Host subagent flag")
            if not main_enabled:
                return

            from astrbot.api import FunctionTool
            from astrbot.core.agent.agent import Agent
            from astrbot.core.agent.handoff import HandoffTool
            from astrbot.core.provider.register import llm_tools as registered_tools

            subagents = getattr(self.context, "subagent_orchestrator", None)
            handoffs_value = getattr(subagents, "handoffs", None)
            if not isinstance(handoffs_value, (tuple, list)):
                raise TypeError("invalid Host subagent handoffs")
            handoffs = tuple(handoffs_value)
            if not all(isinstance(handoff, HandoffTool) for handoff in handoffs):
                raise TypeError("invalid Host subagent handoff")
            if len({handoff.name for handoff in handoffs}) != len(handoffs):
                raise TypeError("ambiguous Host handoff scope")

            manager_getter = getattr(self.context, "get_llm_tool_manager", None)
            manager = (
                manager_getter()
                if callable(manager_getter)
                else self.context.provider_manager.llm_tools
            )
            if manager is not self.context.provider_manager.llm_tools:
                raise TypeError("ambiguous Host tool manager")

            handles = tuple(
                handle
                for handle in self.handles
                if handle.action_descriptors is not None and self._current(handle)
            )
            if any(handle.manager is not manager for handle in handles):
                raise TypeError("ambiguous Host aggregate manager")
            by_parent = {handle.published_name: handle for handle in handles}
            if len(by_parent) != len(handles):
                raise TypeError("ambiguous Host aggregate scope")
            legacy = {
                descriptor.name: (handle, descriptor)
                for handle in handles
                for descriptor in handle.action_descriptors.values()
            }
            if len(legacy) != sum(len(h.action_descriptors) for h in handles):
                raise TypeError("ambiguous Host legacy tool scope")

            class DeferredTools(list):
                """Evaluate an explicit legacy projection at Host execution time."""

                def __init__(self, factory):
                    super().__init__()
                    self._factory = factory

                def __bool__(self):
                    # Force Host's explicit-list branch. Iteration may still
                    # yield no tools, in which case Host returns None.
                    return True

                def __iter__(self):
                    return iter(self._factory())

            clones = {}

            def validate_handoff(handoff, seen):
                if not isinstance(handoff, HandoffTool):
                    raise TypeError("invalid nested Host handoff")
                if id(handoff) in seen:
                    return
                seen.add(id(handoff))
                agent = getattr(handoff, "agent", None)
                if not isinstance(agent, Agent):
                    raise TypeError("invalid Host handoff agent")
                configured = agent.tools
                if configured is None:
                    return
                if not isinstance(configured, list):
                    raise TypeError("invalid Host handoff tools")
                for value in configured:
                    if isinstance(value, str):
                        continue
                    if isinstance(value, HandoffTool):
                        validate_handoff(value, seen)
                        continue
                    if not isinstance(value, FunctionTool):
                        raise TypeError("invalid Host handoff tool")

            for handoff in handoffs:
                validate_handoff(handoff, set())

            def requires_projection(handoff, seen=None):
                if not isinstance(handoff, HandoffTool):
                    raise TypeError("invalid nested Host handoff")
                if seen is None:
                    seen = set()
                if id(handoff) in seen:
                    return False
                seen.add(id(handoff))
                agent = getattr(handoff, "agent", None)
                if not isinstance(agent, Agent):
                    raise TypeError("invalid Host handoff agent")
                configured = agent.tools
                if configured is None:
                    return False
                if not isinstance(configured, list):
                    raise TypeError("invalid Host handoff tools")
                for value in configured:
                    if isinstance(value, str):
                        if value in legacy:
                            return True
                        if value in by_parent:
                            continue
                        resolved = registered_tools.get_func(value)
                        if isinstance(resolved, HandoffTool) and requires_projection(
                            resolved, seen
                        ):
                            return True
                    elif isinstance(value, HandoffTool):
                        if requires_projection(value, seen):
                            return True
                    elif not isinstance(value, FunctionTool):
                        raise TypeError("invalid Host handoff tool")
                return False

            def materialize(configured):
                selected = {id(handle): [] for handle in handles}
                native_full = set()
                base = []
                for value in configured:
                    if isinstance(value, str):
                        handle = by_parent.get(value)
                        if handle is not None:
                            native_full.add(id(handle))
                            base.append(value)
                        elif value in legacy:
                            legacy_handle, descriptor = legacy[value]
                            selected[id(legacy_handle)].append(descriptor)
                        else:
                            # Match Host's module-global, execution-time string
                            # resolution. A registered handoff is cloned only
                            # after the current target and active flag are known;
                            # ordinary strings stay on Host's native path.
                            resolved = registered_tools.get_func(value)
                            if isinstance(resolved, HandoffTool):
                                if bool(getattr(resolved, "active", True)):
                                    base.append(
                                        clone_handoff(resolved)
                                        if requires_projection(resolved)
                                        else value
                                    )
                                else:
                                    base.append(value)
                            else:
                                base.append(value)
                    elif isinstance(value, HandoffTool):
                        base.append(
                            clone_handoff(value)
                            if requires_projection(value)
                            else value
                        )
                    elif isinstance(value, FunctionTool):
                        handle = next(
                            (
                                candidate
                                for candidate in handles
                                if value is candidate.tool
                                or self._is_owned_permission_guard(value, candidate)
                            ),
                            None,
                        )
                        if handle is None:
                            base.append(value)
                        else:
                            native_full.add(id(handle))
                            base.append(value)
                    else:
                        raise TypeError("invalid Host handoff tool")

                occupied_names = {
                    value.name for value in base if isinstance(value, FunctionTool)
                }
                for handle in handles:
                    if (
                        id(handle) in native_full
                        or handle.published_name in occupied_names
                    ):
                        continue
                    if not self.exposed_current(handle):
                        continue
                    chosen = selected[id(handle)]
                    descriptors = tuple(
                        descriptor
                        for descriptor in handle.action_descriptors.values()
                        if descriptor in chosen
                        and descriptor.name not in inactive
                        and handle.published_name not in inactive
                    )
                    if descriptors:
                        base.append(self._scoped_tool(handle, event, descriptors))
                return base

            def clone_handoff(handoff):
                if not isinstance(handoff, HandoffTool):
                    raise TypeError("invalid nested Host handoff")
                existing_clone = clones.get(id(handoff))
                if existing_clone is not None:
                    return existing_clone
                agent = getattr(handoff, "agent", None)
                if not isinstance(agent, Agent):
                    raise TypeError("invalid Host handoff agent")
                if not requires_projection(handoff):
                    return handoff
                clone = copy(handoff)
                clone_agent = copy(agent)
                clone_agent.tools = []
                clone.agent = clone_agent
                clone._ygl_scope_owner = self.owner
                clone._ygl_handoff_parent = handoff
                clone._ygl_event = event
                clones[id(handoff)] = clone

                configured = agent.tools
                if not isinstance(configured, list) or not configured:
                    raise TypeError("legacy projection requires explicit tools")
                configured = tuple(configured)
                clone_agent.tools = DeferredTools(lambda: materialize(configured))
                return clone

            replacements = []
            removals = []
            for handoff in handoffs:
                existing = tool_set.get_tool(handoff.name)
                if existing is handoff:
                    if requires_projection(handoff):
                        replacements.append((handoff.name, clone_handoff(handoff)))
                    continue
                if getattr(existing, "_ygl_handoff_parent", None) is handoff:
                    # Never rebind a request-local clone to another event.
                    if (
                        getattr(existing, "_ygl_scope_owner", None) is not self.owner
                        or getattr(existing, "_ygl_event", None) is not event
                    ):
                        removals.append(handoff.name)
                    continue
                # Host filtering removed this handoff; do not reconstruct it.
            for name in removals:
                tool_set.remove_tool(name)
            for name, clone in replacements:
                tool_set.remove_tool(name)
                tool_set.add_tool(clone)
        except PermissionError:
            raise
        except Exception:
            raise PermissionError("cannot scope Host subagent handoff") from None

    def _plugin_allows_reconstruction(self, handle, event):
        if getattr(event, "plugins_name", None) is None:
            return True
        path = getattr(handle.tool, "handler_module_path", None)
        if not path:
            return False
        try:
            from astrbot.core.star.star_handler import star_map

            plugin = star_map.get(path)
            return plugin is not None and (
                plugin.name in event.plugins_name or plugin.reserved
            )
        except Exception:
            return False

    @staticmethod
    def _is_owned_permission_guard(tool, handle):
        """Recognize only Host's exact transparent guard for this manager."""
        try:
            from astrbot.core.provider.func_tool_manager import (
                _PermissionGuardedTool,
            )
        except ImportError:
            return False
        return (
            type(tool) is _PermissionGuardedTool
            and tool._wrapped is handle.tool
            and tool._mgr is handle.manager
        )

    def _sanitize_tool_set(self, tool_set, *, event):
        """Remove this owner's callable authority before any fallible projection."""
        from astrbot.api import ToolSet

        if not isinstance(tool_set, ToolSet):
            return ToolSet(), (), False
        original = tuple(tool_set)
        kept = []
        for tool in original:
            if any(
                tool is handle.tool or self._is_owned_permission_guard(tool, handle)
                for handle in self.handles
                if handle.action_descriptors is not None
            ):
                continue
            if getattr(tool, "_ygl_scope_owner", None) is self.owner:
                handoff_parent = getattr(tool, "_ygl_handoff_parent", None)
                if (
                    handoff_parent is not None
                    and getattr(tool, "_ygl_event", None) is event
                ):
                    kept.append(handoff_parent)
                continue
            kept.append(tool)
        return ToolSet(kept), original, True

    async def scope_request(self, event, request):
        """Sanitize first, then atomically commit a complete request projection."""
        from astrbot.api import ToolSet

        sanitized, original_tools, valid = self._sanitize_tool_set(
            getattr(request, "func_tool", None), event=event
        )
        request.func_tool = sanitized
        if not valid:
            raise PermissionError("valid Host request ToolSet required")
        sanitized_tools = tuple(sanitized)
        original = ToolSet(list(original_tools))
        proposed = ToolSet(list(sanitized.tools))
        assigned = self._subagent_assigned_tools(event)
        inactive = await self._inactive_tool_names()
        await self._scope_handoffs(event, proposed, inactive)
        for handle in tuple(self.handles):
            if handle.action_descriptors is None or not self._current(handle):
                continue
            existing = original.get_tool(handle.published_name)
            raw_parent_present = existing is handle.tool or (
                self._is_owned_permission_guard(existing, handle)
            )
            prior_scope = (
                existing
                if getattr(existing, "_ygl_scope_owner", None) is self.owner
                and getattr(existing, "_ygl_parent", None) is handle
                and getattr(existing, "_ygl_event", None) is event
                else None
            )
            if not self.exposed_current(handle):
                continue
            if existing is not None and not raw_parent_present and prior_scope is None:
                # A same-name request tool belongs to another owner. Preserve
                # the exact object selected by Host; absence is the only state
                # in which legacy reconstruction may claim this name.
                continue
            if prior_scope is not None:
                descriptors = tuple(prior_scope._ygl_descriptors)
            elif raw_parent_present:
                descriptors = tuple(handle.action_descriptors.values())
            else:
                try:
                    names = await self._legacy_persona_names(event, request)
                except Exception:
                    continue
                if names is None:
                    descriptors = tuple(handle.action_descriptors.values())
                else:
                    descriptors = tuple(
                        descriptor
                        for descriptor in handle.action_descriptors.values()
                        if descriptor.name in names
                    )
                if descriptors and not self._plugin_allows_reconstruction(
                    handle, event
                ):
                    descriptors = ()
            if handle.published_name in assigned or handle.published_name in inactive:
                descriptors = ()
            else:
                descriptors = tuple(
                    descriptor
                    for descriptor in descriptors
                    if descriptor.name not in assigned
                    and descriptor.name not in inactive
                )
            if descriptors:
                proposed.add_tool(self._scoped_tool(handle, event, tuple(descriptors)))
        current = getattr(request, "func_tool", None)
        current_tools = tuple(current) if isinstance(current, ToolSet) else ()
        unchanged = current is sanitized and len(current_tools) == len(sanitized_tools)
        if unchanged:
            unchanged = all(
                actual is expected
                for actual, expected in zip(current_tools, sanitized_tools, strict=True)
            )
        if not unchanged:
            fail_closed, _, _ = self._sanitize_tool_set(current, event=event)
            request.func_tool = fail_closed
            raise PermissionError("Host request ToolSet changed during projection")
        request.func_tool = proposed

    def _aggregate_tool(self, handle):
        from astrbot.api import FunctionTool

        descriptors = tuple(handle.action_descriptors.values())
        actions = MappingProxyType(
            {descriptor.action: descriptor for descriptor in descriptors}
        )
        parameters = _aggregate_parameters(handle.manifest, descriptors)
        publisher = self

        class AggregateParentTool(FunctionTool):
            async def call(self, context, **kwargs):
                return await publisher._call_aggregate(
                    handle,
                    context,
                    kwargs,
                    actions,
                    parameters,
                )

        return AggregateParentTool(
            name=handle.published_name,
            description=handle.manifest.model_tool_description,
            parameters=parameters,
            handler=None,
        )

    def _scoped_tool(self, handle, event, descriptors):
        from astrbot.api import FunctionTool

        publisher = self
        actions = MappingProxyType(
            {descriptor.action: descriptor for descriptor in descriptors}
        )
        parameters = _aggregate_parameters(handle.manifest, descriptors)

        class ScopedAggregateTool(FunctionTool):
            async def call(self, context, **kwargs):
                return await publisher._call_aggregate(
                    handle,
                    context,
                    kwargs,
                    actions,
                    parameters,
                    exact_event=event,
                )

        scoped = ScopedAggregateTool(
            name=handle.published_name,
            description=handle.manifest.model_tool_description,
            parameters=parameters,
            handler=None,
        )
        scoped.handler_module_path = getattr(handle.tool, "handler_module_path", None)
        scoped._ygl_scope_owner = self.owner
        scoped._ygl_parent = handle
        scoped._ygl_event = event
        scoped._ygl_descriptors = descriptors
        return scoped

    async def _call_aggregate(
        self,
        handle,
        context,
        kwargs,
        actions,
        parameters,
        *,
        exact_event=None,
    ):
        from astrbot.core.agent.run_context import ContextWrapper
        from astrbot.core.platform.astr_message_event import AstrMessageEvent

        from ...core.contracts.validation import _validate

        actual_context = getattr(context, "context", None)
        actual_event = getattr(actual_context, "event", None)
        if (
            not isinstance(context, ContextWrapper)
            or not isinstance(actual_event, AstrMessageEvent)
            or (exact_event is not None and actual_event is not exact_event)
            or getattr(context.context, "context", None) is not self.context
        ):
            message = (
                "exact Host request event required"
                if exact_event is not None
                else "official Host event required"
            )
            raise PermissionError(message)
        if not self._plugin_allows_reconstruction(handle, actual_event):
            raise PermissionError("Host plugin scope does not allow this tool")

        def require_current_context():
            if (
                getattr(context, "context", None) is not actual_context
                or getattr(actual_context, "event", None) is not actual_event
                or getattr(actual_context, "context", None) is not self.context
                or (exact_event is not None and actual_event is not exact_event)
            ):
                raise PermissionError("Host request context changed during execution")
            if not self._plugin_allows_reconstruction(handle, actual_event):
                raise PermissionError("Host plugin scope does not allow this tool")

        _validate(parameters, kwargs, "parameters", 0)
        action = kwargs["action"]
        descriptor = actions.get(action)
        if descriptor is None or not self.scoped_current(handle, action, descriptor):
            raise PermissionError("model tool scope is no longer current")
        inactive = await self._inactive_tool_names()
        if handle.published_name in inactive or descriptor.name in inactive:
            raise PermissionError("Host tool is inactive")
        arguments = kwargs["arguments"]
        capability = next(
            cap
            for cap in handle.manifest.capabilities
            if cap.capability_id == descriptor.capability_id
        )
        _validate(
            _tool_parameters(descriptor, capability),
            arguments,
            "arguments",
            0,
        )
        try:
            aggregate_error = await handle.manager._check_tool_permission(
                handle.published_name, context
            )
            legacy_error = await handle.manager._check_tool_permission(
                descriptor.name, context
            )
        except Exception:
            raise PermissionError("Host tool permission check failed") from None
        require_current_context()
        if aggregate_error is not None:
            return aggregate_error
        if legacy_error is not None:
            return legacy_error

        async def final_policy_check():
            if not self.scoped_current(handle, action, descriptor):
                raise PermissionError("model tool scope changed during execution")
            inactive = await self._inactive_tool_names()
            if not self.scoped_current(handle, action, descriptor):
                raise PermissionError(
                    "model tool scope changed during final policy check"
                )
            require_current_context()
            if handle.published_name in inactive or descriptor.name in inactive:
                raise PermissionError("Host tool became inactive")

        unavailable = False
        try:
            outcome = await self.invoke(
                handle.module_id,
                descriptor.name,
                arguments,
                actual_event,
                tool_handle=handle,
                tool_descriptor=descriptor,
                tool_action=action,
                final_policy_check=final_policy_check,
            )
        except HostIngressUnavailable:
            unavailable = True
        if not self.scoped_current(handle, action, descriptor):
            raise PermissionError("model tool scope changed during execution")
        require_current_context()
        if unavailable:
            return json.dumps(
                {
                    "error": {
                        "code": "module_unavailable",
                        "message": "模块或依赖服务暂时不可用。",
                    }
                },
                ensure_ascii=False,
            )
        routed = outcome.output
        if (
            routed.status is not OutputStatus.TOOL_RESULT
            or type(routed.result) is not ToolOutput
        ):
            raise PermissionError("Core did not authorize public ToolOutput")
        return json.dumps(
            _plain(routed.result.facts.facts),
            ensure_ascii=False,
            allow_nan=False,
        )

    def _tool(self, handle, capability):
        from astrbot.core.agent.tool import FunctionTool

        parameters = _tool_parameters(handle.descriptor, capability)
        publisher = self

        class OwnedFunctionTool(FunctionTool):
            async def call(self, context, **kwargs):
                from astrbot.core.agent.run_context import ContextWrapper
                from astrbot.core.platform.astr_message_event import AstrMessageEvent

                from ...core.contracts.validation import _validate

                if not publisher.exposed_current(handle):
                    raise PermissionError("tool revoked or module changed")
                if (
                    not isinstance(context, ContextWrapper)
                    or not isinstance(
                        getattr(context.context, "event", None), AstrMessageEvent
                    )
                    or getattr(context.context, "context", None)
                    is not publisher.context
                ):
                    raise PermissionError("official Host event required")
                _validate(parameters, kwargs, "parameters", 0)
                event = context.context.event
                unavailable = False
                try:
                    outcome = await publisher.invoke(
                        handle.module_id,
                        handle.descriptor.name,
                        kwargs,
                        event,
                        tool_handle=handle,
                    )
                except HostIngressUnavailable:
                    unavailable = True
                if not publisher.exposed_current(handle):
                    raise PermissionError("tool revoked during execution")
                if unavailable:
                    # Fixed public control feedback, with no module result,
                    # identity, arbitrary exception text or new HTTP request.
                    return json.dumps(
                        {
                            "error": {
                                "code": "module_unavailable",
                                "message": "模块或依赖服务暂时不可用。",
                            }
                        },
                        ensure_ascii=False,
                    )
                routed = outcome.output
                if (
                    routed.status is not OutputStatus.TOOL_RESULT
                    or type(routed.result) is not ToolOutput
                ):
                    raise PermissionError("Core did not authorize public ToolOutput")
                return json.dumps(
                    _plain(routed.result.facts.facts),
                    ensure_ascii=False,
                    allow_nan=False,
                )

        return OwnedFunctionTool(
            name=handle.published_name,
            description=handle.descriptor.description,
            parameters=parameters,
            handler=None,
        )
