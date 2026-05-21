"""Unit tests for the Ecowitt push receiver."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

import pytest

from . import app


def _line(pt: Any) -> str:
    """Test helper: serialise an influxdb_client Point to its line-protocol
    string. Wrapped in a typed shim because influxdb_client's stubs lack
    annotations on ``Point.to_line_protocol``."""
    result: str = pt.to_line_protocol()
    return result


# ---------- dewpoint ----------

def test_dewpoint_saturated_equals_temperature():
    # At 100% RH dewpoint == temperature.
    dp = app.compute_dewpoint_f(72.0, 100.0)
    assert dp is not None
    assert math.isclose(dp, 72.0, abs_tol=0.05)


def test_dewpoint_dry_air_well_below_temp():
    dp = app.compute_dewpoint_f(80.0, 30.0)
    assert dp is not None
    # NWS dewpoint calculator gives ~45.6°F for 80°F / 30% RH.
    assert math.isclose(dp, 45.6, abs_tol=0.5)


def test_dewpoint_humid_summer_day():
    # 90°F / 70% RH -> dewpoint ~78.5°F per NWS calculator.
    dp = app.compute_dewpoint_f(90.0, 70.0)
    assert dp is not None
    assert math.isclose(dp, 78.5, abs_tol=0.5)


def test_dewpoint_returns_none_for_missing_inputs():
    assert app.compute_dewpoint_f(None, 50.0) is None
    assert app.compute_dewpoint_f(70.0, None) is None
    assert app.compute_dewpoint_f(None, None) is None


def test_dewpoint_rejects_invalid_humidity():
    assert app.compute_dewpoint_f(70.0, 0.0) is None
    assert app.compute_dewpoint_f(70.0, -5.0) is None
    assert app.compute_dewpoint_f(70.0, 105.0) is None


# ---------- dateutc parser ----------

def test_parse_dateutc_standard_format():
    ts = app.parse_dateutc("2026-05-11 14:32:00")
    assert ts == datetime(2026, 5, 11, 14, 32, 0, tzinfo=timezone.utc)


def test_parse_dateutc_with_plus():
    ts = app.parse_dateutc("2026-05-11+14:32:00")
    assert ts == datetime(2026, 5, 11, 14, 32, 0, tzinfo=timezone.utc)


def test_parse_dateutc_falls_back_on_malformed():
    before = datetime.now(timezone.utc)
    ts = app.parse_dateutc("not-a-date")
    after = datetime.now(timezone.utc)
    assert before <= ts <= after


def test_parse_dateutc_falls_back_on_missing():
    before = datetime.now(timezone.utc)
    ts = app.parse_dateutc(None)
    after = datetime.now(timezone.utc)
    assert before <= ts <= after


# ---------- payload → point mapping ----------

def _payload_with_shaded_and_sun() -> dict[str, str]:
    """GW1200B + WS90 (sun, on tempf/humidity) + WN31 on channel 1 (shaded).

    This is the steady-state architecture: the WH26/WH32 outdoor slot is
    intentionally empty so the WS90's onboard temp/RH fills tempf/humidity
    as the sun comparator, and a WH31 channel provides the shaded canonical
    reading.
    """
    return {
        "PASSKEY": "ABC123DEF456",
        "stationtype": "GW1200B_V1.4.7",
        "dateutc": "2026-05-11 19:00:00",
        "model": "GW1200B",
        # GW1200 internal
        "tempinf": "71.8",
        "humidityin": "34",
        "baromrelin": "29.51",
        "baromabsin": "29.51",
        # WS90 onboard (sun-exposed pergola)
        "tempf": "82.4",
        "humidity": "31",
        # WS90 wind/solar/rain/UV
        "windspeedmph": "3.2",
        "windgustmph": "5.8",
        "winddir": "224",
        "maxdailygust": "10.4",
        "solarradiation": "742.1",
        "uv": "6",
        "rrain_piezo": "0.00",
        "erain_piezo": "0.00",
        "drain_piezo": "0.00",
        "srain_piezo": "0",
        # WN31 on channel 1 (shaded N/E wall) -- canonical
        "temp1f": "74.6",
        "humidity1": "38",
    }


# ---- canonical shaded outdoor (WN31 on ECOWITT_SHADED_CHANNEL) ----

def test_outdoor_sourced_from_configured_shaded_channel():
    p = app.build_point(_payload_with_shaded_and_sun(), shaded_channel=1)
    assert p is not None
    lp = _line(p)
    # outdoor_* IS the shaded WN31 (channel 1), not the sun-exposed WS90
    assert "outdoor_temp_f=74.6" in lp
    assert "outdoor_rh_pct=38" in lp
    assert "outdoor_dewpoint_f=" in lp


def test_outdoor_omitted_when_shaded_channel_unset():
    # The "WN31 not yet paired" gap. Service must not silently substitute the
    # WS90 sun reading -- downstream analysis would be biased.
    p = app.build_point(_payload_with_shaded_and_sun(), shaded_channel=None)
    assert p is not None
    lp = _line(p)
    assert "outdoor_temp_f" not in lp
    assert "outdoor_rh_pct" not in lp
    assert "outdoor_dewpoint_f" not in lp
    # WS90 sun comparator still present.
    assert "ws90_temp_f=82.4" in lp


def test_outdoor_omitted_when_shaded_channel_silent():
    # Shaded sensor configured but the gateway didn't include its channel in
    # this push (battery dead, lost RF, etc.). Treated as a data gap, not as
    # a fall-through to WS90.
    form = _payload_with_shaded_and_sun()
    form.pop("temp1f")
    form.pop("humidity1")

    p = app.build_point(form, shaded_channel=1)
    assert p is not None
    lp = _line(p)
    assert "outdoor_temp_f" not in lp
    assert "outdoor_rh_pct" not in lp
    assert "outdoor_dewpoint_f" not in lp


# ---- WS90 sun comparator (always emitted on tempf/humidity) ----

def test_ws90_onboard_always_emitted():
    p = app.build_point(_payload_with_shaded_and_sun(), shaded_channel=1)
    assert p is not None
    lp = _line(p)
    assert "ws90_temp_f=82.4" in lp
    assert "ws90_rh_pct=31" in lp
    assert "ws90_dewpoint_f=" in lp


def test_ws90_onboard_emitted_even_without_shaded_channel():
    p = app.build_point(_payload_with_shaded_and_sun(), shaded_channel=None)
    assert p is not None
    lp = _line(p)
    assert "ws90_temp_f=82.4" in lp
    assert "ws90_rh_pct=31" in lp


def test_ws90_and_outdoor_are_independent_values():
    # The sun vs shade split is the whole point. Make sure they don't get
    # collapsed.
    p = app.build_point(_payload_with_shaded_and_sun(), shaded_channel=1)
    assert p is not None
    lp = _line(p)
    assert "outdoor_temp_f=74.6" in lp
    assert "ws90_temp_f=82.4" in lp


# ---- WS90 wind / solar / rain / GW1200 (unchanged) ----

def test_ws90_wind_solar_baro_fields_present():
    p = app.build_point(_payload_with_shaded_and_sun(), shaded_channel=1)
    assert p is not None
    lp = _line(p)
    assert "wind_mph=3.2" in lp
    assert "wind_gust_mph=5.8" in lp
    assert "wind_dir_deg=224" in lp
    assert "wind_gust_max_daily_mph=10.4" in lp
    assert "solar_wm2=742.1" in lp
    assert "uv_index=6" in lp
    assert "pressure_inhg=29.51" in lp


def test_rain_fields_use_correct_types():
    p = app.build_point(_payload_with_shaded_and_sun(), shaded_channel=1)
    assert p is not None
    lp = _line(p)
    assert "rain_state=0i" in lp  # int
    assert "rain_event_in=0" in lp
    assert "rain_daily_in=0" in lp


def test_gw1200_internal_fields_present():
    p = app.build_point(_payload_with_shaded_and_sun(), shaded_channel=1)
    assert p is not None
    lp = _line(p)
    assert "indoor_temp_f=71.8" in lp
    assert "indoor_rh_pct=34" in lp
    assert "baro_abs_inhg=29.51" in lp


# ---- other WH31 channels (not the shaded canonical) ----

def test_other_channels_emitted_as_ch_fields():
    # WN31 on channel 1 is the shaded canonical; a separate WH31 on channel 3
    # (e.g. basement, garage) is just supplemental.
    form = _payload_with_shaded_and_sun()
    form["temp3f"] = "64.2"
    form["humidity3"] = "55"

    p = app.build_point(form, shaded_channel=1)
    assert p is not None
    lp = _line(p)
    # Shaded canonical still on channel 1
    assert "outdoor_temp_f=74.6" in lp
    # Supplemental channel 3 emitted as ch3_*
    assert "ch3_temp_f=64.2" in lp
    assert "ch3_rh_pct=55" in lp
    assert "ch3_dewpoint_f=" in lp


def test_shaded_channel_not_duplicated_as_ch_fields():
    # Channel 1 hosts the shaded sensor -- it populates outdoor_* and should
    # NOT also appear as ch1_*. Double-writing the same physical sensor under
    # two field names would confuse downstream analysis.
    p = app.build_point(_payload_with_shaded_and_sun(), shaded_channel=1)
    assert p is not None
    lp = _line(p)
    assert "ch1_temp_f" not in lp
    assert "ch1_rh_pct" not in lp
    assert "ch1_dewpoint_f" not in lp


def test_unpaired_channels_omitted():
    p = app.build_point(_payload_with_shaded_and_sun(), shaded_channel=1)
    assert p is not None
    lp = _line(p)
    for ch in (2, 3, 4, 5, 6, 7, 8):
        assert f"ch{ch}_temp_f" not in lp


def test_multiple_supplemental_channels_emitted():
    form = _payload_with_shaded_and_sun()
    for ch in (2, 4, 7):
        form[f"temp{ch}f"] = f"{60 + ch}.0"
        form[f"humidity{ch}"] = f"{50 + ch}"

    p = app.build_point(form, shaded_channel=1)
    assert p is not None
    lp = _line(p)
    for ch in (2, 4, 7):
        assert f"ch{ch}_temp_f={60 + ch}" in lp
        assert f"ch{ch}_rh_pct={50 + ch}" in lp


# ---- timestamp ----

def test_build_point_uses_dateutc_for_timestamp():
    p = app.build_point(_payload_with_shaded_and_sun(), shaded_channel=1)
    assert p is not None
    lp = _line(p)
    expected_ts = int(datetime(2026, 5, 11, 19, 0, 0, tzinfo=timezone.utc).timestamp())
    assert lp.endswith(f" {expected_ts}")


# ---- empty / malformed / tag handling ----

def test_build_point_returns_none_for_handshake_only():
    p = app.build_point(
        {"PASSKEY": "ABC", "dateutc": "2026-05-11 12:00:00"},
        shaded_channel=1,
    )
    assert p is None


def test_build_point_handles_partial_payload():
    form = _payload_with_shaded_and_sun()
    form.pop("windspeedmph")
    form.pop("windgustmph")
    p = app.build_point(form, shaded_channel=1)
    assert p is not None
    lp = _line(p)
    assert "wind_mph" not in lp
    assert "wind_gust_mph" not in lp
    # Both temp streams still present.
    assert "outdoor_temp_f=74.6" in lp
    assert "ws90_temp_f=82.4" in lp


def test_build_point_ignores_garbage_numeric_values():
    form = _payload_with_shaded_and_sun()
    form["tempf"] = "not-a-number"
    p = app.build_point(form, shaded_channel=1)
    assert p is not None
    lp = _line(p)
    # ws90 sun comparator silently dropped, not crashed.
    assert "ws90_temp_f" not in lp
    # Shaded outdoor unaffected (separate sensor).
    assert "outdoor_temp_f=74.6" in lp


def test_build_point_tags_unknown_gateway_when_passkey_missing():
    form = _payload_with_shaded_and_sun()
    form.pop("PASSKEY")
    p = app.build_point(form, shaded_channel=1)
    assert p is not None
    lp = _line(p)
    assert "gateway=unknown" in lp


# ---- Config.from_env shaded-channel parsing ----

def test_config_shaded_channel_unset(monkeypatch):
    monkeypatch.delenv("ECOWITT_SHADED_CHANNEL", raising=False)
    monkeypatch.setenv("INFLUXDB_TOKEN", "x")
    monkeypatch.setenv("INFLUXDB_ORG", "x")
    monkeypatch.setenv("INFLUXDB_BUCKET", "x")
    cfg = app.Config.from_env()
    assert cfg.shaded_channel is None


def test_config_shaded_channel_set(monkeypatch):
    monkeypatch.setenv("ECOWITT_SHADED_CHANNEL", "3")
    monkeypatch.setenv("INFLUXDB_TOKEN", "x")
    monkeypatch.setenv("INFLUXDB_ORG", "x")
    monkeypatch.setenv("INFLUXDB_BUCKET", "x")
    cfg = app.Config.from_env()
    assert cfg.shaded_channel == 3


def test_config_shaded_channel_empty_string_is_unset(monkeypatch):
    monkeypatch.setenv("ECOWITT_SHADED_CHANNEL", "")
    monkeypatch.setenv("INFLUXDB_TOKEN", "x")
    monkeypatch.setenv("INFLUXDB_ORG", "x")
    monkeypatch.setenv("INFLUXDB_BUCKET", "x")
    cfg = app.Config.from_env()
    assert cfg.shaded_channel is None


def test_config_shaded_channel_invalid_value_exits(monkeypatch):
    monkeypatch.setenv("ECOWITT_SHADED_CHANNEL", "abc")
    monkeypatch.setenv("INFLUXDB_TOKEN", "x")
    monkeypatch.setenv("INFLUXDB_ORG", "x")
    monkeypatch.setenv("INFLUXDB_BUCKET", "x")
    with pytest.raises(SystemExit):
        app.Config.from_env()


def test_config_shaded_channel_out_of_range_exits(monkeypatch):
    monkeypatch.setenv("ECOWITT_SHADED_CHANNEL", "9")
    monkeypatch.setenv("INFLUXDB_TOKEN", "x")
    monkeypatch.setenv("INFLUXDB_ORG", "x")
    monkeypatch.setenv("INFLUXDB_BUCKET", "x")
    with pytest.raises(SystemExit):
        app.Config.from_env()
