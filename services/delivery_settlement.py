"""Exact authority to settle a delivery whose external IO already started.

This is deliberately passed to terminal repository methods, never installed as
an invocation guard. It cannot authorize another send or arbitrary SQL work.
"""

from dataclasses import dataclass

from .runtime_effect_gate import EffectRejected, current_effect_guard


@dataclass(frozen=True, slots=True)
class DeliveryCompletionIdentity:
    kind: str
    key: tuple


def event_completion_identity(
    event_key,
    event_version,
    subscription_id,
    subscription_revision,
    attempt_number,
    started_at,
    idempotency_key,
):
    return DeliveryCompletionIdentity(
        "event",
        (
            event_key,
            event_version,
            subscription_id,
            subscription_revision,
            attempt_number,
            started_at,
            idempotency_key,
        ),
    )


def digest_completion_identity(
    window_id,
    recipient,
    claim_token,
    claimed_at,
    expires_at,
    revision,
    attempt_number,
    started_at,
    idempotency_key,
):
    return DeliveryCompletionIdentity(
        "digest",
        (
            window_id,
            recipient,
            claim_token,
            claimed_at,
            expires_at,
            revision,
            attempt_number,
            started_at,
            idempotency_key,
        ),
    )


@dataclass(frozen=True, slots=True)
class DeliveryCompletionPermit:
    """Retain one executing owner ticket through actual IO and receipt commit."""

    identity: DeliveryCompletionIdentity
    _operation: object

    def __post_init__(self):
        if type(self.identity) is not DeliveryCompletionIdentity:
            raise TypeError("exact delivery identity required")

    @classmethod
    def capture(cls, identity, *, guard=None):
        guard = current_effect_guard() or guard
        if guard is None:
            return None
        operation = guard.capture()
        try:
            operation.mark_executing()
            return cls(identity, operation)
        except BaseException:
            operation.finish()
            raise

    def assert_exact(self, identity):
        if (
            type(identity) is not DeliveryCompletionIdentity
            or identity != self.identity
        ):
            raise EffectRejected("delivery_completion_mismatch")
        operation = self._operation
        gate = operation.gate
        with gate.kernel.lock:
            record = gate.kernel._record(gate.scope)
            if (
                operation.finished
                or not operation.executing
                or record.token != gate.identity.core_token
                or record.epoch != gate.identity.core_epoch
                or record.tickets.get(operation.ticket) != "executing"
            ):
                raise EffectRejected("delivery_completion_expired")

    def finish(self):
        self._operation.finish()
