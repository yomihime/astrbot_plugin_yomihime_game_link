"""Display metadata is bounded declaration data, never a routing identity."""

import json
import unittest
from dataclasses import replace

from ygl_test_subject.api.administration import ModuleLifecycle
from ygl_test_subject.core.lifecycle import LifecycleState
from ygl_test_subject.core.registry import Registry
from ygl_test_subject.services.module_catalog import project_module_catalog

from extensions.disk_manifest import ManifestError, parse_manifest
from tests.core.test_registry import _handlers, _module, _package
from tests.extensions.test_disk_manifest import valid_document
from yomihime_sdk import api
from yomihime_sdk.api import manifests


class ModuleDisplayTests(unittest.TestCase):
    def display(self, *args, **kwargs):
        return manifests.ModuleDisplay(*args, **kwargs)

    def test_names_are_bounded_plain_text_and_mapping_is_frozen(self):
        names = {"zh": "最终幻想 XIV", "en-US": "FINAL FANTASY XIV"}
        display = self.display("最终幻想 XIV", names, "FF14")
        names["zh"] = "changed"
        self.assertEqual(display.localized_names["zh"], "最终幻想 XIV")
        with self.assertRaises(TypeError):
            display.localized_names["zh"] = "changed"
        self.assertEqual(dict(self.display("Name").localized_names), {})
        for invalid in ("", " ", "x" * 129, "name\n", "name\x7f", "a\u202eb"):
            with self.subTest(invalid=repr(invalid)):
                with self.assertRaises((TypeError, ValueError)):
                    self.display(invalid)
        for invalid in ("", "x" * 33, "x\t"):
            with self.assertRaises((TypeError, ValueError)):
                self.display("Name", short_name=invalid)
        with self.assertRaises((TypeError, ValueError)):
            self.display("Name", {"en": "x" * 129})

    def test_locale_syntax_budget_and_casefold_duplicates(self):
        self.display(
            "Name", {"zh-Hans-CN": "名称", "en-US": "Name", "es-419": "Nombre"}
        )
        for names in (
            {"en": "A", "EN": "B"},
            {"en_US": "A"},
            {"": "A"},
            {"en--US": "A"},
            {"x-private": "A"},
            {1: "A"},
            {f"en-{i:03}": "A" for i in range(17)},
        ):
            with self.subTest(names=names):
                with self.assertRaises((TypeError, ValueError)):
                    self.display("Name", names)

    def test_display_requires_new_package_contract_old_set_remains_compatible(self):
        module = replace(_module("catalog", "catalog"), display=self.display("Name"))
        package = _package("sample", module)
        self.assertEqual(package.contract_version, "1.8.0")
        for version in (
            "1.0.0",
            "1.1.0",
            "1.2.0",
            "1.3.0",
            "1.4.0",
            "1.5.0",
            "1.6.0",
            "1.7.0",
        ):
            with self.subTest(version=version):
                with self.assertRaises(ValueError):
                    replace(package, contract_version=version)
                legacy = replace(
                    package,
                    contract_version=version,
                    modules=(replace(module, display=None),),
                )
                self.assertIsNone(legacy.modules[0].display)
        with self.assertRaises((TypeError, ValueError)):
            replace(module, display={"default_name": "Name"})

    def test_disk_closed_display_shape_and_legacy_field_rejection(self):
        document = valid_document()
        document["contract_version"] = "1.8.0"
        document["modules"][0]["display"] = {
            "default_name": "Name",
            "localized_names": {"en": "Name"},
            "short_name": "N",
        }
        parsed = parse_manifest(json.dumps(document).encode())
        self.assertEqual(parsed.modules[0].display.short_name, "N")
        for value in (
            None,
            {},
            {"default_name": "Name", "html": "<b>"},
            {"default_name": "Name", "localized_names": {"en": "A", "EN": "B"}},
        ):
            candidate = json.loads(json.dumps(document))
            candidate["modules"][0]["display"] = value
            with self.assertRaises(ManifestError):
                parse_manifest(json.dumps(candidate).encode())
        for version in (
            "1.0.0",
            "1.1.0",
            "1.2.0",
            "1.3.0",
            "1.4.0",
            "1.5.0",
            "1.6.0",
            "1.7.0",
        ):
            document["contract_version"] = version
            with self.assertRaises(ManifestError):
                parse_manifest(json.dumps(document).encode())
            legacy = json.loads(json.dumps(document))
            del legacy["modules"][0]["display"]
            self.assertIsNone(
                parse_manifest(json.dumps(legacy).encode()).modules[0].display
            )

    def test_catalog_projects_trusted_names_without_business_execution_or_identity_change(
        self,
    ):
        display = self.display("Name", {"zh": "名称"}, "N")
        module = replace(_module("catalog", "catalog"), display=display)
        registry = Registry()
        snapshot = registry.register_package(
            _package("sample", module), {"catalog": _handlers("record.query")}
        )
        owner = "sample/catalog"
        registered = snapshot.modules[owner]
        states = {
            owner: LifecycleState(
                owner,
                registered.enabled,
                ModuleLifecycle.STOPPED,
                registered.epoch,
                snapshot.revision,
            )
        }
        result = project_module_catalog(
            snapshot, states, {owner: None}, runtime_state="ready"
        )
        item = result["modules"][0]
        self.assertEqual(
            item["display"],
            {
                "default_name": "Name",
                "localized_names": {"zh": "名称"},
                "short_name": "N",
            },
        )
        item["display"]["localized_names"]["zh"] = "changed"
        self.assertEqual(display.localized_names["zh"], "名称")
        self.assertEqual((item["module_id"], item["route"]), (owner, "catalog"))
        self.assertEqual(snapshot.routes["catalog"], owner)

    def test_facade_and_legacy_shim_share_display_identity(self):
        from ygl_test_subject.api.manifests import ModuleDisplay

        import yomihime_sdk as sdk

        self.assertIs(sdk.ModuleDisplay, ModuleDisplay)
        self.assertIs(api.manifests.ModuleDisplay, ModuleDisplay)
