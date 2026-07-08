"""Flux query builders for the Vigil endpoints — rev-4 measurements only.

Reuses the row plumbing from ``influx.py`` (``_first_row`` / ``_series_rows`` /
``QueryApi``). Each ``query_*`` returns plain dicts/lists the assemblers
consume. Functions that feed time arithmetic (price age, hold expiry, episode
grouping) return aware ``datetime`` objects, not ISO strings.

Schema note: every query pre-filters ``_field`` before any ``group()`` /
``pivot()`` — mixing fields under a ``group()`` on a multi-field measurement
collides at runtime (this project has paid for that once).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .influx import QueryApi, _first_row, _series_rows


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def query_latest_price_5min(client: QueryApi, *, bucket: str) -> dict[str, Any] | None:
    flux = f"""
from(bucket: "{bucket}")
  |> range(start: -60m)
  |> filter(fn: (r) => r._measurement == "comed.prices"
                   and r.period_type == "5min"
                   and r._field == "price_cents_per_kwh")
  |> last()
"""
    row = _first_row(client.query(flux))
    if row is None:
        return None
    v = row.get("_value")
    return {"cents": float(v) if v is not None else None, "time": _aware(row.get("_time"))}


def query_latest_hourly_avg(client: QueryApi, *, bucket: str) -> float | None:
    flux = f"""
from(bucket: "{bucket}")
  |> range(start: -3h)
  |> filter(fn: (r) => r._measurement == "comed.prices"
                   and r.period_type == "hourly_avg"
                   and r._field == "price_cents_per_kwh")
  |> last()
"""
    row = _first_row(client.query(flux))
    if row is None:
        return None
    v = row.get("_value")
    return float(v) if v is not None else None


def query_latest_thermostat(client: QueryApi, *, bucket: str) -> dict[str, Any] | None:
    flux = f"""
from(bucket: "{bucket}")
  |> range(start: -30m)
  |> filter(fn: (r) => r._measurement == "hvac.thermostat")
  |> filter(fn: (r) => r._field == "indoor_temp_f"
                   or r._field == "indoor_temp_f_hires"
                   or r._field == "cool_setpoint_f"
                   or r._field == "hold_mode"
                   or r._field == "hvac_state"
                   or r._field == "fan_mode"
                   or r._field == "humidity_pct")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
    row = _first_row(client.query(flux))
    if row is None:
        return None
    indoor = row.get("indoor_temp_f_hires")
    if indoor is None:
        indoor = row.get("indoor_temp_f")
    return {
        "indoor_temp_f": indoor,
        "cool_setpoint_f": row.get("cool_setpoint_f"),
        "hold_mode": row.get("hold_mode"),
        "hvac_state": row.get("hvac_state"),
        "fan_mode": row.get("fan_mode"),
        "humidity_pct": row.get("humidity_pct"),
        "time": _aware(row.get("_time")),
    }


def query_latest_action(client: QueryApi, *, bucket: str) -> dict[str, Any] | None:
    flux = f"""
from(bucket: "{bucket}")
  |> range(start: -8h)
  |> filter(fn: (r) => r._measurement == "hvac.actions")
  |> filter(fn: (r) => r._field == "commanded_cool"
                   or r._field == "schedule_cool"
                   or r._field == "hold_expires_at"
                   or r._field == "humidity_gated"
                   or r._field == "applied")
  |> pivot(rowKey: ["_time", "unit", "tier", "action_label", "dry_run"],
           columnKey: ["_field"], valueColumn: "_value")
  |> group()
  |> sort(columns: ["_time"], desc: true)
  |> limit(n: 1)
"""
    row = _first_row(client.query(flux))
    if row is None:
        return None
    expires_raw = row.get("hold_expires_at") or ""
    return {
        "tier": row.get("tier"),
        "commanded_cool": row.get("commanded_cool"),
        "schedule_cool": row.get("schedule_cool"),
        "hold_expires_at": _parse_iso(expires_raw),
        "humidity_gated": bool(row.get("humidity_gated")),
        "applied": bool(row.get("applied")),
        "_time": _aware(row.get("_time")),
    }


def query_latest_arm_mode(client: QueryApi, *, bucket: str) -> dict[str, Any] | None:
    flux = f"""
from(bucket: "{bucket}")
  |> range(start: -30m)
  |> filter(fn: (r) => r._measurement == "hvac.arm_mode")
  |> filter(fn: (r) => r._field == "mode_actual")
  |> last()
"""
    row = _first_row(client.query(flux))
    if row is None:
        return None
    return {"scheduler_mode": row.get("scheduler_mode"), "time": _aware(row.get("_time"))}


def query_watchdog_down(client: QueryApi, *, bucket: str, window_min: int = 10) -> bool:
    flux = f"""
from(bucket: "{bucket}")
  |> range(start: -{window_min}m)
  |> filter(fn: (r) => r._measurement == "hvac.heartbeat"
                   and r._field == "controller_alive"
                   and r._value == false)
  |> count()
"""
    row = _first_row(client.query(flux))
    if row is None:
        return False
    v = row.get("_value")
    return v is not None and v > 0


def query_outdoor_now(client: QueryApi, *, bucket: str) -> dict[str, Any] | None:
    flux = f"""
from(bucket: "{bucket}")
  |> range(start: -30m)
  |> filter(fn: (r) => r._measurement == "ecowitt.weather")
  |> filter(fn: (r) => r._field == "ch1_temp_f"
                   or r._field == "ch1_rh_pct"
                   or r._field == "ch1_dewpoint_f")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
    row = _first_row(client.query(flux))
    if row is None:
        return None
    def _r(v: Any) -> Any:
        return round(float(v), 1) if v is not None else None

    return {
        "temp_f": _r(row.get("ch1_temp_f")),
        "rh_pct": _r(row.get("ch1_rh_pct")),
        "dewpoint_f": _r(row.get("ch1_dewpoint_f")),
    }


def query_price_series(client: QueryApi, *, bucket: str, hours: int = 24) -> list[dict[str, Any]]:
    """~48 points (30-min mean) of the 5-min ComEd price over the window."""
    flux = f"""
from(bucket: "{bucket}")
  |> range(start: -{hours}h)
  |> filter(fn: (r) => r._measurement == "comed.prices"
                   and r.period_type == "5min"
                   and r._field == "price_cents_per_kwh")
  |> aggregateWindow(every: 30m, fn: mean, createEmpty: false)
  |> sort(columns: ["_time"])
"""
    out: list[dict[str, Any]] = []
    for row in _series_rows(client.query(flux)):
        t, v = _aware(row.get("_time")), row.get("_value")
        if t is None or v is None:
            continue
        out.append({"t": t.isoformat(), "cents": round(float(v), 1)})
    return out


def query_price_series_raw(
    client: QueryApi, *, bucket: str, hours: int = 24
) -> list[tuple[datetime, float]]:
    """Raw 5-min ComEd price points as ``[(t, cents)]`` for accurate
    spike-peak lookup (the 30-min mean series would understate peaks)."""
    flux = f"""
from(bucket: "{bucket}")
  |> range(start: -{hours}h)
  |> filter(fn: (r) => r._measurement == "comed.prices"
                   and r.period_type == "5min"
                   and r._field == "price_cents_per_kwh")
  |> sort(columns: ["_time"])
"""
    out: list[tuple[datetime, float]] = []
    for row in _series_rows(client.query(flux)):
        t, v = _aware(row.get("_time")), row.get("_value")
        if t is None or v is None:
            continue
        out.append((t, float(v)))
    return out


def query_price_overlay_transitions(
    client: QueryApi, *, bucket: str, hours: int = 24
) -> list[dict[str, Any]]:
    """Tier transitions over the window, ascending. Each: {tier(new_tier),
    at(datetime), cents}."""
    flux = f"""
from(bucket: "{bucket}")
  |> range(start: -{hours}h)
  |> filter(fn: (r) => r._measurement == "hvac.price_overlay"
                   and r._field == "current_price_cents")
  |> sort(columns: ["_time"])
"""
    out: list[dict[str, Any]] = []
    for row in _series_rows(client.query(flux)):
        t = _aware(row.get("_time"))
        tier = row.get("new_tier")
        v = row.get("_value")
        if t is None or tier is None:
            continue
        out.append({"tier": tier, "at": t, "cents": float(v) if v is not None else 0.0})
    return out


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware(dt)
