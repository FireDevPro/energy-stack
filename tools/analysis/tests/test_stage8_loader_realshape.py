"""Real-shape Stage 8 loader tests.

Builds synthetic real-shape Stage 1 parquet bundles (per
`fixture_real_shape.write_bundle`) plus Stage 2 and Stage 3 CSV
outputs, then exercises the real `stage8_decomposition` orchestrator
WITHOUT monkeypatching `_load_stage8_inputs`. This is the
feature-level outside-in test for the Stage 8 loader path.

Stage 8 is built phase by phase. Each phase adds its assertions; the
test file grows as the loader gains capability. Tests use the
acceptance oracles in `docs/plans/stage8-loader-plan.md`.
"""
from __future__ import annotations

import csv
import datetime
from pathlib import Path

import pandas as pd
import pytest

from tools.analysis import pipeline
from tools.analysis.replay.manifest import OBSERVED_RECENT
from tools.analysis.tests.fixture_real_shape import (
    build_nws_forecast_df,
    write_bundle,
)


# Sum of DTOD delivery rates over 24 CT hours. Hand-computed from
# pipeline.DTOD_PERIODS_CT. Used by the o1 dollar oracle below.
#   Morning  (06-12, 7 hrs):  7 × 4.009  = 28.063
#   Mid-Day  (13-18, 6 hrs):  6 × 10.712 = 64.272
#   Evening  (19-20, 2 hrs):  2 × 3.747  =  7.494
#   Overnight pre  (21-23, 3 hrs): 3 × 2.984 =  8.952
#   Overnight post (00-05, 6 hrs): 6 × 2.984 = 17.904
#   Total                                   = 126.685
DTOD_SUM_FULL_DAY_CENTS = 126.685


def _write_qualifying_days_csv(
    path: Path,
    weeks: list[dict],
) -> None:
    """Write a Phase 0 stage2/qualifying_days.csv with one or more weeks.

    `weeks` is a list of dicts, each shaped:
      {"week_start_ct": datetime.date,
       "arm": "A" | "B",
       "included_day_indexes": list[int]}

    `included_day_indexes` are integers in [0, 7) listing which
    day-of-week positions are included. The rest are written as
    excluded with `rule9_vacation` as the exclusion source.
    """
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "week_start_ct", "arm", "date", "included", "exclusion_source",
        ])
        for spec in weeks:
            week_start_ct: datetime.date = spec["week_start_ct"]
            arm: str = spec["arm"]
            included = set(spec["included_day_indexes"])
            for i in range(7):
                d = week_start_ct + datetime.timedelta(days=i)
                is_included = i in included
                w.writerow([
                    week_start_ct.isoformat(),
                    arm,
                    d.isoformat(),
                    "true" if is_included else "false",
                    "" if is_included else "rule9_vacation",
                ])


def _write_stage3_weekly_csv(
    path: Path,
    rows: list[dict],
) -> None:
    """Write a minimal Stage 3 weekly.csv. Required columns only; the
    Phase 1 loader reads (week_start_ct, arm, qualifies). Other columns
    are filled with zeros to satisfy the locked schema.
    """
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(pipeline.WEEKLY_CSV_LOCKED_COLUMNS))
        w.writeheader()
        for row in rows:
            full = {col: "0" for col in pipeline.WEEKLY_CSV_LOCKED_COLUMNS}
            full.update(row)
            w.writerow(full)


def test_stage8_phase1_tracer_one_no_spike_day_oracle(tmp_path):
    """Phase 1 tracer acceptance test.

    One no-spike day, REAL loader path (no monkeypatch on
    `_load_stage8_inputs`). Exact dollar oracle for o1; verifies the
    tracer principle that Phase 1 populates ONLY `o1_daily_hvac_dollars`
    per day (o3/o4 are Phase 3) so the orchestrator's
    `outcome in d["outcomes"]` filter keeps o3/o4 from appearing as
    rows. Placeholder zeros would lie; missing rows are honest.

    Fixture:
      - Stage 1 parquet: refoss.channel em:2 power_w = 500 W constant
        for 24 CT hours (Mon 2026-06-08), 30 s cadence (matches prod).
        comed.prices price_cents_per_kwh = 6.0 ¢/kWh constant, 5 min
        cadence, period_type tag = "5min".
      - Stage 2 qualifying_days.csv: week 2026-06-08, Arm A, only
        Monday included; other 6 days excluded as rule9_vacation.
      - Stage 3 weekly.csv: same week qualifies, weekly_cdd=0 so the
        zero-CDD-still-counted property holds (Stage 8 ignores CDD).

    Oracle (hand-computed):
      hvac_kwh per hour = mean(em:2 power_w) / 1000 = 500/1000 = 0.5
      o1 = Σ_h (0.5 × (6.0 + DTOD(h))) / 100
         = 0.5 × (24 × 6.0 + Σ DTOD) / 100
         = 0.5 × (144 + 126.685) / 100
         = 1.353425 dollars

    Expectations:
      - decomposition.csv has EXACTLY ONE row.
      - That row is (o1_daily_hvac_dollars, no_spike), unit=dollars.
      - arm_a_median_value = 1.353425 exactly (1e-6 tolerance).
      - Arm B has no data -> quiet-zero guard fires: arm_b columns blank,
        delta_median_value blank, reason emitted in reason_report.json.
      - No o3_daily_peak_hvac_kw or o4_daily_mains_dollars rows exist
        (Phase 3 adds them; Phase 1 must not emit placeholder zeros).
      - No forecast_correlated_spike or grid_event_spike rows (Phase 2
        classification; Phase 1 hardcodes no_spike).
    """
    day_ct = datetime.date(2026, 6, 8)
    day_start_utc = pipeline._ct_date_to_utc(day_ct, 0)
    day_end_utc = pipeline._ct_date_to_utc(
        day_ct + datetime.timedelta(days=1), 0,
    )

    # Refoss em:2 power_w = 500 W constant, 30 s cadence (matches prod).
    refoss_times = pd.date_range(
        day_start_utc, day_end_utc, freq="30s", inclusive="left",
    )
    refoss_df = pd.DataFrame({
        "_time": refoss_times,
        "_measurement": "refoss.channel",
        "_field": "power_w",
        "_value": 500.0,
        "channel": "em:2",
    })

    # comed.prices: 5-min cadence, period_type=5min, 6.0 ¢/kWh.
    prices_times = pd.date_range(
        day_start_utc, day_end_utc, freq="5min", inclusive="left",
    )
    prices_df = pd.DataFrame({
        "_time": prices_times,
        "_measurement": "comed.prices",
        "_field": "price_cents_per_kwh",
        "_value": 6.0,
        "period_type": "5min",
    })

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "refoss.channel": refoss_df,
            "comed.prices": prices_df,
            "nws.forecast": build_nws_forecast_df([day_ct]),
        },
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-15T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )

    # Stage 2 qualifying_days.csv: only Monday included, other 6 days
    # excluded as rule9_vacation. Verifies the Phase 0 contract that
    # excluded days are dropped from Stage 8's decomposition.
    stage2_dir = tmp_path / "stage2"
    stage2_dir.mkdir()
    _write_qualifying_days_csv(
        stage2_dir / "qualifying_days.csv",
        weeks=[{
            "week_start_ct": day_ct,
            "arm": "A",
            "included_day_indexes": [0],
        }],
    )

    # Stage 3 weekly.csv: week qualifies. CDD=0 so the zero-CDD path is
    # exercised. Stage 8 should ignore CDD entirely; the day still
    # appears in decomposition.csv.
    stage3_dir = tmp_path / "stage3"
    stage3_dir.mkdir()
    _write_stage3_weekly_csv(
        stage3_dir / "weekly.csv",
        rows=[{
            "week_start_ct": day_ct.isoformat(),
            "arm": "A",
            "qualifies": "True",
            "weekly_cdd": "0",
        }],
    )

    # Run the real Stage 8 orchestrator end-to-end. No monkeypatch on
    # _load_stage8_inputs: this is the outside-in path under test.
    pipeline.stage8_decomposition(stage1_dir, stage3_dir, tmp_path)
    stage8_dir = tmp_path / "stage8"

    with open(stage8_dir / "decomposition.csv") as f:
        rows = list(csv.DictReader(f))

    # Phase 3 contract change: o1, o3, o4 each emit one row for this
    # no_spike day. Phase 1's "exactly one row" expectation widened to
    # three (one per Stage 8 outcome). The o1 oracle below is unchanged.
    assert len(rows) == 3, (
        f"Phase 3 emits 3 rows (o1, o3, o4) for one (day, category); "
        f"got {len(rows)}: {rows}"
    )
    by_outcome = {r["outcome"]: r for r in rows}
    assert set(by_outcome.keys()) == {
        "o1_daily_hvac_dollars",
        "o3_daily_peak_hvac_kw",
        "o4_daily_mains_dollars",
    }
    r = by_outcome["o1_daily_hvac_dollars"]
    assert r["category"] == "no_spike"
    assert r["unit"] == "dollars"

    # Exact dollar oracle. Pennies-tolerant (1e-6 dollars = 0.0001 cents).
    assert int(r["arm_a_n_days"]) == 1
    expected_o1 = 0.5 * (24 * 6.0 + DTOD_SUM_FULL_DAY_CENTS) / 100.0
    assert expected_o1 == pytest.approx(1.353425, abs=1e-6), (
        "Test sanity: oracle constant drifted from hand-computed value."
    )
    assert float(r["arm_a_median_value"]) == pytest.approx(
        expected_o1, abs=1e-6,
    )

    # Arm B has zero days -> quiet-zero guard fires for each outcome row.
    assert int(r["arm_b_n_days"]) == 0
    assert r["arm_b_median_value"] == ""
    assert r["delta_median_value"] == ""

    # Reason report records the quiet-zero firing for this cell.
    import json
    from tools.analysis.replay.reason_codes import ReasonCode
    reason_path = stage8_dir / "reason_report.json"
    assert reason_path.exists(), (
        "quiet-zero on Arm B should emit INSUFFICIENT_ARM_DAYS_FOR_CATEGORY"
    )
    with open(reason_path) as f:
        report = json.load(f)
    guard_entries = [
        e for e in report["entries"]
        if e["reason_code"]
        == ReasonCode.INSUFFICIENT_ARM_DAYS_FOR_CATEGORY.value
    ]
    # Phase 3 contract change: 3 outcomes (o1, o3, o4) each emit a row
    # for (no_spike), so 3 quiet-zero entries fire for Arm B (one per
    # outcome). Phase 0/1 had 1 entry (o1 only).
    assert len(guard_entries) == 3
    notes = " ".join(e.get("note") or "" for e in guard_entries)
    assert "o1_daily_hvac_dollars" in notes
    assert "o3_daily_peak_hvac_kw" in notes
    assert "o4_daily_mains_dollars" in notes
    assert "no_spike" in notes

    # layer_attribution.csv is header-only (Phase 4 populates).
    with open(stage8_dir / "layer_attribution.csv") as f:
        layer_rows = list(csv.DictReader(f))
    assert layer_rows == [], (
        "layer_attribution.csv should be header-only until Phase 4"
    )


def test_stage8_phase1_tracer_multi_day_multi_channel_oracle(tmp_path):
    """Phase 1 audit gap-closer: two included days in one qualifying
    week, all three HVAC channels (em:2, em:8, em:9) populated with
    different per-channel power values.

    Catches:
      - Day-loop bugs (single-day fixture wouldn't catch a "loop quits
        after day 1" regression).
      - Channel-set summing in `_load_daily_hourly_records` (Phase 1's
        original fixture had em:2 only; this exercises the sum across
        HVAC_CHANNELS).
      - Median aggregation across two distinct daily values.

    Oracle (hand-computed):
      Day 1 (Mon 2026-06-08, channels em:2=100W, em:8=200W, em:9=300W):
        hourly_kwh = (100 + 200 + 300) / 1000 = 0.6 kWh
        daily_o1 = 0.6 × (24 × 6.0 + 126.685) / 100
                 = 0.6 × 270.685 / 100
                 = 1.624110 dollars

      Day 2 (Tue 2026-06-09, channels em:2=200W, em:8=200W, em:9=100W):
        hourly_kwh = (200 + 200 + 100) / 1000 = 0.5 kWh
        daily_o1 = 0.5 × 270.685 / 100
                 = 1.353425 dollars

      Median of [1.624110, 1.353425] = (1.624110 + 1.353425) / 2
                                     = 1.4887675 dollars
    """
    week_start = datetime.date(2026, 6, 8)
    day1_ct = week_start
    day2_ct = week_start + datetime.timedelta(days=1)

    def _refoss_rows_for_day(
        day_ct: datetime.date,
        power_by_channel: dict[str, float],
    ) -> pd.DataFrame:
        day_start_utc = pipeline._ct_date_to_utc(day_ct, 0)
        day_end_utc = pipeline._ct_date_to_utc(
            day_ct + datetime.timedelta(days=1), 0,
        )
        times = pd.date_range(
            day_start_utc, day_end_utc, freq="30s", inclusive="left",
        )
        frames = []
        for channel, watts in power_by_channel.items():
            frames.append(pd.DataFrame({
                "_time": times,
                "_measurement": "refoss.channel",
                "_field": "power_w",
                "_value": float(watts),
                "channel": channel,
            }))
        return pd.concat(frames, ignore_index=True)

    refoss_df = pd.concat([
        _refoss_rows_for_day(day1_ct, {"em:2": 100.0, "em:8": 200.0, "em:9": 300.0}),
        _refoss_rows_for_day(day2_ct, {"em:2": 200.0, "em:8": 200.0, "em:9": 100.0}),
    ], ignore_index=True)

    # Constant supply price across both days (24h x 2 = 48h).
    prices_start_utc = pipeline._ct_date_to_utc(day1_ct, 0)
    prices_end_utc = pipeline._ct_date_to_utc(
        day2_ct + datetime.timedelta(days=1), 0,
    )
    prices_times = pd.date_range(
        prices_start_utc, prices_end_utc, freq="5min", inclusive="left",
    )
    prices_df = pd.DataFrame({
        "_time": prices_times,
        "_measurement": "comed.prices",
        "_field": "price_cents_per_kwh",
        "_value": 6.0,
        "period_type": "5min",
    })

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "refoss.channel": refoss_df,
            "comed.prices": prices_df,
            "nws.forecast": build_nws_forecast_df(
                [day1_ct, day2_ct],
            ),
        },
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-15T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )

    stage2_dir = tmp_path / "stage2"
    stage2_dir.mkdir()
    _write_qualifying_days_csv(
        stage2_dir / "qualifying_days.csv",
        weeks=[{
            "week_start_ct": week_start,
            "arm": "A",
            "included_day_indexes": [0, 1],  # Mon + Tue
        }],
    )

    stage3_dir = tmp_path / "stage3"
    stage3_dir.mkdir()
    _write_stage3_weekly_csv(
        stage3_dir / "weekly.csv",
        rows=[{
            "week_start_ct": week_start.isoformat(),
            "arm": "A",
            "qualifies": "True",
        }],
    )

    pipeline.stage8_decomposition(stage1_dir, stage3_dir, tmp_path)
    stage8_dir = tmp_path / "stage8"

    with open(stage8_dir / "decomposition.csv") as f:
        rows = list(csv.DictReader(f))

    # Phase 3 contract: 3 rows (one per outcome × no_spike) with
    # quiet-zero on Arm B. The o1 oracle below is unchanged.
    assert len(rows) == 3
    by_outcome = {r["outcome"]: r for r in rows}
    r = by_outcome["o1_daily_hvac_dollars"]
    assert r["category"] == "no_spike"
    assert int(r["arm_a_n_days"]) == 2, (
        "Day-loop must process both included days"
    )

    # Hand-computed: day 1 = 0.6 × 270.685 / 100, day 2 = 0.5 × 270.685 / 100
    day1_o1 = 0.6 * (24 * 6.0 + DTOD_SUM_FULL_DAY_CENTS) / 100.0
    day2_o1 = 0.5 * (24 * 6.0 + DTOD_SUM_FULL_DAY_CENTS) / 100.0
    expected_median = (day1_o1 + day2_o1) / 2.0
    assert day1_o1 == pytest.approx(1.624110, abs=1e-6)
    assert day2_o1 == pytest.approx(1.353425, abs=1e-6)
    assert expected_median == pytest.approx(1.4887675, abs=1e-6)
    assert float(r["arm_a_median_value"]) == pytest.approx(
        expected_median, abs=1e-6,
    )

    # Arm B has no data -> quiet-zero.
    assert int(r["arm_b_n_days"]) == 0
    assert r["arm_b_median_value"] == ""
    assert r["delta_median_value"] == ""


def test_stage8_phase1_tracer_both_arms_real_loader_delta(tmp_path):
    """Phase 1 audit gap-closer: both arms present in the qualifying
    set, exercised through the real loader.

    Two qualifying weeks: Arm A on Mon 2026-06-08, Arm B on Mon
    2026-06-15. Each week has one included day with different power
    so the per-arm medians differ and the delta is non-zero.

    Catches:
      - Real-loader Arm B path (Phase 1's original fixture had Arm A only).
      - Delta subtraction direction (Arm B - Arm A, not Arm A - Arm B).
      - Quiet-zero NOT firing when both arms have data.

    Oracle (hand-computed):
      Arm A Mon 06-08 (em:2 = 500W): hourly_kwh = 0.5
        daily_o1 = 0.5 × 270.685 / 100 = 1.353425

      Arm B Mon 06-15 (em:2 = 400W): hourly_kwh = 0.4
        daily_o1 = 0.4 × 270.685 / 100 = 1.082740

      delta_median = arm_b_median - arm_a_median = 1.082740 - 1.353425
                   = -0.270685
    """
    arm_a_day = datetime.date(2026, 6, 8)
    arm_b_day = datetime.date(2026, 6, 15)

    def _refoss_day(day_ct: datetime.date, watts_em2: float) -> pd.DataFrame:
        day_start_utc = pipeline._ct_date_to_utc(day_ct, 0)
        day_end_utc = pipeline._ct_date_to_utc(
            day_ct + datetime.timedelta(days=1), 0,
        )
        times = pd.date_range(
            day_start_utc, day_end_utc, freq="30s", inclusive="left",
        )
        return pd.DataFrame({
            "_time": times,
            "_measurement": "refoss.channel",
            "_field": "power_w",
            "_value": watts_em2,
            "channel": "em:2",
        })

    refoss_df = pd.concat([
        _refoss_day(arm_a_day, 500.0),
        _refoss_day(arm_b_day, 400.0),
    ], ignore_index=True)

    # Prices: constant 6.0 c/kWh across the full window.
    prices_start_utc = pipeline._ct_date_to_utc(arm_a_day, 0)
    prices_end_utc = pipeline._ct_date_to_utc(
        arm_b_day + datetime.timedelta(days=1), 0,
    )
    prices_times = pd.date_range(
        prices_start_utc, prices_end_utc, freq="5min", inclusive="left",
    )
    prices_df = pd.DataFrame({
        "_time": prices_times,
        "_measurement": "comed.prices",
        "_field": "price_cents_per_kwh",
        "_value": 6.0,
        "period_type": "5min",
    })

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "refoss.channel": refoss_df,
            "comed.prices": prices_df,
            "nws.forecast": build_nws_forecast_df(
                [arm_a_day, arm_b_day],
            ),
        },
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-22T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )

    stage2_dir = tmp_path / "stage2"
    stage2_dir.mkdir()
    _write_qualifying_days_csv(
        stage2_dir / "qualifying_days.csv",
        weeks=[
            {"week_start_ct": arm_a_day, "arm": "A",
             "included_day_indexes": [0]},
            {"week_start_ct": arm_b_day, "arm": "B",
             "included_day_indexes": [0]},
        ],
    )

    stage3_dir = tmp_path / "stage3"
    stage3_dir.mkdir()
    _write_stage3_weekly_csv(
        stage3_dir / "weekly.csv",
        rows=[
            {"week_start_ct": arm_a_day.isoformat(), "arm": "A",
             "qualifies": "True"},
            {"week_start_ct": arm_b_day.isoformat(), "arm": "B",
             "qualifies": "True"},
        ],
    )

    pipeline.stage8_decomposition(stage1_dir, stage3_dir, tmp_path)
    stage8_dir = tmp_path / "stage8"

    with open(stage8_dir / "decomposition.csv") as f:
        rows = list(csv.DictReader(f))

    # Phase 3 contract: 3 rows (one per outcome × no_spike). The o1
    # delta oracle below is unchanged.
    assert len(rows) == 3
    by_outcome = {r["outcome"]: r for r in rows}
    r = by_outcome["o1_daily_hvac_dollars"]
    assert r["category"] == "no_spike"
    assert int(r["arm_a_n_days"]) == 1
    assert int(r["arm_b_n_days"]) == 1

    arm_a_o1 = 0.5 * (24 * 6.0 + DTOD_SUM_FULL_DAY_CENTS) / 100.0
    arm_b_o1 = 0.4 * (24 * 6.0 + DTOD_SUM_FULL_DAY_CENTS) / 100.0
    expected_delta = arm_b_o1 - arm_a_o1
    assert arm_a_o1 == pytest.approx(1.353425, abs=1e-6)
    assert arm_b_o1 == pytest.approx(1.082740, abs=1e-6)
    assert expected_delta == pytest.approx(-0.270685, abs=1e-6)

    assert float(r["arm_a_median_value"]) == pytest.approx(
        arm_a_o1, abs=1e-6,
    )
    assert float(r["arm_b_median_value"]) == pytest.approx(
        arm_b_o1, abs=1e-6,
    )
    assert float(r["delta_median_value"]) == pytest.approx(
        expected_delta, abs=1e-6,
    )

    # Both arms have data -> quiet-zero must NOT fire. No reason report.
    assert not (stage8_dir / "reason_report.json").exists(), (
        "Quiet-zero guard should not fire when both arms have data"
    )


# -- Phase 2: spike classification (nws.forecast 21:00-prior lookup) -------


def _forecast_row(
    time_utc: pd.Timestamp,
    field: str,
    value,
    for_period: str = "tomorrow",
) -> dict:
    """Build one long-format nws.forecast row matching the production
    parquet schema (post-_split_value_column_by_type). String fields
    use _value_text; numeric fields use _value with _value_text=None.
    """
    if isinstance(value, str):
        return {
            "_time": time_utc,
            "_measurement": "nws.forecast",
            "_field": field,
            "_value": float("nan"),
            "_value_text": value,
            "for_period": for_period,
        }
    return {
        "_time": time_utc,
        "_measurement": "nws.forecast",
        "_field": field,
        "_value": float(value),
        "_value_text": None,
        "for_period": for_period,
    }


def _ct_to_utc(year, month, day, hour, minute=0) -> pd.Timestamp:
    """Helper to compose a CT-local time as a UTC pandas Timestamp."""
    from zoneinfo import ZoneInfo
    ct = ZoneInfo("America/Chicago")
    dt = datetime.datetime(year, month, day, hour, minute, tzinfo=ct)
    return pd.Timestamp(dt.astimezone(datetime.timezone.utc))


def _issuance_rows(
    time_utc: pd.Timestamp,
    high_f: float,
    apparent_max_f: float,
    period_date: str,
    for_period: str = "tomorrow",
) -> list[dict]:
    """Build the 3 long-format rows representing one nws.forecast
    issuance (high_f, apparent_max_f, period_date all share _time).
    """
    return [
        _forecast_row(time_utc, "high_f", high_f, for_period),
        _forecast_row(time_utc, "apparent_max_f", apparent_max_f, for_period),
        _forecast_row(time_utc, "period_date", period_date, for_period),
    ]


def test_phase2_forecast_high_f_maps_to_max_forecast_temp_f():
    """Phase 2 oracle: _forecast_for_day_ct maps nws.forecast.high_f ->
    classifier's max_forecast_temp_f argument.

    Catches a regression that maps the wrong production field (e.g.
    a hypothetical 'max_temp_f' or 'forecast_high') as the analysis
    vocabulary's max_forecast_temp_f.
    """
    day_ct = datetime.date(2026, 6, 8)
    # D-1 21:00 CT exact.
    t = _ct_to_utc(2026, 6, 7, 21, 0)
    forecast_df = pd.DataFrame(_issuance_rows(
        t, high_f=87.0, apparent_max_f=90.0,
        period_date="2026-06-08",
    ))

    result = pipeline._forecast_for_day_ct(forecast_df, day_ct)
    assert result is not None
    assert result["max_forecast_temp_f"] == pytest.approx(87.0)
    assert result["apparent_max_f"] == pytest.approx(90.0)


def test_phase2_forecast_21_00_prior_beats_14_00():
    """Phase 2 oracle: when D-1 has two issuances (14:00 + 21:00 CT),
    the 21:00 issuance is selected. The 14:00 one is outside the
    +/- 30 min window and must not be used.
    """
    day_ct = datetime.date(2026, 6, 8)
    t_1400 = _ct_to_utc(2026, 6, 7, 14, 0)
    t_2100 = _ct_to_utc(2026, 6, 7, 21, 0)
    rows = (
        _issuance_rows(t_1400, high_f=70.0, apparent_max_f=75.0,
                       period_date="2026-06-08")
        + _issuance_rows(t_2100, high_f=87.0, apparent_max_f=90.0,
                         period_date="2026-06-08")
    )
    forecast_df = pd.DataFrame(rows)
    result = pipeline._forecast_for_day_ct(forecast_df, day_ct)
    assert result is not None
    assert result["max_forecast_temp_f"] == pytest.approx(87.0), (
        "21:00-prior issuance must be selected, not 14:00"
    )


def test_phase2_forecast_multiple_near_21_chooses_closest():
    """Phase 2 oracle (Chris's audit amendment): when multiple
    `for_period=tomorrow` rows fall inside the +/- 30 min window
    around D-1 21:00 CT, the helper selects the issuance whose _time
    is CLOSEST to 21:00, not the first arbitrary one.

    Fixture issuances on 2026-06-07:
      20:35 CT (25 min before target, high_f=80) — within window
      20:55 CT  (5 min before target, high_f=85) — CLOSEST, must win
      21:15 CT (15 min after target,  high_f=90) — within window
    """
    day_ct = datetime.date(2026, 6, 8)
    t_20_35 = _ct_to_utc(2026, 6, 7, 20, 35)
    t_20_55 = _ct_to_utc(2026, 6, 7, 20, 55)
    t_21_15 = _ct_to_utc(2026, 6, 7, 21, 15)
    rows = (
        _issuance_rows(t_20_35, high_f=80.0, apparent_max_f=85.0,
                       period_date="2026-06-08")
        + _issuance_rows(t_20_55, high_f=85.0, apparent_max_f=88.0,
                         period_date="2026-06-08")
        + _issuance_rows(t_21_15, high_f=90.0, apparent_max_f=92.0,
                         period_date="2026-06-08")
    )
    forecast_df = pd.DataFrame(rows)
    result = pipeline._forecast_for_day_ct(forecast_df, day_ct)
    assert result is not None
    assert result["max_forecast_temp_f"] == pytest.approx(85.0), (
        "Helper must choose closest-to-21:00 issuance (20:55, 5 min "
        "off), not 20:35 (25 min off) or 21:15 (15 min off)"
    )


def test_phase2_forecast_today_row_not_selected():
    """Phase 2 filter test: a `for_period="today"` row at exactly
    D-1 21:00 CT must NOT be selected. Catches a removed or wrong
    `for_period` filter.

    The fixture is constructed so the row WOULD match every other
    constraint (exact-target _time, period_date == D.isoformat()) —
    only the `for_period` tag value disqualifies it.
    """
    day_ct = datetime.date(2026, 6, 8)
    t = _ct_to_utc(2026, 6, 7, 21, 0)
    forecast_df = pd.DataFrame(_issuance_rows(
        t,
        high_f=99.0,
        apparent_max_f=99.0,
        period_date=day_ct.isoformat(),
        for_period="today",  # WRONG: only "tomorrow" should match.
    ))
    result = pipeline._forecast_for_day_ct(forecast_df, day_ct)
    assert result is None, (
        "for_period='today' row must be rejected even when _time "
        "and period_date both match the target day"
    )


def test_phase2_forecast_period_date_mismatch_rejected():
    """Phase 2 cross-check test: a `for_period="tomorrow"` row whose
    `period_date` points at a different calendar day must NOT be
    selected. Catches a removed `period_date` cross-check.

    The fixture is constructed so the row WOULD match every other
    constraint (exact-target _time, correct `for_period`) — only the
    `period_date` field's value (D+1 instead of D) disqualifies it.
    """
    day_ct = datetime.date(2026, 6, 8)
    wrong_day = day_ct + datetime.timedelta(days=1)
    t = _ct_to_utc(2026, 6, 7, 21, 0)
    forecast_df = pd.DataFrame(_issuance_rows(
        t,
        high_f=99.0,
        apparent_max_f=99.0,
        period_date=wrong_day.isoformat(),  # WRONG: should be day_ct.
        for_period="tomorrow",
    ))
    result = pipeline._forecast_for_day_ct(forecast_df, day_ct)
    assert result is None, (
        "tomorrow row with period_date != D must be rejected even "
        "when _time falls in the +/- 30 min window"
    )


def test_phase2_three_category_fixture_via_real_loader(tmp_path):
    """Phase 2 integration: three included days each producing a
    different spike category. The full pipeline (real
    `_load_stage8_inputs` -> classify_spike_day -> orchestrator)
    surfaces one row per (o1, category) for the three categories.

    Per pipeline.classify_spike_day():
      no_spike: no hour >= 10c
      forecast_correlated_spike: spike AND (temp >= 85 OR apparent >= 90)
      grid_event_spike: spike AND temp < 85 AND apparent < 90

    Fixture (Arm A only, Mon-Wed of week 2026-06-08):
      Mon 06-08: prices 5c constant, forecast 80F/85F -> no_spike
      Tue 06-09: prices include 15c, forecast 90F/95F -> forecast_correlated
      Wed 06-10: prices include 15c, forecast 70F/80F -> grid_event
    """
    week_start = datetime.date(2026, 6, 8)
    mon = week_start
    tue = week_start + datetime.timedelta(days=1)
    wed = week_start + datetime.timedelta(days=2)

    def _refoss_day_em2(day_ct: datetime.date, watts: float) -> pd.DataFrame:
        day_start_utc = pipeline._ct_date_to_utc(day_ct, 0)
        day_end_utc = pipeline._ct_date_to_utc(
            day_ct + datetime.timedelta(days=1), 0,
        )
        times = pd.date_range(
            day_start_utc, day_end_utc, freq="30s", inclusive="left",
        )
        return pd.DataFrame({
            "_time": times,
            "_measurement": "refoss.channel",
            "_field": "power_w",
            "_value": watts,
            "channel": "em:2",
        })

    def _prices_day(day_ct: datetime.date, base_c: float,
                    spike_hours: list[int] | None = None,
                    spike_c: float = 15.0) -> pd.DataFrame:
        day_start_utc = pipeline._ct_date_to_utc(day_ct, 0)
        day_end_utc = pipeline._ct_date_to_utc(
            day_ct + datetime.timedelta(days=1), 0,
        )
        times = pd.date_range(
            day_start_utc, day_end_utc, freq="5min", inclusive="left",
        )
        df = pd.DataFrame({
            "_time": times,
            "_measurement": "comed.prices",
            "_field": "price_cents_per_kwh",
            "_value": base_c,
            "period_type": "5min",
        })
        if spike_hours:
            # Set the 5-min slots inside spike_hours to spike_c.
            for h in spike_hours:
                hour_start_utc = pipeline._ct_date_to_utc(day_ct, h)
                hour_end_utc = pipeline._ct_date_to_utc(day_ct, h) + (
                    datetime.timedelta(hours=1)
                )
                mask = (df["_time"] >= hour_start_utc) & (df["_time"] < hour_end_utc)
                df.loc[mask, "_value"] = spike_c
        return df

    refoss_df = pd.concat([
        _refoss_day_em2(mon, 500.0),
        _refoss_day_em2(tue, 500.0),
        _refoss_day_em2(wed, 500.0),
    ], ignore_index=True)
    prices_df = pd.concat([
        _prices_day(mon, base_c=5.0),
        _prices_day(tue, base_c=5.0, spike_hours=[17]),
        _prices_day(wed, base_c=5.0, spike_hours=[17]),
    ], ignore_index=True)
    forecast_rows = (
        _issuance_rows(_ct_to_utc(2026, 6, 7, 21, 0),
                       high_f=80.0, apparent_max_f=85.0,
                       period_date=mon.isoformat())
        + _issuance_rows(_ct_to_utc(2026, 6, 8, 21, 0),
                         high_f=90.0, apparent_max_f=95.0,
                         period_date=tue.isoformat())
        + _issuance_rows(_ct_to_utc(2026, 6, 9, 21, 0),
                         high_f=70.0, apparent_max_f=80.0,
                         period_date=wed.isoformat())
    )
    forecast_df = pd.DataFrame(forecast_rows)

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "refoss.channel": refoss_df,
            "comed.prices": prices_df,
            "nws.forecast": forecast_df,
        },
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-15T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )

    stage2_dir = tmp_path / "stage2"
    stage2_dir.mkdir()
    _write_qualifying_days_csv(
        stage2_dir / "qualifying_days.csv",
        weeks=[{
            "week_start_ct": week_start,
            "arm": "A",
            "included_day_indexes": [0, 1, 2],  # Mon, Tue, Wed
        }],
    )

    stage3_dir = tmp_path / "stage3"
    stage3_dir.mkdir()
    _write_stage3_weekly_csv(
        stage3_dir / "weekly.csv",
        rows=[{
            "week_start_ct": week_start.isoformat(),
            "arm": "A",
            "qualifies": "True",
        }],
    )

    pipeline.stage8_decomposition(stage1_dir, stage3_dir, tmp_path)
    stage8_dir = tmp_path / "stage8"
    with open(stage8_dir / "decomposition.csv") as f:
        rows = list(csv.DictReader(f))

    # Phase 3 contract change: 9 rows = 3 outcomes (o1, o3, o4) × 3
    # categories (no_spike, forecast_correlated_spike, grid_event_spike).
    # All Arm A only -> quiet-zero on B for every cell.
    assert len(rows) == 9
    categories = {r["category"] for r in rows}
    assert categories == {"no_spike", "forecast_correlated_spike",
                          "grid_event_spike"}, (
        f"expected all 3 categories, got {categories}"
    )
    outcomes_seen = {r["outcome"] for r in rows}
    assert outcomes_seen == {
        "o1_daily_hvac_dollars",
        "o3_daily_peak_hvac_kw",
        "o4_daily_mains_dollars",
    }
    expected_unit = {
        "o1_daily_hvac_dollars": "dollars",
        "o3_daily_peak_hvac_kw": "kw",
        "o4_daily_mains_dollars": "dollars",
    }
    for r in rows:
        assert r["unit"] == expected_unit[r["outcome"]]
        assert int(r["arm_a_n_days"]) == 1
        assert int(r["arm_b_n_days"]) == 0


def test_phase2_missing_21_00_issuance_drops_day_and_emits_reason(tmp_path):
    """Phase 2 oracle: when no nws.forecast issuance exists in the
    D-1 21:00 CT +/- 30 min window for an otherwise-qualifying day,
    the day is DROPPED from decomposition and a
    NO_NWS_FORECAST_FOR_CLASSIFICATION reason is emitted per-day in
    reason_report.json.
    """
    import json
    from tools.analysis.replay.reason_codes import ReasonCode

    day_ct = datetime.date(2026, 6, 8)
    day_start_utc = pipeline._ct_date_to_utc(day_ct, 0)
    day_end_utc = pipeline._ct_date_to_utc(
        day_ct + datetime.timedelta(days=1), 0,
    )

    refoss_times = pd.date_range(
        day_start_utc, day_end_utc, freq="30s", inclusive="left",
    )
    refoss_df = pd.DataFrame({
        "_time": refoss_times,
        "_measurement": "refoss.channel",
        "_field": "power_w",
        "_value": 500.0,
        "channel": "em:2",
    })
    prices_times = pd.date_range(
        day_start_utc, day_end_utc, freq="5min", inclusive="left",
    )
    prices_df = pd.DataFrame({
        "_time": prices_times,
        "_measurement": "comed.prices",
        "_field": "price_cents_per_kwh",
        "_value": 6.0,
        "period_type": "5min",
    })
    # nws.forecast: only a 14:00 CT issuance on D-1 (outside the window).
    forecast_rows = _issuance_rows(
        _ct_to_utc(2026, 6, 7, 14, 0),
        high_f=70.0, apparent_max_f=75.0,
        period_date=day_ct.isoformat(),
    )
    forecast_df = pd.DataFrame(forecast_rows)

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "refoss.channel": refoss_df,
            "comed.prices": prices_df,
            "nws.forecast": forecast_df,
        },
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-15T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )

    stage2_dir = tmp_path / "stage2"
    stage2_dir.mkdir()
    _write_qualifying_days_csv(
        stage2_dir / "qualifying_days.csv",
        weeks=[{
            "week_start_ct": day_ct,
            "arm": "A",
            "included_day_indexes": [0],
        }],
    )

    stage3_dir = tmp_path / "stage3"
    stage3_dir.mkdir()
    _write_stage3_weekly_csv(
        stage3_dir / "weekly.csv",
        rows=[{
            "week_start_ct": day_ct.isoformat(),
            "arm": "A",
            "qualifies": "True",
        }],
    )

    pipeline.stage8_decomposition(stage1_dir, stage3_dir, tmp_path)
    stage8_dir = tmp_path / "stage8"

    with open(stage8_dir / "decomposition.csv") as f:
        rows = list(csv.DictReader(f))
    # All days dropped -> no daily_records -> all (outcome, category)
    # cells are both-arms-zero -> all skipped. CSV is header-only.
    assert rows == [], (
        "Day with missing forecast should be dropped; no rows emitted"
    )

    reason_path = stage8_dir / "reason_report.json"
    assert reason_path.exists()
    with open(reason_path) as f:
        report = json.load(f)
    codes = {e["reason_code"] for e in report["entries"]}
    assert (
        ReasonCode.NO_NWS_FORECAST_FOR_CLASSIFICATION.value in codes
    ), (
        f"expected NO_NWS_FORECAST_FOR_CLASSIFICATION; got {codes}"
    )
    nws_entries = [
        e for e in report["entries"]
        if e["reason_code"]
        == ReasonCode.NO_NWS_FORECAST_FOR_CLASSIFICATION.value
    ]
    # Per-day note: the entry mentions the dropped date + arm.
    assert any(
        day_ct.isoformat() in (e.get("note") or "") for e in nws_entries
    ), "missing-forecast reason should name the dropped date"


# -- Phase 3: per-day outcome arithmetic (o1, o3, o4) -----------------------


# Locked DTOD per-hour values (cents/kWh) for a non-DST cooling-season
# day, mirroring pipeline.DTOD_PERIODS_CT. Used by Phase 3 oracles so
# the hand-computation in the test does not import any production code.
DTOD_PER_HOUR_CENTS = (
    [2.984] * 6      # hours 0-5  (Overnight post-midnight)
    + [4.009] * 7    # hours 6-12 (Morning)
    + [10.712] * 6   # hours 13-18 (Mid-Day Peak)
    + [3.747] * 2    # hours 19-20 (Evening)
    + [2.984] * 3    # hours 21-23 (Overnight pre-midnight)
)
assert len(DTOD_PER_HOUR_CENTS) == 24


def _per_channel_refoss_constant_for_band(
    day_ct: datetime.date,
    hours_range,
    power_w_by_channel: dict[str, float],
) -> pd.DataFrame:
    """Build refoss.channel rows for a contiguous hour-of-day range
    inside one CT day. Each channel writes power_w constant across
    the band at 30-second cadence (matches prod refoss).
    """
    band_start_utc = pipeline._ct_date_to_utc(day_ct, min(hours_range))
    # Stop at the start of the hour AFTER the last hour in the band.
    band_stop_utc = pipeline._ct_date_to_utc(
        day_ct, max(hours_range),
    ) + datetime.timedelta(hours=1)
    times = pd.date_range(
        band_start_utc, band_stop_utc, freq="30s", inclusive="left",
    )
    frames = []
    for channel, watts in power_w_by_channel.items():
        frames.append(pd.DataFrame({
            "_time": times,
            "_measurement": "refoss.channel",
            "_field": "power_w",
            "_value": float(watts),
            "channel": channel,
        }))
    return pd.concat(frames, ignore_index=True)


def _supply_prices_for_band(
    day_ct: datetime.date,
    hours_range,
    cents_per_kwh: float,
) -> pd.DataFrame:
    """5-minute supply-price rows for a contiguous hour-of-day band."""
    band_start_utc = pipeline._ct_date_to_utc(day_ct, min(hours_range))
    band_stop_utc = pipeline._ct_date_to_utc(
        day_ct, max(hours_range),
    ) + datetime.timedelta(hours=1)
    times = pd.date_range(
        band_start_utc, band_stop_utc, freq="5min", inclusive="left",
    )
    return pd.DataFrame({
        "_time": times,
        "_measurement": "comed.prices",
        "_field": "price_cents_per_kwh",
        "_value": cents_per_kwh,
        "period_type": "5min",
    })


def test_stage8_phase3_asymmetric_o1_o4_distinct_and_exact(tmp_path):
    """Phase 3 acceptance: asymmetric hourly fixture pins exact daily
    dollar values for o1 (HVAC) and o4 (mains) and proves the channel
    split is not mixed up.

    Per Chris's audit requirements:
      - HVAC and mains channels carry DIFFERENT power, mains much larger,
        so a channel mix-up cannot pass.
      - Non-uniform kWh AND non-uniform supply prices, so neither a
        "multiply after summing" mistake nor sum-vs-mean confusion can
        pass silently.

    Hand-computed oracles (no production code referenced in the
    expectation derivation; DTOD_PER_HOUR_CENTS is a test-side constant
    mirroring DTOD_PERIODS_CT):

      Three power bands aligned with DTOD periods:
        Hours 0-5 / 21-23 (Overnight, 9 hrs total):
          HVAC = em:2+em:8+em:9 = 100+150+50 = 300 W   -> 0.3 kWh/hr
          mains = em:1+em:7     = 1000+500   = 1500 W  -> 1.5 kWh/hr
          supply = 4.0 c/kWh

        Hours 6-12, 19-20 (Morning + Evening, 9 hrs total):
          HVAC = em:2+em:8+em:9 = 250+250+100 = 600 W  -> 0.6 kWh/hr
          mains = em:1+em:7     = 1200+800    = 2000 W -> 2.0 kWh/hr
          supply = 6.0 c/kWh

        Hours 13-18 (Mid-Day, 6 hrs total):
          HVAC = em:2+em:8+em:9 = 600+400+200 = 1200 W -> 1.2 kWh/hr
          mains = em:1+em:7     = 1500+1000   = 2500 W -> 2.5 kWh/hr
          supply = 9.0 c/kWh (high but < 10 -> classify as no_spike)

    Catches:
      - Channel mix-up (o1 would use mains values or vice versa).
      - Multiply-after-summing mistake (the band-aligned correlation
        between high kWh and high supply prices makes the wrong
        formula numerically distinct).
      - Sum-vs-mean confusion in o1/o4 (24-hour sum is well-defined;
        mean would yield much smaller numbers).
      - Wrong DTOD rate lookup.
    """
    day_ct = datetime.date(2026, 6, 8)

    # (hours, hvac_per_channel_W, mains_per_channel_W, supply_c)
    BANDS = [
        (range(0, 6),   {"em:2": 100.0, "em:8": 150.0, "em:9":  50.0},
                        {"em:1": 1000.0, "em:7": 500.0},   4.0),
        (range(6, 13),  {"em:2": 250.0, "em:8": 250.0, "em:9": 100.0},
                        {"em:1": 1200.0, "em:7": 800.0},   6.0),
        (range(13, 19), {"em:2": 600.0, "em:8": 400.0, "em:9": 200.0},
                        {"em:1": 1500.0, "em:7": 1000.0},  9.0),
        (range(19, 21), {"em:2": 250.0, "em:8": 250.0, "em:9": 100.0},
                        {"em:1": 1200.0, "em:7": 800.0},   6.0),
        (range(21, 24), {"em:2": 100.0, "em:8": 150.0, "em:9":  50.0},
                        {"em:1": 1000.0, "em:7": 500.0},   4.0),
    ]

    refoss_frames = []
    prices_frames = []
    for hours, hvac_pwr, mains_pwr, supply_c in BANDS:
        all_pwr = {**hvac_pwr, **mains_pwr}
        refoss_frames.append(
            _per_channel_refoss_constant_for_band(day_ct, hours, all_pwr)
        )
        prices_frames.append(
            _supply_prices_for_band(day_ct, hours, supply_c)
        )
    refoss_df = pd.concat(refoss_frames, ignore_index=True)
    prices_df = pd.concat(prices_frames, ignore_index=True)

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "refoss.channel": refoss_df,
            "comed.prices": prices_df,
            "nws.forecast": build_nws_forecast_df([day_ct]),
        },
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-15T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )

    stage2_dir = tmp_path / "stage2"
    stage2_dir.mkdir()
    _write_qualifying_days_csv(
        stage2_dir / "qualifying_days.csv",
        weeks=[{"week_start_ct": day_ct, "arm": "A",
                "included_day_indexes": [0]}],
    )
    stage3_dir = tmp_path / "stage3"
    stage3_dir.mkdir()
    _write_stage3_weekly_csv(
        stage3_dir / "weekly.csv",
        rows=[{"week_start_ct": day_ct.isoformat(),
               "arm": "A", "qualifies": "True"}],
    )

    pipeline.stage8_decomposition(stage1_dir, stage3_dir, tmp_path)
    stage8_dir = tmp_path / "stage8"
    with open(stage8_dir / "decomposition.csv") as f:
        rows = list(csv.DictReader(f))

    # Hand-derive expected o1 + o4 from the same band definitions used
    # to build the fixture. The derivation calls no production code.
    expected_o1_cents = 0.0
    expected_o4_cents = 0.0
    for hours, hvac_pwr, mains_pwr, supply_c in BANDS:
        hvac_kwh_per_hour = sum(hvac_pwr.values()) / 1000.0
        mains_kwh_per_hour = sum(mains_pwr.values()) / 1000.0
        for h in hours:
            tariff_c = supply_c + DTOD_PER_HOUR_CENTS[h]
            expected_o1_cents += hvac_kwh_per_hour * tariff_c
            expected_o4_cents += mains_kwh_per_hour * tariff_c
    expected_o1 = expected_o1_cents / 100.0
    expected_o4 = expected_o4_cents / 100.0

    # Sanity: the two values must be distinct, else a channel mix-up
    # could silently pass. (Hand-derivation already differs because
    # mains carries much larger power than HVAC.)
    assert abs(expected_o1 - expected_o4) > 1.0, (
        "Test sanity: o1 and o4 must be distinct enough to prove "
        "channel split"
    )

    by_outcome = {r["outcome"]: r for r in rows if r["category"] == "no_spike"}
    assert "o1_daily_hvac_dollars" in by_outcome
    assert "o4_daily_mains_dollars" in by_outcome
    assert by_outcome["o1_daily_hvac_dollars"]["unit"] == "dollars"
    assert by_outcome["o4_daily_mains_dollars"]["unit"] == "dollars"

    o1_row = by_outcome["o1_daily_hvac_dollars"]
    o4_row = by_outcome["o4_daily_mains_dollars"]
    assert float(o1_row["arm_a_median_value"]) == pytest.approx(
        expected_o1, abs=1e-4,
    ), f"o1 mismatch: expected {expected_o1}, got {o1_row['arm_a_median_value']}"
    assert float(o4_row["arm_a_median_value"]) == pytest.approx(
        expected_o4, abs=1e-4,
    ), f"o4 mismatch: expected {expected_o4}, got {o4_row['arm_a_median_value']}"


def test_stage8_phase3_o3_is_max_hvac_kwh_not_mean(tmp_path):
    """Phase 3 acceptance: o3_daily_peak_hvac_kw is max(hourly hvac_kwh),
    not the day's mean. Fixture has one spiky hour at 2.0 kWh and 23
    flat hours at 0.2 kWh, so max and mean are far apart:

      max  = 2.0
      mean = (23 * 0.2 + 2.0) / 24 = 6.6 / 24 = 0.275

    Catches: an implementation that returned mean, sum, median, or
    last-hour value.

    Unit asserted as `kw` (not dollars) so the o3 row's unit column
    is also pinned.
    """
    day_ct = datetime.date(2026, 6, 8)
    day_start_utc = pipeline._ct_date_to_utc(day_ct, 0)
    day_end_utc = pipeline._ct_date_to_utc(
        day_ct + datetime.timedelta(days=1), 0,
    )
    refoss_times = pd.date_range(
        day_start_utc, day_end_utc, freq="30s", inclusive="left",
    )
    # Default em:2 = 200W -> 0.2 kWh/hr. Overwrite hour 14 (CT) -> 2000W.
    refoss_df = pd.DataFrame({
        "_time": refoss_times,
        "_measurement": "refoss.channel",
        "_field": "power_w",
        "_value": 200.0,
        "channel": "em:2",
    })
    spike_hour_start_utc = pipeline._ct_date_to_utc(day_ct, 14)
    spike_hour_end_utc = pipeline._ct_date_to_utc(day_ct, 14) + (
        datetime.timedelta(hours=1)
    )
    spike_mask = (
        (refoss_df["_time"] >= spike_hour_start_utc)
        & (refoss_df["_time"] < spike_hour_end_utc)
    )
    refoss_df.loc[spike_mask, "_value"] = 2000.0

    # Constant 5c supply (below spike threshold -> no_spike).
    prices_times = pd.date_range(
        day_start_utc, day_end_utc, freq="5min", inclusive="left",
    )
    prices_df = pd.DataFrame({
        "_time": prices_times,
        "_measurement": "comed.prices",
        "_field": "price_cents_per_kwh",
        "_value": 5.0,
        "period_type": "5min",
    })

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "refoss.channel": refoss_df,
            "comed.prices": prices_df,
            "nws.forecast": build_nws_forecast_df([day_ct]),
        },
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-15T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )

    stage2_dir = tmp_path / "stage2"
    stage2_dir.mkdir()
    _write_qualifying_days_csv(
        stage2_dir / "qualifying_days.csv",
        weeks=[{"week_start_ct": day_ct, "arm": "A",
                "included_day_indexes": [0]}],
    )
    stage3_dir = tmp_path / "stage3"
    stage3_dir.mkdir()
    _write_stage3_weekly_csv(
        stage3_dir / "weekly.csv",
        rows=[{"week_start_ct": day_ct.isoformat(),
               "arm": "A", "qualifies": "True"}],
    )

    pipeline.stage8_decomposition(stage1_dir, stage3_dir, tmp_path)
    stage8_dir = tmp_path / "stage8"
    with open(stage8_dir / "decomposition.csv") as f:
        rows = list(csv.DictReader(f))

    o3_rows = [r for r in rows if r["outcome"] == "o3_daily_peak_hvac_kw"]
    assert len(o3_rows) == 1, (
        f"Phase 3 should emit one o3 row; got {len(o3_rows)}"
    )
    r = o3_rows[0]
    assert r["category"] == "no_spike"
    assert r["unit"] == "kw", (
        "o3 unit must be kw (peak instantaneous power), not dollars"
    )
    assert float(r["arm_a_median_value"]) == pytest.approx(2.0, abs=1e-6), (
        "o3 must be the spiky hour's kWh (2.0), not the mean (0.275) "
        "or any other aggregate"
    )


def test_stage8_phase3_o3_multi_day_median_across_days(tmp_path):
    """Phase 3 audit gap-closer: o3's per-arm-category value is the
    MEDIAN of daily peak-hour kW values across the days in that
    (outcome, category) cell — NOT max across the category, NOT mean
    across days.

    Three included days in one qualifying week (Arm A only). Each day
    has em:2 constant at a different power so the per-day peak HVAC kW
    is a distinct round number:

      Day 1 peak hvac_kw = 1.0
      Day 2 peak hvac_kw = 3.0
      Day 3 peak hvac_kw = 9.0

      median = 3.0
      mean   = (1.0 + 3.0 + 9.0) / 3 = 4.333...
      max    = 9.0
      sum    = 13.0

    Catches: mean-instead-of-median, max-instead-of-median,
    sum-instead-of-median, first-day-only, last-day-only.
    """
    week_start = datetime.date(2026, 6, 8)
    days = [week_start + datetime.timedelta(days=i) for i in range(3)]
    peak_watts_by_day = {days[0]: 1000.0, days[1]: 3000.0, days[2]: 9000.0}

    refoss_frames = []
    prices_frames = []
    for day_ct, watts in peak_watts_by_day.items():
        day_start_utc = pipeline._ct_date_to_utc(day_ct, 0)
        day_end_utc = pipeline._ct_date_to_utc(
            day_ct + datetime.timedelta(days=1), 0,
        )
        refoss_times = pd.date_range(
            day_start_utc, day_end_utc, freq="30s", inclusive="left",
        )
        refoss_frames.append(pd.DataFrame({
            "_time": refoss_times,
            "_measurement": "refoss.channel",
            "_field": "power_w",
            "_value": watts,
            "channel": "em:2",
        }))
        prices_times = pd.date_range(
            day_start_utc, day_end_utc, freq="5min", inclusive="left",
        )
        prices_frames.append(pd.DataFrame({
            "_time": prices_times,
            "_measurement": "comed.prices",
            "_field": "price_cents_per_kwh",
            "_value": 5.0,
            "period_type": "5min",
        }))
    refoss_df = pd.concat(refoss_frames, ignore_index=True)
    prices_df = pd.concat(prices_frames, ignore_index=True)

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "refoss.channel": refoss_df,
            "comed.prices": prices_df,
            "nws.forecast": build_nws_forecast_df(days),
        },
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-15T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )

    stage2_dir = tmp_path / "stage2"
    stage2_dir.mkdir()
    _write_qualifying_days_csv(
        stage2_dir / "qualifying_days.csv",
        weeks=[{"week_start_ct": week_start, "arm": "A",
                "included_day_indexes": [0, 1, 2]}],
    )
    stage3_dir = tmp_path / "stage3"
    stage3_dir.mkdir()
    _write_stage3_weekly_csv(
        stage3_dir / "weekly.csv",
        rows=[{"week_start_ct": week_start.isoformat(),
               "arm": "A", "qualifies": "True"}],
    )

    pipeline.stage8_decomposition(stage1_dir, stage3_dir, tmp_path)
    stage8_dir = tmp_path / "stage8"
    with open(stage8_dir / "decomposition.csv") as f:
        rows = list(csv.DictReader(f))

    o3_rows = [
        r for r in rows
        if r["outcome"] == "o3_daily_peak_hvac_kw"
        and r["category"] == "no_spike"
    ]
    assert len(o3_rows) == 1
    r = o3_rows[0]
    assert int(r["arm_a_n_days"]) == 3
    assert r["unit"] == "kw"

    # Median of [1.0, 3.0, 9.0] = 3.0. Distinct from mean (4.333...),
    # max (9.0), sum (13.0), first (1.0), last (9.0).
    assert float(r["arm_a_median_value"]) == pytest.approx(3.0, abs=1e-6), (
        "o3 per-arm-category must be median of daily peaks; got "
        f"{r['arm_a_median_value']} for daily peaks [1.0, 3.0, 9.0]"
    )
