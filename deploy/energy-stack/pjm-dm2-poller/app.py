"""PJM Data Miner 2 -> InfluxDB poller.

Phase 1 scope (this PR): two highest-value feeds for the residential
HVAC controls field study (docs/EXPERIMENT_DESIGN.md). Polls at a fixed
hourly cadence; each fetcher knows which local-time hours it should
fire on and silently skips the rest.

Feeds polled:
  * `da_hrl_lmps` for ComEd zonal aggregate (pnode_id=33092371) at 17:00 CT.
    Day-ahead market clears ~16:00 CT; 17:00 ensures tomorrow's prices are
    posted. Writes one Influx point per hour (24/day) to `pjm.lmp_da_hourly`.
  * `load_frcstd_7_day` for forecast_area=COMED at 06:00 + 13:00 CT.
    Twice daily picks up the morning revision and the late-morning update.
    Writes one Influx point per (forecast_target_hour, evaluated_at) pair to
    `pjm.load_forecast`.

Schema and design rationale: docs/PJM_DM2_INTEGRATION.md.
Feed catalog with column refs and ComEd-specific constants: docs/PJM_DM2_FEEDS.md.

Non-Member API tier:
  * 6 calls/min ceiling. Phase 1 polls at most 3 calls/day; trivially under.
  * 50,000-row max per call. ComEd zonal LMP for one day = 24 rows; 7-day
    load forecast = ~168 forecast points per evaluated_at. Both well under.

Auth: header `Ocp-Apim-Subscription-Key: $PJM_DM2_API_KEY`.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

HEALTH_MARKER = Path("/tmp/last_poll_ok")
PJM_API_BASE = "https://api.pjm.com/api/v1"

# ComEd zonal aggregate pnode (drives ComEd retail hourly pricing).
# Confirmed via /metadata + /search probes; see docs/PJM_DM2_FEEDS.md §"Filterable columns".
COMED_PNODE_ID = 33092371
COMED_FORECAST_AREA = "COMED"

# Per-feed schedule: which local hours (in cfg.tz) the feed should fire on.
# Hourly wake-loop checks these and dispatches matching feeds.
FEED_SCHEDULE = {
    "da_hrl_lmps": (17,),
    "load_frcstd_7_day": (6, 13),
}


def log(level: str, msg: str, **fields: object) -> None:
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "level": level, "msg": msg}
    rec.update(fields)
    print(json.dumps(rec), flush=True)


@dataclass(frozen=True)
class Config:
    api_key: str
    poll_interval_s: float
    influx_url: str
    influx_token: str
    influx_org: str
    influx_bucket: str
    tz: ZoneInfo

    @staticmethod
    def from_env() -> "Config":
        api_key = os.environ.get("PJM_DM2_API_KEY", "").strip()
        if not api_key:
            raise SystemExit("PJM_DM2_API_KEY not set")
        return Config(
            api_key=api_key,
            poll_interval_s=float(os.environ.get("PJM_DM2_POLL_INTERVAL", "3600")),
            influx_url=os.environ.get("INFLUX_URL", "http://influxdb:8086"),
            influx_token=os.environ["INFLUXDB_INIT_ADMIN_TOKEN"],
            influx_org=os.environ.get("INFLUXDB_INIT_ORG", "depaola-home"),
            influx_bucket=os.environ.get("INFLUXDB_INIT_BUCKET", "energy"),
            tz=ZoneInfo(os.environ.get("PJM_DM2_TZ", "America/Chicago")),
        )


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


class PJMClient:
    """Thin async wrapper around the PJM DM2 search endpoints. Adds the
    required `Ocp-Apim-Subscription-Key` header and parses the JSON
    `{items, totalRows}` envelope."""

    def __init__(self, cfg: Config, session: aiohttp.ClientSession | None = None) -> None:
        self._cfg = cfg
        self._session = session
        self._owns_session = session is None

    async def __aenter__(self) -> "PJMClient":
        if self._session is None:
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()

    async def fetch(self, feed: str, params: dict[str, str | int]) -> list[dict]:
        assert self._session is not None
        url = f"{PJM_API_BASE}/{feed}"
        headers = {"Ocp-Apim-Subscription-Key": self._cfg.api_key}
        async with self._session.get(url, params=params, headers=headers) as resp:
            text = await resp.text()
            if resp.status != 200:
                # Log only the error code/message, never the URL with key.
                raise RuntimeError(
                    f"PJM {feed} HTTP {resp.status}: {text[:200]}"
                )
            payload = json.loads(text)
        items = payload.get("items") or []
        log("debug", "pjm_fetch_ok", feed=feed,
            total_rows=payload.get("totalRows"), returned=len(items))
        return items


# ---------------------------------------------------------------------------
# Feed-specific fetchers (pure-logic core that builds Influx points)
# ---------------------------------------------------------------------------


def _parse_ept(s: str, tz: ZoneInfo) -> datetime:
    """Parse a PJM EPT timestamp like '2026-05-05T13:00:00' as tz-local."""
    return datetime.fromisoformat(s).replace(tzinfo=tz)


def build_da_lmp_points(items: list[dict], tz: ZoneInfo) -> list[Point]:
    """Convert da_hrl_lmps items (ComEd zonal aggregate) to Influx points
    on measurement `pjm.lmp_da_hourly`."""
    out: list[Point] = []
    for it in items:
        ts_local = _parse_ept(it["datetime_beginning_ept"], tz)
        ts_utc = ts_local.astimezone(timezone.utc)
        p = (
            Point("pjm.lmp_da_hourly")
            .tag("pnode_id", str(it.get("pnode_id", "")))
            .tag("pnode_name", it.get("pnode_name", "") or "")
            .tag("zone", it.get("zone") or it.get("pnode_name") or "")
            .field("total_lmp_da", float(it.get("total_lmp_da") or 0.0))
            .field("system_energy_price_da", float(it.get("system_energy_price_da") or 0.0))
            .field("congestion_price_da", float(it.get("congestion_price_da") or 0.0))
            .field("marginal_loss_price_da", float(it.get("marginal_loss_price_da") or 0.0))
            .time(ts_utc)
        )
        out.append(p)
    return out


def build_load_forecast_points(items: list[dict], tz: ZoneInfo) -> list[Point]:
    """Convert load_frcstd_7_day items to Influx points on `pjm.load_forecast`.
    Each forecast point is (target_hour, evaluated_at) — a single target hour
    can have multiple forecasts as PJM revises through the day, so the
    `evaluated_at_iso` tag keeps revisions distinct."""
    out: list[Point] = []
    for it in items:
        target_local = _parse_ept(it["forecast_datetime_beginning_ept"], tz)
        target_utc = target_local.astimezone(timezone.utc)
        evaluated_local = _parse_ept(it["evaluated_at_datetime_ept"], tz)
        evaluated_utc = evaluated_local.astimezone(timezone.utc)
        horizon_hours = int((target_utc - evaluated_utc).total_seconds() // 3600)
        p = (
            Point("pjm.load_forecast")
            .tag("forecast_area", it.get("forecast_area", "") or "")
            .tag("evaluated_at_iso", evaluated_utc.isoformat())
            .field("forecast_load_mw", float(it.get("forecast_load_mw") or 0.0))
            .field("horizon_hours", horizon_hours)
            .time(target_utc)
        )
        out.append(p)
    return out


# ---------------------------------------------------------------------------
# Per-feed orchestration (HTTP -> points -> Influx)
# ---------------------------------------------------------------------------


async def fetch_da_lmp_for_today(client: PJMClient, cfg: Config, today_local: datetime) -> list[Point]:
    """Pull DA LMPs for ComEd zone for `today_local`'s date. PJM publishes
    24 hourly prices per pnode per day."""
    date_str = today_local.strftime("%Y-%m-%dT00:00:00.0")
    items = await client.fetch(
        "da_hrl_lmps",
        {
            "pnode_id": COMED_PNODE_ID,
            "datetime_beginning_ept": date_str,
            "rowCount": 50,
            "startRow": 1,
        },
    )
    return build_da_lmp_points(items, cfg.tz)


async def fetch_load_forecast(client: PJMClient, cfg: Config, now_local: datetime) -> list[Point]:
    """Pull the 7-day load forecast for ComEd, restricted to `forecast_area=COMED`.
    Server-side filter keeps response small (~168 hours)."""
    items = await client.fetch(
        "load_frcstd_7_day",
        {
            "forecast_area": COMED_FORECAST_AREA,
            "rowCount": 500,
            "startRow": 1,
        },
    )
    return build_load_forecast_points(items, cfg.tz)


async def poll_once(client: PJMClient, write_api, cfg: Config) -> None:
    """One pass through the feed schedule. Called hourly by the main loop;
    each fetcher fires only when the current local hour matches its schedule."""
    now_local = datetime.now(cfg.tz)
    hour = now_local.hour
    fired: list[str] = []

    if hour in FEED_SCHEDULE["da_hrl_lmps"]:
        try:
            points = await fetch_da_lmp_for_today(client, cfg, now_local)
            if points:
                write_api.write(bucket=cfg.influx_bucket, record=points)
                log("info", "feed_ok", feed="da_hrl_lmps", points=len(points))
                fired.append("da_hrl_lmps")
        except Exception as exc:
            log("error", "feed_failed", feed="da_hrl_lmps",
                error=str(exc), error_type=type(exc).__name__)

    if hour in FEED_SCHEDULE["load_frcstd_7_day"]:
        try:
            points = await fetch_load_forecast(client, cfg, now_local)
            if points:
                write_api.write(bucket=cfg.influx_bucket, record=points)
                log("info", "feed_ok", feed="load_frcstd_7_day", points=len(points))
                fired.append("load_frcstd_7_day")
        except Exception as exc:
            log("error", "feed_failed", feed="load_frcstd_7_day",
                error=str(exc), error_type=type(exc).__name__)

    log("info", "poll_cycle_done", local_hour=hour, fired=fired)
    HEALTH_MARKER.touch()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    cfg = Config.from_env()
    log("info", "startup", poll_interval_s=cfg.poll_interval_s,
        bucket=cfg.influx_bucket, tz=str(cfg.tz))

    influx = InfluxDBClient(url=cfg.influx_url, token=cfg.influx_token, org=cfg.influx_org)
    write_api = influx.write_api(write_options=SYNCHRONOUS)

    stop = asyncio.Event()

    def handle_stop(signum, _frame):
        log("info", "signal_received", signum=signum)
        stop.set()
    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    async def run():
        async with PJMClient(cfg) as client:
            while not stop.is_set():
                try:
                    await poll_once(client, write_api, cfg)
                except Exception as exc:
                    log("error", "poll_unhandled_error",
                        error=str(exc), error_type=type(exc).__name__)
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
