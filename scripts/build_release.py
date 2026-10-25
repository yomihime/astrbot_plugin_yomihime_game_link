"""Build the plugin ZIP and independently installable, pinned SDK wheel."""

from __future__ import annotations

import argparse
import ast
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

if __package__:
    from .build_dashboard_zip import build as build_dashboard_zip
    from .build_dashboard_zip import (
        publish_artifacts,
        validate_output_paths,
        validate_wheel,
    )
else:
    from build_dashboard_zip import build as build_dashboard_zip
    from build_dashboard_zip import (
        publish_artifacts,
        validate_output_paths,
        validate_wheel,
    )

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PYTHON = (3, 13)
EXPECTED_TOOL_VERSIONS = {"setuptools": "80.9.0", "wheel": "0.45.1"}
SOURCE_DATE_EPOCH = "315532800"
SDK_SOURCE_FILES = ("SDK-LICENSE", "pyproject.toml", "setup.py")
SDK_EXAMPLE_DIRS = ("empty_module", "offline_sample")
TAG_PATTERN = re.compile(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
PLUGIN_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")


def _metadata_value(metadata_path: Path, key: str) -> str:
    lines = Path(metadata_path).read_text(encoding="utf-8").splitlines()
    matches = [
        line.split(":", 1)[1].strip()
        for line in lines
        if re.match(rf"^{re.escape(key)}\s*:", line)
    ]
    if len(matches) != 1 or not matches[0]:
        raise ValueError(f"metadata.yaml must contain exactly one nonempty {key!r}")
    value = matches[0]
    if value.startswith(("'", '"')):
        try:
            value = ast.literal_eval(value)
        except (SyntaxError, ValueError) as exc:
            raise ValueError(f"metadata.yaml has an invalid {key!r} value") from exc
    if not isinstance(value, str) or not value:
        raise ValueError(f"metadata.yaml has an invalid {key!r} value")
    return value


def plugin_metadata(repository_root: Path = ROOT) -> tuple[str, str]:
    metadata_path = Path(repository_root) / "metadata.yaml"
    plugin_name = _metadata_value(metadata_path, "name")
    plugin_version = _metadata_value(metadata_path, "version")
    if not PLUGIN_NAME_PATTERN.fullmatch(plugin_name):
        raise ValueError(f"Unsupported plugin name in metadata.yaml: {plugin_name!r}")
    if not TAG_PATTERN.fullmatch(plugin_version):
        raise ValueError(
            f"metadata.yaml version must be a vMAJOR.MINOR.PATCH tag: {plugin_version!r}"
        )
    return plugin_name, plugin_version


def validate_tag(tag: str, metadata_version: str) -> str:
    """Require a stable vMAJOR.MINOR.PATCH tag matching plugin metadata."""
    if not TAG_PATTERN.fullmatch(tag):
        raise ValueError(f"Invalid release tag {tag!r}; expected vMAJOR.MINOR.PATCH")
    if tag != metadata_version:
        raise ValueError(
            f"Release tag {tag!r} does not match metadata.yaml version {metadata_version!r}"
        )
    return tag


def validate_build_environment() -> None:
    if sys.version_info[:2] != EXPECTED_PYTHON:
        actual = f"{sys.version_info.major}.{sys.version_info.minor}"
        raise RuntimeError(f"Release builds require Python 3.13; found {actual}")
    for package, expected in EXPECTED_TOOL_VERSIONS.items():
        try:
            actual = version(package)
        except PackageNotFoundError as exc:
            raise RuntimeError(f"Release builds require {package}=={expected}") from exc
        if actual != expected:
            raise RuntimeError(
                f"Release builds require {package}=={expected}; found {actual}"
            )


def _copy_sdk_source(repository_root: Path, source_root: Path) -> None:
    source_root.mkdir(parents=True)
    for name in SDK_SOURCE_FILES:
        shutil.copy2(repository_root / name, source_root / name)
    docs_source = repository_root / "docs" / "module-sdk.md"
    docs_target = source_root / "docs" / "module-sdk.md"
    docs_target.parent.mkdir(parents=True)
    shutil.copy2(docs_source, docs_target)
    shutil.copytree(
        repository_root / "yomihime_game_link_sdk",
        source_root / "yomihime_game_link_sdk",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for example in SDK_EXAMPLE_DIRS:
        shutil.copytree(
            repository_root / "examples" / example,
            source_root / "examples" / example,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )


def build_sdk_wheel(work_root: Path, repository_root: Path = ROOT) -> Path:
    """Build the SDK from a disposable source copy, keeping build/ out of the repo."""
    repository_root = Path(repository_root).resolve(strict=True)
    source_root = Path(work_root) / "sdk-source"
    wheelhouse = Path(work_root) / "wheelhouse"
    wheelhouse.mkdir(parents=True)
    _copy_sdk_source(repository_root, source_root)

    environment = os.environ.copy()
    environment["SOURCE_DATE_EPOCH"] = SOURCE_DATE_EPOCH
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-index",
            "--no-deps",
            "--no-build-isolation",
            "--no-cache-dir",
            "--wheel-dir",
            str(wheelhouse),
            str(source_root),
        ],
        check=True,
        cwd=source_root,
        env=environment,
    )
    wheels = tuple(wheelhouse.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"Expected one SDK wheel, found {len(wheels)}")
    return wheels[0]


def write_checksums(artifacts: tuple[Path, ...], destination: Path) -> Path:
    """Write sha256sum-compatible checksums for the supplied artifacts."""
    entries = []
    for artifact in sorted(artifacts, key=lambda path: path.name):
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        entries.append(f"{digest}  {artifact.name}\n")
    destination = Path(destination)
    publish_artifacts({destination: "".join(entries).encode("ascii")}, artifacts)
    return destination


def build_release(
    tag: str | None = None,
    output_dir: Path | None = None,
    sdk_wheel: Path | None = None,
    repository_root: Path = ROOT,
    *,
    external_page_roots=(),
    external_source_policy_roots=(),
) -> tuple[Path, Path, Path]:
    """Build the validated plugin ZIP, pinned SDK wheel, and SHA256SUMS."""
    repository_root = Path(repository_root).resolve(strict=True)
    plugin_name, metadata_version = plugin_metadata(repository_root)
    if tag is not None:
        validate_tag(tag, metadata_version)
    if sdk_wheel is None:
        validate_build_environment()

    output_dir = (
        Path(output_dir) if output_dir is not None else repository_root / "dist"
    )
    zip_name = f"{plugin_name}-{metadata_version}.zip"
    wheel_name = "yomihime_game_link_sdk-0.1.0a22-py3-none-any.whl"
    output_artifacts = (output_dir / zip_name, output_dir / wheel_name)
    sums_path = output_dir / "SHA256SUMS"
    protected = (repository_root / "metadata.yaml",)
    retain_wheel = False
    if sdk_wheel is not None:
        protected += (Path(sdk_wheel),)
        try:
            retain_wheel = os.path.samefile(sdk_wheel, output_artifacts[1])
        except FileNotFoundError:
            pass
        if retain_wheel:
            # Same-file reuse is a no-op, never an exemption from source or
            # destination link/reparse/hardlink and independently pinned checks.
            validate_wheel(Path(sdk_wheel))
            validate_output_paths((output_artifacts[1],))
    destinations = (
        (output_artifacts[0], sums_path)
        if retain_wheel
        else (*output_artifacts, sums_path)
    )
    if any(
        Path(output).absolute().is_relative_to(Path(root).absolute())
        for output in destinations
        for root in external_page_roots
    ):
        raise ValueError("Release output overlaps an external page input")
    validate_output_paths(destinations, protected)

    with tempfile.TemporaryDirectory(prefix="yomihime-release-build-") as temporary:
        work_root = Path(temporary)
        source_wheel = (
            build_sdk_wheel(work_root, repository_root)
            if sdk_wheel is None
            else Path(sdk_wheel)
        )
        if source_wheel.name != wheel_name:
            raise RuntimeError(f"Unexpected SDK wheel filename: {source_wheel.name}")
        validate_wheel(source_wheel)
        staged_wheel = source_wheel if retain_wheel else work_root / wheel_name
        if not retain_wheel:
            shutil.copyfile(source_wheel, staged_wheel)
        staged_zip = work_root / zip_name
        if external_page_roots or external_source_policy_roots:
            build_dashboard_zip(
                staged_wheel,
                staged_zip,
                repository_root,
                external_page_roots=external_page_roots,
                **(
                    {"external_source_policy_roots": external_source_policy_roots}
                    if external_source_policy_roots
                    else {}
                ),
            )
        else:
            build_dashboard_zip(staged_wheel, staged_zip, repository_root)

        staged_artifacts = (staged_zip, staged_wheel)
        payloads = {
            destination: source.read_bytes()
            for source, destination in zip(
                staged_artifacts, output_artifacts, strict=True
            )
        }
        checksums = "".join(
            f"{hashlib.sha256(data).hexdigest()}  {destination.name}\n"
            for destination, data in sorted(
                payloads.items(), key=lambda item: item[0].name
            )
        ).encode("ascii")
        if retain_wheel:
            del payloads[output_artifacts[1]]
        payloads[sums_path] = checksums
        publish_artifacts(payloads, protected)
    return output_artifacts[0], output_artifacts[1], sums_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the plugin ZIP, pinned SDK wheel, and SHA256SUMS."
    )
    parser.add_argument(
        "--tag",
        help="Optional vMAJOR.MINOR.PATCH tag; must equal metadata.yaml version.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Artifact directory (defaults to the repository dist/ directory).",
    )
    parser.add_argument(
        "--sdk-wheel",
        type=Path,
        help="Reuse an existing SDK wheel after checking its pinned hash and contents.",
    )
    parser.add_argument(
        "--external-page-package",
        action="append",
        type=Path,
        default=[],
        help="Explicit reviewed external package root for fixed-pair static pages only.",
    )
    parser.add_argument(
        "--external-source-policy-package",
        action="append",
        type=Path,
        default=[],
        help="Explicit reviewed external credential/authorization policy root; requires exact paired page manifest.",
    )
    args = parser.parse_args()
    archive, wheel, checksums = build_release(
        args.tag,
        args.output_dir,
        sdk_wheel=args.sdk_wheel,
        external_page_roots=args.external_page_package,
        external_source_policy_roots=args.external_source_policy_package,
    )
    print(f"Created {archive}")
    print(f"Created {wheel}")
    print(f"Created {checksums}")


if __name__ == "__main__":
    main()
