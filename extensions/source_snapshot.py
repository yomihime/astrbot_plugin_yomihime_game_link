"""Pinned filesystem provenance and immutable Python source snapshots.

Scanning records only the identity of a validated package and its manifest.
Source bytes are captured later, after the host has authorized the exact
candidate. This module never compiles or imports extension source.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from yomihime_sdk.api.manifests import EXTENSION_MANIFEST_ABI

from .disk_manifest import ManifestError, parse_manifest

MAX_SOURCE_FILES = 256
MAX_SOURCE_FILE_BYTES = 1 * 1024 * 1024
MAX_PACKAGE_SOURCE_BYTES = 8 * 1024 * 1024
MAX_SOURCE_TREE_ENTRIES = 1024
MAX_SOURCE_TREE_DEPTH = 16
MAX_SOURCE_PATH_BYTES = 1024
MAX_RUNTIME_SOURCE_BYTES = 32 * 1024 * 1024

_PROVENANCE_SEAL = object()


class SourceSnapshotError(ValueError):
    """A stable failure while validating or reading a captured package."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class WindowsPackageIdentity:
    """WinAPI identities captured by the Windows manifest scanner."""

    volume_guid: str
    volume_serial: int
    root_file_id: int
    package_file_id: int
    manifest_file_id: int
    manifest_size: int
    manifest_last_write: int


@dataclass(frozen=True, slots=True)
class PackageProvenance:
    """Private locator and identity proof attached to one scanned package."""

    platform: str
    root_locator: str
    package_name: str
    package_id: str
    manifest_sha256: bytes
    manifest_abi: tuple[int, str, str, int]
    root_identity: tuple[int, int] | None = None
    package_identity: tuple[int, int] | None = None
    manifest_identity: tuple[int, int, int, int, int, int] | None = None
    windows_identity: WindowsPackageIdentity | None = None
    _seal: object = field(default=None, repr=False, compare=False)

    @property
    def trusted(self) -> bool:
        return self._seal is _PROVENANCE_SEAL


def make_posix_provenance(
    *,
    root_locator: Path,
    package_name: str,
    package_id: str,
    root_info: os.stat_result,
    package_info: os.stat_result,
    manifest_info: os.stat_result,
    manifest_bytes: bytes,
) -> PackageProvenance:
    """Mint scan provenance while all three directory/file handles are open."""
    return PackageProvenance(
        platform="posix",
        root_locator=os.fspath(root_locator),
        package_name=package_name,
        package_id=package_id,
        manifest_sha256=hashlib.sha256(manifest_bytes).digest(),
        manifest_abi=_manifest_abi(),
        root_identity=(int(root_info.st_dev), int(root_info.st_ino)),
        package_identity=(int(package_info.st_dev), int(package_info.st_ino)),
        manifest_identity=(
            int(manifest_info.st_dev),
            int(manifest_info.st_ino),
            int(manifest_info.st_size),
            int(manifest_info.st_mtime_ns),
            int(manifest_info.st_ctime_ns),
            int(manifest_info.st_nlink),
        ),
        _seal=_PROVENANCE_SEAL,
    )


def make_windows_provenance(
    *,
    root_locator: Path,
    package_name: str,
    package_id: str,
    manifest_bytes: bytes,
    identity: WindowsPackageIdentity,
) -> PackageProvenance:
    """Mint scan provenance from the pinned WinAPI scan record."""
    return PackageProvenance(
        platform="nt",
        root_locator=os.fspath(root_locator),
        package_name=package_name,
        package_id=package_id,
        manifest_sha256=hashlib.sha256(manifest_bytes).digest(),
        manifest_abi=_manifest_abi(),
        windows_identity=identity,
        _seal=_PROVENANCE_SEAL,
    )


@dataclass(frozen=True, slots=True)
class SourceSnapshotLimits:
    """Per-package and per-runtime source limits; callers may only lower them."""

    max_package_bytes: int = MAX_PACKAGE_SOURCE_BYTES
    max_file_bytes: int = MAX_SOURCE_FILE_BYTES
    max_files: int = MAX_SOURCE_FILES
    max_tree_entries: int = MAX_SOURCE_TREE_ENTRIES
    max_depth: int = MAX_SOURCE_TREE_DEPTH
    max_runtime_bytes: int = MAX_RUNTIME_SOURCE_BYTES

    def __post_init__(self) -> None:
        maximums = {
            "max_package_bytes": MAX_PACKAGE_SOURCE_BYTES,
            "max_file_bytes": MAX_SOURCE_FILE_BYTES,
            "max_files": MAX_SOURCE_FILES,
            "max_tree_entries": MAX_SOURCE_TREE_ENTRIES,
            "max_depth": MAX_SOURCE_TREE_DEPTH,
            "max_runtime_bytes": MAX_RUNTIME_SOURCE_BYTES,
        }
        for name, maximum in maximums.items():
            value = getattr(self, name)
            if type(value) is not int or not 0 < value <= maximum:
                raise ValueError(f"{name} is outside the source snapshot budget")
        if self.max_package_bytes > self.max_runtime_bytes:
            raise ValueError("max_package_bytes exceeds max_runtime_bytes")


@dataclass(frozen=True, slots=True)
class SourceBundle:
    """Immutable path-to-bytes snapshot; it has no filesystem import path."""

    files: Mapping[str, bytes]
    digest: bytes
    total_bytes: int

    def __post_init__(self) -> None:
        copied = dict(self.files)
        if any(
            type(name) is not str or type(content) is not bytes
            for name, content in copied.items()
        ):
            raise TypeError("source bundle requires text paths and byte sources")
        if self.total_bytes != sum(map(len, copied.values())):
            raise ValueError("source bundle byte count is inconsistent")
        if (
            type(self.digest) is not bytes
            or len(self.digest) != hashlib.sha256().digest_size
            or self.digest != _bundle_digest(copied)
        ):
            raise ValueError("source bundle digest is invalid")
        object.__setattr__(self, "files", MappingProxyType(copied))


def capture_source_bundle(
    provenance: PackageProvenance, limits: SourceSnapshotLimits
) -> SourceBundle:
    """Reopen the exact scanned package and capture its bounded Python tree."""
    _require_provenance(provenance)
    if not isinstance(limits, SourceSnapshotLimits):
        raise TypeError("limits must be SourceSnapshotLimits")
    if provenance.manifest_abi != _manifest_abi():
        raise SourceSnapshotError("candidate_stale")
    if provenance.platform == "nt":
        if provenance.windows_identity is None:
            raise SourceSnapshotError("candidate_stale")
        from .windows_fs import capture_python_sources

        files = capture_python_sources(
            provenance.root_locator,
            provenance.package_name,
            provenance.package_id,
            provenance.windows_identity,
            provenance.manifest_sha256,
            limits=limits,
        )
    elif provenance.platform == "posix" and os.name != "nt":
        files = _capture_posix_sources(provenance, limits)
    else:
        raise SourceSnapshotError("unsupported_environment")
    digest = _bundle_digest(files)
    total = sum(map(len, files.values()))
    return SourceBundle(files, digest, total)


def _capture_posix_sources(
    provenance: PackageProvenance, limits: SourceSnapshotLimits
) -> dict[str, bytes]:
    if (
        provenance.root_identity is None
        or provenance.package_identity is None
        or provenance.manifest_identity is None
    ):
        raise SourceSnapshotError("candidate_stale")
    from .discovery import _open_flags

    root_fd: int | None = None
    package_fd: int | None = None
    directory_fds: list[tuple[str, int, tuple[tuple[object, ...], ...]]] = []
    try:
        root_fd = os.open(
            provenance.root_locator,
            _open_flags(directory=True),
        )
        root_info = os.fstat(root_fd)
        if _posix_identity(root_info) != provenance.root_identity:
            raise SourceSnapshotError("candidate_stale")
        package_fd = os.open(
            provenance.package_name,
            _open_flags(directory=True),
            dir_fd=root_fd,
        )
        package_info = os.fstat(package_fd)
        if (
            not stat.S_ISDIR(package_info.st_mode)
            or _posix_identity(package_info) != provenance.package_identity
            or package_info.st_dev != root_info.st_dev
        ):
            raise SourceSnapshotError("candidate_stale")

        manifest_bytes, manifest_info = _read_posix_manifest(package_fd)
        _verify_posix_manifest(provenance, manifest_bytes, manifest_info)
        try:
            manifest = parse_manifest(manifest_bytes)
        except ManifestError:
            raise SourceSnapshotError("candidate_stale") from None
        if manifest.package_id != provenance.package_id:
            raise SourceSnapshotError("candidate_stale")

        files: dict[str, bytes] = {}
        entry_count = [0]
        total_bytes = [0]
        package_walk_fd = os.open(
            ".", _posix_open_flags(directory=True), dir_fd=package_fd
        )
        _walk_posix_directory(
            package_walk_fd,
            "",
            0,
            root_device=int(root_info.st_dev),
            limits=limits,
            files=files,
            entry_count=entry_count,
            total_bytes=total_bytes,
            directory_fds=directory_fds,
        )

        for relative, descriptor, before in directory_fds:
            after = _posix_directory_inventory(descriptor, limits)
            if before != after:
                raise SourceSnapshotError("candidate_stale")
            current = os.fstat(descriptor)
            if not stat.S_ISDIR(current.st_mode):
                raise SourceSnapshotError("candidate_stale")
            # The root package fd is checked again by name below; nested
            # directory identities are represented by their held descriptors.
            del relative

        final_manifest, final_info = _read_posix_manifest(package_fd)
        _verify_posix_manifest(provenance, final_manifest, final_info)
        package_entry = os.stat(
            provenance.package_name, dir_fd=root_fd, follow_symlinks=False
        )
        if (
            stat.S_ISLNK(package_entry.st_mode)
            or not stat.S_ISDIR(package_entry.st_mode)
            or _posix_identity(package_entry) != provenance.package_identity
        ):
            raise SourceSnapshotError("candidate_stale")
        return files
    except SourceSnapshotError:
        raise
    except (OSError, TypeError, ValueError, RuntimeError):
        raise SourceSnapshotError("source_unavailable") from None
    finally:
        for _, descriptor, _ in reversed(directory_fds):
            try:
                os.close(descriptor)
            except OSError:
                pass
        if package_fd is not None:
            os.close(package_fd)
        if root_fd is not None:
            os.close(root_fd)


def _walk_posix_directory(
    directory_fd: int,
    prefix: str,
    depth: int,
    *,
    root_device: int,
    limits: SourceSnapshotLimits,
    files: dict[str, bytes],
    entry_count: list[int],
    total_bytes: list[int],
    directory_fds: list[tuple[str, int, tuple[tuple[object, ...], ...]]],
) -> None:
    if depth > limits.max_depth:
        raise SourceSnapshotError("source_budget_exceeded")
    directory_fds.append((prefix, directory_fd, ()))
    inventory = _posix_directory_inventory(directory_fd, limits, entry_count)
    directory_fds[-1] = (prefix, directory_fd, inventory)
    for entry in inventory:
        name = str(entry[0])
        mode = int(entry[1])
        entry_device, entry_inode = int(entry[2]), int(entry[3])
        relative = f"{prefix}/{name}" if prefix else name
        _check_source_path(relative)
        if stat.S_ISLNK(mode):
            raise SourceSnapshotError("source_link_rejected")
        if not stat.S_ISDIR(mode) and not stat.S_ISREG(mode):
            if name.endswith(".py"):
                raise SourceSnapshotError("source_not_regular")
            continue
        if entry_device != root_device:
            raise SourceSnapshotError("source_cross_volume")
        if stat.S_ISDIR(mode):
            if depth >= limits.max_depth:
                raise SourceSnapshotError("source_budget_exceeded")
            try:
                child_fd = os.open(
                    name,
                    _posix_open_flags(directory=True),
                    dir_fd=directory_fd,
                )
            except OSError:
                raise SourceSnapshotError("source_link_rejected") from None
            try:
                child_info = os.fstat(child_fd)
            except OSError:
                os.close(child_fd)
                raise SourceSnapshotError("source_unavailable") from None
            if (
                not stat.S_ISDIR(child_info.st_mode)
                or (int(child_info.st_dev), int(child_info.st_ino))
                != (entry_device, entry_inode)
                or child_info.st_dev != root_device
            ):
                os.close(child_fd)
                raise SourceSnapshotError("candidate_stale")
            try:
                _walk_posix_directory(
                    child_fd,
                    relative,
                    depth + 1,
                    root_device=root_device,
                    limits=limits,
                    files=files,
                    entry_count=entry_count,
                    total_bytes=total_bytes,
                    directory_fds=directory_fds,
                )
            except BaseException:
                if not any(
                    descriptor == child_fd for _, descriptor, _ in directory_fds
                ):
                    os.close(child_fd)
                raise
            continue
        if not name.endswith(".py"):
            continue
        if len(files) >= limits.max_files:
            raise SourceSnapshotError("source_budget_exceeded")
        if int(entry[4]) > limits.max_file_bytes:
            raise SourceSnapshotError("source_budget_exceeded")
        if int(entry[7]) != 1:
            raise SourceSnapshotError("source_hardlink_rejected")
        if not _importable_source_path(relative):
            raise SourceSnapshotError("source_path_rejected")
        try:
            source_fd = os.open(
                name,
                _posix_open_flags(directory=False),
                dir_fd=directory_fd,
            )
        except OSError:
            raise SourceSnapshotError("source_link_rejected") from None
        try:
            before = os.fstat(source_fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or (int(before.st_dev), int(before.st_ino))
                != (entry_device, entry_inode)
                or before.st_dev != root_device
                or before.st_nlink != 1
            ):
                raise SourceSnapshotError("source_hardlink_rejected")
            content = _read_bounded(source_fd, limits.max_file_bytes)
            after = os.fstat(source_fd)
            if _posix_file_identity(before) != _posix_file_identity(after):
                raise SourceSnapshotError("candidate_stale")
        finally:
            os.close(source_fd)
        total_bytes[0] += len(content)
        if total_bytes[0] > limits.max_package_bytes:
            raise SourceSnapshotError("source_budget_exceeded")
        files[relative] = content


def _posix_directory_inventory(
    directory_fd: int,
    limits: SourceSnapshotLimits,
    total_count: list[int] | None = None,
) -> tuple[tuple[object, ...], ...]:
    fresh_fd = os.open(".", _posix_open_flags(directory=True), dir_fd=directory_fd)
    try:
        with os.scandir(fresh_fd) as iterator:
            names: list[str] = []
            for entry in iterator:
                name = entry.name
                if (
                    type(name) is not str
                    or name in {".", ".."}
                    or "/" in name
                    or "\\" in name
                ):
                    raise SourceSnapshotError("source_path_rejected")
                names.append(name)
                if len(names) > limits.max_tree_entries:
                    raise SourceSnapshotError("source_budget_exceeded")
            names.sort()
        entries: list[tuple[object, ...]] = []
        for name in names:
            info = os.stat(name, dir_fd=fresh_fd, follow_symlinks=False)
            encoded = name.encode("utf-8", "strict")
            entry = (
                name,
                int(info.st_mode),
                int(info.st_dev),
                int(info.st_ino),
                int(info.st_size),
                int(info.st_mtime_ns),
                int(info.st_ctime_ns),
                int(info.st_nlink),
                len(encoded),
            )
            entries.append(entry)
            if total_count is not None:
                total_count[0] += 1
                if total_count[0] > limits.max_tree_entries:
                    raise SourceSnapshotError("source_budget_exceeded")
        return tuple(entries)
    except SourceSnapshotError:
        raise
    except (OSError, UnicodeError, ValueError):
        raise SourceSnapshotError("source_unavailable") from None
    finally:
        os.close(fresh_fd)


def _read_posix_manifest(
    package_fd: int,
) -> tuple[bytes, os.stat_result]:
    from .discovery import _open_flags

    try:
        descriptor = os.open(
            EXTENSION_MANIFEST_ABI.filename,
            _open_flags(),
            dir_fd=package_fd,
        )
    except OSError:
        raise SourceSnapshotError("candidate_stale") from None
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise SourceSnapshotError("candidate_stale")
        data = _read_bounded(descriptor, EXTENSION_MANIFEST_ABI.max_bytes)
        after = os.fstat(descriptor)
        if _posix_file_identity(before) != _posix_file_identity(after):
            raise SourceSnapshotError("candidate_stale")
        return data, after
    finally:
        os.close(descriptor)


def _verify_posix_manifest(
    provenance: PackageProvenance,
    data: bytes,
    info: os.stat_result,
) -> None:
    identity = (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_size),
        int(info.st_mtime_ns),
        int(info.st_ctime_ns),
        int(info.st_nlink),
    )
    if (
        identity != provenance.manifest_identity
        or hashlib.sha256(data).digest() != provenance.manifest_sha256
    ):
        raise SourceSnapshotError("candidate_stale")


def _read_bounded(descriptor: int, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    remaining = max_bytes + 1
    while remaining:
        chunk = os.read(descriptor, min(65_536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b"".join(chunks)
    if len(data) > max_bytes:
        raise SourceSnapshotError("source_budget_exceeded")
    return data


def _posix_open_flags(*, directory: bool) -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
        | os.O_NOFOLLOW
        | (os.O_DIRECTORY if directory else 0)
    )


def _posix_identity(info: os.stat_result) -> tuple[int, int]:
    return int(info.st_dev), int(info.st_ino)


def _posix_file_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_size),
        int(info.st_mtime_ns),
        int(info.st_ctime_ns),
        int(info.st_nlink),
    )


def _check_source_path(relative: str) -> None:
    try:
        encoded = relative.encode("utf-8", "strict")
    except UnicodeError:
        raise SourceSnapshotError("source_path_rejected") from None
    if not relative or len(encoded) > MAX_SOURCE_PATH_BYTES:
        raise SourceSnapshotError("source_budget_exceeded")
    if (
        relative.startswith("/")
        or "\\" in relative
        or any(component in {"", ".", ".."} for component in relative.split("/"))
    ):
        raise SourceSnapshotError("source_path_rejected")


def _importable_source_path(relative: str) -> bool:
    components = relative.split("/")
    for index, component in enumerate(components):
        if component.endswith(".py"):
            stem = component[:-3]
            if stem == "__init__" and index == len(components) - 1:
                continue
            if not stem.isidentifier():
                return False
        elif not component.isidentifier():
            return False
    return True


def _bundle_digest(files: Mapping[str, bytes]) -> bytes:
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        encoded = name.encode("utf-8", "strict")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.digest()


def _manifest_abi() -> tuple[int, str, str, int]:
    return (
        EXTENSION_MANIFEST_ABI.schema_version,
        EXTENSION_MANIFEST_ABI.filename,
        EXTENSION_MANIFEST_ABI.root_policy,
        EXTENSION_MANIFEST_ABI.factory_abi_version,
    )


def _require_provenance(provenance: PackageProvenance) -> None:
    if (
        type(provenance) is not PackageProvenance
        or not provenance.trusted
        or not provenance.root_locator
        or not provenance.package_name
        or not provenance.package_id
        or len(provenance.manifest_sha256) != hashlib.sha256().digest_size
    ):
        raise SourceSnapshotError("candidate_stale")
