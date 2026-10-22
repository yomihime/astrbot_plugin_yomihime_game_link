"""Cross-layer public query cache and safe upstream error contracts."""

import unittest
from collections.abc import Mapping

from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.invocation import Gateway
from ygl_test_subject.services.module_services import _BoundCache
from ygl_test_subject.services.output import OutputService

import tests.services.test_cache_resources as cache_fixtures
import tests.services.test_source_http as http_fixtures
import yomihime_game_link_sdk as ygl


class PublicCacheContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = cache_fixtures.CacheResourcesServiceTests()
        await self.fixture.asyncSetUp()

    async def asyncTearDown(self):
        await self.fixture.asyncTearDown()

    def bound(self, view, *, public=True):
        def current():
            self.fixture.issuer.require(view)
            self.fixture.admission.check(self.fixture.issuer.lease_for(view))

        async def check():
            current()

        return _BoundCache(self.fixture._cache(view), current, check, public)

    async def test_explicit_public_write_is_shared_and_returns_frozen_projection(self):
        fixture = self.fixture
        command = fixture._invocation("alice")
        cache = self.bound(command)
        await cache.put(
            "source:recent:1:5",
            {"projection": [{"kills": 0}]},
            ttl_seconds=60,
            visibility=ygl.CacheVisibility.PUBLIC,
        )
        tool = fixture.issuer.issue(
            origin=ygl.InvocationOrigin.LLM_TOOL,
            module_id="pkg/cache",
            module_epoch=fixture.identity.module_epoch,
            registry_revision=fixture.identity_registry_revision,
            actor_id="bob",
            adapter_id="adapter",
            conversation_id="conversation",
            capability_id="read",
        )
        lease = fixture.admission.admit(tool, "read")
        fixture._views.append((tool, lease))
        result = await self.bound(tool).lookup(
            ygl.CacheQuery("source:recent:1:5", ygl.CacheVisibility.PUBLIC)
        )
        self.assertIs(result.status, ygl.CacheLookupStatus.HIT)
        self.assertIsInstance(result.entry.payload["projection"][0], Mapping)
        self.assertEqual(result.entry.payload["projection"][0]["kills"], 0)
        for key in ("source:recent:2:5", "source:recent:1:10", "other:recent:1:5"):
            result = await self.bound(tool).lookup(
                ygl.CacheQuery(key, ygl.CacheVisibility.PUBLIC)
            )
            self.assertIs(result.status, ygl.CacheLookupStatus.MISS)

    async def test_default_stays_user_and_private_binding_cannot_write_public(self):
        alice = self.bound(self.fixture._invocation("alice"))
        bob = self.bound(self.fixture._invocation("bob"))
        await alice.put("private", {"identity": "alice"}, ttl_seconds=60)
        self.assertIsNotNone(await alice.get("private"))
        self.assertIsNone(await bob.get("private"))
        self.assertIs(
            (
                await bob.lookup(ygl.CacheQuery("private", ygl.CacheVisibility.PUBLIC))
            ).status,
            ygl.CacheLookupStatus.MISS,
        )
        readonly_private = self.bound(self.fixture._invocation("alice"), public=False)
        with self.assertRaises(ygl.InvalidInvocation):
            await readonly_private.put(
                "leak",
                {"identity": "private"},
                ttl_seconds=60,
                visibility=ygl.CacheVisibility.PUBLIC,
            )


class RetryAfterContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_transport_boundary_gateway_and_output_keep_only_bounded_hint(self):
        for raw, expected in (
            ("120", 120),
            ("0", 0),
            ("86401", None),
            ("-1", None),
            ("secret upstream", None),
        ):
            with self.subTest(header=raw):
                transport = http_fixtures._FakeTransport()
                transport.response = validate_contract(
                    ygl.HttpResponse(
                        429, {"Retry-After": raw}, b"private upstream body"
                    )
                )
                service = http_fixtures.SourceHttpService(
                    (http_fixtures._source(),), transport
                )
                with self.assertRaises(ygl.SourceHttpError) as caught:
                    await service.fetch(ygl.HttpRequest("steam", "/limited"))
                error = caught.exception
                self.assertEqual(error.retry_after_seconds, expected)
                self.assertEqual(error.status_code, 429)
                self.assertEqual(len(transport.calls), 1)
                self.assertNotIn("private", str(error))
                detail = Gateway._rebuild_error(
                    ygl.ErrorDetail(
                        ygl.ErrorCode.RATE_LIMITED,
                        "private module message",
                        error.retry_after_seconds,
                    )
                )
                result = ygl.CapabilityResult(
                    "limited", ygl.ResultStatus.ERROR, error=detail
                )
                text = (await OutputService.__new__(OutputService)._render(result)).text
                self.assertNotIn("private", text)
                if expected is not None:
                    self.assertIn(f"{expected} 秒", text)

    def test_result_hint_rejects_unbounded_or_noninteger_values(self):
        for value in (True, -1, 86401, 1.5, "120"):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                validate_contract(
                    ygl.ErrorDetail(ygl.ErrorCode.RATE_LIMITED, "safe", value)
                )
