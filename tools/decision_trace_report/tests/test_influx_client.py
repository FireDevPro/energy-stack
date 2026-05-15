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


def test_fetch_comed_prices_normalizes_value_to_price_cents(influx_url):
    """The §3 contract: rows MUST expose `price_cents` (semantic field name)
    + `_time` as a datetime — not the raw `_value` / `_field` shape that
    comed.prices stores in InfluxDB.

    This is the regression guard for the Codex P1 row-shape bug: §3 was
    written against `{_time: ISO str, price_cents: float}` but the unpivoted
    comed.prices query actually returns `{_time: datetime, _value: float,
    _field: "price_cents_per_kwh"}`. Without this test, §3 hard-crashes
    in production.
    """
    from datetime import datetime, timezone
    fake_record = MagicMock()
    fake_record.values = {
        "_field": "price_cents_per_kwh",
        "_value": 12.5,
        "_measurement": "comed.prices",
        "result": "_result",
        "table": 0,
    }
    fake_record.get_time.return_value = datetime(2026, 5, 15, 18, 0, tzinfo=timezone.utc)
    fake_table = MagicMock()
    fake_table.records = [fake_record]
    fake_query_api = MagicMock()
    fake_query_api.query.return_value = [fake_table]

    client = InfluxClient(influx_url, "t", "o", "energy", query_api=fake_query_api)
    rows = client.fetch_comed_prices_above("2026-05-15", threshold_cents=10.0)

    assert len(rows) == 1
    row = rows[0]
    assert row["price_cents"] == 12.5
    assert isinstance(row["_time"], datetime)
    assert row["_time"] == datetime(2026, 5, 15, 18, 0, tzinfo=timezone.utc)
    # `_value` MUST NOT survive — sections speak `price_cents`.
    assert "_value" not in row


def test_last_write_time_returns_utc_datetime(influx_url):
    """last_write_time queries `max(_time)` across the measurement and
    returns a timezone-aware UTC datetime, or None if no rows."""
    from datetime import datetime, timezone
    fake_record = MagicMock()
    fake_record.values = {}
    fake_record.get_time.return_value = datetime(2026, 5, 15, 17, 0, tzinfo=timezone.utc)
    fake_table = MagicMock()
    fake_table.records = [fake_record]
    fake_query_api = MagicMock()
    fake_query_api.query.return_value = [fake_table]

    client = InfluxClient(influx_url, "t", "o", "energy", query_api=fake_query_api)
    last = client.last_write_time("comed.prices")

    flux = fake_query_api.query.call_args[0][0]
    assert 'r._measurement == "comed.prices"' in flux
    # Flux `last()` is per-table-per-series — for multi-series
    # measurements like refoss.channel (one series per channel tag)
    # it returns the last point of whichever series Flux processes
    # first, NOT the latest write across the measurement. We need
    # `group()` to collapse series + `max(column: "_time")` to pick
    # the genuinely-latest timestamp.
    assert "|> group()" in flux
    assert 'max(column: "_time")' in flux
    assert "|> last()" not in flux
    assert last == datetime(2026, 5, 15, 17, 0, tzinfo=timezone.utc)


def test_last_write_time_returns_max_across_multiple_tables(influx_url):
    """Codex P2 regression: with multiple tables (multi-series
    measurement, no group()) the OLDER timestamp can appear in the
    first table. We must return the maximum across ALL tables, not
    the first record we see."""
    from datetime import datetime, timezone
    older = MagicMock()
    older.values = {}
    older.get_time.return_value = datetime(2026, 5, 15, 10, 0, tzinfo=timezone.utc)
    newer = MagicMock()
    newer.values = {}
    newer.get_time.return_value = datetime(2026, 5, 15, 17, 0, tzinfo=timezone.utc)
    # First table has the OLDER record — Flux's iteration order can be
    # this way for multi-series. group() in the query collapses to one
    # table; this test asserts that even WITHOUT trusting the query
    # collapse, the Python side picks max across whatever it receives.
    t_old = MagicMock(); t_old.records = [older]
    t_new = MagicMock(); t_new.records = [newer]
    fake_query_api = MagicMock()
    fake_query_api.query.return_value = [t_old, t_new]

    client = InfluxClient(influx_url, "t", "o", "energy", query_api=fake_query_api)
    last = client.last_write_time("refoss.channel")

    assert last == datetime(2026, 5, 15, 17, 0, tzinfo=timezone.utc)


def test_last_write_time_returns_none_for_empty_result(influx_url):
    fake_query_api = MagicMock()
    fake_query_api.query.return_value = []
    client = InfluxClient(influx_url, "t", "o", "energy", query_api=fake_query_api)
    assert client.last_write_time("nws.forecast") is None
