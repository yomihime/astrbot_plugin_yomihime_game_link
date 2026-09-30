"""SQLite repositories and resource visibility for the bounded B04 runtime."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime

from ..infrastructure.sqlite.database import (
    SQLiteDatabase,
    SQLiteDatabaseError,
    SQLiteUnitOfWork,
)
from ..infrastructure.sqlite.repositories_subscriptions import (
    SQLiteDeliveryRepository,
    SQLiteDigestWindowRepository,
    SQLiteSchedulerRepository,
    SQLiteSubscriptionJobRepository,
    SQLiteSubscriptionLifecycleRepository,
    SQLiteSubscriptionStore,
)


class SQLiteResourceVisibilityProbe:
    """Bounded all-scope probe for private asset IDs embedded in output text."""

    MAX_REFERENCE_CHARS = 4096
    MAX_STORED_ASSET_ID_BYTES = 512
    MAX_CANDIDATES = 256
    MAX_SQLITE_VM_STEPS = 25_000

    def __init__(
        self,
        database: SQLiteDatabase,
        *,
        max_candidates: int = MAX_CANDIDATES,
    ) -> None:
        if not isinstance(database, SQLiteDatabase):
            raise TypeError("database must be SQLiteDatabase")
        if (
            type(max_candidates) is not int
            or not 1 <= max_candidates <= self.MAX_CANDIDATES
        ):
            raise ValueError("max_candidates must be between 1 and 256")
        self.database = database
        self.max_candidates = max_candidates

    async def contains_non_public_resource_reference(self, text: str) -> bool:
        if type(text) is not str or len(text) > self.MAX_REFERENCE_CHARS:
            raise ValueError("resource reference candidate exceeds bounds")

        def read(unit: SQLiteUnitOfWork) -> tuple[str, ...]:
            connection = unit.connection
            # Parse the table schema before lowering SQLite's value-size
            # limit; otherwise SQLite may reject the schema DDL itself.
            unit.execute("SELECT asset_id FROM assets LIMIT 0")
            # SQLite rejects an oversized stored TEXT value before returning it
            # to Python, bounding per-candidate memory and substring work.
            connection.setlimit(
                sqlite3.SQLITE_LIMIT_LENGTH, self.MAX_STORED_ASSET_ID_BYTES
            )
            steps = 0

            def bound_query_work() -> int:
                nonlocal steps
                steps += 1000
                return int(steps > self.MAX_SQLITE_VM_STEPS)

            connection.set_progress_handler(bound_query_work, 1000)
            try:
                rows = unit.execute(
                    "SELECT asset_id FROM assets WHERE scope_kind <> 'public' "
                    "ORDER BY asset_id LIMIT ?",
                    (self.max_candidates + 1,),
                ).fetchall()
                return tuple(str(row[0]) for row in rows)
            finally:
                connection.set_progress_handler(None, 0)

        try:
            asset_ids = await self.database.executor.run_read(read)
        except (SQLiteDatabaseError, sqlite3.Error):
            # Query errors and SQLite work-budget exhaustion fail closed.
            raise RuntimeError("resource visibility probe failed") from None
        if len(asset_ids) > self.max_candidates:
            raise RuntimeError("resource visibility work limit exceeded")
        return any(asset_id in text for asset_id in asset_ids)


class B04Repositories:
    """All B04 stores sharing the host's initialized SQLite database."""

    def __init__(
        self,
        database: SQLiteDatabase,
        *,
        utc_clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database
        self.subscriptions = SQLiteSubscriptionStore(database)
        self.jobs = SQLiteSubscriptionJobRepository(database)
        self.lifecycle = SQLiteSubscriptionLifecycleRepository(database)
        self.scheduler = SQLiteSchedulerRepository(database, utc_clock=utc_clock)
        self.windows = SQLiteDigestWindowRepository(database)
        self.deliveries = SQLiteDeliveryRepository(database)


__all__ = [
    "B04Repositories",
    "SQLiteResourceVisibilityProbe",
]
