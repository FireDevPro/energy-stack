"""PJM Data Miner 2 -> InfluxDB poller.

Polls every 5 minutes; each feed knows when it should fire (hours + minutes
+ optional weekday/month/day-of-month) and silently skips otherwise. The
scheduling is per-feed because cadences differ widely:

  * `da_hrl_lmps` for ComEd zonal aggregate (pnode_id=33092371) at 17:00 CT.
    Day-ahead market clears ~16:00 CT; 17:00 ensures tomorrow's prices are
    posted. Writes to `pjm.lmp_da_hourly` (24 points/day, 4 fields each).
  * `load_frcstd_7_day` for forecast_area=COMED at 06:00 + 13:00 CT.
    Twice daily picks up the morning revision and the late-morning update.
    Writes to `pjm.load_forecast` with `evaluated_at_iso` tag distinguishing
    forecast revisions of the same target hour.
  * `hrl_load_metered` for zone=CE (ComEd's PJM zone code per the official
    DM2 OpenAPI spec; the `area` filter on inst_load uses "COMED" instead
    so the convention differs by feed) every hour with a 5-day rolling
    lookback window. Cadence migrated from weekly Sunday-02:00 in May
    2026, then widened from 3-hour to 5-day lookback when the official
    PJM spec confirmed: "There will be a lag in updated data
    availability due to wait time for possible corrections. Data
    adjustments can occur up to 90 days after the actual date." Hourly
    polling catches newly-posted historical data within an hour of when
    PJM ships it; the 5-day lookback is wide enough to absorb PJM's
    typical multi-day publish lag plus weekend gaps. Influx deduplicates
    on identical timestamps so overlapping pulls are safe.
    Writes to `pjm.metered_load`. Feeds the §3 5CP-detector's
    season-to-date 5th-highest baseline.
  * `inst_load` for area=COMED every 5 minutes. PJM-described as
    "approximate, NOT official PJM Loads" but "frequently updated
    throughout the operating day" — exactly what the §3 5CP-detector's
    `current_load_mw` side needs (a real-time directional signal of
    "are we climbing toward season-5th right now?"). Writes to
    `pjm.inst_load`.
  * `ops_sum_frcst_peak_rto` (PJM RTO peak forecast) at 06:00 + 13:00 CT
    during the cooling season (Jun-Sep). PJM's own daily projected peak
    load + projected peak hour. Writes to `pjm.peak_forecast_rto`.
  * `annual_zonal_nspl` for zone=COMED (NOTE: this feed uses "COMED", not
    "CE" -- the zone-code convention differs from hrl_load_metered) on
    December 1 at 03:00 CT. Yearly snapshot of the Network Service Peak
    Load allocation. Writes to `pjm.nspl_zonal`.

Schema and design rationale: docs/PJM_DM2_INTEGRATION.md.
Feed catalog with column refs and ComEd-specific constants: docs/PJM_DM2_FEEDS.md.

Non-Member API tier:
  * 6 calls/min ceiling. Steady-state load is at most 4 calls per scheduled
    hour (when multiple feeds share the same hour); inter-call spacing
    handled by the per-feed dispatch loop.
  * 50,000-row max per call. Largest feed payload (load_frcstd_7_day with
    forecast_area=COMED) is ~168 hours x several revisions = under 1k.

ComEd zone-code conventions (this differs by feed per the PJM DM2
OpenAPI spec -- there is no single canonical ComEd zone code in the
PJM API):
  * da_hrl_lmps:        pnode_id=33092371 / pnode_name="COMED"
  * load_frcstd_7_day:  forecast_area="COMED"
  * hrl_load_metered:   zone="CE"     (PJM transmission-zone code list)
  * inst_load:          area="COMED"  (PJM area-code list)
  * annual_zonal_nspl:  zone="COMED"  (full name)

Auth: header `Ocp-Apim-Subscription-Key: $PJM_DM2_API_KEY`.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

import aiohttp
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

HEALTH_MARKER = Path("/tmp/last_poll_ok")
PJM_API_BASE = "https://api.pjm.com/api/v1"

# ComEd-zone codes by feed (see module docstring for why this isn't uniform).
COMED_PNODE_ID = 33092371
COMED_FORECAST_AREA = "COMED"
COMED_METERED_ZONE = "CE"
COMED_INST_AREA = "COMED"
COMED_NSPL_ZONE = "COMED"


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Schedule:
    """When a feed should fire, in cfg.tz local time. The wake loop ticks
    every cfg.poll_interval_s and checks should_fire() each cycle.

    ``minutes`` defaults to ``(0,)`` so feeds added before the 5-minute
    poll-interval migration keep firing at the top of the hour only.
    Sub-hourly feeds (notably ``inst_load``) override with a wider tuple.

    ``weekdays`` uses ISO convention (0=Monday, 6=Sunday). All
    None-valued optional fields mean 'any'."""
    hours: tuple[int, ...]
    minutes: tuple[int, ...] = (0,)
    weekdays: tuple[int, ...] | None = None
    months: tuple[int, ...] | None = None
    days: tuple[int, ...] | None = None

    def should_fire(self, now_local: datetime) -> bool:
        if now_local.hour not in self.hours:
            return False
        if now_local.minute not in self.minutes:
            return False
        if self.weekdays is not None and now_local.weekday() not in self.weekdays:
            return False
        if self.months is not None and now_local.month not in self.months:
            return False
        if self.days is not None and now_local.day not in self.days:
            return False
        return True


# Wake-loop tick cadence. With minute-level Schedule gating, the wake loop
# ticks every 5 minutes; sub-hourly feeds (inst_load) fire on every tick
# and hourly feeds fire on the matching :00 tick. Pre-§inst-load this was
# 3600 (hourly); the change is forward-compatible since every existing
# Schedule has minutes=(0,) so they still fire only at the top of the hour.
DEFAULT_POLL_INTERVAL_S = 300

# Sub-hourly inst_load tick set: every 5 minutes within the hour.
_EVERY_5_MIN = tuple(range(0, 60, 5))


FEED_SCHEDULE: dict[str, Schedule] = {
    "da_hrl_lmps":            Schedule(hours=(17,)),
    "load_frcstd_7_day":      Schedule(hours=(6, 13)),
    "hrl_load_metered":       Schedule(hours=tuple(range(0, 24))),                            # Hourly, 5d lookback
    "inst_load":              Schedule(hours=tuple(range(0, 24)), minutes=_EVERY_5_MIN),      # Every 5 min
    "ops_sum_frcst_peak_rto": Schedule(hours=(6, 13), months=(6, 7, 8, 9)),                   # Cooling season only
    "annual_zonal_nspl":      Schedule(hours=(3,), months=(12,), days=(1,)),                  # Dec 1, 03:00 CT
}


def log(level: str, msg: str, **fields_: object) -> None:
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "level": level, "msg": msg}
    rec.update(fields_)
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
            poll_interval_s=float(os.environ.get("PJM_DM2_POLL_INTERVAL",
                                                  str(DEFAULT_POLL_INTERVAL_S))),
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
                raise RuntimeError(f"PJM {feed} HTTP {resp.status}: {text[:200]}")
            payload = json.loads(text)
        items = payload.get("items") or []
        log("debug", "pjm_fetch_ok", feed=feed,
            total_rows=payload.get("totalRows"), returned=len(items))
        return items


# ---------------------------------------------------------------------------
# Pure-logic point builders (one per feed)
# ---------------------------------------------------------------------------


def _parse_ept(s: str, tz: ZoneInfo) -> datetime:
    """Parse a PJM EPT timestamp like '2026-05-05T13:00:00' as tz-local."""
    return datetime.fromisoformat(s).replace(tzinfo=tz)


def build_da_lmp_points(items: list[dict], tz: ZoneInfo) -> list[Point]:
    out: list[Point] = []
    for it in items:
        ts_utc = _parse_ept(it["datetime_beginning_ept"], tz).astimezone(timezone.utc)
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
    out: list[Point] = []
    for it in items:
        target_utc = _parse_ept(it["forecast_datetime_beginning_ept"], tz).astimezone(timezone.utc)
        evaluated_utc = _parse_ept(it["evaluated_at_datetime_ept"], tz).astimezone(timezone.utc)
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


def build_inst_load_points(items: list[dict], tz: ZoneInfo) -> list[Point]:
    """Convert ``inst_load`` items to ``pjm.inst_load`` points. One point
    per posted observation (PJM publishes throughout the operating day at
    irregular sub-hour intervals); tagged by ``area``. The ``mw`` field
    name matches ``pjm.metered_load`` so the §3 5CP detector can switch
    feeds without renaming downstream queries."""
    out: list[Point] = []
    for it in items:
        ts_utc = _parse_ept(it["datetime_beginning_ept"], tz).astimezone(timezone.utc)
        out.append(
            Point("pjm.inst_load")
            .tag("area", it.get("area", "") or "")
            .field("mw", float(it.get("instantaneous_load") or 0.0))
            .time(ts_utc)
        )
    return out


def build_metered_load_points(items: list[dict], tz: ZoneInfo) -> list[Point]:
    """Convert hrl_load_metered items to `pjm.metered_load` points.
    One point per hour; tagged by zone (`CE` for ComEd) and is_verified."""
    out: list[Point] = []
    for it in items:
        ts_utc = _parse_ept(it["datetime_beginning_ept"], tz).astimezone(timezone.utc)
        p = (
            Point("pjm.metered_load")
            .tag("zone", it.get("zone", "") or "")
            .tag("load_area", it.get("load_area", "") or "")
            .tag("is_verified", str(bool(it.get("is_verified"))).lower())
            .field("mw", float(it.get("mw") or 0.0))
            .time(ts_utc)
        )
        out.append(p)
    return out


def build_peak_forecast_points(items: list[dict], tz: ZoneInfo) -> list[Point]:
    """Convert ops_sum_frcst_peak_rto items to `pjm.peak_forecast_rto` points.
    Timestamp is `generated_at_ept` (when PJM published the forecast); the
    projected-peak datetime is stored as a string field for downstream
    parsing. Multiple revisions per day are kept separate by the timestamp."""
    out: list[Point] = []
    for it in items:
        ts_utc = _parse_ept(it["generated_at_ept"], tz).astimezone(timezone.utc)
        projected_local = it.get("projected_peak_datetime_ept", "") or ""
        p = (
            Point("pjm.peak_forecast_rto")
            .tag("area", it.get("area", "") or "")
            .field("load_forecast_mw", float(it.get("load_forecast") or 0.0))
            .field("total_scheduled_capacity_mw", float(it.get("total_scheduled_capacity") or 0.0))
            .field("operating_reserve_mw", float(it.get("operating_reserve") or 0.0))
            .field("internal_scheduled_capacity_mw", float(it.get("internal_scheduled_capacity") or 0.0))
            .field("scheduled_tie_flow_mw", float(it.get("scheduled_tie_flow_total") or 0.0))
            .field("unscheduled_steam_capacity_mw", float(it.get("unscheduled_steam_capacity") or 0.0))
            .field("projected_peak_datetime_ept", projected_local)
            .time(ts_utc)
        )
        out.append(p)
    return out


def build_nspl_points(items: list[dict], tz: ZoneInfo) -> list[Point]:
    """Convert annual_zonal_nspl items to `pjm.nspl_zonal` points.
    Timestamp is the actual peak hour from the prior summer (which is the
    underlying load coincidence that determined this NSPL). Tagged by
    zone and effective billing year."""
    out: list[Point] = []
    for it in items:
        ts_utc = _parse_ept(it["datetime_beginning_ept"], tz).astimezone(timezone.utc)
        p = (
            Point("pjm.nspl_zonal")
            .tag("zone", it.get("zone", "") or "")
            .tag("year", str(it.get("year", "")))
            .field("nspl_mw", float(it.get("nspl_mw") or 0.0))
            .time(ts_utc)
        )
        out.append(p)
    return out


# ---------------------------------------------------------------------------
# Per-feed orchestration
# ---------------------------------------------------------------------------


async def fetch_da_lmp_for_tomorrow(client: PJMClient, cfg: Config, now_local: datetime) -> list[Point]:
    """Pull tomorrow's DA LMPs for ComEd zone (24 rows).

    PJM's day-ahead market clears around 16:00 ET; this fetcher fires at
    17:00 local (per FEED_SCHEDULE) so tomorrow's hourly DA prices are
    posted by then. ComEd's Hourly Pricing FAQ confirms day-ahead prices
    are available after 5 p.m. CT.

    `row_is_current=true` filters out superseded revisions when PJM
    re-posts a price.
    """
    target = (now_local + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00.0")
    items = await client.fetch(
        "da_hrl_lmps",
        {
            "pnode_id": COMED_PNODE_ID,
            "datetime_beginning_ept": target,
            "row_is_current": "true",
            "rowCount": 50,
            "startRow": 1,
        },
    )
    return build_da_lmp_points(items, cfg.tz)


async def fetch_load_forecast(client: PJMClient, cfg: Config, now_local: datetime) -> list[Point]:
    """Pull the 7-day load forecast for ComEd (~168 hours)."""
    items = await client.fetch(
        "load_frcstd_7_day",
        {
            "forecast_area": COMED_FORECAST_AREA,
            "rowCount": 500,
            "startRow": 1,
        },
    )
    return build_load_forecast_points(items, cfg.tz)


async def fetch_metered_load_recent(client: PJMClient, cfg: Config, now_local: datetime) -> list[Point]:
    """Pull the last 5 days of ComEd metered load. Range filter uses
    PJM's `<start>to<end>` syntax.

    The 5-day window comes from the official PJM DM2 spec for
    ``hrl_load_metered``: "There will be a lag in updated data
    availability due to wait time for possible corrections. Data
    adjustments can occur up to 90 days after the actual date." The
    earlier 3-hour window (May 2026) was wrong; PJM publishes this feed
    daily with a 2-3 day typical lag, not the 1-hour lag the §0b spec
    assumed. The 5-day lookback absorbs PJM's typical multi-day publish
    delay plus weekend gaps and still keeps payloads under 200 rows.
    Influx dedups identical timestamps so the hourly polling cadence
    layered on top of this 5-day window writes nothing extra unless PJM
    posted new data."""
    end = now_local
    start = end - timedelta(days=5)
    items = await client.fetch(
        "hrl_load_metered",
        {
            "zone": COMED_METERED_ZONE,
            "datetime_beginning_ept": (
                f"{start.strftime('%Y-%m-%dT%H:%M:%S')}.0to"
                f"{end.strftime('%Y-%m-%dT%H:%M:%S')}.0"
            ),
            "rowCount": 500,
            "startRow": 1,
        },
    )
    return build_metered_load_points(items, cfg.tz)


async def fetch_inst_load_recent(client: PJMClient, cfg: Config, now_local: datetime) -> list[Point]:
    """Pull the last 30 minutes of ComEd instantaneous load. PJM publishes
    inst_load "frequently throughout the operating day" — the documented
    cadence is sub-5-minute. The 30-minute lookback catches stragglers
    if the poller missed a tick (container restart, transient Influx
    write failure) without bloating payloads. Influx dedups overlapping
    pulls so the 5-min poll cadence layered on top is safe.

    Note: ``inst_load`` filters by ``area`` (not ``zone`` like
    ``hrl_load_metered``). The PJM DM2 spec lists this asymmetry as
    intentional; ``area="COMED"`` is the right code for the ComEd
    transmission area in this feed."""
    end = now_local
    start = end - timedelta(minutes=30)
    items = await client.fetch(
        "inst_load",
        {
            "area": COMED_INST_AREA,
            "datetime_beginning_ept": (
                f"{start.strftime('%Y-%m-%dT%H:%M:%S')}.0to"
                f"{end.strftime('%Y-%m-%dT%H:%M:%S')}.0"
            ),
            "rowCount": 200,
            "startRow": 1,
        },
    )
    return build_inst_load_points(items, cfg.tz)


async def fetch_peak_forecast_rto(client: PJMClient, cfg: Config, now_local: datetime) -> list[Point]:
    """Pull today's PJM RTO peak forecast. PJM may revise through the day,
    so we fetch all rows generated since midnight local."""
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    items = await client.fetch(
        "ops_sum_frcst_peak_rto",
        {
            "area": "PJM RTO",
            "generated_at_ept": (
                f"{today_start.strftime('%Y-%m-%dT%H:%M:%S')}.0to"
                f"{now_local.strftime('%Y-%m-%dT23:59:59')}.0"
            ),
            "rowCount": 50,
            "startRow": 1,
        },
    )
    return build_peak_forecast_points(items, cfg.tz)


async def fetch_annual_nspl(client: PJMClient, cfg: Config, now_local: datetime) -> list[Point]:
    """Pull all available NSPL years for ComEd (typically the most recent
    5 years; PJM retains a window). Idempotent — re-runs upsert by
    (zone, year, timestamp)."""
    items = await client.fetch(
        "annual_zonal_nspl",
        {
            "zone": COMED_NSPL_ZONE,
            "rowCount": 50,
            "startRow": 1,
        },
    )
    return build_nspl_points(items, cfg.tz)


# Dispatch table: schedule keys map to fetcher coroutines.
FEED_DISPATCHERS: dict[
    str, Callable[[PJMClient, Config, datetime], Awaitable[list[Point]]]
] = {
    "da_hrl_lmps": fetch_da_lmp_for_tomorrow,
    "load_frcstd_7_day": fetch_load_forecast,
    "hrl_load_metered": fetch_metered_load_recent,
    "inst_load": fetch_inst_load_recent,
    "ops_sum_frcst_peak_rto": fetch_peak_forecast_rto,
    "annual_zonal_nspl": fetch_annual_nspl,
}


def _write_feed_status(
    write_api,
    cfg: Config,
    feed_name: str,
    *,
    success: bool,
    points: int = 0,
    error_type: str = "",
    error_msg: str = "",
) -> None:
    """Write one `pjm.feed_status` point recording the outcome of a single
    feed attempt. Independent of whether the feed itself wrote any data
    points to the bucket — observability that survives outages.

    Tagged by `feed` and `success` so per-feed dashboards can filter
    cleanly without parsing field values. Fields carry counts and
    (truncated) error context for the failure case.

    A failure here (e.g., the influx write itself errors) is logged at
    `warn` and swallowed — we don't want monitoring to fail the cycle.
    """
    point = (
        Point("pjm.feed_status")
        .tag("feed", feed_name)
        .tag("success", "true" if success else "false")
        .field("points_written", int(points))
        .field("error_type", error_type)
        .field("error_msg", error_msg[:200])
        .time(datetime.now(timezone.utc))
    )
    try:
        write_api.write(bucket=cfg.influx_bucket, record=[point])
    except Exception as exc:
        log("warn", "feed_status_write_failed", feed=feed_name,
            error=str(exc), error_type=type(exc).__name__)


async def poll_once(client: PJMClient, write_api, cfg: Config) -> None:
    """One pass through the feed schedule. Each feed fires only when its
    Schedule says so; a single feed failure does not abort the cycle.

    Health-marker semantics (CodeX pass 2, 2026-05-07): the
    `/tmp/last_poll_ok` marker is **loop-liveness only** — touched on
    every cycle regardless of whether any due feed succeeded. The earlier
    feed-success-gating attempt was unsound: a 17:00 cycle with all due
    feeds failing would leave the marker stale, but the next 18:00 idle
    cycle would refresh it again, so the staleness budget never actually
    fired on real failures. More fundamentally, Docker HEALTHCHECK is
    semantically "should this container be restarted?" — restarting the
    poller does not fix a PJM API outage, an expired API key, or schema
    drift, so flipping the container unhealthy on feed failures is
    operationally wrong even when the gating logic technically works.

    Data-freshness alerting belongs in the data layer, not the container
    healthcheck. Every feed attempt (success or failure) writes a row to
    `pjm.feed_status` tagged by `feed` and `success`. A telegram-notifier
    or Grafana deadman alert against that measurement, with per-feed
    expected cadence (DA LMP daily, NSPL annually, etc.), is the correct
    surface for "did the data flow when it was supposed to?" — and a
    follow-up PR will wire it.
    """
    now_local = datetime.now(cfg.tz)
    due_feeds: list[str] = []
    fired: list[str] = []
    failed: list[str] = []

    for feed_name, schedule in FEED_SCHEDULE.items():
        if not schedule.should_fire(now_local):
            continue
        due_feeds.append(feed_name)
        try:
            fetcher = FEED_DISPATCHERS[feed_name]
            points = await fetcher(client, cfg, now_local)
            n_points = len(points)
            if points:
                write_api.write(bucket=cfg.influx_bucket, record=points)
                log("info", "feed_ok", feed=feed_name, points=n_points)
            else:
                log("info", "feed_empty", feed=feed_name)
            fired.append(feed_name)
            _write_feed_status(write_api, cfg, feed_name,
                               success=True, points=n_points)
        except Exception as exc:
            log("error", "feed_failed", feed=feed_name,
                error=str(exc), error_type=type(exc).__name__)
            failed.append(feed_name)
            _write_feed_status(write_api, cfg, feed_name, success=False,
                               error_type=type(exc).__name__,
                               error_msg=str(exc))

    # Always touch on a clean loop pass — see docstring. Per-feed health
    # lives in pjm.feed_status, queried by downstream alerting.
    HEALTH_MARKER.touch()

    # Liveness heartbeat for telegram-notifier's check_poller_silence.
    # Written every cycle regardless of whether any feed fired, so the
    # downstream silence check doesn't false-fire during the long quiet
    # stretches between scheduled feeds (e.g., the 6 days between
    # weekly metered-load fires). Failures here are swallowed: a
    # heartbeat write blip should not look like the poller is dead.
    try:
        write_api.write(
            bucket=cfg.influx_bucket,
            record=[
                Point("pjm.poller_heartbeat")
                .field("alive", 1)
                .time(datetime.now(timezone.utc))
            ],
        )
    except Exception as exc:
        log("warn", "heartbeat_write_failed",
            error=str(exc), error_type=type(exc).__name__)

    log("info", "poll_cycle_done",
        local_hour=now_local.hour, weekday=now_local.weekday(),
        month=now_local.month,
        due=due_feeds, fired=fired, failed=failed)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    cfg = Config.from_env()
    log("info", "startup", poll_interval_s=cfg.poll_interval_s,
        bucket=cfg.influx_bucket, tz=str(cfg.tz),
        feeds=list(FEED_SCHEDULE.keys()))

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
