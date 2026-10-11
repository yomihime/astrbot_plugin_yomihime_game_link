"""Canonical DTOs cross real Gateway, renderer and persisted decode receivers."""

import json
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from ygl_test_subject.infrastructure.sqlite.repositories_subscriptions import (
    _encode,
    _load,
)
from ygl_test_subject.presentation.rendering import RenderingFailure

import tests.core.test_invocation as invocation_fixtures
import yomihime_game_link_sdk as ygl
from tests.presentation.test_rendering import _limits, _renderer


def blocks():
    instant = ygl.TimeValue(datetime(2026, 1, 1, tzinfo=UTC), "UTC")
    return (
        (ygl.TextBlock("text"), {"required": "yes"}),
        (
            ygl.FieldsBlock(
                {"money": ygl.MoneyValue(Decimal("1.2"), "USD"), "time": instant}
            ),
            {"fields": {"n": Decimal("NaN")}},
        ),
        (
            ygl.MetricsBlock({"money": ygl.MoneyValue(1, "USD")}),
            {"metrics": {"money": ygl.MoneyValue(1, "")}},
        ),
        (ygl.TableBlock(("column",), ((1,),)), {"rows": ((1, 2),)}),
        (
            ygl.ItemGridBlock((ygl.GridItem("item", 1),)),
            {"items": (ygl.GridItem("private", visibility=ygl.Privacy.PRIVATE),)},
        ),
        (ygl.ImageBlock("asset", "image"), {"asset_id": "../private"}),
        (
            ygl.SeriesBlock(((instant, ygl.NumberValue(1)),)),
            {"points": ((ygl.TimeValue(datetime(2026, 1, 1)), ygl.NumberValue(1)),)},
        ),
        (
            ygl.LinksBlock((ygl.Link("docs", "https://example.test"),)),
            {"links": (ygl.Link("", "https://example.test"),)},
        ),
        (ygl.CommandsBlock(("/status",)), {"commands": (object(),)}),
        (
            ygl.UnknownBlock("future", fallback_text="safe fallback"),
            {"required": True, "fallback_text": None},
        ),
    )


class ReceivingTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_display_block_crosses_gateway_and_rejects_nested_forgery(self):
        fixture = invocation_fixtures.GatewayTests()
        fixture.setUp()
        handler, view = await fixture._ready()
        gateway = fixture._gateway()
        for good, mutation in blocks():
            for bad in (False, True):
                block = replace(good, **mutation) if bad else good
                handler.result = ygl.CapabilityResult(
                    "result",
                    ygl.ResultStatus.SUCCESS,
                    ygl.DisplayDocument("title", "subject", (block,)),
                )
                with self.subTest(block=type(good).__name__, bad=bad):
                    result = await gateway.invoke_command(view, "查询", {"id": "one"})
                    if bad:
                        self.assertIs(result.status, ygl.ResultStatus.ERROR)
                        self.assertIsNone(result.document)
                        self.assertIsNone(result.model_facts)
                    else:
                        self.assertIs(result.status, ygl.ResultStatus.SUCCESS)
                        self.assertIs(
                            type(result.document.ordered_blocks[0]), type(good)
                        )
        self.assertEqual(len(handler.calls), 20)

    async def test_renderer_and_actual_b04_decoder_reject_the_same_nested_constraints(
        self,
    ):
        for good, mutation in blocks():
            document = ygl.DisplayDocument("title", "subject", (good,))
            with self.subTest(block=type(good).__name__, phase="positive"):
                rendered = await _renderer().render(document, limits=_limits())
                self.assertIs(type(rendered), ygl.DisplayOutput)
                decoded = _load(json.dumps(_encode(document)))
                self.assertIs(type(decoded), ygl.DisplayDocument)
                self.assertIs(type(decoded.ordered_blocks[0]), type(good))
            if type(good) is ygl.CommandsBlock:
                mutation = {"commands": (1,)}
            invalid = ygl.DisplayDocument(
                "title", "subject", (replace(good, **mutation),)
            )
            with self.subTest(block=type(good).__name__, phase="negative"):
                with self.assertRaises(RenderingFailure) as raised:
                    await _renderer().render(invalid, limits=_limits())
                self.assertIsNone(raised.exception.__cause__)
                self.assertIsNone(raised.exception.__context__)
                with self.assertRaises(ValueError):
                    _load(json.dumps(_encode(invalid)))

    async def test_digest_batch_association_revision_privacy_and_limits_reach_renderer(
        self,
    ):
        document = ygl.DisplayDocument("title", "subject", (ygl.TextBlock("text"),))
        member = ygl.DisplayBatchMember(
            ygl.DigestMember("subscription", 1, "event", 1), document
        )
        output = await _renderer().render_batch(
            ygl.DisplayBatch((member,), ygl.DisplayAudience.PUBLIC), _limits()
        )
        self.assertIs(type(output), ygl.DisplayOutput)
        for batch in (
            ygl.DisplayBatch((member, member), ygl.DisplayAudience.PUBLIC),
            ygl.DisplayBatch(
                (replace(member, member=replace(member.member, event_version=True)),),
                ygl.DisplayAudience.PUBLIC,
            ),
            ygl.DisplayBatch(
                (
                    replace(
                        member, document=replace(document, privacy=ygl.Privacy.PRIVATE)
                    ),
                ),
                ygl.DisplayAudience.PUBLIC,
            ),
        ):
            with self.assertRaises(RenderingFailure):
                await _renderer().render_batch(batch, _limits())
        with self.assertRaises(RenderingFailure):
            await _renderer().render(document, limits=ygl.DisplayLimits(True, 1))


class RegistrationReceivingTests(unittest.TestCase):
    def test_mutated_nested_declarations_reject_before_registry_publication(self):
        from ygl_test_subject.core.registry import Registry, RegistryError

        from tests.core.test_registry import _handlers, _module, _package

        def base():
            module = _module("main", "main", tool_name="status")
            object.__setattr__(
                module, "config_fields", (ygl.ConfigField("region", default="global"),)
            )
            object.__setattr__(
                module, "sources", (ygl.SourceDeclaration("source", "example.test"),)
            )
            object.__setattr__(
                module,
                "collections",
                (
                    ygl.CollectionDescriptor(
                        "items",
                        1,
                        ygl.OwnershipKind.USER,
                        (ygl.CollectionIndex("value", "value"),),
                    ),
                ),
            )
            object.__setattr__(
                module,
                "display",
                ygl.ModuleDisplay("Display", localized_names={"en-US": "Name"}),
            )
            return _package("sample", module), _handlers("record.query")

        registry = Registry()
        package, handlers = base()
        registry.register_package(package, {"main": handlers})
        self.assertEqual(registry.snapshot().revision, 1)
        mutations = (
            lambda p, h: object.__setattr__(p, "contract_version", "1.8.0"),
            lambda p, h: object.__setattr__(
                p.modules[0].capabilities[0], "output_version", "2.0"
            ),
            lambda p, h: object.__setattr__(
                p.modules[0].capabilities[0], "input_schema", {"type": "unknown"}
            ),
            lambda p, h: object.__setattr__(
                p.modules[0].commands[0], "capability_id", "undeclared"
            ),
            lambda p, h: object.__setattr__(
                p.modules[0].tools[0], "parameter_mapping", {"bad": object()}
            ),
            lambda p, h: object.__setattr__(
                p.modules[0].capabilities[0],
                "required_capabilities",
                (ygl.CapabilityReference("wrong", "read"),),
            ),
            lambda p, h: object.__setattr__(
                p.modules[0].config_fields[0], "sensitive", "yes"
            ),
            lambda p, h: object.__setattr__(
                p.modules[0].sources[0], "host", "https://example.test"
            ),
            lambda p, h: object.__setattr__(
                p.modules[0].collections[0].indexes[0], "field", "../private"
            ),
            lambda p, h: object.__setattr__(
                p.modules[0].collections[0], "owner_kind", "user"
            ),
            lambda p, h: object.__setattr__(
                p.modules[0].display, "localized_names", {"en-US": "a", "EN-us": "b"}
            ),
            lambda p, h: object.__setattr__(h, "capabilities", {1: object()}),
        )
        for mutate in mutations:
            package, handlers = base()
            mutate(package, handlers)
            registry = Registry()
            before = registry.snapshot()
            with self.subTest(mutation=mutations.index(mutate)):
                with self.assertRaises((RegistryError, ValueError, TypeError)):
                    registry.register_package(package, {"main": handlers})
                self.assertIs(registry.snapshot(), before)


class HealthReceivingTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_candidate_health_rejects_nested_invalid_status_before_admission(
        self,
    ):
        from uuid import uuid4

        from ygl_test_subject.core.lifecycle import LifecycleController

        from tests.core.test_lifecycle import _Instance, _registry

        for status in (ygl.HealthStatus.AVAILABLE, "available"):
            registry = _registry()
            controller = LifecycleController(registry)
            module = registry.snapshot().module("pkg/mod")

            class Instance(_Instance):
                async def check_health(self):
                    return ygl.HealthReport({"read": ygl.CapabilityHealth(status)})

            instance = Instance(module.handlers)
            install_id = uuid4().hex
            handlers = controller.adopt_candidate(
                "pkg", module.manifest, install_id, instance
            )
            controller.install_dormant("pkg", "pkg/mod", install_id, instance, handlers)
            if type(status) is ygl.HealthStatus:
                run_id = uuid4().hex
                identity, health = await controller.start_candidate("pkg/mod", run_id)
                self.assertIs(type(health), ygl.HealthReport)
                controller.publish_committed_intent(
                    "pkg/mod", run_id, identity, True, registry.snapshot().revision
                )
                await controller.stop("pkg/mod")
            else:
                with self.assertRaises((TypeError, ValueError)):
                    await controller.start_candidate("pkg/mod", uuid4().hex)
                self.assertEqual(instance.stopped, 1)
                from ygl_test_subject.core.contracts.administration import (
                    ModuleLifecycle,
                )

                self.assertIsNot(
                    controller.state("pkg/mod").lifecycle, ModuleLifecycle.ACTIVE
                )
