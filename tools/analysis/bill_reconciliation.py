"""Bill reconciliation reconstruction per
docs/plans/sced-rebaseline-spec-2026-05-13.md §10.

Source priority: Eagle smart-meter delivered-kWh is canonical (closest
to the ComEd revenue meter); Refoss mains ``em:1 + em:7`` is the
fallback for Eagle-gap hours. NEVER silently rescale HVAC outcomes -
divergence is reported, not corrected.

Divergence threshold (spec §10): flag if reconstructed monthly
variable charges differ from bill variable charges by more than
``max(5%, $10)``.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Sequence

import pandas as pd

from tools.analysis.dtod_rates import dtod_total_delivery_c_per_kwh
from tools.analysis.hvac_dollars import HourlyRateInputs, hourly_rate_c_per_kwh


DIVERGENCE_PCT_FLOOR = 0.05  # 5%
DIVERGENCE_DOLLAR_FLOOR = 10.0  # $10 absolute


@dataclass(frozen=True)
class BillReconciliation:
    """Per-bill-period reconciliation result. See spec §10 for semantics."""
    bill_period_start_utc: datetime.datetime
    bill_period_end_utc: datetime.datetime
    reconstructed_variable_dollars: float
    actual_bill_variable_dollars: float
    divergence_dollars: float
    divergence_pct: float
    divergence_flagged: bool
    pct_hours_eagle: float
    pct_hours_refoss_fallback: float
    eagle_refoss_drift_during_overlap_kwh: float


def _hourly_kwh_from_eagle_totalizer(
    eagle_df: pd.DataFrame,
    start_utc: datetime.datetime,
    end_utc: datetime.datetime,
) -> dict[datetime.datetime, float]:
    """Derive per-hour kWh from an Eagle delivered-kWh totalizer column.

    Returns ``{hour_start_utc: kwh_in_hour}``. An hour with no Eagle row
    is omitted (caller falls back to Refoss). Per-hour kWh = (last
    totalizer in hour) - (last totalizer in previous hour). Negative
    deltas (totalizer reset / restart) yield ``0.0`` and are surfaced
    as a per-hour zero so the bill reconstruction does not turn a meter
    rollover into negative consumption.
    """
    if eagle_df.empty:
        return {}
    df = eagle_df.copy()
    df["_time"] = pd.to_datetime(df["_time"])
    if df["_time"].dt.tz is not None:
        df["_time"] = df["_time"].dt.tz_convert("UTC").dt.tz_localize(None)
    df = df[(df["_time"] >= start_utc) & (df["_time"] < end_utc)]
    if df.empty:
        return {}
    # Take the last reading in each UTC hour as the totalizer for that hour.
    df["_hour"] = df["_time"].dt.floor("h")
    df = df.sort_values("_time")
    last_per_hour = df.groupby("_hour", as_index=False).last()
    last_per_hour = last_per_hour.sort_values("_hour").reset_index(drop=True)
    if "delivered_kwh" in last_per_hour.columns:
        totalizer = last_per_hour["delivered_kwh"]
    elif "_field" in last_per_hour.columns:
        # Long-form _field/_value Influx shape
        only_delivered = last_per_hour[last_per_hour["_field"] == "delivered_kwh"]
        last_per_hour = only_delivered.copy()
        totalizer = last_per_hour["_value"]
    else:
        totalizer = last_per_hour["_value"]
    deltas = totalizer.diff().fillna(0.0).clip(lower=0.0)
    return {
        ts: float(d) for ts, d in zip(last_per_hour["_hour"], deltas)
    }


def _hourly_kwh_from_refoss_mains(
    refoss_df: pd.DataFrame,
    start_utc: datetime.datetime,
    end_utc: datetime.datetime,
) -> dict[datetime.datetime, float]:
    """Per-hour Refoss-mains kWh = mean(em:1 + em:7 power_w) * 1h / 1000."""
    df = refoss_df
    if "_field" in df.columns:
        df = df[df["_field"] == "power_w"]
    df = df[df["channel"].isin(("em:1", "em:7"))]
    if df.empty:
        return {}
    df = df.copy()
    df["_time"] = pd.to_datetime(df["_time"])
    if df["_time"].dt.tz is not None:
        df["_time"] = df["_time"].dt.tz_convert("UTC").dt.tz_localize(None)
    df = df[(df["_time"] >= start_utc) & (df["_time"] < end_utc)]
    if df.empty:
        return {}
    df["_hour"] = df["_time"].dt.floor("h")
    hourly_w = (
        df.groupby(["_hour", "channel"])["_value"].mean()
        .groupby(level=0).sum()
    )
    return {ts: float(w) / 1000.0 for ts, w in hourly_w.items()}


def _hourly_rate_lookup(
    rt_hrl_lmps_df: pd.DataFrame,
    rate_snapshot: dict[str, float],
    hours: Sequence[datetime.datetime],
) -> dict[datetime.datetime, float]:
    """Build a c/kWh lookup at each hour. Missing LMP -> raise."""
    from zoneinfo import ZoneInfo
    ct = ZoneInfo("America/Chicago")
    utc = ZoneInfo("UTC")
    df = rt_hrl_lmps_df.copy()
    df["_time"] = pd.to_datetime(df["_time"])
    if df["_time"].dt.tz is not None:
        df["_time"] = df["_time"].dt.tz_convert("UTC").dt.tz_localize(None)
    lookup = df.set_index("_time")["total_lmp_rt"]
    out: dict[datetime.datetime, float] = {}
    for h in hours:
        if h not in lookup.index:
            raise ValueError(
                f"Missing rt_hrl_lmps for hour {h} during bill reconciliation"
            )
        lmp = float(lookup.loc[h])
        ct_hour = h.replace(tzinfo=utc).astimezone(ct).hour
        inputs = HourlyRateInputs(
            rt_hrl_lmps_per_mwh=lmp,
            pea_c_per_kwh=rate_snapshot["pea_c_per_kwh"],
            transmission_c_per_kwh=rate_snapshot["transmission_c_per_kwh"],
            misc_procurement_c_per_kwh=rate_snapshot["misc_procurement_c_per_kwh"],
            dtod_total_delivery_c_per_kwh=dtod_total_delivery_c_per_kwh(ct_hour),
            variable_riders_c_per_kwh=rate_snapshot["variable_riders_c_per_kwh"],
            carbon_free_credit_c_per_kwh=rate_snapshot["carbon_free_credit_c_per_kwh"],
        )
        out[h] = hourly_rate_c_per_kwh(inputs)
    return out


def reconcile_bill_period(
    *,
    bill_period_start_utc: datetime.datetime,
    bill_period_end_utc: datetime.datetime,
    eagle_df: pd.DataFrame,
    refoss_df: pd.DataFrame,
    rt_hrl_lmps_df: pd.DataFrame,
    rate_snapshot: dict[str, float],
    actual_bill_variable_dollars: float,
) -> BillReconciliation:
    """Reconstruct the variable-charge portion of one bill period and
    compare to the actual bill total. Per spec §10 divergence is
    reported only; no HVAC outcome rescaling.
    """
    if bill_period_end_utc <= bill_period_start_utc:
        raise ValueError("bill_period_end_utc must be > bill_period_start_utc")

    total_hours = int(
        (bill_period_end_utc - bill_period_start_utc).total_seconds() // 3600
    )
    eagle_kwh = _hourly_kwh_from_eagle_totalizer(
        eagle_df, bill_period_start_utc, bill_period_end_utc,
    )
    refoss_kwh = _hourly_kwh_from_refoss_mains(
        refoss_df, bill_period_start_utc, bill_period_end_utc,
    )

    # Per-hour kWh: Eagle primary, Refoss fallback.
    hour_list = [
        bill_period_start_utc + datetime.timedelta(hours=k)
        for k in range(total_hours)
    ]
    rate_lookup = _hourly_rate_lookup(rt_hrl_lmps_df, rate_snapshot, hour_list)
    reconstructed_cents = 0.0
    n_eagle = 0
    n_fallback = 0
    overlap_diffs: list[float] = []
    for h in hour_list:
        e = eagle_kwh.get(h)
        r = refoss_kwh.get(h)
        if e is not None and r is not None:
            overlap_diffs.append(abs(e - r))
        if e is not None:
            kwh = e
            n_eagle += 1
        elif r is not None:
            kwh = r
            n_fallback += 1
        else:
            kwh = 0.0
        reconstructed_cents += kwh * rate_lookup[h]

    reconstructed_dollars = reconstructed_cents / 100.0
    divergence_dollars = reconstructed_dollars - actual_bill_variable_dollars
    if actual_bill_variable_dollars > 0:
        divergence_pct = (
            abs(divergence_dollars) / actual_bill_variable_dollars
        )
    else:
        divergence_pct = 0.0
    divergence_flagged = (
        abs(divergence_dollars)
        > max(DIVERGENCE_DOLLAR_FLOOR,
              actual_bill_variable_dollars * DIVERGENCE_PCT_FLOOR)
    )

    pct_eagle = (n_eagle / total_hours * 100.0) if total_hours else 0.0
    pct_fallback = (n_fallback / total_hours * 100.0) if total_hours else 0.0
    drift = (
        sum(overlap_diffs) / len(overlap_diffs)
        if overlap_diffs else 0.0
    )

    return BillReconciliation(
        bill_period_start_utc=bill_period_start_utc,
        bill_period_end_utc=bill_period_end_utc,
        reconstructed_variable_dollars=reconstructed_dollars,
        actual_bill_variable_dollars=actual_bill_variable_dollars,
        divergence_dollars=divergence_dollars,
        divergence_pct=divergence_pct,
        divergence_flagged=divergence_flagged,
        pct_hours_eagle=pct_eagle,
        pct_hours_refoss_fallback=pct_fallback,
        eagle_refoss_drift_during_overlap_kwh=drift,
    )
