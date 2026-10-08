"""R1 contract, lifecycle, bundle-resource and retained ownership regressions."""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from ygl_test_subject.adapters.astrbot.command_bridge import (
    AstrBotCommandBridge,
    CommandHelp,
    CommandInvocation,
)
from ygl_test_subject.core.help_catalog import HelpCatalog
from ygl_test_subject.core.lifecycle import LifecycleController
from ygl_test_subject.core.registry import Registry
from ygl_test_subject.extensions.page_resources import (
    MAX_PAGE_RESOURCE_BYTES,
    read_page_resources,
)
from ygl_test_subject.services.module_catalog import project_module_catalog
from ygl_test_subject.services.module_storage import ModuleStorageRouter

from tests.core.test_lifecycle import _Instance
from yomihime_sdk import PageDescriptor, PageResource
from yomihime_sdk.api.manifests import (
    CapabilityDescriptor,
    CapabilityEffect,
    CommandDescriptor,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
)
from yomihime_sdk.api.services import (
    CapabilityHealth,
    HealthReport,
    HealthStatus,
    ModuleHandlers,
)


def package():
    capability = CapabilityDescriptor(
        "query",
        {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        InvocationPolicy.COMMAND_AND_PUBLIC_WEB,
        CapabilityEffect.READ_ONLY,
    )
    raw = CommandDescriptor("search", "query", {}, "Search", "raw_tail", "text")
    structured = CommandDescriptor("search exact", "query", {"query": "text"}, "Exact")
    content = b"export const mount = () => {};\n"
    resource = PageResource("pages/dist/entry.js", hashlib.sha256(content).hexdigest())
    module = ModuleManifest(
        "demo",
        "demo",
        ModuleCategory.GAME,
        "module:Factory",
        "1.0.0",
        (capability,),
        commands=(raw, structured),
        pages=(
            PageDescriptor("search", "Search", resource.path, capability_id="query"),
        ),
        resources=(resource,),
    )
    return PackageManifest(
        "sample", "1.0.0", "1.5.0", (module,), "Tests", "MIT", "offline"
    ), content


class ContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_raw_tail_preserves_text_and_longest_structured_path_wins(self):
        manifest, _ = package()
        registry = Registry()

        class Handler:
            calls = 0

            async def invoke(self, *args):
                self.calls += 1
                raise AssertionError("help invoked a business handler")

        handler = Handler()
        handlers = ModuleHandlers({"query": handler}, {}, {})
        registry.register_package(manifest, {"demo": handlers})
        lifecycle = LifecycleController(registry, runtime_id="raw-tail-contract")
        bridge = AstrBotCommandBridge(registry)

        class Event:
            def __init__(self, text):
                self.text = text

            def get_message_str(self):
                return self.text

        def assert_unavailable():
            for text in ("/ygl demo search help", "/ygl demo search value"):
                action = bridge.parse(Event(text))
                self.assertIsInstance(action, str)
                self.assertNotIsInstance(action, CommandInvocation)
                self.assertNotIsInstance(action, CommandHelp)

        assert_unavailable()

        class Instance(_Instance):
            async def check_health(self):
                return HealthReport({"query": CapabilityHealth(HealthStatus.AVAILABLE)})

        instance = Instance(handlers)
        lifecycle.adopt_candidate("sample", manifest.modules[0], "install", instance)
        lifecycle.install_dormant(
            "sample", "sample/demo", "install", instance, handlers
        )
        identity, _ = await lifecycle.start_candidate("sample/demo", "start")
        lifecycle.publish_committed_intent(
            "sample/demo", "start", identity, True, registry.snapshot().revision
        )
        try:
            action = bridge.parse(Event('/ygl demo search "A  B"  name="C D"  '))
            self.assertIsInstance(action, CommandInvocation)
            self.assertEqual(action.parameters, {"text": '"A  B"  name="C D"  '})
            action = bridge.parse(Event("/ygl demo search help"))
            self.assertIsInstance(action, CommandHelp)
            self.assertNotIsInstance(action, CommandInvocation)
            self.assertEqual(handler.calls, 0)
            for tail in ('"help"', "help more"):
                action = bridge.parse(Event("/ygl demo search " + tail))
                self.assertIsInstance(action, CommandInvocation)
                self.assertEqual(action.parameters, {"text": tail})
            action = bridge.parse(Event('/ygl demo search exact "A B"'))
            self.assertIsInstance(action, CommandInvocation)
            self.assertEqual(
                (action.operation_path, dict(action.parameters)),
                ("search exact", {"query": "A B"}),
            )
        finally:
            await lifecycle.stop("sample/demo")
        assert_unavailable()
        self.assertEqual(handler.calls, 0)

    def test_raw_tail_cannot_bypass_schema_or_mapping(self):
        manifest, _ = package()
        module = manifest.modules[0]
        with self.assertRaises(ValueError):
            replace(module.commands[0], parameter_mapping={"text": "text"})
        with self.assertRaises(ValueError):
            replace(
                module,
                commands=(replace(module.commands[0], raw_tail_parameter="unknown"),),
            )
        with self.assertRaises(ValueError):
            replace(
                module,
                capabilities=(
                    replace(
                        module.capabilities[0],
                        input_schema={
                            "type": "object",
                            "properties": {"text": {"type": "integer"}},
                            "required": ["text"],
                        },
                    ),
                ),
            )

    def test_paths_hashes_references_and_namespace_are_closed(self):
        manifest, _ = package()
        for path in (
            "../entry.js",
            "/entry.js",
            "C:/entry.js",
            "https://host/entry.js",
            "pages\\entry.js",
            "pages//entry.js",
            "pages/private.json",
            "CON.js",
            "entry.js.map",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                PageResource(path, "a" * 64)
        with self.assertRaises(ValueError):
            PageResource("entry.js", "A" * 64)
        module = manifest.modules[0]
        with self.assertRaises(ValueError):
            replace(module, resources=())
        with self.assertRaises(ValueError):
            replace(
                manifest,
                modules=(module, replace(module, module_id="second", route="second")),
            )

    def test_resource_hash_link_and_size_negatives(self):
        manifest, content = package()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset = root / "pages/dist/entry.js"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(content)
            self.assertEqual(
                read_page_resources(root, manifest)["pages/dist/entry.js"], content
            )
            asset.write_bytes(content + b"changed")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                read_page_resources(root, manifest)
            with asset.open("wb") as stream:
                stream.truncate(MAX_PAGE_RESOURCE_BYTES + 1)
            with self.assertRaisesRegex(ValueError, "budget"):
                read_page_resources(root, manifest)
            asset.unlink()
            sentinel = root / "sentinel"
            sentinel.write_bytes(content)
            os.link(sentinel, asset)
            with self.assertRaisesRegex(ValueError, "hardlinks"):
                read_page_resources(root, manifest)

    def test_storage_is_stable_isolated_and_retained(self):
        with tempfile.TemporaryDirectory() as temporary:
            router = ModuleStorageRouter(Path(temporary) / "modules")
            first = router.paths("sample/demo")
            second = router.paths("sample/other")
            self.assertNotEqual(first.root, second.root)
            (Path(first.data) / "retained").write_bytes(b"data")
            self.assertEqual(router.paths("sample/demo"), first)
            self.assertTrue((Path(first.data) / "retained").exists())
            with self.assertRaises(ValueError):
                router.paths("../outside")


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_14_manifest_has_closed_catalog_without_pages_or_authority(
        self,
    ):
        manifest, _ = package()
        module = replace(
            manifest.modules[0],
            pages=(),
            resources=(),
            commands=(manifest.modules[0].commands[1],),
        )
        legacy = replace(manifest, contract_version="1.4.0", modules=(module,))

        class Handler:
            async def invoke(self, *args):
                raise AssertionError("metadata invoked legacy handler")

        registry = Registry()
        registry.register_package(
            legacy, {"demo": ModuleHandlers({"query": Handler()}, {}, {})}
        )
        lifecycle = LifecycleController(registry, runtime_id="legacy-test")
        catalog = project_module_catalog(
            registry.snapshot(),
            {"sample/demo": lifecycle.state("sample/demo")},
            {"sample/demo": lifecycle.current_identity("sample/demo")},
            runtime_state="ready",
        )
        self.assertEqual(catalog["schema_version"], 1)
        self.assertEqual(
            set(catalog), {"schema_version", "catalog_revision", "runtime", "modules"}
        )
        projected = catalog["modules"][0]
        self.assertEqual(
            set(projected),
            {
                "module_id",
                "route",
                "category",
                "version",
                "enabled",
                "lifecycle",
                "state",
                "reason",
                "module_epoch",
                "runtime_id",
                "asset_version",
                "pages",
                "resources",
                "capabilities",
                "config_fields",
                "display",
            },
        )
        self.assertIsNone(projected["display"])
        self.assertEqual(projected["pages"], [])
        self.assertEqual(projected["resources"], [])
        self.assertIsNone(projected["runtime_id"])
        self.assertEqual(projected["state"], "disabled")

    async def test_active_help_and_page_identity_exit_on_stop(self):
        manifest, _ = package()

        class Handler:
            async def invoke(self, *args):
                return None

        handlers = ModuleHandlers({"query": Handler()}, {}, {})
        registry = Registry()
        registry.register_package(manifest, {"demo": handlers})
        lifecycle = LifecycleController(registry, runtime_id="test-runtime")
        catalog = HelpCatalog(active_query=registry.is_active)

        def project():
            return project_module_catalog(
                registry.snapshot(),
                {"sample/demo": lifecycle.state("sample/demo")},
                {"sample/demo": lifecycle.current_identity("sample/demo")},
                runtime_state="ready",
            )["modules"][0]

        self.assertEqual(project()["pages"], [])
        self.assertNotIn("/ygl demo search", str(catalog.total(registry.snapshot())))

        class Instance(_Instance):
            async def check_health(self):
                return HealthReport({"query": CapabilityHealth(HealthStatus.AVAILABLE)})

        instance = Instance(handlers)
        lifecycle.adopt_candidate("sample", manifest.modules[0], "install", instance)
        lifecycle.install_dormant(
            "sample", "sample/demo", "install", instance, handlers
        )
        identity, _ = await lifecycle.start_candidate("sample/demo", "start")
        lifecycle.publish_committed_intent(
            "sample/demo", "start", identity, True, registry.snapshot().revision
        )
        active = project()
        old_snapshot = registry.snapshot()
        self.assertEqual(active["runtime_id"], "test-runtime")
        self.assertEqual(
            active["pages"][0]["entry"], "module-assets/sample/demo/pages/dist/entry.js"
        )
        self.assertTrue(
            catalog.module(registry.snapshot(), "demo").ordered_blocks[1].commands
        )
        await lifecycle.stop("sample/demo")
        self.assertEqual(project()["pages"], [])
        self.assertIsNone(project()["runtime_id"])
        self.assertNotIn(
            "/ygl demo search", str(catalog.module(registry.snapshot(), "demo"))
        )
        self.assertIn("sample/demo", registry.snapshot().modules)
        identity, _ = await lifecycle.start_candidate("sample/demo", "restart")
        lifecycle.publish_committed_intent(
            "sample/demo", "restart", identity, True, registry.snapshot().revision
        )
        self.assertEqual(old_snapshot.module("sample/demo").epoch, 1)
        self.assertEqual(registry.snapshot().module("sample/demo").epoch, 3)
        self.assertNotIn("/ygl demo search", str(catalog.total(old_snapshot)))
        self.assertNotIn("/ygl demo search", str(catalog.module(old_snapshot, "demo")))
        current_snapshot = registry.snapshot()
        self.assertTrue(
            catalog.module(current_snapshot, "demo").ordered_blocks[1].commands
        )
        sibling = replace(
            manifest.modules[0],
            module_id="sibling",
            route="sibling",
            pages=(),
            resources=(),
        )
        registry.register_package(
            replace(manifest, package_id="sibling", modules=(sibling,)),
            {"sibling": handlers},
        )
        self.assertNotEqual(current_snapshot.revision, registry.snapshot().revision)
        self.assertTrue(
            catalog.module(current_snapshot, "demo").ordered_blocks[1].commands
        )
        await lifecycle.stop("sample/demo")
