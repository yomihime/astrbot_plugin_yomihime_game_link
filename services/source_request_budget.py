"""Small persistent local ledger shared by interactive and scheduled HTTP.

Reservations count explicit transport attempts, including failures/retries.
The database is Core owned; callers cannot choose a filesystem path or host.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from time import time

from yomihime_game_link_sdk import HttpBudgetStatus, SourceHttpError


class SourceRequestBudget:
    def __init__(self, database, *, clock=time):
        self._database, self._clock = database, clock

    def _now(self):
        now = float(self._clock())
        if not math.isfinite(now) or now < 0:
            raise SourceHttpError("request_rejected")
        return now

    def _status(self, connection, declaration, limit, now):
        times = [
            row[0]
            for row in connection.execute(
                "SELECT attempted_at FROM source_request_attempts "
                "WHERE host=? AND attempted_at>? ORDER BY attempted_at",
                (declaration.host, now - 3600),
            )
        ]
        cooldown = connection.execute(
            "SELECT until_at FROM source_request_cooldowns WHERE host=?",
            (declaration.host,),
        ).fetchone()
        due = max(now, cooldown[0] if cooldown else now)
        if limit is not None and len(times) >= limit:
            due = max(due, times[len(times) - limit] + 3600)
        minute = [instant for instant in times if instant > now - 60]
        rpm = declaration.requests_per_minute
        if len(minute) >= rpm:
            due = max(due, minute[len(minute) - rpm] + 60)
        return HttpBudgetStatus(
            declaration.source_id,
            limit,
            len(times),
            max(0, limit - len(times)) if limit is not None else None,
            datetime.fromtimestamp(due, UTC) if due > now else None,
        )

    async def status(self, declaration, limit):
        now = self._now()
        return await self._database.executor.run_read(
            lambda unit: self._status(unit.connection, declaration, limit, now)
        )

    async def reserve(self, declaration, limit):
        now = self._now()

        def operation(unit):
            connection = unit.connection
            # One Core SQLite executor serializes this check+insert transaction.
            connection.execute(
                "DELETE FROM source_request_attempts WHERE attempted_at<=?",
                (now - 3600,),
            )
            status = self._status(connection, declaration, limit, now)
            if status.next_available_at is not None:
                return status
            connection.execute(
                "INSERT INTO source_request_attempts(host,attempted_at) VALUES(?,?)",
                (declaration.host, now),
            )
            return None

        status = await self._database.executor.run_transaction(
            operation, begin_mode="IMMEDIATE"
        )
        if status is not None:
            raise SourceHttpError(
                "rate_limited",
                retry_after_seconds=min(
                    86400,
                    max(1, math.ceil(status.next_available_at.timestamp() - now)),
                ),
                next_available_at=status.next_available_at,
            )

    async def cool_down(self, declaration, seconds):
        until = self._now() + max(1, min(86400, seconds))

        def operation(unit):
            unit.connection.execute(
                "INSERT INTO source_request_cooldowns(host,until_at) VALUES(?,?) "
                "ON CONFLICT(host) DO UPDATE SET until_at=max(until_at,excluded.until_at)",
                (declaration.host, until),
            )

        await self._database.executor.run_transaction(operation, begin_mode="IMMEDIATE")
