"""Fetch PJM day-ahead LMP at the COMED zone for given summer years.

Runs ONCE at OSF lock to produce data/lmp_<year>.csv files that
compute_spread.py consumes.

Implementation cribs from deploy/energy-stack/scripts/backfill_pjm.py
(the production backfiller): single API call per calendar year using
the `<start>to<end>` range filter, 11-second pacing between calls
(6 calls/min ceiling of the free Non-Member tier), and
`pnode_id=33092371` for the COMED zonal aggregator.

PJM archive boundary: da_hrl_lmps splits at 731 days. Years entirely
inside the standard window query cleanly with the pnode_id filter;
older years are in the archive tier (filter by type=Zone, parse
client-side). For OSF-lock spread derivation we currently target
2024-2025 — both inside the standard window as of mid-May 2026.
Add archive support if 2023 is needed.

Authentication: free Non-Member key at https://apiportal.pjm.com/.
Set PJM_DM2_API_KEY in the environment.

Usage:
    python fetch_lmp.py --years 2024,2025
    python fetch_lmp.py --year 2024
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
PJM_API_BASE = "https://api.pjm.com/api/v1"
COMED_PNODE_ID = 33092371
EPT = ZoneInfo("America/New_York")
RATE_LIMIT_PAUSE_S = 11   # 6 calls/min — matches backfill_pjm.py
ROW_COUNT_PER_CALL = 10000  # ~8,760 hours/year fits comfortably
ARCHIVE_CUTOFF_DAYS = 731


def fetch_year(api_key: str, year: int, months: set[int]) -> list[dict]:
    """One API call: fetch the chosen-month window of COMED zone DA LMP.

    Uses the earliest-to-latest selected-month boundary as the API
    range so 2024 summer (post-archive-cutoff) queries cleanly.
    Raises if any part of the requested window is older than the
    archive cutoff.
    """
    cutoff = datetime.datetime.now(EPT).date() - timedelta(days=ARCHIVE_CUTOFF_DAYS)
    first_month = min(months)
    last_month = max(months)
    window_start = datetime.date(year, first_month, 1)
    if last_month == 12:
        window_end_exclusive = datetime.date(year + 1, 1, 1)
    else:
        window_end_exclusive = datetime.date(year, last_month + 1, 1)
    if window_start < cutoff:
        raise SystemExit(
            f"year {year} window starts {window_start.isoformat()} which is "
            f"older than the {ARCHIVE_CUTOFF_DAYS}-day archive cutoff "
            f"({cutoff.isoformat()}). Archive-tier query (type=Zone) "
            f"not implemented in this script. See "
            f"deploy/energy-stack/scripts/backfill_pjm.py archive_cutoff_days."
        )
    start = f"{window_start.isoformat()}T00:00:00.0"
    end = f"{(window_end_exclusive - timedelta(days=1)).isoformat()}T23:00:00.0"
    params = {
        "datetime_beginning_ept": f"{start}to{end}",
        "rowCount": ROW_COUNT_PER_CALL,
        "startRow": 1,
        "pnode_id": str(COMED_PNODE_ID),
    }
    qs = urllib.parse.urlencode(params)
    url = f"{PJM_API_BASE}/da_hrl_lmps?{qs}"
    req = urllib.request.Request(url, headers={"Ocp-Apim-Subscription-Key": api_key})
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read())
    items = payload.get("items") or []
    total = payload.get("totalRows", 0)
    if total > len(items):
        raise RuntimeError(
            f"year {year}: totalRows={total} but rowCount={ROW_COUNT_PER_CALL} "
            f"only returned {len(items)}; need pagination support"
        )
    return items


def write_year_csv(items: list[dict], year: int, months: set[int]) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / f"lmp_{year}.csv"
    fields = ("datetime_beginning_ept", "total_lmp_da")
    n_written = 0
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for it in items:
            ts_str = it.get("datetime_beginning_ept", "")
            if not ts_str:
                continue
            try:
                month = datetime.datetime.fromisoformat(ts_str).month
            except ValueError:
                continue
            if month not in months:
                continue
            w.writerow({k: it.get(k, "") for k in fields})
            n_written += 1
    print(f"  -> {out_path} ({n_written} rows in months {sorted(months)})")
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--year", type=int)
    grp.add_argument("--years", help="comma-separated, e.g. 2024,2025")
    ap.add_argument(
        "--months", default="6,7,8,9",
        help="Comma-separated month numbers; default Jun-Sep "
             "(matches EXPERIMENT_DESIGN.md §4 Rule 3 'summers'). "
             "May added if and only if archive support lands.",
    )
    args = ap.parse_args()
    api_key = os.environ.get("PJM_DM2_API_KEY", "").strip()
    if not api_key:
        print(
            "ERROR: set PJM_DM2_API_KEY in env. Free key at "
            "https://apiportal.pjm.com/",
            file=sys.stderr,
        )
        return 1
    years = [args.year] if args.year else [int(y) for y in args.years.split(",")]
    months = {int(m) for m in args.months.split(",")}
    print(f"fetching PJM DA-LMP at COMED: years={years} months={sorted(months)}")
    for i, y in enumerate(years):
        if i > 0:
            time.sleep(RATE_LIMIT_PAUSE_S)
        items = fetch_year(api_key, y, months)
        print(f"  year {y}: {len(items)} rows total returned")
        write_year_csv(items, y, months)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
