"""One formal page root, including a non-default reviewed SDK-only package."""

import hashlib
import json
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

    def test_presentation_projects_only_its_exact_owner_and_keeps_styles_inert(self):
        with tempfile.TemporaryDirectory(
            prefix="ygl-presentation-", dir=ROOT
        ) as temporary:
            root = Path(temporary)
            package = root / "examples/modules/minimal"
            shutil.copytree(ROOT / "examples/modules/minimal", package)
            page = root / "pages/00-game-link"
            page.mkdir(parents=True)
            (page / "index.html").write_text(
                '<html data-page-name="00-game-link"><template id="module-style-assets"></template></html>',
                encoding="utf-8",
            )
            manifest_path = package / "yomihime.manifest.json"
            manifest = json.loads(manifest_path.read_bytes())
            files = {
                "pages/dist/presentation-demo.js": b"export const presentation={schemaVersion:1};",
                "pages/dist/presentation-demo.css": b".minimal-demo-logo{display:block}",
                "pages/dist/presentation-other.js": b"throw Error('wrong owner');",
                "pages/dist/presentation-other.css": b".other-logo{display:block}",
            }
            assets = projected_page_assets(root, bundle_roots=("examples/modules",))
            self.assertNotIn(
                b"presentation-demo.js", assets["pages/00-game-link/module-loader.js"]
            )
            self.assertNotIn(
                b"presentation-demo.css", assets["pages/00-game-link/index.html"]
            )
            for relative, data in files.items():
                path = package / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            with self.assertRaisesRegex(
                ValueError, "bundled_package_has_unreviewed_files"
            ):
                projected_page_assets(root, bundle_roots=("examples/modules",))

            manifest["modules"][0]["resources"].extend(
                {"path": relative, "sha256": hashlib.sha256(data).hexdigest()}
                for relative, data in files.items()
            )
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            assets = projected_page_assets(root, bundle_roots=("examples/modules",))
            loader = assets["pages/00-game-link/module-loader.js"]
            business, presentation = loader.split(
                b"export async function loadPresentation", 1
            )
            expected = b"module-assets/minimal/demo/pages/dist/presentation-demo.js"
            self.assertNotIn(expected, business)
            self.assertIn(b'case "' + expected + b'"', presentation)
            self.assertNotIn(b"presentation-other.js", presentation)
            self.assertEqual(
                assets["pages/00-game-link/" + expected.decode()],
                files["pages/dist/presentation-demo.js"],
            )
            index = assets["pages/00-game-link/index.html"]
            self.assertIn(
                b'<template id="module-style-assets"><link rel="stylesheet" '
                b'data-resource="module-assets/minimal/demo/pages/dist/presentation-demo.css"',
                index,
            )
            self.assertNotIn(b"presentation-other.css", index)
            (package / "pages/dist/presentation-demo.js").write_bytes(b"tampered")
            with self.assertRaises(ValueError):
                projected_page_assets(root, bundle_roots=("examples/modules",))
