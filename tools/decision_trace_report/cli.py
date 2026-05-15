"""CLI entry point for `python -m tools.decision_trace_report`."""
import argparse
import logging
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("decision_trace_report")


CT = ZoneInfo("America/Chicago")
DEFAULT_OUTPUT_DIR = Path(r"D:\Projects\energy-proxy\docs\test-reports")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m tools.decision_trace_report",
        description=("Render a daily decision-trace commissioning report from "
                      "Loki + InfluxDB. See docs/superpowers/specs/2026-05-15-"
                      "decision-trace-report-tool-design.md."),
    )
    parser.add_argument("--date", help="CT calendar day to render (YYYY-MM-DD). "
                        "Default: yesterday CT.")
    parser.add_argument("--from", dest="from_ct",
                        help="Custom range start (CT, ISO local).")
    parser.add_argument("--to", dest="to_ct",
                        help="Custom range end (CT, ISO local).")
    parser.add_argument("--output", help="Override output file path.")
    parser.add_argument("--no-telegram", action="store_true",
                        help="Suppress Telegram heartbeat.")
    parser.add_argument("--verbose", action="store_true",
                        help="Echo Loki + Influx query bodies (debug queries).")
    parser.add_argument("--loki-url", help="Override LOKI_URL env.")
    parser.add_argument("--influx-url", help="Override INFLUXDB_URL env.")
    parser.add_argument("--env-file",
                        help="Optional dotenv file to load before running.")
    return parser.parse_args(argv)


def default_target_date(*, now: date | None = None) -> str:
    """Yesterday's CT calendar day in YYYY-MM-DD format."""
    if now is None:
        now = datetime.now(CT).date()
    return (now - timedelta(days=1)).isoformat()


def validate_args(args: argparse.Namespace) -> None:
    """Check mutual exclusion + completeness of --date / --from / --to.

    Exits with status 2 (argparse-style usage error) on violation.
    Called by main() after parse_args, before any work."""
    if (args.from_ct and not args.to_ct) or (args.to_ct and not args.from_ct):
        log.error("--from and --to must be used together")
        sys.exit(2)
    if args.date and (args.from_ct or args.to_ct):
        log.error("--date is mutually exclusive with --from/--to")
        sys.exit(2)


def resolve_window(
    args: argparse.Namespace,
    *,
    now: date | None = None,
) -> tuple[str, datetime, datetime]:
    """Resolve CLI args into (label, start_ct, end_ct).

    `label` is used for the output filename + report header. For a
    day-aligned query (default or --date) the label is the CT
    `YYYY-MM-DD` date. For a custom range (--from/--to) the label
    embeds both endpoints so the filename reflects the actual window.

    `start_ct` and `end_ct` are timezone-aware datetimes in
    `America/Chicago` — ZoneInfo handles DST symmetrically (CDT in
    summer, CST in winter). Callers pass these through
    `.astimezone(timezone.utc)` for Loki/Influx query parameters.
    """
    if args.from_ct and args.to_ct:
        start_ct = datetime.fromisoformat(args.from_ct).replace(tzinfo=CT)
        end_ct = datetime.fromisoformat(args.to_ct).replace(tzinfo=CT)
        label = f"{args.from_ct}__to__{args.to_ct}"
        return label, start_ct, end_ct

    target = args.date or default_target_date(now=now)
    start_ct = datetime.fromisoformat(target).replace(tzinfo=CT)
    end_ct = start_ct + timedelta(days=1)
    return target, start_ct, end_ct


def load_env_file(path: str) -> None:
    """Read a dotenv-style file into os.environ. Keys already set in
    the environment are NOT overwritten."""
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def _last_da_lmp_fire_utc(now_utc: datetime) -> datetime:
    """Most-recent expected 17:00 CT daily publish, in UTC.

    Used for §4 feed-health event-feed staleness check on
    `pjm.lmp_da_hourly`. If `now` is BEFORE today's 17:00 CT publish,
    last expected fire is yesterday's; otherwise today's."""
    now_ct = now_utc.astimezone(CT)
    today_17 = now_ct.replace(hour=17, minute=0, second=0, microsecond=0)
    if now_ct < today_17:
        today_17 = today_17 - timedelta(days=1)
    return today_17.astimezone(timezone.utc).replace(tzinfo=timezone.utc)


def _last_metered_load_fire_utc(now_utc: datetime) -> datetime:
    """Most-recent expected Sunday 02:00 CT weekly publish, in UTC.

    Used for §4 feed-health event-feed staleness check on
    `pjm.metered_load`."""
    now_ct = now_utc.astimezone(CT)
    # weekday: Monday=0 ... Sunday=6
    days_since_sunday = (now_ct.weekday() + 1) % 7
    last_sunday = (now_ct - timedelta(days=days_since_sunday)).replace(
        hour=2, minute=0, second=0, microsecond=0,
    )
    # If we're early Sunday before 02:00, use the previous Sunday's fire
    if last_sunday > now_ct:
        last_sunday = last_sunday - timedelta(days=7)
    return last_sunday.astimezone(timezone.utc).replace(tzinfo=timezone.utc)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.env_file:
        load_env_file(args.env_file)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    target = args.date or default_target_date()
    log.info("rendering decision-trace report for target CT day %s", target)
    # End-to-end render wired in Task 6.2.
    return 0


if __name__ == "__main__":
    sys.exit(main())
