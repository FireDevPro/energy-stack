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

SCENARIO INJECTION (P1 reviewer fix): each scenario in SCENARIOS is actually
injected into the generated data — not just claimed in the expected table.
- B-fallback hours: written as `mode_actual = "B-fallback"` in hvac_arm_mode_df
- Telemetry-invalid hours: Refoss rows OMITTED for those hours (validity check
  treats missing-hour data as invalid per spec §7)
- Weather-outlier arm: ch1_temp_f shifted +20°F vs other arms
- Cooling-active hours: em:2/em:8/em:9 set to non-zero only during
  cooling_active_hours_per_day window (default 12:00-12+N:00 CT)

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
# Tuple = (arm_period_idx_1based, arm_letter, start_utc, end_utc).
# Phase 0 scaffold treats timestamps as UTC throughout for simplicity.
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
ARM_TOTAL_HOURS = 14 * 24      # 336 — full arm including washout

# Injected scenario constants.
SAVINGS_PCT = 0.15
BASELINE_HVAC_KW = 2.5             # em:2 + em:8 sum when AC running
BLOWER_W_WHEN_RUNNING = 100.0      # em:9 when AC active
KNOWN_LMP_C_PER_KWH = 5.0
KNOWN_DELIVERY_C_PER_KWH = 4.5
TOTAL_RATE_C_PER_KWH = KNOWN_LMP_C_PER_KWH + KNOWN_DELIVERY_C_PER_KWH  # 9.5

# Outlier-arm temperature shift, applied to ch1_temp_f for the entire arm.
WEATHER_OUTLIER_SHIFT_F = 20.0
BASE_TEMP_F = 78.0
BASE_DEWPOINT_F = 60.0

# Per-pair injected scenarios. Each row = one synthesis scenario.
# (pair_id, arm_a, arm_b, label, cooling_per_day, fallback_hours_b,
#  telemetry_invalid_a, telemetry_invalid_b, weather_outlier_on_b, dst_crossing)
SCENARIOS: tuple = (
    (1, 1, 2,  "mild_summer",     6,  0,  0,  0, False, False),
    (2, 3, 4,  "heat_wave",       10, 20, 0,  0, False, False),
    (3, 5, 6,  "weather_outlier", 8,  0,  0,  0, True,  False),
    (4, 7, 8,  "telemetry_gap",   8,  0,  15, 18, False, False),
    (5, 9, 10, "shoulder",        0,  0,  0,  0, False, False),
    (6, 11, 12, "dst_cool",       2,  0,  0,  0, False, True),
)

# Modes the fixture INJECTS via mode_telemetry construction. Acceptance test
# asserts these modes are observed in the pipeline output. Does NOT require
# all 4 modes to be present universally (per Chris's M-tier guidance).
INJECTED_MODES = {"A-active", "B-active", "B-fallback", "telemetry-invalid"}

COOLING_START_HOUR_CT = 12  # cooling-active window starts at 12:00 CT


# ---------------------------------------------------------------------------
# Per-arm scenario lookup
# ---------------------------------------------------------------------------


def _build_arm_state() -> dict[int, dict]:
    """Returns {arm_period_idx: state_dict}.

    state_dict keys:
    - role: "A" or "B"
    - pair_id, label
    - cooling_per_day
    - fallback_hour_set_in_arm:   set of hour-offsets-from-arm-start where mode=B-fallback (only for B arms)
    - telemetry_invalid_hour_set: set of hour-offsets-from-arm-start where Refoss data is OMITTED
    - weather_outlier: bool (only meaningful on the arm with the outlier shift)
    """
    state = {}
    for (pair_id, arm_a, arm_b, label, cooling_per_day, fallback_hours_b,
         ti_a, ti_b, weather_outlier_b, dst) in SCENARIOS:
        # cooling-active hour offsets within the post-washout window of an arm
        cooling_offsets = _cooling_active_offsets(cooling_per_day)
        # Available cooling-active hours we can "spend" on fallback / invalid
        # injection. Pull from the start of the list deterministically.
        fallback_set_b = set(cooling_offsets[:fallback_hours_b])
        # Telemetry-invalid hours follow after fallback in the same list to
        # avoid overlap. Each arm has its own injected indices.
        ti_set_a = set(cooling_offsets[fallback_hours_b:fallback_hours_b + ti_a])
        ti_set_b = set(cooling_offsets[fallback_hours_b:fallback_hours_b + ti_b])
        state[arm_a] = {
            "role": "A",
            "pair_id": pair_id,
            "label": label,
            "cooling_per_day": cooling_per_day,
            "fallback_hour_set": set(),
            "telemetry_invalid_hour_set": ti_set_a,
            "weather_outlier": False,
            "dst_crossing": dst,
        }
        state[arm_b] = {
            "role": "B",
            "pair_id": pair_id,
            "label": label,
            "cooling_per_day": cooling_per_day,
            "fallback_hour_set": fallback_set_b,
            "telemetry_invalid_hour_set": ti_set_b,
            "weather_outlier": weather_outlier_b,
            "dst_crossing": dst,
        }
    return state


def _cooling_active_offsets(cooling_per_day: int) -> list[int]:
    """Hour-offsets-from-arm-start where cooling is active.

    Cooling window: 12:00-(12+cooling_per_day):00 each day, post-washout only.
    Arm starts Monday 00:00; washout = first 48h (Mon 00:00 -> Wed 00:00).
    Post-washout days = days 2..13 (Wed through Sun next-week before Mon switch).
    """
    if cooling_per_day <= 0:
        return []
    offsets = []
    # Day 0 = Mon (washout day 1)
    # Day 1 = Tue (washout day 2)
    # Day 2 = Wed (post-washout day 1) - first cooling-active day
    # Day 13 = Sun (post-washout day 12) - last cooling-active day
    for day in range(2, 14):  # 12 post-washout days
        for hour in range(COOLING_START_HOUR_CT,
                          COOLING_START_HOUR_CT + cooling_per_day):
            offsets.append(day * 24 + hour)
    return offsets


# ---------------------------------------------------------------------------
# Hand-computed expected outputs (oracle-independence #8 rule)
# ---------------------------------------------------------------------------


def _expected_per_pair_row(scenario_tuple: tuple) -> dict:
    """Hand-computed expected per-pair row from scenario parameters."""
    (pair_id, arm_a, arm_b, label, cooling_per_day, fallback_hours_b,
     ti_a, ti_b, weather_outlier, dst) = scenario_tuple

    cooling_hours_total = cooling_per_day * 12  # 12 post-washout days

    # Cost-matched symmetric exclusion drops:
    # - B-fallback hours from B + symmetric matched-drops from A
    # - A's telemetry-invalid hours + symmetric matched-drops from B
    # - B's telemetry-invalid hours + symmetric matched-drops from A
    # In the fixture, injected sets are DISJOINT within an arm (fallback then
    # invalid pulled from sequential cooling-active offsets), so total
    # cooling-active hours dropped per arm = (fallback_hours_b + ti_a + ti_b)
    # clamped to cooling_hours_total.
    cooling_hours_dropped = min(cooling_hours_total,
                                fallback_hours_b + ti_a + ti_b)
    effective_cooling = cooling_hours_total - cooling_hours_dropped
    kwh_per_arm = effective_cooling * BASELINE_HVAC_KW
    hvac_dollars_a = kwh_per_arm * TOTAL_RATE_C_PER_KWH / 100.0
    hvac_dollars_b = kwh_per_arm * (1 - SAVINGS_PCT) * TOTAL_RATE_C_PER_KWH / 100.0
    diff = hvac_dollars_b - hvac_dollars_a

    # valid_pair_hours: total 288 minus union of all exclusions.
    # Fallback/invalid hours are disjoint per construction.
    excluded_total = fallback_hours_b + ti_a + ti_b
    valid_pair_hours = POST_WASHOUT_HOURS - excluded_total

    return {
        "pair_id": pair_id,
        "scenario_label": label,
        "arm_a_id": f"A{arm_a}",
        "arm_b_id": f"B{arm_b}",
        "valid_pair_hours": valid_pair_hours,
        "hvac_dollars_a": round(hvac_dollars_a, 2),
        "hvac_dollars_b": round(hvac_dollars_b, 2),
        "diff_dollars_b_minus_a": round(diff, 2),
        "poor_weather_match_flag": weather_outlier,
        "low_cooling_exposure_flag": cooling_hours_total < 6,
        "expected_savings_pct": (SAVINGS_PCT if hvac_dollars_a > 5.0 else None),
        "dst_crossing_arm": dst,
    }


# ---------------------------------------------------------------------------
# Public dataset construction
# ---------------------------------------------------------------------------


@dataclass
class SynthDataset:
    """Synthetic inputs + hand-pinned expected outputs."""
    refoss_df: pd.DataFrame
    eagle_df: pd.DataFrame
    ecowitt_df: pd.DataFrame
    rt_hrl_lmps_df: pd.DataFrame
    comed_prices_df: pd.DataFrame
    hvac_arm_mode_df: pd.DataFrame
    bills_df: pd.DataFrame
    expected_per_pair_table: pd.DataFrame
    expected_arms_passed_validity: set = field(default_factory=set)
    injected_modes: set = field(default_factory=lambda: set(INJECTED_MODES))


def build_synth_dataset() -> SynthDataset:
    """Construct the synthetic dataset and its hand-pinned expected outputs.

    Each scenario in SCENARIOS is actually INJECTED into the data:
    - cooling-active power patterns per arm
    - B-fallback mode telemetry for Arm 4 (20 hours)
    - telemetry-invalid (Refoss-omitted) hours for Arms 7/8 (15/18 hours)
    - weather outlier (+20°F) for Arm 6

    Test `test_fixture_actually_injects_claimed_scenarios` verifies this.
    """
    arm_state = _build_arm_state()

    refoss_rows = []
    eagle_rows = []
    ecowitt_rows = []
    rt_lmps_rows = []
    comed_prices_rows = []
    arm_mode_rows = []

    for arm_idx, arm_letter, start, _ in CALENDAR:
        state = arm_state[arm_idx]
        # Build full arm timeline at hourly resolution
        for hour_offset in range(ARM_TOTAL_HOURS):
            ts = start + datetime.timedelta(hours=hour_offset)
            is_post_washout = hour_offset >= WASHOUT_HOURS
            offset_in_post_washout = hour_offset  # used to look up injected sets
            is_cooling_active = (
                is_post_washout and
                offset_in_post_washout in set(_cooling_active_offsets(state["cooling_per_day"]))
            )
            is_telemetry_invalid = offset_in_post_washout in state["telemetry_invalid_hour_set"]
            is_fallback = offset_in_post_washout in state["fallback_hour_set"]

            # Refoss: OMIT the hour entirely if telemetry-invalid (validity
            # check treats missing-hour data as invalid per spec §7).
            if not is_telemetry_invalid:
                if is_cooling_active and not is_fallback:
                    # Arm B during B-active cooling: 15% less power
                    if arm_letter == "B":
                        em2_w = 1500.0 * (1 - SAVINGS_PCT)
                        em8_w = 1000.0 * (1 - SAVINGS_PCT)
                    else:
                        em2_w = 1500.0
                        em8_w = 1000.0
                    em9_w = BLOWER_W_WHEN_RUNNING
                elif is_cooling_active and is_fallback:
                    # B-fallback during cooling: controller in fallback;
                    # data shows AC running at thermostat-like behavior
                    # (equivalent to Arm A power level).
                    em2_w = 1500.0
                    em8_w = 1000.0
                    em9_w = BLOWER_W_WHEN_RUNNING
                else:
                    em2_w = em8_w = em9_w = 0.0

                # em:1 / em:7 (mains): always have some baseline household load
                em1_w = 800.0
                em7_w = 1200.0
                # Mains must accommodate HVAC load (mains-sanity check)
                em1_w += em2_w + em8_w + em9_w  # ensure mains >= HVAC

                for ch, watts in (("em:1", em1_w), ("em:2", em2_w),
                                  ("em:7", em7_w), ("em:8", em8_w),
                                  ("em:9", em9_w)):
                    refoss_rows.append({
                        "_time": ts, "channel": ch, "_value": watts,
                        "_field": "power_w",
                    })

            # Eagle hourly delivered kWh (whole-home; close to em:1+em:7+em:2+em:8+em:9)
            eagle_kwh = (em1_w + em2_w + em7_w + em8_w + em9_w) / 1000.0 if not is_telemetry_invalid else 0.0
            eagle_rows.append({
                "_time": ts, "delivered_kwh": eagle_kwh,
                "_measurement": "eagle.meter",
            })

            # Ecowitt hourly weather
            temp_f = BASE_TEMP_F + (WEATHER_OUTLIER_SHIFT_F if state["weather_outlier"] else 0.0)
            ecowitt_rows.append({
                "_time": ts, "ch1_temp_f": temp_f,
                "ch1_dewpoint_f": BASE_DEWPOINT_F,
            })

            # rt_hrl_lmps hourly settled (flat synthetic rate)
            rt_lmps_rows.append({
                "_time": ts,
                "total_lmp_rt": KNOWN_LMP_C_PER_KWH * 10.0,
                "pnode_id": "33092371",
            })

            # comed.prices 5-min stream (12 rows per hour)
            for minute in range(0, 60, 5):
                comed_prices_rows.append({
                    "_time": ts + datetime.timedelta(minutes=minute),
                    "price_cents_per_kwh": KNOWN_LMP_C_PER_KWH,
                    "period_type": "5min",
                })

            # hvac.arm_mode 5-min (12 rows per hour)
            if is_post_washout:
                if is_telemetry_invalid:
                    mode = "telemetry-invalid"
                elif arm_letter == "A":
                    mode = "A-active"
                elif is_fallback:
                    mode = "B-fallback"
                else:
                    mode = "B-active"
            else:
                # Washout hours: arm-letter-based default, no fallback markers
                mode = "A-active" if arm_letter == "A" else "B-active"
            for minute in range(0, 60, 5):
                arm_mode_rows.append({
                    "_time": ts + datetime.timedelta(minutes=minute),
                    "arm": arm_letter,
                    "mode_actual": mode,
                })

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

    expected_rows = [_expected_per_pair_row(s) for s in SCENARIOS]

    # All 12 arms expected to pass the validity gate in the scaffold (the
    # injected telemetry-invalid hours are << the 90% threshold).
    expected_arms_passed = set()
    for arm_idx, arm_letter, _, _ in CALENDAR:
        expected_arms_passed.add(f"{arm_letter}{arm_idx}")

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
