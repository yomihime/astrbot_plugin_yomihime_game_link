"""Owned FunctionTools over the existing Host provider chain and Core ingress."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from ...core.contracts.services import ToolOutput
from ...core.policy import tool_allowed
from ...services.output import OutputStatus


def _plain(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {key: _plain(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(child) for child in value]
    return value


@dataclass(slots=True)
class ToolHandle:
    owner: object
    module_id: str
    epoch: int
    revision: int
    descriptor: object
    manager: object
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
            return (
                module.enabled
                and module.epoch == handle.epoch
                and self.core.registry.is_active(module)
                and self.context.provider_manager.llm_tools is handle.manager
                and any(tool is handle.tool for tool in handle.manager.func_list)
                and handle.descriptor in module.manifest.tools
            )
        except Exception:
            return False

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
            for descriptor in module.manifest.tools:
                capability = next(
                    cap
                    for cap in module.manifest.capabilities
                    if cap.capability_id == descriptor.capability_id
                )
                if not tool_allowed(capability):
                    continue
                owned = next(
                    (
                        handle
                        for handle in self.handles
                        if self._current(handle)
                        and handle.module_id == module.module_id
                        and handle.descriptor == descriptor
                    ),
                    None,
                )
                if owned is not None:
                    if not any(tool is owned.tool for tool in manager.func_list):
                        owned.revoked = True
                        raise ToolPublicationError("owned tool replaced")
                    continue
                if any(tool.name == descriptor.name for tool in manager.func_list):
                    raise ToolPublicationError("tool name already owned")
                handle = ToolHandle(
                    self.owner,
                    module.module_id,
                    module.epoch,
                    snapshot.revision,
                    descriptor,
                    manager,
                )
                handle.tool = self._tool(handle, capability)
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

    def _tool(self, handle, capability):
        from astrbot.core.agent.tool import FunctionTool

        mapping = handle.descriptor.parameter_mapping
        schema = _plain(capability.input_schema)
        properties = {
            name: schema["properties"][field] for name, field in mapping.items()
        }
        required = [
            name for name, field in mapping.items() if field in schema["required"]
        ]
        parameters = dict(
            type="object",
            properties=properties,
            required=required,
            additionalProperties=False,
        )
        publisher = self

        class OwnedFunctionTool(FunctionTool):
            async def call(self, context, **kwargs):
                from astrbot.core.agent.run_context import ContextWrapper
                from astrbot.core.platform.astr_message_event import AstrMessageEvent

                from ...core.contracts.validation import _validate

                if not publisher._current(handle):
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
                outcome = await publisher.invoke(
                    handle.module_id,
                    handle.descriptor.name,
                    kwargs,
                    event,
                    tool_handle=handle,
                )
                if not publisher._current(handle):
                    raise PermissionError("tool revoked during execution")
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
            name=handle.descriptor.name,
            description=handle.descriptor.description,
            parameters=parameters,
            handler=None,
        )
