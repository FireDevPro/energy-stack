#!/usr/bin/env python3
"""One-shot 5-year backfill of ComEd-zone PJM data into InfluxDB.

Pulls historical hourly data the live `pjm-dm2-poller` doesn't cover
because that service only writes today's / forward-looking data. This
script fills in the past so the 5CP-probability classifier and the
forecast-bias-correction analyses have a training corpus to work with.

Two feeds, both filtered to ComEd zone:
  * `da_hrl_lmps` for `pnode_id=33092371` -> `pjm.lmp_da_hourly`
    Captures the day-ahead market clear that drives ComEd retail hourly.
  * `hrl_load_metered` for `zone=CE` (ComEd's PJM zone code is "CE",
    not "COMED" — verified empirically) -> `pjm.metered_load`
    The actual hourly metered load. Training feature for the 5CP-probability
    classifier alongside temperature and day-of-week.

Per-year iteration: one API call per (feed, year) using the `<start>to<end>`
range filter on `datetime_beginning_ept`. ~8,760 rows per call (one zone,
hourly), well under the 50,000-row ceiling. 5 years × 2 feeds = 10 calls
total at 11s pacing = ~2 minutes wall time.

Idempotent: re-running with the same date range upserts the same Influx
points because the (measurement, tag set, timestamp) tuple is identical.
Safe to retry on errors; safe to run weekly to refresh "this year" without
double-counting.

Archive boundary (phase 1 limitation):
  PJM splits da_hrl_lmps data into "standard" (last 731 days) and "archived"
  (older). Archive queries don't accept the `pnode_id` filter, so they need
  different query logic (filter by `type=Zone` and parse client-side). This
  script targets the standard tier only and SKIPS years that fall entirely
  or partially before the archive boundary, with a clear log line. Phase 2
  can add archive support if pre-2024 DA LMP backfill becomes valuable.

  hrl_load_metered has no archive cutoff (verified empirically via
  /metadata: archiveCutoffDays=null), so all years backfill cleanly.

Schema and design rationale: docs/PJM_DM2_INTEGRATION.md.
ComEd-specific filter constants: docs/PJM_DM2_FEEDS.md.

Usage:
    python backfill_pjm.py                                # default 2021..(last full year)
    python backfill_pjm.py --years 2024                   # just one year
    python backfill_pjm.py --years 2021,2022,2023         # specific years
    python backfill_pjm.py --feed da_lmp                  # one feed only
    python backfill_pjm.py --feed metered_load
    python backfill_pjm.py --dry-run                      # parse + count, no writes

Environment:
    PJM_DM2_API_KEY  (required)  -- Non-Member API key
    INFLUX_URL       (default http://localhost:8086)
    INFLUX_TOKEN     (required unless --dry-run)
    INFLUX_ORG       (default depaola-home)
    INFLUX_BUCKET    (default energy)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable
from zoneinfo import ZoneInfo


PJM_API_BASE = "https://api.pjm.com/api/v1"
COMED_PNODE_ID = 33092371
COMED_ZONE_CODE = "CE"  # Empirically verified: ComEd's hrl_load_metered zone code is "CE", not "COMED"
EPT = ZoneInfo("America/New_York")  # PJM EPT follows DST same as America/New_York
RATE_LIMIT_PAUSE_S = 11   # 6 calls/min -> safe spacing
ROW_COUNT_PER_CALL = 10000  # comfortable cushion above 8,760 hours/year


@dataclass(frozen=True)
class FeedSpec:
    """One feed's URL + filter recipe + Influx-write recipe.

    `archive_cutoff_days`: if set, data older than this many days is in PJM's
    archived tier and uses different filter rules (e.g. da_hrl_lmps doesn't
    accept pnode_id on archive queries). Phase 1 backfill skips years that
    would span into archive; phase 2 can add archive-tier support."""
    feed: str
    extra_filters: dict[str, str]
    measurement: str
    tag_keys: tuple[str, ...]
    field_specs: tuple[tuple[str, str], ...]   # (api_field_name, influx_field_name)
    timestamp_field: str = "datetime_beginning_ept"
    archive_cutoff_days: int | None = None


# Archive cutoffs sourced from the /metadata endpoint (archiveCutoffDays).
# Verified live 2026-05-06: da_hrl_lmps=731, hrl_load_metered=null (no archive).
FEED_SPECS: dict[str, FeedSpec] = {
    "da_lmp": FeedSpec(
        feed="da_hrl_lmps",
        extra_filters={"pnode_id": str(COMED_PNODE_ID)},
        measurement="pjm.lmp_da_hourly",
        tag_keys=("pnode_id", "pnode_name"),
        field_specs=(
            ("total_lmp_da", "total_lmp_da"),
            ("system_energy_price_da", "system_energy_price_da"),
            ("congestion_price_da", "congestion_price_da"),
            ("marginal_loss_price_da", "marginal_loss_price_da"),
        ),
        archive_cutoff_days=731,
    ),
    "metered_load": FeedSpec(
        feed="hrl_load_metered",
        extra_filters={"zone": COMED_ZONE_CODE},
        measurement="pjm.metered_load",
        tag_keys=("zone", "load_area"),
        field_specs=(("mw", "mw"),),
        archive_cutoff_days=None,
    ),
}


class ArchiveSpanError(RuntimeError):
    """Raised when the requested year would cross PJM's archive boundary,
    which requires different filter rules than this script supports."""


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def fetch_year(spec: FeedSpec, api_key: str, year: int) -> list[dict]:
    """One API call: fetch a full calendar year for the given feed.
    Uses PJM's `<start>to<end>` range syntax on datetime_beginning_ept.

    Raises ArchiveSpanError if any part of the year is older than the
    feed's archive cutoff. Caller can catch and skip."""
    if spec.archive_cutoff_days is not None:
        # Year-end (Dec 31) being older than (today - cutoff) means the
        # entire year is in archive. Year-start being older than the cutoff
        # but year-end still in standard means the year SPANS the boundary,
        # which PJM also rejects with API_1043.
        cutoff = datetime.now(EPT).date() - timedelta(days=spec.archive_cutoff_days)
        year_start = datetime(year, 1, 1).date()
        if year_start < cutoff:
            raise ArchiveSpanError(
                f"{spec.feed} year {year}: starts {year_start.isoformat()}, "
                f"older than archive cutoff {cutoff.isoformat()} "
                f"({spec.archive_cutoff_days}-day window). Phase 1 backfill skips "
                f"this; archive-tier support is phase 2."
            )
    start = f"{year}-01-01T00:00:00.0"
    end = f"{year}-12-31T23:00:00.0"
    params = {
        spec.timestamp_field: f"{start}to{end}",
        "rowCount": ROW_COUNT_PER_CALL,
        "startRow": 1,
        **spec.extra_filters,
    }
    qs = urllib.parse.urlencode(params)
    url = f"{PJM_API_BASE}/{spec.feed}?{qs}"
    req = urllib.request.Request(url, headers={"Ocp-Apim-Subscription-Key": api_key})
    with urllib.request.urlopen(req, timeout=60) as resp:
        if resp.status != 200:
            raise RuntimeError(f"PJM {spec.feed} year {year}: HTTP {resp.status}")
        payload = json.loads(resp.read())
    items = payload.get("items") or []
    total = payload.get("totalRows", 0)
    if total > len(items):
        raise RuntimeError(
            f"PJM {spec.feed} year {year}: totalRows={total} but rowCount={ROW_COUNT_PER_CALL} "
            f"only returned {len(items)}; need pagination support"
        )
    return items


# ---------------------------------------------------------------------------
# Point construction (returns lists of dicts the caller serializes for Influx)
# ---------------------------------------------------------------------------


def build_points(spec: FeedSpec, items: Iterable[dict]) -> list[dict]:
    """Return a list of {measurement, tags, fields, time_utc} dicts. Pure
    function so it's testable without Influx."""
    out: list[dict] = []
    for it in items:
        ts_local = datetime.fromisoformat(it[spec.timestamp_field]).replace(tzinfo=EPT)
        ts_utc = ts_local.astimezone(timezone.utc)
        tags = {k: str(it.get(k) or "") for k in spec.tag_keys}
        # zone fallback: ZONE-type pnodes return zone=null; use pnode_name if so
        if spec.measurement == "pjm.lmp_da_hourly":
            tags["zone"] = it.get("zone") or it.get("pnode_name") or ""
        fields = {
            inf_name: float(it.get(api_name) or 0.0)
            for api_name, inf_name in spec.field_specs
        }
        out.append({
            "measurement": spec.measurement,
            "tags": tags,
            "fields": fields,
            "time_utc": ts_utc,
        })
    return out


# ---------------------------------------------------------------------------
# Influx write
# ---------------------------------------------------------------------------


def write_points(points: list[dict]) -> None:
    """Write the synthetic point dicts to InfluxDB. Imported lazily so
    --dry-run never needs the deps."""
    from influxdb_client import InfluxDBClient, Point
    from influxdb_client.client.write_api import SYNCHRONOUS

    url = os.environ.get("INFLUX_URL", "http://localhost:8086")
    token = os.environ.get("INFLUX_TOKEN")
    org = os.environ.get("INFLUX_ORG", "depaola-home")
    bucket = os.environ.get("INFLUX_BUCKET", "energy")
    if not token:
        raise SystemExit("INFLUX_TOKEN env var is required (or use --dry-run)")

    influx_points = []
    for pt in points:
        p = Point(pt["measurement"])
        for k, v in pt["tags"].items():
            if v:
                p = p.tag(k, v)
        for k, v in pt["fields"].items():
            p = p.field(k, v)
        p = p.time(pt["time_utc"])
        influx_points.append(p)

    with InfluxDBClient(url=url, token=token, org=org) as client:
        # Batched in chunks to keep payload size reasonable; influxdb-client
        # handles internal batching but explicit chunks here keep failure
        # mode obvious.
        write_api = client.write_api(write_options=SYNCHRONOUS)
        chunk = 5000
        for i in range(0, len(influx_points), chunk):
            write_api.write(bucket=bucket, record=influx_points[i : i + chunk])


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def default_year_range() -> list[int]:
    """5-year backfill ending at the most recently completed calendar year.
    PJM publishes hrl_load_metered with ~1-day delay, so 'last completed year'
    is well-supported."""
    now = datetime.now(EPT)
    last_full_year = now.year - 1
    return list(range(last_full_year - 4, last_full_year + 1))


def parse_years(arg: str | None) -> list[int]:
    if not arg:
        return default_year_range()
    return [int(y.strip()) for y in arg.split(",") if y.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--years", help="Comma-separated list, e.g. 2021,2022,2023,2024,2025. Default: last 5 full years.")
    ap.add_argument("--feed", choices=list(FEED_SPECS.keys()),
                    help="Run only one feed (default: both).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Fetch + count without writing to Influx.")
    args = ap.parse_args(argv)

    api_key = os.environ.get("PJM_DM2_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("PJM_DM2_API_KEY env var is required")

    years = parse_years(args.years)
    feeds_to_run = [args.feed] if args.feed else list(FEED_SPECS.keys())

    print(f"Backfill plan: years={years}, feeds={feeds_to_run}, "
          f"dry_run={args.dry_run}")
    print(f"  Estimated calls: {len(years) * len(feeds_to_run)} (one per year per feed)")

    total_points = 0
    started = time.monotonic()
    for feed_key in feeds_to_run:
        spec = FEED_SPECS[feed_key]
        print(f"\n=== {feed_key} -> {spec.measurement} ===")
        feed_points: list[dict] = []
        for year in years:
            try:
                t0 = time.monotonic()
                items = fetch_year(spec, api_key, year)
                pts = build_points(spec, items)
                feed_points.extend(pts)
                elapsed = time.monotonic() - t0
                print(f"  {year}: {len(items)} rows, {len(pts)} points ({elapsed:.1f}s)")
            except ArchiveSpanError as exc:
                print(f"  {year}: SKIPPED (archive) -- {exc}", file=sys.stderr)
                continue  # No sleep needed, no API call was made
            except Exception as exc:
                print(f"  {year}: FAILED -- {exc}", file=sys.stderr)
                return 3
            time.sleep(RATE_LIMIT_PAUSE_S)
        if args.dry_run:
            print(f"  [dry-run] would write {len(feed_points)} points")
        else:
            write_points(feed_points)
            print(f"  wrote {len(feed_points)} points to {spec.measurement}")
        total_points += len(feed_points)

    elapsed_total = time.monotonic() - started
    print(f"\nDone. {total_points} total points across {len(years)} year(s) "
          f"and {len(feeds_to_run)} feed(s) in {elapsed_total:.1f}s.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
