"""Reload-surviving Core owner coordinator; no host or SDK imports.

The sys.modules anchor intentionally retains this kernel and its old class
after bootstrap modules are purged. All owner operations use a short thread
lock; user callbacks and coroutine work run outside it.
"""

from __future__ import annotations

import _imp
import asyncio
import re
import secrets
import sys
import threading
import types
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from ..infrastructure.runtime_owner_lock import (
    RuntimeOwnerLock,
    RuntimeOwnerLockError,
)

_ANCHOR = "_ygl_runtime_coordinator_v1"
_SOURCE = "ygl.bootstrap.coordinator.v1"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class CoordinatorConflict(RuntimeError):
    def __init__(self, code: str = "coordinator_conflict") -> None:
        self.code = code
        super().__init__(code)


class _Scope:
    def __init__(self, root: str, context: object, lock: RuntimeOwnerLock) -> None:
        self.root = root
        self.context = context
        self.file_lock = lock
        self.epoch = 0
        self.owner: object | None = None
        self.token: str | None = None
        self.candidate_digest: str | None = None
        self.fenced = False
        self.close_failed = False
        self.gates: list[Callable[[], None]] = []
        self.tickets: dict[str, str] = {}
        self.cleanup: asyncio.Task[Any] | None = None
        self.cleanup_id: str | None = None
        self.durable_confirmed = False
        self.quiet_check: Callable[[str, object], Mapping[str, Any]] | None = None


class CoordinatorKernelV1:
    protocol_major = 1
    source_marker = _SOURCE

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.scopes: dict[str, _Scope] = {}
        self.epoch_watermarks: dict[str, int] = {}
        self.boot_id = secrets.token_hex(32)

    @staticmethod
    def _root(scope: tuple[str | Path, object]) -> tuple[str, object]:
        if type(scope) is not tuple or len(scope) != 2 or scope[1] is None:
            raise CoordinatorConflict("invalid_scope")
        root = str(Path(scope[0]).resolve(strict=True))
        if not Path(root).is_dir():
            raise CoordinatorConflict("invalid_scope")
        return root, scope[1]

    def _record(self, scope: tuple[str | Path, object]) -> _Scope:
        root, context = self._root(scope)
        record = self.scopes.get(root)
        if record is None or record.context is not context:
            raise CoordinatorConflict()
        return record

    @staticmethod
    def _quiet_state(record: _Scope) -> bool:
        if (
            record.owner is None
            or not record.fenced
            or record.close_failed
            or record.cleanup is None
            or not record.cleanup.done()
            or record.cleanup.cancelled()
            or record.tickets
            or not record.durable_confirmed
            or record.quiet_check is None
        ):
            return False
        try:
            if record.cleanup.exception() is not None:
                return False
        except BaseException:
            return False
        return True

    @staticmethod
    def _valid_proof(proof: Mapping[str, Any], token: str) -> bool:
        return (
            type(proof) is dict
            and proof.get("owner_token") == token
            and type(proof.get("tracked_inflight")) is int
            and proof["tracked_inflight"] == 0
            and proof.get("cleanup_complete") is True
            and proof.get("durable_fence") is True
            and proof.get("resources_closed") is True
        )

    def _probe_quiet(self, record: _Scope) -> bool:
        """Run the integrator probe without holding the coordinator lock."""
        with self.lock:
            if not self._quiet_state(record):
                return False
            token, owner, check = record.token, record.owner, record.quiet_check
        try:
            proof = check(token, owner)
        except BaseException:
            return False
        with self.lock:
            return (
                self.scopes.get(record.root) is record
                and record.token == token
                and record.owner is owner
                and self._quiet_state(record)
                and self._valid_proof(proof, token)
            )

    async def acquire_core(
        self,
        scope: tuple[str | Path, object],
        owner: object,
        expected_epoch: int,
        candidate_digest: str,
    ) -> dict[str, Any]:
        """CAS acquire. Even a repeated request from the same owner parks.

        The initial token is delivered only once; a repeated caller must keep
        its original result rather than using acquire as a token lookup.
        """
        if (
            owner is None
            or type(expected_epoch) is not int
            or expected_epoch < 0
            or type(candidate_digest) is not str
            or _DIGEST.fullmatch(candidate_digest) is None
        ):
            return {"state": "rejected", "core_epoch": 0, "error": "invalid_input"}
        root, context = self._root(scope)
        with self.lock:
            old = self.scopes.get(root)
        quiet_old = old is not None and self._probe_quiet(old)
        with self.lock:
            record = self.scopes.get(root)
            if record is None:
                if expected_epoch != self.epoch_watermarks.get(root, 0):
                    return {
                        "state": "rejected",
                        "core_epoch": self.epoch_watermarks.get(root, 0),
                        "error": "stale_epoch",
                    }
                try:
                    process_lock = RuntimeOwnerLock(root)
                except RuntimeOwnerLockError as exc:
                    return {
                        "state": "rejected",
                        "core_epoch": expected_epoch,
                        "error": exc.code,
                    }
                record = _Scope(root, context, process_lock)
                record.epoch = self.epoch_watermarks.get(root, 0)
                self.scopes[root] = record
            elif record.context is not context:
                return {
                    "state": "rejected",
                    "core_epoch": record.epoch,
                    "error": "coordinator_conflict",
                }
            if expected_epoch != record.epoch:
                return {
                    "state": "rejected",
                    "core_epoch": record.epoch,
                    "error": "stale_epoch",
                }
            if record.owner is not None and (record is not old or not quiet_old):
                result = {"state": "parked", "core_epoch": record.epoch}
                if record.cleanup_id:
                    result["cleanup_id"] = record.cleanup_id
                return result
            record.epoch += 1
            self.epoch_watermarks[root] = record.epoch
            record.owner = owner
            record.token = secrets.token_hex(32)
            record.candidate_digest = candidate_digest
            record.fenced = False
            record.close_failed = False
            record.gates = []
            record.tickets = {}
            record.cleanup = None
            record.cleanup_id = None
            record.durable_confirmed = False
            record.quiet_check = None
            return {
                "state": "acquired",
                "core_epoch": record.epoch,
                "owner_token": record.token,
            }

    def register_gate(
        self,
        scope: tuple[str | Path, object],
        owner_token: str,
        close: Callable[[], None],
    ) -> bool:
        with self.lock:
            record = self._record(scope)
            if record.token != owner_token or record.fenced:
                return False
            record.gates.append(close)
            return True

    def queue_ticket(
        self, scope: tuple[str | Path, object], owner_token: str
    ) -> str | None:
        with self.lock:
            record = self._record(scope)
            if record.token != owner_token or record.fenced:
                return None
            ticket = secrets.token_hex(32)
            record.tickets[ticket] = "queued"
            return ticket

    def mark_executing(
        self, scope: tuple[str | Path, object], owner_token: str, ticket: str
    ) -> bool:
        """Atomic queued-to-executing gate with sync_fence ordering."""
        with self.lock:
            record = self._record(scope)
            if record.token != owner_token or record.fenced:
                return False
            if record.tickets.get(ticket) != "queued":
                return False
            record.tickets[ticket] = "executing"
            return True

    def finish_ticket(
        self, scope: tuple[str | Path, object], owner_token: str, ticket: str
    ) -> bool:
        with self.lock:
            record = self._record(scope)
            if record.token != owner_token or ticket not in record.tickets:
                return False
            del record.tickets[ticket]
            return True

    def sync_fence(
        self,
        scope: tuple[str | Path, object],
        owner_token: str,
        cleanup_factory: Callable[[], Awaitable[Any]],
        durable_fence_factory: Callable[[], Awaitable[bool]],
        quiet_check: Callable[[str, object], Mapping[str, Any]],
    ) -> str | None:
        """Close admission synchronously, then retain exactly one cleanup task."""
        with self.lock:
            record = self._record(scope)
            if record.token != owner_token:
                return None
            if record.fenced:
                return record.cleanup_id
            record.fenced = True
            record.cleanup_id = secrets.token_hex(16)
            record.quiet_check = quiet_check
            gates = tuple(record.gates)
            cleanup_id = record.cleanup_id
        failed = False
        for close in gates:
            try:
                close()
            except BaseException:
                failed = True
        if failed:
            with self.lock:
                record.close_failed = True

        async def run_cleanup() -> None:
            durable = await durable_fence_factory()
            if durable is not True:
                raise CoordinatorConflict("durable_fence_unconfirmed")
            with self.lock:
                record.durable_confirmed = True
            await cleanup_factory()

        task = asyncio.get_running_loop().create_task(run_cleanup())

        def observe_failure(completed: asyncio.Task[Any]) -> None:
            # Reading the exception suppresses an un-retrieved-task warning.
            # The task and its exact outcome remain retained on the record.
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(observe_failure)
        with self.lock:
            record.cleanup = task
        return cleanup_id

    async def await_quiet(
        self,
        scope: tuple[str | Path, object],
        owner_token: str,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            record = self._record(scope)
            if record.token != owner_token:
                return {"state": "rejected", "error": "stale_owner"}
            task = record.cleanup
            cleanup_id = record.cleanup_id
        if task is None:
            return {"state": "parked", "cleanup_id": cleanup_id}
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if asyncio.current_task().cancelling():
                raise
        except BaseException:
            pass
        with self.lock:
            if record.token != owner_token:
                return {"state": "rejected", "error": "stale_owner"}
        quiet = self._probe_quiet(record)
        with self.lock:
            return {
                "state": "quiet" if quiet and record.token == owner_token else "parked",
                "core_epoch": record.epoch,
                "cleanup_id": cleanup_id,
            }

    def release_scope(self, scope: tuple[str | Path, object], owner_token: str) -> bool:
        """Explicitly release only a proven quiet scope, retaining its epoch watermark."""
        with self.lock:
            record = self._record(scope)
        if not self._probe_quiet(record):
            return False
        with self.lock:
            if (
                record.token != owner_token
                or self.scopes.get(record.root) is not record
            ):
                return False
            del self.scopes[record.root]
            record.file_lock.close()
            return True


def get_coordinator() -> CoordinatorKernelV1:
    """Return the one process kernel, validating an occupied anchor first."""
    _imp.acquire_lock()
    try:
        if _ANCHOR not in sys.modules:
            anchor = types.ModuleType(_ANCHOR)
            anchor.__dict__.update(
                source_marker=_SOURCE,
                protocol_major=1,
                kernel=CoordinatorKernelV1(),
            )
            sys.modules[_ANCHOR] = anchor
        else:
            anchor = sys.modules[_ANCHOR]
        data = vars(anchor) if type(anchor) is types.ModuleType else {}
        kernel = data.get("kernel")
        if (
            data.get("source_marker") != _SOURCE
            or data.get("protocol_major") != 1
            or getattr(kernel, "source_marker", None) != _SOURCE
            or getattr(kernel, "protocol_major", None) != 1
            or not isinstance(getattr(kernel, "lock", None), type(threading.RLock()))
            or type(getattr(kernel, "scopes", None)) is not dict
            or type(getattr(kernel, "epoch_watermarks", None)) is not dict
            or not isinstance(getattr(kernel, "boot_id", None), str)
            or any(
                not callable(getattr(kernel, name, None))
                for name in (
                    "acquire_core",
                    "register_gate",
                    "queue_ticket",
                    "mark_executing",
                    "finish_ticket",
                    "sync_fence",
                    "await_quiet",
                    "release_scope",
                )
            )
        ):
            raise CoordinatorConflict()
        return kernel
    finally:
        _imp.release_lock()
