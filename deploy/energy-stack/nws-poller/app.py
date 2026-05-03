"""NWS forecast -> InfluxDB poller.

Pulls hourly forecast and active alerts from api.weather.gov for a fixed
lat/lon (default Plainfield IL) and writes daily snapshots to InfluxDB.
The HVAC scheduler reads the most recent snapshot to decide day-type.

Measurements:
  * `nws.forecast` -- one point per period per poll, tagged for_period.
    Tags: for_period ("today"|"tomorrow"|"day2")
    Fields: high_f, low_f, max_dewpoint_f, max_wind_mph,
            max_precip_prob_pct, is_heat_advisory (0|1),
            alert_summary (string), period_date (string YYYY-MM-DD)
  * `nws.alerts` -- one point per active alert, tagged event/severity.
    Fields: active=1, expires_unix, headline (truncated)

NWS API quirks:
  * Requires User-Agent header (no key needed).
  * Gridpoint URL is cached; only re-fetched on 404.
  * Hourly forecast covers ~156 hours; we slice into local-time days.
  * dewpoint is reported in degC; we convert to F.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

import aiohttp

HEALTH_MARKER = Path("/tmp/last_poll_ok")
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS


def log(level: str, msg: str, **fields: object) -> None:
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "level": level, "msg": msg}
    rec.update(fields)
    print(json.dumps(rec), flush=True)


@dataclass(frozen=True)
class Config:
    lat: float
    lon: float
    user_agent: str
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
            lat=float(os.environ.get("NWS_LAT", "41.6151")),
            lon=float(os.environ.get("NWS_LON", "-88.2018")),
            user_agent=os.environ.get(
                "NWS_USER_AGENT",
                "energy-stack/1.0 (https://github.com/depaola; contact@local)"
            ),
            poll_interval_s=float(os.environ.get("NWS_POLL_INTERVAL", "1800")),  # 30 min
            influx_url=os.environ.get("INFLUXDB_URL", "http://influxdb:8086"),
            influx_token=required("INFLUXDB_TOKEN"),
            influx_org=required("INFLUXDB_ORG"),
            influx_bucket=required("INFLUXDB_BUCKET"),
        )


def c_to_f(c):
    return None if c is None else c * 9 / 5 + 32


class NWSClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._gridpoint_url: str | None = None
        self._gridpoint_meta: dict = {}

    async def _get(self, session: aiohttp.ClientSession, url: str) -> dict:
        async with session.get(url, headers={"User-Agent": self.cfg.user_agent},
                                timeout=aiohttp.ClientTimeout(total=30)) as r:
            r.raise_for_status()
            return await r.json()

    async def resolve_gridpoint(self, session: aiohttp.ClientSession) -> str:
        if self._gridpoint_url:
            return self._gridpoint_url
        url = f"https://api.weather.gov/points/{self.cfg.lat},{self.cfg.lon}"
        data = await self._get(session, url)
        props = data["properties"]
        self._gridpoint_url = props["forecastHourly"]
        self._gridpoint_meta = {
            "office": props.get("cwa"),
            "gridX": props.get("gridX"),
            "gridY": props.get("gridY"),
            "city": (props.get("relativeLocation") or {}).get("properties", {}).get("city"),
            "state": (props.get("relativeLocation") or {}).get("properties", {}).get("state"),
        }
        log("info", "gridpoint_resolved", url=self._gridpoint_url, **self._gridpoint_meta)
        return self._gridpoint_url

    async def fetch_hourly_forecast(self, session: aiohttp.ClientSession) -> list[dict]:
        url = await self.resolve_gridpoint(session)
        data = await self._get(session, url)
        return data["properties"]["periods"]

    async def fetch_alerts(self, session: aiohttp.ClientSession) -> list[dict]:
        url = f"https://api.weather.gov/alerts/active?point={self.cfg.lat},{self.cfg.lon}"
        data = await self._get(session, url)
        return data.get("features", [])


def parse_iso(s: str) -> datetime:
    """NWS returns ISO8601 with offset; Python 3.11+ handles directly."""
    return datetime.fromisoformat(s)


def is_heat_related(event_text: str) -> bool:
    t = (event_text or "").lower()
    return any(k in t for k in ("heat advisory", "excessive heat", "heat warning"))


def aggregate_period(periods: list[dict], target_date) -> dict:
    """Roll up hourly periods that fall on `target_date` (local-time)."""
    matching = []
    for p in periods:
        try:
            dt_local = parse_iso(p["startTime"]).astimezone()
        except Exception:
            continue
        if dt_local.date() == target_date:
            matching.append(p)
    if not matching:
        return {}
    temps_f = [p.get("temperature") for p in matching if p.get("temperature") is not None]
    dewpoints_c = [
        (p.get("dewpoint") or {}).get("value")
        for p in matching if (p.get("dewpoint") or {}).get("value") is not None
    ]
    pops = [
        (p.get("probabilityOfPrecipitation") or {}).get("value") or 0
        for p in matching
    ]
    # windSpeed is "5 to 10 mph" or "10 mph" string -- extract max int
    wind_speeds = []
    for p in matching:
        ws = p.get("windSpeed", "")
        nums = [int(s) for s in ws.replace("mph", "").replace("to", " ").split() if s.isdigit()]
        if nums:
            wind_speeds.append(max(nums))
    return {
        "high_f": float(max(temps_f)) if temps_f else None,
        "low_f": float(min(temps_f)) if temps_f else None,
        "max_dewpoint_f": float(c_to_f(max(dewpoints_c))) if dewpoints_c else None,
        "max_wind_mph": float(max(wind_speeds)) if wind_speeds else None,
        "max_precip_prob_pct": float(max(pops)) if pops else 0.0,
        "hours_covered": len(matching),
    }


def build_forecast_points(periods: list[dict], alerts: list[dict]) -> list[Point]:
    """Build today / tomorrow / day2 snapshot points with alert overlay."""
    now_local = datetime.now().astimezone()
    today = now_local.date()
    days = {
        "today":    today,
        "tomorrow": today + timedelta(days=1),
        "day2":     today + timedelta(days=2),
    }

    # Active heat advisory flag (any heat-related alert active right now is enough
    # for the scheduler's purposes -- we don't time-slice alerts to specific days)
    heat_active = any(
        is_heat_related((a.get("properties") or {}).get("event", ""))
        for a in alerts
    )
    alert_summary = "; ".join(
        sorted({(a.get("properties") or {}).get("event", "") for a in alerts if a.get("properties")})
    )[:240]

    points = []
    for period_label, target_date in days.items():
        agg = aggregate_period(periods, target_date)
        p = (Point("nws.forecast")
             .tag("for_period", period_label)
             .field("period_date", target_date.isoformat())
             .field("is_heat_advisory", 1 if heat_active else 0)
             .field("alert_summary", alert_summary or ""))
        for k, v in agg.items():
            if v is None:
                continue
            p = p.field(k, v if isinstance(v, (int, float)) else float(v))
        points.append(p)
    return points


def build_alert_points(alerts: list[dict]) -> list[Point]:
    out = []
    for a in alerts:
        props = a.get("properties") or {}
        event = props.get("event", "Unknown")
        severity = props.get("severity", "Unknown")
        headline = (props.get("headline") or "")[:200]
        expires = props.get("expires") or props.get("ends") or ""
        try:
            expires_unix = int(parse_iso(expires).timestamp()) if expires else 0
        except Exception:
            expires_unix = 0
        out.append(
            Point("nws.alerts")
            .tag("event", event)
            .tag("severity", severity)
            .field("active", 1)
            .field("expires_unix", expires_unix)
            .field("headline", headline)
        )
    return out


async def poll_once(client: NWSClient, write_api, cfg: Config) -> None:
    async with aiohttp.ClientSession() as session:
        try:
            periods = await client.fetch_hourly_forecast(session)
        except Exception as exc:
            log("warn", "forecast_fetch_failed", error=str(exc), error_type=type(exc).__name__)
            return
        try:
            alerts = await client.fetch_alerts(session)
        except Exception as exc:
            log("warn", "alerts_fetch_failed", error=str(exc), error_type=type(exc).__name__)
            alerts = []

    forecast_points = build_forecast_points(periods, alerts)
    alert_points = build_alert_points(alerts)

    # Influx write errors NOT caught -- bubble up, Docker restarts container
    if forecast_points:
        write_api.write(bucket=cfg.influx_bucket, record=forecast_points)
    if alert_points:
        write_api.write(bucket=cfg.influx_bucket, record=alert_points)

    today_pt = next((p for p in forecast_points if "for_period=today" in str(p)), None)
    log("info", "poll_ok",
        hours_covered=len(periods),
        forecast_points=len(forecast_points),
        active_alerts=len(alerts),
        alert_events=sorted({(a.get("properties") or {}).get("event") for a in alerts}))
    HEALTH_MARKER.touch()


def main() -> int:
    cfg = Config.from_env()
    log("info", "startup", lat=cfg.lat, lon=cfg.lon,
        poll_interval_s=cfg.poll_interval_s, bucket=cfg.influx_bucket)

    influx = InfluxDBClient(url=cfg.influx_url, token=cfg.influx_token, org=cfg.influx_org)
    write_api = influx.write_api(write_options=SYNCHRONOUS)
    client = NWSClient(cfg)

    stop = asyncio.Event()

    def handle_stop(signum, _frame):
        log("info", "signal_received", signum=signum)
        stop.set()
    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    async def run():
        while not stop.is_set():
            try:
                await poll_once(client, write_api, cfg)
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
        influx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
