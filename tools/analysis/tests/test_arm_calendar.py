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
