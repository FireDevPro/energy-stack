"""Replay-bundle inspection: turn `analysis/exports/<ts>/` into an audit artifact.

Usage::

    python -m tools.analysis.replay_inspect <run_dir>

Where ``<run_dir>`` is the pipeline output directory containing
``stage1/`` ... ``stage9/``. Emits a JSON summary on stdout and to
``<run_dir>/inspection.json``.

Per stage / output file the summary records:

- populated vs header-only
- row count
- reason codes (from ``<stage>/reason_report.json`` when present)
- per-row provenance source types (from ``stage1/manifest.json`` entries
  per measurement; from ``<stage>/provenance.json`` when present)
- sanity checks per stage where meaningful (nonzero kWh, nonzero
  price rows, nonzero Refoss mains rows, etc.)

The summary is structured so it can be diffed across runs (sorted
keys, stable numeric precision).

Designed as a read-only audit tool — never writes back into stage
directories, never touches the source bundle.
"""
from __future__ import annotations

import csv
import datetime
import json
import sys
from pathlib import Path
from typing import Any


# -------------------- low-level readers --------------------


def _safe_read_csv(path: Path) -> tuple[list[dict] | None, int]:
    """Return (rows or None, row_count). None when file is missing
    entirely; empty list when the file exists but is header-only."""
    if not path.exists():
        return None, 0
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    return rows, len(rows)


def _read_reason_report(stage_dir: Path) -> list[dict]:
    p = stage_dir / "reason_report.json"
    if not p.exists():
        return []
    with open(p) as f:
        data = json.load(f)
    return data.get("entries", [])


def _read_provenance(stage_dir: Path) -> dict:
    p = stage_dir / "provenance.json"
    if not p.exists():
        return {}
    with open(p) as f:
        return json.load(f)


# -------------------- per-stage inspectors --------------------


def _inspect_stage1(run_dir: Path) -> dict:
    """Stage 1: read manifest.json and summarize per-measurement entries."""
    stage_dir = run_dir / "stage1"
    out: dict[str, Any] = {
        "stage": "stage1",
        "exists": stage_dir.exists(),
    }
    if not stage_dir.exists():
        return out
    manifest_path = stage_dir / "manifest.json"
    if not manifest_path.exists():
        out["error"] = "manifest.json missing"
        return out
    with open(manifest_path) as f:
        manifest = json.load(f)
    out["export_window_start_ct"] = manifest.get("export_window_start_ct")
    out["export_window_end_ct"] = manifest.get("export_window_end_ct")
    out["source_bucket"] = manifest.get("source_bucket")
    out["entries"] = []
    for entry in manifest.get("entries", []):
        out["entries"].append({
            "measurement": entry["measurement"],
            "source_type": entry["source_type"],
            "row_count": entry["row_count"],
            "parquet_path": entry["parquet_path"],
            "field_set": list(entry.get("field_set", [])),
            "first_timestamp_utc": entry.get("first_timestamp_utc"),
            "last_timestamp_utc": entry.get("last_timestamp_utc"),
        })
    out["known_missing_measurements"] = [
        {
            "measurement": m["measurement"],
            "reason_code": m.get("reason_code"),
            "note": m.get("note"),
        }
        for m in manifest.get("known_missing_measurements", [])
    ]
    return out


def _inspect_csv_stage(
    run_dir: Path,
    stage_name: str,
    files: list[str],
    sanity_checks: dict[str, Any] | None = None,
) -> dict:
    """Generic per-stage CSV summary: populated / header-only / row count
    plus reason codes and (when provided) sanity-check results."""
    stage_dir = run_dir / stage_name
    out: dict[str, Any] = {
        "stage": stage_name,
        "exists": stage_dir.exists(),
        "outputs": {},
    }
    if not stage_dir.exists():
        return out
    for fname in files:
        rows, n = _safe_read_csv(stage_dir / fname)
        out["outputs"][fname] = {
            "exists": rows is not None,
            "populated": (rows is not None) and (n > 0),
            "row_count": n,
        }
    reasons = _read_reason_report(stage_dir)
    if reasons:
        out["reason_codes"] = [
            {"output_file": r["output_file"], "reason_code": r["reason_code"]}
            for r in reasons
        ]
    prov = _read_provenance(stage_dir)
    if prov:
        out["provenance"] = prov

    if sanity_checks:
        out["sanity"] = sanity_checks
    return out


# -------------------- sanity checks (per stage) --------------------


def _stage2_sanity(run_dir: Path) -> dict:
    """Sanity: at least one qualifying week has nonzero weekly_hvac_kwh,
    nonzero imputed-intervals coverage is reasonable, etc."""
    qual_path = run_dir / "stage2" / "qualifying_weeks.csv"
    if not qual_path.exists():
        return {"available": False}
    with open(qual_path) as f:
        rows = list(csv.DictReader(f))
    nonzero_kwh = sum(
        1 for r in rows
        if r.get("weekly_hvac_kwh") and float(r["weekly_hvac_kwh"]) > 0
    )
    qualifying = sum(1 for r in rows if r.get("qualifying", "").lower() in ("true", "1"))
    return {
        "available": True,
        "weeks_total": len(rows),
        "weeks_with_nonzero_weekly_hvac_kwh": nonzero_kwh,
        "weeks_qualifying": qualifying,
    }


def _stage3_sanity(run_dir: Path) -> dict:
    weekly_path = run_dir / "stage3" / "weekly.csv"
    if not weekly_path.exists():
        return {"available": False}
    with open(weekly_path) as f:
        rows = list(csv.DictReader(f))
    nonzero_o1 = sum(
        1 for r in rows
        if r.get("o1_dollars_per_cdd") and float(r["o1_dollars_per_cdd"]) > 0
    )
    nonzero_cdd = sum(
        1 for r in rows
        if r.get("weekly_cdd") and float(r["weekly_cdd"]) > 0
    )
    return {
        "available": True,
        "weeks_total": len(rows),
        "weeks_with_nonzero_o1_dollars_per_cdd": nonzero_o1,
        "weeks_with_nonzero_cdd": nonzero_cdd,
    }


def _stage6_sanity(run_dir: Path) -> dict:
    stage_dir = run_dir / "stage6"
    if not stage_dir.exists():
        return {"available": False}
    out: dict[str, Any] = {"available": True}
    layer1_path = stage_dir / "o2_layer1.csv"
    if layer1_path.exists():
        with open(layer1_path) as f:
            rows = list(csv.DictReader(f))
        if rows:
            r = rows[0]
            out["layer1_n_peaks_arm_a"] = int(r.get("n_peaks_arm_a", 0))
            out["layer1_n_peaks_arm_b"] = int(r.get("n_peaks_arm_b", 0))
            out["layer1_delta_kw"] = float(r.get("delta_kw", 0))
    detector_path = stage_dir / "detector_accuracy.csv"
    if detector_path.exists():
        with open(detector_path) as f:
            rows = list(csv.DictReader(f))
        if rows:
            by_scope = {r["scope"]: r for r in rows}
            out["detector_scopes_emitted"] = sorted(by_scope.keys())
            for scope in ("rto", "comed_zone", "combined_any"):
                if scope in by_scope:
                    out[f"detector_{scope}_tp"] = int(by_scope[scope]["tp"])
                    out[f"detector_{scope}_summer_hours_n"] = int(
                        by_scope[scope]["summer_hours_n"]
                    )
    return out


def _stage1_data_sanity(run_dir: Path) -> dict:
    """Quick check that the parquet files actually contain rows for
    the measurements claimed by the manifest. Catches the case where
    the manifest is well-formed but every measurement returned zero
    rows in the window."""
    try:
        import pandas as pd
    except ImportError:
        return {"available": False, "reason": "pandas not installed"}
    manifest_path = run_dir / "stage1" / "manifest.json"
    if not manifest_path.exists():
        return {"available": False, "reason": "no manifest.json"}
    with open(manifest_path) as f:
        manifest = json.load(f)
    out: dict[str, Any] = {"available": True, "per_measurement": {}}
    for entry in manifest.get("entries", []):
        parquet_file = run_dir / "stage1" / entry["parquet_path"]
        if not parquet_file.exists():
            out["per_measurement"][entry["measurement"]] = {
                "exists": False,
            }
            continue
        df = pd.read_parquet(parquet_file)
        info: dict[str, Any] = {
            "exists": True,
            "row_count": int(len(df)),
            "field_set": sorted(df["_field"].unique().tolist()) if "_field" in df.columns else [],
            "source_type": entry["source_type"],
        }
        # Per-measurement sanity heuristics.
        m = entry["measurement"]
        if m == "comed.prices" and "_field" in df.columns:
            # Production field: price_cents_per_kwh. Filter to 5min
            # rows for parity with the Stage 2/3 loaders.
            mask = df["_field"] == "price_cents_per_kwh"
            if "period_type" in df.columns:
                mask = mask & (df["period_type"] == "5min")
            price_rows = df[mask]
            info["price_rows_5min"] = int(len(price_rows))
            info["nonzero_price_rows"] = int(
                (price_rows["_value"].astype(float) > 0).sum()
            )
        if m == "refoss.channel" and {"_field", "channel"}.issubset(df.columns):
            mains = df[
                (df["_field"] == "power_w")
                & (df["channel"].isin(["em:1", "em:7"]))
            ]
            info["nonzero_mains_power_w_rows"] = int(
                (mains["_value"].astype(float) > 0).sum()
            )
            hvac = df[
                (df["_field"] == "power_w")
                & (df["channel"].isin(["em:2", "em:8", "em:9"]))
            ]
            info["nonzero_hvac_power_w_rows"] = int(
                (hvac["_value"].astype(float) > 0).sum()
            )
        out["per_measurement"][m] = info
    return out


# -------------------- top-level --------------------


def inspect(run_dir: Path) -> dict:
    """Build the audit summary dict for a pipeline run directory."""
    summary: dict[str, Any] = {
        "run_dir": str(run_dir),
        "inspected_at_utc": datetime.datetime.now(
            datetime.timezone.utc,
        ).isoformat(),
        "stages": {},
    }
    summary["stages"]["stage1"] = _inspect_stage1(run_dir)
    summary["stages"]["stage1_data_sanity"] = _stage1_data_sanity(run_dir)
    summary["stages"]["stage2"] = _inspect_csv_stage(
        run_dir, "stage2",
        ["qualifying_weeks.csv", "imputed_intervals.csv", "outages.csv"],
        sanity_checks=_stage2_sanity(run_dir),
    )
    summary["stages"]["stage3"] = _inspect_csv_stage(
        run_dir, "stage3", ["weekly.csv"],
        sanity_checks=_stage3_sanity(run_dir),
    )
    summary["stages"]["stage4"] = _inspect_csv_stage(
        run_dir, "stage4", ["matched_pairs.csv", "unmatched_weeks.csv"],
    )
    summary["stages"]["stage5"] = _inspect_csv_stage(
        run_dir, "stage5", ["effects.csv", "pair_diffs.csv"],
    )
    summary["stages"]["stage6"] = _inspect_csv_stage(
        run_dir, "stage6",
        ["o2_layer1.csv", "o2_layer2.csv",
         "o2_layer3.csv", "detector_accuracy.csv"],
        sanity_checks=_stage6_sanity(run_dir),
    )
    summary["stages"]["stage7"] = _inspect_csv_stage(
        run_dir, "stage7", ["sced_pvalues.csv"],
    )
    summary["stages"]["stage8"] = _inspect_csv_stage(
        run_dir, "stage8", ["decomposition.csv", "layer_attribution.csv"],
    )
    summary["stages"]["stage9"] = _inspect_csv_stage(
        run_dir, "stage9",
        ["effects_like.csv", "day_of_week.csv", "threshold_robustness.csv"],
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="python -m tools.analysis.replay_inspect",
        description=__doc__,
    )
    ap.add_argument("run_dir", type=Path, help="Pipeline run output directory")
    ap.add_argument(
        "--out", type=Path, default=None,
        help="Where to write inspection.json (default: <run_dir>/inspection.json)",
    )
    args = ap.parse_args(argv)
    run_dir = args.run_dir
    if not run_dir.exists():
        print(f"run_dir not found: {run_dir}", file=sys.stderr)
        return 1
    summary = inspect(run_dir)
    out_path = args.out or (run_dir / "inspection.json")
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2, sort_keys=True)
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"\nwrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
