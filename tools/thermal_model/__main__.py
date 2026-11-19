"""CLI: python -m tools.thermal_model {pull,fit,counterfactual}  (run from the repo root)."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from typing import Any

import numpy as np
import pandas as pd

from . import controller_sim as ctl
from . import counterfactual as cf
from . import data
from . import optimize as opt
from . import pjm_value as pjm
from . import validate as val
from .model import Params, fit, score, segments
from .solar import blended_solar

RESULTS = data.CACHE / "results"

# Validation split: fit on Jun-Aug, test on Sep (held out).
TRAIN = ("2026-06-01", "2026-08-31")
HOLDOUT = ("2026-09-01", "2026-09-30")
# Cooling-season window for the production fit and the counterfactual.
SEASON = ("2026-06-01", "2026-09-30")
MONTHS = [("2026-06-01", "2026-06-30"), ("2026-07-01", "2026-07-31"), ("2026-08-01", "2026-08-31"), ("2026-09-01", "2026-09-30")]
# Primary model is one-node (1R1C): the 2R2C mass node fits single days as well but
# fails the closed-loop replay (see README), so it is kept only as a diagnostic.
ONE_NODE = {"a_im": 0.0, "b_mass": 0.0, "r": 0.05}
COOLING_ONLY = {"k_heat": 2.0, **ONE_NODE}  # k_heat unidentifiable without furnace runtime; pinned placeholder


def _save(name: str, obj: Any) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{name}.json").write_text(json.dumps(obj, indent=1, default=float))


def _fmt(p: Params) -> str:
    return (f"a_env={p.a_env:.4f} (tau {p.tau_hours:.0f}h)  b_solar={p.b_air:.2f}  "
            f"c={p.c:.2f}  k_cool={p.k_cool:.2f}  k_heat={p.k_heat:.2f}")


def cmd_fit(w: pd.DataFrame) -> None:
    out: dict[str, Any] = {}
    tr, te = segments(w, *TRAIN), segments(w, *HOLDOUT)
    r = fit(tr, fixed=COOLING_ONLY)
    out["validation"] = {"params": asdict(r.params), "train": score(r.params, tr), "holdout_sep": score(r.params, te)}
    print("== validation: fit Jun-Aug, test Sep")
    print(" ", _fmt(r.params))
    for k in ("train", "holdout_sep"):
        s = out["validation"][k]
        print(f"  {k:12s} days={s['n_days']:.0f}  rmse24h={s['rmse_24h']:.2f}F  persistence={s['rmse_persistence']:.2f}F  bias={s['bias']:+.2f}")

    print("\n== month-by-month stability")
    out["monthly"] = {}
    for a, b in MONTHS:
        rm = fit(segments(w, a, b), fixed=COOLING_ONLY)
        out["monthly"][a[:7]] = asdict(rm.params)
        print(f"  {a[:7]} ", _fmt(rm.params))

    print("\n== fall (Oct 1 - Nov 19): horizontal vs south-window solar input")
    out["fall"] = {}
    for f in (0.0, 0.5, 0.8):
        w2 = w.assign(solar_x=blended_solar(w, f))
        train = segments(w2, *TRAIN, solar_col="solar_x") + segments(w2, "2026-10-01", "2026-10-20", solar_col="solar_x")
        rf = fit(train, fixed=ONE_NODE)
        res = {k: score(rf.params, segments(w2, a, b, solar_col="solar_x"))["rmse_24h"]
               for k, (a, b) in {"sep": HOLDOUT, "late_oct": ("2026-10-21", "2026-10-31"), "nov": ("2026-11-01", "2026-11-19")}.items()}
        out["fall"][str(f)] = {"params": asdict(rf.params), "rmse": res}
        print(f"  south_fraction={f}: " + "  ".join(f"{k} {v:.2f}F" for k, v in res.items()) + f"   k_heat={rf.params.k_heat:.2f}")

    season = fit(segments(w, *SEASON), fixed=COOLING_ONLY)
    out["season_params"] = asdict(season.params)
    print("\n== production cooling-season fit (Jun-Sep)\n ", _fmt(season.params))
    _save("fit", out)


def cmd_counterfactual(w: pd.DataFrame) -> None:
    curve = cf.power_curve(w)
    offset = cf.tstat_offset_by_hour(w)
    out: dict[str, Any] = {"power_curve": {"u": curve.u.tolist(), "kw": curve.kw.tolist()}}

    p2 = fit(segments(w, *SEASON), fixed={"k_heat": 2.0}).params
    rep2 = cf.run(p2, w, "2026-06-25", SEASON[1], w["cool_setpoint_f"], curve, offset)
    out["2r2c_replay_kwh"] = float(rep2.kwh.sum())
    print(f"diagnostic: 2R2C replay {rep2.kwh.sum():.0f} kWh (rejected for closed-loop use; see README)")

    for s1 in (0.5, 0.7):
        p = fit(segments(w, *SEASON), stage1_capacity=s1, fixed=COOLING_ONLY).params
        tag = f"stage1_{s1}"
        out[tag] = {"params": asdict(p)}
        print(f"\n######## stage-1 capacity = {s1:.0%} of full ########")

        # Replay: the setpoint history the house actually ran (incl. Arm B holds).
        start = "2026-06-25"  # thermostat setpoints continuous from here
        rep = cf.run(p, w, start, SEASON[1], w["cool_setpoint_f"], curve, offset)
        dc = cf.daily_compare(rep, w)
        meas_cost = float((w.loc[rep.index, "hvac_kw"] * 0.25 * w.loc[rep.index, "price_cents_per_kwh"] / 100).sum())
        rv = {
            "sim_kwh": float(dc.sim_kwh.sum()), "meas_kwh": float(dc.meas_kwh.sum()),
            "sim_cost": float(rep.cost_usd.sum()), "meas_cost": meas_cost,
            "daily_corr": float(dc[["sim_kwh", "meas_kwh"]].corr().iloc[0, 1]),
            "daily_mae_kwh": float((dc.sim_kwh - dc.meas_kwh).abs().mean()),
            "indoor_rmse_f": float(((rep.house_f - w.loc[rep.index, "house_f"]) ** 2).mean() ** 0.5),
            "sim_delivery": float(rep.delivery_usd.sum()),
            "meas_delivery": float((w.loc[rep.index, "hvac_kw"] * 0.25
                                    * cf.delivery_cents_per_kwh(pd.DatetimeIndex(rep.index)) / 100).sum()),
        }
        out[tag]["replay"] = rv
        print(f"replay {start}..{SEASON[1]}: sim {rv['sim_kwh']:.0f} kWh / ${rv['sim_cost']:.0f}  vs measured "
              f"{rv['meas_kwh']:.0f} kWh / ${rv['meas_cost']:.0f};  daily r={rv['daily_corr']:.2f}, "
              f"daily MAE {rv['daily_mae_kwh']:.1f} kWh, indoor RMSE {rv['indoor_rmse_f']:.2f}F; "
              f"DTOD delivery sim ${rv['sim_delivery']:.0f} vs measured ${rv['meas_delivery']:.0f}")

        rows = {}
        for name, prog in cf.SCENARIOS.items():
            idx = w.loc[SEASON[0]:SEASON[1]].index
            tr = cf.run(p, w, *SEASON, cf.program_series(pd.DatetimeIndex(idx), prog), curve, offset)
            summ = cf.summarize(tr)
            hot = tr[tr.out_f.resample("D").transform("max") >= 89]
            summ["hot_day_kwh_per_day"] = float(hot.kwh.sum() / max(1, hot.index.normalize().nunique()))
            rows[name] = summ
        df = pd.DataFrame(rows).T
        base = df.iloc[0]
        df["kwh_vs_program"] = (df.kwh / base.kwh - 1) * 100
        df["cost_vs_program"] = (df.cost_usd / base.cost_usd - 1) * 100
        df["bill_vs_program"] = (df.bill_usd / base.bill_usd - 1) * 100
        out[tag]["scenarios"] = df.to_dict(orient="index")
        cols = ["kwh", "kwh_vs_program", "cost_usd", "delivery_usd", "bill_usd", "bill_vs_program", "avg_price_paid_c", "dtod_peak_1_7pm_kwh",
                "hot_day_kwh_per_day", "sleep_mean_f", "afternoon_max_f_median", "hours_above_78"]
        print(f"scenarios {SEASON[0]}..{SEASON[1]} (actual weather + ComEd prices):")
        print(df[cols].round(1).to_string())
    _save("counterfactual", out)


def cmd_validate(w: pd.DataFrame) -> None:
    """Out-of-sample checks: leave-one-month-out energy, peak events, pulldown rates."""
    tr, rows = val.lomo_replays(w)
    df = pd.DataFrame(rows)
    tot = df[["sim_kwh", "meas_kwh", "sim_cost", "meas_cost"]].sum()
    print("== leave-one-month-out energy replay (params, kW curve, thermostat offset from the other 3 months)")
    print(df.round(2).to_string(index=False))
    print(f"total: sim {tot.sim_kwh:.0f} vs meas {tot.meas_kwh:.0f} kWh ({100 * (tot.sim_kwh / tot.meas_kwh - 1):+.1f}%), "
          f"supply ${tot.sim_cost:.0f} vs ${tot.meas_cost:.0f}")
    pe = val.peak_events(tr)
    ha = pd.DataFrame(pe["hottest_afternoons"])
    print("\n== hottest 10 afternoons (noon-8pm), held-out")
    print(ha.round(1).to_string(index=False))
    print(f"sum: sim {ha.sim_kwh.sum():.0f} vs meas {ha.meas_kwh.sum():.0f} kWh; mean peak-temp error {(ha.sim_max_f - ha.meas_max_f).mean():+.2f}F")
    for k in ("all_evenings", "hot_evenings", "expensive_slots"):
        print(k, {a: round(b, 2) if isinstance(b, float) else b for a, b in pe[k].items()})
    p = fit(segments(w, *SEASON), fixed=COOLING_ONLY).params
    pr = val.pulldown_rates(w, p)
    cols = ["thermostat", "house mean", "2F hall", "2F S-BR", "1F office", "model", "out_minus_in"]
    print("\n== evening full-stage-2 pulldown rate, F/h (median over runs)")
    print(pr.groupby("period")[cols].median().round(2).to_string())
    print(pr.groupby("period").size().to_string())
    _save("validate", {"lomo": rows, "peak_events": pe,
                       "pulldown_median": pr.groupby("period")[cols].median().to_dict(orient="index")})


# Cost-curve window: day-ahead LMP coverage starts ~Jun 24.
CURVE_WINDOW = ("2026-06-25", "2026-09-30")
CAPS = [76.0, 77.0, 78.0, 79.0, 80.0]
SLEEP_CAP_F = 75.2  # the program's overnight comfort, held fixed so the curve prices afternoon comfort only
FLOOR_F = 72.0


def cmd_costcurve(w: pd.DataFrame) -> None:
    """Cheapest cooling per afternoon cap at fixed sleep comfort, by planner knowledge."""
    out: dict[str, Any] = {}
    s, e = CURVE_WINDOW
    idx = pd.DatetimeIndex(w.loc[s:e].index)
    for s1 in (0.5, 0.7):
        p = fit(segments(w, *SEASON), stage1_capacity=s1, fixed=COOLING_ONLY).params
        curve, off = cf.power_curve(w), cf.tstat_offset_by_hour(w)
        df = opt.cost_curve(p, w, s, e, CAPS, FLOOR_F, curve, off, sleep_cap=SLEEP_CAP_F)
        sig = opt.price_signals(w, s, e)["hour-of-day"]
        derived_rows, programs = {}, {}
        for cap in CAPS[:2]:
            traj, _ = opt.plan(p, w, s, e, cap, FLOOR_F, curve, plan_price=sig, sleep_cap=SLEEP_CAP_F)
            prog = opt.derived_program(traj, off)
            programs[f"{cap:.0f}"] = prog
            tr = cf.run(p, w, s, e, cf.program_series(idx, prog), curve, off)
            derived_rows[f"derived hourly program (cap {cap:.0f})"] = {"cap_f": cap, **opt._metrics(tr, w)}
        for name, prog in cf.THREE_PERIOD_PROGRAMS.items():
            tr = cf.run(p, w, s, e, cf.program_series(idx, prog), curve, off)
            derived_rows[f"3-period {name}"] = {"cap_f": float("nan"), **opt._metrics(tr, w)}
        df = pd.concat([df, pd.DataFrame(derived_rows).T])
        base = df.loc["program (75 / 79 pm)", "bill_usd"]
        df["bill_vs_program_pct"] = (df["bill_usd"] / base - 1) * 100
        cols = ["bill_usd", "bill_vs_program_pct", "delivery_usd", "kwh", "dtod_peak_kwh", "avg_supply_c", "max_house_f",
                "afternoon_max_f_median", "sleep_mean_f", "min_house_f"]
        print(f"\n######## stage-1 capacity = {s1:.0%}; {s}..{e}; sleep (10pm-7am) <= {SLEEP_CAP_F}F ########")
        print(df[cols].round(1).to_string())
        out[f"stage1_{s1}"] = {"table": df.to_dict(orient="index"), "derived_programs_tstat_f": programs}
    _save("costcurve", out)


FOURWAY_WINDOW = ("2026-07-06", "2026-09-30")  # controller live; config unchanged throughout
FOURWAY_PROGRAMS = {
    "current program": cf.OBSERVED_PROGRAM_F,
    "3-period 22.5/25.0": cf.THREE_PERIOD_PROGRAMS["24.0 | 22.5 from 3am | 25.0 from 1pm"],
    "3-period 22.0/24.5": cf.THREE_PERIOD_PROGRAMS["24.0 | 22.0 from 3am | 24.5 from 1pm"],
    "3-period 23.0/25.5": cf.three_period_program(24.0, 23.0, 3, 25.5),
    "3-period 23.0/26.0": cf.three_period_program(24.0, 23.0, 3, 26.0),
    "3-period 23.5/26.0": cf.three_period_program(24.0, 23.5, 3, 26.0),
}


def cmd_fourway(w: pd.DataFrame) -> None:
    """Schedule x controller comparison, with the controller emulated on each schedule."""
    s, e = FOURWAY_WINDOW
    idx = pd.DatetimeIndex(w.loc[s:e].index)
    ci = data.controller_inputs()
    price = ci["price"].set_index("_time")["_value"].sort_index()
    price = price[~price.index.duplicated()]
    rh = ci["rh"].set_index("_time")["_value"].sort_index()
    out: dict[str, Any] = {}

    v = ctl.validate_against_log(cf.OBSERVED_PROGRAM_F, price, rh, ci["actions"], idx)
    out["emulator_validation"] = v
    print(f"== controller emulator vs real action log ({s}..{e}, current program)")
    print(f"   hold time emulated {v['emulated_hold_h']:.0f} h vs actual {v['actual_hold_h']:.0f} h; "
          f"recall {v['recall']:.1%}, precision {v['precision']:.1%}, Jaccard {v['jaccard']:.2f}; "
          f"episodes {v['emulated_episodes']:.0f} vs {v['actual_episodes']:.0f}; daily r {v['daily_hold_corr']:.2f}")

    m = w.loc[s:e]
    meas_supply = float((m.hvac_kw * 0.25 * m.price_cents_per_kwh / 100).sum())
    meas_deliv = float((m.hvac_kw * 0.25 * cf.delivery_cents_per_kwh(pd.DatetimeIndex(m.index)) / 100).sum())
    out["measured"] = {"bill_usd": meas_supply + meas_deliv, "supply_usd": meas_supply, "delivery_usd": meas_deliv,
                       "kwh": float((m.hvac_kw * 0.25).sum()),
                       "dtod_peak_kwh": float((m.hvac_kw * 0.25)[m.index.hour.isin(cf.DTOD_PEAK_HOURS)].sum())}

    for s1 in (0.5, 0.7):
        p = fit(segments(w, *SEASON), stage1_capacity=s1, fixed=COOLING_ONLY).params
        curve, off = cf.power_curve(w), cf.tstat_offset_by_hour(w)
        rows: dict[str, dict[str, float]] = {}
        rep = cf.run(p, w, s, e, w["cool_setpoint_f"], curve, off)
        rows["replay: actual setpoint history"] = {**_fourway_metrics(rep), "hold_h": float("nan")}
        for pname, prog in FOURWAY_PROGRAMS.items():
            for enabled in (False, True):
                run = ctl.run_controller(prog, price, rh, idx, enabled=enabled)
                tr = cf.run(p, w, s, e, run.setpoint_f_15min, curve, off)
                tiered = (run.tier_5min > 0).groupby(run.tier_5min.index.floor("15min")).mean().reindex(idx).fillna(0)
                label = f"{pname} {'+ controller' if enabled else '(no controller)'}"
                rows[label] = {**_fourway_metrics(tr), "hold_h": float(run.hold_5min.sum() / 12),
                               "spike_tier_usd": float(((tr.cost_usd + tr.delivery_usd) * tiered.to_numpy()).sum()),
                               "spike_tier_kwh": float((tr.kwh * tiered.to_numpy()).sum())}
        df = pd.DataFrame(rows).T
        out[f"stage1_{s1}"] = df.to_dict(orient="index")
        print(f"\n######## stage-1 capacity = {s1:.0%}; {s}..{e}; measured HVAC: "
              f"${out['measured']['bill_usd']:.0f} (supply ${meas_supply:.0f} + DTOD ${meas_deliv:.0f}), "
              f"{out['measured']['kwh']:.0f} kWh, {out['measured']['dtod_peak_kwh']:.0f} kWh 1-7pm ########")
        print(df[["bill_usd", "supply_usd", "delivery_usd", "kwh", "dtod_peak_kwh", "hold_h", "spike_tier_kwh",
                  "spike_tier_usd"]].round(1).to_string())
        print(df[["afternoon_max_f_median", "max_f", "h_above_78", "h_above_80", "h_above_82", "morning_6_12_mean_f",
                  "h_below_72", "h_below_70", "min_f", "evening_19_22_mean_f", "bedtime_22_mean_f",
                  "sleep_mean_f"]].round(1).to_string())
    _save("fourway", out)


def cmd_pjm(w: pd.DataFrame) -> None:
    """Would PJM day-ahead / day-of information have changed anything? (controller always on)"""
    out: dict[str, Any] = {}
    for s1 in (0.5, 0.7):
        p = fit(segments(w, *SEASON), stage1_capacity=s1, fixed=COOLING_ONLY).params
        r = pjm.evaluate(p, w, cf.power_curve(w), cf.tstat_offset_by_hour(w))
        if s1 == 0.5:
            d = r["daily"]
            print(f"== predictability, {pjm.WINDOW[0]}..{pjm.WINDOW[1]}: {len(d)} days, "
                  f"{int((d.spike_pm_h > 0).sum())} with any afternoon spike tier, {d.spike_pm_h.sum():.0f} afternoon spike-hours; "
                  f"top 10% of days hold {d.spike_pm_h.nlargest(int(len(d) * 0.1)).sum() / d.spike_pm_h.sum():.0%} of them")
            print(r["flags"].round(2).to_string())
            cp = r["cp"]
            print("\n== estimated 2026 capacity hours: 5 PJM RTO + 5 ComEd-zone daily peaks, weekdays, NERC holidays out")
            print(cp.assign(hour=cp["hour"].dt.strftime("%a %m-%d %H:00")).round(2).to_string(index=False))
            avg = float(cp.house_kw_measured.mean())
            print(f"10-hour mean house load {avg:.2f} kW (HVAC {cp.hvac_kw_measured.mean():.2f}, other "
                  f"{(cp.house_kw_measured - cp.hvac_kw_measured).mean():.2f}); PJM-5 {cp[cp.set == 'RTO'].house_kw_measured.mean():.2f}, "
                  f"ComEd-5 {cp[cp.set == 'CE'].house_kw_measured.mean():.2f}")
            alt = pjm.capacity_hours(exclude_july3=True)
            mains = pjm.house_mains_hourly()
            print(f"  with Fri Jul 3 excluded as a holiday: 10-hour mean {np.mean([mains.get(t) for t in alt.hour]):.2f} kW")
            print(f"billed kW = Capacity PLC x {pjm.BILLED_FACTOR:.4f}; today's {pjm.PLC_KW_NOW} billed kW "
                  f"-> Capacity PLC {pjm.PLC_KW_NOW / pjm.BILLED_FACTOR:.2f} kW")
            for k, v in pjm.projected_billed_kw(avg).items():
                print(f"  {k}: projected billed {v:.2f} kW -> ${v * pjm.CAPACITY_USD_PER_KW_MONTH:.2f}/mo vs "
                      f"${pjm.PLC_KW_NOW * pjm.CAPACITY_USD_PER_KW_MONTH:.2f}; "
                      f"${(pjm.PLC_KW_NOW - v) * pjm.CAPACITY_USD_PER_KW_MONTH * 12:,.0f}/yr lower at today's rate and factors")
            print("\n== what makes up the load at those hours (Refoss circuits)")
            print(pjm.circuit_breakdown(list(cp.hour)).round(2).to_string())
            ev = pjm.eagle_vs_mains()
            print("utility meter check:", {k: round(v, 3) for k, v in ev.items()})
            out["capacity_hours"] = cp.assign(hour=cp["hour"].astype(str)).to_dict(orient="records")
            out["eagle_vs_mains"] = ev
            out["flags"] = r["flags"].to_dict(orient="index")
        res = r["strategies"]
        out[f"stage1_{s1}"] = res.to_dict(orient="index")
        print(f"\n######## strategies with controller on, stage-1 = {s1:.0%}, {pjm.WINDOW[0]}..{pjm.WINDOW[1]} ########")
        print(res[["flagged_days", "bill_usd", "bill_vs_current", "kwh", "dtod_peak_kwh", "hvac_kw_at_peaks",
                   "billed_cap_kw", "capacity_usd_per_yr_vs_current"]].round(2).to_string())
        print(res[["afternoon_max_f_median", "h_above_78", "h_above_80", "morning_6_12_mean_f", "h_below_72",
                   "sleep_mean_f"]].round(1).to_string())
        prot = r["protocols"]
        out[f"protocols_stage1_{s1}"] = prot.to_dict(orient="index")
        print(f"\n-- selective capacity protocols on {r['protocol_flag_days']:.0f} weekdays flagged by ComEd day-before fcst >= 19 GW "
              "(billed kW assumes loss factor 1.05)")
        print(prot[["hvac_kw_at_peaks", "hvac_kw_at_peaks_max", "billed_cap_kw", "capacity_usd_per_yr_vs_current",
                    "energy_vs_current", "flagdays_max_f", "flagdays_h_above_80", "flagdays_3_6pm_mean_f",
                    "flagdays_morning_mean_f"]].round(2).to_string())
    _save("pjm", out)


def _fourway_metrics(tr: pd.DataFrame) -> dict[str, float]:
    sm = cf.summarize(tr)
    return {"bill_usd": sm["bill_usd"], "supply_usd": sm["cost_usd"], "delivery_usd": sm["delivery_usd"],
            "kwh": sm["kwh"], "dtod_peak_kwh": sm["dtod_peak_1_7pm_kwh"], **cf.excursions(tr)}


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m tools.thermal_model")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("pull", help="pull telemetry from pi-lab Influx into the local cache")
    pp.add_argument("--start", default="2026-05-26T00:00:00Z")
    pp.add_argument("--stop", default=pd.Timestamp.now(tz="UTC").floor("h").strftime("%Y-%m-%dT%H:%M:%SZ"))
    pp.add_argument("--refresh", action="store_true")
    sub.add_parser("fit", help="validation fit, monthly stability, fall/solar diagnosis")
    sub.add_parser("counterfactual", help="replay validation + steady-hold vs setback scenarios")
    sub.add_parser("validate", help="leave-one-month-out energy, peak events, pulldown rates")
    sub.add_parser("costcurve", help="cheapest cooling per afternoon cap (perfect / day-ahead / hourly-shape planners)")
    sub.add_parser("fourway", help="schedule x controller comparison with the rev 4.1 controller emulated on each schedule")
    sub.add_parser("pjm", help="PJM day-ahead/day-of forecasts: spike predictability, forecast-triggered strategies, 5CP")
    a = ap.parse_args()
    if a.cmd == "pull":
        data.pull(a.start, a.stop, a.refresh)
        data.load(rebuild=True)
        return
    w = data.load()
    {"fit": cmd_fit, "counterfactual": cmd_counterfactual, "validate": cmd_validate, "costcurve": cmd_costcurve,
     "fourway": cmd_fourway, "pjm": cmd_pjm}[a.cmd](w)


if __name__ == "__main__":
    main()
