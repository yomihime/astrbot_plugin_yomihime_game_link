"""One formal page root, including a non-default reviewed SDK-only package."""

import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.build_dashboard_zip import projected_page_assets

ROOT = Path(__file__).resolve().parents[2]


class SinglePageProjectionTests(unittest.TestCase):
    def test_minimal_manifest_drives_literal_loader_and_hash_checked_resources(self):
        with tempfile.TemporaryDirectory(
            prefix="ygl-projection-", dir=ROOT
        ) as temporary:
            root = Path(temporary)
            shutil.copytree(
                ROOT / "examples/modules/minimal", root / "examples/modules/minimal"
            )
            page = root / "pages/00-game-link"
            page.mkdir(parents=True)
            (page / "index.html").write_text(
                '<html data-page-name="00-game-link"><template id="module-style-assets"></template></html>',
                encoding="utf-8",
            )
            assets = projected_page_assets(root, bundle_roots=("examples/modules",))
            entry = "pages/00-game-link/module-assets/minimal/demo/pages/entry.js"
            self.assertEqual(
                assets[entry],
                (ROOT / "examples/modules/minimal/pages/entry.js").read_bytes(),
            )
            self.assertTrue(
                all(name.startswith("pages/00-game-link/") for name in assets)
            )
            self.assertIn(
                b'case "module-assets/minimal/demo/pages/entry.js"',
                assets["pages/00-game-link/module-loader.js"],
            )
            self.assertIn(
                b"Unknown module page", assets["pages/00-game-link/module-loader.js"]
            )
            self.assertNotIn(b"ff14", assets["pages/00-game-link/module-loader.js"])
            (root / "examples/modules/minimal/pages/entry.js").write_bytes(b"tampered")
            with self.assertRaises(ValueError):
                projected_page_assets(root, bundle_roots=("examples/modules",))
