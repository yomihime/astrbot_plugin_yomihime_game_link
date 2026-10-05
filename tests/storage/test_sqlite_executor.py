from __future__ import annotations

import asyncio
import sqlite3
import threading
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.infrastructure.sqlite.database import (
    SQLiteBusyError,
    SQLiteDatabase,
    SQLiteUnitOfWork,
)
from ygl_test_subject.infrastructure.sqlite.executor import (
    SQLiteExecutorCloseTimeout,
)


class SQLiteExecutorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "runtime.sqlite3"
        self.database = SQLiteDatabase(self.path, timeout=0.08)
        self.executor = self.database.executor

    async def asyncTearDown(self) -> None:
        if self.executor.state != "CLOSED":
            await self.executor.close()

    async def test_executor_initializes_once_and_keeps_connections_on_worker(
        self,
    ) -> None:
        integrity_calls = 0
        connect_threads: list[int] = []
        close_threads: list[int] = []
        original_check = self.database._check_integrity
        original_connect = self.database._connect
        original_close = SQLiteUnitOfWork.close_sync

        def checked(connection: sqlite3.Connection) -> None:
            nonlocal integrity_calls
            integrity_calls += 1
            original_check(connection)

        def connected() -> sqlite3.Connection:
            connect_threads.append(threading.get_ident())
            return original_connect()

        def closed(unit) -> None:
            close_threads.append(threading.get_ident())
            original_close(unit)

        with (
            patch.object(self.database, "_check_integrity", side_effect=checked),
            patch.object(self.database, "_connect", side_effect=connected),
            patch(
                "ygl_test_subject.infrastructure.sqlite.database.SQLiteUnitOfWork.close_sync",
                new=closed,
            ),
        ):
            version = await self.executor.initialize()
            self.assertEqual(version, 100)
            await self.executor.run_transaction(
                lambda unit: _execute_statement(
                    unit, "CREATE TABLE worker_probe(value TEXT)"
                )
            )
            await self.executor.run_transaction(
                lambda unit: _execute_statement(
                    unit, "INSERT INTO worker_probe VALUES ('saved')"
                ),
                begin_mode="IMMEDIATE",
            )
            values = await self.executor.run_read(
                lambda unit: tuple(
                    row[0] for row in unit.execute("SELECT value FROM worker_probe")
                )
            )

        self.assertEqual(values, ("saved",))
        self.assertEqual(integrity_calls, 1)
        self.assertTrue(connect_threads)
        self.assertEqual(set(connect_threads), set(close_threads))
        self.assertEqual(len(set(connect_threads)), 1)

    async def test_sync_connection_and_integrity_paths_do_not_read_entire_file(
        self,
    ) -> None:
        await self.executor.initialize()
        with patch.object(
            Path, "read_bytes", side_effect=AssertionError("unbounded read")
        ):
            connection = self.database.connect()
            try:
                self.assertGreater(
                    connection.execute("PRAGMA schema_version").fetchone()[0], 0
                )
            finally:
                connection.close()

    async def test_callback_failure_rolls_back_before_returning(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "callback failed"):
            await self.executor.run_transaction(
                lambda unit: _insert_then_raise(unit), begin_mode="IMMEDIATE"
            )
        count = await self.executor.run_read(
            lambda unit: int(
                unit.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE name='rolled_back'"
                ).fetchone()[0]
            )
        )
        self.assertEqual(count, 0)

    async def test_result_whitelist_accepts_detached_dtos_and_json_containers(self):
        value = await self.executor.run_read(
            lambda _unit: _DetachedResult(
                datetime(2026, 9, 27, tzinfo=UTC),
                _ProbeStatus.READY,
                MappingProxyType({"values": ["safe", 7]}),
            )
        )
        self.assertEqual(value.status, _ProbeStatus.READY)
        self.assertEqual(value.payload["values"], ["safe", 7])

    async def test_result_whitelist_rejects_opaque_wrappers_and_cycles(self):
        with self.assertRaisesRegex(TypeError, "unsupported SQLite worker result"):
            await self.executor.run_transaction(
                lambda unit: SimpleNamespace(payload={"nested": [unit.connection]})
            )
        with self.assertRaisesRegex(TypeError, "cannot escape worker"):
            await self.executor.run_read(lambda unit: {"nested": [unit.connection]})

        cyclic: list[object] = []
        cyclic.append(cyclic)
        with self.assertRaisesRegex(TypeError, "cyclic SQLite worker result"):
            await self.executor.run_read(lambda _unit: cyclic)

    async def test_result_whitelist_rejects_handles_in_supported_type_extra_state(self):
        try:
            with self.assertRaisesRegex(TypeError, "unsupported attributes"):
                await self.executor.run_read(_enum_with_worker_connection)
        finally:
            if hasattr(_ProbeStatus.READY, "worker_connection"):
                delattr(_ProbeStatus.READY, "worker_connection")

        with self.assertRaisesRegex(TypeError, "unsupported SQLite worker result"):
            await self.executor.run_read(_datetime_subclass_with_worker_connection)

        with self.assertRaisesRegex(TypeError, "unsupported slots"):
            await self.executor.run_read(_dataclass_subclass_with_worker_connection)

    async def test_worker_connection_hook_stays_on_worker_and_is_used(self):
        await self.executor.initialize()
        loop_thread_id = threading.get_ident()
        connect_threads: list[tuple[int, str]] = []
        original_connect = self.database.connect

        def observed_connect() -> sqlite3.Connection:
            thread = threading.current_thread()
            connect_threads.append((threading.get_ident(), thread.name))
            return original_connect()

        with patch.object(self.database, "connect", side_effect=observed_connect):
            self.assertEqual(await self.executor.run_read(lambda _unit: 4), 4)

        self.assertEqual(len(connect_threads), 1)
        self.assertNotEqual(connect_threads[0][0], loop_thread_id)
        self.assertEqual(connect_threads[0][1], "yomihime-sqlite-worker")

    async def test_worker_connect_override_failure_is_observed(self):
        class FailingDatabase(SQLiteDatabase):
            def connect(self):
                raise sqlite3.OperationalError("injected connection failure")

        database = FailingDatabase(Path(self.temp.name) / "connect-failure.sqlite3")
        executor = database.executor
        try:
            await executor.initialize()
            with self.assertRaisesRegex(
                sqlite3.OperationalError, "injected connection failure"
            ):
                await executor.run_read(lambda _unit: None)
        finally:
            await executor.close(timeout=2)

    async def test_accepted_cancellation_waits_for_commit_then_propagates(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def callback(unit) -> int:
            unit.execute("CREATE TABLE cancellation_probe(value TEXT)")
            unit.execute("INSERT INTO cancellation_probe VALUES ('committed')")
            started.set()
            if not release.wait(2):
                raise TimeoutError("test callback was not released")
            return 7

        task = asyncio.create_task(self.executor.run_transaction(callback))
        deadline = asyncio.get_running_loop().time() + 1
        while not started.is_set() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.005)
        self.assertTrue(started.is_set())
        task.cancel()
        await asyncio.sleep(0.02)
        self.assertFalse(task.done(), "accepted work must drain before cancellation")
        task.cancel()
        await asyncio.sleep(0.02)
        self.assertFalse(task.done(), "repeated cancellation must keep draining")
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        values = await self.executor.run_read(
            lambda unit: tuple(
                row[0] for row in unit.execute("SELECT value FROM cancellation_probe")
            )
        )
        self.assertEqual(values, ("committed",))

    async def test_worker_busy_wait_does_not_block_event_loop_and_close_is_final(
        self,
    ) -> None:
        await self.executor.initialize()
        lock = sqlite3.connect(self.path, timeout=0.08, isolation_level=None)
        lock.execute("BEGIN IMMEDIATE")
        lock.execute("CREATE TABLE IF NOT EXISTS lock_probe(value TEXT)")
        ticks = 0

        async def heartbeat() -> None:
            nonlocal ticks
            for _ in range(8):
                await asyncio.sleep(0.01)
                ticks += 1

        beat = asyncio.create_task(heartbeat())
        try:
            with self.assertRaises(SQLiteBusyError):
                await self.executor.run_transaction(
                    lambda unit: unit.execute(
                        "INSERT INTO lock_probe VALUES ('blocked')"
                    ),
                    begin_mode="IMMEDIATE",
                )
            await beat
            self.assertEqual(ticks, 8)
        finally:
            lock.rollback()
            lock.close()
        await self.executor.close(timeout=1)
        self.assertEqual(self.executor.state, "CLOSED")
        await self.executor.close(timeout=1)
        self.assertFalse(
            any(
                thread.name == "yomihime-sqlite-worker" and thread.is_alive()
                for thread in threading.enumerate()
            )
        )

    async def test_close_timeout_keeps_executor_closing_until_drain(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def callback(unit) -> int:
            started.set()
            if not release.wait(2):
                raise TimeoutError("test callback was not released")
            return 1

        task = asyncio.create_task(self.executor.run_transaction(callback))
        deadline = asyncio.get_running_loop().time() + 1
        while not started.is_set() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.005)
        self.assertTrue(started.is_set())
        with self.assertRaises(SQLiteExecutorCloseTimeout):
            await self.executor.close(timeout=0.01)
        self.assertEqual(self.executor.state, "CLOSING")
        release.set()
        self.assertEqual(await task, 1)
        await self.executor.close(timeout=1)
        self.assertEqual(self.executor.state, "CLOSED")


def _insert_then_raise(unit) -> None:
    unit.execute("CREATE TABLE rolled_back(value TEXT)")
    unit.execute("INSERT INTO rolled_back VALUES ('no')")
    raise RuntimeError("callback failed")


def _execute_statement(unit, sql: str) -> None:
    unit.execute(sql)


class _ProbeStatus(Enum):
    READY = "ready"


@dataclass(frozen=True, slots=True)
class _DetachedResult:
    created_at: datetime
    status: _ProbeStatus
    payload: object


class _TaggedDateTime(datetime):
    __slots__ = ("worker_connection",)


@dataclass(slots=True)
class _DataclassBase:
    label: str


class _DataclassConnectionWrapper(_DataclassBase):
    __slots__ = ("worker_connection",)


def _enum_with_worker_connection(unit):
    _ProbeStatus.READY.worker_connection = unit.connection
    return _ProbeStatus.READY


def _datetime_subclass_with_worker_connection(unit):
    value = _TaggedDateTime(2026, 9, 27, tzinfo=UTC)
    value.worker_connection = unit.connection
    return value


def _dataclass_subclass_with_worker_connection(unit):
    value = _DataclassConnectionWrapper("wrapper")
    value.worker_connection = unit.connection
    return value


if __name__ == "__main__":
    unittest.main()
