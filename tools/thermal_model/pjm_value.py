"""What PJM day-ahead and day-of information would have changed (summer 2026).

Two questions:

1. Predictability. Could information available in advance have flagged the
   days the spike controller ended up fighting? Predictors, with when each is
   actually known:
   - day-ahead LMP, ComEd zone, max over 12-9pm (published ~1:30pm the day before)
   - ComEd-zone 7-day peak load forecast, latest issue by 6pm the day before (noon issue)
   - PJM RTO daily peak forecast, issued ~5-7am the same day
   Scored against afternoon spike hours from the controller emulator.

2. Value. Forecast-triggered strategies run with the controller still on:
   keep the current program on normal days, and switch to a pre-cool program
   only on flagged days. Each is costed (supply + DTOD), comfort-scored, and
   checked for HVAC load during the estimated 2026 5CP hours, which set next
   year's capacity PLC.

The 2026 5CP hours are *estimated* as the five highest RTO daily-peak hours
(Jun-Sep, one per day) from pjm.metered_load. PJM's official list was not yet
in Influx on 2026-11-19.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from . import controller_sim as ctl
from . import counterfactual as cf
from . import data
from .model import Params

WINDOW = ("2026-06-01", "2026-09-30")
CAPACITY_USD_PER_KW_MONTH = 10.11236  # Capacity Charge rate on the Jun-Sep 2026 bills
PLC_KW_NOW = 7.01  # set by the 2025 5CP hours

FLAGS: dict[str, tuple[str, float, str]] = {
    # name: (predictor column, threshold, when known)
    "day-ahead LMP >= 7.5c": ("da_pm_max", 7.5, "day before ~1:30pm"),
    "day-ahead LMP >= 10c": ("da_pm_max", 10.0, "day before ~1:30pm"),
    "ComEd load fcst >= 17 GW": ("comed_fc_eve", 17000.0, "day before (noon issue)"),
    "ComEd load fcst >= 19 GW": ("comed_fc_eve", 19000.0, "day before (noon issue)"),
    "RTO peak fcst >= 140 GW": ("rto_fc_morn", 140000.0, "same day ~5-7am"),
    "RTO peak fcst >= 150 GW": ("rto_fc_morn", 150000.0, "same day ~5-7am"),
}


def precool_program(start_hour: int) -> dict[int, float]:
    """24.0C night | 23.0C pre-cool from ``start_hour`` | 25.5C coast 1-10pm (the four-way candidate)."""
    return cf.three_period_program(24.0, 23.0, start_hour, 25.5)


def cp_program(start_hour: int) -> dict[int, float]:
    """Capacity-peak protocol: deeper pre-cool, AC effectively off 1-7pm, then recover.
    24.0 night | 22.5 from ``start_hour`` | 26.5 from 1pm | 25.0 from 7pm | 24.0 from 10pm."""
    def f(c: float) -> float:
        return c * 9 / 5 + 32
    out = {}
    for h in range(24):
        c = 24.0 if (h >= 22 or h < start_hour) else 22.5 if h < 13 else 26.5 if h < 19 else 25.0
        out[h] = f(c)
    return out


def _known_hour(when: str) -> int:
    return 7 if when.startswith("same day") else 3


def daily_predictors(w: pd.DataFrame, tier_5min: pd.Series) -> pd.DataFrame:
    s, e = WINDOW
    days = pd.date_range(s, e, freq="D", tz=w.index.tz)
    d = pd.DataFrame(index=days)
    pm = tier_5min.index.hour.isin(range(12, 21))
    d["spike_pm_h"] = ((tier_5min > 0) & pm).groupby(tier_5min.index.normalize()).sum().reindex(days).fillna(0) / 12
    d["scarcity_h"] = (tier_5min == 2).groupby(tier_5min.index.normalize()).sum().reindex(days).fillna(0) / 12
    da = w["da_cents_per_kwh"].loc[s:e]
    da_pm = da[da.index.hour.isin(range(12, 21))]
    d["da_pm_max"] = da_pm.groupby(da_pm.index.normalize()).max().reindex(days)
    out = w["out_f"].loc[s:e]
    d["out_max"] = out.groupby(out.index.normalize()).max().reindex(days)

    lf = load_forecasts()
    lf["day"] = lf["_time"].dt.normalize()
    def fc_peak(day: pd.Timestamp, cutoff: pd.Timestamp) -> float:
        sub = lf[(lf.day == day) & (lf.issued <= cutoff)]
        if sub.empty:
            return float("nan")
        return float(sub[sub.issued == sub.issued.max()]["_value"].max())
    d["comed_fc_eve"] = [fc_peak(x, x - pd.Timedelta(hours=6)) for x in days]

    pf = peak_forecasts()
    pfd = pf.groupby(pf["_time"].dt.normalize())["fc"].max()
    d["rto_fc_morn"] = pfd.reindex(days)
    return d


def load_forecasts() -> pd.DataFrame:
    lf = data.raw_series("pjm_load_forecast", '''from(bucket: "energy") |> range(start: 2026-05-31T00:00:00Z, stop: 2026-10-01T06:00:00Z)
  |> filter(fn: (r) => r._measurement == "pjm.load_forecast" and r._field == "forecast_load_mw")
  |> keep(columns: ["_time", "_value", "evaluated_at_iso", "forecast_area"])''')
    lf["issued"] = pd.to_datetime(lf["evaluated_at_iso"], utc=True).dt.tz_convert(data.TZ)
    return lf


def peak_forecasts() -> pd.DataFrame:
    pf = data.raw_series("pjm_peak_forecast", '''from(bucket: "energy") |> range(start: 2026-05-31T00:00:00Z, stop: 2026-10-01T06:00:00Z)
  |> filter(fn: (r) => r._measurement == "pjm.peak_forecast_rto" and (r._field == "load_forecast_mw" or r._field == "projected_peak_datetime_ept"))
  |> map(fn: (r) => ({r with _value: string(v: r._value)}))
  |> group() |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> keep(columns: ["_time", "load_forecast_mw", "projected_peak_datetime_ept"])''')
    pf["fc"] = pd.to_numeric(pf["load_forecast_mw"], errors="coerce")
    return pf.dropna(subset=["fc"])


def estimated_5cp() -> pd.DataFrame:
    """Five highest RTO daily-peak hours, Jun-Sep 2026, one per day (local hour-beginning)."""
    ml = data.raw_series("pjm_metered_load", '''from(bucket: "energy") |> range(start: 2026-05-31T00:00:00Z, stop: 2026-10-01T06:00:00Z)
  |> filter(fn: (r) => r._measurement == "pjm.metered_load" and r._field == "mw")
  |> group(columns: ["zone", "_time"]) |> max() |> group()
  |> keep(columns: ["_time", "zone", "_value"])''')
    rto = ml[ml.zone == "RTO"].set_index("_time")["_value"].sort_index().loc[WINDOW[0]:WINDOW[1]]
    daily = rto.groupby(rto.index.normalize()).agg(["max", "idxmax"])
    top = daily.sort_values("max", ascending=False).head(5)
    return pd.DataFrame({"hour": top["idxmax"].to_numpy(), "rto_mw": top["max"].to_numpy()})


def house_mains_hourly() -> pd.Series:
    h = data.raw_series("house_mains_hourly", '''from(bucket: "energy-longterm") |> range(start: 2026-05-31T00:00:00Z, stop: 2026-10-01T06:00:00Z)
  |> filter(fn: (r) => r._measurement == "refoss.channel" and r._field == "power_w" and (r.channel == "em:1" or r.channel == "em:7"))
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false, timeSrc: "_start")
  |> group(columns: ["_time"]) |> sum() |> group()
  |> keep(columns: ["_time", "_value"])''')
    return h.set_index("_time")["_value"].sort_index() / 1000


def score_flags(d: pd.DataFrame) -> pd.DataFrame:
    rows = {}
    spike = d["spike_pm_h"] > 0
    big = d["spike_pm_h"] >= d["spike_pm_h"].quantile(0.9)
    for name, (col, thr, when) in FLAGS.items():
        f = d[col] >= thr
        rows[name] = {
            "known": when, "flagged_days": int(f.sum()),
            "precision_any_spike": float((f & spike).sum() / max(int(f.sum()), 1)),
            "share_of_spike_hours": float(d["spike_pm_h"][f].sum() / d["spike_pm_h"].sum()),
            "top10pct_days_caught": float((f & big).sum() / big.sum()),
            "days_with_no_spike": int((f & ~spike).sum()),
        }
    return pd.DataFrame(rows).T


def strategies(d: pd.DataFrame) -> dict[str, dict[pd.Timestamp, dict[int, float]] | None]:
    out: dict[str, dict[pd.Timestamp, dict[int, float]] | None] = {
        "current program (every day)": {},
        "23.0/25.5 pre-cool (every day)": {x: precool_program(3) for x in d.index},
    }
    for name, (col, thr, when) in FLAGS.items():
        flagged = d.index[d[col] >= thr]
        out[f"pre-cool on flag: {name}"] = {x: precool_program(_known_hour(when)) for x in flagged}
    for name in ("ComEd load fcst >= 19 GW", "RTO peak fcst >= 150 GW"):
        col, thr, when = FLAGS[name]
        flagged = d.index[d[col] >= thr]
        out[f"capacity-peak protocol on flag: {name}"] = {x: cp_program(_known_hour(when)) for x in flagged}
    return out


def evaluate(p: Params, w: pd.DataFrame, curve: cf.PowerCurve, offset: dict[int, float]) -> dict[str, Any]:
    s, e = WINDOW
    idx = pd.DatetimeIndex(w.loc[s:e].index)
    ci = data.controller_inputs()
    price = ci["price"].set_index("_time")["_value"].sort_index()
    price = price[~price.index.duplicated()]
    rh = ci["rh"].set_index("_time")["_value"].sort_index()
    base_run = ctl.run_controller(cf.OBSERVED_PROGRAM_F, price, rh, idx)
    d = daily_predictors(w, base_run.tier_5min)
    cp = estimated_5cp()
    hvac_meas = w["hvac_kw"].resample("1h").mean()
    mains = house_mains_hourly()
    cp["house_kw_measured"] = [float(mains.get(t, np.nan)) for t in cp["hour"]]
    cp["hvac_kw_measured"] = [float(hvac_meas.get(t, np.nan)) for t in cp["hour"]]
    cp["rto_fc_rank"] = [float(d["rto_fc_morn"].rank(ascending=False).get(t.normalize(), np.nan)) for t in cp["hour"]]
    cp["comed_fc_rank"] = [float(d["comed_fc_eve"].rank(ascending=False).get(t.normalize(), np.nan)) for t in cp["hour"]]

    rows: dict[str, dict[str, float]] = {}
    for name, overrides in strategies(d).items():
        run = ctl.run_controller(cf.OBSERVED_PROGRAM_F, price, rh, idx, day_programs=overrides)
        tr = cf.run(p, w, s, e, run.setpoint_f_15min, curve, offset)
        sm = cf.summarize(tr)
        ex = cf.excursions(tr)
        cp_kw = float(np.mean([tr["kw"].loc[t:t + pd.Timedelta(minutes=45)].mean() for t in cp["hour"]]))
        rows[name] = {"flagged_days": float(len(overrides or {})), "bill_usd": sm["bill_usd"],
                      "supply_usd": sm["cost_usd"], "delivery_usd": sm["delivery_usd"], "kwh": sm["kwh"],
                      "dtod_peak_kwh": sm["dtod_peak_1_7pm_kwh"], "hvac_kw_at_5cp": cp_kw,
                      "h_above_78": ex["h_above_78"], "h_above_80": ex["h_above_80"],
                      "h_below_72": ex["h_below_72"], "morning_6_12_mean_f": ex["morning_6_12_mean_f"],
                      "afternoon_max_f_median": ex["afternoon_max_f_median"], "sleep_mean_f": ex["sleep_mean_f"]}
    res = pd.DataFrame(rows).T
    base = res.iloc[0]
    res["bill_vs_current"] = res["bill_usd"] - base["bill_usd"]
    res["capacity_usd_per_yr_vs_current"] = (res["hvac_kw_at_5cp"] - base["hvac_kw_at_5cp"]) * CAPACITY_USD_PER_KW_MONTH * 12
    return {"daily": d, "flags": score_flags(d), "cp": cp, "strategies": res}
