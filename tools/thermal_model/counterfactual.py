"""Closed-loop setpoint-schedule simulation on real weather and ComEd prices.

A simulated thermostat runs the AC just enough each 15 min to keep its own
reading at or below the cool setpoint. Its reading is the modelled house mean
plus the measured thermostat-vs-house offset for that hour of day. Delivered
cooling comes from the fitted model; electrical kW comes from the measured
ComfortNet-% -> Refoss-kW curve. Cost is ComEd hourly-pricing supply only.

Validate first with ``replay`` (the actual setpoint history) before trusting
any scenario: it shows whether the simulated thermostat uses about as much
energy as the real one did.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .model import Params, cool_capacity_inverse, step

# Cool-setpoint program the CTK04 actually ran Jul-Sep 2026, as the hourly median
# of hvac.thermostat cool_setpoint_f (deg C half-steps converted to deg F).
OBSERVED_PROGRAM_F: dict[int, float] = {
    **{h: 75.2 for h in range(0, 13)},
    13: 77.9,
    **{h: 78.8 for h in range(14, 19)},
    19: 77.9,
    20: 76.1,
    21: 76.1,
    22: 75.2,
    23: 75.2,
}

SCENARIOS: dict[str, dict[int, float]] = {
    "program (75 / 79 pm setback)": OBSERVED_PROGRAM_F,
    "steady 75": {h: 75.2 for h in range(24)},
    "steady 76": {h: 76.1 for h in range(24)},
    "steady 77": {h: 77.0 for h in range(24)},
}

def three_period_program(night_c: float, precool_c: float, precool_start: int, coast_c: float,
                         coast_start: int = 13, night_start: int = 22) -> dict[int, float]:
    """CTK04-style program in deg C half-steps: night (from 10pm), pre-cool, coast (from 1pm). Returns deg F by hour."""
    def f(c: float) -> float:
        return c * 9 / 5 + 32
    return {h: f(night_c) if (h >= night_start or h < precool_start) else f(precool_c) if h < coast_start else f(coast_c)
            for h in range(24)}


THREE_PERIOD_PROGRAMS: dict[str, dict[int, float]] = {
    "24.0 | 22.0 from 3am | 24.5 from 1pm": three_period_program(24.0, 22.0, 3, 24.5),
    "24.0 | 22.5 from 3am | 25.0 from 1pm": three_period_program(24.0, 22.5, 3, 25.0),
    "24.0 | 22.5 from 3am | 24.5 from 1pm": three_period_program(24.0, 22.5, 3, 24.5),
    "24.0 | 23.0 from 3am | 24.5 from 1pm": three_period_program(24.0, 23.0, 3, 24.5),
    "24.0 | 22.5 from 1am | 24.5 from 1pm": three_period_program(24.0, 22.5, 1, 24.5),
    "24.0 | 22.5 from 4am | 24.5 from 1pm": three_period_program(24.0, 22.5, 4, 24.5),
}

PEAK_HOURS = range(14, 19)  # 2-7pm local
DTOD_PEAK_HOURS = range(13, 19)  # ComEd DTOD Mid-Day Peak, 1-7pm

# ComEd DTOD (Delivery Time-of-Day) Distribution Facility Charge plus the other
# per-kWh bill items, by billing period, from the ingested bills
# (comed.bill_lineitems). Units are c/kWh. Windows are local clock hours:
# Overnight 9pm-6am, Morning 6am-1pm, Mid-Day Peak 1-7pm, Evening 7-9pm.
# "flat" = IL Electricity Distribution + Transmission Services + Carbon-Free
# Energy Resource Adj. These are time-invariant within a bill; the CFE credit
# moves month to month. Excluded: fixed charges (customer, metering), the
# Capacity Charge ($/kW-month PLC, set by last summer's 5CP), and the lump
# Purchased Electricity Adjustment.
DTOD_PERIODS: tuple[tuple[str, dict[str, float]], ...] = (
    ("2026-05-25", {"overnight": 3.345, "morning": 4.475, "midday": 11.852, "evening": 4.185, "flat": 0.128 + 1.074 - 1.344}),
    ("2026-06-23", {"overnight": 3.345, "morning": 4.475, "midday": 11.852, "evening": 4.185, "flat": 0.128 + 1.074 - 1.228}),
    ("2026-07-23", {"overnight": 3.345, "morning": 4.475, "midday": 11.852, "evening": 4.185, "flat": 0.128 + 1.074 - 0.690}),
    ("2026-08-21", {"overnight": 3.349, "morning": 4.479, "midday": 11.863, "evening": 4.189, "flat": 0.128 + 1.073 - 1.628}),
    ("2026-09-23", {"overnight": 3.349, "morning": 4.479, "midday": 11.863, "evening": 4.189, "flat": 0.128 + 1.075 - 0.115}),
)


def _dtod_window(hour: int) -> str:
    if 13 <= hour < 19:
        return "midday"
    if 6 <= hour < 13:
        return "morning"
    if 19 <= hour < 21:
        return "evening"
    return "overnight"


def delivery_cents_per_kwh(index: pd.DatetimeIndex) -> np.ndarray:
    """Per-kWh delivery + flat bill items (c/kWh) at each timestamp, per DTOD window and bill period."""
    starts = pd.DatetimeIndex([pd.Timestamp(d, tz=index.tz) for d, _ in DTOD_PERIODS])
    period = np.clip(np.searchsorted(starts, index, side="right") - 1, 0, len(DTOD_PERIODS) - 1)
    out = np.empty(len(index))
    for i, (h, per) in enumerate(zip(index.hour, period, strict=True)):
        rates = DTOD_PERIODS[per][1]
        out[i] = rates[_dtod_window(int(h))] + rates["flat"]
    return out


SLEEP_HOURS = (*range(22, 24), *range(0, 7))


@dataclass
class PowerCurve:
    u: np.ndarray
    kw: np.ndarray

    def __call__(self, u: np.ndarray | float) -> np.ndarray:
        return np.interp(u, self.u, self.kw)


def _without(w: pd.DataFrame, start: str, stop: str, exclude: tuple[str, str] | None) -> pd.DataFrame:
    d = w.loc[start:stop]
    if exclude is not None:
        d = d[(d.index < pd.Timestamp(exclude[0], tz=d.index.tz)) | (d.index >= pd.Timestamp(exclude[1], tz=d.index.tz) + pd.Timedelta(days=1))]
    return d


def power_curve(w: pd.DataFrame, exclude: tuple[str, str] | None = None) -> PowerCurve:
    """Binned mean HVAC kW vs ComfortNet cool fraction (Jun-Sep, minus ``exclude``), anchored at (0, 0)."""
    d = _without(w, "2026-06-01", "2026-09-30", exclude)[["cool", "hvac_kw"]].dropna()
    d = d[d["cool"] > 0.01]
    bins = pd.cut(d["cool"], [0.01, 0.15, 0.3, 0.45, 0.55, 0.7, 0.85, 0.95, 1.0])
    g = d.groupby(bins, observed=True).mean()
    return PowerCurve(np.r_[0.0, g["cool"].to_numpy()], np.r_[0.0, g["hvac_kw"].to_numpy()])


def tstat_offset_by_hour(w: pd.DataFrame, exclude: tuple[str, str] | None = None) -> dict[int, float]:
    """Mean (thermostat reading - house mean) by hour of day, after the TCC cutover (minus ``exclude``)."""
    d = _without(w, "2026-06-25", "2026-09-30", exclude)
    d = (d["tstat_f"] - d["house_f"]).dropna()
    return {int(h): float(v) for h, v in d.groupby(d.index.hour).mean().items()}


def run(
    p: Params,
    w: pd.DataFrame,
    start: str,
    stop: str,
    setpoint_f: pd.Series,
    curve: PowerCurve,
    offset: dict[int, float],
) -> pd.DataFrame:
    """Continuous closed-loop run over [start, stop]. Returns per-step trace."""
    d = w.loc[start:stop, ["out_f", "solar", "price_cents_per_kwh", "house_f"]].copy()
    d[["out_f", "solar", "price_cents_per_kwh"]] = d[["out_f", "solar", "price_cents_per_kwh"]].interpolate(limit_direction="both")
    sp = setpoint_f.reindex(d.index).ffill().bfill().to_numpy(float)
    hours = d.index.hour.to_numpy()
    to, s = d["out_f"].to_numpy(float), d["solar"].to_numpy(float) / 1000.0

    ti = float(d["house_f"].dropna().iloc[0])
    tm = float(w["house_f"].loc[:start].tail(96).mean())
    n = len(d)
    ti_tr, g_tr = np.empty(n), np.empty(n)
    for k in range(n):
        off = offset.get(int(hours[k]), 0.0)
        free_ti, _ = step(p, ti, tm, to[k], s[k], 0.0, 0.0)
        full_ti, _ = step(p, ti, tm, to[k], s[k], 1.0, 0.0)
        span = free_ti - full_ti
        g = float(np.clip((free_ti + off - sp[k]) / span, 0.0, 1.0)) if span > 0 else 0.0
        ti, tm = step(p, ti, tm, to[k], s[k], g, 0.0)
        ti_tr[k], g_tr[k] = ti, g
    u = cool_capacity_inverse(g_tr, p.stage1_capacity)
    kw = curve(u)
    return pd.DataFrame(
        {
            "house_f": ti_tr,
            "tstat_f": ti_tr + np.array([offset.get(int(h), 0.0) for h in hours]),
            "setpoint_f": sp,
            "u": u,
            "kw": kw,
            "kwh": kw * 0.25,
            "cost_usd": kw * 0.25 * d["price_cents_per_kwh"].to_numpy(float) / 100,
            "delivery_usd": kw * 0.25 * delivery_cents_per_kwh(pd.DatetimeIndex(d.index)) / 100,
            "out_f": to,
        },
        index=d.index,
    )


def summarize(tr: pd.DataFrame) -> dict[str, float]:
    peak = tr.index.hour.isin(PEAK_HOURS)
    sleep = tr.index.hour.isin(SLEEP_HOURS)
    return {
        "kwh": float(tr["kwh"].sum()),
        "cost_usd": float(tr["cost_usd"].sum()),
        "delivery_usd": float(tr["delivery_usd"].sum()),
        "bill_usd": float(tr["cost_usd"].sum() + tr["delivery_usd"].sum()),
        "peak_2_7pm_kwh": float(tr.loc[peak, "kwh"].sum()),
        "dtod_peak_1_7pm_kwh": float(tr.loc[tr.index.hour.isin(DTOD_PEAK_HOURS), "kwh"].sum()),
        "avg_price_paid_c": float(100 * tr["cost_usd"].sum() / tr["kwh"].sum()) if tr["kwh"].sum() else 0.0,
        "sleep_mean_f": float(tr.loc[sleep, "house_f"].mean()),
        "afternoon_max_f_median": float(tr.loc[peak, "house_f"].resample("D").max().median()),
        "hours_above_78": float((tr["house_f"] > 78).sum() * 0.25),
    }


def daily_compare(sim: pd.DataFrame, w: pd.DataFrame) -> pd.DataFrame:
    meas = w.loc[sim.index, "hvac_kw"] * 0.25
    return pd.DataFrame({"sim_kwh": sim["kwh"].resample("D").sum(), "meas_kwh": meas.resample("D").sum(), "out_max": sim["out_f"].resample("D").max()})


def program_series(index: pd.DatetimeIndex, program: dict[int, float]) -> pd.Series:
    return pd.Series([program[int(h)] for h in index.hour], index=index)


def excursions(tr: pd.DataFrame) -> dict[str, float]:
    """Comfort excursions of the house mean, in hours, plus period means."""
    h = tr.index.hour
    q = 0.25
    return {
        "h_above_78": float((tr.house_f > 78).sum() * q),
        "h_above_80": float((tr.house_f > 80).sum() * q),
        "h_above_82": float((tr.house_f > 82).sum() * q),
        "h_below_72": float((tr.house_f < 72).sum() * q),
        "h_below_70": float((tr.house_f < 70).sum() * q),
        "max_f": float(tr.house_f.max()),
        "min_f": float(tr.house_f.min()),
        "morning_6_12_mean_f": float(tr.house_f[(h >= 6) & (h < 12)].mean()),
        "afternoon_max_f_median": float(tr.house_f[(h >= 13) & (h < 19)].resample("D").max().median()),
        "evening_19_22_mean_f": float(tr.house_f[(h >= 19) & (h < 22)].mean()),
        "bedtime_22_mean_f": float(tr.house_f[h == 22].mean()),
        "sleep_mean_f": float(tr.house_f[np.isin(h, SLEEP_HOURS)].mean()),
    }
