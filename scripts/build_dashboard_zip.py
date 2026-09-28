"""Build a Dashboard ZIP containing only the independently pinned SDK wheel."""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
import zipfile
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "dist" / "astrbot_plugin_yomihime_game_link-local.zip"
EXPECTED_WHEEL_SHA256 = (
    "8fd73fa756b5f2a077b9fb95cd483594d75894ef76ff17f3d90a89caad7e607c"
)
EXPECTED_WHEEL_ENTRIES = {
    "yomihime_module_sdk-1.1.0.dist-info/licenses/LICENSE",
    "yomihime_module_sdk-1.1.0.dist-info/METADATA",
    "yomihime_module_sdk-1.1.0.dist-info/WHEEL",
    "yomihime_module_sdk-1.1.0.dist-info/top_level.txt",
    "yomihime_module_sdk-1.1.0.dist-info/RECORD",
    "yomihime_sdk/__init__.py",
    "yomihime_sdk/py.typed",
    "yomihime_sdk/_examples/empty_module/README.md",
    "yomihime_sdk/_examples/empty_module/manifest.json",
    "yomihime_sdk/_examples/empty_module/module.py",
    "yomihime_sdk/_examples/offline_sample/README.md",
    "yomihime_sdk/_examples/offline_sample/manifest.json",
    "yomihime_sdk/_examples/offline_sample/module.py",
    "yomihime_sdk/api/__init__.py",
    "yomihime_sdk/api/administration.py",
    "yomihime_sdk/api/contexts.py",
    "yomihime_sdk/api/display.py",
    "yomihime_sdk/api/manifests.py",
    "yomihime_sdk/api/results.py",
    "yomihime_sdk/api/schema.py",
    "yomihime_sdk/api/services.py",
    "yomihime_sdk/api/storage.py",
    "yomihime_sdk/api/subscriptions.py",
    "yomihime_sdk/api/validation.py",
    "yomihime_sdk/api/version.py",
}
PACKAGE_ENTRY_NAMES = {
    name for name in EXPECTED_WHEEL_ENTRIES if name.startswith("yomihime_sdk/")
}
ROOT_FILES = (
    "main.py",
    "__init__.py",
    "metadata.yaml",
    "logo.png",
    "LICENSE",
    "README.md",
    "CHANGELOG.md",
)
RUNTIME_DIRS = (
    "adapters",
    "api",
    "core",
    "extensions",
    "services",
    "infrastructure",
    "presentation",
)
ALLOWED_SUFFIXES = {".py", ".sql"}
EXCLUDED_RUNTIME_DIRS = {
    "__pycache__",
    ".cache",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "cache",
    "caches",
    "data",
    "instance",
    "local-data",
    "local_data",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _inside(path: Path, root: Path, label: str) -> Path:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes its allowed root") from exc
    return path


def _reject_link(path: Path, metadata: os.stat_result, label: str) -> None:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    is_junction = getattr(path, "is_junction", lambda: False)()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or is_junction
        or bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)
    ):
        raise ValueError(
            f"{label} must not be a symlink, junction, or reparse point: {path}"
        )
    if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink > 1:
        raise ValueError(f"{label} must not be a hard link: {path}")


def _path_parts(path: Path) -> list[Path]:
    parts = [path]
    parts.extend(path.parents)
    return list(reversed(parts))


def validate_source_path(
    path: Path, allowed_root: Path, label: str = "Package input"
) -> Path:
    """Resolve a package input only after rejecting links and root escapes."""
    root = Path(allowed_root).resolve(strict=True)
    candidate = Path(os.path.abspath(path))
    _inside(candidate, root, label)
    relative = candidate.relative_to(root)
    current = root
    root_metadata = current.lstat()
    _reject_link(current, root_metadata, label)
    for part in relative.parts:
        current = current / part
        metadata = current.lstat()
        _reject_link(current, metadata, label)
    resolved = candidate.resolve(strict=True)
    _inside(resolved, root, label)
    return resolved


def _validate_artifact_path(path: Path) -> Path:
    """Reject linked/reparse components for the explicitly supplied wheel."""
    candidate = Path(os.path.abspath(path))
    for component in _path_parts(candidate):
        metadata = component.lstat()
        _reject_link(component, metadata, "SDK wheel input")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("SDK wheel input must be a regular file")
    return resolved


def _file_token(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_nlink,
    )


def _read_snapshot(
    path: Path, allowed_root: Path | None = None, label: str = "Package input"
) -> bytes:
    """Read one opened file object after checking its path and handle identity."""
    candidate = Path(path)
    safe_path = (
        validate_source_path(candidate, allowed_root, label)
        if allowed_root is not None
        else _validate_artifact_path(candidate)
    )
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(safe_path, flags)
    try:
        opened_before = os.fstat(descriptor)
        _reject_link(safe_path, opened_before, label)
        if not stat.S_ISREG(opened_before.st_mode):
            raise ValueError(f"{label} must be a regular file: {safe_path}")

        current_path = (
            validate_source_path(candidate, allowed_root, label)
            if allowed_root is not None
            else _validate_artifact_path(candidate)
        )
        current_before = current_path.stat()
        if _file_token(opened_before) != _file_token(current_before):
            raise ValueError(f"{label} changed while it was being opened: {candidate}")

        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            content = stream.read()

        opened_after = os.fstat(descriptor)
        current_path = (
            validate_source_path(candidate, allowed_root, label)
            if allowed_root is not None
            else _validate_artifact_path(candidate)
        )
        current_after = current_path.stat()
        if _file_token(opened_before) != _file_token(opened_after) or _file_token(
            opened_after
        ) != _file_token(current_after):
            raise ValueError(
                f"{label} changed while its snapshot was read: {candidate}"
            )
        return content
    finally:
        os.close(descriptor)


def validate_wheel(path: Path) -> dict[str, bytes]:
    wheel_path = _validate_artifact_path(path)
    wheel_bytes = _read_snapshot(wheel_path, label="SDK wheel input")
    if _sha256(wheel_bytes) != EXPECTED_WHEEL_SHA256:
        raise ValueError("SDK wheel does not match the independently pinned SHA-256")
    with zipfile.ZipFile(BytesIO(wheel_bytes)) as wheel:
        names = wheel.namelist()
        if len(names) != len(set(names)) or set(names) != EXPECTED_WHEEL_ENTRIES:
            raise ValueError(
                "SDK wheel entry manifest differs from the reviewed artifact"
            )
        metadata_lines = set(
            wheel.read("yomihime_module_sdk-1.1.0.dist-info/METADATA")
            .decode("utf-8")
            .splitlines()
        )
        if not {"Name: yomihime-module-sdk", "Version: 1.1.0"} <= metadata_lines:
            raise ValueError("SDK wheel distribution metadata is unsupported")
        package = {name: wheel.read(name) for name in sorted(PACKAGE_ENTRY_NAMES)}
    return package


def _runtime_files(directory: Path, repository_root: Path) -> list[Path]:
    found: list[Path] = []
    pending = [directory]
    while pending:
        current = pending.pop()
        for entry in sorted(current.iterdir(), key=lambda item: item.name):
            resolved = validate_source_path(entry, repository_root, "Runtime source")
            metadata = resolved.stat()
            if stat.S_ISDIR(metadata.st_mode):
                if entry.name not in EXCLUDED_RUNTIME_DIRS:
                    pending.append(resolved)
            elif stat.S_ISREG(metadata.st_mode):
                if resolved.suffix in ALLOWED_SUFFIXES:
                    found.append(resolved)
            else:
                raise ValueError(
                    f"Runtime source is not a regular file or directory: {entry}"
                )
    return found


def included_files(root: Path = ROOT) -> list[Path]:
    repository_root = Path(root).resolve(strict=True)
    files = [
        validate_source_path(
            repository_root / name, repository_root, "Plugin root input"
        )
        for name in ROOT_FILES
    ]
    for directory in RUNTIME_DIRS:
        runtime_root = validate_source_path(
            repository_root / directory, repository_root, "Runtime directory"
        )
        files.extend(_runtime_files(runtime_root, repository_root))
    return sorted(
        set(files), key=lambda path: path.relative_to(repository_root).as_posix()
    )


def build(
    sdk_wheel: Path,
    archive_path: Path = ARCHIVE,
    repository_root: Path = ROOT,
) -> Path:
    package = validate_wheel(sdk_wheel)
    repository_root = Path(repository_root).resolve(strict=True)
    files = included_files(repository_root)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        archive_path, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        for path in files:
            name = path.relative_to(repository_root).as_posix()
            content = _read_snapshot(path, repository_root, "Runtime source")
            _write_entry(archive, name, content)
        for name, data in package.items():
            _write_entry(archive, name, data)

    with zipfile.ZipFile(archive_path) as result:
        names = result.namelist()
        expected_names = {
            path.relative_to(repository_root).as_posix() for path in files
        } | set(package)
        if len(names) != len(set(names)) or set(names) != expected_names:
            raise RuntimeError("Generated ZIP entry manifest is not exact")
        for name, data in package.items():
            if result.read(name) != data:
                raise RuntimeError(f"SDK bytes differ from the pinned wheel: {name}")
        if result.testzip() is not None:
            raise RuntimeError("The generated ZIP failed its integrity check")
    return archive_path


def _write_entry(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, data)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sdk-wheel",
        required=True,
        type=Path,
        help="Path to the offline K-reviewed wheel; its digest is checked against the built-in pin.",
    )
    args = parser.parse_args()
    archive = build(args.sdk_wheel)
    print(f"Created {archive}")
    print(f"Included {len(included_files()) + len(PACKAGE_ENTRY_NAMES)} files")


if __name__ == "__main__":
    main()
