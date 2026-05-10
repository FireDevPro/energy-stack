#!/usr/bin/env python3
"""Log a Monday Arm A <-> Arm B transition to InfluxDB.

The CTK04AE installer-menu setting ISU 4090 (Adaptive Intelligent Recovery,
"AIR") needs to flip every time the experiment switches arms:

  * Arm A: AIR = ON (thermostat learns recovery timing, matches consumer
    Nest/Ecobee Smart Recovery behaviour)
  * Arm B: AIR = OFF (Pi setpoint pushes are honored at the scheduled
    minute, not pre-emptively reinterpreted by the thermostat)

The toggle itself is currently a manual TCC-web-UI step (v1; v2 may
automate via Cinegration's Control4 driver if it exposes ISU 4090 as a
writable parameter). This script just writes the audit row so the
experimental record reflects which arm was live at each transition.

Usage:
    python log_arm_transition.py --from A --to B --air off --mode manual
    python log_arm_transition.py --from B --to A --air on  --mode auto

Required env (read from the same /home/chris/energy-stack/.env the
docker-compose stack uses):
    INFLUXDB_URL, INFLUXDB_INIT_ADMIN_TOKEN, INFLUXDB_INIT_ORG,
    INFLUXDB_INIT_BUCKET
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS


def build_transition_point(
    *,
    from_arm: str,
    to_arm: str,
    air_setting: str,
    manual_or_auto: str,
    pi_dry_run: bool,
    note: str = "",
    timestamp_utc: datetime | None = None,
) -> Point:
    """Pure construction of the hvac.arm_transitions row. Pulled out so
    tests can assert the line-protocol shape without an InfluxDB
    connection.

    Tagged by from_arm + to_arm so transitions can be filtered cleanly
    (e.g., 'every B->A transition this season'). pi_dry_run carries the
    expected scheduler mode going into the new arm — true for Arm A
    (scheduler logs only), false for Arm B (scheduler pushes setpoints)."""
    if from_arm not in ("A", "B"):
        raise ValueError(f"from_arm must be 'A' or 'B', got {from_arm!r}")
    if to_arm not in ("A", "B"):
        raise ValueError(f"to_arm must be 'A' or 'B', got {to_arm!r}")
    if air_setting not in ("on", "off"):
        raise ValueError(f"air_setting must be 'on' or 'off', got {air_setting!r}")
    if manual_or_auto not in ("manual", "auto"):
        raise ValueError(f"manual_or_auto must be 'manual' or 'auto', got {manual_or_auto!r}")
    p = (Point("hvac.arm_transitions")
         .tag("from_arm", from_arm)
         .tag("to_arm", to_arm)
         .tag("manual_or_auto", manual_or_auto)
         .field("air_setting", air_setting)
         .field("pi_dry_run", pi_dry_run)
         .field("note", note))
    if timestamp_utc is not None:
        p = p.time(timestamp_utc)
    return p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Log an arm-transition audit row.")
    ap.add_argument("--from", dest="from_arm", required=True, choices=["A", "B"])
    ap.add_argument("--to", dest="to_arm", required=True, choices=["A", "B"])
    ap.add_argument("--air", dest="air_setting", required=True, choices=["on", "off"])
    ap.add_argument("--mode", dest="manual_or_auto", default="manual",
                    choices=["manual", "auto"])
    ap.add_argument("--note", default="",
                    help="Free-form note (e.g., reason for an off-schedule "
                         "transition).")
    args = ap.parse_args(argv)

    pi_dry_run = (args.to_arm == "A")
    point = build_transition_point(
        from_arm=args.from_arm,
        to_arm=args.to_arm,
        air_setting=args.air_setting,
        manual_or_auto=args.manual_or_auto,
        pi_dry_run=pi_dry_run,
        note=args.note,
        timestamp_utc=datetime.now(timezone.utc),
    )

    influx_url = os.environ.get("INFLUXDB_URL", "http://localhost:8086")
    token = os.environ["INFLUXDB_INIT_ADMIN_TOKEN"]
    org = os.environ.get("INFLUXDB_INIT_ORG", "depaola-home")
    bucket = os.environ.get("INFLUXDB_INIT_BUCKET", "energy")

    with InfluxDBClient(url=influx_url, token=token, org=org) as client:
        write_api = client.write_api(write_options=SYNCHRONOUS)
        write_api.write(bucket=bucket, record=point)

    print(f"Logged transition {args.from_arm} -> {args.to_arm} "
          f"(AIR={args.air_setting}, mode={args.manual_or_auto}, "
          f"pi_dry_run={pi_dry_run})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
