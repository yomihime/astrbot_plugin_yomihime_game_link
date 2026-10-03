"""Finite, epoch-bound work scopes with bounded summaries."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from math import isfinite
from time import monotonic
from typing import Any, Protocol


class TaskObserver(Protocol):
    """Cooperative ownership hook for supported scope spawns, not raw asyncio."""

    def check(self) -> None: ...

    def register(self, task: asyncio.Task[Any]) -> None: ...


_TASK_OBSERVER: ContextVar[TaskObserver | None] = ContextVar(
    "task_observer", default=None
)


@contextmanager
def observe_tasks(observer: TaskObserver):
    token = _TASK_OBSERVER.set(observer)
    try:
        yield
    finally:
        _TASK_OBSERVER.reset(token)


class TaskScopeError(RuntimeError):
    """Base error for work that cannot continue in a scope."""


class ScopeClosedError(TaskScopeError):
    """The scope has already completed its cleanup."""


class ScopeCancelled(TaskScopeError):
    """The scope has received a cancellation request."""


class ScopeDeadlineExceeded(TaskScopeError):
    """The active work deadline elapsed."""


class ScopeStaleError(TaskScopeError):
    """The module runtime identity is no longer current."""


class ScopeStopTimeout(TimeoutError, TaskScopeError):
    """Tracked work did not finish within the finite cleanup window."""


@dataclass(frozen=True, slots=True)
class TaskOutcome:
    """A bounded diagnostic summary; never retains a Task or its result."""

    name: str
    status: str
    error_code: str | None = None
    cancelled: bool = False

    @property
    def result(self) -> None:
        """Compatibility view; completed values are deliberately not retained."""
        return None

    @property
    def error(self) -> None:
        """Compatibility view; exception objects and tracebacks are not retained."""
        return None


CurrentCheck = Callable[[], None]
CleanupTimeoutCallback = Callable[[str], None]


class TaskScope:
    """Own running tasks, enforce deadlines, and reject stale results.

    Cancelling an asyncio task cancels only the local await. It cannot retract
    work already accepted by an external system. A task that suppresses
    cancellation remains in the pending set after the cleanup bound and keeps
    its module isolated until it actually finishes.
    """

    HISTORY_LIMIT = 128
    DEFAULT_CLEANUP_TIMEOUT = 5.0

    def __init__(
        self,
        module_id: str,
        epoch: int,
        *,
        registry_revision: int | None = None,
        deadline_monotonic: float | None = None,
        clock: Callable[[], float] = monotonic,
        current_check: CurrentCheck | None = None,
        cleanup_timeout: float = DEFAULT_CLEANUP_TIMEOUT,
        history_limit: int = HISTORY_LIMIT,
        on_cleanup_timeout: CleanupTimeoutCallback | None = None,
    ) -> None:
        if type(module_id) is not str or not module_id.strip():
            raise ValueError("module_id must be non-empty text")
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
            raise ValueError("epoch must be a positive integer")
        if registry_revision is not None and (
            isinstance(registry_revision, bool)
            or not isinstance(registry_revision, int)
            or registry_revision < 0
        ):
            raise ValueError("registry_revision must be a non-negative integer")
        if deadline_monotonic is not None:
            _finite_timestamp(deadline_monotonic, "deadline_monotonic")
        _finite_timeout(cleanup_timeout, "cleanup_timeout")
        if (
            isinstance(history_limit, bool)
            or not isinstance(history_limit, int)
            or history_limit < 1
        ):
            raise ValueError("history_limit must be a positive integer")
        self.module_id = module_id
        self.epoch = epoch
        # Retained as a compatibility/audit hint, never as an execution fence.
        self.registry_revision = registry_revision
        self._deadline = deadline_monotonic
        self._clock = clock
        self._current_check = current_check
        self._cleanup_timeout = float(cleanup_timeout)
        self._on_cleanup_timeout = on_cleanup_timeout
        self._cancel_event = asyncio.Event()
        self._pending: dict[asyncio.Task[Any], str] = {}
        self._outcomes: deque[TaskOutcome] = deque(maxlen=history_limit)
        self._cancel_requested = False
        self._closed = False
        self._deadline_exceeded = False
        self._cleanup_timeout_notified = False

    @property
    def deadline_monotonic(self) -> float | None:
        return self._deadline

    @property
    def cancel_event(self) -> asyncio.Event:
        return self._cancel_event

    @property
    def cancelled(self) -> bool:
        return self._cancel_requested

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def deadline_exceeded(self) -> bool:
        return self._deadline_exceeded

    @property
    def cleanup_pending(self) -> bool:
        return bool(self._pending)

    @property
    def tasks(self) -> tuple[asyncio.Task[Any], ...]:
        """Only unfinished work is retained."""
        return tuple(self._pending)

    @property
    def errors(self) -> tuple[str, ...]:
        """Stable exception class names from the bounded diagnostic ring."""
        return tuple(
            outcome.error_code
            for outcome in self._outcomes
            if outcome.error_code is not None
        )

    @property
    def outcomes(self) -> tuple[TaskOutcome, ...]:
        return tuple(self._outcomes)

    def check_before_await(self) -> None:
        """Check admission immediately before an awaited operation."""
        self._check_open()
        if self._current_check is not None:
            self._current_check()

    def check_after_await(self) -> None:
        """Check result publication against the live runtime identity."""
        self._check_open()
        if self._current_check is not None:
            self._current_check()

    ensure_current = check_after_await
    check_commit = check_after_await

    async def await_result(self, work: Awaitable[Any]) -> Any:
        """Compatibility wrapper that uses managed execution."""
        return await self.run(
            work, name="await_result", deadline_monotonic=self._deadline
        )

    async def run(
        self,
        work: Awaitable[Any],
        *,
        name: str,
        deadline_monotonic: float | None,
    ) -> Any:
        """Run one operation with an active deadline and finite cancellation.

        A timeout cancels the underlying task and waits only up to the cleanup
        bound. If it ignores cancellation, the task remains pending and the
        optional lifecycle callback isolates the owning module.
        """
        if not isinstance(name, str) or not name.strip():
            _close_awaitable(work)
            raise ValueError("task name must be non-empty text")
        try:
            effective_deadline = self._effective_deadline(deadline_monotonic)
            self.check_before_await()
        except BaseException:
            _close_awaitable(work)
            raise

        task = self._spawn(work, name)
        try:
            remaining = (
                None
                if effective_deadline is None
                else max(0.0, effective_deadline - self._clock())
            )
            done, _ = await asyncio.wait({task}, timeout=remaining)
            if not done:
                self._deadline_exceeded = True
                task.cancel()
                if not await self._wait_for_cleanup(task):
                    self._notify_cleanup_timeout("deadline_cleanup_timeout")
                    raise ScopeStopTimeout(
                        "task ignored cancellation after its deadline"
                    )
                raise ScopeDeadlineExceeded("task scope deadline has expired")
            return task.result()
        except asyncio.CancelledError:
            task.cancel()
            if not await self._wait_for_cleanup(task):
                self._notify_cleanup_timeout("cancel_cleanup_timeout")
            raise

    def create_task(self, work: Awaitable[object], *, name: str) -> asyncio.Task[Any]:
        """Compatibility scheduler; work also gets active deadline enforcement."""
        if not isinstance(name, str) or not name.strip():
            _close_awaitable(work)
            raise ValueError("task name must be non-empty text")
        try:
            self.check_before_await()
        except BaseException:
            _close_awaitable(work)
            raise

        started = False

        async def supervise() -> object:
            nonlocal started
            started = True
            return await self.run(work, name=name, deadline_monotonic=self._deadline)

        try:
            task = self._spawn(supervise(), f"{name}:scope")
        except BaseException:
            _close_awaitable(work)
            raise
        task.add_done_callback(lambda completed: _close_if_never_started(work, started))
        return task

    def cancel(self) -> None:
        """Request local cancellation without claiming external IO has stopped."""
        if self._closed:
            return
        self._cancel_requested = True
        self._cancel_event.set()
        for task in tuple(self._pending):
            if not task.done():
                task.cancel()

    async def wait(self, timeout: float | None = None) -> tuple[TaskOutcome, ...]:
        """Wait for all tracked work, raising on a finite cleanup timeout."""
        if timeout is not None:
            _finite_timeout(timeout, "timeout")
        pending = {task for task in self._pending if not task.done()}
        if pending:
            _, pending = await asyncio.wait(pending, timeout=timeout)
        if pending:
            raise ScopeStopTimeout(
                f"module task scope did not stop within {timeout!r} seconds"
            )
        self._closed = True
        return self.outcomes

    async def stop(self, timeout: float | None = None) -> tuple[TaskOutcome, ...]:
        """Cancel tracked work and await finite local cleanup."""
        self.cancel()
        try:
            return await self.wait(timeout)
        except ScopeStopTimeout:
            self._notify_cleanup_timeout("scope_stop_timeout")
            raise
        except asyncio.CancelledError:
            drained = await self._wait_for_all_cleanup(timeout)
            if not drained:
                self._notify_cleanup_timeout("scope_stop_cancel_cleanup_timeout")
            else:
                self._closed = True
            raise

    async def close(self, timeout: float | None = None) -> tuple[TaskOutcome, ...]:
        return await self.wait(timeout)

    def _effective_deadline(self, deadline: float | None) -> float | None:
        if deadline is not None:
            _finite_timestamp(deadline, "deadline_monotonic")
        if self._deadline is None:
            return deadline
        if deadline is None:
            return self._deadline
        return min(self._deadline, deadline)

    def _spawn(self, work: Awaitable[Any], name: str) -> asyncio.Task[Any]:
        started = False
        observer = _TASK_OBSERVER.get()
        try:
            if observer is not None:
                observer.check()
        except BaseException:
            _close_awaitable(work)
            raise

        async def runner() -> Any:
            nonlocal started
            # Inherited contexts can outlive the request that created them.
            # Recheck sealing before touching the supplied module awaitable.
            if observer is not None:
                observer.check()
            self.check_before_await()
            started = True
            result = await work
            self.check_after_await()
            return result

        task = asyncio.create_task(runner(), name=name)
        self._pending[task] = name
        task.add_done_callback(lambda completed: _close_if_never_started(work, started))
        task.add_done_callback(self._record_done)
        if observer is not None:
            try:
                observer.register(task)  # Synchronous, before the caller yields.
            except BaseException:
                task.cancel()
                raise
        return task

    async def _wait_for_cleanup(self, task: asyncio.Task[Any]) -> bool:
        deadline = asyncio.get_running_loop().time() + self._cleanup_timeout
        while not task.done():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return False
            try:
                await asyncio.wait({task}, timeout=remaining)
            except asyncio.CancelledError:
                continue
        return True

    async def _wait_for_all_cleanup(self, timeout: float | None) -> bool:
        pending = {task for task in self._pending if not task.done()}
        if not pending:
            return True
        duration = (
            self._cleanup_timeout
            if timeout is None
            else min(self._cleanup_timeout, timeout)
        )
        deadline = asyncio.get_running_loop().time() + duration
        while pending:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return False
            try:
                _, pending = await asyncio.wait(pending, timeout=remaining)
            except asyncio.CancelledError:
                continue
        return True

    def _notify_cleanup_timeout(self, reason: str) -> None:
        if self._cleanup_timeout_notified:
            return
        self._cleanup_timeout_notified = True
        if self._on_cleanup_timeout is not None:
            self._on_cleanup_timeout(reason)

    def _check_open(self) -> None:
        if self._closed:
            raise ScopeClosedError("task scope is closed")
        if self._cancel_requested:
            raise ScopeCancelled("task scope is cancelled")
        if self._deadline is not None and self._clock() >= self._deadline:
            self._deadline_exceeded = True
            self.cancel()
            raise ScopeDeadlineExceeded("task scope deadline has expired")

    def _record_done(self, task: asyncio.Task[Any]) -> None:
        name = self._pending.pop(task, task.get_name())
        if task.cancelled():
            self._outcomes.append(
                TaskOutcome(name=name, status="cancelled", cancelled=True)
            )
            return
        try:
            task.result()
        except BaseException as exc:
            self._outcomes.append(
                TaskOutcome(name=name, status="failed", error_code=type(exc).__name__)
            )
        else:
            self._outcomes.append(TaskOutcome(name=name, status="completed"))


def _finite_timestamp(value: float, field: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{field} must be a finite positive timestamp")


def _finite_timeout(value: float, field: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{field} must be a finite non-negative number")


def _close_awaitable(work: Awaitable[object]) -> None:
    close = getattr(work, "close", None)
    if callable(close):
        close()


def _close_if_never_started(work: Awaitable[object], started: bool) -> None:
    if not started:
        _close_awaitable(work)


TaskScopeCancelled = ScopeCancelled
TaskScopeDeadlineExceeded = ScopeDeadlineExceeded
TaskScopeStopTimeout = ScopeStopTimeout
