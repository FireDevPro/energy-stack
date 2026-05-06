"""Tests for the Refoss EM16P poller's pure-logic helpers.

The Refoss EM16P JSON-RPC API's Status.Get and Config.Get responses are
nested dicts that the poller flattens into channel and system Influx
Points. These tests cover that flattening and the channel-name
extraction.

HTTP / network paths are not exercised here; they're handled live by
`requests` and the EM16P firmware doesn't have surprises beyond what's
already covered.

Run from this directory:
    python -m pytest tests.py
"""
from __future__ import annotations

from poller import (
    build_channel_point,
    build_system_point,
    parse_channel_names,
)


# ---- parse_channel_names --------------------------------------------------


def test_parse_channel_names_basic():
    config = {
        "em:0": {"name": "Mains A"},
        "em:1": {"name": "Master Bedroom"},
        "em:6": {"name": "Mains B"},
    }
    assert parse_channel_names(config) == {
        "em:0": "Mains A",
        "em:1": "Master Bedroom",
        "em:6": "Mains B",
    }


def test_parse_channel_names_falls_back_to_key_when_unnamed():
    """Channels with no name in the Refoss app fall back to the key itself."""
    config = {
        "em:0": {"name": "Mains A"},
        "em:5": {"name": ""},          # empty string
        "em:6": {"name": None},        # explicit null
        "em:7": {},                    # missing key entirely
    }
    result = parse_channel_names(config)
    assert result == {
        "em:0": "Mains A",
        "em:5": "em:5",
        "em:6": "em:6",
        "em:7": "em:7",
    }


def test_parse_channel_names_filters_to_em_prefix():
    """emmerge:N rollups, sys, wifi, and other sections must be skipped."""
    config = {
        "em:0": {"name": "Mains A"},
        "emmerge:0": {"name": "should-be-skipped"},
        "sys": {"uptime": 1234},
        "wifi": {"rssi": -42},
        "schedule:0": {"name": "also-skipped"},
    }
    result = parse_channel_names(config)
    assert result == {"em:0": "Mains A"}


def test_parse_channel_names_skips_non_dict_values():
    """A bare em:N value (e.g. firmware quirk) shouldn't break things."""
    config = {
        "em:0": {"name": "Real channel"},
        "em:1": "stringly",
        "em:2": None,
        "em:3": 42,
    }
    result = parse_channel_names(config)
    assert result == {"em:0": "Real channel"}


def test_parse_channel_names_empty_config():
    assert parse_channel_names({}) == {}


# ---- build_channel_point --------------------------------------------------


def _point_fields(p) -> dict:
    """influxdb_client.Point exposes ``_fields`` directly — use it rather
    than parsing line protocol (which escapes spaces in tag values)."""
    return dict(p._fields)


def _point_tags(p) -> dict:
    return dict(p._tags)


def test_build_channel_point_includes_all_present_fields():
    data = {
        "power": 1234.5,
        "voltage": 120.1,
        "current": 10.27,
        "pf": 0.99,
        "day_energy": 5.6,
        "day_ret_energy": 0.0,
        "week_energy": 18.2,
        "week_ret_energy": 0.0,
        "month_energy": 102.4,
        "month_ret_energy": 0.0,
    }
    p = build_channel_point("em:1", "Master Bedroom", data)
    fields = _point_fields(p)
    assert "power_w" in fields
    assert "voltage_v" in fields
    assert "current_a" in fields
    assert "power_factor" in fields
    assert "day_energy_kwh" in fields
    assert "month_ret_energy_kwh" in fields


def test_build_channel_point_skips_missing_fields():
    """Refoss firmware sometimes omits fields (esp. on freshly-rebooted
    channels); the point must skip them rather than write None."""
    data = {"power": 100.0}  # only power present
    p = build_channel_point("em:1", "Master Bedroom", data)
    fields = _point_fields(p)
    assert "power_w" in fields
    assert "voltage_v" not in fields
    assert "day_energy_kwh" not in fields


def test_build_channel_point_carries_tags():
    p = build_channel_point("em:1", "Master Bedroom", {"power": 100.0})
    tags = _point_tags(p)
    assert tags == {"channel": "em:1", "name": "Master Bedroom"}


def test_build_channel_point_handles_negative_power():
    """em:5 and em:14 had inverted CT polarity until the Refoss-app `factor`
    fix landed; the poller must accept negative power without throwing."""
    p = build_channel_point("em:5", "Master Bedroom", {"power": -50.3})
    fields = _point_fields(p)
    assert fields["power_w"] == -50.3


# ---- build_system_point ---------------------------------------------------


def test_build_system_point_full_status():
    status = {
        "sys": {"uptime": 12345, "cfg_rev": 3},
        "wifi": {"rssi": -42},
    }
    p = build_system_point(status)
    assert p is not None
    fields = _point_fields(p)
    assert "uptime_s" in fields
    assert "cfg_rev" in fields
    assert "wifi_rssi_dbm" in fields


def test_build_system_point_partial_status():
    """If only sys block is present (no wifi yet), still emit a point."""
    status = {"sys": {"uptime": 100, "cfg_rev": 1}}
    p = build_system_point(status)
    assert p is not None
    fields = _point_fields(p)
    assert "uptime_s" in fields
    assert "cfg_rev" in fields


def test_build_system_point_empty_status_returns_none():
    """If neither sys nor wifi yields any field, return None — caller must
    not write an empty point (Influx rejects a Point with no fields)."""
    assert build_system_point({}) is None
    assert build_system_point({"sys": {}, "wifi": {}}) is None


def test_build_system_point_missing_wifi_rssi():
    """wifi block present but no rssi — skip the wifi field cleanly."""
    status = {"sys": {"uptime": 50}, "wifi": {}}
    p = build_system_point(status)
    assert p is not None
    fields = _point_fields(p)
    assert "uptime_s" in fields
    assert "wifi_rssi_dbm" not in fields
