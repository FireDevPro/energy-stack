"""Comfort-vs-cost curve: cheapest cooling trajectory for a given indoor-temperature cap.

Two stages, so the reported cost always comes from the replay-validated simulator:

1. Plan. A linear program over the whole season, with perfect foresight of
   weather and ComEd prices, chooses delivered cooling g_k in [0, 1] each 15 min
   to minimise sum (price_k + delivery) * kW(g_k), subject to the 1R1C dynamics
   and floor <= house_f <= cap. kW(g) is the lower convex hull of the measured
   operating points (stage 1, stage 2), which is what an LP can represent.
   Cap violations are allowed but heavily penalised, so infeasible hours are
   reported rather than crashing the solve.
2. Price. The planned house-temperature path becomes a thermostat setpoint
   schedule (plan + hourly thermostat offset) and is run through
   ``counterfactual.run`` with the measured kW curve. Cost and comfort are read
   from that trace.

Perfect foresight makes this an upper bound on what any real controller
could save at a given cap.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import scipy.sparse as sp
from scipy.optimize import linprog

from . import counterfactual as cf
from .model import DT, SUBSTEPS, Params

CAP_PENALTY_USD_PER_DEGF_STEP = 5.0


def _hull(curve: cf.PowerCurve, stage1: float) -> tuple[float, float, float]:
    """(slope1, breakpoint, slope2) of the convex hull of (0,0), (stage1, kW@u=0.5), (1, kW@u=1)."""
    k1, k2 = float(curve(0.5)), float(curve(1.0))
    s_a, s_b = k1 / stage1, (k2 - k1) / (1 - stage1)
    if s_a <= s_b:  # stage 1 is the cheaper way to deliver cooling: two segments
        return s_a, stage1, s_b
    return k2, 1.0, k2  # stage 2 cycling dominates: single segment


def plan(
    p: Params, w: pd.DataFrame, start: str, stop: str, cap: float, floor: float, curve: cf.PowerCurve,
    plan_price: pd.Series | None = None, sleep_cap: float | None = None,
) -> tuple[pd.Series, dict[str, float]]:
    """``plan_price`` (c/kWh) is what the planner believes prices will be; default is the
    actual real-time price (perfect foresight). Weather is always perfect foresight."""
    d = w.loc[start:stop, ["out_f", "solar", "price_cents_per_kwh", "house_f"]].copy()
    if plan_price is not None:
        d["price_cents_per_kwh"] = plan_price.reindex(d.index)
    d[["out_f", "solar", "price_cents_per_kwh"]] = d[["out_f", "solar", "price_cents_per_kwh"]].interpolate(limit_direction="both")
    n = len(d)
    to, s = d["out_f"].to_numpy(float), d["solar"].to_numpy(float) / 1000
    price = (d["price_cents_per_kwh"].to_numpy(float) + cf.DELIVERY_CENTS_PER_KWH) / 100  # $/kWh

    # exact composition of the SUBSTEPS Euler updates for the linear 1R1C model
    h = DT / SUBSTEPS
    q = 1 - h * p.a_env
    alpha = q**SUBSTEPS
    gain = h * sum(q**i for i in range(SUBSTEPS))
    drive = gain * (p.a_env * to + p.b_air * s + p.c)
    cool = gain * p.k_cool

    slope1, bp, slope2 = _hull(curve, p.stage1_capacity)
    # variables: T[0..n] | g1[0..n-1] | g2[0..n-1] | v[0..n-1] (cap slack) | f[0..n-1] (floor slack)
    # The floor is soft: on cool nights the house drifts below it with the AC off and
    # there is no heating in this model. Cooling only makes floor slack worse, so the
    # penalty stops pre-cooling below the floor without making the LP infeasible.
    iT, ig1, ig2, iv, i_f = 0, n + 1, 2 * n + 1, 3 * n + 1, 4 * n + 1
    nv = 5 * n + 1
    c = np.zeros(nv)
    c[ig1 : ig1 + n] = price * slope1 * 0.25
    c[ig2 : ig2 + n] = price * slope2 * 0.25
    c[iv : iv + n] = CAP_PENALTY_USD_PER_DEGF_STEP
    c[i_f : i_f + n] = CAP_PENALTY_USD_PER_DEGF_STEP

    k = np.arange(n)
    # T[k+1] - alpha T[k] + cool (g1 + g2) = drive_k
    rows = np.concatenate([k, k, k, k])
    cols = np.concatenate([iT + k + 1, iT + k, ig1 + k, ig2 + k])
    vals = np.concatenate([np.ones(n), -alpha * np.ones(n), cool * np.ones(n), cool * np.ones(n)])
    a_eq = sp.csr_matrix((vals, (rows, cols)), shape=(n + 1, nv))
    a_eq = sp.vstack([a_eq[:n], sp.csr_matrix(([1.0], ([0], [iT])), shape=(1, nv))])
    t0 = float(d["house_f"].dropna().iloc[0])
    b_eq = np.r_[drive, t0]
    # T[k+1] - v[k] <= cap ;  -T[k+1] - f[k] <= -floor
    cap_rows = sp.csr_matrix((np.r_[np.ones(n), -np.ones(n)], (np.r_[k, k], np.r_[iT + k + 1, iv + k])), shape=(n, nv))
    floor_rows = sp.csr_matrix((np.r_[-np.ones(n), -np.ones(n)], (np.r_[k, k], np.r_[iT + k + 1, i_f + k])), shape=(n, nv))
    a_ub = sp.vstack([cap_rows, floor_rows])
    # cap applies to T[k+1], i.e. the end of step k
    end_hours = (d.index + pd.Timedelta(minutes=15)).hour
    caps = np.full(n, cap)
    if sleep_cap is not None:
        caps = np.where(np.isin(end_hours, cf.SLEEP_HOURS), min(cap, sleep_cap), cap)
    b_ub = np.r_[caps, np.full(n, -floor)]

    bounds = [(None, None)] * (n + 1) + [(0, bp)] * n + [(0, 1 - bp if bp < 1 else 0)] * n + [(0, None)] * (2 * n)
    res = linprog(c, A_ub=a_ub, b_ub=b_ub, A_eq=a_eq, b_eq=b_eq, bounds=bounds, method="highs")
    if res.status != 0:
        raise RuntimeError(f"LP failed at cap={cap}: {res.message}")
    x = res.x
    traj = pd.Series(x[iT + 1 : iT + n + 1], index=d.index)
    info = {"lp_cost_usd": float(c[ig1:iv] @ x[ig1:iv]), "cap_violation_degf_hours": float(x[iv : iv + n].sum() * 0.25)}
    return traj, info


def price_signals(w: pd.DataFrame, start: str, stop: str) -> dict[str, pd.Series]:
    """Price beliefs for the planner, from most to least informed.

    - perfect: actual ComEd real-time price (not achievable; upper bound)
    - day-ahead: PJM DA LMP, ComEd zone (knowable the evening before)
    - hour-of-day: median ComEd price by hour of day over the window (knows the daily
      shape, nothing about specific spikes)
    """
    d = w.loc[start:stop]
    rt = d["price_cents_per_kwh"]
    shape = rt.groupby(rt.index.hour).median()
    typical = pd.Series(shape.reindex(d.index.hour).to_numpy(), index=d.index)
    sig = {"perfect": rt}
    if "da_cents_per_kwh" in d and d["da_cents_per_kwh"].notna().mean() > 0.85:
        sig["day-ahead"] = d["da_cents_per_kwh"].fillna(typical)  # small poller gaps -> typical shape
    sig["hour-of-day"] = typical
    return sig


def cost_curve(
    p: Params, w: pd.DataFrame, start: str, stop: str, caps: list[float], floor: float,
    curve: cf.PowerCurve, offset: dict[int, float], sleep_cap: float | None = None,
) -> pd.DataFrame:
    """Cheapest cooling per daytime cap. ``sleep_cap`` holds 10pm-7am comfort fixed across
    caps, so the curve prices afternoon comfort alone; without it, cheap plans buy
    savings by running warm nights."""
    rows: dict[str, dict[str, Any]] = {}
    idx = pd.DatetimeIndex(w.loc[start:stop].index)
    hour_off = np.array([offset.get(int(hh), 0.0) for hh in idx.hour])

    prog = cf.run(p, w, start, stop, cf.program_series(idx, cf.OBSERVED_PROGRAM_F), curve, offset)
    rows["program (75 / 79 pm)"] = {"cap_f": float(prog.house_f.max()), **_metrics(prog, w)}

    for cap in caps:
        # rule-based reference: fixed schedule holding the house mean at the cap
        # (and at the sleep cap overnight, if one is set)
        fixed = np.full(len(idx), cap)
        if sleep_cap is not None:
            fixed = np.where(np.isin(idx.hour, cf.SLEEP_HOURS), min(cap, sleep_cap), cap)
        st = cf.run(p, w, start, stop, pd.Series(fixed + hour_off, index=idx), curve, offset)
        rows[f"fixed at {cap:.0f}"] = {"cap_f": cap, **_metrics(st, w)}

        for sig_name, sig in price_signals(w, start, stop).items():
            traj, info = plan(p, w, start, stop, cap, floor, curve, plan_price=sig, sleep_cap=sleep_cap)
            # small margin so the simulated thermostat tracks the plan rather than idling just above it
            opt_sp = pd.Series(traj.to_numpy() + hour_off + 0.05, index=idx)
            op = cf.run(p, w, start, stop, opt_sp, curve, offset)
            rows[f"optimized cap {cap:.0f} [{sig_name}]"] = {"cap_f": cap, **_metrics(op, w), **info}
    return pd.DataFrame(rows).T


def _metrics(tr: pd.DataFrame, w: pd.DataFrame) -> dict[str, float]:
    s = cf.summarize(tr)
    peak = tr.index.hour.isin(cf.PEAK_HOURS)
    return {
        "bill_usd": s["bill_usd"],
        "kwh": s["kwh"],
        "peak_2_7pm_kwh": s["peak_2_7pm_kwh"],
        "avg_supply_c": s["avg_price_paid_c"],
        "max_house_f": float(tr.house_f.max()),
        "p99_house_f": float(tr.house_f.quantile(0.99)),
        "afternoon_max_f_median": float(tr.loc[peak, "house_f"].resample("D").max().median()),
        "min_house_f": float(tr.house_f.min()),
        "sleep_mean_f": s["sleep_mean_f"],
    }


def derived_program(traj: pd.Series, offset: dict[int, float]) -> dict[int, float]:
    """Collapse a planned trajectory into a fixed 24-hour thermostat program.

    Median planned house temperature by hour, plus the thermostat offset. A plain
    CTK04 program can implement this; it needs no price or weather forecasts.
    """
    prof = traj.groupby(traj.index.hour).median()
    return {int(h): float(v) + offset.get(int(h), 0.0) for h, v in prof.items()}
