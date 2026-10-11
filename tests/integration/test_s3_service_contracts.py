"""Real S3 issuer/binder/cache/subscription chains and public consumers."""

import asyncio
import hashlib
import json
import tempfile
import time
import traceback
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.core.contracts.services import Grant, GrantStatus
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.invocation import Gateway
from ygl_test_subject.modules.ff14.features.market_sources import (
    SOURCE_ROOT,
    SOURCE_VERSION,
    MarketSourceClient,
)
from ygl_test_subject.services.module_services import ModuleServicesFactory

import yomihime_game_link_sdk as ygl
from tests.fixtures.b03_runtime import (
    OfflineHttpTransport,
    _RuntimeInstance,
    build_runtime,
)
from tests.fixtures.s3_sdk_consumers import CacheConsumer, SubscriptionConsumer


class CacheContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = await build_runtime(Path(self.temp.name))

    async def asyncTearDown(self):
        for owner in tuple(self.runtime.registry.snapshot().modules):
            await self.runtime.lifecycle.stop(owner)
        await self.runtime.services.close_credentials()
        await self.runtime.database.executor.close(timeout=2)
        self.temp.cleanup()

    def view(self, actor="alice", module="sample/alpha", grant=None, deadline=None):
        current = self.runtime.registry.snapshot()
        registered = current.module(module)
        view = self.runtime.issuer.issue(
            origin=ygl.InvocationOrigin.COMMAND,
            module_id=module,
            module_epoch=registered.epoch,
            registry_revision=current.revision,
            actor_id=actor,
            conversation_id="room",
            adapter_id="test-adapter",
            capability_id="read",
            deadline=deadline,
            grant_id=None if grant is None else grant.grant_id,
            grant_revision=None if grant is None else grant.revision,
        )
        self.runtime.lifecycle.admission.admit(view, "read")
        return view

    async def bound(self, **kwargs):
        view = self.view(**kwargs)
        return view, await self.runtime.services.for_module(view.module_id).scopes.bind(
            view
        )

    def safe(self, error):
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        rendered = "".join(traceback.format_exception(error))
        for canary in ("synthetic-private-canary", "SELECT private", "C:/private"):
            self.assertNotIn(canary, rendered)

    async def grant(self, identity):
        grant = Grant(
            identity,
            1,
            "alice",
            "sample/alpha",
            "account-" + identity,
            ("read",),
            ygl.SecretRef(
                "secret_" + identity, "alice", "sample/alpha", "credential", "fixture"
            ),
            GrantStatus.ACTIVE,
        )
        return await self.runtime.repositories.authorization.create_grant(
            grant, expected_revision=0
        )

    async def test_get_is_only_default_lookup_projection(self):
        _, bound = await self.bound()
        saved = await bound.cache.put("entry", {"value": 1}, ttl_seconds=120)
        lookup = await bound.cache.lookup(ygl.CacheQuery("entry"))
        entry = await bound.cache.get("entry")
        self.assertIs(lookup.status, ygl.CacheLookupStatus.HIT)
        self.assertIs(type(entry), ygl.CacheEntry)
        self.assertEqual(
            (entry.key, entry.payload, entry.expires_at, entry.revision),
            (saved.key, saved.payload, saved.expires_at, saved.revision),
        )
        self.assertEqual(entry, lookup.entry)
        self.assertEqual(
            (await bound.cache.lookup(ygl.CacheQuery("missing"))),
            ygl.CacheLookup(ygl.CacheLookupStatus.MISS),
        )
        self.assertIsNone(await bound.cache.get("missing"))
        await self.runtime.database.executor.run_transaction(
            lambda unit: unit.execute(
                "UPDATE cache_entries SET expires_at=? WHERE visibility='user'",
                ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(),),
            ).rowcount
        )
        expired = await bound.cache.lookup(ygl.CacheQuery("entry"))
        self.assertIs(expired.status, ygl.CacheLookupStatus.EXPIRED)
        self.assertIsNone(expired.entry)
        self.assertIsNone(await bound.cache.get("entry"))
        grant = await self.grant("current-grant")
        _, authorized = await self.bound(grant=grant)
        await authorized.cache.put("old-grant-row", {"private": True}, ttl_seconds=120)
        self.assertIs(
            (await authorized.cache.lookup(ygl.CacheQuery("old-grant-row"))).status,
            ygl.CacheLookupStatus.HIT,
        )
        # Actual older-row grant eligibility is different from revoking current authority.
        await self.runtime.database.executor.run_transaction(
            lambda unit: unit.execute(
                "UPDATE cache_entries SET grant_revision=0 WHERE visibility='authorized' AND grant_id=?",
                (grant.grant_id,),
            ).rowcount
        )
        rejected = await authorized.cache.lookup(ygl.CacheQuery("old-grant-row"))
        self.assertIs(rejected.status, ygl.CacheLookupStatus.REJECTED)
        self.assertIsNone(rejected.entry)
        self.assertIsNone(await authorized.cache.get("old-grant-row"))
        self.assertEqual(
            (
                await self.runtime.repositories.authorization.current_grant(
                    grant.grant_id
                )
            ).revision,
            grant.revision,
        )

    async def test_get_errors_cancel_deadline_revoke_and_release_are_not_none(self):
        _, bound = await self.bound()
        original = type(self.runtime.repositories.cache).get

        async def outage(*args, **kwargs):
            raise RuntimeError("SELECT private C:/private synthetic-private-canary")

        with patch.object(type(self.runtime.repositories.cache), "get", outage):
            for operation in (
                lambda: bound.cache.get("entry"),
                lambda: bound.cache.lookup(ygl.CacheQuery("entry")),
            ):
                with self.assertRaises(ygl.ServiceUnavailable) as raised:
                    await operation()
                self.safe(raised.exception)
        cancel = asyncio.CancelledError("bounded exact cancellation")

        async def cancelled(*args, **kwargs):
            raise cancel

        with patch.object(type(self.runtime.repositories.cache), "get", cancelled):
            for operation in (
                lambda: bound.cache.get("entry"),
                lambda: bound.cache.lookup(ygl.CacheQuery("entry")),
            ):
                with self.assertRaises(asyncio.CancelledError) as raised:
                    await operation()
                self.assertIs(raised.exception, cancel)
        for method in ("get", "lookup"):
            grant = await self.grant("revoke-" + method)
            _, authorized = await self.bound(grant=grant)
            await authorized.cache.put("entry", {"value": 2}, ttl_seconds=120)

            async def revoke(repository, *args, **kwargs):
                result = await original(repository, *args, **kwargs)
                await self.runtime.repositories.authorization.revoke_grant(
                    grant, expected_revision=grant.revision
                )
                return result

            def call():
                return (
                    authorized.cache.get("entry")
                    if method == "get"
                    else authorized.cache.lookup(ygl.CacheQuery("entry"))
                )

            with patch.object(type(self.runtime.repositories.cache), "get", revoke):
                with self.assertRaises(ygl.AccessDenied) as raised:
                    await call()
                self.safe(raised.exception)
            with self.assertRaises(ygl.AccessDenied) as raised:
                await call()
            self.safe(raised.exception)
        clock = [time.monotonic()]
        factory = ModuleServicesFactory(
            self.runtime.registry,
            self.runtime.issuer,
            self.runtime.lifecycle,
            self.runtime.repositories,
            self.runtime.transport,
            config_principal_id="host-config",
            identity_namespace="test-users",
            clock=lambda: clock[0],
        )
        view = self.view(deadline=clock[0] + 60)
        timed = await factory.for_module(view.module_id).scopes.bind(view)
        clock[0] += 61
        with self.assertRaises(ygl.OperationTimeout) as raised:
            await timed.cache.get("entry")
        self.safe(raised.exception)
        view, released = await self.bound()
        self.runtime.issuer.release(view)
        with self.assertRaises(ygl.InvalidInvocation) as raised:
            await released.cache.get("entry")
        self.safe(raised.exception)

    async def test_stored_expiry_after_repository_await_is_not_hit(self):
        _, bound = await self.bound()
        await bound.cache.put("late", {"value": 1}, ttl_seconds=120)
        original = type(self.runtime.repositories.cache).get

        async def returned_expired(repository, *args, **kwargs):
            result = await original(repository, *args, **kwargs)
            self.assertIs(result.status, ygl.CacheLookupStatus.HIT)
            # Repository DTO fault injection retains real read/partition/proof.
            return ygl.CacheLookup(
                ygl.CacheLookupStatus.HIT,
                replace(
                    result.entry, expires_at=datetime.now(UTC) - timedelta(seconds=1)
                ),
            )

        with patch.object(
            type(self.runtime.repositories.cache), "get", returned_expired
        ):
            self.assertIsNone(await bound.cache.get("late"))
            self.assertEqual(
                await bound.cache.lookup(ygl.CacheQuery("late")),
                ygl.CacheLookup(ygl.CacheLookupStatus.EXPIRED),
            )

    async def test_ff_market_and_sdk_only_consumers_use_actual_bound_cache(self):
        _, bound = await self.bound()
        path = "/api/v2/fixture"
        key = (
            "market-v1-"
            + hashlib.sha256(
                json.dumps(
                    [SOURCE_VERSION, path, (), ()], separators=(",", ":")
                ).encode()
            ).hexdigest()
        )
        await bound.cache.put(
            key,
            {
                "version": SOURCE_VERSION,
                "url": SOURCE_ROOT + path,
                "fetched_at_ms": int(datetime.now(UTC).timestamp() * 1000),
                "body": {"value": 7},
            },
            ttl_seconds=120,
        )
        client = MarketSourceClient(bound.http, bound.cache)
        result, provenance = await client.start_bound(bound.http, bound.cache).read(
            path, (), lambda value: value
        )
        self.assertEqual(result, {"value": 7})
        self.assertTrue(provenance.cached)
        self.assertEqual(self.runtime.transport.requests, [])
        # Neutral SDK-only handler is actually registered, lifecycle owned and
        # invoked by the production Gateway with its injected ModuleServices.
        consumer = CacheConsumer()
        module = validate_contract(
            ygl.ModuleManifest(
                "client",
                "client",
                ygl.ModuleCategory.PLATFORM,
                "tests.fixtures.s3_sdk_consumers:CacheConsumer",
                "1.0.0",
                (
                    ygl.CapabilityDescriptor(
                        "read",
                        {"type": "object", "additionalProperties": False},
                        ygl.InvocationPolicy.COMMAND_ONLY,
                        ygl.CapabilityEffect.READ_ONLY,
                    ),
                ),
                commands=(ygl.CommandDescriptor("read", "read", {}, "Read cache"),),
                sources=(
                    ygl.SourceDeclaration("universalis_market", "universalis.app"),
                ),
            )
        )
        package = validate_contract(
            ygl.PackageManifest(
                "neutral",
                "1.0.0",
                ygl.MODULE_ABI_VERSION,
                (module,),
                "tests",
                "MIT",
                "local",
            )
        )
        handlers = validate_contract(ygl.ModuleHandlers({"read": consumer}, {}, {}))
        self.runtime.registry.register_package(package, {"client": handlers})
        instance = _RuntimeInstance(handlers, module)
        owned = self.runtime.lifecycle.adopt_candidate(
            "neutral", module, "s3-install", instance
        )
        self.runtime.lifecycle.install_dormant(
            "neutral", "neutral/client", "s3-install", instance, owned
        )
        consumer.services = self.runtime.services.for_module("neutral/client")
        identity, _ = await self.runtime.lifecycle.start_candidate(
            "neutral/client", "s3-start"
        )
        await self.runtime.health.prepare("neutral/client", module)
        self.runtime.lifecycle.publish_committed_intent(
            "neutral/client",
            "s3-start",
            identity,
            True,
            self.runtime.registry.snapshot().revision,
        )
        view, neutral = await self.bound(module="neutral/client")
        await neutral.cache.put("consumer", {"value": 9}, ttl_seconds=120)
        gateway = Gateway(
            self.runtime.registry,
            self.runtime.issuer,
            admission=self.runtime.lifecycle.admission,
            lifecycle=self.runtime.lifecycle,
        )
        response = await gateway.invoke_command(view, "read", {})
        self.assertIs(response.status, ygl.ResultStatus.SUCCESS)
        self.assertIs(consumer.lookup.status, ygl.CacheLookupStatus.HIT)
        self.assertEqual(consumer.lookup.entry.payload, {"value": 9})
        from ygl_test_subject.infrastructure.http import HttpTransport

        class OfflineJSON(HttpTransport):
            def __init__(self):
                self.requests = []

            async def request(self, request):
                self.requests.append(request)
                return ygl.HttpResponse(
                    200, {"content-type": "application/json"}, b'{"value":11}'
                )

        transport = OfflineJSON()
        factory = ModuleServicesFactory(
            self.runtime.registry,
            self.runtime.issuer,
            self.runtime.lifecycle,
            self.runtime.repositories,
            transport,
            config_principal_id="host-config",
            identity_namespace="test-users",
        )
        actual = await factory.for_module("neutral/client").scopes.bind(view)
        fresh = MarketSourceClient(actual.http, actual.cache).start_bound(
            actual.http, actual.cache
        )
        result, provenance = await fresh.read("/api/v2/miss", (), lambda value: value)
        self.assertEqual(result, {"value": 11})
        self.assertFalse(provenance.cached)
        self.assertEqual(len(transport.requests), 1)
        result, provenance = await fresh.read("/api/v2/miss", (), lambda value: value)
        self.assertTrue(provenance.cached)
        self.assertEqual(len(transport.requests), 1)
        self.runtime.issuer.release(view)
        with self.assertRaises(ygl.InvalidInvocation):
            await consumer.invoke(view, {})


class SubscriptionContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import shutil

        from ygl_test_subject.services.core_runtime import CoreRuntime

        from tests.fixtures.b04_runtime import build_runtime as build_b04
        from tests.host.assembly_contract import selected_assembly
        from tests.services.test_admin_operations import _Codec, _MessagePort, _Renderer

        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.neutral = build_b04(self.root / "neutral")
        extensions = self.root / "extensions"
        shutil.copytree(
            Path(__file__).resolve().parents[2] / "modules/ff14",
            extensions / "ff14",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        assembly = selected_assembly(principal_id="fixture")
        self.ff = CoreRuntime(
            database=self.root / "ff.sqlite3",
            extension_root=extensions,
            file_root=self.root / "assets",
            secret_root=self.root / "secrets",
            secret_codec=_Codec(),
            http_transport=OfflineHttpTransport(),
            renderer=_Renderer(),
            display_limits=validate_contract(ygl.DisplayLimits(2, 4096)),
            message_port=_MessagePort(),
            config_principal_id="fixture",
            identity_namespace="fixture",
            admin_context_validator=lambda *_: False,
            host_ingress_validator=lambda *_: False,
            trusted_bundled_manifests=assembly.manifests,
            trusted_subscription_gates=assembly.gates,
            module_config_validators=assembly.validators,
            managed_module_owners={"ff14/ff14"},
            cleanup_timeout=1,
        )
        await self.ff.database.executor.initialize()
        self.ff.extension_runtime.scan(extensions)
        self.assertTrue(await self.ff.config_repository.initialize_subscription_gates())
        from ygl_test_subject.core.contracts.administration import AdminOperation

        source = self.ff.admin_authorization.register_source(
            "s3-ff-enable",
            resources=self.ff.admin_operations.module_resources(),
            operations={AdminOperation.SET_ENABLED},
        )
        raw = object()
        context = source.issue(
            subject="synthetic-admin",
            request=raw,
            expiry=time.time() + 60,
            operations=source.operations,
            resources=source.resources,
            live=lambda item: item is raw,
        )
        await self.ff.admin_operations.set_enabled(
            None,
            "ff14/ff14",
            True,
            expected_registry_revision=self.ff.registry.snapshot().revision,
            authorization=context,
        )
        source.end(context)
        from ygl_test_subject.core.ports import Principal

        await self.ff.repositories.identities.save_principal(
            Principal("ff-alice", "fixture", "alice")
        )
        await self.ff.repositories.conversations.save(
            ygl.ConversationRef(
                "test-adapter", ygl.ConversationKind.DIRECT, "alice", "private-alice"
            )
        )

    async def asyncTearDown(self):
        if hasattr(self, "ff"):
            await self.ff.close(timeout=1)
        if hasattr(self, "neutral"):
            await self.neutral.lifecycle.stop("sample/feed")
            await self.neutral.module_factory.close_credentials()
            await self.neutral.database.executor.close(timeout=2)
        self.temp.cleanup()

    async def ff_view(self, action):
        snapshot = self.ff.registry.snapshot()
        capability = "ff14.calendar.subscription." + action
        view = self.ff.issuer.issue(
            origin=ygl.InvocationOrigin.COMMAND,
            module_id="ff14/ff14",
            module_epoch=snapshot.module("ff14/ff14").epoch,
            registry_revision=snapshot.revision,
            actor_id="alice",
            conversation_id="alice",
            adapter_id="test-adapter",
            capability_id=capability,
        )
        lease = self.ff.lifecycle.admission.admit(view, capability)
        await self.ff.owner_authority.capture(view, lease)
        return view

    async def test_sdk_only_and_ff_current_requests_are_bound_and_exit_safe(self):
        neutral = self.neutral
        await neutral.host_repositories.conversations.save(
            ygl.ConversationRef(
                "test-adapter", ygl.ConversationKind.DIRECT, "alice", "private-alice"
            )
        )
        invocation = neutral.invocation("alice", "alice")
        consumer = SubscriptionConsumer(
            neutral.module_factory.for_module("sample/feed")
        )
        request = ygl.SubscriptionRequest(
            "feed-alert", {"region": "global"}, {"minimum": 1}, "instant"
        )
        first = await consumer.create(invocation, request)
        second = await consumer.create(invocation, request)
        self.assertIs(type(first), ygl.SubscriptionView)
        self.assertEqual((first.owner_id, first.revision), ("principal-alice", 1))
        self.assertNotEqual(first.subscription_id, second.subscription_id)
        record = await neutral.repositories.subscriptions.current(first.subscription_id)
        other = await neutral.repositories.subscriptions.current(second.subscription_id)
        self.assertEqual(record.collection_key, other.collection_key)
        self.assertEqual(record.collection_key.parameters.values, {"region": "global"})
        self.assertFalse(hasattr(first, "collector_parameters"))
        revised = await consumer.revise(
            invocation,
            replace(
                request,
                filters={"minimum": 2},
                subscription_id=first.subscription_id,
                expected_revision=first.revision,
            ),
        )
        self.assertEqual(revised.revision, 2)
        with self.assertRaises(ygl.RevisionConflict) as caught:
            await consumer.revise(
                invocation,
                replace(
                    request, subscription_id=first.subscription_id, expected_revision=1
                ),
            )
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        await consumer.cancel(invocation, first.subscription_id, revised.revision)
        self.assertEqual(
            tuple(
                v.subscription_id
                for v in await consumer.services.subscriptions.list_current(invocation)
            ),
            (second.subscription_id,),
        )
        neutral.issuer.release(invocation)
        with self.assertRaises(ygl.InvalidInvocation):
            await consumer.create(invocation, request)
        await neutral.lifecycle.stop("sample/feed")
        with self.assertRaises(ygl.InvalidInvocation):
            await consumer.services.subscriptions.list_current(invocation)

        create = await self.ff_view("create")
        handlers = self.ff.registry.snapshot().module("ff14/ff14").handlers.capabilities
        response = await handlers["ff14.calendar.subscription.create"].invoke(
            create, {"region": "cn", "timezone": "UTC", "time": "08:00"}
        )
        self.assertIs(response.status, ygl.ResultStatus.SUCCESS)
        services = self.ff.module_services.for_module("ff14/ff14")
        views = await services.subscriptions.list_current(create)
        self.assertEqual(len(views), 1)
        first = views[0]
        self.assertEqual((first.owner_id, first.revision), ("ff-alice", 1))
        record = await self.ff.b04_repositories.subscriptions.current(
            first.subscription_id
        )
        self.assertEqual(record.collection_key.parameters.values, {"region": "cn"})
        update = await self.ff_view("update")
        response = await handlers["ff14.calendar.subscription.update"].invoke(
            update,
            {
                "subscription_id": first.subscription_id,
                "expected_revision": first.revision,
                "time": "09:00",
            },
        )
        self.assertIs(response.status, ygl.ResultStatus.SUCCESS)
        revised = (await services.subscriptions.list_current(update))[0]
        self.assertEqual((revised.revision, revised.filters["time"]), (2, "09:00"))
        record = await self.ff.b04_repositories.subscriptions.current(
            first.subscription_id
        )
        self.assertEqual(record.collection_key.parameters.values, {"region": "cn"})
        stale = ygl.SubscriptionRequest(
            "ff14.calendar.daily_summary",
            {"region": "cn"},
            dict(revised.filters),
            "instant",
            subscription_id=first.subscription_id,
            expected_revision=1,
        )
        with self.assertRaises(ygl.RevisionConflict):
            await services.subscriptions.revise_request(update, stale)
        cancel = await self.ff_view("cancel")
        response = await handlers["ff14.calendar.subscription.cancel"].invoke(
            cancel, {"subscription_id": first.subscription_id, "expected_revision": 2}
        )
        self.assertIs(response.status, ygl.ResultStatus.SUCCESS)
        self.assertEqual(await services.subscriptions.list_current(cancel), ())
        self.ff.issuer.release(update)
        with self.assertRaises(ygl.InvalidInvocation):
            await services.subscriptions.list_current(update)
        from ygl_test_subject.core.contracts.administration import AdminOperation

        source = self.ff.admin_authorization.register_source(
            "s3-ff-disable",
            resources=self.ff.admin_operations.module_resources(),
            operations={AdminOperation.SET_ENABLED},
        )
        raw = object()
        proof = source.issue(
            subject="synthetic-admin",
            request=raw,
            expiry=time.time() + 60,
            operations=source.operations,
            resources=source.resources,
            live=lambda item: item is raw,
        )
        await self.ff.admin_operations.set_enabled(
            None,
            "ff14/ff14",
            False,
            expected_registry_revision=self.ff.registry.snapshot().revision,
            authorization=proof,
        )
        with self.assertRaises(ygl.InvalidInvocation):
            await services.subscriptions.list_current(create)
