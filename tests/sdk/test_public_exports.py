"""The SDK root is the one documented, host-independent public import path."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class PublicExportTests(unittest.TestCase):
    def test_all_api_declarations_are_listed_at_the_sdk_root(self) -> None:
        sdk_tree = ast.parse(
            (ROOT / "yomihime_sdk/__init__.py").read_text(encoding="utf-8")
        )
        exports = next(
            ast.literal_eval(node.value)
            for node in sdk_tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__all__"
                for target in node.targets
            )
        )
        self.assertEqual(len(exports), len(set(exports)))
        self.assertTrue(exports)
        self.assertTrue(
            all(name == "__version__" or not name.startswith("_") for name in exports)
        )

        declared = set()
        for path in (ROOT / "yomihime_sdk/api").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(
                    node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    if not node.name.startswith("_"):
                        declared.add(node.name)
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                    declared.update(
                        target.id
                        for target in targets
                        if isinstance(target, ast.Name)
                        and not target.id.startswith("_")
                    )
        self.assertEqual(set(exports) - {"__version__"}, declared)

    def test_sdk_source_uses_only_the_canonical_api_package(self) -> None:
        source = (ROOT / "yomihime_sdk/__init__.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = [
            node
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module is not None
        ]
        self.assertTrue(imports)
        self.assertTrue(
            all(node.level == 1 and node.module.startswith("api.") for node in imports)
        )


if __name__ == "__main__":
    unittest.main()
