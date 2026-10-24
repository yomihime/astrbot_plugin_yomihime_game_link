"""Real resource-capacity failures through the optional collage output cache."""

from __future__ import annotations

import asyncio
import base64
import io
import random
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from ygl_test_subject.infrastructure.files import LocalSafeFileStore
from ygl_test_subject.infrastructure.secret_store import SQLiteSecretStore
from ygl_test_subject.infrastructure.sqlite.repositories import SQLiteRepositories
from ygl_test_subject.services.managed_resources import ResourceCachePolicy
from ygl_test_subject.services.module_services import (
    ModuleServicesFactory,
    RegistryRegistrationLookup,
)
from ygl_test_subject.services.output import OutputStatus
from ygl_test_subject.services.resources import ResourceAccessService

from tests.fixtures.b03_runtime import OfflineHttpTransport, _TestCodec
from tests.integration import test_managed_resources as managed_fixtures
from tests.services import test_collage_output as fixtures
from yomihime_game_link_sdk.cards import (
    CollageItem,
    Document,
    ResourceImage,
    WeightedCollage,
)
from yomihime_game_link_sdk.errors import (
    AccessDenied,
    CacheUnavailable,
    InvalidInvocation,
    ServiceUnavailable,
)
from yomihime_game_link_sdk.storage import CacheVisibility


class BoundCollageReferenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.subject = managed_fixtures.ManagedResourceConsumerTests()
        await self.subject.asyncSetUp()

    async def asyncTearDown(self):
        await self.subject.asyncTearDown()

    async def test_real_public_and_user_snapshots_use_two_outer_checks_and_one_current(
        self,
    ):
        _, scope = await self.subject.bound()
        payload = fixtures._png("#123456").content
        resources = scope.resources
        original = resources._BoundResources__check
        checks = 0

        async def checked():
            nonlocal checks
            checks += 1
            await original()

        for visibility in (CacheVisibility.PUBLIC, CacheVisibility.USER):
            ref = await resources.store("image/png", payload, visibility=visibility)
            checks = 0
            current_calls = 0

            async def current():
                nonlocal current_calls
                current_calls += 1
                await original()

            card = Document(
                "Real snapshot",
                (
                    WeightedCollage(
                        (
                            CollageItem(
                                "one",
                                1,
                                ResourceImage(ref.asset_id, visibility, required=True),
                            ),
                        )
                    ),
                ),
            )
            with patch.object(resources, "_BoundResources__check", checked):
                resolved, _ = await fixtures.resolve_collage_resources(
                    card, scope, current
                )
            self.assertEqual(checks, 2)
            self.assertEqual(current_calls, 1)
            self.assertEqual(resolved.blocks[0].items[0].image.content, payload)
            metadata, content = await resources._read_collage_reference(
                ref.asset_id, visibility
            )
            self.assertIs(type((metadata, content)), tuple)
            self.assertIs(type(content), bytes)
            with self.assertRaises(AttributeError):
                metadata.media_type = "text/html"
        _, bob = await self.subject.bound("bob")
        with self.assertRaises(FileNotFoundError):
            await bob.resources._read_collage_reference(
                ref.asset_id, CacheVisibility.USER
            )

    async def test_actual_invocation_revocation_after_metadata_or_bytes_rejects_snapshot(
        self,
    ):
        payload = fixtures._png("#234567").content
        for method in ("_collage_snapshot", "_collage_read"):
            with self.subTest(method=method):
                view, scope = await self.subject.bound()
                ref = await scope.resources.store("image/png", payload)
                original = getattr(ResourceAccessService, method)

                async def revoke(service, *args, **kwargs):
                    result = await original(service, *args, **kwargs)
                    self.subject.runtime.issuer.release(view)
                    _, lease = self.subject.views.pop()
                    self.subject.runtime.lifecycle.admission.release(lease)
                    return result

                with patch.object(ResourceAccessService, method, revoke):
                    with self.assertRaises(InvalidInvocation):
                        await scope.resources._read_collage_reference(
                            ref.asset_id, CacheVisibility.USER
                        )

    async def test_cancel_and_missing_preserve_original_resource_semantics(self):
        _, scope = await self.subject.bound()
        ref = await scope.resources.store("image/png", fixtures._png("#345678").content)
        with self.assertRaises(FileNotFoundError):
            await scope.resources._read_collage_reference(
                "asset_absent", CacheVisibility.USER
            )
        entered, release = asyncio.Event(), asyncio.Event()
        original = ResourceAccessService._collage_read

        async def paused(service, *args, **kwargs):
            entered.set()
            await release.wait()
            return await original(service, *args, **kwargs)

        with patch.object(ResourceAccessService, "_collage_read", paused):
            task = asyncio.create_task(
                scope.resources._read_collage_reference(
                    ref.asset_id, CacheVisibility.USER
                )
            )
            entered_wait = asyncio.create_task(entered.wait())
            try:
                done, _ = await asyncio.wait(
                    (task, entered_wait), return_when=asyncio.FIRST_COMPLETED
                )
                self.assertIn(entered_wait, done)
            finally:
                entered_wait.cancel()
                await asyncio.gather(entered_wait, return_exceptions=True)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(
            await scope.resources.read(ref.asset_id), fixtures._png("#345678").content
        )

    async def test_actual_grant_revocation_after_metadata_or_bytes_rejects_public_snapshot(
        self,
    ):
        _, public = await self.subject.bound()
        ref = await public.resources.store(
            "image/png",
            fixtures._png("#234567").content,
            visibility=CacheVisibility.PUBLIC,
        )
        base = await self.subject.grant()
        for method in ("_collage_snapshot", "_collage_read"):
            with self.subTest(method=method):
                grant = replace(
                    base, grant_id="snapshot-" + method, account_id="snapshot-" + method
                )
                await self.subject.runtime.repositories.authorization.create_grant(
                    grant, expected_revision=0
                )
                _, scope = await self.subject.bound(
                    capability="private.read", grant=grant
                )
                original = getattr(ResourceAccessService, method)

                async def revoke(service, *args, **kwargs):
                    result = await original(service, *args, **kwargs)
                    await self.subject.runtime.repositories.authorization.revoke_grant(
                        grant, expected_revision=1
                    )
                    return result

                with patch.object(ResourceAccessService, method, revoke):
                    with self.assertRaises(AccessDenied):
                        await scope.resources._read_collage_reference(
                            ref.asset_id, CacheVisibility.PUBLIC
                        )

    async def test_real_snapshot_retains_missing_required_and_source_budget(self):
        _, scope = await self.subject.bound()

        async def current():
            return None

        absent = Document(
            "Absent",
            (
                WeightedCollage(
                    (
                        CollageItem(
                            "one",
                            1,
                            ResourceImage(
                                "asset_absent", CacheVisibility.USER, required=True
                            ),
                        ),
                    )
                ),
            ),
        )
        with self.assertRaises(fixtures.RequiredCollageResourceUnavailable):
            await fixtures.resolve_collage_resources(absent, scope, current)
        content = fixtures._png("#456789").content
        ref = await scope.resources.store("image/png", content)
        repeated = Document(
            "Budget",
            (
                WeightedCollage(
                    tuple(
                        CollageItem(
                            f"tile{i}",
                            3 - i,
                            ResourceImage(
                                ref.asset_id, CacheVisibility.USER, required=True
                            ),
                        )
                        for i in range(3)
                    )
                ),
            ),
        )
        with patch(
            "ygl_test_subject.services.collage_cache._SOURCE_LIMIT", len(content) * 2
        ):
            with self.assertRaisesRegex(ValueError, "collage source budget"):
                await fixtures.resolve_collage_resources(repeated, scope, current)

    async def test_invalid_media_and_oversized_optional_refs_never_explicitly_read(
        self,
    ):
        _, scope = await self.subject.bound()
        stream = io.BytesIO()
        Image.frombytes(
            "RGB", (640, 600), random.Random(17).randbytes(640 * 600 * 3)
        ).save(stream, format="PNG")
        oversized = stream.getvalue()
        self.assertGreater(len(oversized), 1_048_576)
        refs = (
            await scope.resources.store("application/octet-stream", b"not an image"),
            await scope.resources.store("image/png", oversized),
        )
        current_calls = 0

        async def current():
            nonlocal current_calls
            current_calls += 1

        # Both actual metadata faults remain optional on every different ref;
        # repeated tiles also keep their usual per-invocation deduplication.
        card = Document(
            "Invalid optional sources",
            (
                WeightedCollage(
                    tuple(
                        CollageItem(
                            f"tile{i}",
                            60 - i,
                            ResourceImage(refs[i % 2].asset_id, CacheVisibility.USER),
                        )
                        for i in range(60)
                    )
                ),
            ),
        )
        with patch.object(
            ResourceAccessService,
            "_collage_read",
            side_effect=AssertionError("metadata rejected before bytes"),
        ) as read:
            with patch("ygl_test_subject.services.collage_cache._SOURCE_LIMIT", 1):
                resolved, versions = await fixtures.resolve_collage_resources(
                    card, scope, current
                )
            self.assertEqual(read.call_count, 0)
        self.assertTrue(all(item.image is None for item in resolved.blocks[0].items))
        self.assertTrue(all(version[-1] == "missing" for version in versions))
        self.assertEqual(current_calls, 2)
        required = replace(
            card,
            blocks=(
                replace(
                    card.blocks[0],
                    items=(
                        replace(
                            card.blocks[0].items[1],
                            image=replace(card.blocks[0].items[1].image, required=True),
                        ),
                    ),
                ),
            ),
        )
        with patch.object(
            ResourceAccessService,
            "_collage_read",
            side_effect=AssertionError("oversized source must not read"),
        ) as read:
            with self.assertRaises(fixtures.RequiredCollageResourceUnavailable):
                await fixtures.resolve_collage_resources(required, scope, current)
            self.assertEqual(read.call_count, 0)

    async def test_real_snapshot_does_not_bypass_required_image_decoder(self):
        _, scope = await self.subject.bound()
        content = b"not an image"
        ref = await scope.resources.store("application/octet-stream", content)

        # Privileged corruption fixture: hash/bytes stay valid but media lies.
        def wrong_media(unit):
            unit.execute(
                "UPDATE assets SET media_type='image/png' WHERE asset_id=?",
                (ref.asset_id,),
            )

        await self.subject.runtime.database.executor.run_transaction(wrong_media)
        card = Document(
            "Invalid image",
            (
                WeightedCollage(
                    (
                        CollageItem(
                            "one",
                            1,
                            ResourceImage(
                                ref.asset_id, CacheVisibility.USER, required=True
                            ),
                        ),
                    )
                ),
            ),
        )

        async def current():
            return None

        resolved, _ = await fixtures.resolve_collage_resources(card, scope, current)
        document = replace(
            fixtures.GeneratedPngFixture.new_result().document, card=resolved
        )
        presentation = fixtures.CommandCardRenderer()(
            document, audience=fixtures.DisplayAudience.PUBLIC
        )
        self.assertIsNone(presentation.bitmap)
        self.assertEqual(presentation.bitmaps, ())

    async def test_owner_route_and_sqlite_principal_change_at_snapshot_end_reject(self):
        for mutation, phase in (
            ("route", "_collage_snapshot"),
            ("route", "_collage_read"),
            ("principal", "_collage_snapshot"),
            ("principal", "_collage_read"),
        ):
            with self.subTest(mutation=mutation, phase=phase):
                owner = await fixtures.GeneratedPngFixture().start(owner=True)
                try:
                    files = LocalSafeFileStore(Path(owner.temp.name) / "assets")
                    secrets = SQLiteSecretStore(
                        owner.db, Path(owner.temp.name) / "secrets", codec=_TestCodec()
                    )
                    repositories = SQLiteRepositories(
                        owner.db,
                        RegistryRegistrationLookup(owner.registry),
                        file_store=files,
                        secret_store=secrets,
                    )
                    factory = ModuleServicesFactory(
                        owner.registry,
                        owner.issuer,
                        owner.lifecycle,
                        repositories,
                        OfflineHttpTransport(),
                        config_principal_id="host-config",
                        identity_namespace="png-users",
                        principal_resolver=owner.principals,
                        owner_authority=owner.owner,
                    )
                    scope = await factory.for_module("sample/demo").scopes.bind(
                        owner.view
                    )
                    ref = await scope.resources.store(
                        "image/png", fixtures._png("#567890").content
                    )
                    original = getattr(ResourceAccessService, phase)

                    async def changed(service, *args, **kwargs):
                        result = await original(service, *args, **kwargs)
                        async with owner.admission.mutation("snapshot-owner-change"):
                            if mutation == "route":
                                owner.routes.value = replace(
                                    owner.route, delivery_route="changed-route"
                                )
                            else:

                                def remap(unit):
                                    unit.execute(
                                        "UPDATE principals SET external_user_id=? WHERE principal_id=?",
                                        ("former-actor", "principal-SENSITIVE"),
                                    )
                                    unit.execute(
                                        "INSERT INTO principals(principal_id,identity_namespace,external_user_id) VALUES (?,?,?)",
                                        (
                                            "changed-principal",
                                            "png-users",
                                            "actor-SENSITIVE",
                                        ),
                                    )

                                await owner.db.executor.run_transaction(remap)
                        return result

                    with patch.object(ResourceAccessService, phase, changed):
                        with self.assertRaises(AccessDenied):
                            await scope.resources._read_collage_reference(
                                ref.asset_id, CacheVisibility.USER
                            )
                    await factory.close_credentials()
                finally:
                    await owner.stop()


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
