"""Contract checks for schedule and subscription registration declarations."""

from __future__ import annotations

import importlib
import inspect
import math
import unittest
from types import MappingProxyType
from typing import get_type_hints

from ygl_test_subject.api.manifests import (
    CapabilityDescriptor,
    CapabilityEffect,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PrivacyFloor,
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


def _capability() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        "lookup",
        {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        InvocationPolicy.NATURAL_LANGUAGE_ALLOWED,
        CapabilityEffect.READ_ONLY,
        privacy_floor=PrivacyFloor.PUBLIC,
    )


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
        "steam",
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


def _module(**changes: object) -> ModuleManifest:
    values: dict[str, object] = {
        "module_id": "steam",
        "route": "steam",
        "category": ModuleCategory.PLATFORM,
        "factory_entry": "example.steam:Factory",
        "module_version": "1.0.0",
        "capabilities": (_capability(),),
    }
    values.update(changes)
    return ModuleManifest(**values)  # type: ignore[arg-type]


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

    def test_sc07_public_annotations_resolve_without_cycles(self) -> None:
        for module_name in ("api.schema", "api.subscriptions", "api.manifests"):
            module = importlib.import_module("ygl_test_subject." + module_name)
            for name, obj in vars(module).items():
                if getattr(obj, "__module__", None) != module.__name__:
                    continue
                if inspect.isfunction(obj) or inspect.isclass(obj):
                    with self.subTest(module=module_name, name=name):
                        get_type_hints(obj)
                if inspect.isclass(obj):
                    for method_name, method in vars(obj).items():
                        if isinstance(method, property):
                            method = method.fget
                        if inspect.isfunction(method):
                            with self.subTest(
                                module=module_name, name=name, method=method_name
                            ):
                                get_type_hints(method)
