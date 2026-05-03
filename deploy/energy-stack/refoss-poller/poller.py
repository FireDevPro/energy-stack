"""Refoss EM16P -> InfluxDB poller.

Polls a Refoss EM16P (Smart Energy Monitor) over the local LAN via JSON-RPC 2.0
and writes per-channel power/voltage/current/PF and day/week/month energy
buckets to InfluxDB. The EM16P exposes 18 `em:N` channel entries on this
firmware (mains and branches share the same namespace) plus optional
`emmerge:N` aggregate entries; the device returns empty objects for the
aggregates so we just write the per-channel data and compute totals in Grafana.

Channel labels are pulled from the device itself (whatever the user named
them in the Refoss app) via `Refoss.Config.Get`. The cache is refreshed
whenever the `sys.cfg_rev` counter in the live status response increments,
so renaming a circuit in the app propagates within one poll cycle.

Measurements:
  * `refoss.channel` - one point per `em:N` channel per cycle.
    Tags: channel ("em:1".."em:18"), name (user label).
    Fields: power_w, voltage_v, current_a, power_factor,
            day_energy_kwh, day_ret_energy_kwh,
            week_energy_kwh, week_ret_energy_kwh,
            month_energy_kwh, month_ret_energy_kwh.
  * `refoss.system` - one point per cycle.
    Fields: uptime_s, wifi_rssi_dbm, cfg_rev.

Environment variables:
    REFOSS_IP              Device IP (e.g., 192.168.20.140)
    REFOSS_POLL_INTERVAL   Seconds between polls (default 30)
    INFLUXDB_URL           Default http://influxdb:8086
    INFLUXDB_TOKEN         Admin or write token
    INFLUXDB_ORG           InfluxDB organization
    INFLUXDB_BUCKET        Target bucket
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

import requests

HEALTH_MARKER = Path("/tmp/last_poll_ok")
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS


CHANNEL_FIELDS = {
    "power": ("power_w", float),
    "voltage": ("voltage_v", float),
    "current": ("current_a", float),
    "pf": ("power_factor", float),
    "day_energy": ("day_energy_kwh", float),
    "day_ret_energy": ("day_ret_energy_kwh", float),
    "week_energy": ("week_energy_kwh", float),
    "week_ret_energy": ("week_ret_energy_kwh", float),
    "month_energy": ("month_energy_kwh", float),
    "month_ret_energy": ("month_ret_energy_kwh", float),
}


def log(level: str, msg: str, **fields: object) -> None:
    record = {"ts": datetime.now(timezone.utc).isoformat(), "level": level, "msg": msg}
    record.update(fields)
    print(json.dumps(record), flush=True)


@dataclass(frozen=True)
class Config:
    refoss_url: str
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

        ip = required("REFOSS_IP")
        return Config(
            refoss_url=f"http://{ip}/rpc",
            poll_interval=float(os.environ.get("REFOSS_POLL_INTERVAL", "30")),
            influx_url=os.environ.get("INFLUXDB_URL", "http://influxdb:8086"),
            influx_token=required("INFLUXDB_TOKEN"),
            influx_org=required("INFLUXDB_ORG"),
            influx_bucket=required("INFLUXDB_BUCKET"),
        )


def rpc_get(cfg: Config, method: str, timeout_s: float = 10.0) -> dict:
    """Call a Refoss JSON-RPC method via the GET shortcut.

    The EM16P exposes `GET /rpc/<Method>` as an unauth'd shortcut for
    read-only methods, returning the result object directly (not wrapped in a
    JSON-RPC envelope). Auth is disabled on this deployment per the project's
    threat model (local LAN, single-user).
    """
    response = requests.get(
        f"{cfg.refoss_url}/{method}",
        timeout=(5.0, timeout_s),
    )
    response.raise_for_status()
    return response.json()


def assert_device_present(cfg: Config) -> None:
    """Confirm the device responds and is an EM16P. Exit non-zero on mismatch."""
    info = rpc_get(cfg, "Refoss.DeviceInfo.Get")
    model = info.get("model")
    if model != "em16p":
        log("error", "unexpected_model", configured_ip=cfg.refoss_url,
            model=model, info=info)
        sys.exit(3)
    log("info", "device_found", model=model, fw_ver=info.get("fw_ver"),
        mac=info.get("mac"), auth_en=info.get("auth_en"))


def fetch_channel_names(cfg: Config) -> dict[str, str]:
    """Return {channel_key: user_label} for every em:N channel in config."""
    config = rpc_get(cfg, "Refoss.Config.Get")
    names = {
        key: value.get("name") or key
        for key, value in config.items()
        if key.startswith("em:") and isinstance(value, dict)
    }
    log("info", "channel_names_loaded", count=len(names))
    return names


def build_channel_point(channel_key: str, name: str, data: dict) -> Point:
    point = (
        Point("refoss.channel")
        .tag("channel", channel_key)
        .tag("name", name)
    )
    for src_key, (field_name, caster) in CHANNEL_FIELDS.items():
        value = data.get(src_key)
        if value is None:
            continue
        point = point.field(field_name, caster(value))
    return point


def build_system_point(status: dict) -> Point | None:
    sys_block = status.get("sys") or {}
    wifi_block = status.get("wifi") or {}
    point = Point("refoss.system")
    wrote_any = False
    if "uptime" in sys_block:
        point = point.field("uptime_s", int(sys_block["uptime"]))
        wrote_any = True
    if "cfg_rev" in sys_block:
        point = point.field("cfg_rev", int(sys_block["cfg_rev"]))
        wrote_any = True
    rssi = wifi_block.get("rssi")
    if rssi is not None:
        point = point.field("wifi_rssi_dbm", int(rssi))
        wrote_any = True
    return point if wrote_any else None


def main() -> int:
    cfg = Config.from_env()
    log(
        "info",
        "startup",
        refoss_url=cfg.refoss_url,
        poll_interval_s=cfg.poll_interval,
        influx_url=cfg.influx_url,
        bucket=cfg.influx_bucket,
    )

    stop_requested = False

    def handle_stop(signum, _frame):
        nonlocal stop_requested
        log("info", "signal_received", signum=signum)
        stop_requested = True

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    assert_device_present(cfg)
    channel_names = fetch_channel_names(cfg)
    last_cfg_rev: int | None = None

    influx = InfluxDBClient(url=cfg.influx_url, token=cfg.influx_token, org=cfg.influx_org)
    write_api = influx.write_api(write_options=SYNCHRONOUS)

    try:
        while not stop_requested:
            cycle_start = time.monotonic()
            try:
                status = rpc_get(cfg, "Refoss.Status.Get")
            except Exception as exc:
                log("warn", "status_read_failed", error=str(exc),
                    error_type=type(exc).__name__)
            else:
                cfg_rev = (status.get("sys") or {}).get("cfg_rev")
                if cfg_rev is not None and cfg_rev != last_cfg_rev:
                    if last_cfg_rev is not None:
                        log("info", "cfg_rev_changed",
                            old=last_cfg_rev, new=cfg_rev)
                        try:
                            channel_names = fetch_channel_names(cfg)
                        except Exception as exc:
                            log("warn", "config_refresh_failed",
                                error=str(exc), error_type=type(exc).__name__)
                    last_cfg_rev = cfg_rev

                points: list[Point] = []
                channels_written = 0
                total_power_w = 0.0
                for key, value in status.items():
                    if not key.startswith("em:") or not isinstance(value, dict):
                        continue
                    if not value:  # empty body, e.g. unconfigured emmerge entry
                        continue
                    name = channel_names.get(key, key)
                    points.append(build_channel_point(key, name, value))
                    channels_written += 1
                    if "power" in value:
                        total_power_w += float(value["power"])

                sys_point = build_system_point(status)
                if sys_point is not None:
                    points.append(sys_point)

                if not points:
                    log("warn", "empty_status",
                        keys=sorted(status.keys()))
                else:
                    # Influx write errors are intentionally NOT caught - they
                    # bubble up and kill the process so Docker restarts the
                    # container.
                    write_api.write(bucket=cfg.influx_bucket, record=points)
                    log("info", "poll_ok",
                        channels=channels_written,
                        total_power_w=round(total_power_w, 2),
                        cfg_rev=last_cfg_rev)
                    HEALTH_MARKER.touch()

            elapsed = time.monotonic() - cycle_start
            sleep_for = max(0.0, cfg.poll_interval - elapsed)
            deadline = time.monotonic() + sleep_for
            while not stop_requested and time.monotonic() < deadline:
                time.sleep(min(1.0, deadline - time.monotonic()))
    finally:
        log("info", "shutdown")
        influx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
