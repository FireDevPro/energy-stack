"""Tests for haven-ingest's local-API mapping and device check.

The /sensors → haven.indoor mapping carries the legacy cloud field names that
Grafana and the kiosk read, so these tests pin the names, the unit conversion,
the reading timestamp, and the skip rules.

Run from deploy/energy-stack:
    python -m pytest haven_ingest/test_haven_ingest.py
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from .app import DeviceMismatch, VOC_PPB_TO_UGM3, check_info, sensors_to_point

POLLED_AT = datetime(2026, 11, 18, 15, 0, 0, tzinfo=timezone.utc)


def _sensors() -> dict[str, Any]:
    # Shape of a real /api/v1/sensors response (fw 2.5.2, 2026-11-18).
    return {
        "sensor_ready": True,
        "latest_sensor_age_s": 43,
        "temperature_c": 20.4475231170654,
        "humidity_pct": 41.9679985046387,
        "pressure_kpa": 100.708168029785,
        "co2_ppm": 401,
        "tvoc_ppb": 2,
        "pm25_ugm3": 0.876318514347076,
        "pm10_ugm3": 1.20634853839874,
        "pm25_count_cm3": 1.5306590795517,
        "pm10_count_cm3": 1.54580795764923,
        "airflow_mps": -1.05666673183441,
        "airflow_duration_s": 24.264,
    }


def _info(**over: Any) -> dict[str, Any]:
    info: dict[str, Any] = {
        "api_version": 1,
        "serial_number": "0000-6267",
        "device_id": "D2047-05924",
        "manufacturer": "HAVEN IAQ",
        "model": "Central Air Monitor",
        "product_type": "cam",
        "fw_ver": "2.5.2.9a69e2e6.0ca16ce",
        "capabilities": ["air_quality", "airflow"],
    }
    info.update(over)
    return info


# ---- sensors_to_point ----------------------------------------------------


def test_point_measurement_and_tag() -> None:
    p = sensors_to_point(_sensors(), "3645", POLLED_AT)
    assert p is not None
    assert p._name == "haven.indoor"
    assert dict(p._tags) == {"device_id": "3645"}


def test_point_keeps_legacy_field_names() -> None:
    """Grafana iaq-comparison and the kiosk read these names."""
    p = sensors_to_point(_sensors(), "3645", POLLED_AT)
    assert p is not None
    f = dict(p._fields)
    assert f["temperature_c"] == pytest.approx(20.4475231170654)
    assert f["humidity"] == pytest.approx(41.9679985046387)
    assert f["pm_mc"] == pytest.approx(0.876318514347076)
    assert f["pm_count"] == pytest.approx(1.5306590795517)
    assert f["airflow"] == pytest.approx(-1.05666673183441)
    assert f["voc_count"] == 2.0


def test_point_derives_voc_mc_with_cloud_factor() -> None:
    p = sensors_to_point(_sensors(), "3645", POLLED_AT)
    assert p is not None
    assert dict(p._fields)["voc_mc"] == pytest.approx(2 * VOC_PPB_TO_UGM3)
    assert VOC_PPB_TO_UGM3 == pytest.approx(2.3529411764705883)


def test_point_carries_local_only_fields() -> None:
    p = sensors_to_point(_sensors(), "3645", POLLED_AT)
    assert p is not None
    f = dict(p._fields)
    for name in ("pressure_kpa", "pm10_ugm3", "pm10_count_cm3", "airflow_duration_s"):
        assert name in f, f"missing field: {name}"
    assert f["sensor_age_s"] == 43.0


def test_point_omits_co2_and_float_types() -> None:
    """The CAM has no CO2 sensor; its co2_ppm is a placeholder. All written
    fields are floats so an int reading never conflicts with an existing
    float series in InfluxDB."""
    p = sensors_to_point(_sensors(), "3645", POLLED_AT)
    assert p is not None
    f = dict(p._fields)
    assert "co2_ppm" not in f
    assert all(isinstance(v, float) for v in f.values())


def test_point_time_is_reading_time() -> None:
    p = sensors_to_point(_sensors(), "3645", POLLED_AT)
    assert p is not None
    assert p._time == POLLED_AT - timedelta(seconds=43)


def test_point_time_falls_back_to_poll_time_without_age() -> None:
    s = _sensors()
    del s["latest_sensor_age_s"]
    p = sensors_to_point(s, "3645", POLLED_AT)
    assert p is not None
    assert p._time == POLLED_AT
    assert "sensor_age_s" not in dict(p._fields)


def test_point_skips_missing_and_non_numeric_values() -> None:
    s = _sensors()
    del s["pm10_ugm3"]
    s["pressure_kpa"] = None
    s["humidity_pct"] = True  # bool must not be read as 1.0
    s["airflow_mps"] = "fast"
    p = sensors_to_point(s, "3645", POLLED_AT)
    assert p is not None
    f = dict(p._fields)
    for name in ("pm10_ugm3", "pressure_kpa", "humidity", "airflow"):
        assert name not in f
    assert "temperature_c" in f


def test_point_none_when_sensor_not_ready() -> None:
    s = _sensors()
    s["sensor_ready"] = False
    assert sensors_to_point(s, "3645", POLLED_AT) is None


def test_point_none_when_sensor_ready_missing() -> None:
    s = _sensors()
    del s["sensor_ready"]
    assert sensors_to_point(s, "3645", POLLED_AT) is None


def test_point_none_when_no_readings() -> None:
    assert sensors_to_point({"sensor_ready": True, "latest_sensor_age_s": 5},
                            "3645", POLLED_AT) is None


# ---- check_info -----------------------------------------------------------


def test_check_info_accepts_central_air_monitor() -> None:
    check_info(_info(), "")
    check_info(_info(), "0000-6267")


@pytest.mark.parametrize("over", [
    {"api_version": 2},
    {"api_version": None},
    {"product_type": "ram"},
    {"product_type": "cac"},
])
def test_check_info_rejects_unsupported_device(over: dict[str, Any]) -> None:
    with pytest.raises(DeviceMismatch):
        check_info(_info(**over), "")


def test_check_info_rejects_wrong_serial() -> None:
    with pytest.raises(DeviceMismatch):
        check_info(_info(serial_number="0000-9999"), "0000-6267")
