"""Produce the 6x6 weather-vector baseline covariance for the
Mahalanobis distance metric (Stage 4 of the pipeline).

Per EXPERIMENT_DESIGN.md §7:

    Distance metric: Mahalanobis distance (generalized point-to-point
    form) d²(x,y) = (x-y)ᵀ Σ⁻¹ (x-y) where Σ is the covariance matrix
    estimated from 2020-2025 NOAA Chicago (KORD) cooling-relevant
    weeks (CDD ≥ 5).

The output `data/baseline_cov.npz` contains:
  - cov: 6x6 covariance matrix (np.float64), components ordered per
    pipeline.WEATHER_VECTOR_COMPONENTS.
  - mean: 6-vector of component means (np.float64), used for the
    point-to-distribution Mahalanobis outlier check.
  - n_weeks: int, number of cooling-relevant weeks in the baseline.
  - source: str describing the input data source.

The shipped npz in this PR is a placeholder identity-matrix
covariance so the pipeline runs end-to-end. The real value is
computed by this script against the NOAA daily summaries before
OSF filing.

Usage (real run, before OSF lock):
    python baseline_distribution.py --noaa-csv path/to/noaa_kord_2020_2025.csv

Usage (CI placeholder generation; default):
    python baseline_distribution.py --placeholder
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np


HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"

COMPONENTS = (
    "weekly_cdd",
    "mean_enthalpy_btu_lb",
    "total_solar_wh_m2",
    "mean_wind_mph",
    "max_temp_f",
    "max_dewpoint_f",
)


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
        source=np.array("PLACEHOLDER — replace before OSF lock", dtype=str),
    )
    print(f"wrote placeholder {out_path}")
    return 0


def compute_from_noaa(csv_path: Path, out_path: Path) -> int:
    """Real-data path. To be implemented when NOAA data is acquired."""
    raise NotImplementedError(
        "Real NOAA baseline computation lands in a follow-up PR (or "
        "fill in here against the data export from "
        "https://www.ncei.noaa.gov/cdo-web/datasets/GHCND/stations/USW00094846/detail "
        "before OSF lock)."
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--noaa-csv", type=Path, default=None,
                    help="Path to NOAA daily-summary CSV (2020-2025).")
    ap.add_argument("--placeholder", action="store_true",
                    help="Emit identity-matrix placeholder.")
    ap.add_argument("--out", type=Path, default=DATA_DIR / "baseline_cov.npz")
    args = ap.parse_args()
    if args.placeholder or args.noaa_csv is None:
        return emit_placeholder(args.out)
    return compute_from_noaa(args.noaa_csv, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
