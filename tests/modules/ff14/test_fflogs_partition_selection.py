"""Explicit partition eligibility, source preservation and exact row winners."""

import unittest
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from modules.ff14.features.fflogs import _assemble_catalog_rankings
from modules.ff14.features.fflogs_cards import (
    character_card,
    partition_version,
    record_version,
)
from modules.ff14.features.fflogs_models import PartitionRecord
from tests.modules.ff14 import test_fflogs_aggregation as fixture
from tests.modules.ff14 import test_fflogs_explicit_history as cache_fixture


class PartitionSelectionTests(unittest.TestCase):
    def metadata(self, partitions):
        metadata = fixture.catalog()
        return replace(
            metadata,
            zones=(
                replace(
                    metadata.zones[0],
                    partitions=tuple(PartitionRecord(*p) for p in partitions),
                ),
                *metadata.zones[4:6],
            ),
        )

    def test_full_version_expressions_and_opaque_labels(self):
        for value in (
            "7.0",
            "7.05",
            "7.51",
            "3.55b+",
            "6.4+",
            "7.0 - 7.05",
            ">=7.05",
            "[7.0, 7.05)",
        ):
            for wrapper in (
                value,
                f"标准阵容构成 ({value})",
                f"Non-Standard Composition (Patch {value})",
            ):
                with self.subTest(wrapper=wrapper):
                    self.assertEqual(partition_version(wrapper), value)
        for value in (
            "Echo",
            "default",
            "unknown 7.4 story",
            "7.4 / 7.5",
            "Standard Composition 3",
        ):
            self.assertIsNone(partition_version(value))

    def test_ultimate_winner_preserves_source_partition_name(self):
        metadata = fixture.catalog()
        targets = fixture.targets(metadata)
        result = _assemble_catalog_rankings(
            fixture.record(),
            metadata,
            targets,
            {fixture.key(t): fixture.selection(t) for t in targets},
        )
        for group in result.groups:
            if group.category == "ultimate":
                self.assertEqual(
                    group.rankings[0].origin.partition_name, "标准阵容构成 (Synthetic)"
                )

    def test_savage_explicit_same_name_ids_echo_and_whole_row_winner(self):
        metadata = self.metadata(
            (
                (1, "标准阵容构成 (7.4)", False),
                (2, "标准阵容构成 (7.4)", True),
                (3, "非标准阵容构成 (7.5)", False),
                (4, "非标准阵容构成 (超越之力)", False),
            )
        )
        targets = fixture.targets(metadata)
        savage = [t for t in targets if t[2] == "savage"]
        self.assertEqual([t[3] for t in savage], [1, 2, 3])
        outcomes = {
            fixture.key(t): fixture.selection(
                t,
                raw="99.00000000000000000000000000001" if t[3] == 3 else "99",
                amount=333 if t[3] == 3 else 555,
                kills=3 if t[3] == 3 else 9,
                job="Scholar" if t[3] == 3 else "Warrior",
            )
            for t in targets
        }
        echo = (*savage[0][:3], 4)
        outcomes[fixture.key(echo)] = fixture.selection(echo, raw="100")
        for ordered in (outcomes, dict(reversed(tuple(outcomes.items())))):
            result = _assemble_catalog_rankings(
                fixture.record(), metadata, targets, ordered
            )
            groups = [g for g in result.groups if g.category == "savage"]
            self.assertEqual(len(groups), 1)
            row = groups[0].rankings[0]
            self.assertEqual(
                (
                    row.origin.partition,
                    row.origin.partition_name,
                    row.spec,
                    row.best_amount,
                    row.total_kills,
                ),
                (3, "非标准阵容构成 (7.5)", "Scholar", 333, 3),
            )
            self.assertEqual(
                row.rank_percent_exact, Decimal("99.00000000000000000000000000001")
            )
            self.assertEqual(record_version(row), "*7.5")
            document = character_card(
                result,
                realm="cn",
                host="https://cn.fflogs.com",
                limit=40,
                explicit_selection=False,
            )
            self.assertIn("* 非标准阵容", str(document))
            self.assertEqual(document.sources, ())

    def test_unknown_default_and_echo_are_distinct_from_successful_empty(self):
        cases = (
            ((), "not_queried", False),
            (((-1, "default", True),), "not_queried", False),
            (((1, "标准阵容构成", True),), "not_queried", False),
            (((1, "Non-Standard Composition (Echo)", True),), "excluded_echo", True),
            (((1, "标准阵容构成 (7.4)", True),), "no_records", True),
        )
        for periods, state, complete in cases:
            with self.subTest(periods=periods):
                metadata = self.metadata(periods)
                targets = fixture.targets(metadata)
                outcomes = {
                    fixture.key(t): fixture.selection(t, rows=False) for t in targets
                }
                result = _assemble_catalog_rankings(
                    fixture.record(), metadata, targets, outcomes
                )
                row = next(g for g in result.groups if g.category == "savage").rankings[
                    0
                ]
                self.assertEqual(
                    (row.query_state, row.history_complete), (state, complete)
                )
                self.assertEqual(result.catalog_complete, complete)
                self.assertIsNone(row.origin)

    def test_true_tie_uses_partition_id_and_partial_keeps_whole_row(self):
        metadata = self.metadata(
            ((9, "非标准阵容构成 (7.5)", True), (2, "标准阵容构成 (7.4)", False))
        )
        targets = fixture.targets(metadata)
        outcomes = {
            fixture.key(t): fixture.selection(t, raw="99", amount=t[3], kills=t[3])
            for t in targets
        }
        for ordered in (targets, tuple(reversed(targets))):
            result = _assemble_catalog_rankings(
                fixture.record(), metadata, ordered, outcomes
            )
            row = next(g for g in result.groups if g.category == "savage").rankings[0]
            self.assertEqual(
                (row.origin.partition, row.best_amount, row.total_kills), (2, 2, 2)
            )
            self.assertEqual(record_version(row), "7.4")
        outcomes[(73, 3, 9, "rdps", None, None)] = "failed"
        partial = _assemble_catalog_rankings(
            fixture.record(), metadata, targets, outcomes
        )
        row = next(g for g in partial.groups if g.category == "savage").rankings[0]
        self.assertEqual(row.origin.partition, 2)
        self.assertFalse(row.history_complete)


class PartitionCacheTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = cache_fixture.ExplicitHistoryTests.asyncSetUp
    fetch = cache_fixture.ExplicitHistoryTests.fetch
    run_history = cache_fixture.ExplicitHistoryTests.run_history
    requested = cache_fixture.ExplicitHistoryTests.requested

    async def test_final_winner_summary_matches_fresh_and_cached_sorted_rows(self):
        events = []
        self.metadata = replace(
            self.metadata, zones=(self.metadata.zones[0], self.metadata.zones[3])
        )
        evidence = SimpleNamespace(
            observe=lambda operation, body, **kwargs: events.append((operation, body))
        )

        async def collect():
            return await self.handler._catalog_rankings(
                self.scope,
                self.source,
                self.role,
                self.metadata,
                cache_fixture.VARIABLES,
                self.handler._lifecycle,
                evidence,
                cache_owner=self.owner,
            )

        fresh = await collect()
        cached = await collect()
        summaries = [
            body for operation, body in events if operation == "FF14HistorySummary"
        ]
        self.assertEqual(len(summaries), 2)
        self.assertEqual(
            summaries[0]["metadata_sha256"], summaries[1]["metadata_sha256"]
        )
        self.assertEqual(summaries[0]["winners"], summaries[1]["winners"])
        self.assertEqual(summaries[1]["cache_hit"], 4)
        self.assertEqual(
            [row["encounter"] for row in summaries[0]["winners"]],
            [row.encounter_id for row in fresh.rankings],
        )
        self.assertEqual(fresh.rankings, cached.rankings)

    async def test_unknown_and_echo_never_dispatch_default_and_old_policy_isolated(
        self,
    ):
        metadata = self.metadata
        self.metadata = replace(
            metadata,
            zones=(
                replace(
                    metadata.zones[0],
                    partitions=(
                        PartitionRecord(1, "标准阵容构成 (7.4)", True),
                        PartitionRecord(2, "非标准阵容构成 (7.4)", False),
                        PartitionRecord(3, "Non-Standard Composition (Echo)", False),
                    ),
                ),
                replace(
                    metadata.zones[1],
                    partitions=(PartitionRecord(-1, "default", True),),
                ),
                replace(
                    metadata.zones[2],
                    partitions=(PartitionRecord(1, "标准阵容构成 (超越之力)", True),),
                ),
                metadata.zones[3],
            ),
        )
        with patch(
            "modules.ff14.features.fflogs_history_cache.POLICY",
            "explicit-standard-history-pack-v2",
        ):
            await self.run_history()
        before = len(self.calls)
        result = await self.run_history()
        requested = self.requested(self.calls[before:])
        self.assertEqual(
            set(requested), {(73, 1), (73, 2), (900, 1), (900, 2), (900, 3)}
        )
        self.assertEqual(result.history_cache_hits, 0)
        cached = await self.run_history()
        self.assertEqual(cached.history_cache_hits, 5)
        self.assertFalse(result.catalog_complete)
        self.assertEqual(result.groups, cached.groups)
