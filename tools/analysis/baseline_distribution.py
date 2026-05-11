"""Produce the 6x6 weather-vector baseline covariance for the
Mahalanobis distance metric (Stage 4 of the pipeline).

Per EXPERIMENT_DESIGN.md §7 (as amended; see this PR's commit
message for the NOAA→ERA5 amendment):

    Distance metric: Mahalanobis distance (generalized point-to-
    point form) d²(x,y) = (x-y)ᵀ Σ⁻¹ (x-y) where Σ is the
    covariance matrix estimated from 2020-2025 ERA5 reanalysis at
    KORD coordinates, cooling-relevant weeks (CDD ≥ 5).

The output `data/baseline_cov.npz` contains:
  - cov:        6x6 covariance matrix (np.float64), components
                ordered per pipeline.WEATHER_VECTOR_COMPONENTS.
  - mean:       6-vector of component means (np.float64), for the
                point-to-distribution Mahalanobis outlier check.
  - n_weeks:    int, number of cooling-relevant weeks in the
                baseline.
  - source:     str describing the input data source.
  - components: str array of component names (for self-describing
                provenance; defensive against future ordering edits).

Two usage modes:
  python baseline_distribution.py --placeholder       # CI smoke
  python baseline_distribution.py --from-era5         # real lock

The real-lock path reads data/kord_era5_<year>.json files produced
by fetch_kord_era5.py. The placeholder path writes an identity
matrix and is gated by check_constants_locked.py.
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import sys
from pathlib import Path

import numpy as np

# Re-use the locked psychrometric enthalpy from the pipeline package
# so the weather-summary vector here matches Stage 3's computation
# byte-for-byte.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis.pipeline import (  # noqa: E402
    enthalpy_btu_per_lb,
    WEATHER_VECTOR_COMPONENTS,
)


HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"

COMPONENTS = WEATHER_VECTOR_COMPONENTS  # locked elsewhere; just re-bind


def emit_placeholder(out_path: Path) -> int:
    """Identity covariance — lets the pipeline run; not for filing."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cov = np.eye(len(COMPONENTS), dtype=np.float64)
    mean = np.zeros(len(COMPONENTS), dtype=np.float64)
    np.savez(
        out_path,
        cov=cov,
        mean=mean,
        n_weeks=np.int64(0),
        source=np.array(
            "PLACEHOLDER — replace before OSF lock", dtype=str,
        ),
        components=np.array(COMPONENTS, dtype=str),
    )
    print(f"wrote placeholder {out_path}")
    return 0


_HPA_TO_INHG = 0.02953


def _load_year(year: int) -> list[dict]:
    """Read one ERA5 yearly JSON, return a list of hourly dicts.

    Note on units: open-meteo ignores the `pressure_unit=inhg` query
    parameter and always returns `surface_pressure` in **hPa**. We
    convert to inHg here so the downstream enthalpy_btu_per_lb call
    (which expects inHg) gets the right value. 1 hPa = 0.02953 inHg.
    """
    path = DATA_DIR / f"kord_era5_{year}.json"
    if not path.exists():
        return []
    with open(path) as f:
        payload = json.load(f)
    h = payload["hourly"]
    n = len(h["time"])
    rows = []
    for i in range(n):
        pressure_raw = h["surface_pressure"][i]
        rows.append(
            {
                "time": datetime.datetime.fromisoformat(h["time"][i]),
                "temp_f": h["temperature_2m"][i],
                "dew_f": h["dew_point_2m"][i],
                "wind_mph": h["wind_speed_10m"][i],
                "solar_wm2": h["shortwave_radiation"][i],
                "pressure_inhg": (
                    pressure_raw * _HPA_TO_INHG
                    if pressure_raw is not None else None
                ),
            }
        )
    return rows


def _week_key(dt: datetime.datetime) -> tuple[int, int]:
    """ISO calendar (year, week) for a Mon-Sun bucket."""
    iso = dt.isocalendar()
    return (iso.year, iso.week)


def _compute_weekly_vector(rows: list[dict]) -> dict[str, float] | None:
    """Compute the 6-component weather-summary vector for one week
    of hourly rows.

    Returns None if the week is incomplete (<24 hours, or any
    missing required field) — those weeks are silently skipped
    from the baseline.
    """
    if len(rows) < 24:
        return None
    # All fields are required and present in ERA5 payloads; defensive
    # guard anyway:
    for r in rows:
        for k in ("temp_f", "dew_f", "wind_mph", "solar_wm2",
                  "pressure_inhg"):
            if r[k] is None:
                return None

    # CDD base 65: per day, max(daily_avg_temp - 65, 0); weekly = sum.
    by_day: dict[datetime.date, list[float]] = {}
    for r in rows:
        d = r["time"].date()
        by_day.setdefault(d, []).append(r["temp_f"])
    cdd = 0.0
    for d, temps in by_day.items():
        daily_avg = sum(temps) / len(temps)
        cdd += max(daily_avg - 65.0, 0.0)

    hourly_enthalpy = [
        enthalpy_btu_per_lb(r["temp_f"], r["dew_f"], r["pressure_inhg"])
        for r in rows
    ]

    return {
        "weekly_cdd": cdd,
        "mean_enthalpy_btu_lb": sum(hourly_enthalpy) / len(hourly_enthalpy),
        "total_solar_wh_m2": sum(r["solar_wm2"] for r in rows),
        "mean_wind_mph": sum(r["wind_mph"] for r in rows) / len(rows),
        "max_temp_f": max(r["temp_f"] for r in rows),
        "max_dewpoint_f": max(r["dew_f"] for r in rows),
    }


def compute_from_era5(out_path: Path, years: list[int],
                      min_cdd: float = 5.0) -> int:
    """Aggregate ERA5 hourly data into weekly vectors, filter to
    cooling-relevant (CDD ≥ min_cdd), compute covariance + mean.
    """
    all_hours: list[dict] = []
    years_seen: list[int] = []
    for y in years:
        rows = _load_year(y)
        if not rows:
            print(f"  skipping {y}: no data/kord_era5_{y}.json")
            continue
        years_seen.append(y)
        all_hours.extend(rows)
    if not all_hours:
        print(
            "ERROR: no yearly JSONs found in data/. Run "
            "fetch_kord_era5.py first.",
            file=sys.stderr,
        )
        return 1

    # Bucket by ISO week.
    by_week: dict[tuple[int, int], list[dict]] = {}
    for r in all_hours:
        key = _week_key(r["time"])
        by_week.setdefault(key, []).append(r)

    vectors: list[list[float]] = []
    for week_rows in by_week.values():
        v = _compute_weekly_vector(week_rows)
        if v is None:
            continue
        if v["weekly_cdd"] < min_cdd:
            continue
        vectors.append([v[c] for c in COMPONENTS])

    if len(vectors) < 6:
        print(
            f"ERROR: only {len(vectors)} cooling-relevant weeks "
            f"(CDD ≥ {min_cdd}) found. Need at least 6 for a "
            f"6x6 covariance.",
            file=sys.stderr,
        )
        return 1

    X = np.asarray(vectors, dtype=np.float64)
    cov = np.cov(X, rowvar=False)
    mean = X.mean(axis=0)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        out_path,
        cov=cov,
        mean=mean,
        n_weeks=np.int64(len(vectors)),
        source=np.array(
            f"ERA5 reanalysis at KORD (41.9786, -87.9047), "
            f"years {years_seen}, CDD>={min_cdd} filter",
            dtype=str,
        ),
        components=np.array(COMPONENTS, dtype=str),
    )
    print(f"wrote {out_path}")
    print(f"  cooling-relevant weeks (CDD >= {min_cdd}): {len(vectors)}")
    print(f"  years contributing: {years_seen}")
    print(f"  mean: {dict(zip(COMPONENTS, mean.round(2)))}")
    print(f"  std:  {dict(zip(COMPONENTS, np.sqrt(np.diag(cov)).round(2)))}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--placeholder", action="store_true",
                     help="Emit identity-matrix placeholder.")
    grp.add_argument("--from-era5", action="store_true",
                     help="Compute from data/kord_era5_<year>.json files.")
    ap.add_argument("--years", default="2020,2021,2022,2023,2024,2025",
                    help="Comma-separated years to include.")
    ap.add_argument("--min-cdd", type=float, default=5.0,
                    help="Cooling-relevance threshold (matches §4).")
    ap.add_argument("--out", type=Path,
                    default=DATA_DIR / "baseline_cov.npz")
    args = ap.parse_args()
    if args.placeholder:
        return emit_placeholder(args.out)
    years = [int(y) for y in args.years.split(",")]
    return compute_from_era5(args.out, years, args.min_cdd)


if __name__ == "__main__":
    raise SystemExit(main())
