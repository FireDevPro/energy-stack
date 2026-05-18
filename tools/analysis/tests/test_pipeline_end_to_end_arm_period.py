"""End-to-end synthetic 12-arm experiment exercising the full
arm-period pipeline.

This is a Phase 3 internal validation -- it uses its OWN synthetic
inputs (aligned to the production-canonical UTC arm boundaries via
zoneinfo), independent of the Phase 0 outside-in acceptance fixture
which gets de-scaffolded in Task 3.15.

Injection: Arm B uses 15% less HVAC power per channel during cooling
hours. The aggregate diff over matched pairs should be ~-15% of A.
"""
from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from tools.analysis.arm_calendar import (
    ARM_CALENDAR,
    ArmPeriod,
    HOURS_PER_ARM,
    post_washout_start,
)
from tools.analysis.arm_period_pipeline import run_full_pipeline


_CT = ZoneInfo("America/Chicago")
_UTC = ZoneInfo("UTC")
SAVINGS_PCT = 0.15


def _post_washout_utc(arm: ArmPeriod) -> datetime.datetime:
    return (
        post_washout_start(arm)
        .replace(tzinfo=_CT)
        .astimezone(_UTC)
        .replace(tzinfo=None)
    )


def _build_one_arm(arm: ArmPeriod, *, scale: float, lmp_per_mwh: float = 35.0):
    """Build full Refoss/Ecowitt/LMP/mode signals for one arm's
    post-washout window.

    Power profile: cooling hours 12-18 CT have nonzero em:2/em:8/em:9;
    other hours have idle em:9 only (no cooling). Apply ``scale`` to the
    cooling-hour power to inject the savings effect on Arm B.
    """
    start_utc = _post_washout_utc(arm)
    refoss_rows = []
    rt_rows = []
    mode_rows = []
    ecowitt_rows = []
    for k in range(HOURS_PER_ARM):
        h_start = start_utc + datetime.timedelta(hours=k)
        ct_hour = h_start.replace(tzinfo=_UTC).astimezone(_CT).hour
        if 12 <= ct_hour < 18:
            em2_base, em8_base, em9_base = 900.0, 600.0, 100.0
        else:
            em2_base, em8_base, em9_base = 0.0, 0.0, 28.0  # idle blower per spec §7
        em2_w = em2_base * scale
        em8_w = em8_base * scale
        em9_w = em9_base if em9_base == 28.0 else em9_base * scale
        # 120 samples per channel per hour at 30s cadence
        for ch, watts in (("em:2", em2_w), ("em:8", em8_w), ("em:9", em9_w)):
            for s in range(120):
                refoss_rows.append({
                    "_time": h_start + datetime.timedelta(seconds=30 * s),
                    "channel": ch, "_value": watts, "_field": "power_w",
                })
        rt_rows.append({"_time": h_start, "total_lmp_rt": lmp_per_mwh})
        for m in range(12):
            mode_rows.append({
                "_time": h_start + datetime.timedelta(minutes=5 * m),
                "arm": arm.arm,
                "mode_actual": "A-active" if arm.arm == "A" else "B-active",
            })
        # Ecowitt: hot during day to trigger high_temp bucket
        temp_f = 88.0 if 12 <= ct_hour < 18 else 70.0
        ecowitt_rows.append({
            "_time": h_start, "ch1_temp_f": temp_f, "ch1_dewpoint_f": 60.0,
        })
    return refoss_rows, rt_rows, mode_rows, ecowitt_rows


def _build_12_arm_dataset(savings_pct: float = SAVINGS_PCT):
    refoss_all, rt_all, mode_all, ecowitt_all = [], [], [], []
    for arm in ARM_CALENDAR:
        scale = 1.0 - savings_pct if arm.arm == "B" else 1.0
        r, rt, m, e = _build_one_arm(arm, scale=scale)
        refoss_all.extend(r)
        rt_all.extend(rt)
        mode_all.extend(m)
        ecowitt_all.extend(e)
    return {
        "refoss_df": pd.DataFrame(refoss_all),
        "rt_hrl_lmps_df": pd.DataFrame(rt_all),
        "hvac_arm_mode_df": pd.DataFrame(mode_all),
        "ecowitt_df": pd.DataFrame(ecowitt_all),
        "eagle_df": pd.DataFrame(columns=["_time", "delivered_kwh"]),
        "comed_prices_df": pd.DataFrame(columns=["_time"]),
        "bills_df": pd.DataFrame(columns=["_time"]),
    }


@pytest.fixture(scope="module")
def twelve_arm_result():
    inputs = _build_12_arm_dataset()
    return run_full_pipeline(**inputs)


def test_all_12_arms_pass_validity_gate(twelve_arm_result):
    expected = {f"{a.arm}{a.index}" for a in ARM_CALENDAR}
    assert twelve_arm_result.arms_passed_validity == expected


def test_six_matched_pairs_produced(twelve_arm_result):
    assert len(twelve_arm_result.per_pair_table) == 6


def test_aggregate_diff_is_negative_at_injected_savings(twelve_arm_result):
    table = twelve_arm_result.per_pair_table
    # Each pair should reflect ~-15% per-pair savings on A's dollars
    pct = table["diff_dollars_b_minus_a"] / table["hvac_dollars_a"]
    mean_pct = float(pct.mean())
    assert mean_pct == pytest.approx(-SAVINGS_PCT, abs=0.01)


def test_high_temp_bucket_populated_when_temps_exceed_85f(twelve_arm_result):
    """Synthetic ecowitt held 88 F during cooling hours -> high-temp
    bucket must contain all 6 pairs."""
    bucket = twelve_arm_result.bucket_summaries["high_temp_exposed_pairs"]
    assert bucket["count"] == 6


def test_mode_distribution_dominated_by_active_modes(twelve_arm_result):
    """All synthetic hours pass telemetry -> only A-active/B-active in
    mode_distribution."""
    md = twelve_arm_result.mode_distribution
    assert md.get("A-active", 0) > 0
    assert md.get("B-active", 0) > 0
    # No fallback / down / invalid in this clean dataset
    assert md.get("B-fallback", 0) == 0
    assert md.get("B-down", 0) == 0
    assert md.get("telemetry-invalid", 0) == 0


def test_valid_pair_hours_equal_to_postwashout_window(twelve_arm_result):
    """Clean signals -> no exclusions -> valid_pair_hours == 288."""
    table = twelve_arm_result.per_pair_table
    assert (table["valid_pair_hours"] == HOURS_PER_ARM).all()
    assert (table["excluded_hours_count"] == 0).all()
