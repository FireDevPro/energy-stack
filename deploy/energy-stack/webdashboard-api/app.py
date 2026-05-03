"""webdashboard-api -- live data aggregator for the sci-fi HUD.

Single endpoint: GET /api/energy returns the JSON shape that data.jsx's
useTelemetry hook produces, sourced from InfluxDB Flux queries against the
energy bucket. The dashboard polls this endpoint every 5 s; we serve from a
5 s in-memory cache so InfluxDB only sees one query burst per cycle.

Sources:
  * eagle.meter        -> currentDemand, eagleSummation, demandH, status
  * comed.prices       -> currentPrice, priceH, forecast (historical hour-of-day
                          average over the last 14 days), status
  * refoss.channel     -> devices list, voltage L1/L2, mains power factor,
                          today/week/month kWh, top non-mains circuit, status

Mock fields (Phase 4 / not collected):
  * indoor, outdoor, hvacMode, setpoint -- placeholder until TCC wired

Dropped vs the design's mock shape:
  * solar (currentSolar, solarH, todaySolarKwh, netDemand) -- no solar PV
  * scenario -- live data has no scenario, only `now`

Environment:
    INFLUXDB_URL              Default http://influxdb:8086
    INFLUXDB_TOKEN            Read or admin token
    INFLUXDB_ORG              InfluxDB organization
    INFLUXDB_BUCKET           Source bucket (energy)
    API_PORT                  Listen port (default 8082)
    CACHE_TTL_S               Response cache TTL (default 5)
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from aiohttp import web
from influxdb_client import InfluxDBClient


# Refoss channel ids that are split-phase mains (everything else is a branch).
MAIN_CHANNELS = ("em:1", "em:7")


def log(level: str, msg: str, **fields: object) -> None:
    record = {"ts": datetime.now(timezone.utc).isoformat(), "level": level, "msg": msg}
    record.update(fields)
    print(json.dumps(record), flush=True)


@dataclass(frozen=True)
class Config:
    influx_url: str
    influx_token: str
    influx_org: str
    influx_bucket: str
    api_port: int
    cache_ttl_s: float

    @staticmethod
    def from_env() -> "Config":
        def required(name: str) -> str:
            value = os.environ.get(name)
            if not value:
                log("error", "missing_env", var=name)
                sys.exit(2)
            return value

        return Config(
            influx_url=os.environ.get("INFLUXDB_URL", "http://influxdb:8086"),
            influx_token=required("INFLUXDB_TOKEN"),
            influx_org=required("INFLUXDB_ORG"),
            influx_bucket=required("INFLUXDB_BUCKET"),
            api_port=int(os.environ.get("API_PORT", "8082")),
            cache_ttl_s=float(os.environ.get("CACHE_TTL_S", "5")),
        )


def query_one(query_api, q: str) -> list:
    """Run a Flux query and return the FluxRecord list (flattened across tables)."""
    out = []
    for table in query_api.query(q):
        for record in table.records:
            out.append(record)
    return out


def fetch_eagle(query_api, bucket: str) -> dict:
    """Latest EAGLE values + 24h hourly demand series."""
    latest_q = f'''
from(bucket: "{bucket}")
  |> range(start: -10m)
  |> filter(fn: (r) => r._measurement == "eagle.meter")
  |> last()
'''
    latest = query_one(query_api, latest_q)
    fields = {r.get_field(): r.get_value() for r in latest}
    last_ts = max((r.get_time() for r in latest), default=None)

    hourly_q = f'''
from(bucket: "{bucket}")
  |> range(start: -25h)
  |> filter(fn: (r) => r._measurement == "eagle.meter" and r._field == "demand_kw")
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> tail(n: 24)
'''
    rows = query_one(query_api, hourly_q)
    # demandH wants 24 numbers in W (the design used W; demand_kw * 1000)
    # Pad with the most recent value if we have fewer than 24 hours of data.
    series_kw = [r.get_value() for r in rows if r.get_value() is not None]
    if not series_kw:
        series_kw = [0.0]
    while len(series_kw) < 24:
        series_kw.insert(0, series_kw[0])
    series_w = [v * 1000.0 for v in series_kw[-24:]]

    return {
        "demand_kw": float(fields.get("demand_kw") or 0.0),
        "delivered_kwh": float(fields.get("delivered_kwh") or 0.0),
        "last_ts": last_ts,
        "demandH_w": series_w,
    }


def fetch_comed(query_api, bucket: str) -> dict:
    """Current 5-min price, current hour avg, last-24h hourly series, last-14d
    hour-of-day forecast."""
    five_min_q = f'''
from(bucket: "{bucket}")
  |> range(start: -30m)
  |> filter(fn: (r) => r._measurement == "comed.prices"
                    and r._field == "price_cents_per_kwh"
                    and r.period_type == "5min")
  |> last()
'''
    rows = query_one(query_api, five_min_q)
    current_5min = float(rows[0].get_value()) if rows else 0.0
    last_ts = rows[0].get_time() if rows else None

    hourly_q = f'''
from(bucket: "{bucket}")
  |> range(start: -25h)
  |> filter(fn: (r) => r._measurement == "comed.prices"
                    and r._field == "price_cents_per_kwh"
                    and r.period_type == "hourly_avg")
  |> aggregateWindow(every: 1h, fn: last, createEmpty: false)
  |> tail(n: 24)
'''
    rows = query_one(query_api, hourly_q)
    priceH = [float(r.get_value()) for r in rows if r.get_value() is not None]
    if not priceH:
        priceH = [current_5min]
    while len(priceH) < 24:
        priceH.insert(0, priceH[0])
    priceH = priceH[-24:]

    # Forecast: hour-of-day average over the last 14 days. ComEd publishes no
    # forward prices, so we derive a "this is what hour X usually costs"
    # estimate. Returns a list keyed 0..23 with the mean ¢/kWh per hour.
    forecast_q = f'''
import "date"
from(bucket: "{bucket}")
  |> range(start: -14d)
  |> filter(fn: (r) => r._measurement == "comed.prices"
                    and r._field == "price_cents_per_kwh"
                    and r.period_type == "hourly_avg")
  |> map(fn: (r) => ({{ r with hour: date.hour(t: r._time) }}))
  |> group(columns: ["hour"])
  |> mean()
  |> keep(columns: ["hour", "_value"])
'''
    rows = query_one(query_api, forecast_q)
    hour_avg = {int(r.values["hour"]): float(r.get_value()) for r in rows
                if r.get_value() is not None}
    # Fill any missing hours with overall mean
    overall_mean = (sum(hour_avg.values()) / len(hour_avg)) if hour_avg else current_5min
    forecast_by_hour = [hour_avg.get(h, overall_mean) for h in range(24)]

    return {
        "current_5min": current_5min,
        "priceH": priceH,
        "forecast_by_hour": forecast_by_hour,
        "last_ts": last_ts,
    }


def fetch_today_cost_proper(query_api, bucket: str) -> float | None:
    """Proper cost integration: hourly mean power × hourly_avg price, summed over today.

    More accurate than `kwh × current_price` (which can be off 30-50% on a hot day
    with high price variance). Uses Refoss em:1+em:7 mains for power and ComEd
    hourly_avg for price (the rate actually billed).

    Returns None if data is insufficient (e.g., partial day with no overlapping
    hours yet).
    """
    # Note: comed.prices hourly_avg points are written with timestamp = the
    # hour-START. Refoss aggregateWindow defaults to timestamp = window END,
    # so we use timeSrc:"_start" to align the join keys.
    # Also: "today" must mean local-time midnight, not UTC. Otherwise the
    # query window slips a full day around the UTC date boundary.
    q = f'''
import "date"
import "timezone"
option location = timezone.location(name: "America/Chicago")
start = date.truncate(t: now(), unit: 1d)

power_hourly = from(bucket: "{bucket}")
  |> range(start: start)
  |> filter(fn: (r) => r._measurement == "refoss.channel"
                    and r._field == "power_w"
                    and (r.channel == "em:1" or r.channel == "em:7"))
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false, timeSrc: "_start")
  |> pivot(rowKey: ["_time"], columnKey: ["channel"], valueColumn: "_value")
  |> map(fn: (r) => ({{ _time: r._time, _value: (r["em:1"] + r["em:7"]) / 1000.0 }}))

price_hourly = from(bucket: "{bucket}")
  |> range(start: start)
  |> filter(fn: (r) => r._measurement == "comed.prices"
                    and r._field == "price_cents_per_kwh"
                    and r.period_type == "hourly_avg")
  |> aggregateWindow(every: 1h, fn: last, createEmpty: false, timeSrc: "_start")

join(tables: {{ p: power_hourly, c: price_hourly }}, on: ["_time"])
  |> map(fn: (r) => ({{ _time: r._time, _value: r._value_p * r._value_c / 100.0 }}))
  |> sum()
'''
    try:
        rows = query_one(query_api, q)
        if not rows:
            return None
        v = rows[0].get_value()
        return float(v) if v is not None else None
    except Exception:
        return None


def fetch_refoss(query_api, bucket: str) -> dict:
    """Latest per-channel readings (power_w, voltage_v, power_factor,
    day/week/month_energy_kwh)."""
    q = f'''
from(bucket: "{bucket}")
  |> range(start: -2m)
  |> filter(fn: (r) => r._measurement == "refoss.channel")
  |> filter(fn: (r) => r._field == "power_w" or r._field == "voltage_v"
                    or r._field == "power_factor"
                    or r._field == "day_energy_kwh"
                    or r._field == "week_energy_kwh"
                    or r._field == "month_energy_kwh")
  |> last()
'''
    rows = query_one(query_api, q)
    channels: dict[str, dict] = {}
    last_ts = None
    for r in rows:
        ch = r.values.get("channel")
        if not ch:
            continue
        bucket_d = channels.setdefault(ch, {"name": r.values.get("name") or ch})
        bucket_d[r.get_field()] = r.get_value()
        if last_ts is None or (r.get_time() and r.get_time() > last_ts):
            last_ts = r.get_time()

    return {"channels": channels, "last_ts": last_ts}


def age_seconds(ts) -> float | None:
    if ts is None:
        return None
    now = datetime.now(timezone.utc)
    return (now - ts).total_seconds()


def build_response(eagle: dict, comed: dict, refoss: dict) -> dict:
    """Assemble the final JSON in the shape data.jsx expects."""
    now = datetime.now(timezone.utc)
    now_h = now.astimezone().hour  # local hour for forecast/priceH alignment

    # ----- Refoss-derived values -----
    channels = refoss["channels"]
    main_a = channels.get(MAIN_CHANNELS[0]) or {}
    main_b = channels.get(MAIN_CHANNELS[1]) or {}

    voltL1 = float(main_a.get("voltage_v") or 0.0)
    voltL2 = float(main_b.get("voltage_v") or 0.0)
    pf_a = main_a.get("power_factor")
    pf_b = main_b.get("power_factor")
    pfs = [abs(float(p)) for p in (pf_a, pf_b) if p is not None]
    pf_mains = sum(pfs) / len(pfs) if pfs else 0.0

    # Whole-home demand from Refoss mains (fallback to EAGLE if Refoss is stale)
    refoss_total_w = (float(main_a.get("power_w") or 0.0)
                      + float(main_b.get("power_w") or 0.0))
    eagle_w = eagle["demand_kw"] * 1000.0
    # Prefer EAGLE for the headline number (billing-grade), but if it's stale
    # (>2min old) fall back to Refoss.
    eagle_age = age_seconds(eagle["last_ts"])
    current_demand_w = (eagle_w if (eagle_age is not None and eagle_age < 120)
                        else refoss_total_w)

    # Today's energy: sum of mains' day_energy buckets (resets on the device's
    # clock at midnight)
    today_kwh = (float(main_a.get("day_energy_kwh") or 0.0)
                 + float(main_b.get("day_energy_kwh") or 0.0))
    week_kwh = (float(main_a.get("week_energy_kwh") or 0.0)
                + float(main_b.get("week_energy_kwh") or 0.0))
    month_kwh = (float(main_a.get("month_energy_kwh") or 0.0)
                 + float(main_b.get("month_energy_kwh") or 0.0))

    current_price = comed["current_5min"]
    cost_per_hour_dollars = (current_demand_w / 1000.0) * current_price / 100.0
    cost_per_hour = (current_demand_w / 1000.0) * current_price  # ¢/hr

    # Today/week/month cost: proper price-weighted integral across hourly buckets.
    # Falls back to approximation (kWh × current price) if integral query fails
    # or returns zero (e.g., insufficient data early on).
    today_cost = today_cost_approx = today_kwh * current_price / 100.0
    today_cost_proper = comed.get("today_cost_proper")
    if today_cost_proper is not None and today_cost_proper > 0:
        today_cost = today_cost_proper

    # ----- Devices list (every Refoss channel, sorted by power) -----
    STANDBY_W = 5.0
    devices = []
    for ch_id, c in channels.items():
        if ch_id in MAIN_CHANNELS:
            continue
        power = float(c.get("power_w") or 0.0)
        devices.append({
            "name": c.get("name", ch_id),
            "power": abs(power),  # CT polarity quirks shouldn't show as negative
            "on": abs(power) >= STANDBY_W,
            "category": _category_for(c.get("name", ch_id)),
            "channel": ch_id,
        })
    devices.sort(key=lambda d: -d["power"])

    # Top non-mains circuit (replaces the dropped Solar tile)
    top_circuit = next((d for d in devices if d["on"]), None) or {
        "name": "—", "power": 0.0
    }

    # ----- Power Flow clusters (matches design's 4-node bottom row) -----
    cluster_w = {
        "hvac": _sum_devices_in_category(devices, "hvac"),
        "fridge": _sum_devices_in_category(devices, "fridge"),
        "living": _sum_devices_in_category(devices, "living"),
        "bedrooms": _sum_devices_in_category(devices, "bedrooms"),
    }

    # ----- ComEd 12h forecast strip (next 12 hours, hour-of-day avg) -----
    forecast = []
    for i in range(12):
        h = (now_h + i) % 24
        forecast.append({
            "hour": h,
            "price": comed["forecast_by_hour"][h],
            "isNow": i == 0,
        })

    # ----- Activity log (derived from real time-series) -----
    log_entries = _build_activity_log(eagle, comed, refoss, current_demand_w,
                                      current_price, top_circuit)

    # ----- System health for the topbar status pills -----
    # Staleness thresholds reflect each source's natural cadence:
    #   * EAGLE polls every 30s, 5-min point = anomaly  -> warn at 2 min
    #   * Refoss polls every 30s                        -> warn at 2 min
    #   * ComEd 5-min data is timestamped at the ComEd-side interval start and
    #     published with a 5-10 min lag, so the most recent _time is always
    #     5-15 min behind clock time even on a healthy poller. Warn at 20 min.
    system_health = {
        "comed":  _health(comed["last_ts"], stale_after_s=20 * 60),
        "eagle":  _health(eagle["last_ts"], stale_after_s=120),
        "refoss": _health(refoss["last_ts"], stale_after_s=120),
        "tcc":    {"state": "idle", "ageSeconds": None},  # Phase 4 placeholder
    }

    return {
        # Hero / center ring
        "currentDemand": current_demand_w,
        "currentPrice": current_price,
        "costPerHour": cost_per_hour,
        "costPerHourDollars": cost_per_hour_dollars,

        # Hero L (price strip mini-bars use last 12h of priceH)
        "priceH": comed["priceH"],

        # Hero R (24h chart)
        "demandH": eagle["demandH_w"],
        "forecast": forecast,

        # Left rail
        "devices": devices,
        "voltL1": voltL1,
        "voltL2": voltL2,
        "pf": pf_mains,  # replaces freq

        # Climate -- mock until Phase 4 TCC wiring
        "indoor": 70.5,
        "outdoor": 52.0,
        "hvacMode": "—",
        "setpoint": 72.0,

        # Bottom strip
        "todayKwh": today_kwh,
        "todayCost": today_cost,
        "weekKwh": week_kwh,
        "weekCost": week_kwh * current_price / 100.0,  # rough
        "monthKwh": month_kwh,
        "monthCost": month_kwh * current_price / 100.0,
        "monthDay": now.astimezone().day,
        "topCircuit": {"name": top_circuit["name"], "power_w": top_circuit["power"]},
        "eagleSummation": eagle["delivered_kwh"],
        "eagleSummationAgeS": age_seconds(eagle["last_ts"]),

        # Right rail
        "log": log_entries,
        "powerFlow": {
            "grid_w": current_demand_w,  # net (no solar; same as demand)
            "home_w": current_demand_w,
            "clusters": cluster_w,
        },

        # Topbar
        "systemHealth": system_health,
    }


def _category_for(name: str) -> str:
    """Map a Refoss channel label to a power-flow cluster."""
    n = (name or "").lower()
    if "ac " in n or "air conditioning" in n or "furnace" in n or "heat" in n:
        return "hvac"
    if "fridge" in n or "freezer" in n:
        return "fridge"
    if any(k in n for k in ("family", "bar", "av rack", "dining", "kitchen")):
        return "living"
    if any(k in n for k in ("bedroom", "primary bath", "washer", "dryer")):
        return "bedrooms"
    return "other"


def _sum_devices_in_category(devices: list, category: str) -> float:
    return sum(d["power"] for d in devices if d["category"] == category and d["on"])


def _health(last_ts, stale_after_s: float) -> dict:
    age = age_seconds(last_ts)
    if age is None:
        return {"state": "idle", "ageSeconds": None}
    if age > stale_after_s:
        return {"state": "warn", "ageSeconds": age}
    return {"state": "ok", "ageSeconds": age}


def _build_activity_log(eagle, comed, refoss, demand_w, price, top_circuit):
    """Synthesize an activity feed from the latest known facts. Keeps the
    visual interesting without inventing fake events -- every line maps to a
    real value in the time-series."""
    entries = []
    eagle_age = age_seconds(eagle["last_ts"])
    if eagle_age is not None:
        entries.append({
            "dt": int(eagle_age),
            "tag": "ok",
            "msg": f"EAGLE-3 sync · {eagle['delivered_kwh']:.2f} kWh delivered",
        })
    comed_age = age_seconds(comed["last_ts"])
    if comed_age is not None:
        tier = "ELEVATED" if price > 7 else "NORMAL" if price > 3 else "LOW"
        entries.append({
            "dt": int(comed_age),
            "tag": "warn" if price > 7 else "info",
            "msg": f"ComEd 5-min · {price:.2f}¢/kWh · {tier}",
        })
    refoss_age = age_seconds(refoss["last_ts"])
    if refoss_age is not None:
        entries.append({
            "dt": int(refoss_age),
            "tag": "info",
            "msg": f"Refoss · {len(refoss['channels'])} channels reporting",
        })
    if top_circuit and top_circuit.get("power", 0) > 100:
        entries.append({
            "dt": int(refoss_age or 0),
            "tag": "info",
            "msg": f"Top draw · {top_circuit['name']} · {top_circuit['power']:.0f} W",
        })
    if demand_w > 5000:
        entries.append({"dt": 0, "tag": "warn",
                        "msg": f"Demand · {demand_w/1000:.2f} kW (>5 kW)"})
    elif demand_w > 3000:
        entries.append({"dt": 0, "tag": "info",
                        "msg": f"Demand · {demand_w/1000:.2f} kW"})
    return sorted(entries, key=lambda e: e["dt"])


# ---- HTTP server -----------------------------------------------------------

class Cache:
    def __init__(self, ttl_s: float):
        self.ttl_s = ttl_s
        self.data = None
        self.fetched_at = 0.0

    def fresh(self) -> bool:
        return self.data is not None and (time.monotonic() - self.fetched_at) < self.ttl_s

    def store(self, data: dict) -> None:
        self.data = data
        self.fetched_at = time.monotonic()


async def handle_energy(request: web.Request) -> web.Response:
    cfg: Config = request.app["cfg"]
    cache: Cache = request.app["cache"]
    if cache.fresh():
        return web.json_response(cache.data, headers={"Cache-Control": "no-store"})
    try:
        client: InfluxDBClient = request.app["influx"]
        query_api = client.query_api()
        eagle = fetch_eagle(query_api, cfg.influx_bucket)
        comed = fetch_comed(query_api, cfg.influx_bucket)
        comed["today_cost_proper"] = fetch_today_cost_proper(query_api, cfg.influx_bucket)
        refoss = fetch_refoss(query_api, cfg.influx_bucket)
        data = build_response(eagle, comed, refoss)
        cache.store(data)
        log("info", "energy_ok",
            demand_w=round(data["currentDemand"], 1),
            price=round(data["currentPrice"], 2),
            channels=len(data["devices"]) + 2)
        return web.json_response(data, headers={"Cache-Control": "no-store"})
    except Exception as exc:
        log("error", "energy_failed", error=str(exc), error_type=type(exc).__name__)
        # If we have stale cached data, serve it rather than 500ing the dashboard.
        if cache.data is not None:
            return web.json_response(cache.data,
                                     headers={"Cache-Control": "no-store",
                                              "X-Cache-Status": "stale-on-error"})
        return web.json_response({"error": str(exc)}, status=500)


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


def main() -> int:
    cfg = Config.from_env()
    log("info", "startup", influx_url=cfg.influx_url, bucket=cfg.influx_bucket,
        port=cfg.api_port, cache_ttl_s=cfg.cache_ttl_s)
    influx = InfluxDBClient(url=cfg.influx_url, token=cfg.influx_token,
                            org=cfg.influx_org)
    app = web.Application()
    app["cfg"] = cfg
    app["cache"] = Cache(cfg.cache_ttl_s)
    app["influx"] = influx
    app.router.add_get("/api/energy", handle_energy)
    app.router.add_get("/api/health", handle_health)
    web.run_app(app, host="0.0.0.0", port=cfg.api_port, print=None,
                access_log=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
