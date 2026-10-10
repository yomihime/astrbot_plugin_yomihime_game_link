"""Persisted B04 delivery state machine for immediate events and digests.

All authority checks and rendering happen before host IO. Repository calls are
short CAS operations; no database transaction is held across a port await.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from yomihime_game_link_sdk.contexts import (
    InvocationConversationKind,
    InvocationSubscriptionScope,
)
from yomihime_game_link_sdk.display import (
    DisplayAudience,
    DisplayBatch,
    DisplayBatchMember,
    DisplayLimits,
    DisplayOutput,
    DisplayRenderer,
    Privacy,
)
from yomihime_game_link_sdk.storage import OwnershipKind
from yomihime_game_link_sdk.subscriptions import ConversationKind, ConversationRef

from ..core.admission import AdmissionError
from ..core.contracts.services import Grant, GrantStatus, TrustedPersistedRouteResolver
from ..core.contracts.subscriptions import (
    DeliveryAttempt,
    DeliveryEvent,
    DeliveryState,
    DigestEnvelope,
    DigestEnvelopeClaim,
    DigestEnvelopeState,
    DigestMemberDisposition,
    DigestMemberReceipt,
    digest_envelope_idempotency_key,
)
from ..core.contracts.validation_boundary import validate_contract
from ..core.ports import (
    AdmissionPort,
    ApprovedSendScheduler,
    DeliveryLease,
    DeliveryMemberIdentity,
    DeliveryRepository,
    DeliveryWorkKind,
    DigestWindowRepository,
    GrantStore,
    MessagePort,
    MessageReceipt,
    MessageStatus,
    MessageTarget,
    RenderedMessage,
    RevisionConflict,
    SendApproval,
    SendPermit,
    SubscriptionStore,
)
from ..core.registry import Registry, RegistryError

_LOG = logging.getLogger(__name__)
_ERROR_CODES = frozenset(
    {
        "subscription_unavailable",
        "subscription_cancelled",
        "subscription_revision_changed",
        "grant_unavailable",
        "module_unavailable",
        "route_unavailable",
        "privacy_rejected",
        "display_render_failed",
        "host_send_failed",
        "host_send_unknown",
        "host_send_timeout",
        "host_send_cancelled",
        "storage_finish_failed",
        "delivery_claim_expired",
    }
)


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    state: DeliveryState
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class _Eligibility:
    allowed: bool
    code: str | None = None
    module_epoch: int | None = None
    owner_id: str | None = None
    authorized: bool = False
    grant_expires_at: datetime | None = None
    record: object | None = None


RetryAt = Callable[[DeliveryEvent, datetime], datetime | None]


class DeliveryService:
    """Recheck saved recipients and saved documents before each delivery."""

    def __init__(
        self,
        *,
        deliveries: DeliveryRepository,
        subscriptions: SubscriptionStore,
        grants: GrantStore,
        windows: DigestWindowRepository,
        registry: Registry,
        admission: AdmissionPort,
        send_scheduler: ApprovedSendScheduler,
        routes: TrustedPersistedRouteResolver,
        renderer: DisplayRenderer,
        message_port: MessagePort,
        limits: DisplayLimits,
        now: Callable[[], datetime],
        claim_lease: timedelta = timedelta(minutes=2),
        send_timeout: float = 30.0,
        retry_at: RetryAt | None = None,
        page_size: int = 100,
    ) -> None:
        validate_contract(renderer)
        validate_contract(limits)
        if claim_lease.total_seconds() <= 0 or send_timeout <= 0 or page_size < 1:
            raise ValueError("delivery bounds must be positive")
        self._deliveries = deliveries
        self._subscriptions = subscriptions
        self._grants = grants
        self._windows = windows
        self._registry = registry
        self._admission = admission
        self._send_scheduler = send_scheduler
        self._routes = routes
        self._renderer = renderer
        self._message_port = message_port
        self._limits = limits
        self._now = now
        self._claim_lease = claim_lease
        self._send_timeout = send_timeout
        self._retry_at = retry_at or (lambda _event, _now: None)
        self._page_size = page_size

    async def recover_startup(
        self,
    ) -> tuple[tuple[DeliveryEvent, ...], tuple[DigestEnvelope, ...]]:
        """Convert every persisted in-flight send to UNKNOWN before due scans."""
        now = self._utc_now()
        stale_events = await self._deliveries.recover_stale_sending(before=now)
        stale_envelopes = await self._windows.recover_expired_envelope_claims(
            before=now, recovered_at=now
        )
        return stale_events, stale_envelopes

    async def dispatch_event(
        self, event_key: str, event_version: int, subscription_id: str, revision: int
    ) -> DeliveryResult:
        """Claim and send one exact subscriber delivery identity."""
        event = await self._deliveries.current_event(
            event_key,
            event_version,
            subscription_id=subscription_id,
            subscription_revision=revision,
        )
        if event is None:
            return DeliveryResult(DeliveryState.CANCELLED, "subscription_unavailable")
        now = self._utc_now()
        expected_state = event.state
        if expected_state not in (DeliveryState.PENDING, DeliveryState.FAILED):
            return DeliveryResult(expected_state)

        try:
            saved_subscription = await self._subscriptions.current(subscription_id)
        except Exception:
            saved_subscription = None
        saved_status = getattr(saved_subscription, "status", None)
        if (
            saved_subscription is not None
            and getattr(saved_subscription, "subscription_id", None) == subscription_id
            and getattr(saved_subscription, "revision", None) == revision
            and getattr(saved_status, "value", None) == "active"
            and getattr(saved_subscription, "notification_mode", None) == "digest"
        ):
            # Digest events remain pending for the envelope path. Do not let a
            # direct event request terminal-cancel a valid digest member.
            return DeliveryResult(expected_state)

        first = await self._eligibility(
            event.subscription_id,
            event.subscription_revision,
            event.owner_id,
            event.grant,
            event.recipient,
            event.display_data.privacy,
            now,
        )
        if not first.allowed:
            return await self._finish_without_send_result(
                event, first.code or "subscription_unavailable", now
            )

        try:
            lease, member = self._delivery_lease(
                event,
                first,
                work_id=event.idempotency_key,
                kind=DeliveryWorkKind.EVENT,
            )
        except Exception:
            return await self._finish_without_send_result(
                event, "module_unavailable", self._utc_now()
            )

        try:
            rendered = await self._renderer.render(
                event.display_data,
                limits=self._limits,
                audience=(
                    DisplayAudience.PRIVATE
                    if event.display_data.privacy is Privacy.PRIVATE
                    else DisplayAudience.PUBLIC
                ),
            )
            if type(rendered) is not DisplayOutput:
                raise TypeError("renderer must return DisplayOutput")
            validate_contract(rendered)
        except asyncio.CancelledError:
            self._admission.release(lease)
            raise
        except Exception:
            _LOG.warning("delivery rejected code=display_render_failed")
            try:
                return await self._finish_without_send_result(
                    event, "display_render_failed", self._utc_now()
                )
            finally:
                self._admission.release(lease)
        try:
            self._admission.check(lease)
        except AdmissionError:
            self._admission.release(lease)
            return await self._finish_without_send_result(
                event, "module_unavailable", self._utc_now()
            )
        except BaseException:
            self._admission.release(lease)
            raise

        # This second check catches changes while rendering. Capture and compare
        # the live module epoch so disable/re-enable cannot revive this attempt.
        try:
            second = await self._eligibility(
                event.subscription_id,
                event.subscription_revision,
                event.owner_id,
                event.grant,
                event.recipient,
                event.display_data.privacy,
                self._utc_now(),
                expected_epoch=first.module_epoch,
            )
        except BaseException:
            self._admission.release(lease)
            raise
        if not second.allowed:
            try:
                return await self._finish_without_send_result(
                    event,
                    second.code or "subscription_unavailable",
                    self._utc_now(),
                )
            finally:
                self._admission.release(lease)
        target = MessageTarget(
            event.recipient.conversation_id,
            event.owner_id if event.grant is not None else None,
            event.recipient,
            event.grant is not None,
        )
        payload = RenderedMessage(rendered.text, rendered.resource_ids)
        state: dict[str, object] = {}
        task_name = f"delivery:{uuid4().hex}"

        async def final_check() -> SendApproval | None:
            final = await self._eligibility(
                event.subscription_id,
                event.subscription_revision,
                event.owner_id,
                event.grant,
                event.recipient,
                event.display_data.privacy,
                self._utc_now(),
                expected_epoch=first.module_epoch,
            )
            if not final.allowed:
                state["result"] = await self._finish_without_send_result(
                    event,
                    final.code or "subscription_unavailable",
                    self._utc_now(),
                )
                return None
            started = self._utc_now()
            attempt_number = (event.attempt.attempt_number if event.attempt else 0) + 1
            try:
                claimed = await self._deliveries.claim_sending(
                    event.event_key,
                    event.event_version,
                    subscription_id=event.subscription_id,
                    subscription_revision=event.subscription_revision,
                    expected_state=expected_state,
                    attempt_number=attempt_number,
                    started_at=started,
                    now=started,
                )
            except asyncio.CancelledError:
                await _drain_cleanup(
                    self._reconcile_cancelled_event_claim(
                        event, attempt_number, started
                    )
                )
                raise
            except Exception:
                await _drain_cleanup(
                    self._reconcile_cancelled_event_claim(
                        event, attempt_number, started
                    )
                )
                state["result"] = DeliveryResult(
                    DeliveryState.UNKNOWN, "storage_finish_failed"
                )
                return None
            if claimed is None:
                current = await self._deliveries.current_event(
                    event.event_key,
                    event.event_version,
                    subscription_id=event.subscription_id,
                    subscription_revision=event.subscription_revision,
                )
                if current is not None and current.state in (
                    DeliveryState.PENDING,
                    DeliveryState.FAILED,
                ):
                    latest = await self._eligibility(
                        current.subscription_id,
                        current.subscription_revision,
                        current.owner_id,
                        current.grant,
                        current.recipient,
                        current.display_data.privacy,
                        self._utc_now(),
                        expected_epoch=first.module_epoch,
                    )
                    if not latest.allowed:
                        code = latest.code or "subscription_unavailable"
                        state["result"] = await self._finish_without_send_result(
                            current, code, self._utc_now()
                        )
                        return None
                state["result"] = DeliveryResult(
                    current.state if current else DeliveryState.CANCELLED
                )
                return None
            state["sending"] = claimed
            token = uuid4().hex
            state["abort_token"] = token
            approval = SendApproval(token, (member,))
            # The CAS await may cross the Grant's natural expiry. Do not issue a
            # permit if its exact saved expiry elapsed while SQLite committed.
            if (
                final.grant_expires_at is not None
                and final.grant_expires_at <= self._utc_now()
            ):
                await _drain_cleanup(
                    _abort_event_send(claimed, self._deliveries, self._utc_now())
                )
                state["result"] = DeliveryResult(
                    DeliveryState.CANCELLED, "grant_unavailable"
                )
                return None
            return approval

        async def abort_before_dispatch(
            approval: SendApproval, _error_code: str
        ) -> None:
            claimed = state.get("sending")
            if isinstance(claimed, DeliveryEvent) and approval.claim_id == state.get(
                "abort_token"
            ):
                await _abort_event_send(claimed, self._deliveries, self._utc_now())

        async def sender(permit: SendPermit) -> object:
            if permit.lease_id != lease.lease_id:
                raise RuntimeError("delivery permit does not match its lease")
            claimed = state.get("sending")
            if not isinstance(claimed, DeliveryEvent):
                raise RuntimeError("delivery has no persisted SENDING attempt")
            grant_expires_at = None

            async def eligible() -> bool:
                nonlocal grant_expires_at
                check = await self._eligibility(
                    claimed.subscription_id,
                    claimed.subscription_revision,
                    claimed.owner_id,
                    claimed.grant,
                    claimed.recipient,
                    claimed.display_data.privacy,
                    self._utc_now(),
                    expected_epoch=first.module_epoch,
                )
                current = await self._deliveries.is_current_for_send(claimed)
                grant_expires_at = check.grant_expires_at
                return (
                    current
                    and check.allowed
                    and (
                        check.grant_expires_at is None
                        or check.grant_expires_at > self._utc_now()
                    )
                )

            def deadlines_current() -> bool:
                return grant_expires_at is None or grant_expires_at > self._utc_now()

            return await self._send_event(
                claimed, target, payload, lease, state, eligible, deadlines_current
            )

        result = await self._approve_and_wait(
            lease,
            task_name,
            final_check,
            sender,
            abort_before_dispatch,
            state,
        )
        if (
            result.state is DeliveryState.UNKNOWN
            and result.error_code == "host_send_timeout"
        ):
            return await self._mark_event_timeout_unknown(state, event)
        return result

    async def dispatch_due_events(self, *, now: datetime | None = None) -> int:
        """Drain one stable cursor cycle of persisted pending/retryable events."""
        scan_now = self._utc_now() if now is None else self._as_utc(now)
        cursor = None
        processed = 0
        while True:
            page = await self._deliveries.list_due_events(
                now=scan_now, limit=self._page_size, after_cursor=cursor
            )
            if not page:
                return processed
            for candidate in page:
                cursor = candidate.cursor
                await self.dispatch_event(
                    candidate.event_key,
                    candidate.event_version,
                    candidate.subscription_id,
                    candidate.subscription_revision,
                )
                processed += 1

    async def dispatch_due_digests(self, *, now: datetime | None = None) -> int:
        """Recover claims, then drain due persisted route candidates once."""
        scan_now = self._utc_now() if now is None else self._as_utc(now)
        await self._windows.recover_expired_envelope_claims(
            before=scan_now, recovered_at=scan_now
        )
        cursor = None
        processed = 0
        while True:
            page = await self._windows.list_due_routes(
                now=scan_now, limit=self._page_size, after_cursor=cursor
            )
            if not page:
                return processed
            for candidate in page:
                cursor = candidate.cursor
                window = await self._windows.get(candidate.window_id)
                if (
                    window is None
                    or window.due_at > scan_now
                    or candidate.due_at != window.due_at
                ):
                    continue
                result = await self.dispatch_digest(
                    candidate.window_id, candidate.recipient, now=scan_now
                )
                if result is not None:
                    processed += 1

    async def dispatch_digest(
        self, window_id: str, recipient: ConversationRef, *, now: datetime | None = None
    ) -> DeliveryResult | None:
        """Claim, prune and render a digest before its exact last-send approval."""
        validate_contract(recipient)
        current_time = self._utc_now() if now is None else self._as_utc(now)
        window = await self._windows.get(window_id)
        if window is None or window.due_at > current_time:
            return None
        saved = await self._windows.current_envelope(window_id, recipient)
        if saved is not None and saved.state is DigestEnvelopeState.FAILED:
            if saved.retry_at is None or saved.retry_at > current_time:
                return None
            reopened = await self._windows.retry_failed_envelope(
                saved.envelope_id,
                expected_revision=saved.revision,
                now=current_time,
            )
            if reopened is None:
                return None

        for rebuild in range(4):
            claim_now = self._utc_now() if now is None else self._as_utc(now)
            claim = await self._windows.claim_due_envelope(
                window_id,
                recipient,
                now=claim_now,
                lease_expires_at=claim_now + self._claim_lease,
            )
            if claim is None:
                return None
            envelope = claim.envelope
            candidates: list[tuple[object, DeliveryEvent, _Eligibility]] = []
            receipts: list[DigestMemberReceipt] = []
            for association in envelope.member_associations:
                event = association.event
                check = await self._eligibility(
                    event.subscription_id,
                    event.subscription_revision,
                    event.owner_id,
                    event.grant,
                    event.recipient,
                    event.display_data.privacy,
                    self._utc_now(),
                )
                receipts.append(
                    DigestMemberReceipt(
                        association.member,
                        self._disposition(check.code)
                        if not check.allowed
                        else DigestMemberDisposition.INCLUDED,
                    )
                )
                if check.allowed:
                    candidates.append((association.member, event, check))
            reconciled = await self._windows.reconcile_envelope_members(
                claim, tuple(receipts), expected_revision=envelope.revision
            )
            if reconciled.state is DigestEnvelopeState.CANCELLED:
                return DeliveryResult(DeliveryState.CANCELLED, "subscription_cancelled")
            retained = {item.member for item in reconciled.member_associations}
            candidates = [item for item in candidates if item[0] in retained]
            if not candidates:
                return DeliveryResult(DeliveryState.CANCELLED, "subscription_cancelled")

            owners = {event.owner_id for _, event, _ in candidates}
            if len(owners) != 1:
                return await self._finish_digest_without_send(
                    claim, reconciled, "route_unavailable", current_time
                )
            lease, member_ids = self._delivery_lease_for(
                tuple((event, check) for _, event, check in candidates),
                work_id=digest_envelope_idempotency_key(window_id, recipient),
                kind=DeliveryWorkKind.DIGEST,
            )
            member_identities = {
                member: identity
                for (member, _, _), identity in zip(candidates, member_ids)
            }

            rendered = None
            stable: list[tuple[object, DeliveryEvent, _Eligibility]] = []
            try:
                while candidates:
                    self._admission.check(lease)
                    batch = validate_contract(
                        DisplayBatch(
                            tuple(
                                validate_contract(
                                    DisplayBatchMember(member, event.display_data)
                                )
                                for member, event, _ in candidates
                            ),
                            DisplayAudience.PRIVATE
                            if any(
                                event.display_data.privacy is Privacy.PRIVATE
                                for _, event, _ in candidates
                            )
                            else DisplayAudience.PUBLIC,
                        )
                    )
                    try:
                        rendered = await self._renderer.render_batch(
                            batch, self._limits
                        )
                        if type(rendered) is not DisplayOutput:
                            raise TypeError("renderer must return DisplayOutput")
                        validate_contract(rendered)
                    except Exception:
                        _LOG.warning("delivery rejected code=display_render_failed")
                        return await self._finish_digest_without_send(
                            claim, reconciled, "display_render_failed", self._utc_now()
                        )
                    self._admission.check(lease)

                    checked: list[tuple[object, DeliveryEvent, _Eligibility]] = []
                    receipts = []
                    for member, event, prior_check in candidates:
                        check = await self._eligibility(
                            event.subscription_id,
                            event.subscription_revision,
                            event.owner_id,
                            event.grant,
                            event.recipient,
                            event.display_data.privacy,
                            self._utc_now(),
                            expected_epoch=prior_check.module_epoch,
                        )
                        self._admission.check(lease)
                        receipts.append(
                            DigestMemberReceipt(
                                member,
                                self._disposition(check.code)
                                if not check.allowed
                                else DigestMemberDisposition.INCLUDED,
                            )
                        )
                        if check.allowed:
                            checked.append((member, event, check))
                    next_envelope = await self._windows.reconcile_envelope_members(
                        claim,
                        tuple(receipts),
                        expected_revision=reconciled.revision,
                    )
                    self._admission.check(lease)
                    if next_envelope.state is DigestEnvelopeState.CANCELLED:
                        return DeliveryResult(
                            DeliveryState.CANCELLED, "subscription_cancelled"
                        )
                    next_retained = {
                        item.member for item in next_envelope.member_associations
                    }
                    checked = [item for item in checked if item[0] in next_retained]
                    reconciled = next_envelope
                    if not checked:
                        return DeliveryResult(
                            DeliveryState.CANCELLED, "subscription_cancelled"
                        )
                    if len(checked) == len(candidates):
                        stable = checked
                        break
                    # A member was removed while rendering. Throw away the old
                    # payload and render only the surviving saved documents.
                    candidates = checked
                if not stable or rendered is None:
                    return DeliveryResult(
                        DeliveryState.CANCELLED, "subscription_cancelled"
                    )

                owner = next(iter(owners))
                authorized = any(event.grant is not None for _, event, _ in stable)
                target = MessageTarget(
                    recipient.conversation_id,
                    owner if authorized else None,
                    recipient,
                    authorized,
                )
                state: dict[str, object] = {}
                task_name = f"digest:{uuid4().hex}"
                stable_members = tuple(
                    member_identities[member] for member, _, _ in stable
                )

                async def final_check() -> SendApproval | None:
                    final_checks: list[tuple[object, DeliveryEvent, _Eligibility]] = []
                    final_receipts: list[DigestMemberReceipt] = []
                    for member, event, prior_check in stable:
                        check = await self._eligibility(
                            event.subscription_id,
                            event.subscription_revision,
                            event.owner_id,
                            event.grant,
                            event.recipient,
                            event.display_data.privacy,
                            self._utc_now(),
                            expected_epoch=prior_check.module_epoch,
                        )
                        final_receipts.append(
                            DigestMemberReceipt(
                                member,
                                self._disposition(check.code)
                                if not check.allowed
                                else DigestMemberDisposition.INCLUDED,
                            )
                        )
                        if check.allowed:
                            final_checks.append((member, event, check))
                    final_envelope = await self._windows.reconcile_envelope_members(
                        claim,
                        tuple(final_receipts),
                        expected_revision=reconciled.revision,
                    )
                    if final_envelope.state is DigestEnvelopeState.CANCELLED:
                        state["result"] = DeliveryResult(
                            DeliveryState.CANCELLED, "subscription_cancelled"
                        )
                        return None
                    retained_members = {
                        item.member for item in final_envelope.member_associations
                    }
                    final_checks = [
                        item for item in final_checks if item[0] in retained_members
                    ]
                    if not final_checks:
                        state["result"] = DeliveryResult(
                            DeliveryState.CANCELLED, "subscription_cancelled"
                        )
                        return None
                    if len(final_checks) != len(stable):
                        # The render no longer describes the envelope. Persist a
                        # known-not-sent attempt to release this claim, then the
                        # outer loop obtains a fresh claim and renders again.
                        started = self._utc_now()
                        attempt_number = len(final_envelope.delivery_attempts) + 1
                        try:
                            sending = await self._windows.begin_envelope_send(
                                claim,
                                expected_revision=final_envelope.revision,
                                attempt_number=attempt_number,
                                started_at=started,
                            )
                        except asyncio.CancelledError:
                            await _drain_cleanup(
                                self._reconcile_cancelled_digest_claim(
                                    claim, attempt_number, started
                                )
                            )
                            raise
                        except Exception:
                            await _drain_cleanup(
                                self._reconcile_cancelled_digest_claim(
                                    claim, attempt_number, started
                                )
                            )
                            state["result"] = DeliveryResult(
                                DeliveryState.UNKNOWN, "storage_finish_failed"
                            )
                            return None
                        if sending is not None:
                            state["sending"] = sending
                            try:
                                await self._abort_digest_send(
                                    claim, sending, "cancelled_before_dispatch"
                                )
                            except asyncio.CancelledError:
                                await _drain_cleanup(
                                    self._reconcile_cancelled_digest_claim(
                                        claim, attempt_number, started
                                    )
                                )
                                raise
                            except Exception:
                                await _drain_cleanup(
                                    self._reconcile_cancelled_digest_claim(
                                        claim, attempt_number, started
                                    )
                                )
                                state["result"] = DeliveryResult(
                                    DeliveryState.UNKNOWN, "storage_finish_failed"
                                )
                                return None
                        state["rerender"] = True
                        state["result"] = DeliveryResult(
                            DeliveryState.CANCELLED,
                            "subscription_revision_changed",
                        )
                        return None

                    started = self._utc_now()
                    attempt_number = len(final_envelope.delivery_attempts) + 1
                    try:
                        sending = await self._windows.begin_envelope_send(
                            claim,
                            expected_revision=final_envelope.revision,
                            attempt_number=attempt_number,
                            started_at=started,
                        )
                    except asyncio.CancelledError:
                        await _drain_cleanup(
                            self._reconcile_cancelled_digest_claim(
                                claim, attempt_number, started
                            )
                        )
                        raise
                    except Exception:
                        await _drain_cleanup(
                            self._reconcile_cancelled_digest_claim(
                                claim, attempt_number, started
                            )
                        )
                        state["result"] = DeliveryResult(
                            DeliveryState.UNKNOWN, "storage_finish_failed"
                        )
                        return None
                    if sending is None:
                        state["result"] = DeliveryResult(
                            DeliveryState.CANCELLED, "subscription_unavailable"
                        )
                        return None
                    state["sending"] = sending
                    state["claim"] = claim
                    token = uuid4().hex
                    state["abort_token"] = token
                    approval = SendApproval(token, stable_members)
                    # begin_envelope_send is the final normal-path await; compare
                    # the persisted claim and every saved Grant synchronously
                    # before issuing permission.
                    if claim.expires_at <= self._utc_now():
                        await _drain_cleanup(
                            self._abort_digest_send(
                                claim, sending, "delivery_claim_expired"
                            )
                        )
                        state["result"] = DeliveryResult(
                            DeliveryState.CANCELLED, "delivery_claim_expired"
                        )
                        return None
                    if any(
                        item.grant_expires_at is not None
                        and item.grant_expires_at <= self._utc_now()
                        for _, _, item in final_checks
                    ):
                        await _drain_cleanup(
                            self._abort_digest_send(claim, sending, "grant_unavailable")
                        )
                        # Abort this known-unsent attempt, then claim the READY
                        # envelope again. The next eligibility pass persists
                        # the expired member's removal and renders the remaining
                        # members from their saved documents.
                        state["rerender"] = True
                        state["result"] = DeliveryResult(
                            DeliveryState.CANCELLED, "grant_unavailable"
                        )
                        return None
                    state["sending"] = sending
                    state["reconciled"] = final_envelope
                    return approval

                async def abort_before_dispatch(
                    approval: SendApproval, _error_code: str
                ) -> None:
                    sending = state.get("sending")
                    if isinstance(
                        sending, DigestEnvelope
                    ) and approval.claim_id == state.get("abort_token"):
                        await self._abort_digest_send(
                            claim, sending, "cancelled_before_dispatch"
                        )

                async def sender(permit: SendPermit) -> object:
                    if permit.lease_id != lease.lease_id:
                        raise RuntimeError("digest permit does not match its lease")
                    sending = state.get("sending")
                    if not isinstance(sending, DigestEnvelope):
                        raise RuntimeError("digest has no persisted SENDING attempt")
                    grant_expiries = ()

                    async def eligible() -> bool:
                        nonlocal grant_expiries
                        checks = []
                        for _, event, prior in stable:
                            checks.append(
                                await self._eligibility(
                                    event.subscription_id,
                                    event.subscription_revision,
                                    event.owner_id,
                                    event.grant,
                                    event.recipient,
                                    event.display_data.privacy,
                                    self._utc_now(),
                                    expected_epoch=prior.module_epoch,
                                )
                            )
                        current = await self._windows.is_current_for_send(
                            claim, sending, now=self._utc_now()
                        )
                        grant_expiries = tuple(item.grant_expires_at for item in checks)
                        return (
                            current
                            and claim.expires_at > self._utc_now()
                            and all(
                                item.allowed
                                and (
                                    item.grant_expires_at is None
                                    or item.grant_expires_at > self._utc_now()
                                )
                                for item in checks
                            )
                        )

                    def deadlines_current() -> bool:
                        now = self._utc_now()
                        return claim.expires_at > now and all(
                            expiry is None or expiry > now for expiry in grant_expiries
                        )

                    return await self._send_digest(
                        claim,
                        sending,
                        target,
                        rendered,
                        lease,
                        state,
                        eligible,
                        deadlines_current,
                    )

                try:
                    result = await self._approve_and_wait(
                        lease,
                        task_name,
                        final_check,
                        sender,
                        abort_before_dispatch,
                        state,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    return DeliveryResult(
                        DeliveryState.CANCELLED, "cancelled_before_dispatch"
                    )
                if (
                    result.state is DeliveryState.UNKNOWN
                    and result.error_code == "host_send_timeout"
                ):
                    result = await self._mark_digest_timeout_unknown(state, claim)
                if state.get("rerender") is True:
                    if rebuild < 3:
                        continue
                value = state.get("result")
                if (
                    isinstance(value, DeliveryResult)
                    and result.state is DeliveryState.CANCELLED
                ):
                    return value
                return result
            finally:
                # Once approve_and_wait schedules a task, its done callback owns
                # lease release even if this caller timed out or was cancelled.
                if not getattr(
                    self._send_scheduler, "has_owned_task", lambda _name: False
                )(locals().get("task_name", "")):
                    self._admission.release(lease)

        return DeliveryResult(DeliveryState.CANCELLED, "subscription_revision_changed")

    async def _abort_digest_send(
        self,
        claim: DigestEnvelopeClaim,
        sending: DigestEnvelope,
        error_code: str,
    ) -> None:
        if not sending.delivery_attempts:
            return
        prior = sending.delivery_attempts[-1]
        attempt = DeliveryAttempt(
            prior.attempt_number,
            DeliveryState.FAILED,
            prior.idempotency_key,
            prior.started_at,
            self._utc_now(),
            "cancelled_before_dispatch",
        )
        await self._windows.abort_envelope_send(
            claim, attempt, expected_revision=sending.revision
        )

    async def _reconcile_cancelled_digest_claim(
        self,
        claim: DigestEnvelopeClaim,
        attempt_number: int,
        started_at: datetime,
    ) -> None:
        try:
            current = await self._windows.current_envelope(
                claim.envelope.window_id, claim.envelope.recipient
            )
        except Exception:
            return
        if (
            current is None
            or current.state is not DigestEnvelopeState.SENDING
            or not current.delivery_attempts
        ):
            return
        attempt = current.delivery_attempts[-1]
        if (
            attempt.attempt_number != attempt_number
            or attempt.started_at != started_at
            or attempt.state is not DeliveryState.SENDING
        ):
            return
        await self._abort_digest_send(claim, current, "cancelled_before_dispatch")

    async def _eligibility(
        self,
        subscription_id: str,
        revision: int,
        owner_id: str,
        grant_ref,
        recipient: ConversationRef,
        privacy: Privacy,
        now: datetime,
        *,
        expected_epoch: int | None = None,
    ) -> _Eligibility:
        try:
            validate_contract(recipient)
            validate_contract(privacy)
        except (TypeError, ValueError):
            return _Eligibility(False, "privacy_rejected")
        try:
            record = await self._subscriptions.current(subscription_id)
        except Exception:
            return _Eligibility(False, "subscription_unavailable")
        if record is None or record.status.value != "active":
            return _Eligibility(False, "subscription_cancelled")
        if record.revision != revision:
            return _Eligibility(False, "subscription_revision_changed")
        if (
            record.owner_id != owner_id
            or record.recipient != recipient
            or record.grant != grant_ref
        ):
            return _Eligibility(False, "subscription_revision_changed")
        scope = record.collection_key.scope
        authorized = scope.kind is OwnershipKind.AUTHORIZED
        if authorized:
            if (
                grant_ref is None
                or recipient.kind is not ConversationKind.DIRECT
                or privacy is not Privacy.PRIVATE
            ):
                return _Eligibility(False, "privacy_rejected")
        elif privacy is Privacy.PRIVATE and scope.kind is OwnershipKind.PUBLIC:
            return _Eligibility(False, "privacy_rejected")

        grant_expiry = None
        if grant_ref is not None:
            try:
                grant = await self._grants.current_grant(grant_ref.grant_id)
            except Exception:
                grant = None
            if (
                not isinstance(grant, Grant)
                or grant.status is not GrantStatus.ACTIVE
                or grant.revision != grant_ref.revision
                or grant.principal_id != owner_id
                or grant.module_id != record.module_id
                or grant.expires_at is not None
                and grant.expires_at <= self._utc_now()
            ):
                return _Eligibility(False, "grant_unavailable")
            grant_expiry = grant.expires_at

        try:
            module = self._registry.snapshot().module(record.module_id)
        except (RegistryError, Exception):
            return _Eligibility(False, "module_unavailable")
        if not module.enabled or (
            expected_epoch is not None and module.epoch != expected_epoch
        ):
            return _Eligibility(False, "module_unavailable")

        try:
            if authorized:
                trusted = await self._routes.resolve_private(owner_id, recipient)
                if recipient.kind is not ConversationKind.DIRECT:
                    trusted = None
            else:
                trusted = await self._routes.resolve_current(owner_id, recipient)
        except Exception:
            trusted = None
        if trusted != recipient:
            return _Eligibility(False, "route_unavailable")

        # Route attestation is an await where a command can cancel/revise the
        # subscription or revoke its Grant. Reread every mutable authority after
        # that await so the resolver cannot make a stale pre-await read look
        # current at the message boundary.
        try:
            current = await self._subscriptions.current(subscription_id)
        except Exception:
            return _Eligibility(False, "subscription_unavailable")
        if current is None or current.status.value != "active":
            return _Eligibility(False, "subscription_cancelled")
        if (
            current.revision != revision
            or current.owner_id != owner_id
            or current.recipient != recipient
            or current.grant != grant_ref
            or current.module_id != record.module_id
            or current.collection_key != record.collection_key
        ):
            return _Eligibility(False, "subscription_revision_changed")
        if grant_ref is not None:
            try:
                grant = await self._grants.current_grant(grant_ref.grant_id)
            except Exception:
                grant = None
            if (
                not isinstance(grant, Grant)
                or grant.status is not GrantStatus.ACTIVE
                or grant.revision != grant_ref.revision
                or grant.principal_id != owner_id
                or grant.module_id != current.module_id
                or (
                    grant.expires_at is not None and grant.expires_at <= self._utc_now()
                )
            ):
                return _Eligibility(False, "grant_unavailable")
            grant_expiry = grant.expires_at
        try:
            current_module = self._registry.snapshot().module(current.module_id)
        except Exception:
            return _Eligibility(False, "module_unavailable")
        if (
            not current_module.enabled
            or current_module.epoch != module.epoch
            or (expected_epoch is not None and current_module.epoch != expected_epoch)
        ):
            return _Eligibility(False, "module_unavailable")
        return _Eligibility(
            True,
            None,
            module.epoch,
            owner_id,
            authorized,
            grant_expiry,
            current,
        )

    def _delivery_lease(
        self,
        event: DeliveryEvent,
        eligibility: _Eligibility,
        *,
        work_id: str,
        kind: DeliveryWorkKind,
    ) -> tuple[DeliveryLease, DeliveryMemberIdentity]:
        lease, members = self._delivery_lease_for(
            ((event, eligibility),), work_id=work_id, kind=kind
        )
        return lease, members[0]

    def _delivery_lease_for(
        self,
        values: tuple[tuple[DeliveryEvent, _Eligibility], ...],
        *,
        work_id: str,
        kind: DeliveryWorkKind,
    ) -> tuple[DeliveryLease, tuple[DeliveryMemberIdentity, ...]]:
        if not values:
            raise ValueError("delivery lease requires at least one member")
        identities: list[DeliveryMemberIdentity] = []
        records = []
        for event, eligibility in values:
            record = eligibility.record
            if record is None:
                raise ValueError("delivery member has no current subscription")
            records.append(record)
            scope = record.collection_key.scope.kind
            identities.append(
                DeliveryMemberIdentity(
                    event_key=event.event_key,
                    event_version=event.event_version,
                    subscription_id=event.subscription_id,
                    subscription_revision=event.subscription_revision,
                    owner_id=event.owner_id,
                    module_id=record.module_id,
                    grant_id=None if event.grant is None else event.grant.grant_id,
                    grant_revision=None
                    if event.grant is None
                    else event.grant.revision,
                    adapter_id=event.recipient.adapter_id,
                    conversation_id=event.recipient.conversation_id,
                    delivery_route=event.recipient.delivery_route,
                    conversation_kind=validate_contract(
                        InvocationConversationKind(event.recipient.kind.value)
                    ),
                    subscription_scope=validate_contract(
                        InvocationSubscriptionScope(scope.value)
                    ),
                )
            )
        first = records[0]
        if any(
            record.module_id != first.module_id
            or record.collection_key.collector_id != first.collection_key.collector_id
            or record.collection_key.key_version != first.collection_key.key_version
            for record in records[1:]
        ):
            raise ValueError("digest members do not share one collector declaration")
        lease = self._admission.admit_delivery(
            work_id=work_id,
            kind=kind,
            module_id=first.module_id,
            collector_id=first.collection_key.collector_id,
            key_version=first.collection_key.key_version,
            members=tuple(identities),
        )
        return lease, tuple(identities)

    async def _approve_and_wait(
        self,
        lease: DeliveryLease,
        task_name: str,
        final_check,
        sender,
        abort_before_dispatch,
        state: dict[str, object],
    ) -> DeliveryResult:
        result_future: asyncio.Future[DeliveryResult] = (
            asyncio.get_running_loop().create_future()
        )
        observe = getattr(self._send_scheduler, "observe_completion", None)
        clear_observer = getattr(
            self._send_scheduler, "clear_completion_observer", None
        )
        has_owned_task = getattr(self._send_scheduler, "has_owned_task", None)
        if not all(
            callable(item) for item in (observe, clear_observer, has_owned_task)
        ):
            self._admission.release(lease)
            raise TypeError("delivery requires the Lifecycle-owned send scheduler")

        async def complete_unstarted() -> None:
            approval = SendApproval(str(state["abort_token"]), lease.members)
            try:
                await abort_before_dispatch(approval, "cancelled_before_dispatch")
                result = DeliveryResult(
                    DeliveryState.CANCELLED, "cancelled_before_dispatch"
                )
            except BaseException:
                result = DeliveryResult(DeliveryState.UNKNOWN, "storage_finish_failed")
            finally:
                self._admission.release(lease)
            if not result_future.done():
                result_future.set_result(result)

        def completed(_task) -> None:
            if not result_future.done() and not state.get("sender_started"):
                # A Lifecycle-owned task can be cancelled before its first instruction.
                state["completion_cleanup"] = asyncio.create_task(complete_unstarted())
            else:
                self._admission.release(lease)

        observe(task_name, completed)

        async def run_sender(permit: SendPermit) -> object:
            state["sender_started"] = True
            work = asyncio.create_task(sender(permit))
            state["send_work"] = work
            try:
                result = await asyncio.shield(work)
                if not isinstance(result, DeliveryResult):
                    result = DeliveryResult(
                        DeliveryState.UNKNOWN, "storage_finish_failed"
                    )
            except asyncio.CancelledError:
                work.cancel()
                result = await _wait_protected(work)
                if not state.get("entered"):
                    await _drain_cleanup(
                        abort_before_dispatch(
                            SendApproval(str(state["abort_token"]), lease.members),
                            "cancelled_before_dispatch",
                        )
                    )
                if not isinstance(result, DeliveryResult):
                    result = state.get("result")
                if not isinstance(result, DeliveryResult):
                    result = DeliveryResult(
                        DeliveryState.UNKNOWN
                        if state.get("entered")
                        else DeliveryState.CANCELLED,
                        "host_send_cancelled"
                        if state.get("entered")
                        else "cancelled_before_dispatch",
                    )
                if not result_future.done():
                    result_future.set_result(result)
                raise
            except Exception:
                result = DeliveryResult(DeliveryState.UNKNOWN, "host_send_unknown")
            if not result_future.done():
                result_future.set_result(result)
            return result

        try:
            permit = await self._admission.approve_and_schedule_send(
                lease,
                name=task_name,
                final_check=final_check,
                sender=run_sender,
                scheduler=self._send_scheduler,
                abort_before_dispatch=abort_before_dispatch,
            )
        except BaseException:
            clear_observer(task_name)
            if not has_owned_task(task_name):
                self._admission.release(lease)
            raise
        if permit is None:
            clear_observer(task_name)
            self._admission.release(lease)
            result = state.get("result")
            if isinstance(result, DeliveryResult):
                return result
            return DeliveryResult(DeliveryState.CANCELLED, "subscription_unavailable")
        try:
            return await asyncio.wait_for(
                asyncio.shield(result_future), timeout=self._send_timeout
            )
        except TimeoutError:
            state["stop"] = True
            child = state.get("io_child")
            if isinstance(child, asyncio.Task):
                child.cancel()
            return await _wait_protected(result_future)
        except asyncio.CancelledError:
            state["stop"] = True
            child = state.get("io_child")
            if isinstance(child, asyncio.Task):
                child.cancel()
            await _wait_protected(result_future)
            raise

    async def _finish_without_send(
        self, event: DeliveryEvent, code: str, completed_at: datetime
    ) -> None:
        state = (
            DeliveryState.SENDING
            if event.state is DeliveryState.SENDING
            else event.state
        )
        if state not in (
            DeliveryState.PENDING,
            DeliveryState.FAILED,
            DeliveryState.SENDING,
        ):
            return
        attempt_number = (event.attempt.attempt_number if event.attempt else 0) + 1
        attempt = DeliveryAttempt(
            attempt_number,
            DeliveryState.CANCELLED
            if code != "display_render_failed"
            else DeliveryState.FAILED,
            event.idempotency_key,
            event.attempt.started_at
            if state is DeliveryState.SENDING and event.attempt
            else completed_at,
            completed_at,
            code,
        )
        await self._deliveries.record_attempt(
            event.event_key,
            event.event_version,
            attempt,
            subscription_id=event.subscription_id,
            subscription_revision=event.subscription_revision,
            expected_state=state,
            retry_at=self._retry_at(event, completed_at)
            if attempt.state is DeliveryState.FAILED
            else None,
        )

    async def _finish_without_send_result(
        self, event: DeliveryEvent, code: str, completed_at: datetime
    ) -> DeliveryResult:
        try:
            await self._finish_without_send(event, code, completed_at)
        except RevisionConflict:
            # A protected digest history or a competing dispatcher won the
            # repository CAS. Report the persisted outcome rather than a
            # synthetic terminal cancellation.
            current = await self._deliveries.current_event(
                event.event_key,
                event.event_version,
                subscription_id=event.subscription_id,
                subscription_revision=event.subscription_revision,
            )
            if current is None:
                return DeliveryResult(DeliveryState.CANCELLED, code)
            return DeliveryResult(current.state)
        result_state = (
            DeliveryState.FAILED
            if code == "display_render_failed"
            else DeliveryState.CANCELLED
        )
        return DeliveryResult(result_state, code)

    async def _finish_attempt(
        self, event: DeliveryEvent, attempt: DeliveryAttempt
    ) -> None:
        try:
            await self._deliveries.record_attempt(
                event.event_key,
                event.event_version,
                attempt,
                subscription_id=event.subscription_id,
                subscription_revision=event.subscription_revision,
                expected_state=DeliveryState.SENDING,
            )
        except Exception:
            _LOG.error("delivery finish failed code=storage_finish_failed")
            raise

    async def _reconcile_cancelled_event_claim(
        self,
        event: DeliveryEvent,
        attempt_number: int,
        started_at: datetime,
    ) -> None:
        try:
            current = await self._deliveries.current_event(
                event.event_key,
                event.event_version,
                subscription_id=event.subscription_id,
                subscription_revision=event.subscription_revision,
            )
        except Exception:
            return
        if (
            current is None
            or current.state is not DeliveryState.SENDING
            or current.attempt is None
            or current.attempt.attempt_number != attempt_number
            or current.attempt.started_at != started_at
        ):
            return
        await _abort_event_send(current, self._deliveries, self._utc_now())

    async def _mark_event_timeout_unknown(
        self, state: dict[str, object], event: DeliveryEvent
    ) -> DeliveryResult:
        """Persist timeout ambiguity immediately while the scoped sender may finish."""
        sending = state.get("sending")
        if not isinstance(sending, DeliveryEvent) or sending.attempt is None:
            return DeliveryResult(DeliveryState.UNKNOWN, "host_send_timeout")
        try:
            current = await self._deliveries.current_event(
                event.event_key,
                event.event_version,
                subscription_id=event.subscription_id,
                subscription_revision=event.subscription_revision,
            )
        except Exception:
            return DeliveryResult(DeliveryState.UNKNOWN, "host_send_timeout")
        if current is None or current.attempt != sending.attempt:
            return DeliveryResult(DeliveryState.UNKNOWN, "host_send_timeout")
        if current.state is not DeliveryState.SENDING:
            return DeliveryResult(current.state, "host_send_timeout")
        return await self._finish_event_receipt(
            current, MessageStatus.UNKNOWN, "host_send_timeout", None
        )

    async def _mark_digest_timeout_unknown(
        self,
        state: dict[str, object],
        claim: DigestEnvelopeClaim,
    ) -> DeliveryResult:
        """Record route-send ambiguity without cancelling a possibly active host call."""
        sending = state.get("sending")
        if not isinstance(sending, DigestEnvelope) or not sending.delivery_attempts:
            return DeliveryResult(DeliveryState.UNKNOWN, "host_send_timeout")
        prior = sending.delivery_attempts[-1]
        try:
            current = await self._windows.current_envelope(
                sending.window_id, sending.recipient
            )
        except Exception:
            return DeliveryResult(DeliveryState.UNKNOWN, "host_send_timeout")
        if current is None or not current.delivery_attempts:
            return DeliveryResult(DeliveryState.UNKNOWN, "host_send_timeout")
        actual = current.delivery_attempts[-1]
        if actual != prior:
            return DeliveryResult(DeliveryState.UNKNOWN, "host_send_timeout")
        if current.state is not DigestEnvelopeState.SENDING:
            state_by_envelope = {
                DigestEnvelopeState.SENT: DeliveryState.SENT,
                DigestEnvelopeState.FAILED: DeliveryState.FAILED,
                DigestEnvelopeState.UNKNOWN: DeliveryState.UNKNOWN,
                DigestEnvelopeState.CANCELLED: DeliveryState.CANCELLED,
            }
            return DeliveryResult(
                state_by_envelope.get(current.state, DeliveryState.UNKNOWN),
                "host_send_timeout",
            )
        return await self._finish_digest_receipt(
            claim,
            current,
            prior,
            MessageStatus.UNKNOWN,
            "host_send_timeout",
            None,
        )

    async def _start_io(
        self,
        lease: DeliveryLease,
        state: dict[str, object],
        eligible,
        deadlines_current,
        abort,
        target: MessageTarget,
        payload: RenderedMessage,
    ) -> asyncio.Task | None:
        """Hold mutation only through proof and the real MessagePort entry handshake."""
        async with self._admission.mutation("subscription-send-start"):
            try:
                self._admission.check(lease)
                allowed = await eligible()
                self._admission.check(lease)
            except asyncio.CancelledError:
                await _drain_cleanup(abort())
                raise
            except Exception:
                allowed = False
            if not allowed or state.get("stop"):
                await _drain_cleanup(abort())
                return None
            entered = asyncio.get_running_loop().create_future()

            async def io():
                # This is the child's first instruction: natural expiry and
                # cancellation may advance after task creation while it queues.
                try:
                    self._admission.check(lease)
                    if state.get("stop") or not deadlines_current():
                        return None
                except Exception:
                    return None
                state["entered"] = True
                entered.set_result(None)
                return await self._message_port.send(target, payload)

            child = asyncio.create_task(io())
            state["io_child"] = child
            try:
                await asyncio.wait(
                    (entered, child), return_when=asyncio.FIRST_COMPLETED
                )
            except asyncio.CancelledError:
                if not state.get("entered"):
                    child.cancel()
                    await _wait_protected(child)
                    await _drain_cleanup(abort())
                raise
            if not state.get("entered"):
                await _wait_protected(child)
                await _drain_cleanup(abort())
                return None
            return child

    async def _send_event(
        self,
        event: DeliveryEvent,
        target: MessageTarget,
        payload: RenderedMessage,
        lease: DeliveryLease,
        state: dict[str, object],
        eligible,
        deadlines_current,
    ) -> DeliveryResult:
        try:
            child = await self._start_io(
                lease,
                state,
                eligible,
                deadlines_current,
                lambda: _abort_event_send(event, self._deliveries, self._utc_now()),
                target,
                payload,
            )
            if child is None:
                return DeliveryResult(
                    DeliveryState.CANCELLED, "cancelled_before_dispatch"
                )
            async with asyncio.timeout(self._send_timeout):
                receipt = await asyncio.shield(child)
        except asyncio.CancelledError:
            child = state.get("io_child")
            receipt = None
            if isinstance(child, asyncio.Task):
                child.cancel()
                receipt = await _wait_protected(child)
            if not state.get("entered"):
                await _drain_cleanup(
                    _abort_event_send(event, self._deliveries, self._utc_now())
                )
                raise
            status, code, message_id = _receipt_fields(receipt, "host_send_cancelled")
            state["result"] = await _drain_cleanup(
                self._finish_event_receipt(event, status, code, message_id)
            )
            raise
        except TimeoutError:
            child.cancel()
            receipt = await _wait_protected(child)
            status, code, message_id = _receipt_fields(receipt, "host_send_timeout")
            return await self._finish_event_receipt(event, status, code, message_id)
        except Exception:
            _LOG.warning("delivery send unresolved code=host_send_unknown")
            await self._finish_event_receipt(
                event, MessageStatus.UNKNOWN, "host_send_unknown", None
            )
            return DeliveryResult(DeliveryState.UNKNOWN, "host_send_unknown")
        if not isinstance(receipt, MessageReceipt):
            return await self._finish_event_receipt(
                event, MessageStatus.UNKNOWN, "host_send_unknown", None
            )
        return await self._finish_event_receipt(
            event,
            receipt.status,
            None
            if receipt.status is MessageStatus.ACCEPTED
            else "host_send_failed"
            if receipt.status is MessageStatus.FAILED
            else "host_send_unknown",
            receipt.platform_message_id,
        )

    async def _finish_event_receipt(
        self,
        event: DeliveryEvent,
        status: MessageStatus,
        code: str | None,
        message_id: str | None,
    ) -> DeliveryResult:
        return await _wait_protected(
            asyncio.create_task(
                self._commit_event_receipt(event, status, code, message_id)
            )
        )

    async def _commit_event_receipt(
        self,
        event: DeliveryEvent,
        status: MessageStatus,
        code: str | None,
        message_id: str | None,
    ) -> DeliveryResult:
        state = {
            MessageStatus.ACCEPTED: DeliveryState.SENT,
            MessageStatus.FAILED: DeliveryState.FAILED,
            MessageStatus.UNKNOWN: DeliveryState.UNKNOWN,
        }[status]
        completed = self._utc_now()
        prior = event.attempt
        if prior is None or prior.state is not DeliveryState.SENDING:
            return DeliveryResult(DeliveryState.UNKNOWN, "storage_finish_failed")
        attempt = DeliveryAttempt(
            prior.attempt_number,
            state,
            event.idempotency_key,
            prior.started_at,
            completed,
            code if state is not DeliveryState.UNKNOWN else None,
            message_id,
        )
        retry = (
            self._retry_at(event, completed) if state is DeliveryState.FAILED else None
        )
        try:
            await self._deliveries.record_attempt(
                event.event_key,
                event.event_version,
                attempt,
                subscription_id=event.subscription_id,
                subscription_revision=event.subscription_revision,
                expected_state=DeliveryState.SENDING,
                retry_at=retry,
            )
        except Exception:
            _LOG.error("delivery finish failed code=storage_finish_failed")
            return DeliveryResult(DeliveryState.UNKNOWN, "storage_finish_failed")
        if code:
            _LOG.warning(
                "delivery completed code=%s",
                code if code in _ERROR_CODES else "host_send_unknown",
            )
        return DeliveryResult(state, code)

    async def _finish_digest_without_send(
        self,
        claim: DigestEnvelopeClaim,
        envelope: DigestEnvelope,
        code: str,
        completed_at: datetime,
    ) -> DeliveryResult:
        started = self._utc_now()
        attempt_number = len(envelope.delivery_attempts) + 1
        sending = await self._windows.begin_envelope_send(
            claim,
            expected_revision=envelope.revision,
            attempt_number=attempt_number,
            started_at=started,
        )
        if sending is None:
            return DeliveryResult(DeliveryState.CANCELLED, code)
        attempt = DeliveryAttempt(
            attempt_number,
            DeliveryState.FAILED,
            digest_envelope_idempotency_key(envelope.window_id, envelope.recipient),
            started,
            completed_at,
            code,
        )
        retry = self._retry_at_for_digest(envelope, completed_at)
        finished = await self._windows.complete_envelope_send(
            claim,
            attempt,
            expected_revision=sending.revision,
            retry_at=retry,
        )
        return DeliveryResult(
            DeliveryState.FAILED if finished else DeliveryState.UNKNOWN, code
        )

    async def _send_digest(
        self,
        claim: DigestEnvelopeClaim,
        envelope: DigestEnvelope,
        target: MessageTarget,
        output: DisplayOutput,
        lease: DeliveryLease,
        state: dict[str, object],
        eligible,
        deadlines_current,
    ) -> DeliveryResult:
        validate_contract(output)
        message = RenderedMessage(output.text, output.resource_ids)
        attempt = envelope.delivery_attempts[-1]
        try:
            child = await self._start_io(
                lease,
                state,
                eligible,
                deadlines_current,
                lambda: self._abort_digest_send(
                    claim, envelope, "cancelled_before_dispatch"
                ),
                target,
                message,
            )
            if child is None:
                return DeliveryResult(
                    DeliveryState.CANCELLED, "cancelled_before_dispatch"
                )
            async with asyncio.timeout(self._send_timeout):
                receipt = await asyncio.shield(child)
            if not isinstance(receipt, MessageReceipt):
                status, code, message_id = (
                    MessageStatus.UNKNOWN,
                    "host_send_unknown",
                    None,
                )
            else:
                status = receipt.status
                code = (
                    None
                    if status is MessageStatus.ACCEPTED
                    else "host_send_failed"
                    if status is MessageStatus.FAILED
                    else "host_send_unknown"
                )
                message_id = receipt.platform_message_id
        except asyncio.CancelledError:
            child = state.get("io_child")
            receipt = None
            if isinstance(child, asyncio.Task):
                child.cancel()
                receipt = await _wait_protected(child)
            if not state.get("entered"):
                await _drain_cleanup(
                    self._abort_digest_send(
                        claim, envelope, "cancelled_before_dispatch"
                    )
                )
                raise
            status, code, message_id = _receipt_fields(receipt, "host_send_cancelled")
            state["result"] = await _drain_cleanup(
                self._finish_digest_receipt(
                    claim,
                    envelope,
                    attempt,
                    status,
                    code,
                    message_id,
                )
            )
            raise
        except TimeoutError:
            child.cancel()
            receipt = await _wait_protected(child)
            status, code, message_id = _receipt_fields(receipt, "host_send_timeout")
            return await self._finish_digest_receipt(
                claim,
                envelope,
                attempt,
                status,
                code,
                message_id,
            )
        except Exception:
            _LOG.warning("delivery send unresolved code=host_send_unknown")
            return await self._finish_digest_receipt(
                claim,
                envelope,
                attempt,
                MessageStatus.UNKNOWN,
                "host_send_unknown",
                None,
            )
        return await self._finish_digest_receipt(
            claim, envelope, attempt, status, code, message_id
        )

    async def _finish_digest_receipt(
        self,
        claim: DigestEnvelopeClaim,
        envelope: DigestEnvelope,
        prior: DeliveryAttempt,
        status: MessageStatus,
        code: str | None,
        message_id: str | None,
    ) -> DeliveryResult:
        return await _wait_protected(
            asyncio.create_task(
                self._commit_digest_receipt(
                    claim, envelope, prior, status, code, message_id
                )
            )
        )

    async def _commit_digest_receipt(
        self,
        claim: DigestEnvelopeClaim,
        envelope: DigestEnvelope,
        prior: DeliveryAttempt,
        status: MessageStatus,
        code: str | None,
        message_id: str | None,
    ) -> DeliveryResult:
        state = {
            MessageStatus.ACCEPTED: DeliveryState.SENT,
            MessageStatus.FAILED: DeliveryState.FAILED,
            MessageStatus.UNKNOWN: DeliveryState.UNKNOWN,
        }[status]
        completed = self._utc_now()
        attempt = DeliveryAttempt(
            prior.attempt_number,
            state,
            prior.idempotency_key,
            prior.started_at,
            completed,
            code if state is not DeliveryState.UNKNOWN else None,
            message_id,
        )
        retry = (
            self._retry_at_for_digest(envelope, completed)
            if state is DeliveryState.FAILED
            else None
        )
        try:
            result = await self._windows.complete_envelope_send(
                claim,
                attempt,
                expected_revision=envelope.revision,
                retry_at=retry,
            )
        except Exception:
            _LOG.error("delivery finish failed code=storage_finish_failed")
            return DeliveryResult(DeliveryState.UNKNOWN, "storage_finish_failed")
        if result is None:
            return DeliveryResult(DeliveryState.UNKNOWN, "storage_finish_failed")
        return DeliveryResult(state, code)

    def _retry_at_for_digest(
        self, envelope: DigestEnvelope, completed: datetime
    ) -> datetime | None:
        if not envelope.member_associations:
            return None
        # A digest is one route-level send that can contain several subscriptions.
        # Apply the configured event retry policy to each retained member and use
        # the earliest explicit retry time; member authority is rechecked on the
        # next attempt, so revoked members are removed before any content is sent.
        retry_times: list[datetime] = []
        for association in envelope.member_associations:
            try:
                retry_at = self._retry_at(association.event, completed)
                if retry_at is not None:
                    retry_times.append(self._as_utc(retry_at))
            except (TypeError, ValueError, OverflowError):
                _LOG.warning(
                    "delivery retry policy rejected code=storage_finish_failed"
                )
                return None
            except Exception:
                _LOG.warning("delivery retry policy failed code=storage_finish_failed")
                return None
        return min(retry_times) if retry_times else None

    @staticmethod
    def _disposition(code: str | None) -> DigestMemberDisposition:
        if code == "subscription_cancelled":
            return DigestMemberDisposition.CANCELLED
        if code == "subscription_revision_changed":
            return DigestMemberDisposition.STALE_REVISION
        if code in ("grant_unavailable", "route_unavailable", "privacy_rejected"):
            return DigestMemberDisposition.UNAUTHORIZED
        return DigestMemberDisposition.MODULE_UNAVAILABLE

    def _utc_now(self) -> datetime:
        return self._as_utc(self._now())

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return an aware datetime")
        return value.astimezone(UTC)


async def _abort_event_send(
    event: DeliveryEvent, repository: DeliveryRepository, completed_at: datetime
) -> None:
    prior = event.attempt
    if event.state is not DeliveryState.SENDING or prior is None:
        return
    attempt = DeliveryAttempt(
        prior.attempt_number,
        DeliveryState.CANCELLED,
        prior.idempotency_key,
        prior.started_at,
        completed_at,
        "cancelled_before_dispatch",
    )
    await repository.record_attempt(
        event.event_key,
        event.event_version,
        attempt,
        subscription_id=event.subscription_id,
        subscription_revision=event.subscription_revision,
        expected_state=DeliveryState.SENDING,
    )


def _receipt_fields(receipt, unknown_code: str):
    if not isinstance(receipt, MessageReceipt):
        return MessageStatus.UNKNOWN, unknown_code, None
    return (
        receipt.status,
        None
        if receipt.status is MessageStatus.ACCEPTED
        else "host_send_failed"
        if receipt.status is MessageStatus.FAILED
        else "host_send_unknown",
        receipt.platform_message_id,
    )


async def _drain_cleanup(work) -> object | None:
    try:
        task = asyncio.create_task(work)
    except BaseException:
        close = getattr(work, "close", None)
        if callable(close):
            close()
        return None
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                try:
                    return task.result()
                except BaseException:
                    return None
        except Exception:
            return None


async def _wait_protected(task) -> object | None:
    """Keep an already-owned task alive through repeated cancellation until done."""
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                try:
                    return task.result()
                except BaseException:
                    return None
        except Exception:
            return None
