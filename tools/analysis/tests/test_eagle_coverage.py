"""Tests for tools.analysis.eagle_coverage."""
from __future__ import annotations

import datetime

import pandas as pd
import pytest

from tools.analysis.eagle_coverage import eagle_coverage_pct


def _eagle_rows(start: datetime.datetime, hours: int,
                cadence_min: int = 60) -> pd.DataFrame:
    rows = []
    n_steps = hours * (60 // cadence_min)
    for i in range(n_steps):
        ts = start + datetime.timedelta(minutes=cadence_min * i)
        rows.append({
            "_time": ts, "_value": 1000.0 + i, "_field": "delivered_kwh",
        })
    return pd.DataFrame(rows)


def test_full_coverage_returns_100():
    start = datetime.datetime(2026, 6, 1, 0, 0)
    end = start + datetime.timedelta(hours=720)  # 30 days
    df = _eagle_rows(start, hours=720)
    pct = eagle_coverage_pct(df, start, end)
    assert pct == pytest.approx(100.0)


def test_half_coverage_returns_50():
    start = datetime.datetime(2026, 6, 1, 0, 0)
    end = start + datetime.timedelta(hours=720)
    # Cover only the first 360 hours
    df = _eagle_rows(start, hours=360)
    pct = eagle_coverage_pct(df, start, end)
    assert pct == pytest.approx(50.0)


def test_empty_eagle_returns_zero():
    start = datetime.datetime(2026, 6, 1, 0, 0)
    end = start + datetime.timedelta(hours=10)
    df = pd.DataFrame(columns=["_time", "_value"])
    assert eagle_coverage_pct(df, start, end) == 0.0


def test_multiple_rows_within_same_hour_count_as_one_hour():
    """Eagle polls fast; multiple rows in the same hour still = 1 covered hour."""
    start = datetime.datetime(2026, 6, 1, 0, 0)
    end = start + datetime.timedelta(hours=2)
    # 5-min cadence -> 12 rows per hour. Only 2 hours covered -> 100%.
    df = _eagle_rows(start, hours=2, cadence_min=5)
    pct = eagle_coverage_pct(df, start, end)
    assert pct == pytest.approx(100.0)


def test_rows_outside_window_do_not_count():
    start = datetime.datetime(2026, 6, 1, 0, 0)
    end = start + datetime.timedelta(hours=10)
    out_of_window = _eagle_rows(
        start + datetime.timedelta(days=5), hours=5,
    )
    pct = eagle_coverage_pct(out_of_window, start, end)
    assert pct == 0.0


def test_tz_aware_input_handled():
    """Production Influx returns tz-aware UTC. Helper must accept that."""
    start = datetime.datetime(2026, 6, 1, 0, 0)
    end = start + datetime.timedelta(hours=5)
    df = _eagle_rows(start, hours=5)
    df["_time"] = pd.to_datetime(df["_time"]).dt.tz_localize("UTC")
    assert eagle_coverage_pct(df, start, end) == pytest.approx(100.0)


def test_zero_length_window_returns_zero():
    start = datetime.datetime(2026, 6, 1, 0, 0)
    df = _eagle_rows(start, hours=5)
    assert eagle_coverage_pct(df, start, start) == 0.0
