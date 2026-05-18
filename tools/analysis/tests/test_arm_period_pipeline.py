"""Smoke tests for tools.analysis.arm_period_pipeline orchestrator.

Light-weight tests on a minimal 2-arm synthetic dataset. The full
12-arm Phase 0 acceptance test lives in
test_rebaseline_end_to_end_acceptance.py and stays xfail until Task
3.15 lands the de-scaffolded fixture.
"""
from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from tools.analysis.arm_calendar import (
    ARM_CALENDAR,
    ArmPeriod,
    HOURS_PER_ARM,
    post_washout_start,
)
from tools.analysis.arm_period_pipeline import (
    DEFAULT_RATE_SNAPSHOT,
    PipelineResult,
    run_full_pipeline,
)


_CT = ZoneInfo("America/Chicago")
_UTC = ZoneInfo("UTC")


def _post_washout_utc(arm: ArmPeriod) -> datetime.datetime:
    return (
        post_washout_start(arm)
        .replace(tzinfo=_CT)
        .astimezone(_UTC)
        .replace(tzinfo=None)
    )


def _build_arm_signals(arm: ArmPeriod, *, scale: float = 1.0,
                       lmp_per_mwh: float = 50.0) -> dict[str, pd.DataFrame]:
    start = _post_washout_utc(arm)
    refoss_rows = []
    rt_rows = []
    arm_mode_rows = []
    ecowitt_rows = []
    base_w = (("em:2", 900.0), ("em:8", 600.0), ("em:9", 100.0))
    for k in range(HOURS_PER_ARM):
        h_start = start + datetime.timedelta(hours=k)
        for ch, base in base_w:
            watts = base * scale
            for s in range(120):
                refoss_rows.append({
                    "_time": h_start + datetime.timedelta(seconds=30 * s),
                    "channel": ch,
                    "_value": watts,
                    "_field": "power_w",
                })
        rt_rows.append({"_time": h_start, "total_lmp_rt": lmp_per_mwh})
        # one mode telemetry row per 5-min
        for m in range(12):
            arm_mode_rows.append({
                "_time": h_start + datetime.timedelta(minutes=5 * m),
                "arm": arm.arm,
                "mode_actual": "A-active" if arm.arm == "A" else "B-active",
            })
        ecowitt_rows.append({
            "_time": h_start,
            "ch1_temp_f": 80.0,
            "ch1_dewpoint_f": 60.0,
        })
    return {
        "refoss": pd.DataFrame(refoss_rows),
        "rt": pd.DataFrame(rt_rows),
        "mode": pd.DataFrame(arm_mode_rows),
        "ecowitt": pd.DataFrame(ecowitt_rows),
    }


def _build_two_arm_dataset(*, b_savings_pct: float = 0.15):
    """One A arm + one B arm (matched calendar pair 1-2). Returns input
    DataFrames to ``run_full_pipeline``."""
    arms = [ARM_CALENDAR[0], ARM_CALENDAR[1]]  # A1, B2
    refoss_all = []
    rt_all = []
    mode_all = []
    ecowitt_all = []
    for arm in arms:
        scale = 1.0 - b_savings_pct if arm.arm == "B" else 1.0
        sig = _build_arm_signals(arm, scale=scale)
        refoss_all.append(sig["refoss"])
        rt_all.append(sig["rt"])
        mode_all.append(sig["mode"])
        ecowitt_all.append(sig["ecowitt"])
    return {
        "refoss_df": pd.concat(refoss_all, ignore_index=True),
        "rt_hrl_lmps_df": pd.concat(rt_all, ignore_index=True),
        "hvac_arm_mode_df": pd.concat(mode_all, ignore_index=True),
        "ecowitt_df": pd.concat(ecowitt_all, ignore_index=True),
        "eagle_df": pd.DataFrame(columns=["_time", "delivered_kwh"]),
        "comed_prices_df": pd.DataFrame(columns=["_time", "price_cents_per_kwh"]),
        "bills_df": pd.DataFrame(columns=["_time", "amount", "category"]),
        "arm_calendar": arms,
    }


def test_pipeline_returns_pipelineresult_with_required_fields():
    inputs = _build_two_arm_dataset()
    result = run_full_pipeline(**inputs)
    assert isinstance(result, PipelineResult)
    assert isinstance(result.per_pair_table, pd.DataFrame)
    assert isinstance(result.bucket_summaries, dict)
    assert isinstance(result.mode_distribution, dict)
    assert isinstance(result.arms_passed_validity, set)


def test_pipeline_arms_pass_validity_under_clean_signals():
    inputs = _build_two_arm_dataset()
    result = run_full_pipeline(**inputs)
    assert result.arms_passed_validity == {"A1", "B2"}


def test_pipeline_produces_one_pair_row_per_match():
    inputs = _build_two_arm_dataset()
    result = run_full_pipeline(**inputs)
    assert len(result.per_pair_table) == 1
    row = result.per_pair_table.iloc[0]
    assert row["arm_a_id"] == "A1"
    assert row["arm_b_id"] == "B2"


def test_pipeline_savings_show_up_in_diff_dollars():
    inputs = _build_two_arm_dataset(b_savings_pct=0.15)
    result = run_full_pipeline(**inputs)
    row = result.per_pair_table.iloc[0]
    # B used less HVAC -> diff is negative dollars (B - A)
    assert row["diff_dollars_b_minus_a"] < 0
    # ~ -15% of A's dollars
    pct = row["diff_dollars_b_minus_a"] / row["hvac_dollars_a"]
    assert pct == pytest.approx(-0.15, abs=0.005)


def test_pipeline_mode_distribution_contains_active_modes():
    inputs = _build_two_arm_dataset()
    result = run_full_pipeline(**inputs)
    assert result.mode_distribution.get("A-active", 0) == HOURS_PER_ARM
    assert result.mode_distribution.get("B-active", 0) == HOURS_PER_ARM


def test_pipeline_per_pair_row_has_required_columns():
    inputs = _build_two_arm_dataset()
    result = run_full_pipeline(**inputs)
    required = {
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
    missing = required - set(result.per_pair_table.columns)
    assert not missing, f"Missing columns: {missing}"


def test_pipeline_bucket_summaries_have_required_keys():
    inputs = _build_two_arm_dataset()
    result = run_full_pipeline(**inputs)
    required_buckets = {
        "all_valid_pairs",
        "high_cooling_pairs",
        "medium_cooling_pairs",
        "low_cooling_pairs",
        "scarcity_exposed_pairs",
        "5cp_exposed_pairs",
        "high_temp_exposed_pairs",
    }
    assert required_buckets <= set(result.bucket_summaries.keys())


def test_pipeline_bill_reconciliations_empty_when_bill_period_columns_absent():
    """When bills_df lacks bill-period columns, pipeline emits no
    reconciliations rather than guessing the window."""
    inputs = _build_two_arm_dataset()
    result = run_full_pipeline(**inputs)
    assert result.bill_reconciliations == []


def test_pipeline_bill_reconciliations_populated_when_bill_period_columns_present():
    inputs = _build_two_arm_dataset()
    start = datetime.datetime(2026, 6, 3, 0, 0) + datetime.timedelta(hours=5)
    bills_df = pd.DataFrame([{
        "bill_period_start_utc": start,
        "bill_period_end_utc": start + datetime.timedelta(hours=72),
        "variable_dollars": 0.0,
    }])
    # Eagle totalizer matching the bill window
    eagle_rows = []
    cur = 50000.0
    for k in range(72):
        cur += 1.0
        eagle_rows.append({
            "_time": start + datetime.timedelta(hours=k),
            "delivered_kwh": cur,
            "_field": "delivered_kwh",
        })
    inputs["eagle_df"] = pd.DataFrame(eagle_rows)
    inputs["bills_df"] = bills_df
    result = run_full_pipeline(**inputs)
    assert len(result.bill_reconciliations) == 1
    br = result.bill_reconciliations[0]
    assert br.reconstructed_variable_dollars > 0


def test_pipeline_accepts_production_comed_bill_shape():
    """Production ``comed.bill`` (deploy/energy-stack/scripts/comed_influx.py)
    writes ``service_from`` / ``service_to`` (ISO dates, CT-local,
    inclusive) and ``total_due``. The orchestrator must accept that
    schema; otherwise Phase 6 shadow validation silently runs zero
    bill reconciliations.
    """
    inputs = _build_two_arm_dataset()
    # Build a tiny bill that overlaps the two-arm window. Two-arm window
    # spans arms 1 (CT 2026-06-01 -> 2026-06-15) and 2 (2026-06-15 ->
    # 2026-06-29). Use service_from=2026-06-05, service_to=2026-06-07
    # (inclusive), so the UTC bill period is 2026-06-05 05:00 ->
    # 2026-06-08 05:00 UTC.
    bills_df = pd.DataFrame([
        {
            "_time": datetime.datetime(2026, 6, 10, 0, 0),
            "service_from": "2026-06-05",
            "service_to": "2026-06-07",
            "total_due": 234.18,  # variable 200 + fixed 34.18
            "category": "DELIVERY",
            "line_item": "Distribution Facility Charge",
            "amount": 80.0,
        },
        # Second variable line item for the same bill -- must NOT cause
        # a duplicate reconciliation.
        {
            "_time": datetime.datetime(2026, 6, 10, 0, 0),
            "service_from": "2026-06-05",
            "service_to": "2026-06-07",
            "total_due": 234.18,
            "category": "SUPPLY",
            "line_item": "Electricity Supply Charge",
            "amount": 120.0,
        },
        # Spec §4 fixed line items: MUST be excluded from the variable
        # total that reconciliation compares against. If a real bill's
        # Customer Charge / Standard Metering Charge / Capacity Charge
        # were treated as variable, the reconstructed-vs-actual delta
        # would false-flag divergence.
        {
            "_time": datetime.datetime(2026, 6, 10, 0, 0),
            "service_from": "2026-06-05",
            "service_to": "2026-06-07",
            "total_due": 234.18,
            "category": "DELIVERY",
            "line_item": "Customer Charge",
            "amount": 15.35,
        },
        {
            "_time": datetime.datetime(2026, 6, 10, 0, 0),
            "service_from": "2026-06-05",
            "service_to": "2026-06-07",
            "total_due": 234.18,
            "category": "DELIVERY",
            "line_item": "Standard Metering Charge",
            "amount": 3.83,
        },
        {
            "_time": datetime.datetime(2026, 6, 10, 0, 0),
            "service_from": "2026-06-05",
            "service_to": "2026-06-07",
            "total_due": 234.18,
            "category": "DELIVERY",
            "line_item": "Capacity Charge",
            "amount": 15.00,
        },
    ])
    # Eagle totalizer covering the bill window (UTC 06-05 05:00 -> 06-08 05:00)
    eagle_rows = []
    cur = 1000.0
    start_utc = datetime.datetime(2026, 6, 5, 5, 0)
    for k in range(72):
        cur += 1.5
        eagle_rows.append({
            "_time": start_utc + datetime.timedelta(hours=k),
            "delivered_kwh": cur,
            "_field": "delivered_kwh",
        })
    inputs["eagle_df"] = pd.DataFrame(eagle_rows)
    inputs["bills_df"] = bills_df
    result = run_full_pipeline(**inputs)
    # Five line items, one bill period -> one reconciliation.
    assert len(result.bill_reconciliations) == 1
    br = result.bill_reconciliations[0]
    # Window bounds were CT-localised. service_from 06-05 -> UTC 05:00.
    # service_to 06-07 inclusive -> +1 day -> 06-08 CT -> UTC 05:00.
    assert br.bill_period_start_utc == datetime.datetime(2026, 6, 5, 5, 0)
    assert br.bill_period_end_utc == datetime.datetime(2026, 6, 8, 5, 0)
    # Eagle covered every hour of the window.
    assert br.pct_hours_eagle == pytest.approx(100.0)
    # Variable charges = 80 (DFC) + 120 (Supply) = $200.
    # Fixed (Customer, Standard Metering, Capacity) MUST be excluded.
    # If the orchestrator naively used total_due (234.18), this fails.
    assert br.actual_bill_variable_dollars == pytest.approx(200.0)
