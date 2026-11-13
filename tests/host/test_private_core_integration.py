"""Real Core + private SDK + SQLite; network and messages remain controlled."""

import asyncio
import base64
import hashlib
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from ygl_test_subject.bootstrap.loader import ArtifactCache, CoreGeneration
from ygl_test_subject.extensions.runtime_bundle import RuntimeBundleStore

from tests.extensions.test_private_runtime_bundle import _release, _write
from tests.host.test_external_packages import _Font, _Transport

ROOT = Path(__file__).parents[2]


def runtime_files():
    result = {"__init__.py": (ROOT / "__init__.py").read_bytes()}
    for directory in (
        "adapters",
        "bootstrap",
        "core",
        "extensions",
        "infrastructure",
        "modules",
        "presentation",
        "services",
        "yomihime_game_link_sdk",
        "schemas",
    ):
        for path in (ROOT / directory).rglob("*"):
            if (
                path.is_file()
                and "__pycache__" not in path.parts
                and path.suffix
                in {".py", ".sql", ".json", ".svg", ".css", ".js", ".html"}
            ):
                result[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    return result


class PrivateCoreIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ygl-private-integration-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = []
        self.context = SimpleNamespace(
            get_all_stars=lambda: self.registry,
            get_registered_star=lambda name: next(
                (m for m in self.registry if m.name == name), None
            ),
        )
        self.data = self.root / "data"
        self.data.mkdir()
        self.installation = self.root / "core"
        files = runtime_files()
        _write(self.installation, files)
        self.store = RuntimeBundleStore()
        self.bundle = self.store.capture(
            self.installation, _release(files, package_id="ygl_core"), kind="core"
        )
        self.generation = CoreGeneration(object(), self.context, self.data, self.bundle)
        self.assertEqual((await self.generation.acquire())["state"], "acquired")
        namespace = self.bundle.mount_core()
        module = importlib.import_module(namespace + ".adapters.astrbot.runtime")
        self.runtime = module.AstrBotRuntime(
            self.context,
            plugin_root=self.bundle.resource_root,
            data_dir=self.data,
            font_manager_factory=_Font,
            bundled_selection=None,
            plain_factory=lambda text: text,
            chain_factory=lambda chain: list(chain),
            http_transport_factory=_Transport,
        )
        self.generation.cache = ArtifactCache(self.data, store=self.store)
        self.generation.attach(self.runtime)
        await self.runtime.initialize()
        self.addAsyncCleanup(self.stop)
        self.assertTrue(self.runtime.ready)
        self.manager = self.runtime._independent_packages
        self.namespace = namespace

    async def stop(self):
        result = await self.generation.terminate(timeout=3)
        if result["state"] != "quiet":
            record = self.generation.kernel._record(self.generation.scope)
            if record.cleanup.done() and not record.cleanup.cancelled():
                raise record.cleanup.exception() or AssertionError("cleanup not quiet")
            raise AssertionError("cleanup did not settle")
        self.assertNotIn(str(self.data.resolve()), self.generation.kernel.scopes)

    async def make_dlc(
        self,
        package_id,
        version="1.0.0",
        sequence=1,
        *,
        source_transform=None,
        payload_mib=0,
        with_pages=False,
        with_jobs=False,
        ordinary=False,
        host_identity=None,
    ):
        root = self.root / (package_id + "_" + version.replace(".", "_"))
        descriptor = {
            "schema": "ygl.plugin/1",
            "role": "dlc",
            "package_id": package_id,
            "manifest": "packages/business/manifest.json",
            "control_protocol": {"name": "ygl.control", "major": 1},
        }
        manifest = json.loads(
            (ROOT / "examples/modules/minimal/yomihime.manifest.json").read_bytes()
        )
        manifest.update(
            package_id=package_id,
            package_version=version,
            release=version,
            module_contract={"major": 2, "minimum_minor": 0},
            required_features=[],
            optional_features=[],
            data={"schema": 1, "writes": 1, "readable": [1]},
        )
        manifest["modules"][0].update(route=package_id, config_fields=[])
        if not with_pages:
            manifest["modules"][0].update(pages=[], resources=[])
        source = (
            (ROOT / "examples/modules/minimal/module.py")
            .read_bytes()
            .replace(b"from yomihime_game_link_sdk", b"from ._api_v2")
        )
        if with_jobs:
            manifest["required_features"] = ["jobs.durable/1"]
            manifest["modules"][0]["jobs"] = [
                {
                    "job_id": "work",
                    "parameter_schema": {
                        "type": "object",
                        "properties": {
                            "hold": {"type": "boolean"},
                            "number": {"type": "integer"},
                        },
                        "additionalProperties": False,
                    },
                    "timeout_seconds": 60,
                    "max_active_per_owner": 4,
                }
            ]
            source = source.replace(
                b"evaluators={}", b'evaluators={}, jobs={"work": JobHandler()}'
            )
            source += b"\nimport asyncio\nENTERED=asyncio.Event()\nRELEASE=asyncio.Event()\nCANCELLED=asyncio.Event()\nDENIED=asyncio.Event()\nLIVE=0\nclass JobHandler:\n    async def run(self, invocation, parameters, control):\n        global LIVE\n        LIVE += 1\n        try:\n            if parameters.get('hold'):\n                ENTERED.set()\n                while not RELEASE.is_set():\n                    try:\n                        await RELEASE.wait()\n                    except asyncio.CancelledError:\n                        CANCELLED.set()\n                try:\n                    await control.update({'late': True})\n                except Exception:\n                    DENIED.set()\n            return {'finished': True}\n        finally:\n            LIVE -= 1\n"
        if source_transform is not None:
            source = source_transform(source)
        files = {
            "main.py": b"",
            "ygl-plugin.json": json.dumps(descriptor).encode(),
            "packages/business/manifest.json": json.dumps(manifest).encode(),
            "packages/business/module.py": source,
        }
        if with_pages:
            files["packages/business/pages/entry.js"] = (
                ROOT / "examples/modules/minimal/pages/entry.js"
            ).read_bytes()
        files.update(
            {
                f"payload/{index}.bin": b"p" * (1024 * 1024)
                for index in range(payload_mib)
            }
        )
        _write(root, files)
        release = _release(files, package_id=package_id)
        release.update(
            release=version,
            release_sequence=sequence,
            plugin_id=host_identity
            or {"author": "Synthetic Author", "name": package_id},
        )
        if ordinary:
            release["schema"] = "ygl.release/2"
            del release["channel"], release["release_sequence"]
        raw = json.dumps(release).encode()
        instance = object()
        metadata = SimpleNamespace(
            **release["plugin_id"],
            version=version,
            activated=True,
            star_cls=instance,
            root_dir_name=root.name,
            module=SimpleNamespace(__file__=str(root / "main.py")),
        )
        (root / "release.json").write_bytes(raw)
        if ordinary:
            signature_path = root / "release.sig.json"
            if signature_path.exists():
                signature_path.unlink()
            self.registry.append(metadata)
            return self.manager.discovery.for_instance(instance), metadata
        key = getattr(self, "key", None)
        if key is None:
            self.key = key = Ed25519PrivateKey.generate()
            public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
            self.key_id = hashlib.sha256(public).hexdigest()
            await self.manager.repository.put_publisher(
                public,
                scope_policy={"channels": ["stable"]},
                actor_ref="offline authenticated test",
                expected_revision=0,
            )
        signature = {
            "algorithm": "ed25519",
            "key_id": self.key_id,
            "signature_base64": base64.b64encode(key.sign(raw)).decode(),
        }
        (root / "release.sig.json").write_text(json.dumps(signature))
        disk_manifest = importlib.import_module(
            self.namespace + ".extensions.disk_manifest"
        )
        protocol = importlib.import_module(
            self.namespace + ".extensions.hot_update_protocol"
        )
        permissions = importlib.import_module(
            self.namespace + ".services.extension_permissions"
        )
        existing = await self.manager.repository.grant(package_id)
        if existing is None:
            await self.manager.repository.approve_package(
                package_id,
                publisher_key_id=self.key_id,
                plugin_id=release["plugin_id"],
                permissions=permissions.permission_snapshot(
                    disk_manifest.parse_manifest(protocol.b05_manifest_bytes(manifest))
                ),
                actor_ref="offline authenticated test",
                expected_revision=0,
            )
        self.registry.append(metadata)
        return self.manager.discovery.for_instance(instance), metadata

    async def preparing_fixture(self, package_id, *, resistant=False):
        def transform(source):
            body = (
                "        del services\n"
                "        ENTERED.set()\n"
                "        try:\n"
                "            await RELEASE.wait()\n"
                "        except asyncio.CancelledError:\n"
                "            CANCELLED.set()\n"
            )
            if resistant:
                body += (
                    "            while not RELEASE.is_set():\n"
                    "                try:\n"
                    "                    await RELEASE.wait()\n"
                    "                except asyncio.CancelledError:\n"
                    "                    CANCEL_COUNT.append(1)\n"
                )
            else:
                body += "            raise\n"
            body += "        return MinimalModule()"
            return (
                source.replace(
                    b"        del services\n        return MinimalModule()",
                    body.encode(),
                ).replace(
                    b"    async def stop(self) -> None:\n        pass",
                    b"    async def stop(self) -> None:\n        STOPPED.append(self)",
                )
                + (
                    "\nimport asyncio\nENTERED=asyncio.Event()\nRELEASE=asyncio.Event()\n"
                    "CANCELLED=asyncio.Event()\nCANCEL_COUNT=[]\nSTOPPED=[]\n"
                    f"PREPARE_FIXTURE={package_id!r}\n"
                ).encode()
            )

        installation, _ = await self.make_dlc(package_id, source_transform=transform)
        reply = await self.manager.endpoint.request(
            installation.instance,
            {
                "protocol": "ygl.control/1",
                "op": "connect",
                "request_id": str(uuid4()),
                "descriptor_digest": installation.descriptor_digest,
            },
        )
        operation = self.manager.endpoint._operations[reply["operation_id"]]
        async with asyncio.timeout(3):
            while True:
                module = next(
                    (
                        m
                        for m in tuple(sys.modules.values())
                        if getattr(m, "PREPARE_FIXTURE", None) == package_id
                    ),
                    None,
                )
                if module is not None and module.ENTERED.is_set():
                    break
                await asyncio.sleep(0.005)

        async def release_factory():
            module.RELEASE.set()

        self.addAsyncCleanup(release_factory)
        return installation, operation, module

    async def test_failed_prepare_cleanup_retains_owner_and_blocks_new_capture(self):
        if os.environ.get("YGL_PREPARE_FAILURE_CHILD") != "1":
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "unittest",
                self.id(),
                env={**os.environ, "YGL_PREPARE_FAILURE_CHILD": "1"},
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), 20)
            self.assertEqual(process.returncode, 0, stderr.decode())
            proof = json.loads(stdout.decode().splitlines()[-1])
            self.assertTrue(all(proof.values()), proof)
            return

        # This intentionally irrecoverable stop failure must never be repaired
        # by resetting a production owner in test teardown. Isolate its ledger
        # in this child; assert retained ownership before process exit.
        self.manager._prepare_timeout = 0.03

        def transform(source):
            return (
                source.replace(
                    b"        del services\n        return MinimalModule()",
                    b"        del services\n        try:\n            await asyncio.Event().wait()\n        except asyncio.CancelledError:\n            return MinimalModule()",
                ).replace(
                    b"    async def stop(self) -> None:\n        pass",
                    b"    async def stop(self) -> None:\n        STOPPED.append(self)\n        raise RuntimeError('synthetic stop failure')",
                )
                + b"\nimport asyncio\nSTOPPED=[]\nPREPARE_FAILURE=True\n"
            )

        installation, _ = await self.make_dlc(
            "failed_prepare_stop", source_transform=transform
        )
        reply = await self.manager.endpoint.request(
            installation.instance,
            {
                "protocol": "ygl.control/1",
                "op": "connect",
                "request_id": str(uuid4()),
                "descriptor_digest": installation.descriptor_digest,
            },
        )
        operation = self.manager.endpoint._operations[reply["operation_id"]]
        await asyncio.wait_for(asyncio.shield(operation.task), 3)
        package = self.manager._prepared[id(installation.instance)]
        cleanup = self.manager._retired_prepared[package.transition_id]
        self.assertTrue(cleanup.done())
        self.assertIsInstance(cleanup.exception(), RuntimeError)
        self.assertTrue(self.manager._failed_candidate_cleanup)
        self.assertTrue(operation.reply["ok"])
        self.assertEqual(operation.reply["state"], "cleanup_pending")
        self.assertEqual(operation.reply["error"]["code"], "prepare_timeout")
        for _ in range(2):
            status = await self.manager.endpoint.request(
                installation.instance,
                {
                    "protocol": "ygl.control/1",
                    "op": "status",
                    "operation_id": operation.operation_id,
                },
            )
            self.assertTrue(status["ok"])
            self.assertEqual(status["state"], "cleanup_pending")
            self.assertEqual(status["operation_id"], operation.operation_id)
        module = next(
            m
            for m in tuple(sys.modules.values())
            if getattr(m, "PREPARE_FAILURE", False) is True
        )
        self.assertEqual(len(module.STOPPED), 1)
        held = self.store.reserved_bytes("dlc")
        self.assertGreater(held, 0)
        candidate, _ = await self.make_dlc("after_failed_stop")
        captures = []
        original_capture = self.manager.cache.capture

        async def forbidden_capture(*args, **kwargs):
            captures.append(True)
            return await original_capture(*args, **kwargs)

        self.manager.cache.capture = forbidden_capture
        errors = importlib.import_module(
            self.namespace + ".yomihime_game_link_sdk.host"
        )
        with self.assertRaises(errors.ExtensionHostError) as denied:
            await self.manager.replace(candidate)
        self.assertEqual(denied.exception.code, "cleanup_pending")
        self.assertEqual(captures, [])
        self.assertEqual(self.store.reserved_bytes("dlc"), held)
        self.assertIs(self.manager._retired_prepared[package.transition_id], cleanup)
        self.assertEqual(len(module.STOPPED), 1)
        result = await self.generation.terminate(timeout=0.2)
        self.assertNotEqual(result["state"], "quiet")
        successor = CoreGeneration(object(), self.context, self.data, self.bundle)
        result = await successor.acquire()
        self.assertNotEqual(result["state"], "acquired")
        self.assertFalse(self.generation.cleanup_complete)
        self.assertEqual(self.store.reserved_bytes("dlc"), held)
        print(
            json.dumps(
                {
                    "failed_cleanup_retained": True,
                    "capture_blocked": True,
                    "stop_once": len(module.STOPPED) == 1,
                    "takeover_blocked": True,
                }
            ),
            flush=True,
        )
        os._exit(0)

    async def test_prepare_timeout_requires_finite_positive_budget(self):
        cls = type(self.manager)
        for value in (0, -1, True, float("inf"), float("nan"), "30"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                cls(
                    self.runtime,
                    self.generation,
                    self.manager.cache,
                    prepare_timeout=value,
                )

    async def test_cooperative_prepare_deadline_releases_slot_for_healthy_candidate(
        self,
    ):
        self.manager._prepare_timeout = 0.15
        sibling, _ = await self.make_dlc("prepare_sibling")
        await self.manager.replace(sibling)
        baseline = self.store.reserved_bytes("dlc")
        installation, operation, module = await self.preparing_fixture(
            "prepare_timeout"
        )
        healthy, _ = await self.make_dlc("after_prepare_timeout")
        healthy_task = asyncio.create_task(self.manager.replace(healthy))
        self.manager.packages["prepare_sibling"].gate.check()
        async with asyncio.timeout(3):
            await asyncio.shield(operation.task)
            self.assertEqual((await healthy_task)["state"], "active")
        self.assertTrue(module.CANCELLED.is_set())
        self.assertEqual(operation.reply["error"]["code"], "prepare_timeout")
        self.assertNotIn(id(installation.instance), self.manager._prepared)
        self.assertFalse(self.manager._retired_prepared)
        self.assertEqual(module.STOPPED, [])
        self.assertGreater(self.store.reserved_bytes("dlc"), baseline)
        self.assertNotIn("prepare_timeout", self.manager.packages)

    async def test_core_close_cancels_cooperative_prepare_before_deadline(self):
        self.manager._prepare_timeout = 30
        _, operation, module = await self.preparing_fixture("prepare_shutdown")
        result = await self.generation.terminate(timeout=3)
        self.assertEqual(result["state"], "quiet")
        self.assertTrue(module.CANCELLED.is_set())
        self.assertTrue(operation.task.done())
        self.assertEqual(self.store.reserved_bytes("dlc"), 0)

    async def test_resistant_prepare_retains_exact_factory_and_cleanup_until_late_stop(
        self,
    ):
        self.manager._prepare_timeout = 0.04
        installation, operation, module = await self.preparing_fixture(
            "prepare_resistant", resistant=True
        )
        await asyncio.wait_for(module.CANCELLED.wait(), 2)
        package = self.manager._prepared[id(installation.instance)]
        factory = package.factory_tasks[0]
        async with asyncio.timeout(2):
            while package.transition_id not in self.manager._retired_prepared:
                await asyncio.sleep(0.005)
        cleanup = self.manager._retired_prepared[package.transition_id]
        held = self.store.reserved_bytes("dlc")
        self.assertFalse(factory.done())
        self.assertTrue(operation.reply["ok"])
        self.assertEqual(operation.reply["state"], "cleanup_pending")
        self.assertEqual(operation.reply["error"]["code"], "prepare_timeout")
        for _ in range(2):
            status = await self.manager.endpoint.request(
                installation.instance,
                {
                    "protocol": "ygl.control/1",
                    "op": "status",
                    "operation_id": operation.operation_id,
                },
            )
            self.assertTrue(status["ok"])
            self.assertEqual(status["state"], "cleanup_pending")
            self.assertEqual(status["operation_id"], operation.operation_id)
        effect = importlib.import_module(
            self.namespace + ".services.runtime_effect_gate"
        )
        with self.assertRaises(effect.EffectRejected):
            package.gate.check()
        for _ in range(2):
            observer = asyncio.create_task(self.manager._discard_prepared(package))
            await asyncio.sleep(0)
            observer.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await observer
            self.assertIs(
                self.manager._retired_prepared[package.transition_id], cleanup
            )
            self.assertIs(package.factory_tasks[0], factory)
            self.assertEqual(self.store.reserved_bytes("dlc"), held)
        healthy, _ = await self.make_dlc("behind_resistant_prepare")
        queued = asyncio.create_task(self.manager.replace(healthy))
        await asyncio.sleep(0.02)
        self.assertFalse(queued.done())
        self.assertTrue(self.manager._transition_lock.locked())
        result = await self.generation.terminate(timeout=0.01)
        self.assertEqual(result["state"], "parked")
        self.assertFalse(cleanup.done())
        self.assertTrue(self.manager._transition_lock.locked())
        self.assertFalse(factory.done())
        self.assertEqual(self.store.reserved_bytes("dlc"), held)
        self.assertEqual(module.CANCEL_COUNT, [])
        self.assertNotIn("behind_resistant_prepare", self.manager.packages)
        await asyncio.gather(queued, return_exceptions=True)
        module.RELEASE.set()
        self.assertEqual((await self.generation.terminate(timeout=3))["state"], "quiet")
        self.assertTrue(factory.done())
        self.assertTrue(cleanup.done())
        self.assertEqual(len(module.STOPPED), 1)
        await self.manager._discard_prepared(package)
        self.assertIs(package.prepare_cleanup_task, cleanup)
        self.assertEqual(len(module.STOPPED), 1)
        self.assertEqual(self.store.reserved_bytes("dlc"), 0)

    async def test_unknown_dlc_uses_private_sdk_and_durable_owner(self):
        installation, _ = await self.make_dlc("unseen")
        result = await self.manager.replace(installation)
        self.assertEqual(result["state"], "active")
        module = self.runtime._core.registry.snapshot().modules["unseen/demo"]
        self.assertTrue(module.enabled)
        package = self.manager.packages["unseen"]
        self.assertTrue(
            package.instances["unseen/demo"].__class__.__module__.startswith(
                self.namespace
            )
        )
        rows = await self.runtime._core.database.executor.run_read(
            lambda u: [
                tuple(r)
                for r in u.execute(
                    "SELECT scope,accepting FROM runtime_owners ORDER BY scope"
                ).fetchall()
            ]
        )
        self.assertEqual(rows, [("core", 1), ("package:unseen", 1)])
        self.assertIsNot(
            self.runtime._core.__class__,
            importlib.import_module(
                "ygl_test_subject.services.core_runtime"
            ).CoreRuntime,
        )

    async def test_update_a_keeps_b_active_and_rejects_old_gate(self):
        a, old_metadata = await self.make_dlc("alpha")
        b, _ = await self.make_dlc("beta")
        await self.manager.replace(a)
        await self.manager.replace(b)
        old_gate = self.manager.packages["alpha"].gate
        self.registry.remove(old_metadata)
        new_a, _ = await self.make_dlc("alpha", "1.0.1", 2)
        result = await self.manager.replace(new_a)
        self.assertEqual(result["running_version"], "1.0.1")
        self.manager.packages["beta"].gate.check()
        with self.assertRaises(Exception):
            old_gate.check()

    async def test_host_install_refresh_needs_no_legacy_approval_and_fences_old_effects(
        self,
    ):
        executor = self.runtime._core.database.executor
        invocation_guard = importlib.import_module(
            self.namespace + ".services.runtime_effect_gate"
        ).invocation_guard
        installation, metadata = await self.make_dlc("native", ordinary=True)
        self.assertEqual((await self.manager.replace(installation))["state"], "active")
        old = self.manager.packages["native"]
        old_digest = old.receipt.artifact_digest
        self.assertIsNone(await self.manager.repository.grant("native"))
        self.assertIsNone(await self.manager.repository.head("native", "", "stable"))

        def save_config(unit):
            unit.execute(
                "INSERT INTO config_state VALUES ('test-user', 'native/demo', 1)"
            )
            unit.execute(
                "INSERT INTO config_entries (principal_id, module_id, field, value_json, revision) "
                "VALUES ('test-user', 'native/demo', 'test', '\"kept\"', 1)"
            )

        with invocation_guard(old.gate):
            await executor.run_transaction(save_config)
        metadata.activated = False
        self.assertFalse(old.active)
        self.registry.remove(metadata)

        signed, metadata = await self.make_dlc("native", "1.0.1", 2)
        self.assertEqual((await self.manager.replace(signed))["state"], "active")
        old_grant = await self.manager.repository.grant("native")
        old_head = await self.manager.repository.head("native", self.key_id, "stable")
        self.registry.remove(metadata)
        ordinary, metadata = await self.make_dlc("native", "1.0.1", ordinary=True)
        result = await self.manager.replace(ordinary)
        self.assertEqual(result["runtime_state"], "active")
        self.assertEqual(result["running_version"], "1.0.1")
        active = self.manager.packages["native"]
        self.assertNotEqual(active.receipt.artifact_digest, old_digest)
        self.assertEqual(await self.manager.repository.grant("native"), old_grant)
        self.assertEqual(
            await self.manager.repository.head("native", self.key_id, "stable"),
            old_head,
        )
        self.assertIsNone(
            await self.manager.repository.approval(active.receipt.artifact_digest)
        )

        await self.manager.repository.revoke_package(
            "native",
            actor_ref="offline authenticated test",
            expected_revision=old_grant["revision"],
        )
        active.gate.check()

        def disable_before_commit(unit):
            unit.execute(
                "UPDATE config_entries SET value_json = '\"late\"', revision = 2 "
                "WHERE principal_id = 'test-user' AND module_id = 'native/demo'"
            )
            metadata.activated = False

        with (
            invocation_guard(active.gate),
            self.assertRaisesRegex(RuntimeError, "stale_lease"),
        ):
            await executor.run_transaction(disable_before_commit)
        persisted = await executor.run_read(
            lambda unit: tuple(
                unit.execute(
                    "SELECT value_json, revision FROM config_entries "
                    "WHERE principal_id = 'test-user' AND module_id = 'native/demo'"
                ).fetchone()
            )
        )
        self.assertEqual(persisted, ('"kept"', 1))

    async def test_ordinary_owner_survives_legacy_grant_and_activation_without_pin(
        self,
    ):
        signed, metadata = await self.make_dlc("native")
        self.registry.remove(metadata)
        executor = self.runtime._core.database.executor
        await executor.run_internal_transaction(
            lambda unit: (
                unit.execute(
                    "INSERT INTO extension_installation_grants VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "native",
                        "native",
                        "old root",
                        1,
                        "{}",
                        "a" * 64,
                        "b" * 64,
                        "fixture",
                        0,
                    ),
                ).rowcount
            )
        )
        await self.manager.repository.revoke_package(
            "native", actor_ref="offline authenticated test", expected_revision=1
        )
        other = {"author": "Different Owner", "name": "different-host-plugin"}
        unowned, metadata = await self.make_dlc(
            "native", "1.0.1", ordinary=True, host_identity=other
        )
        with self.assertRaisesRegex(ValueError, "package_already_owned"):
            await self.manager.replace(unowned)
        self.registry.remove(metadata)
        author_only = {"author": "Different Owner", "name": "native"}
        unowned, metadata = await self.make_dlc(
            "native", "1.0.2", ordinary=True, host_identity=author_only
        )
        with self.assertRaisesRegex(ValueError, "package_already_owned"):
            await self.manager.replace(unowned)
        self.assertNotIn("native", self.manager.cache.current)
        self.registry.remove(metadata)

        successor, _ = await self.make_dlc("native", "1.0.3", ordinary=True)
        self.assertEqual(successor.plugin_id, signed.plugin_id)
        with patch.object(
            self.manager.cache,
            "publish",
            side_effect=RuntimeError("failed pin publication"),
        ):
            with self.assertRaisesRegex(RuntimeError, "failed pin publication"):
                await self.manager.replace(successor)
        self.assertNotIn("native", self.manager.cache.current)
        owner = await executor.run_internal_transaction(
            lambda unit: unit.execute(
                "SELECT plugin_id FROM extension_package_owners WHERE package_id=?",
                ("native",),
            ).fetchone()[0]
        )
        self.assertEqual(json.loads(owner), signed.plugin_id)
        self.assertTrue((await self.manager.repository.grant("native"))["revoked"])

        # A fresh ordinary package can commit durable identity without any legacy grant.
        fresh, metadata = await self.make_dlc("fresh", ordinary=True)
        with patch.object(
            self.manager.cache,
            "publish",
            side_effect=RuntimeError("failed pin publication"),
        ):
            with self.assertRaisesRegex(RuntimeError, "failed pin publication"):
                await self.manager.replace(fresh)
        self.assertNotIn("fresh", self.manager.cache.current)
        self.assertIsNone(await self.manager.repository.grant("fresh"))
        self.registry.remove(metadata)
        replacement, _ = await self.make_dlc(
            "fresh", "1.0.1", ordinary=True, host_identity=other
        )
        with self.assertRaisesRegex(ValueError, "package_already_owned"):
            await self.manager.replace(replacement)

    async def test_same_instance_reapproval_retries_fresh_epoch_and_retires_old_lease(
        self,
    ):
        from uuid import uuid4

        installation, _ = await self.make_dlc("reapprove")
        endpoint = self.manager.endpoint

        async def connect():
            queued = await endpoint.request(
                installation.instance,
                {
                    "protocol": "ygl.control/1",
                    "op": "connect",
                    "request_id": str(uuid4()),
                    "descriptor_digest": installation.descriptor_digest,
                },
            )
            self.assertTrue(queued["ok"])
            for _ in range(200):
                reply = await endpoint.request(
                    installation.instance,
                    {
                        "protocol": "ygl.control/1",
                        "op": "status",
                        "operation_id": queued["operation_id"],
                    },
                )
                if reply["state"] not in {"queued", "preparing", "cleanup_pending"}:
                    return queued, reply
                await asyncio.sleep(0.005)
            self.fail("connect did not settle")

        queued, original = await connect()
        self.assertEqual(original["state"], "active")
        old = self.manager.packages["reapprove"]
        grant = await self.manager.repository.grant("reapprove")
        await self.manager.repository.revoke_package(
            "reapprove",
            expected_revision=grant["revision"],
            actor_ref="offline authenticated test",
        )
        retired = await endpoint.request(
            installation.instance,
            {
                "protocol": "ygl.control/1",
                "op": "status",
                "operation_id": queued["operation_id"],
            },
        )
        self.assertNotEqual(retired["state"], "active")
        revoked = await self.manager.repository.grant("reapprove")
        await self.manager.repository.approve_package(
            "reapprove",
            publisher_key_id=self.key_id,
            plugin_id={"author": "Synthetic Author", "name": "reapprove"},
            permissions=json.loads(old.receipt.permission_projection_json),
            expected_revision=revoked["revision"],
            actor_ref="offline authenticated test",
        )
        _, active = await connect()
        self.assertEqual(active["state"], "active")
        self.assertNotEqual(active["lease"], original["lease"])
        fresh = self.manager.packages["reapprove"]
        self.assertIsNot(fresh, old)
        self.assertGreater(fresh.epoch, old.epoch)
        fresh.gate.check()
        with self.assertRaises(Exception):
            old.gate.check()
        retired = await endpoint.request(
            installation.instance,
            {
                "protocol": "ygl.control/1",
                "op": "status",
                "operation_id": queued["operation_id"],
            },
        )
        self.assertNotEqual(retired["state"], "active")

    async def test_package_job_resistant_run_and_terminal_sql_drain_before_update(self):
        first, metadata = await self.make_dlc("job_a", with_jobs=True)
        sibling, _ = await self.make_dlc("job_b", with_jobs=True)
        await self.manager.replace(first)
        await self.manager.replace(sibling)
        jobs = self.runtime._core.jobs
        sdk = importlib.import_module(self.namespace + ".yomihime_game_link_sdk.jobs")
        old = self.manager.packages["job_a"]
        business = importlib.import_module(old.bundle.namespace + ".module")
        operations = await jobs.for_dashboard(
            subject="test-admin", module_id="job_a/demo", current=lambda: True
        )
        job = await operations.submit(sdk.JobRequest("work", {"hold": True}))
        await asyncio.wait_for(business.ENTERED.wait(), 2)
        active = jobs._runs[job.job_id]
        active["scope"]._cleanup_timeout = 0.01
        module_scope = self.runtime._core.lifecycle.scope("job_a/demo")
        module_scope._cleanup_timeout = 0.01
        original_finish = jobs.repository.finish
        terminal_entered, terminal_release = asyncio.Event(), asyncio.Event()

        async def hold_terminal(*args, **kwargs):
            if args[0] == job.job_id:
                terminal_entered.set()
                await terminal_release.wait()
            return await original_finish(*args, **kwargs)

        jobs.repository.finish = hold_terminal
        self.registry.remove(metadata)
        update, _ = await self.make_dlc("job_a", "1.0.1", 2, with_jobs=True)
        task = asyncio.create_task(self.manager.replace(update))
        try:
            await asyncio.wait_for(business.CANCELLED.wait(), 2)
            for _ in range(4):
                module_scope.cancel()
                await asyncio.sleep(0.02)
            self.assertFalse(task.done())
            self.assertFalse(old.bundle._released)
            self.assertIn(job.job_id, jobs._runs)
            sibling_ops = await jobs.for_dashboard(
                subject="test-admin", module_id="job_b/demo", current=lambda: True
            )
            sibling_job = await sibling_ops.submit(
                sdk.JobRequest("work", {"hold": False})
            )

            async def wait_sibling():
                while (await sibling_ops.status(sibling_job.job_id)).status.value in {
                    "queued",
                    "running",
                }:
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(wait_sibling(), 2)
            business.RELEASE.set()
            await asyncio.wait_for(terminal_entered.wait(), 2)
            await asyncio.sleep(0.02)
            self.assertFalse(task.done())
            self.assertTrue(business.DENIED.is_set())
            self.assertFalse(old.bundle._released)
            terminal_release.set()
            result = await asyncio.wait_for(task, 3)
            self.assertEqual(result["running_version"], "1.0.1")
            self.assertEqual(business.LIVE, 0)
            self.assertTrue(old.bundle._released)
            rows = await self.runtime._core.database.executor.run_read(
                lambda unit: [
                    tuple(row)
                    for row in unit.execute(
                        "SELECT status,run_token FROM durable_jobs WHERE job_id=?",
                        (job.job_id,),
                    ).fetchall()
                ]
            )
            self.assertEqual(rows, [("interrupted", None)])
            self.manager.packages["job_b"].gate.check()
        finally:
            business.RELEASE.set()
            terminal_release.set()
            await asyncio.gather(task, return_exceptions=True)
            jobs.repository.finish = original_finish

    async def test_package_instance_stop_can_signal_its_resistant_job_before_drain(
        self,
    ):
        first, metadata = await self.make_dlc("stop_job", with_jobs=True)
        await self.manager.replace(first)
        jobs = self.runtime._core.jobs
        sdk = importlib.import_module(self.namespace + ".yomihime_game_link_sdk.jobs")
        old = self.manager.packages["stop_job"]
        business = importlib.import_module(old.bundle.namespace + ".module")
        operations = await jobs.for_dashboard(
            subject="test-admin", module_id="stop_job/demo", current=lambda: True
        )
        job = await operations.submit(sdk.JobRequest("work", {"hold": True}))
        await asyncio.wait_for(business.ENTERED.wait(), 2)
        stopped = asyncio.Event()
        delegate = old.instances["stop_job/demo"]._delegate
        actual_stop = delegate.stop

        async def signal_job():
            stopped.set()
            business.RELEASE.set()
            await actual_stop()

        delegate.stop = signal_job
        self.registry.remove(metadata)
        update, _ = await self.make_dlc("stop_job", "1.0.1", 2, with_jobs=True)
        try:
            result = await asyncio.wait_for(self.manager.replace(update), 2)
            self.assertEqual(result["running_version"], "1.0.1")
            self.assertTrue(stopped.is_set())
            self.assertEqual(business.LIVE, 0)
            self.assertNotIn(job.job_id, jobs._runs)
            self.assertTrue(old.bundle._released)
        finally:
            business.RELEASE.set()

    async def test_package_job_unstarted_claim_settlement_blocks_only_its_update(self):
        first, metadata = await self.make_dlc("unstarted", with_jobs=True)
        sibling, _ = await self.make_dlc("unrelated", with_jobs=True)
        await self.manager.replace(first)
        await self.manager.replace(sibling)
        jobs = self.runtime._core.jobs
        sdk = importlib.import_module(self.namespace + ".yomihime_game_link_sdk.jobs")
        scope = self.runtime._core.lifecycle.scope("unstarted/demo")
        original_create, original_finish = scope.create_task, jobs.repository.finish
        terminal_entered, terminal_release = asyncio.Event(), asyncio.Event()

        def no_entry(work, *, name):
            child = original_create(work, name=name)
            child.cancel()
            return child

        async def hold_terminal(*args, **kwargs):
            if args[2] == "interrupted":
                terminal_entered.set()
                await terminal_release.wait()
            return await original_finish(*args, **kwargs)

        scope.create_task = no_entry
        jobs.repository.finish = hold_terminal
        operations = await jobs.for_dashboard(
            subject="test-admin", module_id="unstarted/demo", current=lambda: True
        )
        job = await operations.submit(sdk.JobRequest("work", {}))
        await asyncio.wait_for(terminal_entered.wait(), 2)
        old = self.manager.packages["unstarted"]
        self.registry.remove(metadata)
        update, _ = await self.make_dlc("unstarted", "1.0.1", 2, with_jobs=True)
        task = asyncio.create_task(self.manager.replace(update))
        try:
            await asyncio.sleep(0.15)
            self.assertFalse(task.done())
            self.assertFalse(old.bundle._released)
            self.manager.packages["unrelated"].gate.check()
            terminal_release.set()
            self.assertEqual(
                (await asyncio.wait_for(task, 3))["running_version"], "1.0.1"
            )
            rows = await self.runtime._core.database.executor.run_read(
                lambda unit: [
                    tuple(row)
                    for row in unit.execute(
                        "SELECT status,run_token FROM durable_jobs WHERE job_id=?",
                        (job.job_id,),
                    ).fetchall()
                ]
            )
            self.assertEqual(rows, [("interrupted", None)])
        finally:
            terminal_release.set()
            await asyncio.gather(task, return_exceptions=True)
            scope.create_task = original_create
            jobs.repository.finish = original_finish

    async def test_host_disable_before_actual_commit_worker_never_installs_owner(self):
        installation, metadata = await self.make_dlc("queued")
        executor = self.runtime._core.database.executor
        actual = executor.run_internal_transaction
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(callback, **kwargs):
            if callback.__name__ == "commit":
                entered.set()
                await release.wait()
            return await actual(callback, **kwargs)

        executor.run_internal_transaction = delayed
        task = asyncio.create_task(self.manager.replace(installation))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            metadata.activated = False
            release.set()
            with self.assertRaisesRegex(Exception, "stale_instance"):
                await task
            rows = await actual(
                lambda unit: [
                    tuple(row)
                    for row in unit.connection.execute(
                        "SELECT scope FROM runtime_owners WHERE scope='package:queued'"
                    ).fetchall()
                ]
            )
            self.assertEqual(rows, [])
            self.assertNotIn("queued", self.manager.packages)
            self.assertNotIn(
                "queued/demo", self.runtime._core.registry.snapshot().modules
            )
        finally:
            release.set()
            executor.run_internal_transaction = actual

    async def test_unknown_package_pages_publish_captured_assets_and_withdraw(self):
        installation, _ = await self.make_dlc("pages", with_pages=True)
        await self.manager.replace(installation)
        bindings = self.runtime._external_page_bindings()
        self.assertIsInstance(bindings["pages"], str)
        assets = self.manager.page_assets("pages/demo", "home")
        self.assertEqual(assets["entry"], "pages/entry.js")
        self.assertEqual(len(assets["resources"]), 1)
        (installation.root / "packages/business/pages/entry.js").write_bytes(
            b"mutable replacement"
        )
        self.assertEqual(self.manager.page_assets("pages/demo", "home"), assets)
        self.assertIn(
            ("pages/demo", "home", "status"), self.manager.public_web_page_bindings()
        )
        await self.manager.withdraw_instance(installation.instance)
        self.assertIsNone(self.runtime._external_page_bindings()["pages"])
        self.assertFalse(self.manager.public_web_page_bindings())
        with self.assertRaisesRegex(Exception, "page_unavailable"):
            self.manager.page_assets("pages/demo", "home")

    async def test_publisher_revoke_invalidates_authorization_and_fences_continuation(
        self,
    ):
        from tests.services.test_source_authorizations import descriptor

        installation, _ = await self.make_dlc("auth")
        await self.manager.replace(installation)
        effect = importlib.import_module(
            self.namespace + ".services.runtime_effect_gate"
        )
        authorization = importlib.import_module(
            self.namespace + ".services.source_authorizations"
        )
        sdk = importlib.import_module(
            self.namespace + ".yomihime_game_link_sdk.services"
        )
        management = self.runtime._source_authorizations
        definition = descriptor()
        management._policy = SimpleNamespace(
            authorizations={
                "auth/demo": {
                    "example_qr": {
                        "credential_field": "credential",
                        "descriptor": definition,
                    }
                }
            }
        )
        binding = authorization.AuthorizationBinding(
            "auth/demo", "admin", "page", "runtime", 1, 1
        )
        entered, release = asyncio.Event(), asyncio.Event()
        requests, writes = [], []
        gate = self.manager.guard_for_owner("auth/demo")

        async def guard():
            gate.check()

        async def commit(material):
            gate.check()
            writes.append(material)

        class Transport:
            async def fetch(
                inner, request, *, _check_authority, _authorization_request
            ):
                await _check_authority()
                requests.append(request.path)
                if request.path == "/apply":
                    result = {
                        "url": "https://identity.example.test/scan?ticket=synthetic"
                    }
                else:
                    entered.set()
                    await release.wait()
                    await _check_authority()
                    result = {
                        "state": 20,
                        "token": "synthetic-token",
                        "account": "user-one",
                    }
                return sdk.HttpResponse(
                    200, {}, json.dumps({"result": result}).encode()
                )

        transport = Transport()

        async def execute(step, session=None):
            # This is the exact package override used by the Core browser entry.
            with effect.invocation_guard(gate):
                return await management.execute(
                    binding=binding,
                    flow_id="example_qr",
                    step=step,
                    session_id=session,
                    guard=guard,
                    commit=commit,
                    http=transport,
                )

        result = await execute("begin")
        session = result["session"]["session_id"]
        runner = management._managers["auth/demo"]
        runner._clock = lambda: 10000.0
        runner._session(session, binding).next_poll_at = 0
        task = asyncio.create_task(execute("poll", session))
        await asyncio.wait_for(entered.wait(), 2)
        publisher = await self.manager.repository.publisher(self.key_id)
        await self.manager.repository.revoke_publisher(
            self.key_id,
            expected_revision=publisher["revision"],
            actor_ref="offline authenticated test",
        )
        count = len(requests)
        release.set()
        with self.assertRaises(Exception):
            await task
        with self.assertRaises(effect.EffectRejected):
            await execute("poll", session)
        self.assertEqual(len(requests), count)
        self.assertEqual(writes, [])
        self.assertFalse(runner._session(session, binding).slots)

    async def test_active_package_budget_preserves_siblings_and_update_reserve(self):
        metadata = {}
        for name in ("large_a", "large_b", "large_c", "large_d"):
            installation, metadata[name] = await self.make_dlc(name, payload_mib=7)
            await self.manager.replace(installation)
        gates = {key: package.gate for key, package in self.manager.packages.items()}
        excess, _ = await self.make_dlc("excess", payload_mib=7)
        with self.assertRaisesRegex(Exception, "active_package_budget_exceeded"):
            await self.manager.replace(excess)
        self.assertEqual(set(self.manager.packages), set(gates))
        for gate in gates.values():
            gate.check()
        self.registry.remove(metadata["large_a"])
        update, _ = await self.make_dlc("large_a", "1.0.1", 2, payload_mib=7)
        self.assertEqual(
            (await self.manager.replace(update))["running_version"], "1.0.1"
        )
        for name in ("large_b", "large_c", "large_d"):
            self.assertIs(self.manager.packages[name].gate, gates[name])
            gates[name].check()
        self.assertLessEqual(
            sum(p.bundle.total_bytes for p in self.manager.packages.values()),
            32 * 1024 * 1024,
        )

    async def test_prepare_config_access_is_closed_and_handlers_share_guard(self):
        effect = importlib.import_module(
            self.namespace + ".services.runtime_effect_gate"
        )
        factory, _ = await self.make_dlc(
            "impure",
            source_transform=lambda source: source.replace(
                b"        del services", b"        await services.config.current()"
            ),
        )
        with self.assertRaises(effect.EffectRejected) as denied:
            await self.manager._prepare(factory)
        self.assertEqual(denied.exception.code, "prepare_side_effect_forbidden")
        self.assertNotIn("impure", self.manager.packages)
        clean, _ = await self.make_dlc("handlers")
        external = importlib.import_module(
            self.namespace + ".services.external_packages"
        )
        original = external._GuardedInstance
        observed = []

        class CheckingGuarded(original):
            def __init__(inner, delegate, gate):
                original_handlers = delegate.handlers

                def handlers():
                    with self.assertRaises(effect.EffectRejected) as denied:
                        effect.check_current_effect()
                    observed.append(denied.exception.code)
                    return original_handlers()

                delegate.handlers = handlers
                super().__init__(delegate, gate)

        external._GuardedInstance = CheckingGuarded
        try:
            with self.assertRaises(effect.EffectRejected) as denied:
                await self.manager._prepare(clean)
            self.assertEqual(denied.exception.code, "prepare_side_effect_forbidden")
            self.assertEqual(observed, ["prepare_side_effect_forbidden"])
        finally:
            external._GuardedInstance = original

    async def test_package_pending_cleanup_retains_future_and_resumes_exact_journal(
        self,
    ):
        original, metadata = await self.make_dlc("pending")
        sibling, _ = await self.make_dlc("sibling")
        await self.manager.replace(original)
        await self.manager.replace(sibling)
        old = self.manager.packages["pending"]
        release, entered = asyncio.Event(), asyncio.Event()

        async def slow_stop():
            entered.set()
            await release.wait()

        old.instances["pending/demo"]._delegate.stop = slow_stop
        self.registry.remove(metadata)
        update, _ = await self.make_dlc("pending", "1.0.1", 2)
        task = asyncio.create_task(self.manager.replace(update))
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.sleep(0.15)
        cleanup = old.cleanup_task
        self.assertIsNotNone(cleanup)
        self.assertFalse(cleanup.done())
        rows = await self.runtime._core.database.executor.run_internal_transaction(
            lambda unit: [
                tuple(row)
                for row in unit.connection.execute(
                    "SELECT phase,resume_phase FROM runtime_transitions WHERE scope='package:pending'"
                ).fetchall()
            ]
        )
        self.assertIn(("WAITING_CLEANUP", "OLD_FENCED"), rows)
        self.manager.packages["sibling"].gate.check()
        self.assertIs(old.cleanup_task, cleanup)
        release.set()
        self.assertEqual((await asyncio.wait_for(task, 4))["running_version"], "1.0.1")
        self.assertIs(old.cleanup_task, cleanup)
        self.assertTrue(cleanup.done())
        self.manager.packages["sibling"].gate.check()

    async def test_start_failure_waits_for_actual_cleanup_then_falls_back(self):
        first, first_metadata = await self.make_dlc("fragile")
        sibling, _ = await self.make_dlc("sibling")
        await self.manager.replace(first)
        await self.manager.replace(sibling)
        old_epoch = self.manager.packages["fragile"].epoch
        self.registry.remove(first_metadata)
        successor, _ = await self.make_dlc("fragile", "1.0.1", 2)
        entered, release = asyncio.Event(), asyncio.Event()
        original_prepare = self.manager._prepare

        async def prepare(installation, **kwargs):
            package = await original_prepare(installation, **kwargs)
            if package.bundle.release_doc["release"] == "1.0.1":
                delegate = package.instances["fragile/demo"]._delegate

                async def fail_start():
                    entered.set()
                    raise RuntimeError("injected start failure")

                async def retained_stop():
                    await release.wait()

                delegate.start = fail_start
                delegate.stop = retained_stop
            return package

        self.manager._prepare = prepare
        task = asyncio.create_task(self.manager.replace(successor))
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.sleep(0.03)
        self.assertFalse(task.done())
        self.manager.packages["sibling"].gate.check()
        self.assertFalse(self.manager.packages["fragile"].fallback)
        release.set()
        result = await asyncio.wait_for(task, 4)
        self.assertEqual(result["runtime_state"], "fallback")
        self.assertEqual(result["running_version"], "1.0.0")
        self.assertGreater(self.manager.packages["fragile"].epoch, old_epoch)
        self.manager.packages["sibling"].gate.check()
        head = await self.manager.repository.head("fragile", self.key_id, "stable")
        self.assertEqual(head["release_sequence"], 2)

    async def test_core_start_failure_fences_owner_and_retains_actual_cleanup(self):
        cache = ArtifactCache(self.data)
        files = runtime_files()
        bundle = cache.store.capture(
            self.installation, _release(files, package_id="ygl_core"), kind="core"
        )
        generation = CoreGeneration(object(), self.context, self.data, bundle)
        generation.cache = cache
        self.assertEqual((await self.generation.terminate(timeout=3))["state"], "quiet")
        self.assertEqual((await generation.acquire())["state"], "acquired")
        namespace = bundle.mount_core()
        module = importlib.import_module(namespace + ".adapters.astrbot.runtime")
        core_module = importlib.import_module(namespace + ".services.core_runtime")
        effect = importlib.import_module(namespace + ".services.runtime_effect_gate")
        release = asyncio.Event()

        class FailedCore(core_module.CoreRuntime):
            async def start(inner):
                raise RuntimeError("injected Core.start failure")

        class RetainedFont(_Font):
            async def close(inner):
                await release.wait()
                await super().close()

        runtime = module.AstrBotRuntime(
            self.context,
            plugin_root=bundle.resource_root,
            data_dir=self.data,
            font_manager_factory=RetainedFont,
            bundled_selection=None,
            core_factory=FailedCore,
            http_transport_factory=_Transport,
        )
        generation.attach(runtime)
        self.generation, self.runtime, self.namespace = generation, runtime, namespace
        try:
            with self.assertRaisesRegex(RuntimeError, "injected Core.start failure"):
                await runtime.initialize()
            self.assertFalse(runtime.ready)
            self.assertIsNone(getattr(runtime, "_control_endpoint", None))
            with self.assertRaises(effect.EffectRejected):
                runtime._hot_gate.check()
            task = generation.cleanup_task
            self.assertIsNotNone(task)
            self.assertFalse(task.done())
            rows = await runtime._core.database.executor.run_internal_transaction(
                lambda unit: [
                    tuple(row)
                    for row in unit.connection.execute(
                        "SELECT phase,error_code FROM runtime_transitions WHERE scope='core' ORDER BY updated_at"
                    ).fetchall()
                ]
            )
            self.assertIn(("FAILED", "core_start_failed"), rows)
            self.assertIs(generation.cleanup_task, task)
        finally:
            release.set()

    async def test_core_successor_reconnects_packages_with_fresh_private_graph(self):
        first, _ = await self.make_dlc("alpha")
        second, _ = await self.make_dlc("beta")
        await self.manager.replace(first)
        await self.manager.replace(second)
        import sys

        initial_finders = len(sys.meta_path)
        for _ in range(10):
            old_namespace = self.namespace
            old_token = self.generation.receipt["owner_token"]
            old_epoch = self.generation.receipt["core_epoch"]
            successor_cache = ArtifactCache(self.data)
            files = runtime_files()
            successor_bundle = successor_cache.store.capture(
                self.installation, _release(files, package_id="ygl_core"), kind="core"
            )
            successor = CoreGeneration(
                object(), self.context, self.data, successor_bundle
            )
            successor.cache = successor_cache
            self.assertEqual((await successor.acquire())["state"], "parked")
            self.assertEqual(
                (await self.generation.terminate(timeout=3))["state"], "quiet"
            )
            receipt = await successor.acquire()
            self.assertEqual(receipt["state"], "acquired")
            self.assertNotEqual(receipt["owner_token"], old_token)
            self.assertGreater(receipt["core_epoch"], old_epoch)
            namespace = successor_bundle.mount_core()
            module = importlib.import_module(namespace + ".adapters.astrbot.runtime")
            runtime = module.AstrBotRuntime(
                self.context,
                plugin_root=successor_bundle.resource_root,
                data_dir=self.data,
                font_manager_factory=_Font,
                bundled_selection=None,
                plain_factory=lambda value: value,
                chain_factory=lambda value: list(value),
                http_transport_factory=_Transport,
            )
            successor.attach(runtime)
            self.generation, self.runtime, self.namespace = (
                successor,
                runtime,
                namespace,
            )
            await runtime.initialize()
            self.manager = runtime._independent_packages
            for installation in self.manager.discovery.scan():
                await self.manager.replace(installation)
            self.assertEqual(set(self.manager.packages), {"alpha", "beta"})
            self.assertNotEqual(namespace, old_namespace)
            for package in self.manager.packages.values():
                package.gate.check()
                self.assertEqual(
                    package.gate.identity.core_token, receipt["owner_token"]
                )
            self.assertEqual(len(sys.meta_path), initial_finders)
            self.assertFalse(
                any(name.startswith(old_namespace + ".") for name in sys.modules)
            )

    async def test_cached_recovery_keeps_head_and_current_authority(self):
        first, first_metadata = await self.make_dlc("recover")
        await self.manager.replace(first)
        first_package = self.manager.packages["recover"]
        raw = (first.root / "release.json").read_bytes()
        signature = json.loads((first.root / "release.sig.json").read_bytes())
        projection = json.loads(first_package.receipt.permission_projection_json)
        digest = first_package.receipt.artifact_digest
        self.registry.remove(first_metadata)
        second, second_metadata = await self.make_dlc("recover", "1.0.1", 2)
        await self.manager.replace(second)
        with self.assertRaisesRegex(Exception, "release_downgrade"):
            await self.manager.trust.evaluate(
                raw,
                signature,
                host_plugin_id={"author": "Synthetic Author", "name": "recover"},
                permission_projection=projection,
            )
        receipt = await self.manager.trust.evaluate_cached(
            raw,
            signature,
            host_plugin_id={"author": "Synthetic Author", "name": "recover"},
            permission_projection=projection,
            pinned_digest=digest,
        )
        self.assertEqual(receipt.artifact_digest, digest)
        self.registry.remove(second_metadata)
        broken, _ = await self.make_dlc("recover", "1.0.2", 3)
        (broken.root / "packages/business/module.py").write_bytes(b"tampered")
        status = await self.manager.replace(broken)
        self.assertEqual(status["state"], "active")
        self.assertEqual(status["runtime_state"], "fallback")
        self.assertEqual(status["installed_version"], "1.0.2")
        self.assertEqual(status["running_version"], "1.0.1")
        head = await self.manager.repository.head("recover", self.key_id, "stable")
        self.assertEqual(head["release_sequence"], 2)
        publisher = await self.manager.repository.publisher(self.key_id)
        await self.manager.repository.revoke_publisher(
            self.key_id,
            expected_revision=publisher["revision"],
            actor_ref="offline authenticated test",
        )
        with self.assertRaisesRegex(Exception, "publisher_untrusted"):
            await self.manager.trust.evaluate_cached(
                raw,
                signature,
                host_plugin_id={"author": "Synthetic Author", "name": "recover"},
                permission_projection=projection,
                pinned_digest=digest,
            )

    async def test_core_fence_stops_unbound_http_and_files_before_sink(self):
        from unittest.mock import AsyncMock

        transport = self.runtime._http_transport
        transport.request = AsyncMock(return_value=None)
        http_module = importlib.import_module(self.namespace + ".infrastructure.http")
        http = http_module.SourceHttpService((), transport)
        storage = importlib.import_module(
            self.namespace + ".yomihime_game_link_sdk.storage"
        )
        await http._invoke_transport(object())
        self.assertEqual(transport.request.await_count, 1)
        transport.request.reset_mock()
        effect = importlib.import_module(
            self.namespace + ".services.runtime_effect_gate"
        )
        self.generation.sync_fence()
        with self.assertRaises(effect.EffectRejected) as denied:
            await http._invoke_transport(object())
        self.assertEqual(denied.exception.code, "stale_lease")
        with self.assertRaises(effect.EffectRejected) as denied:
            await self.runtime._core.file_store.stage(
                "fenced", b"never written", storage.OwnerScope.public()
            )
        self.assertEqual(transport.request.await_count, 0)
        self.assertEqual(tuple(self.runtime._core.file_store._staging.iterdir()), ())

    async def test_pending_core_cleanup_keeps_same_future_and_blocks_takeover(self):
        release = asyncio.Event()
        original_close = self.runtime._managed_font.close

        async def resistant_close():
            await release.wait()
            await original_close()

        self.runtime._managed_font.close = resistant_close
        result = await self.generation.terminate(timeout=0.01)
        self.assertEqual(result["state"], "parked")
        original_future = self.generation.cleanup_task
        other_cache = ArtifactCache(self.data)
        other_bundle = other_cache.store.capture(
            self.installation,
            _release(runtime_files(), package_id="ygl_core"),
            kind="core",
        )
        other = CoreGeneration(object(), self.context, self.data, other_bundle)
        self.assertEqual((await other.acquire())["state"], "parked")
        self.assertIs(self.generation.cleanup_task, original_future)
        release.set()
        await asyncio.wait_for(asyncio.shield(original_future), 3)
        self.assertTrue(self.generation.cleanup_complete)
        # No second terminate is needed for eventual owner/OS lock release.
        await asyncio.sleep(0)
        self.assertNotIn(str(self.data.resolve()), self.generation.kernel.scopes)
        other_bundle.release()
        other_cache.close()

    async def test_ten_package_generations_leave_no_retired_finder_or_gate_growth(self):
        import sys

        initial_finders = len(sys.meta_path)
        initial_gates = len(self.generation.kernel._record(self.generation.scope).gates)
        old_metadata = None
        last = None
        for sequence in range(1, 11):
            if old_metadata is not None:
                self.registry.remove(old_metadata)
            installation, old_metadata = await self.make_dlc(
                "cycles", f"1.0.{sequence}", sequence
            )
            await self.manager.replace(installation)
            last = self.manager.packages["cycles"]
            self.assertEqual(len(sys.meta_path), initial_finders)
            self.assertLessEqual(
                len(self.generation.kernel._record(self.generation.scope).gates),
                initial_gates + 1,
            )
        self.assertEqual(last.epoch, 10)
        self.assertEqual(
            self.runtime._core.registry.snapshot()
            .modules["cycles/demo"]
            .manifest.module_version,
            "1.0.0",
        )
