"""Compute the month-matched (RTP − day-ahead LMP) median spread.

Per EXPERIMENT_DESIGN.md §4 Rule 3 (locked):

    Hours below 6/12 5-minute prints are filled from the day-ahead
    PJM LMP at COMED, adjusted by the historical month-matched
    median (RTP - day-ahead LMP) spread computed once at OSF lock
    from public PJM data over summers 2023-2025.

This script reads:
  * data/rtp_<year>.txt   — ComEd RTP 5-minute prints (one file per year-month, concatenated)
  * data/lmp_<year>.csv   — PJM DA LMP at COMED zone, hour-beginning EPT

And writes:
  * spread_constants.json (locked output consumed by the pipeline)

The shipped JSON in this PR is a placeholder (PLACEHOLDER: true).
This script must run on the real 2023-2025 data and overwrite the
placeholder before OSF filing. See README.md.
"""
from __future__ import annotations

import csv
import datetime
import json
import os
from collections import defaultdict
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo


CT = ZoneInfo("America/Chicago")
EPT = ZoneInfo("America/New_York")  # PJM publishes DA LMP in EPT
HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"


def parse_rtp_file(path: Path) -> list[tuple[datetime.datetime, float]]:
    """Parse ComEd RTP text format: 'unixMillis:cents,unixMillis:cents,...'.

    Returns (hour-beginning CT datetime, cents) hourly averages.
    """
    with open(path) as f:
        raw = f.read().strip()
    records: list[tuple[datetime.datetime, float]] = []
    for pair in raw.split(","):
        if ":" not in pair:
            continue
        millis_str, cents_str = pair.split(":")
        ts = datetime.datetime.fromtimestamp(
            int(millis_str) / 1000.0, tz=datetime.timezone.utc
        ).astimezone(CT)
        records.append((ts, float(cents_str)))
    # Aggregate to hourly average
    hourly: dict[datetime.datetime, list[float]] = defaultdict(list)
    for ts, c in records:
        h = ts.replace(minute=0, second=0, microsecond=0)
        hourly[h].append(c)
    out = []
    for h in sorted(hourly):
        prints = hourly[h]
        if len(prints) >= 6:  # rule 3 acceptance threshold
            out.append((h, sum(prints) / len(prints)))
    return out


def parse_lmp_csv(path: Path) -> dict[datetime.datetime, float]:
    """Parse PJM DA-LMP CSV: columns 'datetime_beginning_ept', 'total_lmp_da'.

    Returns hour-beginning CT -> $/MWh.
    """
    out: dict[datetime.datetime, float] = {}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            t_ept = datetime.datetime.fromisoformat(
                row["datetime_beginning_ept"]
            ).replace(tzinfo=EPT)
            t_ct = t_ept.astimezone(CT)
            out[t_ct.replace(minute=0, second=0, microsecond=0)] = float(
                row["total_lmp_da"]
            )
    return out


def compute_for_years(years: list[int]) -> dict[int, float]:
    """For each month 6..9, compute the median (RTP - LMP) spread in
    cents/kWh across all available hours in `years`."""
    by_month: dict[int, list[float]] = defaultdict(list)
    total_hours = 0
    for year in years:
        rtp_path = DATA_DIR / f"rtp_{year}.txt"
        lmp_path = DATA_DIR / f"lmp_{year}.csv"
        if not (rtp_path.exists() and lmp_path.exists()):
            print(
                f"  skipping {year}: missing {rtp_path.name} or "
                f"{lmp_path.name}"
            )
            continue
        rtp = parse_rtp_file(rtp_path)
        lmp = parse_lmp_csv(lmp_path)
        for hour, rtp_cents in rtp:
            if hour.month not in (6, 7, 8, 9):
                continue
            lmp_per_mwh = lmp.get(hour)
            if lmp_per_mwh is None:
                continue
            lmp_cents = lmp_per_mwh / 10.0  # $/MWh -> ¢/kWh
            by_month[hour.month].append(rtp_cents - lmp_cents)
            total_hours += 1
    return {
        m: round(median(by_month[m]), 4)
        for m in sorted(by_month)
        if by_month[m]
    }, total_hours


def main() -> int:
    years = [2023, 2024, 2025]
    print(f"computing spread for years {years} from {DATA_DIR}")
    medians, total_hours = compute_for_years(years)
    if not medians:
        print("ERROR: no valid input data found. See README.md for "
              "the data-acquisition step (fetch_lmp.py).")
        return 1

    out = {
        "computed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "input_years": years,
        "input_hours_total": total_hours,
        "median_spread_cents_per_kwh_by_month": {
            str(m): v for m, v in medians.items()
        },
        "PLACEHOLDER": False,
    }
    out_path = HERE / "spread_constants.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"wrote {out_path}")
    for m, v in medians.items():
        print(f"  month {m}: median spread = {v:+.4f} ¢/kWh")
    print(f"  total hours: {total_hours}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
