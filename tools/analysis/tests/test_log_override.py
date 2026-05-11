"""Tests for tools/log_override.py."""
from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import pytest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import log_override  # noqa: E402


CT = ZoneInfo("America/Chicago")


def test_parse_ct_ts_accepts_trailing_ct():
    dt = log_override.parse_ct_ts("2026-07-15 14:00 CT")
    assert dt == datetime.datetime(2026, 7, 15, 14, 0, tzinfo=CT)


def test_parse_ct_ts_accepts_no_suffix():
    dt = log_override.parse_ct_ts("2026-07-15 14:00")
    assert dt == datetime.datetime(2026, 7, 15, 14, 0, tzinfo=CT)


def test_parse_ct_ts_rejects_malformed():
    with pytest.raises(ValueError):
        log_override.parse_ct_ts("2026/07/15 14:00")


def test_build_point_operational_basic():
    start = datetime.datetime(2026, 7, 15, 14, 0, tzinfo=CT)
    end = datetime.datetime(2026, 7, 15, 17, 0, tzinfo=CT)
    p = log_override.build_override_point(
        category="operational",
        start_ct=start,
        end_ct=end,
        setpoint_f=76.0,
        note="WFH afternoon",
    )
    lp = p.to_line_protocol()
    assert lp.startswith("hvac.overrides,category=operational ")
    assert "setpoint_f=76" in lp
    assert "duration_hours=3" in lp
    assert "WFH afternoon" in lp


def test_build_point_vacation_multi_day():
    start = datetime.datetime(2026, 8, 5, 8, 0, tzinfo=CT)
    end = datetime.datetime(2026, 8, 12, 18, 0, tzinfo=CT)
    p = log_override.build_override_point(
        category="vacation",
        start_ct=start,
        end_ct=end,
        setpoint_f=82.0,
    )
    lp = p.to_line_protocol()
    assert "category=vacation" in lp
    assert "duration_hours=178" in lp  # 7*24 + 10 = 178


def test_build_point_rejects_invalid_category():
    start = datetime.datetime(2026, 7, 15, 14, 0, tzinfo=CT)
    with pytest.raises(ValueError, match="category"):
        log_override.build_override_point(
            category="bogus",
            start_ct=start,
            end_ct=start + datetime.timedelta(hours=1),
            setpoint_f=76.0,
        )


def test_build_point_rejects_inverted_span():
    start = datetime.datetime(2026, 7, 15, 14, 0, tzinfo=CT)
    with pytest.raises(ValueError, match="end"):
        log_override.build_override_point(
            category="operational",
            start_ct=start,
            end_ct=start - datetime.timedelta(hours=1),
            setpoint_f=76.0,
        )


def test_build_point_rejects_implausible_setpoint():
    start = datetime.datetime(2026, 7, 15, 14, 0, tzinfo=CT)
    with pytest.raises(ValueError, match="setpoint"):
        log_override.build_override_point(
            category="operational",
            start_ct=start,
            end_ct=start + datetime.timedelta(hours=1),
            setpoint_f=120.0,
        )
