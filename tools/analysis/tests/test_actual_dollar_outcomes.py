"""Acceptance tests for the actual-dollar-outcomes migration.

Per docs/plans/actual-dollar-outcomes-migration-plan.md. Phase 1.1
wrote these as RED (xfail) tests; Phase 1.2 lands the implementation
and removes the xfail markers, flipping the suite to GREEN.

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
import json
from pathlib import Path
from typing import Any, Sequence

import pytest

from tools.analysis import pipeline


# ---------------------------------------------------------------------------
# Oracle 1 — Weekly HVAC actual dollars helper
# ---------------------------------------------------------------------------


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


def test_eagle_hourly_kwh_first_hour_uses_first_sample_when_no_pre_boundary():
    """Edge case (P2 finding 2026-05-13): the Stage 1 export typically
    starts at the week boundary and the first Eagle sample lands a few
    seconds AFTER the boundary, so no sample exists at-or-before
    boundary 0. Without the fallback, hour 0 would be 0.0 — a silent
    undercount.

    Fixture: the first Eagle sample is at week_start + 30 seconds,
    not at the boundary itself. Linear 1.5 kWh/h ramp continues from
    there.

    Expectation: hour 0's kWh is approximately 1.5 (treating the
    boundary 0 baseline as the first available sample, ~30s/3600s ≈
    0.8% undercount of the true hour 0 energy — within acceptable
    edge-effect tolerance). Hours 1-167 are unaffected and exactly 1.5.
    """
    pd = pytest.importorskip("pandas")
    week_start_ct = datetime.date(2026, 6, 8)
    week_start_utc = datetime.datetime.combine(
        week_start_ct, datetime.time(5, 0), tzinfo=datetime.timezone.utc,
    )
    rows = []
    # First sample at +30s, not at the boundary. Subsequent samples
    # at hour boundaries thereafter. Ramp: 10000 + 1.5 × elapsed_hours
    # from the +30s base.
    rows.append({
        "_time": week_start_utc + datetime.timedelta(seconds=30),
        "_field": "delivered_kwh",
        "_value": 10000.0 + 1.5 * (30 / 3600),
    })
    for h in range(1, 169):
        rows.append({
            "_time": week_start_utc + datetime.timedelta(hours=h),
            "_field": "delivered_kwh",
            "_value": 10000.0 + 1.5 * h,
        })
    eagle_df = pd.DataFrame(rows)

    hourly_kwh = pipeline.eagle_hourly_kwh_from_delivered(
        eagle_df=eagle_df, week_start_ct=week_start_ct,
    )
    assert len(hourly_kwh) == 168
    # Hour 0 uses the first sample (at +30s) as the boundary 0
    # baseline. True energy in [0, 1h) = 1.5; reported = boundary[1]
    # value − first-sample value = (10000 + 1.5) − (10000 + 1.5×30/3600)
    # = 1.5 − 0.0125 ≈ 1.4875. ~0.8% undercount — much better than 0.0.
    assert hourly_kwh[0] == pytest.approx(1.5 - 1.5 * 30 / 3600, abs=1e-6)
    # Hours 1-167 are unaffected: full ramp differential.
    for h in range(1, 168):
        assert hourly_kwh[h] == pytest.approx(1.5, abs=1e-6), (
            f"hour {h}: expected 1.5, got {hourly_kwh[h]}"
        )


# ---------------------------------------------------------------------------
# Oracle 3 — Eagle vs Refoss-mains drift threshold (3-case boundary)
# ---------------------------------------------------------------------------


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
    assert o1["unit"] == "dollars"
    assert float(o1["median_diff"]) == pytest.approx(-0.20, abs=1e-9)
    assert o1["percent_of_arm_a"] != "", "percent_of_arm_a must be populated for O1"
    assert float(o1["percent_of_arm_a"]) == pytest.approx(-20.0, abs=1e-9)

    # O4 (weekly_whole_home_dollars) — dollar outcome, percent populated.
    o4 = by_outcome["weekly_whole_home_dollars"]
    assert o4["unit"] == "dollars"
    assert o4["percent_of_arm_a"] != "", "percent_of_arm_a must be populated for O4"
    assert float(o4["percent_of_arm_a"]) == pytest.approx(-20.0, abs=1e-9)

    # O3 (peak HVAC kW) — non-dollar outcome, percent blank, unit kw.
    o3 = by_outcome["o3_peak_hvac_kw"]
    assert o3["unit"] == "kw"
    assert o3["percent_of_arm_a"] == "", (
        "percent_of_arm_a must be blank for non-dollar outcome o3_peak_hvac_kw"
    )

    # O7 (weekly_hvac_kwh) — kWh outcome, percent blank.
    o7 = by_outcome["weekly_hvac_kwh"]
    assert o7["unit"] == "kwh"
    assert o7["percent_of_arm_a"] == "", (
        "percent_of_arm_a must be blank for kWh outcome weekly_hvac_kwh"
    )

    # O8 (weekly_whole_home_kwh) — kWh outcome, percent blank.
    o8 = by_outcome["weekly_whole_home_kwh"]
    assert o8["unit"] == "kwh"
    assert o8["percent_of_arm_a"] == "", (
        "percent_of_arm_a must be blank for kWh outcome weekly_whole_home_kwh"
    )

    # O2 must NOT appear in stage5/effects.csv at all — it lives in
    # Stage 6 with a different bootstrap denominator (PJM 5CP hours,
    # not matched weekly pairs).
    assert "o2_layer1" not in by_outcome, (
        "O2 must NOT appear in stage5/effects.csv; it lives in Stage 6"
    )


# ---------------------------------------------------------------------------
# P1 behavioral test — Eagle missing drops O4 / O8 with reason code
# (no silent Refoss substitution)
# ---------------------------------------------------------------------------


def test_eagle_missing_drops_whole_home_outcomes_no_silent_refoss_substitution():
    """When Eagle is absent for a week, weekly_whole_home_dollars and
    weekly_whole_home_kwh DROP for that week (empty cells). Refoss
    em:1 + em:7 mains is NOT silently substituted as canonical.

    Per docs/EXPERIMENT_DESIGN.md §2 and the locked findings at
    docs/replay-validation/2026-05-12-eagle-shape-verification/findings.md.

    Direct unit test of _compute_weekly_row with hourly_eagle_records=[].
    Other outcomes (O1 HVAC dollars, O3 peak kW, O7 HVAC kWh) still
    populate normally — only the whole-home pair drops.
    """
    inputs = {
        "week_start_ct": datetime.date(2026, 6, 8),
        "arm": "A",
        "qualifies": True,
        "daily_avg_temps_f": [75.0] * 7,
        "hourly_hvac_records": [
            {"hour_of_day_ct": h % 24, "hvac_kwh": 0.5, "supply_c_per_kwh": 8.0}
            for h in range(168)
        ],
        "hourly_mains_records": [
            {"hour_of_day_ct": h % 24, "hvac_kwh": 1.5, "supply_c_per_kwh": 8.0}
            for h in range(168)
        ],
        # Eagle absent for this week — Refoss must NOT be substituted.
        "hourly_eagle_records": [],
        "hourly_weather": [
            {"temp_f": 85.0, "dewpoint_f": 70.0, "pressure_inhg": 29.92,
             "solar_wm2": 100.0, "wind_mph": 5.0}
            for _ in range(168)
        ],
    }
    row = pipeline._compute_weekly_row(inputs)

    # O4 (whole-home dollars) and O8 (whole-home kWh) DROP — empty cells.
    assert row["weekly_whole_home_dollars"] == "", (
        "weekly_whole_home_dollars must drop when Eagle is absent; "
        f"got {row['weekly_whole_home_dollars']!r} (Refoss-substitution "
        "would have produced a numeric value)"
    )
    assert row["weekly_whole_home_kwh"] == "", (
        "weekly_whole_home_kwh must drop when Eagle is absent; "
        f"got {row['weekly_whole_home_kwh']!r}"
    )

    # Other outcomes still populate normally.
    assert isinstance(row["weekly_hvac_dollars"], float)
    assert row["weekly_hvac_dollars"] > 0
    assert isinstance(row["weekly_hvac_kwh"], float)
    assert row["weekly_hvac_kwh"] > 0
    assert isinstance(row["o3_peak_hvac_kw"], float)
    assert row["o3_peak_hvac_kw"] > 0


# ---------------------------------------------------------------------------
# Real-loader integration test — addresses Chris's PR-review test gap
# 2026-05-13. Builds a synthetic Stage 1 export (refoss + comed.prices +
# ecowitt + eagle parquets via the fixture_real_shape helpers), runs the
# full Stage 3 orchestrator, asserts the new columns are populated from
# the real loader path AND that Eagle is canonical (not Refoss-mains
# silently substituted) AND that the drift provenance fires.
# ---------------------------------------------------------------------------


def test_stage3_real_loader_eagle_canonical_with_drift_provenance(tmp_path: Path):
    """Full Stage 3 orchestrator through _load_stage3_inputs_for_week
    against a synthetic real-shape Stage 1 export. Proves:

    - Eagle is canonical: weekly_whole_home_kwh equals Eagle's totalized
      energy, NOT the Refoss-mains computed value (the two are
      deliberately set to differ by ~25% in this fixture).
    - weekly_hvac_dollars and weekly_hvac_kwh populate from Refoss HVAC
      channels via the real loader (not zero).
    - stage3/provenance.json carries a per-week drift record with the
      correct drift_pct and exceeds_threshold=True (since the
      fixture's drift is over the 10% threshold).
    """
    pytest.importorskip("pandas")
    from tools.analysis.tests import fixture_real_shape as frs

    # Week of 2026-06-08 (Mon) — CT midnight = UTC 05:00 (CDT).
    week_start_ct = datetime.date(2026, 6, 8)
    week_start_utc = datetime.datetime(2026, 6, 8, 5, 0, tzinfo=datetime.timezone.utc)
    week_end_utc = week_start_utc + datetime.timedelta(days=7)

    # Refoss mains: em:1 + em:7 sum to 1500 W = 1.5 kWh/h × 168 = 252 kWh/wk.
    # Refoss HVAC: em:2 + em:8 + em:9 sum to 2000 W = 2.0 kWh/h × 168 = 336 kWh/wk.
    refoss = frs.build_refoss_channel_df(
        start_utc=week_start_utc,
        end_utc=week_end_utc,
        power_w_by_channel={
            "em:1": 800.0,   # mains leg 1
            "em:7": 700.0,   # mains leg 2  -> total mains 1500 W
            "em:2": 1000.0,  # HVAC leg
            "em:8": 950.0,   # HVAC leg
            "em:9": 50.0,    # furnace blower -> total HVAC 2000 W
        },
        cadence_minutes=1,
    )

    # Eagle delivered_kwh totalizer: 2.0 kWh/h × 168 = 336 kWh/wk.
    # Deliberately ~33% higher than Refoss mains (252 kWh) so the drift
    # fires (>= 10% threshold). drift_pct = |252 - 336| / 336 × 100 = 25%.
    eagle = frs.build_eagle_meter_df(
        start_utc=week_start_utc,
        end_utc=week_end_utc,
        base_kwh=10000.0,
        kwh_per_hour=2.0,
        cadence_seconds=30,
    )

    prices = frs.build_comed_prices_df(
        start_utc=week_start_utc,
        end_utc=week_end_utc,
        price_cents_fn=lambda ts: 5.0,
        cadence_minutes=5,
    )

    ecowitt = frs.build_ecowitt_weather_df(
        start_utc=week_start_utc,
        end_utc=week_end_utc,
        temp_f_fn=lambda ts: 80.0,
        dewpoint_f_fn=lambda ts: 65.0,
        cadence_minutes=5,
    )

    stage1_dir = tmp_path / "stage1"
    frs.write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "refoss.channel": refoss,
            "comed.prices": prices,
            "ecowitt.weather": ecowitt,
            "eagle.meter": eagle,
        },
        window_start_ct=week_start_utc.isoformat(),
        window_end_ct=week_end_utc.isoformat(),
    )

    # Stage 2 qualifying_weeks.csv naming the one qualifying week.
    stage2_dir = tmp_path
    with open(stage2_dir / "qualifying_weeks.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["week_start_ct", "arm", "qualifying"])
        w.writerow([week_start_ct.isoformat(), "A", "true"])

    pipeline.stage3_weekly(
        stage1_dir=stage1_dir, stage2_dir=stage2_dir, out_dir=tmp_path,
    )

    # Read the weekly.csv row produced by the real loader path.
    with open(tmp_path / "stage3" / "weekly.csv") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    row = rows[0]

    # Eagle canonicality: weekly_whole_home_kwh must equal Eagle's
    # totalized energy (~336 kWh), NOT the Refoss-mains-derived value
    # (~252 kWh). 1% tolerance for first-hour edge effect (~0.8%
    # undercount when the first sample is +30s rather than exactly at
    # the boundary, per Phase 1.2 edge-case handling).
    eagle_expected_kwh = 2.0 * 168
    refoss_mains_expected_kwh = 1.5 * 168
    actual_kwh = float(row["weekly_whole_home_kwh"])
    assert abs(actual_kwh - eagle_expected_kwh) / eagle_expected_kwh < 0.01, (
        f"weekly_whole_home_kwh ({actual_kwh:.2f}) must equal Eagle "
        f"({eagle_expected_kwh:.2f}), NOT Refoss-mains "
        f"({refoss_mains_expected_kwh:.2f}). A Refoss-substitution bug "
        f"would land at the Refoss number."
    )

    # HVAC outcomes populated and non-zero via the real loader.
    assert float(row["weekly_hvac_dollars"]) > 0
    assert float(row["weekly_hvac_kwh"]) > 0
    assert float(row["weekly_whole_home_dollars"]) > 0

    # Drift provenance: drift_pct ~ 25%, exceeds_threshold = True.
    with open(tmp_path / "stage3" / "provenance.json") as pf:
        provenance = json.load(pf)
    drift_records = provenance["eagle_vs_refoss_drift"]
    assert len(drift_records) == 1
    drift = drift_records[0]
    assert drift["week_start_ct"] == week_start_ct.isoformat()
    assert drift["arm"] == "A"
    assert drift["exceeds_threshold"] is True
    # |refoss - eagle| / eagle * 100 with the fixture's 1.5 vs 2.0 ratio
    # is exactly 25% modulo the first-hour edge effect; allow 1% slack.
    assert 24.0 < drift["drift_pct"] < 26.0
    assert provenance["drift_threshold_pct"] == 10.0
    # No Eagle-missing entries for this fixture (Eagle is present).
    assert provenance["eagle_missing_weeks"] == []
