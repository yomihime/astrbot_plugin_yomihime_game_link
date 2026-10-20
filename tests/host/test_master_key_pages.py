"""Real pinned-Host route checks with an isolated synthetic plugin runtime."""

import json
import os
import time
import unittest
from pathlib import Path

from ygl_test_subject.core.contracts.administration import (
    AdminAuthorizationDenied,
    AdminOperation,
)
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.ports import SecretOwner

from tests.host import test_admin_pages as fixture
from yomihime_game_link_sdk.services import ConfigTarget


class MasterKeyPagesTests(unittest.IsolatedAsyncioTestCase):
    fixture_parent = Path(
        os.environ.get("YGL_MANAGED_KEY_TEST_ROOT")
        or (Path.home() / "AppData" / "Local" if os.name == "nt" else Path.cwd())
    )
    asyncTearDown = fixture.AdminPagesTests.asyncTearDown
    call = fixture.AdminPagesTests.call

    async def asyncSetUp(self):
        old = os.environ.pop("YGL_SECRET_KEY", None)
        self.addCleanup(
            lambda: os.environ.pop("YGL_SECRET_KEY", None)
            if old is None
            else os.environ.__setitem__("YGL_SECRET_KEY", old)
        )
        self.assertTrue(self.fixture_parent.is_dir())
        await fixture.AdminPagesTests.asyncSetUp(self)

    async def test_global_status_uses_independent_proof_and_exposes_no_key(self):
        core = self.runtime.core_runtime
        self.assertIsNotNone(core.admin_operations.managed_keys)
        source = self.runtime._key_admin_source
        self.assertIsNotNone(source)
        self.assertIsNot(source, self.runtime._credential_admin_source)
        self.assertIsNot(source, self.runtime._admin_source)
        response = await self.call("master-key-status", {})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        data = response.json()["data"]
        self.assertTrue(data["ready"], data)
        self.assertEqual(data["source"], "managed")
        self.assertEqual(
            set(data),
            {
                "state",
                "reason_code",
                "ready",
                "source",
                "generation",
                "revision",
                "inventory_digest",
                "unknown_count",
                "impact",
                "storage_security",
                "environment_present",
                "environment_matches",
                "recovery_action",
            },
        )
        self.assertNotIn("key_id", response.text)
        self.assertNotIn("encoded_key", response.text)
        self.assertNotIn("secret_token", response.text)
        credential_source = self.runtime._credential_admin_source
        with self.assertRaises(AdminAuthorizationDenied):
            credential_source.issue(
                subject="synthetic",
                request=object(),
                expiry=time.time() + 5,
                operations={AdminOperation.READ_CONFIG},
                resources=core.admin_operations.master_key_resources(),
                live=lambda: True,
            )
        self.assertEqual(source._proofs, {})

    async def test_global_route_rejects_wrong_request_auth_and_old_module_source(self):
        status = (await self.call("master-key-status", {})).json()["data"]
        self.assertEqual(
            (
                await self.call("master-key-status", {"module_id": "ff14/ff14"})
            ).status_code,
            400,
        )
        self.assertEqual(
            (await self.call("master-key-status", {}, headers={})).status_code,
            401,
        )
        self.assertEqual(
            (
                await self.call(
                    "master-key-status",
                    {},
                    headers={**self.headers, "Origin": "https://other.test"},
                )
            ).status_code,
            403,
        )
        cas = {
            "expected_revision": status["revision"],
            "expected_generation": status["generation"],
            "inventory_digest": status["inventory_digest"],
        }
        self.assertEqual((await self.call("master-key-reset", cas)).status_code, 400)
        self.assertEqual(
            (
                await self.call(
                    "master-key-reset",
                    {**cas, "confirmation": "RESET ENCRYPTED CREDENTIALS", "extra": 1},
                )
            ).status_code,
            400,
        )
        self.runtime._credential_admin_source.close()
        response = await self.call("master-key-status", {})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"], status)
        self.assertEqual(self.runtime._key_admin_source._proofs, {})

    async def test_fresh_host_save_module_read_and_restarted_host_read(self):
        first = (await self.call("master-key-status", {})).json()["data"]
        self.assertTrue(first["ready"], first)
        self.assertEqual(first["source"], "managed")
        self.assertTrue(
            (await self.call("credential-readiness", {})).json()["data"]["ready"]
        )
        target = validate_contract(
            ConfigTarget("astrbot_plugin_yomihime_game_link", "ff14/ff14")
        )
        before = (await self.call("credential-status", {})).json()["data"]
        pair = {"client_id": "synthetic-id", "client_secret": "synthetic-secret"}
        saved = await self.call(
            "credential-update",
            {
                "module_id": "ff14/ff14",
                "expected_revision": before["ff14/ff14"]["revision"],
                "updates": [
                    {"field": "credential_fflogs_cn", "mode": "replace", "value": pair}
                ],
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertNotIn("synthetic-secret", saved.text)

        async def module_read(core):
            snapshot = await core.config_repository.current(target)
            metadata = next(
                row
                for row in snapshot.secret_metadata
                if row.field == "credential_fflogs_cn"
            )
            ref = metadata.secret_ref
            self.assertIsNotNone(ref)
            payload = await core.secret_store.read(
                ref,
                owner=SecretOwner(
                    target.principal_id,
                    target.module_id,
                    "credential_fflogs_cn",
                    ref.operation_id,
                ),
            )
            self.assertEqual(
                json.loads(payload)["client_secret"], pair["client_secret"]
            )

        await module_read(self.runtime.core_runtime)
        self.pages.close()
        await self.runtime.terminate()
        self.transport = fixture._IdleTransport()
        self.runtime = fixture.AstrBotRuntime(
            self.context,
            font_manager_factory=fixture.OfflineManagedFont,
            plugin_root=Path(self.temp.name) / "plugin",
            data_dir=Path(self.temp.name) / "data",
            config={},
            http_transport_factory=lambda: self.transport,
            plain_factory=fixture._Plain,
            chain_factory=fixture._MessageChain,
        )
        await self.runtime.initialize()
        self.pages = fixture.AdminPages(self.context, self.runtime)
        self.pages.register()
        restarted = (await self.call("master-key-status", {})).json()["data"]
        self.assertTrue(restarted["ready"], restarted)
        self.assertEqual(restarted["generation"], first["generation"])
        await module_read(self.runtime.core_runtime)


if __name__ == "__main__":
    unittest.main()
