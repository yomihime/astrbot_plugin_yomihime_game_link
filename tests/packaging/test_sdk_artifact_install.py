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

from extensions.disk_manifest import parse_manifest
from scripts.build_release import build_release

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_WHEEL_SHA256 = (
    "3c1a911d510507a4b4ddebfd254872a5f276d48c159e015f9a1277f20f0736d3"
)
EXPECTED_RESOURCES = {
    f"yomihime_sdk/_examples/{example}/{filename}"
    for example in ("empty_module", "offline_sample")
    for filename in ("manifest.json", "module.py", "README.md")
}


VERIFY_INSTALLED_ARTIFACT = r"""
import ast
import asyncio
import hashlib
import importlib
import importlib.resources
import importlib.util
import inspect
import json
import os
import sys
import threading
from pathlib import Path

site_root, extraction_root, repository_root = map(Path, sys.argv[1:4])
sys.path.insert(0, str(site_root))

main_tree = ast.parse((repository_root / "main.py").read_text(encoding="utf-8"))
sdk_manifest = next(
    ast.literal_eval(statement.value)
    for statement in main_tree.body
    if isinstance(statement, ast.Assign)
    and any(
        isinstance(target, ast.Name) and target.id == "SDK_PACKAGE_MANIFEST"
        for target in statement.targets
    )
)
installed_sdk_root = site_root / "yomihime_sdk"
installed_payload = {
    path.relative_to(installed_sdk_root).as_posix()
    for path in installed_sdk_root.rglob("*")
    if path.is_file() and "__pycache__" not in path.parts
}
assert len(sdk_manifest) == 20
assert installed_payload == set(sdk_manifest)
for relative, expected_digest in sdk_manifest.items():
    assert hashlib.sha256(
        (installed_sdk_root / relative).read_bytes()
    ).hexdigest() == expected_digest

spec = importlib.util.spec_from_file_location(
    "ygl_artifact_subject",
    repository_root / "__init__.py",
    submodule_search_locations=[str(repository_root)],
)
assert spec is not None and spec.loader is not None
plugin = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = plugin
spec.loader.exec_module(plugin)

import yomihime_sdk as sdk
assert Path(sdk.__file__).resolve() == installed_sdk_root / "__init__.py"
assert len(sdk.__path__) == 1
assert Path(sdk.__path__[0]).resolve() == installed_sdk_root
from yomihime_sdk.api.display import DisplayDocument as CanonicalDisplayDocument
from yomihime_sdk.api.results import CapabilityResult as CanonicalCapabilityResult
from yomihime_sdk.api.results import FactDocument as CanonicalFactDocument
from yomihime_sdk.api.results import ResultStatus as CanonicalResultStatus
from yomihime_sdk.api.services import ModuleHandlers as CanonicalModuleHandlers
from yomihime_sdk.api.services import ModuleServices as CanonicalModuleServices
from yomihime_sdk.api.display import TextBlock as CanonicalTextBlock
from yomihime_sdk.api.services import HealthStatus as CanonicalHealthStatus
from yomihime_sdk.api.services import SourceHttpError as CanonicalSourceHttpError

from ygl_artifact_subject.api.display import TextBlock as ShimTextBlock
from ygl_artifact_subject.api.results import CapabilityResult as ShimCapabilityResult
from ygl_artifact_subject.api.results import FactDocument as ShimFactDocument
from ygl_artifact_subject.api.results import ResultStatus as ShimResultStatus
from ygl_artifact_subject.api.services import ModuleHandlers as ShimModuleHandlers
from ygl_artifact_subject.api.services import ModuleServices as ShimModuleServices
from ygl_artifact_subject.api.services import HealthStatus as ShimHealthStatus
from ygl_artifact_subject.core.invocation import CapabilityResult as GatewayResultType
from ygl_artifact_subject.core.invocation import Gateway
from ygl_artifact_subject.services.module_services import ModuleServices as RuntimeModuleServices
from ygl_artifact_subject.services.output import CapabilityResult as OutputResultType
from ygl_artifact_subject.extensions.disk_manifest import parse_manifest

assert sdk.CapabilityResult is CanonicalCapabilityResult is ShimCapabilityResult
assert sdk.ModuleServices is CanonicalModuleServices is ShimModuleServices
assert CanonicalModuleServices is RuntimeModuleServices
assert sdk.ModuleHandlers is CanonicalModuleHandlers is ShimModuleHandlers
assert sdk.DisplayDocument is CanonicalDisplayDocument
assert sdk.TextBlock is CanonicalTextBlock is ShimTextBlock
assert sdk.FactDocument is CanonicalFactDocument is ShimFactDocument
assert sdk.ResultStatus is CanonicalResultStatus is ShimResultStatus
assert sdk.HealthStatus is CanonicalHealthStatus is ShimHealthStatus
assert sdk.SourceHttpError is CanonicalSourceHttpError
assert GatewayResultType is sdk.CapabilityResult
assert OutputResultType is sdk.CapabilityResult

assert sdk.__version__ == "1.7.0"
assert sdk.CONTRACT_VERSION == "1.7.0"
assert sdk.CONTRACT_REVISION == "MODULE-LIFECYCLE-01"
assert sdk.COMPATIBLE_CONTRACT_VERSIONS == (
    "1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0", "1.5.0", "1.6.0", "1.7.0"
)
assert all(
    sdk.is_compatible_contract_version(version)
    for version in ("1.0.0", "1.1.0", "1.2.0", "1.3.0")
)
assert sdk.PrivacyFloor.OWNER.value == "owner"
assert sdk.InvocationOrigin("web_public") is sdk.InvocationOrigin.WEB_PUBLIC
assert sdk.InvocationPolicy("command_and_public_web") is sdk.InvocationPolicy.COMMAND_AND_PUBLIC_WEB
web_capability = sdk.CapabilityDescriptor(
    "public_web_read", {"type": "object"},
    sdk.InvocationPolicy.COMMAND_AND_PUBLIC_WEB,
    sdk.CapabilityEffect.READ_ONLY,
)
assert web_capability.privacy_floor is sdk.PrivacyFloor.PUBLIC
for privacy, effect in (
    (sdk.PrivacyFloor.PRIVATE, sdk.CapabilityEffect.READ_ONLY),
    (sdk.PrivacyFloor.OWNER, sdk.CapabilityEffect.READ_ONLY),
    (sdk.PrivacyFloor.PUBLIC, sdk.CapabilityEffect.WRITE),
):
    try:
        sdk.CapabilityDescriptor(
            "rejected_web", {"type": "object"},
            sdk.InvocationPolicy.COMMAND_AND_PUBLIC_WEB,
            effect, privacy_floor=privacy,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("installed wheel widened public web permissions")
owner_capability = sdk.CapabilityDescriptor(
    "owner_read",
    {"type": "object"},
    sdk.InvocationPolicy.COMMAND_ONLY,
    sdk.CapabilityEffect.READ_ONLY,
    privacy_floor=sdk.PrivacyFloor.OWNER,
)
assert owner_capability.privacy_floor is sdk.PrivacyFloor.OWNER
try:
    sdk.CapabilityDescriptor(
        "owner_read",
        {"type": "object"},
        sdk.InvocationPolicy.NATURAL_LANGUAGE_ALLOWED,
        sdk.CapabilityEffect.READ_ONLY,
        privacy_floor=sdk.PrivacyFloor.OWNER,
    )
except ValueError as exc:
    assert "command_only" in str(exc)
else:
    raise AssertionError("OWNER capability accepted a non-command invocation")
assert len(sdk.__all__) == len(set(sdk.__all__))
assert all(hasattr(sdk, name) for name in sdk.__all__)
assert inspect.iscoroutinefunction(sdk.ModuleFactory.create)
assert tuple(sdk.ModuleServices.__annotations__) == (
    "config", "identities", "accounts", "subscriptions", "scopes", "storage"
)
before_threads = {thread.ident for thread in threading.enumerate()}

class _ConfigDouble:
    pass

class _IdentityDouble:
    pass

class _AccountOperationsDouble:
    pass

class _SubscriptionOperationsDouble:
    pass

class _ScopeBinderDouble:
    pass

services = sdk.ModuleServices(
    config=_ConfigDouble(),
    identities=_IdentityDouble(),
    accounts=_AccountOperationsDouble(),
    subscriptions=_SubscriptionOperationsDouble(),
    scopes=_ScopeBinderDouble(),
)

for example in ("empty_module", "offline_sample"):
    package = importlib.import_module(f"yomihime_sdk._examples.{example}")
    resource = importlib.resources.files(package)
    output = extraction_root / example
    output.mkdir(parents=True)
    for name in ("manifest.json", "module.py", "README.md"):
        data = resource.joinpath(name).read_bytes()
        target_name = "yomihime.manifest.json" if name == "manifest.json" else name
        (output / target_name).write_bytes(data)

    if example == "empty_module":
        module_path = output / "module.py"
        spec = importlib.util.spec_from_file_location("empty_template_module", module_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        async def exercise_empty_factory():
            # Deliberately a fake service value: the no-op template ignores it.
            instance = await module.Factory().create(object())
            handlers = instance.handlers()
            assert handlers.capabilities == {}
            assert handlers.collectors == {}
            assert handlers.evaluators == {}
            assert (await instance.check_health()).capabilities == {}

        asyncio.run(exercise_empty_factory())
        continue
    module_path = output / "module.py"
    spec = importlib.util.spec_from_file_location("offline_sample_module", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    async def exercise_factory():
        instance = await module.Factory().create(services)
        source_instance = await module.SourceFactory().create(services)
        handlers = instance.handlers()
        source_handlers = source_instance.handlers()
        assert set(handlers.capabilities) == {
            "status", "from_source", "configured_status", "account_bind",
            "account_list", "account_unbind", "subscription_create",
            "subscription_list", "subscription_cancel", "private_status",
        }
        assert set(handlers.collectors) == {"public_catalog", "private_catalog"}
        assert set(handlers.evaluators) == {"sample_matcher"}
        assert set(source_handlers.capabilities) == {"read"}

        manifest_path = output / "yomihime.manifest.json"
        manifest = parse_manifest(manifest_path.read_bytes())
        declared = {item.module_id: item for item in manifest.modules}
        assert set(declared) == {"status", "source"}
        status = declared["status"]
        source = declared["source"]
        assert (len(status.commands), len(status.tools)) == (10, 2)
        assert (len(status.schedules), len(status.subscriptions)) == (2, 2)
        assert len(status.config_fields) == 3
        assert (len(source.commands), len(source.tools)) == (1, 0)
        assert (len(source.schedules), len(source.subscriptions)) == (0, 0)
        assert len(source.config_fields) == 0

        result = await handlers.capabilities["status"].invoke(None, {})
        assert isinstance(result, sdk.CapabilityResult)
        assert result.document is not None
        assert result.model_facts is not None
        assert result.model_facts.facts["sample"] == "offline"
        assert type(result) is CanonicalCapabilityResult
        assert type(result.status) is CanonicalResultStatus
        assert type(result.document) is CanonicalDisplayDocument
        assert type(result.document.ordered_blocks[0]) is CanonicalTextBlock
        assert type(result.model_facts) is CanonicalFactDocument
        health = await instance.check_health()
        assert health.capabilities["status"].status is sdk.HealthStatus.AVAILABLE

        source_result = await source_handlers.capabilities["read"].invoke(None, {})
        assert source_result.model_facts is not None
        assert source_result.model_facts.facts["value"] == 7
        checked = Gateway._valid_public_result(result)
        assert type(checked) is sdk.CapabilityResult
        assert type(checked.document.ordered_blocks[0]) is sdk.TextBlock
        assert type(checked.model_facts) is sdk.FactDocument
    asyncio.run(exercise_factory())

assert {thread.ident for thread in threading.enumerate()} == before_threads
"""


class InstalledArtifactTests(unittest.TestCase):
    def test_built_wheel_installs_and_supplies_both_example_resources(self) -> None:
        self.assertEqual(version("setuptools"), "80.9.0")
        self.assertEqual(version("wheel"), "0.45.1")
        with tempfile.TemporaryDirectory(prefix="yomihime-sdk-build-") as work:
            workdir = Path(work).resolve()
            source_root = workdir / "source"
            source_root.mkdir()
            for relative in ("LICENSE", "pyproject.toml", "setup.py"):
                shutil.copy2(ROOT / relative, source_root / relative)
            (source_root / "docs").mkdir()
            shutil.copy2(
                ROOT / "docs/module-sdk.md", source_root / "docs/module-sdk.md"
            )
            shutil.copytree(
                ROOT / "yomihime_sdk",
                source_root / "yomihime_sdk",
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
            self.assertEqual(wheel.name, "yomihime_module_sdk-1.7.0-py3-none-any.whl")
            with zipfile.ZipFile(wheel) as archive:
                entries = set(archive.namelist())
                self.assertFalse(
                    any(
                        name.endswith(".pyc") or "__pycache__" in name
                        for name in entries
                    )
                )
                self.assertTrue(EXPECTED_RESOURCES <= entries)
                self.assertIn("yomihime_sdk/__init__.py", entries)
                self.assertIn("yomihime_sdk/py.typed", entries)
                self.assertIn("yomihime_sdk/api/results.py", entries)
                self.assertIn("yomihime_sdk/api/services.py", entries)
                self.assertFalse(
                    any(name.startswith("yomihime_sdk/_api/") for name in entries)
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
            release_root = workdir / "release"
            plugin_root = workdir / "astrbot_plugin_yomihime_game_link"
            plugin_root.mkdir()
            archive_path, _, _ = build_release(
                sdk_wheel=wheel,
                output_dir=release_root,
                repository_root=ROOT,
            )
            with zipfile.ZipFile(archive_path) as archive:
                archive.extractall(plugin_root)

            # Install the reviewed wheel into the extracted plugin root, the
            # same location from which the host bootstrap loads its pinned SDK.
            shutil.rmtree(plugin_root / "yomihime_sdk")
            install_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--no-index",
                    "--no-deps",
                    "--no-cache-dir",
                    "--target",
                    str(plugin_root),
                    str(wheel),
                ],
                check=False,
                cwd=workdir,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                install_result.returncode,
                0,
                f"SDK wheel installation failed:\n{install_result.stdout}\n{install_result.stderr}",
            )
            probe = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    VERIFY_INSTALLED_ARTIFACT,
                    str(plugin_root),
                    str(extension_root),
                    str(plugin_root),
                ],
                check=False,
                cwd=workdir,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                probe.returncode,
                0,
                f"installed release probe failed:\n{probe.stdout}\n{probe.stderr}",
            )

            environment = os.environ.copy()
            environment.pop("PYTHONPATH", None)
            installed_package = plugin_root.name
            entrypoints = (
                f"{installed_package}.scripts.configure_source_credentials",
                f"{installed_package}.scripts.admin_credentials",
            )
            sdk_origin_probe = """
import pathlib, runpy, sys
parent = pathlib.Path(sys.argv[1]).resolve()
module_name = sys.argv[2]
sys.path.insert(0, str(parent))
sys.argv = [module_name, "--help"]
try:
    runpy.run_module(module_name, run_name="__main__")
except SystemExit as exc:
    assert exc.code in (None, 0), exc.code
else:
    raise AssertionError("--help did not exit")
plugin_root = parent / module_name.split(".", 1)[0]
import yomihime_sdk
assert pathlib.Path(yomihime_sdk.__file__).resolve() == plugin_root / "yomihime_sdk" / "__init__.py"
assert "astrbot_plugin_yomihime_game_link.main" not in sys.modules
"""
            external_sdk_probe = """
import pathlib, runpy, sys
parent = pathlib.Path(sys.argv[1]).resolve()
module_name = sys.argv[2]
external_root = pathlib.Path(sys.argv[3]).resolve()
sys.path.insert(0, str(parent))
sys.path.insert(0, str(external_root))
import yomihime_sdk
assert pathlib.Path(yomihime_sdk.__file__).resolve().is_relative_to(external_root)
sys.argv = [module_name, "--help"]
try:
    runpy.run_module(module_name, run_name="__main__")
except RuntimeError as exc:
    assert "pinned plugin-local SDK" in str(exc)
else:
    raise AssertionError("a preloaded external SDK was accepted")
assert "astrbot_plugin_yomihime_game_link.main" not in sys.modules
"""
            for module_name in entrypoints:
                help_result = subprocess.run(
                    [sys.executable, "-m", module_name, "--help"],
                    cwd=workdir,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    help_result.returncode,
                    0,
                    f"installed CLI help failed: {module_name}\n"
                    f"{help_result.stdout}\n{help_result.stderr}",
                )
                origin_result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        sdk_origin_probe,
                        str(workdir),
                        module_name,
                    ],
                    cwd=workdir,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    origin_result.returncode,
                    0,
                    f"installed SDK origin failed: {module_name}\n"
                    f"{origin_result.stdout}\n{origin_result.stderr}",
                )
                external_root = workdir / f"external-{module_name.rsplit('.', 1)[-1]}"
                shutil.copytree(
                    plugin_root / "yomihime_sdk", external_root / "yomihime_sdk"
                )
                external_result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        external_sdk_probe,
                        str(workdir),
                        module_name,
                        str(external_root),
                    ],
                    cwd=workdir,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    external_result.returncode,
                    0,
                    f"external SDK was not rejected: {module_name}\n"
                    f"{external_result.stdout}\n{external_result.stderr}",
                )

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
                (10, 10, 2, 3, 2, 2),
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
