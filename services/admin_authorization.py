"""Core authorization and lifecycle for the independent admin credential."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import inspect
import threading
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from time import time

from yomihime_game_link_sdk.contexts import InvocationView
from yomihime_game_link_sdk.services import ConfigSnapshot

from ..core.contracts.administration import (
    AdminAuthorizationContext,
    AdminAuthorizationDenied,
    AdminAuthorizationGrant,
    AdminOperation,
)
from ..core.contracts.validation_boundary import validate_contract
from ..core.ports import AdmissionPort
from ..infrastructure.sqlite.repositories_admin_credentials import (
    AdminCredentialState,
    AdminCredentialStatus,
    SQLiteAdminCredentialRepository,
)
from .admin_sources import TrustedAdminSource


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
        self._sources = {}
        self._grants = {}
        self._lock = threading.RLock()
        self._closed = False

    def register_source(self, source_id, *, resources, operations):
        """Explicit deployment assembly; never exposed as a request operation."""
        with self._lock:
            if self._closed or source_id in self._sources:
                raise ValueError("authority unavailable or already registered")
            source = TrustedAdminSource(source_id, resources, operations)
            self._sources[source_id] = source
            return source

    def close(self):
        with self._lock:
            self._closed = True
            self._grants.clear()
            sources = tuple(self._sources.values())
        for source in sources:
            source.close()

    def _owned(self, grant):
        with self._lock:
            if self._closed or self._grants.get(id(grant)) is not grant:
                raise AdminAuthorizationDenied
        return grant._effect

    def _mint(
        self, operation, generation, context, invocation, source=None, resources=None
    ):
        effect = _GrantEffect(
            self, operation, generation, context, invocation, source, resources or {}
        )
        grant = AdminAuthorizationGrant(operation, generation, effect)
        effect.grant = grant
        with self._lock:
            if self._closed:
                raise AdminAuthorizationDenied
            # Grants have a hard request lifetime, including compatibility native grants.
            self._grants = {
                key: item
                for key, item in self._grants.items()
                if item._effect.expiry > time()
            }
            self._grants[id(grant)] = grant
        return grant

    async def authorize(
        self,
        operation: AdminOperation,
        *,
        invocation: InvocationView | None,
        context: AdminAuthorizationContext | None,
        resources=None,
    ) -> AdminAuthorizationGrant:
        validate_contract(invocation)
        if not isinstance(operation, AdminOperation) or not _context_is_structural(
            context
        ):
            raise AdminAuthorizationDenied from None
        if self._closed:
            raise AdminAuthorizationDenied
        source = self._sources.get(context.adapter_id)
        if source is not None:
            proof = source.check(context, operation, resources or {})
            return self._mint(
                operation, proof.epoch, context, invocation, source, resources
            )
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
            return self._mint(operation, state.generation, context, invocation)
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
        validate_contract(invocation)
        effect = self._owned(grant)
        if grant.operation is not operation or effect.context is not context:
            raise AdminAuthorizationDenied from None
        latest = await self.authorize(
            operation,
            invocation=invocation,
            context=context,
            resources=effect.resources,
        )
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
        effect = self._owned(grant)
        effect.check_lifetime()
        if effect.source is not None:
            effect.source.check(effect.context, operation, effect.resources)
            return
        try:
            state = await self.repository.current()
        except Exception:
            raise AdminAuthorizationDenied from None
        if (
            state.status is not AdminCredentialStatus.ACTIVE
            or state.generation != grant.generation
        ):
            raise AdminAuthorizationDenied from None
        effect.check_lifetime()
        validator = self.context_validator
        accepted = validator(
            operation, effect.invocation, effect.context, grant.generation
        )
        if inspect.isawaitable(accepted):
            accepted = await accepted
        if accepted is not True:
            raise AdminAuthorizationDenied
        effect.check_lifetime()

    async def _authorize_lifecycle(
        self,
        operation: AdminCredentialOperation,
        current_credential: str,
        *,
        invocation: InvocationView | None,
        context: AdminAuthorizationContext | None,
    ) -> AdminCredentialState:
        validate_contract(invocation)
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
        from ..infrastructure.sqlite.repositories_admin_credentials import (
            assert_generation_current,
        )

        assert_generation_current(unit, grant, operation)

    async def rotate(
        self,
        current_credential: str,
        new_credential: str,
        *,
        invocation: InvocationView | None,
        context: AdminAuthorizationContext | None,
    ) -> AdminCredentialState:
        validate_contract(invocation)
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
        validate_contract(invocation)
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


class _GrantEffect:
    def __init__(
        self, service, operation, generation, context, invocation, source, resources
    ):
        self.service = service
        self.operation = operation
        self.generation = generation
        self.context = context
        self.invocation = invocation
        self.source = source
        self.resources = {t: frozenset(fields) for t, fields in resources.items()}
        self.expiry = time() + 60
        self.task = asyncio.current_task()
        self.grant = None

    def check_lifetime(self):
        self.service._owned(self.grant)
        if (
            time() >= self.expiry
            or self.task is None
            or self.task.done()
            or self.task.cancelling()
        ):
            raise AdminAuthorizationDenied

    def resource_fields(self, target):
        self.service._owned(self.grant)
        if self.source is None:
            return None  # Native authority retains its existing unrestricted resources.
        if target not in self.resources:
            raise AdminAuthorizationDenied
        return self.resources[target]

    def project_snapshot(self, snapshot):
        fields = self.resource_fields(snapshot.target)
        return _project_snapshot_fields(snapshot, fields)

    def check_resource(self, target, fields):
        if self.source is not None and (
            target not in self.resources or not set(fields) <= self.resources[target]
        ):
            raise AdminAuthorizationDenied

    def _check(self, unit):
        self.check_lifetime()
        path = unit.execute("PRAGMA database_list").fetchone()[2]
        if Path(path).resolve() != self.service.repository.database.path.resolve():
            raise AdminAuthorizationDenied
        if self.source is not None:
            self.source.check(self.context, self.operation, self.resources)
            return
        row = unit.execute(
            "SELECT state,generation,verifier_digest FROM admin_credentials WHERE singleton=1"
        ).fetchone()
        if (
            row is None
            or row["state"] != AdminCredentialStatus.ACTIVE.value
            or row["generation"] != self.generation
            or type(row["verifier_digest"]) is not bytes
            or len(row["verifier_digest"]) != 32
        ):
            raise AdminAuthorizationDenied
        validator = self.service.context_validator
        # The synchronous SQLite fence must prove the native request is still
        # live. An async-only validator cannot prove this at commit and is denied.
        if validator is None:
            raise AdminAuthorizationDenied
        if inspect.iscoroutinefunction(validator):
            raise AdminAuthorizationDenied
        else:
            valid = validator(
                self.operation, self.invocation, self.context, self.generation
            )
            if inspect.isawaitable(valid):
                valid.close()
                raise AdminAuthorizationDenied
            if valid is not True:
                raise AdminAuthorizationDenied

    def check(self, unit, operation):
        if operation is not self.operation:
            raise AdminAuthorizationDenied
        self._check(unit)
        unit.add_commit_guard(lambda: self.fence(unit))

    @contextmanager
    def fence(self, unit):
        if self.source is None:
            with self.service._lock:
                self._check(unit)
                yield
        else:
            with self.source.fence(self.context, self.operation, self.resources):
                self._check(unit)
                yield


def _grant_effect(grant):
    if (
        not isinstance(grant, AdminAuthorizationGrant)
        or type(grant._effect) is not _GrantEffect
        or grant._effect.grant is not grant
    ):
        raise AdminAuthorizationDenied
    grant._effect.service._owned(grant)
    return grant._effect


def _grant_resource_fields(grant, target):
    return _grant_effect(grant).resource_fields(target)


def _project_snapshot_fields(snapshot, fields):
    """Pure output projection of a previously owned, immutable result scope.

    This does not authorize an operation or effect. A committed operation can
    finish its internal cleanup after close without reusing a revoked grant.
    """
    if fields is None:
        return snapshot
    return validate_contract(
        ConfigSnapshot(
            snapshot.revision,
            {name: value for name, value in snapshot.values.items() if name in fields},
            tuple(item for item in snapshot.secret_metadata if item.field in fields),
            snapshot.target,
        )
    )


def _project_grant_snapshot(grant, snapshot):
    return _grant_effect(grant).project_snapshot(snapshot)


__all__ = [
    "AdminAuthorizationService",
    "AdminCredentialOperation",
    "ContextValidator",
]
