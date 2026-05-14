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
    `pjm.inst_load` tagged `area="COMED"`.
  * `inst_load_rto` (P1.1) — same feed, area=PJM RTO, every 5 minutes.
    Feeds the parallel RTO 5CP detector. Writes to `pjm.inst_load`
    tagged `area="PJM RTO"`.
  * `hrl_load_metered_rto` (P1.1) — same feed, zone=RTO (PJM's
    aggregate-level zone code, NOT the sum of zonal rows), hourly with
    5-day lookback. Feeds the RTO 5CP detector's season-to-date
    5th-highest baseline. Writes to `pjm.metered_load` tagged
    `zone="RTO"`. First-call tripwire logs `warn` if any hour comes
    back with >1 row (would indicate PJM started returning per-zone
    rows instead of an aggregate).
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
import math
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

# PJM's `_ept` timestamp fields are always Eastern Prevailing Time, which
# follows DST identically to America/New_York (EST in winter, EDT in
# summer). The scheduler's operator-local tz (``cfg.tz``, default Chicago)
# is only relevant to wake-loop scheduling and per-feed dispatch -- NOT
# to parsing PJM timestamps. Conflating the two is a 1-hour summer offset
# bug that misaligns every PJM-written row.
#
# Backfill script ``scripts/backfill_pjm.py`` already uses this constant
# correctly (line 74); this constant unifies the live poller with that
# convention. See PJM Data Miner 2 spec for the EPT field definition.
EPT = ZoneInfo("America/New_York")

# PJM RTO-wide codes (P1.1). PJM 5CPs are RTO-wide, not zonal -- the
# residential capacity charge depends on the household's metered demand
# during the 5 highest hourly demand hours of the *entire* PJM
# footprint (per HVAC_LOGIC.md "Capacity peak context"). The hvac-
# scheduler's §3 detector runs a parallel state machine on RTO data
# alongside the existing ComEd-zone path; this poller feeds it.
#
# Codes per PJM DM2 OpenAPI spec:
#   inst_load:        area="PJM RTO"  (URL-encoded as "PJM%20RTO" by the
#                                       HTTP client's urlencode pass)
#   hrl_load_metered: zone="RTO"      (RTO is a documented aggregation
#                                       level alongside the 20 zonal
#                                       codes; returns 1 aggregate row
#                                       per hour, NOT N rows per zone)
RTO_INST_AREA = "PJM RTO"
RTO_METERED_ZONE = "RTO"


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


def seconds_to_next_aligned_tick(now_local: datetime, interval_s: float) -> float:
    """Compute seconds until the next ``interval_s``-aligned wall-clock
    boundary. For ``interval_s=300`` this returns the time to the next
    :00, :05, :10, ..., :55 minute mark.

    The wake loop must align to clock boundaries because ``Schedule``
    gates on ``now_local.minute in self.minutes`` and the existing
    minute tuples are ``(0,)`` (hourly feeds) and
    ``(0, 5, 10, ..., 55)`` (inst_load). A fixed-interval loop anchored
    to startup time would tick at offsets like :49, :54, :59, :04, :09
    if the container started at :49 — none of those land on the
    schedule's allowed minutes, so inst_load would never fire.

    Always returns a strictly positive value so the loop guarantees
    forward progress (returns ``interval_s`` rather than 0 when called
    exactly on a boundary).
    """
    epoch = now_local.timestamp()
    next_tick = math.ceil((epoch + 1e-6) / interval_s) * interval_s
    return next_tick - epoch

# Sub-hourly inst_load tick set: every 5 minutes within the hour.
_EVERY_5_MIN = tuple(range(0, 60, 5))


FEED_SCHEDULE: dict[str, Schedule] = {
    "da_hrl_lmps":            Schedule(hours=(17,)),
    "load_frcstd_7_day":      Schedule(hours=(6, 13)),
    "hrl_load_metered":       Schedule(hours=tuple(range(0, 24))),                            # Hourly, 5d lookback, zone=CE
    "hrl_load_metered_rto":   Schedule(hours=tuple(range(0, 24))),                            # Hourly, 5d lookback, zone=RTO (P1.1)
    "inst_load":              Schedule(hours=tuple(range(0, 24)), minutes=_EVERY_5_MIN),      # Every 5 min, area=COMED
    "inst_load_rto":          Schedule(hours=tuple(range(0, 24)), minutes=_EVERY_5_MIN),      # Every 5 min, area=PJM RTO (P1.1)
    "ops_sum_frcst_peak_rto": Schedule(hours=(6, 13), months=(6, 7, 8, 9)),                   # Cooling season only
    "annual_zonal_nspl":      Schedule(hours=(3,), months=(12,), days=(1,)),                  # Dec 1, 03:00 CT
    "rt_hrl_lmps":            Schedule(hours=(12,)),                                          # 12:00 CT = 13:00 ET, ~1h after PJM 11-12 ET publish (Phase 2 spec §8)
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


def _parse_ept(s: str) -> datetime:
    """Parse a PJM EPT timestamp like '2026-05-05T13:00:00' as
    Eastern Prevailing Time.

    EPT is the timezone PJM uses for the ``_ept`` field suffix across
    every Data Miner 2 endpoint. It is NOT the operator's local time:
    Chicago-based operators must still parse with America/New_York to
    avoid a 1-hour summer (EDT vs CDT) offset. Pre-2026-05 the live
    poller had ``_parse_ept(s, tz=cfg.tz)`` which Chicago-defaulted to
    CDT in summer and wrote every PJM-derived row an hour late.
    """
    return datetime.fromisoformat(s).replace(tzinfo=EPT)


def build_rt_lmp_points(items: list[dict]) -> list[Point]:
    """Convert ``rt_hrl_lmps`` items to ``pjm.lmp_rt_hourly`` points.
    One point per hourly settled LMP row for the COMED zone.

    Bill-canonical supply price for ComEd Rate BESH (spec §8): the
    bill charges PJM RT settled hourly LMP for COMED with no markup,
    so this is what HVAC$ rolls up against. PJM publishes settled
    rows 11am-12pm ET T+1, so the daily orchestrator pulls
    *yesterday's* date.

    Schema parallels ``build_da_lmp_points`` with `_rt` field suffixes
    and the ``pjm.lmp_rt_hourly`` measurement name.
    """
    out: list[Point] = []
    for it in items:
        ts_utc = _parse_ept(it["datetime_beginning_ept"]).astimezone(timezone.utc)
        p = (
            Point("pjm.lmp_rt_hourly")
            .tag("pnode_id", str(it.get("pnode_id", "")))
            .tag("pnode_name", it.get("pnode_name", "") or "")
            .tag("zone", it.get("zone") or it.get("pnode_name") or "")
            .field("total_lmp_rt", float(it.get("total_lmp_rt") or 0.0))
            .field("system_energy_price_rt", float(it.get("system_energy_price_rt") or 0.0))
            .field("congestion_price_rt", float(it.get("congestion_price_rt") or 0.0))
            .field("marginal_loss_price_rt", float(it.get("marginal_loss_price_rt") or 0.0))
            .time(ts_utc)
        )
        out.append(p)
    return out


def build_da_lmp_points(items: list[dict]) -> list[Point]:
    out: list[Point] = []
    for it in items:
        ts_utc = _parse_ept(it["datetime_beginning_ept"]).astimezone(timezone.utc)
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


def build_load_forecast_points(items: list[dict]) -> list[Point]:
    out: list[Point] = []
    for it in items:
        target_utc = _parse_ept(it["forecast_datetime_beginning_ept"]).astimezone(timezone.utc)
        evaluated_utc = _parse_ept(it["evaluated_at_datetime_ept"]).astimezone(timezone.utc)
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


def build_inst_load_points(items: list[dict]) -> list[Point]:
    """Convert ``inst_load`` items to ``pjm.inst_load`` points. One point
    per posted observation (PJM publishes throughout the operating day at
    irregular sub-hour intervals); tagged by ``area``. The ``mw`` field
    name matches ``pjm.metered_load`` so the §3 5CP detector can switch
    feeds without renaming downstream queries."""
    out: list[Point] = []
    for it in items:
        ts_utc = _parse_ept(it["datetime_beginning_ept"]).astimezone(timezone.utc)
        out.append(
            Point("pjm.inst_load")
            .tag("area", it.get("area", "") or "")
            .field("mw", float(it.get("instantaneous_load") or 0.0))
            .time(ts_utc)
        )
    return out


def build_metered_load_points(items: list[dict]) -> list[Point]:
    """Convert hrl_load_metered items to `pjm.metered_load` points.
    One point per hour; tagged by zone (`CE` for ComEd) and is_verified."""
    out: list[Point] = []
    for it in items:
        ts_utc = _parse_ept(it["datetime_beginning_ept"]).astimezone(timezone.utc)
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


def build_peak_forecast_points(items: list[dict]) -> list[Point]:
    """Convert ops_sum_frcst_peak_rto items to `pjm.peak_forecast_rto` points.
    Timestamp is `generated_at_ept` (when PJM published the forecast); the
    projected-peak datetime is stored as a string field for downstream
    parsing. Multiple revisions per day are kept separate by the timestamp."""
    out: list[Point] = []
    for it in items:
        ts_utc = _parse_ept(it["generated_at_ept"]).astimezone(timezone.utc)
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


def build_nspl_points(items: list[dict]) -> list[Point]:
    """Convert annual_zonal_nspl items to `pjm.nspl_zonal` points.
    Timestamp is the actual peak hour from the prior summer (which is the
    underlying load coincidence that determined this NSPL). Tagged by
    zone and effective billing year."""
    out: list[Point] = []
    for it in items:
        ts_utc = _parse_ept(it["datetime_beginning_ept"]).astimezone(timezone.utc)
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


async def fetch_rt_lmp_for_date(
    client: PJMClient, cfg: Config, target_date_ept: datetime,
) -> list[Point]:
    """Pull 24 hourly settled RT LMPs for ComEd zone for ``target_date_ept``.

    ``target_date_ept`` is treated as an EPT calendar date — only the
    date portion is used. The PJM ``rt_hrl_lmps`` endpoint returns one
    row per hour for the requested date (24 rows on standard days,
    23 or 25 on DST transition days).

    Used by both:
      - The daily 12:00 CT orchestrator ``fetch_rt_lmp_for_yesterday``
        (passes yesterday-in-EPT)
      - The backfill script ``backfill_rt_hrl_lmps.py`` (iterates
        2026-01-01 through yesterday)
    """
    target = target_date_ept.strftime("%Y-%m-%dT00:00:00.0")
    items = await client.fetch(
        "rt_hrl_lmps",
        {
            "pnode_id": COMED_PNODE_ID,
            "datetime_beginning_ept": target,
            "rowCount": 50,
            "startRow": 1,
        },
    )
    return build_rt_lmp_points(items)


async def fetch_rt_lmp_for_yesterday(
    client: PJMClient, cfg: Config, now_local: datetime,
) -> list[Point]:
    """Daily 12:00 CT orchestrator: pull yesterday's settled LMP rows.

    PJM posts settled hourly data 11am-12pm ET on business days for
    the prior calendar day. Firing at 12:00 CT (= 13:00 ET) gives a
    ~1h margin past the typical publish window. The "yesterday" date
    boundary is computed in EPT (not Chicago) since PJM's
    ``datetime_beginning_ept`` filter is Eastern; near midnight the
    two TZs disagree by 1h and a Chicago-based "yesterday" would ask
    PJM for the wrong calendar date.

    Weekend behavior: PJM publishes settled data on business days only,
    so Monday's run for Sunday will see zero rows; Tuesday's run will
    pick up Sunday + Monday (the backfill window covers the prior day,
    but Influx dedups by timestamp so re-fetching is idempotent).
    """
    yesterday_ept = now_local.astimezone(EPT) - timedelta(days=1)
    return await fetch_rt_lmp_for_date(client, cfg, yesterday_ept)


async def fetch_da_lmp_for_tomorrow(client: PJMClient, cfg: Config, now_local: datetime) -> list[Point]:
    """Pull tomorrow's DA LMPs for ComEd zone (24 rows).

    PJM's day-ahead market clears around 16:00 ET; this fetcher fires at
    17:00 local (per FEED_SCHEDULE) so tomorrow's hourly DA prices are
    posted by then. ComEd's Hourly Pricing FAQ confirms day-ahead prices
    are available after 5 p.m. CT.

    `row_is_current=true` filters out superseded revisions when PJM
    re-posts a price.

    EPT conversion (post-2026-05): the ``datetime_beginning_ept`` filter
    expects Eastern Prevailing Time. Convert from cfg.tz (Chicago) to
    EPT before formatting so the "tomorrow" date boundary is computed
    in Eastern (matters near midnight when the CT and EPT calendar
    dates diverge).
    """
    now_ept = now_local.astimezone(EPT)
    target = (now_ept + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00.0")
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
    return build_da_lmp_points(items)


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
    return build_load_forecast_points(items)


def _check_rto_metered_load_rows_per_hour(items: list[dict]) -> None:
    """P1.1 first-call invariant: PJM's ``hrl_load_metered`` feed
    treats ``RTO`` as an aggregation level that should return *one*
    aggregate row per hour (per ``datetime_beginning_ept``), not N
    rows per zone summing to RTO.

    If any hour has more than one row, the schema assumption is wrong
    and downstream Flux queries that read ``pjm.metered_load{zone=RTO}``
    would sum N rows into a meaninglessly-inflated season-5th-highest
    value. Log a warn so the regression surfaces immediately instead
    of silently corrupting the RTO detector's baseline.
    """
    hour_counts: dict[str, int] = {}
    for it in items:
        key = str(it.get("datetime_beginning_ept", "") or "")
        hour_counts[key] = hour_counts.get(key, 0) + 1
    duplicate_hours = {ts: n for ts, n in hour_counts.items() if n > 1}
    if duplicate_hours:
        log("warn", "rto_metered_load_unexpected_rows_per_hour",
            duplicate_hours=duplicate_hours,
            note="zone=RTO should return one aggregate row per hour",
        )


async def _fetch_metered_load_recent_for_zone(
    client: PJMClient, cfg: Config, now_local: datetime, *, zone: str,
) -> list[Point]:
    """Internal: pull the last 5 days of hrl_load_metered for a given
    PJM zone (ComEd ``CE`` or aggregate ``RTO``). Range filter uses
    PJM's ``<start>to<end>`` syntax.

    The 5-day window comes from the official PJM DM2 spec for
    ``hrl_load_metered``: "There will be a lag in updated data
    availability due to wait time for possible corrections. Data
    adjustments can occur up to 90 days after the actual date." PJM
    publishes this feed daily with a 2-3 day typical lag. The 5-day
    lookback absorbs PJM's typical multi-day publish delay plus
    weekend gaps and still keeps payloads under 200 rows. Influx
    dedups identical timestamps so the hourly polling cadence layered
    on top of this 5-day window writes nothing extra unless PJM posted
    new data.

    EPT conversion: ``datetime_beginning_ept`` expects Eastern; convert
    from cfg.tz (Chicago) before formatting. With a 5-day window the
    1-hour offset is rounding error, but consistency across fetchers
    matters.
    """
    end = now_local.astimezone(EPT)
    start = end - timedelta(days=5)
    items = await client.fetch(
        "hrl_load_metered",
        {
            "zone": zone,
            "datetime_beginning_ept": (
                f"{start.strftime('%Y-%m-%dT%H:%M:%S')}.0to"
                f"{end.strftime('%Y-%m-%dT%H:%M:%S')}.0"
            ),
            "rowCount": 500,
            "startRow": 1,
        },
    )
    if zone == RTO_METERED_ZONE:
        _check_rto_metered_load_rows_per_hour(items)
    return build_metered_load_points(items)


async def fetch_metered_load_recent(client: PJMClient, cfg: Config, now_local: datetime) -> list[Point]:
    """ComEd-zone metered load (zone=CE). See _fetch_metered_load_recent_for_zone."""
    return await _fetch_metered_load_recent_for_zone(
        client, cfg, now_local, zone=COMED_METERED_ZONE,
    )


async def fetch_metered_load_recent_rto(client: PJMClient, cfg: Config, now_local: datetime) -> list[Point]:
    """RTO-aggregate metered load (zone=RTO) for the §3 RTO 5CP detector."""
    return await _fetch_metered_load_recent_for_zone(
        client, cfg, now_local, zone=RTO_METERED_ZONE,
    )


async def _fetch_inst_load_recent_for_area(
    client: PJMClient, cfg: Config, now_local: datetime, *, area: str,
) -> list[Point]:
    """Internal: pull the last 30 minutes of inst_load for a given PJM
    area (``COMED`` or ``PJM RTO``). PJM publishes inst_load
    "frequently throughout the operating day" -- the documented cadence
    is sub-5-minute. The 30-minute lookback catches stragglers if the
    poller missed a tick (container restart, transient Influx write
    failure) without bloating payloads. Influx dedups overlapping pulls
    so the 5-min poll cadence layered on top is safe.

    Note: ``inst_load`` filters by ``area`` (not ``zone`` like
    ``hrl_load_metered``). The PJM DM2 spec lists this asymmetry as
    intentional; ``area="COMED"`` is the right code for ComEd in this
    feed, and ``area="PJM RTO"`` is the RTO-wide aggregate (URL-encoded
    as ``PJM%20RTO`` by the HTTP client).

    EPT conversion (post-2026-05): the ``datetime_beginning_ept``
    filter expects Eastern Prevailing Time. Pre-fix this function
    formatted ``now_local`` (cfg.tz = America/Chicago) directly,
    which in summer asks PJM for the 30-min window ending 1 hour BEFORE
    actual now -- the 5CP detector then reads stale or empty
    ``pjm.inst_load`` rows and the live-load side of the trigger
    silently degrades. Converting to ``EPT`` aligns the request
    window with what PJM expects.
    """
    end = now_local.astimezone(EPT)
    start = end - timedelta(minutes=30)
    items = await client.fetch(
        "inst_load",
        {
            "area": area,
            "datetime_beginning_ept": (
                f"{start.strftime('%Y-%m-%dT%H:%M:%S')}.0to"
                f"{end.strftime('%Y-%m-%dT%H:%M:%S')}.0"
            ),
            "rowCount": 200,
            "startRow": 1,
        },
    )
    return build_inst_load_points(items)


async def fetch_inst_load_recent(client: PJMClient, cfg: Config, now_local: datetime) -> list[Point]:
    """ComEd-area instantaneous load (area=COMED). See _fetch_inst_load_recent_for_area."""
    return await _fetch_inst_load_recent_for_area(
        client, cfg, now_local, area=COMED_INST_AREA,
    )


async def fetch_inst_load_recent_rto(client: PJMClient, cfg: Config, now_local: datetime) -> list[Point]:
    """RTO-area instantaneous load (area="PJM RTO") for the §3 RTO 5CP
    detector. Independent feed from the ComEd inst_load; both run on
    the same 5-minute schedule."""
    return await _fetch_inst_load_recent_for_area(
        client, cfg, now_local, area=RTO_INST_AREA,
    )


async def fetch_peak_forecast_rto(client: PJMClient, cfg: Config, now_local: datetime) -> list[Point]:
    """Pull today's PJM RTO peak forecast. PJM may revise through the day,
    so we fetch all rows generated since midnight Eastern.

    EPT conversion: ``generated_at_ept`` expects Eastern. The "today
    midnight" boundary is in Eastern, which can differ from Chicago
    midnight by an hour. Convert before formatting."""
    now_ept = now_local.astimezone(EPT)
    today_start = now_ept.replace(hour=0, minute=0, second=0, microsecond=0)
    items = await client.fetch(
        "ops_sum_frcst_peak_rto",
        {
            "area": "PJM RTO",
            "generated_at_ept": (
                f"{today_start.strftime('%Y-%m-%dT%H:%M:%S')}.0to"
                f"{now_ept.strftime('%Y-%m-%dT23:59:59')}.0"
            ),
            "rowCount": 50,
            "startRow": 1,
        },
    )
    return build_peak_forecast_points(items)


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
    return build_nspl_points(items)


# Dispatch table: schedule keys map to fetcher coroutines.
FEED_DISPATCHERS: dict[
    str, Callable[[PJMClient, Config, datetime], Awaitable[list[Point]]]
] = {
    "da_hrl_lmps": fetch_da_lmp_for_tomorrow,
    "load_frcstd_7_day": fetch_load_forecast,
    "hrl_load_metered": fetch_metered_load_recent,
    "hrl_load_metered_rto": fetch_metered_load_recent_rto,  # P1.1
    "inst_load": fetch_inst_load_recent,
    "inst_load_rto": fetch_inst_load_recent_rto,  # P1.1
    "ops_sum_frcst_peak_rto": fetch_peak_forecast_rto,
    "annual_zonal_nspl": fetch_annual_nspl,
    "rt_hrl_lmps": fetch_rt_lmp_for_yesterday,  # Phase 2 (spec §8)
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
            # Initial alignment: sleep to the next clock-aligned tick so
            # the loop's wake times land on the minute marks the schedule
            # tuples expect (e.g., :00, :05, :10, ... for poll_interval=300).
            sleep_s = seconds_to_next_aligned_tick(datetime.now(cfg.tz),
                                                    cfg.poll_interval_s)
            log("info", "initial_alignment_sleep", seconds=round(sleep_s, 2))
            try:
                await asyncio.wait_for(stop.wait(), timeout=sleep_s)
            except asyncio.TimeoutError:
                pass

            while not stop.is_set():
                try:
                    await poll_once(client, write_api, cfg)
                except Exception as exc:
                    log("error", "poll_unhandled_error",
                        error=str(exc), error_type=type(exc).__name__)
                # Sleep to the NEXT aligned boundary, not a fixed interval
                # from now. If poll_once took 1.3s the next tick is in
                # interval_s - 1.3 seconds, keeping the loop on the clock
                # grid even if poll_once durations vary.
                sleep_s = seconds_to_next_aligned_tick(datetime.now(cfg.tz),
                                                        cfg.poll_interval_s)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=sleep_s)
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
