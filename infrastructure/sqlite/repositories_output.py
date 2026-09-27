"""SQLite persistence for root result-send claims and recovery."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ...core.ports import (
    MessageReceipt,
    MessageStatus,
    RootOutputClaim,
    RootOutputConflict,
    RootOutputOutcome,
    RootOutputState,
)
from .database import SQLiteDatabase, SQLiteUnitOfWork


def _utc(value: datetime, field: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{field} must be a timezone-aware UTC instant")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError(f"{field} must be a UTC instant")
    return value.astimezone(UTC)


def _stamp(value: datetime) -> str:
    return _utc(value, "timestamp").isoformat()


def _read(row: sqlite3.Row) -> RootOutputClaim:
    receipt = (
        None
        if row["receipt_status"] is None
        else MessageReceipt(
            MessageStatus(row["receipt_status"]), row["platform_message_id"]
        )
    )
    return RootOutputClaim(
        row["root_invocation_id"],
        row["output_identity"],
        row["payload_fingerprint"],
        RootOutputState(row["state"]),
        row["owner_token"],
        row["claim_generation"],
        datetime.fromisoformat(row["claimed_at"]),
        datetime.fromisoformat(row["lease_expires_at"]),
        None
        if row["output_outcome"] is None
        else RootOutputOutcome(row["output_outcome"]),
        receipt,
        row["error_code"],
    )


class SQLiteRootOutputRepository:
    """CAS repository; terminal rows expire after a bounded configured period.

    The retention window is an operational bound on duplicate suppression for
    a retry of the same invocation ID. It does not deduplicate new invocations.
    Live CLAIMED/SENDING rows are never pruned by retention.
    """

    def __init__(
        self,
        database: SQLiteDatabase | str | Path,
        *,
        retention: timedelta = timedelta(days=30),
    ) -> None:
        if isinstance(database, SQLiteDatabase):
            self.database = database
        elif isinstance(database, (str, Path)) and str(database).strip():
            self.database = SQLiteDatabase(database)
        else:
            raise TypeError("database must be a SQLiteDatabase or path")
        if not isinstance(retention, timedelta) or not timedelta(
            0
        ) < retention <= timedelta(days=3650):
            raise ValueError(
                "retention must be greater than zero and at most ten years"
            )
        self.retention = retention

    @staticmethod
    def _lookup(unit: SQLiteUnitOfWork, root_invocation_id: str) -> sqlite3.Row | None:
        return unit.execute(
            "SELECT * FROM b04_root_outputs WHERE root_invocation_id=?",
            (root_invocation_id,),
        ).fetchone()

    @staticmethod
    def _next_generation(unit: SQLiteUnitOfWork) -> int:
        row = unit.execute(
            "SELECT next_generation FROM b04_root_output_generation WHERE singleton=1"
        ).fetchone()
        if row is None:
            raise RuntimeError("root output generation counter is unavailable")
        generation = int(row["next_generation"])
        cursor = unit.execute(
            "UPDATE b04_root_output_generation SET next_generation=? WHERE singleton=1 AND next_generation=?",
            (generation + 1, generation),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("root output generation CAS failed")
        return generation

    async def claim(
        self,
        root_invocation_id: str,
        output_identity: str,
        payload_fingerprint: str,
        owner_token: str,
        *,
        now: datetime,
        lease_expires_at: datetime,
    ) -> RootOutputClaim:
        now = _utc(now, "now")
        lease_expires_at = _utc(lease_expires_at, "lease_expires_at")
        if lease_expires_at <= now:
            raise ValueError("claim lease must expire after now")
        cutoff = _stamp(now - self.retention)

        def _worker(unit: SQLiteUnitOfWork):
            unit.execute(
                "DELETE FROM b04_root_outputs WHERE state IN ('completed','unknown') AND updated_at < ?",
                (cutoff,),
            )
            row = self._lookup(unit, root_invocation_id)
            if row is None:
                candidate = RootOutputClaim(
                    root_invocation_id,
                    output_identity,
                    payload_fingerprint,
                    RootOutputState.CLAIMED,
                    owner_token,
                    self._next_generation(unit),
                    now,
                    lease_expires_at,
                )
                unit.execute(
                    "INSERT INTO b04_root_outputs(root_invocation_id,output_identity,payload_fingerprint,state,output_outcome,owner_token,claimed_at,lease_expires_at,receipt_status,platform_message_id,error_code,updated_at,claim_generation) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    self._values(candidate, updated_at=now),
                )
                return candidate
            current = _read(row)
            if (
                current.output_identity != output_identity
                or current.payload_fingerprint != payload_fingerprint
            ):
                raise RootOutputConflict(
                    "root invocation is already bound to another output identity or payload"
                )
            if current.state in (RootOutputState.COMPLETED, RootOutputState.UNKNOWN):
                return current
            if current.lease_expires_at > now:
                return current
            if current.state is RootOutputState.SENDING:
                unknown = RootOutputClaim(
                    current.root_invocation_id,
                    current.output_identity,
                    current.payload_fingerprint,
                    RootOutputState.UNKNOWN,
                    current.owner_token,
                    current.claim_generation,
                    current.claimed_at,
                    current.lease_expires_at,
                    None,
                    None,
                    "interrupted_send",
                )
                self._save(unit, unknown, updated_at=now)
                return unknown
            generation = self._next_generation(unit)
            unit.execute(
                "UPDATE b04_root_outputs SET owner_token=?,claimed_at=?,lease_expires_at=?,updated_at=?,claim_generation=? WHERE root_invocation_id=? AND claim_generation=? AND state='claimed' AND lease_expires_at<=?",
                (
                    owner_token,
                    _stamp(now),
                    _stamp(lease_expires_at),
                    _stamp(now),
                    generation,
                    root_invocation_id,
                    current.claim_generation,
                    _stamp(now),
                ),
            )
            refreshed = self._lookup(unit, root_invocation_id)
            assert refreshed is not None
            return _read(refreshed)

        return await self.database.executor.run_transaction(
            _worker, begin_mode="IMMEDIATE"
        )

    async def begin_sending(
        self,
        claim: RootOutputClaim,
        *,
        now: datetime,
        lease_expires_at: datetime,
    ) -> RootOutputClaim | None:
        now = _utc(now, "now")
        lease_expires_at = _utc(lease_expires_at, "lease_expires_at")
        if (
            not isinstance(claim, RootOutputClaim)
            or claim.state is not RootOutputState.CLAIMED
        ):
            raise TypeError("begin_sending requires a claimed output")
        if lease_expires_at <= now:
            raise ValueError("send lease must expire after now")

        def _worker(unit: SQLiteUnitOfWork):
            cursor = unit.execute(
                "UPDATE b04_root_outputs SET state='sending',claimed_at=?,lease_expires_at=?,updated_at=? WHERE root_invocation_id=? AND output_identity=? AND payload_fingerprint=? AND owner_token=? AND claim_generation=? AND state='claimed' AND lease_expires_at>?",
                (
                    _stamp(now),
                    _stamp(lease_expires_at),
                    _stamp(now),
                    claim.root_invocation_id,
                    claim.output_identity,
                    claim.payload_fingerprint,
                    claim.owner_token,
                    claim.claim_generation,
                    _stamp(now),
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = self._lookup(unit, claim.root_invocation_id)
            assert row is not None
            return _read(row)

        return await self.database.executor.run_transaction(
            _worker, begin_mode="IMMEDIATE"
        )

    async def complete(
        self,
        claim: RootOutputClaim,
        receipt: MessageReceipt | None,
        *,
        outcome: RootOutputOutcome,
        completed_at: datetime,
        error_code: str | None = None,
    ) -> RootOutputClaim | None:
        completed_at = _utc(completed_at, "completed_at")
        if not isinstance(claim, RootOutputClaim):
            raise TypeError("complete requires a root output claim")
        if not isinstance(outcome, RootOutputOutcome):
            raise TypeError("outcome must be a RootOutputOutcome")
        if receipt is not None and not isinstance(receipt, MessageReceipt):
            raise TypeError("receipt must be a MessageReceipt or None")
        if (outcome is RootOutputOutcome.MESSAGE) != (receipt is not None):
            raise ValueError("only a message outcome carries a MessageReceipt")
        expected_state = (
            RootOutputState.SENDING if receipt is not None else RootOutputState.CLAIMED
        )
        if claim.state is not expected_state:
            raise TypeError("claim state does not match the output route")
        if outcome is RootOutputOutcome.CONTROLLED_RESULT:
            if error_code is None:
                raise ValueError("controlled result requires an error code")
        elif error_code is not None:
            raise ValueError("error_code is only valid for controlled results")
        state = RootOutputState.COMPLETED

        def _worker(unit: SQLiteUnitOfWork):
            cursor = unit.execute(
                "UPDATE b04_root_outputs SET state=?,output_outcome=?,receipt_status=?,platform_message_id=?,error_code=?,updated_at=? WHERE root_invocation_id=? AND output_identity=? AND payload_fingerprint=? AND owner_token=? AND claim_generation=? AND state=?",
                (
                    state.value,
                    outcome.value,
                    None if receipt is None else receipt.status.value,
                    None if receipt is None else receipt.platform_message_id,
                    error_code,
                    _stamp(completed_at),
                    claim.root_invocation_id,
                    claim.output_identity,
                    claim.payload_fingerprint,
                    claim.owner_token,
                    claim.claim_generation,
                    expected_state.value,
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = self._lookup(unit, claim.root_invocation_id)
            assert row is not None
            return _read(row)

        return await self.database.executor.run_transaction(
            _worker, begin_mode="IMMEDIATE"
        )

    async def abort_before_dispatch(
        self,
        claim: RootOutputClaim,
        *,
        completed_at: datetime,
        error_code: str,
    ) -> RootOutputClaim | None:
        """Close a SENDING claim only when dispatch is known not to have run."""

        completed_at = _utc(completed_at, "completed_at")
        if (
            not isinstance(claim, RootOutputClaim)
            or claim.state is not RootOutputState.SENDING
            or claim.receipt is not None
        ):
            raise TypeError(
                "abort_before_dispatch requires a receipt-free sending claim"
            )
        if not isinstance(error_code, str) or not error_code.strip():
            raise ValueError("abort_before_dispatch requires an error code")

        def _worker(unit: SQLiteUnitOfWork):
            cursor = unit.execute(
                "UPDATE b04_root_outputs SET state='completed',output_outcome='controlled_result',receipt_status=NULL,platform_message_id=NULL,error_code=?,updated_at=? "
                "WHERE root_invocation_id=? AND output_identity=? AND payload_fingerprint=? "
                "AND owner_token=? AND claim_generation=? AND state='sending' "
                "AND receipt_status IS NULL AND platform_message_id IS NULL",
                (
                    error_code,
                    _stamp(completed_at),
                    claim.root_invocation_id,
                    claim.output_identity,
                    claim.payload_fingerprint,
                    claim.owner_token,
                    claim.claim_generation,
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = self._lookup(unit, claim.root_invocation_id)
            assert row is not None
            return _read(row)

        return await self.database.executor.run_transaction(
            _worker, begin_mode="IMMEDIATE"
        )

    async def recover_expired(
        self, *, before: datetime, recovered_at: datetime
    ) -> tuple[RootOutputClaim, ...]:
        before = _utc(before, "before")
        recovered_at = _utc(recovered_at, "recovered_at")

        def _worker(unit: SQLiteUnitOfWork) -> tuple[RootOutputClaim, ...]:
            recovered: list[RootOutputClaim] = []
            rows = unit.execute(
                "SELECT * FROM b04_root_outputs WHERE state='sending' AND lease_expires_at<=? ORDER BY root_invocation_id,output_identity",
                (_stamp(before),),
            ).fetchall()
            for row in rows:
                current = _read(row)
                unknown = RootOutputClaim(
                    current.root_invocation_id,
                    current.output_identity,
                    current.payload_fingerprint,
                    RootOutputState.UNKNOWN,
                    current.owner_token,
                    current.claim_generation,
                    current.claimed_at,
                    current.lease_expires_at,
                    None,
                    None,
                    "interrupted_send",
                )
                cursor = unit.execute(
                    "UPDATE b04_root_outputs SET state='unknown',output_outcome=NULL,receipt_status=NULL,platform_message_id=NULL,error_code='interrupted_send',updated_at=? WHERE root_invocation_id=? AND output_identity=? AND owner_token=? AND claim_generation=? AND state='sending' AND lease_expires_at<=?",
                    (
                        _stamp(recovered_at),
                        current.root_invocation_id,
                        current.output_identity,
                        current.owner_token,
                        current.claim_generation,
                        _stamp(before),
                    ),
                )
                if cursor.rowcount == 1:
                    recovered.append(unknown)
            return tuple(recovered)

        return await self.database.executor.run_transaction(
            _worker, begin_mode="IMMEDIATE"
        )

    @staticmethod
    def _values(claim: RootOutputClaim, *, updated_at: datetime) -> tuple[object, ...]:
        receipt = claim.receipt
        return (
            claim.root_invocation_id,
            claim.output_identity,
            claim.payload_fingerprint,
            claim.state.value,
            None if claim.outcome is None else claim.outcome.value,
            claim.owner_token,
            _stamp(claim.claimed_at),
            _stamp(claim.lease_expires_at),
            None if receipt is None else receipt.status.value,
            None if receipt is None else receipt.platform_message_id,
            claim.error_code,
            _stamp(updated_at),
            claim.claim_generation,
        )

    def _save(
        self, unit: SQLiteUnitOfWork, claim: RootOutputClaim, *, updated_at: datetime
    ) -> None:
        unit.execute(
            "UPDATE b04_root_outputs SET state=?,output_outcome=?,owner_token=?,claimed_at=?,lease_expires_at=?,receipt_status=?,platform_message_id=?,error_code=?,updated_at=? WHERE root_invocation_id=? AND output_identity=? AND claim_generation=?",
            (
                claim.state.value,
                None if claim.outcome is None else claim.outcome.value,
                claim.owner_token,
                _stamp(claim.claimed_at),
                _stamp(claim.lease_expires_at),
                None if claim.receipt is None else claim.receipt.status.value,
                None if claim.receipt is None else claim.receipt.platform_message_id,
                claim.error_code,
                _stamp(updated_at),
                claim.root_invocation_id,
                claim.output_identity,
                claim.claim_generation,
            ),
        )
