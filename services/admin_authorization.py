"""Core authorization and lifecycle for the independent admin credential."""

from __future__ import annotations

import base64
import hashlib
import hmac
import inspect
from collections.abc import Awaitable, Callable
from enum import StrEnum

from ..api.administration import (
    AdminAuthorizationContext,
    AdminAuthorizationDenied,
    AdminAuthorizationGrant,
    AdminOperation,
)
from ..api.contexts import InvocationView
from ..core.ports import AdmissionPort
from ..infrastructure.sqlite.repositories_admin_credentials import (
    AdminCredentialState,
    AdminCredentialStatus,
    SQLiteAdminCredentialRepository,
)


class AdminCredentialOperation(StrEnum):
    ROTATE = "admin_credential_rotate"
    REVOKE = "admin_credential_revoke"


ContextValidator = Callable[
    [
        AdminOperation | AdminCredentialOperation,
        InvocationView | None,
        AdminAuthorizationContext,
        int,
    ],
    bool | Awaitable[bool],
]


def _digest(credential: str) -> bytes:
    """Validate canonical base64url encoding of at least 32 random bytes."""
    if type(credential) is not str or not credential or len(credential) > 4096:
        raise ValueError("admin credential must encode at least 32 random bytes")
    try:
        encoded = credential.encode("ascii")
        raw = base64.b64decode(
            encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True
        )
    except (UnicodeEncodeError, ValueError):
        raise ValueError(
            "admin credential must encode at least 32 random bytes"
        ) from None
    canonical = base64.urlsafe_b64encode(raw).rstrip(b"=")
    if len(raw) < 32 or canonical != encoded:
        raise ValueError("admin credential must encode at least 32 random bytes")
    return hashlib.sha256(raw).digest()


def _matches(credential: str, state: AdminCredentialState) -> bool:
    try:
        candidate = _digest(credential)
    except ValueError:
        candidate = bytes(32)
        valid = False
    else:
        valid = True
    expected = state.verifier_digest or bytes(32)
    return hmac.compare_digest(candidate, expected) and valid


def _context_is_structural(context: object) -> bool:
    try:
        return all(
            type(getattr(context, field)) is str
            and bool(getattr(context, field).strip())
            for field in ("adapter_id", "request_id", "session_id")
        )
    except Exception:
        return False


class AdminAuthorizationService:
    """Fail-closed Core seam; trusted session proof must be injected by H-host.

    The validator must prove that this request/session was established by a
    successful independent credential check and is bound to the supplied
    generation. Merely having the protocol's descriptive IDs is insufficient.
    """

    def __init__(
        self,
        repository: SQLiteAdminCredentialRepository,
        *,
        admission: AdmissionPort | None = None,
        context_validator: ContextValidator | None = None,
    ) -> None:
        if admission is not None and not callable(getattr(admission, "mutation", None)):
            raise TypeError("admin admission is not usable")
        self.repository = repository
        self.admission = admission
        self.context_validator = context_validator

    async def authorize(
        self,
        operation: AdminOperation,
        *,
        invocation: InvocationView | None,
        context: AdminAuthorizationContext | None,
    ) -> AdminAuthorizationGrant:
        if not isinstance(operation, AdminOperation) or not _context_is_structural(
            context
        ):
            raise AdminAuthorizationDenied from None
        validator = self.context_validator
        if validator is None:
            raise AdminAuthorizationDenied from None
        try:
            state = await self.repository.current()
            if state.status is not AdminCredentialStatus.ACTIVE:
                raise AdminAuthorizationDenied
            valid = validator(operation, invocation, context, state.generation)
            if inspect.isawaitable(valid):
                valid = await valid
            if valid is not True:
                raise AdminAuthorizationDenied
            # A rotation/revoke during validation fences the late grant.
            latest = await self.repository.current()
            if (
                latest.status is not AdminCredentialStatus.ACTIVE
                or latest.generation != state.generation
            ):
                raise AdminAuthorizationDenied
            return AdminAuthorizationGrant(operation, state.generation)
        except AdminAuthorizationDenied:
            raise
        except Exception:
            raise AdminAuthorizationDenied from None

    async def revalidate(
        self,
        grant: AdminAuthorizationGrant,
        *,
        operation: AdminOperation,
        invocation: InvocationView | None,
        context: AdminAuthorizationContext | None,
    ) -> AdminAuthorizationGrant:
        """Recheck a prior grant immediately before a Core read/write effect."""
        if grant.operation is not operation:
            raise AdminAuthorizationDenied from None
        latest = await self.authorize(operation, invocation=invocation, context=context)
        if latest.generation != grant.generation:
            raise AdminAuthorizationDenied from None
        return latest

    async def validate_generation(
        self, grant: AdminAuthorizationGrant, *, operation: AdminOperation
    ) -> None:
        """Recheck an operation grant against the durable active generation.

        Callers that need cross-writer serialization must already hold the
        shared Admission mutation gate. This method never acquires that gate
        and never validates host session or network credentials.
        """

        if (
            not isinstance(grant, AdminAuthorizationGrant)
            or not isinstance(operation, AdminOperation)
            or grant.operation is not operation
        ):
            raise AdminAuthorizationDenied from None
        try:
            state = await self.repository.current()
        except Exception:
            raise AdminAuthorizationDenied from None
        if (
            state.status is not AdminCredentialStatus.ACTIVE
            or state.generation != grant.generation
        ):
            raise AdminAuthorizationDenied from None

    async def _authorize_lifecycle(
        self,
        operation: AdminCredentialOperation,
        current_credential: str,
        *,
        invocation: InvocationView | None,
        context: AdminAuthorizationContext | None,
    ) -> AdminCredentialState:
        validator = self.context_validator
        if validator is None or not _context_is_structural(context):
            raise AdminAuthorizationDenied from None
        try:
            state = await self.repository.current()
            if state.status is not AdminCredentialStatus.ACTIVE or not _matches(
                current_credential, state
            ):
                raise AdminAuthorizationDenied
            accepted = validator(operation, invocation, context, state.generation)
            if inspect.isawaitable(accepted):
                accepted = await accepted
            if accepted is not True:
                raise AdminAuthorizationDenied
            latest = await self.repository.current()
            if (
                latest.status is not AdminCredentialStatus.ACTIVE
                or latest.generation != state.generation
                or latest.verifier_digest != state.verifier_digest
            ):
                raise AdminAuthorizationDenied
            return latest
        except AdminAuthorizationDenied:
            raise
        except Exception:
            raise AdminAuthorizationDenied from None

    @staticmethod
    def assert_generation_current(
        unit: object, grant: AdminAuthorizationGrant, operation: AdminOperation
    ) -> None:
        """Fence a grant inside the caller's SQLite read/write transaction.

        Admin operations call this after opening their own transaction and
        before reading or mutating protected state. It intentionally accepts
        the existing unit-of-work boundary instead of opening a second one.
        """
        if grant.operation is not operation:
            raise AdminAuthorizationDenied from None
        try:
            row = unit.execute(  # type: ignore[attr-defined]
                "SELECT state, generation, verifier_digest FROM admin_credentials "
                "WHERE singleton=1"
            ).fetchone()
            state = AdminCredentialState(
                AdminCredentialStatus(row["state"]),
                row["generation"],
                row["verifier_digest"],
            )
            if (
                state.status is not AdminCredentialStatus.ACTIVE
                or type(state.generation) is not int
                or state.generation != grant.generation
                or type(state.verifier_digest) is not bytes
                or len(state.verifier_digest) != 32
            ):
                raise AdminAuthorizationDenied
        except AdminAuthorizationDenied:
            raise
        except Exception:
            raise AdminAuthorizationDenied from None

    async def rotate(
        self,
        current_credential: str,
        new_credential: str,
        *,
        invocation: InvocationView | None,
        context: AdminAuthorizationContext | None,
    ) -> AdminCredentialState:
        admission = self.admission
        if admission is None:
            raise AdminAuthorizationDenied from None
        new_digest = _digest(new_credential)
        state = await self._authorize_lifecycle(
            AdminCredentialOperation.ROTATE,
            current_credential,
            invocation=invocation,
            context=context,
        )
        async with admission.mutation("admin-credential-rotate"):
            return await self.repository.rotate(state.generation, new_digest)

    async def revoke(
        self,
        current_credential: str,
        *,
        invocation: InvocationView | None,
        context: AdminAuthorizationContext | None,
    ) -> AdminCredentialState:
        admission = self.admission
        if admission is None:
            raise AdminAuthorizationDenied from None
        state = await self._authorize_lifecycle(
            AdminCredentialOperation.REVOKE,
            current_credential,
            invocation=invocation,
            context=context,
        )
        async with admission.mutation("admin-credential-revoke"):
            return await self.repository.revoke(state.generation)


__all__ = [
    "AdminAuthorizationService",
    "AdminCredentialOperation",
    "ContextValidator",
]
