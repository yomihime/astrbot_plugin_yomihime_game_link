"""Closed, immutable Core/DLC artifacts and same-generation private imports.

Authentication is an upstream Host responsibility. This module accepts only a
release inventory already admitted by that boundary, then independently checks
the installed bytes and owns their lifetime. It never reads the install root
after capture.
"""

from __future__ import annotations

import hashlib
import importlib
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from .hot_update_protocol import ProtocolError, parse_release
from .snapshot_importer import SnapshotFinder

CORE_MAX_BYTES = 128 * 1024 * 1024
CORE_MAX_FILE_BYTES = 8 * 1024 * 1024
CORE_MAX_FILES = 4096
DLC_MAX_BYTES = 8 * 1024 * 1024
DLC_MAX_FILE_BYTES = 1 * 1024 * 1024
DLC_MAX_FILES = 256
MAX_TREE_ENTRIES = 8192
MAX_DEPTH = 24
_IMPORT_LOCK = threading.RLock()
_EXCLUDED = frozenset({"release.json", "release.sig.json"})
_FORBIDDEN_DLC_PARTS = frozenset({"_api_v2", "yomihime_game_link_sdk", "bootstrap"})
_PACKAGE_ID = re.compile(r"[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*\Z")


class RuntimeBundleError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class RuntimeBundleStore:
    """Reserve room for old and candidate bundles before reading either one."""

    def __init__(
        self,
        *,
        core_capacity: int = 2 * CORE_MAX_BYTES,
        dlc_capacity: int = 40 * 1024 * 1024,
        shared_budget=None,
    ) -> None:
        if type(core_capacity) is not int or core_capacity < CORE_MAX_BYTES:
            raise ValueError("invalid Core capacity")
        if type(dlc_capacity) is not int or dlc_capacity < DLC_MAX_BYTES:
            raise ValueError("invalid DLC capacity")
        if shared_budget is None:
            shared_budget = {
                "capacities": {"core": core_capacity, "dlc": dlc_capacity},
                "used": {"core": 0, "dlc": 0},
                "lock": threading.Lock(),
            }
        if (
            type(shared_budget) is not dict
            or set(shared_budget) != {"capacities", "used", "lock"}
            or shared_budget["capacities"]
            != {"core": core_capacity, "dlc": dlc_capacity}
            or type(shared_budget["used"]) is not dict
            or set(shared_budget["used"]) != {"core", "dlc"}
            or any(
                type(value) is not int
                or value < 0
                or value > shared_budget["capacities"][key]
                for key, value in shared_budget["used"].items()
            )
            or not callable(getattr(shared_budget["lock"], "acquire", None))
            or not callable(getattr(shared_budget["lock"], "release", None))
        ):
            raise ValueError("invalid shared runtime budget")
        self.budget_state = shared_budget
        self._capacities = shared_budget["capacities"]
        self._used = shared_budget["used"]
        self._lock = shared_budget["lock"]

    def reserved_bytes(self, kind: str) -> int:
        with self._lock:
            return self._used[kind]

    def capture(
        self, installation_root: Path, release: Mapping[str, Any], *, kind: str
    ) -> RuntimeBundleLease:
        if kind not in ("core", "dlc"):
            raise RuntimeBundleError("invalid_bundle_kind")
        # Reparse even if a caller supplied a dict to establish the exact v1 shape.
        import json

        try:
            doc = parse_release(
                json.dumps(dict(release), ensure_ascii=False).encode("utf-8")
            )
        except (ProtocolError, TypeError, ValueError) as exc:
            raise RuntimeBundleError("invalid_release_inventory") from exc
        maximum = CORE_MAX_BYTES if kind == "core" else DLC_MAX_BYTES
        file_maximum = CORE_MAX_FILE_BYTES if kind == "core" else DLC_MAX_FILE_BYTES
        files_maximum = CORE_MAX_FILES if kind == "core" else DLC_MAX_FILES
        listed = {item["path"]: item for item in doc["files"]}
        if any("__pycache__" in path.split("/") for path in listed):
            raise RuntimeBundleError("generated_cache_in_inventory")
        if kind == "dlc" and any(
            part.casefold().removesuffix(".py") in _FORBIDDEN_DLC_PARTS
            for path in listed
            for part in path.split("/")
        ):
            raise RuntimeBundleError("reserved_package_path")
        if (
            len(listed) > files_maximum
            or sum(item["size"] for item in listed.values()) > maximum
        ):
            raise RuntimeBundleError("bundle_budget_exceeded")
        if any(item["size"] > file_maximum for item in listed.values()):
            raise RuntimeBundleError("bundle_budget_exceeded")
        with self._lock:
            if self._used[kind] + maximum > self._capacities[kind]:
                raise RuntimeBundleError("runtime_bundle_budget_exceeded")
            self._used[kind] += maximum
        try:
            captured = _capture_closed_tree(
                Path(installation_root), listed, file_maximum
            )
            lease = RuntimeBundleLease(self, kind, doc, captured)
        except BaseException:
            with self._lock:
                self._used[kind] -= maximum
            raise
        with self._lock:
            self._used[kind] -= maximum - lease.total_bytes
        return lease

    def _release(self, lease: RuntimeBundleLease) -> None:
        with self._lock:
            self._used[lease.kind] -= lease.total_bytes


class RuntimeBundleLease:
    """One captured artifact. Busy references and child packages retain it."""

    def __init__(
        self,
        store: RuntimeBundleStore,
        kind: str,
        release: dict[str, Any],
        files: dict[str, bytes],
    ) -> None:
        self._store = store
        self.kind = kind
        self.release_doc = MappingProxyType(release)
        self.files = MappingProxyType(dict(files))
        self.total_bytes = sum(map(len, files.values()))
        self.digest = release["payload_digest"]
        self._root = _materialize(files, self.digest)
        self.resource_root = self._root
        self._namespace: str | None = None
        self._finder: SnapshotFinder | None = None
        self._parent: RuntimeBundleLease | None = None
        self._children: set[RuntimeBundleLease] = set()
        self._owned_paths: set[str] = set()
        self._busy = 0
        self._released = False

    @property
    def namespace(self) -> str | None:
        return self._namespace

    def read_resource(self, path: str) -> bytes:
        if self._released or path not in self.files:
            raise RuntimeBundleError("resource_unavailable")
        return self.files[path]

    def retain(self) -> None:
        if self._released:
            raise RuntimeBundleError("bundle_released")
        self._busy += 1

    def drop(self) -> None:
        if self._busy <= 0:
            raise RuntimeBundleError("bundle_reference_underflow")
        self._busy -= 1

    def mount_core(self) -> str:
        if self.kind != "core" or self._released:
            raise RuntimeBundleError("invalid_core_mount")
        with _IMPORT_LOCK:
            if self._namespace is not None:
                return self._namespace
            if "yomihime_game_link_sdk/__init__.py" not in self.files:
                raise RuntimeBundleError("private_sdk_missing")
            namespace = f"_ygl_runtime_{uuid4().hex}"
            sources = {p: data for p, data in self.files.items() if p.endswith(".py")}
            sources["packages/_api_v2/__init__.py"] = (
                b"from ...yomihime_game_link_sdk import *\n"
            )
            for part in (
                "services",
                "declarations",
                "contexts",
                "results",
                "storage",
                "jobs",
                "cards",
                "display",
                "subscriptions",
                "errors",
                "host",
            ):
                sources[f"packages/_api_v2/{part}.py"] = (
                    f"from ...yomihime_game_link_sdk.{part} import *\n".encode()
                )
            origins = {p: str(self._root / p) for p in sources}
            finder = SnapshotFinder(namespace, sources, self.digest, origins)
            sys.meta_path.insert(0, finder)
            self._finder = finder
            self._namespace = namespace
            return namespace

    def mount_package(
        self, core: RuntimeBundleLease, package_id: str, business_root: str
    ) -> str:
        if (
            self.kind != "dlc"
            or core.kind != "core"
            or self._released
            or core._released
        ):
            raise RuntimeBundleError("invalid_package_mount")
        if self._namespace is not None:
            return self._namespace
        if (
            type(package_id) is not str
            or _PACKAGE_ID.fullmatch(package_id) is None
            or package_id != self.release_doc["package_id"]
        ):
            raise RuntimeBundleError("invalid_package_id")
        parts = business_root.split("/")
        if not parts or any(
            not part or part in (".", "..") or not part.isidentifier() for part in parts
        ):
            raise RuntimeBundleError("invalid_business_root")
        prefix = business_root + "/"
        relative = {
            p[len(prefix) :]: data
            for p, data in self.files.items()
            if p.startswith(prefix)
        }
        if not relative or not any(p.endswith(".py") for p in relative):
            raise RuntimeBundleError("business_root_missing")
        if any(
            part.casefold() in _FORBIDDEN_DLC_PARTS
            for p in relative
            for part in p.split("/")
        ):
            raise RuntimeBundleError("reserved_package_path")
        with _IMPORT_LOCK:
            if core._finder is None:
                raise RuntimeBundleError("core_not_mounted")
            key = "".join(c if c.isalnum() or c == "_" else "_" for c in package_id)
            if key[0].isdigit():
                key = "p_" + key
            child = f"{key}_{uuid4().hex}"
            base = f"packages/{child}"
            sources = {
                f"{base}/{p}": data for p, data in relative.items() if p.endswith(".py")
            }
            origins = {
                f"{base}/{p}": str(self._root / business_root / p)
                for p in relative
                if p.endswith(".py")
            }
            # Nested business modules import .._api_v2 from the child root.
            # Both facade locations resolve concrete types from this Core graph.
            for part in (
                "",
                "services",
                "declarations",
                "contexts",
                "results",
                "storage",
                "jobs",
                "cards",
                "display",
                "subscriptions",
                "errors",
                "host",
            ):
                name = "__init__" if not part else part
                suffix = "." + part if part else ""
                sources[f"{base}/_api_v2/{name}.py"] = (
                    f"from ....yomihime_game_link_sdk{suffix} import *\n".encode()
                )
            core._finder.add_files(sources, origins)
            self._owned_paths = set(sources)
            self._finder = core._finder
            self._namespace = f"{core.namespace}.packages.{child}"
            self._parent = core
            core._children.add(self)
            return self._namespace

    def resolve(self, factory_entry: str) -> Any:
        if (
            self.kind != "dlc"
            or self._released
            or self._parent is None
            or self._namespace is None
        ):
            raise RuntimeBundleError("package_not_mounted")
        if type(factory_entry) is not str or factory_entry.count(":") != 1:
            raise RuntimeBundleError("invalid_factory_entry")
        module_name, attribute = factory_entry.split(":", 1)
        if (
            not module_name
            or not all(part.isidentifier() for part in module_name.split("."))
            or not attribute.isidentifier()
        ):
            raise RuntimeBundleError("invalid_factory_entry")
        with _IMPORT_LOCK:
            factory_type = importlib.import_module(
                f"{self._parent.namespace}.yomihime_game_link_sdk.services"
            ).ModuleFactory
            module = importlib.import_module(f"{self._namespace}.{module_name}")
            value = getattr(module, attribute)
            if isinstance(value, type):
                value = value()
            if not isinstance(value, factory_type):
                raise RuntimeBundleError("factory_type_mismatch")
            return value

    def release(self) -> str:
        if self._released:
            return "released"
        with _IMPORT_LOCK:
            if self._busy or self._children:
                return "retained_busy"
            try:
                _remove_materialized(self._root)
            except OSError as exc:
                # Keep the import and accounting lease until cleanup succeeds.
                raise RuntimeBundleError("resource_cleanup_failed") from exc
            if self._parent is None and self._finder is not None:
                self._finder._released = True
                if self._finder in sys.meta_path:
                    sys.meta_path.remove(self._finder)
                self._finder._files.clear()
                self._finder._module_paths.clear()
                self._finder._origins.clear()
                for name in tuple(sys.modules):
                    if name == self._namespace or name.startswith(
                        self._namespace + "."
                    ):
                        sys.modules.pop(name, None)
            elif self._parent is not None:
                assert self._finder is not None
                self._finder.remove_files(self._owned_paths)
                parent_name, child_name = self._namespace.rsplit(".", 1)
                parent_module = sys.modules.get(parent_name)
                child_module = sys.modules.get(self._namespace)
                if (
                    child_module is not None
                    and parent_module is not None
                    and getattr(parent_module, child_name, None) is child_module
                ):
                    delattr(parent_module, child_name)
                for name in tuple(sys.modules):
                    if name == self._namespace or name.startswith(
                        self._namespace + "."
                    ):
                        sys.modules.pop(name, None)
                self._parent._children.remove(self)
            self._released = True
            self.files = MappingProxyType({})
            self._owned_paths.clear()
            self._parent = None
            self._finder = None
            self._store._release(self)
            return "released"


def _capture_closed_tree(
    root: Path, listed: Mapping[str, Mapping[str, Any]], file_limit: int
) -> dict[str, bytes]:
    if os.name == "nt":
        return _capture_windows_tree(root, listed, file_limit)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        root_fd = os.open(root, flags)
    except OSError as exc:
        raise RuntimeBundleError("installation_root_unavailable") from exc
    try:
        initial = os.fstat(root_fd)
        if not stat.S_ISDIR(initial.st_mode):
            raise RuntimeBundleError("installation_root_unavailable")
        files: dict[str, bytes] = {}
        count = [0]

        def walk(fd: int, prefix: str, depth: int, cache: bool = False) -> None:
            if depth > MAX_DEPTH:
                raise RuntimeBundleError("bundle_tree_budget_exceeded")
            before = os.fstat(fd)
            with os.scandir(fd) as entries:
                names = sorted(entry.name for entry in entries)
            if len(names) + count[0] > MAX_TREE_ENTRIES:
                raise RuntimeBundleError("bundle_tree_budget_exceeded")
            count[0] += len(names)
            if len({name.casefold() for name in names}) != len(names):
                raise RuntimeBundleError("case_collision")
            for name in names:
                path = f"{prefix}/{name}" if prefix else name
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    raise RuntimeBundleError("bundle_link_rejected")
                if info.st_dev != initial.st_dev:
                    raise RuntimeBundleError("bundle_cross_volume")
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(name, flags, dir_fd=fd)
                    try:
                        if _identity(os.fstat(child)) != _identity(info):
                            raise RuntimeBundleError("bundle_changed")
                        # Validate generated caches for links/collisions as well.
                        walk(child, path, depth + 1, cache or name == "__pycache__")
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode):
                    if path not in listed:
                        if cache:
                            continue
                        if path.casefold() in _EXCLUDED:
                            continue
                        raise RuntimeBundleError("unlisted_file")
                    if (
                        info.st_nlink != 1
                        or info.st_size > file_limit
                        or info.st_size != listed[path]["size"]
                    ):
                        raise RuntimeBundleError("bundle_file_invalid")
                    file_fd = os.open(
                        name,
                        os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                        dir_fd=fd,
                    )
                    try:
                        opened = os.fstat(file_fd)
                        if _identity(opened) != _identity(info):
                            raise RuntimeBundleError("bundle_changed")
                        data = b""
                        while len(data) <= file_limit:
                            chunk = os.read(
                                file_fd, min(65536, file_limit + 1 - len(data))
                            )
                            if not chunk:
                                break
                            data += chunk
                        if _identity(os.fstat(file_fd)) != _identity(opened):
                            raise RuntimeBundleError("bundle_changed")
                    finally:
                        os.close(file_fd)
                    if (
                        len(data) != listed[path]["size"]
                        or hashlib.sha256(data).hexdigest() != listed[path]["sha256"]
                    ):
                        raise RuntimeBundleError("bundle_hash_mismatch")
                    files[path] = data
                else:
                    raise RuntimeBundleError("bundle_special_file")
            if _identity(os.fstat(fd)) != _identity(before):
                raise RuntimeBundleError("bundle_changed")
            with os.scandir(fd) as entries:
                if names != sorted(entry.name for entry in entries):
                    raise RuntimeBundleError("bundle_changed")

        walk(root_fd, "", 0)
        if files.keys() != listed.keys():
            raise RuntimeBundleError("missing_file")
        if _identity(os.stat(root, follow_symlinks=False)) != _identity(initial):
            raise RuntimeBundleError("installation_root_changed")
        return files
    except OSError as exc:
        raise RuntimeBundleError("bundle_unavailable") from exc
    finally:
        os.close(root_fd)


def _identity(info: os.stat_result) -> tuple[int, ...]:
    from .file_identity import FileIdentityUnavailable, stable_identity

    try:
        return stable_identity(info)
    except FileIdentityUnavailable:
        raise RuntimeBundleError("bundle_identity_unavailable") from None


def _handle_identity(info: os.stat_result) -> tuple[int, ...]:
    from .file_identity import FileIdentityUnavailable, handle_identity

    try:
        return handle_identity(info)
    except FileIdentityUnavailable:
        raise RuntimeBundleError("bundle_identity_unavailable") from None


def _capture_windows_tree(
    root: Path, listed: Mapping[str, Mapping[str, Any]], file_limit: int
) -> dict[str, bytes]:
    """Handle-checked Windows capture; native race testing belongs in Host CI.

    Every component is checked for reparse points before and after the walk.
    Opened file IDs, lengths, timestamps and bytes must match the closed release.
    """
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

    def checked(path: Path) -> os.stat_result:
        info = path.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & reparse
        ):
            raise RuntimeBundleError("bundle_link_rejected")
        return info

    try:
        initial = checked(root)
        if not stat.S_ISDIR(initial.st_mode):
            raise RuntimeBundleError("installation_root_unavailable")
        files: dict[str, bytes] = {}
        count = [0]
        directories: list[tuple[Path, tuple[int, ...], tuple[str, ...]]] = []

        def walk(directory: Path, prefix: str, depth: int, cache: bool = False) -> None:
            if depth > MAX_DEPTH:
                raise RuntimeBundleError("bundle_tree_budget_exceeded")
            before = checked(directory)
            names = tuple(sorted(entry.name for entry in os.scandir(directory)))
            count[0] += len(names)
            if count[0] > MAX_TREE_ENTRIES:
                raise RuntimeBundleError("bundle_tree_budget_exceeded")
            if len({n.casefold() for n in names}) != len(names):
                raise RuntimeBundleError("case_collision")
            directories.append((directory, _identity(before), names))
            for name in names:
                path = directory / name
                relative = f"{prefix}/{name}" if prefix else name
                info = checked(path)
                if info.st_dev != initial.st_dev:
                    raise RuntimeBundleError("bundle_cross_volume")
                if stat.S_ISDIR(info.st_mode):
                    walk(path, relative, depth + 1, cache or name == "__pycache__")
                elif stat.S_ISREG(info.st_mode):
                    if relative not in listed:
                        if cache:
                            continue
                        if relative.casefold() in _EXCLUDED:
                            continue
                        raise RuntimeBundleError("unlisted_file")
                    if (
                        info.st_nlink != 1
                        or info.st_size > file_limit
                        or info.st_size != listed[relative]["size"]
                    ):
                        raise RuntimeBundleError("bundle_file_invalid")
                    handle = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
                    try:
                        opened = os.fstat(handle)
                        if _identity(opened) != _identity(info):
                            raise RuntimeBundleError("bundle_changed")
                        chunks: list[bytes] = []
                        size = 0
                        while size <= file_limit:
                            chunk = os.read(handle, min(65536, file_limit + 1 - size))
                            if not chunk:
                                break
                            chunks.append(chunk)
                            size += len(chunk)
                        data = b"".join(chunks)
                        if _handle_identity(os.fstat(handle)) != _handle_identity(
                            opened
                        ):
                            raise RuntimeBundleError("bundle_changed")
                    finally:
                        os.close(handle)
                    if _identity(checked(path)) != _identity(info):
                        raise RuntimeBundleError("bundle_changed")
                    if (
                        len(data) != listed[relative]["size"]
                        or hashlib.sha256(data).hexdigest()
                        != listed[relative]["sha256"]
                    ):
                        raise RuntimeBundleError("bundle_hash_mismatch")
                    files[relative] = data
                else:
                    raise RuntimeBundleError("bundle_special_file")

        walk(root, "", 0)
        for directory, identity, names in directories:
            if (
                _identity(checked(directory)) != identity
                or tuple(sorted(entry.name for entry in os.scandir(directory))) != names
            ):
                raise RuntimeBundleError("bundle_changed")
        if _identity(checked(root)) != _identity(initial):
            raise RuntimeBundleError("installation_root_changed")
        if files.keys() != listed.keys():
            raise RuntimeBundleError("missing_file")
        return files
    except OSError as exc:
        raise RuntimeBundleError("bundle_unavailable") from exc


def _materialize(files: Mapping[str, bytes], digest: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix=f"ygl-{digest[:12]}-"))
    try:
        for name, data in files.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(data)
            target.chmod(0o444)
        for directory, _, _ in os.walk(root, topdown=False):
            Path(directory).chmod(0o555)
        return root
    except BaseException:
        _remove_materialized(root)
        raise


def _remove_materialized(root: Path) -> None:
    if not root.exists():
        return
    for directory, _, files in os.walk(root):
        Path(directory).chmod(0o700)
        for name in files:
            (Path(directory) / name).chmod(0o600)
    shutil.rmtree(root)
