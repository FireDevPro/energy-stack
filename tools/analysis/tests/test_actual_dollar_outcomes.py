"""RED acceptance tests for Phase 1 of the actual-dollar-outcomes migration.

Per docs/plans/actual-dollar-outcomes-migration-plan.md Phase 1.1.
All tests are marked xfail(strict=True) in the Phase 1.1 commit — they
fail because the helpers / columns they assert against do not yet
exist. Phase 1.2 implementation lands the helpers + columns, and
removes the xfail markers in the same PR to flip the suite to GREEN.

Five oracle tests, mirroring the migration plan's acceptance set:

1. test_weekly_actual_dollars_oracle — pure helper, hand-computed value
2. test_eagle_hourly_kwh_from_delivered_oracle — pure helper, totalizer differential
3. test_eagle_refoss_mains_drift_boundary — parametrized 3-case boundary
   pinning the >= operator AND absolute-difference behavior in both directions
   (per Chris's lock 2026-05-12)
4. test_stage3_weekly_csv_has_actual_dollar_and_kwh_columns — Stage 3 integration
5. test_stage5_effects_percent_of_arm_a_populated_for_dollar_outcomes_only —
   Stage 5 integration; pins O1/O4 scope and the O2 exclusion

Eagle is the canonical whole-home source. Refoss split-phase mains
(em:1 + em:7) is a CT-clamp sanity check. Drift threshold is locked at
10% weekly per docs/replay-validation/2026-05-12-eagle-shape-verification/findings.md.
"""
from __future__ import annotations

import csv
import datetime
from pathlib import Path
from typing import Any, Sequence

import pytest

from tools.analysis import pipeline


PHASE_1_2_PENDING = pytest.mark.xfail(
    strict=True,
    reason="Phase 1.2 implementation pending; expected RED until helpers + columns land",
)


# ---------------------------------------------------------------------------
# Oracle 1 — Weekly HVAC actual dollars helper
# ---------------------------------------------------------------------------


@PHASE_1_2_PENDING
def test_weekly_actual_dollars_oracle():
    """O1 numerator: Σ_h hvac_kwh × (supply_c + dtod_c) / 100, in dollars.

    Single hour at 15:00 CT (Mid-Day Peak DTOD = 10.712¢/kWh):
        1.0 kWh × (10.0 + 10.712)¢ = 20.712¢ = $0.20712
    """
    helper = getattr(pipeline, "weekly_actual_dollars", None)
    assert helper is not None, "weekly_actual_dollars helper not yet implemented"

    hourly = [{"hour_of_day_ct": 15, "hvac_kwh": 1.0, "supply_c_per_kwh": 10.0}]
    out = helper(hourly_records=hourly)
    assert out == pytest.approx(0.20712, abs=1e-5)


# ---------------------------------------------------------------------------
# Oracle 2 — Eagle delivered_kwh → hourly kWh differential
# ---------------------------------------------------------------------------


@PHASE_1_2_PENDING
def test_eagle_hourly_kwh_from_delivered_oracle():
    """Eagle.meter delivered_kwh is a monotonic cumulative totalizer.
    Per-hour kWh = last_value_in_hour - last_value_in_prior_hour.

    Synthetic shape: 168 hourly samples, linear ramp +1.5 kWh per hour
    starting at delivered_kwh=10000.0 at week_start. Each of the 168
    output values should be 1.5 kWh.
    """
    pd = pytest.importorskip("pandas")
    helper = getattr(pipeline, "eagle_hourly_kwh_from_delivered", None)
    assert helper is not None, "eagle_hourly_kwh_from_delivered helper not yet implemented"

    week_start_ct = datetime.date(2026, 6, 8)  # Monday
    week_start_utc = datetime.datetime.combine(
        week_start_ct, datetime.time(5, 0), tzinfo=datetime.timezone.utc,
    )
    rows = []
    for h in range(169):  # 168 intervals → 169 samples
        rows.append({
            "_time": week_start_utc + datetime.timedelta(hours=h),
            "_field": "delivered_kwh",
            "_value": 10000.0 + 1.5 * h,
        })
    eagle_df = pd.DataFrame(rows)

    hourly_kwh = helper(eagle_df=eagle_df, week_start_ct=week_start_ct)
    assert len(hourly_kwh) == 168
    for h, v in enumerate(hourly_kwh):
        assert v == pytest.approx(1.5, abs=1e-6), f"hour {h}: expected 1.5, got {v}"


# ---------------------------------------------------------------------------
# Oracle 3 — Eagle vs Refoss-mains drift threshold (3-case boundary)
# ---------------------------------------------------------------------------


@PHASE_1_2_PENDING
@pytest.mark.parametrize(
    "eagle_kwh, refoss_kwh, expected_drift_pct, expected_flag",
    [
        # Just under the 10% boundary: drift = 9.9%, no flag.
        pytest.param(100.0, 109.9, 9.9, False, id="just_under_threshold"),
        # Exactly at the 10% boundary: drift = 10.0%, flag (>= operator).
        pytest.param(100.0, 110.0, 10.0, True, id="at_threshold_refoss_higher"),
        # Boundary on the negative side: |90 - 100| / 100 = 10.0%, flag.
        # Pins absolute-difference behavior — both Refoss-higher and
        # Refoss-lower trigger the flag at the same threshold.
        pytest.param(100.0, 90.0, 10.0, True, id="at_threshold_refoss_lower"),
    ],
)
def test_eagle_refoss_mains_drift_boundary(
    eagle_kwh: float,
    refoss_kwh: float,
    expected_drift_pct: float,
    expected_flag: bool,
):
    """drift_pct = abs(refoss_mains_kwh - eagle_kwh) / eagle_kwh * 100.

    Eagle is denominator because Eagle is canonical (the smart meter
    feed the ComEd bill is computed from). Threshold is >= 10% weekly,
    per the locked finding at
    docs/replay-validation/2026-05-12-eagle-shape-verification/findings.md.
    Drift >= threshold flags provenance for investigation; drift alone
    does NOT drop Eagle-derived outcomes and Refoss is never averaged.
    """
    threshold = getattr(pipeline, "EAGLE_REFOSS_DRIFT_THRESHOLD_PCT", None)
    helper = getattr(pipeline, "eagle_refoss_mains_drift", None)
    assert threshold == 10.0, (
        f"EAGLE_REFOSS_DRIFT_THRESHOLD_PCT not yet locked at 10.0; got {threshold}"
    )
    assert helper is not None, "eagle_refoss_mains_drift helper not yet implemented"

    result = helper(eagle_kwh=eagle_kwh, refoss_mains_kwh=refoss_kwh)
    assert result["drift_pct"] == pytest.approx(expected_drift_pct, abs=1e-9)
    assert result["exceeds_threshold"] is expected_flag


# ---------------------------------------------------------------------------
# Oracle 4 — Stage 3 weekly.csv carries new actual-$ and actual-kWh columns
# ---------------------------------------------------------------------------


@PHASE_1_2_PENDING
def test_stage3_weekly_csv_has_actual_dollar_and_kwh_columns():
    """WEEKLY_CSV_LOCKED_COLUMNS includes the four new actual-outcome
    columns Phase 1.2 introduces additively (the existing $/CDD
    columns are retained in Phase 1 as cross-validation scaffolding
    only — Phase 2 removes them).
    """
    cols = set(pipeline.WEEKLY_CSV_LOCKED_COLUMNS)
    for required in (
        "weekly_hvac_dollars",
        "weekly_whole_home_dollars",
        "weekly_hvac_kwh",
        "weekly_whole_home_kwh",
    ):
        assert required in cols, (
            f"{required!r} missing from WEEKLY_CSV_LOCKED_COLUMNS; Phase 1.2 must add it"
        )


# ---------------------------------------------------------------------------
# Oracle 5 — Stage 5 effects.csv percent_of_arm_a scope (O1/O4 only, O2 excluded)
# ---------------------------------------------------------------------------


def _write_weekly_csv_with_actual_cols(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    """Helper that writes a stage3/weekly.csv including the new actual-$
    and actual-kWh columns. Used by Oracle 5. Will start working once
    Phase 1.2 adds the columns to WEEKLY_CSV_LOCKED_COLUMNS.
    """
    fields = list(pipeline.WEEKLY_CSV_LOCKED_COLUMNS)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in fields})


@PHASE_1_2_PENDING
def test_stage5_effects_percent_of_arm_a_populated_for_dollar_outcomes_only(tmp_path: Path):
    """Stage 5 effects.csv adds a `percent_of_arm_a` column.

    Locked scope (per spec amendment in PR #108 and Chris 2026-05-12):
      - Populated for O1 (weekly_hvac_dollars) and O4 (weekly_whole_home_dollars).
      - NOT populated for O3 (peak kW), O7 (HVAC kWh), O8 (whole-home kWh) —
        the unit isn't dollars; percent-of-cost framing doesn't apply.
      - O2 is computed in Stage 6 (not Stage 5) with a different
        bootstrap denominator; it does NOT appear in this CSV at all,
        and reports absolute $ delta only outside Stage 5.

    Synthetic fixture: 3 matched pairs. Arm A weekly HVAC $ = 1.00 per
    week; Arm B = 0.80 per week. Median Δ = -0.20. percent_of_arm_a
    = median(-0.20 / 1.00 × 100) = -20.0%.
    """
    stage3 = tmp_path / "stage3"
    stage3.mkdir()
    rows = []
    for i in range(3):
        rows.append({
            "week_start_ct": f"A{i}", "arm": "A", "qualifies": "true",
            "weekly_hvac_dollars": "1.00",
            "weekly_whole_home_dollars": "2.00",
            "weekly_hvac_kwh": "10.0",
            "weekly_whole_home_kwh": "20.0",
            "o3_peak_hvac_kw": "4.0",
            "o1_dollars_per_cdd": "0.6", "o4_dollars_per_cdd_whole_home": "0.8",
            "weekly_cdd": "60", "mean_enthalpy_btu_lb": "30",
            "total_solar_wh_m2": "100000", "mean_wind_mph": "5",
            "max_temp_f": "90", "max_dewpoint_f": "70",
        })
        rows.append({
            "week_start_ct": f"B{i}", "arm": "B", "qualifies": "true",
            "weekly_hvac_dollars": "0.80",
            "weekly_whole_home_dollars": "1.60",
            "weekly_hvac_kwh": "10.0",
            "weekly_whole_home_kwh": "20.0",
            "o3_peak_hvac_kw": "3.5",
            "o1_dollars_per_cdd": "0.5", "o4_dollars_per_cdd_whole_home": "0.7",
            "weekly_cdd": "60", "mean_enthalpy_btu_lb": "30",
            "total_solar_wh_m2": "100000", "mean_wind_mph": "5",
            "max_temp_f": "90", "max_dewpoint_f": "70",
        })
    _write_weekly_csv_with_actual_cols(stage3 / "weekly.csv", rows)

    stage4 = tmp_path / "stage4"
    stage4.mkdir()
    with open(stage4 / "matched_pairs.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pair_id", "week_a", "week_b", "distance", "quality"])
        for i in range(3):
            w.writerow([i, f"A{i}", f"B{i}", "0.5", "primary"])

    pipeline.stage5_effects(stage3_dir=stage3, stage4_dir=stage4, out_dir=tmp_path)

    with open(tmp_path / "stage5" / "effects.csv") as f:
        rows = list(csv.DictReader(f))

    by_outcome = {r["outcome"]: r for r in rows}

    # O1 (weekly_hvac_dollars) — dollar outcome, percent populated.
    o1 = by_outcome["weekly_hvac_dollars"]
    assert float(o1["median_diff"]) == pytest.approx(-0.20, abs=1e-9)
    assert o1["percent_of_arm_a"] != "", "percent_of_arm_a must be populated for O1"
    assert float(o1["percent_of_arm_a"]) == pytest.approx(-20.0, abs=1e-9)

    # O4 (weekly_whole_home_dollars) — dollar outcome, percent populated.
    o4 = by_outcome["weekly_whole_home_dollars"]
    assert o4["percent_of_arm_a"] != "", "percent_of_arm_a must be populated for O4"
    assert float(o4["percent_of_arm_a"]) == pytest.approx(-20.0, abs=1e-9)

    # O3 (peak HVAC kW) — non-dollar outcome, percent blank.
    o3 = by_outcome["o3_peak_hvac_kw"]
    assert o3["percent_of_arm_a"] == "", (
        "percent_of_arm_a must be blank for non-dollar outcome o3_peak_hvac_kw"
    )

    # O7 (weekly_hvac_kwh) — kWh outcome, percent blank.
    o7 = by_outcome["weekly_hvac_kwh"]
    assert o7["percent_of_arm_a"] == "", (
        "percent_of_arm_a must be blank for kWh outcome weekly_hvac_kwh"
    )

    # O8 (weekly_whole_home_kwh) — kWh outcome, percent blank.
    o8 = by_outcome["weekly_whole_home_kwh"]
    assert o8["percent_of_arm_a"] == "", (
        "percent_of_arm_a must be blank for kWh outcome weekly_whole_home_kwh"
    )

    # O2 must NOT appear in stage5/effects.csv at all — it lives in
    # Stage 6 with a different bootstrap denominator (PJM 5CP hours,
    # not matched weekly pairs).
    assert "o2_layer1" not in by_outcome, (
        "O2 must NOT appear in stage5/effects.csv; it lives in Stage 6"
    )
