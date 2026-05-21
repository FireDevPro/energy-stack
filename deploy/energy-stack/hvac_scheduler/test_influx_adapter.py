"""Tests for influx_adapter.py — typed projection layer over influxdb_client records."""

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from .influx_adapter import TypedRecord, project_record, write_point


class TestProjectRecord:
    def test_projects_a_complete_record(self):
        """Happy path: record has all fields; project_record returns a TypedRecord."""
        raw = MagicMock()
        raw.get_value.return_value = 12.5
        raw.get_time.return_value = datetime(2026, 5, 20, 18, 0, tzinfo=timezone.utc)
        raw.get_field.return_value = "price_cents_per_kwh"
        raw.get_measurement.return_value = "comed.prices"

        result = project_record(raw)

        assert isinstance(result, TypedRecord)
        assert result.value == 12.5
        assert result.time_utc == datetime(2026, 5, 20, 18, 0, tzinfo=timezone.utc)
        assert result.field == "price_cents_per_kwh"
        assert result.measurement == "comed.prices"

    def test_raises_when_value_is_None(self):
        """If get_value() returns None, project_record raises ValueError."""
        raw = MagicMock()
        raw.get_value.return_value = None
        raw.get_time.return_value = datetime(2026, 5, 20, 18, 0, tzinfo=timezone.utc)
        raw.get_field.return_value = "price_cents_per_kwh"
        raw.get_measurement.return_value = "comed.prices"

        with pytest.raises(ValueError, match="value"):
            project_record(raw)

    def test_raises_when_time_is_None(self):
        """If get_time() returns None, project_record raises ValueError."""
        raw = MagicMock()
        raw.get_value.return_value = 12.5
        raw.get_time.return_value = None
        raw.get_field.return_value = "price_cents_per_kwh"
        raw.get_measurement.return_value = "comed.prices"

        with pytest.raises(ValueError, match="time"):
            project_record(raw)

    def test_coerces_naive_datetime_to_utc(self):
        """If get_time() returns a naive datetime, project_record assumes UTC and tags it."""
        raw = MagicMock()
        raw.get_value.return_value = 12.5
        raw.get_time.return_value = datetime(2026, 5, 20, 18, 0)  # naive
        raw.get_field.return_value = "price_cents_per_kwh"
        raw.get_measurement.return_value = "comed.prices"

        result = project_record(raw)

        assert result.time_utc.tzinfo == timezone.utc

    def test_value_is_coerced_to_float(self):
        """If get_value() returns an int, project_record coerces to float."""
        raw = MagicMock()
        raw.get_value.return_value = 12  # int, not float
        raw.get_time.return_value = datetime(2026, 5, 20, 18, 0, tzinfo=timezone.utc)
        raw.get_field.return_value = "price_cents_per_kwh"
        raw.get_measurement.return_value = "comed.prices"

        result = project_record(raw)

        assert isinstance(result.value, float)
        assert result.value == 12.0


class TestTypedRecord:
    def test_is_frozen(self):
        """TypedRecord is immutable."""
        rec = TypedRecord(
            value=12.5,
            time_utc=datetime(2026, 5, 20, 18, 0, tzinfo=timezone.utc),
            field="price_cents_per_kwh",
            measurement="comed.prices",
        )
        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            rec.value = 13.0  # type: ignore[misc]


class _FakePoint:
    """Test double for influxdb_client.Point that records all chain calls."""

    def __init__(self, measurement: str) -> None:
        self.measurement = measurement
        self.tags: dict[str, str] = {}
        self.fields: dict[str, object] = {}
        self.time_value: Any = None

    def tag(self, k: str, v: str) -> "_FakePoint":
        self.tags[k] = v
        return self

    def field(self, k: str, v: object) -> "_FakePoint":
        self.fields[k] = v
        return self

    def time(self, t: Any) -> "_FakePoint":
        self.time_value = t
        return self


class TestWritePoint:
    def test_writes_point_with_tags_and_fields(self, monkeypatch):
        """Happy path: write_point builds a Point with the given measurement,
        tags, and fields, and calls write_api.write(bucket=..., record=...)."""
        captured: list[_FakePoint] = []

        def factory(name):
            p = _FakePoint(name)
            captured.append(p)
            return p

        from . import influx_adapter
        monkeypatch.setattr(influx_adapter, "Point", factory)

        write_api = MagicMock()
        write_point(
            write_api,
            bucket="energy",
            measurement="hvac.test",
            tags={"k1": "v1", "k2": "v2"},
            fields={"f1": 1.5, "f2": 7},
        )

        write_api.write.assert_called_once()
        call_kwargs = write_api.write.call_args.kwargs
        assert call_kwargs["bucket"] == "energy"
        # The recorded Point object is in 'record' kwarg
        assert call_kwargs["record"] is captured[0]
        assert captured[0].measurement == "hvac.test"
        assert captured[0].tags == {"k1": "v1", "k2": "v2"}
        assert captured[0].fields == {"f1": 1.5, "f2": 7}
        assert captured[0].time_value is None

    def test_writes_point_with_time(self, monkeypatch):
        """When `time` kwarg is provided, the Point has that timestamp."""
        captured: list[_FakePoint] = []
        def _factory(name: str) -> _FakePoint:
            p = _FakePoint(name)
            captured.append(p)
            return p
        from . import influx_adapter
        monkeypatch.setattr(influx_adapter, "Point", _factory)

        write_api = MagicMock()
        ts = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
        write_point(
            write_api,
            bucket="energy",
            measurement="hvac.switch_event",
            tags={"feed": "price"},
            fields={"healthy": 1},
            time=ts,
        )

        assert captured[0].time_value == ts

    def test_writes_point_without_time_uses_no_time(self, monkeypatch):
        """When `time` kwarg is omitted, Point.time() is never called."""
        captured: list[_FakePoint] = []
        def _factory(name: str) -> _FakePoint:
            p = _FakePoint(name)
            captured.append(p)
            return p
        from . import influx_adapter
        monkeypatch.setattr(influx_adapter, "Point", _factory)

        write_api = MagicMock()
        write_point(
            write_api,
            bucket="energy",
            measurement="hvac.decisions",
            tags={"decision_for_date": "2026-06-01"},
            fields={"high_f": 92.0},
        )

        assert captured[0].time_value is None

    def test_accepts_int_float_bool_str_field_values(self, monkeypatch):
        """Field values may be a mix of int, float, bool, and str.

        Bool is preserved (NOT coerced to int) so InfluxDB stores it as
        a native boolean field — the ``hvac.input_feed_health`` measurement
        depends on ``healthy=true``/``healthy=false`` line protocol shape."""
        captured: list[_FakePoint] = []
        def _factory(name: str) -> _FakePoint:
            p = _FakePoint(name)
            captured.append(p)
            return p
        from . import influx_adapter
        monkeypatch.setattr(influx_adapter, "Point", _factory)

        write_api = MagicMock()
        write_point(
            write_api,
            bucket="energy",
            measurement="hvac.actions",
            tags={"day_type": "hot"},
            fields={
                "cool_setpoint_f": 74.0,  # float
                "applied": 1,             # int
                "fan_mode": "auto",       # str
                "healthy": True,          # bool
            },
        )

        assert captured[0].fields == {
            "cool_setpoint_f": 74.0,
            "applied": 1,
            "fan_mode": "auto",
            "healthy": True,
        }
        # Bool must NOT be coerced — `is True` checks identity-strict.
        assert captured[0].fields["healthy"] is True
