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

PEAK_HOURS = range(14, 19)  # 2-7pm local
# Per-kWh delivery adder. 2026 ComEd bills (comed.bill) run 7.3-7.7 c/kWh of
# delivery_total / kwh, which includes the fixed customer charge, so the true
# marginal rate is a little lower. Supply cost uses the actual hourly prices.
DELIVERY_CENTS_PER_KWH = 7.0
SLEEP_HOURS = (*range(22, 24), *range(0, 7))


@dataclass
class PowerCurve:
    u: np.ndarray
    kw: np.ndarray

    def __call__(self, u: np.ndarray | float) -> np.ndarray:
        return np.interp(u, self.u, self.kw)


def power_curve(w: pd.DataFrame) -> PowerCurve:
    """Binned mean HVAC kW vs ComfortNet cool fraction (Jun-Sep), anchored at (0, 0)."""
    d = w.loc["2026-06-01":"2026-09-30", ["cool", "hvac_kw"]].dropna()
    d = d[d["cool"] > 0.01]
    bins = pd.cut(d["cool"], [0.01, 0.15, 0.3, 0.45, 0.55, 0.7, 0.85, 0.95, 1.0])
    g = d.groupby(bins, observed=True).mean()
    return PowerCurve(np.r_[0.0, g["cool"].to_numpy()], np.r_[0.0, g["hvac_kw"].to_numpy()])


def tstat_offset_by_hour(w: pd.DataFrame) -> dict[int, float]:
    """Mean (thermostat reading - house mean) by hour of day, after the TCC cutover."""
    d = (w["tstat_f"] - w["house_f"]).loc["2026-06-25":"2026-09-30"].dropna()
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
        "bill_usd": float(tr["cost_usd"].sum() + tr["kwh"].sum() * DELIVERY_CENTS_PER_KWH / 100),
        "peak_2_7pm_kwh": float(tr.loc[peak, "kwh"].sum()),
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
