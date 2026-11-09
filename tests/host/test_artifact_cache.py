"""Real temporary-filesystem checks for the durable verified artifact cache."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.bootstrap.artifact_cache import ArtifactCache
from ygl_test_subject.extensions.hot_update_protocol import payload_digest
from ygl_test_subject.extensions.runtime_bundle import (
    RuntimeBundleError,
)


async def trusted(raw, signature, bundle):
    return {"digest": bundle.digest}


class CacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.cache = self.new_cache(self.data)
        self.leases = []
        self.addCleanup(lambda: [lease.release() for lease in self.leases])

    def new_cache(self, *args, **kwargs):
        cache = ArtifactCache(*args, **kwargs)

        def close():
            for lease in getattr(self, "leases", ()):
                lease.release()
            cache.close()

        self.addCleanup(close)
        return cache

    def contents(self):
        return [
            p for p in self.cache.root.iterdir() if p.name != ".ygl-runtime-owner.lock"
        ]

    def install(self, sequence=1, package="example", signature=b"synthetic signature"):
        root = self.root / f"install-{package}-{sequence}"
        root.mkdir()
        content = f"VALUE = {sequence}\n".encode()
        (root / "main.py").write_bytes(content)
        files = [
            {
                "path": "main.py",
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        ]
        release = {
            "schema": "ygl.release/1",
            "plugin_id": {"author": "A", "name": "N"},
            "package_id": package,
            "release": f"1.0.{sequence}",
            "channel": "preview",
            "release_sequence": sequence,
            "payload_digest": payload_digest(files),
            "files": files,
        }
        (root / "release.json").write_bytes(json.dumps(release).encode())
        if signature is not None:
            (root / "release.sig.json").write_bytes(signature)
        return root

    async def capture(self, root, cache=None, validate=trusted):
        result = await (cache or self.cache).capture(
            root, kind="dlc", validate=validate
        )
        self.leases.append(result[0])
        return result[2]

    async def test_default_store_uses_sibling_lease_root_after_reopen(self):
        parent = self.data / "runtime-leases-v1"
        first = await self.capture(self.install())
        lease = self.leases[-1]
        self.assertEqual(lease.resource_root.parent, parent)
        self.assertNotIn(parent, self.contents())
        self.cache.publish("example", first)
        other = self.new_cache(self.data)
        self.assertIs(other.store.budget_state, self.cache.store.budget_state)
        second = await self.capture(self.install(2), other)
        other_lease = self.leases[-1]
        self.assertEqual(other_lease.resource_root.parent, parent)
        self.assertEqual(self.cache.gc(), ())
        other.publish("example", second)
        self.assertEqual(lease.release(), "released")
        self.assertEqual(other_lease.release(), "released")
        self.assertTrue(parent.is_dir())
        self.assertEqual(list(parent.iterdir()), [])

    async def test_explicit_store_keeps_its_selected_materialization_parent(self):
        store = type(self.cache.store)(materialization_parent=self.root / "chosen")
        data = self.root / "other-data"
        cache = self.new_cache(data, store=store)
        self.assertIs(cache.store, store)
        await self.capture(self.install(), cache)
        lease = self.leases[-1]
        self.assertEqual(lease.resource_root.parent, self.root / "chosen")
        self.assertFalse((data / "runtime-leases-v1").exists())
        self.assertEqual(lease.release(), "released")

    async def test_restart_current_previous_gc_and_explicit_retention(self):
        first = await self.capture(self.install(1))
        self.cache.publish("example", first)
        second = await self.capture(self.install(2))
        self.cache.publish("example", second)
        third = await self.capture(self.install(3))
        self.cache.discard(third)
        for lease in self.leases:
            lease.release()
        self.cache.close()
        restarted = self.new_cache(self.data)
        self.assertEqual(restarted.current, {"example": second})
        self.assertEqual(restarted.last_good, {"example": first})
        self.assertEqual(restarted.gc(retained=(third,)), ())
        self.assertEqual(restarted.gc(), (third,))
        self.assertTrue((restarted.root / first).exists())
        self.assertTrue((restarted.root / second).exists())
        result = await restarted.load(first, kind="dlc", validate=trusted)
        self.leases.append(result[0])
        self.assertEqual(result[0].read_resource("main.py"), b"VALUE = 1\n")

    async def test_load_revalidates_authority_and_returns_immutable_copy(self):
        key = await self.capture(self.install())
        calls = []

        async def revoked(*args):
            calls.append(args)
            return False

        with self.assertRaisesRegex(RuntimeBundleError, "artifact_untrusted"):
            await self.cache.load(key, kind="dlc", validate=revoked)
        self.assertEqual(len(calls), 1)
        lease, _, _ = await self.cache.load(key, kind="dlc", validate=trusted)
        self.leases.append(lease)
        (self.cache.root / key / "main.py").write_bytes(b"tampered")
        self.assertEqual(lease.read_resource("main.py"), b"VALUE = 1\n")
        with self.assertRaises(RuntimeBundleError):
            await self.cache.load(key, kind="dlc", validate=trusted)

    async def test_unsigned_rejection_never_writes(self):
        async def signed_only(raw, signature, bundle):
            return signature is not None

        with self.assertRaisesRegex(RuntimeBundleError, "artifact_untrusted"):
            await self.capture(self.install(signature=None), validate=signed_only)
        self.assertEqual(self.contents(), [])
        self.assertEqual(self.cache.store.reserved_bytes("dlc"), 0)

    async def test_raw_release_signature_and_inventory_tampering(self):
        for part in ("release.json", "release.sig.json", "main.py", "extra.py"):
            with self.subTest(part=part):
                key = await self.capture(self.install(len(self.leases) + 1))
                target = self.cache.root / key
                (target / part).write_bytes(b"corrupt")
                with self.assertRaises((ValueError, OSError)):
                    await self.cache.load(
                        key,
                        kind="dlc",
                        validate=(lambda raw, sig, bundle: self.require_signature(sig)),
                    )
        key = await self.capture(self.install(20))
        (self.cache.root / key / "main.py").unlink()
        with self.assertRaises(RuntimeBundleError):
            await self.cache.load(key, kind="dlc", validate=trusted)

    async def require_signature(self, signature):
        return signature == b"synthetic signature"

    async def test_existing_entry_does_not_capture_second_runtime_lease(self):
        root = self.install()
        key = await self.capture(root)
        with patch.object(
            self.cache.store, "capture", wraps=self.cache.store.capture
        ) as capture:
            self.assertEqual(await self.capture(root), key)
            self.assertEqual(capture.call_count, 1)
        (self.cache.root / key / "extra.py").write_text("evil")
        with self.assertRaisesRegex(RuntimeBundleError, "cache_inventory_changed"):
            await self.capture(root)

    async def test_candidate_budget_keeps_active_and_candidates(self):
        cache = self.new_cache(self.data, max_entries=2)
        first = await self.capture(self.install(1), cache)
        cache.publish("example", first)
        second = await self.capture(self.install(2), cache)
        with self.assertRaisesRegex(RuntimeBundleError, "cache_.*budget_exceeded"):
            await self.capture(self.install(3), cache)
        self.assertTrue((cache.root / first).exists())
        self.assertTrue((cache.root / second).exists())
        cache.discard(second)
        third = await self.capture(self.root / "install-example-3", cache)
        self.assertFalse((cache.root / second).exists())
        self.assertTrue((cache.root / third).exists())
        cache.max_disk_bytes = sum(
            p.stat().st_size for p in (cache.root / first).iterdir()
        )
        with self.assertRaisesRegex(RuntimeBundleError, "cache_disk_budget_exceeded"):
            await self.capture(self.install(4), cache)
        self.assertEqual(cache.current["example"], first)

    async def test_failed_stage_cleanup_releases_candidate(self):
        with patch(
            "ygl_test_subject.bootstrap.artifact_cache._write",
            side_effect=OSError("disk full"),
        ):
            with self.assertRaises(OSError):
                await self.capture(self.install())
        self.assertEqual(self.contents(), [])
        self.assertEqual(self.cache.store.reserved_bytes("dlc"), 0)
        self.assertEqual(self.cache.candidates, set())

    async def test_corrupt_or_wrong_identity_pins_fail_closed(self):
        key = await self.capture(self.install())
        pin = self.cache.root / "pins.json"
        cases = [
            b"{",
            b'{"current":{},"current":{},"last_good":{}}',
            json.dumps({"current": {"other": key}, "last_good": {}}).encode(),
            json.dumps({"current": {"example": "a" * 64}, "last_good": {}}).encode(),
        ]
        for content in cases:
            pin.write_bytes(content)
            with self.assertRaisesRegex(RuntimeBundleError, "cache_pins_invalid"):
                self.new_cache(self.data)

    async def test_pin_count_bound_and_publish_failure_preserves_memory(self):
        cache = self.new_cache(self.data, max_pin_entries=1)
        first = await self.capture(self.install(), cache)
        cache.publish("example", first)
        second = await self.capture(self.install(package="another"), cache)
        with self.assertRaises(ValueError):
            cache.publish("another", second)
        self.assertEqual(cache.current, {"example": first})
        with patch(
            "ygl_test_subject.bootstrap.artifact_cache.os.replace",
            side_effect=OSError("disk"),
        ):
            with self.assertRaises(OSError):
                cache.publish("example", first)
        self.assertEqual(cache.current, {"example": first})
        self.assertEqual(self.new_cache(self.data).current, cache.current)

    async def test_links_and_metadata_bounds_rejected(self):
        for part in ("release.json", "release.sig.json"):
            root = self.install(len(self.leases) + (1 if part == "release.json" else 2))
            source = root / part
            outside = root.parent / f"outside-{part}"
            source.rename(outside)
            source.symlink_to(outside)
            with self.assertRaisesRegex(RuntimeBundleError, "cache_path_rejected"):
                await self.capture(root)
        root = self.install(10)
        (root / "release.sig.json").write_bytes(b"x" * (65536 + 1))
        with self.assertRaisesRegex(
            RuntimeBundleError, "cache_metadata_budget_exceeded"
        ):
            await self.capture(root)
        root = self.install(11)
        key = await self.capture(root)
        outside = self.root / "business-data"
        outside.write_text("KEEP")
        (self.cache.root / key / "extra.py").symlink_to(outside)
        self.cache.discard(key)
        with self.assertRaises(RuntimeBundleError):
            self.cache.gc()
        self.assertEqual(outside.read_text(), "KEEP")

    async def test_pin_fsync_failure_keeps_visible_state_consistent(self):
        first = await self.capture(self.install())
        self.cache.publish("example", first)
        second = await self.capture(self.install(2))
        with patch(
            "ygl_test_subject.bootstrap.artifact_cache._sync_dir",
            side_effect=OSError("fsync"),
        ):
            with self.assertRaises(OSError):
                self.cache.publish("example", second)
        self.assertEqual(self.cache.current, {"example": second})
        self.assertEqual(self.new_cache(self.data).current, self.cache.current)
        self.cache.gc()
        self.assertTrue((self.cache.root / second).exists())

    async def test_symlink_cache_root_rejected_before_resolve(self):
        outside = self.root / "outside"
        outside.mkdir()
        alias = self.root / "alias"
        alias.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeBundleError, "cache_path_rejected"):
            self.new_cache(alias)

    async def test_cross_class_instances_merge_pins_and_preserve_other_holds(self):
        spec = importlib.util.spec_from_file_location(
            "ygl_test_subject.bootstrap._cache_reload_test",
            sys.modules[ArtifactCache.__module__].__file__,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        bundle_spec = importlib.util.spec_from_file_location(
            "ygl_test_subject.extensions._bundle_reload_test",
            sys.modules[self.cache.store.__class__.__module__].__file__,
        )
        bundle_module = importlib.util.module_from_spec(bundle_spec)
        bundle_spec.loader.exec_module(bundle_module)
        module.RuntimeBundleStore = bundle_module.RuntimeBundleStore
        other = module.ArtifactCache(self.data)
        self.addCleanup(other.close)
        self.assertIsNot(type(other), ArtifactCache)
        self.assertIsNot(type(other.store), type(self.cache.store))
        self.assertIsNot(other.store, self.cache.store)
        self.assertIs(other.store.budget_state, self.cache.store.budget_state)
        first = await self.capture(self.install())
        self.cache.publish("example", first)
        with patch.object(
            self.cache.store,
            "capture",
            side_effect=AssertionError("old capture called"),
        ):
            with patch.object(
                other.store, "capture", wraps=other.store.capture
            ) as new_capture:
                second = await self.capture(self.install(package="another"), other)
                self.assertEqual(new_capture.call_count, 1)
        other.publish("another", second)
        third = await self.capture(self.install(3))
        self.cache.publish("example", third)
        self.assertEqual(other.current, {"example": third, "another": second})
        self.assertEqual(other.last_good, {"example": first})
        candidate_root = self.install(4)
        held = await self.capture(candidate_root)
        self.assertEqual(await self.capture(candidate_root, other), held)
        self.cache.discard(held)
        self.assertEqual(self.cache.gc(), ())
        other.discard(held)
        self.assertEqual(self.cache.gc(), (held,))

    async def test_pending_validation_holds_existing_candidate_for_other_gc(self):
        root = self.install()
        key = await self.capture(root)
        self.cache.discard(key)
        other = self.new_cache(self.data)
        entered, finish = asyncio.Event(), asyncio.Event()

        async def delayed(*args):
            entered.set()
            await finish.wait()
            return True

        task = asyncio.create_task(self.capture(root, other, delayed))
        await entered.wait()
        self.assertEqual(self.cache.gc(), ())
        finish.set()
        self.assertEqual(await task, key)
        other.close()
        self.assertEqual(self.cache.gc(), (key,))

    async def test_cancelled_validation_releases_only_its_hold_and_budget(self):
        root = self.install()
        key = await self.capture(root)
        entered = asyncio.Event()

        async def delayed(*args):
            entered.set()
            await asyncio.Future()

        other = self.new_cache(self.data)
        before = other.store.reserved_bytes("dlc")
        task = asyncio.create_task(other.capture(root, kind="dlc", validate=delayed))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(other.store.reserved_bytes("dlc"), before)
        self.assertEqual(self.cache.candidates, {key})
        self.cache.discard(key)
        self.assertEqual(self.cache.gc(), (key,))

    async def test_pending_unique_candidates_bounded_before_capture(self):
        self.cache.close()
        cache = self.new_cache(self.data, max_entries=1)
        entered, finish = asyncio.Event(), asyncio.Event()

        async def delayed(*args):
            entered.set()
            await finish.wait()
            return True

        task = asyncio.create_task(self.capture(self.install(), cache, delayed))
        await entered.wait()
        before = cache.store.reserved_bytes("dlc")
        with self.assertRaisesRegex(
            RuntimeBundleError, "cache_candidate_budget_exceeded"
        ):
            await self.capture(self.install(2), cache)
        self.assertEqual(cache.store.reserved_bytes("dlc"), before)
        finish.set()
        await task

    async def test_explicit_store_must_share_exact_budget_ledger(self):
        store_class = type(self.cache.store)
        with self.assertRaisesRegex(RuntimeBundleError, "cache_store_conflict"):
            self.new_cache(self.data, store=store_class())
        supplied = store_class(shared_budget=self.cache.store.budget_state)
        other = self.new_cache(self.data, store=supplied)
        self.assertIs(other.store, supplied)
        key = await self.capture(self.install(), other)
        self.assertGreater(self.cache.store.reserved_bytes("dlc"), 0)
        self.assertEqual(self.cache.candidates, {key})

    async def test_final_close_retains_registry_until_last_lease_released(self):
        key = await self.capture(self.install())
        with self.assertRaisesRegex(RuntimeBundleError, "cache_bundles_retained"):
            self.cache.close()
        scope = str(self.cache.root)
        self.assertIn(scope, self.cache.kernel.artifact_cache_states)
        other = self.new_cache(self.data)
        self.assertIsNot(other.store, self.cache.store)
        self.assertIs(other.store.budget_state, self.cache.store.budget_state)
        self.assertEqual(other.candidates, {key})
        other.close()
        self.leases[-1].release()
        self.cache.close()
        self.assertNotIn(scope, self.cache.kernel.artifact_cache_states)
        reopened = self.new_cache(self.data)
        self.assertEqual(reopened.store.reserved_bytes("dlc"), 0)
        self.assertIsNot(reopened.store, self.cache.store)

    async def test_process_lock_rejects_overlap_and_final_close_releases(self):
        script = (
            "import sys, tests; "
            "from ygl_test_subject.bootstrap.artifact_cache import ArtifactCache; "
            "c=ArtifactCache(sys.argv[1]); c.close()"
        )

        def child():
            return subprocess.run(
                [sys.executable, "-c", script, str(self.data)],
                capture_output=True,
                text=True,
                check=False,
            )

        blocked = child()
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("data_dir_busy", blocked.stderr)
        other = self.new_cache(self.data)
        self.cache.close()
        self.assertNotEqual(child().returncode, 0)
        other.close()
        allowed = child()
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertNotIn(str(self.cache.root), self.cache.kernel.artifact_cache_states)
        with self.assertRaisesRegex(RuntimeBundleError, "cache_closed"):
            self.cache.gc()

    async def test_disk_accounting_includes_partial_stage_and_metadata(self):
        cache = self.new_cache(self.data, max_disk_bytes=1024)
        stage = cache.root / ".candidate-interrupted"
        stage.mkdir()
        (stage / "partial").write_bytes(b"x" * 1024)
        with self.assertRaisesRegex(RuntimeBundleError, "cache_disk_budget_exceeded"):
            await self.capture(self.install(), cache)
        self.assertEqual(cache.store.reserved_bytes("dlc"), 0)


if __name__ == "__main__":
    unittest.main()
