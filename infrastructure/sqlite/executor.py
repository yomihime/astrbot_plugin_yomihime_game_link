"""Bounded asynchronous execution for complete SQLite operations.

Each accepted job runs on the database's one dedicated worker at a time. The
connection, unit of work, cursors, commit/rollback, and close all stay inside
that worker. The short idle exit lets independent repository use release its
thread without requiring a hidden process-global executor; a later operation
starts the same executor's worker again.
"""

from __future__ import annotations

import asyncio
import queue
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import fields, is_dataclass
from datetime import date, datetime, timedelta, timezone
from datetime import time as datetime_time
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Any, TypeVar
from uuid import UUID

from .database import SQLiteDatabase, SQLiteUnitOfWork

T = TypeVar("T")
_CAPACITY = 64
_IDLE_EXIT_SECONDS = 0.05


class SQLiteExecutorError(RuntimeError):
    """Base error for the asynchronous SQLite executor."""


class SQLiteExecutorClosedError(SQLiteExecutorError):
    """The executor no longer accepts work."""


class SQLiteExecutorCloseTimeout(SQLiteExecutorError):
    """The executor could not drain and stop before its close deadline."""


class _Job:
    __slots__ = ("kind", "callback", "begin_mode", "loop", "future")

    def __init__(
        self,
        kind: str,
        callback: Callable[[SQLiteUnitOfWork], Any] | None,
        begin_mode: str,
        loop: asyncio.AbstractEventLoop,
        future: asyncio.Future[Any],
    ) -> None:
        self.kind = kind
        self.callback = callback
        self.begin_mode = begin_mode
        self.loop = loop
        self.future = future


class SQLiteExecutor:
    """Run complete SQLite callbacks on one bounded, single-thread worker."""

    CAPACITY = _CAPACITY

    def __init__(self, database: SQLiteDatabase) -> None:
        if not isinstance(database, SQLiteDatabase):
            raise TypeError("database must be SQLiteDatabase")
        self.database = database
        self._queue: queue.Queue[_Job] = queue.Queue(maxsize=_CAPACITY)
        self._slots = asyncio.Semaphore(_CAPACITY)
        self._state_lock = threading.Lock()
        self._state = "OPEN"
        self._worker: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._outstanding = 0
        self._idle: asyncio.Event | None = None
        self._initialize_lock = asyncio.Lock()
        self._initialized = False
        self._initialization_version = 0

    @property
    def state(self) -> str:
        with self._state_lock:
            return self._state

    def _bind_loop(self) -> tuple[asyncio.AbstractEventLoop, asyncio.Event]:
        loop = asyncio.get_running_loop()
        with self._state_lock:
            if self._loop is not None and self._loop is not loop:
                raise SQLiteExecutorError(
                    "SQLiteExecutor belongs to a different event loop"
                )
            self._loop = loop
            if self._idle is None:
                self._idle = asyncio.Event()
                self._idle.set()
            return loop, self._idle

    async def _ensure_initialized(self) -> int:
        if self._initialized:
            return self._initialization_version
        async with self._initialize_lock:
            if self._initialized:
                return self._initialization_version
            version = await self._submit("initialize", None, "DEFERRED")
            if type(version) is not int:
                raise SQLiteExecutorError(
                    "database initialization returned invalid version"
                )
            self._initialization_version = version
            self._initialized = True
            return version

    async def initialize(self) -> int:
        """Validate integrity and apply migrations once on the worker."""

        return await self._ensure_initialized()

    async def verify_integrity(self) -> None:
        """Run an explicit maintenance integrity check on the worker."""

        await self._ensure_initialized()
        await self._submit("verify", None, "DEFERRED")

    async def run_transaction(
        self,
        callback: Callable[[SQLiteUnitOfWork], T],
        *,
        begin_mode: str = "DEFERRED",
    ) -> T:
        """Execute one synchronous callback as one complete SQLite transaction."""

        if not callable(callback):
            raise TypeError("transaction callback must be callable")
        begin_mode = begin_mode.upper()
        if begin_mode not in {"DEFERRED", "IMMEDIATE", "EXCLUSIVE"}:
            raise ValueError("begin_mode must be DEFERRED, IMMEDIATE, or EXCLUSIVE")
        await self._ensure_initialized()
        return await self._submit("transaction", callback, begin_mode)

    async def run_read(self, callback: Callable[[SQLiteUnitOfWork], T]) -> T:
        """Execute one synchronous read callback and return only detached data."""

        if not callable(callback):
            raise TypeError("read callback must be callable")
        await self._ensure_initialized()
        return await self._submit("read", callback, "DEFERRED")

    async def _submit(
        self,
        kind: str,
        callback: Callable[[SQLiteUnitOfWork], Any] | None,
        begin_mode: str,
    ) -> Any:
        loop, idle = self._bind_loop()
        await self._slots.acquire()
        future: asyncio.Future[Any] = loop.create_future()
        job = _Job(kind, callback, begin_mode, loop, future)
        try:
            with self._state_lock:
                if self._state != "OPEN":
                    raise SQLiteExecutorClosedError("SQLiteExecutor is closing")
                self._outstanding += 1
                idle.clear()
                worker = self._worker
                if worker is None:
                    worker = threading.Thread(
                        target=self._work,
                        name="yomihime-sqlite-worker",
                        daemon=False,
                    )
                    self._worker = worker
                    worker.start()
                try:
                    self._queue.put_nowait(job)
                except Exception:
                    self._outstanding -= 1
                    if self._outstanding == 0:
                        idle.set()
                    raise
        except BaseException:
            self._slots.release()
            raise

        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError as cancelled:
            # A queued job owns its capacity until the SQLite connection has
            # committed/rolled back and closed. Drain it before propagating the
            # caller's cancellation, even if the worker itself raised.
            while not future.done():
                try:
                    await asyncio.shield(future)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    break
            if future.done() and not future.cancelled():
                try:
                    future.exception()
                except BaseException:
                    pass
            raise cancelled

    def _work(self) -> None:
        current = threading.current_thread()
        while True:
            try:
                job = self._queue.get(timeout=_IDLE_EXIT_SECONDS)
            except queue.Empty:
                with self._state_lock:
                    # Submissions enqueue under the same lock. Once this
                    # worker relinquishes the slot, a new job may start one
                    # successor, but two workers never run SQLite callbacks.
                    if self._queue.empty() and self._worker is current:
                        self._worker = None
                        return
                continue
            try:
                result = self._run_job(job)
            except BaseException as exc:
                self._complete(job, error=exc)
            else:
                self._complete(job, result=result)
            finally:
                self._queue.task_done()

    def _run_job(self, job: _Job) -> Any:
        if job.kind == "initialize":
            return self.database._initialize_sync()
        if job.kind == "verify":
            connection = self.database.connect()
            try:
                self.database._check_integrity(connection)
            finally:
                connection.close()
            return None
        if job.kind not in {"read", "transaction"} or job.callback is None:
            raise SQLiteExecutorError("invalid worker job")

        connection = self.database.connect()
        unit = SQLiteUnitOfWork(connection, begin_mode=job.begin_mode)
        try:
            unit._begin_sync()
            try:
                result = job.callback(unit)
                if hasattr(result, "__await__"):
                    raise TypeError("SQLite worker callbacks must be synchronous")
                _reject_sqlite_handles(result)
            except BaseException:
                if unit.in_transaction:
                    unit.rollback_sync()
                raise
            else:
                if unit.in_transaction:
                    unit.commit_sync()
                return result
        finally:
            unit.close_sync()

    def _complete(
        self, job: _Job, *, result: Any = None, error: BaseException | None = None
    ) -> None:
        def finish() -> None:
            with self._state_lock:
                self._outstanding -= 1
                if self._outstanding == 0 and self._idle is not None:
                    self._idle.set()
            if not job.future.done():
                if error is None:
                    job.future.set_result(result)
                else:
                    job.future.set_exception(error)
            self._slots.release()

        try:
            job.loop.call_soon_threadsafe(finish)
        except RuntimeError:
            # A caller cannot normally close its loop before the shielded job
            # drains. Keep this fail-safe so a torn-down loop cannot pin the
            # worker forever; the executor is unusable on that loop afterward.
            with self._state_lock:
                self._outstanding -= 1
                if self._outstanding == 0:
                    self._state = "CLOSED"

    async def close(self, *, timeout: float = 5.0) -> None:
        """Reject new jobs, drain accepted work, and wait for the worker exit."""

        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or timeout < 0
        ):
            raise ValueError("timeout must be non-negative")
        loop, idle = self._bind_loop()
        with self._state_lock:
            if self._state == "CLOSED":
                return
            self._state = "CLOSING"
        deadline = loop.time() + float(timeout)
        while True:
            with self._state_lock:
                worker = self._worker
                outstanding = self._outstanding
                if outstanding == 0 and (worker is None or not worker.is_alive()):
                    self._state = "CLOSED"
                    return
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise SQLiteExecutorCloseTimeout(
                    "SQLiteExecutor did not drain before close timeout"
                )
            # Waiting on idle wakes as soon as accepted callbacks finish; the
            # short poll also observes the worker's bounded idle exit.
            try:
                await asyncio.wait_for(idle.wait(), min(remaining, 0.01))
            except TimeoutError:
                pass


def _current_version(unit: SQLiteUnitOfWork) -> int:
    row = unit.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
    ).fetchone()
    return int(row[0])


def _reject_sqlite_handles(
    value: Any,
    active: set[int] | None = None,
    checked: set[int] | None = None,
) -> None:
    """Validate detached worker results against an explicit value whitelist."""

    if isinstance(value, (sqlite3.Connection, sqlite3.Cursor, SQLiteUnitOfWork)):
        raise TypeError(
            "SQLite connection, cursor, or unit of work cannot escape worker"
        )

    value_type = type(value)
    if value is None or value_type in (
        str,
        bytes,
        bytearray,
        int,
        float,
        bool,
        Decimal,
        UUID,
        timedelta,
    ):
        return
    if value_type in (date, datetime, datetime_time):
        if value_type in (datetime, datetime_time):
            tzinfo = value.tzinfo
            if tzinfo is not None and type(tzinfo) is not timezone:
                raise TypeError("unsupported timezone in SQLite worker result")
        return

    active = set() if active is None else active
    checked = set() if checked is None else checked
    identity = id(value)
    if identity in active:
        raise TypeError("cyclic SQLite worker result cannot escape worker")
    if identity in checked:
        return

    active.add(identity)
    try:
        if isinstance(value, Enum):
            attributes = vars(value)
            if set(attributes) - {
                "_value_",
                "_name_",
                "__objclass__",
                "_sort_order_",
            }:
                raise TypeError("unsupported attributes in SQLite enum result")
            if _declared_slots(value_type):
                raise TypeError("unsupported slots in SQLite enum result")
            _reject_sqlite_handles(value.value, active, checked)
        elif value_type is dict or value_type is type(MappingProxyType({})):
            for key, item in value.items():
                _reject_sqlite_handles(key, active, checked)
                _reject_sqlite_handles(item, active, checked)
        elif value_type in (tuple, list, set, frozenset):
            for item in value:
                _reject_sqlite_handles(item, active, checked)
        elif is_dataclass(value) and not isinstance(value, type):
            dataclass_fields = fields(value)
            declared = {field.name for field in dataclass_fields}
            extra = set(vars(value)) - declared if hasattr(value, "__dict__") else set()
            if extra:
                raise TypeError("unsupported attributes in SQLite worker DTO")
            if _declared_slots(type(value)) - declared:
                raise TypeError("unsupported slots in SQLite worker DTO")
            for field in dataclass_fields:
                _reject_sqlite_handles(getattr(value, field.name), active, checked)
        else:
            raise TypeError("unsupported SQLite worker result type")
    finally:
        active.remove(identity)
    checked.add(identity)


def _declared_slots(value_type: type[Any]) -> set[str]:
    """Return instance slots declared anywhere in the concrete type's MRO."""

    slots: set[str] = set()
    for base in value_type.__mro__:
        declared = base.__dict__.get("__slots__", ())
        if isinstance(declared, str):
            slots.add(declared)
        else:
            slots.update(declared)
    return slots - {"__dict__", "__weakref__"}


__all__ = [
    "SQLiteExecutor",
    "SQLiteExecutorClosedError",
    "SQLiteExecutorCloseTimeout",
    "SQLiteExecutorError",
]
