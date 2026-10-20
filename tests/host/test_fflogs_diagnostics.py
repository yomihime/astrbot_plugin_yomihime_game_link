"""Actual Module projection -> SDK port -> generic Host sink, synthetic logger."""

from __future__ import annotations

import asyncio
import json
import unittest
from copy import deepcopy
from types import SimpleNamespace

from ygl_test_subject.adapters.astrbot.diagnostics import forward_diagnostic
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.services.diagnostics import (
    BoundDiagnostics,
    DiagnosticPolicy,
    DiagnosticStore,
)

from modules.ff14.features.diagnostic_declarations import COUNTS, DECLARATIONS
from modules.ff14.features.fflogs_catalog import parse_character_metadata
from modules.ff14.features.fflogs_evidence import (
    Evidence,
    metadata_digest,
    metadata_payload,
    project,
)
from tests.modules.ff14 import (
    test_fflogs as fixture,
)
from tests.modules.ff14 import (
    test_fflogs_aggregation as rankings,
)
from yomihime_game_link_sdk import AccessDenied, InvalidInvocation, ParameterError


class DiagnosticBridgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sent = []
        self.active = True
        self.view = SimpleNamespace(
            invocation_id="a" * 32, module_id="ff14/ff14", module_epoch=2
        )
        self.logger = SimpleNamespace(debug=self.sent.append)
        self.store = DiagnosticStore(
            lambda event: forward_diagnostic(event, logger=self.logger)
        )
        self.declarations = tuple(validate_contract(event) for event in DECLARATIONS)
        self.policy = DiagnosticPolicy(
            frozenset(event.event_id for event in self.declarations), 64
        )
        self.port = BoundDiagnostics(
            self.view, self.declarations, self.policy, self.store, self.current
        )
        self.addCleanup(self.store.close)

    async def current(self):
        if not self.active:
            raise InvalidInvocation()

    def events(self):
        return [json.loads(message.split(" ", 1)[1]) for message in self.sent]

    def history(self):
        return {name: 1 for name in COUNTS}

    async def test_real_emitter_projects_identity_before_generic_forwarding(self):
        raw = deepcopy(fixture._character()["data"])
        raw["characterData"]["character"].update(
            name="PRIVATE_NAME",
            id=918273,
            token="PRIVATE_TOKEN",
            server={"name": "PRIVATE_SERVER"},
        )
        evidence = Evidence("fflogs_public_cn", self.port)
        evidence.observe("FF14PublicCharacter", raw)
        await evidence.flush()
        self.assertEqual(len(self.sent), 1)
        event = self.events()[0]
        self.assertEqual(
            (event["owner"], event["epoch"], event["trace"]), ("ff14/ff14", 2, "a" * 32)
        )
        for private in ("PRIVATE_NAME", "PRIVATE_TOKEN", "PRIVATE_SERVER", "918273"):
            self.assertNotIn(private, self.sent[0])
        self.assertIn("rankPercent", self.sent[0])

    async def test_history_and_duty_fields_keep_closed_values_and_cache_metadata(self):
        evidence = Evidence("fflogs_public_cn", self.port)
        evidence.observe("FF14HistorySummary", self.history(), status=None)
        evidence.observe(
            "FF14DutyOrderState",
            {
                "state": "complete",
                "complete": True,
                "pages": 2,
                "rows": 200,
                "matched": 4,
                "unmatched": 0,
            },
            status=None,
            cached=True,
        )
        await evidence.flush()
        events = self.events()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["payload"]["payload"], self.history())
        self.assertTrue(events[1]["payload"]["cached"])
        self.assertEqual(events[1]["payload"]["payload"]["rows"], 200)

    async def test_history_wire_rejects_unknown_missing_and_private_fields(self):
        valid = {
            "source": "fflogs_public_cn",
            "status": None,
            "cached": False,
            "payload": self.history(),
        }
        for change in (
            {"unknown": "private"},
            {"headers": {}},
            {"client_secret": "synthetic"},
        ):
            body = {**valid, "payload": {**valid["payload"], **change}}
            with self.subTest(change=change), self.assertRaises(ParameterError):
                await self.port.emit("FF14HistorySummary", body)
        for key in COUNTS:
            body = deepcopy(valid)
            body["payload"].pop(key)
            with self.subTest(missing=key), self.assertRaises(ParameterError):
                await self.port.emit("FF14HistorySummary", body)
        self.assertEqual(self.sent, [])

    async def test_history_wire_rejects_noninteger_out_of_range_and_source(self):
        for key in COUNTS:
            for value in (True, -1, 2147483648, "1", 1.5):
                body = {
                    "source": "fflogs_public_cn",
                    "status": None,
                    "cached": False,
                    "payload": {**self.history(), key: value},
                }
                with (
                    self.subTest(key=key, value=value),
                    self.assertRaises(ParameterError),
                ):
                    await self.port.emit("FF14HistorySummary", body)
        with self.assertRaises(ParameterError):
            await self.port.emit(
                "FF14HistorySummary",
                {
                    "source": "private-source",
                    "status": None,
                    "cached": False,
                    "payload": self.history(),
                },
            )
        self.assertEqual(self.sent, [])

    async def test_close_budget_and_release_are_not_authority(self):
        for _ in range(70):
            await self.port.emit(
                "FF14HistorySummary",
                {
                    "source": "fflogs_public_cn",
                    "status": None,
                    "cached": False,
                    "payload": self.history(),
                },
            )
        self.assertEqual(len(self.sent), 64)
        self.assertLessEqual(self.store._bytes, 512 * 1024)
        self.active = False
        with self.assertRaises(InvalidInvocation):
            await self.port.emit(
                "FF14HistorySummary",
                {
                    "source": "fflogs_public_cn",
                    "status": None,
                    "cached": False,
                    "payload": self.history(),
                },
            )

    async def test_closed_and_undeclared_events_still_fail_controls(self):
        self.store.close()
        self.active = False
        with self.assertRaises(InvalidInvocation):
            await self.port.emit(
                "FF14MetadataState",
                {
                    "source": "fflogs_public_cn",
                    "status": None,
                    "cached": False,
                    "payload": {"state": "base_catalog"},
                },
            )
        self.active = True
        with self.assertRaises(AccessDenied):
            await self.port.emit("Unknown", {})

    async def test_cancel_and_revocation_during_flush_propagate(self):
        evidence = Evidence("fflogs_public_cn", self.port)
        evidence.observe("FF14HistorySummary", self.history(), status=None)
        self.active = False
        with self.assertRaises(InvalidInvocation):
            await evidence.flush()

        async def cancelled():
            raise asyncio.CancelledError()

        evidence = Evidence(
            "fflogs_public_cn",
            BoundDiagnostics(
                self.view, self.declarations, self.policy, self.store, cancelled
            ),
        )
        evidence.observe("FF14HistorySummary", self.history(), status=None)
        with self.assertRaises(asyncio.CancelledError):
            await evidence.flush()
        self.assertEqual(self.sent, [])

    def test_catalog_digest_reconstructs_without_private_specs(self):
        metadata = rankings.catalog()
        safe = project("FF14CharacterMetadata", metadata_payload(metadata))
        reconstructed = parse_character_metadata({**safe, "gameData": {"classes": []}})
        self.assertEqual(metadata_digest(metadata), metadata_digest(reconstructed))

    async def test_nonbusiness_generic_envelope_is_forwarded(self):
        forward_diagnostic(
            {
                "owner": "neutral/sample",
                "epoch": 1,
                "trace": "0" * 32,
                "event_id": "ReadSummary",
                "payload": {"count": 1},
            },
            logger=self.logger,
        )
        self.assertEqual(self.events()[0]["owner"], "neutral/sample")
        with self.assertRaises(ValueError):
            forward_diagnostic({"headers": {}}, logger=self.logger)
