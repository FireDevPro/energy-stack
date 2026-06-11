"""Day-at-a-glance payload builder.

Assembles the chart data the narrative-cockpit `DayAtAGlance` component
renders: 24 hourly price bars (DA forecast + realized overlay), an
indoor temperature series (past only), an actual setpoint series (past
only), and the planned future setpoint actions for the current
day_type.

Pure function: I/O is the caller's job (app.py wires it to real query
functions). Tests pass synthetic inputs and assert the assembled
output.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .schedules import (
    PlannedAction,
    planned_setpoints_for_day,
)


CT = ZoneInfo("America/Chicago")


def build_day_at_a_glance_live(
    *,
    now: datetime,
    day_type: str | None,
    thermostat_history: list[dict[str, Any]],
    realized_hourly: list[dict[str, Any]],
    da_forecast: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the day_at_a_glance payload from live query results.

    Inputs:
      - now: tz-aware datetime (any zone; converted to CT for hour
        bucketing).
      - day_type: today's winning day type (NORMAL / HOT_5CP_RISK /
        MILD / HOT_STREAK_DAY1). None falls back to NORMAL.
      - thermostat_history: rows from
        influx.query_thermostat_history({indoor, cool, heat}_setpoint_f).
        Sorted ascending by ts.
      - realized_hourly: rows from influx.query_hourly_avg_prices.
        Each row keyed by hour-bucket UTC.
      - da_forecast: rows from influx.query_da_lmp_forecast (already
        converted to ¢/kWh).
    """
    now_ct = now.astimezone(CT)
    today_ct = now_ct.replace(hour=0, minute=0, second=0, microsecond=0)

    indoor = _indoor_series(thermostat_history)
    setpoint_past = _setpoint_history_series(thermostat_history)
    setpoint_planned = _planned_setpoints(day_type)
    bars = _hourly_bars(today_ct, realized_hourly, da_forecast)

    return {
        "now": now_ct.isoformat(),
        "day_type": day_type or "NORMAL",
        "indoor_history": indoor,
        "setpoint_history": setpoint_past,
        "setpoint_planned": setpoint_planned,
        "bars": bars,
    }


def _indoor_series(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        v = row.get("indoor_temp_f")
        ts = row.get("ts")
        if v is None or ts is None:
            continue
        out.append({"ts": ts, "temp_f": float(v)})
    return out


def _setpoint_history_series(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        cool = row.get("cool_setpoint_f")
        ts = row.get("ts")
        if cool is None or ts is None:
            continue
        out.append({"ts": ts, "cool_f": int(cool)})
    return out


def _planned_setpoints(day_type: str | None) -> list[dict[str, Any]]:
    actions: list[PlannedAction] = planned_setpoints_for_day(day_type)
    return [asdict(a) for a in actions]


def _hourly_bars(
    today_ct_start: datetime,
    realized_hourly: list[dict[str, Any]],
    da_forecast: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build 24 bars indexed by CT hour (0..23) for today.

    Each bar: {hour, forecast_cents, realized_cents}.
    realized_cents is None for hours not yet reported.
    forecast_cents is None for hours with no DA publish (rare —
    end-of-day boundary for tomorrow before PJM posts).
    """
    forecast_by_hour: dict[int, float] = {}
    for row in da_forecast:
        ts_iso = row.get("ts")
        if not ts_iso:
            continue
        try:
            ts = datetime.fromisoformat(str(ts_iso))
        except ValueError:
            continue
        ts_ct = ts.astimezone(CT)
        if ts_ct.date() != today_ct_start.date():
            continue
        val = row.get("cents_per_kwh")
        if val is not None:
            forecast_by_hour[ts_ct.hour] = float(val)

    realized_by_hour: dict[int, float] = {}
    for row in realized_hourly:
        ts_iso = row.get("ts")
        if not ts_iso:
            continue
        try:
            ts = datetime.fromisoformat(str(ts_iso))
        except ValueError:
            continue
        ts_ct = ts.astimezone(CT)
        if ts_ct.date() != today_ct_start.date():
            continue
        val = row.get("cents_per_kwh")
        if val is not None:
            realized_by_hour[ts_ct.hour] = float(val)

    return [
        {
            "hour": h,
            "forecast_cents": forecast_by_hour.get(h),
            "realized_cents": realized_by_hour.get(h),
        }
        for h in range(24)
    ]


# ---- Canned payload --------------------------------------------------


def build_day_at_a_glance_canned() -> dict[str, Any]:
    """Return a synthetic day_at_a_glance for canned-mode dev / fixture
    consumers. Anchored to a mid-summer afternoon similar to the
    summer_normal snapshot fixture."""
    now_ct = datetime(2026, 7, 14, 13, 0, 30, tzinfo=CT)
    today_start = now_ct.replace(hour=0, minute=0, second=0, microsecond=0)

    # Synthetic indoor + setpoint history every 15 min for the prior 6h
    # (matches the existing summer_normal fixture's 24-sample sparkline).
    indoor_samples = [
        76.4, 76.2, 76.1, 76.0, 75.8, 75.7, 75.6, 75.6,
        75.4, 75.3, 75.2, 75.0, 74.9, 74.9, 74.8, 74.7,
        74.7, 74.8, 74.8, 74.7, 74.8, 74.7, 74.8, 74.8,
    ]
    indoor_history = [
        {
            "ts": (now_ct - timedelta(minutes=15 * (len(indoor_samples) - 1 - i))).isoformat(),
            "temp_f": v,
        }
        for i, v in enumerate(indoor_samples)
    ]

    # Setpoint history: 70 (PRE_COOL) -> 79 (COAST started at 13:00).
    setpoint_history: list[dict[str, Any]] = []
    for i in range(len(indoor_samples)):
        ts = now_ct - timedelta(minutes=15 * (len(indoor_samples) - 1 - i))
        if ts.hour < 13:
            cool = 70
        else:
            cool = 79
        setpoint_history.append({"ts": ts.isoformat(), "cool_f": cool})

    # 24h forecast — flat mild day with a small evening peak.
    forecast_curve = [
        5.2, 4.8, 4.6, 4.7, 5.1, 6.2,  # 0-5
        7.4, 7.8, 8.1, 8.0, 8.2, 8.3,  # 6-11
        8.4, 9.1, 11.2, 14.6, 18.1, 16.2,  # 12-17
        11.8, 8.9, 7.4, 6.5, 5.8, 5.2,  # 18-23
    ]
    realized_curve = forecast_curve[: now_ct.hour + 1]  # past + current
    bars = [
        {
            "hour": h,
            "forecast_cents": forecast_curve[h],
            "realized_cents": realized_curve[h] if h < len(realized_curve) else None,
        }
        for h in range(24)
    ]

    return {
        "now": now_ct.isoformat(),
        "day_type": "NORMAL",
        "indoor_history": indoor_history,
        "setpoint_history": setpoint_history,
        "setpoint_planned": _planned_setpoints("NORMAL"),
        "bars": bars,
    }
