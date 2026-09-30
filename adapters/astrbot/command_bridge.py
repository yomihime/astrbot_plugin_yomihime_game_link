"""Parse the generic ``/ygl`` command surface against one Core snapshot."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from yomihime_sdk.api.manifests import CapabilityDescriptor, CommandDescriptor
from yomihime_sdk.api.validation import validate_parameters

from ...core.help_catalog import HelpCatalog
from ...core.registry import (
    RegisteredModule,
    Registry,
    RegistryError,
)
from ...presentation.text import TextPresenter

_ROUTE = re.compile(r"[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*\Z")
_USAGE = "用法：/ygl [help | <模块路由> help | <模块路由> <命令及参数>]。参数以空格分隔；双引号用于包含空格；不支持转义或 shell 语法。"


class MessageTextEvent(Protocol):
    """The host text accessor required by the parser."""

    def get_message_str(self) -> str: ...


@dataclass(frozen=True, slots=True)
class CommandInvocation:
    """A parsed, descriptor-backed command ready for CoreRuntime."""

    module_id: str
    operation_path: str
    parameters: Mapping[str, object]


class AstrBotCommandBridge:
    """Render help or parse a generic module command from a Core snapshot."""

    def __init__(
        self,
        registry: Registry,
        *,
        catalog: HelpCatalog | None = None,
        presenter: TextPresenter | None = None,
        max_chars: int = 4000,
    ) -> None:
        if not isinstance(registry, Registry):
            raise TypeError("registry must be a Registry")
        if type(max_chars) is not int or max_chars < 1:
            raise ValueError("max_chars must be a positive integer")
        self._registry = registry
        self._catalog = catalog or HelpCatalog()
        self._presenter = presenter or TextPresenter()
        self._max_chars = max_chars

    def handle(self, event: MessageTextEvent) -> str:
        """Retain the text-only help/usage view for callers without dispatch."""

        action = self.parse(event)
        if isinstance(action, CommandInvocation):
            return _USAGE
        return action

    def parse(self, event: MessageTextEvent) -> str | CommandInvocation:
        """Resolve help or produce a command invocation using descriptor schema."""

        tokens = self._tokens(event)
        if tokens is None:
            return _USAGE
        snapshot = self._registry.snapshot()
        if not tokens or tokens == ["help"]:
            return self._render(self._catalog.total(snapshot))
        if _ROUTE.fullmatch(tokens[0]) is None:
            return _USAGE
        try:
            module = snapshot.module_for_route(tokens[0])
        except RegistryError:
            return self._render(self._catalog.total(snapshot))
        tail = tokens[1:]
        if tail == ["help"]:
            return self._render(self._catalog.module(snapshot, module.manifest.route))
        invocation = self._parse_module_command(module, tail)
        if invocation is None:
            return self._render(self._catalog.module(snapshot, module.manifest.route))
        return invocation

    def _render(self, document) -> str:
        return self._presenter.render(document, max_chars=self._max_chars)

    @staticmethod
    def _tokens(event: MessageTextEvent) -> list[str] | None:
        get_message_str: Callable[[], object] | None = getattr(
            event, "get_message_str", None
        )
        if not callable(get_message_str):
            return None
        try:
            message = get_message_str()
        except Exception:
            return None
        if not isinstance(message, str):
            return None

        tokens = AstrBotCommandBridge._tokenize(message)
        if tokens is None:
            return None
        if tokens and tokens[0] == "/ygl":
            tokens = tokens[1:]
        elif tokens and tokens[0] == "ygl":
            tokens = tokens[1:]
        else:
            return None
        return tokens

    @staticmethod
    def _tokenize(message: str) -> list[str] | None:
        """Split on whitespace, with only explicit double-quoted values.

        This is a command argument grammar, not a shell: there are no escape,
        interpolation, concatenation, or command-separator rules. A quoted
        positional token or a quoted ``key=value`` value must end at the next
        whitespace boundary. Apostrophes remain ordinary characters.
        """
        tokens: list[str] = []
        index = 0
        length = len(message)
        while index < length:
            while index < length and message[index].isspace():
                index += 1
            if index >= length:
                break

            start = index
            if message[index] == '"':
                closing = message.find('"', start + 1)
                if closing < 0:
                    return None
                if closing + 1 < length and not message[closing + 1].isspace():
                    return None
                tokens.append(message[start + 1 : closing])
                index = closing + 1
                continue

            while index < length and not message[index].isspace():
                if message[index] == '"':
                    break
                index += 1
            raw = message[start:index]

            if index < length and message[index] == '"':
                # Only key="value with spaces" is allowed inside an
                # assignment token; positional token concatenation is not.
                if not raw.endswith("=") or raw.count("=") != 1:
                    return None
                value_start = index + 1
                closing = message.find('"', value_start)
                if closing < 0:
                    return None
                if closing + 1 < length and not message[closing + 1].isspace():
                    return None
                tokens.append(f"{raw}{message[value_start:closing]}")
                index = closing + 1
                continue
            tokens.append(raw)
        return tokens

    @classmethod
    def _parse_module_command(
        cls, module: RegisteredModule, tokens: list[str]
    ) -> CommandInvocation | None:
        if not tokens:
            return None
        matches = [
            command
            for command in module.manifest.commands
            if cls._path_tokens(command.operation_path)
            and tuple(tokens[: len(cls._path_tokens(command.operation_path))])
            == cls._path_tokens(command.operation_path)
        ]
        if not matches:
            return None
        command = max(
            matches, key=lambda item: len(cls._path_tokens(item.operation_path))
        )
        path = cls._path_tokens(command.operation_path)
        argument_tokens = tokens[len(path) :]
        capability = next(
            (
                item
                for item in module.manifest.capabilities
                if item.capability_id == command.capability_id
            ),
            None,
        )
        if capability is None:
            return None
        parameters = cls._parse_parameters(command, capability, argument_tokens)
        if parameters is None:
            return None
        return CommandInvocation(module.module_id, command.operation_path, parameters)

    @staticmethod
    def _path_tokens(operation_path: str) -> tuple[str, ...]:
        return tuple(operation_path.split())

    @staticmethod
    def _parse_parameters(
        command: CommandDescriptor,
        capability: CapabilityDescriptor,
        values: list[str],
    ) -> dict[str, object] | None:
        mapping = tuple(command.parameter_mapping.items())
        schema = capability.input_schema
        properties = schema["properties"]
        required = set(schema["required"])
        result: dict[str, object] = {}
        mapped: dict[str, object] = {}
        field_schemas: dict[str, Mapping[str, object]] = {}
        argument_fields: dict[str, str] = {}
        for argument_name, field_name in mapping:
            field_schema = properties.get(field_name)
            if (
                not isinstance(field_schema, Mapping)
                or argument_name in argument_fields
                or field_name in field_schemas
            ):
                return None
            field_schemas[field_name] = field_schema
            argument_fields[argument_name] = field_name

        # `name=value` is the only way to disambiguate optional fields around
        # a free-text positional tail. Parse only declared CLI argument names;
        # unknown and duplicate assignments fail before schema validation.
        positionals: list[str] = []
        assigned: set[str] = set()
        for token in values:
            if "=" not in token:
                positionals.append(token)
                continue
            argument_name, raw = token.split("=", 1)
            field_name = argument_fields.get(argument_name)
            if field_name is None or argument_name in assigned:
                return None
            try:
                value = AstrBotCommandBridge._parse_value(
                    raw, field_schemas[field_name]
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                return None
            result[argument_name] = value
            mapped[field_name] = value
            assigned.add(argument_name)

        required_positions = [
            index
            for index, (_argument_name, field_name) in enumerate(mapping)
            if field_name in required
        ]
        tail_index: int | None = None
        if required_positions:
            candidate = required_positions[-1]
            candidate_field = mapping[candidate][1]
            if field_schemas[candidate_field].get("type") == "string":
                tail_index = candidate

        position = 0
        for index, (argument_name, field_name) in enumerate(mapping):
            if argument_name in assigned:
                continue
            field_schema = field_schemas[field_name]
            is_required = field_name in required
            raw: str | None = None

            if tail_index is not None and index == tail_index:
                raw = " ".join(positionals[position:])
                position = len(positionals)
                if not raw:
                    if is_required:
                        return None
                    continue
            elif tail_index is not None and index > tail_index:
                # Bare tokens after the required text would be ambiguous with
                # the free-text tail. Optional fields remain available as
                # explicit key=value assignments above.
                continue
            elif tail_index is not None and index < tail_index and not is_required:
                # An optional prefix before a free-text tail is likewise only
                # accepted by key=value, never guessed from token counts.
                continue
            elif position < len(positionals):
                remaining_required = sum(
                    1
                    for later_index, (_later_argument, later_field) in enumerate(
                        mapping[index + 1 :], start=index + 1
                    )
                    if later_field in required
                    and mapping[later_index][0] not in assigned
                )
                if is_required or len(positionals) - position > remaining_required:
                    raw = positionals[position]
                    position += 1
                elif is_required:
                    return None
            elif is_required:
                return None

            if raw is None:
                if is_required:
                    return None
                continue
            try:
                value = AstrBotCommandBridge._parse_value(raw, field_schema)
            except (TypeError, ValueError, json.JSONDecodeError):
                return None
            result[argument_name] = value
            mapped[field_name] = value
            assigned.add(argument_name)

        if position != len(positionals):
            return None
        try:
            validate_parameters(capability, mapped)
        except (TypeError, ValueError):
            return None
        return result

    @staticmethod
    def _parse_value(raw: str, schema: Mapping[str, object]) -> object:
        kind = schema["type"]
        if kind == "string":
            return raw
        if kind == "integer":
            if re.fullmatch(r"-?(?:0|[1-9][0-9]*)", raw) is None:
                raise ValueError("invalid integer")
            return int(raw)
        if kind == "number":
            value = float(raw)
            if not math.isfinite(value):
                raise ValueError("non-finite number")
            return value
        if kind == "boolean":
            if raw == "true":
                return True
            if raw == "false":
                return False
            raise ValueError("invalid boolean")
        if kind in {"array", "object"}:
            value = json.loads(
                raw,
                parse_constant=lambda _constant: (_ for _ in ()).throw(
                    ValueError("non-finite JSON number")
                ),
            )
            if kind == "array" and not isinstance(value, list):
                raise ValueError("expected JSON array")
            if kind == "object" and not isinstance(value, dict):
                raise ValueError("expected JSON object")
            return value
        raise ValueError("unsupported command parameter type")


__all__ = ["AstrBotCommandBridge", "CommandInvocation", "MessageTextEvent"]
