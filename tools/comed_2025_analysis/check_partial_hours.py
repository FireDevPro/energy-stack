"""One-shot sensitivity check: re-derive the headline numbers under
both the production '>=6 of 12' inclusion rule and a strict '12 of 12'
filter. Confirms (or refutes) that the locked thresholds are robust
to the partial-hour rule choice.

Not part of the binding analysis. Run on demand to support the
audit-bundle's partial-hours documentation in README.md.
"""
from __future__ import annotations

import datetime
import json
from collections import defaultdict
from pathlib import Path
from statistics import median, mean, quantiles
from zoneinfo import ZoneInfo


CT = ZoneInfo("America/Chicago")
DATA_DIR = Path(__file__).resolve().parent / "data"


def parse_price_file(path: Path):
    raw = path.read_text().strip()
    out = []
    for pair in raw.split(","):
        if ":" not in pair:
            continue
        millis_str, cents_str = pair.split(":")
        ts = datetime.datetime.fromtimestamp(
            int(millis_str) / 1000, tz=datetime.timezone.utc
        ).astimezone(CT)
        out.append((ts, float(cents_str)))
    return out


def summarize(min_prints: int) -> dict:
    all_5min: list[tuple[datetime.datetime, float]] = []
    for fname in ("may2025.txt", "jun2025.txt", "jul2025.txt", "aug2025.txt", "sep2025.txt"):
        all_5min.extend(parse_price_file(DATA_DIR / fname))
    hourly: dict[datetime.datetime, list[float]] = defaultdict(list)
    for ts, p in all_5min:
        hourly[ts.replace(minute=0, second=0, microsecond=0)].append(p)
    n_total_hours = len(hourly)
    n_with_12 = sum(1 for v in hourly.values() if len(v) == 12)
    n_with_6_to_11 = sum(1 for v in hourly.values() if 6 <= len(v) < 12)
    n_below_6 = sum(1 for v in hourly.values() if len(v) < 6)
    included = {k: mean(v) for k, v in hourly.items() if len(v) >= min_prints}
    prices = sorted(included.values())
    # Per-month load
    daily_scarcity = defaultdict(lambda: {"prices": []})
    for h, p in included.items():
        if 6 <= h.month <= 9:
            daily_scarcity[h.date()]["prices"].append(p)
    scarcity_days = sum(
        1 for v in daily_scarcity.values() if any(p >= 20 for p in v["prices"])
    )
    return {
        "min_prints": min_prints,
        "n_total_hours_seen": n_total_hours,
        "n_with_12_prints": n_with_12,
        "n_with_6_to_11_prints": n_with_6_to_11,
        "n_below_6_prints": n_below_6,
        "n_included_hours": len(prices),
        "n_excluded_hours": n_total_hours - len(prices),
        "p95_cents": round(quantiles(prices, n=20)[18], 2),
        "p99_cents": round(quantiles(prices, n=100)[98], 2),
        "max_cents": round(max(prices), 2),
        "scarcity_days_jun_sep": scarcity_days,
    }


def main() -> int:
    print("partial-hour inclusion sensitivity check")
    print(f"  data dir: {DATA_DIR}")
    print()
    for rule in (6, 12):
        s = summarize(rule)
        print(f"--- min_prints = {rule} of 12 ---")
        for k, v in s.items():
            print(f"  {k:32s} {v}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
