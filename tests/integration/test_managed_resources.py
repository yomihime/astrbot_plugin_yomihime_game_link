"""Real public consumers over the production binder and binary repositories."""

from __future__ import annotations

import asyncio
import os
import struct
import tempfile
import unittest
import zlib
from contextlib import AsyncExitStack
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.core.contracts.services import Grant, GrantStatus
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.registry import Registry
from ygl_test_subject.presentation.card_bitmap import RenderedBitmap
from ygl_test_subject.services.collage_cache import (
    inspect_collage_resources,
    load_finished_pages,
    resolve_collage_resources,
    store_finished_pages,
)
from ygl_test_subject.services.managed_resources import ResourceCachePolicy

from tests.fixtures.b03_runtime import _Collector, _Handler, _manifest, build_runtime
from yomihime_game_link_sdk import cards
from yomihime_game_link_sdk.contexts import InvocationOrigin
from yomihime_game_link_sdk.declarations import CapabilityReference, PackageManifest
from yomihime_game_link_sdk.errors import (
    AccessDenied,
    InvalidInvocation,
    ParameterError,
    ServiceUnavailable,
)
from yomihime_game_link_sdk.services import ModuleHandlers
from yomihime_game_link_sdk.storage import (
    CacheVisibility,
    OwnerScope,
    ResourceMetadata,
    SecretRef,
)
from yomihime_game_link_sdk.version import MODULE_ABI_VERSION


def png(color=37):
    def chunk(name, body):
        return (
            struct.pack(">I", len(body))
            + name
            + body
            + struct.pack(">I", zlib.crc32(name + body) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes((0, color, 131, 231, 255))))
        + chunk(b"IEND", b"")
    )


def consumer_registry():
    handler = _Handler()
    private = _Handler("private.read")
    alpha = _manifest("alpha", (CapabilityReference("sample/beta", "read"),))
    alpha = replace(
        alpha,
        capabilities=tuple(
            replace(
                c, required_capabilities=(CapabilityReference("sample/beta", "read"),)
            )
            if c.capability_id == "private.read"
            else c
            for c in alpha.capabilities
        ),
    )
    registry = Registry()
    registry.register_package(
        validate_contract(
            PackageManifest(
                "sample",
                "1.0.0",
                MODULE_ABI_VERSION,
                (alpha, _manifest("beta")),
                "tests",
                "MIT",
                "generic public binary consumer",
            )
        ),
        {
            "alpha": ModuleHandlers(
                {"read": handler, "private.read": private},
                {"account-collector": _Collector()},
                {},
            ),
            "beta": ModuleHandlers({"read": handler}, {}, {}),
        },
    )
    return registry


class ManagedResourceConsumerTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_derivatives_cross_users_and_reject_private_source(self):
        _, preparation = await self.bound()
        with self.assertRaises(ParameterError):
            await preparation.cache.put(
                "core-collage-public-v1-forged",
                {"id": "forged"},
                ttl_seconds=60,
                visibility=CacheVisibility.PUBLIC,
            )
        reference = await preparation.resources.store(
            "image/png", png(), visibility=CacheVisibility.PUBLIC
        )
        _, alice = await self.bound(capability="private.read", grant=await self.grant())
        _, bob = await self.bound(
            "bob", capability="private.read", grant=await self.grant("bob")
        )
        card = cards.Document(
            "Private selection",
            (
                cards.DenseCollage(
                    (
                        cards.DenseCollageItem(
                            "2", 2, cards.ResourceImage(reference.asset_id)
                        ),
                    )
                ),
            ),
            width=1080,
        )

        async def current():
            return None

        with self.assertRaises(AccessDenied):
            await alice.resources.store(
                "image/png", png(), visibility=CacheVisibility.PUBLIC
            )
        prepared, _, snapshots = await inspect_collage_resources(card, alice, current)
        await resolve_collage_resources(
            prepared, alice, current, snapshots=snapshots, may_cache_derivatives=True
        )
        files = self.runtime.repositories.resources.file_store
        original = files.read
        reads = []

        async def read(asset_id, scope):
            reads.append(asset_id)
            return await original(asset_id, scope)

        with patch.object(files, "read", read):
            prepared, _, snapshots = await inspect_collage_resources(card, bob, current)
            await resolve_collage_resources(
                prepared, bob, current, snapshots=snapshots, may_cache_derivatives=True
            )
        self.assertEqual(len(reads), 1)  # derivative fully verified, original not read
        self.assertNotIn(reference.asset_id, reads)
        self.assertEqual(
            (await alice.resources.stats(visibility=CacheVisibility.USER)).items, 0
        )
        self.assertEqual(
            (await bob.resources.stats(visibility=CacheVisibility.USER)).items, 0
        )
        personal = await alice.resources.store(
            "image/png", png(91), visibility=CacheVisibility.USER
        )
        snapshot = await alice.resources._collage_snapshot(
            personal.asset_id, CacheVisibility.USER
        )
        with self.assertRaises(AccessDenied):
            await alice.resources._collage_store_derivative(
                snapshot, (108, 162), "cover", png(91)
            )
        with self.assertRaises(AccessDenied):
            await alice.cache._collage_public_lookup(
                alice.resources, snapshot, (108, 162), "cover"
            )

    async def test_public_derivative_write_rechecks_revoked_invocation(self):
        view, bound = await self.bound(
            capability="private.read", grant=await self.grant()
        )
        _, preparation = await self.bound()
        reference = await preparation.resources.store(
            "image/png", png(), visibility=CacheVisibility.PUBLIC
        )
        snapshot = await bound.resources._collage_snapshot(
            reference.asset_id, CacheVisibility.PUBLIC
        )
        repository = self.runtime.repositories.cache
        original = repository.put

        async def revoke(*args, **kwargs):
            self.runtime.issuer.release(view)
            entry = next(pair for pair in self.views if pair[0] is view)
            self.runtime.lifecycle.admission.release(entry[1])
            self.views.remove(entry)
            return await original(*args, **kwargs)

        with self.assertRaises((InvalidInvocation, AccessDenied)):
            async with bound.resources._collage_source_leases(
                {(CacheVisibility.PUBLIC, reference.asset_id): snapshot}
            ):
                derivative = await bound.resources._collage_store_derivative(
                    snapshot, (108, 162), "cover", png()
                )
                with patch.object(repository, "put", revoke):
                    await bound.cache._collage_public_put(derivative)

    async def test_dense_derivatives_cannot_evict_current_invocation_sources(self):
        _, bound = await self.bound()
        policy = self.runtime.repositories.resources.managed_cache
        policy.policy = ResourceCachePolicy(max_items=2)
        first = await bound.resources.store(
            "image/png", png(1), visibility=CacheVisibility.PUBLIC
        )
        second = await bound.resources.store(
            "image/png", png(2), visibility=CacheVisibility.PUBLIC
        )
        card = cards.Document(
            "Capacity",
            (
                cards.DenseCollage(
                    tuple(
                        cards.DenseCollageItem(
                            str(index), 2, cards.ResourceImage(reference.asset_id)
                        )
                        for index, reference in enumerate((first, second))
                    )
                ),
            ),
            width=1080,
        )

        async def current():
            return None

        prepared, _, snapshots = await inspect_collage_resources(card, bound, current)
        resolved, _ = await resolve_collage_resources(
            prepared, bound, current, snapshots=snapshots, may_cache_derivatives=True
        )
        self.assertTrue(
            all(type(item.image) is cards.Asset for item in resolved.blocks[0].items)
        )
        self.assertEqual(
            await bound.resources.read(
                first.asset_id, visibility=CacheVisibility.PUBLIC
            ),
            png(1),
        )
        self.assertEqual(
            await bound.resources.read(
                second.asset_id, visibility=CacheVisibility.PUBLIC
            ),
            png(2),
        )
        # The invocation lease ends after preparation, so future bounded stores
        # can make progress rather than retaining an unbounded pin.
        await bound.resources.store(
            "image/png", png(3), visibility=CacheVisibility.PUBLIC
        )

    async def test_dense_derivatives_reuse_per_size_and_unknown_identity_rehashes(self):
        view, bound = await self.bound()
        ref = await bound.resources.store(
            "image/png", png(), visibility=CacheVisibility.PUBLIC
        )
        card = cards.Document(
            "Career",
            (
                cards.DenseCollage(
                    (
                        cards.DenseCollageItem(
                            "2", 2, cards.ResourceImage(ref.asset_id)
                        ),
                        cards.DenseCollageItem(
                            "10", 1, cards.ResourceImage(ref.asset_id)
                        ),
                    )
                ),
            ),
            width=1080,
        )

        async def current():
            return None

        files = self.runtime.repositories.resources.file_store
        original = files.read
        reads = []

        async def read(asset_id, scope):
            reads.append(asset_id)
            return await original(asset_id, scope)

        with patch.object(files, "read", read):
            prepared, _, snapshots = await inspect_collage_resources(
                card, bound, current
            )
            await resolve_collage_resources(
                prepared,
                bound,
                current,
                snapshots=snapshots,
                may_cache_derivatives=True,
            )
            self.assertEqual(reads.count(ref.asset_id), 1)
            reads.clear()
            prepared, _, snapshots = await inspect_collage_resources(
                card, bound, current
            )
            await resolve_collage_resources(
                prepared,
                bound,
                current,
                snapshots=snapshots,
                may_cache_derivatives=True,
            )
            self.assertNotIn(ref.asset_id, reads)
            self.assertEqual(len(reads), 2)  # each dimension's derivative verified
            reads.clear()

            async def unknown_identity(*args):
                return None

            with patch.object(files, "identity", unknown_identity):
                await inspect_collage_resources(card, bound, current)
            self.assertEqual(reads, [ref.asset_id])
        self.runtime.issuer.release(view)
        self.runtime.lifecycle.admission.release(self.views[-1][1])
        self.views.pop()
        with self.assertRaises(InvalidInvocation):
            await bound.resources._collage_snapshot(
                ref.asset_id, CacheVisibility.PUBLIC
            )

    async def test_collage_snapshot_reads_once_then_restart_hot_without_source_reads(
        self,
    ):
        _, bound = await self.bound()
        payload = png()
        ref = await bound.resources.store(
            "image/png", payload, visibility=CacheVisibility.PUBLIC
        )
        card = cards.Document(
            "Career",
            (
                cards.DenseCollage(
                    (
                        cards.DenseCollageItem(
                            "2", 2, cards.ResourceImage(ref.asset_id)
                        ),
                        cards.DenseCollageItem(
                            "10", 1, cards.ResourceImage(ref.asset_id)
                        ),
                    )
                ),
            ),
            width=1080,
        )

        async def current():
            return None

        files = self.runtime.repositories.resources.file_store

        # Simulate a pre-upgrade index: first admission hashes exactly once.
        def clear_integrity(unit):
            unit.execute("DELETE FROM resource_integrity_snapshots")

        await self.runtime.database.executor.run_transaction(clear_integrity)
        original = files.read
        reads = []

        async def read(asset_id, scope):
            reads.append(asset_id)
            return await original(asset_id, scope)

        with patch.object(files, "read", read):
            prepared, versions, snapshots = await inspect_collage_resources(
                card, bound, current
            )
            resolved, _ = await resolve_collage_resources(
                prepared, bound, current, snapshots=snapshots
            )
            self.assertEqual(resolved.blocks[0].items[0].image.kind, "png")
            self.assertEqual(reads, [ref.asset_id])
            reads.clear()
            _, hot_versions, _ = await inspect_collage_resources(card, bound, current)
            self.assertEqual(versions, hot_versions)
            self.assertEqual(reads, [])
        page = RenderedBitmap(png(99), 1, 1)
        await store_finished_pages(bound, "integrity-hot", (page,), current)
        await self.close_runtime()
        self.runtime = await build_runtime(self.root, registry=consumer_registry())
        _, bound = await self.bound()
        files = self.runtime.repositories.resources.file_store
        original = files.read
        with patch.object(files, "read", read):
            _, restarted_versions, _ = await inspect_collage_resources(
                card, bound, current
            )
            cached = await load_finished_pages(bound, "integrity-hot", current)
        self.assertEqual(restarted_versions, versions)
        self.assertEqual(cached, (page,))
        self.assertNotIn(ref.asset_id, reads)
        self.assertEqual(len(reads), 1)  # finished PNG still fully read/hash checked

    async def test_integrity_detects_in_place_same_size_edit_with_restored_mtime(self):
        _, bound = await self.bound()
        payload = png()
        ref = await bound.resources.store(
            "image/png", payload, visibility=CacheVisibility.PUBLIC
        )
        self.assertIsNotNone(await bound.resources.metadata(ref.asset_id))
        files = self.runtime.repositories.resources.file_store
        path = files._final(ref.asset_id, OwnerScope.public())
        before = path.stat()
        altered = bytearray(payload)
        altered[len(altered) // 2] ^= 1
        with path.open("r+b") as stream:
            stream.write(altered)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertIsNone(await bound.resources.metadata(ref.asset_id))
        healed = await bound.resources.store(
            "image/png", payload, visibility=CacheVisibility.PUBLIC
        )
        self.assertEqual(healed, ref)
        self.assertEqual(await bound.resources.read(ref.asset_id), payload)

    async def test_expired_unmanaged_store_rejects_without_renewal_or_adoption(self):
        _, bound = await self.bound()
        repo = self.runtime.repositories.resources
        stage = await repo.file_store.stage(
            "expired-legacy", png(), OwnerScope.public()
        )
        expired = ResourceMetadata(
            stage.asset_id,
            "image/png",
            OwnerScope.public(),
            len(png()),
            datetime.now(UTC) - timedelta(seconds=10),
        )
        await repo.file_store.commit(stage, expired)
        registered = await repo.register(expired)
        with self.assertRaises(ParameterError):
            await bound.resources.store(
                "image/png", png(), visibility=CacheVisibility.PUBLIC
            )
        self.assertEqual(
            await repo.registration_state(stage.asset_id, OwnerScope.public()),
            registered,
        )
        self.assertIsNone(await repo.managed_entry(stage.asset_id, OwnerScope.public()))
        self.assertIsNone(await bound.resources.metadata(stage.asset_id))

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = await build_runtime(self.root, registry=consumer_registry())
        self.views = []

    async def asyncTearDown(self):
        await self.close_runtime()
        self.temp.cleanup()

    async def close_runtime(self):
        for view, lease in self.views:
            self.runtime.issuer.release(view)
            self.runtime.lifecycle.admission.release(lease)
        self.views.clear()
        await self.runtime.services.close_credentials()
        await self.runtime.database.executor.close(timeout=2)

    async def bound(
        self, actor="alice", capability="read", grant=None, module="sample/alpha"
    ):
        registered = self.runtime.registry.snapshot().module(module)
        view = self.runtime.issuer.issue(
            origin=InvocationOrigin.COMMAND,
            module_id=module,
            module_epoch=registered.epoch,
            registry_revision=self.runtime.registry.snapshot().revision,
            actor_id=actor,
            conversation_id="room",
            adapter_id="test-adapter",
            capability_id=capability,
            grant_id=None if grant is None else grant.grant_id,
            grant_revision=None if grant is None else grant.revision,
        )
        lease = self.runtime.lifecycle.admission.admit(view, capability)
        self.views.append((view, lease))
        return view, await self.runtime.services.for_module(module).scopes.bind(view)

    async def grant(self, actor="alice"):
        grant = Grant(
            "resource-test-grant"
            if actor == "alice"
            else "resource-test-grant-" + actor,
            1,
            actor,
            "sample/alpha",
            "account",
            ("private.read",),
            SecretRef(
                "secret_resource",
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

    async def test_public_consumer_roundtrip_restart_cross_user_and_scoped_stats(self):
        _, alice = await self.bound()
        payload = png()
        first = await alice.resources.store(
            "image/png", payload, visibility=CacheVisibility.PUBLIC
        )
        _, second = await self.bound()
        self.assertEqual(await second.resources.read(first.asset_id), payload)
        self.assertEqual(
            (await second.resources.metadata(first.asset_id)).media_type, "image/png"
        )
        _, bob = await self.bound("bob")
        reused = await bob.resources.store(
            "image/png", payload, visibility=CacheVisibility.PUBLIC
        )
        self.assertEqual(reused, first)
        stats = await bob.resources.stats(visibility=CacheVisibility.PUBLIC)
        self.assertEqual(
            (stats.bytes, stats.items, stats.evictions), (len(payload), 1, 0)
        )
        self.assertEqual((await bob.resources.stats()).items, 0)
        await self.close_runtime()
        self.runtime = await build_runtime(self.root, registry=consumer_registry())
        _, cold = await self.bound("bob")
        self.assertEqual(
            await cold.resources.read(
                first.asset_id, visibility=CacheVisibility.PUBLIC
            ),
            payload,
        )
        self.assertEqual(
            (await cold.resources.stats(visibility=CacheVisibility.PUBLIC)).items, 1
        )

    async def test_missing_same_length_corruption_and_repair(self):
        _, bound = await self.bound()
        payload = png()
        ref = await bound.resources.store(
            "image/png", payload, visibility=CacheVisibility.PUBLIC
        )
        # Privileged fault injection only; consumer never sees a filesystem path.
        path = self.runtime.repositories.resources.file_store._final(
            ref.asset_id, OwnerScope.public()
        )
        for fault in ("missing", "same_length_corrupt"):
            if fault == "missing":
                path.unlink()
            else:
                path.write_bytes(b"x" * len(payload))
            self.assertIsNone(await bound.resources.metadata(ref.asset_id))
            with self.assertRaises(FileNotFoundError):
                await bound.resources.read(ref.asset_id)
            healed = await bound.resources.store(
                "image/png", payload, visibility=CacheVisibility.PUBLIC
            )
            self.assertEqual(healed, ref)
            self.assertEqual(await bound.resources.read(ref.asset_id), payload)

    async def test_concurrent_store_and_cancelled_writer_drain(self):
        _, bound = await self.bound()
        payload = png()
        files = self.runtime.repositories.resources.file_store
        original = files.replace_managed
        entered, release = asyncio.Event(), asyncio.Event()
        writes = 0

        async def paused(stage):
            nonlocal writes
            writes += 1
            entered.set()
            await release.wait()
            await original(stage)

        with patch.object(files, "replace_managed", paused):
            cancelled = asyncio.create_task(
                bound.resources.store(
                    "image/png", payload, visibility=CacheVisibility.PUBLIC
                )
            )
            await entered.wait()
            cancelled.cancel()
            waiter = asyncio.create_task(
                bound.resources.store(
                    "image/png", payload, visibility=CacheVisibility.PUBLIC
                )
            )
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await cancelled
            ref = await waiter
        self.assertEqual(writes, 1)
        self.assertEqual(await bound.resources.read(ref.asset_id), payload)
        self.assertEqual(
            (await bound.resources.stats(visibility=CacheVisibility.PUBLIC)).items, 1
        )
        self.assertEqual(await files.recover_orphans(), ())

    async def test_capacity_lru_physical_cleanup_legacy_and_reader_lease(self):
        _, bound = await self.bound()
        repo = self.runtime.repositories.resources
        manager = repo.managed_cache
        manager.policy = ResourceCachePolicy(
            max_file_bytes=80, max_bytes=160, max_items=2
        )
        legacy = await repo.file_store.stage("legacy", b"legacy", OwnerScope.public())
        from yomihime_game_link_sdk.storage import ResourceMetadata

        metadata = ResourceMetadata(
            legacy.asset_id, "application/octet-stream", OwnerScope.public(), 6, None
        )
        await repo.file_store.commit(legacy, metadata)
        await repo.register(metadata)
        a = await bound.resources.store(
            "image/png", png(1), visibility=CacheVisibility.PUBLIC
        )
        b = await bound.resources.store(
            "image/png", png(2), visibility=CacheVisibility.PUBLIC
        )
        async with manager.lease(a.asset_id, OwnerScope.public()):
            async with manager.lease(b.asset_id, OwnerScope.public()):
                with self.assertRaises(ServiceUnavailable):
                    await bound.resources.store(
                        "image/png", png(3), visibility=CacheVisibility.PUBLIC
                    )
            c = await bound.resources.store(
                "image/png", png(3), visibility=CacheVisibility.PUBLIC
            )
            self.assertEqual(await bound.resources.read(a.asset_id), png(1))
        self.assertIsNone(await bound.resources.metadata(b.asset_id))
        self.assertFalse(
            repo.file_store._final(b.asset_id, OwnerScope.public()).exists()
        )
        self.assertEqual(await bound.resources.read(c.asset_id), png(3))
        self.assertEqual(await bound.resources.read(legacy.asset_id), b"legacy")
        stats = await bound.resources.stats(visibility=CacheVisibility.PUBLIC)
        self.assertEqual((stats.items, stats.evictions), (2, 1))
        self.assertLessEqual(stats.bytes, stats.max_bytes)

    async def test_capacity_excludes_leased_prefix_before_selecting_candidates(self):
        _, alice = await self.bound()
        _, bob = await self.bound("bob")
        repo = self.runtime.repositories.resources
        manager = repo.managed_cache
        manager.policy = ResourceCachePolicy(
            max_file_bytes=128, max_bytes=128 * 33, max_items=33
        )
        bob_ref = await bob.resources.store("image/png", png(90))
        refs = [
            await alice.resources.store("image/png", png(color)) for color in range(33)
        ]
        async with AsyncExitStack() as readers:
            for ref in refs[:32]:
                await readers.enter_async_context(
                    manager.lease(ref.asset_id, ref.scope)
                )
            admitted = await alice.resources.store("image/png", png(33))
            for ref in refs[:32]:
                self.assertTrue(
                    repo.file_store._final(ref.asset_id, ref.scope).exists()
                )
        self.assertEqual(await alice.resources.read(admitted.asset_id), png(33))
        self.assertIsNone(await alice.resources.metadata(refs[32].asset_id))
        self.assertFalse(
            repo.file_store._final(refs[32].asset_id, refs[32].scope).exists()
        )
        self.assertEqual(await bob.resources.read(bob_ref.asset_id), png(90))
        stats = await alice.resources.stats()
        self.assertEqual((stats.items, stats.evictions), (33, 1))

    async def test_capacity_continues_bounded_batches_until_large_write_fits(self):
        repo = self.runtime.repositories.resources
        manager = repo.managed_cache
        manager.policy = ResourceCachePolicy(
            max_file_bytes=33, max_bytes=40, max_items=40
        )
        for expired, actor in ((False, "alice"), (True, "bob")):
            with self.subTest(expired=expired):
                _, scope = await self.bound(actor)
                refs = [
                    await scope.resources.store("application/octet-stream", bytes((n,)))
                    for n in range(40)
                ]
                if expired:

                    def expire(unit):
                        unit.execute(
                            "UPDATE managed_resource_entries SET expires_at=? WHERE scope_key=?",
                            (
                                "2000-01-01T00:00:00+00:00",
                                repo.managed_scope_key(refs[0].scope),
                            ),
                        )
                        unit.execute(
                            "UPDATE assets SET expires_at=? WHERE scope_kind='user' AND user_id=?",
                            ("2000-01-01T00:00:00+00:00", actor),
                        )

                    await self.runtime.database.executor.run_transaction(expire)
                batches = []
                original = repo.managed_candidates

                async def candidates(*args, **kwargs):
                    rows = await original(*args, **kwargs)
                    batches.append((kwargs["limit"], len(rows)))
                    return rows

                payload = b"x" * 33
                self.assertEqual(len(payload), 33)
                with patch.object(repo, "managed_candidates", candidates):
                    admitted = await scope.resources.store(
                        "application/octet-stream", payload
                    )
                self.assertEqual(await scope.resources.read(admitted.asset_id), payload)
                stats = await scope.resources.stats()
                self.assertEqual(
                    (stats.bytes, stats.items, stats.evictions), (40, 8, 33)
                )
                self.assertTrue(
                    all(0 < limit <= 32 and rows <= limit for limit, rows in batches)
                )
                self.assertLessEqual(len(batches), 3)
                self.assertEqual(
                    sum(
                        not repo.file_store._final(ref.asset_id, ref.scope).exists()
                        for ref in refs
                    ),
                    33,
                )

    async def test_expired_cleanup_and_private_public_boundary(self):
        _, public = await self.bound()
        ref = await public.resources.store(
            "image/png", png(), visibility=CacheVisibility.PUBLIC, ttl_seconds=0.01
        )
        await asyncio.sleep(0.02)
        self.assertIsNone(await public.resources.metadata(ref.asset_id))
        next_ref = await public.resources.store(
            "image/png", png(90), visibility=CacheVisibility.PUBLIC
        )
        self.assertEqual(
            (await public.resources.stats(visibility=CacheVisibility.PUBLIC)).evictions,
            1,
        )
        self.assertNotEqual(ref.asset_id, next_ref.asset_id)
        grant = await self.grant()
        _, private = await self.bound(capability="private.read", grant=grant)
        self.assertEqual(
            await private.resources.read(
                next_ref.asset_id, visibility=CacheVisibility.PUBLIC
            ),
            png(90),
        )
        with self.assertRaises(AccessDenied):
            await private.resources.store(
                "image/png", png(), visibility=CacheVisibility.PUBLIC
            )
        personal = await private.resources.store("image/png", png())
        _, bob = await self.bound("bob")
        self.assertIsNone(await bob.resources.metadata(personal.asset_id))

    async def test_private_parent_declared_public_child_prepares_only_public_bytes(
        self,
    ):
        grant = await self.grant()
        parent, private = await self.bound(capability="private.read", grant=grant)
        handler = (
            self.runtime.registry.snapshot()
            .module("sample/beta")
            .handlers.capabilities["read"]
        )
        original = handler.invoke
        observed = {}

        async def child_handler(child, parameters):
            child_bound = await self.runtime.services.for_module(
                "sample/beta"
            ).scopes.bind(child)
            observed["grant_id"] = child.grant_id
            observed["ref"] = await child_bound.resources.store(
                "image/png", png(), visibility=CacheVisibility.PUBLIC
            )
            return await original(child, parameters)

        with patch.object(handler, "invoke", child_handler):
            result = await private.dependencies.invoke(
                parent, CapabilityReference("sample/beta", "read"), {}
            )
        self.assertIsNone(result.error)
        self.assertIsNone(observed["grant_id"])
        self.assertEqual(await private.resources.read(observed["ref"].asset_id), png())

    async def test_invalid_images_size_ttl_and_stale_invocation(self):
        view, bound = await self.bound()
        for media, body, ttl in (
            ("image/png", b"<html>login</html>", None),
            ("image/jpeg", png(), None),
            ("image/png", png(), 31 * 86400),
            ("image/png", b"x" * (4 * 1024 * 1024 + 1), None),
        ):
            with self.assertRaises(ParameterError):
                await bound.resources.store(
                    media, body, visibility=CacheVisibility.PUBLIC, ttl_seconds=ttl
                )
        self.assertEqual(
            (await bound.resources.stats(visibility=CacheVisibility.PUBLIC)).items, 0
        )
        self.runtime.issuer.release(view)
        self.runtime.lifecycle.admission.release(self.views[-1][1])
        self.views.pop()
        with self.assertRaises(InvalidInvocation):
            await bound.resources.store(
                "image/png", png(), visibility=CacheVisibility.PUBLIC
            )
