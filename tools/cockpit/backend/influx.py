"""Influx query builders for the cockpit snapshot.

Each `query_*` function issues a Flux query against the energy-stack
InfluxDB bucket and returns a plain dict the assembler can consume.

The queries follow the standard idiom used elsewhere in the energy-stack:
`range -> filter measurement -> filter fields -> last -> pivot`. Each
returns the most-recent state of a single measurement.

Live smoke against the Pi-lab Influx is the operator's manual step (see
README) — these query strings have not been validated against the
production Influx in this PR. Mocked tests exercise the assembler path
end-to-end with synthetic InfluxDB tables.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Protocol


class _Record(Protocol):
    @property
    def values(self) -> dict[str, Any]: ...

    def get_value(self) -> Any: ...

    def get_time(self) -> datetime: ...


class _Table(Protocol):
    @property
    def records(self) -> Iterable[_Record]: ...


class QueryApi(Protocol):
    """Subset of influxdb_client.QueryApi we depend on. Tests use a
    plain Mock; the real client matches this shape."""

    def query(self, query: str, org: str | None = None) -> Iterable[_Table]: ...


def _first_row(tables: Iterable[_Table]) -> dict[str, Any] | None:
    for table in tables:
        for record in table.records:
            return dict(record.values)
    return None


def _to_iso(ts: datetime | None) -> str | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.isoformat()


def query_latest_thermostat(client: QueryApi, *, bucket: str) -> dict[str, Any] | None:
    """Latest hvac.thermostat row pivoted across required fields.

    Returns dict shaped as:
      {
        "indoor_temp_f": float, "humidity_pct": float,
        "cool_setpoint_f": float, "heat_setpoint_f": float,
        "hvac_mode": str, "fan_mode": str,
        "source_ts": ISO-8601 str,
      }
    or None if no rows in the last 30m.
    """
    flux = f"""
from(bucket: "{bucket}")
  |> range(start: -30m)
  |> filter(fn: (r) => r._measurement == "hvac.thermostat")
  |> filter(fn: (r) => r._field == "indoor_temp_f"
                   or r._field == "humidity_pct"
                   or r._field == "cool_setpoint_f"
                   or r._field == "heat_setpoint_f"
                   or r._field == "hvac_mode"
                   or r._field == "fan_mode")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
    row = _first_row(client.query(flux))
    if row is None:
        return None
    return {
        "indoor_temp_f": row.get("indoor_temp_f"),
        "indoor_humidity_pct": row.get("humidity_pct"),
        "cool_setpoint_f": row.get("cool_setpoint_f"),
        "heat_setpoint_f": row.get("heat_setpoint_f"),
        "hvac_mode": row.get("hvac_mode"),
        "fan_mode": row.get("fan_mode"),
        "source_ts": _to_iso(row.get("_time")),
    }


def query_latest_arm_mode(client: QueryApi, *, bucket: str) -> dict[str, Any] | None:
    """Latest hvac.arm_mode row. arm + scheduler_mode are tags;
    mode_actual is a field."""
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
    return {
        "mode_actual": row.get("_value"),
        "arm": row.get("arm"),
        "scheduler_mode": row.get("scheduler_mode"),
        "source_ts": _to_iso(row.get("_time")),
    }


def query_latest_price(client: QueryApi, *, bucket: str) -> dict[str, Any] | None:
    """Latest comed.prices row.

    Returns the raw current cents/kWh; tier classification happens in the
    assembler so the tier-policy lives in one place."""
    flux = f"""
from(bucket: "{bucket}")
  |> range(start: -30m)
  |> filter(fn: (r) => r._measurement == "comed.prices")
  |> filter(fn: (r) => r._field == "current_5min_price" or r._field == "current_hour_avg")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
    row = _first_row(client.query(flux))
    if row is None:
        return None
    # Prefer the 5-min real-time price; fall back to hour-average if the
    # 5-min field isn't pivoted in.
    cents = row.get("current_5min_price") or row.get("current_hour_avg")
    return {
        "current_cents_per_kwh": float(cents) if cents is not None else None,
        "source_ts": _to_iso(row.get("_time")),
    }


def query_latest_heartbeat(
    client: QueryApi, *, bucket: str, window_min: int = 30
) -> dict[str, Any] | None:
    """Last hvac.heartbeat row (controller_alive=false written by the
    out-of-band watchdog). Returns None if no row in window — that's the
    normal healthy state."""
    flux = f"""
from(bucket: "{bucket}")
  |> range(start: -{window_min}m)
  |> filter(fn: (r) => r._measurement == "hvac.heartbeat")
  |> filter(fn: (r) => r._field == "controller_alive")
  |> last()
"""
    row = _first_row(client.query(flux))
    if row is None:
        return None
    return {
        "controller_alive": bool(row.get("_value")),
        "source_ts": _to_iso(row.get("_time")),
    }


# Feeds the cockpit surfaces in the feed_health strip. Display name →
# (measurement, optional field filter, expected cadence-source-key for
# freshness classification). The display name is what the UI renders.
FEED_DEFINITIONS: list[tuple[str, str, str]] = [
    ("ComEd", "comed.prices", "comed.prices"),
    ("NWS", "nws.forecast.hourly", "nws.forecast"),
    ("PJM forecast", "pjm.load_forecast", "pjm.load_forecast"),
    ("PJM RT LMP", "pjm.lmp_rt_hourly", "pjm.rt_hrl_lmps"),
    ("Refoss", "refoss.channel", "refoss.channel"),
    ("EAGLE", "eagle.meter", "eagle.meter"),
    ("Thermostat", "hvac.thermostat", "hvac.thermostat"),
]


def query_feed_last_ts(
    client: QueryApi, *, bucket: str, measurement: str
) -> datetime | None:
    """Most-recent write timestamp for `measurement` in the last 24h."""
    flux = f"""
from(bucket: "{bucket}")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "{measurement}")
  |> keep(columns: ["_time"])
  |> last(column: "_time")
"""
    row = _first_row(client.query(flux))
    if row is None:
        return None
    return row.get("_time")
