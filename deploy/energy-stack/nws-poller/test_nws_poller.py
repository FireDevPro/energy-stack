"""Tests for the NWS poller's pure-logic functions.

Covers:
- ISO 8601 duration parsing for ``validTime`` interval components.
- Grid-value expansion: variable-granularity entries flatten into 1-hour
  UTC slots so cross-variable alignment is uniform before roll-up.
- ``summarize_grid_for_date`` daily roll-up in a configured timezone (the
  same fix for the container-local-time bug as in the forecastHourly era,
  re-asserted under the gridData parser).
- ``heat_active_on_date`` time-slicing of heat alerts.
- ``alert_window`` extraction with all four NWS time fields and missing
  bounds.
- An integration check that runs the parser against a real
  ``forecastGridData`` fixture committed alongside this test file.

Run from this directory:
    python -m pytest test_nws_poller.py
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app import (
    alert_window,
    expand_grid_values,
    heat_active_on_date,
    parse_iso_duration_hours,
    summarize_grid_for_date,
)

CHICAGO = ZoneInfo("America/Chicago")
UTC = ZoneInfo("UTC")
FIXTURES = Path(__file__).parent / "tests" / "fixtures"


# ---- parse_iso_duration_hours ---------------------------------------------


def test_parse_iso_duration_hours_basic():
    assert parse_iso_duration_hours("PT1H") == 1.0
    assert parse_iso_duration_hours("PT3H") == 3.0
    assert parse_iso_duration_hours("PT6H") == 6.0


def test_parse_iso_duration_hours_with_days():
    """``validTimes`` envelope uses ``P8DT1H`` to describe the full window."""
    assert parse_iso_duration_hours("P1D") == 24.0
    assert parse_iso_duration_hours("P1DT3H") == 27.0
    assert parse_iso_duration_hours("P8DT1H") == 193.0


def test_parse_iso_duration_hours_subhour():
    """Sub-hour components surface as fractional hours; callers floor as
    needed when slotting into integer hourly grid."""
    assert parse_iso_duration_hours("PT30M") == 0.5
    assert parse_iso_duration_hours("PT15M") == 0.25
    assert parse_iso_duration_hours("PT3H30M") == 3.5


def test_parse_iso_duration_hours_unparseable_returns_zero():
    assert parse_iso_duration_hours("") == 0.0
    assert parse_iso_duration_hours("PT") == 0.0
    assert parse_iso_duration_hours("not-a-duration") == 0.0


# ---- expand_grid_values ----------------------------------------------------


def test_expand_grid_values_pt1h_emits_single_slot():
    out = expand_grid_values([
        {"validTime": "2026-05-10T00:00:00+00:00/PT1H", "value": 21.5},
    ])
    assert out == {datetime(2026, 5, 10, 0, tzinfo=timezone.utc): 21.5}


def test_expand_grid_values_pt3h_emits_three_slots_same_value():
    """A single PT3H entry expands into three hourly slots all carrying the
    same value (NWS publishes one value per interval; no interpolation)."""
    out = expand_grid_values([
        {"validTime": "2026-05-10T18:00:00+00:00/PT3H", "value": 30.0},
    ])
    assert sorted(out.keys()) == [
        datetime(2026, 5, 10, 18, tzinfo=timezone.utc),
        datetime(2026, 5, 10, 19, tzinfo=timezone.utc),
        datetime(2026, 5, 10, 20, tzinfo=timezone.utc),
    ]
    assert all(v == 30.0 for v in out.values())


def test_expand_grid_values_handles_mixed_granularity():
    """Variables transition from 1h to 2h to 3h granularity within a single
    response. The expansion produces a contiguous hourly grid regardless."""
    out = expand_grid_values([
        {"validTime": "2026-05-10T00:00:00+00:00/PT1H", "value": 1.0},
        {"validTime": "2026-05-10T01:00:00+00:00/PT2H", "value": 2.0},
        {"validTime": "2026-05-10T03:00:00+00:00/PT3H", "value": 3.0},
    ])
    expected_hours = [0, 1, 2, 3, 4, 5]
    assert sorted(out.keys()) == [
        datetime(2026, 5, 10, h, tzinfo=timezone.utc) for h in expected_hours
    ]


def test_expand_grid_values_skips_missing_values():
    out = expand_grid_values([
        {"validTime": "2026-05-10T00:00:00+00:00/PT1H", "value": None},
        {"validTime": "2026-05-10T01:00:00+00:00/PT1H", "value": 22.0},
    ])
    assert list(out.values()) == [22.0]


def test_expand_grid_values_skips_subhour_intervals():
    """Sub-hour intervals (PT30M etc.) don't fit the 1-hour slot grid; drop
    rather than corrupt downstream aggregation."""
    out = expand_grid_values([
        {"validTime": "2026-05-10T00:00:00+00:00/PT30M", "value": 99.0},
        {"validTime": "2026-05-10T00:30:00+00:00/PT30M", "value": 100.0},
    ])
    assert out == {}


def test_expand_grid_values_skips_unparseable_validtime():
    out = expand_grid_values([
        {"validTime": "garbage", "value": 1.0},
        {"validTime": "no-slash-anywhere", "value": 2.0},
        {"value": 3.0},  # missing validTime entirely
    ])
    assert out == {}


# ---- summarize_grid_for_date ----------------------------------------------


def _grid_props(**variables) -> dict:
    """Construct a minimal grid_props dict from {var_name: list_of_value_dicts}."""
    return {name: {"values": vals} for name, vals in variables.items()}


def test_summarize_grid_for_date_buckets_in_local_tz():
    """22:00 Chicago == 03:00 UTC the next day; the period belongs to the
    Chicago calendar day, not the UTC one. Same correctness contract as the
    pre-§0a forecastHourly aggregator, re-asserted under gridData."""
    grid = _grid_props(
        # Chicago 22:00 on 2026-05-06 = UTC 03:00 on 2026-05-07
        temperature=[
            {"validTime": "2026-05-07T03:00:00+00:00/PT1H", "value": 22.0},
        ],
    )
    in_chicago = summarize_grid_for_date(grid, date(2026, 5, 6), CHICAGO)
    assert in_chicago["hours_covered"] == 1
    in_utc = summarize_grid_for_date(grid, date(2026, 5, 6), UTC)
    assert in_utc == {}


def test_summarize_grid_for_date_emits_apparent_min_max_avg():
    """The apparent_max_f field is the trigger the day-type classifier (§1)
    reads; min and avg accompany it for sensitivity reporting."""
    grid = _grid_props(
        temperature=[
            {"validTime": "2026-07-01T18:00:00+00:00/PT3H", "value": 30.0},
        ],
        apparentTemperature=[
            {"validTime": "2026-07-01T18:00:00+00:00/PT1H", "value": 30.0},
            {"validTime": "2026-07-01T19:00:00+00:00/PT1H", "value": 32.0},
            {"validTime": "2026-07-01T20:00:00+00:00/PT1H", "value": 34.0},
        ],
    )
    # Use UTC tz so 18-20Z stays on 2026-07-01 in the bucketing.
    out = summarize_grid_for_date(grid, date(2026, 7, 1), UTC)
    assert round(out["apparent_max_f"], 1) == 93.2
    assert round(out["apparent_min_f"], 1) == 86.0
    assert round(out["apparent_avg_f"], 1) == 89.6


def test_summarize_grid_for_date_kmh_to_mph_conversion():
    """Wind speed and gust come back from the API in km/h; we convert to
    mph so the existing scheduler comparison thresholds stay applicable."""
    grid = _grid_props(
        temperature=[{"validTime": "2026-07-01T12:00:00+00:00/PT1H", "value": 25.0}],
        windSpeed=[{"validTime": "2026-07-01T12:00:00+00:00/PT1H", "value": 16.09}],  # 16.09 km/h ≈ 10 mph
        windGust=[{"validTime": "2026-07-01T12:00:00+00:00/PT1H", "value": 32.18}],   # ≈ 20 mph
    )
    out = summarize_grid_for_date(grid, date(2026, 7, 1), UTC)
    assert round(out["max_wind_mph"], 0) == 10.0
    assert round(out["wind_gust_max_mph"], 0) == 20.0


def test_summarize_grid_for_date_drops_optional_vars_when_absent():
    """Variables missing from the response must not crash the aggregator;
    only the corresponding output fields are skipped."""
    grid = _grid_props(
        temperature=[{"validTime": "2026-07-01T12:00:00+00:00/PT1H", "value": 25.0}],
    )
    out = summarize_grid_for_date(grid, date(2026, 7, 1), UTC)
    assert "high_f" in out
    assert "apparent_max_f" not in out
    assert "rh_max_pct" not in out
    assert "sky_cover_avg_pct" not in out
    assert "wind_gust_max_mph" not in out


def test_summarize_grid_for_date_returns_empty_when_temperature_missing():
    """Without temperature observations on the target date, the entire
    roll-up is empty (downstream skip)."""
    grid = _grid_props(
        temperature=[{"validTime": "2026-07-01T12:00:00+00:00/PT1H", "value": 25.0}],
    )
    assert summarize_grid_for_date(grid, date(2026, 7, 5), UTC) == {}


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


# ---- Real-fixture integration --------------------------------------------


def _load_fixture():
    fixture_path = FIXTURES / "forecastGridData_LOT_57_60_2026-05-10.json"
    with fixture_path.open() as f:
        return json.load(f)["properties"]


def test_real_fixture_temperature_field_populates():
    """Real LOT/57,60 fixture (captured 2026-05-10): the parser produces
    a non-empty roll-up for the day with the most coverage."""
    grid = _load_fixture()
    # Fixture window starts 2026-05-10T00:00:00+00:00 — fully covers May 11 in UTC.
    out = summarize_grid_for_date(grid, date(2026, 5, 11), UTC)
    assert out["hours_covered"] == 24
    assert "high_f" in out
    assert "low_f" in out
    assert out["high_f"] > out["low_f"]


def test_real_fixture_emits_all_new_apparent_fields():
    """Validation criterion §0a: new apparent_*, rh_*, sky_cover, wind_gust,
    precip_prob fields populate when the upstream variables are present."""
    grid = _load_fixture()
    out = summarize_grid_for_date(grid, date(2026, 5, 11), UTC)
    expected = {
        "apparent_max_f", "apparent_min_f", "apparent_avg_f",
        "rh_max_pct", "rh_avg_pct",
        "sky_cover_avg_pct",
        "wind_gust_max_mph",
        "max_precip_prob_pct",
    }
    assert expected.issubset(out.keys())


def test_real_fixture_apparent_avg_lies_between_min_and_max():
    """Sanity check on the avg computation against real data."""
    grid = _load_fixture()
    out = summarize_grid_for_date(grid, date(2026, 5, 11), UTC)
    assert out["apparent_min_f"] <= out["apparent_avg_f"] <= out["apparent_max_f"]
