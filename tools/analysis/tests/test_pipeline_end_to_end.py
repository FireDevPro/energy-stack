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


def _stage8_inputs(weeks_a, weeks_b) -> dict:
    """Synthetic Stage 8 inputs: 4 weeks × 7 days = 28 days mixed across
    the three spike categories. Arm B's per-day values are 20% lower
    than Arm A's (matching the Stage 3 fixture), so the per-category
    decomposition shows B − A < 0 medians.
    """
    daily_records = []
    for arm_weeks, arm in [(weeks_a, "A"), (weeks_b, "B")]:
        for week in arm_weeks:
            for d in range(7):
                date = week + datetime.timedelta(days=d)
                # Distribute categories: 4 forecast / 2 grid_event / 1 no_spike per week
                if d < 4:
                    category = "forecast_correlated_spike"
                elif d < 6:
                    category = "grid_event_spike"
                else:
                    category = "no_spike"
                base_value = {
                    "forecast_correlated_spike": 3.50,
                    "grid_event_spike": 2.20,
                    "no_spike": 1.10,
                }[category]
                multiplier = 0.8 if arm == "B" else 1.0
                daily_records.append({
                    "date": date,
                    "arm": arm,
                    "category": category,
                    "outcomes": {
                        "o1_daily_hvac_dollars": base_value * multiplier,
                        "o3_daily_peak_hvac_kw": (base_value / 2) * multiplier,
                        "o4_daily_mains_dollars": (base_value * 1.4) * multiplier,
                    },
                })

    # Layer attribution: a few grid-event days in Arm B with synthetic responses
    layer_attribution = []
    for week in weeks_b:
        for d in [4, 5]:  # Fri/Sat grid-event days
            layer_attribution.append({
                "date": week + datetime.timedelta(days=d),
                "hour_ct": 17,
                "arm": "B",
                "layer_triggered": "price_spike_reactivity",
                "indoor_temp_f": 78.5,
                "action": "PRICE_OVERLAY_TIER2",
            })

    return {
        "decomposition": {"data": daily_records},
        "layer_attribution": {"data": layer_attribution},
    }


def _effects_like_row(outcome: str, median: float) -> dict:
    return {
        "outcome": outcome,
        "n_pairs": 2,
        "median_diff": f"{median:.6f}",
        "ci_low_95": f"{(median - 0.05):.6f}",
        "ci_high_95": f"{(median + 0.05):.6f}",
    }


def _stage9_inputs() -> dict:
    """Synthetic Stage 9 inputs: each sensitivity returns a small set
    of result rows mirroring what the real upstream-re-run would
    produce. All sensitivities reflect the fixture's B−A < 0 direction.
    """
    effects_outcomes = (
        "o1_dollars_per_cdd", "o3_peak_hvac_kw", "o4_dollars_per_cdd_whole_home",
    )
    return {
        "euclidean_zscore": [
            _effects_like_row(o, -0.10) for o in effects_outcomes
        ],
        "include_washout": [
            _effects_like_row(o, -0.08) for o in effects_outcomes
        ],
        "em2_em8_only": [
            _effects_like_row("o1_dollars_per_cdd", -0.09),
        ],
        "five_min_pricing": [
            _effects_like_row("o1_dollars_per_cdd", -0.11),
        ],
        "day_of_week": [
            {"outcome": "o1_dollars_per_cdd",
             "day_of_week": dow, "arm": arm,
             "n": 4, "mean_value": f"{(0.5 if arm == 'A' else 0.4):.6f}"}
            for dow in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
            for arm in ("A", "B")
        ],
        "threshold_robustness": [
            {"threshold_pair": tp, "outcome": "o1_dollars_per_cdd",
             "category": cat, "delta_median": f"{-0.10:.6f}"}
            for tp in ("8/15", "10/20", "12/25")
            for cat in ("forecast_correlated_spike",
                        "grid_event_spike", "no_spike")
        ],
    }


def _stage6_inputs() -> dict:
    """Per-output Stage 6 inputs shape (post-Phase-1).

    Phase 1 scope: only Layer 1 is wired. Other outputs return None
    and the orchestrator emits header-only for them. As later phases
    land, this fixture grows.
    """
    from tools.o2_capacity_reconstruction.reconstruct import TariffConstants
    pjm_peaks_a = [datetime.datetime(2026, 7, 14 + i, 17) for i in range(2)]
    pjm_peaks_b = [datetime.datetime(2026, 8, 4 + i, 18) for i in range(3)]
    all_peaks = pjm_peaks_a + pjm_peaks_b
    tariff = TariffConstants(
        year=2026,
        comed_npl_mw=20736.0,
        a_comed_cpl_mw=19138.22,
        portfolio_sum_mw=1500.0,
        rate_dollars_per_kw_month=10.13567,
        is_placeholder=False,
    )
    return {
        "layer1": {
            "data": {
                "pjm_peak_hours_by_arm": {"A": pjm_peaks_a, "B": pjm_peaks_b},
                "hourly_mains_kw": {p: 3.0 for p in all_peaks},
                "capacity_rate_dollars_per_kw_month":
                    tariff.rate_dollars_per_kw_month,
                "summer_year": 2026,
                "tariff_constants": tariff,
            },
        },
        "layer2": None,
        "layer3": None,
        "detector": None,
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
        pipeline, "_load_stage6_inputs", lambda _, **kw: _stage6_inputs(),
    )

    monkeypatch.setattr(
        pipeline, "_load_stage8_inputs",
        lambda stage1, stage3: _stage8_inputs(weeks_a, weeks_b),
    )

    monkeypatch.setattr(
        pipeline, "_load_stage9_inputs",
        lambda stage1, stage2, stage3: _stage9_inputs(),
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
    """Phase 1 of Stage 6 loader: Layer 1 is the only output wired
    through the per-output loader shape. Layers 2/3/detector remain
    header-only until later phases land. The earlier perfect-detector
    expectations move into Phase 5's acceptance tests."""
    stage6 = full_pipeline_run["out_dir"] / "stage6"
    with open(stage6 / "o2_layer1.csv") as f:
        l1 = list(csv.DictReader(f))
    assert len(l1) == 1
    assert float(l1[0]["a_cust_cpl_kw_arm_a"]) == 3.0

    for name in ("o2_layer2.csv", "o2_layer3.csv", "detector_accuracy.csv"):
        with open(stage6 / name) as f:
            assert list(csv.DictReader(f)) == []


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


def test_e2e_through_stage_8(full_pipeline_run):
    """Stage 8 emits decomposition.csv (per-outcome × per-category B−A
    median day cost) and layer_attribution.csv (per-grid-event-day
    which Arm B layer triggered).
    """
    stage8 = full_pipeline_run["out_dir"] / "stage8"
    with open(stage8 / "decomposition.csv") as f:
        decomp = list(csv.DictReader(f))
    # 3 outcomes × 3 categories = 9 rows in the fixture
    assert len(decomp) == 9, (
        f"Stage 8 should emit one row per (outcome × category); got {len(decomp)}"
    )
    # Each row should have arm B with lower value than arm A in the fixture
    # (Arm B uses 20% less HVAC), so delta_median_value is negative everywhere.
    for r in decomp:
        assert float(r["delta_median_value"]) < 0, (
            f"{r['outcome']}/{r['category']} delta_median_value should be < 0 "
            f"(Arm B uses less in fixture); got {r['delta_median_value']}"
        )
    # Layer attribution: 2 grid-event days × 2 Arm B weeks = 4 rows
    with open(stage8 / "layer_attribution.csv") as f:
        attrib = list(csv.DictReader(f))
    assert len(attrib) == 4
    assert all(r["arm"] == "B" for r in attrib)
    assert all(r["layer_triggered"] == "price_spike_reactivity" for r in attrib)


def test_e2e_through_stage_9(full_pipeline_run):
    """Stage 9 emits six per-sensitivity CSVs (one per pre-committed
    sensitivity in EXPERIMENT_DESIGN.md §7). Each must contain at
    least one row, and the rows should reflect the fixture's B−A < 0
    direction where applicable.
    """
    stage9 = full_pipeline_run["out_dir"] / "stage9"

    # Sensitivities 1-4: effects-like outputs with negative B−A medians
    for sid in ("euclidean_zscore", "include_washout",
                "em2_em8_only", "five_min_pricing"):
        with open(stage9 / f"{sid}.csv") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) >= 1, f"sensitivity '{sid}' should emit ≥1 row"
        for r in rows:
            assert float(r["median_diff"]) < 0, (
                f"sensitivity '{sid}' / {r['outcome']} median_diff should "
                f"be < 0 (fixture direction); got {r['median_diff']}"
            )

    # Day-of-week: 7 days × 2 arms × 1 outcome = 14 rows
    with open(stage9 / "day_of_week.csv") as f:
        dow_rows = list(csv.DictReader(f))
    assert len(dow_rows) == 14
    arms = {r["arm"] for r in dow_rows}
    assert arms == {"A", "B"}

    # Threshold robustness: 3 thresholds × 3 categories × 1 outcome = 9 rows
    with open(stage9 / "threshold_robustness.csv") as f:
        tr_rows = list(csv.DictReader(f))
    assert len(tr_rows) == 9
    pairs = {r["threshold_pair"] for r in tr_rows}
    assert pairs == {"8/15", "10/20", "12/25"}


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
