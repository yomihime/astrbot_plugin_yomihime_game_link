"""Command-scoped login sessions and authorization grants.

This service owns the small state machine around the frozen ``Grant`` and
``LoginSession`` contracts.  It deliberately does not know how a provider
authenticates a user: a provider-specific adapter must verify an exchange and
pass its result to :meth:`complete_login`.

The service never returns a secret, login challenge, account credential, or
provider payload.  A grant is usable only when its current SQLite row is
active and its opaque secret reference can be resolved by the injected
availability check.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from yomihime_game_link_sdk.contexts import InvocationOrigin, InvocationView
from yomihime_game_link_sdk.storage import GrantReference, SecretRef

from ..core.context_issuer import ContextIssuer, InvalidInvocation
from ..core.contracts.services import (
    Grant,
    GrantStatus,
    LoginSession,
    LoginSessionStatus,
)
from ..core.contracts.storage import GrantReference_validate
from ..core.contracts.validation_boundary import validate_contract
from ..core.ports import (
    AdmissionLease,
    AdmissionPort,
    AuthorizationRepository,
    GrantRevocationCoordinator,
    LoginSessionRepository,
    RevisionConflict,
    ScheduledLease,
    SecretOwner,
    UniqueConstraintViolation,
)
from .identity import (
    InvocationPrincipalResolver,
    PrincipalResolutionDenied,
    PrincipalResolutionUnavailable,
)


class AuthorizationError(RuntimeError):
    """Sanitized base error for authorization state transitions."""

    code: str = "authorization_error"

    def __init__(self, message: str = "authorization operation failed") -> None:
        super().__init__(message)


class AuthorizationPermissionError(PermissionError, AuthorizationError):
    code = "authorization_forbidden"

    def __init__(self, message: str = "authorization operation is not permitted"):
        super().__init__(message)


class AuthorizationConflict(AuthorizationError):
    code = "revision_conflict"

    def __init__(self, message: str = "authorization state changed"):
        super().__init__(message)


class AuthorizationNotFound(AuthorizationError):
    code = "authorization_not_found"

    def __init__(self, message: str = "authorization state was not found"):
        super().__init__(message)


class AuthorizationUnavailable(AuthorizationError):
    code = "authorization_unavailable"

    def __init__(self, message: str = "authorization is unavailable"):
        super().__init__(message)


class LoginSessionExpired(AuthorizationError):
    code = "login_session_expired"

    def __init__(self, message: str = "login session is no longer active"):
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class AuthorizationStatus:
    """A redacted status snapshot suitable for command presentation.

    It intentionally contains no account identifier, secret reference, or
    provider response.  ``grant`` is only a versioned capability reference;
    it is not authority on its own.
    """

    grant: GrantReference | None
    grant_status: GrantStatus | None
    secret_available: bool
    login_status: LoginSessionStatus | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.grant is not None:
            object.__setattr__(self, "grant", GrantReference_validate(self.grant))
        if self.grant_status is not None and not isinstance(
            self.grant_status, GrantStatus
        ):
            raise TypeError("grant status must be a GrantStatus")
        if not isinstance(self.secret_available, bool):
            raise TypeError("secret availability must be a bool")
        if self.login_status is not None and not isinstance(
            self.login_status, LoginSessionStatus
        ):
            raise TypeError("login status must be a LoginSessionStatus")
        if self.reason is not None:
            if type(self.reason) is not str or not self.reason:
                raise ValueError("status reason must be non-empty text")
            if any(ord(char) < 32 for char in self.reason):
                raise ValueError("status reason contains control characters")


class AuthorizationExchange(Protocol):
    """Provider adapter boundary used by the generic callback state machine."""

    async def verify(
        self, session: LoginSession
    ) -> Grant:  # pragma: no cover - protocol declaration
        ...


SecretAvailability = Callable[[Grant], bool | Awaitable[bool]]


def _bounded_text(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"{field} contains control characters")
    return value


class AuthorizationService:
    """Implement command-only login, grant status, and revocation.

    ``identity_repository``, ``conversation_repository`` and
    ``module_lookup`` are optional host-side checks.  When supplied, they are
    used exactly like the A1 identity service.  The issuer remains mandatory:
    a copied or manually-created ``InvocationView`` is never accepted.
    """

    __slots__ = (
        "_grants",
        "_sessions",
        "_revocation_coordinator",
        "_issuer",
        "_identity_repository",
        "_conversation_repository",
        "_module_lookup",
        "_identity_namespace",
        "_principal_resolver",
        "_secret_store",
        "_secret_available",
        "_exchange_verifier",
        "_generations",
        "_current_sessions",
        "_session_conversations",
        "_max_generation_retries",
        "_admission",
        "_now",
    )

    def __init__(
        self,
        grant_store: AuthorizationRepository,
        login_repository: LoginSessionRepository,
        issuer: ContextIssuer,
        *,
        revocation_coordinator: GrantRevocationCoordinator | None = None,
        identity_repository: object | None = None,
        conversation_repository: object | None = None,
        module_lookup: object | None = None,
        identity_namespace: str | None = None,
        principal_resolver: InvocationPrincipalResolver | None = None,
        secret_store: object | None = None,
        secret_available: SecretAvailability | None = None,
        exchange_verifier: Callable[..., Grant | Awaitable[Grant]] | None = None,
        max_generation_retries: int = 8,
        admission: AdmissionPort | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._require_port(grant_store, "current_grant", "grant store")
        self._require_port(grant_store, "list_for", "grant store")
        self._require_port(grant_store, "create_grant", "grant store")
        self._require_port(grant_store, "rotate_grant", "grant store")
        self._require_port(grant_store, "revoke_grant", "grant store")
        self._require_port(grant_store, "complete_login", "authorization repository")
        self._require_port(login_repository, "current", "login repository")
        self._require_port(login_repository, "next_generation", "login repository")
        self._require_port(login_repository, "save", "login repository")
        self._require_port(
            login_repository, "expire_pending_on_restart", "login repository"
        )
        if not isinstance(issuer, ContextIssuer):
            raise TypeError("authorization requires a ContextIssuer")
        if revocation_coordinator is None:
            candidate = getattr(grant_store, "revoke_grant_with_invalidation", None)
            if callable(candidate):
                revocation_coordinator = grant_store
        if revocation_coordinator is not None:
            self._require_port(
                revocation_coordinator,
                "revoke_grant_with_invalidation",
                "grant revocation coordinator",
            )
        if identity_repository is not None:
            self._require_port(
                identity_repository, "find_principal", "identity repository"
            )
        if conversation_repository is not None:
            self._require_port(
                conversation_repository, "current", "conversation repository"
            )
        if module_lookup is not None:
            self._require_port(module_lookup, "require_registered", "module lookup")
        if identity_namespace is not None:
            identity_namespace = _bounded_text(identity_namespace, "identity_namespace")
        if principal_resolver is not None and not callable(
            getattr(principal_resolver, "principal_id", None)
        ):
            raise TypeError("principal resolver is not usable")
        if secret_available is not None and not callable(secret_available):
            raise TypeError("secret availability checker is not callable")
        if secret_store is not None and not callable(
            getattr(secret_store, "read", None)
        ):
            raise TypeError("secret store is not usable")
        if exchange_verifier is not None and not callable(exchange_verifier):
            raise TypeError("exchange verifier is not callable")
        if admission is not None and not callable(getattr(admission, "mutation", None)):
            raise TypeError("admission mutation gate is not usable")
        if now is not None and not callable(now):
            raise TypeError("authorization clock is not callable")
        if (
            isinstance(max_generation_retries, bool)
            or not isinstance(max_generation_retries, int)
            or max_generation_retries < 1
        ):
            raise ValueError("max generation retries must be positive")
        self._grants = grant_store
        self._sessions = login_repository
        self._revocation_coordinator = revocation_coordinator
        self._issuer = issuer
        self._identity_repository = identity_repository
        self._conversation_repository = conversation_repository
        self._module_lookup = module_lookup
        self._identity_namespace = identity_namespace
        if principal_resolver is None and identity_repository is not None:
            if identity_namespace is None:
                raise ValueError(
                    "identity namespace is required with identity repository"
                )
            principal_resolver = InvocationPrincipalResolver(
                issuer,
                identity_repository,
                identity_namespace=identity_namespace,
                admission=admission,
            )
        self._principal_resolver = principal_resolver
        self._secret_store = secret_store
        self._secret_available = secret_available
        self._exchange_verifier = exchange_verifier
        self._generations: dict[tuple[str, str], int] = {}
        self._current_sessions: dict[tuple[str, str], str] = {}
        self._session_conversations: dict[str, tuple[str, str]] = {}
        self._max_generation_retries = max_generation_retries
        self._admission = admission
        self._now = now or (lambda: datetime.now(UTC))

    def _utc_now(self) -> datetime:
        value = self._now()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise AuthorizationUnavailable("authorization clock is unavailable")
        return value.astimezone(UTC)

    @staticmethod
    def _require_port(value: object, method: str, label: str) -> None:
        if not callable(getattr(value, method, None)):
            raise TypeError(f"{label} is not usable")

    async def _scope(
        self,
        invocation: InvocationView,
        *,
        allow_derived_command: bool = False,
    ) -> tuple[InvocationView, str, str]:
        validate_contract(invocation)
        try:
            checked = self._issuer.require_adapter(invocation)
        except (InvalidInvocation, TypeError, ValueError):
            raise AuthorizationPermissionError() from None
        if checked.origin is not InvocationOrigin.COMMAND or (
            checked.parent_id is not None and not allow_derived_command
        ):
            raise AuthorizationPermissionError()
        self._check_live(checked)
        if checked.actor_id is None or checked.conversation_id is None:
            raise AuthorizationPermissionError()
        if checked.adapter_id is None:
            raise AuthorizationPermissionError()
        if self._module_lookup is not None:
            try:
                registration = await self._module_lookup.require_registered(
                    checked.module_id
                )
                if getattr(registration, "module_id", None) != checked.module_id:
                    raise AuthorizationUnavailable("module registration is unavailable")
                if getattr(registration, "enabled", True) is False:
                    raise AuthorizationUnavailable("module is unavailable")
            except AuthorizationError:
                raise
            except Exception:
                raise AuthorizationUnavailable(
                    "module registration is unavailable"
                ) from None
        if self._conversation_repository is not None:
            try:
                conversation = await self._conversation_repository.current(
                    checked.adapter_id, checked.conversation_id
                )
                if (
                    conversation is None
                    or conversation.adapter_id != checked.adapter_id
                    or conversation.conversation_id != checked.conversation_id
                ):
                    raise AuthorizationPermissionError()
            except AuthorizationError:
                raise
            except Exception:
                raise AuthorizationUnavailable("conversation is unavailable") from None
        principal_id = checked.actor_id
        if self._principal_resolver is not None:
            try:
                principal_id = await self._principal_resolver.principal_id(checked)
            except PrincipalResolutionDenied:
                raise AuthorizationPermissionError() from None
            except PrincipalResolutionUnavailable:
                raise AuthorizationUnavailable("identity is unavailable") from None
            except Exception:
                raise AuthorizationUnavailable("identity is unavailable") from None
        self._check_live(checked)
        return checked, principal_id, checked.module_id

    def _check_live(self, invocation: InvocationView) -> None:
        validate_contract(invocation)
        try:
            self._issuer.require(invocation)
            if self._admission is not None:
                lease = self._issuer.lease_for(invocation)
                if not isinstance(lease, AdmissionLease):
                    raise ValueError
                self._admission.check(lease)
        except Exception:
            raise AuthorizationPermissionError() from None

    @asynccontextmanager
    async def _mutation(self, owner: str):
        if self._admission is None:
            # Direct construction remains supported for isolated service
            # contracts. Runtime composition always injects its shared gate.
            yield
            return
        async with self._admission.mutation(owner):
            yield

    async def begin_login(self, invocation: InvocationView) -> str:
        """Start a new login generation and return only its opaque session id."""

        validate_contract(invocation)
        checked, principal_id, module_id = await self._scope(invocation)
        async with self._mutation(f"authorization-login:{module_id}"):
            self._check_live(checked)
            return await self._begin_login_locked(checked, principal_id, module_id)

    async def _begin_login_locked(
        self, checked: InvocationView, principal_id: str, module_id: str
    ) -> str:
        validate_contract(checked)
        key = (principal_id, module_id)
        expected = self._generations.get(key, 0)
        for _ in range(self._max_generation_retries):
            try:
                session = await self._sessions.next_generation(
                    principal_id, module_id, expected_generation=expected
                )
            except RevisionConflict as error:
                if error.resource != "login_session_generation":
                    raise AuthorizationConflict() from None
                actual = getattr(error, "actual_revision", None)
                if type(actual) is not int or actual < expected:
                    actual = expected + 1
                expected = actual
                continue
            except Exception:
                raise AuthorizationUnavailable("login session is unavailable") from None
            if not isinstance(session, LoginSession):
                raise AuthorizationUnavailable("login session is invalid")
            if (
                session.principal_id != principal_id
                or session.module_id != module_id
                or session.status is not LoginSessionStatus.PENDING
            ):
                raise AuthorizationUnavailable("login session is invalid")
            self._check_live(checked)
            self._generations[key] = session.generation
            self._current_sessions[key] = session.session_id
            self._session_conversations[session.session_id] = (
                checked.adapter_id or "",
                checked.conversation_id or "",
            )
            return session.session_id
        raise AuthorizationConflict()

    async def reopen(self, invocation: InvocationView) -> str:
        """Start a fresh generation; the old callback remains unusable."""

        validate_contract(invocation)
        return await self.begin_login(invocation)

    reopen_login = reopen

    async def restart(self) -> int:
        """Expire unfinished login sessions after a process restart."""

        async with self._mutation("authorization-restart"):
            try:
                count = await self._sessions.expire_pending_on_restart()
            except Exception:
                raise AuthorizationUnavailable(
                    "login session recovery is unavailable"
                ) from None
            if type(count) is not int or count < 0:
                raise AuthorizationUnavailable("login session recovery is invalid")
            self._current_sessions.clear()
            self._session_conversations.clear()
            return count

    recover = restart

    async def _current_grant(
        self, principal_id: str, module_id: str, grant_ref: GrantReference
    ) -> Grant:
        validate_contract(grant_ref)
        try:
            grant = await self._grants.current_grant(grant_ref.grant_id)
        except Exception:
            raise AuthorizationUnavailable("grant authority is unavailable") from None
        if grant is None:
            raise AuthorizationNotFound()
        if (
            grant.principal_id != principal_id
            or grant.module_id != module_id
            or grant.revision != grant_ref.revision
        ):
            raise AuthorizationConflict()
        return grant

    async def _has_secret(self, grant: Grant) -> bool:
        if not isinstance(grant.secret_ref, SecretRef):
            return False
        if self._secret_available is not None:
            try:
                result = self._secret_available(grant)
                result = await result if inspect.isawaitable(result) else result
                return result is True
            except Exception:
                return False
        if self._secret_store is None:
            return False
        try:
            ref = grant.secret_ref
            owner = SecretOwner(
                grant.principal_id, grant.module_id, ref.field, ref.operation_id
            )
            value = await self._secret_store.read(ref, owner=owner)
            return value is not None
        except Exception:
            return False

    async def status_details(self, invocation: InvocationView) -> AuthorizationStatus:
        """Read current redacted login/grant status for a command invocation."""

        validate_contract(invocation)
        _, principal_id, module_id = await self._scope(invocation)
        try:
            grants = await self._grants.list_for(principal_id, module_id)
        except Exception:
            raise AuthorizationUnavailable("grant authority is unavailable") from None
        matching: list[Grant] = []
        for grant in grants:
            if not isinstance(grant, Grant):
                raise AuthorizationUnavailable("grant authority returned invalid state")
            if grant.principal_id != principal_id or grant.module_id != module_id:
                continue
            matching.append(grant)
        login_status: LoginSessionStatus | None = None
        session_id = self._current_sessions.get((principal_id, module_id))
        if session_id is not None:
            try:
                session = await self._sessions.current(session_id)
            except Exception:
                raise AuthorizationUnavailable("login session is unavailable") from None
            if session is not None:
                login_status = session.status
        now = self._utc_now()
        usable: list[Grant] = []
        for grant in matching:
            if grant.status is not GrantStatus.ACTIVE:
                continue
            if grant.expires_at is not None and grant.expires_at <= now:
                continue
            if await self._has_secret(grant):
                usable.append(grant)
        # The repository permits one active owner/module grant. If legacy or
        # corrupt data violates that rule, fail closed instead of comparing
        # revisions from unrelated grant IDs.
        if len(usable) != 1:
            return AuthorizationStatus(
                None, None, False, login_status, "not_authorized"
            )
        current = usable[0]
        return AuthorizationStatus(
            validate_contract(GrantReference(current.grant_id, current.revision)),
            GrantStatus.ACTIVE,
            True,
            login_status,
            None,
        )

    async def status(self, invocation: InvocationView) -> GrantReference | None:
        """Return a current usable grant reference, never its secret."""

        validate_contract(invocation)
        snapshot = await self.status_details(invocation)
        return snapshot.grant if snapshot.grant_status is GrantStatus.ACTIVE else None

    async def require_current_grant(self, invocation: InvocationView) -> Grant:
        """Return the actual active grant after identity and secret checks.

        A GrantReference on a view is only a lookup key; this method verifies
        that it still names the authenticated principal's active persisted row
        and that its secret reference is available.
        """

        validate_contract(invocation)
        checked, principal_id, module_id = await self._scope(invocation)
        return await self._require_current_grant_for_scope(
            checked, principal_id, module_id
        )

    async def require_current_derived_grant(self, invocation: InvocationView) -> Grant:
        """Revalidate a grant inherited by an exact COMMAND dependency view.

        This internal binding path is narrower than ordinary command grant
        authorization: only an issuer-owned child view with a live admission
        lease is accepted. ``ContextIssuer.require`` checks its complete live
        parent chain, while the persisted row must belong to this child module
        and principal. Account and subscription entry points continue to use
        the root-only ``_scope`` default.
        """

        validate_contract(invocation)
        checked, principal_id, module_id = await self._scope(
            invocation, allow_derived_command=True
        )
        if checked.parent_id is None:
            raise AuthorizationPermissionError()
        return await self._require_current_grant_for_scope(
            checked, principal_id, module_id
        )

    async def _require_current_grant_for_scope(
        self, checked: InvocationView, principal_id: str, module_id: str
    ) -> Grant:
        validate_contract(checked)
        if checked.grant_id is None or checked.grant_revision is None:
            raise AuthorizationPermissionError()
        try:
            reference = validate_contract(
                GrantReference(checked.grant_id, checked.grant_revision)
            )
        except (TypeError, ValueError):
            raise AuthorizationPermissionError() from None
        grant = await self._current_grant(principal_id, module_id, reference)
        self._check_live(checked)
        if not self._grant_is_current(grant, principal_id, module_id, reference):
            raise AuthorizationPermissionError()

        secret_available = await self._has_secret(grant)
        self._check_live(checked)
        if not secret_available:
            raise AuthorizationPermissionError()

        # Secret resolution may yield while another command revokes, rotates,
        # or expires this row. Re-read durable authority before returning it.
        current = await self._current_grant(principal_id, module_id, reference)
        self._check_live(checked)
        if (
            not self._grant_is_current(current, principal_id, module_id, reference)
            or current.secret_ref != grant.secret_ref
        ):
            raise AuthorizationPermissionError()
        return current

    def _grant_is_current(
        self,
        grant: Grant,
        principal_id: str,
        module_id: str,
        reference: GrantReference,
    ) -> bool:
        validate_contract(reference)
        return (
            grant.grant_id == reference.grant_id
            and grant.revision == reference.revision
            and grant.principal_id == principal_id
            and grant.module_id == module_id
            and grant.status is GrantStatus.ACTIVE
            and (grant.expires_at is None or grant.expires_at > self._utc_now())
        )

    def _check_dependency_views(
        self, parent: InvocationView, child: InvocationView
    ) -> tuple[InvocationView, AdmissionLease, AdmissionLease]:
        """Fence an exact same-module child in a COMMAND invocation chain."""

        validate_contract(parent)
        validate_contract(child)
        try:
            checked_parent = self._issuer.require_adapter(parent)
            checked_child = self._issuer.require_adapter(child)
            if (
                checked_parent.origin is not InvocationOrigin.COMMAND
                or checked_child.origin is not InvocationOrigin.COMMAND
                or checked_child.parent_id != checked_parent.invocation_id
                or checked_parent.module_id != checked_child.module_id
                or checked_parent.module_epoch != checked_child.module_epoch
                or checked_parent.registry_revision != checked_child.registry_revision
                or checked_parent.actor_id != checked_child.actor_id
                or checked_parent.conversation_id != checked_child.conversation_id
                or checked_parent.adapter_id != checked_child.adapter_id
                or checked_parent.grant_id is None
                or checked_parent.grant_revision is None
                or checked_child.grant_id != checked_parent.grant_id
                or checked_child.grant_revision != checked_parent.grant_revision
                or checked_child.capability_id is None
                or checked_parent.subscription_id is not None
                or checked_child.subscription_id is not None
            ):
                raise ValueError
            parent_lease = self._issuer.lease_for(checked_parent)
            child_lease = self._issuer.lease_for(checked_child)
            if (
                not isinstance(parent_lease, AdmissionLease)
                or not isinstance(child_lease, AdmissionLease)
                or self._admission is None
            ):
                raise ValueError
            self._admission.check(parent_lease)
            self._admission.check(child_lease)
            return checked_child, parent_lease, child_lease
        except Exception:
            raise AuthorizationPermissionError() from None

    async def require_dependency_grant(
        self, parent: InvocationView, child: InvocationView
    ) -> Grant:
        """Authorize a declared same-module private child of a COMMAND chain.

        Login, status, and revoke entry points continue to use ``_scope`` with
        its root-only default. Only DependencyInvoker receives this method.
        """

        validate_contract(parent)
        validate_contract(child)
        self._check_dependency_views(parent, child)
        checked, principal_id, module_id = await self._scope(
            parent, allow_derived_command=True
        )
        self._check_dependency_views(checked, child)
        if (
            checked.grant_id is None
            or checked.grant_revision is None
            or child.grant_id != checked.grant_id
            or child.grant_revision != checked.grant_revision
        ):
            raise AuthorizationPermissionError()
        try:
            reference = validate_contract(
                GrantReference(checked.grant_id, checked.grant_revision)
            )
        except (TypeError, ValueError):
            raise AuthorizationPermissionError() from None

        grant = await self._current_grant(principal_id, module_id, reference)
        self._check_dependency_views(checked, child)
        if not self._grant_is_current(grant, principal_id, module_id, reference):
            raise AuthorizationPermissionError()
        secret_available = await self._has_secret(grant)
        self._check_dependency_views(checked, child)
        if not secret_available:
            raise AuthorizationPermissionError()

        current = await self._current_grant(principal_id, module_id, reference)
        self._check_dependency_views(checked, child)
        if (
            not self._grant_is_current(current, principal_id, module_id, reference)
            or current.secret_ref != grant.secret_ref
        ):
            raise AuthorizationPermissionError()
        return current

    def _check_scheduled_live(
        self, invocation: InvocationView, lease: ScheduledLease
    ) -> None:
        """Require the exact active scheduler lease attached by this issuer."""

        validate_contract(invocation)
        try:
            checked = self._issuer.require(invocation)
            if (
                checked.origin is not InvocationOrigin.SCHEDULER
                or checked.parent_id is not None
                or checked.capability_id is not None
                or checked.module_id != lease.module_id
                or checked.module_epoch != lease.module_epoch
                or self._issuer.lease_for(checked) is not lease
                or self._admission is None
            ):
                raise ValueError
            self._admission.check(lease)
        except Exception:
            raise AuthorizationPermissionError() from None

    def _scheduled_grant_is_current(
        self, grant: Grant, invocation: InvocationView, reference: GrantReference
    ) -> bool:
        validate_contract(invocation)
        validate_contract(reference)
        return (
            grant.grant_id == reference.grant_id
            and grant.revision == reference.revision
            and grant.principal_id == invocation.actor_id
            and grant.module_id == invocation.module_id
            and grant.status is GrantStatus.ACTIVE
            and (grant.expires_at is None or grant.expires_at > self._utc_now())
        )

    async def require_scheduled_grant(
        self, invocation: InvocationView, lease: ScheduledLease
    ) -> Grant:
        """Validate a persisted grant carried by an exact scheduler claim.

        Scheduler views have no adapter conversation identity, so they cannot
        use the command identity path. The persisted scheduler execution claim
        and its sidecar lease provide the authority to look up the same
        actor/module/grant tuple. Tool and nested views never enter this path.
        """

        validate_contract(invocation)
        if not isinstance(lease, ScheduledLease):
            raise AuthorizationPermissionError()
        self._check_scheduled_live(invocation, lease)
        if (
            invocation.actor_id is None
            or invocation.grant_id is None
            or invocation.grant_revision is None
        ):
            raise AuthorizationPermissionError()
        try:
            reference = validate_contract(
                GrantReference(invocation.grant_id, invocation.grant_revision)
            )
        except (TypeError, ValueError):
            raise AuthorizationPermissionError() from None

        # Check the lease after every awaited authority boundary. A grant
        # reference on the InvocationView is only a lookup key, never proof.
        grant = await self._current_grant(
            invocation.actor_id, invocation.module_id, reference
        )
        self._check_scheduled_live(invocation, lease)
        if not self._scheduled_grant_is_current(grant, invocation, reference):
            raise AuthorizationPermissionError()
        secret_available = await self._has_secret(grant)
        self._check_scheduled_live(invocation, lease)
        if not secret_available:
            raise AuthorizationPermissionError()

        # Secret resolution can yield. Re-read the durable row and fence the
        # exact lease again so revocation/rotation during that await fails.
        current = await self._current_grant(
            invocation.actor_id, invocation.module_id, reference
        )
        self._check_scheduled_live(invocation, lease)
        if (
            not self._scheduled_grant_is_current(current, invocation, reference)
            or current.secret_ref != grant.secret_ref
        ):
            raise AuthorizationPermissionError()
        return current

    async def _verify_exchange(self, session: LoginSession, exchange: object) -> Grant:
        verifier = self._exchange_verifier
        if verifier is None:
            raise AuthorizationPermissionError("authorization exchange is unverified")
        else:
            try:
                result = verifier(session, exchange)
            except Exception:
                raise AuthorizationPermissionError(
                    "authorization exchange is unverified"
                ) from None
        try:
            result = await result if inspect.isawaitable(result) else result
        except Exception:
            raise AuthorizationPermissionError(
                "authorization exchange is unverified"
            ) from None
        if not isinstance(result, Grant):
            raise AuthorizationPermissionError("authorization exchange is unverified")
        return result

    async def complete_login(
        self,
        invocation: InvocationView,
        session_id: str,
        exchange: object,
        *,
        generation: int | None = None,
    ) -> GrantReference:
        validate_contract(invocation)
        checked, _, module_id = await self._scope(invocation)
        async with self._mutation(f"authorization-complete:{module_id}"):
            self._check_live(checked)
            return await self._complete_login_locked(
                checked, session_id, exchange, generation=generation
            )

    async def _complete_login_locked(
        self,
        invocation: InvocationView,
        session_id: str,
        exchange: object,
        *,
        generation: int | None = None,
    ) -> GrantReference:
        """Commit a verified exchange only for the current login generation."""

        validate_contract(invocation)
        checked, principal_id, module_id = await self._scope(invocation)
        try:
            session_id = _bounded_text(session_id, "session_id")
        except ValueError:
            raise AuthorizationNotFound() from None
        try:
            session = await self._sessions.current(session_id)
        except Exception:
            raise AuthorizationUnavailable("login session is unavailable") from None
        if (
            session is None
            or session.principal_id != principal_id
            or session.module_id != module_id
            or session.status is not LoginSessionStatus.PENDING
            or session.expires_at <= self._utc_now()
            or (generation is not None and generation != session.generation)
        ):
            raise LoginSessionExpired()
        key = (principal_id, module_id)
        if (
            self._current_sessions.get(key) != session.session_id
            or self._generations.get(key) != session.generation
        ):
            # Persisted PENDING rows from another runtime are not callbacks
            # for this runtime's current login flow.
            raise LoginSessionExpired()
        conversation = self._session_conversations.get(session.session_id)
        if conversation is not None and conversation != (
            checked.adapter_id,
            checked.conversation_id,
        ):
            raise AuthorizationPermissionError()
        grant = await self._verify_exchange(session, exchange)
        if (
            grant.principal_id != principal_id
            or grant.module_id != module_id
            or grant.status is not GrantStatus.ACTIVE
            or not await self._has_secret(grant)
        ):
            raise AuthorizationUnavailable("authorization exchange is unavailable")
        existing: Grant | None
        try:
            existing = await self._grants.current_grant(grant.grant_id)
        except Exception:
            raise AuthorizationUnavailable("grant authority is unavailable") from None
        completed = LoginSession(
            session.session_id,
            session.principal_id,
            session.module_id,
            session.generation,
            LoginSessionStatus.COMPLETED,
            session.expires_at,
        )
        expected_grant_revision: int | None = None
        if existing is not None and (
            existing.principal_id != principal_id or existing.module_id != module_id
        ):
            raise AuthorizationConflict()

        if existing is not None and existing.status is GrantStatus.ACTIVE:
            expected_grant_revision = existing.revision
            grant = Grant(
                grant.grant_id,
                existing.revision + 1,
                grant.principal_id,
                grant.module_id,
                grant.account_id,
                grant.scopes,
                grant.secret_ref,
                grant.status,
                grant.expires_at,
            )
        else:
            # A revoked grant remains the durable unique row for its account.
            # Find that owner/module/account row and rotate its exact current
            # revision so reauthorization preserves the revoked revision's
            # denial while satisfying the account uniqueness constraint.
            try:
                owner_grants = await self._grants.list_for(principal_id, module_id)
            except Exception:
                raise AuthorizationUnavailable(
                    "grant authority is unavailable"
                ) from None
            if any(not isinstance(item, Grant) for item in owner_grants):
                raise AuthorizationUnavailable("grant authority returned invalid state")
            account_rows = [
                item
                for item in owner_grants
                if item.principal_id == principal_id
                and item.module_id == module_id
                and item.account_id == grant.account_id
            ]
            if len(account_rows) > 1:
                raise AuthorizationConflict()
            account_row = account_rows[0] if account_rows else None
            if account_row is not None and account_row.status is GrantStatus.REVOKED:
                if existing is not None and existing.grant_id != account_row.grant_id:
                    raise AuthorizationConflict()
                expected_grant_revision = account_row.revision
                grant = Grant(
                    account_row.grant_id,
                    account_row.revision + 1,
                    grant.principal_id,
                    grant.module_id,
                    grant.account_id,
                    grant.scopes,
                    grant.secret_ref,
                    grant.status,
                    grant.expires_at,
                )
            elif existing is not None:
                raise AuthorizationConflict()
        try:
            persisted_session, persisted = await self._grants.complete_login(
                completed,
                grant,
                expected_generation=session.generation,
                expected_grant_revision=expected_grant_revision,
            )
        except RevisionConflict:
            raise AuthorizationConflict() from None
        except UniqueConstraintViolation:
            raise AuthorizationConflict() from None
        except AuthorizationError:
            raise
        except Exception:
            raise AuthorizationUnavailable(
                "authorization could not be committed"
            ) from None
        if (
            not isinstance(persisted_session, LoginSession)
            or persisted_session.session_id != session.session_id
            or persisted_session.generation != session.generation
            or persisted_session.status is not LoginSessionStatus.COMPLETED
            or not isinstance(persisted, Grant)
            or persisted.status is not GrantStatus.ACTIVE
            or persisted.principal_id != principal_id
            or persisted.module_id != module_id
        ):
            raise AuthorizationUnavailable(
                "authorization repository returned invalid state"
            )
        if self._generations.get(key, session.generation) <= session.generation:
            self._generations[key] = session.generation
            self._current_sessions[key] = session.session_id
            self._session_conversations[session.session_id] = (
                checked.adapter_id or "",
                checked.conversation_id or "",
            )
        return validate_contract(GrantReference(persisted.grant_id, persisted.revision))

    complete = complete_login
    confirm = complete_login

    async def revoke(self, invocation: InvocationView, grant: GrantReference) -> None:
        validate_contract(invocation)
        validate_contract(grant)
        checked, _, module_id = await self._scope(invocation)
        async with self._mutation(f"authorization-revoke:{module_id}"):
            self._check_live(checked)
            await self._revoke_locked(checked, grant)

    async def _revoke_locked(
        self, invocation: InvocationView, grant: GrantReference
    ) -> None:
        """CAS-revoke a user-owned grant and invalidate private data."""

        validate_contract(invocation)
        validate_contract(grant)
        _, principal_id, module_id = await self._scope(invocation)
        try:
            grant = GrantReference_validate(grant)
        except (TypeError, ValueError):
            raise AuthorizationPermissionError() from None
        current = await self._current_grant(principal_id, module_id, grant)
        if current.status is GrantStatus.REVOKED:
            raise AuthorizationConflict()
        coordinator = self._revocation_coordinator
        if coordinator is None:
            raise AuthorizationUnavailable("atomic grant revocation is unavailable")
        try:
            revoked = await coordinator.revoke_grant_with_invalidation(
                current, expected_revision=grant.revision
            )
        except RevisionConflict:
            raise AuthorizationConflict() from None
        except Exception:
            raise AuthorizationUnavailable("grant revocation is unavailable") from None
        if (
            not isinstance(revoked, Grant)
            or revoked.status is not GrantStatus.REVOKED
            or revoked.grant_id != current.grant_id
            or revoked.principal_id != current.principal_id
            or revoked.module_id != current.module_id
            or revoked.revision != current.revision + 1
        ):
            raise AuthorizationUnavailable("grant authority returned invalid state")
        key = (principal_id, module_id)
        session_id = self._current_sessions.pop(key, None)
        if session_id is not None:
            self._session_conversations.pop(session_id, None)


AuthorizationServiceImpl = AuthorizationService
AccountAuthorizationService = AuthorizationService

__all__ = [
    "AccountAuthorizationService",
    "AuthorizationConflict",
    "AuthorizationError",
    "AuthorizationExchange",
    "AuthorizationNotFound",
    "AuthorizationPermissionError",
    "AuthorizationService",
    "AuthorizationServiceImpl",
    "AuthorizationStatus",
    "AuthorizationUnavailable",
    "LoginSessionExpired",
]
