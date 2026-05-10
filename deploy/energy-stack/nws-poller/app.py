"""NWS forecast -> InfluxDB poller.

Pulls gridded forecast and active alerts from api.weather.gov for a fixed
lat/lon (default Plainfield IL) and writes daily snapshots to InfluxDB.
The HVAC scheduler reads the most recent snapshot to decide day-type.

Forecast endpoint: ``forecastGridData`` (migrated from ``forecastHourly``
in May 2026 per ARM_B_IMPLEMENTATION §0a). The grid endpoint exposes
``apparentTemperature`` as a first-class field, which the recalibrated
day-type classifier (§1) needs; it also surfaces relativeHumidity,
skyCover, windGust, and probabilityOfPrecipitation natively.

Each gridded variable's ``values`` list is keyed by an ISO-8601
``start/duration`` interval (e.g. ``2026-05-10T00:00:00+00:00/PT3H``).
Granularity ranges 1h-6h depending on horizon. The parser expands every
entry into 1-hour UTC slots so cross-variable alignment is uniform
before daily roll-up.

Measurements:
  * `nws.forecast` -- one point per period per poll, tagged for_period.
    Tags: for_period ("today"|"tomorrow"|"day2")
    Fields (preserved from forecastHourly era): high_f, low_f,
            max_dewpoint_f, max_wind_mph, max_precip_prob_pct,
            is_heat_advisory (0|1), alert_summary, period_date.
    Fields (new in §0a): apparent_max_f, apparent_min_f, apparent_avg_f,
            rh_max_pct, rh_avg_pct, sky_cover_avg_pct, wind_gust_max_mph.
  * `nws.alerts` -- one point per active alert, tagged event/severity.
    Fields: active=1, expires_unix, headline (truncated)

NWS API quirks:
  * Requires User-Agent header (no key needed).
  * Gridpoint URL is cached; only re-fetched on 404.
  * Grid response covers ~7 days; we slice into local-time days.
  * Temperatures in degC, wind in km/h: converted to F / mph.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import sys
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

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
    tz: ZoneInfo

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
            # Anchor day-bucket and "now" to the user's wall-clock tz, NOT the
            # container's. Containers are typically UTC, so without this, at
            # 21:00 America/Chicago the poller would label calendar days one
            # day ahead of what the scheduler expects — choosing tomorrow's
            # day-type from the wrong forecast.
            tz=ZoneInfo(os.environ.get("SCHEDULER_TZ", "America/Chicago")),
        )


def c_to_f(c):
    return None if c is None else c * 9 / 5 + 32


def kmh_to_mph(kmh):
    return None if kmh is None else kmh * 0.621371


# ---- ISO-8601 duration parsing (validTime intervals) -----------------------

_ISO_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


def parse_iso_duration_hours(s: str) -> float:
    """Parse the duration component of a ``validTime`` interval (e.g. ``PT1H``,
    ``PT3H``, ``P1DT3H``, ``P8DT1H``) into total hours.

    Returns 0.0 for unparseable input. Sub-hour components (``M``/``S``) are
    rolled into fractional hours so callers can decide whether to floor to
    integer slots.
    """
    m = _ISO_DURATION_RE.match(s or "")
    if not m:
        return 0.0
    days = int(m.group("days") or 0)
    hours = int(m.group("hours") or 0)
    minutes = int(m.group("minutes") or 0)
    seconds = int(m.group("seconds") or 0)
    return days * 24 + hours + minutes / 60 + seconds / 3600


# ---- Grid expansion: { validTime, value } -> per-hour UTC slots -----------


def expand_grid_values(values: list[dict]) -> dict[datetime, float]:
    """Expand a single gridded variable's ``values`` list into 1-hour UTC
    slots. Each ``{"validTime": "<start>/PT<n>H", "value": v}`` becomes ``n``
    UTC datetimes ``{start, start+1h, ... start+(n-1)h}``, all carrying the
    same value (NWS publishes one value per interval, not interpolated).

    Entries with sub-hour durations or missing values are dropped; entries
    outside ISO-8601 syntax are skipped. Sub-hour drops emit a ``warn`` log
    so a future NWS schema change to finer granularity doesn't silently
    discard data — easier to debug a log line than a missing-data bug.
    """
    out: dict[datetime, float] = {}
    for entry in values or []:
        vt = entry.get("validTime", "")
        if "/" not in vt:
            continue
        start_iso, dur = vt.split("/", 1)
        try:
            start_utc = datetime.fromisoformat(start_iso).astimezone(timezone.utc)
        except (ValueError, TypeError):
            continue
        hours = parse_iso_duration_hours(dur)
        if entry.get("value") is None:
            continue
        if hours < 1:
            log("warn", "subhour_grid_entry_dropped",
                valid_time=vt, hours=hours)
            continue
        n_hours = int(hours)
        value = float(entry["value"])
        for i in range(n_hours):
            slot = start_utc + timedelta(hours=i)
            out[slot] = value
    return out


def _expand_var(grid_props: dict, name: str) -> dict[datetime, float]:
    """Pull ``grid_props[name].values`` and expand it into hourly slots.
    Returns an empty dict if the variable is absent or empty."""
    var = grid_props.get(name) or {}
    return expand_grid_values(var.get("values") or [])


# ---- Daily roll-up ---------------------------------------------------------


def summarize_grid_for_date(grid_props: dict, target_date, tz: ZoneInfo) -> dict:
    """Aggregate gridded forecast data for ``target_date`` in ``tz``.

    Reads the variables listed in the module docstring, expands each into
    hourly UTC slots, filters by local-tz calendar date, and returns the
    daily roll-up dict consumed by ``build_forecast_points``. Returns ``{}``
    when no temperature observations fall on the target date.
    """
    grids = {
        "temperature":               _expand_var(grid_props, "temperature"),       # degC
        "apparentTemperature":       _expand_var(grid_props, "apparentTemperature"),  # degC
        "dewpoint":                  _expand_var(grid_props, "dewpoint"),          # degC
        "windSpeed":                 _expand_var(grid_props, "windSpeed"),         # km/h
        "windGust":                  _expand_var(grid_props, "windGust"),          # km/h
        "relativeHumidity":          _expand_var(grid_props, "relativeHumidity"),  # %
        "skyCover":                  _expand_var(grid_props, "skyCover"),          # %
        "probabilityOfPrecipitation": _expand_var(grid_props, "probabilityOfPrecipitation"),  # %
    }

    def slice_by_date(grid: dict[datetime, float]) -> list[float]:
        return [v for slot_utc, v in grid.items()
                if slot_utc.astimezone(tz).date() == target_date]

    temps_c = slice_by_date(grids["temperature"])
    if not temps_c:
        return {}

    apps_c = slice_by_date(grids["apparentTemperature"])
    dews_c = slice_by_date(grids["dewpoint"])
    winds_kmh = slice_by_date(grids["windSpeed"])
    gusts_kmh = slice_by_date(grids["windGust"])
    rhs = slice_by_date(grids["relativeHumidity"])
    skies = slice_by_date(grids["skyCover"])
    pops = slice_by_date(grids["probabilityOfPrecipitation"])

    out: dict[str, float] = {
        "high_f": float(c_to_f(max(temps_c))),
        "low_f": float(c_to_f(min(temps_c))),
        "hours_covered": float(len(temps_c)),
    }
    if dews_c:
        out["max_dewpoint_f"] = float(c_to_f(max(dews_c)))
    if winds_kmh:
        out["max_wind_mph"] = float(kmh_to_mph(max(winds_kmh)))
    if pops:
        out["max_precip_prob_pct"] = float(max(pops))
    if apps_c:
        out["apparent_max_f"] = float(c_to_f(max(apps_c)))
        out["apparent_min_f"] = float(c_to_f(min(apps_c)))
        out["apparent_avg_f"] = float(c_to_f(sum(apps_c) / len(apps_c)))
    if rhs:
        out["rh_max_pct"] = float(max(rhs))
        out["rh_avg_pct"] = float(sum(rhs) / len(rhs))
    if skies:
        out["sky_cover_avg_pct"] = float(sum(skies) / len(skies))
    if gusts_kmh:
        out["wind_gust_max_mph"] = float(kmh_to_mph(max(gusts_kmh)))
    return out


# ---- NWS HTTP client -------------------------------------------------------


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
        """Resolve the gridpoint -> forecastGridData URL for the configured
        lat/lon. Cached after first success; on 404 the caller invalidates
        and retries."""
        if self._gridpoint_url:
            return self._gridpoint_url
        url = f"https://api.weather.gov/points/{self.cfg.lat},{self.cfg.lon}"
        data = await self._get(session, url)
        props = data["properties"]
        self._gridpoint_url = props["forecastGridData"]
        self._gridpoint_meta = {
            "office": props.get("cwa"),
            "gridX": props.get("gridX"),
            "gridY": props.get("gridY"),
            "city": (props.get("relativeLocation") or {}).get("properties", {}).get("city"),
            "state": (props.get("relativeLocation") or {}).get("properties", {}).get("state"),
        }
        log("info", "gridpoint_resolved", url=self._gridpoint_url, **self._gridpoint_meta)
        return self._gridpoint_url

    async def fetch_grid_data(self, session: aiohttp.ClientSession) -> dict:
        """Pull the full gridded forecast properties block. Each variable
        (``temperature``, ``apparentTemperature``, ...) lives under its own
        key with a ``values: [{validTime, value}, ...]`` list."""
        url = await self.resolve_gridpoint(session)
        data = await self._get(session, url)
        return data["properties"]

    async def fetch_alerts(self, session: aiohttp.ClientSession) -> list[dict]:
        url = f"https://api.weather.gov/alerts/active?point={self.cfg.lat},{self.cfg.lon}"
        data = await self._get(session, url)
        return data.get("features", [])


# ---- Alert handling (unchanged from forecastHourly era) -------------------


def parse_iso(s: str) -> datetime:
    """NWS returns ISO8601 with offset; Python 3.11+ handles directly."""
    return datetime.fromisoformat(s)


def is_heat_related(event_text: str) -> bool:
    t = (event_text or "").lower()
    return any(k in t for k in ("heat advisory", "excessive heat", "heat warning"))


def alert_window(alert: dict) -> tuple[datetime | None, datetime | None]:
    """Return (start, end) datetimes for an alert's hazardous-conditions window.

    Per the NWS API, the actual hazard window is bounded by ``onset`` (or
    ``effective`` if onset isn't set) on the start side and ``ends`` (or
    ``expires``) on the end side. Either bound may be missing — caller treats
    None as open-ended on that side.
    """
    props = alert.get("properties") or {}
    start_str = props.get("onset") or props.get("effective")
    end_str = props.get("ends") or props.get("expires")
    start = parse_iso(start_str) if start_str else None
    end = parse_iso(end_str) if end_str else None
    return start, end


def heat_active_on_date(alerts: list[dict], target_date, tz: ZoneInfo) -> bool:
    """True if any heat-related alert overlaps ``target_date`` in ``tz``.

    Each forecast period (today/tomorrow/day2) needs a per-day flag; a single
    alert that's ending tonight should not bias tomorrow as HOT.
    """
    day_start = datetime.combine(target_date, datetime.min.time(), tzinfo=tz)
    day_end = day_start + timedelta(days=1)
    for a in alerts:
        if not is_heat_related((a.get("properties") or {}).get("event", "")):
            continue
        start, end = alert_window(a)
        # Open-ended bound = treat as overlapping that side.
        if start is not None and start >= day_end:
            continue
        if end is not None and end <= day_start:
            continue
        return True
    return False


# ---- InfluxDB point construction -------------------------------------------


def build_forecast_points(grid_props: dict, alerts: list[dict], tz: ZoneInfo) -> list[Point]:
    """Build today / tomorrow / day2 snapshot points with alert overlay.

    Days are bucketed in ``tz`` (the user's wall-clock zone). Heat-advisory
    flags are time-sliced per period: an alert ending tonight only flags
    today, not tomorrow or day2.
    """
    now_local = datetime.now(tz)
    today = now_local.date()
    days = {
        "today":    today,
        "tomorrow": today + timedelta(days=1),
        "day2":     today + timedelta(days=2),
    }

    # Cross-period summary stays as "what's currently active anywhere" — useful
    # context for diagnostics. Per-period heat flag is computed below.
    alert_summary = "; ".join(
        sorted({(a.get("properties") or {}).get("event", "") for a in alerts if a.get("properties")})
    )[:240]

    points = []
    for period_label, target_date in days.items():
        agg = summarize_grid_for_date(grid_props, target_date, tz)
        heat_active = heat_active_on_date(alerts, target_date, tz)
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
            grid_props = await client.fetch_grid_data(session)
        except Exception as exc:
            log("warn", "forecast_fetch_failed", error=str(exc), error_type=type(exc).__name__)
            return
        try:
            alerts = await client.fetch_alerts(session)
        except Exception as exc:
            log("warn", "alerts_fetch_failed", error=str(exc), error_type=type(exc).__name__)
            alerts = []

    forecast_points = build_forecast_points(grid_props, alerts, cfg.tz)
    alert_points = build_alert_points(alerts)

    # Influx write errors NOT caught -- bubble up, Docker restarts container
    if forecast_points:
        write_api.write(bucket=cfg.influx_bucket, record=forecast_points)
    if alert_points:
        write_api.write(bucket=cfg.influx_bucket, record=alert_points)

    # Approximate hours_covered from the temperature variable for log line.
    temp_values = (grid_props.get("temperature") or {}).get("values") or []
    log("info", "poll_ok",
        temperature_intervals=len(temp_values),
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
