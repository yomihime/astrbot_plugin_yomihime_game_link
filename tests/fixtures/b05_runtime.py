"""Offline builder and isolated scanner probe for the R artifact preflight."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_WHEEL_SHA256 = (
    "84cd029d271c1dce09935a7eba26a1b0f3229fa44369ee618d82ce03774ad73e"
)


@dataclass(frozen=True, slots=True)
class InstalledSdk:
    """A freshly built and installed SDK wheel with its verified digest."""

    wheel_path: Path
    site_root: Path
    sha256: str


def build_and_install_pinned_sdk(work_root: Path) -> InstalledSdk:
    """Build and install the reviewed wheel without network or repo build residue."""

    if version("setuptools") != "80.9.0" or version("wheel") != "0.45.1":
        raise RuntimeError(
            "R preflight requires the K-reviewed local build tool versions"
        )

    work = Path(work_root)
    source = work / "sdk-source"
    wheelhouse = work / "wheelhouse"
    site_root = work / "installed-sdk"
    for path in (source, wheelhouse, site_root):
        path.mkdir()

    for filename in ("LICENSE", "pyproject.toml", "setup.py"):
        shutil.copy2(REPOSITORY_ROOT / filename, source / filename)
    (source / "docs").mkdir()
    shutil.copy2(
        REPOSITORY_ROOT / "docs" / "module-sdk.md", source / "docs" / "module-sdk.md"
    )
    shutil.copytree(
        REPOSITORY_ROOT / "yomihime_sdk",
        source / "yomihime_sdk",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for example in ("empty_module", "offline_sample"):
        shutil.copytree(
            REPOSITORY_ROOT / "examples" / example,
            source / "examples" / example,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )

    environment = os.environ.copy()
    environment["SOURCE_DATE_EPOCH"] = "315532800"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-cache-dir",
            "--no-index",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheelhouse),
            str(source),
        ],
        check=True,
        cwd=source,
        env=environment,
        capture_output=True,
        text=True,
    )
    wheels = tuple(wheelhouse.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("SDK build did not produce exactly one wheel")
    wheel = wheels[0]
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if wheel.name != "yomihime_module_sdk-1.8.0-py3-none-any.whl":
        raise RuntimeError(
            "SDK wheel name or version does not match the reviewed artifact"
        )
    if digest != EXPECTED_WHEEL_SHA256:
        raise RuntimeError("SDK wheel does not match the reviewed SHA-256 pin")

    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "--no-index",
            "--no-deps",
            "--target",
            str(site_root),
            str(wheel),
        ],
        check=True,
        cwd=work,
        capture_output=True,
        text=True,
    )
    return InstalledSdk(wheel_path=wheel, site_root=site_root, sha256=digest)


STATIC_SCAN_PROBE = r"""
import importlib.metadata
import importlib.util
import json
import sys
import threading
from pathlib import Path

site_root, repository_root, extension_root = map(Path, sys.argv[1:4])
sys.path.insert(0, str(repository_root))
sys.path.insert(0, str(site_root))

spec = importlib.util.spec_from_file_location(
    "ygl_r_preflight_subject",
    repository_root / "__init__.py",
    submodule_search_locations=[str(repository_root)],
)
assert spec is not None and spec.loader is not None
plugin = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = plugin
spec.loader.exec_module(plugin)

import yomihime_sdk
from ygl_r_preflight_subject.extensions.discovery import DiscoveryRootError
from ygl_r_preflight_subject.extensions.loader import CandidateState, ExtensionLoader
from ygl_r_preflight_subject.core.registry import Registry
from ygl_r_preflight_subject.services.b05_runtime import (
    extract_installed_sdk_examples,
)

assert Path(yomihime_sdk.__file__).resolve().is_relative_to(site_root.resolve())
assert importlib.metadata.version("yomihime-module-sdk") == "1.8.0"
before_threads = {thread.ident for thread in threading.enumerate()}
registry = Registry()
before_snapshot = registry.snapshot()
examples = extract_installed_sdk_examples(extension_root)
assert tuple(example.package_id for example in examples) == (
    "empty_module", "offline_sample"
)
try:
    candidates = ExtensionLoader().scan(str(extension_root))
except DiscoveryRootError as exc:
    if "handle-relative no-follow filesystem support" in str(exc):
        print(json.dumps({"unsupported": str(exc)}))
        raise SystemExit(0)
    raise
assert registry.snapshot() is before_snapshot
assert before_snapshot.modules == {}
assert before_snapshot.routes == {}
assert before_snapshot.tools == {}
assert {thread.ident for thread in threading.enumerate()} == before_threads

example_files = {
    path.resolve()
    for path in extension_root.rglob("*")
    if path.is_file()
}
loaded_from_extension = set()
for module in tuple(sys.modules.values()):
    origin = getattr(module, "__file__", None)
    if isinstance(origin, str):
        try:
            resolved = Path(origin).resolve()
        except OSError:
            continue
        if resolved in example_files:
            loaded_from_extension.add(resolved)
assert not loaded_from_extension

summary = []
for candidate in candidates:
    package = candidate.package
    assert candidate.state is CandidateState.DISABLED
    assert package.valid and package.enabled is False
    modules = []
    for module in package.manifest.modules:
        modules.append({
            "module_id": module.module_id,
            "capabilities": len(module.capabilities),
            "commands": len(module.commands),
            "tools": len(module.tools),
            "config_fields": len(module.config_fields),
            "schedules": len(module.schedules),
            "subscriptions": len(module.subscriptions),
        })
    summary.append({
        "package_id": package.package_id,
        "state": candidate.state.value,
        "enabled": package.enabled,
        "reason_code": candidate.reason_code,
        "modules": modules,
    })

assert {item["package_id"] for item in summary} == {
    "empty_module", "offline_sample"
}
expected_modules = {
    "empty_module": [],
    "offline_sample": [
        {
            "module_id": "status",
            "capabilities": 10,
            "commands": 10,
            "tools": 2,
            "config_fields": 3,
            "schedules": 2,
            "subscriptions": 2,
        },
        {
            "module_id": "source",
            "capabilities": 1,
            "commands": 1,
            "tools": 0,
            "config_fields": 0,
            "schedules": 0,
            "subscriptions": 0,
        },
    ],
}
assert {
    item["package_id"]: item["modules"]
    for item in summary
} == expected_modules
print(json.dumps(summary, sort_keys=True))
"""


def run_installed_artifact_static_scan(
    installed: InstalledSdk,
    extension_root: Path,
) -> list[dict[str, object]]:
    """Run resource extraction and E scan in an isolated installed-wheel process."""

    repository_root = REPOSITORY_ROOT
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            STATIC_SCAN_PROBE,
            str(installed.site_root),
            str(repository_root),
            str(extension_root),
        ],
        check=True,
        cwd=installed.site_root.parent,
        capture_output=True,
        text=True,
    )
    import json

    return json.loads(result.stdout)


__all__ = [
    "EXPECTED_WHEEL_SHA256",
    "InstalledSdk",
    "build_and_install_pinned_sdk",
    "run_installed_artifact_static_scan",
]
