#!/usr/bin/env python3
"""One-shot backfill of pjm.lmp_rt_hourly for the SCED rebaseline window.

Pulls PJM ``rt_hrl_lmps`` settled hourly LMPs for the ComEd zone
(pnode_id=33092371) from ``DEFAULT_START_DATE`` (2026-01-01 per spec
§8) through ``yesterday`` and writes them to InfluxDB
(``pjm.lmp_rt_hourly`` measurement). Bill-canonical supply price input
for the HVAC$ outcome.

The live poller (``fetch_rt_lmp_for_yesterday`` in app.py, fires at
12:00 CT) covers yesterday onward from deploy time forward; this
script fills in the prior backlog so the analysis pipeline (Phase 3)
has a complete settled-LMP series for the experiment window.

Run inside the pjm-dm2-poller container so it shares the live
poller's env (PJM_DM2_API_KEY, INFLUXDB_*):

    docker exec pjm-dm2-poller python backfill_rt_hrl_lmps.py

Idempotent — re-runs upsert the same (pnode_id, timestamp) points.
PJM rate limit (6 calls/min) honored via ``--sleep`` between dates;
default 5s gives a 2x safety margin.

Usage:
    python backfill_rt_hrl_lmps.py
    python backfill_rt_hrl_lmps.py --start 2026-03-01 --end 2026-05-13
    python backfill_rt_hrl_lmps.py --sleep 2.0
    python backfill_rt_hrl_lmps.py --dry-run

Environment:
    PJM_DM2_API_KEY            (required)
    INFLUX_URL                 (default http://influxdb:8086)
    INFLUXDB_INIT_ADMIN_TOKEN  (required)
    INFLUXDB_INIT_ORG          (default depaola-home)
    INFLUXDB_INIT_BUCKET       (default energy)

Spec: docs/plans/sced-rebaseline-spec-2026-05-13.md §8.
Plan: docs/plans/sced-rebaseline-implementation-2026-05-13.md Task 2.3.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Iterator
from datetime import date, datetime, timedelta

from influxdb_client import InfluxDBClient
from influxdb_client.client.write_api import SYNCHRONOUS

from app import (
    Config,
    PJMClient,
    fetch_rt_lmp_for_date,
    log,
)


# Spec §8 + plan Task 2.3: backfill from 2026-01-01 (24-month PJM
# retention is aspirational; pre-2024 LMP requires the archive tier
# which has different query semantics — out of scope for Phase 2).
DEFAULT_START_DATE = date(2026, 1, 1)


def default_end_date(*, today: date | None = None) -> date:
    """Default end-date is yesterday: PJM settled data is T+1, and the
    live poller covers yesterday onward, so the backfill ends at the
    last fully-settled day to avoid a gap or double-write."""
    if today is None:
        today = date.today()
    return today - timedelta(days=1)


def iter_target_dates(start: date, end: date) -> Iterator[date]:
    """Yield dates from ``start`` through ``end`` inclusive. Empty
    iterator when ``start > end``."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


async def backfill_range(
    client: PJMClient,
    write_api,
    cfg: Config,
    *,
    start_date: date,
    end_date: date,
    sleep_s: float = 5.0,
) -> "BackfillResult":
    """Fetch + write rt_hrl_lmps for every date in [start_date, end_date].

    Returns a ``BackfillResult`` with per-date counts. Writes per-date
    (not batched across dates) to bound memory over a multi-month
    window. Skips the influx write when PJM returns zero rows for a
    date — Influx dedups by timestamp so retrying later is idempotent.

    ``sleep_s`` is the inter-date pacing margin; PJM Non-Member API is
    6 calls/min so 5s pacing keeps us well under the ceiling.

    Resilience: a transient PJM/aiohttp error (timeout, 429, 5xx) on
    one date is logged and recorded in ``failed_dates``; the loop
    continues to the next date so a single failure mid-backfill does
    not require the operator to manually compute the resume offset.
    Failed dates can be re-run after the main backfill completes.
    """
    dates = list(iter_target_dates(start_date, end_date))
    result = BackfillResult()
    for i, target in enumerate(dates):
        target_dt = datetime(target.year, target.month, target.day)
        try:
            points = await fetch_rt_lmp_for_date(client, cfg, target_dt)
        except Exception as exc:
            log("error", "backfill_rt_lmp_date_failed",
                date=target.isoformat(),
                error_type=type(exc).__name__, error=str(exc))
            result.failed_dates.append(target)
        else:
            if points:
                write_api.write(bucket=cfg.influx_bucket, record=points)
                log("info", "backfill_rt_lmp_date_ok",
                    date=target.isoformat(), points=len(points))
                result.total_points += len(points)
            else:
                log("warn", "backfill_rt_lmp_date_empty",
                    date=target.isoformat(),
                    note="PJM returned 0 rows; may not be posted yet")
                result.empty_dates.append(target)
        result.dates_attempted += 1
        if i < len(dates) - 1:
            await asyncio.sleep(sleep_s)
    return result


class BackfillResult:
    """Per-run summary; aggregated and logged at end so the operator
    sees one summary line listing dates that need follow-up rather
    than scanning 130+ per-date log lines."""

    def __init__(self) -> None:
        self.dates_attempted: int = 0
        self.total_points: int = 0
        self.empty_dates: list[date] = []
        self.failed_dates: list[date] = []

    def needs_followup(self) -> bool:
        return bool(self.empty_dates or self.failed_dates)


def _parse_iso_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill pjm.lmp_rt_hourly for the SCED rebaseline window."
    )
    parser.add_argument(
        "--start", type=_parse_iso_date, default=DEFAULT_START_DATE,
        help=f"first date inclusive (default {DEFAULT_START_DATE.isoformat()})",
    )
    parser.add_argument(
        "--end", type=_parse_iso_date, default=None,
        help="last date inclusive (default yesterday)",
    )
    parser.add_argument(
        "--sleep", type=float, default=5.0,
        help="seconds between PJM calls (default 5.0; PJM limit is 6 calls/min)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print the date range and exit without making PJM calls",
    )
    return parser.parse_args(argv)


async def main_async(args: argparse.Namespace) -> int:
    end = args.end if args.end is not None else default_end_date()
    if args.start > end:
        log("error", "backfill_rt_lmp_invalid_range",
            start=args.start.isoformat(), end=end.isoformat())
        return 2

    dates = list(iter_target_dates(args.start, end))
    log("info", "backfill_rt_lmp_starting",
        start=args.start.isoformat(), end=end.isoformat(),
        dates=len(dates), sleep_s=args.sleep,
        expected_rows_approx=len(dates) * 24)

    if args.dry_run:
        log("info", "backfill_rt_lmp_dry_run_done")
        return 0

    cfg = Config.from_env()
    influx = InfluxDBClient(
        url=cfg.influx_url, token=cfg.influx_token, org=cfg.influx_org,
    )
    write_api = influx.write_api(write_options=SYNCHRONOUS)
    try:
        async with PJMClient(cfg) as client:
            result = await backfill_range(
                client, write_api, cfg,
                start_date=args.start, end_date=end, sleep_s=args.sleep,
            )
        log("info", "backfill_rt_lmp_done",
            dates_attempted=result.dates_attempted,
            total_points=result.total_points,
            empty_dates=[d.isoformat() for d in result.empty_dates],
            failed_dates=[d.isoformat() for d in result.failed_dates],
            note=(
                "re-run with --start <date> --end <date> for any failed "
                "or empty dates after PJM publishes them"
                if result.needs_followup() else "all dates covered"
            ))
    finally:
        influx.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
