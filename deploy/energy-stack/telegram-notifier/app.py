"""Telegram notifier for energy-stack.

Two background tasks:
  1. Daily summary -- once a day at SUMMARY_HOUR (default 08:00 local), sends
     a formatted summary message: yesterday's usage, today's stats so far,
     HVAC scheduler activity, tomorrow's forecast.
  2. Alert checker -- runs every ALERT_CHECK_INTERVAL_S (default 300s), checks:
     - Any poller silent > POLLER_SILENT_MIN minutes
     - ComEd 5-min price spike > PRICE_SPIKE_THRESHOLD_C cents
     - Latest hvac.actions has applied=false with a non-skip error
     Dedupe: same alert won't re-fire within ALERT_DEDUPE_MIN.

Reuses the Life Engine Telegram bot (same token + chat_id), so messages land in
the same channel Chris already uses.

Environment variables:
    TELEGRAM_BOT_TOKEN          Bot token (from BotFather)
    TELEGRAM_CHAT_ID            Chat to send to (numeric ID, possibly negative for groups)
    SUMMARY_HOUR                Hour-of-day to send daily summary (default 8 = 8 AM)
    ALERT_CHECK_INTERVAL_S      Seconds between alert checks (default 300)
    POLLER_SILENT_MIN           Min minutes before flagging a silent poller (default 10)
    PRICE_SPIKE_THRESHOLD_C     Cents/kWh above which to alert (default 20)
    ALERT_DEDUPE_MIN            Don't repeat same alert within N min (default 30)
    SCHEDULER_TZ                IANA tz (default America/Chicago)
    INFLUXDB_URL / TOKEN / ORG / BUCKET
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
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
from influxdb_client import InfluxDBClient


def log(level: str, msg: str, **fields: Any) -> None:
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "level": level, "msg": msg}
    rec.update(fields)
    print(json.dumps(rec, default=str), flush=True)


@dataclass(frozen=True)
class Config:
    bot_token: str
    chat_id: str
    summary_hour: int
    alert_check_interval_s: int
    poller_silent_min: int
    price_spike_threshold_c: float
    alert_dedupe_min: int
    tz_name: str
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
            bot_token=required("TELEGRAM_BOT_TOKEN"),
            chat_id=required("TELEGRAM_CHAT_ID"),
            summary_hour=int(os.environ.get("SUMMARY_HOUR", "8")),
            alert_check_interval_s=int(os.environ.get("ALERT_CHECK_INTERVAL_S", "300")),
            poller_silent_min=int(os.environ.get("POLLER_SILENT_MIN", "10")),
            price_spike_threshold_c=float(os.environ.get("PRICE_SPIKE_THRESHOLD_C", "20")),
            alert_dedupe_min=int(os.environ.get("ALERT_DEDUPE_MIN", "30")),
            tz_name=os.environ.get("SCHEDULER_TZ", "America/Chicago"),
            influx_url=os.environ.get("INFLUXDB_URL", "http://influxdb:8086"),
            influx_token=required("INFLUXDB_TOKEN"),
            influx_org=required("INFLUXDB_ORG"),
            influx_bucket=required("INFLUXDB_BUCKET"),
        )


# ---- Telegram client -------------------------------------------------------

async def send_telegram(cfg: Config, text: str, parse_mode: str = "HTML") -> bool:
    url = f"https://api.telegram.org/bot{cfg.bot_token}/sendMessage"
    payload = {"chat_id": cfg.chat_id, "text": text, "parse_mode": parse_mode,
               "disable_web_page_preview": True}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload,
                                     timeout=aiohttp.ClientTimeout(total=15)) as r:
                ok = r.status == 200
                if not ok:
                    body = await r.text()
                    log("warn", "telegram_send_failed", status=r.status, body=body[:300])
                return ok
    except Exception as exc:
        log("warn", "telegram_send_error", error=str(exc), error_type=type(exc).__name__)
        return False


# ---- Influx queries --------------------------------------------------------

def fetch_one(query_api, flux: str) -> list[dict]:
    out = []
    for table in query_api.query(flux):
        for r in table.records:
            out.append(r.values)
    return out


def cost_proper_for_window(query_api, bucket: str, start: str, end: str) -> float | None:
    """Hour-by-hour price-weighted cost: sum over hours of (mean_kw × hourly_avg_price_c) / 100.

    This is the honest answer vs `total_kwh × avg_price`. The approximation
    can be off 30-50% when load is concentrated in expensive hours.
    """
    # Use timeSrc:"_start" on aggregateWindow so the join keys align with
    # comed.prices hourly_avg (which is written at hour-start timestamp).
    flux = f'''
power_hourly = from(bucket: "{bucket}")
  |> range(start: {start}, stop: {end})
  |> filter(fn: (r) => r._measurement == "refoss.channel"
                    and r._field == "power_w"
                    and (r.channel == "em:1" or r.channel == "em:7"))
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false, timeSrc: "_start")
  |> pivot(rowKey: ["_time"], columnKey: ["channel"], valueColumn: "_value")
  |> map(fn: (r) => ({{ _time: r._time, _value: (r["em:1"] + r["em:7"]) / 1000.0 }}))

price_hourly = from(bucket: "{bucket}")
  |> range(start: {start}, stop: {end})
  |> filter(fn: (r) => r._measurement == "comed.prices"
                    and r._field == "price_cents_per_kwh"
                    and r.period_type == "hourly_avg")
  |> aggregateWindow(every: 1h, fn: last, createEmpty: false, timeSrc: "_start")

join(tables: {{ p: power_hourly, c: price_hourly }}, on: ["_time"])
  |> map(fn: (r) => ({{ _time: r._time, _value: r._value_p * r._value_c / 100.0 }}))
  |> sum()
'''
    try:
        rows = fetch_one(query_api, flux)
        if not rows:
            return None
        v = rows[0].get("_value")
        return float(v) if v is not None else None
    except Exception:
        return None


def yesterday_stats(query_api, bucket: str, tz: ZoneInfo) -> dict:
    """Yesterday's total kWh + cost + top circuits."""
    now_local = datetime.now(tz)
    start_local = (now_local - timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    # Convert to UTC ISO for Flux
    start = start_local.astimezone(timezone.utc).isoformat()
    end = end_local.astimezone(timezone.utc).isoformat()

    # Whole-home kWh from Refoss mains (em:1 + em:7), integrated power
    flux_kwh = f'''
from(bucket: "{bucket}")
  |> range(start: {start}, stop: {end})
  |> filter(fn: (r) => r._measurement == "refoss.channel"
                    and r._field == "power_w"
                    and (r.channel == "em:1" or r.channel == "em:7"))
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> integral(unit: 1h)
  |> sum()
'''
    rows = fetch_one(query_api, flux_kwh)
    # integral over Wh basis -> /1000 for kWh
    total_kwh = (rows[0].get("_value") / 1000.0) if rows and rows[0].get("_value") else 0.0

    # Average ComEd hourly_avg yesterday for reference
    flux_avg_price = f'''
from(bucket: "{bucket}")
  |> range(start: {start}, stop: {end})
  |> filter(fn: (r) => r._measurement == "comed.prices"
                    and r._field == "price_cents_per_kwh"
                    and r.period_type == "hourly_avg")
  |> mean()
'''
    rows = fetch_one(query_api, flux_avg_price)
    avg_price_c = (rows[0].get("_value") if rows and rows[0].get("_value") else 0.0) or 0.0

    # Proper cost: hour-by-hour integration. Falls back to avg_price approx if
    # the join produces nothing (e.g., partial-day or missing comed data).
    cost_proper = cost_proper_for_window(query_api, bucket, start, end)
    total_cost = cost_proper if (cost_proper is not None and cost_proper > 0) else (total_kwh * avg_price_c / 100.0)

    # Top 3 circuits by integrated kWh (excluding mains)
    flux_top = f'''
from(bucket: "{bucket}")
  |> range(start: {start}, stop: {end})
  |> filter(fn: (r) => r._measurement == "refoss.channel"
                    and r._field == "power_w"
                    and r.channel != "em:1" and r.channel != "em:7")
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> integral(unit: 1h, column: "_value")
  |> group(columns: ["channel", "name"])
  |> sum()
  |> group()
  |> sort(columns: ["_value"], desc: true)
  |> limit(n: 5)
'''
    rows = fetch_one(query_api, flux_top)
    top_circuits = [
        {"name": r.get("name") or r.get("channel"), "wh": r.get("_value") or 0}
        for r in rows
    ]

    return {
        "total_kwh": total_kwh,
        "total_cost": total_cost,
        "avg_price_c": avg_price_c,
        "top_circuits": top_circuits,
        "date": start_local.date().isoformat(),
    }


def today_so_far(query_api, bucket: str, tz: ZoneInfo) -> dict:
    """Today's running totals + current state."""
    now_local = datetime.now(tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    start = start_local.astimezone(timezone.utc).isoformat()

    # Current power (latest Refoss em:1 + em:7)
    flux_now = f'''
from(bucket: "{bucket}")
  |> range(start: -2m)
  |> filter(fn: (r) => r._measurement == "refoss.channel"
                    and r._field == "power_w"
                    and (r.channel == "em:1" or r.channel == "em:7"))
  |> last()
  |> sum()
'''
    rows = fetch_one(query_api, flux_now)
    current_w = (rows[0].get("_value") if rows else 0.0) or 0.0

    # Today's kWh integrated
    flux_today = f'''
from(bucket: "{bucket}")
  |> range(start: {start})
  |> filter(fn: (r) => r._measurement == "refoss.channel"
                    and r._field == "power_w"
                    and (r.channel == "em:1" or r.channel == "em:7"))
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> integral(unit: 1h)
  |> sum()
'''
    rows = fetch_one(query_api, flux_today)
    today_wh = (rows[0].get("_value") if rows else 0.0) or 0.0
    today_kwh = today_wh / 1000.0

    # Latest ComEd 5-min
    flux_price = f'''
from(bucket: "{bucket}")
  |> range(start: -30m)
  |> filter(fn: (r) => r._measurement == "comed.prices"
                    and r._field == "price_cents_per_kwh"
                    and r.period_type == "5min")
  |> last()
'''
    rows = fetch_one(query_api, flux_price)
    current_price_c = (rows[0].get("_value") if rows else 0.0) or 0.0

    # Proper today cost: hour-by-hour price-weighted integral.
    # Fallback to approximation if integration query returns nothing.
    end_now = datetime.now(timezone.utc).isoformat()
    cost_proper = cost_proper_for_window(query_api, bucket, start, end_now)
    today_cost = cost_proper if (cost_proper is not None and cost_proper > 0) else (today_kwh * current_price_c / 100.0)

    return {
        "current_w": current_w,
        "today_kwh": today_kwh,
        "today_cost": today_cost,
        "current_price_c": current_price_c,
    }


def hvac_today_state(query_api, bucket: str, tz: ZoneInfo) -> dict:
    """Today's day-type decision + last action + next action."""
    today_iso = datetime.now(tz).date().isoformat()
    flux = f'''
from(bucket: "{bucket}")
  |> range(start: -36h)
  |> filter(fn: (r) => r._measurement == "hvac.decisions"
                    and r.decision_for_date == "{today_iso}")
  |> last()
'''
    rows = fetch_one(query_api, flux)
    day_type = rows[0].get("day_type") if rows else "unknown"

    # Last hvac.action today
    flux_actions = f'''
from(bucket: "{bucket}")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "hvac.actions")
  |> last()
'''
    rows = fetch_one(query_api, flux_actions)
    last_action = rows[0] if rows else {}

    return {
        "day_type": day_type,
        "last_action_label": last_action.get("action_label", ""),
        "last_action_time": last_action.get("_time"),
    }


def tomorrow_forecast(query_api, bucket: str) -> dict:
    flux = f'''
from(bucket: "{bucket}")
  |> range(start: -3h)
  |> filter(fn: (r) => r._measurement == "nws.forecast" and r.for_period == "tomorrow")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
'''
    rows = fetch_one(query_api, flux)
    if not rows:
        return {}
    return rows[0]


def poller_last_writes(query_api, bucket: str) -> dict:
    """For each major measurement, return the last-write timestamp (UTC)."""
    measurements = [
        ("comed.prices", "comed-poller"),
        ("eagle.meter", "eagle-poller"),
        ("refoss.channel", "refoss-poller"),
        ("nws.forecast", "nws-poller"),
    ]
    out = {}
    for meas, name in measurements:
        flux = f'''
from(bucket: "{bucket}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "{meas}")
  |> last()
  |> keep(columns: ["_time"])
'''
        rows = fetch_one(query_api, flux)
        out[name] = rows[0].get("_time") if rows else None
    return out


# ---- Daily summary ---------------------------------------------------------

def build_daily_summary(query_api, bucket: str, tz: ZoneInfo) -> str:
    now_local = datetime.now(tz)
    yest = yesterday_stats(query_api, bucket, tz)
    today = today_so_far(query_api, bucket, tz)
    hvac = hvac_today_state(query_api, bucket, tz)
    tomorrow = tomorrow_forecast(query_api, bucket)

    lines = []
    lines.append(f"<b>🏠 dePaola Home — Daily Summary</b>")
    lines.append(f"<i>{now_local.strftime('%a %b %d, %Y')}</i>")
    lines.append("")
    lines.append(f"<b>⚡ Yesterday ({yest['date']})</b>")
    lines.append(f"  • Used: {yest['total_kwh']:.1f} kWh · ${yest['total_cost']:.2f}")
    lines.append(f"  • Avg price: {yest['avg_price_c']:.2f}¢/kWh")
    if yest["top_circuits"]:
        lines.append(f"  • Top circuits:")
        for c in yest["top_circuits"][:3]:
            kwh = c["wh"] / 1000.0
            lines.append(f"      {c['name']}: {kwh:.1f} kWh")
    lines.append("")
    lines.append(f"<b>📊 Today so far</b>")
    lines.append(f"  • Used: {today['today_kwh']:.1f} kWh · ${today['today_cost']:.2f}")
    lines.append(f"  • Current draw: {today['current_w']/1000:.2f} kW")
    tier = "ELEVATED" if today["current_price_c"] > 7 else "NORMAL" if today["current_price_c"] > 3 else "LOW"
    lines.append(f"  • ComEd 5-min: {today['current_price_c']:.2f}¢/kWh ({tier})")
    lines.append("")
    lines.append(f"<b>🌡️ HVAC</b>")
    lines.append(f"  • Today's day-type: {hvac['day_type']}")
    if hvac["last_action_label"]:
        lines.append(f"  • Last action: {hvac['last_action_label']}")
    lines.append("")
    if tomorrow:
        high = tomorrow.get("high_f")
        dp = tomorrow.get("max_dewpoint_f")
        is_heat = bool(tomorrow.get("is_heat_advisory", 0))
        lines.append(f"<b>📅 Tomorrow forecast</b>")
        if high is not None:
            lines.append(f"  • High: {high:.0f}°F  Dewpoint: {dp:.0f}°F")
        if is_heat:
            lines.append(f"  • ⚠️ Heat Advisory active")
    return "\n".join(lines)


# ---- Alerts ----------------------------------------------------------------

@dataclass(frozen=True)
class Alert:
    key: str   # dedupe key
    text: str  # message body


def check_poller_silence(query_api, bucket: str, threshold_min: int) -> list[Alert]:
    last_writes = poller_last_writes(query_api, bucket)
    now = datetime.now(timezone.utc)
    out = []
    for poller, last_ts in last_writes.items():
        if last_ts is None:
            out.append(Alert(
                key=f"silent:{poller}",
                text=f"⚠️ <b>{poller}</b> has no data in the last hour."
            ))
            continue
        # last_ts is a datetime
        age_min = (now - last_ts).total_seconds() / 60
        # Poller-specific tolerances: comed naturally lags 5-15 min; nws polls every 30 min.
        tol = {"comed-poller": 25, "nws-poller": 45, "refoss-poller": threshold_min,
                "eagle-poller": threshold_min}.get(poller, threshold_min)
        if age_min > tol:
            out.append(Alert(
                key=f"silent:{poller}",
                text=f"⚠️ <b>{poller}</b> silent for {age_min:.0f} min (tolerance {tol} min)."
            ))
    return out


def check_price_spike(query_api, bucket: str, threshold_c: float) -> list[Alert]:
    flux = f'''
from(bucket: "{bucket}")
  |> range(start: -15m)
  |> filter(fn: (r) => r._measurement == "comed.prices"
                    and r._field == "price_cents_per_kwh"
                    and r.period_type == "5min")
  |> last()
'''
    rows = fetch_one(query_api, flux)
    if not rows:
        return []
    price = rows[0].get("_value")
    if price is not None and price >= threshold_c:
        return [Alert(
            key=f"price_spike:{int(price)}",
            text=f"⚡ <b>ComEd price spike: {price:.1f}¢/kWh</b>\nDefer big loads if possible."
        )]
    return []


def check_fridge_anomalies(query_api, bucket: str) -> list[Alert]:
    """Detect fridge/freezer circuits running significantly above their 14-day
    baseline. Common causes: door left open, compressor failure, defrost cycle
    stuck, refrigerant leak, blocked condenser/vents.

    Compares last 6h average power to last-14-days average (excluding the most
    recent 6h). Alert fires when recent > 1.5× baseline AND absolute jump > 50W.
    """
    flux = f'''
baseline = from(bucket: "{bucket}")
  |> range(start: -14d, stop: -6h)
  |> filter(fn: (r) => r._measurement == "refoss.channel"
                    and r._field == "power_w"
                    and r.name =~ /(?i)fridge|freezer/)
  |> group(columns: ["channel", "name"])
  |> mean()
  |> set(key: "_field", value: "baseline_w")

recent = from(bucket: "{bucket}")
  |> range(start: -6h)
  |> filter(fn: (r) => r._measurement == "refoss.channel"
                    and r._field == "power_w"
                    and r.name =~ /(?i)fridge|freezer/)
  |> group(columns: ["channel", "name"])
  |> mean()
  |> set(key: "_field", value: "recent_w")

union(tables: [baseline, recent])
  |> pivot(rowKey: ["channel", "name"], columnKey: ["_field"], valueColumn: "_value")
'''
    try:
        rows = fetch_one(query_api, flux)
    except Exception as exc:
        log("warn", "fridge_check_failed", error=str(exc))
        return []

    alerts: list[Alert] = []
    for r in rows:
        baseline = r.get("baseline_w")
        recent = r.get("recent_w")
        name = r.get("name") or r.get("channel") or "Unknown fridge"
        channel = r.get("channel", "?")
        if baseline is None or recent is None:
            continue
        # Skip offline fridges (baseline <5W means not really running)
        if baseline < 5:
            continue
        ratio = recent / baseline
        delta = recent - baseline
        if ratio > 1.5 and delta > 50:
            alerts.append(Alert(
                key=f"fridge_anomaly:{channel}",
                text=(
                    f"❄️ <b>{name}</b> ({channel}) running "
                    f"<b>{ratio:.1f}× baseline</b>\n"
                    f"  • Last 6h avg: {recent:.0f} W\n"
                    f"  • 14-day baseline: {baseline:.0f} W\n"
                    f"  • Excess: +{delta:.0f} W\n"
                    f"  Possible: door open, compressor strain, blocked vents, defrost stuck."
                )
            ))
    return alerts


def check_hvac_action_errors(query_api, bucket: str) -> list[Alert]:
    flux = f'''
from(bucket: "{bucket}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "hvac.actions" and r._field == "error")
  |> filter(fn: (r) => r._value != "" and r._value != "hvac_mode_not_cooling ('Heat')"
                    and r._value != "hvac_mode_not_cooling ('Off')")
  |> last()
'''
    rows = fetch_one(query_api, flux)
    if not rows:
        return []
    err = rows[0].get("_value")
    if err:
        return [Alert(
            key=f"hvac_error:{err[:40]}",
            text=f"🌡️ <b>HVAC scheduler error</b>\n<code>{err}</code>"
        )]
    return []


# ---- Main loop -------------------------------------------------------------

async def daily_summary_loop(cfg: Config, query_api, tz: ZoneInfo, stop: asyncio.Event):
    log("info", "daily_summary_loop_starting", summary_hour=cfg.summary_hour)
    while not stop.is_set():
        now_local = datetime.now(tz)
        next_run = now_local.replace(hour=cfg.summary_hour, minute=0, second=0, microsecond=0)
        if next_run <= now_local:
            next_run += timedelta(days=1)
        sleep_s = (next_run - now_local).total_seconds()
        log("info", "daily_summary_sleep_until", next_run=next_run.isoformat(), sleep_s=int(sleep_s))
        try:
            await asyncio.wait_for(stop.wait(), timeout=sleep_s)
            return  # stop fired
        except asyncio.TimeoutError:
            pass
        # Fire summary
        try:
            text = build_daily_summary(query_api, cfg.influx_bucket, tz)
            ok = await send_telegram(cfg, text)
            log("info", "daily_summary_sent", ok=ok)
        except Exception as exc:
            log("error", "daily_summary_failed", error=str(exc), error_type=type(exc).__name__)


async def alert_loop(cfg: Config, query_api, stop: asyncio.Event):
    from pathlib import Path
    health_marker = Path("/tmp/last_tick_ok")
    log("info", "alert_loop_starting", interval_s=cfg.alert_check_interval_s)
    last_sent: dict[str, datetime] = {}
    while not stop.is_set():
        try:
            alerts: list[Alert] = []
            alerts.extend(check_poller_silence(query_api, cfg.influx_bucket, cfg.poller_silent_min))
            alerts.extend(check_price_spike(query_api, cfg.influx_bucket, cfg.price_spike_threshold_c))
            alerts.extend(check_hvac_action_errors(query_api, cfg.influx_bucket))
            alerts.extend(check_fridge_anomalies(query_api, cfg.influx_bucket))
            now = datetime.now(timezone.utc)
            for a in alerts:
                if a.key in last_sent and (now - last_sent[a.key]).total_seconds() < cfg.alert_dedupe_min * 60:
                    continue
                ok = await send_telegram(cfg, a.text)
                if ok:
                    last_sent[a.key] = now
                    log("info", "alert_sent", key=a.key)
        except Exception as exc:
            log("error", "alert_check_failed", error=str(exc), error_type=type(exc).__name__)
        try:
            health_marker.touch()
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=cfg.alert_check_interval_s)
            return
        except asyncio.TimeoutError:
            pass


async def main_async(cfg: Config) -> int:
    tz = ZoneInfo(cfg.tz_name)
    log("info", "startup",
        summary_hour=cfg.summary_hour,
        alert_check_interval_s=cfg.alert_check_interval_s,
        poller_silent_min=cfg.poller_silent_min,
        price_spike_threshold_c=cfg.price_spike_threshold_c,
        chat_id=cfg.chat_id)

    influx = InfluxDBClient(url=cfg.influx_url, token=cfg.influx_token, org=cfg.influx_org)
    query_api = influx.query_api()

    # Send startup ping so we know the channel works
    await send_telegram(cfg, "🟢 <b>energy-stack telegram-notifier</b> started.")

    stop = asyncio.Event()

    def handle_stop(signum, _frame):
        log("info", "signal_received", signum=signum)
        stop.set()
    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    await asyncio.gather(
        daily_summary_loop(cfg, query_api, tz, stop),
        alert_loop(cfg, query_api, stop),
    )

    log("info", "shutdown")
    influx.close()
    return 0


def main() -> int:
    cfg = Config.from_env()
    return asyncio.run(main_async(cfg))


if __name__ == "__main__":
    sys.exit(main())
