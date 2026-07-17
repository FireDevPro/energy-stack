"""PurpleAir sensor -> InfluxDB poller.

Polls one PurpleAir sensor via the cloud API (v1) and writes a corrected
outdoor air-quality snapshot to InfluxDB. Applies the US EPA correction
(Barkjohn et al. 2021 + the 2022 extreme-wildfire-smoke extension — the same
piecewise the AirNow Fire & Smoke Map uses) to the raw PurpleAir PM2.5, then
computes the US AQI (EPA 2024 PM2.5 breakpoints).

Measurement: ``purpleair.sensor``
  Tags:   sensor_index, name
  Fields: pm25_raw (cf_1), pm25_epa (corrected), pm10, aqi (int),
          aqi_category, humidity_pct, temperature_f, confidence

Notes
  * PurpleAir's onboard temperature reads high and humidity low; the EPA
    correction was derived with the *raw* onboard RH, so we feed it raw.
  * The cloud API is points-billed (~17 pts/read; 100k pts = $1; the free 1M
    lasts ~20 months at 15-min polling). A locally-owned sensor's ``/json``
    endpoint removes the cost entirely — a future swap that only changes the
    fetch (the correction + write below are reusable).

Env:
  PURPLEAIR_API_KEY       (required)  X-API-Key read key
  PURPLEAIR_SENSOR_ID     (default 290986 — White Oak Library, Romeoville)
  PURPLEAIR_POLL_INTERVAL (default 900 s = 15 min)
  INFLUXDB_URL / INFLUXDB_TOKEN / INFLUXDB_ORG / INFLUXDB_BUCKET
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
from types import FrameType
from typing import Any, cast

import aiohttp
from influxdb_client import InfluxDBClient, Point  # type: ignore[attr-defined]  # stubs lack __all__
from influxdb_client.client.write_api import SYNCHRONOUS

HEALTH_MARKER = Path("/tmp/last_poll_ok")
RETRY_BASE_S = 60.0  # backoff floor after a failed poll; doubles up to poll_interval
PA_API = "https://api.purpleair.com/v1/sensors"
# cf_1 is the variant the EPA correction was built on; pm2.5 (atm) + pm10 kept for reference.
FIELDS = "name,humidity,temperature,pm2.5_cf_1,pm2.5,pm10.0,confidence,last_seen"


def log(level: str, msg: str, **fields: object) -> None:
    rec: dict[str, Any] = {"ts": datetime.now(timezone.utc).isoformat(), "level": level, "msg": msg}
    rec.update(fields)
    print(json.dumps(rec), flush=True)


@dataclass(frozen=True)
class Config:
    api_key: str
    sensor_id: int
    poll_interval_s: float
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
            api_key=required("PURPLEAIR_API_KEY"),
            sensor_id=int(os.environ.get("PURPLEAIR_SENSOR_ID", "290986")),
            poll_interval_s=float(os.environ.get("PURPLEAIR_POLL_INTERVAL", "900")),
            influx_url=os.environ.get("INFLUXDB_URL", "http://influxdb:8086"),
            influx_token=required("INFLUXDB_TOKEN"),
            influx_org=required("INFLUXDB_ORG"),
            influx_bucket=required("INFLUXDB_BUCKET"),
        )


# ---- US EPA correction + AQI ------------------------------------------------


def epa_correct(pa: float, rh: float) -> float:
    """US EPA / AirNow Fire & Smoke Map correction for PurpleAir PM2.5.

    Barkjohn et al. 2021 + the 2022 extreme-wildfire-smoke extension.
    ``pa`` = raw cf_1 PM2.5 (ug/m3); ``rh`` = onboard relative humidity (%).
    """
    if pa <= 0:
        return 0.0
    if pa < 30:
        return 0.524 * pa - 0.0862 * rh + 5.75
    if pa < 50:
        f = pa / 20 - 1.5
        return (0.786 * f + 0.524 * (1 - f)) * pa - 0.0862 * rh + 5.75
    if pa < 210:
        return 0.786 * pa - 0.0862 * rh + 5.75
    if pa < 260:
        f = pa / 50 - 4.2
        return ((0.69 * f + 0.786 * (1 - f)) * pa
                - 0.0862 * rh * (1 - f)
                + 2.966 * f
                + 5.75 * (1 - f)
                + 8.84e-4 * pa * pa * f)
    return 2.966 + 0.69 * pa + 8.84e-4 * pa * pa


# EPA 2024 PM2.5 -> AQI breakpoints: (BP_lo, BP_hi, I_lo, I_hi, category)
_AQI_BANDS = [
    (0.0, 9.0, 0, 50, "Good"),
    (9.1, 35.4, 51, 100, "Moderate"),
    (35.5, 55.4, 101, 150, "Unhealthy for sensitive"),
    (55.5, 125.4, 151, 200, "Unhealthy"),
    (125.5, 225.4, 201, 300, "Very unhealthy"),
    (225.5, 325.4, 301, 500, "Hazardous"),
]


def pm_to_aqi(pm: float) -> tuple[int, str]:
    """US AQI + category from a PM2.5 concentration (already corrected)."""
    c = round(max(pm, 0.0), 1)
    for bp_lo, bp_hi, i_lo, i_hi, cat in _AQI_BANDS:
        if c <= bp_hi:
            return round((i_hi - i_lo) / (bp_hi - bp_lo) * (c - bp_lo) + i_lo), cat
    return 500, "Hazardous"


# ---- fetch + point build ----------------------------------------------------


async def fetch_sensor(session: aiohttp.ClientSession, cfg: Config) -> dict[str, Any]:
    url = f"{PA_API}/{cfg.sensor_id}?fields={FIELDS}"
    async with session.get(url, headers={"X-API-Key": cfg.api_key},
                           timeout=aiohttp.ClientTimeout(total=30)) as r:
        r.raise_for_status()
        body = cast(dict[str, Any], await r.json())
    return cast(dict[str, Any], body["sensor"])


def build_point(s: dict[str, Any]) -> Point:
    rh = float(s.get("humidity") or 0)
    pm_raw = s.get("pm2.5_cf_1")
    if pm_raw is None:  # cf_1 absent -> fall back to the plain (atm) field
        pm_raw = s.get("pm2.5")
    pm_raw = float(pm_raw)
    pm_epa = epa_correct(pm_raw, rh)
    aqi, cat = pm_to_aqi(pm_epa)
    p = (Point("purpleair.sensor")  # type: ignore[no-untyped-call]  # influxdb_client stubs not annotated
         .tag("sensor_index", str(s.get("sensor_index", "")))
         .tag("name", str(s.get("name", "")))
         .field("pm25_raw", pm_raw)
         .field("pm25_epa", round(pm_epa, 1))
         .field("aqi", aqi)
         .field("aqi_category", cat)
         .field("humidity_pct", rh)
         .field("temperature_f", float(s.get("temperature") or 0)))
    if s.get("pm10.0") is not None:
        p = p.field("pm10", float(s["pm10.0"]))
    if s.get("confidence") is not None:
        p = p.field("confidence", int(s["confidence"]))
    return p


async def poll_once(session: aiohttp.ClientSession, write_api: Any, cfg: Config) -> bool:
    """Returns True on a successful write, False on a fetch failure (so the
    caller can retry with backoff instead of waiting the full interval)."""
    try:
        sensor = await fetch_sensor(session, cfg)
    except Exception as exc:
        log("warn", "fetch_failed", error=str(exc), error_type=type(exc).__name__)
        return False
    point = build_point(sensor)
    write_api.write(bucket=cfg.influx_bucket, record=point)  # not caught -> Docker restarts on Influx error
    log("info", "poll_ok",
        name=sensor.get("name"),
        pm25_raw=sensor.get("pm2.5_cf_1"),
        aqi=point._fields.get("aqi") if hasattr(point, "_fields") else None)  # type: ignore[attr-defined]
    HEALTH_MARKER.touch()
    return True


def main() -> int:
    cfg = Config.from_env()
    log("info", "startup", sensor_id=cfg.sensor_id, poll_interval_s=cfg.poll_interval_s, bucket=cfg.influx_bucket)

    influx = InfluxDBClient(url=cfg.influx_url, token=cfg.influx_token, org=cfg.influx_org)
    write_api = influx.write_api(write_options=SYNCHRONOUS)

    stop = asyncio.Event()

    def handle_stop(signum: int, _frame: FrameType | None) -> None:
        log("info", "signal_received", signum=signum)
        stop.set()
    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    async def run() -> None:
        fail_streak = 0
        async with aiohttp.ClientSession() as session:
            while not stop.is_set():
                ok = False
                try:
                    ok = await poll_once(session, write_api, cfg)
                except Exception as exc:
                    log("error", "poll_unhandled_error", error=str(exc), error_type=type(exc).__name__)
                if ok:
                    fail_streak = 0
                    delay = cfg.poll_interval_s
                else:
                    fail_streak += 1
                    delay = min(RETRY_BASE_S * 2 ** (fail_streak - 1), cfg.poll_interval_s)
                    log("info", "retry_backoff", fail_streak=fail_streak, delay_s=delay)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass

    try:
        asyncio.run(run())
    finally:
        log("info", "shutdown")
        influx.close()  # type: ignore[no-untyped-call]  # influxdb_client stubs not annotated
    return 0


if __name__ == "__main__":
    sys.exit(main())
