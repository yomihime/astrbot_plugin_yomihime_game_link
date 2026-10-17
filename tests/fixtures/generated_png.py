"""Offline fixture: real Core authority/SQLite identity, pausable boundary ports.

No Host or platform is started. Only route/Grant reads and identity read timing
are controlled; the identity values come from SQLite. No TestCase inheritance.
"""

import asyncio
import tempfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ygl_test_subject.core.context_issuer import ContextIssuer
from ygl_test_subject.core.contracts.services import Grant, GrantStatus, Principal
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.health import HealthResolver
from ygl_test_subject.core.lifecycle import LifecycleController
from ygl_test_subject.core.registry import Registry
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories_identity import (
    SQLiteIdentityRepository,
)
from ygl_test_subject.infrastructure.sqlite.repositories_output import (
    SQLiteRootOutputRepository,
)
from ygl_test_subject.services.generated_png import (
    GeneratedPngCoordinator,
    GeneratedPngLimits,
)
from ygl_test_subject.services.identity import InvocationPrincipalResolver
from ygl_test_subject.services.output import (
    LifecycleApprovedSendScheduler,
    OutputService,
)
from ygl_test_subject.services.owner_authority import OwnerRouteProofAuthority

from tests.fixtures.minimal_module import build_package
from yomihime_game_link_sdk.contexts import InvocationOrigin
from yomihime_game_link_sdk.declarations import (
    ConfigField,
    InvocationPolicy,
    PrivacyFloor,
)
from yomihime_game_link_sdk.display import (
    DisplayDocument,
    DisplayLimits,
    Privacy,
    TextBlock,
)
from yomihime_game_link_sdk.results import CapabilityResult, ResultStatus
from yomihime_game_link_sdk.services import (
    CapabilityHealth,
    ConfigSnapshot,
    ConfigTarget,
    HealthReport,
    HealthStatus,
)
from yomihime_game_link_sdk.subscriptions import ConversationKind, ConversationRef


class Pause:
    def __init__(self):
        self.entered = asyncio.Event()
        self.resume = asyncio.Event()
        self.enabled = False

    def arm(self):
        self.entered.clear()
        self.resume.clear()
        self.enabled = True

    async def checkpoint(self):
        if self.enabled:
            self.enabled = False
            self.entered.set()
            await self.resume.wait()


class _IdentityReads(Pause):
    def __init__(self, repository):
        super().__init__()
        self.repository = repository

    async def find_principal(self, *args):
        await self.checkpoint()
        return await self.repository.find_principal(*args)


class _Routes(Pause):
    def __init__(self, route):
        super().__init__()
        self.value = route

    async def resolve(self, invocation):
        await self.checkpoint()
        return self.value


class _Grants(Pause):
    def __init__(self):
        super().__init__()
        self.value = None

    async def current_grant(self, grant_id):
        await self.checkpoint()
        return self.value


class _UnusedPort:
    async def render(self, *args, **kwargs):
        raise AssertionError("B must not render or send through output")

    async def send(self, *args, **kwargs):
        raise AssertionError("B must not send")


class _Instance:
    def __init__(self, handlers):
        self._handlers = handlers

    def handlers(self):
        return self._handlers

    async def start(self):
        pass

    async def stop(self):
        pass

    async def check_health(self):
        return validate_contract(
            HealthReport(
                {
                    key: CapabilityHealth(HealthStatus.AVAILABLE)
                    for key in ("lookup", "status")
                }
            )
        )


class GeneratedPngFixture:
    async def start(self, *, owner=False, limits=GeneratedPngLimits()):
        self.now = 10.0
        self.utc = datetime(2026, 10, 15, tzinfo=UTC)
        self.temp = tempfile.TemporaryDirectory(prefix="generated-png-")
        self.db = SQLiteDatabase(Path(self.temp.name) / "test.sqlite3")
        self.db.initialize()
        self.identities = SQLiteIdentityRepository(self.db)
        await self.identities.save_principal(
            Principal("principal-SENSITIVE", "png-users", "actor-SENSITIVE")
        )
        self.identity_reads = _IdentityReads(self.identities)
        self.registry = Registry()
        package, handlers = build_package()
        module = package.modules[0]
        module = validate_contract(
            replace(
                module,
                capabilities=(
                    replace(module.capabilities[0], required_config=("ready",)),
                    module.capabilities[1],
                ),
                config_fields=(ConfigField("ready"),),
            )
        )
        package = validate_contract(replace(package, modules=(module,)))
        if owner:
            module = package.modules[0]
            capability = replace(
                module.capabilities[0],
                invocation_policy=InvocationPolicy.COMMAND_ONLY,
                privacy_floor=PrivacyFloor.OWNER,
            )
            module = validate_contract(
                replace(
                    module, capabilities=(capability, module.capabilities[1]), tools=()
                )
            )
            package = validate_contract(replace(package, modules=(module,)))
        self.registry.register_package(package, {"demo": handlers})
        self.issuer = ContextIssuer(clock=lambda: self.now)
        self.health = HealthResolver(config_snapshot=self._config_snapshot)
        self.lifecycle = LifecycleController(
            self.registry, issuer=self.issuer, capability_health_query=self.health.query
        )
        self.admission = self.lifecycle.admission
        self.health.bind_runtime(self.lifecycle, self.admission)
        module = self.registry.snapshot().module("sample/demo")
        instance = _Instance(handlers)
        captured = self.lifecycle.adopt_candidate(
            "sample", module.manifest, "png-install", instance
        )
        self.lifecycle.install_dormant(
            "sample", module.module_id, "png-install", instance, captured
        )
        identity, _ = await self.lifecycle.start_candidate(
            module.module_id, "png-start"
        )
        await self.health.prepare(module.module_id, module.manifest)
        self.lifecycle.publish_committed_intent(
            module.module_id,
            "png-start",
            identity,
            True,
            self.registry.snapshot().revision,
        )
        self.route = validate_contract(
            ConversationRef(
                "adapter-SENSITIVE",
                ConversationKind.DIRECT,
                "conversation-SENSITIVE",
                "direct:SENSITIVE",
            )
        )
        self.routes = _Routes(self.route)
        self.grants = _Grants()
        self.principals = InvocationPrincipalResolver(
            self.issuer,
            self.identity_reads,
            identity_namespace="png-users",
            admission=self.admission,
        )
        self.owner = OwnerRouteProofAuthority(
            self.issuer, self.admission, self.principals, self.routes
        )
        self.root_outputs = SQLiteRootOutputRepository(self.db)
        self.output = OutputService(
            issuer=self.issuer,
            admission=self.admission,
            send_scheduler=LifecycleApprovedSendScheduler(self.lifecycle),
            registry=self.registry,
            renderer=_UnusedPort(),
            limits=DisplayLimits(max_pages=1, max_image_bytes=1),
            conversations=self.routes,
            message_port=_UnusedPort(),
            deliveries=None,
            grants=self.grants,
            subscriptions=None,
            root_outputs=self.root_outputs,
            resource_visibility=None,
            principal_resolver=self.principals,
            owner_authority=self.owner,
            claim_lease=timedelta(seconds=60),
            send_timeout=1,
            now=lambda: self.utc,
        )
        self.coordinator = self.new_coordinator(limits)
        self.coordinators = [self.coordinator]
        self.view = self.new_view()
        self.result = self.new_result(Privacy.PRIVATE if owner else Privacy.PUBLIC)
        if owner:
            await self.owner.capture(self.view, self.issuer.lease_for(self.view))
        return self

    async def _config_snapshot(self, module_id):
        return validate_contract(
            ConfigSnapshot(
                1, {"ready": True}, target=ConfigTarget("png-tests", module_id)
            )
        )

    def new_coordinator(self, limits=GeneratedPngLimits()):
        return GeneratedPngCoordinator(
            output_authority=self.output,
            limits=limits,
            monotonic_clock=lambda: self.now,
            utc_clock=lambda: self.utc,
        )

    def new_view(self, **changes):
        snapshot = self.registry.snapshot()
        values = dict(
            origin=InvocationOrigin.COMMAND,
            module_id="sample/demo",
            module_epoch=snapshot.module("sample/demo").epoch,
            registry_revision=snapshot.revision,
            actor_id="actor-SENSITIVE",
            adapter_id=self.route.adapter_id,
            conversation_id=self.route.conversation_id,
            capability_id="lookup",
        )
        values.update(changes)
        view = self.issuer.issue(**values)
        if view.capability_id is not None:
            self.admission.admit(view, view.capability_id)
        return view

    @staticmethod
    def new_result(privacy=Privacy.PUBLIC):
        return validate_contract(
            CapabilityResult(
                "result-SENSITIVE",
                ResultStatus.SUCCESS,
                document=DisplayDocument(
                    "title-SENSITIVE",
                    "subject-SENSITIVE",
                    (TextBlock("body-SENSITIVE"),),
                    privacy=privacy,
                ),
                privacy=privacy,
            )
        )

    def grant(self, **changes):
        value = Grant(
            "grant-SENSITIVE",
            1,
            "principal-SENSITIVE",
            "sample/demo",
            "account-SENSITIVE",
            ("read",),
            None,
            GrantStatus.ACTIVE,
            self.utc + timedelta(seconds=30),
        )
        self.grants.value = validate_contract(replace(value, **changes))
        return self.grants.value

    async def stop(self):
        for coordinator in getattr(self, "coordinators", ()):
            if not coordinator.close():
                raise AssertionError("test left live reservations or pins")
        if (
            hasattr(self, "lifecycle")
            and self.registry.snapshot().module("sample/demo").enabled
        ):
            await self.lifecycle.stop("sample/demo", timeout=1)
        if hasattr(self, "db"):
            await self.db.executor.close()
        if hasattr(self, "temp"):
            self.temp.cleanup()
