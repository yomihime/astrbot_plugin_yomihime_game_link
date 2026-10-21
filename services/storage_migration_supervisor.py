"""Exact process-backed offline authority for controlled synthetic sources.

There is deliberately no adapter for an arbitrary existing Host deployment.
PID lists, receipts and a caller's 'stopped' boolean cannot create this lease.
The synthetic adapter creates a source from birth and has a closed launcher:
its only worker is the fixed non-spawning encrypt/wait program below. This is
testable lineage, not a claim to cover unrelated legacy installations.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from threading import RLock

from ..infrastructure.private_storage import PrivateStorageError

_SEAL = object()
_WORKER = r"""
import pathlib,sys
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
root=pathlib.Path(sys.argv[1])
# Deterministic synthetic material only, never read environment or user keys.
encrypted=AESGCM(bytes(range(32))).encrypt(bytes(range(12)),b'SYNTHETIC DELAYED WRITER',b'fixture')
sys.stdout.write('encrypted-ready\n');sys.stdout.flush()
command=sys.stdin.buffer.read(1)
if command==b'p':
 (root/'secrets').mkdir(exist_ok=True)
 (root/'secrets'/'synthetic_delayed.blob').write_bytes(encrypted)
 sys.stdout.write('published\n');sys.stdout.flush()
elif command==b'q':
 sys.stdout.write('stopped-clean\n');sys.stdout.flush()
else:
 raise SystemExit(2)
"""


def _plan_binding(plan):
    return (
        plan.source_identity,
        plan.operation_id,
        plan.inventory_digest,
        plan.snapshot_digest,
        plan.files_digest,
        plan.generation,
        plan.revision,
        str(plan.layout.storage_root.absolute()),
    )


class OfflineSourceLease:
    """Nonserializable authority owned by one live supervisor maintenance scope."""

    __slots__ = ("_supervisor", "_binding", "_generation", "_active")

    def __init__(self, supervisor, plan, *, _seal=None):
        if _seal is not _SEAL or type(supervisor) is not SyntheticSourceSupervisor:
            raise PrivateStorageError("source_exclusivity_unproven")
        self._supervisor = supervisor
        self._binding = _plan_binding(plan)
        self._generation = supervisor._generation
        self._active = True

    def assert_current(self, plan):
        supervisor = self._supervisor
        with supervisor._lock:
            if (
                not self._active
                or supervisor._lease is not self
                or self._generation != supervisor._generation
                or self._binding != _plan_binding(plan)
            ):
                raise PrivateStorageError("source_exclusivity_unproven")
            supervisor._assert_exclusive()

    def __reduce__(self):
        raise TypeError("source authority cannot be serialized")


class _Writer:
    """Retain the exact Popen OS process handle through normal exit."""

    def __init__(self, process, supervisor):
        self.process = process
        self._supervisor = supervisor
        self._launch_generation = supervisor._generation
        self._stopping = False
        # Windows Popen.poll/wait use this fresh CreateProcess HANDLE, not a
        # reopened PID. Linux retains its own unreaped child plus pidfd.
        self.handle = getattr(process, "_handle", None)
        self.pidfd = None
        self.pid = process.pid
        self.creation_time = None
        self._identity_valid = False
        self._handles_closed = False

    def capture_identity(self):
        """Observe creation identity through the handle created by this launcher."""
        if os.name != "nt":
            if not hasattr(os, "pidfd_open"):
                raise PrivateStorageError("offline_supervisor_unavailable")
            self.pidfd = os.pidfd_open(self.pid)
            self.creation_time = os.fstat(self.pidfd).st_ino
        elif self.handle is None:
            raise PrivateStorageError("offline_supervisor_unavailable")
        else:
            self.creation_time = self._windows_identity()
        self._identity_valid = True

    def _windows_identity(self):
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetProcessId.argtypes = [wintypes.HANDLE]
        kernel.GetProcessId.restype = wintypes.DWORD
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [
            ctypes.POINTER(wintypes.FILETIME)
        ] * 4
        kernel.GetProcessTimes.restype = wintypes.BOOL
        created, exited, system, user = (wintypes.FILETIME() for _ in range(4))
        if kernel.GetProcessId(
            int(self.handle)
        ) != self.pid or not kernel.GetProcessTimes(
            int(self.handle),
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(system),
            ctypes.byref(user),
        ):
            raise PrivateStorageError("source_consumer_identity_unknown")
        return created.dwLowDateTime | (created.dwHighDateTime << 32)

    def assert_identity(self):
        if not self._identity_valid or self._handles_closed:
            raise PrivateStorageError("source_consumer_identity_unknown")
        current = (
            self._windows_identity() if os.name == "nt" else os.fstat(self.pidfd).st_ino
        )
        if current != self.creation_time:
            raise PrivateStorageError("source_consumer_identity_unknown")

    def exited(self):
        self.assert_identity()
        return self.process.poll() is not None

    def _stop_binding(self):
        self.assert_identity()
        return self.pid, self.creation_time, self._launch_generation

    async def stop_normally(self, *, publish=False):
        supervisor = self._supervisor
        try:
            with supervisor._lock:
                self.assert_identity()
                if self._stopping:
                    raise PrivateStorageError("source_consumer_stop_unknown")
                if self.process.poll() is not None:
                    if self.process.returncode != 0:
                        raise PrivateStorageError("source_consumer_failed")
                    supervisor._assert_normal_stop(self)
                    return
                self._stopping = True
                if publish:
                    # This deliberately exercises a delayed publication. It is
                    # not the controlled q-stop protocol and never mints proof.
                    supervisor.mark_coverage_unknown()
            self.process.stdin.write(b"p" if publish else b"q")
            self.process.stdin.flush()
            # Cancellation after sending q is still an unconfirmed stop until
            # this task has observed both the protocol receipt and exact exit.
            await asyncio.sleep(0)
            deadline = asyncio.get_running_loop().time() + 5
            while self.process.poll() is None:
                if asyncio.get_running_loop().time() >= deadline:
                    raise PrivateStorageError("source_consumer_exit_unknown")
                await asyncio.sleep(0.01)
            result = self.process.wait(timeout=0)
            self.assert_identity()
            if result != 0:
                raise PrivateStorageError("source_consumer_failed")
            acknowledgment = self.process.stdout.read(64).rstrip(b"\r\n")
            expected = b"published" if publish else b"stopped-clean"
            if acknowledgment != expected:
                raise PrivateStorageError("source_consumer_stop_unknown")
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError
            with supervisor._lock:
                if (
                    supervisor._coverage_known
                    and not supervisor._closed
                    and not publish
                ):
                    supervisor._normal_stops[self] = self._stop_binding()
        except BaseException:
            # A caught error/cancellation can never be converted into a later
            # poll-only proof. All receipts and any current lease lose lineage.
            supervisor.mark_coverage_unknown()
            raise
        finally:
            self._stopping = False

    def close_handles(self):
        if self._handles_closed:
            return
        # Cleanup also covers failure to capture identity immediately after our
        # own CreateProcess; it never turns that failure into migration proof.
        if self.process.poll() is None:
            raise PrivateStorageError("source_consumer_still_running")
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()
        if self.pidfd is not None:
            os.close(self.pidfd)
            self.pidfd = None
        if self.handle is not None:
            self.handle.Close()
        self._handles_closed = True


class SyntheticSourceSupervisor:
    """Closed built-in deployment adapter for a newly-created synthetic source.

    No attach-existing, arbitrary executable, provider import, manual PID or
    'coverage=true' API exists. The launcher code never creates descendants.
    A reported coverage gap poisons this session permanently.
    """

    def __init__(self, *, _seal=None, parent=None):
        if _seal is not _SEAL:
            raise PrivateStorageError("offline_supervisor_unavailable")
        self.root = Path(tempfile.mkdtemp(prefix="supervised-synthetic-", dir=parent))
        self.source_root = self.root / "source"
        self.source_root.mkdir()
        self._lock = RLock()
        self._generation = 1
        self._maintenance = False
        self._closed = False
        self._coverage_known = True
        self._writers = []
        self._normal_stops = {}
        self._lease = None
        self._lock_file = None

    @classmethod
    def create_synthetic(cls, parent: Path):
        return cls(_seal=_SEAL, parent=parent)

    async def start_delayed_writer(self):
        with self._lock:
            if self._maintenance or self._closed or not self._coverage_known:
                raise PrivateStorageError("source_launch_fenced")
            process = subprocess.Popen(
                [sys.executable, "-I", "-c", _WORKER, str(self.source_root)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            writer = _Writer(process, self)
            self._writers.append(writer)
            try:
                writer.capture_identity()
            except BaseException:
                self._coverage_known = False
                raise
        try:
            ready = await asyncio.wait_for(
                asyncio.to_thread(process.stdout.readline), 5
            )
            if ready.rstrip(b"\r\n") != b"encrypted-ready":
                raise PrivateStorageError("source_consumer_start_unknown")
            writer.assert_identity()
        except BaseException:
            self.mark_coverage_unknown()
            raise
        return writer

    def mark_coverage_unknown(self):
        """A missing consumer/launcher report can deny, never create authority."""
        with self._lock:
            self._coverage_known = False
            self._normal_stops.clear()
            self._generation += 1

    def _assert_normal_stop(self, writer):
        if (
            writer._supervisor is not self
            or self._normal_stops.get(writer) != writer._stop_binding()
            or writer.process.poll() != 0
        ):
            raise PrivateStorageError("source_consumer_stop_unproven")

    def _consumers_stopped(self):
        for writer in self._writers:
            try:
                writer.assert_identity()
                running = writer.process.poll() is None
            except BaseException:
                self.mark_coverage_unknown()
                raise
            if running:
                raise PrivateStorageError("source_consumer_still_running")
            try:
                self._assert_normal_stop(writer)
            except BaseException:
                self.mark_coverage_unknown()
                raise

    def _take_maintenance_lock(self):
        stream = (self.root / "maintenance.lock").open("a+b")
        stream.seek(0)
        if stream.read(1) == b"":
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            raise PrivateStorageError("source_exclusivity_unproven") from None
        self._lock_file = stream

    def _assert_exclusive(self):
        if (
            self._closed
            or not self._coverage_known
            or not self._maintenance
            or self._lock_file is None
            or self._lock_file.closed
        ):
            raise PrivateStorageError("source_exclusivity_unproven")
        self._consumers_stopped()

    @asynccontextmanager
    async def acquire_offline_source(self, plan):
        with self._lock:
            if (
                self._closed
                or self._maintenance
                or not self._coverage_known
                or plan.layout.storage_root.absolute() != self.source_root.absolute()
            ):
                raise PrivateStorageError("source_exclusivity_unproven")
            self._consumers_stopped()
            self._maintenance = True
            self._generation += 1
            try:
                self._take_maintenance_lock()
                self._lease = OfflineSourceLease(self, plan, _seal=_SEAL)
                self._lease.assert_current(plan)
            except BaseException:
                self._maintenance = False
                if self._lock_file is not None:
                    self._lock_file.close()
                    self._lock_file = None
                self._lease = None
                raise
            lease = self._lease
        try:
            yield lease
        finally:
            with self._lock:
                lease._active = False
                self._lease = None
                # Once an offline migration window starts, this source is
                # retired. Never restart its old launcher after scope exit.
                self._closed = True
                self._generation += 1
                if self._lock_file is not None:
                    self._lock_file.close()
                    self._lock_file = None

    async def close(self):
        with self._lock:
            self._closed = True
            self.mark_coverage_unknown()
            if self._lease is not None:
                self._lease._active = False
        failure = None
        for writer in self._writers:
            try:
                if writer.process.poll() is None:
                    await writer.stop_normally()
            except BaseException as error:
                if failure is None:
                    failure = error
            finally:
                if writer.process.poll() is not None:
                    writer.close_handles()
        if failure is not None:
            # A live/unknown owned worker keeps its handles; never kill it or
            # claim that a failed drain completed merely because close ran.
            raise failure


def deployment_supervisor_for_existing_source(_layout):
    """No arbitrary legacy deployment has a proven complete launcher adapter."""
    raise PrivateStorageError("offline_supervisor_unavailable")
