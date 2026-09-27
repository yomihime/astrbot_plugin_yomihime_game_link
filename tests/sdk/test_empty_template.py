"""Contract checks for inert SDK examples; fake services are local fixtures."""

from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

from extensions.disk_manifest import parse_manifest

ROOT = Path(__file__).resolve().parents[2]


class EmptyTemplateTests(unittest.TestCase):
    def test_samples_import_only_the_sdk_and_future_annotations(self) -> None:
        for name in ("empty_module", "offline_sample"):
            source = ROOT / "examples" / name / "module.py"
            tree = ast.parse(source.read_text(encoding="utf-8"))
            roots = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots.update(alias.name.split(".", 1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module is not None:
                    roots.add(node.module.split(".", 1)[0])
            with self.subTest(example=name):
                # offline_sample uses datetime only for its fixed aware SDK
                # Observation timestamp; it must not import Core or host code.
                self.assertLessEqual(roots, {"__future__", "datetime", "yomihime_sdk"})

    def test_empty_and_offline_manifests_match_the_e_schema(self) -> None:
        empty_raw = json.loads(
            (ROOT / "examples/empty_module/manifest.json").read_text("utf-8")
        )
        sample_raw = json.loads(
            (ROOT / "examples/offline_sample/manifest.json").read_text("utf-8")
        )
        empty = parse_manifest(json.dumps(empty_raw).encode())
        sample = parse_manifest(json.dumps(sample_raw).encode())

        self.assertEqual(empty.modules, ())
        self.assertEqual(empty.contract_version, "1.1.0")
        self.assertEqual(
            {module.module_id for module in sample.modules}, {"status", "source"}
        )
        status = next(
            module for module in sample.modules if module.module_id == "status"
        )
        self.assertEqual(
            next(
                command.operation_path
                for command in status.commands
                if command.capability_id == "status"
            ),
            "status",
        )
        self.assertEqual(
            next(tool.name for tool in status.tools if tool.capability_id == "status"),
            "sample_status",
        )


if __name__ == "__main__":
    unittest.main()
