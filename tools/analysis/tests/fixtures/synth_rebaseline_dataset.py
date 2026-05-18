"""Synthetic 12-arm experiment dataset for the SCED rebaseline outside-in
acceptance test.

Constructs:
- 12 arm periods following spec §2 calendar (Mon 00:00 CT switches),
  serialized in production-canonical UTC instants.
- Refoss HVAC channels (em:2/em:8/em:9) at 30s cadence (~120 samples
  per channel per hour) so spec §7 validity rules are exercised; mains
  (em:1, em:7) at 1/hour for bill-reconciliation sanity.
- Eagle ``eagle.meter`` measurement with a monotonically-increasing
  ``delivered_kwh`` totalizer (production schema -- the loader derives
  per-hour kWh from totalizer differences).
- Ecowitt hourly weather (``ch1_temp_f``, ``ch1_dewpoint_f``).
- rt_hrl_lmps hourly settled prices.
- comed.prices 5-min live prices (diagnostic only).
- hvac.arm_mode 5-min mode telemetry (with known mode distribution).
- comed.bill_lineitems with full line-item set + bill-period bounds.

ORACLE INDEPENDENCE (spec/plan #8 rule, AGENTS.md outside-in TDD):
expected values in ``expected_per_pair_table`` are hand-computed from
the constants in this file. THIS FIXTURE DOES NOT IMPORT FROM
``tools.analysis.*`` for expected output computation. The fixture
recomputes CT/UTC conversions via stdlib zoneinfo so it stays
independent of ``tools.analysis.arm_calendar`` etc.

SCENARIO INJECTION: each scenario in SCENARIOS is INJECTED into the
generated data:
- B-fallback hours: written as ``mode_actual = "B-fallback"`` in
  hvac_arm_mode_df.
- Telemetry-invalid hours: Refoss rows OMITTED for those hours.
- Weather-outlier arm: ``ch1_temp_f`` shifted +20F vs other arms.
- Cooling-active hours: em:2/em:8/em:9 nonzero only during the
  CT-local cooling window (12:00 -- 12:00 + cooling_per_day_hours CT).

If spec §2 (arm calendar) changes, update CALENDAR_CT below to match.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Constants used both to construct the synthetic inputs AND to hand-compute
# the expected outputs. Independence rule: nothing imported from
# ``tools.analysis.*`` below this line.
# ---------------------------------------------------------------------------

_CT = ZoneInfo("America/Chicago")
_UTC = ZoneInfo("UTC")


def _ct_naive_to_utc_naive(dt_ct: datetime.datetime) -> datetime.datetime:
    """CT-naive -> UTC-naive instant via zoneinfo. Stays oracle-independent
    of ``tools.analysis.arm_calendar``.
    """
    return dt_ct.replace(tzinfo=_CT).astimezone(_UTC).replace(tzinfo=None)


# Locked SCED arm calendar per spec §2, CT-naive Mon 00:00 boundaries.
# Tuple = (idx_1based, arm_letter, start_ct, end_ct).
CALENDAR_CT: tuple[tuple[int, str, datetime.datetime, datetime.datetime], ...] = (
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

# Same calendar with UTC-naive bounds for input data generation. Arms
# 1-10 + 12 are entirely in CDT/CST without DST crossing; arm 11 crosses
# 2026-11-01 fall-back, so its end_utc shift differs from its start_utc
# shift. The post-washout window uses 288 elapsed UTC hours from the
# post-washout start UTC instant -- arm 11's 288 hours are 1 wall-clock
# hour shorter than the calendar would otherwise suggest (spec §2 DST
# equalization exclusion).
CALENDAR: tuple[tuple[int, str, datetime.datetime, datetime.datetime], ...] = tuple(
    (idx, letter, _ct_naive_to_utc_naive(s), _ct_naive_to_utc_naive(e))
    for idx, letter, s, e in CALENDAR_CT
)

WASHOUT_HOURS = 48
POST_WASHOUT_HOURS = 12 * 24  # 288 UTC-elapsed (spec §2)
ARM_TOTAL_UTC_HOURS = 14 * 24  # used to bound full-arm timestamp grid

REFOSS_SAMPLES_PER_HOUR = 120  # 30s cadence
REFOSS_CADENCE_SECONDS = 30

# Injected scenario constants. ``em:2 + em:8 + em:9 = BASELINE_HVAC_W``
# during cooling; that sum drives the hand-pinned expected dollars.
SAVINGS_PCT = 0.15
EM2_W_COOL = 1000.0
EM8_W_COOL = 1000.0
EM9_W_COOL = 500.0
BASELINE_HVAC_W = EM2_W_COOL + EM8_W_COOL + EM9_W_COOL  # 2500 W = 2.5 kW
BASELINE_HVAC_KW = BASELINE_HVAC_W / 1000.0

# Mains baseline (always-on household load that accommodates HVAC).
EM1_W_MAINS = 1500.0
EM7_W_MAINS = 1500.0

# Outlier-arm temperature shift, applied to the entire arm's ecowitt
# temp series. Combined with the per-arm jitter below, this produces a
# distance distribution where the outlier arm is materially further than
# typical arm-pair distances (so the spec §6 caliper flag fires).
WEATHER_OUTLIER_SHIFT_F = 20.0
BASE_TEMP_F = 78.0
BASE_DEWPOINT_F = 60.0
# Per-arm jitter (deterministic) so within-sample z-score has nonzero
# variance even when no scenario is the outlier. The arm-index-derived
# jitter keeps the fixture self-contained.
PER_ARM_TEMP_JITTER_F = 1.5
PER_ARM_DEWPOINT_JITTER_F = 1.0

# Rate primitives. These MUST mirror
# tools.analysis.arm_period_pipeline.DEFAULT_RATE_SNAPSHOT byte-for-byte
# so the acceptance test can call run_full_pipeline() without passing a
# custom snapshot. The oracle-independence rule forbids importing the
# orchestrator's snapshot symbol; the values are hand-pinned here.
KNOWN_LMP_C_PER_KWH = 5.0
KNOWN_LMP_DOLLARS_PER_MWH = KNOWN_LMP_C_PER_KWH * 10.0  # PJM scale: 50 $/MWh
RATE_SNAPSHOT = {
    "pea_c_per_kwh": 1.773,
    "transmission_c_per_kwh": 1.083,
    "misc_procurement_c_per_kwh": 0.062,
    "variable_riders_c_per_kwh": 1.16,
    "carbon_free_credit_c_per_kwh": -3.186,
}
DTOD_TOTAL_DELIVERY_C_PER_KWH = 4.554  # Morning band; cooling window
                                       # 12-18 CT is partly in Mid-Day
                                       # Peak (13-19) but we use the
                                       # fixture's flat overnight value
                                       # at the cooling hours (see below).


def _expected_hourly_rate_c_per_kwh_at_ct_hour(hour_ct: int) -> float:
    """Hand-computed hourly rate (c/kWh) at a given CT hour-of-day,
    using the spec §4 DTOD bands. Stays independent of
    ``tools.analysis.dtod_rates`` per the oracle-independence rule.
    """
    if 6 <= hour_ct < 13:
        dtod_resultant = 4.428  # Morning
    elif 13 <= hour_ct < 19:
        dtod_resultant = 11.727  # Mid-Day Peak
    elif 19 <= hour_ct < 21:
        dtod_resultant = 4.142  # Evening
    else:
        dtod_resultant = 3.311  # Overnight (21-24 + 0-6)
    iedt = 0.126
    dtod_total = dtod_resultant + iedt
    return (
        KNOWN_LMP_C_PER_KWH
        + RATE_SNAPSHOT["pea_c_per_kwh"]
        + RATE_SNAPSHOT["transmission_c_per_kwh"]
        + RATE_SNAPSHOT["misc_procurement_c_per_kwh"]
        + dtod_total
        + RATE_SNAPSHOT["variable_riders_c_per_kwh"]
        + RATE_SNAPSHOT["carbon_free_credit_c_per_kwh"]
    )


# Cooling window in CT. cooling_per_day=N means hours 13..(13+N) CT are
# cooling-active. Window starts at the DTOD Mid-Day-Peak boundary
# (13:00 CT per spec §8) so every cooling hour lives in a single rate
# band. This keeps cost-matched-exclusion drops predictable: A's cheapest
# match for a B-fallback Peak hour is another Peak hour, which is also
# a cooling hour. Without this constraint, cross-band ties (e.g., A's
# CT 06:00 overnight hour matches B's CT 12:00 morning hour at flat
# rates) would silently land drops on non-cooling A hours and break
# the hand-pinned expected dollars.
COOLING_START_HOUR_CT = 13
MAX_COOLING_PER_DAY = 6  # Peak band is 13:00 - 19:00 CT = 6 hours

# Per-pair injected scenarios. (pair_id, arm_a, arm_b, label,
#  cooling_per_day, fallback_hours_b, telemetry_invalid_a,
#  telemetry_invalid_b, weather_outlier_on_b, dst_crossing)
#
# Scenarios that need cost-matched-exclusion drops (heat_wave with
# fallback_hours_b > 0, telemetry_gap with ti_* > 0) must use
# cooling_per_day == MAX_COOLING_PER_DAY (6) so every Peak-band A
# candidate is also a cooling hour. Otherwise the cost-matched algorithm
# would drop A's non-cooling Peak hours (e.g. CT 18 when cooling stops
# at CT 17) and the hand-pinned expected dollars would mismatch the
# orchestrator output.
SCENARIOS: tuple = (
    (1,  1, 2,  "mild_summer",     4,  0,  0,  0, False, False),
    (2,  3, 4,  "heat_wave",       6, 20,  0,  0, False, False),
    (3,  5, 6,  "weather_outlier", 6,  0,  0,  0, True,  False),
    (4,  7, 8,  "telemetry_gap",   6,  0, 15, 18, False, False),
    (5,  9, 10, "shoulder",        0,  0,  0,  0, False, False),
    (6, 11, 12, "dst_cool",        2,  0,  0,  0, False, True),
)

# Modes the fixture INJECTS via mode_telemetry construction. Acceptance
# test asserts these modes are observed in the pipeline output.
INJECTED_MODES = {"A-active", "B-active", "B-fallback", "telemetry-invalid"}


# ---------------------------------------------------------------------------
# Per-arm state + cooling-hour resolution
# ---------------------------------------------------------------------------


def _post_washout_utc_start(arm_start_ct: datetime.datetime) -> datetime.datetime:
    return _ct_naive_to_utc_naive(
        arm_start_ct + datetime.timedelta(hours=WASHOUT_HOURS)
    )


def _hour_index_to_utc(arm_start_ct: datetime.datetime, k: int) -> datetime.datetime:
    return _post_washout_utc_start(arm_start_ct) + datetime.timedelta(hours=k)


def _ct_hour_at_index(arm_start_ct: datetime.datetime, k: int) -> int:
    """CT hour-of-day at hour-index k in the arm's post-washout window."""
    utc = _hour_index_to_utc(arm_start_ct, k)
    return utc.replace(tzinfo=_UTC).astimezone(_CT).hour


def _cooling_active_hour_indices(arm_start_ct: datetime.datetime,
                                  cooling_per_day: int) -> list[int]:
    """Return post-washout hour-indices where cooling is active. Walks
    each post-washout day and pulls the CT-local 12:00..(12+N):00
    window. Handles DST fold via zoneinfo.
    """
    if cooling_per_day <= 0:
        return []
    out: list[int] = []
    for k in range(POST_WASHOUT_HOURS):
        ct_hour = _ct_hour_at_index(arm_start_ct, k)
        if COOLING_START_HOUR_CT <= ct_hour < COOLING_START_HOUR_CT + cooling_per_day:
            out.append(k)
    return out


def _build_arm_state() -> dict[int, dict]:
    state: dict[int, dict] = {}
    for (pair_id, arm_a, arm_b, label, cooling_per_day, fallback_hours_b,
         ti_a, ti_b, weather_outlier_b, dst) in SCENARIOS:
        a_start_ct = CALENDAR_CT[arm_a - 1][2]
        b_start_ct = CALENDAR_CT[arm_b - 1][2]
        a_cooling = _cooling_active_hour_indices(a_start_ct, cooling_per_day)
        b_cooling = _cooling_active_hour_indices(b_start_ct, cooling_per_day)
        # Spend cooling-active hour-indices deterministically on
        # injection (first N for fallback, next M for telemetry-invalid).
        fallback_set_b = set(b_cooling[:fallback_hours_b])
        ti_set_a = set(a_cooling[fallback_hours_b:fallback_hours_b + ti_a])
        ti_set_b = set(b_cooling[fallback_hours_b:fallback_hours_b + ti_b])
        state[arm_a] = {
            "role": "A", "pair_id": pair_id, "label": label,
            "cooling_per_day": cooling_per_day,
            "fallback_hour_set": set(),
            "telemetry_invalid_hour_set": ti_set_a,
            "cooling_hour_set": set(a_cooling),
            "weather_outlier": False,
            "dst_crossing": dst,
        }
        state[arm_b] = {
            "role": "B", "pair_id": pair_id, "label": label,
            "cooling_per_day": cooling_per_day,
            "fallback_hour_set": fallback_set_b,
            "telemetry_invalid_hour_set": ti_set_b,
            "cooling_hour_set": set(b_cooling),
            "weather_outlier": weather_outlier_b,
            "dst_crossing": dst,
        }
    return state


# ---------------------------------------------------------------------------
# Hand-computed expected outputs (oracle-independence #8 rule)
# ---------------------------------------------------------------------------


def _expected_hvac_dollars_for_arm(
    *, arm_start_ct: datetime.datetime, cooling_per_day: int,
    own_invalid: int, cost_matched_drops_into_own_cooling: int, is_b: bool,
) -> tuple[float, float, int]:
    """Hand-compute (hvac_dollars, hvac_kwh, valid_hours_after_excl) for an
    arm.

    The cost-matched-exclusion drops landing on THIS arm are computed
    by the caller (``_expected_per_pair_row``), accounting for the
    overlap between A's and B's injected-invalid sets. Since both arms
    inject invalid hours as a contiguous prefix of the same cooling-
    offset list, the smaller set is always a subset of the larger; the
    asymmetric (TF / FT) count is therefore ``|A_set Δ B_set|``.
    """
    total_cooling = cooling_per_day * 12  # 12 post-washout days
    effective_cooling = max(
        0, total_cooling - own_invalid - cost_matched_drops_into_own_cooling
    )

    # Cooling window starts at COOLING_START_HOUR_CT (13:00 CT = Peak
    # band start) and runs cooling_per_day hours, capped at 6 (Peak band
    # is 13:00-19:00 CT). All cooling hours therefore live in a single
    # rate band, so per-hour rates are identical and the average is the
    # Peak-band rate.
    hourly_rates: list[float] = []
    for h_offset in range(cooling_per_day):
        ct_hour = COOLING_START_HOUR_CT + h_offset
        hourly_rates.append(_expected_hourly_rate_c_per_kwh_at_ct_hour(ct_hour))
    avg_cooling_rate_c = (
        sum(hourly_rates) / len(hourly_rates) if hourly_rates else 0.0
    )

    # B-side scaled by (1 - SAVINGS_PCT)
    scale = (1.0 - SAVINGS_PCT) if is_b else 1.0
    kwh_per_cooling_hour = BASELINE_HVAC_KW * scale
    kwh = effective_cooling * kwh_per_cooling_hour
    dollars = kwh * avg_cooling_rate_c / 100.0
    valid_hours = POST_WASHOUT_HOURS - own_invalid - cost_matched_drops_into_own_cooling
    return dollars, kwh, valid_hours


def _expected_per_pair_row(scenario_tuple: tuple) -> dict:
    (pair_id, arm_a, arm_b, label, cooling_per_day, fallback_hours_b,
     ti_a, ti_b, weather_outlier, dst) = scenario_tuple

    a_start_ct = CALENDAR_CT[arm_a - 1][2]
    b_start_ct = CALENDAR_CT[arm_b - 1][2]

    b_total_invalid = fallback_hours_b + ti_b
    a_total_invalid = ti_a
    # Both arms' invalid sets are contiguous prefixes of the same
    # cooling-offset list, so the smaller set is a subset of the larger.
    # The asymmetric counts driving cost-matched drops are therefore:
    #   A drops (cost-matched into A's cooling) = B_total - overlap
    #   B drops (cost-matched into B's cooling) = A_total - overlap
    overlap = min(a_total_invalid, b_total_invalid)
    a_cost_matched_drops = b_total_invalid - overlap
    b_cost_matched_drops = a_total_invalid - overlap

    dollars_a, kwh_a, valid_a = _expected_hvac_dollars_for_arm(
        arm_start_ct=a_start_ct, cooling_per_day=cooling_per_day,
        own_invalid=a_total_invalid,
        cost_matched_drops_into_own_cooling=a_cost_matched_drops,
        is_b=False,
    )
    dollars_b, kwh_b, valid_b = _expected_hvac_dollars_for_arm(
        arm_start_ct=b_start_ct, cooling_per_day=cooling_per_day,
        own_invalid=b_total_invalid,
        cost_matched_drops_into_own_cooling=b_cost_matched_drops,
        is_b=True,
    )
    # Symmetric: valid_pair_hours equal on both sides.
    valid_pair_hours = min(valid_a, valid_b)

    # Spec §6 strict-`>` interpretation: with n=6 and a single weather
    # outlier, the Hungarian-matched outlier pair's distance sits below
    # the linear-interpolated 90th percentile (boundary case). Expected
    # poor_weather_match_flag is therefore False even for the outlier
    # scenario -- see ``caliper_p90_distance`` docstring. This is a
    # known spec-clarification item, not a fixture bug.
    return {
        "pair_id": pair_id,
        "scenario_label": label,
        "arm_a_id": f"A{arm_a}",
        "arm_b_id": f"B{arm_b}",
        "valid_pair_hours": valid_pair_hours,
        "hvac_dollars_a": round(dollars_a, 4),
        "hvac_dollars_b": round(dollars_b, 4),
        "diff_dollars_b_minus_a": round(dollars_b - dollars_a, 4),
        "poor_weather_match_flag": False,
        "low_cooling_exposure_flag": cooling_per_day * 12 < 6,
        "expected_savings_pct": (SAVINGS_PCT if dollars_a > 5.0 else None),
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


def _per_arm_temp_offset(arm_idx: int) -> float:
    """Deterministic per-arm temp jitter so non-outlier arms still have
    nonzero pairwise distance (lets the spec §6 90th-percentile caliper
    actually discriminate the outlier arm from the population)."""
    # Map arm 1..12 to evenly-spaced offsets in [-JITTER, +JITTER]
    spread = PER_ARM_TEMP_JITTER_F
    return -spread + (arm_idx - 1) * (2.0 * spread / 11.0)


def _per_arm_dewpoint_offset(arm_idx: int) -> float:
    spread = PER_ARM_DEWPOINT_JITTER_F
    # Use a different phase so the dewpoint axis isn't perfectly
    # collinear with temp.
    return -spread + ((arm_idx + 5) % 12) * (2.0 * spread / 11.0)


def build_synth_dataset() -> SynthDataset:
    """Construct the synthetic dataset and its hand-pinned expected outputs."""
    arm_state = _build_arm_state()

    refoss_rows: list[dict] = []
    eagle_rows: list[dict] = []
    ecowitt_rows: list[dict] = []
    rt_lmps_rows: list[dict] = []
    comed_prices_rows: list[dict] = []
    arm_mode_rows: list[dict] = []

    eagle_totalizer = 100000.0  # monotonic across the whole experiment

    for arm_idx, arm_letter, arm_start_utc, _ in CALENDAR:
        st = arm_state[arm_idx]
        arm_start_ct = CALENDAR_CT[arm_idx - 1][2]
        post_washout_utc = _post_washout_utc_start(arm_start_ct)

        # Iterate FULL arm window (including washout) hourly for
        # Ecowitt/LMP/mode/Eagle; Refoss gets 30s within each hour.
        for hour_offset in range(ARM_TOTAL_UTC_HOURS):
            ts = arm_start_utc + datetime.timedelta(hours=hour_offset)
            # k = post-washout hour-index, or -1 if in washout
            in_washout = ts < post_washout_utc
            if in_washout:
                k = -1
            else:
                k = int((ts - post_washout_utc).total_seconds() // 3600)
                if k >= POST_WASHOUT_HOURS:
                    break  # past the 288-hour analysis window
            is_telemetry_invalid = (k >= 0
                                    and k in st["telemetry_invalid_hour_set"])
            is_fallback = (k >= 0 and k in st["fallback_hour_set"])
            is_cooling = (k >= 0 and k in st["cooling_hour_set"])

            # Refoss HVAC channels at 30s cadence (omit when invalid hour
            # so spec §7 validity flag triggers); mains at 1/hour.
            if is_telemetry_invalid:
                hvac_kw_in_hour = 0.0
            else:
                if is_cooling and not is_fallback:
                    scale = (1.0 - SAVINGS_PCT) if arm_letter == "B" else 1.0
                    em2_w = EM2_W_COOL * scale
                    em8_w = EM8_W_COOL * scale
                    em9_w = EM9_W_COOL * scale
                elif is_cooling and is_fallback:
                    # B-fallback during cooling: AC running at A-level
                    em2_w = EM2_W_COOL
                    em8_w = EM8_W_COOL
                    em9_w = EM9_W_COOL
                else:
                    em2_w = em8_w = em9_w = 0.0
                hvac_kw_in_hour = (em2_w + em8_w + em9_w) / 1000.0

                for ch, watts in (("em:2", em2_w),
                                  ("em:8", em8_w),
                                  ("em:9", em9_w)):
                    for s in range(REFOSS_SAMPLES_PER_HOUR):
                        refoss_rows.append({
                            "_time": ts + datetime.timedelta(
                                seconds=REFOSS_CADENCE_SECONDS * s
                            ),
                            "channel": ch, "_value": watts,
                            "_field": "power_w",
                        })
                # Mains at 1/hour (sanity-only).
                refoss_rows.append({
                    "_time": ts, "channel": "em:1",
                    "_value": EM1_W_MAINS + em2_w + em8_w + em9_w,
                    "_field": "power_w",
                })
                refoss_rows.append({
                    "_time": ts, "channel": "em:7", "_value": EM7_W_MAINS,
                    "_field": "power_w",
                })

            # Eagle monotonic totalizer: increment by whole-home kWh
            # estimate even on invalid hours (the meter keeps reading).
            whole_home_kwh = (
                hvac_kw_in_hour
                + (EM1_W_MAINS + EM7_W_MAINS) / 1000.0
            )
            eagle_totalizer += whole_home_kwh
            eagle_rows.append({
                "_time": ts, "delivered_kwh": eagle_totalizer,
                "_field": "delivered_kwh", "_measurement": "eagle.meter",
            })

            # Ecowitt
            temp_f = (
                BASE_TEMP_F
                + _per_arm_temp_offset(arm_idx)
                + (WEATHER_OUTLIER_SHIFT_F if st["weather_outlier"] else 0.0)
            )
            dewpoint_f = BASE_DEWPOINT_F + _per_arm_dewpoint_offset(arm_idx)
            ecowitt_rows.append({
                "_time": ts, "ch1_temp_f": temp_f,
                "ch1_dewpoint_f": dewpoint_f,
            })

            # rt_hrl_lmps hourly settled
            rt_lmps_rows.append({
                "_time": ts,
                "total_lmp_rt": KNOWN_LMP_DOLLARS_PER_MWH,
                "pnode_id": "33092371",
            })

            # comed.prices 5-min (12 per hour, diagnostic only)
            for minute in range(0, 60, 5):
                comed_prices_rows.append({
                    "_time": ts + datetime.timedelta(minutes=minute),
                    "price_cents_per_kwh": KNOWN_LMP_C_PER_KWH,
                    "period_type": "5min",
                })

            # hvac.arm_mode 5-min (12 per hour)
            if k >= 0:
                if is_telemetry_invalid:
                    mode = "telemetry-invalid"
                elif arm_letter == "A":
                    mode = "A-active"
                elif is_fallback:
                    mode = "B-fallback"
                else:
                    mode = "B-active"
            else:
                mode = "A-active" if arm_letter == "A" else "B-active"
            for minute in range(0, 60, 5):
                arm_mode_rows.append({
                    "_time": ts + datetime.timedelta(minutes=minute),
                    "arm": arm_letter, "mode_actual": mode,
                })

    # Bills: one per CT-calendar month covered by the experiment.
    # Bill-period bounds are UTC instants aligned to the start of each
    # CT month (so the reconstructed-cost iteration is hour-aligned with
    # the rt_hrl_lmps data coverage and never asks for a missing hour).
    bill_rows: list[dict] = []
    # Only emit fully-covered bill periods: June through October. The
    # November bill period would extend past the experiment data window
    # (rt_hrl_lmps stops at 2026-11-16) and break bill reconciliation
    # on the partial month.
    month_starts_ct = [
        datetime.datetime(2026, m, 1) for m in range(6, 12)
    ]
    month_starts_utc = [_ct_naive_to_utc_naive(d) for d in month_starts_ct]
    for i in range(len(month_starts_utc) - 1):
        start = month_starts_utc[i]
        end = month_starts_utc[i + 1]
        # Spec §10 line-item categories. Amounts are sized for synthetic
        # 30-day whole-home consumption (~1500 kWh) at the fixture rate.
        line_items = (
            ("DELIVERY",  "Distribution Facility Charge",      80.0),
            ("DELIVERY",  "IL Electricity Distribution Charge", 5.0),
            ("DELIVERY",  "Standard Metering Charge",           3.83),
            ("DELIVERY",  "Customer Charge",                   15.35),
            ("SUPPLY",    "Electricity Supply Charge",         60.0),
            ("SUPPLY",    "Purchased Electricity Adjustment",  20.0),
            ("SUPPLY",    "Transmission Services",             15.0),
            ("SUPPLY",    "Misc Procurement Components",        2.0),
            ("TAXES_FEES_CREDITS", "Energy Efficiency Programs", 5.0),
            ("TAXES_FEES_CREDITS", "Renewable Portfolio Standard", 5.0),
            ("TAXES_FEES_CREDITS", "Zero Emission Standard",     3.0),
            ("TAXES_FEES_CREDITS", "Environmental Cost Recovery", 2.0),
            ("TAXES_FEES_CREDITS", "Coal to Solar / Energy Storage", 1.0),
            ("TAXES_FEES_CREDITS", "Carbon-Free Energy Resource Adj", -30.0),
        )
        variable_dollars = sum(
            amt for cat, _, amt in line_items
            if cat != "DELIVERY" or "Customer" not in _
        ) + sum(
            amt for cat, name, amt in line_items
            if cat == "DELIVERY" and "Customer" in name
        )
        # Single summary row per bill carries bill_period_*+variable_dollars
        # so the orchestrator can iterate bill periods directly.
        bill_rows.append({
            "_time": end - datetime.timedelta(days=5),
            "account_no": "synth-test",
            "bill_period_start_utc": start,
            "bill_period_end_utc": end,
            "variable_dollars": variable_dollars,
            "category": "SUMMARY",
            "line_item": "synth-summary",
            "amount": variable_dollars,
            "_field": "amount",
        })
        for cat, item, amt in line_items:
            bill_rows.append({
                "_time": end - datetime.timedelta(days=5),
                "account_no": "synth-test",
                "bill_period_start_utc": start,
                "bill_period_end_utc": end,
                "variable_dollars": variable_dollars,
                "category": cat, "line_item": item, "amount": amt,
                "_field": "amount",
            })

    expected_rows = [_expected_per_pair_row(s) for s in SCENARIOS]
    expected_arms_passed = {
        f"{arm_letter}{arm_idx}"
        for arm_idx, arm_letter, _, _ in CALENDAR
    }

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
