"""Optional history diagnostics must not change validated business outcomes."""

import asyncio
import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.modules.ff14.features import fflogs, fflogs_evidence
from ygl_test_subject.modules.ff14.features.diagnostic_declarations import DECLARATIONS
from ygl_test_subject.services.diagnostics import (
    BoundDiagnostics,
    DiagnosticPolicy,
    DiagnosticStore,
)

from tests.modules.ff14 import test_fflogs as lookup_fixture
from tests.modules.ff14 import test_fflogs_aggregation as fixture
from yomihime_game_link_sdk.contexts import InvocationOrigin
from yomihime_game_link_sdk.errors import (
    AccessDenied,
    InvalidInvocation,
    OperationTimeout,
    ParameterError,
    ServiceUnavailable,
    SourceHttpError,
)
from yomihime_game_link_sdk.results import ResultStatus


class CommandDiagnosticIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def invoke_command(self, diagnostics, source_control=None):
        universalis = [
            [{"id": 701, "name": "Cerberus"}, {"id": 702, "name": "Synthetic Orphan"}],
            [{"name": "SyntheticDC", "region": "中国", "worlds": [701]}],
        ]
        http = lookup_fixture._FakeHttp(
            [
                *universalis,
                *lookup_fixture._character_responses(
                    realm="cn", character=lookup_fixture._character(partition=5)
                ),
            ]
        )
        fetch_default = http.fetch

        async def fetch(request):
            if (
                source_control is not None
                and request.body
                and b"FF14PublicCharacter" in request.body
            ):
                raise source_control
            return await fetch_default(request)

        http.fetch = fetch
        handler = fflogs.FFLogsCharacterLookup(
            lookup_fixture._services(http, diagnostics=diagnostics)
        )
        self.addCleanup(handler.close)
        return await handler.invoke(
            SimpleNamespace(origin=InvocationOrigin.COMMAND),
            {
                "command": 'Cerberus "Synthetic Hero" zone_id=73 difficulty=101 partition=5'
            },
        )

    async def test_optional_diagnostic_write_failure_preserves_successful_command(self):
        expected = await self.invoke_command(lookup_fixture._Diagnostics())
        self.assertIs(expected.status, ResultStatus.SUCCESS)

        attempted = []

        async def emit(event_id, payload):
            attempted.append(event_id)
            raise RuntimeError("synthetic optional emitter failure")

        result = await self.invoke_command(SimpleNamespace(emit=emit))
        self.assertEqual(result, expected)
        self.assertIn("FF14PublicCharacterZone", attempted)
        self.assertIn("FF14RequestSummary", attempted)

    async def test_optional_store_failure_preserves_command_through_bound_port(self):
        expected = await self.invoke_command(lookup_fixture._Diagnostics())
        self.assertIs(expected.status, ResultStatus.SUCCESS)
        store = DiagnosticStore()
        self.addCleanup(store.close)
        record = store.record
        event_ids = frozenset({"FF14PublicCharacterZone", "FF14RequestSummary"})

        async def check():
            pass

        diagnostics = BoundDiagnostics(
            SimpleNamespace(
                invocation_id="a" * 32, module_id="ff14/ff14", module_epoch=1
            ),
            tuple(
                validate_contract(item)
                for item in DECLARATIONS
                if item.event_id in event_ids
            ),
            DiagnosticPolicy(event_ids),
            store,
            check,
        )

        def record_with_failure(invocation, event_id, schema, payload, policy):
            if event_id == "FF14PublicCharacterZone":
                raise RuntimeError("synthetic optional store failure")
            record(invocation, event_id, schema, payload, policy)

        with patch.object(store, "record", side_effect=record_with_failure):
            self.assertEqual(await self.invoke_command(diagnostics), expected)
        self.assertEqual(
            [event["event_id"] for event in store.snapshot()], ["FF14RequestSummary"]
        )

    async def test_command_diagnostic_control_failures_still_propagate(self):
        for failure in (
            InvalidInvocation(),
            OperationTimeout(),
            ServiceUnavailable(),
            asyncio.CancelledError(),
        ):

            async def emit(event_id, payload):
                if event_id == "FF14PublicCharacterZone":
                    raise failure

            with (
                self.subTest(failure=type(failure).__name__),
                self.assertRaises(type(failure)) as raised,
            ):
                await self.invoke_command(SimpleNamespace(emit=emit))
            self.assertIs(raised.exception, failure)

    async def test_character_source_control_is_not_mapped_to_business_failure(self):
        for control in (
            AccessDenied("source_revoked"),
            OperationTimeout(),
            SourceHttpError("cancelled"),
        ):
            diagnostics = lookup_fixture._Diagnostics()
            with (
                self.subTest(code=control.code),
                self.assertRaises(type(control)) as caught,
            ):
                await self.invoke_command(diagnostics, source_control=control)
            self.assertIs(caught.exception, control)


class DiagnosticIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.handler = fflogs.FFLogsCharacterLookup(None)
        self.addCleanup(self.handler.close)
        self.metadata = replace(fixture.catalog(), zones=(fixture.catalog().zones[0],))
        self.role = fixture.record()
        self.calls = []

    async def collect(self, evidence=None, source_control=None):
        async def fetch(request):
            variables = json.loads(request.body)["variables"]
            self.calls.append(variables)
            if source_control is not None:
                raise source_control
            character = dict(
                id=self.role.character_id,
                name=self.role.name,
                hidden=False,
                server={"name": self.role.server_name},
            )

            for alias, zone_id in variables.items():
                if alias.startswith("z"):
                    suffix = alias[1:]
                    character[alias] = dict(
                        metric="rdps",
                        zone=zone_id,
                        difficulty=variables["d" + suffix],
                        partition=variables["p" + suffix],
                        size=8,
                        rankings=[
                            dict(
                                encounter={
                                    "id": zone_id * 10,
                                    "name": f"Synthetic encounter {zone_id}",
                                },
                                rankPercent=99.9,
                                bestAmount=1234,
                                totalKills=3,
                                bestSpec="Scholar",
                            )
                        ],
                    )
            return SimpleNamespace(
                status_code=200,
                body=json.dumps(
                    {"data": {"characterData": {"character": character}}}
                ).encode(),
            )

        with patch.object(fflogs, "time", return_value=1_800_000_000):
            return await self.handler._catalog_rankings(
                SimpleNamespace(http=SimpleNamespace(fetch=fetch)),
                "fflogs_public_cn",
                self.role,
                self.metadata,
                dict(
                    name=self.role.name,
                    serverSlug="cerberus",
                    serverRegion="CN",
                    metric="rdps",
                ),
                self.handler._lifecycle,
                evidence,
            )

    async def test_history_worker_does_not_degrade_sdk_control_failures(self):
        for control in (
            AccessDenied("source_revoked"),
            OperationTimeout(),
            SourceHttpError("cancelled"),
        ):
            with (
                self.subTest(code=control.code),
                self.assertRaises(type(control)) as caught,
            ):
                await self.collect(source_control=control)
            self.assertIs(caught.exception, control)

    async def test_winner_builder_ordinary_failure_does_not_change_rankings(self):
        expected = await self.collect()
        self.assertEqual(expected.rankings[0].query_state, "record")
        self.assertEqual(expected.rankings[0].origin.partition, 3)
        self.assertEqual(expected.rankings[0].best_amount, 1234)
        for failure in (
            RuntimeError("diagnostic only"),
            ValueError("diagnostic value"),
        ):
            with self.subTest(failure=type(failure).__name__):
                with patch.object(
                    fflogs_evidence, "winner_summary", side_effect=failure
                ):
                    actual = await self.collect(
                        SimpleNamespace(observe=lambda *a, **k: None)
                    )
                self.assertEqual(actual, expected)

    async def test_invalid_diagnostic_return_and_emitter_failure_are_isolated(self):
        expected = await self.collect()
        self.assertEqual(expected.rankings[0].query_state, "record")
        with patch.object(fflogs_evidence, "winner_summary", return_value=None):
            actual = await self.collect(SimpleNamespace(observe=lambda *a, **k: None))
        self.assertEqual(actual, expected)

        def broken(operation, *args, **kwargs):
            if operation == "FF14HistorySummary":
                raise OSError("diagnostic sink only")

        self.assertEqual(await self.collect(SimpleNamespace(observe=broken)), expected)

    async def test_control_failures_from_diagnostics_keep_their_identities(self):
        for failure in (
            asyncio.CancelledError(),
            PermissionError(),
            InvalidInvocation(),
            OperationTimeout(),
            fflogs.FFLogsPayloadError("business validation"),
            ParameterError("invalid business input"),
            ServiceUnavailable(),
            SourceHttpError("source_revoked"),
        ):
            for phase in ("build", "emit"):
                with self.subTest(failure=type(failure).__name__, phase=phase):

                    def observe(operation, *args, **kwargs):
                        if operation == "FF14HistorySummary":
                            raise failure

                    with patch.object(
                        fflogs_evidence,
                        "winner_summary",
                        **(
                            {"side_effect": failure}
                            if phase == "build"
                            else {"wraps": fflogs_evidence.winner_summary}
                        ),
                    ):
                        with self.assertRaises(type(failure)) as raised:
                            await self.collect(SimpleNamespace(observe=observe))
                    self.assertIs(raised.exception, failure)

    async def test_business_validation_outside_diagnostics_is_not_suppressed(self):
        failure = fflogs.FFLogsPayloadError("business validation")
        with patch.object(fflogs, "_assemble_catalog_rankings", side_effect=failure):
            with self.assertRaises(fflogs.FFLogsPayloadError) as raised:
                await self.collect(SimpleNamespace(observe=lambda *a, **k: None))
        self.assertIs(raised.exception, failure)

    async def test_catalog_fallback_describes_actual_partition_selection(self):
        character = await self.collect()
        result = fflogs._character_result(
            character, "https://cn.fflogs.com", "CN", "rdps", None, "cn"
        )
        text = "\n".join(
            b.text for b in result.document.ordered_blocks if hasattr(b, "text")
        )
        self.assertIn("标准与非标准阵容分区", text)
        self.assertIn("排除 Echo / 超越之力", text)
        self.assertIn("按原始百分位选优", text)
        self.assertNotIn("默认分区", text)
