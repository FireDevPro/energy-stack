"""Tests for controller_core — comfort_baseline_cool pure function.

TDD: tests written before implementation. All must fail RED until
controller_core.py exists and passes them.

Run: cd deploy/energy-stack/hvac_scheduler && python -m pytest test_controller_core.py
"""
from __future__ import annotations

from datetime import datetime, time as dtime, timedelta

import pytest

from .controller_core import comfort_baseline_cool, hold_expiry, needs_hold_refresh


# Comfort program matching commissioning-controller.example.yaml (Celsius)
PROGRAM = [
    {"from": "22:00", "to": "06:00", "cool": 23.5},  # sleep  (midnight wrap)
    {"from": "06:00", "to": "12:00", "cool": 24.5},  # morning
    {"from": "12:00", "to": "18:00", "cool": 25.5},  # midday
    {"from": "18:00", "to": "22:00", "cool": 24.5},  # evening
]


# ---------------------------------------------------------------------------
# Representative times — non-wrap blocks
# ---------------------------------------------------------------------------

def test_morning_block() -> None:
    when = datetime(2026, 6, 20, 7, 0)   # 07:00 — inside 06:00-12:00
    assert comfort_baseline_cool(PROGRAM, when) == 24.5


def test_midday_block() -> None:
    when = datetime(2026, 6, 20, 15, 0)  # 15:00 — inside 12:00-18:00
    assert comfort_baseline_cool(PROGRAM, when) == 25.5


def test_evening_block() -> None:
    when = datetime(2026, 6, 20, 20, 0)  # 20:00 — inside 18:00-22:00
    assert comfort_baseline_cool(PROGRAM, when) == 24.5


def test_block_boundary_start_inclusive() -> None:
    when = datetime(2026, 6, 20, 6, 0)   # exactly 06:00 — morning starts
    assert comfort_baseline_cool(PROGRAM, when) == 24.5


def test_block_boundary_end_exclusive() -> None:
    when = datetime(2026, 6, 20, 12, 0)  # exactly 12:00 — midday starts
    assert comfort_baseline_cool(PROGRAM, when) == 25.5


# ---------------------------------------------------------------------------
# Midnight-wrap block: 22:00 -> 06:00
# ---------------------------------------------------------------------------

def test_midnight_wrap_evening_side() -> None:
    """23:00 is past midnight boundary — inside 22:00-06:00 (sleep)."""
    when = datetime(2026, 6, 20, 23, 0)
    assert comfort_baseline_cool(PROGRAM, when) == 23.5


def test_midnight_wrap_early_morning() -> None:
    """03:00 is before 06:00 — still inside the 22:00-06:00 sleep block."""
    when = datetime(2026, 6, 20, 3, 0)
    assert comfort_baseline_cool(PROGRAM, when) == 23.5


def test_midnight_wrap_exactly_at_22() -> None:
    """22:00 exactly — start of the sleep block."""
    when = datetime(2026, 6, 20, 22, 0)
    assert comfort_baseline_cool(PROGRAM, when) == 23.5


def test_midnight_wrap_just_before_end() -> None:
    """05:59 — last minute of the sleep block before morning starts."""
    when = datetime(2026, 6, 20, 5, 59)
    assert comfort_baseline_cool(PROGRAM, when) == 23.5


# ---------------------------------------------------------------------------
# F-scale program (whole-number setpoints)
# ---------------------------------------------------------------------------

PROGRAM_F = [
    {"from": "22:00", "to": "06:00", "cool": 73},
    {"from": "06:00", "to": "12:00", "cool": 75},
    {"from": "12:00", "to": "18:00", "cool": 78},
    {"from": "18:00", "to": "22:00", "cool": 75},
]


def test_f_scale_midday() -> None:
    when = datetime(2026, 6, 20, 14, 0)
    assert comfort_baseline_cool(PROGRAM_F, when) == 78


def test_f_scale_midnight_wrap() -> None:
    when = datetime(2026, 6, 20, 1, 0)
    assert comfort_baseline_cool(PROGRAM_F, when) == 73


# ---------------------------------------------------------------------------
# hold_expiry — timed-hold device expiry (spec Safety #2: lapse <= TTL)
# ---------------------------------------------------------------------------


def test_hold_expiry_floors_to_quarter_hour() -> None:
    # 12:07 + 60 min = 13:07 -> floor to 13:00 (never past now+TTL)
    now = datetime(2026, 7, 3, 12, 7)
    assert hold_expiry(now, 60) == datetime(2026, 7, 3, 13, 0)


def test_hold_expiry_on_boundary_unchanged() -> None:
    now = datetime(2026, 7, 3, 12, 0)
    assert hold_expiry(now, 60) == datetime(2026, 7, 3, 13, 0)


def test_hold_expiry_never_exceeds_ttl() -> None:
    now = datetime(2026, 7, 3, 12, 14)
    exp = hold_expiry(now, 60)
    assert exp == datetime(2026, 7, 3, 13, 0)
    assert exp - now <= timedelta(minutes=60)


def test_hold_expiry_zeroes_seconds() -> None:
    now = datetime(2026, 7, 3, 12, 7, 45, 123456)
    exp = hold_expiry(now, 60)
    assert exp == datetime(2026, 7, 3, 13, 0)
    assert exp.second == 0 and exp.microsecond == 0


def test_hold_expiry_midnight_wrap() -> None:
    # 23:50 + 60 = 00:50 next day -> floor 00:45 next day (date advances;
    # the device sees the time-of-day slot and holds to its next occurrence)
    now = datetime(2026, 7, 3, 23, 50)
    assert hold_expiry(now, 60) == datetime(2026, 7, 4, 0, 45)


def test_hold_expiry_result_on_device_grid() -> None:
    # aiosomecomfort raises on non-quarter-hour times; every result must land
    # on the device grid
    for minute in (0, 3, 7, 14, 22, 29, 31, 44, 46, 59):
        exp = hold_expiry(datetime(2026, 7, 3, 9, minute), 60)
        assert exp.minute in (0, 15, 30, 45)


def test_hold_expiry_preserves_tzinfo() -> None:
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("America/Chicago")
    now = datetime(2026, 7, 3, 12, 7, tzinfo=tz)
    exp = hold_expiry(now, 60)
    assert exp.tzinfo == tz
    assert exp.time() == dtime(13, 0)


# ---------------------------------------------------------------------------
# needs_hold_refresh — an alive controller re-establishes its hold before the
# device-side expiry lapses (5-min margin = ~5 one-per-tick retries)
# ---------------------------------------------------------------------------


def test_no_refresh_when_no_hold_tracked() -> None:
    assert needs_hold_refresh(datetime(2026, 7, 3, 12, 0), None) is False


def test_no_refresh_far_from_expiry() -> None:
    now = datetime(2026, 7, 3, 12, 0)
    assert needs_hold_refresh(now, datetime(2026, 7, 3, 13, 0)) is False


def test_refresh_inside_margin() -> None:
    now = datetime(2026, 7, 3, 12, 56)
    assert needs_hold_refresh(now, datetime(2026, 7, 3, 13, 0)) is True


def test_refresh_at_exact_margin_boundary() -> None:
    now = datetime(2026, 7, 3, 12, 55)
    assert needs_hold_refresh(now, datetime(2026, 7, 3, 13, 0)) is True


def test_refresh_after_missed_expiry_self_heals() -> None:
    # Past-expiry (missed refreshes / device already reverted): keep asking
    # for a push every tick until one succeeds
    now = datetime(2026, 7, 3, 13, 7)
    assert needs_hold_refresh(now, datetime(2026, 7, 3, 13, 0)) is True
