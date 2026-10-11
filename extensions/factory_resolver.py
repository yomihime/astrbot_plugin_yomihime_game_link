"""Authorize-then-capture factory resolution from immutable source bundles."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
import threading
from collections.abc import Mapping
from types import ModuleType
from typing import Any
from uuid import uuid4

from yomihime_game_link_sdk.services import ModuleFactory

from .loader import ExtensionCandidate
from .source_snapshot import (
    MAX_PACKAGE_SOURCE_BYTES,
    SourceBundle,
    SourceSnapshotError,
    SourceSnapshotLimits,
    capture_source_bundle,
)

_IMPORT_LOCK = threading.RLock()


class FactoryResolutionError(RuntimeError):
    """A safe failure while resolving an entry from a captured bundle."""


class FilesystemFactorySource:
    """One CoreRuntime's owner for bounded captures and their source leases."""

    def __init__(
        self,
        *,
        max_package_bytes: int = MAX_PACKAGE_SOURCE_BYTES,
        max_file_bytes: int | None = None,
        max_files: int | None = None,
        max_tree_entries: int | None = None,
        max_depth: int | None = None,
        max_runtime_bytes: int = 32 * 1024 * 1024,
    ) -> None:
        values: dict[str, int] = {"max_package_bytes": max_package_bytes}
        if max_file_bytes is not None:
            values["max_file_bytes"] = max_file_bytes
        if max_files is not None:
            values["max_files"] = max_files
        if max_tree_entries is not None:
            values["max_tree_entries"] = max_tree_entries
        if max_depth is not None:
            values["max_depth"] = max_depth
        values["max_runtime_bytes"] = max_runtime_bytes
        self._limits = SourceSnapshotLimits(**values)
        self._lock = threading.Lock()
        self._reserved_bytes = 0
        self._leases: set[_FactoryBundleLease] = set()

    @property
    def reserved_bytes(self) -> int:
        """A read-only budget observation used by focused component tests."""
        with self._lock:
            return self._reserved_bytes

    async def capture(self, candidate: ExtensionCandidate) -> _FactoryBundleLease:
        package = getattr(candidate, "package", None)
        provenance = getattr(package, "_provenance", None)
        if provenance is None:
            raise SourceSnapshotError("candidate_stale")
        reservation = self._limits.max_package_bytes
        with self._lock:
            if self._reserved_bytes + reservation > self._limits.max_runtime_bytes:
                raise SourceSnapshotError("runtime_source_budget_exceeded")
            self._reserved_bytes += reservation

        reader = asyncio.create_task(
            asyncio.to_thread(capture_source_bundle, provenance, self._limits),
            name=f"extension-source-capture:{package.package_id or 'invalid'}",
        )
        try:
            bundle = await asyncio.shield(reader)
        except asyncio.CancelledError:
            # Keep this capture in A's build-flight await until the actual
            # worker has closed every handle. No detached completion owner.
            while not reader.done():
                try:
                    await asyncio.shield(reader)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    break
            try:
                if reader.done() and not reader.cancelled():
                    reader.exception()
            finally:
                self._release_reservation(reservation)
            raise
        except BaseException:
            self._release_reservation(reservation)
            raise

        try:
            lease = _FactoryBundleLease(self, bundle, reservation)
        except BaseException:
            self._release_reservation(reservation)
            raise
        with self._lock:
            self._leases.add(lease)
            self._reserved_bytes -= reservation - bundle.total_bytes
        return lease

    def _release_reservation(self, amount: int) -> None:
        with self._lock:
            self._reserved_bytes -= amount
            if self._reserved_bytes < 0:
                self._reserved_bytes = 0

    def _release_lease(self, lease: _FactoryBundleLease) -> None:
        with _IMPORT_LOCK:
            lease._remove_namespace_locked()
        with self._lock:
            if lease in self._leases:
                self._leases.remove(lease)
                self._reserved_bytes -= lease._reserved_bytes
                lease._reserved_bytes = 0
                if self._reserved_bytes < 0:
                    self._reserved_bytes = 0


class ReviewedInventoryFactorySource(FilesystemFactorySource):
    """Consistency fence for the explicit Host-selected immutable inventory."""

    def __init__(self, inventory, *, selected=False):
        if selected is not True:
            raise FactoryResolutionError("reviewed inventory was not selected")
        super().__init__()
        self._expected_package = inventory.package_id
        self._expected_manifest = hashlib.sha256(inventory.manifest).digest()
        self._expected_sources = {
            name: content
            for name, content in inventory.files.items()
            if name.endswith(".py")
        }

    async def capture(self, candidate):
        package = candidate.package
        provenance = package._provenance
        if (
            package.package_id != self._expected_package
            or provenance is None
            or provenance.manifest_sha256 != self._expected_manifest
        ):
            raise SourceSnapshotError("candidate_stale")
        lease = await super().capture(candidate)
        if dict(lease._bundle.files) != self._expected_sources:
            lease.release()
            raise SourceSnapshotError("candidate_stale")
        return lease


class _SnapshotFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Imports one private namespace exclusively from immutable source bytes."""

    def __init__(self, namespace: str, files: Mapping[str, bytes], token: str) -> None:
        self.namespace = namespace
        self._files = dict(files)
        self._token = token
        self._module_paths = _module_paths(self._files)
        self._released = False

    def find_spec(
        self, fullname: str, path: object = None, target: ModuleType | None = None
    ) -> importlib.machinery.ModuleSpec | None:
        del path, target
        if fullname != self.namespace and not fullname.startswith(self.namespace + "."):
            return None
        if self._released:
            raise ModuleNotFoundError(
                f"captured namespace {self.namespace!r} is released"
            )
        relative = fullname[len(self.namespace) :].lstrip(".").replace(".", "/")
        details = self._module_paths.get(relative)
        if details is None:
            # Claim missing names inside this namespace so PathFinder can never
            # fall back to a same-named disk package.
            loader = self
            spec = importlib.util.spec_from_loader(fullname, loader, is_package=False)
            assert spec is not None
            spec.origin = f"snapshot://{self._token}/missing/{relative}"
            return spec
        source_path, is_package = details
        spec = importlib.util.spec_from_loader(fullname, self, is_package=is_package)
        assert spec is not None
        spec.origin = f"snapshot://{self._token}/{source_path}"
        if is_package:
            spec.submodule_search_locations = []
        return spec

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType | None:
        del spec
        return None

    def exec_module(self, module: ModuleType) -> None:
        if self._released:
            raise ImportError("captured namespace has been released")
        fullname = module.__name__
        if fullname != self.namespace and not fullname.startswith(self.namespace + "."):
            raise ImportError("module is outside its captured namespace")
        relative = fullname[len(self.namespace) :].lstrip(".").replace(".", "/")
        details = self._module_paths.get(relative)
        if details is None:
            raise ModuleNotFoundError(f"captured module {fullname!r} is absent")
        source_path, is_package = details
        if not source_path:
            if is_package:
                module.__path__ = []
                return
            raise ModuleNotFoundError(f"captured module {fullname!r} is absent")
        module.__file__ = f"snapshot://{self._token}/{source_path}"
        if is_package:
            module.__path__ = []
        code = compile(
            self._files[source_path],
            module.__file__,
            "exec",
            dont_inherit=True,
        )
        exec(code, module.__dict__)


class ReviewedSupportLease:
    """Explicit reviewed assembly code from already captured immutable bytes.

    This is internal Host wiring, never a manifest entry or factory authority.
    Namespace disposal removes future imports; it does not kill existing code.
    """

    def __init__(self, files: Mapping[str, bytes], *, selected: bool = False):
        if selected is not True:
            raise FactoryResolutionError("reviewed support was not selected")
        sources = {
            name: content for name, content in files.items() if name.endswith(".py")
        }
        if (
            "assembly.py" not in sources
            or len(sources) > SourceSnapshotLimits().max_files
            or sum(map(len, sources.values())) > MAX_PACKAGE_SOURCE_BYTES
        ):
            raise FactoryResolutionError("reviewed support inventory is invalid")
        self._namespace = f"_yomihime_reviewed_{uuid4().hex}"
        self._finder = _SnapshotFinder(self._namespace, sources, uuid4().hex)
        self._released = False
        self.support = None
        with _IMPORT_LOCK:
            sys.meta_path.insert(0, self._finder)
            try:
                # Fixed Host-selected pure support entry. JSON cannot redirect it.
                module = importlib.import_module(f"{self._namespace}.assembly")
                self.support = module.SUPPORT
            except BaseException:
                self.release()
                raise

    def release(self) -> None:
        if self._released:
            return
        with _IMPORT_LOCK:
            self._released = True
            if self._finder in sys.meta_path:
                sys.meta_path.remove(self._finder)
            self._finder._released = True
            self._finder._files.clear()
            self._finder._module_paths.clear()
            for name in tuple(sys.modules):
                if name == self._namespace or name.startswith(self._namespace + "."):
                    sys.modules.pop(name, None)
            self.support = None


class _FactoryBundleLease:
    """A's synchronous lease contract over one captured source bundle."""

    def __init__(
        self, owner: FilesystemFactorySource, bundle: SourceBundle, reserved_bytes: int
    ) -> None:
        self._owner = owner
        self._bundle: SourceBundle | None = bundle
        self._reserved_bytes = bundle.total_bytes
        # The capture reservation is reduced to actual retained source bytes.
        del reserved_bytes
        self._namespace = f"_yomihime_snapshot_{uuid4().hex}"
        self._finder = _SnapshotFinder(self._namespace, bundle.files, uuid4().hex)
        self._factories: dict[str, ModuleFactory] = {}
        self._released = False
        with _IMPORT_LOCK:
            sys.meta_path.insert(0, self._finder)

    def resolve(self, factory_entry: str) -> ModuleFactory:
        if self._released or self._bundle is None:
            raise FactoryResolutionError("factory bundle lease is released")
        if type(factory_entry) is not str:
            raise FactoryResolutionError("factory entry is invalid")
        if factory_entry.count(":") != 1:
            raise FactoryResolutionError("factory entry is invalid")
        module_name, attribute = factory_entry.split(":", 1)
        if (
            not module_name
            or not attribute
            or not all(part.isidentifier() for part in module_name.split("."))
            or not attribute.isidentifier()
        ):
            raise FactoryResolutionError("factory entry is invalid")
        with _IMPORT_LOCK:
            cached = self._factories.get(factory_entry)
            if cached is not None:
                return cached
            before = {
                name
                for name in sys.modules
                if name == self._namespace or name.startswith(self._namespace + ".")
            }
            try:
                module = importlib.import_module(f"{self._namespace}.{module_name}")
                value: Any = getattr(module, attribute)
                if isinstance(value, type):
                    value = value()
                if not isinstance(value, ModuleFactory):
                    raise FactoryResolutionError("factory entry is invalid")
                self._factories[factory_entry] = value
                return value
            except BaseException:
                for name in tuple(sys.modules):
                    if (
                        name == self._namespace
                        or name.startswith(self._namespace + ".")
                    ) and name not in before:
                        sys.modules.pop(name, None)
                raise

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._owner._release_lease(self)

    def _remove_namespace_locked(self) -> None:
        if self._finder in sys.meta_path:
            sys.meta_path.remove(self._finder)
        self._finder._released = True
        self._finder._files.clear()
        self._finder._module_paths.clear()
        for name in tuple(sys.modules):
            if name == self._namespace or name.startswith(self._namespace + "."):
                sys.modules.pop(name, None)
        self._factories.clear()
        self._bundle = None


def _module_paths(files: Mapping[str, bytes]) -> dict[str, tuple[str, bool]]:
    modules: dict[str, tuple[str, bool]] = {}
    packages: set[str] = set()
    for path in files:
        if not path.endswith(".py"):
            continue
        stem = path[:-3]
        parts = stem.split("/")
        if parts[-1] == "__init__":
            package = "/".join(parts[:-1])
            _insert_module(modules, package, (path, True))
            for index in range(1, len(parts)):
                packages.add("/".join(parts[:index]))
        else:
            _insert_module(modules, stem, (path, False))
            for index in range(1, len(parts)):
                packages.add("/".join(parts[:index]))
    for package in packages:
        if package not in modules:
            modules[package] = ("", True)
        elif not modules[package][1]:
            raise FactoryResolutionError("captured module path is ambiguous")
    # The private root itself is an implicit package with no source file.
    modules.setdefault("", ("", True))
    return modules


def _insert_module(
    modules: dict[str, tuple[str, bool]],
    name: str,
    entry: tuple[str, bool],
) -> None:
    if name in modules:
        raise FactoryResolutionError("captured module path is ambiguous")
    modules[name] = entry
