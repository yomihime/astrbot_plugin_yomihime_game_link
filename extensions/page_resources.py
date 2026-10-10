"""Bounded, hash-verified static resources from a pinned extension candidate."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from types import MappingProxyType

from yomihime_game_link_sdk.declarations import PackageManifest

from ..core.contracts.manifests import (
    EXTENSION_MANIFEST_FILENAME,
    EXTENSION_MANIFEST_MAX_BYTES,
    validate_page_resource_path,
)
from ..core.contracts.validation_boundary import validate_contract
from .disk_manifest import parse_manifest
from .source_snapshot import (
    MAX_PACKAGE_SOURCE_BYTES,
    PackageProvenance,
    SourceSnapshotLimits,
    capture_source_bundle,
)

MAX_PAGE_RESOURCE_BYTES = 4 * 1024 * 1024
MAX_PAGE_PACKAGE_BYTES = 16 * 1024 * 1024


def _check_path(path: Path, root: Path) -> None:
    relative = path.relative_to(root)
    current = root
    for part in (None, *relative.parts):
        if part is not None:
            current /= part
        metadata = current.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or getattr(current, "is_junction", lambda: False)()
            or getattr(metadata, "st_file_attributes", 0) & 0x400
        ):
            raise ValueError("page resource links are forbidden")
        if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
            raise ValueError("page resource hardlinks are forbidden")
        if current != path and not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("page resource parent must be a directory")
    if not path.resolve(strict=True).is_relative_to(root.resolve(strict=True)):
        raise ValueError("page resource escapes its root")


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink)


def read_page_resources(root: Path, manifest: PackageManifest) -> dict[str, bytes]:
    """Read only closed declarations; declarations alone do not grant trust."""
    validate_contract(manifest)
    declarations = {}
    names = set()
    for module in manifest.modules:
        for resource in module.resources:
            validate_page_resource_path(resource.path)
            folded = resource.path.casefold()
            if folded in names:
                raise ValueError("page resource namespace collision")
            names.add(folded)
            declarations[resource.path] = resource.sha256
    if len(declarations) > 128:
        raise ValueError("page resource declaration budget exceeded")
    payload = {}
    total = 0
    for relative, digest in sorted(declarations.items()):
        content = _read_file(root / relative, root, MAX_PAGE_RESOURCE_BYTES)
        total += len(content)
        if total > MAX_PAGE_PACKAGE_BYTES:
            raise ValueError("page resource package budget exceeded")
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("page resource hash mismatch")
        payload[relative] = content

    return payload


def _read_file(path: Path, root: Path, max_bytes: int) -> bytes:
    _check_path(path, root)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
        raise ValueError("page resource file budget exceeded")
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        opened = os.fstat(descriptor)
        if _identity(opened) != _identity(before):
            raise ValueError("page resource changed")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            content = stream.read(max_bytes + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    _check_path(path, root)
    if _identity(after) != _identity(before) or _identity(path.stat()) != _identity(
        before
    ):
        raise ValueError("page resource changed")
    if len(content) > max_bytes:
        raise ValueError("page resource file budget exceeded")
    return content


def capture_page_resources(provenance: PackageProvenance, manifest: PackageManifest):
    """Bind declarations to the same sealed manifest before reading assets."""
    validate_contract(manifest)
    limits = SourceSnapshotLimits(max_runtime_bytes=MAX_PACKAGE_SOURCE_BYTES)
    capture_source_bundle(provenance, limits)
    root = Path(provenance.root_locator) / provenance.package_name
    manifest_bytes = _read_file(
        root / EXTENSION_MANIFEST_FILENAME, root, EXTENSION_MANIFEST_MAX_BYTES
    )
    if (
        hashlib.sha256(manifest_bytes).digest() != provenance.manifest_sha256
        or parse_manifest(manifest_bytes) != manifest
    ):
        raise ValueError("page resource manifest does not match the pinned candidate")
    payload = read_page_resources(root, manifest)
    capture_source_bundle(provenance, limits)
    return MappingProxyType(payload)
