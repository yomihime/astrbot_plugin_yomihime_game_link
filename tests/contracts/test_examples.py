"""Examples share the same type identity as consumers under the host package."""

import unittest

from ygl_test_subject.api.display import DisplayDocument
from ygl_test_subject.api.results import CapabilityResult
from ygl_test_subject.examples.contracts import example_package, example_result


class ContractExampleTests(unittest.TestCase):
    def test_example_package_resolves_its_scoped_capability(self):
        package = example_package()
        self.assertEqual(package.global_module_id("sample"), "example/sample")
        module = package.modules[0]
        self.assertEqual(
            module.commands[0].capability_id, module.tools[0].capability_id
        )

    def test_example_result_uses_public_types_and_truthful_source(self):
        result = example_result()
        self.assertIsInstance(result, CapabilityResult)
        self.assertIsInstance(result.document, DisplayDocument)
        self.assertEqual(result.document.sources, ("offline example",))
