"""Fetch PJM day-ahead LMP at the COMED zone for given summer years.

Runs ONCE at OSF lock to produce data/lmp_<year>.csv files that
compute_spread.py consumes.

PJM Data Miner 2 endpoint:
    da_hrl_lmps  — Day-Ahead Hourly LMPs (zone-aggregated)
    parameters:
      datetime_beginning_ept >= start, < end
      pnode_name = 'COMED'   (or pnode_id=33092371 for the COMED zone)
      type = 'ZONE'
    output: CSV with at minimum 'datetime_beginning_ept', 'total_lmp_da'

Authentication: this tier of PJM DM2 is accessible with a free API
key. Set PJM_DM2_API_KEY in the environment.

Usage:
    python fetch_lmp.py --years 2023,2024,2025
    python fetch_lmp.py --year 2024
"""
from __future__ import annotations

import argparse
import csv
import datetime
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path


HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
DM2_BASE = "https://api.pjm.com/api/v1/da_hrl_lmps"
PAGE_SIZE = 50_000


def fetch_year(year: int) -> int:
    """Fetch full summer (Jun 1 - Sep 30) DA LMP for COMED zone.

    Writes data/lmp_<year>.csv with columns datetime_beginning_ept,
    total_lmp_da. Returns the row count.
    """
    api_key = os.environ.get("PJM_DM2_API_KEY")
    if not api_key:
        print(
            "ERROR: set PJM_DM2_API_KEY in env. Free key at "
            "https://dataminer2.pjm.com/list",
            file=sys.stderr,
        )
        return 0

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / f"lmp_{year}.csv"

    start = f"{year}-06-01T00:00:00"
    end = f"{year}-10-01T00:00:00"
    rows: list[dict[str, str]] = []
    start_row = 1
    while True:
        params = {
            "rowCount": str(PAGE_SIZE),
            "startRow": str(start_row),
            "format": "csv",
            "datetime_beginning_ept": f"{start} to {end}",
            "type": "ZONE",
            "pnode_name": "COMED",
        }
        url = DM2_BASE + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Ocp-Apim-Subscription-Key": api_key})
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode("utf-8")
        if not text.strip():
            break
        reader = csv.DictReader(text.splitlines())
        page_rows = list(reader)
        if not page_rows:
            break
        rows.extend(page_rows)
        if len(page_rows) < PAGE_SIZE:
            break
        start_row += PAGE_SIZE

    if not rows:
        print(f"  year {year}: no rows returned", file=sys.stderr)
        return 0

    fields = ("datetime_beginning_ept", "total_lmp_da")
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})

    print(f"  year {year}: wrote {len(rows)} rows -> {out_path}")
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--year", type=int)
    grp.add_argument("--years", help="comma-separated, e.g. 2023,2024,2025")
    args = ap.parse_args()
    years = [args.year] if args.year else [int(y) for y in args.years.split(",")]
    total = 0
    for y in years:
        total += fetch_year(y)
    print(f"done. total rows: {total}")
    return 0 if total > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
