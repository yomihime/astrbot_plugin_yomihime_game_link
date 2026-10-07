"""Offline declaration probes for the B05 extension manifest and factory ABI."""

import inspect
import unittest
from dataclasses import fields
from typing import get_type_hints

from ygl_test_subject.api.manifests import (
    EXTENSION_DESCRIPTOR_FIELDS,
    EXTENSION_FACTORY_ABI_VERSION,
    EXTENSION_MANIFEST_ABI,
    EXTENSION_MANIFEST_FILENAME,
    EXTENSION_MANIFEST_MAX_BYTES,
    EXTENSION_PACKAGE_FIELDS,
    CapabilityDescriptor,
    CapabilityEffect,
    CapabilityReference,
    ExtensionManifestABI,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
    PrivacyFloor,
)
from ygl_test_subject.api.services import (
    HealthReport,
    ModuleFactory,
    ModuleHandlers,
    ModuleInstance,
    ModuleServices,
)
from ygl_test_subject.api.version import COMPATIBLE_CONTRACT_VERSIONS, CONTRACT_VERSION


class B05ExtensionContractTests(unittest.TestCase):
    def test_disk_envelope_and_budgets_are_frozen(self):
        self.assertEqual(EXTENSION_MANIFEST_FILENAME, "yomihime.manifest.json")
        self.assertEqual(EXTENSION_MANIFEST_MAX_BYTES, 262_144)
        self.assertEqual(
            EXTENSION_PACKAGE_FIELDS,
            frozenset(
                {
                    "schema_version",
                    "package_id",
                    "package_version",
                    "contract_version",
                    "modules",
                    "author",
                    "license",
                    "source",
                }
            ),
        )
        self.assertEqual(EXTENSION_MANIFEST_ABI.schema_version, 1)
        with self.assertRaises(ValueError):
            ExtensionManifestABI(schema_version=True)
        self.assertEqual(EXTENSION_MANIFEST_ABI.max_modules, 64)
        self.assertEqual(EXTENSION_MANIFEST_ABI.max_declarations_per_module, 128)
        self.assertEqual(EXTENSION_MANIFEST_ABI.max_schema_depth, 16)
        self.assertEqual(EXTENSION_MANIFEST_ABI.max_schema_nodes, 2_048)
        self.assertEqual(
            EXTENSION_MANIFEST_ABI.root_policy, "host_supplied_read_only_root"
        )
        self.assertIn("factory_entry", EXTENSION_DESCRIPTOR_FIELDS["module"])
        self.assertIn("input_schema", EXTENSION_DESCRIPTOR_FIELDS["capability"])
        self.assertIn("sensitive", EXTENSION_DESCRIPTOR_FIELDS["config_field"])
        self.assertNotIn("credential_ref", EXTENSION_DESCRIPTOR_FIELDS["source"])
        self.assertEqual(
            EXTENSION_DESCRIPTOR_FIELDS["capability_reference"],
            frozenset({"module_id", "capability_id"}),
        )
        self.assertEqual(EXTENSION_FACTORY_ABI_VERSION, 1)
        with self.assertRaises(ValueError):
            ExtensionManifestABI(max_bytes=0)

    def test_package_mapping_preserves_existing_contract_compatibility(self):
        self.assertIn(CONTRACT_VERSION, COMPATIBLE_CONTRACT_VERSIONS)
        module = ModuleManifest(
            module_id="sample",
            route="sample",
            category=ModuleCategory.GAME,
            factory_entry="sample.module:create",
            module_version="1.0.0",
            capabilities=(),
        )
        package = PackageManifest(
            package_id="sample_pkg",
            package_version="1.0.0",
            contract_version=CONTRACT_VERSION,
            modules=(module,),
            author="Example",
            license="MIT",
            source="https://example.invalid/sample",
        )
        self.assertEqual(package.global_module_id("sample"), "sample_pkg/sample")
        self.assertEqual(
            tuple(field.name for field in fields(PackageManifest)),
            (
                "package_id",
                "package_version",
                "contract_version",
                "modules",
                "author",
                "license",
                "source",
            ),
        )
        with self.assertRaises(ValueError):
            PackageManifest(
                package_id="sample_pkg",
                package_version="1.0.0",
                contract_version="99.0.0",
                modules=(module,),
                author="Example",
                license="MIT",
                source="https://example.invalid/sample",
            )

    def test_factory_abi_is_async_and_does_not_expand_services(self):
        signature = inspect.signature(ModuleFactory.create)
        self.assertEqual(tuple(signature.parameters), ("self", "services"))
        self.assertTrue(inspect.iscoroutinefunction(ModuleFactory.create))
        hints = get_type_hints(ModuleFactory.create)
        self.assertIs(hints["services"], ModuleServices)
        self.assertIs(hints["return"], ModuleInstance)
        self.assertEqual(
            tuple(inspect.signature(ModuleInstance.handlers).parameters), ("self",)
        )
        for name in ("start", "stop", "check_health"):
            method = getattr(ModuleInstance, name)
            self.assertTrue(inspect.iscoroutinefunction(method))
        self.assertIs(
            get_type_hints(ModuleInstance.check_health)["return"], HealthReport
        )
        self.assertEqual(
            tuple(ModuleServices.__dataclass_fields__),
            ("config", "identities", "accounts", "subscriptions", "scopes", "storage"),
        )
        signature = inspect.signature(ModuleServices)
        self.assertEqual(
            tuple(signature.parameters),
            ("config", "identities", "accounts", "subscriptions", "scopes", "storage"),
        )
        self.assertIsNone(signature.parameters["storage"].default)
        self.assertEqual(CONTRACT_VERSION, "1.6.0")
        self.assertIn("1.4.0", COMPATIBLE_CONTRACT_VERSIONS)
        # The five positional handles used by old factories remain valid.
        handles = tuple(object() for _ in range(5))
        legacy_services = ModuleServices(*handles)
        self.assertIsNone(legacy_services.storage)
        self.assertEqual(
            tuple(
                getattr(legacy_services, name)
                for name in tuple(signature.parameters)[:5]
            ),
            handles,
        )
        with self.assertRaises(TypeError):
            ModuleServices(*handles, host=object())
        from ygl_test_subject.examples.empty_module.module import (
            Factory as EmptyFactory,
        )
        from ygl_test_subject.examples.offline_sample.module import (
            Factory as SampleFactory,
        )

        for factory in (EmptyFactory, SampleFactory):
            self.assertEqual(
                tuple(inspect.signature(factory.create).parameters),
                ("self", "services"),
            )
            self.assertTrue(inspect.iscoroutinefunction(factory.create))
            self.assertIs(get_type_hints(factory.create)["services"], ModuleServices)
        self.assertEqual(
            tuple(ModuleHandlers.__dataclass_fields__),
            ("capabilities", "collectors", "evaluators"),
        )

    def test_capability_schema_and_tool_policy_are_explicit(self):
        # This is the exact strict JSON object shape. It maps directly to the
        # Core DTO and carries a globally qualified cross-package module ID.
        reference_fixture = {
            "module_id": "other_pkg/status",
            "capability_id": "lookup",
        }
        self.assertEqual(
            set(reference_fixture), EXTENSION_DESCRIPTOR_FIELDS["capability_reference"]
        )
        reference = CapabilityReference(**reference_fixture)
        self.assertEqual(reference.module_id, "other_pkg/status")
        self.assertEqual(reference.capability_id, "lookup")
        descriptor = CapabilityDescriptor(
            capability_id="status",
            input_schema={
                "type": "object",
                "properties": {},
                "required": (),
                "additionalProperties": False,
            },
            invocation_policy=InvocationPolicy.NATURAL_LANGUAGE_ALLOWED,
            effect=CapabilityEffect.READ_ONLY,
            privacy_floor=PrivacyFloor.PUBLIC,
        )
        self.assertFalse(descriptor.input_schema["additionalProperties"])
        dependent = CapabilityDescriptor(
            capability_id="dependent",
            input_schema={"type": "object"},
            invocation_policy=InvocationPolicy.NATURAL_LANGUAGE_ALLOWED,
            effect=CapabilityEffect.READ_ONLY,
            required_capabilities=(reference,),
        )
        self.assertEqual(dependent.required_capabilities, (reference,))
        with self.assertRaises((TypeError, ValueError)):
            CapabilityReference(**{**reference_fixture, "extra": "rejected"})
        with self.assertRaises(ValueError):
            CapabilityDescriptor(
                capability_id="status",
                input_schema={"type": "object", "additionalProperties": True},
                invocation_policy=InvocationPolicy.NATURAL_LANGUAGE_ALLOWED,
                effect=CapabilityEffect.READ_ONLY,
            )


if __name__ == "__main__":
    unittest.main()
