"""Independent validation: leave-one-month-out energy replay, peak events, pulldown rates.

Every number here is out-of-sample. For each summer month, the model
parameters, the kW curve and the thermostat offset are estimated from the
other three months only. The held-out month is then replayed under its real
setpoint history and compared with metered energy.

Caveat recorded in the doc: the *structure* choice (1R1C over 2R2C) was made
on the full-season replay, so structure selection is not held out; parameters are.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from . import counterfactual as cf
from .model import Params, fit, segments

MONTHS = [("2026-06-01", "2026-06-30"), ("2026-07-01", "2026-07-31"), ("2026-08-01", "2026-08-31"), ("2026-09-01", "2026-09-30")]
ONE_NODE = {"a_im": 0.0, "b_mass": 0.0, "r": 0.05, "k_heat": 2.0}


def lomo_replays(w: pd.DataFrame) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Replay each month with parameters fit on the other three. Returns concatenated trace + per-month stats."""
    traces, rows = [], []
    for a, b in MONTHS:
        train = [s for (x, y) in MONTHS if (x, y) != (a, b) for s in segments(w, x, y)]
        p = fit(train, fixed=ONE_NODE).params
        curve, off = cf.power_curve(w, exclude=(a, b)), cf.tstat_offset_by_hour(w, exclude=(a, b))
        tr = cf.run(p, w, a, b, w["cool_setpoint_f"], curve, off)
        m = w.loc[tr.index]
        meas_kwh = float((m["hvac_kw"] * 0.25).sum())
        meas_cost = float((m["hvac_kw"] * 0.25 * m["price_cents_per_kwh"] / 100).sum())
        dc = cf.daily_compare(tr, w)
        rows.append({
            "month": a[:7], "sim_kwh": float(tr.kwh.sum()), "meas_kwh": meas_kwh,
            "kwh_err_pct": 100 * (tr.kwh.sum() / meas_kwh - 1),
            "sim_cost": float(tr.cost_usd.sum()), "meas_cost": meas_cost,
            "daily_r": float(dc[["sim_kwh", "meas_kwh"]].corr().iloc[0, 1]),
            "indoor_rmse_f": float(((tr.house_f - m.house_f) ** 2).mean() ** 0.5),
            "a_env": p.a_env, "k_cool": p.k_cool,
        })
        traces.append(tr.assign(meas_kw=m["hvac_kw"], meas_house_f=m["house_f"], price=m["price_cents_per_kwh"]))
    return pd.concat(traces), rows


def peak_events(tr: pd.DataFrame) -> dict[str, Any]:
    """Sim vs metered on the hours that carry the money, using the held-out traces."""
    out: dict[str, Any] = {}
    day = tr.index.normalize()

    # hottest afternoons: 10 hottest days, noon-8pm
    hot_days = tr["out_f"].groupby(day).max().nlargest(10).index
    rows = []
    for d in hot_days:
        blk = tr[(day == d) & tr.index.hour.isin(range(12, 20))]
        rows.append({"day": d.strftime("%m-%d"), "out_max": float(blk.out_f.max()),
                     "sim_kwh": float(blk.kwh.sum()), "meas_kwh": float((blk.meas_kw * 0.25).sum()),
                     "sim_max_f": float(blk.house_f.max()), "meas_max_f": float(blk.meas_house_f.max())})
    out["hottest_afternoons"] = rows

    # evening recovery 7pm-midnight, all days and hot days (peak >= 89F)
    ev = tr[tr.index.hour.isin(range(19, 24))]
    evd = pd.DataFrame({"sim": ev.kwh.groupby(ev.index.normalize()).sum(),
                        "meas": (ev.meas_kw * 0.25).groupby(ev.index.normalize()).sum()})
    evd["out_max"] = tr["out_f"].groupby(day).max().reindex(evd.index)
    for name, sub in {"all_evenings": evd, "hot_evenings": evd[evd.out_max >= 89]}.items():
        out[name] = {"n": int(len(sub)), "sim_kwh": float(sub.sim.sum()), "meas_kwh": float(sub.meas.sum()),
                     "ratio": float(sub.sim.sum() / sub.meas.sum()), "daily_mae_kwh": float((sub.sim - sub.meas).abs().mean()),
                     "daily_r": float(sub[["sim", "meas"]].corr().iloc[0, 1])}

    # expensive hours: top 5% of 15-min price slots
    thr = float(tr.price.quantile(0.95))
    ex = tr[tr.price >= thr]
    out["expensive_slots"] = {"price_threshold_c": thr, "n_slots": int(len(ex)),
                              "sim_kwh": float(ex.kwh.sum()), "meas_kwh": float((ex.meas_kw * 0.25).sum()),
                              "sim_cost": float(ex.cost_usd.sum()), "meas_cost": float((ex.meas_kw * 0.25 * ex.price / 100).sum())}
    return out


def pulldown_rates(w: pd.DataFrame, p: Params) -> pd.DataFrame:
    """Measured vs modelled cooling rate during evening full-stage-2 runs (>=1 h, 7pm-2am start).

    Per sensor, the rate is the least-squares slope over the run. The model rate is the
    1R1C dT/dt evaluated at the run's mean conditions with full cooling.
    """
    d = w.loc["2026-05-27":"2026-09-30"]
    full = (d["cool"] >= 0.95).to_numpy()
    grp = np.cumsum(np.r_[True, full[1:] != full[:-1]])
    rows = []
    sensors = {"tstat_f": "thermostat", "house_f": "house mean", "ch2_temp_f": "2F hall", "ch3_temp_f": "2F S-BR", "indoor_temp_f": "1F office"}
    for g in np.unique(grp[full]):
        idx = np.where(grp == g)[0]
        if len(idx) < 4:
            continue
        blk = d.iloc[idx]
        h0 = blk.index[0].hour
        if not (h0 >= 19 or h0 < 2):
            continue
        t = (blk.index - blk.index[0]).total_seconds().to_numpy() / 3600
        r: dict[str, Any] = {"start": blk.index[0], "hours": len(idx) * 0.25, "period": "Jun (old 73F prog)" if blk.index[0] < pd.Timestamp("2026-06-22", tz=blk.index.tz) else "Jul-Sep"}
        for col, name in sensors.items():
            y = blk[col].to_numpy(float)
            ok = np.isfinite(y)
            r[name] = float(np.polyfit(t[ok], y[ok], 1)[0]) if ok.sum() >= 4 else np.nan
        ti = float(blk["house_f"].mean())
        r["model"] = p.a_env * (float(blk.out_f.mean()) - ti) + p.b_air * float(blk.solar.mean()) / 1000 + p.c - p.k_cool * 1.0
        r["out_minus_in"] = float(blk.out_f.mean()) - ti
        rows.append(r)
    return pd.DataFrame(rows)
