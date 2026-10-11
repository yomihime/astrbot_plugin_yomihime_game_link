"""Validated, immutable snapshots of loaded package declarations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType

from yomihime_game_link_sdk.declarations import ModuleManifest, PackageManifest
from yomihime_game_link_sdk.services import ModuleHandlers

from ..core.contracts.validation_boundary import validate_contract


class RegistryError(ValueError):
    """A package or registry update does not satisfy the static contract."""


def _query_key(value: object, field: str) -> str:
    """Validate lookup keys before passing them to a mapping."""

    if not isinstance(value, str):
        raise RegistryError(f"{field} must be text")
    try:
        hash(value)
    except TypeError as exc:
        raise RegistryError(f"{field} is not a valid lookup key") from exc
    return value


def _require_callable_method(handler: object, method_name: str, message: str):
    method = None
    try:
        method = getattr(handler, method_name)
    except Exception:
        pass
    if not callable(method):
        raise RegistryError(message)
    return method


@dataclass(frozen=True, slots=True)
class RegisteredModule:
    """One module in a registry snapshot.

    ``handlers`` is trusted runtime code.  The registry freezes the mapping
    that points to those handlers, but does not claim to freeze their internal
    state.
    """

    module_id: str
    manifest: ModuleManifest
    handlers: ModuleHandlers
    enabled: bool
    epoch: int


@dataclass(frozen=True, slots=True)
class RegistrySnapshot:
    """A complete, read-only view shared by help and invocation consumers."""

    revision: int
    modules: Mapping[str, RegisteredModule]
    routes: Mapping[str, str]
    tools: Mapping[str, tuple[str, str]]

    def __post_init__(self) -> None:
        if isinstance(self.revision, bool) or not isinstance(self.revision, int):
            raise TypeError("registry revision must be an integer")
        if self.revision < 0:
            raise ValueError("registry revision must be non-negative")
        for name, value in (
            ("modules", self.modules),
            ("routes", self.routes),
            ("tools", self.tools),
        ):
            if not isinstance(value, Mapping):
                raise TypeError(f"registry {name} must be a mapping")
            object.__setattr__(self, name, MappingProxyType(dict(value)))

    def module(self, module_id: str) -> RegisteredModule:
        """Return a module or raise a stable lookup error."""

        module_id = _query_key(module_id, "module_id")
        try:
            return self.modules[module_id]
        except KeyError as exc:
            raise RegistryError("module is not registered") from exc

    def module_for_route(self, route: str) -> RegisteredModule:
        """Resolve a user route through this snapshot."""

        route = _query_key(route, "route")
        try:
            return self.modules[self.routes[route]]
        except KeyError as exc:
            raise RegistryError("route is not registered") from exc

    def tool(self, name: str) -> tuple[str, str]:
        """Return ``(global_module_id, local_capability_id)`` for a Tool."""

        name = _query_key(name, "tool name")
        try:
            return self.tools[name]
        except KeyError as exc:
            raise RegistryError("tool is not registered") from exc


class Registry:
    """Own the current package set and publish whole immutable snapshots."""

    def __init__(self) -> None:
        self._packages: set[str] = set()
        self._observers: set[Callable[[], None]] = set()
        self._lifecycle_owner: object | None = None
        self._is_lifecycle_active: Callable[[str], bool] | None = None
        self._snapshot = RegistrySnapshot(
            revision=0,
            modules={},
            routes={},
            tools={},
        )

    def observe(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Observe published lifecycle projections; observers cannot undo commits."""
        self._observers.add(callback)
        return lambda: self._observers.discard(callback)

    def _notify(self) -> None:
        for callback in tuple(self._observers):
            try:
                callback()
            except Exception:
                # The projection is already committed; observer owns recovery.
                continue

    def snapshot(self) -> RegistrySnapshot:
        """Return the current snapshot object without copying or mutating it."""

        return self._snapshot

    def is_active(self, module_id: str | RegisteredModule) -> bool:
        """Consult the Lifecycle owner; intent alone never proves availability."""
        if self._is_lifecycle_active is None:
            return False
        try:
            if isinstance(module_id, RegisteredModule):
                registered = module_id
                module_id = registered.module_id
                # Registry keeps the same owned module object across unrelated
                # publications, but replaces it for every lifecycle projection.
                if registered is not self._snapshot.module(module_id):
                    return False
            state = self._lifecycle_owner.guard(module_id)
            return (
                state.cleanup_pending is False
                and state.identity is self._lifecycle_owner.current_identity(module_id)
            )
        except Exception:
            return False

    def register_package(
        self,
        manifest: PackageManifest,
        handlers_by_module: Mapping[str, ModuleHandlers],
    ) -> RegistrySnapshot:
        """Validate and atomically register one complete package."""

        validate_contract(manifest)
        validate_contract(handlers_by_module)
        if not isinstance(manifest, PackageManifest):
            raise RegistryError("manifest must be a PackageManifest")
        if not isinstance(handlers_by_module, Mapping):
            raise RegistryError("handlers_by_module must be a mapping")
        if manifest.package_id in self._packages:
            raise RegistryError("package is already registered")

        local_ids = {module.module_id for module in manifest.modules}
        if set(handlers_by_module) != local_ids:
            raise RegistryError("handlers do not match the package modules")

        old = self._snapshot
        candidate_modules = dict(old.modules)
        candidate_routes = dict(old.routes)
        candidate_tools = dict(old.tools)
        additions: dict[str, RegisteredModule] = {}

        # Build and validate every index entry before touching registry state.
        for module in manifest.modules:
            global_id = manifest.global_module_id(module.module_id)
            if global_id == "game_link/core":
                raise RegistryError("module ID is reserved by Core")
            if global_id in candidate_modules:
                raise RegistryError("module is already registered")
            if module.route in candidate_routes:
                raise RegistryError("route is already registered")

            handlers = handlers_by_module[module.module_id]
            self._validate_handlers(module, handlers)
            additions[global_id] = RegisteredModule(
                module_id=global_id,
                manifest=module,
                handlers=handlers,
                enabled=False,
                # Epochs belong to Lifecycle and begin only when an instance
                # is actually started. Registry keeps a read-only projection.
                epoch=0,
            )

            for tool in module.tools:
                if tool.name in candidate_tools:
                    raise RegistryError("tool name is already registered")
                candidate_tools[tool.name] = (global_id, tool.capability_id)
            candidate_routes[module.route] = global_id

        candidate_modules.update(additions)
        new_snapshot = RegistrySnapshot(
            revision=old.revision + 1,
            modules=candidate_modules,
            routes=candidate_routes,
            tools=candidate_tools,
        )
        self._packages.add(manifest.package_id)
        self._snapshot = new_snapshot
        self._notify()
        return new_snapshot

    def set_enabled(self, module_id: str, enabled: bool) -> RegistrySnapshot:
        """Compatibility intent/projection update for inactive modules only.

        This method never creates or changes a run identity and never opens an
        admission gate. Active modules must be changed through Lifecycle.
        """

        if not isinstance(enabled, bool):
            raise RegistryError("enabled must be a bool")
        module_id = _query_key(module_id, "module_id")
        old = self._snapshot
        try:
            module = old.modules[module_id]
        except KeyError as exc:
            raise RegistryError("module is not registered") from exc
        if self._is_lifecycle_active is not None and self._is_lifecycle_active(
            module_id
        ):
            raise RegistryError("active module state is owned by Lifecycle")
        if module.enabled is enabled:
            return old

        modules = dict(old.modules)
        modules[module_id] = replace(
            module,
            enabled=enabled,
        )
        new_snapshot = RegistrySnapshot(
            revision=old.revision + 1,
            modules=modules,
            routes=old.routes,
            tools=old.tools,
        )
        self._snapshot = new_snapshot
        self._notify()
        return new_snapshot

    def _bind_lifecycle_owner(
        self, owner: object, is_active: Callable[[str], bool]
    ) -> None:
        """Bind the sole lifecycle projection writer for this Registry."""
        if not callable(is_active):
            raise TypeError("Lifecycle active check must be callable")
        if self._lifecycle_owner is None:
            self._lifecycle_owner = owner
            self._is_lifecycle_active = is_active
        elif self._lifecycle_owner is not owner:
            raise RegistryError("Registry already has a Lifecycle owner")

    def _publish_lifecycle_projection(
        self,
        module_id: str,
        *,
        enabled: bool,
        epoch: int,
        expected_revision: int | None = None,
    ) -> RegistrySnapshot:
        """Publish Lifecycle-owned state without allocating a run epoch."""
        if self._lifecycle_owner is None:
            raise RegistryError("Registry has no Lifecycle projection owner")
        if not isinstance(enabled, bool):
            raise RegistryError("enabled must be a bool")
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
            raise RegistryError("epoch must be a non-negative integer")
        module_id = _query_key(module_id, "module_id")
        old = self._snapshot
        if expected_revision is not None and old.revision != expected_revision:
            raise RegistryError("registry revision compare-and-swap failed")
        try:
            module = old.modules[module_id]
        except KeyError as exc:
            raise RegistryError("module is not registered") from exc
        if module.enabled is enabled and module.epoch == epoch:
            return old
        modules = dict(old.modules)
        modules[module_id] = replace(module, enabled=enabled, epoch=epoch)
        updated = RegistrySnapshot(
            revision=old.revision + 1,
            modules=modules,
            routes=old.routes,
            tools=old.tools,
        )
        self._snapshot = updated
        self._notify()
        return updated

    def _install_lifecycle_handlers(
        self, module_id: str, handlers: ModuleHandlers
    ) -> RegistrySnapshot:
        """Publish the handlers owned by an installed Lifecycle instance."""
        validate_contract(handlers)
        if self._lifecycle_owner is None:
            raise RegistryError("Registry has no Lifecycle projection owner")
        module_id = _query_key(module_id, "module_id")
        old = self._snapshot
        try:
            module = old.modules[module_id]
        except KeyError as exc:
            raise RegistryError("module is not registered") from exc
        self._validate_handlers(module.manifest, handlers)
        modules = dict(old.modules)
        modules[module_id] = replace(module, handlers=handlers)
        updated = RegistrySnapshot(
            revision=old.revision + 1,
            modules=modules,
            routes=old.routes,
            tools=old.tools,
        )
        self._snapshot = updated
        self._notify()
        return updated

    def _detach_lifecycle_module(self, owner, module_id):
        if owner is not self._lifecycle_owner or self.is_active(module_id):
            raise RegistryError("only quiescent Lifecycle owner may detach a module")
        old = self._snapshot
        old.module(module_id)
        self._snapshot = RegistrySnapshot(
            old.revision + 1,
            {key: value for key, value in old.modules.items() if key != module_id},
            {key: value for key, value in old.routes.items() if value != module_id},
            {key: value for key, value in old.tools.items() if value[0] != module_id},
        )
        package_id = module_id.split("/", 1)[0]
        if not any(key.startswith(package_id + "/") for key in self._snapshot.modules):
            self._packages.discard(package_id)
        self._notify()
        return self._snapshot

    def _restore_lifecycle_module(self, owner, package_id, manifest, handlers):
        if owner is not self._lifecycle_owner:
            raise RegistryError("only Lifecycle may restore a module")
        module_id = f"{package_id}/{manifest.module_id}"
        old = self._snapshot
        if (
            module_id in old.modules
            or manifest.route in old.routes
            or any(tool.name in old.tools for tool in manifest.tools)
        ):
            raise RegistryError("restored module indexes conflict")
        self._validate_handlers(manifest, handlers)
        self._snapshot = RegistrySnapshot(
            old.revision + 1,
            {
                **old.modules,
                module_id: RegisteredModule(module_id, manifest, handlers, False, 0),
            },
            {**old.routes, manifest.route: module_id},
            {
                **old.tools,
                **{
                    tool.name: (module_id, tool.capability_id)
                    for tool in manifest.tools
                },
            },
        )
        self._packages.add(package_id)
        self._notify()
        return self._snapshot

    @staticmethod
    def _validate_handlers(module: ModuleManifest, handlers: ModuleHandlers) -> None:
        validate_contract(module)
        validate_contract(handlers)
        if not isinstance(handlers, ModuleHandlers):
            raise RegistryError("module handlers have an invalid type")
        expected = {capability.capability_id for capability in module.capabilities}
        actual = set(handlers.capabilities)
        if actual != expected:
            raise RegistryError("capability handlers do not match the manifest")
        for handler in handlers.capabilities.values():
            _require_callable_method(
                handler, "invoke", "capability handler invoke is not callable"
            )

        expected_collectors = {schedule.collector_id for schedule in module.schedules}
        if set(handlers.collectors) != expected_collectors:
            raise RegistryError("collector handlers do not match the manifest")
        for collector in handlers.collectors.values():
            _require_callable_method(
                collector, "normalize", "collector normalize is not callable"
            )
            _require_callable_method(
                collector, "collect", "collector collect is not callable"
            )

        expected_evaluators = {
            subscription.matcher_id for subscription in module.subscriptions
        }
        if set(handlers.evaluators) != expected_evaluators:
            raise RegistryError("evaluator handlers do not match the manifest")
        for evaluator in handlers.evaluators.values():
            _require_callable_method(
                evaluator, "evaluate", "evaluator evaluate is not callable"
            )
