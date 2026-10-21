"""Explicit adoption uses only synthetic environment keys and isolated SQLite."""

import base64
import os
import unittest
from unittest.mock import patch

from ygl_test_subject.infrastructure.key_provider import SecretKeyUnavailable
from ygl_test_subject.infrastructure.managed_key_file import ManagedKeyFileError
from ygl_test_subject.infrastructure.managed_key_manager import (
    ManagedKeyConflict,
    ManagedKeyManager,
)

from tests.storage import test_managed_key_manager as fixture


class EnvironmentAdoptionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.ManagedKeyTests.asyncSetUp
    asyncTearDown = fixture.ManagedKeyTests.asyncTearDown
    _insert_secret = fixture.ManagedKeyTests._insert_secret

    async def selected(self, *, payload=True):
        key = bytes(range(32))
        os.environ["YGL_SECRET_KEY"] = base64.b64encode(key).decode()
        if payload:
            self._insert_secret(key)
        status = await self.manager.startup()
        self.assertTrue(status["ready"], status)
        return key, status

    @staticmethod
    def cas(status):
        return {
            "expected_revision": status["revision"],
            "expected_generation": status["generation"],
            "inventory_digest": status["inventory_digest"],
        }

    async def test_preserves_key_payload_generation_and_restart_without_environment(
        self,
    ):
        key, before = await self.selected()
        blob = self.root / "secrets" / "secret_synthetic_01.blob"
        original = blob.read_bytes()
        other = ManagedKeyManager(
            self.database, self.root, self.root / "secrets", files=self.files
        )
        await other.startup()
        after = await self.manager.adopt_environment(**self.cas(before))
        self.assertTrue(after["ready"], after)
        self.assertEqual(after["source"], "managed")
        self.assertTrue(after["environment_present"])
        self.assertTrue(after["environment_matches"])
        self.assertEqual(after["generation"], before["generation"])
        self.assertEqual(after["revision"], before["revision"] + 1)
        self.assertEqual(blob.read_bytes(), original)
        self.assertEqual(set(self.files.saved.values()), {key})
        with self.assertRaises(SecretKeyUnavailable):
            other.get_key()
        del os.environ["YGL_SECRET_KEY"]
        restarted = ManagedKeyManager(
            self.database, self.root, self.root / "secrets", files=self.files
        )
        self.assertTrue((await restarted.startup())["ready"])
        self.assertEqual(restarted.get_key(), key)

    async def test_interrupted_durable_candidate_is_reused_by_explicit_adoption(self):
        key, before = await self.selected()
        self.files.confirm_fails = True
        with self.assertRaises(ManagedKeyFileError):
            await self.manager.adopt_environment(**self.cas(before))
        saved = dict(self.files.saved)
        restarted = ManagedKeyManager(
            self.database, self.root, self.root / "secrets", files=self.files
        )
        blocked = await restarted.startup()
        self.assertFalse(blocked["ready"])
        self.assertEqual(blocked["recovery_action"], "adopt_environment")
        self.assertEqual(self.files.saved, saved)
        with self.assertRaises(ManagedKeyConflict):
            await restarted.retry(**self.cas(blocked))
        with self.assertRaises(ManagedKeyConflict):
            await restarted.restore(base64.b64encode(key).decode(), **self.cas(blocked))
        self.files.confirm_fails = False
        self.assertTrue(
            (await restarted.adopt_environment(**self.cas(blocked)))["ready"]
        )
        self.assertEqual(self.files.saved, saved)

    async def test_partial_candidate_keeps_purpose_and_never_changes_original_key(self):
        key, before = await self.selected()

        def interrupt(identifier, _key):
            self.files.saved[identifier] = b"partial"
            raise ManagedKeyFileError("key_file_invalid")

        with patch.object(self.files, "create", side_effect=interrupt):
            with self.assertRaises(ManagedKeyFileError):
                await self.manager.adopt_environment(**self.cas(before))
        failed = dict(self.files.saved)
        self.assertTrue(
            (
                await self.manager.adopt_environment(
                    **self.cas(await self.manager.status())
                )
            )["ready"]
        )
        self.assertTrue(failed.items() <= self.files.saved.items())
        self.assertEqual(
            [value for value in self.files.saved.values() if len(value) == 32], [key]
        )

    async def test_stale_cas_conflict_and_invalid_environment_never_write(self):
        _, before = await self.selected()
        with self.assertRaises(ManagedKeyConflict):
            await self.manager.adopt_environment(
                **{**self.cas(before), "expected_revision": before["revision"] - 1}
            )
        for value in ("", "invalid", base64.b64encode(b"x" * 32).decode()):
            os.environ["YGL_SECRET_KEY"] = value
            with self.assertRaises(ManagedKeyConflict):
                await self.manager.adopt_environment(**self.cas(before))
            self.assertEqual(self.files.saved, {})

    async def test_rechecks_fence_and_environment_before_commit(self):
        key, before = await self.selected()
        calls = []

        def fence(_unit):
            calls.append(True)
            if len(calls) == 4:
                raise PermissionError("synthetic revoked grant")

        with self.assertRaises(PermissionError):
            await self.manager.adopt_environment(**self.cas(before), fence=fence)
        self.assertEqual((await self.manager.status())["source"], "environment")
        self.assertEqual(set(self.files.saved.values()), {key})
        self.assertTrue(
            (
                await self.manager.adopt_environment(
                    **self.cas(await self.manager.status())
                )
            )["ready"]
        )

    async def test_managed_idempotence_checks_cas_and_conflicting_environment(self):
        _, before = await self.selected(payload=False)
        after = await self.manager.adopt_environment(**self.cas(before))
        self.assertEqual(await self.manager.adopt_environment(**self.cas(after)), after)
        with self.assertRaises(ManagedKeyConflict):
            await self.manager.adopt_environment(**self.cas(before))
        os.environ["YGL_SECRET_KEY"] = base64.b64encode(b"x" * 32).decode()
        with self.assertRaises(ManagedKeyConflict):
            await self.manager.adopt_environment(**self.cas(after))
