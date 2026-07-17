"""AirNow (EPA official AQI) -> InfluxDB poller.

Pulls current observed AQI for a lat/lon from the EPA AirNow API. Unlike the
PurpleAir sensor (particulates only), AirNow aggregates the regulatory
monitors and reports every pollutant the local area measures — PM2.5, PM10,
**ozone**, NO2, CO, SO2 — so it catches Chicago's common non-smoke driver
(summer ground-level ozone) that a PM sensor cannot see.

Companion to the purpleair poller: AirNow = authoritative area AQI across all
pollutants, hourly; PurpleAir = hyperlocal real-time PM. Use AirNow for the
headline ("AQI 93, dominant PM2.5") and PurpleAir for the local PM detail.

Measurement: ``airnow.aqi``
  Tags:   reporting_area
  Fields: overall_aqi (int, max across pollutants), dominant (str),
          category (str), o3_aqi / pm25_aqi / pm10_aqi / no2_aqi / co_aqi /
          so2_aqi (whichever the area reports), obs_age_min (int)

AirNow updates hourly; polling every 30 min is plenty. Free API key required.

Env:
  AIRNOW_API_KEY       (required)
  AIRNOW_LAT           (default 41.6151)
  AIRNOW_LON           (default -88.2018)
  AIRNOW_DISTANCE_MI   (default 50)
  AIRNOW_POLL_INTERVAL (default 1800 s = 30 min)
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
AIRNOW_URL = "https://www.airnowapi.org/aq/observation/latLong/current/"

# AirNow ParameterName -> Influx field key
_PARAM_FIELD = {
    "O3": "o3_aqi", "PM2.5": "pm25_aqi", "PM10": "pm10_aqi",
    "NO2": "no2_aqi", "CO": "co_aqi", "SO2": "so2_aqi",
}


def log(level: str, msg: str, **fields: object) -> None:
    rec: dict[str, Any] = {"ts": datetime.now(timezone.utc).isoformat(), "level": level, "msg": msg}
    rec.update(fields)
    print(json.dumps(rec), flush=True)


@dataclass(frozen=True)
class Config:
    api_key: str
    lat: float
    lon: float
    distance_mi: int
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
            api_key=required("AIRNOW_API_KEY"),
            lat=float(os.environ.get("AIRNOW_LAT", "41.653976")),
            lon=float(os.environ.get("AIRNOW_LON", "-88.211868")),
            distance_mi=int(os.environ.get("AIRNOW_DISTANCE_MI", "50")),
            poll_interval_s=float(os.environ.get("AIRNOW_POLL_INTERVAL", "1800")),
            influx_url=os.environ.get("INFLUXDB_URL", "http://influxdb:8086"),
            influx_token=required("INFLUXDB_TOKEN"),
            influx_org=required("INFLUXDB_ORG"),
            influx_bucket=required("INFLUXDB_BUCKET"),
        )


async def fetch_observations(session: aiohttp.ClientSession, cfg: Config) -> list[dict[str, Any]]:
    params = {
        "format": "application/json",
        "latitude": str(cfg.lat),
        "longitude": str(cfg.lon),
        "distance": str(cfg.distance_mi),
        "API_KEY": cfg.api_key,
    }
    async with session.get(AIRNOW_URL, params=params,
                           timeout=aiohttp.ClientTimeout(total=30)) as r:
        r.raise_for_status()
        return cast(list[dict[str, Any]], await r.json())


def _obs_age_min(obs: list[dict[str, Any]]) -> int:
    """Minutes since the observation hour (AirNow stamps DateObserved + HourObserved
    in the area's LocalTimeZone; treat that hour as the observation instant)."""
    try:
        o = obs[0]
        # LocalTimeZone like "CST"/"EST"; AirNow hours are standard time, no DST.
        tz_off = {"CST": -6, "CDT": -5, "EST": -5, "EDT": -4, "MST": -7, "PST": -8}.get(o.get("LocalTimeZone", ""), -6)
        d = datetime.strptime(f"{o['DateObserved'].strip()} {int(o['HourObserved']):02d}", "%Y-%m-%d %H")
        obs_utc = d.replace(tzinfo=timezone.utc) - _tzdelta(tz_off)
        return max(0, int((datetime.now(timezone.utc) - obs_utc).total_seconds() // 60))
    except Exception:
        return -1


def _tzdelta(hours: int):
    from datetime import timedelta
    return timedelta(hours=hours)


def build_point(obs: list[dict[str, Any]]) -> Point | None:
    if not obs:
        return None
    area = str(obs[0].get("ReportingArea", ""))
    p = Point("airnow.aqi").tag("reporting_area", area)  # type: ignore[no-untyped-call]
    overall = -1
    dominant = ""
    dom_cat = ""
    for o in obs:
        name = o.get("ParameterName", "")
        aqi = o.get("AQI")
        if aqi is None:
            continue
        aqi = int(aqi)
        field = _PARAM_FIELD.get(name)
        if field:
            p = p.field(field, aqi)
        if aqi > overall:
            overall = aqi
            dominant = name
            dom_cat = (o.get("Category") or {}).get("Name", "")
    if overall < 0:
        return None
    p = (p.field("overall_aqi", overall)
          .field("dominant", dominant)
          .field("category", dom_cat)
          .field("obs_age_min", _obs_age_min(obs)))
    return p


async def poll_once(session: aiohttp.ClientSession, write_api: Any, cfg: Config) -> None:
    try:
        obs = await fetch_observations(session, cfg)
    except Exception as exc:
        log("warn", "fetch_failed", error=str(exc), error_type=type(exc).__name__)
        return
    point = build_point(obs)
    if point is None:
        log("warn", "no_observations", count=len(obs))
        return
    write_api.write(bucket=cfg.influx_bucket, record=point)  # not caught -> Docker restarts on Influx error
    log("info", "poll_ok",
        pollutants=[o.get("ParameterName") for o in obs],
        aqis={o.get("ParameterName"): o.get("AQI") for o in obs})
    HEALTH_MARKER.touch()


def main() -> int:
    cfg = Config.from_env()
    log("info", "startup", lat=cfg.lat, lon=cfg.lon, poll_interval_s=cfg.poll_interval_s, bucket=cfg.influx_bucket)

    influx = InfluxDBClient(url=cfg.influx_url, token=cfg.influx_token, org=cfg.influx_org)
    write_api = influx.write_api(write_options=SYNCHRONOUS)

    stop = asyncio.Event()

    def handle_stop(signum: int, _frame: FrameType | None) -> None:
        log("info", "signal_received", signum=signum)
        stop.set()
    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    async def run() -> None:
        async with aiohttp.ClientSession() as session:
            while not stop.is_set():
                try:
                    await poll_once(session, write_api, cfg)
                except Exception as exc:
                    log("error", "poll_unhandled_error", error=str(exc), error_type=type(exc).__name__)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=cfg.poll_interval_s)
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
