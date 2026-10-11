from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ygl_test_subject.core.contracts.services import Grant, GrantStatus
from ygl_test_subject.core.contracts.subscriptions import (
    DeliveryAttempt,
    DeliveryEvent,
    DeliveryState,
    DigestEnvelope,
    DigestEnvelopeState,
    DigestMemberAssociation,
    DigestWindow,
    delivery_idempotency_key,
    digest_envelope_idempotency_key,
)
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.ports import SecretOwner
from ygl_test_subject.infrastructure.sqlite.repositories_subscriptions import (
    _dump,
    _load,
    _route_key,
)

from tests.fixtures.b04_runtime import DeterministicClock, build_runtime
from yomihime_game_link_sdk.display import (
    DigestMember,
    DisplayDocument,
    Privacy,
    TextBlock,
)
from yomihime_game_link_sdk.storage import GrantReference
from yomihime_game_link_sdk.subscriptions import (
    ConversationKind,
    ConversationRef,
    DigestScheduleProfile,
    DstFoldPolicy,
    DstGapPolicy,
    SubscriptionRequest,
)


class SQLiteGrantRevocationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = datetime(2030, 1, 1, 12, tzinfo=UTC)
        self.clock = DeterministicClock(self.now)
        self.runtime = build_runtime(self.root, clock=self.clock)

    async def asyncTearDown(self) -> None:
        await self.runtime.database.executor.close()
        self.temp.cleanup()

    async def _grant(self, grant_id: str, actor: str = "alice") -> Grant:
        principal_id = {"alice": "principal-alice", "bob": "principal-bob"}[actor]
        secret_owner = SecretOwner(
            principal_id, "sample/feed", "credential", f"revoke-{grant_id}"
        )
        receipt = await self.runtime.host_repositories.secret_store.put(
            b"revoke-test-secret", owner=secret_owner
        )
        grant = Grant(
            grant_id,
            1,
            principal_id,
            "sample/feed",
            f"account-{grant_id}",
            ("read",),
            receipt.secret_ref,
            GrantStatus.ACTIVE,
        )
        await self.runtime.host_repositories.authorization.create_grant(
            grant, expected_revision=0
        )
        return grant

    async def _subscription(
        self,
        actor: str,
        route: str,
        *,
        grant: Grant | None = None,
        mode: str = "instant",
    ):
        recipient = validate_contract(
            ConversationRef(
                "test-adapter", ConversationKind.DIRECT, route, f"private-route-{route}"
            )
        )
        await self.runtime.host_repositories.conversations.save(recipient)
        invocation = self.runtime.invocation(actor, route, grant=grant)
        if grant is None:
            type_id = "feed-alert"
            schedule = None
        else:
            type_id = "private-alert"
            schedule = (
                validate_contract(
                    DigestScheduleProfile(
                        "UTC",
                        "12:00",
                        3600,
                        DstFoldPolicy.FIRST_OCCURRENCE,
                        DstGapPolicy.SKIP,
                    )
                )
                if mode == "digest"
                else None
            )
        request = validate_contract(
            SubscriptionRequest(
                type_id,
                {"region": "global"},
                {"minimum": 0},
                mode,
                schedule,
            )
        )
        services = self.runtime.module_factory.for_module("sample/feed")
        return await services.subscriptions.create_request(invocation, request)

    async def _event(
        self,
        record,
        event_key: str,
        *,
        state: DeliveryState = DeliveryState.PENDING,
    ) -> DeliveryEvent:
        key = delivery_idempotency_key(
            event_key,
            1,
            record.subscription_id,
            record.revision,
            record.recipient,
        )
        attempt = None
        if state is DeliveryState.SENDING:
            attempt = DeliveryAttempt(1, state, key, self.now)
        elif state in (DeliveryState.UNKNOWN, DeliveryState.SENT):
            attempt = DeliveryAttempt(
                1,
                state,
                key,
                self.now,
                self.now + timedelta(seconds=1),
                platform_message_id=(
                    "platform-receipt" if state is DeliveryState.SENT else None
                ),
            )
        event = DeliveryEvent(
            event_key,
            1,
            record.subscription_id,
            record.revision,
            record.owner_id,
            record.grant,
            record.recipient,
            validate_contract(
                DisplayDocument(
                    "Private result",
                    "Revocation fixture",
                    (validate_contract(TextBlock(event_key)),),
                    privacy=Privacy.PRIVATE
                    if record.grant is not None
                    else Privacy.PUBLIC,
                )
            ),
            key,
            state,
            attempt,
        )
        return await self.runtime.repositories.deliveries.create_event(event)

    def _connection(self):
        return self.runtime.database.connect()

    def _cache(self, *, key, visibility, user, grant_id=None, grant_revision=None):
        connection = self._connection()
        try:
            connection.execute(
                "INSERT INTO cache_entries(cache_key,visibility,user_id,grant_id,grant_revision,"
                "payload_json,expires_at,source_version,revision,invalidated) "
                "VALUES (?,?,?,?,?,?,?,?,?,0)",
                (
                    key,
                    visibility,
                    user,
                    grant_id,
                    grant_revision,
                    json.dumps({"value": key}),
                    (self.now + timedelta(days=1)).isoformat(),
                    1,
                    1,
                ),
            )
            connection.commit()
        finally:
            connection.close()

    async def _digest_envelope(
        self,
        grant: Grant,
        record,
        *,
        window_id: str,
        event_key: str,
        state: DigestEnvelopeState = DigestEnvelopeState.READY,
        member_event_state: DeliveryState | None = None,
    ) -> DigestEnvelope:
        event_state = member_event_state or {
            DigestEnvelopeState.SENDING: DeliveryState.SENDING,
            DigestEnvelopeState.UNKNOWN: DeliveryState.UNKNOWN,
            DigestEnvelopeState.SENT: DeliveryState.SENT,
        }.get(state, DeliveryState.PENDING)
        event = await self._event(record, event_key, state=event_state)
        member = validate_contract(
            DigestMember(
                record.subscription_id,
                record.revision,
                event.event_key,
                event.event_version,
            )
        )
        association = DigestMemberAssociation(
            window_id, record.recipient, member, event
        )
        window = DigestWindow(
            window_id,
            "UTC",
            "2030-01-01",
            self.now - timedelta(hours=2),
            self.now - timedelta(hours=1),
            self.now - timedelta(minutes=30),
            DstFoldPolicy.FIRST_OCCURRENCE,
            DstGapPolicy.SKIP,
            (),
        )
        await self.runtime.repositories.windows.create(window)
        await self.runtime.repositories.windows.add_members(window_id, (member,))
        recipient_key = _route_key(record.recipient)
        connection = self._connection()
        try:
            connection.execute(
                "INSERT INTO b04_digest_members(window_id,recipient_key,subscription_id,"
                "subscription_revision,event_key,event_version,association_json) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    window_id,
                    recipient_key,
                    member.subscription_id,
                    member.subscription_revision,
                    member.event_key,
                    member.event_version,
                    _dump(association),
                ),
            )
            connection.commit()
        finally:
            connection.close()

        attempts = ()
        claim = {}
        if state in (
            DigestEnvelopeState.SENDING,
            DigestEnvelopeState.UNKNOWN,
            DigestEnvelopeState.SENT,
        ):
            digest_key = digest_envelope_idempotency_key(window_id, record.recipient)
            completed = (
                None
                if state is DigestEnvelopeState.SENDING
                else self.now + timedelta(seconds=1)
            )
            attempt_state = {
                DigestEnvelopeState.SENDING: DeliveryState.SENDING,
                DigestEnvelopeState.UNKNOWN: DeliveryState.UNKNOWN,
                DigestEnvelopeState.SENT: DeliveryState.SENT,
            }[state]
            attempts = (
                DeliveryAttempt(1, attempt_state, digest_key, self.now, completed),
            )
            if state is DigestEnvelopeState.SENDING:
                claim = {
                    "claim_token": f"claim-{window_id}",
                    "claimed_at": self.now,
                    "claim_expires_at": self.now + timedelta(minutes=5),
                }
        envelope = DigestEnvelope(
            f"envelope-{window_id}",
            window_id,
            record.recipient,
            (member,),
            state=state,
            member_associations=(association,),
            delivery_attempts=attempts,
            **claim,
        )
        await self.runtime.repositories.deliveries.create_envelope(envelope)
        return envelope

    async def _revoke_through_module(self, grant: Grant) -> None:
        await self.runtime.host_repositories.conversations.save(
            validate_contract(
                ConversationRef(
                    "test-adapter",
                    ConversationKind.DIRECT,
                    "command-alice",
                    "private-route-command-alice",
                )
            )
        )
        invocation = self.runtime.invocation("alice", "command-alice")
        accounts = self.runtime.module_factory.for_module("sample/feed").accounts
        await accounts.revoke(
            invocation,
            validate_contract(GrantReference(grant.grant_id, grant.revision)),
        )

    async def test_revoke_is_exact_and_preserves_immutable_private_history(
        self,
    ) -> None:
        grant = await self._grant("grant-alice-old")
        other_grant = await self._grant("grant-alice-other")
        private_view = await self._subscription("alice", "private-alice", grant=grant)
        other_view = await self._subscription("alice", "other-alice", grant=other_grant)
        public_view = await self._subscription("bob", "public-bob")
        private = await self.runtime.repositories.subscriptions.current(
            private_view.subscription_id
        )
        other = await self.runtime.repositories.subscriptions.current(
            other_view.subscription_id
        )
        public = await self.runtime.repositories.subscriptions.current(
            public_view.subscription_id
        )
        events = {
            "pending": await self._event(private, "revoked-pending"),
            "sending": await self._event(
                private, "revoked-sending", state=DeliveryState.SENDING
            ),
            "unknown": await self._event(
                private, "revoked-unknown", state=DeliveryState.UNKNOWN
            ),
            "sent": await self._event(
                private, "revoked-sent", state=DeliveryState.SENT
            ),
            "other": await self._event(other, "other-grant-pending"),
            "public": await self._event(public, "public-pending"),
        }
        self._cache(
            key="cache-revoked",
            visibility="authorized",
            user="principal-alice",
            grant_id=grant.grant_id,
            grant_revision=grant.revision,
        )
        self._cache(
            key="cache-other-grant",
            visibility="authorized",
            user="principal-alice",
            grant_id=other_grant.grant_id,
            grant_revision=other_grant.revision,
        )
        self._cache(
            key="cache-other-revision",
            visibility="authorized",
            user="principal-alice",
            grant_id=grant.grant_id,
            grant_revision=grant.revision + 1,
        )
        self._cache(key="cache-user", visibility="user", user="principal-alice")
        self._cache(key="cache-public", visibility="public", user=None)

        connection = self._connection()
        try:
            connection.execute(
                "INSERT INTO assets(asset_id,media_type,scope_kind,user_id,grant_id,"
                "grant_revision,size_bytes,expires_at,temporary,revision) "
                "VALUES ('keep-private-asset','image/png','authorized','principal-alice',?,?,8,NULL,0,1)",
                (grant.grant_id, grant.revision),
            )
            connection.execute(
                "INSERT INTO module_collections(module_id,collection,schema_version,owner_kind,indexes_json) "
                "VALUES ('sample/feed','keep-private-records',1,'authorized','[]')"
            )
            connection.execute(
                "INSERT INTO module_records(module_id,collection,owner_kind,owner_user_id,"
                "owner_grant_id,owner_grant_revision,record_key,schema_version,payload_json,revision) "
                "VALUES ('sample/feed','keep-private-records','authorized','principal-alice',?,?,"
                "'keep-record',1,'{\"private\":true}',1)",
                (grant.grant_id, grant.revision),
            )
            connection.commit()
        finally:
            connection.close()

        await self._revoke_through_module(grant)

        revoked = await self.runtime.host_repositories.authorization.current_grant(
            grant.grant_id
        )
        self.assertEqual((revoked.status, revoked.revision), (GrantStatus.REVOKED, 2))
        cancelled = await self.runtime.repositories.subscriptions.current(
            private.subscription_id
        )
        self.assertEqual(cancelled.revision, private.revision + 1)
        self.assertEqual(cancelled.status.value, "cancelled")
        self.assertIsNone(
            await self.runtime.repositories.jobs.current_for_subscription(
                private.subscription_id
            )
        )
        other_after = await self.runtime.repositories.subscriptions.current(
            other.subscription_id
        )
        public_after = await self.runtime.repositories.subscriptions.current(
            public.subscription_id
        )
        self.assertEqual(other_after, other)
        self.assertEqual(public_after, public)

        current_events = {
            name: await self.runtime.repositories.deliveries.current_event(
                event.event_key,
                event.event_version,
                subscription_id=event.subscription_id,
                subscription_revision=event.subscription_revision,
            )
            for name, event in events.items()
        }
        self.assertEqual(current_events["pending"].state, DeliveryState.CANCELLED)
        self.assertEqual(current_events["pending"].attempt.error_code, "grant_revoked")
        for name in ("sending", "unknown", "sent", "other", "public"):
            self.assertEqual(current_events[name], events[name])
        connection = self._connection()
        try:
            cache_rows = {
                row["cache_key"]: (row["invalidated"], row["revision"])
                for row in connection.execute(
                    "SELECT cache_key,invalidated,revision FROM cache_entries"
                )
            }
            self.assertEqual(cache_rows["cache-revoked"], (1, 2))
            for key in (
                "cache-other-grant",
                "cache-other-revision",
                "cache-user",
                "cache-public",
            ):
                self.assertEqual(cache_rows[key], (0, 1))
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM assets WHERE asset_id='keep-private-asset'"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM module_records WHERE record_key='keep-record'"
                ).fetchone()[0],
                1,
            )
            jobs_by_scope = {}
            for row in connection.execute(
                "SELECT key_json,due_at FROM b04_collection_jobs"
            ):
                key = _load(row["key_json"])
                if key.scope.grant is None:
                    jobs_by_scope["public"] = row["due_at"]
                else:
                    jobs_by_scope[key.scope.grant.grant_id] = row["due_at"]
            self.assertIsNone(jobs_by_scope[grant.grant_id])
            self.assertIsNotNone(jobs_by_scope[other_grant.grant_id])
            self.assertIsNotNone(jobs_by_scope["public"])
            attempts = connection.execute(
                "SELECT state,attempt_number FROM b04_delivery_attempts "
                "WHERE event_key='revoked-pending' ORDER BY attempt_number"
            ).fetchall()
            self.assertEqual(
                [(row["state"], row["attempt_number"]) for row in attempts],
                [("cancelled", 1)],
            )
        finally:
            connection.close()

    async def test_revocation_prunes_ready_digest_and_keeps_sending_history(
        self,
    ) -> None:
        grant = await self._grant("grant-digest-old")
        other_grant = await self._grant("grant-digest-other")
        target_view = await self._subscription(
            "alice", "digest-route", grant=grant, mode="digest"
        )
        other_view = await self._subscription(
            "alice", "digest-route", grant=other_grant, mode="digest"
        )
        target = await self.runtime.repositories.subscriptions.current(
            target_view.subscription_id
        )
        other = await self.runtime.repositories.subscriptions.current(
            other_view.subscription_id
        )
        target_event = await self._event(target, "digest-ready-target")
        other_event = await self._event(other, "digest-ready-other")
        window = DigestWindow(
            "window-mixed-ready",
            "UTC",
            "2030-01-01",
            self.now - timedelta(hours=2),
            self.now - timedelta(hours=1),
            self.now - timedelta(minutes=30),
            DstFoldPolicy.FIRST_OCCURRENCE,
            DstGapPolicy.SKIP,
            (),
        )
        await self.runtime.repositories.windows.create(window)
        associations = tuple(
            DigestMemberAssociation(
                window.window_id,
                target.recipient,
                validate_contract(
                    DigestMember(
                        record.subscription_id,
                        record.revision,
                        event.event_key,
                        event.event_version,
                    )
                ),
                event,
            )
            for record, event in ((target, target_event), (other, other_event))
        )
        await self.runtime.repositories.windows.add_members(
            window.window_id, tuple(item.member for item in associations)
        )
        connection = self._connection()
        try:
            for association in associations:
                member = association.member
                connection.execute(
                    "INSERT INTO b04_digest_members(window_id,recipient_key,subscription_id,"
                    "subscription_revision,event_key,event_version,association_json) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        window.window_id,
                        _route_key(association.recipient),
                        member.subscription_id,
                        member.subscription_revision,
                        member.event_key,
                        member.event_version,
                        _dump(association),
                    ),
                )
            connection.commit()
        finally:
            connection.close()
        mixed = DigestEnvelope(
            "envelope-mixed-ready",
            window.window_id,
            target.recipient,
            tuple(item.member for item in associations),
            member_associations=associations,
        )
        await self.runtime.repositories.deliveries.create_envelope(mixed)

        immutable = []
        for state, suffix in (
            (DigestEnvelopeState.SENDING, "sending"),
            (DigestEnvelopeState.UNKNOWN, "unknown"),
            (DigestEnvelopeState.SENT, "sent"),
        ):
            private_view = await self._subscription(
                "alice", "digest-route", grant=grant, mode="digest"
            )
            private_record = await self.runtime.repositories.subscriptions.current(
                private_view.subscription_id
            )
            immutable.append(
                await self._digest_envelope(
                    grant,
                    private_record,
                    window_id=f"window-{suffix}",
                    event_key=f"digest-{suffix}-target",
                    state=state,
                    member_event_state=DeliveryState.PENDING,
                )
            )

        await self._revoke_through_module(grant)

        pruned = await self.runtime.repositories.windows.current_envelope(
            mixed.window_id, mixed.recipient
        )
        self.assertEqual(pruned.revision, mixed.revision + 1)
        self.assertEqual(pruned.state, DigestEnvelopeState.READY)
        self.assertEqual(pruned.members, (associations[1].member,))
        self.assertEqual(pruned.member_associations, (associations[1],))
        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM b04_digest_members WHERE window_id=?",
                    (mixed.window_id,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM b04_digest_window_members WHERE window_id=?",
                    (mixed.window_id,),
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()
        for envelope in immutable:
            persisted = await self.runtime.repositories.windows.current_envelope(
                envelope.window_id, envelope.recipient
            )
            self.assertEqual(persisted, envelope)
            association = envelope.member_associations[0]
            persisted_event = await self.runtime.repositories.deliveries.current_event(
                association.event.event_key,
                association.event.event_version,
                subscription_id=association.event.subscription_id,
                subscription_revision=association.event.subscription_revision,
            )
            self.assertEqual(persisted_event, association.event)

    async def test_revocation_prunes_future_digest_member_without_envelope(
        self,
    ) -> None:
        grant = await self._grant("grant-future-digest-old")
        other_grant = await self._grant("grant-future-digest-other")
        target_view = await self._subscription(
            "alice", "future-digest-route", grant=grant, mode="digest"
        )
        other_view = await self._subscription(
            "alice", "future-digest-route", grant=other_grant, mode="digest"
        )
        target = await self.runtime.repositories.subscriptions.current(
            target_view.subscription_id
        )
        other = await self.runtime.repositories.subscriptions.current(
            other_view.subscription_id
        )
        target_event = await self._event(target, "future-digest-target")
        other_event = await self._event(other, "future-digest-other")
        window_start = self.now + timedelta(days=1)
        window_end = window_start + timedelta(hours=1)
        window = DigestWindow(
            "window-future-without-envelope",
            "UTC",
            "2030-01-02",
            window_start,
            window_end,
            window_end + timedelta(minutes=1),
            DstFoldPolicy.FIRST_OCCURRENCE,
            DstGapPolicy.SKIP,
            (),
        )
        await self.runtime.repositories.windows.create(window)
        associations = tuple(
            DigestMemberAssociation(
                window.window_id,
                target.recipient,
                validate_contract(
                    DigestMember(
                        record.subscription_id,
                        record.revision,
                        event.event_key,
                        event.event_version,
                    )
                ),
                event,
            )
            for record, event in ((target, target_event), (other, other_event))
        )
        await self.runtime.repositories.windows.add_members(
            window.window_id, tuple(item.member for item in associations)
        )
        connection = self._connection()
        try:
            for association in associations:
                member = association.member
                connection.execute(
                    "INSERT INTO b04_digest_members(window_id,recipient_key,subscription_id,"
                    "subscription_revision,event_key,event_version,association_json) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        window.window_id,
                        _route_key(association.recipient),
                        member.subscription_id,
                        member.subscription_revision,
                        member.event_key,
                        member.event_version,
                        _dump(association),
                    ),
                )
            connection.commit()
        finally:
            connection.close()

        before_revoke = await self.runtime.repositories.windows.get(window.window_id)
        self.assertIsNotNone(before_revoke)
        self.assertEqual(
            before_revoke.members, tuple(item.member for item in associations)
        )
        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM b04_digest_envelopes WHERE window_id=?",
                    (window.window_id,),
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

        await self._revoke_through_module(grant)

        revoked = await self.runtime.host_repositories.authorization.current_grant(
            grant.grant_id
        )
        self.assertEqual((revoked.status, revoked.revision), (GrantStatus.REVOKED, 2))
        target_after = await self.runtime.repositories.deliveries.current_event(
            target_event.event_key,
            target_event.event_version,
            subscription_id=target_event.subscription_id,
            subscription_revision=target_event.subscription_revision,
        )
        self.assertEqual(target_after.state, DeliveryState.CANCELLED)
        self.assertEqual(target_after.attempt.error_code, "grant_revoked")
        other_after = await self.runtime.repositories.deliveries.current_event(
            other_event.event_key,
            other_event.event_version,
            subscription_id=other_event.subscription_id,
            subscription_revision=other_event.subscription_revision,
        )
        self.assertEqual(other_after, other_event)
        after_revoke = await self.runtime.repositories.windows.get(window.window_id)
        self.assertIsNotNone(after_revoke)
        self.assertEqual(after_revoke.members, (associations[1].member,))

        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM b04_digest_members WHERE window_id=?",
                    (window.window_id,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT subscription_id FROM b04_digest_members WHERE window_id=?",
                    (window.window_id,),
                ).fetchone()[0],
                other.subscription_id,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM b04_digest_envelopes WHERE window_id=?",
                    (window.window_id,),
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

    async def test_mid_transaction_failure_rolls_back_every_revocation_write(
        self,
    ) -> None:
        grant = await self._grant("grant-rollback")
        view = await self._subscription("alice", "rollback-route", grant=grant)
        record = await self.runtime.repositories.subscriptions.current(
            view.subscription_id
        )
        event = await self._event(record, "rollback-pending")
        self._cache(
            key="cache-rollback",
            visibility="authorized",
            user="principal-alice",
            grant_id=grant.grant_id,
            grant_revision=grant.revision,
        )
        connection = self._connection()
        try:
            connection.execute(
                "CREATE TRIGGER fail_revocation_event BEFORE UPDATE OF state ON b04_delivery_events "
                "BEGIN SELECT RAISE(ABORT,'injected revocation failure'); END"
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(sqlite3.IntegrityError):
            await self.runtime.host_repositories.grant_revocation.revoke_grant_with_invalidation(
                grant, expected_revision=grant.revision
            )

        connection = self._connection()
        try:
            connection.execute("DROP TRIGGER fail_revocation_event")
            connection.commit()
            saved_grant = connection.execute(
                "SELECT revision,status FROM grants WHERE grant_id=?", (grant.grant_id,)
            ).fetchone()
            cache = connection.execute(
                "SELECT invalidated,revision FROM cache_entries WHERE cache_key='cache-rollback'"
            ).fetchone()
            subscription = connection.execute(
                "SELECT revision,status FROM b04_subscriptions WHERE subscription_id=?",
                (record.subscription_id,),
            ).fetchone()
            link = connection.execute(
                "SELECT subscription_revision FROM b04_subscription_jobs WHERE subscription_id=?",
                (record.subscription_id,),
            ).fetchone()
            event_row = connection.execute(
                "SELECT state,attempt_number FROM b04_delivery_events WHERE event_key=?",
                (event.event_key,),
            ).fetchone()
            self.assertEqual(
                (saved_grant["revision"], saved_grant["status"]), (1, "active")
            )
            self.assertEqual((cache["invalidated"], cache["revision"]), (0, 1))
            self.assertEqual(
                (subscription["revision"], subscription["status"]), (1, "active")
            )
            self.assertEqual(link["subscription_revision"], 1)
            self.assertEqual(
                (event_row["state"], event_row["attempt_number"]), ("pending", 0)
            )
        finally:
            connection.close()

    async def test_stale_revision_and_wrong_owner_fail_without_writes(self) -> None:
        grant = await self._grant("grant-exact-tuple")
        self._cache(
            key="cache-exact-tuple",
            visibility="authorized",
            user="principal-alice",
            grant_id=grant.grant_id,
            grant_revision=grant.revision,
        )
        coordinator = self.runtime.host_repositories.grant_revocation
        from ygl_test_subject.core.ports import RevisionConflict

        with self.assertRaises(RevisionConflict):
            await coordinator.revoke_grant_with_invalidation(
                grant, expected_revision=grant.revision + 1
            )
        forged = Grant(
            grant.grant_id,
            grant.revision,
            "principal-bob",
            grant.module_id,
            grant.account_id,
            grant.scopes,
            None,
            grant.status,
            grant.expires_at,
        )
        with self.assertRaises(ValueError):
            await coordinator.revoke_grant_with_invalidation(
                forged, expected_revision=grant.revision
            )
        persisted = await self.runtime.host_repositories.authorization.current_grant(
            grant.grant_id
        )
        self.assertEqual(persisted, grant)
        connection = self._connection()
        try:
            row = connection.execute(
                "SELECT invalidated,revision FROM cache_entries WHERE cache_key='cache-exact-tuple'"
            ).fetchone()
            self.assertEqual((row["invalidated"], row["revision"]), (0, 1))
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
