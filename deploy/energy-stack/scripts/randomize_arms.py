#!/usr/bin/env python3
"""DEPRECATED — pre-rebaseline historical artifact (do not run).

Retired 2026-05-13 by docs/plans/sced-rebaseline-spec-2026-05-13.md §0 + §2.
The current experiment uses deterministic 14-day arm alternation (12 arms,
6 Arm A + 6 Arm B) — no PRNG seed, no 4-week blocks, no weekly assignment
CSV. The canonical arm calendar is now in tools/analysis/arm_calendar.py
(analysis-side) and deploy/energy-stack/hvac-scheduler/arm_calendar.py
(controller-side, byte-identical, CI hash-sync checked).

This script and its output docs/experiment-assignments-summer-2026.csv are
preserved in-tree as historical artifacts (and to keep
tests/test_randomize_arms.py pinning the original algorithm for audit
traceability). The OSF prereg DOES NOT reference this script's output;
see spec §11 + §13 for the canonical OSF artifact list.

Tracked since PR #137 F3 deferral
(https://github.com/Promithius-DR/energy-stack/pull/137); deprecation
banner added by this doc-sweep.

Original docstring follows below for reference.

----

Generate the pre-committed Arm A / Arm B week-level assignment for the
residential HVAC controls field study (EXPERIMENT_DESIGN.md §5).

Block-of-4 randomization: every consecutive 4-week block contains 2
consecutive Arm A weeks and 2 consecutive Arm B weeks, with the order
within the block (AABB or BBAA) determined by the seed. The 2-week-arm
unit gives 12 analyzable days per arm-period after the 48h post-switch
washout (vs 5 days under 1-week arms), and 4-week blocks guarantee
balanced exposure within each calendar month while preserving the SCED
randomization-test reference distribution as a secondary inference.

Trailing weeks (1-3 weeks past the last complete 4-week block) are
randomized independently from the same RNG stream so the date range can
be any length without changing the algorithm.

This script is the binding artifact behind the OSF pre-registration. The
seed and the algorithm are pre-committed; running with the same seed and
date range MUST produce the same CSV. Verified deterministic across
CPython 3.10+ (Python's random.Random.shuffle / .choice are stable across
3.x releases for a fixed seed).

Default seed `20260601` is the date of pre-registration commitment per
EXPERIMENT_DESIGN.md §13.

Usage:
    python randomize_arms.py
    python randomize_arms.py --seed 20260602 --start 2027-06-01 --end 2027-05-31
    python randomize_arms.py --output /tmp/assignments.csv

Output (CSV, header included):
    iso_week,monday_date,arm
    2026-W23,2026-06-01,A
    2026-W24,2026-06-08,A
    2026-W25,2026-06-15,B
    2026-W26,2026-06-22,B
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


_BLOCK_PATTERNS = (("A", "A", "B", "B"), ("B", "B", "A", "A"))


def generate_assignments(
    seed: int,
    start: date,
    end: date,
) -> list[WeekAssignment]:
    """Block-of-4 randomized A/B assignment for each Monday-anchored week
    intersecting [start, end]. Deterministic given the seed.

    Each complete 4-week block runs either AABB or BBAA, chosen by the
    RNG. Trailing weeks (1-3 past the last complete block) are
    randomized independently.
    """
    rng = random.Random(seed)
    mondays = _iter_mondays(start, end)
    assignments: list[WeekAssignment] = []

    i = 0
    while i + 3 < len(mondays):
        pattern = rng.choice(_BLOCK_PATTERNS)
        for monday, arm in zip(mondays[i : i + 4], pattern):
            assignments.append(WeekAssignment(_format_iso_week(monday), monday, arm))
        i += 4

    while i < len(mondays):
        monday = mondays[i]
        arm = rng.choice(["A", "B"])
        assignments.append(WeekAssignment(_format_iso_week(monday), monday, arm))
        i += 1

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
    ap.add_argument("--end", type=date.fromisoformat, default=date(2027, 5, 31),
                    help="End date inclusive, ISO YYYY-MM-DD (default: 2027-05-31; "
                         "year-round 2026 weeks per EXPERIMENT_DESIGN §5)")
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
