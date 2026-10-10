"""Recovery: module ABI and effective canonical import graph."""

import importlib
import json
import unittest

from ygl_test_subject.extensions.disk_manifest import ManifestError, parse_manifest

from yomihime_game_link_sdk import MODULE_ABI_VERSION, ModuleManifest


class RecoveryIdentityTests(unittest.TestCase):
    def test_disk_module_abi_is_separate_from_output_version(self):
        document = {
            "schema_version": 1,
            "package_id": "sample",
            "package_version": "1.0.0",
            "contract_version": "2.0",
            "author": "Example",
            "license": "MIT",
            "source": "local",
            "modules": [
                {
                    "module_id": "status",
                    "route": "status",
                    "category": "platform",
                    "factory_entry": "sample.module:Factory",
                    "module_version": "1.0.0",
                    "capabilities": [
                        {
                            "capability_id": "lookup",
                            "input_schema": {"type": "object"},
                            "invocation_policy": "natural_language_allowed",
                            "effect": "read_only",
                        }
                    ],
                }
            ],
        }
        document["contract_version"] = MODULE_ABI_VERSION
        document["modules"][0]["display"] = {"default_name": "Status"}
        document["modules"][0]["capabilities"][0]["output_version"] = "1.8.0"
        parsed = parse_manifest(json.dumps(document).encode())
        self.assertIs(type(parsed.modules[0]), ModuleManifest)
        self.assertEqual(parsed.contract_version, "2.0")
        document["modules"][0]["capabilities"][0]["output_version"] = "2.0"
        with self.assertRaises(ManifestError):
            parse_manifest(json.dumps(document).encode())

    def test_retired_leaf_type_graph_is_unimportable(self):
        for name in (
            "api.contexts",
            "api.results",
            "api.services",
            "ygl_test_subject.api.contexts",
            "yomihime_sdk.api.contexts",
            "yomihime_sdk.api.results",
            "yomihime_sdk.api.services",
        ):
            with self.subTest(name=name), self.assertRaises(ModuleNotFoundError):
                importlib.import_module(name)
