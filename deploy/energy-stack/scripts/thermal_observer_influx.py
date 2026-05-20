from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from thermal_observer import ThermalFitResult


MODEL_VERSION = "thermal_observer.v1"
MEASUREMENT = "hvac.thermal_observer"


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


def build_line_protocol(
    result: ThermalFitResult,
    outdoor_measurement: str,
    generated_at: datetime,
) -> str:
    tags = {
        "model_version": MODEL_VERSION,
        "outdoor_measurement": outdoor_measurement,
        "read_only": "true",
        "accepted": _bool_tag(result.accepted),
    }
    fields = _result_fields(result)
    timestamp_ns = _datetime_to_ns(generated_at)

    tag_text = ",".join(
        f"{_escape_tag_part(key)}={_escape_tag_part(value)}"
        for key, value in tags.items()
    )
    field_text = ",".join(
        f"{_escape_tag_part(key)}={_format_field_value(value)}"
        for key, value in fields.items()
    )
    return f"{_escape_measurement(MEASUREMENT)},{tag_text} {field_text} {timestamp_ns}"


def json_artifact_payload(
    result: ThermalFitResult,
    outdoor_measurement: str,
    generated_at: datetime,
) -> dict[str, Any]:
    return {
        "model_version": MODEL_VERSION,
        "read_only": True,
        "outdoor_measurement": outdoor_measurement,
        "generated_at": _normalized_datetime(generated_at).isoformat(),
        "result": result.to_json_dict(),
    }


def write_json_atomic(path: str | Path, payload: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            json.dump(payload, tmp_file, indent=2, sort_keys=True)
            tmp_file.write("\n")
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_name, destination)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def _result_fields(result: ThermalFitResult) -> dict[str, Any]:
    result_dict = result.to_json_dict()
    fields: dict[str, Any] = {}
    for key, value in result_dict.items():
        if key == "filter_counts":
            for filter_key in sorted(value):
                fields[f"filter_{filter_key}"] = value[filter_key]
            continue
        if value is not None:
            fields[key] = value
    return fields


def _format_field_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"{value}i"
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (tuple, list)):
        return json.dumps(json.dumps(list(value), separators=(",", ":")))
    if isinstance(value, str):
        return json.dumps(value)
    return json.dumps(value, separators=(",", ":"))


def _datetime_to_ns(value: datetime) -> int:
    normalized = _normalized_datetime(value)
    return int(normalized.timestamp()) * 1_000_000_000 + normalized.microsecond * 1_000


def _normalized_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bool_tag(value: bool) -> str:
    return "true" if value else "false"


def _escape_measurement(value: str) -> str:
    return value.replace("\\", "\\\\").replace(",", r"\,").replace(" ", r"\ ")


def _escape_tag_part(value: object) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(",", r"\,")
        .replace(" ", r"\ ")
        .replace("=", r"\=")
    )


def _flux_record_ref(field: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", field):
        return f"r.{field}"
    return f"r[{json.dumps(field)}]"
