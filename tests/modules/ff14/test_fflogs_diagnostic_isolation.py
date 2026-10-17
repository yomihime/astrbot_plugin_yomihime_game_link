"""Optional history diagnostics must not change validated business outcomes."""

import asyncio
import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from modules.ff14.features import fflogs, fflogs_evidence
from tests.modules.ff14 import test_fflogs_aggregation as fixture
from yomihime_game_link_sdk.errors import (
    InvalidInvocation,
    OperationTimeout,
    ParameterError,
    ServiceUnavailable,
    SourceHttpError,
)


class DiagnosticIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.handler = fflogs.FFLogsCharacterLookup(None)
        self.addCleanup(self.handler.close)
        self.metadata = replace(fixture.catalog(), zones=(fixture.catalog().zones[0],))
        self.role = fixture.record()
        self.calls = []

    async def collect(self, evidence=None):
        async def fetch(request):
            variables = json.loads(request.body)["variables"]
            self.calls.append(variables)
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
