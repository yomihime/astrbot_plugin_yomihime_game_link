"""Packaged resources through unchanged, pinned Host asset rewrite contracts."""

from __future__ import annotations

import asyncio
import hashlib
import re
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ygl_test_subject.adapters.astrbot.bundled import install_bundled_ff14
from ygl_test_subject.extensions.discovery import discover_packages
from ygl_test_subject.extensions.page_resources import capture_page_resources

from scripts.build_dashboard_zip import ROOT, projected_page_assets
from tests.host.astrbot_contract import page_asset_contract


class ModulePageAssetTests(unittest.TestCase):
    def test_pinned_discovery_default_and_independent_legal_roots(self):
        service, namespace = page_asset_contract()

        async def pages_root(_plugin):
            return ROOT / "pages"

        service.resolve_plugin_pages_root = pages_root
        pages = asyncio.run(service.discover_plugin_pages(None))
        self.assertEqual(
            [page.name for page in pages],
            ["00-game-link", "ff14", "management", "shell"],
        )
        assets = projected_page_assets(ROOT)
        for page in ("00-game-link", "shell", "management"):
            root = ROOT / "pages" / page
            html = assets.get(
                f"pages/{page}/index.html", (root / "index.html").read_bytes()
            ).decode()
            rewritten_html = service.rewrite_plugin_page_html(
                html,
                "fixture-plugin",
                page,
                "index.html",
                theme=None,
                extra_query_params={"asset_token": "offline-proof"},
            )
            self.assertIn(
                f'href="/api/plugin/page/content/fixture-plugin/{page}/styles.css?asset_token=offline-proof"',
                rewritten_html,
            )
            self.assertIn(
                f'src="/api/plugin/page/content/fixture-plugin/{page}/app.js?asset_token=offline-proof"',
                rewritten_html,
            )
            css = (root / "styles.css").read_text(encoding="utf-8")
            rewritten_css = service.rewrite_plugin_page_css(
                css,
                "fixture-plugin",
                page,
                "styles.css",
                {"asset_token": "offline-proof"},
            )
            for match in namespace["_CSS_URL_RE"].finditer(rewritten_css):
                if service.is_rewritable_asset_url(match["url"]):
                    self.assertTrue(
                        match["url"].startswith(
                            f"/api/plugin/page/content/fixture-plugin/{page}/"
                        )
                    )
                    self.assertEqual(
                        parse_qs(urlsplit(match["url"]).query),
                        {"asset_token": ["offline-proof"]},
                    )
            self.assertEqual(
                (root / "runtime.js").read_bytes(),
                (ROOT / "pages/shell/runtime.js").read_bytes(),
            )
            self.assertEqual(
                (root / "THIRD_PARTY_NOTICES.txt").read_bytes(),
                (ROOT / "pages/shell/THIRD_PARTY_NOTICES.txt").read_bytes(),
            )
            for license in (ROOT / "pages/shell/licenses").iterdir():
                self.assertEqual(
                    (root / "licenses" / license.name).read_bytes(),
                    license.read_bytes(),
                )
            paths = (
                ["app.js"]
                if page == "management"
                else [
                    "app.js",
                    "module-loader.js",
                    "module-assets/ff14/ff14/pages/dist/entry.js",
                ]
            )
            observed = set()
            for path in paths:
                source = assets.get(f"pages/{page}/{path}", None)
                if source is None:
                    source = (root / path).read_bytes()
                rewritten = service.rewrite_plugin_page_js(
                    source.decode(),
                    "fixture-plugin",
                    page,
                    path,
                    {"asset_token": "offline-proof"},
                )
                for pattern in (
                    "_JS_DYNAMIC_IMPORT_RE",
                    "_JS_MODULE_FROM_RE",
                    "_JS_SIDE_EFFECT_IMPORT_RE",
                ):
                    for match in namespace[pattern].finditer(rewritten):
                        url = urlsplit(match["url"])
                        self.assertTrue(
                            url.path.startswith(
                                f"/api/plugin/page/content/fixture-plugin/{page}/"
                            ),
                            match["url"],
                        )
                        self.assertEqual(
                            parse_qs(url.query), {"asset_token": ["offline-proof"]}
                        )
                        observed.add(url.path.rsplit("/", 1)[-1])
            self.assertIn("runtime.js", observed)
        default_index = assets["pages/00-game-link/index.html"].decode()
        self.assertIn('data-page-name="00-game-link"', default_index)
        self.assertNotIn(
            'data-page-name="00-game-link"', assets["pages/shell/index.html"].decode()
        )

    def test_inert_styles_and_literal_js_follow_host_asset_proof_chain(self):
        service, namespace = page_asset_contract()
        assets = projected_page_assets(ROOT)
        # This inert fixture value is not a JWT or a credential; it proves only
        # the original Host rewrite propagation, never HTTP authorization.
        proof = {"asset_token": "offline-rewrite-fixture"}
        html = assets["pages/shell/index.html"].decode()
        self.assertIn('<template id="module-style-assets"><link ', html)
        rewritten = service.rewrite_plugin_page_html(
            html,
            "fixture-plugin",
            "shell",
            "index.html",
            theme=None,
            extra_query_params=proof,
        )
        style_paths = re.findall(r'data-resource="([^"]+)"', html)
        for path in style_paths:
            self.assertIn(
                'href="/api/plugin/page/content/fixture-plugin/shell/'
                + path
                + '?asset_token=offline-rewrite-fixture"',
                rewritten,
            )
            self.assertIn("pages/shell/" + path, assets)
        required = {
            "module-loader.js",
            "app.js",
            "module-assets/ff14/ff14/pages/dist/entry.js",
        }
        observed = set()
        for path in required:
            text = assets.get("pages/shell/" + path)
            if text is None:
                text = (ROOT / "pages/shell" / path).read_bytes()
            rewritten_js = service.rewrite_plugin_page_js(
                text.decode(), "fixture-plugin", "shell", path, proof
            )
            for pattern in (
                "_JS_DYNAMIC_IMPORT_RE",
                "_JS_MODULE_FROM_RE",
                "_JS_SIDE_EFFECT_IMPORT_RE",
            ):
                for match in namespace[pattern].finditer(rewritten_js):
                    url = urlsplit(match["url"])
                    if url.path.startswith(
                        "/api/plugin/page/content/fixture-plugin/shell/"
                    ):
                        self.assertEqual(
                            parse_qs(url.query),
                            {"asset_token": ["offline-rewrite-fixture"]},
                        )
                        observed.add(
                            url.path.removeprefix(
                                "/api/plugin/page/content/fixture-plugin/shell/"
                            )
                        )
                    else:
                        self.assertFalse(
                            match["url"].startswith(("./", "../")), match["url"]
                        )
        self.assertTrue(
            {
                "runtime.js",
                "module-loader.js",
                "module-assets/ff14/ff14/pages/dist/entry.js",
            }
            <= observed
        )
        # Original Host cannot attach a proof to computed runtime CSS/JS URLs.
        computed = "link.href = './' + descriptor.styles[0]; import(variable);"
        self.assertEqual(
            service.rewrite_plugin_page_js(
                computed, "fixture-plugin", "shell", "app.js", proof
            ),
            computed,
        )
        with self.assertRaises(ValueError):
            service.rewrite_relative_asset_url(
                "../../outside.js", "index.html", "fixture-plugin", "shell", proof
            )

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
