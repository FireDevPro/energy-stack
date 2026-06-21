"""Tests for controller_core — comfort_baseline_cool pure function.

TDD: tests written before implementation. All must fail RED until
controller_core.py exists and passes them.

Run: cd deploy/energy-stack/hvac_scheduler && python -m pytest test_controller_core.py
"""
from __future__ import annotations

from datetime import datetime

import pytest

from .controller_core import comfort_baseline_cool


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
