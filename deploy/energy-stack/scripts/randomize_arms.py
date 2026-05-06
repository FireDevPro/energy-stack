#!/usr/bin/env python3
"""Generate the pre-committed Arm A / Arm B week-level assignment for the
residential HVAC controls field study (EXPERIMENT_DESIGN.md).

Block-of-2 randomization: every consecutive pair of weeks contains one Arm A
week and one Arm B week, with the order within the block determined by the
seed. Guarantees within-month balance and prevents long single-arm runs.
A trailing odd week (if the date range yields an odd week count) is assigned
independently from the same RNG stream.

This script is the binding artifact behind the OSF pre-registration. The
seed and the algorithm are pre-committed; running with the same seed and
date range MUST produce the same CSV. Verified deterministic across
CPython 3.10+ (Python's random.Random.shuffle / .choice are stable across
3.x releases for a fixed seed).

Default seed `20260601` is the date of pre-registration commitment per
EXPERIMENT_DESIGN.md §13.

Usage:
    python randomize_arms.py
    python randomize_arms.py --seed 20260602 --start 2027-06-01 --end 2027-09-30
    python randomize_arms.py --output /tmp/assignments.csv

Output (CSV, header included):
    iso_week,monday_date,arm
    2026-W23,2026-06-01,A
    2026-W24,2026-06-08,B
    ...
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


@dataclass(frozen=True)
class WeekAssignment:
    iso_week: str       # e.g. "2026-W23"
    monday_date: date   # the Monday that starts the week (local CT)
    arm: str            # "A" or "B"


def _first_monday_on_or_after(d: date) -> date:
    return d + timedelta(days=(7 - d.weekday()) % 7)


def _iter_mondays(start_inclusive: date, end_inclusive: date) -> list[date]:
    """All Mondays whose 7-day week intersects [start_inclusive, end_inclusive]."""
    first = _first_monday_on_or_after(start_inclusive)
    out: list[date] = []
    cur = first
    while cur <= end_inclusive:
        out.append(cur)
        cur += timedelta(days=7)
    return out


def _format_iso_week(d: date) -> str:
    iso_year, iso_week, _ = d.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def generate_assignments(
    seed: int,
    start: date,
    end: date,
) -> list[WeekAssignment]:
    """Block-of-2 randomized A/B assignment for each Monday-anchored week
    intersecting [start, end]. Deterministic given the seed."""
    rng = random.Random(seed)
    mondays = _iter_mondays(start, end)
    assignments: list[WeekAssignment] = []

    i = 0
    while i + 1 < len(mondays):
        block = ["A", "B"]
        rng.shuffle(block)
        for monday, arm in zip(mondays[i : i + 2], block):
            assignments.append(WeekAssignment(_format_iso_week(monday), monday, arm))
        i += 2

    if i < len(mondays):
        monday = mondays[i]
        arm = rng.choice(["A", "B"])
        assignments.append(WeekAssignment(_format_iso_week(monday), monday, arm))

    return assignments


def write_csv(assignments: list[WeekAssignment], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["iso_week", "monday_date", "arm"])
        for a in assignments:
            writer.writerow([a.iso_week, a.monday_date.isoformat(), a.arm])


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=20260601,
                    help="Random seed (default: 20260601, pre-committed in EXPERIMENT_DESIGN.md §13)")
    ap.add_argument("--start", type=date.fromisoformat, default=date(2026, 6, 1),
                    help="Start date inclusive, ISO YYYY-MM-DD (default: 2026-06-01)")
    ap.add_argument("--end", type=date.fromisoformat, default=date(2026, 9, 30),
                    help="End date inclusive, ISO YYYY-MM-DD (default: 2026-09-30)")
    ap.add_argument("--output", type=Path, default=Path("docs/experiment-assignments-summer-2026.csv"),
                    help="CSV output path (default: docs/experiment-assignments-summer-2026.csv)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print to stdout instead of writing the CSV")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    assignments = generate_assignments(args.seed, args.start, args.end)
    n_a = sum(1 for a in assignments if a.arm == "A")
    n_b = sum(1 for a in assignments if a.arm == "B")

    if args.dry_run:
        writer = csv.writer(sys.stdout)
        writer.writerow(["iso_week", "monday_date", "arm"])
        for a in assignments:
            writer.writerow([a.iso_week, a.monday_date.isoformat(), a.arm])
    else:
        write_csv(assignments, args.output)
        print(f"Wrote {len(assignments)} weekly assignments to {args.output}")

    print(f"  Seed: {args.seed}")
    print(f"  Window: {args.start} -> {args.end}")
    print(f"  Arm A weeks: {n_a}")
    print(f"  Arm B weeks: {n_b}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
