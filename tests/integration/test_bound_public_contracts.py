"""Public contracts exercised through actual issuer/lifecycle/scopes.bind."""

import asyncio
import tempfile
import traceback
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.core.contracts.services import CacheAccessRequest

import yomihime_game_link_sdk as ygl
from tests.fixtures.b03_runtime import build_runtime


class BoundPublicContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = await build_runtime(Path(self.temp.name))

    async def asyncTearDown(self):
        await self.runtime.services.close_credentials()
        await self.runtime.database.executor.close(timeout=1)
        self.temp.cleanup()

    def view(self, actor="alice", module="sample/alpha", deadline=None, grant=None):
        registered = self.runtime.registry.snapshot().module(module)
        view = self.runtime.issuer.issue(
            origin=ygl.InvocationOrigin.COMMAND,
            module_id=module,
            module_epoch=registered.epoch,
            registry_revision=self.runtime.registry.snapshot().revision,
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
        for marker in ("SELECT private", "C:/private", "synthetic-secret"):
            self.assertNotIn(marker, rendered)

    async def test_cache_query_derives_principal_and_module_partitions(self):
        _, alice = await self.bound()
        _, bob = await self.bound(actor="bob")
        _, beta = await self.bound(module="sample/beta")
        await alice.cache.put("same", {"value": "alice"}, ttl_seconds=120)
        await bob.cache.put("same", {"value": "bob"}, ttl_seconds=120)
        self.assertEqual(
            (await alice.cache.lookup(ygl.CacheQuery("same"))).entry.payload["value"],
            "alice",
        )
        self.assertEqual(
            (await bob.cache.lookup(ygl.CacheQuery("same"))).entry.payload["value"],
            "bob",
        )
        self.assertIs(
            (await beta.cache.lookup(ygl.CacheQuery("same"))).status,
            ygl.CacheLookupStatus.MISS,
        )

    async def test_invalid_query_private_scope_and_subclass_rejected_before_repository(
        self,
    ):
        _, bound = await self.bound()

        class Forged(ygl.CacheQuery):
            pass

        for query in (
            ygl.CacheQuery("../bad"),
            ygl.CacheQuery("key", "public"),
            Forged("key"),
            CacheAccessRequest(
                "key", ygl.CacheVisibility.USER, ygl.OwnerScope.user("bob")
            ),
        ):
            with (
                self.subTest(query=type(query).__name__),
                patch.object(type(self.runtime.repositories.cache), "get") as get,
            ):
                with self.assertRaises(ygl.ParameterError) as raised:
                    await bound.cache.lookup(query)
                self.safe(raised.exception)
                get.assert_not_called()

    async def test_repo_failure_has_public_identity_and_no_raw_exception_chain(self):
        _, bound = await self.bound()

        async def fail(*args, **kwargs):
            raise RuntimeError("SELECT private C:/private synthetic-secret")

        with patch.object(type(self.runtime.repositories.cache), "get", fail):
            with self.assertRaises(ygl.ServiceUnavailable) as raised:
                await bound.cache.lookup(ygl.CacheQuery("key"))
            self.safe(raised.exception)

    async def test_copied_and_released_invocation_use_public_error(self):
        view, bound = await self.bound()
        with self.assertRaises(ygl.InvalidInvocation) as raised:
            await self.runtime.services.for_module(view.module_id).scopes.bind(
                replace(view)
            )
        self.safe(raised.exception)
        self.runtime.issuer.release(view)
        with self.assertRaises(ygl.InvalidInvocation) as raised:
            await bound.cache.lookup(ygl.CacheQuery("key"))
        self.safe(raised.exception)

    async def test_cancelled_repository_await_propagates_unchanged(self):
        _, bound = await self.bound()
        cancelled = asyncio.CancelledError("bounded cancellation")

        async def fail(*args, **kwargs):
            raise cancelled

        with patch.object(type(self.runtime.repositories.cache), "get", fail):
            with self.assertRaises(asyncio.CancelledError) as raised:
                await bound.cache.lookup(ygl.CacheQuery("key"))
            self.assertIs(raised.exception, cancelled)

    async def test_cache_four_statuses_and_malformed_return_are_distinct(self):
        _, bound = await self.bound()
        entry = await bound.cache.put("entry", {"value": 1}, ttl_seconds=120)
        self.assertEqual(entry.key, "entry")
        lookup = await bound.cache.lookup(ygl.CacheQuery("entry"))
        self.assertIs(lookup.status, ygl.CacheLookupStatus.HIT)
        self.assertEqual(lookup.entry.key, "entry")
        for status in (
            ygl.CacheLookupStatus.MISS,
            ygl.CacheLookupStatus.EXPIRED,
            ygl.CacheLookupStatus.REJECTED,
        ):

            async def result(*args, **kwargs):
                return ygl.CacheLookup(status)

            with (
                self.subTest(status=status),
                patch.object(type(self.runtime.repositories.cache), "get", result),
            ):
                lookup = await bound.cache.lookup(ygl.CacheQuery("entry"))
                self.assertIs(lookup.status, status)
                self.assertIsNone(lookup.entry)

        async def malformed(*args, **kwargs):
            return ygl.CacheLookup(ygl.CacheLookupStatus.REJECTED, entry)

        with patch.object(type(self.runtime.repositories.cache), "get", malformed):
            with self.assertRaises(ygl.ServiceUnavailable) as raised:
                await bound.cache.lookup(ygl.CacheQuery("entry"))
            self.safe(raised.exception)

    async def test_cache_namespace_is_bounded_and_old_raw_row_unchanged(self):
        from datetime import UTC, datetime, timedelta

        _, bound = await self.bound()
        raw = CacheAccessRequest(
            "old", ygl.CacheVisibility.USER, ygl.OwnerScope.user("alice")
        )
        old = ygl.CacheEntry(
            "old", {"legacy": True}, datetime.now(UTC) + timedelta(seconds=120)
        )
        await self.runtime.repositories.cache.put(raw, old)
        self.assertIs(
            (await bound.cache.lookup(ygl.CacheQuery("old"))).status,
            ygl.CacheLookupStatus.MISS,
        )
        self.assertEqual((await self.runtime.repositories.cache.get(raw)).entry, old)
        logical = "x" * 512
        saved = await bound.cache.put(logical, {"boundary": True}, ttl_seconds=120)
        self.assertEqual(saved.key, logical)
        self.assertEqual(
            (await bound.cache.lookup(ygl.CacheQuery(logical))).entry.key, logical
        )
        rows = await self.runtime.database.executor.run_read(
            lambda unit: tuple(
                row[0]
                for row in unit.execute(
                    "SELECT cache_key FROM cache_entries"
                ).fetchall()
            )
        )
        self.assertEqual(sorted(len(key) for key in rows), [3, 71])
        with self.assertRaises(ygl.ParameterError) as raised:
            await bound.cache.lookup(ygl.CacheQuery(logical + "x"))
        self.safe(raised.exception)

    async def test_records_conflict_input_and_dependency_failures(self):
        from ygl_test_subject.infrastructure.sqlite.repositories_config import (
            SQLiteRecordCollection,
        )

        _, bound = await self.bound()
        records = await bound.records.collection("profiles")
        record = await records.create("key", {"value": 1})
        self.assertIs(type(record), ygl.VersionedRecord)
        self.assertEqual(records.scope.user_id, "alice")
        with self.assertRaises(ygl.UniqueConstraintViolation) as raised:
            await records.create("key", {"value": 2})
        self.safe(raised.exception)
        self.assertEqual(raised.exception.resource, "module_record")
        with self.assertRaises(ygl.RevisionConflict) as raised:
            await records.replace("key", {"value": 2}, expected_revision=2)
        self.safe(raised.exception)
        self.assertEqual(
            (raised.exception.expected_revision, raised.exception.actual_revision),
            (2, 1),
        )
        with self.assertRaises(ygl.ParameterError) as raised:
            await records.replace("../key", {"value": 2}, expected_revision=True)
        self.safe(raised.exception)

        async def fail(*args, **kwargs):
            raise RuntimeError("SELECT private C:/private synthetic-secret")

        with patch.object(SQLiteRecordCollection, "get", fail):
            with self.assertRaises(ygl.ServiceUnavailable) as raised:
                await records.get("key")
            self.safe(raised.exception)

    async def test_cancellation_after_record_commit_does_not_undo_or_retry(self):
        from ygl_test_subject.infrastructure.sqlite.repositories_config import (
            SQLiteRecordCollection,
        )

        _, bound = await self.bound()
        records = await bound.records.collection("profiles")
        original = SQLiteRecordCollection.create
        cancelled = asyncio.CancelledError("accepted write cancelled")
        calls = []

        async def commit_then_cancel(collection, *args, **kwargs):
            calls.append(1)
            await original(collection, *args, **kwargs)
            raise cancelled

        with patch.object(SQLiteRecordCollection, "create", commit_then_cancel):
            with self.assertRaises(asyncio.CancelledError) as raised:
                await records.create("committed", {"value": 1})
            self.assertIs(raised.exception, cancelled)
        self.assertEqual(calls, [1])
        self.assertEqual((await records.get("committed")).revision, 1)

    async def test_authorized_query_revocation_before_and_after_repository_await(self):
        from ygl_test_subject.core.contracts.services import Grant, GrantStatus

        grant = Grant(
            "grant-alice",
            1,
            "alice",
            "sample/alpha",
            "account-alice",
            ("read",),
            ygl.SecretRef(
                "secret_grant-alice",
                "alice",
                "sample/alpha",
                "credential",
                "test_exchange",
            ),
            GrantStatus.ACTIVE,
        )
        await self.runtime.repositories.authorization.create_grant(
            grant, expected_revision=0
        )
        _, bound = await self.bound(grant=grant)
        await bound.cache.put("auth", {"private": True}, ttl_seconds=120)
        self.assertIs(
            (await bound.cache.lookup(ygl.CacheQuery("auth"))).status,
            ygl.CacheLookupStatus.HIT,
        )
        original = type(self.runtime.repositories.cache).get

        async def revoke_after_read(repository, *args, **kwargs):
            result = await original(repository, *args, **kwargs)
            await self.runtime.repositories.authorization.revoke_grant(
                grant, expected_revision=1
            )
            return result

        with patch.object(
            type(self.runtime.repositories.cache), "get", revoke_after_read
        ):
            with self.assertRaises(ygl.AccessDenied) as raised:
                await bound.cache.lookup(
                    ygl.CacheQuery("auth", ygl.CacheVisibility.AUTHORIZED)
                )
            self.safe(raised.exception)
        with patch.object(type(self.runtime.repositories.cache), "get") as get:
            with self.assertRaises(ygl.AccessDenied) as raised:
                await bound.cache.lookup(ygl.CacheQuery("auth"))
            self.safe(raised.exception)
            get.assert_not_called()

    async def test_cache_bad_ttl_is_parameter_error_without_write(self):
        _, bound = await self.bound()
        for ttl in (True, 0, -1, float("nan")):
            with (
                self.subTest(ttl=ttl),
                patch.object(type(self.runtime.repositories.cache), "put") as put,
            ):
                with self.assertRaises(ygl.ParameterError) as raised:
                    await bound.cache.put("key", {"value": 1}, ttl_seconds=ttl)
                self.safe(raised.exception)
                put.assert_not_called()

    async def test_deadline_is_public_timeout_and_shared_handles_reject_release(self):
        import time

        from ygl_test_subject.services.module_services import ModuleServicesFactory

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
        bound = await factory.for_module(view.module_id).scopes.bind(view)
        clock[0] += 61
        with self.assertRaises(ygl.OperationTimeout) as raised:
            await bound.cache.lookup(ygl.CacheQuery("key"))
        self.safe(raised.exception)
        self.runtime.issuer.release(view)
        for operation in (
            lambda: bound.records.collection("profiles"),
            lambda: bound.resources.read("asset"),
        ):
            with self.assertRaises(ygl.InvalidInvocation) as raised:
                await operation()
            self.safe(raised.exception)
        with self.assertRaises(ygl.SourceHttpError) as raised:
            await bound.http.fetch(ygl.HttpRequest("catalog", "/items"))
        self.safe(raised.exception)

    async def test_subscription_request_parameters_and_unavailable_delegate(self):
        view = self.view()
        operations = self.runtime.services.for_module(view.module_id).subscriptions
        with self.assertRaises(ygl.ParameterError) as raised:
            await operations.create_request(view, object())
        self.safe(raised.exception)
        with self.assertRaises(ygl.ServiceUnavailable) as raised:
            await operations.list_current(view)
        self.safe(raised.exception)
        with self.assertRaises(ygl.InvalidInvocation) as raised:
            await operations.list_current(replace(view))
        self.safe(raised.exception)

    async def test_http_hostile_mapping_is_safely_rejected_before_transport(self):
        _, bound = await self.bound()

        class Hostile(dict):
            def items(self):
                raise RuntimeError("SELECT private C:/private synthetic-secret")

        request = ygl.HttpRequest("catalog", "/items")
        object.__setattr__(request, "headers", Hostile())
        with self.assertRaises(ygl.ParameterError) as raised:
            await bound.http.fetch(request)
        self.safe(raised.exception)
        self.assertEqual(self.runtime.transport.requests, [])

    async def test_query_cursor_range_is_parameter_checked_before_repository(self):
        from ygl_test_subject.infrastructure.sqlite.repositories_config import (
            SQLiteRecordCollection,
        )

        _, bound = await self.bound()
        descriptor = (
            self.runtime.registry.snapshot()
            .module("sample/alpha")
            .manifest.collections[0]
        )
        object.__setattr__(
            descriptor, "indexes", (ygl.CollectionIndex("value", "value"),)
        )
        records = await bound.records.collection("profiles")
        original = await records.create("cursor-control", {"value": "a"})
        query = ygl.DeclaredIndexQuery("value", ygl.QueryOperator.EQUALS, "a")
        normal = await records.query(query)
        self.assertEqual(len(normal.records), 1)
        page = await records.query(replace(query, cursor="2147483648"))
        self.assertIs(type(page), ygl.RecordPage)
        self.assertEqual(page.records, ())
        with patch.object(SQLiteRecordCollection, "query") as operation:
            with self.assertRaises(ygl.ParameterError) as raised:
                await records.query(replace(query, cursor="2147483649"))
            self.assertIs(type(raised.exception), ygl.ParameterError)
            self.safe(raised.exception)
            operation.assert_not_called()
        retained = await records.get("cursor-control")
        self.assertEqual(retained.revision, original.revision)
        self.assertEqual(retained.value, original.value)

    async def test_query_cursor_conversion_is_parameter_checked_before_repository(self):
        from ygl_test_subject.infrastructure.sqlite.repositories_config import (
            SQLiteRecordCollection,
        )

        _, bound = await self.bound()
        descriptor = (
            self.runtime.registry.snapshot()
            .module("sample/alpha")
            .manifest.collections[0]
        )
        object.__setattr__(
            descriptor, "indexes", (ygl.CollectionIndex("value", "value"),)
        )
        records = await bound.records.collection("profiles")
        for cursor in ("²", "9" * 4301):
            with self.subTest(
                cursor_kind="unicode_digit" if len(cursor) == 1 else "conversion_limit"
            ):
                with patch.object(SQLiteRecordCollection, "query") as operation:
                    with self.assertRaises(ygl.ParameterError) as raised:
                        await records.query(
                            ygl.DeclaredIndexQuery(
                                "value", ygl.QueryOperator.EQUALS, "a", cursor=cursor
                            )
                        )
                    self.assertIs(type(raised.exception), ygl.ParameterError)
                    self.safe(raised.exception)
                    operation.assert_not_called()

    async def test_records_return_and_index_query_are_receiving_boundaries(self):
        from ygl_test_subject.infrastructure.sqlite.repositories_config import (
            SQLiteRecordCollection,
        )

        _, bound = await self.bound()
        descriptor = (
            self.runtime.registry.snapshot()
            .module("sample/alpha")
            .manifest.collections[0]
        )
        object.__setattr__(
            descriptor, "indexes", (ygl.CollectionIndex("value", "value"),)
        )
        records = await bound.records.collection("profiles")
        for query in (
            ygl.DeclaredIndexQuery("unknown", ygl.QueryOperator.EQUALS, "a"),
            ygl.DeclaredIndexQuery("value", ygl.QueryOperator.EQUALS, float("nan")),
        ):
            with (
                self.subTest(query=query),
                patch.object(SQLiteRecordCollection, "query") as operation,
            ):
                with self.assertRaises(ygl.ParameterError) as raised:
                    await records.query(query)
                self.safe(raised.exception)
                operation.assert_not_called()
        for method, returned, call in (
            ("get", ygl.VersionedRecord("key", True, {}), lambda: records.get("key")),
            (
                "query",
                ygl.RecordPage((object(),)),
                lambda: records.query(
                    ygl.DeclaredIndexQuery("value", ygl.QueryOperator.EQUALS, "a")
                ),
            ),
        ):

            async def invalid(*args, **kwargs):
                return returned

            with (
                self.subTest(method=method),
                patch.object(SQLiteRecordCollection, method, invalid),
            ):
                with self.assertRaises(ygl.ServiceUnavailable) as raised:
                    await call()
                self.safe(raised.exception)

    async def test_issuer_deadline_at_binder_records_and_tasks_is_timeout(self):
        import time

        clock = [time.monotonic()]
        self.runtime.issuer._clock = lambda: clock[0]
        view, bound = await self.bound(deadline=clock[0] + 60)
        records = await bound.records.collection("profiles")
        clock[0] += 61

        async def never():
            self.fail("expired work must never execute")

        for call in (
            lambda: self.runtime.services.for_module(view.module_id).scopes.bind(view),
            lambda: records.get("key"),
            lambda: bound.tasks.await_result(never()),
        ):
            with self.assertRaises(ygl.OperationTimeout) as raised:
                await call()
            self.safe(raised.exception)

    async def test_http_return_and_resource_dependency_are_safe_service_failures(self):
        from ygl_test_subject.infrastructure.http import SourceHttpService
        from ygl_test_subject.services.resources import ResourceAccessService

        _, bound = await self.bound()

        async def invalid(*args, **kwargs):
            return ygl.HttpResponse(True, {}, b"body")

        with patch.object(SourceHttpService, "fetch", invalid):
            with self.assertRaises(ygl.ServiceUnavailable) as raised:
                await bound.http.fetch(ygl.HttpRequest("catalog", "/items"))
            self.safe(raised.exception)

        async def broken(*args, **kwargs):
            raise ValueError("SELECT private C:/private synthetic-secret")

        with patch.object(ResourceAccessService, "read", broken):
            with self.assertRaises(ygl.ServiceUnavailable) as raised:
                await bound.resources.read("asset")
            self.safe(raised.exception)

    async def test_module_config_identity_and_account_returned_dtos_are_checked(self):
        from ygl_test_subject.services.configuration import ConfigurationService
        from ygl_test_subject.services.identity import IdentityResolverService
        from ygl_test_subject.services.module_services import _AccountOperations

        view, _ = await self.bound()
        services = self.runtime.services.for_module(view.module_id)
        self.assertIs(type(await services.config.current()), ygl.ConfigSnapshot)
        observed = set()

        async def config_bad(*args, **kwargs):
            observed.add("config")
            return ygl.ConfigSnapshot(True, {})

        with patch.object(ConfigurationService, "current", config_bad):
            with self.assertRaises(ygl.ServiceUnavailable) as raised:
                await services.config.current()
            self.safe(raised.exception)

        async def identity_bad(*args, **kwargs):
            observed.add("identity")
            return ygl.ResolvedIdentity(
                "identity", "provider", "subject", display_name=object()
            )

        with patch.object(IdentityResolverService, "default_identity", identity_bad):
            with self.assertRaises(ygl.ServiceUnavailable) as raised:
                await services.identities.default_identity(view)
            self.safe(raised.exception)

        async def account_bad(*args, **kwargs):
            observed.add("account")
            return ygl.GrantReference("grant", True)

        with patch.object(_AccountOperations, "status", account_bad):
            with self.assertRaises(ygl.ServiceUnavailable) as raised:
                await services.accounts.status(view)
            self.safe(raised.exception)
        self.assertEqual(observed, {"config", "identity", "account"})

    async def test_resource_reference_and_bytes_results_are_canonical(self):
        from ygl_test_subject.services.resources import ResourceAccessService

        _, bound = await self.bound()
        for method, call in (
            (
                "register",
                lambda: bound.resources.register("asset", "text/plain", b"bytes"),
            ),
            ("read", lambda: bound.resources.read("asset")),
        ):

            async def malformed(*args, **kwargs):
                return object()

            with patch.object(ResourceAccessService, method, malformed):
                with self.assertRaises(ygl.ServiceUnavailable) as raised:
                    await call()
                self.safe(raised.exception)
