"""SDK 1.7 persistence upgrades; real SQLite, renderer and controlled delivery."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from ygl_test_subject.api.display import (
    DisplayAudience,
    DisplayDocument,
    DisplayLimits,
)
from ygl_test_subject.api.services import Grant, GrantStatus
from ygl_test_subject.api.storage import OwnerScope
from ygl_test_subject.api.subscriptions import DeliveryEventCursor, DeliveryState
from ygl_test_subject.api.version import CONTRACT_VERSION
from ygl_test_subject.infrastructure.sqlite.database import SQLiteDatabase
from ygl_test_subject.infrastructure.sqlite.repositories_subscriptions import (
    SQLiteDeliveryRepository,
    SQLiteDigestWindowRepository,
    SQLiteSubscriptionStore,
    _dump,
    _load,
)
from ygl_test_subject.presentation.rendering import (
    GenericDisplayRenderer,
    RenderingBounds,
    RenderingFailure,
)

from tests.services import test_delivery as delivery_fixture

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "display17"
GOLDENS = json.loads((FIXTURES / "events.json").read_text(encoding="utf-8"))
WORK = (
    Path(__file__).resolve().parents[2]
    / ".architecture-refactor"
    / "management-home-settings-ux"
    / "pr2-revisions"
    / "tests"
)


class AssetReader:
    def __init__(self):
        self.calls = []

    async def read(self, asset_id, *, audience, max_bytes):
        self.calls.append((asset_id, audience, max_bytes))
        return b"synthetic"


class ImageBackend:
    async def validate(self, image_bytes, *, max_bytes, max_dimension):
        if len(image_bytes) > max_bytes or max_dimension < 1:
            raise ValueError("synthetic bounds")


def renderer(**kwargs):
    return GenericDisplayRenderer(
        RenderingBounds(2000, 100, 100, 100, 1024, 2048, 2, 64, 100),
        image_backend=ImageBackend(),
        asset_reader=kwargs.get("asset_reader", AssetReader()),
    )


class DisplayDocumentUpgradeTests(unittest.IsolatedAsyncioTestCase):
    _event = delivery_fixture.DeliveryTests._event
    _service = delivery_fixture.DeliveryTests._service
    _disable = delivery_fixture.DeliveryTests._disable

    def setUp(self):
        WORK.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory
        with patch.object(
            delivery_fixture.tempfile,
            "TemporaryDirectory",
            side_effect=lambda: temporary(dir=WORK),
        ):
            delivery_fixture.DeliveryTests.setUp(self)

    async def asyncSetUp(self):
        await delivery_fixture.DeliveryTests.asyncSetUp(self)

    async def asyncTearDown(self):
        await self.db.executor.close()
        self.temp.cleanup()

    async def seed(self, name):
        raw = GOLDENS[name]
        fields = json.loads(raw)["fields"]
        attempt = fields["attempt"]

        def insert(unit):
            unit.execute(
                "INSERT INTO b04_delivery_events(event_key,event_version,subscription_id,subscription_revision,owner_id,grant_id,grant_revision,idempotency_key,state,attempt_number,retry_at,event_json,gate_revision,intent_revision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,1)",
                (
                    fields["event_key"],
                    1,
                    "sub-1",
                    1,
                    "u1",
                    None if fields["grant"] is None else "synthetic-grant",
                    None if fields["grant"] is None else 2,
                    fields["idempotency_key"],
                    fields["state"]["value"],
                    0 if attempt is None else 1,
                    None
                    if fields["retry_at"] is None
                    else fields["retry_at"]["$datetime"],
                    raw,
                ),
            )
            if attempt is not None:
                unit.execute(
                    "INSERT INTO b04_delivery_attempts VALUES (?,?,?,?,?,?,?)",
                    (
                        fields["event_key"],
                        1,
                        "sub-1",
                        1,
                        1,
                        "failed",
                        json.dumps(attempt, separators=(",", ":"), sort_keys=True),
                    ),
                )

        await self.db.executor.run_transaction(insert, begin_mode="IMMEDIATE")
        return fields["event_key"]

    async def reopen(self):
        await self.db.executor.close()
        self.db = SQLiteDatabase(self.path)
        self.deliveries = SQLiteDeliveryRepository(
            self.db, subscription_gate_bindings=self.bindings
        )
        self.windows = SQLiteDigestWindowRepository(
            self.db, subscription_gate_bindings=self.bindings
        )
        self.subscriptions = SQLiteSubscriptionStore(self.db)

    async def current(self, key):
        return await self.deliveries.current_event(
            key, 1, subscription_id="sub-1", subscription_revision=1
        )

    async def stored(self, key):
        return await self.db.executor.run_read(
            lambda unit: unit.execute(
                "SELECT event_json FROM b04_delivery_events WHERE event_key=?", (key,)
            ).fetchone()[0]
        )

    async def test_goldens_have_fixed_old_source_and_preserve_every_display_field(self):
        provenance = json.loads((FIXTURES / "provenance.json").read_text())
        self.assertEqual(
            provenance["baseline"], "da16405f613a01de5b8844d7bdb6b96156c4a098"
        )
        self.assertEqual(
            hashlib.sha256((FIXTURES / "events.json").read_bytes()).hexdigest(),
            provenance["events_sha256"],
        )
        for name, raw in GOLDENS.items():
            with self.subTest(name=name):
                original = json.loads(raw)
                self.assertEqual(
                    original["fields"]["display_data"]["fields"]["schema_version"],
                    "1.7.0",
                )
                event = _load(raw)
                converted = json.loads(_dump(event))
                converted["fields"]["display_data"]["fields"]["schema_version"] = (
                    "1.7.0"
                )
                self.assertEqual(converted, original)
                self.assertEqual(event.display_data.schema_version, CONTRACT_VERSION)
                self.assertEqual(len(event.display_data.ordered_blocks), 10)
                self.assertEqual(
                    event.display_data.ordered_blocks[1].fields["nested"]["decimal"],
                    Decimal("1.234"),
                )

    async def test_reopen_current_and_mixed_due_pagination_do_not_rewrite_legacy_rows(
        self,
    ):
        keys = [await self.seed(name) for name in GOLDENS]
        await self.reopen()
        for name in GOLDENS:
            key = "legacy-" + name
            event = await self.current(key)
            self.assertEqual(event.state.value, name.split("-")[1])
            self.assertEqual(
                event.retry_at,
                None if event.state is DeliveryState.PENDING else self.now,
            )
            self.assertEqual(await self.stored(key), GOLDENS[name])
        seen, cursor = [], None
        while True:
            page = await self.deliveries.list_due_events(
                now=self.now, limit=2, after_cursor=cursor
            )
            if not page:
                break
            seen.extend(event.event_key for event in page)
            last = page[-1]
            cursor = DeliveryEventCursor(
                last.event_key,
                last.event_version,
                last.subscription_id,
                last.subscription_revision,
            )
        self.assertEqual(seen, sorted(keys + ["event-1"]))
        before_retry = await self.deliveries.list_due_events(
            now=self.now - timedelta(seconds=1), limit=10
        )
        self.assertFalse(
            any(event.state is DeliveryState.FAILED for event in before_retry)
        )

    async def test_old_pending_and_failed_dispatch_use_real_renderer_and_deduplicate(
        self,
    ):
        for name in ("public-pending", "public-failed"):
            key = await self.seed(name)
        await self.reopen()
        service = self._service(renderer=renderer())
        for name in ("public-pending", "public-failed"):
            key = "legacy-" + name
            before = await self.current(key)
            result = await service.dispatch_event(key, 1, "sub-1", 1)
            self.assertEqual(result.state, DeliveryState.SENT)
            saved = await self.current(key)
            self.assertEqual(saved.idempotency_key, before.idempotency_key)
            self.assertEqual(
                saved.attempt.attempt_number, 1 if name.endswith("pending") else 2
            )
            self.assertEqual(saved.attempt.platform_message_id, "platform-1")
            await service.dispatch_event(key, 1, "sub-1", 1)
        self.assertEqual(len(self.port.calls), 2)
        self.assertIn("legacy-body", self.port.calls[0][1].text)
        self.assertEqual(self.port.calls[0][1].resource_ids, ("img:synthetic",))
        self.assertEqual(
            await self.db.executor.run_read(
                lambda unit: unit.execute(
                    "SELECT count(*) FROM b04_delivery_events"
                ).fetchone()[0]
            ),
            3,
        )

    async def test_private_dispatch_requires_current_grant_and_admission(self):
        key = await self.seed("private-pending")
        await self.reopen()
        event = await self.current(key)
        record = replace(
            self.record,
            grant=event.grant,
            collection_key=replace(
                self.key, scope=OwnerScope.authorized("u1", event.grant)
            ),
        )

        def authorize(unit):
            unit.execute(
                "UPDATE b04_subscriptions SET scope_kind='authorized',grant_id=?,grant_revision=?,record_json=? WHERE subscription_id=?",
                ("synthetic-grant", 2, _dump(record), "sub-1"),
            )

        await self.db.executor.run_transaction(authorize, begin_mode="IMMEDIATE")
        service = self._service(renderer=renderer())
        result = await service.dispatch_event(key, 1, "sub-1", 1)
        self.assertNotEqual(result.state, DeliveryState.SENT)
        self.assertEqual(self.port.calls, [])
        key = await self.seed("private-failed")
        grant = Grant(
            "synthetic-grant",
            2,
            "u1",
            "sample/game",
            "synthetic-account",
            ("read",),
            None,
            GrantStatus.ACTIVE,
            self.now + timedelta(minutes=10),
        )
        service._grants = delivery_fixture._Grants(grant)
        result = await service.dispatch_event(key, 1, "sub-1", 1)
        self.assertEqual(result.state, DeliveryState.SENT)
        self.assertEqual(len(self.port.calls), 1)
        self.assertGreater(self.routes.private_calls, 0)

    async def test_old_document_does_not_bypass_lifecycle_admission(self):
        key = await self.seed("public-pending")
        await self.reopen()
        await self._disable()
        result = await self._service(renderer=renderer()).dispatch_event(
            key, 1, "sub-1", 1
        )
        self.assertNotEqual(result.state, DeliveryState.SENT)
        self.assertEqual(self.port.calls, [])

    async def test_renderer_keeps_privacy_asset_access_and_budget_bounds(self):
        public = _load(GOLDENS["public-pending"]).display_data
        private = _load(GOLDENS["private-pending"]).display_data
        with self.assertRaises(RenderingFailure):
            await renderer().render(
                private, limits=DisplayLimits(1, 32), audience=DisplayAudience.PUBLIC
            )

        class Reader:
            def __init__(self):
                self.calls = []

            async def read(self, asset_id, *, audience, max_bytes):
                self.calls.append((asset_id, audience, max_bytes))
                raise OSError("synthetic unavailable")

        reader = Reader()
        with self.assertRaises(RenderingFailure):
            await renderer(asset_reader=reader).render(
                public, limits=DisplayLimits(1, 32)
            )
        rendered = await renderer(asset_reader=reader).render(
            replace(public, ordered_blocks=(public.ordered_blocks[5],)),
            limits=DisplayLimits(1, 32),
        )
        self.assertEqual(rendered.resource_ids, ())
        self.assertTrue(
            all(
                call == ("img:synthetic", DisplayAudience.PUBLIC, 32)
                for call in reader.calls
            )
        )
        self.assertLessEqual(len(reader.calls), 2)
        bounded = GenericDisplayRenderer(RenderingBounds(80, 4, 1, 1, 32, 32, 1, 2, 1))
        output = await bounded.render(public, limits=DisplayLimits(1, 32))
        self.assertLessEqual(len(output.text), 80)
        self.assertEqual(output.resource_ids, ())

    async def test_unrecognized_missing_and_corrupt_legacy_contracts_remain_invalid(
        self,
    ):
        original = json.loads(GOLDENS["public-pending"])
        for version in ("1.6.0", "1.9.0", "future", None):
            value = json.loads(GOLDENS["public-pending"])
            fields = value["fields"]["display_data"]["fields"]
            if version is None:
                del fields["schema_version"]
            else:
                fields["schema_version"] = version
            with self.subTest(version=version), self.assertRaises(ValueError):
                _load(json.dumps(value))
        for change in ("extra", "privacy", "asset", "number"):
            value = json.loads(GOLDENS["public-pending"])
            fields = value["fields"]["display_data"]["fields"]
            if change == "extra":
                fields["future_field"] = True
            elif change == "privacy":
                fields["ordered_blocks"][5]["fields"]["visibility"]["value"] = "private"
            elif change == "asset":
                fields["ordered_blocks"][5]["fields"]["asset_id"] = "../invalid"
            else:
                fields["ordered_blocks"][2]["fields"]["metrics"]["money"]["fields"][
                    "value"
                ] = "NaN"
            with self.subTest(change=change), self.assertRaises(ValueError):
                _load(json.dumps(value))
        self.assertEqual(
            original["fields"]["display_data"]["fields"]["schema_version"], "1.7.0"
        )
        with self.assertRaises(ValueError):
            DisplayDocument("t", "s", (), schema_version="1.7.0")
