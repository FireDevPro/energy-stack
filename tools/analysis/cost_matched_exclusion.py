"""Cost-matched symmetric exclusion per
docs/plans/sced-rebaseline-spec-2026-05-13.md §5.

For each hour ``k`` where one arm is fully-valid but the other is not,
exclude one matched hour from the fully-valid arm by minimising
``|hourly_rate_other[h] - hourly_rate_excluded[k]|``. Greedy 1:1, ties
broken by chronologically earliest ``h``.

Result: after exclusion both arms have equal counts of valid hours,
conditioned on similar cost conditions. Defensible for a price-aware
controller, since the conditioning variable IS the treatment mechanism.

Indexing contract (Phase 3 DST fold safety): every input list is
length 288, indexed by post-washout hour-index 0..287. Each index is
a UTC-aligned hour boundary; there is no naive-CT collision in arm 11
because we never key on the wall-clock string.
"""
from __future__ import annotations

from dataclasses import dataclass


HOURS_PER_ARM = 288


@dataclass(frozen=True)
class ExclusionResult:
    valid_a: list[bool]
    valid_b: list[bool]
    cost_match_quality_median_diff_c_per_kwh: float
    n_a_dropped_for_b: int
    n_b_dropped_for_a: int


def _validate(rates_a, rates_b, valid_a, valid_b) -> None:
    if not (len(rates_a) == len(rates_b) == len(valid_a) == len(valid_b)
            == HOURS_PER_ARM):
        raise ValueError(
            f"all inputs must be length {HOURS_PER_ARM}; got "
            f"rates_a={len(rates_a)} rates_b={len(rates_b)} "
            f"valid_a={len(valid_a)} valid_b={len(valid_b)}"
        )


def cost_matched_exclude_with_provenance(
    rates_a: list[float],
    rates_b: list[float],
    valid_a: list[bool],
    valid_b: list[bool],
) -> ExclusionResult:
    """Greedy cost-matched symmetric exclusion. Returns the post-exclusion
    valid masks plus provenance (median absolute rate-difference, drop
    counts).
    """
    _validate(rates_a, rates_b, valid_a, valid_b)
    va = list(valid_a)
    vb = list(valid_b)

    # Snapshot the asymmetric-invalid hours from the INITIAL state. Each
    # one drives exactly one symmetric drop on the other side.
    a_to_drop_for_b = [k for k in range(HOURS_PER_ARM)
                       if valid_a[k] and not valid_b[k]]
    b_to_drop_for_a = [k for k in range(HOURS_PER_ARM)
                       if not valid_a[k] and valid_b[k]]

    abs_rate_diffs: list[float] = []
    dropped_a: set[int] = set()
    dropped_b: set[int] = set()

    # Drop A hours, one per B-invalid hour. Candidate pool per spec §5:
    # "closest unmatched fully-valid hour h in the OTHER arm" -- here
    # the OTHER arm is A, so h is fully-valid in A (valid_a[h] initially
    # True, except those already chosen as drops).
    for k in a_to_drop_for_b:
        target_rate = rates_b[k]
        best_h: int | None = None
        best_diff: float = float("inf")
        for h in range(HOURS_PER_ARM):
            if not valid_a[h] or h in dropped_a:
                continue
            diff = abs(rates_a[h] - target_rate)
            if diff < best_diff:
                best_diff = diff
                best_h = h
            # tie-break: chronologically earlier wins; since we iterate
            # h in ascending order and only overwrite on strict
            # less-than, the first hit at this diff stays.
        if best_h is None:
            break  # no candidates left
        dropped_a.add(best_h)
        va[best_h] = False
        abs_rate_diffs.append(best_diff)

    # Drop B hours, one per A-invalid hour
    for k in b_to_drop_for_a:
        target_rate = rates_a[k]
        best_h: int | None = None
        best_diff: float = float("inf")
        for h in range(HOURS_PER_ARM):
            if not valid_b[h] or h in dropped_b:
                continue
            diff = abs(rates_b[h] - target_rate)
            if diff < best_diff:
                best_diff = diff
                best_h = h
        if best_h is None:
            break
        dropped_b.add(best_h)
        vb[best_h] = False
        abs_rate_diffs.append(best_diff)

    if abs_rate_diffs:
        # Median; sort + pick middle for stdlib-only behavior.
        sorted_diffs = sorted(abs_rate_diffs)
        n = len(sorted_diffs)
        median = (
            sorted_diffs[n // 2]
            if n % 2 == 1
            else (sorted_diffs[n // 2 - 1] + sorted_diffs[n // 2]) / 2.0
        )
    else:
        median = 0.0

    return ExclusionResult(
        valid_a=va,
        valid_b=vb,
        cost_match_quality_median_diff_c_per_kwh=median,
        n_a_dropped_for_b=len(a_to_drop_for_b),
        n_b_dropped_for_a=len(b_to_drop_for_a),
    )


def cost_matched_exclude(
    rates_a: list[float],
    rates_b: list[float],
    valid_a: list[bool],
    valid_b: list[bool],
) -> tuple[list[bool], list[bool]]:
    """Convenience wrapper that returns just the (valid_a, valid_b)
    pair. Use ``cost_matched_exclude_with_provenance`` to get the
    median-rate-diff and drop counts the per-pair table needs.
    """
    out = cost_matched_exclude_with_provenance(
        rates_a, rates_b, valid_a, valid_b
    )
    return out.valid_a, out.valid_b
