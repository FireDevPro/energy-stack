"""Eagle coverage helper per
docs/plans/sced-rebaseline-spec-2026-05-13.md §10.

For bill-reconciliation provenance: report what fraction of a bill
period's hours have at least one Eagle delivered-kWh observation. The
caller falls back to Refoss mains for hours not covered by Eagle.

Real-shape input:
- ``eagle_df`` columns include ``_time`` and ``_value`` (delivered_kwh
  totalizer). The function only cares whether a row exists in each
  hour; it does not validate totalizer monotonicity (that lives in the
  reconciliation step proper).
- ``_time`` may be naive UTC (fixture) or tz-aware UTC (production).
"""
from __future__ import annotations

import datetime

import pandas as pd


def eagle_coverage_pct(
    eagle_df: pd.DataFrame,
    start_utc: datetime.datetime,
    end_utc: datetime.datetime,
) -> float:
    """Return percentage [0, 100] of hours in [start_utc, end_utc) where
    Eagle has at least one observation.

    Empty interval (start >= end) -> 0.0.
    """
    if start_utc >= end_utc:
        return 0.0
    total_hours = int((end_utc - start_utc).total_seconds() // 3600)
    if total_hours <= 0:
        return 0.0
    if eagle_df.empty:
        return 0.0
    df = eagle_df.copy()
    times = pd.to_datetime(df["_time"])
    if times.dt.tz is not None:
        times = times.dt.tz_convert("UTC").dt.tz_localize(None)
    df["_time"] = times
    in_window = (df["_time"] >= start_utc) & (df["_time"] < end_utc)
    df = df.loc[in_window]
    if df.empty:
        return 0.0
    covered_hours = df["_time"].dt.floor("h").nunique()
    return float(min(covered_hours, total_hours) / total_hours * 100.0)
