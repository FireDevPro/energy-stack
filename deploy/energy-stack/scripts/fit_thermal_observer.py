#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from thermal_observer import FitConfig, fit_thermal_response
from thermal_observer_influx import build_query, rows_to_samples


REQUIRED_ENV_VARS = ("INFLUX_URL", "INFLUX_TOKEN", "INFLUX_ORG", "INFLUX_BUCKET")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fit the HVAC thermal observer model.")
    parser.add_argument("--window-days", type=int, default=14)
    parser.add_argument("--sample-minutes", type=int, default=10)
    parser.add_argument("--min-samples", type=int, default=72)
    parser.add_argument("--stage1-min-pct", type=float, default=5.0)
    parser.add_argument("--stage2-min-pct", type=float, default=75.0)
    parser.add_argument("--outdoor-measurement", default="ecowitt.outdoor")
    parser.add_argument("--outdoor-temp-field", default="outdoor_temp_f")
    parser.add_argument("--solar-field", default="solar_radiation_w_m2")
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path.home() / "energy-stack" / "runtime" / "thermal_observer_latest.json",
        help="Deprecated no-op; retained so older operator commands stay read-only.",
    )
    parser.add_argument("--dry-run", action="store_true", help="No-op; all runs are read-only.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    env = _required_env()
    if env is None:
        return 2

    from influxdb_client import InfluxDBClient

    query = build_query(
        bucket=env["INFLUX_BUCKET"],
        start=f"-{args.window_days}d",
        sample_minutes=args.sample_minutes,
        outdoor_measurement=args.outdoor_measurement,
        outdoor_temp_field=args.outdoor_temp_field,
        solar_field=args.solar_field,
    )

    with InfluxDBClient(
        url=env["INFLUX_URL"],
        token=env["INFLUX_TOKEN"],
        org=env["INFLUX_ORG"],
        timeout=60_000,
    ) as client:
        rows = _query_rows(client, query, env["INFLUX_ORG"])
        samples = rows_to_samples(rows)
        result = fit_thermal_response(
            samples,
            FitConfig(
                sample_minutes=args.sample_minutes,
                min_samples=args.min_samples,
                stage1_min_pct=args.stage1_min_pct,
                stage2_min_pct=args.stage2_min_pct,
            ),
        )

        _print_summary(result, len(rows), len(samples))

    return 0 if result.total_interval_count > 0 else 1


def _required_env() -> dict[str, str] | None:
    missing = [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing:
        print(
            "Missing required environment variables: " + ", ".join(missing),
            file=sys.stderr,
        )
        return None
    return {name: os.environ[name] for name in REQUIRED_ENV_VARS}


def _query_rows(client: Any, query: str, org: str) -> list[dict[str, Any]]:
    tables = client.query_api().query(org=org, query=query)
    rows: list[dict[str, Any]] = []
    for table in tables:
        for record in table.records:
            rows.append(dict(record.values))
    return rows


def _print_summary(result: Any, row_count: int, sample_count: int) -> None:
    print(f"Rows: {row_count}")
    print(f"Samples: {sample_count}")
    print(f"Intervals: {result.total_interval_count}")
    print(f"Accepted: {result.accepted}")
    if result.rejection_reasons:
        print("Rejection reasons: " + ", ".join(result.rejection_reasons))
    print(f"Tau hours: {_format_optional(result.tau_hours)}")
    print(f"Stage 1 cooling F/hr: {_format_optional(result.stage1_cooling_f_per_hr)}")
    print(f"Stage 2 cooling F/hr: {_format_optional(result.stage2_cooling_f_per_hr)}")
    print(f"Train RMSE F/sample: {_format_optional(result.train_rmse_f_per_sample)}")
    print(f"Test RMSE F/sample: {_format_optional(result.test_rmse_f_per_sample)}")
    print(f"Persistence RMSE F/sample: {_format_optional(result.persistence_rmse_f_per_sample)}")
    print(f"Skill score: {_format_optional(result.skill_score)}")
    print(f"Fit window start: {result.fit_window_start or 'n/a'}")
    print(f"Fit window end: {result.fit_window_end or 'n/a'}")
    print("Filter counts:")
    for name in sorted(result.filter_counts):
        print(f"  {name}: {result.filter_counts[name]}")


def _format_optional(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.6g}"


if __name__ == "__main__":
    raise SystemExit(main())
