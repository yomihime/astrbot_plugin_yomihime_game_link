"""In-memory, capability-level health projection for Core consumers.

The Lifecycle health report remains the source of instance health.  This
resolver adds only the readiness facts owned by Core (configuration metadata,
trusted source probes, and declared capability dependencies); it never
retains configuration values or performs I/O from the synchronous query path.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from yomihime_game_link_sdk.declarations import CapabilityReference, ModuleManifest
from yomihime_game_link_sdk.services import (
    CapabilityHealth,
    ConfigSnapshot,
    HealthStatus,
)
from yomihime_game_link_sdk.storage import SecretMetadataState

from ..core.contracts.administration import ModuleLifecycle
from ..core.contracts.validation_boundary import validate_contract
from .lifecycle import LifecycleController
from .ports import AdmissionPort, RunIdentity

ConfigSnapshotLoader = Callable[[str], Awaitable[ConfigSnapshot]]
SourceHealthProvider = Callable[[str, str], Awaitable[CapabilityHealth]]

_UNKNOWN_CONFIG = validate_contract(
    CapabilityHealth(HealthStatus.UNKNOWN, "configuration_unknown")
)
_MISSING_CONFIG = validate_contract(
    CapabilityHealth(HealthStatus.UNAVAILABLE, "configuration_missing")
)
_UNKNOWN_SOURCE = validate_contract(
    CapabilityHealth(HealthStatus.UNKNOWN, "source_unknown")
)
_UNKNOWN_DEPENDENCY = validate_contract(
    CapabilityHealth(HealthStatus.UNKNOWN, "dependency_unknown")
)
_UNAVAILABLE_DEPENDENCY = validate_contract(
    CapabilityHealth(HealthStatus.UNAVAILABLE, "dependency_unavailable")
)


@dataclass(slots=True)
class _ConfigReadiness:
    revision: int
    fields: dict[str, CapabilityHealth]


@dataclass(slots=True)
class _RunHealth:
    identity: RunIdentity
    manifest: ModuleManifest
    sources: dict[str, CapabilityHealth]
    generations: dict[str, int]
    revisions: dict[str, int]
    last_health: dict[str, CapabilityHealth] = field(default_factory=dict)


class HealthResolver:
    """Publish and synchronously resolve health for a single CoreRuntime."""

    def __init__(
        self,
        *,
        config_snapshot: ConfigSnapshotLoader,
        source_health: SourceHealthProvider | None = None,
    ) -> None:
        if not callable(config_snapshot):
            raise TypeError("config_snapshot must be callable")
        if source_health is not None and not callable(source_health):
            raise TypeError("source_health must be callable")
        self._config_snapshot = config_snapshot
        self._source_health = source_health
        self._lifecycle: LifecycleController | None = None
        self._admission: AdmissionPort | None = None
        self._configs: dict[str, _ConfigReadiness] = {}
        self._runs: dict[str, _RunHealth] = {}
        self._source_generations: dict[tuple[str, str], int] = {}

    def bind_runtime(
        self, lifecycle: LifecycleController, admission: AdmissionPort
    ) -> None:
        if not isinstance(lifecycle, LifecycleController):
            raise TypeError("health resolver requires a LifecycleController")
        if not callable(getattr(admission, "mutation", None)) or not callable(
            getattr(admission, "check", None)
        ):
            raise TypeError("health resolver requires an AdmissionPort")
        if lifecycle.admission is not admission:
            raise ValueError("health resolver must bind the Lifecycle Admission")
        if self._lifecycle is not None or self._admission is not None:
            raise RuntimeError("health resolver is already bound")
        self._lifecycle = lifecycle
        self._admission = admission

    async def prepare(self, module_id: str, manifest: ModuleManifest) -> None:
        """Prepare readiness for the exact STARTING run before gate opening."""

        validate_contract(manifest)
        lifecycle, admission = self._runtime()
        if not isinstance(manifest, ModuleManifest):
            raise TypeError("manifest must be a ModuleManifest")
        module = self._registered_manifest(module_id)
        if module != manifest:
            raise ValueError("health manifest does not match the registered module")
        state = lifecycle.state(module_id)
        identity = state.identity
        if (
            identity is None
            or state.lifecycle is not ModuleLifecycle.STARTING
            or state.health_report is None
        ):
            raise RuntimeError("health can be prepared only for a STARTING run")

        # Reserve the source generations before any await. A newer explicit
        # refresh can then supersede this preparation without being overwritten.
        async with admission.mutation(f"health-prepare:{module_id}"):
            self._require_run(lifecycle, module_id, identity, starting=True)
            config_revision_at_start = (
                self._configs[module_id].revision
                if module_id in self._configs
                else None
            )
            previous = self._runs.get(module_id)
            if previous is not None and previous.identity is identity:
                raise RuntimeError("health run is already prepared")
            generations: dict[str, int] = {}
            sources: dict[str, CapabilityHealth] = {}
            for source in manifest.sources:
                key = (module_id, source.source_id)
                generation = self._source_generations.get(key, 0) + 1
                self._source_generations[key] = generation
                generations[source.source_id] = generation
                sources[source.source_id] = _UNKNOWN_SOURCE
            revisions = {
                capability.capability_id: max(
                    0,
                    int(
                        (state.health_revisions or {}).get(capability.capability_id, 0)
                    ),
                )
                for capability in manifest.capabilities
            }
            run = _RunHealth(identity, manifest, sources, generations, revisions)
            self._runs[module_id] = run

        config_task = self._load_config(module_id, manifest)
        source_tasks = {
            source.source_id: self._load_source(module_id, source.source_id)
            for source in manifest.sources
        }
        config_result, source_results = await asyncio.gather(
            config_task,
            asyncio.gather(*source_tasks.values()) if source_tasks else _empty_tuple(),
        )
        source_values = dict(zip(source_tasks, source_results, strict=True))

        async with admission.mutation(f"health-publish-prepare:{module_id}"):
            if not self._same_run(lifecycle, module_id, identity, starting=True):
                return
            current = self._runs.get(module_id)
            if current is not run:
                return
            if config_result is not None:
                known = self._configs.get(module_id)
                if known is None or config_result.revision >= known.revision:
                    self._configs[module_id] = self._readiness(
                        module_id, manifest, config_result
                    )
            elif manifest.config_fields:
                known = self._configs.get(module_id)
                if known is None or known.revision == config_revision_at_start:
                    self._configs[module_id] = _ConfigReadiness(
                        1 if known is None else known.revision,
                        {
                            field.name: _UNKNOWN_CONFIG
                            for field in manifest.config_fields
                        },
                    )
            for source_id, health in source_values.items():
                key = (module_id, source_id)
                if self._source_generations.get(key) != generations[source_id]:
                    continue
                current.sources[source_id] = health
            self._prime_health(module_id, current, state.health_report.capabilities)

    def publish_config(
        self,
        module_id: str,
        snapshot: ConfigSnapshot,
        *,
        changed_fields: frozenset[str],
    ) -> tuple[str, ...]:
        """Publish a metadata-only configuration snapshot while holding mutation."""

        validate_contract(snapshot)
        self._runtime()
        manifest = self._registered_manifest(module_id)
        if not isinstance(snapshot, ConfigSnapshot):
            raise TypeError("snapshot must be a ConfigSnapshot")
        if snapshot.target is None or snapshot.target.module_id != module_id:
            raise ValueError("configuration target does not match module")
        if not isinstance(changed_fields, frozenset) or any(
            type(field) is not str for field in changed_fields
        ):
            raise TypeError("changed_fields must be a frozenset of field names")
        declared = {field.name for field in manifest.config_fields}
        if not changed_fields <= declared:
            raise ValueError("configuration update contains an undeclared field")
        previous = self._configs.get(module_id)
        if previous is not None and (
            snapshot.revision < previous.revision
            or (changed_fields and snapshot.revision == previous.revision)
        ):
            raise ValueError("configuration snapshot revision is stale")
        readiness = self._readiness(module_id, manifest, snapshot)
        if previous is not None:
            # Preserve readiness for untouched fields. Canonical patches give
            # this method exactly the non-KEEP fields whose values may change.
            readiness.fields = {
                **previous.fields,
                **{
                    name: value
                    for name, value in readiness.fields.items()
                    if name in changed_fields
                },
            }
        self._configs[module_id] = readiness
        run = self._runs.get(module_id)
        affected: tuple[str, ...] = ()
        if run is not None:
            affected = tuple(
                capability.capability_id
                for capability in manifest.capabilities
                if changed_fields.intersection(capability.required_config)
            )
            for capability_id in affected:
                run.revisions[capability_id] = run.revisions.get(capability_id, 0) + 1
                run.last_health[capability_id] = self._effective(
                    module_id, capability_id, (), override=None
                )
        return affected

    async def refresh_source(self, module_id: str, source_id: str) -> bool:
        """Refresh one declared source without allowing stale probes to publish."""

        lifecycle, admission = self._runtime()
        manifest = self._registered_manifest(module_id)
        if not any(source.source_id == source_id for source in manifest.sources):
            return False
        state = lifecycle.state(module_id)
        identity = state.identity
        if identity is None or state.lifecycle not in (
            ModuleLifecycle.STARTING,
            ModuleLifecycle.ACTIVE,
        ):
            return False
        async with admission.mutation(f"health-source-reserve:{module_id}:{source_id}"):
            if not self._same_run(lifecycle, module_id, identity, starting=True):
                return False
            key = (module_id, source_id)
            generation = self._source_generations.get(key, 0) + 1
            self._source_generations[key] = generation
            run = self._runs.get(module_id)
            if run is None or run.identity is not identity:
                return False
            run.generations[source_id] = generation
        health = await self._load_source(module_id, source_id)
        async with admission.mutation(f"health-source-publish:{module_id}:{source_id}"):
            if not self._same_run(lifecycle, module_id, identity, starting=True):
                return False
            if self._source_generations.get((module_id, source_id)) != generation:
                return False
            run = self._runs.get(module_id)
            if run is None or run.identity is not identity:
                return False
            previous = run.sources.get(source_id, _UNKNOWN_SOURCE)
            run.sources[source_id] = health
            if previous != health:
                for capability in manifest.capabilities:
                    if source_id in capability.required_sources:
                        capability_id = capability.capability_id
                        run.revisions[capability_id] = (
                            run.revisions.get(capability_id, 0) + 1
                        )
                        run.last_health[capability_id] = self._effective(
                            module_id, capability_id, (), override=None
                        )
            return True

    def query(
        self,
        module_id: str,
        capability_id: str,
        lifecycle_health: CapabilityHealth,
        lifecycle_revision: int,
    ) -> tuple[CapabilityHealth, int]:
        """Synchronous in-memory projection consumed by Admission/Lifecycle."""

        validate_contract(lifecycle_health)
        lifecycle, _ = self._runtime()
        if not isinstance(lifecycle_health, CapabilityHealth):
            raise TypeError("lifecycle health must be CapabilityHealth")
        if type(lifecycle_revision) is not int or lifecycle_revision < 0:
            raise ValueError("lifecycle revision must be non-negative")
        run = self._runs.get(module_id)
        health = self._effective(
            module_id,
            capability_id,
            (),
            override=(lifecycle_health, lifecycle_revision),
        )
        if run is None or run.identity is not lifecycle.current_identity(module_id):
            return health, lifecycle_revision
        revision = max(
            run.revisions.get(capability_id, lifecycle_revision), lifecycle_revision
        )
        previous = run.last_health.get(capability_id)
        if previous is None:
            run.last_health[capability_id] = health
        elif previous != health:
            revision += 1
            run.revisions[capability_id] = revision
            run.last_health[capability_id] = health
        else:
            run.revisions[capability_id] = revision
        return health, revision

    def current(
        self, module_id: str, capability_id: str
    ) -> tuple[CapabilityHealth, int]:
        """Read effective current health for Help/Admin projections."""

        lifecycle, _ = self._runtime()
        state = lifecycle.state(module_id)
        report = state.health_report
        if report is None:
            return validate_contract(
                CapabilityHealth(HealthStatus.UNKNOWN, "health_not_prepared")
            ), 0
        health = report.capabilities.get(capability_id)
        revision = (state.health_revisions or {}).get(capability_id)
        if health is None or revision is None:
            return validate_contract(
                CapabilityHealth(HealthStatus.UNKNOWN, "health_not_declared")
            ), 0
        return self.query(module_id, capability_id, health, revision)

    async def _load_config(
        self, module_id: str, manifest: ModuleManifest
    ) -> ConfigSnapshot | None:
        validate_contract(manifest)
        if not manifest.config_fields:
            return None
        try:
            snapshot = await self._config_snapshot(module_id)
            if (
                not isinstance(snapshot, ConfigSnapshot)
                or snapshot.target is None
                or snapshot.target.module_id != module_id
            ):
                return None
            return snapshot
        except asyncio.CancelledError:
            raise
        except Exception:
            return None

    async def _load_source(self, module_id: str, source_id: str) -> CapabilityHealth:
        provider = self._source_health
        if provider is None:
            return _UNKNOWN_SOURCE
        try:
            value = await provider(module_id, source_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            return _UNKNOWN_SOURCE
        if not isinstance(value, CapabilityHealth):
            return _UNKNOWN_SOURCE
        if value.status is HealthStatus.AVAILABLE:
            return validate_contract(CapabilityHealth(HealthStatus.AVAILABLE))
        if value.status is HealthStatus.UNAVAILABLE:
            return validate_contract(
                CapabilityHealth(HealthStatus.UNAVAILABLE, "source_unavailable")
            )
        return _UNKNOWN_SOURCE

    def _readiness(
        self, module_id: str, manifest: ModuleManifest, snapshot: ConfigSnapshot
    ) -> _ConfigReadiness:
        validate_contract(manifest)
        validate_contract(snapshot)
        declarations = {field.name: field for field in manifest.config_fields}
        fields: dict[str, CapabilityHealth] = {}
        target = snapshot.target
        if target is None or target.module_id != module_id:
            # ConfigTarget is optional in the DTO for compatibility; readiness
            # consumers still require the real host-bound target.
            return _ConfigReadiness(
                snapshot.revision,
                {name: _UNKNOWN_CONFIG for name in declarations},
            )
        metadata = {item.field: item for item in snapshot.secret_metadata}
        for name, declaration in declarations.items():
            if not declaration.sensitive:
                fields[name] = (
                    validate_contract(CapabilityHealth(HealthStatus.AVAILABLE))
                    if name in snapshot.values
                    else _MISSING_CONFIG
                )
                continue
            item = metadata.get(name)
            reference = None if item is None else item.secret_ref
            if (
                item is None
                or item.state is not SecretMetadataState.ACTIVE
                or reference is None
                or reference.field != name
                or reference.module_id != target.module_id
                or reference.principal_id != target.principal_id
                or not reference.token
            ):
                fields[name] = _MISSING_CONFIG
            else:
                # This records configured metadata only; it does not claim
                # the secret can be decrypted or the provider accepts it.
                fields[name] = validate_contract(
                    CapabilityHealth(HealthStatus.AVAILABLE)
                )
        return _ConfigReadiness(snapshot.revision, fields)

    def _effective(
        self,
        module_id: str,
        capability_id: str,
        stack: tuple[tuple[str, str], ...],
        *,
        override: tuple[CapabilityHealth, int] | None,
    ) -> CapabilityHealth:
        validate_contract(override)
        key = (module_id, capability_id)
        if key in stack:
            return _UNAVAILABLE_DEPENDENCY
        lifecycle, _ = self._runtime()
        try:
            registered = lifecycle.registry.snapshot().module(module_id)
        except Exception:
            return _UNAVAILABLE_DEPENDENCY
        descriptor = next(
            (
                item
                for item in registered.manifest.capabilities
                if item.capability_id == capability_id
            ),
            None,
        )
        if descriptor is None:
            return _UNAVAILABLE_DEPENDENCY
        state = lifecycle.state(module_id)
        if not registered.enabled or state.lifecycle is not ModuleLifecycle.ACTIVE:
            return validate_contract(
                CapabilityHealth(HealthStatus.UNAVAILABLE, "module_unavailable")
            )
        if state.identity is None or state.identity.module_epoch != registered.epoch:
            return validate_contract(
                CapabilityHealth(HealthStatus.UNAVAILABLE, "module_unavailable")
            )
        if module_id == key[0] and override is not None:
            base = override[0]
        else:
            report = state.health_report
            base = (
                report.capabilities.get(capability_id) if report is not None else None
            )
        if not isinstance(base, CapabilityHealth):
            return validate_contract(
                CapabilityHealth(HealthStatus.UNKNOWN, "health_unknown")
            )
        if base.status is not HealthStatus.AVAILABLE:
            return base

        config = self._configs.get(module_id)
        config_results: list[CapabilityHealth] = []
        for name in descriptor.required_config:
            config_results.append(
                _UNKNOWN_CONFIG
                if config is None
                else config.fields.get(name, _MISSING_CONFIG)
            )
        source_state = self._runs.get(module_id)
        source_results = [
            _UNKNOWN_SOURCE
            if source_state is None or source_state.identity is not state.identity
            else source_state.sources.get(source_id, _UNKNOWN_SOURCE)
            for source_id in descriptor.required_sources
        ]
        for value in (*config_results, *source_results):
            if value.status is HealthStatus.UNAVAILABLE:
                return value
        if any(
            value.status is HealthStatus.UNKNOWN
            for value in (*config_results, *source_results)
        ):
            return (
                _UNKNOWN_CONFIG
                if any(value.status is HealthStatus.UNKNOWN for value in config_results)
                else _UNKNOWN_SOURCE
            )

        next_stack = stack + (key,)
        for reference in descriptor.required_capabilities:
            if isinstance(reference, CapabilityReference):
                target_module, target_capability = (
                    reference.module_id,
                    reference.capability_id,
                )
            else:
                target_module, target_capability = module_id, reference
            dependency = self._effective(
                target_module, target_capability, next_stack, override=None
            )
            if dependency.status is HealthStatus.UNAVAILABLE:
                return _UNAVAILABLE_DEPENDENCY
            if dependency.status is HealthStatus.UNKNOWN:
                return _UNKNOWN_DEPENDENCY
        return validate_contract(CapabilityHealth(HealthStatus.AVAILABLE))

    def _prime_health(
        self,
        module_id: str,
        run: _RunHealth,
        health: object,
    ) -> None:
        if not hasattr(health, "items"):
            return
        for capability_id, base in health.items():
            if isinstance(base, CapabilityHealth):
                run.last_health[capability_id] = self._effective(
                    module_id, capability_id, (), override=(base, 0)
                )

    def _registered_manifest(self, module_id: str) -> ModuleManifest:
        lifecycle, _ = self._runtime()
        try:
            return lifecycle.registry.snapshot().module(module_id).manifest
        except Exception:
            raise ValueError("module is not registered") from None

    @staticmethod
    def _same_run(
        lifecycle: LifecycleController,
        module_id: str,
        identity: RunIdentity,
        *,
        starting: bool,
    ) -> bool:
        try:
            state = lifecycle.state(module_id)
        except Exception:
            return False
        states = (
            (ModuleLifecycle.STARTING, ModuleLifecycle.ACTIVE)
            if starting
            else (ModuleLifecycle.ACTIVE,)
        )
        return state.identity is identity and state.lifecycle in states

    @classmethod
    def _require_run(
        cls,
        lifecycle: LifecycleController,
        module_id: str,
        identity: RunIdentity,
        *,
        starting: bool,
    ) -> None:
        if not cls._same_run(lifecycle, module_id, identity, starting=starting):
            raise RuntimeError("module run changed during health preparation")

    def _runtime(self) -> tuple[LifecycleController, AdmissionPort]:
        if self._lifecycle is None or self._admission is None:
            raise RuntimeError("health resolver is not bound to a CoreRuntime")
        return self._lifecycle, self._admission


async def _empty_tuple() -> tuple[()]:
    return ()


__all__ = [
    "ConfigSnapshotLoader",
    "HealthResolver",
    "SourceHealthProvider",
]
