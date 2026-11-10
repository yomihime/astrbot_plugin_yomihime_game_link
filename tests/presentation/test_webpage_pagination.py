"""Source-row pagination contract; browser geometry is tested separately."""

import unittest

from ygl_test_subject.presentation.webpage_pagination import (
    CutBoundary,
    PaginationError,
    ProtectedSpan,
    plan_pages,
)


class WebpagePaginationTests(unittest.TestCase):
    def test_whole_page_through_thirty_widths_when_budget_allows(self):
        for height in (1, 1200 * 10, 1200 * 30):
            plan = plan_pages(1200, height)
            assert [(p.offset, p.height) for p in plan.pages] == [(0, height)]
            assert plan.reason == "single_ratio"

    def test_long_page_uses_fewest_balanced_safe_pages(self):
        plan = plan_pages(
            1200,
            48000,
            max_page_height=48000,
            boundaries=tuple(CutBoundary(y) for y in (12000, 24000, 36000)),
        )
        assert plan.cut_positions == (24000,)
        assert [(p.offset, p.height) for p in plan.pages] == [
            (0, 24000),
            (24000, 24000),
        ]
        assert plan.reason == "ratio"

    def test_section_boundary_preferred_within_reasonable_balance(self):
        plan = plan_pages(
            1200,
            40000,
            boundaries=(CutBoundary(19000), CutBoundary(21000, 2)),
        )
        assert plan.cut_positions == (21000,)

    def test_protected_text_and_image_interiors_are_never_cut(self):
        plan = plan_pages(
            1000,
            40000,
            boundaries=(CutBoundary(19000), CutBoundary(21000)),
            protected=(ProtectedSpan(18500, 19500),),
        )
        assert plan.cut_positions == (21000,)

    def test_hard_budget_can_force_early_split_and_full_coverage(self):
        plan = plan_pages(
            1200,
            30000,
            max_page_height=20000,
            boundaries=(CutBoundary(15000),),
        )
        assert [(p.offset, p.height) for p in plan.pages] == [
            (0, 15000),
            (15000, 15000),
        ]
        assert plan.reason == "hard_budget"

    def test_target_can_relax_but_hard_budget_cannot(self):
        plan = plan_pages(
            1000,
            36000,
            boundaries=(CutBoundary(23000),),
        )
        assert plan.cut_positions == (23000,)
        assert "target_relaxed" in plan.reason
        with self.assertRaisesRegex(PaginationError, "no_safe_partition_within_budget"):
            plan_pages(
                1000, 36000, boundaries=(CutBoundary(23000),), max_page_height=20000
            )

    def test_missing_safe_cut_and_indivisible_image_fail_explicitly(self):
        with self.assertRaisesRegex(PaginationError, "no_safe_cut_boundary"):
            plan_pages(1200, 40000)
        with self.assertRaisesRegex(PaginationError, "indivisible_content"):
            plan_pages(
                1000,
                40000,
                max_page_height=20000,
                boundaries=(CutBoundary(20000),),
                protected=(ProtectedSpan(10000, 35000),),
            )

    def test_bounded_untrusted_geometry_and_exact_row_accounting(self):
        with self.assertRaisesRegex(PaginationError, "pagination_input_budget"):
            plan_pages(1000, 40000, boundaries=(CutBoundary(1),) * 30001)
        with self.assertRaisesRegex(PaginationError, "invalid_cut_boundary"):
            plan_pages(1000, 40000, boundaries=(CutBoundary(True),))
        with self.assertRaisesRegex(PaginationError, "invalid_protected_span"):
            plan_pages(1000, 40000, protected=(ProtectedSpan(-1, 2),))
        plan = plan_pages(
            1000,
            40000,
            max_page_height=16000,
            boundaries=(CutBoundary(10000), CutBoundary(20000), CutBoundary(30000)),
        )
        assert len(plan.pages) == 4
        assert plan.pages[0].offset == 0
        assert plan.pages[-1].offset + plan.pages[-1].height == 40000
        assert all(
            a.offset + a.height == b.offset for a, b in zip(plan.pages, plan.pages[1:])
        )

    def test_large_dom_geometry_retains_all_safe_choices_and_minimum_pages(self):
        # A dense DOM may yield one protected interval and candidate cut per line.
        boundaries = tuple(CutBoundary(2 * i) for i in range(1, 30001))
        protected = tuple(ProtectedSpan(2 * i + 1, 2 * i + 2) for i in range(30000))
        plan = plan_pages(1000, 60002, boundaries=boundaries, protected=protected)
        assert len(plan.pages) == 4
        assert all(10000 <= page.height <= 20000 for page in plan.pages)
        assert plan.pages[-1].offset + plan.pages[-1].height == 60002

    def test_exact_feasibility_chooses_three_pages_when_two_cannot_hit_target(self):
        plan = plan_pages(
            1000,
            45000,
            max_page_height=45000,
            boundaries=(CutBoundary(10000), CutBoundary(25000)),
        )
        assert plan.cut_positions == (10000, 25000)

    def test_single_page_does_not_require_dom_geometry(self):
        plan = plan_pages(1200, 2000, boundaries=(CutBoundary(1),) * 30001)
        assert [(page.offset, page.height) for page in plan.pages] == [(0, 2000)]

    def test_ratio_reason_when_full_height_exceeds_hard_limit_but_pages_do_not(self):
        plan = plan_pages(
            1200,
            89957,
            boundaries=tuple(CutBoundary(y) for y in (18000, 36000, 54000, 72000)),
        )
        assert len(plan.pages) == 5
        assert plan.reason == "ratio"

    def test_hard_reason_only_when_hard_limit_changes_ratio_plan(self):
        boundaries = tuple(CutBoundary(y) for y in (16000, 24000, 32000))
        unconstrained = plan_pages(
            1200, 48000, boundaries=boundaries, max_page_height=48000
        )
        constrained = plan_pages(
            1200, 48000, boundaries=boundaries, max_page_height=18000
        )
        assert len(unconstrained.pages) == 2
        assert len(constrained.pages) == 3
        assert constrained.reason == "ratio_and_hard_budget"
