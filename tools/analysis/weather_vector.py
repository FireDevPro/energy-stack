"""4-component arm-period weather vector per
docs/plans/sced-rebaseline-spec-2026-05-13.md §6.

Components (in fixed order):
1. ``cdd_total`` -- sum of hourly CDD (base 65 F) over the 12-day window
2. ``mean_daily_max_temp_f`` -- mean of per-day max temp over the 12 days
3. ``mean_nocturnal_min_temp_f`` -- mean of per-night (22:00-06:00 CT)
   min temp over the 12 nights
4. ``mean_dewpoint_f`` -- mean over all valid hours

Solar and wind are intentionally dropped (spec §6 "dropped from vector").

Time-zone contract (DST fold safety, plan §3 caller contract):
- ``arm.start_ct`` is CT-local naive.
- ``ecowitt_df["_time"]`` is naive UTC instants (matches fixture and
  matches what we ingest from Influx after a ``.dt.tz_convert(None)``).
- All bucketing into "CT day" and "CT hour-of-day" goes through
  ``zoneinfo.ZoneInfo("America/Chicago")`` so arm 11 (2026-11-01
  fall-back) folds correctly.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from tools.analysis.arm_calendar import (
    ArmPeriod,
    HOURS_PER_ARM,
    post_washout_start,
)


CDD_BASE_F = 65.0
NOCTURNAL_HOURS_CT = (22, 23, 0, 1, 2, 3, 4, 5)
_CT = ZoneInfo("America/Chicago")
_UTC = ZoneInfo("UTC")


@dataclass(frozen=True)
class WeatherVector:
    cdd_total: float
    mean_daily_max_temp_f: float
    mean_nocturnal_min_temp_f: float
    mean_dewpoint_f: float

    def as_array(self) -> np.ndarray:
        return np.array(
            [
                self.cdd_total,
                self.mean_daily_max_temp_f,
                self.mean_nocturnal_min_temp_f,
                self.mean_dewpoint_f,
            ],
            dtype=float,
        )


def _post_washout_utc_bounds(arm: ArmPeriod) -> tuple[datetime.datetime, datetime.datetime]:
    """Return naive-UTC [start, end) bounds for the arm's 288-hour
    post-washout window."""
    start_aware = post_washout_start(arm).replace(tzinfo=_CT)
    start_utc = start_aware.astimezone(_UTC).replace(tzinfo=None)
    end_utc = start_utc + datetime.timedelta(hours=HOURS_PER_ARM)
    return start_utc, end_utc


def _normalize_to_naive_utc(times: pd.Series) -> pd.Series:
    """Return a tz-naive UTC Series regardless of input timezone state.

    Real Influx returns tz-aware UTC; fixtures often store naive UTC.
    Normalize at the boundary so window comparisons against naive bounds
    don't raise.
    """
    times = pd.to_datetime(times)
    if times.dt.tz is None:
        return times
    return times.dt.tz_convert("UTC").dt.tz_localize(None)


def _aggregate_to_hourly(df: pd.DataFrame) -> pd.DataFrame:
    """Group rows by UTC hour and average the temp/dewpoint columns.

    Production Ecowitt is poll-cadence (~minutes); the spec aggregations
    (CDD/daily-max/nocturnal-min/mean-dewpoint) are HOURLY primitives,
    so we aggregate the sub-hourly stream first. For the Phase 0
    fixture (one row per hour) this is a no-op.
    """
    df = df.copy()
    df["_time"] = _normalize_to_naive_utc(df["_time"])
    df["_hour_utc"] = df["_time"].dt.floor("h")
    return (
        df.groupby("_hour_utc", as_index=False)[
            ["ch1_temp_f", "ch1_dewpoint_f"]
        ]
        .mean()
        .rename(columns={"_hour_utc": "_time"})
    )


def _attach_ct_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add America/Chicago bucketing columns. Input must already have
    naive-UTC ``_time``."""
    out = df.copy()
    times_aware = out["_time"].dt.tz_localize("UTC")
    times_ct = times_aware.dt.tz_convert(_CT)
    out["_time_ct"] = times_ct
    out["date_ct"] = times_ct.dt.date
    out["hour_ct"] = times_ct.dt.hour
    return out


def build_weather_vector(arm: ArmPeriod, ecowitt_df: pd.DataFrame) -> WeatherVector:
    """Aggregate Ecowitt data over the arm's 288-hour post-washout
    window. Uses ``ch1_temp_f`` (shaded outdoor) and ``ch1_dewpoint_f``.

    Handles both naive-UTC fixtures and tz-aware-UTC real-shape inputs.
    Aggregates sub-hourly cadence to hourly mean before computing spec
    §6 primitives.
    """
    start_utc, end_utc = _post_washout_utc_bounds(arm)
    df = ecowitt_df.copy()
    df["_time"] = _normalize_to_naive_utc(df["_time"])
    in_window = (df["_time"] >= start_utc) & (df["_time"] < end_utc)
    df = df.loc[in_window]
    if df.empty:
        raise ValueError(
            f"No Ecowitt rows in arm-period window [{start_utc}, {end_utc})"
        )

    df = _aggregate_to_hourly(df)
    df = _attach_ct_columns(df)

    # 1. CDD total
    hourly_cdd = (df["ch1_temp_f"] - CDD_BASE_F).clip(lower=0) / 24.0
    cdd_total = float(hourly_cdd.sum())

    # 2. mean_daily_max_temp_f
    daily_max = df.groupby("date_ct")["ch1_temp_f"].max()
    mean_daily_max = float(daily_max.mean())

    # 3. mean_nocturnal_min_temp_f. A "night N" runs 22:00 of date N
    # through 05:59 of date N+1. We label each row's night by shifting
    # the date for early-morning rows, then drop partial nights that
    # don't have BOTH a pre-midnight half and a post-midnight half
    # within the window -- otherwise the 12-day arm window produces
    # 13 named nights (one with hours 0-5 only at the window start,
    # one with hours 22-23 only at the window end), inflating spec §6
    # "mean over 12 days" by averaging 13 partials.
    mask = df["hour_ct"].isin(NOCTURNAL_HOURS_CT)
    nocturnal = df.loc[mask].copy()
    night = nocturnal.apply(
        lambda r: r["date_ct"] if r["hour_ct"] >= 22
        else r["date_ct"] - datetime.timedelta(days=1),
        axis=1,
    )
    nocturnal = nocturnal.assign(night=night)
    half = nocturnal["hour_ct"].apply(lambda h: "pre" if h >= 22 else "post")
    halves_present = (
        nocturnal.assign(half=half)
        .groupby("night")["half"]
        .agg(lambda s: set(s.unique()))
    )
    full_nights = halves_present[halves_present.apply(
        lambda s: {"pre", "post"}.issubset(s)
    )].index
    nightly_min = (
        nocturnal[nocturnal["night"].isin(full_nights)]
        .groupby("night")["ch1_temp_f"].min()
    )
    if nightly_min.empty:
        mean_nocturnal_min = float("nan")
    else:
        mean_nocturnal_min = float(nightly_min.mean())

    # 4. mean_dewpoint_f
    mean_dewpoint = float(df["ch1_dewpoint_f"].mean())

    return WeatherVector(
        cdd_total=cdd_total,
        mean_daily_max_temp_f=mean_daily_max,
        mean_nocturnal_min_temp_f=mean_nocturnal_min,
        mean_dewpoint_f=mean_dewpoint,
    )
