"""Static, inert discovery of extension packages under an injected root."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from yomihime_game_link_sdk.declarations import PackageManifest

from ..core.contracts.manifests import EXTENSION_MANIFEST_ABI
from .disk_manifest import ManifestError, parse_manifest
from .source_snapshot import (
    PackageProvenance,
    make_posix_provenance,
    make_windows_provenance,
)

# E-local safety budgets. H owns the manifest ABI; these only bound one scan.
EXTENSION_ROOT_MAX_ENTRIES = 256
EXTENSION_ROOT_MAX_PACKAGES = 128


@dataclass(frozen=True, slots=True)
class DiscoveredPackage:
    """A validated declaration or safe diagnostic; never a registered module."""

    package_id: str | None
    package_dir: Path
    manifest: PackageManifest | None
    diagnostic: str | None = None
    enabled: bool = False
    _provenance: PackageProvenance | None = field(
        default=None, repr=False, compare=False
    )

    @property
    def valid(self) -> bool:
        return self.manifest is not None and self.diagnostic is None


class DiscoveryRootError(ValueError):
    """The injected package root is absent or cannot be scanned safely."""


def _require_handle_relative_support() -> None:
    """Fail closed where this runtime cannot pin and traverse directory handles."""
    required = (
        hasattr(os, "O_NOFOLLOW"),
        hasattr(os, "O_DIRECTORY"),
        os.open in os.supports_dir_fd,
        os.stat in os.supports_dir_fd,
        os.scandir in os.supports_fd,
    )
    if not all(required):
        raise DiscoveryRootError(
            "extension root requires handle-relative no-follow filesystem support"
        )


def _bounded_entry_names(entries: Iterable[object]) -> list[str]:
    """Collect at most the fixed scan budget; never sort an unbounded root."""
    names: list[str] = []
    for entry in entries:
        name = entry if type(entry) is str else getattr(entry, "name", None)
        if type(name) is not str or name in {".", ".."} or "/" in name or "\\" in name:
            raise DiscoveryRootError("extension root contains an invalid entry name")
        names.append(name)
        if len(names) > EXTENSION_ROOT_MAX_ENTRIES:
            raise DiscoveryRootError("extension root entry budget exceeded")
    names.sort()
    return names


def _check_package_budget(count: int) -> None:
    if count > EXTENSION_ROOT_MAX_PACKAGES:
        raise DiscoveryRootError("extension package budget exceeded")


def _open_flags(*, directory: bool = False) -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
        | os.O_NOFOLLOW
        | (os.O_DIRECTORY if directory else 0)
    )


def _read_manifest_at(package_fd: int) -> bytes:
    data, _ = _read_manifest_record_at(package_fd)
    return data


def _read_manifest_record_at(
    package_fd: int,
) -> tuple[bytes, os.stat_result]:
    try:
        descriptor = os.open(
            EXTENSION_MANIFEST_ABI.filename,
            _open_flags(),
            dir_fd=package_fd,
        )
    except OSError:
        raise ManifestError("manifest is unavailable or linked") from None
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise ManifestError("manifest is not a regular file")
        chunks: list[bytes] = []
        remaining = EXTENSION_MANIFEST_ABI.max_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > EXTENSION_MANIFEST_ABI.max_bytes:
            raise ManifestError("manifest byte budget exceeded")
        after = os.fstat(descriptor)

        def identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
            return (
                info.st_dev,
                info.st_ino,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            )

        if identity(opened) != identity(after):
            raise ManifestError("manifest changed during read")
        return data, after
    finally:
        os.close(descriptor)


def _entry_info(root_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError:
        return None


def _scan_pinned_root(root_fd: int, root_path: Path) -> tuple[DiscoveredPackage, ...]:
    root_info = os.fstat(root_fd)
    try:
        with os.scandir(root_fd) as iterator:
            names = _bounded_entry_names(iterator)
    except DiscoveryRootError:
        raise
    except OSError:
        raise DiscoveryRootError(
            "extension root could not be enumerated safely"
        ) from None

    directory_names: list[str] = []
    result: list[DiscoveredPackage] = []
    for name in names:
        info = _entry_info(root_fd, name)
        if info is None:
            continue
        if stat.S_ISLNK(info.st_mode):
            result.append(
                DiscoveredPackage(None, root_path / name, None, "package_link_rejected")
            )
        elif stat.S_ISDIR(info.st_mode):
            directory_names.append(name)
            _check_package_budget(len(directory_names))

    for name in directory_names:
        package_path = root_path / name  # display only; never used to open package data
        package_fd: int | None = None
        try:
            package_fd = os.open(name, _open_flags(directory=True), dir_fd=root_fd)
            package_info = os.fstat(package_fd)
            if not stat.S_ISDIR(package_info.st_mode):
                raise ManifestError("package is not a directory")
            if package_info.st_dev != root_info.st_dev:
                raise ManifestError("package crosses a filesystem boundary")
            data, manifest_info = _read_manifest_record_at(package_fd)
            manifest = parse_manifest(data)
            provenance = make_posix_provenance(
                root_locator=root_path,
                package_name=name,
                package_id=manifest.package_id,
                root_info=root_info,
                package_info=package_info,
                manifest_info=manifest_info,
                manifest_bytes=data,
            )
            result.append(
                DiscoveredPackage(
                    manifest.package_id, package_path, manifest, _provenance=provenance
                )
            )
        except ManifestError as exc:
            result.append(DiscoveredPackage(None, package_path, None, str(exc)))
        except OSError:
            result.append(
                DiscoveredPackage(None, package_path, None, "package_unavailable")
            )
        finally:
            if package_fd is not None:
                os.close(package_fd)

    result.sort(key=lambda item: item.package_dir.name)
    return _reject_duplicate_package_ids(result)


def _reject_duplicate_package_ids(
    result: list[DiscoveredPackage],
) -> tuple[DiscoveredPackage, ...]:
    package_ids: dict[str, list[int]] = {}
    for index, item in enumerate(result):
        if item.valid and item.package_id is not None:
            package_ids.setdefault(item.package_id, []).append(index)
    for indexes in package_ids.values():
        if len(indexes) > 1:
            for index in indexes:
                item = result[index]
                result[index] = DiscoveredPackage(
                    None, item.package_dir, None, "duplicate_package_id"
                )
    return tuple(result)


def _discover_windows(
    root: str | os.PathLike[str],
) -> tuple[DiscoveredPackage, ...]:
    from .windows_fs import WindowsScanError, scan_windows_root

    root_path = Path(root)
    try:
        records = scan_windows_root(
            root,
            max_bytes=EXTENSION_MANIFEST_ABI.max_bytes,
            max_entries=EXTENSION_ROOT_MAX_ENTRIES,
            max_packages=EXTENSION_ROOT_MAX_PACKAGES,
        )
    except WindowsScanError as exc:
        if exc.code == "budget_exceeded":
            raise DiscoveryRootError("extension root budget exceeded") from None
        raise DiscoveryRootError("extension root could not be scanned safely") from None

    messages = {
        "package_link_rejected": "package_link_rejected",
        "package_unavailable": "package_unavailable",
        "manifest_unavailable": "manifest is unavailable or linked",
        "manifest_invalid": "manifest is not a regular file",
        "manifest_linked": "manifest is unavailable or linked",
        "budget_exceeded": "manifest byte budget exceeded",
        "identity_changed": "manifest changed during read",
        "io_error": "manifest is unavailable or linked",
    }
    result: list[DiscoveredPackage] = []
    for record in records:
        package_path = root_path / record.name
        if record.diagnostic is not None:
            result.append(
                DiscoveredPackage(
                    None,
                    package_path,
                    None,
                    messages.get(record.diagnostic, "package_unavailable"),
                )
            )
            continue
        if record.data is None:
            result.append(
                DiscoveredPackage(None, package_path, None, "package_unavailable")
            )
            continue
        try:
            manifest = parse_manifest(record.data)
        except ManifestError as exc:
            result.append(DiscoveredPackage(None, package_path, None, str(exc)))
        else:
            result.append(
                DiscoveredPackage(
                    manifest.package_id,
                    package_path,
                    manifest,
                    _provenance=make_windows_provenance(
                        root_locator=root_path,
                        package_name=record.name,
                        package_id=manifest.package_id,
                        manifest_bytes=record.data,
                        identity=record.identity,
                    ),
                )
            )

    result.sort(key=lambda item: item.package_dir.name)
    return _reject_duplicate_package_ids(result)


def discover_packages(
    root: str | os.PathLike[str] | None,
) -> tuple[DiscoveredPackage, ...]:
    """Read manifests relative to pinned handles only; unsupported systems fail closed."""
    if root is None:
        raise DiscoveryRootError("extension root is required")
    if os.name == "nt":
        try:
            return _discover_windows(root)
        except DiscoveryRootError:
            raise
        except (OSError, TypeError, ValueError, RuntimeError):
            raise DiscoveryRootError("extension root is unavailable") from None
    _require_handle_relative_support()
    root_fd: int | None = None
    try:
        root_path = Path(root).absolute()
        root_fd = os.open(root_path, _open_flags(directory=True))
        info = os.fstat(root_fd)
        if not stat.S_ISDIR(info.st_mode):
            raise DiscoveryRootError("extension root must be a directory")
        return _scan_pinned_root(root_fd, root_path)
    except DiscoveryRootError:
        raise
    except (OSError, TypeError, ValueError, RuntimeError):
        raise DiscoveryRootError("extension root is unavailable") from None
    finally:
        if root_fd is not None:
            os.close(root_fd)
