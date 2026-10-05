"""Authorized administrative operations over the live Core runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from functools import wraps
from math import isfinite
from types import MappingProxyType

from ..api.administration import (
    AdminAuthorizationContext,
    AdminAuthorizationDenied,
    AdminAuthorizationGrant,
    AdminOperation,
    AdminOperations,
    CapabilitySummary,
    ConfigSummary,
    CoreConfigSummary,
    ModuleAdminSnapshot,
    ModuleHealth,
    ModuleLifecycle,
    ModuleStatus,
)
from ..api.contexts import InvocationView
from ..api.manifests import ModuleManifest, PackageManifest
from ..api.services import ConfigPatch, ConfigSnapshot, ConfigTarget
from ..api.storage import SecretMetadataState
from ..core.health import HealthResolver
from ..core.lifecycle import LifecycleController
from ..core.ports import RevisionConflict
from ..core.registry import RegisteredModule, Registry, RegistrySnapshot
from ..extensions.discovery import DiscoveredPackage
from ..extensions.loader import ExtensionCandidate
from ..infrastructure.secret_store import SQLiteSecretStore
from ..infrastructure.sqlite.database import SQLiteDatabase
from ..infrastructure.sqlite.repositories_config import SQLiteConfigRepository
from ..services.admin_authorization import AdminAuthorizationService
from ..services.configuration import (
    ConfigurationCoordinator,
    validate_configuration_value,
)
from ..services.extension_runtime import ExtensionRuntime
from .core_configuration import (
    CORE_CONFIG_FIELDS,
    CORE_DEFAULTS_FIELD,
    CORE_MODULE_ID,
    DEFAULT_REGION,
    core_config_target,
)


@dataclass(frozen=True, slots=True)
class _ManifestSelection:
    manifest: ModuleManifest
    package_id: str
    candidate: ExtensionCandidate | None
    registered: RegisteredModule | None


@dataclass(frozen=True, slots=True)
class AdminStartupFailure:
    module_id: str
    reason_code: str


def _tracked_admin_request(method):
    """Keep Core's request owner alive through every awaited admin boundary."""

    @wraps(method)
    async def tracked(self, *args, **kwargs):
        self._require_accepting()
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("admin operation requires an asyncio task")
        self._inflight.add(task)
        try:
            return await method(self, *args, **kwargs)
        finally:
            self._inflight.discard(task)

    return tracked


class AdminOperationsService(AdminOperations):
    """Core implementation of all four SDK administration operations.

    Every operation obtains its own grant from ``AdminAuthorizationService``.
    Read projections run under Admission's shared mutation gate and fence both
    the durable admin generation and the exact Registry snapshot they read.
    """

    def __init__(
        self,
        *,
        authorization: AdminAuthorizationService,
        extension_runtime: ExtensionRuntime,
        registry: Registry,
        lifecycle: LifecycleController,
        health_resolver: HealthResolver,
        database: SQLiteDatabase,
        config_repository: SQLiteConfigRepository,
        secret_store: SQLiteSecretStore,
        config_principal_id: str,
        subscription_gate_fields: Mapping[ConfigTarget, tuple[str, ...]] | None = None,
        module_config_validators: Mapping[str, Mapping[str, object]] | None = None,
        ordinary_migration=None,
    ) -> None:
        if not isinstance(authorization, AdminAuthorizationService):
            raise TypeError("authorization must be AdminAuthorizationService")
        if not isinstance(extension_runtime, ExtensionRuntime):
            raise TypeError("extension_runtime must be ExtensionRuntime")
        if not isinstance(registry, Registry) or lifecycle.registry is not registry:
            raise TypeError("admin operations require the shared Registry/Lifecycle")
        if not isinstance(health_resolver, HealthResolver):
            raise TypeError("health_resolver must be HealthResolver")
        if not isinstance(database, SQLiteDatabase):
            raise TypeError("database must be SQLiteDatabase")
        if not isinstance(config_repository, SQLiteConfigRepository):
            raise TypeError("config_repository must be SQLiteConfigRepository")
        if not isinstance(secret_store, SQLiteSecretStore):
            raise TypeError("secret_store must be SQLiteSecretStore")
        if type(config_principal_id) is not str or not config_principal_id.strip():
            raise ValueError("config principal is required")
        if extension_runtime.registry is not registry:
            raise ValueError("extension runtime must use the shared Registry")
        self.authorization = authorization
        self.extension_runtime = extension_runtime
        self.registry = registry
        self.lifecycle = lifecycle
        self.health_resolver = health_resolver
        self.database = database
        self._config_repository = config_repository
        self.secret_store = secret_store
        self.config_principal_id = config_principal_id
        self._subscription_gate_fields = MappingProxyType(
            dict(subscription_gate_fields or {})
        )
        self._accepting = True
        self._module_config_validators = MappingProxyType(
            {
                module_id: MappingProxyType(dict(checks))
                for module_id, checks in (module_config_validators or {}).items()
            }
        )
        self._inflight: set[asyncio.Task[object]] = set()
        self._ordinary_migration = ordinary_migration

    def ordinary_resources(self):
        migration = self._ordinary_migration
        if migration is None:
            raise AdminAuthorizationDenied
        result = {}
        for field in migration.fields:
            result.setdefault(field.target, set()).add(field.declaration.name)
        return result

    @_tracked_admin_request
    async def ordinary_snapshot(self, *, authorization):
        resources = self.ordinary_resources()
        grant = await self.authorization.authorize(
            AdminOperation.READ_CONFIG,
            invocation=None,
            context=authorization,
            resources=resources,
        )
        result = {}
        async with self.lifecycle.admission.mutation("admin-ordinary-read"):
            for target in resources:
                await self.authorization.validate_generation(
                    grant, operation=AdminOperation.READ_CONFIG
                )
                snapshot = await self._config_repository.current(
                    target, grant=grant, operation=AdminOperation.READ_CONFIG
                )
                await self.authorization.validate_generation(
                    grant, operation=AdminOperation.READ_CONFIG
                )
                projection = {}
                for field in self._ordinary_migration.fields:
                    if field.target != target:
                        continue
                    name = field.declaration.name
                    present = name in snapshot.values
                    value = snapshot.values.get(name, field.declaration.default)
                    try:
                        if any(m.field == name for m in snapshot.secret_metadata):
                            raise ValueError("ordinary metadata")
                        validate_configuration_value(
                            field.declaration, value, field.validator
                        )
                    except (ValueError, TypeError):
                        state, value = "invalid", None
                    else:
                        state = "valid"
                    projection[name] = {
                        "value": value,
                        "state": state,
                        "present": present,
                        "source": "sqlite" if present else "default",
                    }
                result[target.module_id] = {
                    "revision": snapshot.revision,
                    "fields": projection,
                }
            self._require_accepting()
            await self.authorization.validate_generation(
                grant, operation=AdminOperation.READ_CONFIG
            )
        return result

    @_tracked_admin_request
    async def ordinary_rollback(self, expected_revisions, *, authorization):
        resources = self.ordinary_resources()
        grant = await self.authorization.authorize(
            AdminOperation.ROLLBACK_CONFIG,
            invocation=None,
            context=authorization,
            resources=resources,
        )
        async with self.lifecycle.admission.mutation("admin-ordinary-rollback"):
            await self.authorization.validate_generation(
                grant, operation=AdminOperation.ROLLBACK_CONFIG
            )
            await self._ordinary_migration.rollback(expected_revisions, grant=grant)
            await self.authorization.validate_generation(
                grant, operation=AdminOperation.ROLLBACK_CONFIG
            )
        return {"rolled_back": True}

    def _core_configuration(self) -> ConfigurationCoordinator:
        async def validate(grant):
            self._require_accepting()
            await self.authorization.validate_generation(
                grant, operation=AdminOperation.UPDATE_CONFIG
            )

        return ConfigurationCoordinator(
            core_config_target(self.config_principal_id),
            CORE_CONFIG_FIELDS,
            self._config_repository,
            self.secret_store,
            admission=self.lifecycle.admission,
            validate_admin_grant=validate,
            publish_config=lambda *_args, **_kwargs: (),
        )

    def stop_accepting(
        self, *, cancel_inflight: bool = False
    ) -> tuple[asyncio.Task[object], ...]:
        self._accepting = False
        flights = tuple(task for task in self._inflight if not task.done())
        if cancel_inflight:
            for task in flights:
                task.cancel()
        return flights

    async def wait_for_quiet(
        self, flights: tuple[asyncio.Task[object], ...], *, timeout: float
    ) -> bool:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError("timeout must be finite and non-negative")
        if not isfinite(float(timeout)) or timeout < 0:
            raise ValueError("timeout must be finite and non-negative")
        active = {task for task in flights if task is not asyncio.current_task()}
        if not active:
            return True
        done, pending = await asyncio.wait(active, timeout=float(timeout))
        for task in done:
            if task.cancelled():
                continue
            try:
                task.exception()
            except BaseException:
                pass
        return not pending

    def _require_accepting(self) -> None:
        if not self._accepting:
            raise AdminAuthorizationDenied from None

    async def _authorize(
        self,
        operation: AdminOperation,
        invocation: InvocationView | None,
        context: AdminAuthorizationContext | None,
    ) -> AdminAuthorizationGrant:
        self._require_accepting()
        if context is None:
            raise AdminAuthorizationDenied from None
        return await self.authorization.authorize(
            operation, invocation=invocation, context=context
        )

    async def _configuration(
        self, selection: _ManifestSelection
    ) -> ConfigurationCoordinator:
        target = ConfigTarget(
            self.config_principal_id,
            f"{selection.package_id}/{selection.manifest.module_id}",
        )

        async def validate(grant: AdminAuthorizationGrant) -> None:
            await self.authorization.validate_generation(
                grant, operation=AdminOperation.UPDATE_CONFIG
            )
            self._require_same_selection(selection)

        def publish_config(
            module_id: str,
            snapshot: ConfigSnapshot,
            *,
            changed_fields: frozenset[str],
        ) -> tuple[str, ...]:
            current = self.registry.snapshot().modules.get(module_id)
            if current is None:
                return ()
            if current.manifest is not selection.manifest:
                raise AdminAuthorizationDenied from None
            return self.health_resolver.publish_config(
                module_id, snapshot, changed_fields=changed_fields
            )

        return ConfigurationCoordinator(
            target,
            selection.manifest.config_fields,
            self._config_repository,
            self.secret_store,
            self.secret_store,
            admission=self.lifecycle.admission,
            validate_admin_grant=validate,
            publish_config=publish_config,
            subscription_gate_fields=self._subscription_gate_fields.get(target, ()),
            value_validators=self._module_config_validators.get(target.module_id),
        )

    def _selection(
        self, module_id: str, snapshot: RegistrySnapshot
    ) -> _ManifestSelection:
        registered = snapshot.modules.get(module_id)
        if registered is not None:
            package_id, local_id = module_id.split("/", 1)
            if registered.manifest.module_id != local_id:
                raise AdminAuthorizationDenied from None
            return _ManifestSelection(registered.manifest, package_id, None, registered)
        package_id, separator, local_id = module_id.partition("/")
        if not separator or not local_id:
            raise AdminAuthorizationDenied from None
        candidate = self.extension_runtime.candidate(package_id)
        if candidate is None or not isinstance(candidate.package, DiscoveredPackage):
            raise AdminAuthorizationDenied from None
        package = candidate.package
        manifest = package.manifest
        if (
            not package.valid
            or not isinstance(manifest, PackageManifest)
            or manifest.package_id != package_id
        ):
            raise AdminAuthorizationDenied from None
        selected = next(
            (item for item in manifest.modules if item.module_id == local_id), None
        )
        if selected is None:
            raise AdminAuthorizationDenied from None
        return _ManifestSelection(selected, package_id, candidate, None)

    def _require_same_selection(self, selection: _ManifestSelection) -> None:
        module_id = f"{selection.package_id}/{selection.manifest.module_id}"
        registered = self.registry.snapshot().modules.get(module_id)
        if selection.registered is not None:
            if registered is not selection.registered:
                raise AdminAuthorizationDenied from None
            return
        if registered is not None:
            if registered.manifest is not selection.manifest:
                raise AdminAuthorizationDenied from None
            return
        if (
            self.extension_runtime.candidate(selection.package_id)
            is not selection.candidate
        ):
            raise AdminAuthorizationDenied from None

    async def _snapshot(
        self,
        selection: _ManifestSelection,
        registered: RegisteredModule | None,
        *,
        allowed_fields: frozenset[str] | None = None,
    ) -> ModuleAdminSnapshot:
        module_id = f"{selection.package_id}/{selection.manifest.module_id}"
        target = ConfigTarget(self.config_principal_id, module_id)
        config = await self._config_repository.current(target)
        sensitive = {
            field.name
            for field in selection.manifest.config_fields
            if field.sensitive
            and (allowed_fields is None or field.name in allowed_fields)
        }
        metadata = {item.field: item for item in config.secret_metadata}
        values: dict[str, str] = {}
        for field in selection.manifest.config_fields:
            if allowed_fields is not None and field.name not in allowed_fields:
                continue
            if field.sensitive:
                receipt = metadata.get(field.name)
                if receipt is not None and receipt.state is SecretMetadataState.ACTIVE:
                    values[field.name] = "configured"
                elif receipt is None:
                    values[field.name] = "unset"
                else:
                    values[field.name] = "unavailable"
            elif field.name in config.values or field.default is not None:
                values[field.name] = "configured"
            else:
                values[field.name] = "unset"
        config_summary = ConfigSummary(
            module_id,
            config.revision,
            values,
            tuple(sorted(sensitive)),
        )

        if registered is None:
            status = ModuleStatus(
                module_id,
                False,
                ModuleLifecycle.DISCOVERED,
                ModuleHealth.DEGRADED,
                0,
                self.registry.snapshot().revision,
                "unregistered",
            )
            capabilities = tuple(
                CapabilitySummary(item.capability_id, False, "unregistered")
                for item in selection.manifest.capabilities
            )
            return ModuleAdminSnapshot(status, config_summary, capabilities)

        state = self.lifecycle.state(module_id)
        capability_rows: list[CapabilitySummary] = []
        health_rows = []
        for capability in selection.manifest.capabilities:
            health, _revision = self.health_resolver.current(
                module_id, capability.capability_id
            )
            health_rows.append(health)
            reason = self._reason_code(health.reason)
            capability_rows.append(
                CapabilitySummary(
                    capability.capability_id,
                    health.status.value == "available",
                    reason,
                )
            )
        status_health, reason_code = self._module_health(health_rows)
        status = ModuleStatus(
            module_id,
            registered.enabled,
            state.lifecycle,
            status_health,
            state.epoch,
            self.registry.snapshot().revision,
            state.reason_code or reason_code,
        )
        return ModuleAdminSnapshot(status, config_summary, tuple(capability_rows))

    @staticmethod
    def _reason_code(reason: str | None) -> str | None:
        if reason in ("configuration_missing", "needs_config"):
            return "needs_config"
        if reason in ("dependency_unavailable", "dependency_missing"):
            return "dependency_missing"
        if reason == "unregistered":
            return "unregistered"
        if reason in ("source_unavailable", "module_error", "operation_failed"):
            return "operation_failed"
        if reason in ("health_unknown", "configuration_unknown", "source_unknown"):
            return "health_unknown"
        return None

    @classmethod
    def _module_health(cls, health_rows) -> tuple[ModuleHealth, str | None]:
        if not health_rows:
            return ModuleHealth.DEGRADED, "health_unknown"
        reasons = {item.reason for item in health_rows if item.reason is not None}
        if "needs_config" in reasons or "configuration_missing" in reasons:
            return ModuleHealth.NEEDS_CONFIG, "needs_config"
        if "dependency_unavailable" in reasons:
            return ModuleHealth.DEPENDENCY_MISSING, "dependency_missing"
        if all(item.status.value == "available" for item in health_rows):
            return ModuleHealth.HEALTHY, None
        if "source_unavailable" in reasons:
            return ModuleHealth.CONNECTION_ERROR, "operation_failed"
        if "module_error" in reasons:
            return ModuleHealth.MODULE_ERROR, "operation_failed"
        return ModuleHealth.DEGRADED, "health_unknown"

    async def recover_discovered_configuration(self) -> tuple[AdminStartupFailure, ...]:
        """Recover receipts for all discovered manifests, including disabled ones."""
        failures: list[AdminStartupFailure] = []
        for candidate in self.extension_runtime.candidates():
            package = candidate.package
            manifest = package.manifest
            if not package.valid or not isinstance(manifest, PackageManifest):
                continue
            if package.package_id is None:
                continue
            for module in manifest.modules:
                module_id = f"{package.package_id}/{module.module_id}"
                selection = _ManifestSelection(
                    module, package.package_id, candidate, None
                )
                try:
                    coordinator = await self._configuration(selection)
                    if await coordinator.recover_manifest_secrets():
                        failures.append(
                            AdminStartupFailure(module_id, "operation_unavailable")
                        )
                except Exception:
                    failures.append(
                        AdminStartupFailure(module_id, "operation_unavailable")
                    )
        return tuple(failures)

    @_tracked_admin_request
    async def list_modules(
        self,
        invocation: InvocationView | None,
        *,
        authorization: AdminAuthorizationContext | None = None,
    ) -> tuple[ModuleAdminSnapshot, ...]:
        grant = await self._authorize(
            AdminOperation.LIST_MODULES, invocation, authorization
        )
        async with self.lifecycle.admission.mutation("admin-list-modules"):
            self._require_accepting()
            await self.authorization.validate_generation(
                grant, operation=AdminOperation.LIST_MODULES
            )
            registry_snapshot = self.registry.snapshot()
            selections: dict[str, _ManifestSelection] = {
                module_id: _ManifestSelection(
                    registered.manifest, module_id.split("/", 1)[0], None, registered
                )
                for module_id, registered in registry_snapshot.modules.items()
            }
            for candidate in self.extension_runtime.candidates():
                package = candidate.package
                manifest = package.manifest
                if not package.valid or not isinstance(manifest, PackageManifest):
                    continue
                for module in manifest.modules:
                    module_id = f"{package.package_id}/{module.module_id}"
                    selections.setdefault(
                        module_id,
                        _ManifestSelection(module, package.package_id, candidate, None),
                    )
            snapshots = tuple(
                [
                    await self._snapshot(
                        selection, registry_snapshot.modules.get(module_id)
                    )
                    for module_id, selection in sorted(selections.items())
                ]
            )
            await self.authorization.validate_generation(
                grant, operation=AdminOperation.LIST_MODULES
            )
            if self.registry.snapshot() is not registry_snapshot:
                raise RevisionConflict(
                    "registry",
                    registry_snapshot.revision,
                    self.registry.snapshot().revision,
                )
            for selection in selections.values():
                self._require_same_selection(selection)
            return snapshots

    @_tracked_admin_request
    async def config_snapshot(
        self,
        invocation: InvocationView | None,
        module_id: str,
        *,
        authorization: AdminAuthorizationContext | None = None,
    ) -> CoreConfigSummary:
        grant = await self._authorize(
            AdminOperation.MODULE_SNAPSHOT, invocation, authorization
        )
        if module_id != CORE_MODULE_ID:
            raise ValueError("config_snapshot requires the reserved Core target")
        async with self.lifecycle.admission.mutation("admin-core-config-snapshot"):
            self._require_accepting()
            await self.authorization.validate_generation(
                grant, operation=AdminOperation.MODULE_SNAPSHOT
            )
            snapshot = await self._config_repository.current(
                core_config_target(self.config_principal_id)
            )
            value = snapshot.values.get(DEFAULT_REGION.name, DEFAULT_REGION.default)
            valid = type(value) is str and value in {"cn", "global"}
            if snapshot.secret_metadata or set(snapshot.values) - {DEFAULT_REGION.name}:
                raise ValueError("Core config contains undeclared metadata")
            result = CoreConfigSummary(
                snapshot.revision,
                value if valid else None,
                DEFAULT_REGION.name in snapshot.values,
                "valid" if valid else "invalid",
            )
            await self.authorization.validate_generation(
                grant, operation=AdminOperation.MODULE_SNAPSHOT
            )
            self._require_accepting()
            return result

    @_tracked_admin_request
    async def module_snapshot(
        self,
        invocation: InvocationView | None,
        module_id: str,
        *,
        authorization: AdminAuthorizationContext | None = None,
    ) -> ModuleAdminSnapshot:
        grant = await self._authorize(
            AdminOperation.MODULE_SNAPSHOT, invocation, authorization
        )
        async with self.lifecycle.admission.mutation("admin-module-snapshot"):
            self._require_accepting()
            await self.authorization.validate_generation(
                grant, operation=AdminOperation.MODULE_SNAPSHOT
            )
            registry_snapshot = self.registry.snapshot()
            selection = self._selection(module_id, registry_snapshot)
            result = await self._snapshot(
                selection, registry_snapshot.modules.get(module_id)
            )
            await self.authorization.validate_generation(
                grant, operation=AdminOperation.MODULE_SNAPSHOT
            )
            if self.registry.snapshot() is not registry_snapshot:
                raise RevisionConflict(
                    "registry",
                    registry_snapshot.revision,
                    self.registry.snapshot().revision,
                )
            self._require_same_selection(selection)
            return result

    @_tracked_admin_request
    async def set_enabled(
        self,
        invocation: InvocationView | None,
        module_id: str,
        enabled: bool,
        *,
        expected_registry_revision: int,
        authorization: AdminAuthorizationContext | None = None,
    ) -> ModuleStatus:
        grant = await self._authorize(
            AdminOperation.SET_ENABLED, invocation, authorization
        )
        self._require_accepting()
        await self.authorization.validate_generation(
            grant, operation=AdminOperation.SET_ENABLED
        )
        result = await self.extension_runtime.set_enabled(
            invocation,
            module_id,
            enabled,
            expected_registry_revision=expected_registry_revision,
            authorization=authorization,
        )
        await self.authorization.validate_generation(
            grant, operation=AdminOperation.SET_ENABLED
        )
        return result

    @_tracked_admin_request
    async def update_config(
        self,
        invocation: InvocationView | None,
        module_id: str,
        patch: ConfigPatch,
        *,
        authorization: AdminAuthorizationContext | None = None,
    ) -> ConfigSummary:
        grant = await self.authorization.authorize(
            AdminOperation.UPDATE_CONFIG,
            invocation=invocation,
            context=authorization,
            resources={
                ConfigTarget(self.config_principal_id, module_id): {
                    update.field for update in patch.updates
                }
            },
        )
        self._require_accepting()
        from .admin_authorization import _grant_resource_fields

        allowed_fields = _grant_resource_fields(
            grant, ConfigTarget(self.config_principal_id, module_id)
        )
        if module_id == CORE_MODULE_ID:
            coordinator = self._core_configuration()
            updated = await coordinator.update_admin(
                core_config_target(self.config_principal_id), patch, grant
            )
            await self.authorization.validate_generation(
                grant, operation=AdminOperation.UPDATE_CONFIG
            )
            return ConfigSummary(
                CORE_MODULE_ID,
                updated.revision,
                {
                    field.name: "configured"
                    if field.name in updated.values
                    else "unset"
                    for field in CORE_CONFIG_FIELDS
                    if allowed_fields is None or field.name in allowed_fields
                },
            )
        if any(
            update.field in {CORE_DEFAULTS_FIELD, "default_region"}
            for update in patch.updates
        ):
            raise ValueError("module patch contains a reserved Core field")
        registry_snapshot = self.registry.snapshot()
        selection = self._selection(module_id, registry_snapshot)
        coordinator = await self._configuration(selection)
        updated = await coordinator.update_admin(
            ConfigTarget(self.config_principal_id, module_id), patch, grant
        )
        snapshot = await self._snapshot(
            selection,
            self.registry.snapshot().modules.get(module_id),
            allowed_fields=allowed_fields,
        )
        if updated.revision != snapshot.config.revision:
            raise RevisionConflict("config", updated.revision, snapshot.config.revision)
        self._require_same_selection(selection)
        await self.authorization.validate_generation(
            grant, operation=AdminOperation.UPDATE_CONFIG
        )
        return snapshot.config


__all__ = ["AdminOperationsService", "AdminStartupFailure"]
