"""Private, byte-backed Python import namespace shared by snapshot consumers."""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import importlib.util
from collections.abc import Mapping
from types import ModuleType


class SnapshotImportError(ValueError):
    """Ambiguous source layout in a captured bundle."""


class SnapshotFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Claim every import below a private prefix, including absent names."""

    def __init__(
        self,
        namespace: str,
        files: Mapping[str, bytes],
        token: str,
        origins: Mapping[str, str] | None = None,
    ) -> None:
        self.namespace = namespace
        self._files = dict(files)
        self._token = token
        self._origins = dict(origins or {})
        self._module_paths = _module_paths(self._files)
        self._released = False

    def add_files(
        self, files: Mapping[str, bytes], origins: Mapping[str, str] | None = None
    ) -> None:
        if self._released or self._files.keys() & files.keys():
            raise SnapshotImportError("snapshot namespace collision")
        merged = self._files | dict(files)
        paths = _module_paths(merged)
        self._files = merged
        self._module_paths = paths
        self._origins.update(origins or {})

    def remove_files(self, paths: set[str]) -> None:
        if self._released:
            return
        self._files = {
            name: data for name, data in self._files.items() if name not in paths
        }
        for path in paths:
            self._origins.pop(path, None)
        self._module_paths = _module_paths(self._files)

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
            spec = importlib.util.spec_from_loader(fullname, self, is_package=False)
            assert spec is not None
            spec.origin = f"snapshot://{self._token}/missing/{relative}"
            return spec
        source_path, is_package = details
        spec = importlib.util.spec_from_loader(fullname, self, is_package=is_package)
        assert spec is not None
        spec.origin = self._origins.get(
            source_path, f"snapshot://{self._token}/{source_path}"
        )
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
        module.__file__ = self._origins.get(
            source_path, f"snapshot://{self._token}/{source_path}"
        )
        if is_package:
            module.__path__ = []
        code = compile(
            self._files[source_path], module.__file__, "exec", dont_inherit=True
        )
        exec(code, module.__dict__)


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
        else:
            _insert_module(modules, stem, (path, False))
        for index in range(1, len(parts)):
            packages.add("/".join(parts[:index]))
    for package in packages:
        if package not in modules:
            modules[package] = ("", True)
        elif not modules[package][1]:
            raise SnapshotImportError("captured module path is ambiguous")
    modules.setdefault("", ("", True))
    return modules


def _insert_module(
    modules: dict[str, tuple[str, bool]], name: str, entry: tuple[str, bool]
) -> None:
    if name in modules:
        raise SnapshotImportError("captured module path is ambiguous")
    modules[name] = entry
