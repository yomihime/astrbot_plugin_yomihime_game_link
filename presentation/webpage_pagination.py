"""Pure, bounded pagination of a full-height browser capture.

The caller must obtain cut boundaries from measured document layout. Pixel
coordinates alone do not prove that a text line or image is safe to divide.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass

MAX_BOUNDARIES = 30000
MAX_PROTECTED = 30000


class PaginationError(ValueError):
    """No complete partition exists under the supplied safety/budget limits."""


@dataclass(frozen=True)
class CutBoundary:
    y: int
    priority: int = 0  # 0: safe line gap, 1: content block, 2: section


@dataclass(frozen=True)
class ProtectedSpan:
    y0: int
    y1: int


@dataclass(frozen=True)
class Page:
    offset: int
    height: int


@dataclass(frozen=True)
class PaginationPlan:
    pages: tuple[Page, ...]
    reason: str
    cut_positions: tuple[int, ...]


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value < 1:
        raise PaginationError(f"invalid_{name}")
    return value


def _solve(
    positions: tuple[int, ...],
    priorities: dict[int, int],
    height: int,
    width: int,
    hard_height: int,
    max_pages: int,
    *,
    strict_target: bool,
) -> tuple[int, ...] | None:
    min_pages = max(2, (height + hard_height - 1) // hard_height)
    minimum = 10 * width if strict_target else 1
    maximum = min(hard_height, 20 * width) if strict_target else hard_height
    if minimum > maximum or min_pages > max_pages:
        return None

    # can[k][i] means the suffix beginning at positions[i] admits exactly k
    # safe pages. Prefix sums answer each reachable-next-cut query in O(log n).
    # No candidate cut is discarded, so the first reachable k is the true
    # minimum page count under this target/hard-height policy.
    previous = [False] * len(positions)
    previous[-1] = True
    can = [previous]
    for count in range(1, max_pages + 1):
        prefix = [0]
        for reachable in previous:
            prefix.append(prefix[-1] + reachable)
        current = [False] * len(positions)
        for index in range(len(positions) - 1):
            low = bisect_left(positions, positions[index] + minimum, index + 1)
            high = bisect_right(positions, positions[index] + maximum, low)
            current[index] = prefix[high] > prefix[low]
        can.append(current)
        if count >= min_pages and current[0]:
            cuts = []
            start_index = 0
            for remaining in range(count, 0, -1):
                ideal = (height - positions[start_index]) / remaining
                bonus = max(1, round(ideal / 5)) ** 2
                low = bisect_left(
                    positions, positions[start_index] + minimum, start_index + 1
                )
                high = bisect_right(positions, positions[start_index] + maximum, low)
                choices = (
                    index for index in range(low, high) if can[remaining - 1][index]
                )
                # Every choice has a proven feasible suffix. Prefer balanced
                # parts, with section/content boundaries winning close calls.
                next_index = min(
                    choices,
                    key=lambda index: (
                        (positions[index] - positions[start_index] - ideal) ** 2
                        - priorities.get(positions[index], 0) * bonus,
                        abs(positions[index] - positions[start_index] - ideal),
                        index,
                    ),
                )
                start_index = next_index
                if remaining > 1:
                    cuts.append(positions[next_index])
            return tuple(cuts)
        previous = current
    return None


def plan_pages(
    width: int,
    height: int,
    *,
    boundaries: tuple[CutBoundary, ...] = (),
    protected: tuple[ProtectedSpan, ...] = (),
    max_page_height: int = 43200,
    max_pages: int = 8,
) -> PaginationPlan:
    """Return exact source-row partitions; never invent a cut or omit a row.

    For pages longer than 30 widths, prefer 10..20 widths per page. A shorter
    image stays whole when its hard height budget permits. Infeasible preferred
    ratios may be relaxed, but the hard budget and safe-cut rule never are.
    """
    width = _positive_int(width, "width")
    height = _positive_int(height, "height")
    max_page_height = _positive_int(max_page_height, "max_page_height")
    max_pages = _positive_int(max_pages, "max_pages")
    if max_pages > 64:
        raise PaginationError("invalid_max_pages")
    ratio_split = height > 30 * width
    if not ratio_split and height <= max_page_height:
        return PaginationPlan((Page(0, height),), "single_ratio", ())
    if len(boundaries) > MAX_BOUNDARIES or len(protected) > MAX_PROTECTED:
        raise PaginationError("pagination_input_budget")

    spans = []
    for span in protected:
        if (
            type(span) is not ProtectedSpan
            or type(span.y0) is not int
            or type(span.y1) is not int
            or not 0 <= span.y0 < span.y1 <= height
        ):
            raise PaginationError("invalid_protected_span")
        spans.append((span.y0, span.y1))
        if span.y1 - span.y0 > max_page_height:
            raise PaginationError("indivisible_content_exceeds_hard_budget")

    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start < merged[-1][1]:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    if any(end - start > max_page_height for start, end in merged):
        raise PaginationError("indivisible_content_exceeds_hard_budget")

    priorities: dict[int, int] = {}
    for boundary in boundaries:
        if (
            type(boundary) is not CutBoundary
            or type(boundary.y) is not int
            or type(boundary.priority) is not int
            or not 0 < boundary.y < height
            or not 0 <= boundary.priority <= 2
        ):
            raise PaginationError("invalid_cut_boundary")
        priorities[boundary.y] = max(priorities.get(boundary.y, 0), boundary.priority)

    # Monotone scan filters all supplied cuts against the merged protected
    # intervals in O(boundaries log boundaries + protected log protected).
    span_index = 0
    safe_priorities = {}
    for y, priority in sorted(priorities.items()):
        while span_index < len(merged) and merged[span_index][1] <= y:
            span_index += 1
        if (
            span_index < len(merged)
            and merged[span_index][0] < y < merged[span_index][1]
        ):
            continue
        safe_priorities[y] = priority

    positions = (0, *safe_priorities, height)
    if len(positions) <= 2:
        raise PaginationError("no_safe_cut_boundary")
    cuts = _solve(
        positions,
        safe_priorities,
        height,
        width,
        max_page_height,
        max_pages,
        strict_target=True,
    )
    target_relaxed = cuts is None
    if target_relaxed:
        cuts = _solve(
            positions,
            safe_priorities,
            height,
            width,
            max_page_height,
            max_pages,
            strict_target=False,
        )
        if cuts is None:
            raise PaginationError("no_safe_partition_within_budget")

    if not ratio_split:
        reason = "hard_budget"
    else:
        reason = "ratio"
        # The full capture exceeding the hard height does not mean the hard
        # limit affected pagination: ratio-driven pages may already be shorter.
        # A strict 10..20-width plan cannot be constrained by a limit >=20w.
        if target_relaxed or max_page_height < 20 * width:
            without_hard = _solve(
                positions,
                safe_priorities,
                height,
                width,
                height,
                max_pages,
                strict_target=True,
            )
            if without_hard is None:
                without_hard = _solve(
                    positions,
                    safe_priorities,
                    height,
                    width,
                    height,
                    max_pages,
                    strict_target=False,
                )
            if without_hard != cuts:
                reason = "ratio_and_hard_budget"
    if target_relaxed:
        reason += "_target_relaxed"

    ends = (*cuts, height)
    pages = tuple(Page(start, end - start) for start, end in zip((0, *cuts), ends))
    if any(
        abs(cut - round(height * index / len(pages))) > width // 2
        for index, cut in enumerate(cuts, 1)
    ):
        reason += "_boundary_shift"
    return PaginationPlan(pages, reason, cuts)
