"""Fetch ComEd Hourly Pricing 5-minute RTP for given years.

Pulls from the public endpoint
  https://hourlypricing.comed.com/api?type=5minutefeed&datestart=...&dateend=...&format=text
and writes one concatenated file per year to data/rtp_<year>.txt in the
same `unixMillis:cents,...` format the threshold-analysis bundle uses.

The 2025 files already in tools/comed_2025_analysis/data/ were pulled by
hand; this script generalizes the procedure so 2023 and 2024 (needed
for the spread-constant lock) can be fetched programmatically.

Usage:
    python fetch_rtp.py --years 2023,2024
    python fetch_rtp.py --year 2024 --months 6,7,8,9
"""
from __future__ import annotations

import argparse
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path


HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
API = "https://hourlypricing.comed.com/api"
PAUSE_S = 1.0  # polite delay between month fetches


def fetch_month(year: int, month: int) -> str:
    """Pull one month of ComEd RTP 5-min prints; return concatenated text."""
    # Inclusive start at month-day-1 00:00, exclusive end at next-month-day-1 00:00
    if month == 12:
        end_year, end_month = year + 1, 1
    else:
        end_year, end_month = year, month + 1
    params = {
        "type": "5minutefeed",
        "datestart": f"{year:04d}{month:02d}010000",
        "dateend": f"{end_year:04d}{end_month:02d}010000",
        "format": "text",
    }
    url = f"{API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "energy-proxy/comed_price_imputation"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


def fetch_year(year: int, months: list[int]) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / f"rtp_{year}.txt"
    pieces: list[str] = []
    for m in months:
        text = fetch_month(year, m).strip()
        if not text:
            print(f"  {year}-{m:02d}: empty response", file=sys.stderr)
            continue
        pieces.append(text)
        print(f"  {year}-{m:02d}: {len(text):,} bytes", file=sys.stderr)
        time.sleep(PAUSE_S)
    combined = ",".join(pieces)
    out_path.write_text(combined, encoding="utf-8")
    n_prints = combined.count(":")
    print(f"  -> {out_path} ({n_prints:,} 5-min prints)")
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--year", type=int)
    grp.add_argument("--years", help="comma-separated, e.g. 2023,2024")
    ap.add_argument(
        "--months", default="5,6,7,8,9",
        help="Comma-separated month numbers; default May-Sep "
             "(matches the locked cooling-season window).",
    )
    args = ap.parse_args()
    years = [args.year] if args.year else [int(y) for y in args.years.split(",")]
    months = [int(m) for m in args.months.split(",")]
    print(f"fetching ComEd RTP: years={years} months={months}")
    for y in years:
        fetch_year(y, months)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
