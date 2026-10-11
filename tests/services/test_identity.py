"""Trusted ingress principal provisioning against the real identity repository."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from ygl_test_subject.core.contracts.services import Principal
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories_identity import (
    SQLiteIdentityRepository,
)
from ygl_test_subject.services.identity import TrustedIngressPrincipalProvisioner


class _ConcurrentFirstLookup:
    """Force two first-insertion callers to observe the same missing mapping."""

    def __init__(self, delegate: SQLiteIdentityRepository) -> None:
        self.delegate = delegate
        self._first_reads = 0
        self._both_read = asyncio.Event()

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)

    async def find_principal(self, namespace: str, external_user_id: str):
        principal = await self.delegate.find_principal(namespace, external_user_id)
        if principal is None and self._first_reads < 2:
            self._first_reads += 1
            if self._first_reads == 2:
                self._both_read.set()
            await self._both_read.wait()
        return principal


class TrustedIngressPrincipalProvisionerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="ygl-principal-provisioner-")
        self.database = SQLiteDatabase(Path(self.temp.name) / "identity.sqlite3")
        self.repository = SQLiteIdentityRepository(self.database)
        self.namespace = "astrbot_plugin_yomihime_game_link"

    async def asyncTearDown(self) -> None:
        await self.database.executor.close(timeout=2)
        self.temp.cleanup()

    async def test_existing_mapping_is_returned_without_replacement(self) -> None:
        existing = Principal("principal-original", self.namespace, "adapter-a:user-1")
        await self.repository.save_principal(existing)
        provisioner = TrustedIngressPrincipalProvisioner(
            self.repository, identity_namespace=self.namespace
        )

        principal = await provisioner.ensure("adapter-a:user-1")

        self.assertEqual(principal, existing)
        self.assertEqual(
            await self.repository.find_principal(self.namespace, "adapter-a:user-1"),
            existing,
        )

    async def test_concurrent_first_ingress_uses_one_durable_principal(self) -> None:
        repository = _ConcurrentFirstLookup(self.repository)
        provisioner = TrustedIngressPrincipalProvisioner(
            repository, identity_namespace=self.namespace
        )

        principals = await asyncio.gather(
            *(provisioner.ensure("adapter-a:same-user") for _ in range(8))
        )

        self.assertEqual(len({principal.principal_id for principal in principals}), 1)
        self.assertTrue(
            all(
                principal.identity_namespace == self.namespace
                and principal.external_user_id == "adapter-a:same-user"
                for principal in principals
            )
        )
        row_count = await self.database.executor.run_read(
            lambda unit: unit.connection.execute(
                "SELECT COUNT(*) FROM principals WHERE identity_namespace = ? AND external_user_id = ?",
                (self.namespace, "adapter-a:same-user"),
            ).fetchone()[0]
        )
        self.assertEqual(row_count, 1)

    async def test_actor_namespace_is_part_of_the_unique_key(self) -> None:
        provisioner = TrustedIngressPrincipalProvisioner(
            self.repository, identity_namespace=self.namespace
        )

        first, second = await asyncio.gather(
            provisioner.ensure("adapter-a:42"), provisioner.ensure("adapter-b:42")
        )

        self.assertNotEqual(first.principal_id, second.principal_id)
        self.assertEqual(first.external_user_id, "adapter-a:42")
        self.assertEqual(second.external_user_id, "adapter-b:42")


if __name__ == "__main__":
    unittest.main()
