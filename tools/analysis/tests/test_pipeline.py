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


# -- Stage 2 rule applicators (test-first; rules 2/4/6 first) --------------


def test_rule6_pjm_never_gates():
    out = pipeline.rule6_pjm_apply()
    assert out.passes is True
    assert out.exclusion_reason is None


def test_rule4_forecast_missing_issuance_flagged_but_passes():
    out = pipeline.rule4_forecast_apply(missing_issuance_count=1)
    assert out.passes is True
    assert out.contributes.get("forecast_substitutions") == 1


def test_rule4_forecast_no_substitutions_when_zero():
    out = pipeline.rule4_forecast_apply(missing_issuance_count=0)
    assert out.passes is True
    assert out.contributes.get("forecast_substitutions") == 0


def test_rule2_comfortnet_no_downtime_all_days_eligible():
    # 7 days, all with 0 minutes of comfortnet downtime
    daily_downtime_minutes = [0, 0, 0, 0, 0, 0, 0]
    out = pipeline.rule2_comfortnet_apply(daily_downtime_minutes)
    assert out.passes is True
    assert out.contributes["o6_ineligible_days"] == 0
    assert out.contributes["o6_eligible_day_count"] == 7


def test_rule2_comfortnet_day_with_over_30min_downtime_flagged():
    # day 3 has 35 min downtime; should be flagged O6-ineligible
    daily_downtime_minutes = [0, 5, 0, 35, 0, 0, 10]
    out = pipeline.rule2_comfortnet_apply(daily_downtime_minutes)
    assert out.passes is True  # rule 2 never gates the week
    assert out.contributes["o6_ineligible_days"] == 1
    assert out.contributes["o6_eligible_day_count"] == 6


def test_rule2_comfortnet_boundary_exactly_30min_is_eligible():
    # spec: ">30 minutes" downtime → ineligible. 30 exactly is eligible.
    out = pipeline.rule2_comfortnet_apply([0, 30, 0, 0, 0, 0, 0])
    assert out.contributes["o6_ineligible_days"] == 0


# -- Rule 10: arm-transition verification ----------------------------------


def test_rule10_transition_verified_by_arm_b_action_within_6h():
    switch = datetime.datetime(2026, 6, 8, 5, 0)  # Mon 00:00 CT
    actions = [
        {"timestamp": datetime.datetime(2026, 6, 8, 9, 0),
         "arm": "B", "action": "HOT_PRE_COOL", "dry_run": False},
    ]
    out = pipeline.rule10_transition_apply(
        switch_ts=switch, intended_arm="B", action_events=actions,
    )
    assert out.passes is True
    assert out.exclusion_reason is None


def test_rule10_transition_verified_by_arm_a_dry_run_action():
    switch = datetime.datetime(2026, 6, 15, 5, 0)
    actions = [
        {"timestamp": datetime.datetime(2026, 6, 15, 8, 30),
         "arm": "A", "action": "NORMAL_PRE_COOL", "dry_run": True},
    ]
    out = pipeline.rule10_transition_apply(
        switch_ts=switch, intended_arm="A", action_events=actions,
    )
    assert out.passes is True


def test_rule10_transition_fails_when_no_action_before_deadline():
    switch = datetime.datetime(2026, 6, 8, 5, 0)
    actions = [
        # Outside the 6h window
        {"timestamp": datetime.datetime(2026, 6, 8, 12, 0),
         "arm": "B", "action": "HOT_PRE_COOL", "dry_run": False},
    ]
    out = pipeline.rule10_transition_apply(
        switch_ts=switch, intended_arm="B", action_events=actions,
    )
    assert out.passes is False
    assert out.exclusion_reason == "arm_transition_unverified"


def test_rule10_transition_fails_when_arm_tag_mismatches():
    switch = datetime.datetime(2026, 6, 8, 5, 0)
    actions = [
        # Right window, but the action is tagged with the OLD arm
        {"timestamp": datetime.datetime(2026, 6, 8, 9, 0),
         "arm": "A", "action": "HOT_PRE_COOL", "dry_run": True},
    ]
    out = pipeline.rule10_transition_apply(
        switch_ts=switch, intended_arm="B", action_events=actions,
    )
    assert out.passes is False


def test_rule10_transition_arm_b_active_actions_only():
    # Arm B requires non-dry-run; an Arm B dry_run row doesn't verify.
    switch = datetime.datetime(2026, 6, 8, 5, 0)
    actions = [
        {"timestamp": datetime.datetime(2026, 6, 8, 9, 0),
         "arm": "B", "action": "HOT_PRE_COOL", "dry_run": True},
    ]
    out = pipeline.rule10_transition_apply(
        switch_ts=switch, intended_arm="B", action_events=actions,
    )
    assert out.passes is False


def test_rule10_transition_only_control_relevant_actions_count():
    # SLEEP, COAST, etc. are not control-relevant; only PRE_COOL variants.
    switch = datetime.datetime(2026, 6, 8, 5, 0)
    actions = [
        {"timestamp": datetime.datetime(2026, 6, 8, 9, 0),
         "arm": "B", "action": "COAST", "dry_run": False},
    ]
    out = pipeline.rule10_transition_apply(
        switch_ts=switch, intended_arm="B", action_events=actions,
    )
    assert out.passes is False


# -- Rule 9: manual setpoint overrides --------------------------------------


def test_rule9_overrides_apply_no_overrides_is_pass():
    out = pipeline.rule9_overrides_apply(
        week_start_ct=datetime.date(2026, 6, 8), overrides=[],
    )
    assert out.passes is True
    assert out.contributes["override_operational_count"] == 0
    assert out.contributes["override_vacation_days"] == 0


def test_rule9_overrides_apply_operational_counted():
    overrides = [
        {"category": "operational",
         "start_ts": datetime.datetime(2026, 6, 9, 13, 0),
         "end_ts": datetime.datetime(2026, 6, 9, 15, 0),
         "setpoint_f": 79.0},
        {"category": "operational",
         "start_ts": datetime.datetime(2026, 6, 11, 16, 0),
         "end_ts": datetime.datetime(2026, 6, 11, 18, 0),
         "setpoint_f": 78.0},
    ]
    out = pipeline.rule9_overrides_apply(
        week_start_ct=datetime.date(2026, 6, 8), overrides=overrides,
    )
    assert out.passes is True
    assert out.contributes["override_operational_count"] == 2
    assert out.contributes["override_vacation_days"] == 0


def test_rule9_overrides_apply_vacation_marks_days():
    # 3-day vacation (Tue, Wed, Thu)
    overrides = [
        {"category": "vacation",
         "start_ts": datetime.datetime(2026, 6, 9, 8, 0),
         "end_ts": datetime.datetime(2026, 6, 11, 17, 0),
         "setpoint_f": 82.0},
    ]
    out = pipeline.rule9_overrides_apply(
        week_start_ct=datetime.date(2026, 6, 8), overrides=overrides,
    )
    assert out.passes is True  # rule9 itself never gates; orchestrator does <5 check
    assert out.contributes["override_vacation_days"] == 3
    excluded_days = {entry["date"] for entry in out.intervals_log}
    assert excluded_days == {
        datetime.date(2026, 6, 9),
        datetime.date(2026, 6, 10),
        datetime.date(2026, 6, 11),
    }


def test_rule9_overrides_apply_reclassifies_long_high_setpoint():
    # Mistagged operational override (24h at 82F) gets reclassified to vacation
    # per the existing rule9_classify_override logic.
    overrides = [
        {"category": "operational",
         "start_ts": datetime.datetime(2026, 6, 9, 6, 0),
         "end_ts": datetime.datetime(2026, 6, 10, 6, 0),
         "setpoint_f": 82.0},
    ]
    out = pipeline.rule9_overrides_apply(
        week_start_ct=datetime.date(2026, 6, 8), overrides=overrides,
    )
    assert out.contributes["override_operational_count"] == 0
    assert out.contributes["override_vacation_days"] >= 1


def test_rule9_overrides_apply_overlapping_vacations_dedup():
    # Two vacation overrides covering the same day count as one excluded day.
    overrides = [
        {"category": "vacation",
         "start_ts": datetime.datetime(2026, 6, 9, 8, 0),
         "end_ts": datetime.datetime(2026, 6, 9, 20, 0),
         "setpoint_f": 82.0},
        {"category": "vacation",
         "start_ts": datetime.datetime(2026, 6, 9, 22, 0),
         "end_ts": datetime.datetime(2026, 6, 10, 6, 0),
         "setpoint_f": 82.0},
    ]
    out = pipeline.rule9_overrides_apply(
        week_start_ct=datetime.date(2026, 6, 8), overrides=overrides,
    )
    excluded_days = {entry["date"] for entry in out.intervals_log}
    assert excluded_days == {datetime.date(2026, 6, 9), datetime.date(2026, 6, 10)}


# -- Rule 3: ComEd RTP price feed -------------------------------------------


def _hour(observed_prints: int) -> dict:
    return {"observed_prints": observed_prints}


def test_rule3_price_all_hours_observed():
    hours = [_hour(12) for _ in range(168)]
    out = pipeline.rule3_price_apply(hours)
    assert out.passes is True
    assert out.contributes["imputed_price_hours_pct"] == 0.0
    assert out.contributes["imputed_price_hours_flagged"] is False


def test_rule3_price_low_imputation_passes_but_unflagged():
    # 5% imputed → still passes, NOT flagged (>5% threshold is strict)
    hours = [_hour(0)] * 8 + [_hour(12)] * 160  # 8/168 ≈ 4.76%
    out = pipeline.rule3_price_apply(hours)
    assert out.passes is True
    assert out.contributes["imputed_price_hours_flagged"] is False


def test_rule3_price_above_5pct_flagged_but_passes():
    # 10% imputed → passes, but flagged
    hours = [_hour(0)] * 17 + [_hour(12)] * 151
    out = pipeline.rule3_price_apply(hours)
    assert out.passes is True
    assert out.contributes["imputed_price_hours_flagged"] is True


def test_rule3_price_above_20pct_excludes_week():
    # 30% imputed → excluded
    hours = [_hour(0)] * 50 + [_hour(12)] * 118
    out = pipeline.rule3_price_apply(hours)
    assert out.passes is False
    assert out.exclusion_reason == "price_imputation_too_high"


def test_rule3_price_observed_threshold_is_six_inclusive():
    # observed_prints == 6 → observed; observed_prints == 5 → imputed
    hours = [_hour(6) for _ in range(84)] + [_hour(5) for _ in range(84)]  # 50% imputed
    out = pipeline.rule3_price_apply(hours)
    assert out.contributes["imputed_price_hours_pct"] == pytest.approx(0.5)
    assert out.passes is False


# -- Rule 7: scheduler service outages --------------------------------------


def _outage(start_hour: int, start_min: int, duration_min: int,
            day: int = 9) -> tuple:
    start = datetime.datetime(2026, 6, day, start_hour, start_min)
    return (start, start + datetime.timedelta(minutes=duration_min))


def test_rule7_no_outages_passes():
    out = pipeline.rule7_scheduler_apply(outages=[], control_relevant_windows=[])
    assert out.passes is True
    assert out.contributes["scheduler_downtime_min"] == 0


def test_rule7_short_single_outage_passes():
    out = pipeline.rule7_scheduler_apply(
        outages=[_outage(10, 0, 30)], control_relevant_windows=[],
    )
    assert out.passes is True
    assert out.contributes["scheduler_downtime_min"] == 30


def test_rule7_long_single_outage_excludes():
    # 65-min single outage → over the 60-min single-outage threshold
    out = pipeline.rule7_scheduler_apply(
        outages=[_outage(10, 0, 65)], control_relevant_windows=[],
    )
    assert out.passes is False
    assert out.exclusion_reason == "scheduler_outage_single_too_long"


def test_rule7_cumulative_downtime_excludes_above_1pct():
    # 12 × 10-min outages = 120 min > 100 min (1% of 168h)
    outages = [_outage(10 + i, 0, 10) for i in range(12)]
    out = pipeline.rule7_scheduler_apply(outages=outages, control_relevant_windows=[])
    assert out.passes is False
    assert out.exclusion_reason == "scheduler_downtime_too_high"


def test_rule7_outage_overlapping_control_window_excludes():
    # 10-min outage during 04:00-06:00 pre-cool window
    out = pipeline.rule7_scheduler_apply(
        outages=[_outage(4, 30, 10)],
        control_relevant_windows=[
            (datetime.datetime(2026, 6, 9, 4, 0),
             datetime.datetime(2026, 6, 9, 6, 0)),
        ],
    )
    assert out.passes is False
    assert out.exclusion_reason == "scheduler_outage_in_control_window"


def test_rule7_outage_outside_control_window_passes():
    # 10-min outage at 02:00, control window is 04:00-06:00
    out = pipeline.rule7_scheduler_apply(
        outages=[_outage(2, 0, 10)],
        control_relevant_windows=[
            (datetime.datetime(2026, 6, 9, 4, 0),
             datetime.datetime(2026, 6, 9, 6, 0)),
        ],
    )
    assert out.passes is True


def test_detect_scheduler_outages_from_write_gaps():
    # Generate scheduler writes every minute for 10 minutes, then a 7-min
    # gap, then resume. detect_scheduler_outages should find ONE outage.
    state_ts = [
        datetime.datetime(2026, 6, 9, 10, m) for m in range(10)
    ] + [
        datetime.datetime(2026, 6, 9, 10, 17 + m) for m in range(10)
    ]
    action_ts = list(state_ts)
    outages = pipeline.detect_scheduler_outages(state_ts, action_ts)
    assert len(outages) == 1
    start, end = outages[0]
    assert (end - start).total_seconds() >= 5 * 60  # ≥5 min per spec


def test_detect_scheduler_outages_short_gap_under_5min_not_an_outage():
    # 4-min gap is below the 5-min outage-detection threshold.
    state_ts = [datetime.datetime(2026, 6, 9, 10, m) for m in [0, 1, 2, 3, 4, 5, 9, 10]]
    outages = pipeline.detect_scheduler_outages(state_ts, list(state_ts))
    assert outages == []


# -- Rule 5: Ecowitt CDD basis ----------------------------------------------


def test_rule5_ecowitt_no_gaps_no_flags():
    out = pipeline.rule5_ecowitt_apply(daily_both_missing_hours=[0] * 7)
    assert out.passes is True
    assert out.contributes["ecowitt_substituted_days"] == 0
    assert out.contributes["ecowitt_dropped_days_for_cdd"] == 0


def test_rule5_ecowitt_2h_gap_below_substitution_threshold():
    # Exactly 2h is NOT >2, so neither substituted nor dropped.
    out = pipeline.rule5_ecowitt_apply(daily_both_missing_hours=[2, 0, 0, 0, 0, 0, 0])
    assert out.contributes["ecowitt_substituted_days"] == 0
    assert out.contributes["ecowitt_dropped_days_for_cdd"] == 0


def test_rule5_ecowitt_3h_gap_flagged_substituted():
    out = pipeline.rule5_ecowitt_apply(daily_both_missing_hours=[3, 0, 0, 0, 0, 0, 0])
    assert out.contributes["ecowitt_substituted_days"] == 1
    assert out.contributes["ecowitt_dropped_days_for_cdd"] == 0


def test_rule5_ecowitt_7h_gap_dropped_not_substituted():
    # >6h supersedes the substitution band; the day is dropped, not substituted.
    out = pipeline.rule5_ecowitt_apply(daily_both_missing_hours=[7, 0, 0, 0, 0, 0, 0])
    assert out.contributes["ecowitt_substituted_days"] == 0
    assert out.contributes["ecowitt_dropped_days_for_cdd"] == 1


def test_rule5_ecowitt_never_gates_even_with_all_days_dropped():
    out = pipeline.rule5_ecowitt_apply(daily_both_missing_hours=[24] * 7)
    assert out.passes is True
    assert out.contributes["ecowitt_dropped_days_for_cdd"] == 7


def test_rule5_ecowitt_mixed_thresholds():
    # day 0: 1h (none), day 1: 4h (substituted), day 2: 7h (dropped),
    # day 3: 2h (none, exact threshold), day 4: 6h (substituted, exact threshold for drop),
    # day 5-6: 0h
    out = pipeline.rule5_ecowitt_apply(
        daily_both_missing_hours=[1, 4, 7, 2, 6, 0, 0],
    )
    assert out.contributes["ecowitt_substituted_days"] == 2  # days 1, 4
    assert out.contributes["ecowitt_dropped_days_for_cdd"] == 1  # day 2


# -- Rule 1: Refoss EM16P 4-tier imputation ---------------------------------


def test_impute_refoss_gap_tier1_linear_interpolation():
    # 3-min gap; before-gap 0.05 kWh/min, after-gap 0.07 kWh/min.
    # Linear interp midpoint: 0.06 kWh/min × 3 min = 0.18 kWh
    iv = {"gap_minutes": 3, "tier": 1,
          "pre_kwh_per_min": 0.05, "post_kwh_per_min": 0.07}
    out = pipeline.impute_refoss_gap(iv)
    assert out["imputed_kwh"] == pytest.approx(0.18, abs=1e-4)


def test_impute_refoss_gap_tier2_history_median_scaled_by_mains():
    # 20-min gap; same-hour median from prior 14d is 1.5 kW; mains ratio 1.2.
    # Expected: 1.5 kW × (20/60)h × 1.2 = 0.6 kWh
    iv = {"gap_minutes": 20, "tier": 2, "history_median_kw": 1.5}
    out = pipeline.impute_refoss_gap(iv, mains_history_ratio=1.2)
    assert out["imputed_kwh"] == pytest.approx(0.6, abs=1e-4)


def test_impute_refoss_gap_tier3_comfortnet_derived():
    # 60-min gap; cool 100%, heat 0%, blower 4500 cfm (full nameplate).
    # comfortnet_kw uses 0..100 percentage scale.
    # comfortnet_kw(100, 0, 4500) = 1.0*4.6 + 0 + (4500/4500)*0.6 = 5.2 kW; 1h → 5.2 kWh
    iv = {"gap_minutes": 60, "tier": 3}
    cn = {"cool_actual_pct": 100, "heat_actual_pct": 0, "blower_cfm": 4500.0}
    out = pipeline.impute_refoss_gap(iv, comfortnet_sample=cn)
    assert out["imputed_kwh"] == pytest.approx(5.2, abs=1e-3)


def test_impute_refoss_gap_tier4_no_imputation():
    iv = {"gap_minutes": 200, "tier": 4}
    out = pipeline.impute_refoss_gap(iv)
    assert out["imputed_kwh"] == 0.0


def test_rule1_refoss_apply_no_gaps_passes():
    out = pipeline.rule1_refoss_apply(weekly_hvac_kwh=100.0, imputed_intervals=[])
    assert out.passes is True
    assert out.contributes["imputed_hvac_kwh_pct"] == 0.0


def test_rule1_refoss_apply_below_10pct_cap_passes():
    intervals = [
        {"tier": 1, "imputed_kwh": 3.0,
         "start_ts": datetime.datetime(2026, 6, 9, 10, 0),
         "end_ts": datetime.datetime(2026, 6, 9, 10, 3)},
        {"tier": 2, "imputed_kwh": 4.0,
         "start_ts": datetime.datetime(2026, 6, 10, 14, 0),
         "end_ts": datetime.datetime(2026, 6, 10, 14, 20)},
    ]
    out = pipeline.rule1_refoss_apply(
        weekly_hvac_kwh=100.0, imputed_intervals=intervals,
    )
    assert out.passes is True
    assert out.contributes["imputed_hvac_kwh_pct"] == pytest.approx(0.07)


def test_rule1_refoss_apply_at_10pct_cap_excludes():
    # Spec: ≥10% of total weekly HVAC kWh → week dropped
    intervals = [{"tier": 1, "imputed_kwh": 10.0,
                  "start_ts": datetime.datetime(2026, 6, 9, 10, 0),
                  "end_ts": datetime.datetime(2026, 6, 9, 10, 4)}]
    out = pipeline.rule1_refoss_apply(
        weekly_hvac_kwh=100.0, imputed_intervals=intervals,
    )
    assert out.passes is False
    assert out.exclusion_reason == "refoss_imputation_too_high"
    assert out.contributes["imputed_hvac_kwh_pct"] == pytest.approx(0.10)


def test_rule1_refoss_apply_tier4_intervals_recorded_in_log():
    # Tier 4 contributes 0 imputed kWh but is logged for the day-flag.
    iv = {"tier": 4, "imputed_kwh": 0.0,
          "start_ts": datetime.datetime(2026, 6, 9, 10, 0),
          "end_ts": datetime.datetime(2026, 6, 9, 13, 30)}
    out = pipeline.rule1_refoss_apply(
        weekly_hvac_kwh=100.0, imputed_intervals=[iv],
    )
    assert out.passes is True
    assert len(out.intervals_log) == 1
    assert out.intervals_log[0]["tier"] == 4


def test_rule1_refoss_apply_zero_weekly_kwh_no_division_error():
    # A week with no HVAC usage at all (cooling-irrelevant) shouldn't crash.
    out = pipeline.rule1_refoss_apply(weekly_hvac_kwh=0.0, imputed_intervals=[])
    assert out.passes is True
    assert out.contributes["imputed_hvac_kwh_pct"] == 0.0


# -- Rule 8: Pi outages + <5 qualifying-days combiner ----------------------


def test_rule8_pi_apply_all_seven_days_qualify():
    out = pipeline.rule8_pi_apply(
        week_start_ct=datetime.date(2026, 6, 8),
        rule1_tier4_days=set(),
        rule7_outage_days=set(),
        rule9_vacation_days=set(),
    )
    assert out.passes is True
    assert out.contributes["qualifying_days"] == 7


def test_rule8_pi_apply_two_excluded_meets_five_threshold():
    out = pipeline.rule8_pi_apply(
        week_start_ct=datetime.date(2026, 6, 8),
        rule1_tier4_days={datetime.date(2026, 6, 9)},
        rule7_outage_days={datetime.date(2026, 6, 10)},
        rule9_vacation_days=set(),
    )
    assert out.passes is True
    assert out.contributes["qualifying_days"] == 5


def test_rule8_pi_apply_three_excluded_falls_below_five():
    out = pipeline.rule8_pi_apply(
        week_start_ct=datetime.date(2026, 6, 8),
        rule1_tier4_days={datetime.date(2026, 6, 9)},
        rule7_outage_days={datetime.date(2026, 6, 10)},
        rule9_vacation_days={datetime.date(2026, 6, 11)},
    )
    assert out.passes is False
    assert out.exclusion_reason == "insufficient_qualifying_days"
    assert out.contributes["qualifying_days"] == 4


def test_rule8_pi_apply_dedups_overlapping_pi_outage_days():
    # Pi outage manifests in both rule 1 + rule 7 simultaneously by spec —
    # only counts once.
    same_day = datetime.date(2026, 6, 9)
    out = pipeline.rule8_pi_apply(
        week_start_ct=datetime.date(2026, 6, 8),
        rule1_tier4_days={same_day, datetime.date(2026, 6, 10)},
        rule7_outage_days={same_day},
        rule9_vacation_days=set(),
    )
    assert out.passes is True
    assert out.contributes["qualifying_days"] == 5
    assert out.contributes["excluded_days"] == 2


def test_rule8_pi_apply_ignores_days_outside_the_week():
    # An exclusion-day timestamp that falls outside the 7-day window
    # shouldn't reduce the qualifying count.
    out = pipeline.rule8_pi_apply(
        week_start_ct=datetime.date(2026, 6, 8),
        rule1_tier4_days={datetime.date(2026, 6, 1)},  # prior week
        rule7_outage_days=set(),
        rule9_vacation_days=set(),
    )
    assert out.passes is True
    assert out.contributes["qualifying_days"] == 7


# -- Stage 2 orchestrator: combine rule results into a per-week row --------


def _happy_week_inputs(arm: str = "B") -> dict:
    """Helper: minimal week-input dict that passes every rule."""
    week = datetime.date(2026, 6, 8)
    switch = datetime.datetime(2026, 6, 8, 5, 0)
    return {
        "week_start_ct": week,
        "arm": arm,
        "weekly_hvac_kwh": 100.0,
        "refoss_intervals": [],  # no gaps
        "hourly_prices": [_hour(12) for _ in range(168)],
        "daily_comfortnet_downtime_minutes": [0] * 7,
        "daily_ecowitt_both_missing_hours": [0] * 7,
        "scheduler_outages": [],
        "control_relevant_windows": [],
        "overrides": [],
        "missing_forecast_issuances": 0,
        "arm_transition": {
            "switch_ts": switch,
            "intended_arm": arm,
            "action_events": [
                {"timestamp": switch + datetime.timedelta(hours=3),
                 "arm": arm, "action": "HOT_PRE_COOL",
                 "dry_run": (arm == "A")},
            ],
        },
    }


def test_stage2_apply_week_happy_path_qualifies():
    out = pipeline._apply_rules_for_week(_happy_week_inputs())
    assert out.row["qualifying"] is True
    assert out.row["exclusion_reason"] is None
    assert out.row["arm"] == "B"
    assert out.row["week_start_ct"] == "2026-06-08"


def test_stage2_apply_week_excludes_on_rule1_imputation_cap():
    inputs = _happy_week_inputs()
    inputs["refoss_intervals"] = [
        {"tier": 1, "imputed_kwh": 15.0,
         "start_ts": datetime.datetime(2026, 6, 9, 10, 0),
         "end_ts": datetime.datetime(2026, 6, 9, 10, 4)},
    ]
    out = pipeline._apply_rules_for_week(inputs)
    assert out.row["qualifying"] is False
    assert out.row["exclusion_reason"] == "refoss_imputation_too_high"


def test_stage2_apply_week_excludes_on_unverified_arm_transition():
    inputs = _happy_week_inputs()
    inputs["arm_transition"]["action_events"] = []  # no verification action
    out = pipeline._apply_rules_for_week(inputs)
    assert out.row["qualifying"] is False
    assert out.row["exclusion_reason"] == "arm_transition_unverified"


def test_stage2_apply_week_exclusion_priority_follows_spec_order():
    # Rule 1 and rule 10 both fail → rule 1's reason wins (spec order)
    inputs = _happy_week_inputs()
    inputs["refoss_intervals"] = [
        {"tier": 1, "imputed_kwh": 15.0,
         "start_ts": datetime.datetime(2026, 6, 9, 10, 0),
         "end_ts": datetime.datetime(2026, 6, 9, 10, 4)},
    ]
    inputs["arm_transition"]["action_events"] = []
    out = pipeline._apply_rules_for_week(inputs)
    assert out.row["exclusion_reason"] == "refoss_imputation_too_high"


def test_stage2_apply_week_vacation_excludes_via_rule8():
    inputs = _happy_week_inputs()
    inputs["overrides"] = [
        {"category": "vacation",
         "start_ts": datetime.datetime(2026, 6, 9, 8, 0),
         "end_ts": datetime.datetime(2026, 6, 11, 17, 0),  # Tue-Thu = 3 days
         "setpoint_f": 82.0},
        {"category": "vacation",
         "start_ts": datetime.datetime(2026, 6, 12, 8, 0),
         "end_ts": datetime.datetime(2026, 6, 12, 17, 0),  # Fri = 1 more day
         "setpoint_f": 82.0},
    ]
    out = pipeline._apply_rules_for_week(inputs)
    # 7 - 4 = 3 qualifying days < 5
    assert out.row["qualifying"] is False
    assert out.row["exclusion_reason"] == "insufficient_qualifying_days"


def test_stage2_apply_week_merges_rule_contributions_into_row():
    inputs = _happy_week_inputs()
    inputs["overrides"] = [
        {"category": "operational",
         "start_ts": datetime.datetime(2026, 6, 9, 13, 0),
         "end_ts": datetime.datetime(2026, 6, 9, 15, 0),
         "setpoint_f": 79.0},
    ]
    inputs["daily_comfortnet_downtime_minutes"] = [0, 35, 0, 0, 0, 0, 0]
    inputs["missing_forecast_issuances"] = 2
    out = pipeline._apply_rules_for_week(inputs)
    assert out.row["qualifying"] is True
    assert out.row["override_operational_count"] == 1
    assert out.row["override_vacation_days"] == 0
    assert out.row["o6_ineligible_days"] == 1
    assert out.row["forecast_substitutions"] == 2


def test_stage2_apply_week_tier4_refoss_intervals_recorded():
    inputs = _happy_week_inputs()
    inputs["refoss_intervals"] = [
        {"tier": 4, "imputed_kwh": 0.0,
         "start_ts": datetime.datetime(2026, 6, 10, 10, 0),
         "end_ts": datetime.datetime(2026, 6, 10, 14, 0)},
    ]
    out = pipeline._apply_rules_for_week(inputs)
    assert any(iv.get("tier") == 4 for iv in out.imputed_intervals)


def test_stage2_apply_week_scheduler_outages_recorded():
    inputs = _happy_week_inputs()
    outage = (
        datetime.datetime(2026, 6, 10, 3, 0),
        datetime.datetime(2026, 6, 10, 3, 30),
    )
    inputs["scheduler_outages"] = [outage]
    out = pipeline._apply_rules_for_week(inputs)
    assert any(o.get("kind") == "scheduler_outage" for o in out.outages)


# -- Stage 2 parquet-I/O wrapper -------------------------------------------


def test_stage2_quality_writes_locked_csv_schema_for_no_weeks(tmp_path):
    """When no weeks are configured, stage2_quality still writes the
    three output files with locked headers (existing skeleton behavior).
    """
    stage1 = tmp_path / "stage1"
    stage1.mkdir()
    pipeline.stage2_quality(stage1, tmp_path)
    stage2 = tmp_path / "stage2"
    assert (stage2 / "qualifying_weeks.csv").exists()
    assert (stage2 / "imputed_intervals.csv").exists()
    assert (stage2 / "outages.csv").exists()
    # Header rows match the locked schema
    with open(stage2 / "qualifying_weeks.csv") as f:
        header = next(csv.reader(f))
    assert header == [
        "week_start_ct", "arm", "qualifying", "exclusion_reason",
        "imputed_hvac_kwh_pct", "imputed_price_hours_pct",
        "override_operational_count", "override_vacation_days",
    ]


# -- Stage 3: DTOD rates synced with production scheduler ------------------


def test_dtod_periods_synced_with_precool_module():
    """The Stage 3 dollar-cost computation uses the ComEd DTOD delivery
    rate, which is also used live by the scheduler in
    deploy/energy-stack/hvac-scheduler/precool.py. The two must stay
    aligned; this test loads precool by file path and asserts the
    rate schedule is identical.
    """
    import importlib.util
    from pathlib import Path
    precool_path = (
        Path(__file__).resolve().parents[3]
        / "deploy" / "energy-stack" / "hvac-scheduler" / "precool.py"
    )
    spec = importlib.util.spec_from_file_location("precool", precool_path)
    precool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(precool)
    assert pipeline.DTOD_PERIODS_CT == precool.DTOD_PERIODS_CT
    # Helpers agree for every hour
    for h in range(24):
        assert (
            pipeline.dtod_delivery_rate_for_hour_ct(h)
            == precool.dtod_delivery_rate_for_hour(h)
        )


def test_dtod_delivery_rate_for_hour_ct_known_buckets():
    # Locked rate schedule per CUB March 2026 fact sheet
    assert pipeline.dtod_delivery_rate_for_hour_ct(7) == 4.009    # Morning
    assert pipeline.dtod_delivery_rate_for_hour_ct(15) == 10.712  # Mid-Day Peak
    assert pipeline.dtod_delivery_rate_for_hour_ct(20) == 3.747   # Evening
    assert pipeline.dtod_delivery_rate_for_hour_ct(3) == 2.984    # Overnight


def test_dtod_delivery_rate_for_hour_ct_rejects_out_of_range():
    with pytest.raises(ValueError):
        pipeline.dtod_delivery_rate_for_hour_ct(24)
    with pytest.raises(ValueError):
        pipeline.dtod_delivery_rate_for_hour_ct(-1)


# -- Stage 3: weekly_cdd ----------------------------------------------------


def test_weekly_cdd_no_cooling_days():
    # All daily T_avg ≤ 65°F → 0 CDD contribution
    assert pipeline.weekly_cdd([60.0, 62.0, 65.0, 64.0, 50.0, 55.0, 65.0]) == 0.0


def test_weekly_cdd_uniform_hot_week():
    # 7 days at 75°F → 7 × (75-65) = 70 CDD
    assert pipeline.weekly_cdd([75.0] * 7) == 70.0


def test_weekly_cdd_mixed_temps():
    # 70, 80, 65, 90, 60, 75, 85 → 5+15+0+25+0+10+20 = 75
    assert pipeline.weekly_cdd([70, 80, 65, 90, 60, 75, 85]) == 75.0


def test_weekly_cdd_handles_short_week_after_day_drops():
    # Spec rule 5: some days may be dropped from CDD numerator/denominator.
    # Sum is over whatever days are present.
    assert pipeline.weekly_cdd([75.0, 80.0]) == (10.0 + 15.0)


# -- Stage 3: weekly_mean_enthalpy_btu_lb ----------------------------------


def test_weekly_mean_enthalpy_btu_lb_constant_hours_equals_point_value():
    # Three identical hours → mean equals the point enthalpy
    rec = {"temp_f": 85.0, "dewpoint_f": 70.0}
    expected = pipeline.enthalpy_btu_per_lb(85.0, 70.0)
    out = pipeline.weekly_mean_enthalpy_btu_lb([rec, rec, rec])
    assert out == pytest.approx(expected)


def test_weekly_mean_enthalpy_btu_lb_averages_across_hours():
    # Two distinct hourly enthalpies — output is the mean
    r1 = {"temp_f": 85.0, "dewpoint_f": 70.0}
    r2 = {"temp_f": 75.0, "dewpoint_f": 60.0}
    expected = (
        pipeline.enthalpy_btu_per_lb(85.0, 70.0)
        + pipeline.enthalpy_btu_per_lb(75.0, 60.0)
    ) / 2.0
    out = pipeline.weekly_mean_enthalpy_btu_lb([r1, r2])
    assert out == pytest.approx(expected)


def test_weekly_mean_enthalpy_btu_lb_uses_per_record_pressure_when_given():
    # If pressure_inhg is on the record, it overrides the default 29.92
    rec_high = {"temp_f": 85.0, "dewpoint_f": 70.0, "pressure_inhg": 30.5}
    rec_default = {"temp_f": 85.0, "dewpoint_f": 70.0}
    h_high = pipeline.weekly_mean_enthalpy_btu_lb([rec_high])
    h_default = pipeline.weekly_mean_enthalpy_btu_lb([rec_default])
    # Higher pressure → slightly lower humidity ratio → slightly lower enthalpy
    assert h_high < h_default


def test_weekly_mean_enthalpy_btu_lb_empty_returns_zero():
    assert pipeline.weekly_mean_enthalpy_btu_lb([]) == 0.0


# -- Stage 3: weekly_dollars_per_cdd ---------------------------------------


def test_weekly_dollars_per_cdd_single_hour_mid_day_peak():
    # 1 kWh consumed at hour=15 (Mid-Day Peak: 10.712¢/kWh delivery)
    # supply = 10.0¢/kWh; total = 20.712¢ = $0.20712
    # CDD = 1.0 → $/CDD = 0.20712
    hourly = [{"hour_of_day_ct": 15, "hvac_kwh": 1.0, "supply_c_per_kwh": 10.0}]
    out = pipeline.weekly_dollars_per_cdd(hourly_records=hourly, weekly_cdd=1.0)
    assert out == pytest.approx(0.20712, abs=1e-5)


def test_weekly_dollars_per_cdd_zero_cdd_returns_zero_no_division():
    hourly = [{"hour_of_day_ct": 15, "hvac_kwh": 1.0, "supply_c_per_kwh": 10.0}]
    out = pipeline.weekly_dollars_per_cdd(hourly_records=hourly, weekly_cdd=0.0)
    assert out == 0.0


def test_weekly_dollars_per_cdd_mixed_periods():
    # Hour 4 (Overnight, 2.984¢) and hour 15 (Mid-Day Peak, 10.712¢)
    # supply 5¢ both hours; 1 kWh each.
    # cents = (5 + 2.984)*1 + (5 + 10.712)*1 = 7.984 + 15.712 = 23.696¢ = $0.23696
    # CDD = 2 → $/CDD = 0.11848
    hourly = [
        {"hour_of_day_ct": 4, "hvac_kwh": 1.0, "supply_c_per_kwh": 5.0},
        {"hour_of_day_ct": 15, "hvac_kwh": 1.0, "supply_c_per_kwh": 5.0},
    ]
    out = pipeline.weekly_dollars_per_cdd(hourly_records=hourly, weekly_cdd=2.0)
    assert out == pytest.approx(0.11848, abs=1e-5)


def test_weekly_dollars_per_cdd_higher_in_peak_hours_than_overnight():
    # Same kWh consumed in peak vs overnight → peak hour costs more per CDD
    peak = [{"hour_of_day_ct": 15, "hvac_kwh": 1.0, "supply_c_per_kwh": 5.0}]
    overnight = [{"hour_of_day_ct": 4, "hvac_kwh": 1.0, "supply_c_per_kwh": 5.0}]
    peak_cost = pipeline.weekly_dollars_per_cdd(peak, weekly_cdd=1.0)
    overnight_cost = pipeline.weekly_dollars_per_cdd(overnight, weekly_cdd=1.0)
    assert peak_cost > overnight_cost


# -- Stage 3: _compute_weekly_row orchestrator -----------------------------


def _happy_stage3_inputs(arm: str = "A", qualifies: bool = True) -> dict:
    """Synthetic happy-path Stage 3 input: hot week, no missing data."""
    return {
        "week_start_ct": datetime.date(2026, 6, 8),
        "arm": arm,
        "qualifies": qualifies,
        "daily_avg_temps_f": [75.0] * 7,
        "hourly_hvac_records": [
            {"hour_of_day_ct": h % 24, "hvac_kwh": 0.5, "supply_c_per_kwh": 8.0}
            for h in range(168)
        ],
        "hourly_mains_records": [
            {"hour_of_day_ct": h % 24, "hvac_kwh": 1.5, "supply_c_per_kwh": 8.0}
            for h in range(168)
        ],
        "hourly_weather": [
            {"temp_f": 85.0, "dewpoint_f": 70.0, "pressure_inhg": 29.92,
             "solar_wm2": 100.0, "wind_mph": 5.0}
            for _ in range(168)
        ],
    }


def test_stage3_compute_weekly_row_populates_all_locked_columns():
    out = pipeline._compute_weekly_row(_happy_stage3_inputs())
    expected_columns = (
        "week_start_ct", "arm", "qualifies",
        "o1_dollars_per_cdd", "o3_peak_hvac_kw",
        "o4_dollars_per_cdd_whole_home",
    ) + pipeline.WEATHER_VECTOR_COMPONENTS
    for col in expected_columns:
        assert col in out, f"missing column: {col}"
    assert out["week_start_ct"] == "2026-06-08"
    assert out["arm"] == "A"
    assert out["qualifies"] is True
    # 7 days × (75-65) = 70 CDD
    assert out["weekly_cdd"] == 70.0
    # Every hour is 0.5 kWh → 0.5 kW peak
    assert out["o3_peak_hvac_kw"] == 0.5
    # Max temp/dewpoint from constant 85/70 hours
    assert out["max_temp_f"] == 85.0
    assert out["max_dewpoint_f"] == 70.0
    # Mean wind/total solar
    assert out["mean_wind_mph"] == pytest.approx(5.0)
    assert out["total_solar_wh_m2"] == 168 * 100.0


def test_stage3_compute_weekly_row_qualifies_comes_from_stage2_verbatim():
    """Boundary rule: Stage 3 ECHOES Stage 2's qualifying decision; it
    does not re-derive quality logic from Stage 1 inputs. Test that a
    pristine Stage 1 dataset still gets qualifies=False when Stage 2
    marked the week excluded."""
    inputs = _happy_stage3_inputs(qualifies=False)
    out = pipeline._compute_weekly_row(inputs)
    assert out["qualifies"] is False


def test_stage3_compute_weekly_row_zero_cdd_week_handles_division():
    # Cool week with all daily T_avg below 65 → CDD = 0; $/CDD must be 0
    # (not NaN/inf) so the row writes cleanly.
    inputs = _happy_stage3_inputs()
    inputs["daily_avg_temps_f"] = [60.0] * 7
    out = pipeline._compute_weekly_row(inputs)
    assert out["weekly_cdd"] == 0.0
    assert out["o1_dollars_per_cdd"] == 0.0
    assert out["o4_dollars_per_cdd_whole_home"] == 0.0


def test_stage3_compute_weekly_row_mains_o4_uses_mains_records_not_hvac():
    # Use distinct hvac vs mains hourly kWh values; verify each routes
    # to the right column.
    inputs = _happy_stage3_inputs()
    for r in inputs["hourly_hvac_records"]:
        r["hvac_kwh"] = 0.1
    for r in inputs["hourly_mains_records"]:
        r["hvac_kwh"] = 1.0  # mains 10x hvac
    out = pipeline._compute_weekly_row(inputs)
    # O4 should be ~10x O1 because mains uses 1.0 kWh vs hvac 0.1 kWh
    assert out["o4_dollars_per_cdd_whole_home"] == pytest.approx(
        10.0 * out["o1_dollars_per_cdd"], rel=1e-9,
    )


def test_stage3_compute_weekly_row_o3_uses_max_not_mean():
    # Most hours 0.3 kWh, one hour 2.5 kWh → O3 = 2.5 kW (the peak)
    inputs = _happy_stage3_inputs()
    for r in inputs["hourly_hvac_records"]:
        r["hvac_kwh"] = 0.3
    inputs["hourly_hvac_records"][50]["hvac_kwh"] = 2.5
    out = pipeline._compute_weekly_row(inputs)
    assert out["o3_peak_hvac_kw"] == 2.5


# -- Stage 3: stage3_weekly orchestrator (parquet + Stage 2 CSV reader) ----


def _write_stage2_qualifying_weeks(stage2_dir: Path, rows: list[dict]) -> None:
    stage2_dir.mkdir(parents=True, exist_ok=True)
    with open(stage2_dir / "qualifying_weeks.csv", "w", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=list(pipeline.QUALIFYING_WEEKS_LOCKED_COLUMNS),
        )
        w.writeheader()
        for r in rows:
            w.writerow(r)


def test_stage3_weekly_writes_locked_schema_header(tmp_path):
    """Existing schema test, preserved: weekly.csv has the locked columns."""
    pipeline.stage3_weekly(
        stage1_dir=tmp_path, stage2_dir=tmp_path, out_dir=tmp_path,
    )
    weekly = tmp_path / "stage3" / "weekly.csv"
    assert weekly.exists()
    with open(weekly) as f:
        header = next(csv.reader(f))
        data_rows = list(csv.reader(f))
    assert header == [
        "week_start_ct", "arm", "qualifies",
        "o1_dollars_per_cdd", "o3_peak_hvac_kw",
        "o4_dollars_per_cdd_whole_home",
        *pipeline.WEATHER_VECTOR_COMPONENTS,
    ]
    # Header-only when no Stage 2 / Stage 1 input is wired
    assert data_rows == []


def test_stage3_weekly_reads_stage2_qualifying_csv(monkeypatch, tmp_path):
    """Stage 3 reads Stage 2's qualifying decision via the
    qualifying_weeks.csv contract — does not re-derive it.
    """
    # Stage 2 says week 2026-06-08 / A passes, but 2026-06-15 / B is excluded.
    _write_stage2_qualifying_weeks(tmp_path / "stage2", [
        {"week_start_ct": "2026-06-08", "arm": "A", "qualifying": "True",
         "exclusion_reason": "",
         "imputed_hvac_kwh_pct": 0.0, "imputed_price_hours_pct": 0.0,
         "override_operational_count": 0, "override_vacation_days": 0},
        {"week_start_ct": "2026-06-15", "arm": "B", "qualifying": "False",
         "exclusion_reason": "refoss_imputation_too_high",
         "imputed_hvac_kwh_pct": 0.12, "imputed_price_hours_pct": 0.0,
         "override_operational_count": 0, "override_vacation_days": 0},
    ])

    def fake_loader(stage1_dir, week_start_ct, arm):
        # Same fixture for both weeks; differing qualifies bool below.
        base = _happy_stage3_inputs(arm=arm)
        base["week_start_ct"] = week_start_ct
        # Note: qualifies is OVERWRITTEN by stage3_weekly with the
        # Stage 2 CSV value, NOT this fixture's value.
        base["qualifies"] = True  # try to lie about it — should be ignored
        return base

    monkeypatch.setattr(
        pipeline, "_load_stage3_inputs_for_week", fake_loader,
    )

    stage1 = tmp_path / "stage1"
    stage1.mkdir()
    pipeline.stage3_weekly(stage1, tmp_path, tmp_path)

    with open(tmp_path / "stage3" / "weekly.csv") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    week_a = next(r for r in rows if r["arm"] == "A")
    week_b = next(r for r in rows if r["arm"] == "B")
    assert week_a["qualifies"] == "True"
    # Stage 3 took Stage 2's False even though the fixture said True
    assert week_b["qualifies"] == "False"


def test_stage3_weekly_skips_weeks_without_stage1_inputs(monkeypatch, tmp_path):
    """If the Stage 1 loader returns None for a week (no parquet data),
    Stage 3 emits a row with qualifies and zero outcome values rather
    than crashing.
    """
    _write_stage2_qualifying_weeks(tmp_path / "stage2", [
        {"week_start_ct": "2026-06-08", "arm": "A", "qualifying": "True",
         "exclusion_reason": "",
         "imputed_hvac_kwh_pct": 0.0, "imputed_price_hours_pct": 0.0,
         "override_operational_count": 0, "override_vacation_days": 0},
    ])
    monkeypatch.setattr(
        pipeline, "_load_stage3_inputs_for_week",
        lambda *args, **kwargs: None,
    )
    stage1 = tmp_path / "stage1"
    stage1.mkdir()
    pipeline.stage3_weekly(stage1, tmp_path, tmp_path)
    with open(tmp_path / "stage3" / "weekly.csv") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["week_start_ct"] == "2026-06-08"
    assert rows[0]["qualifies"] == "True"
    # Outcome values default to 0 when stage1 input is absent
    assert float(rows[0]["o1_dollars_per_cdd"]) == 0.0


# -- Stage 6: O2 Layer 1 (observed ACustCPL difference) --------------------


def _pjm_5cp_2025():
    return [
        datetime.datetime(2025, 6, 24, 17),
        datetime.datetime(2025, 7, 22, 17),
        datetime.datetime(2025, 7, 23, 18),
        datetime.datetime(2025, 8, 12, 17),
        datetime.datetime(2025, 8, 26, 18),
    ]


def test_compute_a_cust_cpl_kw_uniform_load():
    peaks = _pjm_5cp_2025()
    hourly_kw = {p: 3.0 for p in peaks}
    assert pipeline.compute_a_cust_cpl_kw(peaks, hourly_kw) == 3.0


def test_compute_a_cust_cpl_kw_skips_missing_hours():
    # Two of five peaks have no household data → mean over the three present
    peaks = _pjm_5cp_2025()
    hourly_kw = {peaks[0]: 2.0, peaks[1]: 4.0, peaks[2]: 6.0}
    assert pipeline.compute_a_cust_cpl_kw(peaks, hourly_kw) == 4.0


def test_compute_a_cust_cpl_kw_returns_zero_when_no_data():
    out = pipeline.compute_a_cust_cpl_kw(_pjm_5cp_2025(), {})
    assert out == 0.0


def test_compute_a_cust_cpl_kw_empty_peak_list_is_zero():
    assert pipeline.compute_a_cust_cpl_kw([], {}) == 0.0


def test_compute_layer1_arm_delta_arm_b_lower_means_negative_delta():
    # Arm A: 3 peaks at 4 kW → mean 4. Arm B: 2 peaks at 3 kW → mean 3.
    # delta = -1 kW. At $10.13567/kW-mo × 5 months → -$50.67835.
    peaks = _pjm_5cp_2025()
    peaks_a, peaks_b = peaks[:3], peaks[3:]
    hourly_kw = {p: 4.0 for p in peaks_a} | {p: 3.0 for p in peaks_b}
    out = pipeline.compute_layer1_arm_delta(
        pjm_peak_hours_by_arm={"A": peaks_a, "B": peaks_b},
        hourly_mains_kw=hourly_kw,
        capacity_rate_dollars_per_kw_month=10.13567,
    )
    assert out["a_cust_cpl_kw_arm_a"] == 4.0
    assert out["a_cust_cpl_kw_arm_b"] == 3.0
    assert out["n_peaks_arm_a"] == 3
    assert out["n_peaks_arm_b"] == 2
    assert out["delta_kw"] == -1.0
    assert out["delta_dollars_total"] == pytest.approx(-50.67835, abs=1e-4)


def test_compute_layer1_arm_delta_zero_when_arms_identical():
    peaks = _pjm_5cp_2025()
    hourly_kw = {p: 3.5 for p in peaks}
    out = pipeline.compute_layer1_arm_delta(
        pjm_peak_hours_by_arm={"A": peaks[:3], "B": peaks[3:]},
        hourly_mains_kw=hourly_kw,
        capacity_rate_dollars_per_kw_month=10.13567,
    )
    assert out["delta_kw"] == 0.0
    assert out["delta_dollars_total"] == 0.0


# -- Stage 6: O2 Layer 2 (CPLC scenarios) ----------------------------------


def _layer2_constants():
    from tools.o2_capacity_reconstruction.reconstruct import TariffConstants
    return TariffConstants(
        year=2025,
        comed_npl_mw=20736.0,
        a_comed_cpl_mw=19138.22,
        portfolio_sum_mw=1500.0,
        rate_dollars_per_kw_month=10.13567,
        is_placeholder=False,
    )


def test_compute_layer2_scenarios_emits_three_named_scenarios():
    peaks_pjm = _pjm_5cp_2025()
    hourly_kw = {p: 3.0 for p in peaks_pjm}
    out = pipeline.compute_layer2_scenarios(
        pjm_peak_hours_by_arm={"A": peaks_pjm[:3], "B": peaks_pjm[3:]},
        comed_peak_hours_by_arm={"A": peaks_pjm[:3], "B": peaks_pjm[3:]},
        hourly_mains_kw=hourly_kw,
        tariff_constants=_layer2_constants(),
    )
    names = {r["scenario"] for r in out}
    assert names == {"low", "anchor_2021", "high"}


def test_compute_layer2_scenarios_branch1_collapses_when_pl_le_cpl():
    # ComEd peaks coincide with PJM peaks; identical kW → branch 1
    peaks = _pjm_5cp_2025()
    hourly_kw = {p: 3.0 for p in peaks}
    out = pipeline.compute_layer2_scenarios(
        pjm_peak_hours_by_arm={"A": peaks, "B": []},
        comed_peak_hours_by_arm={"A": peaks, "B": []},
        hourly_mains_kw=hourly_kw,
        tariff_constants=_layer2_constants(),
    )
    cplc_arm_a = {r["cplc_kw_arm_a"] for r in out}
    # Branch 1 collapses CPLC = ACustCPL regardless of scenario
    assert cplc_arm_a == {3.0}


def test_compute_layer2_scenarios_branch2_widens_with_smaller_denominator():
    # ACustPL > ACustCPL → branch 2; smaller portfolio_sum → bigger adjustment
    peaks_pjm = _pjm_5cp_2025()
    peaks_comed = [datetime.datetime(2025, 7, 20, 15)]
    hourly_kw = {p: 2.0 for p in peaks_pjm} | {peaks_comed[0]: 6.0}
    out = pipeline.compute_layer2_scenarios(
        pjm_peak_hours_by_arm={"A": peaks_pjm, "B": []},
        comed_peak_hours_by_arm={"A": peaks_comed, "B": []},
        hourly_mains_kw=hourly_kw,
        tariff_constants=_layer2_constants(),
    )
    rows = {r["scenario"]: r["cplc_kw_arm_a"] for r in out}
    # low denominator (1500) → biggest adjustment; high (3000) → smallest
    assert rows["low"] > rows["anchor_2021"] > rows["high"]


# -- Stage 6: O2 Layer 3 (bill reconciliation) -----------------------------


def test_compute_layer3_bill_capacity_dollars_sums_may_through_sep_only():
    bills = [
        {"year": 2026, "month": 4, "capacity_charge_dollars": 5.0},   # excluded
        {"year": 2026, "month": 5, "capacity_charge_dollars": 30.0},  # May
        {"year": 2026, "month": 6, "capacity_charge_dollars": 50.0},  # Jun
        {"year": 2026, "month": 9, "capacity_charge_dollars": 35.0},  # Sep
        {"year": 2026, "month": 10, "capacity_charge_dollars": 5.0},  # excluded
        {"year": 2027, "month": 6, "capacity_charge_dollars": 99.0},  # wrong year
    ]
    out = pipeline.compute_layer3_bill_capacity_dollars(bills, year_y_plus_1=2026)
    assert out["year"] == 2026
    assert out["months_summed"] == 3
    assert out["total_capacity_charge_dollars"] == pytest.approx(115.0)


def test_compute_layer3_bill_capacity_dollars_handles_missing_months():
    bills = [{"year": 2026, "month": 7, "capacity_charge_dollars": 42.0}]
    out = pipeline.compute_layer3_bill_capacity_dollars(bills, year_y_plus_1=2026)
    assert out["months_summed"] == 1
    assert out["total_capacity_charge_dollars"] == 42.0


# -- Stage 6: Detector accuracy --------------------------------------------


def test_compute_detector_accuracy_perfect_detector():
    peaks = _pjm_5cp_2025()
    other_hours = [datetime.datetime(2025, 7, 10, h) for h in range(24)]
    all_summer = peaks + other_hours
    state = {h: ("holding" if h in peaks else "off") for h in all_summer}
    out = pipeline.compute_detector_accuracy(
        published_5cp_hours=peaks,
        summer_hours=all_summer,
        fivecp_state_by_hour=state,
    )
    assert out["tp"] == 5
    assert out["fn"] == 0
    assert out["fp"] == 0
    assert out["tn"] == 24
    assert out["tpr"] == 1.0
    assert out["fpr"] == 0.0


def test_compute_detector_accuracy_one_miss_and_one_false_alarm():
    peaks = _pjm_5cp_2025()
    other_hours = [datetime.datetime(2025, 7, 10, h) for h in range(24)]
    all_summer = peaks + other_hours
    state = {h: "off" for h in all_summer}
    for p in peaks[1:]:  # miss peaks[0]
        state[p] = "holding"
    state[datetime.datetime(2025, 7, 10, 14)] = "holding"  # false alarm
    out = pipeline.compute_detector_accuracy(
        published_5cp_hours=peaks,
        summer_hours=all_summer,
        fivecp_state_by_hour=state,
    )
    assert out["tp"] == 4
    assert out["fn"] == 1
    assert out["fp"] == 1
    assert out["tpr"] == pytest.approx(0.8)
    assert out["fnr"] == pytest.approx(0.2)


def test_compute_detector_accuracy_empty_truth_returns_zero_rates():
    summer = [datetime.datetime(2025, 7, 10, h) for h in range(3)]
    state = {h: "off" for h in summer}
    out = pipeline.compute_detector_accuracy(
        published_5cp_hours=[],
        summer_hours=summer,
        fivecp_state_by_hour=state,
    )
    assert out["tpr"] == 0.0
    assert out["fnr"] == 0.0
    assert out["tn"] == 3


# -- Stage 6: stage6_o2 orchestrator ---------------------------------------


def test_stage6_o2_writes_four_csvs_header_only_when_no_inputs(tmp_path):
    stage1 = tmp_path / "stage1"
    stage1.mkdir()
    pipeline.stage6_o2(stage1, tmp_path)
    stage6 = tmp_path / "stage6"
    for name in ("o2_layer1.csv", "o2_layer2.csv",
                 "o2_layer3.csv", "detector_accuracy.csv"):
        assert (stage6 / name).exists()


def test_stage6_o2_with_synthetic_loader_populates_layer1_layer2(
    monkeypatch, tmp_path,
):
    peaks_pjm = _pjm_5cp_2025()
    peaks_comed = peaks_pjm  # identical for this fixture; branch 1 case
    hourly_kw = {p: 3.0 for p in peaks_pjm}
    fake_inputs = {
        "pjm_peak_hours_by_arm": {"A": peaks_pjm[:3], "B": peaks_pjm[3:]},
        "comed_peak_hours_by_arm": {"A": peaks_comed[:3], "B": peaks_comed[3:]},
        "hourly_mains_kw": hourly_kw,
        "tariff_constants": _layer2_constants(),
        "summer_year": 2025,
        "comed_bills": [
            {"year": 2026, "month": 6, "capacity_charge_dollars": 20.0},
        ],
        "summer_hours": peaks_pjm,
        "fivecp_state_by_hour": {h: "holding" for h in peaks_pjm},
    }
    monkeypatch.setattr(
        pipeline, "_load_stage6_inputs", lambda _: fake_inputs,
    )
    stage1 = tmp_path / "stage1"
    stage1.mkdir()
    pipeline.stage6_o2(stage1, tmp_path)
    # Layer 1: one row
    with open(tmp_path / "stage6" / "o2_layer1.csv") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert float(rows[0]["a_cust_cpl_kw_arm_a"]) == 3.0
    assert float(rows[0]["a_cust_cpl_kw_arm_b"]) == 3.0
    # Layer 2: three rows (one per scenario)
    with open(tmp_path / "stage6" / "o2_layer2.csv") as f:
        rows2 = list(csv.DictReader(f))
    assert {r["scenario"] for r in rows2} == {"low", "anchor_2021", "high"}
    # Layer 3: one row summing bills
    with open(tmp_path / "stage6" / "o2_layer3.csv") as f:
        rows3 = list(csv.DictReader(f))
    assert len(rows3) == 1
    assert float(rows3[0]["total_capacity_charge_dollars"]) == 20.0
    # Detector accuracy: one row
    with open(tmp_path / "stage6" / "detector_accuracy.csv") as f:
        rows4 = list(csv.DictReader(f))
    assert len(rows4) == 1
    assert int(rows4[0]["tp"]) == 5


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
