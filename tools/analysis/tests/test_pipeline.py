"""Synthetic-data tests for the math primitives + skeleton orchestration
in tools.analysis.pipeline.

These tests prove the inferential cores (Mahalanobis distance,
Hungarian matching, stationary bootstrap, sign-flip randomization,
heat index, enthalpy, ComfortNet imputation, rule helpers) work as
documented in EXPERIMENT_DESIGN.md. They do NOT need an InfluxDB
connection or any external data.

End-to-end Stage 1 (Influx extract) is excluded — it requires live
Influx — and tested via a separate replay-data integration script.
"""
from __future__ import annotations

import csv
import datetime
import math
from pathlib import Path

import numpy as np
import pytest

from tools.analysis import pipeline


# -- Math primitives --------------------------------------------------------


def test_mahalanobis_identity_reduces_to_euclidean():
    sigma_inv = np.eye(3)
    x = np.array([0.0, 0.0, 0.0])
    y = np.array([3.0, 4.0, 0.0])
    assert pipeline.mahalanobis_distance(x, y, sigma_inv) == pytest.approx(5.0)


def test_mahalanobis_with_scale_matches_expected():
    # If the covariance scales the first axis by 4, distance along
    # that axis should be halved.
    sigma = np.diag([4.0, 1.0])
    sigma_inv = np.linalg.inv(sigma)
    x = np.array([0.0, 0.0])
    y = np.array([2.0, 0.0])
    # d² = (y-x)^T Σ⁻¹ (y-x) = 4 * 0.25 = 1, so d = 1
    assert pipeline.mahalanobis_distance(x, y, sigma_inv) == pytest.approx(1.0)


def test_hungarian_perfect_match():
    sigma_inv = np.eye(2)
    a = np.array([[0.0, 0.0], [10.0, 10.0]])
    b = np.array([[10.1, 10.1], [0.1, 0.1]])
    pairs, ua, ub = pipeline.hungarian_match(a, b, sigma_inv)
    # Optimal pairing flips B order to match A
    assert sorted([(i, j) for i, j, _ in pairs]) == [(0, 1), (1, 0)]
    assert ua == [] and ub == []
    # Distances should be small (~0.14)
    for _, _, d in pairs:
        assert d < 0.5


def test_hungarian_asymmetric_marks_extras_unmatched():
    sigma_inv = np.eye(1)
    a = np.array([[0.0], [5.0], [10.0]])
    b = np.array([[0.1], [10.1]])
    pairs, ua, ub = pipeline.hungarian_match(a, b, sigma_inv)
    assert len(pairs) == 2
    assert sorted(ua) == [1]  # the 5.0 in A had no B partner
    assert ub == []


def test_stationary_bootstrap_central_tendency():
    diffs = list(np.linspace(-0.5, 0.5, 9))  # median = 0.0
    res = pipeline.stationary_bootstrap_median_diff(
        diffs, n_resamples=500, rng_seed=42,
    )
    assert res["n"] == 9
    assert abs(res["point"]) < 1e-9
    assert res["ci_low"] < 0 < res["ci_high"]


def test_stationary_bootstrap_handles_empty():
    res = pipeline.stationary_bootstrap_median_diff([], n_resamples=10)
    assert math.isnan(res["point"])
    assert math.isnan(res["ci_low"])
    assert res["n"] == 0


def test_sced_pvalue_exact_for_small_n():
    # 4 pairs all positive: median = +1.
    # With 4 elements, median = avg of middle two sorted values.
    # Sign patterns with |median| >= 1: those with >=3 same-sign elements.
    # That's C(4,4)+C(4,3) on each side = (1+4)+(1+4) = 10 of 16.
    diffs = [1.0, 1.0, 1.0, 1.0]
    res = pipeline.sced_randomization_pvalue(diffs)
    assert res["exact"]
    assert res["pvalue"] == pytest.approx(10 / 16)


def test_sced_pvalue_zero_observed_returns_one():
    diffs = [-1.0, 1.0, -1.0, 1.0]  # median 0
    res = pipeline.sced_randomization_pvalue(diffs)
    assert res["pvalue"] == pytest.approx(1.0)


def test_heat_index_below_80_returns_temp_unchanged():
    assert pipeline.heat_index_f(70.0, 50.0) == pytest.approx(70.0)


def test_heat_index_high_humidity_amplifies():
    hi = pipeline.heat_index_f(90.0, 80.0)
    # NWS-published reference: 90F + 80% RH ≈ 113F
    assert hi == pytest.approx(113.4, abs=1.0)


def test_enthalpy_summer_typical():
    # 85F dry-bulb, 65F dewpoint at standard pressure: ~34-35 BTU/lb
    # per ASHRAE psychrometric chart cross-reference.
    h = pipeline.enthalpy_btu_per_lb(85.0, 65.0)
    assert 32.0 < h < 37.0


def test_enthalpy_monotonic_in_dewpoint():
    # At fixed dry-bulb, higher dewpoint => higher enthalpy.
    low = pipeline.enthalpy_btu_per_lb(85.0, 55.0)
    high = pipeline.enthalpy_btu_per_lb(85.0, 75.0)
    assert high > low + 5.0  # ~10 BTU/lb gap expected


def test_comfortnet_kw_full_cool():
    # 100% cool, 0% heat, 1500 cfm blower
    kw = pipeline.comfortnet_kw(100.0, 0.0, 1500.0)
    # 4.6 (cool) + 0 + 1500*(0.6/4500)=0.2 = 4.8
    assert kw == pytest.approx(4.8, abs=0.01)


def test_comfortnet_kw_zero_when_idle():
    assert pipeline.comfortnet_kw(0.0, 0.0, 0.0) == 0.0


def test_rule1_refoss_tier_assignments():
    intervals = [
        {"gap_minutes": 3, "comfortnet_available": False},
        {"gap_minutes": 20, "comfortnet_available": False},
        {"gap_minutes": 90, "comfortnet_available": True},
        {"gap_minutes": 90, "comfortnet_available": False},
        {"gap_minutes": 240, "comfortnet_available": True},
    ]
    out = pipeline.rule1_refoss(intervals)
    assert [r["tier"] for r in out] == [1, 2, 3, 4, 4]


def test_rule9_reclassify_long_vacation_setpoint():
    # Tagged operational but obviously vacation: 24h at 82F
    assert pipeline.rule9_classify_override("operational", 24.0, 82.0) == "vacation"


def test_rule9_keeps_short_operational_alone():
    assert pipeline.rule9_classify_override("operational", 3.0, 76.0) == "operational"


def test_rule9_respects_explicit_vacation_tag():
    assert pipeline.rule9_classify_override("vacation", 2.0, 82.0) == "vacation"


def test_rule10_earliest_of_first_window_or_6h():
    sw = datetime.datetime(2026, 6, 15, 0, 0)
    # First window 3h after switch -> 3h wins
    earlier = sw + datetime.timedelta(hours=3)
    assert pipeline.rule10_arm_transition_deadline(sw, earlier) == earlier
    # First window 10h after switch -> 6h cap wins
    later = sw + datetime.timedelta(hours=10)
    six_h = sw + datetime.timedelta(hours=6)
    assert pipeline.rule10_arm_transition_deadline(sw, later) == six_h
    # No first window known -> 6h cap
    assert pipeline.rule10_arm_transition_deadline(sw, None) == six_h


# -- Stage skeletons --------------------------------------------------------


def test_stage2_emits_locked_csv_schema(tmp_path):
    pipeline.stage2_quality(stage1_dir=tmp_path, out_dir=tmp_path)
    qual = tmp_path / "stage2" / "qualifying_weeks.csv"
    assert qual.exists()
    with open(qual) as f:
        header = next(csv.reader(f))
    assert header == [
        "week_start_ct", "arm", "qualifying", "exclusion_reason",
        "imputed_hvac_kwh_pct", "imputed_price_hours_pct",
        "override_operational_count", "override_vacation_days",
    ]


def test_stage3_emits_weather_summary_components(tmp_path):
    pipeline.stage3_weekly(stage1_dir=tmp_path, stage2_dir=tmp_path, out_dir=tmp_path)
    with open(tmp_path / "stage3" / "weekly.csv") as f:
        header = next(csv.reader(f))
    # Last 6 columns must match the locked weather summary vector.
    assert tuple(header[-6:]) == pipeline.WEATHER_VECTOR_COMPONENTS


def _write_minimal_weekly(weekly_path: Path, rows: list[dict]) -> None:
    fields = (
        "week_start_ct", "arm", "qualifies",
        "o1_dollars_per_cdd", "o3_peak_hvac_kw",
        "o4_dollars_per_cdd_whole_home",
        *pipeline.WEATHER_VECTOR_COMPONENTS,
    )
    with open(weekly_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def test_stage4_full_match_with_placeholder_baseline(tmp_path):
    # Two A weeks + two B weeks; pair the closest weather profiles.
    pipeline.stage3_weekly(tmp_path, tmp_path, tmp_path)  # sets up dir
    weekly = tmp_path / "stage3" / "weekly.csv"
    _write_minimal_weekly(
        weekly,
        [
            {"week_start_ct": "2026-06-08", "arm": "A", "qualifies": "true",
             "o1_dollars_per_cdd": "0.50", "o3_peak_hvac_kw": "3.5",
             "o4_dollars_per_cdd_whole_home": "0.65",
             "weekly_cdd": "50", "mean_enthalpy_btu_lb": "30",
             "total_solar_wh_m2": "100000", "mean_wind_mph": "6",
             "max_temp_f": "88", "max_dewpoint_f": "70"},
            {"week_start_ct": "2026-07-13", "arm": "A", "qualifies": "true",
             "o1_dollars_per_cdd": "0.55", "o3_peak_hvac_kw": "4.0",
             "o4_dollars_per_cdd_whole_home": "0.70",
             "weekly_cdd": "100", "mean_enthalpy_btu_lb": "40",
             "total_solar_wh_m2": "120000", "mean_wind_mph": "5",
             "max_temp_f": "94", "max_dewpoint_f": "75"},
            {"week_start_ct": "2026-06-22", "arm": "B", "qualifies": "true",
             "o1_dollars_per_cdd": "0.40", "o3_peak_hvac_kw": "3.0",
             "o4_dollars_per_cdd_whole_home": "0.55",
             "weekly_cdd": "52", "mean_enthalpy_btu_lb": "31",
             "total_solar_wh_m2": "98000", "mean_wind_mph": "6",
             "max_temp_f": "89", "max_dewpoint_f": "71"},
            {"week_start_ct": "2026-07-27", "arm": "B", "qualifies": "true",
             "o1_dollars_per_cdd": "0.45", "o3_peak_hvac_kw": "3.5",
             "o4_dollars_per_cdd_whole_home": "0.60",
             "weekly_cdd": "98", "mean_enthalpy_btu_lb": "39",
             "total_solar_wh_m2": "118000", "mean_wind_mph": "5",
             "max_temp_f": "93", "max_dewpoint_f": "74"},
        ],
    )
    baseline = tmp_path / "baseline_cov.npz"
    np.savez(
        baseline,
        cov=np.eye(6, dtype=np.float64),
        mean=np.zeros(6, dtype=np.float64),
    )
    pipeline.stage4_matching(
        stage3_dir=tmp_path / "stage3",
        baseline_cov_path=baseline,
        out_dir=tmp_path,
    )
    pairs_path = tmp_path / "stage4" / "matched_pairs.csv"
    with open(pairs_path) as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    # The 50-CDD A-week pairs with the 52-CDD B-week (Jun pair).
    jun_pair = next(r for r in rows if r["week_a"] == "2026-06-08")
    assert jun_pair["week_b"] == "2026-06-22"


def test_stage5_computes_pair_differences(tmp_path):
    # Build a 3-pair matched-pair table by hand; stage5 reads weekly + pairs.
    weekly = tmp_path / "stage3"
    weekly.mkdir()
    _write_minimal_weekly(
        weekly / "weekly.csv",
        [
            {"week_start_ct": f"A{i}", "arm": "A", "qualifies": "true",
             "o1_dollars_per_cdd": f"{0.6 - 0.05*i}", "o3_peak_hvac_kw": "4.0",
             "o4_dollars_per_cdd_whole_home": "0.8",
             "weekly_cdd": "60", "mean_enthalpy_btu_lb": "30",
             "total_solar_wh_m2": "100000", "mean_wind_mph": "5",
             "max_temp_f": "90", "max_dewpoint_f": "70"}
            for i in range(3)
        ]
        + [
            {"week_start_ct": f"B{i}", "arm": "B", "qualifies": "true",
             "o1_dollars_per_cdd": f"{0.5 - 0.05*i}", "o3_peak_hvac_kw": "3.5",
             "o4_dollars_per_cdd_whole_home": "0.7",
             "weekly_cdd": "60", "mean_enthalpy_btu_lb": "30",
             "total_solar_wh_m2": "100000", "mean_wind_mph": "5",
             "max_temp_f": "90", "max_dewpoint_f": "70"}
            for i in range(3)
        ],
    )
    stage4 = tmp_path / "stage4"
    stage4.mkdir()
    with open(stage4 / "matched_pairs.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pair_id", "week_a", "week_b", "distance", "quality"])
        for i in range(3):
            w.writerow([i, f"A{i}", f"B{i}", "0.5", "primary"])
    pipeline.stage5_effects(stage3_dir=weekly, stage4_dir=stage4, out_dir=tmp_path)
    with open(tmp_path / "stage5" / "effects.csv") as f:
        rows = list(csv.DictReader(f))
    # Every B minus A diff = -0.1 for O1. Median = -0.1.
    o1 = next(r for r in rows if r["outcome"] == "o1_dollars_per_cdd")
    assert float(o1["median_diff"]) == pytest.approx(-0.10, abs=1e-9)
    assert int(o1["n_pairs"]) == 3
