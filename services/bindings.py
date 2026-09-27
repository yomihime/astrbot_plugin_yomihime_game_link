"""Command-scoped account binding operations."""

from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4

from ..api.contexts import InvocationOrigin, InvocationView
from ..api.services import (
    AccountOperations,
    Binding,
    BindingDefaultSnapshot,
    BindingView,
    GrantReference,
    ResolvedIdentity,
)
from ..core.context_issuer import ContextIssuer
from ..core.ports import (
    AdmissionLease,
    AdmissionPort,
    BindingRepository,
    ConversationRepository,
    IdentityRepository,
    ModuleRegistrationLookup,
    RevisionConflict,
    UniqueConstraintViolation,
)
from .identity import (
    IdentityBindingConflict,
    IdentityBindingError,
    IdentityBindingNotFound,
    IdentityBindingPermissionError,
    IdentityBindingUnavailable,
    IdentityBindingUniqueError,
    InvocationPrincipalResolver,
    ModuleBindingUnavailable,
    _IdentityScope,
)


def _repository_error(error: Exception) -> IdentityBindingError:
    if isinstance(error, RevisionConflict):
        return IdentityBindingConflict()
    if isinstance(error, UniqueConstraintViolation):
        return IdentityBindingUniqueError()
    return IdentityBindingUnavailable()


class AccountOperationsService(AccountOperations):
    """Expose only command-authorized binding operations to a module.

    Grant and login operations belong to A2.  Their methods are present to
    preserve the public AccountOperations shape and fail closed until A2 owns
    those stores.
    """

    __slots__ = ("_scope", "_admission")

    def __init__(
        self,
        identity_repository: IdentityRepository,
        conversation_repository: ConversationRepository,
        binding_repository: BindingRepository,
        module_lookup: ModuleRegistrationLookup,
        issuer: ContextIssuer,
        *,
        identity_namespace: str,
        admission: AdmissionPort | None = None,
        principal_resolver: InvocationPrincipalResolver | None = None,
    ) -> None:
        self._scope = _IdentityScope(
            identity_repository,
            conversation_repository,
            binding_repository,
            module_lookup,
            issuer,
            identity_namespace=identity_namespace,
            principal_resolver=principal_resolver,
        )
        if admission is not None and not callable(getattr(admission, "mutation", None)):
            raise TypeError("account operations require an Admission mutation gate")
        self._admission = admission

    async def _command_scope(self, invocation: InvocationView):
        result = await self._scope.invocation(
            invocation, origins=(InvocationOrigin.COMMAND,)
        )
        checked = result[0]
        try:
            self._scope.issuer.require(checked)
            if self._admission is not None:
                lease = self._scope.issuer.lease_for(checked)
                if not isinstance(lease, AdmissionLease):
                    raise ValueError
                self._admission.check(lease)
        except Exception:
            raise IdentityBindingPermissionError() from None
        return result

    @asynccontextmanager
    async def _mutation(self, owner: str):
        if self._admission is None:
            # Direct service construction remains useful for isolated contract
            # tests. CoreRuntime's factory always injects the shared gate.
            yield
            return
        async with self._admission.mutation(owner):
            yield

    async def status(self, invocation: InvocationView) -> GrantReference | None:
        await self._command_scope(invocation)
        raise IdentityBindingUnavailable("account authorization is unavailable")

    async def begin_login(self, invocation: InvocationView) -> str:
        await self._command_scope(invocation)
        raise IdentityBindingUnavailable("account authorization is unavailable")

    async def bind(
        self, invocation: InvocationView, identity: ResolvedIdentity
    ) -> BindingView:
        _, principal_id, module_id, conversation = await self._command_scope(invocation)
        if not isinstance(identity, ResolvedIdentity):
            raise IdentityBindingNotFound("identity is invalid")
        if identity.principal_id not in (None, principal_id):
            raise IdentityBindingPermissionError()
        try:
            persisted = await self._scope.identity_repository.current_identity(
                identity.identity_id
            )
        except Exception:
            raise IdentityBindingUnavailable() from None
        if persisted is None:
            raise IdentityBindingNotFound("identity is not available")
        if (
            persisted.provider != identity.provider
            or persisted.subject != identity.subject
            or persisted.principal_id != principal_id
        ):
            raise IdentityBindingPermissionError()
        async with self._mutation(f"identity-bind:{module_id}"):
            await self._recheck_command(invocation)
            try:
                snapshot: BindingDefaultSnapshot = (
                    await self._scope.binding_repository.current_default_snapshot(
                        principal_id, module_id, conversation
                    )
                )
                if not isinstance(snapshot, BindingDefaultSnapshot):
                    raise TypeError
                binding = Binding(
                    uuid4().hex,
                    1,
                    principal_id,
                    module_id,
                    persisted.provider,
                    persisted.identity_id,
                    False,
                    "command",
                    conversation.conversation_id,
                    conversation.adapter_id,
                )
                saved = await self._scope.binding_repository.bind_default(
                    binding,
                    expected_binding_revision=0,
                    expected_default_revision=snapshot.default_revision,
                )
            except (RevisionConflict, UniqueConstraintViolation) as error:
                raise _repository_error(error) from None
            except IdentityBindingError:
                raise
            except Exception:
                raise IdentityBindingUnavailable() from None
        return await self._scope.view_for_binding(
            saved, principal_id, module_id, conversation
        )

    async def bindings(self, invocation: InvocationView) -> tuple[BindingView, ...]:
        _, principal_id, module_id, conversation = await self._command_scope(invocation)
        try:
            values = await self._scope.binding_repository.list_for(
                principal_id, module_id, conversation
            )
        except Exception as error:
            raise _repository_error(error) from None
        try:
            result: list[BindingView] = []
            for binding in values:
                result.append(
                    await self._scope.view_for_binding(
                        binding, principal_id, module_id, conversation
                    )
                )
            return tuple(result)
        except IdentityBindingError:
            raise
        except Exception:
            raise IdentityBindingUnavailable() from None

    async def replace_default(
        self, invocation: InvocationView, binding_id: str
    ) -> BindingView:
        _, principal_id, module_id, conversation = await self._command_scope(invocation)
        if type(binding_id) is not str or not binding_id.strip():
            raise IdentityBindingNotFound()
        async with self._mutation(f"identity-default:{module_id}"):
            await self._recheck_command(invocation)
            try:
                current = await self._scope.binding_repository.current(binding_id)
                if current is None:
                    raise IdentityBindingNotFound()
                await self._scope.identity_for_binding(
                    current, principal_id, module_id, conversation
                )
                snapshot = (
                    await self._scope.binding_repository.current_default_snapshot(
                        principal_id, module_id, conversation
                    )
                )
                selected = await self._scope.binding_repository.replace_default(
                    principal_id,
                    module_id,
                    conversation,
                    binding_id,
                    expected_revision=snapshot.default_revision,
                )
            except IdentityBindingError:
                raise
            except (RevisionConflict, UniqueConstraintViolation) as error:
                raise _repository_error(error) from None
            except Exception:
                raise IdentityBindingUnavailable() from None
        return await self._scope.view_for_binding(
            selected, principal_id, module_id, conversation
        )

    async def unbind(
        self, invocation: InvocationView, binding_id: str, *, expected_revision: int
    ) -> None:
        _, principal_id, module_id, conversation = await self._command_scope(invocation)
        if type(binding_id) is not str or not binding_id.strip():
            raise IdentityBindingNotFound()
        if type(expected_revision) is not int or expected_revision < 1:
            raise IdentityBindingConflict("binding revision is invalid")
        async with self._mutation(f"identity-unbind:{module_id}"):
            await self._recheck_command(invocation)
            try:
                current = await self._scope.binding_repository.current(binding_id)
                if current is None:
                    raise IdentityBindingNotFound()
                await self._scope.identity_for_binding(
                    current, principal_id, module_id, conversation
                )
                await self._scope.binding_repository.delete(
                    binding_id, expected_revision=expected_revision
                )
            except IdentityBindingError:
                raise
            except (RevisionConflict, UniqueConstraintViolation) as error:
                raise _repository_error(error) from None
            except Exception:
                raise IdentityBindingUnavailable() from None

    async def revoke(self, invocation: InvocationView, grant: GrantReference) -> None:
        await self._command_scope(invocation)
        raise IdentityBindingUnavailable("account authorization is unavailable")

    async def _recheck_command(self, invocation: InvocationView) -> None:
        try:
            checked = self._scope.issuer.require(invocation)
            if self._admission is not None:
                lease = self._scope.issuer.lease_for(checked)
                if not isinstance(lease, AdmissionLease):
                    raise ValueError
                self._admission.check(lease)
        except Exception:
            raise IdentityBindingPermissionError() from None


AccountService = AccountOperationsService
BindingService = AccountOperationsService
AccountOperationsImpl = AccountOperationsService

__all__ = [
    "AccountOperationsImpl",
    "AccountOperationsService",
    "AccountService",
    "BindingService",
    "ModuleBindingUnavailable",
]
