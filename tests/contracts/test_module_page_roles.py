import unittest
from dataclasses import replace
from pathlib import Path

from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.extensions.disk_manifest import parse_manifest

from yomihime_game_link_sdk import PageDescriptor


class PageRoleTests(unittest.TestCase):
    def test_closed_role(self):
        self.assertEqual(
            validate_contract(PageDescriptor("query", "Query", "pages/entry.js")).role,
            "aux",
        )
        for role in ("home", "config", "aux"):
            self.assertEqual(
                validate_contract(
                    PageDescriptor("query", "Query", "pages/entry.js", role=role)
                ).role,
                role,
            )
        with self.assertRaises(ValueError):
            validate_contract(
                PageDescriptor("query", "Query", "pages/entry.js", role="admin")
            )

    def test_duplicate_home_or_config_rejected(self):
        manifest = parse_manifest(
            (
                Path(__file__).resolve().parents[2]
                / "modules/ff14/yomihime.manifest.json"
            ).read_bytes()
        ).modules[0]
        page = manifest.pages[0]
        for role in ("home", "config"):
            with self.assertRaises(ValueError):
                validate_contract(
                    replace(
                        manifest,
                        pages=(
                            replace(page, role=role),
                            replace(page, route_id="second", role=role),
                        ),
                    )
                )
