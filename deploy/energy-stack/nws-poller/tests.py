"""Tests for the NWS poller's pure-logic functions.

Covers:
- ``aggregate_period`` day bucketing in a configured timezone (the fix
  for the container-local-time bug).
- ``heat_active_on_date`` time-slicing of heat alerts (the fix for alerts
  being applied indiscriminately to today/tomorrow/day2).
- ``alert_window`` extraction with all four NWS time fields and missing
  bounds.

Run from this directory:
    python -m pytest tests.py
"""
from __future__ import annotations

from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from app import (
    aggregate_period,
    alert_window,
    heat_active_on_date,
)

CHICAGO = ZoneInfo("America/Chicago")
UTC = ZoneInfo("UTC")


# ---- aggregate_period ------------------------------------------------------


def test_aggregate_period_buckets_in_local_tz_not_utc():
    """A 22:00 Chicago hour (= 03:00 UTC next day) must roll up to the
    Chicago calendar day, not the UTC one."""
    periods = [
        # 2026-05-06 22:00 America/Chicago == 2026-05-07 03:00 UTC
        {
            "startTime": "2026-05-06T22:00:00-05:00",
            "temperature": 75,
            "windSpeed": "5 mph",
            "dewpoint": {"value": 18.0},
            "probabilityOfPrecipitation": {"value": 10},
        },
    ]
    # In Chicago: this period belongs to 2026-05-06.
    in_chicago = aggregate_period(periods, date(2026, 5, 6), CHICAGO)
    assert in_chicago.get("hours_covered") == 1
    # In UTC: same period belongs to 2026-05-07.
    in_utc = aggregate_period(periods, date(2026, 5, 6), UTC)
    assert in_utc == {}


def test_aggregate_period_aggregates_max_high():
    periods = [
        {"startTime": "2026-07-01T13:00:00-05:00", "temperature": 91,
         "windSpeed": "10 mph", "dewpoint": {"value": 22.0},
         "probabilityOfPrecipitation": {"value": 5}},
        {"startTime": "2026-07-01T15:00:00-05:00", "temperature": 96,
         "windSpeed": "5 to 10 mph", "dewpoint": {"value": 23.0},
         "probabilityOfPrecipitation": {"value": 0}},
    ]
    agg = aggregate_period(periods, date(2026, 7, 1), CHICAGO)
    assert agg["high_f"] == 96.0
    assert agg["low_f"] == 91.0
    assert agg["hours_covered"] == 2


def test_aggregate_period_skips_unparseable_entries():
    periods = [
        {"startTime": "not-a-date", "temperature": 75},
        {"startTime": "2026-05-06T13:00:00-05:00", "temperature": 80,
         "windSpeed": "", "dewpoint": {"value": 18.0},
         "probabilityOfPrecipitation": {"value": 0}},
    ]
    agg = aggregate_period(periods, date(2026, 5, 6), CHICAGO)
    assert agg["hours_covered"] == 1
    assert agg["high_f"] == 80.0


# ---- alert_window ----------------------------------------------------------


def test_alert_window_prefers_onset_and_ends():
    alert = {"properties": {
        "onset":     "2026-07-01T12:00:00-05:00",
        "effective": "2026-06-30T20:00:00-05:00",  # earlier; should be ignored
        "ends":      "2026-07-01T20:00:00-05:00",
        "expires":   "2026-07-02T01:00:00-05:00",  # later; should be ignored
    }}
    start, end = alert_window(alert)
    assert start == datetime(2026, 7, 1, 12, 0, tzinfo=CHICAGO)
    assert end == datetime(2026, 7, 1, 20, 0, tzinfo=CHICAGO)


def test_alert_window_falls_back_to_effective_and_expires():
    alert = {"properties": {
        "effective": "2026-07-01T08:00:00-05:00",
        "expires":   "2026-07-01T22:00:00-05:00",
    }}
    start, end = alert_window(alert)
    assert start == datetime(2026, 7, 1, 8, 0, tzinfo=CHICAGO)
    assert end == datetime(2026, 7, 1, 22, 0, tzinfo=CHICAGO)


def test_alert_window_returns_none_for_missing_bounds():
    assert alert_window({"properties": {}}) == (None, None)
    assert alert_window({}) == (None, None)


# ---- heat_active_on_date ---------------------------------------------------


def _heat_alert(onset: str | None = None, ends: str | None = None,
                event: str = "Heat Advisory") -> dict:
    props: dict = {"event": event}
    if onset is not None:
        props["onset"] = onset
    if ends is not None:
        props["ends"] = ends
    return {"properties": props}


def test_heat_active_today_only_when_alert_ends_tonight():
    """Alert ending tonight should flag today, NOT tomorrow or day2 — the
    main bug from the review."""
    today = date(2026, 7, 1)
    tomorrow = date(2026, 7, 2)
    day2 = date(2026, 7, 3)
    alerts = [_heat_alert(
        onset="2026-07-01T12:00:00-05:00",
        ends="2026-07-01T22:00:00-05:00",
    )]
    assert heat_active_on_date(alerts, today, CHICAGO) is True
    assert heat_active_on_date(alerts, tomorrow, CHICAGO) is False
    assert heat_active_on_date(alerts, day2, CHICAGO) is False


def test_heat_active_spans_two_days_when_alert_overruns():
    today = date(2026, 7, 1)
    tomorrow = date(2026, 7, 2)
    day2 = date(2026, 7, 3)
    alerts = [_heat_alert(
        onset="2026-07-01T18:00:00-05:00",
        ends="2026-07-02T20:00:00-05:00",
    )]
    assert heat_active_on_date(alerts, today, CHICAGO) is True
    assert heat_active_on_date(alerts, tomorrow, CHICAGO) is True
    assert heat_active_on_date(alerts, day2, CHICAGO) is False


def test_heat_active_open_ended_alert_treated_as_overlapping():
    """An alert with no end time is treated as ongoing — defensive for the
    case where NWS hasn't published an end yet."""
    today = date(2026, 7, 1)
    tomorrow = date(2026, 7, 2)
    alerts = [_heat_alert(onset="2026-07-01T12:00:00-05:00", ends=None)]
    assert heat_active_on_date(alerts, today, CHICAGO) is True
    assert heat_active_on_date(alerts, tomorrow, CHICAGO) is True


def test_non_heat_alerts_ignored():
    today = date(2026, 7, 1)
    alerts = [
        {"properties": {"event": "Severe Thunderstorm Warning",
                         "onset": "2026-07-01T14:00:00-05:00",
                         "ends":  "2026-07-01T16:00:00-05:00"}}
    ]
    assert heat_active_on_date(alerts, today, CHICAGO) is False


def test_heat_keywords_match_case_insensitive():
    today = date(2026, 7, 1)
    for event in ("Heat Advisory", "EXCESSIVE HEAT WARNING", "heat warning"):
        alerts = [_heat_alert(
            onset="2026-07-01T12:00:00-05:00",
            ends="2026-07-01T22:00:00-05:00",
            event=event,
        )]
        assert heat_active_on_date(alerts, today, CHICAGO) is True, event


def test_alert_starting_after_target_day_does_not_match():
    today = date(2026, 7, 1)
    alerts = [_heat_alert(
        onset="2026-07-02T12:00:00-05:00",
        ends="2026-07-02T22:00:00-05:00",
    )]
    assert heat_active_on_date(alerts, today, CHICAGO) is False
