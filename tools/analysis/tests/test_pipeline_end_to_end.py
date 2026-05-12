"""Outside-in end-to-end pipeline test.

Expresses the full Stage 1-9 pipeline intent from `ANALYSIS_PIPELINE.md`,
not just the current implementation state. Stages 1-6 are implemented
and their assertions are expected to pass. Stages 7-9 are stubs; their
assertions are marked `xfail(strict=True)` so that:
  - CI is green today (xfail counts as expected)
  - When a stage's implementation lands, its test XPASSes
  - Strict mode means XPASS fails the test, forcing the author to
    remove the xfail marker as part of the implementing PR

This is the outside-in TDD discipline made visible: the test for each
stage exists before the stage's code does, and the pytest output shows
which stages have been built (PASSED) vs which are still owed (XFAIL).

The test uses monkeypatched loaders to inject synthetic data at the
loader-stub boundary (`_load_week_inputs_from_stage1`,
`_load_stage3_inputs_for_week`, `_load_stage6_inputs`), but the
CSV/parquet I/O between stages goes through the real production code
paths. Real-data parquet validation is gated separately by
OSF_FILING.md criterion 14 (2025 replay export).

Stage 1 is excluded: it needs a live Influx connection. The synthetic
fixture starts at Stage 2.
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
    return {
        "timestamp": switch + datetime.timedelta(hours=3),
        "arm": arm,
        "action": "HOT_PRE_COOL",
        "dry_run": (arm == "A"),
    }


def _stage2_week_inputs(week_start_ct: datetime.date, arm: str) -> dict:
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
    """Arm B uses 20% less HVAC than Arm A so Stage 5's matched-pair
    median effect lands non-zero in the expected direction (B − A < 0).
    """
    base_temp = 85.0 + heat_offset
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


# -- Pipeline-run fixture: runs all 9 stages, returns the output dir -------


@pytest.fixture
def full_pipeline_run(monkeypatch, tmp_path):
    """Run Stages 2-9 against synthetic fixtures and return tmp_path.

    Stages 7-9 are currently stubs and produce empty CSVs; tests that
    assert against them are xfailed until implementation lands.
    """
    weeks_a = [datetime.date(2026, 6, 8), datetime.date(2026, 6, 22)]
    weeks_b = [datetime.date(2026, 6, 15), datetime.date(2026, 6, 29)]

    stage2_inputs = [
        _stage2_week_inputs(w, "A") for w in weeks_a
    ] + [
        _stage2_week_inputs(w, "B") for w in weeks_b
    ]
    monkeypatch.setattr(
        pipeline, "_load_week_inputs_from_stage1",
        lambda _: stage2_inputs,
    )

    stage3_lookup = {}
    for i, w in enumerate(weeks_a):
        stage3_lookup[(w, "A")] = _stage3_week_inputs(
            w, "A", qualifies=True, heat_offset=2.0 * i,
        )
    for i, w in enumerate(weeks_b):
        stage3_lookup[(w, "B")] = _stage3_week_inputs(
            w, "B", qualifies=True, heat_offset=2.0 * i,
        )
    monkeypatch.setattr(
        pipeline, "_load_stage3_inputs_for_week",
        lambda stage1_dir, w, arm: stage3_lookup[(w, arm)],
    )

    monkeypatch.setattr(
        pipeline, "_load_stage6_inputs", lambda _: _stage6_inputs(),
    )

    baseline_cov_path = tmp_path / "baseline_cov.npz"
    np.savez(
        baseline_cov_path,
        cov=np.eye(6, dtype=np.float64),
        mean=np.zeros(6, dtype=np.float64),
    )

    stage1_dir = tmp_path / "stage1"
    stage1_dir.mkdir()

    # Run all 9 stages. Stages 7-9 are stubs that touch empty CSVs.
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
    pipeline.stage7_sced(
        stage5_dir=tmp_path / "stage5", out_dir=tmp_path,
    )
    pipeline.stage8_decomposition(
        stage1_dir=stage1_dir,
        stage3_dir=tmp_path / "stage3",
        out_dir=tmp_path,
    )
    pipeline.stage9_sensitivity(
        stage1_dir=stage1_dir,
        stage2_dir=tmp_path / "stage2",
        stage3_dir=tmp_path / "stage3",
        out_dir=tmp_path,
    )

    return {"out_dir": tmp_path, "weeks_a": weeks_a, "weeks_b": weeks_b}


# -- Per-stage outside-in assertions ---------------------------------------


def test_e2e_through_stage_2(full_pipeline_run):
    """Stage 2 emits qualifying_weeks.csv with one row per (week, arm)."""
    with open(full_pipeline_run["out_dir"] / "stage2" / "qualifying_weeks.csv") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 4
    assert all(r["qualifying"] == "True" for r in rows)


def test_e2e_through_stage_3(full_pipeline_run):
    """Stage 3 reads Stage 2's CSV and emits weekly.csv. The qualifies
    bool is copied verbatim from Stage 2 (boundary rule preserved across
    serialization). Outcome columns are populated with non-zero values
    for the warm fixture.
    """
    with open(full_pipeline_run["out_dir"] / "stage3" / "weekly.csv") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 4
    assert all(r["qualifies"] == "True" for r in rows)
    for r in rows:
        assert float(r["weekly_cdd"]) > 0
        assert float(r["o3_peak_hvac_kw"]) > 0


def test_e2e_through_stage_4(full_pipeline_run):
    """Stage 4 reads Stage 3's weekly.csv and produces matched_pairs.csv.
    With 2 Arm A + 2 Arm B qualifying weeks, at least one (A, B) pair
    must be formed.
    """
    out_dir = full_pipeline_run["out_dir"]
    weeks_a = full_pipeline_run["weeks_a"]
    weeks_b = full_pipeline_run["weeks_b"]
    with open(out_dir / "stage4" / "matched_pairs.csv") as f:
        pairs = list(csv.DictReader(f))
    assert len(pairs) >= 1
    a_set = {w.isoformat() for w in weeks_a}
    b_set = {w.isoformat() for w in weeks_b}
    for p in pairs:
        assert p["week_a"] in a_set
        assert p["week_b"] in b_set


def test_e2e_through_stage_5(full_pipeline_run):
    """Stage 5 reads Stage 3 + Stage 4 and emits effects.csv. With Arm B
    using 20% less HVAC than Arm A in the fixture, all three outcome
    medians must be negative (B − A < 0).
    """
    with open(full_pipeline_run["out_dir"] / "stage5" / "effects.csv") as f:
        rows = {r["outcome"]: r for r in csv.DictReader(f)}
    assert set(rows) == {
        "o1_dollars_per_cdd", "o3_peak_hvac_kw", "o4_dollars_per_cdd_whole_home",
    }
    for outcome, row in rows.items():
        assert float(row["median_diff"]) < 0, (
            f"{outcome} median_diff should be < 0 (Arm B uses less HVAC); "
            f"got {row['median_diff']}"
        )


def test_e2e_through_stage_6(full_pipeline_run):
    """Stage 6 emits four O2 / detector-accuracy CSVs from Stage 1
    (mocked via _load_stage6_inputs in this test)."""
    stage6 = full_pipeline_run["out_dir"] / "stage6"
    with open(stage6 / "o2_layer1.csv") as f:
        l1 = list(csv.DictReader(f))
    assert len(l1) == 1
    assert float(l1[0]["a_cust_cpl_kw_arm_a"]) == 3.0

    with open(stage6 / "o2_layer2.csv") as f:
        l2 = list(csv.DictReader(f))
    assert {r["scenario"] for r in l2} == {"low", "anchor_2021", "high"}

    with open(stage6 / "o2_layer3.csv") as f:
        l3 = list(csv.DictReader(f))
    assert float(l3[0]["total_capacity_charge_dollars"]) == pytest.approx(51.25)

    with open(stage6 / "detector_accuracy.csv") as f:
        det = list(csv.DictReader(f))
    assert int(det[0]["tp"]) == 5


@pytest.mark.xfail(
    strict=True,
    reason="Stage 7 (SCED randomization) not yet implemented; "
           "sced_pvalues.csv is an empty stub. Remove this xfail when Stage 7 lands.",
)
def test_e2e_through_stage_7(full_pipeline_run):
    """Stage 7 reads Stage 5's effects.csv (or recomputes pair differences
    from Stage 3 + Stage 4) and emits sced_pvalues.csv with one row per
    outcome (o1, o3, o4). Each row has a two-sided p-value in [0, 1]
    and an `exact` flag indicating exhaustive vs random sampling.
    """
    with open(full_pipeline_run["out_dir"] / "stage7" / "sced_pvalues.csv") as f:
        rows = list(csv.DictReader(f))
    outcomes = {r["outcome"] for r in rows}
    assert outcomes == {
        "o1_dollars_per_cdd", "o3_peak_hvac_kw", "o4_dollars_per_cdd_whole_home",
    }
    for r in rows:
        p = float(r["pvalue"])
        assert 0.0 <= p <= 1.0


@pytest.mark.xfail(
    strict=True,
    reason="Stage 8 (forecast-vs-grid decomposition) not yet implemented; "
           "decomposition.csv + layer_attribution.csv are empty stubs. "
           "Remove this xfail when Stage 8 lands.",
)
def test_e2e_through_stage_8(full_pipeline_run):
    """Stage 8 emits decomposition.csv (per-outcome magnitude attribution
    across forecast_correlated_spike / grid_event_spike / no_spike
    categories) and layer_attribution.csv (per-grid-event-day which Arm B
    layer triggered).
    """
    stage8 = full_pipeline_run["out_dir"] / "stage8"
    with open(stage8 / "decomposition.csv") as f:
        decomp = list(csv.DictReader(f))
    assert len(decomp) >= 1, "Stage 8 should emit at least one decomposition row"
    with open(stage8 / "layer_attribution.csv") as f:
        attrib = list(csv.DictReader(f))
    # Layer attribution may be empty if there are no grid-event days in the
    # fixture; the FILE must exist with the locked header.
    assert attrib is not None


@pytest.mark.xfail(
    strict=True,
    reason="Stage 9 (six pre-committed sensitivities) not yet implemented; "
           "all six sensitivity CSVs are empty stubs. Remove this xfail "
           "when Stage 9 lands.",
)
def test_e2e_through_stage_9(full_pipeline_run):
    """Stage 9 emits six per-sensitivity CSVs (one per pre-committed
    sensitivity in EXPERIMENT_DESIGN.md §7). Each should contain at
    least one row.
    """
    stage9 = full_pipeline_run["out_dir"] / "stage9"
    sensitivities = (
        "euclidean_zscore", "include_washout", "em2_em8_only",
        "five_min_pricing", "day_of_week", "threshold_robustness",
    )
    for sid in sensitivities:
        with open(stage9 / f"{sid}.csv") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) >= 1, f"Stage 9 sensitivity '{sid}' should emit at least one row"


# -- Orthogonal property test: Stage 3 boundary rule -----------------------


def test_pipeline_synthetic_end_to_end_stage3_respects_stage2_exclusion(
    monkeypatch, tmp_path,
):
    """If Stage 2 excludes a week, Stage 3 must echo qualifies=False even
    when Stage 3's loader provides a complete fixture for that week. This
    is the integration version of the boundary rule check in test_pipeline.py.
    """
    week_a = datetime.date(2026, 6, 8)
    week_b = datetime.date(2026, 6, 15)

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

    with open(tmp_path / "stage2" / "qualifying_weeks.csv") as f:
        s2 = {r["arm"]: r for r in csv.DictReader(f)}
    assert s2["A"]["qualifying"] == "True"
    assert s2["B"]["qualifying"] == "False"
    assert s2["B"]["exclusion_reason"] == "refoss_imputation_too_high"

    with open(tmp_path / "stage3" / "weekly.csv") as f:
        s3 = {r["arm"]: r for r in csv.DictReader(f)}
    assert s3["A"]["qualifies"] == "True"
    assert s3["B"]["qualifies"] == "False"
