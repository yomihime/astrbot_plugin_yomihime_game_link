"""Offline public-registry and real queued-endpoint contract tests."""

import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from ygl_test_subject.services.extension_discovery import ExtensionDiscovery
from ygl_test_subject.services.extension_transition import ExtensionControlEndpoint


class LogicalArtifactPathsTest(unittest.TestCase):
    def test_manifest_business_root_remains_posix_on_windows(self):
        from pathlib import PureWindowsPath
        from unittest.mock import patch

        from ygl_test_subject.services import external_packages

        logical = "packages/unknown/manifest.json"
        self.assertEqual(str(PureWindowsPath(logical).parent), "packages\\unknown")
        with patch.object(external_packages, "Path", PureWindowsPath):
            self.assertEqual(
                external_packages._manifest_business_root(logical), "packages/unknown"
            )


class IndependentDiscoveryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.registry = []
        self.context = SimpleNamespace(get_all_stars=lambda: self.registry)
        self.discovery = ExtensionDiscovery(self.context)
        self.started, self.fenced, self.removed = [], [], []
        self.allow = asyncio.Event()
        self.allow.set()

        async def activate(installation):
            self.started.append(installation.package_id)
            await self.allow.wait()
            return {
                "state": "active",
                "package_epoch": 1,
                "installed_version": "1.0.0",
                "running_version": "1.0.0",
            }

        async def withdraw(instance):
            self.removed.append(instance)

        self.endpoint = ExtensionControlEndpoint(
            self.discovery,
            core_epoch=1,
            activate=activate,
            withdraw=withdraw,
            fence=self.fenced.append,
        )
        self.addAsyncCleanup(self.endpoint.close)

    def install(self, package_id, name=None):
        name = name or package_id
        root = Path(self.temp.name) / name
        root.mkdir()
        (root / "main.py").write_text("")
        raw = json.dumps(
            {
                "schema": "ygl.plugin/1",
                "role": "dlc",
                "package_id": package_id,
                "manifest": "package/manifest.json",
                "control_protocol": {"name": "ygl.control", "major": 1},
            }
        ).encode()
        (root / "ygl-plugin.json").write_bytes(raw)
        instance = object()
        metadata = SimpleNamespace(
            name=name,
            activated=True,
            star_cls=instance,
            root_dir_name=name,
            module=SimpleNamespace(__file__=str(root / "main.py")),
        )
        self.registry.append(metadata)
        return instance, hashlib.sha256(raw).hexdigest(), metadata

    async def connect(self, instance, digest, request_id=None):
        return await self.endpoint.request(
            instance,
            {
                "protocol": "ygl.control/1",
                "op": "connect",
                "request_id": request_id or str(uuid4()),
                "descriptor_digest": digest,
            },
        )

    async def status(self, instance, operation):
        return await self.endpoint.request(
            instance,
            {"protocol": "ygl.control/1", "op": "status", "operation_id": operation},
        )

    async def test_unknown_packages_replay_conflict_and_sibling_isolation(self):
        a, digest, _ = self.install("never_predeclared")
        b, bd, _ = self.install("another_unknown")
        request_id = str(uuid4())
        queued = await self.connect(a, digest, request_id)
        self.assertEqual(queued["state"], "queued")
        replay = await self.connect(a, digest, request_id)
        self.assertEqual(queued["operation_id"], replay["operation_id"])
        conflict = await self.connect(a, "0" * 64, request_id)
        self.assertEqual(conflict["error"]["code"], "request_conflict")
        qb = await self.connect(b, bd)
        await asyncio.sleep(0)
        sa = await self.status(a, queued["operation_id"])
        sb = await self.status(b, qb["operation_id"])
        self.assertEqual(sa["state"], "active")
        self.assertEqual(sb["state"], "active")
        self.assertEqual(self.started.count("never_predeclared"), 1)
        await self.endpoint.request(
            a, {"protocol": "ygl.control/1", "op": "disconnect", "lease": sa["lease"]}
        )
        self.assertEqual(self.fenced, [a])
        self.assertEqual((await self.status(b, qb["operation_id"]))["state"], "active")
        self.assertEqual([x.instance for x in self.discovery.scan()], [b])

    async def test_fresh_retries_share_lease_without_exhausting_capacity(self):
        self.endpoint.capacity = 4
        instance, digest, metadata = self.install("retry")
        leases = set()
        for _ in range(12):
            queued = await self.connect(instance, digest)
            self.assertTrue(queued["ok"])
            await asyncio.sleep(0)
            status = await self.status(instance, queued["operation_id"])
            self.assertEqual(status["state"], "active")
            leases.add(status["lease"])
        self.assertEqual(len(leases), 1)
        self.endpoint.retire_instance(instance)
        self.assertEqual(
            (await self.status(instance, queued["operation_id"]))["state"], "disabled"
        )
        self.registry.remove(metadata)
        successor, successor_digest, _ = self.install("retry", "new")
        queued = await self.connect(successor, successor_digest)
        self.assertTrue(queued["ok"])
        await asyncio.sleep(0)
        status = await self.status(successor, queued["operation_id"])
        self.assertEqual(status["state"], "active")
        await self.endpoint.request(
            instance,
            {
                "protocol": "ygl.control/1",
                "op": "disconnect",
                "lease": next(iter(leases)),
            },
        )
        self.assertNotIn(successor, self.fenced)

    async def test_duplicate_identity_and_stale_instance_fail_closed(self):
        old, digest, old_meta = self.install("duplicate", "old")
        new, _, _ = self.install("duplicate", "new")
        self.assertEqual(self.discovery.scan(), ())
        self.assertEqual(self.discovery.rejections["duplicate"], "owner_conflict")
        self.registry.remove(old_meta)
        self.assertEqual(
            (await self.connect(old, digest))["error"]["code"], "stale_instance"
        )
        self.discovery.tombstone(old)
        self.assertIs(self.discovery.scan()[0].instance, new)

    async def test_prepare_revalidates_registry_and_never_publishes_disabled(self):
        instance, digest, metadata = self.install("pending")
        self.allow.clear()
        queued = await self.connect(instance, digest)
        await asyncio.sleep(0)
        metadata.activated = False
        self.allow.set()
        await asyncio.sleep(0)
        result = await self.status(instance, queued["operation_id"])
        self.assertEqual(result["error"]["code"], "stale_instance")
        self.assertIn(instance, self.fenced)
        self.assertIn(instance, self.removed)

    async def test_disconnect_old_lease_cannot_revoke_new_instance(self):
        old, digest, metadata = self.install("replace")
        qo = await self.connect(old, digest)
        await asyncio.sleep(0)
        old_status = await self.status(old, qo["operation_id"])
        self.registry.remove(metadata)
        new, nd, _ = self.install("replace", "replacement")
        qn = await self.connect(new, nd)
        await asyncio.sleep(0)
        await self.endpoint.request(
            new,
            {
                "protocol": "ygl.control/1",
                "op": "disconnect",
                "lease": old_status["lease"],
            },
        )
        self.assertNotIn(new, self.fenced)
        self.assertEqual(
            (await self.status(new, qn["operation_id"]))["state"], "active"
        )
        await self.endpoint.request(
            old,
            {
                "protocol": "ygl.control/1",
                "op": "disconnect",
                "lease": old_status["lease"],
            },
        )
        self.assertEqual(self.fenced, [old])
        self.assertIs(self.discovery.scan()[0].instance, new)

    async def test_repeated_close_cancels_activation_once_and_retains_withdrawal(self):
        active, digest, _ = self.install("close_active")
        ready = await self.connect(active, digest)
        await asyncio.sleep(0)
        self.assertEqual(
            (await self.status(active, ready["operation_id"]))["state"], "active"
        )
        blocked, digest, _ = self.install("close_preparing")
        self.allow.clear()
        queued = await self.connect(blocked, digest)
        await asyncio.sleep(0)
        activation = self.endpoint._operations[queued["operation_id"]].task
        entered, release = asyncio.Event(), asyncio.Event()
        cancelled = []

        async def withdraw(instance):
            self.assertIs(instance, active)
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
            self.removed.append(instance)

        self.endpoint.withdraw = withdraw
        self.addCleanup(release.set)
        observer = asyncio.create_task(self.endpoint.close())
        await asyncio.wait_for(entered.wait(), 1)
        actual = self.endpoint._operations[ready["operation_id"]].task
        observer.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await observer
        second = asyncio.create_task(self.endpoint.close())
        await asyncio.sleep(0)
        self.assertIs(self.endpoint._operations[ready["operation_id"]].task, actual)
        self.assertFalse(actual.done())
        self.assertTrue(activation.cancelled())
        self.assertEqual(activation.cancelling(), 1)
        self.assertEqual(cancelled, [])
        release.set()
        await asyncio.wait_for(second, 1)
        self.assertEqual(self.removed, [active])

    async def test_cancellation_does_not_cancel_queued_operation(self):
        instance, digest, _ = self.install("cancel")
        self.allow.clear()
        queued = await self.connect(instance, digest)
        await asyncio.sleep(0)
        self.assertEqual(
            (await self.status(instance, queued["operation_id"]))["state"], "preparing"
        )
        self.allow.set()
        await asyncio.sleep(0)
        self.assertEqual(
            (await self.status(instance, queued["operation_id"]))["state"], "active"
        )

    async def test_json_only_and_bounded_pending_queue(self):
        instance, digest, _ = self.install("bound")
        self.endpoint.capacity = 1
        self.allow.clear()
        await self.connect(instance, digest)
        self.assertEqual(
            (await self.connect(instance, digest))["error"]["code"], "busy"
        )
        result = await self.endpoint.request(
            instance, {"protocol": "ygl.control/1", "op": "describe", "obj": object()}
        )
        self.assertFalse(result["ok"])
        self.allow.set()
