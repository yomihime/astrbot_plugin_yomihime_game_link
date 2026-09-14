"""Internal issuer for identity-bound invocation views.

The public DTO is data, not a credential. Only the exact object issued by this
instance is accepted. This is a cooperative runtime boundary, not a sandbox
against hostile Python code in the same process.
"""

from collections.abc import Callable
from dataclasses import replace
from time import monotonic
from uuid import uuid4

from ..api.contexts import InvocationOrigin, InvocationView


class InvalidInvocation(PermissionError):
    """A view was forged, expired, released, or belongs to another issuer."""


class ContextIssuer:
    """Own invocation provenance; keep this object outside ModuleServices.

    Host bridges call issue only after establishing the caller's identity.
    Downstream services must also check their own capability, grant and module
    state. An issued view alone is not blanket permission for an operation.
    """

    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._issued: dict[str, InvocationView] = {}

    def issue(
        self,
        *,
        origin: InvocationOrigin,
        module_id: str,
        module_epoch: int,
        registry_revision: int,
        actor_id: str | None = None,
        conversation_id: str | None = None,
        deadline: float | None = None,
        grant_id: str | None = None,
        grant_revision: int | None = None,
        subscription_id: str | None = None,
        subscription_revision: int | None = None,
    ) -> InvocationView:
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
        )
        self._check_deadline(view)
        self._issued[view.invocation_id] = view
        return view

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
                return view
            parent = self._issued.get(current.parent_id)
            if parent is None:
                raise InvalidInvocation("Parent invocation is no longer active")
            current = parent

    def derive(
        self,
        parent: InvocationView,
        *,
        module_id: str,
        module_epoch: int,
        deadline: float | None = None,
    ) -> InvocationView:
        """Enter a declared dependency without upgrading origin or identity.

        Capability dependencies, target epochs and scope narrowing must be
        authorized by the caller before deriving. There is deliberately no
        parameter that can change actor, grant, subscription, or origin.
        """
        self.require(parent)
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
        )
        self._check_deadline(view)
        self._issued[view.invocation_id] = view
        return view

    def release(self, view: InvocationView) -> None:
        """Release a completed request and its descendants, including expired ones."""
        if not isinstance(view, InvocationView):
            raise InvalidInvocation("Unrecognized invocation")
        if self._issued.get(view.invocation_id) is not view:
            raise InvalidInvocation("Cannot release an unissued invocation")
        removed = {view.invocation_id}
        while True:
            children = {
                key for key, item in self._issued.items() if item.parent_id in removed
            }
            expanded = removed | children
            if expanded == removed:
                break
            removed = expanded
        for key in removed:
            self._issued.pop(key, None)

    def _check_deadline(self, view: InvocationView) -> None:
        if view.deadline is not None and view.deadline <= self._clock():
            raise InvalidInvocation("Invocation deadline has expired")
