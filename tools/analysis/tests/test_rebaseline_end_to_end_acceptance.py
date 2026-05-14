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

import pytest

from tools.analysis.tests.fixtures.synth_rebaseline_dataset import (
    SAVINGS_PCT,
    build_synth_dataset,
)

# The pipeline entry point lands progressively across Phases 1-6.
# Until it exists, this test is SKIPPED. When `run_full_pipeline` is
# importable, the test executes and must pass against the real impl.
try:
    from tools.analysis.arm_period_pipeline import run_full_pipeline  # noqa: F401
    PIPELINE_AVAILABLE = True
except ImportError:
    PIPELINE_AVAILABLE = False


@pytest.mark.skipif(
    not PIPELINE_AVAILABLE,
    reason=(
        "Outside-in: implementation lands across Phases 1-6. Test is "
        "intentionally skipped until tools.analysis.arm_period_pipeline."
        "run_full_pipeline exists. Feature is NOT complete until this "
        "test passes with the real implementation."
    ),
)
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

    # 10. Poor-weather-match flag triggers on the constructed weather-outlier
    #     scenario (Pair 3 in SCENARIOS), and does NOT exclude that pair from
    #     primary (per spec §6: flag-only, not exclude).
    poor_match_flagged = actual["poor_weather_match_flag"].sum()
    expected_poor_match = expected["poor_weather_match_flag"].sum()
    assert poor_match_flagged == expected_poor_match, (
        f"poor_weather_match_flag count mismatch: "
        f"actual={poor_match_flagged} expected={expected_poor_match}"
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
