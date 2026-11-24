"""Bound admission to top-level module work on this Core runtime's event loop."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from typing import TypeVar

from .admission import AdmissionError
from .task_scope import ScopeDeadlineExceeded

T = TypeVar("T")


class HandlerDispatch:
    """Lifecycle scopes retain task ownership, deadlines and cancellation."""

    def __init__(self, capacity=8, reserved=2, queue_limit=64):
        if not (0 < reserved < capacity and queue_limit >= 0):
            raise ValueError("invalid handler dispatch limits")
        self._all = asyncio.Semaphore(capacity)
        self._background = asyncio.Semaphore(capacity - reserved)
        self._queue_limit = queue_limit
        self._waiting = {True: 0, False: 0}

    async def run(
        self,
        start: Callable[[], Awaitable[T]],
        *,
        background: bool,
        deadline,
        clock,
        after_wait: Callable[[], Awaitable[None]] | None = None,
    ) -> T:
        # asyncio semaphores retain FIFO order among their waiters. Background
        # work waits for its own quota first, leaving capacity for live callers.
        must_wait = self._all.locked() or (background and self._background.locked())
        if must_wait and self._waiting[background] >= self._queue_limit:
            raise AdmissionError("module handler queue is full")
        self._waiting[background] += 1
        async with AsyncExitStack() as slots:
            try:
                remaining = None if deadline is None else deadline - clock()
                if remaining is not None and remaining <= 0:
                    raise ScopeDeadlineExceeded("module dispatch deadline expired")
                try:
                    async with asyncio.timeout(remaining):
                        if background:
                            await slots.enter_async_context(self._background)
                        await slots.enter_async_context(self._all)
                except TimeoutError:
                    raise ScopeDeadlineExceeded(
                        "module dispatch deadline expired"
                    ) from None
            finally:
                self._waiting[background] -= 1
            if deadline is not None and clock() >= deadline:
                raise ScopeDeadlineExceeded("module dispatch deadline expired")
            if must_wait and after_wait is not None:
                await after_wait()
            return await start()
