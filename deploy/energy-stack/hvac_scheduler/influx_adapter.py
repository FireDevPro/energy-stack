"""Typed adapter around influxdb_client record/query results.

The hvac-scheduler reads ComEd prices and other measurements from
Influx via the influxdb_client library. The library's `record` API
(`get_value()`, `get_time()`, `get_field()`, `get_measurement()`) is
weakly typed — every method returns `Any`. The 2026-05-19 19:18Z
freshness bug was directly enabled by this: nothing checked whether
`get_value()` returned a float or None, and nothing checked whether
the float's timestamp was current.

This module is the TYPED PROJECTION SURFACE. Code throughout the
scheduler imports `from .influx_adapter import project_record` (or
`from hvac_scheduler.influx_adapter import project_record` from
outside the package) rather than touching the raw library directly.

The adapter is enforced via an import-linter contract in
`pyproject.toml`: direct `from influxdb_client import ...` anywhere
under `hvac_scheduler` other than this module is a CI failure. See
spec §5.5.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from influxdb_client import Point  # ADAPTER is the only legitimate import site


@dataclass(frozen=True)
class TypedRecord:
    """Typed projection of an influxdb_client Record.

    All fields are validated and non-None at construction time. Callers
    receive a TypedRecord or an exception, NEVER a partial / None-valued
    record."""

    value: float
    time_utc: datetime
    field: str
    measurement: str


def project_record(record: Any) -> TypedRecord:
    """Project a raw influxdb_client.Record into a TypedRecord.

    Reads `get_value()`, `get_time()`, `get_field()`, `get_measurement()`
    from the raw record, validates each, and returns a frozen dataclass.

    Raises ValueError if any required field is missing (None) on the
    record. The caller is responsible for translating this into a
    domain-appropriate error (e.g., the scheduler may treat a missing
    value as `freshness="missing"`)."""

    value_raw = record.get_value()
    if value_raw is None:
        raise ValueError("influx record has no value")

    time_raw = record.get_time()
    if time_raw is None:
        raise ValueError("influx record has no time")

    if not isinstance(time_raw, datetime):
        raise ValueError(f"influx record time is not a datetime: {type(time_raw)}")

    # Influx client returns timezone-naive datetimes in some versions;
    # we treat them as UTC because that's what the bucket _time field
    # canonically is.
    if time_raw.tzinfo is None:
        time_utc = time_raw.replace(tzinfo=timezone.utc)
    else:
        time_utc = time_raw.astimezone(timezone.utc)

    field_raw = record.get_field()
    if field_raw is None:
        raise ValueError("influx record has no field")

    measurement_raw = record.get_measurement()
    if measurement_raw is None:
        raise ValueError("influx record has no measurement")

    return TypedRecord(
        value=float(value_raw),
        time_utc=time_utc,
        field=str(field_raw),
        measurement=str(measurement_raw),
    )


def write_point(
    write_api: Any,
    bucket: str,
    measurement: str,
    *,
    tags: dict[str, str],
    fields: dict[str, float | int | bool | str],
    time: datetime | None = None,
) -> None:
    """Typed write helper around influxdb_client.Point construction.

    Eliminates the untyped ``Point().tag().field().time()`` chain pattern
    from callers and centralizes the influxdb_client write-API import in
    this module. Per spec §5.5 import-linter contract, callers should
    import this function rather than touching ``influxdb_client.Point``
    directly.

    Field values may be int, float, bool, or str. Bools are passed
    through to ``Point.field()`` so InfluxDB stores them as native
    booleans (``healthy=true``/``healthy=false``) rather than coercing
    to int. Tag values are always str. ``time`` is optional; when
    provided, the Point uses that timestamp, otherwise Influx assigns
    server-time at write.
    """
    p = Point(measurement)
    for tag_key, tag_value in tags.items():
        p = p.tag(tag_key, tag_value)
    for field_key, field_value in fields.items():
        p = p.field(field_key, field_value)
    if time is not None:
        p = p.time(time)
    write_api.write(bucket=bucket, record=p)
