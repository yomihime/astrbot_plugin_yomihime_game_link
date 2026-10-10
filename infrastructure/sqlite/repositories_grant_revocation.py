"""Atomic SQLite grant revocation and private-work invalidation."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from yomihime_game_link_sdk.storage import GrantReference, OwnershipKind
from yomihime_game_link_sdk.subscriptions import CollectionKey

from ...core.contracts.services import Grant
from ...core.contracts.subscriptions import (
    DeliveryAttempt,
    DeliveryEvent,
    DeliveryState,
    DigestEnvelope,
    DigestEnvelopeState,
    DigestMemberAssociation,
    SubscriptionRecord,
    SubscriptionStatus,
)
from ...core.contracts.validation_boundary import validate_contract
from ...core.ports import RevisionConflict
from .database import SQLiteDatabase, SQLiteUnitOfWork
from .repositories_auth import SQLiteGrantStore
from .repositories_subscriptions import (
    _dump,
    _envelope_values,
    _iso,
    _load,
    _read_envelope,
    _route_key,
    _save_envelope,
    _scope_values,
)

_PAGE_SIZE = 128


class SQLiteGrantRevocationRepository:
    """Commit Grant CAS and all private-work invalidation in one SQLite UoW."""

    __slots__ = ("database",)

    def __init__(self, database: SQLiteDatabase | str | Path) -> None:
        if isinstance(database, SQLiteDatabase):
            self.database = database
        elif isinstance(database, (str, Path)):
            self.database = SQLiteDatabase(database)
        else:
            raise TypeError("database must be a SQLiteDatabase or path")

    async def revoke_grant_with_invalidation(
        self, grant: Grant, *, expected_revision: int
    ) -> Grant:
        if not isinstance(grant, Grant):
            raise TypeError("grant must be a Grant")
        expected_revision = SQLiteGrantStore._validate_expected(expected_revision)
        if grant.revision != expected_revision:
            raise RevisionConflict("grant", expected_revision, grant.revision)
        cancelled_at = datetime.now(UTC)

        def _worker(unit: SQLiteUnitOfWork) -> Grant:
            revoked = SQLiteGrantStore._revoke_grant_in_unit(
                unit,
                grant,
                expected_revision=expected_revision,
                require_exact_grant=True,
            )
            scope = _OldGrantScope(
                revoked.grant_id,
                revoked.principal_id,
                revoked.module_id,
                expected_revision,
            )
            unit.execute(
                "UPDATE cache_entries SET invalidated=1, revision=revision+1 "
                "WHERE visibility='authorized' AND user_id=? AND grant_id=? "
                "AND grant_revision=?",
                (scope.principal_id, scope.grant_id, scope.revision),
            )
            self._cancel_subscriptions(unit, scope)
            self._disarm_collection_jobs(unit, scope)
            self._cancel_pending_events(unit, scope, cancelled_at)
            self._prune_digest_members(unit, scope)
            return revoked

        return await self.database.executor.run_transaction(
            _worker, begin_mode="IMMEDIATE"
        )

    @staticmethod
    def _cancel_subscriptions(unit: SQLiteUnitOfWork, scope: _OldGrantScope) -> None:
        after: str | None = None
        while True:
            if after is None:
                page = unit.execute(
                    "SELECT * FROM b04_subscriptions WHERE scope_kind='authorized' "
                    "AND owner_id=? AND scope_user=? AND grant_id=? AND grant_revision=? "
                    "ORDER BY subscription_id LIMIT ?",
                    (
                        scope.principal_id,
                        scope.principal_id,
                        scope.grant_id,
                        scope.revision,
                        _PAGE_SIZE,
                    ),
                ).fetchall()
            else:
                page = unit.execute(
                    "SELECT * FROM b04_subscriptions WHERE scope_kind='authorized' "
                    "AND owner_id=? AND scope_user=? AND grant_id=? AND grant_revision=? "
                    "AND subscription_id>? ORDER BY subscription_id LIMIT ?",
                    (
                        scope.principal_id,
                        scope.principal_id,
                        scope.grant_id,
                        scope.revision,
                        after,
                        _PAGE_SIZE,
                    ),
                ).fetchall()
            if not page:
                return
            for row in page:
                record = _load(row["record_json"])
                if not isinstance(record, SubscriptionRecord):
                    raise ValueError("stored subscription JSON has an invalid type")
                _validate_subscription_row(row, record, scope)
                link = unit.execute(
                    "SELECT sj.subscription_revision,sj.association_revision,sj.job_key,j.key_json "
                    "FROM b04_subscription_jobs sj JOIN b04_collection_jobs j USING(job_key) "
                    "WHERE sj.subscription_id=?",
                    (record.subscription_id,),
                ).fetchone()
                if link is not None:
                    key = _load(link["key_json"])
                    if (
                        not isinstance(key, CollectionKey)
                        or key != record.collection_key
                        or key.module_id != scope.module_id
                        or key.scope.kind is not OwnershipKind.AUTHORIZED
                        or key.scope.user_id != scope.principal_id
                        or key.scope.grant
                        != validate_contract(
                            GrantReference(scope.grant_id, scope.revision)
                        )
                    ):
                        raise ValueError(
                            "subscription job link does not match its stored record"
                        )
                if record.status is SubscriptionStatus.ACTIVE and (
                    link is None
                    or int(link["subscription_revision"]) != record.revision
                ):
                    raise ValueError("active subscription is missing its job link")
                if record.status is SubscriptionStatus.CANCELLED and link is not None:
                    if int(link["subscription_revision"]) >= record.revision:
                        raise ValueError(
                            "cancelled subscription has an invalid job-link revision"
                        )
                if record.status is SubscriptionStatus.ACTIVE:
                    cancelled = replace(
                        record,
                        revision=record.revision + 1,
                        status=SubscriptionStatus.CANCELLED,
                    )
                    kind, scope_user, grant_id, grant_revision = _scope_values(
                        cancelled
                    )
                    cursor = unit.execute(
                        "UPDATE b04_subscriptions SET revision=?,owner_id=?,scope_kind=?,"
                        "scope_user=?,grant_id=?,grant_revision=?,status=?,record_json=? "
                        "WHERE subscription_id=? AND revision=? AND owner_id=? "
                        "AND scope_kind='authorized' AND scope_user=? AND grant_id=? "
                        "AND grant_revision=? AND status=? AND record_json=?",
                        (
                            cancelled.revision,
                            cancelled.owner_id,
                            kind,
                            scope_user,
                            grant_id,
                            grant_revision,
                            cancelled.status.value,
                            _dump(cancelled),
                            record.subscription_id,
                            record.revision,
                            record.owner_id,
                            scope.principal_id,
                            scope.grant_id,
                            scope.revision,
                            record.status.value,
                            row["record_json"],
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise RevisionConflict(
                            "subscription", record.revision, record.revision + 1
                        )
                if link is not None:
                    cursor = unit.execute(
                        "DELETE FROM b04_subscription_jobs WHERE subscription_id=? "
                        "AND subscription_revision=? AND association_revision=? AND job_key=?",
                        (
                            record.subscription_id,
                            link["subscription_revision"],
                            link["association_revision"],
                            link["job_key"],
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise RevisionConflict(
                            "subscription-job",
                            int(link["association_revision"]),
                            int(link["association_revision"]),
                        )
                after = record.subscription_id

    @staticmethod
    def _disarm_collection_jobs(unit: SQLiteUnitOfWork, scope: _OldGrantScope) -> None:
        after: str | None = None
        while True:
            if after is None:
                page = unit.execute(
                    "SELECT * FROM b04_collection_jobs WHERE scope_kind='authorized' "
                    "AND owner_id=? AND grant_id=? AND grant_revision=? "
                    "ORDER BY job_key LIMIT ?",
                    (scope.principal_id, scope.grant_id, scope.revision, _PAGE_SIZE),
                ).fetchall()
            else:
                page = unit.execute(
                    "SELECT * FROM b04_collection_jobs WHERE scope_kind='authorized' "
                    "AND owner_id=? AND grant_id=? AND grant_revision=? AND job_key>? "
                    "ORDER BY job_key LIMIT ?",
                    (
                        scope.principal_id,
                        scope.grant_id,
                        scope.revision,
                        after,
                        _PAGE_SIZE,
                    ),
                ).fetchall()
            if not page:
                return
            for row in page:
                key = _load(row["key_json"])
                if not isinstance(key, CollectionKey):
                    raise ValueError("stored collection key JSON has an invalid type")
                if (
                    key.module_id != scope.module_id
                    or key.scope.kind is not OwnershipKind.AUTHORIZED
                    or key.scope.user_id != scope.principal_id
                    or key.scope.grant
                    != validate_contract(GrantReference(scope.grant_id, scope.revision))
                ):
                    raise ValueError("collection job scope does not match its columns")
                associations = unit.execute(
                    "SELECT s.* FROM b04_subscription_jobs j "
                    "JOIN b04_subscriptions s USING(subscription_id) WHERE j.job_key=?",
                    (row["job_key"],),
                ).fetchall()
                has_active_association = False
                for association in associations:
                    record = _load(association["record_json"])
                    if not isinstance(record, SubscriptionRecord):
                        raise ValueError("stored subscription JSON has an invalid type")
                    if (
                        record.revision != int(association["revision"])
                        or record.status.value != association["status"]
                        or record.owner_id != association["owner_id"]
                    ):
                        raise ValueError(
                            "subscription row disagrees with its stored JSON"
                        )
                    if record.status is SubscriptionStatus.ACTIVE:
                        if (
                            record.module_id != scope.module_id
                            or record.owner_id != scope.principal_id
                            or record.grant
                            != validate_contract(
                                GrantReference(scope.grant_id, scope.revision)
                            )
                        ):
                            raise ValueError(
                                "active subscription has a mismatched collection grant"
                            )
                        has_active_association = True
                if not has_active_association:
                    unit.execute(
                        "UPDATE b04_collection_jobs SET due_at=NULL,cadence_seconds=NULL,"
                        "config_revision=NULL,module_epoch=NULL,registry_revision=NULL,"
                        "lease_token=NULL,lease_expires_at=NULL WHERE job_key=? "
                        "AND scope_kind='authorized' AND owner_id=? AND grant_id=? "
                        "AND grant_revision=?",
                        (
                            row["job_key"],
                            scope.principal_id,
                            scope.grant_id,
                            scope.revision,
                        ),
                    )
                after = row["job_key"]

    @staticmethod
    def _cancel_pending_events(
        unit: SQLiteUnitOfWork,
        scope: _OldGrantScope,
        cancelled_at: datetime,
    ) -> None:
        after: tuple[str, int, str, int] | None = None
        while True:
            predicate = (
                "owner_id=? AND grant_id=? AND grant_revision=? AND "
                "(state='pending' OR (state='failed' AND retry_at IS NOT NULL)) "
                "AND NOT EXISTS (SELECT 1 FROM b04_digest_members dm "
                "JOIN b04_digest_envelopes de ON de.window_id=dm.window_id "
                "AND de.recipient_key=dm.recipient_key WHERE dm.event_key="
                "b04_delivery_events.event_key AND dm.event_version=b04_delivery_events.event_version "
                "AND dm.subscription_id=b04_delivery_events.subscription_id "
                "AND dm.subscription_revision=b04_delivery_events.subscription_revision "
                "AND de.state IN ('sending','unknown','sent'))"
            )
            params: list[object] = [
                scope.principal_id,
                scope.grant_id,
                scope.revision,
            ]
            if after is not None:
                predicate += (
                    " AND (event_key,event_version,subscription_id,subscription_revision) "
                    "> (?,?,?,?)"
                )
                params.extend(after)
            params.append(_PAGE_SIZE)
            page = unit.execute(
                "SELECT * FROM b04_delivery_events WHERE "
                + predicate
                + " ORDER BY event_key,event_version,subscription_id,subscription_revision LIMIT ?",
                tuple(params),
            ).fetchall()
            if not page:
                return
            for row in page:
                event = _load(row["event_json"])
                if not isinstance(event, DeliveryEvent):
                    raise ValueError("stored delivery event JSON has an invalid type")
                _validate_event_row(row, event, scope)
                if event.state not in (DeliveryState.PENDING, DeliveryState.FAILED):
                    raise ValueError("delivery event state changed inside revocation")
                if event.state is DeliveryState.FAILED and event.retry_at is None:
                    raise ValueError(
                        "non-retryable failed event entered revocation scan"
                    )
                attempt_number = int(row["attempt_number"]) + 1
                attempt = DeliveryAttempt(
                    attempt_number,
                    DeliveryState.CANCELLED,
                    event.idempotency_key,
                    cancelled_at,
                    cancelled_at,
                    "grant_revoked",
                )
                updated = replace(
                    event,
                    state=DeliveryState.CANCELLED,
                    attempt=attempt,
                    retry_at=None,
                )
                cursor = unit.execute(
                    "UPDATE b04_delivery_events SET state=?,attempt_number=?,retry_at=NULL,"
                    "event_json=? WHERE event_key=? AND event_version=? AND subscription_id=? "
                    "AND subscription_revision=? AND owner_id=? AND grant_id=? "
                    "AND grant_revision=? AND state=? AND attempt_number=? AND event_json=?",
                    (
                        updated.state.value,
                        attempt_number,
                        _dump(updated),
                        event.event_key,
                        event.event_version,
                        event.subscription_id,
                        event.subscription_revision,
                        scope.principal_id,
                        scope.grant_id,
                        scope.revision,
                        event.state.value,
                        row["attempt_number"],
                        row["event_json"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise RevisionConflict(
                        "delivery-event", int(row["attempt_number"]), attempt_number
                    )
                unit.execute(
                    "INSERT INTO b04_delivery_attempts(event_key,event_version,subscription_id,"
                    "subscription_revision,attempt_number,state,attempt_json) VALUES (?,?,?,?,?,?,?)",
                    (
                        event.event_key,
                        event.event_version,
                        event.subscription_id,
                        event.subscription_revision,
                        attempt_number,
                        DeliveryState.CANCELLED.value,
                        _dump(attempt),
                    ),
                )
                after = (
                    event.event_key,
                    event.event_version,
                    event.subscription_id,
                    event.subscription_revision,
                )

    @staticmethod
    def _prune_digest_members(unit: SQLiteUnitOfWork, scope: _OldGrantScope) -> None:
        mutable_states = {
            DigestEnvelopeState.READY,
            DigestEnvelopeState.CLAIMED,
            DigestEnvelopeState.FAILED,
        }
        after: str | None = None
        while True:
            if after is None:
                candidates = unit.execute(
                    "SELECT d.envelope_id FROM b04_digest_envelopes d "
                    "JOIN b04_digest_members m ON m.window_id=d.window_id "
                    "AND m.recipient_key=d.recipient_key JOIN b04_delivery_events e "
                    "ON e.event_key=m.event_key AND e.event_version=m.event_version "
                    "AND e.subscription_id=m.subscription_id "
                    "AND e.subscription_revision=m.subscription_revision "
                    "WHERE e.owner_id=? AND e.grant_id=? AND e.grant_revision=? "
                    "GROUP BY d.envelope_id ORDER BY d.envelope_id LIMIT ?",
                    (scope.principal_id, scope.grant_id, scope.revision, _PAGE_SIZE),
                ).fetchall()
            else:
                candidates = unit.execute(
                    "SELECT d.envelope_id FROM b04_digest_envelopes d "
                    "JOIN b04_digest_members m ON m.window_id=d.window_id "
                    "AND m.recipient_key=d.recipient_key JOIN b04_delivery_events e "
                    "ON e.event_key=m.event_key AND e.event_version=m.event_version "
                    "AND e.subscription_id=m.subscription_id "
                    "AND e.subscription_revision=m.subscription_revision "
                    "WHERE e.owner_id=? AND e.grant_id=? AND e.grant_revision=? "
                    "AND d.envelope_id>? GROUP BY d.envelope_id "
                    "ORDER BY d.envelope_id LIMIT ?",
                    (
                        scope.principal_id,
                        scope.grant_id,
                        scope.revision,
                        after,
                        _PAGE_SIZE,
                    ),
                ).fetchall()
            if not candidates:
                break
            for candidate in candidates:
                row = unit.execute(
                    "SELECT * FROM b04_digest_envelopes WHERE envelope_id=?",
                    (candidate["envelope_id"],),
                ).fetchone()
                if row is None:
                    raise RevisionConflict("digest-envelope", 1, 0)
                envelope = _read_envelope(row)
                if not isinstance(envelope, DigestEnvelope):
                    raise ValueError("stored digest envelope JSON has an invalid type")
                _validate_envelope_row(row, envelope)
                if envelope.state not in mutable_states:
                    after = envelope.envelope_id
                    continue
                member_rows = unit.execute(
                    "SELECT m.*,e.event_json,e.owner_id AS event_owner_id,e.grant_id AS event_grant_id,"
                    "e.grant_revision AS event_grant_revision,e.state AS event_state,"
                    "e.attempt_number AS event_attempt_number,e.retry_at AS event_retry_at,"
                    "e.idempotency_key AS event_idempotency_key FROM b04_digest_members m "
                    "JOIN b04_delivery_events e ON e.event_key=m.event_key "
                    "AND e.event_version=m.event_version AND e.subscription_id=m.subscription_id "
                    "AND e.subscription_revision=m.subscription_revision "
                    "WHERE m.window_id=? AND m.recipient_key=? AND e.owner_id=? "
                    "AND e.grant_id=? AND e.grant_revision=? ORDER BY m.subscription_id,"
                    "m.subscription_revision,m.event_key,m.event_version",
                    (
                        envelope.window_id,
                        row["recipient_key"],
                        scope.principal_id,
                        scope.grant_id,
                        scope.revision,
                    ),
                ).fetchall()
                members: set[object] = set()
                associations_by_member: dict[object, DigestMemberAssociation] = {}
                for member_row in member_rows:
                    association = _load(member_row["association_json"])
                    if not isinstance(association, DigestMemberAssociation):
                        raise ValueError(
                            "stored digest association JSON has an invalid type"
                        )
                    _validate_digest_association(member_row, association, scope)
                    members.add(association.member)
                    associations_by_member[association.member] = association
                envelope_associations = {
                    association.member: association
                    for association in envelope.member_associations
                }
                envelope_grant_members = {
                    association.member
                    for association in envelope.member_associations
                    if association.event.owner_id == scope.principal_id
                    and association.event.grant
                    == validate_contract(GrantReference(scope.grant_id, scope.revision))
                }
                if not envelope_grant_members.issubset(members):
                    raise ValueError(
                        "revoked digest envelope member is missing its relational association"
                    )
                for member in members.intersection(set(envelope.members)):
                    if associations_by_member[member] != envelope_associations.get(
                        member
                    ):
                        raise ValueError(
                            "digest envelope disagrees with its relational association"
                        )
                if (
                    any(
                        association.event.grant
                        == validate_contract(
                            GrantReference(scope.grant_id, scope.revision)
                        )
                        for association in envelope.member_associations
                    )
                    and not envelope_grant_members
                ):
                    raise ValueError("digest envelope grant membership is malformed")
                envelope_pruned_members = members.intersection(set(envelope.members))
                retained_members = tuple(
                    member
                    for member in envelope.members
                    if member not in envelope_pruned_members
                )
                retained_associations = tuple(
                    association
                    for association in envelope.member_associations
                    if association.member not in envelope_pruned_members
                )
                if not members:
                    after = envelope.envelope_id
                    continue
                if envelope_pruned_members:
                    next_state = envelope.state
                    if not retained_members:
                        next_state = DigestEnvelopeState.CANCELLED
                    elif envelope.state is DigestEnvelopeState.CLAIMED:
                        next_state = DigestEnvelopeState.READY
                    updated = replace(
                        envelope,
                        members=retained_members,
                        member_associations=retained_associations,
                        revision=envelope.revision + 1,
                        state=next_state,
                        claim_token=None,
                        claimed_at=None,
                        claim_expires_at=None,
                        retry_at=(
                            envelope.retry_at
                            if next_state is DigestEnvelopeState.FAILED
                            else None
                        ),
                    )
                    if not _save_envelope(
                        unit, updated, expected_revision=envelope.revision
                    ):
                        raise RevisionConflict(
                            "digest-envelope", envelope.revision, envelope.revision + 1
                        )
                for member_row in member_rows:
                    member = _load(member_row["association_json"]).member
                    cursor = unit.execute(
                        "DELETE FROM b04_digest_members WHERE window_id=? AND recipient_key=? "
                        "AND subscription_id=? AND subscription_revision=? AND event_key=? "
                        "AND event_version=? AND association_json=?",
                        (
                            member_row["window_id"],
                            member_row["recipient_key"],
                            member_row["subscription_id"],
                            member_row["subscription_revision"],
                            member_row["event_key"],
                            member_row["event_version"],
                            member_row["association_json"],
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise RevisionConflict(
                            "digest-member", member.subscription_revision, 0
                        )
                    unit.execute(
                        "DELETE FROM b04_digest_window_members WHERE window_id=? AND member_json=?",
                        (envelope.window_id, _dump(member)),
                    )
                after = envelope.envelope_id
        SQLiteGrantRevocationRepository._prune_orphan_digest_members(unit, scope)

    @staticmethod
    def _prune_orphan_digest_members(
        unit: SQLiteUnitOfWork, scope: _OldGrantScope
    ) -> None:
        after: tuple[str, str, str, int, str, int] | None = None
        while True:
            params: list[object] = [
                scope.principal_id,
                scope.grant_id,
                scope.revision,
            ]
            predicate = "e.owner_id=? AND e.grant_id=? AND e.grant_revision=? "
            if after is not None:
                predicate += (
                    "AND (m.window_id,m.recipient_key,m.subscription_id,m.subscription_revision,"
                    "m.event_key,m.event_version)>(?,?,?,?,?,?) "
                )
                params.extend(after)
            page = unit.execute(
                "SELECT m.*,e.event_json,e.owner_id AS event_owner_id,e.grant_id AS event_grant_id,"
                "e.grant_revision AS event_grant_revision,e.state AS event_state,"
                "e.attempt_number AS event_attempt_number,e.retry_at AS event_retry_at,"
                "e.idempotency_key AS event_idempotency_key FROM b04_digest_members m "
                "JOIN b04_delivery_events e "
                "ON e.event_key=m.event_key AND e.event_version=m.event_version "
                "AND e.subscription_id=m.subscription_id AND e.subscription_revision=m.subscription_revision "
                "LEFT JOIN b04_digest_envelopes d ON d.window_id=m.window_id AND d.recipient_key=m.recipient_key "
                "WHERE "
                + predicate
                + "AND d.envelope_id IS NULL ORDER BY m.window_id,m.recipient_key,m.subscription_id,"
                "m.subscription_revision,m.event_key,m.event_version LIMIT ?",
                (*params, _PAGE_SIZE),
            ).fetchall()
            if not page:
                return
            for row in page:
                association = _load(row["association_json"])
                if not isinstance(association, DigestMemberAssociation):
                    raise ValueError(
                        "stored digest association JSON has an invalid type"
                    )
                _validate_digest_association(row, association, scope)
                cursor = unit.execute(
                    "DELETE FROM b04_digest_members WHERE window_id=? AND recipient_key=? "
                    "AND subscription_id=? AND subscription_revision=? AND event_key=? "
                    "AND event_version=? AND association_json=?",
                    (
                        row["window_id"],
                        row["recipient_key"],
                        row["subscription_id"],
                        row["subscription_revision"],
                        row["event_key"],
                        row["event_version"],
                        row["association_json"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise RevisionConflict("digest-member", 1, 0)
                unit.execute(
                    "DELETE FROM b04_digest_window_members WHERE window_id=? AND member_json=?",
                    (row["window_id"], _dump(association.member)),
                )
                after = (
                    row["window_id"],
                    row["recipient_key"],
                    row["subscription_id"],
                    int(row["subscription_revision"]),
                    row["event_key"],
                    int(row["event_version"]),
                )


class _OldGrantScope:
    __slots__ = ("grant_id", "principal_id", "module_id", "revision")

    def __init__(
        self, grant_id: str, principal_id: str, module_id: str, revision: int
    ) -> None:
        self.grant_id = grant_id
        self.principal_id = principal_id
        self.module_id = module_id
        self.revision = revision


def _validate_subscription_row(
    row: sqlite3.Row, record: SubscriptionRecord, scope: _OldGrantScope
) -> None:
    if (
        record.revision != int(row["revision"])
        or record.owner_id != row["owner_id"]
        or record.status.value != row["status"]
        or _scope_values(record)
        != (
            row["scope_kind"],
            row["scope_user"],
            row["grant_id"],
            row["grant_revision"],
        )
        or record.module_id != scope.module_id
        or record.owner_id != scope.principal_id
        or record.grant
        != validate_contract(GrantReference(scope.grant_id, scope.revision))
        or record.collection_key.scope.kind is not OwnershipKind.AUTHORIZED
    ):
        raise ValueError("subscription row disagrees with its stored JSON")


def _validate_event_row(
    row: sqlite3.Row, event: DeliveryEvent, scope: _OldGrantScope
) -> None:
    attempt_number = 0 if event.attempt is None else event.attempt.attempt_number
    retry_at = None if event.retry_at is None else _iso(event.retry_at)
    if (
        event.event_key != row["event_key"]
        or event.event_version != int(row["event_version"])
        or event.subscription_id != row["subscription_id"]
        or event.subscription_revision != int(row["subscription_revision"])
        or event.owner_id != row["owner_id"]
        or event.grant
        != validate_contract(GrantReference(scope.grant_id, scope.revision))
        or event.state.value != row["state"]
        or attempt_number != int(row["attempt_number"])
        or retry_at != row["retry_at"]
        or row["grant_id"] != scope.grant_id
        or int(row["grant_revision"]) != scope.revision
        or row["owner_id"] != scope.principal_id
    ):
        raise ValueError("delivery event row disagrees with its stored JSON")


def _validate_envelope_row(row: sqlite3.Row, envelope: DigestEnvelope) -> None:
    values = _envelope_values(envelope)
    columns = (
        "envelope_id",
        "window_id",
        "recipient_key",
        "state",
        "revision",
        "claim_token",
        "claimed_at",
        "claim_expires_at",
        "retry_at",
        "envelope_json",
    )
    if tuple(row[column] for column in columns) != values:
        raise ValueError("digest envelope row disagrees with its stored JSON")


def _validate_digest_association(
    row: sqlite3.Row,
    association: DigestMemberAssociation,
    scope: _OldGrantScope,
) -> None:
    member = association.member
    persisted_event = _load(row["event_json"])
    if not isinstance(persisted_event, DeliveryEvent):
        raise ValueError("stored delivery event JSON has an invalid type")
    persisted_attempt_number = (
        0 if persisted_event.attempt is None else persisted_event.attempt.attempt_number
    )
    persisted_retry_at = (
        None if persisted_event.retry_at is None else _iso(persisted_event.retry_at)
    )
    persisted_grant_id = (
        None if persisted_event.grant is None else persisted_event.grant.grant_id
    )
    persisted_grant_revision = (
        None if persisted_event.grant is None else persisted_event.grant.revision
    )
    if (
        association.window_id != row["window_id"]
        or _route_key(association.recipient) != row["recipient_key"]
        or member.subscription_id != row["subscription_id"]
        or member.subscription_revision != int(row["subscription_revision"])
        or member.event_key != row["event_key"]
        or member.event_version != int(row["event_version"])
        or association.event.owner_id != scope.principal_id
        or association.event.grant
        != validate_contract(GrantReference(scope.grant_id, scope.revision))
        or association.event.event_key != row["event_key"]
        or association.event.event_version != int(row["event_version"])
        or association.event.subscription_id != row["subscription_id"]
        or association.event.subscription_revision != int(row["subscription_revision"])
        or persisted_event.event_key != row["event_key"]
        or persisted_event.event_version != int(row["event_version"])
        or persisted_event.subscription_id != row["subscription_id"]
        or persisted_event.subscription_revision != int(row["subscription_revision"])
        or persisted_event.owner_id != row["event_owner_id"]
        or persisted_grant_id != row["event_grant_id"]
        or persisted_grant_revision != row["event_grant_revision"]
        or persisted_event.state.value != row["event_state"]
        or persisted_attempt_number != int(row["event_attempt_number"])
        or persisted_retry_at != row["event_retry_at"]
        or persisted_event.idempotency_key != row["event_idempotency_key"]
        or association.event.owner_id != persisted_event.owner_id
        or association.event.grant != persisted_event.grant
        or association.event.recipient != persisted_event.recipient
        or association.event.display_data != persisted_event.display_data
        or association.event.idempotency_key != persisted_event.idempotency_key
    ):
        raise ValueError("digest association row disagrees with its stored JSON")
