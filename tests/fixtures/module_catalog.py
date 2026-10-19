"""Real offline AstrBotRuntime catalog; no Host instance or external requests."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from ygl_test_subject.adapters.astrbot.runtime import AstrBotRuntime
from ygl_test_subject.extensions.factory_resolver import FilesystemFactorySource
from ygl_test_subject.services.core_runtime import CoreRuntime

from tests.fixtures.managed_font import OfflineManagedFont
from tests.fixtures.settings_module import write_settings_module

ROOT = Path(__file__).resolve().parents[2]


class _Context:
    async def send_message(self, *_args):
        raise AssertionError("catalog fixture must not send messages")


class _OfflineTransport:
    async def request(self, *_args):
        raise AssertionError("catalog fixture must not perform HTTP")

    async def request_credential_exchange(self, *_args):
        raise AssertionError("catalog fixture must not exchange credentials")

    async def close(self):
        pass


def _core_with_neutral_module(**kwargs):
    # Add a genuinely loaded second disk module to the installer's isolated
    # extension root. Its general resource is neither FF14 nor an SVG.
    extension_root = kwargs["extension_root"]
    write_settings_module(extension_root, "neutral", "neutral")
    directory = extension_root / "neutral"
    manifest_path = directory / "yomihime.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    module = manifest["modules"][0]
    (directory / "pages/dist").mkdir(parents=True)
    shutil.move(directory / "page.js", directory / "pages/dist/entry.js")
    module["pages"][0]["entry"] = "pages/dist/entry.js"
    module["resources"][0]["path"] = "pages/dist/entry.js"
    resource = b"offline-font-resource-identity-only"
    (directory / "assets/fonts").mkdir(parents=True)
    (directory / "assets/fonts/demo.woff2").write_bytes(resource)
    module["resources"].append(
        {
            "path": "assets/fonts/demo.woff2",
            "sha256": hashlib.sha256(resource).hexdigest(),
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    reviewed = kwargs["factory_source"]
    filesystem = FilesystemFactorySource()

    class FixtureSources:
        async def capture(self, candidate):
            # Keep FF14 on the exact production reviewed-inventory loader;
            # only our isolated neutral fixture uses the real disk loader.
            source = (
                filesystem if candidate.package.package_id == "neutral" else reviewed
            )
            return await source.capture(candidate)

    kwargs["factory_source"] = FixtureSources()

    class FixtureCore(CoreRuntime):
        async def _seed_trusted_bundled_manifests(self, candidates):
            await super()._seed_trusted_bundled_manifests(candidates)
            # Explicit durable intent for our isolated fixture, through the
            # real repository; startup discovery/build/lifecycle remain real.
            await self.runtime_repository.seed_default_enabled_if_absent(
                "neutral",
                "demo",
                expected_registry_revision=self.registry.snapshot().revision,
            )

    return FixtureCore(**kwargs)


async def export_catalog() -> dict:
    private_root = os.environ.get("YGL_MANAGED_KEY_TEST_ROOT")
    with tempfile.TemporaryDirectory(
        prefix="ygl-catalog-contract-", dir=private_root
    ) as temporary:
        directory = Path(temporary)
        plugin_root = directory / "plugin"
        (plugin_root / "modules").mkdir(parents=True)
        shutil.copytree(
            ROOT / "modules/ff14",
            plugin_root / "modules/ff14",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        data_dir = directory / "data"
        data_dir.mkdir()
        runtime = AstrBotRuntime(
            _Context(),
            font_manager_factory=OfflineManagedFont,
            plugin_root=plugin_root,
            data_dir=data_dir,
            http_transport_factory=_OfflineTransport,
            core_factory=_core_with_neutral_module,
        )
        try:
            await runtime.initialize()
            catalog = runtime.public_module_catalog()
            assert runtime.ready, (runtime.bundle_failure_reason, catalog)
            assert {module["module_id"] for module in catalog["modules"]} == {
                "ff14/ff14",
                "neutral/demo",
            }
            assert all(module["state"] == "loaded" for module in catalog["modules"])
            return catalog
        finally:
            await runtime.terminate()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(export_catalog()), ensure_ascii=False))
