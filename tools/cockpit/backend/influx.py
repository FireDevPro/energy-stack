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
    """Latest comed.prices row, restricted to period_type=5min for the
    real-time current price. The measurement also carries an hourly_avg
    series — those rows are stale by design relative to the 5-min feed.
    """
    flux = f"""
from(bucket: "{bucket}")
  |> range(start: -30m)
  |> filter(fn: (r) => r._measurement == "comed.prices"
                   and r.period_type == "5min"
                   and r._field == "price_cents_per_kwh")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
    row = _first_row(client.query(flux))
    if row is None:
        return None
    cents = row.get("price_cents_per_kwh")
    return {
        "current_cents_per_kwh": float(cents) if cents is not None else None,
        "source_ts": _to_iso(row.get("_time")),
    }


def query_outdoor_now(
    client: QueryApi, *, bucket: str
) -> dict[str, Any] | None:
    """Latest ecowitt.weather Channel 1 outdoor reading. Per operator
    direction, ch1_* is the outdoor sensor (the WS90 array sits indoors
    or somewhere else)."""
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
    return {
        "outdoor_temp_f": row.get("ch1_temp_f"),
        "outdoor_rh_pct": row.get("ch1_rh_pct"),
        "outdoor_dewpoint_f": row.get("ch1_dewpoint_f"),
        "source_ts": _to_iso(row.get("_time")),
    }


def query_today_forecast(
    client: QueryApi, *, bucket: str
) -> dict[str, Any] | None:
    """Latest nws.forecast row tagged for_period=today. Returns high,
    apparent_max, dewpoint, heat advisory flag. Source: nws-poller."""
    flux = f"""
from(bucket: "{bucket}")
  |> range(start: -2h)
  |> filter(fn: (r) => r._measurement == "nws.forecast" and r.for_period == "today")
  |> filter(fn: (r) => r._field == "high_f"
                   or r._field == "low_f"
                   or r._field == "apparent_max_f"
                   or r._field == "max_dewpoint_f"
                   or r._field == "is_heat_advisory")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
    row = _first_row(client.query(flux))
    if row is None:
        return None
    return {
        "high_f": row.get("high_f"),
        "low_f": row.get("low_f"),
        "apparent_max_f": row.get("apparent_max_f"),
        "max_dewpoint_f": row.get("max_dewpoint_f"),
        "is_heat_advisory": bool(row.get("is_heat_advisory")),
        "source_ts": _to_iso(row.get("_time")),
    }


def query_latest_action(client: QueryApi, *, bucket: str) -> dict[str, Any] | None:
    """Latest hvac.actions row (the most recent action the scheduler
    fired or would have fired in shadow). Returns the full pivoted row
    with action_label, applied (int), dry_run (tag), supervisor_decision,
    effective_cool_f, schedule_cool_f, etc. Source: deploy/energy-stack/
    hvac_scheduler/app.py write_action()."""
    # hvac.actions has six tags (action_label, day_type, dry_run,
    # fivecp_active, price_overlay_tier, supervisor_decision). Default
    # Influx grouping by tags means `last()` returns one row per tag-
    # combination, and `_first_row()` would pick one arbitrarily — not
    # necessarily the absolute most-recent action. Pivot with all tag
    # columns in rowKey preserves tag values per emission, then sort by
    # _time desc + limit 1 gives the unambiguous latest row.
    flux = f"""
from(bucket: "{bucket}")
  |> range(start: -8h)
  |> filter(fn: (r) => r._measurement == "hvac.actions")
  |> filter(fn: (r) => r._field == "applied"
                   or r._field == "cool_setpoint_f"
                   or r._field == "heat_setpoint_f"
                   or r._field == "effective_cool_f"
                   or r._field == "schedule_cool_f"
                   or r._field == "setpoint_reason"
                   or r._field == "supervisor_reason"
                   or r._field == "fan_mode"
                   or r._field == "error")
  |> pivot(
       rowKey: ["_time", "action_label", "day_type", "dry_run",
                "supervisor_decision", "price_overlay_tier", "fivecp_active"],
       columnKey: ["_field"],
       valueColumn: "_value")
  |> group()
  |> sort(columns: ["_time"], desc: true)
  |> limit(n: 1)
"""
    row = _first_row(client.query(flux))
    if row is None:
        return None
    return {
        "action_label": row.get("action_label"),
        "day_type": row.get("day_type"),
        "dry_run": str(row.get("dry_run")).lower() == "true",
        "supervisor_decision": row.get("supervisor_decision"),
        "applied": bool(row.get("applied")),
        "cool_setpoint_f": row.get("cool_setpoint_f"),
        "heat_setpoint_f": row.get("heat_setpoint_f"),
        "effective_cool_f": row.get("effective_cool_f"),
        "schedule_cool_f": row.get("schedule_cool_f"),
        "setpoint_reason": row.get("setpoint_reason"),
        "supervisor_reason": row.get("supervisor_reason"),
        "fan_mode": row.get("fan_mode"),
        "error": row.get("error"),
        "fire_ts": _to_iso(row.get("_time")),
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


# Feeds the cockpit surfaces in the feed_health strip. Each entry:
#   (display_name, measurement, freshness_cadence_key, tag_filter)
# where tag_filter is None or a {tag_name: tag_value} dict that pins the
# series to the operationally-meaningful one. ComEd writes both
# period_type=5min (the live spot price the controller reacts to) and
# period_type=hourly_avg (a derived rollup); without the filter, a fresh
# hourly_avg row would mark ComEd "fresh" while the 5min feed silently
# went stale — misleading the operator. NWS writes for_period=today and
# for_period=tomorrow; freshness must mean "today's forecast is fresh".
FEED_DEFINITIONS: list[tuple[str, str, str, dict[str, str] | None]] = [
    ("ComEd",        "comed.prices",      "comed.prices",      {"period_type": "5min"}),
    ("NWS",          "nws.forecast",      "nws.forecast",      {"for_period": "today"}),
    ("PJM forecast", "pjm.load_forecast", "pjm.load_forecast", None),
    ("PJM RT LMP",   "pjm.lmp_rt_hourly", "pjm.rt_hrl_lmps",   None),
    ("Refoss",       "refoss.channel",    "refoss.channel",    None),
    ("EAGLE",        "eagle.meter",       "eagle.meter",       None),
    ("Thermostat",   "hvac.thermostat",   "hvac.thermostat",   None),
]


def _series_rows(tables: Iterable[_Table]) -> list[dict[str, Any]]:
    """Materialize every row across every table into a list of dicts.

    Used by the day_at_a_glance series queries which read time-ordered
    point streams rather than a single latest-row pivot.
    """
    out: list[dict[str, Any]] = []
    for table in tables:
        for record in table.records:
            out.append(dict(record.values))
    return out


def query_thermostat_history(
    client: QueryApi,
    *,
    bucket: str,
    start_utc: datetime,
    end_utc: datetime,
) -> list[dict[str, Any]]:
    """Read indoor_temp_f + cool_setpoint_f + heat_setpoint_f from
    hvac.thermostat over [start_utc, end_utc).

    Returns list of {ts: ISO-8601, indoor_temp_f, cool_setpoint_f,
    heat_setpoint_f} ordered by time ascending. Drops rows missing all
    three fields. Cadence in production is ~6 min, so a 24h window
    yields ~240 rows.
    """
    flux = f"""
from(bucket: "{bucket}")
  |> range(start: {start_utc.isoformat()}, stop: {end_utc.isoformat()})
  |> filter(fn: (r) => r._measurement == "hvac.thermostat")
  |> filter(fn: (r) => r._field == "indoor_temp_f"
                   or r._field == "cool_setpoint_f"
                   or r._field == "heat_setpoint_f")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"])
"""
    out: list[dict[str, Any]] = []
    for row in _series_rows(client.query(flux)):
        ts = row.get("_time")
        indoor = row.get("indoor_temp_f")
        cool = row.get("cool_setpoint_f")
        heat = row.get("heat_setpoint_f")
        if indoor is None and cool is None and heat is None:
            continue
        out.append(
            {
                "ts": _to_iso(ts),
                "indoor_temp_f": indoor,
                "cool_setpoint_f": cool,
                "heat_setpoint_f": heat,
            }
        )
    return out


def query_hourly_avg_prices(
    client: QueryApi,
    *,
    bucket: str,
    start_utc: datetime,
    end_utc: datetime,
) -> list[dict[str, Any]]:
    """Realized comed.prices hourly_avg rows over [start_utc, end_utc).

    Each row: {ts: ISO-8601 (hour-bucket UTC), cents_per_kwh}. The
    comed_poller upserts the same hour-bucket as the in-progress hour
    accumulates samples; the latest value for each hour is its final
    realized average. Sorted ascending.
    """
    flux = f"""
from(bucket: "{bucket}")
  |> range(start: {start_utc.isoformat()}, stop: {end_utc.isoformat()})
  |> filter(fn: (r) => r._measurement == "comed.prices"
                   and r.period_type == "hourly_avg"
                   and r._field == "price_cents_per_kwh")
  |> sort(columns: ["_time"])
"""
    out: list[dict[str, Any]] = []
    for row in _series_rows(client.query(flux)):
        ts = row.get("_time")
        val = row.get("_value")
        if ts is None or val is None:
            continue
        out.append({"ts": _to_iso(ts), "cents_per_kwh": float(val)})
    return out


def query_da_lmp_forecast(
    client: QueryApi,
    *,
    bucket: str,
    start_utc: datetime,
    end_utc: datetime,
    zone: str = "COMED",
) -> list[dict[str, Any]]:
    """24 hourly PJM day-ahead LMP rows for the COMED zone over
    [start_utc, end_utc).

    Each row: {ts: ISO-8601 (hour-bucket UTC), cents_per_kwh}.
    PJM publishes total_lmp_da in $/MWh; cockpit converts to ¢/kWh
    (÷10) so the chart shares units with comed.prices. Sorted
    ascending. Returns up to 24 rows when the range spans a full
    PJM-published day; fewer if the publish hadn't landed at query
    time.
    """
    flux = f"""
from(bucket: "{bucket}")
  |> range(start: {start_utc.isoformat()}, stop: {end_utc.isoformat()})
  |> filter(fn: (r) => r._measurement == "pjm.lmp_da_hourly"
                   and r.zone == "{zone}"
                   and r._field == "total_lmp_da")
  |> sort(columns: ["_time"])
"""
    out: list[dict[str, Any]] = []
    for row in _series_rows(client.query(flux)):
        ts = row.get("_time")
        val = row.get("_value")
        if ts is None or val is None:
            continue
        out.append({"ts": _to_iso(ts), "cents_per_kwh": float(val) / 10.0})
    return out


def query_feed_last_ts(
    client: QueryApi,
    *,
    bucket: str,
    measurement: str,
    tag_filter: dict[str, str] | None = None,
) -> datetime | None:
    """Most-recent write timestamp for `measurement` in the last 24h,
    optionally pinned to a specific tag-value combination. Collapses all
    remaining tag groups so multi-series measurements return the absolute
    latest timestamp matching the filter (not whichever tag-group came
    first)."""
    extra = ""
    if tag_filter:
        for k, v in tag_filter.items():
            extra += f' and r["{k}"] == "{v}"'
    flux = f"""
from(bucket: "{bucket}")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "{measurement}"{extra})
  |> keep(columns: ["_time"])
  |> group()
  |> max(column: "_time")
"""
    row = _first_row(client.query(flux))
    if row is None:
        return None
    return row.get("_time")
