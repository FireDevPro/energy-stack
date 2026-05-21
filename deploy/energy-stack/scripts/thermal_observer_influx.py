from __future__ import annotations

import json
import re
from typing import Any

from thermal_observer import ThermalSample


def rows_to_samples(rows: list[dict[str, Any]]) -> list[ThermalSample]:
    samples: list[ThermalSample] = []
    previous_cool_setpoint: float | None = None
    required_fields = (
        "_time",
        "indoor_temp_f",
        "outdoor_temp_f",
        "solar_radiation_w_m2",
        "cool_actual_pct",
        "heat_actual_pct",
    )

    for row in rows:
        if any(row.get(field) is None for field in required_fields):
            continue

        cool_setpoint = _optional_float(row.get("cool_setpoint_f"))
        setpoint_changed = (
            previous_cool_setpoint is not None
            and cool_setpoint is not None
            and abs(cool_setpoint - previous_cool_setpoint) >= 0.5
        )
        if cool_setpoint is not None:
            previous_cool_setpoint = cool_setpoint

        samples.append(
            ThermalSample(
                ts=row["_time"],
                indoor_temp_f=float(row["indoor_temp_f"]),
                outdoor_temp_f=float(row["outdoor_temp_f"]),
                solar_radiation_w_m2=float(row["solar_radiation_w_m2"]),
                cool_actual_pct=float(row["cool_actual_pct"]),
                heat_actual_pct=float(row["heat_actual_pct"]),
                cool_setpoint_f=cool_setpoint,
                setpoint_changed=setpoint_changed,
            )
        )

    return samples


def build_query(
    bucket: str,
    start: str,
    sample_minutes: int,
    outdoor_measurement: str,
    outdoor_temp_field: str,
    solar_field: str,
) -> str:
    window = f"{sample_minutes}m"
    bucket_literal = json.dumps(bucket)
    outdoor_measurement_literal = json.dumps(outdoor_measurement)
    outdoor_temp_literal = json.dumps(outdoor_temp_field)
    solar_literal = json.dumps(solar_field)
    outdoor_temp_ref = _flux_record_ref(outdoor_temp_field)
    solar_ref = _flux_record_ref(solar_field)

    return f"""thermostat = from(bucket: {bucket_literal})
  |> range(start: {start})
  |> filter(fn: (r) => r._measurement == "hvac.thermostat")
  |> filter(fn: (r) => r._field == "indoor_temp_f" or r._field == "cool_setpoint_f")
  |> aggregateWindow(every: {window}, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> keep(columns: ["_time", "indoor_temp_f", "cool_setpoint_f"])

comfortnet = from(bucket: {bucket_literal})
  |> range(start: {start})
  |> filter(fn: (r) => r._measurement == "hvac.comfortnet")
  |> filter(fn: (r) => r._field == "cool_actual_pct" or r._field == "heat_actual_pct")
  |> aggregateWindow(every: {window}, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> keep(columns: ["_time", "cool_actual_pct", "heat_actual_pct"])

weather = from(bucket: {bucket_literal})
  |> range(start: {start})
  |> filter(fn: (r) => r._measurement == {outdoor_measurement_literal})
  |> filter(fn: (r) => r._field == {outdoor_temp_literal} or r._field == {solar_literal})
  |> aggregateWindow(every: {window}, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> map(fn: (r) => ({{ r with outdoor_temp_f: {outdoor_temp_ref}, solar_radiation_w_m2: {solar_ref} }}))
  |> keep(columns: ["_time", "outdoor_temp_f", "solar_radiation_w_m2"])

hvac = join(tables: {{thermostat: thermostat, comfortnet: comfortnet}}, on: ["_time"])

join(tables: {{hvac: hvac, weather: weather}}, on: ["_time"])
  |> sort(columns: ["_time"])"""


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _flux_record_ref(field: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", field):
        return f"r.{field}"
    return f"r[{json.dumps(field)}]"
