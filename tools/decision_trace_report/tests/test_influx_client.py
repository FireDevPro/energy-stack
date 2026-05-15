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
