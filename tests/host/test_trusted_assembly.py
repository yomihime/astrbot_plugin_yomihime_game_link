"""Selected release input is trust; captured bytes are consistency evidence."""

import json
import shutil
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.adapters.astrbot.bundled import install_bundled_ff14

ROOT = Path(__file__).resolve().parents[2]


class TrustedAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ygl-assembly-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plugin = self.root / "plugin"
        self.data = self.root / "data"
        self.data.mkdir()
        shutil.copytree(ROOT / "modules/ff14", self.plugin / "modules/ff14")

    def test_descriptor_is_part_of_immutable_inventory(self):
        installed = install_bundled_ff14(self.plugin, self.data)
        self.assertTrue(installed.trusted)
        captured = installed.inventory
        self.assertIn("assembly.json", captured.files)
        before = captured.files["assembly.json"]
        (self.plugin / "modules/ff14/assembly.json").write_text("{}", encoding="utf8")
        self.assertEqual(captured.files["assembly.json"], before)
        with self.assertRaises(TypeError):
            captured.files["assembly.json"] = b"{}"

    def test_installed_descriptor_tamper_or_extra_file_never_inherits_trust(self):
        installed = install_bundled_ff14(self.plugin, self.data)
        path = installed.package_dir / "assembly.json"
        before = path.read_bytes()
        path.write_bytes(b"{}")
        self.assertFalse(install_bundled_ff14(self.plugin, self.data).trusted)
        path.write_bytes(before)
        extra = installed.package_dir / "unexpected.json"
        extra.write_bytes(b"{}")
        self.assertFalse(install_bundled_ff14(self.plugin, self.data).trusted)

    def test_adapter_requires_explicit_reviewed_selection_and_closed_refs(self):
        from ygl_test_subject.modules.ff14.assembly import SUPPORT
        from ygl_test_subject.services.trusted_assembly import (
            assemble_reviewed,
        )

        installed = install_bundled_ff14(self.plugin, self.data)
        inventory = installed.inventory
        assembly = assemble_reviewed(
            inventory, principal_id="host", support=SUPPORT, selected=True
        )
        self.assertEqual(len(assembly.credential_policies), 2)
        self.assertEqual(len(assembly.managed_credentials), 2)
        sync_interval = "ff14_calendar_sync_interval_seconds"
        self.assertIn(sync_interval, SUPPORT.ordinary_config_fields)
        self.assertEqual(
            assembly.public_bindings["items"], ("ff14/ff14", "item.lookup")
        )
        with self.assertRaises(ValueError):
            assemble_reviewed(
                inventory, principal_id="host", support=SUPPORT, selected=False
            )
        for mutation in ("unknown", "owner", "alias", "capability", "duplicate"):
            descriptor = json.loads(inventory.files["assembly.json"])
            if mutation == "unknown":
                descriptor["import"] = "evil:Factory"
            elif mutation == "owner":
                descriptor["module_id"] = "other/mod"
            elif mutation == "alias":
                descriptor["credentials"][0]["alias"] = "credential_unknown"
            elif mutation == "capability":
                descriptor["public"]["items"] = "unknown"
            else:
                descriptor["credentials"].append(descriptor["credentials"][0])
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                assemble_reviewed(
                    replace(
                        inventory,
                        files={
                            **inventory.files,
                            "assembly.json": json.dumps(descriptor).encode(),
                        },
                    ),
                    principal_id="host",
                    support=SUPPORT,
                    selected=True,
                )

    def test_capture_replacement_rejects_before_any_support_import(self):
        from ygl_test_subject.adapters.astrbot import bundled

        original = bundled._read_stable_file
        descriptor = self.plugin / "modules/ff14/assembly.json"
        changed = False

        def replacement(path, root, *, max_bytes):
            nonlocal changed
            content = original(path, root, max_bytes=max_bytes)
            if path == descriptor and not changed:
                changed = True
                descriptor.write_bytes(b"{}")
            return content

        with patch.object(bundled, "_read_stable_file", side_effect=replacement):
            with self.assertRaisesRegex(ValueError, "bundled_assembly_changed"):
                install_bundled_ff14(self.plugin, self.data)
        self.assertEqual(list(self.data.iterdir()), [])

    def test_execution_budgets_are_required_selected_closed_and_typed(
        self,
    ):
        from ygl_test_subject.modules.ff14.assembly import SUPPORT
        from ygl_test_subject.services.trusted_assembly import assemble_reviewed

        inventory = install_bundled_ff14(self.plugin, self.data).inventory
        descriptor = json.loads(inventory.files["assembly.json"])

        def assemble(value):
            return assemble_reviewed(
                replace(
                    inventory,
                    files={
                        **inventory.files,
                        "assembly.json": json.dumps(value).encode(),
                    },
                ),
                principal_id="host",
                support=SUPPORT,
                selected=True,
            )

        selected = assemble(descriptor)
        self.assertEqual(
            dict(selected.capability_timeouts),
            {("ff14/ff14", "ff14.calendar.query"): 45},
        )
        self.assertEqual(
            dict(selected.collector_timeouts), {("ff14/ff14", "ff14.calendar.sync"): 45}
        )
        for values in (selected.capability_timeouts, selected.collector_timeouts):
            with self.assertRaises(TypeError):
                values[("ff14/ff14", "other")] = 45
        legacy = dict(descriptor)
        legacy.pop("execution_budgets")
        with self.assertRaises(ValueError):
            assemble(legacy)
        invalid = [
            {},
            {"capabilities": {}},
            {"capabilities": {}, "collectors": {}, "extra": {}},
            {"capabilities": [], "collectors": {}},
            {"capabilities": {"unknown": 45}, "collectors": {}},
            {"capabilities": {"ff14.calendar.sync": 45}, "collectors": {}},
            {"capabilities": {}, "collectors": {"ff14.calendar.query": 45}},
        ]
        invalid.extend(
            {"capabilities": {"ff14.calendar.query": value}, "collectors": {}}
            for value in (
                True,
                False,
                0,
                -1,
                91,
                float("nan"),
                float("inf"),
                "45",
                None,
            )
        )
        for values in invalid:
            with self.subTest(budget=values), self.assertRaises(ValueError):
                assemble({**descriptor, "execution_budgets": values})

    def test_support_lease_imports_captured_code_and_disposes_exact_namespace(self):
        from ygl_test_subject.extensions.factory_resolver import ReviewedSupportLease

        installed = install_bundled_ff14(self.plugin, self.data)
        files = dict(installed.inventory.files)
        # Exercise import side effects, without constructing a factory instance.
        files["__init__.py"] = (
            b"imported = True\ndef create():\n    raise AssertionError('create must not run')\n"
        )
        first = ReviewedSupportLease(files, selected=True)
        second = ReviewedSupportLease(files, selected=True)
        self.addCleanup(second.release)
        self.addCleanup(first.release)
        self.assertTrue(sys.modules[first._namespace].imported)
        files["assembly.py"] = b"raise AssertionError('live replaced source')\n"
        self.assertEqual(first.support.module_id, "ff14/ff14")
        first.release()
        first.release()
        self.assertFalse(
            any(
                name == first._namespace or name.startswith(first._namespace + ".")
                for name in sys.modules
            )
        )
        self.assertIn(second._namespace + ".assembly", sys.modules)
        before = {
            name for name in sys.modules if name.startswith("_yomihime_reviewed_")
        }
        with self.assertRaises(AssertionError):
            ReviewedSupportLease(files, selected=True)
        self.assertEqual(
            before,
            {name for name in sys.modules if name.startswith("_yomihime_reviewed_")},
        )
        with self.assertRaises(RuntimeError):
            ReviewedSupportLease(installed.inventory.files)

    def test_neutral_selected_fixture_reuses_adapter_without_production_autotrust(self):
        from ygl_test_subject.services.trusted_assembly import (
            CapturedAssemblyInventory,
            assemble_reviewed,
        )

        manifest = {
            "schema_version": 1,
            "package_id": "neutral",
            "package_version": "1.0.0",
            "contract_version": "2.0",
            "author": "fixture",
            "license": "MIT",
            "source": "https://example.test/fixture",
            "modules": [
                {
                    "module_id": "mod",
                    "route": "neutral",
                    "category": "platform",
                    "factory_entry": "module:Factory",
                    "module_version": "1.0.0",
                    "capabilities": [
                        {
                            "capability_id": "query",
                            "input_schema": {"type": "object"},
                            "invocation_policy": "command_and_public_web",
                            "effect": "read_only",
                            "output_version": "1.8.0",
                            "privacy_floor": "public",
                        }
                    ],
                    "config_fields": [
                        {"name": "enabled", "default": True},
                        {"name": "value", "default": 1},
                        {
                            "name": "credential_pair",
                            "sensitive": True,
                            "group": "provider",
                        },
                    ],
                    "sources": [{"source_id": "api", "host": "example.test"}],
                }
            ],
        }
        descriptor = {
            "version": 1,
            "module_id": "neutral/mod",
            "sources": {"api": "example.test"},
            "credentials": [
                {
                    "source": "api",
                    "alias": "credential_pair",
                    "resource_paths": ["/query"],
                    "token_host": "auth.example.test",
                    "token_path": "/token",
                    "scope": "read",
                    "group": "provider",
                    "description": "Synthetic",
                    "region": "test",
                    "label": "Synthetic",
                }
            ],
            "gate": "enabled",
            "public": {"items": "query"},
            "page_sources": ["api"],
            "credential_regions": ["test"],
            "execution_budgets": {"capabilities": {}, "collectors": {}},
        }
        support = SimpleNamespace(module_id="neutral/mod", validators={})
        support.with_metadata = lambda _: support
        inventory = CapturedAssemblyInventory(
            "neutral",
            json.dumps(manifest).encode(),
            {"assembly.json": json.dumps(descriptor).encode()},
        )
        with self.assertRaises(ValueError):
            assemble_reviewed(inventory, principal_id="host", support=support)
        assembly = assemble_reviewed(
            inventory, principal_id="host", support=support, selected=True
        )
        self.assertEqual(
            assembly.credential_policies[0].token_host, "auth.example.test"
        )
        self.assertEqual(
            assembly.public_capabilities, frozenset({("neutral/mod", "query")})
        )
        for raw in (b'{"version":1,"version":1}', b"x" * 32769):
            with self.assertRaises(ValueError):
                assemble_reviewed(
                    replace(inventory, files={"assembly.json": raw}),
                    principal_id="host",
                    support=support,
                    selected=True,
                )


class ReviewedFactoryInventoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_factory_capture_rejects_slot_python_replacement_and_releases(
        self,
    ):
        from ygl_test_subject.extensions.discovery import discover_packages
        from ygl_test_subject.extensions.factory_resolver import (
            ReviewedInventoryFactorySource,
        )
        from ygl_test_subject.extensions.loader import (
            CandidateState,
            ExtensionCandidate,
        )

        with tempfile.TemporaryDirectory(
            prefix="ygl-factory-inventory-", dir=ROOT
        ) as name:
            plugin, data = Path(name) / "plugin", Path(name) / "data"
            shutil.copytree(ROOT / "modules/ff14", plugin / "modules/ff14")
            data.mkdir()
            installed = install_bundled_ff14(plugin, data)
            source = ReviewedInventoryFactorySource(installed.inventory, selected=True)
            package = discover_packages(installed.extension_root)[0]
            candidate = ExtensionCandidate(package, CandidateState.READY)
            lease = await source.capture(candidate)
            self.assertGreater(source.reserved_bytes, 0)
            lease.release()
            self.assertEqual(source.reserved_bytes, 0)
            path = installed.package_dir / "module.py"
            path.write_bytes(
                path.read_bytes() + b"\nraise AssertionError('must never execute')\n"
            )
            package = discover_packages(installed.extension_root)[0]
            with self.assertRaises(ValueError):
                await source.capture(ExtensionCandidate(package, CandidateState.READY))
            self.assertEqual(source.reserved_bytes, 0)


if __name__ == "__main__":
    unittest.main()
