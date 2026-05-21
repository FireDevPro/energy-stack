"""Tests for the ComEd Hourly Pricing poller's response-parsing helpers.

The ComEd HP API returns JSON arrays of {millisUTC, price} for the 5-minute
feed and {price} for the current-hour-average. Parsers normalize these
into (timestamp, price) tuples used by the writer.

HTTP layer not exercised here.

Run from this directory:
    python -m pytest tests.py
"""
from __future__ import annotations

from .poller import parse_current_hour_avg, parse_latest_5min


# ---- parse_current_hour_avg ----------------------------------------------


def test_parse_current_hour_avg_returns_float():
    assert parse_current_hour_avg([{"price": "3.4"}]) == 3.4


def test_parse_current_hour_avg_handles_negative_pricing():
    """Negative prices happen during oversupply (e.g. high wind, low load)."""
    assert parse_current_hour_avg([{"price": "-1.2"}]) == -1.2


def test_parse_current_hour_avg_empty_returns_none():
    assert parse_current_hour_avg([]) is None


def test_parse_current_hour_avg_takes_first_entry():
    """API may return multiple entries — first one is current."""
    data = [{"price": "5.0"}, {"price": "99.0"}]
    assert parse_current_hour_avg(data) == 5.0


# ---- parse_latest_5min ---------------------------------------------------


def test_parse_latest_5min_picks_max_millis():
    """API returns entries unordered; the parser picks the largest
    millisUTC, not the first or last entry by position."""
    data = [
        {"millisUTC": "1700000000000", "price": "3.0"},
        {"millisUTC": "1700000300000", "price": "5.5"},  # newest
        {"millisUTC": "1700000150000", "price": "4.1"},
    ]
    assert parse_latest_5min(data) == (1700000300000, 5.5)


def test_parse_latest_5min_returns_int_millis():
    """API returns millisUTC as a string — parser must coerce to int."""
    data = [{"millisUTC": "1700000000000", "price": "3.0"}]
    result = parse_latest_5min(data)
    assert result is not None  # non-empty data; parser returns tuple
    millis, _ = result
    assert isinstance(millis, int)
    assert millis == 1700000000000


def test_parse_latest_5min_returns_float_price():
    data = [{"millisUTC": "1700000000000", "price": "3.456"}]
    result = parse_latest_5min(data)
    assert result is not None
    _, price = result
    assert isinstance(price, float)
    assert price == 3.456


def test_parse_latest_5min_empty_returns_none():
    assert parse_latest_5min([]) is None


def test_parse_latest_5min_single_entry():
    data = [{"millisUTC": "1700000000000", "price": "3.0"}]
    assert parse_latest_5min(data) == (1700000000000, 3.0)


def test_parse_latest_5min_handles_negative_price():
    """Negative real-time prices are real, must round-trip cleanly."""
    data = [
        {"millisUTC": "1700000000000", "price": "2.0"},
        {"millisUTC": "1700000300000", "price": "-1.5"},
    ]
    assert parse_latest_5min(data) == (1700000300000, -1.5)


def test_parse_latest_5min_handles_string_millis_lexicographic_trap():
    """Critical: millis is a STRING in the API; max() with key=int must
    sort numerically, not lexicographically. A naive sort would put
    '999...' (10 chars) ahead of '1700...' (13 chars)."""
    data = [
        {"millisUTC": "999000000000", "price": "3.0"},      # 12 chars
        {"millisUTC": "1700000000000", "price": "5.0"},     # 13 chars, actually newer
    ]
    result = parse_latest_5min(data)
    assert result is not None
    millis, price = result
    assert millis == 1700000000000
    assert price == 5.0
