"""Generic static Host discovery; caller objects are hints, never credentials."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..extensions.hot_update_protocol import ProtocolError, parse_plugin_descriptor


class DiscoveryError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class HostInstallation:
    metadata: Any = field(repr=False)
    instance: Any = field(repr=False)
    root: Path
    descriptor: dict
    descriptor_digest: str
    author: str | None
    name: str | None
    version: str | None

    @property
    def package_id(self):
        return self.descriptor["package_id"]

    @property
    def plugin_id(self):
        return {"author": self.author, "name": self.name}

    def metadata_current(self):
        return (
            self.metadata.activated is True
            and self.metadata.star_cls is self.instance
            and getattr(self.metadata, "author", None) == self.author
            and getattr(self.metadata, "name", None) == self.name
            and getattr(self.metadata, "version", None) == self.version
        )


class ExtensionDiscovery:
    """Read a shallow public registry snapshot without taking Host private locks."""

    def __init__(self, context):
        self.context = context
        self._tombstones = {}
        self.rejections = {}

    def tombstone(self, instance):
        # Retain the object: id reuse must never detach a successor.
        self._tombstones[id(instance)] = instance

    def is_tombstoned(self, instance):
        return self._tombstones.get(id(instance)) is instance

    def prune_tombstones(self):
        """Called by event-loop reconciliation, the other tombstone writer."""
        metadata_snapshot = tuple(self.context.get_all_stars())
        present = {
            id(getattr(meta, "star_cls", None)): getattr(meta, "star_cls", None)
            for meta in metadata_snapshot
        }
        self._tombstones = {
            key: obj for key, obj in self._tombstones.items() if present.get(key) is obj
        }

    def scan(self):
        # SQL workers also scan. Their snapshots must not prune retirements.
        metadata_snapshot = tuple(self.context.get_all_stars())
        found = []
        rejections = {}
        for metadata in metadata_snapshot:
            instance = getattr(metadata, "star_cls", None)
            if getattr(metadata, "activated", None) is not True or instance is None:
                continue
            if self.is_tombstoned(instance):
                continue
            try:
                candidate = self._read(metadata)
            except DiscoveryError as exc:
                rejections[getattr(metadata, "name", "")] = exc.code
                continue
            if candidate is not None:
                found.append(candidate)
        counts = {}
        for item in found:
            counts[item.package_id] = counts.get(item.package_id, 0) + 1
        for item in found:
            if counts[item.package_id] != 1:
                rejections[item.package_id] = "owner_conflict"
        self.rejections = rejections
        return tuple(item for item in found if counts[item.package_id] == 1)

    @staticmethod
    def _read(metadata):
        module_file = getattr(getattr(metadata, "module", None), "__file__", None)
        if type(module_file) is not str:
            raise DiscoveryError("installation_unknown")
        try:
            module_path = Path(module_file)
            root = module_path.resolve(strict=True).parent
            # A renamed/symlinked installation cannot borrow another tree.
            if module_path.is_symlink() or root.name != getattr(
                metadata, "root_dir_name", None
            ):
                raise DiscoveryError("installation_unknown")
            path = root / "ygl-plugin.json"
            if not path.exists():
                return None
            if path.is_symlink() or not path.is_file():
                raise DiscoveryError("descriptor_invalid")
            with path.open("rb") as stream:
                raw = stream.read(16385)
            descriptor = parse_plugin_descriptor(raw)
        except (OSError, RuntimeError, ProtocolError) as exc:
            raise DiscoveryError("descriptor_invalid") from exc
        return HostInstallation(
            metadata,
            metadata.star_cls,
            root,
            descriptor,
            hashlib.sha256(raw).hexdigest(),
            getattr(metadata, "author", None),
            getattr(metadata, "name", None),
            getattr(metadata, "version", None),
        )

    def current(self, installation):
        # Rescan revalidates exact metadata, instance, root, descriptor and conflicts.
        return installation.metadata_current() and any(
            item.metadata is installation.metadata
            and item.instance is installation.instance
            and item.root == installation.root
            and item.descriptor_digest == installation.descriptor_digest
            for item in self.scan()
        )

    def for_instance(self, instance):
        matches = tuple(
            item
            for item in self.scan()
            if item.instance is instance and item.descriptor["role"] == "dlc"
        )
        if len(matches) != 1:
            raise DiscoveryError("stale_instance")
        return matches[0]
