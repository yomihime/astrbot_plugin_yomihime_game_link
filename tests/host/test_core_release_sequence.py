"""Signed real-cache/Core-owner admission; offline native filesystem evidence."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from ygl_test_subject.bootstrap.loader import (
    ArtifactCache,
    CoreGeneration,
    validate_core_release,
)
from ygl_test_subject.extensions.hot_update_protocol import payload_digest
from ygl_test_subject.extensions.runtime_bundle import RuntimeBundleError


def sha(data):
    return hashlib.sha256(data).hexdigest()


async def validate(raw, signature, bundle):
    return validate_core_release(raw, signature, bundle)


class CoreReleaseSequenceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ygl-core-sequence-")
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.key = Ed25519PrivateKey.generate()  # Private key stays in memory.
        self.context = object()
        self.caches, self.bundles, self.generations = [], [], []
        self.serial = 0
        self.cache = self.new_cache()

    async def asyncTearDown(self):
        for generation in reversed(self.generations):
            await generation.terminate()
        for bundle in self.bundles:
            bundle.release()
        for cache in self.caches:
            cache.close()
        self.temp.cleanup()

    def new_cache(self, **kwargs):
        cache = ArtifactCache(self.data, **kwargs)
        self.caches.append(cache)
        return cache

    def install(
        self, sequence, tag="first", *, key=None, channel="preview", migration=b""
    ):
        self.serial += 1
        root = self.root / f"install-{self.serial}"
        root.mkdir()
        private = key or self.key
        public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        key_id = sha(public)
        files = {
            "main.py": f"# {tag}\n".encode(),
            "metadata.yaml": b"name: astrbot_plugin_yomihime_game_link\nauthor: yomihime\nversion: 0.2.0\n",
            "requirements.txt": b"",
            "yomihime_game_link_sdk/__init__.py": b"",
            "ygl-plugin.json": json.dumps(
                {
                    "schema": "ygl.plugin/1",
                    "role": "core",
                    "package_id": "ygl_core",
                    "manifest": "release.json",
                    "control_protocol": {"name": "ygl.control", "major": 1},
                }
            ).encode(),
            "release-key.json": json.dumps(
                {
                    "algorithm": "ed25519",
                    "key_id": key_id,
                    "public_key_base64": base64.b64encode(public).decode(),
                }
            ).encode(),
        }
        if migration:
            files["infrastructure/sqlite/migrations/0010_example.sql"] = migration
        inventory = [
            {"path": p, "size": len(b), "sha256": sha(b)} for p, b in files.items()
        ]
        doc = {
            "schema": "ygl.release/1",
            "plugin_id": {
                "author": "yomihime",
                "name": "astrbot_plugin_yomihime_game_link",
            },
            "package_id": "ygl_core",
            "release": f"0.2.{sequence}",
            "channel": channel,
            "release_sequence": sequence,
            "files": inventory,
            "payload_digest": payload_digest(inventory),
        }
        raw = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
        signature = json.dumps(
            {
                "algorithm": "ed25519",
                "key_id": key_id,
                "signature_base64": base64.b64encode(private.sign(raw)).decode(),
            }
        ).encode()
        for name, content in {
            **files,
            "release.json": raw,
            "release.sig.json": signature,
        }.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return root

    async def capture(self, installation, cache=None, callback=validate):
        bundle, _, key = await (cache or self.cache).capture(
            installation, kind="core", validate=callback
        )
        self.bundles.append(bundle)
        return bundle, key

    async def owner(self, bundle, cache=None, publish=True):
        cache = cache or self.cache
        generation = CoreGeneration(object(), self.context, self.data, bundle)
        generation.cache = cache
        self.generations.append(generation)
        self.assertEqual(
            (await generation.acquire_admitted(cache))["state"], "acquired"
        )
        if publish:
            cache.publish("ygl_core", cache.check_core_admission(bundle))
        return generation

    async def test_same_stream_collision_downgrade_idempotence_and_forward(self):
        installation = self.install(2)
        bundle, key = await self.capture(installation)
        generation = await self.owner(bundle)
        self.assertEqual((await generation.terminate())["state"], "quiet")
        self.cache = self.new_cache()
        same, same_key = await self.capture(installation)
        self.assertEqual(same_key, key)
        for install in (self.install(2, "conflict"), self.install(1, "downgrade")):
            with self.assertRaisesRegex(
                RuntimeBundleError, "core_release_sequence_rejected"
            ):
                await self.capture(install)
        same.release()
        forward, forward_key = await self.capture(self.install(3, "forward"))
        next_owner = await self.owner(forward)
        self.assertEqual(self.cache.current["ygl_core"], forward_key)
        self.assertEqual(
            next(iter(self.cache.release_heads.values())),
            {"release_sequence": 3, "digest": forward_key},
        )
        self.assertEqual((await next_owner.terminate())["state"], "quiet")

    async def test_concurrent_validation_rechecks_latest_head(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(raw, signature, bundle):
            entered.set()
            await release.wait()
            return await validate(raw, signature, bundle)

        lower = asyncio.create_task(self.capture(self.install(2), callback=delayed))
        await entered.wait()
        higher, key = await self.capture(self.install(3))
        release.set()
        with self.assertRaisesRegex(
            RuntimeBundleError, "core_release_sequence_rejected"
        ):
            await lower
        self.assertEqual(next(iter(self.cache.release_heads.values()))["digest"], key)
        await self.owner(higher)

    async def test_shared_cache_stale_candidate_cannot_acquire_or_publish(self):
        lower, lower_key = await self.capture(self.install(2))
        other = self.new_cache()
        await self.capture(self.install(3), other)
        generation = CoreGeneration(object(), self.context, self.data, lower)
        with self.assertRaisesRegex(
            RuntimeBundleError, "core_release_sequence_rejected"
        ):
            await generation.acquire_admitted(self.cache)
        self.assertIsNone(generation.receipt)
        with self.assertRaisesRegex(
            RuntimeBundleError, "core_release_sequence_rejected"
        ):
            self.cache.publish("ygl_core", lower_key)

    async def test_internal_exact_last_good_only_and_head_stays_high(self):
        old_install = self.install(1)
        old, old_key = await self.capture(old_install)
        self.cache.publish("ygl_core", old_key)
        current, _ = await self.capture(self.install(2))
        recovery = self.cache.core_recovery_plan(current)
        high = self.cache.release_heads
        with self.assertRaisesRegex(
            RuntimeBundleError, "core_release_sequence_rejected"
        ):
            await self.capture(old_install)
        with self.assertRaisesRegex(
            RuntimeBundleError, "core_release_sequence_rejected"
        ):
            await self.cache.load(old_key, kind="core", validate=validate)
        fallback, _, _ = await self.cache.load_core_fallback(
            old_key, recovery=recovery, validate=validate
        )
        self.bundles.append(fallback)
        self.cache.check_core_admission(fallback, fallback=True)
        generation = await self.owner(fallback, publish=False)
        with self.assertRaisesRegex(RuntimeBundleError, "core_admission_missing"):
            self.new_cache().publish("ygl_core", old_key)
        self.assertEqual(self.cache.release_heads, high)
        self.assertEqual((await generation.terminate())["state"], "quiet")

    async def test_fallback_current_key_channel_and_migrations_must_match(self):
        old, old_key = await self.capture(self.install(1))
        self.cache.publish("ygl_core", old_key)
        candidates = (
            self.install(2, key=Ed25519PrivateKey.generate()),
            self.install(2, channel="stable"),
            self.install(2, migration=b"CREATE TABLE added(id INTEGER);"),
        )
        for candidate in candidates:
            current, _ = await self.capture(candidate)
            recovery = self.cache.core_recovery_plan(current)
            with self.assertRaisesRegex(RuntimeBundleError, "core_fallback_rejected"):
                await self.cache.load_core_fallback(
                    old_key, recovery=recovery, validate=validate
                )

    async def test_head_budget_never_evicts_an_existing_stream(self):
        self.cache.close()
        self.cache = self.new_cache(max_pin_entries=1)
        await self.capture(self.install(2))
        original = self.cache.release_heads
        with self.assertRaisesRegex(ValueError, "release head limit"):
            await self.capture(self.install(1, channel="stable"))
        self.assertEqual(self.cache.release_heads, original)

        self.cache.check_core_admission(self.bundles[0])

    async def test_corrupt_missing_and_legacy_history_fail_closed(self):
        bundle, key = await self.capture(self.install(2))
        self.cache.publish("ygl_core", key)
        path = self.cache.root / "pins.json"
        original = path.read_bytes()
        for heads in ([], {"bad-stream": {"release_sequence": 1, "digest": key}}, {}):
            doc = json.loads(original)
            doc["release_heads"] = heads
            path.write_text(json.dumps(doc))
            with self.assertRaises(RuntimeBundleError):
                self.new_cache()
            with self.assertRaises(RuntimeBundleError):
                self.cache.check_core_admission(bundle)
        doc = json.loads(original)
        del doc["release_heads"]
        path.write_text(json.dumps(doc))
        with self.assertRaisesRegex(
            RuntimeBundleError, "cache_core_head_migration_required"
        ):
            self.new_cache()
        path.unlink()
        with self.assertRaisesRegex(RuntimeBundleError, "cache_pins_missing"):
            self.new_cache()
        path.write_bytes(original)

    async def test_unrelated_corrupt_dlc_orphan_isolated_but_its_load_rejected(self):
        await self.capture(self.install(2))
        root = self.root / "dlc-orphan"
        root.mkdir()
        content = b"VALUE = 1\n"
        inventory = [{"path": "main.py", "size": len(content), "sha256": sha(content)}]
        doc = {
            "schema": "ygl.release/1",
            "plugin_id": {"author": "A", "name": "N"},
            "package_id": "other_dlc",
            "release": "1.0.0",
            "channel": "preview",
            "release_sequence": 1,
            "files": inventory,
            "payload_digest": payload_digest(inventory),
        }
        (root / "main.py").write_bytes(content)
        (root / "release.json").write_text(json.dumps(doc))

        async def fixture_authority(*args):
            return True

        orphan, _, key = await self.cache.capture(
            root, kind="dlc", validate=fixture_authority
        )
        self.bundles.append(orphan)
        (self.cache.root / key / "release.json").write_bytes(b"broken")
        forward, _ = await self.capture(self.install(3))
        self.cache.check_core_admission(forward)
        self.assertEqual(
            next(iter(self.cache.release_heads.values()))["release_sequence"], 3
        )
        with self.assertRaises(ValueError):
            await self.cache.load(key, kind="dlc", validate=fixture_authority)

    async def test_candidate_bytes_gc_does_not_erase_persisted_watermark(self):
        bundle, key = await self.capture(self.install(2))
        history = self.cache.release_heads
        bundle.release()
        self.cache.discard(key)
        self.assertEqual(self.cache.gc(), (key,))
        self.cache.close()
        self.cache = self.new_cache()
        self.assertEqual(self.cache.release_heads, history)
        with self.assertRaisesRegex(
            RuntimeBundleError, "core_release_sequence_rejected"
        ):
            await self.capture(self.install(1))

    async def test_atomic_head_write_failure_preserves_published_owner_and_recovery(
        self,
    ):
        old, old_key = await self.capture(self.install(2))
        self.cache.publish("ygl_core", old_key)
        recovery = self.cache.core_recovery_plan(old)
        original = self.cache.release_heads
        pin_bytes = (self.cache.root / "pins.json").read_bytes()
        candidate = self.install(3)
        real_replace = __import__("os").replace

        def fail_pins(source, target):
            if Path(target).name == "pins.json":
                raise OSError("injected metadata replace failure")
            return real_replace(source, target)

        with patch(
            "ygl_test_subject.bootstrap.artifact_cache.os.replace",
            side_effect=fail_pins,
        ):
            with self.assertRaisesRegex(OSError, "metadata replace failure"):
                await self.capture(candidate)
        self.assertEqual(self.cache.release_heads, original)
        self.assertEqual((self.cache.root / "pins.json").read_bytes(), pin_bytes)
        self.assertEqual(self.cache.current, {"ygl_core": old_key})
        self.assertEqual(self.cache.check_core_admission(old), old_key)
        fallback, _, _ = await self.cache.load_core_fallback(
            old_key, recovery=recovery, validate=validate
        )
        self.bundles.append(fallback)
        self.cache.check_core_admission(fallback, fallback=True)

        # The orphan is preserved, but its bytes supply no execution admission.
        raw = (candidate / "release.json").read_bytes()
        orphan_key = sha(raw)
        self.assertTrue((self.cache.root / orphan_key).is_dir())
        unadmitted = self.cache.store.capture(candidate, json.loads(raw), kind="core")
        self.bundles.append(unadmitted)
        instance = self.shell()
        with patch.object(unadmitted, "mount_core") as mount:
            with self.assertRaisesRegex(RuntimeBundleError, "core_admission_missing"):
                await instance._run_candidate(
                    self.cache, unadmitted, orphan_key, fallback=False
                )
            mount.assert_not_called()
        unadmitted.release()
        fallback.release()
        old.release()
        self.cache.close()

        script = """import asyncio,sys,tests
from ygl_test_subject.bootstrap.loader import ArtifactCache,validate_core_release
async def run():
    cache=ArtifactCache(sys.argv[1])
    async def validate(raw,sig,bundle): return validate_core_release(raw,sig,bundle)
    old,_,_=await cache.load(sys.argv[2],kind='core',validate=validate)
    cache.check_core_admission(old,fallback=False)
    plan=cache.core_recovery_plan(old)
    fallback,_,_=await cache.load_core_fallback(sys.argv[2],recovery=plan,validate=validate)
    cache.check_core_admission(fallback,fallback=True)
    fallback.release()
    old.release()
    cache.close()
    print('old_admission_and_fallback_passed')
asyncio.run(run())
"""
        restarted_process = subprocess.run(
            [sys.executable, "-c", script, str(self.data), old_key],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(restarted_process.returncode, 0, restarted_process.stderr)
        self.assertEqual(
            restarted_process.stdout.strip(), "old_admission_and_fallback_passed"
        )

        # Genuine process-state reconstruction uses the same durable old ledger.
        self.cache = self.new_cache()
        self.assertEqual(self.cache.release_heads, original)
        restored, _, _ = await self.cache.load(old_key, kind="core", validate=validate)
        self.bundles.append(restored)
        self.cache.check_core_admission(restored, fallback=False)
        restored_fallback, _, _ = await self.cache.load_core_fallback(
            old_key, recovery=recovery, validate=validate
        )
        self.bundles.append(restored_fallback)
        self.cache.check_core_admission(restored_fallback, fallback=True)
        self.assertTrue((self.cache.root / orphan_key).is_dir())
        self.assertEqual((self.cache.root / "pins.json").read_bytes(), pin_bytes)

        # Retrying through ordinary verification can now commit the candidate.
        retried, _ = await self.capture(candidate)
        self.cache.check_core_admission(retried, fallback=False)
        self.assertEqual(
            next(iter(self.cache.release_heads.values()))["release_sequence"], 3
        )

    async def test_first_ledger_write_failure_leaves_no_core_artifact(self):
        installation = self.install(2)
        key = sha((installation / "release.json").read_bytes())
        with patch(
            "ygl_test_subject.bootstrap.artifact_cache.os.replace",
            side_effect=OSError("injected initial ledger failure"),
        ):
            with self.assertRaisesRegex(OSError, "initial ledger failure"):
                await self.capture(installation)
        self.assertFalse((self.cache.root / key).exists())
        self.assertFalse((self.cache.root / "pins.json").exists())
        self.cache.close()
        self.cache = self.new_cache()
        admitted, _ = await self.capture(installation)
        self.cache.check_core_admission(admitted)

    async def test_first_core_head_write_failure_retries_without_cleanup(self):
        installation = self.install(2)
        key = sha((installation / "release.json").read_bytes())
        real_replace = __import__("os").replace

        def fail_nonempty_head(source, target):
            if Path(target).name == "pins.json":
                doc = json.loads(Path(source).read_bytes())
                if doc["release_heads"]:
                    raise OSError("injected first head failure")
            return real_replace(source, target)

        with patch(
            "ygl_test_subject.bootstrap.artifact_cache.os.replace",
            side_effect=fail_nonempty_head,
        ):
            with self.assertRaisesRegex(OSError, "first head failure"):
                await self.capture(installation)
        self.assertTrue((self.cache.root / key).is_dir())
        metadata = json.loads((self.cache.root / "pins.json").read_bytes())
        self.assertEqual(
            metadata, {"current": {}, "last_good": {}, "release_heads": {}}
        )
        self.cache.close()
        self.cache = self.new_cache()
        admitted, _ = await self.capture(installation)
        self.cache.check_core_admission(admitted)
        self.assertTrue((self.cache.root / key).is_dir())

    async def test_cross_process_persisted_head_and_exclusive_writer(self):
        bundle, key = await self.capture(self.install(2))
        self.cache.publish("ygl_core", key)
        lower = self.install(1)
        script = """import asyncio,sys,tests
from ygl_test_subject.bootstrap.loader import ArtifactCache,validate_core_release
async def run():
    cache=None
    try:
        cache=ArtifactCache(sys.argv[1])
        async def validate(raw,sig,bundle): return validate_core_release(raw,sig,bundle)
        await cache.capture(sys.argv[2],kind='core',validate=validate)
        print('UNEXPECTED_ADMISSION')
    except Exception as exc: print(getattr(exc,'code',type(exc).__name__))
    finally:
        if cache is not None: cache.close()
asyncio.run(run())
"""
        locked = subprocess.run(
            [sys.executable, "-c", script, str(self.data), str(lower)],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(locked.returncode, 0, locked.stderr)
        self.assertEqual(locked.stdout.strip(), "data_dir_busy")
        bundle.release()
        self.cache.close()
        restarted = subprocess.run(
            [sys.executable, "-c", script, str(self.data), str(lower)],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(restarted.returncode, 0, restarted.stderr)
        self.assertIn("core_release_sequence_rejected", restarted.stdout)
        self.assertNotIn("UNEXPECTED_ADMISSION", restarted.stdout)

    def shell(self):
        class Filters:
            def __getattr__(self, name):
                return lambda *args, **kwargs: lambda callback: callback

        star = types.ModuleType("astrbot.api.star")
        star.Star = type("Star", (), {})
        star.register = lambda *args, **kwargs: lambda cls: cls
        event = types.ModuleType("astrbot.api.event")
        event.AstrMessageEvent, event.filter = object, Filters()
        with patch.dict(
            sys.modules, {"astrbot.api.star": star, "astrbot.api.event": event}
        ):
            sys.modules.pop("ygl_test_subject.main", None)
            module = importlib.import_module("ygl_test_subject.main")
        self.addCleanup(sys.modules.pop, "ygl_test_subject.main", None)
        instance = module.YomihimeGameLink.__new__(module.YomihimeGameLink)
        instance._host_context, instance._data_root = self.context, self.data
        instance._stopping, instance._host_config = False, None
        instance._generation = instance._runtime = None
        instance._pages = instance._admin_pages = None
        return instance

    async def test_actual_thin_shell_rejects_missing_and_stale_before_mount(self):
        installation = self.install(2)
        raw = (installation / "release.json").read_bytes()
        unadmitted = self.cache.store.capture(
            installation, json.loads(raw), kind="core"
        )
        self.bundles.append(unadmitted)
        instance = self.shell()
        with patch.object(unadmitted, "mount_core") as mount:
            with self.assertRaisesRegex(RuntimeBundleError, "core_admission_missing"):
                await instance._run_candidate(
                    self.cache, unadmitted, sha(raw), fallback=False
                )
            mount.assert_not_called()
        stale, key = await self.capture(installation)
        await self.capture(self.install(3))
        with patch.object(stale, "mount_core") as mount:
            with self.assertRaisesRegex(
                RuntimeBundleError, "core_release_sequence_rejected"
            ):
                await instance._run_candidate(self.cache, stale, key, fallback=False)
            mount.assert_not_called()

    async def test_actual_native_entry_rejects_external_downgrade_and_collision(self):
        await self.capture(self.install(2))
        for installation in (self.install(1), self.install(2, "collision")):
            instance = self.shell()
            instance._plugin_root = installation
            with patch("ygl_test_subject.main.CoreGeneration") as generation:
                with self.assertRaisesRegex(
                    RuntimeBundleError, "core_release_sequence_rejected"
                ):
                    await instance._start_generation()
                generation.assert_not_called()
            self.assertEqual(instance._state, "failed")
        self.assertEqual(
            next(iter(self.cache.release_heads.values()))["release_sequence"], 2
        )

    async def test_actual_shell_compatible_forward_sequence_runs_without_restart(self):
        class Runtime:
            def __init__(self, *args, **kwargs):
                self.ready = False

            async def initialize(self):
                self.ready = True

            def sync_fence(self):
                self.ready = False

            async def fence_durable_owner(self):
                pass

            async def cleanup_until_quiet(self):
                pass

        class Pages:
            def __init__(self, *args):
                pass

            def register(self):
                pass

            def close(self):
                pass

        instance = self.shell()
        for sequence in (2, 3):
            if sequence == 3:
                self.cache = self.new_cache()
            bundle, key = await self.capture(self.install(sequence))
            with patch(
                "ygl_test_subject.main.importlib.import_module",
                return_value=SimpleNamespace(
                    AstrBotRuntime=Runtime, PublicPages=Pages, AdminPages=Pages
                ),
            ):
                await instance._run_candidate(self.cache, bundle, key, fallback=False)
            self.generations.append(instance._generation)
            self.assertEqual(instance._state, "active")
            self.assertEqual(instance._running_version, f"0.2.{sequence}")
            self.assertTrue(instance._runtime.ready)
            self.assertEqual((await instance._generation.terminate())["state"], "quiet")
        self.assertEqual(
            next(iter(self.cache.release_heads.values()))["release_sequence"], 3
        )


if __name__ == "__main__":
    unittest.main()
