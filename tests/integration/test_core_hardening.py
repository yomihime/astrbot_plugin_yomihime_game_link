"""R vertical integration against the installed SDK and one real CoreRuntime."""

from __future__ import annotations

import unittest

from tests.fixtures.core_hardening import InstalledCoreHardeningWorkspace


class CoreHardeningInstalledSampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workspace = InstalledCoreHardeningWorkspace()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.workspace.close()

    def test_installed_sample_activation_dependency_config_and_sdk_identity(
        self,
    ) -> None:
        result = self.workspace.run("activation")

        self.assertEqual(set(result["package_ids"]), {"empty_module", "offline_sample"})
        self.assertEqual(result["status_command_sent"], "sent")
        self.assertEqual(result["tool_status"], "tool_result")
        self.assertEqual(result["tool_facts"], {"ready": True, "sample": "offline"})
        self.assertEqual(result["independent_before"], "available")
        self.assertEqual(result["dependent_after"], "unavailable")
        self.assertEqual(result["independent_after"], "available")
        self.assertTrue(result["dependency_denied"])
        self.assertTrue(result["dependency_restored"])
        self.assertTrue(result["required_config_denied"])
        self.assertEqual(result["required_config_before"], "unavailable")
        self.assertEqual(result["required_config_after"], "available")
        self.assertTrue(result["configured_admin_available"])
        self.assertIsNone(result["configured_admin_reason"])
        self.assertTrue(result["help_includes_configured"])
        self.assertGreaterEqual(result["send_calls"], 3)

    def test_gateway_account_subscription_and_three_output_routes(self) -> None:
        result = self.workspace.run("subscriptions")
        self.assertTrue(result["subscription_gate_admitted"])

        self.assertEqual(result["alice_principal"], "principal-alice")
        self.assertEqual(result["bob_principal"], "principal-bob")
        self.assertTrue(result["account_binding_isolation"])
        self.assertTrue(result["account_unbind_empty"])
        self.assertTrue(result["cross_owner_account_unbind_denied"])
        self.assertTrue(result["stale_account_revision_denied"])
        self.assertTrue(result["cross_owner_subscription_cancel_denied"])
        self.assertTrue(result["stale_subscription_revision_denied"])
        self.assertTrue(result["no_grant_private_command_denied"])
        self.assertTrue(result["no_grant_private_subscription_denied"])
        self.assertTrue(result["subscription_list_calls_and_owner_rows_checked"])
        self.assertTrue(result["account_tool_denied"])
        self.assertTrue(result["subscription_tool_denied"])
        self.assertNotEqual(
            result["shared_public_observation"], result["private_observation"]
        )
        self.assertTrue(result["grant_revoked"])
        with self.subTest("digest event waits for its configured UTC boundary"):
            self.assertFalse(result["digest_sent_before_due"])
            self.assertEqual(result["early_digest_targets"], [])
        self.assertEqual(result["revoke_private_event_state"], "cancelled")
        self.assertEqual(result["private_event_state"], "cancelled")
        self.assertEqual(result["alice_public_event_state"], "sent")
        self.assertEqual(result["bob_digest_event_state"], "pending")
        self.assertEqual(result["instant_send_count"], 1)
        self.assertEqual(result["instant_send_targets"], ["dm-alice"])
        self.assertEqual(result["digest_send_count"], 1)
        self.assertEqual(result["digest_target"], "dm-bob")
        self.assertFalse(result["secret_leaked"])

    def test_disable_reenable_and_reopen_preserve_authority_and_business_data(
        self,
    ) -> None:
        result = self.workspace.run("reopen")
        self.assertTrue(result["subscription_gate_admitted"])
        self.assertTrue(result["subscription_gate_after_reopen"])

        self.assertTrue(result["stale_admin_denied"])
        self.assertTrue(result["epoch_advanced"])
        self.assertEqual(result["old_epoch_rejected"], "module_unavailable")
        self.assertEqual(result["credential_generation_after_reopen"], 2)
        self.assertTrue(result["module_enabled_after_reopen"])
        self.assertEqual(result["config_region_after_reopen"], "configured")
        self.assertTrue(result["binding_persisted"])
        self.assertTrue(result["subscription_persisted"])
        self.assertEqual(
            result["old_issuer_rejected_after_reopen"], "module_unavailable"
        )
        self.assertEqual(result["status_after_reopen"], "sent")

    def test_zero_module_runtime_closes_owned_resources(self) -> None:
        result = self.workspace.run("empty")

        self.assertTrue(result["started"])
        self.assertTrue(result["closed"])
        self.assertEqual(result["module_count"], 0)
        self.assertEqual(result["owned_threads_after_close"], 0)
        self.assertEqual(result["owned_tasks_after_close"], 0)


if __name__ == "__main__":
    unittest.main()
