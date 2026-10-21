"""Inspect a read-only migration plan; production execution is not supported.

Layout resolution can create empty identity-owned private scaffolding on Windows.
Planning never copies or changes database, key, secret, selector or journal content.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from ..infrastructure.private_storage import (
    PrivateStorageError,
    resolve_private_storage,
)
from ..infrastructure.private_storage_migration import plan_storage_migration


async def run(mode, data_root, *, private_base=None):
    if mode != "plan":
        raise PrivateStorageError("offline_supervisor_unavailable")
    layout = resolve_private_storage(data_root, private_base=private_base)
    plan = plan_storage_migration(layout)
    return plan.safe_projection()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode", choices=("plan",), help="read-only plan; no production executor"
    )
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.data_root.is_absolute() or ".." in args.data_root.parts:
        parser.error("data root must be an absolute normalized path")
    try:
        result = asyncio.run(run(args.mode, args.data_root))
    except PrivateStorageError as error:
        print(json.dumps({"status": "blocked", "code": error.code}))
        return 2
    print(json.dumps({"status": "ok", "data": result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
