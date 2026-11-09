"""Build, install, and extract examples from a clean SDK wheel artifact."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from importlib.metadata import version
from pathlib import Path

from ygl_test_subject.extensions.disk_manifest import parse_manifest

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_WHEEL_SHA256 = (
    "0d741c2d998c63df481ec2fe5670048f04b7170e822616a3d8fe6816527b0324"
)
EXPECTED_RESOURCES = {
    f"yomihime_game_link_sdk/_examples/{example}/{filename}"
    for example in ("empty_module", "offline_sample")
    for filename in ("manifest.json", "module.py", "README.md")
}


VERIFY_INSTALLED_ARTIFACT = r"""
import asyncio, importlib, importlib.abc, importlib.resources, importlib.util, os, sys, threading
from pathlib import Path
site_root, extraction_root = map(Path, sys.argv[1:3])
sys.path.insert(0, str(site_root))
phase = [True]
class SDKOnly(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if phase[0] and fullname.split(".")[0] in {"core", "services", "adapters", "astrbot", "modules"}:
            raise AssertionError("SDK reverse dependency: " + fullname)
def audit(event,args):
    if phase[0] and event in {"socket.connect","socket.bind","subprocess.Popen","os.system"}:
        raise AssertionError("SDK import performed external work")
    if phase[0] and event=="open" and isinstance(args[0],(str,bytes)) and os.fsdecode(args[0]).lower().endswith((".json",".yaml",".sqlite",".db",".toml")):
        raise AssertionError("SDK import read product configuration")
sys.addaudithook(audit)
sys.meta_path.insert(0,SDKOnly())
before={t.ident for t in threading.enumerate()}
import yomihime_game_link_sdk as sdk
phase[0]=False
assert Path(sdk.__file__).resolve()==site_root/"yomihime_game_link_sdk/__init__.py"
assert sdk.__version__=="0.1.0a24" and sdk.MODULE_ABI_VERSION=="2.0"
class ConfigDouble:
    async def current(self):
        return sdk.ConfigSnapshot(1, {"region": "cn"})
services=sdk.ModuleServices(config=ConfigDouble(),identities=object(),accounts=object(),subscriptions=object(),scopes=object())
for example in ("empty_module","offline_sample"):
    package=importlib.import_module("yomihime_game_link_sdk._examples."+example)
    output=extraction_root/example
    output.mkdir(parents=True)
    for name in ("README.md","module.py","manifest.json"):
        data=importlib.resources.files(package).joinpath(name).read_bytes()
        (output/("yomihime.manifest.json" if name=="manifest.json" else name)).write_bytes(data)
    spec=importlib.util.spec_from_file_location("example_"+example,output/"module.py")
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    if example=="offline_sample":
        async def exercise():
            instance=await module.Factory().create(services)
            result=await instance.handlers().capabilities["status"].invoke(None,{})
            assert type(result) is sdk.CapabilityResult
            assert result.model_facts.facts["sample"]=="offline"
            assert type(result.document) is sdk.DisplayDocument
            assert type(result.document.ordered_blocks[0]) is sdk.TextBlock
        asyncio.run(exercise())
assert {t.ident for t in threading.enumerate()}==before
"""


class InstalledArtifactTests(unittest.TestCase):
    def test_built_wheel_installs_and_supplies_both_example_resources(self) -> None:
        self.assertEqual(version("setuptools"), "80.9.0")
        self.assertEqual(version("wheel"), "0.45.1")
        with tempfile.TemporaryDirectory(prefix="yomihime-sdk-build-") as work:
            workdir = Path(work).resolve()
            source_root = workdir / "source"
            source_root.mkdir()
            for relative in ("SDK-LICENSE", "pyproject.toml", "setup.py"):
                shutil.copy2(ROOT / relative, source_root / relative)
            (source_root / "docs").mkdir()
            shutil.copy2(
                ROOT / "docs/module-sdk.md", source_root / "docs/module-sdk.md"
            )
            shutil.copytree(
                ROOT / "yomihime_game_link_sdk",
                source_root / "yomihime_game_link_sdk",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            for example in ("empty_module", "offline_sample"):
                shutil.copytree(
                    ROOT / "examples" / example,
                    source_root / "examples" / example,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                )
            wheelhouse = workdir / "wheelhouse"
            wheelhouse.mkdir()
            env = os.environ.copy()
            env["SOURCE_DATE_EPOCH"] = "315532800"

            def build_wheel(destination: Path) -> Path:
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "pip",
                        "wheel",
                        "--no-index",
                        "--no-deps",
                        "--no-cache-dir",
                        "--no-build-isolation",
                        "--wheel-dir",
                        str(destination),
                        str(source_root),
                    ],
                    check=True,
                    cwd=source_root,
                    env=env,
                    capture_output=True,
                    text=True,
                )
                wheels = tuple(destination.glob("*.whl"))
                self.assertEqual(len(wheels), 1)
                return wheels[0]

            wheel = build_wheel(wheelhouse)
            wheel_digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
            self.assertEqual(wheel_digest, EXPECTED_WHEEL_SHA256)
            self.assertEqual(
                wheel.name, "yomihime_game_link_sdk-0.1.0a24-py3-none-any.whl"
            )
            with zipfile.ZipFile(wheel) as archive:
                entries = set(archive.namelist())
                self.assertFalse(
                    any(
                        name.endswith(".pyc") or "__pycache__" in name
                        for name in entries
                    )
                )
                self.assertTrue(EXPECTED_RESOURCES <= entries)
                self.assertIn("yomihime_game_link_sdk/__init__.py", entries)
                self.assertIn("yomihime_game_link_sdk/py.typed", entries)
                self.assertIn("yomihime_game_link_sdk/results.py", entries)
                self.assertIn("yomihime_game_link_sdk/services.py", entries)
                self.assertFalse(
                    any(
                        name.startswith("yomihime_game_link_sdk/_api/")
                        for name in entries
                    )
                )
                self.assertFalse(any(name.startswith("api/") for name in entries))
                metadata_name = next(
                    name for name in entries if name.endswith(".dist-info/METADATA")
                )
                metadata = archive.read(metadata_name).decode("utf-8")
                self.assertIn("Requires-Python: >=3.11", metadata)
                self.assertNotIn("Requires-Dist:", metadata)

            # Reproduce `compileall .` residue in both the source tree and
            # setuptools' incremental build tree. The next wheel must remain
            # byte-for-byte pinned.
            subprocess.run(
                [sys.executable, "-m", "compileall", "-q", "."],
                check=True,
                cwd=source_root,
                capture_output=True,
                text=True,
            )
            compiled_wheelhouse = workdir / "compiled-wheelhouse"
            compiled_wheelhouse.mkdir()
            compiled_wheel = build_wheel(compiled_wheelhouse)
            compiled_digest = hashlib.sha256(compiled_wheel.read_bytes()).hexdigest()
            self.assertEqual(compiled_digest, EXPECTED_WHEEL_SHA256)
            self.assertEqual(compiled_digest, wheel_digest)
            with zipfile.ZipFile(compiled_wheel) as archive:
                self.assertFalse(
                    any(
                        name.endswith(".pyc") or "__pycache__" in name
                        for name in archive.namelist()
                    )
                )
            wheel = compiled_wheel

            extension_root = workdir / "extensions"
            extension_root.mkdir()
            site_root = workdir / "sdk-target"
            installed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--no-index",
                    "--no-deps",
                    "--no-cache-dir",
                    "--target",
                    str(site_root),
                    str(wheel),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                installed.returncode, 0, installed.stdout + installed.stderr
            )
            probe = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    VERIFY_INSTALLED_ARTIFACT,
                    str(site_root),
                    str(extension_root),
                ],
                cwd=workdir,
                capture_output=True,
                text=True,
            )
            self.assertEqual(probe.returncode, 0, probe.stdout + probe.stderr)
            empty = parse_manifest(
                (extension_root / "empty_module/yomihime.manifest.json").read_bytes()
            )
            sample = parse_manifest(
                (extension_root / "offline_sample/yomihime.manifest.json").read_bytes()
            )
            self.assertEqual(empty.modules, ())
            modules = {module.module_id: module for module in sample.modules}
            self.assertEqual(set(modules), {"status", "source"})
            status = modules["status"]
            source = modules["source"]
            capabilities = {item.capability_id: item for item in status.capabilities}
            for capability_id in (
                "account_bind",
                "account_list",
                "account_unbind",
                "subscription_create",
                "subscription_list",
                "subscription_cancel",
            ):
                self.assertEqual(
                    capabilities[capability_id].privacy_floor.value, "public"
                )
                self.assertEqual(
                    capabilities[capability_id].invocation_policy.value,
                    "command_only",
                )
            self.assertEqual(
                capabilities["private_status"].privacy_floor.value, "private"
            )
            self.assertEqual(
                (
                    len(status.capabilities),
                    len(status.commands),
                    len(status.tools),
                    len(status.config_fields),
                    len(status.schedules),
                    len(status.subscriptions),
                ),
                (14, 14, 3, 3, 2, 2),
            )
            self.assertEqual(
                (
                    len(source.capabilities),
                    len(source.commands),
                    len(source.tools),
                    len(source.config_fields),
                    len(source.schedules),
                    len(source.subscriptions),
                ),
                (1, 1, 0, 0, 0, 0),
            )


if __name__ == "__main__":
    unittest.main()
