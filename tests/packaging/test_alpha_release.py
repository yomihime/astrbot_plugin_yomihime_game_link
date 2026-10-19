"""Current alpha installation, licensing and external-dependency policy."""

from __future__ import annotations

import ast
import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class AlphaReleaseTests(unittest.TestCase):
    def test_plugin_entry_version_matches_metadata(self):
        tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        versions = [
            ast.literal_eval(decorator.args[3])
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
            and decorator.func.id == "register"
        ]
        metadata = (ROOT / "metadata.yaml").read_text(encoding="utf-8")
        self.assertEqual(
            versions, [re.search(r"^version:\s*v(.+)$", metadata, re.M)[1]]
        )
        self.assertIn('astrbot_version: ">=4.29.1"', metadata)

    def test_external_runtime_dependencies_have_only_evidenced_minimums(self):
        dependencies = [
            line.strip()
            for line in (ROOT / "requirements.txt").read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
        self.assertEqual(len(dependencies), 8)
        for dependency in dependencies:
            self.assertRegex(
                dependency, r"^[A-Za-z0-9_-]+>=\d+(?:\.\d+)+(?:\.post\d+)?$"
            )

    def test_sdk_and_plugin_have_distinct_matching_licenses(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        self.assertEqual(project["license"], "MIT")
        self.assertEqual(project["license-files"], ["SDK-LICENSE"])
        self.assertIn("MIT License", (ROOT / "SDK-LICENSE").read_text())
        self.assertIn(
            "GNU AFFERO GENERAL PUBLIC LICENSE", (ROOT / "LICENSE").read_text()
        )
        self.assertTrue((ROOT / "yomihime_game_link_sdk/py.typed").is_file())

    def test_host_localization_uses_current_and_required_4291_aliases(self):
        import json

        from scripts.build_dashboard_zip import HOST_METADATA_FILES

        self.assertEqual(
            set(HOST_METADATA_FILES),
            {".astrbot-plugin/i18n/zh-CN.json", ".astrbot-plugin/i18n/en-US.json"},
        )
        for filename in HOST_METADATA_FILES:
            labels = json.loads((ROOT / filename).read_text(encoding="utf-8"))
            self.assertEqual(labels["views"], labels["pages"])
            self.assertEqual(
                set(labels["views"]), {"00-game-link", "management", "shell"}
            )
            self.assertTrue(
                all(item["title"].strip() for item in labels["views"].values())
            )

    def test_fresh_assembly_has_no_legacy_migration_input(self):
        import json

        descriptor = json.loads(
            (ROOT / "modules/ff14/assembly.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("migration", descriptor)
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        self.assertEqual(set(schema), {"web_public_origin"})
        runtime = (ROOT / "adapters/astrbot/runtime.py").read_text(encoding="utf-8")
        self.assertNotIn("load_prepared_input", runtime)
        self.assertNotIn("ordinary_migration", runtime)
