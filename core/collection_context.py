"""Validation boundary for collector invocation contexts.

Collectors receive a :class:`CollectionView` as data, but may only obtain
scope-sensitive services after the core verifies the invocation view that was
issued for that collection.  This module intentionally contains no storage,
network, or authorization backend; those checks remain the responsibility of
the bound service implementation at each operation.
"""

from __future__ import annotations

from collections.abc import Callable
from math import isfinite
from time import monotonic

from yomihime_game_link_sdk.contexts import InvocationOrigin, InvocationView
from yomihime_game_link_sdk.storage import OwnershipKind
from yomihime_game_link_sdk.subscriptions import CollectionView

from ..core.contracts.validation_boundary import validate_contract
from .context_issuer import ContextIssuer, InvalidInvocation


def require_collection_context(
    issuer: ContextIssuer,
    context: CollectionView,
    *,
    clock: Callable[[], float] = monotonic,
) -> InvocationView:
    """Validate and return the exact issuer-owned view for a collection.

    The returned object is the one to pass to ``ModuleServices.scopes.bind``.
    A missing view remains constructible for compatibility with old DTO users,
    but it is never accepted at this authority boundary.
    """

    validate_contract(context)
    if not isinstance(context, CollectionView):
        raise InvalidInvocation("Unrecognized collection context")
    invocation = context.invocation
    if invocation is None:
        raise InvalidInvocation("Collection context has no invocation")

    # This also rejects forged copies, released views, expired invocations, and
    # views issued by another ContextIssuer before any scope comparison.
    view = issuer.require(invocation)
    if view.origin is not InvocationOrigin.SCHEDULER:
        raise InvalidInvocation("Collection requires a scheduler invocation")
    if view.module_id != context.key.module_id:
        raise InvalidInvocation("Collection module does not match invocation")

    scope = context.key.scope
    if scope.kind is OwnershipKind.PUBLIC:
        # A public key can be initiated by a subscription owner, but it must
        # never carry a private authorization grant into shared collection.
        if view.grant_id is not None or view.grant_revision is not None:
            raise InvalidInvocation("Public collection cannot carry a grant")
    elif scope.kind is OwnershipKind.USER:
        if view.actor_id != scope.user_id:
            raise InvalidInvocation("Collection owner does not match invocation")
        if view.grant_id is not None or view.grant_revision is not None:
            raise InvalidInvocation("User collection cannot carry a grant")
    elif scope.kind is OwnershipKind.AUTHORIZED:
        grant = scope.grant
        if (
            view.actor_id != scope.user_id
            or grant is None
            or view.grant_id != grant.grant_id
            or view.grant_revision != grant.revision
        ):
            raise InvalidInvocation("Collection authorization does not match")
    else:  # pragma: no cover - OwnerScope validates this at construction.
        raise InvalidInvocation("Unknown collection ownership scope")

    collection_deadline = context.deadline_monotonic
    invocation_deadline = view.deadline
    if invocation_deadline is not None and collection_deadline is None:
        raise InvalidInvocation("Collection must declare the invocation deadline")
    if collection_deadline is not None:
        if not isfinite(collection_deadline) or collection_deadline <= 0:
            raise InvalidInvocation("Collection deadline is invalid")
        if (
            invocation_deadline is not None
            and collection_deadline > invocation_deadline
        ):
            raise InvalidInvocation("Collection deadline exceeds invocation")
        if collection_deadline <= clock():
            raise InvalidInvocation("Collection deadline has expired")

    return view
