"""ComEd Hourly Pricing → InfluxDB poller.

Polls the public ComEd Hourly Pricing API on a fixed interval and writes the
current 5-minute real-time price and current hour average to InfluxDB.

The API is unauthenticated and returns JSON. Two endpoints:
  * /api?type=5minutefeed&format=json        — last ~24 h of 5-min intervals
  * /api?type=currenthouraverage&format=json — single-entry current hour average

Measurement: `comed.prices`
Tag:         `period_type` = "5min" | "hourly_avg"
Field:       `price_cents_per_kwh` (float)

Points are written with the source-native timestamp (millisUTC for 5-min, the
current hour truncated for hourly_avg) so repeated polls during the same window
idempotently overwrite rather than duplicate.

Environment variables:
    COMED_POLL_INTERVAL   Seconds between polls (default 60)
    COMED_API_BASE        Default https://hourlypricing.comed.com/api
    INFLUXDB_URL          Default http://influxdb:8086
    INFLUXDB_TOKEN        Admin or write token
    INFLUXDB_ORG          InfluxDB organization
    INFLUXDB_BUCKET       Target bucket
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

HEALTH_MARKER = Path("/tmp/last_poll_ok")
from influxdb_client import InfluxDBClient, Point, WritePrecision  # type: ignore[attr-defined]  # stubs lack __all__
from influxdb_client.client.write_api import SYNCHRONOUS


def log(level: str, msg: str, **fields: object) -> None:
    record: dict[str, Any] = {"ts": datetime.now(timezone.utc).isoformat(), "level": level, "msg": msg}
    record.update(fields)
    print(json.dumps(record), flush=True)


@dataclass(frozen=True)
class Config:
    api_base: str
    poll_interval: float
    influx_url: str
    influx_token: str
    influx_org: str
    influx_bucket: str

    @staticmethod
    def from_env() -> "Config":
        def required(name: str) -> str:
            value = os.environ.get(name)
            if not value:
                log("error", "missing_env", var=name)
                sys.exit(2)
            return value

        return Config(
            api_base=os.environ.get("COMED_API_BASE", "https://hourlypricing.comed.com/api"),
            poll_interval=float(os.environ.get("COMED_POLL_INTERVAL", "60")),
            influx_url=os.environ.get("INFLUXDB_URL", "http://influxdb:8086"),
            influx_token=required("INFLUXDB_TOKEN"),
            influx_org=required("INFLUXDB_ORG"),
            influx_bucket=required("INFLUXDB_BUCKET"),
        )


def parse_current_hour_avg(data: list[dict[str, Any]]) -> float | None:
    """Pull the price from a ``type=currenthouraverage`` response payload."""
    if not data:
        return None
    return float(data[0]["price"])


def parse_latest_5min(data: list[dict[str, Any]]) -> tuple[int, float] | None:
    """Pick the most recent (millisUTC, price_cents) tuple from a
    ``type=5minutefeed`` response payload.

    The API returns entries unordered (or close to it); pick the largest
    millisUTC rather than trusting position. Returns None on empty input.
    """
    if not data:
        return None
    latest = max(data, key=lambda entry: int(entry["millisUTC"]))
    return int(latest["millisUTC"]), float(latest["price"])


def fetch_current_hour_avg(cfg: Config) -> float | None:
    response = requests.get(
        f"{cfg.api_base}?type=currenthouraverage&format=json",
        timeout=(5.0, 15.0),
    )
    response.raise_for_status()
    return parse_current_hour_avg(response.json())


def fetch_latest_5min(cfg: Config) -> tuple[int, float] | None:
    """Return (millis_utc, price_cents) for the most recent 5-minute interval."""
    response = requests.get(
        f"{cfg.api_base}?type=5minutefeed&format=json",
        timeout=(5.0, 15.0),
    )
    response.raise_for_status()
    return parse_latest_5min(response.json())


def write_points(write_api: Any, cfg: Config, hourly_avg: float | None,
                 five_min: tuple[int, float] | None) -> int:
    points: list[Point] = []

    if hourly_avg is not None:
        # Truncate to the current hour so repeated polls upsert the same point.
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        points.append(
            Point("comed.prices")  # type: ignore[no-untyped-call]
            .tag("period_type", "hourly_avg")
            .field("price_cents_per_kwh", hourly_avg)
            .time(now, WritePrecision.S)
        )

    if five_min is not None:
        millis_utc, price = five_min
        points.append(
            Point("comed.prices")  # type: ignore[no-untyped-call]
            .tag("period_type", "5min")
            .field("price_cents_per_kwh", price)
            .time(millis_utc * 1_000_000, WritePrecision.NS)
        )

    if points:
        write_api.write(bucket=cfg.influx_bucket, record=points)
    return len(points)


def main() -> int:
    cfg = Config.from_env()
    log(
        "info",
        "startup",
        api_base=cfg.api_base,
        poll_interval_s=cfg.poll_interval,
        influx_url=cfg.influx_url,
        bucket=cfg.influx_bucket,
    )

    stop_requested = False

    def handle_stop(signum: int, _frame: Any) -> None:
        nonlocal stop_requested
        log("info", "signal_received", signum=signum)
        stop_requested = True

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    influx = InfluxDBClient(url=cfg.influx_url, token=cfg.influx_token, org=cfg.influx_org)
    write_api = influx.write_api(write_options=SYNCHRONOUS)

    try:
        while not stop_requested:
            # Wall-clock phase alignment: each cycle starts at the next
            # XX:XX:00 boundary. Makes the poll/scheduler timing
            # deterministic across restarts (container boot time no
            # longer dictates poll phase). Preserved interruptible-sleep
            # loop so SIGTERM still drops the wait within ~1s.
            now = time.time()
            sleep_for = (cfg.poll_interval - (now % cfg.poll_interval)) % cfg.poll_interval
            deadline = time.monotonic() + sleep_for
            while not stop_requested and time.monotonic() < deadline:
                time.sleep(min(1.0, deadline - time.monotonic()))
            # SIGTERM during the wall-clock-aligned sleep: skip the work
            # and let the outer-while condition exit. mypy can't track
            # the signal-handler-set `stop_requested`, hence the ignore.
            if stop_requested:
                break  # type: ignore[unreachable]

            cycle_start = time.monotonic()
            hourly_avg = None
            five_min = None
            try:
                hourly_avg = fetch_current_hour_avg(cfg)
            except Exception as exc:
                log("warn", "hourly_avg_read_failed", error=str(exc),
                    error_type=type(exc).__name__)
            try:
                five_min = fetch_latest_5min(cfg)
            except Exception as exc:
                log("warn", "five_min_read_failed", error=str(exc),
                    error_type=type(exc).__name__)

            if hourly_avg is None and five_min is None:
                log("warn", "empty_reading")
            else:
                # Influx write errors are NOT caught — they bubble up and kill
                # the process so Docker restarts it.
                written = write_points(write_api, cfg, hourly_avg, five_min)
                log(
                    "info",
                    "poll_ok",
                    hourly_avg_cents=hourly_avg,
                    latest_5min_cents=five_min[1] if five_min else None,
                    latest_5min_ts_ms=five_min[0] if five_min else None,
                    points_written=written,
                )
                HEALTH_MARKER.touch()
    finally:
        log("info", "shutdown")
        influx.close()  # type: ignore[no-untyped-call]
    return 0


if __name__ == "__main__":
    sys.exit(main())
