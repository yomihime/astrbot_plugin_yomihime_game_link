"""Fixed-pair page composition; external Python and runtime URLs never enter it."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import MappingProxyType

from ..core.contracts.manifests import EXTENSION_MANIFEST_MAX_BYTES
from .disk_manifest import parse_manifest
from .page_resources import _read_file, read_page_resources

PROJECTION_FILE = "external-page-projection.json"
MAX_PROJECTION_BYTES = 16 * 1024 * 1024
MAX_PROJECTED_PACKAGES = 32


def projected_path(owner, digest, relative):
    # Keep the existing public page-path grammar and all resource-relative URLs.
    suffix = f"pages/p{digest}/{relative}"
    if len(suffix) > 240:
        raise ValueError("projected page resource path exceeds the public limit")
    return f"module-assets/{owner}/{suffix}"


class StaticPageProjection:
    """Immutable build inputs bound to complete, validated manifest bytes."""

    def __init__(self, manifests=()):
        if len(manifests) > MAX_PROJECTED_PACKAGES:
            raise ValueError("external page package budget exceeded")
        records = {}
        for raw in manifests:
            manifest = parse_manifest(raw)
            if manifest.package_id in records:
                raise ValueError("duplicate external page package")
            digest = hashlib.sha256(raw).hexdigest()
            for module in manifest.modules:
                owner = manifest.global_module_id(module.module_id)
                for resource in module.resources:
                    projected_path(owner, digest, resource.path)
            records[manifest.package_id] = (digest, manifest, raw)
        self.packages = MappingProxyType(records)

    @classmethod
    def load(cls, plugin_root):
        root = Path(plugin_root)
        path = root / PROJECTION_FILE
        if not path.exists() and not path.is_symlink():
            return cls()
        payload = _read_file(path, root, MAX_PROJECTION_BYTES)
        value = json.loads(payload)
        if (
            type(value) is not dict
            or set(value) != {"schema_version", "manifests"}
            or type(value["schema_version"]) is not int
            or value["schema_version"] != 1
            or type(value["manifests"]) is not list
            or any(type(item) is not str for item in value["manifests"])
        ):
            raise ValueError("invalid external page projection")
        return cls(tuple(item.encode("utf-8") for item in value["manifests"]))

    def serialize(self):
        payload = json.dumps(
            {
                "schema_version": 1,
                "manifests": [r[2].decode("utf-8") for r in self.packages.values()],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > MAX_PROJECTION_BYTES:
            raise ValueError("external page projection byte budget exceeded")
        return payload

    def permits(self, owner, bindings, manifest=None):
        package_id = owner.partition("/")[0]
        if package_id not in bindings:
            return True  # Built-in composition retains its existing policy.
        record = self.packages.get(package_id)
        return bool(
            record is not None
            and bindings[package_id] == record[0]
            and any(
                record[1].global_module_id(m.module_id) == owner
                and (manifest is None or manifest == m)
                for m in record[1].modules
            )
        )

    def catalog(self, data, bindings):
        result = copy.deepcopy(data)
        for module in result.get("modules") or ():
            owner = module["module_id"]
            package_id = owner.partition("/")[0]
            if package_id not in bindings:
                continue
            if not self.permits(owner, bindings):
                module["pages"], module["resources"] = [], []
                continue
            digest = self.packages[package_id][0]
            module["asset_version"] = digest
            declared = next(
                m
                for m in self.packages[package_id][1].modules
                if m.module_id == owner.partition("/")[2]
            )
            expected = [
                {"path": f"module-assets/{owner}/{r.path}", "sha256": r.sha256}
                for r in declared.resources
            ]
            if (module["pages"] or module["resources"]) and module[
                "resources"
            ] != expected:
                module["pages"], module["resources"] = [], []
                continue
            prefix = f"module-assets/{owner}/"

            def rewrite(path):
                if not path.startswith(prefix):
                    raise ValueError("external page resource owner mismatch")
                return projected_path(owner, digest, path[len(prefix) :])

            for page in module["pages"]:
                page["entry"] = rewrite(page["entry"])
                page["styles"] = [rewrite(path) for path in page["styles"]]
            for resource in module["resources"]:
                resource["path"] = rewrite(resource["path"])
        return result


def capture_static_pages(package_roots):
    """Explicit build input, bounded no-follow reads; never imports factory code."""
    manifests, resources = [], {}
    for root in package_roots:
        root = Path(root).absolute()
        raw = _read_file(
            root / "yomihime.manifest.json", root, EXTENSION_MANIFEST_MAX_BYTES
        )
        manifest = parse_manifest(raw)
        payload = read_page_resources(root, manifest)
        if (
            _read_file(
                root / "yomihime.manifest.json", root, EXTENSION_MANIFEST_MAX_BYTES
            )
            != raw
        ):
            raise ValueError("external page manifest changed during capture")
        manifests.append(raw)
        if len(manifests) > MAX_PROJECTED_PACKAGES:
            raise ValueError("external page package budget exceeded")
        digest = hashlib.sha256(raw).hexdigest()
        for module in manifest.modules:
            owner = manifest.global_module_id(module.module_id)
            for resource in module.resources:
                name = projected_path(owner, digest, resource.path)
                if name.casefold() in {key.casefold() for key in resources}:
                    raise ValueError("external page resource collision")
                resources[name] = payload[resource.path]
                if (
                    sum(len(value) for value in resources.values())
                    > MAX_PROJECTION_BYTES
                ):
                    raise ValueError("external static resource byte budget exceeded")
    return StaticPageProjection(tuple(manifests)), resources
