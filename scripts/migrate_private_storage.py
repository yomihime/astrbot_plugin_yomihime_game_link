"""Plan content transfer or execute with trusted supervision, without a stopped flag.

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
from ..infrastructure.private_storage_migration import (
    execute_storage_migration,
    plan_storage_migration,
)
from ..services.storage_migration_supervisor import (
    deployment_supervisor_for_existing_source,
)


async def run(mode, data_root, *, private_base=None):
    layout = resolve_private_storage(data_root, private_base=private_base)
    plan = plan_storage_migration(layout)
    if mode == "plan":
        return plan.safe_projection()
    # There is currently no production deployment adapter. This lookup denies
    # before taking a target guard or transferring DB/key/secret content and
    # publishing a selector/journal. The resolver above may create scaffolding.
    supervisor = deployment_supervisor_for_existing_source(layout)
    async with supervisor.acquire_offline_source(plan) as proof:
        with layout.open_guard() as guard:
            return execute_storage_migration(
                plan, source_proof=proof, private_guard=guard
            ).safe_projection()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "execute"))
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
