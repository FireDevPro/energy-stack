"""Outside-in end-to-end pipeline test.

Validates that Stages 2 through 6 chain correctly: each stage's output
file is read and consumed by the next stage's input. This catches the
class of bug that per-stage unit tests miss (data-shape mismatches
across stage boundaries, schema drift, CSV header expectations).

The test uses monkeypatched loaders to inject synthetic data at the
loader-stub boundary (`_load_week_inputs_from_stage1`,
`_load_stage3_inputs_for_week`, `_load_stage6_inputs`), but the
CSV/parquet I/O between stages goes through the real production code
paths. So it does NOT exercise the real-data parquet readers (those are
gated by OSF_FILING.md criterion 14), but it DOES exercise every
between-stage handoff via the locked CSV schemas.

Stages 1, 7, 8, 9 are excluded: Stage 1 needs Influx; 7/8/9 are not
yet implemented.
"""
from __future__ import annotations

import csv
import datetime
from pathlib import Path

import numpy as np
import pytest

from tools.analysis import pipeline


# -- Synthetic-fixture builders --------------------------------------------


def _arm_b_action(switch: datetime.datetime, arm: str) -> dict:
    """A Stage 2 rule-10 verification action — 3h after switch, control-relevant."""
    return {
        "timestamp": switch + datetime.timedelta(hours=3),
        "arm": arm,
        "action": "HOT_PRE_COOL",
        "dry_run": (arm == "A"),
    }


def _stage2_week_inputs(week_start_ct: datetime.date, arm: str) -> dict:
    """One week of Stage 2 inputs that passes every rule."""
    switch = datetime.datetime.combine(week_start_ct, datetime.time(5, 0))
    return {
        "week_start_ct": week_start_ct,
        "arm": arm,
        "weekly_hvac_kwh": 100.0,
        "refoss_intervals": [],
        "hourly_prices": [{"observed_prints": 12} for _ in range(168)],
        "daily_comfortnet_downtime_minutes": [0] * 7,
        "daily_ecowitt_both_missing_hours": [0] * 7,
        "scheduler_outages": [],
        "control_relevant_windows": [],
        "overrides": [],
        "missing_forecast_issuances": 0,
        "arm_transition": {
            "switch_ts": switch,
            "intended_arm": arm,
            "action_events": [_arm_b_action(switch, arm)],
        },
    }


def _stage3_week_inputs(week_start_ct: datetime.date, arm: str,
                        qualifies: bool, heat_offset: float = 0.0) -> dict:
    """One week of Stage 3 inputs. ``heat_offset`` shifts the weather
    vector so weeks differ along the matching axes (Stage 4 needs that).

    Arm B uses 20% less HVAC than Arm A to simulate the live-aware
    arm's price-overlay savings, so Stage 5's matched-pair median
    effect lands non-zero in the expected direction (B − A < 0).
    """
    base_temp = 85.0 + heat_offset
    # Arm A: 0.5 kWh/h baseline HVAC; Arm B: 0.4 kWh/h (20% less)
    hvac_hourly_kwh = 0.4 if arm == "B" else 0.5
    return {
        "week_start_ct": week_start_ct,
        "arm": arm,
        "qualifies": qualifies,
        "daily_avg_temps_f": [75.0 + heat_offset] * 7,
        "hourly_hvac_records": [
            {"hour_of_day_ct": h % 24, "hvac_kwh": hvac_hourly_kwh,
             "supply_c_per_kwh": 8.0}
            for h in range(168)
        ],
        "hourly_mains_records": [
            # Mains tracks HVAC + a fixed 1.0 kWh/h non-HVAC base load
            {"hour_of_day_ct": h % 24, "hvac_kwh": hvac_hourly_kwh + 1.0,
             "supply_c_per_kwh": 8.0}
            for h in range(168)
        ],
        "hourly_weather": [
            {"temp_f": base_temp, "dewpoint_f": 70.0 + heat_offset,
             "pressure_inhg": 29.92,
             "solar_wm2": 100.0, "wind_mph": 5.0}
            for _ in range(168)
        ],
    }


def _stage6_inputs() -> dict:
    """Stage 6 inputs: PJM 5CPs distributed across arms, simple loads."""
    from tools.o2_capacity_reconstruction.reconstruct import TariffConstants
    pjm_peaks_a = [datetime.datetime(2026, 7, 14 + i, 17) for i in range(2)]
    pjm_peaks_b = [datetime.datetime(2026, 8, 4 + i, 18) for i in range(3)]
    return {
        "pjm_peak_hours_by_arm": {"A": pjm_peaks_a, "B": pjm_peaks_b},
        "comed_peak_hours_by_arm": {"A": pjm_peaks_a, "B": pjm_peaks_b},
        "hourly_mains_kw": {p: 3.0 for p in pjm_peaks_a + pjm_peaks_b},
        "tariff_constants": TariffConstants(
            year=2026,
            comed_npl_mw=20736.0,
            a_comed_cpl_mw=19138.22,
            portfolio_sum_mw=1500.0,
            rate_dollars_per_kw_month=10.13567,
            is_placeholder=False,
        ),
        "summer_year": 2026,
        "comed_bills": [
            {"year": 2027, "month": 6, "capacity_charge_dollars": 22.50},
            {"year": 2027, "month": 7, "capacity_charge_dollars": 28.75},
        ],
        "summer_hours": pjm_peaks_a + pjm_peaks_b,
        "fivecp_state_by_hour": {p: "holding" for p in pjm_peaks_a + pjm_peaks_b},
    }


# -- The outside-in test ---------------------------------------------------


def test_pipeline_synthetic_end_to_end(monkeypatch, tmp_path):
    """Stages 2 → 3 → 4 → 5 → 6 chain on synthetic data.

    Each stage produces its locked output CSV; the next stage reads
    that CSV (or its own monkeypatched loader for stages whose real-data
    loader is stubbed). Verifies cross-stage data flow through the
    actual CSV I/O paths, not by passing dicts in memory.
    """
    # Four weeks: 2 Arm A + 2 Arm B, spread across summer 2026
    weeks_a = [datetime.date(2026, 6, 8), datetime.date(2026, 6, 22)]
    weeks_b = [datetime.date(2026, 6, 15), datetime.date(2026, 6, 29)]

    # Stage 2: build synthetic inputs, all passing every rule
    stage2_inputs = [
        _stage2_week_inputs(w, "A") for w in weeks_a
    ] + [
        _stage2_week_inputs(w, "B") for w in weeks_b
    ]
    monkeypatch.setattr(
        pipeline, "_load_week_inputs_from_stage1",
        lambda _: stage2_inputs,
    )

    # Stage 3: per-(week, arm) loader returns Stage 3 inputs with
    # varying weather to give Stage 4 something to match on. Stage 3
    # itself overwrites qualifies from Stage 2's CSV, so the value
    # passed here is intentionally bogus (True) to verify the override.
    stage3_lookup = {}
    for i, w in enumerate(weeks_a):
        stage3_lookup[(w, "A")] = _stage3_week_inputs(
            w, "A", qualifies=True, heat_offset=2.0 * i,
        )
    for i, w in enumerate(weeks_b):
        stage3_lookup[(w, "B")] = _stage3_week_inputs(
            w, "B", qualifies=True, heat_offset=2.0 * i,
        )

    def stage3_loader(stage1_dir, week_start_ct, arm):
        return stage3_lookup[(week_start_ct, arm)]
    monkeypatch.setattr(
        pipeline, "_load_stage3_inputs_for_week", stage3_loader,
    )

    # Stage 6: independent loader
    monkeypatch.setattr(
        pipeline, "_load_stage6_inputs", lambda _: _stage6_inputs(),
    )

    # Stage 4 needs a baseline covariance matrix on disk
    baseline_cov_path = tmp_path / "baseline_cov.npz"
    np.savez(
        baseline_cov_path,
        cov=np.eye(6, dtype=np.float64),
        mean=np.zeros(6, dtype=np.float64),
    )

    stage1_dir = tmp_path / "stage1"
    stage1_dir.mkdir()

    # --- Run the pipeline -------------------------------------------------
    pipeline.stage2_quality(stage1_dir, tmp_path)
    pipeline.stage3_weekly(stage1_dir, tmp_path, tmp_path)
    pipeline.stage4_matching(
        stage3_dir=tmp_path / "stage3",
        baseline_cov_path=baseline_cov_path,
        out_dir=tmp_path,
    )
    pipeline.stage5_effects(
        stage3_dir=tmp_path / "stage3",
        stage4_dir=tmp_path / "stage4",
        out_dir=tmp_path,
    )
    pipeline.stage6_o2(stage1_dir, tmp_path)

    # --- Assert each stage produced sensible cross-stage output ----------

    # Stage 2: 4 qualifying rows, all qualifying=True
    with open(tmp_path / "stage2" / "qualifying_weeks.csv") as f:
        s2_rows = list(csv.DictReader(f))
    assert len(s2_rows) == 4, "Stage 2 should emit one row per (week, arm)"
    assert all(r["qualifying"] == "True" for r in s2_rows), (
        f"All 4 synthetic weeks should pass every rule; got: "
        f"{[(r['week_start_ct'], r['qualifying'], r['exclusion_reason']) for r in s2_rows]}"
    )

    # Stage 3: 4 rows, qualifies copied verbatim from Stage 2's CSV
    with open(tmp_path / "stage3" / "weekly.csv") as f:
        s3_rows = list(csv.DictReader(f))
    assert len(s3_rows) == 4, "Stage 3 should emit one row per Stage 2 row"
    assert all(r["qualifies"] == "True" for r in s3_rows)
    # Sanity: outcome columns populated, not zero
    for r in s3_rows:
        assert float(r["weekly_cdd"]) > 0, (
            f"Stage 3 weekly_cdd should be >0 for the 75°F+ synthetic week; "
            f"got {r['weekly_cdd']}"
        )

    # Stage 4: should produce >=1 matched pair (2 A weeks × 2 B weeks)
    with open(tmp_path / "stage4" / "matched_pairs.csv") as f:
        s4_rows = list(csv.DictReader(f))
    assert len(s4_rows) >= 1, (
        f"Stage 4 should produce at least one (A, B) pair from 2A+2B; "
        f"got {len(s4_rows)} pairs"
    )
    # Every pair should reference an A week and a B week
    for p in s4_rows:
        assert p["week_a"] in {w.isoformat() for w in weeks_a}
        assert p["week_b"] in {w.isoformat() for w in weeks_b}

    # Stage 5: effects.csv has one row per outcome (o1, o3, o4) AND
    # the median diff is directionally correct (B uses 20% less HVAC
    # in the fixture, so o1/o3/o4 medians should all be negative).
    with open(tmp_path / "stage5" / "effects.csv") as f:
        s5_rows = {r["outcome"]: r for r in csv.DictReader(f)}
    assert set(s5_rows) == {
        "o1_dollars_per_cdd", "o3_peak_hvac_kw", "o4_dollars_per_cdd_whole_home",
    }
    # Effect size sanity: Arm B's HVAC is 20% lower → all three outcomes
    # should have negative B−A medians. If any is non-negative, either
    # the fixture or Stage 5's diff computation is wrong.
    for outcome, row in s5_rows.items():
        assert float(row["median_diff"]) < 0, (
            f"Stage 5 median_diff for {outcome} should be < 0 "
            f"(Arm B uses less HVAC in the fixture); got {row['median_diff']}"
        )

    # Stage 6: all four output CSVs populated
    stage6 = tmp_path / "stage6"
    with open(stage6 / "o2_layer1.csv") as f:
        layer1 = list(csv.DictReader(f))
    assert len(layer1) == 1
    assert float(layer1[0]["a_cust_cpl_kw_arm_a"]) == 3.0
    assert float(layer1[0]["a_cust_cpl_kw_arm_b"]) == 3.0

    with open(stage6 / "o2_layer2.csv") as f:
        layer2 = list(csv.DictReader(f))
    assert {r["scenario"] for r in layer2} == {"low", "anchor_2021", "high"}

    with open(stage6 / "o2_layer3.csv") as f:
        layer3 = list(csv.DictReader(f))
    assert len(layer3) == 1
    assert float(layer3[0]["total_capacity_charge_dollars"]) == pytest.approx(51.25)

    with open(stage6 / "detector_accuracy.csv") as f:
        detector = list(csv.DictReader(f))
    assert len(detector) == 1
    assert int(detector[0]["tp"]) == 5  # all 5 peaks are "holding" in fixture


def test_pipeline_synthetic_end_to_end_stage3_respects_stage2_exclusion(
    monkeypatch, tmp_path,
):
    """If Stage 2 excludes a week, Stage 3 must echo qualifies=False
    even when Stage 3's loader provides a complete fixture for that
    week. This is the integration version of the per-stage boundary
    test in test_pipeline.py.
    """
    week_a = datetime.date(2026, 6, 8)
    week_b = datetime.date(2026, 6, 15)

    # Week A's Stage 2 inputs are fine; week B's blow rule 1 (15% imputation)
    a_inputs = _stage2_week_inputs(week_a, "A")
    b_inputs = _stage2_week_inputs(week_b, "B")
    b_inputs["refoss_intervals"] = [
        {"tier": 1, "imputed_kwh": 15.0,
         "start_ts": datetime.datetime(2026, 6, 16, 10, 0),
         "end_ts": datetime.datetime(2026, 6, 16, 10, 4)},
    ]
    monkeypatch.setattr(
        pipeline, "_load_week_inputs_from_stage1",
        lambda _: [a_inputs, b_inputs],
    )

    # Stage 3's loader returns full data for BOTH weeks, ignoring Stage 2's
    # exclusion. The pipeline must still echo qualifies=False for week B.
    stage3_lookup = {
        (week_a, "A"): _stage3_week_inputs(week_a, "A", qualifies=True),
        (week_b, "B"): _stage3_week_inputs(week_b, "B", qualifies=True),
    }
    monkeypatch.setattr(
        pipeline, "_load_stage3_inputs_for_week",
        lambda _stage1, w, arm: stage3_lookup[(w, arm)],
    )

    stage1_dir = tmp_path / "stage1"
    stage1_dir.mkdir()
    pipeline.stage2_quality(stage1_dir, tmp_path)
    pipeline.stage3_weekly(stage1_dir, tmp_path, tmp_path)

    # Stage 2: A qualifies, B does not
    with open(tmp_path / "stage2" / "qualifying_weeks.csv") as f:
        s2 = {r["arm"]: r for r in csv.DictReader(f)}
    assert s2["A"]["qualifying"] == "True"
    assert s2["B"]["qualifying"] == "False"
    assert s2["B"]["exclusion_reason"] == "refoss_imputation_too_high"

    # Stage 3: copies qualifies verbatim, even though its loader said True
    with open(tmp_path / "stage3" / "weekly.csv") as f:
        s3 = {r["arm"]: r for r in csv.DictReader(f)}
    assert s3["A"]["qualifies"] == "True"
    assert s3["B"]["qualifies"] == "False"
