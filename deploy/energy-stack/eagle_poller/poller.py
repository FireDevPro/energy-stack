"""EAGLE-3 → InfluxDB poller.

Polls the Rainforest EAGLE-3 Local API on a fixed interval and writes
instantaneous demand and cumulative summation counters to InfluxDB.

Environment variables (all required except noted):
    EAGLE_IP                  Meter host (e.g., 192.168.20.192)
    EAGLE_CLOUD_ID            Basic-auth username
    EAGLE_INSTALL_CODE        Basic-auth password
    EAGLE_METER_HW_ADDRESS    Zigbee HardwareAddress of the meter
    EAGLE_POLL_INTERVAL       Seconds between polls (default 30)
    INFLUXDB_URL              Default http://influxdb:8086
    INFLUXDB_TOKEN            Admin or write token
    INFLUXDB_ORG              InfluxDB organization
    INFLUXDB_BUCKET           Target bucket
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
import urllib3
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

HEALTH_MARKER = Path("/tmp/last_poll_ok")
from influxdb_client import InfluxDBClient, Point  # type: ignore[attr-defined]  # stubs lack __all__
from influxdb_client.client.write_api import SYNCHRONOUS

# EAGLE-3 uses a self-signed cert on the local network. We verify the
# destination via the local-network IP + Basic auth rather than PKI.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

EAGLE_VARS = (
    "zigbee:InstantaneousDemand",
    "zigbee:CurrentSummationDelivered",
    "zigbee:CurrentSummationReceived",
)

FIELD_BY_VAR = {
    "zigbee:InstantaneousDemand": "demand_kw",
    "zigbee:CurrentSummationDelivered": "delivered_kwh",
    "zigbee:CurrentSummationReceived": "received_kwh",
}


def log(level: str, msg: str, **fields: object) -> None:
    record: dict[str, Any] = {"ts": datetime.now(timezone.utc).isoformat(), "level": level, "msg": msg}
    record.update(fields)
    print(json.dumps(record), flush=True)


@dataclass(frozen=True)
class Config:
    eagle_url: str
    eagle_auth: tuple[str, str]
    meter_hw: str
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

        ip = required("EAGLE_IP")
        return Config(
            eagle_url=f"https://{ip}/cgi-bin/post_manager",
            eagle_auth=(required("EAGLE_CLOUD_ID"), required("EAGLE_INSTALL_CODE")),
            meter_hw=required("EAGLE_METER_HW_ADDRESS"),
            poll_interval=float(os.environ.get("EAGLE_POLL_INTERVAL", "30")),
            influx_url=os.environ.get("INFLUXDB_URL", "http://influxdb:8086"),
            influx_token=required("INFLUXDB_TOKEN"),
            influx_org=required("INFLUXDB_ORG"),
            influx_bucket=required("INFLUXDB_BUCKET"),
        )


def post_eagle(cfg: Config, body: str, timeout_s: float = 10.0) -> ET.Element:
    response = requests.post(
        cfg.eagle_url,
        auth=cfg.eagle_auth,
        data=body,
        headers={"Content-Type": "text/xml"},
        verify=False,
        timeout=(5.0, timeout_s),
    )
    response.raise_for_status()
    return ET.fromstring(response.text)


def assert_meter_present(cfg: Config) -> None:
    """Confirm the configured meter is visible on startup. Exit non-zero if not."""
    root = post_eagle(cfg, "<Command><Name>device_list</Name></Command>")
    addresses = extract_hardware_addresses(root)
    if cfg.meter_hw not in addresses:
        log(
            "error",
            "meter_not_found",
            configured=cfg.meter_hw,
            discovered=addresses,
        )
        sys.exit(3)
    log("info", "meter_found", hw_address=cfg.meter_hw)


def extract_variable_value(root: ET.Element, zigbee_name: str) -> float | None:
    """Pull a named variable's float value from a device_query XML response.

    EAGLE-3 returns values as pre-formatted ASCII strings with units, like
    ``"1.602 kW"`` or ``"78967.773 kWh"``. The split-and-float parses any
    unit suffix uniformly. Returns None if the variable isn't in the response
    or the Value element is empty.
    """
    for variable in root.iter("Variable"):
        name_el = variable.find("Name")
        value_el = variable.find("Value")
        if name_el is None or value_el is None or not value_el.text:
            continue
        if name_el.text == zigbee_name:
            return float(value_el.text.split()[0])
    return None


def extract_hardware_addresses(root: ET.Element) -> list[str]:
    """Pull all <HardwareAddress> text from a device_list response."""
    return [e.text for e in root.iter("HardwareAddress") if e.text]


def query_one_variable(cfg: Config, zigbee_name: str) -> float | None:
    """Query a single meter variable.

    The EAGLE-3 firmware drops variables at position 2+ when multiple are
    requested in one device_query call, so we issue one POST per variable.
    Returns the float value, or None if the meter didn't include it.
    """
    body = (
        "<Command>"
        "<Name>device_query</Name>"
        f"<DeviceDetails><HardwareAddress>{cfg.meter_hw}</HardwareAddress></DeviceDetails>"
        "<Components><Component>"
        "<Name>Main</Name>"
        f"<Variables><Variable><Name>{zigbee_name}</Name></Variable></Variables>"
        "</Component></Components>"
        "</Command>"
    )
    root = post_eagle(cfg, body)
    return extract_variable_value(root, zigbee_name)


def query_meter(cfg: Config) -> dict[str, float]:
    readings: dict[str, float] = {}
    for zigbee_name in EAGLE_VARS:
        try:
            value = query_one_variable(cfg, zigbee_name)
        except Exception as exc:
            log(
                "warn",
                "variable_read_failed",
                variable=zigbee_name,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            continue
        if value is None:
            log("warn", "variable_missing_in_response", variable=zigbee_name)
            continue
        readings[FIELD_BY_VAR[zigbee_name]] = value
    return readings


def write_point(influx_write: Any, bucket: str, cfg: Config, readings: dict[str, float]) -> None:
    point = (
        Point("eagle.meter")  # type: ignore[no-untyped-call]
        .tag("hw_address", cfg.meter_hw)
        .tag("source", "eagle3")
    )
    for field, value in readings.items():
        point = point.field(field, value)
    influx_write.write(bucket=bucket, record=point)


def main() -> int:
    cfg = Config.from_env()
    log(
        "info",
        "startup",
        eagle_url=cfg.eagle_url,
        meter=cfg.meter_hw,
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

    assert_meter_present(cfg)

    influx = InfluxDBClient(url=cfg.influx_url, token=cfg.influx_token, org=cfg.influx_org)
    write_api = influx.write_api(write_options=SYNCHRONOUS)

    try:
        while not stop_requested:
            cycle_start = time.monotonic()
            try:
                readings = query_meter(cfg)
            except Exception as exc:
                # Unexpected top-level failure — log and try next cycle.
                log("warn", "eagle_read_failed", error=str(exc), error_type=type(exc).__name__)
            else:
                if not readings:
                    # All three variable queries failed. Don't write an empty
                    # point; wait for the next cycle.
                    log("warn", "empty_reading")
                else:
                    if len(readings) < len(FIELD_BY_VAR):
                        log("warn", "partial_reading", got=sorted(readings.keys()))
                    # Influx write errors are intentionally NOT caught — they
                    # bubble up and kill the process, letting Docker restart
                    # the container.
                    write_point(write_api, cfg.influx_bucket, cfg, readings)
                    log("info", "poll_ok", **readings)
                    HEALTH_MARKER.touch()

            elapsed = time.monotonic() - cycle_start
            sleep_for = max(0.0, cfg.poll_interval - elapsed)
            # Break the sleep into 1-second chunks so SIGTERM is responsive.
            deadline = time.monotonic() + sleep_for
            while not stop_requested and time.monotonic() < deadline:
                time.sleep(min(1.0, deadline - time.monotonic()))
    finally:
        log("info", "shutdown")
        influx.close()  # type: ignore[no-untyped-call]
    return 0


if __name__ == "__main__":
    sys.exit(main())
