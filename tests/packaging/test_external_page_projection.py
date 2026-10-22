"""Fixed external pages reject mismatches without importing external Python."""

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

    def test_malformed_metadata_and_resource_hardlink_are_rejected(self):
        (self.root / PROJECTION_FILE).write_text(
            '{"schema_version":1,"manifests":[],"extra":true}', encoding="utf-8"
        )
        with self.assertRaises(ValueError):
            StaticPageProjection.load(self.root)
        (self.package / "pages/alias.js").hardlink_to(self.package / "pages/entry.js")
        with self.assertRaisesRegex(ValueError, "hardlink"):
            capture_static_pages((self.package,))
