"""Tests for `_load_week_inputs_from_stage1` against synthetic real-
shape replay bundles.

Builds parquet files with the Influx export shape via
`fixture_real_shape`, writes a manifest, and verifies the loader
enumerates (week, arm) pairs correctly from the manifest's window
intersected with the locked arm-assignment CSV.

Per-measurement parsing (refoss kWh sums, gap detection, comed.prices
hourly observation counts, etc.) is exercised in follow-on test files
as each measurement's loader logic lands.
"""
from __future__ import annotations

import csv
import datetime
import json
from pathlib import Path

import pandas as pd
import pytest

from tools.analysis import pipeline
from tools.analysis.replay.manifest import (
    OBSERVED_RECENT,
)
from tools.analysis.tests.fixture_real_shape import (
    build_comed_prices_df,
    build_ecowitt_weather_df,
    build_hvac_thermostat_df,
    build_refoss_channel_df,
    write_bundle,
)


# -- Test-local assignment CSV helper --------------------------------------


def _write_test_assignment_csv(path: Path, rows: list[dict]) -> None:
    """Write a minimal arm-assignment CSV (iso_week, monday_date, arm)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["iso_week", "monday_date", "arm"])
        w.writeheader()
        for row in rows:
            w.writerow(row)


# -- Loader: window enumeration --------------------------------------------


def test_loader_returns_empty_when_no_manifest(tmp_path):
    """No stage1/manifest.json → loader returns empty list."""
    stage1_dir = tmp_path / "stage1"
    stage1_dir.mkdir()
    assert pipeline._load_week_inputs_from_stage1(stage1_dir) == []


def test_loader_enumerates_weeks_in_window(tmp_path):
    """Bundle covering 2026-06-08 through 2026-06-29 → 3 Mondays in
    that window (06-08, 06-15, 06-22) → 3 weeks emitted."""
    # Build a small fixture covering 2026-06-08 12:00 UTC to 2026-06-29 12:00 UTC
    start = datetime.datetime(2026, 6, 8, 5, 0, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(2026, 6, 29, 5, 0, tzinfo=datetime.timezone.utc)
    refoss_df = build_refoss_channel_df(start, end, cadence_minutes=60)  # coarse for speed

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={"refoss.channel": refoss_df},
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-29T00:00:00-05:00",
    )

    # Synthetic 3-week assignment CSV
    assignment_csv = tmp_path / "assignment.csv"
    _write_test_assignment_csv(assignment_csv, [
        {"iso_week": "2026-W24", "monday_date": "2026-06-08", "arm": "A"},
        {"iso_week": "2026-W25", "monday_date": "2026-06-15", "arm": "A"},
        {"iso_week": "2026-W26", "monday_date": "2026-06-22", "arm": "B"},
    ])

    inputs = pipeline._load_week_inputs_from_stage1(
        stage1_dir=stage1_dir, assignment_csv=assignment_csv,
    )
    assert len(inputs) == 3
    weeks = sorted(i["week_start_ct"] for i in inputs)
    assert weeks == [
        datetime.date(2026, 6, 8),
        datetime.date(2026, 6, 15),
        datetime.date(2026, 6, 22),
    ]
    arms = [i["arm"] for i in inputs]
    assert arms.count("A") == 2
    assert arms.count("B") == 1


def test_loader_returns_empty_for_pre_randomization_window(tmp_path):
    """A May 2026 window (pre-randomization-start 2026-06-01) contains
    no assignment Mondays → loader returns empty."""
    start = datetime.datetime(2026, 5, 5, 5, 0, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(2026, 5, 19, 5, 0, tzinfo=datetime.timezone.utc)
    df = build_refoss_channel_df(start, end, cadence_minutes=60)

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={"refoss.channel": df},
        window_start_ct="2026-05-04T00:00:00-05:00",
        window_end_ct="2026-05-18T00:00:00-05:00",
    )

    assignment_csv = tmp_path / "assignment.csv"
    _write_test_assignment_csv(assignment_csv, [
        {"iso_week": "2026-W23", "monday_date": "2026-06-01", "arm": "A"},
        {"iso_week": "2026-W24", "monday_date": "2026-06-08", "arm": "A"},
    ])

    inputs = pipeline._load_week_inputs_from_stage1(
        stage1_dir=stage1_dir, assignment_csv=assignment_csv,
    )
    assert inputs == []


def test_loader_emitted_week_input_has_complete_shape(tmp_path):
    """Each (week, arm) input must include every key the orchestrator
    expects, even if measurement-derived values are still defaults."""
    start = datetime.datetime(2026, 6, 8, 5, 0, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(2026, 6, 15, 5, 0, tzinfo=datetime.timezone.utc)
    df = build_refoss_channel_df(start, end, cadence_minutes=60)

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={"refoss.channel": df},
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-15T00:00:00-05:00",
    )

    assignment_csv = tmp_path / "assignment.csv"
    _write_test_assignment_csv(assignment_csv, [
        {"iso_week": "2026-W24", "monday_date": "2026-06-08", "arm": "A"},
    ])

    inputs = pipeline._load_week_inputs_from_stage1(
        stage1_dir=stage1_dir, assignment_csv=assignment_csv,
    )
    assert len(inputs) == 1
    week = inputs[0]
    required_keys = {
        "week_start_ct", "arm", "weekly_hvac_kwh",
        "refoss_intervals", "hourly_prices",
        "daily_comfortnet_downtime_minutes",
        "daily_ecowitt_both_missing_hours",
        "scheduler_outages", "control_relevant_windows",
        "overrides", "missing_forecast_issuances",
        "arm_transition",
    }
    assert set(week.keys()) >= required_keys
    assert week["arm_transition"]["intended_arm"] == "A"
    assert week["arm_transition"]["action_events"][0]["arm"] == "A"
    assert week["arm_transition"]["action_events"][0]["dry_run"] is True


def test_loader_handles_multi_measurement_bundle(tmp_path):
    """Bundle with refoss + comed.prices + ecowitt + hvac.thermostat
    parquet files. The loader still enumerates weeks correctly (other
    measurements aren't consumed yet in this MVP, but their presence
    in the bundle must not break the loader)."""
    start = datetime.datetime(2026, 6, 8, 5, 0, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(2026, 6, 15, 5, 0, tzinfo=datetime.timezone.utc)
    refoss = build_refoss_channel_df(start, end, cadence_minutes=60)
    prices = build_comed_prices_df(start, end, cadence_minutes=60)
    ecowitt = build_ecowitt_weather_df(start, end, cadence_minutes=60)
    thermostat = build_hvac_thermostat_df(start, end, cadence_minutes=60)

    stage1_dir = tmp_path / "stage1"
    manifest = write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "refoss.channel": refoss,
            "comed.prices": prices,
            "ecowitt.weather": ecowitt,
            "hvac.thermostat": thermostat,
        },
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-15T00:00:00-05:00",
    )
    # Bundle has at least one observed_* entry → satisfies the
    # bundle-level minimum (criterion 14)
    assert manifest.has_any_observed_data() is True

    assignment_csv = tmp_path / "assignment.csv"
    _write_test_assignment_csv(assignment_csv, [
        {"iso_week": "2026-W24", "monday_date": "2026-06-08", "arm": "A"},
    ])
    inputs = pipeline._load_week_inputs_from_stage1(
        stage1_dir=stage1_dir, assignment_csv=assignment_csv,
    )
    assert len(inputs) == 1


# -- Helper unit tests -----------------------------------------------------


def test_parse_ct_window_returns_date_only():
    """Window strings include time + tz; loader needs just the date."""
    assert pipeline._parse_ct_window("2026-06-08T00:00:00-05:00") == (
        datetime.date(2026, 6, 8)
    )


def test_read_assignment_csv_parses_dates(tmp_path):
    path = tmp_path / "assignment.csv"
    _write_test_assignment_csv(path, [
        {"iso_week": "2026-W23", "monday_date": "2026-06-01", "arm": "A"},
        {"iso_week": "2026-W24", "monday_date": "2026-06-08", "arm": "B"},
    ])
    rows = pipeline._read_assignment_csv(path)
    assert len(rows) == 2
    assert rows[0]["monday_date"] == datetime.date(2026, 6, 1)
    assert rows[0]["arm"] == "A"
    assert rows[1]["monday_date"] == datetime.date(2026, 6, 8)


# -- E2E sanity: loader feeds the existing Stage 2 orchestrator -----------


def test_loader_computes_weekly_hvac_kwh_from_refoss(tmp_path):
    """A week of refoss.channel data at 1-min cadence with constant
    power on em:2 (1.2 kW), em:8 (1.1 kW), em:9 (0.05 kW) →
    expected weekly HVAC kWh = (1.2 + 1.1 + 0.05) * 168 hours = 394.8 kWh
    """
    start = datetime.datetime(2026, 6, 8, 5, 0, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(2026, 6, 15, 5, 0, tzinfo=datetime.timezone.utc)
    refoss_df = build_refoss_channel_df(
        start, end,
        power_w_by_channel={
            "em:1": 800.0,
            "em:2": 1200.0,
            "em:7": 700.0,
            "em:8": 1100.0,
            "em:9": 50.0,
        },
        cadence_minutes=1,
    )

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={"refoss.channel": refoss_df},
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-15T00:00:00-05:00",
    )
    assignment_csv = tmp_path / "assignment.csv"
    _write_test_assignment_csv(assignment_csv, [
        {"iso_week": "2026-W24", "monday_date": "2026-06-08", "arm": "A"},
    ])

    inputs = pipeline._load_week_inputs_from_stage1(
        stage1_dir=stage1_dir, assignment_csv=assignment_csv,
    )
    assert len(inputs) == 1
    # Expected: (1.2 + 1.1 + 0.05) * 168 = 394.8 kWh
    # (em:1 and em:7 are mains, not HVAC; excluded)
    assert inputs[0]["weekly_hvac_kwh"] == pytest.approx(394.8, rel=1e-3)


def test_loader_weekly_hvac_kwh_zero_when_refoss_empty(tmp_path):
    """No refoss.channel parquet → default weekly_hvac_kwh fallback."""
    # Bundle with only comed.prices (no refoss)
    start = datetime.datetime(2026, 6, 8, 5, 0, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(2026, 6, 15, 5, 0, tzinfo=datetime.timezone.utc)
    prices = build_comed_prices_df(start, end, cadence_minutes=60)
    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={"comed.prices": prices},
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-15T00:00:00-05:00",
    )
    assignment_csv = tmp_path / "assignment.csv"
    _write_test_assignment_csv(assignment_csv, [
        {"iso_week": "2026-W24", "monday_date": "2026-06-08", "arm": "A"},
    ])
    inputs = pipeline._load_week_inputs_from_stage1(
        stage1_dir=stage1_dir, assignment_csv=assignment_csv,
    )
    # Falls back to default placeholder so the rest of the pipeline
    # continues; the actual loader-level reason code for missing
    # refoss is emitted by the Stage 2 orchestrator separately.
    assert inputs[0]["weekly_hvac_kwh"] == 100.0


def test_refoss_weekly_hvac_kwh_aggregates_mean_within_hour_not_sum():
    """Oracle test that distinguishes mean-then-sum from sum-then-sum.

    Three power_w samples within one hour on a single HVAC channel:
    [600, 1200, 1800] W. Mean = 1200 W = 1.2 kW. Over 1 h → 1.2 kWh.
    Other 167 hours empty → 0. Weekly total = 1.2 kWh.

    A buggy sum-then-sum aggregator would yield (600+1200+1800)/1000
    = 3.6 kWh per hour; weekly would be 3.6. The 1.2 vs 3.6 split
    distinguishes the two interpretations.
    """
    from tools.analysis.tests.fixture_real_shape import build_long_format_df
    week_start_utc = datetime.datetime(
        2026, 6, 8, 5, 0, tzinfo=datetime.timezone.utc,
    )
    # Three em:2 samples at 0, 20, 40 min after week start.
    rows = []
    for offset_min, watts in [(0, 600.0), (20, 1200.0), (40, 1800.0)]:
        rows.append({
            "_time": week_start_utc + datetime.timedelta(minutes=offset_min),
            "_measurement": "refoss.channel",
            "_field": "power_w",
            "_value": watts,
            "channel": "em:2",
        })
    df = pd.DataFrame(rows)
    df["_time"] = pd.to_datetime(df["_time"], utc=True)

    result = pipeline._refoss_weekly_hvac_kwh(df, datetime.date(2026, 6, 8))
    assert result == pytest.approx(1.2, abs=0.001)


def test_stage3_hourly_refoss_kwh_aggregates_mean_within_hour_not_sum():
    """Same mean-within-hour oracle for the Stage 3 helper.

    Two channels in one hour:
      em:2 samples [600, 1200, 1800] W → mean 1200 W = 1.2 kW
      em:8 samples [400,  800, 1200] W → mean  800 W = 0.8 kW
    Sum across channels = 2.0 kW × 1 h = 2.0 kWh in that hour.
    Other hours = 0.
    """
    from tools.analysis.tests.fixture_real_shape import build_long_format_df
    week_start_utc = datetime.datetime(
        2026, 6, 8, 5, 0, tzinfo=datetime.timezone.utc,
    )
    rows = []
    for offset_min, watts_em2, watts_em8 in [
        (0, 600.0, 400.0),
        (20, 1200.0, 800.0),
        (40, 1800.0, 1200.0),
    ]:
        ts = week_start_utc + datetime.timedelta(minutes=offset_min)
        rows.append({
            "_time": ts, "_measurement": "refoss.channel",
            "_field": "power_w", "_value": watts_em2, "channel": "em:2",
        })
        rows.append({
            "_time": ts, "_measurement": "refoss.channel",
            "_field": "power_w", "_value": watts_em8, "channel": "em:8",
        })
    df = pd.DataFrame(rows)
    df["_time"] = pd.to_datetime(df["_time"], utc=True)

    result = pipeline._stage3_hourly_refoss_kwh(
        df, datetime.date(2026, 6, 8),
        pipeline.HVAC_CHANNELS,
    )
    assert len(result) == 168
    # Hour 0 has data; mean(em:2)/1000 + mean(em:8)/1000 = 1.2 + 0.8 = 2.0
    assert result[0]["hvac_kwh"] == pytest.approx(2.0, abs=0.001)
    # All other hours empty.
    for h in range(1, 168):
        assert result[h]["hvac_kwh"] == 0.0


def test_refoss_weekly_hvac_kwh_helper_filters_correctly(tmp_path):
    """Direct test of _refoss_weekly_hvac_kwh: only em:2+em:8+em:9
    power_w in the week's CT range contribute to the kWh sum."""
    start = datetime.datetime(2026, 6, 8, 5, 0, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(2026, 6, 15, 5, 0, tzinfo=datetime.timezone.utc)
    refoss = build_refoss_channel_df(
        start, end,
        power_w_by_channel={
            "em:1": 100.0,  # mains; should be excluded
            "em:2": 1000.0,  # HVAC
            "em:7": 100.0,  # mains; excluded
            "em:8": 500.0,   # HVAC
            "em:9": 0.0,     # HVAC blower; zero contribution
        },
        cadence_minutes=60,
    )
    kwh = pipeline._refoss_weekly_hvac_kwh(refoss, datetime.date(2026, 6, 8))
    # 168 hours × (1000 + 500 + 0) W = 252,000 Wh = 252.0 kWh
    assert kwh == pytest.approx(252.0, rel=1e-3)


def test_refoss_weekly_hvac_kwh_handles_dst_boundary():
    """The CT-to-UTC conversion must use America/Chicago zone so weeks
    spanning DST transitions don't drift. Summer 2026 weeks are all in
    CDT, but the conversion should still go through zoneinfo, not
    a hardcoded UTC-5 offset."""
    # June 8 2026 (CDT): 05:00 UTC = 00:00 CT
    utc_start = pipeline._ct_date_to_utc(datetime.date(2026, 6, 8), 0)
    assert utc_start.hour == 5  # CDT is UTC-5
    # January 1 2026 (CST): 06:00 UTC = 00:00 CT
    utc_winter = pipeline._ct_date_to_utc(datetime.date(2026, 1, 1), 0)
    assert utc_winter.hour == 6  # CST is UTC-6


# -- Hourly price observation counts ---------------------------------------


def test_hourly_price_observation_counts_full_week(tmp_path):
    """A full week of 5-min comed.prices → 12 prints per hour, 168 hours."""
    start = datetime.datetime(2026, 6, 8, 5, 0, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(2026, 6, 15, 5, 0, tzinfo=datetime.timezone.utc)
    prices = build_comed_prices_df(start, end, cadence_minutes=5)
    result = pipeline._hourly_price_observation_counts(
        prices, datetime.date(2026, 6, 8),
    )
    assert len(result) == 168
    assert all(r["observed_prints"] == 12 for r in result)


def test_hourly_price_observation_counts_partial_hour(tmp_path):
    """An hour with only some 5-min prints reports the partial count."""
    # 6 ticks for hour 0, then nothing for hours 1-167
    one_hour_only = pd.DataFrame({
        "_time": pd.date_range(
            "2026-06-08 05:00",
            periods=6, freq="5min", tz="UTC",
        ),
        "_measurement": ["comed.prices"] * 6,
        "_field": ["price_cents"] * 6,
        "_value": [5.0] * 6,
    })
    result = pipeline._hourly_price_observation_counts(
        one_hour_only, datetime.date(2026, 6, 8),
    )
    assert result[0]["observed_prints"] == 6
    assert all(r["observed_prints"] == 0 for r in result[1:])


def test_hourly_price_observation_counts_empty_df_returns_zeros():
    result = pipeline._hourly_price_observation_counts(
        pd.DataFrame(), datetime.date(2026, 6, 8),
    )
    assert len(result) == 168
    assert all(r["observed_prints"] == 0 for r in result)


# -- ComfortNet daily downtime --------------------------------------------


def test_comfortnet_daily_downtime_full_uptime():
    """A full week of 1-min comfortnet → 0 minutes downtime per day."""
    start = datetime.datetime(2026, 6, 8, 5, 0, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(2026, 6, 15, 5, 0, tzinfo=datetime.timezone.utc)
    df = pipeline._load_concat_parquets  # ignore; just need a DataFrame
    # Build a 1-min cadence comfortnet DataFrame
    from tools.analysis.tests.fixture_real_shape import build_long_format_df
    df = build_long_format_df(
        measurement="hvac.comfortnet",
        start_utc=start, end_utc=end,
        fields={
            "cool_actual_pct": lambda ts, _: 50.0,
            "heat_actual_pct": lambda ts, _: 0.0,
            "blower_cfm": lambda ts, _: 2000.0,
        },
        cadence=datetime.timedelta(minutes=1),
    )
    result = pipeline._comfortnet_daily_downtime_minutes(
        df, datetime.date(2026, 6, 8),
    )
    assert result == [0] * 7


def test_comfortnet_daily_downtime_one_full_day_missing():
    """6 days of comfortnet, day 3 absent → 1440 min downtime on day 3."""
    from tools.analysis.tests.fixture_real_shape import build_long_format_df
    # Days 0-2 + 4-6 have data; day 3 (Thursday 2026-06-11) skipped
    dfs = []
    for day_offset in [0, 1, 2, 4, 5, 6]:
        day_start = datetime.datetime(2026, 6, 8 + day_offset, 5, 0, tzinfo=datetime.timezone.utc)
        day_end = day_start + datetime.timedelta(days=1)
        dfs.append(build_long_format_df(
            measurement="hvac.comfortnet",
            start_utc=day_start, end_utc=day_end,
            fields={"cool_actual_pct": lambda ts, _: 50.0},
            cadence=datetime.timedelta(minutes=1),
        ))
    df = pd.concat(dfs, ignore_index=True)
    result = pipeline._comfortnet_daily_downtime_minutes(
        df, datetime.date(2026, 6, 8),
    )
    assert result[0] == 0
    assert result[1] == 0
    assert result[2] == 0
    assert result[3] == 1440  # the missing day
    assert result[4] == 0
    assert result[5] == 0
    assert result[6] == 0


def test_comfortnet_daily_downtime_empty_df():
    result = pipeline._comfortnet_daily_downtime_minutes(
        pd.DataFrame(), datetime.date(2026, 6, 8),
    )
    assert result == [1440] * 7


# -- Missing forecast issuances -------------------------------------------


def test_missing_forecast_issuances_full_coverage():
    """7 21:00 CT issuances present → 0 missing."""
    from tools.analysis.tests.fixture_real_shape import build_long_format_df
    # Build rows at exactly 21:00 CT each day (= 02:00 UTC next day in CDT)
    rows = []
    for d in range(7):
        day_ct = datetime.date(2026, 6, 8 + d)
        ts_utc = pipeline._ct_date_to_utc(day_ct, 21)
        rows.append({
            "_time": ts_utc, "_measurement": "nws.forecast",
            "_field": "max_temp_f", "_value": 85.0,
        })
    df = pd.DataFrame(rows)
    df["_time"] = pd.to_datetime(df["_time"], utc=True)
    result = pipeline._missing_forecast_issuances(df, datetime.date(2026, 6, 8))
    assert result == 0


def test_missing_forecast_issuances_some_days_missing():
    """3 of 7 days have a 21:00 issuance → 4 missing."""
    rows = []
    for d in [0, 2, 5]:
        day_ct = datetime.date(2026, 6, 8 + d)
        ts_utc = pipeline._ct_date_to_utc(day_ct, 21)
        rows.append({
            "_time": ts_utc, "_measurement": "nws.forecast",
            "_field": "max_temp_f", "_value": 85.0,
        })
    df = pd.DataFrame(rows)
    df["_time"] = pd.to_datetime(df["_time"], utc=True)
    result = pipeline._missing_forecast_issuances(df, datetime.date(2026, 6, 8))
    assert result == 4


def test_missing_forecast_issuances_empty_df_all_missing():
    result = pipeline._missing_forecast_issuances(
        pd.DataFrame(), datetime.date(2026, 6, 8),
    )
    assert result == 7


def test_refoss_gap_intervals_no_gaps_returns_empty():
    """Continuous 1-min refoss data across a week has no gaps."""
    start = datetime.datetime(2026, 6, 8, 5, 0, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(2026, 6, 15, 5, 0, tzinfo=datetime.timezone.utc)
    df = build_refoss_channel_df(start, end, cadence_minutes=1)
    result = pipeline._refoss_gap_intervals(df, datetime.date(2026, 6, 8))
    assert result == []


def test_refoss_gap_intervals_detects_short_gap_tier1():
    """A 2-minute gap is classified Tier 1 (<5 min) and gets linear
    interpolation imputation from adjacent ticks."""
    from tools.analysis.tests.fixture_real_shape import build_long_format_df
    # Build refoss data with a 2-min gap between t=10 and t=12 in em:2
    start = datetime.datetime(2026, 6, 8, 5, 0, tzinfo=datetime.timezone.utc)
    # First chunk: minutes 0..10
    df1 = build_long_format_df(
        measurement="refoss.channel",
        start_utc=start,
        end_utc=start + datetime.timedelta(minutes=11),
        fields={"power_w": lambda ts, _: 1200.0},
        cadence=datetime.timedelta(minutes=1),
        tags={"channel": ["em:2"]},
    )
    # Second chunk: minutes 13..20 (12 missing)
    df2 = build_long_format_df(
        measurement="refoss.channel",
        start_utc=start + datetime.timedelta(minutes=13),
        end_utc=start + datetime.timedelta(minutes=21),
        fields={"power_w": lambda ts, _: 1200.0},
        cadence=datetime.timedelta(minutes=1),
        tags={"channel": ["em:2"]},
    )
    df = pd.concat([df1, df2], ignore_index=True)
    result = pipeline._refoss_gap_intervals(df, datetime.date(2026, 6, 8))
    assert len(result) == 1
    gap = result[0]
    assert gap["tier"] == 1
    assert 1 <= gap["gap_minutes"] <= 5
    assert gap["imputed_kwh"] >= 0.0


def test_refoss_gap_intervals_detects_tier4_long_gap():
    """A gap >180 min on an HVAC channel is Tier 4 (no imputation)."""
    from tools.analysis.tests.fixture_real_shape import build_long_format_df
    start = datetime.datetime(2026, 6, 8, 5, 0, tzinfo=datetime.timezone.utc)
    df1 = build_long_format_df(
        measurement="refoss.channel",
        start_utc=start,
        end_utc=start + datetime.timedelta(minutes=30),
        fields={"power_w": lambda ts, _: 1200.0},
        cadence=datetime.timedelta(minutes=1),
        tags={"channel": ["em:2"]},
    )
    # 4-hour gap, then resume
    df2 = build_long_format_df(
        measurement="refoss.channel",
        start_utc=start + datetime.timedelta(hours=4, minutes=30),
        end_utc=start + datetime.timedelta(hours=5),
        fields={"power_w": lambda ts, _: 1200.0},
        cadence=datetime.timedelta(minutes=1),
        tags={"channel": ["em:2"]},
    )
    df = pd.concat([df1, df2], ignore_index=True)
    result = pipeline._refoss_gap_intervals(df, datetime.date(2026, 6, 8))
    assert len(result) == 1
    gap = result[0]
    assert gap["tier"] == 4
    assert gap["gap_minutes"] >= 180
    assert gap["imputed_kwh"] == 0.0


def test_refoss_gap_intervals_only_hvac_channels():
    """Gaps in mains-only channels (em:1, em:7) are ignored — Rule 1
    operates on the HVAC channel set (em:2, em:8, em:9)."""
    from tools.analysis.tests.fixture_real_shape import build_long_format_df
    start = datetime.datetime(2026, 6, 8, 5, 0, tzinfo=datetime.timezone.utc)
    # HVAC channel em:2: continuous
    hvac = build_long_format_df(
        measurement="refoss.channel",
        start_utc=start,
        end_utc=start + datetime.timedelta(hours=1),
        fields={"power_w": lambda ts, _: 1200.0},
        cadence=datetime.timedelta(minutes=1),
        tags={"channel": ["em:2"]},
    )
    # Mains em:1 has a 10-minute gap (irrelevant to HVAC)
    mains1 = build_long_format_df(
        measurement="refoss.channel",
        start_utc=start,
        end_utc=start + datetime.timedelta(minutes=20),
        fields={"power_w": lambda ts, _: 800.0},
        cadence=datetime.timedelta(minutes=1),
        tags={"channel": ["em:1"]},
    )
    mains2 = build_long_format_df(
        measurement="refoss.channel",
        start_utc=start + datetime.timedelta(minutes=30),
        end_utc=start + datetime.timedelta(minutes=60),
        fields={"power_w": lambda ts, _: 800.0},
        cadence=datetime.timedelta(minutes=1),
        tags={"channel": ["em:1"]},
    )
    df = pd.concat([hvac, mains1, mains2], ignore_index=True)
    result = pipeline._refoss_gap_intervals(df, datetime.date(2026, 6, 8))
    assert result == []


def test_refoss_gap_intervals_empty_df():
    """Empty refoss df → no intervals (orchestrator handles missing-week
    via the weekly_hvac_kwh=0 path)."""
    result = pipeline._refoss_gap_intervals(pd.DataFrame(), datetime.date(2026, 6, 8))
    assert result == []


def test_scheduler_outages_no_gaps_returns_empty():
    """Continuous 5cp_state + actions data → no outages."""
    from tools.analysis.tests.fixture_real_shape import build_long_format_df
    start = datetime.datetime(2026, 6, 8, 5, 0, tzinfo=datetime.timezone.utc)
    fivecp = build_long_format_df(
        measurement="hvac.5cp_state",
        start_utc=start,
        end_utc=start + datetime.timedelta(hours=2),
        fields={"state": lambda ts, _: "NONE"},
        cadence=datetime.timedelta(minutes=2),
    )
    actions = build_long_format_df(
        measurement="hvac.actions",
        start_utc=start,
        end_utc=start + datetime.timedelta(hours=2),
        fields={"action": lambda ts, _: "NONE"},
        cadence=datetime.timedelta(minutes=1),
    )
    result = pipeline._scheduler_outages_from_parquet(
        fivecp, actions, datetime.date(2026, 6, 8),
    )
    assert result == []


def test_scheduler_outages_detects_gap_in_both_feeds():
    """A 10-minute gap simultaneous in both feeds → one outage."""
    from tools.analysis.tests.fixture_real_shape import build_long_format_df
    start = datetime.datetime(2026, 6, 8, 5, 0, tzinfo=datetime.timezone.utc)
    fivecp_a = build_long_format_df(
        measurement="hvac.5cp_state",
        start_utc=start,
        end_utc=start + datetime.timedelta(minutes=10),
        fields={"state": lambda ts, _: "NONE"},
        cadence=datetime.timedelta(minutes=2),
    )
    fivecp_b = build_long_format_df(
        measurement="hvac.5cp_state",
        start_utc=start + datetime.timedelta(minutes=20),
        end_utc=start + datetime.timedelta(minutes=30),
        fields={"state": lambda ts, _: "NONE"},
        cadence=datetime.timedelta(minutes=2),
    )
    actions_a = build_long_format_df(
        measurement="hvac.actions",
        start_utc=start,
        end_utc=start + datetime.timedelta(minutes=10),
        fields={"action": lambda ts, _: "NONE"},
        cadence=datetime.timedelta(minutes=1),
    )
    actions_b = build_long_format_df(
        measurement="hvac.actions",
        start_utc=start + datetime.timedelta(minutes=20),
        end_utc=start + datetime.timedelta(minutes=30),
        fields={"action": lambda ts, _: "NONE"},
        cadence=datetime.timedelta(minutes=1),
    )
    fivecp = pd.concat([fivecp_a, fivecp_b], ignore_index=True)
    actions = pd.concat([actions_a, actions_b], ignore_index=True)
    result = pipeline._scheduler_outages_from_parquet(
        fivecp, actions, datetime.date(2026, 6, 8),
    )
    assert len(result) == 1
    outage_start, outage_end = result[0]
    assert (outage_end - outage_start) >= datetime.timedelta(minutes=5)


def test_scheduler_outages_empty_dfs():
    """Both feeds empty for the week → no outages reported. The
    orchestrator's missing-week reason-code handles the empty case."""
    result = pipeline._scheduler_outages_from_parquet(
        pd.DataFrame(), pd.DataFrame(), datetime.date(2026, 6, 8),
    )
    assert result == []


def test_control_relevant_windows_empty_df():
    """No precool_window rows → no control windows."""
    result = pipeline._control_relevant_windows_from_parquet(
        pd.DataFrame(), datetime.date(2026, 6, 8),
    )
    assert result == []


def test_control_relevant_windows_single_precool_row():
    """One precool_window row → one 1-hour window at hour_ct CT."""
    from tools.analysis.tests.fixture_real_shape import build_long_format_df
    # Precool decided for 2026-06-09 at hour_ct=14 (2pm CT)
    decision_ts = datetime.datetime(2026, 6, 8, 2, 0, tzinfo=datetime.timezone.utc)
    df = build_long_format_df(
        measurement="hvac.precool_window",
        start_utc=decision_ts,
        end_utc=decision_ts + datetime.timedelta(minutes=1),
        fields={
            "hour_ct": lambda ts, _: 14,
            "depth_f": lambda ts, _: 67,
        },
        cadence=datetime.timedelta(minutes=1),
        tags={"target_date": ["2026-06-09"]},
    )
    result = pipeline._control_relevant_windows_from_parquet(
        df, datetime.date(2026, 6, 8),
    )
    assert len(result) == 1
    start, end = result[0]
    assert (end - start) == datetime.timedelta(hours=1)
    # 14:00 CT on 2026-06-09 = 19:00 UTC during CDT
    expected_start_utc = pipeline._ct_date_to_utc(datetime.date(2026, 6, 9), 14)
    assert start == expected_start_utc


def test_control_relevant_windows_filters_to_week():
    """Decision rows outside the CT week are filtered out by their
    target_date, not the decision _time (decisions are made the prior day)."""
    from tools.analysis.tests.fixture_real_shape import build_long_format_df
    decision_ts = datetime.datetime(2026, 6, 8, 2, 0, tzinfo=datetime.timezone.utc)
    df = build_long_format_df(
        measurement="hvac.precool_window",
        start_utc=decision_ts,
        end_utc=decision_ts + datetime.timedelta(minutes=1),
        fields={
            "hour_ct": lambda ts, _: 14,
            "depth_f": lambda ts, _: 67,
        },
        cadence=datetime.timedelta(minutes=1),
        tags={"target_date": ["2026-06-20"]},  # outside week starting 2026-06-08
    )
    result = pipeline._control_relevant_windows_from_parquet(
        df, datetime.date(2026, 6, 8),
    )
    assert result == []


def test_overrides_empty_df():
    """No hvac.overrides rows → empty overrides list."""
    result = pipeline._overrides_from_parquet(
        pd.DataFrame(), datetime.date(2026, 6, 8),
    )
    assert result == []


def test_overrides_single_operational_override_in_week():
    """One operational override row inside the CT week → one entry
    with the correct (start_ts, end_ts, category, setpoint_f) shape."""
    from tools.analysis.tests.fixture_real_shape import build_long_format_df
    # Override fired 2026-06-10 14:00 CT for 2 hours
    start_ct = datetime.datetime(2026, 6, 10, 14, 0)
    end_ct = datetime.datetime(2026, 6, 10, 16, 0)
    logged_at = datetime.datetime(2026, 6, 10, 21, 0, tzinfo=datetime.timezone.utc)
    df = build_long_format_df(
        measurement="hvac.overrides",
        start_utc=logged_at,
        end_utc=logged_at + datetime.timedelta(minutes=1),
        fields={
            "start_ts": lambda ts, _: int(start_ct.timestamp()),
            "end_ts": lambda ts, _: int(end_ct.timestamp()),
            "setpoint_f": lambda ts, _: 76.0,
            "duration_hours": lambda ts, _: 2.0,
        },
        cadence=datetime.timedelta(minutes=1),
        tags={"category": ["operational"]},
    )
    result = pipeline._overrides_from_parquet(
        df, datetime.date(2026, 6, 8),
    )
    assert len(result) == 1
    ov = result[0]
    assert ov["category"] == "operational"
    assert ov["setpoint_f"] == 76.0
    assert (ov["end_ts"] - ov["start_ts"]).total_seconds() == 2 * 3600


def test_overrides_filters_to_week():
    """Override rows whose span falls entirely outside the CT week
    are filtered out."""
    from tools.analysis.tests.fixture_real_shape import build_long_format_df
    # Override fires 2026-05-01 (well before week 2026-06-08)
    start_ct = datetime.datetime(2026, 5, 1, 14, 0)
    end_ct = datetime.datetime(2026, 5, 1, 16, 0)
    logged_at = datetime.datetime(2026, 5, 1, 21, 0, tzinfo=datetime.timezone.utc)
    df = build_long_format_df(
        measurement="hvac.overrides",
        start_utc=logged_at,
        end_utc=logged_at + datetime.timedelta(minutes=1),
        fields={
            "start_ts": lambda ts, _: int(start_ct.timestamp()),
            "end_ts": lambda ts, _: int(end_ct.timestamp()),
            "setpoint_f": lambda ts, _: 76.0,
            "duration_hours": lambda ts, _: 2.0,
        },
        cadence=datetime.timedelta(minutes=1),
        tags={"category": ["operational"]},
    )
    result = pipeline._overrides_from_parquet(
        df, datetime.date(2026, 6, 8),
    )
    assert result == []


def test_action_events_empty_df():
    """No hvac.actions rows → empty action_events list."""
    result = pipeline._action_events_from_parquet(
        pd.DataFrame(),
        switch_ts_utc=datetime.datetime(2026, 6, 8, 10, 0, tzinfo=datetime.timezone.utc),
        intended_arm="A",
    )
    assert result == []


def test_action_events_within_six_hour_window():
    """hvac.actions rows within 6h after switch → events with timestamp,
    action, arm, dry_run."""
    from tools.analysis.tests.fixture_real_shape import build_long_format_df
    switch_ts = datetime.datetime(2026, 6, 8, 10, 0, tzinfo=datetime.timezone.utc)
    # One action 2h after switch
    df = build_long_format_df(
        measurement="hvac.actions",
        start_utc=switch_ts + datetime.timedelta(hours=2),
        end_utc=switch_ts + datetime.timedelta(hours=2, minutes=1),
        fields={"applied": lambda ts, _: 1},
        cadence=datetime.timedelta(minutes=1),
        tags={"action_label": ["HOT_PRE_COOL"], "dry_run": ["true"]},
    )
    result = pipeline._action_events_from_parquet(
        df, switch_ts_utc=switch_ts, intended_arm="A",
    )
    assert len(result) == 1
    ev = result[0]
    assert ev["action"] == "HOT_PRE_COOL"
    assert ev["arm"] == "A"
    assert ev["dry_run"] is True
    assert ev["timestamp"] == switch_ts + datetime.timedelta(hours=2)


def test_action_events_outside_window_filtered():
    """Rows >6h after switch are filtered out."""
    from tools.analysis.tests.fixture_real_shape import build_long_format_df
    switch_ts = datetime.datetime(2026, 6, 8, 10, 0, tzinfo=datetime.timezone.utc)
    df = build_long_format_df(
        measurement="hvac.actions",
        start_utc=switch_ts + datetime.timedelta(hours=12),
        end_utc=switch_ts + datetime.timedelta(hours=12, minutes=1),
        fields={"applied": lambda ts, _: 1},
        cadence=datetime.timedelta(minutes=1),
        tags={"action_label": ["HOT_PRE_COOL"], "dry_run": ["true"]},
    )
    result = pipeline._action_events_from_parquet(
        df, switch_ts_utc=switch_ts, intended_arm="A",
    )
    assert result == []


def test_ecowitt_daily_missing_hours_full_coverage():
    """Continuous ecowitt outdoor_temp_f for a full week → 0 missing
    hours every day."""
    start = datetime.datetime(2026, 6, 8, 5, 0, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(2026, 6, 15, 5, 0, tzinfo=datetime.timezone.utc)
    from tools.analysis.tests.fixture_real_shape import build_ecowitt_weather_df
    df = build_ecowitt_weather_df(start, end, cadence_minutes=5)
    result = pipeline._ecowitt_daily_missing_hours_from_parquet(
        df, datetime.date(2026, 6, 8),
    )
    assert result == [0] * 7


def test_ecowitt_daily_missing_hours_one_day_missing():
    """If day 2 has no ecowitt rows, that day reports 24 missing hours."""
    from tools.analysis.tests.fixture_real_shape import build_ecowitt_weather_df
    dfs = []
    for d in [0, 1, 3, 4, 5, 6]:  # skip day 2
        day_start = datetime.datetime(
            2026, 6, 8 + d, 5, 0, tzinfo=datetime.timezone.utc,
        )
        day_end = day_start + datetime.timedelta(days=1)
        dfs.append(build_ecowitt_weather_df(day_start, day_end, cadence_minutes=5))
    df = pd.concat(dfs, ignore_index=True)
    result = pipeline._ecowitt_daily_missing_hours_from_parquet(
        df, datetime.date(2026, 6, 8),
    )
    assert result[0] == 0
    assert result[1] == 0
    assert result[2] == 24
    assert result[3] == 0


def test_ecowitt_daily_missing_hours_empty_df_all_missing():
    """Empty ecowitt df → 24 missing hours per day."""
    result = pipeline._ecowitt_daily_missing_hours_from_parquet(
        pd.DataFrame(), datetime.date(2026, 6, 8),
    )
    assert result == [24] * 7


def test_stage3_daily_avg_temps_constant_temperature():
    """Continuous 80°F ecowitt for a full week → daily averages of 80°F."""
    from tools.analysis.tests.fixture_real_shape import build_ecowitt_weather_df
    start = datetime.datetime(2026, 6, 8, 5, 0, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(2026, 6, 15, 5, 0, tzinfo=datetime.timezone.utc)
    df = build_ecowitt_weather_df(
        start, end,
        temp_f_fn=lambda ts: 80.0,
        cadence_minutes=5,
    )
    result = pipeline._stage3_daily_avg_temps_f(df, datetime.date(2026, 6, 8))
    assert len(result) == 7
    for t in result:
        assert t == pytest.approx(80.0, abs=0.01)


def test_stage3_daily_avg_temps_empty_df():
    """No ecowitt data → 7 zeros (caller will Rule-5-drop them upstream)."""
    result = pipeline._stage3_daily_avg_temps_f(
        pd.DataFrame(), datetime.date(2026, 6, 8),
    )
    assert result == [0.0] * 7


def test_stage3_hourly_hvac_kwh_constant_power():
    """Continuous 1.2 + 1.1 + 0.05 kW = 2.35 kW on em:2/8/9 → each
    hourly record sums to 2.35 kWh."""
    start = datetime.datetime(2026, 6, 8, 5, 0, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(2026, 6, 15, 5, 0, tzinfo=datetime.timezone.utc)
    df = build_refoss_channel_df(start, end, cadence_minutes=1)
    from tools.analysis.pipeline import HVAC_CHANNELS
    result = pipeline._stage3_hourly_refoss_kwh(
        df, datetime.date(2026, 6, 8), HVAC_CHANNELS,
    )
    assert len(result) == 168
    # 1.2 (em:2) + 1.1 (em:8) + 0.05 (em:9) = 2.35 kW * 1h = 2.35 kWh
    for record in result:
        assert record["hvac_kwh"] == pytest.approx(2.35, abs=0.05)
    # First record covers hour 0 CT Monday (= 5 UTC), so hour_of_day_ct = 0
    assert result[0]["hour_of_day_ct"] == 0
    assert result[23]["hour_of_day_ct"] == 23
    assert result[24]["hour_of_day_ct"] == 0


def test_stage3_hourly_hvac_kwh_filters_to_channel_set():
    """Mains channels (em:1, em:7) are excluded from the HVAC sum."""
    start = datetime.datetime(2026, 6, 8, 5, 0, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(2026, 6, 9, 5, 0, tzinfo=datetime.timezone.utc)
    df = build_refoss_channel_df(start, end, cadence_minutes=1)
    from tools.analysis.pipeline import MAINS_CHANNELS
    result = pipeline._stage3_hourly_refoss_kwh(
        df, datetime.date(2026, 6, 8), MAINS_CHANNELS,
    )
    # Mains: 0.8 + 0.7 = 1.5 kW
    for record in result[:24]:
        assert record["hvac_kwh"] == pytest.approx(1.5, abs=0.05)


def test_stage3_hourly_supply_prices_constant():
    """Constant 5¢/kWh prices → each hour reports 5¢."""
    start = datetime.datetime(2026, 6, 8, 5, 0, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(2026, 6, 15, 5, 0, tzinfo=datetime.timezone.utc)
    from tools.analysis.tests.fixture_real_shape import build_comed_prices_df
    df = build_comed_prices_df(start, end, price_cents_fn=lambda ts: 5.0)
    result = pipeline._stage3_hourly_supply_prices(
        df, datetime.date(2026, 6, 8),
    )
    assert len(result) == 168
    for p in result:
        assert p == pytest.approx(5.0, abs=0.01)


def test_stage3_hourly_weather_constant():
    """Constant ecowitt readings → each hour reports the same values."""
    start = datetime.datetime(2026, 6, 8, 5, 0, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(2026, 6, 15, 5, 0, tzinfo=datetime.timezone.utc)
    from tools.analysis.tests.fixture_real_shape import build_ecowitt_weather_df
    df = build_ecowitt_weather_df(
        start, end,
        temp_f_fn=lambda ts: 85.0,
        dewpoint_f_fn=lambda ts: 70.0,
        pressure_inhg_fn=lambda ts: 30.05,
    )
    result = pipeline._stage3_hourly_weather(df, datetime.date(2026, 6, 8))
    assert len(result) == 168
    for h in result:
        assert h["temp_f"] == pytest.approx(85.0)
        assert h["dewpoint_f"] == pytest.approx(70.0)
        assert h["pressure_inhg"] == pytest.approx(30.05)


def test_load_stage3_inputs_for_week_no_manifest_returns_none():
    """No stage1/manifest.json → None (Stage 3 falls back to empty row)."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        result = pipeline._load_stage3_inputs_for_week(
            Path(td), datetime.date(2026, 6, 8), "A",
        )
        assert result is None


def test_load_stage3_inputs_for_week_full_bundle(tmp_path):
    """End-to-end: bundle with refoss + prices + ecowitt → complete dict
    fed into _compute_weekly_row produces a non-trivial weekly.csv row."""
    from tools.analysis.tests.fixture_real_shape import (
        build_comed_prices_df,
        build_ecowitt_weather_df,
    )
    start = datetime.datetime(2026, 6, 8, 5, 0, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(2026, 6, 15, 5, 0, tzinfo=datetime.timezone.utc)
    refoss = build_refoss_channel_df(start, end, cadence_minutes=1)
    prices = build_comed_prices_df(start, end, price_cents_fn=lambda ts: 4.0)
    weather = build_ecowitt_weather_df(
        start, end,
        temp_f_fn=lambda ts: 82.0,
        dewpoint_f_fn=lambda ts: 68.0,
        pressure_inhg_fn=lambda ts: 29.92,
    )
    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "refoss.channel": refoss,
            "comed.prices": prices,
            "ecowitt.weather": weather,
        },
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-15T00:00:00-05:00",
    )
    result = pipeline._load_stage3_inputs_for_week(
        stage1_dir, datetime.date(2026, 6, 8), "A",
    )
    assert result is not None
    assert result["week_start_ct"] == datetime.date(2026, 6, 8)
    assert result["arm"] == "A"
    assert len(result["daily_avg_temps_f"]) == 7
    assert len(result["hourly_hvac_records"]) == 168
    assert len(result["hourly_mains_records"]) == 168
    assert len(result["hourly_weather"]) == 168
    # Each hourly record carries supply_c_per_kwh from prices
    assert result["hourly_hvac_records"][0]["supply_c_per_kwh"] == pytest.approx(4.0)
    # Feed into _compute_weekly_row and verify it doesn't crash
    result["qualifies"] = True
    row = pipeline._compute_weekly_row(result)
    assert row["arm"] == "A"
    assert row["qualifies"] is True
    assert row["o1_dollars_per_cdd"] > 0  # 82°F-65 = 17 CDD/day → positive cost
    assert row["max_temp_f"] == pytest.approx(82.0)


def test_loader_output_feeds_stage2_quality_orchestrator(tmp_path):
    """The real-shape loader's output dicts must satisfy
    `_apply_rules_for_week`'s input contract. Smoke test: build a
    fixture, run the loader, then run `_apply_rules_for_week` against
    each loaded week and verify it doesn't crash and produces a
    qualifying row."""
    start = datetime.datetime(2026, 6, 8, 5, 0, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(2026, 6, 15, 5, 0, tzinfo=datetime.timezone.utc)
    df = build_refoss_channel_df(start, end, cadence_minutes=1)

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={"refoss.channel": df},
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-15T00:00:00-05:00",
    )

    assignment_csv = tmp_path / "assignment.csv"
    _write_test_assignment_csv(assignment_csv, [
        {"iso_week": "2026-W24", "monday_date": "2026-06-08", "arm": "A"},
    ])

    inputs = pipeline._load_week_inputs_from_stage1(
        stage1_dir=stage1_dir, assignment_csv=assignment_csv,
    )
    assert len(inputs) == 1
    # Continuous 1-min refoss data → no gaps detected
    assert inputs[0]["refoss_intervals"] == []
    result = pipeline._apply_rules_for_week(inputs[0])
    assert result.row["qualifying"] is True
    assert result.row["arm"] == "A"
    assert result.row["week_start_ct"] == "2026-06-08"
