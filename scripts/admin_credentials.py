"""Local hidden-input maintenance CLI for Core admin credentials."""

# Imports below the bootstrap deliberately follow the plugin-local SDK check.
# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import getpass
import importlib
import os
import stat
import sys
import warnings
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from pathlib import Path

# Package execution starts at the install parent's path, not the plugin root.
# Pin the SDK import to this installed package before importing Core-facing APIs,
# while keeping this small maintenance CLI independent of AstrBot main.py.
_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_SDK_VERSION = "1.6.0"
_SDK_CONTRACT_REVISION = "R5-LLM-TOOLS"
_SDK_COMPATIBLE_CONTRACT_VERSIONS = (
    "1.0.0",
    "1.1.0",
    "1.2.0",
    "1.3.0",
    "1.4.0",
    "1.5.0",
    "1.6.0",
)
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))
try:
    _sdk = importlib.import_module("yomihime_sdk")
    _sdk_version = importlib.import_module("yomihime_sdk.api.version")
    _sdk_root = Path(_sdk.__file__).resolve().parent
    if (
        _sdk_root != (_PLUGIN_ROOT / "yomihime_sdk").resolve()
        or _sdk.__version__ != _SDK_VERSION
        or _sdk_version.CONTRACT_VERSION != _SDK_VERSION
        or _sdk_version.CONTRACT_REVISION != _SDK_CONTRACT_REVISION
        or tuple(_sdk_version.COMPATIBLE_CONTRACT_VERSIONS)
        != _SDK_COMPATIBLE_CONTRACT_VERSIONS
    ):
        raise RuntimeError("unsupported plugin-local SDK")
except Exception:
    raise RuntimeError("the pinned plugin-local SDK is unavailable") from None

from ..extensions.windows_maintenance import WindowsMaintenanceGuard
from ..infrastructure.sqlite.executor import SQLiteExecutorCloseTimeout
from ..infrastructure.sqlite.repositories_admin_credentials import (
    AdminCredentialStateError,
    AdminCredentialStatus,
    SQLiteAdminCredentialRepository,
)
from ..services.admin_authorization import _digest, _matches

_POSIX = os.name == "posix"


class LocalMaintenanceAuthorizationError(RuntimeError):
    """The current process cannot prove local maintenance authority."""


def _directory_is_restricted(directory: Path, user_id: int) -> bool:
    try:
        metadata = directory.stat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(metadata.st_mode)
        and metadata.st_uid in (0, user_id)
        and not metadata.st_mode & 0o022
    )


def authorize_local_maintenance(database: Path) -> None:
    """Retain the existing POSIX proof; Windows uses the held guard below."""
    if not _POSIX or not hasattr(os, "geteuid"):
        raise LocalMaintenanceAuthorizationError(
            "local maintenance authority cannot be verified on this platform"
        )
    if not sys.stdin.isatty() or os.getuid() != os.geteuid():
        raise LocalMaintenanceAuthorizationError(
            "local maintenance authority cannot be verified for this process"
        )
    user_id = os.geteuid()
    try:
        database_path = Path(database).resolve(strict=True)
        script_path = Path(__file__).resolve(strict=True)
    except OSError:
        raise LocalMaintenanceAuthorizationError(
            "local maintenance files cannot be verified"
        ) from None
    for path in (database_path, script_path):
        metadata = path.stat()
        exposed_bits = 0o077 if path == database_path else 0o022
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != user_id
            or metadata.st_mode & exposed_bits
        ):
            raise LocalMaintenanceAuthorizationError(
                "local maintenance file permissions are not restricted"
            )
    directories = {
        directory
        for path in (database_path.parent, script_path.parent)
        for directory in (path, *path.parents)
    }
    for directory in directories:
        if not _directory_is_restricted(directory, user_id):
            raise LocalMaintenanceAuthorizationError(
                "local maintenance directory permissions are not restricted"
            )
    if not os.access(database_path, os.R_OK | os.W_OK):
        raise LocalMaintenanceAuthorizationError(
            "local maintenance database access cannot be verified"
        )


@contextmanager
def local_maintenance_guard(database: Path, *, plugin_root: Path = _PLUGIN_ROOT):
    """Authorize actual loaded code; hold OS proof until all resources close."""
    if Path(plugin_root) != _PLUGIN_ROOT:
        raise LocalMaintenanceAuthorizationError(
            "loaded maintenance code cannot be verified"
        )
    if os.name == "nt":
        with WindowsMaintenanceGuard(_PLUGIN_ROOT, Path(database)):
            yield
    elif _POSIX:
        authorize_local_maintenance(database)
        yield
    else:
        raise LocalMaintenanceAuthorizationError(
            "local maintenance authority cannot be verified"
        )


def _read_hidden(label: str, *, prompt=getpass.getpass) -> str:
    """A hidden prompt must fail before getpass can fall back to echoed input."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        return prompt(label)


async def _finish_cleanup(
    close: Callable[[], Awaitable[object]],
    *,
    confirmed_closed: Callable[[], bool],
    pending: tuple[type[Exception], ...] = (),
    after: Callable[[], Awaitable[object]] | None = None,
) -> None:
    """Keep OS pins until known pending cleanup drains, even on repeated cancel.

    Each close attempt retains its own timeout. A pending worker cannot safely
    outlive these pins, so the stopped-host CLI waits with a backoff until it
    closes; a supervisor may terminate the whole owned process if it stalls.
    """

    async def drain():
        failure = None
        while True:
            try:
                await close()
                break
            except pending:
                try:
                    await asyncio.sleep(0.05)
                except BaseException as error:
                    failure = error
                    break
            except BaseException as error:
                failure = error
                break
        if after is not None:
            try:
                await after()
            except BaseException as error:
                if failure is None:
                    failure = error
        notified = False
        while True:
            try:
                closed = confirmed_closed()
                if type(closed) is not bool:
                    raise RuntimeError("cleanup state is unavailable")
            except BaseException as error:
                closed = False
                if failure is None:
                    failure = error
            if closed:
                break
            if not notified:
                notified = True
                try:
                    print(
                        "Local maintenance cleanup is unconfirmed; keeping OS protection "
                        "until resources close or the owned process is terminated.",
                        file=sys.stderr,
                    )
                except BaseException as error:
                    if failure is None:
                        failure = error
            try:
                await asyncio.sleep(0.05)
            except BaseException as error:
                if failure is None:
                    failure = error
        if failure is not None:
            raise failure

    task = asyncio.create_task(drain())
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()  # Never swallow an unexpected cleanup failure.
    if cancelled:
        raise asyncio.CancelledError()


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        self.print_usage(sys.stderr)
        self.exit(
            2, "admin credential CLI: invalid arguments; values are not accepted\n"
        )


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description=__doc__)
    parser.add_argument(
        "--database",
        required=True,
        type=Path,
        help="Core SQLite database path (credential values are never accepted here)",
    )
    parser.add_argument("action", choices=("bootstrap", "rotate", "revoke", "recover"))
    return parser


def _read(label: str) -> str:
    return _read_hidden(f"{label} (hidden): ")


async def _run(database: Path, action: str) -> int:
    if action not in {"bootstrap", "rotate", "revoke", "recover"}:
        raise ValueError("unsupported operation")
    with local_maintenance_guard(database):
        repository = SQLiteAdminCredentialRepository(database)
        try:
            if action == "bootstrap":
                first = _read("Enter operator-generated base64url credential")
                confirmation = _read("Confirm new credential")
                if first != confirmation:
                    raise ValueError("credential confirmation did not match")
                state = await repository.bootstrap(_digest(first))
            elif action == "rotate":
                current = _read("Current credential")
                new = _read("New operator-generated base64url credential")
                confirmation = _read("Confirm new credential")
                if new != confirmation:
                    raise ValueError("credential confirmation did not match")
                state = await _rotate_local(repository, current, new)
            elif action == "revoke":
                confirmation = _read("Type REVOKE to confirm local revocation")
                if confirmation != "REVOKE":
                    raise ValueError("revocation confirmation did not match")
                state = await _revoke_local(repository)
            else:
                new = _read("New operator-generated base64url credential")
                confirmation = _read("Confirm new credential")
                if new != confirmation:
                    raise ValueError("credential confirmation did not match")
                current = await repository.current()
                if current.status is not AdminCredentialStatus.REVOKED:
                    raise AdminCredentialStateError(
                        "admin credential lifecycle conflict"
                    )
                state = await repository.recover(current.generation, _digest(new))
            print(
                f"Admin credential state: {state.status.value}; generation {state.generation}"
            )
            return 0
        finally:
            await _finish_cleanup(
                repository.database.executor.close,
                confirmed_closed=lambda: repository.database.executor.state == "CLOSED",
                pending=(SQLiteExecutorCloseTimeout,),
            )


async def _rotate_local(repository, current_credential: str, new_credential: str):
    current = await repository.current()
    if current.status is not AdminCredentialStatus.ACTIVE or not _matches(
        current_credential, current
    ):
        raise AdminCredentialStateError("admin credential lifecycle conflict")
    return await repository.rotate(current.generation, _digest(new_credential))


async def _revoke_local(repository):
    current = await repository.current()
    if current.status is not AdminCredentialStatus.ACTIVE:
        raise AdminCredentialStateError("admin credential lifecycle conflict")
    return await repository.revoke(current.generation)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return asyncio.run(_run(args.database, args.action))
    except Exception:
        # Never print parser, storage, or operator-input details.
        print("Admin credential operation failed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
