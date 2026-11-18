"""HAVEN Central Air Monitor local HTTP API poller → InfluxDB.

Polls the monitor's on-device API (``http://<HAVEN_HOST>/api/v1``) and writes
``haven.indoor``. No cloud account, no Auth0 refresh token: the local API is
unauthenticated on the LAN. It is off by default on the device and is turned on
by enabling "Home Assistant" for the monitor in the HAVEN app (firmware 2.5.1+).

Until 2026-11-18 this service polled the HAVEN cloud API (havenapi.tzoa.io)
with a refresh token that had to be re-captured from a browser login about
every 30 days. That path, the startup backfill and the ``haven.outdoor``
measurement (cloud-only) were removed; outdoor history stays in InfluxDB.

Endpoints used:
  GET /api/v1/info     once at startup — api_version, product_type, serial
  GET /api/v1/sensors  every poll — cached latest readings

Field mapping (local → haven.indoor). Legacy cloud field names are kept so
existing Grafana panels and the kiosk keep working; equivalence was checked
against simultaneous cloud and local reads on 2026-11-18:
  temperature_c     ← temperature_c
  humidity          ← humidity_pct
  pm_mc             ← pm25_ugm3
  pm_count          ← pm25_count_cm3
  airflow           ← airflow_mps
  voc_count         ← tvoc_ppb             (cloud voc_count was integer ppb)
  voc_mc            ← tvoc_ppb × 40/17     (the cloud's ppb→µg/m³ factor)
New fields available only locally:
  pressure_kpa, pm10_ugm3, pm10_count_cm3, airflow_duration_s,
  sensor_age_s (seconds between the device's reading and the poll)
Not written:
  co2_ppm           HAVEN states the Central Air Monitor does not measure CO2
  *_status grades   computed by the HAVEN cloud, not exposed locally

The point time is the device's reading time: poll time minus
``latest_sensor_age_s``. A reading that is not ready (``sensor_ready`` false)
is skipped.

Environment variables:
    HAVEN_HOST              Monitor address (default 192.168.1.227)
    HAVEN_DEVICE_ID         device_id tag value (default 3645, the cloud ID the
                            series has always carried; kept for continuity)
    HAVEN_EXPECTED_SERIAL   Optional. If set, startup refuses a device whose
                            serial differs (guards a DHCP reassignment)
    HAVEN_POLL_INTERVAL     Seconds between polls (default 300)
    INFLUXDB_URL            Default http://influxdb:8086
    INFLUXDB_TOKEN          InfluxDB write token
    INFLUXDB_ORG            InfluxDB organization
    INFLUXDB_BUCKET         Target bucket
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import FrameType
from typing import Any, cast

import requests
from influxdb_client import InfluxDBClient, Point, WritePrecision  # type: ignore[attr-defined]  # stubs lack __all__
from influxdb_client.client.write_api import SYNCHRONOUS

HEALTH_MARKER = Path("/tmp/last_poll_ok")
API_PATH = "/api/v1"
SUPPORTED_API_VERSION = 1
SUPPORTED_PRODUCT = "cam"  # Central Air Monitor
REQUEST_TIMEOUT_S = 10
STARTUP_RETRY_S = 30

# The HAVEN cloud converted tVOC ppb to µg/m³ with this factor (voc_mc / voc_count
# was exactly 2.3529… across 30 days of cloud data).
VOC_PPB_TO_UGM3 = 40.0 / 17.0

# (local key, haven.indoor field) for direct numeric copies.
FIELD_MAP: tuple[tuple[str, str], ...] = (
    ("temperature_c", "temperature_c"),
    ("humidity_pct", "humidity"),
    ("pm25_ugm3", "pm_mc"),
    ("pm25_count_cm3", "pm_count"),
    ("airflow_mps", "airflow"),
    ("tvoc_ppb", "voc_count"),
    ("pressure_kpa", "pressure_kpa"),
    ("pm10_ugm3", "pm10_ugm3"),
    ("pm10_count_cm3", "pm10_count_cm3"),
    ("airflow_duration_s", "airflow_duration_s"),
)


def log(level: str, msg: str, **fields: Any) -> None:
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "level": level, "msg": msg}
    rec.update(fields)
    print(json.dumps(rec, default=str), flush=True)


@dataclass
class Config:
    host: str
    device_id: str
    expected_serial: str
    poll_interval: float
    influx_url: str
    influx_token: str
    influx_org: str
    influx_bucket: str

    @property
    def base_url(self) -> str:
        return f"http://{self.host}{API_PATH}"

    @staticmethod
    def from_env() -> "Config":
        def required(name: str) -> str:
            v = os.environ.get(name)
            if not v:
                log("error", "missing_env", var=name)
                sys.exit(2)
            return v

        return Config(
            host=os.environ.get("HAVEN_HOST", "192.168.1.227"),
            device_id=os.environ.get("HAVEN_DEVICE_ID", "3645"),
            expected_serial=os.environ.get("HAVEN_EXPECTED_SERIAL", ""),
            poll_interval=float(os.environ.get("HAVEN_POLL_INTERVAL", "300")),
            influx_url=os.environ.get("INFLUXDB_URL", "http://influxdb:8086"),
            influx_token=required("INFLUXDB_TOKEN"),
            influx_org=required("INFLUXDB_ORG"),
            influx_bucket=required("INFLUXDB_BUCKET"),
        )


class DeviceMismatch(Exception):
    """The device at HAVEN_HOST is not one this service can or should poll."""


class HavenLocalClient:
    def __init__(self, cfg: Config) -> None:
        self._base = cfg.base_url

    def _get(self, endpoint: str) -> dict[str, Any]:
        r = requests.get(f"{self._base}/{endpoint}", timeout=REQUEST_TIMEOUT_S)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise ValueError(f"/{endpoint} returned {type(data).__name__}, expected object")
        return cast(dict[str, Any], data)

    def info(self) -> dict[str, Any]:
        return self._get("info")

    def sensors(self) -> dict[str, Any]:
        return self._get("sensors")


def check_info(info: dict[str, Any], expected_serial: str) -> None:
    """Raise DeviceMismatch unless the device speaks API v1 as a Central Air
    Monitor (and carries the expected serial, when one is configured)."""
    api_version = info.get("api_version")
    if api_version != SUPPORTED_API_VERSION:
        raise DeviceMismatch(f"unsupported api_version {api_version!r}")
    product = info.get("product_type")
    if product != SUPPORTED_PRODUCT:
        raise DeviceMismatch(f"unsupported product_type {product!r}")
    serial = info.get("serial_number")
    if expected_serial and serial != expected_serial:
        raise DeviceMismatch(f"serial {serial!r} != expected {expected_serial!r}")


def _num(v: Any) -> float | None:
    # bool is an int subclass; never treat it as a reading.
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def sensors_to_point(s: dict[str, Any], device_id: str, polled_at: datetime) -> Point | None:
    """Map a /sensors response to a haven.indoor point, or None when the
    device reports it has no ready reading (or no usable fields)."""
    if s.get("sensor_ready") is not True:
        return None

    point: Any = Point("haven.indoor").tag("device_id", device_id)  # type: ignore[no-untyped-call]
    n_fields = 0
    for src, dst in FIELD_MAP:
        v = _num(s.get(src))
        if v is not None:
            point = point.field(dst, v)
            n_fields += 1
    tvoc = _num(s.get("tvoc_ppb"))
    if tvoc is not None:
        point = point.field("voc_mc", tvoc * VOC_PPB_TO_UGM3)
    if n_fields == 0:
        return None

    age = _num(s.get("latest_sensor_age_s"))
    ts = polled_at
    if age is not None and age >= 0:
        point = point.field("sensor_age_s", age)
        ts = polled_at - timedelta(seconds=age)
    point = point.time(ts, WritePrecision.S)
    return cast(Point, point)


def main() -> int:
    cfg = Config.from_env()
    log("info", "startup", host=cfg.host, device_id=cfg.device_id,
        poll_interval=cfg.poll_interval)

    client = HavenLocalClient(cfg)
    influx = InfluxDBClient(url=cfg.influx_url, token=cfg.influx_token, org=cfg.influx_org)
    write_api = influx.write_api(write_options=SYNCHRONOUS)

    stop_requested = False

    def handle_stop(signum: int, _frame: FrameType | None) -> None:
        nonlocal stop_requested
        log("info", "signal_received", signum=signum)
        stop_requested = True

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    def sleep_until(deadline: float) -> None:
        while not stop_requested and time.monotonic() < deadline:
            time.sleep(min(1.0, deadline - time.monotonic()))

    try:
        # Identify the device before polling. A wrong device is fatal (exit so
        # the restart loop and logs make it visible); an unreachable one is
        # retried, since the monitor reboots and Wi-Fi drops happen.
        while not stop_requested:
            try:
                info = client.info()
                check_info(info, cfg.expected_serial)
                log("info", "device_ok", serial=info.get("serial_number"),
                    model=info.get("model"), fw_ver=info.get("fw_ver"))
                break
            except DeviceMismatch as exc:
                log("error", "device_mismatch", host=cfg.host, error=str(exc))
                return 2
            except Exception as exc:
                log("error", "device_info_failed", host=cfg.host,
                    error=str(exc), error_type=type(exc).__name__)
                sleep_until(time.monotonic() + STARTUP_RETRY_S)

        while not stop_requested:
            try:
                polled_at = datetime.now(timezone.utc)
                data = client.sensors()
                point = sensors_to_point(data, cfg.device_id, polled_at)
                if point is None:
                    log("warning", "sensor_not_ready", sensor_ready=data.get("sensor_ready"))
                else:
                    write_api.write(bucket=cfg.influx_bucket, record=point)
                    HEALTH_MARKER.touch()
                    log("info", "poll_ok",
                        temp_c=data.get("temperature_c"),
                        humidity=data.get("humidity_pct"),
                        tvoc_ppb=data.get("tvoc_ppb"),
                        pm25_ugm3=data.get("pm25_ugm3"),
                        sensor_age_s=data.get("latest_sensor_age_s"))
            except Exception as exc:
                log("error", "poll_failed", error=str(exc), error_type=type(exc).__name__)

            sleep_until(time.monotonic() + cfg.poll_interval)
    finally:
        log("info", "shutdown")
        influx.close()  # type: ignore[no-untyped-call]  # influxdb_client stubs lack close annotation
    return 0


if __name__ == "__main__":
    sys.exit(main())
