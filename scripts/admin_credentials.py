"""Local hidden-input maintenance CLI for Core admin credentials."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import stat
import sys
from pathlib import Path

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
    """Require an interactive POSIX owner process with private files/parents.

    Windows ACL inspection is not implemented here, so Windows invocations
    fail closed. A future host-specific ACL verifier can replace this seam.
    """
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
    return getpass.getpass(f"{label} (hidden): ")


async def _run(database: Path, action: str) -> int:
    authorize_local_maintenance(database)
    repository = SQLiteAdminCredentialRepository(database)
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
            raise AdminCredentialStateError("admin credential lifecycle conflict")
        state = await repository.recover(current.generation, _digest(new))
    print(
        f"Admin credential state: {state.status.value}; generation {state.generation}"
    )
    return 0


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
