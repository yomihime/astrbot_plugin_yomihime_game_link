"""Contract checks for validated static registration declarations."""

from __future__ import annotations

import math
import unittest
from dataclasses import FrozenInstanceError
from types import MappingProxyType

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
from ygl_test_subject.api.schema import freeze_input_schema
from ygl_test_subject.api.storage import OwnerScope, OwnershipKind
from ygl_test_subject.api.subscriptions import (
    CollectionKey,
    NormalizedInput,
    ScheduleDescriptor,
    ScheduleTrigger,
    SubscriptionDescriptor,
)
from ygl_test_subject.api.version import CONTRACT_VERSION
from ygl_test_subject.examples.contracts import example_package


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

    def test_owner_floor_is_command_only_and_cannot_be_a_tool(self) -> None:
        owner = _capability(
            "owner_read",
            invocation_policy=InvocationPolicy.COMMAND_ONLY,
            privacy_floor=PrivacyFloor.OWNER,
        )
        module = _module(
            capabilities=(owner,),
            commands=(
                CommandDescriptor("read", "owner_read", {"query": "query"}, "Read"),
            ),
        )
        package = PackageManifest(
            "owner-tests",
            "1.0.0",
            CONTRACT_VERSION,
            (module,),
            "Tests",
            "MIT",
            "offline",
        )
        self.assertIs(
            package.modules[0].capabilities[0].privacy_floor, PrivacyFloor.OWNER
        )

        with self.assertRaisesRegex(ValueError, "command_only"):
            _capability("owner_read", privacy_floor=PrivacyFloor.OWNER)

        tool = ToolDescriptor("owner_read", "owner_read", {"query": "query"}, "Read")
        with self.assertRaisesRegex(ValueError, "owner"):
            _module(capabilities=(owner,), tools=(tool,))

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


def _schedule(
    collector_id: str = "prices",
    *,
    trigger: ScheduleTrigger = ScheduleTrigger.PERIODIC,
    minimum: float = 30,
    default: float | None = 60,
    config_key: str | None = "prices_interval",
    input_schema: dict[str, object] | None = None,
) -> ScheduleDescriptor:
    return ScheduleDescriptor(
        collector_id,
        1,
        "catalog",
        1,
        input_schema or {"type": "object", "properties": {}, "required": []},
        OwnershipKind.PUBLIC,
        trigger,
        minimum,
        default,
        config_key,
    )


def _subscription(
    type_id: str = "price_alert",
    *,
    collector_id: str = "prices",
    filter_schema: dict[str, object] | None = None,
    notification_modes: tuple[str, ...] = ("instant",),
) -> SubscriptionDescriptor:
    return SubscriptionDescriptor(
        type_id,
        collector_id,
        "price_matcher",
        filter_schema
        or {
            "type": "object",
            "properties": {"threshold": {"type": "number"}},
            "required": ["threshold"],
        },
        notification_modes,
    )


class ScheduleRegistrationTests(unittest.TestCase):
    def test_sc01_old_manifest_and_nested_declarations_are_immutable(self) -> None:
        old = _module()
        self.assertEqual(old.schedules, ())
        self.assertEqual(old.subscriptions, ())

        source = {"type": "object", "properties": {}, "required": []}
        schedule = _schedule(input_schema=source)
        subscription = _subscription()
        source["properties"] = {"changed": {"type": "string"}}
        module = _module(schedules=(schedule,), subscriptions=(subscription,))
        self.assertIsInstance(module.schedules[0].input_schema, MappingProxyType)
        with self.assertRaises(TypeError):
            module.schedules[0].input_schema["type"] = "array"  # type: ignore[index]
        with self.assertRaises(TypeError):
            module.subscriptions[0].filter_schema["type"] = "array"  # type: ignore[index]

    def test_sc02_rejects_duplicates_unknown_collectors_and_wrong_types(self) -> None:
        with self.assertRaisesRegex(ValueError, "schedule collectors"):
            _module(schedules=(_schedule(), _schedule()))
        with self.assertRaisesRegex(ValueError, "subscriptions"):
            _module(
                schedules=(_schedule(),),
                subscriptions=(_subscription(), _subscription()),
            )
        with self.assertRaisesRegex(ValueError, "undeclared collector"):
            _module(
                schedules=(_schedule(),),
                subscriptions=(_subscription(collector_id="missing"),),
            )
        with self.assertRaises(TypeError):
            _module(schedules=(object(),))  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            _module(subscriptions=(object(),))  # type: ignore[arg-type]

    def test_sc03_periodic_defaults_are_finite_positive_and_not_below_minimum(
        self,
    ) -> None:
        for changes in (
            {"default": None},
            {"default": 0},
            {"default": math.inf},
            {"default": 20, "minimum": 30},
            {"config_key": None},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                _schedule(**changes)

    def test_sc04_on_demand_has_no_periodic_configuration(self) -> None:
        self.assertEqual(
            _schedule(
                trigger=ScheduleTrigger.ON_DEMAND,
                default=None,
                config_key=None,
            ).trigger,
            ScheduleTrigger.ON_DEMAND,
        )
        for changes in ({"default": 60}, {"config_key": "refresh_interval"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                _schedule(
                    trigger=ScheduleTrigger.ON_DEMAND,
                    default=changes.get("default"),
                    config_key=changes.get("config_key"),
                )

    def test_sc05_schedule_and_filter_schema_share_closed_object_rules(self) -> None:
        with self.assertRaises(ValueError):
            _schedule(input_schema={"type": "array", "items": {"type": "string"}})
        with self.assertRaises(ValueError):
            _subscription(filter_schema={"type": "array", "items": {"type": "string"}})
        with self.assertRaises(ValueError):
            _schedule(input_schema={"type": "object", "additionalProperties": True})
        with self.assertRaises((TypeError, ValueError)):
            _subscription(
                filter_schema={
                    "type": "object",
                    "properties": {"mode": {"type": "string", "enum": [1]}},
                }
            )
        with self.assertRaises(ValueError):
            freeze_input_schema({"type": "string", "enum": ["a"], "minimum": 1})

    def test_sc06_notification_filter_differences_do_not_change_public_key(
        self,
    ) -> None:
        first = _subscription(notification_modes=("instant",))
        second = _subscription(
            type_id="price_digest",
            filter_schema={
                "type": "object",
                "properties": {"threshold": {"type": "number", "minimum": 5}},
                "required": ["threshold"],
            },
            notification_modes=("digest",),
        )
        self.assertNotEqual(first.filter_schema, second.filter_schema)
        public_key_first = CollectionKey(
            "example/steam",
            "prices",
            1,
            "steam",
            NormalizedInput({"app": 1}),
            OwnerScope.public(),
        )
        public_key_second = CollectionKey(
            "example/steam",
            "prices",
            1,
            "steam",
            NormalizedInput({"app": 1}),
            OwnerScope.public(),
        )
        self.assertEqual(public_key_first, public_key_second)
        private_key_first = CollectionKey(
            "example/steam",
            "prices",
            1,
            "steam",
            NormalizedInput({"app": 1}),
            OwnerScope.user("owner-1"),
        )
        private_key_second = CollectionKey(
            "example/steam",
            "prices",
            1,
            "steam",
            NormalizedInput({"app": 1}),
            OwnerScope.user("owner-2"),
        )
        self.assertNotEqual(private_key_first, private_key_second)


class ExampleManifestContractTests(unittest.TestCase):
    def test_example_package_resolves_its_scoped_capability(self):
        package = example_package()
        self.assertEqual(package.global_module_id("sample"), "example/sample")
        module = package.modules[0]
        self.assertEqual(
            module.commands[0].capability_id, module.tools[0].capability_id
        )
