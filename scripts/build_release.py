"""Build a complete Core release; optional SDK wheel tooling is development-only."""

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
    plugin_version = "v" + plugin_version.removeprefix("v")
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
    tag=None,
    output_dir=None,
    sdk_wheel=None,
    repository_root=ROOT,
    *,
    signing_key=None,
    channel="preview",
    sequence=1,
    external_page_roots=(),
    external_source_policy_roots=(),
):
    """Build one complete Core ZIP and SHA256SUMS; no DLC checkout needed."""
    repository_root = Path(repository_root).resolve(strict=True)
    name, metadata_version = plugin_metadata(repository_root)
    if tag is not None:
        validate_tag(tag, metadata_version)
    output_dir = (
        Path(output_dir) if output_dir is not None else repository_root / "dist"
    )
    archive = output_dir / f"{name}-{metadata_version}.zip"
    checksums = output_dir / "SHA256SUMS"
    # Preflight the entire batch before capturing or writing anything.
    from_inputs = tuple(repository_root.rglob("metadata.yaml"))
    if sdk_wheel is not None:
        validate_wheel(Path(sdk_wheel))
        from_inputs += (Path(sdk_wheel),)
    validate_output_paths((archive, checksums), from_inputs)
    with tempfile.TemporaryDirectory(prefix="ygl-complete-release-") as temporary:
        staged = Path(temporary) / archive.name
        build_dashboard_zip(
            sdk_wheel,
            staged,
            repository_root,
            signing_key=signing_key,
            channel=channel,
            sequence=sequence,
            external_page_roots=external_page_roots,
            external_source_policy_roots=external_source_policy_roots,
        )
        payload = staged.read_bytes()
        sums = f"{hashlib.sha256(payload).hexdigest()}  {archive.name}\n".encode(
            "ascii"
        )
        # Protect every captured source path, including generated static assets.
        if __package__:
            from .build_dashboard_zip import included_files
        else:
            from build_dashboard_zip import included_files
        publish_artifacts(
            {archive: payload, checksums: sums},
            (*from_inputs, *included_files(repository_root)),
        )
    return archive, checksums


def main():
    if __package__:
        from .release_envelope import signing_key
    else:
        from release_envelope import signing_key
    parser = argparse.ArgumentParser(
        description="Build a complete AstrBot Core installation ZIP."
    )
    parser.add_argument("--tag")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--signing-key", type=Path)
    parser.add_argument(
        "--test-signing",
        action="store_true",
        help="Ephemeral development key; not publisher enrollment or production signing.",
    )
    parser.add_argument("--channel", choices=("preview", "stable"))
    parser.add_argument("--sequence", type=int)
    args = parser.parse_args()
    if args.test_signing and args.signing_key is not None:
        parser.error("choose --signing-key or --test-signing")
    if not (args.test_signing or args.signing_key) and (
        args.channel is not None or args.sequence is not None
    ):
        parser.error("--channel and --sequence apply to signed releases")
    channel = args.channel or "preview"
    if args.test_signing and channel != "preview":
        parser.error("test signing is preview-only")
    for path in build_release(
        args.tag,
        args.output_dir,
        signing_key=signing_key(args.signing_key, test=args.test_signing),
        channel=channel,
        sequence=1 if args.sequence is None else args.sequence,
    ):
        print(f"Created {path}")


if __name__ == "__main__":
    main()
