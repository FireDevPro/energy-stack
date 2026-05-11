"""Unit tests for the Ecowitt push receiver."""
from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

import app


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
    # If the parser receives the raw '+' form it must still work.
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

def _real_world_payload() -> dict[str, str]:
    """GW1200B v1.4.7 + WN32 (WH26 slot) + WS90 push.

    With the WN32 paired the gateway sends a *single* outdoor temp/humidity
    pair (``tempf``/``humidity``) which is the WN32's shaded reading; the
    WS90's onboard temp/humidity is shadowed and not separately reported.
    """
    return {
        "PASSKEY": "ABC123DEF456",
        "stationtype": "GW1200B_V1.4.7",
        "dateutc": "2026-05-11 19:00:00",
        "model": "GW1200B",
        # GW1200 internal (WH25 block in livedata)
        "tempinf": "71.8",
        "humidityin": "34",
        "baromrelin": "29.51",
        "baromabsin": "29.51",
        # Outdoor slot -> WN32 wins (shaded N/E wall)
        "tempf": "75.0",
        "humidity": "38",
        # WS90: wind, solar, UV, rain
        "windspeedmph": "0.0",
        "windgustmph": "0.0",
        "winddir": "224",
        "maxdailygust": "1.79",
        "solarradiation": "0.55",
        "uv": "0",
        "rrain_piezo": "0.00",
        "erain_piezo": "0.00",
        "drain_piezo": "0.00",
        "srain_piezo": "0",
    }


def test_build_point_real_world_payload_writes_canonical_schema():
    p = app.build_point(_real_world_payload())
    assert p is not None
    lp = p.to_line_protocol()
    # Measurement and tag
    assert lp.startswith("ecowitt.weather,gateway=ABC123DEF456 ")
    # Canonical outdoor (WN32 via WH26 slot)
    assert "outdoor_temp_f=75" in lp
    assert "outdoor_rh_pct=38" in lp
    assert "outdoor_dewpoint_f=" in lp
    # WS90 wind + solar + baro
    assert "wind_mph=0" in lp
    assert "solar_wm2=0.55" in lp
    assert "pressure_inhg=29.51" in lp
    assert "wind_dir_deg=224" in lp
    assert "wind_gust_max_daily_mph=1.79" in lp
    assert "uv_index=0" in lp
    # Piezo rain (state stays integer)
    assert "rain_state=0i" in lp
    assert "rain_event_in=0" in lp
    assert "rain_daily_in=0" in lp
    # Indoor + abs baro
    assert "indoor_temp_f=71.8" in lp
    assert "indoor_rh_pct=34" in lp
    assert "baro_abs_inhg=29.51" in lp
    # No WS90 temp/RH fields -- WN32 shadows them in the WH26 slot.
    assert "ws90_temp_f" not in lp
    assert "ws90_rh_pct" not in lp


def test_build_point_uses_dateutc_for_timestamp():
    p = app.build_point(_real_world_payload())
    assert p is not None
    lp = p.to_line_protocol()
    expected_ts = int(datetime(2026, 5, 11, 19, 0, 0, tzinfo=timezone.utc).timestamp())
    assert lp.endswith(f" {expected_ts}")


def test_build_point_emits_wh31_channel_fields_when_present():
    # Simulates tomorrow's WN31 paired on channel 1. The parser should pick
    # it up generically without configuration.
    form = _real_world_payload()
    form["temp1f"] = "68.4"
    form["humidity1"] = "72"

    p = app.build_point(form)
    assert p is not None
    lp = p.to_line_protocol()
    assert "ch1_temp_f=68.4" in lp
    assert "ch1_rh_pct=72" in lp
    assert "ch1_dewpoint_f=" in lp
    # Canonical outdoor still sourced from WN32, not from channel 1.
    assert "outdoor_temp_f=75" in lp


def test_build_point_omits_unpaired_wh31_channels():
    # Real-world payload has no WH31 channels paired; none of the ch*_ fields
    # should appear in the line protocol.
    p = app.build_point(_real_world_payload())
    assert p is not None
    lp = p.to_line_protocol()
    for ch in range(1, 9):
        assert f"ch{ch}_temp_f" not in lp
        assert f"ch{ch}_rh_pct" not in lp
        assert f"ch{ch}_dewpoint_f" not in lp


def test_build_point_supports_multiple_simultaneous_channels():
    # Stress test: WN31 has 8 dip-switch channels and we should map all of
    # them. Tomorrow's deployment is one channel; design for the upper bound.
    form = _real_world_payload()
    for ch in range(1, 9):
        form[f"temp{ch}f"] = f"{60 + ch}.0"
        form[f"humidity{ch}"] = f"{50 + ch}"

    p = app.build_point(form)
    assert p is not None
    lp = p.to_line_protocol()
    for ch in range(1, 9):
        assert f"ch{ch}_temp_f={60 + ch}" in lp
        assert f"ch{ch}_rh_pct={50 + ch}" in lp


def test_build_point_returns_none_for_handshake_only():
    p = app.build_point({"PASSKEY": "ABC", "dateutc": "2026-05-11 12:00:00"})
    assert p is None


def test_build_point_handles_partial_payload():
    # Wind sensor offline mid-storm but everything else reports.
    form = _real_world_payload()
    form.pop("windspeedmph")
    form.pop("windgustmph")
    p = app.build_point(form)
    assert p is not None
    lp = p.to_line_protocol()
    assert "wind_mph" not in lp
    assert "wind_gust_mph" not in lp
    # Outdoor temp still present.
    assert "outdoor_temp_f=75" in lp


def test_build_point_ignores_garbage_numeric_values():
    form = _real_world_payload()
    form["tempf"] = "not-a-number"
    p = app.build_point(form)
    assert p is not None
    lp = p.to_line_protocol()
    # Outdoor temp silently dropped, not crashed.
    assert "outdoor_temp_f" not in lp
    # WS90-side fields still present.
    assert "solar_wm2=0.55" in lp


def test_build_point_tags_unknown_gateway_when_passkey_missing():
    form = _real_world_payload()
    form.pop("PASSKEY")
    p = app.build_point(form)
    assert p is not None
    lp = p.to_line_protocol()
    assert "gateway=unknown" in lp
