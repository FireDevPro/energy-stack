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
from tools.analysis.tests.fixture_real_shape import write_bundle


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

    # Phase 1 contract: exactly ONE row.
    # - 1 qualifying day in 1 qualifying week, Arm A only -> 1 outcome
    #   (o1) populated, 1 category (no_spike), quiet-zero on Arm B.
    # - o3 and o4 absent from `outcomes` dict -> no rows emitted for
    #   them (Phase 1 doesn't fake-populate them with zeros).
    assert len(rows) == 1, (
        f"Phase 1 should emit exactly 1 row "
        f"(o1, no_spike); got {len(rows)}: {rows}"
    )

    r = rows[0]
    assert r["outcome"] == "o1_daily_hvac_dollars"
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

    # Arm B has zero days -> quiet-zero guard fires.
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
    assert len(guard_entries) == 1
    assert "o1_daily_hvac_dollars" in (guard_entries[0].get("note") or "")
    assert "no_spike" in (guard_entries[0].get("note") or "")

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

    # One row: (o1, no_spike) with quiet-zero on Arm B.
    assert len(rows) == 1
    r = rows[0]
    assert r["outcome"] == "o1_daily_hvac_dollars"
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

    assert len(rows) == 1
    r = rows[0]
    assert r["outcome"] == "o1_daily_hvac_dollars"
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
