from __future__ import annotations

import unittest
from uuid import uuid4

from ygl_test_subject.core.context_issuer import ContextIssuer
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.health import HealthResolver
from ygl_test_subject.core.invocation import Gateway
from ygl_test_subject.core.lifecycle import LifecycleController
from ygl_test_subject.core.registry import Registry
from ygl_test_subject.services.owner_authority import (
    OwnerRouteProofAuthority,
    OwnerRouteProofError,
)

from yomihime_game_link_sdk.contexts import InvocationOrigin
from yomihime_game_link_sdk.declarations import (
    CapabilityDescriptor,
    CapabilityEffect,
    CommandDescriptor,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
    PrivacyFloor,
)
from yomihime_game_link_sdk.display import DisplayDocument, Privacy, TextBlock
from yomihime_game_link_sdk.results import CapabilityResult, ResultStatus
from yomihime_game_link_sdk.services import (
    CapabilityHealth,
    HealthReport,
    HealthStatus,
    ModuleHandlers,
)
from yomihime_game_link_sdk.subscriptions import ConversationKind, ConversationRef
from yomihime_game_link_sdk.version import MODULE_ABI_VERSION


class _Handler:
    def __init__(self) -> None:
        self.calls = 0
        self.privacy = Privacy.PRIVATE

    async def invoke(self, context, parameters):
        self.calls += 1
        return validate_contract(
            CapabilityResult(
                "owner-result",
                ResultStatus.SUCCESS,
                document=validate_contract(
                    DisplayDocument(
                        "Owner result",
                        "private details",
                        (validate_contract(TextBlock("mine")),),
                        privacy=self.privacy,
                    )
                ),
                privacy=self.privacy,
            )
        )


class _Instance:
    def __init__(self, handler: _Handler) -> None:
        self._handler = handler

    def handlers(self) -> ModuleHandlers:
        return validate_contract(ModuleHandlers({"owner.read": self._handler}, {}, {}))

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def check_health(self) -> HealthReport:
        return validate_contract(
            HealthReport(
                {
                    "owner.read": validate_contract(
                        CapabilityHealth(HealthStatus.AVAILABLE)
                    )
                }
            )
        )


class _PrincipalResolver:
    def __init__(self, principal_id: str = "principal-a") -> None:
        self.value = principal_id

    async def principal_id(self, invocation) -> str:
        return self.value


class _RouteResolver:
    def __init__(self, route: ConversationRef) -> None:
        self.value = route

    async def resolve(self, invocation) -> ConversationRef:
        return self.value


class OwnerRouteProofTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.registry = Registry()
        self.issuer = ContextIssuer()
        self.health = HealthResolver(config_snapshot=self._empty_config)
        self.lifecycle = LifecycleController(
            self.registry,
            issuer=self.issuer,
            capability_health_query=self.health.query,
        )
        self.health.bind_runtime(self.lifecycle, self.lifecycle.admission)
        self.handler = _Handler()
        capability = validate_contract(
            CapabilityDescriptor(
                "owner.read",
                {"type": "object", "properties": {}, "required": []},
                InvocationPolicy.COMMAND_ONLY,
                CapabilityEffect.READ_ONLY,
                privacy_floor=PrivacyFloor.OWNER,
            )
        )
        module = validate_contract(
            ModuleManifest(
                "owner",
                "owner",
                ModuleCategory.PLATFORM,
                "tests.module:Factory",
                "1.0.0",
                (capability,),
                (
                    validate_contract(
                        CommandDescriptor(
                            "read", "owner.read", {}, "Private owner read"
                        )
                    ),
                ),
            )
        )
        package = validate_contract(
            PackageManifest(
                "owner-tests",
                "1.0.0",
                MODULE_ABI_VERSION,
                (module,),
                "Tests",
                "MIT",
                "offline",
            )
        )
        self.registry.register_package(
            package,
            {
                "owner": validate_contract(
                    ModuleHandlers({"owner.read": self.handler}, {}, {})
                )
            },
        )
        registered = self.registry.snapshot().module("owner-tests/owner")
        instance = _Instance(self.handler)
        install_id = uuid4().hex
        handlers = self.lifecycle.adopt_candidate(
            "owner-tests", registered.manifest, install_id, instance
        )
        self.lifecycle.install_dormant(
            "owner-tests", "owner-tests/owner", install_id, instance, handlers
        )
        run_id = uuid4().hex
        identity, _ = await self.lifecycle.start_candidate("owner-tests/owner", run_id)
        await self.health.prepare("owner-tests/owner", registered.manifest)
        self.lifecycle.publish_committed_intent(
            "owner-tests/owner",
            run_id,
            identity,
            True,
            self.registry.snapshot().revision,
        )

        self.route = validate_contract(
            ConversationRef("adapter-a", ConversationKind.DIRECT, "dm-a", "direct:a")
        )
        self.principals = _PrincipalResolver()
        self.routes = _RouteResolver(self.route)
        self.authority = OwnerRouteProofAuthority(
            self.issuer,
            self.lifecycle.admission,
            self.principals,
            self.routes,
        )
        snapshot = self.registry.snapshot()
        self.view = self.issuer.issue(
            origin=InvocationOrigin.COMMAND,
            module_id="owner-tests/owner",
            module_epoch=snapshot.modules["owner-tests/owner"].epoch,
            registry_revision=snapshot.revision,
            actor_id="adapter-a:user-a",
            adapter_id=self.route.adapter_id,
            conversation_id=self.route.conversation_id,
            capability_id="owner.read",
        )
        self.lease = self.lifecycle.admission.admit(self.view, "owner.read")

    async def _empty_config(self, module_id):
        from yomihime_game_link_sdk.services import ConfigSnapshot, ConfigTarget

        return validate_contract(
            ConfigSnapshot(
                1, {}, target=validate_contract(ConfigTarget("test-host", module_id))
            )
        )

    async def test_exact_direct_view_gets_private_gateway_result_and_release_revokes(
        self,
    ):
        gateway = Gateway(
            self.registry,
            self.issuer,
            admission=self.lifecycle.admission,
            lifecycle=self.lifecycle,
            owner_authority=self.authority,
        )

        result = await gateway.invoke_command(self.view, "read", {})

        self.assertEqual(result.status, ResultStatus.SUCCESS)
        self.assertIs(result.privacy, Privacy.PRIVATE)
        self.assertIs(result.document.privacy, Privacy.PRIVATE)
        self.assertEqual(self.handler.calls, 1)
        self.assertEqual(await self.authority.require_current(self.view), "principal-a")
        self.authority.release(self.view)
        with self.assertRaises(OwnerRouteProofError):
            await self.authority.require_current(self.view)

    async def test_reconstructed_or_changed_principal_or_route_is_rejected(self):
        await self.authority.capture(self.view, self.lease)

        from dataclasses import replace

        with self.assertRaises(OwnerRouteProofError):
            await self.authority.require_current(replace(self.view))

        self.principals.value = "principal-b"
        with self.assertRaises(OwnerRouteProofError):
            await self.authority.require_current(self.view)
        self.principals.value = "principal-a"
        self.routes.value = validate_contract(
            ConversationRef(
                self.route.adapter_id,
                self.route.kind,
                self.route.conversation_id,
                "changed-direct-route",
            )
        )
        with self.assertRaises(OwnerRouteProofError):
            await self.authority.require_current(self.view)

    async def test_group_route_is_refused_before_handler(self):
        self.routes.value = validate_contract(
            ConversationRef(
                self.route.adapter_id,
                ConversationKind.GROUP,
                self.route.conversation_id,
                "group:changed",
            )
        )
        gateway = Gateway(
            self.registry,
            self.issuer,
            admission=self.lifecycle.admission,
            lifecycle=self.lifecycle,
            owner_authority=self.authority,
        )

        result = await gateway.invoke_command(self.view, "read", {})

        self.assertIs(result.privacy, Privacy.PRIVATE)
        self.assertEqual(result.status, ResultStatus.ERROR)
        self.assertEqual(self.handler.calls, 0)

    async def test_owner_handler_cannot_weaken_result_to_public(self):
        self.handler.privacy = Privacy.PUBLIC
        gateway = Gateway(
            self.registry,
            self.issuer,
            admission=self.lifecycle.admission,
            lifecycle=self.lifecycle,
            owner_authority=self.authority,
        )

        result = await gateway.invoke_command(self.view, "read", {})

        self.assertEqual(result.status, ResultStatus.ERROR)
        self.assertIs(result.privacy, Privacy.PRIVATE)
        self.assertEqual(self.handler.calls, 1)


if __name__ == "__main__":
    unittest.main()
