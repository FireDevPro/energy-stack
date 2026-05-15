"""CLI entry point for `python -m tools.decision_trace_report`."""
import argparse
import logging
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from tools.decision_trace_report.influx_client import InfluxClient
from tools.decision_trace_report.loki_client import LokiClient
from tools.decision_trace_report.telegram_client import TelegramClient

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
    from tools.decision_trace_report import (
        decision_codes_loader,
        renderer,
    )
    from tools.decision_trace_report.sections import (
        coverage_scorecard,
        day_of,
        feed_health,
        night_before,
        price_spikes,
    )

    args = parse_args(argv)
    validate_args(args)
    if args.env_file:
        load_env_file(args.env_file)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Three concepts deliberately kept separate:
    #
    #   report_label   — used for output filename + section headers.
    #                    For --date / default mode: the YYYY-MM-DD date.
    #                    For --from/--to custom range: "FROM__to__TO".
    #
    #   target_date    — set only in date-mode (default or --date).
    #                    Used as a Loki `decision_for_date` filter for
    #                    §1 night-before and as the tag for InfluxDB
    #                    `hvac.decisions` / `hvac.precool_window`
    #                    lookups. None in custom-range mode, where the
    #                    range query itself is the source of truth and
    #                    decision_for_date filtering is skipped.
    #
    #   start_ct / end_ct — always set, used for every range-mode query
    #                    (Loki time bounds + Influx range-mode helpers).
    #
    # Conflating these (e.g., `target = label`) breaks custom-range
    # mode because date-mode helpers like `fetch_hvac_actions(target)`
    # would try to parse the label as YYYY-MM-DD and crash.
    report_label, start_ct, end_ct = resolve_window(args)
    is_custom_range = bool(args.from_ct and args.to_ct)
    target_date = (
        None if is_custom_range
        else (args.date or default_target_date())
    )
    log.info("rendering decision-trace report for window %s (%s)",
              report_label, "custom range" if is_custom_range else "CT day")

    loki = LokiClient(args.loki_url or os.environ["LOKI_URL"])
    influx = InfluxClient(
        url=args.influx_url or os.environ["INFLUXDB_URL"],
        token=os.environ["INFLUXDB_TOKEN"],
        org=os.environ["INFLUXDB_ORG"],
        bucket=os.environ.get("INFLUXDB_BUCKET", "energy"),
    )

    sections_md: dict[str, str] = {}
    query_errors = 0

    start_utc = start_ct.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_utc = end_ct.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # §1 night-before
    #
    # Date mode (default / --date): filter Loki events by
    # `decision_for_date == target_date` (so the 21:00 night-before
    # decision and the 06:00/11:00 revisits all key cleanly), and
    # cross-reference InfluxDB by the same tag.
    #
    # Custom-range mode (--from/--to): no `decision_for_date` filter
    # — return any matching trace events whose Loki ingest timestamp
    # falls in the requested CT window. Skip Influx cross-reference
    # (no canonical "this is THE date" — caller asked for a window).
    try:
        if target_date is not None:
            extended_start_utc = (start_ct - timedelta(hours=4)).astimezone(
                timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            day_type_events = [
                e for e in loki.fetch_decision_traces(
                    "day_type_decision",
                    start=extended_start_utc, end=end_utc,
                ) if e.get("decision_for_date") == target_date
            ]
            precool_events = [
                e for e in loki.fetch_decision_traces(
                    "precool_decision",
                    start=extended_start_utc, end=end_utc,
                ) if e.get("decision_for_date") == target_date
            ]
            hvac_decisions = influx.fetch_hvac_decisions(target_date)
            hvac_precool = influx.fetch_precool_window(target_date)
        else:
            # Custom range — no decision_for_date filter, no Influx cross-ref
            day_type_events = loki.fetch_decision_traces(
                "day_type_decision", start=start_utc, end=end_utc,
            )
            precool_events = loki.fetch_decision_traces(
                "precool_decision", start=start_utc, end=end_utc,
            )
            hvac_decisions = []
            hvac_precool = None
        sections_md["night_before"] = night_before.render(
            target_date=report_label,
            day_type_events=day_type_events,
            precool_events=precool_events,
            hvac_decisions=hvac_decisions,
            hvac_precool_window=hvac_precool,
        )
        nb_disc = night_before.count_discrepancies(
            day_type_events=day_type_events,
            precool_events=precool_events,
            hvac_decisions=hvac_decisions,
            hvac_precool_window=hvac_precool,
        )
    except Exception as exc:
        log.warning("§1 query failed: %s", exc)
        sections_md["night_before"] = f"## §1 Night-before\n\n⚠️ query failed: `{exc}`\n"
        nb_disc = 0
        day_type_events, precool_events, hvac_decisions, hvac_precool = [], [], [], None
        query_errors += 1

    # §2 day-of
    # Both date-mode and custom-range mode use the range-mode Influx
    # helper. fetch_hvac_actions_by_range(start_ct, end_ct) is the
    # canonical primitive; the date-mode wrapper just computes day
    # bounds and delegates.
    try:
        layer_events = loki.fetch_decision_traces(
            "layer_resolution", start=start_utc, end=end_utc,
        )
        supervisor_events = loki.fetch_decision_traces(
            "supervisor", start=start_utc, end=end_utc,
        )
        hvac_actions = influx.fetch_hvac_actions_by_range(
            start_ct=start_ct, end_ct=end_ct,
        )
        sections_md["day_of"] = day_of.render(
            target_date=report_label,
            layer_events=layer_events,
            supervisor_events=supervisor_events,
            hvac_actions=hvac_actions,
        )
        sup_non_approved = day_of.count_supervisor_non_approved(supervisor_events)
        action_mismatches = day_of.count_action_fire_mismatches(
            layer_events=layer_events, hvac_actions=hvac_actions,
        )
    except Exception as exc:
        log.warning("§2 query failed: %s", exc)
        sections_md["day_of"] = f"## §2 Day-of\n\n⚠️ query failed: `{exc}`\n"
        sup_non_approved, action_mismatches = 0, 0
        query_errors += 1

    # §3 price spikes
    # Same pattern as §2 — range-mode Influx helper handles both modes.
    try:
        spikes = influx.fetch_comed_prices_above_by_range(
            start_ct=start_ct, end_ct=end_ct, threshold_cents=10.0,
        )
        overlay_events = loki.fetch_decision_traces(
            "price_overlay_eval", start=start_utc, end=end_utc,
        )
        sections_md["price_spikes"] = price_spikes.render(
            target_date=report_label, spikes=spikes, overlay_events=overlay_events,
        )
        unexplained = price_spikes.count_unexplained(
            spikes=spikes, overlay_events=overlay_events,
        )
    except Exception as exc:
        log.warning("§3 query failed: %s", exc)
        sections_md["price_spikes"] = (
            f"## §3 Price spikes\n\n⚠️ query failed: `{exc}`\n"
        )
        unexplained = 0
        query_errors += 1

    # §4 feed health
    try:
        from datetime import timedelta as td2
        now_utc = datetime.now(timezone.utc)
        # Compute expected last-fire timestamps for event feeds, in UTC.
        last_da_lmp_fire = _last_da_lmp_fire_utc(now_utc)
        last_metered_load_fire = _last_metered_load_fire_utc(now_utc)
        feeds = [
            {"name": "comed.prices", "kind": "continuous",
             "last_write": influx.last_write_time("comed.prices"),
             "warn": td2(minutes=5), "stale": td2(minutes=10)},
            {"name": "nws.forecast", "kind": "continuous",
             "last_write": influx.last_write_time("nws.forecast"),
             "warn": td2(minutes=60), "stale": td2(hours=2)},
            {"name": "refoss.channel", "kind": "continuous",
             "last_write": influx.last_write_time("refoss.channel"),
             "warn": td2(minutes=2), "stale": td2(minutes=5)},
            {"name": "hvac.thermostat", "kind": "continuous",
             "last_write": influx.last_write_time("hvac.thermostat"),
             "warn": td2(minutes=15), "stale": td2(minutes=30)},
            {"name": "haven.indoor", "kind": "continuous",
             "last_write": influx.last_write_time("haven.indoor"),
             "warn": td2(minutes=10), "stale": td2(minutes=30)},
            {"name": "pjm.inst_load", "kind": "continuous",
             "last_write": influx.last_write_time("pjm.inst_load"),
             "warn": td2(minutes=10), "stale": td2(minutes=30)},
            # Event feeds — spec §4 explicitly requires both. Missing
            # last_write (None) renders as "missing" + counts as stale
            # per feed_health._classify_feed.
            {"name": "pjm.lmp_da_hourly", "kind": "event",
             "last_write": influx.last_write_time("pjm.lmp_da_hourly"),
             "expected_fire_description": "17:00 CT daily",
             "last_expected_fire_utc": last_da_lmp_fire,
             "grace": td2(hours=2)},
            {"name": "pjm.metered_load", "kind": "event",
             "last_write": influx.last_write_time("pjm.metered_load"),
             "expected_fire_description": "Sunday 02:00 CT weekly",
             "last_expected_fire_utc": last_metered_load_fire,
             "grace": td2(hours=6)},
        ]
        # DO NOT filter feeds with last_write=None — those are surfaced
        # as "missing" by feed_health._classify_feed. Silently dropping
        # them is the exact failure mode this report is meant to catch.
        sections_md["feed_health"] = feed_health.render(now=now_utc, feeds=feeds)
        stale = feed_health.count_stale(now=now_utc, feeds=feeds)
    except Exception as exc:
        log.warning("§4 query failed: %s", exc)
        sections_md["feed_health"] = f"## §4 Feed health\n\n⚠️ query failed: `{exc}`\n"
        stale = 0
        query_errors += 1

    # §5 coverage scorecard
    try:
        reference_codes = decision_codes_loader.load_reference_codes()
        # Live counts via LokiClient.count_reason_codes over two windows.
        # 30d for cumulative is a Loki-retention-friendly proxy for
        # "since trace started" — if retention is shorter, Loki returns
        # what it has and the function still produces a usable summary.
        from datetime import timedelta as td3
        cumulative_start = (now_utc - td3(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        recent_start = (now_utc - td3(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cumulative_counts = loki.count_reason_codes(
            start=cumulative_start,
            end=end_utc,
        )
        recent_7d_counts = loki.count_reason_codes(
            start=recent_start,
            end=end_utc,
        )
        sections_md["coverage_scorecard"] = coverage_scorecard.render(
            reference_codes=reference_codes,
            cumulative_counts=cumulative_counts,
            recent_7d_counts=recent_7d_counts,
        )
        unexpected = coverage_scorecard.count_unexpected(
            reference_codes=reference_codes,
            cumulative_counts=cumulative_counts,
        )
    except Exception as exc:
        log.warning("§5 failed: %s", exc)
        sections_md["coverage_scorecard"] = (
            f"## §5 Coverage scorecard\n\n⚠️ query/load failed: `{exc}`\n"
        )
        unexpected = 0
        query_errors += 1

    summary = renderer.AnomalySummary(
        unexpected_codes=unexpected,
        supervisor_non_approved=sup_non_approved,
        stale_feeds=stale,
        trace_influx_discrepancies=nb_disc + action_mismatches,
        unexplained_spikes=unexplained,
        query_errors=query_errors,
    )

    full_md = renderer.build_report(
        target_date=report_label,
        rendered_at=datetime.now(timezone.utc),
        sections=sections_md,
        anomaly_summary=summary,
    )

    # Sanitize label for Windows filesystems — colons (in custom-range
    # labels like "2026-05-14T13:00__to__2026-05-14T19:30") and other
    # path-hostile chars become underscores.
    safe_label = re.sub(r'[\\/:*?"<>|]', "_", report_label)
    output_path = Path(args.output) if args.output else (
        DEFAULT_OUTPUT_DIR / f"{safe_label}-decision-trace.md"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(full_md, encoding="utf-8")
    log.info("wrote report to %s (%d lines)", output_path, full_md.count("\n"))

    # Heartbeat
    if not args.no_telegram:
        try:
            tg = TelegramClient(
                bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
                chat_id=os.environ["TELEGRAM_CHAT_ID"],
            )
            tg.send_message(
                f"Decision-trace commissioning report ready: {report_label}\n"
                f"Anomalies: {summary.total()} "
                f"({summary.unexpected_codes} unexpected codes, "
                f"{summary.supervisor_non_approved} sup non-approved, "
                f"{summary.stale_feeds} stale feeds, "
                f"{summary.query_errors} query errors)\n"
                f"Status: {summary.heartbeat_status()}\n"
                f"File: {output_path}"
            )
        except Exception as exc:
            log.warning("telegram heartbeat failed: %s", exc)

    return 0


if __name__ == "__main__":
    sys.exit(main())
