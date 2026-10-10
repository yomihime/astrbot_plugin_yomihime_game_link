"""Guard source-valid item output at the existing public display boundary."""

from __future__ import annotations

import unittest
from dataclasses import replace

from ygl_test_subject.adapters.astrbot.web_public import (
    WebPublicRejected,
    project_result,
)
from ygl_test_subject.core.contracts.validation_boundary import validate_contract

from tests.fixtures.ff14.item_display import lookup_result, source_snapshot
from yomihime_game_link_sdk.display import DisplayDocument, TextBlock
from yomihime_game_link_sdk.results import CapabilityResult, ResultStatus


class ItemDisplayBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_valid_boundary_overflow_routes_and_many_warnings(self):
        for counts, many_warnings in (
            ((5, 5, 5, 4, 1, 1), False),  # Previously exactly 32 blocks.
            ((5, 5, 5, 5, 1, 1), False),  # Previously 33 blocks.
            ((5, 5, 5, 5, 5, 5), False),  # 24 routes plus missing-link warnings.
            ((6, 6, 6, 6, 6, 6), True),  # Per-group caps and optional errors.
        ):
            with self.subTest(counts=counts, many_warnings=many_warnings):
                snapshot = await source_snapshot(counts, extra_warnings=many_warnings)
                result = await lookup_result(counts, extra_warnings=many_warnings)
                self.assertIs(result.status, ResultStatus.PARTIAL_SUCCESS)
                self.assertLessEqual(len(result.document.ordered_blocks), 32)
                data = project_result(result)
                self.assertEqual(data["warnings"], list(snapshot.record.warnings))
                self.assertEqual(
                    data["document"]["sources"], list(result.document.sources)
                )
                text = "\n".join(
                    block["text"]
                    for block in data["document"]["blocks"]
                    if block["kind"] == "text"
                )
                self.assertIn("Public description retained", text)
                for warning in snapshot.record.warnings:
                    self.assertIn(warning, text)
                for route in snapshot.record.routes:
                    for detail in route.details:
                        self.assertIn(detail, text)
                self.assertEqual(len(data["document"]["blocks"][-1]["links"]), 2)
                if sum(counts) > 24:
                    self.assertEqual(len(snapshot.record.routes), 24)
                    self.assertIn("获取途径总数已截断。", text)

    def test_public_projection_keeps_exact_32_limit_and_rejects_33(self):
        result = validate_contract(
            CapabilityResult(
                "budget-boundary",
                ResultStatus.SUCCESS,
                validate_contract(
                    DisplayDocument(
                        "Boundary",
                        "Synthetic public result",
                        tuple(
                            validate_contract(TextBlock(str(index)))
                            for index in range(32)
                        ),
                    )
                ),
            )
        )
        self.assertEqual(len(project_result(result)["document"]["blocks"]), 32)
        oversized = replace(
            result,
            document=replace(
                result.document,
                ordered_blocks=result.document.ordered_blocks
                + (validate_contract(TextBlock("overflow")),),
            ),
        )
        with self.assertRaises(WebPublicRejected):
            project_result(oversized)
