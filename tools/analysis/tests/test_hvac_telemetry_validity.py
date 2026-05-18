"""Tests for tools.analysis.hvac_telemetry_validity per spec §7."""
from __future__ import annotations

import datetime

import pandas as pd

from tools.analysis.hvac_telemetry_validity import (
    HVAC_CHANNELS,
    hour_is_telemetry_valid,
)


def _samples(start: datetime.datetime, count: int, *, channel: str,
             gap_seconds: int = 30) -> pd.DataFrame:
    return pd.DataFrame({
        "_time": [start + datetime.timedelta(seconds=gap_seconds * i)
                  for i in range(count)],
        "_value": [100.0] * count,
        "channel": [channel] * count,
    })


def _all_channels(start: datetime.datetime, count: int = 120,
                  gap_seconds: int = 30) -> pd.DataFrame:
    return pd.concat(
        [_samples(start, count, channel=ch, gap_seconds=gap_seconds)
         for ch in HVAC_CHANNELS],
        ignore_index=True,
    )


def test_120_samples_no_gap_is_valid():
    start = datetime.datetime(2026, 6, 5, 14, 0)
    df = _all_channels(start, count=120, gap_seconds=30)
    assert hour_is_telemetry_valid(df, hour_start_utc=start) is True


def test_below_110_samples_is_invalid():
    start = datetime.datetime(2026, 6, 5, 14, 0)
    df = _all_channels(start, count=109, gap_seconds=30)
    assert hour_is_telemetry_valid(df, hour_start_utc=start) is False


def test_exactly_110_samples_evenly_spread_is_valid():
    """110 samples spread evenly across the hour ~32.7s apart -> no gap > 120s."""
    start = datetime.datetime(2026, 6, 5, 14, 0)
    # ~32.7s spacing keeps all gaps (incl. boundaries) <= ~33s
    df = _all_channels(start, count=110, gap_seconds=32)
    assert hour_is_telemetry_valid(df, hour_start_utc=start) is True


def test_110_samples_packed_to_first_55min_is_invalid():
    """110 samples at 30s only spans 54.5 min -> trailing 5.5 min gap > 120s."""
    start = datetime.datetime(2026, 6, 5, 14, 0)
    df = _all_channels(start, count=110, gap_seconds=30)
    assert hour_is_telemetry_valid(df, hour_start_utc=start) is False


def test_leading_boundary_gap_over_120s_is_invalid():
    """First sample 3 min into the hour -> 180s leading gap > 120s."""
    start = datetime.datetime(2026, 6, 5, 14, 0)
    delayed = start + datetime.timedelta(seconds=180)
    df = _all_channels(delayed, count=115, gap_seconds=30)
    assert hour_is_telemetry_valid(df, hour_start_utc=start) is False


def test_gap_over_120s_is_invalid():
    """em:2 gets 60 samples + 121s gap + 60 samples; em:8/em:9 clean."""
    start = datetime.datetime(2026, 6, 5, 14, 0)
    em2_first = _samples(start, 60, channel="em:2", gap_seconds=30)
    em2_second = _samples(
        start + datetime.timedelta(seconds=60 * 30 + 121),
        60, channel="em:2", gap_seconds=30,
    )
    em8 = _samples(start, 120, channel="em:8", gap_seconds=30)
    em9 = _samples(start, 120, channel="em:9", gap_seconds=30)
    df = pd.concat([em2_first, em2_second, em8, em9], ignore_index=True)
    assert hour_is_telemetry_valid(df, hour_start_utc=start) is False


def test_any_channel_failure_kills_hour():
    """em:9 has only 100 samples; em:2/em:8 are fine -> hour invalid."""
    start = datetime.datetime(2026, 6, 5, 14, 0)
    em2 = _samples(start, 120, channel="em:2", gap_seconds=30)
    em8 = _samples(start, 120, channel="em:8", gap_seconds=30)
    em9 = _samples(start, 100, channel="em:9", gap_seconds=30)
    df = pd.concat([em2, em8, em9], ignore_index=True)
    assert hour_is_telemetry_valid(df, hour_start_utc=start) is False


def test_other_hours_data_does_not_affect_decision():
    """Rows outside [hour_start, hour_start+1h) must be ignored."""
    start = datetime.datetime(2026, 6, 5, 14, 0)
    in_hour = _all_channels(start, count=120, gap_seconds=30)
    out_of_hour = _all_channels(
        start + datetime.timedelta(hours=3), count=120, gap_seconds=30,
    )
    df = pd.concat([in_hour, out_of_hour], ignore_index=True)
    assert hour_is_telemetry_valid(df, hour_start_utc=start) is True


def test_field_filter_excludes_non_power_w_rows():
    """If _field column is present, only _field == 'power_w' rows count."""
    start = datetime.datetime(2026, 6, 5, 14, 0)
    power = _all_channels(start, count=120, gap_seconds=30)
    power["_field"] = "power_w"
    # Add extra rows tagged as a different field; they should NOT count.
    pf = _all_channels(start, count=120, gap_seconds=30)
    pf["_field"] = "power_factor"
    df_with_extras = pd.concat([power, pf], ignore_index=True)
    assert hour_is_telemetry_valid(df_with_extras, hour_start_utc=start) is True

    # If the power_w rows alone are insufficient (90 samples), it must fail
    # even when many power_factor rows exist.
    sparse = _all_channels(start, count=90, gap_seconds=40)
    sparse["_field"] = "power_w"
    df_sparse_power = pd.concat([sparse, pf], ignore_index=True)
    assert hour_is_telemetry_valid(df_sparse_power, hour_start_utc=start) is False
