from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from unittest.mock import patch
from uuid import uuid4

from ygl_test_subject.core.admission import AdmissionError
from ygl_test_subject.core.context_issuer import ContextIssuer
from ygl_test_subject.core.contracts.services import (
    CacheAccessRequest,
    Grant,
    GrantStatus,
    Principal,
)
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.lifecycle import LifecycleController
from ygl_test_subject.core.ports import ExecutionLease, RevisionConflict
from ygl_test_subject.core.registry import Registry
from ygl_test_subject.infrastructure.files import LocalSafeFileStore
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories_auth import SQLiteGrantStore
from ygl_test_subject.infrastructure.sqlite.repositories_cache_resources import (
    SQLiteCacheRepository,
    SQLiteResourceRepository,
)
from ygl_test_subject.infrastructure.sqlite.repositories_identity import (
    SQLiteIdentityRepository,
)
from ygl_test_subject.services import resources as resource_service_module
from ygl_test_subject.services.cache import CacheAccessService
from ygl_test_subject.services.identity import InvocationPrincipalResolver
from ygl_test_subject.services.resources import (
    ResourceAccessError,
    ResourceAccessService,
)

import yomihime_game_link_sdk as ygl
from yomihime_game_link_sdk.contexts import InvocationOrigin
from yomihime_game_link_sdk.declarations import (
    CapabilityDescriptor,
    CapabilityEffect,
    CommandDescriptor,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
)
from yomihime_game_link_sdk.services import (
    CapabilityHealth,
    HealthReport,
    HealthStatus,
    ModuleHandlers,
)
from yomihime_game_link_sdk.storage import (
    CacheVisibility,
    GrantReference,
    OwnerScope,
    OwnershipKind,
)
from yomihime_game_link_sdk.subscriptions import (
    CollectionKey,
    NormalizedInput,
    ScheduleDescriptor,
    ScheduleTrigger,
)
from yomihime_game_link_sdk.version import MODULE_ABI_VERSION


class _Handler:
    async def invoke(self, context, parameters):
        return None


class _Collector:
    def normalize(self, parameters):
        return None

    async def collect(self, context, parameters, previous):
        return None


class _Instance:
    def __init__(self, handlers):
        self._handlers = handlers

    def handlers(self):
        return self._handlers

    async def start(self):
        return None

    async def stop(self):
        return None

    async def check_health(self):
        return validate_contract(
            HealthReport(
                {"read": validate_contract(CapabilityHealth(HealthStatus.AVAILABLE))}
            )
        )


class _PausedIdentityRepository:
    def __init__(self, repository) -> None:
        self.repository = repository
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    def __getattr__(self, name):
        return getattr(self.repository, name)

    async def find_principal(self, namespace, external_user_id):
        self.entered.set()
        await self.release.wait()
        return await self.repository.find_principal(namespace, external_user_id)


class _ObservedMethod:
    def __init__(
        self,
        delegate,
        method: str,
        *,
        events: list[str] | None = None,
        label: str | None = None,
        pause: bool = False,
        pause_after: bool = False,
    ) -> None:
        self.delegate = delegate
        self.method = method
        self.events = events if events is not None else []
        self.label = label or method
        self.pause = pause
        self.pause_after = pause_after
        self.started = asyncio.Event()
        self.resume = asyncio.Event()

    def __getattr__(self, name: str):
        if name == self.method:
            return self._invoke
        return getattr(self.delegate, name)

    async def _invoke(self, *args, **kwargs):
        if self.pause and not self.pause_after:
            self.started.set()
            await self.resume.wait()
        result = await getattr(self.delegate, self.method)(*args, **kwargs)
        self.events.append(self.label)
        if self.pause and self.pause_after:
            self.started.set()
            await self.resume.wait()
        return result


class _ControlledConfirmFileStore:
    def __init__(
        self,
        delegate,
        *,
        pause_before: bool = False,
        pause_after: bool = False,
        fail: bool = False,
    ) -> None:
        self.delegate = delegate
        self.pause_before = pause_before
        self.pause_after = pause_after
        self.fail = fail
        self.started = asyncio.Event()
        self.resume = asyncio.Event()

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)

    async def confirm(self, metadata):
        if self.pause_before:
            self.started.set()
            await self.resume.wait()
        if self.fail:
            raise RuntimeError("confirmation failed")
        result = await self.delegate.confirm(metadata)
        if self.pause_after:
            self.started.set()
            await self.resume.wait()
        return result


class _CancelAfterStageFileStore:
    def __init__(self, delegate) -> None:
        self.delegate = delegate

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)

    async def stage(self, *args, **kwargs):
        stage = await self.delegate.stage(*args, **kwargs)
        task = asyncio.current_task()
        assert task is not None
        asyncio.get_running_loop().call_soon(task.cancel)
        return stage


class _ObservedResourceRepository:
    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.registration_state_calls = 0

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)

    async def registration_state(self, asset_id, scope):
        self.registration_state_calls += 1
        return await self.delegate.registration_state(asset_id, scope)


class _DeadlineRecordingRepository:
    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.deadlines: list[datetime | None] = []

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)

    async def put(self, *args, **kwargs):
        self.deadlines.append(kwargs.get("authorization_deadline"))
        return await self.delegate.put(*args, **kwargs)

    async def invalidate(self, *args, **kwargs):
        self.deadlines.append(kwargs.get("authorization_deadline"))
        return await self.delegate.invalidate(*args, **kwargs)

    async def register(self, *args, **kwargs):
        self.deadlines.append(kwargs.get("authorization_deadline"))
        return await self.delegate.register(*args, **kwargs)

    async def delete(self, *args, **kwargs):
        self.deadlines.append(kwargs.get("authorization_deadline"))
        return await self.delegate.delete(*args, **kwargs)


class _SwitchableDateTime(datetime):
    expired_at: datetime | None = None

    @classmethod
    def now(cls, tz=None):
        if cls.expired_at is None:
            return datetime.now(tz)
        value = cls.expired_at
        return value if tz is None else value.astimezone(tz)


def _package() -> tuple[PackageManifest, ModuleHandlers]:
    manifest = validate_contract(
        ModuleManifest(
            module_id="cache",
            route="cache",
            category=ModuleCategory.GAME,
            factory_entry="tests:Factory",
            module_version="1.0.0",
            capabilities=(
                validate_contract(
                    CapabilityDescriptor(
                        capability_id="read",
                        input_schema={
                            "type": "object",
                            "properties": {},
                            "required": [],
                        },
                        invocation_policy=InvocationPolicy.NATURAL_LANGUAGE_ALLOWED,
                        effect=CapabilityEffect.READ_ONLY,
                    )
                ),
            ),
            commands=(
                validate_contract(
                    CommandDescriptor(
                        operation_path="read",
                        capability_id="read",
                        parameter_mapping={},
                        help_text="read",
                    )
                ),
            ),
            schedules=(
                validate_contract(
                    ScheduleDescriptor(
                        collector_id="cache-collector",
                        key_version=1,
                        source_id="cache-source",
                        data_version=1,
                        input_schema={
                            "type": "object",
                            "properties": {},
                            "required": [],
                        },
                        shared_scope=OwnershipKind.PUBLIC,
                        trigger=ScheduleTrigger.ON_DEMAND,
                        minimum_interval_seconds=30,
                    )
                ),
            ),
        )
    )
    package = validate_contract(
        PackageManifest(
            package_id="pkg",
            package_version="1.0.0",
            contract_version=MODULE_ABI_VERSION,
            modules=(manifest,),
            author="tests",
            license="AGPL-3.0",
            source="offline",
        )
    )
    return package, validate_contract(
        ModuleHandlers({"read": _Handler()}, {"cache-collector": _Collector()}, {})
    )


class CacheResourcesServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = SQLiteDatabase(self.root / "state.sqlite3")
        self.identity_repository = SQLiteIdentityRepository(self.database)
        await self.identity_repository.save_principal(
            Principal("alice", "test-users", "alice")
        )
        await self.identity_repository.save_principal(
            Principal("bob", "test-users", "bob")
        )
        await self.identity_repository.save_principal(
            Principal("principal-alice", "test-users", "external-alice")
        )
        self.cache_repository = SQLiteCacheRepository(self.database)
        self.files = LocalSafeFileStore(self.root / "assets")
        self.resources_repository = SQLiteResourceRepository(self.database, self.files)
        self.grants = SQLiteGrantStore(self.database)
        self.clock_value = [monotonic()]
        self.execution_claims: list[ExecutionLease] = []
        package, handlers = _package()
        self.registry = Registry()
        self.registry.register_package(package, {"cache": handlers})
        self.issuer = ContextIssuer(clock=lambda: self.clock_value[0])
        self.lifecycle = LifecycleController(
            self.registry,
            issuer=self.issuer,
            clock=lambda: self.clock_value[0],
            execution_claim_prover=lambda candidate: any(
                candidate is claim for claim in self.execution_claims
            ),
        )
        operation_id = uuid4().hex
        instance = _Instance(handlers)
        adopted_handlers = self.lifecycle.adopt_candidate(
            "pkg",
            self.registry.snapshot().module("pkg/cache").manifest,
            operation_id,
            instance,
        )
        self.lifecycle.install_dormant(
            "pkg", "pkg/cache", operation_id, instance, adopted_handlers
        )
        self.identity, _ = await self.lifecycle.start_candidate(
            "pkg/cache", operation_id
        )
        self.lifecycle.publish_committed_intent(
            "pkg/cache",
            operation_id,
            self.identity,
            True,
            self.registry.snapshot().revision,
        )
        self.admission = self.lifecycle.admission
        self.principal_resolver = InvocationPrincipalResolver(
            self.issuer,
            self.identity_repository,
            identity_namespace="test-users",
            admission=self.admission,
        )
        self._views: list[tuple[object, object]] = []

    async def asyncTearDown(self) -> None:
        for view, lease in self._views:
            try:
                self.issuer.release(view)
            except Exception:
                pass
            try:
                self.admission.release(lease)
            except Exception:
                pass
        self.temp.cleanup()

    def _invocation(
        self,
        actor: str | None = None,
        *,
        grant: Grant | None = None,
        deadline: float | None = None,
        admit: bool = True,
    ):
        if actor is None:
            view, _ = self._scheduled_view(deadline=deadline)
            return view
        view = self.issuer.issue(
            origin=InvocationOrigin.COMMAND,
            module_id="pkg/cache",
            module_epoch=self.identity.module_epoch,
            registry_revision=self.identity_registry_revision,
            actor_id=actor,
            conversation_id="conversation",
            adapter_id="adapter",
            capability_id="read",
            grant_id=None if grant is None else grant.grant_id,
            grant_revision=None if grant is None else grant.revision,
            deadline=deadline,
        )
        lease = None
        if actor and admit:
            lease = self.admission.admit(view, "read")
        if lease is not None:
            self._views.append((view, lease))
        return view

    @property
    def identity_registry_revision(self) -> int:
        return self.lifecycle.state("pkg/cache").registry_revision

    def _scheduled_view(self, *, deadline: float | None = None):
        key = validate_contract(
            CollectionKey(
                "pkg/cache",
                "cache-collector",
                1,
                "cache-source",
                validate_contract(NormalizedInput({})),
                OwnerScope.public(),
            )
        )
        execution = ExecutionLease(
            key=key,
            token=uuid4().hex,
            config_revision=1,
            module_epoch=self.identity.module_epoch,
            registry_revision=self.registry.snapshot().revision,
            expires_at=datetime.now(UTC) + timedelta(minutes=1),
        )
        self.execution_claims.append(execution)
        lease = self.admission.admit_schedule(execution, "cache-collector")
        view = self.issuer.issue(
            origin=InvocationOrigin.SCHEDULER,
            module_id="pkg/cache",
            module_epoch=self.identity.module_epoch,
            registry_revision=self.registry.snapshot().revision,
            deadline=deadline,
            capability_id=None,
        )
        self.issuer.attach_lease(view, lease)
        self._views.append((view, lease))
        return view, lease

    def _cache(self, invocation, *, repository=None, **kwargs):
        options = {
            "issuer": self.issuer,
            "admission": self.admission,
            "principal_resolver": self.principal_resolver,
            "clock": lambda: self.clock_value[0],
        }
        options.update(kwargs)
        return CacheAccessService(
            invocation, repository or self.cache_repository, **options
        )

    def _resource(self, invocation, *, repository=None, file_store=None, **kwargs):
        options = {
            "issuer": self.issuer,
            "admission": self.admission,
            "principal_resolver": self.principal_resolver,
            "clock": lambda: self.clock_value[0],
        }
        options.update(kwargs)
        return ResourceAccessService(
            invocation,
            repository or self.resources_repository,
            file_store or self.files,
            **options,
        )

    async def _grant(
        self,
        grant_id: str = "grant-a",
        principal: str = "alice",
        *,
        expires_at: datetime | None = None,
    ) -> Grant:
        grant = Grant(
            grant_id,
            1,
            principal,
            "pkg/cache",
            grant_id,
            ("read",),
            None,
            GrantStatus.ACTIVE,
            expires_at,
        )
        await self.grants.create_grant(grant, expected_revision=0)
        return grant

    async def _revoke_under_gate(
        self,
        grant: Grant,
        *,
        events: list[str] | None = None,
        started: asyncio.Event | None = None,
    ) -> None:
        if started is not None:
            started.set()
        async with self.admission.mutation("test-grant-revoke"):
            current = await self.grants.current_grant(grant.grant_id)
            await self.grants.revoke_grant(current, expected_revision=grant.revision)
            if events is not None:
                events.append("grant-revoked")

    async def _asset_id(self, content: bytes, scope: OwnerScope) -> str:
        stage = await self.files.stage("asset-probe", content, scope)
        await self.files.discard(stage)
        return stage.asset_id

    async def test_d2_01_cache_resource_normal_and_restart(self) -> None:
        public_invocation = self._invocation()
        cache = self._cache(public_invocation)
        written = await cache.put("public", {"value": 1}, ttl_seconds=30)
        self.assertEqual((await cache.get("public")).payload["value"], 1)
        self.assertEqual(written.revision, 1)

        resource = self._resource(public_invocation)
        content = b"public-payload"
        asset_id = await self._asset_id(content, OwnerScope.public())
        reference = await resource.register(
            asset_id, "application/octet-stream", content
        )
        self.assertEqual(reference.asset_id, asset_id)
        self.assertEqual(await resource.read(asset_id), content)
        command_reader = self._resource(self._invocation("alice"))
        self.assertEqual(await command_reader.read(asset_id), content)
        other_command_reader = self._resource(self._invocation("bob"))
        self.assertEqual(await other_command_reader.read(asset_id), content)

        reopened_cache = CacheAccessService(
            public_invocation,
            SQLiteCacheRepository(SQLiteDatabase(self.root / "state.sqlite3")),
            issuer=self.issuer,
            admission=self.admission,
            principal_resolver=self.principal_resolver,
        )
        self.assertEqual((await reopened_cache.get("public")).payload["value"], 1)
        reopened_resources = ResourceAccessService(
            public_invocation,
            SQLiteResourceRepository(
                SQLiteDatabase(self.root / "state.sqlite3"),
                LocalSafeFileStore(self.root / "assets"),
            ),
            LocalSafeFileStore(self.root / "assets"),
            issuer=self.issuer,
            admission=self.admission,
            principal_resolver=self.principal_resolver,
        )
        self.assertEqual(await reopened_resources.read(asset_id), content)

    async def test_d2_02_user_and_grant_scopes_are_isolated_and_revocable(self) -> None:
        alice = self._cache(self._invocation("alice"))
        bob = self._cache(self._invocation("bob"))
        await alice.put("same", {"owner": "alice"}, ttl_seconds=30)
        self.assertIsNone(await bob.get("same"))

        grant = await self._grant()
        authorised = self._cache(
            self._invocation("alice", grant=grant), grant_store=self.grants
        )
        await authorised.put("private", {"owner": "alice"}, ttl_seconds=30)
        self.assertEqual((await authorised.get("private")).payload["owner"], "alice")

        await self.grants.revoke_grant(grant, expected_revision=1)
        with self.assertRaises(ygl.AccessDenied) as cache_error:
            await authorised.get("private")
        self.assertEqual(cache_error.exception.code, "grant_revoked")

        content = b"private"
        authorized_scope = OwnerScope.authorized(
            "alice", validate_contract(GrantReference("grant-a", 1))
        )
        resource = self._resource(
            self._invocation("alice", grant=grant), grant_store=self.grants
        )
        asset_id = await self._asset_id(content, authorized_scope)
        with self.assertRaises(ResourceAccessError):
            await resource.register(asset_id, "application/octet-stream", content)

        fresh_grant = await self._grant("grant-b")
        fresh_resource = self._resource(
            self._invocation("alice", grant=fresh_grant), grant_store=self.grants
        )
        fresh_scope = OwnerScope.authorized(
            "alice", validate_contract(GrantReference("grant-b", 1))
        )
        fresh_asset_id = await self._asset_id(content, fresh_scope)
        await fresh_resource.register(
            fresh_asset_id, "application/octet-stream", content
        )
        await self.grants.revoke_grant(fresh_grant, expected_revision=1)
        with self.assertRaises(ResourceAccessError):
            await fresh_resource.read(fresh_asset_id)

        alice_resource = self._resource(self._invocation("alice"))
        private_content = b"user-private"
        private_asset_id = await self._asset_id(
            private_content, OwnerScope.user("alice")
        )
        await alice_resource.register(
            private_asset_id, "application/octet-stream", private_content
        )
        bob_resource = self._resource(self._invocation("bob"))
        with self.assertRaises(FileNotFoundError):
            await bob_resource.read(private_asset_id)

    async def test_principal_ownership_uses_real_external_to_internal_mapping(self):
        user_view = self._invocation("external-alice")
        cache = self._cache(user_view)
        stored = await cache.put(
            "mapped-user", {"owner": "principal-alice"}, ttl_seconds=30
        )
        self.assertEqual(stored.key, "mapped-user")
        self.assertEqual(
            (await cache.get("mapped-user")).payload["owner"], "principal-alice"
        )

        grant = await self._grant("grant-mapped", principal="principal-alice")
        authorized_view = self._invocation("external-alice", grant=grant)
        authorized_cache = self._cache(authorized_view, grant_store=self.grants)
        private = await authorized_cache.put(
            "mapped-private",
            {"owner": "principal-alice"},
            ttl_seconds=30,
            visibility=CacheVisibility.AUTHORIZED,
        )
        self.assertEqual(private.key, "mapped-private")
        self.assertEqual(
            (await authorized_cache.get("mapped-private")).payload["owner"],
            "principal-alice",
        )
        private_scope = OwnerScope.authorized(
            "principal-alice",
            validate_contract(GrantReference(grant.grant_id, grant.revision)),
        )

        payload = b"principal-owned-resource"
        asset_id = await self._asset_id(payload, private_scope)
        resource = self._resource(authorized_view, grant_store=self.grants)
        reference = await resource.register(
            asset_id,
            "application/octet-stream",
            payload,
            visibility=CacheVisibility.AUTHORIZED,
        )
        self.assertEqual(reference.scope, private_scope)
        self.assertEqual(
            await resource.read(asset_id, visibility=CacheVisibility.AUTHORIZED),
            payload,
        )

        await self.identity_repository.save_principal(
            Principal("foreign-principal", "other-users", "external-alice")
        )
        foreign_resolver = InvocationPrincipalResolver(
            self.issuer,
            self.identity_repository,
            identity_namespace="other-users",
            admission=self.admission,
        )
        foreign_cache = self._cache(user_view, principal_resolver=foreign_resolver)
        self.assertIsNone(await foreign_cache.get("mapped-user"))

    async def test_principal_lookup_rechecks_exact_view_after_await(self):
        view = self._invocation("external-alice")
        lease = self.issuer.lease_for(view)
        paused_repository = _PausedIdentityRepository(self.identity_repository)
        resolver = InvocationPrincipalResolver(
            self.issuer,
            paused_repository,
            identity_namespace="test-users",
            admission=self.admission,
        )
        cache = self._cache(view, principal_resolver=resolver)
        pending = asyncio.create_task(
            cache.put("release-during-resolution", {"x": 1}, ttl_seconds=30)
        )
        await asyncio.wait_for(paused_repository.entered.wait(), timeout=1)
        self.issuer.release(view)
        paused_repository.release.set()
        with self.assertRaises(ygl.AccessDenied):
            await pending
        lookup = await self.cache_repository.get(
            CacheAccessRequest(
                "release-during-resolution",
                CacheVisibility.USER,
                OwnerScope.user("principal-alice"),
            )
        )
        self.assertIsNone(lookup.entry)
        if lease is not None:
            self.admission.release(lease)

    async def test_private_read_results_are_dropped_when_grant_is_revoked_mid_await(
        self,
    ) -> None:
        cache_grant = await self._grant("read-cache-grant")
        cache_view = self._invocation("alice", grant=cache_grant)
        cache = self._cache(cache_view, grant_store=self.grants)
        await cache.put(
            "private-result",
            {"secret": "cache-secret"},
            ttl_seconds=30,
            visibility=CacheVisibility.AUTHORIZED,
        )
        paused_cache_repository = _ObservedMethod(
            self.cache_repository, "get", pause=True, pause_after=True
        )
        paused_cache = self._cache(
            cache_view,
            repository=paused_cache_repository,
            grant_store=self.grants,
        )
        pending_cache_read = asyncio.create_task(paused_cache.get("private-result"))
        await paused_cache_repository.started.wait()
        await self._revoke_under_gate(cache_grant)
        paused_cache_repository.resume.set()
        with self.assertRaises(ygl.AccessDenied) as cache_error:
            await pending_cache_read
        self.assertEqual(cache_error.exception.code, "grant_revoked")

        resource_grant = await self._grant("read-resource-grant")
        resource_view = self._invocation("alice", grant=resource_grant)
        resource = self._resource(resource_view, grant_store=self.grants)
        payload = b"private-resource-bytes"
        scope = OwnerScope.authorized(
            "alice",
            validate_contract(
                GrantReference(resource_grant.grant_id, resource_grant.revision)
            ),
        )
        asset_id = await self._asset_id(payload, scope)
        await resource.register(
            asset_id,
            "application/octet-stream",
            payload,
            visibility=CacheVisibility.AUTHORIZED,
        )
        paused_resource_repository = _ObservedMethod(
            self.resources_repository, "read", pause=True, pause_after=True
        )
        paused_resource = self._resource(
            resource_view,
            repository=paused_resource_repository,
            grant_store=self.grants,
        )
        pending_resource_read = asyncio.create_task(paused_resource.read(asset_id))
        await paused_resource_repository.started.wait()
        await self._revoke_under_gate(resource_grant)
        paused_resource_repository.resume.set()
        with self.assertRaises(ResourceAccessError) as resource_error:
            await pending_resource_read
        self.assertEqual(resource_error.exception.code, "grant_revoked")

    async def test_grant_expiry_during_paused_private_read_drops_cache_result(self):
        grant = await self._grant(
            "expiring-cache-grant",
            expires_at=datetime.now(UTC) + timedelta(seconds=0.7),
        )
        view = self._invocation("alice", grant=grant)
        cache = self._cache(view, grant_store=self.grants)
        await cache.put(
            "expires-mid-read",
            {"secret": "no longer authorized"},
            ttl_seconds=30,
            visibility=CacheVisibility.AUTHORIZED,
        )
        paused_repository = _ObservedMethod(
            self.cache_repository, "get", pause=True, pause_after=True
        )
        reader = self._cache(
            view, repository=paused_repository, grant_store=self.grants
        )
        pending = asyncio.create_task(reader.get("expires-mid-read"))
        await paused_repository.started.wait()
        await asyncio.sleep(0.8)
        paused_repository.resume.set()
        with self.assertRaises(ygl.AccessDenied) as error:
            await pending
        self.assertEqual(error.exception.code, "grant_expired")

    async def test_private_resource_stage_is_discarded_when_grant_is_revoked(self):
        grant = await self._grant("stage-revocation-grant")
        view = self._invocation("alice", grant=grant)
        payload = b"must-be-discarded"
        scope = OwnerScope.authorized(
            "alice", validate_contract(GrantReference(grant.grant_id, 1))
        )
        asset_id = await self._asset_id(payload, scope)
        paused_files = _ObservedMethod(
            self.files, "stage", pause=True, pause_after=True
        )
        resource = self._resource(
            view, file_store=paused_files, grant_store=self.grants
        )
        pending = asyncio.create_task(
            resource.register(
                asset_id,
                "application/octet-stream",
                payload,
                visibility=CacheVisibility.AUTHORIZED,
            )
        )
        await paused_files.started.wait()
        await self._revoke_under_gate(grant)
        paused_files.resume.set()
        with self.assertRaises(ResourceAccessError) as error:
            await pending
        self.assertEqual(error.exception.code, "grant_revoked")
        self.assertIsNone(await self.resources_repository.metadata(asset_id, scope))
        with self.assertRaises(FileNotFoundError):
            await self.resources_repository.read(asset_id, scope)
        self.assertEqual(tuple(self.files.root.rglob("*.tmp")), ())
        self.assertEqual(await self.files.recover_orphans(), ())
        reopened = LocalSafeFileStore(self.files.root)
        self.assertEqual(tuple(reopened.root.rglob("*.tmp")), ())
        self.assertEqual(await reopened.recover_orphans(), ())

    async def test_private_resource_stage_mismatch_and_post_stage_cancel_remove_real_tmp(
        self,
    ):
        grant = await self._grant("stage-cleanup")
        view = self._invocation("alice", grant=grant)
        scope = OwnerScope.authorized(
            "alice", validate_contract(GrantReference(grant.grant_id, grant.revision))
        )
        mismatch_content = b"mismatched-stage-content"
        wrong_asset = await self._asset_id(b"different-content", scope)
        service = self._resource(view, grant_store=self.grants)
        with self.assertRaisesRegex(ValueError, "asset id does not match"):
            await service.register(
                wrong_asset,
                "application/octet-stream",
                mismatch_content,
                visibility=CacheVisibility.AUTHORIZED,
            )
        self.assertEqual(tuple(self.files.root.rglob("*.tmp")), ())
        self.assertEqual(await self.files.recover_orphans(), ())

        cancel_files = _CancelAfterStageFileStore(self.files)
        cancel_service = self._resource(
            view, file_store=cancel_files, grant_store=self.grants
        )
        cancel_content = b"cancel-after-stage-content"
        cancel_asset = await self._asset_id(cancel_content, scope)
        with self.assertRaises(asyncio.CancelledError):
            await cancel_service.register(
                cancel_asset,
                "application/octet-stream",
                cancel_content,
                visibility=CacheVisibility.AUTHORIZED,
            )
        self.assertEqual(tuple(self.files.root.rglob("*.tmp")), ())
        self.assertEqual(await self.files.recover_orphans(), ())
        self.assertIsNone(
            await self.resources_repository.registration_state(cancel_asset, scope)
        )

    async def test_private_persistence_shares_grant_revoke_mutation_gate(self):
        cache_grant = await self._grant("serialized-cache-grant")
        cache_view = self._invocation("alice", grant=cache_grant)
        cache_events: list[str] = []
        paused_cache_repository = _ObservedMethod(
            self.cache_repository,
            "put",
            events=cache_events,
            label="cache-put",
            pause=True,
        )
        cache = self._cache(
            cache_view,
            repository=paused_cache_repository,
            grant_store=self.grants,
        )
        cache_write = asyncio.create_task(
            cache.put(
                "serialized",
                {"value": 1},
                ttl_seconds=30,
                visibility=CacheVisibility.AUTHORIZED,
            )
        )
        await paused_cache_repository.started.wait()
        cache_revoke_started = asyncio.Event()
        cache_revoke = asyncio.create_task(
            self._revoke_under_gate(
                cache_grant, events=cache_events, started=cache_revoke_started
            )
        )
        await cache_revoke_started.wait()
        await asyncio.sleep(0.01)
        self.assertFalse(cache_revoke.done())
        paused_cache_repository.resume.set()
        await cache_write
        await cache_revoke
        self.assertEqual(cache_events, ["cache-put", "grant-revoked"])

        resource_grant = await self._grant("serialized-resource-grant")
        resource_view = self._invocation("alice", grant=resource_grant)
        resource_events: list[str] = []
        paused_files = _ObservedMethod(
            self.files,
            "commit",
            events=resource_events,
            label="file-commit",
            pause=True,
        )
        recording_repository = _ObservedMethod(
            self.resources_repository,
            "register",
            events=resource_events,
            label="resource-metadata",
        )
        resource = self._resource(
            resource_view,
            repository=recording_repository,
            file_store=paused_files,
            grant_store=self.grants,
        )
        content = b"serialized-resource"
        scope = OwnerScope.authorized(
            "alice",
            validate_contract(
                GrantReference(resource_grant.grant_id, resource_grant.revision)
            ),
        )
        asset_id = await self._asset_id(content, scope)
        resource_write = asyncio.create_task(
            resource.register(
                asset_id,
                "application/octet-stream",
                content,
                visibility=CacheVisibility.AUTHORIZED,
            )
        )
        await paused_files.started.wait()
        resource_revoke_started = asyncio.Event()
        resource_revoke = asyncio.create_task(
            self._revoke_under_gate(
                resource_grant,
                events=resource_events,
                started=resource_revoke_started,
            )
        )
        await resource_revoke_started.wait()
        await asyncio.sleep(0.01)
        self.assertFalse(resource_revoke.done())
        paused_files.resume.set()
        await resource_write
        await resource_revoke
        self.assertEqual(
            resource_events,
            ["file-commit", "resource-metadata", "grant-revoked"],
        )

    async def test_d2_03_concurrent_cache_writers_have_revision_conflict(self) -> None:
        service = self._cache(self._invocation("alice"))
        await service.put("race", {"value": 0}, ttl_seconds=30)

        async def write(value: int):
            return await service.put(
                "race", {"value": value}, ttl_seconds=30, expected_revision=1
            )

        results = await asyncio.gather(write(1), write(2), return_exceptions=True)
        self.assertEqual(sum(isinstance(item, RevisionConflict) for item in results), 1)
        self.assertEqual(sum(not isinstance(item, Exception) for item in results), 1)

    async def test_d2_04_expiry_path_and_media_failures_are_controlled(self) -> None:
        service = self._cache(self._invocation("alice"))
        with self.assertRaises(ValueError):
            await service.put("expired", {}, ttl_seconds=0)
        with self.assertRaises(ygl.ParameterError):
            await service.lookup(
                CacheAccessRequest("x", CacheVisibility.USER, OwnerScope.user("bob"))
            )

        content = b"bad-media"
        asset_id = await self._asset_id(content, OwnerScope.user("alice"))
        resources = self._resource(self._invocation("alice"))
        with self.assertRaises(ValueError):
            await resources.register(asset_id, "unknown", content)
        self.assertIsNone(
            await self.resources_repository.metadata(asset_id, OwnerScope.user("alice"))
        )
        self.assertTrue(await self.files.recover_orphans())
        with self.assertRaises(ValueError):
            await resources.read("../outside")

    async def test_d2_05_unfinished_stage_never_looks_registered_after_reopen(
        self,
    ) -> None:
        content = b"unfinished"
        scope = OwnerScope.user("alice")
        stage = await self.files.stage("unfinished", content, scope)
        reopened = LocalSafeFileStore(self.root / "assets")
        self.assertIsNone(
            await self.resources_repository.metadata(stage.asset_id, scope)
        )
        self.assertTrue(await reopened.recover_orphans() == ())
        with self.assertRaises(FileNotFoundError):
            await self.resources_repository.read(stage.asset_id, scope)
        await self.files.discard(stage)

    async def test_unrelated_directory_publication_keeps_admitted_services_usable(self):
        view = self._invocation("alice")
        cache = self._cache(view)
        resource = self._resource(view)
        await cache.put("stable", {"value": 7}, ttl_seconds=30)
        content = b"still-active"
        asset_id = await self._asset_id(content, OwnerScope.user("alice"))

        module = validate_contract(
            ModuleManifest(
                module_id="other",
                route="other",
                category=ModuleCategory.GAME,
                factory_entry="tests:OtherFactory",
                module_version="1.0.0",
                capabilities=(),
            )
        )
        self.registry.register_package(
            validate_contract(
                PackageManifest(
                    "other-package",
                    "1.0.0",
                    MODULE_ABI_VERSION,
                    (module,),
                    "tests",
                    "AGPL-3.0",
                    "offline",
                )
            ),
            {"other": validate_contract(ModuleHandlers({}, {}, {}))},
        )

        await resource.register(asset_id, "application/octet-stream", content)
        self.assertEqual((await cache.get("stable")).payload["value"], 7)
        self.assertEqual(await resource.read(asset_id), content)
        self.admission.check(self.issuer.lease_for(view))

    async def test_real_scheduled_lease_allows_public_cache_and_resource_access(self):
        view, lease = self._scheduled_view()
        cache = self._cache(view)
        resource = self._resource(view)
        await cache.put("scheduled", {"value": "ok"}, ttl_seconds=30)
        self.assertEqual((await cache.get("scheduled")).payload["value"], "ok")
        payload = b"scheduled-public"
        asset_id = await self._asset_id(payload, OwnerScope.public())
        await resource.register(asset_id, "application/octet-stream", payload)
        self.assertEqual(await resource.read(asset_id), payload)
        self.assertIsNone(view.capability_id)
        self.assertIs(self.issuer.lease_for(view), lease)

    async def test_missing_gate_copy_lease_deadline_and_epoch_all_fail_closed(self):
        view = self._invocation("alice", admit=False)
        unbound = CacheAccessService(view, self.cache_repository, issuer=self.issuer)
        unbound_resource = ResourceAccessService(
            view,
            self.resources_repository,
            self.files,
            issuer=self.issuer,
        )
        with self.assertRaises(ygl.ServiceUnavailable):
            await unbound.get("x")
        with self.assertRaises(ResourceAccessError):
            await unbound_resource.metadata("x")

        valid_view = self._invocation("alice")
        valid_lease = self.issuer.lease_for(valid_view)
        copied = replace(valid_lease)
        self.issuer.detach_lease(valid_view, valid_lease)
        self.issuer.attach_lease(valid_view, copied)
        with self.assertRaises(ygl.InvalidInvocation):
            await self._cache(valid_view).get("x")
        with self.assertRaises(AdmissionError):
            self.admission.check(copied)
        self.issuer.detach_lease(valid_view, copied)
        self.issuer.attach_lease(valid_view, valid_lease)
        await self._cache(valid_view).put("restored", {"v": 1}, ttl_seconds=30)

        deadline_view = self._invocation("alice", deadline=self.clock_value[0] + 1.0)
        deadline_service = self._cache(deadline_view)
        deadline_resource = self._resource(deadline_view)
        self.clock_value[0] += 2.0
        with self.assertRaises(ygl.OperationTimeout):
            await deadline_service.get("x")
        with self.assertRaises(ResourceAccessError):
            await deadline_resource.metadata("x")

        released_view = self._invocation("alice")
        released_lease = self.issuer.lease_for(released_view)
        released_cache = self._cache(released_view)
        released_resource = self._resource(released_view)
        self.issuer.release(released_view)
        with self.assertRaises(ygl.InvalidInvocation):
            await released_cache.get("x")
        with self.assertRaises(ResourceAccessError):
            await released_resource.metadata("x")
        self.admission.release(released_lease)

        epoch_view = self._invocation("alice")
        epoch_service = self._resource(epoch_view)
        self.lifecycle.quiesce("pkg/cache", uuid4().hex, "test invalidation")
        with self.assertRaises(ResourceAccessError):
            await epoch_service.metadata("x")

    async def test_private_resource_commit_remains_registered_when_confirm_crosses_expiry(
        self,
    ):
        grant = await self._grant(
            "confirm-expiry", expires_at=datetime.now(UTC) + timedelta(hours=1)
        )
        scope = OwnerScope.authorized(
            "alice", validate_contract(GrantReference(grant.grant_id, grant.revision))
        )
        content = b"commit-approved-before-expiry"
        asset_id = await self._asset_id(content, scope)
        controlled_files = _ControlledConfirmFileStore(self.files, pause_after=True)
        repository = SQLiteResourceRepository(self.database, controlled_files)
        resource = self._resource(
            self._invocation("alice", grant=grant),
            repository=repository,
            file_store=controlled_files,
            grant_store=self.grants,
        )
        _SwitchableDateTime.expired_at = None
        try:
            with patch.object(resource_service_module, "datetime", _SwitchableDateTime):
                pending = asyncio.create_task(
                    resource.register(
                        asset_id,
                        "application/octet-stream",
                        content,
                        visibility=CacheVisibility.AUTHORIZED,
                    )
                )
                await controlled_files.started.wait()
                _SwitchableDateTime.expired_at = grant.expires_at + timedelta(seconds=1)
                controlled_files.resume.set()
                with self.assertRaises(ResourceAccessError) as error:
                    await pending
            self.assertEqual(error.exception.code, "grant_expired")
        finally:
            _SwitchableDateTime.expired_at = None
        registered = await self.resources_repository.registration_state(asset_id, scope)
        self.assertIsNotNone(registered)
        self.assertEqual(await self.resources_repository.read(asset_id, scope), content)
        self.assertEqual(await self.files.recover_orphans(), ())

    async def test_confirm_error_and_cancellation_preserve_registered_resource(self):
        error_grant = await self._grant("confirm-error")
        error_scope = OwnerScope.authorized(
            "alice",
            validate_contract(
                GrantReference(error_grant.grant_id, error_grant.revision)
            ),
        )
        error_content = b"confirm-error-content"
        error_asset = await self._asset_id(error_content, error_scope)
        failing_files = _ControlledConfirmFileStore(self.files, fail=True)
        failing_repository = SQLiteResourceRepository(self.database, failing_files)
        failing_service = self._resource(
            self._invocation("alice", grant=error_grant),
            repository=failing_repository,
            file_store=failing_files,
            grant_store=self.grants,
        )
        with self.assertRaisesRegex(RuntimeError, "confirmation failed"):
            await failing_service.register(
                error_asset,
                "application/octet-stream",
                error_content,
                visibility=CacheVisibility.AUTHORIZED,
            )
        self.assertIsNotNone(
            await self.resources_repository.registration_state(error_asset, error_scope)
        )
        self.assertEqual(
            await self.resources_repository.read(error_asset, error_scope),
            error_content,
        )
        self.assertTrue(await self.files.recover_orphans())
        self.assertFalse(
            any(item.endswith(".orphan") for item in await self.files.recover_orphans())
        )

        cancel_grant = await self._grant("confirm-cancel")
        cancel_scope = OwnerScope.authorized(
            "alice",
            validate_contract(
                GrantReference(cancel_grant.grant_id, cancel_grant.revision)
            ),
        )
        cancel_content = b"confirm-cancel-content"
        cancel_asset = await self._asset_id(cancel_content, cancel_scope)
        paused_files = _ControlledConfirmFileStore(self.files, pause_before=True)
        base_repository = SQLiteResourceRepository(self.database, paused_files)
        observed_repository = _ObservedResourceRepository(base_repository)
        cancel_service = self._resource(
            self._invocation("alice", grant=cancel_grant),
            repository=observed_repository,
            file_store=paused_files,
            grant_store=self.grants,
        )
        registration = asyncio.create_task(
            cancel_service.register(
                cancel_asset,
                "application/octet-stream",
                cancel_content,
                visibility=CacheVisibility.AUTHORIZED,
            )
        )
        await paused_files.started.wait()
        registration.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await registration
        self.assertEqual(observed_repository.registration_state_calls, 0)
        self.assertIsNotNone(
            await self.resources_repository.registration_state(
                cancel_asset, cancel_scope
            )
        )
        self.assertEqual(
            await self.resources_repository.read(cancel_asset, cancel_scope),
            cancel_content,
        )
        self.assertFalse(
            any(item.endswith(".orphan") for item in await self.files.recover_orphans())
        )

    async def test_queue_pressure_cancellation_keeps_resource_pending_without_cleanup_job(
        self,
    ):
        grant = await self._grant("queue-cancel")
        scope = OwnerScope.authorized(
            "alice", validate_contract(GrantReference(grant.grant_id, grant.revision))
        )
        content = b"queued-resource-cancel"
        asset_id = await self._asset_id(content, scope)
        executor = self.database.executor
        worker_started = threading.Event()
        release_worker = threading.Event()
        ready = asyncio.Event()
        background: list[asyncio.Task] = []

        def blocker_callback(unit):
            worker_started.set()
            release_worker.wait(timeout=5)

        class _SaturatedRepository:
            def __getattr__(self, name):
                return getattr(self.delegate, name)

            def __init__(self, delegate):
                self.delegate = delegate

            async def register(self, *args, **kwargs):
                background.append(
                    asyncio.create_task(
                        executor.run_transaction(
                            blocker_callback, begin_mode="IMMEDIATE"
                        )
                    )
                )
                self.assert_started = await asyncio.to_thread(worker_started.wait, 1)
                if not self.assert_started:
                    raise AssertionError("SQLite worker did not start")
                background.extend(
                    asyncio.create_task(executor.run_read(lambda unit: 1))
                    for _ in range(executor.CAPACITY - 1)
                )
                for _ in range(200):
                    if executor._outstanding == executor.CAPACITY:
                        break
                    await asyncio.sleep(0.005)
                if executor._outstanding != executor.CAPACITY:
                    raise AssertionError("SQLite queue did not reach capacity")
                ready.set()
                return await self.delegate.register(*args, **kwargs)

        repository = _SaturatedRepository(self.resources_repository)
        resource = self._resource(
            self._invocation("alice", grant=grant),
            repository=repository,
            grant_store=self.grants,
        )
        registration = asyncio.create_task(
            resource.register(
                asset_id,
                "application/octet-stream",
                content,
                visibility=CacheVisibility.AUTHORIZED,
            )
        )
        await ready.wait()
        await asyncio.sleep(0)
        before_cancel = executor._outstanding
        registration.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await registration
        self.assertEqual(executor._outstanding, before_cancel)
        release_worker.set()
        await asyncio.gather(*background)
        self.assertIsNone(
            await self.resources_repository.registration_state(asset_id, scope)
        )
        recovered = await self.files.recover_orphans()
        self.assertTrue(recovered)
        self.assertFalse(any(item.endswith(".orphan") for item in recovered))

    async def test_private_mutators_pass_the_actual_current_grant_deadline(self):
        grant = await self._grant(
            "deadline-forwarding", expires_at=datetime.now(UTC) + timedelta(minutes=5)
        )
        cache_repository = _DeadlineRecordingRepository(self.cache_repository)
        cache = self._cache(
            self._invocation("alice", grant=grant),
            repository=cache_repository,
            grant_store=self.grants,
        )
        await cache.put(
            "deadline-forwarded",
            {"value": "private"},
            ttl_seconds=30,
            visibility=CacheVisibility.AUTHORIZED,
        )
        await cache.invalidate("deadline-forwarded")
        self.assertEqual(
            cache_repository.deadlines, [grant.expires_at, grant.expires_at]
        )

        resource_repository = _DeadlineRecordingRepository(self.resources_repository)
        resource = self._resource(
            self._invocation("alice", grant=grant),
            repository=resource_repository,
            grant_store=self.grants,
        )
        scope = OwnerScope.authorized(
            "alice", validate_contract(GrantReference(grant.grant_id, grant.revision))
        )
        content = b"deadline-forwarded-resource"
        asset_id = await self._asset_id(content, scope)
        await resource.register(
            asset_id,
            "application/octet-stream",
            content,
            visibility=CacheVisibility.AUTHORIZED,
        )
        await resource.delete(asset_id, visibility=CacheVisibility.AUTHORIZED)
        self.assertEqual(
            resource_repository.deadlines, [grant.expires_at, grant.expires_at]
        )


if __name__ == "__main__":
    unittest.main()
