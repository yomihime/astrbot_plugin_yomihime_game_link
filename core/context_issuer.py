"""Internal issuer for identity-bound invocation views.

The public DTO is data, not a credential. Only the exact object issued by this
instance is accepted. This is a cooperative runtime boundary, not a sandbox
against hostile Python code in the same process.
"""

from collections.abc import Callable
from dataclasses import replace
from time import monotonic
from uuid import uuid4

from ..api.contexts import (
    InvocationConversationKind,
    InvocationOrigin,
    InvocationSubscriptionScope,
    InvocationView,
)
from .policy import public_web_allowed
from .ports import PublicWebBinding, PublicWebProofValidator


class InvalidInvocation(PermissionError):
    """A view was forged, expired, released, or belongs to another issuer."""


class ContextIssuer:
    """Own invocation provenance; keep this object outside ModuleServices.

    Host bridges call issue only after establishing the caller's identity.
    Downstream services must also check their own capability, grant and module
    state. An issued view alone is not blanket permission for an operation.
    ``adapter_id`` is supplied by that trusted bridge; modules and models do
    not receive this issuer and cannot choose or replace the adapter scope.
    ``capability_id`` is supplied by the trusted dispatcher after resolving
    the invoked manifest entry, so binders can require capability-scoped views.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = monotonic,
        public_web_validator: PublicWebProofValidator | None = None,
        public_web_capabilities: frozenset[tuple[str, str]] = frozenset(),
    ) -> None:
        deployment = frozenset(public_web_capabilities)
        if any(
            type(item) is not tuple
            or len(item) != 2
            or any(type(value) is not str or not value.strip() for value in item)
            for item in deployment
        ):
            raise TypeError("public web deployment must contain exact capability pairs")
        if public_web_validator is not None and any(
            not callable(getattr(public_web_validator, name, None))
            for name in ("consume", "is_current", "revoke")
        ):
            raise TypeError("public web proof validator is invalid")
        self._clock = clock
        self._web_validator = public_web_validator
        self._web_deployment = deployment
        self._web_proofs: dict[str, tuple[object, PublicWebBinding]] = {}
        self._issued: dict[str, InvocationView] = {}
        self._children: dict[str, set[str]] = {}
        self._leases: dict[str, object] = {}
        self._release_observers: set[Callable[[InvocationView], None]] = set()

    def issue(
        self,
        *,
        origin: InvocationOrigin,
        module_id: str,
        module_epoch: int,
        registry_revision: int,
        actor_id: str | None = None,
        conversation_id: str | None = None,
        adapter_id: str | None = None,
        delivery_route: str | None = None,
        conversation_kind: InvocationConversationKind | None = None,
        subscription_scope: InvocationSubscriptionScope | None = None,
        deadline: float | None = None,
        grant_id: str | None = None,
        grant_revision: int | None = None,
        subscription_id: str | None = None,
        subscription_revision: int | None = None,
        capability_id: str | None = None,
    ) -> InvocationView:
        if origin is InvocationOrigin.WEB_PUBLIC:
            raise InvalidInvocation(
                "Public web invocations require an owned host proof"
            )
        if origin in (InvocationOrigin.COMMAND, InvocationOrigin.LLM_TOOL) and (
            actor_id is None or conversation_id is None
        ):
            raise InvalidInvocation(
                "Chat invocations require an actor and conversation"
            )
        if origin is InvocationOrigin.ADMIN and actor_id is None:
            raise InvalidInvocation("Admin invocations require a verified actor")
        if grant_id is not None and actor_id is None:
            raise InvalidInvocation("Granted invocations require the grant owner")
        if subscription_id is not None and actor_id is None:
            raise InvalidInvocation("Subscription invocations require the owner")
        if origin is InvocationOrigin.SUBSCRIPTION:
            if not all(
                value is not None
                for value in (
                    actor_id,
                    subscription_id,
                    subscription_revision,
                    adapter_id,
                    conversation_id,
                    delivery_route,
                    conversation_kind,
                    subscription_scope,
                )
            ):
                raise InvalidInvocation(
                    "Subscription invocations require an owner, revision, and trusted route"
                )
            if subscription_scope is InvocationSubscriptionScope.AUTHORIZED:
                if grant_id is None:
                    raise InvalidInvocation(
                        "Authorized subscription invocations require their Grant"
                    )
            elif grant_id is not None:
                raise InvalidInvocation(
                    "Only authorized subscription invocations may carry a Grant"
                )
            if (
                subscription_scope is not InvocationSubscriptionScope.PUBLIC
                and conversation_kind is not InvocationConversationKind.DIRECT
            ):
                raise InvalidInvocation(
                    "Private subscription invocations require a trusted direct route"
                )
        elif delivery_route is not None:
            raise InvalidInvocation(
                "Only subscription invocations may carry a delivery route"
            )
        elif conversation_kind is not None:
            raise InvalidInvocation(
                "Only subscription invocations may carry a conversation kind"
            )
        elif subscription_scope is not None:
            raise InvalidInvocation(
                "Only subscription invocations may carry a subscription scope"
            )
        view = InvocationView(
            invocation_id=uuid4().hex,
            origin=origin,
            actor_id=actor_id,
            conversation_id=conversation_id,
            module_id=module_id,
            module_epoch=module_epoch,
            registry_revision=registry_revision,
            deadline=deadline,
            parent_id=None,
            grant_id=grant_id,
            grant_revision=grant_revision,
            subscription_id=subscription_id,
            subscription_revision=subscription_revision,
            adapter_id=adapter_id,
            capability_id=capability_id,
            delivery_route=delivery_route,
            conversation_kind=conversation_kind,
            subscription_scope=subscription_scope,
        )
        self._check_deadline(view)
        self._issued[view.invocation_id] = view
        return view

    def issue_public_web(
        self,
        proof: object,
        *,
        module_id: str,
        capability_id: str,
        module_epoch: int,
        registry_revision: int,
        generation: int,
        deadline: float,
    ) -> InvocationView:
        """Consume an exact host proof, without fabricating any chat identity."""
        validator = self._web_validator
        if validator is None or (module_id, capability_id) not in self._web_deployment:
            raise InvalidInvocation("Public web capability is not deployed")
        binding = validator.consume(
            proof,
            module_id=module_id,
            capability_id=capability_id,
            generation=generation,
        )
        try:
            if (
                type(binding) is not PublicWebBinding
                or binding.module_id != module_id
                or binding.capability_id != capability_id
                or binding.generation != generation
                or validator.is_current(proof, binding) is not True
            ):
                raise InvalidInvocation("Public web proof was rejected")
            view = InvocationView(
                invocation_id=uuid4().hex,
                origin=InvocationOrigin.WEB_PUBLIC,
                actor_id=None,
                conversation_id=None,
                module_id=module_id,
                module_epoch=module_epoch,
                registry_revision=registry_revision,
                deadline=min(deadline, binding.deadline_monotonic),
                capability_id=capability_id,
            )
            self._check_deadline(view)
        except BaseException:
            validator.revoke(proof)
            raise
        self._issued[view.invocation_id] = view
        self._web_proofs[view.invocation_id] = (proof, binding)
        return view

    def public_web_binding(self, view: InvocationView) -> PublicWebBinding:
        self.require(view)
        current = view
        while current.parent_id is not None:
            current = self._issued[current.parent_id]
        pair = self._web_proofs.get(current.invocation_id)
        if pair is None:
            raise InvalidInvocation("Public web proof is unavailable")
        return pair[1]

    def allows_public_web(self, module_id: str, capability: object) -> bool:
        return public_web_allowed(module_id, capability, self._web_deployment)

    def require(self, view: InvocationView) -> InvocationView:
        """Validate identity and the entire still-active parent chain."""
        if not isinstance(view, InvocationView):
            raise InvalidInvocation("Unrecognized invocation")
        current = view
        while True:
            if self._issued.get(current.invocation_id) is not current:
                raise InvalidInvocation("Invocation is not active in this runtime")
            self._check_deadline(current)
            if current.parent_id is None:
                if current.origin is InvocationOrigin.WEB_PUBLIC:
                    pair = self._web_proofs.get(current.invocation_id)
                    if (
                        pair is None
                        or self._web_validator is None
                        or self._web_validator.is_current(*pair) is not True
                    ):
                        raise InvalidInvocation("Public web proof is no longer current")
                return view
            parent = self._issued.get(current.parent_id)
            if parent is None:
                raise InvalidInvocation("Parent invocation is no longer active")
            current = parent

    def require_adapter(self, view: InvocationView) -> InvocationView:
        """Require an active invocation with a trusted adapter scope.

        This is the gate for identity/binding consumers.  The adapter is read
        only from the exact view issued by this instance; callers cannot pass
        a replacement adapter or infer one from module/conversation fields.
        B02 callers that do not use adapter-scoped identity may continue to
        call :meth:`require`.
        """
        checked = self.require(view)
        if checked.adapter_id is None:
            raise InvalidInvocation("Identity-bound invocations require an adapter")
        return checked

    def derive(
        self,
        parent: InvocationView,
        *,
        module_id: str,
        module_epoch: int,
        deadline: float | None = None,
        capability_id: str | None = None,
    ) -> InvocationView:
        """Enter a declared dependency without upgrading origin or identity.

        Capability dependencies, target epochs and scope narrowing must be
        authorized by the caller before deriving. Trusted dependency invokers
        pass the target capability_id explicitly. Omitting it clears any parent
        capability identity. A cross-module COMMAND or LLM_TOOL child does not
        inherit its parent's Grant; private Grant authority remains module
        scoped. Other origins may not carry a Grant across modules. Actor,
        origin, and subscription provenance are never caller-selectable here.
        """
        self.require(parent)
        if parent.origin is InvocationOrigin.WEB_PUBLIC and (
            capability_id is None
            or (module_id, capability_id) not in self._web_deployment
        ):
            raise InvalidInvocation("Public web dependency is not deployed")
        grant_id = parent.grant_id
        grant_revision = parent.grant_revision
        if module_id != parent.module_id and (
            grant_id is not None or grant_revision is not None
        ):
            if parent.origin in (InvocationOrigin.COMMAND, InvocationOrigin.LLM_TOOL):
                grant_id = None
                grant_revision = None
            else:
                raise InvalidInvocation(
                    "A Grant cannot cross module boundaries for this origin"
                )
        effective_deadline = parent.deadline if deadline is None else deadline
        if parent.deadline is not None and (
            effective_deadline is None or effective_deadline > parent.deadline
        ):
            raise InvalidInvocation("A child cannot extend its parent's deadline")
        view = replace(
            parent,
            invocation_id=uuid4().hex,
            parent_id=parent.invocation_id,
            module_id=module_id,
            module_epoch=module_epoch,
            deadline=effective_deadline,
            capability_id=capability_id,
            grant_id=grant_id,
            grant_revision=grant_revision,
        )
        self._check_deadline(view)
        self._issued[view.invocation_id] = view
        self._children.setdefault(parent.invocation_id, set()).add(view.invocation_id)
        return view

    def attach_lease(self, view: InvocationView, lease: object) -> None:
        """Attach the exact internal admission lease to an issuer-owned view.

        This sidecar does not alter the SDK DTO. Callers must pass the original
        view and lease objects; equality of copied DTO fields grants nothing.
        """
        self.require(view)
        if lease is None:
            raise TypeError("an admission lease is required")
        existing = self._leases.get(view.invocation_id)
        if existing is not None and existing is not lease:
            raise InvalidInvocation("Invocation already has an admission lease")
        self._leases[view.invocation_id] = lease

    def lease_for(self, view: InvocationView) -> object | None:
        """Return the exact attached lease for an active original view."""
        self.require(view)
        return self._leases.get(view.invocation_id)

    def detach_lease(self, view: InvocationView, lease: object) -> None:
        """Remove one exact lease sidecar while keeping its invocation active."""
        self.require(view)
        if self._leases.get(view.invocation_id) is not lease:
            raise InvalidInvocation("Cannot detach a different admission lease")
        self._leases.pop(view.invocation_id, None)

    def detach_lease_for_cleanup(self, view: InvocationView, lease: object) -> bool:
        """Detach one exact lease without requiring an unexpired invocation.

        Admission cleanup is allowed after the deadline. Releasing the view may
        also have removed the sidecar already, in which case this operation is
        idempotent. It never detaches a different sidecar or accepts a copied
        view while the original is still issued.
        """
        if not isinstance(view, InvocationView):
            raise InvalidInvocation("Unrecognized invocation")
        current = self._leases.get(view.invocation_id)
        if self._issued.get(view.invocation_id) is not view:
            if current is None:
                return False
            raise InvalidInvocation("Cannot clean up an unissued invocation")
        if current is None:
            return False
        if current is not lease:
            raise InvalidInvocation("Cannot detach a different admission lease")
        self._leases.pop(view.invocation_id, None)
        return True

    def add_release_observer(
        self, observer: Callable[[InvocationView], None]
    ) -> Callable[[], None]:
        """Observe each original view removed by release and get an unsubscribe.

        Observers are synchronous cleanup hooks. They should remove authority
        sidecars keyed by invocation ID and must not retain the supplied view.
        """
        if not callable(observer):
            raise TypeError("release observer must be callable")
        self._release_observers.add(observer)

        def unsubscribe() -> None:
            self._release_observers.discard(observer)

        return unsubscribe

    def release(self, view: InvocationView) -> None:
        """Release a completed request and its descendants, including expired ones."""
        if not isinstance(view, InvocationView):
            raise InvalidInvocation("Unrecognized invocation")
        if self._issued.get(view.invocation_id) is not view:
            raise InvalidInvocation("Cannot release an unissued invocation")
        if view.parent_id is not None:
            siblings = self._children.get(view.parent_id)
            if siblings is not None:
                siblings.discard(view.invocation_id)
                if not siblings:
                    self._children.pop(view.parent_id, None)
        removed: list[str] = []
        pending = [view.invocation_id]
        seen: set[str] = set()
        while pending:
            current_id = pending.pop()
            if current_id in seen:
                continue
            seen.add(current_id)
            removed.append(current_id)
            pending.extend(self._children.pop(current_id, ()))
        observers = tuple(self._release_observers)
        for key in removed:
            released = self._issued.pop(key, None)
            self._leases.pop(key, None)
            proof = self._web_proofs.pop(key, None)
            if proof is not None and self._web_validator is not None:
                try:
                    self._web_validator.revoke(proof[0])
                except Exception:
                    # Issuer authority is already invalidated; a faulty Host
                    # cleanup hook cannot keep the invocation usable.
                    pass
            if released is None:
                continue
            for observer in observers:
                try:
                    observer(released)
                except Exception:
                    # Release must invalidate the whole chain even if a
                    # best-effort sidecar cleanup hook is faulty.
                    continue

    def _check_deadline(self, view: InvocationView) -> None:
        if view.deadline is not None and view.deadline <= self._clock():
            raise InvalidInvocation("Invocation deadline has expired")
