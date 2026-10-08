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

    def test_descriptor_and_legacy_are_one_immutable_inventory(self):
        installed = install_bundled_ff14(self.plugin, self.data)
        self.assertTrue(installed.trusted)
        captured = installed.inventory
        self.assertIn("assembly.json", captured.files)
        for name in ("app.js", "index.html", "styles.css"):
            self.assertEqual(
                captured.files[f"pages/compat/{name}"],
                (ROOT / "pages/ff14" / name).read_bytes(),
            )
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
        from ygl_test_subject.adapters.astrbot.trusted_assembly import (
            assemble_reviewed,
        )
        from ygl_test_subject.modules.ff14.assembly import SUPPORT

        installed = install_bundled_ff14(self.plugin, self.data)
        inventory = installed.inventory
        assembly = assemble_reviewed(
            inventory, principal_id="host", support=SUPPORT, selected=True
        )
        self.assertEqual(len(assembly.credential_policies), 2)
        self.assertEqual(len(assembly.managed_credentials), 2)
        self.assertEqual(len(assembly.migration_fields), 4)
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

    def test_legacy_without_descriptor_has_no_credential_or_public_grants(self):
        from ygl_test_subject.services.trusted_assembly import (
            LegacyAssemblySupport,
            assemble_reviewed,
        )

        (self.plugin / "modules/ff14/assembly.json").unlink()
        (self.plugin / "modules/ff14/assembly.py").unlink()
        installed = install_bundled_ff14(self.plugin, self.data)
        assembly = assemble_reviewed(
            installed.inventory,
            principal_id="host",
            support=LegacyAssemblySupport("ff14/ff14"),
            selected=True,
        )
        self.assertEqual(assembly.credential_policies, ())
        self.assertEqual(assembly.managed_credentials, ())
        self.assertEqual(assembly.public_capabilities, frozenset())
        self.assertEqual(assembly.migration_fields, ())

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
            "contract_version": "1.6.0",
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
                            "output_version": "1.0.0",
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
            "migration": {
                "id": "neutral-v1",
                "fields": [
                    {"target": "neutral/mod", "field": "value", "legacy": "old_value"}
                ],
            },
            "public": {"items": "query"},
            "page_sources": ["api"],
            "credential_regions": ["test"],
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
        self.assertEqual(assembly.migration_fields[0].legacy_field, "old_value")
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
