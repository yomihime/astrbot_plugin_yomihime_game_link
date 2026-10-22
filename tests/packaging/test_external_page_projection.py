"""Fixed external pages reject mismatches without importing external Python."""

import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ygl_test_subject.extensions.static_page_projection import (
    PROJECTION_FILE,
    StaticPageProjection,
    capture_static_pages,
)

from scripts.build_dashboard_zip import projected_page_assets
from tests.fixtures.external_page_catalog import (
    LOGO_BYTES,
    LOGO_PATH,
    external_catalog_fixture,
)

ROOT = Path(__file__).resolve().parents[2]


class ExternalPageProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="ygl-static-pages-", dir=ROOT
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.package = self.root / "external"
        self.package.mkdir()
        self.raw = (
            ROOT / "examples/modules/minimal/yomihime.manifest.json"
        ).read_bytes()
        (self.package / "yomihime.manifest.json").write_bytes(self.raw)
        (self.package / "pages").mkdir()
        (self.package / "pages/entry.js").write_bytes(
            (ROOT / "examples/modules/minimal/pages/entry.js").read_bytes()
        )
        # Neither presence nor validity of Python is needed for static capture.
        (self.package / "module.py").write_text(
            "raise AssertionError('must not import')", encoding="utf-8"
        )
        page = self.root / "pages/00-game-link"
        page.mkdir(parents=True)
        (page / "index.html").write_text(
            '<html data-page-name="00-game-link"><template id="module-style-assets"></template></html>',
            encoding="utf-8",
        )

    def test_closed_build_contains_only_hash_bound_static_inputs(self):
        assets = projected_page_assets(
            self.root, bundle_roots=(), external_page_roots=(self.package,)
        )
        digest = hashlib.sha256(self.raw).hexdigest()
        entry = f"module-assets/minimal/demo/pages/p{digest}/pages/entry.js"
        self.assertEqual(
            assets[f"pages/00-game-link/{entry}"],
            (self.package / "pages/entry.js").read_bytes(),
        )
        self.assertIn(
            f'case "{entry}"'.encode(), assets["pages/00-game-link/module-loader.js"]
        )
        self.assertFalse(any(name.endswith(".py") for name in assets))
        (self.root / PROJECTION_FILE).write_bytes(assets[PROJECTION_FILE])
        projection = StaticPageProjection.load(self.root)
        self.assertTrue(projection.permits("minimal/demo", {"minimal": digest}))
        self.assertFalse(projection.permits("minimal/demo", {"minimal": None}))
        self.assertFalse(projection.permits("minimal/demo", {"minimal": "0" * 64}))
        self.assertFalse(projection.permits("minimal/missing", {"minimal": digest}))

    def test_manifest_only_change_is_a_pairing_mismatch(self):
        projection, _ = capture_static_pages((self.package,))
        changed = json.loads(self.raw)
        changed["author"] += " changed"
        digest = hashlib.sha256(json.dumps(changed).encode()).hexdigest()
        self.assertFalse(projection.permits("minimal/demo", {"minimal": digest}))

    def test_catalog_rewrites_current_owner_and_strips_mismatch_without_mutation(self):
        projection, _ = capture_static_pages((self.package,))
        digest = hashlib.sha256(self.raw).hexdigest()
        data = {
            "modules": [
                {
                    "module_id": "minimal/demo",
                    "pages": [
                        {
                            "entry": "module-assets/minimal/demo/pages/entry.js",
                            "styles": [],
                        }
                    ],
                    "resources": [
                        {
                            "path": "module-assets/minimal/demo/pages/entry.js",
                            "sha256": "0" * 64,
                        }
                    ],
                }
            ]
        }
        data["modules"][0]["resources"][0]["sha256"] = json.loads(self.raw)["modules"][
            0
        ]["resources"][0]["sha256"]
        result = projection.catalog(data, {"minimal": digest})
        self.assertIn(f"/pages/p{digest}/", result["modules"][0]["pages"][0]["entry"])
        self.assertEqual(result["modules"][0]["asset_version"], digest)
        self.assertNotIn(f"/pages/p{digest}/", data["modules"][0]["pages"][0]["entry"])
        rejected = projection.catalog(data, {"minimal": None})
        self.assertEqual(rejected["modules"][0]["pages"], [])
        self.assertEqual(rejected["modules"][0]["resources"], [])
        data["modules"][0]["resources"][0]["sha256"] = "0" * 64
        self.assertEqual(
            projection.catalog(data, {"minimal": digest})["modules"][0]["pages"], []
        )

    def test_resource_tamper_duplicate_package_and_builtin_collision_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate|collision"):
            capture_static_pages((self.package, self.package))
        shutil.copytree(
            ROOT / "examples/modules/minimal", self.root / "examples/modules/minimal"
        )
        with self.assertRaisesRegex(ValueError, "built-in"):
            projected_page_assets(
                self.root,
                bundle_roots=("examples/modules",),
                external_page_roots=(self.package,),
            )
        (self.package / "pages/entry.js").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            capture_static_pages((self.package,))

    def test_catalog_logo_tracks_projected_resources_without_mutating_input(self):
        projection, catalog, digest = external_catalog_fixture()
        (self.package / "yomihime.manifest.json").write_bytes(
            projection.packages["minimal"][2]
        )
        (self.package / "assets").mkdir()
        (self.package / "assets/logo.svg").write_bytes(LOGO_BYTES)
        projection, resources = capture_static_pages((self.package,))
        before = copy.deepcopy(catalog)
        module = projection.catalog(catalog, {"minimal": digest})["modules"][0]
        expected = f"module-assets/minimal/demo/pages/p{digest}/assets/logo.svg"
        self.assertEqual(resources[expected], LOGO_BYTES)
        self.assertEqual(module["display"]["logo_resource"], expected)
        self.assertIn(expected, [item["path"] for item in module["resources"]])
        self.assertEqual(catalog, before)
        self.assertEqual(catalog["modules"][0]["display"]["logo_resource"], LOGO_PATH)
        self.assertEqual(projection.catalog(catalog, {}), catalog)
        wrong_owner = copy.deepcopy(catalog)
        wrong_owner["modules"][0]["display"]["logo_resource"] = (
            "module-assets/other/demo/assets/logo.svg"
        )
        with self.assertRaisesRegex(ValueError, "owner mismatch"):
            projection.catalog(wrong_owner, {"minimal": digest})

    def test_catalog_hidden_external_resources_also_hide_logo(self):
        projection, catalog, digest = external_catalog_fixture()
        before = copy.deepcopy(catalog)
        for binding in (None, "0" * 64):
            with self.subTest(binding=binding):
                module = projection.catalog(catalog, {"minimal": binding})["modules"][0]
                self.assertEqual(module["pages"], [])
                self.assertEqual(module["resources"], [])
                self.assertIsNone(module["display"]["logo_resource"])
        for actual_projection, data in (
            (StaticPageProjection(), catalog),
            (projection, copy.deepcopy(catalog)),
        ):
            if actual_projection is projection:
                data["modules"][0]["resources"][0]["sha256"] = "0" * 64
            module = actual_projection.catalog(data, {"minimal": digest})["modules"][0]
            self.assertEqual(module["pages"], [])
            self.assertEqual(module["resources"], [])
            self.assertIsNone(module["display"]["logo_resource"])
        self.assertEqual(catalog, before)

    def test_catalog_without_logo_keeps_pairing_behavior(self):
        projection, catalog, digest = external_catalog_fixture(with_logo=False)
        for binding in (digest, None, "0" * 64):
            with self.subTest(binding=binding):
                module = projection.catalog(catalog, {"minimal": binding})["modules"][0]
                self.assertIsNone(module["display"]["logo_resource"])
                self.assertEqual(bool(module["pages"]), binding == digest)
                self.assertEqual(bool(module["resources"]), binding == digest)
        catalog["modules"][0]["display"] = None
        self.assertIsNone(
            projection.catalog(catalog, {"minimal": digest})["modules"][0]["display"]
        )

    def test_malformed_metadata_and_resource_hardlink_are_rejected(self):
        (self.root / PROJECTION_FILE).write_text(
            '{"schema_version":1,"manifests":[],"extra":true}', encoding="utf-8"
        )
        with self.assertRaises(ValueError):
            StaticPageProjection.load(self.root)
        (self.package / "pages/alias.js").hardlink_to(self.package / "pages/entry.js")
        with self.assertRaisesRegex(ValueError, "hardlink"):
            capture_static_pages((self.package,))
