from __future__ import annotations

import unittest

from ygl_test_subject.api.manifests import (
    CapabilityDescriptor,
    CapabilityEffect,
    CommandDescriptor,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
    ToolDescriptor,
)
from ygl_test_subject.api.services import ModuleHandlers
from ygl_test_subject.api.storage import OwnershipKind
from ygl_test_subject.api.subscriptions import (
    ScheduleDescriptor,
    ScheduleTrigger,
    SubscriptionDescriptor,
)
from ygl_test_subject.api.version import CONTRACT_VERSION
from ygl_test_subject.core.registry import (
    Registry,
    RegistryError,
)


class _Handler:
    async def invoke(self, context, parameters):  # pragma: no cover - never run
        return None


class _NotAHandler:
    pass


class _Collector:
    def normalize(self, parameters):
        return None

    async def collect(self, context, parameters, previous):
        return None


class _NoNormalizeCollector:
    async def collect(self, context, parameters, previous):
        return None


class _NoCollectCollector:
    def normalize(self, parameters):
        return None


class _ExplodingNormalizeCollector:
    @property
    def normalize(self):
        raise RuntimeError("sensitive implementation detail")

    async def collect(self, context, parameters, previous):
        return None


class _ExplodingCollectCollector:
    def normalize(self, parameters):
        return None

    @property
    def collect(self):
        raise RuntimeError("sensitive implementation detail")


class _Evaluator:
    def evaluate(self, subscription, observation, previous_state):
        return None


class _NoEvaluateEvaluator:
    pass


class _ExplodingEvaluateEvaluator:
    @property
    def evaluate(self):
        raise RuntimeError("sensitive implementation detail")


def _module(
    module_id: str,
    route: str,
    *,
    capability_id: str = "record.query",
    tool_name: str | None = None,
    operation_path: str = "查询",
    schedules: tuple[ScheduleDescriptor, ...] = (),
    subscriptions: tuple[SubscriptionDescriptor, ...] = (),
) -> ModuleManifest:
    capability = CapabilityDescriptor(
        capability_id=capability_id,
        input_schema={"type": "object", "additionalProperties": False},
        invocation_policy=InvocationPolicy.NATURAL_LANGUAGE_ALLOWED,
        effect=CapabilityEffect.READ_ONLY,
    )
    tools = (
        ()
        if tool_name is None
        else (
            ToolDescriptor(
                name=tool_name,
                capability_id=capability_id,
                parameter_mapping={},
                description="A test tool",
            ),
        )
    )
    return ModuleManifest(
        module_id=module_id,
        route=route,
        category=ModuleCategory.GAME,
        factory_entry="test.module:Factory",
        module_version="0.1.0",
        capabilities=(capability,),
        commands=(
            CommandDescriptor(
                operation_path=operation_path,
                capability_id=capability_id,
                parameter_mapping={},
                help_text="A test command",
            ),
        ),
        tools=tools,
        schedules=schedules,
        subscriptions=subscriptions,
    )


def _package(
    package_id: str,
    *modules: ModuleManifest,
) -> PackageManifest:
    return PackageManifest(
        package_id=package_id,
        package_version="0.1.0",
        contract_version=CONTRACT_VERSION,
        modules=modules,
        author="Tests",
        license="AGPL-3.0",
        source="offline tests",
    )


def _handlers(*capability_ids: str) -> ModuleHandlers:
    return ModuleHandlers(
        capabilities={capability_id: _Handler() for capability_id in capability_ids},
        collectors={},
        evaluators={},
    )


def _schedule(collector_id: str = "prices") -> ScheduleDescriptor:
    return ScheduleDescriptor(
        collector_id=collector_id,
        key_version=1,
        source_id="steam",
        data_version=1,
        input_schema={"type": "object", "properties": {}, "required": []},
        shared_scope=OwnershipKind.PUBLIC,
        trigger=ScheduleTrigger.PERIODIC,
        minimum_interval_seconds=30,
        default_interval_seconds=60,
        interval_config_key="prices_interval",
    )


def _subscription(
    collector_id: str = "prices", matcher_id: str = "price_matcher"
) -> SubscriptionDescriptor:
    return SubscriptionDescriptor(
        type_id="price_alert",
        collector_id=collector_id,
        matcher_id=matcher_id,
        filter_schema={"type": "object", "properties": {}, "required": []},
        notification_modes=("instant",),
    )


class RegistryTests(unittest.TestCase):
    def test_rg01_empty_and_multiple_packages_have_no_module_limit(self) -> None:
        registry = Registry()
        empty = registry.snapshot()
        self.assertEqual(empty.revision, 0)
        self.assertEqual(dict(empty.modules), {})
        self.assertEqual(dict(empty.routes), {})
        self.assertEqual(dict(empty.tools), {})

        first = _package(
            "first",
            _module("one", "one", tool_name="first_one"),
            _module("two", "two", capability_id="other.read"),
        )
        second = _package(
            "second",
            _module("one", "second_one", tool_name="second_one"),
        )
        registry.register_package(
            first,
            {"one": _handlers("record.query"), "two": _handlers("other.read")},
        )
        registry.register_package(second, {"one": _handlers("record.query")})

        snapshot = registry.snapshot()
        self.assertEqual(
            set(snapshot.modules), {"first/one", "first/two", "second/one"}
        )
        self.assertEqual(snapshot.routes["one"], "first/one")
        self.assertEqual(snapshot.routes["second_one"], "second/one")
        self.assertEqual(snapshot.tools["first_one"], ("first/one", "record.query"))

    def test_rg02_rejects_cross_package_collisions_but_allows_local_operations(
        self,
    ) -> None:
        registry = Registry()
        registry.register_package(
            _package(
                "first",
                _module("one", "one", tool_name="first_tool"),
                _module("two", "two", tool_name="second_tool"),
            ),
            {"one": _handlers("record.query"), "two": _handlers("record.query")},
        )

        duplicate_package = _package(
            "first", _module("new", "new", tool_name="new_tool")
        )
        duplicate_route = _package(
            "second", _module("new", "one", tool_name="new_tool_2")
        )
        duplicate_tool = _package(
            "third", _module("new", "three", tool_name="first_tool")
        )
        for package, handlers in (
            (duplicate_package, {"new": _handlers("record.query")}),
            (duplicate_route, {"new": _handlers("record.query")}),
            (duplicate_tool, {"new": _handlers("record.query")}),
        ):
            with self.subTest(package=package.package_id):
                with self.assertRaises(RegistryError):
                    registry.register_package(package, handlers)

        self.assertEqual(registry.snapshot().revision, 1)
        self.assertEqual(
            registry.snapshot().module_for_route("one").module_id, "first/one"
        )
        self.assertEqual(
            registry.snapshot().module_for_route("two").module_id, "first/two"
        )

    def test_rg03_rejects_missing_extra_and_non_callable_capability_handlers(
        self,
    ) -> None:
        registry = Registry()
        package = _package("pkg", _module("mod", "mod"))

        with self.assertRaises(RegistryError):
            registry.register_package(package, {})
        with self.assertRaises(RegistryError):
            registry.register_package(
                package,
                {"mod": _handlers("record.query", "extra.capability")},
            )
        with self.assertRaises(RegistryError):
            registry.register_package(
                package,
                {
                    "mod": ModuleHandlers(
                        capabilities={"record.query": _NotAHandler()},
                        collectors={},
                        evaluators={},
                    )
                },
            )
        self.assertEqual(registry.snapshot().revision, 0)
        self.assertEqual(dict(registry.snapshot().modules), {})

    def test_rg04_failed_update_and_source_mutation_do_not_change_snapshot(
        self,
    ) -> None:
        registry = Registry()
        handlers = {"mod": _handlers("record.query")}
        package = _package("pkg", _module("mod", "mod", tool_name="test_tool"))
        original = registry.register_package(package, handlers)
        handlers.clear()
        self.assertEqual(original.tools["test_tool"], ("pkg/mod", "record.query"))

        with self.assertRaises(RegistryError):
            registry.register_package(
                _package("other", _module("mod", "mod", tool_name="other_tool")),
                {"mod": _handlers("record.query")},
            )
        self.assertIs(registry.snapshot(), original)
        self.assertEqual(original.revision, 1)
        with self.assertRaises(TypeError):
            original.modules["pkg/mod"] = original.modules["pkg/mod"]

    def test_rg05_enabled_projection_advances_directory_revision_only(self) -> None:
        registry = Registry()
        registry.register_package(
            _package("pkg", _module("mod", "mod")),
            {"mod": _handlers("record.query")},
        )
        disabled = registry.snapshot()
        self.assertFalse(disabled.modules["pkg/mod"].enabled)
        self.assertEqual(disabled.modules["pkg/mod"].epoch, 0)

        self.assertIs(registry.set_enabled("pkg/mod", False), disabled)
        self.assertIs(registry.snapshot(), disabled)
        enabled = registry.set_enabled("pkg/mod", True)
        self.assertEqual(enabled.revision, 2)
        self.assertTrue(enabled.modules["pkg/mod"].enabled)
        self.assertEqual(enabled.modules["pkg/mod"].epoch, 0)
        self.assertFalse(disabled.modules["pkg/mod"].enabled)
        self.assertEqual(disabled.modules["pkg/mod"].epoch, 0)

        disabled_again = registry.set_enabled("pkg/mod", False)
        self.assertEqual(disabled_again.revision, 3)
        self.assertEqual(disabled_again.modules["pkg/mod"].epoch, 0)
        self.assertTrue(enabled.modules["pkg/mod"].enabled)
        with self.assertRaises(RegistryError):
            registry.set_enabled("missing/module", True)

    def test_rg05_malformed_lookup_keys_use_registry_error_without_mutation(
        self,
    ) -> None:
        registry = Registry()
        registry.register_package(
            _package("pkg", _module("mod", "mod", tool_name="test_tool")),
            {"mod": _handlers("record.query")},
        )
        original = registry.snapshot()

        for operation in (
            lambda: original.module([]),
            lambda: original.module_for_route([]),
            lambda: original.tool([]),
            lambda: registry.set_enabled([], True),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(RegistryError):
                    operation()

        self.assertIs(registry.snapshot(), original)
        self.assertEqual(registry.snapshot().revision, 1)

    def test_rg06_schedule_handlers_match_declarations_and_methods(self) -> None:
        scheduled = _module(
            "mod",
            "mod",
            schedules=(_schedule(),),
            subscriptions=(_subscription(),),
        )

        def handlers(*, collectors=None, evaluators=None) -> ModuleHandlers:
            return ModuleHandlers(
                capabilities={"record.query": _Handler()},
                collectors={"prices": _Collector()}
                if collectors is None
                else collectors,
                evaluators={"price_matcher": _Evaluator()}
                if evaluators is None
                else evaluators,
            )

        invalid_handlers = (
            handlers(collectors={}),
            handlers(collectors={"prices": _Collector(), "extra": _Collector()}),
            handlers(evaluators={}),
            handlers(
                evaluators={
                    "price_matcher": _Evaluator(),
                    "extra": _Evaluator(),
                }
            ),
            handlers(collectors={"prices": _NoNormalizeCollector()}),
            handlers(collectors={"prices": _NoCollectCollector()}),
            handlers(evaluators={"price_matcher": _NoEvaluateEvaluator()}),
        )
        for item in invalid_handlers:
            with self.subTest(handlers=item):
                registry = Registry()
                with self.assertRaises(RegistryError):
                    registry.register_package(_package("pkg", scheduled), {"mod": item})
                self.assertEqual(registry.snapshot().revision, 0)
                self.assertEqual(dict(registry.snapshot().modules), {})

        getter_failures = (
            (
                handlers(collectors={"prices": _ExplodingNormalizeCollector()}),
                "collector normalize",
            ),
            (
                handlers(collectors={"prices": _ExplodingCollectCollector()}),
                "collector collect",
            ),
            (
                handlers(evaluators={"price_matcher": _ExplodingEvaluateEvaluator()}),
                "evaluator evaluate",
            ),
        )
        for item, label in getter_failures:
            with self.subTest(getter=label):
                registry = Registry()
                original = registry.snapshot()
                with self.assertRaises(RegistryError) as raised:
                    registry.register_package(_package("pkg", scheduled), {"mod": item})
                self.assertEqual(str(raised.exception), f"{label} is not callable")
                self.assertNotIn(
                    "sensitive implementation detail", str(raised.exception)
                )
                self.assertIsNone(raised.exception.__cause__)
                self.assertIsNone(raised.exception.__context__)
                self.assertIs(registry.snapshot(), original)
                self.assertEqual(original.revision, 0)
                self.assertEqual(dict(registry.snapshot().modules), {})

        registry = Registry()
        registry.register_package(_package("pkg", scheduled), {"mod": handlers()})
        self.assertEqual(registry.snapshot().revision, 1)
        self.assertIn("pkg/mod", registry.snapshot().modules)


if __name__ == "__main__":
    unittest.main()
