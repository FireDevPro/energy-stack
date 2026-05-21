from __future__ import annotations

from datetime import datetime, timezone

from thermal_observer import ThermalSample
from thermal_observer_influx import build_query, rows_to_samples


def test_rows_to_samples_converts_query_rows_to_thermal_samples():
    ts = datetime(2026, 7, 15, 12, 30, tzinfo=timezone.utc)

    samples = rows_to_samples(
        [
            {
                "_time": ts,
                "indoor_temp_f": 74.2,
                "outdoor_temp_f": 91.5,
                "solar_radiation_w_m2": 710.0,
                "cool_actual_pct": 42.0,
                "heat_actual_pct": 0.0,
                "cool_setpoint_f": 73.0,
            }
        ]
    )

    assert samples == [
        ThermalSample(
            ts=ts,
            indoor_temp_f=74.2,
            outdoor_temp_f=91.5,
            solar_radiation_w_m2=710.0,
            cool_actual_pct=42.0,
            heat_actual_pct=0.0,
            cool_setpoint_f=73.0,
            setpoint_changed=False,
        )
    ]


def test_rows_to_samples_skips_rows_missing_required_telemetry():
    ts = datetime(2026, 7, 15, 12, 30, tzinfo=timezone.utc)

    samples = rows_to_samples(
        [
            {
                "_time": ts,
                "indoor_temp_f": 74.2,
                "outdoor_temp_f": 91.5,
                "solar_radiation_w_m2": 710.0,
                "heat_actual_pct": 0.0,
                "cool_setpoint_f": 73.0,
            },
            {
                "_time": ts,
                "indoor_temp_f": 74.2,
                "outdoor_temp_f": 91.5,
                "cool_actual_pct": 42.0,
                "heat_actual_pct": 0.0,
                "cool_setpoint_f": 73.0,
            },
            {
                "_time": ts,
                "indoor_temp_f": 74.2,
                "outdoor_temp_f": 91.5,
                "solar_radiation_w_m2": 710.0,
                "cool_actual_pct": 42.0,
                "cool_setpoint_f": 73.0,
            },
        ]
    )

    assert samples == []


def test_rows_to_samples_marks_later_sample_when_setpoint_changes_by_half_degree():
    first_ts = datetime(2026, 7, 15, 12, 30, tzinfo=timezone.utc)
    second_ts = datetime(2026, 7, 15, 12, 40, tzinfo=timezone.utc)

    samples = rows_to_samples(
        [
            {
                "_time": first_ts,
                "indoor_temp_f": 74.2,
                "outdoor_temp_f": 91.5,
                "solar_radiation_w_m2": 0.0,
                "cool_actual_pct": 0.0,
                "heat_actual_pct": 0.0,
                "cool_setpoint_f": 73.0,
            },
            {
                "_time": second_ts,
                "indoor_temp_f": 74.4,
                "outdoor_temp_f": 91.7,
                "solar_radiation_w_m2": 0.0,
                "cool_actual_pct": 0.0,
                "heat_actual_pct": 0.0,
                "cool_setpoint_f": 73.5,
            },
        ]
    )

    assert [sample.setpoint_changed for sample in samples] == [False, True]


def test_build_query_uses_configured_weather_and_hvac_measurements():
    query = build_query(
        bucket="energy",
        start="-14d",
        sample_minutes=10,
        outdoor_measurement="ecowitt.outdoor",
        outdoor_temp_field="temperature",
        solar_field="solar_radiation",
    )

    assert 'r._measurement == "ecowitt.outdoor"' in query
    assert 'r._measurement == "hvac.thermostat"' in query
    assert 'r._measurement == "hvac.comfortnet"' in query
    assert "aggregateWindow(every: 10m, fn: mean" in query
    assert "outdoor_temp_f: r.temperature" in query
    assert "solar_radiation_w_m2: r.solar_radiation" in query
    assert '|> sort(columns: ["_time"])' in query
