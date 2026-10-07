"""Host-independent composition root for one Yomihime runtime."""

from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite
from pathlib import Path
from time import monotonic
from types import MappingProxyType
from typing import Protocol

from ..api.administration import AdminAuthorizationDenied, AdminOperation
from ..api.contexts import InvocationOrigin, InvocationView
from ..api.display import DisplayLimits, DisplayRenderer, Privacy
from ..api.manifests import (
    CapabilityEffect,
    CommandDescriptor,
    ModuleManifest,
    PrivacyFloor,
    ToolDescriptor,
)
from ..api.results import CapabilityResult
from ..api.services import CapabilityHealth, ConfigTarget
from ..api.storage import GrantReference
from ..api.subscriptions import CadenceConfiguration, ConversationKind, ConversationRef
from ..core.context_issuer import ContextIssuer
from ..core.health import HealthResolver
from ..core.invocation import Gateway
from ..core.lifecycle import LifecycleController
from ..core.policy import tool_allowed
from ..core.ports import (
    PublicWebProofValidator,
    SubscriptionGateBinding,
    SubscriptionGateState,
)
from ..core.registry import RegisteredModule, Registry
from ..extensions.discovery import DiscoveredPackage
from ..extensions.loader import ExtensionCandidate
from ..extensions.source_snapshot import PackageProvenance
from ..infrastructure.files import LocalSafeFileStore
from ..infrastructure.secret_store import SecretCodec, SQLiteSecretStore
from ..infrastructure.sqlite.database import SQLiteDatabase
from ..infrastructure.sqlite.repositories import SQLiteRepositories
from ..infrastructure.sqlite.repositories_admin_credentials import (
    SQLiteAdminCredentialRepository,
)
from ..infrastructure.sqlite.repositories_config import SQLiteConfigRepository
from ..infrastructure.sqlite.repositories_config_migration import (
    SQLiteOrdinaryConfigurationMigrationRepository,
)
from ..infrastructure.sqlite.repositories_output import SQLiteRootOutputRepository
from ..infrastructure.sqlite.repositories_runtime import SQLiteModuleRuntimeRepository
from ..services.admin_authorization import AdminAuthorizationService, ContextValidator
from ..services.admin_facade import AdminFacade
from ..services.admin_operations import (
    AdminOperationsService,
    AdminStartupFailure,
)
from ..services.b04_runtime import B04Repositories, SQLiteResourceVisibilityProbe
from ..services.delivery import DeliveryService
from ..services.extension_runtime import (
    ExtensionRestoreFailure,
    ExtensionRuntime,
    FactorySourcePort,
)
from ..services.identity import (
    InvocationPrincipalResolver,
    TrustedIngressPrincipalProvisioner,
    TrustedRoutePublisher,
)
from ..services.module_catalog import project_module_catalog
from ..services.module_services import (
    ModuleServicesFactory,
    RegistryRegistrationLookup,
    _freeze_host_config_snapshots,
)
from ..services.module_storage import ModuleStorageRouter
from ..services.output import (
    LifecycleApprovedSendScheduler,
    OutputResult,
    OutputService,
    OutputStatus,
)
from ..services.owner_authority import OwnerRouteProofAuthority
from ..services.scheduler import (
    ExecutionClaimProofRegistry,
    RescheduleConfiguration,
    SchedulerQuotas,
    SharedCollectionScheduler,
)
from ..services.source_credentials import SourceCredentialPolicy
from ..services.subscriptions import SubscriptionOperationsService
from .configuration import ConfigurationCoordinator
from .configuration_migration import OrdinaryConfigurationMigration
from .core_configuration import CORE_CONFIG_FIELDS, CoreDefaultsView, core_config_target

DEFAULT_CADENCE_CONFIGURATION = CadenceConfiguration((60.0, 300.0, 900.0, 3600.0))
DEFAULT_CADENCE_REVISION = 1
DEFAULT_SCHEDULER_QUOTAS = SchedulerQuotas(4, 2, 2, 20, 10.0, 1.0, 30)
DEFAULT_RESCHEDULE_CONFIGURATION = RescheduleConfiguration(120.0, 0.05, 0.1, 0.0)
_MODULE_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*\Z")


class HostIngressValidator(Protocol):
    def __call__(
        self, origin: InvocationOrigin, ingress: "HostIngress"
    ) -> bool | Awaitable[bool]: ...


@dataclass(frozen=True, slots=True)
class TrustedSubscriptionGate:
    """Immutable host-composition declaration from one sealed bundle scan."""

    manifest: ModuleManifest
    field: str
    manifest_sha256: bytes


@dataclass(frozen=True, slots=True)
class HostIngress:
    """Explicit host request facts plus opaque evidence checked by the host."""

    adapter_id: str
    actor_id: str
    conversation_id: str
    delivery_route: str
    conversation_kind: ConversationKind
    evidence: object
    grant_reference: GrantReference | None = None

    def __post_init__(self) -> None:
        ConversationRef(
            self.adapter_id,
            self.conversation_kind,
            self.conversation_id,
            self.delivery_route,
        )
        if type(self.actor_id) is not str or not self.actor_id.strip():
            raise ValueError("host actor identity is required")
        if self.evidence is None:
            raise ValueError("host ingress evidence is required")
        if self.grant_reference is not None and not isinstance(
            self.grant_reference, GrantReference
        ):
            raise TypeError("grant_reference must be a GrantReference")


@dataclass(frozen=True, slots=True)
class CoreInvocationOutcome:
    result: object
    output: object


@dataclass(frozen=True, slots=True)
class CoreRuntimeFailure:
    component: str
    key: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class CoreStartupReport:
    migration_version: int
    expired_pending_logins: int
    recovered_root_outputs: int
    recovered_delivery_events: int
    recovered_digest_envelopes: int
    file_recovery_items: tuple[str, ...]
    config_failures: tuple[AdminStartupFailure, ...]
    module_statuses: tuple[object, ...]
    extension_failures: tuple[ExtensionRestoreFailure, ...]


class CoreRuntimeCleanupPending(RuntimeError):
    """Shutdown did not reach quiet; owner resources remain available to retry."""

    code = "cleanup_pending"

    def __init__(self, component: str) -> None:
        self.component = component
        super().__init__(f"CoreRuntime cleanup remains pending: {component}")


@dataclass(frozen=True, slots=True)
class DeploymentCadencePolicy:
    """Selected deployment cadence with a stable positive configuration revision."""

    configuration: CadenceConfiguration = DEFAULT_CADENCE_CONFIGURATION
    revision: int = DEFAULT_CADENCE_REVISION

    def __post_init__(self) -> None:
        if not isinstance(self.configuration, CadenceConfiguration):
            raise TypeError("configuration must be CadenceConfiguration")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("cadence revision must be positive")

    def resolve(self, module_id: str, schedule) -> tuple[float, int]:
        del module_id
        minimum = float(schedule.minimum_interval_seconds)
        requested = float(
            schedule.default_interval_seconds
            if schedule.default_interval_seconds is not None
            else minimum
        )
        target = max(minimum, requested)
        available = tuple(
            value for value in self.configuration.allowed_seconds if value >= target
        )
        if not available:
            raise ValueError("no configured cadence satisfies the module schedule")
        return self.configuration.validate_target(available[0]), self.revision


class _CoreConversationResolver:
    def __init__(self, issuer: ContextIssuer, conversations) -> None:
        self._issuer = issuer
        self._conversations = conversations

    async def resolve(self, invocation: InvocationView) -> ConversationRef | None:
        self._issuer.require(invocation)
        if invocation.adapter_id is None or invocation.conversation_id is None:
            return None
        current = await self._conversations.current(
            invocation.adapter_id, invocation.conversation_id
        )
        if (
            current is None
            or current.adapter_id != invocation.adapter_id
            or current.conversation_id != invocation.conversation_id
        ):
            return None
        return current


class _CorePersistedRouteResolver:
    def __init__(self, conversations) -> None:
        self._conversations = conversations

    async def resolve_current(
        self, owner_id: str, persisted_recipient: ConversationRef
    ) -> ConversationRef | None:
        del owner_id
        current = await self._conversations.current(
            persisted_recipient.adapter_id, persisted_recipient.conversation_id
        )
        return current if current == persisted_recipient else None

    async def resolve_private(
        self, owner_id: str, persisted_recipient: ConversationRef
    ) -> ConversationRef | None:
        current = await self.resolve_current(owner_id, persisted_recipient)
        if current is None or current.kind is not ConversationKind.DIRECT:
            return None
        return current


class CoreRuntime:
    """Compose and own every host-independent service for one runtime instance."""

    def __init__(
        self,
        *,
        database: SQLiteDatabase | str | Path,
        extension_root: str | Path,
        file_root: str | Path,
        secret_root: str | Path,
        secret_codec: SecretCodec | None,
        http_transport: object,
        renderer: DisplayRenderer,
        display_limits: DisplayLimits,
        message_port,
        admin_context_validator: ContextValidator,
        host_ingress_validator: HostIngressValidator,
        config_principal_id: str,
        identity_namespace: str,
        trusted_bundled_defaults: Mapping[str, tuple[str, ...]] | None = None,
        trusted_bundled_manifests: Mapping[str, ModuleManifest] | None = None,
        trusted_subscription_gates: Mapping[str, TrustedSubscriptionGate] | None = None,
        module_host_config_snapshots: Mapping[str, Mapping[str, object]] | None = None,
        public_web_validator: PublicWebProofValidator | None = None,
        public_web_capabilities: frozenset[tuple[str, str]] = frozenset(),
        source_credential_policies: Sequence[SourceCredentialPolicy] = (),
        factory_source: FactorySourcePort | None = None,
        source_health: Callable[[str, str], Awaitable[CapabilityHealth]] | None = None,
        utc_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic_clock: Callable[[], float] = monotonic,
        cadence_configuration: CadenceConfiguration = DEFAULT_CADENCE_CONFIGURATION,
        cadence_revision: int = DEFAULT_CADENCE_REVISION,
        scheduler_quotas: SchedulerQuotas = DEFAULT_SCHEDULER_QUOTAS,
        reschedule_configuration: RescheduleConfiguration = DEFAULT_RESCHEDULE_CONFIGURATION,
        max_subscriptions_per_owner: int = 100,
        handler_timeout: float = 30.0,
        send_timeout: float = 30.0,
        claim_lease: timedelta = timedelta(minutes=2),
        pump_interval: float = 5.0,
        cleanup_timeout: float = 5.0,
        legacy_core_defaults: Mapping[str, object] | None = None,
        module_config_validators: Mapping[str, Mapping[str, object]] | None = None,
        ordinary_migration_fields: tuple = (),
        ordinary_migration_source: Callable[[], Mapping[str, object]] | None = None,
        ordinary_migration_id: str = "ordinary-defaults-v1",
    ) -> None:
        if not isinstance(database, SQLiteDatabase):
            database = SQLiteDatabase(database)
        if type(config_principal_id) is not str or not config_principal_id.strip():
            raise ValueError("config_principal_id is required")
        if type(identity_namespace) is not str or not identity_namespace.strip():
            raise ValueError("identity_namespace is required")
        bundled_defaults = _normalize_bundled_defaults(trusted_bundled_defaults)
        bundled_manifests = _normalize_bundled_manifests(trusted_bundled_manifests)
        subscription_gates = _normalize_subscription_gates(
            trusted_subscription_gates, bundled_manifests
        )
        gate_fields = MappingProxyType(
            {
                ConfigTarget(config_principal_id, module_id): (gate.field,)
                for module_id, gate in subscription_gates.items()
            }
        )
        host_config_snapshots = _freeze_host_config_snapshots(
            module_host_config_snapshots
        )
        for name, callback in (
            ("admin_context_validator", admin_context_validator),
            ("host_ingress_validator", host_ingress_validator),
            ("utc_clock", utc_clock),
            ("monotonic_clock", monotonic_clock),
        ):
            if not callable(callback):
                raise TypeError(f"{name} must be callable")
        if source_health is not None and not callable(source_health):
            raise TypeError("source_health must be callable")
        if not isinstance(display_limits, DisplayLimits):
            raise TypeError("display_limits must be DisplayLimits")
        if not callable(getattr(renderer, "render", None)):
            raise TypeError("renderer must implement DisplayRenderer")
        if not callable(getattr(message_port, "send", None)):
            raise TypeError("message_port must implement MessagePort")
        _positive_finite(handler_timeout, "handler_timeout")
        _positive_finite(send_timeout, "send_timeout")
        _positive_finite(pump_interval, "pump_interval")
        _positive_finite(cleanup_timeout, "cleanup_timeout")
        if not isinstance(claim_lease, timedelta) or claim_lease <= timedelta(0):
            raise ValueError("claim_lease must be positive")
        if (
            type(max_subscriptions_per_owner) is not int
            or max_subscriptions_per_owner < 1
        ):
            raise ValueError("max_subscriptions_per_owner must be positive")
        cadence_policy = DeploymentCadencePolicy(
            cadence_configuration, cadence_revision
        )

        self.database = database
        self.module_host_config_snapshots = host_config_snapshots
        self.extension_root = Path(extension_root)
        self._trusted_bundled_defaults = bundled_defaults
        self._trusted_bundled_manifests = bundled_manifests
        self._trusted_subscription_gates = subscription_gates
        self.module_storage = ModuleStorageRouter(Path(file_root).parent / "modules")
        self.file_store = LocalSafeFileStore(file_root)
        self.secret_store = SQLiteSecretStore(database, secret_root, codec=secret_codec)
        self.registry = Registry()
        self.issuer = ContextIssuer(
            clock=monotonic_clock,
            public_web_validator=public_web_validator,
            public_web_capabilities=public_web_capabilities,
        )
        self.execution_claim_proofs = ExecutionClaimProofRegistry(now=utc_clock)
        lookup = RegistryRegistrationLookup(self.registry)
        self.repositories = SQLiteRepositories(
            database,
            lookup,
            file_store=self.file_store,
            secret_store=self.secret_store,
            subscription_gate_fields=gate_fields,
            config_clock=utc_clock,
        )
        execution_gate_bindings = MappingProxyType(
            {
                module_id: SubscriptionGateBinding(
                    ConfigTarget(config_principal_id, module_id),
                    gate.field,
                    *module_id.split("/", 1),
                )
                for module_id, gate in subscription_gates.items()
            }
        )
        self.b04_repositories = B04Repositories(
            database,
            utc_clock=utc_clock,
            subscription_gate_bindings=execution_gate_bindings,
        )
        self.config_repository: SQLiteConfigRepository = self.repositories.config
        self.ordinary_config_migration = (
            OrdinaryConfigurationMigration(
                SQLiteOrdinaryConfigurationMigrationRepository(self.config_repository),
                ordinary_migration_fields,
                migration_id=ordinary_migration_id,
                value_validators=module_config_validators or {},
            )
            if ordinary_migration_fields
            else None
        )
        self._ordinary_migration_source = ordinary_migration_source
        self.runtime_repository = SQLiteModuleRuntimeRepository(database)
        self.admin_credential_repository = SQLiteAdminCredentialRepository(database)

        async def load_config(module_id: str):
            return await self.config_repository.current(
                ConfigTarget(config_principal_id, module_id)
            )

        self.health_resolver = HealthResolver(
            config_snapshot=load_config, source_health=source_health
        )
        self.lifecycle = LifecycleController(
            self.registry,
            issuer=self.issuer,
            clock=monotonic_clock,
            utc_clock=utc_clock,
            capability_health_query=self.health_resolver.query,
            execution_claim_prover=self.execution_claim_proofs,
        )
        self.health_resolver.bind_runtime(self.lifecycle, self.lifecycle.admission)
        self.principal_resolver = InvocationPrincipalResolver(
            self.issuer,
            self.repositories.identities,
            identity_namespace=identity_namespace,
            admission=self.lifecycle.admission,
        )
        self._ingress_principal_provisioner = TrustedIngressPrincipalProvisioner(
            self.repositories.identities,
            identity_namespace=identity_namespace,
        )
        self._conversation_resolver = _CoreConversationResolver(
            self.issuer, self.repositories.conversations
        )
        self.owner_authority = OwnerRouteProofAuthority(
            self.issuer,
            self.lifecycle.admission,
            self.principal_resolver,
            self._conversation_resolver,
        )
        self._persisted_routes = _CorePersistedRouteResolver(
            self.repositories.conversations
        )
        self.subscription_operations = SubscriptionOperationsService(
            issuer=self.issuer,
            admission=self.lifecycle.admission,
            registry=self.registry,
            resolver=self._conversation_resolver,
            cadence=cadence_policy,
            subscriptions=self.b04_repositories.subscriptions,
            lifecycle=self.b04_repositories.lifecycle,
            jobs=self.b04_repositories.jobs,
            scheduler=self.b04_repositories.scheduler,
            digest_windows=self.b04_repositories.windows,
            grants=self.repositories.authorization,
            principal_resolver=self.principal_resolver,
            owner_authority=self.owner_authority,
            now=utc_clock,
            max_subscriptions_per_owner=max_subscriptions_per_owner,
        )
        # Build exactly one ModuleServicesFactory with the shared B04 service.
        core_configuration = ConfigurationCoordinator(
            core_config_target(config_principal_id),
            CORE_CONFIG_FIELDS,
            self.config_repository,
            self.secret_store,
        )
        self.core_defaults = CoreDefaultsView(
            core_configuration.view(),
            legacy_defaults=legacy_core_defaults,
            migration_complete=(
                self.ordinary_config_migration.complete
                if self.ordinary_config_migration is not None
                else None
            ),
        )
        self.module_services = ModuleServicesFactory(
            self.registry,
            self.issuer,
            self.lifecycle,
            self.repositories,
            http_transport,
            config_principal_id=config_principal_id,
            identity_namespace=identity_namespace,
            subscriptions=self.subscription_operations,
            principal_resolver=self.principal_resolver,
            owner_authority=self.owner_authority,
            source_credential_policies=source_credential_policies,
            clock=monotonic_clock,
            utc_clock=utc_clock,
            module_host_config_snapshots=host_config_snapshots,
            core_defaults=self.core_defaults,
            module_config_validators=module_config_validators,
            storage_router=self.module_storage,
        )
        self.send_scheduler = LifecycleApprovedSendScheduler(self.lifecycle)
        self.root_output_repository = SQLiteRootOutputRepository(database)
        self.resource_visibility = SQLiteResourceVisibilityProbe(database)
        self.output = OutputService(
            issuer=self.issuer,
            admission=self.lifecycle.admission,
            send_scheduler=self.send_scheduler,
            registry=self.registry,
            renderer=renderer,
            limits=display_limits,
            conversations=self._conversation_resolver,
            message_port=message_port,
            deliveries=self.b04_repositories.deliveries,
            grants=self.repositories.authorization,
            subscriptions=self.b04_repositories.subscriptions,
            root_outputs=self.root_output_repository,
            resource_visibility=self.resource_visibility,
            principal_resolver=self.principal_resolver,
            owner_authority=self.owner_authority,
            claim_lease=claim_lease,
            send_timeout=send_timeout,
            now=utc_clock,
        )
        self.delivery = DeliveryService(
            deliveries=self.b04_repositories.deliveries,
            subscriptions=self.b04_repositories.subscriptions,
            grants=self.repositories.authorization,
            windows=self.b04_repositories.windows,
            registry=self.registry,
            admission=self.lifecycle.admission,
            send_scheduler=self.send_scheduler,
            routes=self._persisted_routes,
            renderer=renderer,
            message_port=message_port,
            limits=display_limits,
            now=utc_clock,
            claim_lease=claim_lease,
            send_timeout=send_timeout,
        )
        self.scheduler = SharedCollectionScheduler(
            registry=self.registry,
            issuer=self.issuer,
            lifecycle=self.lifecycle,
            admission=self.lifecycle.admission,
            execution_claim_proofs=self.execution_claim_proofs,
            binder_for=lambda module_id: (
                self.module_services.for_module(module_id).scopes
            ),
            repository=self.b04_repositories.scheduler,
            subscriptions=self.b04_repositories.subscriptions,
            job_links=self.b04_repositories.jobs,
            digest_windows=self.b04_repositories.windows,
            grant_store=self.repositories.authorization,
            routes=self._persisted_routes,
            evaluation_preparer=self.subscription_operations,
            cadence=cadence_policy.configuration,
            reschedule=reschedule_configuration,
            quotas=scheduler_quotas,
            now=utc_clock,
            monotonic_clock=monotonic_clock,
        )
        self.admin_authorization = AdminAuthorizationService(
            self.admin_credential_repository,
            admission=self.lifecycle.admission,
            context_validator=admin_context_validator,
        )

        async def validate_set_enabled(grant) -> None:
            from ..api.administration import AdminOperation

            await self.admin_authorization.validate_generation(
                grant, operation=AdminOperation.SET_ENABLED
            )

        if factory_source is None:
            try:
                from ..extensions.factory_resolver import FilesystemFactorySource
            except ImportError:
                raise RuntimeError(
                    "E source resolver is not available; inject a FactorySourcePort"
                ) from None
            factory_source = FilesystemFactorySource()
        self.extension_runtime = ExtensionRuntime(
            self.extension_root,
            registry=self.registry,
            lifecycle=self.lifecycle,
            authorization=self.admin_authorization,
            validate_admin_grant=validate_set_enabled,
            runtime_repository=self.runtime_repository,
            module_services=self.module_services,
            health_resolver=self.health_resolver,
            factory_source=factory_source,
            cleanup_timeout=cleanup_timeout,
        )
        self.admin_operations = AdminOperationsService(
            authorization=self.admin_authorization,
            extension_runtime=self.extension_runtime,
            registry=self.registry,
            lifecycle=self.lifecycle,
            health_resolver=self.health_resolver,
            database=database,
            config_repository=self.config_repository,
            module_config_validators=module_config_validators,
            secret_store=self.secret_store,
            config_principal_id=config_principal_id,
            subscription_gate_fields=gate_fields,
            ordinary_migration=self.ordinary_config_migration,
        )
        self.admin_facade = AdminFacade(self.admin_operations, self.admin_authorization)
        self.gateway = Gateway(
            self.registry,
            self.issuer,
            admission=self.lifecycle.admission,
            lifecycle=self.lifecycle,
            private_authorizer=self.module_services.authorize_private,
            owner_authority=self.owner_authority,
            handler_timeout=handler_timeout,
            clock=monotonic_clock,
        )
        self.trusted_route_publisher = TrustedRoutePublisher(
            self.repositories.conversations, self.lifecycle.admission
        )
        self._host_ingress_validator = host_ingress_validator
        self._utc_clock = utc_clock
        self._monotonic_clock = monotonic_clock
        self._handler_timeout = float(handler_timeout)
        self._send_timeout = float(send_timeout)
        self._pump_interval = float(pump_interval)
        self._cleanup_timeout = float(cleanup_timeout)
        self._started = False
        self._management_ready = False
        self._configuration_blocked = False
        self._starting = False
        self._accepting = False
        self._closing = False
        self._closed = False
        self._cleanup_pending = False
        self._pump_task: asyncio.Task[None] | None = None
        self._host_flights: set[asyncio.Task[object]] = set()
        self._pump_failures: tuple[CoreRuntimeFailure, ...] = ()
        self._startup_report: CoreStartupReport | None = None
        self._close_lock = asyncio.Lock()
        self._startup_done = asyncio.Event()
        self._startup_done.set()

    @property
    def started(self) -> bool:
        return self._started

    @property
    def management_available(self):
        return self._management_ready and not (
            self._closed or self._closing or self._cleanup_pending
        )

    @property
    def configuration_blocked(self):
        return self.management_available and self._configuration_blocked

    @property
    def accepting(self) -> bool:
        return self._accepting

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def cleanup_pending(self) -> bool:
        return self._cleanup_pending

    @property
    def startup_report(self) -> CoreStartupReport | None:
        return self._startup_report

    @property
    def restore_failures(self) -> tuple[ExtensionRestoreFailure, ...]:
        return self.extension_runtime.restore_failures

    @property
    def pump_failures(self) -> tuple[CoreRuntimeFailure, ...]:
        return self._pump_failures

    def public_module_catalog(self) -> dict:
        """Read registered modules through the fixed metadata-only projection."""
        snapshot = self.registry.snapshot()
        states = {
            module_id: self.lifecycle.state(module_id) for module_id in snapshot.modules
        }
        identities = {
            module_id: self.lifecycle.current_identity(module_id)
            for module_id in snapshot.modules
        }
        runtime_state = (
            "closed"
            if self._closed
            else "closing"
            if self._closing
            else "ready"
            if self._started and self._accepting and not self._cleanup_pending
            else "not_ready"
        )
        return project_module_catalog(
            snapshot, states, identities, runtime_state=runtime_state
        )

    async def subscription_gate_state(self, module_id: str) -> SubscriptionGateState:
        """Read exact persistent eligibility and live state under the original gate."""
        if module_id not in self._trusted_subscription_gates:
            return SubscriptionGateState(False, None, None, "unsupported")
        try:
            async with self.lifecycle.admission.mutation("subscription-gate-read"):
                # run_read lazily initializes a fresh executor. Page reads must
                # never become a startup/migration authority.
                if not self._started:
                    return SubscriptionGateState(True, None, None, "state_unknown")
                persisted = (
                    await self.b04_repositories.lifecycle.read_subscription_gate_state(
                        module_id
                    )
                )
                if persisted.reason is not None:
                    return SubscriptionGateState(
                        persisted.supported, None, False, persisted.reason
                    )
                if persisted.enabled is False:
                    return SubscriptionGateState(True, False, False, "gate_disabled")
                if persisted.intent_enabled is False:
                    return SubscriptionGateState(True, True, False, "module_disabled")
                module = self.registry.snapshot().modules.get(module_id)
                state = self.lifecycle.state(module_id) if module is not None else None
                if (
                    not self._started
                    or not self._accepting
                    or self._closing
                    or self._closed
                    or module is None
                    or not module.enabled
                    or state is None
                    or state.lifecycle.value != "active"
                    or state.identity is None
                    or state.identity.module_epoch != module.epoch
                ):
                    return SubscriptionGateState(True, True, False, "runtime_not_ready")
                return SubscriptionGateState(True, True, True, None)
        except Exception:
            return SubscriptionGateState(True, None, None, "state_unknown")

    async def recover_management(
        self, expected_revisions, *, authorization, complete_from_current=False
    ):
        if not self.configuration_blocked or self._starting:
            raise AdminAuthorizationDenied
        resources = self.admin_operations.ordinary_resources()
        grant = await self.admin_authorization.authorize(
            AdminOperation.RECOVER_CONFIG,
            invocation=None,
            context=authorization,
            resources=resources,
        )
        migration = self.ordinary_config_migration
        self.admin_operations.require_ordinary_validators()
        async with self.lifecycle.admission.mutation("admin-ordinary-recovery"):

            def check(unit):
                self.admin_authorization.assert_generation_current(
                    unit, grant, AdminOperation.RECOVER_CONFIG
                )
                if set(expected_revisions) != set(resources):
                    raise ValueError("recovery requires both target revisions")
                for target, revision in expected_revisions.items():
                    if type(revision) is not int or revision < 1:
                        raise ValueError("recovery revision is invalid")
                    migration.repository._revision(unit, target, revision)

            await self.database.executor.run_transaction(check, begin_mode="IMMEDIATE")
            await self.admin_authorization.validate_generation(
                grant, operation=AdminOperation.RECOVER_CONFIG
            )
            if complete_from_current:
                await migration.complete_from_current(expected_revisions, grant=grant)
            elif not await migration.complete():
                if not callable(self._ordinary_migration_source):
                    raise ValueError("prepared ordinary migration input is required")
                await migration.migrate(self._ordinary_migration_source(), grant=grant)
            await self.admin_authorization.validate_generation(
                grant, operation=AdminOperation.RECOVER_CONFIG
            )
        await self.start(recovery_grant=grant)
        await self.admin_authorization.validate_generation(
            grant, operation=AdminOperation.RECOVER_CONFIG
        )
        return {"recovered": True}

    def _check_recovery(self, grant):
        if grant is not None:
            effect = self.admin_authorization._owned(grant)
            effect.check_lifetime()
            if effect.source is not None:
                effect.source.check(
                    effect.context, AdminOperation.RECOVER_CONFIG, effect.resources
                )

    async def start(self, *, recovery_grant=None) -> CoreStartupReport:
        if self._closed or self._closing:
            raise RuntimeError("CoreRuntime is closing or closed")
        if self._started:
            assert self._startup_report is not None
            return self._startup_report
        if self._starting:
            raise RuntimeError("CoreRuntime startup is already in progress")
        self._starting = True
        self._startup_done.clear()
        try:
            self._check_recovery(recovery_grant)
            try:
                migration_version = await self.database.executor.initialize()
                self._check_recovery(recovery_grant)
                if self._closing:
                    raise asyncio.CancelledError
                await self.database.executor.verify_integrity()
                self._check_recovery(recovery_grant)
                if self._closing:
                    raise asyncio.CancelledError
                candidates = self.extension_runtime.scan(self.extension_root)
            except BaseException:
                self._management_ready = False
                self._configuration_blocked = False
                raise
            self._management_ready = True
            self._configuration_blocked = False
            if self.ordinary_config_migration is not None:
                try:
                    async with self.lifecycle.admission.mutation(
                        "ordinary-config-startup-migration"
                    ):
                        self.admin_operations.require_ordinary_validators()
                        complete = await self.ordinary_config_migration.complete()
                        self._check_recovery(recovery_grant)
                        if not complete:
                            if not callable(self._ordinary_migration_source):
                                raise ValueError(
                                    "prepared ordinary migration input is required"
                                )
                            await self.ordinary_config_migration.migrate(
                                self._ordinary_migration_source(), grant=recovery_grant
                            )
                            self._check_recovery(recovery_grant)
                except Exception:
                    self._configuration_blocked = True
                    raise
            expired_logins = (
                await self.repositories.authorization.expire_pending_on_restart()
            )
            self._check_recovery(recovery_grant)
            if self._closing:
                raise asyncio.CancelledError
            if self._subscription_gate_candidates_match(candidates):
                await self.config_repository.initialize_subscription_gates()
            self._check_recovery(recovery_grant)
            if self._closing:
                raise asyncio.CancelledError
            if self._trusted_bundled_manifests:
                await self._seed_trusted_bundled_manifests(candidates)
            self._check_recovery(recovery_grant)
            if self._closing:
                raise asyncio.CancelledError
            if self._trusted_bundled_defaults:
                await self._seed_trusted_bundled_defaults(candidates)
            self._check_recovery(recovery_grant)
            if self._closing:
                raise asyncio.CancelledError
            config_failures = (
                await self.admin_operations.recover_discovered_configuration()
            )
            self._check_recovery(recovery_grant)
            if self._closing:
                raise asyncio.CancelledError
            file_items = await self.file_store.recover_orphans()
            self._check_recovery(recovery_grant)
            if self._closing:
                raise asyncio.CancelledError
            now = _require_utc(self._utc_clock())
            recovered_outputs = await self.root_output_repository.recover_expired(
                before=now, recovered_at=now
            )
            self._check_recovery(recovery_grant)
            if self._closing:
                raise asyncio.CancelledError
            (
                recovered_events,
                recovered_envelopes,
            ) = await self.delivery.recover_startup()
            self._check_recovery(recovery_grant)
            if self._closing:
                raise asyncio.CancelledError
            module_statuses = await self.extension_runtime.restore_startup()
            self._check_recovery(recovery_grant)
            report = CoreStartupReport(
                migration_version,
                expired_logins,
                len(recovered_outputs),
                len(recovered_events),
                len(recovered_envelopes),
                file_items,
                config_failures,
                module_statuses,
                self.extension_runtime.restore_failures,
            )
            self._startup_report = report
            self._started = True
            if not self._closing:
                self._accepting = True
                self._pump_task = asyncio.get_running_loop().create_task(
                    self._pump_loop(), name="yomihime-core-runtime-pump"
                )
            return report
        finally:
            self._starting = False
            self._startup_done.set()

    def _subscription_gate_candidates_match(
        self, candidates: tuple[ExtensionCandidate, ...]
    ) -> bool:
        """Validate every requested binding before any bootstrap mutation."""
        if not self._trusted_subscription_gates:
            return False
        for module_id, gate in self._trusted_subscription_gates.items():
            package_id, local_module_id = module_id.split("/", 1)
            matches = tuple(
                item for item in candidates if item.package.package_id == package_id
            )
            if len(matches) != 1:
                return False
            package = matches[0].package
            if not isinstance(package, DiscoveredPackage) or not package.valid:
                return False
            manifest = package.manifest
            if manifest is None or manifest.package_id != package_id:
                return False
            modules = tuple(
                item for item in manifest.modules if item.module_id == local_module_id
            )
            provenance = package._provenance
            if (
                len(modules) != 1
                or not _matches_trusted_bundled_manifest(modules[0], gate.manifest)
                or not _is_subscription_gate_field(modules[0], gate.field)
                or type(provenance) is not PackageProvenance
                or not provenance.trusted
                or provenance.package_id != package_id
                or type(provenance.manifest_sha256) is not bytes
                or provenance.manifest_sha256 != gate.manifest_sha256
            ):
                return False
        return True

    async def _seed_trusted_bundled_defaults(
        self, candidates: tuple[ExtensionCandidate, ...]
    ) -> None:
        """Seed requested safe modules without replacing persisted user intent."""
        for (
            global_module_id,
            expected_capabilities,
        ) in self._trusted_bundled_defaults.items():
            package_id, local_module_id = global_module_id.split("/", 1)
            matches = tuple(
                candidate
                for candidate in candidates
                if candidate.package.package_id == package_id
            )
            if len(matches) != 1:
                continue
            package = matches[0].package
            manifest = package.manifest
            if not package.valid or manifest is None:
                continue
            modules = tuple(
                module
                for module in manifest.modules
                if module.module_id == local_module_id
            )
            if len(modules) != 1 or not _is_safe_bundled_default(
                modules[0], expected_capabilities
            ):
                continue
            await self.runtime_repository.seed_default_enabled_if_absent(
                package_id,
                local_module_id,
                expected_registry_revision=self.registry.snapshot().revision,
            )

    async def _seed_trusted_bundled_manifests(
        self, candidates: tuple[ExtensionCandidate, ...]
    ) -> None:
        """Seed exact host-reviewed module declarations without replacing intent.

        The mapping is a host-composition input built only from a byte-exact
        bundled installation. Candidate manifests are inertly parsed by the
        extension scanner. Equality covers every descriptor, including
        capabilities, privacy/effects, sources, config, schedules and
        subscriptions; Core additionally refuses raw disk credential aliases.
        """
        for module_id, expected in self._trusted_bundled_manifests.items():
            package_id, local_module_id = module_id.split("/", 1)
            matches = tuple(
                item for item in candidates if item.package.package_id == package_id
            )
            if len(matches) != 1:
                continue
            package = matches[0].package
            if not package.valid or package.manifest is None:
                continue
            modules = tuple(
                item
                for item in package.manifest.modules
                if item.module_id == local_module_id
            )
            if len(modules) != 1 or not _matches_trusted_bundled_manifest(
                modules[0], expected
            ):
                continue
            await self.runtime_repository.seed_default_enabled_if_absent(
                package_id,
                local_module_id,
                expected_registry_revision=self.registry.snapshot().revision,
            )

    async def _pump_loop(self) -> None:
        while self._accepting:
            try:
                await self.scheduler.run_due_page()
                await self.scheduler.create_digest_windows_page()
                await self.delivery.dispatch_due_events()
                await self.delivery.dispatch_due_digests()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._pump_failures = (
                    *self._pump_failures,
                    CoreRuntimeFailure(
                        "scheduler_pump", "owned_pump", "operation_failed"
                    ),
                )
                return
            await asyncio.sleep(self._pump_interval)

    def stop_accepting_host_ingress(self) -> None:
        """Synchronous shutdown fence before Host tool cleanup awaits."""
        self._accepting = False

    def stop_accepting_admin_operations(self) -> None:
        self.admin_operations.stop_accepting()
        self.admin_authorization.close()

    async def _host_ingress(
        self, origin: InvocationOrigin, ingress: HostIngress
    ) -> None:
        self._require_accepting_ingress()
        if not isinstance(ingress, HostIngress):
            raise PermissionError("trusted host ingress is required")
        if origin is InvocationOrigin.LLM_TOOL and ingress.grant_reference is not None:
            raise PermissionError("Tool ingress cannot carry a private grant")
        accepted = self._host_ingress_validator(origin, ingress)
        if inspect.isawaitable(accepted):
            accepted = await accepted
        if accepted is not True:
            raise PermissionError("host ingress evidence was rejected")
        self._require_accepting_ingress()

    def _require_accepting_ingress(self) -> None:
        if not self._started or not self._accepting or self._closing or self._closed:
            raise RuntimeError("CoreRuntime is not accepting host ingress")

    def _active_module(
        self, module_id: str, *, origin: InvocationOrigin, target: str
    ) -> tuple[RegisteredModule, str]:
        module = self.registry.snapshot().module(module_id)
        if not module.enabled:
            raise PermissionError("module is disabled")
        state = self.lifecycle.state(module_id)
        if state.lifecycle.value != "active" or state.identity is None:
            raise PermissionError("module is not active")
        if state.identity.module_epoch != module.epoch:
            raise PermissionError("module epoch is stale")
        if origin is InvocationOrigin.COMMAND:
            descriptor = next(
                (
                    item
                    for item in module.manifest.commands
                    if item.operation_path == target
                ),
                None,
            )
            if not isinstance(descriptor, CommandDescriptor):
                raise PermissionError("command is not declared")
            capability_id = descriptor.capability_id
        elif origin is InvocationOrigin.LLM_TOOL:
            descriptor = next(
                (item for item in module.manifest.tools if item.name == target), None
            )
            if not isinstance(descriptor, ToolDescriptor):
                raise PermissionError("Tool is not declared")
            capability_id = descriptor.capability_id
            capability = next(
                (
                    item
                    for item in module.manifest.capabilities
                    if item.capability_id == capability_id
                ),
                None,
            )
            if capability is None or not tool_allowed(capability):
                raise PermissionError("capability cannot be invoked as a Tool")
        else:
            raise PermissionError("unsupported host invocation origin")
        return module, capability_id

    async def invoke_command(
        self,
        module_id: str,
        operation_path: str,
        parameters: object,
        *,
        ingress: HostIngress,
    ) -> CoreInvocationOutcome:
        return await self._invoke_host(
            InvocationOrigin.COMMAND,
            module_id,
            operation_path,
            parameters,
            ingress=ingress,
        )

    async def invoke_public_web(
        self, module_id: str, capability_id: str, parameters: object, *, proof: object
    ) -> CapabilityResult:
        """Standalone data ingress: no principal, trusted route or OutputService."""
        self._require_accepting_ingress()
        return await self.gateway.invoke_public_web(
            proof, module_id, capability_id, parameters
        )

    async def invoke_tool(
        self,
        module_id: str,
        tool_name: str,
        parameters: object,
        *,
        ingress: HostIngress,
    ) -> CoreInvocationOutcome:
        return await self._invoke_host(
            InvocationOrigin.LLM_TOOL,
            module_id,
            tool_name,
            parameters,
            ingress=ingress,
        )

    async def _invoke_host(
        self,
        origin: InvocationOrigin,
        module_id: str,
        target: str,
        parameters: object,
        *,
        ingress: HostIngress,
    ) -> CoreInvocationOutcome:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("host ingress requires an asyncio task")
        self._host_flights.add(task)
        try:
            self._require_accepting_ingress()
            await self._host_ingress(origin, ingress)
            module, capability_id = self._active_module(
                module_id, origin=origin, target=target
            )
            if origin is InvocationOrigin.COMMAND:
                # The ingress validator above is the authority boundary. Keep
                # principal creation after it and after command declaration
                # validation so forged or unsupported events never persist an
                # actor mapping.
                await self._ingress_principal_provisioner.ensure(ingress.actor_id)
            if (
                origin is InvocationOrigin.LLM_TOOL
                and ingress.grant_reference is not None
            ):
                raise PermissionError("Tool ingress cannot carry a private grant")
            await self.trusted_route_publisher.publish(
                ConversationRef(
                    ingress.adapter_id,
                    ingress.conversation_kind,
                    ingress.conversation_id,
                    ingress.delivery_route,
                )
            )
            self._require_accepting_ingress()
            snapshot = self.registry.snapshot()
            current = snapshot.module(module_id)
            state = self.lifecycle.state(module_id)
            if (
                current is not module
                or not current.enabled
                or current.epoch != module.epoch
                or state.lifecycle.value != "active"
                or state.identity is None
                or state.identity.module_epoch != module.epoch
            ):
                raise PermissionError("module changed while ingress was being prepared")
            deadline = self._monotonic_clock() + self._handler_timeout
            view = self.issuer.issue(
                origin=origin,
                module_id=module_id,
                module_epoch=module.epoch,
                registry_revision=snapshot.revision,
                actor_id=ingress.actor_id,
                conversation_id=ingress.conversation_id,
                adapter_id=ingress.adapter_id,
                capability_id=capability_id,
                deadline=deadline,
                grant_id=(
                    ingress.grant_reference.grant_id
                    if ingress.grant_reference is not None
                    else None
                ),
                grant_revision=(
                    ingress.grant_reference.revision
                    if ingress.grant_reference is not None
                    else None
                ),
            )
            try:
                self.lifecycle.admission.admit(view, capability_id)
                if origin is InvocationOrigin.COMMAND:
                    result = await self.gateway.invoke_command(view, target, parameters)
                else:
                    result = await self.gateway.invoke_tool(view, target, parameters)
                routed = await self.output.route(view, result)
                exposed_result = result
                if (
                    isinstance(result, CapabilityResult)
                    and result.privacy is Privacy.PRIVATE
                    and routed.status is not OutputStatus.SENT
                ):
                    # A failed or stale root route is not a second channel for
                    # private documents or facts. Keep the safe routing result
                    # and hide both raw copies from the Core-facing outcome.
                    exposed_result = None
                    routed = OutputResult(
                        routed.status,
                        receipt=routed.receipt,
                        error_code=routed.error_code,
                    )
                return CoreInvocationOutcome(exposed_result, routed)
            finally:
                self.owner_authority.release(view)
                self.issuer.release(view)
        finally:
            self._host_flights.discard(task)

    async def close(self, *, timeout: float | None = None) -> bool:
        bound = self._cleanup_timeout if timeout is None else timeout
        _positive_finite(bound, "timeout")
        self.gateway.fence_public_web()  # Web-only synchronous fence before first await.
        self.admin_authorization.close()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + float(bound)
        try:
            await asyncio.wait_for(self._close_lock.acquire(), timeout=float(bound))
        except TimeoutError as exc:
            self._cleanup_pending = True
            raise CoreRuntimeCleanupPending("close_lock") from exc
        try:
            return await self._close_with_lock(deadline)
        finally:
            self._close_lock.release()

    async def _close_with_lock(self, deadline: float) -> bool:
        if self._closed:
            return True
        self._closing = True
        self._accepting = False
        # Fence pending durable activation before close can suspend on the
        # shared Admission mutation gate.
        self.extension_runtime.stop_accepting()
        admin_flights = self.admin_operations.stop_accepting(cancel_inflight=True)
        loop = asyncio.get_running_loop()
        if not await self.gateway.drain_public_web(max(0.0, deadline - loop.time())):
            self._cleanup_pending = True
            raise CoreRuntimeCleanupPending("public_web")
        if self._starting:
            try:
                await asyncio.wait_for(
                    self._startup_done.wait(),
                    timeout=max(0.0, deadline - loop.time()),
                )
            except TimeoutError as exc:
                self._cleanup_pending = True
                raise CoreRuntimeCleanupPending("startup") from exc
        pump = self._pump_task
        host_flights = tuple(
            task
            for task in self._host_flights
            if task is not asyncio.current_task() and not task.done()
        )
        for task in host_flights:
            task.cancel()
        if pump is not None and not pump.done():
            pump.cancel()
        remaining = max(0.0, deadline - loop.time())
        if remaining <= 0:
            self._cleanup_pending = True
            raise CoreRuntimeCleanupPending("module_quiesce")
        try:
            await asyncio.wait_for(self._quiesce_admission_gates(), timeout=remaining)
        except TimeoutError as exc:
            self._cleanup_pending = True
            raise CoreRuntimeCleanupPending("module_quiesce") from exc
        except Exception as exc:
            self._cleanup_pending = True
            raise CoreRuntimeCleanupPending("module_quiesce") from exc

        if pump is not None and not pump.done():
            done, pending = await asyncio.wait(
                {pump}, timeout=max(0.0, deadline - loop.time())
            )
            if pending:
                self._cleanup_pending = True
                raise CoreRuntimeCleanupPending("scheduler_pump")
            del done
        if pump is not None and pump.done():
            try:
                pump.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                self._pump_failures = (
                    *self._pump_failures,
                    CoreRuntimeFailure(
                        "scheduler_pump", "owned_pump", "operation_failed"
                    ),
                )
            self._pump_task = None

        if host_flights:
            _done, pending = await asyncio.wait(
                host_flights,
                timeout=max(0.0, deadline - loop.time()),
            )
            if pending:
                self._cleanup_pending = True
                raise CoreRuntimeCleanupPending("host_ingress")
            for task in _done:
                try:
                    task.result()
                except BaseException:
                    pass

        remaining = max(0.0, deadline - loop.time())
        if not await self.admin_operations.wait_for_quiet(
            admin_flights, timeout=remaining
        ):
            self._cleanup_pending = True
            raise CoreRuntimeCleanupPending("admin_operations")

        remaining = max(0.0, deadline - loop.time())
        if remaining <= 0:
            self._cleanup_pending = True
            raise CoreRuntimeCleanupPending("module_cleanup")
        try:
            await self.extension_runtime.close(timeout=remaining)
        except Exception as exc:
            self._cleanup_pending = True
            raise CoreRuntimeCleanupPending("extension_lifecycle") from exc

        remaining = max(0.0, deadline - loop.time())
        if remaining <= 0:
            self._cleanup_pending = True
            raise CoreRuntimeCleanupPending("credential_services")
        try:
            await self.module_services.close_credentials()
        except Exception as exc:
            self._cleanup_pending = True
            raise CoreRuntimeCleanupPending("credential_services") from exc

        remaining = max(0.0, deadline - loop.time())
        if remaining <= 0:
            self._cleanup_pending = True
            raise CoreRuntimeCleanupPending("sqlite_executor")
        try:
            await self.database.executor.close(timeout=remaining)
        except Exception as exc:
            self._cleanup_pending = True
            raise CoreRuntimeCleanupPending("sqlite_executor") from exc

        self._cleanup_pending = False
        self._closed = True
        self._started = False
        return True

    async def _quiesce_admission_gates(self) -> None:
        async with self.lifecycle.admission.mutation("core-runtime-quiesce"):
            for module_id, _module in tuple(self.registry.snapshot().modules.items()):
                try:
                    state = self.lifecycle.state(module_id)
                except Exception:
                    continue
                if state.lifecycle.value == "active" and state.identity is not None:
                    # Close the shared gate and cancel its owned scope now.
                    # ExtensionRuntime remains the sole Lifecycle operation
                    # owner and performs the durable stop after ingress/pump
                    # work has drained.
                    self.lifecycle.admission.close(
                        state.identity, "core_runtime_shutdown"
                    )
                    if state.scope is not None:
                        state.scope.cancel()


def _normalize_bundled_defaults(
    requested: Mapping[str, tuple[str, ...]] | None,
) -> Mapping[str, frozenset[str]]:
    if requested is None:
        return {}
    if not isinstance(requested, Mapping):
        raise TypeError("trusted_bundled_defaults must be a mapping")
    normalized: dict[str, frozenset[str]] = {}
    for module_id, capability_ids in requested.items():
        if (
            type(module_id) is not str
            or module_id.count("/") != 1
            or any(
                _MODULE_IDENTIFIER.fullmatch(part) is None
                for part in module_id.split("/")
            )
        ):
            raise ValueError("trusted bundled module id is invalid")
        if not isinstance(capability_ids, tuple) or not capability_ids:
            raise ValueError("trusted bundled capabilities must be a non-empty tuple")
        if any(
            type(capability_id) is not str
            or _MODULE_IDENTIFIER.fullmatch(capability_id) is None
            for capability_id in capability_ids
        ):
            raise ValueError("trusted bundled capability id is invalid")
        frozen = frozenset(capability_ids)
        if len(frozen) != len(capability_ids):
            raise ValueError("trusted bundled capabilities contain duplicates")
        normalized[module_id] = frozen
    return normalized


def _normalize_bundled_manifests(
    requested: Mapping[str, ModuleManifest] | None,
) -> Mapping[str, ModuleManifest]:
    """Copy exact expected declarations supplied by trusted host composition."""
    if requested is None:
        return MappingProxyType({})
    if not isinstance(requested, Mapping):
        raise TypeError("trusted_bundled_manifests must be a mapping")
    normalized: dict[str, ModuleManifest] = {}
    for module_id, manifest in requested.items():
        if (
            type(module_id) is not str
            or module_id.count("/") != 1
            or any(
                _MODULE_IDENTIFIER.fullmatch(part) is None
                for part in module_id.split("/")
            )
        ):
            raise ValueError("trusted bundled module id is invalid")
        if not isinstance(manifest, ModuleManifest):
            raise TypeError("trusted bundled declarations must be ModuleManifest")
        if module_id.split("/", 1)[1] != manifest.module_id:
            raise ValueError("trusted bundled manifest id does not match its key")
        normalized[module_id] = manifest
    return MappingProxyType(normalized)


def _is_subscription_gate_field(manifest: ModuleManifest, field: str) -> bool:
    declarations = tuple(item for item in manifest.config_fields if item.name == field)
    return (
        len(declarations) == 1
        and not declarations[0].sensitive
        and declarations[0].default is True
    )


def _normalize_subscription_gates(
    requested: Mapping[str, TrustedSubscriptionGate] | None,
    manifests: Mapping[str, ModuleManifest],
) -> Mapping[str, TrustedSubscriptionGate]:
    if requested is None:
        return MappingProxyType({})
    if not isinstance(requested, Mapping):
        raise TypeError("trusted subscription gates must be a mapping")
    normalized = dict(requested)
    if normalized and set(normalized) != set(manifests):
        raise ValueError("trusted subscription gates require the complete manifest map")
    for module_id, gate in normalized.items():
        if (
            type(gate) is not TrustedSubscriptionGate
            or gate.manifest != manifests[module_id]
            or type(gate.field) is not str
            or not _is_subscription_gate_field(gate.manifest, gate.field)
            or type(gate.manifest_sha256) is not bytes
            or len(gate.manifest_sha256) != 32
        ):
            raise ValueError("trusted subscription gate declaration is invalid")
    return MappingProxyType(normalized)


def _matches_trusted_bundled_manifest(
    candidate: object, expected: ModuleManifest
) -> bool:
    """Match the complete host-reviewed declaration and reject disk aliases."""
    try:
        return (
            isinstance(candidate, ModuleManifest)
            and candidate == expected
            and all(source.credential_ref is None for source in candidate.sources)
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _is_safe_bundled_default(
    module: object, expected_capabilities: frozenset[str]
) -> bool:
    """Require a side-effect-free module with exactly allowlisted capabilities."""
    try:
        if (
            not module.capabilities
            or frozenset(item.capability_id for item in module.capabilities)
            != expected_capabilities
            or module.tools
            or module.schedules
            or module.subscriptions
            or module.config_fields
            or module.collections
        ):
            return False
        # Source declarations are read-only transport policy. Bundled trust is
        # granted by the host only for a byte-exact reviewed package; Core
        # still rejects credentials and leaves undeclared/missing required
        # sources in HealthResolver's capability-level UNKNOWN state.
        if any(source.credential_ref is not None for source in module.sources):
            return False
        if not module.commands:
            return False
        if any(
            command.capability_id not in expected_capabilities
            for command in module.commands
        ):
            return False
        for capability in module.capabilities:
            schema = capability.input_schema
            if (
                capability.effective_origins != (InvocationOrigin.COMMAND,)
                or capability.effect is not CapabilityEffect.READ_ONLY
                or capability.privacy_floor is not PrivacyFloor.PUBLIC
                or capability.required_config
                or capability.required_capabilities
                or schema["type"] != "object"
                or schema["additionalProperties"] is not False
            ):
                return False
        return True
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return False


def _positive_finite(value: object, field: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(float(value))
        or value <= 0
    ):
        raise ValueError(f"{field} must be finite and positive")


def _require_utc(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset().total_seconds() != 0
    ):
        raise ValueError("UTC clock must return an aware UTC datetime")
    return value.astimezone(UTC)


__all__ = [
    "CoreInvocationOutcome",
    "CoreRuntime",
    "CoreRuntimeCleanupPending",
    "CoreRuntimeFailure",
    "CoreStartupReport",
    "DeploymentCadencePolicy",
    "HostIngress",
]
