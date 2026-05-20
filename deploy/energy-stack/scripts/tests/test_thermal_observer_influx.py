from __future__ import annotations

from datetime import datetime, timezone

from thermal_observer import ThermalFitResult, ThermalSample
from thermal_observer_influx import (
    build_line_protocol,
    build_query,
    json_artifact_payload,
    rows_to_samples,
    write_json_atomic,
)


def split_line_protocol(line: str) -> tuple[str, str, str]:
    separators: list[int] = []
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == " ":
            separators.append(index)
            if len(separators) == 2:
                break

    first, second = separators
    return line[:first], line[first + 1 : second], line[second + 1 :]


def make_result(accepted: bool = True) -> ThermalFitResult:
    return ThermalFitResult(
        tau_hours=9.5,
        stage1_cooling_f_per_hr=1.7,
        stage2_cooling_f_per_hr=3.1,
        solar_coupling_f_per_hr_per_w_m2=0.0012,
        intercept_f_per_hr=-0.04,
        train_sample_count=120,
        test_sample_count=30,
        total_interval_count=150,
        filter_counts={"gap": 3, "heating_active": 2, "valid": 150},
        train_rmse_f_per_sample=0.18,
        test_rmse_f_per_sample=0.22,
        persistence_rmse_f_per_sample=0.5,
        skill_score=0.56,
        accepted=accepted,
        rejection_reasons=() if accepted else ("tau_out_of_bounds",),
        fit_window_start="2026-07-01T00:00:00+00:00",
        fit_window_end="2026-07-15T00:00:00+00:00",
        sample_minutes=10,
    )


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


def test_rows_to_samples_marks_later_sample_when_setpoint_changes_by_half_degree():
    first_ts = datetime(2026, 7, 15, 12, 30, tzinfo=timezone.utc)
    second_ts = datetime(2026, 7, 15, 12, 40, tzinfo=timezone.utc)

    samples = rows_to_samples(
        [
            {
                "_time": first_ts,
                "indoor_temp_f": 74.2,
                "outdoor_temp_f": 91.5,
                "cool_setpoint_f": 73.0,
            },
            {
                "_time": second_ts,
                "indoor_temp_f": 74.4,
                "outdoor_temp_f": 91.7,
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


def test_build_line_protocol_emits_thermal_observer_point():
    generated_at = datetime(2026, 7, 15, 12, 30, tzinfo=timezone.utc)

    line = build_line_protocol(make_result(), "ecowitt.outdoor", generated_at)

    assert line.startswith("hvac.thermal_observer,")
    assert "model_version=thermal_observer.v1" in line
    assert "outdoor_measurement=ecowitt.outdoor" in line
    assert "read_only=true" in line
    assert "accepted=true" in line
    assert "tau_hours=9.5" in line
    assert "stage2_cooling_f_per_hr=3.1" in line
    assert "filter_gap=3i" in line
    assert line.endswith(" 1784118600000000000")


def test_build_line_protocol_keeps_accepted_only_as_tag_for_rejections():
    generated_at = datetime(2026, 7, 15, 12, 30, tzinfo=timezone.utc)

    line = build_line_protocol(make_result(accepted=False), "back yard,unit=west", generated_at)
    measurement_and_tags, fields, _timestamp = split_line_protocol(line)
    _measurement, tags = measurement_and_tags.split(",", 1)

    assert "outdoor_measurement=back\\ yard\\,unit\\=west" in tags
    assert "accepted=false" in tags
    assert "accepted=" not in fields
    assert line.count("accepted=") == 1
    assert 'rejection_reasons="[\\"tau_out_of_bounds\\"]"' in fields
    assert 'fit_window_start="2026-07-01T00:00:00+00:00"' in fields
    assert 'fit_window_end="2026-07-15T00:00:00+00:00"' in fields


def test_json_artifact_payload_is_stable_and_explicit():
    generated_at = datetime(2026, 7, 15, 12, 30, tzinfo=timezone.utc)

    payload = json_artifact_payload(make_result(), "ecowitt.outdoor", generated_at)

    assert list(payload) == [
        "model_version",
        "read_only",
        "outdoor_measurement",
        "generated_at",
        "result",
    ]
    assert payload["model_version"] == "thermal_observer.v1"
    assert payload["read_only"] is True
    assert payload["outdoor_measurement"] == "ecowitt.outdoor"
    assert payload["generated_at"] == "2026-07-15T12:30:00+00:00"
    assert payload["result"]["tau_hours"] == 9.5
    assert payload["result"]["filter_counts"] == {"gap": 3, "heating_active": 2, "valid": 150}


def test_write_json_atomic_creates_parent_and_replaces_file(tmp_path):
    path = tmp_path / "nested" / "thermal.json"

    write_json_atomic(path, {"value": 1})
    write_json_atomic(path, {"value": 2})

    assert path.read_text() == '{\n  "value": 2\n}\n'
    assert list(path.parent.glob("*.tmp")) == []
