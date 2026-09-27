"""Small, fail-closed facade over Core's administrative operations."""

from __future__ import annotations

from dataclasses import dataclass

from ..api.administration import (
    AdminAuthorizationContext,
    AdminAuthorizationDenied,
    AdminAuthorizationGrant,
    AdminOperation,
    AdminOperations,
    ConfigSummary,
    ModuleAdminSnapshot,
    ModuleStatus,
)
from ..api.contexts import InvocationView
from ..api.services import ConfigPatch
from ..core.ports import AdminAuthorizationPort


@dataclass(frozen=True, slots=True)
class ModuleListProjection:
    """A successful Core read; an empty tuple is distinct from a failed read."""

    modules: tuple[ModuleAdminSnapshot, ...]

    @property
    def is_empty(self) -> bool:
        return not self.modules


class AdminFacade:
    """Authorize each request through Core, then delegate to AdminOperations.

    The context is only evidence supplied to Core. Its descriptive fields never
    grant access by themselves; an absent context fails before any operation.
    AdminOperations remains responsible for its own per-call and mutation-fence
    checks, so this facade cannot replace the Core authorization boundary.
    """

    def __init__(
        self,
        operations: AdminOperations,
        authorization: AdminAuthorizationPort,
    ) -> None:
        if operations is None or authorization is None:
            raise ValueError("Core admin operations and authorization are required")
        self._operations = operations
        self._authorization = authorization

    @staticmethod
    def _require_context(
        authorization: AdminAuthorizationContext | None,
    ) -> AdminAuthorizationContext:
        if authorization is None:
            raise AdminAuthorizationDenied from None
        return authorization

    async def _authorize(
        self,
        operation: AdminOperation,
        invocation: InvocationView | None,
        context: AdminAuthorizationContext | None,
    ) -> AdminAuthorizationGrant:
        trusted_context = self._require_context(context)
        return await self._authorization.authorize(
            operation, invocation=invocation, context=trusted_context
        )

    async def list_modules(
        self,
        invocation: InvocationView | None,
        *,
        authorization: AdminAuthorizationContext | None = None,
    ) -> ModuleListProjection:
        context = self._require_context(authorization)
        await self._authorize(AdminOperation.LIST_MODULES, invocation, context)
        modules = await self._operations.list_modules(invocation, authorization=context)
        return ModuleListProjection(tuple(modules))

    async def module_snapshot(
        self,
        invocation: InvocationView | None,
        module_id: str,
        *,
        authorization: AdminAuthorizationContext | None = None,
    ) -> ModuleAdminSnapshot:
        context = self._require_context(authorization)
        await self._authorize(AdminOperation.MODULE_SNAPSHOT, invocation, context)
        return await self._operations.module_snapshot(
            invocation, module_id, authorization=context
        )

    async def set_enabled(
        self,
        invocation: InvocationView | None,
        module_id: str,
        enabled: bool,
        *,
        expected_registry_revision: int,
        authorization: AdminAuthorizationContext | None = None,
    ) -> ModuleStatus:
        context = self._require_context(authorization)
        await self._authorize(AdminOperation.SET_ENABLED, invocation, context)
        return await self._operations.set_enabled(
            invocation,
            module_id,
            enabled,
            expected_registry_revision=expected_registry_revision,
            authorization=context,
        )

    async def update_config(
        self,
        invocation: InvocationView | None,
        module_id: str,
        patch: ConfigPatch,
        *,
        authorization: AdminAuthorizationContext | None = None,
    ) -> ConfigSummary:
        context = self._require_context(authorization)
        await self._authorize(AdminOperation.UPDATE_CONFIG, invocation, context)
        # ConfigPatch carries its expected revision and explicit keep/replace/clear
        # modes. It also reserves SecretMaterial for declared sensitive fields.
        return await self._operations.update_config(
            invocation, module_id, patch, authorization=context
        )


__all__ = ["AdminFacade", "ModuleListProjection"]
