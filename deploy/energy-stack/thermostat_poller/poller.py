"""CTK04 Thermostat → InfluxDB poller via a ThermostatClient device seam.

The device client is the direct Total Connect Comfort (aiosomecomfort)
``TCCClient`` (see tcc_client.py). When TCC creds are absent the poller
keeps the inert ``StubThermostatClient`` (fail-loud on a read) so a
credential-less deploy stays in the known not-yet-wired state.

Reads thermostat state every THERMOSTAT_POLL_INTERVAL seconds (default 600 s =
10 min, the TCC rate-limit floor) and writes:

  Measurement `hvac.thermostat`:
    Tags:   thermostat_id (str)  -- carries the TCC device id (cfg.device_id)
    Fields: indoor_temp_f, indoor_temp_f_hires, humidity_pct, cool_setpoint_f,
            heat_setpoint_f, hvac_mode (str), hvac_state (str), fan_mode (str),
            hold_mode (str)

    indoor_temp_f_hires == indoor_temp_f under TCC: TCC exposes only the single
    whole-display-unit DispTemperature; there is no separate finer value. (The
    Control4 "hi-res" came from the Director's separate TEMPERATURE_C variable,
    which no longer exists.) Non-load-bearing; Ecowitt is the canonical finer
    indoor source. The field is retained for schema continuity.

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

Independent of hvac-scheduler: this poller owns its own TCC device client
(aiosomecomfort manages its own session; no persisted token file).

Environment variables:
    TCC_DEVICE_ID               Honeywell TCC device id (default 4750378).
    TCC_USERNAME                TCC account username. When unset (with
                                TCC_PASSWORD) the poller stays inert (Stub).
    TCC_PASSWORD                TCC account password.
    THERMOSTAT_POLL_INTERVAL    Seconds between polls (default 600)
    OVERRIDE_GRACE_MIN          Minutes after last action before counting
                                a setpoint mismatch as an override (default 5)
    INFLUXDB_URL                Default http://influxdb:8086
    INFLUXDB_TOKEN              Admin or write token
    INFLUXDB_ORG                InfluxDB organization
    INFLUXDB_BUCKET             Target bucket
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
from typing import Any, Awaitable, Callable, Protocol

from influxdb_client import InfluxDBClient, Point  # type: ignore[attr-defined]  # stubs lack __all__
from influxdb_client.client.write_api import SYNCHRONOUS

from .tcc_client import TCCClient

HEALTH_MARKER = Path("/tmp/last_poll_ok")


def log(level: str, msg: str, **fields: Any) -> None:
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "level": level, "msg": msg}
    rec.update(fields)
    print(json.dumps(rec, default=str), flush=True)


@dataclass(frozen=True)
class Config:
    # Honeywell Total Connect Comfort device id (the aiosomecomfort DeviceID).
    device_id: int
    # TCC credentials. Optional: when both are absent the poller keeps the
    # inert StubThermostatClient (fail-loud on a device call) rather than
    # constructing a TCCClient with no creds. Real values land via SOPS/.env
    # at go-active.
    email: str | None
    password: str | None
    poll_interval: float
    override_grace_min: int
    influx_url: str
    influx_token: str
    influx_org: str
    influx_bucket: str

    @staticmethod
    def from_env() -> "Config":
        def required(name: str) -> str:
            v = os.environ.get(name)
            if not v:
                log("error", "missing_env", var=name)
                sys.exit(2)
            return v
        return Config(
            device_id=int(os.environ.get("TCC_DEVICE_ID", "4750378")),
            email=os.environ.get("TCC_USERNAME"),
            password=os.environ.get("TCC_PASSWORD"),
            poll_interval=float(os.environ.get("THERMOSTAT_POLL_INTERVAL", "600")),
            override_grace_min=int(os.environ.get("OVERRIDE_GRACE_MIN", "5")),
            influx_url=os.environ.get("INFLUXDB_URL", "http://influxdb:8086"),
            influx_token=required("INFLUXDB_TOKEN"),
            influx_org=required("INFLUXDB_ORG"),
            influx_bucket=required("INFLUXDB_BUCKET"),
        )


# ---- Thermostat device seam -----------------------------------------------
#
# The concrete device I/O (the prior Control4 Director client) is
# demolished. ``ThermostatClient`` is the thin read-only interface the
# poller talks to; ``StubThermostatClient`` is the not-yet-wired
# implementation that raises ``NotImplementedError`` for every method.
# The Control4 -> TCC swap fills this seam with an aiosomecomfort-backed
# client at rebuild / go-active. ``read_snapshot`` is typed to the
# interface and its body is unchanged.


class ClimateDevice(Protocol):
    """The per-thermostat read surface the poller invokes via
    ``ThermostatClient.call_with_reauth``. Exactly the methods
    ``read_snapshot`` calls — nothing more. All methods are async."""

    async def get_current_temperature_f(self) -> float: ...
    async def get_cool_setpoint_f(self) -> float: ...
    async def get_heat_setpoint_f(self) -> float: ...
    async def get_hvac_mode(self) -> str: ...
    async def get_hvac_state(self) -> str: ...
    async def get_fan_mode(self) -> str: ...
    async def get_hold_mode(self) -> str: ...
    async def get_humidity(self) -> float: ...


class ThermostatClient(Protocol):
    """The device-client seam the poller talks to. Concrete impls
    (Control4 -> TCC swap) own auth/token lifecycle; the poller only
    needs to fetch the climate device and run a read with reauth-on-401."""

    async def get_climate(self) -> ClimateDevice: ...

    async def call_with_reauth(
        self, coro_fn: Callable[[], Awaitable[Any]]
    ) -> Any: ...


_SWAP_PENDING = "Control4->TCC swap pending: no device client wired"


class StubThermostatClient:
    """Not-yet-wired ``ThermostatClient``. Every method raises
    ``NotImplementedError`` — this is the seam the aiosomecomfort/TCC read
    client fills at rebuild / go-active. The poll loop still constructs
    and threads this client; the per-cycle try/except in ``main_async``
    logs ``poll_failed`` and survives until the real client is wired."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    async def get_climate(self) -> ClimateDevice:
        raise NotImplementedError(_SWAP_PENDING)

    async def call_with_reauth(
        self, coro_fn: Callable[[], Awaitable[Any]]
    ) -> Any:
        raise NotImplementedError(_SWAP_PENDING)


async def read_snapshot(c4: ThermostatClient) -> dict[str, Any]:
    climate = await c4.get_climate()
    snap: dict[str, Any] = {}
    snap["indoor_temp_f"] = await c4.call_with_reauth(climate.get_current_temperature_f)
    # TCC exposes only the single whole-display-unit DispTemperature; there is
    # no separate finer value, so indoor_temp_f_hires collapses to the whole
    # reading in write_snapshot. (The Control4 "hi-res" came from the
    # Director's separate TEMPERATURE_C variable, which no longer exists.
    # Ecowitt is the canonical finer indoor source.)
    snap["cool_setpoint_f"] = await c4.call_with_reauth(climate.get_cool_setpoint_f)
    snap["heat_setpoint_f"] = await c4.call_with_reauth(climate.get_heat_setpoint_f)
    snap["hvac_mode"] = await c4.call_with_reauth(climate.get_hvac_mode)
    snap["hvac_state"] = await c4.call_with_reauth(climate.get_hvac_state)
    snap["fan_mode"] = await c4.call_with_reauth(climate.get_fan_mode)
    snap["hold_mode"] = await c4.call_with_reauth(climate.get_hold_mode)
    snap["humidity_pct"] = await c4.call_with_reauth(climate.get_humidity)
    return snap


def write_snapshot(write_api: Any, cfg: Config, snap: dict[str, Any]) -> None:
    # `p: Any` lets the Point()/.tag()/.field() chain (untyped in
    # influxdb_client stubs) flow through without per-call ignores.
    # TCC has no finer-than-whole value: indoor_temp_f_hires == indoor_temp_f
    # (the one deliberate behavior change; non-load-bearing, Ecowitt canonical).
    indoor_f_hires = float(snap.get("indoor_temp_f") or 0)
    p: Any = (
        Point("hvac.thermostat")  # type: ignore[no-untyped-call]
        .tag("thermostat_id", str(cfg.device_id))
        .field("indoor_temp_f", float(snap.get("indoor_temp_f") or 0))
        .field("indoor_temp_f_hires", indoor_f_hires)
        .field("humidity_pct", float(snap.get("humidity_pct") or 0))
        .field("cool_setpoint_f", float(snap.get("cool_setpoint_f") or 0))
        .field("heat_setpoint_f", float(snap.get("heat_setpoint_f") or 0))
        .field("hvac_mode", str(snap.get("hvac_mode") or ""))
        .field("hvac_state", str(snap.get("hvac_state") or ""))
        .field("fan_mode", str(snap.get("fan_mode") or ""))
        .field("hold_mode", str(snap.get("hold_mode") or ""))
    )
    write_api.write(bucket=cfg.influx_bucket, record=p)


def fetch_last_action(query_api: Any, bucket: str) -> dict[str, Any] | None:
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
    rows: list[dict[str, Any]] = []
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


def classify_override(snap: dict[str, Any], last: dict[str, Any], override_grace_min: float) -> dict[str, Any] | None:
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


def detect_and_write_override(query_api: Any, write_api: Any, cfg: Config, snap: dict[str, Any]) -> dict[str, Any] | None:
    """If current setpoints differ from last applied action (after grace period),
    write hvac.overrides row and return the override summary. Returns None if
    no override or last action data unavailable."""
    last = fetch_last_action(query_api, cfg.influx_bucket)
    if not last:
        return None
    summary = classify_override(snap, last, cfg.override_grace_min)
    if summary is None:
        return None

    # `p: Any` lets the Point()/.tag()/.field() chain (untyped in
    # influxdb_client stubs) flow through without per-call ignores.
    p: Any = (
        Point("hvac.overrides")  # type: ignore[no-untyped-call]
        .tag("thermostat_id", str(cfg.device_id))
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
        device_id=cfg.device_id,
        poll_interval_s=cfg.poll_interval,
        override_grace_min=cfg.override_grace_min,
        bucket=cfg.influx_bucket)

    influx = InfluxDBClient(url=cfg.influx_url, token=cfg.influx_token, org=cfg.influx_org)
    query_api = influx.query_api()
    write_api = influx.write_api(write_options=SYNCHRONOUS)
    # Construct the real TCC client when creds are present; otherwise keep the
    # inert StubThermostatClient (fail-loud on a device call) so a credential-
    # less deploy stays in the known not-yet-wired state rather than crashing.
    c4: ThermostatClient
    if cfg.email and cfg.password:
        c4 = TCCClient(cfg.email, cfg.password, cfg.device_id)
    else:
        log("warn", "tcc_creds_absent_using_stub",
            message="TCC_USERNAME/TCC_PASSWORD unset; thermostat reads inert")
        c4 = StubThermostatClient(cfg)

    stop = asyncio.Event()

    def handle_stop(signum: int, _frame: Any) -> None:
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
        influx.close()  # type: ignore[no-untyped-call]  # stubs lack annotation on InfluxDBClient.close
    return 0


def main() -> int:
    cfg = Config.from_env()
    return asyncio.run(main_async(cfg))


if __name__ == "__main__":
    sys.exit(main())
