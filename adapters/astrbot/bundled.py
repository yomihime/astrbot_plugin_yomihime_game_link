"""Install the reviewed FF14 source bundle into AstrBot's extension data root."""

from __future__ import annotations

import hashlib
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ...extensions.discovery import DiscoveredPackage, discover_packages
from ...extensions.source_snapshot import (
    MAX_PACKAGE_SOURCE_BYTES,
    SourceSnapshotError,
    SourceSnapshotLimits,
    capture_source_bundle,
)

PACKAGE_ID = "ff14"
PACKAGE_DIRECTORY = "ff14"
MANIFEST_FILENAME = "yomihime.manifest.json"
README_FILENAME = "README.md"
MAX_README_BYTES = 1 * 1024 * 1024
IGNORED_DIRECTORIES = {
    "__pycache__",
    ".cache",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "cache",
    "caches",
}


@dataclass(frozen=True, slots=True)
class BundledExtensionInstallation:
    """Outcome of installing the built-in package into its content root."""

    extension_root: Path
    package_dir: Path
    installed: bool
    trusted: bool
    reason: str | None = None


class BundledExtensionError(ValueError):
    """The packaged FF14 bundle cannot be validated or safely installed."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def install_bundled_ff14(
    plugin_root: str | os.PathLike[str], data_dir: str | os.PathLike[str]
) -> BundledExtensionInstallation:
    """Install FF14 into an immutable content-addressed extension root.

    The package source is statically discovered and captured before any write.
    The fingerprint covers all runtime source plus the manifest and README.
    A fresh version slot is staged outside the Core scan root and published by
    one same-filesystem rename. Existing slots and the legacy user extension
    directory are never modified; only a slot matching the current bytes is
    trusted.
    """
    plugin_root_path = _require_directory(Path(plugin_root), "plugin_root")
    source_root = _require_directory(plugin_root_path / "modules", "bundle_root")
    data_root = _require_directory(Path(data_dir), "data_root")

    payload = _read_bundled_package(source_root)
    fingerprint = _bundle_fingerprint(
        payload["sources"], payload["manifest"], payload["readme"]
    )
    bundle_root = data_root / "bundled_extensions"
    _ensure_directory(bundle_root, data_root)
    extension_root = bundle_root / fingerprint
    package_dir = extension_root / PACKAGE_DIRECTORY

    if os.path.lexists(extension_root):
        matches, reason = _matches_existing_slot(bundle_root, extension_root, payload)
        return BundledExtensionInstallation(
            extension_root=extension_root,
            package_dir=package_dir,
            installed=False,
            trusted=matches,
            reason=None if matches else reason,
        )

    for _ in range(8):
        staging = bundle_root / f".ff14-{fingerprint}-{secrets.token_hex(8)}.stage"
        try:
            staging.mkdir()
            break
        except FileExistsError:
            continue
        except OSError:
            return BundledExtensionInstallation(
                extension_root=extension_root,
                package_dir=package_dir,
                installed=False,
                trusted=False,
                reason="package_root_unavailable",
            )
    else:
        return BundledExtensionInstallation(
            extension_root=extension_root,
            package_dir=package_dir,
            installed=False,
            trusted=False,
            reason="package_root_unavailable",
        )

    try:
        _validate_path(staging, bundle_root)
        staged_package_dir = staging / PACKAGE_DIRECTORY
        staged_package_dir.mkdir()
        _write_package(
            staged_package_dir,
            payload["sources"],
            payload["manifest"],
            payload["readme"],
        )
        matches, reason = _matches_current_bundle(
            staging,
            staged_package_dir,
            payload["sources"],
            payload["manifest"],
            payload["readme"],
        )
        if not matches:
            return BundledExtensionInstallation(
                extension_root=extension_root,
                package_dir=package_dir,
                installed=False,
                trusted=False,
                reason=reason or "package_write_failed",
            )
        _validate_path(staging, bundle_root)
        try:
            os.rename(staging, extension_root)
        except FileExistsError:
            matches, reason = _matches_existing_slot(
                bundle_root, extension_root, payload
            )
            return BundledExtensionInstallation(
                extension_root=extension_root,
                package_dir=package_dir,
                installed=False,
                trusted=matches,
                reason=None if matches else reason,
            )
    except (OSError, ValueError):
        return BundledExtensionInstallation(
            extension_root=extension_root,
            package_dir=package_dir,
            installed=False,
            trusted=False,
            reason="package_write_failed",
        )

    matches, reason = _matches_existing_slot(bundle_root, extension_root, payload)
    return BundledExtensionInstallation(
        extension_root=extension_root,
        package_dir=package_dir,
        installed=True,
        trusted=matches,
        reason=None if matches else reason,
    )


def _bundle_fingerprint(
    sources: Mapping[str, bytes], manifest: bytes, readme: bytes
) -> str:
    records = dict(sources)
    records[MANIFEST_FILENAME] = manifest
    records[README_FILENAME] = readme
    digest = hashlib.sha256(b"yomihime-bundled-extension-v1\0")
    package_id = PACKAGE_ID.encode("utf-8")
    digest.update(len(package_id).to_bytes(8, "big"))
    digest.update(package_id)
    for relative, content in sorted(records.items()):
        name = relative.encode("utf-8")
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _matches_existing_slot(
    bundle_root: Path, extension_root: Path, payload: Mapping[str, object]
) -> tuple[bool, str | None]:
    try:
        _validate_path(extension_root, bundle_root)
        if not extension_root.is_dir():
            return False, "existing_bundle_slot_invalid"
        entries = tuple(extension_root.iterdir())
        if len(entries) != 1 or entries[0].name != PACKAGE_DIRECTORY:
            return False, "existing_bundle_slot_mismatch"
        _validate_path(entries[0], extension_root)
        if not entries[0].is_dir():
            return False, "existing_bundle_slot_invalid"
        matches, reason = _matches_current_bundle(
            extension_root,
            entries[0],
            payload["sources"],
            payload["manifest"],
            payload["readme"],
        )
        if not matches:
            return False, reason
        final_entries = tuple(extension_root.iterdir())
        if len(final_entries) != 1 or final_entries[0].name != PACKAGE_DIRECTORY:
            return False, "existing_bundle_slot_changed"
        _validate_path(final_entries[0], extension_root)
        return True, None
    except (OSError, ValueError):
        return False, "existing_bundle_slot_unavailable"


def _read_bundled_package(source_root: Path) -> dict[str, object]:
    packages = discover_packages(source_root)
    matches = tuple(
        package
        for package in packages
        if package.package_id == PACKAGE_ID
        and package.package_dir.name == PACKAGE_DIRECTORY
        and package.valid
    )
    if len(packages) != 1 or len(matches) != 1:
        raise BundledExtensionError("bundled_manifest_invalid")
    package = matches[0]
    provenance = package._provenance
    if provenance is None:
        raise BundledExtensionError("bundled_provenance_missing")
    try:
        limits = SourceSnapshotLimits(max_runtime_bytes=MAX_PACKAGE_SOURCE_BYTES)
        snapshot = capture_source_bundle(provenance, limits)
    except (SourceSnapshotError, OSError, ValueError):
        raise BundledExtensionError("bundled_source_invalid") from None

    source_paths = set(snapshot.files)
    if "module.py" not in source_paths or "__init__.py" not in source_paths:
        raise BundledExtensionError("bundled_factory_missing")
    inventory = _inventory_package(package.package_dir)
    python_paths = {relative for relative in inventory if relative.endswith(".py")}
    if python_paths != source_paths:
        raise BundledExtensionError("bundled_source_mismatch")
    required_files = {MANIFEST_FILENAME, README_FILENAME}
    if not required_files <= inventory:
        raise BundledExtensionError("bundled_documentation_missing")
    if inventory != source_paths | required_files:
        raise BundledExtensionError("bundled_package_has_unreviewed_files")

    manifest = _read_stable_file(
        package.package_dir / MANIFEST_FILENAME,
        source_root,
        max_bytes=262_144,
    )
    if hashlib.sha256(manifest).digest() != provenance.manifest_sha256:
        raise BundledExtensionError("bundled_manifest_changed")
    readme = _read_stable_file(
        package.package_dir / README_FILENAME,
        source_root,
        max_bytes=MAX_README_BYTES,
    )
    return {
        "sources": dict(snapshot.files),
        "manifest": manifest,
        "readme": readme,
    }


def _matches_current_bundle(
    extension_root: Path,
    target: Path,
    sources: Mapping[str, bytes],
    manifest: bytes,
    readme: bytes,
) -> tuple[bool, str | None]:
    try:
        _validate_path(target, extension_root)
        if not target.is_dir():
            return False, "existing_package_invalid"
        inventory = _inventory_package(target)
        expected_paths = set(sources) | {MANIFEST_FILENAME, README_FILENAME}
        if inventory != expected_paths:
            return False, "existing_package_mismatch"

        package = _find_package(extension_root, target)
        if package is None or not package.valid or package.package_id != PACKAGE_ID:
            return False, "existing_manifest_invalid"
        provenance = package._provenance
        if provenance is None:
            return False, "existing_provenance_missing"
        snapshot = capture_source_bundle(
            provenance,
            SourceSnapshotLimits(max_runtime_bytes=MAX_PACKAGE_SOURCE_BYTES),
        )
        if dict(snapshot.files) != dict(sources):
            return False, "existing_source_mismatch"
        actual_manifest = _read_stable_file(
            target / MANIFEST_FILENAME, extension_root, max_bytes=262_144
        )
        if actual_manifest != manifest:
            return False, "existing_manifest_mismatch"
        actual_readme = _read_stable_file(
            target / README_FILENAME,
            extension_root,
            max_bytes=MAX_README_BYTES,
        )
        if actual_readme != readme:
            return False, "existing_readme_mismatch"

        # Re-scan once after all reads so a replaced manifest or package tree
        # cannot inherit trust from the first static discovery result.
        checked = _find_package(extension_root, target)
        if checked is None or not checked.valid or checked.package_id != PACKAGE_ID:
            return False, "existing_package_changed"
        checked_provenance = checked._provenance
        if checked_provenance is None:
            return False, "existing_package_changed"
        checked_snapshot = capture_source_bundle(
            checked_provenance,
            SourceSnapshotLimits(max_runtime_bytes=MAX_PACKAGE_SOURCE_BYTES),
        )
        if dict(checked_snapshot.files) != dict(sources):
            return False, "existing_package_changed"
        if checked_provenance.manifest_sha256 != hashlib.sha256(manifest).digest():
            return False, "existing_package_changed"
        return True, None
    except (OSError, ValueError, SourceSnapshotError):
        return False, "existing_package_unavailable"


def _find_package(root: Path, target: Path) -> DiscoveredPackage | None:
    packages = discover_packages(root)
    found = tuple(
        package
        for package in packages
        if package.package_dir.name == target.name
        and package.package_dir.parent == root
    )
    return found[0] if len(found) == 1 else None


def _write_package(
    target: Path,
    sources: Mapping[str, bytes],
    manifest: bytes,
    readme: bytes,
) -> None:
    files = dict(sources)
    files[README_FILENAME] = readme
    files[MANIFEST_FILENAME] = manifest
    for relative, content in sorted(
        files.items(), key=lambda item: item[0] == MANIFEST_FILENAME
    ):
        destination = target / _safe_relative(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _validate_path(destination.parent, target)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        descriptor = os.open(destination, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(descriptor)


def _inventory_package(package_root: Path) -> set[str]:
    package_root = _require_directory(package_root, "package_root")
    found: set[str] = set()
    pending = [package_root]
    while pending:
        current = pending.pop()
        for entry in sorted(current.iterdir(), key=lambda item: item.name):
            resolved = _validate_path(entry, package_root)
            metadata = resolved.stat()
            if stat.S_ISDIR(metadata.st_mode):
                if entry.name not in IGNORED_DIRECTORIES:
                    pending.append(resolved)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise BundledExtensionError("package_entry_invalid")
            relative = resolved.relative_to(package_root).as_posix()
            if relative.endswith(".py") or relative in {
                MANIFEST_FILENAME,
                README_FILENAME,
            }:
                found.add(relative)
            else:
                raise BundledExtensionError("package_entry_unreviewed")
    return found


def _read_stable_file(path: Path, allowed_root: Path, *, max_bytes: int) -> bytes:
    safe_path = _validate_path(path, allowed_root)
    before_path = safe_path.stat()
    if not stat.S_ISREG(before_path.st_mode) or before_path.st_nlink > 1:
        raise BundledExtensionError("file_not_regular")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(safe_path, flags)
    try:
        before = os.fstat(descriptor)
        _reject_link(safe_path, before, "bundle_file")
        if _file_identity(before) != _file_identity(before_path):
            raise BundledExtensionError("file_changed")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) > max_bytes:
            raise BundledExtensionError("file_budget_exceeded")
        after = os.fstat(descriptor)
        final_path = _validate_path(path, allowed_root)
        if _file_identity(before) != _file_identity(after) or final_path != safe_path:
            raise BundledExtensionError("file_changed")
        if _file_identity(after) != _file_identity(final_path.stat()):
            raise BundledExtensionError("file_changed")
        return content
    finally:
        os.close(descriptor)


def _validate_path(path: Path, allowed_root: Path) -> Path:
    raw_root = Path(os.path.abspath(allowed_root))
    _reject_path_components(raw_root, "bundle_root")
    root = raw_root.resolve(strict=True)
    candidate = Path(os.path.abspath(path))
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        raise BundledExtensionError("path_escape") from None
    current = root
    for part in relative.parts:
        current = current / part
        metadata = current.lstat()
        _reject_link(current, metadata, "bundle_path")
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError:
        raise BundledExtensionError("path_escape") from None
    return resolved


def _require_directory(path: Path, label: str) -> Path:
    candidate = Path(os.path.abspath(path))
    _reject_path_components(candidate, label)
    resolved = candidate.resolve(strict=True)
    if not resolved.is_dir():
        raise BundledExtensionError(f"{label}_not_directory")
    return resolved


def _ensure_directory(path: Path, allowed_parent: Path) -> None:
    try:
        path.mkdir()
    except FileExistsError:
        pass
    except OSError:
        raise BundledExtensionError("extension_root_unavailable") from None
    try:
        _validate_path(path, allowed_parent)
    except (OSError, ValueError):
        raise BundledExtensionError("extension_root_unsafe") from None
    if not path.is_dir():
        raise BundledExtensionError("extension_root_not_directory")


def _reject_path_components(path: Path, label: str) -> None:
    for component in reversed((path, *path.parents)):
        try:
            metadata = component.lstat()
        except OSError:
            raise BundledExtensionError(f"{label}_unavailable") from None
        _reject_link(component, metadata, label)


def _reject_link(path: Path, metadata: os.stat_result, label: str) -> None:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or getattr(path, "is_junction", lambda: False)()
        or bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)
    ):
        raise BundledExtensionError(f"{label}_linked")
    if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink > 1:
        raise BundledExtensionError(f"{label}_hard_linked")


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_nlink,
    )


def _safe_relative(relative: str) -> Path:
    if (
        type(relative) is not str
        or not relative
        or relative.startswith("/")
        or "\\" in relative
        or any(part in {"", ".", ".."} for part in relative.split("/"))
    ):
        raise BundledExtensionError("source_path_invalid")
    return Path(*relative.split("/"))


__all__ = [
    "BundledExtensionError",
    "BundledExtensionInstallation",
    "install_bundled_ff14",
]
