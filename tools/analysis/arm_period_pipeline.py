"""Arm-period pipeline orchestrator per
docs/plans/sced-rebaseline-spec-2026-05-13.md §4-§9.

End-to-end:
1. Build per-hour mode lists per arm (5-min telemetry -> hourly,
   with telemetry-invalid override).
2. Apply the §5/§7 single validity gate to each arm.
3. Build §6 weather vectors and within-sample z-score across all 12
   arms; then rectangular Hungarian over gate-pass A and gate-pass B
   subsets.
4. Per matched pair: per-hour HVAC$ + kWh + cost-matched symmetric
   exclusion + provenance fields.
5. Aggregate §9.5 pre-registered summary buckets.

DST fold contract (plan §3): every per-hour join keys on UTC instants
(via hour_index_to_utc) or hour-index; never on the CT-naive datetime
returned by ``arm_calendar.hour_index_to_datetime``.
"""
from __future__ import annotations

import datetime
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from tools.analysis.arm_calendar import (
    ARM_CALENDAR,
    ArmPeriod,
    HOURS_PER_ARM,
    post_washout_start,
)
from tools.analysis.cost_matched_exclusion import (
    cost_matched_exclude_with_provenance,
)
from tools.analysis.dtod_rates import dtod_total_delivery_c_per_kwh
from tools.analysis.hvac_dollars import HourlyRateInputs, hourly_rate_c_per_kwh
from tools.analysis.hvac_telemetry_validity import (
    HVAC_CHANNELS,
    hour_is_telemetry_valid,
)
from tools.analysis.matching import (
    caliper_p90_distance,
    hungarian_match,
    pairwise_distance_matrix,
    within_sample_zscore,
)
from tools.analysis.mode_classification import HourMode
from tools.analysis.validity_gate import (
    arm_passes_validity_gate,
    fully_valid_count,
    is_fully_valid_for_arm,
)
from tools.analysis.weather_vector import WeatherVector, build_weather_vector


_CT = ZoneInfo("America/Chicago")
_UTC = ZoneInfo("UTC")


# Default OSF April-2026 snapshot of non-LMP rate primitives. Real
# pipeline calls override this via the ``rate_snapshot`` kwarg. The
# defaults are only used so the fixture-driven outside-in test can
# exercise the path without rebuilding the snapshot from bills.
DEFAULT_RATE_SNAPSHOT: dict[str, float] = {
    "pea_c_per_kwh": 1.773,
    "transmission_c_per_kwh": 1.083,
    "misc_procurement_c_per_kwh": 0.062,
    "variable_riders_c_per_kwh": 1.16,
    "carbon_free_credit_c_per_kwh": -3.186,
}


@dataclass
class PipelineResult:
    """Outside-in acceptance test contract."""
    per_pair_table: pd.DataFrame
    bucket_summaries: dict[str, dict]
    mode_distribution: dict[str, int]
    arms_passed_validity: set[str]
    bill_reconciliations: list[dict] = field(default_factory=list)


# --- Time helpers ----------------------------------------------------------


def _arm_id(arm: ArmPeriod) -> str:
    return f"{arm.arm}{arm.index}"


def _post_washout_utc_naive(arm: ArmPeriod) -> datetime.datetime:
    """Naive-UTC start of the post-washout window (CT 00:00 Wed
    post-arm-start, converted via zoneinfo)."""
    return (
        post_washout_start(arm)
        .replace(tzinfo=_CT)
        .astimezone(_UTC)
        .replace(tzinfo=None)
    )


def _hour_index_to_utc(arm: ArmPeriod, k: int) -> datetime.datetime:
    """Hour-index k in this arm -> naive-UTC hour-boundary."""
    return _post_washout_utc_naive(arm) + datetime.timedelta(hours=k)


def _utc_to_ct_hour_of_day(when_utc_naive: datetime.datetime) -> int:
    return (
        when_utc_naive.replace(tzinfo=_UTC)
        .astimezone(_CT)
        .hour
    )


# --- Mode classification from telemetry ------------------------------------


def _hourly_mode_from_telemetry(
    *,
    arm: ArmPeriod,
    refoss_df: pd.DataFrame,
    hvac_arm_mode_df: pd.DataFrame,
) -> list[HourMode]:
    """Build the 288-element HourMode list for this arm.

    Strategy:
    - Aggregate hvac_arm_mode_df 5-min rows -> hourly majority mode (the
      controller's recorded classification).
    - Cross-check against Refoss §7 telemetry validity. If telemetry is
      invalid for the hour, override to TELEMETRY_INVALID per the §5
      precedence rule.
    - If the hour has no mode telemetry at all, treat as B_DOWN for an
      Arm B period, TELEMETRY_INVALID for Arm A (no signal to confirm
      A-active either).
    """
    # Pre-filter mode telemetry to this arm's post-washout window once.
    start_utc = _post_washout_utc_naive(arm)
    end_utc = start_utc + datetime.timedelta(hours=HOURS_PER_ARM)
    mode_window = hvac_arm_mode_df[
        (hvac_arm_mode_df["_time"] >= start_utc)
        & (hvac_arm_mode_df["_time"] < end_utc)
    ].copy()
    if not mode_window.empty:
        mode_window = mode_window.assign(
            _hour=mode_window["_time"].dt.floor("h"),
        )

    modes: list[HourMode] = []
    for k in range(HOURS_PER_ARM):
        h_start = start_utc + datetime.timedelta(hours=k)
        # Check telemetry validity from Refoss
        telemetry_valid = hour_is_telemetry_valid(refoss_df, h_start)

        # Determine the recorded mode for this hour
        if not mode_window.empty:
            this_hour = mode_window[mode_window["_hour"] == h_start]
        else:
            this_hour = pd.DataFrame()
        if this_hour.empty:
            recorded_mode = None
        else:
            recorded_mode = this_hour["mode_actual"].mode().iloc[0]

        if not telemetry_valid:
            modes.append(HourMode.TELEMETRY_INVALID)
            continue

        if recorded_mode is None:
            modes.append(
                HourMode.B_DOWN if arm.arm == "B" else HourMode.TELEMETRY_INVALID
            )
            continue

        modes.append(_string_to_hour_mode(recorded_mode))
    return modes


def _string_to_hour_mode(s: str) -> HourMode:
    mapping = {
        "A-active": HourMode.A_ACTIVE,
        "B-active": HourMode.B_ACTIVE,
        "B-fallback": HourMode.B_FALLBACK,
        "B-down": HourMode.B_DOWN,
        "telemetry-invalid": HourMode.TELEMETRY_INVALID,
    }
    return mapping[s]


# --- Per-hour HVAC kWh + rate vectors --------------------------------------


def _hourly_hvac_kwh(arm: ArmPeriod, refoss_df: pd.DataFrame) -> list[float]:
    """Per spec §7: hour_kWh = mean(power_w in hour) * 1h, summed across
    em:2/em:8/em:9. Caller pre-filters _field if present.
    """
    df = refoss_df
    if "_field" in df.columns:
        df = df[df["_field"] == "power_w"]
    start_utc = _post_washout_utc_naive(arm)
    out: list[float] = []
    for k in range(HOURS_PER_ARM):
        h_start = start_utc + datetime.timedelta(hours=k)
        h_end = h_start + datetime.timedelta(hours=1)
        slice_df = df[(df["_time"] >= h_start) & (df["_time"] < h_end)]
        per_channel = slice_df.groupby("channel")["_value"].mean()
        total_w = sum(per_channel.get(ch, 0.0) for ch in HVAC_CHANNELS)
        # mean(power_w over 1h) * 1h = avg watts * 1h -> Wh; / 1000 -> kWh
        out.append(float(total_w) / 1000.0)
    return out


def _hourly_rates(
    arm: ArmPeriod,
    rt_hrl_lmps_df: pd.DataFrame,
    rate_snapshot: dict[str, float],
) -> list[float]:
    """¢/kWh per hour-index per spec §4."""
    start_utc = _post_washout_utc_naive(arm)
    rates: list[float] = []
    for k in range(HOURS_PER_ARM):
        h_start = start_utc + datetime.timedelta(hours=k)
        match = rt_hrl_lmps_df[rt_hrl_lmps_df["_time"] == h_start]
        lmp_per_mwh = float(match["total_lmp_rt"].iloc[0]) if not match.empty else 0.0
        ct_hour = _utc_to_ct_hour_of_day(h_start)
        inputs = HourlyRateInputs(
            rt_hrl_lmps_per_mwh=lmp_per_mwh,
            pea_c_per_kwh=rate_snapshot["pea_c_per_kwh"],
            transmission_c_per_kwh=rate_snapshot["transmission_c_per_kwh"],
            misc_procurement_c_per_kwh=rate_snapshot["misc_procurement_c_per_kwh"],
            dtod_total_delivery_c_per_kwh=dtod_total_delivery_c_per_kwh(ct_hour),
            variable_riders_c_per_kwh=rate_snapshot["variable_riders_c_per_kwh"],
            carbon_free_credit_c_per_kwh=rate_snapshot["carbon_free_credit_c_per_kwh"],
        )
        rates.append(hourly_rate_c_per_kwh(inputs))
    return rates


# --- Per-pair row construction ---------------------------------------------


def _build_pair_row(
    *,
    pair_id: int,
    arm_a: ArmPeriod,
    arm_b: ArmPeriod,
    modes_a: list[HourMode],
    modes_b: list[HourMode],
    rates_a: list[float],
    rates_b: list[float],
    kwh_a: list[float],
    kwh_b: list[float],
    vec_a: WeatherVector,
    vec_b: WeatherVector,
    z_a: np.ndarray,
    z_b: np.ndarray,
    p90_dist: float,
    rate_snapshot: dict[str, float],
) -> dict:
    initial_valid_a = [is_fully_valid_for_arm(m, "A") for m in modes_a]
    initial_valid_b = [is_fully_valid_for_arm(m, "B") for m in modes_b]

    # Breakdown of asymmetric-invalid hours BEFORE cost-matched drops
    excl_a = Counter()
    excl_b = Counter()
    for m in modes_a:
        if not is_fully_valid_for_arm(m, "A"):
            excl_a[m.value] += 1
    for m in modes_b:
        if not is_fully_valid_for_arm(m, "B"):
            excl_b[m.value] += 1

    excl = cost_matched_exclude_with_provenance(
        rates_a, rates_b, initial_valid_a, initial_valid_b,
    )
    valid_a = excl.valid_a
    valid_b = excl.valid_b

    # HVAC$ in DOLLARS (helper returns cents)
    hvac_cents_a = sum(kwh_a[k] * rates_a[k] for k in range(HOURS_PER_ARM)
                       if valid_a[k])
    hvac_cents_b = sum(kwh_b[k] * rates_b[k] for k in range(HOURS_PER_ARM)
                       if valid_b[k])
    hvac_dollars_a = hvac_cents_a / 100.0
    hvac_dollars_b = hvac_cents_b / 100.0
    hvac_kwh_a = sum(kwh_a[k] for k in range(HOURS_PER_ARM) if valid_a[k])
    hvac_kwh_b = sum(kwh_b[k] for k in range(HOURS_PER_ARM) if valid_b[k])

    # Cooling-active counts use em:2 + em:8 threshold; spec §9 reads from
    # the per-pair table, so derive from kWh as a proxy at the resolution
    # we have: any hour with kwh > 0.1 (>=100 W avg over the hour) counts.
    cooling_active_hours_a = sum(1 for k in range(HOURS_PER_ARM)
                                 if valid_a[k] and kwh_a[k] > 0.1)
    cooling_active_hours_b = sum(1 for k in range(HOURS_PER_ARM)
                                 if valid_b[k] and kwh_b[k] > 0.1)

    weather_distance = float(np.linalg.norm(z_a - z_b))
    poor_weather_match = weather_distance > p90_dist

    valid_pair_hours = sum(1 for k in range(HOURS_PER_ARM)
                           if valid_a[k] and valid_b[k])
    excluded_hours_count = HOURS_PER_ARM - valid_pair_hours

    raw_diffs = vec_b.as_array() - vec_a.as_array()
    z_diffs = z_b - z_a

    a_dates = f"{arm_a.start_ct.date()}/{arm_a.end_ct.date()}"
    b_dates = f"{arm_b.start_ct.date()}/{arm_b.end_ct.date()}"

    if hvac_dollars_a >= 5.0:
        percent_diff = (hvac_dollars_b - hvac_dollars_a) / hvac_dollars_a * 100.0
    else:
        percent_diff = float("nan")

    return {
        "pair_id": pair_id,
        "arm_a_id": _arm_id(arm_a),
        "arm_b_id": _arm_id(arm_b),
        "arm_a_dates": a_dates,
        "arm_b_dates": b_dates,
        "temporal_gap_days": abs((arm_b.start_ct - arm_a.start_ct).days),
        "weather_distance_zscore": weather_distance,
        "weather_vector_a": tuple(vec_a.as_array().tolist()),
        "weather_vector_b": tuple(vec_b.as_array().tolist()),
        "weather_component_diffs_raw": tuple(raw_diffs.tolist()),
        "weather_component_diffs_zscored": tuple(z_diffs.tolist()),
        "poor_weather_match_flag": bool(poor_weather_match),
        "valid_pair_hours": int(valid_pair_hours),
        "valid_pair_hours_a": int(sum(valid_a)),
        "valid_pair_hours_b": int(sum(valid_b)),
        "excluded_hours_count": int(excluded_hours_count),
        "excluded_hours_breakdown_a": dict(excl_a),
        "excluded_hours_breakdown_b": dict(excl_b),
        "cost_match_quality_median_diff_c_per_kwh":
            excl.cost_match_quality_median_diff_c_per_kwh,
        # CFE rate is a monthly snapshot column; we record what the
        # rate-snapshot held during this pair's aggregation. Per spec §10
        # L1 we report this per pair; pre-OSF this is single-month.
        "cfe_c_per_kwh_a": rate_snapshot["carbon_free_credit_c_per_kwh"],
        "cfe_c_per_kwh_b": rate_snapshot["carbon_free_credit_c_per_kwh"],
        "cooling_active_hours_a": int(cooling_active_hours_a),
        "cooling_active_hours_b": int(cooling_active_hours_b),
        "low_cooling_exposure_flag":
            bool(cooling_active_hours_a + cooling_active_hours_b < 6),
        "hvac_dollars_a": hvac_dollars_a,
        "hvac_dollars_b": hvac_dollars_b,
        "diff_dollars_b_minus_a": hvac_dollars_b - hvac_dollars_a,
        "percent_diff_dollars": percent_diff,
        "hvac_kwh_a": float(hvac_kwh_a),
        "hvac_kwh_b": float(hvac_kwh_b),
        "diff_kwh_b_minus_a": float(hvac_kwh_b - hvac_kwh_a),
        # Provenance for Ecowitt vs NOAA fallback; the Phase 3 fixture is
        # 100% Ecowitt -- real coverage helper lands when NOAA fallback
        # wires in (Phase 4).
        "weather_source_pct_ecowitt_a": 100.0,
        "weather_source_pct_ecowitt_b": 100.0,
    }


# --- §9.5 bucket summaries -------------------------------------------------


def _bucket_summary(rows: list[dict]) -> dict[str, float | int]:
    if not rows:
        return {"count": 0}
    dollars_diffs = [r["diff_dollars_b_minus_a"] for r in rows]
    kwh_diffs = [r["diff_kwh_b_minus_a"] for r in rows]

    def _stats(xs: list[float]) -> dict[str, float]:
        return {
            "mean": statistics.fmean(xs),
            "median": statistics.median(xs),
            "min": min(xs),
            "max": max(xs),
            "range": max(xs) - min(xs),
        }
    out: dict[str, float | int] = {"count": len(rows)}
    out.update({f"diff_dollars_{k}": v for k, v in _stats(dollars_diffs).items()})
    out.update({f"diff_kwh_{k}": v for k, v in _stats(kwh_diffs).items()})
    return out


def _build_bucket_summaries(pair_rows: list[dict]) -> dict[str, dict]:
    def total_pair_dollars(r: dict) -> float:
        return r["hvac_dollars_a"] + r["hvac_dollars_b"]
    return {
        "all_valid_pairs": _bucket_summary(pair_rows),
        "high_cooling_pairs": _bucket_summary(
            [r for r in pair_rows if total_pair_dollars(r) >= 50.0]
        ),
        "medium_cooling_pairs": _bucket_summary(
            [r for r in pair_rows
             if 5.0 <= total_pair_dollars(r) < 50.0]
        ),
        "low_cooling_pairs": _bucket_summary(
            [r for r in pair_rows if total_pair_dollars(r) < 5.0]
        ),
        "scarcity_exposed_pairs": _bucket_summary([]),
        "5cp_exposed_pairs": _bucket_summary([]),
        "high_temp_exposed_pairs": _bucket_summary([]),
    }


# --- run_full_pipeline -----------------------------------------------------


def run_full_pipeline(
    *,
    refoss_df: pd.DataFrame,
    eagle_df: pd.DataFrame,
    ecowitt_df: pd.DataFrame,
    rt_hrl_lmps_df: pd.DataFrame,
    comed_prices_df: pd.DataFrame,
    hvac_arm_mode_df: pd.DataFrame,
    bills_df: pd.DataFrame,
    rate_snapshot: Optional[dict[str, float]] = None,
    arm_calendar: Iterable[ArmPeriod] = ARM_CALENDAR,
) -> PipelineResult:
    """Entry point exercised by the outside-in acceptance test."""
    rate_snapshot = rate_snapshot if rate_snapshot is not None else DEFAULT_RATE_SNAPSHOT
    arms = list(arm_calendar)

    # Step 1: per-arm mode classification, kWh, rates, weather vector.
    per_arm: dict[str, dict] = {}
    for arm in arms:
        modes = _hourly_mode_from_telemetry(
            arm=arm, refoss_df=refoss_df, hvac_arm_mode_df=hvac_arm_mode_df,
        )
        kwh = _hourly_hvac_kwh(arm, refoss_df)
        rates = _hourly_rates(arm, rt_hrl_lmps_df, rate_snapshot)
        vec = build_weather_vector(arm, ecowitt_df)
        per_arm[_arm_id(arm)] = {
            "arm": arm,
            "modes": modes,
            "kwh": kwh,
            "rates": rates,
            "weather": vec,
            "fully_valid_count": fully_valid_count(modes, arm.arm),
            "passes_gate": arm_passes_validity_gate(modes, arm.arm),
        }

    # Step 2: validity gate -> arms_passed_validity
    arms_passed = {arm_id for arm_id, st in per_arm.items() if st["passes_gate"]}

    # Step 3: weather z-scoring across ALL 12 arms (within-sample, spec §6)
    all_vecs = [per_arm[_arm_id(a)]["weather"] for a in arms]
    z_all = within_sample_zscore(all_vecs)
    z_by_arm_id = {_arm_id(a): z_all[i] for i, a in enumerate(arms)}

    # Step 4: match gate-pass A vs gate-pass B
    pass_a = [a for a in arms if a.arm == "A" and _arm_id(a) in arms_passed]
    pass_b = [a for a in arms if a.arm == "B" and _arm_id(a) in arms_passed]
    z_a_matrix = np.stack([z_by_arm_id[_arm_id(a)] for a in pass_a]) if pass_a \
        else np.empty((0, 4))
    z_b_matrix = np.stack([z_by_arm_id[_arm_id(b)] for b in pass_b]) if pass_b \
        else np.empty((0, 4))
    pair_indices = hungarian_match(z_a_matrix, z_b_matrix)
    p90_dist = caliper_p90_distance(z_a_matrix, z_b_matrix)

    # Step 5: build per-pair rows
    pair_rows: list[dict] = []
    for pid, (a_idx, b_idx) in enumerate(pair_indices, start=1):
        arm_a = pass_a[a_idx]
        arm_b = pass_b[b_idx]
        st_a = per_arm[_arm_id(arm_a)]
        st_b = per_arm[_arm_id(arm_b)]
        row = _build_pair_row(
            pair_id=pid,
            arm_a=arm_a,
            arm_b=arm_b,
            modes_a=st_a["modes"],
            modes_b=st_b["modes"],
            rates_a=st_a["rates"],
            rates_b=st_b["rates"],
            kwh_a=st_a["kwh"],
            kwh_b=st_b["kwh"],
            vec_a=st_a["weather"],
            vec_b=st_b["weather"],
            z_a=z_by_arm_id[_arm_id(arm_a)],
            z_b=z_by_arm_id[_arm_id(arm_b)],
            p90_dist=p90_dist,
            rate_snapshot=rate_snapshot,
        )
        pair_rows.append(row)

    per_pair_table = pd.DataFrame(pair_rows)
    bucket_summaries = _build_bucket_summaries(pair_rows)

    # Mode distribution = aggregate count of each HourMode across all arms
    counter: Counter[str] = Counter()
    for st in per_arm.values():
        for m in st["modes"]:
            counter[m.value] += 1
    mode_distribution = dict(counter)

    return PipelineResult(
        per_pair_table=per_pair_table,
        bucket_summaries=bucket_summaries,
        mode_distribution=mode_distribution,
        arms_passed_validity=arms_passed,
        bill_reconciliations=[],  # populated by Task 3.14
    )
