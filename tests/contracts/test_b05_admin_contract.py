"""Contract probes for the host-independent B05 administration surface."""

import inspect
import unittest
from typing import get_type_hints

from ygl_test_subject.api.administration import (
    ADMIN_CONTRACT_REVISION,
    ADMIN_ERROR_MESSAGES,
    AdminAuthorizationContext,
    AdminAuthorizationDenied,
    AdminAuthorizationGrant,
    AdminError,
    AdminOperation,
    AdminOperations,
    ConfigSummary,
    ModuleAdminSnapshot,
    ModuleHealth,
    ModuleLifecycle,
    ModuleStatus,
)
from ygl_test_subject.api.services import ConfigPatch
from ygl_test_subject.api.version import (
    COMPATIBLE_CONTRACT_VERSIONS,
    CONTRACT_REVISION,
    CONTRACT_VERSION,
)
from ygl_test_subject.core.ports import AdminAuthorizationPort


class B05AdminContractTests(unittest.TestCase):
    def test_admin_revision_is_additive_to_b04_c13(self):
        self.assertEqual(ADMIN_CONTRACT_REVISION, "B05-H-CORE-01")
        self.assertEqual((CONTRACT_VERSION, CONTRACT_REVISION), ("1.3.0", "FF14-W1-P1"))
        self.assertIn("1.1.0", COMPATIBLE_CONTRACT_VERSIONS)

    def test_snapshot_fields_defaults_and_redaction_contract(self):
        self.assertEqual(
            tuple(ModuleStatus.__dataclass_fields__),
            (
                "module_id",
                "enabled",
                "lifecycle",
                "health",
                "epoch",
                "registry_revision",
                "reason_code",
            ),
        )
        self.assertIsNone(ModuleStatus.__dataclass_fields__["reason_code"].default)
        self.assertEqual(
            (ModuleLifecycle.DISCOVERED.value, ModuleHealth.NEEDS_CONFIG.value),
            ("discovered", "needs_config"),
        )
        self.assertEqual(
            ModuleAdminSnapshot.__dataclass_fields__["capabilities"].default, ()
        )
        self.assertEqual(
            ModuleAdminSnapshot.__dataclass_fields__["data_counts"].default, ()
        )
        self.assertIsNone(ModuleAdminSnapshot.__dataclass_fields__["error"].default)
        self.assertEqual(
            ConfigSummary.__dataclass_fields__["sensitive_fields"].default, ()
        )

        snapshot = ConfigSummary("pkg/mod", 3, {"api_token": "configured"})
        self.assertEqual(snapshot.fields["api_token"], "configured")
        with self.assertRaises((TypeError, AttributeError)):
            snapshot.fields["api_token"] = "raw-secret"
        with self.assertRaises(ValueError):
            ConfigSummary("pkg/mod", 3, {"api_token": "raw-secret"})

    def test_admin_error_and_reason_text_are_closed_catalogs(self):
        public_message = ADMIN_ERROR_MESSAGES["operation_failed"]
        self.assertEqual(
            AdminError("operation_failed", public_message).message, public_message
        )
        raw_detail = "hunter2-unknown-private-value"
        with self.assertRaises(ValueError) as rejected:
            AdminError("operation_failed", raw_detail)
        self.assertNotIn(raw_detail, str(rejected.exception))
        with self.assertRaises(ValueError):
            AdminError("unrecognized-secret", raw_detail)
        with self.assertRaises(ValueError):
            ModuleStatus(
                "pkg/mod",
                True,
                ModuleLifecycle.FAILED,
                ModuleHealth.MODULE_ERROR,
                1,
                1,
                raw_detail,
            )

    def test_operations_signatures_and_fail_closed_defaults(self):
        expected = {
            "list_modules": ("self", "invocation", "authorization"),
            "module_snapshot": ("self", "invocation", "module_id", "authorization"),
            "set_enabled": (
                "self",
                "invocation",
                "module_id",
                "enabled",
                "expected_registry_revision",
                "authorization",
            ),
            "update_config": (
                "self",
                "invocation",
                "module_id",
                "patch",
                "authorization",
            ),
        }
        for name, parameter_names in expected.items():
            method = getattr(AdminOperations, name)
            signature = inspect.signature(method)
            self.assertEqual(tuple(signature.parameters), parameter_names)
            self.assertIsNone(signature.parameters["authorization"].default)
            self.assertIn("InvocationView", str(get_type_hints(method)["invocation"]))
        self.assertIs(
            ConfigPatch, get_type_hints(AdminOperations.update_config)["patch"]
        )
        self.assertTrue(inspect.iscoroutinefunction(AdminOperations.list_modules))
        self.assertTrue(inspect.iscoroutinefunction(AdminOperations.set_enabled))

    def test_core_authorizer_contract_and_denial_are_stable(self):
        signature = inspect.signature(AdminAuthorizationPort.authorize)
        self.assertEqual(
            tuple(signature.parameters),
            ("self", "operation", "invocation", "context"),
        )
        self.assertIs(
            signature.parameters["invocation"].default, inspect.Parameter.empty
        )
        self.assertIs(signature.parameters["context"].default, inspect.Parameter.empty)
        self.assertIs(
            get_type_hints(AdminAuthorizationPort.authorize)["return"],
            AdminAuthorizationGrant,
        )
        grant = AdminAuthorizationGrant(AdminOperation.SET_ENABLED, 1)
        self.assertEqual(grant.generation, 1)
        with self.assertRaises(ValueError):
            AdminAuthorizationGrant(AdminOperation.SET_ENABLED, 0)
        self.assertEqual(AdminAuthorizationDenied.code, "admin_authorization_denied")
        self.assertNotIn("credential", str(AdminAuthorizationDenied()).lower())
        self.assertTrue(hasattr(AdminAuthorizationContext, "session_id"))


if __name__ == "__main__":
    unittest.main()
