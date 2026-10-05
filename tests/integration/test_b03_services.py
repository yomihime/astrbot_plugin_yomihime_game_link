from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from secrets import token_urlsafe
from uuid import uuid4

from ygl_test_subject.api.administration import AdminOperation
from ygl_test_subject.api.contexts import InvocationOrigin
from ygl_test_subject.api.display import DisplayDocument, Privacy, TextBlock
from ygl_test_subject.api.manifests import (
    CapabilityReference,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
)
from ygl_test_subject.api.results import CapabilityResult, ResultStatus
from ygl_test_subject.api.services import (
    CacheAccessRequest,
    ConfigFieldUpdate,
    ConfigPatch,
    ConfigPatchMode,
    ConfigTarget,
    ConversationKind,
    ConversationRef,
    Grant,
    GrantStatus,
    HttpRequest,
    LoginSessionStatus,
    ModuleHandlers,
    ResolvedIdentity,
    SubscriptionUnavailable,
)
from ygl_test_subject.api.storage import (
    CacheVisibility,
    GrantReference,
    OwnerScope,
    SecretRef,
)
from ygl_test_subject.api.subscriptions import CollectionKey, NormalizedInput
from ygl_test_subject.api.version import CONTRACT_VERSION
from ygl_test_subject.core.context_issuer import ContextIssuer
from ygl_test_subject.core.invocation import Gateway
from ygl_test_subject.core.ports import CollectionRunRequest, SecretOwner
from ygl_test_subject.core.registry import Registry
from ygl_test_subject.infrastructure.http import SourceHttpError
from ygl_test_subject.infrastructure.secret_store import SQLiteSecretStore
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories import SQLiteRepositories
from ygl_test_subject.infrastructure.sqlite.repositories_admin_credentials import (
    SQLiteAdminCredentialRepository,
)
from ygl_test_subject.infrastructure.sqlite.repositories_subscriptions import (
    SQLiteSchedulerRepository,
)
from ygl_test_subject.services.admin_authorization import (
    AdminAuthorizationService,
    _digest,
)
from ygl_test_subject.services.authorization import (
    AuthorizationPermissionError,
    AuthorizationService,
)
from ygl_test_subject.services.configuration import ConfigurationCoordinator
from ygl_test_subject.services.dependency_calls import DependencyInvoker
from ygl_test_subject.services.module_services import (
    InvocationBindingError,
    ModuleServicesFactory,
    UnavailableSubscriptionOperations,
)

from tests.contracts.test_context_issuer import _PublicWebProofs
from tests.fixtures.b03_runtime import build_runtime
from tests.fixtures.b04_runtime import (
    initialize_subscription_gate_fixture,
    replace_subscription_gate_fixture,
    synthetic_subscription_gate_bindings,
)


class B03ServiceAssemblyTests(unittest.IsolatedAsyncioTestCase):
    async def test_web_binder_real_services_dependencies_and_revoked_late_access(self):
        from tests.fixtures.b03_runtime import _Collector, _Handler, _manifest

        proofs = _PublicWebProofs()
        issuer = ContextIssuer(
            public_web_validator=proofs,
            public_web_capabilities=frozenset(
                {("sample/alpha", "read"), ("sample/beta", "read")}
            ),
        )
        registry = Registry()
        alpha = _manifest(
            "alpha",
            (
                CapabilityReference("sample/beta", "read"),
                CapabilityReference("sample/alpha", "private.read"),
            ),
        )
        beta = _manifest("beta")
        modules = tuple(
            replace(
                module,
                capabilities=tuple(
                    replace(
                        capability,
                        invocation_policy=InvocationPolicy.COMMAND_AND_PUBLIC_WEB,
                    )
                    if capability.capability_id == "read"
                    else capability
                    for capability in module.capabilities
                ),
            )
            for module in (alpha, beta)
        )
        handler = _Handler()
        registry.register_package(
            PackageManifest(
                "sample", "1.0.0", CONTRACT_VERSION, modules, "tests", "MIT", "offline"
            ),
            {
                "alpha": ModuleHandlers(
                    {"read": handler, "private.read": _Handler("private.read")},
                    {"account-collector": _Collector()},
                    {},
                ),
                "beta": ModuleHandlers({"read": handler}, {}, {}),
            },
        )
        runtime = await build_runtime(
            self.root / "web", registry=registry, issuer=issuer
        )
        try:
            module = registry.snapshot().module("sample/alpha")
            proof = proofs.new("sample/alpha", "read")
            view = issuer.issue_public_web(
                proof,
                module_id=module.module_id,
                capability_id="read",
                module_epoch=module.epoch,
                registry_revision=registry.snapshot().revision,
                generation=1,
                deadline=asyncio.get_running_loop().time() + 30,
            )
            runtime.lifecycle.admission.admit(view, "read")
            bound = await runtime.services.for_module(module.module_id).scopes.bind(
                view
            )
            response = await bound.http.fetch(HttpRequest("catalog", "/items"))
            self.assertEqual(response.status_code, 200)
            result = await bound.dependencies.invoke(
                view, CapabilityReference("sample/beta", "read"), {}
            )
            self.assertEqual(result.status, ResultStatus.SUCCESS)
            denied = await bound.dependencies.invoke(
                view, CapabilityReference("sample/alpha", "private.read"), {}
            )
            self.assertEqual(denied.status, ResultStatus.ERROR)
            self.assertIsNone(handler.calls[-1][0].actor_id)
            with self.assertRaises(InvocationBindingError):
                await runtime.services.for_module(module.module_id).scopes.bind(
                    replace(view)
                )
            issuer.release(view)
            self.assertIn(proof, proofs.revoked)
            with self.assertRaises(SourceHttpError):
                await bound.http.fetch(HttpRequest("catalog", "/items"))
            self.assertEqual(len(runtime.transport.requests), 1)
        finally:
            await runtime.services.close_credentials()
            await runtime.database.executor.close(timeout=1)

    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = await build_runtime(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _view(
        self,
        module_id: str = "sample/alpha",
        *,
        actor: str | None = "alice",
        origin: InvocationOrigin = InvocationOrigin.COMMAND,
        grant: Grant | None = None,
        capability_id: str | None = "read",
        conversation_id: str = "room",
    ):
        module = self.runtime.registry.snapshot().module(module_id)
        view = self.runtime.issuer.issue(
            origin=origin,
            module_id=module_id,
            module_epoch=module.epoch,
            registry_revision=self.runtime.registry.snapshot().revision,
            actor_id=actor,
            conversation_id=conversation_id if actor is not None else None,
            adapter_id="test-adapter" if actor is not None else None,
            capability_id=capability_id,
            grant_id=None if grant is None else grant.grant_id,
            grant_revision=None if grant is None else grant.revision,
        )
        if capability_id is not None and origin in (
            InvocationOrigin.COMMAND,
            InvocationOrigin.LLM_TOOL,
        ):
            try:
                self.runtime.lifecycle.admission.admit(view, capability_id)
            except Exception:
                # Negative tests retain the exact issuer view and prove that
                # the binder rejects an unavailable/undeclared capability.
                pass
        return view

    async def _restart_module(self, module_id: str) -> None:
        operation_id = uuid4().hex
        identity, _ = await self.runtime.lifecycle.start_candidate(
            module_id, operation_id
        )
        self.runtime.lifecycle.publish_committed_intent(
            module_id,
            operation_id,
            identity,
            True,
            self.runtime.registry.snapshot().revision,
        )

    async def _grant(
        self, grant_id: str = "grant-alice", actor: str = "alice"
    ) -> Grant:
        grant = Grant(
            grant_id,
            1,
            actor,
            "sample/alpha",
            f"account-{actor}",
            ("read", "private.read"),
            SecretRef(
                f"secret_{grant_id}",
                actor,
                "sample/alpha",
                "credential",
                "test_exchange",
            ),
            GrantStatus.ACTIVE,
        )
        await self.runtime.repositories.authorization.create_grant(
            grant, expected_revision=0
        )
        return grant

    def _factory(
        self, *, secret_available=None, module_host_config_snapshots=None
    ) -> ModuleServicesFactory:
        return ModuleServicesFactory(
            self.runtime.registry,
            self.runtime.issuer,
            self.runtime.lifecycle,
            self.runtime.repositories,
            self.runtime.transport,
            config_principal_id="host-config",
            identity_namespace="test-users",
            secret_available=secret_available,
            utc_clock=lambda: datetime.now(UTC),
            module_host_config_snapshots=module_host_config_snapshots,
        )

    async def test_host_defaults_compose_without_changing_core_revision_or_storage(
        self,
    ):
        core = await self.runtime.services.for_module("sample/alpha").config.current()
        requested = {"sample/alpha": {"host_days": 3}, "sample/beta": {"host_days": 10}}
        factory = self._factory(module_host_config_snapshots=requested)
        requested["sample/alpha"]["host_days"] = 20
        view = await factory.for_module("sample/alpha").config.current()
        self.assertEqual(view.values, {**core.values, "host_days": 3})
        self.assertEqual(view.revision, core.revision)
        self.assertEqual(view.secret_metadata, core.secret_metadata)
        self.assertEqual(view.target, core.target)
        self.assertEqual(
            (await factory.for_module("sample/beta").config.current()).values,
            {"host_days": 10},
        )
        with self.assertRaises(TypeError):
            view.values["host_days"] = 40
        self.assertEqual(
            await self.runtime.services.for_module("sample/alpha").config.current(),
            core,
        )
        self.assertNotIn(
            "host_days",
            (await self.runtime.repositories.config.current(core.target)).values,
        )

    async def test_host_config_collision_rejected_before_secret_service_or_module(self):
        factory = self._factory(
            module_host_config_snapshots={"sample/alpha": {"region": "cn"}}
        )
        with self.assertRaisesRegex(ValueError, "conflicts"):
            factory.for_module("sample/alpha")
        self.assertEqual(factory._credential_services, [])
        self.assertEqual(factory._http_by_module, {})

    async def _grant_with_secret(
        self,
        grant_id: str = "grant-alice",
        *,
        actor: str = "alice",
        expires_at=None,
    ) -> Grant:
        secret_owner = SecretOwner(
            actor, "sample/alpha", "credential", f"exchange-{grant_id}"
        )
        receipt = await self.runtime.repositories.secret_store.put(
            b"private-dependency-secret", owner=secret_owner
        )
        grant = Grant(
            grant_id,
            1,
            actor,
            "sample/alpha",
            f"account-{actor}",
            ("read", "private.read"),
            receipt.secret_ref,
            GrantStatus.ACTIVE,
            expires_at=expires_at,
        )
        await self.runtime.repositories.authorization.create_grant(
            grant, expected_revision=0
        )
        return grant

    async def _authorized_record_count(self) -> int:
        def read(unit):
            row = unit.execute(
                "SELECT COUNT(*) FROM module_records "
                "WHERE module_id=? AND collection=?",
                ("sample/alpha", "account_records"),
            ).fetchone()
            return int(row[0])

        return await self.runtime.database.executor.run_read(read)

    async def test_i01_i03_two_users_modules_and_invocation_scopes(self) -> None:
        alpha_services = self.runtime.services.for_module("sample/alpha")
        beta_services = self.runtime.services.for_module("sample/beta")
        self.assertNotIsInstance(
            alpha_services.subscriptions, UnavailableSubscriptionOperations
        )
        self.assertNotIsInstance(
            beta_services.subscriptions, UnavailableSubscriptionOperations
        )

        public_view = self._view(actor="alice", origin=InvocationOrigin.COMMAND)
        public = await alpha_services.scopes.bind(public_view)
        public_announcements = await public.records.collection("announcements")
        await public_announcements.create("release", {"visibility": "public"})

        alice_view = self._view(actor="alice")
        bob_view = self._view(actor="bob")
        alice = await alpha_services.scopes.bind(alice_view)
        bob = await alpha_services.scopes.bind(bob_view)
        self.assertIs(alice.invocation, alice_view)
        self.assertIs(bob.invocation, bob_view)
        await alice.cache.put("private", {"owner": "alice"}, ttl_seconds=120)
        await bob.cache.put("private", {"owner": "bob"}, ttl_seconds=120)
        self.assertEqual((await alice.cache.get("private")).payload["owner"], "alice")
        self.assertEqual((await bob.cache.get("private")).payload["owner"], "bob")
        alice_asset = await self._asset_id(b"alice-only", OwnerScope.user("alice"))
        bob_asset = await self._asset_id(b"bob-only", OwnerScope.user("bob"))
        await alice.resources.register(alice_asset, "text/plain", b"alice-only")
        await bob.resources.register(bob_asset, "text/plain", b"bob-only")
        self.assertEqual(await alice.resources.read(alice_asset), b"alice-only")
        self.assertEqual(await bob.resources.read(bob_asset), b"bob-only")
        with self.assertRaises(FileNotFoundError):
            await bob.resources.read(alice_asset)

        response = await alice.http.fetch(HttpRequest("catalog", "/items"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.runtime.transport.requests), 1)
        with self.assertRaises(SourceHttpError) as http_error:
            await alice.http.fetch(HttpRequest("undeclared", "/items"))
        self.assertEqual(http_error.exception.code, "source_not_declared")
        self.assertEqual(len(self.runtime.transport.requests), 1)

        alice_profiles = await alice.records.collection("profiles")
        bob_profiles = await bob.records.collection("profiles")
        beta_view = self._view(module_id="sample/beta", actor="alice")
        beta_handles = await beta_services.scopes.bind(beta_view)
        beta_profiles = await beta_handles.records.collection("profiles")
        await asyncio.gather(
            alice_profiles.create("same", {"owner": "alice"}),
            bob_profiles.create("same", {"owner": "bob"}),
            beta_profiles.create("same", {"owner": "beta"}),
        )
        self.assertEqual((await alice_profiles.get("same")).value["owner"], "alice")
        self.assertEqual((await bob_profiles.get("same")).value["owner"], "bob")
        self.assertEqual((await beta_profiles.get("same")).value["owner"], "beta")
        alice_announcements = await alice.records.collection("announcements")
        bob_announcements = await bob.records.collection("announcements")
        self.assertEqual(
            (await alice_announcements.get("release")).value["visibility"], "public"
        )
        self.assertEqual(
            (await bob_announcements.get("release")).value["visibility"], "public"
        )

        alice_grant = await self._grant()
        bob_grant = await self._grant("grant-bob", actor="bob")
        alice_granted = await alpha_services.scopes.bind(
            self._view(actor="alice", grant=alice_grant)
        )
        bob_granted = await alpha_services.scopes.bind(
            self._view(actor="bob", grant=bob_grant)
        )
        alice_granted_profiles = await alice_granted.records.collection("profiles")
        self.assertEqual(
            (await alice_granted_profiles.get("same")).value["owner"], "alice"
        )
        alice_account_records = await alice_granted.records.collection(
            "account_records"
        )
        bob_account_records = await bob_granted.records.collection("account_records")
        await alice_account_records.create("same", {"owner": "alice"})
        await bob_account_records.create("same", {"owner": "bob"})
        self.assertEqual(
            (await alice_account_records.get("same")).value["owner"], "alice"
        )
        self.assertEqual((await bob_account_records.get("same")).value["owner"], "bob")
        with self.assertRaises(FileNotFoundError):
            await bob.resources.read(alice_asset)
        with self.assertRaises(InvocationBindingError):
            await beta_services.scopes.bind(alice_view)

        # Dependency authority is bound to the issuer-carried caller capability.
        self.assertEqual(alice.dependencies.caller_capability.capability_id, "read")
        self.assertEqual(alice.dependencies.caller_capability.module_id, "sample/alpha")
        result = await alice.dependencies.invoke(
            alice_view,
            CapabilityReference("sample/beta", "read"),
            {},
        )
        self.assertEqual(result.status, ResultStatus.SUCCESS, result.error)
        issued_key = (alice_view.invocation_id, "read")
        self.assertIn(issued_key, self.runtime.services._caller_issuer._issued)
        self.runtime.issuer.release(alice_view)
        self.assertNotIn(issued_key, self.runtime.services._caller_issuer._issued)
        with self.assertRaises(InvocationBindingError):
            await alice.dependencies.invoke(
                alice_view,
                CapabilityReference("sample/beta", "read"),
                {},
            )

    async def test_user_and_authorized_record_owners_use_internal_principal(self):
        alpha_services = self.runtime.services.for_module("sample/alpha")
        external_view = self._view(actor="external-alice")
        external_handles = await alpha_services.scopes.bind(external_view)
        profiles = await external_handles.records.collection("profiles")
        await profiles.create("external-owner", {"owner": "principal-alice"})
        self.assertEqual(
            (await profiles.get("external-owner")).value["owner"],
            "principal-alice",
        )

        grant = await self._grant("grant-external-alice", actor="principal-alice")
        authorized_view = self._view(actor="external-alice", grant=grant)
        authorized_handles = await alpha_services.scopes.bind(authorized_view)
        records = await authorized_handles.records.collection("account_records")
        await records.create("external-grant", {"owner": "principal-alice"})
        self.assertEqual(
            (await records.get("external-grant")).value["owner"],
            "principal-alice",
        )

    async def test_i02_private_command_requires_current_identity_grant_and_secret(self):
        gateway = Gateway(
            self.runtime.registry,
            self.runtime.issuer,
            admission=self.runtime.lifecycle.admission,
            lifecycle=self.runtime.lifecycle,
            private_authorizer=self.runtime.services.authorize_private,
        )
        private_handler = (
            self.runtime.registry.snapshot()
            .module("sample/alpha")
            .handlers.capabilities["private.read"]
        )

        no_grant = self._view(capability_id="private.read")
        denied = await gateway.invoke_command(no_grant, "private", {})
        self.assertEqual(denied.error.code.value, "module_unavailable")
        self.assertEqual(private_handler.calls, [])

        grant = await self._grant()
        wrong_owner = self._view(actor="bob", grant=grant, capability_id="private.read")
        denied = await gateway.invoke_command(wrong_owner, "private", {})
        self.assertEqual(denied.error.code.value, "module_unavailable")
        self.assertEqual(private_handler.calls, [])

        authorized = self._view(
            actor="alice", grant=grant, capability_id="private.read"
        )
        result = await gateway.invoke_command(authorized, "private", {})
        self.assertEqual(result.status, ResultStatus.SUCCESS)
        self.assertEqual(result.privacy, Privacy.PRIVATE)
        self.assertEqual(result.document.privacy, Privacy.PRIVATE)
        self.assertEqual(len(private_handler.calls), 1)

    async def test_private_same_module_dependency_uses_real_factory_grant(self):
        grant = await self._grant_with_secret("grant-private-dependency")
        factory = self._factory()
        root = self._view(grant=grant, capability_id="read")
        services = await factory.for_module("sample/alpha").scopes.bind(root)
        root_revision = root.registry_revision

        # A publication in an unrelated package advances the registry's
        # global revision. It must not invalidate the exact root/child lease
        # chain for this still-current module invocation.
        unrelated = ModuleManifest(
            "noise",
            "noise",
            ModuleCategory.PLATFORM,
            "tests.module:Factory",
            "1.0.0",
            (),
        )
        self.runtime.registry.register_package(
            PackageManifest(
                "unrelated",
                "1.0.0",
                CONTRACT_VERSION,
                (unrelated,),
                "Tests",
                "MIT",
                "unrelated registry revision regression",
            ),
            {"noise": ModuleHandlers({}, {}, {})},
        )
        self.assertGreater(self.runtime.registry.snapshot().revision, root_revision)
        private_handler = (
            self.runtime.registry.snapshot()
            .module("sample/alpha")
            .handlers.capabilities["private.read"]
        )

        try:
            result = await services.dependencies.invoke(
                root,
                CapabilityReference("sample/alpha", "private.read"),
                {},
            )
            self.assertEqual(result.status, ResultStatus.SUCCESS)
            self.assertEqual(result.privacy, Privacy.PRIVATE)
            self.assertEqual(result.document.privacy, Privacy.PRIVATE)
            self.assertEqual(len(private_handler.calls), 1)
        finally:
            self.runtime.issuer.release(root)

    async def test_cross_module_public_dependency_drops_grant_authority(self):
        grant = await self._grant_with_secret("grant-cross-module-public")
        factory = self._factory()
        root = self._view(grant=grant, capability_id="read")
        root_services = await factory.for_module("sample/alpha").scopes.bind(root)
        beta_services = factory.for_module("sample/beta")
        handler = self.runtime.handler
        original_invoke = handler.invoke
        observations: dict[str, object] = {}

        async def bind_public_child(child, parameters):
            bound = await beta_services.scopes.bind(child)
            profiles = await bound.records.collection("profiles")
            await profiles.create("cross-module-public", {"owner": "alice"})
            observations["public_record"] = (
                await profiles.get("cross-module-public")
            ).value["owner"]

            with self.assertRaises(InvocationBindingError):
                await bound.records.collection("account_records")
            authorized_cache = CacheAccessRequest(
                "private-entry",
                CacheVisibility.AUTHORIZED,
                OwnerScope.authorized(
                    "alice", GrantReference(grant.grant_id, grant.revision)
                ),
            )
            with self.assertRaises(PermissionError):
                await bound.cache.lookup(authorized_cache)

            private = await bound.dependencies.invoke(
                child, CapabilityReference("sample/alpha", "private.read"), {}
            )
            observations["private_dependency"] = private.error.code.value
            return await original_invoke(child, parameters)

        handler.invoke = bind_public_child
        try:
            result = await root_services.dependencies.invoke(
                root, CapabilityReference("sample/beta", "read"), {}
            )
        finally:
            handler.invoke = original_invoke
            self.runtime.issuer.release(root)

        self.assertEqual(result.status, ResultStatus.SUCCESS)
        self.assertEqual(result.privacy, Privacy.PUBLIC)
        self.assertEqual(observations["public_record"], "alice")
        self.assertEqual(observations["private_dependency"], "module_unavailable")

    async def test_private_dependency_child_binds_and_uses_real_services(self):
        grant = await self._grant_with_secret("grant-private-child-services")
        factory = self._factory()
        module_services = factory.for_module("sample/alpha")
        root = self._view(grant=grant, capability_id="read")
        root_services = await module_services.scopes.bind(root)
        private_handler = (
            self.runtime.registry.snapshot()
            .module("sample/alpha")
            .handlers.capabilities["private.read"]
        )
        original_invoke = private_handler.invoke
        observations: dict[str, object] = {}

        async def bind_and_use(child, parameters):
            child_services = await factory.for_module("sample/alpha").scopes.bind(child)
            profiles = await child_services.records.collection("profiles")
            await profiles.create("child-private", {"owner": "alice"})
            stored = await profiles.get("child-private")
            observations["record_owner"] = stored.value["owner"]
            account_records = await child_services.records.collection("account_records")
            await account_records.create("child-private-authorized", {"owner": "alice"})
            authorized_record = await account_records.get("child-private-authorized")
            observations["authorized_record_owner"] = authorized_record.value["owner"]
            with self.assertRaises(InvocationBindingError):
                await module_services.accounts.begin_login(child)
            with self.assertRaises(InvocationBindingError):
                await module_services.subscriptions.list_current(child)
            observations["management_denied"] = True
            return await original_invoke(child, parameters)

        private_handler.invoke = bind_and_use
        try:
            result = await root_services.dependencies.invoke(
                root,
                CapabilityReference("sample/alpha", "private.read"),
                {},
            )
        finally:
            private_handler.invoke = original_invoke
            self.runtime.issuer.release(root)

        self.assertEqual(result.status, ResultStatus.SUCCESS)
        self.assertEqual(result.privacy, Privacy.PRIVATE)
        self.assertEqual(observations["record_owner"], "alice")
        self.assertEqual(observations["authorized_record_owner"], "alice")
        self.assertTrue(observations["management_denied"])

    async def test_scheduled_claim_and_proof_authorize_persisted_grant_records(self):
        grant = await self._grant_with_secret("grant-scheduled-records")
        snapshot = self.runtime.registry.snapshot()
        module = snapshot.module("sample/alpha")
        now = datetime.now(UTC)
        key = CollectionKey(
            "sample/alpha",
            "account-collector",
            1,
            "catalog",
            NormalizedInput({}),
            OwnerScope.authorized(
                grant.principal_id, GrantReference(grant.grant_id, grant.revision)
            ),
        )
        request = CollectionRunRequest(
            key,
            now - timedelta(seconds=1),
            60.0,
            1,
            module.epoch,
            snapshot.revision,
        )
        # This historical B03 assembly has no Host gate composition. Prove the
        # default rejection, then explicitly compose the test-only persisted gate.
        self.assertIsNone(
            await self.runtime.scheduler_repository.claim_due(request, now=now)
        )
        bindings = synthetic_subscription_gate_bindings(("sample/alpha",))
        await initialize_subscription_gate_fixture(self.runtime.database, bindings, now)
        scheduler = SQLiteSchedulerRepository(
            self.runtime.database, subscription_gate_bindings=bindings
        )
        execution = await scheduler.claim_due(request, now=now)
        self.assertIsNotNone(execution)
        self.runtime.execution_claim_proofs.prove(execution)
        scheduled_lease = self.runtime.lifecycle.admission.admit_schedule(
            execution, "account-collector"
        )
        invocation = self.runtime.issuer.issue(
            origin=InvocationOrigin.SCHEDULER,
            module_id=module.module_id,
            module_epoch=module.epoch,
            registry_revision=snapshot.revision,
            actor_id=grant.principal_id,
            grant_id=grant.grant_id,
            grant_revision=grant.revision,
            capability_id=None,
        )
        self.runtime.issuer.attach_lease(invocation, scheduled_lease)

        try:
            bound = await self.runtime.services.for_module("sample/alpha").scopes.bind(
                invocation
            )
            records = await bound.records.collection("account_records")
            await records.create("scheduled-authorized", {"owner": "alice"})
            stored = await records.get("scheduled-authorized")
            self.assertEqual(stored.value["owner"], "alice")
        finally:
            self.runtime.issuer.release(invocation)

        await replace_subscription_gate_fixture(
            self.runtime.database, bindings, "sample/alpha", False, now
        )
        self.assertFalse(await scheduler.is_current(execution, now=now))
        self.assertIsNone(await scheduler.claim_due(request, now=now))

    async def test_authorized_records_reject_expired_wrong_source_and_release_without_writes(
        self,
    ):
        service = self.runtime.services.for_module("sample/alpha").scopes
        active = await self._grant_with_secret("grant-authorized-negative")
        expired = await self._grant_with_secret(
            "grant-authorized-expired",
            actor="bob",
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )

        async def assert_record_access_denied(view):
            try:
                bound = await service.bind(view)
            except InvocationBindingError:
                pass
            else:
                with self.assertRaises(InvocationBindingError):
                    await bound.records.collection("account_records")
            finally:
                self.runtime.issuer.release(view)
            self.assertEqual(await self._authorized_record_count(), 0)

        await assert_record_access_denied(self._view(actor="bob", grant=expired))
        await assert_record_access_denied(self._view(actor="bob", grant=active))
        await assert_record_access_denied(
            self._view(origin=InvocationOrigin.LLM_TOOL, grant=active)
        )

        released = self._view(grant=active)
        bound = await service.bind(released)
        self.runtime.issuer.release(released)
        with self.assertRaises(InvocationBindingError):
            await bound.records.collection("account_records")
        self.assertEqual(await self._authorized_record_count(), 0)

    async def test_private_child_service_and_dependency_reject_revoked_grant(self):
        grant = await self._grant_with_secret("grant-private-child-revoke")
        factory = self._factory()
        root = self._view(grant=grant, capability_id="read")
        caller = factory._caller_issuer.issue(root, "read")
        authorization = factory._new_authorization()
        invoker = DependencyInvoker(
            self.runtime.registry,
            self.runtime.issuer,
            self.runtime.lifecycle,
            factory._caller_issuer,
            caller,
            private_authorizer=authorization.require_dependency_grant,
        )
        private_handler = (
            self.runtime.registry.snapshot()
            .module("sample/alpha")
            .handlers.capabilities["private.read"]
        )
        original_invoke = private_handler.invoke
        observations: dict[str, object] = {}

        async def use_then_revoke(child, parameters):
            child_services = await factory.for_module("sample/alpha").scopes.bind(child)
            profiles = await child_services.records.collection("profiles")
            await profiles.create("before-revoke", {"owner": "alice"})
            current = await self.runtime.repositories.authorization.current_grant(
                grant.grant_id
            )
            await self.runtime.repositories.authorization.revoke_grant(
                current, expected_revision=current.revision
            )
            with self.assertRaises(InvocationBindingError):
                await profiles.get("before-revoke")
            with self.assertRaises(InvocationBindingError):
                await child_services.records.collection("account_records")
            observations["service_denied_after_revoke"] = True
            return await original_invoke(child, parameters)

        private_handler.invoke = use_then_revoke
        try:
            result = await invoker.invoke(
                root, CapabilityReference("sample/alpha", "private.read"), {}
            )
        finally:
            private_handler.invoke = original_invoke
            self.runtime.issuer.release(root)

        self.assertEqual(result.status, ResultStatus.ERROR)
        self.assertEqual(result.error.code.value, "module_unavailable")
        self.assertTrue(observations["service_denied_after_revoke"])
        self.assertEqual(await self._authorized_record_count(), 0)

    async def test_bound_dependency_normalizes_privacy_validation_error(self):
        factory = self._factory()
        root = self._view(module_id="sample/alpha", capability_id="read")
        module_services = await factory.for_module("sample/alpha").scopes.bind(root)
        handler = self.runtime.handler
        original_invoke = handler.invoke

        async def returns_private_result(invocation, parameters):
            return CapabilityResult(
                "unexpected-private",
                ResultStatus.SUCCESS,
                document=DisplayDocument(
                    "private",
                    "subject",
                    (TextBlock("sensitive"),),
                    privacy=Privacy.PRIVATE,
                ),
                privacy=Privacy.PRIVATE,
            )

        handler.invoke = returns_private_result
        try:
            result = await module_services.dependencies.invoke(
                root, CapabilityReference("sample/beta", "read"), {}
            )
        finally:
            handler.invoke = original_invoke
            self.runtime.issuer.release(root)

        self.assertEqual(result.status, ResultStatus.ERROR)
        self.assertEqual(result.error.code.value, "unknown")
        self.assertEqual(result.error.message, "invocation failed")
        self.assertNotIn("privacy", result.error.message)

    async def test_command_grant_revoke_during_secret_await_blocks_handler(self):
        grant = await self._grant("grant-command-race")

        async def revoke_during_secret_read(current: Grant) -> bool:
            await self.runtime.repositories.authorization.revoke_grant(
                current, expected_revision=current.revision
            )
            return True

        factory = self._factory(secret_available=revoke_during_secret_read)
        private_handler = (
            self.runtime.registry.snapshot()
            .module("sample/alpha")
            .handlers.capabilities["private.read"]
        )
        invocation = self._view(grant=grant, capability_id="private.read")
        gateway = Gateway(
            self.runtime.registry,
            self.runtime.issuer,
            admission=self.runtime.lifecycle.admission,
            lifecycle=self.runtime.lifecycle,
            private_authorizer=factory.authorize_private,
        )

        try:
            result = await gateway.invoke_command(invocation, "private", {})
        finally:
            self.runtime.issuer.release(invocation)

        self.assertEqual(result.error.code.value, "module_unavailable")
        self.assertEqual(private_handler.calls, [])
        current = await self.runtime.repositories.authorization.current_grant(
            grant.grant_id
        )
        self.assertEqual(current.status, GrantStatus.REVOKED)
        self.assertEqual(current.revision, grant.revision + 1)

    async def test_private_dependency_rechecks_grant_after_handler_await(self):
        grant = await self._grant_with_secret("grant-dependency-race")
        factory = self._factory()
        root = self._view(grant=grant, capability_id="read")
        caller = factory._caller_issuer.issue(root, "read")
        authorization = factory._new_authorization()
        invoker = DependencyInvoker(
            self.runtime.registry,
            self.runtime.issuer,
            self.runtime.lifecycle,
            factory._caller_issuer,
            caller,
            private_authorizer=authorization.require_dependency_grant,
        )
        private_handler = (
            self.runtime.registry.snapshot()
            .module("sample/alpha")
            .handlers.capabilities["private.read"]
        )
        original_invoke = private_handler.invoke

        async def revoke_then_invoke(invocation, parameters):
            current = await self.runtime.repositories.authorization.current_grant(
                grant.grant_id
            )
            await self.runtime.repositories.authorization.revoke_grant(
                current, expected_revision=current.revision
            )
            return await original_invoke(invocation, parameters)

        private_handler.invoke = revoke_then_invoke
        try:
            result = await invoker.invoke(
                root, CapabilityReference("sample/alpha", "private.read"), {}
            )
        finally:
            self.runtime.issuer.release(root)

        self.assertEqual(result.status, ResultStatus.ERROR)
        self.assertEqual(result.error.code.value, "module_unavailable")
        self.assertEqual(len(private_handler.calls), 1)

    async def test_child_authorization_does_not_open_management_or_tool_paths(self):
        grant = await self._grant("grant-management-child")
        factory = self._factory(secret_available=lambda current: True)
        root = self._view(grant=grant, capability_id="read")
        child = self.runtime.issuer.derive(
            root,
            module_id=root.module_id,
            module_epoch=root.module_epoch,
            capability_id="private.read",
        )
        self.runtime.lifecycle.admission.admit(child, "private.read")
        authorization = factory._new_authorization()
        try:
            with self.assertRaises(AuthorizationPermissionError):
                await authorization.begin_login(child)
        finally:
            self.runtime.issuer.release(child)
            self.runtime.issuer.release(root)

        tool = self._view(
            origin=InvocationOrigin.LLM_TOOL,
            capability_id="read",
        )
        services = await factory.for_module("sample/alpha").scopes.bind(tool)
        result = await services.dependencies.invoke(
            tool,
            CapabilityReference("sample/alpha", "private.read"),
            {},
        )
        self.assertEqual(result.status, ResultStatus.ERROR)
        self.assertEqual(result.error.code.value, "module_unavailable")
        self.runtime.issuer.release(tool)

    async def test_i02_trusted_origins_and_unavailable_subscription_side_effects(self):
        module_services = self.runtime.services.for_module("sample/alpha")
        issued = self._view(actor="alice")
        before = {
            table: self._count(table)
            for table in ("grants", "login_sessions", "subscriptions")
            if self._table_exists(table)
        }
        subscription_api = module_services.subscriptions
        methods = (
            subscription_api.create(issued, None),
            subscription_api.revise(issued, None),
            subscription_api.list_current(issued),
            subscription_api.cancel(issued, "sub-1", expected_revision=1),
        )
        for method in methods:
            with self.assertRaises(SubscriptionUnavailable) as caught:
                await method
            self.assertEqual(caught.exception.code, "subscription_unavailable")

        tool = self._view(origin=InvocationOrigin.LLM_TOOL)
        tool_methods = (
            subscription_api.create(tool, None),
            subscription_api.revise(tool, None),
            subscription_api.list_current(tool),
            subscription_api.cancel(tool, "sub-1", expected_revision=1),
        )
        for method in tool_methods:
            with self.assertRaises(PermissionError):
                await method
        after = {table: self._count(table) for table in before}
        self.assertEqual(after, before)

        tool_view = self._view(origin=InvocationOrigin.LLM_TOOL)
        with self.assertRaises(InvocationBindingError):
            await module_services.accounts.bindings(tool_view)
        with self.assertRaises(InvocationBindingError):
            await module_services.scopes.bind(self._view(capability_id=None))
        with self.assertRaises(InvocationBindingError):
            await module_services.scopes.bind(self._view(capability_id="undeclared"))

        grant = await self._grant()
        mismatched_actor = self._view(actor="bob", grant=grant)
        with self.assertRaises(InvocationBindingError):
            await module_services.scopes.bind(mismatched_actor)

        # A structurally identical view from a different issuer is not authority.
        other_runtime = await build_runtime(self.root / "other")
        forged = other_runtime.issuer.issue(
            origin=issued.origin,
            module_id=issued.module_id,
            module_epoch=issued.module_epoch,
            registry_revision=issued.registry_revision,
            actor_id=issued.actor_id,
            conversation_id=issued.conversation_id,
            adapter_id=issued.adapter_id,
            capability_id=issued.capability_id,
        )
        with self.assertRaises(InvocationBindingError):
            await module_services.scopes.bind(forged)

        stale_revision = self._view()
        await self.runtime.lifecycle.stop("sample/beta")
        with self.assertRaises(InvocationBindingError):
            await module_services.scopes.bind(stale_revision)

    async def test_i03_grant_revoke_disable_and_release_reject_late_calls(self):
        service = self.runtime.services.for_module("sample/alpha")
        grant = await self._grant()
        invocation = self._view(grant=grant)
        bound = await service.scopes.bind(invocation)
        await bound.cache.put("authorized", {"value": 1}, ttl_seconds=120)
        authorized_asset = await self._asset_id(
            b"grant-private",
            OwnerScope.authorized("alice", GrantReference(grant.grant_id, 1)),
        )
        await bound.resources.register(authorized_asset, "text/plain", b"grant-private")
        async with self.runtime.lifecycle.admission.mutation("test-revoke-grant"):
            await self.runtime.repositories.authorization.revoke_grant(
                grant, expected_revision=1
            )
        with self.assertRaises(InvocationBindingError):
            await bound.cache.get("authorized")
        with self.assertRaises(InvocationBindingError):
            await bound.resources.read(authorized_asset)

        active = self._view()
        active_handles = await service.scopes.bind(active)
        profiles = await active_handles.records.collection("profiles")
        await self.runtime.lifecycle.stop("sample/alpha")
        with self.assertRaises(InvocationBindingError):
            await active_handles.records.collection("profiles")
        with self.assertRaises(InvocationBindingError):
            await profiles.get("late")

        # A fresh epoch can start, but a released invocation stays unusable.
        await self._restart_module("sample/alpha")
        released = self._view()
        released_handles = await service.scopes.bind(released)
        self.runtime.issuer.release(released)
        with self.assertRaises(InvocationBindingError):
            await released_handles.resources.read(
                await self._asset_id(b"shared-content", OwnerScope.user("alice"))
            )

        # Stopping the exact Lifecycle-owned beta instance fences its handle.
        beta_service = self.runtime.services.for_module("sample/beta")
        beta_view = self._view(module_id="sample/beta")
        beta_handles = await beta_service.scopes.bind(beta_view)
        beta_profiles = await beta_handles.records.collection("profiles")
        await self.runtime.lifecycle.stop("sample/beta")
        with self.assertRaises(InvocationBindingError):
            await beta_profiles.get("late")

    async def test_i03_inflight_dependency_result_is_rejected_after_revoke(self):
        service = self.runtime.services.for_module("sample/alpha")
        grant = await self._grant()
        invocation = self._view(grant=grant)
        bound = await service.scopes.bind(invocation)
        self.runtime.handler.block = True

        call = asyncio.create_task(
            bound.dependencies.invoke(
                invocation,
                CapabilityReference("sample/beta", "read"),
                {},
            )
        )
        await asyncio.wait_for(self.runtime.handler.started.wait(), timeout=2)
        async with self.runtime.lifecycle.admission.mutation("test-inflight-revoke"):
            await self.runtime.repositories.authorization.revoke_grant(
                grant, expected_revision=grant.revision
            )
        self.runtime.handler.release.set()
        with self.assertRaises(InvocationBindingError):
            await call

    async def test_i05_sqlite_restart_persists_state_and_expires_pending_sessions(self):
        old_epoch_view = self._view()
        repo = self.runtime.repositories
        async with self.runtime.lifecycle.admission.mutation("test-identity-bootstrap"):
            identity = await repo.identities.save_identity(
                "alice", ResolvedIdentity("identity-alice", "steam", "subject-alice")
            )
        from ygl_test_subject.services.identity import TrustedRoutePublisher

        publisher = TrustedRoutePublisher(
            repo.conversations, self.runtime.lifecycle.admission
        )
        route = await publisher.publish(
            ConversationRef(
                "test-adapter", ConversationKind.DIRECT, "direct-room", "route"
            )
        )
        self.assertEqual(route.conversation_id, "direct-room")
        command = self._view(actor="alice", conversation_id="direct-room")
        account = self.runtime.services.for_module("sample/alpha").accounts
        saved_binding = await account.bind(command, identity)
        grant = await self._grant()

        config_target = ConfigTarget("host-config", "sample/alpha")
        fields = (
            self.runtime.registry.snapshot()
            .module("sample/alpha")
            .manifest.config_fields
        )
        credential_repository = SQLiteAdminCredentialRepository(repo.database)
        admin_secret = token_urlsafe(32)
        await credential_repository.bootstrap(_digest(admin_secret))
        admin_context = type(
            "TrustedAdminContext",
            (),
            {"adapter_id": "test", "request_id": "request", "session_id": "i05"},
        )()
        admin_authorization = AdminAuthorizationService(
            credential_repository,
            admission=self.runtime.lifecycle.admission,
            context_validator=lambda _operation, _invocation, context, _generation: (
                context is admin_context
            ),
        )
        admin_grant = await admin_authorization.authorize(
            AdminOperation.UPDATE_CONFIG,
            invocation=None,
            context=admin_context,
        )
        coordinator = ConfigurationCoordinator(
            config_target,
            fields,
            repo.config,
            repo.secret_store,
            admission=self.runtime.lifecycle.admission,
            validate_admin_grant=lambda grant: admin_authorization.validate_generation(
                grant, operation=AdminOperation.UPDATE_CONFIG
            ),
            publish_config=self.runtime.health.publish_config,
        )
        current = await coordinator.current()
        health_before_update = self.runtime.health.current("sample/alpha", "read")
        updated = await coordinator.update_admin(
            config_target,
            ConfigPatch(
                current.revision,
                (ConfigFieldUpdate("region", ConfigPatchMode.REPLACE, "eu-west"),),
                declared_fields=fields,
            ),
            admin_grant,
        )
        self.assertEqual(updated.values["region"], "eu-west")
        self.assertEqual(
            self.runtime.health.current("sample/alpha", "read"), health_before_update
        )
        async with self.runtime.lifecycle.admission.mutation("test-pending-session"):
            pending = await repo.authorization.next_generation(
                "alice", "sample/alpha", expected_generation=0
            )

        async with self.runtime.lifecycle.admission.mutation("test-unfinished-config"):
            transaction = await repo.database.begin()
            transaction.connection.execute(
                "INSERT INTO config_state(principal_id,module_id,revision) VALUES(?,?,?)",
                ("unfinished", "sample/alpha", 1),
            )
            await transaction.close()

        # The reopened graph must not admit an invocation from the prior epoch.
        await self.runtime.lifecycle.stop("sample/alpha")
        await self._restart_module("sample/alpha")

        reopened_db = SQLiteDatabase(self.root / "runtime.sqlite3")
        reopened_files = self.runtime.repositories.resources.file_store
        reopened_secret = SQLiteSecretStore(
            reopened_db,
            self.root / "secrets",
            codec=self.runtime.repositories.secret_store.codec,
        )
        reopened_repositories = SQLiteRepositories(
            reopened_db,
            self.runtime.lookup,
            file_store=reopened_files,
            secret_store=reopened_secret,
        )
        await AuthorizationService(
            reopened_repositories.authorization,
            reopened_repositories.authorization,
            self.runtime.issuer,
            admission=self.runtime.lifecycle.admission,
        ).restart()
        reopened_services = ModuleServicesFactory(
            self.runtime.registry,
            self.runtime.issuer,
            self.runtime.lifecycle,
            reopened_repositories,
            self.runtime.transport,
            config_principal_id="host-config",
            identity_namespace="test-users",
            secret_available=lambda current_grant: current_grant.secret_ref is not None,
        )
        self.assertEqual(
            (
                await reopened_services.for_module("sample/alpha").config.current()
            ).values["region"],
            "eu-west",
        )
        restored_binding = await reopened_repositories.bindings.current(
            saved_binding.binding_id
        )
        self.assertEqual(restored_binding.revision, saved_binding.revision)
        self.assertEqual(restored_binding.principal_id, "alice")
        self.assertTrue(restored_binding.is_default)
        self.assertEqual(
            await reopened_repositories.authorization.current_grant(grant.grant_id),
            grant,
        )
        self.assertEqual(
            (
                await reopened_repositories.authorization.current(pending.session_id)
            ).status,
            LoginSessionStatus.EXPIRED,
        )
        with self.assertRaises(InvocationBindingError):
            await reopened_services.for_module("sample/alpha").scopes.bind(
                old_epoch_view
            )
        connection = reopened_repositories.database.connect()
        try:
            unfinished = connection.execute(
                "SELECT 1 FROM config_state WHERE principal_id='unfinished' "
                "AND module_id='sample/alpha'"
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNone(unfinished)

    def _table_exists(self, table: str) -> bool:
        connection = self.runtime.repositories.database.connect()
        try:
            return (
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                is not None
            )
        finally:
            connection.close()

    def _count(self, table: str) -> int:
        connection = self.runtime.repositories.database.connect()
        try:
            return int(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            )
        finally:
            connection.close()

    async def _asset_id(self, content: bytes, scope: OwnerScope) -> str:
        files = self.runtime.repositories.resources.file_store
        stage = await files.stage("asset-id-probe", content, scope)
        await files.discard(stage)
        return stage.asset_id


__all__ = ["B03ServiceAssemblyTests"]
