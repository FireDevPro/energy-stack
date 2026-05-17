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


def _attach_ct_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Localize naive-UTC ``_time`` to America/Chicago and add CT
    bucketing columns the aggregators need.
    """
    out = df.copy()
    times = pd.to_datetime(out["_time"])
    # Localize as UTC (whether arriving naive or tz-aware UTC works the same
    # via the .dt.tz_localize/.dt.tz_convert pair, but only naive needs
    # localize).
    if times.dt.tz is None:
        times = times.dt.tz_localize("UTC")
    else:
        times = times.dt.tz_convert("UTC")
    times_ct = times.dt.tz_convert(_CT)
    out["_time_ct"] = times_ct
    out["date_ct"] = times_ct.dt.date
    out["hour_ct"] = times_ct.dt.hour
    return out


def build_weather_vector(arm: ArmPeriod, ecowitt_df: pd.DataFrame) -> WeatherVector:
    """Aggregate Ecowitt data over the arm's 288-hour post-washout
    window. Uses ``ch1_temp_f`` (shaded outdoor) and ``ch1_dewpoint_f``.
    """
    start_utc, end_utc = _post_washout_utc_bounds(arm)
    in_window = (ecowitt_df["_time"] >= start_utc) & (ecowitt_df["_time"] < end_utc)
    df = ecowitt_df.loc[in_window].copy()
    if df.empty:
        raise ValueError(
            f"No Ecowitt rows in arm-period window [{start_utc}, {end_utc})"
        )

    df = _attach_ct_columns(df)

    # 1. CDD total
    hourly_cdd = (df["ch1_temp_f"] - CDD_BASE_F).clip(lower=0) / 24.0
    cdd_total = float(hourly_cdd.sum())

    # 2. mean_daily_max_temp_f
    daily_max = df.groupby("date_ct")["ch1_temp_f"].max()
    mean_daily_max = float(daily_max.mean())

    # 3. mean_nocturnal_min_temp_f. A "night N" runs 22:00 of date N
    # through 05:59 of date N+1. We label each row's night by shifting
    # the date for early-morning rows.
    mask = df["hour_ct"].isin(NOCTURNAL_HOURS_CT)
    nocturnal = df.loc[mask].copy()
    night = nocturnal.apply(
        lambda r: r["date_ct"] if r["hour_ct"] >= 22
        else r["date_ct"] - datetime.timedelta(days=1),
        axis=1,
    )
    nocturnal = nocturnal.assign(night=night)
    nightly_min = nocturnal.groupby("night")["ch1_temp_f"].min()
    mean_nocturnal_min = float(nightly_min.mean())

    # 4. mean_dewpoint_f
    mean_dewpoint = float(df["ch1_dewpoint_f"].mean())

    return WeatherVector(
        cdd_total=cdd_total,
        mean_daily_max_temp_f=mean_daily_max,
        mean_nocturnal_min_temp_f=mean_nocturnal_min,
        mean_dewpoint_f=mean_dewpoint,
    )
