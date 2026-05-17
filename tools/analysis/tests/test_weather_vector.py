"""Tests for tools.analysis.weather_vector per spec §6."""
from __future__ import annotations

import datetime

import pandas as pd
import pytest

from tools.analysis.arm_calendar import (
    ARM_CALENDAR,
    HOURS_PER_ARM,
    post_washout_start,
)
from tools.analysis.weather_vector import (
    CDD_BASE_F,
    WeatherVector,
    build_weather_vector,
)
from zoneinfo import ZoneInfo

_CT = ZoneInfo("America/Chicago")
_UTC = ZoneInfo("UTC")


def _post_washout_start_utc_naive(arm) -> datetime.datetime:
    return (
        post_washout_start(arm)
        .replace(tzinfo=_CT)
        .astimezone(_UTC)
        .replace(tzinfo=None)
    )


def _ecowitt_constant(arm, *, temp_f=80.0, dewpoint_f=60.0) -> pd.DataFrame:
    """Build 288 hourly Ecowitt rows aligned to the arm's post-washout
    window (naive UTC timestamps)."""
    start_utc = _post_washout_start_utc_naive(arm)
    rows = []
    for k in range(HOURS_PER_ARM):
        rows.append({
            "_time": start_utc + datetime.timedelta(hours=k),
            "ch1_temp_f": temp_f,
            "ch1_dewpoint_f": dewpoint_f,
        })
    return pd.DataFrame(rows)


def _ecowitt_diurnal(arm, *, day_temp=95.0, night_temp=70.0,
                    dewpoint_f=65.0) -> pd.DataFrame:
    """Hourly Ecowitt rows with day_temp during CT daytime (06:00-22:00)
    and night_temp during CT nocturnal hours (22:00-06:00)."""
    start_utc = _post_washout_start_utc_naive(arm)
    rows = []
    for k in range(HOURS_PER_ARM):
        ts_utc = start_utc + datetime.timedelta(hours=k)
        ts_ct = ts_utc.replace(tzinfo=_UTC).astimezone(_CT)
        is_nocturnal = ts_ct.hour in (22, 23, 0, 1, 2, 3, 4, 5)
        rows.append({
            "_time": ts_utc,
            "ch1_temp_f": night_temp if is_nocturnal else day_temp,
            "ch1_dewpoint_f": dewpoint_f,
        })
    return pd.DataFrame(rows)


def test_weather_vector_has_four_components():
    arm = ARM_CALENDAR[0]  # Arm 1
    df = _ecowitt_constant(arm, temp_f=80.0, dewpoint_f=60.0)
    vec = build_weather_vector(arm, df)
    assert isinstance(vec, WeatherVector)
    assert vec.as_array().shape == (4,)
    assert vec.mean_daily_max_temp_f == pytest.approx(80.0)
    assert vec.mean_nocturnal_min_temp_f == pytest.approx(80.0)
    assert vec.mean_dewpoint_f == pytest.approx(60.0)


def test_cdd_total_sum_over_12_days():
    """Constant 80 F over 288 hours -> CDD = 288 * (80 - 65) / 24 = 180."""
    arm = ARM_CALENDAR[0]
    df = _ecowitt_constant(arm, temp_f=80.0)
    vec = build_weather_vector(arm, df)
    assert vec.cdd_total == pytest.approx(180.0)


def test_cdd_zero_when_below_base():
    arm = ARM_CALENDAR[0]
    df = _ecowitt_constant(arm, temp_f=60.0)  # below base
    vec = build_weather_vector(arm, df)
    assert vec.cdd_total == pytest.approx(0.0)


def test_nocturnal_min_uses_22_06_window():
    """Diurnal pattern: day=95, night=70 -> daily_max~=95, nocturnal_min~=70."""
    arm = ARM_CALENDAR[0]
    df = _ecowitt_diurnal(arm, day_temp=95.0, night_temp=70.0)
    vec = build_weather_vector(arm, df)
    assert vec.mean_daily_max_temp_f == pytest.approx(95.0)
    assert vec.mean_nocturnal_min_temp_f == pytest.approx(70.0)


def test_outside_window_rows_ignored():
    """Rows before/after the post-washout window must not contribute."""
    arm = ARM_CALENDAR[0]
    in_window = _ecowitt_constant(arm, temp_f=80.0)
    start_utc = _post_washout_start_utc_naive(arm)
    # 10 trailing rows at temp 200 F (would massively inflate stats)
    out_of_window = pd.DataFrame([
        {
            "_time": start_utc + datetime.timedelta(hours=HOURS_PER_ARM + i),
            "ch1_temp_f": 200.0,
            "ch1_dewpoint_f": 200.0,
        }
        for i in range(10)
    ])
    df = pd.concat([in_window, out_of_window], ignore_index=True)
    vec = build_weather_vector(arm, df)
    assert vec.mean_daily_max_temp_f == pytest.approx(80.0)
    assert vec.mean_dewpoint_f == pytest.approx(60.0)


def test_empty_window_raises():
    arm = ARM_CALENDAR[0]
    df = pd.DataFrame(columns=["_time", "ch1_temp_f", "ch1_dewpoint_f"])
    with pytest.raises(ValueError):
        build_weather_vector(arm, df)


def test_arm11_dst_fold_still_aggregates_288_hours():
    """Arm 11 spans 2026-11-01 fall-back. The 288-elapsed-UTC-hour window
    must still yield well-defined aggregates (no NaN, no duplicate-key
    collapse).
    """
    arm11 = ARM_CALENDAR[10]
    assert arm11.index == 11
    df = _ecowitt_constant(arm11, temp_f=72.0, dewpoint_f=55.0)
    vec = build_weather_vector(arm11, df)
    # 288 hours at (72 - 65) / 24 = 0.2917 -> ~84 CDD
    assert vec.cdd_total == pytest.approx(288 * 7 / 24, rel=1e-6)
    assert vec.mean_daily_max_temp_f == pytest.approx(72.0)
    assert vec.mean_nocturnal_min_temp_f == pytest.approx(72.0)
    assert vec.mean_dewpoint_f == pytest.approx(55.0)
