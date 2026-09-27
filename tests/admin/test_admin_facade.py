"""Local contract tests for the host-independent admin facade."""

from __future__ import annotations

import unittest
from typing import cast

from ygl_test_subject.api.administration import (
    AdminAuthorizationContext,
    AdminAuthorizationDenied,
    AdminAuthorizationGrant,
    AdminOperation,
    AdminOperations,
    ConfigSummary,
    ModuleAdminSnapshot,
    ModuleHealth,
    ModuleLifecycle,
    ModuleStatus,
)
from ygl_test_subject.api.manifests import ConfigUpdateMode
from ygl_test_subject.api.services import ConfigFieldUpdate, ConfigPatch
from ygl_test_subject.core.ports import AdminAuthorizationPort
from ygl_test_subject.services.admin_facade import AdminFacade


class _Context:
    adapter_id = "test-adapter"
    request_id = "test-request"
    session_id = "test-session"


class _Authorization:
    def __init__(self, *, allow: bool = True) -> None:
        self.allow = allow
        self.operations: list[AdminOperation] = []

    async def authorize(self, operation, *, invocation, context):
        self.operations.append(operation)
        if not self.allow or context is None:
            raise AdminAuthorizationDenied
        return AdminAuthorizationGrant(operation, 1)


class _Operations:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.snapshots: tuple[ModuleAdminSnapshot, ...] = ()
        self.status = ModuleStatus(
            "pkg/mod",
            False,
            ModuleLifecycle.STOPPED,
            ModuleHealth.NEEDS_CONFIG,
            0,
            0,
        )
        self.config = ConfigSummary("pkg/mod", 0, {})

    async def list_modules(self, invocation, *, authorization=None):
        self.calls.append("list_modules")
        return self.snapshots

    async def module_snapshot(self, invocation, module_id, *, authorization=None):
        self.calls.append("module_snapshot")
        return ModuleAdminSnapshot(self.status, self.config)

    async def set_enabled(
        self,
        invocation,
        module_id,
        enabled,
        *,
        expected_registry_revision,
        authorization=None,
    ):
        self.calls.append("set_enabled")
        return self.status

    async def update_config(self, invocation, module_id, patch, *, authorization=None):
        self.calls.append("update_config")
        self.patch = patch
        return self.config


class AdminFacadeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.operations = _Operations()
        self.authorization = _Authorization()
        self.facade = AdminFacade(
            cast(AdminOperations, self.operations),
            cast(AdminAuthorizationPort, self.authorization),
        )
        self.context = cast(AdminAuthorizationContext, _Context())

    async def test_missing_context_fails_before_authorization_or_core_read(self):
        with self.assertRaises(AdminAuthorizationDenied):
            await self.facade.list_modules(None)
        self.assertEqual(self.authorization.operations, [])
        self.assertEqual(self.operations.calls, [])

    async def test_core_denial_is_distinct_from_a_successful_empty_registry(self):
        self.authorization.allow = False
        with self.assertRaises(AdminAuthorizationDenied):
            await self.facade.list_modules(None, authorization=self.context)
        self.assertEqual(self.operations.calls, [])

        self.authorization.allow = True
        result = await self.facade.list_modules(None, authorization=self.context)
        self.assertTrue(result.is_empty)
        self.assertEqual(result.modules, ())
        self.assertEqual(self.operations.calls, ["list_modules"])

    async def test_each_read_and_write_is_authorized_before_core_operation(self):
        await self.facade.module_snapshot(None, "pkg/mod", authorization=self.context)
        await self.facade.set_enabled(
            None,
            "pkg/mod",
            True,
            expected_registry_revision=0,
            authorization=self.context,
        )
        patch = ConfigPatch(expected_revision=0, updates=())
        await self.facade.update_config(
            None, "pkg/mod", patch, authorization=self.context
        )
        self.assertEqual(
            self.authorization.operations,
            [
                AdminOperation.MODULE_SNAPSHOT,
                AdminOperation.SET_ENABLED,
                AdminOperation.UPDATE_CONFIG,
            ],
        )
        self.assertEqual(
            self.operations.calls,
            ["module_snapshot", "set_enabled", "update_config"],
        )
        self.assertIs(self.operations.patch, patch)

    async def test_authorization_denial_prevents_mutation_side_effect(self):
        self.authorization.allow = False
        with self.assertRaises(AdminAuthorizationDenied):
            await self.facade.set_enabled(
                None,
                "pkg/mod",
                True,
                expected_registry_revision=0,
                authorization=self.context,
            )
        self.assertEqual(self.operations.calls, [])

    async def test_config_patch_preserves_revision_and_keep_clear_intent(self):
        patch = ConfigPatch(
            expected_revision=17,
            updates=(
                ConfigFieldUpdate("endpoint", ConfigUpdateMode.KEEP),
                ConfigFieldUpdate("legacy_option", ConfigUpdateMode.CLEAR),
            ),
        )
        await self.facade.update_config(
            None, "pkg/mod", patch, authorization=self.context
        )
        forwarded = self.operations.patch
        self.assertEqual(forwarded.expected_revision, 17)
        self.assertEqual(
            tuple(update.mode for update in forwarded.updates),
            (ConfigUpdateMode.KEEP, ConfigUpdateMode.CLEAR),
        )
        self.assertTrue(all(update.value is None for update in forwarded.updates))


if __name__ == "__main__":
    unittest.main()
