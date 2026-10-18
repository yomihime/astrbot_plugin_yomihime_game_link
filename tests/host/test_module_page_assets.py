"""Packaged resources through official4.29.1 native relative asset path contracts."""

from __future__ import annotations

import asyncio
import hashlib
import re
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from ygl_test_subject.adapters.astrbot.bundled import install_bundled_ff14
from ygl_test_subject.extensions.discovery import discover_packages
from ygl_test_subject.extensions.page_resources import capture_page_resources

from scripts.build_dashboard_zip import ROOT, included_files, projected_page_assets
from tests.host.astrbot_contract import page_asset_contract


class ModulePageAssetTests(unittest.TestCase):
    def test_official_view_discovery_and_per_root_resources(self):
        service, _ = page_asset_contract()

        assets = projected_page_assets(ROOT)
        # Discover the actual package inputs, not ignored historical files in a
        # developer checkout. FF14 is now a module inside the unified shell.
        with tempfile.TemporaryDirectory() as temporary:
            staged = Path(temporary)
            for source in included_files(ROOT):
                relative = source.relative_to(ROOT)
                if relative.parts[0] == "pages":
                    target = staged / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(source.read_bytes())
            for name, content in assets.items():
                if name.startswith("pages/"):
                    target = staged / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)

            async def pages_root(_plugin):
                return staged / "pages"

            service.resolve_plugin_pages_root = pages_root
            pages = asyncio.run(service.discover_plugin_pages(None))
            self.assertEqual(
                [page.name for page in pages],
                ["00-game-link", "management", "shell"],
            )
            self.assertFalse((staged / "pages/ff14").exists())
        for page in ("00-game-link", "shell", "management"):
            root = ROOT / "pages" / page
            html = assets.get(
                f"pages/{page}/index.html", (root / "index.html").read_bytes()
            ).decode()
            processed = service.process_plugin_page_html(
                html, theme=None, extra_query_params={"asset_token": "offline-proof"}
            )
            self.assertIn('href="./styles.css"', processed)
            self.assertIn('src="./app.js"', processed)
            self.assertIn("asset_token=offline-proof", processed)
            base = service.build_plugin_page_view_content_path(
                "fixture-plugin", page, "offline-proof"
            )
            self.assertIn("/_t/offline-proof/", base)
            for name in [
                "runtime.js",
                "THIRD_PARTY_NOTICES.txt",
                *[
                    "licenses/" + p.name
                    for p in (ROOT / "pages/shell/licenses").iterdir()
                ],
            ]:
                self.assertEqual(
                    (root / name).read_bytes(),
                    (ROOT / "pages/shell" / name).read_bytes(),
                )
            for name in ("app.js", "styles.css", "runtime.js"):
                self.assertTrue(
                    urlsplit(
                        urljoin("https://host" + base, "./" + name)
                    ).path.startswith(base.rsplit("/", 1)[0] + "/")
                )
        self.assertIn(
            'data-page-name="00-game-link"',
            assets["pages/00-game-link/index.html"].decode(),
        )

    def test_nested_native_relative_imports_and_inert_styles_keep_view_token(self):
        service, _ = page_asset_contract()
        assets = projected_page_assets(ROOT)
        base = service.build_plugin_page_view_content_path(
            "fixture-plugin", "shell", "offline-proof"
        )
        prefix = base.rsplit("/", 1)[0] + "/"
        html = assets["pages/shell/index.html"].decode()
        self.assertIn('<template id="module-style-assets"><link ', html)
        for path in re.findall(r'data-resource="([^"]+)"', html):
            self.assertIn("pages/shell/" + path, assets)
            self.assertTrue(
                urlsplit(urljoin("https://host" + base, "./" + path)).path.startswith(
                    prefix
                )
            )
        observed = set()
        for path in (
            "app.js",
            "module-loader.js",
            "module-assets/ff14/ff14/pages/dist/entry.js",
        ):
            source = assets.get("pages/shell/" + path)
            if source is None:
                source = (ROOT / "pages/shell" / path).read_bytes()
            # The host no longer rewrites JavaScript/CSS. Native browser URL
            # resolution carries the view's path token through relative imports.
            for reference in re.findall(
                r"(?:from\s*|import\s*\(|import\s*)[\"\'](\.{1,2}/[^\"\']+)",
                source.decode(),
            ):
                resolved = urlsplit(
                    urljoin(urljoin("https://host" + base, path), reference)
                ).path
                self.assertTrue(resolved.startswith(prefix), reference)
                relative = resolved.removeprefix(prefix)
                self.assertTrue(
                    "pages/shell/" + relative in assets
                    or (ROOT / "pages/shell" / relative).is_file(),
                    relative,
                )
                observed.add(relative)
        self.assertTrue(
            {
                "runtime.js",
                "module-loader.js",
                "module-assets/ff14/ff14/pages/dist/entry.js",
            }
            <= observed
        )
        with self.assertRaises(ValueError):
            service.normalize_plugin_page_path("../../outside.js")

    def test_bundle_copy_and_candidate_manifest_binding(self):
        # Use the published package inputs only, never authoring/node_modules.
        import shutil

        from scripts.build_dashboard_zip import _ff14_bundle_files

        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            plugin = root / "plugin"
            package_root = plugin / "modules/ff14"
            package_root.mkdir(parents=True)
            for source in _ff14_bundle_files(ROOT):
                relative = source.relative_to(ROOT / "modules/ff14")
                target = package_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            data = root / "data"
            data.mkdir()
            installed = install_bundled_ff14(plugin, data)
            self.assertTrue(installed.trusted, installed.reason)
            package = discover_packages(installed.extension_root)[0]
            resources = capture_page_resources(package._provenance, package.manifest)
            self.assertTrue(resources)
            for module in package.manifest.modules:
                for resource in module.resources:
                    self.assertEqual(
                        hashlib.sha256(resources[resource.path]).hexdigest(),
                        resource.sha256,
                    )
                    self.assertEqual(
                        resources[resource.path],
                        (package_root / resource.path).read_bytes(),
                    )
            wrong = replace(package.manifest, package_version="9.9.9")
            with self.assertRaisesRegex(ValueError, "pinned candidate"):
                capture_page_resources(package._provenance, wrong)
            first = next(iter(resources))
            (installed.package_dir / first).write_bytes(b"modified")
            repeated = install_bundled_ff14(plugin, data)
            self.assertFalse(repeated.trusted)
