"""Honeywell VisionPRO 8000 → InfluxDB poller via Control4 EA-5.

Reads thermostat state every THERMOSTAT_POLL_INTERVAL seconds (default 600 s =
10 min, the TCC rate-limit floor) and writes:

  Measurement `hvac.thermostat`:
    Tags:   thermostat_id (str)
    Fields: indoor_temp_f, humidity_pct, cool_setpoint_f, heat_setpoint_f,
            hvac_mode (str), hvac_state (str), fan_mode (str), hold_mode (str)

  Measurement `hvac.overrides` (only when manual override detected):
    Tags:   thermostat_id, source ("manual_override")
    Fields: expected_cool_setpoint_f, actual_cool_setpoint_f, delta_cool_f,
            expected_heat_setpoint_f, actual_heat_setpoint_f, delta_heat_f,
            last_action_label (str), minutes_since_last_action,
            indoor_temp_f, humidity_pct, hvac_mode (str)

Override detection: compares current setpoints against the most recent
`hvac.actions` row written by the hvac-scheduler. If they differ AND the
last action fired more than OVERRIDE_GRACE_MIN ago (default 5 min, gives
the thermostat time to acknowledge the scheduler's push), an override
event is logged. No dedup in v1 — each poll cycle while overridden writes
a new row, which is fine at 10-min cadence.

Independent of hvac-scheduler: this poller does its own Control4 auth
with its own persisted token at /data/director_token.json. Two services
sharing the same Control4 account is fine — the bearer token is per-token,
not per-process.

Environment variables:
    CONTROL4_EMAIL              Control4 cloud login email
    CONTROL4_PASSWORD           Control4 cloud password
    CONTROL4_CONTROLLER_IP      Local Director IP (default 192.168.1.30)
    CONTROL4_THERMOSTAT_ID      Thermostat item ID (default 3231)
    THERMOSTAT_POLL_INTERVAL    Seconds between polls (default 600)
    OVERRIDE_GRACE_MIN          Minutes after last action before counting
                                a setpoint mismatch as an override (default 5)
    INFLUXDB_URL                Default http://influxdb:8086
    INFLUXDB_TOKEN              Admin or write token
    INFLUXDB_ORG                InfluxDB organization
    INFLUXDB_BUCKET             Target bucket
    DIRECTOR_TOKEN_FILE         Persisted Control4 token (default /data/director_token.json)
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
from pyControl4.account import C4Account
from pyControl4.director import C4Director
from pyControl4.climate import C4Climate

HEALTH_MARKER = Path("/tmp/last_poll_ok")


def log(level: str, msg: str, **fields: Any) -> None:
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "level": level, "msg": msg}
    rec.update(fields)
    print(json.dumps(rec, default=str), flush=True)


@dataclass(frozen=True)
class Config:
    email: str
    password: str
    controller_ip: str
    thermostat_id: int
    poll_interval: float
    override_grace_min: int
    influx_url: str
    influx_token: str
    influx_org: str
    influx_bucket: str
    token_file: Path

    @staticmethod
    def from_env() -> "Config":
        def required(name: str) -> str:
            v = os.environ.get(name)
            if not v:
                log("error", "missing_env", var=name)
                sys.exit(2)
            return v
        return Config(
            email=required("CONTROL4_EMAIL"),
            password=required("CONTROL4_PASSWORD"),
            controller_ip=os.environ.get("CONTROL4_CONTROLLER_IP", "192.168.1.30"),
            thermostat_id=int(os.environ.get("CONTROL4_THERMOSTAT_ID", "3231")),
            poll_interval=float(os.environ.get("THERMOSTAT_POLL_INTERVAL", "600")),
            override_grace_min=int(os.environ.get("OVERRIDE_GRACE_MIN", "5")),
            influx_url=os.environ.get("INFLUXDB_URL", "http://influxdb:8086"),
            influx_token=required("INFLUXDB_TOKEN"),
            influx_org=required("INFLUXDB_ORG"),
            influx_bucket=required("INFLUXDB_BUCKET"),
            token_file=Path(os.environ.get("DIRECTOR_TOKEN_FILE", "/data/director_token.json")),
        )


class C4Client:
    """Same pattern as hvac-scheduler's C4Client — token-cached director access
    with reauth-on-401. Lifted in spirit, kept independent (own token file)."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._account: C4Account | None = None
        self._director: C4Director | None = None
        self._climate: C4Climate | None = None
        self._token: str | None = None
        self._common_name: str | None = None

    def _load_token(self) -> dict | None:
        if not self.cfg.token_file.exists():
            return None
        try:
            return json.loads(self.cfg.token_file.read_text())
        except Exception as exc:
            log("warn", "token_load_failed", error=str(exc))
            return None

    def _save_token(self, token: str, common_name: str) -> None:
        self.cfg.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.cfg.token_file.write_text(json.dumps({
            "token": token,
            "common_name": common_name,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }))
        try:
            os.chmod(self.cfg.token_file, 0o600)
        except Exception:
            pass

    async def _cloud_auth(self) -> None:
        log("info", "cloud_auth_starting", email=self.cfg.email)
        account = C4Account(self.cfg.email, self.cfg.password)
        await account.get_account_bearer_token()
        controllers = await account.get_account_controllers()
        common_name = controllers["controllerCommonName"]
        token_data = await account.get_director_bearer_token(common_name)
        token = token_data["token"]
        self._account = account
        self._token = token
        self._common_name = common_name
        self._save_token(token, common_name)
        log("info", "cloud_auth_ok", common_name=common_name)

    async def ensure_director(self) -> C4Director:
        if self._director and self._token:
            return self._director
        cached = self._load_token()
        if cached:
            self._token = cached["token"]
            self._common_name = cached.get("common_name")
            log("info", "token_loaded_from_disk")
        else:
            await self._cloud_auth()
        self._director = C4Director(self.cfg.controller_ip, self._token)
        self._climate = C4Climate(self._director, self.cfg.thermostat_id)
        return self._director

    async def get_climate(self) -> C4Climate:
        await self.ensure_director()
        assert self._climate is not None
        return self._climate

    async def call_with_reauth(self, coro_fn):
        try:
            return await coro_fn()
        except Exception as exc:
            txt = str(exc).lower()
            if "401" in txt or "unauthorized" in txt or "forbidden" in txt:
                log("warn", "director_token_invalid_reauth", error=str(exc))
                await self._cloud_auth()
                self._director = C4Director(self.cfg.controller_ip, self._token)
                self._climate = C4Climate(self._director, self.cfg.thermostat_id)
                return await coro_fn()
            raise


async def read_snapshot(c4: C4Client) -> dict:
    climate = await c4.get_climate()
    snap: dict = {}
    snap["indoor_temp_f"] = await c4.call_with_reauth(climate.get_current_temperature_f)
    snap["cool_setpoint_f"] = await c4.call_with_reauth(climate.get_cool_setpoint_f)
    snap["heat_setpoint_f"] = await c4.call_with_reauth(climate.get_heat_setpoint_f)
    snap["hvac_mode"] = await c4.call_with_reauth(climate.get_hvac_mode)
    snap["hvac_state"] = await c4.call_with_reauth(climate.get_hvac_state)
    snap["fan_mode"] = await c4.call_with_reauth(climate.get_fan_mode)
    snap["hold_mode"] = await c4.call_with_reauth(climate.get_hold_mode)
    snap["humidity_pct"] = await c4.call_with_reauth(climate.get_humidity)
    return snap


def write_snapshot(write_api, cfg: Config, snap: dict) -> None:
    p = (Point("hvac.thermostat")
         .tag("thermostat_id", str(cfg.thermostat_id))
         .field("indoor_temp_f", float(snap.get("indoor_temp_f") or 0))
         .field("humidity_pct", float(snap.get("humidity_pct") or 0))
         .field("cool_setpoint_f", float(snap.get("cool_setpoint_f") or 0))
         .field("heat_setpoint_f", float(snap.get("heat_setpoint_f") or 0))
         .field("hvac_mode", str(snap.get("hvac_mode") or ""))
         .field("hvac_state", str(snap.get("hvac_state") or ""))
         .field("fan_mode", str(snap.get("fan_mode") or ""))
         .field("hold_mode", str(snap.get("hold_mode") or ""))
         )
    write_api.write(bucket=cfg.influx_bucket, record=p)


def fetch_last_action(query_api, bucket: str) -> dict | None:
    """Return the most recent applied (non-dry-run) hvac.actions row, with
    cool_setpoint_f / heat_setpoint_f / action_label / minutes_since_last_action."""
    flux = f'''
from(bucket: "{bucket}")
  |> range(start: -2d)
  |> filter(fn: (r) => r._measurement == "hvac.actions" and r.dry_run == "false")
  |> filter(fn: (r) => r._field == "cool_setpoint_f" or r._field == "heat_setpoint_f" or r._field == "applied")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
'''
    rows: list[dict] = []
    for table in query_api.query(flux):
        for record in table.records:
            rows.append({**record.values})
    if not rows:
        return None
    row = rows[0]
    # last() returned per-field rows pivoted; flatten any None fields
    action_time = row.get("_time")
    minutes_since = (datetime.now(timezone.utc) - action_time).total_seconds() / 60.0 if action_time else None
    return {
        "cool_setpoint_f": row.get("cool_setpoint_f"),
        "heat_setpoint_f": row.get("heat_setpoint_f"),
        "applied": row.get("applied"),
        "action_label": row.get("action_label", ""),
        "minutes_since_last_action": minutes_since,
        "action_time": action_time,
    }


def classify_override(snap: dict, last: dict, override_grace_min: float) -> dict | None:
    """Decide whether the current thermostat snapshot represents a manual
    override of the last applied scheduler action.

    Returns a summary dict when:
      * Both expected and actual setpoints (heat AND cool) are present, AND
      * It has been at least ``override_grace_min`` minutes since the last
        applied action (avoids flagging the action's own write as an
        override before the read-back settles), AND
      * Either the cool or heat setpoint differs from the action's setpoint
        by 0.5°F or more (sub-half-degree differences are rounding noise).

    Returns None on any condition above failing.
    """
    expected_cool = last.get("cool_setpoint_f")
    expected_heat = last.get("heat_setpoint_f")
    actual_cool = snap.get("cool_setpoint_f")
    actual_heat = snap.get("heat_setpoint_f")
    minutes_since = last.get("minutes_since_last_action")
    if (expected_cool is None or expected_heat is None
            or actual_cool is None or actual_heat is None):
        return None
    if minutes_since is None or minutes_since < override_grace_min:
        return None
    delta_cool = float(actual_cool) - float(expected_cool)
    delta_heat = float(actual_heat) - float(expected_heat)
    if abs(delta_cool) < 0.5 and abs(delta_heat) < 0.5:
        return None  # within rounding; not an override
    return {
        "expected_cool_f": expected_cool,
        "actual_cool_f": actual_cool,
        "delta_cool_f": delta_cool,
        "expected_heat_f": expected_heat,
        "actual_heat_f": actual_heat,
        "delta_heat_f": delta_heat,
        "minutes_since_last_action": minutes_since,
        "last_action_label": last.get("action_label"),
    }


def detect_and_write_override(query_api, write_api, cfg: Config, snap: dict) -> dict | None:
    """If current setpoints differ from last applied action (after grace period),
    write hvac.overrides row and return the override summary. Returns None if
    no override or last action data unavailable."""
    last = fetch_last_action(query_api, cfg.influx_bucket)
    if not last:
        return None
    summary = classify_override(snap, last, cfg.override_grace_min)
    if summary is None:
        return None

    p = (Point("hvac.overrides")
         .tag("thermostat_id", str(cfg.thermostat_id))
         .tag("source", "manual_override")
         .field("expected_cool_setpoint_f", float(summary["expected_cool_f"]))
         .field("actual_cool_setpoint_f", float(summary["actual_cool_f"]))
         .field("delta_cool_f", float(summary["delta_cool_f"]))
         .field("expected_heat_setpoint_f", float(summary["expected_heat_f"]))
         .field("actual_heat_setpoint_f", float(summary["actual_heat_f"]))
         .field("delta_heat_f", float(summary["delta_heat_f"]))
         .field("last_action_label", str(summary["last_action_label"] or ""))
         .field("minutes_since_last_action", float(summary["minutes_since_last_action"]))
         .field("indoor_temp_f", float(snap.get("indoor_temp_f") or 0))
         .field("humidity_pct", float(snap.get("humidity_pct") or 0))
         .field("hvac_mode", str(snap.get("hvac_mode") or ""))
         )
    write_api.write(bucket=cfg.influx_bucket, record=p)
    return summary


async def main_async(cfg: Config) -> int:
    log("info", "startup",
        controller_ip=cfg.controller_ip,
        thermostat_id=cfg.thermostat_id,
        poll_interval_s=cfg.poll_interval,
        override_grace_min=cfg.override_grace_min,
        bucket=cfg.influx_bucket)

    influx = InfluxDBClient(url=cfg.influx_url, token=cfg.influx_token, org=cfg.influx_org)
    query_api = influx.query_api()
    write_api = influx.write_api(write_options=SYNCHRONOUS)
    c4 = C4Client(cfg)

    stop = asyncio.Event()

    def handle_stop(signum, _frame):
        log("info", "signal_received", signum=signum)
        stop.set()
    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    try:
        while not stop.is_set():
            try:
                snap = await read_snapshot(c4)
                write_snapshot(write_api, cfg, snap)
                override = detect_and_write_override(query_api, write_api, cfg, snap)
                log("info", "poll_ok",
                    indoor_temp_f=snap.get("indoor_temp_f"),
                    humidity_pct=snap.get("humidity_pct"),
                    cool_setpoint_f=snap.get("cool_setpoint_f"),
                    heat_setpoint_f=snap.get("heat_setpoint_f"),
                    hvac_mode=snap.get("hvac_mode"),
                    hvac_state=snap.get("hvac_state"),
                    fan_mode=snap.get("fan_mode"),
                    hold_mode=snap.get("hold_mode"),
                    override_detected=bool(override),
                    override=override)
                HEALTH_MARKER.touch()
            except Exception as exc:
                log("error", "poll_failed", error=str(exc), error_type=type(exc).__name__)
            try:
                await asyncio.wait_for(stop.wait(), timeout=cfg.poll_interval)
            except asyncio.TimeoutError:
                pass
    finally:
        log("info", "shutdown")
        influx.close()
    return 0


def main() -> int:
    cfg = Config.from_env()
    return asyncio.run(main_async(cfg))


if __name__ == "__main__":
    sys.exit(main())
