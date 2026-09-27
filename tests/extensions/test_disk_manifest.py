"""Offline tests for the frozen B05 disk manifest envelope."""

from __future__ import annotations

import json
import unittest

from ygl_test_subject.core import registry as core_registry
from ygl_test_subject.services import module_services as core_module_services

from extensions import discovery as e_discovery
from extensions import disk_manifest as e_disk_manifest
from extensions.disk_manifest import ManifestError, parse_manifest
from yomihime_sdk.api.manifests import (
    CapabilityReference,
    ModuleManifest,
    PackageManifest,
)
from yomihime_sdk.api.services import ModuleFactory, ModuleHandlers, ModuleServices


def valid_document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "package_id": "sample_pkg",
        "package_version": "1.0.0",
        "contract_version": "1.1.0",
        "author": "Example",
        "license": "MIT",
        "source": "https://example.invalid/project",
        "modules": [
            {
                "module_id": "status",
                "route": "status",
                "category": "platform",
                "factory_entry": "sample.factory:Factory",
                "module_version": "1.0.0",
                "capabilities": [
                    {
                        "capability_id": "lookup",
                        "input_schema": {
                            "type": "object",
                            "properties": {},
                            "required": [],
                        },
                        "invocation_policy": "natural_language_allowed",
                        "effect": "read_only",
                    }
                ],
                "tools": [
                    {
                        "name": "lookup",
                        "capability_id": "lookup",
                        "parameter_mapping": {},
                        "description": "Look up status",
                    }
                ],
            }
        ],
    }


class DiskManifestTests(unittest.TestCase):
    def test_e_core_and_sdk_use_identical_manifest_factory_and_service_types(
        self,
    ) -> None:
        from ygl_test_subject.api.services import (
            ModuleFactory as CoreFactoryContract,
        )
        from ygl_test_subject.api.services import (
            ModuleServices as CoreModuleServices,
        )

        self.assertIs(core_registry.PackageManifest, PackageManifest)
        self.assertIs(core_registry.ModuleManifest, ModuleManifest)
        self.assertIs(core_registry.ModuleHandlers, ModuleHandlers)
        self.assertIs(core_module_services.ModuleServices, ModuleServices)
        self.assertIs(e_disk_manifest.PackageManifest, PackageManifest)
        self.assertIs(e_disk_manifest.ModuleManifest, ModuleManifest)
        self.assertIs(
            e_discovery.EXTENSION_MANIFEST_ABI,
            e_disk_manifest.EXTENSION_MANIFEST_ABI,
        )
        self.assertIs(CoreFactoryContract, ModuleFactory)
        self.assertIs(CoreModuleServices, ModuleServices)

        package = parse_manifest(json.dumps(valid_document()).encode())
        self.assertIs(type(package), PackageManifest)
        self.assertIs(type(package.modules[0]), ModuleManifest)

    def test_maps_v1_manifest_to_core_dto_without_importing_factory(self) -> None:
        package = parse_manifest(json.dumps(valid_document()).encode())
        self.assertIsInstance(package, PackageManifest)
        self.assertEqual(package.global_module_id("status"), "sample_pkg/status")
        self.assertEqual(package.modules[0].tools[0].name, "lookup")

    def test_cross_package_capability_reference_uses_frozen_object_shape(self) -> None:
        document = valid_document()
        capability = document["modules"][0]["capabilities"][0]  # type: ignore[index]
        capability["required_capabilities"] = [  # type: ignore[index]
            {"module_id": "other_pkg/status", "capability_id": "lookup"}
        ]
        package = parse_manifest(json.dumps(document).encode())
        dependency = package.modules[0].capabilities[0].required_capabilities[0]
        self.assertEqual(dependency, CapabilityReference("other_pkg/status", "lookup"))

    def test_rejects_duplicate_json_keys_and_unknown_fields(self) -> None:
        raw = json.dumps(valid_document()).replace(
            '"schema_version": 1,', '"schema_version": 1, "schema_version": 1,'
        )
        with self.assertRaisesRegex(ManifestError, "duplicate JSON key"):
            parse_manifest(raw.encode())
        document = valid_document()
        document["extension_hook"] = "sample:run"
        with self.assertRaises(ManifestError):
            parse_manifest(json.dumps(document).encode())

    def test_rejects_boolean_version_oversize_and_unsupported_contract(self) -> None:
        document = valid_document()
        document["schema_version"] = True
        with self.assertRaises(ManifestError):
            parse_manifest(json.dumps(document).encode())

    def test_multibyte_memoryview_cannot_bypass_byte_budget(self) -> None:
        payload = json.dumps(valid_document()).encode()
        payload += b" " * (262_146 - len(payload))
        view = memoryview(payload).cast("H")
        self.assertEqual(len(view), 131_073)
        self.assertEqual(view.nbytes, 262_146)
        with self.assertRaisesRegex(ManifestError, "byte budget"):
            parse_manifest(view)
        with self.assertRaisesRegex(ManifestError, "byte budget"):
            parse_manifest(b" " * 262_145)
        document = valid_document()
        document["contract_version"] = "9.0.0"
        with self.assertRaises(ManifestError):
            parse_manifest(json.dumps(document).encode())

    def test_rejects_duplicate_module_route_or_tool_identity(self) -> None:
        for field in ("module_id", "route"):
            document = valid_document()
            document["modules"].append(dict(document["modules"][0]))  # type: ignore[index]
            document["modules"][1][field] = document["modules"][0][field]  # type: ignore[index]
            with self.subTest(field=field), self.assertRaises(ManifestError):
                parse_manifest(json.dumps(document).encode())
        document = valid_document()
        document["modules"][0]["tools"].append(dict(document["modules"][0]["tools"][0]))  # type: ignore[index]
        with self.assertRaises(ManifestError):
            parse_manifest(json.dumps(document).encode())

    def test_rejects_unknown_reference_members_and_source_credentials(self) -> None:
        document = valid_document()
        capability = document["modules"][0]["capabilities"][0]  # type: ignore[index]
        capability["required_capabilities"] = [  # type: ignore[index]
            {"module_id": "other_pkg/status", "capability_id": "lookup", "secret": "x"}
        ]
        with self.assertRaises(ManifestError):
            parse_manifest(json.dumps(document).encode())
        document = valid_document()
        document["modules"][0]["sources"] = [  # type: ignore[index]
            {
                "source_id": "api",
                "host": "example.invalid",
                "credential_ref": "credential_x",
            }
        ]
        with self.assertRaises(ManifestError):
            parse_manifest(json.dumps(document).encode())


if __name__ == "__main__":
    unittest.main()
