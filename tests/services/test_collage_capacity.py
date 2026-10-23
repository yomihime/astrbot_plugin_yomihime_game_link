"""Real resource-capacity failures through the optional collage output cache."""

from __future__ import annotations

import asyncio
import base64
import unittest
from unittest.mock import patch

from ygl_test_subject.services.managed_resources import ResourceCachePolicy
from ygl_test_subject.services.output import OutputStatus

from tests.services import test_collage_output as fixtures
from yomihime_game_link_sdk.errors import (
    CacheUnavailable,
    InvalidInvocation,
    ServiceUnavailable,
)
from yomihime_game_link_sdk.storage import CacheVisibility


class CollageCapacityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bound = fixtures.BoundFinishedPageCacheTests()
        await self.bound.asyncSetUp()
        self.output = fixtures.PrivateCollageOutputTests()
        await self.output.asyncSetUp()
        self.scope, self.current = await self.bound.bound("alice")
        self.manager = self.bound.runtime.repositories.resources.managed_cache
        self.manager.policy = ResourceCachePolicy(
            max_file_bytes=1024, max_bytes=1024, max_items=1
        )
        self.occupied = await self.scope.resources.store(
            "image/png",
            fixtures._png("#111111").content,
            visibility=CacheVisibility.USER,
        )

    async def asyncTearDown(self):
        await self.output.asyncTearDown()
        await self.bound.asyncTearDown()

    async def test_real_bound_capacity_keeps_complete_rendered_image_chain(self):
        self.output.scope = self.scope
        async with self.manager.lease(self.occupied.asset_id, self.occupied.scope):
            outcome = await self.output.fixture.output.route(
                self.output.fixture.view, self.output.result
            )
        self.assertIs(outcome.status, OutputStatus.SENT, outcome)
        self.assertEqual(self.output.render_calls, 1)
        self.assertEqual(self.output.text_sends, [])
        self.assertEqual(len(self.output.event.calls), 1)
        self.assertEqual(
            [
                base64.b64decode(part.file.removeprefix("base64://"))
                for part in self.output.event.calls[0]
            ],
            [page.content for page in self.output.bitmaps],
        )
        for _ in range(20):
            if not self.output.fixture.output._collage_png._entries:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(self.output.fixture.output._collage_png._entries, {})
        self.assertEqual((await self.scope.resources.stats()).items, 1)
        bob, current_bob = await self.bound.bound("bob")
        self.assertIsNone(
            await fixtures.load_finished_pages(bob, "collage-capacity", current_bob)
        )

    async def test_real_bound_capacity_uses_canonical_cache_unavailable(self):
        async with self.manager.lease(self.occupied.asset_id, self.occupied.scope):
            with self.assertRaises(CacheUnavailable):
                await self.scope.resources.store(
                    "image/png",
                    fixtures._png("#222222").content,
                    visibility=CacheVisibility.USER,
                )

    async def test_capacity_mapping_rechecks_current_authority(self):
        original = self.manager.store

        async def revoke_then_capacity(*args, **kwargs):
            try:
                return await original(*args, **kwargs)
            finally:
                view, lease = self.bound.active.pop()
                self.bound.runtime.issuer.release(view)
                self.bound.runtime.lifecycle.admission.release(lease)

        async with self.manager.lease(self.occupied.asset_id, self.occupied.scope):
            with patch.object(self.manager, "store", revoke_then_capacity):
                with self.assertRaises(InvalidInvocation):
                    await self.scope.resources.store(
                        "image/png",
                        fixtures._png("#333333").content,
                        visibility=CacheVisibility.USER,
                    )

    async def test_other_storage_outage_is_not_optional_capacity(self):
        async def unavailable(*_args, **_kwargs):
            raise ServiceUnavailable()

        with patch.object(self.manager, "store", unavailable):
            with self.assertRaises(ServiceUnavailable) as caught:
                await self.scope.resources.store(
                    "image/png",
                    fixtures._png("#444444").content,
                    visibility=CacheVisibility.USER,
                )
        self.assertIs(type(caught.exception), ServiceUnavailable)
