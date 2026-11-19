"""CLI: python -m tools.thermal_model {pull,fit,counterfactual}  (run from the repo root)."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from typing import Any

import pandas as pd

from . import counterfactual as cf
from . import data
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
        }
        out[tag]["replay"] = rv
        print(f"replay {start}..{SEASON[1]}: sim {rv['sim_kwh']:.0f} kWh / ${rv['sim_cost']:.0f}  vs measured "
              f"{rv['meas_kwh']:.0f} kWh / ${rv['meas_cost']:.0f};  daily r={rv['daily_corr']:.2f}, "
              f"daily MAE {rv['daily_mae_kwh']:.1f} kWh, indoor RMSE {rv['indoor_rmse_f']:.2f}F")

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
        cols = ["kwh", "kwh_vs_program", "cost_usd", "cost_vs_program", "bill_usd", "bill_vs_program", "avg_price_paid_c", "peak_2_7pm_kwh",
                "hot_day_kwh_per_day", "sleep_mean_f", "afternoon_max_f_median", "hours_above_78"]
        print(f"scenarios {SEASON[0]}..{SEASON[1]} (actual weather + ComEd prices):")
        print(df[cols].round(1).to_string())
    _save("counterfactual", out)


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m tools.thermal_model")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("pull", help="pull telemetry from pi-lab Influx into the local cache")
    pp.add_argument("--start", default="2026-05-26T00:00:00Z")
    pp.add_argument("--stop", default=pd.Timestamp.now(tz="UTC").floor("h").strftime("%Y-%m-%dT%H:%M:%SZ"))
    pp.add_argument("--refresh", action="store_true")
    sub.add_parser("fit", help="validation fit, monthly stability, fall/solar diagnosis")
    sub.add_parser("counterfactual", help="replay validation + steady-hold vs setback scenarios")
    a = ap.parse_args()
    if a.cmd == "pull":
        data.pull(a.start, a.stop, a.refresh)
        data.load(rebuild=True)
        return
    w = data.load()
    {"fit": cmd_fit, "counterfactual": cmd_counterfactual}[a.cmd](w)


if __name__ == "__main__":
    main()
