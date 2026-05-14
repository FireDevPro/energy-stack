"""Tests for tools/analysis/arm_calendar.py.

Spec: docs/plans/sced-rebaseline-spec-2026-05-13.md §2 (locked 12-arm
A/B-alternating calendar, 14d periods, 48h washout).
"""
from __future__ import annotations

import datetime

import pytest

from tools.analysis.arm_calendar import (
    ARM_CALENDAR,
    HOURS_PER_ARM,
    POST_WASHOUT_DAYS,
    WASHOUT_HOURS,
    current_arm_at,
    datetime_to_hour_index,
    hour_index_to_datetime,
    post_washout_start,
)


def test_calendar_has_12_arms_alternating():
    assert len(ARM_CALENDAR) == 12
    assert [a.arm for a in ARM_CALENDAR] == ["A", "B"] * 6


def test_calendar_indices_are_one_based_and_sequential():
    assert [a.index for a in ARM_CALENDAR] == list(range(1, 13))


def test_first_arm_starts_2026_06_01_monday():
    assert ARM_CALENDAR[0].start_ct == datetime.datetime(2026, 6, 1, 0, 0)
    assert ARM_CALENDAR[0].arm == "A"
    assert ARM_CALENDAR[0].start_ct.weekday() == 0  # Monday


def test_last_arm_ends_2026_11_16():
    assert ARM_CALENDAR[-1].end_ct == datetime.datetime(2026, 11, 16, 0, 0)
    assert ARM_CALENDAR[-1].arm == "B"
    assert ARM_CALENDAR[-1].index == 12


def test_arm_periods_are_14_days():
    for arm in ARM_CALENDAR:
        assert (arm.end_ct - arm.start_ct).days == 14


def test_arm_periods_are_contiguous():
    for prev, nxt in zip(ARM_CALENDAR, ARM_CALENDAR[1:]):
        assert prev.end_ct == nxt.start_ct


def test_constants_match_spec():
    assert WASHOUT_HOURS == 48
    assert POST_WASHOUT_DAYS == 12
    assert HOURS_PER_ARM == 288


def test_current_arm_at_first_arm():
    assert current_arm_at(datetime.datetime(2026, 6, 5, 14, 0)) == "A"


def test_current_arm_at_second_arm():
    assert current_arm_at(datetime.datetime(2026, 6, 20, 14, 0)) == "B"


def test_current_arm_at_boundary_inclusive_start():
    # Boundary: arm 2 starts exactly at 2026-06-15 00:00.
    assert current_arm_at(datetime.datetime(2026, 6, 15, 0, 0)) == "B"


def test_current_arm_at_boundary_exclusive_end():
    # Last arm ends 2026-11-16 00:00 exclusive.
    assert current_arm_at(datetime.datetime(2026, 11, 16, 0, 0)) is None


def test_current_arm_at_before_window_is_none():
    assert current_arm_at(datetime.datetime(2026, 5, 31, 23, 59)) is None


def test_current_arm_at_after_window_is_none():
    assert current_arm_at(datetime.datetime(2026, 11, 17, 0, 0)) is None


def test_post_washout_start_is_wed_00():
    # Arm 1 starts Mon 2026-06-01; washout ends Wed 2026-06-03 00:00.
    assert post_washout_start(ARM_CALENDAR[0]) == datetime.datetime(2026, 6, 3, 0, 0)
    assert post_washout_start(ARM_CALENDAR[0]).weekday() == 2  # Wednesday


def test_hour_index_0_is_post_washout_start():
    arm = ARM_CALENDAR[0]
    assert hour_index_to_datetime(arm, 0) == datetime.datetime(2026, 6, 3, 0, 0)


def test_hour_index_287_is_sunday_23():
    arm = ARM_CALENDAR[0]
    # Wed 06-03 00:00 + 287 hours = Sun 06-14 23:00.
    assert hour_index_to_datetime(arm, 287) == datetime.datetime(2026, 6, 14, 23, 0)


def test_hour_index_out_of_range_raises():
    arm = ARM_CALENDAR[0]
    with pytest.raises(ValueError):
        hour_index_to_datetime(arm, -1)
    with pytest.raises(ValueError):
        hour_index_to_datetime(arm, HOURS_PER_ARM)


def test_datetime_to_hour_index_roundtrip():
    arm = ARM_CALENDAR[0]
    for k in (0, 12, 100, 287):
        dt = hour_index_to_datetime(arm, k)
        assert datetime_to_hour_index(arm, dt) == k


def test_datetime_to_hour_index_in_washout_is_none():
    arm = ARM_CALENDAR[0]
    # Mon 06-01 12:00 is in 48h washout
    assert datetime_to_hour_index(arm, datetime.datetime(2026, 6, 1, 12, 0)) is None
    # Tue 06-02 23:00 is still in washout (washout ends Wed 06-03 00:00)
    assert datetime_to_hour_index(arm, datetime.datetime(2026, 6, 2, 23, 0)) is None


def test_datetime_to_hour_index_after_arm_end_is_none():
    arm = ARM_CALENDAR[0]
    assert datetime_to_hour_index(arm, datetime.datetime(2026, 6, 15, 0, 0)) is None


def test_datetime_to_hour_index_in_other_arm_is_none():
    arm = ARM_CALENDAR[0]
    # Inside Arm 2's window
    assert datetime_to_hour_index(arm, datetime.datetime(2026, 6, 20, 12, 0)) is None


# ---- Arm 11 DST equalization (spec §2) -----------------------------------
#
# 2026-11-01 02:00 CDT falls back to 01:00 CST. Arm 11 (2026-10-19 ..
# 2026-11-02) crosses this transition. The spec uses 288 ELAPSED UTC
# hours per arm (uniform) so:
#   - hour_index 0   = Wed 2026-10-21 00:00 CDT (= 10-21 05:00 UTC)
#   - hour_index 287 = Sun 2026-11-01 22:00 CST (= 11-02 04:00 UTC)
# The "extra" wall-clock hour 23:00 CST 11-01 is the planned DST
# equalization exclusion — not missing data, not in the analysis index.


def test_arm_11_post_washout_start_is_2026_10_21():
    arm11 = ARM_CALENDAR[10]
    assert arm11.index == 11
    assert arm11.arm == "A"
    assert arm11.start_ct == datetime.datetime(2026, 10, 19, 0, 0)
    assert arm11.end_ct == datetime.datetime(2026, 11, 2, 0, 0)


def test_arm_11_hour_index_287_is_2026_11_01_22_cst():
    """Spec §2: arm 11 hour-index 287 = wall-clock Sun 11-01 22:00 CST
    (NOT 23:00 — that is the DST-equalization-excluded wall-clock hour).
    Naive CT arithmetic (start + timedelta(hours=287)) returns 23:00,
    which is wrong; the correct value comes from UTC-arithmetic.
    """
    arm11 = ARM_CALENDAR[10]
    assert hour_index_to_datetime(arm11, 287) == datetime.datetime(
        2026, 11, 1, 22, 0
    )


def test_arm_11_hour_index_286_is_2026_11_01_21_cst():
    arm11 = ARM_CALENDAR[10]
    assert hour_index_to_datetime(arm11, 286) == datetime.datetime(
        2026, 11, 1, 21, 0
    )


def test_arm_11_datetime_to_hour_index_roundtrip():
    arm11 = ARM_CALENDAR[10]
    for k in (0, 12, 100, 200, 286, 287):
        dt = hour_index_to_datetime(arm11, k)
        assert datetime_to_hour_index(arm11, dt) == k, (
            f"arm 11 roundtrip failed for hour_index {k}: "
            f"hour->dt={dt}, dt->hour={datetime_to_hour_index(arm11, dt)}"
        )


def test_arm_11_excluded_dst_hour_is_not_in_index():
    """The 11-01 23:00 CST wall-clock hour falls in the 1-hour gap
    between hour_index 287 (11-01 22:00 CST) and arm 12 start
    (11-02 00:00 CST). It must NOT map to a valid hour_index."""
    arm11 = ARM_CALENDAR[10]
    # 11-01 23:00 naive CT is the excluded hour
    excluded = datetime.datetime(2026, 11, 1, 23, 0)
    # It is NOT a valid hour_index in arm 11
    assert datetime_to_hour_index(arm11, excluded) is None


def test_arm_1_hour_index_287_unchanged_by_dst_fix():
    """Arms that don't cross DST must produce the same result as the
    naive implementation — hour_index 287 = Sun 23:00 CT."""
    arm1 = ARM_CALENDAR[0]
    assert hour_index_to_datetime(arm1, 287) == datetime.datetime(
        2026, 6, 14, 23, 0
    )


def test_arm_12_hour_index_287_is_2026_11_15_23_cst():
    """Arm 12 (2026-11-02 .. 2026-11-16) is post-DST-fall-back; no
    DST cross within arm 12. hour_index 287 should be Sun 23:00 CST."""
    arm12 = ARM_CALENDAR[11]
    assert hour_index_to_datetime(arm12, 287) == datetime.datetime(
        2026, 11, 15, 23, 0
    )
