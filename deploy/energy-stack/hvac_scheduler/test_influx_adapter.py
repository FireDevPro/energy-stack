"""Tests for influx_adapter.py — typed projection layer over influxdb_client records."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from .influx_adapter import TypedRecord, project_record


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
