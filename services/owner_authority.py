"""Core-private proof for owner-only command results and operations."""

from __future__ import annotations

from dataclasses import dataclass

from ..api.contexts import InvocationOrigin, InvocationView
from ..api.subscriptions import ConversationKind, ConversationRef
from ..core.context_issuer import ContextIssuer
from ..core.ports import AdmissionLease, AdmissionPort
from .identity import InvocationPrincipalResolver


class OwnerRouteProofError(PermissionError):
    """The exact owner invocation, principal, admission, or route is stale."""


@dataclass(frozen=True, slots=True)
class _OwnerRouteProof:
    invocation: InvocationView
    lease: AdmissionLease
    actor_id: str
    principal_id: str
    route: ConversationRef


class OwnerRouteProofAuthority:
    """Capture and revalidate one private owner/route binding per invocation.

    This is a cooperative Core boundary, not a Python sandbox. The authority
    itself stays in Core composition and is never included in SDK services,
    handler arguments, invocation DTOs, or output DTOs.
    """

    __slots__ = (
        "_issuer",
        "_admission",
        "_principals",
        "_conversations",
        "_proofs",
    )

    def __init__(
        self,
        issuer: ContextIssuer,
        admission: AdmissionPort,
        principal_resolver: InvocationPrincipalResolver,
        conversations: object,
    ) -> None:
        if not isinstance(issuer, ContextIssuer):
            raise TypeError("owner proof requires the Core ContextIssuer")
        if not callable(getattr(admission, "check", None)):
            raise TypeError("owner proof requires the Core Admission port")
        if not callable(getattr(principal_resolver, "principal_id", None)):
            raise TypeError("owner proof requires a principal resolver")
        if not callable(getattr(conversations, "resolve", None)):
            raise TypeError("owner proof requires a trusted route resolver")
        self._issuer = issuer
        self._admission = admission
        self._principals = principal_resolver
        self._conversations = conversations
        self._proofs: dict[str, _OwnerRouteProof] = {}

    async def capture(self, invocation: InvocationView, lease: AdmissionLease) -> None:
        """Freeze a direct owner binding before the selected handler runs."""

        self._require_capture_view(invocation, lease)
        if invocation.invocation_id in self._proofs:
            raise OwnerRouteProofError("owner proof already exists")
        # The supported identity and conversation repositories are append-only
        # for these keys: an existing principal mapping or route can only be
        # re-saved with the same value. Route publication additionally shares
        # Admission's mutation gate with final output approval. A second pair
        # of reads would not make these awaits atomic, so the proof records the
        # trusted values once and relies on the repository/write-path contract
        # plus the exact-value revalidation at later authority checkpoints.
        principal_id = await self._principal(invocation)
        route = await self._route(invocation)
        self._require_capture_view(invocation, lease)
        proof = _OwnerRouteProof(
            invocation,
            lease,
            invocation.actor_id,
            principal_id,
            route,
        )
        if invocation.invocation_id in self._proofs:
            raise OwnerRouteProofError("owner proof already exists")
        self._proofs[invocation.invocation_id] = proof

    async def require_current(self, invocation: InvocationView) -> str:
        """Return the frozen principal only while identity and route still match."""

        proof = self._proofs.get(getattr(invocation, "invocation_id", ""))
        if proof is None or proof.invocation is not invocation:
            raise OwnerRouteProofError("owner proof is missing")
        self._require_exact_view(invocation, proof)
        principal_id = await self._principal(invocation)
        route = await self._route(invocation)
        self._require_exact_view(invocation, proof)
        if principal_id != proof.principal_id or route != proof.route:
            raise OwnerRouteProofError("owner binding is no longer current")
        return proof.principal_id

    def release(self, invocation: InvocationView) -> None:
        """Revoke the exact invocation proof and release its retained objects."""

        if not isinstance(invocation, InvocationView):
            return
        proof = self._proofs.get(invocation.invocation_id)
        if proof is not None and proof.invocation is invocation:
            self._proofs.pop(invocation.invocation_id, None)

    def _require_capture_view(
        self, invocation: InvocationView, lease: AdmissionLease
    ) -> None:
        try:
            checked = self._issuer.require(invocation)
            if (
                checked is not invocation
                or invocation.origin is not InvocationOrigin.COMMAND
                or invocation.parent_id is not None
                or not invocation.actor_id
                or invocation.adapter_id is None
                or invocation.conversation_id is None
                or invocation.capability_id is None
                or invocation.grant_id is not None
                or invocation.grant_revision is not None
                or not isinstance(lease, AdmissionLease)
                or self._issuer.lease_for(invocation) is not lease
                or lease.invocation_id != invocation.invocation_id
                or lease.module_id != invocation.module_id
                or lease.module_epoch != invocation.module_epoch
                or lease.capability_id != invocation.capability_id
            ):
                raise ValueError
            self._admission.check(lease)
        except Exception:
            raise OwnerRouteProofError("owner command is not admitted") from None

    def _require_exact_view(
        self, invocation: InvocationView, proof: _OwnerRouteProof
    ) -> None:
        try:
            checked = self._issuer.require(invocation)
            if (
                checked is not proof.invocation
                or checked is not invocation
                or checked.invocation_id != proof.invocation.invocation_id
                or checked.module_id != proof.invocation.module_id
                or checked.module_epoch != proof.invocation.module_epoch
                or checked.capability_id != proof.invocation.capability_id
                or checked.actor_id != proof.actor_id
                or checked.origin is not InvocationOrigin.COMMAND
                or checked.parent_id is not None
                or checked.grant_id is not None
                or checked.grant_revision is not None
                or self._issuer.lease_for(checked) is not proof.lease
            ):
                raise ValueError
            self._admission.check(proof.lease)
        except Exception:
            raise OwnerRouteProofError(
                "owner invocation is no longer admitted"
            ) from None

    async def _principal(self, invocation: InvocationView) -> str:
        self._require_active_invocation(invocation)
        try:
            principal_id = await self._principals.principal_id(invocation)
        except Exception:
            raise OwnerRouteProofError("owner principal is unavailable") from None
        self._require_active_invocation(invocation)
        if type(principal_id) is not str or not principal_id.strip():
            raise OwnerRouteProofError("owner principal is unavailable")
        return principal_id

    async def _route(self, invocation: InvocationView) -> ConversationRef:
        self._require_active_invocation(invocation)
        try:
            route = await self._conversations.resolve(invocation)
        except Exception:
            route = None
        self._require_active_invocation(invocation)
        if (
            not isinstance(route, ConversationRef)
            or route.kind is not ConversationKind.DIRECT
            or route.adapter_id != invocation.adapter_id
            or route.conversation_id != invocation.conversation_id
        ):
            raise OwnerRouteProofError("owner route is not direct and current")
        return route

    def _require_active_invocation(self, invocation: InvocationView) -> None:
        try:
            if self._issuer.require(invocation) is not invocation:
                raise ValueError
            lease = self._issuer.lease_for(invocation)
            if not isinstance(lease, AdmissionLease):
                raise ValueError
            self._admission.check(lease)
        except Exception:
            raise OwnerRouteProofError(
                "owner invocation is no longer admitted"
            ) from None


__all__ = ["OwnerRouteProofAuthority", "OwnerRouteProofError"]
