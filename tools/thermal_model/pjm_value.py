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

Capacity obligation, per ComEd's Hourly Pricing FAQ and CUB (2026-06-23):
- **Hours:** the customer's demand is averaged over 10 hours of the prior summer.
  These are the 5 PJM system peak hours plus the 5 ComEd-zone peak hours.
- **Adjustments:** the average is adjusted for T&D losses (loss factor not
  published here; bracketed 1.00-1.10).
- **Billed quantity:** the "Capacity Charge N kW" on the bill is
  Capacity PLC x scaling factor (0.97854) x forecast pool requirement (0.9291).
  7.01 kW x $10.11236 = $70.89, matching the bill.
- **Peak selection:** both sets of peaks are estimated from pjm.metered_load as
  the top-5 daily peaks on weekdays, excluding NERC holidays.

Neither official list was in Influx on 2026-11-19.
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
PLC_KW_NOW = 7.01  # billed kW on the Jun-Oct 2026 bills (already scaled), set by summer 2025
SCALING_FACTOR = 0.97854  # CUB, 2026-06-23
FORECAST_POOL_REQUIREMENT = 0.9291  # CUB, 2026-06-23
BILLED_FACTOR = SCALING_FACTOR * FORECAST_POOL_REQUIREMENT
LOSS_FACTORS = (1.00, 1.05, 1.10)  # bracket; ComEd's T&D loss adjustment not confirmed here
# NERC holidays Jun-Sep 2026: Independence Day falls on Saturday Jul 4 and Labor Day on Mon Sep 7.
# Whether Fri Jul 3 counts as a holiday for the PJM peak list is reported both ways.
HOLIDAYS = ("2026-09-07",)
JULY3_AMBIGUOUS = "2026-07-03"

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


def _metered_load() -> pd.DataFrame:
    return data.raw_series("pjm_metered_load", '''from(bucket: "energy") |> range(start: 2026-05-31T00:00:00Z, stop: 2026-10-01T06:00:00Z)
  |> filter(fn: (r) => r._measurement == "pjm.metered_load" and r._field == "mw")
  |> group(columns: ["zone", "_time"]) |> max() |> group()
  |> keep(columns: ["_time", "zone", "_value"])''')


def peak_hours(zone: str, exclude_july3: bool) -> pd.DataFrame:
    """Top-5 daily-peak hours for ``zone`` ("RTO" or "CE"), Jun-Sep 2026, weekdays, NERC holidays out."""
    ml = _metered_load()
    s = ml[ml.zone == zone].set_index("_time")["_value"].sort_index().loc[WINDOW[0]:WINDOW[1]]
    excl = list(HOLIDAYS) + ([JULY3_AMBIGUOUS] if exclude_july3 else [])
    days = s.index.normalize()
    keep = (s.index.day_of_week < 5) & ~days.isin(pd.DatetimeIndex(excl).tz_localize(s.index.tz))
    s = s[keep]
    daily = s.groupby(s.index.normalize()).agg(["max", "idxmax"]).sort_values("max", ascending=False).head(5)
    return pd.DataFrame({"hour": daily["idxmax"].to_numpy(), "mw": daily["max"].to_numpy(), "set": zone})


def capacity_hours(exclude_july3: bool = False) -> pd.DataFrame:
    """The 10 hours ComEd averages: 5 PJM RTO peaks + 5 ComEd-zone peaks (a day can appear in both)."""
    return pd.concat([peak_hours("RTO", exclude_july3), peak_hours("CE", exclude_july3)], ignore_index=True)


def projected_billed_kw(avg_kw: float) -> dict[str, float]:
    return {f"loss {lf:.2f}": avg_kw * lf * BILLED_FACTOR for lf in LOSS_FACTORS}


def estimated_5cp() -> pd.DataFrame:
    """Back-compat: the PJM RTO set only."""
    return peak_hours("RTO", exclude_july3=False).rename(columns={"mw": "rto_mw"})


def circuits_hourly() -> pd.DataFrame:
    c = data.raw_series("refoss_circuits_hourly", '''from(bucket: "energy-longterm") |> range(start: 2026-05-31T00:00:00Z, stop: 2026-10-01T06:00:00Z)
  |> filter(fn: (r) => r._measurement == "refoss.channel" and r._field == "power_w")
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false, timeSrc: "_start")
  |> keep(columns: ["_time", "channel", "name", "_value"])''')
    wide = c.pivot_table(index="_time", columns="name", values="_value", aggfunc="sum") / 1000
    wide["HVAC"] = wide["Air Conditioning"] + wide["Furnace"]
    rooms = [n for n in wide.columns if n not in ("Main Breaker", "Air Conditioning", "Furnace", "HVAC")]
    wide["Unmonitored remainder"] = wide["Main Breaker"] - wide["HVAC"] - wide[rooms].sum(axis=1)
    return wide


CIRCUIT_CLASS = {
    "HVAC": "HVAC (controller)", "Unmonitored remainder": "unknown: circuits without a CT",
    "AV Rack": "continuous (network/servers)", "Kitchen Fridge": "essential", "Laundry Fridge": "essential",
    "Basement Fridge/Freezer": "essential", "Basement Micro/Fridge": "essential", "Washer/Dryer": "deferrable",
}


def circuit_breakdown(hours: list[pd.Timestamp]) -> pd.DataFrame:
    wide = circuits_hourly()
    at = wide.reindex(hours)
    summer = wide.loc[WINDOW[0]:WINDOW[1]]
    night = summer[summer.index.hour.isin([3, 4])]
    cols = ["HVAC"] + [n for n in wide.columns if n not in ("Main Breaker", "Air Conditioning", "Furnace", "HVAC")]
    t = pd.DataFrame({"class": [CIRCUIT_CLASS.get(n, "room circuit (manual)") for n in cols],
                      "kw_at_peak_hours": [float(at[n].mean()) for n in cols],
                      "kw_3_5am": [float(night[n].mean()) for n in cols]}, index=cols)
    return t.sort_values("kw_at_peak_hours", ascending=False)


def eagle_vs_mains() -> dict[str, float]:
    """Utility meter (EAGLE) vs Refoss mains, hourly, to confirm the always-on baseline is real."""
    e = data.raw_series("eagle_hourly", '''from(bucket: "energy-longterm") |> range(start: 2026-06-15T05:00:00Z, stop: 2026-09-30T05:00:00Z)
  |> filter(fn: (r) => r._measurement == "eagle.meter" and r._field == "demand_kw")
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false, timeSrc: "_start") |> keep(columns: ["_time", "_value"])''')
    eagle = e.set_index("_time")["_value"]
    mains = house_mains_hourly()
    j = pd.concat([eagle.rename("eagle"), mains.rename("mains")], axis=1).dropna()
    night = j[j.index.hour.isin([3, 4])]
    return {"hours": float(len(j)), "eagle_mean_kw": float(j.eagle.mean()), "mains_over_eagle": float(j.mains.sum() / j.eagle.sum()),
            "night_eagle_kw": float(night.eagle.mean()), "night_mains_kw": float(night.mains.mean()),
            "eagle_p05_kw": float(j.eagle.quantile(0.05)), "eagle_min_kw": float(j.eagle.min())}


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
    cp = capacity_hours(exclude_july3=False)
    hvac_meas = w["hvac_kw"].resample("1h").mean()
    mains = house_mains_hourly()
    cp["house_kw_measured"] = [float(mains.get(t, np.nan)) for t in cp["hour"]]
    cp["hvac_kw_measured"] = [float(hvac_meas.get(t, np.nan)) for t in cp["hour"]]
    wk = d[d.index.day_of_week < 5]
    cp["rto_fc_rank"] = [float(wk["rto_fc_morn"].rank(ascending=False).get(t.normalize(), np.nan)) for t in cp["hour"]]
    cp["comed_fc_rank"] = [float(wk["comed_fc_eve"].rank(ascending=False).get(t.normalize(), np.nan)) for t in cp["hour"]]
    non_hvac = (cp["house_kw_measured"] - cp["hvac_kw_measured"]).to_numpy()

    rows: dict[str, dict[str, float]] = {}
    for name, overrides in strategies(d).items():
        run = ctl.run_controller(cf.OBSERVED_PROGRAM_F, price, rh, idx, day_programs=overrides)
        tr = cf.run(p, w, s, e, run.setpoint_f_15min, curve, offset)
        sm = cf.summarize(tr)
        ex = cf.excursions(tr)
        cp_kw = float(np.mean([tr["kw"].loc[t:t + pd.Timedelta(minutes=45)].mean() for t in cp["hour"]]))
        billed = float(np.mean(non_hvac) + cp_kw) * 1.05 * BILLED_FACTOR
        rows[name] = {"flagged_days": float(len(overrides or {})), "bill_usd": sm["bill_usd"],
                      "supply_usd": sm["cost_usd"], "delivery_usd": sm["delivery_usd"], "kwh": sm["kwh"],
                      "dtod_peak_kwh": sm["dtod_peak_1_7pm_kwh"], "hvac_kw_at_peaks": cp_kw, "billed_cap_kw": billed,
                      "h_above_78": ex["h_above_78"], "h_above_80": ex["h_above_80"],
                      "h_below_72": ex["h_below_72"], "morning_6_12_mean_f": ex["morning_6_12_mean_f"],
                      "afternoon_max_f_median": ex["afternoon_max_f_median"], "sleep_mean_f": ex["sleep_mean_f"]}
    res = pd.DataFrame(rows).T
    base = res.iloc[0]
    res["bill_vs_current"] = res["bill_usd"] - base["bill_usd"]
    res["capacity_usd_per_yr_vs_current"] = (res["billed_cap_kw"] - base["billed_cap_kw"]) * CAPACITY_USD_PER_KW_MONTH * 12

    # Selective capacity protocols on the day-before ComEd flag (weekdays), comfort tracked on flagged days
    flag_days = d.index[(d["comed_fc_eve"] >= 19000) & (d.index.day_of_week < 5)]
    prot_rows: dict[str, dict[str, float]] = {}
    for name, prog in capacity_protocols().items():
        overrides = {x: prog for x in flag_days} if prog else {}
        run = ctl.run_controller(cf.OBSERVED_PROGRAM_F, price, rh, idx, day_programs=overrides)
        tr = cf.run(p, w, s, e, run.setpoint_f_15min, curve, offset)
        sm = cf.summarize(tr)
        hv = np.array([tr["kw"].loc[t:t + pd.Timedelta(minutes=45)].mean() for t in cp["hour"]])
        fd = tr[tr.index.normalize().isin(flag_days)]
        prot_rows[name] = {"hvac_kw_at_peaks": float(hv.mean()), "hvac_kw_at_peaks_max": float(hv.max()),
                           "billed_cap_kw": float(np.mean(non_hvac) + hv.mean()) * 1.05 * BILLED_FACTOR,
                           "bill_usd": sm["bill_usd"], "flagdays_max_f": float(fd.house_f.max()),
                           "flagdays_h_above_80": float((fd.house_f > 80).sum() * 0.25),
                           "flagdays_3_6pm_mean_f": float(fd.house_f[fd.index.hour.isin([15, 16, 17])].mean()),
                           "flagdays_morning_mean_f": float(fd.house_f[fd.index.hour.isin(range(6, 12))].mean())}
    prot = pd.DataFrame(prot_rows).T
    prot["energy_vs_current"] = prot["bill_usd"] - prot["bill_usd"].iloc[0]
    prot["capacity_usd_per_yr_vs_current"] = (prot["billed_cap_kw"] - prot["billed_cap_kw"].iloc[0]) * CAPACITY_USD_PER_KW_MONTH * 12
    return {"daily": d, "flags": score_flags(d), "cp": cp, "strategies": res, "protocols": prot,
            "protocol_flag_days": float(len(flag_days))}


def capacity_protocols() -> dict[str, dict[int, float] | None]:
    """Selective capacity protection (deg F by hour) applied on flagged days; None = current program."""
    def f(c: float) -> float:
        return c * 9 / 5 + 32
    base = cf.OBSERVED_PROGRAM_F
    return {
        "current program + controller": None,
        "window only: 26.5C 3-6pm": {h: f(26.5) if 15 <= h < 18 else base[h] for h in range(24)},
        "pre-cool 23.0C 3am-1pm + 26.5C 3-6pm": {h: f(23.0) if 3 <= h < 13 else f(26.5) if 15 <= h < 18 else base[h]
                                                for h in range(24)},
        "full protocol: 22.5C pre-cool, 26.5C 1-7pm": cp_program(3),
    }
