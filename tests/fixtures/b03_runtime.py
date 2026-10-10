"""Real SQLite runtime fixture for B03 service assembly integration tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ygl_test_subject.core.context_issuer import ContextIssuer
from ygl_test_subject.core.contracts.services import Principal
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.health import HealthResolver
from ygl_test_subject.core.lifecycle import LifecycleController
from ygl_test_subject.core.registry import Registry
from ygl_test_subject.infrastructure.files import LocalSafeFileStore
from ygl_test_subject.infrastructure.http import HttpTransport, TransportRequest
from ygl_test_subject.infrastructure.secret_store import SQLiteSecretStore
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories import SQLiteRepositories
from ygl_test_subject.infrastructure.sqlite.repositories_subscriptions import (
    SQLiteSchedulerRepository,
)
from ygl_test_subject.services.configuration import ConfigurationService
from ygl_test_subject.services.module_services import (
    ModuleServicesFactory,
    RegistryRegistrationLookup,
)
from ygl_test_subject.services.scheduler import ExecutionClaimProofRegistry

from yomihime_game_link_sdk.declarations import (
    CapabilityDescriptor,
    CapabilityEffect,
    CapabilityReference,
    CommandDescriptor,
    ConfigField,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
    PrivacyFloor,
    SourceDeclaration,
)
from yomihime_game_link_sdk.display import DisplayDocument, Privacy, TextBlock
from yomihime_game_link_sdk.results import CapabilityResult, ResultStatus
from yomihime_game_link_sdk.services import (
    CapabilityHealth,
    ConfigSnapshot,
    ConfigTarget,
    HealthReport,
    HealthStatus,
    ModuleHandlers,
)
from yomihime_game_link_sdk.storage import CollectionDescriptor, OwnershipKind
from yomihime_game_link_sdk.subscriptions import (
    ConversationKind,
    ConversationRef,
    ScheduleDescriptor,
    ScheduleTrigger,
)
from yomihime_game_link_sdk.version import MODULE_ABI_VERSION


class _TestCodec:
    """Deterministic test-only envelope; no production key is implied."""

    def encrypt(self, value: bytes) -> bytes:
        return b"test-envelope:" + value[::-1]

    def decrypt(self, value: bytes) -> bytes:
        prefix = b"test-envelope:"
        if not value.startswith(prefix):
            raise ValueError("invalid test envelope")
        return value[len(prefix) :][::-1]


class OfflineHttpTransport(HttpTransport):
    """Injected transport that returns bounded fixed data without networking."""

    def __init__(self) -> None:
        self.requests: list[TransportRequest] = []

    async def request(self, request: TransportRequest):
        self.requests.append(request)
        from yomihime_game_link_sdk.services import HttpResponse

        return validate_contract(
            HttpResponse(200, {"content-type": "text/plain"}, b"offline")
        )


class _Handler:
    def __init__(self, capability_id: str = "read") -> None:
        self.capability_id = capability_id
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False
        self.calls: list[object] = []

    async def invoke(self, context, parameters):
        self.calls.append((context, parameters))
        if self.block:
            self.started.set()
            await self.release.wait()
        privacy = (
            Privacy.PRIVATE if self.capability_id == "private.read" else Privacy.PUBLIC
        )
        return validate_contract(
            CapabilityResult(
                "b03-test",
                ResultStatus.SUCCESS,
                document=validate_contract(
                    DisplayDocument(
                        "B03",
                        "test",
                        (validate_contract(TextBlock("offline")),),
                        privacy=privacy,
                    )
                ),
                privacy=privacy,
            )
        )


class _Collector:
    def normalize(self, parameters):
        from yomihime_game_link_sdk.subscriptions import NormalizedInput

        return validate_contract(NormalizedInput(parameters))

    async def collect(self, context, parameters, previous):
        raise AssertionError("the B03 scheduled collector is not run by this fixture")


class _RuntimeInstance:
    """A real Lifecycle-owned offline module instance."""

    def __init__(self, handlers: ModuleHandlers, manifest: ModuleManifest) -> None:
        self._handlers = handlers
        self._capabilities = tuple(item.capability_id for item in manifest.capabilities)

    def handlers(self) -> ModuleHandlers:
        return self._handlers

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def check_health(self) -> HealthReport:
        return validate_contract(
            HealthReport(
                {
                    capability_id: validate_contract(
                        CapabilityHealth(HealthStatus.AVAILABLE)
                    )
                    for capability_id in self._capabilities
                }
            )
        )


def _manifest(module_id: str, dependencies=()) -> ModuleManifest:
    descriptor = validate_contract(
        CapabilityDescriptor(
            "read",
            {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            InvocationPolicy.NATURAL_LANGUAGE_ALLOWED,
            CapabilityEffect.READ_ONLY,
            required_capabilities=tuple(dependencies),
        )
    )
    private_descriptor = validate_contract(
        CapabilityDescriptor(
            "private.read",
            {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            InvocationPolicy.COMMAND_ONLY,
            CapabilityEffect.READ_ONLY,
            privacy_floor=PrivacyFloor.PRIVATE,
        )
    )
    capabilities = (
        (descriptor, private_descriptor) if module_id == "alpha" else (descriptor,)
    )
    return validate_contract(
        ModuleManifest(
            module_id,
            module_id,
            ModuleCategory.GAME,
            "tests.fixtures.b03_runtime:build_runtime",
            "1.0.0",
            capabilities,
            commands=(
                (
                    validate_contract(
                        CommandDescriptor("private", "private.read", {}, "private read")
                    ),
                )
                if module_id == "alpha"
                else ()
            ),
            config_fields=(validate_contract(ConfigField("region", default="global")),),
            sources=(validate_contract(SourceDeclaration("catalog", "example.test")),),
            collections=(
                validate_contract(
                    CollectionDescriptor("profiles", 1, OwnershipKind.USER)
                ),
                validate_contract(
                    CollectionDescriptor("announcements", 1, OwnershipKind.PUBLIC)
                ),
                validate_contract(
                    CollectionDescriptor("account_records", 1, OwnershipKind.AUTHORIZED)
                ),
            ),
            schedules=(
                (
                    validate_contract(
                        ScheduleDescriptor(
                            "account-collector",
                            1,
                            "catalog",
                            1,
                            {
                                "type": "object",
                                "properties": {},
                                "required": [],
                                "additionalProperties": False,
                            },
                            OwnershipKind.AUTHORIZED,
                            ScheduleTrigger.PERIODIC,
                            60.0,
                            60.0,
                            "cadence",
                        )
                    ),
                )
                if module_id == "alpha"
                else ()
            ),
        )
    )


@dataclass(slots=True)
class B03Runtime:
    root: Path
    registry: Registry
    issuer: ContextIssuer
    lifecycle: LifecycleController
    health: HealthResolver
    lookup: RegistryRegistrationLookup
    database: SQLiteDatabase
    repositories: SQLiteRepositories
    transport: OfflineHttpTransport
    handler: _Handler
    services: ModuleServicesFactory
    scheduler_repository: SQLiteSchedulerRepository
    execution_claim_proofs: ExecutionClaimProofRegistry


async def build_runtime(
    root: str | Path,
    *,
    registry: Registry | None = None,
    issuer: ContextIssuer | None = None,
) -> B03Runtime:
    """Build or reopen reviewed SQLite repositories around trusted host state."""

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if registry is None:
        registry = Registry()
        handler = _Handler()
        beta = _manifest(
            "beta",
            (validate_contract(CapabilityReference("sample/alpha", "private.read")),),
        )
        alpha = _manifest(
            "alpha",
            (
                validate_contract(CapabilityReference("sample/beta", "read")),
                validate_contract(CapabilityReference("sample/alpha", "private.read")),
            ),
        )
        package = validate_contract(
            PackageManifest(
                "sample",
                "1.0.0",
                MODULE_ABI_VERSION,
                (alpha, beta),
                "tests",
                "MIT",
                "offline B03 integration fixture",
            )
        )
        registry.register_package(
            package,
            {
                "alpha": validate_contract(
                    ModuleHandlers(
                        {"read": handler, "private.read": _Handler("private.read")},
                        {"account-collector": _Collector()},
                        {},
                    )
                ),
                "beta": validate_contract(ModuleHandlers({"read": handler}, {}, {})),
            },
        )
    handler = registry.snapshot().module("sample/alpha").handlers.capabilities["read"]
    issuer = issuer or ContextIssuer()
    lookup = RegistryRegistrationLookup(registry)
    database = SQLiteDatabase(root / "runtime.sqlite3")
    file_store = LocalSafeFileStore(root / "assets")
    secret_store = SQLiteSecretStore(database, root / "secrets", codec=_TestCodec())
    repositories = SQLiteRepositories(
        database,
        lookup,
        file_store=file_store,
        secret_store=secret_store,
    )

    async def config_snapshot(module_id: str) -> ConfigSnapshot:
        module = registry.snapshot().module(module_id)
        if not module.manifest.config_fields:
            return validate_contract(
                ConfigSnapshot(
                    1,
                    {},
                    target=validate_contract(ConfigTarget("host-config", module_id)),
                )
            )
        return await ConfigurationService(
            validate_contract(ConfigTarget("host-config", module_id)),
            module.manifest.config_fields,
            repositories.config,
            repositories.secret_store,
        ).current()

    async def source_health(module_id: str, source_id: str) -> CapabilityHealth:
        # The fixture uses a deterministic offline transport and explicitly
        # publishes its locally available source. Production defaults to UNKNOWN.
        return validate_contract(CapabilityHealth(HealthStatus.AVAILABLE))

    health = HealthResolver(
        config_snapshot=config_snapshot,
        source_health=source_health,
    )
    execution_claim_proofs = ExecutionClaimProofRegistry(now=lambda: datetime.now(UTC))
    lifecycle = LifecycleController(
        registry,
        issuer=issuer,
        capability_health_query=health.query,
        execution_claim_prover=execution_claim_proofs,
    )
    health.bind_runtime(lifecycle, lifecycle.admission)
    await repositories.identities.save_principal(
        Principal("alice", "test-users", "alice")
    )
    await repositories.identities.save_principal(
        Principal("principal-alice", "test-users", "external-alice")
    )
    await repositories.identities.save_principal(Principal("bob", "test-users", "bob"))
    await repositories.conversations.save(
        validate_contract(
            ConversationRef(
                "test-adapter", ConversationKind.GROUP, "room", "test-route"
            )
        )
    )
    for module_id in ("sample/beta", "sample/alpha"):
        if lifecycle.state(module_id).active:
            continue
        module = registry.snapshot().module(module_id)
        instance = _RuntimeInstance(module.handlers, module.manifest)
        suffix = module_id.rsplit("/", 1)[-1]
        install_id = f"b03-install-{suffix}"
        handlers = lifecycle.adopt_candidate(
            "sample", module.manifest, install_id, instance
        )
        lifecycle.install_dormant("sample", module_id, install_id, instance, handlers)
        operation_id = f"b03-start-{suffix}"
        identity, _ = await lifecycle.start_candidate(module_id, operation_id)
        await health.prepare(module_id, module.manifest)
        lifecycle.publish_committed_intent(
            module_id,
            operation_id,
            identity,
            True,
            registry.snapshot().revision,
        )
    transport = OfflineHttpTransport()
    services = ModuleServicesFactory(
        registry,
        issuer,
        lifecycle,
        repositories,
        transport,
        config_principal_id="host-config",
        identity_namespace="test-users",
        secret_available=lambda grant: grant.secret_ref is not None,
    )
    scheduler_repository = SQLiteSchedulerRepository(database)
    return B03Runtime(
        root,
        registry,
        issuer,
        lifecycle,
        health,
        lookup,
        database,
        repositories,
        transport,
        handler,
        services,
        scheduler_repository,
        execution_claim_proofs,
    )


__all__ = ["B03Runtime", "OfflineHttpTransport", "build_runtime"]
