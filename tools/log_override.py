#!/usr/bin/env python3
"""Log a manual setpoint override to InfluxDB for the SCED analysis.

Per EXPERIMENT_DESIGN.md §4 Rule 9, manual setpoint overrides are
classified into two categories with different analysis-time handling:

  * operational: occupant briefly bumps the setpoint for comfort
    (e.g. +1F during work-from-home stretches). Kept in formal
    analysis with an `override=operational` flag. Reported
    descriptively as count and total-degree-hours per week.

  * vacation: occupant departs; thermostat held at 82F absolute
    with no active scheduling. Excludes the affected calendar-days
    from formal analysis; if the resulting week has <5 qualifying
    days, excludes the whole week.

Annotation requirement holds for both categories; absence of
annotation at week-close defaults the override to "operational"
with a flag, per the locked spec.

This script writes one row per annotated override to the
`hvac.overrides` measurement. Multiple-day overrides (typically
vacation) take a span via --start and --end; single-event overrides
default end_ts to start_ts + duration (default 1 hour).

Usage:
    # Operational override: +1F bump from 14:00-17:00 CT today
    python log_override.py --category operational \\
        --start "2026-07-15 14:00 CT" --end "2026-07-15 17:00 CT" \\
        --setpoint 76 --note "WFH afternoon"

    # Vacation: house empty Aug 5-12, set 82F
    python log_override.py --category vacation \\
        --start "2026-08-05 08:00 CT" --end "2026-08-12 18:00 CT" \\
        --setpoint 82 --note "out of town"

Environment (matches log_arm_transition.py):
    INFLUXDB_URL                   default http://localhost:8086
    INFLUXDB_INIT_ADMIN_TOKEN      required unless --dry-run
    INFLUXDB_INIT_ORG              default depaola-home
    INFLUXDB_INIT_BUCKET           default energy
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS


CT = ZoneInfo("America/Chicago")

CATEGORIES = ("operational", "vacation")


def parse_ct_ts(s: str) -> datetime:
    """Parse a 'YYYY-MM-DD HH:MM [CT]' string into a CT-aware datetime.

    Accepts trailing 'CT' for readability; treats the value as CT
    regardless (consistent with the locked spec timezone).
    """
    s = s.strip()
    if s.endswith(" CT"):
        s = s[:-3].strip()
    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M")
    except ValueError as e:
        raise ValueError(
            f"timestamp must be 'YYYY-MM-DD HH:MM [CT]', got {s!r}: {e}"
        ) from e
    return dt.replace(tzinfo=CT)


def build_override_point(
    *,
    category: str,
    start_ct: datetime,
    end_ct: datetime,
    setpoint_f: float,
    note: str = "",
    logged_at_utc: datetime | None = None,
) -> Point:
    """Pure construction of the hvac.overrides row.

    The override SPAN is encoded as start_ts and end_ts fields (epoch
    seconds, UTC). The row's own timestamp is when the annotation was
    logged, NOT when the override fired — analysis code joins on the
    span fields. This separation lets us re-log corrections after the
    fact without losing the original-event reference.
    """
    if category not in CATEGORIES:
        raise ValueError(
            f"category must be one of {CATEGORIES}, got {category!r}"
        )
    if end_ct < start_ct:
        raise ValueError(
            f"end ({end_ct.isoformat()}) must be >= start ({start_ct.isoformat()})"
        )
    if not (40 <= setpoint_f <= 90):
        raise ValueError(
            f"setpoint_f out of plausible range [40, 90]: {setpoint_f}"
        )
    if logged_at_utc is None:
        logged_at_utc = datetime.now(timezone.utc)

    duration_hours = (end_ct - start_ct).total_seconds() / 3600.0

    p = (
        Point("hvac.overrides")
        .tag("category", category)
        .field("start_ts", int(start_ct.timestamp()))
        .field("end_ts", int(end_ct.timestamp()))
        .field("duration_hours", duration_hours)
        .field("setpoint_f", float(setpoint_f))
        .field("note", note)
        .time(logged_at_utc)
    )
    return p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Annotate a manual setpoint override per "
        "EXPERIMENT_DESIGN.md §4 Rule 9.",
    )
    ap.add_argument(
        "--category", required=True, choices=list(CATEGORIES),
        help="operational (kept with flag) or vacation (excludes days).",
    )
    ap.add_argument(
        "--start", required=True,
        help="Start of override span, 'YYYY-MM-DD HH:MM' CT.",
    )
    ap.add_argument(
        "--end", default=None,
        help="End of override span. Defaults to --start + --duration.",
    )
    ap.add_argument(
        "--duration", type=float, default=1.0,
        help="Hours, if --end omitted. Default 1.0.",
    )
    ap.add_argument(
        "--setpoint", type=float, required=True,
        help="Override cool setpoint in F (validated 40-90).",
    )
    ap.add_argument(
        "--note", default="",
        help="Free-form note (e.g., 'WFH afternoon').",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Print the line protocol without writing.",
    )
    args = ap.parse_args(argv)

    start_ct = parse_ct_ts(args.start)
    if args.end is not None:
        end_ct = parse_ct_ts(args.end)
    else:
        end_ct = start_ct + timedelta(hours=args.duration)

    point = build_override_point(
        category=args.category,
        start_ct=start_ct,
        end_ct=end_ct,
        setpoint_f=args.setpoint,
        note=args.note,
    )

    if args.dry_run:
        print(point.to_line_protocol())
        return 0

    influx_url = os.environ.get("INFLUXDB_URL", "http://localhost:8086")
    try:
        token = os.environ["INFLUXDB_INIT_ADMIN_TOKEN"]
    except KeyError:
        print(
            "INFLUXDB_INIT_ADMIN_TOKEN not set (use --dry-run to "
            "preview the row).",
            file=sys.stderr,
        )
        return 2
    org = os.environ.get("INFLUXDB_INIT_ORG", "depaola-home")
    bucket = os.environ.get("INFLUXDB_INIT_BUCKET", "energy")

    with InfluxDBClient(url=influx_url, token=token, org=org) as client:
        write_api = client.write_api(write_options=SYNCHRONOUS)
        write_api.write(bucket=bucket, record=point)

    duration = (end_ct - start_ct).total_seconds() / 3600.0
    print(
        f"Logged {args.category} override: "
        f"{start_ct.strftime('%Y-%m-%d %H:%M %Z')} -> "
        f"{end_ct.strftime('%Y-%m-%d %H:%M %Z')} "
        f"({duration:.1f}h at {args.setpoint:.0f}F)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
