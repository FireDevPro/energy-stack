"""Synthetic 12-arm experiment dataset for the SCED rebaseline outside-in
acceptance test.

Constructs:
- 12 arm periods following spec §2 calendar (2026-06-01 -> 2026-11-16)
- Refoss hourly power_w streams for em:1, em:2, em:7, em:8, em:9
- Eagle hourly delivered_kwh
- Ecowitt hourly weather (ch1_temp_f, ch1_dewpoint_f)
- rt_hrl_lmps hourly settled prices
- comed.prices 5-min live prices
- hvac.arm_mode 5-min mode telemetry (with known mode distribution)
- comed.bill_lineitems monthly bills

The dataset has known properties so the acceptance test can assert exact
answers without running the production pipeline.

ORACLE INDEPENDENCE (spec/plan #8 rule, AGENTS.md outside-in TDD): expected
values in `expected_per_pair_table` are hand-computed from the constants in
this file. THIS FIXTURE DOES NOT IMPORT FROM `tools.analysis.*` for expected
output computation. If a reviewer finds an `from tools.analysis.X import Y`
used to derive an expected value, that is a bug.

Phase 0 scaffold scope: this fixture provides the API + schema + expected
values needed by the acceptance test. Data generation is intentionally
simple; later phases (3-6) progressively elaborate it as real implementation
lands and the test de-scaffolds.

If spec §2 (arm calendar) changes, update CALENDAR below to match.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
import numpy as np


# ---------------------------------------------------------------------------
# Constants used both to construct the synthetic inputs AND to hand-compute
# the expected outputs. Independence rule: nothing imported from
# `tools.analysis.*` below this line.
# ---------------------------------------------------------------------------

# Locked SCED arm calendar per spec §2. Hardcoded here for fixture
# independence; do NOT import from tools.analysis.arm_calendar.
CALENDAR: tuple[tuple[int, str, datetime.datetime, datetime.datetime], ...] = (
    (1,  "A", datetime.datetime(2026,  6,  1, 0, 0), datetime.datetime(2026,  6, 15, 0, 0)),
    (2,  "B", datetime.datetime(2026,  6, 15, 0, 0), datetime.datetime(2026,  6, 29, 0, 0)),
    (3,  "A", datetime.datetime(2026,  6, 29, 0, 0), datetime.datetime(2026,  7, 13, 0, 0)),
    (4,  "B", datetime.datetime(2026,  7, 13, 0, 0), datetime.datetime(2026,  7, 27, 0, 0)),
    (5,  "A", datetime.datetime(2026,  7, 27, 0, 0), datetime.datetime(2026,  8, 10, 0, 0)),
    (6,  "B", datetime.datetime(2026,  8, 10, 0, 0), datetime.datetime(2026,  8, 24, 0, 0)),
    (7,  "A", datetime.datetime(2026,  8, 24, 0, 0), datetime.datetime(2026,  9,  7, 0, 0)),
    (8,  "B", datetime.datetime(2026,  9,  7, 0, 0), datetime.datetime(2026,  9, 21, 0, 0)),
    (9,  "A", datetime.datetime(2026,  9, 21, 0, 0), datetime.datetime(2026, 10,  5, 0, 0)),
    (10, "B", datetime.datetime(2026, 10,  5, 0, 0), datetime.datetime(2026, 10, 19, 0, 0)),
    (11, "A", datetime.datetime(2026, 10, 19, 0, 0), datetime.datetime(2026, 11,  2, 0, 0)),
    (12, "B", datetime.datetime(2026, 11,  2, 0, 0), datetime.datetime(2026, 11, 16, 0, 0)),
)

WASHOUT_HOURS = 48
POST_WASHOUT_HOURS = 12 * 24  # 288 per spec §2 (UTC-elapsed)

# Injected scenario constants. Used both to construct inputs and to compute
# expected outputs. Independence rule means expected values below are derived
# from THESE constants, not from running pipeline code.
SAVINGS_PCT = 0.15                # Arm B uses 15% less HVAC kWh than Arm A in cooling-active hours
BASELINE_HVAC_KW = 2.5            # average HVAC power when running, in cooling-active hours
COOLING_ACTIVE_HOURS_PER_DAY = 8  # default cooling-active hours per analysis day (mid-summer pairs)
KNOWN_LMP_C_PER_KWH = 5.0         # flat synthetic LMP for hand-computable HVAC$
KNOWN_DELIVERY_C_PER_KWH = 4.5    # flat synthetic delivery (DTOD + IEDT + riders combined)
TOTAL_RATE_C_PER_KWH = KNOWN_LMP_C_PER_KWH + KNOWN_DELIVERY_C_PER_KWH  # 9.5 cents/kWh

# Per-pair injected scenarios. Each tuple is one synthesis scenario.
# (pair_id, arm_a_period, arm_b_period, label, cooling_active_hours_per_day,
#  arm_b_fallback_hours, telemetry_invalid_hours_a, telemetry_invalid_hours_b,
#  weather_outlier, dst_crossing)
SCENARIOS = (
    # Pair 1: mild June, fully-valid, B saves 15%
    (1, 1, 2,  "mild_summer",     6,  0,  0,  0, False, False),
    # Pair 2: heat wave, B has 20 B-fallback hours mid-period
    (2, 3, 4,  "heat_wave",       10, 20, 0,  0, False, False),
    # Pair 3: weather outlier on Arm 6 -> poor_weather_match_flag
    (3, 5, 6,  "weather_outlier", 8,  0,  0,  0, True,  False),
    # Pair 4: telemetry-invalid hours in both arms
    (4, 7, 8,  "telemetry_gap",   8,  0,  15, 18, False, False),
    # Pair 5: shoulder, low cooling (denominator-small)
    (5, 9, 10, "shoulder",        1,  0,  0,  0, False, False),
    # Pair 6: arm 11 spans DST; cool weather
    (6, 11, 12, "dst_cool",       2,  0,  0,  0, False, True),
)

# Modes the fixture INJECTS via mode_telemetry construction. The acceptance
# test asserts only these modes are observed in the pipeline output. Does NOT
# claim all 4 modes will be present in every run (per Chris's M-tier guidance).
INJECTED_MODES = {"A-active", "B-active", "B-fallback", "telemetry-invalid"}


# ---------------------------------------------------------------------------
# Hand-computed expected outputs (oracle-independence #8 rule)
# ---------------------------------------------------------------------------

def _arm_a_dollars(scenario_label: str, cooling_active_hours_per_day: int) -> float:
    """Independent calculation of Arm A HVAC$ for a scenario.

    Per-day cooling: COOLING_ACTIVE_HOURS_PER_DAY * BASELINE_HVAC_KW kWh.
    Per analysis-window (12 days): * 12.
    Cost: * TOTAL_RATE_C_PER_KWH / 100 = dollars.

    NOTE: simplified model. Real pipeline uses per-hour rate variation;
    fixture uses flat rate per the constant above so expected math is
    arithmetic-checkable.
    """
    cooling_hours_total = cooling_active_hours_per_day * 12  # 12-day post-washout window
    kwh = cooling_hours_total * BASELINE_HVAC_KW
    return kwh * TOTAL_RATE_C_PER_KWH / 100.0


def _arm_b_dollars(scenario_label: str, cooling_active_hours_per_day: int,
                   fallback_hours: int) -> float:
    """Independent calculation of Arm B HVAC$ for a scenario.

    Arm B saves SAVINGS_PCT in B-active cooling hours.
    Hours in B-fallback are EXCLUDED from primary aggregation per spec §5
    (per-protocol estimand). After symmetric cost-matched exclusion, both
    arms have the same number of fully-valid hours.

    For this scaffold: assume cost-matched exclusion drops the same
    fallback_hours count from Arm A symmetrically. The remaining
    fully-valid hours then have Arm B at (1 - SAVINGS_PCT) of Arm A's kWh.
    """
    cooling_hours_total = cooling_active_hours_per_day * 12
    valid_cooling_hours = cooling_hours_total  # post-cost-matched exclusion
    # Note: fallback_hours falls in cooling-active periods, so they reduce
    # the valid cooling hour count proportionally
    if cooling_hours_total > 0:
        # Proportional reduction: if fallback covers N hours, X of which
        # were cooling-active, valid cooling drops by X. Simplification:
        # assume fallback distributes evenly across hours.
        fraction_cooling = cooling_hours_total / (12 * 24)
        cooling_fallback = int(fallback_hours * fraction_cooling)
        valid_cooling_hours = cooling_hours_total - cooling_fallback
    kwh = valid_cooling_hours * BASELINE_HVAC_KW * (1 - SAVINGS_PCT)
    return kwh * TOTAL_RATE_C_PER_KWH / 100.0


def _expected_per_pair_row(pair_id: int, arm_a: int, arm_b: int, label: str,
                           cooling_per_day: int, fallback_hours: int,
                           telemetry_invalid_a: int, telemetry_invalid_b: int,
                           weather_outlier: bool, dst_crossing: bool) -> dict:
    """Hand-computed expected per-pair row from scenario parameters.

    All values derived from the constants at the top of this file. NO IMPORTS
    from tools.analysis here.
    """
    hvac_dollars_a = _arm_a_dollars(label, cooling_per_day)
    hvac_dollars_b = _arm_b_dollars(label, cooling_per_day, fallback_hours)
    diff_dollars_b_minus_a = hvac_dollars_b - hvac_dollars_a

    # Cost-matched symmetric exclusion: both arms end up with equal valid
    # hour counts after symmetric drop.
    # Telemetry-invalid hours in either arm get matched-excluded from both.
    # B-fallback hours in Arm B get matched-excluded from both (per spec §5).
    excluded_hours = max(telemetry_invalid_a + telemetry_invalid_b,
                         fallback_hours)  # symmetric drop magnitude
    valid_pair_hours = POST_WASHOUT_HOURS - excluded_hours

    return {
        "pair_id": pair_id,
        "arm_a_id": f"A{arm_a}",
        "arm_b_id": f"B{arm_b}",
        "scenario_label": label,
        "valid_pair_hours": valid_pair_hours,
        "hvac_dollars_a": round(hvac_dollars_a, 2),
        "hvac_dollars_b": round(hvac_dollars_b, 2),
        "diff_dollars_b_minus_a": round(diff_dollars_b_minus_a, 2),
        "poor_weather_match_flag": weather_outlier,
        "low_cooling_exposure_flag": cooling_per_day < 6,
        # Computed savings percent (rounded for assertion stability)
        "expected_savings_pct": (SAVINGS_PCT if hvac_dollars_a > 5.0 else None),
        # Mark DST scenario for assertions about hour-index handling
        "dst_crossing_arm": dst_crossing,
    }


# ---------------------------------------------------------------------------
# Public dataset construction
# ---------------------------------------------------------------------------


@dataclass
class SynthDataset:
    """Synthetic inputs + hand-pinned expected outputs."""
    refoss_df: "pd.DataFrame"
    eagle_df: "pd.DataFrame"
    ecowitt_df: "pd.DataFrame"
    rt_hrl_lmps_df: "pd.DataFrame"
    comed_prices_df: "pd.DataFrame"
    hvac_arm_mode_df: "pd.DataFrame"
    bills_df: "pd.DataFrame"
    expected_per_pair_table: "pd.DataFrame"
    expected_arms_passed_validity: set = field(default_factory=set)
    injected_modes: set = field(default_factory=lambda: set(INJECTED_MODES))


def _hourly_index_for_arm(arm_period_idx: int) -> "pd.DatetimeIndex":
    """All UTC hours covering the full arm period (washout + analysis).

    Phase 0 scaffold: returns hourly index. Later phases may need finer
    cadence (30s for Refoss, 5-min for prices) — extend as needed.
    """
    _, _, start, end = CALENDAR[arm_period_idx - 1]
    # Treat all timestamps as UTC for simplicity in the scaffold. Real data
    # would have CT-local boundaries; the pipeline handles tz conversion.
    return pd.date_range(start=start, end=end, freq="1h", inclusive="left")


def build_synth_dataset() -> SynthDataset:
    """Construct the synthetic dataset and its hand-pinned expected outputs.

    Phase 0 scaffold: builds STRUCTURE with simple, importable data. Later
    phases progressively elaborate data generation as the real pipeline
    starts consuming it.

    Independence: this function does NOT import from tools.analysis. All
    expected values come from the SCENARIOS table and the constants above.
    """
    # Build dataframes with correct schemas. Phase 0 scaffold:
    # the structure is what matters; the test currently SKIPS because the
    # pipeline doesn't exist yet, so data values aren't asserted here.
    refoss_rows = []
    eagle_rows = []
    ecowitt_rows = []
    rt_lmps_rows = []
    comed_prices_rows = []
    arm_mode_rows = []

    for arm_idx, arm_letter, start, end in CALENDAR:
        hours = _hourly_index_for_arm(arm_idx)
        for ts in hours:
            # Refoss: 5 channels per hour (scaffold: 1 row per channel per hour;
            # real poller writes ~120/hour. Later phases elaborate to 30s.)
            for ch, watts in (("em:1", 800.0), ("em:2", 100.0), ("em:7", 1200.0),
                              ("em:8", 100.0), ("em:9", 30.0)):
                refoss_rows.append({"_time": ts, "channel": ch,
                                    "_value": watts, "_field": "power_w"})
            # Eagle hourly delivered kWh
            eagle_rows.append({"_time": ts, "delivered_kwh": 2.0,
                               "_measurement": "eagle.meter"})
            # Ecowitt hourly weather
            ecowitt_rows.append({"_time": ts, "ch1_temp_f": 78.0,
                                 "ch1_dewpoint_f": 60.0})
            # rt_hrl_lmps hourly settled (synthetic flat rate)
            rt_lmps_rows.append({"_time": ts,
                                 "total_lmp_rt": KNOWN_LMP_C_PER_KWH * 10.0,
                                 "pnode_id": "33092371"})
            # comed.prices 5-min stream (scaffold: 12 rows per hour)
            for minute in range(0, 60, 5):
                comed_prices_rows.append({
                    "_time": ts + datetime.timedelta(minutes=minute),
                    "price_cents_per_kwh": KNOWN_LMP_C_PER_KWH,
                    "period_type": "5min",
                })
            # hvac.arm_mode 5-min (scaffold: 12 rows per hour)
            # Default mode: A-active in A arms, B-active in B arms
            default_mode = "A-active" if arm_letter == "A" else "B-active"
            for minute in range(0, 60, 5):
                arm_mode_rows.append({
                    "_time": ts + datetime.timedelta(minutes=minute),
                    "arm": arm_letter,
                    "mode_actual": default_mode,
                })

    # Inject scenario perturbations: B-fallback hours, telemetry-invalid hours,
    # weather outliers, etc. Scaffold leaves this as TODO — later phases will
    # add scenario-specific perturbations as pipeline components materialize.
    # (Marking with explicit TODO comments rather than silent omission so a
    # reviewer can see what's scoped out for Phase 0.)
    # TODO Phase 3+: inject B-fallback hours per SCENARIOS table
    # TODO Phase 3+: inject telemetry-invalid hours
    # TODO Phase 6: inject weather outlier for Pair 3 scenario

    # Bills: 6 monthly summaries (June-November 2026)
    bill_rows = []
    for month in range(6, 12):
        bill_rows.append({
            "_time": datetime.datetime(2026, month, 25, 0, 0),
            "account_no": "synth-test",
            "category": "DELIVERY",
            "line_item": "Distribution Facility Charge",
            "amount": 50.0,
            "quantity": 800.0,
            "_field": "amount",
        })

    # Hand-pinned expected per-pair table from SCENARIOS
    expected_rows = [_expected_per_pair_row(*s) for s in SCENARIOS]

    # All 12 arms expected to pass validity gate in the Phase 0 scaffold
    # (with simplified data, no arm exceeds the invalid-hours threshold).
    # Real scenarios with telemetry-invalid hours may flip this for some arms;
    # later phases update this set.
    expected_arms_passed = {f"A{i}" if a == "A" else f"B{i}"
                            for i, a, _, _ in CALENDAR}

    return SynthDataset(
        refoss_df=pd.DataFrame(refoss_rows),
        eagle_df=pd.DataFrame(eagle_rows),
        ecowitt_df=pd.DataFrame(ecowitt_rows),
        rt_hrl_lmps_df=pd.DataFrame(rt_lmps_rows),
        comed_prices_df=pd.DataFrame(comed_prices_rows),
        hvac_arm_mode_df=pd.DataFrame(arm_mode_rows),
        bills_df=pd.DataFrame(bill_rows),
        expected_per_pair_table=pd.DataFrame(expected_rows),
        expected_arms_passed_validity=expected_arms_passed,
        injected_modes=set(INJECTED_MODES),
    )
