"""Tests for tools.analysis.cost_matched_exclusion per spec §5."""
from __future__ import annotations

import pytest

from tools.analysis.cost_matched_exclusion import (
    HOURS_PER_ARM,
    cost_matched_exclude,
    cost_matched_exclude_with_provenance,
)


def _full_valid_lists():
    return ([True] * HOURS_PER_ARM, [True] * HOURS_PER_ARM)


def _flat_rates(value: float) -> list[float]:
    return [value] * HOURS_PER_ARM


def test_no_exclusion_when_both_arms_fully_valid():
    va, vb = _full_valid_lists()
    out_va, out_vb = cost_matched_exclude(
        _flat_rates(10.0), _flat_rates(10.0), va, vb,
    )
    assert sum(out_va) == HOURS_PER_ARM
    assert sum(out_vb) == HOURS_PER_ARM


def test_b_invalid_2_hours_drops_2_matched_a_hours():
    va, vb = _full_valid_lists()
    vb[100] = False
    vb[200] = False
    out_va, out_vb = cost_matched_exclude(
        _flat_rates(10.0), _flat_rates(15.0), va, vb,
    )
    assert sum(out_va) == HOURS_PER_ARM - 2
    assert sum(out_vb) == HOURS_PER_ARM - 2


def test_a_invalid_drops_matched_b_hours_symmetric():
    va, vb = _full_valid_lists()
    va[50] = False
    out_va, out_vb = cost_matched_exclude(
        _flat_rates(10.0), _flat_rates(10.0), va, vb,
    )
    assert sum(out_va) == HOURS_PER_ARM - 1
    assert sum(out_vb) == HOURS_PER_ARM - 1


def test_cost_matching_picks_closest_rate():
    """Construct mostly-equal arms; drop B[0] (rate 12 in B). Pick A's
    hour with rate closest to 12 from the originally-valid pool."""
    rates_a = _flat_rates(15.0)
    rates_b = _flat_rates(12.0)
    # Make a few A hours have rate 10 -> closest is them
    rates_a[5] = 10.0
    rates_a[6] = 10.0
    rates_a[7] = 10.0
    va, vb = _full_valid_lists()
    vb[0] = False
    out_va, out_vb = cost_matched_exclude(rates_a, rates_b, va, vb)
    # |12-10|=2 (idx 5, 6, 7) beats |12-15|=3 (all other A hours).
    # Earliest tie-break -> idx 5.
    assert out_va[5] is False
    # All other A hours stay True
    assert sum(out_va) == HOURS_PER_ARM - 1
    # B keeps its original valid hours minus the original invalid
    assert sum(out_vb) == HOURS_PER_ARM - 1


def test_tie_break_chronologically_earlier():
    """All A rates equal; ties broken by smallest index."""
    rates_a = _flat_rates(10.0)
    rates_b = _flat_rates(10.0)
    va, vb = _full_valid_lists()
    vb[100] = False
    out_va, _ = cost_matched_exclude(rates_a, rates_b, va, vb)
    # Earliest unmatched valid index is 0
    assert out_va[0] is False
    assert sum(out_va) == HOURS_PER_ARM - 1


def test_excluded_hour_in_arm_is_not_a_candidate_for_its_own_arm():
    """A-invalid hour cannot be its own drop-candidate."""
    rates_a = _flat_rates(10.0)
    rates_b = _flat_rates(10.0)
    va, vb = _full_valid_lists()
    va[200] = False  # A-invalid at 200; B should drop a counterpart
    out_va, out_vb = cost_matched_exclude(rates_a, rates_b, va, vb)
    assert out_va[200] is False
    # B must drop someone OTHER than 200 (since 200 was already not in A's
    # valid pool, B at 200 is not a both-valid candidate either)
    assert sum(out_vb) == HOURS_PER_ARM - 1


def test_provenance_records_median_rate_diff_and_drop_counts():
    rates_a = _flat_rates(15.0)
    rates_b = _flat_rates(12.0)
    rates_a[5] = 10.0
    rates_a[6] = 10.0
    va, vb = _full_valid_lists()
    vb[0] = False
    vb[1] = False
    out = cost_matched_exclude_with_provenance(rates_a, rates_b, va, vb)
    assert out.n_a_dropped_for_b == 2
    assert out.n_b_dropped_for_a == 0
    # Two A drops: pick rate 10 for both -> |10-12|=2 each
    assert out.cost_match_quality_median_diff_c_per_kwh == pytest.approx(2.0)


def test_validate_lengths():
    with pytest.raises(ValueError):
        cost_matched_exclude([1.0] * 287, [1.0] * 288, [True] * 288, [True] * 288)


def test_when_no_overlap_drops_stop_gracefully():
    """If every B hour is invalid, A has no both-valid candidates to drop -> stop."""
    rates_a = _flat_rates(10.0)
    rates_b = _flat_rates(10.0)
    va, vb = [True] * HOURS_PER_ARM, [False] * HOURS_PER_ARM
    out_va, out_vb = cost_matched_exclude(rates_a, rates_b, va, vb)
    # No symmetric drops possible because there's no both-valid hour to remove.
    assert sum(out_va) == HOURS_PER_ARM
    assert sum(out_vb) == 0
