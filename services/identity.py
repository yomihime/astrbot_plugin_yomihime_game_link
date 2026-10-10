"""Chat identity and default binding resolution.

The host supplies this service with an issuer and adapter identity repository.
The service never accepts actor or adapter values from a module or model; both
are read from the exact invocation issued by :class:`ContextIssuer`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final
from uuid import uuid4

from yomihime_game_link_sdk.contexts import InvocationOrigin, InvocationView
from yomihime_game_link_sdk.services import (
    BindingView,
    IdentityResolver,
    ResolvedIdentity,
)
from yomihime_game_link_sdk.subscriptions import ConversationRef

from ..core.context_issuer import ContextIssuer, InvalidInvocation
from ..core.contracts.services import (
    Binding,
    BindingDefaultSnapshot,
    ConversationKey,
    Principal,
)
from ..core.contracts.validation_boundary import validate_contract
from ..core.ports import (
    AdmissionLease,
    AdmissionPort,
    BindingRepository,
    ConversationRepository,
    IdentityRepository,
    ModuleNotRegistered,
    ModuleRegistrationLookup,
    RevisionConflict,
    ScheduledLease,
    UniqueConstraintViolation,
)


class IdentityBindingError(RuntimeError):
    """Stable, sanitized failure from identity and binding services."""

    code: Final[str] = "identity_binding_error"

    def __init__(self, message: str = "identity binding operation failed") -> None:
        super().__init__(message)


class IdentityBindingPermissionError(PermissionError, IdentityBindingError):
    code = "identity_binding_forbidden"

    def __init__(self, message: str = "identity binding operation is not permitted"):
        super().__init__(message)


class PrincipalResolutionDenied(PermissionError):
    """The exact invocation cannot resolve or use a durable principal."""


class PrincipalResolutionUnavailable(RuntimeError):
    """The core identity repository could not resolve a durable principal."""


class TrustedIngressPrincipalProvisioner:
    """Idempotently persist a host-verified actor mapping.

    CoreRuntime calls this only after its host ingress evidence validator has
    accepted the exact ingress. The actor key is supplied by that host and is
    expected to include its adapter namespace where platform identities can
    otherwise collide.
    """

    __slots__ = ("_identities", "_namespace")

    def __init__(self, identity_repository, *, identity_namespace: str) -> None:
        if not callable(
            getattr(identity_repository, "find_principal", None)
        ) or not callable(getattr(identity_repository, "save_principal", None)):
            raise TypeError("principal provisioner requires an identity repository")
        try:
            namespace = _stable_text(identity_namespace, "identity_namespace")
        except ValueError:
            raise ValueError("identity namespace is invalid") from None
        self._identities = identity_repository
        self._namespace = namespace

    async def ensure(self, actor_id: str) -> Principal:
        """Return the existing mapping or create one without replacing it."""

        try:
            actor = _stable_text(actor_id, "actor_id")
        except ValueError:
            raise PrincipalResolutionDenied() from None
        existing = await self._find(actor)
        if existing is not None:
            return existing

        candidate = Principal(uuid4().hex, self._namespace, actor)
        try:
            saved = await self._identities.save_principal(candidate)
        except UniqueConstraintViolation:
            # Another accepted ingress may have won the unique
            # (namespace, external actor) insert. Re-read and return that exact
            # mapping; never retry with a new principal or overwrite it.
            raced = await self._find(actor)
            if raced is None:
                raise PrincipalResolutionUnavailable() from None
            return raced
        except Exception:
            raise PrincipalResolutionUnavailable() from None
        if not self._matches(saved, actor):
            raise PrincipalResolutionUnavailable()
        return saved

    async def _find(self, actor_id: str) -> Principal | None:
        try:
            existing = await self._identities.find_principal(self._namespace, actor_id)
        except Exception:
            raise PrincipalResolutionUnavailable() from None
        if existing is None:
            return None
        if not self._matches(existing, actor_id):
            raise PrincipalResolutionUnavailable()
        return existing

    def _matches(self, principal: object, actor_id: str) -> bool:
        return (
            isinstance(principal, Principal)
            and principal.identity_namespace == self._namespace
            and principal.external_user_id == actor_id
            and bool(principal.principal_id)
        )


class InvocationPrincipalResolver:
    """Resolve adapter actors to principals without keeping an identity cache."""

    __slots__ = ("_issuer", "_identities", "_namespace", "_admission")

    def __init__(
        self,
        issuer: ContextIssuer,
        identity_repository: IdentityRepository,
        *,
        identity_namespace: str,
        admission: AdmissionPort | None,
    ) -> None:
        if not isinstance(issuer, ContextIssuer):
            raise TypeError("principal resolver requires a ContextIssuer")
        if not callable(getattr(identity_repository, "find_principal", None)):
            raise TypeError("principal resolver requires an identity repository")
        if admission is not None and not callable(getattr(admission, "check", None)):
            raise TypeError("principal resolver requires an Admission port")
        try:
            namespace = _stable_text(identity_namespace, "identity_namespace")
        except ValueError:
            raise ValueError("identity namespace is invalid") from None
        self._issuer = issuer
        self._identities = identity_repository
        self._namespace = namespace
        self._admission = admission

    async def principal_id(self, invocation: InvocationView) -> str:
        """Return the durable owner for an exact active issuer-owned view."""

        validate_contract(invocation)
        try:
            trusted = self._issuer.require(invocation)
        except Exception:
            raise PrincipalResolutionDenied() from None
        actor_id = trusted.actor_id
        if type(actor_id) is not str or not actor_id.strip():
            raise PrincipalResolutionDenied()

        lease = self._issuer.lease_for(trusted)
        if trusted.origin in (InvocationOrigin.COMMAND, InvocationOrigin.LLM_TOOL):
            try:
                self._issuer.require_adapter(trusted)
                if lease is not None and not isinstance(lease, AdmissionLease):
                    raise ValueError
                self._check_lease(trusted, lease)
            except Exception:
                raise PrincipalResolutionDenied() from None
            try:
                principal = await self._identities.find_principal(
                    self._namespace, actor_id
                )
            except Exception:
                raise PrincipalResolutionUnavailable() from None
            try:
                self._issuer.require_adapter(trusted)
                if self._issuer.lease_for(trusted) is not lease:
                    raise ValueError
                self._check_lease(trusted, lease)
            except Exception:
                raise PrincipalResolutionDenied() from None
            principal_id = getattr(principal, "principal_id", None)
            if type(principal_id) is not str or not principal_id.strip():
                raise PrincipalResolutionDenied()
            return principal_id

        if trusted.origin is InvocationOrigin.SCHEDULER:
            if (
                not isinstance(lease, ScheduledLease)
                or lease.module_id != trusted.module_id
                or lease.module_epoch != trusted.module_epoch
            ):
                raise PrincipalResolutionDenied()
            self._check_lease(trusted, lease)
            return actor_id

        if trusted.origin is InvocationOrigin.SUBSCRIPTION:
            if (
                trusted.parent_id is not None
                or trusted.subscription_id is None
                or trusted.subscription_revision is None
                or not isinstance(lease, AdmissionLease)
            ):
                raise PrincipalResolutionDenied()
            self._check_lease(trusted, lease)
            return actor_id

        raise PrincipalResolutionDenied()

    def _check_lease(self, invocation: InvocationView, lease: object | None) -> None:
        validate_contract(invocation)
        if self._admission is None:
            return
        if not isinstance(lease, (AdmissionLease, ScheduledLease)):
            raise ValueError("invocation has no exact Admission lease")
        if (
            lease.module_id != invocation.module_id
            or lease.module_epoch != invocation.module_epoch
        ):
            raise ValueError("invocation lease does not match its module epoch")
        self._admission.check(lease)


class IdentityBindingConflict(IdentityBindingError):
    code = "revision_conflict"

    def __init__(self, message: str = "identity binding revision conflict"):
        super().__init__(message)


class IdentityBindingUniqueError(IdentityBindingError):
    code = "unique_constraint"

    def __init__(
        self, message: str = "identity binding conflicts with an existing object"
    ):
        super().__init__(message)


class IdentityBindingNotFound(IdentityBindingError):
    code = "identity_binding_not_found"

    def __init__(self, message: str = "identity binding was not found"):
        super().__init__(message)


class IdentityBindingUnavailable(IdentityBindingError):
    code = "identity_binding_unavailable"

    def __init__(self, message: str = "identity binding is unavailable"):
        super().__init__(message)


class ModuleBindingUnavailable(IdentityBindingUnavailable):
    code = "module_not_registered"

    def __init__(self, message: str = "module is not registered"):
        super().__init__(message)


class TrustedRoutePublisher:
    """Host-only writer for adapter-attested conversation routes.

    The writer is intentionally absent from the SDK ``IdentityResolver`` and
    ``ModuleServices`` protocols. Only CoreRuntime/host ingress should retain
    it; a module-supplied ConversationRef is not route authority.
    """

    __slots__ = ("_conversations", "_admission")

    def __init__(
        self, conversations: ConversationRepository, admission: AdmissionPort
    ) -> None:
        if not callable(getattr(conversations, "save", None)):
            raise TypeError("conversation repository is not usable")
        if not callable(getattr(admission, "mutation", None)):
            raise TypeError("route publisher requires the Core mutation gate")
        self._conversations = conversations
        self._admission = admission

    async def publish(self, reference: ConversationRef) -> ConversationRef:
        """Persist a route supplied by a trusted host adapter under the gate."""

        validate_contract(reference)
        if not isinstance(reference, ConversationRef):
            raise TypeError("route publisher requires a ConversationRef")
        try:
            checked = validate_contract(
                ConversationRef(
                    reference.adapter_id,
                    reference.kind,
                    reference.conversation_id,
                    reference.delivery_route,
                )
            )
        except (AttributeError, TypeError, ValueError):
            raise IdentityBindingPermissionError() from None
        async with self._admission.mutation(
            f"conversation-route:{checked.adapter_id}:{checked.conversation_id}"
        ):
            try:
                saved = await self._conversations.save(checked)
                if not isinstance(saved, ConversationRef):
                    raise TypeError
                persisted = validate_contract(
                    ConversationRef(
                        saved.adapter_id,
                        saved.kind,
                        saved.conversation_id,
                        saved.delivery_route,
                    )
                )
            except Exception:
                raise IdentityBindingUnavailable() from None
            if persisted != checked:
                raise IdentityBindingUnavailable()
            return persisted


def _stable_text(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value


class _IdentityScope:
    """Trusted host-side scope checks shared by resolver and account service."""

    __slots__ = (
        "identity_repository",
        "conversation_repository",
        "binding_repository",
        "module_lookup",
        "issuer",
        "identity_namespace",
        "principal_resolver",
    )

    def __init__(
        self,
        identity_repository: IdentityRepository,
        conversation_repository: ConversationRepository,
        binding_repository: BindingRepository,
        module_lookup: ModuleRegistrationLookup,
        issuer: ContextIssuer,
        *,
        identity_namespace: str,
        principal_resolver: InvocationPrincipalResolver | None = None,
    ) -> None:
        if not callable(getattr(identity_repository, "find_principal", None)):
            raise TypeError("identity repository is not usable")
        if not callable(getattr(conversation_repository, "current", None)):
            raise TypeError("conversation repository is not usable")
        if not callable(getattr(binding_repository, "current_default", None)):
            raise TypeError("binding repository is not usable")
        if not callable(getattr(module_lookup, "require_registered", None)):
            raise TypeError("module lookup is not usable")
        if not isinstance(issuer, ContextIssuer):
            raise TypeError("identity binding requires a ContextIssuer")
        try:
            identity_namespace = _stable_text(identity_namespace, "identity_namespace")
        except ValueError:
            raise ValueError("identity namespace is invalid") from None
        self.identity_repository = identity_repository
        self.conversation_repository = conversation_repository
        self.binding_repository = binding_repository
        self.module_lookup = module_lookup
        self.issuer = issuer
        self.identity_namespace = identity_namespace
        if principal_resolver is not None and not callable(
            getattr(principal_resolver, "principal_id", None)
        ):
            raise TypeError("principal resolver is not usable")
        self.principal_resolver = principal_resolver or InvocationPrincipalResolver(
            issuer,
            identity_repository,
            identity_namespace=identity_namespace,
            admission=None,
        )

    async def invocation(
        self,
        invocation: InvocationView,
        *,
        origins: Iterable[InvocationOrigin],
    ) -> tuple[InvocationView, str, str, ConversationKey]:
        validate_contract(invocation)
        validate_contract(origins)
        try:
            checked = self.issuer.require_adapter(invocation)
        except InvalidInvocation:
            raise IdentityBindingPermissionError() from None
        if checked.origin not in tuple(origins):
            raise IdentityBindingPermissionError()
        if checked.parent_id is not None:
            raise IdentityBindingPermissionError()
        if checked.actor_id is None or checked.conversation_id is None:
            raise IdentityBindingPermissionError()
        if checked.adapter_id is None:
            raise IdentityBindingPermissionError()
        try:
            registration = await self.module_lookup.require_registered(
                checked.module_id
            )
            if getattr(registration, "module_id", None) != checked.module_id:
                raise IdentityBindingUnavailable()
        except ModuleNotRegistered:
            raise ModuleBindingUnavailable() from None
        except IdentityBindingUnavailable:
            raise
        except (AttributeError, TypeError, ValueError):
            raise IdentityBindingUnavailable() from None
        try:
            conversation = await self.conversation_repository.current(
                checked.adapter_id, checked.conversation_id
            )
        except Exception:
            raise IdentityBindingUnavailable() from None
        try:
            conversation_matches = (
                conversation is not None
                and conversation.adapter_id == checked.adapter_id
                and conversation.conversation_id == checked.conversation_id
            )
        except (AttributeError, TypeError):
            conversation_matches = False
        if not conversation_matches:
            raise IdentityBindingPermissionError()
        try:
            principal_id = await self.principal_resolver.principal_id(checked)
        except PrincipalResolutionDenied:
            raise IdentityBindingPermissionError() from None
        except PrincipalResolutionUnavailable:
            raise IdentityBindingUnavailable() from None
        return (
            checked,
            principal_id,
            checked.module_id,
            ConversationKey(checked.adapter_id, checked.conversation_id),
        )

    async def identity_for_binding(
        self,
        binding: Binding,
        principal_id: str,
        module_id: str,
        conversation: ConversationKey,
    ) -> ResolvedIdentity:
        if (
            binding.principal_id != principal_id
            or binding.module_id != module_id
            or binding.conversation_key != conversation
        ):
            raise IdentityBindingPermissionError()
        try:
            identity = await self.identity_repository.current_identity(
                binding.object_id
            )
        except Exception:
            raise IdentityBindingUnavailable() from None
        if (
            identity is None
            or identity.principal_id != principal_id
            or identity.provider != binding.object_type
        ):
            raise IdentityBindingNotFound()
        return identity

    async def view_for_binding(
        self,
        binding: Binding,
        principal_id: str,
        module_id: str,
        conversation: ConversationKey,
    ) -> BindingView:
        identity = await self.identity_for_binding(
            binding, principal_id, module_id, conversation
        )
        return validate_contract(
            BindingView(
                binding.binding_id, binding.revision, identity, binding.is_default
            )
        )


class IdentityResolverService(IdentityResolver):
    """Resolve the current user's module default identity."""

    __slots__ = ("_scope",)

    def __init__(
        self,
        identity_repository: IdentityRepository,
        conversation_repository: ConversationRepository,
        binding_repository: BindingRepository,
        module_lookup: ModuleRegistrationLookup,
        issuer: ContextIssuer,
        *,
        identity_namespace: str,
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

    async def default_identity(
        self, invocation: InvocationView
    ) -> ResolvedIdentity | None:
        validate_contract(invocation)
        _, principal_id, module_id, conversation = await self._scope.invocation(
            invocation,
            origins=(InvocationOrigin.COMMAND,),
        )
        try:
            snapshot: BindingDefaultSnapshot = (
                await self._scope.binding_repository.current_default_snapshot(
                    principal_id, module_id, conversation
                )
            )
        except AttributeError:
            raise IdentityBindingUnavailable() from None
        except (RevisionConflict, UniqueConstraintViolation):
            raise IdentityBindingConflict() from None
        except Exception:
            raise IdentityBindingUnavailable() from None
        if not isinstance(snapshot, BindingDefaultSnapshot):
            raise IdentityBindingUnavailable()
        if snapshot.binding is None:
            return None
        return await self._scope.identity_for_binding(
            snapshot.binding, principal_id, module_id, conversation
        )


IdentityResolverImpl = IdentityResolverService
IdentityService = IdentityResolverService

__all__ = [
    "InvocationPrincipalResolver",
    "IdentityBindingConflict",
    "IdentityBindingError",
    "IdentityBindingNotFound",
    "IdentityBindingPermissionError",
    "IdentityBindingUniqueError",
    "IdentityBindingUnavailable",
    "PrincipalResolutionDenied",
    "PrincipalResolutionUnavailable",
    "IdentityResolverImpl",
    "IdentityResolverService",
    "IdentityService",
    "ModuleBindingUnavailable",
    "TrustedRoutePublisher",
    "_IdentityScope",
]
