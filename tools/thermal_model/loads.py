"""Where the house's non-HVAC baseline goes: CT audit, per-leg unmonitored load, weather sensitivity.

The Refoss EM16P mains CTs are em:1 (leg A) and em:7 (leg B). Its banks are
A (em:1-6), B (em:7-12) and C (em:13-18, voltage-referenced to leg A), but a
bank does not fix which leg a circuit's breaker is on. ``phase_check`` finds
the leg from the data: when one circuit steps while every other CT is quiet,
the step shows up on exactly one main. ``PHYSICAL_LEG`` records the result:

    unmonitored leg A = em:1 - (CT'd circuits on leg A)
    unmonitored leg B = em:7 - (CT'd circuits on leg B)

A 240 V load draws on both legs equally, so a remainder on one leg only is one
or more 120 V circuits on that leg without a CT.

All windows are fixed so the numbers in docs/THERMAL_MODEL_2026-11.md reproduce.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from . import data

SEASON = ("2026-05-26T05:00:00Z", "2026-11-19T18:00:00Z")
AUDIT = ("2026-11-12T18:00:00Z", "2026-11-19T18:00:00Z")  # last 7 days of 30 s raw data
NIGHT_HOURS = (1, 2, 3, 4)  # 1:00-4:59 local: no cooking, laundry or lighting
PHASE_WEEK = AUDIT  # 1-min data for the step test; the AC is off in November, so steps are clean
MAINS_A, MAINS_B = "em:1", "em:7"
# Physical leg of each CT'd circuit. "step" = confirmed by phase_check on Nov 12-19 (median step
# ratio >= 0.83 on one main, <= 0.04 on the other). AC is 240 V: one CT per leg.
# em:14 Basement Micro/Fridge is in bank C (leg A reference) but its compressor steps land on em:7.
# em:12 AV Rack's own jitter does not reach either main, so it can't be step-tested; it must be on leg B,
# or the leg-A remainder would be >= its 270 W minimum (it is ~100 W).
# em:4, em:5, em:15-18 are too steady to step-test; they are assumed to be on their bank's leg (A),
# and ``unmonitored`` also reports leg B's lower bound with all of them moved to leg B.
PHYSICAL_LEG = {
    "em:2": "A", "em:3": "A", "em:6": "A", "em:13": "A",            # step (em:2 by design)
    "em:8": "B", "em:9": "B", "em:10": "B", "em:11": "B", "em:14": "B",  # step (em:8 by design)
    "em:12": "B",                                                   # constraint
    "em:4": "A", "em:5": "A", "em:15": "A", "em:16": "A", "em:17": "A", "em:18": "A",  # assumed
}
UNDETERMINED = ["em:4", "em:5", "em:15", "em:16", "em:17", "em:18"]
LEG_A = [c for c, leg in PHYSICAL_LEG.items() if leg == "A"]
LEG_B = [c for c, leg in PHYSICAL_LEG.items() if leg == "B"]
TEMP_BINS = [-np.inf, 45, 55, 65, 75, np.inf]


def leg_of(channel: str) -> str:
    return {MAINS_A: "A", MAINS_B: "B"}.get(channel) or PHYSICAL_LEG[channel]


def ct_audit() -> pd.DataFrame:
    """Per channel over the last 7 days of raw data: mean power, share of negative readings, PF, current, voltage."""
    flux = f'''data = from(bucket: "energy") |> range(start: {AUDIT[0]}, stop: {AUDIT[1]})
  |> filter(fn: (r) => r._measurement == "refoss.channel" and
       (r._field == "power_w" or r._field == "current_a" or r._field == "power_factor" or r._field == "voltage_v"))
  |> toFloat() |> keep(columns: ["_time", "_field", "_value", "channel", "name"])
m = data |> group(columns: ["channel", "name", "_field"]) |> mean() |> map(fn: (r) => ({{r with stat: "mean"}}))
lo = data |> filter(fn: (r) => r._field == "power_w") |> group(columns: ["channel", "name", "_field"]) |> min()
  |> map(fn: (r) => ({{r with stat: "min"}}))
neg = data |> filter(fn: (r) => r._field == "power_w")
  |> map(fn: (r) => ({{r with _value: if r._value < 0.0 then 1.0 else 0.0}}))
  |> group(columns: ["channel", "name", "_field"]) |> mean() |> map(fn: (r) => ({{r with stat: "frac_neg"}}))
union(tables: [m, lo, neg]) |> group() |> keep(columns: ["channel", "name", "_field", "stat", "_value"])'''
    a = data.raw_series("refoss_ct_audit_7d", flux)
    t = a.pivot_table(index=["channel", "name"], columns=["_field", "stat"], values="_value")
    out = pd.DataFrame({
        "mean_w": t[("power_w", "mean")], "min_w": t[("power_w", "min")], "frac_neg": t[("power_w", "frac_neg")],
        "pf": t[("power_factor", "mean")], "amps": t[("current_a", "mean")], "volts": t[("voltage_v", "mean")],
    }).reset_index()
    out["leg"] = out["channel"].map(leg_of)
    out["n"] = out["channel"].str.split(":").str[1].astype(int)
    return out.sort_values("n").drop(columns="n").set_index("channel")


def channels_hourly() -> pd.DataFrame:
    """Hourly mean W per channel (columns em:N), local time, from the 1-min long-term bucket, pulled by month."""
    parts = []
    for a, b in data._month_chunks(pd.Timestamp(SEASON[0]), pd.Timestamp(SEASON[1])):
        flux = f'''from(bucket: "energy-longterm") |> range(start: {a:%Y-%m-%dT%H:%M:%SZ}, stop: {b:%Y-%m-%dT%H:%M:%SZ})
  |> filter(fn: (r) => r._measurement == "refoss.channel" and r._field == "power_w")
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false, timeSrc: "_start")
  |> keep(columns: ["_time", "channel", "_value"])'''
        parts.append(data.raw_series(f"refoss_channels_hourly_{a:%Y%m%d}", flux))
    long = pd.concat(parts)
    wide = long.pivot_table(index="_time", columns="channel", values="_value", aggfunc="mean").sort_index()
    return wide[[MAINS_A, MAINS_B, *PHYSICAL_LEG]].dropna()


def phase_check() -> pd.DataFrame:
    """Which main each circuit's isolated steps land on (1-min data, AC off).

    An event is a minute where the circuit changes by > 15 W while every other
    CT'd circuit changes by < 8 W. Ratio = mains change / circuit change (median).
    """
    flux = f'''from(bucket: "energy-longterm") |> range(start: {PHASE_WEEK[0]}, stop: {PHASE_WEEK[1]})
  |> filter(fn: (r) => r._measurement == "refoss.channel" and r._field == "power_w")
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false, timeSrc: "_start")
  |> keep(columns: ["_time", "channel", "_value"])'''
    a = data.raw_series("refoss_channels_1m_phase", flux)
    d = a.pivot_table(index="_time", columns="channel", values="_value").dropna().diff().dropna()
    circuits = list(PHYSICAL_LEG)
    rows = {}
    for c in circuits:
        quiet = (d[[o for o in circuits if o != c]].abs() < 8).all(axis=1)
        ev = d[(d[c].abs() > 15) & quiet]
        rows[c] = {"events": len(ev),
                   "on_em1": float(np.median(ev[MAINS_A] / ev[c])) if len(ev) else np.nan,
                   "on_em7": float(np.median(ev[MAINS_B] / ev[c])) if len(ev) else np.nan,
                   "leg_used": PHYSICAL_LEG[c]}
    return pd.DataFrame.from_dict(rows, orient="index")


def leg_voltages() -> dict[str, Any]:
    """Which leg bank C's voltage reference (Lc) is on, and how far the legs drift apart under uneven load.

    With 120 V loads, V_A - V_B ~ (R_hot + 2 R_neutral) x (I_B - I_A); the slope is fitted per month on
    5-min means. A connection getting worse would show as a rising slope.
    """
    week = data.raw_series("refoss_volts_amps_1m", f'''from(bucket: "energy-longterm") |> range(start: {AUDIT[0]}, stop: {AUDIT[1]})
  |> filter(fn: (r) => r._measurement == "refoss.channel" and (r.channel == "em:1" or r.channel == "em:7" or r.channel == "em:13")
       and (r._field == "voltage_v" or r._field == "current_a"))
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false, timeSrc: "_start")
  |> keep(columns: ["_time", "channel", "_field", "_value"])''')
    v = week[week["_field"] == "voltage_v"].pivot_table(index="_time", columns="channel", values="_value").dropna()
    parts = []
    for a, b in data._month_chunks(pd.Timestamp(SEASON[0]), pd.Timestamp(SEASON[1])):
        parts.append(data.raw_series(f"refoss_va_5m_{a:%Y%m%d}", f'''from(bucket: "energy-longterm") |> range(start: {a:%Y-%m-%dT%H:%M:%SZ}, stop: {b:%Y-%m-%dT%H:%M:%SZ})
  |> filter(fn: (r) => r._measurement == "refoss.channel" and (r.channel == "em:1" or r.channel == "em:7") and (r._field == "voltage_v" or r._field == "current_a"))
  |> aggregateWindow(every: 5m, fn: mean, createEmpty: false, timeSrc: "_start") |> keep(columns: ["_time", "channel", "_field", "_value"])'''))
    w = pd.concat(parts).pivot_table(index="_time", columns=["channel", "_field"], values="_value").dropna()
    w = w[(w[(MAINS_A, "voltage_v")] > 90) & (w[(MAINS_B, "voltage_v")] > 90)]  # drop dropout readings
    dv = w[(MAINS_A, "voltage_v")] - w[(MAINS_B, "voltage_v")]
    di = w[(MAINS_B, "current_a")] - w[(MAINS_A, "current_a")]
    monthly = pd.DataFrame({m: {"ohm": float(np.polyfit(di[g], dv[g], 1)[0]), "r": float(np.corrcoef(di[g], dv[g])[0, 1]),
                                "dv_p95": float(dv[g].quantile(0.95)), "imbalance_p95_a": float(di[g].quantile(0.95)),
                                "v_a_max": float(w.loc[g, (MAINS_A, "voltage_v")].max())}
                            for m, g in dv.groupby(dv.index.strftime("%Y-%m")).groups.items()}).T
    return {"bank_c_minus_leg_a_v": float((v["em:13"] - v[MAINS_A]).mean()),
            "bank_c_minus_leg_b_v": float((v["em:13"] - v[MAINS_B]).mean()),
            "imbalance_slope_by_month": monthly}


def unmonitored(ch: pd.DataFrame) -> pd.DataFrame:
    a = ch[MAINS_A] - ch[LEG_A].sum(axis=1)
    b = ch[MAINS_B] - ch[LEG_B].sum(axis=1)
    return pd.DataFrame({"leg_a": a, "leg_b": b, "leg_b_low": b - ch[UNDETERMINED].sum(axis=1),
                         "total": a + b, "mains": ch[MAINS_A] + ch[MAINS_B]})


def nightly(u: pd.DataFrame, w: pd.DataFrame, fridge_w: pd.Series) -> pd.DataFrame:
    """One row per night: mean unmonitored W (1-5am), outdoor temp, monitored fridge W."""
    out_h = w["out_f"].resample("h").mean()
    j = u.join(out_h.rename("out_f")).join(fridge_w.rename("fridges"))
    j = j[j.index.hour.isin(NIGHT_HOURS)]
    return j.groupby(j.index.normalize()).mean().dropna()


def _slope(x: pd.Series, y: pd.Series) -> float:
    return float(np.polyfit(x.to_numpy(float), y.to_numpy(float), 1)[0])


def analyse(w: pd.DataFrame) -> dict[str, Any]:
    audit = ct_audit()
    names = audit["name"]
    phase = phase_check().join(names)
    volts = leg_voltages()
    ch = channels_hourly()
    u = unmonitored(ch)
    night = u[u.index.hour.isin(NIGHT_HOURS)]
    fridge_cols = [c for c in names.index if "fridge" in names[c].lower() and c in ch]
    nights = nightly(u, w, ch[fridge_cols].sum(axis=1))
    nights["bin"] = pd.cut(nights["out_f"], TEMP_BINS, labels=["<=45F", "45-55F", "55-65F", "65-75F", ">75F"])
    by_temp = nights.groupby("bin", observed=True)[["leg_a", "leg_b", "leg_b_low", "total", "fridges", "out_f"]].mean()
    by_temp["nights"] = nights.groupby("bin", observed=True).size()
    monthly = night.groupby(night.index.strftime("%Y-%m"))[["leg_a", "leg_b", "leg_b_low", "total"]].mean()
    share = float(u["total"].sum() / u["mains"].sum())
    return {
        "audit": audit,
        "phase": phase,
        "volts": volts,
        "fridge_channels": {c: names[c] for c in fridge_cols},
        "hours": len(u), "nights": len(nights),
        "unmonitored_kwh_share": share,
        "night": {leg: {"median_w": float(night[leg].median()), "p05_w": float(night[leg].quantile(0.05)),
                        "p95_w": float(night[leg].quantile(0.95))} for leg in ("leg_a", "leg_b", "leg_b_low", "total")},
        "negative_hours": {leg: float((u[leg] < 0).mean()) for leg in ("leg_a", "leg_b", "total")},
        "total_p01_w": float(u["total"].quantile(0.01)),
        "monthly_night": monthly,
        "by_outdoor_temp": by_temp,
        "slope_w_per_f": {k: _slope(nights["out_f"], nights[k]) for k in ("leg_a", "leg_b", "leg_b_low", "total", "fridges")},
    }
