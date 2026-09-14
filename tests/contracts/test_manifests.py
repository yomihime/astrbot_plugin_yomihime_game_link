"""Contract checks for validated static registration declarations."""

from __future__ import annotations

import math
import unittest
from dataclasses import FrozenInstanceError
from types import MappingProxyType

from ygl_test_subject.api.contexts import InvocationOrigin, InvocationView
from ygl_test_subject.api.manifests import (
    CapabilityDescriptor,
    CapabilityEffect,
    CommandDescriptor,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
    PrivacyFloor,
    ToolDescriptor,
)
from ygl_test_subject.api.version import CONTRACT_VERSION


def _capability(
    capability_id: str = "lookup", **changes: object
) -> CapabilityDescriptor:
    values: dict[str, object] = {
        "capability_id": capability_id,
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        "invocation_policy": InvocationPolicy.NATURAL_LANGUAGE_ALLOWED,
        "effect": CapabilityEffect.READ_ONLY,
        "privacy_floor": PrivacyFloor.PUBLIC,
    }
    values.update(changes)
    return CapabilityDescriptor(**values)  # type: ignore[arg-type]


def _module(**changes: object) -> ModuleManifest:
    values: dict[str, object] = {
        "module_id": "catalog",
        "route": "catalog",
        "category": ModuleCategory.GAME,
        "factory_entry": "example.catalog:Factory",
        "module_version": "1.0.0",
        "capabilities": (_capability(),),
    }
    values.update(changes)
    return ModuleManifest(**values)  # type: ignore[arg-type]


class ManifestContractTests(unittest.TestCase):
    def test_valid_package_is_frozen_and_uses_immutable_mappings(self) -> None:
        parameters = {"query": "query"}
        module = _module(
            commands=(
                CommandDescriptor("catalog search", "lookup", parameters, "Search"),
            ),
            tools=(ToolDescriptor("catalog_search", "lookup", parameters, "Search"),),
        )
        package = PackageManifest(
            "yomihime", "1.0.0", CONTRACT_VERSION, (module,), "YGL", "GPL-3.0", "local"
        )
        parameters["changed"] = "changed"
        self.assertIsInstance(
            package.modules[0].commands[0].parameter_mapping, MappingProxyType
        )
        self.assertNotIn("changed", package.modules[0].commands[0].parameter_mapping)
        with self.assertRaises(TypeError):
            package.modules[0].commands[0].parameter_mapping["x"] = "x"  # type: ignore[index]
        with self.assertRaises(FrozenInstanceError):
            package.package_id = "other"  # type: ignore[misc]

    def test_invalid_ids_versions_and_reserved_help_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _capability("UPPER")
        with self.assertRaises(ValueError):
            _module(route="help")
        with self.assertRaises(ValueError):
            CommandDescriptor("catalog help", "lookup", {}, "Help")
        with self.assertRaises(ValueError):
            PackageManifest("yomihime", "1.0", CONTRACT_VERSION, (), "a", "b", "c")
        with self.assertRaises(ValueError):
            PackageManifest("yomihime", "1.0.0", "2.0.0", (), "a", "b", "c")
        with self.assertRaises(TypeError):
            _module(category="catalogue")  # type: ignore[arg-type]

    def test_commands_are_unique_per_route_and_allow_localized_tokens(self) -> None:
        command = CommandDescriptor("查询 玩家", "lookup", {"玩家": "query"}, "Search")
        steam = _module(commands=(command,))
        dota = _module(module_id="dota", route="dota", commands=(command,))
        package = PackageManifest(
            "yomihime", "1.0.0", CONTRACT_VERSION, (steam, dota), "a", "b", "c"
        )
        self.assertEqual("yomihime/catalog", package.global_module_id("catalog"))
        with self.assertRaises(ValueError):
            CommandDescriptor("/catalog query", "lookup", {}, "Search")

    def test_duplicate_declarations_and_unknown_references_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _module(capabilities=(_capability(), _capability()))
        with self.assertRaises(ValueError):
            _module(
                commands=(CommandDescriptor("catalog search", "missing", {}, "Search"),)
            )
        one = _module()
        two = _module(module_id="other", route="catalog")
        with self.assertRaises(ValueError):
            PackageManifest(
                "yomihime", "1.0.0", CONTRACT_VERSION, (one, two), "a", "b", "c"
            )

    def test_tool_cannot_expose_write_or_command_only_capability(self) -> None:
        tool = ToolDescriptor("catalog_change", "change", {}, "Change")
        with self.assertRaisesRegex(ValueError, "write"):
            _module(
                capabilities=(_capability("change", effect=CapabilityEffect.WRITE),),
                tools=(tool,),
            )
        with self.assertRaisesRegex(ValueError, "command_only"):
            _module(
                capabilities=(
                    _capability(
                        "change", invocation_policy=InvocationPolicy.COMMAND_ONLY
                    ),
                ),
                tools=(tool,),
            )

    def test_tool_cannot_expose_private_capability(self) -> None:
        private = _capability(
            "private_lookup",
            invocation_policy=InvocationPolicy.COMMAND_ONLY,
            privacy_floor=PrivacyFloor.PRIVATE,
        )
        tool = ToolDescriptor(
            "private_lookup", "private_lookup", {"query": "query"}, "Lookup"
        )
        with self.assertRaisesRegex(ValueError, "private"):
            _module(capabilities=(private,), tools=(tool,))
        with self.assertRaisesRegex(ValueError, "command_only"):
            _capability(privacy_floor=PrivacyFloor.PRIVATE)

    def test_parameter_mappings_cover_required_fields_once_and_in_direction(
        self,
    ) -> None:
        capability = _capability(
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "region": {"type": "string"},
                },
                "required": ["query", "region"],
            }
        )
        missing = CommandDescriptor(
            "catalog search", "lookup", {"term": "query"}, "Search"
        )
        unknown = CommandDescriptor(
            "catalog search", "lookup", {"term": "unknown", "area": "region"}, "Search"
        )
        repeated = CommandDescriptor(
            "catalog search",
            "lookup",
            {"term": "query", "again": "query", "area": "region"},
            "Search",
        )
        for descriptor in (missing, unknown, repeated):
            with self.subTest(descriptor=descriptor):
                with self.assertRaises(ValueError):
                    _module(capabilities=(capability,), commands=(descriptor,))

    def test_schema_enum_types_bounds_and_closed_objects_are_validated(self) -> None:
        for schema in (
            {"type": "integer", "enum": [True]},
            {"type": "integer", "enum": ["1"]},
            {"type": "string", "enum": [1]},
            {"type": "number", "enum": [math.inf]},
            {"type": "integer", "minimum": 1.5},
            {"type": "number", "minimum": 2, "maximum": 1},
            {"type": "string", "enum": ["a"], "minLength": 2},
            {"type": "object", "additionalProperties": True},
        ):
            with self.subTest(schema=schema):
                with self.assertRaises((TypeError, ValueError)):
                    _capability(input_schema=schema)
        with self.assertRaisesRegex(ValueError, "output_version"):
            _capability(output_version="1.0.1")


class InvocationViewContractTests(unittest.TestCase):
    def test_valid_context_and_optional_references(self) -> None:
        view = InvocationView(
            "request.1",
            InvocationOrigin.SCHEDULER,
            None,
            "conversation.1",
            "yomihime/catalog",
            2,
            3,
            12.5,
            grant_id="grant.1",
            grant_revision=4,
            subscription_id="sub.1",
            subscription_revision=5,
        )
        self.assertEqual(12.5, view.deadline)
        with self.assertRaises(FrozenInstanceError):
            view.origin = InvocationOrigin.COMMAND  # type: ignore[misc]

    def test_context_rejects_forged_values_and_unpaired_references(self) -> None:
        base = (
            "5c2da6e2-1d19-4f08-ab24-62eff2810024",
            InvocationOrigin.COMMAND,
            "123456",
            None,
            "yomihime/catalog",
            1,
            1,
        )
        for deadline in (0, -1.0, math.inf, math.nan, True):
            with self.subTest(deadline=deadline):
                with self.assertRaises((TypeError, ValueError)):
                    InvocationView(*base, deadline=deadline)
        with self.assertRaises(TypeError):
            InvocationView(*base, module_epoch=True)
        with self.assertRaises(ValueError):
            InvocationView(*base, grant_id="grant.1")
        with self.assertRaises(ValueError):
            InvocationView(*base, subscription_revision=1)
        with self.assertRaises(ValueError):
            InvocationView("bad\x00id", *base[1:])
        for module_id in ("pkg./module", "pkg/module.", "pkg-/module"):
            with self.subTest(module_id=module_id):
                with self.assertRaises(ValueError):
                    InvocationView(*base[:4], module_id, *base[5:])
