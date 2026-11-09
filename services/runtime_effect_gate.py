"""Core-owned execution leases; cooperative lifecycle fencing, not a sandbox.

A captured operation belongs to one immutable generation. Never replace it with
whatever generation happens to be current when a queue eventually drains.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


class EffectRejected(RuntimeError):
    def __init__(self, code: str = "stale_lease") -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class EffectIdentity:
    core_token: str = field(repr=False)
    core_epoch: int
    package_id: str | None = None
    package_token: str | None = field(default=None, repr=False)
    package_epoch: int = 0
    grant_revision: int = 0


class RuntimeEffectGate:
    def __init__(
        self,
        kernel,
        scope,
        core_token,
        core_epoch,
        *,
        package_id=None,
        package_token=None,
        package_epoch=0,
        grant_revision=0,
        authority_check: Callable[[], None] | None = None,
        policy_assert: Callable[[Any], None] | None = None,
        initially_open=True,
    ):
        if not core_token or type(core_epoch) is not int or core_epoch < 1:
            raise ValueError("invalid Core identity")
        if package_id is not None and (
            type(package_id) is not str
            or len(package_id) > 64
            or re.fullmatch(r"[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*", package_id) is None
            or type(package_token) is not str
            or re.fullmatch(r"[0-9a-f]{64}", package_token) is None
            or type(package_epoch) is not int
            or package_epoch < 1
            or authority_check is None
            or policy_assert is None
        ):
            raise ValueError("package requires identity and fresh policy checks")
        self.kernel, self.scope = kernel, scope
        self.identity = EffectIdentity(
            core_token,
            core_epoch,
            package_id,
            package_token,
            package_epoch,
            grant_revision,
        )
        self.authority_check, self.policy_assert = authority_check, policy_assert
        self._open = bool(initially_open)
        self._preparing = package_id is not None and not initially_open
        self.prepare_violation = False
        self._revoked = False
        if not kernel.register_gate(scope, core_token, self.close):
            self._open = False
            raise EffectRejected()

    def close(self):
        with self.kernel.lock:
            self._open = False
            self._revoked = True
            record = self.kernel._record(self.scope)
            # A closed lease is irreversible. Remove only its exact callback so
            # repeated package replacement does not grow the Core gate table.
            if record.token == self.identity.core_token:
                record.gates[:] = [
                    close for close in record.gates if close != self.close
                ]

    def open(self):
        """Integrator calls only after durable ACTIVE and route publication."""
        with self.kernel.lock:
            record = self.kernel._record(self.scope)
            if (
                self._revoked
                or record.fenced
                or record.token != self.identity.core_token
            ):
                raise EffectRejected()
            self._preparing = False
            self._open = True

    def check(self):
        if self._preparing and not self._revoked:
            self.prepare_violation = True
            raise EffectRejected("prepare_side_effect_forbidden")
        # Policy callbacks must be synchronous and run outside the kernel lock.
        if self.authority_check is not None:
            result = self.authority_check()
            if result is False or hasattr(result, "__await__"):
                raise EffectRejected("grant_revoked")
        with self.kernel.lock:
            self._check_locked()

    def _check_locked(self):
        record = self.kernel._record(self.scope)
        if (
            not self._open
            or record.fenced
            or record.token != self.identity.core_token
            or record.epoch != self.identity.core_epoch
        ):
            raise EffectRejected()

    def capture(self):
        self.check()
        with self.kernel.lock:
            self._check_locked()
            ticket = self.kernel.queue_ticket(self.scope, self.identity.core_token)
            if ticket is None:
                raise EffectRejected()
            return EffectOperation(self, ticket)


class EffectOperation:
    def __init__(self, gate, ticket):
        self.gate, self.ticket = gate, ticket
        self.executing = False
        self.finished = False

    def mark_executing(self):
        self.gate.check()
        with self.gate.kernel.lock:
            self.gate._check_locked()
            if self.finished or not self.gate.kernel.mark_executing(
                self.gate.scope, self.gate.identity.core_token, self.ticket
            ):
                raise EffectRejected()
            self.executing = True

    def check_sql(self, unit):
        from ..infrastructure.sqlite.runtime_ownership import assert_owner

        if not self.executing or self.finished:
            raise EffectRejected()
        identity = self.gate.identity
        assert_owner(unit, "core", identity.core_token, identity.core_epoch, 0)
        if identity.package_id is not None:
            assert_owner(
                unit,
                "package:" + identity.package_id,
                identity.package_token,
                identity.core_epoch,
                identity.package_epoch,
                identity.grant_revision,
            )
            self.gate.policy_assert(unit)

    def finish(self):
        with self.gate.kernel.lock:
            if not self.finished:
                self.gate.kernel.finish_ticket(
                    self.gate.scope, self.gate.identity.core_token, self.ticket
                )
                self.finished = True


_current: ContextVar[RuntimeEffectGate | None] = ContextVar(
    "runtime_effect_gate", default=None
)


def current_effect_guard():
    return _current.get()


@contextmanager
def invocation_guard(gate):
    token = _current.set(gate)
    try:
        yield
    finally:
        _current.reset(token)


def check_current_effect(default_guard=None):
    gate = current_effect_guard() or default_guard
    if gate is not None:
        gate.check()


async def run_effect(factory, *, guard=None):
    """Retain the actual child and ticket even when its waiter is canceled.

    Factories (rather than already-created coroutines) ensure a rejected lease
    cannot leave an unawaited coroutine or start a synchronous side effect.
    """
    guard = current_effect_guard() if guard is None else guard
    if guard is None:
        return await factory()
    operation = guard.capture()

    async def execute():
        try:
            with invocation_guard(guard):
                operation.mark_executing()
                return await factory()
        finally:
            if operation is not None:
                operation.finish()

    try:
        task = asyncio.create_task(execute())
    except BaseException:
        if operation is not None:
            operation.finish()
        raise
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if not task.cancelled():
            task.exception()
        raise
