"""Persistent B04 subscription, collection, evaluation, digest, and delivery stores.

Each public operation uses the B03 SQLite unit of work. Combined operations
write through a single ``BEGIN IMMEDIATE`` transaction so revisions, leases,
observations, and outputs cannot be observed half committed.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from dataclasses import fields, is_dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping

from ...api.display import (
    CommandsBlock,
    DisplayDocument,
    FieldsBlock,
    GridItem,
    ImageBlock,
    ItemGridBlock,
    Link,
    LinksBlock,
    MetricsBlock,
    MoneyValue,
    NumberValue,
    Privacy,
    SeriesBlock,
    TableBlock,
    TextBlock,
    TimeValue,
    UnknownBlock,
)
from ...api.storage import GrantReference, OwnerScope, OwnershipKind
from ...api.subscriptions import (
    ActiveDigestSchedule,
    CollectionKey,
    ConversationKind,
    ConversationRef,
    DeliveryAttempt,
    DeliveryEvent,
    DeliveryEventCursor,
    DeliveryState,
    DigestEnvelope,
    DigestEnvelopeClaim,
    DigestEnvelopeState,
    DigestMember,
    DigestMemberAssociation,
    DigestMemberDisposition,
    DigestMemberReceipt,
    DigestRouteCandidate,
    DigestRouteCursor,
    DigestScheduleProfile,
    DigestWindow,
    DigestWindowSelector,
    DstFoldPolicy,
    DstGapPolicy,
    DueCollectionJob,
    DueJobCursor,
    EvaluationState,
    NormalizedInput,
    Observation,
    ObservationCompleteness,
    ObservationCursor,
    ObservationEvaluationCommit,
    SubscriptionEvaluationCommit,
    SubscriptionEvaluationSnapshot,
    SubscriptionJobAssociation,
    SubscriptionJobChange,
    SubscriptionJobChangeKind,
    SubscriptionRecord,
    SubscriptionStatus,
    digest_envelope_idempotency_key,
)
from ...api.version import CONTRACT_VERSION
from ...core.ports import (
    CollectionRunRequest,
    EvaluationCheckpoint,
    ExecutionLease,
    PersistedSubscriptionGateRead,
    RevisionConflict,
    SubscriptionFence,
    SubscriptionGateBinding,
    UniqueConstraintViolation,
)
from .database import SQLiteDatabase, SQLiteUnitOfWork

_StoredDateTime = datetime


def _immutable_digest_event_sql(event_alias: str) -> str:
    """Shared SQL identity check for events in immutable digest envelopes."""

    return (
        "EXISTS (SELECT 1 FROM b04_digest_members AS dm "
        "JOIN b04_digest_envelopes AS de ON de.window_id=dm.window_id "
        "AND de.recipient_key=dm.recipient_key "
        f"WHERE dm.event_key={event_alias}.event_key "
        f"AND dm.event_version={event_alias}.event_version "
        f"AND dm.subscription_id={event_alias}.subscription_id "
        f"AND dm.subscription_revision={event_alias}.subscription_revision "
        "AND de.state IN ('sending','unknown','sent'))"
    )


_DATACLASSES = {
    cls.__name__: cls
    for cls in (
        CollectionKey,
        ActiveDigestSchedule,
        NormalizedInput,
        OwnerScope,
        GrantReference,
        ConversationRef,
        SubscriptionRecord,
        SubscriptionJobAssociation,
        DigestMember,
        DigestWindow,
        DigestWindowSelector,
        DigestRouteCandidate,
        DigestRouteCursor,
        DigestScheduleProfile,
        DeliveryEventCursor,
        DueJobCursor,
        DueCollectionJob,
        DigestMemberAssociation,
        DigestMemberReceipt,
        DigestEnvelope,
        DeliveryAttempt,
        DeliveryEvent,
        EvaluationState,
        Observation,
        ObservationCursor,
        ObservationEvaluationCommit,
        SubscriptionEvaluationCommit,
        SubscriptionEvaluationSnapshot,
        DisplayDocument,
        TextBlock,
        FieldsBlock,
        MetricsBlock,
        TableBlock,
        ItemGridBlock,
        GridItem,
        ImageBlock,
        SeriesBlock,
        LinksBlock,
        CommandsBlock,
        Link,
        UnknownBlock,
        NumberValue,
        MoneyValue,
        TimeValue,
    )
}
_ENUMS = {
    cls.__name__: cls
    for cls in (
        OwnershipKind,
        ConversationKind,
        SubscriptionStatus,
        ObservationCompleteness,
        DstFoldPolicy,
        DstGapPolicy,
        DigestMemberDisposition,
        DigestEnvelopeState,
        DeliveryState,
        Privacy,
    )
}


def _encode(value: Any) -> Any:
    if isinstance(value, Enum):
        return {"$enum": type(value).__name__, "value": value.value}
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if is_dataclass(value):
        return {
            "$type": type(value).__name__,
            "fields": {
                item.name: _encode(getattr(value, item.name))
                for item in fields(value)
                if item.init
            },
        }
    if isinstance(value, Mapping):
        return {str(key): _encode(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_encode(item) for item in value]
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise TypeError("value cannot be stored as B04 JSON")


def _decode(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_decode(item) for item in value)
    if isinstance(value, dict):
        if "$enum" in value:
            return _ENUMS[value["$enum"]](value["value"])
        if "$datetime" in value:
            return _StoredDateTime.fromisoformat(value["$datetime"])
        if "$date" in value:
            return date.fromisoformat(value["$date"])
        if "$decimal" in value:
            return Decimal(value["$decimal"])
        if "$type" in value:
            cls = _DATACLASSES[value["$type"]]
            stored_fields = value["fields"]
            if cls is DisplayDocument:
                # Only the frozen 1.7 document shape is wire-compatible with
                # 1.8. Reconstruct current DTOs here, leaving public SDK and
                # renderer version checks strict and persisted bytes untouched.
                version = stored_fields["schema_version"]
                if version == "1.7.0" and CONTRACT_VERSION == "1.8.0":
                    if set(stored_fields) != {
                        "title",
                        "subject",
                        "ordered_blocks",
                        "sources",
                        "timestamps",
                        "privacy",
                        "schema_version",
                    }:
                        raise ValueError("invalid stored 1.7 display shape")
                    stored_fields = {**stored_fields, "schema_version": CONTRACT_VERSION}
            return cls(**{key: _decode(item) for key, item in stored_fields.items()})
        return {key: _decode(item) for key, item in value.items()}
    return value


def _dump(value: Any) -> str:
    return json.dumps(
        _encode(value),
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    )


def _load(value: str) -> Any:
    try:
        return _decode(json.loads(value))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("stored B04 record is invalid") from None


def _key_json(key: CollectionKey) -> str:
    return _dump(key)


def _job_key(key: CollectionKey) -> str:
    return hashlib.sha256(_key_json(key).encode("utf-8")).hexdigest()


def _route_key(recipient: object) -> str:
    return hashlib.sha256(_dump(recipient).encode("utf-8")).hexdigest()


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _utc_instant(value: datetime, field: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{field} must be a UTC instant")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError(f"{field} must be a UTC instant")
    return value.astimezone(UTC)


class _StaleEvaluationCommit(Exception):
    """Internal rollback signal for a commit that lost a revision race."""


def _db(value: SQLiteDatabase | str | Path) -> SQLiteDatabase:
    if isinstance(value, SQLiteDatabase):
        return value
    if isinstance(value, (str, Path)):
        return SQLiteDatabase(value)
    raise TypeError("database must be a SQLiteDatabase or path")


class _Repository:
    def __init__(
        self,
        database: SQLiteDatabase | str | Path,
        *,
        subscription_gate_bindings: Mapping[str, SubscriptionGateBinding] | None = None,
    ) -> None:
        self.database = _db(database)
        bindings = dict(subscription_gate_bindings or {})
        if any(
            type(value) is not SubscriptionGateBinding or key != value.target.module_id
            for key, value in bindings.items()
        ):
            raise ValueError("subscription gate binding map is invalid")
        self._subscription_gate_bindings = MappingProxyType(bindings)


def _row_fence(row: sqlite3.Row | None) -> SubscriptionFence | None:
    if row is None:
        return None
    try:
        return SubscriptionFence(row["gate_revision"], row["intent_revision"])
    except (TypeError, ValueError):
        return None


def _read_subscription_admission(
    unit: SQLiteUnitOfWork, binding: SubscriptionGateBinding | None
) -> tuple[SubscriptionFence, datetime] | None:
    """Read only the exact ordinary gate and intent in the caller's transaction."""
    if binding is None:
        return None
    phase = unit.execute(
        "SELECT phase FROM subscription_gate_bootstrap WHERE singleton=1"
    ).fetchone()
    if phase is None or phase[0] != "complete":
        return None
    marker = unit.execute(
        "SELECT 1 FROM subscription_gate_initializations WHERE principal_id=? AND module_id=? AND field=?",
        (binding.target.principal_id, binding.target.module_id, binding.field),
    ).fetchone()
    gate = unit.execute(
        "SELECT value_json,revision,subscription_transition_at FROM config_entries WHERE principal_id=? AND module_id=? AND field=?",
        (binding.target.principal_id, binding.target.module_id, binding.field),
    ).fetchone()
    intent = unit.execute(
        "SELECT desired_enabled,intent_revision,updated_at FROM module_runtime_intents WHERE package_id=? AND module_id=?",
        (binding.package_id, binding.module_id),
    ).fetchone()
    if marker is None or gate is None or intent is None:
        return None
    try:
        if (
            json.loads(gate["value_json"]) is not True
            or type(intent["desired_enabled"]) is not int
            or intent["desired_enabled"] != 1
        ):
            return None
        gate_cutoff = _utc_instant(
            _StoredDateTime.fromisoformat(gate["subscription_transition_at"]),
            "gate cutoff",
        )
        intent_cutoff = _utc_instant(
            _StoredDateTime.fromisoformat(intent["updated_at"]), "intent cutoff"
        )
        return (
            SubscriptionFence(gate["revision"], intent["intent_revision"]),
            max(gate_cutoff, intent_cutoff),
        )
    except (TypeError, ValueError, OverflowError):
        return None


def _read_subscription_fence(
    unit: SQLiteUnitOfWork, binding: SubscriptionGateBinding | None
) -> SubscriptionFence | None:
    admission = _read_subscription_admission(unit, binding)
    return None if admission is None else admission[0]


def _scope_values(
    record: SubscriptionRecord,
) -> tuple[str, str | None, str | None, int | None]:
    scope = record.collection_key.scope
    grant = scope.grant
    return (
        scope.kind.value,
        scope.user_id,
        None if grant is None else grant.grant_id,
        None if grant is None else grant.revision,
    )


def _put_job(
    unit: SQLiteUnitOfWork,
    key: CollectionKey,
    initial_run: CollectionRunRequest | None = None,
) -> str:
    if initial_run is not None:
        if not isinstance(initial_run, CollectionRunRequest):
            raise TypeError("initial_run must be a CollectionRunRequest")
        initial_run.__post_init__()
        if initial_run.key != key:
            raise ValueError("initial_run must match the collection key")
    identity = _job_key(key)
    scope = key.scope
    grant = scope.grant
    unit.execute(
        "INSERT OR IGNORE INTO b04_collection_jobs(job_key,key_json,scope_kind,owner_id,grant_id,grant_revision,due_at,cadence_seconds,config_revision,module_epoch,registry_revision) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            identity,
            _key_json(key),
            scope.kind.value,
            scope.user_id,
            None if grant is None else grant.grant_id,
            None if grant is None else grant.revision,
            None if initial_run is None else _iso(initial_run.due_at),
            None if initial_run is None else initial_run.cadence_seconds,
            None if initial_run is None else initial_run.config_revision,
            None if initial_run is None else initial_run.module_epoch,
            None if initial_run is None else initial_run.registry_revision,
        ),
    )
    row = unit.execute(
        "SELECT key_json,due_at,cadence_seconds,config_revision,module_epoch,registry_revision,lease_token,lease_expires_at FROM b04_collection_jobs WHERE job_key=?",
        (identity,),
    ).fetchone()
    if row is None or row[0] != _key_json(key):
        raise ValueError("collection key digest collision")
    if initial_run is not None:
        schedule = tuple(
            row[name]
            for name in (
                "due_at",
                "cadence_seconds",
                "config_revision",
                "module_epoch",
                "registry_revision",
            )
        )
        if all(value is None for value in schedule):
            if row["lease_token"] is not None or row["lease_expires_at"] is not None:
                raise ValueError("unarmed collection job cannot retain a lease")
            cursor = unit.execute(
                "UPDATE b04_collection_jobs SET due_at=?,cadence_seconds=?,config_revision=?,module_epoch=?,registry_revision=? WHERE job_key=? AND due_at IS NULL AND cadence_seconds IS NULL AND config_revision IS NULL AND module_epoch IS NULL AND registry_revision IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL",
                (
                    _iso(initial_run.due_at),
                    initial_run.cadence_seconds,
                    initial_run.config_revision,
                    initial_run.module_epoch,
                    initial_run.registry_revision,
                    identity,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("collection job schedule changed during arm")
        elif any(value is None for value in schedule):
            raise ValueError("collection job schedule is incomplete")
    return identity


def _record_row(unit: SQLiteUnitOfWork, record: SubscriptionRecord) -> None:
    scope_kind, scope_user, grant_id, grant_revision = _scope_values(record)
    unit.execute(
        "INSERT INTO b04_subscriptions(subscription_id,revision,owner_id,scope_kind,scope_user,grant_id,grant_revision,status,record_json) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            record.subscription_id,
            record.revision,
            record.owner_id,
            scope_kind,
            scope_user,
            grant_id,
            grant_revision,
            record.status.value,
            _dump(record),
        ),
    )


def _read_record(row: sqlite3.Row | None) -> SubscriptionRecord | None:
    return None if row is None else _load(row["record_json"])


def _read_event(row: sqlite3.Row | None) -> DeliveryEvent | None:
    return None if row is None else _load(row["event_json"])


def _upsert_event(
    unit: SQLiteUnitOfWork,
    event: DeliveryEvent,
    *,
    fence: SubscriptionFence | None = None,
) -> DeliveryEvent:
    if fence is not None:
        old = unit.execute(
            "SELECT gate_revision,intent_revision FROM b04_delivery_events WHERE event_key=? AND event_version=? AND subscription_id=? AND subscription_revision=?",
            (
                event.event_key,
                event.event_version,
                event.subscription_id,
                event.subscription_revision,
            ),
        ).fetchone()
        if old is not None and _row_fence(old) != fence:
            raise _StaleEvaluationCommit
    try:
        unit.execute(
            "INSERT INTO b04_delivery_events(event_key,event_version,subscription_id,subscription_revision,owner_id,grant_id,grant_revision,idempotency_key,state,attempt_number,retry_at,event_json,gate_revision,intent_revision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event.event_key,
                event.event_version,
                event.subscription_id,
                event.subscription_revision,
                event.owner_id,
                None if event.grant is None else event.grant.grant_id,
                None if event.grant is None else event.grant.revision,
                event.idempotency_key,
                event.state.value,
                0 if event.attempt is None else event.attempt.attempt_number,
                None if event.retry_at is None else _iso(event.retry_at),
                _dump(event),
                None if fence is None else fence.gate_revision,
                None if fence is None else fence.intent_revision,
            ),
        )
    except Exception as exc:
        if isinstance(exc, UniqueConstraintViolation):
            raise
        row = unit.execute(
            "SELECT event_json,gate_revision,intent_revision FROM b04_delivery_events WHERE event_key=? AND event_version=? AND subscription_id=? AND subscription_revision=?",
            (
                event.event_key,
                event.event_version,
                event.subscription_id,
                event.subscription_revision,
            ),
        ).fetchone()
        existing = _read_event(row)
        if row is not None and fence is not None and _row_fence(row) != fence:
            raise _StaleEvaluationCommit from None
        if existing == event:
            return existing
        raise
    if event.attempt is not None:
        unit.execute(
            "INSERT INTO b04_delivery_attempts(event_key,event_version,subscription_id,subscription_revision,attempt_number,state,attempt_json) VALUES (?,?,?,?,?,?,?)",
            (
                event.event_key,
                event.event_version,
                event.subscription_id,
                event.subscription_revision,
                event.attempt.attempt_number,
                event.attempt.state.value,
                _dump(event.attempt),
            ),
        )
    return event


class SQLiteSubscriptionStore(_Repository):
    async def create(self, record: SubscriptionRecord) -> SubscriptionRecord:
        if record.revision != 1 or record.status is not SubscriptionStatus.ACTIVE:
            raise ValueError("subscription create requires active revision one")

        def _run(unit: SQLiteUnitOfWork) -> Any:
            try:
                _record_row(unit, record)
            except UniqueConstraintViolation:
                raise UniqueConstraintViolation("subscription") from None

        await self.database.executor.run_transaction(_run, begin_mode="IMMEDIATE")
        return record

    async def revise(
        self, record: SubscriptionRecord, *, expected_revision: int
    ) -> SubscriptionRecord:
        if (
            record.revision != expected_revision + 1
            or record.status is not SubscriptionStatus.ACTIVE
        ):
            raise ValueError("subscription revise must advance an active revision")

        def _run(unit: SQLiteUnitOfWork) -> Any:
            row = unit.execute(
                "SELECT revision FROM b04_subscriptions WHERE subscription_id=?",
                (record.subscription_id,),
            ).fetchone()
            actual = None if row is None else int(row[0])
            cursor = unit.execute(
                "UPDATE b04_subscriptions SET revision=?,owner_id=?,scope_kind=?,scope_user=?,grant_id=?,grant_revision=?,status=?,record_json=? WHERE subscription_id=? AND revision=?",
                (
                    record.revision,
                    record.owner_id,
                    *_scope_values(record),
                    record.status.value,
                    _dump(record),
                    record.subscription_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise RevisionConflict(
                    "subscription",
                    expected_revision,
                    expected_revision if actual is None else actual,
                )

        await self.database.executor.run_transaction(_run, begin_mode="IMMEDIATE")
        return record

    async def current(self, subscription_id: str) -> SubscriptionRecord | None:
        def _run(unit: SQLiteUnitOfWork) -> Any:
            row = unit.execute(
                "SELECT record_json FROM b04_subscriptions WHERE subscription_id=?",
                (subscription_id,),
            ).fetchone()
            return _read_record(row)

        return await self.database.executor.run_read(_run)

    async def current_for_owner(
        self, subscription_id: str, owner_id: str
    ) -> SubscriptionRecord | None:
        """Read a subscription only inside its persisted owner partition."""

        def _run(unit: SQLiteUnitOfWork) -> Any:
            row = unit.execute(
                "SELECT record_json FROM b04_subscriptions WHERE subscription_id=? AND owner_id=?",
                (subscription_id, owner_id),
            ).fetchone()
            return _read_record(row)

        return await self.database.executor.run_read(_run)

    async def list_for_owner(
        self, owner_id: str, *, limit: int, after_subscription_id: str | None = None
    ) -> tuple[SubscriptionRecord, ...]:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be a positive integer")

        def _run(unit: SQLiteUnitOfWork) -> Any:
            if after_subscription_id is None:
                rows = unit.execute(
                    "SELECT record_json FROM b04_subscriptions WHERE owner_id=? AND status='active' ORDER BY subscription_id LIMIT ?",
                    (owner_id, limit),
                ).fetchall()
            else:
                rows = unit.execute(
                    "SELECT record_json FROM b04_subscriptions WHERE owner_id=? AND status='active' AND subscription_id>? ORDER BY subscription_id LIMIT ?",
                    (owner_id, after_subscription_id, limit),
                ).fetchall()
            return tuple(_load(row[0]) for row in rows)

        return await self.database.executor.run_read(_run)

    async def list_active_digest_schedules(
        self, *, limit: int, after_subscription_id: str | None = None
    ) -> tuple[ActiveDigestSchedule, ...]:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be a positive integer")

        def _run(unit: SQLiteUnitOfWork) -> Any:
            if after_subscription_id is None:
                rows = unit.execute(
                    "SELECT record_json FROM b04_subscriptions WHERE status='active' AND json_extract(record_json,'$.fields.type_id') IS NOT NULL AND json_extract(record_json,'$.fields.notification_mode')='digest' AND json_extract(record_json,'$.fields.digest_schedule') IS NOT NULL ORDER BY subscription_id LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = unit.execute(
                    "SELECT record_json FROM b04_subscriptions WHERE status='active' AND subscription_id>? AND json_extract(record_json,'$.fields.type_id') IS NOT NULL AND json_extract(record_json,'$.fields.notification_mode')='digest' AND json_extract(record_json,'$.fields.digest_schedule') IS NOT NULL ORDER BY subscription_id LIMIT ?",
                    (after_subscription_id, limit),
                ).fetchall()
            schedules = []
            for row in rows:
                record = _load(row[0])
                if record.type_id is not None and record.digest_schedule is not None:
                    schedules.append(ActiveDigestSchedule(record))
            return tuple(schedules)

        return await self.database.executor.run_read(_run)

    async def cancel(
        self, subscription_id: str, *, expected_revision: int
    ) -> SubscriptionRecord:
        def _run(unit: SQLiteUnitOfWork) -> Any:
            row = unit.execute(
                "SELECT record_json FROM b04_subscriptions WHERE subscription_id=?",
                (subscription_id,),
            ).fetchone()
            current = _read_record(row)
            if current is None or current.revision != expected_revision:
                raise RevisionConflict(
                    "subscription",
                    expected_revision,
                    expected_revision if current is None else current.revision,
                )
            updated = SubscriptionRecord(
                current.subscription_id,
                current.revision + 1,
                current.module_id,
                current.collection_key,
                current.owner_id,
                current.grant,
                current.recipient,
                current.notification_mode,
                current.filters,
                SubscriptionStatus.CANCELLED,
                current.type_id,
                current.digest_schedule,
            )
            unit.execute(
                "UPDATE b04_subscriptions SET revision=?,status='cancelled',record_json=? WHERE subscription_id=? AND revision=?",
                (updated.revision, _dump(updated), subscription_id, expected_revision),
            )
            return updated

        return await self.database.executor.run_transaction(
            _run, begin_mode="IMMEDIATE"
        )


class SQLiteSubscriptionJobRepository(_Repository):
    async def associate(
        self, association: SubscriptionJobAssociation, *, expected_revision: int | None
    ) -> SubscriptionJobAssociation:
        if expected_revision is None and association.association_revision != 1:
            raise ValueError(
                "first subscription-job association must start at revision one"
            )
        if (
            expected_revision is not None
            and association.association_revision != expected_revision + 1
        ):
            raise ValueError("subscription-job association must advance one revision")

        def _run(unit: SQLiteUnitOfWork) -> Any:
            job = _put_job(unit, association.collection_key)
            if expected_revision is None:
                try:
                    unit.execute(
                        "INSERT INTO b04_subscription_jobs VALUES (?,?,?,?,?,?)",
                        (
                            association.subscription_id,
                            job,
                            association.subscription_revision,
                            association.association_revision,
                            association.cadence_seconds,
                            association.config_revision,
                        ),
                    )
                except UniqueConstraintViolation:
                    raise UniqueConstraintViolation("subscription-job") from None
            else:
                cursor = unit.execute(
                    "UPDATE b04_subscription_jobs SET job_key=?,subscription_revision=?,association_revision=?,cadence_seconds=?,config_revision=? WHERE subscription_id=? AND association_revision=?",
                    (
                        job,
                        association.subscription_revision,
                        association.association_revision,
                        association.cadence_seconds,
                        association.config_revision,
                        association.subscription_id,
                        expected_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    row = unit.execute(
                        "SELECT association_revision FROM b04_subscription_jobs WHERE subscription_id=?",
                        (association.subscription_id,),
                    ).fetchone()
                    raise RevisionConflict(
                        "subscription-job",
                        expected_revision,
                        expected_revision if row is None else int(row[0]),
                    )

        await self.database.executor.run_transaction(_run, begin_mode="IMMEDIATE")
        return association

    async def remove(self, subscription_id: str, *, expected_revision: int) -> None:
        def _run(unit: SQLiteUnitOfWork) -> Any:
            cursor = unit.execute(
                "DELETE FROM b04_subscription_jobs WHERE subscription_id=? AND association_revision=?",
                (subscription_id, expected_revision),
            )
            if cursor.rowcount != 1:
                row = unit.execute(
                    "SELECT association_revision FROM b04_subscription_jobs WHERE subscription_id=?",
                    (subscription_id,),
                ).fetchone()
                raise RevisionConflict(
                    "subscription-job",
                    expected_revision,
                    expected_revision if row is None else int(row[0]),
                )

        return await self.database.executor.run_transaction(
            _run, begin_mode="IMMEDIATE"
        )

    async def for_collection(
        self, key: CollectionKey
    ) -> tuple[SubscriptionJobAssociation, ...]:
        def _run(unit: SQLiteUnitOfWork) -> Any:
            rows = unit.execute(
                "SELECT sj.subscription_id,sj.subscription_revision,sj.cadence_seconds,sj.config_revision,sj.association_revision,j.key_json FROM b04_subscription_jobs sj JOIN b04_subscriptions s USING(subscription_id) JOIN b04_collection_jobs j USING(job_key) WHERE sj.job_key=? ORDER BY sj.subscription_id",
                (_job_key(key),),
            ).fetchall()
            if any(row["key_json"] != _key_json(key) for row in rows):
                raise ValueError("collection key digest collision")
            return tuple(
                SubscriptionJobAssociation(
                    row["subscription_id"],
                    int(row["subscription_revision"]),
                    key,
                    float(row["cadence_seconds"]),
                    int(row["config_revision"]),
                    int(row["association_revision"]),
                )
                for row in rows
            )

        return await self.database.executor.run_read(_run)

    async def current_for_subscription(
        self, subscription_id: str
    ) -> SubscriptionJobAssociation | None:
        def _run(unit: SQLiteUnitOfWork) -> Any:
            row = unit.execute(
                "SELECT sj.subscription_id,sj.subscription_revision,sj.cadence_seconds,sj.config_revision,sj.association_revision,j.key_json FROM b04_subscription_jobs sj JOIN b04_subscriptions s USING(subscription_id) JOIN b04_collection_jobs j USING(job_key) WHERE sj.subscription_id=?",
                (subscription_id,),
            ).fetchone()
            if row is None:
                return None
            return SubscriptionJobAssociation(
                row["subscription_id"],
                int(row["subscription_revision"]),
                _load(row["key_json"]),
                float(row["cadence_seconds"]),
                int(row["config_revision"]),
                int(row["association_revision"]),
            )

        return await self.database.executor.run_read(_run)


class SQLiteSubscriptionLifecycleRepository(_Repository):
    async def read_subscription_gate_state(
        self, module_id: str
    ) -> PersistedSubscriptionGateRead:
        binding = self._subscription_gate_bindings.get(module_id)
        if binding is None:
            return PersistedSubscriptionGateRead(False, None, None, "unsupported")

        def read(unit: SQLiteUnitOfWork) -> PersistedSubscriptionGateRead:
            def invalid(reason: str) -> PersistedSubscriptionGateRead:
                return PersistedSubscriptionGateRead(True, None, None, reason)

            phase = unit.execute(
                "SELECT phase FROM subscription_gate_bootstrap WHERE singleton=1"
            ).fetchone()
            marker = unit.execute(
                "SELECT 1 FROM subscription_gate_initializations WHERE principal_id=? AND module_id=? AND field=?",
                (binding.target.principal_id, binding.target.module_id, binding.field),
            ).fetchone()
            if phase is None or phase[0] != "complete" or marker is None:
                return invalid("initialization_invalid")
            gate = unit.execute(
                "SELECT value_json,revision,subscription_transition_at FROM config_entries WHERE principal_id=? AND module_id=? AND field=?",
                (binding.target.principal_id, binding.target.module_id, binding.field),
            ).fetchone()
            if gate is None:
                return invalid("config_missing")
            try:
                enabled = json.loads(gate["value_json"])
            except (TypeError, ValueError):
                return invalid("config_invalid")
            if type(enabled) is not bool:
                return invalid("config_invalid")
            intent = unit.execute(
                "SELECT desired_enabled,intent_revision,updated_at FROM module_runtime_intents WHERE package_id=? AND module_id=?",
                (binding.package_id, binding.module_id),
            ).fetchone()
            try:
                if (
                    intent is None
                    or type(intent["desired_enabled"]) is not int
                    or intent["desired_enabled"] not in (0, 1)
                ):
                    return invalid("fence_invalid")
                SubscriptionFence(gate["revision"], intent["intent_revision"])
                intent_enabled = intent["desired_enabled"] == 1
                if enabled:
                    _utc_instant(
                        _StoredDateTime.fromisoformat(
                            gate["subscription_transition_at"]
                        ),
                        "gate cutoff",
                    )
                if intent_enabled:
                    _utc_instant(
                        _StoredDateTime.fromisoformat(intent["updated_at"]),
                        "intent cutoff",
                    )
            except (TypeError, ValueError, OverflowError):
                return invalid("fence_invalid")
            return PersistedSubscriptionGateRead(True, enabled, intent_enabled, None)

        return await self.database.executor.run_read(read)

    async def current_fence(self, module_id: str) -> SubscriptionFence | None:
        return await self.database.executor.run_read(
            lambda unit: _read_subscription_fence(
                unit, self._subscription_gate_bindings.get(module_id)
            )
        )

    async def apply(
        self,
        change: SubscriptionJobChange,
        *,
        initial_run: CollectionRunRequest | None = None,
    ) -> SubscriptionRecord:
        if not isinstance(change, SubscriptionJobChange):
            raise TypeError("change must be a SubscriptionJobChange")
        record, assoc = change.record, change.association
        if change.kind in (
            SubscriptionJobChangeKind.CREATE,
            SubscriptionJobChangeKind.REVISE,
        ):
            if initial_run is None:
                raise ValueError("CREATE and REVISE require initial_run")
            if not isinstance(initial_run, CollectionRunRequest):
                raise TypeError("initial_run must be a CollectionRunRequest")
            initial_run.__post_init__()
            if assoc is None:
                raise ValueError("CREATE and REVISE require an association")
            if initial_run.key != assoc.collection_key:
                raise ValueError("initial_run key must match the association")
            if initial_run.cadence_seconds != assoc.cadence_seconds:
                raise ValueError("initial_run cadence must match the association")
            if initial_run.config_revision != assoc.config_revision:
                raise ValueError("initial_run config must match the association")
        elif change.kind is SubscriptionJobChangeKind.CANCEL:
            if initial_run is not None:
                raise ValueError("CANCEL cannot supply initial_run")
        else:
            raise ValueError("unsupported subscription lifecycle change")

        def _run(unit: SQLiteUnitOfWork) -> Any:
            if change.kind is SubscriptionJobChangeKind.CREATE:
                if (
                    _read_subscription_fence(
                        unit, self._subscription_gate_bindings.get(record.module_id)
                    )
                    is None
                ):
                    raise PermissionError("subscription admission is unavailable")
                assert assoc is not None
                job = _put_job(unit, assoc.collection_key, initial_run)
                _record_row(unit, record)
                unit.execute(
                    "INSERT INTO b04_subscription_jobs VALUES (?,?,?,?,?,?)",
                    (
                        record.subscription_id,
                        job,
                        record.revision,
                        assoc.association_revision,
                        assoc.cadence_seconds,
                        assoc.config_revision,
                    ),
                )
            elif change.kind is SubscriptionJobChangeKind.REVISE:
                assert (
                    assoc is not None
                    and change.expected_subscription_revision is not None
                    and change.expected_association_revision is not None
                )
                job = _put_job(unit, assoc.collection_key, initial_run)
                cursor = unit.execute(
                    "UPDATE b04_subscriptions SET revision=?,owner_id=?,scope_kind=?,scope_user=?,grant_id=?,grant_revision=?,status=?,record_json=? WHERE subscription_id=? AND revision=?",
                    (
                        record.revision,
                        record.owner_id,
                        *_scope_values(record),
                        record.status.value,
                        _dump(record),
                        record.subscription_id,
                        change.expected_subscription_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    row = unit.execute(
                        "SELECT revision FROM b04_subscriptions WHERE subscription_id=?",
                        (record.subscription_id,),
                    ).fetchone()
                    raise RevisionConflict(
                        "subscription",
                        change.expected_subscription_revision,
                        change.expected_subscription_revision
                        if row is None
                        else int(row[0]),
                    )
                cursor = unit.execute(
                    "UPDATE b04_subscription_jobs SET job_key=?,subscription_revision=?,association_revision=?,cadence_seconds=?,config_revision=? WHERE subscription_id=? AND association_revision=?",
                    (
                        job,
                        record.revision,
                        assoc.association_revision,
                        assoc.cadence_seconds,
                        assoc.config_revision,
                        record.subscription_id,
                        change.expected_association_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    row = unit.execute(
                        "SELECT association_revision FROM b04_subscription_jobs WHERE subscription_id=?",
                        (record.subscription_id,),
                    ).fetchone()
                    raise RevisionConflict(
                        "subscription-job",
                        change.expected_association_revision,
                        change.expected_association_revision
                        if row is None
                        else int(row[0]),
                    )
            else:
                assert (
                    change.expected_subscription_revision is not None
                    and change.expected_association_revision is not None
                )
                existing_row = unit.execute(
                    "SELECT record_json FROM b04_subscriptions WHERE subscription_id=?",
                    (record.subscription_id,),
                ).fetchone()
                existing = _read_record(existing_row)
                if (
                    existing is None
                    or existing.revision != change.expected_subscription_revision
                ):
                    raise RevisionConflict(
                        "subscription",
                        change.expected_subscription_revision,
                        change.expected_subscription_revision
                        if existing is None
                        else existing.revision,
                    )
                expected_cancelled = SubscriptionRecord(
                    existing.subscription_id,
                    record.revision,
                    existing.module_id,
                    existing.collection_key,
                    existing.owner_id,
                    existing.grant,
                    existing.recipient,
                    existing.notification_mode,
                    existing.filters,
                    SubscriptionStatus.CANCELLED,
                    existing.type_id,
                    existing.digest_schedule,
                )
                if record != expected_cancelled:
                    raise ValueError(
                        "cancel cannot change subscription ownership or settings"
                    )
                cursor = unit.execute(
                    "UPDATE b04_subscriptions SET revision=?,status='cancelled',record_json=? WHERE subscription_id=? AND revision=?",
                    (
                        record.revision,
                        _dump(record),
                        record.subscription_id,
                        change.expected_subscription_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    row = unit.execute(
                        "SELECT revision FROM b04_subscriptions WHERE subscription_id=?",
                        (record.subscription_id,),
                    ).fetchone()
                    raise RevisionConflict(
                        "subscription",
                        change.expected_subscription_revision,
                        change.expected_subscription_revision
                        if row is None
                        else int(row[0]),
                    )
                cursor = unit.execute(
                    "DELETE FROM b04_subscription_jobs WHERE subscription_id=? AND association_revision=?",
                    (record.subscription_id, change.expected_association_revision),
                )
                if cursor.rowcount != 1:
                    row = unit.execute(
                        "SELECT association_revision FROM b04_subscription_jobs WHERE subscription_id=?",
                        (record.subscription_id,),
                    ).fetchone()
                    raise RevisionConflict(
                        "subscription-job",
                        change.expected_association_revision,
                        change.expected_association_revision
                        if row is None
                        else int(row[0]),
                    )

        await self.database.executor.run_transaction(_run, begin_mode="IMMEDIATE")
        return record


class SQLiteSchedulerRepository(_Repository):
    def __init__(
        self,
        database: SQLiteDatabase | str | Path,
        *,
        utc_clock: Callable[[], datetime] | None = None,
        subscription_gate_bindings: Mapping[str, SubscriptionGateBinding] | None = None,
    ) -> None:
        super().__init__(
            database, subscription_gate_bindings=subscription_gate_bindings
        )
        if utc_clock is None:

            def current_utc_time() -> datetime:
                return datetime.now(UTC)

            utc_clock = current_utc_time
        if not callable(utc_clock):
            raise TypeError("utc_clock must be callable")
        self._utc_clock = utc_clock

    async def claim_due(
        self, request: CollectionRunRequest, *, now: datetime
    ) -> ExecutionLease | None:
        now = now.astimezone(UTC)

        def _run(unit: SQLiteUnitOfWork) -> Any:
            fence = _read_subscription_fence(
                unit, self._subscription_gate_bindings.get(request.key.module_id)
            )
            if fence is None:
                return None
            job = _put_job(unit, request.key)
            row = unit.execute(
                "SELECT due_at,lease_token,lease_expires_at FROM b04_collection_jobs WHERE job_key=?",
                (job,),
            ).fetchone()
            stored_due = (
                None
                if row["due_at"] is None
                else _StoredDateTime.fromisoformat(row["due_at"])
            )
            if (
                request.due_at > now
                or stored_due is not None
                and stored_due > now
                or row["lease_token"] is not None
                and _StoredDateTime.fromisoformat(row["lease_expires_at"]) > now
            ):
                return None
            token = secrets.token_urlsafe(24).replace("/", "_")
            expires = now + timedelta(seconds=request.cadence_seconds)
            unit.execute(
                "UPDATE b04_collection_jobs SET due_at=?,cadence_seconds=?,config_revision=?,module_epoch=?,registry_revision=?,lease_token=?,lease_expires_at=?,gate_revision=?,intent_revision=? WHERE job_key=?",
                (
                    _iso(request.due_at),
                    request.cadence_seconds,
                    request.config_revision,
                    request.module_epoch,
                    request.registry_revision,
                    token,
                    _iso(expires),
                    fence.gate_revision,
                    fence.intent_revision,
                    job,
                ),
            )
            return ExecutionLease(
                request.key,
                token,
                request.config_revision,
                request.module_epoch,
                request.registry_revision,
                expires,
                fence,
            )

        return await self.database.executor.run_transaction(
            _run, begin_mode="IMMEDIATE"
        )

    async def is_current(self, lease: ExecutionLease, *, now: datetime) -> bool:
        now = _utc_instant(now, "now")
        job = _job_key(lease.key)
        key_json = _key_json(lease.key)

        def _run(unit: SQLiteUnitOfWork) -> bool:
            row = unit.execute(
                "SELECT key_json,config_revision,module_epoch,registry_revision,"
                "lease_token,lease_expires_at,gate_revision,intent_revision FROM b04_collection_jobs WHERE job_key=?",
                (job,),
            ).fetchone()
            if row is None or row["key_json"] != key_json:
                return False
            current = _read_subscription_fence(
                unit, self._subscription_gate_bindings.get(lease.key.module_id)
            )
            if (
                current is None
                or lease.subscription_fence != current
                or _row_fence(row) != current
            ):
                return False
            expires_at = row["lease_expires_at"]
            if expires_at is None:
                return False
            try:
                persisted_expiry = _StoredDateTime.fromisoformat(expires_at)
            except (TypeError, ValueError):
                return False
            if (
                persisted_expiry.tzinfo is None
                or persisted_expiry.utcoffset() is None
                or persisted_expiry.utcoffset().total_seconds() != 0
            ):
                return False
            return (
                row["lease_token"] == lease.token
                and row["config_revision"] == lease.config_revision
                and row["module_epoch"] == lease.module_epoch
                and row["registry_revision"] == lease.registry_revision
                and persisted_expiry.astimezone(UTC) == lease.expires_at
                and persisted_expiry.astimezone(UTC) > now
            )

        return await self.database.executor.run_read(_run)

    async def list_due_jobs(
        self, *, now: datetime, limit: int, after_cursor: DueJobCursor | None = None
    ) -> tuple[DueCollectionJob, ...]:
        now = _utc_instant(now, "now")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be a positive integer")

        def _run(unit: SQLiteUnitOfWork) -> Any:
            sql = (
                "SELECT j.key_json,j.job_key,j.due_at,j.cadence_seconds,j.config_revision "
                "FROM b04_collection_jobs j WHERE j.due_at IS NOT NULL AND j.due_at<=? "
                "AND (j.lease_token IS NULL OR j.lease_expires_at<=?) "
                "AND j.cadence_seconds IS NOT NULL AND j.config_revision IS NOT NULL "
                "AND EXISTS (SELECT 1 FROM b04_subscription_jobs sj JOIN b04_subscriptions s USING(subscription_id) "
                "WHERE sj.job_key=j.job_key AND s.status='active' AND sj.subscription_revision=s.revision)"
            )
            parameters: list[object] = [_iso(now), _iso(now)]
            if after_cursor is not None:
                sql += " AND (j.due_at>? OR (j.due_at=? AND j.job_key>?))"
                parameters.extend(
                    (
                        _iso(after_cursor.due_at),
                        _iso(after_cursor.due_at),
                        after_cursor.job_key,
                    )
                )
            sql += " ORDER BY j.due_at,j.job_key LIMIT ?"
            parameters.append(limit)
            rows = unit.execute(sql, tuple(parameters)).fetchall()
            return tuple(
                DueCollectionJob(
                    _load(row["key_json"]),
                    _StoredDateTime.fromisoformat(row["due_at"]),
                    float(row["cadence_seconds"]),
                    int(row["config_revision"]),
                    row["job_key"],
                )
                for row in rows
            )

        return await self.database.executor.run_read(_run)

    async def current_evaluation(
        self, subscription_id: str, collection_key: CollectionKey
    ) -> SubscriptionEvaluationSnapshot | None:
        checkpoint = await self.current_checkpoint(subscription_id, collection_key)
        return None if checkpoint is None else checkpoint.snapshot

    async def current_checkpoint(
        self, subscription_id: str, collection_key: CollectionKey
    ) -> EvaluationCheckpoint | None:
        def _run(unit: SQLiteUnitOfWork) -> Any:
            row = unit.execute(
                "SELECT record_json FROM b04_subscriptions WHERE subscription_id=?",
                (subscription_id,),
            ).fetchone()
            record = _read_record(row)
            if record is None or record.collection_key != collection_key:
                return None
            state_row = unit.execute(
                "SELECT state_json,cursor_json,revision,gate_revision,intent_revision FROM b04_evaluation_states WHERE subscription_id=?",
                (subscription_id,),
            ).fetchone()
            if state_row is None:
                return EvaluationCheckpoint(
                    SubscriptionEvaluationSnapshot(record, None, None, None), None, None
                )
            state = _load(state_row[0])
            cursor = _load(state_row[1])
            observation_row = unit.execute(
                "SELECT observation_json FROM b04_observations WHERE observation_id=?",
                (cursor.observation_id,),
            ).fetchone()
            if observation_row is None:
                raise ValueError("stored evaluation observation is unavailable")
            observation = _load(observation_row[0])
            return EvaluationCheckpoint(
                SubscriptionEvaluationSnapshot(record, state, cursor, observation),
                state_row["revision"],
                _row_fence(state_row),
            )

        return await self.database.executor.run_read(_run)

    async def commit_observation(
        self,
        lease: ExecutionLease,
        observation: Observation,
        *,
        next_due_at: datetime | None = None,
    ) -> bool:
        return await self.commit_observation_with_evaluations(
            lease,
            ObservationEvaluationCommit(observation, ()),
            next_due_at=next_due_at,
        )

    async def commit_observation_with_evaluations(
        self,
        lease: ExecutionLease,
        commit: ObservationEvaluationCommit,
        *,
        next_due_at: datetime | None = None,
    ) -> bool:
        observation = commit.observation
        if lease.key != observation.key:
            return False
        if next_due_at is not None:
            next_due_at = _utc_instant(next_due_at, "next_due_at")
            if next_due_at <= observation.collected_at.astimezone(UTC):
                raise ValueError("next_due_at must be later than collection time")
        try:

            def _run(unit: SQLiteUnitOfWork) -> Any:
                job = _job_key(lease.key)
                row = unit.execute(
                    "SELECT * FROM b04_collection_jobs WHERE job_key=?", (job,)
                ).fetchone()
                committed_at = _utc_instant(self._utc_clock(), "utc_clock")
                current = _read_subscription_fence(
                    unit, self._subscription_gate_bindings.get(lease.key.module_id)
                )
                if (
                    row is None
                    or current is None
                    or lease.subscription_fence != current
                    or _row_fence(row) != current
                    or row["key_json"] != _key_json(lease.key)
                    or row["lease_token"] != lease.token
                    or int(row["config_revision"] or 0) != lease.config_revision
                    or int(row["module_epoch"] or 0) != lease.module_epoch
                    or int(row["registry_revision"] or 0) != lease.registry_revision
                    or _StoredDateTime.fromisoformat(row["lease_expires_at"])
                    <= observation.collected_at.astimezone(UTC)
                    or _StoredDateTime.fromisoformat(row["lease_expires_at"])
                    <= committed_at
                ):
                    return False
                for item in commit.subscriptions:
                    sub = unit.execute(
                        "SELECT revision,status FROM b04_subscriptions WHERE subscription_id=?",
                        (item.subscription_id,),
                    ).fetchone()
                    link = unit.execute(
                        "SELECT job_key,subscription_revision FROM b04_subscription_jobs WHERE subscription_id=?",
                        (item.subscription_id,),
                    ).fetchone()
                    state = unit.execute(
                        "SELECT revision FROM b04_evaluation_states WHERE subscription_id=?",
                        (item.subscription_id,),
                    ).fetchone()
                    actual_state = None if state is None else int(state[0])
                    if (
                        sub is None
                        or int(sub["revision"]) != item.subscription_revision
                        or sub["status"] != "active"
                        or link is None
                        or link["job_key"] != job
                        or int(link["subscription_revision"])
                        != item.subscription_revision
                        or actual_state != item.expected_state_revision
                    ):
                        return False
                    for assoc in item.digest_members:
                        if (
                            unit.execute(
                                "SELECT 1 FROM b04_digest_windows WHERE window_id=?",
                                (assoc.window_id,),
                            ).fetchone()
                            is None
                        ):
                            return False
                unit.execute(
                    "INSERT INTO b04_observations VALUES (?,?,?,?,?,?)",
                    (
                        observation.observation_id,
                        job,
                        observation.data_version,
                        observation.completeness.value,
                        _dump(observation.payload),
                        _dump(observation),
                    ),
                )
                if observation.completeness is not ObservationCompleteness.FAILED:
                    for item in commit.subscriptions:
                        state_json, cursor_json = _dump(item.state), _dump(item.cursor)
                        if item.expected_state_revision is None:
                            unit.execute(
                                "INSERT INTO b04_evaluation_states "
                                "(subscription_id,revision,state_json,cursor_json,gate_revision,intent_revision) VALUES (?,?,?,?,?,?)",
                                (
                                    item.subscription_id,
                                    item.state.revision,
                                    state_json,
                                    cursor_json,
                                    current.gate_revision,
                                    current.intent_revision,
                                ),
                            )
                        else:
                            cur = unit.execute(
                                "UPDATE b04_evaluation_states SET revision=?,state_json=?,cursor_json=?,gate_revision=?,intent_revision=? WHERE subscription_id=? AND revision=?",
                                (
                                    item.state.revision,
                                    state_json,
                                    cursor_json,
                                    current.gate_revision,
                                    current.intent_revision,
                                    item.subscription_id,
                                    item.expected_state_revision,
                                ),
                            )
                            if cur.rowcount != 1:
                                raise _StaleEvaluationCommit
                        for event in item.delivery_events:
                            _upsert_event(unit, event, fence=current)
                        for assoc in item.digest_members:
                            _insert_digest_member(unit, assoc)
                    unit.execute(
                        "UPDATE b04_collection_jobs SET observation_id=? WHERE job_key=?",
                        (observation.observation_id, job),
                    )
                unit.execute(
                    "UPDATE b04_collection_jobs SET lease_token=NULL,lease_expires_at=NULL,due_at=? WHERE job_key=? AND lease_token=?",
                    (
                        _iso(
                            next_due_at
                            if next_due_at is not None
                            else observation.collected_at
                            + timedelta(seconds=float(row["cadence_seconds"]))
                        ),
                        job,
                        lease.token,
                    ),
                )
                return True

            return await self.database.executor.run_transaction(
                _run, begin_mode="IMMEDIATE"
            )
        except _StaleEvaluationCommit:
            return False

    async def release(self, lease: ExecutionLease) -> None:
        def _run(unit: SQLiteUnitOfWork) -> Any:
            unit.execute(
                "UPDATE b04_collection_jobs SET lease_token=NULL,lease_expires_at=NULL WHERE job_key=? AND lease_token=?",
                (_job_key(lease.key), lease.token),
            )

        return await self.database.executor.run_transaction(
            _run, begin_mode="IMMEDIATE"
        )


def _insert_digest_member(
    unit: SQLiteUnitOfWork, association: DigestMemberAssociation
) -> None:
    recipient_key = _route_key(association.recipient)
    member = association.member
    unit.execute(
        "INSERT INTO b04_digest_members VALUES (?,?,?,?,?,?,?)",
        (
            association.window_id,
            recipient_key,
            member.subscription_id,
            member.subscription_revision,
            member.event_key,
            member.event_version,
            _dump(association),
        ),
    )


def _envelope_values(envelope: DigestEnvelope) -> tuple[object, ...]:
    return (
        envelope.envelope_id,
        envelope.window_id,
        _route_key(envelope.recipient),
        envelope.state.value,
        envelope.revision,
        envelope.claim_token,
        None if envelope.claimed_at is None else _iso(envelope.claimed_at),
        None if envelope.claim_expires_at is None else _iso(envelope.claim_expires_at),
        None if envelope.retry_at is None else _iso(envelope.retry_at),
        _dump(envelope),
    )


def _read_envelope(row: sqlite3.Row | None) -> DigestEnvelope | None:
    return None if row is None else _load(row["envelope_json"])


def _save_envelope(
    unit: SQLiteUnitOfWork,
    envelope: DigestEnvelope,
    *,
    expected_revision: int | None = None,
) -> bool:
    if expected_revision is None:
        unit.execute(
            "INSERT INTO b04_digest_envelopes VALUES (?,?,?,?,?,?,?,?,?,?)",
            _envelope_values(envelope),
        )
        return True
    values = _envelope_values(envelope)
    cursor = unit.execute(
        "UPDATE b04_digest_envelopes SET window_id=?,recipient_key=?,state=?,revision=?,claim_token=?,claimed_at=?,claim_expires_at=?,retry_at=?,envelope_json=? WHERE envelope_id=? AND revision=?",
        (*values[1:], values[0], expected_revision),
    )
    return cursor.rowcount == 1


class SQLiteDigestWindowRepository(_Repository):
    async def create(self, window: DigestWindow) -> DigestWindow:
        def _run(unit: SQLiteUnitOfWork) -> Any:
            row = unit.execute(
                "SELECT window_json FROM b04_digest_windows WHERE window_id=?",
                (window.window_id,),
            ).fetchone()
            if row is not None:
                existing = _load(row[0])
                if existing.window_id != window.window_id or any(
                    getattr(existing, name) != getattr(window, name)
                    for name in (
                        "timezone_name",
                        "local_schedule_key",
                        "utc_start",
                        "utc_end",
                        "due_at",
                        "fold_policy",
                        "gap_policy",
                        "policy_revision",
                        "schedule_profile",
                        "schedule_recipient",
                    )
                ):
                    raise UniqueConstraintViolation("digest-window")
                return self._window_with_members(unit, existing)
            persisted = DigestWindow(
                window.window_id,
                window.timezone_name,
                window.local_schedule_key,
                window.utc_start,
                window.utc_end,
                window.due_at,
                window.fold_policy,
                window.gap_policy,
                (),
                window.policy_revision,
                window.schedule_profile,
                window.schedule_recipient,
            )
            unit.execute(
                "INSERT INTO b04_digest_windows VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    window.window_id,
                    window.timezone_name,
                    window.local_schedule_key,
                    _iso(window.utc_start),
                    _iso(window.utc_end),
                    _iso(window.due_at),
                    window.fold_policy.value,
                    window.gap_policy.value,
                    window.policy_revision,
                    _dump(persisted),
                ),
            )
            for member in window.members:
                unit.execute(
                    "INSERT INTO b04_digest_window_members(window_id,member_json) VALUES (?,?)",
                    (window.window_id, _dump(member)),
                )
            return window

        return await self.database.executor.run_transaction(
            _run, begin_mode="IMMEDIATE"
        )

    def _window_with_members(
        self, unit: SQLiteUnitOfWork, window: DigestWindow
    ) -> DigestWindow:
        rows = unit.execute(
            "SELECT member_json FROM b04_digest_window_members WHERE window_id=? ORDER BY rowid",
            (window.window_id,),
        ).fetchall()
        relational = tuple(_load(row[0]) for row in rows)
        assoc_rows = unit.execute(
            "SELECT association_json FROM b04_digest_members WHERE window_id=? ORDER BY rowid",
            (window.window_id,),
        ).fetchall()
        associated = tuple(_load(row[0]).member for row in assoc_rows)
        members = tuple(dict.fromkeys((*relational, *associated)))
        return DigestWindow(
            window.window_id,
            window.timezone_name,
            window.local_schedule_key,
            window.utc_start,
            window.utc_end,
            window.due_at,
            window.fold_policy,
            window.gap_policy,
            members,
            window.policy_revision,
            window.schedule_profile,
            window.schedule_recipient,
        )

    async def get(self, window_id: str) -> DigestWindow | None:
        def _run(unit: SQLiteUnitOfWork) -> Any:
            row = unit.execute(
                "SELECT window_json FROM b04_digest_windows WHERE window_id=?",
                (window_id,),
            ).fetchone()
            return (
                None if row is None else self._window_with_members(unit, _load(row[0]))
            )

        return await self.database.executor.run_read(_run)

    async def for_schedule(self, selector: DigestWindowSelector) -> DigestWindow | None:
        def _run(unit: SQLiteUnitOfWork) -> Any:
            rows = unit.execute(
                "SELECT window_json FROM b04_digest_windows WHERE utc_start<=? AND utc_end>? ORDER BY utc_end,window_id",
                (_iso(selector.event_at), _iso(selector.event_at)),
            ).fetchall()
            for row in rows:
                window = _load(row[0])
                if (
                    window.schedule_profile == selector.profile
                    and window.schedule_recipient == selector.recipient
                ):
                    return self._window_with_members(unit, window)
            return None

        return await self.database.executor.run_read(_run)

    async def list_due_routes(
        self,
        *,
        now: datetime,
        limit: int,
        after_cursor: DigestRouteCursor | None = None,
    ) -> tuple[DigestRouteCandidate, ...]:
        now = _utc_instant(now, "now")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be a positive integer")

        def _run(unit: SQLiteUnitOfWork) -> Any:
            sql = (
                "SELECT w.window_id,w.due_at,m.recipient_key,"
                "MIN(m.association_json) AS association_json "
                "FROM b04_digest_windows w JOIN b04_digest_members m USING(window_id) "
                "LEFT JOIN b04_digest_envelopes e ON e.window_id=w.window_id AND e.recipient_key=m.recipient_key "
                "WHERE w.due_at<=? AND (e.envelope_id IS NULL OR e.state='ready' "
                "OR (e.state='failed' AND e.retry_at IS NOT NULL AND e.retry_at<=?))"
            )
            parameters = [_iso(now), _iso(now)]
            if after_cursor is not None:
                sql += (
                    " AND (w.due_at>? OR (w.due_at=? AND "
                    "(w.window_id>? OR (w.window_id=? AND m.recipient_key>?))))"
                )
                parameters.extend(
                    (
                        _iso(after_cursor.due_at),
                        _iso(after_cursor.due_at),
                        after_cursor.window_id,
                        after_cursor.window_id,
                        after_cursor.recipient_key,
                    )
                )
            sql += (
                " GROUP BY w.window_id,m.recipient_key "
                "ORDER BY w.due_at,w.window_id,m.recipient_key LIMIT ?"
            )
            parameters.append(limit)
            rows = unit.execute(sql, tuple(parameters)).fetchall()
            return tuple(
                DigestRouteCandidate(
                    row["window_id"],
                    _load(row["association_json"]).recipient,
                    _StoredDateTime.fromisoformat(row["due_at"]),
                    row["recipient_key"],
                )
                for row in rows
            )

        return await self.database.executor.run_read(_run)

    async def current_envelope(
        self, window_id: str, recipient: ConversationRef
    ) -> DigestEnvelope | None:
        if not isinstance(recipient, ConversationRef):
            raise TypeError("recipient must be a ConversationRef")

        def _run(unit: SQLiteUnitOfWork) -> Any:
            row = unit.execute(
                "SELECT envelope_json FROM b04_digest_envelopes WHERE window_id=? AND recipient_key=?",
                (window_id, _route_key(recipient)),
            ).fetchone()
            return _read_envelope(row)

        return await self.database.executor.run_read(_run)

    async def add_members(
        self, window_id: str, members: tuple[DigestMember, ...]
    ) -> DigestWindow:
        def _run(unit: SQLiteUnitOfWork) -> Any:
            row = unit.execute(
                "SELECT window_json FROM b04_digest_windows WHERE window_id=?",
                (window_id,),
            ).fetchone()
            if row is None:
                raise KeyError("digest window does not exist")
            existing = self._window_with_members(unit, _load(row[0]))
            combined = tuple(dict.fromkeys((*existing.members, *members)))
            for member in members:
                unit.execute(
                    "INSERT OR IGNORE INTO b04_digest_window_members(window_id,member_json) VALUES (?,?)",
                    (window_id, _dump(member)),
                )
            return DigestWindow(
                existing.window_id,
                existing.timezone_name,
                existing.local_schedule_key,
                existing.utc_start,
                existing.utc_end,
                existing.due_at,
                existing.fold_policy,
                existing.gap_policy,
                combined,
                existing.policy_revision,
                existing.schedule_profile,
                existing.schedule_recipient,
            )

        return await self.database.executor.run_transaction(
            _run, begin_mode="IMMEDIATE"
        )

    async def claim_due_envelope(
        self,
        window_id: str,
        recipient: object,
        *,
        now: datetime,
        lease_expires_at: datetime,
    ) -> DigestEnvelopeClaim | None:
        if not isinstance(recipient, ConversationRef):
            raise TypeError("recipient must be a ConversationRef")
        now = _utc_instant(now, "now")
        lease_expires_at = _utc_instant(lease_expires_at, "lease_expires_at")

        def _run(unit: SQLiteUnitOfWork) -> Any:
            window = unit.execute(
                "SELECT due_at FROM b04_digest_windows WHERE window_id=?", (window_id,)
            ).fetchone()
            if (
                window is None
                or _StoredDateTime.fromisoformat(window["due_at"]) > now
                or lease_expires_at <= now
            ):
                return None
            route = _route_key(recipient)
            row = unit.execute(
                "SELECT * FROM b04_digest_envelopes WHERE window_id=? AND recipient_key=?",
                (window_id, route),
            ).fetchone()
            if row is None:
                rows = unit.execute(
                    "SELECT association_json FROM b04_digest_members WHERE window_id=? AND recipient_key=? ORDER BY rowid",
                    (window_id, route),
                ).fetchall()
                associations = tuple(_load(item[0]) for item in rows)
                if not associations:
                    return None
                members = tuple(item.member for item in associations)
                if not any(
                    self._current_member_disposition(unit, member, recipient, window_id)
                    is DigestMemberDisposition.INCLUDED
                    for member in members
                ):
                    return None
                eid = (
                    "digest-"
                    + hashlib.sha256(f"{window_id}:{route}".encode()).hexdigest()[:40]
                )
                envelope = DigestEnvelope(
                    eid, window_id, recipient, members, member_associations=associations
                )
                _save_envelope(unit, envelope)
                row = unit.execute(
                    "SELECT * FROM b04_digest_envelopes WHERE envelope_id=?", (eid,)
                ).fetchone()
            envelope = _read_envelope(row)
            if envelope is None or envelope.state is not DigestEnvelopeState.READY:
                return None
            if not any(
                self._current_member_disposition(
                    unit, member, envelope.recipient, envelope.window_id
                )
                is DigestMemberDisposition.INCLUDED
                for member in envelope.members
            ):
                return None
            token = secrets.token_urlsafe(24).replace("/", "_")
            claimed = DigestEnvelope(
                envelope.envelope_id,
                envelope.window_id,
                envelope.recipient,
                envelope.members,
                envelope.revision + 1,
                DigestEnvelopeState.CLAIMED,
                envelope.member_receipts,
                envelope.member_associations,
                envelope.delivery_attempts,
                token,
                now,
                lease_expires_at,
            )
            if not _save_envelope(unit, claimed, expected_revision=envelope.revision):
                return None
            return DigestEnvelopeClaim(token, claimed, now, lease_expires_at)

        return await self.database.executor.run_transaction(
            _run, begin_mode="IMMEDIATE"
        )

    def _claim_current(
        self, unit: SQLiteUnitOfWork, claim: DigestEnvelopeClaim, expected_revision: int
    ) -> DigestEnvelope | None:
        row = unit.execute(
            "SELECT envelope_json FROM b04_digest_envelopes WHERE envelope_id=?",
            (claim.envelope.envelope_id,),
        ).fetchone()
        current = _read_envelope(row)
        if (
            current is None
            or current.revision != expected_revision
            or current.claim_token != claim.claim_token
        ):
            return None
        return current

    def _current_member_disposition(
        self,
        unit: SQLiteUnitOfWork,
        member: DigestMember,
        recipient: ConversationRef,
        window_id: str,
    ) -> DigestMemberDisposition:
        row = unit.execute(
            "SELECT revision,status,record_json FROM b04_subscriptions WHERE subscription_id=?",
            (member.subscription_id,),
        ).fetchone()
        if row is None or row["status"] != SubscriptionStatus.ACTIVE.value:
            return DigestMemberDisposition.CANCELLED
        if int(row["revision"]) != member.subscription_revision:
            return DigestMemberDisposition.STALE_REVISION
        record = _load(row["record_json"])
        if record.status is not SubscriptionStatus.ACTIVE:
            return DigestMemberDisposition.CANCELLED
        if record.recipient != recipient:
            return DigestMemberDisposition.UNAUTHORIZED
        admission = _read_subscription_admission(
            unit, self._subscription_gate_bindings.get(record.module_id)
        )
        event = unit.execute(
            "SELECT gate_revision,intent_revision,event_json FROM b04_delivery_events WHERE event_key=? AND event_version=? AND subscription_id=? AND subscription_revision=?",
            (
                member.event_key,
                member.event_version,
                member.subscription_id,
                member.subscription_revision,
            ),
        ).fetchone()
        window = unit.execute(
            "SELECT due_at FROM b04_digest_windows WHERE window_id=?", (window_id,)
        ).fetchone()
        if admission is None or _row_fence(event) != admission[0] or window is None:
            return DigestMemberDisposition.UNAUTHORIZED
        saved_event = _read_event(event)
        if saved_event is None or saved_event.recipient != recipient:
            return DigestMemberDisposition.UNAUTHORIZED
        try:
            due = _utc_instant(
                _StoredDateTime.fromisoformat(window["due_at"]), "digest due"
            )
        except (TypeError, ValueError, OverflowError):
            return DigestMemberDisposition.UNAUTHORIZED
        if due <= admission[1]:
            return DigestMemberDisposition.UNAUTHORIZED
        return DigestMemberDisposition.INCLUDED

    async def reconcile_envelope_members(
        self,
        claim: DigestEnvelopeClaim,
        receipts: tuple[DigestMemberReceipt, ...],
        *,
        expected_revision: int,
    ) -> DigestEnvelope:
        def _run(unit: SQLiteUnitOfWork) -> Any:
            current = self._claim_current(unit, claim, expected_revision)
            if current is None or current.state is not DigestEnvelopeState.CLAIMED:
                raise RevisionConflict(
                    "digest-envelope",
                    expected_revision,
                    expected_revision if current is None else current.revision,
                )
            if tuple(item.member for item in receipts) != current.members:
                raise ValueError(
                    "receipts must cover the current ordered envelope members"
                )
            effective_receipts = []
            for receipt in receipts:
                if receipt.disposition is DigestMemberDisposition.INCLUDED:
                    disposition = self._current_member_disposition(
                        unit, receipt.member, current.recipient, current.window_id
                    )
                    if disposition is not DigestMemberDisposition.INCLUDED:
                        receipt = DigestMemberReceipt(receipt.member, disposition)
                effective_receipts.append(receipt)
                member = receipt.member
                unit.execute(
                    "INSERT OR REPLACE INTO b04_digest_member_receipts VALUES (?,?,?,?,?,?,?)",
                    (
                        current.envelope_id,
                        member.subscription_id,
                        member.subscription_revision,
                        member.event_key,
                        member.event_version,
                        receipt.disposition.value,
                        _dump(receipt),
                    ),
                )
            included = tuple(
                item.member
                for item in effective_receipts
                if item.disposition is DigestMemberDisposition.INCLUDED
            )
            effective_receipts = tuple(effective_receipts)
            receipts_by_member = {item.member: item for item in current.member_receipts}
            for receipt in effective_receipts:
                receipts_by_member[receipt.member] = receipt
            current_receipts = tuple(receipts_by_member.values())
            associations = tuple(
                item for item in current.member_associations if item.member in included
            )
            state = (
                DigestEnvelopeState.CANCELLED
                if not included
                else DigestEnvelopeState.CLAIMED
            )
            updated = DigestEnvelope(
                current.envelope_id,
                current.window_id,
                current.recipient,
                included,
                current.revision + 1,
                state,
                current_receipts,
                associations,
                current.delivery_attempts,
                current.claim_token if included else None,
                current.claimed_at if included else None,
                current.claim_expires_at if included else None,
            )
            if not _save_envelope(unit, updated, expected_revision=expected_revision):
                raise RevisionConflict(
                    "digest-envelope", expected_revision, expected_revision + 1
                )
            return updated

        return await self.database.executor.run_transaction(
            _run, begin_mode="IMMEDIATE"
        )

    async def begin_envelope_send(
        self,
        claim: DigestEnvelopeClaim,
        *,
        expected_revision: int,
        attempt_number: int,
        started_at: datetime,
    ) -> DigestEnvelope | None:
        started_at = _utc_instant(started_at, "started_at")

        def _run(unit: SQLiteUnitOfWork) -> Any:
            current = self._claim_current(unit, claim, expected_revision)
            if (
                current is None
                or current.state is not DigestEnvelopeState.CLAIMED
                or current.claim_expires_at is None
                or started_at >= current.claim_expires_at
            ):
                return None
            if any(
                self._current_member_disposition(
                    unit, member, current.recipient, current.window_id
                )
                is not DigestMemberDisposition.INCLUDED
                for member in current.members
            ):
                return None
            key = digest_envelope_idempotency_key(current.window_id, current.recipient)
            attempt = DeliveryAttempt(
                attempt_number, DeliveryState.SENDING, key, started_at
            )
            if attempt_number != len(current.delivery_attempts) + 1:
                return None
            updated = DigestEnvelope(
                current.envelope_id,
                current.window_id,
                current.recipient,
                current.members,
                current.revision + 1,
                DigestEnvelopeState.SENDING,
                current.member_receipts,
                current.member_associations,
                (*current.delivery_attempts, attempt),
                current.claim_token,
                current.claimed_at,
                current.claim_expires_at,
            )
            if not _save_envelope(unit, updated, expected_revision=expected_revision):
                return None
            return updated

        return await self.database.executor.run_transaction(
            _run, begin_mode="IMMEDIATE"
        )

    async def is_current_for_send(
        self, claim: DigestEnvelopeClaim, envelope: DigestEnvelope, *, now: datetime
    ) -> bool:
        now = _utc_instant(now, "now")

        def _run(unit: SQLiteUnitOfWork) -> bool:
            current = self._claim_current(unit, claim, envelope.revision)
            return (
                current == envelope
                and envelope.state is DigestEnvelopeState.SENDING
                and envelope.claimed_at == claim.claimed_at
                and envelope.claim_expires_at == claim.expires_at
                and now < claim.expires_at
                and bool(envelope.members)
                and all(
                    self._current_member_disposition(
                        unit, member, envelope.recipient, envelope.window_id
                    )
                    is DigestMemberDisposition.INCLUDED
                    for member in envelope.members
                )
            )

        return await self.database.executor.run_read(_run)

    async def complete_envelope_send(
        self,
        claim: DigestEnvelopeClaim,
        attempt: DeliveryAttempt,
        *,
        expected_revision: int,
        retry_at: datetime | None = None,
    ) -> DigestEnvelope | None:
        if retry_at is not None:
            retry_at = _utc_instant(retry_at, "retry_at")
            if attempt.state is not DeliveryState.FAILED:
                raise ValueError("retry_at is only valid for a known FAILED attempt")

        def _run(unit: SQLiteUnitOfWork) -> Any:
            current = self._claim_current(unit, claim, expected_revision)
            if (
                current is None
                or current.state is not DigestEnvelopeState.SENDING
                or current.claimed_at != claim.claimed_at
                or current.claim_expires_at != claim.expires_at
                or not current.delivery_attempts
            ):
                return None
            prior = current.delivery_attempts[-1]
            if (
                attempt.state
                not in (DeliveryState.SENT, DeliveryState.FAILED, DeliveryState.UNKNOWN)
                or attempt.attempt_number != prior.attempt_number
                or attempt.idempotency_key != prior.idempotency_key
                or attempt.started_at != prior.started_at
                or attempt.completed_at is None
                or attempt.completed_at < attempt.started_at
            ):
                return None
            state = {
                DeliveryState.SENT: DigestEnvelopeState.SENT,
                DeliveryState.FAILED: DigestEnvelopeState.FAILED,
                DeliveryState.UNKNOWN: DigestEnvelopeState.UNKNOWN,
            }[attempt.state]
            updated = DigestEnvelope(
                current.envelope_id,
                current.window_id,
                current.recipient,
                current.members,
                current.revision + 1,
                state,
                current.member_receipts,
                current.member_associations,
                (*current.delivery_attempts[:-1], attempt),
                retry_at=retry_at,
            )
            if not _save_envelope(unit, updated, expected_revision=expected_revision):
                return None
            return updated

        return await self.database.executor.run_transaction(
            _run, begin_mode="IMMEDIATE"
        )

    async def abort_envelope_send(
        self,
        claim: DigestEnvelopeClaim,
        attempt: DeliveryAttempt,
        *,
        expected_revision: int,
    ) -> DigestEnvelope | None:
        if (
            not isinstance(attempt, DeliveryAttempt)
            or attempt.state is not DeliveryState.FAILED
            or attempt.error_code != "cancelled_before_dispatch"
            or attempt.platform_message_id is not None
        ):
            return None

        def _run(unit: SQLiteUnitOfWork) -> DigestEnvelope | None:
            current = self._claim_current(unit, claim, expected_revision)
            if (
                current is None
                or current.state is not DigestEnvelopeState.SENDING
                or current.claimed_at != claim.claimed_at
                or current.claim_expires_at != claim.expires_at
                or not current.delivery_attempts
            ):
                return None
            prior = current.delivery_attempts[-1]
            if (
                prior.state is not DeliveryState.SENDING
                or attempt.attempt_number != prior.attempt_number
                or attempt.idempotency_key != prior.idempotency_key
                or attempt.started_at != prior.started_at
                or attempt.completed_at is None
                or attempt.completed_at < attempt.started_at
            ):
                return None
            state = (
                DigestEnvelopeState.READY
                if current.members
                else DigestEnvelopeState.CANCELLED
            )
            updated = DigestEnvelope(
                current.envelope_id,
                current.window_id,
                current.recipient,
                current.members,
                current.revision + 1,
                state,
                current.member_receipts,
                current.member_associations,
                (*current.delivery_attempts[:-1], attempt),
            )
            if not _save_envelope(unit, updated, expected_revision=expected_revision):
                return None
            return updated

        return await self.database.executor.run_transaction(
            _run, begin_mode="IMMEDIATE"
        )

    async def recover_expired_envelope_claims(
        self, *, before: datetime, recovered_at: datetime
    ) -> tuple[DigestEnvelope, ...]:
        before = _utc_instant(before, "before")
        recovered_at = _utc_instant(recovered_at, "recovered_at")
        if recovered_at < before:
            raise ValueError("recovered_at must not precede the recovery cutoff")
        changed = []

        def _run(unit: SQLiteUnitOfWork) -> Any:
            rows = unit.execute(
                "SELECT envelope_json FROM b04_digest_envelopes WHERE state IN ('claimed','sending') AND claim_expires_at<=? ORDER BY envelope_id",
                (_iso(before),),
            ).fetchall()
            for row in rows:
                current = _load(row[0])
                if current.state is DigestEnvelopeState.CLAIMED:
                    state = (
                        DigestEnvelopeState.READY
                        if current.members
                        else DigestEnvelopeState.CANCELLED
                    )
                    updated = DigestEnvelope(
                        current.envelope_id,
                        current.window_id,
                        current.recipient,
                        current.members,
                        current.revision + 1,
                        state,
                        current.member_receipts,
                        current.member_associations,
                        current.delivery_attempts,
                    )
                else:
                    prior = current.delivery_attempts[-1]
                    attempt = DeliveryAttempt(
                        prior.attempt_number,
                        DeliveryState.UNKNOWN,
                        prior.idempotency_key,
                        prior.started_at,
                        recovered_at,
                        None,
                        prior.platform_message_id,
                    )
                    updated = DigestEnvelope(
                        current.envelope_id,
                        current.window_id,
                        current.recipient,
                        current.members,
                        current.revision + 1,
                        DigestEnvelopeState.UNKNOWN,
                        current.member_receipts,
                        current.member_associations,
                        (*current.delivery_attempts[:-1], attempt),
                    )
                _save_envelope(unit, updated, expected_revision=current.revision)
                changed.append(updated)

        await self.database.executor.run_transaction(_run, begin_mode="IMMEDIATE")
        return tuple(changed)

    async def retry_failed_envelope(
        self, envelope_id: str, *, expected_revision: int, now: datetime
    ) -> DigestEnvelope | None:
        now = _utc_instant(now, "now")

        def _run(unit: SQLiteUnitOfWork) -> Any:
            row = unit.execute(
                "SELECT envelope_json,retry_at FROM b04_digest_envelopes WHERE envelope_id=?",
                (envelope_id,),
            ).fetchone()
            current = _read_envelope(row)
            if (
                current is None
                or current.revision != expected_revision
                or current.state is not DigestEnvelopeState.FAILED
                or current.retry_at is None
                or current.retry_at > now
                or row["retry_at"] is None
                or _StoredDateTime.fromisoformat(row["retry_at"]) > now
            ):
                return None
            if not any(
                self._current_member_disposition(
                    unit, member, current.recipient, current.window_id
                )
                is DigestMemberDisposition.INCLUDED
                for member in current.members
            ):
                return None
            updated = DigestEnvelope(
                current.envelope_id,
                current.window_id,
                current.recipient,
                current.members,
                current.revision + 1,
                DigestEnvelopeState.READY,
                current.member_receipts,
                current.member_associations,
                current.delivery_attempts,
            )
            _save_envelope(unit, updated, expected_revision=expected_revision)
            return updated

        return await self.database.executor.run_transaction(
            _run, begin_mode="IMMEDIATE"
        )


class SQLiteDeliveryRepository(_Repository):
    def _event_qualified(
        self, unit: SQLiteUnitOfWork, row: sqlite3.Row, event: DeliveryEvent
    ) -> bool:
        subscription_row = unit.execute(
            "SELECT record_json FROM b04_subscriptions WHERE subscription_id=?",
            (event.subscription_id,),
        ).fetchone()
        subscription = _read_record(subscription_row)
        if (
            subscription is None
            or subscription.revision != event.subscription_revision
            or subscription.status is not SubscriptionStatus.ACTIVE
            or subscription.notification_mode != "instant"
            or subscription.recipient != event.recipient
        ):
            return False
        current = _read_subscription_fence(
            unit, self._subscription_gate_bindings.get(subscription.module_id)
        )
        return current is not None and _row_fence(row) == current

    async def is_current_for_send(self, event: DeliveryEvent) -> bool:
        def _run(unit: SQLiteUnitOfWork) -> bool:
            result = self._event_for_update(
                unit,
                event.event_key,
                event.event_version,
                event.subscription_id,
                event.subscription_revision,
            )
            return (
                result is not None
                and result[1] == event
                and event.state is DeliveryState.SENDING
                and self._event_qualified(unit, result[0], event)
                and not self._has_immutable_digest_association(unit, event)
            )

        return await self.database.executor.run_read(_run)

    async def create_event(self, event: DeliveryEvent) -> DeliveryEvent:
        def _run(unit: SQLiteUnitOfWork) -> Any:
            existing = unit.execute(
                "SELECT event_json FROM b04_delivery_events WHERE event_key=? AND event_version=? AND subscription_id=? AND subscription_revision=?",
                (
                    event.event_key,
                    event.event_version,
                    event.subscription_id,
                    event.subscription_revision,
                ),
            ).fetchone()
            if existing is not None:
                value = _read_event(existing)
                if value == event:
                    return value
                raise UniqueConstraintViolation("delivery-event")
            _upsert_event(unit, event)
            return event

        return await self.database.executor.run_transaction(
            _run, begin_mode="IMMEDIATE"
        )

    async def current_event(
        self,
        event_key: str,
        event_version: int,
        *,
        subscription_id: str,
        subscription_revision: int,
    ) -> DeliveryEvent | None:
        def _run(unit: SQLiteUnitOfWork) -> Any:
            row = unit.execute(
                "SELECT event_json FROM b04_delivery_events WHERE event_key=? AND event_version=? AND subscription_id=? AND subscription_revision=?",
                (event_key, event_version, subscription_id, subscription_revision),
            ).fetchone()
            return _read_event(row)

        return await self.database.executor.run_read(_run)

    def _event_for_update(
        self,
        unit: SQLiteUnitOfWork,
        event_key: str,
        event_version: int,
        subscription_id: str,
        subscription_revision: int,
    ) -> tuple[sqlite3.Row, DeliveryEvent] | None:
        row = unit.execute(
            "SELECT rowid AS rid,* FROM b04_delivery_events WHERE event_key=? AND event_version=? AND subscription_id=? AND subscription_revision=?",
            (event_key, event_version, subscription_id, subscription_revision),
        ).fetchone()
        event = _read_event(row)
        return None if event is None else (row, event)

    @staticmethod
    def _has_immutable_digest_association(
        unit: SQLiteUnitOfWork, event: DeliveryEvent
    ) -> bool:
        row = unit.execute(
            "SELECT "
            + _immutable_digest_event_sql("e")
            + " FROM b04_delivery_events AS e WHERE e.event_key=? AND "
            "e.event_version=? AND e.subscription_id=? AND e.subscription_revision=?",
            (
                event.event_key,
                event.event_version,
                event.subscription_id,
                event.subscription_revision,
            ),
        ).fetchone()
        return bool(row[0])

    async def record_attempt(
        self,
        event_key: str,
        event_version: int,
        attempt: DeliveryAttempt,
        *,
        subscription_id: str,
        subscription_revision: int,
        expected_state: DeliveryState,
        retry_at: datetime | None = None,
    ) -> DeliveryEvent:
        if retry_at is not None:
            retry_at = _utc_instant(retry_at, "retry_at")
            if attempt.state is not DeliveryState.FAILED:
                raise ValueError("retry_at is only valid for a known FAILED attempt")

        def _run(unit: SQLiteUnitOfWork) -> Any:
            result = self._event_for_update(
                unit,
                event_key,
                event_version,
                subscription_id,
                subscription_revision,
            )
            if result is None:
                raise KeyError("delivery event does not exist")
            row, event = result
            next_attempt = (
                int(row["attempt_number"])
                if expected_state is DeliveryState.SENDING
                else int(row["attempt_number"]) + 1
            )
            if (
                event.state is not expected_state
                or attempt.idempotency_key != event.idempotency_key
                or attempt.attempt_number != next_attempt
                or expected_state is DeliveryState.SENDING
                and (
                    event.attempt is None
                    or event.attempt.state is not DeliveryState.SENDING
                    or attempt.started_at != event.attempt.started_at
                )
                or attempt.state
                not in (
                    DeliveryState.SENT,
                    DeliveryState.FAILED,
                    DeliveryState.UNKNOWN,
                    DeliveryState.CANCELLED,
                )
            ):
                raise RevisionConflict(
                    "delivery-event", int(row["attempt_number"]), attempt.attempt_number
                )
            if self._has_immutable_digest_association(unit, event) and (
                expected_state in (DeliveryState.PENDING, DeliveryState.FAILED)
                or attempt.state is DeliveryState.CANCELLED
            ):
                raise RevisionConflict(
                    "delivery-event", int(row["attempt_number"]), attempt.attempt_number
                )
            updated = DeliveryEvent(
                event.event_key,
                event.event_version,
                event.subscription_id,
                event.subscription_revision,
                event.owner_id,
                event.grant,
                event.recipient,
                event.display_data,
                event.idempotency_key,
                attempt.state,
                attempt,
                retry_at,
            )
            cursor = unit.execute(
                "UPDATE b04_delivery_events SET state=?,attempt_number=?,retry_at=?,event_json=? WHERE event_key=? AND event_version=? AND subscription_id=? AND subscription_revision=? AND state=? AND attempt_number=?",
                (
                    updated.state.value,
                    attempt.attempt_number,
                    None if retry_at is None else _iso(retry_at),
                    _dump(updated),
                    event.event_key,
                    event.event_version,
                    event.subscription_id,
                    event.subscription_revision,
                    expected_state.value,
                    row["attempt_number"],
                ),
            )
            if cursor.rowcount != 1:
                raise RevisionConflict(
                    "delivery-event", int(row["attempt_number"]), attempt.attempt_number
                )
            if expected_state is DeliveryState.SENDING:
                ledger = unit.execute(
                    "UPDATE b04_delivery_attempts SET state=?,attempt_json=? WHERE event_key=? AND event_version=? AND subscription_id=? AND subscription_revision=? AND attempt_number=? AND state='sending' AND attempt_json=?",
                    (
                        attempt.state.value,
                        _dump(attempt),
                        event.event_key,
                        event.event_version,
                        event.subscription_id,
                        event.subscription_revision,
                        attempt.attempt_number,
                        _dump(event.attempt),
                    ),
                )
                if ledger.rowcount != 1:
                    raise RevisionConflict(
                        "delivery-attempt",
                        attempt.attempt_number,
                        attempt.attempt_number,
                    )
            else:
                unit.execute(
                    "INSERT INTO b04_delivery_attempts(event_key,event_version,subscription_id,subscription_revision,attempt_number,state,attempt_json) VALUES (?,?,?,?,?,?,?)",
                    (
                        event.event_key,
                        event.event_version,
                        event.subscription_id,
                        event.subscription_revision,
                        attempt.attempt_number,
                        attempt.state.value,
                        _dump(attempt),
                    ),
                )
            return updated

        return await self.database.executor.run_transaction(
            _run, begin_mode="IMMEDIATE"
        )

    async def list_due_events(
        self,
        *,
        now: datetime,
        limit: int,
        after_cursor: DeliveryEventCursor | None = None,
    ) -> tuple[DeliveryEvent, ...]:
        now = _utc_instant(now, "now")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be a positive integer")

        def _run(unit: SQLiteUnitOfWork) -> Any:
            sql = (
                "SELECT e.event_json FROM b04_delivery_events AS e WHERE "
                "(e.state='pending' OR (e.state='failed' AND e.retry_at IS NOT NULL AND e.retry_at<=?)) "
                "AND (NOT EXISTS (SELECT 1 FROM b04_subscriptions AS s "
                "WHERE s.subscription_id=e.subscription_id AND s.revision=e.subscription_revision AND s.status='active') "
                "OR EXISTS (SELECT 1 FROM b04_subscriptions AS s "
                "WHERE s.subscription_id=e.subscription_id AND s.revision=e.subscription_revision AND s.status='active' "
                "AND json_extract(s.record_json,'$.fields.notification_mode')='instant')) "
                "AND NOT " + _immutable_digest_event_sql("e")
            )
            parameters: list[object] = [_iso(now)]
            if after_cursor is not None:
                sql += (
                    " AND (e.event_key,e.event_version,e.subscription_id,e.subscription_revision) "
                    "> (?,?,?,?)"
                )
                parameters.extend(
                    (
                        after_cursor.event_key,
                        after_cursor.event_version,
                        after_cursor.subscription_id,
                        after_cursor.subscription_revision,
                    )
                )
            sql += " ORDER BY e.event_key,e.event_version,e.subscription_id,e.subscription_revision LIMIT ?"
            parameters.append(limit)
            rows = unit.execute(sql, tuple(parameters)).fetchall()
            return tuple(_load(row[0]) for row in rows)

        return await self.database.executor.run_read(_run)

    async def claim_sending(
        self,
        event_key: str,
        event_version: int,
        *,
        subscription_id: str,
        subscription_revision: int,
        expected_state: DeliveryState,
        attempt_number: int,
        started_at: datetime,
        now: datetime,
    ) -> DeliveryEvent | None:
        started_at = _utc_instant(started_at, "started_at")
        now = _utc_instant(now, "now")
        if started_at > now:
            raise ValueError("started_at cannot be later than now")

        def _run(unit: SQLiteUnitOfWork) -> Any:
            result = self._event_for_update(
                unit,
                event_key,
                event_version,
                subscription_id,
                subscription_revision,
            )
            if result is None:
                return None
            row, event = result
            if not self._event_qualified(unit, row, event):
                return None
            if self._has_immutable_digest_association(unit, event):
                return None
            subscription_row = unit.execute(
                "SELECT revision,status,record_json FROM b04_subscriptions WHERE subscription_id=?",
                (subscription_id,),
            ).fetchone()
            if (
                subscription_row is None
                or int(subscription_row["revision"]) != subscription_revision
                or subscription_row["status"] != SubscriptionStatus.ACTIVE.value
            ):
                return None
            subscription = _read_record(subscription_row)
            if (
                subscription is None
                or subscription.subscription_id != subscription_id
                or subscription.revision != subscription_revision
                or subscription.status is not SubscriptionStatus.ACTIVE
                or subscription.notification_mode != "instant"
            ):
                return None
            if (
                event.state is not expected_state
                or expected_state not in (DeliveryState.PENDING, DeliveryState.FAILED)
                or attempt_number != int(row["attempt_number"]) + 1
                or expected_state is DeliveryState.FAILED
                and (
                    event.retry_at is None
                    or event.retry_at > now
                    or row["retry_at"] is None
                    or _StoredDateTime.fromisoformat(row["retry_at"]) > now
                )
            ):
                return None
            attempt = DeliveryAttempt(
                attempt_number, DeliveryState.SENDING, event.idempotency_key, started_at
            )
            updated = DeliveryEvent(
                event.event_key,
                event.event_version,
                event.subscription_id,
                event.subscription_revision,
                event.owner_id,
                event.grant,
                event.recipient,
                event.display_data,
                event.idempotency_key,
                DeliveryState.SENDING,
                attempt,
            )
            cur = unit.execute(
                "UPDATE b04_delivery_events SET state='sending',attempt_number=?,retry_at=NULL,event_json=? WHERE event_key=? AND event_version=? AND subscription_id=? AND subscription_revision=? AND state=? AND attempt_number=?",
                (
                    attempt_number,
                    _dump(updated),
                    event.event_key,
                    event.event_version,
                    event.subscription_id,
                    event.subscription_revision,
                    expected_state.value,
                    row["attempt_number"],
                ),
            )
            if cur.rowcount != 1:
                return None
            unit.execute(
                "INSERT INTO b04_delivery_attempts(event_key,event_version,subscription_id,subscription_revision,attempt_number,state,attempt_json) VALUES (?,?,?,?,?,?,?)",
                (
                    event.event_key,
                    event.event_version,
                    event.subscription_id,
                    event.subscription_revision,
                    attempt_number,
                    DeliveryState.SENDING.value,
                    _dump(attempt),
                ),
            )
            return updated

        return await self.database.executor.run_transaction(
            _run, begin_mode="IMMEDIATE"
        )

    async def mark_sending_unknown(
        self,
        event_key: str,
        event_version: int,
        *,
        subscription_id: str,
        subscription_revision: int,
        expected_attempt_number: int,
        expected_started_at: datetime,
        completed_at: datetime,
    ) -> DeliveryEvent | None:
        completed_at = completed_at.astimezone(UTC)

        def _run(unit: SQLiteUnitOfWork) -> Any:
            result = self._event_for_update(
                unit,
                event_key,
                event_version,
                subscription_id,
                subscription_revision,
            )
            if result is None:
                return None
            row, event = result
            old = event.attempt
            if (
                event.state is not DeliveryState.SENDING
                or old is None
                or old.attempt_number != expected_attempt_number
                or old.started_at != expected_started_at
                or completed_at < old.started_at
            ):
                return None
            attempt = DeliveryAttempt(
                old.attempt_number,
                DeliveryState.UNKNOWN,
                old.idempotency_key,
                old.started_at,
                completed_at,
                None,
                old.platform_message_id,
            )
            updated = DeliveryEvent(
                event.event_key,
                event.event_version,
                event.subscription_id,
                event.subscription_revision,
                event.owner_id,
                event.grant,
                event.recipient,
                event.display_data,
                event.idempotency_key,
                DeliveryState.UNKNOWN,
                attempt,
            )
            cur = unit.execute(
                "UPDATE b04_delivery_events SET state='unknown',retry_at=NULL,event_json=? WHERE event_key=? AND event_version=? AND subscription_id=? AND subscription_revision=? AND state='sending' AND attempt_number=?",
                (
                    _dump(updated),
                    event.event_key,
                    event.event_version,
                    event.subscription_id,
                    event.subscription_revision,
                    expected_attempt_number,
                ),
            )
            if cur.rowcount != 1:
                return None
            ledger = unit.execute(
                "UPDATE b04_delivery_attempts SET state='unknown',attempt_json=? WHERE event_key=? AND event_version=? AND subscription_id=? AND subscription_revision=? AND attempt_number=? AND state='sending'",
                (
                    _dump(attempt),
                    event.event_key,
                    event.event_version,
                    event.subscription_id,
                    event.subscription_revision,
                    expected_attempt_number,
                ),
            )
            if ledger.rowcount != 1:
                raise RevisionConflict(
                    "delivery-attempt", expected_attempt_number, expected_attempt_number
                )
            return updated

        return await self.database.executor.run_transaction(
            _run, begin_mode="IMMEDIATE"
        )

    async def create_envelope(self, envelope: DigestEnvelope) -> DigestEnvelope:
        def _run(unit: SQLiteUnitOfWork) -> Any:
            row = unit.execute(
                "SELECT envelope_json FROM b04_digest_envelopes WHERE window_id=? AND recipient_key=?",
                (envelope.window_id, _route_key(envelope.recipient)),
            ).fetchone()
            if row is not None:
                existing = _load(row[0])
                if existing == envelope:
                    return existing
                raise UniqueConstraintViolation("digest-envelope")
            _save_envelope(unit, envelope)
            return envelope

        return await self.database.executor.run_transaction(
            _run, begin_mode="IMMEDIATE"
        )

    async def record_member_receipt(
        self, envelope_id: str, receipt: DigestMemberReceipt
    ) -> None:
        def _run(unit: SQLiteUnitOfWork) -> Any:
            row = unit.execute(
                "SELECT envelope_json FROM b04_digest_envelopes WHERE envelope_id=?",
                (envelope_id,),
            ).fetchone()
            current = _read_envelope(row)
            if current is None or receipt.member not in current.members:
                raise KeyError("digest member does not exist")
            unit.execute(
                "INSERT OR REPLACE INTO b04_digest_member_receipts VALUES (?,?,?,?,?,?,?)",
                (
                    envelope_id,
                    receipt.member.subscription_id,
                    receipt.member.subscription_revision,
                    receipt.member.event_key,
                    receipt.member.event_version,
                    receipt.disposition.value,
                    _dump(receipt),
                ),
            )
            receipts = tuple(
                item
                for item in current.member_receipts
                if item.member != receipt.member
            ) + (receipt,)
            updated = DigestEnvelope(
                current.envelope_id,
                current.window_id,
                current.recipient,
                current.members,
                current.revision + 1,
                current.state,
                receipts,
                current.member_associations,
                current.delivery_attempts,
                current.claim_token,
                current.claimed_at,
                current.claim_expires_at,
            )
            _save_envelope(unit, updated, expected_revision=current.revision)

        return await self.database.executor.run_transaction(
            _run, begin_mode="IMMEDIATE"
        )

    async def recover_stale_sending(
        self, *, before: datetime
    ) -> tuple[DeliveryEvent, ...]:
        before = before.astimezone(UTC)
        candidates = []

        def _run(unit: SQLiteUnitOfWork) -> Any:
            rows = unit.execute(
                "SELECT event_json FROM b04_delivery_events WHERE state='sending' ORDER BY event_key,event_version,subscription_id"
            ).fetchall()
            for row in rows:
                event = _load(row[0])
                if event.attempt is not None and event.attempt.started_at <= before:
                    candidates.append(event)
            recovered = []
            for event in candidates:
                attempt = event.attempt
                unknown = DeliveryAttempt(
                    attempt.attempt_number,
                    DeliveryState.UNKNOWN,
                    attempt.idempotency_key,
                    attempt.started_at,
                    before,
                    None,
                    attempt.platform_message_id,
                )
                updated = DeliveryEvent(
                    event.event_key,
                    event.event_version,
                    event.subscription_id,
                    event.subscription_revision,
                    event.owner_id,
                    event.grant,
                    event.recipient,
                    event.display_data,
                    event.idempotency_key,
                    DeliveryState.UNKNOWN,
                    unknown,
                )
                changed = unit.execute(
                    "UPDATE b04_delivery_events SET state='unknown',retry_at=NULL,event_json=? WHERE event_key=? AND event_version=? AND subscription_id=? AND subscription_revision=? AND state='sending' AND attempt_number=?",
                    (
                        _dump(updated),
                        event.event_key,
                        event.event_version,
                        event.subscription_id,
                        event.subscription_revision,
                        attempt.attempt_number,
                    ),
                )
                if changed.rowcount != 1:
                    continue
                ledger = unit.execute(
                    "UPDATE b04_delivery_attempts SET state='unknown',attempt_json=? WHERE event_key=? AND event_version=? AND subscription_id=? AND subscription_revision=? AND attempt_number=? AND state='sending'",
                    (
                        _dump(unknown),
                        event.event_key,
                        event.event_version,
                        event.subscription_id,
                        event.subscription_revision,
                        attempt.attempt_number,
                    ),
                )
                if ledger.rowcount != 1:
                    raise RevisionConflict(
                        "delivery-attempt",
                        attempt.attempt_number,
                        attempt.attempt_number,
                    )
                recovered.append(updated)
            return tuple(recovered)

        return await self.database.executor.run_transaction(
            _run, begin_mode="IMMEDIATE"
        )


__all__ = [
    "SQLiteDeliveryRepository",
    "SQLiteDigestWindowRepository",
    "SQLiteSchedulerRepository",
    "SQLiteSubscriptionJobRepository",
    "SQLiteSubscriptionLifecycleRepository",
    "SQLiteSubscriptionStore",
]
