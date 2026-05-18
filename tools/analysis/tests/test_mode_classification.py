"""Tests for tools.analysis.mode_classification per spec §5 + §5.1."""
from __future__ import annotations

import datetime

from tools.analysis.mode_classification import (
    CAPACITY_RISK_WINDOW_END,
    CAPACITY_RISK_WINDOW_START,
    HourMode,
    classify_hour,
    in_capacity_risk_window,
    is_fully_valid,
    required_feeds_at,
)

TS_IN_WINDOW = datetime.datetime(2026, 7, 15, 14, 0)
TS_OUT_WINDOW = datetime.datetime(2026, 10, 20, 14, 0)


def _all_healthy() -> dict[str, bool]:
    return {"price": True, "weather": True, "pjm_capacity_risk": True}


def test_arm_a_telemetry_valid_is_a_active():
    assert classify_hour(arm="A", when_ct=TS_IN_WINDOW, telemetry_valid=True,
                         controller_alive=True, feeds=_all_healthy()) == HourMode.A_ACTIVE


def test_arm_a_telemetry_invalid_is_invalid():
    assert classify_hour(arm="A", when_ct=TS_IN_WINDOW, telemetry_valid=False,
                         controller_alive=True, feeds=_all_healthy()) == HourMode.TELEMETRY_INVALID


def test_arm_b_all_required_healthy_in_window_is_b_active():
    assert classify_hour(arm="B", when_ct=TS_IN_WINDOW, telemetry_valid=True,
                         controller_alive=True, feeds=_all_healthy()) == HourMode.B_ACTIVE


def test_arm_b_controller_dead_is_b_down():
    assert classify_hour(arm="B", when_ct=TS_IN_WINDOW, telemetry_valid=True,
                         controller_alive=False, feeds=_all_healthy()) == HourMode.B_DOWN


def test_arm_b_price_stale_in_window_is_b_fallback():
    feeds = {"price": False, "weather": True, "pjm_capacity_risk": True}
    assert classify_hour(arm="B", when_ct=TS_IN_WINDOW, telemetry_valid=True,
                         controller_alive=True, feeds=feeds) == HourMode.B_FALLBACK


def test_arm_b_weather_stale_in_window_is_b_fallback():
    feeds = {"price": True, "weather": False, "pjm_capacity_risk": True}
    assert classify_hour(arm="B", when_ct=TS_IN_WINDOW, telemetry_valid=True,
                         controller_alive=True, feeds=feeds) == HourMode.B_FALLBACK


def test_arm_b_pjm_capacity_stale_INSIDE_window_is_b_fallback():
    """Inside §5.1 window, PJM capacity-risk inputs ARE required."""
    feeds = {"price": True, "weather": True, "pjm_capacity_risk": False}
    assert classify_hour(arm="B", when_ct=TS_IN_WINDOW, telemetry_valid=True,
                         controller_alive=True, feeds=feeds) == HourMode.B_FALLBACK


def test_arm_b_pjm_capacity_stale_OUTSIDE_window_is_b_active():
    """Outside §5.1 window, PJM capacity-risk inputs are NOT required."""
    feeds = {"price": True, "weather": True, "pjm_capacity_risk": False}
    assert classify_hour(arm="B", when_ct=TS_OUT_WINDOW, telemetry_valid=True,
                         controller_alive=True, feeds=feeds) == HourMode.B_ACTIVE


def test_arm_b_telemetry_invalid_dominates():
    """telemetry-invalid wins regardless of treatment-mode state."""
    feeds = {"price": False, "weather": False, "pjm_capacity_risk": False}
    assert classify_hour(arm="B", when_ct=TS_IN_WINDOW, telemetry_valid=False,
                         controller_alive=False, feeds=feeds) == HourMode.TELEMETRY_INVALID


def test_capacity_window_boundaries():
    # start is inclusive, end is exclusive
    assert in_capacity_risk_window(CAPACITY_RISK_WINDOW_START) is True
    just_before = CAPACITY_RISK_WINDOW_START - datetime.timedelta(seconds=1)
    assert in_capacity_risk_window(just_before) is False
    just_inside_end = CAPACITY_RISK_WINDOW_END - datetime.timedelta(seconds=1)
    assert in_capacity_risk_window(just_inside_end) is True
    assert in_capacity_risk_window(CAPACITY_RISK_WINDOW_END) is False


def test_required_feeds_in_vs_out_of_window():
    assert required_feeds_at(TS_IN_WINDOW) == {"price", "weather", "pjm_capacity_risk"}
    assert required_feeds_at(TS_OUT_WINDOW) == {"price", "weather"}


def test_is_fully_valid_only_a_active_and_b_active():
    assert is_fully_valid(HourMode.A_ACTIVE) is True
    assert is_fully_valid(HourMode.B_ACTIVE) is True
    assert is_fully_valid(HourMode.B_FALLBACK) is False
    assert is_fully_valid(HourMode.B_DOWN) is False
    assert is_fully_valid(HourMode.TELEMETRY_INVALID) is False
