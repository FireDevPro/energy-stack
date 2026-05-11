"""Fetch ERA5 reanalysis hourly weather at KORD coordinates for the
baseline-covariance computation.

Per EXPERIMENT_DESIGN.md §7 (amended from "NOAA Chicago (KORD)" to
"ERA5 reanalysis at KORD coordinates" as part of this PR): the
Mahalanobis Σ for matched-pair distance is estimated from
cooling-relevant weeks (CDD ≥ 5) over 2020-2025 at the KORD point
(O'Hare International Airport, lat 41.9786°N lon 87.9047°W).

Why ERA5 instead of multi-source NOAA stitching: ERA5 provides all
six required components (CDD basis, dewpoint, wind, solar, enthalpy
inputs) consistently at a single point. NOAA GHCND has TMAX/TMIN
but no dewpoint or solar; LCD has hourly temp/dewpoint/wind but no
solar; NSRDB has solar but separately gridded. Stitching three
sources doubles the code, the failure modes, and the timing skew.
ERA5 is bias-adjusted against the same station observations so the
weekly aggregates we compute are functionally equivalent for the
matched-pair distance metric (covariance matrix; absolute scale of
each component is normalized out).

Source: open-meteo Historical Archive API (ECMWF ERA5 reanalysis).
Free, no auth required, ~50,000 row payload comfortable per call.
Hourly fields:
  temperature_2m, dew_point_2m, wind_speed_10m, shortwave_radiation,
  surface_pressure
Units: Fahrenheit / mph / W/m² / inHg via API unit parameters.

Output: data/kord_era5_<year>.json — one file per calendar year,
the unmodified open-meteo response payload. The downstream
baseline_distribution.py aggregates hours → days → cooling-relevant
weeks → 6-component vectors → covariance.

Usage:
    python fetch_kord_era5.py --years 2020,2021,2022,2023,2024,2025
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path


HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
API = "https://archive-api.open-meteo.com/v1/archive"

# O'Hare International Airport (KORD). Standard published coordinates.
KORD_LAT = 41.9786
KORD_LON = -87.9047

PAUSE_S = 1.0  # polite delay between yearly fetches


def fetch_year(year: int) -> dict:
    params = {
        "latitude": KORD_LAT,
        "longitude": KORD_LON,
        "start_date": f"{year}-01-01",
        "end_date": f"{year}-12-31",
        "hourly": ",".join(
            (
                "temperature_2m",
                "dew_point_2m",
                "wind_speed_10m",
                "shortwave_radiation",
                "surface_pressure",
                "relative_humidity_2m",
            )
        ),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        # NOTE: open-meteo silently ignores pressure_unit; surface_pressure
        # is always returned in hPa. baseline_distribution.py converts
        # hPa -> inHg in its loader. Do not add a pressure_unit param
        # here under the impression it works.
        "timezone": "America/Chicago",
    }
    url = f"{API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "energy-proxy/baseline_cov"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--year", type=int)
    grp.add_argument("--years", help="comma-separated, e.g. 2020,2021,2022")
    args = ap.parse_args()
    years = [args.year] if args.year else [int(y) for y in args.years.split(",")]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(
        f"fetching ERA5 at KORD ({KORD_LAT}, {KORD_LON}) for {years}",
    )
    for i, year in enumerate(years):
        if i > 0:
            time.sleep(PAUSE_S)
        try:
            payload = fetch_year(year)
        except Exception as e:
            print(f"  {year}: FAILED — {e}", file=sys.stderr)
            return 1
        out_path = DATA_DIR / f"kord_era5_{year}.json"
        with open(out_path, "w") as f:
            json.dump(payload, f, indent=None, separators=(",", ":"))
        hours = len(payload.get("hourly", {}).get("time", []))
        print(f"  {year}: {hours:,} hourly records -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
