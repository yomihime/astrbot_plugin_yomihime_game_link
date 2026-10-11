"""Contract checks for inert SDK examples; fake services are local fixtures."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

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
                # offline_sample uses datetime and asyncio for aware SDK
                # Observation timestamp; it must not import Core or host code.
                self.assertLessEqual(
                    roots,
                    {"__future__", "datetime", "asyncio", "yomihime_game_link_sdk"},
                )


if __name__ == "__main__":
    unittest.main()
