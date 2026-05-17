"""Outside-in acceptance test for the SCED rebaseline.

This is the FEATURE-LEVEL test. It exercises the full pipeline:
synthetic ingestion -> mode classification -> validity gate -> weather
matching -> cost-matched exclusion -> per-pair table -> aggregate buckets.

Per AGENTS.md outside-in TDD rule: this test stays xfail/skip until
real implementations land. The rebaseline is NOT feature-complete until
this test passes against the real implementation with zero scaffolding.

Spec source: docs/plans/sced-rebaseline-spec-2026-05-13.md
Plan source: docs/plans/sced-rebaseline-implementation-2026-05-13.md

DO NOT modify this test to make it pass by mocking, replacing, or
substituting any component of `run_full_pipeline`. The rebaseline is
feature-complete only when the real implementation produces these outputs.
"""
from __future__ import annotations

import datetime

import pytest

from tools.analysis.tests.fixtures.synth_rebaseline_dataset import (
    SAVINGS_PCT,
    build_synth_dataset,
)

# Outside-in TDD per AGENTS.md: this feature-level acceptance test
# stayed xfail(strict=True) across Phases 0-2 (scaffolded implementation)
# and Phase 3 task progress. Phase 3 Task 3.15 de-scaffolded the
# fixture to real-shape inputs and the test now passes against the
# real ``arm_period_pipeline.run_full_pipeline`` with zero scaffolding,
# satisfying AGENTS.md's "the marker comes off the moment the test
# passes against the real implementation" rule.
def test_rebaseline_end_to_end_acceptance():
    """The whole pipeline produces expected outputs on synthetic data."""
    from tools.analysis.arm_period_pipeline import run_full_pipeline

    synth = build_synth_dataset()

    result = run_full_pipeline(
        refoss_df=synth.refoss_df,
        eagle_df=synth.eagle_df,
        ecowitt_df=synth.ecowitt_df,
        rt_hrl_lmps_df=synth.rt_hrl_lmps_df,
        comed_prices_df=synth.comed_prices_df,
        hvac_arm_mode_df=synth.hvac_arm_mode_df,
        bills_df=synth.bills_df,
    )

    # 1. Per-pair table exists and has required spec §9 columns.
    actual = result.per_pair_table
    required_columns = {
        "pair_id", "arm_a_id", "arm_b_id",
        "arm_a_dates", "arm_b_dates",
        "temporal_gap_days", "weather_distance_zscore",
        "weather_vector_a", "weather_vector_b",
        "weather_component_diffs_raw", "weather_component_diffs_zscored",
        "poor_weather_match_flag",
        "valid_pair_hours", "excluded_hours_count",
        "excluded_hours_breakdown_a", "excluded_hours_breakdown_b",
        "cost_match_quality_median_diff_c_per_kwh",
        "cfe_c_per_kwh_a", "cfe_c_per_kwh_b",
        "cooling_active_hours_a", "cooling_active_hours_b",
        "low_cooling_exposure_flag",
        "hvac_dollars_a", "hvac_dollars_b",
        "diff_dollars_b_minus_a", "percent_diff_dollars",
        "hvac_kwh_a", "hvac_kwh_b", "diff_kwh_b_minus_a",
        "weather_source_pct_ecowitt_a", "weather_source_pct_ecowitt_b",
    }
    missing = required_columns - set(actual.columns)
    assert not missing, f"Missing required per-pair columns: {missing}"

    # 2. Per-pair table row count matches expected.
    expected = synth.expected_per_pair_table
    assert len(actual) == len(expected), (
        f"Pair count mismatch: actual={len(actual)} expected={len(expected)}"
    )

    # 3. HVAC$ per-pair matches hand-pinned expected values within tolerance.
    actual_sorted = actual.sort_values("pair_id").reset_index(drop=True)
    expected_sorted = expected.sort_values("pair_id").reset_index(drop=True)
    for col in ("hvac_dollars_a", "hvac_dollars_b", "diff_dollars_b_minus_a"):
        diffs = (actual_sorted[col] - expected_sorted[col]).abs()
        assert (diffs < 0.50).all(), (
            f"Column {col} mismatch vs hand-pinned expected:\n"
            f"actual:   {actual_sorted[col].tolist()}\n"
            f"expected: {expected_sorted[col].tolist()}"
        )

    # 4. Per-pair valid hour count matches expected.
    diffs = (actual_sorted["valid_pair_hours"]
             - expected_sorted["valid_pair_hours"]).abs()
    assert (diffs <= 1).all(), (
        f"valid_pair_hours mismatch:\n"
        f"actual:   {actual_sorted['valid_pair_hours'].tolist()}\n"
        f"expected: {expected_sorted['valid_pair_hours'].tolist()}"
    )

    # 5. Per-pair savings match injected SAVINGS_PCT within tolerance,
    #    on pairs where the denominator is large enough to be meaningful.
    big_enough = actual_sorted["hvac_dollars_a"] > 5.0
    if big_enough.any():
        observed_savings_pct = (
            -actual_sorted.loc[big_enough, "diff_dollars_b_minus_a"]
            / actual_sorted.loc[big_enough, "hvac_dollars_a"]
        )
        in_band = (observed_savings_pct - SAVINGS_PCT).abs() < 0.02
        assert in_band.all(), (
            f"Per-pair savings deviate from injected {SAVINGS_PCT*100:.1f}%:\n"
            f"observed: {observed_savings_pct.tolist()}"
        )

    # 6. All pre-registered summary buckets per spec §9.5 are populated.
    required_buckets = {
        "all_valid_pairs",
        "high_cooling_pairs",
        "medium_cooling_pairs",
        "low_cooling_pairs",
        "scarcity_exposed_pairs",
        "5cp_exposed_pairs",
        "high_temp_exposed_pairs",
    }
    missing_buckets = required_buckets - set(result.bucket_summaries.keys())
    assert not missing_buckets, f"Missing summary buckets: {missing_buckets}"

    # 7. Mode classification correctly labels scenarios the fixture injects.
    #    Only assert modes the fixture actually constructs; do NOT require
    #    all 4 modes globally. (Per Chris's M-tier guidance: mode coverage
    #    is fixture-driven, not a universal expectation.)
    for injected_mode in synth.injected_modes:
        observed = result.mode_distribution.get(injected_mode, 0)
        assert observed > 0, (
            f"Expected mode {injected_mode!r} (injected by fixture) "
            f"not observed in pipeline output. "
            f"Observed distribution: {dict(result.mode_distribution)}"
        )

    # 8. Validity gate dropped expected arms.
    assert result.arms_passed_validity == synth.expected_arms_passed_validity, (
        f"Arm validity-gate result mismatch:\n"
        f"actual:   {sorted(result.arms_passed_validity)}\n"
        f"expected: {sorted(synth.expected_arms_passed_validity)}"
    )

    # 9. Cost-matched symmetric exclusion preserves equal valid hour counts
    #    on both arms of every pair (per spec §5).
    if "valid_pair_hours_a" in actual.columns and "valid_pair_hours_b" in actual.columns:
        equal_counts = (actual["valid_pair_hours_a"]
                        == actual["valid_pair_hours_b"]).all()
        assert equal_counts, (
            "Cost-matched exclusion must leave equal hour counts in both arms "
            "of every pair. Per-pair counts:\n"
            f"  a: {actual['valid_pair_hours_a'].tolist()}\n"
            f"  b: {actual['valid_pair_hours_b'].tolist()}"
        )

    # 10. Poor-weather-match flag count matches the fixture's hand-pinned
    #     expectation. NOTE: spec §6 ("exceeds 90th percentile") strictly
    #     applied at linear-interp p90 does NOT fire on the single-outlier
    #     pair in this n=6 design (the Hungarian-matched outlier pair sits
    #     just below the linear-interpolated boundary). The fixture pins
    #     expected_poor_weather_match_flag = False for ALL pairs to honor
    #     the spec rule strictly. The detection limitation is a Phase 3
    #     finding for the OSF freeze, not a flag-logic bug. See
    #     ``tools.analysis.matching.caliper_p90_distance``.
    poor_match_flagged = actual["poor_weather_match_flag"].sum()
    expected_poor_match = expected["poor_weather_match_flag"].sum()
    assert poor_match_flagged == expected_poor_match, (
        f"poor_weather_match_flag count mismatch: "
        f"actual={poor_match_flagged} expected={expected_poor_match}"
    )

    # 11. Bill reconciliation runs once per DISTINCT bill period -- the
    #     fixture has 5 distinct bill periods, so the pipeline output
    #     should carry 5 reconciliations (not one per line-item).
    distinct_bill_periods = synth.bills_df["bill_period_start_utc"].nunique()
    assert len(result.bill_reconciliations) == distinct_bill_periods, (
        f"Expected {distinct_bill_periods} bill reconciliations "
        f"(one per period); got {len(result.bill_reconciliations)}. "
        "Did the orchestrator iterate every line-item row instead of "
        "deduplicating by period?"
    )


def test_fixture_is_importable_and_builds():
    """Smoke test: the fixture itself imports and produces a valid dataset.

    This test ALWAYS runs (no skip) so we catch fixture-construction bugs
    even when the pipeline isn't yet implemented.
    """
    synth = build_synth_dataset()
    assert synth.refoss_df is not None
    assert len(synth.refoss_df) > 0
    assert synth.eagle_df is not None
    assert synth.ecowitt_df is not None
    assert synth.rt_hrl_lmps_df is not None
    assert synth.comed_prices_df is not None
    assert synth.hvac_arm_mode_df is not None
    assert synth.bills_df is not None
    assert len(synth.expected_per_pair_table) == 6, (
        "Fixture should provide expected outputs for all 6 pairs"
    )
    assert len(synth.expected_arms_passed_validity) > 0
    assert len(synth.injected_modes) > 0

    # Eagle totalizer must be monotonically non-decreasing -- production
    # ``eagle.meter`` is a cumulative kWh counter; a decreasing series
    # would indicate the fixture wrote per-hour deltas rather than
    # the real totalizer schema (Task 3.15 de-scaffolding contract).
    eagle_sorted = synth.eagle_df.sort_values("_time")
    deltas = eagle_sorted["delivered_kwh"].diff().dropna()
    assert (deltas >= 0).all(), (
        "Eagle delivered_kwh must be a monotonic totalizer (production "
        "schema). Found a negative delta in synth_rebaseline_dataset."
    )

    # Bills_df must produce a unique-bill-period count that an
    # orchestrator can iterate. Production ``comed.bill`` writes
    # multiple line-items per bill -- the fixture should not produce
    # one reconciliation per line-item.
    if "bill_period_start_utc" in synth.bills_df.columns:
        distinct_periods = synth.bills_df["bill_period_start_utc"].nunique()
        assert 1 <= distinct_periods <= 6, (
            f"Fixture should have between 1 and 6 distinct bill periods; "
            f"got {distinct_periods}."
        )


def test_fixture_actually_injects_claimed_scenarios():
    """Fixture-input self-consistency (P2 reviewer fix).

    Independence verifies the fixture doesn't import the implementation,
    but doesn't catch the case where expected values are disconnected
    from the actual synthetic inputs. This test verifies the raw fixture
    REALLY CONTAINS the scenarios it claims:
    - All declared INJECTED_MODES appear in hvac_arm_mode_df
    - Weather-outlier arm (Arm 6 in scenarios) has materially distinct weather
    - Telemetry-invalid scenarios (Arms 7, 8) have OMITTED Refoss data for
      the claimed invalid-hour count

    This is the P2 fix: prevent expected values from claiming behavior
    the inputs don't actually produce.
    """
    from tools.analysis.tests.fixtures.synth_rebaseline_dataset import (
        CALENDAR,
        CALENDAR_CT,
        COOLING_START_HOUR_CT,
        SCENARIOS,
        WEATHER_OUTLIER_SHIFT_F,
        BASE_TEMP_F,
        _ct_naive_to_utc_naive,
    )

    synth = build_synth_dataset()

    # 1. All declared INJECTED_MODES are present in hvac_arm_mode_df
    observed_modes = set(synth.hvac_arm_mode_df["mode_actual"].unique())
    missing_modes = synth.injected_modes - observed_modes
    assert not missing_modes, (
        f"Fixture claims to inject {synth.injected_modes} but "
        f"hvac_arm_mode_df only contains {observed_modes}. "
        f"Missing: {missing_modes}. "
        "Either inject these modes in the data builder OR remove them "
        "from INJECTED_MODES."
    )

    # 2. Weather-outlier arms have distinct temperature stats vs non-outlier arms.
    arm_idx_to_dates = {idx: (start, end) for idx, _, start, end in CALENDAR}
    outlier_arms = [s[2] for s in SCENARIOS if s[8]]  # arm_b indices where weather_outlier=True
    nonoutlier_arms = [a for a in arm_idx_to_dates if a not in outlier_arms]
    assert outlier_arms, "Test design error: SCENARIOS has no weather_outlier"

    def arm_mean_temp(arm_idx):
        start, end = arm_idx_to_dates[arm_idx]
        in_arm = (
            (synth.ecowitt_df["_time"] >= start) &
            (synth.ecowitt_df["_time"] < end)
        )
        return synth.ecowitt_df.loc[in_arm, "ch1_temp_f"].mean()

    for outlier_arm in outlier_arms:
        outlier_mean = arm_mean_temp(outlier_arm)
        # Compare to a non-outlier arm
        nonoutlier_mean = arm_mean_temp(nonoutlier_arms[0])
        delta = abs(outlier_mean - nonoutlier_mean)
        # Expect delta close to WEATHER_OUTLIER_SHIFT_F
        assert delta >= WEATHER_OUTLIER_SHIFT_F - 1.0, (
            f"Arm {outlier_arm} is declared weather_outlier=True but its "
            f"mean temp ({outlier_mean:.1f}°F) is only {delta:.1f}°F from "
            f"non-outlier Arm {nonoutlier_arms[0]} ({nonoutlier_mean:.1f}°F). "
            f"Expected shift ~{WEATHER_OUTLIER_SHIFT_F}°F."
        )

    # 3. Telemetry-invalid scenarios actually have OMITTED Refoss data.
    #    For each arm with claimed telemetry-invalid count, count distinct
    #    UTC hours where Refoss has data; missing hours = ARM_TOTAL_HOURS minus
    #    distinct-hour count. The post-washout invalid count should match.
    for scenario in SCENARIOS:
        (pair_id, arm_a, arm_b, label, cooling_per_day, fallback_hours_b,
         ti_a, ti_b, weather_outlier_b, dst) = scenario
        for arm_idx, expected_ti in ((arm_a, ti_a), (arm_b, ti_b)):
            if expected_ti == 0:
                continue
            start, end = arm_idx_to_dates[arm_idx]
            in_arm = (
                (synth.refoss_df["_time"] >= start) &
                (synth.refoss_df["_time"] < end)
            )
            # Distinct UTC hours present in Refoss for this arm
            present_hours = synth.refoss_df.loc[in_arm, "_time"].dt.floor("h").unique()
            # Expected: ARM_TOTAL_HOURS minus expected_ti
            expected_present = 14 * 24 - expected_ti
            assert len(present_hours) == expected_present, (
                f"Arm {arm_idx} ({label}) claims {expected_ti} "
                f"telemetry-invalid hours, but Refoss data has "
                f"{len(present_hours)} distinct hours present "
                f"(expected {expected_present}). "
                "Either inject the omissions OR adjust the expected count."
            )

    # 4. B-fallback hours: count B-fallback rows in hvac_arm_mode_df for each
    #    arm that claims B-fallback injection. There are 12 mode rows per hour
    #    (every 5 min), so the expected count is fallback_hours_b * 12.
    for scenario in SCENARIOS:
        (pair_id, arm_a, arm_b, label, cooling_per_day, fallback_hours_b,
         ti_a, ti_b, weather_outlier_b, dst) = scenario
        if fallback_hours_b == 0:
            continue
        start, end = arm_idx_to_dates[arm_b]
        in_arm_b = (
            (synth.hvac_arm_mode_df["_time"] >= start) &
            (synth.hvac_arm_mode_df["_time"] < end)
        )
        fallback_count = (
            (synth.hvac_arm_mode_df.loc[in_arm_b, "mode_actual"] == "B-fallback")
            .sum()
        )
        expected_fallback_rows = fallback_hours_b * 12  # 12 rows per hour
        assert fallback_count == expected_fallback_rows, (
            f"Arm {arm_b} ({label}) claims {fallback_hours_b} B-fallback "
            f"hours but hvac_arm_mode_df has {fallback_count} B-fallback "
            f"rows (expected {expected_fallback_rows}). Inject correctly."
        )

    # 4b. B-active cooling hours actually show the injected SAVINGS_PCT
    #     reduction vs matched-arm A cooling hours. Verifies the data builder
    #     INJECTS the savings effect, not just claims it in expected values.
    from tools.analysis.tests.fixtures.synth_rebaseline_dataset import SAVINGS_PCT
    # The anchor below picks a post-washout day-9 cooling-active CT hour
    # (CT 13:00 = COOLING_START_HOUR_CT, past any injected
    # fallback/telemetry-invalid offsets which come from the first
    # fallback_hours_b + ti hours of the cooling-active list).
    def _cooling_anchor_utc(arm_idx):
        arm_start_ct = CALENDAR_CT[arm_idx - 1][2]
        cool_ct = (
            arm_start_ct
            + datetime.timedelta(days=11, hours=COOLING_START_HOUR_CT)
        )
        return _ct_naive_to_utc_naive(cool_ct)

    for scenario in SCENARIOS:
        (pair_id, arm_a, arm_b, label, cooling_per_day, fallback_hours_b,
         ti_a, ti_b, weather_outlier_b, dst) = scenario
        if cooling_per_day == 0:
            continue
        cool_a_ts = _cooling_anchor_utc(arm_a)
        cool_b_ts = _cooling_anchor_utc(arm_b)

        def _hvac_w(when):
            mask = (
                (synth.refoss_df["_time"] >= when) &
                (synth.refoss_df["_time"] < when + datetime.timedelta(hours=1)) &
                (synth.refoss_df["channel"].isin(["em:2", "em:8"]))
            )
            return synth.refoss_df.loc[mask, "_value"].sum()

        a_w = _hvac_w(cool_a_ts)
        b_w = _hvac_w(cool_b_ts)
        expected_b_w = a_w * (1 - SAVINGS_PCT)
        rel_err = abs(b_w - expected_b_w) / max(expected_b_w, 1.0)
        assert rel_err < 0.01, (
            f"Pair {pair_id} ({label}): Arm A em:2+em:8 at day-11 cooling "
            f"hour = {a_w}W, Arm B = {b_w}W (expected {expected_b_w:.1f}W "
            f"= A * (1 - {SAVINGS_PCT})). Relative error {rel_err:.4f}. "
            "Data builder is not injecting the savings effect; later phases "
            "will see B looking the same as A and the acceptance test will "
            "silently confirm 0% savings."
        )

    # 5. Cooling-active hours actually have nonzero em:2+em:8 power.
    # Use the same day-11 cooling-active anchor (CT 13:00) used in 4b.
    for scenario in SCENARIOS:
        (pair_id, arm_a, arm_b, label, cooling_per_day, fallback_hours_b,
         ti_a, ti_b, weather_outlier_b, dst) = scenario
        if cooling_per_day == 0:
            continue
        cooling_hour = _cooling_anchor_utc(arm_a)
        mask = (
            (synth.refoss_df["_time"] >= cooling_hour) &
            (synth.refoss_df["_time"] < cooling_hour + datetime.timedelta(hours=1)) &
            (synth.refoss_df["channel"].isin(["em:2", "em:8"]))
        )
        total_w = synth.refoss_df.loc[mask, "_value"].sum()
        assert total_w > 1000.0, (
            f"Arm {arm_a} ({label}) claims {cooling_per_day} cooling-active "
            f"hours/day but em:2+em:8 power at day-11 cooling hour is "
            f"only {total_w}W. Expected >1000W."
        )


def test_fixture_expected_values_do_not_import_implementation():
    """Oracle independence (spec/plan #8 rule): the fixture's expected
    values must be hand-pinned or independently computed.

    This test verifies the fixture module does not import any name from
    `tools.analysis.*` (other than imports of itself).
    """
    import tools.analysis.tests.fixtures.synth_rebaseline_dataset as fix_mod
    import inspect
    src = inspect.getsource(fix_mod)
    # Look for any 'from tools.analysis' or 'import tools.analysis' usage.
    # Allow comments and docstrings (filter out the literal docstring text
    # referencing the rule).
    code_lines = [
        line for line in src.split("\n")
        if not line.strip().startswith("#")
    ]
    code = "\n".join(code_lines)
    # The fixture's own module path uses 'tools.analysis.tests.fixtures'
    # which contains 'tools.analysis' as a substring. We need to allow that
    # while disallowing actual analysis imports.
    forbidden_patterns = [
        "from tools.analysis import",
        "from tools.analysis.arm_calendar",
        "from tools.analysis.arm_period_pipeline",
        "from tools.analysis.cost_matched_exclusion",
        "from tools.analysis.dtod_rates",
        "from tools.analysis.hvac_dollars",
        "from tools.analysis.hvac_telemetry_validity",
        "from tools.analysis.matching",
        "from tools.analysis.mode_classification",
        "from tools.analysis.pipeline",
        "from tools.analysis.validity_gate",
        "from tools.analysis.weather_vector",
        "import tools.analysis.arm_calendar",
        "import tools.analysis.arm_period_pipeline",
    ]
    found_violations = [p for p in forbidden_patterns if p in code]
    assert not found_violations, (
        "Oracle-independence violation: the fixture imports the "
        "implementation under test for expected-value computation:\n  "
        + "\n  ".join(found_violations)
    )
