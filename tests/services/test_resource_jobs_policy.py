"""Persistent pins, raw reservation and metadata-hit LRU over real resources."""

import unittest

from ygl_test_subject.services.managed_resources import (
    ManagedResourceCache,
    ResourceCachePolicy,
)

from tests.integration import test_managed_resources as fixtures
from yomihime_game_link_sdk.errors import AccessDenied, InvalidInvocation
from yomihime_game_link_sdk.services import ResourceCacheClass
from yomihime_game_link_sdk.storage import CacheVisibility, OwnerScope


class ResourceJobPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = fixtures.ManagedResourceConsumerTests()
        await self.fixture.asyncSetUp()
        self.repository = self.fixture.runtime.repositories.resources
        self.repository.managed_cache.policy = ResourceCachePolicy(
            max_file_bytes=1024, max_bytes=4096, max_items=2
        )
        _, self.bound = await self.fixture.bound()

    async def asyncTearDown(self):
        await self.fixture.asyncTearDown()

    async def store(self, color, cache_class=ResourceCacheClass.DEFAULT):
        return await self.bound.resources.store(
            "image/png",
            fixtures.png(color),
            visibility=CacheVisibility.PUBLIC,
            cache_class=cache_class,
        )

    async def test_metadata_hit_A_B_A_C_retains_A_and_evicts_B(self):
        first, second = await self.store(1), await self.store(2)
        self.assertIsNotNone(await self.bound.resources.metadata(first.asset_id))
        await self.store(3)
        self.assertIsNotNone(await self.bound.resources.metadata(first.asset_id))
        self.assertIsNone(await self.bound.resources.metadata(second.asset_id))

    async def test_raw_reservation_survives_derived_pressure(self):
        self.repository.managed_cache.policy = ResourceCachePolicy(
            max_file_bytes=1024, max_bytes=4096, max_items=4, raw_reserved_fraction=0.5
        )
        originals = [
            await self.store(color, ResourceCacheClass.RAW) for color in (1, 2)
        ]
        for color in range(3, 10):
            await self.store(color, ResourceCacheClass.DERIVED)
        for original in originals:
            self.assertIsNotNone(await self.bound.resources.metadata(original.asset_id))
        self.assertEqual(
            (await self.bound.resources.stats(visibility=CacheVisibility.PUBLIC)).items,
            4,
        )

    async def test_persistent_pin_survives_manager_recreation_and_foreign_unpin_denied(
        self,
    ):
        first, second = await self.store(1), await self.store(2)
        pin = await self.bound.resources.pin((first.asset_id,))
        previous = self.repository.managed_cache
        self.repository._managed_cache = ManagedResourceCache(
            self.repository, policy=previous.policy
        )
        _, bob = await self.fixture.bound("bob")
        with self.assertRaises((PermissionError, AccessDenied, InvalidInvocation)):
            await bob.resources.unpin(pin)
        await self.store(3)
        self.assertIsNotNone(await self.bound.resources.metadata(first.asset_id))
        self.assertIsNone(await self.bound.resources.metadata(second.asset_id))
        await self.bound.resources.unpin(pin)
        pins = await self.repository.pinned_asset_ids(
            OwnerScope.public(), fixtures.datetime.now(fixtures.UTC)
        )
        self.assertEqual(pins, ())

    async def test_public_raw_pin_does_not_grant_other_owners_private_resource_access(
        self,
    ):
        private = await self.bound.resources.store("image/png", fixtures.png(5))
        pin = await self.bound.resources.pin((private.asset_id,))
        _, bob = await self.fixture.bound("bob")
        self.assertIsNone(await bob.resources.metadata(private.asset_id))
        await self.bound.resources.unpin(pin)
        self.assertIsNotNone(await self.bound.resources.metadata(private.asset_id))

    async def test_pin_commit_rechecks_real_invocation_after_repository_await(self):
        invocation, bound = await self.fixture.bound("bob")
        resource = await bound.resources.store("image/png", fixtures.png(8))
        original = self.repository.pin_resources

        async def revoke_then_pin(*args, **kwargs):
            self.fixture.runtime.issuer.release(invocation)
            self.fixture.views = [
                pair for pair in self.fixture.views if pair[0] is not invocation
            ]
            return await original(*args, **kwargs)

        self.repository.pin_resources = revoke_then_pin
        try:
            with self.assertRaises((PermissionError, AccessDenied, InvalidInvocation)):
                await bound.resources.pin((resource.asset_id,))
        finally:
            self.repository.pin_resources = original
        pins = await self.repository.pinned_asset_ids(
            OwnerScope.user(resource.scope.user_id), fixtures.datetime.now(fixtures.UTC)
        )
        self.assertEqual(pins, ())
