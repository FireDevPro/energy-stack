"""Tests for InfluxClient — pure wrapper, no live calls."""
from unittest.mock import MagicMock

import pytest

from tools.decision_trace_report.influx_client import InfluxClient


def test_fetch_hvac_decisions_builds_flux_with_target_date(influx_url):
    """fetch_hvac_decisions filters by decision_for_date tag."""
    fake_query_api = MagicMock()
    fake_query_api.query.return_value = []  # no rows

    client = InfluxClient(
        url=influx_url, token="t", org="o", bucket="energy",
        query_api=fake_query_api,
    )
    client.fetch_hvac_decisions("2026-05-15")

    flux = fake_query_api.query.call_args[0][0]
    assert 'r._measurement == "hvac.decisions"' in flux
    assert 'r.decision_for_date == "2026-05-15"' in flux


def test_fetch_hvac_decisions_pivots_fields_into_rows(influx_url):
    """The function returns one dict per decision row, with all fields
    flattened (high_f, day_type, reason, etc.)."""
    fake_record = MagicMock()
    fake_record.values = {
        "decision_for_date": "2026-05-15",
        "day_type": "NORMAL",
        "high_f": 80.0,
        "reason": "high_75_to_84",
    }
    fake_record.get_time.return_value = "2026-05-15T02:00:00Z"
    fake_table = MagicMock()
    fake_table.records = [fake_record]
    fake_query_api = MagicMock()
    fake_query_api.query.return_value = [fake_table]

    client = InfluxClient(
        url=influx_url, token="t", org="o", bucket="energy",
        query_api=fake_query_api,
    )
    rows = client.fetch_hvac_decisions("2026-05-15")

    assert len(rows) == 1
    assert rows[0]["day_type"] == "NORMAL"
    assert rows[0]["high_f"] == 80.0
    assert rows[0]["decision_for_date"] == "2026-05-15"


def test_fetch_precool_window_filters_by_target_date(influx_url):
    fake_query_api = MagicMock()
    fake_query_api.query.return_value = []
    client = InfluxClient(influx_url, "t", "o", "energy", query_api=fake_query_api)
    client.fetch_precool_window("2026-05-15")
    flux = fake_query_api.query.call_args[0][0]
    assert 'r._measurement == "hvac.precool_window"' in flux
    assert 'r.target_date == "2026-05-15"' in flux


def test_fetch_hvac_actions_for_ct_day_cdt(influx_url):
    """Summer (CDT, UTC-5): CT day 2026-05-15 -> UTC [05:00 May 15, 05:00 May 16)."""
    fake_query_api = MagicMock()
    fake_query_api.query.return_value = []
    client = InfluxClient(influx_url, "t", "o", "energy", query_api=fake_query_api)
    client.fetch_hvac_actions("2026-05-15")
    flux = fake_query_api.query.call_args[0][0]
    assert 'r._measurement == "hvac.actions"' in flux
    assert "2026-05-15T05:00:00Z" in flux
    assert "2026-05-16T05:00:00Z" in flux


def test_fetch_hvac_actions_for_ct_day_cst(influx_url):
    """Winter (CST, UTC-6): CT day 2026-01-15 -> UTC [06:00 Jan 15, 06:00 Jan 16).
    Proves the tz arithmetic isn't hardcoded to CDT — DST hygiene check."""
    fake_query_api = MagicMock()
    fake_query_api.query.return_value = []
    client = InfluxClient(influx_url, "t", "o", "energy", query_api=fake_query_api)
    client.fetch_hvac_actions("2026-01-15")
    flux = fake_query_api.query.call_args[0][0]
    assert "2026-01-15T06:00:00Z" in flux
    assert "2026-01-16T06:00:00Z" in flux


def test_fetch_hvac_actions_by_range_accepts_arbitrary_ct_window(influx_url):
    """Range-mode variant: arbitrary CT datetime window for custom
    --from/--to mode. Date-mode methods delegate here; this is the
    canonical primitive."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    fake_query_api = MagicMock()
    fake_query_api.query.return_value = []
    client = InfluxClient(influx_url, "t", "o", "energy", query_api=fake_query_api)
    ct = ZoneInfo("America/Chicago")
    client.fetch_hvac_actions_by_range(
        start_ct=datetime(2026, 5, 14, 13, 0, tzinfo=ct),
        end_ct=datetime(2026, 5, 14, 19, 30, tzinfo=ct),
    )
    flux = fake_query_api.query.call_args[0][0]
    assert 'r._measurement == "hvac.actions"' in flux
    # CT 13:00 CDT = UTC 18:00; CT 19:30 CDT = UTC 00:30 next day
    assert "2026-05-14T18:00:00Z" in flux
    assert "2026-05-15T00:30:00Z" in flux


def test_fetch_comed_prices_spikes_only_date_mode(influx_url):
    """Date-mode fetch_comed_prices_above filters by threshold + CT day."""
    fake_query_api = MagicMock()
    fake_query_api.query.return_value = []
    client = InfluxClient(influx_url, "t", "o", "energy", query_api=fake_query_api)
    client.fetch_comed_prices_above("2026-05-15", threshold_cents=10.0)
    flux = fake_query_api.query.call_args[0][0]
    assert 'r._measurement == "comed.prices"' in flux
    assert "r._value >= 10.0" in flux


def test_fetch_comed_prices_above_by_range_accepts_arbitrary_window(influx_url):
    """Range-mode variant for custom --from/--to."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    fake_query_api = MagicMock()
    fake_query_api.query.return_value = []
    client = InfluxClient(influx_url, "t", "o", "energy", query_api=fake_query_api)
    ct = ZoneInfo("America/Chicago")
    client.fetch_comed_prices_above_by_range(
        start_ct=datetime(2026, 5, 14, 13, 0, tzinfo=ct),
        end_ct=datetime(2026, 5, 14, 19, 30, tzinfo=ct),
        threshold_cents=10.0,
    )
    flux = fake_query_api.query.call_args[0][0]
    assert "r._value >= 10.0" in flux
    assert "2026-05-14T18:00:00Z" in flux
    assert "2026-05-15T00:30:00Z" in flux
